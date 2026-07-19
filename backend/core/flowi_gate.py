"""core/flowi_gate.py — Flow-i 운용 게이트 (권한 + 동시성 + 리소스 양보).

Flow-i 는 '여러 DB 를 가볍게 조회해 업무를 보조'하는 기능이다. SplitTable 조회 등
서버의 주 작업을 밀어내지 않도록:

  1) 접근 권한 — tabs 토큰 "flowi" 가 있는 유저(또는 admin)만 사용.
  2) 동시 실행 상한 — 기본 2 (env FLOW_FLOWI_MAX_CONCURRENCY). 꽉 차면 짧게
     대기 후(기본 6초) "지금 사용이 많다"는 안내를 돌려준다.
  3) 리소스 양보 — 프로세스가 이미 바쁘면(CPU admit 임계 초과·메모리 여유 부족)
     새 Flow-i 실행을 정중히 미룬다. LLM 추론은 원격이라 로컬 비용은 도구
     실행(Polars/DuckDB 스캔)뿐 — 동시 2회 + admit 게이트로 대략 2코어 수준을
     넘지 않게 억제한다.

모든 게이트는 채팅 응답(안내 문구)으로 표면화된다 — HTTP 에러가 아니다.
"""
from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

from core import runtime_limits

_MAX_CONCURRENCY_ENV = "FLOW_FLOWI_MAX_CONCURRENCY"
_QUEUE_WAIT_ENV = "FLOW_FLOWI_QUEUE_WAIT_SEC"
_CPU_ADMIT_ENV = "FLOW_FLOWI_CPU_ADMIT_CORES"
_MEM_RESERVE_ENV = "FLOW_FLOWI_MEMORY_RESERVE_GB"

_DEFAULT_MAX_CONCURRENCY = 2
_DEFAULT_QUEUE_WAIT_S = 6.0
_DEFAULT_MEM_RESERVE_GB = 2.0

_LOCK = threading.Lock()
_ACTIVE: dict[str, int] = {}          # username -> active count
_WAITING = 0


class FlowiBusy(Exception):
    """게이트가 실행을 미룬 경우 — .message 는 사용자 안내 문구."""

    def __init__(self, message: str, info: dict[str, Any]):
        super().__init__(message)
        self.message = message
        self.info = info


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(os.environ.get(name, "") or default)
    except Exception:
        value = default
    return max(lo, min(hi, value))


def _env_float(name: str, default: float, lo: float, hi: float) -> float:
    try:
        value = float(os.environ.get(name, "") or default)
    except Exception:
        value = default
    return max(lo, min(hi, value))


def max_concurrency() -> int:
    return _env_int(_MAX_CONCURRENCY_ENV, _DEFAULT_MAX_CONCURRENCY, 1, 16)


def queue_wait_seconds() -> float:
    return _env_float(_QUEUE_WAIT_ENV, _DEFAULT_QUEUE_WAIT_S, 0.0, 60.0)


def cpu_admit_cores() -> float:
    """이 값 이상으로 프로세스가 이미 CPU 를 태우고 있으면 새 Flow-i 를 미룬다.

    기본 max(1, cpu_budget - 2) — 5코어 호스트(budget 4)면 2코어. 즉 서버가
    다른 작업으로 2코어 이상 쓰는 중엔 Flow-i 가 양보한다."""
    default = max(1.0, runtime_limits.cpu_budget_cores() - 2.0)
    return _env_float(_CPU_ADMIT_ENV, default, 0.5, 1024.0)


def memory_reserve_gb() -> float:
    return _env_float(_MEM_RESERVE_ENV, _DEFAULT_MEM_RESERVE_GB, 0.5, 64.0)


# ── 접근 권한 ────────────────────────────────────────────────────────────────
def access_allowed(user: dict[str, Any] | None) -> bool:
    """tabs 토큰 "flowi" 보유(또는 admin) 여부. users.csv 를 재조회해 세션
    스냅샷보다 최신 권한을 본다 (관리자가 방금 부여/회수한 경우 즉시 반영)."""
    role = str((user or {}).get("role") or "")
    if role == "admin":
        return True
    username = str((user or {}).get("username") or "").strip()
    if not username:
        return False
    tabs_raw: Any = (user or {}).get("tabs", "")
    try:
        from routers.auth import read_users
        for row in read_users():
            if str(row.get("username") or "").strip() == username:
                if str(row.get("role") or "") == "admin":
                    return True
                tabs_raw = row.get("tabs", tabs_raw)
                break
    except Exception:
        pass
    if str(tabs_raw or "").strip() == "__all__":
        return True
    try:
        from core.auth import parse_tab_tokens
        tabs, _subs = parse_tab_tokens(tabs_raw)
    except Exception:
        return False
    return "flowi" in tabs


def denied_message(user: dict[str, Any] | None) -> str:
    username = str((user or {}).get("username") or "user")
    return (
        f"현재 계정({username})에는 Flow-i 사용 권한이 없습니다.\n"
        "관리자에게 'Flow-i' 권한을 요청한 뒤 다시 시도해주세요. (관리자 > 유저 > 권한)"
    )


def denied_payload(user: dict[str, Any] | None) -> dict[str, Any]:
    """채팅 응답 형태의 권한 거부 페이로드 (HTTP 에러 아님)."""
    return {
        "ok": True,
        "type": "answer",
        "handled": True,
        "intent": "flowi_access_denied",
        "blocked": True,
        "answer": denied_message(user),
        "missing_permission": "flowi",
        "llm": {"available": False, "used": False},
    }


# ── 리소스 admit ─────────────────────────────────────────────────────────────
def _resource_block_reason() -> str:
    """새 실행을 미뤄야 하면 사유 문자열, 아니면 ""."""
    try:
        if runtime_limits.process_memory_high(reserve_gb=memory_reserve_gb()):
            return "memory"
    except Exception:
        pass
    try:
        snap = runtime_limits.process_cpu_snapshot(sample_seconds=0.15)
        if float(snap.get("process_cpu_cores") or 0.0) >= cpu_admit_cores():
            return "cpu"
    except Exception:
        pass
    return ""


def _busy_message(reason: str, active: int, waiting: int) -> str:
    limit = max_concurrency()
    if reason == "concurrency":
        return (
            f"지금 Flow-i 를 사용하는 요청이 많습니다 (동시 {active}/{limit}, 대기 {waiting}건).\n"
            "잠시 후 다시 시도해주세요 — 앞선 실행이 끝나면 바로 처리됩니다."
        )
    if reason == "memory":
        return (
            "서버 메모리 여유가 부족해 Flow-i 실행을 잠시 미루고 있습니다.\n"
            "잠시 후 다시 시도해주세요."
        )
    return (
        "서버가 다른 작업(조회/캐시 빌드)으로 바빠 Flow-i 실행을 잠시 미룹니다.\n"
        "잠시 후 다시 시도해주세요."
    )


def busy_payload(busy: "FlowiBusy") -> dict[str, Any]:
    """채팅 응답 형태의 대기 안내 페이로드."""
    return {
        "ok": True,
        "type": "answer",
        "handled": True,
        "intent": "flowi_busy",
        "answer": busy.message,
        "busy": dict(busy.info),
        "llm": {"available": False, "used": False},
    }


# ── 동시 실행 게이트 ─────────────────────────────────────────────────────────
def _active_total() -> int:
    return sum(_ACTIVE.values())


def snapshot() -> dict[str, Any]:
    with _LOCK:
        return {
            "active": _active_total(),
            "waiting": _WAITING,
            "max_concurrency": max_concurrency(),
            "users": dict(_ACTIVE),
        }


@contextmanager
def slot(username: str = "", role: str = "user") -> Iterator[None]:
    """Flow-i 실행 슬롯. 진입 실패 시 FlowiBusy — 응답은 busy_payload() 로.

    admin 은 admission(동시성/리소스) 검사를 우회하되 active 로는 집계된다
    (운영자 진단은 막지 않고, 유저 쪽 카운트에는 반영)."""
    global _WAITING
    user = str(username or "").strip() or "user"
    is_admin = str(role or "") == "admin"
    limit = max_concurrency()

    if not is_admin:
        reason = _resource_block_reason()
        if reason:
            raise FlowiBusy(_busy_message(reason, _active_total(), 0), {
                "reason": reason, "active": _active_total(),
                "waiting": 0, "max_concurrency": limit,
            })
        deadline = time.monotonic() + queue_wait_seconds()
        waited = False
        while True:
            with _LOCK:
                if _active_total() < limit:
                    _ACTIVE[user] = _ACTIVE.get(user, 0) + 1
                    if waited:
                        _WAITING = max(0, _WAITING - 1)
                    break
                if not waited:
                    waited = True
                    _WAITING += 1
            if time.monotonic() >= deadline:
                with _LOCK:
                    if waited:
                        _WAITING = max(0, _WAITING - 1)
                    active, waiting = _active_total(), _WAITING
                raise FlowiBusy(_busy_message("concurrency", active, waiting), {
                    "reason": "concurrency", "active": active,
                    "waiting": waiting, "max_concurrency": limit,
                })
            time.sleep(0.2)
    else:
        with _LOCK:
            _ACTIVE[user] = _ACTIVE.get(user, 0) + 1

    try:
        yield
    finally:
        with _LOCK:
            left = _ACTIVE.get(user, 0) - 1
            if left > 0:
                _ACTIVE[user] = left
            else:
                _ACTIVE.pop(user, None)
