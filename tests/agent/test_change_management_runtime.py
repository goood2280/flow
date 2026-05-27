from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core import llm_adapter  # noqa: E402
from core.flowi_units import change_management_runtime as runtime  # noqa: E402
from routers import agent, calendar as calendar_router, meetings  # noqa: E402


class _State:
    def __init__(self, user: dict):
        self.user = user


class _Request:
    headers = {}
    method = "GET"
    query_params = {}

    def __init__(self, username: str = "viewer", role: str = "user"):
        self.state = _State({"username": username, "role": role})


class _DummyPaths:
    def __init__(self, root: Path):
        self.data_root = root / "flow-data"
        self.data_root.mkdir(parents=True, exist_ok=True)


def _meeting(mid: str, title: str, *, group_ids=None, action="Send inform mail", decision="Proceed with MASK_A"):
    return {
        "id": mid,
        "title": title,
        "owner": "owner",
        "created_by": "owner",
        "status": "active",
        "group_ids": list(group_ids or []),
        "sessions": [{
            "id": f"SS-{mid}",
            "idx": 1,
            "scheduled_at": "2026-05-12T09:00:00",
            "status": "completed",
            "agendas": [{"title": "Mask change review", "description": "Check split table result", "owner": "owner"}],
            "minutes": {
                "body": "Reviewed mask split change.",
                "decisions": [{"id": f"d-{mid}", "text": decision, "due": "2026-05-13"}],
                "action_items": [{"id": f"a-{mid}", "text": action, "owner": "worker", "due": "2026-05-14", "status": "pending"}],
            },
        }],
    }


def _install_fixture(monkeypatch, tmp_path: Path, rows: list[dict], events: list[dict] | None = None) -> None:
    paths = _DummyPaths(tmp_path)
    monkeypatch.setattr(runtime, "PATHS", paths)
    monkeypatch.setattr(runtime, "current_user", lambda request: request.state.user)
    monkeypatch.setattr(agent, "current_user", lambda request: request.state.user)
    monkeypatch.setattr(meetings, "_load", lambda: rows)
    monkeypatch.setattr(meetings, "_my_meeting_group_ids", lambda _username, _role: set())
    monkeypatch.setattr(calendar_router, "_my_group_ids", lambda _username, _role: set())
    monkeypatch.setattr(calendar_router, "_load_events", lambda: list(events or []))


def _run(prompt: str, request: _Request | None = None) -> dict:
    return agent.unit_ai_runtime_run(
        "change_management",
        agent.UnitAiRuntimeRunReq(prompt=prompt),
        request or _Request(),
    )


def test_change_management_graph_shape_and_catalog(monkeypatch):
    monkeypatch.setattr(agent, "current_user", lambda request: request.state.user)

    graph_payload = runtime.change_management_graph()
    assert [node["id"] for node in graph_payload["nodes"]] == [
        "context_scope",
        "meeting_reference",
        "evidence_pack",
        "answer_compose",
    ]
    assert graph_payload["state_design"]["answer_pack"]["producer"] == "answer_compose"
    for node in graph_payload["nodes"]:
        assert node["persona"]
        assert isinstance(node["state_io"]["reads"], list)
        assert isinstance(node["state_io"]["writes"], list)
        assert node["answer_attach_rule"]

    catalog = agent.unit_ai_catalog(_Request())
    change = next(unit for unit in catalog["units"] if unit["key"] == "change_management")
    assert change["title"] == "변경점 관리 Flow-i"
    assert change["handler_entry"]["function"] == "run_change_management_runtime"

    graph = agent.unit_ai_runtime_graph("change_management", _Request())
    assert graph["ok"] is True
    assert graph["graph"]["nodes"][0]["id"] == "context_scope"


def test_change_management_runtime_resolves_meeting_and_writes_history(monkeypatch, tmp_path):
    sync = _meeting("MT-SYNC", "Device Change Sync", action="Send mask owner due mail")
    review = _meeting("MT-REVIEW", "PM Review", action="Review unrelated PM window")
    _install_fixture(monkeypatch, tmp_path, [sync, review])
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)

    out = _run("Device Change Sync 회의 액션 담당자와 마감일 정리해줘")

    assert out["ok"] is True
    assert out["needs_clarification"] is False
    assert out["meeting_reference"]["focus_meeting_id"] == "MT-SYNC"
    assert "Send mask owner due mail" in out["answer"]
    assert "Review unrelated PM window" not in out["answer"]
    assert "**" not in out["answer"]
    assert "###" not in out["answer"]
    assert [row["node_id"] for row in out["trace"]] == [
        "context_scope",
        "meeting_reference",
        "evidence_pack",
        "answer_compose",
    ]

    history = agent.unit_ai_runtime_history("change_management", _Request())["history"]
    assert history[0]["run_id"] == out["run_id"]
    assert history[0]["meeting_reference"]["focus_meeting_id"] == "MT-SYNC"
    assert history[0]["answer"]


def test_change_management_runtime_does_not_guess_ambiguous_meeting(monkeypatch, tmp_path):
    sync = _meeting("MT-SYNC", "Device Change Sync")
    review = _meeting("MT-REVIEW", "Device Change Review")
    hidden = _meeting("MT-HIDDEN", "Device Change Secret", group_ids=["secret-group"])
    _install_fixture(monkeypatch, tmp_path, [sync, review, hidden])
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)

    out = _run("Device Change 회의 결정사항 정리해줘")

    assert out["ok"] is True
    assert out["needs_clarification"] is True
    assert out["status"] == "needs_clarification"
    titles = [row["title"] for row in out["meeting_reference"]["candidates"]]
    assert set(titles) == {"Device Change Sync", "Device Change Review"}
    assert "Device Change Secret" not in titles
    assert "회의명을 확인" in out["answer"]


def test_change_management_runtime_strips_llm_markdown(monkeypatch, tmp_path):
    sync = _meeting("MT-SYNC", "Device Change Sync")
    _install_fixture(monkeypatch, tmp_path, [sync])
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(
        llm_adapter,
        "complete",
        lambda *_args, **_kwargs: {"ok": True, "text": "### 요약\n**결정사항**\n- Proceed with MASK_A"},
    )

    out = _run("Device Change Sync 회의 결정사항 정리해줘")

    assert out["llm"]["used"] is True
    assert "###" not in out["answer"]
    assert "**" not in out["answer"]
    assert "- Proceed" not in out["answer"]
    assert "Proceed with MASK_A" in out["answer"]
