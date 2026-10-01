"""Diagnose actual override effects without altering settings or starting load."""
import pytest
from core.runtime_limits import detected_cpu_count as detect_usable_cores


@pytest.fixture
def host(monkeypatch, tmp_path):
    from core import cache_budget, cache_settings, runtime_limits, sysmon
    from core.paths import PATHS
    from core.resource_diagnostics import RESOURCE_ENV_KEYS

    for name in RESOURCE_ENV_KEYS + ("FLOW_RESOURCE_PROFILE_SOURCE", "FLOW_PREWARM_CHILD", "FLOW_REFORMATIZE_IN_CHILD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FLOW_RESOURCE_PROFILE", "auto")
    monkeypatch.setattr(runtime_limits, "detected_cpu_count", lambda: 8.0)
    monkeypatch.setattr(runtime_limits, "system_memory_snapshot", lambda: {
        "system_memory_total_gb": 128.0, "system_memory_available_gb": 100.0,
    })
    monkeypatch.setattr(runtime_limits, "_windows_commit_bytes", lambda: {})
    monkeypatch.setattr(runtime_limits, "_module_available", lambda _: True)
    monkeypatch.setattr(PATHS, "is_prod", True)
    monkeypatch.setattr(cache_settings, "read", lambda: {})
    monkeypatch.setattr(sysmon, "SYSMON_STATE_FILE", tmp_path / "schedule.json")
    cache_budget.invalidate()
    yield runtime_limits
    cache_budget.invalidate()


@pytest.mark.parametrize("profile", ["auto", "large"])
def test_large_default_resource_paths_are_reported(host, monkeypatch, profile):
    monkeypatch.setenv("FLOW_RESOURCE_PROFILE", profile)
    info, warnings = host.host_diagnostics()
    assert info["detected_cores"] == info["cores"] == info["cpu_budget"] == 8
    assert info["profile"] == "large"
    assert info["cache_pool_gb"] == pytest.approx(61.44)
    assert info["process_memory_limit_gb"] == pytest.approx(99.8)
    assert info["cache_read_lane_slots"] == 2
    assert info["prewarm_process_enabled"] is True
    assert info["heavy_request_concurrency"] == 4
    assert info["essential_request_concurrency"] == 8
    assert info["threadpool_tokens"] == 240
    assert info["filebrowser_sql_concurrency"] == 3
    assert info["filebrowser_sql_per_user"] == 1
    assert info["duckdb_threads"] == 8
    assert info["duckdb_memory_limit_gb"] == 16
    assert info["heavy_background_jobs_enabled"] is False
    assert info["cache_change_driven_enabled"] is False
    for field in ("gc_freeze_enabled", "json_fast_routes_enabled", "lot_progress_vector_enabled", "splittable_view_pack_enabled"):
        assert info[field] is True
    assert len(warnings) == 1 and "예약 합성 부하" in warnings[0]


def test_legacy_cpu_ceiling_cannot_hide_small_profile(host, monkeypatch):
    monkeypatch.setenv("FLOW_SYSTEM_CPU_CORES", "4")
    info, warnings = host.host_diagnostics()
    assert info["detected_cores"] == 8
    assert info["cores"] == 4
    assert info["cpu_budget"] == 3
    assert info["profile"] == "small"
    assert any("FLOW_SYSTEM_CPU_CORES=4" in w for w in warnings)
    assert not any("FLOW_RESOURCE_PROFILE=small" in w for w in warnings)


def test_affinity_and_quota_are_not_mislabeled_as_stale_env(host, monkeypatch):
    monkeypatch.setattr(host.os, "cpu_count", lambda: 12)
    monkeypatch.setattr(host.os, "sched_getaffinity", lambda _: set(range(4)), raising=False)
    monkeypatch.setattr(host, "_cgroup_cpu_quota_cores", lambda: 2.5)
    monkeypatch.setattr(host, "detected_cpu_count", detect_usable_cores)
    monkeypatch.setenv("FLOW_SYSTEM_CPU_CORES", "4")
    assert host.detected_cpu_count() == host.effective_cpu_count() == 2.5
    _, warnings = host.host_diagnostics()
    assert not any("FLOW_SYSTEM_CPU_CORES=" in w for w in warnings)


def test_small_profile_and_development_mode_are_visible(host, monkeypatch):
    from core import cache_budget
    from core.paths import PATHS
    monkeypatch.setenv("FLOW_RESOURCE_PROFILE", "small")
    info, warnings = host.host_diagnostics()
    assert info["cpu_budget"] == 7
    assert info["cache_pool_gb"] == pytest.approx(46.08)
    assert any("FLOW_RESOURCE_PROFILE=small" in w for w in warnings)
    monkeypatch.setenv("FLOW_RESOURCE_PROFILE", "large")
    monkeypatch.setattr(PATHS, "is_prod", False)
    cache_budget.invalidate()
    info, warnings = host.host_diagnostics()
    assert host._polars_threads_for_role() == 1
    assert info["cache_pool_gb"] == round(61.44 * 0.35, 2)
    assert info["daily_synthetic_load_enabled"] is False
    assert any("FLOW_PROD=1" in w for w in warnings)


@pytest.mark.parametrize("env,value,field,expected", [
    ("FLOW_CPU_BUDGET_CORES", "2", "cpu_budget", 2),
    ("FLOW_DUCKDB_THREADS", "2", "duckdb_threads", 2),
    ("FLOW_PROCESS_MEMORY_LIMIT_GB", "70", "process_memory_limit_gb", 70),
    ("FLOW_MEMORY_CEILING_GB", "60", "process_memory_limit_gb", 60),
    ("FLOW_PROCESS_CPU_GUARD_CORES", "2", "cpu_guard_cores", 2),
    ("FLOW_CACHE_TOTAL_BUDGET_FRACTION", "0.45", "cache_pool_fraction", 0.45),
    ("FLOW_CACHE_MEMORY_TARGET_RATIO", "0.5", "cache_memory_target_ratio", 0.5),
    ("FLOW_CACHE_READ_LANE_SLOTS", "0", "cache_read_lane_slots", 0),
    ("FLOW_SPLITTABLE_PREWARM_PROCESS", "0", "prewarm_process_enabled", False),
    ("FLOW_HEAVY_REQUEST_CONCURRENCY", "1", "heavy_request_concurrency", 1),
    ("FLOW_ESSENTIAL_REQUEST_CONCURRENCY", "2", "essential_request_concurrency", 2),
    ("FLOW_THREADPOOL_TOKENS", "80", "threadpool_tokens", 80),
    ("FLOW_FILEBROWSER_SQL_CONCURRENCY", "1", "filebrowser_sql_concurrency", 1),
    ("FLOW_DUCKDB_MEMORY_LIMIT_GB", "2", "duckdb_memory_limit_gb", 2),
    ("FLOW_GC_FREEZE", "0", "gc_freeze_enabled", False),
    ("FLOW_JSON_FAST_ROUTES", "0", "json_fast_routes_enabled", False),
    ("FLOW_LOT_PROGRESS_VECTOR", "0", "lot_progress_vector_enabled", False),
    ("FLOW_SPLITTABLE_VIEW_PACK", "0", "splittable_view_pack_enabled", False),
])
def test_restrictive_overrides_report_effect_and_preserve_settings(host, monkeypatch, env, value, field, expected):
    import os
    monkeypatch.setenv(env, value)
    before = dict(os.environ)
    info, warnings = host.host_diagnostics()
    assert info[field] == expected
    assert any(env in w for w in warnings)
    assert dict(os.environ) == before


def test_runtime_and_diagnostic_admission_limits_share_policy(host, monkeypatch):
    from app_v2.runtime.resource_guard import ResourceGuardMiddleware
    monkeypatch.setenv("FLOW_HEAVY_REQUEST_CONCURRENCY", "2")
    monkeypatch.setenv("FLOW_ESSENTIAL_REQUEST_CONCURRENCY", "3")
    info, _ = host.host_diagnostics()
    guard = ResourceGuardMiddleware(lambda *args: None)
    assert guard._concurrency == info["heavy_request_concurrency"] == 2
    assert guard._essential_concurrency == info["essential_request_concurrency"] == 3


def test_polars_pool_mismatch_is_reported(host, monkeypatch):
    import polars as pl
    monkeypatch.setenv("POLARS_MAX_THREADS", "8")
    monkeypatch.setattr(pl, "thread_pool_size", lambda: 2)
    info, warnings = host.host_diagnostics()
    assert info["polars_actual_threads"] == 2
    assert any("초기화된 Polars 풀은 2" in w for w in warnings)


def test_ops_scan_surfaces_cpu_ceiling_and_full_resource_report(host, monkeypatch):
    from core import heavy_jobs, ops_scan, sysmon
    monkeypatch.setenv("FLOW_SYSTEM_CPU_CORES", "4")
    monkeypatch.setattr(sysmon, "collect_once", lambda: {})
    monkeypatch.setattr(sysmon, "history", lambda **_: [])
    monkeypatch.setattr(heavy_jobs, "status", lambda: {})
    monkeypatch.setattr(ops_scan, "_scheduler_states", lambda: [])
    findings = ops_scan._Findings()
    facts = ops_scan.scan_servers(findings)
    assert facts["env_overrides"]["FLOW_SYSTEM_CPU_CORES"] == "4"
    assert facts["resources"]["detected_cores"] == 8
    assert any("FLOW_SYSTEM_CPU_CORES=4" in f["detail"] for f in findings.items)
