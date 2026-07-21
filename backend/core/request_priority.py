"""core/request_priority.py — user requests outrank background work.

Background jobs (pivot cache builds, RAM cache warmup, periodic S3 sync)
call yield_to_users() between work units. The API middleware stamps a
timestamp on every user-facing request; background work waits until the
server has been quiet for a short grace period (bounded by max_wait_sec,
so background work always makes progress even under constant traffic).

An activity timestamp is used instead of an in-flight counter so long-lived
streaming/SSE connections cannot starve background work forever.
"""
from __future__ import annotations

import os
import threading
import time

_LOCK = threading.Lock()
_LAST_USER_REQUEST_TS = 0.0

# 타이머성 폴링 endpoint 는 사용자 조작으로 치지 않는다 (idle 화면이 백그라운드
# 작업을 계속 밀어내는 것을 방지).
_IGNORED_PATH_SUFFIXES = ("/health",)
_IGNORED_PATHS = (
    "/api/s3ingest/status-by-target",
    "/api/splittable/cache/pivot/status",
)


def _env_float(name: str, default: float, lo: float, hi: float) -> float:
    try:
        value = float(os.environ.get(name, "") or default)
    except Exception:
        value = default
    return max(lo, min(hi, value))


def note_api_request(path: str) -> None:
    """Called by the middleware for every /api request."""
    p = str(path or "")
    if p in _IGNORED_PATHS or p.endswith(_IGNORED_PATH_SUFFIXES):
        return
    global _LAST_USER_REQUEST_TS
    with _LOCK:
        _LAST_USER_REQUEST_TS = time.monotonic()


def seconds_since_user_activity() -> float:
    with _LOCK:
        last = _LAST_USER_REQUEST_TS
    if last <= 0:
        return float("inf")
    return max(0.0, time.monotonic() - last)


def users_active(quiet_for_sec: float | None = None) -> bool:
    quiet = quiet_for_sec if quiet_for_sec is not None else _env_float(
        "FLOW_BACKGROUND_YIELD_QUIET_SEC", 2.0, 0.0, 60.0)
    return seconds_since_user_activity() < quiet


def yield_to_users(max_wait_sec: float = 20.0, quiet_for_sec: float | None = None,
                   step_sec: float = 0.5) -> float:
    """Block the calling background thread while users are active.

    Returns the seconds actually waited. Never waits longer than
    max_wait_sec so background work cannot be starved indefinitely."""
    quiet = quiet_for_sec if quiet_for_sec is not None else _env_float(
        "FLOW_BACKGROUND_YIELD_QUIET_SEC", 2.0, 0.0, 60.0)
    max_wait = _env_float("FLOW_BACKGROUND_YIELD_MAX_WAIT_SEC", max_wait_sec, 0.0, 600.0)
    waited = 0.0
    while waited < max_wait and seconds_since_user_activity() < quiet:
        time.sleep(step_sec)
        waited += step_sec
    return waited
