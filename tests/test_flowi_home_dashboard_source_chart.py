from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
for p in (BACKEND, ROOT):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)


def test_flowi_source_chart_uses_home_dashboard_runtime(monkeypatch):
    from routers import llm
    from core.flowi_units import home_sql_join_dashboard_runtime as runtime

    captured: dict[str, object] = {}

    def fake_runtime(payload, *, username="", agent_context=None):
        captured["payload"] = payload
        captured["username"] = username
        captured["agent_context"] = agent_context
        chart_result = {
            "ok": True,
            "kind": "dashboard_scatter",
            "chart_type": "scatter",
            "title": "IOFF by wafer",
            "points": [{"x": 1, "y": 0.12}],
            "total": 1,
            "config": {
                "chart_type": "scatter",
                "x": "wafer_id",
                "y": "IOFF",
                "source_evidence": {
                    "source_ids": ["db_FAB"],
                    "relation_ids": [],
                    "join_keys": [],
                    "selected_columns": ["wafer_id", "IOFF"],
                },
            },
            "chart_config": {"chart_type": "scatter", "x": "wafer_id", "y": "IOFF"},
        }
        return {
            "ok": True,
            "status": "success",
            "run_id": "source_dash_1",
            "unit_ai": "home_sql_join_dashboard",
            "trace": [{"node_id": "dashboard_draft", "status": "success", "warnings": []}],
            "source_resolution": {"needs_input": False, "selected": {"source_id": "db_FAB", "root": "FAB", "product": "PRODA"}},
            "ai_sql": {"display_sql": "SELECT wafer_id, IOFF", "selected_columns": ["wafer_id", "IOFF"], "ok": True},
            "data_need": {"needs_join": False},
            "join_plan": {"single_source": True, "blocked": False, "relation_ids": [], "join_keys": []},
            "joined": {
                "row_count": 1,
                "columns": ["wafer_id", "IOFF"],
                "sample_rows": [{"wafer_id": 1, "IOFF": 0.12}],
            },
            "output_route": {"mode": "chart"},
            "dashboard": {
                "chart_type": "scatter",
                "config": chart_result["config"],
                "chart_result": chart_result,
            },
            "chart_type": "scatter",
            "config": chart_result["config"],
            "chart_result": chart_result,
            "warnings": [],
        }

    monkeypatch.setattr(runtime, "run_home_sql_join_dashboard_runtime", fake_runtime)

    out = llm._run_flowi_chat(
        prompt="PRODA FAB IOFF chart",
        product="PRODA",
        max_rows=12,
        me={"username": "tester", "role": "admin"},
        agent_context={},
    )

    assert captured["payload"]["root"] == "FAB"
    assert captured["payload"]["product"] == "PRODA"
    assert out["tool"]["feature"] == "dashboard"
    assert out["tool"]["source_orchestration"] is True
    assert out["tool"]["selected_columns"] == ["wafer_id", "IOFF"]
    assert out["tool"]["chart_result"]["points"][0]["y"] == 0.12
    assert out["tool"]["chart_result"]["config"]["source_evidence"]["source_ids"] == ["db_FAB"]
