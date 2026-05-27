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

from core import lot_progress_cache as cache  # noqa: E402


def test_lot_progress_metadata_documents_filebrowser_cache_rules():
    meta = cache.metadata()

    assert meta["product_binding"]["source_column"] == "product_dir.name"
    assert "product" in meta["latest_key_columns"]
    assert "LOT_WF(root_lot_id + wafer_id)" in meta["latest_key_columns"]
    assert meta["latest_order_columns"] == ["update_time", "tkout_time", "tkin_time", "time"]
    assert meta["lot_id_source_column"] == "lot_id"
    assert meta["root_lot_id_source_column"] == "root_lot_id"
    assert "wafer_id" in meta["wafer_id_source_column"]
    assert "step_matching.csv" in meta["step_mapping_sources"]
    assert meta["manual_change_points"]["db_root"] == "settings.json.lot_progress_source_root"
    assert meta["manual_change_points"]["column_mapping"] == "settings.json.lot_progress_column_mapping"
    assert meta["column_mapping"]["root_lot_id"] == "root_lot_id"
    assert meta["column_mapping"]["ppid"] == "ppid"
    assert "step_desc" in meta["function_step_source_columns"]


def test_load_step_matching_indexes_comma_separated_product_cells(monkeypatch, tmp_path):
    base = tmp_path / "base"
    data = tmp_path / "data"
    base.mkdir()
    data.mkdir()
    (tmp_path / "Vehicle_matching.csv").write_text(
        "product,step_id,step_desc\n"
        "\"PRODB,PRODA\",STEP_SHARED,PC\n",
        encoding="utf-8",
    )
    paths = type("Paths", (), {"db_root": tmp_path, "base_root": base, "data_root": data})()
    monkeypatch.setattr(cache, "PATHS", paths)

    by_product, by_step = cache.load_step_matching()

    assert by_product[("PRODA", "STEP_SHARED")] == "PC"
    assert by_product[("PRODB", "STEP_SHARED")] == "PC"
    assert by_step["STEP_SHARED"] == "PC"


def test_tracker_lot_status_cache_keeps_requested_fields(monkeypatch, tmp_path):
    fp = tmp_path / "lot_status_cache.json"
    monkeypatch.setattr(cache, "lot_status_cache_file", lambda: fp)

    state = cache.upsert_tracker_lot_status_rows([
        {
            "product": "PRODA",
            "root_lot_id": "A1000",
            "lot_id": "A1000A.1",
            "wafer_id": "W01",
            "step_id": "STEP_010",
            "func_step": "PC_LITHO",
            "tkout_time": "2026-05-08T10:00:00",
            "eqp_id": "EQP_SHOULD_NOT_PERSIST",
        }
    ])

    assert state["items"] == [{
        "root_lot_id": "A1000",
        "wafer_id": "1",
        "lot_id": "A1000A.1",
        "step_id": "STEP_010",
        "func_step": "PC_LITHO",
        "update_time": "2026-05-08T10:00:00",
    }]


def test_lot_progress_summary_returns_wafers_and_steps(monkeypatch):
    monkeypatch.setattr(cache, "load_lot_progress_cache", lambda max_age_seconds=None: {
        "items": [
            {"product": "PRODA", "root_lot_id": "A1000", "lot_id": "A1000A.1", "wafer_id": "2", "step_id": "STEP_020", "func_step": "GATE", "time": "2026-05-08T11:00:00"},
            {"product": "PRODA", "root_lot_id": "A1000", "lot_id": "A1000A.1", "wafer_id": "1", "step_id": "STEP_010", "func_step": "STI", "time": "2026-05-08T10:00:00"},
        ]
    })

    summary = cache.lot_progress_summary(lot_id="A1000A.1")

    assert summary["wafer_count"] == 2
    assert summary["wafer_ids"] == ["1", "2"]
    assert summary["step_id"] == "STEP_020"
    assert summary["func_step"] == "GATE"
    assert [(r["wafer_id"], r["step_id"], r["func_step"]) for r in summary["rows"]] == [
        ("1", "STEP_010", "STI"),
        ("2", "STEP_020", "GATE"),
    ]


def test_lot_progress_candidates_expose_full_lot_id(monkeypatch):
    monkeypatch.setattr(cache, "load_lot_progress_cache", lambda max_age_seconds=None: {
        "items": [
            {
                "product": "PRODA",
                "root_lot_id": "A1000",
                "lot_id": "A1000A.1",
                "wafer_id": "1",
                "step_id": "STEP_010",
                "func_step": "STI",
                "time": "2026-05-08T10:00:00",
            }
        ]
    })

    rows = cache.lot_id_candidates(product="PRODA")

    assert rows[0]["value"] == "A1000A.1"
    assert rows[0]["lot_id"] == "A1000A.1"
    assert rows[0]["fab_lot_id"] == "A1000A.1"
    assert rows[0]["root_lot_id"] == "A1000"


def test_lot_progress_lookup_uses_index_for_specific_lot(monkeypatch):
    items = [
        {
            "product": "PRODA",
            "root_lot_id": f"A{i:04d}",
            "lot_id": f"A{i:04d}A.1",
            "wafer_id": "1",
            "lot_wf": f"A{i:04d}_1",
            "step_id": "STEP_010",
            "tkout_time": f"2026-05-08T10:{i % 60:02d}:00",
        }
        for i in range(1000)
    ]
    state = {"generated_at": "2026-05-08T10:00:00", "count": len(items), "items": items}
    monkeypatch.setattr(cache, "load_lot_progress_cache", lambda max_age_seconds=None: state)
    original_matches = cache._matches
    calls = {"count": 0}

    def counted_matches(*args, **kwargs):
        calls["count"] += 1
        return original_matches(*args, **kwargs)

    monkeypatch.setattr(cache, "_matches", counted_matches)

    rows = cache.lookup_lot_progress(root_lot_id="A0999", wafer_id="1")

    assert rows[0]["lot_id"] == "A0999A.1"
    assert calls["count"] == 1


def test_lot_progress_list_products_reads_existing_cache_without_refresh(monkeypatch, tmp_path):
    data_root = tmp_path / "flow-data"
    db_root = tmp_path / "Fab"

    class DummyPaths:
        def __init__(self):
            self.cache_dir = data_root / "cache"
            self.db_cache_dir = db_root / "cache"
            self.data_root = data_root
            self.db_root = db_root
            self.base_root = db_root

    monkeypatch.setattr(cache, "PATHS", DummyPaths())
    monkeypatch.setattr(cache, "_CACHE_STATE", None)
    state = {
        "generated_at": "2026-05-08T10:00:00",
        "count": 2,
        "source_root": "",
        "source_roots": [],
        "column_mapping": cache.DEFAULT_LOT_PROGRESS_COLUMN_MAPPING,
        "items": [
            {"product": "PRODA", "root_lot_id": "A1000", "lot_id": "A1000A.1", "wafer_id": "1"},
            {"product": "PRODB", "root_lot_id": "B1000", "lot_id": "B1000A.1", "wafer_id": "1"},
        ],
    }
    cache.cache_file().parent.mkdir(parents=True, exist_ok=True)
    cache.cache_file().write_text(json.dumps(state), encoding="utf-8")

    def fail_refresh(*args, **kwargs):
        raise AssertionError("list_products should not refresh source parquet")

    monkeypatch.setattr(cache, "refresh_lot_progress_cache", fail_refresh)

    assert cache.list_products() == ["PRODA", "PRODB"]


def test_latest_fab_step_uses_lot_progress_memory_cache(monkeypatch):
    from core import lot_step

    monkeypatch.setattr(cache, "load_lot_progress_cache", lambda max_age_seconds=None: {
        "generated_at": "2026-05-08T10:00:00",
        "items": [
            {
                "product": "PRODA",
                "root_lot_id": "A1000",
                "lot_id": "A1000A.1",
                "wafer_id": "1",
                "lot_wf": "A1000_1",
                "step_id": "STEP_010",
                "function_step": "STI",
                "tkout_time": "2026-05-08T10:00:00",
            }
        ],
    })
    monkeypatch.setattr(cache, "read_lot_progress_cache", lambda max_age_seconds=None, allow_stale=True: {
        "generated_at": "2026-05-08T10:00:00",
        "items": [
            {
                "product": "PRODA",
                "root_lot_id": "A1000",
                "lot_id": "A1000A.1",
                "wafer_id": "1",
                "lot_wf": "A1000_1",
                "step_id": "STEP_010",
                "function_step": "STI",
                "tkout_time": "2026-05-08T10:00:00",
            }
        ],
    })

    row = lot_step._latest_fab_step_from_lot_progress_cache(product="PRODA", root_lot_id="A1000", wafer_id="1")

    assert row["step_id"] == "STEP_010"
    assert row["function_step"] == "STI"
    assert row["source"] == "lot_progress_latest_cache"


def test_export_lot_progress_parquet_writes_readable_latest_lot_file(monkeypatch, tmp_path):
    data_root = tmp_path / "flow-data"
    db_root = tmp_path / "Fab"
    db_root.mkdir()

    class DummyPaths:
        def __init__(self):
            self.cache_dir = data_root / "cache"
            self.db_cache_dir = db_root / "cache"
            self.base_root = db_root

        @property
        def data_root(self):
            return data_root

        @property
        def db_root(self):
            return db_root

    monkeypatch.setattr(cache, "PATHS", DummyPaths())

    state = {
        "generated_at": "2026-05-08T10:00:00",
        "items": [
            {
                "product": "PRODA",
                "process_id": "PRDA",
                "root_lot_id": "A1000",
                "lot_id": "A1000A.2",
                "wafer_id": "W01",
                "step_id": "STEP_020",
                "func_step": "GATE",
                "tkout_time": "2026-05-08T09:00:00",
            }
        ],
    }

    out = cache.export_lot_progress_parquet(state)
    df = pl.read_parquet(cache.filebrowser_cache_parquet_file())

    assert out["rows"] == 1
    assert out["paths"] == [str(cache.filebrowser_cache_parquet_file())]
    assert not cache.cache_parquet_file().exists()
    assert df.columns == [
        "product", "root_lot_id", "wafer_id", "lot_id",
        "step_id", "function_step", "tkout_time", "update_time",
    ]
    assert df.select(["product", "root_lot_id", "wafer_id", "lot_id", "step_id", "function_step", "tkout_time", "update_time"]).to_dicts() == [{
        "product": "PRODA",
        "root_lot_id": "A1000",
        "wafer_id": "1",
        "lot_id": "A1000A.2",
        "step_id": "STEP_020",
        "function_step": "GATE",
        "tkout_time": "2026-05-08T09:00:00",
        "update_time": "2026-05-08T10:00:00",
    }]


def test_refresh_lot_progress_cache_uses_fab_product_folder_without_process_id(monkeypatch, tmp_path):
    data_root = tmp_path / "flow-data"
    db_root = tmp_path / "Fab"
    fab_product = db_root / "1.RAWDATA_DB_FAB" / "PRODA"
    fab_product.mkdir(parents=True)
    pl.DataFrame({
        "root_lot_id": ["A1000", "A1000"],
        "lot_id": ["A1000A.1", "A1000A.2"],
        "wafer_id": ["W01", "W01"],
        "step_id": ["STEP_010", "STEP_020"],
        "tkin_time": ["2026-05-08T08:00:00", "2026-05-08T09:00:00"],
        "tkout_time": ["2026-05-08T08:30:00", "2026-05-08T09:30:00"],
    }).write_parquet(fab_product / "part.parquet")

    class DummyPaths:
        def __init__(self):
            self.cache_dir = data_root / "cache"
            self.db_cache_dir = db_root / "cache"
            self.data_root = data_root
            self.db_root = db_root
            self.base_root = db_root

    monkeypatch.setattr(cache, "PATHS", DummyPaths())
    monkeypatch.setattr(cache, "load_step_matching", lambda: ({}, {}))
    monkeypatch.setattr(cache, "_CACHE_STATE", None)

    state = cache.refresh_lot_progress_cache(force=True)
    df = pl.read_parquet(cache.filebrowser_cache_parquet_file())
    status = cache.cache_status()

    assert state["count"] == 1
    assert state["freshness_state"] == "ok"
    assert state["last_success_at"]
    assert Path(state["refresh_log_path"]).is_file()
    assert state["items"][0]["product"] == "PRODA"
    assert state["items"][0]["process_id"] == ""
    assert state["items"][0]["lot_id"] == "A1000A.2"
    assert df.to_dicts()[0]["product"] == "PRODA"
    assert status["row_count"] == 1
    assert status["freshness_state"] == "ok"


def test_refresh_lot_progress_cache_prefers_source_update_time_for_latest(monkeypatch, tmp_path):
    data_root = tmp_path / "flow-data"
    db_root = tmp_path / "Fab"
    fab_product = db_root / "1.RAWDATA_DB_FAB" / "PRODA"
    fab_product.mkdir(parents=True)
    pl.DataFrame({
        "root_lot_id": ["A1000", "A1000"],
        "lot_id": ["A1000A.OLD", "A1000A.NEW"],
        "wafer_id": ["W01", "W01"],
        "step_id": ["STEP_OLD", "STEP_NEW"],
        "tkin_time": ["2026-05-08T08:00:00", "2026-05-08T08:00:00"],
        "tkout_time": ["2026-05-08T12:00:00", "2026-05-08T11:00:00"],
        "update_time": ["2026-05-08T09:00:00", "2026-05-08T13:00:00"],
    }).write_parquet(fab_product / "part.parquet")

    class DummyPaths:
        def __init__(self):
            self.cache_dir = data_root / "cache"
            self.db_cache_dir = db_root / "cache"
            self.data_root = data_root
            self.db_root = db_root
            self.base_root = db_root

    monkeypatch.setattr(cache, "PATHS", DummyPaths())
    monkeypatch.setattr(cache, "load_step_matching", lambda: ({}, {}))
    monkeypatch.setattr(cache, "_CACHE_STATE", None)

    state = cache.refresh_lot_progress_cache(force=True)

    assert state["count"] == 1
    assert state["items"][0]["lot_id"] == "A1000A.NEW"
    assert state["items"][0]["update_time"] == "2026-05-08T13:00:00"


def test_refresh_lot_progress_cache_uses_step_desc_as_function_step_fallback(monkeypatch, tmp_path):
    data_root = tmp_path / "flow-data"
    db_root = tmp_path / "Fab"
    fab_product = db_root / "1.RAWDATA_DB_FAB" / "PRODA"
    fab_product.mkdir(parents=True)
    pl.DataFrame({
        "root_lot_id": ["A1000", "A1000"],
        "lot_id": ["A1000A.1", "A1000A.2"],
        "wafer_id": ["W01", "W01"],
        "step_id": ["STEP_010", "STEP_020"],
        "step_desc": ["OLD_DESC", "SORT_DESC"],
        "tkout_time": ["2026-05-08T08:30:00", "2026-05-08T09:30:00"],
    }).write_parquet(fab_product / "part.parquet")

    class DummyPaths:
        def __init__(self):
            self.cache_dir = data_root / "cache"
            self.db_cache_dir = db_root / "cache"
            self.data_root = data_root
            self.db_root = db_root
            self.base_root = db_root

    monkeypatch.setattr(cache, "PATHS", DummyPaths())
    monkeypatch.setattr(cache, "load_step_matching", lambda: ({}, {}))
    monkeypatch.setattr(cache, "_CACHE_STATE", None)

    state = cache.refresh_lot_progress_cache(force=True)
    df = pl.read_parquet(cache.filebrowser_cache_parquet_file())

    assert state["count"] == 1
    assert state["items"][0]["lot_id"] == "A1000A.2"
    assert state["items"][0]["function_step"] == "SORT_DESC"
    assert state["items"][0]["func_step"] == "SORT_DESC"
    assert df.to_dicts()[0]["function_step"] == "SORT_DESC"


def test_refresh_lot_progress_cache_uses_custom_column_mapping(monkeypatch, tmp_path):
    data_root = tmp_path / "flow-data"
    db_root = tmp_path / "Fab"
    fab_product = db_root / "1.RAWDATA_DB_FAB" / "PRODA"
    fab_product.mkdir(parents=True)
    data_root.mkdir(parents=True)
    mapping = {
        "root_lot_id": "ROOT_LOT",
        "lot_id": "FAB_LOT",
        "wafer_id": "WF",
        "step_id": "STEP",
        "process_id": "PROC",
        "tkin_time": "IN_TIME",
        "tkout_time": "OUT_TIME",
        "time": "EVENT_TIME",
        "update_time": "UPDATED",
        "eqp_id": "EQP",
        "chamber_id": "CHAMBER",
        "ppid": "PPID_COL",
    }
    (data_root / "settings.json").write_text(json.dumps({"lot_progress_column_mapping": mapping}), encoding="utf-8")
    pl.DataFrame({
        "ROOT_LOT": ["A1000", "A1000"],
        "FAB_LOT": ["A1000A.1", "A1000A.2"],
        "WF": ["W01", "W01"],
        "STEP": ["STEP_010", "STEP_020"],
        "PROC": ["PRODA", "PRODA"],
        "IN_TIME": ["2026-05-08T08:00:00", "2026-05-08T09:00:00"],
        "OUT_TIME": ["2026-05-08T08:30:00", "2026-05-08T09:30:00"],
        "UPDATED": ["2026-05-08T08:40:00", "2026-05-08T09:40:00"],
        "EQP": ["E1", "E2"],
        "CHAMBER": ["C1", "C2"],
        "PPID_COL": ["P1", "P2"],
    }).write_parquet(fab_product / "part.parquet")

    class DummyPaths:
        def __init__(self):
            self.cache_dir = data_root / "cache"
            self.db_cache_dir = db_root / "cache"
            self.data_root = data_root
            self.db_root = db_root
            self.base_root = db_root

    monkeypatch.setattr(cache, "PATHS", DummyPaths())
    monkeypatch.setattr(cache, "load_step_matching", lambda: ({}, {}))
    monkeypatch.setattr(cache, "_CACHE_STATE", None)

    state = cache.refresh_lot_progress_cache(force=True)
    df = pl.read_parquet(cache.filebrowser_cache_parquet_file())
    status = cache.cache_status()

    assert state["column_mapping"] == mapping
    assert state["count"] == 1
    assert state["items"][0]["root_lot_id"] == "A1000"
    assert state["items"][0]["lot_id"] == "A1000A.2"
    assert state["items"][0]["wafer_id"] == "1"
    assert state["items"][0]["step_id"] == "STEP_020"
    assert state["items"][0]["eqp_id"] == "E2"
    assert df.columns == [
        "product", "root_lot_id", "wafer_id", "lot_id",
        "step_id", "function_step", "tkout_time", "update_time",
    ]
    assert df.to_dicts()[0]["lot_id"] == "A1000A.2"
    assert status["column_mapping"] == mapping


def test_refresh_lot_progress_cache_reads_internal_rawdata_root_alias(monkeypatch, tmp_path):
    data_root = tmp_path / "flow-data"
    db_root = tmp_path / "Fab"
    fab_product = db_root / "1.RAWDATA_DB" / "PRODX" / "date=20260513"
    fab_product.mkdir(parents=True)
    pl.DataFrame({
        "root_lot_id": ["B2000"],
        "lot_id": ["B2000A.1"],
        "wafer_id": ["#21"],
        "step_id": ["STEP_090"],
        "tkin_time": ["2026-05-13T08:00:00"],
        "tkout_time": ["2026-05-13T09:00:00"],
    }).write_parquet(fab_product / "part.parquet")

    class DummyPaths:
        def __init__(self):
            self.cache_dir = data_root / "cache"
            self.db_cache_dir = db_root / "cache"
            self.data_root = data_root
            self.db_root = db_root
            self.base_root = db_root

    monkeypatch.setattr(cache, "PATHS", DummyPaths())
    monkeypatch.setattr(cache, "load_step_matching", lambda: ({}, {}))
    monkeypatch.setattr(cache, "_CACHE_STATE", None)

    state = cache.refresh_lot_progress_cache(force=True, source_root="1.RAWDATA_DB")
    df = pl.read_parquet(cache.filebrowser_cache_parquet_file())

    assert state["source_root"] == "1.RAWDATA_DB"
    assert state["source_roots"] == ["1.RAWDATA_DB"]
    assert state["count"] == 1
    assert state["items"][0]["product"] == "PRODX"
    assert state["items"][0]["source_root"] == "1.RAWDATA_DB"
    assert state["items"][0]["wafer_id"] == "21"
    assert df.to_dicts()[0]["lot_id"] == "B2000A.1"


def test_refresh_lot_progress_cache_uses_configured_source_root(monkeypatch, tmp_path):
    data_root = tmp_path / "flow-data"
    db_root = tmp_path / "Fab"
    raw_product = db_root / "1.RAWDATA_DB" / "PROD_RAW" / "date=20260513"
    legacy_product = db_root / "1.RAWDATA_DB_FAB" / "PROD_LEGACY" / "date=20260513"
    raw_product.mkdir(parents=True)
    legacy_product.mkdir(parents=True)
    data_root.mkdir(parents=True)
    (data_root / "settings.json").write_text(json.dumps({"lot_progress_source_root": "1.RAWDATA_DB_FAB"}), encoding="utf-8")
    pl.DataFrame({
        "root_lot_id": ["RAW1000"],
        "lot_id": ["RAW1000A.1"],
        "wafer_id": ["1"],
        "step_id": ["RAW_STEP"],
        "tkin_time": ["2026-05-13T08:00:00"],
        "tkout_time": ["2026-05-13T09:00:00"],
    }).write_parquet(raw_product / "part.parquet")
    pl.DataFrame({
        "root_lot_id": ["LEG1000"],
        "lot_id": ["LEG1000A.1"],
        "wafer_id": ["2"],
        "step_id": ["LEG_STEP"],
        "tkin_time": ["2026-05-13T10:00:00"],
        "tkout_time": ["2026-05-13T11:00:00"],
    }).write_parquet(legacy_product / "part.parquet")

    class DummyPaths:
        def __init__(self):
            self.cache_dir = data_root / "cache"
            self.db_cache_dir = db_root / "cache"
            self.data_root = data_root
            self.db_root = db_root
            self.base_root = db_root

    monkeypatch.setattr(cache, "PATHS", DummyPaths())
    monkeypatch.setattr(cache, "load_step_matching", lambda: ({}, {}))
    monkeypatch.setattr(cache, "_CACHE_STATE", None)

    state = cache.refresh_lot_progress_cache(force=True)

    assert state["configured_source_root"] == "1.RAWDATA_DB_FAB"
    assert state["source_roots"] == ["1.RAWDATA_DB_FAB"]
    assert state["count"] == 1
    assert state["items"][0]["product"] == "PROD_LEGACY"
    assert state["items"][0]["lot_id"] == "LEG1000A.1"


def test_refresh_lot_progress_cache_auto_uses_short_fab_root(monkeypatch, tmp_path):
    data_root = tmp_path / "flow-data"
    db_root = tmp_path / "Fab"
    fab_product = db_root / "FAB" / "PROD_FAB" / "date=20260513"
    fab_product.mkdir(parents=True)
    pl.DataFrame({
        "root_lot_id": ["FAB1000"],
        "lot_id": ["FAB1000A.1"],
        "wafer_id": ["3"],
        "step_id": ["FAB_STEP"],
        "tkin_time": ["2026-05-13T08:00:00"],
        "tkout_time": ["2026-05-13T09:00:00"],
    }).write_parquet(fab_product / "part.parquet")

    class DummyPaths:
        def __init__(self):
            self.cache_dir = data_root / "cache"
            self.db_cache_dir = db_root / "cache"
            self.data_root = data_root
            self.db_root = db_root
            self.base_root = db_root

    monkeypatch.setattr(cache, "PATHS", DummyPaths())
    monkeypatch.setattr(cache, "load_step_matching", lambda: ({}, {}))
    monkeypatch.setattr(cache, "_CACHE_STATE", None)

    state = cache.refresh_lot_progress_cache(force=True)
    status = cache.cache_status()

    assert state["source_root"] == "FAB"
    assert state["source_roots"] == ["FAB"]
    assert state["effective_source_roots"] == ["FAB"]
    assert state["items"][0]["source_root"] == "FAB"
    assert any(c["source_root"] == "FAB" and c["exists"] for c in status["source_root_candidates"])
