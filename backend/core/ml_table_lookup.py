"""ML_TABLE root_lot_id lookup cache.

The wide ML_TABLE parquet files are optimized for root-lot lookups by building
one hive-partitioned cache per source file. Query paths never scan the original
source when the cache is missing; they return readiness state and enqueue a
single background build.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import OrderedDict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import polars as pl

from core.paths import PATHS
from core.runtime_limits import (
    cpu_budget_cores,
    process_cpu_snapshot,
    process_memory_high,
    process_memory_limit_gb,
    process_memory_snapshot,
    system_memory_snapshot,
)

logger = logging.getLogger("flow.ml_table_lookup")

CACHE_VERSION = 1
MAX_RESULT_ROWS = 25
LOOKUP_CACHE_DIRNAME = "ml_table_lookup"
META_FILE = "_meta.json"
CANDIDATE_INDEX_FILE = "_candidate_index.json"
CANDIDATE_INDEX_VERSION = 1
CANDIDATE_COLUMN_PREFIXES = ("KNOB", "MASK", "INLINE", "VM", "TAG", "MGMT")
_META_CACHE_LOCK = threading.Lock()
_META_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_META_CACHE_TTL_SEC = 2.0
_META_CACHE_MAX = 512
BUILD_LOCK_STALE_SECONDS = 30 * 60
LOOKUP_CACHE_MEMORY_WAIT_SECONDS_DEFAULT = 5.0
LOOKUP_CACHE_PARTITION_MAX_ROWS_DEFAULT = 250_000
LOOKUP_CACHE_BUILD_CHUNK_SIZE_DEFAULT = 4
# 개발서버는 청크당 collect 메모리를 더 낮게 — 대용량 제품 빌드도 평탄하게(느려도 OK).
LOOKUP_CACHE_BUILD_CHUNK_SIZE_DEV_DEFAULT = 2
LOOKUP_CACHE_BUILD_RETRY_SECONDS_DEFAULT = 30.0
LOOKUP_CACHE_BUILD_RETRY_MAX_DEFAULT = 3
LATEST_LOT_BY_ROOT_WAFER_FILE = "lot_progress_latest_lot_by_root_wafer.parquet"
_STR = getattr(pl, "Utf8", None) or getattr(pl, "String", pl.Object)

IDENTITY_COLUMN_CANDIDATES = (
    "product",
    "root_lot_id",
    "lot_id",
    "fab_lot_id",
    "wafer_id",
    "wf_id",
    "step_id",
    "function_step",
    "func_step",
    "tkout_time",
    "tkin_time",
    "update_time",
    "time",
    "timestamp",
    "datetime",
    "date",
)

_BUILD_LOCK = threading.Lock()
_BUILD_QUEUE: deque[Path] = deque()
_BUILD_THREAD: threading.Thread | None = None
_BUILD_RETRY_TIMERS: dict[str, threading.Timer] = {}
_BUILD_RETRY_COUNTS: dict[str, int] = {}
_BUILD_STATE: dict[str, Any] = {
    "running": False,
    "paused": False,
    "pause_reason": "",
    "resource_snapshot": {},
    "queued": [],
    "current": "",
    "started_at": "",
    "finished_at": "",
    "last_error": "",
    "last_source": "",
}

ROOT_RAM_CACHE_VERSION = 1
ROOT_RAM_CACHE_MAX_GB_DEFAULT = 3.0
ROOT_RAM_CACHE_REFRESH_MINUTES_DEFAULT = 30
ROOT_RAM_CACHE_REFRESH_MINUTES_MIN = 5
ROOT_RAM_CACHE_REFRESH_MINUTES_MAX = 240
ROOT_RAM_CACHE_RECENT_ROOTS_DEFAULT = 100
ROOT_RAM_CACHE_FREQUENT_ROOTS_DEFAULT = 100
# 캐싱 대상 step 선정: 톱니바퀴 설정(step_ids)으로 지정한 step 을 지난(통과한=tkout)
# lot 을 latest cache 에서 tkout_time 최신순으로 채운다. 비면 step 필터 없이
# searched+recent 만 유지.
ROOT_RAM_CACHE_STEP_IDS_DEFAULT: tuple[str, ...] = ()
# step/latest(tkout_time 최신순) 후보 정렬 시 이 prefix 로 시작하는 root lot 을
# 먼저 채우고, 남는 자리를 나머지로 채운다. 빈 문자열이면 prefix 우선순위 없음.
ROOT_RAM_CACHE_PRIORITY_ROOT_PREFIX_DEFAULT = "AZ"
# searched roots 는 "한번이라도 검색된 root" 로 무조건 최우선 포함이므로 target 만큼
# 넉넉히 허용한다(실사용 검색 수는 훨씬 작다). target_roots 가 최종 상한 역할.
ROOT_RAM_CACHE_SEARCHED_ROOTS_DEFAULT = 1000
# 상시 메모리에 유지할 총 root(knob 수준) 개수 목표. searched 를 최우선으로 채우고
# 남는 자리를 latest cache 기준 지정 step 통과 lot(tkout_time 최신순)으로 채운다.
ROOT_RAM_CACHE_TARGET_ROOTS_DEFAULT = 150
ROOT_RAM_CACHE_ROOTS_MAX = 50000
# 0 = 크기 무제한(기본). 대용량 제품도 자동으로 lookup 캐시를 빌드한다 —
# 빌드는 root_lot_id 청크 단위(_sink_lookup_cache_partitions_chunked)로
# 메모리를 가드하며 진행하므로 파일 크기로 자동 빌드를 막지 않는다(막으면
# 대용량 제품이 영구 미캐시). 운영자가 상한을 원하면 env(MB)>0 로 지정.
ROOT_RAM_CACHE_BUILD_MAX_MB_DEFAULT = 0.0
ROOT_RAM_CACHE_CPU_CORES_DEFAULT = 2.0
ROOT_RAM_CACHE_RESOURCE_CHECK_SEC = 1.0
ROOT_RAM_CACHE_SETTINGS_FILE = PATHS.data_root / "splittable" / "source_config.json"
# 우선 lot 등록 파일 — RAM 캐시 예열 시 최우선으로 적재할 lot 목록.
_RAM_CACHE_PRIORITY_FILE = PATHS.data_root / "splittable" / "ram_cache_priority_lots.json"
_ROOT_RAM_CACHE_LOCK = threading.RLock()
_ROOT_RAM_CACHE: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()
_ROOT_RAM_ACCESS: dict[tuple[str, str], dict[str, Any]] = {}
_ROOT_RAM_STATUS: dict[str, Any] = {
    "last_refresh_at": "",
    "last_refresh_epoch": 0.0,
    "last_error": "",
    "products": [],
}
_ROOT_RAM_STOP = threading.Event()
_ROOT_RAM_THREAD: threading.Thread | None = None
_ROOT_RAM_STARTED = False
_ROOT_RAM_REFRESH_LOCK = threading.Lock()
# step/latest 후보 갱신 주기 — 매 사이클이 아닌 N번째 사이클에만 step/latest 후보를
# 다시 계산한다. priority/searched 는 매 사이클 즉시 반영.
_ROOT_RAM_STEP_LATEST_REFRESH_EVERY = 3  # 예: 30분 간격이면 90분마다 step/latest 갱신
_ROOT_RAM_REFRESH_COUNTER = 0
_ROOT_RAM_LAST_STEP_ROOTS: dict[str, list[str]] = {}   # file.name → 마지막 step_roots
_ROOT_RAM_LAST_LATEST_ROOTS: dict[str, list[str]] = {}  # file.name → 마지막 latest_roots
_ROOT_RAM_RESOURCE_STATE: dict[str, Any] = {
    "checked_epoch": 0.0,
    "reason": "",
    "snapshot": {},
}
_ROOT_RAM_PREFETCH_LOCK = threading.Lock()
_ROOT_RAM_PREFETCH_QUEUE: deque[tuple[Path, str]] = deque()
_ROOT_RAM_PREFETCH_PENDING: set[str] = set()
_ROOT_RAM_PREFETCH_WAKE = threading.Event()
_ROOT_RAM_PREFETCH_THREAD: threading.Thread | None = None
_ROOT_RAM_PREFETCH_STATE: dict[str, Any] = {
    "running": False,
    "current_product": "",
    "current_root": "",
    "last_error": "",
}


class MlTableLookupError(ValueError):
    """Machine-readable lookup validation failure."""

    def __init__(self, code: str, message: str, *, column: str = "", columns: list[str] | None = None):
        super().__init__(message)
        self.code = code
        self.column = column
        self.columns = columns or []

    def to_detail(self) -> dict[str, Any]:
        detail = {"code": self.code, "message": str(self)}
        if self.column:
            detail["column"] = self.column
        if self.columns:
            detail["columns"] = self.columns
        return detail


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_product_token(raw: str) -> str:
    token = "".join(ch if ch.isalnum() or ch in ("_", "-", ".") else "_" for ch in str(raw or "").strip())
    return token.strip("._") or "ML_TABLE"


def _source_sig(fp: Path) -> dict[str, Any]:
    st = fp.stat()
    return {
        "source_path": str(fp.resolve()),
        "source_mtime": st.st_mtime,
        "source_size": st.st_size,
    }


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(os.environ.get(name, "") or default)
    except Exception:
        value = default
    return max(lo, min(hi, value))


def _env_float(name: str, default: float, lo: float, hi: float) -> float:
    try:
        value = float(os.environ.get(name, "") or default)
    except Exception:
        value = default
    return max(lo, min(hi, value))


def _bounded_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(lo, min(hi, parsed))


def _root_ram_settings() -> dict[str, Any]:
    try:
        raw = json.loads(ROOT_RAM_CACHE_SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    settings = raw.get("root_lot_cache") or {}
    return settings if isinstance(settings, dict) else {}


def _root_ram_cache_use_dev() -> bool:
    """개발서버(운영보다 작은 lot 예산) 컨텍스트 여부.

    PATHS.is_prod 가 아니면 dev. 운영이라도 server_role 이 worker(개발서버
    오프로드)이면 dev 예산을 쓴다."""
    if not PATHS.is_prod:
        return True
    try:
        from core.worker_dispatch import server_role
        if server_role() == "worker":
            return True
    except Exception:
        pass
    return False


def _root_ram_cache_product_budget(product_token: str) -> int:
    """source_config.json 최상위 `ram_cache_product_budgets` 에서 제품별
    max_roots 를 읽는다. 키는 제품 캐시 관리 페이지가 저장한 제품명
    (예: "ML_TABLE_PRODA"). bare 이름("PRODA")도 허용. 미설정이면 0.

    운영/개발 서버 구분: 개발서버(_root_ram_cache_use_dev)이면 max_roots_dev 를
    사용한다. **개발서버에서 max_roots_dev 가 설정돼 있지 않으면 0 을 반환**해
    호출측이 개발 기본 target(_root_ram_cache_target_roots_dev)으로 폴백하게
    한다 — 예전엔 운영 max_roots 를 그대로 써서 개발서버가 운영과 같은 수의
    lot 을 적재(메모리 오버킬)하는 버그가 있었다."""
    try:
        raw = json.loads(ROOT_RAM_CACHE_SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return 0
    budgets = raw.get("ram_cache_product_budgets") if isinstance(raw, dict) else None
    if not isinstance(budgets, dict):
        return 0
    token = str(product_token or "").strip().upper()
    bare = token[len("ML_TABLE_"):] if token.startswith("ML_TABLE_") else token
    use_dev = _root_ram_cache_use_dev()
    for key, val in budgets.items():
        key_upper = str(key).strip().upper()
        key_bare = key_upper[len("ML_TABLE_"):] if key_upper.startswith("ML_TABLE_") else key_upper
        if key_upper == token or (bare and key_bare == bare):
            if isinstance(val, dict):
                if use_dev:
                    dev_val = val.get("max_roots_dev")
                    if dev_val is None:
                        # 개발서버 dev 예산 미설정 → 운영값을 쓰지 않고 기본 폴백(0).
                        return 0
                    max_roots = dev_val
                else:
                    max_roots = val.get("max_roots")
            else:
                # 스칼라 예산은 운영 전용으로 간주 — 개발서버는 기본 폴백(0).
                if use_dev:
                    return 0
                max_roots = val
            try:
                return max(0, min(ROOT_RAM_CACHE_ROOTS_MAX, int(max_roots)))
            except Exception:
                return 0
    return 0


def _normalize_str_list(raw: Any) -> list[str]:
    """콤마/개행 구분 문자열 또는 리스트를 upper-cased, 중복 제거 리스트로."""
    if isinstance(raw, str):
        parts = raw.replace("\n", ",").split(",")
    elif isinstance(raw, (list, tuple, set)):
        parts = list(raw)
    else:
        parts = []
    out: list[str] = []
    seen: set[str] = set()
    for item in parts:
        value = str(item or "").strip().upper()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _load_priority_root_lot_ids(product: str) -> list[str]:
    """우선 lot 등록 파일에서 product 에 해당하는 root_lot_id 목록을 반환한다."""
    try:
        raw = json.loads(_RAM_CACHE_PRIORITY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(raw, dict):
        return []
    # product key 매칭: 대소문자 무시
    product_upper = str(product or "").strip().upper()
    lots = None
    for key, val in raw.items():
        if str(key).strip().upper() == product_upper and isinstance(val, list):
            lots = val
            break
    if not lots:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for entry in lots:
        if not isinstance(entry, dict):
            continue
        # cache_enabled=False 인 항목은 목록에 유지하되 캐싱에서 제외
        if not entry.get("cache_enabled", True):
            continue
        root = str(entry.get("root_lot_id") or "").strip().upper()
        if root and root not in seen:
            seen.add(root)
            out.append(root)
    return out


def root_ram_cache_available() -> bool:
    if _env_bool("FLOW_DISABLE_SPLITTABLE_ROOT_LOT_RAM_CACHE", False):
        return False
    try:
        from core.worker_dispatch import server_role

        if server_role() == "worker" and not _env_bool("FLOW_ENABLE_WORKER_RAM_CACHE", False):
            return False
    except Exception:
        pass
    return True


def root_ram_cache_refresh_minutes() -> int:
    return _env_int(
        "FLOW_SPLITTABLE_ROOT_LOT_RAM_CACHE_REFRESH_MINUTES",
        ROOT_RAM_CACHE_REFRESH_MINUTES_DEFAULT,
        ROOT_RAM_CACHE_REFRESH_MINUTES_MIN,
        ROOT_RAM_CACHE_REFRESH_MINUTES_MAX,
    )


def _root_ram_cache_recent_limit() -> int:
    return _env_int("FLOW_SPLITTABLE_ROOT_LOT_RAM_CACHE_RECENT_ROOTS", ROOT_RAM_CACHE_RECENT_ROOTS_DEFAULT, 0, ROOT_RAM_CACHE_ROOTS_MAX)


def _root_ram_cache_frequent_limit() -> int:
    return _env_int("FLOW_SPLITTABLE_ROOT_LOT_RAM_CACHE_FREQUENT_ROOTS", ROOT_RAM_CACHE_FREQUENT_ROOTS_DEFAULT, 0, ROOT_RAM_CACHE_ROOTS_MAX)


def _root_ram_cache_step_ids() -> list[str]:
    """메모리 캐싱 대상 step_id 목록. env(콤마) 우선, 없으면 톱니바퀴 설정.

    이 step 들을 지난(통과=tkout) lot 을 latest cache 에서 tkout_time 최신순으로
    캐싱한다. 비면 step 필터 없이 searched+recent 만 유지한다.
    """
    raw = os.environ.get("FLOW_SPLITTABLE_ROOT_LOT_RAM_CACHE_STEP_IDS")
    if raw is None:
        raw = _root_ram_settings().get("step_ids")
    step_ids = _normalize_str_list(raw)
    return step_ids or list(ROOT_RAM_CACHE_STEP_IDS_DEFAULT)


def _root_ram_cache_priority_prefix() -> str:
    """step/latest 후보 정렬에서 우선 적재할 root lot prefix (기본 "AZ").

    env FLOW_SPLITTABLE_ROOT_LOT_RAM_CACHE_ROOT_PREFIX > 톱니바퀴 설정
    (priority_root_prefix) > 기본값. 빈 문자열로 지정하면 prefix 우선순위 없음.
    """
    raw = os.environ.get("FLOW_SPLITTABLE_ROOT_LOT_RAM_CACHE_ROOT_PREFIX")
    if raw is None:
        raw = _root_ram_settings().get("priority_root_prefix")
    if raw is None:
        return ROOT_RAM_CACHE_PRIORITY_ROOT_PREFIX_DEFAULT
    return str(raw).strip().upper()


def _root_ram_cache_searched_limit() -> int:
    if "FLOW_SPLITTABLE_ROOT_LOT_RAM_CACHE_SEARCHED_ROOTS" in os.environ:
        return _env_int(
            "FLOW_SPLITTABLE_ROOT_LOT_RAM_CACHE_SEARCHED_ROOTS",
            ROOT_RAM_CACHE_SEARCHED_ROOTS_DEFAULT,
            0,
            ROOT_RAM_CACHE_ROOTS_MAX,
        )
    if "FLOW_SPLITTABLE_ROOT_LOT_RAM_CACHE_FREQUENT_ROOTS" in os.environ:
        return _root_ram_cache_frequent_limit()
    return _bounded_int(
        _root_ram_settings().get("searched_limit"),
        ROOT_RAM_CACHE_SEARCHED_ROOTS_DEFAULT,
        0,
        ROOT_RAM_CACHE_ROOTS_MAX,
    )


def _root_ram_cache_target_roots() -> int:
    if "FLOW_SPLITTABLE_ROOT_LOT_RAM_CACHE_TARGET_ROOTS" in os.environ:
        return _env_int(
            "FLOW_SPLITTABLE_ROOT_LOT_RAM_CACHE_TARGET_ROOTS",
            ROOT_RAM_CACHE_TARGET_ROOTS_DEFAULT,
            0,
            ROOT_RAM_CACHE_ROOTS_MAX,
        )
    return _bounded_int(
        _root_ram_settings().get("target_roots"),
        ROOT_RAM_CACHE_TARGET_ROOTS_DEFAULT,
        0,
        ROOT_RAM_CACHE_ROOTS_MAX,
    )


ROOT_RAM_CACHE_TARGET_ROOTS_DEV_DEFAULT = 50


def _root_ram_cache_target_roots_dev() -> int:
    """개발서버 기본 target root 수 — 운영보다 작게 유지(메모리 보호).

    제품별 dev 예산(max_roots_dev)이 없을 때의 폴백. 기본 50 과 운영 target 중
    작은 값. env FLOW_SPLITTABLE_ROOT_LOT_RAM_CACHE_TARGET_ROOTS_DEV 로 강제.
    """
    prod_target = _root_ram_cache_target_roots()
    base = min(ROOT_RAM_CACHE_TARGET_ROOTS_DEV_DEFAULT, prod_target)
    return _env_int(
        "FLOW_SPLITTABLE_ROOT_LOT_RAM_CACHE_TARGET_ROOTS_DEV",
        base,
        0,
        ROOT_RAM_CACHE_ROOTS_MAX,
    )


def _root_ram_cache_default_target_roots() -> int:
    """제품별 예산이 없을 때 적용할 기본 target — 서버 역할(운영/개발) 반영."""
    if _root_ram_cache_use_dev():
        return _root_ram_cache_target_roots_dev()
    return _root_ram_cache_target_roots()


def _root_ram_cache_load_workers() -> int:
    """상시 캐시 예열의 병렬 파티션-로드 워커 수 — 역할 기반 기본값.

    예열은 파티션 parquet 을 읽어 RAM 에 적재한다. 워커 수는 '동시에 몇 파티션을
    로드하나'라는 IO/메모리 축(각 collect 는 공용 Polars 풀을 빌려 CPU 병렬도는
    풀 크기로 상한). query_workers 설정과는 무관(분리).
      · 개발서버(dev/worker): 1 — **순차 로드**. 병렬 로드는 한 번에 여러 프레임을
        메모리에 올려 순간 RSS 스파이크 → 메모리 워치독 축출/OOM 을 유발했다.
        개발서버는 메모리 안전을 예열 속도보다 우선한다(사용자 승인: 순차 허용).
      · 운영(api/standalone-prod): 2 — 사용자 검색에 코어를 양보하며 완만히 예열.
    env FLOW_SPLITTABLE_ROOT_LOT_RAM_CACHE_LOAD_WORKERS 로 강제 가능(1~32).
    """
    # API searches use up to four Polars threads. Background RAM warmup stays
    # single-file even on production so five interactive users keep CPU/I/O.
    default = 1
    return _env_int("FLOW_SPLITTABLE_ROOT_LOT_RAM_CACHE_LOAD_WORKERS", default, 1, 32)


def root_ram_cache_settings() -> dict[str, Any]:
    return {
        "step_ids": _root_ram_cache_step_ids(),
        "priority_root_prefix": _root_ram_cache_priority_prefix(),
        "searched_limit": _root_ram_cache_searched_limit(),
        "recent_roots": _root_ram_cache_recent_limit(),
        "frequent_roots": _root_ram_cache_frequent_limit(),
        "target_roots": _root_ram_cache_target_roots(),
    }


def _root_ram_cache_cpu_budget_cores() -> float:
    default = min(ROOT_RAM_CACHE_CPU_CORES_DEFAULT, cpu_budget_cores())
    return _env_float(
        "FLOW_SPLITTABLE_ROOT_LOT_RAM_CACHE_CPU_CORES",
        default,
        0.1,
        ROOT_RAM_CACHE_CPU_CORES_DEFAULT,
    )


def _root_ram_cache_resource_snapshot() -> dict[str, Any]:
    snap: dict[str, Any] = {}
    try:
        snap.update(process_memory_snapshot())
    except Exception:
        pass
    try:
        snap.update(process_cpu_snapshot(guard_cores=_root_ram_cache_cpu_budget_cores()))
    except Exception:
        pass
    return snap


def _root_ram_cache_resource_guard_reason() -> tuple[str, dict[str, Any]]:
    now = time.time()
    with _ROOT_RAM_CACHE_LOCK:
        last = float(_ROOT_RAM_RESOURCE_STATE.get("checked_epoch") or 0.0)
        if now - last < ROOT_RAM_CACHE_RESOURCE_CHECK_SEC:
            return (
                str(_ROOT_RAM_RESOURCE_STATE.get("reason") or ""),
                dict(_ROOT_RAM_RESOURCE_STATE.get("snapshot") or {}),
            )
    snap = _root_ram_cache_resource_snapshot()
    reason = ""
    try:
        # process_memory_over_limit(=rss>=limit) 는 RSS-only false signal 이라
        # 회수 가능한 mmap/arena 로 부풀어도 상시 참이 돼 RAM 캐시 적재를 영구
        # 차단했다. 실제 호스트 메모리 압박을 반영하는 process_memory_high 만 본다
        # (RAM 캐시는 자체 max_bytes eviction 으로 상한이 이미 보장됨).
        if process_memory_high():
            reason = "process_memory_high"
    except Exception:
        pass
    if not reason and bool(snap.get("process_cpu_over_limit")):
        reason = "process_cpu_high"
    with _ROOT_RAM_CACHE_LOCK:
        _ROOT_RAM_RESOURCE_STATE.update({
            "checked_epoch": now,
            "reason": reason,
            "snapshot": snap,
        })
    return reason, snap


ROOT_RAM_CACHE_AUTO_MIN_GB = 2.0
ROOT_RAM_CACHE_AUTO_MAX_GB = 6.0
_ROOT_RAM_AUTO_GB_TTL_SEC = 5.0
_ROOT_RAM_AUTO_GB_LOCK = threading.Lock()
_ROOT_RAM_AUTO_GB_CACHE: dict[str, tuple[float, float]] = {}


def _root_ram_cache_auto_max_gb() -> float:
    """현재 호스트 메모리 상황 기반 동적 예산 — [2GB, 6GB].

    10GB급 호스트에서 고정 40%-of-limit 예산이 상주 RSS 를 밀어올려
    메모리 가드 503(대시보드 등 heavy 경로 거절)을 유발했다. 예산을
    `(현재 가용 + 캐시가 이미 점유한 양 - 가드 하한) × 0.4` 로 계산해
    [2, 6]GB 로 클램프한다. 캐시 보유분을 되더해 "캐시가 가용치를 깎아 자기
    예산을 줄이는" 피드백 루프를 상쇄 — 예산은 호스트의 다른 소비자가 얼마나
    쓰는지에만 반응한다. 5s TTL 메모이즈로 snapshot 비용/값 요동을 제한.
    20GB 급 호스트에서는 가용 여유가 크므로 최대 6GB 까지 허용해 캐시 히트율을
    높이고 I/O 경합을 줄인다.
    Env FLOW_SPLITTABLE_ROOT_LOT_RAM_CACHE_MAX_GB 는 여전히 정확한 값을 고정.
    """
    now = time.monotonic()
    with _ROOT_RAM_AUTO_GB_LOCK:
        cached = _ROOT_RAM_AUTO_GB_CACHE.get("v")
        if cached is not None and now - cached[0] < _ROOT_RAM_AUTO_GB_TTL_SEC:
            return cached[1]
    lo, hi = ROOT_RAM_CACHE_AUTO_MIN_GB, ROOT_RAM_CACHE_AUTO_MAX_GB
    budget = lo
    try:
        snap = system_memory_snapshot()
        avail_gb = float(snap.get("system_memory_available_gb") or 0.0)
        min_avail_gb = float(snap.get("system_memory_min_available_gb") or 2.0)
        total_gb = float(snap.get("system_memory_total_gb") or 0.0)
    except Exception:
        avail_gb = 0.0
        min_avail_gb = 2.0
        total_gb = 0.0
    if total_gb > 0 and avail_gb > 0:
        with _ROOT_RAM_CACHE_LOCK:
            held_gb = _root_ram_total_bytes_locked() / (1024.0 ** 3)
        slack_gb = max(0.0, (avail_gb + held_gb) - min_avail_gb)
        budget = max(lo, min(hi, round(slack_gb * 0.4, 2)))
    else:
        # 호스트 메모리를 알 수 없으면 프로세스 한도 기반 폴백 (같은 밴드로 클램프).
        try:
            limit_gb = float(process_memory_limit_gb() or 0.0)
        except Exception:
            limit_gb = 0.0
        if limit_gb > 0:
            budget = max(lo, min(hi, round(limit_gb * 0.40, 1)))
    with _ROOT_RAM_AUTO_GB_LOCK:
        _ROOT_RAM_AUTO_GB_CACHE["v"] = (now, budget)
    return budget


def _root_ram_cache_max_bytes() -> int:
    if "FLOW_SPLITTABLE_ROOT_LOT_RAM_CACHE_MAX_GB" in os.environ:
        gb = _env_float("FLOW_SPLITTABLE_ROOT_LOT_RAM_CACHE_MAX_GB", ROOT_RAM_CACHE_MAX_GB_DEFAULT, 0.0, 64.0)
        budget = int(gb * 1024 * 1024 * 1024)
    else:
        # 톱니바퀴 설정(관리자 UI): 운영/개발 분리 — root_ram_gb(_dev)>0 이면 고정.
        _gb_s = None
        try:
            from core import cache_settings
            _gb_s = cache_settings.get_float_role("root_ram_gb", _root_ram_cache_use_dev())
        except Exception:
            _gb_s = None
        if _gb_s is not None and _gb_s > 0:
            budget = int(max(0.0, min(64.0, _gb_s)) * 1024 * 1024 * 1024)
        else:
            budget = int(_root_ram_cache_auto_max_gb() * 1024 * 1024 * 1024)
    try:
        from core import cache_budget
        # 개별 env 고정값도 전체 프로세스 캐시 안전 풀을 우회하지 않는다.
        budget = cache_budget.capped("splittable_root_ram", budget)
    except Exception:
        pass
    return budget


def _root_ram_cache_build_max_bytes() -> int:
    mb = _env_float("FLOW_SPLITTABLE_ROOT_LOT_RAM_CACHE_BUILD_MAX_MB", ROOT_RAM_CACHE_BUILD_MAX_MB_DEFAULT, 0.0, 1024 * 1024.0)
    return int(mb * 1024 * 1024)


def _estimated_df_bytes(df: pl.DataFrame) -> int:
    try:
        return int(df.estimated_size())
    except Exception:
        try:
            return int(df.height) * max(1, len(df.columns)) * 16
        except Exception:
            return 0


def _root_cache_key(fp: Path, root_lot_id: str) -> tuple[str, str]:
    return (str(Path(fp).resolve()), str(root_lot_id or "").strip().upper())


def _partition_sig(files: list[Path]) -> tuple[int, float, int]:
    count = 0
    max_mtime = 0.0
    total_size = 0
    for fp in files or []:
        try:
            st = fp.stat()
        except Exception:
            continue
        count += 1
        max_mtime = max(max_mtime, float(st.st_mtime))
        total_size += int(st.st_size)
    return (count, max_mtime, total_size)


def _root_ram_source_key(status: dict[str, Any]) -> tuple[Any, ...]:
    meta = status.get("meta") or {}
    return (
        meta.get("version") or CACHE_VERSION,
        meta.get("source_path") or "",
        meta.get("source_mtime") or 0,
        meta.get("source_size") or 0,
        meta.get("built_at") or "",
    )


def _root_ram_total_bytes_locked(exclude_key: tuple[str, str] | None = None) -> int:
    total = 0
    for key, entry in _ROOT_RAM_CACHE.items():
        if exclude_key is not None and key == exclude_key:
            continue
        try:
            total += int(entry.get("estimated_bytes") or 0)
        except Exception:
            pass
    return total


def root_ram_cache_lot_count(source_path: str = "") -> int:
    """현재 메모리에 올라와 있는 '분리된 랏(root_lot_id) 캐시' 개수.
    source_path 지정 시 해당 제품 파일에 속한 랏 캐시 수만 센다."""
    with _ROOT_RAM_CACHE_LOCK:
        if not source_path:
            return len(_ROOT_RAM_CACHE)
        sp = str(source_path)
        return sum(1 for key in _ROOT_RAM_CACHE if key[0] == sp)


def _root_ram_source_bytes_locked() -> dict[str, int]:
    """source_path(제품 파일)별 캐시 점유 바이트 합계."""
    totals: dict[str, int] = {}
    for key, entry in _ROOT_RAM_CACHE.items():
        try:
            totals[key[0]] = totals.get(key[0], 0) + int(entry.get("estimated_bytes") or 0)
        except Exception:
            pass
    return totals


def _root_ram_source_share_locked(source_path: str) -> int:
    """제품별 공정 지분(바이트) = 전체 예산 / 활성 소스 수.

    활성 소스 = 현재 캐시에 항목이 있는 소스 ∪ 삽입하려는 소스. 제품이 늘면
    지분이 자동으로 줄어 기존 제품의 초과분이 eviction 대상이 된다."""
    max_bytes = _root_ram_cache_max_bytes()
    if max_bytes <= 0:
        return 0
    sources = {key[0] for key in _ROOT_RAM_CACHE}
    if source_path:
        sources.add(source_path)
    return max_bytes // max(1, len(sources))


def _evict_root_ram_locked(
    source_path: str = "",
    reserve_bytes: int = 0,
    keep_keys: set[tuple[str, str]] | None = None,
    *,
    strict_keep: bool = False,
    exclude_key: tuple[str, str] | None = None,
) -> bool:
    """예산 초과분을 제거하고, 예산(reserve 포함)을 맞췄으면 True.

    제품(source_path) 인지 eviction — 한 제품이 다른 제품의 지분(share)을
    침범하지 못하게 하고, 전체 clear 는 절대 하지 않는다(점진 교체 보장).

    제거 우선순위(항상 오래된 순):
      · 삽입 소스가 지분을 초과한 상태 → 자기 항목부터 LRU(자기 제한).
      · 지분 이내 → 지분 초과한 다른 소스의 항목부터 회수(공정 재분배),
        없으면 자기 항목 LRU.
      · (strict_keep=False 한정) 그래도 없으면 아무 소스나 keep_keys 제외
        항목 — 최후 수단.
    전부 불가능하면 False 를 반환해 호출측이 삽입을 스킵한다.

    strict_keep=True 면 keep_keys 항목은 어떤 경우에도 제거하지 않는다 — 예열
    삽입에서 자기보다 선순위 후보를 밀어내는 것을 막는다.
    exclude_key 는 같은 키를 교체(swap)하는 중이므로 용량 계산·희생 대상에서
    제외한다."""
    keep_keys = keep_keys or set()
    max_bytes = _root_ram_cache_max_bytes()
    if max_bytes <= 0:
        return False
    reserve = max(0, reserve_bytes)
    while _ROOT_RAM_CACHE and _root_ram_total_bytes_locked(exclude_key) + reserve > max_bytes:
        per_source = _root_ram_source_bytes_locked()
        share = _root_ram_source_share_locked(source_path)
        own_bytes = per_source.get(source_path, 0) if source_path else 0

        def _own_victim() -> tuple[str, str] | None:
            for key in _ROOT_RAM_CACHE:
                if key == exclude_key or key in keep_keys:
                    continue
                if source_path and key[0] == source_path:
                    return key
            return None

        def _over_share_victim() -> tuple[str, str] | None:
            for key in _ROOT_RAM_CACHE:
                if key == exclude_key or key in keep_keys:
                    continue
                if key[0] != source_path and per_source.get(key[0], 0) > share:
                    return key
            return None

        if source_path and own_bytes > share:
            victim = _own_victim() or _over_share_victim()
        else:
            victim = _over_share_victim() or _own_victim()
        # 최후 수단(비-strict): 아무 소스나 오래된 항목
        if victim is None and not strict_keep:
            for key in _ROOT_RAM_CACHE:
                if key == exclude_key or key in keep_keys:
                    continue
                victim = key
                break
        if victim is None:
            return False
        _ROOT_RAM_CACHE.pop(victim, None)
    return _root_ram_total_bytes_locked(exclude_key) + reserve <= max_bytes


def emergency_evict_root_ram(max_bytes: int) -> int:
    """메모리 워치독 긴급 축출 — 오래된 항목(LRU 앞)부터 최대 max_bytes 제거.

    전체 clear 는 하지 않는다. access 메타데이터(_ROOT_RAM_ACCESS)는 남겨
    다음 예열 사이클이 자주 쓰인 root 를 우선 재적재하게 한다.
    반환: 회수한 추정 바이트."""
    if max_bytes <= 0:
        return 0
    freed = 0
    with _ROOT_RAM_CACHE_LOCK:
        while _ROOT_RAM_CACHE and freed < max_bytes:
            _key, entry = _ROOT_RAM_CACHE.popitem(last=False)
            try:
                freed += int(entry.get("estimated_bytes") or 0)
            except Exception:
                pass
    return freed


def clear_root_ram_cache() -> None:
    with _ROOT_RAM_CACHE_LOCK:
        _ROOT_RAM_CACHE.clear()
        _ROOT_RAM_ACCESS.clear()
        _ROOT_RAM_STATUS.update({
            "last_refresh_at": "",
            "last_refresh_epoch": 0.0,
            "last_error": "",
            "products": [],
        })
        _ROOT_RAM_RESOURCE_STATE.update({"checked_epoch": 0.0, "reason": "", "snapshot": {}})


def evict_root_ram_cache_entry(source_path: str, root_lot_id: str) -> dict[str, Any]:
    """개별 root lot 캐시 항목 제거 — 관리자 페이지에서 선택 삭제용."""
    root = str(root_lot_id or "").strip().upper()
    sp = str(source_path or "").strip()
    removed = False
    with _ROOT_RAM_CACHE_LOCK:
        # source_path + root_lot_id 조합으로 정확 매칭, 또는 source_path 비어있으면 root만으로 매칭
        keys_to_remove = []
        for key in list(_ROOT_RAM_CACHE.keys()):
            if sp and key[0] != sp:
                continue
            if key[1].upper() == root:
                keys_to_remove.append(key)
        for key in keys_to_remove:
            _ROOT_RAM_CACHE.pop(key, None)
            _ROOT_RAM_ACCESS.pop(key, None)
            removed = True
    return {"ok": True, "removed": removed, "root_lot_id": root, "removed_count": len(keys_to_remove)}


# 검색된 root 마다 한 줄씩 쌓이는 접근 메타 — 캐시 eviction 과 무관하게
# 프로세스 수명 내내 자라던 무한 성장 누수. 상한 도달 시 오래된 접근부터
# 반절 정리한다 (정렬은 overflow 시에만 발생).
_ROOT_RAM_ACCESS_MAX = 20000


def _prune_root_ram_access_locked() -> None:
    if len(_ROOT_RAM_ACCESS) <= _ROOT_RAM_ACCESS_MAX:
        return
    items = sorted(
        _ROOT_RAM_ACCESS.items(),
        key=lambda kv: float(kv[1].get("last_access_epoch") or 0.0),
    )
    for key, _meta in items[: len(items) - _ROOT_RAM_ACCESS_MAX // 2]:
        # 현재 캐시에 실제로 올라가 있는 root 의 접근 메타는 유지 (통계 표시용).
        if key not in _ROOT_RAM_CACHE:
            _ROOT_RAM_ACCESS.pop(key, None)


def _record_root_access(fp: Path, root_lot_id: str) -> None:
    root = str(root_lot_id or "").strip().upper()
    if not root:
        return
    key = _root_cache_key(fp, root)
    now = time.time()
    with _ROOT_RAM_CACHE_LOCK:
        cur = dict(_ROOT_RAM_ACCESS.get(key) or {})
        cur.update({
            "source_path": key[0],
            "root_lot_id": root,
            "last_access_epoch": now,
            "access_count": int(cur.get("access_count") or 0) + 1,
        })
        _ROOT_RAM_ACCESS[key] = cur
        _prune_root_ram_access_locked()


def record_root_access(fp: Path, root_lot_id: str) -> None:
    _record_root_access(fp, root_lot_id)


def _root_ram_cache_get(fp: Path, root_lot_id: str, files: list[Path], status: dict[str, Any]) -> pl.LazyFrame | None:
    if not root_ram_cache_available() or _root_ram_cache_max_bytes() <= 0:
        return None
    root = str(root_lot_id or "").strip().upper()
    key = _root_cache_key(fp, root)
    source_key = _root_ram_source_key(status)
    part_sig = _partition_sig(files)
    with _ROOT_RAM_CACHE_LOCK:
        entry = _ROOT_RAM_CACHE.get(key)
        if not entry:
            return None
        if entry.get("source_key") != source_key or entry.get("partition_sig") != part_sig:
            # 소스 갱신으로 stale 이 되어도 즉시 지우지 않는다 — 새 데이터가
            # 준비되면 refresh 가 같은 키를 개별 교체(swap)한다. 지우면 소스
            # 파일 갱신 순간 전 항목이 한꺼번에 사라지는 갭이 생긴다.
            # stale 항목은 access 갱신 없이 두어 LRU 최우선 eviction 대상이 된다.
            if not entry.get("stale"):
                entry["stale"] = True
                entry["stale_epoch"] = time.time()
            return None
        entry["last_access_epoch"] = time.time()
        entry["access_count"] = int(entry.get("access_count") or 0) + 1
        _ROOT_RAM_CACHE.move_to_end(key)
        df = entry.get("df")
    if df is None:
        return None
    try:
        return df.lazy()
    except Exception:
        return None


def _load_root_ram_cache_frame(files: list[Path]) -> pl.DataFrame:
    return pl.scan_parquet([str(p) for p in files], hive_partitioning=True).collect()


def _root_ram_cache_update_metadata(
    fp: Path,
    root_lot_id: str,
    *,
    cache_group: str = "",
    cache_sources: list[str] | None = None,
) -> None:
    root = str(root_lot_id or "").strip().upper()
    if not root:
        return
    key = _root_cache_key(fp, root)
    sources = [str(source or "").strip() for source in (cache_sources or []) if str(source or "").strip()]
    with _ROOT_RAM_CACHE_LOCK:
        entry = _ROOT_RAM_CACHE.get(key)
        if not entry:
            return
        if cache_group:
            entry["cache_group"] = cache_group
        if sources:
            entry["cache_sources"] = sources


def _root_ram_cache_store_frame(
    fp: Path,
    root_lot_id: str,
    df: pl.DataFrame,
    files: list[Path],
    status: dict[str, Any],
    *,
    cache_group: str = "",
    cache_sources: list[str] | None = None,
    protect_keys: set[tuple[str, str]] | None = None,
) -> pl.LazyFrame | None:
    """이미 로드된 DataFrame 을 RAM 캐시에 저장(락+eviction). 병렬 예열이 프레임을
    먼저 병렬로 읽어온 뒤, 우선순위 순서대로 이 함수로 삽입해 eviction 결정성을
    유지한다.

    protect_keys 가 주어지면(예열 경로) 해당 키는 절대 evict 하지 않으며, 이들을
    유지한 채 예산을 못 맞추면 삽입하지 않고 None 을 반환한다 — 후순위 lot 이
    선순위(AZ/priority/searched) lot 을 밀어내는 것을 방지."""
    if df is None:
        return None
    root = str(root_lot_id or "").strip().upper()
    key = _root_cache_key(fp, root)
    source_key = _root_ram_source_key(status)
    part_sig = _partition_sig(files)
    estimated_bytes = _estimated_df_bytes(df)
    max_bytes = _root_ram_cache_max_bytes()
    if max_bytes <= 0 or estimated_bytes > max_bytes:
        return None
    now = time.time()
    # 그룹은 후보 선정 소스에서 넘어온 값(step/other)을 그대로 쓴다. 사용자 검색으로
    # 즉석 적재되는 등 소스가 없으면 "other".
    group = cache_group or "other"
    sources = [str(source or "").strip() for source in (cache_sources or []) if str(source or "").strip()]
    source_path = key[0]
    with _ROOT_RAM_CACHE_LOCK:
        keep = {key} | (protect_keys or set())
        strict = protect_keys is not None
        # exclude_key=key: 같은 키를 새 프레임으로 교체하는 경우 기존(stale)
        # 항목의 바이트를 이중 계산하지 않는다 — 교체는 아래 대입 한 번으로
        # 원자적으로 일어나며, 그 전까지 기존 데이터가 계속 서빙 가능하다.
        if not _evict_root_ram_locked(
            source_path,
            reserve_bytes=estimated_bytes,
            keep_keys=keep,
            strict_keep=strict,
            exclude_key=key,
        ):
            if strict:
                return None
        _ROOT_RAM_CACHE[key] = {
            "version": ROOT_RAM_CACHE_VERSION,
            "source_path": str(Path(fp).resolve()),
            "root_lot_id": root,
            "source_key": source_key,
            "partition_sig": part_sig,
            "df": df,
            "row_count": int(df.height),
            "estimated_bytes": estimated_bytes,
            "loaded_at": datetime.fromtimestamp(now).isoformat(timespec="seconds"),
            "loaded_epoch": now,
            "last_access_epoch": now,
            "access_count": int((_ROOT_RAM_ACCESS.get(key) or {}).get("access_count") or 0),
            "cache_group": group,
            "cache_sources": sources,
        }
        _ROOT_RAM_CACHE.move_to_end(key)
        _evict_root_ram_locked(source_path, keep_keys=keep, strict_keep=strict)
    return df.lazy()


def _root_ram_cache_put(
    fp: Path,
    root_lot_id: str,
    files: list[Path],
    status: dict[str, Any],
    *,
    cache_group: str = "",
    cache_sources: list[str] | None = None,
) -> pl.LazyFrame | None:
    if not root_ram_cache_available() or _root_ram_cache_max_bytes() <= 0 or not files:
        return None
    guard_reason, _snap = _root_ram_cache_resource_guard_reason()
    if guard_reason:
        return None
    root = str(root_lot_id or "").strip().upper()
    try:
        df = _load_root_ram_cache_frame(files)
    except Exception as exc:
        logger.debug("ML_TABLE root RAM cache load failed source=%s root=%s: %s", fp, root, exc)
        return None
    return _root_ram_cache_store_frame(
        fp, root, df, files, status, cache_group=cache_group, cache_sources=cache_sources,
    )


def _root_ram_prefetch_key(fp: Path, root_lot_id: str) -> str:
    return f"{Path(fp).resolve()}|{str(root_lot_id or '').strip().upper()}"


def root_ram_prefetch_snapshot(limit: int = 50) -> dict[str, Any]:
    """Small, path-safe snapshot for the cache management screen."""
    with _ROOT_RAM_PREFETCH_LOCK:
        depth = len(_ROOT_RAM_PREFETCH_QUEUE)
        queued = list(_ROOT_RAM_PREFETCH_QUEUE)[:max(0, int(limit or 0))]
        state = dict(_ROOT_RAM_PREFETCH_STATE)
    return {
        **state,
        "depth": depth,
        "queued": [
            {"product": Path(fp).stem, "root_lot_id": root}
            for fp, root in queued
        ],
    }


def _root_ram_prefetch_loop() -> None:
    """Load searched roots into API-local RAM only after user traffic is quiet."""
    import gc

    while True:
        _ROOT_RAM_PREFETCH_WAKE.wait(timeout=60.0)
        while True:
            with _ROOT_RAM_PREFETCH_LOCK:
                if not _ROOT_RAM_PREFETCH_QUEUE:
                    _ROOT_RAM_PREFETCH_STATE.update(
                        running=False, current_product="", current_root=""
                    )
                    _ROOT_RAM_PREFETCH_WAKE.clear()
                    break
                fp, root = _ROOT_RAM_PREFETCH_QUEUE.popleft()
                _ROOT_RAM_PREFETCH_STATE.update(
                    running=True,
                    current_product=Path(fp).stem,
                    current_root=root,
                    last_error="",
                )
            key = _root_ram_prefetch_key(fp, root)
            requeued = False
            try:
                from core import request_priority

                quiet_for = _env_float(
                    "FLOW_SPLITTABLE_ROOT_PREFETCH_IDLE_QUIET_SEC", 5.0, 0.0, 300.0
                )
                while request_priority.users_active(quiet_for_sec=quiet_for):
                    time.sleep(1.0)
                status = cache_status(fp)
                files = _partition_files(cache_dir_for(fp), root)
                if not files or not status.get("has_cache"):
                    continue
                # A single unusually large root must not create an unbounded
                # decompression spike. Parquet can expand several-fold in RAM;
                # skip RAM warming and keep serving it through projected disk I/O.
                max_input_mb = _env_float(
                    "FLOW_SPLITTABLE_ROOT_PREFETCH_MAX_INPUT_MB", 128.0, 1.0, 4096.0
                )
                compressed_bytes = 0
                for part in files:
                    try:
                        compressed_bytes += int(part.stat().st_size)
                    except OSError:
                        pass
                if compressed_bytes > int(max_input_mb * 1024 * 1024):
                    with _ROOT_RAM_PREFETCH_LOCK:
                        _ROOT_RAM_PREFETCH_STATE["last_error"] = (
                            f"prefetch_skipped_large_partition:{compressed_bytes / (1024 ** 2):.1f}MB"
                        )
                    continue
                if _root_ram_cache_get(fp, root, files, status) is not None:
                    continue
                guard_reason, _snapshot = _root_ram_cache_resource_guard_reason()
                if guard_reason:
                    with _ROOT_RAM_PREFETCH_LOCK:
                        _ROOT_RAM_PREFETCH_QUEUE.append((fp, root))
                        requeued = True
                    time.sleep(10.0)
                    break
                df = _load_root_ram_cache_frame(files)
                _root_ram_cache_store_frame(fp, root, df, files, status)
                df = None
                gc.collect()
            except Exception as exc:
                logger.debug(
                    "root RAM idle prefetch failed product=%s root=%s: %s",
                    Path(fp).stem, root, exc,
                )
                with _ROOT_RAM_PREFETCH_LOCK:
                    _ROOT_RAM_PREFETCH_STATE["last_error"] = str(exc)
            finally:
                with _ROOT_RAM_PREFETCH_LOCK:
                    if not requeued:
                        _ROOT_RAM_PREFETCH_PENDING.discard(key)
                    _ROOT_RAM_PREFETCH_STATE.update(
                        running=False, current_product="", current_root=""
                    )


def enqueue_root_ram_prefetch(fp: Path, root_lot_id: str) -> bool:
    """Queue a cold root for idle-time RAM warmup without delaying its request."""
    global _ROOT_RAM_PREFETCH_THREAD
    if not root_ram_cache_available() or _root_ram_cache_max_bytes() <= 0:
        return False
    fp = Path(fp).resolve()
    root = str(root_lot_id or "").strip().upper()
    if not root:
        return False
    key = _root_ram_prefetch_key(fp, root)
    max_queue = _env_int("FLOW_SPLITTABLE_ROOT_PREFETCH_MAX_QUEUE", 256, 1, 5000)
    with _ROOT_RAM_PREFETCH_LOCK:
        if key in _ROOT_RAM_PREFETCH_PENDING:
            return False
        if len(_ROOT_RAM_PREFETCH_QUEUE) >= max_queue:
            old_fp, old_root = _ROOT_RAM_PREFETCH_QUEUE.popleft()
            _ROOT_RAM_PREFETCH_PENDING.discard(_root_ram_prefetch_key(old_fp, old_root))
        _ROOT_RAM_PREFETCH_QUEUE.append((fp, root))
        _ROOT_RAM_PREFETCH_PENDING.add(key)
        if _ROOT_RAM_PREFETCH_THREAD is None or not _ROOT_RAM_PREFETCH_THREAD.is_alive():
            _ROOT_RAM_PREFETCH_THREAD = threading.Thread(
                target=_root_ram_prefetch_loop,
                daemon=True,
                name="ml-table-root-idle-prefetch",
            )
            _ROOT_RAM_PREFETCH_THREAD.start()
    _ROOT_RAM_PREFETCH_WAKE.set()
    return True


def _product_match_keys(fp: Path) -> set[str]:
    stem = Path(fp).stem.strip().upper()
    keys = {stem}
    if stem.startswith("ML_TABLE_"):
        keys.add(stem[len("ML_TABLE_"):])
    return {k for k in keys if k}


def _row_time_text(row: dict[str, Any]) -> str:
    return str(row.get("update_time") or row.get("tkout_time") or row.get("tkin_time") or row.get("time") or "")


def _latest_lot_by_root_wafer_path() -> Path:
    return PATHS.db_cache_dir / LATEST_LOT_BY_ROOT_WAFER_FILE


def _recent_root_lot_ids_from_latest_parquet(
    fp: Path,
    limit: int,
    *,
    step_ids: list[str] | None = None,
    allowed_roots: set[str] | None = None,
) -> list[str]:
    cache_fp = _latest_lot_by_root_wafer_path()
    if limit <= 0 or not cache_fp.is_file():
        return []
    keys = _product_match_keys(fp)
    norm_steps = [str(s or "").strip().upper() for s in (step_ids or []) if str(s or "").strip()]
    try:
        lf = pl.scan_parquet(str(cache_fp))
        cols = lf.collect_schema().names()
    except Exception:
        return []
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    if not root_col:
        return []
    product_col = _ci_col(cols, "product", "process_id", "PRODUCT", "PROCESS_ID")
    time_col = _ci_col(cols, "tkout_time", "update_time", "tkin_time", "time", "timestamp", "datetime")
    step_col = _ci_col(cols, "step_id", "STEP_ID", "step")
    q = lf
    if product_col and keys:
        q = q.filter(pl.col(product_col).cast(_STR, strict=False).str.strip_chars().str.to_uppercase().is_in(sorted(keys)))
    # step_ids 지정 시: 해당 step 을 지난(latest 행의 step_id 가 그 step) lot 만.
    # step 컬럼이 없으면 필터를 걸 수 없으므로 빈 결과(잘못된 전량 통과 방지).
    if norm_steps:
        if not step_col:
            return []
        q = q.filter(pl.col(step_col).cast(_STR, strict=False).str.strip_chars().str.to_uppercase().is_in(sorted(set(norm_steps))))
    q = q.select([
        pl.col(root_col).cast(_STR, strict=False).str.strip_chars().str.to_uppercase().alias("root_lot_id"),
        (
            pl.col(time_col).cast(_STR, strict=False).alias("__time")
            if time_col else pl.lit("").alias("__time")
        ),
    ]).filter(pl.col("root_lot_id").is_not_null() & (pl.col("root_lot_id") != ""))
    if allowed_roots is not None:
        q = q.filter(pl.col("root_lot_id").is_in(sorted(allowed_roots)))
    # AZ prefix root 우선: prefix 매칭 lot 을 tkout_time 최신순으로 먼저 채우고,
    # 남는 자리를 나머지 lot(tkout_time 최신순)으로 채운다.
    prefix = _root_ram_cache_priority_prefix()
    sort_exprs: list[Any] = []
    if prefix:
        sort_exprs.append(pl.col("root_lot_id").str.starts_with(prefix))
    if time_col:
        sort_exprs.append(pl.col("__time"))
    if sort_exprs:
        q = q.sort(sort_exprs, descending=True, nulls_last=True)
    try:
        rows = q.unique(subset=["root_lot_id"], keep="first", maintain_order=True).head(limit).collect()
    except Exception:
        return []
    return [str(v or "").strip().upper() for v in rows["root_lot_id"].to_list() if str(v or "").strip()]


def _recent_root_lot_ids_from_latest_cache(
    fp: Path,
    limit: int,
    *,
    step_ids: list[str] | None = None,
    allowed_roots: set[str] | None = None,
) -> list[str]:
    if limit <= 0:
        return []
    norm_steps = [str(s or "").strip().upper() for s in (step_ids or []) if str(s or "").strip()]
    out: list[str] = []
    seen: set[str] = set()

    def add(root_lot_id: str) -> None:
        root = str(root_lot_id or "").strip().upper()
        if (
            not root
            or root in seen
            or len(out) >= limit
            or (allowed_roots is not None and root not in allowed_roots)
        ):
            return
        seen.add(root)
        out.append(root)

    for root in _recent_root_lot_ids_from_latest_parquet(
        fp,
        limit,
        step_ids=norm_steps or None,
        allowed_roots=allowed_roots,
    ):
        add(root)
    if len(out) >= limit:
        return out
    try:
        from core import lot_progress_cache
        state = lot_progress_cache.read_lot_progress_cache(allow_stale=True)
    except Exception:
        return out
    keys = _product_match_keys(fp)
    step_set = set(norm_steps)
    rows = [row for row in (state.get("items") or []) if isinstance(row, dict)]
    # parquet 경로와 동일하게 AZ prefix root 우선 → 시간 최신순.
    prefix = _root_ram_cache_priority_prefix()

    def _fallback_rank(row: dict[str, Any]) -> tuple[int, str]:
        root = str(row.get("root_lot_id") or "").strip().upper()
        return (1 if prefix and root.startswith(prefix) else 0, _row_time_text(row))

    rows.sort(key=_fallback_rank, reverse=True)
    for row in rows:
        product = str(row.get("product") or row.get("process_id") or "").strip().upper()
        if keys and product and product not in keys:
            continue
        if step_set and str(row.get("step_id") or "").strip().upper() not in step_set:
            continue
        add(str(row.get("root_lot_id") or ""))
        if len(out) >= limit:
            break
    return out


def _frequent_root_lot_ids(fp: Path, limit: int) -> list[str]:
    if limit <= 0:
        return []
    source_path = str(Path(fp).resolve())
    with _ROOT_RAM_CACHE_LOCK:
        rows = [
            dict(row)
            for key, row in _ROOT_RAM_ACCESS.items()
            if key[0] == source_path and row.get("root_lot_id")
        ]
    rows.sort(key=lambda row: (int(row.get("access_count") or 0), float(row.get("last_access_epoch") or 0.0)), reverse=True)
    out: list[str] = []
    seen: set[str] = set()
    for row in rows:
        root = str(row.get("root_lot_id") or "").strip().upper()
        if not root or root in seen:
            continue
        seen.add(root)
        out.append(root)
        if len(out) >= limit:
            break
    return out


def _searched_root_lot_ids(fp: Path, limit: int) -> list[str]:
    if limit <= 0:
        return []
    source_path = str(Path(fp).resolve())
    with _ROOT_RAM_CACHE_LOCK:
        rows = [
            dict(row)
            for key, row in _ROOT_RAM_ACCESS.items()
            if key[0] == source_path and row.get("root_lot_id")
        ]
    rows.sort(key=lambda row: float(row.get("last_access_epoch") or 0.0), reverse=True)
    out: list[str] = []
    seen: set[str] = set()
    for row in rows:
        root = str(row.get("root_lot_id") or "").strip().upper()
        if not root or root in seen:
            continue
        seen.add(root)
        out.append(root)
        if len(out) >= limit:
            break
    return out


def _root_ram_cache_candidates(
    *,
    priority_roots: list[str] | None = None,  # 우선 lot 등록 페이지에서 등록된 root
    searched_roots: list[str],
    step_roots: list[str],
    latest_roots: list[str],
    target: int = 0,
) -> list[dict[str, Any]]:
    """상시 메모리 캐시에 올릴 root 후보를 **포함 우선순위 순서**로 만든다.

    우선순위(사용자 요구):
      ⓪ priority — 우선 lot 등록 페이지에서 등록한 root (무조건 최우선).
      ① searched — 한번이라도 검색된 root 는 무조건 최우선 포함.
      ② step   — 지정 step_id 를 지난(통과=tkout) lot 중 latest cache tkout_time 최신순.
      ③ latest — step 무관 최근 변경 root (step 미설정 시/남는 자리 채움).
    target(>0)이면 그 개수로 상한(≈1000). priority+searched 가 target 을 채우면
    나머지 소스는 자연히 잘려 priority·searched 가 항상 살아남는다.
    cache_group: priority 소스 = "priority", step 소스 = "step", 그 외 "other".
    """
    candidates: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def add(root_lot_id: str, source: str) -> None:
        root = str(root_lot_id or "").strip().upper()
        if not root:
            return
        if source == "priority":
            group = "priority"
        elif source == "step":
            group = "step"
        else:
            group = "other"
        current = candidates.get(root)
        if current is None:
            candidates[root] = {
                "root_lot_id": root,
                "cache_group": group,
                "cache_sources": [source],
            }
            return
        # priority > step > other 순서로 그룹 승격
        if group == "priority":
            current["cache_group"] = "priority"
        elif group == "step" and current["cache_group"] != "priority":
            current["cache_group"] = "step"
        sources = current.setdefault("cache_sources", [])
        if source not in sources:
            sources.append(source)

    # ⓪ 우선 lot 등록 root — 최우선 포함
    for root in (priority_roots or []):
        add(root, "priority")
    for root in searched_roots:
        add(root, "searched")
    for root in step_roots:
        add(root, "step")
    for root in latest_roots:
        add(root, "latest")

    rows = list(candidates.values())
    if target and target > 0:
        rows = rows[:target]
    return rows


def _discover_ml_table_files() -> list[Path]:
    roots: list[Path] = []
    seen_roots: set[str] = set()
    for root in (PATHS.base_root, PATHS.db_root):
        try:
            key = str(Path(root).resolve())
        except Exception:
            key = str(root)
        if key and key not in seen_roots:
            seen_roots.add(key)
            roots.append(Path(root))
    files: list[Path] = []
    seen_files: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        try:
            candidates = sorted(root.glob("ML_TABLE_*.parquet"), key=lambda p: p.name.lower())
        except Exception:
            candidates = []
        for fp in candidates:
            try:
                key = str(fp.resolve())
            except Exception:
                key = str(fp)
            if key not in seen_files and fp.is_file():
                seen_files.add(key)
                files.append(fp)
    return files


def discover_ml_table_files() -> list[Path]:
    """Public read-only discovery used by the shared search-cache maintainer."""
    return _discover_ml_table_files()


def _ensure_lookup_cache_ready_for_root_ram(fp: Path, status: dict[str, Any], *, force: bool = False) -> bool:
    if status.get("has_cache") and not status.get("source_stale"):
        return True
    try:
        source_size = int(_source_sig(fp).get("source_size") or 0)
    except Exception:
        source_size = 0
    max_build_bytes = _root_ram_cache_build_max_bytes()
    # 대용량 제품도 자동으로 lookup 캐시를 빌드한다. 빌드는 청크 단위
    # (_sink_lookup_cache_partitions_chunked)로 메모리를 가드하며 진행하므로
    # 파일 크기로 자동 빌드를 막지 않는다 — 막으면 대용량 제품이 영구 미캐시.
    # 운영자가 상한(env MB>0)을 두면 그 이하만 자동 빌드한다.
    #   · force=True(수동 스캔): 항상 빌드.
    #   · max_build_bytes<=0(기본, 무제한): 항상 빌드.
    #   · 상한 지정 시: source_size<=상한일 때만.
    if force or max_build_bytes <= 0 or not source_size or source_size <= max_build_bytes:
        enqueue_build(fp)
    return False


def refresh_root_lot_ram_cache(product: str = "", file: str = "", *, force: bool = False,
                               load_now: bool = False) -> dict[str, Any]:
    # load_now: 관리자 트리거(수동 스캔/전체 셋업)면 True — 활성 유저에게 yield 하지 않고
    # 지금 적재한다. 예약(백그라운드) warmup 은 기본 False(opportunistic) — 바쁜 서버에서
    # 유저 요청에 양보. 예전엔 force 와 무관하게 항상 yield 해, 바쁜 운영서버에서 수동
    # 스캔조차 '0/N 적재'만 뜨던 문제(첫 청크에서 users_active 로 break)를 해결.
    if not root_ram_cache_available() or _root_ram_cache_max_bytes() <= 0:
        return {
            "ok": False,
            "enabled": root_ram_cache_available(),
            "skipped": True,
            "reason": "disabled",
            "max_gb": round(_root_ram_cache_max_bytes() / (1024 ** 3), 3) if _root_ram_cache_max_bytes() else 0,
        }
    if product or file:
        fp = resolve_ml_table_file(product=product, file=file)
        files = [fp] if fp else []
    else:
        files = _discover_ml_table_files()
    settings = root_ram_cache_settings()
    step_ids = settings["step_ids"]
    recent_limit = int(settings["recent_roots"])
    searched_limit = int(settings["searched_limit"])
    target_roots = int(settings["target_roots"])
    # 제품별 예산이 없을 때 적용할 기본 target — 개발서버면 작게(메모리 보호).
    default_target_roots = _root_ram_cache_default_target_roots()
    # 진행 로그(캐시 관리 페이지 이벤트 로그) — 로드 중 실시간 진행 표시.
    try:
        from core.cache_event_log import record as _cache_log
    except Exception:
        _cache_log = None

    def _scan_progress(msg: str, *, ok: bool = True) -> None:
        if _cache_log is None:
            return
        try:
            _cache_log("cache_op", msg, ok=ok, product=product or "")
        except Exception:
            pass

    rows: list[dict[str, Any]] = []
    with _ROOT_RAM_REFRESH_LOCK:
        for fp in files:
            if fp is None:
                continue
            status = cache_status(fp)
            if not _ensure_lookup_cache_ready_for_root_ram(fp, status, force=force):
                # 원본 lookup 캐시(파티션)가 아직 없거나 stale — 빌드를 큐에 넣고
                # 이번 사이클은 적재를 건너뛴다. 대용량 제품은 빌드가 오래 걸리므로
                # 진행 로그로 "빌드 중"을 알려 조용한 미캐시로 오인하지 않게 한다.
                _reason = status.get("status") or "missing"
                _prod_lbl = _safe_product_token(Path(fp).stem) or Path(fp).name
                _scan_progress(
                    f"[빌드] {_prod_lbl}: 원본 lookup 캐시 준비 중({_reason}) — "
                    f"빌드는 청크 단위로 진행되며 완료 후 다음 사이클에 적재됩니다",
                    ok=True,
                )
                rows.append({
                    "file": Path(fp).name,
                    "ok": False,
                    "skipped": True,
                    "reason": _reason,
                    "cache_status": _reason,
                    "build_pending": True,
                })
                continue
            candidate_index_meta = read_candidate_index(fp)
            available_roots = {
                str(value or "").strip().upper()
                for value in (candidate_index_meta.get("root_lot_ids") or [])
                if str(value or "").strip()
            }
            if not available_roots:
                available_roots = {
                    str(value or "").strip().upper()
                    for value in _root_lot_ids_from_cache_dir(cache_dir_for(fp))
                    if str(value or "").strip()
                }
            roots: list[str] = []
            seen: set[str] = set()
            _file_key = Path(fp).name
            # ⓪ priority: 우선 lot 등록 페이지에서 등록된 root — 최우선, 매 사이클 갱신.
            _product_token = _safe_product_token(Path(fp).stem)
            priority_roots = _load_priority_root_lot_ids(_product_token)
            # 제품별 예산(max_roots)이 설정돼 있으면 이 파일에 한해 기본 target 을 덮어쓴다.
            # 미설정이면 서버 역할(운영/개발)에 맞는 기본 target 으로 폴백한다.
            product_budget = _root_ram_cache_product_budget(_product_token)
            file_target_roots = product_budget if product_budget > 0 else default_target_roots
            # ① searched: 한번이라도 검색된 root — 무조건 최우선, 매 사이클 갱신.
            searched_roots = _searched_root_lot_ids(fp, searched_limit)
            # ② step/latest 후보 — 빈 공간 채우기용이므로 매 사이클 계산하지 않고
            #    N번째 사이클에만 갱신한다. 이전 값이 있으면 재사용.
            _refresh_step_latest = force or (_ROOT_RAM_REFRESH_COUNTER % _ROOT_RAM_STEP_LATEST_REFRESH_EVERY == 0)
            if _refresh_step_latest:
                step_roots = _recent_root_lot_ids_from_latest_cache(
                    fp,
                    file_target_roots or recent_limit,
                    step_ids=step_ids,
                    allowed_roots=available_roots,
                ) if step_ids else []
                # latest 도 target 까지 공급 — 기존 recent_limit(기본 100) 상한 탓에
                # step 미설정 시 캐시가 ~100 root 에서 멈췄다. 실제 저장량은 바이트
                # 예산(eviction)이 동적으로 제한하므로 후보는 target 만큼 넉넉히.
                latest_roots = _recent_root_lot_ids_from_latest_cache(
                    fp,
                    file_target_roots or recent_limit,
                    allowed_roots=available_roots,
                )
                _ROOT_RAM_LAST_STEP_ROOTS[_file_key] = step_roots
                _ROOT_RAM_LAST_LATEST_ROOTS[_file_key] = latest_roots
            else:
                step_roots = _ROOT_RAM_LAST_STEP_ROOTS.get(_file_key, [])
                latest_roots = _ROOT_RAM_LAST_LATEST_ROOTS.get(_file_key, [])
            raw_candidates = _root_ram_cache_candidates(
                priority_roots=priority_roots,
                searched_roots=searched_roots,
                step_roots=step_roots,
                latest_roots=latest_roots,
                # target은 실제 lookup 파티션이 있는 후보를 거른 뒤 적용한다.
                # canonical latest-lot cache는 cross-product FAB lineage를 포함할 수
                # 있어 다른 제품 root가 섞인다. 먼저 자르면 존재하지 않는 root가
                # 슬롯을 먹고 해당 제품의 정상 lot가 RAM에 올라오지 않았다.
                target=0,
            )
            invalid_candidate_count = 0
            # fresh lookup에 root가 0개라면 "필터 없음"이 아니라 실제 적재
            # 가능 후보가 없다는 뜻이다. 빈 set도 유효한 allow-list로 취급한다.
            if available_roots is not None:
                invalid_candidate_count = len([
                    row for row in raw_candidates
                    if str(row.get("root_lot_id") or "").strip().upper() not in available_roots
                ])
                candidates = [
                    row for row in raw_candidates
                    if str(row.get("root_lot_id") or "").strip().upper() in available_roots
                ]
            else:
                candidates = raw_candidates
            if file_target_roots > 0:
                candidates = candidates[:file_target_roots]
            for candidate in candidates:
                root = str(candidate.get("root_lot_id") or "").strip().upper()
                if root and root not in seen:
                    seen.add(root)
                    roots.append(root)
            priority_target_roots = len([row for row in candidates if row.get("cache_group") == "priority"])
            step_target_roots = len([row for row in candidates if row.get("cache_group") == "step"])
            other_target_roots = len(candidates) - step_target_roots - priority_target_roots
            cached = 0
            missing = invalid_candidate_count
            resource_skipped = 0
            last_skip_reason = ""
            # Phase 0: 이미 캐시된 것/파티션 없는 것을 걸러 로드 대상만 추린다.
            to_load: list[tuple[dict[str, Any], str, list[Path]]] = []
            for candidate in candidates:
                root = str(candidate.get("root_lot_id") or "").strip().upper()
                cache_group = str(candidate.get("cache_group") or "")
                cache_sources = [str(source) for source in (candidate.get("cache_sources") or [])]
                part_files = _partition_files(cache_dir_for(fp), root)
                if not part_files:
                    missing += 1
                    continue
                if not force and _root_ram_cache_get(fp, root, part_files, status) is not None:
                    _root_ram_cache_update_metadata(
                        fp, root, cache_group=cache_group, cache_sources=cache_sources,
                    )
                    cached += 1
                    continue
                to_load.append((candidate, root, part_files))
            guard_reason, _snap = _root_ram_cache_resource_guard_reason()
            if guard_reason and to_load:
                resource_skipped += len(to_load)
                last_skip_reason = guard_reason
                to_load = []
            budget_skipped = 0
            if to_load:
                # 사용자 요청이 진행 중이면 예열을 잠시 멈춰 API 응답을 우선한다.
                from core import request_priority
                request_priority.yield_to_users(max_wait_sec=10.0)
                workers = _root_ram_cache_load_workers()
                # 후보(우선순위) 순서 → 캐시 키 인덱스. 예열 삽입 시 자기보다 앞선
                # 후보는 evict 금지(protect) — 후순위 latest lot 이 AZ/priority/
                # searched lot 을 밀어내지 못한다.
                candidate_keys = [
                    _root_cache_key(fp, str(c.get("root_lot_id") or "").strip().upper())
                    for c in candidates
                ]
                candidate_index = {k: i for i, k in enumerate(candidate_keys)}

                def _safe_load(files: list[Path]) -> pl.DataFrame | None:
                    try:
                        return _load_root_ram_cache_frame(files)
                    except Exception as exc:
                        logger.debug("root RAM warm load 실패 source=%s: %s", fp, exc)
                        return None

                def _store_frame(candidate, root, part_files, df) -> str:
                    """로드된 프레임 1개를 삽입. 반환: "cached"|"budget"|"skip"."""
                    nonlocal last_skip_reason
                    if df is None:
                        return "skip"
                    key = _root_cache_key(fp, root)
                    idx = candidate_index.get(key, len(candidate_keys))
                    if _root_ram_cache_store_frame(
                        fp,
                        root,
                        df,
                        part_files,
                        status,
                        cache_group=str(candidate.get("cache_group") or ""),
                        cache_sources=[str(s) for s in (candidate.get("cache_sources") or [])],
                        protect_keys=set(candidate_keys[:idx]),
                    ) is not None:
                        return "cached"
                    last_skip_reason = "ram_budget_full"
                    return "budget"

                # target 확대(≈1000 root)로 전량 선로드하면 예산과 무관하게 미삽입
                # 프레임이 RSS 를 부풀리므로 chunk 단위로 로드→삽입하고, 예산이
                # 차면 나머지(전부 후순위)는 이번 사이클에서 중단한다.
                #   · 순차(workers==1, 개발서버): chunk_size=1 — 한 번에 프레임 1개만
                #     메모리에 올려 로드→삽입→해제. 순간 RSS 스파이크를 없앤다.
                #   · 병렬(workers>1, 운영): 기존과 동일한 chunk 단위 병렬 로드.
                chunk_size = 1 if workers <= 1 else max(8, workers * 2)
                total_to_load = len(to_load)
                _prod_label = _product_token or Path(fp).stem
                if total_to_load:
                    _scan_progress(
                        f"[적재] {_prod_label}: {total_to_load} root 로드 시작 "
                        f"(target {file_target_roots}, workers {workers})"
                    )
                _progress_every = max(1, total_to_load // 4)
                _next_progress = _progress_every
                budget_stop = False
                for start in range(0, total_to_load, chunk_size):
                    # RAM warmup is opportunistic. If a user starts searching,
                    # stop this cycle and let the next scheduler tick continue.
                    # On-demand requests still read the projected disk partition.
                    if not load_now and request_priority.users_active(quiet_for_sec=5.0):
                        resource_skipped += total_to_load - start
                        last_skip_reason = "user_requests_active"
                        break
                    if budget_stop:
                        budget_skipped += total_to_load - start
                        last_skip_reason = "ram_budget_full"
                        break
                    chunk = to_load[start:start + chunk_size]
                    if workers > 1 and len(chunk) > 1:
                        # 병렬 로드 → 삽입.
                        loaded: dict[str, pl.DataFrame] = {}
                        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="root-ram-warm") as ex:
                            futs = {ex.submit(_safe_load, files): root for (_c, root, files) in chunk}
                            for fut in as_completed(futs):
                                df = fut.result()
                                if df is not None:
                                    loaded[futs[fut]] = df
                        for candidate, root, part_files in chunk:
                            df = loaded.get(root)
                            if df is None:
                                continue
                            # 로드 중 메모리 압박이 올라갔을 수 있으니 삽입 직전 재확인.
                            gr, _snap2 = _root_ram_cache_resource_guard_reason()
                            if gr:
                                resource_skipped += 1
                                last_skip_reason = gr
                                continue
                            outcome = _store_frame(candidate, root, part_files, df)
                            if outcome == "cached":
                                cached += 1
                            elif outcome == "budget":
                                budget_skipped += 1
                        loaded.clear()
                    else:
                        # 순차 모드: 한 번에 프레임 1개만 — 로드 즉시 삽입/해제.
                        for candidate, root, part_files in chunk:
                            # 로드 전에 가드 확인 — 압박 시 아예 로드하지 않는다.
                            gr, _snap2 = _root_ram_cache_resource_guard_reason()
                            if gr:
                                resource_skipped += 1
                                last_skip_reason = gr
                                continue
                            df = _safe_load(part_files)
                            outcome = _store_frame(candidate, root, part_files, df)
                            if outcome == "cached":
                                cached += 1
                            elif outcome == "budget":
                                budget_skipped += 1
                            df = None
                    # 진행 로그 — 25% 단위 throttle.
                    done = min(total_to_load, start + len(chunk))
                    if total_to_load and done >= _next_progress:
                        _scan_progress(
                            f"[적재] {_prod_label}: {cached}/{total_to_load} 랏 적재"
                            f" · 메모리 총 {root_ram_cache_lot_count()}랏 상주"
                        )
                        _next_progress += _progress_every
                    # 전역 예산 95% 이상이라도 이 제품이 아직 자기 지분(share)을
                    # 못 채웠으면 계속 로드한다 — 삽입 시 지분 초과 제품의 항목이
                    # eviction 되므로 모든 제품이 공정하게 등록된다. 전역이 차고
                    # 자기 지분도 채웠을 때만 남은 후순위 로드를 멈춘다.
                    _source_path_now = str(Path(fp).resolve())
                    with _ROOT_RAM_CACHE_LOCK:
                        total_now = _root_ram_total_bytes_locked()
                        own_now = _root_ram_source_bytes_locked().get(_source_path_now, 0)
                        share_now = _root_ram_source_share_locked(_source_path_now)
                    max_now = _root_ram_cache_max_bytes()
                    if max_now > 0 and total_now >= max_now * 0.95 and own_now >= share_now:
                        budget_stop = True
            rows.append({
                "file": Path(fp).name,
                "ok": True,
                "target_roots": len(roots),
                "cached_roots": cached,
                "missing_roots": missing,
                "priority_roots": len(priority_roots),
                "priority_target_roots": priority_target_roots,
                "step_roots": len(step_roots),
                "step_target_roots": step_target_roots,
                "other_target_roots": other_target_roots,
                "latest_roots": len(latest_roots),
                "searched_roots": len(searched_roots),
                "target_roots_cap": target_roots,
                "resource_skipped_roots": resource_skipped,
                "budget_skipped_roots": budget_skipped,
                "last_skip_reason": last_skip_reason,
                "cache_status": status.get("status") or "",
            })
    now = time.time()
    resource_reason, resource_snapshot = _root_ram_cache_resource_guard_reason()
    with _ROOT_RAM_CACHE_LOCK:
        _ROOT_RAM_STATUS.update({
            "last_refresh_at": datetime.fromtimestamp(now).isoformat(timespec="seconds"),
            "last_refresh_epoch": now,
            "last_error": resource_reason or "",
            "last_resource_guard_reason": resource_reason,
            "resource": resource_snapshot,
            "products": rows,
        })
    # 캐시 이벤트 로그 기록 — 예열 결과
    try:
        from core.cache_event_log import record as _log_event
        _mem_lots_total = root_ram_cache_lot_count()
        for row in rows:
            fname = row.get("file", "?")
            if row.get("ok"):
                _log_event(
                    "warmup",
                    f"예열 완료: {fname} — {row.get('cached_roots', 0)}/{row.get('target_roots', 0)} 랏 적재"
                    + f" · 메모리 총 {_mem_lots_total}랏 상주"
                    + (f" (skip: resource {row.get('resource_skipped_roots', 0)}, budget {row.get('budget_skipped_roots', 0)})"
                       if row.get("resource_skipped_roots") or row.get("budget_skipped_roots") else ""),
                    ok=True,
                    product=fname,
                    detail={"cached": row.get("cached_roots", 0), "target": row.get("target_roots", 0),
                            "missing": row.get("missing_roots", 0),
                            "mem_lots_total": _mem_lots_total,
                            "resource_skipped": row.get("resource_skipped_roots", 0),
                            "budget_skipped": row.get("budget_skipped_roots", 0)},
                )
            elif row.get("build_pending") or row.get("skipped"):
                # 진짜 실패가 아니라 원본 lookup 캐시가 아직 빌드 중 — 빌드 완료 후
                # 다음 사이클에 적재된다. '실패'로 오표시하지 않는다(ok=True, 대기).
                _reason = row.get("reason") or row.get("cache_status") or "빌드 대기"
                _log_event(
                    "warmup",
                    f"예열 대기: {fname} — 원본 lookup 캐시 빌드 중({_reason}), 완료 후 다음 사이클에 적재",
                    ok=True,
                    product=fname,
                    detail={"build_pending": True, "reason": _reason,
                            "mem_lots_total": _mem_lots_total},
                )
            else:
                _log_event("warmup", f"예열 실패: {fname}", ok=False, product=fname)
    except Exception:
        pass
    # 예열 사이클 동안 eviction 으로 참조가 끊긴 프레임의 파이썬측 순환을 즉시
    # 회수한다 — allocator 가 OS 에 돌려주는 것과 별개로, 지연 회수분이 다음
    # 사이클의 로드 스파이크와 겹치는 것을 막는다.
    try:
        import gc
        gc.collect()
    except Exception:
        pass
    # build_pending(원본 lookup 캐시가 아직 빌드 중)은 실패가 아니라 '대기' 상태다.
    # 진짜 실패(ok=False 이면서 build_pending/skipped 아님)가 하나도 없으면 ok=True 로
    # 본다 — 전부 대기여도 '실패'로 표시하지 않는다(빌드 완료 후 다음 사이클에 적재).
    genuine_fail = any(
        (not row.get("ok")) and not (row.get("build_pending") or row.get("skipped"))
        for row in rows
    )
    pending_ct = sum(1 for row in rows if row.get("build_pending"))
    warmed_ct = sum(1 for row in rows if row.get("ok"))
    return {
        "ok": (not genuine_fail),
        "build_pending": pending_ct,
        "warmed_products": warmed_ct,
        "enabled": True,
        "products": rows,
        "interval_minutes": root_ram_cache_refresh_minutes(),
        "step_ids": step_ids,
        "priority_root_prefix": settings["priority_root_prefix"],
        "recent_roots": recent_limit,
        "searched_roots": searched_limit,
        "frequent_roots": searched_limit,
        "target_roots": target_roots,
        "max_gb": round(_root_ram_cache_max_bytes() / (1024 ** 3), 3),
        "cpu_budget_cores": round(_root_ram_cache_cpu_budget_cores(), 3),
        "status": root_ram_cache_status(include_detail=False),
    }


def root_ram_cache_status(fp: Path | None = None, *, include_detail: bool = False) -> dict[str, Any]:
    source_path = str(Path(fp).resolve()) if fp else ""
    with _ROOT_RAM_CACHE_LOCK:
        entries = [
            (key, dict(entry))
            for key, entry in _ROOT_RAM_CACHE.items()
            if not source_path or key[0] == source_path
        ]
        access_count = len([
            1 for key in _ROOT_RAM_ACCESS
            if not source_path or key[0] == source_path
        ])
        status = dict(_ROOT_RAM_STATUS)
    total_bytes = sum(int(entry.get("estimated_bytes") or 0) for _, entry in entries)
    settings = root_ram_cache_settings()
    entry_groups = [str(entry.get("cache_group") or "other") for _key, entry in entries]
    out = {
        "enabled": root_ram_cache_available(),
        "hit_roots": len(entries),
        "stale_roots": len([1 for _key, entry in entries if entry.get("stale")]),
        "priority_hit_roots": len([g for g in entry_groups if g == "priority"]),
        "step_hit_roots": len([group for group in entry_groups if group == "step"]),
        "other_hit_roots": len([group for group in entry_groups if group not in ("step", "priority")]),
        "estimated_mb": round(total_bytes / (1024 * 1024), 3),
        "max_gb": round(_root_ram_cache_max_bytes() / (1024 ** 3), 3) if _root_ram_cache_max_bytes() else 0,
        "cpu_budget_cores": round(_root_ram_cache_cpu_budget_cores(), 3),
        "polars_threads": os.environ.get("POLARS_MAX_THREADS") or os.environ.get("FLOW_POLARS_MAX_THREADS") or "",
        "warm_load_workers": _root_ram_cache_load_workers(),
        "step_ids": settings["step_ids"],
        "priority_root_prefix": settings["priority_root_prefix"],
        "searched_roots": settings["searched_limit"],
        "recent_roots": settings["recent_roots"],
        "frequent_roots": settings["searched_limit"],
        "target_roots": settings["target_roots"],
        "interval_minutes": root_ram_cache_refresh_minutes(),
        "scheduler_started": _ROOT_RAM_STARTED,
        "last_refresh_at": status.get("last_refresh_at") or "",
        "last_error": status.get("last_error") or "",
        "last_resource_guard_reason": status.get("last_resource_guard_reason") or "",
        "access_roots": access_count,
    }
    if include_detail:
        out["resource"] = status.get("resource") or _root_ram_cache_resource_snapshot()
    if include_detail:
        out["roots"] = [
            {
                "source_path": key[0],
                "root_lot_id": key[1],
                "row_count": int(entry.get("row_count") or 0),
                "estimated_mb": round(float(entry.get("estimated_bytes") or 0) / (1024 * 1024), 3),
                "loaded_at": entry.get("loaded_at") or "",
                "access_count": int(entry.get("access_count") or 0),
                "cache_group": str(entry.get("cache_group") or "other"),
                "cache_sources": list(entry.get("cache_sources") or []),
                "stale": bool(entry.get("stale")),
            }
            for key, entry in entries
        ]
        out["products"] = status.get("products") or []
    return out


def _filter_wafer_lf(lf: pl.LazyFrame, wafer_ids: str = "") -> pl.LazyFrame:
    wf_values = [str(w).strip() for w in str(wafer_ids or "").split(",") if str(w).strip()]
    if not wf_values:
        return lf
    cols = lf.collect_schema().names()
    wf_col = _ci_col(cols, "wafer_id", "wf_id", "WAFER_ID", "WF_ID")
    if not wf_col:
        return lf
    forms: set[str] = set()
    for raw in wf_values:
        value = raw.upper().lstrip("#")
        forms.add(value)
        try:
            n = int(value)
            forms.update({str(n), f"{n:02d}", f"W{n}", f"W{n:02d}", f"WF{n}", f"WF{n:02d}"})
        except Exception:
            pass
    return lf.filter(pl.col(wf_col).cast(_STR, strict=False).str.strip_chars().str.to_uppercase().is_in(sorted(forms)))


def _root_ram_cache_loop() -> None:
    global _ROOT_RAM_REFRESH_COUNTER
    # 개발서버는 시작 직후 부하를 피하기 위해 첫 예열을 지연한다.
    if not PATHS.is_prod:
        initial_delay = 120.0  # 2분 지연
        logger.info("ML_TABLE root RAM cache: dev server initial delay %.0fs", initial_delay)
        while initial_delay > 0 and not _ROOT_RAM_STOP.is_set():
            step = min(initial_delay, 30.0)
            _ROOT_RAM_STOP.wait(step)
            initial_delay -= step
    while not _ROOT_RAM_STOP.is_set():
        try:
            refresh_root_lot_ram_cache(force=False)
        except Exception as exc:
            logger.warning("ML_TABLE root RAM cache scheduler tick failed: %s", exc)
            with _ROOT_RAM_CACHE_LOCK:
                _ROOT_RAM_STATUS["last_error"] = f"{type(exc).__name__}: {exc}"
        _ROOT_RAM_REFRESH_COUNTER += 1
        wait_s = max(60.0, root_ram_cache_refresh_minutes() * 60.0)
        while wait_s > 0 and not _ROOT_RAM_STOP.is_set():
            step = min(wait_s, 60.0)
            _ROOT_RAM_STOP.wait(step)
            wait_s -= step


def start_root_lot_ram_cache_scheduler() -> bool:
    global _ROOT_RAM_THREAD, _ROOT_RAM_STARTED
    if _ROOT_RAM_STARTED:
        return False
    try:
        from core.worker_dispatch import server_role

        if server_role() == "worker" and not _env_bool("FLOW_ENABLE_WORKER_RAM_CACHE", False):
            logger.info(
                "ML_TABLE root RAM cache scheduler disabled on worker role "
                "(shared lookup builds remain enabled)"
            )
            return False
    except Exception:
        pass
    if not root_ram_cache_available() or _root_ram_cache_max_bytes() <= 0:
        logger.info("ML_TABLE root RAM cache scheduler disabled")
        return False
    _ROOT_RAM_STOP.clear()
    _ROOT_RAM_THREAD = threading.Thread(
        target=_root_ram_cache_loop,
        name="ml-table-root-ram-cache",
        daemon=True,
    )
    _ROOT_RAM_THREAD.start()
    _ROOT_RAM_STARTED = True
    logger.info("ML_TABLE root RAM cache scheduler started (interval=%sm, is_prod=%s)", root_ram_cache_refresh_minutes(), PATHS.is_prod)
    return True


_CACHE_ROOT_MEMO_LOCK = threading.Lock()
_CACHE_ROOT_MEMO: tuple[float, Path | None] = (0.0, None)


def _cache_root() -> Path:
    """Resolve the runtime-editable DB cache root with a short read TTL.

    ``PATHS.db_cache_dir`` intentionally re-reads the root profile. Calling it
    several times in every SplitTable request costs milliseconds on a shared
    workspace, while a two-second delay for an admin path change is harmless.
    """
    global _CACHE_ROOT_MEMO
    now = time.monotonic()
    cached_at, cached_path = _CACHE_ROOT_MEMO
    if cached_path is not None and now - cached_at <= 2.0:
        return cached_path
    resolved = PATHS.db_cache_dir / LOOKUP_CACHE_DIRNAME
    with _CACHE_ROOT_MEMO_LOCK:
        cached_at, cached_path = _CACHE_ROOT_MEMO
        if cached_path is not None and now - cached_at <= 2.0:
            return cached_path
        _CACHE_ROOT_MEMO = (now, resolved)
        return resolved


def cache_dir_for(fp: Path) -> Path:
    return _cache_root() / _safe_product_token(fp.stem)


def meta_path_for(fp: Path) -> Path:
    return cache_dir_for(fp) / META_FILE


def candidate_index_path_for(fp: Path) -> Path:
    return cache_dir_for(fp) / CANDIDATE_INDEX_FILE


def _read_meta(fp: Path) -> dict[str, Any]:
    meta_fp = meta_path_for(fp)
    key = str(meta_fp)
    now = time.monotonic()
    with _META_CACHE_LOCK:
        cached = _META_CACHE.get(key)
        if cached is not None and now - cached[0] <= _META_CACHE_TTL_SEC:
            return dict(cached[1])
    if not meta_fp.is_file():
        return {}
    try:
        data = json.loads(meta_fp.read_text(encoding="utf-8"))
        out = data if isinstance(data, dict) else {}
    except Exception:
        out = {}
    with _META_CACHE_LOCK:
        _META_CACHE[key] = (now, dict(out))
        if len(_META_CACHE) > _META_CACHE_MAX:
            oldest = min(_META_CACHE, key=lambda item: _META_CACHE[item][0])
            _META_CACHE.pop(oldest, None)
    return out


def _write_meta(fp: Path, meta: dict[str, Any]) -> None:
    meta_fp = meta_path_for(fp)
    meta_fp.parent.mkdir(parents=True, exist_ok=True)
    tmp = meta_fp.with_suffix(meta_fp.suffix + ".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(meta_fp)
    with _META_CACHE_LOCK:
        _META_CACHE[str(meta_fp)] = (time.monotonic(), dict(meta))


def read_candidate_index(fp: Path) -> dict[str, Any]:
    index_fp = candidate_index_path_for(fp)
    if not index_fp.is_file():
        return {}
    try:
        data = json.loads(index_fp.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_candidate_index(fp: Path, index: dict[str, Any]) -> None:
    index_fp = candidate_index_path_for(fp)
    index_fp.parent.mkdir(parents=True, exist_ok=True)
    tmp = index_fp.with_suffix(index_fp.suffix + ".tmp")
    tmp.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(index_fp)


def _build_lock_path(fp: Path) -> Path:
    root = _cache_root()
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{_safe_product_token(Path(fp).stem)}.build.lock"


def _local_pid_alive(pid: int) -> bool:
    """같은 호스트가 남긴 build lock의 프로세스 생존 여부를 보수적으로 확인."""
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    try:
        import psutil  # type: ignore

        return bool(psutil.pid_exists(pid))
    except Exception:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
        except Exception:
            # 권한 등으로 확인할 수 없으면 활성 lock으로 간주한다.
            return True


def _try_acquire_build_lock(fp: Path) -> tuple[int | None, Path, str]:
    lock_fp = _build_lock_path(fp)
    owner_id = f"{socket.gethostname()}:{os.getpid()}"
    payload = json.dumps({
        "owner": owner_id,
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "started_at": _utc_now(),
        "source_path": str(Path(fp).resolve()),
    }, ensure_ascii=False)
    for attempt in range(2):
        try:
            fd = os.open(str(lock_fp), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, payload.encode("utf-8"))
            return fd, lock_fp, ""
        except FileExistsError:
            try:
                age = time.time() - lock_fp.stat().st_mtime
            except Exception:
                age = 0.0
            try:
                owner_meta = json.loads(lock_fp.read_text(encoding="utf-8"))
                if not isinstance(owner_meta, dict):
                    owner_meta = {}
            except Exception:
                owner_meta = {}
            # 워커가 OOM/종료되면 O_EXCL lock 파일만 남고 30분 동안 api의
            # 폴백까지 막혔다. 마지막 heartbeat의 정확한 owner와 일치하고 그
            # heartbeat가 stale인 경우에만 즉시 고아 lock으로 회수한다.
            orphaned_worker_lock = False
            lock_owner_id = str(owner_meta.get("owner") or "")
            lock_host = str(owner_meta.get("host") or "")
            try:
                lock_pid = int(owner_meta.get("pid") or 0)
            except Exception:
                lock_pid = 0
            # worker 재시작 후 heartbeat owner가 새 PID로 바뀐 경우에도, 같은
            # 머신의 새 worker는 이전 PID를 검사해 OOM 고아 lock을 즉시 회수한다.
            orphaned_local_process_lock = bool(
                lock_host
                and lock_host.casefold() == socket.gethostname().casefold()
                and lock_pid > 0
                and lock_pid != os.getpid()
                and not _local_pid_alive(lock_pid)
            )
            if lock_owner_id and lock_owner_id != owner_id:
                try:
                    from core import worker_dispatch as _wd

                    hb = _wd.heartbeat_meta(fresh_read=True)
                    hb_owner = str(hb.get("owner") or "")
                    orphaned_worker_lock = bool(
                        hb_owner
                        and lock_owner_id == hb_owner
                        and not _wd.worker_alive(fresh_read=True)
                    )
                except Exception:
                    orphaned_worker_lock = False
            if attempt == 0 and (
                age > BUILD_LOCK_STALE_SECONDS
                or orphaned_worker_lock
                or orphaned_local_process_lock
            ):
                try:
                    lock_fp.unlink()
                    if orphaned_worker_lock or orphaned_local_process_lock:
                        logger.warning(
                            "reclaimed orphaned worker lookup-build lock source=%s owner=%s",
                            fp, lock_owner_id,
                        )
                    continue
                except Exception:
                    pass
            try:
                owner = json.dumps(owner_meta, ensure_ascii=False)[:1000] if owner_meta else lock_fp.read_text(encoding="utf-8")[:1000]
            except Exception:
                owner = ""
            return None, lock_fp, owner
    return None, lock_fp, ""


def _release_build_lock(fd: int | None, lock_fp: Path) -> None:
    if fd is not None:
        try:
            os.close(fd)
        except Exception:
            pass
    try:
        lock_fp.unlink(missing_ok=True)
    except Exception:
        pass


def _normalize_product(product: str) -> str:
    raw = str(product or "").strip()
    if not raw:
        return ""
    if raw.lower().endswith(".parquet"):
        raw = Path(raw).stem
    if raw.upper().startswith("ML_TABLE_"):
        return raw
    return f"ML_TABLE_{raw}"


def _candidate_names(product: str) -> list[str]:
    raw = str(product or "").strip()
    norm = _normalize_product(raw)
    names: list[str] = []
    for item in (raw, norm):
        if not item:
            continue
        stem = Path(item).stem
        for name in (item, stem, f"{stem}.parquet"):
            if name and name not in names:
                names.append(name)
    return names


def _find_case_insensitive_file(root: Path, names: list[str]) -> Path | None:
    if not root.is_dir():
        return None
    folded = {n.casefold() for n in names if n}
    for name in names:
        cand = (root / name).resolve()
        try:
            cand.relative_to(root.resolve())
        except ValueError:
            continue
        if cand.is_file() and cand.suffix.lower() == ".parquet":
            return cand
    try:
        for fp in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            if fp.is_file() and fp.suffix.lower() == ".parquet" and fp.name.casefold() in folded:
                return fp
            if fp.is_file() and fp.suffix.lower() == ".parquet" and fp.stem.casefold() in folded:
                return fp
    except Exception:
        return None
    return None


def resolve_ml_table_file(product: str = "", file: str = "") -> Path | None:
    """Resolve an ML_TABLE parquet under the configured DB/base roots."""
    raw_file = str(file or "").strip()
    roots = [PATHS.base_root, PATHS.db_root]
    seen_roots: set[str] = set()
    unique_roots: list[Path] = []
    for root in roots:
        key = str(root.resolve()) if root else ""
        if key and key not in seen_roots:
            unique_roots.append(root)
            seen_roots.add(key)
    if raw_file:
        rel = Path(raw_file)
        if rel.is_absolute() or ".." in rel.parts:
            return None
        names = [str(rel)]
        if rel.suffix.lower() != ".parquet":
            names.append(f"{rel}.parquet")
        for root in unique_roots:
            found = _find_case_insensitive_file(root, names)
            if found and found.stem.upper().startswith("ML_TABLE_"):
                return found
        return None
    names = _candidate_names(product)
    for root in unique_roots:
        found = _find_case_insensitive_file(root, names)
        if found and found.stem.upper().startswith("ML_TABLE_"):
            return found
    return None


def _job_snapshot() -> dict[str, Any]:
    with _BUILD_LOCK:
        state = dict(_BUILD_STATE)
        state["queued"] = list(_BUILD_QUEUE)
    state["queued"] = [str(p) for p in state.get("queued") or []]
    return state


def build_queue_snapshot() -> dict[str, Any]:
    """Return the lookup build queue without exposing mutable internal state."""
    return _job_snapshot()


def _job_status_for(fp: Path) -> str:
    target = str(fp.resolve())
    snap = _job_snapshot()
    if snap.get("running") and str(snap.get("current") or "") == target:
        return "running"
    if target in [str(p) for p in snap.get("queued") or []]:
        return "queued"
    return ""


def _partition_files(cache_dir: Path, root_lot_id: str = "") -> list[Path]:
    if not cache_dir.is_dir():
        return []
    if root_lot_id:
        part_dir = cache_dir / f"root_lot_id={root_lot_id}"
        return sorted(part_dir.glob("*.parquet")) if part_dir.is_dir() else []
    return sorted(p for p in cache_dir.rglob("*.parquet") if p.name != META_FILE)


def _root_lot_ids_from_cache_dir(cache_dir: Path) -> list[str]:
    if not cache_dir.is_dir():
        return []
    roots: dict[str, str] = {}
    try:
        for path in cache_dir.iterdir():
            if not path.is_dir() or not path.name.startswith("root_lot_id="):
                continue
            root = path.name.split("=", 1)[1].strip()
            if not root:
                continue
            roots.setdefault(root.upper(), root)
    except Exception:
        return []
    return [roots[key] for key in sorted(roots)]


def _columns_by_candidate_prefix(columns: list[str]) -> dict[str, list[str]]:
    grouped = {prefix: [] for prefix in CANDIDATE_COLUMN_PREFIXES}
    for col in columns:
        text = str(col or "").strip()
        if not text:
            continue
        upper = text.upper()
        for prefix in CANDIDATE_COLUMN_PREFIXES:
            if upper.startswith(f"{prefix}_"):
                grouped[prefix].append(text)
                break
    return {prefix: sorted(dict.fromkeys(values), key=lambda s: str(s).upper()) for prefix, values in grouped.items()}


def _candidate_index_source_stale(index: dict[str, Any], fp: Path) -> bool:
    if not index:
        return True
    try:
        sig = _source_sig(fp)
    except Exception:
        return True
    return (
        str(index.get("source_path") or "") != str(sig["source_path"])
        or float(index.get("source_mtime") or 0) != float(sig["source_mtime"])
        or int(index.get("source_size") or -1) != int(sig["source_size"])
    )


def _candidate_index_summary(fp: Path, index: dict[str, Any]) -> dict[str, Any]:
    columns_by_prefix = index.get("columns_by_prefix") if isinstance(index.get("columns_by_prefix"), dict) else {}
    return {
        "has_index": bool(index),
        "path": str(candidate_index_path_for(fp)),
        "version": int(index.get("version") or 0) if index else CANDIDATE_INDEX_VERSION,
        "root_lot_id_count": int(index.get("root_lot_id_count") or len(index.get("root_lot_ids") or [])) if index else 0,
        "default_prefix": str(index.get("default_prefix") or "KNOB") if index else "KNOB",
        "knob_column_count": len(columns_by_prefix.get("KNOB") or []),
    }


def _build_candidate_index_from_cache(fp: Path, cache_dir: Path, final_cols: list[str]) -> dict[str, Any]:
    columns_by_prefix = _columns_by_candidate_prefix(final_cols)
    default_prefix = "KNOB"
    if not columns_by_prefix.get(default_prefix):
        default_prefix = next((prefix for prefix, values in columns_by_prefix.items() if values), "KNOB")
    roots = _root_lot_ids_from_cache_dir(cache_dir)
    return {
        "version": CANDIDATE_INDEX_VERSION,
        **_source_sig(fp),
        "built_at": _utc_now(),
        "root_lot_ids": roots,
        "root_lot_id_count": len(roots),
        "columns_by_prefix": columns_by_prefix,
        "default_prefix": default_prefix,
    }


def _root_lot_candidates_from_index(index: dict[str, Any], prefix: str = "", limit: int = 500) -> list[str]:
    try:
        limit = max(1, int(limit or 500))
    except Exception:
        limit = 500
    needle = str(prefix or "").strip().upper()
    out: list[str] = []
    seen: set[str] = set()
    for value in index.get("root_lot_ids") or []:
        root = str(value or "").strip()
        if not root:
            continue
        root_upper = root.upper()
        if needle and needle not in root_upper:
            continue
        if root_upper in seen:
            continue
        seen.add(root_upper)
        out.append(root)
        if len(out) >= limit:
            break
    return out


def _meta_source_stale(meta: dict[str, Any], fp: Path) -> bool:
    if not meta:
        return False
    try:
        sig = _source_sig(fp)
    except Exception:
        return True
    return (
        str(meta.get("source_path") or "") != str(sig["source_path"])
        or float(meta.get("source_mtime") or 0) != float(sig["source_mtime"])
        or int(meta.get("source_size") or -1) != int(sig["source_size"])
    )


def cache_status(fp: Path) -> dict[str, Any]:
    fp = Path(fp)
    cdir = cache_dir_for(fp)
    meta = _read_meta(fp)
    # _meta.json은 전체 파티션 쓰기가 성공한 뒤 마지막에 원자 기록된다. 따라서
    # 상태 확인마다 수천 개 root 디렉터리를 rglob 할 필요가 없다. 기존 코드는
    # 첫 검색마다 전체 lookup tree를 순회해 root 수에 비례해 느려졌다.
    has_cache = bool(meta and cdir.is_dir())
    stale = _meta_source_stale(meta, fp) if has_cache else False
    job = _job_status_for(fp)
    status = "fresh" if has_cache and not stale else ("stale" if has_cache and stale else "missing")
    if job and not (has_cache and not stale):
        status = job
    return {
        "ok": True,
        "status": status,
        "cache_dir": str(cdir),
        "meta_path": str(meta_path_for(fp)),
        "has_cache": has_cache,
        "source_stale": stale,
        "job_status": job,
        "meta": meta,
    }


def root_lot_candidates_from_lookup_cache(fp: Path, prefix: str = "", limit: int = 500) -> dict[str, Any]:
    """Return root_lot_id candidates from hive partition names when cache is fresh."""
    try:
        limit = max(1, int(limit or 500))
    except Exception:
        limit = 500
    fp = Path(fp)
    cdir = cache_dir_for(fp)
    meta = _read_meta(fp)
    has_candidate_cache = bool(meta and cdir.is_dir())
    source_stale = _meta_source_stale(meta, fp) if has_candidate_cache else False
    job_status = _job_status_for(fp)
    status_text = "fresh" if has_candidate_cache and not source_stale else ("stale" if has_candidate_cache and source_stale else "missing")
    if job_status and status_text != "fresh":
        status_text = job_status
    out = {
        "ok": True,
        "status": status_text,
        "has_cache": has_candidate_cache,
        "source_stale": source_stale,
        "job_status": job_status,
        "root_lot_id_count": int(meta.get("root_lot_id_count") or 0),
        "candidate_index": False,
        "meta": meta,
        "candidates": [],
    }
    if has_candidate_cache and not source_stale:
        index = read_candidate_index(fp)
        if index and not _candidate_index_source_stale(index, fp):
            out["candidate_index"] = True
            out["root_lot_id_count"] = int(index.get("root_lot_id_count") or len(index.get("root_lot_ids") or []))
            out["candidate_index_meta"] = _candidate_index_summary(fp, index)
            out["candidates"] = _root_lot_candidates_from_index(index, prefix=prefix, limit=limit)
            return out

    status = cache_status(fp)
    meta = status.get("meta") or {}
    out = {
        "ok": True,
        "status": status.get("status") or "",
        "has_cache": bool(status.get("has_cache")),
        "source_stale": bool(status.get("source_stale")),
        "job_status": status.get("job_status") or "",
        "root_lot_id_count": int(meta.get("root_lot_id_count") or 0),
        "candidate_index": False,
        "meta": meta,
        "candidates": [],
    }
    if not status.get("has_cache") or status.get("source_stale"):
        return out
    needle = str(prefix or "").strip().upper()
    roots: list[str] = []
    try:
        for path in cache_dir_for(fp).iterdir():
            if not path.is_dir() or not path.name.startswith("root_lot_id="):
                continue
            root = path.name.split("=", 1)[1].strip()
            if not root:
                continue
            if needle and needle not in root.upper():
                continue
            roots.append(root)
    except Exception as exc:
        out.update({"ok": False, "error": str(exc), "candidates": []})
        return out
    out["candidates"] = sorted(dict.fromkeys(roots), key=lambda s: str(s).upper())[:limit]
    return out


def _scan_schema(fp: Path) -> tuple[list[str], dict[str, str]]:
    schema_obj = pl.scan_parquet(str(fp)).collect_schema()
    cols = list(schema_obj.names())
    return cols, {c: str(schema_obj[c]) for c in cols}


def _ci_col(columns: list[str], *names: str) -> str:
    folded = {str(c).casefold(): str(c) for c in columns}
    for name in names:
        hit = folded.get(str(name).casefold())
        if hit:
            return hit
    return ""


def _normalize_root_expr(root_col: str) -> pl.Expr:
    return pl.col(root_col).cast(_STR, strict=False).str.strip_chars().str.to_uppercase()


def _lookup_cache_memory_wait_seconds() -> float:
    return _env_float("FLOW_ML_TABLE_LOOKUP_CACHE_MEMORY_WAIT_SECONDS", LOOKUP_CACHE_MEMORY_WAIT_SECONDS_DEFAULT, 0.0, 300.0)


def _lookup_cache_partition_max_rows() -> int:
    return _env_int(
        "FLOW_ML_TABLE_LOOKUP_CACHE_MAX_ROWS_PER_FILE",
        LOOKUP_CACHE_PARTITION_MAX_ROWS_DEFAULT,
        10_000,
        2_000_000,
    )


def _lookup_cache_build_chunk_size() -> int:
    # 개발서버는 청크를 작게 — 대용량 제품 빌드도 청크당 collect 메모리를 낮춰
    # 순간 스파이크를 억제(느려도 OOM 방지 우선).
    default = LOOKUP_CACHE_BUILD_CHUNK_SIZE_DEFAULT
    if _root_ram_cache_use_dev():
        default = min(default, LOOKUP_CACHE_BUILD_CHUNK_SIZE_DEV_DEFAULT)
    return _env_int(
        "FLOW_ML_TABLE_LOOKUP_CACHE_BUILD_CHUNK_SIZE",
        default,
        1,
        500,
    )


def _wait_for_lookup_cache_memory(fp: Path) -> bool:
    """메모리 여유가 생길 때까지 대기. 상한 초과 시 False — 이번 빌드는 건너뛴다.

    상한 없이 돌면 지속적인 메모리 압박에서 워커 스레드가 영원히 잠들어
    lookup 캐시가 재생성되지 않는다. 건너뛰어도 다음 enqueue 에서 재시도된다.
    """
    max_wait = _env_float(
        "FLOW_ML_TABLE_LOOKUP_CACHE_MEMORY_MAX_WAIT_SECONDS", 1800.0, 0.0, 21600.0
    )
    waited = 0.0
    while process_memory_high():
        if max_wait > 0 and waited >= max_wait:
            with _BUILD_LOCK:
                _BUILD_STATE["paused"] = False
                _BUILD_STATE["pause_reason"] = "memory_wait_timeout"
                _BUILD_STATE["resource_snapshot"] = {}
            logger.warning(
                "ML_TABLE lookup cache build skipped after %.0fs memory-guard wait source=%s",
                waited, fp,
            )
            return False
        snapshot = process_memory_snapshot()
        with _BUILD_LOCK:
            _BUILD_STATE["paused"] = True
            _BUILD_STATE["pause_reason"] = "memory_high"
            _BUILD_STATE["resource_snapshot"] = snapshot
        logger.info("ML_TABLE lookup cache build paused by memory guard source=%s snapshot=%s", fp, snapshot)
        step = _lookup_cache_memory_wait_seconds()
        time.sleep(step)
        waited += max(step, 0.1)
    with _BUILD_LOCK:
        _BUILD_STATE["paused"] = False
        _BUILD_STATE["pause_reason"] = ""
        _BUILD_STATE["resource_snapshot"] = {}
    return True


def _sink_lookup_cache_partitions(lf: pl.LazyFrame, tmp_dir: Path) -> None:
    sink_target = pl.PartitionBy(
        tmp_dir,
        key="root_lot_id",
        include_key=True,
        max_rows_per_file=_lookup_cache_partition_max_rows(),
        approximate_bytes_per_file="auto",
    )
    lf.sink_parquet(sink_target, mkdir=True, maintain_order=False)


def _sink_lookup_cache_partitions_chunked(
    fp: Path, lf_base: pl.LazyFrame, tmp_dir: Path,
) -> None:
    """root_lot_id 단위 청크로 파티션 작성 — OOM 방지.

    전체 파일을 한꺼번에 sink_parquet(PartitionBy) 하면 메모리가 급증해
    8 GB 급 서버에서 OOM 이 발생했다. 대신:
      1. unique root_lot_id 목록을 먼저 추출 (컬럼 1개만 스캔)
      2. CHUNK_SIZE 개씩 묶어서 필터 → collect → 파티션 파일 쓰기
      3. 매 청크 완료 후 gc.collect() + 메모리 체크
    최대 메모리 사용량이 (전체 / 청크 수) 수준으로 제한된다.
    """
    import gc
    from core.parquet_perf import collect_streaming

    chunk_size = _lookup_cache_build_chunk_size()
    max_rows = _lookup_cache_partition_max_rows()

    # ① unique root_lot_id 추출 (컬럼 하나만 스캔 — 메모리 부담 최소)
    root_lot_ids = (
        collect_streaming(lf_base.select("root_lot_id").unique())["root_lot_id"]
        .to_list()
    )
    root_lot_ids = sorted(set(str(r) for r in root_lot_ids if r))
    total = len(root_lot_ids)
    logger.info(
        "ML_TABLE lookup cache chunked build: %d root_lot_ids (chunk_size=%d), source=%s",
        total, chunk_size, fp,
    )

    # 캐시 이벤트 로그(관리 화면)에도 랏 단위 진행을 표시한다 — 기존엔 logger.info(터미널)
    # 로만 찍혀 수동스캔 3/3 화면에서 lookup 빌드 진행이 안 보였다(FAB 처럼 보이게).
    try:
        from core.cache_event_log import record as _cache_log
    except Exception:
        _cache_log = None
    _prod_lbl = _safe_product_token(Path(fp).stem) or Path(fp).name

    def _emit(msg: str, ok: bool = True) -> None:
        if _cache_log is None:
            return
        try:
            _cache_log("cache_op", msg, ok=ok, product=_prod_lbl)
        except Exception:
            pass

    def _rss_gb() -> float:
        try:
            return round(float(process_memory_snapshot().get("process_rss_gb") or 0.0), 2)
        except Exception:
            return 0.0

    def _fmt_dur(sec: float) -> str:
        sec = int(max(0, sec))
        if sec < 60:
            return f"{sec}초"
        if sec < 3600:
            return f"{sec // 60}분 {sec % 60}초"
        return f"{sec // 3600}시간 {(sec % 3600) // 60}분"

    _bld_started = time.monotonic()
    _last_emit = 0.0
    _emit(f"[랏캐시빌드] {_prod_lbl}: {total:,} 랏 → 청크 {chunk_size}개씩 파티션 생성 시작 · RSS {_rss_gb()}GB")

    # ② 청크 단위 처리
    for chunk_start in range(0, total, chunk_size):
        chunk_roots = root_lot_ids[chunk_start : chunk_start + chunk_size]

        # 메모리 압박 시 대기 — 타임아웃이면 빌드 중단
        if process_memory_high():
            gc.collect()
            if not _wait_for_lookup_cache_memory(fp):
                raise RuntimeError(
                    f"memory guard timeout during chunked cache build "
                    f"({chunk_start}/{total})"
                )

        # 이 청크의 root 만 필터 → collect. 스트리밍 엔진으로 읽어 파일 전체가
        # 한꺼번에 디컴프레션되며 peak RAM 이 치솟는 것을 막는다(정렬 안 된 ML_TABLE
        # 에서 is_in 필터는 row-group skip 이 안 돼 in-memory collect 는 파일 전체를
        # 메모리에 올린다). 스트리밍은 morsel 단위로 처리 → peak = 청크 결과 크기 수준.
        chunk_df = collect_streaming(
            lf_base.filter(pl.col("root_lot_id").is_in(chunk_roots))
        )

        # 각 root_lot_id 별 파티션 디렉토리에 쓰기
        for root in chunk_roots:
            root_df = chunk_df.filter(pl.col("root_lot_id") == root)
            if root_df.height == 0:
                continue
            part_dir = tmp_dir / f"root_lot_id={root}"
            part_dir.mkdir(parents=True, exist_ok=True)
            for file_idx, row_start in enumerate(range(0, root_df.height, max_rows)):
                part = root_df.slice(row_start, max_rows)
                out_path = part_dir / f"{file_idx:04d}.parquet"
                part.write_parquet(str(out_path))

        del chunk_df
        gc.collect()

        done = min(chunk_start + chunk_size, total)
        logger.info(
            "ML_TABLE lookup cache build: %d/%d root_lot_ids written",
            done, total,
        )
        # 첫·마지막 청크는 항상, 그 외엔 ~2초 간격 스로틀로 이벤트 로그에 진행 표시.
        _now = time.monotonic()
        if done >= total or (_now - _last_emit) >= 2.0:
            _last_emit = _now
            _elapsed = _now - _bld_started
            _eta = (_elapsed / done) * (total - done) if done else 0.0
            _pct = done * 100 // total if total else 100
            _emit(f"[랏캐시빌드] {_prod_lbl}: {done:,}/{total:,} 랏 ({_pct}%)"
                  f" · RSS {_rss_gb()}GB · 남은 ~{_fmt_dur(_eta)}")


def _parquet_row_count(fp: Path) -> int:
    try:
        import pyarrow.parquet as pq  # type: ignore

        return int(pq.ParquetFile(str(fp)).metadata.num_rows)
    except Exception:
        return int(pl.read_parquet(str(fp), columns=[]).height)


def _lookup_cache_written_stats(cache_dir: Path) -> tuple[int, int]:
    row_count = 0
    roots: set[str] = set()
    for fp in _partition_files(cache_dir):
        row_count += _parquet_row_count(fp)
        for part in fp.parts:
            if str(part).startswith("root_lot_id="):
                root = str(part).split("=", 1)[1].strip()
                if root:
                    roots.add(root.upper())
                break
    return row_count, len(roots)


def _build_lookup_cache(fp: Path) -> dict[str, Any]:
    fp = Path(fp).resolve()
    started = time.monotonic()
    _prod_lbl = _safe_product_token(Path(fp).stem) or Path(fp).name
    # 오프로드된 개발서버(worker)에서 빌드해도 공유 로그로 운영 화면에 뜨도록 기록.
    try:
        from core.cache_event_log import record as _cache_log
        _cache_log("build", f"lookup 캐시 빌드 시작: {_prod_lbl}", product=_prod_lbl)
    except Exception:
        pass
    cdir = cache_dir_for(fp)
    tmp_dir = cdir.with_name(cdir.name + ".tmp")
    cols, schema = _scan_schema(fp)
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    if not root_col:
        raise MlTableLookupError("missing_root_lot_id", "ML_TABLE에 root_lot_id 컬럼이 없습니다.", columns=cols[:80])
    lf = pl.scan_parquet(str(fp))
    if root_col != "root_lot_id":
        if "root_lot_id" in cols:
            lf = lf.with_columns(_normalize_root_expr(root_col).alias("root_lot_id"))
        else:
            lf = lf.rename({root_col: "root_lot_id"}).with_columns(_normalize_root_expr("root_lot_id").alias("root_lot_id"))
    else:
        lf = lf.with_columns(_normalize_root_expr("root_lot_id").alias("root_lot_id"))
    lf = lf.filter(pl.col("root_lot_id").is_not_null() & (pl.col("root_lot_id") != ""))
    final_schema_obj = lf.collect_schema()
    final_cols = list(final_schema_obj.names())
    final_schema = {c: str(final_schema_obj[c]) for c in final_cols}
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        _sink_lookup_cache_partitions_chunked(fp, lf, tmp_dir)
    except Exception:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        raise
    if cdir.exists():
        shutil.rmtree(cdir)
    tmp_dir.replace(cdir)
    row_count, root_count = _lookup_cache_written_stats(cdir)
    candidate_index = _build_candidate_index_from_cache(fp, cdir, final_cols)
    _write_candidate_index(fp, candidate_index)
    meta = {
        "version": CACHE_VERSION,
        **_source_sig(fp),
        "row_count": row_count,
        "total_cols": len(final_cols),
        "root_lot_id_count": root_count,
        "root_col": "root_lot_id",
        "original_root_col": root_col,
        "schema": final_schema,
        "original_schema": schema,
        "identity_columns": identity_columns(final_cols),
        "candidate_index": _candidate_index_summary(fp, candidate_index),
        "built_at": _utc_now(),
        "build_seconds": round(time.monotonic() - started, 3),
    }
    _write_meta(fp, meta)
    try:
        from core.cache_event_log import record as _cache_log
        _cache_log("build", f"lookup 캐시 빌드 완료: {_prod_lbl} — {root_count} roots, {meta['build_seconds']}s",
                   product=_prod_lbl, detail={"roots": root_count, "rows": row_count, "seconds": meta["build_seconds"]})
    except Exception:
        pass
    return {"ok": True, "cache_dir": str(cdir), "meta": meta}


def build_lookup_cache(fp: Path, *, force: bool = False) -> dict[str, Any]:
    status = cache_status(fp)
    if not force and status.get("status") == "fresh":
        return {"ok": True, "skipped": True, "cache_dir": status.get("cache_dir"), "meta": status.get("meta") or {}}
    lock_fd, lock_fp, lock_owner = _try_acquire_build_lock(fp)
    if lock_fd is None:
        status = cache_status(fp)
        if status.get("status") == "fresh":
            return {"ok": True, "skipped": True, "cache_dir": status.get("cache_dir"), "meta": status.get("meta") or {}}
        return {
            "ok": True,
            "skipped": True,
            "reason": "build_lock_held",
            "lock_path": str(lock_fp),
            "lock_owner": lock_owner,
            "cache_status": status.get("status") or "",
            "cache_dir": status.get("cache_dir") or "",
            "meta": status.get("meta") or {},
        }
    try:
        status = cache_status(fp)
        if not force and status.get("status") == "fresh":
            return {"ok": True, "skipped": True, "cache_dir": status.get("cache_dir"), "meta": status.get("meta") or {}}
        return _build_lookup_cache(fp)
    finally:
        _release_build_lock(lock_fd, lock_fp)


_BUILD_COMPLETE_HOOKS: list = []


def register_build_complete_hook(fn) -> None:
    """lookup 캐시 빌드 완료 시 호출할 콜백 등록 (예: SplitTable view payload
    캐시 무효화). 순환 import 를 피하려고 소비 모듈(splittable router)이 등록한다."""
    if callable(fn) and fn not in _BUILD_COMPLETE_HOOKS:
        _BUILD_COMPLETE_HOOKS.append(fn)


def _run_build_complete_hooks(fp: Path) -> None:
    for fn in list(_BUILD_COMPLETE_HOOKS):
        try:
            fn(fp)
        except Exception:
            logger.debug("ml_table_lookup build-complete hook failed", exc_info=True)


def _cancel_build_retry(fp: Path) -> None:
    key = str(Path(fp).resolve())
    with _BUILD_LOCK:
        timer = _BUILD_RETRY_TIMERS.pop(key, None)
        _BUILD_RETRY_COUNTS.pop(key, None)
    if timer is not None:
        try:
            timer.cancel()
        except Exception:
            pass


def _schedule_build_retry(fp: Path) -> bool:
    """짧은 지연 뒤 실패한 lookup build를 제한 횟수만큼 재등록한다.

    워커가 죽은 직후 로컬 폴백이 고아 lock/일시 메모리 압박에 막혀도 30분짜리
    RAM scheduler 다음 tick까지 제품이 비지 않게 한다. 같은 source timer는 하나만
    유지하고, 연속 자동 재시도는 기본 3회로 제한한다.
    """
    path = Path(fp).resolve()
    key = str(path)
    max_retries = _env_int(
        "FLOW_ML_TABLE_LOOKUP_CACHE_BUILD_RETRY_MAX",
        LOOKUP_CACHE_BUILD_RETRY_MAX_DEFAULT,
        0,
        20,
    )
    delay = _env_float(
        "FLOW_ML_TABLE_LOOKUP_CACHE_BUILD_RETRY_SECONDS",
        LOOKUP_CACHE_BUILD_RETRY_SECONDS_DEFAULT,
        1.0,
        3600.0,
    )
    with _BUILD_LOCK:
        current = _BUILD_RETRY_TIMERS.get(key)
        if current is not None and current.is_alive():
            return True
        count = int(_BUILD_RETRY_COUNTS.get(key) or 0)
        if count >= max_retries:
            return False
        _BUILD_RETRY_COUNTS[key] = count + 1

        def _retry() -> None:
            with _BUILD_LOCK:
                _BUILD_RETRY_TIMERS.pop(key, None)
            enqueue_build(path)

        timer = threading.Timer(delay, _retry)
        timer.daemon = True
        timer.name = "ml-table-lookup-retry"
        _BUILD_RETRY_TIMERS[key] = timer
        timer.start()
    return True


def _warm_root_ram_after_lookup_build(fp: Path) -> None:
    """공유 lookup 산출물을 만든 뒤 api/standalone의 로컬 RAM을 즉시 예열."""
    try:
        from core.worker_dispatch import server_role

        if server_role() == "worker":
            return
    except Exception:
        pass
    try:
        refresh_root_lot_ram_cache(product=Path(fp).stem, force=False)
    except Exception as exc:
        logger.warning("root RAM warm after lookup build failed source=%s: %s", fp, exc)


def _worker_loop() -> None:
    while True:
        with _BUILD_LOCK:
            if not _BUILD_QUEUE:
                _BUILD_STATE["running"] = False
                _BUILD_STATE["paused"] = False
                _BUILD_STATE["pause_reason"] = ""
                _BUILD_STATE["resource_snapshot"] = {}
                _BUILD_STATE["current"] = ""
                return
            fp = _BUILD_QUEUE.popleft()
            _BUILD_STATE["running"] = True
            _BUILD_STATE["paused"] = False
            _BUILD_STATE["pause_reason"] = ""
            _BUILD_STATE["resource_snapshot"] = {}
            _BUILD_STATE["current"] = str(fp.resolve())
            _BUILD_STATE["started_at"] = _utc_now()
            _BUILD_STATE["last_error"] = ""
        try:
            def _local_build() -> dict:
                # 로컬 실행일 때만 메모리 대기 — 오프로드되면 이 서버 메모리를
                # 쓰지 않으므로 대기 없이 워커가 바로 빌드한다.
                if cache_status(fp).get("status") != "fresh" and not _wait_for_lookup_cache_memory(fp):
                    return {"ok": False, "error": "memory_wait_timeout"}
                return build_lookup_cache(fp, force=False)

            # v9.4.x: 개발서버(워커) 생존 시 파티션 빌드를 오프로드 — 산출물은
            # 공유 db cache 파티션 트리라 어느 서버가 빌드해도 동일하게 읽힌다.
            # 워커 다운/타임아웃이면 로컬 폴백 (core.worker_dispatch.run_heavy).
            from core import worker_dispatch as _wd
            res = _wd.run_heavy(
                "ml_lookup_cache_build",
                {
                    "product": fp.stem,
                    "file": fp.name,
                    # 구버전 worker 호환용. 새 worker는 logical identifier를 우선한다.
                    "source_path": str(fp.resolve()),
                },
                _local_build,
                label=f"ml_lookup:{fp.stem}",
                local_idle_only=True,
            ) or {}
            fresh_after = cache_status(fp).get("status") == "fresh"
            if not fresh_after:
                reason = str(
                    res.get("error")
                    or res.get("reason")
                    or cache_status(fp).get("status")
                    or "lookup_build_not_fresh"
                )
                with _BUILD_LOCK:
                    _BUILD_STATE["last_error"] = reason
                    _BUILD_STATE["last_source"] = str(fp.resolve())
                    _BUILD_STATE["finished_at"] = _utc_now()
                if _schedule_build_retry(fp):
                    logger.warning(
                        "ML_TABLE lookup cache not fresh after build; retry scheduled source=%s reason=%s",
                        fp, reason,
                    )
                continue
            _cancel_build_retry(fp)
            with _BUILD_LOCK:
                _BUILD_STATE["last_error"] = ""
                _BUILD_STATE["last_source"] = str(fp.resolve())
                _BUILD_STATE["finished_at"] = _utc_now()
            # 빌드 완료 → 소비 캐시(SplitTable view payload) 무효화 훅 실행.
            # 이렇게 해야 stale 파티션으로 렌더한 payload 가 fresh 데이터로 수렴.
            _run_build_complete_hooks(fp)
            # lookup은 공유 디스크 산출물이지만 root RAM은 프로세스 로컬이다.
            # remote build 직후 api가 자기 RAM을 채워야 "빌드는 됐는데 제품별 lot
            # 캐시는 다음 30분 tick까지 0"인 공백이 생기지 않는다.
            _warm_root_ram_after_lookup_build(fp)
        except Exception as exc:
            logger.warning("ML_TABLE lookup cache build failed source=%s: %s", fp, exc, exc_info=True)
            with _BUILD_LOCK:
                _BUILD_STATE["last_error"] = str(exc)
                _BUILD_STATE["last_source"] = str(fp.resolve())
                _BUILD_STATE["finished_at"] = _utc_now()
                _BUILD_STATE["paused"] = False
                _BUILD_STATE["pause_reason"] = ""
                _BUILD_STATE["resource_snapshot"] = {}


def enqueue_build(fp: Path) -> dict[str, Any]:
    fp = Path(fp).resolve()
    with _BUILD_LOCK:
        queued_paths = {str(p.resolve()) for p in _BUILD_QUEUE}
        current = str(_BUILD_STATE.get("current") or "")
        target = str(fp)
        if target != current and target not in queued_paths:
            _BUILD_QUEUE.append(fp)
        global _BUILD_THREAD
        if _BUILD_THREAD is None or not _BUILD_THREAD.is_alive():
            _BUILD_THREAD = threading.Thread(target=_worker_loop, name="ml-table-lookup-build", daemon=True)
            _BUILD_THREAD.start()
        status = "running" if current == target and _BUILD_STATE.get("running") else "queued"
        return {"ok": True, "status": status, "queued": [str(p) for p in _BUILD_QUEUE], "current": _BUILD_STATE.get("current") or ""}


def identity_columns(columns: list[str] | tuple[str, ...]) -> list[str]:
    lookup = {str(c).casefold(): str(c) for c in (columns or [])}
    out: list[str] = []
    for name in IDENTITY_COLUMN_CANDIDATES:
        hit = lookup.get(name.casefold())
        if hit and hit not in out:
            out.append(hit)
    return out


def _parse_select_cols(select_cols: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if select_cols is None:
        return []
    if isinstance(select_cols, str):
        return [c.strip() for c in select_cols.split(",") if c.strip()]
    return [str(c).strip() for c in select_cols if str(c).strip()]


def _resolve_selected_columns(requested: list[str], schema_cols: list[str], *, default_identity: bool) -> list[str]:
    if any(c in {"*", "ALL", "__all__"} for c in requested):
        raise MlTableLookupError("full_width_blocked", "ML_TABLE 전체 컬럼 조회는 차단됩니다. 필요한 컬럼을 명시하세요.")
    if not requested:
        return identity_columns(schema_cols) if default_identity else []
    folded = {c.casefold(): c for c in schema_cols}
    out: list[str] = []
    unknown: list[str] = []
    for raw in requested:
        hit = folded.get(raw.casefold())
        if not hit:
            unknown.append(raw)
        elif hit not in out:
            out.append(hit)
    if unknown:
        raise MlTableLookupError("unknown_column", f"Unknown ML_TABLE column: {unknown[0]}", column=unknown[0], columns=unknown)
    return out


def _read_partition(cache_dir: Path, root_lot_id: str, selected_cols: list[str], wafer_id: str = "") -> tuple[list[dict[str, Any]], int, bool]:
    files = _partition_files(cache_dir, root_lot_id)
    if not files:
        return [], 0, False
    lf = pl.scan_parquet([str(p) for p in files], hive_partitioning=True)
    if wafer_id:
        schema_cols = lf.collect_schema().names()
        wf_col = _ci_col(schema_cols, "wafer_id", "wf_id", "WAFER_ID", "WF_ID")
        if wf_col:
            wf = str(wafer_id).strip().upper().lstrip("#")
            wf_forms = {wf}
            try:
                n = int(wf)
                wf_forms.update({str(n), f"{n:02d}", f"W{n}", f"W{n:02d}", f"WF{n}", f"WF{n:02d}"})
            except Exception:
                pass
            lf = lf.filter(pl.col(wf_col).cast(_STR, strict=False).str.strip_chars().str.to_uppercase().is_in(sorted(wf_forms)))
    total = int(lf.select(pl.len().alias("n")).collect().item(0, 0))
    limited = total > MAX_RESULT_ROWS
    if selected_cols:
        lf = lf.select([pl.col(c).cast(_STR, strict=False).alias(c) for c in selected_cols])
    df = lf.head(MAX_RESULT_ROWS).collect()
    return df.to_dicts(), total, limited


def scan_root_lot_cache(fp: Path, root_lot_id: str, wafer_ids: str = "", *, allow_stale: bool = False, profile: dict[str, Any] | None = None) -> tuple[pl.LazyFrame | None, dict[str, Any]]:
    """Return a LazyFrame for a cached root partition, or None when unavailable.

    allow_stale=True (SplitTable view fast-path): 소스 ML_TABLE 이 갱신돼 cache 가
    stale 여도 해당 root 의 hive 파티션이 있으면 **즉시 그 파티션을 서빙**하고
    백그라운드 재빌드만 예약한다. 이렇게 하면 데이터 갱신 직후(가장 흔한 stale
    구간)마다 모든 검색이 소스 전체를 재스캔(5~10초)하던 것을 파티션 인덱스 읽기
    (~수십 ms)로 바꾼다. 서빙 데이터는 최대 한 갱신 주기만큼 과거일 수 있으나,
    pivot 캐시·view payload 캐시와 동일한 stale-while-revalidate 철학이며, 빌드
    완료 시 view payload 캐시가 무효화돼 다음 조회부터 fresh 로 수렴한다.
    """
    fp = Path(fp).resolve()
    root = str(root_lot_id or "").strip().upper()
    status = cache_status(fp)
    if not root:
        return None, status
    _record_root_access(fp, root)
    if not status.get("has_cache"):
        _ensure_lookup_cache_ready_for_root_ram(fp, status)
        return None, status
    if status.get("source_stale"):
        if not allow_stale:
            _ensure_lookup_cache_ready_for_root_ram(fp, status)
            return None, status
        # stale-while-revalidate: 파티션을 서빙하면서 백그라운드 재빌드 예약.
        enqueue_build(fp)
    files = _partition_files(cache_dir_for(fp), root)
    if not files:
        # meta is written last, so this normally means a partition was removed
        # after a successful build. Rebuild only when the candidate index says
        # the requested root should exist (or the index itself is unavailable).
        index = read_candidate_index(fp)
        known_roots = {
            str(value or "").strip().upper()
            for value in (index.get("root_lot_ids") or [])
            if str(value or "").strip()
        }
        if not index or not known_roots or root in known_roots:
            enqueue_build(fp)
        return None, status
    # data_source 를 profile 에 기록해 호출측이 RAM 히트 / 첫 적재 / 디스크 스캔을
    # 구분해 타이밍 breakdown 에 표시할 수 있게 한다.
    _t0 = time.perf_counter()
    lf = _root_ram_cache_get(fp, root, files, status)
    data_source = "ram" if lf is not None else ""
    if lf is None:
        # A cold root can be very wide. Collecting every column into RAM inside
        # the HTTP request made switching to another root much slower and let
        # five different-root requests multiply peak memory. Serve a projected
        # lazy parquet scan now; warm the full root later in one idle thread.
        prefetch_queued = enqueue_root_ram_prefetch(fp, root)
        lf = pl.scan_parquet([str(p) for p in files], hive_partitioning=True)
        data_source = "disk"
        if profile is not None:
            profile["root_prefetch_queued"] = bool(prefetch_queued)
    if profile is not None:
        profile["root_data_source"] = data_source
        profile["root_scan_ms"] = round((time.perf_counter() - _t0) * 1000.0, 3)
    return _filter_wafer_lf(lf, wafer_ids), status


def readiness_response(fp: Path, root_lot_id: str, selected_cols: list[str], status: dict[str, Any], queued: dict[str, Any] | None = None) -> dict[str, Any]:
    cache_state = status.get("status") or "missing"
    if queued and cache_state == "missing":
        cache_state = queued.get("status") or "queued"
    meta = status.get("meta") or {}
    return {
        "ok": True,
        "file": fp.name,
        "source_path": str(fp),
        "root_lot_id": root_lot_id,
        "columns": selected_cols,
        "data": [],
        "showing": 0,
        "total_rows": 0,
        "limited": False,
        "lookup_cache_hit": False,
        "cache_status": cache_state,
        "source_stale": bool(status.get("source_stale")),
        "cache_build": queued or {},
        "cache": {
            "cache_dir": status.get("cache_dir") or "",
            "built_at": meta.get("built_at") or "",
            "row_count": meta.get("row_count") or 0,
            "total_cols": meta.get("total_cols") or 0,
            "root_lot_id_count": meta.get("root_lot_id_count") or 0,
        },
    }


def query_root_lot(
    fp: Path,
    root_lot_id: str,
    selected_cols: str | list[str] | tuple[str, ...] | None = None,
    wafer_id: str = "",
    *,
    enqueue_missing: bool = True,
) -> dict[str, Any]:
    fp = Path(fp).resolve()
    root = str(root_lot_id or "").strip().upper()
    if not root:
        raise MlTableLookupError("missing_root_lot_id", "root_lot_id is required")
    status = cache_status(fp)
    meta = status.get("meta") or {}
    schema = meta.get("schema") or {}
    schema_cols = list(schema.keys())
    requested = _parse_select_cols(selected_cols)
    selected = _resolve_selected_columns(requested, schema_cols, default_identity=True) if schema_cols else []
    if not schema_cols and requested:
        allowed = set(identity_columns(list(IDENTITY_COLUMN_CANDIDATES)))
        unknown = [c for c in requested if c not in allowed]
        if unknown:
            raise MlTableLookupError("cache_schema_unavailable", "ML_TABLE schema cache is not ready. Build lookup cache first.", columns=unknown)
    if not status.get("has_cache"):
        queued = enqueue_build(fp) if enqueue_missing else {}
        return readiness_response(fp, root, selected, status, queued)
    if status.get("source_stale") and enqueue_missing:
        enqueue_build(fp)
    rows, total, limited = _read_partition(cache_dir_for(fp), root, selected, wafer_id=wafer_id)
    return {
        "ok": True,
        "file": fp.name,
        "source_path": str(fp),
        "root_lot_id": root,
        "wafer_id": str(wafer_id or "").strip(),
        "columns": selected,
        "data": rows,
        "showing": len(rows),
        "total_rows": total,
        "limited": limited,
        "lookup_cache_hit": True,
        "cache_status": "stale" if status.get("source_stale") else "fresh",
        "source_stale": bool(status.get("source_stale")),
        "cache": {
            "cache_dir": status.get("cache_dir") or "",
            "built_at": meta.get("built_at") or "",
            "row_count": meta.get("row_count") or 0,
            "total_cols": meta.get("total_cols") or 0,
            "root_lot_id_count": meta.get("root_lot_id_count") or 0,
        },
    }


def search_columns(fp: Path, q: str = "", limit: int = 200, offset: int = 0) -> dict[str, Any]:
    status = cache_status(fp)
    meta = status.get("meta") or {}
    schema = meta.get("schema") or {}
    cols = list(schema.keys())
    needle = str(q or "").strip().casefold()
    matches = [c for c in cols if not needle or needle in c.casefold()]
    limit = max(1, min(500, int(limit or 200)))
    offset = max(0, int(offset or 0))
    page = matches[offset:offset + limit]
    return {
        "ok": True,
        "columns": page,
        "dtypes": {c: schema.get(c, "") for c in page},
        "query": q,
        "offset": offset,
        "limit": limit,
        "matched": len(matches),
        "total_cols": len(cols),
        "has_more": offset + len(page) < len(matches),
        "cache_status": status.get("status"),
        "source_stale": bool(status.get("source_stale")),
    }
