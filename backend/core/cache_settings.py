"""core/cache_settings.py — 캐시 예산 런타임 조절값(관리자 톱니바퀴).

캐시관리 페이지 톱니바퀴에서 저장하는 예산 오버라이드를 보관한다. 우선순위는
항상 **env > 이 설정 파일 > 적응형 기본값** 이다 — 운영자가 env 로 고정한 값은
UI 설정보다 우선한다.

저장 위치: {data_root}/splittable/cache_budget_settings.json (운영/개발 서버가
data_root 를 공유하므로 두 서버에 동일 적용된다).

키:
  pool_fraction        전체 캐시 풀 = 호스트총량 × 이 비율 (0.1~0.8)
  dev_factor           개발서버(비운영) 캐시 풀 축소 계수 (0.05~1.0)
  root_ram_gb          root RAM 캐시 고정 GB (>0 이면 고정, 0/미설정=적응형)
  product_ram_enabled  제품 원본 RAM 캐시 사용 여부 (bool)
  product_ram_gb       제품 원본 RAM 캐시 고정 GB (>0 이면 고정)
  view_mb              view payload 캐시 고정 MB (>0 이면 고정)
"""
from __future__ import annotations

import json
import threading
import time

_LOCK = threading.Lock()
_CACHE: dict = {"ts": 0.0, "data": {}}
_TTL = 2.0


def _path():
    from core.paths import PATHS
    return PATHS.data_root / "splittable" / "cache_budget_settings.json"


def read() -> dict:
    now = time.monotonic()
    if _CACHE["data"] and now - float(_CACHE["ts"] or 0.0) < _TTL:
        return dict(_CACHE["data"])
    data: dict = {}
    try:
        p = _path()
        if p.exists():
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
    except Exception:
        data = {}
    _CACHE.update(ts=now, data=data)
    return dict(data)


def save(partial: dict) -> dict:
    """부분 갱신. 값이 None 이면 해당 키 삭제(기본값으로 복귀)."""
    with _LOCK:
        cur: dict = {}
        try:
            p = _path()
            if p.exists():
                raw = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    cur = raw
        except Exception:
            cur = {}
        for k, v in (partial or {}).items():
            if v is None:
                cur.pop(k, None)
            else:
                cur[k] = v
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(p) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=2)
        import os
        os.replace(tmp, p)
    _CACHE.update(ts=0.0, data={})  # 즉시 무효화
    return cur


def get_float(key: str, default=None):
    v = read().get(key)
    if v is None or v == "":
        return default
    try:
        return float(v)
    except Exception:
        return default


def get_bool(key: str, default: bool) -> bool:
    v = read().get(key)
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def _role_value(key: str, is_dev: bool):
    """운영/개발 분리 값. 개발서버(is_dev)면 <key>_dev 우선, 없으면 <key>.
    둘 다 없으면 None. (max_roots/max_roots_dev 와 동일한 폴백 규칙)"""
    data = read()
    if is_dev:
        dv = data.get(key + "_dev")
        if dv is not None and dv != "":
            return dv
    v = data.get(key)
    if v is not None and v != "":
        return v
    return None


def get_float_role(key: str, is_dev: bool, default=None):
    v = _role_value(key, is_dev)
    if v is None:
        return default
    try:
        return float(v)
    except Exception:
        return default


def get_bool_role(key: str, is_dev: bool, default: bool) -> bool:
    v = _role_value(key, is_dev)
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"1", "true", "yes", "on"}
