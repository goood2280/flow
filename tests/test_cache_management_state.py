import time


def test_external_scan_gate_task_reports_elapsed_and_original_wait():
    from core import scan_gate

    task = {
        "id": "ext-1",
        "kind": "lookup",
        "label": "lookup build",
        "product": "PRODUCT_A",
        "source": "worker",
        "started_mono": 90.0,
        "started_iso": "2026-09-01T12:00:00",
        "waited_sec": 3.5,
    }

    public = scan_gate._public(task, now=100.0)

    assert public["started_at"] == "2026-09-01T12:00:00"
    assert public["waited_sec"] == 3.5
    assert public["elapsed_sec"] == 10.0


def test_product_artifact_snapshot_ttl_starts_after_refresh(monkeypatch):
    from core import cache_event_log
    from routers import splittable

    with splittable._PRODUCT_CACHE_STATUS_LOCK:
        previous = dict(splittable._PRODUCT_CACHE_STATUS_SNAPSHOT)
        splittable._PRODUCT_CACHE_STATUS_SNAPSHOT.update({
            "at": 0.0,
            "value": None,
            "refreshing": False,
        })

    monotonic_values = iter([100.0, 125.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(splittable, "_match_cache_products", lambda _product: [])
    monkeypatch.setattr(cache_event_log, "product_status", lambda **_kwargs: {
        "products": [],
        "ok_count": 0,
        "failed_count": 0,
        "running_count": 0,
    })

    try:
        result = splittable._product_cache_status_snapshot(nonblocking=False)
        assert result["products"] == []
        assert splittable._PRODUCT_CACHE_STATUS_SNAPSHOT["at"] == 125.0
    finally:
        with splittable._PRODUCT_CACHE_STATUS_LOCK:
            splittable._PRODUCT_CACHE_STATUS_SNAPSHOT.clear()
            splittable._PRODUCT_CACHE_STATUS_SNAPSHOT.update(previous)


def test_orphaned_running_lifecycle_is_downgraded_when_no_builder_exists(monkeypatch):
    from core import cache_event_log
    from routers import splittable

    with splittable._PRODUCT_CACHE_STATUS_LOCK:
        previous = dict(splittable._PRODUCT_CACHE_STATUS_SNAPSHOT)
        splittable._PRODUCT_CACHE_STATUS_SNAPSHOT.update({
            "at": 0.0,
            "value": None,
            "refreshing": False,
        })

    kinds = []
    for kind in cache_event_log.PRODUCT_STATUS_KINDS:
        kinds.append({
            "kind": kind,
            "label": cache_event_log.CACHE_KIND_LABELS[kind],
            "state": "running" if kind == "lookup" else "never",
            "started_ts": 100.0 if kind == "lookup" else 0.0,
            "success_ts": 0.0,
            "failed_ts": 0.0,
            "last_ts": 100.0 if kind == "lookup" else 0.0,
            "idle_sec": 600.0 if kind == "lookup" else 0.0,
            "stalled": kind == "lookup",
            "message": "lookup started" if kind == "lookup" else "",
        })
    monkeypatch.setattr(splittable, "_match_cache_products", lambda _product: ["ML_TABLE_ORPHAN"])
    monkeypatch.setattr(cache_event_log, "product_status", lambda **_kwargs: {
        "products": [{"product": "ML_TABLE_ORPHAN", "state": "running", "kinds": kinds}],
        "ok_count": 0,
        "failed_count": 0,
        "running_count": 1,
    })
    monkeypatch.setattr(splittable, "_required_split_cache_status", lambda _product: {
        "ready_count": 0,
        "total": 4,
        "kinds": [
            {"kind": kind, "ready": False, "state": "missing", "done": 0, "built_ts": 0.0}
            for kind in cache_event_log.PRODUCT_STATUS_KINDS
        ],
    })

    try:
        result = splittable._product_cache_status_snapshot(nonblocking=False)
        row = result["products"][0]
        lookup = next(item for item in row["kinds"] if item["kind"] == "lookup")
        assert lookup["state"] == "stale"
        assert "현재 실행 중인 빌드도 없습니다" in lookup["message"]
        assert row["state"] == "partial"
        assert result["running_count"] == 0
    finally:
        with splittable._PRODUCT_CACHE_STATUS_LOCK:
            splittable._PRODUCT_CACHE_STATUS_SNAPSHOT.clear()
            splittable._PRODUCT_CACHE_STATUS_SNAPSHOT.update(previous)
