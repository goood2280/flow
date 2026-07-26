"""core/cache_event_log.py — 캐시 성공/실패 이벤트 로그 수집기.

관리자 캐시관리 페이지에서 예열 성공/실패, eviction, watchdog 트리거 등의
이벤트 이력을 시간순으로 볼 수 있도록 인메모리 링 버퍼에 이벤트를 저장한다.

API 엔드포인트: GET /api/splittable/cache-event-log
"""
from __future__ import annotations

import json
import logging
import copy
import os
import socket
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("flow.cache_event_log")

_MAX_EVENTS = 200
_LOCK = threading.Lock()
_EVENTS: deque[dict[str, Any]] = deque(maxlen=_MAX_EVENTS)
_ACTIVE_JOBS: dict[str, dict[str, Any]] = {}
_RECENT_JOBS: deque[dict[str, Any]] = deque(maxlen=10)
_SAMPLER_STARTED = False
_SAMPLER_WAKE = threading.Event()

# ── 서버 간 공유 이벤트 로그 ──────────────────────────────────────────────
# 운영(api)/개발(worker) 서버가 같은 data_root 를 공유하므로, 각 서버의 캐시
# 이벤트를 공유 JSONL 파일에 append 해 두면 운영 서버의 캐시관리 화면에서 두
# 서버의 로그를 한 곳에서 볼 수 있다. get_events() 가 인메모리 + 공유파일을
# eid 로 병합한다. 파일은 append-only(다중 서버 동시 기록 안전)이며 주기적으로
# 최근 N 줄만 남기고 정리한다.
_FILE_LOCK = threading.Lock()
_SHARED_MAX_LINES = 4000      # 정리 시 남길 최근 줄 수
_SHARED_TRIM_EVERY = 200      # 이 횟수마다 파일 크기 확인/정리
_append_count = 0
_ORIGIN_CACHE: dict[str, Any] = {"ts": 0.0, "label": "", "host": ""}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _shared_log_path():
    """공유 캐시 이벤트 로그 경로. data_root 를 못 읽으면 None (파일 기록 생략)."""
    try:
        from core.paths import PATHS
        p = PATHS.data_root / "logs" / "cache_events.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    except Exception:
        return None


def _origin() -> tuple[str, str]:
    """(label, host) — 이 서버의 표시 라벨과 호스트명. 5초 메모이즈."""
    now = time.time()
    if now - float(_ORIGIN_CACHE.get("ts") or 0.0) < 5.0 and _ORIGIN_CACHE.get("label"):
        return _ORIGIN_CACHE["label"], _ORIGIN_CACHE["host"]
    role = ""
    try:
        from core.worker_dispatch import server_role
        role = server_role()
    except Exception:
        pass
    prod = None
    try:
        from core.paths import PATHS
        prod = bool(PATHS.is_prod)
    except Exception:
        pass
    if role == "worker":
        label = "개발(worker)"
    elif role == "api":
        label = "운영"
    elif prod is True:
        label = "운영"
    elif prod is False:
        label = "개발"
    else:
        label = role or "server"
    try:
        host = socket.gethostname()[:24]
    except Exception:
        host = ""
    _ORIGIN_CACHE.update(ts=now, label=label, host=host)
    return label, host


def _append_shared(entry: dict[str, Any]) -> None:
    path = _shared_log_path()
    if path is None:
        return
    global _append_count
    line = json.dumps(entry, ensure_ascii=False)
    with _FILE_LOCK:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            return
        _append_count += 1
        if _append_count % _SHARED_TRIM_EVERY == 0:
            _trim_shared_locked(path)


def _trim_shared_locked(path) -> None:
    """파일이 너무 길면 최근 _SHARED_MAX_LINES 줄만 남긴다."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) <= _SHARED_MAX_LINES:
            return
        keep = lines[-_SHARED_MAX_LINES:]
        tmp = str(path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(keep)
        os.replace(tmp, path)
    except Exception:
        pass


def _read_shared_tail(max_lines: int) -> list[dict[str, Any]]:
    path = _shared_log_path()
    if path is None or not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        for raw in lines[-max_lines:]:
            raw = raw.strip()
            if not raw:
                continue
            try:
                out.append(json.loads(raw))
            except Exception:
                continue
    except Exception:
        return []
    return out


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
    origin_label, origin_host = _origin()
    entry = {
        "eid": uuid.uuid4().hex,
        "ts": time.time(),
        "ts_iso": _utc_iso(),
        "category": category,
        "event": event,
        "ok": ok,
        "product": product,
        "origin": origin_label,   # "운영" | "개발" | "개발(worker)"
        "host": origin_host,
    }
    if detail:
        entry["detail"] = detail
    with _LOCK:
        _EVENTS.append(entry)
        # 이 이벤트가 특정 작업(job)에 속하면 그 작업의 '마지막 진행 시각'을 갱신한다.
        # get_jobs() 가 이 값으로 '진행 없음(정지)'을 판정하므로, 로그가 계속 찍히는
        # 동안은 절대 stale 로 오판하지 않는다.
        job_id = str((detail or {}).get("job_id") or "")
        if job_id:
            job = _ACTIVE_JOBS.get(job_id)
            if job:
                job["updated_ts"] = entry["ts"]
                job["last_event"] = event
    # 서버 간 공유 — 다른 서버(운영)의 캐시관리 화면에서도 보이도록 파일에 append.
    _append_shared(entry)


def get_events(limit: int = 100, category: str = "") -> list[dict[str, Any]]:
    """최근 이벤트 반환 (최신순). 인메모리(이 서버) + 공유파일(전 서버)을 eid 로
    병합해 운영/개발 두 서버의 캐시 로그를 한 곳에서 본다."""
    with _LOCK:
        mem = list(_EVENTS)
    merged: dict[str, dict[str, Any]] = {}
    # 공유 파일 먼저(전 서버) → 인메모리로 덮어씀(이 서버가 최신 origin 보유).
    for e in _read_shared_tail(max(600, limit * 4)):
        eid = e.get("eid")
        merged[eid if eid else f"f{len(merged)}"] = e
    for e in mem:
        eid = e.get("eid")
        merged[eid if eid else f"m{len(merged)}"] = e
    items = list(merged.values())
    if category:
        items = [e for e in items if e.get("category") == category]
    items.sort(key=lambda e: e.get("ts", 0), reverse=True)
    return items[:limit]


def _memory_sample() -> dict[str, float]:
    try:
        from core.runtime_limits import process_memory_snapshot

        snap = process_memory_snapshot()
        return {
            "rss_gb": float(snap.get("process_rss_gb") or 0.0),
            "effective_gb": float(snap.get("process_memory_effective_gb") or 0.0),
            "system_available_gb": float(snap.get("system_memory_available_gb") or 0.0),
        }
    except Exception:
        return {"rss_gb": 0.0, "effective_gb": 0.0, "system_available_gb": 0.0}


def _apply_memory_sample(job: dict[str, Any], sample: dict[str, float]) -> None:
    rss = float(sample.get("rss_gb") or 0.0)
    effective = float(sample.get("effective_gb") or 0.0)
    available = float(sample.get("system_available_gb") or 0.0)
    job["current_rss_gb"] = round(rss, 3)
    job["current_effective_gb"] = round(effective, 3)
    job["peak_rss_gb"] = round(max(float(job.get("peak_rss_gb") or 0.0), rss), 3)
    job["peak_effective_gb"] = round(max(float(job.get("peak_effective_gb") or 0.0), effective), 3)
    if available > 0:
        previous = float(job.get("min_system_available_gb") or 0.0)
        job["min_system_available_gb"] = round(available if previous <= 0 else min(previous, available), 3)
    baseline = float(job.get("start_effective_gb") or 0.0)
    job["peak_delta_gb"] = round(max(0.0, float(job.get("peak_effective_gb") or 0.0) - baseline), 3)


def _sampler_loop() -> None:
    while True:
        _SAMPLER_WAKE.wait(timeout=1.0)
        _SAMPLER_WAKE.clear()
        with _LOCK:
            active = bool(_ACTIVE_JOBS)
        if not active:
            continue
        sample = _memory_sample()
        with _LOCK:
            for job in _ACTIVE_JOBS.values():
                _apply_memory_sample(job, sample)


def _ensure_sampler_locked() -> None:
    global _SAMPLER_STARTED
    if _SAMPLER_STARTED:
        return
    _SAMPLER_STARTED = True
    threading.Thread(target=_sampler_loop, name="cache-job-memory-sampler", daemon=True).start()


def start_job(kind: str, label: str, stages: list[tuple[str, str]], *, product: str = "") -> str:
    """실행/대기 stage와 작업별 peak memory를 추적할 job을 등록한다."""
    job_id = uuid.uuid4().hex[:12]
    now = time.time()
    sample = _memory_sample()
    job = {
        "id": job_id,
        "kind": str(kind or "cache"),
        "label": str(label or kind or "cache job"),
        "product": str(product or ""),
        "status": "running",
        "started_ts": now,
        "updated_ts": now,
        "last_event": "",
        "started_at": _utc_iso(),
        "finished_at": "",
        "current_stage": "",
        "stages": [
            {"id": str(stage_id), "label": str(stage_label), "status": "queued", "started_at": "", "finished_at": ""}
            for stage_id, stage_label in stages
        ],
        "start_rss_gb": round(float(sample.get("rss_gb") or 0.0), 3),
        "start_effective_gb": round(float(sample.get("effective_gb") or 0.0), 3),
        "peak_rss_gb": 0.0,
        "peak_effective_gb": 0.0,
        "peak_delta_gb": 0.0,
        "min_system_available_gb": 0.0,
    }
    _apply_memory_sample(job, sample)
    with _LOCK:
        _ACTIVE_JOBS[job_id] = job
        _ensure_sampler_locked()
    _SAMPLER_WAKE.set()
    return job_id


def _update_stage(job_id: str, stage_id: str, status: str, detail: dict[str, Any] | None = None) -> None:
    sample = _memory_sample()
    now_iso = _utc_iso()
    with _LOCK:
        job = _ACTIVE_JOBS.get(job_id)
        if not job:
            return
        _apply_memory_sample(job, sample)
        job["updated_ts"] = time.time()
        for stage in job.get("stages") or []:
            if stage.get("id") != stage_id:
                continue
            stage["status"] = status
            if status == "running":
                stage["started_at"] = stage.get("started_at") or now_iso
                job["current_stage"] = stage_id
            elif status in {"done", "failed", "skipped"}:
                stage["finished_at"] = now_iso
                if detail:
                    stage["detail"] = dict(detail)
                if job.get("current_stage") == stage_id:
                    job["current_stage"] = ""
            break


def stage_started(job_id: str, stage_id: str) -> None:
    _update_stage(job_id, stage_id, "running")


def heartbeat(job_id: str, note: str = "") -> None:
    """작업이 살아 있음을 알린다 — 오래 걸리는 대기(다른 잡 종료 대기, 랏캐시 빌드
    대기)에서 주기적으로 호출한다. 이 값이 갱신되지 않으면 get_jobs() 가 정지로
    보고 자동 실패 처리하므로, '진짜 진행 중'인 작업은 반드시 여기를 두드려야 한다."""
    if not job_id:
        return
    with _LOCK:
        job = _ACTIVE_JOBS.get(job_id)
        if not job:
            return
        job["updated_ts"] = time.time()
        if note:
            job["last_event"] = str(note)


def stage_skipped(job_id: str, stage_id: str, detail: dict[str, Any] | None = None) -> None:
    """단계를 '건너뜀'으로 종료 — 비활성(설정에서 꺼짐) 등. 실패가 아니다."""
    _update_stage(job_id, stage_id, "skipped", detail)


def stage_finished(job_id: str, stage_id: str, *, ok: bool, detail: dict[str, Any] | None = None) -> None:
    _update_stage(job_id, stage_id, "done" if ok else "failed", detail)


def finish_job(job_id: str, *, ok: bool, detail: dict[str, Any] | None = None) -> None:
    sample = _memory_sample()
    with _LOCK:
        job = _ACTIVE_JOBS.pop(job_id, None)
        if not job:
            return
        _apply_memory_sample(job, sample)
        job["status"] = "done" if ok else "failed"
        job["finished_at"] = _utc_iso()
        job["finished_ts"] = time.time()
        job["current_stage"] = ""
        if detail:
            job["detail"] = dict(detail)
        for stage in job.get("stages") or []:
            if stage.get("status") in {"queued", "running"}:
                stage["status"] = "skipped"
                stage["finished_at"] = job["finished_at"]
        _RECENT_JOBS.appendleft(copy.deepcopy(job))


def stale_after_sec() -> float:
    """이 시간 동안 아무 진행(이벤트/단계 변화/heartbeat)이 없으면 정지로 본다."""
    try:
        value = float(os.environ.get("FLOW_CACHE_JOB_STALE_SEC", "") or 1800.0)
    except Exception:
        value = 1800.0
    return max(120.0, min(6 * 3600.0, value))


def reap_stale_jobs() -> list[str]:
    """진행이 끊긴 작업을 '실패(응답 없음)'로 종료한다.

    스레드가 예외 없이 죽거나 외부 호출에 영원히 붙잡히면 작업이 running 으로
    남아 화면이 무한 로딩된다. 진행 신호(updated_ts)가 stale_after_sec 동안
    없으면 실패로 확정해 화면이 반드시 끝난 상태를 보게 한다."""
    limit = stale_after_sec()
    now = time.time()
    stale_ids: list[str] = []
    with _LOCK:
        for job_id, job in list(_ACTIVE_JOBS.items()):
            idle = now - float(job.get("updated_ts") or job.get("started_ts") or now)
            if idle >= limit:
                stale_ids.append(job_id)
    for job_id in stale_ids:
        idle_txt = f"{int(limit // 60)}분"
        finish_job(job_id, ok=False, detail={"error": "stale_no_progress", "idle_limit_sec": limit})
        try:
            record("scan", f"[작업 실패] 진행 신호 없음 — {idle_txt} 이상 아무 진행이 없어 중단 처리했습니다"
                           f" (job {job_id}). 서버 로그(터미널)에서 원인을 확인하세요.", ok=False)
        except Exception:
            pass
    return stale_ids


def get_jobs(*, recent: int = 3) -> list[dict[str, Any]]:
    """활성 작업을 먼저, 그 뒤 최근 완료 작업을 반환한다.

    반환 전 정지된 작업을 실패로 확정하고(reap), 각 작업에 경과/무진행 시간을
    덧붙인다 — 화면이 '얼마나 돌고 있는지'와 '멈춘 건지'를 구분할 수 있게 한다."""
    reap_stale_jobs()
    now = time.time()
    limit = stale_after_sec()
    with _LOCK:
        active = [copy.deepcopy(job) for job in _ACTIVE_JOBS.values()]
        history = [copy.deepcopy(job) for job in list(_RECENT_JOBS)[:max(0, recent)]]
    active.sort(key=lambda row: float(row.get("started_ts") or 0.0), reverse=True)
    for job in active:
        started = float(job.get("started_ts") or now)
        updated = float(job.get("updated_ts") or started)
        job["elapsed_sec"] = round(max(0.0, now - started), 1)
        job["idle_sec"] = round(max(0.0, now - updated), 1)
        job["stale_after_sec"] = limit
    for job in history:
        started = float(job.get("started_ts") or now)
        job["elapsed_sec"] = round(max(0.0, float(job.get("finished_ts") or now) - started), 1)
        job["idle_sec"] = 0.0
    return active + history


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
