"""core/memory_watchdog.py — 프로세스 메모리 감시 + 긴급 캐시 축출.

개발서버는 OOM 으로 죽으면 자동 재시작이 없어 수동 복구가 필요하다. 이 워치독은
백그라운드 데몬 스레드로 프로세스 메모리(effective = PSS > USS > RSS)를 주기
확인하고, 호스트 총 메모리 대비 비율이 임계값을 넘으면 인메모리 캐시를 큰
축출 비용이 낮은 순서(filebrowser preview → reformatize → SplitTable view
payload → 제품 RAM → root RAM)로 오래된 항목부터 긴급 제거해 OOM 직전에
압력을 낮춘다. 전체 clear 는 하지 않는다 — 안전 수준까지 필요한 만큼만.

임계값 (호스트 총 메모리 대비 프로세스 effective 비율, %):
  FLOW_MEMORY_WARN_PCT      경고 로그 (기본 75)
  FLOW_MEMORY_CRITICAL_PCT  긴급 축출 트리거 (기본 85)
  FLOW_MEMORY_SAFE_PCT      축출 목표 — 이 밑으로 내려오면 중단 (기본 70)
  FLOW_MEMORY_WATCHDOG_INTERVAL_SEC  확인 주기 (기본 15초; critical 후엔 5초)
  FLOW_MEMORY_WATCHDOG_ENABLE        0/false 로 끄기 (기본 켜짐)

resource_guard 가 메모리 가드 503 을 반환할 때 request_emergency_evict() 를
호출해 다음 요청이 통과할 수 있게 스스로 공간을 만든다 (디바운스 20초).

주의: 캐시 축출 후에도 RSS 는 allocator arena/mmap 때문에 즉시 줄지 않을 수
있다. gc.collect() 후 재측정하며, 한 번의 pass 에서 전 tier 를 최대 1회씩만
돌고 다음 주기에 재평가한다 — 측정 지연 때문에 캐시를 전부 비우는 과잉 축출을
막기 위함이다.
"""
from __future__ import annotations

import gc
import logging
import os
import threading
import time
from typing import Callable

logger = logging.getLogger("flow.memory_watchdog")

_WARN_PCT_DEFAULT = 75.0
_CRITICAL_PCT_DEFAULT = 85.0
_SAFE_PCT_DEFAULT = 70.0
_INTERVAL_SEC_DEFAULT = 15.0

# 개발서버 전용 — 더 적극적인 eviction 으로 OOM 방지
_DEV_WARN_PCT_DEFAULT = 60.0
_DEV_CRITICAL_PCT_DEFAULT = 70.0
_DEV_SAFE_PCT_DEFAULT = 50.0
_DEV_INTERVAL_SEC_DEFAULT = 10.0
_CRITICAL_RECHECK_SEC = 5.0
_WARN_LOG_COOLDOWN_SEC = 300.0
_EXTERNAL_TRIGGER_DEBOUNCE_SEC = 20.0
_EVENT_HISTORY_MAX = 20

_LOCK = threading.Lock()
_EVICT_LOCK = threading.Lock()
_STARTED = False
_BG_THREAD: threading.Thread | None = None
_LAST_WARN_LOG_TS = 0.0
_LAST_EXTERNAL_TRIGGER_TS = 0.0
_STATE: dict = {
    "last_check_ts": 0.0,
    "last_pct": 0.0,
    "last_effective_gb": 0.0,
    "last_total_gb": 0.0,
    "warn_count": 0,
    "evict_pass_count": 0,
    "total_freed_bytes": 0,
    "last_eviction": {},
    "events": [],  # 최근 축출 이벤트 (최대 _EVENT_HISTORY_MAX)
}


def _env_float(name: str, default: float, lo: float, hi: float) -> float:
    raw = os.environ.get(name, "")
    try:
        value = float(raw) if raw not in (None, "") else default
    except Exception:
        value = default
    return max(lo, min(hi, value))


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _is_prod_server() -> bool:
    """운영 서버 여부 — 개발서버면 watchdog 임계값을 낮춘다."""
    try:
        from core.worker_dispatch import server_role

        if server_role() == "worker":
            return False
    except Exception:
        pass
    try:
        from core.paths import PATHS

        return PATHS.is_prod
    except Exception:
        return True  # 판별 불가 시 안전하게 운영 기본값 사용


def enabled() -> bool:
    return _env_flag("FLOW_MEMORY_WATCHDOG_ENABLE", True)


def warn_pct() -> float:
    default = _WARN_PCT_DEFAULT if _is_prod_server() else _DEV_WARN_PCT_DEFAULT
    return _env_float("FLOW_MEMORY_WARN_PCT", default, 30.0, 99.0)


def critical_pct() -> float:
    # critical 은 warn 밑으로 내려갈 수 없다 — 잘못 설정해도 순서 보장.
    default = _CRITICAL_PCT_DEFAULT if _is_prod_server() else _DEV_CRITICAL_PCT_DEFAULT
    return max(warn_pct(), _env_float("FLOW_MEMORY_CRITICAL_PCT", default, 40.0, 99.0))


def safe_pct() -> float:
    # safe 는 warn 위로 올라갈 수 없다.
    default = _SAFE_PCT_DEFAULT if _is_prod_server() else _DEV_SAFE_PCT_DEFAULT
    return min(warn_pct(), _env_float("FLOW_MEMORY_SAFE_PCT", default, 20.0, 95.0))


def interval_sec() -> float:
    default = _INTERVAL_SEC_DEFAULT if _is_prod_server() else _DEV_INTERVAL_SEC_DEFAULT
    return _env_float("FLOW_MEMORY_WATCHDOG_INTERVAL_SEC", default, 3.0, 300.0)


def _memory_pct() -> tuple[float, float, float]:
    """(pct, effective_gb, total_gb). 호스트 총량 미상이면 프로세스 한도 기준 폴백."""
    try:
        from core.runtime_limits import process_memory_snapshot

        snap = process_memory_snapshot()
    except Exception:
        return 0.0, 0.0, 0.0
    effective_gb = float(snap.get("process_memory_effective_gb") or 0.0)
    if effective_gb <= 0:
        effective_gb = float(snap.get("process_rss_gb") or 0.0)
    total_gb = float(snap.get("system_memory_total_gb") or 0.0)
    if total_gb <= 0:
        total_gb = float(snap.get("process_memory_limit_gb") or 0.0)
    if total_gb <= 0 or effective_gb <= 0:
        return 0.0, effective_gb, total_gb
    return 100.0 * effective_gb / total_gb, effective_gb, total_gb


# ── 축출 tier — 축출 비용이 낮고 복구가 쉬운 순서. 각 함수는 (max_bytes)->freed.
#    lazy import 로 순환 import 를 피하고, 미로딩 모듈은 freed=0 으로 스킵된다.

def _evict_filebrowser(max_bytes: int) -> int:
    from core import filebrowser_cache

    return filebrowser_cache.emergency_evict(max_bytes)


def _evict_reformatize(max_bytes: int) -> int:
    from routers import reformatize

    return reformatize.emergency_evict(max_bytes)


def _evict_view_payload(max_bytes: int) -> int:
    from routers import splittable

    return splittable.emergency_evict_view_cache(max_bytes)


def _evict_product_ram(max_bytes: int) -> int:
    from routers import splittable

    return splittable.emergency_evict_product_ram(max_bytes)


def _evict_root_ram(max_bytes: int) -> int:
    from core import ml_table_lookup

    return ml_table_lookup.emergency_evict_root_ram(max_bytes)


_TIERS: tuple[tuple[str, Callable[[int], int]], ...] = (
    ("filebrowser_preview", _evict_filebrowser),
    ("reformatize", _evict_reformatize),
    ("splittable_view_payload", _evict_view_payload),
    ("splittable_product_ram", _evict_product_ram),
    ("splittable_root_ram", _evict_root_ram),
)


def _run_eviction_pass(trigger: str, pct: float, effective_gb: float, total_gb: float) -> dict:
    """단일 축출 pass — safe 수준까지 필요한 바이트를 tier 순서로 회수.

    반환: {"freed_bytes", "freed_by_tier", "pct_before", "pct_after", ...}
    """
    target = safe_pct()
    need = int(max(0.0, (pct - target) / 100.0) * total_gb * (1024 ** 3))
    if need <= 0:
        return {}
    freed_by_tier: dict[str, int] = {}
    freed_total = 0
    for name, fn in _TIERS:
        remaining = need - freed_total
        if remaining <= 0:
            break
        try:
            freed = int(fn(remaining) or 0)
        except Exception as exc:
            logger.debug("memory watchdog tier %s evict failed: %s", name, exc)
            freed = 0
        if freed > 0:
            freed_by_tier[name] = freed
            freed_total += freed
    try:
        gc.collect()
    except Exception:
        pass
    pct_after, effective_after, _ = _memory_pct()
    event = {
        "ts": time.time(),
        "trigger": trigger,
        "pct_before": round(pct, 1),
        "pct_after": round(pct_after, 1),
        "effective_gb_before": round(effective_gb, 3),
        "effective_gb_after": round(effective_after, 3),
        "need_bytes": need,
        "freed_bytes": freed_total,
        "freed_by_tier": freed_by_tier,
    }
    with _LOCK:
        _STATE["evict_pass_count"] = int(_STATE.get("evict_pass_count") or 0) + 1
        _STATE["total_freed_bytes"] = int(_STATE.get("total_freed_bytes") or 0) + freed_total
        _STATE["last_eviction"] = event
        events = list(_STATE.get("events") or [])
        events.append(event)
        _STATE["events"] = events[-_EVENT_HISTORY_MAX:]
    logger.warning(
        "[memory_watchdog] emergency eviction (%s): %.1f%% -> %.1f%% "
        "(need %.0fMB, freed %.0fMB: %s)",
        trigger,
        pct,
        pct_after,
        need / (1024 * 1024),
        freed_total / (1024 * 1024),
        {k: round(v / (1024 * 1024), 1) for k, v in freed_by_tier.items()} or "nothing left",
    )
    # 캐시 이벤트 로그 기록
    try:
        from core.cache_event_log import record as _log_event
        tier_summary = ", ".join(f"{k}: {round(v / (1024*1024), 1)}MB" for k, v in freed_by_tier.items()) or "nothing"
        _log_event(
            "eviction",
            f"긴급 축출 ({trigger}): {round(pct, 1)}% → {round(pct_after, 1)}% — freed {round(freed_total / (1024*1024), 1)}MB [{tier_summary}]",
            ok=freed_total > 0,
            detail={"freed_bytes": freed_total, "freed_by_tier": freed_by_tier,
                    "pct_before": round(pct, 1), "pct_after": round(pct_after, 1), "trigger": trigger},
        )
    except Exception:
        pass
    return event


def check_once(trigger: str = "interval") -> dict:
    """메모리 확인 1회 — 필요 시 경고/긴급 축출. 반환: 현재 상태 요약."""
    global _LAST_WARN_LOG_TS
    pct, effective_gb, total_gb = _memory_pct()
    now = time.time()
    with _LOCK:
        _STATE["last_check_ts"] = now
        _STATE["last_pct"] = round(pct, 1)
        _STATE["last_effective_gb"] = round(effective_gb, 3)
        _STATE["last_total_gb"] = round(total_gb, 3)
    if pct <= 0:
        return {"pct": 0.0, "acted": False}
    acted = False
    if pct >= warn_pct():
        with _LOCK:
            should_log = now - _LAST_WARN_LOG_TS >= _WARN_LOG_COOLDOWN_SEC
            if should_log:
                _LAST_WARN_LOG_TS = now
            _STATE["warn_count"] = int(_STATE.get("warn_count") or 0) + 1
        if should_log:
            logger.warning(
                "[memory_watchdog] memory high: process %.2fGB / host %.2fGB (%.1f%% >= warn %.0f%%)",
                effective_gb, total_gb, pct, warn_pct(),
            )
            try:
                from core.cache_event_log import record as _log_event
                _log_event(
                    "watchdog",
                    f"메모리 경고: {round(effective_gb, 2)}GB / {round(total_gb, 2)}GB ({round(pct, 1)}% ≥ warn {warn_pct():.0f}%)",
                    ok=True,
                    detail={"pct": round(pct, 1), "effective_gb": round(effective_gb, 2),
                            "total_gb": round(total_gb, 2), "threshold": warn_pct()},
                )
            except Exception:
                pass
    if pct >= critical_pct():
        # 동시 트리거(백그라운드 주기 + 503 경로) 직렬화 — 이미 진행 중이면 스킵.
        if _EVICT_LOCK.acquire(blocking=False):
            try:
                _run_eviction_pass(trigger, pct, effective_gb, total_gb)
                acted = True
            finally:
                _EVICT_LOCK.release()
    return {"pct": round(pct, 1), "acted": acted}


def request_emergency_evict(reason: str = "external") -> bool:
    """외부(요청 가드 503 등)에서 긴급 축출 요청 — 디바운스 후 별도 스레드 실행.

    호출측을 절대 블로킹하지 않는다. 실행 예약 여부를 반환."""
    global _LAST_EXTERNAL_TRIGGER_TS
    if not enabled():
        return False
    now = time.time()
    with _LOCK:
        if now - _LAST_EXTERNAL_TRIGGER_TS < _EXTERNAL_TRIGGER_DEBOUNCE_SEC:
            return False
        _LAST_EXTERNAL_TRIGGER_TS = now
    threading.Thread(
        target=lambda: check_once(trigger=reason),
        name="memory-watchdog-evict",
        daemon=True,
    ).start()
    return True


def status() -> dict:
    """관리자 화면용 상태 스냅샷."""
    with _LOCK:
        state = {k: (list(v) if isinstance(v, list) else (dict(v) if isinstance(v, dict) else v))
                 for k, v in _STATE.items()}
    state.update({
        "enabled": enabled(),
        "warn_pct": warn_pct(),
        "critical_pct": critical_pct(),
        "safe_pct": safe_pct(),
        "interval_sec": interval_sec(),
        "running": bool(_BG_THREAD and _BG_THREAD.is_alive()),
    })
    return state


def _bg_loop() -> None:
    delay = interval_sec()
    while True:
        try:
            time.sleep(delay)
        except Exception:
            return
        try:
            result = check_once(trigger="interval")
            # critical 축출 직후엔 짧은 주기로 재확인 — 안전 수준 도달까지 반복.
            delay = _CRITICAL_RECHECK_SEC if result.get("acted") else interval_sec()
        except Exception as exc:
            logger.warning("memory watchdog loop error: %s", exc)
            delay = interval_sec()


def start_background() -> None:
    """앱 기동 시 1회 호출. 비활성화 env 면 no-op. idempotent."""
    global _STARTED, _BG_THREAD
    if not enabled():
        logger.info("[memory_watchdog] disabled via FLOW_MEMORY_WATCHDOG_ENABLE")
        return
    with _LOCK:
        if _STARTED and _BG_THREAD and _BG_THREAD.is_alive():
            return
        _BG_THREAD = threading.Thread(target=_bg_loop, name="memory-watchdog", daemon=True)
        _BG_THREAD.start()
        _STARTED = True
    logger.info(
        "[memory_watchdog] started (warn %.0f%% / critical %.0f%% / safe %.0f%%, every %.0fs)",
        warn_pct(), critical_pct(), safe_pct(), interval_sec(),
    )
