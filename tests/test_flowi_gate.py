"""tests/test_flowi_gate.py — Flow-i 운용 게이트 (권한 + 동시성 + 리소스 양보).

hermetic: users.csv/리소스 스냅샷은 monkeypatch. 실제 대기 시간은 0으로 둔다.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

import pytest  # noqa: E402

from core import flowi_gate, home_orchestrator  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_gate_state(monkeypatch):
    flowi_gate._ACTIVE.clear()
    monkeypatch.setattr(flowi_gate, "_WAITING", 0)
    monkeypatch.setenv("FLOW_FLOWI_QUEUE_WAIT_SEC", "0")
    yield
    flowi_gate._ACTIVE.clear()


def _no_resource_block(monkeypatch):
    monkeypatch.setattr(flowi_gate, "_resource_block_reason", lambda: "")


# ── 접근 권한 ────────────────────────────────────────────────────────────────

def test_access_allowed_admin_always(monkeypatch):
    monkeypatch.setattr("routers.auth.read_users", lambda: [])
    assert flowi_gate.access_allowed({"username": "boss", "role": "admin"}) is True


def test_access_requires_flowi_tab(monkeypatch):
    rows = [
        {"username": "alice", "role": "user", "tabs": "flowi,splittable"},
        {"username": "bob", "role": "user", "tabs": "splittable,filebrowser"},
        {"username": "carol", "role": "user", "tabs": "__all__"},
    ]
    monkeypatch.setattr("routers.auth.read_users", lambda: rows)
    assert flowi_gate.access_allowed({"username": "alice", "role": "user"}) is True
    assert flowi_gate.access_allowed({"username": "bob", "role": "user"}) is False
    assert flowi_gate.access_allowed({"username": "carol", "role": "user"}) is True
    assert flowi_gate.access_allowed({"username": "", "role": "user"}) is False
    assert flowi_gate.access_allowed(None) is False


def test_access_uses_fresh_users_file_over_session_snapshot(monkeypatch):
    # 세션 스냅샷에는 flowi 가 있어도 users.csv 에서 회수됐으면 거부.
    monkeypatch.setattr(
        "routers.auth.read_users",
        lambda: [{"username": "dave", "role": "user", "tabs": "splittable"}],
    )
    assert flowi_gate.access_allowed(
        {"username": "dave", "role": "user", "tabs": "flowi"}) is False


def test_denied_payload_shape():
    payload = flowi_gate.denied_payload({"username": "bob"})
    assert payload["blocked"] is True
    assert payload["intent"] == "flowi_access_denied"
    assert payload["missing_permission"] == "flowi"
    assert "권한" in payload["answer"]


# ── 동시성 게이트 ────────────────────────────────────────────────────────────

def test_slot_concurrency_limit_and_busy_message(monkeypatch):
    _no_resource_block(monkeypatch)
    monkeypatch.setenv("FLOW_FLOWI_MAX_CONCURRENCY", "1")
    with flowi_gate.slot(username="u1"):
        with pytest.raises(flowi_gate.FlowiBusy) as exc:
            with flowi_gate.slot(username="u2"):
                pass
        assert exc.value.info["reason"] == "concurrency"
        assert exc.value.info["active"] == 1
        assert "잠시 후" in exc.value.message
    # 슬롯 반납 후에는 즉시 획득 가능.
    with flowi_gate.slot(username="u2"):
        assert flowi_gate.snapshot()["active"] == 1
    assert flowi_gate.snapshot()["active"] == 0


def test_slot_admin_bypasses_admission_but_counts(monkeypatch):
    monkeypatch.setenv("FLOW_FLOWI_MAX_CONCURRENCY", "1")
    # 리소스가 바빠도 admin 은 진입한다 (검사 자체를 건너뜀).
    monkeypatch.setattr(flowi_gate, "_resource_block_reason", lambda: "cpu")
    with flowi_gate.slot(username="u1", role="admin"):
        with flowi_gate.slot(username="boss", role="admin"):
            assert flowi_gate.snapshot()["active"] == 2


def test_slot_queue_wait_succeeds_when_released(monkeypatch):
    _no_resource_block(monkeypatch)
    monkeypatch.setenv("FLOW_FLOWI_MAX_CONCURRENCY", "1")
    monkeypatch.setenv("FLOW_FLOWI_QUEUE_WAIT_SEC", "5")
    entered = threading.Event()

    def _holder():
        with flowi_gate.slot(username="holder"):
            entered.set()
            time.sleep(0.5)

    t = threading.Thread(target=_holder, daemon=True)
    t.start()
    assert entered.wait(2.0)
    # holder 가 0.5초 뒤 반납 → 대기 후 진입 성공해야 한다.
    with flowi_gate.slot(username="queued"):
        assert flowi_gate.snapshot()["active"] == 1
    t.join(2.0)


# ── 리소스 게이트 ────────────────────────────────────────────────────────────

def test_slot_defers_on_memory_pressure(monkeypatch):
    monkeypatch.setattr(flowi_gate.runtime_limits, "process_memory_high", lambda reserve_gb=1.0: True)
    with pytest.raises(flowi_gate.FlowiBusy) as exc:
        with flowi_gate.slot(username="u1"):
            pass
    assert exc.value.info["reason"] == "memory"


def test_slot_defers_on_cpu_pressure(monkeypatch):
    monkeypatch.setattr(flowi_gate.runtime_limits, "process_memory_high", lambda reserve_gb=1.0: False)
    monkeypatch.setattr(
        flowi_gate.runtime_limits, "process_cpu_snapshot",
        lambda sample_seconds=0.0, guard_cores=None: {"process_cpu_cores": 99.0})
    with pytest.raises(flowi_gate.FlowiBusy) as exc:
        with flowi_gate.slot(username="u1"):
            pass
    assert exc.value.info["reason"] == "cpu"
    assert "바빠" in exc.value.message


def test_busy_payload_shape(monkeypatch):
    busy = flowi_gate.FlowiBusy("잠시 후 다시", {"reason": "concurrency", "active": 2})
    payload = flowi_gate.busy_payload(busy)
    assert payload["intent"] == "flowi_busy"
    assert payload["busy"]["reason"] == "concurrency"


# ── 오케스트레이터 기능 권한 필터 ────────────────────────────────────────────

def _tools():
    return [
        {"kind": "unit_ai", "name": "split_nav", "title": "SplitTable"},
        {"kind": "unit_ai", "name": "filebrowser_ai_sql", "title": "FileBrowser AI SQL"},
        {"kind": "unit_ai", "name": "step_lookup", "title": "Step 매칭"},
        {"kind": "function", "name": "query_splittable_view", "title": "Split 조회"},
        {"kind": "function", "name": "preview_filebrowser_data", "title": "파일 미리보기"},
        {"kind": "function", "name": "route_flowi_feature", "title": "라우터"},
    ]


def test_filter_tools_drops_unpermitted_features(monkeypatch):
    from routers import llm as llm_router
    monkeypatch.setattr(llm_router, "_allowed_flowi_feature_keys", lambda _me: {"splittable"})
    out = home_orchestrator._filter_tools_for_user(_tools(), {"username": "bob", "role": "user"})
    names = [t["name"] for t in out]
    assert "split_nav" in names
    assert "step_lookup" in names            # splittable 로도 허용되는 조회 유닛
    assert "filebrowser_ai_sql" not in names  # filebrowser 권한 없음
    assert "query_splittable_view" in names
    assert "preview_filebrowser_data" not in names
    assert "route_flowi_feature" in names     # 범용 라우터는 유지


def test_filter_tools_admin_and_internal_unfiltered(monkeypatch):
    tools = _tools()
    assert home_orchestrator._filter_tools_for_user(tools, {"username": "boss", "role": "admin"}) == tools
    assert home_orchestrator._filter_tools_for_user(tools, None) == tools
    assert home_orchestrator._filter_tools_for_user(tools, {"username": ""}) == tools


def test_execute_step_blocks_unpermitted_unit(monkeypatch):
    from routers import llm as llm_router
    monkeypatch.setattr(llm_router, "_allowed_flowi_feature_keys", lambda _me: set())
    out = home_orchestrator._execute_step(
        {"kind": "unit_ai", "name": "filebrowser_ai_sql", "title": "FileBrowser AI SQL"},
        {"prompt": "PRODA에서 IOFF 높은 wafer"},
        user={"username": "bob", "role": "user"},
    )
    assert out["ok"] is False
    assert out["blocked"] is True
    assert out["result"]["intent"] == "permission_denied"


def test_execute_step_allows_permitted_unit_path(monkeypatch):
    # 권한이 있으면 가드를 통과해 기존 실행 경로(디스패치)로 진행한다.
    from routers import llm as llm_router
    from core.flowi_units import dispatcher
    monkeypatch.setattr(llm_router, "_allowed_flowi_feature_keys", lambda _me: {"splittable"})
    monkeypatch.setattr(
        dispatcher, "try_dispatch",
        lambda prompt, product="", max_rows=12, only=None, **_k: {"handled": True, "answer": "ok"})
    out = home_orchestrator._execute_step(
        {"kind": "unit_ai", "name": "split_nav", "title": "SplitTable"},
        {"prompt": "A1001 스플릿"},
        user={"username": "alice", "role": "user"},
    )
    assert out.get("blocked") is not True
    assert out["result"].get("intent") != "permission_denied"
