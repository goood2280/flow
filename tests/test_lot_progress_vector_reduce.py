"""WIP/latest-lot refresh: Polars pre-selection must give byte-identical cache items.

The row-by-row Python loop (FLOW_LOT_PROGRESS_VECTOR=0) is the reference. Synthetic FAB
files cover the tricky parts of its string rules: whitespace/"nan" blanks, `a or b`
fallbacks between time columns, case-colliding roots, wafer spellings, ties (first row
wins), cross-file replacement, missing columns, and dtypes that must fall back.
"""
from __future__ import annotations

import datetime as dt

import polars as pl
import pytest


def _write(path, frame: pl.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(path)


def _fab(tmp_path):
    fab = tmp_path / "1.RAWDATA_DB_FAB"
    s = pl.String
    base = {
        "root_lot_id": ["A1000", " A1000 ", "a1000", "A1000", "A1001", "A1001", "nan", "A1002", "A1002",
                        "A1003", "A1003", "A1004", "A1004", "A1005", None, "A1006", "A1006", "A1007"],
        "lot_id": ["A1000.1"] * 4 + ["A1001.1"] * 2 + ["X"] + ["A1002.1"] * 2 + ["A1003.1"] * 2
                  + ["A1004.1"] * 2 + ["A1005.1", "Z", "A1006.1", "A1006.2", "A1007.1"],
        "wafer_id": ["1", "01", "W01", "#1", "wf 2", "2.0", "3", "abc", " abc ", "", "4", "5", "5",
                     "NULL", "6", "7", "7", "8"],
        "step_id": ["S10", "S20", "S30", "S40", "S10", "S20", "S10", "S10", "S20", "S10", "S10", "S10",
                    " ", "S10", "S10", "S10", "S20", "S10"],
        "process_id": ["P1"] * 18,
        "tkin_time": ["2026-09-01 08:00:00", "2026-09-01 09:00:00", "2026-09-01 09:00:00", "2026-09-01 07:00:00",
                      "2026-09-02 01:00:00", "", "2026-09-01 00:00:00", "2026-09-03 00:00:00", "2026-09-03 00:00:00",
                      "2026-09-01", "2026-09-01", " nan ", "2026-09-05", "2026-09-01", "2026-09-01",
                      "2026-09-06", "2026-09-06", "2026-09-07"],
        "tkout_time": ["2026-09-01 08:30:00", "", "2026-09-01 09:00:00", "NaN", "", "2026-09-02 02:00:00",
                       "2026-09-01 00:10:00", "2026-09-03 00:00:00", "2026-09-03 00:00:00", "x", "y", None,
                       "2026-09-05 01:00:00", "z", "z", "2026-09-06 01:00:00", "2026-09-06 01:00:00", "  "],
        "eqp_id": [f"E{i}" for i in range(18)],
        "chamber_id": ["C"] * 18,
        "ppid": [f"PP{i}" for i in range(18)],
        "lot_type": ["P"] * 18,
        "step_desc": ["desc"] * 18,
    }
    _write(fab / "PRODA" / "date=20260901" / "part_0.parquet", pl.DataFrame(base, schema={k: s for k in base}))
    # Later file: newer row replaces, equal-time row must not, update_time (odd case) only here.
    later = {
        "ROOT_LOT_ID": ["A1000", "A1001", "A1006", "A1007"],
        "lot_id": ["A1000.9", "A1001.9", "A1006.9", "A1007.9"],
        "wafer_id": ["1", "W02", "7", "8"],
        "step_id": ["S90", "S90", "S90", "S90"],
        "process_id": ["P1"] * 4,
        "tkin_time": ["2026-09-09 00:00:00", "2026-09-01", "2026-09-06", ""],
        "tkout_time": ["2026-09-09 00:10:00", "2026-09-01", "2026-09-06 01:00:00", ""],
        "Update_Time": ["", "null", "", "2026-09-10"],
        "eqp_id": ["N1", "N2", "N3", "N4"],
    }
    _write(fab / "PRODA" / "date=20260902" / "part_0.parquet", pl.DataFrame(later, schema={k: s for k in later}))
    # Integer wafer ids are reducible; a datetime tkout_time is not (must fall back).
    _write(fab / "PRODB" / "date=20260901" / "part_0.parquet", pl.DataFrame({
        "root_lot_id": ["B1000"] * 4 + ["B1001"] * 2,
        "wafer_id": pl.Series([1, 1, 2, 2, 3, 3], dtype=pl.Int64),
        "step_id": ["S1", "S2", "S1", "S2", "S1", "S2"],
        "tkout_time": ["2026-09-01 01:00", "2026-09-01 02:00", "2026-09-01 03:00", "2026-09-01 03:00", "", "b"],
    }))
    _write(fab / "PRODB" / "date=20260902" / "part_0.parquet", pl.DataFrame({
        "root_lot_id": ["B1000", "B1002"],
        "wafer_id": ["1", "1"],
        "step_id": ["S3", "S1"],
        "tkout_time": [dt.datetime(2026, 9, 3, 4, 5, 6), dt.datetime(2026, 9, 3, 4, 5, 6, 700)],
    }))
    # Non-ASCII roots take the Python path for that batch.
    _write(fab / "PRODC" / "date=20260901" / "part_0.parquet", pl.DataFrame({
        "root_lot_id": ["C1000", "Ç1000", "c1000"],
        "wafer_id": ["1", "1", "1"],
        "step_id": ["S1", "S2", "S3"],
        "tkout_time": ["2026-09-01", "2026-09-02", "2026-09-02"],
    }))
    (tmp_path / "step_matching.csv").write_text("product,step_id,function_step\nPRODA,S20,FS20\n,S30,FS30\n", "utf-8")


@pytest.mark.parametrize("batch_rows", [5, 64000])
def test_vector_reduce_matches_row_by_row_python(monkeypatch, tmp_path, batch_rows):
    from core import lot_progress_cache as lpc
    from core import paths

    _fab(tmp_path)
    monkeypatch.setattr(paths, "_get_db_root", lambda: tmp_path)
    monkeypatch.setattr(paths, "_get_base_root", lambda: tmp_path)
    monkeypatch.setattr(lpc, "_FAB_READ_BATCH_ROWS", batch_rows)
    monkeypatch.setattr(lpc, "_yield_scan_slice", lambda: None)
    monkeypatch.setattr(lpc, "_ml_table_root_lot_ids", lambda **kw: set())
    # Same sources twice: the change-driven check would reuse the first result instead
    # of rescanning, and this test must compare two real scans.
    monkeypatch.setenv("FLOW_CACHE_CHANGE_DRIVEN", "0")

    def refresh():
        state = lpc.refresh_lot_progress_cache(force=True, required_products=["__always_rebuild__"])
        return state["items"], state["rows_seen"], state["errors"]

    monkeypatch.setenv("FLOW_LOT_PROGRESS_VECTOR", "0")
    expected, expected_rows, expected_errors = refresh()
    monkeypatch.setenv("FLOW_LOT_PROGRESS_VECTOR", "1")
    got, got_rows, got_errors = refresh()

    assert expected, "fixture should produce cache items"
    assert got == expected  # same dicts, same values, same order
    assert got_rows == expected_rows == 33
    assert got_errors == expected_errors == []


def test_batch_reducer_falls_back_on_types_python_would_print_differently():
    import pyarrow as pa
    from core import lot_progress_cache as lpc

    sources = {c: c for c in lpc._FAB_PROGRESS_COLUMNS}
    stringy = pa.RecordBatch.from_pydict({
        "root_lot_id": ["A", "A", "A"], "wafer_id": ["1", "1", "2"], "step_id": ["S1", "S2", "S1"],
        "tkout_time": ["2", "3", "1"],
    })
    # rows 1 (A_1, newest) and 2 (A_2); keys in first-appearance order
    assert lpc._batch_latest_candidates(stringy, sources) == ([1, 2], ["A_1", "A_2"], ["3", "1"])
    dated = pa.RecordBatch.from_pydict({
        "root_lot_id": ["A"], "wafer_id": ["1"], "step_id": ["S1"],
        "tkout_time": [dt.datetime(2026, 9, 1)],
    })
    assert lpc._batch_latest_candidates(dated, sources) is None
    floats = pa.RecordBatch.from_pydict({"root_lot_id": ["A"], "wafer_id": [1.0], "step_id": ["S1"]})
    assert lpc._batch_latest_candidates(floats, sources) is None
