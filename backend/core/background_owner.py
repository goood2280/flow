"""Single cross-process owner for shared background schedulers.

Every API/worker process may join the election, but only the holder of one
renewed lease starts schedulers that read or write shared data.  Process-local
services (request dispatch, memory guards and RAM-only maintenance) stay out of
this coordinator.
"""
from __future__ import annotations

import atexit
import logging
import os
import threading
import time
from collections.abc import Callable

from core import shared_lease

LEASE_NAME = "flow_background_services_owner_v1"

_LOCK = threading.Lock()
_STARTED = False
_SERVICES_STARTED = False
_OWNER = threading.Event()
_THREAD: threading.Thread | None = None
_ACQUIRED_AT = 0.0
_LAST_ERROR = ""


def _env_float(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def lease_ttl_sec() -> float:
    return _env_float("FLOW_BACKGROUND_OWNER_TTL_SEC", 60.0, 20.0, 600.0)


def renew_interval_sec() -> float:
    return min(
        lease_ttl_sec() / 3.0,
        _env_float("FLOW_BACKGROUND_OWNER_RENEW_SEC", 10.0, 2.0, 60.0),
    )


def is_owner() -> bool:
    """Return True only while this process still holds the shared lease."""
    if not _OWNER.is_set():
        return False
    if shared_lease.owned(LEASE_NAME):
        return True
    _OWNER.clear()
    return False


def _release() -> None:
    if _OWNER.is_set():
        shared_lease.release(LEASE_NAME)
    _OWNER.clear()


def _election_loop(on_acquired: Callable[[], None], logger: logging.Logger) -> None:
    global _SERVICES_STARTED, _ACQUIRED_AT, _LAST_ERROR
    retry = _env_float("FLOW_BACKGROUND_OWNER_RETRY_SEC", 5.0, 1.0, 60.0)
    while True:
        try:
            if not is_owner():
                if shared_lease.try_acquire(LEASE_NAME, ttl_sec=lease_ttl_sec()):
                    _OWNER.set()
                    _ACQUIRED_AT = time.time()
                    _LAST_ERROR = ""
                    logger.info("background scheduler ownership acquired (%s)", shared_lease.owner_id())
                    if not _SERVICES_STARTED:
                        on_acquired()
                        _SERVICES_STARTED = True
                else:
                    time.sleep(retry)
                    continue
            if not shared_lease.renew(LEASE_NAME, ttl_sec=lease_ttl_sec()):
                _OWNER.clear()
                _LAST_ERROR = "lease renewal failed"
                logger.error("background scheduler ownership lost; scheduled loops will pause")
        except Exception as exc:
            _OWNER.clear()
            _LAST_ERROR = f"{type(exc).__name__}: {exc}"
            logger.exception("background scheduler owner election failed")
        time.sleep(renew_interval_sec())


def start(on_acquired: Callable[[], None], logger: logging.Logger | None = None) -> bool:
    """Start the local election participant once."""
    global _STARTED, _THREAD
    log = logger or logging.getLogger("flow.background_owner")
    with _LOCK:
        if _STARTED:
            return False
        _STARTED = True
        _THREAD = threading.Thread(
            target=_election_loop,
            args=(on_acquired, log),
            daemon=True,
            name="flow-background-owner",
        )
        _THREAD.start()
    return True


def snapshot() -> dict:
    return {
        "lease": LEASE_NAME,
        "owner": shared_lease.holder(LEASE_NAME),
        "this_process": shared_lease.owner_id(),
        "is_owner": is_owner(),
        "services_started": _SERVICES_STARTED,
        "acquired_at": _ACQUIRED_AT,
        "last_error": _LAST_ERROR,
        "ttl_sec": lease_ttl_sec(),
    }


atexit.register(_release)
