"""Bounded local fallback for caches required by interactive reads."""

import contextlib


def _set_busy(monkeypatch):
    from core import request_priority

    monkeypatch.setattr(request_priority, "users_active", lambda **_kwargs: True)


def _busy_clock(monkeypatch):
    now = [0.0]

    def monotonic():
        return now[0]

    def sleep(seconds):
        now[0] += float(seconds)

    monkeypatch.setattr("core.worker_dispatch.time.monotonic", monotonic)
    monkeypatch.setattr("core.worker_dispatch.time.sleep", sleep)
    return now


def test_required_cache_breaks_idle_grace_under_constant_traffic(monkeypatch):
    from core import worker_dispatch
    from core import runtime_limits

    _busy_clock(monkeypatch)
    monkeypatch.setattr(worker_dispatch, "_env_float", lambda name, default, lo, hi: (
        1.0 if name == "FLOW_REQUIRED_CACHE_IDLE_WAIT_SEC" else default
    ))
    _set_busy(monkeypatch)
    monkeypatch.setattr(runtime_limits, "process_memory_high", lambda: False)
    monkeypatch.setattr(runtime_limits, "process_memory_snapshot", lambda: {"system_memory_available_gb": 24.0})
    monkeypatch.setattr(worker_dispatch, "_cache_gate", lambda *_a, **_k: contextlib.nullcontext(True))

    result = worker_dispatch._run_local_heavy(
        "ml_lookup_cache_build", "lookup", lambda: {"ok": True}, idle_only=True
    )

    assert result == {"ok": True}


def test_nonrequired_background_job_still_defers_under_constant_traffic(monkeypatch):
    from core import worker_dispatch

    _busy_clock(monkeypatch)
    _set_busy(monkeypatch)
    result = worker_dispatch._run_local_heavy(
        "fab_matching_alert_scan", "alerts", lambda: {"ok": True}, idle_only=True
    )

    assert result == {"ok": False, "error": "local_heavy_waiting_for_idle"}


def test_required_cache_grace_does_not_bypass_memory_admission(monkeypatch):
    from core import worker_dispatch
    from core import runtime_limits

    _busy_clock(monkeypatch)
    monkeypatch.setattr(worker_dispatch, "_env_float", lambda name, default, lo, hi: (
        0.0 if name == "FLOW_REQUIRED_CACHE_IDLE_WAIT_SEC" else default
    ))
    _set_busy(monkeypatch)
    monkeypatch.setattr(runtime_limits, "process_memory_high", lambda: True)
    monkeypatch.setattr(runtime_limits, "process_memory_snapshot", lambda: {"system_memory_available_gb": 24.0})
    monkeypatch.setattr(worker_dispatch, "_cache_gate", lambda *_a, **_k: contextlib.nullcontext(True))

    result = worker_dispatch._run_local_heavy(
        "splittable_pivot_build", "pivot", lambda: {"ok": True}, idle_only=True
    )

    assert result["ok"] is False
    assert result["error"] == "local_heavy_memory_guard"


def test_required_cache_offline_durable_fallback_does_not_queue(monkeypatch):
    from core import worker_dispatch

    calls = []
    monkeypatch.setattr(worker_dispatch, "server_role", lambda: "api")
    monkeypatch.setattr(worker_dispatch, "offload_enabled", lambda: True)
    monkeypatch.setattr(worker_dispatch, "worker_alive", lambda **_kwargs: False)
    monkeypatch.setattr(worker_dispatch, "_discard_unclaimed_deduped_task", lambda key: calls.append(("discard", key)))
    monkeypatch.setattr(worker_dispatch, "_submit", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not queue")))
    monkeypatch.setattr(
        worker_dispatch,
        "_run_local_heavy",
        lambda task_type, name, fn, **kwargs: calls.append(("local", task_type, kwargs)) or fn(),
    )

    result = worker_dispatch.run_heavy(
        "splittable_pivot_build",
        {"product": "ML_TABLE_PRODUCT"},
        lambda: {"ok": True, "executed_on": "local"},
        durable=True,
        local_fallback=True,
        local_idle_only=True,
        dedupe_key="pivot:ML_TABLE_PRODUCT",
    )

    assert result == {"ok": True, "executed_on": "local"}
    assert ("discard", "pivot:ML_TABLE_PRODUCT") in calls
    assert any(item[0] == "local" for item in calls)
