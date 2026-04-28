"""core/sysmon.py v8.8.18 — 크로스플랫폼 시스템 모니터 + 유휴 부하 정책.

psutil 로 CPU / Memory / Disk 사용량을 5분 주기로 수집해 resource_log 에 append.
최근 6시간 동안 CPU / Memory 가 **한 번도 85% 이상 찍지 않았으면** 5~10분
가량의 더미 부하를 생성해 자원 유휴 상태를 보완한다. 사용자 활동이 감지되면
부하 생성 스레드를 즉시 중단하고 **30분 대기** 후 다시 유휴 체크를 수행.

외부에서 쓰는 API:
  - collect_once() → dict (현재 CPU/Mem/Disk + 타임스탬프). 호출 시 resource_log 에도 append.
  - get_state() → dict (last_sample, load_thread 상태, 최근 활동 시각 등).
  - mark_user_activity() → 사용자 활동 감지 시 호출. load 중이면 중단 신호 설정.
  - start_background() → 5분 주기 수집/유휴 체크 백그라운드 스레드 시작 (idempotent).
  - history(limit=288) → resource_log tail 반환.
"""
from __future__ import annotations

import datetime as _dt
import logging
import os
import random
import threading
import time
from pathlib import Path
from typing import List, Optional

from core.paths import PATHS
from core.utils import jsonl_append, jsonl_read, jsonl_trim

logger = logging.getLogger("flow.sysmon")

try:
    import psutil as _psutil
except Exception:
    _psutil = None

# ── 설정 상수 ────────────────────────────────────────────────────────
SAMPLE_INTERVAL_SEC  = 5 * 60           # 5분 주기로 수집
HISTORY_WINDOW_HOURS = 6                # 최근 6시간 검사 창
THRESHOLD_PCT        = 85.0             # 85% 이상이 한 번이라도 있었는지
LOAD_MIN_SEC         = 5 * 60           # 부하 최소 5분
LOAD_MAX_SEC         = 10 * 60          # 부하 최대 10분
PAUSE_AFTER_USER_SEC = 30 * 60          # 사용자 활동 감지 후 30분 대기
USER_ACTIVITY_TTL_SEC = 2 * 60          # 직전 2분 이내 활동이면 "active" 로 간주
MANUAL_LOAD_MAX_SEC  = 10 * 60          # Admin 수동 부하 최대 10분
MEM_CHUNK_MB         = 16               # 메모리 pressure 는 작은 chunk 로 점진 할당

RESOURCE_LOG: Path = PATHS.resource_log
SYSMON_STATE_FILE: Path = PATHS.log_dir / "sysmon_state.json"
RESOURCE_LOG.parent.mkdir(parents=True, exist_ok=True)


def _load_generation_enabled() -> bool:
    """Synthetic load is opt-in on small shared Flow hosts."""
    raw = os.environ.get("FLOW_SYSMON_ENABLE_LOAD", "").strip().lower()
    return raw in {"1", "true", "yes", "on", "enabled"}


def _manual_memory_cap_mb() -> int:
    raw = os.environ.get("FLOW_SYSMON_MAX_MEM_LOAD_MB", "").strip()
    try:
        val = int(raw)
    except Exception:
        val = 1024
    return max(0, min(8192, val))

# ── 내부 상태 ────────────────────────────────────────────────────────
_lock = threading.Lock()
_last_user_activity: float = 0.0        # epoch seconds
_load_thread: Optional[threading.Thread] = None
_load_stop = threading.Event()
_load_started_at: float = 0.0
_load_end_at: float = 0.0
_load_mode: str = ""
_load_target_pct: float = THRESHOLD_PCT
_mem_hold: list[bytearray] = []
_mem_allocated_mb: int = 0
_paused_until: float = 0.0              # 유휴 체크를 건너뛰는 마감 시각
_bg_thread: Optional[threading.Thread] = None
_last_sample: dict = {}


def _now() -> float:
    return time.time()


def _iso(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def _disk_target() -> Path:
    """디스크 사용량 측정 기준 경로 — data_root 가 있는 드라이브/파티션."""
    try:
        return PATHS.data_root
    except Exception:
        return Path(".").resolve()


_PROC_CPU_LAST: dict = {"idle": 0, "total": 0, "ts": 0.0}


def _read_proc_cpu_percent() -> float:
    """v8.8.21: psutil 없을 때 Linux /proc/stat 로 CPU 사용률 폴백.
    2회 샘플 차이로 계산 — 첫 호출은 0 반환 후 다음 호출에서 실제 값."""
    try:
        with open("/proc/stat", "r") as f:
            line = f.readline()
        parts = line.split()
        if parts[0] != "cpu":
            return 0.0
        vals = [int(x) for x in parts[1:8]]
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        total = sum(vals)
        prev_idle = _PROC_CPU_LAST.get("idle", 0)
        prev_total = _PROC_CPU_LAST.get("total", 0)
        _PROC_CPU_LAST["idle"] = idle
        _PROC_CPU_LAST["total"] = total
        _PROC_CPU_LAST["ts"] = _now()
        if prev_total == 0 or total <= prev_total:
            return 0.0
        d_idle = idle - prev_idle
        d_total = total - prev_total
        if d_total <= 0:
            return 0.0
        return max(0.0, min(100.0, 100.0 * (1.0 - d_idle / d_total)))
    except Exception:
        return 0.0


def _read_proc_meminfo() -> tuple:
    """v8.8.21: /proc/meminfo 로 mem_pct / used_gb / total_gb 반환."""
    try:
        info: dict = {}
        with open("/proc/meminfo", "r") as f:
            for ln in f:
                k, _, v = ln.partition(":")
                info[k.strip()] = v.strip()

        def _kb(key):
            v = info.get(key, "0 kB")
            try:
                return int(v.split()[0])
            except Exception:
                return 0
        total_kb = _kb("MemTotal")
        avail_kb = _kb("MemAvailable") or (_kb("MemFree") + _kb("Buffers") + _kb("Cached"))
        used_kb = max(0, total_kb - avail_kb)
        pct = (100.0 * used_kb / total_kb) if total_kb > 0 else 0.0
        return round(pct, 1), round(used_kb / 1e6, 2), round(total_kb / 1e6, 2)
    except Exception:
        return 0.0, 0.0, 0.0


def _read_proc_disk(path: Path) -> tuple:
    """v8.8.21: os.statvfs 로 data_root 파티션 사용량. Linux/macOS 공통."""
    try:
        import os
        st = os.statvfs(str(path))
        total = st.f_frsize * st.f_blocks
        free = st.f_frsize * st.f_bavail
        used = max(0, total - free)
        pct = (100.0 * used / total) if total > 0 else 0.0
        return round(pct, 1), round(used / 1e9, 2), round(total / 1e9, 2)
    except Exception:
        return 0.0, 0.0, 0.0


def _collect_stats() -> dict:
    """현재 CPU / Mem / Disk 사용량 수집. psutil 미설치면 /proc/statvfs 폴백."""
    ts = _now()
    if _psutil is None:
        # v8.8.21: Linux /proc 폴백 — 사내 서버는 psutil 없을 수 있음.
        cpu = _read_proc_cpu_percent()
        mem_pct, mem_used, mem_total = _read_proc_meminfo()
        disk_pct, disk_used, disk_total = _read_proc_disk(_disk_target())
        return {
            "timestamp": _iso(ts), "ts_epoch": ts,
            "cpu_percent": round(cpu, 1),
            "memory_percent": mem_pct,
            "memory_used_gb": mem_used,
            "memory_total_gb": mem_total,
            "disk_percent": disk_pct,
            "disk_used_gb": disk_used,
            "disk_total_gb": disk_total,
            "psutil": False,
            "source": "proc_fallback",
        }
    try:
        cpu = float(_psutil.cpu_percent(interval=0.3))
    except Exception:
        cpu = 0.0
    try:
        vm = _psutil.virtual_memory()
        mem_pct = float(vm.percent)
        mem_used = float(vm.used) / 1e9
        mem_total = float(vm.total) / 1e9
    except Exception:
        mem_pct, mem_used, mem_total = 0.0, 0.0, 0.0
    try:
        du = _psutil.disk_usage(str(_disk_target()))
        disk_pct = float(du.percent)
        disk_used = float(du.used) / 1e9
        disk_total = float(du.total) / 1e9
    except Exception:
        disk_pct, disk_used, disk_total = 0.0, 0.0, 0.0
    return {
        "timestamp": _iso(ts), "ts_epoch": ts,
        "cpu_percent": round(cpu, 1),
        "memory_percent": round(mem_pct, 1),
        "memory_used_gb": round(mem_used, 2),
        "memory_total_gb": round(mem_total, 2),
        "disk_percent": round(disk_pct, 1),
        "disk_used_gb": round(disk_used, 2),
        "disk_total_gb": round(disk_total, 2),
        "psutil": True,
    }


def collect_once() -> dict:
    """현재 상태를 읽어 resource_log 에 append 하고 반환."""
    global _last_sample
    s = _collect_stats()
    try:
        jsonl_append(RESOURCE_LOG, s)
        jsonl_trim(RESOURCE_LOG, 8640)   # ≈ 1 month @ 5min
    except Exception as e:
        logger.warning(f"resource_log append failed: {e}")
    with _lock:
        _last_sample = dict(s)
    return s


def history(limit: int = 288) -> List[dict]:
    """resource_log tail. 기본 288 = 1일치 @ 5min."""
    try:
        return jsonl_read(RESOURCE_LOG, limit) or []
    except Exception:
        return []


def _window_peaked_above(threshold: float) -> bool:
    """최근 HISTORY_WINDOW_HOURS 창 안에서 cpu or memory 가 threshold% 이상이었는지."""
    cutoff = _iso(_now() - HISTORY_WINDOW_HOURS * 3600)
    entries = jsonl_read(RESOURCE_LOG, 0, lambda e: e.get("timestamp", "") >= cutoff)
    # 데이터가 충분치 않으면 False — 유휴 체크 skip (너무 이른 판단 방지).
    if len(entries) < max(3, HISTORY_WINDOW_HOURS // 2):
        return True
    for e in entries:
        if float(e.get("cpu_percent", 0)) >= threshold or float(e.get("memory_percent", 0)) >= threshold:
            return True
    return False


def mark_user_activity() -> None:
    """사용자 활동 감지 — 부하 중이면 중단 신호, 30분 대기 창 설정."""
    global _last_user_activity, _paused_until
    with _lock:
        _last_user_activity = _now()
        _paused_until = _last_user_activity + PAUSE_AFTER_USER_SEC
    _load_stop.set()


def _has_recent_user_activity() -> bool:
    with _lock:
        return (_now() - _last_user_activity) < USER_ACTIVITY_TTL_SEC


def get_state() -> dict:
    """현재 모니터 상태 스냅샷 (FE 위젯용)."""
    with _lock:
        sample = dict(_last_sample or {})
        load_active = bool(_load_thread and _load_thread.is_alive())
        end_at = _load_end_at if load_active else 0.0
        started_at = _load_started_at if load_active else 0.0
        paused_until = _paused_until
        last_user = _last_user_activity
        mode = _load_mode if load_active else ""
        target_pct = _load_target_pct if load_active else THRESHOLD_PCT
        mem_allocated_mb = _mem_allocated_mb if load_active else 0
    return {
        "sample": sample,
        "load_active": load_active,
        "farming": load_active,
        "load_mode": mode,
        "load_target_pct": target_pct,
        "load_memory_allocated_mb": mem_allocated_mb,
        "load_memory_cap_mb": _manual_memory_cap_mb(),
        "load_started_at": _iso(started_at) if started_at else "",
        "load_estimated_end": _iso(end_at) if end_at else "",
        "paused_until": _iso(paused_until) if paused_until and paused_until > _now() else "",
        "last_user_activity": _iso(last_user) if last_user else "",
        "recent_user_activity": _has_recent_user_activity(),
        "psutil_available": _psutil is not None,
        "threshold_pct": THRESHOLD_PCT,
        "window_hours": HISTORY_WINDOW_HOURS,
        "load_generation_enabled": _load_generation_enabled(),
    }


def _burn_cpu(stop_event: threading.Event, deadline: float) -> None:
    """CPU 부하 — 단일 스레드 numpy-free 연산. stop_event 또는 deadline 까지 반복."""
    try:
        import numpy as _np
        have_np = True
    except Exception:
        have_np = False

    while not stop_event.is_set() and _now() < deadline:
        if have_np:
            # 적당히 CPU 를 끌어쓰는 연산 — numpy 있을 때.
            try:
                a = _np.random.rand(400, 400)
                b = _np.random.rand(400, 400)
                _ = _np.linalg.svd(a @ b, full_matrices=False)
            except Exception:
                have_np = False
                continue
        else:
            # Pure Python fallback
            _ = sum(i * i for i in range(500_000))
        # 너무 과하게 못 돌게 약간의 양보.
        if stop_event.wait(timeout=0.01):
            return


def _hold_memory_until(stop_event: threading.Event, deadline: float, target_pct: float) -> None:
    """Manual memory pressure with a hard cap and frequent system checks."""
    global _mem_hold, _mem_allocated_mb
    max_mb = _manual_memory_cap_mb()
    if max_mb <= 0:
        return
    chunk_bytes = MEM_CHUNK_MB * 1024 * 1024
    try:
        while not stop_event.is_set() and _now() < deadline:
            s = _collect_stats()
            mem_pct = float(s.get("memory_percent") or 0)
            with _lock:
                allocated = _mem_allocated_mb
            if mem_pct >= target_pct or allocated + MEM_CHUNK_MB > max_mb:
                break
            _mem_hold.append(bytearray(chunk_bytes))
            with _lock:
                _mem_allocated_mb += MEM_CHUNK_MB
            if stop_event.wait(timeout=0.25):
                return
        while not stop_event.is_set() and _now() < deadline:
            if stop_event.wait(timeout=0.5):
                return
    finally:
        _mem_hold = []
        with _lock:
            _mem_allocated_mb = 0


def _load_worker(duration_sec: int, mode: str = "auto", target_pct: float = THRESHOLD_PCT, memory: bool = False) -> None:
    """부하 스레드 entry point — _burn_cpu 를 듀얼 쓰레드로 돌려 85% 근처까지 끌어올림."""
    global _load_started_at, _load_end_at, _load_mode, _load_target_pct, _mem_hold, _mem_allocated_mb
    start = _now()
    end = start + duration_sec
    with _lock:
        _load_started_at = start
        _load_end_at = end
        _load_mode = mode
        _load_target_pct = float(target_pct or THRESHOLD_PCT)
        _mem_allocated_mb = 0
    _mem_hold = []
    logger.info(f"[sysmon] load generation start — {duration_sec}s planned mode={mode} target={target_pct}")
    _load_stop.clear()

    # 보조 워커 1~2 개로 병렬 부하. psutil 이 있으면 코어수 기반으로 조절.
    n_aux = 1
    if _psutil is not None:
        try:
            n_aux = max(1, min(4, (_psutil.cpu_count(logical=False) or 2) - 1))
        except Exception:
            n_aux = 1
    aux_threads: List[threading.Thread] = []
    for _ in range(n_aux):
        t = threading.Thread(target=_burn_cpu, args=(_load_stop, end), daemon=True)
        t.start()
        aux_threads.append(t)
    if memory:
        t = threading.Thread(target=_hold_memory_until, args=(_load_stop, end, float(target_pct or THRESHOLD_PCT)), daemon=True)
        t.start()
        aux_threads.append(t)
    # 메인에서도 함께 burn — 단일 워커가 아닌 다수 스레드가 함께 돌도록.
    _burn_cpu(_load_stop, end)
    for t in aux_threads:
        t.join(timeout=2.0)
    stopped_early = _load_stop.is_set()
    with _lock:
        _load_end_at = _now() if stopped_early else end
        _load_mode = ""
        _mem_allocated_mb = 0
    _mem_hold = []
    logger.info(f"[sysmon] load generation {'stopped' if stopped_early else 'finished'} "
                f"after {int(_now() - start)}s")


def _maybe_start_load() -> None:
    """유휴 조건 만족 시 부하 스레드 시작. 이미 돌고 있거나 최근 사용자 활동이 있으면 skip."""
    global _load_thread
    if not _load_generation_enabled():
        return
    if _load_thread and _load_thread.is_alive():
        return
    if _has_recent_user_activity():
        return
    if _paused_until > _now():
        return
    # 최근 6시간 안에 85% 찍은 적이 있으면 유휴가 아님.
    if _window_peaked_above(THRESHOLD_PCT):
        return
    duration = random.randint(LOAD_MIN_SEC, LOAD_MAX_SEC)
    _load_stop.clear()
    _load_thread = threading.Thread(target=_load_worker, args=(duration,), daemon=True)
    _load_thread.start()


def start_manual_load(duration_sec: int = 180, target_pct: float = THRESHOLD_PCT, memory: bool = True) -> dict:
    """Admin-triggered synthetic load. Keeps the API server alive if the worker stops."""
    global _load_thread, _paused_until
    duration = max(15, min(MANUAL_LOAD_MAX_SEC, int(duration_sec or 180)))
    target = max(10.0, min(THRESHOLD_PCT, float(target_pct or THRESHOLD_PCT)))
    if _load_thread and _load_thread.is_alive():
        return {"ok": False, "already_running": True, "state": get_state()}
    with _lock:
        _paused_until = _now() + duration + 60
    _load_stop.clear()
    _load_thread = threading.Thread(
        target=_load_worker,
        args=(duration, "manual", target, bool(memory)),
        name="sysmon-manual-load",
        daemon=True,
    )
    _load_thread.start()
    return {"ok": True, "duration_sec": duration, "target_pct": target, "memory": bool(memory), "state": get_state()}


def stop_load() -> dict:
    _load_stop.set()
    return {"ok": True, "state": get_state()}


def _bg_loop() -> None:
    """5분 주기 샘플 + 유휴 체크. 앱 기동 시 1회 즉시 샘플."""
    try:
        collect_once()
    except Exception as e:
        logger.warning(f"initial sample failed: {e}")
    while True:
        try:
            time.sleep(SAMPLE_INTERVAL_SEC)
        except Exception:
            return
        try:
            collect_once()
            _maybe_start_load()
        except Exception as e:
            logger.warning(f"sysmon loop error: {e}")


def start_background() -> None:
    """앱 기동 시 1회 호출. 이미 실행 중이면 no-op."""
    global _bg_thread
    if _bg_thread and _bg_thread.is_alive():
        return
    _bg_thread = threading.Thread(target=_bg_loop, name="sysmon-bg", daemon=True)
    _bg_thread.start()
    logger.info("[sysmon] background loop started")
