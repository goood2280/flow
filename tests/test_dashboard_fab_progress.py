from __future__ import annotations

import datetime
import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from routers import dashboard  # noqa: E402


def test_fab_progress_target_eta_uses_last_three_same_product_lots(monkeypatch):
    base = datetime.datetime(2026, 4, 20, 8, 0, 0)
    rows = []
    for i, hours in enumerate([10, 20, 30, 40, 50], start=1):
        lot = f"L{i:03d}"
        start = base + datetime.timedelta(days=i)
        rows.extend([
            {"product": "PRODX", "root_lot_id": lot, "fab_lot_id": lot, "step_id": "S10", "time": start.isoformat()},
            {"product": "PRODX", "root_lot_id": lot, "fab_lot_id": lot, "step_id": "S20", "time": (start + datetime.timedelta(hours=hours)).isoformat()},
        ])
    rows.append({
        "product": "PRODX",
        "root_lot_id": "L999",
        "fab_lot_id": "L999",
        "step_id": "S10",
        "time": (base + datetime.timedelta(days=9)).isoformat(),
    })
    monkeypatch.setattr(dashboard, "_scan_dashboard_fab_long", lambda _product: pl.DataFrame(rows).lazy())

    data = dashboard._compute_fab_progress(
        product="PRODX",
        days=999,
        limit=10,
        target_step_id="S20",
        lot_query="L999",
        sample_lots=3,
    )

    bench = data["target_benchmark"]
    assert bench["samples"] == 3
    assert bench["historical_samples"] == 5
    assert bench["avg_hours"] == 40
    assert [row["root_lot_id"] for row in bench["recent_lots"]] == ["L005", "L004", "L003"]

    lot = data["wip_lots"][0]
    assert lot["root_lot_id"] == "L999"
    assert lot["target_eta"]["status"] == "estimated"
    assert lot["target_eta"]["avg_hours"] == 40


def test_fab_progress_uses_reference_step_recent_lots_for_speed_basis(monkeypatch):
    base = datetime.datetime(2026, 4, 20, 8, 0, 0)
    rows = []
    for i, hours in enumerate([10, 20, 30, 40], start=1):
        lot = f"L{i:03d}"
        start = base + datetime.timedelta(days=i)
        rows.extend([
            {"product": "PRODX", "root_lot_id": lot, "fab_lot_id": lot, "step_id": "S10", "time": start.isoformat()},
            {"product": "PRODX", "root_lot_id": lot, "fab_lot_id": lot, "step_id": "S20", "time": (start + datetime.timedelta(hours=hours)).isoformat()},
            {"product": "PRODX", "root_lot_id": lot, "fab_lot_id": lot, "step_id": "AA200000", "time": (start + datetime.timedelta(hours=hours + i)).isoformat()},
        ])
    rows.append({
        "product": "PRODX",
        "root_lot_id": "L999",
        "fab_lot_id": "L999",
        "step_id": "S10",
        "time": (base + datetime.timedelta(days=9)).isoformat(),
    })
    monkeypatch.setattr(dashboard, "_scan_dashboard_fab_long", lambda _product: pl.DataFrame(rows).lazy())

    data = dashboard._compute_fab_progress(
        product="PRODX",
        days=999,
        limit=10,
        target_step_id="S20",
        lot_query="L999",
        sample_lots=2,
        reference_step_id="AA200000",
    )

    compare = data["step_speed_compare"]
    assert compare["reference_step_id"] == "AA200000"
    assert [row["root_lot_id"] for row in compare["sample_lots"]] == ["L004", "L003"]
    assert compare["sample_pool"] == 4
    assert compare["target_eta"]["status"] == "estimated"
    assert compare["target_eta"]["avg_hours"] == 35


def test_dashboard_pushdown_filters_apply_before_collect():
    now = datetime.datetime.now()
    lf = pl.DataFrame([
        {"x": "keep", "y": 1.0, "flag": "hit", "time": now.isoformat()},
        {"x": "old", "y": 2.0, "flag": "hit", "time": (now - datetime.timedelta(days=30)).isoformat()},
        {"x": "drop", "y": 3.0, "flag": "miss", "time": now.isoformat()},
    ]).lazy()

    filtered, pushed = dashboard._pushdown_dashboard_filters(
        lf,
        {"filter_expr": "flag == 'hit'", "time_col": "time", "days": 2},
        {"x", "y", "flag", "time"},
    )
    out = filtered.collect().to_dicts()

    assert pushed == {"filter_expr": True, "time_window": True}
    assert out == [{"x": "keep", "y": 1.0, "flag": "hit", "time": now.isoformat()}]


def test_dashboard_compute_chart_uses_lazy_filter_and_time_pushdown(monkeypatch):
    now = datetime.datetime.now()
    source = pl.DataFrame([
        {"x": "keep", "y": 1.0, "flag": "hit", "time": now.isoformat()},
        {"x": "old", "y": 2.0, "flag": "hit", "time": (now - datetime.timedelta(days=30)).isoformat()},
        {"x": "drop", "y": 3.0, "flag": "miss", "time": now.isoformat()},
    ])
    monkeypatch.setattr(dashboard, "lazy_read_source", lambda *_args, **_kwargs: source.lazy())

    def fail_read_source(*_args, **_kwargs):
        raise AssertionError("read_source fallback should not be used")

    monkeypatch.setattr(dashboard, "read_source", fail_read_source)

    out = dashboard._compute_chart({
        "id": "lazy-filter",
        "chart_type": "scatter",
        "x_col": "x",
        "y_expr": "y",
        "filter_expr": "flag == 'hit'",
        "time_col": "time",
        "days": 2,
    })

    assert out["error"] is None
    assert out["total"] == 1
    assert out["points"] == [{"x": "keep", "y": 1.0, "color": None}]


def test_dashboard_join_right_source_uses_lazy_projection(monkeypatch):
    right = pl.DataFrame({
        "join_id": ["L1", "L2"],
        "joined_y": [10.0, 20.0],
        "unused_wide_col": ["x", "y"],
    })
    monkeypatch.setattr(dashboard, "lazy_read_source", lambda *_args, **_kwargs: right.lazy())

    def fail_read_source(*_args, **_kwargs):
        raise AssertionError("read_source fallback should not be used for join projection")

    monkeypatch.setattr(dashboard, "read_source", fail_read_source)

    out, right_on = dashboard._load_dashboard_join_right_source(
        "DB", "RIGHT", "PRODX", "",
        ["join_id"],
        {"chart_type": "scatter", "x_col": "x", "y_expr": "joined_y"},
        "_j1",
    )

    assert right_on == ["join_id"]
    assert out.columns == ["join_id", "joined_y"]
    assert "unused_wide_col" not in out.columns


def test_dashboard_compute_chart_joins_lazy_right_source(monkeypatch):
    main = pl.DataFrame({
        "lot_id": ["L1", "L2"],
        "x": ["A", "B"],
    })
    right = pl.DataFrame({
        "join_id": ["L1", "L2"],
        "joined_y": [10.0, 20.0],
        "unused_wide_col": ["x", "y"],
    })

    def fake_lazy_read_source(_source_type="", root="", *_args, **_kwargs):
        return right.lazy() if root == "RIGHT" else main.lazy()

    monkeypatch.setattr(dashboard, "lazy_read_source", fake_lazy_read_source)
    monkeypatch.setattr(dashboard, "read_source", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("fallback should not be used")))

    out = dashboard._compute_chart({
        "id": "join-lazy",
        "chart_type": "scatter",
        "x_col": "x",
        "y_expr": "joined_y",
        "joins": [{
            "source_type": "DB",
            "root": "RIGHT",
            "product": "PRODX",
            "left_on": "lot_id",
            "right_on": "join_id",
            "suffix": "_j1",
        }],
    })

    assert out["error"] is None
    assert out["total"] == 2
    assert [p["y"] for p in out["points"]] == [10.0, 20.0]
