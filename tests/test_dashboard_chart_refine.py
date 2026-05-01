from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core import auth as auth_core  # noqa: E402
from core import dashboard_join  # noqa: E402
from routers import dashboard  # noqa: E402


class _State:
    pass


class _Request:
    headers = {}

    def __init__(self):
        self.state = _State()


def test_chart_refine_session_updates_style_state(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard_join, "CHART_SESSION_DIR", tmp_path / "sessions")
    sid = dashboard_join.save_chart_session({
        "username": "tester",
        "chart_type": "scatter",
        "config": {"font_size": 14, "legend": True},
        "base_data_query": {"x_item": "CD", "y_item": "LKG"},
        "data": [{"x": 1, "y": 2}],
    })
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "tester", "role": "user"})

    req = _Request()
    bigger = dashboard.chart_refine(dashboard.ChartRefineReq(chart_session_id=sid, action="font_size_delta", value=2), req)
    assert bigger["config"]["font_size"] == 16

    legend = dashboard.chart_refine(dashboard.ChartRefineReq(chart_session_id=sid, action="legend", value=False), req)
    assert legend["config"]["legend"] is False

    log = dashboard.chart_refine(dashboard.ChartRefineReq(chart_session_id=sid, action="y_scale", value="log"), req)
    assert log["config"]["y_scale"] == "log"
    assert len(log["history"]) == 3
