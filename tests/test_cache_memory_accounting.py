import sys
import json
import time
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def test_cgroup_snapshot_exposes_grafana_working_set(monkeypatch):
    from core import runtime_limits

    gib = 1024 ** 3

    def fake_int(path):
        if path.endswith("memory.max"):
            return 32 * gib
        if path.endswith("memory.current"):
            return 17 * gib
        return 0

    def fake_stat(_path, field):
        return {
            "inactive_file": 13 * gib,
            "active_file": 1 * gib,
        }.get(field, 0)

    monkeypatch.setattr(runtime_limits, "_read_int_file", fake_int)
    monkeypatch.setattr(runtime_limits, "_read_cgroup_stat_field", fake_stat)

    snapshot = runtime_limits._cgroup_memory_snapshot_bytes()

    assert snapshot["used_raw_bytes"] == 17 * gib
    assert snapshot["working_set_bytes"] == 4 * gib
    # The pressure guard intentionally retains the stricter anonymous estimate.
    assert snapshot["used_bytes"] == 3 * gib


def test_process_effective_memory_prefers_container_working_set(monkeypatch):
    from core import runtime_limits

    gib = 1024 ** 3
    monkeypatch.setattr(
        runtime_limits,
        "_cgroup_memory_snapshot_bytes",
        lambda: {"working_set_bytes": 3.8 * gib, "used_raw_bytes": 17 * gib},
    )
    monkeypatch.setattr(
        runtime_limits,
        "_read_smaps_rollup",
        lambda: {"pss_kb": 0, "uss_kb": 0, "anon_kb": 0},
    )
    monkeypatch.setattr(runtime_limits, "system_memory_snapshot", lambda: {})

    snapshot = runtime_limits.process_memory_snapshot()

    assert snapshot["process_memory_effective_kind"] == "container_working_set"
    assert snapshot["process_memory_effective_gb"] == 3.8
    assert snapshot["container_memory_current_gb"] == 17.0


def test_cache_job_tracks_lookup_and_pivot_stage_peaks(monkeypatch):
    from core import cache_event_log

    job_id = "memory-stage-test"
    job = {
        "id": job_id,
        "status": "running",
        "start_effective_gb": 3.0,
        "peak_effective_gb": 3.0,
        "peak_rss_gb": 10.0,
        "stages": [
            {"id": "lookup_build", "status": "queued", "started_at": "", "finished_at": ""},
            {"id": "pivot_build", "status": "queued", "started_at": "", "finished_at": ""},
        ],
    }
    samples = iter([
        {"rss_gb": 10.0, "effective_gb": 3.0, "effective_kind": "container_working_set", "system_available_gb": 20.0},
        {"rss_gb": 17.0, "effective_gb": 3.8, "effective_kind": "container_working_set", "system_available_gb": 19.0},
        {"rss_gb": 12.0, "effective_gb": 3.2, "effective_kind": "container_working_set", "system_available_gb": 19.5},
        {"rss_gb": 14.0, "effective_gb": 3.6, "effective_kind": "container_working_set", "system_available_gb": 19.2},
    ])
    monkeypatch.setattr(cache_event_log, "_memory_sample", lambda: next(samples))
    with cache_event_log._LOCK:
        cache_event_log._ACTIVE_JOBS[job_id] = job
    try:
        cache_event_log.stage_started(job_id, "lookup_build")
        cache_event_log.stage_finished(job_id, "lookup_build", ok=True)
        cache_event_log.stage_started(job_id, "pivot_build")
        cache_event_log.stage_finished(job_id, "pivot_build", ok=True)
        lookup, pivot = job["stages"]
        assert lookup["peak_effective_gb"] == 3.8
        assert lookup["peak_delta_gb"] == 0.8
        assert pivot["peak_effective_gb"] == 3.6
        assert pivot["peak_delta_gb"] == 0.4
        assert job["memory_metric_kind"] == "container_working_set"
    finally:
        with cache_event_log._LOCK:
            cache_event_log._ACTIVE_JOBS.pop(job_id, None)


def test_recent_peak_does_not_mix_legacy_rss_fallback_with_working_set(monkeypatch, tmp_path):
    from core import cache_event_log

    path = tmp_path / "ram.jsonl"
    now = time.time()
    rows = [
        {"ts": now - 2, "rss_gb": 17.0, "effective_gb": 17.0,
         "effective_kind": "rss_fallback", "origin": "prod", "host": "flow"},
        {"ts": now - 1, "rss_gb": 17.0, "effective_gb": 3.8,
         "effective_kind": "container_working_set", "origin": "prod", "host": "flow"},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(cache_event_log, "_ram_peak_log_path", lambda: path)
    monkeypatch.setattr(cache_event_log, "_origin", lambda: ("prod", "flow"))

    peak = cache_event_log.recent_peak_rss(
        48.0, effective_kind="container_working_set"
    )

    assert peak["peak_rss_gb"] == 17.0
    assert peak["peak_effective_gb"] == 3.8
    assert peak["effective_sample_count"] == 1
