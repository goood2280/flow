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
    """사용자/관리자의 HTTP SplitTable 검색 1건만 기록한다.

    캐시 재검증·Inform embed·Flow-i 등 내부 호출은 같은 view 코어를 사용하지만
    검색 속도 통계에는 포함하지 않는다. 호출자가 명시적인 actor 표식을 주지
    않으면 보수적으로 버려 내부 작업이 다시 섞이는 일을 막는다.
    """
    if str((entry or {}).get("actor_type") or "") != "user_search":
        return
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


# 단계별 breakdown 키 — splittable._PHASE_KEYS 와 같은 목록이지만, 로그 모듈이
# 라우터를 import 하면 순환이 되므로 여기 복사해 둔다. 오래된 줄에는 없는 키가
# 많으므로 집계는 항상 0 기본값으로 읽는다.
_PHASE_KEYS: tuple[str, ...] = (
    "prelude_ms", "fastpath_ms", "scan_ms", "schema_ms", "fabscope_ms",
    "select_ms", "collect_ms", "emptyfallback_ms", "header_ms", "overlay_ms",
    "matrix_ms", "mismatch_ms", "progress_ms", "payload_ms", "cacheput_ms",
    "finish_ms", "serialize_ms",
)

# 화면에 그대로 쓰는 한국어 라벨.
PHASE_LABELS: dict[str, str] = {
    "prelude_ms": "진입·캐시판정",
    "fastpath_ms": "pivot 탐색",
    "scan_ms": "소스 스캔",
    "schema_ms": "스키마 해석",
    "fabscope_ms": "FAB 스코프",
    "select_ms": "컬럼 선택",
    "collect_ms": "polars 실행",
    "emptyfallback_ms": "빈결과 재해석",
    "header_ms": "웨이퍼 헤더",
    "overlay_ms": "plan·태그 로드",
    "matrix_ms": "셀 조립",
    "mismatch_ms": "불일치 알림",
    "progress_ms": "진행 step",
    "payload_ms": "응답 조립",
    "cacheput_ms": "캐시 저장",
    "finish_ms": "마무리 메타",
    "serialize_ms": "직렬화",
    "cold_lane_wait_ms": "레인 대기",
    "singleflight_wait_ms": "중복검색 대기",
    "unaccounted_ms": "미계측",
}

# 합계에 넣는 키 = 단계 + 핸들러 안에서의 대기 + 미설명. root_scan_ms 는 scan_ms
# 안쪽이라 제외한다(이중 계산).
_BREAKDOWN_KEYS: tuple[str, ...] = _PHASE_KEYS + (
    "cold_lane_wait_ms", "singleflight_wait_ms", "unaccounted_ms",
)


def phase_breakdown(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """단계별 합계·비중을 큰 순서로. 어느 구간을 손봐야 하는지 바로 읽히게 한다.

    breakdown 이 하나도 없는 오래된 기록만 있으면 빈 목록을 돌려준다 — 화면은
    그걸 보고 "이 기간에는 단계 기록이 없다"고 말할 수 있다.
    """
    totals: dict[str, float] = {k: 0.0 for k in _BREAKDOWN_KEYS}
    seen = 0
    for row in rows:
        if not any(k in row for k in _PHASE_KEYS):
            continue  # 계측 도입 이전 기록
        seen += 1
        for key in _BREAKDOWN_KEYS:
            totals[key] += float(row.get(key) or 0.0)
    if not seen:
        return []
    grand = sum(totals.values()) or 1.0
    out = [
        {
            "key": key,
            "label": PHASE_LABELS.get(key, key),
            "total_ms": round(value, 1),
            "avg_ms": round(value / seen, 2),
            "pct": round(100.0 * value / grand, 1),
        }
        for key, value in totals.items()
    ]
    out.sort(key=lambda e: -e["total_ms"])
    return out


def top_phase(row: dict[str, Any]) -> tuple[str, float]:
    """이 검색에서 가장 오래 걸린 단계 (라벨, ms). 기록이 없으면 ("", 0.0)."""
    best_key, best_val = "", 0.0
    for key in _BREAKDOWN_KEYS:
        val = float(row.get(key) or 0.0)
        if val > best_val:
            best_key, best_val = key, val
    if not best_key:
        return "", 0.0
    return PHASE_LABELS.get(best_key, best_key), round(best_val, 1)


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


# 캐시에서 응답한 것으로 분류하는 data_source 값 — 이 밖(disk/ram_load/raw/미상)은
# 미스(cold, 원본을 읽은 검색)로 본다. 관리자 화면의 색 구분과 같은 기준이다.
_HIT_SOURCES = {"payload_cache", "pivot_cache", "product_ram", "ram", "root_cache"}

# 그중 **이 서버 프로세스의 root lot RAM 캐시**에서 나온 것. 나머지 히트
# (payload/pivot/product_ram)는 응답 캐시나 공유 디스크 pivot 캐시라서, 랏 RAM
# 캐시가 텅 비어 있어도 히트율은 100% 가 될 수 있다 — 캐시 관리 화면의 "제품별
# 현황은 0인데 히트율은 100%" 괴리가 정확히 이 차이다.
_RAM_HIT_SOURCES = {"ram", "root_cache"}


def _is_hit(row: dict) -> bool:
    ds = str(row.get("data_source") or "")
    if ds:
        return ds in _HIT_SOURCES
    # 아주 오래된 기록엔 data_source 가 없다 — 캐시 히트 플래그로 폴백.
    return bool(row.get("payload_cache_hit") or row.get("root_cache_hit"))


def _is_ram_hit(row: dict) -> bool:
    ds = str(row.get("data_source") or "")
    if ds:
        return ds in _RAM_HIT_SOURCES
    return bool(row.get("root_cache_hit"))


def this_origin() -> str:
    """이 서버의 origin 라벨."""
    return _origin()[0]


def _wall_of(row: dict) -> float:
    v = row.get("wall_ms")
    if v is None:
        v = _wait_of(row) + _compute_of(row)
    return float(v or 0.0)


def query(hours: float = 24.0, limit: int = 200, origin: str = "") -> dict:
    """기간 조회 + 집계. 파일과 메모리를 ts 기준으로 병합한다.

    집계는 "랏 히트 시 / 아닐 시 속도"를 나눠 준다 — 캐시 관리 페이지가 히트율과
    히트/미스 각각의 체감 속도를 보고 코어·슬롯·예산을 조절하는 근거다. origin
    (운영/개발)별 분해와, 미스가 반복된 root 목록(주요 Lot 등록 후보)도 함께 낸다.

    origin 을 주면 그 서버 기록만 집계한다. **공유 JSONL 이라 기본(빈 값)은
    운영+개발 합산**이라는 점이 중요하다 — 개발서버에서 화면을 열어도 히트율은
    운영 기록까지 섞인 값이었고, "제품별 현황은 0인데 히트율 100%" 오해의 한
    축이었다."""
    hours = max(0.1, min(24.0 * 90, float(hours or 24.0)))
    since = time.time() - hours * 3600.0
    rows = _read_shared(since)
    # 구버전 행에는 호출 주체가 없어 사용자 검색과 캐시 작업을 구분할 수 없다.
    # 정확한 체감 속도를 위해 명시적으로 사용자 HTTP 검색인 새 행만 집계한다.
    rows = [r for r in rows if str(r.get("actor_type") or "") == "user_search"]
    seen = {(r.get("ts"), r.get("root_lot_id"), r.get("product")) for r in rows}
    with _MEM_LOCK:
        mem = list(_MEM)
    for r in mem:
        if (str(r.get("actor_type") or "") == "user_search"
                and float(r.get("ts") or 0.0) >= since
                and (r.get("ts"), r.get("root_lot_id"), r.get("product")) not in seen):
            rows.append(r)
    origin_filter = str(origin or "").strip()
    all_origins = sorted({str(r.get("origin") or "") for r in rows if r.get("origin")})
    if origin_filter:
        rows = [r for r in rows if str(r.get("origin") or "") == origin_filter]
    rows.sort(key=lambda r: float(r.get("ts") or 0.0), reverse=True)

    waits = [_wait_of(r) for r in rows]
    comps = [_compute_of(r) for r in rows]
    walls = [float(r.get("wall_ms") or (w + c)) for r, w, c in zip(rows, waits, comps)]
    by_source: dict[str, int] = {}
    for r in rows:
        key = str(r.get("data_source") or "")
        by_source[key] = by_source.get(key, 0) + 1
    slow_wait = len([w for w in waits if w >= 200.0])

    # ── 히트/미스 분리 + origin(운영/개발) 분해 ──
    hit_walls = [_wall_of(r) for r in rows if _is_hit(r)]
    miss_walls = [_wall_of(r) for r in rows if not _is_hit(r)]
    origin_acc: dict[str, dict[str, list[float]]] = {}
    for r in rows:
        label = str(r.get("origin") or "?")
        bucket = origin_acc.setdefault(label, {"hit": [], "miss": []})
        bucket["hit" if _is_hit(r) else "miss"].append(_wall_of(r))
    by_origin = []
    for label, bucket in sorted(origin_acc.items()):
        n_hit, n_miss = len(bucket["hit"]), len(bucket["miss"])
        total = n_hit + n_miss
        by_origin.append({
            "origin": label,
            "count": total,
            "hit_count": n_hit,
            "miss_count": n_miss,
            "hit_rate_pct": round(100.0 * n_hit / total, 1) if total else 0.0,
            "hit_wall_ms": _stats(bucket["hit"]),
            "miss_wall_ms": _stats(bucket["miss"]),
        })

    # ── 미스 반복 root — "자주 검색되는데 캐시에 없어 매번 느린" 등록 후보 ──
    miss_acc: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        if _is_hit(r):
            continue
        root = str(r.get("root_lot_id") or "").strip().upper()
        if not root:
            continue
        key = (str(r.get("product") or ""), root)
        entry = miss_acc.setdefault(key, {"count": 0, "walls": [], "last_at": ""})
        entry["count"] += 1
        entry["walls"].append(_wall_of(r))
        at = str(r.get("at") or "")
        if at > entry["last_at"]:
            entry["last_at"] = at
    miss_roots = [
        {
            "product": product,
            "root_lot_id": root,
            "count": entry["count"],
            "wall_ms_p50": _pct(sorted(entry["walls"]), 0.5),
            "last_at": entry["last_at"],
        }
        for (product, root), entry in miss_acc.items()
    ]
    # 반복(2회 이상)을 먼저, 그 안에서 횟수·느린 순 — 상위 20개만.
    miss_roots.sort(key=lambda e: (-e["count"], -e["wall_ms_p50"]))
    miss_roots = miss_roots[:20]

    n = len(rows)
    # 랏 RAM 캐시에서 나온 히트만 따로 — 나머지 히트(응답/pivot/제품 RAM)는 랏
    # RAM 캐시가 비어 있어도 성립한다.
    ram_hits = len([1 for r in rows if _is_ram_hit(r)])
    return {
        "hours": hours,
        "count": n,
        "persisted": _shared_log_path() is not None,
        "origin": origin_filter,
        "this_origin": this_origin(),
        "origins": all_origins,
        "summary": {
            "ram_hit_count": ram_hits,
            "ram_hit_rate_pct": round(100.0 * ram_hits / n, 1) if rows else 0.0,
            "wait_ms": _stats(waits),
            "compute_ms": _stats(comps),
            "wall_ms": _stats(walls),
            # 줄서기가 눈에 띄는(≥200ms) 검색의 비율 — 레인 크기를 올릴지 판단하는 핵심 지표.
            "slow_wait_count": slow_wait,
            "slow_wait_pct": round(100.0 * slow_wait / n, 1) if rows else 0.0,
            "by_source": by_source,
            # 히트/미스 — 캐시가 실제로 속도를 벌어주는지, 히트율은 충분한지.
            "hit_count": len(hit_walls),
            "miss_count": len(miss_walls),
            "hit_rate_pct": round(100.0 * len(hit_walls) / n, 1) if rows else 0.0,
            "hit_wall_ms": _stats(hit_walls),
            "miss_wall_ms": _stats(miss_walls),
            "by_origin": by_origin,
            # 단계별 시간 배분 — "느리다" 를 "어디가 느리다" 로 바꾸는 표.
            # 히트/미스를 나눠 준다: 히트가 느리면 캐시 밖(진입·마무리·직렬화),
            # 미스가 느리면 읽기(스캔·collect) 문제다.
            "phase_breakdown": phase_breakdown(rows),
            "phase_breakdown_miss": phase_breakdown([r for r in rows if not _is_hit(r)]),
            "phase_breakdown_hit": phase_breakdown([r for r in rows if _is_hit(r)]),
        },
        "miss_roots": miss_roots,
        "rows": [_with_top_phase(r) for r in rows[:max(1, min(2000, int(limit or 200)))]],
    }


def _with_top_phase(row: dict[str, Any]) -> dict[str, Any]:
    """행에 top_phase(가장 오래 걸린 단계)를 붙인다 — 표에서 한 눈에 원인 보기."""
    label, value = top_phase(row)
    if not label:
        return row
    out = dict(row)
    out["top_phase"] = label
    out["top_phase_ms"] = value
    return out
