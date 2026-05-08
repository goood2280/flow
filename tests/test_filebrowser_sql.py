from __future__ import annotations

import json
import sys
import csv
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
from core import auth as auth_core  # noqa: E402
from routers import filebrowser  # noqa: E402


class _State:
    def __init__(self, user: dict):
        self.user = user


class _Request:
    headers = {}

    def __init__(self, username: str = "admin", role: str = "admin"):
        self.state = _State({"username": username, "role": role})


class _DummyPaths:
    def __init__(self, root: Path):
        self.base_root = root
        self.db_root = root
        self.data_root = root
        self.upload_dir = root / "uploads"
        self.cache_dir = root / "cache"
        self.db_cache_dir = root / "cache"
        self.log_dir = root / "logs"
        self.activity_log = self.log_dir / "activity.jsonl"
        self.download_log = self.log_dir / "downloads.jsonl"
        self.upload_dir.mkdir(exist_ok=True)
        self.log_dir.mkdir(exist_ok=True)


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


def test_base_files_cleans_legacy_cache_and_exposes_only_canonical(monkeypatch, tmp_path):
    fp = tmp_path / "ML_TABLE_PRODA.parquet"
    pl.DataFrame({
        "product": ["PRODA", "PRODA", "PRODA"],
        "lot_id": ["L1000", "L1000", "L2000"],
        "step_id": ["STEP_010", "STEP_020", "STEP_005"],
        "tkout_time": [
            "2026-04-28T08:00:00",
            "2026-04-28T09:00:00",
            "2026-04-27T07:00:00",
        ],
    }).write_parquet(fp)
    cache_dir = tmp_path / "cache"
    nested = cache_dir / "cache"
    nested.mkdir(parents=True)
    legacy_files = [
        cache_dir / "ML_TABLE_PRODA.parquet.latest_step_by_lot.parquet",
        cache_dir / "ML_TABLE_PRODA.parquet.latest_step_by_lot.meta.json",
        cache_dir / "ML_TABLE_PRODA.parquet.latest_lot_by_root_wafer.parquet",
        cache_dir / "ML_TABLE_PRODA.parquet.latest_lot_by_root_wafer.meta.json",
        cache_dir / "splittable_latest_lot_step.parquet",
        nested / "lot_progress_latest_lot_by_root_wafer.parquet.latest_step_by_lot.parquet",
    ]
    for legacy in legacy_files:
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text("legacy", encoding="utf-8")
    canonical = cache_dir / "lot_progress_latest_lot_by_root_wafer.parquet"
    pl.DataFrame({
        "product": ["PRODA"],
        "root_lot_id": ["A1000"],
        "wafer_id": ["1"],
        "lot_id": ["A1000A.2"],
        "step_id": ["STEP_CACHE"],
        "function_step": ["CACHE_FUNC"],
        "tkout_time": ["2026-05-08T08:55:00"],
        "update_time": ["2026-05-08T09:00:00"],
    }).write_parquet(canonical)

    class DummyPaths:
        pass

    dummy_paths = DummyPaths()
    dummy_paths.base_root = tmp_path
    dummy_paths.db_root = tmp_path
    dummy_paths.data_root = tmp_path
    dummy_paths.upload_dir = tmp_path / "uploads"
    dummy_paths.upload_dir.mkdir()
    monkeypatch.setattr(filebrowser, "PATHS", dummy_paths)
    filebrowser._LIST_CACHE.clear()

    listed = filebrowser.base_files()
    cache_rows = [row for row in listed["files"] if row.get("source") == "cache"]

    assert [row["path"] for row in listed["dirs"]] == ["cache"]
    assert [row["path"] for row in cache_rows] == ["cache/lot_progress_latest_lot_by_root_wafer.parquet"]
    assert cache_rows[0]["editable"] is False
    assert not nested.exists()
    for legacy in legacy_files:
        assert not legacy.exists()
    preview = filebrowser.base_file_view(
        file=cache_rows[0]["path"],
        sql="",
        rows=200,
        cols=10,
        select_cols="",
        engine="auto",
        meta_only=False,
        page=0,
        page_size=200,
    )
    assert preview["data"][0]["step_id"] == "STEP_CACHE"
    assert preview["data"][0]["function_step"] == "CACHE_FUNC"
    assert preview["data"][0]["tkout_time"] == "2026-05-08T08:55:00"
    assert preview["data"][0]["update_time"] == "2026-05-08T09:00:00"


def test_base_files_does_not_build_single_file_derived_cache(monkeypatch, tmp_path):
    fp = tmp_path / "ML_TABLE_PRODA.parquet"
    pl.DataFrame({
        "product": ["PRODA", "PRODA"],
        "lot_id": ["L1000", "L1000"],
        "step_id": ["STEP_010", "STEP_020"],
        "time": ["2026-04-28T08:00:00", "2026-04-28T09:00:00"],
    }).write_parquet(fp)

    class DummyPaths:
        pass

    dummy_paths = DummyPaths()
    dummy_paths.base_root = tmp_path
    dummy_paths.db_root = tmp_path
    dummy_paths.data_root = tmp_path
    dummy_paths.upload_dir = tmp_path / "uploads"
    dummy_paths.upload_dir.mkdir()
    monkeypatch.setattr(filebrowser, "PATHS", dummy_paths)
    filebrowser._LIST_CACHE.clear()

    listed = filebrowser.base_files()
    cache_rows = [row for row in listed["files"] if row.get("source") == "cache"]

    assert [row["path"] for row in listed["dirs"]] == []
    assert cache_rows == []
    assert not (tmp_path / "cache" / f"{fp.name}.latest_step_by_lot.parquet").exists()


def test_base_file_view_does_not_build_single_file_derived_cache(monkeypatch, tmp_path):
    fp = tmp_path / "ML_TABLE_PRODA.parquet"
    pl.DataFrame({
        "product": ["PRODA", "PRODA"],
        "lot_id": ["L1000", "L1000"],
        "step_id": ["STEP_010", "STEP_020"],
        "time": ["2026-04-28T08:00:00", "2026-04-28T09:00:00"],
    }).write_parquet(fp)

    class DummyPaths:
        pass

    dummy_paths = DummyPaths()
    dummy_paths.base_root = tmp_path
    dummy_paths.db_root = tmp_path
    dummy_paths.data_root = tmp_path
    dummy_paths.upload_dir = tmp_path / "uploads"
    dummy_paths.upload_dir.mkdir()
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

    assert preview["data"][0]["step_id"] == "STEP_010"
    assert not (tmp_path / "cache" / f"{fp.name}.latest_step_by_lot.parquet").exists()


def test_base_files_exposes_readable_lot_progress_cache(monkeypatch, tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_fp = cache_dir / "lot_progress_latest_lot_by_root_wafer.parquet"
    pl.DataFrame({
        "product": ["PRODA"],
        "root_lot_id": ["A1000"],
        "wafer_id": ["1"],
        "lot_id": ["A1000A.2"],
        "step_id": ["STEP_CACHE"],
        "function_step": ["CACHE_FUNC"],
        "tkout_time": ["2026-05-08T08:55:00"],
        "update_time": ["2026-05-08T09:00:00"],
    }).write_parquet(cache_fp)

    class DummyPaths:
        pass

    dummy_paths = DummyPaths()
    dummy_paths.base_root = tmp_path
    dummy_paths.db_root = tmp_path
    dummy_paths.data_root = tmp_path
    dummy_paths.upload_dir = tmp_path / "uploads"
    dummy_paths.upload_dir.mkdir()
    monkeypatch.setattr(filebrowser, "PATHS", dummy_paths)
    filebrowser._LIST_CACHE.clear()

    listed = filebrowser.base_files()
    cache_rows = [row for row in listed["files"] if row.get("path") == f"cache/{cache_fp.name}"]

    assert cache_rows and cache_rows[0]["role"] == "latest lot/step cache"
    preview = filebrowser.base_file_view(
        file=cache_rows[0]["path"],
        sql="",
        rows=200,
        cols=10,
        select_cols="",
        engine="auto",
        meta_only=False,
        page=0,
        page_size=200,
    )
    assert preview["data"] == [{
        "product": "PRODA",
        "root_lot_id": "A1000",
        "wafer_id": "1",
        "lot_id": "A1000A.2",
        "step_id": "STEP_CACHE",
        "function_step": "CACHE_FUNC",
        "tkout_time": "2026-05-08T08:55:00",
        "update_time": "2026-05-08T09:00:00",
    }]


def test_base_file_versioned_allows_any_single_csv_under_5mb(tmp_path):
    fp = tmp_path / "custom_lookup.csv"
    fp.write_text("a,b\n1,2\n", encoding="utf-8")

    assert filebrowser._base_file_versioned(fp.name, fp) is True


def test_base_file_versioned_rejects_single_csv_over_5mb(tmp_path):
    fp = tmp_path / "large_lookup.csv"
    fp.write_bytes(b"x" * (filebrowser.EDM_VERSION_MAX_CSV_BYTES + 1))

    assert filebrowser._base_file_versioned(fp.name, fp) is False


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


def test_base_file_view_reads_small_csv_fully_and_threshold_falls_back(monkeypatch, tmp_path):
    fp = tmp_path / "small_lookup.csv"
    fp.write_text("id,value\n" + "\n".join(f"{i},{i * 10}" for i in range(250)) + "\n", encoding="utf-8")

    dummy_paths = _DummyPaths(tmp_path)
    monkeypatch.setattr(filebrowser, "PATHS", dummy_paths)

    full = filebrowser.base_file_view(
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

    assert full["single_file_full_read"] is True
    assert full["showing"] == 250
    assert full["has_more"] is False

    filebrowser._save_filebrowser_settings({"csv_full_read_max_bytes": 1, "csv_rules": {}})
    paged = filebrowser.base_file_view(
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

    assert paged.get("single_file_full_read") is not True
    assert paged["showing"] == 200
    assert paged["has_more"] is True


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


def test_filebrowser_settings_gate_allows_page_admin(monkeypatch, tmp_path):
    dummy_paths = _DummyPaths(tmp_path)
    monkeypatch.setattr(filebrowser, "PATHS", dummy_paths)
    monkeypatch.setattr(auth_core, "is_page_admin", lambda username, page: username == "fb_mgr" and page == "filebrowser")

    with pytest.raises(HTTPException) as exc:
        filebrowser.save_filebrowser_settings(
            filebrowser.FileBrowserSettingsReq(csv_full_read_max_bytes=1024, csv_rules={}),
            _Request("viewer", "user"),
        )
    assert exc.value.status_code == 403

    saved = filebrowser.save_filebrowser_settings(
        filebrowser.FileBrowserSettingsReq(
            csv_full_read_max_bytes=2048,
            csv_rules={"rules.csv": {"required_columns": ["id"]}},
        ),
        _Request("fb_mgr", "user"),
    )

    assert saved["ok"] is True
    assert saved["csv_full_read_max_bytes"] == 2048
    assert saved["csv_rules"]["rules.csv"]["required_columns"] == ["id"]


def test_roots_hide_default_and_configured_db_dirs(monkeypatch, tmp_path):
    dummy_paths = _DummyPaths(tmp_path)
    monkeypatch.setattr(filebrowser, "PATHS", dummy_paths)
    filebrowser._LIST_CACHE.clear()
    for name in ("cache", "reformatter", "secret", "VISIBLE_DB"):
        target = tmp_path / name
        target.mkdir()
        pl.DataFrame({"value": [1]}).write_parquet(target / "part.parquet")
    filebrowser._save_filebrowser_settings({
        "csv_full_read_max_bytes": 10485760,
        "csv_rules": {},
        "hidden_db_dirs": ["cache", "reformatter", "secret"],
    })

    names = [row["name"] for row in filebrowser.list_roots(all=False)["roots"]]

    assert names == ["VISIBLE_DB"]


def test_csv_rule_validation_reports_supported_failures():
    rule = filebrowser._normalize_csv_rule({
        "required_columns": ["id", "name", "status", "qty", "code", "start", "end"],
        "not_empty": ["name"],
        "unique_keys": [["id"]],
        "enums": {"status": ["OK", "BAD"]},
        "numeric": {"qty": {"min": 1, "max": 10, "integer": True}},
        "regex": {"code": r"[A-Z]{2}\d{2}"},
        "conditions": [{"expr": "status == 'BAD'", "message": "BAD rows are not allowed"}],
    })

    result = filebrowser._validate_csv_rule(
        ["id", "name", "status", "qty", "code", "start", "end"],
        [
            ["1", "alpha", "OK", "2", "AB12", "2026-01-01", "2026-01-02"],
            ["1", "", "NOPE", "2.5", "bad", "2026-01-01", "2026-01-02"],
            ["3", "beta", "BAD", "11", "CD34", "2026-01-01", "2026-01-02"],
        ],
        rule,
    )

    assert result["ok"] is False
    assert {"not_empty", "unique_keys", "enums", "numeric", "regex", "conditions"}.issubset(
        {err["rule"] for err in result["errors"]}
    )


def test_csv_rule_blocks_save_and_sort_rule_reorders_physical_csv(monkeypatch, tmp_path):
    fp = tmp_path / "rules.csv"
    fp.write_text("id,name,rank\n1,alpha,2\n2,beta,1\n", encoding="utf-8")
    dummy_paths = _DummyPaths(tmp_path)
    monkeypatch.setattr(filebrowser, "PATHS", dummy_paths)
    monkeypatch.setattr(filebrowser, "BASE_VERSION_DIR", tmp_path / "versions")
    monkeypatch.setattr(filebrowser._s3, "sync_saved_path", lambda *_args, **_kwargs: {"ok": True, "skipped": True})
    monkeypatch.setattr(auth_core, "is_page_admin", lambda username, page: username == "fb_mgr" and page == "filebrowser")
    filebrowser._save_filebrowser_settings({
        "csv_full_read_max_bytes": 10485760,
        "csv_rules": {
            "rules.csv": {
                "required_columns": ["id", "name", "rank"],
                "not_empty": ["name"],
                "sort": [{"column": "rank", "direction": "asc", "type": "numeric", "nulls": "last"}],
            }
        },
    })

    with pytest.raises(HTTPException) as exc:
        filebrowser._save_base_file(
            filebrowser.BaseFileSaveReq(
                file="rules.csv",
                csv_text="id,name,rank\n1,,2\n2,beta,1\n",
                delimiter="comma",
                include_header=True,
            ),
            _Request("admin", "admin"),
        )
    assert exc.value.status_code == 400
    assert "CSV validation failed" in str(exc.value.detail)

    saved = filebrowser._save_base_file(
        filebrowser.BaseFileSaveReq(
            file="rules.csv",
            csv_text="id,name,rank\n1,alpha,2\n2,beta,1\n",
            delimiter="comma",
            include_header=True,
        ),
        _Request("fb_mgr", "user"),
    )
    assert saved["csv_validation"]["sorted"] is True

    rows = list(csv.DictReader(fp.open(encoding="utf-8")))
    assert [row["id"] for row in rows] == ["2", "1"]


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
