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
_ACTIVE_SPLITTABLE_REQUESTS = 0

_SPLITTABLE_PRIORITY_PREFIXES = (
    "/api/splittable/view",
    "/api/splittable/lot-ids",
    "/api/splittable/lot-candidates",
    "/api/splittable/column-values",
)

# 타이머성 폴링 endpoint 는 사용자 조작으로 치지 않는다 (idle 화면이 백그라운드
# 작업을 계속 밀어내는 것을 방지).
_IGNORED_PATH_SUFFIXES = ("/health",)
_IGNORED_PATHS = (
    "/api/s3ingest/status-by-target",
    "/api/splittable/cache/pivot/status",
    # 캐시 관리 화면(My_RamCache)이 스캔 중 2.5초마다 두드리는 진행 조회들.
    # 사용자 조작으로 치면 '진행을 지켜보는 것만으로' users_active() 가 계속
    # True 라, idle 을 기다리는 랏(lookup) 캐시 빌드가 30분 대기 끝에
    # local_heavy_waiting_for_idle 로 취소되고 재큐 → 영원히 시작 못 한다.
    "/api/splittable/ram-cache/scan-status",
    "/api/splittable/ram-cache/overview",
    "/api/splittable/ram-cache/contents",
    "/api/splittable/memory/overview",
    "/api/splittable/product-cache/status",
    "/api/splittable/root-lot-cache/status",
    "/api/splittable/cache-event-log",
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


def begin_splittable_request(path: str) -> bool:
    """Keep background work yielding for the full lifetime of a SplitTable read."""
    if not any(str(path or "").startswith(prefix) for prefix in _SPLITTABLE_PRIORITY_PREFIXES):
        return False
    global _ACTIVE_SPLITTABLE_REQUESTS, _LAST_USER_REQUEST_TS
    with _LOCK:
        _ACTIVE_SPLITTABLE_REQUESTS += 1
        _LAST_USER_REQUEST_TS = time.monotonic()
    return True


def end_splittable_request(active: bool) -> None:
    if not active:
        return
    global _ACTIVE_SPLITTABLE_REQUESTS, _LAST_USER_REQUEST_TS
    with _LOCK:
        _ACTIVE_SPLITTABLE_REQUESTS = max(0, _ACTIVE_SPLITTABLE_REQUESTS - 1)
        _LAST_USER_REQUEST_TS = time.monotonic()


def seconds_since_user_activity() -> float:
    with _LOCK:
        last = _LAST_USER_REQUEST_TS
        active_split = _ACTIVE_SPLITTABLE_REQUESTS
    if active_split > 0:
        return 0.0
    if last <= 0:
        return float("inf")
    return max(0.0, time.monotonic() - last)


def users_active(quiet_for_sec: float | None = None) -> bool:
    quiet = quiet_for_sec if quiet_for_sec is not None else _env_float(
        "FLOW_BACKGROUND_YIELD_QUIET_SEC", 2.0, 0.0, 60.0)
    with _LOCK:
        if _ACTIVE_SPLITTABLE_REQUESTS > 0:
            return True
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
    while waited < max_wait and users_active(quiet_for_sec=quiet):
        time.sleep(step_sec)
        waited += step_sec
    return waited
