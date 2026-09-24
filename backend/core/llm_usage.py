"""Shared sliding-window budget: at most 25 provider attempts per 60 seconds."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import math
import os
import threading
import time

from core.paths import PATHS
from core.utils import save_json

_LOCK = threading.Lock()
WINDOW_SECONDS = 60
MAX_MINUTE_CALLS = 25
_TURN_USAGE = ContextVar("flow_llm_turn_usage", default=None)


@contextmanager
def turn_budget(limit=6):
    """A shared counter for all provider attempts inside one home request."""
    existing = _TURN_USAGE.get()
    if existing is not None:
        yield existing
        return
    usage = {"llm_calls_used": 0, "llm_call_limit": max(0, min(MAX_MINUTE_CALLS, int(limit)))}
    token = _TURN_USAGE.set(usage)
    try:
        yield usage
    finally:
        _TURN_USAGE.reset(token)


def current_turn():
    """현재 요청 스코프의 turn 카운터. turn_budget 밖이면 None."""
    return _TURN_USAGE.get()


def _path():
    return PATHS.data_root / "llm" / "minute_usage.json"


def minute_limit():
    try:
        return max(0, min(MAX_MINUTE_CALLS, int(os.environ.get("FLOW_LLM_MINUTE_CALL_LIMIT", str(MAX_MINUTE_CALLS)))))
    except ValueError:
        return MAX_MINUTE_CALLS


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
        retry = max(1, math.ceil(attempts[len(attempts) - limit] + WINDOW_SECONDS - now)) if limit and len(attempts) >= limit else (WINDOW_SECONDS if not limit else 0)
        return {"minute_call_limit": limit, "minute_calls_used": len(attempts),
                "minute_calls_remaining": max(0, limit - len(attempts)),
                "window_seconds": WINDOW_SECONDS, "retry_after_s": retry}
    except Exception:
        return {"minute_call_limit": limit, "minute_calls_used": None,
                "minute_calls_remaining": 0, "window_seconds": WINDOW_SECONDS,
                "usage_unavailable": True}


def reserve_attempt():
    """Reserve before every outgoing attempt, shared across hosts and retries.

    과금 정책: 실제 provider 전송 1건당 1차감. capability 재시도·429 재전송·
    연결 검사(probe=True)도 전송이 나가면 차감한다. 전송 없이 끝나는 경로
    (breaker open·disabled·설정 오류)는 차감하지 않는다."""
    try:
        path = _path()
        with _store_lock(path):
            turn = _TURN_USAGE.get()
            if turn is not None and turn["llm_calls_used"] >= turn["llm_call_limit"]:
                return "llm request call limit reached; split the remaining questions into a new request"
            now = time.time()
            attempts = _read(path, now)
            limit = minute_limit()
            if len(attempts) >= limit:
                retry = max(1, math.ceil(attempts[len(attempts) - limit] + WINDOW_SECONDS - now)) if limit else WINDOW_SECONDS
                return f"llm minute call limit reached; retry after {retry}s"
            save_json(path, {"attempts": [*attempts, now]})
            if turn is not None:
                turn["llm_calls_used"] += 1
        return ""
    except Exception:
        return "llm usage counter unavailable; no provider call was sent"
