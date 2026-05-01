from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app_v2.modules.informs.splittable_embed import build_splittable_embed  # noqa: E402
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
            "rows": [
                {
                    "_param": "KNOB_GATE",
                    "_cells": {
                        "0": {"actual": "R1", "plan": "R2"},
                        "1": {"actual": "R1", "plan": ""},
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
    assert embed["rows"][0] == ["KNOB_GATE", "R1 → R2", "R1"]
    assert embed["st_view"]["root_lot_id"] == "A1000"
    assert embed["st_view"]["header_groups"] == [{"label": "A1000A.1", "span": 2}]
    assert embed["st_scope"]["inline_cols"] == ["KNOB_GATE", "MASK_ID"]


def test_create_inform_keeps_service_snapshot_fab_lot_labels(tmp_path, monkeypatch):
    informs_file = tmp_path / "informs.json"
    monkeypatch.setattr(informs, "INFORMS_FILE", informs_file)
    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "admin", "username": "tester"})
    monkeypatch.setattr(informs, "_resolve_fab_lot_snapshot", lambda *_args, **_kwargs: "")
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
