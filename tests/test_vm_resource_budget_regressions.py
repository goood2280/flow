"""Active CPU guards and read-only diagnosis of migrated resource settings."""
import pytest


@pytest.fixture
def large_host(monkeypatch):
    from core import cache_budget, cache_settings, runtime_limits
    from core.paths import PATHS

    for key in (
        "FLOW_CPU_BUDGET_CORES", "FLOW_PROCESS_MEMORY_LIMIT_GB", "FLOW_MEMORY_CEILING_GB",
        "FLOW_SPLITTABLE_ROOT_LOT_RAM_CACHE_CPU_CORES", "FLOW_PROCESS_CPU_GUARD_CORES",
        "FLOW_CACHE_TOTAL_BUDGET_FRACTION", "FLOW_CACHE_MEMORY_TARGET_RATIO",
        "FLOW_SPLITTABLE_ROOT_LOT_RAM_CACHE_MAX_GB", "FLOW_SPLITTABLE_PRODUCT_RAM_CACHE_MAX_GB",
        "FLOW_SPLITTABLE_VIEW_CACHE_MAX_MB", "FLOW_PREVIEW_MEMORY_CACHE_GB",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("FLOW_RESOURCE_PROFILE", "auto")
    monkeypatch.setattr(runtime_limits, "effective_cpu_count", lambda: 8.0)
    monkeypatch.setattr(runtime_limits, "system_memory_snapshot", lambda: {"system_memory_total_gb": 128.0})
    monkeypatch.setattr(PATHS, "is_prod", True)
    monkeypatch.setattr(cache_settings, "read", lambda: {})
    cache_budget.invalidate()
    yield
    cache_budget.invalidate()


@pytest.mark.parametrize("profile", ["auto", "large"])
def test_active_cpu_guard_uses_host_budget_and_reports_legacy_override(large_host, monkeypatch, profile):
    from core import runtime_limits
    from app_v2.runtime.resource_guard import ResourceGuardMiddleware

    monkeypatch.setenv("FLOW_RESOURCE_PROFILE", profile)
    monkeypatch.setattr(runtime_limits, "_read_process_cpu_seconds", lambda: 13.0)
    monkeypatch.setattr(runtime_limits.time, "monotonic", lambda: 2.0)
    monkeypatch.setattr(runtime_limits, "_PROCESS_CPU_LAST", {"cpu_seconds": 10.0, "wall": 1.0})
    guard = ResourceGuardMiddleware(lambda *args: None)
    assert guard._cpu_guard_snapshot() == {}
    info, _ = runtime_limits.host_diagnostics()
    assert info["cpu_budget"] == info["cpu_guard_cores"] == 8
    monkeypatch.setenv("FLOW_PROCESS_CPU_GUARD_CORES", "2")
    assert guard._cpu_guard_snapshot()["process_cpu_over_limit"] is True
    info, warnings = runtime_limits.host_diagnostics()
    assert info["cpu_guard_cores"] == 2
    assert any("FLOW_PROCESS_CPU_GUARD_CORES=2" in warning for warning in warnings)


def test_saved_cache_limits_are_reported_without_mutation(large_host, monkeypatch):
    from core import cache_budget, cache_settings

    saved = {"pool_fraction": 0.3, "root_ram_gb": 1, "product_ram_gb": 0.5, "view_mb": 128}
    monkeypatch.setattr(cache_settings, "read", lambda: dict(saved))
    before = dict(saved)
    info, warnings = cache_budget.diagnostics()
    assert info["cache_pool_gb"] == pytest.approx(30.72)
    for key in ("pool_fraction", "view_mb"):
        assert any("cache_budget_settings.json: " + key in warning for warning in warnings)
    assert saved == before
    # Environment overrides win over stored values in both the report and runtime.
    monkeypatch.setenv("FLOW_CACHE_TOTAL_BUDGET_FRACTION", "0.6")
    monkeypatch.setenv("FLOW_SPLITTABLE_VIEW_CACHE_MAX_MB", "256")
    monkeypatch.setenv("FLOW_PREVIEW_MEMORY_CACHE_GB", "0.5")
    cache_budget.invalidate()
    info, warnings = cache_budget.diagnostics()
    assert info["cache_pool_gb"] == pytest.approx(61.44)
    assert not any("pool_fraction" in warning for warning in warnings)
    assert any("FLOW_SPLITTABLE_VIEW_CACHE_MAX_MB=256" in warning for warning in warnings)
    assert any("FLOW_PREVIEW_MEMORY_CACHE_GB=0.5" in warning for warning in warnings)
    assert not any("cache_budget_settings.json: view_mb" in warning for warning in warnings)


def test_automatic_cache_defaults_have_no_legacy_limit_warning(large_host):
    from core import cache_budget

    info, warnings = cache_budget.diagnostics()
    assert info["cache_pool_gb"] == pytest.approx(61.44)
    assert warnings == []


def test_host_diagnostics_identifies_small_profile_and_cpu_guards(large_host, monkeypatch):
    from core import runtime_limits

    monkeypatch.setenv("FLOW_RESOURCE_PROFILE", "small")
    _, warnings = runtime_limits.host_diagnostics()
    assert any("FLOW_RESOURCE_PROFILE=small" in warning for warning in warnings)
    monkeypatch.setenv("FLOW_RESOURCE_PROFILE", "large")
    monkeypatch.setenv("FLOW_PROCESS_CPU_GUARD_CORES", "2")
    _, warnings = runtime_limits.host_diagnostics()
    assert any("FLOW_PROCESS_CPU_GUARD_CORES=2" in warning for warning in warnings)


def test_retired_cache_limits_are_not_reported_as_active_bottlenecks(large_host, monkeypatch):
    from core import cache_settings, runtime_limits

    monkeypatch.setattr(cache_settings, "read", lambda: {"root_ram_gb": 1, "product_ram_gb": 0.5})
    monkeypatch.setenv("FLOW_SPLITTABLE_ROOT_LOT_RAM_CACHE_CPU_CORES", "2")
    _, warnings = runtime_limits.host_diagnostics()
    assert not any(key in warning for warning in warnings
                   for key in ("root_ram_gb", "product_ram_gb", "FLOW_SPLITTABLE_ROOT_LOT_RAM_CACHE_CPU_CORES"))
