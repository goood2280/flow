"""core/cache_event_log.py — 캐시 성공/실패 이벤트 로그 수집기.

관리자 캐시관리 페이지에서 예열 성공/실패, eviction, watchdog 트리거 등의
이벤트 이력을 시간순으로 볼 수 있도록 인메모리 링 버퍼에 이벤트를 저장한다.

API 엔드포인트: GET /api/splittable/cache-event-log
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("flow.cache_event_log")

_MAX_EVENTS = 200
_LOCK = threading.Lock()
_EVENTS: deque[dict[str, Any]] = deque(maxlen=_MAX_EVENTS)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def record(
    category: str,
    event: str,
    *,
    ok: bool = True,
    detail: dict[str, Any] | None = None,
    product: str = "",
) -> None:
    """이벤트 기록.

    category: "warmup" | "eviction" | "watchdog" | "budget" | "cache_op" 등
    event:    사람이 읽을 수 있는 설명 (예: "root RAM 예열 완료: 120 roots")
    ok:       성공 여부
    detail:   추가 데이터 (freed_bytes, tier 등)
    product:  관련 제품명 (빈 문자열이면 전체)
    """
    entry = {
        "ts": time.time(),
        "ts_iso": _utc_iso(),
        "category": category,
        "event": event,
        "ok": ok,
        "product": product,
    }
    if detail:
        entry["detail"] = detail
    with _LOCK:
        _EVENTS.append(entry)


def get_events(limit: int = 100, category: str = "") -> list[dict[str, Any]]:
    """최근 이벤트 반환 (최신순)."""
    with _LOCK:
        items = list(_EVENTS)
    if category:
        items = [e for e in items if e.get("category") == category]
    items.sort(key=lambda e: e.get("ts", 0), reverse=True)
    return items[:limit]


def peak_rss_bytes() -> int:
    """프로세스 peak RSS (bytes). resource.getrusage 사용 (Unix) / psutil 폴백."""
    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF)
        # Linux: ru_maxrss 는 KB 단위, macOS: bytes 단위
        import sys as _sys
        if _sys.platform == "linux":
            return int(usage.ru_maxrss * 1024)
        return int(usage.ru_maxrss)
    except Exception:
        pass
    # fallback: psutil
    try:
        import psutil, os  # type: ignore
        p = psutil.Process(os.getpid())
        mi = p.memory_info()
        return int(getattr(mi, "peak_wset", 0) or getattr(mi, "rss", 0))
    except Exception:
        return 0


def peak_ram_info() -> dict[str, Any]:
    """Peak RAM 정보 — 관리자 화면용."""
    peak_bytes = peak_rss_bytes()

    current = {}
    try:
        from core.runtime_limits import process_memory_snapshot, system_memory_snapshot
        snap = process_memory_snapshot()
        sys_snap = system_memory_snapshot()
        current = {
            "rss_gb": snap.get("process_rss_gb", 0.0),
            "effective_gb": snap.get("process_memory_effective_gb", 0.0),
            "limit_gb": snap.get("process_memory_limit_gb", 0.0),
            "system_total_gb": sys_snap.get("system_memory_total_gb", 0.0),
            "system_available_gb": sys_snap.get("system_memory_available_gb", 0.0),
        }
    except Exception:
        pass

    watchdog_thresholds = {}
    try:
        from core import memory_watchdog
        watchdog_thresholds = {
            "warn_pct": memory_watchdog.warn_pct(),
            "critical_pct": memory_watchdog.critical_pct(),
            "safe_pct": memory_watchdog.safe_pct(),
        }
    except Exception:
        pass

    return {
        "peak_rss_gb": round(peak_bytes / (1024 ** 3), 3) if peak_bytes else 0.0,
        "peak_rss_mb": round(peak_bytes / (1024 ** 2), 1) if peak_bytes else 0.0,
        **current,
        "watchdog": watchdog_thresholds,
    }
