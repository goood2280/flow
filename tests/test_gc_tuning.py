import gc
import time

import pytest

from core import gc_tuning, memory_trim


@pytest.fixture(autouse=True)
def _unfreeze_after():
    yield
    gc.unfreeze()


def _cache_like(n):
    return [{"rows": [[f"v{i}_{r}_{c}" for c in range(25)] for r in range(200)]} for i in range(n)]


def test_settle_freezes_heap_so_full_collect_skips_cache_objects(monkeypatch):
    monkeypatch.delenv("FLOW_GC_FREEZE", raising=False)
    cache = _cache_like(200)
    before = gc.get_freeze_count()
    out = gc_tuning.settle("unit")
    assert out["frozen"] is True
    assert gc.get_freeze_count() > before
    # 캐시 객체는 영구 세대로 옮겨져 전체 수집이 훑지 않는다.
    started = time.perf_counter()
    gc.collect()
    assert (time.perf_counter() - started) < 0.5
    assert cache  # 살아 있는 캐시는 그대로


def test_trim_uses_settle_not_full_collect(monkeypatch):
    calls = []
    monkeypatch.setattr(gc_tuning, "settle", lambda reason="": calls.append(("settle", reason)) or {"collected": 0, "ms": 0.1})
    monkeypatch.setattr(gc_tuning, "full_collect", lambda reason="": calls.append(("full", reason)) or {"collected": 0, "ms": 0.1})
    memory_trim.trim(reason="task_done:x")
    memory_trim.trim(reason="crisis", collect="full")
    memory_trim.trim(reason="skip", collect=False)
    assert calls == [("settle", "task_done:x"), ("full", "crisis")]


def test_freeze_disabled_keeps_old_behaviour(monkeypatch):
    monkeypatch.setenv("FLOW_GC_FREEZE", "0")
    before = gc.get_freeze_count()
    assert gc_tuning.settle("unit")["frozen"] is False
    assert gc.get_freeze_count() == before


def test_full_collect_refreezes(monkeypatch):
    monkeypatch.delenv("FLOW_GC_FREEZE", raising=False)
    _cache = _cache_like(20)
    gc_tuning.settle("unit")
    out = gc_tuning.full_collect("unit")
    assert out["ms"] >= 0
    assert gc.get_freeze_count() > 0


def test_maintenance_waits_for_quiet_users(monkeypatch):
    calls = []
    monkeypatch.delenv("FLOW_GC_FREEZE", raising=False)
    monkeypatch.setattr(gc_tuning, "full_collect", lambda reason="": calls.append(reason) or {"collected": 0, "ms": 1.0})
    monkeypatch.setitem(gc_tuning._STATE, "last_maintenance_ts", 0.0)
    monkeypatch.setattr("core.request_priority.seconds_since_user_activity", lambda: 30.0)
    assert gc_tuning.maybe_maintenance(now=10_000.0) is None
    monkeypatch.setattr("core.request_priority.seconds_since_user_activity", lambda: 3600.0)
    assert gc_tuning.maybe_maintenance(now=10_000.0) is not None
    # 최소 간격 안에서는 다시 돌지 않는다.
    assert gc_tuning.maybe_maintenance(now=10_060.0) is None
    assert calls == ["maintenance"]


def test_pause_monitor_records_full_collections():
    gc_tuning.install_pause_monitor()
    gc_tuning.install_pause_monitor()  # 중복 등록하지 않는다
    assert gc.callbacks.count(gc_tuning._gc_callback) == 1
    before = gc_tuning.status()["gen2_collections"]
    gc.collect()
    assert gc_tuning.status()["gen2_collections"] == before + 1
