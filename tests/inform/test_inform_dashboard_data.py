from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from backend.routers import informs  # noqa: E402


METRICS = ["count", "resolution_rate", "first_reply_h", "mail_rate", "attach_rate", "pending_age"]
GROUPBYS = [
    "module",
    "product",
    "root_lot",
    "fab_lot",
    "author",
    "status",
    "date_day",
    "date_week",
    "date_month",
    "hour_of_day",
    "day_of_week",
]


def _items():
    return [
        {
            "id": "r1",
            "product": "PRODA",
            "root_lot_id": "R1000",
            "lot_id": "R1000.01",
            "fab_lot_id_at_save": "FAB1000A",
            "module": "GATE",
            "reason": "PEMS",
            "author": "alice",
            "flow_status": "completed",
            "created_at": "2026-04-01T08:00:00",
            "mail_history": [{"at": "2026-04-01T09:00:00"}],
            "images": [{"uid": "img1", "name": "a.png"}],
        },
        {
            "id": "c1",
            "parent_id": "r1",
            "product": "PRODA",
            "root_lot_id": "R1000",
            "lot_id": "R1000.01",
            "module": "GATE",
            "author": "bob",
            "created_at": "2026-04-01T10:00:00",
        },
        {
            "id": "r2",
            "product": "PRODA",
            "root_lot_id": "R2000",
            "lot_id": "R2000.01",
            "module": "STI",
            "reason": "PEMS",
            "author": "bob",
            "flow_status": "received",
            "created_at": "2026-04-02T09:00:00",
            "embed_table": {
                "st_view": {
                    "root_lot_id": "R2000",
                    "header_groups": [{"label": "FAB2000A", "span": 1}],
                }
            },
        },
        {
            "id": "c2",
            "parent_id": "r2",
            "product": "PRODA",
            "root_lot_id": "R2000",
            "lot_id": "R2000.01",
            "module": "STI",
            "author": "alice",
            "created_at": "2026-04-03T12:00:00",
        },
        {
            "id": "r3",
            "product": "PRODB",
            "root_lot_id": "R3000",
            "lot_id": "R3000",
            "module": "GATE",
            "reason": "PEMS",
            "author": "alice",
            "flow_status": "received",
            "created_at": "2026-04-10T15:30:00",
        },
    ]


def _by_x(points):
    return {row["x"]: row for row in points}


def test_dashboard_data_metric_groupby_matrix_returns_contract():
    items = _items()
    for metric in METRICS:
        for groupby in GROUPBYS:
            out = informs.build_inform_dashboard_data(metric=metric, groupby=groupby, period="all", items=items)
            assert set(out) == {"points", "series_order", "meta"}
            assert isinstance(out["points"], list)
            assert isinstance(out["series_order"], list)
            assert out["meta"]["metric"] == metric
            assert out["meta"]["groupby"] == groupby
            assert out["meta"]["period"] == "all"


def test_dashboard_data_core_aggregates_are_correct():
    items = _items()

    module_count = _by_x(informs.build_inform_dashboard_data("count", "module", items=items)["points"])
    assert module_count["GATE"]["y"] == 2
    assert module_count["STI"]["y"] == 1

    product_resolution = _by_x(informs.build_inform_dashboard_data("resolution_rate", "product", items=items)["points"])
    assert product_resolution["PRODA"]["y"] == 50.0
    assert product_resolution["PRODB"]["y"] == 0.0

    module_first_reply = _by_x(informs.build_inform_dashboard_data("first_reply_h", "module", items=items)["points"])
    assert module_first_reply["GATE"]["y"] == 2.0
    assert module_first_reply["GATE"]["median"] == 2.0
    assert module_first_reply["STI"]["y"] == 27.0

    module_mail = _by_x(informs.build_inform_dashboard_data("mail_rate", "module", items=items)["points"])
    assert module_mail["GATE"]["y"] == 50.0
    assert module_mail["STI"]["y"] == 0.0

    product_attach = _by_x(informs.build_inform_dashboard_data("attach_rate", "product", items=items)["points"])
    assert product_attach["PRODA"]["y"] == 100.0
    assert product_attach["PRODB"]["y"] == 0.0


def test_dashboard_data_period_and_filters():
    items = _items()
    out = informs.build_inform_dashboard_data(
        "count",
        "module",
        period="all",
        product="PRODA",
        module="GATE",
        items=items,
    )
    rows = _by_x(out["points"])
    assert rows["GATE"]["y"] == 1
    assert "STI" not in rows
    assert out["meta"]["total_roots"] == 1


def test_dashboard_data_preset_shapes():
    items = _items()

    buckets = informs.build_inform_dashboard_data("first_reply_h", "first_reply_bucket", items=items)
    assert sum(row["y"] for row in buckets["points"]) == 3

    heatmap = informs.build_inform_dashboard_data(
        "count",
        "root_lot",
        items=items,
        x_groupby="module",
        y_groupby="root_lot",
        top_n=20,
    )
    assert heatmap["meta"]["heatmap_meta"]["kind"] == "categorical"
    assert {"x", "y", "cnt"}.issubset(heatmap["points"][0])

    rate = informs.build_inform_dashboard_data("attach_mail_rate", "rate_kind", items=items)
    assert [row["x"] for row in rate["points"]] == ["첨부 있음", "메일 발송"]

    pending = informs.build_inform_dashboard_data("pending_age", "pending_table", items=items, top_n=20)
    assert pending["meta"]["table_columns"][:5] == ["root_lot", "fab_lot", "모듈", "작성자", "경과시간(h)"]
    assert [row["root_lot"] for row in pending["points"]] == ["R2000", "R3000"] or [row["root_lot"] for row in pending["points"]] == ["R3000", "R2000"]


def test_dashboard_data_uses_ttl_cache(monkeypatch):
    informs._INFORM_DASHBOARD_CACHE.clear()
    monkeypatch.setattr(informs, "_load_upgraded", lambda: [_items()[0]])

    first = informs.build_inform_dashboard_data("count", "module")
    monkeypatch.setattr(informs, "_load_upgraded", lambda: [_items()[0], _items()[2]])
    second = informs.build_inform_dashboard_data("count", "module")

    assert first["points"] == second["points"]
    informs._INFORM_DASHBOARD_CACHE.clear()
