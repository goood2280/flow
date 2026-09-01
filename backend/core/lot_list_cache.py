"""core/lot_list_cache.py — SplitTable/Inform 제품별 lot 후보 풀 캐시.

제품을 고르면 SplitTable 은 그 제품의 root_lot_id와 LOT ID 전체 목록을 한 번에
받아 드롭다운/자동완성을 로컬에서 필터하고, 신규 Inform도 같은 LOT ID 풀을 쓴다.
그 한 번이 느렸다.

기존 계층(`splittable._LOT_LOOKUP_CACHE`)은 **TTL 60초 + 256개 FIFO** 라서
- 1분마다 첫 사용자가 split_table 캐시 디렉터리 glob + FAB latest 캐시
  unique 스캔을 통째로 다시 치렀고,
- prefix 마다 키가 갈려(인폼/트래커의 키 입력 검색) 정작 비싼 "빈 prefix 전체
  풀" 항목이 FIFO 에서 먼저 밀려났으며,
- 프로세스 재시작이면 전부 콜드였다.

이 모듈은 그 위에 얹는 **소스 시그니처 기반** 계층이다. TTL 로 만료시키지
않고 소스(ML_TABLE 파일 / lookup meta / FAB latest 캐시 / match 캐시 /
split_table 캐시 디렉터리)가 실제로 바뀔 때까지 유효하다. 시그니처 계산과
목록 조립은 호출자(splittable router)가 하고, 여기서는 보관만 한다 —
순환 import 를 피하고 캐시 정책을 한 곳에 모으기 위해서다.

계층:
  RAM (LRU, 바이트 예산)  →  디스크 JSON  →  (miss) 호출자가 빌드

디스크 사본은 `{data_root}/splittable/lot_list_cache/` 에 제품·kind 당 한
파일이다. 재시작·워커 교체 후에도 첫 조회가 즉답이고, 운영/개발 두 서버가
data_root 를 공유하므로 한쪽이 만든 목록을 다른 쪽도 쓴다. 읽기전용 root 등
쓰기가 막힌 환경에서는 조용히 RAM 전용으로 동작한다.

예산: env `FLOW_LOT_LIST_CACHE_MB` > 톱니바퀴 설정 `lot_list_mb`(운영/개발
분리) > 기본 32MB. root 문자열 5만 개가 대략 3~4MB 이므로 기본값으로 큰 제품
10개 안팎을 상주시킨다.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

logger = logging.getLogger("flow.lot_list_cache")

# 디스크 포맷 버전 — 스키마가 바뀌면 올린다(옛 파일은 miss 로 떨어진다).
FORMAT_VERSION = 1

DEFAULT_BUDGET_MB = 32.0
_BUDGET_MB_MIN = 1.0
_BUDGET_MB_MAX = 512.0

# CPython 짧은 ASCII str 1개 ≈ 49B + 리스트 슬롯 포인터 8B. 정확할 필요는 없고
# 예산이 실제 사용량을 과소평가하지만 않으면 된다.
_STR_OVERHEAD = 57
_ENTRY_OVERHEAD = 512

_LOCK = threading.RLock()
_RAM: "OrderedDict[str, dict]" = OrderedDict()
_RAM_BYTES = 0
_STATS = {"hit_ram": 0, "hit_disk": 0, "miss": 0, "put": 0, "evicted": 0}
# 읽기전용/공유 root 에서 쓰기가 막히면 매 호출 예외 로그를 남기지 않는다.
_DISK_WRITE_DISABLED = False


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "")
    if str(raw).strip() == "":
        return default
    try:
        return float(raw)
    except Exception:
        return default


def budget_bytes() -> int:
    """RAM 예산(bytes). env > 톱니바퀴 설정 > 기본값."""
    mb = _env_float("FLOW_LOT_LIST_CACHE_MB", 0.0)
    if mb <= 0:
        try:
            from core import cache_settings
            from core import ml_table_lookup

            is_dev = bool(ml_table_lookup._root_ram_cache_use_dev())
            mb = float(cache_settings.get_float_role("lot_list_mb", is_dev, DEFAULT_BUDGET_MB) or 0.0)
        except Exception:
            mb = 0.0
    if mb <= 0:
        mb = DEFAULT_BUDGET_MB
    mb = max(_BUDGET_MB_MIN, min(_BUDGET_MB_MAX, mb))
    return int(mb * 1024 * 1024)


def _cache_dir() -> Path:
    from core.paths import PATHS

    return PATHS.data_root / "splittable" / "lot_list_cache"


def _safe_token(raw: str) -> str:
    text = str(raw or "").strip().upper()
    out = "".join(ch if (ch.isalnum() or ch in "_-.") else "_" for ch in text)
    return out[:96] or "_"


def cache_key(product: str, kind: str = "root") -> str:
    return f"{_safe_token(product)}__{_safe_token(kind)}"


def _disk_path(key: str) -> Path:
    return _cache_dir() / f"{key}.json"


def _entry_bytes(values: list) -> int:
    total = _ENTRY_OVERHEAD
    for value in values:
        total += _STR_OVERHEAD + len(str(value))
    return total


def _evict_locked(budget: int) -> None:
    global _RAM_BYTES
    while _RAM and _RAM_BYTES > budget:
        _, dropped = _RAM.popitem(last=False)
        _RAM_BYTES -= int(dropped.get("bytes") or 0)
        _STATS["evicted"] += 1
    if _RAM_BYTES < 0:
        _RAM_BYTES = 0


def _read_disk(key: str, sig: str) -> dict | None:
    try:
        fp = _disk_path(key)
        if not fp.is_file():
            return None
        raw = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    if int(raw.get("format_version") or 0) != FORMAT_VERSION:
        return None
    if str(raw.get("sig") or "") != str(sig or ""):
        return None
    values = raw.get("values")
    complete = bool(raw.get("complete"))
    # 완성된 0건과 아직 준비 중인 0건은 다르다. 전자는 정상 캐시 hit이고,
    # 후자만 miss 로 돌려 빌드/재폴링을 이어간다.
    if not isinstance(values, list) or (not values and not complete):
        return None
    meta = raw.get("meta")
    return {
        "key": key,
        "product": str(raw.get("product") or ""),
        "kind": str(raw.get("kind") or ""),
        "sig": str(raw.get("sig") or ""),
        "values": [str(v) for v in values],
        "meta": dict(meta) if isinstance(meta, dict) else {},
        "complete": complete,
        "built_at": str(raw.get("built_at") or ""),
        "built_epoch": float(raw.get("built_epoch") or 0.0),
    }


def _write_disk(entry: dict) -> None:
    global _DISK_WRITE_DISABLED
    if _DISK_WRITE_DISABLED:
        return
    fp = _disk_path(entry["key"])
    payload = {
        "format_version": FORMAT_VERSION,
        "product": entry.get("product") or "",
        "kind": entry.get("kind") or "",
        "sig": entry.get("sig") or "",
        "complete": bool(entry.get("complete")),
        "built_at": entry.get("built_at") or "",
        "built_epoch": float(entry.get("built_epoch") or 0.0),
        "value_count": len(entry.get("values") or []),
        "meta": entry.get("meta") or {},
        "values": entry.get("values") or [],
    }
    tmp = fp.with_suffix(fp.suffix + f".tmp{os.getpid()}")
    try:
        fp.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, fp)
    except Exception as exc:
        # 공유/읽기전용 root 는 정상 시나리오다 — RAM 계층만으로 계속 동작한다.
        _DISK_WRITE_DISABLED = True
        logger.info("lot_list_cache 디스크 사본 비활성화 (%s: %s)", type(exc).__name__, exc)
        try:
            Path(tmp).unlink(missing_ok=True)
        except Exception:
            pass


def _remember_locked(entry: dict) -> None:
    global _RAM_BYTES
    key = entry["key"]
    old = _RAM.pop(key, None)
    if old is not None:
        _RAM_BYTES -= int(old.get("bytes") or 0)
    entry["bytes"] = _entry_bytes(entry.get("values") or [])
    _RAM[key] = entry
    _RAM_BYTES += int(entry["bytes"])
    _evict_locked(budget_bytes())


def _public(entry: dict, cached_from: str) -> dict:
    return {
        "values": list(entry.get("values") or []),
        "meta": dict(entry.get("meta") or {}),
        "complete": bool(entry.get("complete")),
        "built_at": entry.get("built_at") or "",
        "built_epoch": float(entry.get("built_epoch") or 0.0),
        "cached": cached_from,
        "value_count": len(entry.get("values") or []),
    }


def get(product: str, sig: str, kind: str = "root") -> dict | None:
    """시그니처가 일치하는 캐시된 목록. RAM → 디스크 순. 없으면 None."""
    global _RAM_BYTES
    key = cache_key(product, kind)
    with _LOCK:
        entry = _RAM.get(key)
        if entry is not None:
            if str(entry.get("sig") or "") == str(sig or ""):
                _RAM.move_to_end(key)
                _STATS["hit_ram"] += 1
                return _public(entry, "ram")
            # 소스가 바뀌었다 — 낡은 항목은 자리만 차지한다.
            _RAM.pop(key, None)
            _RAM_BYTES -= int(entry.get("bytes") or 0)
    disk = _read_disk(key, sig)
    if disk is None:
        with _LOCK:
            _STATS["miss"] += 1
        return None
    with _LOCK:
        _remember_locked(disk)
        _STATS["hit_disk"] += 1
    return _public(disk, "disk")


def put(product: str, sig: str, values: list, *, kind: str = "root",
        meta: dict | None = None, complete: bool = True) -> dict:
    """빌드 결과를 보관한다. **미완성 빈 목록만 캐시하지 않는다.**

    ``complete=False``인 빈 결과는 대개 lookup 빌드 중이므로 굳히지 않는다.
    반대로 ``complete=True``인 빈 결과는 "이 제품에 lot이 없음"이라는 정상적인
    최종 상태다. 이를 버리면 모든 요청이 같은 pool을 다시 만들고 UI도 영원히
    준비 중으로 남는다.
    """
    clean = [str(v) for v in (values or []) if str(v or "").strip()]
    entry = {
        "key": cache_key(product, kind),
        "product": str(product or ""),
        "kind": str(kind or "root"),
        "sig": str(sig or ""),
        "values": clean,
        "meta": dict(meta or {}),
        "complete": bool(complete),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "built_epoch": time.time(),
    }
    if not clean and not complete:
        return _public(entry, "")
    with _LOCK:
        _remember_locked(entry)
        _STATS["put"] += 1
    _write_disk(entry)
    return _public(entry, "")


def invalidate(product: str = "", kind: str = "") -> int:
    """캐시 항목을 버린다.

    소스가 바뀌면 시그니처가 달라져 어차피 miss 지만, 캐시 재빌드 직후처럼
    "지금 바로 비워졌음"을 보장해야 하는 지점에서 명시적으로 부른다.

    제품을 지정하면 디스크 사본까지 지운다(재빌드 직후 = 그 목록은 확실히
    낡았다). 제품 없이 부르면 RAM 만 비운다 — 광범위한 무효화 신호는 대개
    "혹시 몰라서" 이므로 다른 서버가 만든 디스크 사본까지 날릴 이유가 없고,
    시그니처가 여전히 정확성을 보장한다.
    """
    global _RAM_BYTES
    dropped = 0
    with _LOCK:
        if not product:
            dropped = len(_RAM)
            _RAM.clear()
            _RAM_BYTES = 0
            return dropped
        prefix = f"{_safe_token(product)}__"
        keys = [cache_key(product, kind)] if kind else [
            key for key in list(_RAM) if key.startswith(prefix)
        ]
        for key in keys:
            entry = _RAM.pop(key, None)
            if entry is not None:
                _RAM_BYTES -= int(entry.get("bytes") or 0)
                dropped += 1
        if _RAM_BYTES < 0:
            _RAM_BYTES = 0
    try:
        if kind:
            _disk_path(cache_key(product, kind)).unlink(missing_ok=True)
        else:
            for fp in _cache_dir().glob(f"{_safe_token(product)}__*.json"):
                fp.unlink(missing_ok=True)
    except Exception:
        pass
    return dropped


def emergency_evict(max_bytes: int) -> int:
    """메모리 워치독 긴급 축출 — RAM 사본만 오래된 순으로 최대 max_bytes 제거.

    디스크 사본은 남기므로 다음 조회는 여전히 즉답이다(디스크 hit). 반환:
    회수 추정 바이트."""
    global _RAM_BYTES
    if max_bytes <= 0:
        return 0
    freed = 0
    with _LOCK:
        while _RAM and freed < max_bytes:
            _key, dropped = _RAM.popitem(last=False)
            size = int(dropped.get("bytes") or 0)
            freed += size
            _RAM_BYTES = max(0, _RAM_BYTES - size)
            _STATS["evicted"] += 1
    return freed


def clear() -> None:
    """RAM + 디스크 사본 전부 제거 (테스트/관리자 초기화용)."""
    global _RAM_BYTES, _DISK_WRITE_DISABLED
    with _LOCK:
        _RAM.clear()
        _RAM_BYTES = 0
        for key in _STATS:
            _STATS[key] = 0
        _DISK_WRITE_DISABLED = False
    try:
        for fp in _cache_dir().glob("*.json"):
            fp.unlink(missing_ok=True)
    except Exception:
        pass


def stats() -> dict[str, Any]:
    """관리자 진단용 스냅샷."""
    with _LOCK:
        entries = [
            {
                "key": entry.get("key") or "",
                "product": entry.get("product") or "",
                "kind": entry.get("kind") or "",
                "value_count": len(entry.get("values") or []),
                "complete": bool(entry.get("complete")),
                "built_at": entry.get("built_at") or "",
                "estimated_kb": round(int(entry.get("bytes") or 0) / 1024, 1),
            }
            for entry in _RAM.values()
        ]
        used = int(_RAM_BYTES)
        counters = dict(_STATS)
    budget = budget_bytes()
    return {
        "ok": True,
        "entries": entries,
        "entry_count": len(entries),
        "used_mb": round(used / (1024 * 1024), 3),
        "budget_mb": round(budget / (1024 * 1024), 3),
        "disk_dir": str(_cache_dir()),
        "disk_writes_disabled": bool(_DISK_WRITE_DISABLED),
        **counters,
    }
