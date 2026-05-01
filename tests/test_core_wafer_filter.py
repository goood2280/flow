from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core import utils  # noqa: E402
from core import duckdb_engine  # noqa: E402
from core.long_pivot import pivot_inline_wafer, scan_long_inline  # noqa: E402


def test_common_file_readers_normalize_wafer_ids_to_physical_range(tmp_path):
    fp = tmp_path / "sample.parquet"
    pl.DataFrame({
        "wafer_id": ["1", "25", "1000", "26", "0", "1.5"],
        "value": ["ok1", "ok25", "mapped1000", "mapped26", "bad0", "bad15"],
    }).write_parquet(fp)

    eager = utils.read_one_file(fp)
    assert eager["wafer_id"].to_list() == ["1", "25", "25", "1"]
    assert eager["value"].to_list() == ["ok1", "ok25", "mapped1000", "mapped26"]

    lazy = utils.scan_one_file(fp).collect()
    assert lazy["wafer_id"].to_list() == ["1", "25", "25", "1"]
    assert lazy["value"].to_list() == ["ok1", "ok25", "mapped1000", "mapped26"]


def test_read_source_filters_invalid_wafers_across_product_db(monkeypatch, tmp_path):
    db_root = tmp_path / "db"
    base_root = tmp_path / "base"
    product_dir = db_root / "1.RAWDATA_DB_FAB" / "PRODA" / "date=20260428"
    product_dir.mkdir(parents=True)
    base_root.mkdir()
    pl.DataFrame({
        "root_lot_id": ["R1000", "R1000", "R1000"],
        "wafer_id": ["1", "25", "1000"],
        "step_id": ["STEP_001", "STEP_025", "STEP_MAPPED"],
    }).write_parquet(product_dir / "part.parquet")
    monkeypatch.setattr(utils, "PATHS", SimpleNamespace(db_root=db_root, base_root=base_root))

    eager = utils.read_source(root="1.RAWDATA_DB_FAB", product="PRODA", max_files=None)
    assert eager["wafer_id"].to_list() == ["1", "25", "25"]
    assert eager["step_id"].to_list() == ["STEP_001", "STEP_025", "STEP_MAPPED"]

    lazy = utils.lazy_read_source(root="1.RAWDATA_DB_FAB", product="PRODA", max_files=None)
    out = lazy.collect().sort("wafer_id")
    assert out["wafer_id"].to_list() == ["1", "25", "25"]
    assert out["step_id"].to_list() == ["STEP_001", "STEP_025", "STEP_MAPPED"]


def test_duckdb_query_files_normalizes_invalid_wafer_ids(tmp_path):
    if not duckdb_engine.is_available():
        pytest.skip("duckdb is not installed")
    fp = tmp_path / "sample.parquet"
    pl.DataFrame({
        "wafer_id": ["1", "25", "1000"],
        "value": ["ok1", "ok25", "mapped1000"],
    }).write_parquet(fp)

    df, _cols, _schema = duckdb_engine.query_files([fp], limit=10)

    assert df["wafer_id"].to_list() == ["1", "25", "25"]
    assert df["value"].to_list() == ["ok1", "ok25", "mapped1000"]


def test_inline_subitem_shot_data_survives_wafer_normalization(tmp_path, monkeypatch):
    inline_dir = tmp_path / "1.RAWDATA_DB_INLINE" / "PRODA" / "date=20260428"
    inline_dir.mkdir(parents=True)
    pl.DataFrame({
        "root_lot_id": ["R1000", "R1000"],
        "lot_id": ["R1000A.1", "R1000A.1"],
        "wafer_id": ["1000", "1000"],
        "item_id": ["CD_GATE", "CD_GATE"],
        "subitem_id": ["SHOT01", "SHOT02"],
        "shot_x": [999, 888],
        "shot_y": [999, 888],
        "value": [10.0, 12.0],
    }).write_parquet(inline_dir / "part.parquet")
    monkeypatch.setattr(utils, "PATHS", SimpleNamespace(db_root=tmp_path, base_root=tmp_path))

    lf = scan_long_inline("PRODA", tmp_path)
    assert lf is not None
    out = lf.collect()
    assert out["wafer_id"].to_list() == ["25", "25"]
    assert "subitem_id" in out.columns
    assert "shot_x" not in out.columns
    assert "shot_y" not in out.columns

    wide = pivot_inline_wafer(lf)
    assert wide.height == 1
    assert wide["wafer_id"].to_list() == ["25"]
    assert wide["INLINE_CD_GATE_MEAN"].to_list() == [11.0]

    raw_lf = utils.lazy_read_source(root="1.RAWDATA_DB_INLINE", product="PRODA", max_files=None, recent_days=None)
    assert raw_lf is not None
    raw = raw_lf.collect()
    assert raw["wafer_id"].to_list() == ["25", "25"]
    assert "subitem_id" in raw.columns
    assert "shot_x" not in raw.columns
    assert "shot_y" not in raw.columns
