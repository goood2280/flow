from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core import llm_adapter  # noqa: E402
from core.flowi_units.dashboard_agent_runtime import dashboard_agent_graph, run_dashboard_agent_runtime  # noqa: E402
from routers import agent  # noqa: E402


class _State:
    def __init__(self, user: dict):
        self.user = user


class _Request:
    headers = {}

    def __init__(self):
        self.state = _State({"username": "tester", "role": "admin"})


def test_dashboard_agent_graph_and_catalog(monkeypatch):
    monkeypatch.setattr(agent, "current_user", lambda _request: {"username": "tester", "role": "admin"})

    graph = dashboard_agent_graph()
    assert [node["id"] for node in graph["nodes"]] == [
        "semantic_layer",
        "chart_intent",
        "chart_type_select",
        "params_fill",
        "spec_validate",
        "render_spec",
    ]
    assert graph["edges"][0] == {"source": "semantic_layer", "target": "chart_intent"}

    catalog = agent.unit_ai_catalog(_Request())
    keys = [unit["key"] for unit in catalog["units"]]
    assert keys == [
        "filebrowser_ai_sql",
        "inform_registration",
        "change_management",
        "dashboard_agent",
        "home_sql_join_dashboard",
    ]


def test_dashboard_agent_run_returns_valid_chart_spec(monkeypatch):
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)

    def fake_complete_json(ask, *, system="", timeout=0, max_retries=0, schema=None):
        payload = json.loads(ask)
        if "chart type" in system:
            assert "scatter" in payload["available_chart_types"]
            return {"ok": True, "obj": {"chart_type": "scatter", "reason": "numeric relationship"}}
        return {"ok": True, "obj": {"x": "wafer_id", "y": "IOFF", "group": "lot_id", "color": "lot_id", "agg": "raw", "title": "IOFF by wafer"}}

    monkeypatch.setattr(llm_adapter, "complete_json", fake_complete_json)

    out = run_dashboard_agent_runtime(
        {
            "natural_language": "wafer별 IOFF 산점도 그려줘",
            "columns": ["wafer_id", "IOFF", "lot_id"],
            "sample_rows": [
                {"wafer_id": 1, "IOFF": 0.12, "lot_id": "A1000"},
                {"wafer_id": 2, "IOFF": 0.2, "lot_id": "A1000"},
            ],
        },
        username="tester",
    )

    assert out["ok"] is True
    assert out["chart_result"]["chart_type"] == "scatter"
    assert out["chart_result"]["chart_config"]["x"] == "wafer_id"
    assert out["chart_result"]["chart_config"]["y"] == "IOFF"
    assert out["chart_result"]["points"][0]["x"] == 1
    assert [row["node_id"] for row in out["trace"]] == [
        "semantic_layer",
        "chart_intent",
        "chart_type_select",
        "params_fill",
        "spec_validate",
        "render_spec",
    ]


def test_dashboard_agent_falls_back_without_llm(monkeypatch):
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)

    out = run_dashboard_agent_runtime(
        {
            "natural_language": "value trend",
            "columns": ["tkout_time", "value", "lot_id"],
            "sample_rows": [{"tkout_time": "2026-01-01", "value": 1.2, "lot_id": "L1"}],
        }
    )

    assert out["ok"] is True
    assert out["chart_result"]["chart_type"]
    assert out["warnings"]
