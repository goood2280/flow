"""Regression coverage for change-driven cache recovery boundaries."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest


def _write_fab(root: Path) -> None:
    path = root / "1.RAWDATA_DB_FAB" / "PRODA" / "date=20260901" / "part_0.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "root_lot_id": ["A1000", "A1000", "A1001"],
        "lot_id": ["A1000.1", "A1000.1", "A1001.1"],
        "wafer_id": ["1", "1", "2"],
        "step_id": ["S10", "S20", "S10"],
        "tkout_time": [
            "2026-09-01 01:00:00",
            "2026-09-01 02:00:00",
            "2026-09-01 03:00:00",
        ],
    }).write_parquet(path)


@pytest.fixture
def original_wip_root_reader():
    from core import lot_progress_cache
    return lot_progress_cache._ml_table_root_lot_ids


@pytest.fixture
def wip_recovery_env(monkeypatch, tmp_path, original_wip_root_reader):
    from core import lot_progress_cache as lpc
    from core import paths

    _write_fab(tmp_path)
    cache_dir = tmp_path / "cache"
    state_paths = {
        "json": cache_dir / "lot_wf_current.json",
        "parquet": cache_dir / "lot_wf_current.parquet",
        "tracker": tmp_path / "tracker" / "lot_status_cache.json",
        "lock": tmp_path / "locks" / "lot_progress_cache.lock",
        "log": tmp_path / "logs" / "lot_progress_cache_refresh.jsonl",
    }
    for path in state_paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(paths, "_get_db_root", lambda: tmp_path)
    monkeypatch.setattr(paths, "_get_base_root", lambda: tmp_path)
    monkeypatch.setattr(lpc, "cache_file", lambda: state_paths["json"])
    monkeypatch.setattr(lpc, "cache_parquet_file", lambda: state_paths["parquet"])
    monkeypatch.setattr(lpc, "lot_status_cache_file", lambda: state_paths["tracker"])
    monkeypatch.setattr(lpc, "refresh_lock_file", lambda: state_paths["lock"])
    monkeypatch.setattr(lpc, "refresh_log_file", lambda: state_paths["log"])
    monkeypatch.setattr(lpc, "_yield_scan_slice", lambda: None)
    monkeypatch.setattr(lpc, "_ml_table_root_lot_ids", lambda **kw: set())
    monkeypatch.setattr(lpc, "_CACHE_STATE", None)
    monkeypatch.setattr(lpc, "_CACHE_INDEX", None)
    monkeypatch.setattr(lpc, "_CACHE_INDEX_KEY", None)
    monkeypatch.setattr(lpc, "_CACHE_FILE_SIG", None)
    monkeypatch.setenv("FLOW_CACHE_CHANGE_DRIVEN", "1")
    monkeypatch.setenv("FLOW_CACHE_FULL_RECHECK_HOURS", "6")

    feeds: list[str] = []
    real_feed = lpc._feed_parquet

    def counting_feed(reducer, parquet, *args, **kwargs):
        feeds.append(str(parquet))
        return real_feed(reducer, parquet, *args, **kwargs)

    monkeypatch.setattr(lpc, "_feed_parquet", counting_feed)
    return lpc, tmp_path, state_paths, feeds


@pytest.mark.parametrize("artifact", ["json", "parquet"])
@pytest.mark.parametrize("aged_memory", [False, True])
def test_missing_wip_artifact_rebuilds_with_memory_state(
    wip_recovery_env, artifact, aged_memory,
):
    lpc, _root, paths, feeds = wip_recovery_env
    first = lpc.refresh_lot_progress_cache(force=True)
    assert first.get("source_digest")
    assert paths["json"].is_file() and paths["parquet"].is_file()
    scanned = len(feeds)

    if aged_memory:
        lpc._CACHE_STATE["generated_at"] = (
            dt.datetime.now() - dt.timedelta(hours=1)
        ).isoformat(timespec="seconds")
        lpc._CACHE_STATE.pop("verified_at", None)
    paths[artifact].unlink()

    recovered = lpc.refresh_lot_progress_cache(
        force=False, required_products=["ML_TABLE_PRODA"])

    assert len(feeds) > scanned
    assert paths["json"].is_file() and paths["parquet"].is_file()
    assert not recovered.get("errors")
    assert recovered.get("source_digest")


def test_full_recheck_uses_generated_at_even_with_recent_verification(wip_recovery_env):
    lpc, _root, _paths, feeds = wip_recovery_env
    lpc.refresh_lot_progress_cache(force=True)
    scanned = len(feeds)
    lpc._CACHE_STATE["generated_at"] = (
        dt.datetime.now() - dt.timedelta(hours=7)
    ).isoformat(timespec="seconds")
    lpc._CACHE_STATE["verified_at"] = dt.datetime.now().isoformat(timespec="seconds")

    rebuilt = lpc.refresh_lot_progress_cache(
        force=False, required_products=["ML_TABLE_PRODA"])

    assert len(feeds) > scanned
    assert not rebuilt.get("verified_unchanged")
    assert dt.datetime.fromisoformat(rebuilt["generated_at"]) > (
        dt.datetime.now() - dt.timedelta(minutes=1)
    )


def test_step_matching_transient_read_error_rebuilds_after_recovery(
    wip_recovery_env, monkeypatch,
):
    lpc, root, _paths, feeds = wip_recovery_env
    step_csv = root / "step_matching.csv"
    step_csv.write_text(
        "product,step_id,function_step\nPRODA,S20,ETCH\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(lpc, "_step_matching_paths", lambda: [step_csv])
    real_open = Path.open
    fail_once = {"pending": True}

    def flaky_open(path, *args, **kwargs):
        if path == step_csv and fail_once["pending"]:
            fail_once["pending"] = False
            raise PermissionError("temporary step-matching read failure")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", flaky_open)

    incomplete = lpc.refresh_lot_progress_cache(force=True)
    scanned = len(feeds)
    assert incomplete.get("errors")
    assert incomplete.get("source_digest") == ""

    recovered = lpc.refresh_lot_progress_cache(force=False)
    assert len(feeds) > scanned
    assert not recovered.get("errors")
    assert recovered.get("source_digest")
    latest = next(item for item in recovered["items"] if item["root_lot_id"] == "A1000")
    assert latest["step_id"] == "S20"
    assert latest["function_step"] == "ETCH"


def test_product_artifact_gate_requires_lookup_candidates_and_complete_pivot(
    monkeypatch, tmp_path,
):
    from core import lot_progress_cache as lpc
    from routers import splittable as s

    source = tmp_path / "ML_TABLE_PRODA.parquet"
    source.touch()
    monkeypatch.setenv("FLOW_CACHE_CHANGE_DRIVEN", "1")
    required = {
        "match": tmp_path / "match.parquet",
        "match_meta": tmp_path / "match.meta.json",
        "latest": tmp_path / "latest.parquet",
        "wip_json": tmp_path / "lot_wf_current.json",
        "wip_parquet": tmp_path / "lot_wf_current.parquet",
    }
    for path in required.values():
        path.touch()

    readiness = {"lookup": False, "pivot": True}
    lookup_calls: list[Path] = []

    def lookup_ready(path):
        lookup_calls.append(Path(path))
        return readiness["lookup"]

    monkeypatch.setattr(s, "_product_path", lambda product: source)
    monkeypatch.setattr(s._ml_table_lookup, "lookup_artifacts_fresh", lookup_ready)
    monkeypatch.setattr(
        s, "_pivot_cache_artifact_status",
        lambda product, path: {"ready": readiness["pivot"]},
    )
    monkeypatch.setattr(s, "_match_cache_path", lambda product: required["match"])
    monkeypatch.setattr(s, "_match_cache_meta_path", lambda product: required["match_meta"])
    monkeypatch.setattr(s, "_latest_lot_step_cache_path", lambda: required["latest"])
    monkeypatch.setattr(lpc, "cache_file", lambda: required["wip_json"])
    monkeypatch.setattr(lpc, "cache_parquet_file", lambda: required["wip_parquet"])
    monkeypatch.setattr(s, "_fab_lot_index_enabled", lambda: False)
    monkeypatch.setattr(
        s, "_current_fab_override",
        lambda product: ("ML_TABLE_PRODA", {}, "PRODA"),
    )

    assert s._auto_product_cache_artifacts_present("ML_TABLE_PRODA") is False
    readiness["lookup"] = True
    readiness["pivot"] = False
    assert s._auto_product_cache_artifacts_present("ML_TABLE_PRODA") is False
    readiness["pivot"] = True
    assert s._auto_product_cache_artifacts_present("ML_TABLE_PRODA") is True
    assert lookup_calls == [source, source, source]


def test_ml_table_transient_read_failure_does_not_publish_success_digest(
    wip_recovery_env, monkeypatch, original_wip_root_reader,
):
    from core import ml_table_lookup

    lpc, root, _paths, feeds = wip_recovery_env
    monkeypatch.setattr(lpc, "_ml_table_root_lot_ids", original_wip_root_reader)
    ml = root / "ML_TABLE_PRODA.parquet"
    pl.DataFrame({"root_lot_id": ["A1000", "A1001"]}).write_parquet(ml)
    monkeypatch.setattr(ml_table_lookup, "_discover_ml_table_files", lambda: [ml])
    real_schema = pl.read_parquet_schema
    failure = {"pending": True}

    def flaky_schema(path, *args, **kwargs):
        if Path(path) == ml and failure["pending"]:
            failure["pending"] = False
            raise PermissionError("temporary ML_TABLE lock")
        return real_schema(path, *args, **kwargs)

    monkeypatch.setattr(pl, "read_parquet_schema", flaky_schema)
    incomplete = lpc.refresh_lot_progress_cache(force=True)
    assert incomplete["errors"] and incomplete["source_digest"] == ""
    scanned = len(feeds)
    recovered = lpc.refresh_lot_progress_cache(force=False)
    assert len(feeds) > scanned
    assert not recovered["errors"] and recovered["source_digest"]
