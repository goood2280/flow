from __future__ import annotations

import json
import sys
from pathlib import Path

import polars as pl
import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core import utils  # noqa: E402
from core import duckdb_engine  # noqa: E402
from routers import filebrowser  # noqa: E402


def test_sql_view_scans_all_partitions_and_filters_before_projection(monkeypatch):
    calls = []

    def fake_lazy_read_source(**kwargs):
        calls.append(kwargs)
        return pl.DataFrame({
            "value": ["hit", "miss"],
            "shown": [1, 2],
        }).lazy()

    monkeypatch.setattr(utils, "lazy_read_source", fake_lazy_read_source)

    result = filebrowser.view_product(
        root="ROOT",
        product="PROD",
        sql="value == 'hit'",
        rows=20,
        select_cols="shown",
        meta_only=False,
        all_partitions=False,
        page=0,
        page_size=20,
    )

    assert calls[-1]["recent_days"] is None
    assert calls[-1]["max_files"] is None
    assert result["columns"] == ["shown"]
    assert result["data"] == [{"shown": 1}]


def test_lazy_view_limits_default_wide_preview_but_filters_full_schema():
    data = {f"c{i:02d}": [i, i + 100] for i in range(30)}
    data["hidden_filter"] = ["hit", "miss"]
    lf = pl.DataFrame(data).lazy()

    result = filebrowser._run_view_lazy(
        lf,
        sql="hidden_filter == 'hit'",
        select_cols="",
        rows=20,
        page=0,
        page_size=20,
        preview_cols=5,
    )

    assert result["total_cols"] == 31
    assert result["preview_cols"] == 5
    assert result["truncated_cols"] is True
    assert result["columns"] == [f"c{i:02d}" for i in range(5)]
    assert result["data"] == [{"c00": 0, "c01": 1, "c02": 2, "c03": 3, "c04": 4}]
    assert result["total_rows_exact"] is False


def test_lazy_view_supports_polars_column_method_expr():
    lf = pl.DataFrame({
        "lot_id": ["A", "B", "C"],
        "value": [10, 20, 30],
    }).lazy()

    result = filebrowser._run_view_lazy(
        lf,
        sql="lot_id.is_in(['B', 'C'])",
        select_cols="value",
        rows=20,
        page=0,
        page_size=20,
        preview_cols=5,
    )

    assert result["data"] == [{"value": 20}, {"value": 30}]
    assert result["total_rows_exact"] is False


def test_filebrowser_dataframe_view_normalizes_wafer_ids():
    df = pl.DataFrame({
        "root_lot_id": ["LOT"] * 4,
        "wafer_id": [1, 25, 1000, 0],
        "value": [10, 20, 999, 888],
    })

    result = filebrowser._run_view(df, sql="", select_cols="wafer_id,value", rows=20)

    assert result["wafer_filter"] == {"max": 25}
    assert result["data"] == [
        {"wafer_id": "1", "value": 10},
        {"wafer_id": "25", "value": 20},
        {"wafer_id": "25", "value": 999},
    ]


def test_filebrowser_lazy_view_normalizes_wafer_ids_before_sql():
    lf = pl.DataFrame({
        "root_lot_id": ["LOT"] * 4,
        "wafer_id": ["1", "25", "1000", "1.5"],
        "value": [10, 20, 999, 777],
    }).lazy()

    result = filebrowser._run_view_lazy(
        lf,
        sql="value >= 10",
        select_cols="wafer_id,value",
        rows=20,
        page=0,
        page_size=20,
        preview_cols=5,
        cached_meta={"row_count": 4},
    )

    assert result["wafer_filter"] == {"max": 25}
    assert result["total_rows_exact"] is False
    assert result["data"] == [
        {"wafer_id": "1", "value": 10},
        {"wafer_id": "25", "value": 20},
        {"wafer_id": "25", "value": 999},
    ]


def test_download_lazy_csv_normalizes_wafer_ids():
    lf = pl.DataFrame({
        "wafer_id": [1, 25, 1000],
        "value": [10, 20, 999],
    }).lazy()

    df, csv_bytes = filebrowser._download_lazy_csv(lf, "", "wafer_id,value", 10)

    assert df.to_dicts() == [
        {"wafer_id": "1", "value": 10},
        {"wafer_id": "25", "value": 20},
        {"wafer_id": "25", "value": 999},
    ]
    assert b"1000" not in csv_bytes


def test_lazy_view_default_preview_orders_latest_rows_first():
    lf = pl.DataFrame({
        "lot_id": ["old", "new", "mid"],
        "tkout_time": [
            "2024-04-20T12:00:00",
            "2024-04-23T12:00:00",
            "2024-04-21T12:00:00",
        ],
    }).lazy()

    result = filebrowser._run_view_lazy(
        lf,
        sql="",
        select_cols="",
        rows=2,
        page=0,
        page_size=2,
        preview_cols=5,
        latest_first=True,
        latest_preview=True,
    )

    assert result["latest_preview"] is True
    assert result["latest_order_col"] == "tkout_time"
    assert result["data"] == [
        {"lot_id": "new", "tkout_time": "2024-04-23T12:00:00"},
        {"lot_id": "mid", "tkout_time": "2024-04-21T12:00:00"},
    ]


def test_product_click_preview_uses_limited_recent_scan(monkeypatch):
    calls = []

    def fake_lazy_read_source(**kwargs):
        calls.append(kwargs)
        return pl.DataFrame({
            "lot_id": ["old", "new"],
            "time": ["2024-04-20T12:00:00", "2024-04-23T12:00:00"],
        }).lazy()

    monkeypatch.setattr(utils, "lazy_read_source", fake_lazy_read_source)

    result = filebrowser.view_product(
        root="ROOT",
        product="PROD",
        sql="",
        rows=200,
        select_cols="",
        meta_only=False,
        all_partitions=False,
        page=0,
        page_size=200,
    )

    assert calls[-1]["recent_days"] == 30
    assert calls[-1]["max_files"] == filebrowser.LATEST_PREVIEW_MAX_FILES
    assert calls[-1]["latest_only"] is True
    assert result["latest_preview"] is True
    assert result["latest_order_col"] == "time"
    assert result["data"][0]["lot_id"] == "new"


def test_column_select_runs_full_scan_without_recent_preview(monkeypatch):
    calls = []

    def fake_lazy_read_source(**kwargs):
        calls.append(kwargs)
        return pl.DataFrame({
            "lot_id": ["old", "new"],
            "time": ["2024-04-20T12:00:00", "2024-04-23T12:00:00"],
        }).lazy()

    monkeypatch.setattr(utils, "lazy_read_source", fake_lazy_read_source)

    result = filebrowser.view_product(
        root="ROOT",
        product="PROD",
        sql="",
        rows=200,
        select_cols="lot_id",
        meta_only=False,
        all_partitions=False,
        page=0,
        page_size=200,
    )

    assert calls[-1]["recent_days"] is None
    assert calls[-1]["max_files"] is None
    assert calls[-1]["latest_only"] is False
    assert result["latest_preview"] is False
    assert result["columns"] == ["lot_id"]


def test_base_file_meta_only_uses_cached_parquet_metadata(monkeypatch, tmp_path):
    fp = tmp_path / "ML_TABLE_BIG.parquet"
    fp.write_bytes(b"placeholder")
    meta = fp.with_suffix(fp.suffix + ".meta.json")
    meta.write_text(
        json.dumps({"row_count": 123, "schema": {"lot": "String", "value": "Float64"}}),
        encoding="utf-8",
    )

    class DummyPaths:
        pass

    dummy_paths = DummyPaths()
    dummy_paths.base_root = tmp_path
    dummy_paths.db_root = tmp_path
    dummy_paths.data_root = tmp_path
    monkeypatch.setattr(filebrowser, "PATHS", dummy_paths)

    def fail_scan(_fp):
        raise AssertionError("meta_only should not scan parquet when sidecar metadata exists")

    monkeypatch.setattr(filebrowser, "scan_one_file", fail_scan)

    result = filebrowser.base_file_view(
        file=fp.name,
        rows=200,
        cols=1,
        meta_only=True,
        page=0,
        page_size=200,
    )

    assert result["meta_only"] is True
    assert result["meta_cached"] is True
    assert result["total_rows"] == 123
    assert result["columns"] == ["lot"]
    assert result["all_columns"] == ["lot", "value"]
    assert result["data"] == []


def test_base_file_view_reads_entire_non_ml_single_file(monkeypatch, tmp_path):
    fp = tmp_path / "matching_step.parquet"
    pl.DataFrame({f"c{i:02d}": [i, i + 10, i + 20] for i in range(12)}).write_parquet(fp)

    class DummyPaths:
        pass

    dummy_paths = DummyPaths()
    dummy_paths.base_root = tmp_path
    dummy_paths.db_root = tmp_path
    dummy_paths.data_root = tmp_path
    monkeypatch.setattr(filebrowser, "PATHS", dummy_paths)

    result = filebrowser.base_file_view(
        file=fp.name,
        sql="",
        rows=200,
        cols=10,
        select_cols="",
        engine="auto",
        meta_only=False,
        page=0,
        page_size=200,
    )

    assert result["single_file_full_read"] is True
    assert result["showing"] == 3
    assert result["total_rows"] == 3
    assert result["has_more"] is False
    assert len(result["showing_cols"]) == 12
    assert result["truncated_cols"] is False


def test_base_file_view_ml_table_defaults_to_200_then_full_on_column_filter(monkeypatch, tmp_path):
    fp = tmp_path / "ML_TABLE_PRODA.parquet"
    pl.DataFrame({
        "lot_id": [f"L{i:03d}" for i in range(250)],
        "value": list(range(250)),
    }).write_parquet(fp)

    class DummyPaths:
        pass

    dummy_paths = DummyPaths()
    dummy_paths.base_root = tmp_path
    dummy_paths.db_root = tmp_path
    dummy_paths.data_root = tmp_path
    monkeypatch.setattr(filebrowser, "PATHS", dummy_paths)

    preview = filebrowser.base_file_view(
        file=fp.name,
        sql="",
        rows=200,
        cols=10,
        select_cols="",
        engine="auto",
        meta_only=False,
        page=0,
        page_size=200,
    )
    assert preview["showing"] == 200
    assert preview["has_more"] is True
    assert preview.get("single_file_full_read") is not True

    selected = filebrowser.base_file_view(
        file=fp.name,
        sql="",
        rows=200,
        cols=10,
        select_cols="value",
        engine="auto",
        meta_only=False,
        page=0,
        page_size=200,
    )
    assert selected["single_file_full_read"] is True
    assert selected["showing"] == 250
    assert selected["has_more"] is False
    assert selected["columns"] == ["value"]
    assert selected["data"][-1] == {"value": 249}


def test_download_lazy_csv_requires_selected_columns_for_wide_sources():
    data = {f"c{i:03d}": [i] for i in range(filebrowser.MAX_CSV_DOWNLOAD_AUTO_COLUMNS + 1)}

    with pytest.raises(HTTPException) as exc:
        filebrowser._download_lazy_csv(pl.DataFrame(data).lazy(), "", "", 10)

    assert exc.value.status_code == 400
    assert "컬럼" in str(exc.value.detail)


def test_download_lazy_csv_applies_sql_projection_and_row_cap():
    lf = pl.DataFrame({
        "flag": ["hit", "hit", "miss"],
        "shown": [1, 2, 3],
        "hidden": ["a", "b", "c"],
    }).lazy()

    df, csv_bytes = filebrowser._download_lazy_csv(lf, "flag == 'hit'", "shown", 10)

    assert df.to_dicts() == [{"shown": 1}, {"shown": 2}]
    assert b"shown" in csv_bytes
    assert b"hidden" not in csv_bytes

    with pytest.raises(HTTPException) as exc:
        filebrowser._download_lazy_csv(lf, "flag == 'hit'", "shown", 1)

    assert exc.value.status_code == 400
    assert "1" in str(exc.value.detail)


def test_duckdb_filter_normalization_keeps_where_read_only():
    assert duckdb_engine.normalize_filter_expr("lot_id == 'A' & value > 3") == "lot_id = 'A' AND value > 3"

    with pytest.raises(ValueError):
        duckdb_engine.normalize_filter_expr("value > 3; DROP TABLE source")


def test_duckdb_query_files_reads_parquet_when_available(tmp_path):
    pytest.importorskip("duckdb")
    fp = tmp_path / "sample.parquet"
    pl.DataFrame({
        "lot_id": ["A", "B", "C"],
        "value": [1, 2, 3],
    }).write_parquet(fp)

    df, columns, schema = duckdb_engine.query_files(
        [fp],
        where="value >= 2",
        select_cols=["lot_id", "value"],
        limit=10,
    )

    assert columns == ["lot_id", "value"]
    assert "value" in schema
    assert df.to_dicts() == [{"lot_id": "B", "value": 2}, {"lot_id": "C", "value": 3}]


def test_duckdb_view_normalizes_invalid_wafer_ids_when_available(tmp_path):
    pytest.importorskip("duckdb")
    fp = tmp_path / "sample.parquet"
    pl.DataFrame({
        "wafer_id": [1, 25, 1000],
        "value": [10, 20, 999],
    }).write_parquet(fp)

    result = filebrowser._run_view_duckdb(
        [fp],
        sql="",
        select_cols="wafer_id,value",
        rows=20,
        page=0,
        page_size=20,
        preview_cols=5,
    )

    assert result["wafer_filter"] == {"max": 25}
    assert result["data"] == [
        {"wafer_id": "1", "value": 10},
        {"wafer_id": "25", "value": 20},
        {"wafer_id": "25", "value": 999},
    ]


def test_source_data_files_resolves_hive_product_partitions(monkeypatch, tmp_path):
    part = tmp_path / "ROOT" / "history" / "product=PRODA" / "date=20240423" / "part.parquet"
    part.parent.mkdir(parents=True)
    part.write_bytes(b"placeholder")

    class DummyPaths:
        pass

    dummy_paths = DummyPaths()
    dummy_paths.db_root = tmp_path
    dummy_paths.base_root = tmp_path
    monkeypatch.setattr(utils, "PATHS", dummy_paths)

    assert utils.source_data_files(root="ROOT", product="PRODA") == [part]
