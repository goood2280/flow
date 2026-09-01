import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def test_pool_uses_eighty_percent_stability_target(monkeypatch):
    from core import cache_budget, runtime_limits

    gib = 1024 ** 3
    monkeypatch.delenv("FLOW_CACHE_MEMORY_TARGET_RATIO", raising=False)
    monkeypatch.setattr(
        runtime_limits,
        "system_memory_snapshot",
        lambda: {"system_memory_total_gb": 10.0},
    )
    monkeypatch.setattr(cache_budget, "_pool_fraction", lambda: 0.50)
    monkeypatch.setattr(cache_budget, "worker_budget_factor", lambda: 1.0)
    cache_budget.invalidate()

    assert cache_budget.memory_target_ratio() == 0.80
    assert cache_budget.pool_bytes() == int(10 * gib * 0.50 * 0.80)


def test_memory_target_ratio_is_clamped_and_invalid_value_is_safe(monkeypatch):
    from core import cache_budget

    monkeypatch.setenv("FLOW_CACHE_MEMORY_TARGET_RATIO", "2")
    assert cache_budget.memory_target_ratio() == 1.0
    monkeypatch.setenv("FLOW_CACHE_MEMORY_TARGET_RATIO", "0.1")
    assert cache_budget.memory_target_ratio() == 0.5
    monkeypatch.setenv("FLOW_CACHE_MEMORY_TARGET_RATIO", "invalid")
    assert cache_budget.memory_target_ratio() == 0.80


def test_filebrowser_explicit_budget_still_uses_global_cap(monkeypatch):
    from core import cache_budget, filebrowser_cache

    calls = []

    def fake_capped(name, budget, *, explicit=False):
        calls.append((name, budget, explicit))
        return 12345

    monkeypatch.setenv("FLOW_PREVIEW_MEMORY_CACHE_GB", "4")
    monkeypatch.setattr(cache_budget, "capped", fake_capped)

    assert filebrowser_cache.memory_cache_budget_bytes() == 12345
    assert calls == [("filebrowser_preview", 4 * 1024 ** 3, True)]


def test_reformatize_explicit_budget_still_uses_global_cap(monkeypatch):
    from core import cache_budget
    from routers import reformatize

    calls = []

    def fake_capped(name, budget, *, explicit=False):
        calls.append((name, budget, explicit))
        return 67890

    monkeypatch.setenv("FLOW_REFORMATIZE_CACHE_MAX_MB", "512")
    monkeypatch.setattr(cache_budget, "capped", fake_capped)

    assert reformatize._cache_max_bytes() == 67890
    assert calls == [("reformatize_wide", 512 * 1024 ** 2, True)]
