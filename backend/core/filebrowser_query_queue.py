"""Execution queue for interactive File Browser SQL scans.

Several users' scans run at the same time (``max_concurrent()``; large host 3,
otherwise 2). A single server-wide slot made a short query wait behind another
user's multi-minute scan (up to the 120 s queue expiry).

What stays serialized:
- one running scan per user (``per_user_limit()``, default 1) — a user's second
  tab waits in FIFO order as before instead of taking every slot;
- one running scan per ``query_key`` (user + source). DuckDB keeps only the
  newest connection per key and interrupts the older one, so two scans with the
  same key must never overlap;
- a page (session) owns only its newest request: an older queued or running
  request of the same session is canceled.

A scan that starts next to others gets a share of the DuckDB thread budget
(8 → 4 → 2 …) so concurrent scans do not multiply the thread count, and no
parallel scan starts while process/host memory is tight — it waits for the
running ones instead. The first scan always starts as it did before.
"""
from __future__ import annotations

import contextlib
import itertools
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
_RUNNING: list[dict[str, Any]] = []
_SEQ = itertools.count(1)
_MEMORY_CHECK: dict[str, float] = {"at": 0.0, "ok": 1.0}
_MEMORY_CHECK_TTL_SEC = 1.0


def _env_seconds(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.environ.get(name, "") or default)
    except Exception:
        value = default
    return max(low, min(high, value))


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.environ.get(name, "") or default)
    except Exception:
        value = default
    return max(low, min(high, value))


def stale_seconds() -> float:
    return _env_seconds("FLOW_FILEBROWSER_SQL_QUEUE_STALE_SEC", 120.0, 10.0, 3600.0)


def max_runtime_seconds() -> float:
    return _env_seconds("FLOW_FILEBROWSER_SQL_MAX_RUNTIME_SEC", 300.0, 30.0, 7200.0)


def max_pending() -> int:
    return _env_int("FLOW_FILEBROWSER_SQL_QUEUE_MAX", 24, 1, 64)


def _default_concurrency() -> int:
    try:
        from core.runtime_limits import is_large_profile
        if is_large_profile():
            return 3
    except Exception:
        pass
    return 2


def max_concurrent() -> int:
    """Server-wide running scans. ``FLOW_FILEBROWSER_SQL_CONCURRENCY`` (1~8)."""
    return _env_int("FLOW_FILEBROWSER_SQL_CONCURRENCY", _default_concurrency(), 1, 8)


def per_user_limit() -> int:
    """Running scans per user. ``FLOW_FILEBROWSER_SQL_PER_USER`` (1~8, default 1)."""
    return _env_int("FLOW_FILEBROWSER_SQL_PER_USER", 1, 1, 8)


def _duckdb_thread_budget() -> int:
    try:
        return max(1, int(duckdb_engine.thread_budget()))
    except Exception:
        return 1


def _parallel_memory_ok_locked(now: float) -> bool:
    """True unless memory is tight. Cached briefly: waiters re-check every 0.5 s."""
    if now - _MEMORY_CHECK["at"] < _MEMORY_CHECK_TTL_SEC:
        return bool(_MEMORY_CHECK["ok"])
    ok = True
    try:
        from core.runtime_limits import process_memory_high
        ok = not process_memory_high()
    except Exception:
        ok = True
    _MEMORY_CHECK["at"] = now
    _MEMORY_CHECK["ok"] = 1.0 if ok else 0.0
    return ok


def _startable_locked(now: float) -> list[dict[str, Any]]:
    """Pending items allowed to start now, oldest first.

    An item blocked by its user's limit or by a running scan with the same
    query_key also blocks that user's/key's later items, so each keeps FIFO order
    while other users' items may pass it.
    """
    free = max_concurrent() - len(_RUNNING)
    if free <= 0 or not _PENDING:
        return []
    user_limit = per_user_limit()
    user_counts: dict[str, int] = {}
    busy_keys: set[str] = set()
    for running in _RUNNING:
        user_counts[running["username"]] = user_counts.get(running["username"], 0) + 1
        if running["query_key"]:
            busy_keys.add(running["query_key"])
    out: list[dict[str, Any]] = []
    for item in _PENDING:
        if len(out) >= free:
            break
        key = item["query_key"]
        user = item["username"]
        if (key and key in busy_keys) or user_counts.get(user, 0) >= user_limit:
            if key:
                busy_keys.add(key)
            continue
        out.append(item)
        user_counts[user] = user_counts.get(user, 0) + 1
        if key:
            busy_keys.add(key)
    if out and _RUNNING and not _parallel_memory_ok_locked(now):
        return []
    return out


def _cancel_item_locked(item: dict[str, Any], reason: str) -> None:
    item["canceled"] = True
    item["cancel_reason"] = reason


def _remove_pending_locked(item: dict[str, Any]) -> None:
    for index, queued in enumerate(_PENDING):
        if queued is item:
            del _PENDING[index]
            return


def _drop_stale_locked(now: float) -> None:
    ttl = stale_seconds()
    for item in list(_PENDING):
        if now - float(item["created_mono"]) >= ttl:
            _cancel_item_locked(item, "queue_expired")
            _remove_pending_locked(item)


def cancel(*, username: str, session_id: str, query_id: str = "", reason: str = "page_left") -> dict:
    """Remove matching queued work and interrupt it when it is already running."""
    removed = 0
    query_keys: list[str] = []
    with _COND:
        for item in list(_PENDING):
            if item["username"] != username or item["session_id"] != session_id:
                continue
            if query_id and item["query_id"] != query_id:
                continue
            _cancel_item_locked(item, reason)
            _remove_pending_locked(item)
            removed += 1
        for running in _RUNNING:
            if (
                running["username"] == username
                and running["session_id"] == session_id
                and (not query_id or running["query_id"] == query_id)
            ):
                _cancel_item_locked(running, reason)
                query_keys.append(str(running.get("query_key") or ""))
        _COND.notify_all()
    for query_key in query_keys:
        if query_key:
            duckdb_engine.interrupt_query(query_key)
    return {"ok": True, "removed": removed, "interrupted": bool(query_keys)}


def _expire_running(item: dict[str, Any]) -> None:
    with _COND:
        if not any(running is item for running in _RUNNING):
            return
        _cancel_item_locked(item, "runtime_expired")
        _COND.notify_all()
    duckdb_engine.interrupt_query(str(item.get("query_key") or ""))


def _raise_if_canceled(item: dict[str, Any]) -> None:
    if not item["canceled"]:
        return
    reason = str(item["cancel_reason"] or "canceled")
    if reason in {"runtime_expired", "queue_expired"}:
        raise QueryQueueExpired(reason)
    raise QueryQueueCanceled(reason)


@contextlib.contextmanager
def execute(*, username: str, session_id: str, query_id: str, query_key: str):
    """Wait for a slot (FIFO per user/key), then run one interactive SQL scan."""
    item = {
        "seq": next(_SEQ),
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
                _remove_pending_locked(old)
        for running in _RUNNING:
            if running["username"] == item["username"] and running["session_id"] == item["session_id"]:
                _cancel_item_locked(running, "replaced")
                duckdb_engine.interrupt_query(str(running.get("query_key") or ""))
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
            if any(ready is item for ready in _startable_locked(now)):
                _remove_pending_locked(item)
                _RUNNING.append(item)
                item["started_mono"] = now
                budget = _duckdb_thread_budget()
                item["duckdb_threads"] = max(min(2, budget), budget // len(_RUNNING))
                # Another free slot may now belong to the next waiter.
                _COND.notify_all()
                break
            remaining = stale_seconds() - (now - float(item["created_mono"]))
            if remaining <= 0:
                _cancel_item_locked(item, "queue_expired")
                _remove_pending_locked(item)
                raise QueryQueueExpired("SQL queue wait expired")
            _COND.wait(timeout=min(0.5, remaining))

    timer = threading.Timer(max_runtime_seconds(), _expire_running, args=(item,))
    timer.daemon = True
    timer.start()
    try:
        try:
            with duckdb_engine.thread_limit(item["duckdb_threads"]):
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
            _RUNNING[:] = [running for running in _RUNNING if running is not item]
            _COND.notify_all()


def snapshot() -> dict:
    with _COND:
        _drop_stale_locked(time.monotonic())
        return {
            "running": bool(_RUNNING),
            "running_count": len(_RUNNING),
            "max_concurrent": max_concurrent(),
            "per_user_limit": per_user_limit(),
            "pending": len(_PENDING),
            "stale_seconds": stale_seconds(),
            "max_runtime_seconds": max_runtime_seconds(),
        }
