from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core import auth as auth_core  # noqa: E402
from core import product_config  # noqa: E402
from routers import dashboard  # noqa: E402


class _State:
    pass


class _Request:
    headers = {}

    def __init__(self):
        self.state = _State()


def test_dashboard_items_endpoint_merges_product_config(monkeypatch):
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "tester", "role": "admin"})
    monkeypatch.setattr(
        product_config,
        "load",
        lambda _root, _product: {
            "canonical_inline_items": ["CD_CUSTOM"],
            "et_key_items": ["LKG_CUSTOM"],
            "canonical_knobs": ["KNOB_STI"],
        },
    )
    req = _Request()
    et = dashboard.get_dashboard_items(req, group="ET", product="PRODX")["items"]
    inline = dashboard.get_dashboard_items(req, group="INLINE", product="PRODX")["items"]
    knob = dashboard.get_dashboard_items(req, group="KNOB", product="PRODX")["items"]

    assert any(row["key"] == "LKG_CUSTOM" and row["source_type"] == "ET" for row in et)
    assert any(row["key"] == "CD_CUSTOM" and row["default_chart_type"] == "scatter" for row in inline)
    assert any(row["key"] == "STI" and row["source_type"] == "KNOB" for row in knob)
