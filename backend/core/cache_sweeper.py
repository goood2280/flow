"""core/cache_sweeper.py — periodic TTL sweep for module-level dict caches.

Most in-process caches use lazy eviction (expiry checked on access), so
entries whose keys are never requested again stay resident forever and
process memory grows over uptime. Modules register a sweep callback here;
a single daemon thread runs all callbacks every SWEEP_INTERVAL_SEC.
"""
from __future__ import annotations
import logging
import threading
import time
from typing import Callable

logger = logging.getLogger("flow.cache_sweeper")

SWEEP_INTERVAL_SEC = 300.0

_CALLBACKS: list[tuple[str, Callable[[], None]]] = []
_LOCK = threading.Lock()
_STARTED = False


def register(name: str, fn: Callable[[], None]) -> None:
    """Register a zero-arg sweep callback. Exceptions are logged, not raised."""
    global _STARTED
    with _LOCK:
        _CALLBACKS.append((name, fn))
        if _STARTED:
            return
        threading.Thread(target=_loop, daemon=True, name="cache-sweeper").start()
        _STARTED = True


def register_ttl_dict(name: str, cache: dict, ttl_sec: float,
                      lock: threading.Lock | None = None,
                      clock: Callable[[], float] = time.time) -> None:
    """Convenience for the common `cache[key] = (timestamp, ...)` shape.
    Entries older than ttl_sec (by the tuple's first element) are removed.
    Pass clock=time.monotonic for caches stamped with monotonic time."""

    def _sweep() -> None:
        now = clock()
        def _prune() -> int:
            removed = 0
            for key, value in list(cache.items()):
                try:
                    ts = float(value[0])
                except Exception:
                    continue
                if now - ts > ttl_sec:
                    cache.pop(key, None)
                    removed += 1
            return removed
        removed = 0
        if lock is not None:
            with lock:
                removed = _prune()
        else:
            removed = _prune()
        if removed:
            logger.debug("cache sweep %s removed %d entries", name, removed)

    register(name, _sweep)


def _loop() -> None:
    while True:
        time.sleep(SWEEP_INTERVAL_SEC)
        with _LOCK:
            callbacks = list(_CALLBACKS)
        for name, fn in callbacks:
            try:
                fn()
            except Exception as exc:
                logger.debug("cache sweep %s failed: %s", name, exc)
