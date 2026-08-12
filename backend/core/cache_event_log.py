"""core/cache_event_log.py — 캐시 성공/실패 이벤트 로그 수집기.

관리자 캐시관리 페이지에서 예열 성공/실패, eviction, watchdog 트리거 등의
이벤트 이력을 시간순으로 볼 수 있도록 인메모리 링 버퍼에 이벤트를 저장한다.

API 엔드포인트: GET /api/splittable/cache-event-log
"""
from __future__ import annotations

import json
import logging
import copy
import hashlib
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

# 프로세스의 ru_maxrss 는 "기동 후 전체 최대"만 알려 주고 언제 발생했는지는
# 알려 주지 않는다. 최근 2일 peak 는 워치독의 주기 RSS 샘플을 별도 보관해 계산한다.
# 공유 data_root 를 쓰는 운영/개발 서버가 섞이지 않도록 origin + host 로 구분한다.
_RAM_PEAK_FILE_LOCK = threading.Lock()
_RAM_PEAK_TRIM_EVERY = 500
_RAM_PEAK_MAX_LINES = 30000
_ram_peak_append_count = 0


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


def _ram_peak_log_path():
    try:
        from core.paths import PATHS
        # 서버별 파일로 나눠 최근 2일 조회가 다른 서버 샘플 때문에 잘리지 않게 한다.
        origin_label, origin_host = _origin()
        server_key = hashlib.sha1(f"{origin_label}|{origin_host}".encode("utf-8")).hexdigest()[:12]
        p = PATHS.data_root / "logs" / f"ram_peak_samples_{server_key}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    except Exception:
        return None


def _trim_ram_peak_samples_locked(path) -> None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) <= _RAM_PEAK_MAX_LINES:
            return
        tmp = str(path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(lines[-_RAM_PEAK_MAX_LINES:])
        os.replace(tmp, path)
    except Exception:
        pass


def record_ram_peak_sample(rss_gb: float | None = None, *, ts: float | None = None,
                           effective_gb: float | None = None,
                           effective_kind: str | None = None) -> None:
    """현재 RSS/Effective 를 최근 peak 계산용 공유 로그에 기록한다.

    메모리 워치독이 주기적으로 호출한다. 실패해도 감시/요청 경로에는 영향을 주지
    않도록 모든 I/O 오류를 삼킨다.

    **두 값을 같이 남기는 이유**: RSS 는 mmap 된 parquet 페이지까지 세므로
    (polars 가 parquet 을 mmap 한다) 컨테이너 working set — Grafana 의
    `container_memory_working_set_bytes`, 회수 가능한 file cache 를 뺀 값 — 보다
    크게 나온다. 실제로 "캐시관리 peak 이 Grafana 보다 훨씬 높다"는 신고가 이
    차이였다. Effective 는 `/proc/self/smaps_rollup` 의 PSS(공유 페이지 비례
    배분)라 mmap 부풀림이 없어 Grafana 와 직접 비교할 수 있다. peak 을 RSS 로만
    들고 있으면 비교 가능한 쪽의 최댓값을 영영 알 수 없다.
    """
    if rss_gb is None or effective_gb is None:
        sample = _memory_sample()
        if rss_gb is None:
            rss_gb = float(sample.get("rss_gb") or 0.0)
        if effective_gb is None:
            effective_gb = float(sample.get("effective_gb") or 0.0)
        if effective_kind is None:
            effective_kind = str(sample.get("effective_kind") or "")
    rss_gb = float(rss_gb or 0.0)
    effective_gb = float(effective_gb or 0.0)
    if rss_gb <= 0:
        return
    path = _ram_peak_log_path()
    if path is None:
        return
    origin_label, origin_host = _origin()
    row = {
        "ts": float(ts if ts is not None else time.time()),
        "rss_gb": round(rss_gb, 4),
        # Linux 가 아니면 smaps_rollup 이 없어 0 이다 — 그때는 읽는 쪽이 RSS 로 폴백한다.
        "effective_gb": round(effective_gb, 4),
        "effective_kind": str(effective_kind or "legacy"),
        "origin": origin_label,
        "host": origin_host,
    }
    global _ram_peak_append_count
    with _RAM_PEAK_FILE_LOCK:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception:
            return
        _ram_peak_append_count += 1
        if _ram_peak_append_count % _RAM_PEAK_TRIM_EVERY == 0:
            _trim_ram_peak_samples_locked(path)


def recent_peak_rss(hours: float = 48.0, *, effective_kind: str = "") -> dict[str, Any]:
    """이 서버(origin + host)의 최근 RSS/Effective 최대값과 측정 시각.

    `peak_rss_gb` 는 mmap 파일 캐시를 포함한 진단값이고, `peak_effective_gb` 는
    Anonymous 우선(PSS/USS/RSS 폴백)의 실제 압박 판단값이다. 두 최댓값은 서로
    다른 시점일 수 있으므로 각각의 시각을 준다.
    """
    empty = {"peak_rss_gb": 0.0, "peak_ts": 0.0, "sample_count": 0,
             "peak_effective_gb": 0.0, "peak_effective_ts": 0.0,
             "effective_sample_count": 0}
    path = _ram_peak_log_path()
    if path is None or not path.exists():
        return dict(empty)
    since = time.time() - max(0.1, float(hours or 48.0)) * 3600.0
    origin_label, origin_host = _origin()
    peak = 0.0
    peak_ts = 0.0
    peak_eff = 0.0
    peak_eff_ts = 0.0
    count = 0
    eff_count = 0
    try:
        with _RAM_PEAK_FILE_LOCK:
            with open(path, "r", encoding="utf-8") as f:
                for raw in f:
                    try:
                        row = json.loads(raw)
                    except Exception:
                        continue
                    row_ts = float(row.get("ts") or 0.0)
                    if row_ts < since:
                        continue
                    if str(row.get("origin") or "") != origin_label or str(row.get("host") or "") != origin_host:
                        continue
                    count += 1
                    value = float(row.get("rss_gb") or 0.0)
                    if value >= peak:
                        peak = value
                        peak_ts = row_ts
                    # 옛 샘플에는 effective 가 없다 — 그 줄은 세지 않는다.
                    # 0 으로 세면 "Effective peak 0GB" 라는 거짓 안심을 준다.
                    row_kind = str(row.get("effective_kind") or "legacy")
                    eff = float(row.get("effective_gb") or 0.0)
                    if eff > 0 and (not effective_kind or row_kind == effective_kind):
                        eff_count += 1
                        if eff >= peak_eff:
                            peak_eff = eff
                            peak_eff_ts = row_ts
    except Exception:
        return dict(empty)
    return {
        "peak_rss_gb": round(peak, 3),
        "peak_ts": peak_ts,
        "sample_count": count,
        "peak_effective_gb": round(peak_eff, 3),
        "peak_effective_ts": peak_eff_ts,
        "effective_sample_count": eff_count,
    }


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
    """파일이 너무 길면 최근 _SHARED_MAX_LINES 줄만 남긴다.

    운영/개발 두 서버가 **같은 파일에** append 한다. _FILE_LOCK 은 프로세스
    안에서만 유효하므로, 읽고→다시 쓰는 사이에 다른 서버가 붙인 줄을 그냥
    덮어쓰면 그 서버의 최신 로그가 통째로 사라진다 — 캐시관리에서 "한쪽 서버
    로그만 보인다"의 원인이다. 읽은 시점의 offset 을 기억했다가 그 뒤에 붙은
    바이트를 새 파일 끝에 이어 붙인 뒤 교체한다. tmp 이름도 프로세스별로
    나눠 두 서버가 서로의 임시 파일을 덮어쓰지 않게 한다.
    """
    try:
        with open(path, "rb") as f:
            raw = f.read()
            end_offset = f.tell()
        lines = raw.splitlines(keepends=True)
        if len(lines) <= _SHARED_MAX_LINES:
            return
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "wb") as f:
            f.writelines(lines[-_SHARED_MAX_LINES:])
            try:
                with open(path, "rb") as src:
                    src.seek(end_offset)
                    while True:
                        chunk = src.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
            except Exception:
                pass
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
        # Cache builders invoked inside a scan-gate task do not all receive the
        # outer UI job id.  Associate their progress events with the active job
        # through the shared gate task id so real builder output is also a
        # heartbeat (and a genuinely silent builder can still be reaped).
        if not job_id:
            try:
                from core import scan_gate
                task_id = scan_gate.current_task_id()
            except Exception:
                task_id = ""
            if task_id:
                job_id = next(
                    (active_id for active_id, active in _ACTIVE_JOBS.items()
                     if str(active.get("task_id") or "") == task_id),
                    "",
                )
        if not job_id and product:
            # FAB index batches are emitted by a child builder thread, so that
            # thread has no scan-gate TLS task id. Associate an unambiguous
            # product/stage progress event with the active outer pipeline.
            progress_kind = str((((detail or {}).get("progress") or {}).get("kind") or ""))
            stage_kind = {
                "lookup_build": "lookup", "pivot_build": "pivot",
                "latest_lot": "latest_lot", "fab_index": "fab_index",
            }
            matches = [
                active_id for active_id, active in _ACTIVE_JOBS.items()
                if str(active.get("product") or "") == str(product or "")
                and (not progress_kind
                     or stage_kind.get(str(active.get("current_stage") or "")) == progress_kind)
            ]
            if len(matches) == 1:
                job_id = matches[0]
        if job_id:
            entry.setdefault("detail", {})["job_id"] = job_id
            job = _ACTIVE_JOBS.get(job_id)
            if job:
                job["updated_ts"] = entry["ts"]
                job["last_event"] = event
    # 서버 간 공유 — 다른 서버(운영)의 캐시관리 화면에서도 보이도록 파일에 append.
    _append_shared(entry)


def _fair_by_origin(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """서버(origin+host)별로 번갈아 뽑아 limit 을 채운다.

    캐시 작업은 대부분 한쪽 서버(보통 개발 워커)에서 돌아 그 서버가 로그를
    훨씬 많이 남긴다. 단순 "최신순 상위 N" 은 그 서버 줄로만 채워져 다른
    서버의 로그가 화면에서 통째로 사라진다 — 캐시관리는 운영·개발 로그를
    **함께** 봐야 하므로 라운드로빈으로 뽑아 두 서버가 반드시 같이 보이게
    하고, 고른 뒤 다시 시간순으로 되돌린다. 서버가 하나뿐이면 그대로 자른다.

    items 는 최신순으로 정렬되어 있어야 한다.
    """
    limit = max(0, int(limit or 0))
    if not items or limit <= 0:
        return []
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for event in items:
        key = (str(event.get("origin") or ""), str(event.get("host") or ""))
        groups.setdefault(key, []).append(event)
    if len(groups) <= 1:
        return items[:limit]
    buckets = list(groups.values())          # 각 버킷도 최신순 (items 순서 유지)
    picked: list[dict[str, Any]] = []
    depth = 0
    while len(picked) < limit:
        added = False
        for bucket in buckets:
            if depth >= len(bucket):
                continue
            picked.append(bucket[depth])
            added = True
            if len(picked) >= limit:
                break
        if not added:
            break
        depth += 1
    picked.sort(key=lambda e: e.get("ts", 0), reverse=True)
    return picked


def get_events(limit: int = 100, category: str = "") -> list[dict[str, Any]]:
    """최근 이벤트 반환 (최신순). 인메모리(이 서버) + 공유파일(전 서버)을 eid 로
    병합해 운영/개발 두 서버의 캐시 로그를 한 곳에서 본다."""
    with _LOCK:
        mem = list(_EVENTS)
    merged: dict[str, dict[str, Any]] = {}
    # 공유 파일 먼저(전 서버) → 인메모리로 덮어씀(이 서버가 최신 origin 보유).
    # 파일 상한(_SHARED_MAX_LINES)까지 넉넉히 읽는다 — 창이 좁으면 바쁜 서버의
    # 줄만 창 안에 남아 다른 서버가 아예 후보에서 빠진다.
    for e in _read_shared_tail(max(_SHARED_MAX_LINES, limit * 4)):
        eid = e.get("eid")
        merged[eid if eid else f"f{len(merged)}"] = e
    for e in mem:
        eid = e.get("eid")
        merged[eid if eid else f"m{len(merged)}"] = e
    items = list(merged.values())
    if category:
        items = [e for e in items if e.get("category") == category]
    items.sort(key=lambda e: e.get("ts", 0), reverse=True)
    return _fair_by_origin(items, limit)


# ── 전체 캐시 진행률 ──────────────────────────────────────────────────────
# 빌더들은 제품별로 "3/10 랏" 을 남기지만, 관리자가 알고 싶은 건 "전체 캐시가
# 몇 랏 중 몇 랏 됐나" 다. 별도 상태 레지스트리를 두는 대신 진행 이벤트의
# detail["progress"] 를 모아 합산한다 — 그래야 운영/개발 워커가 제품을 나눠
# 빌드해도 공유 이벤트 로그만으로 합계가 맞고, 프로세스가 죽어도 새어 나갈
# 상태가 없다(오래된 항목은 창을 벗어나며 자연 소멸).
_PROGRESS_WINDOW_SEC = 900.0
_PROGRESS_STALE_SEC = 180.0


def progress_detail(kind: str, done: int, total: int, *, state: str = "running",
                    unit: str = "랏") -> dict[str, Any]:
    """진행 이벤트 detail 에 넣을 표준 progress 블록."""
    total = max(0, int(total or 0))
    done = max(0, min(int(done or 0), total or int(done or 0)))
    return {"kind": str(kind or "cache"), "done": done, "total": total,
            "state": str(state or "running"), "unit": str(unit or "랏")}


def progress_snapshot(*, window_sec: float = _PROGRESS_WINDOW_SEC,
                      recent_limit: int = 5) -> dict[str, Any]:
    """진행 중인 **(제품 × 캐시 종류)별** 랏 진행률. 합산하지 않는다.

    여러 제품·여러 종류의 랏 수를 한 분모로 더하면 "40/100 랏"이 무엇의 40인지
    알 수 없다 — 제품마다 랏 수가 다르고 종류마다 하는 일이 다르므로 합계는
    의미가 없다. 그래서 각 (제품, 종류)를 **자기 분모로** 따로 준다.

    `recent` 는 방금 끝난 것 몇 개(기본 5) — 진행이 없을 때 화면이 텅 비지
    않게 직전 결과만 보여주는 용도다.
    """
    now = time.time()
    with _LOCK:
        active_job_ids = set(_ACTIVE_JOBS)
    seen: dict[tuple, dict[str, Any]] = {}
    # get_events 는 최신순이므로 각 키의 첫 항목이 최신이다.
    for event in get_events(limit=600):
        progress = ((event.get("detail") or {}).get("progress") or {})
        if not isinstance(progress, dict) or not progress.get("kind"):
            continue
        if now - float(event.get("ts") or 0.0) > max(60.0, float(window_sec or 0.0)):
            continue
        key = (event.get("origin") or "", event.get("host") or "",
               progress.get("kind"), event.get("product") or "")
        if key in seen:
            continue
        total = int(progress.get("total") or 0)
        done = int(progress.get("done") or 0)
        state = str(progress.get("state") or "running")
        seen[key] = {
            "product": event.get("product") or "",
            "kind": progress.get("kind"),
            "unit": progress.get("unit") or "랏",
            "done": done,
            "total": total,
            "pct": int(done * 100 / total) if total else 0,
            "state": state,
            "origin": event.get("origin") or "",
            "host": event.get("host") or "",
            "ts": float(event.get("ts") or 0.0),
            "idle_sec": round(max(0.0, now - float(event.get("ts") or 0.0)), 1),
            "event": event.get("event") or "",
            "job_id": str((event.get("detail") or {}).get("job_id") or ""),
        }
    # 각 키의 최신 상태가 running 인 것만 진행 중으로 본다.
    # job 이 이미 종료됐는데 builder가 마지막 done progress를 못 남긴 경우에는
    # 오래된 running 줄을 현재 작업으로 계속 노출하지 않는다.
    rows = [
        r for r in seen.values()
        if r["state"] == "running"
        and (not r.get("job_id") or r.get("job_id") in active_job_ids)
    ]
    rows.sort(key=lambda r: -r["ts"])
    for row in rows:
        row["stalled"] = bool(row["idle_sec"] >= _PROGRESS_STALE_SEC)
    recent = [r for r in seen.values() if r["state"] != "running"]
    recent.sort(key=lambda r: -r["ts"])
    recent = recent[:max(0, int(recent_limit or 0))]
    return {
        "items": rows,
        "recent": recent,
        "active_count": len(rows),
        "products_active": sorted({r["product"] for r in rows if r["product"]}),
        "stalled_count": sum(1 for r in rows if r.get("stalled")),
        "window_sec": float(window_sec or _PROGRESS_WINDOW_SEC),
    }


# ── 제품별 캐시 작업 이력 ────────────────────────────────────────────────
# "지금 몇 % 진행중"보다 관리자가 실제로 묻는 것: **제품별로 각 캐싱 작업이
# 마지막으로 언제 성공했고, 어디서 실패했나.** 진행 이벤트를 문자열로 추측하지
# 않도록 수명주기 이벤트에 detail["stage"] = {kind, phase} 를 명시적으로 붙이고
# 여기서 그것만 읽는다.
CACHE_KIND_LABELS: dict[str, str] = {
    "lookup": "랏 lookup",
    "pivot": "SplitTable pivot",
    "match": "FAB 매칭",
    "fab_index": "FAB latest 인덱스",
    "latest_lot": "WIP latest-lot",
    "et_history": "ET history",
}
PRODUCT_STATUS_KINDS: tuple[str, ...] = (
    "lookup", "pivot", "latest_lot", "fab_index",
)
_PRODUCT_STATUS_WINDOW_SEC = 7 * 24 * 3600.0


def stage_detail(kind: str, phase: str) -> dict[str, Any]:
    """캐시 작업 수명주기 표식. phase: start | done | fail | skip."""
    return {"kind": str(kind or ""), "phase": str(phase or "")}


def milestones(limit: int = 40) -> list[dict[str, Any]]:
    """제품별 '큰 단위' 캐시 작업 이력 — 시작/완료/실패만 골라 시간순으로.

    캐시 이벤트 로그는 청크·배치 진행까지 다 흘러서 "이 제품 캐시가 언제
    시작해서 언제 끝났나"가 묻히다. 수명주기 표식(detail.stage)이 붙은 줄만
    남기면 같은 로그 형식 그대로 제품 단위 이력이 된다.
    """
    out: list[dict[str, Any]] = []
    for event in get_events(limit=600):
        stage = ((event.get("detail") or {}).get("stage") or {})
        if not isinstance(stage, dict):
            continue
        kind = str(stage.get("kind") or "")
        phase = str(stage.get("phase") or "")
        # 운영 요약에는 제품별로 선행 생성하는 필수 4종만 남긴다.
        # 과거 FAB match/RAM 예열 이벤트는 아래 원본 이벤트 로그에서만 확인한다.
        if kind not in PRODUCT_STATUS_KINDS or not phase:
            continue
        out.append({
            "ts": float(event.get("ts") or 0.0),
            "ts_iso": event.get("ts_iso") or "",
            "product": event.get("product") or "",
            "kind": kind,
            "label": CACHE_KIND_LABELS.get(kind, kind),
            "phase": phase,
            "ok": bool(event.get("ok", True)),
            "event": event.get("event") or "",
            "origin": event.get("origin") or "",
            "host": event.get("host") or "",
        })
        if len(out) >= max(1, int(limit or 40)):
            break
    return out


def product_status(*, window_sec: float = _PRODUCT_STATUS_WINDOW_SEC,
                   products: list[str] | tuple[str, ...] | None = None) -> dict[str, Any]:
    """제품 × 캐싱 작업별 최근 시작/성공/실패 시각.

    한 제품의 모든 작업이 마지막에 성공했으면 그 제품은 'ok' 다. 하나라도
    실패로 끝났으면 'failed', 아직 도는 중이면 'running', 창 안에 기록이 아예
    없으면 'never'(기록 없음)로 둔다 — 기록이 없는 것과 실패한 것은 다르다.
    """
    now = time.time()
    cutoff = now - max(3600.0, float(window_sec or _PRODUCT_STATUS_WINDOW_SEC))
    slots: dict[tuple[str, str], dict[str, Any]] = {}

    def _slot(product: str, kind: str) -> dict[str, Any]:
        key = (product, kind)
        if key not in slots:
            slots[key] = {
                "product": product, "kind": kind,
                "label": CACHE_KIND_LABELS.get(kind, kind),
                "started_ts": 0.0, "success_ts": 0.0, "failed_ts": 0.0, "skipped_ts": 0.0,
                "last_ts": 0.0, "message": "", "fail_message": "",
                "origin": "", "host": "",
            }
        return slots[key]

    events = get_events(limit=600)
    event_products = {
        str(event.get("product") or "")
        for event in events
        if str(event.get("product") or "")
        and float(event.get("ts") or 0.0) >= cutoff
    }
    allowed_products = (
        {str(product or "").strip() for product in products if str(product or "").strip()}
        if products is not None else None
    )
    # A supplied catalog is authoritative.  Test/diagnostic events can remain
    # in the shared seven-day log, but must never create fake product rows in
    # the operator-facing cache status list.
    products_in_window = set(allowed_products) if allowed_products is not None else event_products
    products_in_window.discard("")
    # 제품마다 필수 4종을 항상 만든다. 기록이 없는 작업도 빈 칸으로 남겨야
    # 한 제품의 성공/실패/미실행 상태를 한 화면에서 비교할 수 있다.
    for product in sorted(products_in_window):
        for kind in PRODUCT_STATUS_KINDS:
            _slot(product, kind)

    # 오래된 것부터 훑어 최신이 덮어쓰게 한다.
    for event in reversed(events):
        stage = ((event.get("detail") or {}).get("stage") or {})
        if not isinstance(stage, dict):
            continue
        kind = str(stage.get("kind") or "")
        phase = str(stage.get("phase") or "")
        product = str(event.get("product") or "")
        ts = float(event.get("ts") or 0.0)
        if (kind not in PRODUCT_STATUS_KINDS or not phase or not product or ts < cutoff
                or (allowed_products is not None and product not in allowed_products)):
            continue
        slot = _slot(product, kind)
        slot["last_ts"] = ts
        slot["origin"] = event.get("origin") or slot["origin"]
        slot["host"] = event.get("host") or slot["host"]
        if phase == "start":
            slot["started_ts"] = ts
            slot["message"] = event.get("event") or ""
        elif phase == "done":
            slot["success_ts"] = ts
            slot["message"] = event.get("event") or ""
            slot["fail_message"] = ""
        elif phase == "skip":
            slot["skipped_ts"] = ts
            slot["success_ts"] = max(float(slot["success_ts"]), ts)
            slot["message"] = event.get("event") or ""
        elif phase == "fail":
            slot["failed_ts"] = ts
            slot["fail_message"] = event.get("event") or ""

    def _kind_state(slot: dict[str, Any]) -> str:
        success = float(slot["success_ts"])
        failed = float(slot["failed_ts"])
        started = float(slot["started_ts"])
        if started > success and started > failed:
            return "running"
        if failed > success:
            return "failed"
        if success > 0:
            return "ok"
        return "never"

    products: dict[str, dict[str, Any]] = {}
    for (product, _kind), slot in slots.items():
        state = _kind_state(slot)
        slot = dict(slot)
        slot["state"] = state
        slot["duration_sec"] = (
            round(float(slot["success_ts"]) - float(slot["started_ts"]), 1)
            if slot["success_ts"] and slot["started_ts"] and slot["success_ts"] >= slot["started_ts"]
            else 0.0
        )
        slot["idle_sec"] = round(max(0.0, now - float(slot["last_ts"] or now)), 1)
        slot["stalled"] = bool(state == "running" and slot["idle_sec"] >= _PROGRESS_STALE_SEC)
        row = products.setdefault(product, {"product": product, "kinds": []})
        row["kinds"].append(slot)

    out: list[dict[str, Any]] = []
    for row in products.values():
        row["kinds"].sort(
            key=lambda k: PRODUCT_STATUS_KINDS.index(k["kind"])
            if k["kind"] in PRODUCT_STATUS_KINDS else 99
        )
        states = {k["state"] for k in row["kinds"]}
        if "running" in states:
            row["state"] = "running"
        elif "failed" in states:
            row["state"] = "failed"
        elif states and states <= {"ok"}:
            row["state"] = "ok"
        else:
            row["state"] = "partial"
        successes = [float(k["success_ts"]) for k in row["kinds"] if k["success_ts"]]
        failures = [float(k["failed_ts"]) for k in row["kinds"] if k["state"] == "failed"]
        # 제품의 '전체 성공 시각'은 가장 늦게 성공한 작업 기준이다 — 그때서야
        # 이 제품의 캐시 한 벌이 다 갖춰진다.
        row["all_success_ts"] = max(successes) if successes and row["state"] == "ok" else 0.0
        row["last_success_ts"] = max(successes) if successes else 0.0
        row["last_failure_ts"] = max(failures) if failures else 0.0
        row["failed_kinds"] = [k["label"] for k in row["kinds"] if k["state"] == "failed"]
        row["running_kinds"] = [k["label"] for k in row["kinds"] if k["state"] == "running"]
        out.append(row)

    # 실패 → 진행중 → 나머지 순, 그 안에서는 최근 활동 순.
    order = {"failed": 0, "running": 1, "partial": 2, "ok": 3}
    out.sort(key=lambda r: (order.get(r["state"], 4),
                            -max([float(k["last_ts"]) for k in r["kinds"]] or [0.0]),
                            str(r.get("product") or "")))
    return {
        "products": out,
        "kinds": list(PRODUCT_STATUS_KINDS),
        "kind_labels": {kind: CACHE_KIND_LABELS[kind] for kind in PRODUCT_STATUS_KINDS},
        "ok_count": sum(1 for r in out if r["state"] == "ok"),
        "failed_count": sum(1 for r in out if r["state"] == "failed"),
        "running_count": sum(1 for r in out if r["state"] == "running"),
        "window_sec": float(window_sec or _PRODUCT_STATUS_WINDOW_SEC),
    }


def _memory_sample() -> dict[str, Any]:
    try:
        from core.runtime_limits import process_memory_snapshot

        snap = process_memory_snapshot()
        return {
            "rss_gb": float(snap.get("process_rss_gb") or 0.0),
            "effective_gb": float(snap.get("process_memory_effective_gb") or 0.0),
            "effective_kind": str(snap.get("process_memory_effective_kind") or "rss_fallback"),
            "system_available_gb": float(snap.get("system_memory_available_gb") or 0.0),
        }
    except Exception:
        return {"rss_gb": 0.0, "effective_gb": 0.0,
                "effective_kind": "unavailable", "system_available_gb": 0.0}


def _apply_memory_sample(job: dict[str, Any], sample: dict[str, Any]) -> None:
    rss = float(sample.get("rss_gb") or 0.0)
    effective = float(sample.get("effective_gb") or 0.0)
    available = float(sample.get("system_available_gb") or 0.0)
    job["current_rss_gb"] = round(rss, 3)
    job["current_effective_gb"] = round(effective, 3)
    job["memory_metric_kind"] = str(sample.get("effective_kind") or job.get("memory_metric_kind") or "")
    job["peak_rss_gb"] = round(max(float(job.get("peak_rss_gb") or 0.0), rss), 3)
    job["peak_effective_gb"] = round(max(float(job.get("peak_effective_gb") or 0.0), effective), 3)
    if available > 0:
        previous = float(job.get("min_system_available_gb") or 0.0)
        job["min_system_available_gb"] = round(available if previous <= 0 else min(previous, available), 3)
    baseline = float(job.get("start_effective_gb") or 0.0)
    job["peak_delta_gb"] = round(max(0.0, float(job.get("peak_effective_gb") or 0.0) - baseline), 3)
    # Track the active stage independently.  A pipeline-wide peak cannot tell
    # whether lookup or pivot caused the allocation, and used to show the same
    # 17GB lifetime RSS against both stages.  Stage peaks use the same
    # Grafana-compatible effective sample as the job peak.
    for stage in job.get("stages") or []:
        if stage.get("status") != "running":
            continue
        stage["current_rss_gb"] = round(rss, 3)
        stage["current_effective_gb"] = round(effective, 3)
        stage["peak_rss_gb"] = round(max(float(stage.get("peak_rss_gb") or 0.0), rss), 3)
        stage["peak_effective_gb"] = round(
            max(float(stage.get("peak_effective_gb") or 0.0), effective), 3
        )
        stage_baseline = float(stage.get("start_effective_gb") or 0.0)
        stage["peak_delta_gb"] = round(
            max(0.0, float(stage.get("peak_effective_gb") or 0.0) - stage_baseline), 3
        )


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
    try:
        from core import scan_gate
        task_id = scan_gate.current_task_id()
    except Exception:
        task_id = ""
    job = {
        "id": job_id,
        "kind": str(kind or "cache"),
        "label": str(label or kind or "cache job"),
        "product": str(product or ""),
        "task_id": str(task_id or ""),
        "status": "running",
        "started_ts": now,
        "updated_ts": now,
        "last_event": "",
        "started_at": _utc_iso(),
        "finished_at": "",
        "current_stage": "",
        "stages": [
            {"id": str(stage_id), "label": str(stage_label), "status": "queued", "started_at": "", "finished_at": "",
             "start_rss_gb": 0.0, "start_effective_gb": 0.0,
             "peak_rss_gb": 0.0, "peak_effective_gb": 0.0, "peak_delta_gb": 0.0}
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
                stage["start_rss_gb"] = round(float(sample.get("rss_gb") or 0.0), 3)
                stage["start_effective_gb"] = round(float(sample.get("effective_gb") or 0.0), 3)
                stage["peak_rss_gb"] = stage["start_rss_gb"]
                stage["peak_effective_gb"] = stage["start_effective_gb"]
                stage["peak_delta_gb"] = 0.0
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
        value = float(os.environ.get("FLOW_CACHE_JOB_STALE_SEC", "") or 300.0)
    except Exception:
        value = 300.0
    return max(60.0, min(6 * 3600.0, value))


def reap_stale_jobs() -> list[str]:
    """진행이 끊긴 작업을 '실패(응답 없음)'로 종료한다.

    스레드가 예외 없이 죽거나 외부 호출에 영원히 붙잡히면 작업이 running 으로
    남아 화면이 무한 로딩된다. 진행 신호(updated_ts)가 stale_after_sec 동안
    없으면 실패로 확정해 화면이 반드시 끝난 상태를 보게 한다."""
    limit = stale_after_sec()
    now = time.time()
    stale_jobs: list[tuple[str, str]] = []
    with _LOCK:
        for job_id, job in list(_ACTIVE_JOBS.items()):
            idle = now - float(job.get("updated_ts") or job.get("started_ts") or now)
            if idle >= limit:
                stale_jobs.append((job_id, str(job.get("task_id") or "")))
    for job_id, task_id in stale_jobs:
        cancel_state = ""
        if task_id:
            try:
                from core import scan_gate
                cancel_state = str(
                    (scan_gate.cancel(task_id, by="cache-job-watchdog") or {}).get("state") or ""
                )
            except Exception:
                logger.debug("stale cache job cancellation failed job=%s task=%s",
                             job_id, task_id, exc_info=True)
        idle_txt = f"{int(limit // 60)}분"
        finish_job(job_id, ok=False, detail={
            "error": "stale_no_progress",
            "idle_limit_sec": limit,
            "task_id": task_id,
            "cancel_state": cancel_state,
        })
        try:
            record("scan", f"[작업 실패] 진행 신호 없음 — {idle_txt} 이상 아무 진행이 없어 중단 처리했습니다"
                           f" (job {job_id}). 서버 로그(터미널)에서 원인을 확인하세요.", ok=False)
        except Exception:
            pass
    return [job_id for job_id, _task_id in stale_jobs]


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
            "effective_kind": snap.get("process_memory_effective_kind", "rss_fallback"),
            "limit_gb": snap.get("process_memory_limit_gb", 0.0),
            "system_total_gb": sys_snap.get("system_memory_total_gb", 0.0),
            "system_available_gb": sys_snap.get("system_memory_available_gb", 0.0),
        }
    except Exception:
        pass

    # 화면 진입 직후에도 현재값이 최근 2일 집계에 포함되게 한다. 이후에는 메모리
    # 워치독이 주기적으로 기록하므로 화면을 열어 두지 않아도 peak가 추적된다.
    try:
        # 이미 떠 있는 스냅샷을 그대로 넘긴다 — 여기서 값을 생략하면 함수가
        # process_memory_snapshot 을 한 번 더 뜬다(화면 폴링마다 중복 비용).
        record_ram_peak_sample(float(current.get("rss_gb") or 0.0),
                               effective_gb=float(current.get("effective_gb") or 0.0),
                               effective_kind=str(current.get("effective_kind") or ""))
        recent_48h = recent_peak_rss(
            48.0, effective_kind=str(current.get("effective_kind") or "")
        )
    except Exception:
        recent_48h = {"peak_rss_gb": 0.0, "peak_ts": 0.0, "sample_count": 0,
                      "peak_effective_gb": 0.0, "peak_effective_ts": 0.0,
                      "effective_sample_count": 0}

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
        "recent_48h_peak_rss_gb": recent_48h["peak_rss_gb"],
        "recent_48h_peak_ts": recent_48h["peak_ts"],
        "recent_48h_sample_count": recent_48h["sample_count"],
        # 실제 메모리 압박 판단값. Linux에서는 Anonymous를 우선해 mmap parquet
        # 페이지를 제외하고, 불가능할 때 PSS/USS/RSS 순으로 폴백한다.
        "recent_48h_peak_effective_gb": recent_48h.get("peak_effective_gb") or 0.0,
        "recent_48h_peak_effective_ts": recent_48h.get("peak_effective_ts") or 0.0,
        "recent_48h_effective_sample_count": recent_48h.get("effective_sample_count") or 0,
        # PSS(smaps_rollup)를 못 읽는 플랫폼에서는 effective 가 RSS 로 폴백되므로
        # 두 값이 같아진다. 화면이 "비교 가능" 이라고 단정하지 않게 알려 준다.
        "effective_is_pss": current.get("effective_kind") == "pss",
        **current,
        "watchdog": watchdog_thresholds,
    }
