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
