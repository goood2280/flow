"""대형 서버: 조회 전제 캐시는 백그라운드 스캔·Auto report 뒤에 줄 서지 않는다."""
import threading

import pytest

from core import heavy_jobs, scan_gate


@pytest.fixture(autouse=True)
def _isolated_lane(monkeypatch):
    monkeypatch.setattr(heavy_jobs, "_memory_block_reason", lambda: "")
    monkeypatch.setattr(heavy_jobs, "_trim_after", lambda label: None)
    monkeypatch.setattr(scan_gate, "_READ_SLOT", None)
    monkeypatch.setattr(scan_gate, "_READ_SLOT_SIZE", 0)
    yield


def _hold_shared_slot():
    """다른 스레드가 공용 슬롯을 든 상태(FAB 매칭 스캔 같은 긴 작업)를 만든다."""
    started, release = threading.Event(), threading.Event()

    def holder():
        with scan_gate.exclusive("fab_matching_alert_scan", "long background scan") as ok:
            assert ok
            started.set()
            release.wait(10)

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    assert started.wait(5)
    return release, thread


def test_read_cache_build_bypasses_busy_shared_slot(monkeypatch):
    monkeypatch.setenv("FLOW_CACHE_READ_LANE_SLOTS", "2")
    release, thread = _hold_shared_slot()
    try:
        seen = []
        result = heavy_jobs.run_heavy(
            "splittable_pivot_build",
            lambda: seen.append(scan_gate.snapshot()["read_lane"]) or {"ok": True},
            label="pivot:P", product="P")
        assert result == {"ok": True}
        assert [item["product"] for item in seen[0]] == ["P"]
        # 공용 슬롯을 든 작업은 그대로 현재 작업으로 보인다.
        assert scan_gate.snapshot()["current"]["label"] == "long background scan"
    finally:
        release.set()
        thread.join(5)
    assert scan_gate.snapshot()["read_lane"] == []


def test_small_host_keeps_single_slot(monkeypatch):
    monkeypatch.setenv("FLOW_CACHE_READ_LANE_SLOTS", "0")
    monkeypatch.setenv("FLOW_CACHE_EXCLUSIVE_WAIT_SEC", "1")
    release, thread = _hold_shared_slot()
    try:
        result = heavy_jobs.run_heavy(
            "splittable_pivot_build", lambda: pytest.fail("must wait for the shared slot"),
            label="pivot:P", product="P")
    finally:
        release.set()
        thread.join(5)
    assert result["error"] == "cache_gate_timeout"


def test_background_scan_still_waits_for_shared_slot(monkeypatch):
    monkeypatch.setenv("FLOW_CACHE_READ_LANE_SLOTS", "2")
    monkeypatch.setenv("FLOW_CACHE_EXCLUSIVE_WAIT_SEC", "1")
    release, thread = _hold_shared_slot()
    try:
        result = heavy_jobs.run_heavy(
            "et_tracker_scan", lambda: pytest.fail("background scans stay serial"), label="et")
    finally:
        release.set()
        thread.join(5)
    assert result["error"] == "cache_gate_timeout"


def test_auto_report_uses_its_own_lane_on_large_host(monkeypatch):
    monkeypatch.setattr(heavy_jobs, "_large_host", lambda: True)
    release, thread = _hold_shared_slot()
    try:
        seen = []
        result = heavy_jobs.run_heavy(
            "auto_report_generate", lambda: seen.append(scan_gate.holding()) or {"ok": True}, label="report")
        assert result == {"ok": True}
        assert seen == [False]  # 캐시 슬롯을 잡지 않는다
    finally:
        release.set()
        thread.join(5)


def test_auto_report_shares_cache_slot_on_small_host(monkeypatch):
    monkeypatch.setattr(heavy_jobs, "_large_host", lambda: False)
    seen = []
    assert heavy_jobs.run_heavy(
        "auto_report_generate", lambda: seen.append(scan_gate.holding()) or {"ok": True}, label="report") == {"ok": True}
    assert seen == [True]


def test_read_lane_task_can_be_cancelled(monkeypatch):
    monkeypatch.setenv("FLOW_CACHE_READ_LANE_SLOTS", "1")
    started, release = threading.Event(), threading.Event()
    seen = {}

    def build():
        started.set()
        release.wait(5)
        seen["cancelled"] = scan_gate.cancel_requested()
        return {"ok": True}

    thread = threading.Thread(
        target=heavy_jobs.run_heavy, args=("ml_lookup_cache_build", build),
        kwargs={"label": "ml_lookup:P", "product": "P"}, daemon=True)
    thread.start()
    assert started.wait(5)
    task_id = scan_gate.snapshot()["read_lane"][0]["id"]
    assert scan_gate.busy()
    assert scan_gate.cancel(task_id, by="admin")["state"] == "requested"
    release.set()
    thread.join(5)
    assert seen["cancelled"] is True
    assert not scan_gate.busy()
