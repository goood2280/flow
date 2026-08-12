"""core/sysmon.py v9.2.1 — 크로스플랫폼 시스템 모니터 + 유휴 부하 정책.

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
from core.runtime_limits import (
    process_cpu_snapshot,
    process_memory_snapshot,
    system_memory_snapshot,
)
from core.utils import jsonl_append, jsonl_read, jsonl_trim, load_json, save_json

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
MEM_STEP_MB          = 1024             # 화면에서 약 1GB 단위로 상승하도록 하는 논리 단계
MEM_CHUNK_MB         = 64               # 페이지 touch 중 API 정지를 줄이기 위한 실제 할당 단위
DEFAULT_SCHEDULE_TIME = "11:00"
DEFAULT_SCHEDULE_TARGET_PCT = 85.0
PAVER_MIN_TARGET_PCT = 80.0
PAVER_MAX_TARGET_PCT = 89.0
PAVER_RELEASE_PCT = 90.0

RESOURCE_LOG: Path = PATHS.resource_log
SYSMON_STATE_FILE: Path = PATHS.log_dir / "sysmon_state.json"
RESOURCE_LOG.parent.mkdir(parents=True, exist_ok=True)


def _load_generation_enabled() -> bool:
    """Synthetic load is opt-in on small shared Flow hosts."""
    raw = os.environ.get("FLOW_SYSMON_ENABLE_LOAD", "").strip().lower()
    return raw in {"1", "true", "yes", "on", "enabled"}


def _manual_memory_cap_mb() -> int:
    """명시된 운영 상한. 0이면 현재 사용량에서 목표치까지 동적으로 계산한다."""
    raw = os.environ.get("FLOW_SYSMON_MAX_MEM_LOAD_MB", "").strip()
    try:
        val = int(raw)
    except Exception:
        val = 0
    return max(0, min(262144, val))


def _normalize_paver_target(target_pct: float) -> float:
    """보도블럭 부하는 80% 이상을 목표로 하되 90% 안전선 아래에 둔다."""
    try:
        target = float(target_pct)
    except (TypeError, ValueError):
        target = DEFAULT_SCHEDULE_TARGET_PCT
    return max(PAVER_MIN_TARGET_PCT, min(PAVER_MAX_TARGET_PCT, target))


def _paver_ceiling_breach(sample: dict) -> str:
    """CPU/RAM 중 먼저 90%에 닿은 항목을 반환한다."""
    cpu_pct = float(sample.get("cpu_percent") or 0.0)
    memory_pct = float(sample.get("memory_percent") or 0.0)
    if cpu_pct >= PAVER_RELEASE_PCT:
        return f"CPU {cpu_pct:.1f}%"
    if memory_pct >= PAVER_RELEASE_PCT:
        return f"RAM {memory_pct:.1f}%"
    return ""

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
_mem_cap_mb: int = 0
_load_error: str = ""
_load_release_reason: str = ""
_manual_paver_hold: list[bytearray] = []
_manual_paver_allocated_mb: int = 0
_manual_paver_pending_steps: int = 0
_manual_paver_thread: Optional[threading.Thread] = None
_manual_paver_stop = threading.Event()
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
        return round(pct, 1), round(used_kb / (1024 ** 2), 2), round(total_kb / (1024 ** 2), 2)
    except Exception:
        return 0.0, 0.0, 0.0


def _apply_effective_memory(sample: dict) -> dict:
    snap = system_memory_snapshot()
    total = float(snap.get("system_memory_total_gb") or 0.0)
    available = float(snap.get("system_memory_available_gb") or 0.0)
    percent = float(snap.get("system_memory_percent") or 0.0)
    if total > 0:
        sample["memory_total_gb"] = round(total, 2)
        sample["memory_used_gb"] = round(max(0.0, total - available), 2)
        sample["memory_percent"] = round(percent, 1)
        sample["memory_source"] = snap.get("system_memory_source") or sample.get("source") or ""
    return sample


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
        sample = {
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
        _apply_effective_memory(sample)
        sample.update(process_memory_snapshot())
        sample.update(process_cpu_snapshot())
        return sample
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
    sample = {
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
    _apply_effective_memory(sample)
    sample.update(process_memory_snapshot())
    sample.update(process_cpu_snapshot())
    return sample


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
        forced_schedule = _load_mode == "scheduled"
    # 예약 실행은 관리자가 지정한 하루 1회 강제 부하다. 일반 화면 요청이 들어와도
    # 멈추지 않되, 관리자 모니터의 중지 버튼은 stop_load()로 언제든 중단할 수 있다.
    if not forced_schedule:
        _load_stop.set()


def _default_schedule() -> dict:
    return {
        "enabled": True,
        "time": DEFAULT_SCHEDULE_TIME,
        "target_pct": DEFAULT_SCHEDULE_TARGET_PCT,
        "last_run_date": "",
        "last_run_at": "",
    }


def _normalize_schedule(raw: dict | None) -> dict:
    cfg = _default_schedule()
    if isinstance(raw, dict):
        cfg.update({k: raw.get(k) for k in cfg if k in raw})
    at = str(cfg.get("time") or DEFAULT_SCHEDULE_TIME).strip()
    try:
        hour_s, minute_s = at.split(":", 1)
        hour, minute = int(hour_s), int(minute_s)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
        at = f"{hour:02d}:{minute:02d}"
    except Exception:
        at = DEFAULT_SCHEDULE_TIME
    try:
        target = max(80.0, min(90.0, float(cfg.get("target_pct") or DEFAULT_SCHEDULE_TARGET_PCT)))
    except Exception:
        target = DEFAULT_SCHEDULE_TARGET_PCT
    cfg.update(enabled=bool(cfg.get("enabled", True)), time=at, target_pct=target)
    cfg["last_run_date"] = str(cfg.get("last_run_date") or "")[:10]
    cfg["last_run_at"] = str(cfg.get("last_run_at") or "")[:32]
    return cfg


def get_schedule() -> dict:
    with _lock:
        return _normalize_schedule(load_json(SYSMON_STATE_FILE, {}))


def save_schedule(*, enabled: bool, at: str) -> dict:
    # 먼저 정규화한 뒤 원문과 달라졌으면 400을 낼 수 있도록 호출자가 검사하지
    # 않아도 항상 유효한 HH:MM만 파일에 남긴다.
    raw_at = str(at or "").strip()
    parts = raw_at.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ValueError("time must be HH:MM")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("time must be HH:MM")
    with _lock:
        cfg = _normalize_schedule(load_json(SYSMON_STATE_FILE, {}))
        cfg.update(enabled=bool(enabled), time=f"{hour:02d}:{minute:02d}")
        save_json(SYSMON_STATE_FILE, cfg, indent=2)
        return dict(cfg)


def _has_recent_user_activity() -> bool:
    with _lock:
        return (_now() - _last_user_activity) < USER_ACTIVITY_TTL_SEC


def get_state() -> dict:
    """현재 모니터 상태 스냅샷 (FE 위젯용)."""
    with _lock:
        sample = dict(_last_sample or {})
        load_active = bool(_load_thread and _load_thread.is_alive() and not _load_stop.is_set())
        end_at = _load_end_at if load_active else 0.0
        started_at = _load_started_at if load_active else 0.0
        paused_until = _paused_until
        last_user = _last_user_activity
        mode = _load_mode if load_active else ""
        target_pct = _load_target_pct if load_active else THRESHOLD_PCT
        mem_allocated_mb = _mem_allocated_mb if load_active else 0
        mem_cap_mb = _mem_cap_mb if load_active else _manual_memory_cap_mb()
        load_error = _load_error
        load_release_reason = _load_release_reason
        combined_paver_active = load_active and mode in {"manual", "scheduled"}
        paver_allocated_mb = _mem_allocated_mb if combined_paver_active else _manual_paver_allocated_mb
        paver_pending_steps = _manual_paver_pending_steps
        paver_active = combined_paver_active or bool(_manual_paver_thread and _manual_paver_thread.is_alive())
    return {
        "sample": sample,
        "load_active": load_active,
        "farming": load_active,
        "load_mode": mode,
        "load_target_pct": target_pct,
        "load_memory_allocated_mb": mem_allocated_mb,
        "load_memory_cap_mb": mem_cap_mb,
        "load_error": load_error,
        "load_release_reason": load_release_reason,
        "paver_allocated_mb": paver_allocated_mb,
        "paver_pending_steps": paver_pending_steps,
        "paver_active": paver_active,
        "paver_cpu_active": combined_paver_active,
        "paver_memory_active": combined_paver_active,
        "paver_target_pct": target_pct if combined_paver_active else DEFAULT_SCHEDULE_TARGET_PCT,
        "paver_release_pct": PAVER_RELEASE_PCT,
        "load_started_at": _iso(started_at) if started_at else "",
        "load_estimated_end": _iso(end_at) if end_at else "",
        "paused_until": _iso(paused_until) if paused_until and paused_until > _now() else "",
        "last_user_activity": _iso(last_user) if last_user else "",
        "recent_user_activity": _has_recent_user_activity(),
        "psutil_available": _psutil is not None,
        "threshold_pct": THRESHOLD_PCT,
        "window_hours": HISTORY_WINDOW_HOURS,
        "load_generation_enabled": _load_generation_enabled(),
        "schedule": get_schedule(),
    }


def _burn_cpu(
    stop_event: threading.Event,
    deadline: float,
    run_gate: threading.Event | None = None,
) -> None:
    """CPU 부하. run_gate로 목표 사용률 부근에서 일시 정지할 수 있다."""
    try:
        import numpy as _np
        have_np = True
    except Exception:
        have_np = False

    while not stop_event.is_set() and _now() < deadline:
        if run_gate is not None and not run_gate.is_set():
            if stop_event.wait(timeout=0.05):
                return
            continue
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
    """목표 사용률까지 resident memory를 약 1GB 단계로 올리고 종료 시 해제한다."""
    global _mem_hold, _mem_allocated_mb, _mem_cap_mb, _load_error, _load_release_reason
    first = _collect_stats()
    total_gb = float(first.get("memory_total_gb") or 0.0)
    current_pct = float(first.get("memory_percent") or 0.0)
    explicit_cap = _manual_memory_cap_mb()
    # 목표까지 필요한 양 + 한 단계의 여유만 허용한다. 측정값이 갱신되지 않아도
    # 무한 할당하지 않으며, FLOW_SYSMON_MAX_MEM_LOAD_MB가 있으면 그 값을 우선한다.
    dynamic_cap = int(max(0.0, (target_pct - current_pct) / 100.0 * total_gb * 1024.0) + MEM_STEP_MB)
    max_mb = explicit_cap or dynamic_cap
    with _lock:
        _mem_cap_mb = max_mb
    if total_gb <= 0 or max_mb <= 0:
        with _lock:
            _load_error = "system memory total is unavailable" if total_gb <= 0 else ""
        return
    chunk_bytes = MEM_CHUNK_MB * 1024 * 1024
    try:
        while not stop_event.is_set() and _now() < deadline:
            # 한 단계 안에서는 64MB씩 실제 페이지를 touch하고, 단계 사이에서
            # 사용률을 다시 읽는다. 그래프에는 약 1GB씩 계단식으로 보인다.
            for _ in range(MEM_STEP_MB // MEM_CHUNK_MB):
                s = _collect_stats()
                mem_pct = float(s.get("memory_percent") or 0)
                with _lock:
                    allocated = _mem_allocated_mb
                if mem_pct >= PAVER_RELEASE_PCT:
                    with _lock:
                        _load_release_reason = f"RAM {mem_pct:.1f}% 안전선 도달로 자동 해제"
                    stop_event.set()
                    break
                if mem_pct >= target_pct or allocated + MEM_CHUNK_MB > max_mb:
                    break
                block = bytearray(chunk_bytes)
                for offset in range(0, chunk_bytes, 4096):
                    block[offset] = 1
                _mem_hold.append(block)
                with _lock:
                    _mem_allocated_mb += MEM_CHUNK_MB
                if stop_event.wait(timeout=0.03):
                    return
            s = _collect_stats()
            with _lock:
                allocated = _mem_allocated_mb
            if float(s.get("memory_percent") or 0) >= target_pct or allocated >= max_mb:
                break
            if stop_event.wait(timeout=0.8):
                return
        while not stop_event.is_set() and _now() < deadline:
            # 목표 도달 후 유지 중에도 다른 프로세스 때문에 90%가 되면 이 기능이
            # 잡은 메모리를 즉시 푼다.
            mem_pct = float(_collect_stats().get("memory_percent") or 0)
            if mem_pct >= PAVER_RELEASE_PCT:
                with _lock:
                    _load_release_reason = f"RAM {mem_pct:.1f}% 안전선 도달로 자동 해제"
                stop_event.set()
                break
            if stop_event.wait(timeout=0.5):
                return
    except MemoryError:
        with _lock:
            _load_error = "memory allocation failed before reaching target"
        logger.warning("[sysmon] memory pressure allocation stopped by MemoryError")
    finally:
        _mem_hold = []
        with _lock:
            _mem_allocated_mb = 0
            _mem_cap_mb = 0


def _load_worker(duration_sec: int, mode: str = "auto", target_pct: float = THRESHOLD_PCT, memory: bool = False) -> None:
    """CPU/RAM을 목표 구간에 유지하고 어느 하나든 90%면 모두 해제한다."""
    global _load_started_at, _load_end_at, _load_mode, _load_target_pct
    global _mem_hold, _mem_allocated_mb, _mem_cap_mb, _load_error, _load_release_reason
    start = _now()
    end = start + duration_sec
    target_pct = _normalize_paver_target(target_pct)
    with _lock:
        _load_started_at = start
        _load_end_at = end
        _load_mode = mode
        _load_target_pct = target_pct
        _mem_allocated_mb = 0
        _mem_cap_mb = 0
        _load_error = ""
        _load_release_reason = ""
    _mem_hold = []
    logger.info(f"[sysmon] load generation start — {duration_sec}s planned mode={mode} target={target_pct}")

    # 처음에는 두 워커만 돌리고, 목표치에 못 미칠 때 코어 수 범위에서 하나씩
    # 늘린다. run_gate를 닫으면 워커가 즉시 양보하므로 90%까지 계속 치솟지 않는다.
    max_workers = 2
    if _psutil is not None:
        try:
            max_workers = max(2, min(8, _psutil.cpu_count(logical=True) or 2))
        except Exception:
            max_workers = 2
    run_gate = threading.Event()
    run_gate.set()
    aux_threads: List[threading.Thread] = []

    def add_cpu_worker() -> None:
        t = threading.Thread(target=_burn_cpu, args=(_load_stop, end, run_gate), daemon=True)
        t.start()
        aux_threads.append(t)

    for _ in range(min(2, max_workers)):
        add_cpu_worker()
    if memory:
        t = threading.Thread(target=_hold_memory_until, args=(_load_stop, end, float(target_pct or THRESHOLD_PCT)), daemon=True)
        t.start()
        aux_threads.append(t)

    while not _load_stop.is_set() and _now() < end:
        sample = _collect_stats()
        breach = _paver_ceiling_breach(sample)
        if breach:
            with _lock:
                _load_release_reason = f"{breach} 안전선 도달로 자동 해제"
            logger.warning("[sysmon] %s >= %.0f%%; releasing CPU/RAM paver load", breach, PAVER_RELEASE_PCT)
            _load_stop.set()
            break

        cpu_pct = float(sample.get("cpu_percent") or 0.0)
        if cpu_pct >= target_pct + 1.0:
            run_gate.clear()
        elif cpu_pct < target_pct - 2.0:
            run_gate.set()
            cpu_workers = len(aux_threads) - (1 if memory else 0)
            if cpu_workers < max_workers:
                add_cpu_worker()
        if _load_stop.wait(timeout=0.2):
            break

    run_gate.set()
    for t in aux_threads:
        t.join(timeout=2.0)
    stopped_early = _load_stop.is_set()
    with _lock:
        _load_end_at = _now() if stopped_early else end
        _load_mode = ""
        _mem_allocated_mb = 0
        _mem_cap_mb = 0
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
    """관리자용 CPU+RAM 보도블럭 부하를 시작한다."""
    global _load_thread, _load_mode, _load_target_pct, _load_release_reason
    duration_sec = max(5, min(MANUAL_LOAD_MAX_SEC, int(duration_sec or 180)))
    target_pct = _normalize_paver_target(target_pct)
    sample = _collect_stats()
    breach = _paver_ceiling_breach(sample)
    if breach:
        _load_stop.set()
        with _lock:
            released = _release_manual_paver_locked()
            _load_release_reason = f"{breach} 안전선 도달로 시작하지 않음"
        return {
            "ok": False,
            "released": True,
            "released_mb": released,
            "error": f"{breach} is already at or above {PAVER_RELEASE_PCT:.0f}%",
            "state": get_state(),
        }
    with _lock:
        already_active = bool(_load_thread and _load_thread.is_alive())
        if not already_active:
            # 이전 버전에서 수동으로 잡아 둔 RAM이 있으면 통합 작업 시작 전에 비운다.
            _release_manual_paver_locked()
            _load_mode = "manual"
            _load_target_pct = target_pct
            _load_release_reason = ""
            _load_stop.clear()
            _load_thread = threading.Thread(
                target=_load_worker,
                args=(duration_sec, "manual", target_pct, True),
                name="sysmon-manual-paver",
                daemon=True,
            )
            _load_thread.start()
    if already_active:
        return {"ok": False, "already_active": True, "state": get_state()}
    return {
        "ok": True,
        "started": True,
        "cpu": True,
        "memory": True,
        "duration_sec": duration_sec,
        "target_pct": target_pct,
        "state": get_state(),
    }


def _release_manual_paver_locked() -> int:
    """_lock을 잡은 호출자가 수동 보도블럭 메모리를 전량 해제한다."""
    global _manual_paver_hold, _manual_paver_allocated_mb, _manual_paver_pending_steps
    released = _manual_paver_allocated_mb
    _manual_paver_hold = []
    _manual_paver_allocated_mb = 0
    _manual_paver_pending_steps = 0
    return released


def release_manual_paver() -> dict:
    """구버전 호출 호환: CPU와 RAM을 함께 해제한다."""
    return stop_load()


def _manual_paver_worker() -> None:
    """클릭 횟수만큼 1GB를 직렬 할당하고 90% 안전선을 계속 감시한다."""
    global _manual_paver_allocated_mb, _manual_paver_pending_steps
    chunk_bytes = MEM_CHUNK_MB * 1024 * 1024
    while True:
        if _manual_paver_stop.is_set():
            return
        sample = _collect_stats()
        mem_pct = float(sample.get("memory_percent") or 0.0)
        if mem_pct >= 90.0:
            with _lock:
                released = _release_manual_paver_locked()
            logger.warning(
                "[sysmon] total memory %.1f%% >= 90%%; released manual paver %dMB",
                mem_pct, released,
            )
            return
        with _lock:
            pending = _manual_paver_pending_steps
            allocated = _manual_paver_allocated_mb
        if pending <= 0:
            if allocated <= 0:
                return
            # 할당을 유지하는 동안 1초마다 90% 기준을 감시한다.
            if _manual_paver_stop.wait(timeout=1.0):
                return
            continue

        completed = True
        try:
            for _ in range(MEM_STEP_MB // MEM_CHUNK_MB):
                if _manual_paver_stop.is_set():
                    completed = False
                    break
                if float(_collect_stats().get("memory_percent") or 0.0) >= 90.0:
                    completed = False
                    break
                block = bytearray(chunk_bytes)
                for offset in range(0, chunk_bytes, 4096):
                    block[offset] = 1
                with _lock:
                    _manual_paver_hold.append(block)
                    _manual_paver_allocated_mb += MEM_CHUNK_MB
                time.sleep(0.03)
        except MemoryError:
            completed = False
            logger.warning("[sysmon] manual paver allocation stopped by MemoryError")

        if not completed:
            with _lock:
                released = _release_manual_paver_locked()
            logger.warning("[sysmon] manual paver safety release: %dMB", released)
            return
        with _lock:
            _manual_paver_pending_steps = max(0, _manual_paver_pending_steps - 1)


def add_manual_paver_step() -> dict:
    """구버전 호출 호환: 1GB RAM 단계 대신 CPU+RAM 통합 작업을 시작한다."""
    return start_manual_load()


def _maybe_start_scheduled_load(now: _dt.datetime | None = None) -> bool:
    """설정 시각 이후 첫 tick에서 하루 한 번 예약 부하를 시작한다."""
    global _load_thread
    try:
        from core.worker_dispatch import server_role
        if server_role() == "worker":
            return False
    except Exception:
        pass
    if _load_thread and _load_thread.is_alive():
        return False
    cfg = get_schedule()
    if not cfg["enabled"]:
        return False
    local_now = now or _dt.datetime.now()
    today = local_now.date().isoformat()
    hour, minute = (int(part) for part in cfg["time"].split(":"))
    scheduled_minute = hour * 60 + minute
    current_minute = local_now.hour * 60 + local_now.minute
    # 5분 수집 tick의 흔들림만 허용한다. 서버가 오후에 재시작됐다고 놓친
    # 오전 11시 부하를 뒤늦게 실행하면 "11시경 1회"라는 운영 의도와 어긋난다.
    in_run_window = 0 <= current_minute - scheduled_minute < 10
    if cfg.get("last_run_date") == today or not in_run_window:
        return False
    # 실행 전에 날짜를 기록해 동일 tick/재시작에서 중복 상승하지 않게 한다.
    with _lock:
        latest = _normalize_schedule(load_json(SYSMON_STATE_FILE, {}))
        if latest.get("last_run_date") == today:
            return False
        latest.update(last_run_date=today, last_run_at=local_now.isoformat(timespec="seconds"))
        save_json(SYSMON_STATE_FILE, latest, indent=2)
    _load_stop.clear()
    _load_thread = threading.Thread(
        target=_load_worker,
        args=(MANUAL_LOAD_MAX_SEC, "scheduled", float(cfg["target_pct"]), True),
        name="sysmon-scheduled-load",
        daemon=True,
    )
    _load_thread.start()
    return True


def stop_load() -> dict:
    global _mem_hold, _mem_allocated_mb, _mem_cap_mb, _load_release_reason
    _load_stop.set()
    _manual_paver_stop.set()
    with _lock:
        released = _release_manual_paver_locked() + _mem_allocated_mb
        _mem_hold = []
        _mem_allocated_mb = 0
        _mem_cap_mb = 0
        _load_release_reason = "관리자가 수동 해제"
    return {"ok": True, "released_mb": released, "state": get_state()}


def _bg_loop() -> None:
    """5분 주기 샘플 + 유휴 체크. 앱 기동 시 1회 즉시 샘플."""
    try:
        collect_once()
        _maybe_start_scheduled_load()
    except Exception as e:
        logger.warning(f"initial sample failed: {e}")
    while True:
        try:
            time.sleep(SAMPLE_INTERVAL_SEC)
        except Exception:
            return
        try:
            collect_once()
            _maybe_start_scheduled_load()
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
