"""core.heavy_jobs — the single-server heavy-work layer (replaced worker_dispatch)."""
import threading

import pytest

from core import heavy_jobs


@pytest.fixture(autouse=True)
def healthy_host(monkeypatch):
    monkeypatch.setattr(heavy_jobs, "_memory_block_reason", lambda: "")
    monkeypatch.setattr(heavy_jobs, "_trim_after", lambda label: None)


def test_interactive_kinds_bypass_the_heavy_gate():
    assert heavy_jobs._LOCAL_HEAVY_GATE.acquire(blocking=False)
    try:
        assert heavy_jobs.run_heavy("home_agent_turn", lambda: {"ok": True}) == {"ok": True}
    finally:
        heavy_jobs._LOCAL_HEAVY_GATE.release()


def test_non_cache_jobs_share_one_slot(monkeypatch):
    monkeypatch.setenv("FLOW_LOCAL_HEAVY_QUEUE_TIMEOUT_SEC", "1")
    assert heavy_jobs._LOCAL_HEAVY_GATE.acquire(blocking=False)
    try:
        result = heavy_jobs.run_heavy("some_heavy_scan", lambda: pytest.fail("must wait for the slot"))
    finally:
        heavy_jobs._LOCAL_HEAVY_GATE.release()
    assert result == {"ok": False, "error": "local_heavy_queue_timeout"}


def test_cache_builds_take_the_server_scan_slot(monkeypatch):
    from core import scan_gate

    seen = []
    result = heavy_jobs.run_heavy(
        "splittable_pivot_build", lambda: seen.append(scan_gate.holding()) or {"ok": True},
        label="pivot:P", product="P")
    assert result == {"ok": True}
    assert seen == [True]
    assert not scan_gate.holding()


def test_memory_admission_refuses_after_the_wait(monkeypatch):
    monkeypatch.setattr(heavy_jobs, "_memory_block_reason", lambda: "process_memory_high")
    monkeypatch.setenv("FLOW_LOCAL_HEAVY_MEMORY_WAIT_SEC", "0")
    result = heavy_jobs.run_heavy("some_heavy_scan", lambda: pytest.fail("must not run"))
    assert result["error"] == "local_heavy_memory_guard" and result["reason"] == "process_memory_high"
    assert heavy_jobs._LOCAL_HEAVY_GATE.acquire(blocking=False)  # slot was released
    heavy_jobs._LOCAL_HEAVY_GATE.release()


def test_idle_only_background_job_defers_while_users_are_active(monkeypatch):
    from core import request_priority

    monkeypatch.setattr(request_priority, "users_active", lambda **_k: True)
    monkeypatch.setenv("FLOW_LOCAL_HEAVY_IDLE_WAIT_SEC", "0")
    result = heavy_jobs.run_heavy("fab_matching_alert_scan", lambda: pytest.fail("must not run"), idle_only=True)
    assert result == {"ok": False, "error": "local_heavy_waiting_for_idle"}


def test_required_read_cache_proceeds_after_short_idle_grace(monkeypatch):
    from core import request_priority

    monkeypatch.setattr(request_priority, "users_active", lambda **_k: True)
    monkeypatch.setenv("FLOW_REQUIRED_CACHE_IDLE_WAIT_SEC", "0")
    assert heavy_jobs.run_heavy("ml_lookup_cache_build", lambda: {"ok": True}, idle_only=True) == {"ok": True}


def test_status_lists_running_jobs():
    started, release = threading.Event(), threading.Event()

    def job():
        started.set()
        release.wait(5)
        return {"ok": True}

    thread = threading.Thread(target=heavy_jobs.run_heavy, args=("some_heavy_scan", job), kwargs={"label": "scan-x"})
    thread.start()
    try:
        assert started.wait(5)
        assert [item["label"] for item in heavy_jobs.status()["running"]] == ["scan-x"]
    finally:
        release.set()
        thread.join(5)
    assert heavy_jobs.status()["running"] == []
