from __future__ import annotations

import json
import sys
import csv
import asyncio
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
from core import llm_adapter  # noqa: E402
from core import auth as auth_core  # noqa: E402
from core import ml_table_lookup  # noqa: E402
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


def test_filebrowser_read_scope_endpoints_require_current_user(monkeypatch, tmp_path):
    dummy_paths = _DummyPaths(tmp_path)
    monkeypatch.setattr(filebrowser, "PATHS", dummy_paths)
    seen = []

    def fake_current_user(request):
        seen.append(request)
        return {"username": "viewer", "role": "user"}

    monkeypatch.setattr(auth_core, "current_user", fake_current_user)
    req = _Request("viewer", "user")

    scopes = filebrowser.list_scopes(req)
    domain = filebrowser.domain_info(req)

    assert [item["key"] for item in scopes["scopes"]] == ["DB", "Base"]
    assert "dbs" in domain
    assert seen == [req, req]


def test_filebrowser_base_file_view_requires_current_user(monkeypatch, tmp_path):
    dummy_paths = _DummyPaths(tmp_path)
    monkeypatch.setattr(filebrowser, "PATHS", dummy_paths)
    (tmp_path / "lookup.csv").write_text("lot,value\nA1000,1\n", encoding="utf-8")
    seen = []

    def fake_current_user(request):
        seen.append(request)
        return {"username": "viewer", "role": "user"}

    monkeypatch.setattr(auth_core, "current_user", fake_current_user)
    req = _Request("viewer", "user")

    preview = filebrowser.base_file_view(file="lookup.csv", rows=10, cols=5, request=req)

    assert seen == [req]
    assert preview["kind"] == "table"
    assert preview["file"] == "lookup.csv"
    assert preview["columns"] == ["lot", "value"]


def test_filebrowser_rejects_db_derived_cache_targets(monkeypatch):
    seen = []

    def fake_current_user(request):
        seen.append(request)
        return {"username": "admin", "role": "admin"}

    monkeypatch.setattr(auth_core, "current_user", fake_current_user)
    req = _Request("admin", "admin")

    with pytest.raises(HTTPException) as status_exc:
        filebrowser.cache_match_status(req, target="et_lot_step_seq", product="PRODA")
    with pytest.raises(HTTPException) as refresh_exc:
        filebrowser.cache_match_refresh(
            filebrowser.CacheMatchRefreshReq(target="et_lot_step_seq", product="PRODA", force=True),
            req,
        )

    assert status_exc.value.status_code == 400
    assert refresh_exc.value.status_code == 400
    assert "Only lot_progress" in str(status_exc.value.detail)
    assert seen == [req, req]


def test_splittable_latest_lot_step_cache_status_reads_canonical_cache(monkeypatch, tmp_path):
    import routers.splittable as splittable

    cache_path = tmp_path / "lot_progress_latest_lot_by_root_wafer.parquet"
    pl.DataFrame({
        "product": ["ML_TABLE_PRODA", "ML_TABLE_PRODA", "ML_TABLE_PRODB"],
        "root_lot_id": ["A1000", "A1001", "B1000"],
        "wafer_id": ["1", "2", "1"],
        "lot_id": ["F1000", "F1001", "F2000"],
        "step_id": ["S1", "S2", "S3"],
        "function_step": ["AA", "BB", "CC"],
        "tkout_time": ["2026-05-10T00:00:00", "2026-05-10T01:00:00", "2026-05-10T02:00:00"],
        "update_time": ["2026-05-10T03:00:00", "2026-05-10T03:00:00", "2026-05-10T04:00:00"],
    }).write_parquet(cache_path)
    monkeypatch.setattr(splittable, "_latest_lot_step_cache_path", lambda: cache_path)
    monkeypatch.setattr(splittable, "_match_cache_state", lambda: {"last_refresh_at": "2026-05-10T04:00:00"})
    monkeypatch.setattr(splittable, "_match_cache_refresh_minutes", lambda: 30)

    status = splittable._latest_lot_step_cache_status(product="PRODA")

    assert status["ok"] is True
    assert status["row_count"] == 3
    assert status["product_row_count"] == 2
    assert status["products"] == ["ML_TABLE_PRODA", "ML_TABLE_PRODB"]
    assert status["updated_at"] == "2026-05-10T04:00:00"


def test_filebrowser_cache_settings_updates_lot_progress_interval(monkeypatch, tmp_path):
    from core import lot_progress_cache

    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "admin", "role": "admin"})
    monkeypatch.setattr(filebrowser, "PATHS", _DummyPaths(tmp_path))
    monkeypatch.setattr(lot_progress_cache, "cache_file", lambda: tmp_path / "lot_wf_current.json")
    monkeypatch.setattr(lot_progress_cache, "filebrowser_cache_parquet_file", lambda: tmp_path / "lot_progress_latest_lot_by_root_wafer.parquet")
    monkeypatch.setattr(lot_progress_cache, "cache_status", lambda: {
        "ok": True,
        "interval_minutes": 45,
        "scheduler_started": True,
        "source_root_candidates": [{"source_root": "1.RAWDATA_DB_FAB", "path": str(tmp_path / "1.RAWDATA_DB_FAB"), "exists": True}],
        "effective_source_roots": ["1.RAWDATA_DB_FAB"],
    })

    column_mapping = {
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

    out = filebrowser.cache_match_settings(
        filebrowser.CacheMatchSettingsReq(
            target="lot_progress",
            interval_minutes=45,
            source_root="1.RAWDATA_DB_FAB",
            auto_s3_upload_on_save=True,
            column_mapping=column_mapping,
        ),
        _Request("admin", "admin"),
    )

    saved = json.loads((tmp_path / "settings.json").read_text("utf-8"))
    fb_saved = json.loads((tmp_path / "filebrowser_settings.json").read_text("utf-8"))
    assert saved["lot_progress_refresh_minutes"] == 45
    assert saved["lot_progress_source_root"] == "1.RAWDATA_DB_FAB"
    assert saved["lot_progress_column_mapping"] == column_mapping
    assert fb_saved["auto_s3_upload_on_save"] is True
    assert out["target"] == "lot_progress"
    assert out["interval_minutes"] == 45
    assert out["configured_source_root"] == "1.RAWDATA_DB_FAB"
    assert out["column_mapping"] == column_mapping
    assert out["manual_change_points"]["column_mapping"] == "settings.json.lot_progress_column_mapping"
    assert out["effective_source_roots"] == ["1.RAWDATA_DB_FAB"]
    assert out["source_root_candidates"][0]["source_root"] == "1.RAWDATA_DB_FAB"
    assert out["auto_s3_upload_on_save"] is True
    assert out["schedule_enabled"] is True


def test_filebrowser_cache_refresh_only_supports_lot_progress(monkeypatch):
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "admin", "role": "admin"})

    with pytest.raises(HTTPException) as exc:
        filebrowser.cache_match_refresh(
            filebrowser.CacheMatchRefreshReq(target="et_lot_step_seq", product="PRODA", source_root="ET_MEASURE", force=True),
            _Request("admin", "admin"),
        )

    assert exc.value.status_code == 400
    assert "Only lot_progress" in str(exc.value.detail)


def test_ml_table_lookup_missing_returns_readiness_without_source_scan(monkeypatch, tmp_path):
    dummy_paths = _DummyPaths(tmp_path)
    monkeypatch.setattr(filebrowser, "PATHS", dummy_paths)
    monkeypatch.setattr(ml_table_lookup, "PATHS", dummy_paths)
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "viewer", "role": "user"})
    fp = tmp_path / "ML_TABLE_PRODX.parquet"
    fp.write_bytes(b"not-a-real-parquet")
    enqueued = []

    def fake_enqueue(path):
        enqueued.append(path)
        return {"ok": True, "status": "queued", "queued": [str(path)], "current": ""}

    def fail_scan(*_args, **_kwargs):
        raise AssertionError("cold lookup must not scan source parquet")

    monkeypatch.setattr(ml_table_lookup, "enqueue_build", fake_enqueue)
    monkeypatch.setattr(ml_table_lookup.pl, "scan_parquet", fail_scan)

    out = filebrowser.ml_table_root_lot_lookup(
        filebrowser.MlTableLookupReq(file=fp.name, root_lot_id="R1000"),
        _Request("viewer", "user"),
    )

    assert out["lookup_cache_hit"] is False
    assert out["cache_status"] == "queued"
    assert out["data"] == []
    assert enqueued == [fp.resolve()]


def test_ml_table_lookup_cache_returns_selected_columns_and_caps_rows(monkeypatch, tmp_path):
    dummy_paths = _DummyPaths(tmp_path)
    monkeypatch.setattr(ml_table_lookup, "PATHS", dummy_paths)
    fp = tmp_path / "ML_TABLE_PRODX.parquet"
    pl.DataFrame({
        "product": ["PRODX"] * 30,
        "root_lot_id": ["R1000"] * 30,
        "lot_id": ["R1000A.1"] * 30,
        "wafer_id": [str(i + 1) for i in range(30)],
        "step_id": ["S1"] * 30,
        "function_step": ["SORT"] * 30,
        "tkout_time": ["2026-05-01T00:00:00"] * 30,
        "KNOB_A": [f"V{i}" for i in range(30)],
        "KNOB_B": [f"B{i}" for i in range(30)],
    }).write_parquet(fp)

    built = ml_table_lookup.build_lookup_cache(fp, force=True)
    out = ml_table_lookup.query_root_lot(fp, "R1000", selected_cols=["wafer_id", "KNOB_A"])

    assert built["ok"] is True
    assert out["lookup_cache_hit"] is True
    assert out["cache_status"] == "fresh"
    assert out["columns"] == ["wafer_id", "KNOB_A"]
    assert out["showing"] == 25
    assert out["total_rows"] == 30
    assert out["limited"] is True
    assert set(out["data"][0]) == {"wafer_id", "KNOB_A"}


def test_ml_table_lookup_defaults_to_identity_columns(monkeypatch, tmp_path):
    dummy_paths = _DummyPaths(tmp_path)
    monkeypatch.setattr(ml_table_lookup, "PATHS", dummy_paths)
    fp = tmp_path / "ML_TABLE_PRODX.parquet"
    pl.DataFrame({
        "root_lot_id": ["R1000"],
        "lot_id": ["R1000A.1"],
        "wafer_id": ["1"],
        "step_id": ["S1"],
        "function_step": ["SORT"],
        "tkout_time": ["2026-05-01T00:00:00"],
        "KNOB_A": ["ON"],
    }).write_parquet(fp)
    ml_table_lookup.build_lookup_cache(fp, force=True)

    out = ml_table_lookup.query_root_lot(fp, "R1000")

    assert out["columns"] == ["root_lot_id", "lot_id", "wafer_id", "step_id", "function_step", "tkout_time"]
    assert "KNOB_A" not in out["data"][0]


def test_ml_table_lookup_rejects_unknown_and_full_width_columns(monkeypatch, tmp_path):
    dummy_paths = _DummyPaths(tmp_path)
    monkeypatch.setattr(ml_table_lookup, "PATHS", dummy_paths)
    fp = tmp_path / "ML_TABLE_PRODX.parquet"
    pl.DataFrame({"root_lot_id": ["R1000"], "wafer_id": ["1"], "KNOB_A": ["ON"]}).write_parquet(fp)
    ml_table_lookup.build_lookup_cache(fp, force=True)

    with pytest.raises(ml_table_lookup.MlTableLookupError) as unknown:
        ml_table_lookup.query_root_lot(fp, "R1000", selected_cols=["NOPE"])
    with pytest.raises(ml_table_lookup.MlTableLookupError) as full:
        ml_table_lookup.query_root_lot(fp, "R1000", selected_cols=["*"])

    assert unknown.value.code == "unknown_column"
    assert full.value.code == "full_width_blocked"


def test_ml_table_lookup_uses_stale_cache_while_rebuild_is_queued(monkeypatch, tmp_path):
    dummy_paths = _DummyPaths(tmp_path)
    monkeypatch.setattr(ml_table_lookup, "PATHS", dummy_paths)
    fp = tmp_path / "ML_TABLE_PRODX.parquet"
    pl.DataFrame({"root_lot_id": ["R1000"], "wafer_id": ["1"], "KNOB_A": ["ON"]}).write_parquet(fp)
    ml_table_lookup.build_lookup_cache(fp, force=True)
    pl.DataFrame({"root_lot_id": ["R1000", "R2000"], "wafer_id": ["1", "1"], "KNOB_A": ["NEW", "OFF"]}).write_parquet(fp)
    enqueued = []
    monkeypatch.setattr(ml_table_lookup, "enqueue_build", lambda path: enqueued.append(path) or {"ok": True, "status": "queued"})

    out = ml_table_lookup.query_root_lot(fp, "R1000", selected_cols=["wafer_id", "KNOB_A"])

    assert out["lookup_cache_hit"] is True
    assert out["cache_status"] == "stale"
    assert out["source_stale"] is True
    assert out["data"] == [{"wafer_id": "1", "KNOB_A": "ON"}]
    assert enqueued == [fp.resolve()]


def test_db_cache_builds_et_step_seq_point_summary(monkeypatch, tmp_path):
    from core import db_cache

    dummy_paths = _DummyPaths(tmp_path)
    monkeypatch.setattr(db_cache, "PATHS", dummy_paths)
    et_dir = tmp_path / "1.RAWDATA_DB_ET" / "PRODA"
    et_dir.mkdir(parents=True)
    pl.DataFrame({
        "root_lot_id": ["A1000", "A1000", "A1000", "A1000"],
        "lot_id": ["A1000A.1", "A1000A.1", "A1000A.1", "A1000A.1"],
        "wafer_id": ["1", "1", "2", "2"],
        "step_id": ["EA10", "EA10", "EA20", "EA20"],
        "step_seq": ["N01", "N01", "N02", "N02"],
        "tkout_time": ["2024-04-20T01:00:00", "2024-04-20T01:00:00", "2024-04-20T02:00:00", "2024-04-20T02:00:00"],
        "value": [1.0, 2.0, 3.0, 4.0],
    }).write_parquet(et_dir / "part_0.parquet")

    out = db_cache.refresh_db_cache("et_lot_step_seq", product="PRODA")
    df = pl.read_parquet(db_cache.cache_file("et_lot_step_seq"))

    assert out["ok"] is True
    assert out["row_count"] == 1
    row = df.to_dicts()[0]
    assert row["lot_id"] == "A1000A.1"
    assert row["step_seq_list"] == "N01,N02"
    assert row["step_seq_pt_list"] == "N01(2pt), N02(2pt)"
    assert row["step_id_list"] == "EA10,EA20"
    assert row["point_count"] == 4
    assert row["tkout_time"] == "2024-04-20T02:00:00"


def test_filebrowser_lot_progress_cache_status_and_refresh_contract(monkeypatch, tmp_path):
    from core import lot_progress_cache

    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "admin", "role": "admin"})
    json_fp = tmp_path / "lot_wf_current.json"
    parquet_fp = tmp_path / "lot_progress_latest_lot_by_root_wafer.parquet"
    json_fp.write_text(json.dumps({
        "generated_at": "2026-05-10T01:00:00",
        "count": 1,
        "items": [{"product": "PRODA", "root_lot_id": "A1000", "wafer_id": "1", "lot_id": "A1000A.1"}],
    }), encoding="utf-8")
    pl.DataFrame({
        "product": ["PRODA"],
        "root_lot_id": ["A1000"],
        "wafer_id": ["1"],
        "lot_id": ["A1000A.1"],
        "step_id": ["ST01"],
        "function_step": ["24.SORT"],
        "tkout_time": ["2026-05-10T00:00:00"],
        "update_time": ["2026-05-10T01:00:00"],
    }).write_parquet(parquet_fp)
    monkeypatch.setattr(lot_progress_cache, "cache_file", lambda: json_fp)
    monkeypatch.setattr(lot_progress_cache, "filebrowser_cache_parquet_file", lambda: parquet_fp)
    refresh_calls = []
    monkeypatch.setattr(
        lot_progress_cache,
        "refresh_lot_progress_cache",
        lambda force=False, source_root="": refresh_calls.append((force, source_root)) or {
            "generated_at": "2026-05-10T02:00:00",
            "count": 2,
            "files_scanned": 3,
            "rows_seen": 4,
            "errors": [],
            "items": [{"product": "PRODA"}, {"product": "PRODB"}],
        },
    )
    monkeypatch.setattr(lot_progress_cache, "export_lot_progress_parquet", lambda state=None: {"ok": True, "rows": 2, "paths": [str(parquet_fp)]})
    monkeypatch.setattr(filebrowser.pl, "scan_parquet", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("status must not scan parquet")))

    status = filebrowser.cache_match_status(_Request("admin", "admin"), target="lot_progress")
    assert refresh_calls == []
    refreshed = filebrowser.cache_match_refresh(
        filebrowser.CacheMatchRefreshReq(target="lot_progress", force=True),
        _Request("admin", "admin"),
    )

    assert status["target"] == "lot_progress"
    assert status["row_count"] == 1
    assert status["products"] == ["PRODA"]
    assert status["product_count"] == 1
    assert status["product_binding"]["source_column"] == "product_dir.name"
    assert status["latest_key_columns"] == ["product", "LOT_WF(root_lot_id + wafer_id)"]
    assert status["latest_order_columns"] == ["update_time", "tkout_time", "tkin_time", "time"]
    assert status["lot_id_source_column"] == "lot_id"
    assert status["root_lot_id_source_column"] == "root_lot_id"
    assert "wafer_id" in status["wafer_id_source_column"]
    assert "step_matching.csv" in status["step_mapping_sources"]
    assert refreshed["unit_action"] == "filebrowser.cache.lot_progress.refresh"
    assert refresh_calls == [(True, "")]
    assert refreshed["row_count"] == 2
    assert refreshed["products"] == ["PRODA", "PRODB"]
    assert refreshed["product_count"] == 2
    assert refreshed["product_binding"]["source_column"] == "product_dir.name"
    assert refreshed["latest_key_columns"] == ["product", "LOT_WF(root_lot_id + wafer_id)"]
    assert refreshed["latest_order_columns"] == ["update_time", "tkout_time", "tkin_time", "time"]
    assert refreshed["paths"] == [str(parquet_fp)]
    assert refreshed["s3_sync"]["status"] == "disabled_by_filebrowser_setting"


def test_filebrowser_cache_llm_refresh_uses_llm_target_allowlist(monkeypatch):
    from core import llm_adapter

    events = []
    calls = []
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "admin", "role": "admin"})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(
        llm_adapter,
        "complete",
        lambda *args, **kwargs: {"ok": True, "text": '{"target":"lot_progress","reason":"latest lot cache"}'},
    )
    monkeypatch.setattr(filebrowser, "jsonl_append", lambda path, row: events.append(row))

    def fake_refresh(target, **kwargs):
        calls.append((target, kwargs))
        return {"ok": True, "target": target, "unit_action": "filebrowser.cache.lot_progress.refresh", "row_count": 9}

    monkeypatch.setattr(filebrowser, "_refresh_filebrowser_cache_target", fake_refresh)

    out = filebrowser.cache_llm_refresh(
        filebrowser.CacheLlmRefreshReq(prompt="1.RAWDATA_DB에서 lot_progress_latest_lot 캐시 만들어줘", product="PRODA", force=True),
        _Request("admin", "admin"),
    )

    assert out["unit_action"] == "filebrowser.cache.llm.refresh"
    assert out["target"] == "lot_progress"
    assert out["llm"]["used"] is True
    assert calls[0][0] == "lot_progress"
    assert calls[0][1]["product"] == "PRODA"
    assert calls[0][1]["source_root"] == "1.RAWDATA_DB"
    assert events[-1]["action"] == "filebrowser:cache-llm-refresh"


def test_filebrowser_settings_llm_draft_sanitizes_csv_rules(monkeypatch):
    from core import llm_adapter

    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "admin", "role": "admin"})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(
        llm_adapter,
        "complete",
        lambda *args, **kwargs: {
            "ok": True,
            "text": json.dumps({
                "csv_rules": {
                    "ppid_knob.csv": {
                        "required_columns": ["product", "missing_col"],
                        "not_empty": ["feature_name"],
                        "unique_keys": [["product", "missing_col"]],
                        "enums": {"operator": ["eq"], "ghost": ["x"]},
                        "conditions": [
                            {"expr": "product != ''", "message": "product required"},
                            {"expr": "missing_col != ''", "message": "bad"},
                        ],
                        "sort": [{"column": "ghost", "direction": "asc", "type": "string", "nulls": "last"}],
                        "write_path": "/tmp/unsafe",
                    }
                },
                "warnings": ["model warning"],
            })
        },
    )

    out = filebrowser.filebrowser_settings_llm_draft(
        filebrowser.FileBrowserSettingsLlmDraftReq(
            file="ppid_knob.csv",
            prompt="필수 컬럼과 unique key 초안",
            columns=["product", "feature_name", "operator"],
            sample_rows=[{"product": "PRODA", "feature_name": "24 SORT", "operator": "eq"}],
            current_rule={},
        ),
        _Request("admin", "admin"),
    )

    draft = out["draft"]
    warning_text = "\n".join(out["warnings"])
    assert out["unit_action"] == "filebrowser.settings.llm.draft"
    assert out["saved"] is False
    assert out["llm"]["used"] is True
    assert draft["required_columns"] == ["product"]
    assert draft["not_empty"] == ["feature_name"]
    assert "unique_keys" not in draft
    assert draft["enums"] == {"operator": ["eq"]}
    assert draft["conditions"] == [{"expr": "product != ''", "message": "product required"}]
    assert "sort" not in draft
    assert "write_path" in warning_text
    assert "missing_col" in warning_text
    assert "ghost" in warning_text


def test_filebrowser_settings_llm_draft_fallback_understands_leading_number_desc(monkeypatch):
    from core import llm_adapter

    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "admin", "role": "admin"})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(
        llm_adapter,
        "complete",
        lambda *args, **kwargs: {"ok": False, "text": "", "error": "timeout"},
    )

    out = filebrowser.filebrowser_settings_llm_draft(
        filebrowser.FileBrowserSettingsLlmDraftReq(
            file="ppid_knob.csv",
            prompt="feature_name열에 앞에 숫자에 따라서 내림차순으로 해줘",
            columns=["product", "feature_name", "operator"],
            sample_rows=[{"product": "PRODA", "feature_name": "24 SORT", "operator": "eq"}],
            current_rule={},
        ),
        _Request("admin", "admin"),
    )

    assert out["llm"]["available"] is True
    assert out["llm"]["used"] is False
    assert out["draft"]["sort"] == [
        {"column": "feature_name", "direction": "desc", "type": "leading_number", "nulls": "last"}
    ]
    assert "ordered_by" not in out["draft"]
    warning_text = "\n".join(out["warnings"])
    assert "LLM failed: timeout" in warning_text


def test_filebrowser_settings_llm_draft_expert_fallback_builds_detailed_rules(monkeypatch):
    from core import llm_adapter

    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "admin", "role": "admin"})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)

    out = filebrowser.filebrowser_settings_llm_draft(
        filebrowser.FileBrowserSettingsLlmDraftReq(
            file="ppid_knob.csv",
            prompt="적용규칙에 대해서 전문가처럼 가능한거 전부 상세하게 짜줘",
            columns=[
                "product", "feature_name", "function_step", "rule_order",
                "operator", "category", "rank", "start_time", "end_time",
            ],
            sample_rows=[{
                "product": "PRODA",
                "feature_name": "24 SORT",
                "function_step": "SORT",
                "rule_order": "R1",
                "operator": "eq",
                "category": "KNOB",
                "rank": "1",
                "start_time": "2026-05-01",
                "end_time": "2026-05-02",
            }],
            current_rule={},
        ),
        _Request("admin", "admin"),
    )

    draft = out["draft"]
    assert "product" in draft["required_columns"]
    assert "feature_name" in draft["not_empty"]
    assert draft["unique_keys"][0] == ["product", "feature_name", "rule_order"]
    assert draft["enums"]["operator"] == ["eq"]
    assert draft["numeric"]["rank"]["integer"] is True
    assert "start_time" in draft["date"]
    assert draft["regex"]["feature_name"] == r"\d+(?:\.\d+)?\s+.+"
    assert draft["conditions"] == [{"expr": "end_time >= start_time", "message": "end_time must be >= start_time"}]
    assert draft["sort"] == [
        {"column": "product", "direction": "asc", "type": "string", "nulls": "last"},
        {"column": "feature_name", "direction": "asc", "type": "leading_number", "nulls": "last"},
        {"column": "rule_order", "direction": "asc", "type": "rule_order", "nulls": "last"},
    ]


def test_filebrowser_settings_llm_draft_explicit_duplicate_prompt_overrides_llm_noise(monkeypatch):
    from core import llm_adapter

    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "admin", "role": "admin"})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(
        llm_adapter,
        "complete",
        lambda *args, **kwargs: {
            "ok": True,
            "text": json.dumps({
                "csv_rules": {
                    "lot.csv": {
                        "required_columns": ["product", "feature_name"],
                        "not_empty": ["product", "feature_name"],
                        "enums": {"operator": ["eq"]},
                    }
                }
            }),
        },
    )

    out = filebrowser.filebrowser_settings_llm_draft(
        filebrowser.FileBrowserSettingsLlmDraftReq(
            file="lot.csv",
            prompt="product, lot_id, wafer_id가 다 똑같은 행이 있어서는 안돼",
            columns=["product", "lot_id", "wafer_id", "feature_name", "operator"],
            sample_rows=[{"product": "PRODA", "lot_id": "A1000.1", "wafer_id": "21"}],
            current_rule={},
        ),
        _Request("admin", "admin"),
    )

    assert out["llm"]["used"] is True
    assert out["draft"] == {"unique_keys": [["product", "lot_id", "wafer_id"]]}
    assert out["warnings"] == []


def test_filebrowser_settings_llm_draft_explicit_duplicate_prompt_warns_missing_columns(monkeypatch):
    from core import llm_adapter

    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "admin", "role": "admin"})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)

    out = filebrowser.filebrowser_settings_llm_draft(
        filebrowser.FileBrowserSettingsLlmDraftReq(
            file="ppid_knob.csv",
            prompt="product, lot_id, wafer_id가 다 똑같은 행이 있어서는 안돼",
            columns=["product", "feature_name", "operator"],
            sample_rows=[{"product": "PRODA", "feature_name": "24 SORT", "operator": "eq"}],
            current_rule={"required_columns": ["product", "feature_name"], "enums": {"operator": ["eq"]}},
        ),
        _Request("admin", "admin"),
    )

    assert out["draft"] == {}
    warning_text = "\n".join(out["warnings"])
    assert "lot_id" in warning_text
    assert "wafer_id" in warning_text
    assert "unique key" in warning_text


def test_filebrowser_settings_llm_draft_explicit_enum_prompt_overrides_llm_noise(monkeypatch):
    from core import llm_adapter

    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "admin", "role": "admin"})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(
        llm_adapter,
        "complete",
        lambda *args, **kwargs: {
            "ok": True,
            "text": json.dumps({
                "csv_rules": {
                    "ppid_knob.csv": {
                        "required_columns": ["product", "feature_name", "function_step", "rule_order", "operator", "category"],
                        "not_empty": ["product", "feature_name", "function_step", "rule_order", "operator", "category"],
                        "unique_keys": [["product", "feature_name", "function_step", "rule_order"]],
                        "enums": {"operator": ["eq", "neq"]},
                    }
                }
            }),
        },
    )

    out = filebrowser.filebrowser_settings_llm_draft(
        filebrowser.FileBrowserSettingsLlmDraftReq(
            file="ppid_knob.csv",
            prompt="operator는 eq나 neq만 있어야해",
            columns=["product", "feature_name", "function_step", "rule_order", "operator", "category"],
            sample_rows=[{"operator": "eq"}],
            current_rule={},
        ),
        _Request("admin", "admin"),
    )

    assert out["llm"]["used"] is True
    assert out["draft"] == {"enums": {"operator": ["eq", "neq"]}}
    assert out["warnings"] == []


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("ppid는 비어있으면 안돼", {"not_empty": ["ppid"]}),
        (
            "product feature_name function_step rule_order operator category 값은 비어있으면 안돼",
            {"not_empty": ["product", "feature_name", "function_step", "rule_order", "operator", "category"]},
        ),
        (
            "product feature_name function_step rule_order operator category 컬럼은 반드시 있어야해",
            {"required_columns": ["product", "feature_name", "function_step", "rule_order", "operator", "category"]},
        ),
        ("rank는 1 이상 숫자 정수여야해", {"numeric": {"rank": {"min": 1, "integer": True}}}),
        ("knob_value는 숫자여야 하고 0 이상 100 이하만 허용해", {"numeric": {"knob_value": {"min": 0, "max": 100}}}),
        ("rule_order는 R1 R2 R3 같은 R숫자 또는 RO만 가능해", {"regex": {"rule_order": r"R\d+|RO"}}),
        ("rule_order는 RO 또는 R1만 허용해", {"enums": {"rule_order": ["RO", "R1"]}}),
        ("feature_name은 앞에 24 SORT처럼 숫자와 공정명이 있어야해", {"regex": {"feature_name": r"\d+(?:\.\d+)?\s+.+"}}),
        ("category는 PPID_05_1 같은 PPID_숫자_숫자 형식이어야해", {"regex": {"category": r"^PPID_\d+_\d+$"}}),
        ("function_step은 대문자와 underscore 형식이어야해", {"regex": {"function_step": r"^[A-Z_]+$"}}),
        ("start_time과 end_time은 날짜 형식이어야해", {"date": ["start_time", "end_time"]}),
        ("end_time은 start_time보다 빠르면 안돼", {"conditions": [{"expr": "end_time >= start_time", "message": "end_time must be >= start_time"}]}),
        (
            "product 오름차순, feature_name 앞 숫자 오름차순, rule_order 순서대로 저장 정렬해줘",
            {"sort": [
                {"column": "product", "direction": "asc", "type": "string", "nulls": "last"},
                {"column": "feature_name", "direction": "asc", "type": "leading_number", "nulls": "last"},
                {"column": "rule_order", "direction": "asc", "type": "rule_order", "nulls": "last"},
            ]},
        ),
        (
            "현재 행 순서가 product, feature_name, rule_order 기준으로 정렬되어 있는지 검증해줘",
            {"ordered_by": {"keys": [
                {"column": "product", "direction": "asc", "type": "string", "nulls": "last"},
                {"column": "feature_name", "direction": "asc", "type": "leading_number", "nulls": "last"},
                {"column": "rule_order", "direction": "asc", "type": "rule_order", "nulls": "last"},
            ]}},
        ),
        ("knob_name과 knob_value는 빈 값이 있으면 안돼", {"not_empty": ["knob_name", "knob_value"]}),
    ],
)
def test_filebrowser_settings_llm_draft_explicit_single_intents_stay_minimal(monkeypatch, prompt, expected):
    from core import llm_adapter

    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "admin", "role": "admin"})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(
        llm_adapter,
        "complete",
        lambda *args, **kwargs: {
            "ok": True,
            "text": json.dumps({
                "csv_rules": {
                    "ppid_knob.csv": {
                        "required_columns": ["product", "feature_name", "function_step", "rule_order", "operator", "category"],
                        "not_empty": ["product", "feature_name", "function_step", "rule_order", "operator", "category"],
                        "unique_keys": [["product", "feature_name", "function_step", "rule_order"]],
                        "enums": {"operator": ["eq", "neq"]},
                    }
                }
            }),
        },
    )

    out = filebrowser.filebrowser_settings_llm_draft(
        filebrowser.FileBrowserSettingsLlmDraftReq(
            file="ppid_knob.csv",
            prompt=prompt,
            columns=[
                "product", "feature_name", "function_step", "rule_order", "operator", "category",
                "ppid", "rank", "knob_name", "knob_value", "start_time", "end_time",
            ],
            sample_rows=[{"product": "PRODA", "feature_name": "24 SORT", "operator": "eq"}],
            current_rule={},
        ),
        _Request("admin", "admin"),
    )

    assert out["llm"]["used"] is True
    assert out["draft"] == expected


def test_filebrowser_settings_llm_draft_combines_validation_and_save_sort(monkeypatch):
    from core import llm_adapter

    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "admin", "role": "admin"})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)

    out = filebrowser.filebrowser_settings_llm_draft(
        filebrowser.FileBrowserSettingsLlmDraftReq(
            file="ppid_knob.csv",
            prompt=(
                "product feature_name rule_order는 필수 컬럼이고 빈 값 있으면 저장 막기. "
                "start_time과 end_time은 날짜 형식이어야 하고 end_time >= start_time 조건으로 이상 있으면 저장 막기. "
                "현재 순서가 product, feature_name, rule_order 기준으로 맞는지 검사하고 저장할 때도 같은 순서로 정렬해줘"
            ),
            columns=[
                "product", "feature_name", "function_step", "rule_order", "operator", "category",
                "start_time", "end_time",
            ],
            sample_rows=[{"product": "PRODA", "feature_name": "24 SORT", "rule_order": "R1"}],
            current_rule={},
        ),
        _Request("admin", "admin"),
    )

    sort_specs = [
        {"column": "product", "direction": "asc", "type": "string", "nulls": "last"},
        {"column": "feature_name", "direction": "asc", "type": "leading_number", "nulls": "last"},
        {"column": "rule_order", "direction": "asc", "type": "rule_order", "nulls": "last"},
    ]
    draft = out["draft"]
    assert draft["required_columns"][:3] == ["product", "feature_name", "rule_order"]
    assert draft["date"] == ["start_time", "end_time"]
    assert draft["conditions"] == [{"expr": "end_time >= start_time", "message": "end_time must be >= start_time"}]
    assert draft["ordered_by"] == {"keys": sort_specs}
    assert draft["sort"] == sort_specs
    assert out["draft_sections"]["validation_logic"]["ordered_by"] == {"keys": sort_specs}
    assert out["draft_sections"]["sort_logic"]["sort"] == sort_specs


def test_filebrowser_sql_llm_draft_writes_filter_only(monkeypatch):
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "viewer", "role": "user"})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(
        llm_adapter,
        "complete",
        lambda *_args, **_kwargs: {"ok": True, "text": json.dumps({"sql": "lot_id = 'A1000' AND wafer_id = 21"})},
    )

    out = filebrowser.filebrowser_sql_llm_draft(
        filebrowser.FileBrowserSqlLlmDraftReq(
            natural_language="A1000 wafer 21만 보여줘",
            columns=["LOT_ID", "wafer_id", "step_id", "value"],
        ),
        _Request("viewer", "user"),
    )

    assert out["ok"] is True
    assert out["unit_action"] == "filebrowser.sql.llm.draft"
    assert out["sql"] == "LOT_ID = 'A1000' AND wafer_id = 21"
    assert out["llm"]["used"] is True
    assert out["resolved_columns"] == ["wafer_id"]


def test_filebrowser_sql_llm_draft_reports_prompt_column_resolution(monkeypatch):
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "viewer", "role": "user"})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)

    out = filebrowser.filebrowser_sql_llm_draft(
        filebrowser.FileBrowserSqlLlmDraftReq(
            natural_language="root_lot_id가 A1000이고 wafer_id가 21이고 ghost_col도 확인",
            columns=["root_lot_id", "wafer_id", "step_id"],
        ),
        _Request("viewer", "user"),
    )

    assert out["ok"] is True
    assert out["resolved_columns"] == ["root_lot_id", "wafer_id"]
    assert out["unknown_column_terms"] == ["ghost_col"]
    assert "A1000" in out["value_terms"]


def test_filebrowser_sql_llm_draft_sends_schema_samples_and_reports_values(monkeypatch):
    calls = []
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "viewer", "role": "user"})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)

    def fake_complete(ask, **_kwargs):
        calls.append(json.loads(ask))
        return {"ok": True, "text": json.dumps({"sql": "step_id = 'ETCH'", "resolved_values": ["ETCH"]})}

    monkeypatch.setattr(llm_adapter, "complete", fake_complete)

    out = filebrowser.filebrowser_sql_llm_draft(
        filebrowser.FileBrowserSqlLlmDraftReq(
            natural_language="step_id가 ETCH인 행",
            columns=["step_id", "value"],
            dtypes={"step_id": "String", "value": "Float64"},
            sample_rows=[{"step_id": "ETCH", "value": 1.2}],
        ),
        _Request("viewer", "user"),
    )

    assert out["ok"] is True
    assert out["resolved_values"] == ["ETCH"]
    assert calls[0]["schema"][0] == {"name": "step_id", "dtype": "String", "sample_values": ["ETCH"]}
    assert calls[0]["sample_rows"] == []
    assert calls[0]["sample_profile"]["rows_sampled"] == 1
    assert calls[0]["sample_profile"]["sampling_policy"]["row_dump_in_prompt"] is False
    assert calls[0]["sample_profile"]["sampling_policy"]["profile_value_limit"] == 3


def test_filebrowser_sql_llm_draft_sanitizes_selected_columns(monkeypatch):
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "viewer", "role": "user"})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(
        llm_adapter,
        "complete",
        lambda *_args, **_kwargs: {
            "ok": True,
            "text": json.dumps({
                "sql": "wafer_id = 21",
                "selected_columns": ["lot_id", "ghost_col", "WAFER_ID"],
            }),
        },
    )

    out = filebrowser.filebrowser_sql_llm_draft(
        filebrowser.FileBrowserSqlLlmDraftReq(
            natural_language="lot_id, wafer_id, ghost_col만 보여주고 wafer 21 필터",
            columns=["lot_id", "wafer_id", "step_id"],
            preferred_selected_columns=["step_id"],
        ),
        _Request("viewer", "user"),
    )

    assert out["ok"] is True
    assert out["sql"] == "wafer_id = 21"
    assert out["where_sql"] == "wafer_id = 21"
    assert out["display_sql"] == "SELECT lot_id, wafer_id WHERE wafer_id = 21"
    assert out["selected_columns"] == ["lot_id", "wafer_id"]
    assert "ghost_col" in "\n".join(out["warnings"])


def test_filebrowser_sql_llm_draft_profiles_server_source(monkeypatch):
    calls = []
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "viewer", "role": "user"})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)

    def fake_lazy_read_source(**kwargs):
        assert kwargs["root"] == "ROOT"
        assert kwargs["product"] == "PRODA"
        return pl.DataFrame({
            "lot_id": [f"A{i:04d}" for i in range(250)],
            "wafer_id": list(range(250)),
            "step_id": ["ETCH"] * 250,
        }).lazy()

    def fake_complete(ask, **_kwargs):
        calls.append(json.loads(ask))
        return {"ok": True, "text": json.dumps({"sql": "wafer_id = 21"})}

    monkeypatch.setattr(filebrowser, "lazy_read_source", fake_lazy_read_source)
    monkeypatch.setattr(llm_adapter, "complete", fake_complete)

    out = filebrowser.filebrowser_sql_llm_draft(
        filebrowser.FileBrowserSqlLlmDraftReq(
            natural_language="조건 없이 구조 확인",
            root="ROOT",
            product="PRODA",
            scope="hive",
        ),
        _Request("viewer", "user"),
    )

    assert out["ok"] is True
    assert out["columns"] == ["lot_id", "wafer_id", "step_id"]
    assert out["sample_profile"]["source_sampled"] is True
    assert out["sample_profile"]["rows_sampled"] == 20
    assert out["sample_profile"]["columns_profiled"] == 3
    assert out["sample_profile"]["sampling_policy"]["rows_limit"] == 20
    assert calls[0]["sample_profile"]["rows_sampled"] == 20
    assert calls[0]["sample_rows"] == []
    assert calls[0]["schema"][0]["sample_values"] == ["A0000", "A0001", "A0002"]


def test_filebrowser_sql_llm_draft_extends_profile_for_value_terms(monkeypatch):
    calls = []
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "viewer", "role": "user"})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)

    def fake_lazy_read_source(**_kwargs):
        return pl.DataFrame({
            "lot_id": [f"A{i:04d}" for i in range(250)],
            "wafer_id": list(range(250)),
            "step_id": ["ETCH"] * 250,
        }).lazy()

    def fake_complete(ask, **_kwargs):
        calls.append(json.loads(ask))
        return {"ok": True, "text": json.dumps({"sql": "lot_id = 'A0049'"})}

    monkeypatch.setattr(filebrowser, "lazy_read_source", fake_lazy_read_source)
    monkeypatch.setattr(llm_adapter, "complete", fake_complete)

    out = filebrowser.filebrowser_sql_llm_draft(
        filebrowser.FileBrowserSqlLlmDraftReq(
            natural_language="lot_id가 A0049인 행",
            root="ROOT",
            product="PRODA",
            scope="hive",
        ),
        _Request("viewer", "user"),
    )

    lot_profile = next(item for item in out["sample_profile"]["columns"] if item["name"] == "lot_id")
    assert out["ok"] is True
    assert out["sample_profile"]["rows_sampled"] == 50
    assert out["sample_profile"]["sampling_policy"]["extra_rows_for_value_terms"] is True
    assert "A0049" in lot_profile["sample_values"]
    assert len(lot_profile["sample_values"]) <= 3
    assert calls[0]["sample_profile"]["rows_sampled"] == 50


def test_filebrowser_sql_llm_draft_profiles_wide_table_columns_selectively(monkeypatch):
    calls = []
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "viewer", "role": "user"})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)

    def fake_complete(ask, **_kwargs):
        calls.append(json.loads(ask))
        return {"ok": True, "text": json.dumps({"sql": "c119 >= 0"})}

    monkeypatch.setattr(llm_adapter, "complete", fake_complete)
    columns = [f"c{i:03d}" for i in range(120)]
    sample_rows = [
        {col: idx + col_idx for col_idx, col in enumerate(columns)}
        for idx in range(60)
    ]

    out = filebrowser.filebrowser_sql_llm_draft(
        filebrowser.FileBrowserSqlLlmDraftReq(
            natural_language="c119 큰순서",
            columns=columns,
            dtypes={col: "Int64" for col in columns},
            sample_rows=sample_rows,
            preferred_selected_columns=["c118"],
        ),
        _Request("viewer", "user"),
    )

    profile_names = [item["name"] for item in out["sample_profile"]["columns"]]
    assert out["ok"] is True
    assert out["sample_profile"]["rows_sampled"] == 20
    assert out["sample_profile"]["columns_scanned"] == 120
    assert out["sample_profile"]["columns_profiled"] == 80
    assert profile_names[:2] == ["c119", "c118"]
    assert calls[0]["sample_rows"] == []
    assert len(calls[0]["columns"]) == 80
    assert len(calls[0]["schema"]) == 80
    assert calls[0]["sample_profile"]["sampling_policy"]["row_dump_in_prompt"] is False


def test_filebrowser_sql_llm_draft_selection_only_prompt(monkeypatch):
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "viewer", "role": "user"})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)

    out = filebrowser.filebrowser_sql_llm_draft(
        filebrowser.FileBrowserSqlLlmDraftReq(
            natural_language="lot_id, wafer_id만 보고 싶어",
            columns=["lot_id", "wafer_id", "step_id"],
        ),
        _Request("viewer", "user"),
    )

    assert out["ok"] is True
    assert out["sql"] == ""
    assert out["where_sql"] == ""
    assert out["display_sql"] == "SELECT lot_id, wafer_id"
    assert out["selected_columns"] == ["lot_id", "wafer_id"]


def test_filebrowser_sql_llm_draft_selection_and_order_by_prompt(monkeypatch):
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "viewer", "role": "user"})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)

    out = filebrowser.filebrowser_sql_llm_draft(
        filebrowser.FileBrowserSqlLlmDraftReq(
            natural_language="product, feature_name, function_step, rule_order만 rule_order 순서",
            columns=["product", "feature_name", "function_step", "rule_order", "operator"],
        ),
        _Request("viewer", "user"),
    )

    assert out["ok"] is True
    assert out["sql"] == ""
    assert out["where_sql"] == ""
    assert out["selected_columns"] == ["product", "feature_name", "function_step", "rule_order"]
    assert out["sort"] == {"column": "rule_order", "direction": "asc", "nulls": "last"}
    assert out["display_sql"] == "SELECT product, feature_name, function_step, rule_order ORDER BY rule_order ASC"


def test_filebrowser_sql_draft_records_target_history(monkeypatch, tmp_path):
    dummy_paths = _DummyPaths(tmp_path)
    monkeypatch.setattr(filebrowser, "PATHS", dummy_paths)
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "viewer", "role": "user"})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)

    filebrowser.filebrowser_sql_llm_draft(
        filebrowser.FileBrowserSqlLlmDraftReq(
            natural_language="product만 rule_order 순서",
            columns=["product", "rule_order"],
            scope="hive",
            root="FAB",
            product="PRODA",
        ),
        _Request("viewer", "user"),
    )
    for source in ("agent_test_prompt", "home_flowi_unit_ai", "home_flowi_sql_draft"):
        filebrowser._record_filebrowser_ai_sql_history(
            "viewer",
            source=source,
            request_payload={
                "natural_language": f"{source} product만",
                "scope": "db_product",
                "root": "FAB",
                "product": "PRODA",
            },
            result_payload={
                "ok": True,
                "answer": "history answer",
                "merged": {
                    "display_sql": "SELECT product",
                    "where_sql": "",
                    "selected_columns": ["product"],
                },
                "preview": {
                    "columns": ["product"],
                    "rows": [{"product": "PRODA"}],
                    "total_rows": 1,
                    "preview_capped": False,
                },
                "trace": [{"node_id": "merge", "status": "success", "duration_ms": 3}],
            },
        )
    history = filebrowser.filebrowser_sql_history(_Request("viewer", "user"))
    by_source = {row["source"]: row for row in history["history"]}

    assert {"filebrowser", "agent_test_prompt", "home_flowi_unit_ai", "home_flowi_sql_draft"}.issubset(by_source)
    assert by_source["filebrowser"]["natural_language"] == "product만 rule_order 순서"
    assert by_source["filebrowser"]["username"] == "viewer"
    assert by_source["filebrowser"]["timestamp"]
    assert by_source["filebrowser"]["scope"] == "db_product"
    assert by_source["filebrowser"]["root"] == "FAB"
    assert by_source["filebrowser"]["product"] == "PRODA"
    assert by_source["filebrowser"]["display_sql"] == "SELECT product ORDER BY rule_order ASC"
    assert by_source["agent_test_prompt"]["answer"] == "history answer"
    assert by_source["home_flowi_unit_ai"]["preview_summary"]["rows_returned"] == 1
    assert "rows" not in by_source["home_flowi_unit_ai"]["preview_summary"]
    assert by_source["home_flowi_sql_draft"]["trace_summary"][0]["node_id"] == "merge"


def test_filebrowser_sql_llm_draft_does_not_select_columns_without_explicit_request(monkeypatch):
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "viewer", "role": "user"})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(
        llm_adapter,
        "complete",
        lambda *_args, **_kwargs: {
            "ok": True,
            "text": json.dumps({"sql": "wafer_id = 21", "selected_columns": ["lot_id"]}),
        },
    )

    out = filebrowser.filebrowser_sql_llm_draft(
        filebrowser.FileBrowserSqlLlmDraftReq(
            natural_language="wafer 21 필터",
            columns=["lot_id", "wafer_id", "step_id"],
        ),
        _Request("viewer", "user"),
    )

    assert out["ok"] is True
    assert out["selected_columns"] == []


@pytest.mark.parametrize("bad_sql", [
    "SELECT * FROM source WHERE lot_id = 'A1000'",
    "value > 3; DROP TABLE source",
    "missing_col = 'x'",
])
def test_filebrowser_sql_llm_draft_rejects_unsafe_or_unknown_columns(monkeypatch, bad_sql):
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "viewer", "role": "user"})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(
        llm_adapter,
        "complete",
        lambda *_args, **_kwargs: {"ok": True, "text": json.dumps({"sql": bad_sql})},
    )

    out = filebrowser.filebrowser_sql_llm_draft(
        filebrowser.FileBrowserSqlLlmDraftReq(
            natural_language="bad sql",
            columns=["lot_id", "wafer_id", "value"],
        ),
        _Request("viewer", "user"),
    )

    assert out["ok"] is False
    assert out["sql"] == ""
    assert out["warnings"]


def test_filebrowser_sql_llm_draft_falls_back_when_llm_unavailable(monkeypatch):
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "viewer", "role": "user"})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)

    out = filebrowser.filebrowser_sql_llm_draft(
        filebrowser.FileBrowserSqlLlmDraftReq(
            natural_language="root lot이 A1000이고 wafer 21",
            columns=["root_lot_id", "wafer_id", "step_id"],
        ),
        _Request("viewer", "user"),
    )

    assert out["ok"] is True
    assert out["fallback"] is True
    assert out["llm"]["used"] is False
    assert out["sql"] == "root_lot_id = 'A1000' AND wafer_id = 21"


def test_filebrowser_sql_llm_draft_fallback_preserves_korean_date(monkeypatch):
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "viewer", "role": "user"})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)

    out = filebrowser.filebrowser_sql_llm_draft(
        filebrowser.FileBrowserSqlLlmDraftReq(
            natural_language="root_lot_id가 B1000이고 wafer_id 가 3인거 필터링해줘 또 tkout_time이 2024년 4월 20일 이후인거만",
            columns=["root_lot_id", "wafer_id", "tkout_time"],
        ),
        _Request("viewer", "user"),
    )

    assert out["ok"] is True
    assert out["fallback"] is True
    assert out["sql"] == "root_lot_id = 'B1000' AND wafer_id = 3 AND tkout_time >= '2024-04-20'"


def test_filebrowser_sql_llm_draft_fallback_preserves_korean_datetime(monkeypatch):
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "viewer", "role": "user"})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)

    out = filebrowser.filebrowser_sql_llm_draft(
        filebrowser.FileBrowserSqlLlmDraftReq(
            natural_language="tkout_time이 2024년 4월 20일 오후 2시 5분 6초 이후인 행",
            columns=["root_lot_id", "wafer_id", "tkout_time"],
        ),
        _Request("viewer", "user"),
    )

    assert out["ok"] is True
    assert out["fallback"] is True
    assert out["sql"] == "tkout_time >= '2024-04-20T14:05:06'"


def test_filebrowser_sql_llm_draft_rejects_year_only_date_and_falls_back(monkeypatch):
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "viewer", "role": "user"})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(
        llm_adapter,
        "complete",
        lambda *_args, **_kwargs: {
            "ok": True,
            "text": json.dumps({"sql": "root_lot_id = 'B1000' AND wafer_id >= 3 AND tkout_time >= 2024"}),
        },
    )

    out = filebrowser.filebrowser_sql_llm_draft(
        filebrowser.FileBrowserSqlLlmDraftReq(
            natural_language="root_lot_id가 B1000이고 wafer_id 가 3인거 필터링해줘 또 tkout_time이 2024년 4월 20일 이후인거만",
            columns=["root_lot_id", "wafer_id", "tkout_time"],
        ),
        _Request("viewer", "user"),
    )

    assert out["ok"] is True
    assert out["fallback"] is True
    assert out["llm"]["used"] is True
    assert out["sql"] == "root_lot_id = 'B1000' AND wafer_id = 3 AND tkout_time >= '2024-04-20'"
    assert "date/time filters" in "\n".join(out["warnings"])


def test_filebrowser_sql_llm_draft_fallback_handles_root_lot_wafer_step(monkeypatch):
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "viewer", "role": "user"})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)

    out = filebrowser.filebrowser_sql_llm_draft(
        filebrowser.FileBrowserSqlLlmDraftReq(
            natural_language="root lot id 가 A1000 이고 wafer id가 21 이면서 step_id가 AA100240 행들을 찾아줘",
            columns=["root_lot_id", "lot_id", "wafer_id", "step_id", "product"],
        ),
        _Request("viewer", "user"),
    )

    assert out["ok"] is True
    assert out["fallback"] is True
    assert out["sql"] == "root_lot_id = 'A1000' AND wafer_id = 21 AND step_id = 'AA100240'"


def test_filebrowser_sql_llm_draft_reverses_function_step_from_step_matching(monkeypatch, tmp_path):
    dummy_paths = _DummyPaths(tmp_path)
    monkeypatch.setattr(filebrowser, "PATHS", dummy_paths)
    (tmp_path / "step_matching.csv").write_text(
        "product,step_id,function_step\n"
        "PRODA,AA100240,PC_LITHO\n"
        "PRODB,BB100240,PC_LITHO\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "viewer", "role": "user"})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)

    out = filebrowser.filebrowser_sql_llm_draft(
        filebrowser.FileBrowserSqlLlmDraftReq(
            natural_language="PC_LITHO step 행만 보여줘",
            product="PRODA",
            columns=["product", "step_id", "value"],
        ),
        _Request("viewer", "user"),
    )

    assert out["ok"] is True
    assert out["fallback"] is True
    assert out["sql"] == "step_id = 'AA100240'"
    assert out["step_mapping"]["used"] is True
    assert out["step_mapping"]["matches"] == [{
        "product": "PRODA",
        "step_id": "AA100240",
        "function_step": "PC_LITHO",
        "source": "step_matching.csv",
    }]


def test_filebrowser_sql_llm_draft_appends_step_mapping_to_llm_filter(monkeypatch, tmp_path):
    dummy_paths = _DummyPaths(tmp_path)
    monkeypatch.setattr(filebrowser, "PATHS", dummy_paths)
    (tmp_path / "step_matching.csv").write_text(
        "product,step_id,function_step\nPRODA,AA100240,PC_LITHO\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "viewer", "role": "user"})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(
        llm_adapter,
        "complete",
        lambda *_args, **_kwargs: {"ok": True, "text": json.dumps({"sql": "root_lot_id = 'A1000'"})},
    )

    out = filebrowser.filebrowser_sql_llm_draft(
        filebrowser.FileBrowserSqlLlmDraftReq(
            natural_language="A1000 PC_LITHO step 행만 보여줘",
            product="PRODA",
            columns=["root_lot_id", "step_id", "value"],
        ),
        _Request("viewer", "user"),
    )

    assert out["ok"] is True
    assert out["fallback"] is False
    assert out["llm"]["used"] is True
    assert out["sql"] == "root_lot_id = 'A1000' AND step_id = 'AA100240'"
    assert "step matching file used" in "\n".join(out["warnings"])


def test_filebrowser_sql_llm_draft_replaces_function_step_value_misread(monkeypatch, tmp_path):
    dummy_paths = _DummyPaths(tmp_path)
    monkeypatch.setattr(filebrowser, "PATHS", dummy_paths)
    (tmp_path / "step_matching.csv").write_text(
        "product,step_id,function_step\nPRODA,AA100240,PC_LITHO\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "viewer", "role": "user"})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(
        llm_adapter,
        "complete",
        lambda *_args, **_kwargs: {"ok": True, "text": json.dumps({"sql": "step_id = 'PC_LITHO'"})},
    )

    out = filebrowser.filebrowser_sql_llm_draft(
        filebrowser.FileBrowserSqlLlmDraftReq(
            natural_language="PC_LITHO step 행만 보여줘",
            product="PRODA",
            columns=["step_id", "value"],
        ),
        _Request("viewer", "user"),
    )

    assert out["ok"] is True
    assert out["fallback"] is True
    assert out["llm"]["used"] is True
    assert out["sql"] == "step_id = 'AA100240'"
    assert "mapped step_id" in "\n".join(out["warnings"])


def test_filebrowser_sql_llm_draft_fallback_handles_ioff_value_desc_sort(monkeypatch):
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "viewer", "role": "user"})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)

    out = filebrowser.filebrowser_sql_llm_draft(
        filebrowser.FileBrowserSqlLlmDraftReq(
            natural_language="IOFF value 큰순서",
            columns=["item_id", "value", "wafer_id"],
        ),
        _Request("viewer", "user"),
    )

    assert out["ok"] is True
    assert out["sql"] == "item_id = 'IOFF'"
    assert out["sort"] == {"column": "value", "direction": "desc", "nulls": "last"}
    assert out["display_sql"] == "item_id = 'IOFF' ORDER BY value DESC"
    assert out["selected_columns"] == []


def test_filebrowser_sql_llm_draft_hash_number_means_wafer_not_lot_text(monkeypatch):
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "viewer", "role": "user"})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(
        llm_adapter,
        "complete",
        lambda *_args, **_kwargs: {
            "ok": True,
            "text": json.dumps({"sql": "lot_id LIKE '%A1000 #3 IOFF%'"}),
        },
    )

    out = filebrowser.filebrowser_sql_llm_draft(
        filebrowser.FileBrowserSqlLlmDraftReq(
            natural_language="A1000 #3 IOFF만 보고싶어",
            columns=["root_lot_id", "lot_id", "wafer_id", "item_id", "value"],
        ),
        _Request("viewer", "user"),
    )

    assert out["ok"] is True
    assert out["fallback"] is True
    assert out["sql"] == "wafer_id = 3 AND root_lot_id = 'A1000' AND item_id = 'IOFF'"
    assert out["selected_columns"] == []
    assert "wafer_id" in out["sql"]
    assert "#3" not in out["sql"]


def test_filebrowser_sql_llm_draft_fallback_handles_ioff_value_threshold(monkeypatch):
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "viewer", "role": "user"})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)

    out = filebrowser.filebrowser_sql_llm_draft(
        filebrowser.FileBrowserSqlLlmDraftReq(
            natural_language="IOFF value가 0.15보다 큰거",
            columns=["item_id", "value", "wafer_id"],
        ),
        _Request("viewer", "user"),
    )

    assert out["ok"] is True
    assert out["sql"] == "item_id = 'IOFF' AND value > 0.15"
    filebrowser._run_view(
        pl.DataFrame({"item_id": ["IOFF", "IOFF", "VOFF"], "value": [0.1, 0.2, 0.3]}),
        sql=out["sql"],
        select_cols="",
        rows=20,
    )


def test_filebrowser_sql_llm_draft_fallback_handles_avg_aggregate(monkeypatch):
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "viewer", "role": "user"})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)

    out = filebrowser.filebrowser_sql_llm_draft(
        filebrowser.FileBrowserSqlLlmDraftReq(
            natural_language="IOFF value 평균 보여줘",
            columns=["item_id", "value", "wafer_id"],
        ),
        _Request("viewer", "user"),
    )

    assert out["ok"] is True
    assert out["sql"] == "item_id = 'IOFF'"
    assert out["aggregate"] == {"function": "avg", "column": "value", "group_by": [], "alias": "avg_value"}
    assert out["selected_columns"] == []


def test_filebrowser_dataframe_view_aggregate_is_read_only_preview():
    df = pl.DataFrame({
        "item_id": ["IOFF", "IOFF", "VOFF"],
        "wafer_id": [3, 4, 3],
        "value": [0.1, 0.3, 9.0],
    })

    result = filebrowser._run_view(
        df,
        sql="item_id = 'IOFF'",
        select_cols="",
        rows=20,
        aggregate_spec={"function": "avg", "column": "value"},
    )

    assert result["aggregate"] == {"function": "avg", "column": "value", "group_by": [], "alias": "avg_value"}
    assert result["columns"] == ["avg_value"]
    assert result["data"] == [{"avg_value": 0.2}]
    assert df.columns == ["item_id", "wafer_id", "value"]


def test_filebrowser_sql_feedback_persists_and_next_draft_uses_context(monkeypatch, tmp_path):
    dummy_paths = _DummyPaths(tmp_path)
    monkeypatch.setattr(filebrowser, "PATHS", dummy_paths)
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "viewer", "role": "user"})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)

    saved = filebrowser.filebrowser_sql_feedback(
        filebrowser.FileBrowserSqlFeedbackReq(
            draft_id="draft-1",
            rating="up",
            natural_language="IOFF value 큰순서",
            sql="item_id = 'IOFF'",
            sort={"column": "value", "direction": "desc", "nulls": "last"},
            columns=["item_id", "value", "wafer_id"],
        ),
        _Request("viewer", "user"),
    )

    assert saved["ok"] is True
    out = filebrowser.filebrowser_sql_llm_draft(
        filebrowser.FileBrowserSqlLlmDraftReq(
            natural_language="IOFF value 큰순서",
            columns=["item_id", "value", "wafer_id"],
        ),
        _Request("viewer", "user"),
    )

    assert out["feedback_context_used"] is True
    assert out["feedback_context"]["positive"] == 1
    assert out["sort"] == {"column": "value", "direction": "desc", "nulls": "last"}


def test_filebrowser_sql_feedback_normalizes_select_display_sql(monkeypatch, tmp_path):
    dummy_paths = _DummyPaths(tmp_path)
    monkeypatch.setattr(filebrowser, "PATHS", dummy_paths)
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "viewer", "role": "user"})

    saved = filebrowser.filebrowser_sql_feedback(
        filebrowser.FileBrowserSqlFeedbackReq(
            draft_id="draft-2",
            rating="up",
            natural_language="A1000 lot_id wafer_id만",
            sql="SELECT lot_id, wafer_id WHERE root_lot_id = A1000",
            selected_columns=["value"],
            columns=["root_lot_id", "lot_id", "wafer_id", "value"],
        ),
        _Request("viewer", "user"),
    )

    records = json.loads((dummy_paths.data_root / filebrowser.FILEBROWSER_AI_SQL_FEEDBACK_FILE).read_text("utf-8").strip())
    assert saved["where_sql"] == "root_lot_id = 'A1000'"
    assert saved["selected_columns"] == ["lot_id", "wafer_id", "value"]
    assert saved["display_sql"] == "SELECT lot_id, wafer_id, value WHERE root_lot_id = 'A1000'"
    assert records["sql"] == "root_lot_id = 'A1000'"
    assert records["display_sql"] == saved["display_sql"]


def test_filebrowser_sql_feedback_order_by_overrides_sort_payload(monkeypatch, tmp_path):
    dummy_paths = _DummyPaths(tmp_path)
    monkeypatch.setattr(filebrowser, "PATHS", dummy_paths)
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "viewer", "role": "user"})

    saved = filebrowser.filebrowser_sql_feedback(
        filebrowser.FileBrowserSqlFeedbackReq(
            draft_id="draft-3",
            rating="up",
            natural_language="feature desc",
            sql="SELECT product, feature_name WHERE product = 'A' ORDER BY feature_name DESC",
            sort={"column": "product", "direction": "asc", "nulls": "last"},
            columns=["product", "feature_name", "rule_order"],
        ),
        _Request("viewer", "user"),
    )

    assert saved["where_sql"] == "product = 'A'"
    assert saved["selected_columns"] == ["product", "feature_name"]
    assert saved["display_sql"] == "SELECT product, feature_name WHERE product = 'A' ORDER BY feature_name DESC"
    records = json.loads((dummy_paths.data_root / filebrowser.FILEBROWSER_AI_SQL_FEEDBACK_FILE).read_text("utf-8").strip())
    assert records["sort"] == {"column": "feature_name", "direction": "desc", "nulls": "last"}


def test_filebrowser_run_view_applies_explicit_sort():
    result = filebrowser._run_view(
        pl.DataFrame({"item_id": ["IOFF", "IOFF"], "value": [0.1, 0.2]}),
        sql="item_id = 'IOFF'",
        select_cols="",
        rows=20,
        sort_spec={"column": "value", "direction": "desc", "nulls": "last"},
    )

    assert [row["value"] for row in result["data"]] == [0.2, 0.1]
    assert result["sort"] == {"column": "value", "direction": "desc", "nulls": "last"}


def test_filebrowser_run_view_parses_order_by_from_display_sql():
    result = filebrowser._run_view(
        pl.DataFrame({
            "product": ["A", "A", "B"],
            "feature_name": ["b", "a", "c"],
            "rule_order": [2, 1, 3],
        }),
        sql="SELECT product, feature_name WHERE product = 'A' ORDER BY feature_name DESC",
        select_cols="",
        rows=20,
    )

    assert result["columns"] == ["product", "feature_name"]
    assert [row["feature_name"] for row in result["data"]] == ["b", "a"]
    assert result["where_sql"] == "product = 'A'"
    assert result["sort"] == {"column": "feature_name", "direction": "desc", "nulls": "last"}


def test_filebrowser_download_lazy_csv_parses_order_by_from_display_sql():
    df, _csv_bytes = filebrowser._download_lazy_csv(
        pl.DataFrame({"lot_id": ["B", "A"], "rank": [2, 1]}).lazy(),
        sql="SELECT lot_id ORDER BY rank ASC",
        select_cols="",
        max_rows=20,
        max_bytes=100000,
    )

    assert df["lot_id"].to_list() == ["A", "B"]
    assert df.columns == ["lot_id"]


@pytest.mark.parametrize("bad_sql", [
    "SELECT product WHERE product = 'A' ORDER BY missing_col ASC",
    "SELECT product ORDER BY lower(product) ASC",
    "SELECT product ORDER BY product ASC; DROP TABLE source",
])
def test_filebrowser_order_by_rejects_unsupported_sql(bad_sql):
    with pytest.raises(ValueError):
        filebrowser._parse_ai_sql_display_sql(bad_sql, ["product", "feature_name"])


def test_filebrowser_cache_refresh_requires_admin(monkeypatch):
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "viewer", "role": "user"})

    with pytest.raises(HTTPException) as exc:
        filebrowser.cache_match_refresh(filebrowser.CacheMatchRefreshReq(target="fab"), _Request("viewer", "user"))

    assert exc.value.status_code == 403


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


@pytest.mark.parametrize("categorical_first", [False, True])
def test_lazy_product_view_allows_categorical_string_partition_mismatch(monkeypatch, tmp_path, categorical_first):
    monkeypatch.setattr(utils, "PATHS", _DummyPaths(tmp_path))
    product_dir = tmp_path / "ROOT" / "PRODA"
    product_dir.mkdir(parents=True)

    parts = [
        ("A1000", "Y", categorical_first),
        ("A1001", "N", not categorical_first),
    ]
    for idx, (lot_id, is_met, as_categorical) in enumerate(parts):
        df = pl.DataFrame({"lot_id": [lot_id], "is_met": [is_met], "value": [idx + 1]})
        if as_categorical:
            df = df.with_columns(pl.col("is_met").cast(pl.Categorical))
        df.write_parquet(product_dir / f"part_{idx}.parquet")

    lf = utils.lazy_read_source(root="ROOT", product="PRODA", recent_days=None, max_files=None)
    assert lf is not None

    result = filebrowser._run_view_lazy(
        lf,
        sql="is_met = 'Y'",
        select_cols="lot_id,is_met",
        rows=20,
        page=0,
        page_size=20,
        preview_cols=5,
    )

    assert result["dtypes"]["is_met"] == "String"
    assert result["data"] == [{"lot_id": "A1000", "is_met": "Y"}]


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


def test_filebrowser_lazy_view_filters_string_wafer_id_with_numeric_sql():
    lf = pl.DataFrame({
        "root_lot_id": ["LOT"] * 4,
        "wafer_id": ["1", "3", "10", "25"],
        "value": [1, 3, 10, 25],
    }).lazy()

    result = filebrowser._run_view_lazy(
        lf,
        sql="wafer_id >= 3",
        select_cols="wafer_id,value",
        rows=20,
        page=0,
        page_size=20,
        preview_cols=5,
    )

    assert result["data"] == [
        {"wafer_id": "3", "value": 3},
        {"wafer_id": "10", "value": 10},
        {"wafer_id": "25", "value": 25},
    ]


def test_filebrowser_dataframe_view_filters_string_wafer_id_with_numeric_sql():
    df = pl.DataFrame({
        "root_lot_id": ["LOT"] * 4,
        "wafer_id": ["1", "3", "10", "25"],
        "value": [1, 3, 10, 25],
    })

    result = filebrowser._run_view(df, sql="wafer_id = 3", select_cols="wafer_id,value", rows=20)

    assert result["data"] == [{"wafer_id": "3", "value": 3}]


def test_filebrowser_manual_sql_accepts_root_lot_id_alias_and_bare_value():
    df = pl.DataFrame({
        "root_lot_id": ["A1000", "B1000"],
        "wafer_id": ["1", "2"],
        "value": [10, 20],
    })

    result = filebrowser._run_view(df, sql="root lot id = A1000", select_cols="root_lot_id,value", rows=20)

    assert filebrowser._validate_where_expression(
        "root lot id = A1000",
        ["root_lot_id", "wafer_id", "value"],
    ) == "root_lot_id = 'A1000'"
    assert result["data"] == [{"root_lot_id": "A1000", "value": 10}]


def test_filebrowser_dataframe_view_accepts_select_prefix_projection():
    df = pl.DataFrame({
        "root_lot_id": ["A1000", "B1000"],
        "wafer_id": ["1", "2"],
        "value": [10, 20],
        "hidden": ["x", "y"],
    })

    result = filebrowser._run_view(
        df,
        sql="SELECT wafer_id, value WHERE root_lot_id = A1000",
        select_cols="",
        rows=20,
    )

    assert result["selected_cols"] == "wafer_id,value"
    assert result["columns"] == ["wafer_id", "value"]
    assert result["data"] == [{"wafer_id": "1", "value": 10}]


def test_filebrowser_lazy_view_accepts_root_lot_id_alias_and_bare_value():
    lf = pl.DataFrame({
        "root_lot_id": ["A1000", "B1000"],
        "wafer_id": ["1", "2"],
        "value": [10, 20],
    }).lazy()

    result = filebrowser._run_view_lazy(
        lf,
        sql="root lot id = A1000",
        select_cols="root_lot_id,value",
        rows=20,
        page=0,
        page_size=20,
        preview_cols=5,
    )

    assert result["data"] == [{"root_lot_id": "A1000", "value": 10}]


def test_filebrowser_lazy_view_accepts_select_prefix_projection():
    lf = pl.DataFrame({
        "root_lot_id": ["A1000", "B1000"],
        "wafer_id": ["1", "2"],
        "value": [10, 20],
        "hidden": ["x", "y"],
    }).lazy()

    result = filebrowser._run_view_lazy(
        lf,
        sql="SELECT wafer_id, value WHERE root_lot_id = A1000",
        select_cols="",
        rows=20,
        page=0,
        page_size=20,
        preview_cols=5,
    )

    assert result["selected_cols"] == "wafer_id,value"
    assert result["columns"] == ["wafer_id", "value"]
    assert result["data"] == [{"wafer_id": "1", "value": 10}]


def test_filebrowser_lazy_csv_download_accepts_root_lot_id_alias_and_bare_value():
    lf = pl.DataFrame({
        "root_lot_id": ["A1000", "B1000"],
        "wafer_id": ["1", "2"],
        "value": [10, 20],
    }).lazy()

    df, csv_bytes = filebrowser._download_lazy_csv(
        lf,
        "root lot id = A1000",
        "root_lot_id,value",
        20,
    )

    assert df.to_dicts() == [{"root_lot_id": "A1000", "value": 10}]
    assert b"A1000" in csv_bytes


def test_filebrowser_lazy_csv_download_accepts_select_prefix_projection():
    lf = pl.DataFrame({
        "root_lot_id": ["A1000", "B1000"],
        "wafer_id": ["1", "2"],
        "value": [10, 20],
        "hidden": ["x", "y"],
    }).lazy()

    df, csv_bytes = filebrowser._download_lazy_csv(
        lf,
        "SELECT wafer_id, value WHERE root_lot_id = A1000",
        "",
        20,
    )

    assert df.to_dicts() == [{"wafer_id": "1", "value": 10}]
    assert b"hidden" not in csv_bytes


def test_filebrowser_duckdb_view_accepts_root_lot_id_alias_and_bare_value(tmp_path):
    if not duckdb_engine.is_available():
        pytest.skip("duckdb is not installed")
    fp = tmp_path / "source.parquet"
    pl.DataFrame({
        "root_lot_id": ["A1000", "B1000"],
        "wafer_id": ["1", "2"],
        "value": [10, 20],
    }).write_parquet(fp)

    result = filebrowser._run_view_duckdb(
        [fp],
        "root lot id = A1000",
        "root_lot_id,value",
        20,
    )

    assert result["data"] == [{"root_lot_id": "A1000", "value": 10}]


def test_filebrowser_duckdb_view_accepts_select_prefix_projection(tmp_path):
    if not duckdb_engine.is_available():
        pytest.skip("duckdb is not installed")
    fp = tmp_path / "source.parquet"
    pl.DataFrame({
        "root_lot_id": ["A1000", "B1000"],
        "wafer_id": ["1", "2"],
        "value": [10, 20],
        "hidden": ["x", "y"],
    }).write_parquet(fp)

    result = filebrowser._run_view_duckdb(
        [fp],
        "SELECT wafer_id, value WHERE root_lot_id = A1000",
        "",
        20,
    )

    assert result["selected_cols"] == "wafer_id,value"
    assert result["columns"] == ["wafer_id", "value"]
    assert result["data"] == [{"wafer_id": "1", "value": 10}]


def test_filebrowser_wafer_sql_normalizer_handles_in_and_prefixed_literals():
    df = pl.DataFrame({
        "root_lot_id": ["LOT"] * 4,
        "wafer_id": ["1", "3", "10", "25"],
        "value": [1, 3, 10, 25],
    })

    result = filebrowser._run_view(df, sql="wafer_id IN ('WF03', 10)", select_cols="wafer_id,value", rows=20)

    assert result["data"] == [
        {"wafer_id": "3", "value": 3},
        {"wafer_id": "10", "value": 10},
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


def test_base_file_meta_only_truncates_very_wide_schema_and_column_search(monkeypatch, tmp_path):
    fp = tmp_path / "ML_TABLE_WIDE.parquet"
    pl.DataFrame({f"c{i:04d}": [i] for i in range(4999)} | {"needle_4999": [4999]}).write_parquet(fp)

    dummy_paths = _DummyPaths(tmp_path)
    monkeypatch.setattr(filebrowser, "PATHS", dummy_paths)

    result = filebrowser.base_file_view(
        file=fp.name,
        rows=200,
        cols=100,
        meta_only=True,
        page=0,
        page_size=200,
    )
    found = filebrowser.search_columns(
        _Request("viewer", "user"),
        file=fp.name,
        q="needle",
        limit=10,
    )

    assert result["meta_only"] is True
    assert result["data"] == []
    assert result["total_cols"] == 5000
    assert result["all_columns_truncated"] is True
    assert len(result["all_columns"]) == filebrowser.DEFAULT_SCHEMA_COLUMN_PAGE_SIZE
    assert result["row_count_unknown"] is True
    assert found["columns"] == ["needle_4999"]
    assert found["total_cols"] == 5000


def test_large_source_download_requires_filter_or_projection():
    lf = pl.DataFrame({"lot_id": ["A1000"], "value": [1]}).lazy()

    with pytest.raises(HTTPException) as exc:
        filebrowser._download_lazy_csv(
            lf,
            "",
            "",
            10,
            source_size=101,
            settings={"sql_query_max_source_bytes": 100, "csv_download_max_bytes": 1000000},
        )

    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "filter_required"


def test_download_stops_by_byte_limit():
    lf = pl.DataFrame({"payload": ["x" * 50 for _ in range(3)]}).lazy()

    with pytest.raises(HTTPException) as exc:
        filebrowser._download_lazy_csv(
            lf,
            "",
            "payload",
            10,
            max_bytes=20,
            settings={"csv_download_max_bytes": 20},
        )

    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "download_too_large"
    assert exc.value.detail["max_bytes"] == 20


def test_wide_download_requires_selected_columns():
    lf = pl.DataFrame({f"c{i:03d}": [i] for i in range(filebrowser.MAX_CSV_DOWNLOAD_AUTO_COLUMNS + 1)}).lazy()

    with pytest.raises(HTTPException) as exc:
        filebrowser._download_lazy_csv(lf, "", "", 10)

    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "too_many_columns_without_projection"


def test_base_files_hides_legacy_cache_and_cleanup_api_deletes_candidates(monkeypatch, tmp_path):
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
        cache_dir / "et_lot_step_seq_summary.parquet",
        cache_dir / "et_lot_step_seq_summary.json",
        cache_dir / "inline_lot_item_summary.parquet",
        cache_dir / "vm_lot_model_summary.parquet",
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
    assert nested.exists()
    for legacy in legacy_files:
        assert legacy.exists()
    candidates = filebrowser.cache_cleanup_candidates(_Request("admin", "admin"))["candidates"]
    relpaths = {row["relpath"] for row in candidates}
    assert "cache/ML_TABLE_PRODA.parquet.latest_step_by_lot.parquet" in relpaths
    assert "cache/lot_progress_latest_lot_by_root_wafer.parquet" not in relpaths
    cleaned = filebrowser.cache_cleanup(
        filebrowser.CacheCleanupReq(paths=[row["path"] for row in candidates]),
        _Request("admin", "admin"),
    )
    assert cleaned["ok"] is True
    assert len(cleaned["deleted"]) == len(candidates)
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


def test_single_file_step_cache_fills_product_from_ml_table_name(monkeypatch, tmp_path):
    fp = tmp_path / "ML_TABLE_PRODA.parquet"
    pl.DataFrame({
        "lot_id": ["L1000", "L1000"],
        "step_id": ["STEP_010", "STEP_020"],
        "time": ["2026-04-28T08:00:00", "2026-04-28T09:00:00"],
    }).write_parquet(fp)

    out = filebrowser._build_single_file_step_cache(fp, force=True)
    df = pl.read_parquet(tmp_path / "cache" / f"{fp.name}.latest_step_by_lot.parquet")
    meta = json.loads((tmp_path / "cache" / f"{fp.name}.latest_step_by_lot.meta.json").read_text("utf-8"))

    assert out["ok"] is True
    assert df.to_dicts()[0]["product"] == "PRODA"
    assert meta["product_col"] == ""
    assert meta["rows"] == 1


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


def test_cache_folder_exposes_only_lot_progress_latest_cache(monkeypatch, tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    csv_fp = cache_dir / "small_cache.csv"
    csv_fp.write_text("id,value\n1,a\n2,b\n", encoding="utf-8")
    pq_fp = cache_dir / "wide_cache.parquet"
    pl.DataFrame({"id": list(range(250)), "value": [f"v{i}" for i in range(250)]}).write_parquet(pq_fp)
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

    dummy_paths = _DummyPaths(tmp_path)
    monkeypatch.setattr(filebrowser, "PATHS", dummy_paths)
    filebrowser._LIST_CACHE.clear()

    listed = filebrowser.base_files()
    paths = {row["path"]: row for row in listed["files"]}

    assert "cache/lot_progress_latest_lot_by_root_wafer.parquet" in paths
    assert "cache/small_cache.csv" not in paths
    assert "cache/wide_cache.parquet" not in paths
    assert csv_fp.exists()
    assert pq_fp.exists()
    candidates = filebrowser.cache_cleanup_candidates(_Request("admin", "admin"))["candidates"]
    relpaths = {row["relpath"] for row in candidates}
    assert {"cache/small_cache.csv", "cache/wide_cache.parquet"}.issubset(relpaths)
    cleaned = filebrowser.cache_cleanup(
        filebrowser.CacheCleanupReq(paths=["cache/small_cache.csv", "cache/wide_cache.parquet"]),
        _Request("admin", "admin"),
    )
    assert cleaned["ok"] is True
    assert not csv_fp.exists()
    assert not pq_fp.exists()

    parquet_preview = filebrowser.base_file_view(
        file="cache/lot_progress_latest_lot_by_root_wafer.parquet",
        sql="",
        rows=200,
        cols=10,
        select_cols="",
        engine="auto",
        meta_only=False,
        page=0,
        page_size=200,
    )
    assert parquet_preview["showing"] == 1
    assert parquet_preview.get("single_file_full_read") is not True
    assert parquet_preview["has_more"] is False
    assert parquet_preview["preview_row_limit"] == 100

    versions = filebrowser.base_file_versions(_Request("admin", "admin"), file="cache/lot_progress_latest_lot_by_root_wafer.parquet")
    assert versions["versioned"] is False
    assert versions["versions"] == []


def test_cache_csv_and_parquet_first_preview_are_100_row_samples(monkeypatch, tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    csv_fp = cache_dir / "small_cache.csv"
    csv_fp.write_text("id,value\n" + "\n".join(f"{i},v{i}" for i in range(250)) + "\n", encoding="utf-8")
    parquet_fp = cache_dir / "lot_progress_latest_lot_by_root_wafer.parquet"
    pl.DataFrame({
        "id": list(range(250)),
        "value": [f"v{i}" for i in range(250)],
    }).write_parquet(parquet_fp)

    dummy_paths = _DummyPaths(tmp_path)
    monkeypatch.setattr(filebrowser, "PATHS", dummy_paths)
    filebrowser._LIST_CACHE.clear()

    csv_preview = filebrowser.base_file_view(
        file="cache/small_cache.csv",
        sql="",
        rows=200,
        cols=10,
        select_cols="",
        engine="auto",
        meta_only=False,
        page=0,
        page_size=200,
    )
    parquet_preview = filebrowser.base_file_view(
        file="cache/lot_progress_latest_lot_by_root_wafer.parquet",
        sql="",
        rows=200,
        cols=10,
        select_cols="",
        engine="auto",
        meta_only=False,
        page=0,
        page_size=200,
    )

    assert csv_preview.get("single_file_full_read") is not True
    assert csv_preview["showing"] == 100
    assert csv_preview["preview_row_limit"] == 100
    assert parquet_preview.get("single_file_full_read") is not True
    assert parquet_preview["showing"] == 100
    assert parquet_preview["preview_row_limit"] == 100


def test_admin_configured_folder_can_be_versioned_single_file(monkeypatch, tmp_path):
    secret_dir = tmp_path / "secret"
    secret_dir.mkdir()
    fp = secret_dir / "lookup.csv"
    fp.write_text("id,value\n1,a\n", encoding="utf-8")

    dummy_paths = _DummyPaths(tmp_path)
    monkeypatch.setattr(filebrowser, "PATHS", dummy_paths)
    filebrowser._LIST_CACHE.clear()
    filebrowser._save_filebrowser_settings({
        "csv_full_read_max_bytes": 10485760,
        "csv_rules": {},
        "hidden_db_dirs": ["cache", "secret"],
        "versioned_single_file_dirs": ["secret", "cache"],
    })

    listed = filebrowser.base_files()
    rows = [row for row in listed["files"] if row.get("path") == "secret/lookup.csv"]

    assert rows and rows[0]["editable"] is True
    assert rows[0]["versioned"] is True
    versions = filebrowser.base_file_versions(_Request("admin", "admin"), file="secret/lookup.csv")
    assert versions["versioned"] is True
    assert "cache" not in filebrowser._load_filebrowser_settings()["versioned_single_file_dirs"]


def test_default_reformatter_folder_versions_json_and_csv(monkeypatch, tmp_path):
    reformatter_dir = tmp_path / "reformatter"
    reformatter_dir.mkdir()
    (reformatter_dir / "PRODA0.json").write_text('{"rules": []}\n', encoding="utf-8")
    (reformatter_dir / "PRODA0.csv").write_text("item,rank\nb,2\na,1\n", encoding="utf-8")

    dummy_paths = _DummyPaths(tmp_path)
    monkeypatch.setattr(filebrowser, "PATHS", dummy_paths)
    filebrowser._LIST_CACHE.clear()

    listed = filebrowser.base_files()
    paths = {row["path"]: row for row in listed["files"]}

    assert paths["reformatter/PRODA0.json"]["versioned"] is True
    assert paths["reformatter/PRODA0.json"]["editable"] is True
    assert paths["reformatter/PRODA0.csv"]["versioned"] is True
    assert paths["reformatter/PRODA0.csv"]["editable"] is True

    json_preview = filebrowser.base_file_view(file="reformatter/PRODA0.json")
    csv_preview = filebrowser.base_file_view(file="reformatter/PRODA0.csv")
    json_versions = filebrowser.base_file_versions(_Request("admin", "admin"), file="reformatter/PRODA0.json")
    csv_versions = filebrowser.base_file_versions(_Request("admin", "admin"), file="reformatter/PRODA0.csv")

    assert json_preview["kind"] == "json"
    assert csv_preview["kind"] == "table"
    assert json_versions["versioned"] is True
    assert csv_versions["versioned"] is True


def test_reformatter_csv_save_applies_file_rule_and_versions_under_flow_data(monkeypatch, tmp_path):
    reformatter_dir = tmp_path / "reformatter"
    reformatter_dir.mkdir()
    fp = reformatter_dir / "PRODA0.csv"
    fp.write_text("item,rank\nb,2\na,1\n", encoding="utf-8")

    dummy_paths = _DummyPaths(tmp_path)
    monkeypatch.setattr(filebrowser, "PATHS", dummy_paths)
    monkeypatch.setattr(filebrowser, "BASE_VERSION_DIR", tmp_path / "file_versions")
    monkeypatch.setattr(filebrowser._s3, "sync_saved_path", lambda *_args, **_kwargs: {"ok": True, "skipped": True})
    filebrowser._save_filebrowser_settings({
        "csv_full_read_max_bytes": 10485760,
        "hidden_db_dirs": ["cache", "reformatter"],
        "versioned_single_file_dirs": ["reformatter"],
        "csv_rules": {
            "reformatter/PRODA0.csv": {
                "required_columns": ["item", "rank"],
                "sort": [{"column": "rank", "direction": "asc", "type": "numeric", "nulls": "last"}],
            }
        },
    })

    saved = filebrowser._save_base_file(
        filebrowser.BaseFileSaveReq(
            file="reformatter/PRODA0.csv",
            csv_text="item,rank\nb,2\na,1\n",
            delimiter="comma",
            include_header=True,
            note="update reformatter csv",
        ),
        _Request("admin", "admin"),
    )

    rows = list(csv.DictReader(fp.open(encoding="utf-8")))
    version_dir = tmp_path / "file_versions" / "reformatter__PRODA0.csv"

    assert saved["csv_validation"]["sorted"] is True
    assert saved["version"]["file"] == "reformatter/PRODA0.csv"
    assert [row["item"] for row in rows] == ["a", "b"]
    assert (version_dir / "v1.csv").is_file()
    assert (version_dir / "v1.meta.json").is_file()


def test_base_file_save_reports_added_rows_in_version_diff(monkeypatch, tmp_path):
    reformatter_dir = tmp_path / "reformatter"
    reformatter_dir.mkdir()
    fp = reformatter_dir / "PRODA0.csv"
    fp.write_text("item,rank\na,1\n", encoding="utf-8")

    dummy_paths = _DummyPaths(tmp_path)
    monkeypatch.setattr(filebrowser, "PATHS", dummy_paths)
    monkeypatch.setattr(filebrowser, "BASE_VERSION_DIR", tmp_path / "file_versions")
    monkeypatch.setattr(filebrowser._s3, "sync_saved_path", lambda *_args, **_kwargs: {"ok": True, "skipped": True})
    filebrowser._save_filebrowser_settings({
        "csv_full_read_max_bytes": 10485760,
        "hidden_db_dirs": ["cache", "reformatter"],
        "versioned_single_file_dirs": ["reformatter"],
        "csv_rules": {},
    })

    saved = filebrowser._save_base_file(
        filebrowser.BaseFileSaveReq(
            file="reformatter/PRODA0.csv",
            csv_text="item,rank\na,1\nb,2\n",
            delimiter="comma",
            include_header=True,
            note="append row",
        ),
        _Request("admin", "admin"),
    )
    versions = filebrowser.base_file_versions(_Request("admin", "admin"), file="reformatter/PRODA0.csv")
    preview = filebrowser.base_file_version_content(
        _Request("admin", "admin"),
        file="reformatter/PRODA0.csv",
        version=versions["versions"][0]["version"],
    )

    assert saved["version"]["change_summary"]["added_rows"] == 1
    assert versions["versions"][0]["change_summary"]["label"] == "추가 1행"
    assert preview["diff_table"]["counts"]["added"] == 1


def test_version_diff_preserves_duplicate_inferred_key_additions(tmp_path):
    previous = tmp_path / "v1.csv"
    current = tmp_path / "current.csv"
    previous.write_text("product,item_id,value\nPRODA,ITEM_A,base\n", encoding="utf-8")
    current.write_text(
        "product,item_id,value\n"
        "PRODA,ITEM_A,base\n"
        + "".join(f"PRODA,ITEM_A,new_{idx}\n" for idx in range(6)),
        encoding="utf-8",
    )

    diff = filebrowser._diff_table_between(current, previous)
    summary = filebrowser._snapshot_change_summary(current, previous)

    assert diff["match_strategy"] == "sequence"
    assert diff["counts"]["added"] == 6
    assert diff["counts"]["modified"] == 0
    assert diff["counts"]["deleted"] == 0
    assert summary["added_rows"] == 6


def test_version_diff_counts_modified_deleted_and_added_rows(tmp_path):
    previous = tmp_path / "v1.csv"
    current = tmp_path / "current.csv"
    previous.write_text(
        "id,name,value\n"
        + "".join(f"{idx},row_{idx},old_{idx}\n" for idx in range(1, 11)),
        encoding="utf-8",
    )
    current_rows = []
    for idx in range(1, 9):
        value = f"new_{idx}" if idx in {2, 5, 7} else f"old_{idx}"
        current_rows.append(f"{idx},row_{idx},{value}\n")
    current_rows.extend(f"{idx},row_{idx},old_{idx}\n" for idx in range(11, 15))
    current.write_text("id,name,value\n" + "".join(current_rows), encoding="utf-8")

    diff = filebrowser._diff_table_between(current, previous)

    assert diff["key_columns"] == ["id"]
    assert diff["counts"]["modified"] == 3
    assert diff["counts"]["deleted"] == 2
    assert diff["counts"]["added"] == 4
    modified = [row for row in diff["rows"] if row["rev"] == "수정"]
    assert len(modified) == 3
    assert all(row["_changed_cols"] == ["value"] for row in modified)


def test_version_diff_prefers_configured_csv_unique_keys(monkeypatch, tmp_path):
    previous = tmp_path / "v1.csv"
    current = tmp_path / "rules.csv"
    previous.write_text("product,step,value\nPRODA,S1,10\nPRODA,S2,20\n", encoding="utf-8")
    current.write_text("product,step,value\nPRODA,S1,11\nPRODA,S3,30\n", encoding="utf-8")
    monkeypatch.setattr(filebrowser, "PATHS", _DummyPaths(tmp_path))
    filebrowser._save_filebrowser_settings({
        "csv_full_read_max_bytes": 10485760,
        "csv_rules": {"rules.csv": {"unique_keys": [["product", "step"]]}},
    })

    diff = filebrowser._diff_table_between(current, previous, file="rules.csv")

    assert diff["key_columns"] == ["product", "step"]
    assert diff["counts"]["modified"] == 1
    assert diff["counts"]["deleted"] == 1
    assert diff["counts"]["added"] == 1
    assert [row["changed_cols"] for row in diff["rows"] if row["rev"] == "수정"] == ["value"]


def test_version_diff_reads_wafer_id_rows_without_filtering(tmp_path):
    previous = tmp_path / "v1.csv"
    current = tmp_path / "current.csv"
    previous.write_text("id,wafer_id,value\n1,not_a_wafer,old\n", encoding="utf-8")
    current.write_text("id,wafer_id,value\n1,not_a_wafer,new\n", encoding="utf-8")

    diff = filebrowser._diff_table_between(current, previous)

    assert diff["counts"]["modified"] == 1
    assert diff["rows"][0]["wafer_id"] == "not_a_wafer"
    assert diff["rows"][0]["_changed_cols"] == ["value"]


def test_base_file_versioned_allows_any_single_csv_under_5mb(tmp_path):
    fp = tmp_path / "custom_lookup.csv"
    fp.write_text("a,b\n1,2\n", encoding="utf-8")

    assert filebrowser._base_file_versioned(fp.name, fp) is True


def test_base_file_versioned_rejects_single_csv_over_5mb(tmp_path):
    fp = tmp_path / "large_lookup.csv"
    fp.write_bytes(b"x" * (filebrowser.EDM_VERSION_MAX_CSV_BYTES + 1))

    assert filebrowser._base_file_versioned(fp.name, fp) is False


def test_base_file_view_parquet_defaults_to_100_row_preview(monkeypatch, tmp_path):
    fp = tmp_path / "matching_step.parquet"
    pl.DataFrame({f"c{i:02d}": [i + row for row in range(250)] for i in range(12)}).write_parquet(fp)

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

    assert result.get("single_file_full_read") is not True
    assert result["showing"] == 100
    assert result["has_more"] is False
    assert result["preview_row_limit"] == 100
    assert len(result["showing_cols"]) == 10
    assert result["truncated_cols"] is True


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
    assert paged["showing"] == 100
    assert paged["has_more"] is False
    assert paged["preview_row_limit"] == 100


def test_base_file_view_ml_table_defaults_to_100_then_filtered_preview(monkeypatch, tmp_path):
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
    assert preview["showing"] == 100
    assert preview["has_more"] is False
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
    assert selected.get("single_file_full_read") is not True
    assert selected["showing"] == 100
    assert selected["has_more"] is False
    assert selected["columns"] == ["value"]
    assert selected["preview_row_limit"] == 100
    assert selected["data"][-1] == {"value": 99}


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
            csv_download_max_rows=12345,
            csv_rules={"rules.csv": {"required_columns": ["id"]}},
        ),
        _Request("fb_mgr", "user"),
    )

    assert saved["ok"] is True
    assert saved["csv_full_read_max_bytes"] == 2048
    assert saved["csv_download_max_rows"] == 12345
    assert saved["csv_rules"]["rules.csv"]["required_columns"] == ["id"]


def test_setup_preserves_filebrowser_settings_file():
    setup_text = (ROOT / "setup.py").read_text(encoding="utf-8")
    build_text = (ROOT / "_build_setup.py").read_text(encoding="utf-8")

    assert "'filebrowser_settings.json'" in setup_text
    assert "'filebrowser_settings.json'" in build_text


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


def test_roots_fast_mode_uses_estimated_counts(monkeypatch, tmp_path):
    dummy_paths = _DummyPaths(tmp_path)
    monkeypatch.setattr(filebrowser, "PATHS", dummy_paths)
    filebrowser._LIST_CACHE.clear()
    target = tmp_path / "VISIBLE_DB" / "product=A"
    target.mkdir(parents=True)
    pl.DataFrame({"value": [1]}).write_parquet(target / "part1.parquet")
    pl.DataFrame({"value": [2]}).write_parquet(target / "part2.parquet")

    rows = filebrowser.list_roots(all=False, fast=True)["roots"]

    assert rows[0]["name"] == "VISIBLE_DB"
    assert rows[0]["parquet_count"] == 1
    assert rows[0]["parquet_count_estimated"] is True


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


def test_csv_conditions_are_and_pass_conditions():
    rule = filebrowser._normalize_csv_rule({
        "conditions": [
            {"expr": "status != 'BAD'", "message": "BAD status is blocked"},
            {"expr": "end_time >= start_time", "message": "end must be after start"},
        ],
    })

    ok = filebrowser._validate_csv_rule(
        ["status", "start_time", "end_time"],
        [
            ["OK", "2026-01-01", "2026-01-02"],
            ["HOLD", "2026-01-03", "2026-01-03"],
        ],
        rule,
    )
    failed = filebrowser._validate_csv_rule(
        ["status", "start_time", "end_time"],
        [
            ["BAD", "2026-01-01", "2026-01-02"],
            ["OK", "2026-01-03", "2026-01-02"],
        ],
        rule,
    )

    assert ok["ok"] is True
    assert failed["ok"] is False
    assert [err["row"] for err in failed["errors"] if err["rule"] == "conditions"] == [1, 2]


def test_csv_ordered_by_validates_product_feature_and_rule_order():
    rule = filebrowser._normalize_csv_rule({
        "ordered_by": {
            "keys": [
                {"column": "product", "type": "string"},
                {"column": "feature_name", "type": "leading_number"},
                {"column": "rule_order", "type": "rule_order"},
            ]
        }
    })
    header = ["product", "feature_name", "rule_order"]
    ok_rows = [
        ["PRODA", "2.0 WELL", "RO"],
        ["PRODA", "5.0 PC", "R1"],
        ["PRODA", "5.0 PC", "R2"],
        ["PRODA", "5.0 PC", "RO"],
        ["PRODB", "1.0 STI", "RO"],
    ]

    product_bad = filebrowser._validate_csv_rule(header, [["PRODB", "1.0 STI", "RO"], ["PRODA", "1.0 STI", "RO"]], rule)
    feature_bad = filebrowser._validate_csv_rule(header, [["PRODA", "10.0 CONTACT", "RO"], ["PRODA", "2.0 WELL", "RO"]], rule)
    rule_bad = filebrowser._validate_csv_rule(header, [["PRODA", "5.0 PC", "RO"], ["PRODA", "5.0 PC", "R1"]], rule)

    assert filebrowser._validate_csv_rule(header, ok_rows, rule)["ok"] is True
    assert product_bad["errors"][0]["rule"] == "ordered_by"
    assert feature_bad["errors"][0]["rule"] == "ordered_by"
    assert rule_bad["errors"][0]["rule"] == "ordered_by"


def test_csv_sort_supports_product_feature_number_and_rule_order():
    rule = filebrowser._normalize_csv_rule({
        "sort": [
            {"column": "product", "type": "string"},
            {"column": "feature_name", "type": "leading_number"},
            {"column": "rule_order", "type": "rule_order"},
        ]
    })
    header = ["product", "feature_name", "rule_order"]
    rows = [
        ["PRODB", "1.0 STI", "RO"],
        ["PRODA", "10.0 CONTACT", "RO"],
        ["PRODA", "5.0 PC", "RO"],
        ["PRODA", "5.0 PC", "R2"],
        ["PRODA", "5.0 PC", "R1"],
    ]

    sorted_rows = filebrowser._apply_csv_sort_rule(header, rows, rule)

    assert sorted_rows == [
        ["PRODA", "5.0 PC", "R1"],
        ["PRODA", "5.0 PC", "R2"],
        ["PRODA", "5.0 PC", "RO"],
        ["PRODA", "10.0 CONTACT", "RO"],
        ["PRODB", "1.0 STI", "RO"],
    ]


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


def test_download_duckdb_csv_handles_partition_dtype_mismatch(tmp_path):
    pytest.importorskip("duckdb")
    files = [tmp_path / "part_a.parquet", tmp_path / "part_b.parquet"]
    pl.DataFrame({"lot_id": ["A1000"], "mixed": [1]}).write_parquet(files[0])
    pl.DataFrame({"lot_id": ["A1001"], "mixed": ["two"]}).write_parquet(files[1])

    with pytest.raises(Exception) as exc:
        filebrowser._download_lazy_csv(pl.scan_parquet([str(fp) for fp in files]), "", "", 10)
    assert filebrowser._is_dtype_mismatch_error(exc.value)

    df, csv_bytes = filebrowser._download_duckdb_csv(files, "", "", 10)

    rows = sorted(df.to_dicts(), key=lambda row: row["lot_id"])
    assert rows == [
        {"lot_id": "A1000", "mixed": "1"},
        {"lot_id": "A1001", "mixed": "two"},
    ]
    assert b"mixed" in csv_bytes
    assert b"two" in csv_bytes


def test_download_duckdb_csv_accepts_select_prefix_projection(tmp_path):
    pytest.importorskip("duckdb")
    fp = tmp_path / "source.parquet"
    pl.DataFrame({
        "root_lot_id": ["A1000", "B1000"],
        "wafer_id": ["1", "2"],
        "value": [10, 20],
        "hidden": ["x", "y"],
    }).write_parquet(fp)

    df, csv_bytes = filebrowser._download_duckdb_csv(
        [fp],
        "SELECT wafer_id, value WHERE root_lot_id = A1000",
        "",
        20,
    )

    assert df.to_dicts() == [{"wafer_id": "1", "value": 10}]
    assert b"hidden" not in csv_bytes


def test_download_csv_falls_back_to_duckdb_for_dtype_mismatch(monkeypatch, tmp_path):
    pytest.importorskip("duckdb")
    dummy_paths = _DummyPaths(tmp_path)
    monkeypatch.setattr(filebrowser, "PATHS", dummy_paths)
    monkeypatch.setattr(filebrowser, "DL_LOG", dummy_paths.download_log)
    monkeypatch.setattr(utils, "PATHS", dummy_paths)
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "viewer", "role": "user"})

    product_dir = tmp_path / "ROOT" / "PRODA"
    product_dir.mkdir(parents=True)
    pl.DataFrame({"lot_id": ["A1000"], "mixed": [1]}).write_parquet(product_dir / "part_a.parquet")
    pl.DataFrame({"lot_id": ["A1001"], "mixed": ["two"]}).write_parquet(product_dir / "part_b.parquet")

    response = filebrowser.download_csv(
        _Request("viewer", "user"),
        root="ROOT",
        product="PRODA",
        file="",
        sql="",
        select_cols="",
        apply_reformatter=True,
        max_rows=10,
    )

    async def read_body():
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)
        return b"".join(chunks)

    body = asyncio.run(read_body())
    assert response.media_type == "text/csv; charset=utf-8"
    assert b"mixed" in body
    assert b"two" in body


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
