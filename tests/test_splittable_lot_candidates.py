from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from routers import splittable  # noqa: E402


def test_root_lot_candidates_prefer_renderable_mltable_roots():
    result = splittable.get_lot_candidates(
        product="ML_TABLE_PRODA",
        col="root_lot_id",
        prefix="A10",
        limit=20,
        source="auto",
        root_lot_id="",
    )

    assert result["source"] == "mltable"
    assert result["fab_source"] == "1.RAWDATA_DB_FAB/PRODA"
    assert "A1000" in result["candidates"]
    assert "A0001" not in result["candidates"]


def test_lot_ids_do_not_suggest_fab_roots_that_cannot_render():
    result = splittable.get_lot_ids(product="ML_TABLE_PRODA", limit=20)

    assert result["fallback"] == ""
    assert result["fab_source"] == "1.RAWDATA_DB_FAB/PRODA"
    assert "A1000" in result["lot_ids"]
    assert "A0001" not in result["lot_ids"]


def test_view_accepts_fab_lot_pasted_into_root_field():
    result = splittable.view_split(
        product="ML_TABLE_PRODA",
        root_lot_id="A1000A.1",
        wafer_ids="",
        prefix="KNOB",
        custom_name="",
        view_mode="all",
        history_mode="all",
        fab_lot_id="",
        custom_cols="",
    )

    assert result["root_lot_id"] == "A1000"
    assert result["headers"]
    assert result["rows"]
    assert "fab_lot_id" in result["lot_warn"]


def test_view_validates_root_and_fab_scope_together():
    result = splittable.view_split(
        product="ML_TABLE_PRODA",
        root_lot_id="A1000",
        wafer_ids="",
        prefix="KNOB",
        custom_name="",
        view_mode="all",
        history_mode="all",
        fab_lot_id="A1001A.1",
        custom_cols="",
    )

    assert result["root_lot_id"] == "A1000"
    assert result["headers"]
    assert result["rows"]
    assert "Root Lot ID 기준" in result["lot_warn"]
    assert all(
        group["label"] == "—" or str(group["label"]).startswith("A1000")
        for group in result["header_groups"]
    )


def test_view_keeps_matching_root_and_fab_scope_narrow():
    result = splittable.view_split(
        product="ML_TABLE_PRODA",
        root_lot_id="A1000",
        wafer_ids="",
        prefix="KNOB",
        custom_name="",
        view_mode="all",
        history_mode="all",
        fab_lot_id="A1000A.1",
        custom_cols="",
    )

    assert result["root_lot_id"] == "A1000"
    assert result["headers"]
    assert result["header_groups"] == [{"label": "A1000A.1", "span": len(result["headers"])}]
    assert result["lot_warn"] == ""


def test_mltable_product_files_are_discovered_case_insensitively(tmp_path, monkeypatch):
    fp = tmp_path / "ml_table_mixed.PARQUET"
    pl.DataFrame({
        "root_lot_id": ["R1000"],
        "wafer_id": [1],
        "KNOB_ALPHA": ["ON"],
    }).write_parquet(fp)
    monkeypatch.setattr(splittable, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_db_base", lambda: tmp_path)

    products = splittable.list_products()["products"]
    assert products == [{
        "name": "ML_TABLE_MIXED",
        "file": "ml_table_mixed.PARQUET",
        "size": fp.stat().st_size,
        "root": "Base",
        "type": "parquet",
        "source_type": "base_file",
    }]
    assert splittable._product_path("ML_TABLE_MIXED") == fp

    result = splittable.view_split(
        product="ML_TABLE_MIXED",
        root_lot_id="R1000",
        wafer_ids="",
        prefix="KNOB",
        custom_name="",
        view_mode="all",
        history_mode="all",
        fab_lot_id="",
        custom_cols="",
    )
    assert result["headers"] == ["#1"]
    assert result["rows"][0]["_param"] == "KNOB_ALPHA"


def test_root_lot_candidates_fall_back_to_detected_uppercase_column(tmp_path, monkeypatch):
    fp = tmp_path / "ML_TABLE_REAL.parquet"
    pl.DataFrame({
        "ROOT_LOT_ID": ["R2000", "R2001"],
        "WAFER_ID": [1, 2],
        "KNOB_ALPHA": ["ON", "OFF"],
    }).write_parquet(fp)
    monkeypatch.setattr(splittable, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_db_base", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_main_table_candidates", lambda *args, **kwargs: {"candidates": []})
    monkeypatch.setattr(splittable, "_fab_history_root_candidates", lambda *args, **kwargs: {"candidates": [], "source": ""})

    result = splittable.get_lot_candidates(
        product="ML_TABLE_REAL",
        col="root_lot_id",
        prefix="R20",
        limit=20,
        source="auto",
        root_lot_id="",
    )

    assert result["match_mode"] == "detected_lot_col_fallback"
    assert result["source_col"] == "ROOT_LOT_ID"
    assert result["candidates"] == ["R2000", "R2001"]
