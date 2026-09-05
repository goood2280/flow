import json
import os
from collections import deque
from pathlib import Path

import polars as pl
import pytest


@pytest.fixture
def cache_env(monkeypatch, tmp_path):
    from core import cache_event_log, ml_table_lookup as lookup
    from app_v2.modules.splittable import cache_builder as pivot

    source = tmp_path / "ML_TABLE_RESILIENT.parquet"
    cdir = tmp_path / "lookup"
    monkeypatch.setattr(lookup, "cache_dir_for", lambda _fp: cdir)
    monkeypatch.setattr(lookup, "process_memory_high", lambda: False)
    monkeypatch.setattr(lookup, "_build_cancel_requested", lambda: False)
    monkeypatch.setattr(cache_event_log, "record", lambda *_a, **_kw: None)
    monkeypatch.setattr(pivot, "CACHE_DIR", tmp_path / "pivot")
    monkeypatch.setattr(pivot, "process_memory_high", lambda: False)
    monkeypatch.setattr(pivot, "_throttled_yield", lambda *_a: False)
    monkeypatch.setenv("FLOW_ML_TABLE_LOOKUP_CACHE_BUILD_CHUNK_SIZE", "8")
    return lookup, pivot, source, cdir


def write_source(source, value=1):
    pl.DataFrame({
        "ROOT_LOT_ID": ["A", "A", "B"], "LOT_ID": ["A.1", "A.2", "B.1"],
        "WAFER_ID": [1, 2, 1], "KNOB_X": [value, None, 3], "INLINE_X": [1.5, 2.5, 3.5],
    }).write_parquet(source)


def test_knob_sidecar_older_by_fraction_of_second_is_rejected(tmp_path):
    from routers import splittable
    main, sidecar = tmp_path / "main.parquet", tmp_path / "knob.parquet"
    frame = pl.DataFrame({"ROOT_LOT_ID": ["A"], "WAFER_ID": [1], "KNOB_X": [1]})
    frame.write_parquet(sidecar)
    frame.write_parquet(main)
    ns = main.stat().st_mtime_ns
    os.utime(sidecar, ns=(ns - 100_000_000, ns - 100_000_000))
    assert not splittable._knob_sidecar_usable(sidecar, main)
    os.utime(sidecar, ns=(ns + 100_000_000, ns + 100_000_000))
    assert splittable._knob_sidecar_usable(sidecar, main)


def test_pi_lot_prewarm_preserves_fab_identity_and_deduplicates(tmp_path, monkeypatch):
    from routers import splittable
    from core.paths import PATHS
    monkeypatch.setattr(PATHS, "data_root", tmp_path)
    tables = tmp_path / "lot_management/tables"
    tables.mkdir(parents=True)
    (tables / "p.json").write_text(json.dumps({"product": "P", "rows": [
        {"values": {"lot_id": "A.2"}}, {"values": {"lot_id": "A.2"}},
        {"values": {"lot_id": "B.1"}}, {"values": {"lot_id": ""}},
    ]}), encoding="utf-8")
    monkeypatch.setattr(splittable, "_knob_prewarm_targets", lambda: [("P", "A")])
    monkeypatch.setattr(splittable, "_knob_prewarm_max_lots", lambda: 3)
    assert splittable._knob_prewarm_requests() == [("P", "", "A.2"), ("P", "", "B.1"), ("P", "A", "")]


def test_lookup_streams_wide_roots_and_harvests_bounded_candidates(cache_env, monkeypatch):
    from core import parquet_perf
    lookup, _, source, cdir = cache_env
    n = 11005
    data = {"ROOT_LOT_ID": [" a "] * 11000 + ["B"] * 5,
            "LOT_ID": ["A.1"] * 11000 + ["B.1"] * 5,
            "WAFER_ID": list(range(n)), "INLINE_X": [1.5] * n}
    data.update({f"KNOB_{i}": [i] * n for i in range(40)})
    pl.DataFrame(data).write_parquet(source)
    monkeypatch.setenv("FLOW_ML_TABLE_LOOKUP_CACHE_MAX_ROWS_PER_FILE", "10000")
    original = parquet_perf.collect_streaming
    widths = []

    def bounded_collect(lf, **kwargs):
        widths.append(len(lf.collect_schema()))
        assert widths[-1] <= 32
        assert kwargs.get("fallback") is False
        return original(lf, **kwargs)

    monkeypatch.setattr(parquet_perf, "collect_streaming", bounded_collect)
    result = lookup._build_lookup_cache(source)
    assert result["meta"]["row_count"] == n
    files = lookup._partition_files(cdir)
    assert len(files) == 3
    assert all(lookup._parquet_row_count(fp) <= 10000 for fp in files)
    frame = pl.read_parquet(files, hive_partitioning=False)
    assert frame["root_lot_id"].unique().sort().to_list() == ["A", "B"]
    assert frame["INLINE_X"].dtype == pl.Float64
    index = json.loads((cdir / lookup.CANDIDATE_INDEX_FILE).read_text("utf-8"))
    assert index["identity_values"]["lot_id"] == ["A.1", "B.1"]
    assert index["values_by_column"]["KNOB_39"] == ["39"]
    assert max(widths) == 32


def test_lookup_metadata_failure_preserves_previous_generation(cache_env, monkeypatch):
    lookup, _, source, cdir = cache_env
    write_source(source)
    lookup._build_lookup_cache(source)
    before = {str(fp.relative_to(cdir)): fp.read_bytes() for fp in cdir.rglob("*") if fp.is_file()}
    write_source(source, 9)
    monkeypatch.setattr(lookup, "_build_candidate_index_from_cache",
                        lambda *_a, **_kw: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        lookup._build_lookup_cache(source)
    after = {str(fp.relative_to(cdir)): fp.read_bytes() for fp in cdir.rglob("*") if fp.is_file()}
    assert after == before


def test_lookup_source_change_does_not_publish_false_fresh(cache_env, monkeypatch):
    lookup, _, source, cdir = cache_env
    write_source(source)
    lookup._build_lookup_cache(source)
    before = (cdir / lookup.META_FILE).read_bytes()
    original = lookup._sink_lookup_cache_partitions_chunked

    def changing_source(*args):
        result = original(*args)
        stat = source.stat()
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000_000))
        return result

    monkeypatch.setattr(lookup, "_sink_lookup_cache_partitions_chunked", changing_source)
    with pytest.raises(RuntimeError, match="source changed"):
        lookup._build_lookup_cache(source)
    assert (cdir / lookup.META_FILE).read_bytes() == before


def test_lookup_publish_rename_failure_rolls_back(cache_env, monkeypatch):
    lookup, _, _, cdir = cache_env
    cdir.mkdir()
    (cdir / "previous").write_text("working")
    staged = cdir.with_name("staged")
    staged.mkdir()
    original = Path.replace

    def failed_publish(path, target):
        if path == staged:
            raise PermissionError("sharing violation")
        return original(path, target)

    monkeypatch.setattr(Path, "replace", failed_publish)
    with pytest.raises(PermissionError):
        lookup._publish_lookup_cache(staged, cdir)
    assert (cdir / "previous").read_text() == "working"
    assert not list(cdir.parent.glob("lookup.previous-*"))
    assert not cdir.with_name(cdir.name + ".previous").exists()


def test_lookup_recovers_interrupted_directory_switch(cache_env):
    lookup, _, source, cdir = cache_env
    write_source(source)
    previous = cdir.with_name(cdir.name + ".previous")
    previous.mkdir()
    (previous / "working").write_text("old generation")
    lookup._recover_lookup_cache(cdir)
    assert (cdir / "working").read_text() == "old generation"
    assert not previous.exists()


@pytest.mark.parametrize("total,expected", [(10, 8.0), (23, 18.4)])
def test_five_core_host_reserves_cpu_and_memory(monkeypatch, total, expected):
    from core import runtime_limits
    for name in ("FLOW_CPU_BUDGET_CORES", "FLOW_PROCESS_MEMORY_LIMIT_GB",
                 "FLOW_DEV_PROCESS_MEMORY_LIMIT_GB", "FLOW_PROCESS_MEMORY_LIMIT_FRACTION"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FLOW_RESOURCE_PROFILE", "small")
    monkeypatch.setattr(runtime_limits, "effective_cpu_count", lambda: 5.0)
    monkeypatch.setattr(runtime_limits, "_memory_override_total_bytes", lambda: total * 1024**3)
    assert runtime_limits.cpu_budget_cores() == 4.0
    assert runtime_limits.process_memory_limit_gb() == expected


def test_pivot_without_fingerprints_is_complete_and_detects_missing_root(cache_env, monkeypatch):
    _, pivot, source, _ = cache_env
    write_source(source)
    pressured = iter([True])
    monkeypatch.setattr(pivot, "process_memory_high", lambda: next(pressured, False))
    assert pivot.build_pivoted_cache_for_product(source.stem, product_path=source)
    out = pivot.CACHE_DIR / source.stem
    assert not (out / pivot._ROOT_FINGERPRINT_FILE).exists()
    assert pivot.completed_cache_matches(out, source)
    assert pl.read_parquet(out / "A.parquet")["KNOB_X"].to_list() == [1, None]
    (out / "A.parquet").unlink()
    assert not pivot.completed_cache_matches(out, source)
    assert pivot.build_pivoted_cache_for_product(source.stem, product_path=source)
    assert pivot.completed_cache_matches(out, source)
    write_source(source, 7)
    assert not pivot.completed_cache_matches(out, source)


def test_pivot_fingerprint_batches_observe_cancellation(cache_env):
    _, pivot, source, _ = cache_env
    write_source(source)
    checks = []

    def cancelled():
        checks.append(True)
        return len(checks) >= 2

    assert not pivot.build_pivoted_cache_for_product(
        source.stem, product_path=source, should_cancel=cancelled)
    assert not pivot.completed_cache_matches(pivot.CACHE_DIR / source.stem, source)


def test_local_lookup_holds_shared_slot_and_does_not_retry_cancellation(cache_env, monkeypatch):
    from core import scan_gate, runtime_limits
    lookup, _, source, _ = cache_env
    write_source(source)
    target = str(source.resolve())
    monkeypatch.setattr(lookup, "_BUILD_QUEUE", deque([source]))
    monkeypatch.setattr(lookup, "_BUILD_LOCAL_ONLY", {target})
    monkeypatch.setattr(lookup, "_BUILD_STATE", {})
    monkeypatch.setattr(lookup, "lookup_artifacts_fresh", lambda *_a: False)
    monkeypatch.setattr(runtime_limits, "process_memory_high", lambda: False)
    monkeypatch.setattr(runtime_limits, "process_memory_snapshot", lambda: {})
    attempts = []

    def cancelled_build(*_a, **_kw):
        assert scan_gate.holding()
        attempts.append(True)
        return {"ok": False, "cancelled": True}

    monkeypatch.setattr(lookup, "build_lookup_cache", cancelled_build)
    monkeypatch.setattr(lookup, "_schedule_build_retry",
                        lambda *_a, **_kw: pytest.fail("cancelled build must not retry"))
    lookup._worker_loop()
    assert attempts == [True]
    assert lookup._BUILD_STATE["last_error"] == "cancelled_by_admin"
    assert not scan_gate.holding()


def test_pivot_retries_only_unfinished_roots(cache_env, monkeypatch):
    _, pivot, source, _ = cache_env
    write_source(source)
    monkeypatch.setenv("FLOW_PIVOT_CACHE_CHUNK_SIZE", "1")
    original = pl.LazyFrame.sink_parquet
    written = []
    fail = [True]

    def sink(lf, path, **kwargs):
        if Path(path).parent.name == source.stem:
            written.append(Path(path).name)
            if Path(path).name == "B.tmp.parquet" and fail[0]:
                raise OSError("transient write error")
        return original(lf, path, **kwargs)

    monkeypatch.setattr(pl.LazyFrame, "sink_parquet", sink)
    assert not pivot.build_pivoted_cache_for_product(source.stem, product_path=source)
    assert written == ["A.tmp.parquet", "B.tmp.parquet"]
    fail[0] = False
    written.clear()
    assert pivot.build_pivoted_cache_for_product(source.stem, product_path=source)
    assert written == ["B.tmp.parquet"]


@pytest.mark.parametrize("recovers", [True, False])
def test_pipeline_retries_pivot_only_then_continues(cache_env, monkeypatch, recovers):
    from routers import splittable as route
    lookup, _, source, _ = cache_env
    write_source(source)
    calls = []
    attempts = []
    monkeypatch.setattr(route, "_scan_cancel_requested", lambda: False)
    monkeypatch.setattr(route, "_match_cache_products", lambda _p: [source.stem])
    monkeypatch.setattr(route, "_product_path", lambda _p: source)
    monkeypatch.setattr(lookup, "enqueue_build", lambda *_a, **_kw: calls.append("lookup") or {})
    monkeypatch.setattr(lookup, "build_queue_snapshot", lambda: {})
    monkeypatch.setattr(lookup, "cache_status", lambda _p: {"status": "fresh"})
    monkeypatch.setattr(route, "_enqueue_pivot_cache_build", lambda *_a, **_kw: attempts.append(1) or True)
    monkeypatch.setattr(route, "_pivot_cache_build_state", lambda _p: "")
    monkeypatch.setattr(route, "_pivot_cache_needs_build", lambda *_a: not (recovers and len(attempts) >= 2))
    monkeypatch.setattr(route, "_enqueue_manual_lot_progress_refresh", lambda _p: calls.append("latest") or True)
    monkeypatch.setattr(route, "_MANUAL_LATEST_REFRESH_RUNNING", False)
    monkeypatch.setattr(route, "_refresh_dashboard_latest_v4", lambda *_a, **_kw: {"ok": True})
    monkeypatch.setattr(route, "_current_fab_override", lambda _p: (None, None, "fab"))
    monkeypatch.setattr(route, "_enqueue_fab_lot_index_build", lambda *_a, **_kw: calls.append("fab") or True)
    monkeypatch.setattr(route, "_FAB_IDX_BUILD_INPROGRESS", set())
    monkeypatch.setattr(route, "_fab_lot_index_read_meta", lambda _p: {"built_at": "now", "root_col": "root"})
    monkeypatch.setenv("FLOW_PIVOT_BUILD_RETRY_SEC", "0")
    monkeypatch.setenv("FLOW_PIVOT_BUILD_RETRY_MAX", "2")
    result = route._enqueue_required_split_caches(source.stem, False, owns_job=False, local_only=True)
    assert result["ok"] is recovers
    assert len(attempts) == (2 if recovers else 3)
    assert calls == ["lookup", "latest", "fab"]
