"""Focused capacity contracts for bounded Flow hosts."""

from __future__ import annotations

import pytest


def _mock_host(monkeypatch, runtime_limits, cores: int, memory_gb: float) -> None:
    monkeypatch.setattr(runtime_limits.os, "cpu_count", lambda: cores)
    monkeypatch.setattr(runtime_limits.os, "sched_getaffinity", lambda _pid: set(range(cores)), raising=False)
    monkeypatch.setattr(runtime_limits, "_cgroup_cpu_quota_cores", lambda: 0.0)
    monkeypatch.setattr(runtime_limits, "_host_memory_snapshot_bytes", lambda: {
        "total_bytes": memory_gb * 1024**3,
        "available_bytes": memory_gb * 0.8 * 1024**3,
        "percent": 20.0,
        "source": "test",
    })
    monkeypatch.setattr(runtime_limits, "_cgroup_memory_snapshot_bytes", lambda: {})
    monkeypatch.setenv("FLOW_SYSTEM_MEMORY_TOTAL_GB", str(memory_gb))
    monkeypatch.delenv("FLOW_PROCESS_MEMORY_LIMIT_FRACTION", raising=False)
    monkeypatch.delenv("FLOW_DEV_PROCESS_MEMORY_LIMIT_GB", raising=False)


@pytest.mark.parametrize(
    ("cores", "memory_gb", "expected_cpu", "expected_memory"),
    [(5, 30.0, 4.0, 24.0), (12, 64.0, 11.0, 51.2)],
)
def test_auto_capacity_scales_with_host(monkeypatch, cores, memory_gb, expected_cpu, expected_memory):
    from core import duckdb_engine, runtime_limits

    _mock_host(monkeypatch, runtime_limits, cores, memory_gb)
    monkeypatch.delenv("FLOW_CPU_BUDGET_CORES", raising=False)
    monkeypatch.delenv("FLOW_DUCKDB_THREADS", raising=False)
    monkeypatch.delenv("FLOW_SYSTEM_CPU_CORES", raising=False)

    assert runtime_limits.effective_cpu_count() == cores
    assert runtime_limits.cpu_budget_cores() == expected_cpu
    assert runtime_limits.auto_process_memory_limit_gb() == expected_memory
    assert duckdb_engine._thread_count() == int(expected_cpu)


def test_memory_override_uses_cgroup_limit_and_used_bytes(monkeypatch):
    from core import runtime_limits

    monkeypatch.setattr(runtime_limits, "_host_memory_snapshot_bytes", lambda: {
        "total_bytes": 128 * 1024**3, "available_bytes": 116 * 1024**3,
        "percent": 9.4, "source": "test",
    })
    monkeypatch.setattr(runtime_limits, "_cgroup_memory_snapshot_bytes", lambda: {
        "total_bytes": 30 * 1024**3, "used_bytes": 12 * 1024**3, "source": "cgroup_v2",
    })
    monkeypatch.setenv("FLOW_SYSTEM_MEMORY_TOTAL_GB", "64")
    assert runtime_limits.system_memory_snapshot()["system_memory_total_gb"] == 30.0
    assert runtime_limits.system_memory_snapshot()["system_memory_available_gb"] == 18.0
    monkeypatch.setenv("FLOW_SYSTEM_MEMORY_TOTAL_GB", "20")
    assert runtime_limits.system_memory_snapshot()["system_memory_total_gb"] == 20.0
    assert runtime_limits.system_memory_snapshot()["system_memory_available_gb"] == 8.0


def test_memory_override_cannot_enlarge_physical_host(monkeypatch):
    from core import runtime_limits

    monkeypatch.setattr(runtime_limits, "_host_memory_snapshot_bytes", lambda: {
        "total_bytes": 16 * 1024**3, "available_bytes": 12 * 1024**3,
        "percent": 25.0, "source": "test",
    })
    monkeypatch.setattr(runtime_limits, "_cgroup_memory_snapshot_bytes", lambda: {})
    monkeypatch.setenv("FLOW_SYSTEM_MEMORY_TOTAL_GB", "64")
    snap = runtime_limits.system_memory_snapshot()
    assert snap["system_memory_total_gb"] == 16.0
    assert snap["system_memory_available_gb"] == 12.0


def test_effective_cpu_count_respects_affinity_quota_and_operator_ceiling(monkeypatch):
    from core import runtime_limits

    monkeypatch.setattr(runtime_limits.os, "cpu_count", lambda: 12)
    monkeypatch.setattr(runtime_limits.os, "sched_getaffinity", lambda _pid: set(range(8)), raising=False)
    monkeypatch.setattr(runtime_limits, "_cgroup_cpu_quota_cores", lambda: 6.0)
    monkeypatch.setenv("FLOW_SYSTEM_CPU_CORES", "4")

    assert runtime_limits.effective_cpu_count() == 4
    assert runtime_limits.cpu_budget_cores() == 3


def test_manual_cpu_and_duckdb_thread_settings_are_preserved_with_budget_clamp(monkeypatch):
    from core import duckdb_engine, runtime_limits

    _mock_host(monkeypatch, runtime_limits, 5, 30.0)
    monkeypatch.setenv("FLOW_CPU_BUDGET_CORES", "2")
    monkeypatch.setenv("FLOW_DUCKDB_THREADS", "99")

    assert runtime_limits.cpu_budget_cores() == 2
    assert duckdb_engine._thread_count() == 2


def test_query_workers_auto_resolves_to_budget_and_worker_stays_fixed(monkeypatch):
    from routers import splittable
    from core import runtime_limits

    monkeypatch.setattr(runtime_limits, "effective_cpu_count", lambda: 5.0)
    monkeypatch.setattr(runtime_limits, "cpu_budget_cores", lambda: 4.0)
    monkeypatch.setattr(splittable._ml_table_lookup, "_root_ram_cache_use_dev", lambda: False)
    monkeypatch.setattr(splittable, "load_json", lambda *_args, **_kwargs: {"query_workers": 0})

    status = splittable._current_query_workers_status()
    assert status["configured"] == 0
    assert status["auto_value"] == 4
    assert status["max_workers"] == 4
    assert status["desired"] == 4

    monkeypatch.setattr(splittable._ml_table_lookup, "_root_ram_cache_use_dev", lambda: True)
    dev = splittable._current_query_workers_status()
    assert dev["configured"] == 1
    assert dev["auto_value"] == 1
    assert dev["desired"] == 1
    assert dev["max_workers"] == 1


def test_query_workers_save_zero_persists_auto_value(monkeypatch):
    from routers import splittable
    from core import runtime_limits

    saved = []
    config = {"query_workers": 4}
    monkeypatch.setattr(runtime_limits, "effective_cpu_count", lambda: 5.0)
    monkeypatch.setattr(runtime_limits, "cpu_budget_cores", lambda: 4.0)
    monkeypatch.setattr(splittable._ml_table_lookup, "_root_ram_cache_use_dev", lambda: False)
    monkeypatch.setattr(splittable, "load_json", lambda *_args, **_kwargs: dict(config))

    def capture_save(_path, value):
        config.update(value)
        saved.append(value)

    monkeypatch.setattr(splittable, "save_json", capture_save)

    result = splittable.save_query_workers({"query_workers": 0}, None)

    assert saved and saved[-1]["query_workers"] == 0
    assert result["configured"] == 0
    assert result["desired"] == 4


@pytest.mark.parametrize(("cores", "expected"), [(5, "4"), (12, "11")])
def test_polars_threads_auto_scales_and_explicit_value_is_preserved(monkeypatch, tmp_path, cores, expected):
    from core import runtime_limits
    from core.paths import PATHS

    _mock_host(monkeypatch, runtime_limits, cores, 30.0)
    monkeypatch.setattr(PATHS, "data_root", tmp_path)
    monkeypatch.setattr(PATHS, "is_prod", True)
    monkeypatch.setattr(runtime_limits, "effective_cpu_count", lambda: float(cores))
    monkeypatch.delenv("POLARS_MAX_THREADS", raising=False)
    monkeypatch.setenv("FLOW_SERVER_ROLE", "api")
    assert runtime_limits._polars_threads_for_role() == int(expected)

    cfg_dir = tmp_path / "splittable"
    cfg_dir.mkdir()
    (cfg_dir / "source_config.json").write_text('{"query_workers": 4}', encoding="utf-8")
    assert runtime_limits._polars_threads_for_role() == min(4, int(expected))


def test_polars_threads_dev_is_fixed_one(monkeypatch, tmp_path):
    from core import runtime_limits
    from core.paths import PATHS

    monkeypatch.setattr(PATHS, "data_root", tmp_path)
    monkeypatch.setenv("FLOW_SERVER_ROLE", "worker")
    monkeypatch.setattr(runtime_limits, "effective_cpu_count", lambda: 12.0)
    assert runtime_limits._polars_threads_for_role() == 1


def test_view_cache_auto_budget_grows_with_64_gib_host(monkeypatch):
    from core import cache_budget, cache_settings
    from routers import splittable

    monkeypatch.setenv("FLOW_CACHE_TOTAL_BUDGET_FRACTION", "0.45")
    monkeypatch.setenv("FLOW_CACHE_MEMORY_TARGET_RATIO", "0.80")
    monkeypatch.setattr(cache_budget, "worker_budget_factor", lambda: 1.0)
    monkeypatch.setattr(cache_budget, "_is_dev", lambda: False)
    monkeypatch.setattr(cache_budget, "system_memory_snapshot", lambda: {"system_memory_total_gb": 64.0}, raising=False)
    # pool_bytes imports the snapshot provider from runtime_limits at call time.
    from core import runtime_limits
    monkeypatch.setattr(runtime_limits, "system_memory_snapshot", lambda: {"system_memory_total_gb": 64.0})
    cache_budget.invalidate()
    cap = cache_budget.cap_bytes("splittable_view_payload")
    maximum = 64 * 1024**3 * 0.45 * 0.80 * 0.35
    assert cap > 6 * 1024**3
    assert cap <= maximum
    monkeypatch.delenv("FLOW_SPLITTABLE_VIEW_CACHE_MAX_MB", raising=False)
    monkeypatch.setattr(cache_settings, "get_float_role", lambda *_args: None)
    monkeypatch.setattr(splittable, "_VIEW_CACHE_AUTO_MB_CACHE", None)
    assert splittable._view_cache_auto_max_mb() == pytest.approx(64 * 1024 * 0.15)
    assert splittable._view_cache_max_bytes() == cap
    cache_budget.invalidate()
