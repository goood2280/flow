"""Shared daily provider-attempt budget for the administrator LLM POC."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
import threading
import time

from core.paths import PATHS
from core.utils import save_json

_LOCK = threading.Lock()
_KST = timezone(timedelta(hours=9))


def _path():
    return PATHS.data_root / "llm" / "daily_usage.json"


def daily_limit():
    try:
        return max(0, min(30, int(os.environ.get("FLOW_LLM_DAILY_CALL_LIMIT", "30"))))
    except ValueError:
        return 30


def _today():
    return datetime.now(_KST).date().isoformat()


def _read(path, today):
    try:
        raw = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return {"date": today, "used": 0}
    # Corruption must not silently reset an exhausted daily allowance.
    if not isinstance(raw, dict) or not isinstance(raw.get("used"), int) or raw["used"] < 0:
        raise ValueError("invalid LLM usage counter")
    datetime.strptime(str(raw.get("date", "")), "%Y-%m-%d")
    return raw if raw["date"] >= today else {"date": today, "used": 0}


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
    limit = daily_limit()
    try:
        state = _read(_path(), _today())
        return {"daily_call_limit": limit, "daily_calls_used": state["used"],
                "daily_calls_remaining": max(0, limit - state["used"]), "daily_reset_timezone": "Asia/Seoul"}
    except Exception:
        return {"daily_call_limit": limit, "daily_calls_used": None,
                "daily_calls_remaining": 0, "daily_reset_timezone": "Asia/Seoul", "usage_unavailable": True}


def reserve_attempt():
    """Count each outgoing attempt, including retries and uncertain failures.

    Reserve before sending; a crash after reservation costs one slot rather
    than allowing a restart or another worker to overspend the shared account.
    """
    try:
        path = _path()
        with _store_lock(path):
            state = _read(path, _today())
            if state["used"] >= daily_limit():
                return "llm daily call limit reached (resets at midnight Asia/Seoul)"
            save_json(path, {"date": state["date"], "used": state["used"] + 1})
        return ""
    except Exception:
        return "llm usage counter unavailable; no provider call was sent"
