from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core.flowi_units import dashboard_agent_runtime, home_sql_join_dashboard_runtime as runtime  # noqa: E402


def test_dashboard_draft_delegates_to_dashboard_agent_preserving_chart_spec(monkeypatch):
    expected_chart = {
        "ok": True,
        "kind": "dashboard_scatter",
        "chart_type": "scatter",
        "title": "IOFF by wafer",
        "points": [{"x": 1, "y": 0.12}],
        "total": 1,
        "config": {"chart_type": "scatter", "x": "wafer_id", "y": "IOFF"},
        "chart_config": {"chart_type": "scatter", "x": "wafer_id", "y": "IOFF"},
        "status": "draft",
    }

    def fake_dashboard_agent(payload, *, username="", agent_context=None):
        assert payload["columns"] == ["wafer_id", "IOFF", "lot_id"]
        assert payload["sample_rows"][0]["IOFF"] == 0.12
        return {
            "ok": True,
            "status": "success",
            "run_id": "dash_sub",
            "trace": [{"node_id": "render_spec", "status": "success", "warnings": []}],
            "chart_type": "scatter",
            "config": expected_chart["config"],
            "chart_result": expected_chart,
            "warnings": [],
        }

    monkeypatch.setattr(dashboard_agent_runtime, "run_dashboard_agent_runtime", fake_dashboard_agent)

    warnings: list[str] = []
    out = runtime._dashboard_draft(
        {
            "username": "tester",
            "request": {"natural_language": "IOFF 산점도"},
            "base_source": {"product": "PRODA"},
            "output_route": {"mode": "chart"},
            "joined": {
                "columns": ["wafer_id", "IOFF", "lot_id"],
                "sample_rows": [{"wafer_id": 1, "IOFF": 0.12, "lot_id": "A1000"}],
                "row_count": 1,
            },
        },
        warnings,
    )

    assert warnings == []
    assert out["dashboard"]["chart_result"] == expected_chart
    assert out["dashboard"]["sub_run_id"] == "dash_sub"
    assert out["dashboard"]["sub_trace"][0]["node_id"] == "render_spec"
