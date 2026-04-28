from __future__ import annotations

import json
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


def test_operational_history_matches_saved_full_inform_root(tmp_path, monkeypatch):
    informs_file = tmp_path / "informs.json"
    tracker_file = tmp_path / "issues.json"
    informs_file.write_text(json.dumps([{
        "id": "inf_1",
        "root_lot_id": "LOT029AA",
        "lot_id": "",
        "wafer_id": "7",
        "product": "PRODA",
        "module": "KNOB",
        "reason": "PEMS",
        "text": "plan saved",
        "author": "tester",
        "created_at": "2026-04-28T10:00:00",
        "flow_status": "received",
        "group_ids": [],
    }]), encoding="utf-8")
    tracker_file.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(splittable, "INFORMS_FILE", informs_file)
    monkeypatch.setattr(splittable, "TRACKER_ISSUES_FILE", tracker_file)

    items = splittable._load_operational_history(
        product="ML_TABLE_PRODA",
        root_lot_id="LOT029AA",
        wafer_ids="",
        username="tester",
        role="admin",
    )

    assert len(items) == 1
    assert items[0]["source"] == "inform"
    assert items[0]["detail"] == "plan saved"


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


def test_root_lot_candidate_search_reaches_beyond_empty_preview(tmp_path, monkeypatch):
    fp = tmp_path / "ML_TABLE_BIG.parquet"
    pl.DataFrame({
        "root_lot_id": [f"R{i:04d}" for i in range(1200)],
        "wafer_id": [1 for _ in range(1200)],
        "KNOB_ALPHA": ["ON" for _ in range(1200)],
    }).write_parquet(fp)
    monkeypatch.setattr(splittable, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_db_base", lambda: tmp_path)

    preview = splittable.get_lot_candidates(
        product="ML_TABLE_BIG",
        col="root_lot_id",
        prefix="",
        limit=20,
        source="auto",
        root_lot_id="",
    )
    searched = splittable.get_lot_candidates(
        product="ML_TABLE_BIG",
        col="root_lot_id",
        prefix="R1199",
        limit=20,
        source="auto",
        root_lot_id="",
    )

    assert len(preview["candidates"]) <= 20
    assert "R1199" not in preview["candidates"]
    assert searched["candidates"] == ["R1199"]


def test_root_lot_view_handles_categorical_fab_partitions(tmp_path, monkeypatch):
    pl.DataFrame({
        "root_lot_id": ["R9000", "R9000"],
        "wafer_id": [1, 2],
        "KNOB_ALPHA": ["ON", "OFF"],
    }).write_parquet(tmp_path / "ML_TABLE_MIXCAT.parquet")

    fab_root = tmp_path / "1.RAWDATA_DB_FAB" / "MIXCAT"
    part_a = fab_root / "date=20240418"
    part_b = fab_root / "date=20240419"
    part_a.mkdir(parents=True)
    part_b.mkdir(parents=True)
    pl.DataFrame({
        "root_lot_id": ["R9000"],
        "fab_lot_id": ["F9000A.1"],
        "wafer_id": [1],
        "tkout_time": ["2024-04-18T10:00:00"],
    }).write_parquet(part_a / "part_0.parquet")
    pl.DataFrame({
        "root_lot_id": ["R9000"],
        "fab_lot_id": ["F9000A.2"],
        "wafer_id": [2],
        "tkout_time": ["2024-04-19T10:00:00"],
    }).with_columns(
        pl.col("root_lot_id").cast(pl.Categorical)
    ).write_parquet(part_b / "part_0.parquet")

    monkeypatch.setattr(splittable, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_db_base", lambda: tmp_path)

    searched = splittable.get_lot_candidates(
        product="ML_TABLE_MIXCAT",
        col="root_lot_id",
        prefix="R9000",
        limit=20,
        source="auto",
        root_lot_id="",
    )
    result = splittable.view_split(
        product="ML_TABLE_MIXCAT",
        root_lot_id="R9000",
        wafer_ids="",
        prefix="KNOB",
        custom_name="",
        view_mode="all",
        history_mode="all",
        fab_lot_id="",
        custom_cols="",
    )

    assert searched["candidates"] == ["R9000"]
    assert result["headers"] == ["#1", "#2"]
    assert [g["label"] for g in result["header_groups"]] == ["F9000A.1", "F9000A.2"]
    assert result["available_fab_lots"] == ["F9000A.1", "F9000A.2"]


def test_fab_lot_id_is_exposed_when_fab_source_uses_lot_id(tmp_path, monkeypatch):
    pl.DataFrame({
        "root_lot_id": ["R9100", "R9100"],
        "wafer_id": [1, 2],
        "KNOB_ALPHA": ["ON", "OFF"],
    }).write_parquet(tmp_path / "ML_TABLE_STD.parquet")

    fab_root = tmp_path / "1.RAWDATA_DB_FAB" / "STD" / "date=20240420"
    fab_root.mkdir(parents=True)
    pl.DataFrame({
        "root_lot_id": ["R9100", "R9100"],
        "lot_id": ["F9100A.1", "F9100A.1"],
        "wafer_id": [1, 2],
        "tkout_time": ["2024-04-20T10:00:00", "2024-04-20T10:01:00"],
    }).write_parquet(fab_root / "part_0.parquet")

    monkeypatch.setattr(splittable, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_db_base", lambda: tmp_path)

    schema_names = splittable._scan_product(
        "ML_TABLE_STD",
        root_lot_id="R9100",
    ).collect_schema().names()
    result = splittable.view_split(
        product="ML_TABLE_STD",
        root_lot_id="R9100",
        wafer_ids="",
        prefix="KNOB",
        custom_name="",
        view_mode="all",
        history_mode="all",
        fab_lot_id="",
        custom_cols="",
    )

    assert "fab_lot_id" in schema_names
    assert result["header_groups"] == [{"label": "F9100A.1", "span": 2}]
    assert result["available_fab_lots"] == ["F9100A.1"]


def test_match_cache_supplies_fab_lot_without_rescanning_source(tmp_path, monkeypatch):
    pl.DataFrame({
        "root_lot_id": ["R9200", "R9200"],
        "wafer_id": [1, 2],
        "KNOB_ALPHA": ["ON", "OFF"],
    }).write_parquet(tmp_path / "ML_TABLE_CACHE.parquet")

    fab_root = tmp_path / "1.RAWDATA_DB_FAB" / "CACHE" / "date=20240420"
    fab_root.mkdir(parents=True)
    pl.DataFrame({
        "root_lot_id": ["R9200", "R9200"],
        "lot_id": ["F9200A.1", "F9200A.1"],
        "wafer_id": [1, 2],
        "tkout_time": ["2024-04-20T10:00:00", "2024-04-20T10:01:00"],
    }).write_parquet(fab_root / "part_0.parquet")

    cache_dir = tmp_path / "flow-data" / "splittable" / "match_cache"
    source_cfg = tmp_path / "flow-data" / "splittable" / "source_config.json"
    monkeypatch.setattr(splittable, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_db_base", lambda: tmp_path)
    monkeypatch.setattr(splittable, "MATCH_CACHE_DIR", cache_dir)
    monkeypatch.setattr(splittable, "SOURCE_CFG", source_cfg)
    splittable._LOT_LOOKUP_CACHE.clear()
    splittable._RGLOB_CACHE.clear()
    splittable._DB_ROOTS_CACHE.clear()

    built = splittable.refresh_match_cache(product="ML_TABLE_CACHE", force=True)
    assert built["products"][0]["ok"] is True
    assert built["products"][0]["row_count"] == 2

    def fail_scan(_source):
        raise AssertionError("raw FAB source should not be scanned when cache exists")

    monkeypatch.setattr(splittable, "_scan_fab_source", fail_scan)

    result = splittable.view_split(
        product="ML_TABLE_CACHE",
        root_lot_id="R9200",
        wafer_ids="",
        prefix="KNOB",
        custom_name="",
        view_mode="all",
        history_mode="all",
        fab_lot_id="",
        custom_cols="",
    )
    fab_candidates = splittable.get_lot_candidates(
        product="ML_TABLE_CACHE",
        col="fab_lot_id",
        prefix="F9200",
        limit=20,
        source="auto",
        root_lot_id="R9200",
    )

    assert result["header_groups"] == [{"label": "F9200A.1", "span": 2}]
    assert result["available_fab_lots"] == ["F9200A.1"]
    assert fab_candidates["candidates"] == ["F9200A.1"]
    assert fab_candidates["fab_source"] == "1.RAWDATA_DB_FAB/CACHE"


def test_match_cache_searches_entire_fab_db_when_product_folder_is_missing(tmp_path, monkeypatch):
    pl.DataFrame({
        "root_lot_id": ["R9300", "R9300"],
        "wafer_id": [1, 2],
        "KNOB_ALPHA": ["ON", "OFF"],
    }).write_parquet(tmp_path / "ML_TABLE_NOMATCH.parquet")

    other_fab = tmp_path / "1.RAWDATA_DB_FAB" / "OTHER" / "date=20240421"
    other_fab.mkdir(parents=True)
    pl.DataFrame({
        "root_lot_id": ["R9300", "R9300"],
        "fab_lot_id": ["F9300A.1", "F9300A.1"],
        "wafer_id": [1, 2],
        "tkout_time": ["2024-04-21T10:00:00", "2024-04-21T10:01:00"],
    }).write_parquet(other_fab / "part_0.parquet")

    cache_dir = tmp_path / "flow-data" / "splittable" / "match_cache"
    source_cfg = tmp_path / "flow-data" / "splittable" / "source_config.json"
    monkeypatch.setattr(splittable, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_db_base", lambda: tmp_path)
    monkeypatch.setattr(splittable, "MATCH_CACHE_DIR", cache_dir)
    monkeypatch.setattr(splittable, "SOURCE_CFG", source_cfg)
    splittable._LOT_LOOKUP_CACHE.clear()
    splittable._RGLOB_CACHE.clear()
    splittable._DB_ROOTS_CACHE.clear()

    built = splittable.refresh_match_cache(product="ML_TABLE_NOMATCH", force=True)
    result = splittable.view_split(
        product="ML_TABLE_NOMATCH",
        root_lot_id="R9300",
        wafer_ids="",
        prefix="KNOB",
        custom_name="",
        view_mode="all",
        history_mode="all",
        fab_lot_id="",
        custom_cols="",
    )

    assert built["products"][0]["ok"] is True
    assert built["products"][0]["fab_source"] == ""
    assert built["products"][0]["fab_sources"] == ["1.RAWDATA_DB_FAB/OTHER"]
    assert result["header_groups"] == [{"label": "F9300A.1", "span": 2}]
    assert result["available_fab_lots"] == ["F9300A.1"]
