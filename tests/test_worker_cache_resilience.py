from __future__ import annotations

import json
import socket
from pathlib import Path


def test_worker_handler_not_ok_is_transport_failure(tmp_path, monkeypatch):
    from core import worker_dispatch as wd

    result_dir = tmp_path / "results"
    result_dir.mkdir()
    claimed = tmp_path / "claimed.task.json"
    claimed.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(wd, "_results_dir", lambda: result_dir)
    monkeypatch.setitem(wd._HANDLERS, "test_not_ok", lambda _payload: {"ok": False, "error": "boom"})

    wd._execute_task(
        {"id": "task-1", "type": "test_not_ok", "payload": {}, "deadline": 0},
        claimed,
    )

    result = json.loads((result_dir / "task-1.json").read_text(encoding="utf-8"))
    assert result["ok"] is False
    assert result["error"] == "boom"
    assert result["result"]["ok"] is False


def test_run_heavy_falls_back_after_remote_handler_failure(tmp_path, monkeypatch):
    from core import worker_dispatch as wd

    queue_file = tmp_path / "task.task.json"
    monkeypatch.setattr(wd, "offload_enabled", lambda: True)
    monkeypatch.setattr(wd, "worker_alive", lambda **_kwargs: True)
    monkeypatch.setattr(wd, "worker_overloaded_reason", lambda _meta=None: "")
    monkeypatch.setattr(wd, "_queue_depth", lambda: 0)
    monkeypatch.setattr(wd, "_submit", lambda *_args, **_kwargs: ("task-2", queue_file))
    monkeypatch.setattr(
        wd,
        "_wait_for_result",
        lambda *_args, **_kwargs: {"id": "task-2", "ok": False, "error": "remote failed"},
    )
    calls = []

    result = wd.run_heavy("cache_build", {}, lambda: calls.append("local") or {"ok": True})

    assert result == {"ok": True}
    assert calls == ["local"]


def test_local_heavy_fallback_waits_for_user_idle_window(monkeypatch):
    from core import request_priority, worker_dispatch as wd

    calls = []
    monkeypatch.setenv("FLOW_LOCAL_HEAVY_IDLE_WAIT_SEC", "0")
    monkeypatch.setattr(request_priority, "users_active", lambda **_kwargs: True)

    result = wd._run_local_heavy(
        "cache_build",
        "idle-only-test",
        lambda: calls.append("ran") or {"ok": True},
        idle_only=True,
    )

    assert result == {"ok": False, "error": "local_heavy_waiting_for_idle"}
    assert calls == []


def test_worker_role_does_not_own_schedulers_or_ram(monkeypatch):
    from core import ml_table_lookup, runtime_limits, worker_dispatch

    monkeypatch.setattr(worker_dispatch, "server_role", lambda: "worker")
    for name in (
        "FLOW_ENABLE_HEAVY_BACKGROUND_JOBS",
        "FLOW_ENABLE_SPLITTABLE_PRODUCT_RAM_CACHE",
        "FLOW_ENABLE_SPLITTABLE_ROOT_LOT_RAM_CACHE",
        "FLOW_ENABLE_WORKER_RAM_CACHE",
        "FLOW_DISABLE_SPLITTABLE_ROOT_LOT_RAM_CACHE",
    ):
        monkeypatch.delenv(name, raising=False)

    assert runtime_limits.heavy_background_jobs_enabled() is False
    assert runtime_limits.splittable_product_ram_cache_scheduler_enabled() is False
    assert runtime_limits.splittable_root_lot_ram_cache_scheduler_enabled() is False
    assert ml_table_lookup.root_ram_cache_available() is False

    monkeypatch.setenv("FLOW_ENABLE_WORKER_RAM_CACHE", "1")
    assert ml_table_lookup.root_ram_cache_available() is True


def test_lookup_worker_resolves_logical_file_before_api_absolute_path(tmp_path, monkeypatch):
    from core import ml_table_lookup, worker_tasks

    local_source = tmp_path / "ML_TABLE_PRODA.parquet"
    local_source.write_bytes(b"test")
    built = []
    monkeypatch.setattr(
        ml_table_lookup,
        "resolve_ml_table_file",
        lambda **kwargs: local_source if kwargs.get("file") == local_source.name else None,
    )
    monkeypatch.setattr(
        ml_table_lookup,
        "build_lookup_cache",
        lambda fp, force=False: built.append((Path(fp), force)) or {"ok": True, "cache_dir": "cache"},
    )
    monkeypatch.setattr(ml_table_lookup, "cache_status", lambda _fp: {"status": "fresh"})

    result = worker_tasks._ml_lookup_cache_build(
        {
            "product": "ML_TABLE_PRODA",
            "file": local_source.name,
            "source_path": "/api-only/mount/ML_TABLE_PRODA.parquet",
        }
    )

    assert result["ok"] is True
    assert built == [(local_source, False)]


def test_lookup_worker_does_not_report_stale_lock_skip_as_success(tmp_path, monkeypatch):
    from core import ml_table_lookup, worker_tasks

    local_source = tmp_path / "ML_TABLE_PRODA.parquet"
    local_source.write_bytes(b"test")
    monkeypatch.setattr(ml_table_lookup, "resolve_ml_table_file", lambda **_kwargs: local_source)
    monkeypatch.setattr(
        ml_table_lookup,
        "build_lookup_cache",
        lambda _fp, force=False: {"ok": True, "skipped": True, "reason": "build_lock_held"},
    )
    monkeypatch.setattr(ml_table_lookup, "cache_status", lambda _fp: {"status": "stale"})

    result = worker_tasks._ml_lookup_cache_build({"file": local_source.name})

    assert result["ok"] is False
    assert result["error"] == "build_lock_held"


def test_latest_lot_candidates_filter_real_product_roots_before_limit(tmp_path, monkeypatch):
    import polars as pl

    from core import ml_table_lookup

    latest = tmp_path / "latest.parquet"
    pl.DataFrame({
        # 오염된 canonical cache처럼 다른 제품 root도 PRODA로 라벨된 상황.
        "product": ["ML_TABLE_PRODA"] * 4,
        "root_lot_id": ["B1000", "B1001", "A1000", "A1001"],
        "tkout_time": ["2026-04-04", "2026-04-03", "2026-04-02", "2026-04-01"],
    }).write_parquet(latest)
    monkeypatch.setattr(ml_table_lookup, "_latest_lot_by_root_wafer_path", lambda: latest)
    monkeypatch.setattr(ml_table_lookup, "_root_ram_cache_priority_prefix", lambda: "")

    roots = ml_table_lookup._recent_root_lot_ids_from_latest_parquet(
        tmp_path / "ML_TABLE_PRODA.parquet",
        2,
        allowed_roots={"A1000", "A1001"},
    )

    assert roots == ["A1000", "A1001"]


def test_empty_allowed_roots_does_not_accept_foreign_candidates(tmp_path, monkeypatch):
    import polars as pl

    from core import ml_table_lookup

    latest = tmp_path / "latest.parquet"
    pl.DataFrame({
        "product": ["ML_TABLE_PRODA"],
        "root_lot_id": ["B1000"],
        "tkout_time": ["2026-04-04"],
    }).write_parquet(latest)
    monkeypatch.setattr(ml_table_lookup, "_latest_lot_by_root_wafer_path", lambda: latest)
    monkeypatch.setattr(ml_table_lookup, "_root_ram_cache_priority_prefix", lambda: "")

    roots = ml_table_lookup._recent_root_lot_ids_from_latest_parquet(
        tmp_path / "ML_TABLE_PRODA.parquet",
        2,
        allowed_roots=set(),
    )

    assert roots == []


def test_fresh_local_orphan_build_lock_is_reclaimed(tmp_path, monkeypatch):
    from core import ml_table_lookup

    source = tmp_path / "ML_TABLE_PRODA.parquet"
    source.write_bytes(b"source")
    lock = tmp_path / "ML_TABLE_PRODA.build.lock"
    lock.write_text(json.dumps({
        "owner": f"{socket.gethostname()}:99999999",
        "host": socket.gethostname(),
        "pid": 99999999,
    }), encoding="utf-8")
    monkeypatch.setattr(ml_table_lookup, "_build_lock_path", lambda _fp: lock)
    monkeypatch.setattr(ml_table_lookup, "_local_pid_alive", lambda _pid: False)

    fd, acquired_lock, _owner = ml_table_lookup._try_acquire_build_lock(source)
    try:
        assert fd is not None
        assert acquired_lock == lock
    finally:
        ml_table_lookup._release_build_lock(fd, acquired_lock)


def test_unified_scan_runs_memory_heavy_stages_in_order(monkeypatch):
    from routers import splittable

    order = []
    monkeypatch.setattr(splittable, "_match_cache_products", lambda _product: ["P"])
    monkeypatch.setattr(splittable, "_begin_match_cache_job", lambda *_args, **_kwargs: (True, {}))
    monkeypatch.setattr(
        splittable,
        "_run_started_match_cache_job",
        lambda *_args, **_kwargs: order.append("match") or {"ok": True, "products": []},
    )
    monkeypatch.setattr(splittable, "_product_ram_cache_products", lambda _product: ["P"])
    monkeypatch.setattr(splittable, "_begin_product_ram_cache_job", lambda *_args, **_kwargs: (True, {}))
    monkeypatch.setattr(
        splittable,
        "_run_started_product_ram_cache_job",
        lambda *_args, **_kwargs: order.append("product") or {"ok": True, "products": []},
    )
    monkeypatch.setattr(
        splittable._ml_table_lookup,
        "refresh_root_lot_ram_cache",
        lambda **_kwargs: order.append("root") or {"ok": True, "products": []},
    )
    monkeypatch.setattr(splittable, "_wait_for_root_lookup_caches", lambda result, **_kwargs: result)
    monkeypatch.setattr(splittable, "_UNIFIED_SCAN_BUSY", True)

    result = splittable._run_unified_scan("P", True)

    assert result["ok"] is True
    assert order == ["match", "product", "root"]
    assert splittable._UNIFIED_SCAN_BUSY is False


def test_unified_scan_records_each_stage_in_cache_event_log(monkeypatch):
    from core import cache_event_log
    from routers import splittable

    monkeypatch.setattr(splittable, "_match_cache_products", lambda _product: ["P"])
    monkeypatch.setattr(splittable, "_begin_match_cache_job", lambda *_args, **_kwargs: (True, {}))
    monkeypatch.setattr(
        splittable, "_run_started_match_cache_job", lambda *_args, **_kwargs: {"ok": True, "products": ["P"]}
    )
    monkeypatch.setattr(splittable, "_product_ram_cache_products", lambda _product: ["P"])
    monkeypatch.setattr(splittable, "_begin_product_ram_cache_job", lambda *_args, **_kwargs: (True, {}))
    monkeypatch.setattr(
        splittable, "_run_started_product_ram_cache_job", lambda *_args, **_kwargs: {"ok": True, "products": ["P"]}
    )
    monkeypatch.setattr(
        splittable._ml_table_lookup,
        "refresh_root_lot_ram_cache",
        lambda **_kwargs: {"ok": True, "products": ["P"]},
    )
    monkeypatch.setattr(splittable, "_wait_for_root_lookup_caches", lambda result, **_kwargs: result)
    monkeypatch.setattr(splittable, "_UNIFIED_SCAN_BUSY", True)

    with cache_event_log._LOCK:
        existing_events = list(cache_event_log._EVENTS)
        cache_event_log._EVENTS.clear()
    try:
        splittable._run_unified_scan("P", True, job_id="scan-history-test")
        events = cache_event_log.get_events(category="scan")
    finally:
        with cache_event_log._LOCK:
            cache_event_log._EVENTS.clear()
            cache_event_log._EVENTS.extend(existing_events)

    for stage in ("match_cache", "product_ram", "root_lot_ram"):
        phases = {(event.get("detail") or {}).get("phase") for event in events if (event.get("detail") or {}).get("stage") == stage}
        assert phases == {"started", "finished"}


def test_lookup_cache_status_does_not_walk_all_root_partitions(tmp_path, monkeypatch):
    from core import ml_table_lookup

    source = tmp_path / "ML_TABLE_PRODA.parquet"
    source.write_bytes(b"source")
    cache_dir = tmp_path / "lookup"
    cache_dir.mkdir()
    meta = {
        "source_path": str(source.resolve()),
        "source_mtime": source.stat().st_mtime,
        "source_size": source.stat().st_size,
        "root_lot_id_count": 5000,
    }
    monkeypatch.setattr(ml_table_lookup, "cache_dir_for", lambda _fp: cache_dir)
    monkeypatch.setattr(ml_table_lookup, "_read_meta", lambda _fp: meta)
    monkeypatch.setattr(ml_table_lookup, "_job_status_for", lambda _fp: "")
    monkeypatch.setattr(
        ml_table_lookup,
        "_partition_files",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("partition tree walked")),
    )

    status = ml_table_lookup.cache_status(source)

    assert status["status"] == "fresh"
    assert status["has_cache"] is True


def test_view_dependency_signature_uses_short_ttl_cache(tmp_path, monkeypatch):
    from routers import splittable

    source = tmp_path / "ML_TABLE_PRODA.parquet"
    source.write_bytes(b"source")
    calls = []
    monkeypatch.setattr(
        splittable,
        "_path_cache_sig",
        lambda path: calls.append(str(path)) or (str(path), 1, 1),
    )
    monkeypatch.setattr(splittable, "_plan_alias_paths", lambda _product: [])
    monkeypatch.setattr(splittable, "_custom_tags_path", lambda: tmp_path / "tags.json")
    monkeypatch.setattr(splittable, "_management_rows_path", lambda: tmp_path / "mgmt.json")
    monkeypatch.setattr(splittable, "_view_global_stat_sig", lambda: ((), ()))
    with splittable._VIEW_PRODUCT_SIG_LOCK:
        splittable._VIEW_PRODUCT_SIG_CACHE.clear()

    first = splittable._split_view_cache_dep_signature("ML_TABLE_PRODA", product_fp=source)
    first_calls = len(calls)
    second = splittable._split_view_cache_dep_signature("ML_TABLE_PRODA", product_fp=source)

    assert first == second
    assert first_calls > 0
    assert len(calls) == first_calls


def test_cache_job_tracker_exposes_queued_stages_and_peak_delta(monkeypatch):
    from core import cache_event_log

    samples = iter([
        {"rss_gb": 1.0, "effective_gb": 0.8, "system_available_gb": 9.0},
        {"rss_gb": 2.0, "effective_gb": 1.8, "system_available_gb": 8.0},
        {"rss_gb": 1.5, "effective_gb": 1.2, "system_available_gb": 8.5},
    ])
    monkeypatch.setattr(cache_event_log, "_memory_sample", lambda: next(samples))
    monkeypatch.setattr(cache_event_log, "_ensure_sampler_locked", lambda: None)

    job_id = cache_event_log.start_job(
        "test", "test cache", [("one", "첫 단계"), ("two", "둘째 단계")]
    )
    cache_event_log.stage_started(job_id, "one")
    active = next(job for job in cache_event_log.get_jobs(recent=0) if job["id"] == job_id)
    assert [stage["status"] for stage in active["stages"]] == ["running", "queued"]
    assert active["peak_delta_gb"] == 1.0

    cache_event_log.finish_job(job_id, ok=True)
    recent = next(job for job in cache_event_log.get_jobs(recent=10) if job["id"] == job_id)
    assert recent["status"] == "done"
    assert recent["stages"][1]["status"] == "skipped"


def test_large_ml_table_miss_defers_instead_of_raw_request_scan(tmp_path, monkeypatch):
    from routers import splittable

    source = tmp_path / "ML_TABLE_PRODA.parquet"
    source.write_bytes(b"source")
    queued = {"status": "queued", "queued": True}
    monkeypatch.setattr(splittable, "_product_ram_cache_entry", lambda _product: None)
    monkeypatch.setattr(splittable, "_split_view_should_defer_raw_fallback", lambda _fp: True)
    monkeypatch.setattr(
        splittable._ml_table_lookup,
        "cache_status",
        lambda _fp: {"status": "missing", "has_cache": False},
    )
    monkeypatch.setattr(splittable._ml_table_lookup, "enqueue_build", lambda _fp: queued)
    monkeypatch.setattr(
        splittable,
        "_split_view_cache_preparing_payload",
        lambda *_args, **_kwargs: {"preparing": True, "message": _kwargs["message"]},
    )

    base, payload = splittable._split_view_large_root_cache_or_defer(
        "ML_TABLE_PRODA",
        "A1000",
        "",
        source,
        started=0.0,
        runtime_profile={},
        view_cache_key=("key",),
        prefix="KNOB",
        history_mode="all",
    )

    assert base is None
    assert payload["preparing"] is True
    assert "자동 재검색" in payload["message"]


def test_missing_known_partition_schedules_lookup_self_heal(tmp_path, monkeypatch):
    from core import ml_table_lookup

    source = tmp_path / "ML_TABLE_PRODA.parquet"
    source.write_bytes(b"source")
    cache_dir = tmp_path / "lookup"
    cache_dir.mkdir()
    enqueued = []
    monkeypatch.setattr(
        ml_table_lookup,
        "cache_status",
        lambda _fp: {"status": "fresh", "has_cache": True, "source_stale": False},
    )
    monkeypatch.setattr(ml_table_lookup, "cache_dir_for", lambda _fp: cache_dir)
    monkeypatch.setattr(
        ml_table_lookup,
        "read_candidate_index",
        lambda _fp: {"root_lot_ids": ["A1000"]},
    )
    monkeypatch.setattr(ml_table_lookup, "enqueue_build", lambda fp: enqueued.append(Path(fp)))
    monkeypatch.setattr(ml_table_lookup, "_record_root_access", lambda *_args: None)

    frame, _status = ml_table_lookup.scan_root_lot_cache(source, "A1000")

    assert frame is None
    assert enqueued == [source.resolve()]


def test_cold_root_is_projected_disk_scan_and_idle_prefetch(tmp_path, monkeypatch):
    import polars as pl

    from core import ml_table_lookup

    source = tmp_path / "ML_TABLE_PRODA.parquet"
    source.write_bytes(b"source")
    part_dir = tmp_path / "lookup" / "root_lot_id=A1000"
    part_dir.mkdir(parents=True)
    part = part_dir / "00000000.parquet"
    pl.DataFrame({"wafer_id": [1, 2], "KNOB_A": [10.0, 20.0], "UNUSED": ["x", "y"]}).write_parquet(part)
    queued = []
    profile = {}
    monkeypatch.setattr(
        ml_table_lookup,
        "cache_status",
        lambda _fp: {"status": "fresh", "has_cache": True, "source_stale": False},
    )
    monkeypatch.setattr(ml_table_lookup, "cache_dir_for", lambda _fp: tmp_path / "lookup")
    monkeypatch.setattr(ml_table_lookup, "_partition_files", lambda *_args: [part])
    monkeypatch.setattr(ml_table_lookup, "_root_ram_cache_get", lambda *_args: None)
    monkeypatch.setattr(
        ml_table_lookup,
        "enqueue_root_ram_prefetch",
        lambda fp, root: queued.append((Path(fp), root)) or True,
    )
    monkeypatch.setattr(ml_table_lookup, "_record_root_access", lambda *_args: None)

    frame, _status = ml_table_lookup.scan_root_lot_cache(source, "A1000", profile=profile)
    projected = frame.select(["wafer_id", "KNOB_A"]).collect()

    assert projected.columns == ["wafer_id", "KNOB_A"]
    assert projected.height == 2
    assert queued == [(source.resolve(), "A1000")]
    assert profile["root_data_source"] == "disk"
    assert profile["root_prefetch_queued"] is True


def test_fab_index_sweep_bootstraps_configured_product_without_cache_dir(tmp_path, monkeypatch):
    from routers import splittable

    queued = []
    monkeypatch.setattr(splittable, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(
        splittable,
        "load_json",
        lambda *_args, **_kwargs: {"enabled": ["PRODA"]},
    )
    monkeypatch.setattr(splittable, "_foreground_global_fab_scan_enabled", lambda: False)
    monkeypatch.setattr(
        splittable,
        "_current_fab_override",
        lambda product: ("ML_TABLE_PRODA", {}, "fab"),
    )
    monkeypatch.setattr(splittable, "_fab_source_signature", lambda *_args: [("fab", 1, 1)])
    monkeypatch.setattr(splittable, "_fab_lot_index_read_meta", lambda _product: {})
    monkeypatch.setattr(
        splittable,
        "_enqueue_fab_lot_index_build",
        lambda product, *_args, **_kwargs: queued.append(product) or True,
    )

    splittable._fab_lot_index_sweep_once()

    assert queued == ["ML_TABLE_PRODA"]


def test_root_view_never_scans_multi_gb_fab_source_on_index_miss(monkeypatch):
    import polars as pl

    from routers import splittable

    base = pl.DataFrame({
        "root_lot_id": ["A1000"],
        "wafer_id": [1],
        "KNOB_A": [10.0],
    }).lazy()
    profile = {}
    monkeypatch.delenv("FLOW_SPLITTABLE_INTERACTIVE_FAB_RAW_FALLBACK", raising=False)
    monkeypatch.setattr(
        splittable,
        "_current_fab_override",
        lambda product: ("ML_TABLE_PRODA", {}, "fab"),
    )
    monkeypatch.setattr(splittable, "_foreground_global_fab_scan_enabled", lambda: False)
    monkeypatch.setattr(splittable, "_latest_lot_progress_projection", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(splittable, "_fab_lot_index_scan_root", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(splittable, "_enqueue_fab_lot_index_build", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        splittable,
        "_scan_global_fab_sources",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("raw FAB scan")),
    )

    result = splittable._scan_product(
        "ML_TABLE_PRODA",
        root_lot_id="A1000",
        base_lf=base,
        runtime_profile=profile,
    ).collect()

    assert result.height == 1
    assert profile["fab_index_queued"] is True
    assert profile["cache_incomplete"] is True


def test_cache_queue_snapshot_distinguishes_running_from_future_queue(monkeypatch):
    from routers import splittable
    from core import worker_dispatch

    monkeypatch.setattr(worker_dispatch, "queue_snapshot", lambda limit=50: {"depth": 0, "queued": [], "running": []})
    monkeypatch.setattr(splittable._ml_table_lookup, "build_queue_snapshot", lambda: {})
    monkeypatch.setattr(splittable._ml_table_lookup, "root_ram_prefetch_snapshot", lambda limit=50: {"depth": 0, "queued": []})
    monkeypatch.setattr(
        splittable,
        "_match_cache_job_status",
        lambda: {"running": True, "current_product": "P1", "total": 3, "done": 0},
    )
    monkeypatch.setattr(
        splittable,
        "_product_ram_cache_job_status",
        lambda: {"running": True, "current_product": "P1", "total": 2, "done": 1},
    )

    queues = splittable._cache_queue_snapshot()

    assert queues["match_cache"]["remaining"] == 3
    assert queues["match_cache"]["queued"] == 2
    assert queues["product_ram"]["remaining"] == 1
    assert queues["product_ram"]["queued"] == 0
