from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core import home_memory, home_orchestrator  # noqa: E402


def test_filebrowser_ai_sql_output_flows_to_dashboard_agent(monkeypatch):
    filebrowser_tool = {
        "name": "filebrowser_ai_sql",
        "kind": "unit_ai",
        "title": "FileBrowser AI SQL",
        "enabled": True,
    }
    dashboard_tool = {
        "name": "dashboard_agent",
        "kind": "unit_ai",
        "title": "Dashboard Agent",
        "enabled": True,
    }
    plan = [
        {"tool": filebrowser_tool, "input": {"prompt": "PRODA 이상치 찾기", "root": "FAB", "product": "PRODA"}, "reason": "sql", "source": "test"},
        {"tool": dashboard_tool, "input": {"prompt": "분포 그려줘"}, "reason": "chart", "source": "test"},
    ]
    captured: dict[str, object] = {}

    monkeypatch.setattr(home_memory, "is_memory_recall_prompt", lambda _prompt: False)
    monkeypatch.setattr(home_orchestrator.tool_registry, "list_tools", lambda include_stats=False: [filebrowser_tool, dashboard_tool])
    monkeypatch.setattr(home_orchestrator, "_plan_from_alias", lambda _prompt, _tools: plan)
    monkeypatch.setattr(home_orchestrator, "_attach_runtime_result", lambda out, **_kwargs: out)

    from core.flowi_units import filebrowser_ai_sql_runtime
    from core.flowi_units import dashboard_agent_runtime

    def fake_filebrowser_runtime(payload, *, username="", agent_context=None):
        return {
            "ok": True,
            "run_id": "fb_1",
            "unit_ai": "filebrowser_ai_sql",
            "trace": [{"node_id": "preview_apply", "status": "success", "warnings": []}],
            "semantic_frame": {"resolved_columns": ["wafer_id", "IOFF"]},
            "filter": {"warnings": []},
            "columns": {"selected_columns": ["wafer_id", "IOFF", "lot_id"], "warnings": []},
            "merged": {"display_sql": "SELECT wafer_id, IOFF, lot_id", "where_sql": "", "selected_columns": ["wafer_id", "IOFF", "lot_id"]},
            "preview": {
                "columns": ["wafer_id", "IOFF", "lot_id"],
                "rows": [{"wafer_id": 1, "IOFF": 0.12, "lot_id": "A1000"}],
                "rows_returned": 1,
                "total_rows": 1,
                "warnings": [],
            },
            "warnings": [],
        }

    def fake_dashboard_runtime(payload, *, username="", agent_context=None):
        captured["payload"] = payload
        captured["agent_context"] = agent_context
        return {
            "ok": True,
            "status": "success",
            "run_id": "dash_1",
            "unit_ai": "dashboard_agent",
            "trace": [{"node_id": "render_spec", "status": "success", "warnings": []}],
            "chart_type": "scatter",
            "config": {"chart_type": "scatter", "x": "wafer_id", "y": "IOFF"},
            "chart_result": {
                "ok": True,
                "kind": "dashboard_scatter",
                "chart_type": "scatter",
                "points": [{"x": 1, "y": 0.12}],
                "total": 1,
                "config": {"chart_type": "scatter", "x": "wafer_id", "y": "IOFF"},
                "chart_config": {"chart_type": "scatter", "x": "wafer_id", "y": "IOFF"},
            },
            "warnings": [],
        }

    monkeypatch.setattr(filebrowser_ai_sql_runtime, "run_filebrowser_ai_sql_runtime", fake_filebrowser_runtime)
    monkeypatch.setattr(dashboard_agent_runtime, "run_dashboard_agent_runtime", fake_dashboard_runtime)

    out = home_orchestrator.orchestrate("PRODA 이상치 찾고 분포 그려줘", user={"username": "tester"})

    assert out["ok"] is True
    assert [row["tool"] for row in out["trace"]] == ["filebrowser_ai_sql", "dashboard_agent"]
    assert captured["payload"]["columns"] == ["wafer_id", "IOFF", "lot_id"]
    assert captured["payload"]["sample_rows"] == [{"wafer_id": 1, "IOFF": 0.12, "lot_id": "A1000"}]
    parent = captured["agent_context"]
    assert parent["tool_calls"][0]["tool"] == "filebrowser_ai_sql"
    assert parent["last_output"]["preview"]["rows"][0]["IOFF"] == 0.12
