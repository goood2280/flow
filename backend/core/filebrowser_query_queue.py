"""FIFO execution queue for interactive File Browser SQL scans."""
from __future__ import annotations

import contextlib
import os
import threading
import time
from collections import deque
from typing import Any

from core import duckdb_engine


class QueryQueueCanceled(RuntimeError):
    pass


class QueryQueueExpired(RuntimeError):
    pass


_COND = threading.Condition()
_PENDING: deque[dict[str, Any]] = deque()
_CURRENT: dict[str, Any] | None = None


def _env_seconds(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.environ.get(name, "") or default)
    except Exception:
        value = default
    return max(low, min(high, value))


def stale_seconds() -> float:
    return _env_seconds("FLOW_FILEBROWSER_SQL_QUEUE_STALE_SEC", 120.0, 10.0, 3600.0)


def max_runtime_seconds() -> float:
    return _env_seconds("FLOW_FILEBROWSER_SQL_MAX_RUNTIME_SEC", 300.0, 30.0, 7200.0)


def max_pending() -> int:
    try:
        return max(1, min(64, int(os.environ.get("FLOW_FILEBROWSER_SQL_QUEUE_MAX", "") or 24)))
    except Exception:
        return 24


def _cancel_item_locked(item: dict[str, Any], reason: str) -> None:
    item["canceled"] = True
    item["cancel_reason"] = reason


def _drop_stale_locked(now: float) -> None:
    ttl = stale_seconds()
    for item in list(_PENDING):
        if now - float(item["created_mono"]) >= ttl:
            _cancel_item_locked(item, "queue_expired")
            try:
                _PENDING.remove(item)
            except ValueError:
                pass


def cancel(*, username: str, session_id: str, query_id: str = "", reason: str = "page_left") -> dict:
    """Remove matching queued work and interrupt it when it is already running."""
    global _CURRENT
    removed = 0
    interrupted = False
    query_key = ""
    with _COND:
        for item in list(_PENDING):
            if item["username"] != username or item["session_id"] != session_id:
                continue
            if query_id and item["query_id"] != query_id:
                continue
            _cancel_item_locked(item, reason)
            try:
                _PENDING.remove(item)
                removed += 1
            except ValueError:
                pass
        current = _CURRENT
        if (
            current
            and current["username"] == username
            and current["session_id"] == session_id
            and (not query_id or current["query_id"] == query_id)
        ):
            _cancel_item_locked(current, reason)
            query_key = str(current.get("query_key") or "")
            interrupted = True
        _COND.notify_all()
    if query_key:
        duckdb_engine.interrupt_query(query_key)
    return {"ok": True, "removed": removed, "interrupted": interrupted}


def _expire_running(query_id: str, query_key: str) -> None:
    with _COND:
        current = _CURRENT
        if not current or current["query_id"] != query_id:
            return
        _cancel_item_locked(current, "runtime_expired")
        _COND.notify_all()
    duckdb_engine.interrupt_query(query_key)


def _raise_if_canceled(item: dict[str, Any]) -> None:
    if not item["canceled"]:
        return
    reason = str(item["cancel_reason"] or "canceled")
    if reason in {"runtime_expired", "queue_expired"}:
        raise QueryQueueExpired(reason)
    raise QueryQueueCanceled(reason)


@contextlib.contextmanager
def execute(*, username: str, session_id: str, query_id: str, query_key: str):
    """Wait in FIFO order, then exclusively run one interactive SQL scan."""
    global _CURRENT
    item = {
        "username": str(username or ""),
        "session_id": str(session_id or ""),
        "query_id": str(query_id or ""),
        "query_key": str(query_key or ""),
        "created_mono": time.monotonic(),
        "canceled": False,
        "cancel_reason": "",
    }
    with _COND:
        # A page can only own its newest request. This also closes the race in
        # which the new GET reaches the server before the explicit cancel POST.
        for old in list(_PENDING):
            if old["username"] == item["username"] and old["session_id"] == item["session_id"]:
                _cancel_item_locked(old, "replaced")
                try:
                    _PENDING.remove(old)
                except ValueError:
                    pass
        if (
            _CURRENT
            and _CURRENT["username"] == item["username"]
            and _CURRENT["session_id"] == item["session_id"]
        ):
            _cancel_item_locked(_CURRENT, "replaced")
            duckdb_engine.interrupt_query(str(_CURRENT.get("query_key") or ""))
        if len(_PENDING) >= max_pending():
            raise QueryQueueExpired("SQL queue is full")
        _PENDING.append(item)
        while True:
            now = time.monotonic()
            _drop_stale_locked(now)
            if item["canceled"]:
                reason = str(item["cancel_reason"] or "canceled")
                if reason == "queue_expired":
                    raise QueryQueueExpired("SQL queue wait expired")
                raise QueryQueueCanceled(reason)
            if _CURRENT is None and _PENDING and _PENDING[0] is item:
                _PENDING.popleft()
                _CURRENT = item
                item["started_mono"] = now
                break
            remaining = stale_seconds() - (now - float(item["created_mono"]))
            if remaining <= 0:
                _cancel_item_locked(item, "queue_expired")
                try:
                    _PENDING.remove(item)
                except ValueError:
                    pass
                raise QueryQueueExpired("SQL queue wait expired")
            _COND.wait(timeout=min(0.5, remaining))

    timer = threading.Timer(
        max_runtime_seconds(), _expire_running, args=(item["query_id"], item["query_key"])
    )
    timer.daemon = True
    timer.start()
    try:
        try:
            yield item
        except Exception:
            # Translate the DuckDB interrupt raised by page leave/replacement
            # into the queue's stable cancellation response.
            _raise_if_canceled(item)
            raise
        _raise_if_canceled(item)
    finally:
        timer.cancel()
        with _COND:
            if _CURRENT is item:
                _CURRENT = None
            _COND.notify_all()


def snapshot() -> dict:
    with _COND:
        _drop_stale_locked(time.monotonic())
        return {
            "running": bool(_CURRENT),
            "pending": len(_PENDING),
            "stale_seconds": stale_seconds(),
            "max_runtime_seconds": max_runtime_seconds(),
        }
