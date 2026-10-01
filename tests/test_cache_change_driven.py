"""PLAN P2: caches rebuild when their sources change, not on a timer.

- core.source_digest folds a source tree into one digest (add/modify/remove all change it).
- WIP latest-lot: an age-expired cache whose source digest is unchanged is re-verified
  instead of rescanning FAB; a changed FAB file rescans. FLOW_CACHE_CHANGE_DRIVEN=0
  restores the age rule. A usable in-memory state never re-reads the JSON file.
- The canonical latest-lot export rebuilds only the refreshed product's rows; the
  result equals a full export except the refreshed rows' update_time.
- Product rotation skips a product whose input fingerprint matches its last success
  (artifacts present, within the full-recheck window) without creating a job.
"""
from __future__ import annotations

import datetime as dt
import os
import time

import polars as pl
import pytest


# ── source_digest ──────────────────────────────────────────────────────────

def test_tree_digest_tracks_add_modify_remove(tmp_path):
    from core import source_digest as sd

    root = tmp_path / "FAB"
    (root / "P1" / "date=1").mkdir(parents=True)
    f1 = root / "P1" / "date=1" / "part_0.parquet"
    f1.write_bytes(b"one")
    (root / "P1" / "notes.txt").write_text("ignored", "utf-8")
    first = sd.tree_digest([root])
    assert first["files"] == 1
    assert sd.tree_digest([root]) == first  # stable

    (root / "P1" / "notes.txt").write_text("still ignored", "utf-8")
    assert sd.tree_digest([root]) == first  # non-data files do not count

    f2 = root / "P1" / "date=2" / "part_0.parquet"
    f2.parent.mkdir()
    f2.write_bytes(b"two")
    added = sd.tree_digest([root])
    assert added["files"] == 2 and added["digest"] != first["digest"]

    os.utime(f1, ns=(time.time_ns(), time.time_ns() + 5_000_000_000))
    modified = sd.tree_digest([root])
    assert modified["digest"] != added["digest"]

    f2.unlink()
    removed = sd.tree_digest([root])
    assert removed["files"] == 1 and removed["digest"] != modified["digest"]
    assert sd.tree_digest([tmp_path / "missing"])["digest"] != removed["digest"]


def test_memo_shares_one_computation_within_ttl():
    from core import source_digest as sd

    sd.clear_memo()
    calls = []
    assert sd.memo(("k",), 30.0, lambda: calls.append(1) or "v") == "v"
    assert sd.memo(("k",), 30.0, lambda: calls.append(1) or "w") == "v"
    assert len(calls) == 1
    assert sd.memo(("k",), 0.0, lambda: calls.append(1) or "w") in {"v", "w"}


def test_change_driven_requires_explicit_opt_in(monkeypatch):
    from core import source_digest as sd
    monkeypatch.delenv("FLOW_CACHE_CHANGE_DRIVEN", raising=False)
    assert sd.change_driven_enabled() is False
    monkeypatch.setenv("FLOW_CACHE_CHANGE_DRIVEN", "1")
    assert sd.change_driven_enabled() is True


@pytest.mark.parametrize("profile", ["auto", "large"])
def test_change_gate_opt_in_preserves_large_host_auto_caching(monkeypatch, profile):
    from core import cache_budget, cache_settings, runtime_limits, source_digest
    from routers import splittable as s
    monkeypatch.setattr(runtime_limits, "system_memory_snapshot",
                        lambda: {"system_memory_total_gb": 128.0})
    monkeypatch.setattr(runtime_limits, "effective_cpu_count", lambda: 8.0)
    monkeypatch.setattr(cache_budget, "_is_dev", lambda: False)
    monkeypatch.setenv("FLOW_RESOURCE_PROFILE", profile)
    monkeypatch.delenv("FLOW_SPLITTABLE_AUTO_PRODUCT_CACHE_ENABLED", raising=False)
    monkeypatch.delenv("FLOW_CACHE_CHANGE_DRIVEN", raising=False)
    monkeypatch.setattr(cache_settings, "_role_value", lambda key, is_dev: None)
    assert runtime_limits.resource_profile() == "large"
    assert s._auto_product_cache_enabled() is True
    assert source_digest.change_driven_enabled() is False


@pytest.mark.parametrize("failure", [{"errors": ["read failed"]}, {"skipped_by_lock": True}])
def test_wip_failure_is_reported_to_product_pipeline(monkeypatch, failure):
    from core import heavy_jobs, lot_progress_cache as lpc
    from routers import splittable as s
    monkeypatch.setattr(s, "_MANUAL_LATEST_REFRESH_RUNNING", False)
    monkeypatch.setattr(s, "_MANUAL_LATEST_REFRESH_RESULT", {})
    monkeypatch.setattr(s, "_cache_build_emit", lambda *a, **kw: None)
    monkeypatch.setattr(heavy_jobs, "run_heavy", lambda kind, fn, **kw: fn())
    monkeypatch.setattr(lpc, "refresh_lot_progress_cache", lambda **kw: {
        "generated_at": dt.datetime.now().isoformat(), "count": 1, **failure})
    assert s._enqueue_manual_lot_progress_refresh(["P1"])
    deadline = time.monotonic() + 5
    while s._MANUAL_LATEST_REFRESH_RUNNING and time.monotonic() < deadline:
        time.sleep(0.01)
    assert s._MANUAL_LATEST_REFRESH_RUNNING is False
    assert s._MANUAL_LATEST_REFRESH_RESULT["ok"] is False
    assert s._MANUAL_LATEST_REFRESH_RESULT["errors"] == failure.get("errors", [])


@pytest.mark.parametrize("age_hours,live_digest,skipped", [
    (1, "same", True), (7, "same", False), (0, "changed", False),
])
def test_match_cache_reuse_has_full_recheck_deadline(monkeypatch, tmp_path, age_hours, live_digest, skipped):
    from routers import splittable as s
    monkeypatch.setenv("FLOW_CACHE_CHANGE_DRIVEN", "1")
    monkeypatch.setenv("FLOW_CACHE_FULL_RECHECK_HOURS", "6")
    monkeypatch.setattr(s, "MATCH_CACHE_DIR", tmp_path)
    monkeypatch.setattr(s, "_scan_cancel_requested", lambda: False)
    monkeypatch.setattr(s, "_current_fab_override", lambda p: (p, {}, "FAB"))
    monkeypatch.setattr(s, "_match_cache_config_key", lambda *a: "cfg")
    monkeypatch.setattr(s, "_match_cache_source_digest", lambda *a: live_digest)
    target = tmp_path / "match.parquet"
    target.write_bytes(b"placeholder")
    meta = tmp_path / "meta.json"
    s.save_json(meta, {"config_key": "cfg", "source_digest": "same",
                       "built_epoch": time.time() - age_hours * 3600, "row_count": 1})
    monkeypatch.setattr(s, "_match_cache_path", lambda p: target)
    monkeypatch.setattr(s, "_match_cache_meta_path", lambda p: meta)
    scans = []

    def scan(product):
        scans.append(product)
        raise RuntimeError("stop at the source scan boundary")

    monkeypatch.setattr(s, "_scan_product_base", scan)
    out = s._refresh_match_cache_products(["P1"], force=False)
    assert out["products"][0]["skipped"] is skipped
    assert scans == ([] if skipped else ["P1"])


# ── WIP latest-lot ─────────────────────────────────────────────────────────

def _write_fab(root, day: str, rows: dict) -> None:
    path = root / "1.RAWDATA_DB_FAB" / "PRODA" / f"date={day}" / "part_0.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(path)


@pytest.fixture
def wip_env(monkeypatch, tmp_path):
    from core import lot_progress_cache as lpc
    from core import paths

    _write_fab(tmp_path, "20260901", {
        "root_lot_id": ["A1000", "A1000", "A1001"],
        "lot_id": ["A1000.1", "A1000.1", "A1001.1"],
        "wafer_id": ["1", "1", "2"],
        "step_id": ["S10", "S20", "S10"],
        "tkout_time": ["2026-09-01 01:00:00", "2026-09-01 02:00:00", "2026-09-01 03:00:00"],
    })
    monkeypatch.setattr(paths, "_get_db_root", lambda: tmp_path)
    monkeypatch.setattr(paths, "_get_base_root", lambda: tmp_path)
    monkeypatch.setattr(lpc, "_yield_scan_slice", lambda: None)
    monkeypatch.setattr(lpc, "_ml_table_root_lot_ids", lambda **kw: set())
    monkeypatch.setattr(lpc, "_CACHE_STATE", None)
    monkeypatch.setattr(lpc, "_CACHE_INDEX", None)
    monkeypatch.setattr(lpc, "_CACHE_INDEX_KEY", None)
    monkeypatch.setattr(lpc, "_CACHE_FILE_SIG", None)
    monkeypatch.setenv("FLOW_CACHE_CHANGE_DRIVEN", "1")
    feeds: list[str] = []
    real_feed = lpc._feed_parquet

    def counting_feed(reducer, parquet, *args, **kwargs):
        feeds.append(str(parquet))
        return real_feed(reducer, parquet, *args, **kwargs)

    monkeypatch.setattr(lpc, "_feed_parquet", counting_feed)
    return lpc, tmp_path, feeds


def _age_out(lpc) -> None:
    old = (dt.datetime.now() - dt.timedelta(hours=1)).isoformat(timespec="seconds")
    lpc._CACHE_STATE["generated_at"] = old
    lpc._CACHE_STATE.pop("verified_at", None)


def test_wip_refresh_reverifies_unchanged_sources_without_rescan(wip_env):
    lpc, root, feeds = wip_env
    first = lpc.refresh_lot_progress_cache(force=True)
    assert first["count"] == 2 and feeds and first.get("source_digest")
    scanned = len(feeds)

    _age_out(lpc)
    # A product without FAB rows used to force a full rescan on every rotation pass.
    again = lpc.refresh_lot_progress_cache(force=True, required_products=["ML_TABLE_NOFAB"])
    assert len(feeds) == scanned, "unchanged sources must not be rescanned"
    assert again.get("verified_unchanged") is True
    assert again["items"] == first["items"]
    assert lpc._cache_state_fresh(lpc._CACHE_STATE, 60)  # verified_at restarts the age

    _write_fab(root, "20260902", {
        "root_lot_id": ["A1002"], "lot_id": ["A1002.1"], "wafer_id": ["3"],
        "step_id": ["S10"], "tkout_time": ["2026-09-02 01:00:00"],
    })
    _age_out(lpc)
    changed = lpc.refresh_lot_progress_cache(force=True)
    assert len(feeds) > scanned, "a new FAB file must be scanned"
    assert changed["count"] == 3
    assert changed["source_digest"] != first["source_digest"]


def test_forced_refresh_rescans_young_cache_when_sources_changed(wip_env):
    """The rotation calls refresh(force=True) because a source changed. A cache younger than
    30 minutes used to be returned as-is, so the WIP stayed stale after a FAB change."""
    lpc, root, feeds = wip_env
    lpc.refresh_lot_progress_cache(force=True)
    scanned = len(feeds)
    lpc.refresh_lot_progress_cache(force=True)
    assert len(feeds) == scanned  # young + unchanged: no rescan

    _write_fab(root, "20260903", {
        "root_lot_id": ["A1000"], "lot_id": ["A1000.1"], "wafer_id": ["1"],
        "step_id": ["S99"], "tkout_time": ["2026-09-03 01:00:00"],
    })
    assert lpc.load_lot_progress_cache()["count"] == 2  # readers stay on the cheap age rule
    assert len(feeds) == scanned
    state = lpc.refresh_lot_progress_cache(force=True)
    assert len(feeds) > scanned
    steps = {item["root_lot_id"]: item["step_id"] for item in state["items"]}
    assert steps["A1000"] == "S99"


def test_wip_refresh_age_rule_when_change_driven_off(wip_env, monkeypatch):
    lpc, _root, feeds = wip_env
    monkeypatch.setenv("FLOW_CACHE_CHANGE_DRIVEN", "0")
    lpc.refresh_lot_progress_cache(force=True)
    scanned = len(feeds)
    _age_out(lpc)
    lpc.refresh_lot_progress_cache(force=True)
    assert len(feeds) > scanned


def test_usable_memory_state_does_not_reparse_cache_file(wip_env, monkeypatch):
    lpc, _root, _feeds = wip_env
    lpc.refresh_lot_progress_cache(force=True)

    def boom(*_args, **_kwargs):
        raise AssertionError("cache file re-read while the memory state was usable")

    monkeypatch.setattr(lpc, "_load_cache_file_state", boom)
    state = lpc._fresh_existing_cache_state(
        lpc.cache_file(), lpc.lot_progress_cache_source_root(),
        lpc.lot_progress_column_mapping(), lpc.lot_progress_cache_refresh_seconds())
    assert state is not None and state["count"] == 2
    assert lpc.load_lot_progress_cache()["count"] == 2


def test_cache_file_of_same_generation_is_not_reparsed(wip_env, monkeypatch):
    lpc, _root, _feeds = wip_env
    lpc.refresh_lot_progress_cache(force=True)
    loads = []
    real_loads = lpc.json.loads
    monkeypatch.setattr(lpc.json, "loads", lambda text, *a, **k: loads.append(1) or real_loads(text, *a, **k))
    state, sig = lpc._load_cache_file_state(lpc.cache_file())
    assert state is lpc._CACHE_STATE and sig == lpc._CACHE_FILE_SIG
    assert loads == []


# ── canonical latest-lot export ────────────────────────────────────────────

def test_incremental_export_matches_full_export(monkeypatch, tmp_path):
    from routers import splittable as s

    def match_frame(product: str, roots: list[str]) -> pl.LazyFrame:
        n = len(roots)
        return pl.DataFrame({
            s.MATCH_CACHE_ROOT_COL: roots,
            s.MATCH_CACHE_WAFER_COL: [str(i + 1) for i in range(n)],
            s.MATCH_CACHE_FAB_COL: [f"{r}.1" for r in roots],
            "step_id": ["S10", " S20 ", None][:n] + ["S10"] * max(0, n - 3),
            "lot_type": ["P "] * n,
            s.MATCH_CACHE_TS_COL: [f"2026-09-0{i + 1} 00:00:00" for i in range(n)],
        }).lazy()

    catalog = {
        "ML_TABLE_PRODA": ["A1", "A2", "A3"],
        "ML_TABLE_PRODB": ["B1", "B2"],
        "ML_TABLE_PRODC": ["C1", "C2", "C3", "C4"],
    }
    target = tmp_path / "cache" / "latest.parquet"
    monkeypatch.setattr(s, "_latest_lot_step_cache_path", lambda: target)
    monkeypatch.setattr(s, "_match_cache_products", lambda product="": list(catalog))
    monkeypatch.setattr(s, "_match_cache_current",
                        lambda product: {"lf": match_frame(product, catalog[product]), "product": product})
    monkeypatch.setattr(s._latest_lot_partitions, "sync_partitions", lambda *a, **k: True)
    import core.lot_step as lot_step
    monkeypatch.setattr(lot_step, "lookup_step_meta",
                        lambda product, step_id: {"function_step": f"F-{product}-{step_id}"})

    full = s.export_latest_lot_step_cache()
    df_full = pl.read_parquet(target)
    assert full["incremental"] is False and df_full.height == 9
    assert set(df_full.get_column("function_step").to_list()) >= {"F-PRODA-S10", "F-PRODA-S20", ""}

    catalog["ML_TABLE_PRODB"] = ["B1", "B2", "B9"]  # PRODB changed
    time.sleep(1.1)  # update_time has second resolution
    inc = s.export_latest_lot_step_cache(refresh_products=["ML_TABLE_PRODB"])
    df_inc = pl.read_parquet(target)
    assert inc["incremental"] is True and inc["refreshed_products"] == ["PRODB"]

    expected = s.export_latest_lot_step_cache()  # full rebuild of the changed catalog
    df_expected = pl.read_parquet(target)
    cols = [c for c in df_expected.columns if c != "update_time"]
    assert df_inc.select(cols).equals(df_expected.select(cols))
    kept_times = (df_inc.filter(pl.col("product") != "PRODB")
                  .get_column("update_time").unique().to_list())
    assert kept_times == df_full.get_column("update_time").unique().to_list()
    assert expected["incremental"] is False

    # Two product refreshes must merge into successive generations, including
    # when they begin together after reading the same previous catalog.
    from concurrent.futures import ThreadPoolExecutor
    import threading

    first_read = threading.Event()
    second_started = threading.Event()
    real_existing = s._latest_lot_step_existing_rows

    def blocked_existing(path, exclude):
        rows = real_existing(path, exclude)
        if "PRODA" in exclude:
            first_read.set()
            assert second_started.wait(5)
            time.sleep(0.1)
        return rows

    def second_export():
        second_started.set()
        return s.export_latest_lot_step_cache(refresh_products=["ML_TABLE_PRODC"])

    monkeypatch.setattr(s, "_latest_lot_step_existing_rows", blocked_existing)
    catalog["ML_TABLE_PRODA"].append("A9")
    catalog["ML_TABLE_PRODC"].append("C9")
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(s.export_latest_lot_step_cache, refresh_products=["ML_TABLE_PRODA"])
        assert first_read.wait(5)
        c = pool.submit(second_export)
        assert a.result(timeout=10)["ok"] and c.result(timeout=10)["ok"]
    roots = set(pl.read_parquet(target).get_column("root_lot_id").to_list())
    assert {"A9", "C9"}.issubset(roots)


def test_incremental_export_falls_back_to_full_without_existing_file(monkeypatch, tmp_path):
    from routers import splittable as s

    target = tmp_path / "missing" / "latest.parquet"
    monkeypatch.setattr(s, "_latest_lot_step_cache_path", lambda: target)
    monkeypatch.setattr(s, "_match_cache_products", lambda product="": ["ML_TABLE_PRODA"])
    monkeypatch.setattr(s, "_match_cache_current", lambda product: {"lf": pl.DataFrame({
        s.MATCH_CACHE_ROOT_COL: ["A1"], s.MATCH_CACHE_WAFER_COL: ["1"],
        s.MATCH_CACHE_FAB_COL: ["A1.1"], "step_id": ["S1"], s.MATCH_CACHE_TS_COL: ["2026-09-01"],
    }).lazy(), "product": product})
    monkeypatch.setattr(s._latest_lot_partitions, "sync_partitions", lambda *a, **k: True)
    out = s.export_latest_lot_step_cache(refresh_products=["ML_TABLE_PRODA"])
    assert out["incremental"] is False and out["row_count"] == 1


# ── product rotation gate ──────────────────────────────────────────────────

@pytest.fixture
def gate(monkeypatch, tmp_path):
    from routers import splittable as s

    monkeypatch.setattr(s, "_auto_product_cache_state_path", lambda: tmp_path / "schedule.json")
    monkeypatch.setattr(s, "_AUTO_PRODUCT_CACHE_FINGERPRINTS", {})
    monkeypatch.setattr(s, "_AUTO_PRODUCT_CACHE_PENDING_FP", {})
    monkeypatch.setattr(s, "_AUTO_PRODUCT_CACHE_GATE_STATS", {
        "unchanged_skips": 0, "built": 0, "last_unchanged_product": "", "last_unchanged_at": "",
        "sweep_unchanged": 0, "sweep_built": 0})
    state = dict(s._AUTO_PRODUCT_CACHE_STATE)
    state.update({"current_product": "", "queued_product": "", "cycle_first_product": "",
                  "cycle_completed_products": 0, "last_product": "", "loaded": True})
    monkeypatch.setattr(s, "_AUTO_PRODUCT_CACHE_STATE", state)
    monkeypatch.setattr(s, "_match_cache_products", lambda product="": ["P1", "P2", "P3"])
    monkeypatch.setattr(s, "_auto_product_cache_interval_minutes", lambda: 15)
    fingerprints = {"P1": "fp1", "P2": "fp2", "P3": "fp3"}
    monkeypatch.setattr(s, "_auto_product_cache_fingerprint_parts",
                        lambda product: {"fab": "f", "ml_table": fingerprints[product]})
    present = {"value": True}
    monkeypatch.setattr(s, "_auto_product_cache_artifacts_present", lambda product: present["value"])
    monkeypatch.setenv("FLOW_CACHE_CHANGE_DRIVEN", "1")
    monkeypatch.delenv("FLOW_CACHE_FULL_RECHECK_HOURS", raising=False)
    return s, fingerprints, present


def _fp(value: str) -> str:
    from core import source_digest as sd
    return sd.combine({"fab": "f", "ml_table": value})


def test_gate_runs_until_first_success_then_skips_unchanged(gate):
    s, fingerprints, present = gate
    unchanged, fp = s._auto_product_cache_check_unchanged("P1")
    assert (unchanged, fp) == (False, _fp("fp1"))  # never built → run
    assert s._auto_product_cache_gate_snapshot()["last_rebuild_reason"] == "첫 실행"

    s._auto_product_cache_on_finished("P1", {"ok": True})
    assert s._auto_product_cache_check_unchanged("P1") == (True, _fp("fp1"))

    fingerprints["P1"] = "fp1-changed"
    assert s._auto_product_cache_check_unchanged("P1") == (False, _fp("fp1-changed"))
    assert s._auto_product_cache_gate_snapshot()["last_rebuild_reason"] == "원천 변경: ml_table"
    fingerprints["P1"] = "fp1"
    present["value"] = False  # an artifact was deleted → rebuild
    assert s._auto_product_cache_check_unchanged("P1")[0] is False
    present["value"] = True
    s._AUTO_PRODUCT_CACHE_FINGERPRINTS["P1"]["at"] = time.time() - 7 * 3600  # past 6h recheck
    assert s._auto_product_cache_check_unchanged("P1")[0] is False
    assert s._auto_product_cache_gate_snapshot()["last_rebuild_reason"] == "주기 재확인"


def test_gate_failed_pipeline_does_not_record_fingerprint(gate):
    s, _fingerprints, _present = gate
    assert s._auto_product_cache_check_unchanged("P2")[0] is False
    s._auto_product_cache_on_finished("P2", {"ok": False})
    assert s._auto_product_cache_check_unchanged("P2")[0] is False


def test_gate_fingerprints_survive_restart(gate, monkeypatch):
    s, _fingerprints, _present = gate
    assert s._auto_product_cache_check_unchanged("P3")[0] is False
    s._auto_product_cache_on_finished("P3", {"ok": True})
    monkeypatch.setattr(s, "_AUTO_PRODUCT_CACHE_FINGERPRINTS", {})  # new process
    assert s._auto_product_cache_check_unchanged("P3") == (True, _fp("fp3"))


def test_skip_advances_rotation_without_a_job(gate, monkeypatch):
    import threading

    s, _fingerprints, _present = gate
    submitted = []
    monkeypatch.setattr(s, "_submit_product_cache_scan", lambda *a, **k: submitted.append(a) or {})
    # An app started by an earlier test may run the real rotation thread; keep it from
    # waking up and touching the rotation state while this sequence runs.
    monkeypatch.setattr(s, "_SEARCH_CACHE_MAINT_WAKE", threading.Event())
    with s._AUTO_PRODUCT_CACHE_STATE_LOCK:
        s._auto_product_cache_skip_unchanged("P1")
        assert submitted == []
        assert s._AUTO_PRODUCT_CACHE_STATE["next_product"] == "P2"
        assert s._AUTO_PRODUCT_CACHE_STATE["next_at_ts"] <= time.time() + 1  # same sweep: no pause
        s._auto_product_cache_skip_unchanged("P2")
        s._auto_product_cache_skip_unchanged("P3")  # wraps → interval pause, sweep summary kept
        assert s._AUTO_PRODUCT_CACHE_STATE["next_at_ts"] > time.time() + 14 * 60
    snap = s._auto_product_cache_gate_snapshot()
    assert snap["unchanged_skips"] == 3 and snap["last_sweep_unchanged"] == 3
    assert snap["sweep_unchanged"] == 0


def test_gate_off_restores_full_sweeps(gate, monkeypatch):
    s, _fingerprints, _present = gate
    monkeypatch.setenv("FLOW_CACHE_CHANGE_DRIVEN", "0")
    assert s._auto_product_cache_change_gate_enabled() is False
    assert s._match_cache_source_digest("ML_TABLE_PRODA", "") == ""


def test_fab_tree_digest_ignores_preferred_folder_order(monkeypatch, tmp_path):
    """_global_fab_source_paths puts the asking product's folder first; the shared FAB
    digest must not depend on which product asked (it did, and every sweep rebuilt)."""
    from core import source_digest as sd
    from routers import splittable as s

    dirs = []
    for name in ("PRODA", "PRODB", "PRODC"):
        d = tmp_path / "FAB" / name / "date=1"
        d.mkdir(parents=True)
        (d / "part_0.parquet").write_bytes(name.encode())
        dirs.append(str(d.parent))
    orders = {"PRODA": dirs, "PRODC": [dirs[2], dirs[0], dirs[1]]}
    monkeypatch.setattr(s, "_fab_source_dirs", lambda fab_source, include_all=True: orders[fab_source])
    sd.clear_memo()
    first = s._fab_tree_digest("PRODA")
    sd.clear_memo()
    assert s._fab_tree_digest("PRODC") == first


def test_ingest_notification_invalidates_digest_memo(monkeypatch):
    from core import source_digest as sd
    from routers import splittable as s
    import threading
    monkeypatch.setattr(s, "_SEARCH_CACHE_MAINT_WAKE", threading.Event())
    monkeypatch.setattr(s, "_AUTO_PRODUCT_CACHE_STATE", {})
    sd.clear_memo()
    assert sd.memo("ingest", 30, lambda: "old") == "old"
    s.notify_fab_sources_changed("test")
    assert sd.memo("ingest", 30, lambda: "new") == "new"
    assert s._SEARCH_CACHE_MAINT_WAKE.is_set()


def test_wip_scan_yields_per_time_slice_not_per_file(monkeypatch):
    """Yielding before every file (up to 3s each on a large host) made a FAB tree of tens of
    thousands of files take hours under constant user traffic."""
    from core import lot_progress_cache as lpc
    from core import request_priority

    calls = []
    monkeypatch.setattr(request_priority, "yield_to_users",
                        lambda max_wait_sec=0.0, **kw: calls.append(max_wait_sec) or 0.0)
    monkeypatch.delenv("FLOW_LOT_PROGRESS_YIELD_EVERY_SEC", raising=False)
    monkeypatch.delenv("FLOW_LOT_PROGRESS_YIELD_MAX_WAIT_SEC", raising=False)
    monkeypatch.setattr(lpc._SCAN_SLICE_TLS, "last", time.monotonic(), raising=False)
    for _ in range(200):
        lpc._yield_scan_slice()
    assert calls == []
    lpc._SCAN_SLICE_TLS.last = time.monotonic() - 1.5
    lpc._yield_scan_slice()
    assert calls == [0.5]


def test_fab_index_build_closes_its_job(monkeypatch):
    """stage_finished/finish_job were called without the required ok=; the TypeError was
    swallowed and every build left a 'running' job that was later reaped as a failure."""
    from core import cache_event_log
    from routers import splittable as s

    monkeypatch.setattr(s, "_fab_source_signature", lambda fab_source, include_all: [])
    monkeypatch.setattr(s, "_fab_lot_index_read_meta", lambda product: {})
    monkeypatch.setattr(s, "_build_fab_lot_index_full", lambda *a, **k: True)
    assert s._build_fab_lot_index("PRODJOBTEST", "", False) is True
    jobs = [j for j in cache_event_log.get_jobs(recent=50) if "PRODJOBTEST" in str(j.get("product"))]
    assert jobs and all(j["status"] == "done" for j in jobs)


@pytest.mark.parametrize("count", [0, 1, 499, 500, 501, 1234])
def test_chunked_state_json_matches_json_dumps(tmp_path, monkeypatch, count):
    """The WIP state file is written in chunks so the C encoder does not hold the GIL for the
    whole dump; the bytes must equal json.dumps(state)."""
    import json
    from core import lot_progress_cache as lpc

    items = [{"product": "제품A", "root_lot_id": f"R{i}", "wafer_id": str(i % 25),
              "nested": {"k": [i, None, 1.5]}, "flag": i % 2 == 0} for i in range(count)]
    state = {"version": 3, "generated_at": "2026-10-01T00:00:00", "errors": ["x"], "items": items}
    path = tmp_path / "state.json"
    lpc._write_state_json(state, path)
    assert path.read_text(encoding="utf-8") == json.dumps(state, ensure_ascii=False)
    other = {"items": items, "tail": 1}  # items not last → plain dump fallback
    lpc._write_state_json(other, path)
    assert json.loads(path.read_text(encoding="utf-8")) == other
