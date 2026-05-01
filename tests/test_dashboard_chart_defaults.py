from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

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


def _auth(monkeypatch, role: str = "admin") -> None:
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "tester", "role": role})


def test_chart_defaults_get_post_substitution_and_admin_guard(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard_join, "CHART_DEFAULTS_FILE", tmp_path / "dashboard_chart_defaults.json")
    _auth(monkeypatch, "admin")
    req = _Request()

    got = dashboard.get_chart_defaults(req)
    assert got["defaults"]["scatter"]["x"] == "$item1"

    posted = dashboard.post_chart_defaults(
        dashboard.ChartDefaultReq(chart_type="scatter", config={"x": "$item1", "y": "$item2", "color": "lot_id", "agg": "raw"}),
        req,
    )
    assert posted["config"]["y"] == "$item2"

    cfg = dashboard_join.apply_chart_defaults("scatter", ["CD", "LKG"])
    assert cfg["x"] == "CD"
    assert cfg["y"] == "LKG"

    _auth(monkeypatch, "user")
    with pytest.raises(HTTPException) as exc:
        dashboard.post_chart_defaults(dashboard.ChartDefaultReq(chart_type="trend", config={"x": "time"}), _Request())
    assert exc.value.status_code == 403
