"""core/heavy_jobs.py — 운영 단일 서버의 무거운 작업 실행 계층.

Flow 는 운영 서버 한 대에서 모든 작업을 처리한다(2026-09-29, 개발서버 오프로드
폐지). 예전 `core/worker_dispatch.py` 는 무거운 작업을 공유 폴더 파일 큐로 개발
worker 에 넘기고, worker 가 없을 때만 운영에서 로컬 실행했다. 이제 넘길 곳이 없으므로
**그 로컬 실행 경로만** 남겼다.

`run_heavy()` 가 하는 일:

- 캐시 산출 작업(`CACHE_BUILD_KINDS`)은 서버 공용 스캔 슬롯(`core.scan_gate.exclusive`)을
  잡는다 — 한 서버에서 백그라운드 캐시 빌드·스캔은 하나만 돈다. 대형 서버에서는 조회
  전제 캐시(`REQUIRED_READ_CACHE_KINDS`)가 조회 레인을, Auto report 는 자기 레인을 쓴다.
- 그 외 무거운 작업은 프로세스당 1개(`_LOCAL_HEAVY_GATE`)로 줄 세운다.
- 대화형 작업(`INTERACTIVE_KINDS`: 홈 에이전트·파일탐색기 SQL·차트 원본 조회)은
  줄을 세우지 않는다 — 긴 캐시 빌드 뒤에서 화면이 멈추면 안 된다.
- `idle_only=True` 면 사용자 요청이 조용해질 때까지 기다린다(백그라운드 유지보수).
  조회의 전제 캐시(`REQUIRED_READ_CACHE_KINDS`)는 짧은 유예 뒤 그냥 진행한다.
- 시작 전 메모리 admission: 프로세스 한도 초과나 호스트 여유 메모리 부족이면 잠시
  기다렸다가, 끝내 부족하면 실행하지 않고 `{"ok": False, "error": ...}` 를 돌려준다.
- 끝나면 해제 메모리를 OS 에 돌려준다(`core.memory_trim`).

락 순서 주의 — 캐시 산출 작업은 `_LOCAL_HEAVY_GATE` 를 쓰지 않고 스캔 슬롯만 잡는다.
둘 다 잡으면 스캔 슬롯을 든 작업과 `_LOCAL_HEAVY_GATE` 를 든 작업이 서로를 기다리는
교착이 난다.

환경변수:
  FLOW_LOCAL_HEAVY_QUEUE_TIMEOUT_SEC   일반 heavy 작업 줄 대기 한도 (기본 600)
  FLOW_LOCAL_HEAVY_IDLE_WAIT_SEC       idle_only 작업의 사용자 조용함 대기 한도 (기본 1800)
  FLOW_LOCAL_HEAVY_IDLE_QUIET_SEC      "조용함" 판정 창 (기본 10)
  FLOW_REQUIRED_CACHE_IDLE_WAIT_SEC    조회 전제 캐시의 idle 유예 (기본 5)
  FLOW_LOCAL_HEAVY_MEMORY_WAIT_SEC     메모리 admission 대기 한도 (기본 120)
  FLOW_LOCAL_HEAVY_MIN_AVAILABLE_GB    호스트 여유 메모리 하한 (기본 총량×15%, 2.5~6GB)
"""
from __future__ import annotations

import contextlib
import logging
import os
import threading
import time
from typing import Callable

logger = logging.getLogger("flow.heavy_jobs")

_LOCAL_HEAVY_GATE = threading.Semaphore(1)
_STATS_LOCK = threading.Lock()
_STATS = {"ran": 0, "queue_timeout": 0, "idle_deferred": 0, "memory_guard": 0, "cache_gate_timeout": 0}
_RUNNING: dict[str, float] = {}

# 대화형 요청 — 캐시 빌드용 직렬화 게이트 뒤에 줄 세우지 않는다.
INTERACTIVE_KINDS = {"filebrowser_sql_query", "home_agent_turn", "chart_builder_run", "flowi_chat_turn"}

# 제품 단위 캐시 산출 작업 — 서버 공용 스캔 슬롯(core.scan_gate)을 잡는다.
CACHE_BUILD_KINDS = {
    "ml_lookup_cache_build",          # 랏(lookup) 캐시
    "splittable_match_cache_refresh",  # FAB 매칭 캐시
    "splittable_pivot_build",          # 제품 원본 pivot 캐시
    "splittable_fab_lot_index_build",  # FAB lot 인덱스
    "splittable_lot_progress_cache_refresh",  # WIP/latest-lot 공유 캐시
    "fab_matching_alert_scan",        # FAB source matching/reticle scan
    "et_tracker_scan",                # ET history/diff background scan
    "auto_report_generate",           # Auto report PPT 생성
    "auto_report_history_refresh",    # Auto report ET history
}

# 조회의 전제 캐시 — 사용자 요청이 계속 들어와도 짧은 유예 뒤 진행한다. 긴 idle 을
# 기다리면 평소 화면 폴링만으로 조회가 영영 막힌다. 대형 서버에서는 scan_gate 의
# 조회 레인을 써서 백그라운드 스캔 뒤에 줄 서지 않는다.
REQUIRED_READ_CACHE_KINDS = {
    "ml_lookup_cache_build",
    "splittable_pivot_build",
    "splittable_fab_lot_index_build",
    "splittable_lot_progress_cache_refresh",
}

# 캐시를 만들지 않지만 소형 서버에서는 캐시 빌드와 CPU 를 다투므로 공용 슬롯에 줄
# 세우는 작업. 대형 서버에서는 자기 레인(1칸)을 쓴다 — Auto report 는 PPT 생성
# 하위 프로세스가 끝날 때까지(최대 6시간) 슬롯을 들고 있어 그 뒤의 pivot·lookup
# 빌드가 전부 멈췄다.
OWN_LANE_ON_LARGE_HOST_KINDS = {"auto_report_generate"}
_REPORT_GATE = threading.Semaphore(1)


def _large_host() -> bool:
    try:
        from core import cache_budget

        return bool(cache_budget.large_host())
    except Exception:
        return False


def _uses_cache_slot(kind: str) -> bool:
    if kind in OWN_LANE_ON_LARGE_HOST_KINDS and _large_host():
        return False
    return kind in CACHE_BUILD_KINDS


def _env_float(name: str, default: float, lo: float, hi: float) -> float:
    try:
        value = float(os.environ.get(name) or default)
    except Exception:
        value = default
    return max(lo, min(hi, value))


def _bump(key: str) -> None:
    with _STATS_LOCK:
        _STATS[key] = _STATS.get(key, 0) + 1


def _cache_gate(kind: str, label: str, *, product: str = ""):
    """캐시 산출 작업이면 서버 공용 슬롯(조회 전제 캐시는 조회 레인)을, 아니면 통과용 더미."""
    if not _uses_cache_slot(kind):
        return contextlib.nullcontext(True)
    try:
        from core import scan_gate
        lane = "read" if kind in REQUIRED_READ_CACHE_KINDS else ""
        return scan_gate.exclusive(kind, label or kind, product=product, source="heavy_jobs", lane=lane)
    except Exception:
        logger.debug("scan gate unavailable for %s", kind, exc_info=True)
        return contextlib.nullcontext(True)


def _trim_after(label: str) -> None:
    """무거운 작업이 끝나면 allocator 가 쥐고 있는 해제 메모리를 OS 에 돌려준다."""
    try:
        from core import memory_trim

        memory_trim.trim(reason=f"task_done:{label}")
    except Exception:
        pass


def _wait_for_idle(kind: str, name: str) -> bool:
    required = kind in REQUIRED_READ_CACHE_KINDS
    idle_wait = (
        _env_float("FLOW_REQUIRED_CACHE_IDLE_WAIT_SEC", 5.0, 0.0, 60.0)
        if required else _env_float("FLOW_LOCAL_HEAVY_IDLE_WAIT_SEC", 1800.0, 0.0, 21600.0)
    )
    quiet_for = _env_float("FLOW_LOCAL_HEAVY_IDLE_QUIET_SEC", 10.0, 0.0, 300.0)
    deadline = time.monotonic() + idle_wait
    while True:
        try:
            from core import request_priority

            busy = request_priority.users_active(quiet_for_sec=quiet_for)
        except Exception:
            busy = False
        if not busy:
            return True
        if time.monotonic() >= deadline:
            if required:
                logger.info("required read cache proceeding after idle grace: %s", name)
                return True
            logger.info("heavy job deferred until next idle window: %s", name)
            return False
        time.sleep(2.0)


def _memory_block_reason() -> str:
    try:
        from core.runtime_limits import process_memory_high, process_memory_snapshot

        snap = process_memory_snapshot()
        total = float(snap.get("system_memory_total_gb") or 0.0)
        available = float(snap.get("system_memory_available_gb") or 0.0)
        default_reserve = max(2.5, min(6.0, total * 0.15)) if total > 0 else 3.0
        reserve = _env_float("FLOW_LOCAL_HEAVY_MIN_AVAILABLE_GB", default_reserve, 0.0, 64.0)
        if process_memory_high():
            return "process_memory_high"
        if 0 < available < reserve:
            return f"low_host_memory:{available:.1f}GB<{reserve:.1f}GB"
    except Exception:
        return ""
    return ""


def run_heavy(
    kind: str,
    fn: Callable[[], dict | None],
    *,
    label: str = "",
    idle_only: bool = False,
    product: str = "",
) -> dict | None:
    """무거운 작업을 이 서버에서 실행한다(admission·직렬화 포함).

    반환값은 `fn()` 의 결과다. admission 에서 막히면 `{"ok": False, "error": ...}`.
    `fn` 이 던진 예외는 그대로 전파한다(호출부의 기존 예외 처리를 유지)."""
    name = label or kind
    if kind in INTERACTIVE_KINDS:
        return fn()
    is_cache_build = _uses_cache_slot(kind)
    gate = _REPORT_GATE if kind in OWN_LANE_ON_LARGE_HOST_KINDS else _LOCAL_HEAVY_GATE
    if not is_cache_build:
        queue_timeout = _env_float("FLOW_LOCAL_HEAVY_QUEUE_TIMEOUT_SEC", 600.0, 1.0, 3600.0)
        if not gate.acquire(timeout=queue_timeout):
            logger.warning("heavy job queue timeout: %s", name)
            _bump("queue_timeout")
            return {"ok": False, "error": "local_heavy_queue_timeout"}
    try:
        if idle_only and not _wait_for_idle(kind, name):
            _bump("idle_deferred")
            return {"ok": False, "error": "local_heavy_waiting_for_idle"}
        deadline = time.monotonic() + _env_float("FLOW_LOCAL_HEAVY_MEMORY_WAIT_SEC", 120.0, 0.0, 1800.0)
        while True:
            reason = _memory_block_reason()
            if not reason:
                break
            if time.monotonic() >= deadline:
                logger.warning("heavy job memory admission timeout: %s (%s)", name, reason)
                _bump("memory_guard")
                return {"ok": False, "error": "local_heavy_memory_guard", "reason": reason}
            time.sleep(2.0)
        # 캐시 작업은 admission 을 지난 뒤에 슬롯을 잡는다 — 대기하는 동안 슬롯을
        # 붙들고 다른 캐시 작업을 막지 않는다.
        with _cache_gate(kind, name, product=product) as acquired:
            if not acquired:
                _bump("cache_gate_timeout")
                return {"ok": False, "error": "cache_gate_timeout", "reason": "다른 캐시 작업 진행 중"}
            _bump("ran")
            with _STATS_LOCK:
                _RUNNING[name] = time.time()
            try:
                return fn()
            finally:
                with _STATS_LOCK:
                    _RUNNING.pop(name, None)
    finally:
        if not is_cache_build:
            gate.release()
        _trim_after(name)


def status() -> dict:
    """관리자 모니터용 — 지금 도는 무거운 작업과 누적 카운터."""
    now = time.time()
    with _STATS_LOCK:
        running = [{"label": k, "elapsed_sec": round(now - v, 1)} for k, v in _RUNNING.items()]
        stats = dict(_STATS)
    return {"mode": "single_server", "running": running, "stats": stats}
