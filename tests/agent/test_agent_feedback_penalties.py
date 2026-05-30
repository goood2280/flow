from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core import agent_feedback_penalties, home_orchestrator, tool_registry  # noqa: E402
from routers import agent  # noqa: E402


class _State:
    def __init__(self, user: dict):
        self.user = user


class _Request:
    headers = {}
    method = "GET"
    query_params = {}

    def __init__(self, username: str = "tester", role: str = "admin"):
        self.state = _State({"username": username, "role": role})


def _tool(name: str, *, tags: list[str], enabled: bool = True) -> dict:
    return {
        "name": name,
        "kind": "unit_ai",
        "title": name,
        "description": name,
        "tags": tags,
        "enabled": enabled,
        "examples": [],
    }


def test_unit_ai_feedback_api_records_unit_and_node_profile(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_feedback_penalties, "PENALTIES_FILE", tmp_path / "agent_feedback_penalties.json")
    monkeypatch.setattr(agent, "current_user", lambda _request: {"username": "tester", "role": "admin"})

    empty = agent.unit_ai_feedback_profile("dashboard_agent", _Request())["profile"]
    assert empty["unit"]["penalty"] == 0
    assert empty["unit"]["boost"] == 0

    down = agent.unit_ai_feedback(
        "dashboard_agent",
        agent.UnitAiFeedbackReq(rating="down", node_id="params_fill", run_id="run-1", reason="wrong y"),
        _Request(),
    )
    profile = down["profile"]
    assert profile["unit"]["down_count"] == 1
    assert profile["unit"]["penalty"] == 1
    assert profile["nodes"]["params_fill"]["down_count"] == 1
    assert profile["nodes"]["params_fill"]["penalty"] == 1

    up = agent.unit_ai_feedback(
        "dashboard_agent",
        agent.UnitAiFeedbackReq(rating="up", run_id="run-2"),
        _Request(),
    )
    assert up["profile"]["unit"]["up_count"] == 1
    assert up["profile"]["unit"]["penalty"] == 0


def test_unit_ai_feedback_api_rejects_bad_rating_and_unknown_unit(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_feedback_penalties, "PENALTIES_FILE", tmp_path / "agent_feedback_penalties.json")
    monkeypatch.setattr(agent, "current_user", lambda _request: {"username": "tester", "role": "admin"})

    with pytest.raises(HTTPException) as bad_rating:
        agent.unit_ai_feedback("dashboard_agent", agent.UnitAiFeedbackReq(rating="meh"), _Request())
    assert bad_rating.value.status_code == 400

    with pytest.raises(HTTPException) as missing:
        agent.unit_ai_feedback_profile("missing_unit", _Request())
    assert missing.value.status_code == 404


def test_runtime_graph_and_trace_include_node_feedback_penalty(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_feedback_penalties, "PENALTIES_FILE", tmp_path / "agent_feedback_penalties.json")
    monkeypatch.setattr(agent, "current_user", lambda _request: {"username": "tester", "role": "admin"})
    agent_feedback_penalties.record_feedback("dashboard_agent", "down", node_id="params_fill", actor="tester")

    graph = agent.unit_ai_runtime_graph("dashboard_agent", _Request())["graph"]
    params = next(node for node in graph["nodes"] if node["id"] == "params_fill")
    assert params["feedback_penalty"]["penalty"] == 1

    trace = agent_feedback_penalties.annotate_trace(
        "dashboard_agent",
        [{"node_id": "params_fill", "status": "success"}],
    )
    assert trace[0]["feedback_penalty"]["down_count"] == 1


def test_home_routing_uses_unit_feedback_penalty_for_tie_break(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_feedback_penalties, "PENALTIES_FILE", tmp_path / "agent_feedback_penalties.json")
    tools = [
        _tool("home_sql_join_dashboard", tags=["chart"]),
        _tool("dashboard_agent", tags=["chart"]),
    ]
    monkeypatch.setattr(tool_registry, "list_tools", lambda include_stats=False: tools)

    agent_feedback_penalties.record_feedback("dashboard_agent", "up", actor="tester")
    picks, meta = home_orchestrator._pick_tools("차트로 보여줘", top_k=2)
    assert [row["tool"]["name"] for row in picks] == ["dashboard_agent", "home_sql_join_dashboard"]
    assert meta["feedback_penalties"]["dashboard_agent"]["boost"] > 0

    monkeypatch.setattr(agent_feedback_penalties, "PENALTIES_FILE", tmp_path / "agent_feedback_penalties_2.json")
    agent_feedback_penalties.record_feedback("dashboard_agent", "down", actor="tester")
    picks, meta = home_orchestrator._pick_tools("차트로 보여줘", top_k=2)
    assert [row["tool"]["name"] for row in picks] == ["home_sql_join_dashboard", "dashboard_agent"]
    assert meta["feedback_penalties"]["dashboard_agent"]["penalty"] > 0


def test_llm_catalog_marks_down_rated_unit_as_low_priority(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_feedback_penalties, "PENALTIES_FILE", tmp_path / "agent_feedback_penalties.json")
    agent_feedback_penalties.record_feedback("dashboard_agent", "down", actor="tester")

    text = home_orchestrator._format_tool_catalog([
        _tool("dashboard_agent", tags=["chart"]),
        _tool("filebrowser_ai_sql", tags=["filebrowser"]),
    ])

    assert "- dashboard_agent (unit_ai): dashboard_agent" in text
    assert "feedback: low_priority penalty=1" in text
