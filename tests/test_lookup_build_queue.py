from pathlib import Path


class _AliveTimer:
    def is_alive(self):
        return True


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


def test_delayed_retry_is_reported_as_queued(tmp_path):
    from core import ml_table_lookup as lookup

    source = (tmp_path / "ML_TABLE_RETRY.parquet").resolve()
    key = str(source)
    with lookup._BUILD_LOCK:
        previous_timers = dict(lookup._BUILD_RETRY_TIMERS)
        previous_state = dict(lookup._BUILD_STATE)
        previous_queue = list(lookup._BUILD_QUEUE)
        lookup._BUILD_RETRY_TIMERS.clear()
        lookup._BUILD_RETRY_TIMERS[key] = _AliveTimer()
        lookup._BUILD_QUEUE.clear()
        lookup._BUILD_STATE.update({"running": False, "current": ""})
    try:
        assert lookup._job_status_for(source) == "queued"
        assert lookup.cache_status(source)["status"] == "queued"
    finally:
        with lookup._BUILD_LOCK:
            lookup._BUILD_RETRY_TIMERS.clear()
            lookup._BUILD_RETRY_TIMERS.update(previous_timers)
            lookup._BUILD_QUEUE.clear()
            lookup._BUILD_QUEUE.extend(previous_queue)
            lookup._BUILD_STATE.clear()
            lookup._BUILD_STATE.update(previous_state)


def test_durable_lookup_falls_back_locally_when_worker_is_offline(monkeypatch):
    from core import worker_dispatch

    calls = []
    monkeypatch.setattr(worker_dispatch, "server_role", lambda: "api")
    monkeypatch.setattr(worker_dispatch, "offload_enabled", lambda: True)
    monkeypatch.setattr(worker_dispatch, "worker_alive", lambda **_kwargs: False)
    monkeypatch.setattr(
        worker_dispatch,
        "_discard_unclaimed_deduped_task",
        lambda key: calls.append(("discard", key)) or 1,
    )
    monkeypatch.setattr(worker_dispatch, "_bump", lambda key: calls.append(("bump", key)))
    monkeypatch.setattr(
        worker_dispatch,
        "_run_local_heavy",
        lambda task_type, name, fn, **kwargs: calls.append(("local", task_type, kwargs)) or fn(),
    )
    monkeypatch.setattr(
        worker_dispatch,
        "_submit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("offline fallback must not queue")),
    )

    result = worker_dispatch.run_heavy(
        "ml_lookup_cache_build",
        {"product": "ML_TABLE_PRODUCT"},
        lambda: {"ok": True, "executed_on": "local"},
        durable=True,
        local_fallback=True,
        local_idle_only=True,
        priority="maintenance",
        dedupe_key="ml_lookup:ML_TABLE_PRODUCT",
    )

    assert result == {"ok": True, "executed_on": "local"}
    assert ("discard", "ml_lookup:ML_TABLE_PRODUCT") in calls
    assert any(call[0] == "local" and call[2]["idle_only"] is True for call in calls)


def test_old_lookup_lock_is_not_reclaimed_while_owner_pid_is_alive(tmp_path, monkeypatch):
    import json
    import os
    import socket
    import time

    from core import ml_table_lookup as lookup

    source = tmp_path / "ML_TABLE_LONG.parquet"
    lock_path = tmp_path / "lookup.build.lock"
    lock_path.write_text(json.dumps({
        "owner": f"{socket.gethostname()}:{os.getpid()}",
        "host": socket.gethostname(),
        "pid": os.getpid(),
    }), encoding="utf-8")
    old = time.time() - lookup.BUILD_LOCK_STALE_SECONDS - 60
    os.utime(lock_path, (old, old))
    monkeypatch.setattr(lookup, "_build_lock_path", lambda _fp: lock_path)

    fd, returned_path, _owner = lookup._try_acquire_build_lock(source)

    assert fd is None
    assert returned_path == lock_path
    assert lock_path.is_file()
