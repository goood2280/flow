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


def test_splittable_embed_normalizes_product_case_for_split_table():
    assert embed_service.ml_product_name("proda") == "ML_TABLE_PRODA"
    assert embed_service.ml_product_name("ml_table_proda") == "ML_TABLE_PRODA"
    assert embed_service.strip_ml_prefix("ml_table_proda") == "proda"


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


def test_splittable_embed_custom_snapshot_keeps_selected_columns_only(monkeypatch):
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

    monkeypatch.setattr(embed_service, "_load_view", fake_view_loader)
    monkeypatch.setattr(embed_service, "_plans_for_root", lambda _product, _root: {
        "A1000|1|KNOB_GATE": "R2",
        "A1000|1|KNOB_PLAN_LATE": "R_PLAN",
    })

    embed = build_splittable_embed(
        "PRODA",
        "A1000",
        custom_cols=["KNOB_GATE"],
    )

    assert [c["custom_cols"] for c in calls] == ["KNOB_GATE"]
    assert [r["_param"] for r in embed["st_view"]["rows"]] == ["KNOB_GATE"]
    assert embed["rows"] == [["KNOB_GATE", "R1 → R2"]]
    assert embed["st_scope"]["inline_cols"] == ["KNOB_GATE"]


def test_splittable_embed_fab_lot_knob_snapshot_keeps_selected_fab_scope():
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
    )

    assert [c["fab_lot_id"] for c in calls] == ["A1000A.1"]
    assert [c["root_lot_id"] for c in calls] == [""]
    assert [c["custom_cols"] for c in calls] == ["KNOB_GATE"]
    assert embed["source"] == "SplitTable/PRODA @ A1000A.1 · CUSTOM(1)"
    assert embed["st_view"]["headers"] == ["#8"]
    assert embed["st_view"]["header_groups"] == [{"label": "A1000A.1", "span": 1}]
    assert [r["_param"] for r in embed["st_view"]["rows"]] == ["KNOB_GATE"]
    assert embed["st_view"]["rows"][0]["_cells"]["0"]["actual"] == "R8"
    assert embed["st_view"]["rows"][0]["_cells"]["0"]["plan"] is None
    assert embed["st_scope"]["inline_cols"] == ["KNOB_GATE"]


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


def test_splittable_embed_appends_saved_plan_only_rows(monkeypatch):
    monkeypatch.setattr(embed_service, "_plans_for_root", lambda _product, _root: {
        "A1000|1|KNOB_PLAN_ONLY": "R9",
    })
    view = {
        "headers": ["#1"],
        "root_lot_id": "A1000",
        "rows": [{"_param": "KNOB_GATE", "_cells": {"0": {"actual": "R1"}}}],
    }

    out = embed_service._apply_saved_plans("ML_TABLE_PRODA", "A1000", view)

    assert [row["_param"] for row in out["rows"]] == ["KNOB_GATE", "KNOB_PLAN_ONLY"]
    plan_row = out["rows"][1]
    assert plan_row["_cells"]["0"]["plan"] == "R9"
    assert plan_row["_cells"]["0"]["actual"] is None


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
    assert "font-size:14px" in html
    assert "color:#000000;font-weight:700'>✗ R1" in html
    assert "color:#000000;font-style:italic;font-weight:700'>📌 R3" in html
    assert "→ R2" not in html
    assert "Wafer별 적용 plan 요약" not in html


def test_splittable_snapshot_split_check_mode_builds_value_check_rows():
    embed = informs._build_splittable_snapshot_embed(informs.SplitTableSnapshotReq(
        product="PRODA",
        lot_id="A1000",
        custom_cols=["KNOB_GATE", "KNOB_TEMP", "KNOB_EMPTY"],
        display_mode="split_check",
        current_view={
            "headers": ["#1", "#2", "#3"],
            "root_lot_id": "A1000",
            "header_groups": [{"label": "A1000A.1", "span": 3}],
            "rows": [
                {
                    "_param": "KNOB_GATE",
                    "_cells": {
                        "0": {"actual": "A"},
                        "1": {"actual": "B"},
                        "2": {"actual": "A"},
                    },
                },
                {
                    "_param": "KNOB_TEMP",
                    "_cells": {
                        "0": {"actual": "X", "plan": "P"},
                        "1": {"actual": "X", "plan": "Q"},
                        "2": {"actual": "X", "plan": "P"},
                    },
                },
                {
                    "_param": "KNOB_EMPTY",
                    "_cells": {
                        "0": {"actual": ""},
                        "1": {"actual": None},
                        "2": {"actual": "null"},
                    },
                },
            ],
        },
    ))

    assert embed["display_mode"] == "split_check"
    assert embed["columns"] == ["항목", "값", "Split", "#1", "#2", "#3"]
    assert embed["rows"] == [
        ["KNOB_GATE", "A", "S0", "✓", "", "✓"],
        ["KNOB_GATE", "B", "S1", "", "✓", ""],
        ["KNOB_TEMP", "P", "S0", "✓", "", "✓"],
        ["KNOB_TEMP", "Q", "S1", "", "✓", ""],
    ]
    assert embed["st_view"]["prefix_columns"] == ["항목", "값", "Split"]
    assert [r["_split_label"] for r in embed["st_view"]["rows"] if r["_param"] == "KNOB_GATE"] == ["S0", "S1"]
    assert [r["_split_label"] for r in embed["st_view"]["rows"] if r["_param"] == "KNOB_TEMP"] == ["S0", "S1"]
    for row in embed["st_view"]["rows"]:
        assert {c.get("actual", "") for c in row["_cells"].values()} <= {"", "✓"}


def test_inform_mail_splittable_snapshot_html_renders_split_check_prefix_columns(monkeypatch):
    from routers import splittable as splittable_router

    monkeypatch.setattr(splittable_router, "_build_knob_meta", lambda _product: {
        "KNOB_GATE": {
            "groups": [
                {"step_desc": "GATE", "func_step": "GATE", "step_ids": ["STEP_GATE_A"]},
                {"step_desc": "ETCH", "func_step": "ETCH", "step_ids": ["STEP_ETCH_A"]},
            ],
        },
    })
    embed = informs._build_splittable_snapshot_embed(informs.SplitTableSnapshotReq(
        product="PRODA",
        lot_id="A1000",
        custom_cols=["KNOB_GATE"],
        display_mode="split_check",
        current_view={
            "headers": ["#1", "#2", "#3"],
            "root_lot_id": "A1000",
            "header_groups": [{"label": "A1000A.1", "span": 3}],
            "rows": [{
                "_param": "KNOB_GATE",
                "_cells": {
                    "0": {"actual": "A"},
                    "1": {"actual": "B"},
                    "2": {"actual": "A"},
                },
            }],
        },
    ))

    html = informs._render_embed_table_html(embed)

    pos_item = html.index("항목")
    pos_value = html.index("값", pos_item)
    pos_split = html.index("Split", pos_value)
    pos_wafer = html.index("#1", pos_split)
    assert pos_item < pos_value < pos_split < pos_wafer
    assert "KNOB_GATE" in html
    assert "[ STEP_GATE_A (GATE) ]" not in html
    assert "S0" in html
    assert "S1" in html
    assert "✓" in html
    assert "background:#C6EFCE;color:#000000;font-weight:700;'>S0" in html
    assert "background:#FFEB9C;color:#000000;font-weight:700;'>S1" in html
    assert html.count("background:#C6EFCE;color:#000000;font-weight:700;") == 3
    assert html.count("background:#FFEB9C;color:#000000;font-weight:700;") == 2
    assert "Split table" in html
    assert "KNOB별 step_desc → step_id 요약" in html
    assert "STEP_GATE_A, STEP_ETCH_A" in html
    assert "GATE, ETCH" in html
    assert "Parameter별 적용 step 요약" not in html
    assert "item_id" not in html


def test_inform_mail_body_links_go_flow_in_new_tab():
    html = informs._build_html_body({
        "id": "inf_test",
        "product": "PRODA",
        "lot_id": "A1000",
        "author": "tester",
        "created_at": "2026-04-29T10:00:00",
    }, "", "")

    assert "href='http://go/flow_process'" in html
    assert "target='_blank'" in html
    assert "go/flow_process" in html
    assert "<b>go/flow_process</b>" not in html
    assert "인폼 공유" not in html
    assert "Sent by flow" not in html
