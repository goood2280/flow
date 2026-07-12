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
from core.flowi_units import dashboard_agent_runtime as runtime  # noqa: E402
from core.flowi_units.dashboard_agent_runtime import dashboard_agent_graph, run_dashboard_agent_runtime  # noqa: E402
from routers import agent  # noqa: E402


class _State:
    def __init__(self, user: dict):
        self.user = user


class _Request:
    headers = {}
    method = "GET"
    query_params = {}

    def __init__(self, username: str = "tester"):
        self.state = _State({"username": username, "role": "admin"})


class _DummyPaths:
    def __init__(self, root: Path):
        self.data_root = root / "flow-data"
        self.data_root.mkdir(parents=True, exist_ok=True)


def _install_history_fixture(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runtime, "PATHS", _DummyPaths(tmp_path))
    monkeypatch.setattr(agent, "current_user", lambda request: request.state.user)
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)


def test_dashboard_agent_graph_and_catalog(monkeypatch):
    monkeypatch.setattr(agent, "current_user", lambda _request: {"username": "tester", "role": "admin"})

    graph = dashboard_agent_graph()
    assert [node["id"] for node in graph["nodes"]] == [
        "data_context",
        "semantic_layer",
        "chart_intent",
        "chart_type_select",
        "params_fill",
        "spec_validate",
        "render_spec",
    ]
    assert graph["edges"][0] == {"source": "data_context", "target": "semantic_layer"}
    assert graph["layout"]["rankdir"] == "LR"

    catalog = agent.unit_ai_catalog(_Request())
    keys = [unit["key"] for unit in catalog["units"]]
    assert keys == [
        "filebrowser_ai_sql",
        "inform_registration",
        "change_management",
        "dashboard_agent",
        "step_lookup",
        "ppid_knob",
        "split_nav",
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
    assert out["chart_result_preview"]["points"][0]["x"] == 1
    assert out["data_context"]["has_columns"] is True
    assert [row["node_id"] for row in out["trace"]] == [
        "data_context",
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


def test_dashboard_agent_data_context_blocks_without_schema_rows_or_source(monkeypatch):
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)

    out = run_dashboard_agent_runtime({"natural_language": "차트 그려줘"})

    assert out["ok"] is False
    assert out["status"] == "blocked"
    assert out["needs_input"] is True
    assert out["data_context"]["needs_input"] is True
    assert out["trace"][0]["node_id"] == "data_context"
    assert out["trace"][0]["status"] == "warning"
    assert out["chart_result"] == {}


def test_dashboard_agent_blocks_when_explicit_axis_value_is_empty(monkeypatch):
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)

    out = run_dashboard_agent_runtime(
        {
            "natural_language": "x축은 wafer_id, y축은 scatter로 그려줘",
            "columns": ["wafer_id", "IOFF", "lot_id"],
            "sample_rows": [{"wafer_id": 1, "IOFF": 0.12, "lot_id": "A1000"}],
        }
    )

    assert out["ok"] is False
    assert out["status"] == "blocked"
    assert out["needs_input"] is True
    assert out["axis_requirements"]["needs_input"] is True
    assert {"axis": "y", "reason": "axis_value_empty"} in out["axis_requirements"]["missing"]


def test_dashboard_agent_blocks_when_both_axis_values_are_empty(monkeypatch):
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)

    out = run_dashboard_agent_runtime(
        {
            "natural_language": "x,y축 모두 필요한 scatter",
            "columns": ["wafer_id", "IOFF", "lot_id"],
            "sample_rows": [{"wafer_id": 1, "IOFF": 0.12, "lot_id": "A1000"}],
        }
    )

    missing = out["axis_requirements"]["missing"]
    assert out["status"] == "blocked"
    assert {"axis": "x", "reason": "axis_value_empty"} in missing
    assert {"axis": "y", "reason": "axis_value_empty"} in missing


def test_dashboard_agent_accepts_explicit_x_y_axes(monkeypatch):
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)

    out = run_dashboard_agent_runtime(
        {
            "natural_language": "x축 wafer_id y축 IOFF scatter",
            "columns": ["wafer_id", "IOFF", "lot_id"],
            "sample_rows": [{"wafer_id": 1, "IOFF": 0.12, "lot_id": "A1000"}],
        }
    )

    assert out["ok"] is True
    assert out["params"]["x"] == "wafer_id"
    assert out["params"]["y"] == "IOFF"


def test_dashboard_agent_run_records_sanitized_user_history(monkeypatch, tmp_path):
    _install_history_fixture(monkeypatch, tmp_path)
    payload = {
        "natural_language": "wafer별 IOFF 산점도 그려줘",
        "columns": ["wafer_id", "IOFF", "lot_id"],
        "sample_rows": [
            {"wafer_id": 1, "IOFF": 0.12, "lot_id": "SENSITIVE_SAMPLE_ROW"},
            {"wafer_id": 2, "IOFF": 0.2, "lot_id": "SENSITIVE_SAMPLE_ROW"},
        ],
    }

    direct = agent.unit_runtime_run("dashboard_agent", agent.UnitAiRuntimeRunReq(**payload), _Request("tester"))
    compat = agent.unit_ai_runtime_run(
        "dashboard_agent",
        agent.UnitAiRuntimeRunReq(**{
            **payload,
            "natural_language": "lot별 IOFF trend",
            "sample_rows": [{"wafer_id": 3, "IOFF": 0.3, "lot_id": "OTHER_PRIVATE_ROW"}],
        }),
        _Request("other"),
    )

    history = agent.unit_runtime_history("dashboard_agent", _Request("tester"))["history"]
    compat_history = agent.unit_ai_runtime_history("dashboard_agent", _Request("other"))["history"]
    assert [row["run_id"] for row in history] == [direct["run_id"]]
    assert [row["run_id"] for row in compat_history] == [compat["run_id"]]

    row = history[0]
    serialized = json.dumps(row, ensure_ascii=False)
    assert row["columns"] == ["wafer_id", "IOFF", "lot_id"]
    assert row["chart_summary"]["chart_type"]
    assert row["chart_summary"]["points_count"] == direct["chart_result"]["total"]
    assert row["trace_summary"][0]["node_id"] == "data_context"
    assert "sample_rows" not in row
    assert "points" not in row["chart_summary"]
    assert "SENSITIVE_SAMPLE_ROW" not in serialized
