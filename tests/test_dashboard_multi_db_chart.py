from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

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


def test_multi_db_chart_join_color_group_fit_and_stats(tmp_path, monkeypatch):
    inline_fp = tmp_path / "INLINE_PRODX.parquet"
    et_fp = tmp_path / "ET_PRODX.parquet"
    fab_fp = tmp_path / "FAB_PRODX.parquet"
    ml_fp = tmp_path / "ML_TABLE_PRODX.parquet"
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "L001", "fab_lot_id": "F001", "wafer_id": "01", "item_id": "CD_MEAN", "value": 10.0},
        {"product": "PRODX", "root_lot_id": "L001", "fab_lot_id": "F001", "wafer_id": "02", "item_id": "CD_MEAN", "value": 20.0},
        {"product": "PRODX", "root_lot_id": "L001", "fab_lot_id": "F001", "wafer_id": "03", "item_id": "CD_MEAN", "value": 30.0},
    ]).write_parquet(inline_fp)
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "L001", "fab_lot_id": "F001", "wafer_id": "01", "item_id": "LKG_RAW", "value": 100.0},
        {"product": "PRODX", "root_lot_id": "L001", "fab_lot_id": "F001", "wafer_id": "02", "item_id": "LKG_RAW", "value": 200.0},
        {"product": "PRODX", "root_lot_id": "L001", "fab_lot_id": "F001", "wafer_id": "03", "item_id": "LKG_RAW", "value": 300.0},
    ]).write_parquet(et_fp)
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "L001", "fab_lot_id": "F001", "wafer_id": "01", "step_id": "24.0 SORT", "eqp_id": "EQP_A", "eqp_chamber": "CH_A", "tkout_time": "2026-04-01T00:00:00"},
        {"product": "PRODX", "root_lot_id": "L001", "fab_lot_id": "F001", "wafer_id": "02", "step_id": "24.0 SORT", "eqp_id": "EQP_B", "eqp_chamber": "CH_B", "tkout_time": "2026-04-02T00:00:00"},
        {"product": "PRODX", "root_lot_id": "L001", "fab_lot_id": "F001", "wafer_id": "03", "step_id": "24.0 SORT", "eqp_id": "EQP_A", "eqp_chamber": "CH_A", "tkout_time": "2026-04-03T00:00:00"},
    ]).write_parquet(fab_fp)
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "L001", "wafer_id": "01", "KNOB_STI": "A"},
        {"product": "PRODX", "root_lot_id": "L001", "wafer_id": "02", "KNOB_STI": "B"},
        {"product": "PRODX", "root_lot_id": "L001", "wafer_id": "03", "KNOB_STI": "A"},
    ]).write_parquet(ml_fp)

    def source_files(source, _product=""):
        return {
            "INLINE": [inline_fp],
            "ET": [et_fp],
            "FAB": [fab_fp],
            "ML_TABLE": [ml_fp],
        }.get(str(source).upper(), [])

    monkeypatch.setattr(dashboard_join, "_source_files", source_files)
    monkeypatch.setattr(dashboard_join, "CHART_SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "tester", "role": "admin"})

    body = dashboard.multi_db_chart(
        dashboard.MultiDbChartReq(
            primary_source="ET",
            secondary_source="INLINE",
            x_item="CD",
            y_item="LKG",
            product="PRODX",
            color_by="fab_step_eqp:24.0 SORT",
            group_by="eqp_chamber:24.0 SORT",
            fit="linear",
            stats={"columns": ["median", "avg"]},
        ),
        _Request(),
    )
    assert body["ok"] is True
    assert body["join_keys"] == ["root_lot_id", "fab_lot_id", "wafer_id"]
    assert len(body["data"]) == 3
    assert body["fit_params"]["r2"] == 1.0
    assert body["color_by"] == "fab_step_eqp:24.0 SORT"
    assert {p["group"] for p in body["data"]} == {"CH_A", "CH_B"}
    assert len(body["panels"]) == 2
    assert set(body["stats_table"][0].keys()) <= {"group", "median", "avg"}
    assert body["chart_session_id"]

    quadratic = dashboard.multi_db_chart(
        dashboard.MultiDbChartReq(
            primary_source="ET",
            secondary_source="INLINE",
            x_item="CD",
            y_item="LKG",
            product="PRODX",
            fit="quadratic",
        ),
        _Request(),
    )
    assert quadratic["fit_params"]["degree"] == 2
    assert quadratic["fit_params"]["r2"] == 1.0

    no_stats = dashboard.multi_db_chart(
        dashboard.MultiDbChartReq(
            primary_source="ET",
            secondary_source="INLINE",
            x_item="CD",
            y_item="LKG",
            product="PRODX",
            stats=None,
        ),
        _Request(),
    )
    assert no_stats["stats_table"] == []

    knob = dashboard_join.parse_color_by("PRODA STI knob 별 ET LKG boxplot")
    assert knob == "ml_knob:STI"
