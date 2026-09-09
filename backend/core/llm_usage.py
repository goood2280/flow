"""Shared sliding-window budget: at most 30 provider attempts per 60 seconds."""
from contextlib import contextmanager
import json
import math
import os
import threading
import time

from core.paths import PATHS
from core.utils import save_json

_LOCK = threading.Lock()
WINDOW_SECONDS = 60


def _path():
    return PATHS.data_root / "llm" / "minute_usage.json"


def minute_limit():
    try:
        return max(0, min(30, int(os.environ.get("FLOW_LLM_MINUTE_CALL_LIMIT", "30"))))
    except ValueError:
        return 30


def _read(path, now):
    try:
        raw = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return []
    attempts = raw.get("attempts") if isinstance(raw, dict) else None
    if not isinstance(attempts, list) or any(
        isinstance(t, bool) or not isinstance(t, (float, int)) or not math.isfinite(t) or t < 0
        for t in attempts
    ):
        raise ValueError("invalid LLM sliding-window counter")
    # Future timestamps remain reserved when a host clock moves backwards.
    return sorted(t for t in attempts if t > now - WINDOW_SECONDS)


@contextmanager
def _store_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, open(path.with_suffix(".lock"), "a+b") as handle:
        if os.name == "nt":
            import msvcrt
            if handle.seek(0, 2) == 0:
                handle.write(b"\0")
                handle.flush()
            deadline = time.monotonic() + 5
            while True:
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("LLM usage counter is busy")
                    time.sleep(0.02)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def snapshot():
    limit = minute_limit()
    try:
        now = time.time()
        attempts = _read(_path(), now)
        retry = max(1, math.ceil(attempts[0] + WINDOW_SECONDS - now)) if attempts and len(attempts) >= limit else 0
        return {"minute_call_limit": limit, "minute_calls_used": len(attempts),
                "minute_calls_remaining": max(0, limit - len(attempts)),
                "window_seconds": WINDOW_SECONDS, "retry_after_s": retry}
    except Exception:
        return {"minute_call_limit": limit, "minute_calls_used": None,
                "minute_calls_remaining": 0, "window_seconds": WINDOW_SECONDS,
                "usage_unavailable": True}


def reserve_attempt():
    """Reserve before every outgoing attempt, shared across hosts and retries."""
    try:
        path = _path()
        with _store_lock(path):
            now = time.time()
            attempts = _read(path, now)
            if len(attempts) >= minute_limit():
                retry = max(1, math.ceil(attempts[0] + WINDOW_SECONDS - now)) if attempts else WINDOW_SECONDS
                return f"llm minute call limit reached; retry after {retry}s"
            save_json(path, {"attempts": [*attempts, now]})
        return ""
    except Exception:
        return "llm usage counter unavailable; no provider call was sent"
