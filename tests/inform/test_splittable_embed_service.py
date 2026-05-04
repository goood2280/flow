from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app_v2.modules.informs import splittable_embed as embed_service  # noqa: E402
from app_v2.modules.informs.splittable_embed import (  # noqa: E402
    build_splittable_embed,
    build_splittable_embed_from_view,
)
from backend.routers import informs  # noqa: E402


def test_splittable_embed_service_builds_inform_snapshot_for_fab_lot():
    calls = []

    def fake_view_loader(**kwargs):
        calls.append(kwargs)
        return {
            "headers": ["#1", "#2"],
            "root_lot_id": "A1000",
            "header_groups": [{"label": "A1000A.1", "span": 2}],
            "wafer_fab_list": ["A1000A.1", "A1000A.1"],
            "row_labels": {"root_lot_id": "root_lot_id", "lot_id": "lot_id", "parameter": "항목"},
            "rows": [
                {
                    "_param": "KNOB_GATE",
                    "_cells": {
                        "0": {"actual": "R1", "plan": "R2"},
                        "1": {"actual": "R1", "plan": "R1"},
                    },
                },
                {
                    "_param": "MASK_ID",
                    "_cells": {"0": {"actual": "M1"}, "1": {"actual": "M2"}},
                },
            ],
        }

    embed = build_splittable_embed(
        "PRODA",
        "A1000A.1",
        custom_cols=["KNOB_GATE", "MASK_ID", "KNOB_GATE"],
        is_fab_lot=True,
        view_loader=fake_view_loader,
    )

    assert calls == [{
        "product": "ML_TABLE_PRODA",
        "root_lot_id": "",
        "wafer_ids": "",
        "prefix": "ALL",
        "custom_name": "",
        "view_mode": "all",
        "history_mode": "all",
        "fab_lot_id": "A1000A.1",
        "custom_cols": "KNOB_GATE,MASK_ID",
    }]
    assert embed["source"] == "SplitTable/PRODA @ A1000A.1 · CUSTOM(2)"
    assert embed["columns"] == ["parameter", "#1", "#2"]
    assert embed["rows"][0] == ["KNOB_GATE", "R1 → R2", "✓ R1 (plan 적용)"]
    assert embed["st_view"]["root_lot_id"] == "A1000"
    assert embed["st_view"]["header_groups"] == [{"label": "A1000A.1", "span": 2}]
    assert embed["st_view"]["row_labels"] == {"root_lot_id": "root_lot_id", "lot_id": "lot_id", "parameter": "항목"}
    assert embed["st_scope"]["inline_cols"] == ["KNOB_GATE", "MASK_ID"]


def test_splittable_embed_from_current_view_preserves_plan_cells():
    embed = build_splittable_embed_from_view(
        "PRODA",
        "A1000",
        {
            "headers": ["#1", "#2"],
            "root_lot_id": "A1000",
            "header_groups": [{"label": "A1000A.1", "span": 2}],
            "wafer_fab_list": ["A1000A.1", "A1000A.1"],
            "rows": [
                {
                    "_param": "KNOB_GATE",
                    "_display": "KNOB_GATE",
                    "_cells": {
                        "0": {"actual": "R1", "plan": "R2"},
                        "1": {"actual": None, "plan": "R3"},
                    },
                },
            ],
        },
        custom_cols=["KNOB_GATE"],
    )

    assert embed["source"] == "SplitTable/PRODA @ A1000 · CURRENT"
    assert embed["rows"][0] == ["KNOB_GATE", "R1 → R2", "R3"]
    assert embed["st_view"]["rows"][0]["_cells"]["0"]["plan"] == "R2"
    assert embed["st_view"]["rows"][0]["_cells"]["1"]["plan"] == "R3"
    assert embed["st_scope"]["snapshot_source"] == "current_splittable"
    assert embed["st_scope"]["lot_id"] == "A1000"


def test_splittable_embed_custom_snapshot_appends_saved_plan_columns():
    calls = []

    def fake_view_loader(**kwargs):
        calls.append(kwargs)
        cols = [c for c in str(kwargs.get("custom_cols") or "").split(",") if c]
        rows = []
        if "KNOB_GATE" in cols:
            rows.append({
                "_param": "KNOB_GATE",
                "_cells": {"0": {"actual": "R1", "plan": None}},
            })
        if "KNOB_PLAN_LATE" in cols:
            rows.append({
                "_param": "KNOB_PLAN_LATE",
                "_cells": {"0": {"actual": None, "plan": "R_PLAN"}},
            })
        return {
            "headers": ["#1"],
            "root_lot_id": "A1000",
            "rows": rows,
        }

    embed = build_splittable_embed(
        "PRODA",
        "A1000",
        custom_cols=["KNOB_GATE"],
        view_loader=fake_view_loader,
        plan_column_loader=lambda _product, _root: ["KNOB_PLAN_LATE"],
    )

    assert [c["custom_cols"] for c in calls] == [
        "KNOB_GATE",
        "KNOB_GATE,KNOB_PLAN_LATE",
    ]
    assert [r["_param"] for r in embed["st_view"]["rows"]] == ["KNOB_GATE", "KNOB_PLAN_LATE"]
    assert embed["rows"][-1] == ["KNOB_PLAN_LATE", "R_PLAN"]
    assert embed["st_scope"]["inline_cols"] == ["KNOB_GATE", "KNOB_PLAN_LATE"]


def test_splittable_embed_fab_lot_knob_snapshot_uses_root_plan_scope():
    calls = []

    def fake_view_loader(**kwargs):
        calls.append(kwargs)
        cols = [c for c in str(kwargs.get("custom_cols") or "").split(",") if c]
        is_root_scope = bool(kwargs.get("root_lot_id")) and not kwargs.get("fab_lot_id")
        headers = ["#1", "#8"] if is_root_scope else ["#8"]
        groups = (
            [{"label": "A1000A.2", "span": 1}, {"label": "A1000A.1", "span": 1}]
            if is_root_scope
            else [{"label": "A1000A.1", "span": 1}]
        )
        rows = []
        if "KNOB_GATE" in cols:
            rows.append({
                "_param": "KNOB_GATE",
                "_cells": {
                    "0": {"actual": "R1", "plan": "R_PLAN"} if is_root_scope else {"actual": "R8", "plan": None},
                    **({"1": {"actual": "R8", "plan": None}} if is_root_scope else {}),
                },
            })
        if "KNOB_PLAN_LATE" in cols:
            rows.append({
                "_param": "KNOB_PLAN_LATE",
                "_cells": {
                    "0": {"actual": None, "plan": "LATE_PLAN"} if is_root_scope else {"actual": None, "plan": None},
                    **({"1": {"actual": None, "plan": None}} if is_root_scope else {}),
                },
            })
        return {
            "headers": headers,
            "root_lot_id": "A1000",
            "header_groups": groups,
            "wafer_fab_list": [g["label"] for g in groups for _ in range(g["span"])],
            "rows": rows,
        }

    embed = build_splittable_embed(
        "PRODA",
        "A1000A.1",
        custom_cols=["KNOB_GATE"],
        is_fab_lot=True,
        view_loader=fake_view_loader,
        plan_column_loader=lambda _product, _root: ["KNOB_PLAN_LATE"],
    )

    assert [c["fab_lot_id"] for c in calls] == ["A1000A.1", "A1000A.1", ""]
    assert calls[-1]["root_lot_id"] == "A1000"
    assert embed["source"] == "SplitTable/PRODA @ A1000A.1 · CUSTOM(2)"
    assert embed["st_view"]["headers"] == ["#1", "#8"]
    assert embed["st_view"]["header_groups"] == [
        {"label": "A1000A.2", "span": 1},
        {"label": "A1000A.1", "span": 1},
    ]
    assert [r["_param"] for r in embed["st_view"]["rows"]] == ["KNOB_GATE", "KNOB_PLAN_LATE"]
    assert embed["st_view"]["rows"][0]["_cells"]["0"]["plan"] == "R_PLAN"
    assert embed["st_view"]["rows"][1]["_cells"]["0"]["plan"] == "LATE_PLAN"
    assert embed["st_scope"]["inline_cols"] == ["KNOB_GATE", "KNOB_PLAN_LATE"]


def test_splittable_embed_from_current_view_uses_first_fab_lot_when_lot_blank():
    embed = build_splittable_embed_from_view(
        "PRODA",
        "",
        {
            "headers": ["#1", "#2"],
            "root_lot_id": "A1000",
            "header_groups": [{"label": "A1000A.1", "span": 2}],
            "wafer_fab_list": ["A1000A.1", "A1000A.1"],
            "rows": [{"_param": "KNOB_GATE", "_cells": {"0": {"actual": "R1"}}}],
        },
        custom_cols=["KNOB_GATE"],
        is_fab_lot=None,
    )

    assert embed["source"] == "SplitTable/PRODA @ A1000A.1 · CURRENT"
    assert embed["note"] == "1 params · fab_lot=A1000A.1 · scope=CURRENT"
    assert embed["st_view"]["root_lot_id"] == "A1000"
    assert embed["st_scope"]["lot_id"] == "A1000A.1"


def test_splittable_embed_keeps_plan_rows_after_default_snapshot_limit():
    rows = [
        {"_param": f"KNOB_{idx:03d}", "_cells": {"0": {"actual": f"R{idx}"}}}
        for idx in range(130)
    ]
    rows.append({
        "_param": "KNOB_PLAN_LATE",
        "_cells": {"0": {"actual": None, "plan": "R_PLAN"}},
    })

    embed = build_splittable_embed_from_view(
        "PRODA",
        "A1000",
        {
            "headers": ["#1"],
            "root_lot_id": "A1000",
            "rows": rows,
        },
    )

    assert len(embed["st_view"]["rows"]) == 121
    assert embed["st_view"]["rows"][-1]["_param"] == "KNOB_PLAN_LATE"
    assert embed["rows"][-1] == ["KNOB_PLAN_LATE", "R_PLAN"]


def test_splittable_embed_overlays_saved_plans_when_view_omits_plan(monkeypatch):
    monkeypatch.setattr(embed_service, "_plans_for_root", lambda _product, _root: {
        "A1000|1|KNOB_GATE": "R2",
        "A1000|2|KNOB_GATE": "R3",
    })
    view = {
        "headers": ["#1", "#2"],
        "root_lot_id": "A1000",
        "rows": [{
            "_param": "KNOB_GATE",
            "_cells": {
                "0": {"actual": "R1", "key": "A1000|1|KNOB_GATE"},
                "1": {"actual": None},
            },
        }],
    }

    out = embed_service._apply_saved_plans("ML_TABLE_PRODA", "A1000", view)

    cells = out["rows"][0]["_cells"]
    assert cells["0"]["plan"] == "R2"
    assert cells["0"]["mismatch"] is True
    assert cells["1"]["plan"] == "R3"
    assert cells["1"]["key"] == "A1000|2|KNOB_GATE"


def test_create_inform_keeps_service_snapshot_fab_lot_labels(tmp_path, monkeypatch):
    informs_file = tmp_path / "informs.json"
    monkeypatch.setattr(informs, "INFORMS_FILE", informs_file)
    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "admin", "username": "tester"})
    monkeypatch.setattr(informs, "_resolve_fab_lot_snapshot", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(informs, "_audit_record", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(informs, "_audit", lambda *_args, **_kwargs: None)

    embed = build_splittable_embed(
        "PRODA",
        "A1000A.1",
        is_fab_lot=True,
        view_loader=lambda **_kwargs: {
            "headers": ["#1"],
            "root_lot_id": "A1000",
            "header_groups": [{"label": "A1000A.1", "span": 1}],
            "wafer_fab_list": ["A1000A.1"],
            "rows": [{"_param": "KNOB_GATE", "_cells": {"0": {"actual": "R1"}}}],
        },
    )
    req = informs.InformCreate(**{
        "lot_id": "A1000A.1",
        "product": "PRODA",
        "module": "KNOB",
        "reason": "PEMS",
        "text": "service snapshot",
        "embed_table": embed,
    })

    created = informs.create_inform(req, object())["inform"]

    assert created["root_lot_id"] == "A1000"
    assert created["fab_lot_id_at_save"] == "A1000A.1"
    assert created["embed_table"]["st_view"]["header_groups"][0]["label"] == "A1000A.1"


def test_auto_log_splittable_change_attaches_changed_column_snapshot(tmp_path, monkeypatch):
    informs_file = tmp_path / "informs.json"
    monkeypatch.setattr(informs, "INFORMS_FILE", informs_file)
    monkeypatch.setattr(informs, "_audit_record", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(informs, "build_splittable_embed", lambda **kwargs: {
        "source": "SplitTable/PRODA @ A1000 · CUSTOM(1)",
        "columns": ["parameter", "#1"],
        "rows": [["KNOB_GATE", "R1 -> R2"]],
        "st_view": {
            "root_lot_id": "A1000",
            "headers": ["#1"],
            "rows": [{"_param": "KNOB_GATE", "_cells": {"0": {"actual": "R1", "plan": "R2"}}}],
        },
        "st_scope": {"inline_cols": kwargs["custom_cols"]},
    })

    informs.auto_log_splittable_change(
        author="tester",
        product="PRODA",
        lot_id="A1000",
        cell_key="A1000|1|KNOB_GATE",
        old_value="R1",
        new_value="R2",
        action="set",
        fab_lot_id="A1000A.1",
    )

    saved = informs._load()
    assert len(saved) == 1
    assert saved[0]["auto_generated"] is True
    assert saved[0]["splittable_change"]["column"] == "KNOB_GATE"
    assert saved[0]["embed_table"]["st_scope"]["inline_cols"] == ["KNOB_GATE"]
    assert saved[0]["embed_table"]["st_view"]["rows"][0]["_cells"]["0"]["plan"] == "R2"


def test_inform_mail_splittable_snapshot_html_uses_single_scrollable_header_table():
    headers = [f"#{i}" for i in range(1, 26)]
    embed = {
        "source": "SplitTable/NO_META @ A1000 · ALL",
        "note": "mail fit check",
        "st_view": {
            "root_lot_id": "A1000",
            "headers": headers,
            "header_groups": [
                {"label": "A1000A.1", "span": 12},
                {"label": "A1000A.2", "span": 13},
            ],
            "rows": [{
                "_param": "KNOB_NO_SUCH_TEST_COLUMN",
                "_cells": {str(i): {"actual": f"R{i}", "plan": ""} for i in range(len(headers))},
            }],
        },
    }

    html = informs._render_embed_table_html(embed)

    assert "overflow-x:auto;-webkit-overflow-scrolling:touch;max-width:100%" in html
    assert html.count("<table") == 1
    assert "wafer columns" not in html
    assert "table-layout:fixed" in html
    assert "#25" in html
    assert "word-break:break-word" in html
    assert "Split table" in html
    assert "root_lot_id" in html
    assert "lot_id" in html
    assert "A1000A.1" in html
    assert "A1000A.2" in html
    assert "root_lot_id</span> A1000" not in html
    assert "lot_id</span> A1000A.1" not in html


def test_inform_mail_splittable_snapshot_html_renders_plan_cells_like_split_table():
    embed = {
        "source": "SplitTable/NO_META @ A1000 · CUSTOM(2)",
        "st_view": {
            "root_lot_id": "A1000",
            "headers": ["#1", "#2", "#3"],
            "header_groups": [{"label": "A1000A.1", "span": 3}],
            "row_labels": {"root_lot_id": "root_lot_id", "lot_id": "lot_id", "parameter": "항목"},
            "rows": [{
                "_param": "KNOB_GATE",
                "_cells": {
                    "0": {"actual": "R1", "plan": "R2"},
                    "1": {"actual": None, "plan": "R3"},
                    "2": {"actual": "R4", "plan": "R4"},
                },
            }],
        },
    }

    html = informs._render_embed_table_html(embed)

    assert "✗ R1" in html
    assert "(≠R2)" in html
    assert "📌 R3" in html
    assert "✓ R4" in html
    assert "plan 적용" in html
    assert "→ R2" not in html
    assert "Wafer별 적용 plan 요약" not in html


def test_inform_mail_body_links_go_flow_in_new_tab():
    html = informs._build_html_body({
        "id": "inf_test",
        "product": "PRODA",
        "lot_id": "A1000",
        "author": "tester",
        "created_at": "2026-04-29T10:00:00",
    }, "", "")

    assert "href='http://go/flow'" in html
    assert "target='_blank'" in html
    assert "<b>go/flow</b>" not in html
    assert "인폼 공유" not in html
    assert "Sent by flow" not in html
