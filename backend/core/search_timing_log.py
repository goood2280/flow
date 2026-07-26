"""core/search_timing_log.py — SplitTable 검색 타이밍 영구 로그.

관리자 화면의 "SplitTable 검색 타이밍" 표는 원래 프로세스 메모리 링버퍼 50건이
전부였다. 재시작하면 사라지고 바쁜 시간대엔 몇 분 만에 밀려서, "동시 검색이
실제로 얼마나 줄 서는가(wait_ms)"를 며칠 단위로 관찰할 수가 없었다.

그래서 캐시 이벤트 로그(core/cache_event_log.py)와 같은 방식으로 공유 JSONL 에
append 한다 — 운영(api)/개발(worker) 서버가 같은 data_root 를 쓰므로 한 화면에서
두 서버 기록을 함께 볼 수 있고 origin 필드로 구분한다. 파일은 append-only
(다중 서버 동시 기록 안전)이며 주기적으로 최근 N 줄만 남긴다.

읽는 법: wall_ms = wait_ms + compute_ms.
  wait_ms 가 크면    → 동시 요청이 레인 슬롯을 넘겨 줄 서는 중(동시성 문제)
  compute_ms 가 크면 → 계산/캐시 미스 문제(레인을 늘려도 나아지지 않는다)
"""
from __future__ import annotations

import json
import socket
import threading
import time
from collections import deque
from typing import Any

# 인메모리 링버퍼 — 화면 기본 조회(최근)는 파일을 읽지 않고 여기서 바로 준다.
_MEM_MAX = 200
_MEM: deque[dict[str, Any]] = deque(maxlen=_MEM_MAX)
_MEM_LOCK = threading.Lock()

# 공유 파일 — 며칠 치 관찰용. 검색 1건당 1줄(≈300B)이라 5만 줄이면 ~15MB.
_FILE_LOCK = threading.Lock()
_SHARED_MAX_LINES = 50000
_SHARED_TRIM_EVERY = 500
_append_count = 0
_ORIGIN_CACHE: dict[str, Any] = {"ts": 0.0, "label": "", "host": ""}


def _shared_log_path():
    try:
        from core.paths import PATHS
        p = PATHS.data_root / "logs" / "search_timings.jsonl"
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
    elif role == "api" or prod is True:
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
    try:
        line = json.dumps(entry, ensure_ascii=False, default=str)
    except Exception:
        return
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
        import os
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) <= _SHARED_MAX_LINES:
            return
        tmp = str(path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(lines[-_SHARED_MAX_LINES:])
        os.replace(tmp, path)
    except Exception:
        pass


def record(entry: dict[str, Any]) -> None:
    """검색 1건 기록. entry 는 splittable 이 만든 타이밍 dict."""
    label, host = _origin()
    row = dict(entry)
    row.setdefault("ts", time.time())
    row["origin"] = label
    row["host"] = host
    with _MEM_LOCK:
        _MEM.append(row)
    _append_shared(row)


def recent(limit: int = 50) -> list[dict[str, Any]]:
    """최근 N건 (메모리만, 최신순) — 기존 화면 기본 조회 경로."""
    with _MEM_LOCK:
        items = list(_MEM)
    items.reverse()
    return items[:max(1, int(limit or 50))]


def _read_shared(since_ts: float) -> list[dict[str, Any]]:
    path = _shared_log_path()
    if path is None or not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except Exception:
                    continue
                if float(row.get("ts") or 0.0) >= since_ts:
                    out.append(row)
    except Exception:
        return []
    return out


def _pct(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return round(sorted_vals[idx], 1)


def _stats(vals: list[float]) -> dict:
    s = sorted(vals)
    return {"p50": _pct(s, 0.5), "p90": _pct(s, 0.9), "max": round(max(s), 1) if s else 0.0}


def _wait_of(row: dict) -> float:
    v = row.get("wait_ms")
    if v is None:
        v = float(row.get("lane_wait_ms") or 0.0) + float(row.get("cold_lane_wait_ms") or 0.0)
    return float(v or 0.0)


def _compute_of(row: dict) -> float:
    v = row.get("compute_ms")
    if v is None:
        v = float(row.get("total_ms") or 0.0) - float(row.get("cold_lane_wait_ms") or 0.0)
    return max(0.0, float(v or 0.0))


def query(hours: float = 24.0, limit: int = 200) -> dict:
    """기간 조회 + 집계. 파일과 메모리를 ts 기준으로 병합한다."""
    hours = max(0.1, min(24.0 * 90, float(hours or 24.0)))
    since = time.time() - hours * 3600.0
    rows = _read_shared(since)
    seen = {(r.get("ts"), r.get("root_lot_id"), r.get("product")) for r in rows}
    with _MEM_LOCK:
        mem = list(_MEM)
    for r in mem:
        if float(r.get("ts") or 0.0) >= since and (r.get("ts"), r.get("root_lot_id"), r.get("product")) not in seen:
            rows.append(r)
    rows.sort(key=lambda r: float(r.get("ts") or 0.0), reverse=True)

    waits = [_wait_of(r) for r in rows]
    comps = [_compute_of(r) for r in rows]
    walls = [float(r.get("wall_ms") or (w + c)) for r, w, c in zip(rows, waits, comps)]
    by_source: dict[str, int] = {}
    for r in rows:
        key = str(r.get("data_source") or "")
        by_source[key] = by_source.get(key, 0) + 1
    slow_wait = len([w for w in waits if w >= 200.0])
    return {
        "hours": hours,
        "count": len(rows),
        "persisted": _shared_log_path() is not None,
        "summary": {
            "wait_ms": _stats(waits),
            "compute_ms": _stats(comps),
            "wall_ms": _stats(walls),
            # 줄서기가 눈에 띄는(≥200ms) 검색의 비율 — 레인 크기를 올릴지 판단하는 핵심 지표.
            "slow_wait_count": slow_wait,
            "slow_wait_pct": round(100.0 * slow_wait / len(rows), 1) if rows else 0.0,
            "by_source": by_source,
        },
        "rows": rows[:max(1, min(2000, int(limit or 200)))],
    }
