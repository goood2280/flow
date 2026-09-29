import polars as pl

from core.parquet_perf import hive_path_values, scan_parquet_hive_files, with_hive_path_columns


def _write(path, df):
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path)


def test_hive_folder_values_become_missing_columns(tmp_path):
    fp = tmp_path / "1.RAWDATA_DB_ET" / "product=PRODA" / "date=2026-07-23" / "part-0.parquet"
    _write(fp, pl.DataFrame({"root_lot_id": ["A1000"], "value": [1.0]}))

    assert hive_path_values(fp) == {"product": "PRODA", "date": "2026-07-23"}
    out = with_hive_path_columns(pl.scan_parquet(str(fp)), fp).collect()
    assert out["product"].to_list() == ["PRODA"] and out["date"].to_list() == ["2026-07-23"]


def test_existing_column_is_not_overwritten(tmp_path):
    fp = tmp_path / "product=PRODA" / "x.parquet"
    _write(fp, pl.DataFrame({"PRODUCT": ["REAL"], "v": [1]}))
    out = with_hive_path_columns(pl.scan_parquet(str(fp)), fp).collect()
    assert out.columns == ["PRODUCT", "v"]


def test_mixed_flat_and_hive_files_scan_together(tmp_path):
    flat = tmp_path / "PRODA" / "PRODA_2026_07_22.parquet"
    hive = tmp_path / "PRODA" / "date=2026-07-23" / "part-0.parquet"
    _write(flat, pl.DataFrame({"root_lot_id": ["A1"], "value": [1.0]}))
    _write(hive, pl.DataFrame({"root_lot_id": ["A2"], "value": [2.0]}))
    out = scan_parquet_hive_files([flat, hive], hive_partitioning=True).collect().sort("root_lot_id")
    assert out["date"].to_list() == [None, "2026-07-23"]


def test_flat_only_layout_is_unchanged(tmp_path):
    flat = tmp_path / "PRODA" / "PRODA_2026_07_22.parquet"
    _write(flat, pl.DataFrame({"root_lot_id": ["A1"]}))
    assert scan_parquet_hive_files([flat]).collect().columns == ["root_lot_id"]
