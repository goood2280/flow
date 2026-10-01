"""core/gc_tuning.py — 큰 캐시 힙에서 파이썬 GC 정지를 짧게 유지한다.

128GB 운영 서버는 SplitTable 응답 캐시(dict/list)만 20GB 안팎을 파이썬 객체로
든다. CPython 의 전체 GC(`gc.collect()`, 자동 2세대 수집)는 추적 객체 **전부**를
GIL 을 잡은 채 훑는다 — 합성 측정에서 응답 150개(추적 객체 0.9M)가 0.45초였고,
20GB 면 수 초 동안 모든 요청이 멈춘다. 예전에는 무거운 작업이 끝날 때마다,
워치독 idle trim(60초)마다 이 전체 수집을 불렀다.

여기서는 두 가지로 정지를 줄인다.

* **settle()** — 젊은 세대만 수집(`gc.collect(1)`)한 뒤 `gc.freeze()` 로 지금 살아
  있는 객체를 영구 세대로 옮긴다. freeze 는 O(1) 이고, 이후 자동·수동 전체 수집은
  freeze 뒤에 새로 생긴 객체만 훑는다. 캐시 항목은 순환 참조가 없는 dict/list 라
  축출하면 참조 카운트로 바로 해제된다(freeze 와 무관).
* **maintenance()** — freeze 된 객체 중 순환 쓰레기는 GC 가 못 치운다. 사용자가
  한동안 없을 때만(기본 10분 조용, 최소 2시간 간격) unfreeze → 전체 수집 → freeze
  로 정리한다. 메모리 위기(워치독 축출로도 모자랄 때)는 `full_collect()` 로 즉시.

gc.callbacks 로 2세대 수집 정지 시간을 기록해 관리자 모니터(`status()`)에 보인다 —
VM 에서 실제 정지가 줄었는지 이 값으로 확인한다.

환경변수:
  FLOW_GC_FREEZE=0                       freeze 끄기(예전처럼 전체 수집)
  FLOW_GC_SETTLE_MIN_INTERVAL_SEC        주기 settle 최소 간격 (기본 60)
  FLOW_GC_MAINTENANCE_QUIET_SEC          정리 전 사용자 무요청 시간 (기본 600)
  FLOW_GC_MAINTENANCE_INTERVAL_SEC       정리 최소 간격 (기본 7200)
"""
from __future__ import annotations

import gc
import logging
import os
import threading
import time
from collections import deque

logger = logging.getLogger("flow.gc_tuning")

_LOCK = threading.Lock()
_STATE: dict = {
    "settles": 0,
    "last_settle_ts": 0.0,
    "maintenance_runs": 0,
    "last_maintenance_ts": 0.0,
    "last_maintenance_ms": 0.0,
    "full_collects": 0,
    "gen2_collections": 0,
    "gen2_pause_max_ms": 0.0,
    "gen2_pause_last_ms": 0.0,
}
_RECENT_GEN2: deque = deque(maxlen=20)
_CALLBACK_INSTALLED = False
_GEN2_STARTED: dict[int, float] = {}


def _env_float(name: str, default: float, lo: float, hi: float) -> float:
    try:
        value = float(os.environ.get(name, "") or default)
    except Exception:
        value = default
    return max(lo, min(hi, value))


def enabled() -> bool:
    return str(os.environ.get("FLOW_GC_FREEZE", "1")).strip().lower() not in {"0", "false", "no", "off"}


def _gc_callback(phase: str, info: dict) -> None:
    # 2세대(전체) 수집만 잰다. 콜백은 모든 수집마다 불리므로 가볍게 유지한다.
    if info.get("generation") != 2:
        return
    ident = threading.get_ident()
    if phase == "start":
        _GEN2_STARTED[ident] = time.perf_counter()
        return
    started = _GEN2_STARTED.pop(ident, None)
    if started is None:
        return
    ms = (time.perf_counter() - started) * 1000.0
    _STATE["gen2_collections"] = int(_STATE.get("gen2_collections") or 0) + 1
    _STATE["gen2_pause_last_ms"] = round(ms, 1)
    if ms > float(_STATE.get("gen2_pause_max_ms") or 0.0):
        _STATE["gen2_pause_max_ms"] = round(ms, 1)
    _RECENT_GEN2.append({"ts": round(time.time(), 1), "ms": round(ms, 1),
                         "collected": int(info.get("collected") or 0)})


def install_pause_monitor() -> None:
    """2세대 수집 정지 시간을 기록하는 gc 콜백을 1회 등록한다."""
    global _CALLBACK_INSTALLED
    with _LOCK:
        if _CALLBACK_INSTALLED:
            return
        gc.callbacks.append(_gc_callback)
        _CALLBACK_INSTALLED = True


def settle(reason: str = "") -> dict:
    """젊은 세대 수집 + freeze. 전체 힙을 훑지 않는다(정지 수 ms)."""
    started = time.perf_counter()
    collected = 0
    try:
        collected = int(gc.collect(1))
    except Exception:
        pass
    frozen = False
    if enabled():
        try:
            gc.freeze()
            frozen = True
        except Exception:
            frozen = False
    with _LOCK:
        _STATE["settles"] = int(_STATE.get("settles") or 0) + 1
        _STATE["last_settle_ts"] = time.time()
        _STATE["last_settle_reason"] = str(reason or "")
    return {"collected": collected, "frozen": frozen,
            "ms": round((time.perf_counter() - started) * 1000.0, 2)}


def full_collect(reason: str = "") -> dict:
    """freeze 를 풀고 전체 수집한 뒤 다시 freeze. 큰 힙이면 수 초 걸린다 — 조용할 때나 위기 때만."""
    started = time.perf_counter()
    collected = 0
    try:
        if enabled():
            gc.unfreeze()
        collected = int(gc.collect())
    except Exception:
        pass
    finally:
        if enabled():
            try:
                gc.freeze()
            except Exception:
                pass
    ms = (time.perf_counter() - started) * 1000.0
    with _LOCK:
        _STATE["full_collects"] = int(_STATE.get("full_collects") or 0) + 1
        _STATE["last_full_collect"] = {"ts": round(time.time(), 1), "ms": round(ms, 1),
                                       "reason": str(reason or ""), "collected": collected}
    if ms >= 1000.0:
        logger.info("gc full collect %.0fms (%s, collected %d)", ms, reason, collected)
    return {"collected": collected, "ms": round(ms, 1)}


def maybe_settle(now: float | None = None) -> dict | None:
    """주기 호출용 — 최소 간격이 지났으면 settle."""
    now = time.time() if now is None else now
    interval = _env_float("FLOW_GC_SETTLE_MIN_INTERVAL_SEC", 60.0, 5.0, 3600.0)
    with _LOCK:
        if now - float(_STATE.get("last_settle_ts") or 0.0) < interval:
            return None
    return settle("periodic")


def maybe_maintenance(now: float | None = None) -> dict | None:
    """사용자가 충분히 조용하고 마지막 정리가 오래됐으면 전체 수집으로 순환 쓰레기를 치운다."""
    if not enabled():
        return None
    now = time.time() if now is None else now
    interval = _env_float("FLOW_GC_MAINTENANCE_INTERVAL_SEC", 7200.0, 300.0, 7 * 86400.0)
    quiet = _env_float("FLOW_GC_MAINTENANCE_QUIET_SEC", 600.0, 10.0, 86400.0)
    with _LOCK:
        last = float(_STATE.get("last_maintenance_ts") or 0.0)
    if last and now - last < interval:
        return None
    try:
        from core import request_priority

        if request_priority.seconds_since_user_activity() < quiet:
            return None
    except Exception:
        return None
    with _LOCK:
        _STATE["last_maintenance_ts"] = now
    result = full_collect("maintenance")
    with _LOCK:
        _STATE["maintenance_runs"] = int(_STATE.get("maintenance_runs") or 0) + 1
        _STATE["last_maintenance_ms"] = result["ms"]
    return result


def status() -> dict:
    with _LOCK:
        state = dict(_STATE)
    try:
        state["freeze_count"] = int(gc.get_freeze_count())
    except Exception:
        state["freeze_count"] = 0
    state["enabled"] = enabled()
    state["recent_gen2"] = list(_RECENT_GEN2)
    state["thresholds"] = list(gc.get_threshold())
    return state
