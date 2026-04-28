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
