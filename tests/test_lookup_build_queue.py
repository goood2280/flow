from pathlib import Path


def test_durable_worker_queue_is_waiting_not_failed(tmp_path, monkeypatch):
    from core import ml_table_lookup as lookup
    from core import worker_dispatch

    source = tmp_path / "ML_TABLE_PRODUCT_A.parquet"
    source.write_bytes(b"source")
    retries = []
    events = []

    monkeypatch.setattr(lookup, "lookup_artifacts_fresh", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(worker_dispatch, "run_heavy", lambda *_args, **_kwargs: {
        "ok": True,
        "queued": True,
        "deferred": True,
        "task_id": "worker-task-1",
        "deduped": False,
    })
    monkeypatch.setattr(
        lookup,
        "_schedule_build_retry",
        lambda fp, **kwargs: retries.append((Path(fp), kwargs)) or True,
    )
    monkeypatch.setattr(
        lookup,
        "_emit_build_event",
        lambda product, event, **kwargs: events.append((product, event, kwargs)),
    )

    with lookup._BUILD_LOCK:
        lookup._BUILD_QUEUE.clear()
        lookup._BUILD_QUEUE.append(source)
        lookup._BUILD_IMMEDIATE.clear()
        lookup._BUILD_LOCAL_ONLY.clear()
        lookup._BUILD_STATE.update({
            "running": False,
            "paused": False,
            "pause_reason": "",
            "resource_snapshot": {},
            "current": "",
            "last_error": "",
        })

    lookup._worker_loop()

    assert retries == [(source.resolve(), {
        "immediate": False,
        "consume_attempt": False,
        "local_only": False,
    })]
    assert lookup._BUILD_STATE["last_error"] == ""
    assert len(events) == 1
    product, message, event_kwargs = events[0]
    assert product == "ML_TABLE_PRODUCT_A"
    assert "워커 빌드 완료 대기" in message
    assert event_kwargs["ok"] is True
    assert event_kwargs["phase"] == "skip"
    assert event_kwargs["detail"]["reason"] == "worker_task_queued"
    assert event_kwargs["detail"]["task_id"] == "worker-task-1"


def test_failed_build_still_consumes_limited_retry(tmp_path, monkeypatch):
    from core import ml_table_lookup as lookup
    from core import worker_dispatch

    source = tmp_path / "ML_TABLE_PRODUCT_B.parquet"
    source.write_bytes(b"source")
    retries = []
    events = []

    monkeypatch.setattr(lookup, "lookup_artifacts_fresh", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(worker_dispatch, "run_heavy", lambda *_args, **_kwargs: {
        "ok": False,
        "queued": False,
        "deferred": True,
        "error": "worker queue is full",
    })
    monkeypatch.setattr(
        lookup,
        "_schedule_build_retry",
        lambda fp, **kwargs: retries.append((Path(fp), kwargs)) or False,
    )
    monkeypatch.setattr(
        lookup,
        "_emit_build_event",
        lambda product, event, **kwargs: events.append((product, event, kwargs)),
    )

    with lookup._BUILD_LOCK:
        lookup._BUILD_QUEUE.clear()
        lookup._BUILD_QUEUE.append(source)
        lookup._BUILD_IMMEDIATE.clear()
        lookup._BUILD_LOCAL_ONLY.clear()
        lookup._BUILD_STATE.update({
            "running": False,
            "paused": False,
            "pause_reason": "",
            "resource_snapshot": {},
            "current": "",
            "last_error": "",
        })

    lookup._worker_loop()

    assert retries == [(source.resolve(), {
        "immediate": False,
        "consume_attempt": True,
        "local_only": False,
    })]
    assert lookup._BUILD_STATE["last_error"] == "worker queue is full"
    assert len(events) == 1
    assert "재시도 없음" in events[0][1]
    assert events[0][2]["ok"] is False


def test_build_exception_retries_and_preserves_manual_local_execution(tmp_path, monkeypatch):
    from core import ml_table_lookup as lookup

    source = tmp_path / "ML_TABLE_PRODUCT_C.parquet"
    source.write_bytes(b"source")
    retries = []
    events = []

    monkeypatch.setattr(lookup, "lookup_artifacts_fresh", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        lookup,
        "_wait_for_lookup_cache_memory",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("temporary io error")),
    )
    monkeypatch.setattr(
        lookup,
        "_schedule_build_retry",
        lambda fp, **kwargs: retries.append((Path(fp), kwargs)) or True,
    )
    monkeypatch.setattr(
        lookup,
        "_emit_build_event",
        lambda product, event, **kwargs: events.append((product, event, kwargs)),
    )

    target = str(source.resolve())
    with lookup._BUILD_LOCK:
        lookup._BUILD_QUEUE.clear()
        lookup._BUILD_QUEUE.append(source)
        lookup._BUILD_IMMEDIATE.clear()
        lookup._BUILD_IMMEDIATE.add(target)
        lookup._BUILD_LOCAL_ONLY.clear()
        lookup._BUILD_LOCAL_ONLY.add(target)
        lookup._BUILD_STATE.update({
            "running": False,
            "paused": False,
            "pause_reason": "",
            "resource_snapshot": {},
            "current": "",
            "last_error": "",
            "last_source": "",
        })

    lookup._worker_loop()

    assert retries == [(source.resolve(), {
        "immediate": True,
        "consume_attempt": True,
        "local_only": True,
    })]
    assert lookup._BUILD_STATE["last_error"] == "temporary io error"
    assert "재시도 예약함" in events[0][1]
    assert events[0][2]["detail"]["retry_scheduled"] is True
