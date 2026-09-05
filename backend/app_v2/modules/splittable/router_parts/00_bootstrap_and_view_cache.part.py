"""routers/splittable.py v4.1.0 - multi-prefix transposed view + history + Base features.

v4.1 (2026-04-19, adapter-engineer slice):
  - Module-level DB_BASE removed. All route handlers now call PATHS.db_root /
    PATHS.base_root at request time, so `FLOW_*` env overrides and the
    admin_settings.json `data_roots` block land without a process restart.
  - New endpoint `GET /api/splittable/features` — joins
      `<db_root>/features_et_wafer.parquet` (wafer-level ET, 750 rows)
      + `<db_root>/features_inline_agg.parquet` (wafer-level INLINE aggregate, 50 rows)
    on (lot_id, wafer_id, product) via ET-left-join (Q005 default — preserves
    wafer coverage, INLINE-side cols are null when an ET wafer has no inline
    data). Returns wide-table metadata + columns + sample rows.
  - New endpoint `GET /api/splittable/uniques` — proxies
      `<db_root>/_uniques.json` unchanged, for frontend feature-select
    autocomplete catalog.
"""
import json, datetime, io, csv as csv_mod, hashlib, logging, time, threading, os, gc, shutil, zlib
import contextlib
from pathlib import Path
import sys
from collections import OrderedDict, deque

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_APP_ROOT = _BACKEND_ROOT.parent
for _path in (_APP_ROOT, _BACKEND_ROOT):
    _raw = str(_path)
    sys.path[:] = [p for p in sys.path if p != _raw]
    sys.path.insert(0, _raw)

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from typing import Any, List, Optional
import polars as pl
from core.paths import PATHS
from core.latest_lot_cache_format import (
    FILE_NAME as LATEST_LOT_STEP_CACHE_FILE,
    FORMAT_COLUMN as LATEST_LOT_STEP_CACHE_FORMAT_COLUMN,
    FORMAT_VERSION as LATEST_LOT_STEP_CACHE_FORMAT_VERSION,
    SOURCE_COLUMN as LATEST_LOT_STEP_CACHE_SOURCE_COLUMN,
    SOURCE_SPLITTABLE as LATEST_LOT_STEP_CACHE_SOURCE,
    normalize_product as _normalize_latest_cache_product,
)
from app_v2.shared.source_adapter import resolve_existing_root, resolve_column
from core.audit import record_user as _audit_user
from core.auth import current_user, is_page_manager, require_page_manager, require_admin
from core.domain import classify_process_area
from core import latest_lot_partitions as _latest_lot_partitions
from core import lot_list_cache as _lot_list_cache
from core import matching_cache as _matching_cache
from core import ml_table_lookup as _ml_table_lookup
from core import product_order as _product_order
from core import search_timing_log as _search_timing_log
from core import s3_sync as _s3
from core.utils import (
    _STR, is_cat, find_lot_wafer_cols, load_json, load_json_cached, save_json, safe_id,
    csv_response, csv_writer_bytes,
)
from core.splittable_sets_cache import invalidate as invalidate_splittable_sets_cache
from app_v2.modules.splittable.rulebook_repository import RulebookRepository
from app_v2.modules.splittable.rulebook_service import RulebookService

rulebook_repo = RulebookRepository()
rulebook_service = RulebookService(rulebook_repo)

# v8.8.26: override CI 매칭 진단 — 실패 경로/스키마 mismatch 를 로그로 가시화.
logger = logging.getLogger("flow.splittable")

router = APIRouter(prefix="/api/splittable", tags=["splittable"])

_DISCOVERY_CACHE_TTL_SEC = 30.0
_RGLOB_CACHE: dict[tuple[str, tuple[str, ...]], tuple[float, list[Path]]] = {}
_FIRST_DATA_FILE_CACHE: dict[tuple[str, tuple[str, ...]], tuple[float, Path | None]] = {}
_DB_ROOTS_CACHE: dict[str, tuple[float, list[Path]]] = {}
_LOT_LOOKUP_CACHE_TTL_SEC = 60.0
_LOT_LOOKUP_CACHE_MAX = 256
_LOT_LOOKUP_CACHE: dict[tuple, tuple[float, dict]] = {}

# 위 캐시들은 접근 시에만 만료를 확인하므로, 다시 조회되지 않는 key 는 메모리에
# 계속 남는다. 주기 sweep 으로 만료 항목을 정리한다.
from core import cache_sweeper as _cache_sweeper
_cache_sweeper.register_ttl_dict("splittable._RGLOB_CACHE", _RGLOB_CACHE, _DISCOVERY_CACHE_TTL_SEC, clock=time.monotonic)
_cache_sweeper.register_ttl_dict("splittable._FIRST_DATA_FILE_CACHE", _FIRST_DATA_FILE_CACHE, _DISCOVERY_CACHE_TTL_SEC, clock=time.monotonic)
_cache_sweeper.register_ttl_dict("splittable._DB_ROOTS_CACHE", _DB_ROOTS_CACHE, _DISCOVERY_CACHE_TTL_SEC, clock=time.monotonic)
_cache_sweeper.register_ttl_dict("splittable._LOT_LOOKUP_CACHE", _LOT_LOOKUP_CACHE, _LOT_LOOKUP_CACHE_TTL_SEC, clock=time.monotonic)
_CSV_ROWS_CACHE: dict[str, tuple[float, int, list[dict]]] = {}
_CSV_ROWS_CACHE_MAX = 32   # 파일 전체 행을 dict 로 들고 있으므로 개수 상한 필수
_SCHEMA_COLUMNS_CACHE: dict[str, tuple[float, int, list[str]]] = {}
_SCHEMA_COLUMNS_CACHE_MAX = 256
SPLITTABLE_VIEW_MAX_WAFERS = 3200
SPLITTABLE_MAX_WAFER_ID = 25


def _db_base() -> Path:
    """Resolve DB root at call time so runtime overrides take effect."""
    return resolve_existing_root("db", PATHS.db_root)


def _base_root() -> Path:
    """Resolve Base root at call time (env / admin_settings / default chain)."""
    return resolve_existing_root("base", PATHS.base_root)


def _path_cache_sig(path: Path | None):
    if path is None:
        return ("", 0.0, 0)
    try:
        st = path.stat()
        return (str(path.resolve()), st.st_mtime, st.st_size)
    except Exception:
        return (str(path), 0.0, 0)


def _lot_lookup_cache_sig(product: str = "") -> tuple:
    try:
        product_fp = _product_path(product) if product else None
        product_sig = _path_cache_sig(product_fp) if product_fp else ("", 0.0, 0)
        lookup_meta_sig = _path_cache_sig(_ml_table_lookup.meta_path_for(product_fp)) if product_fp else ("", 0.0, 0)
    except Exception:
        product_sig = (str(product or ""), 0.0, 0)
        lookup_meta_sig = ("", 0.0, 0)
    return (
        _path_cache_sig(_db_base()),
        _path_cache_sig(_base_root()),
        _path_cache_sig(SOURCE_CFG if "SOURCE_CFG" in globals() else None),
        product_sig,
        lookup_meta_sig,
    )


def _clone_lookup_payload(payload: dict | None) -> dict | None:
    if payload is None:
        return None
    out = {}
    for key, value in payload.items():
        if isinstance(value, list):
            out[key] = list(value)
        elif isinstance(value, dict):
            out[key] = dict(value)
        else:
            out[key] = value
    return out


def _lot_lookup_cache_get(key: tuple) -> dict | None:
    now = time.monotonic()
    cached = _LOT_LOOKUP_CACHE.get(key)
    if cached and now - cached[0] < _LOT_LOOKUP_CACHE_TTL_SEC:
        return _clone_lookup_payload(cached[1])
    if cached:
        _LOT_LOOKUP_CACHE.pop(key, None)
    return None


def _lot_lookup_cache_set(key: tuple, payload: dict) -> dict:
    if len(_LOT_LOOKUP_CACHE) >= _LOT_LOOKUP_CACHE_MAX:
        try:
            _LOT_LOOKUP_CACHE.pop(next(iter(_LOT_LOOKUP_CACHE)))
        except Exception:
            _LOT_LOOKUP_CACHE.clear()
    _LOT_LOOKUP_CACHE[key] = (time.monotonic(), _clone_lookup_payload(payload) or {})
    return payload


PLAN_DIR = PATHS.data_root / "splittable"
PLAN_DIR.mkdir(parents=True, exist_ok=True)
MATCH_CACHE_DIR = PLAN_DIR / "match_cache"
MATCH_CACHE_STATE_FILE = PLAN_DIR / "match_cache_state.json"
MATCH_CACHE_VERSION = 5
MATCH_CACHE_REFRESH_MINUTES_DEFAULT = 30
MATCH_CACHE_REFRESH_MINUTES_MIN = 30
MATCH_CACHE_REFRESH_MINUTES_MAX = 60
MATCH_CACHE_ROOT_COL = "__cache_root_lot_id"
MATCH_CACHE_WAFER_COL = "__cache_wafer_id"
MATCH_CACHE_FAB_COL = "__cache_fab_lot_id"
MATCH_CACHE_TS_COL = "__cache_ts"
# 이 행이 온 FAB 제품 폴더. 매칭은 FAB 전체를 훑으므로(랏 lineage 가 다른 제품
# 폴더에 있을 수 있다) 제품별 물량을 세려면 행마다 출처가 있어야 한다.
MATCH_CACHE_SRC_PRODUCT_COL = "__cache_src_product"
LEGACY_LATEST_LOT_STEP_CACHE_FILE = "splittable_latest_lot_step.parquet"
LATEST_LOT_STEP_CACHE_COLUMNS = [
    LATEST_LOT_STEP_CACHE_FORMAT_COLUMN,
    LATEST_LOT_STEP_CACHE_SOURCE_COLUMN,
    "product",
    # 이 랏이 실제로 속한 FAB 제품 폴더. product(=ML_TABLE 제품, 교차 폴더
    # lineage 포함)와 다르며 물량 집계는 이쪽을 봐야 한다.
    "src_product",
    "root_lot_id",
    "wafer_id",
    "lot_id",
    "step_id",
    "function_step",
    # FAB 가 주는 랏 구분(양산/엔지니어링/모니터…). 매칭 캐시에는 이미 있었지만
    # 이 캐시에 안 실려서 대시보드가 lot_type 으로 물량을 나눠 볼 수 없었다.
    # FAB 에 열이 없는 환경에서는 빈 문자열로 채운다.
    "lot_type",
    "tkout_time",
    "update_time",
]
_MATCH_CACHE_THREAD: threading.Thread | None = None
_MATCH_CACHE_STARTED = False
_MATCH_CACHE_NEXT_TICK_AT = ""
_MATCH_CACHE_STOP = threading.Event()
# 프로세스 종료용 _MATCH_CACHE_STOP 과 다르다 — 이쪽은 관리자가 "지금 이 제품만"
# 중단시키는 신호다. 중단된 제품은 부분 결과를 버리고 다음 제품으로 넘어간다.
_MATCH_CACHE_CANCEL_LOCK = threading.Lock()
_MATCH_CACHE_CANCEL: dict = {"product": "", "by": "", "at": ""}
_MATCH_CACHE_BUILD_LOCK = threading.Lock()
_MATCH_CACHE_AUTO_BUILD_MISS_TTL_SEC = 120.0
_MATCH_CACHE_AUTO_BUILD_MISS: dict[str, tuple[float, str]] = {}
_MATCH_CACHE_JOB_LOCK = threading.Lock()
_MATCH_CACHE_JOB_STATE: dict = {
    "running": False,
    "queued": False,
    "reason": "",
    "started_at": "",
    "finished_at": "",
    "current_product": "",
    "current_started_ts": 0.0,
    "total": 0,
    "done": 0,
    "ok_count": 0,
    "failed_count": 0,
    "skipped_count": 0,
    "paused": False,
    "last_error": "",
    "products": [],
    "order": [],
}
_PLAN_RISK_CACHE: dict[tuple[str, bool], dict] = {}
_PLAN_RISK_CACHE_LOCK = threading.Lock()
_PLAN_RISK_CACHE_MAX = 64
# 엔트리 = (hard_sig, soft_sig, payload, approx_bytes). hard_sig 는 즉시 무효화
# 대상(소스 ML_TABLE = 신규 lot 신호 + 사용자 편집 입력), soft_sig 는 백그라운드
# 스케줄러가 주기적으로 재기록하는 파생 캐시 — soft 만 바뀌면
# stale-while-revalidate 로 즉시 서빙한다.
# 항목 수와 별도로 바이트 예산을 둔다 — wide KNOB payload 는 개당 ~22MB
# (실측 441B/셀)라 128개 상한만으로는 수 GB 까지 자랄 수 있다.
_VIEW_CACHE: OrderedDict[tuple, tuple[tuple, tuple, dict, int]] = OrderedDict()
_VIEW_CACHE_LOCK = threading.Lock()
_VIEW_CACHE_MAX_ENTRIES_DEFAULT = 512
_VIEW_CACHE_BYTES = 0  # 현재 보유 추정치 (lock 하에서만 갱신)
_VIEW_CACHE_CELL_COST = 450  # 레거시 _cells 셀당 파이썬 객체 비용 (실측 441B)
_VIEW_CACHE_COMPACT_CELL_COST = 40  # v2 슬림 행(a/p/m) 셀당 비용 — 캐시는 이쪽만 담는다
_VIEW_CACHE_AUTO_MB_LOCK = threading.Lock()
_VIEW_CACHE_AUTO_MB_CACHE: tuple[float, float] | None = None
_VIEW_CACHE_AUTO_MB_TTL = 60.0
# v3: KNOB metadata aliases are lookup-only; cached payloads from v2 may contain
# duplicate space/underscore/_Split virtual rows and must not survive deploy.
_VIEW_DISK_CACHE_VERSION = 3
_VIEW_DISK_CACHE_LOCK = threading.Lock()
_VIEW_PRODUCT_SIG_CACHE: OrderedDict[tuple[str, ...], tuple[float, tuple]] = OrderedDict()
_VIEW_PRODUCT_SIG_LOCK = threading.Lock()
_VIEW_PRODUCT_SIG_CACHE_MAX = 512
_VIEW_COMPUTE_LOCK = threading.Lock()
_VIEW_COMPUTE_EVENTS: dict[tuple, tuple[int, threading.Event]] = {}

# ── /view cold 계산 전용 레인 ────────────────────────────────────────────────
# 예전에는 resource_guard 의 essential 세마포어(코어수 기준 2~4슬롯)가 /view 요청
# '전체' 를 직렬화했다. 그래서 payload 캐시 HIT(수십 ms)도 앞선 cold 계산(수 초)
# 뒤에 줄을 섰고, 동시 사용자가 늘수록 HIT 응답까지 같이 느려졌다.
# 이제 미들웨어는 /view 를 self-gated 로 통과시키고(app_v2/runtime/resource_guard.py
# DEFAULT_SELF_GATED_PATHS), 실제 메모리를 쓰는 cold 계산 구간만 이 세마포어로
# 직렬화한다. 캐시 HIT·빈 결과·single-flight 대기는 여기 줄서지 않는다.
_VIEW_COLD_LANE_TLS = threading.local()


_VIEW_COLD_CONCURRENCY_MIN = 1
_VIEW_COLD_CONCURRENCY_MAX = 8


def _view_cold_lane_default() -> int:
    if _ml_table_lookup._root_ram_cache_use_dev():
        return 1
    try:
        from core.runtime_limits import effective_cpu_count
        cores = int(effective_cpu_count())
    except Exception:
        cores = 4
    # 운영 기본은 3슬롯이다. 실제 할당 코어가 3보다 적으면 그 수에 맞춰 낮춘다.
    return max(1, min(3, cores))


def _view_cold_lane_concurrency() -> int:
    """우선순위: env > 톱니바퀴 설정(운영/개발 분리) > 코어수 기반 기본값.

    호출할 때마다 다시 읽는다 — 톱니바퀴에서 저장하면 재시작 없이 다음 요청부터
    적용된다(설정 읽기는 2초 메모이즈). polars 스레드 풀과 달리 이 레인은
    런타임 조절이 가능하다."""
    # 개발 서버는 백그라운드 작업과 Flow-i에 여유를 남기기 위해 항상 1슬롯이다.
    if _ml_table_lookup._root_ram_cache_use_dev():
        return 1
    raw = os.environ.get("FLOW_SPLITTABLE_VIEW_COLD_CONCURRENCY", "").strip()
    if raw:
        try:
            return max(_VIEW_COLD_CONCURRENCY_MIN, min(_VIEW_COLD_CONCURRENCY_MAX, int(raw)))
        except Exception:
            pass
    try:
        from core import cache_settings
        saved = cache_settings.get_int_role(
            "view_cold_concurrency", _ml_table_lookup._root_ram_cache_use_dev(), None)
        if saved:
            return max(_VIEW_COLD_CONCURRENCY_MIN, min(_VIEW_COLD_CONCURRENCY_MAX, int(saved)))
    except Exception:
        pass
    return _view_cold_lane_default()


class _ResizableLane:
    """크기를 런타임에 바꿀 수 있는 동시성 게이트.

    threading.Semaphore 는 생성 시점에 크기가 고정이라 톱니바퀴로 조절할 수 없다.
    여기서는 활성 수를 직접 세고 목표 크기를 acquire 시점마다 다시 읽는다.
    대기 중에도 상한이 커질 수 있으므로 최대 1초 간격으로 깨어나 재확인한다."""

    def __init__(self, size_fn):
        self._size_fn = size_fn
        self._cond = threading.Condition()
        self._active = 0

    def acquire(self, timeout: float) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._cond:
            while True:
                try:
                    limit = max(1, int(self._size_fn()))
                except Exception:
                    limit = 1
                if self._active < limit:
                    self._active += 1
                    return True
                remain = deadline - time.monotonic()
                if remain <= 0:
                    return False
                self._cond.wait(min(remain, 1.0))

    def release(self) -> None:
        with self._cond:
            self._active = max(0, self._active - 1)
            self._cond.notify()

    def stats(self) -> dict:
        with self._cond:
            active = self._active
        try:
            limit = max(1, int(self._size_fn()))
        except Exception:
            limit = 1
        return {"active": active, "limit": limit}


_VIEW_COLD_SEMAPHORE = _ResizableLane(_view_cold_lane_concurrency)


def _view_cold_lane_stats() -> dict:
    out = _VIEW_COLD_SEMAPHORE.stats()
    out["default"] = _view_cold_lane_default()
    out["env_pinned"] = bool(os.environ.get("FLOW_SPLITTABLE_VIEW_COLD_CONCURRENCY", "").strip())
    return out


def _view_cold_lane_wait_sec() -> float:
    return max(1.0, min(600.0, _env_float("FLOW_SPLITTABLE_VIEW_COLD_QUEUE_TIMEOUT_SEC", 90.0)))


def _view_cold_lane_acquire(runtime_profile: dict | None = None) -> bool:
    """cold 계산 슬롯을 잡는다. 대기 시간은 runtime_profile 에 남긴다."""
    wait_started = time.perf_counter()
    ok = _VIEW_COLD_SEMAPHORE.acquire(timeout=_view_cold_lane_wait_sec())
    if runtime_profile is not None:
        runtime_profile["cold_lane_wait_ms"] = (time.perf_counter() - wait_started) * 1000.0
    if ok:
        _VIEW_COLD_LANE_TLS.held = True
    return ok


def _view_cold_lane_release() -> None:
    """획득한 스레드에서만 1회 반납 (미획득 상태 호출은 무시)."""
    if getattr(_VIEW_COLD_LANE_TLS, "held", False):
        _VIEW_COLD_LANE_TLS.held = False
        try:
            _VIEW_COLD_SEMAPHORE.release()
        except Exception:
            pass


def _view_cache_max_entries() -> int:
    try:
        n = int(float(os.environ.get("FLOW_SPLITTABLE_VIEW_CACHE_MAX_ENTRIES", "")
                      or _VIEW_CACHE_MAX_ENTRIES_DEFAULT))
    except Exception:
        n = _VIEW_CACHE_MAX_ENTRIES_DEFAULT
    return max(8, min(4096, n))


def _view_cache_auto_max_mb() -> float:
    """호스트 메모리 비례 hot 응답 예산 — 총량의 15%를 [1GB, 6GB]로 제한.

    SplitTable 읽기 순서의 첫 계층이라 pivot/lookup/FAB 연산을 모두 건너뛴다.
    30GB 호스트에서는 약 4.5GB를 사용하고 전체 cache_budget 지분 상한을 한 번
    더 지나 ET 다운로드·백그라운드 빌드 여유를 보존한다."""
    global _VIEW_CACHE_AUTO_MB_CACHE
    now = time.monotonic()
    with _VIEW_CACHE_AUTO_MB_LOCK:
        cached = _VIEW_CACHE_AUTO_MB_CACHE
        if cached is not None and now - cached[0] < _VIEW_CACHE_AUTO_MB_TTL:
            return cached[1]
    mb = 1024.0
    try:
        from core.runtime_limits import system_memory_snapshot
        total_gb = float(system_memory_snapshot().get("system_memory_total_gb") or 0.0)
        if total_gb > 0:
            mb = max(1024.0, min(6144.0, total_gb * 1024.0 * 0.15))
    except Exception:
        mb = 1024.0
    with _VIEW_CACHE_AUTO_MB_LOCK:
        _VIEW_CACHE_AUTO_MB_CACHE = (now, mb)
    return mb


def _view_cache_max_bytes() -> int:
    raw = str(os.environ.get("FLOW_SPLITTABLE_VIEW_CACHE_MAX_MB", "") or "").strip()
    if raw:
        try:
            mb = float(raw)
        except Exception:
            mb = _view_cache_auto_max_mb()
        budget = int(max(64.0, min(8192.0, mb)) * 1024 * 1024)
    else:
        configured = None
        try:
            from core import cache_settings
            configured = cache_settings.get_float_role(
                "view_mb", _ml_table_lookup._root_ram_cache_use_dev())
        except Exception:
            configured = None
        mb = configured if configured is not None and configured > 0 else _view_cache_auto_max_mb()
        budget = int(max(64.0, min(8192.0, mb)) * 1024 * 1024)
    try:
        from core import cache_budget
        # 운영자 개별 설정도 프로세스 전체 안전 풀은 우회하지 않는다.
        budget = cache_budget.capped("splittable_view_payload", budget)
    except Exception:
        pass
    return budget


def _estimate_view_payload_bytes(payload: dict) -> int:
    compact = payload.get("rows_compact")
    if compact is not None:
        cells = 0
        for r in compact:
            a = r.get("a")
            if isinstance(a, list):
                cells += len(a)
        return 8192 + cells * _VIEW_CACHE_COMPACT_CELL_COST
    cells = 0
    for r in (payload.get("rows") or []):
        c = r.get("_cells")
        if isinstance(c, dict):
            cells += len(c)
    return 8192 + cells * _VIEW_CACHE_CELL_COST


def _view_signature_digest(value: tuple) -> str:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _view_disk_cache_enabled() -> bool:
    return _env_bool("FLOW_SPLITTABLE_VIEW_DISK_CACHE", True)


def _view_disk_cache_path(key: tuple) -> Path:
    product = str(key[0] if key else "")
    product_hash = hashlib.sha1(product.encode("utf-8")).hexdigest()[:16]
    key_raw = json.dumps(key, ensure_ascii=False, separators=(",", ":"), default=str)
    key_hash = hashlib.sha256(key_raw.encode("utf-8")).hexdigest()
    return _base_root() / "cache" / "split_table_view_payload" / f"v{_VIEW_DISK_CACHE_VERSION}" / product_hash / f"{key_hash}.json.z"


def _view_disk_cache_read(key: tuple, hard_sig: tuple, soft_sig: tuple) -> tuple[str, dict | None]:
    if not _view_disk_cache_enabled():
        return "miss", None
    fp = _view_disk_cache_path(key)
    try:
        raw = zlib.decompress(fp.read_bytes())
        if _orjson is not None:
            record = _orjson.loads(raw)
        else:
            record = json.loads(raw.decode("utf-8"))
        if not isinstance(record, dict) or int(record.get("version") or 0) != _VIEW_DISK_CACHE_VERSION:
            raise ValueError("unsupported view disk cache version")
        if tuple(record.get("key") or ()) != tuple(key):
            raise ValueError("view disk cache key mismatch")
        if str(record.get("hard") or "") != _view_signature_digest(hard_sig):
            try:
                fp.unlink()
            except OSError:
                pass
            return "miss", None
        payload = record.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("view disk cache payload missing")
        freshness = (
            "fresh" if str(record.get("soft") or "") == _view_signature_digest(soft_sig)
            else "stale"
        )
        return freshness, payload
    except FileNotFoundError:
        return "miss", None
    except Exception:
        logger.debug("SplitTable disk view cache read failed: %s", fp, exc_info=True)
        try:
            fp.unlink()
        except OSError:
            pass
        return "miss", None


def _view_disk_cache_write(key: tuple, hard_sig: tuple, soft_sig: tuple, payload: dict) -> None:
    if not _view_disk_cache_enabled():
        return
    try:
        record = {
            "version": _VIEW_DISK_CACHE_VERSION,
            "key": list(key),
            "hard": _view_signature_digest(hard_sig),
            "soft": _view_signature_digest(soft_sig),
            "payload": payload,
        }
        if _orjson is not None:
            raw = _orjson.dumps(
                record, default=str,
                option=_orjson.OPT_SERIALIZE_NUMPY | _orjson.OPT_NON_STR_KEYS)
        else:
            raw = json.dumps(record, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
        max_mb = max(1.0, min(256.0, _env_float("FLOW_SPLITTABLE_VIEW_DISK_ENTRY_MAX_MB", 64.0)))
        if len(raw) > int(max_mb * 1024 * 1024):
            return
        compressed = zlib.compress(raw, level=1)
        fp = _view_disk_cache_path(key)
        fp.parent.mkdir(parents=True, exist_ok=True)
        tmp = fp.with_name(f"{fp.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_bytes(compressed)
        os.replace(tmp, fp)
        # 제품별 최근 결과만 디스크에 유지한다. RAM과 달리 압축 bytes라 작지만
        # 검색 이력이 무한히 쌓이지 않도록 쓰기 시점에 저비용 상한을 적용한다.
        limit = int(max(8.0, min(512.0, _env_float("FLOW_SPLITTABLE_VIEW_DISK_MAX_PER_PRODUCT", 64.0))))
        with _VIEW_DISK_CACHE_LOCK:
            files = sorted(fp.parent.glob("*.json.z"), key=lambda p: p.stat().st_mtime, reverse=True)
            for old in files[limit:]:
                try:
                    old.unlink()
                except OSError:
                    pass
    except Exception:
        logger.debug("SplitTable disk view cache write failed", exc_info=True)


def _view_product_signature(paths: list[Path]) -> tuple:
    """공유 드라이브 stat 묶음을 짧게 메모이즈해 payload-cache HIT를 빠르게 한다.

    앱을 통한 plan/tag/config 쓰기는 `_clear_split_view_cache()`를 호출하므로 즉시
    무효화된다. 외부에서 ML_TABLE을 교체한 경우만 기본 2초 안에 감지한다.
    """
    key = tuple(str(Path(path)) for path in paths)
    now = time.monotonic()
    ttl = max(0.0, min(30.0, _env_float("FLOW_SPLITTABLE_VIEW_SIGNATURE_TTL_SEC", 2.0)))
    with _VIEW_PRODUCT_SIG_LOCK:
        cached = _VIEW_PRODUCT_SIG_CACHE.get(key)
        if cached is not None and now - cached[0] <= ttl:
            _VIEW_PRODUCT_SIG_CACHE.move_to_end(key)
            return cached[1]
    value = tuple(_path_cache_sig(path) for path in paths)
    with _VIEW_PRODUCT_SIG_LOCK:
        _VIEW_PRODUCT_SIG_CACHE[key] = (now, value)
        _VIEW_PRODUCT_SIG_CACHE.move_to_end(key)
        while len(_VIEW_PRODUCT_SIG_CACHE) > _VIEW_PRODUCT_SIG_CACHE_MAX:
            _VIEW_PRODUCT_SIG_CACHE.popitem(last=False)
    return value


def _view_compute_begin(key: tuple) -> tuple[bool, threading.Event]:
    """같은 검색 key의 동시 cold 계산을 single-flight로 병합한다."""
    with _VIEW_COMPUTE_LOCK:
        current = _VIEW_COMPUTE_EVENTS.get(key)
        if current is not None:
            return False, current[1]
        event = threading.Event()
        _VIEW_COMPUTE_EVENTS[key] = (threading.get_ident(), event)
        return True, event


def _view_compute_finish(key: tuple | None) -> None:
    # cold 레인 반납은 owner 등록 여부와 무관하게 항상 시도한다 — 모든 종료 경로가
    # 이 함수를 지나므로 여기 한 곳에서 반납하면 누수가 없다(미획득이면 no-op).
    _view_cold_lane_release()
    if key is None:
        return
    event = None
    with _VIEW_COMPUTE_LOCK:
        current = _VIEW_COMPUTE_EVENTS.get(key)
        if current is not None and current[0] == threading.get_ident():
            _VIEW_COMPUTE_EVENTS.pop(key, None)
            event = current[1]
    if event is not None:
        event.set()


def _view_compute_wait_seconds() -> float:
    return max(1.0, min(300.0, _env_float("FLOW_SPLITTABLE_VIEW_SINGLEFLIGHT_WAIT_SEC", 90.0)))
# stale hit → 백그라운드 재검증. 전역 단일 워커 + 병합 큐 — 예전 thread-per-key
# 즉시 실행은 lot_progress 재기록 직후 검색마다 풀 재계산 스레드를 띄워, 5코어의
# 전역 polars 풀을 사용자 검색과 나눠 쓰는 CPU 경쟁(연속 검색 지연)을 만들었다.
# stale 재계산도 SplitTable 응답의 일부이므로 운영 서버에서만 실행한다.
# soft dep(최신 lot/fab 라벨)의 신선도는 쿨다운(기본 3h) 간격 갱신으로 충분하고,
# 신규 lot/사용자 편집은 hard_sig 가 요청 내 동기 반영하므로 정확성과 무관하다.
# TLS.force 는 재검증 워커가 view_split 을 재진입할 때 캐시 서빙을 건너뛰고 강제
# 재계산하게 하는 플래그.
_VIEW_REVALIDATE_TLS = threading.local()
# HTTP 경로에서 타이밍 기록을 직렬화 뒤로 미루기 위한 슬롯. view_split_http 가
# 리스트를 걸어 두면 _record_search_timing 이 즉시 쓰지 않고 여기에 담고,
# 직렬화 시간을 채운 뒤 flush 한다.
_VIEW_TIMING_TLS = threading.local()
_VIEW_REVALIDATE_LOCK = threading.Lock()
_VIEW_REVALIDATE_INFLIGHT: set[tuple] = set()
_VIEW_REVALIDATE_LAST: dict[tuple, float] = {}
_VIEW_REVALIDATE_PENDING: OrderedDict[tuple, tuple[float, dict]] = OrderedDict()
_VIEW_REVALIDATE_WAKE = threading.Event()
_VIEW_REVALIDATE_THREAD: threading.Thread | None = None
_VIEW_REVALIDATE_LAST_ENQUEUE_TS = 0.0
_VIEW_REVALIDATE_PENDING_MAX = 16
_VIEW_REVALIDATE_LAST_MAX = 512
_VIEW_REVALIDATE_COOLDOWN_SEC_DEFAULT = 3 * 3600.0
_VIEW_REVALIDATE_DELAY_SEC_DEFAULT = 30.0
_VIEW_REVALIDATE_BURST_QUIET_SEC = 3.0
# HIT 경로 최적화: 의존 시그니처의 stat 중 product-독립 전역 파일(config/rulebook =
# hard, lot_progress 파생 = soft)만 짧은 TTL 로 캐시한다. 동시 다수 사용자가 매
# 요청마다 동일 전역 파일을 재-stat 하던 공유드라이브 부하를 제거. per-product 파일
# (소스 ML_TABLE/plan/tag/management/custom)은 항상 fresh stat 하므로 사용자 편집·
# 신규 lot 은 지연 없이 즉시 감지된다. 전역 config/rulebook admin 변경과 soft 파생
# 변경만 최대 TTL(≤1s) 지연 — 각각 체감 없음 / SWR 이 흡수.
_VIEW_GLOBAL_SIG_CACHE: dict = {}
_VIEW_GLOBAL_SIG_LOCK = threading.Lock()
_VIEW_GLOBAL_SIG_TTL = 1.0
_VIEW_RAW_FALLBACK_MAX_MB_DEFAULT = 16.0
_MISMATCH_NOTIFY_LOCK = threading.Lock()
_MISMATCH_NOTIFY_WAKE = threading.Event()
_MISMATCH_NOTIFY_PENDING: OrderedDict[tuple, dict] = OrderedDict()
_MISMATCH_NOTIFY_THREAD: threading.Thread | None = None
_MISMATCH_NOTIFY_DEBOUNCE_SEC = 0.2
_MISMATCH_NOTIFY_PENDING_MAX = 2000
PRODUCT_RAM_CACHE_VERSION = 1
PRODUCT_RAM_CACHE_REFRESH_MINUTES_DEFAULT = 30
PRODUCT_RAM_CACHE_REFRESH_MINUTES_MIN = 30
PRODUCT_RAM_CACHE_REFRESH_MINUTES_MAX = 240
PRODUCT_RAM_CACHE_MAX_GB_DEFAULT = 2.0
ROOT_LOT_CACHE_LIMIT_MAX = 50000
_PRODUCT_RAM_CACHE_LOCK = threading.RLock()
_PRODUCT_RAM_CACHE: dict[str, dict] = {}
_PRODUCT_RAM_CACHE_STATUS: dict[str, dict] = {}
_PRODUCT_RAM_CACHE_REFRESHING: set[str] = set()
_PRODUCT_RAM_CACHE_BUILD_LOCK = threading.Lock()
_PRODUCT_RAM_CACHE_STOP = threading.Event()
_PRODUCT_RAM_CACHE_THREAD: threading.Thread | None = None
_PRODUCT_RAM_CACHE_STARTED = False
_PRODUCT_RAM_CACHE_NEXT_TICK_AT = ""
_PRODUCT_RAM_CACHE_JOB_LOCK = threading.Lock()
_PRODUCT_RAM_CACHE_JOB_STATE: dict = {
    "running": False,
    "queued": False,
    "reason": "",
    "started_at": "",
    "finished_at": "",
    "current_product": "",
    "total": 0,
    "done": 0,
    "ok_count": 0,
    "failed_count": 0,
    "skipped_count": 0,
    "last_error": "",
    "products": [],
    "order": [],
}
LONG_PIVOT_CACHE_VERSION = 1
_LONG_PIVOT_CACHE_DIR = PLAN_DIR / "long_pivot_cache"
_LONG_PIVOT_JOB_LOCK = threading.Lock()
_LONG_PIVOT_QUEUE: deque[tuple[str, str, bool]] = deque()
_LONG_PIVOT_JOB_THREAD: threading.Thread | None = None
_LONG_PIVOT_JOB_STATE: dict = {
    "running": False,
    "queued": False,
    "current": "",
    "started_at": "",
    "finished_at": "",
    "last_error": "",
    "last_source": "",
}
PREFIX_CFG = PLAN_DIR / "prefix_config.json"
DEFAULT_PREFIXES = ["KNOB", "MASK", "INLINE", "VM", "FAB"]
PLAN_ALLOWED_PREFIXES = ["KNOB", "MASK", "FAB"]  # Only these can have plan values
CUSTOM_TAG_PREFIX = "TAG"
DEFAULT_CUSTOM_TAG_COLUMN = f"{CUSTOM_TAG_PREFIX}_purpose"
DEFAULT_CUSTOM_TAG_LABEL = "purpose"
CUSTOM_TAG_COLOR_PALETTE = {
    "#ffffff", "#f3f4f6", "#d1d5db", "#fecaca", "#fed7aa",
    "#fef3c7", "#d9f99d", "#bbf7d0", "#99f6e4", "#a5f3fc",
    "#bfdbfe", "#c7d2fe", "#ddd6fe", "#e9d5ff", "#f5d0fe",
    "#fbcfe8", "#fee2e2", "#ffedd5", "#ecfccb", "#e0f2fe",
}
MANAGEMENT_ROW_PREFIX = "MGMT"
_INVALID_CUSTOM_TOKENS = {"undefined", "null"}
# v8.8.6: paste 세트 공유 저장소 — LocalStorage 대신 BE 에 올려 팀 공용 풀 + CUSTOM 탭 연동.
PASTE_SETS_FILE = PLAN_DIR / "paste_sets.json"
# v8.4.9: 엑셀 메모/태그 저장소 — wafer 단위(tag) + parameter 단위(memo) 공용.
#   scope="wafer": target_key = "{product}__{root_lot_id}__W{wafer_id}"
#   scope="param": target_key = "{product}__{root_lot_id}__W{wafer_id}__{param}"
# 각 항목은 {id, text, username, created_at} 를 보관하고 작성자/관리자만 삭제 가능.
NOTES_FILE = PLAN_DIR / "notes.json"
TRACKER_ISSUES_FILE = PATHS.data_root / "tracker" / "issues.json"
INFORMS_FILE = PATHS.data_root / "informs" / "informs.json"


def _clean_custom_token(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    token = value.strip()
    if not token or token.casefold() in _INVALID_CUSTOM_TOKENS:
        return ""
    return token


def _clean_custom_column_name(value: Any, *, allow_management: bool = False) -> str:
    column = _clean_custom_token(value)
    if not column:
        return ""
    if not allow_management and column.upper().startswith(f"{MANAGEMENT_ROW_PREFIX}_"):
        return ""
    return column


def _clean_custom_columns(columns: Any, *, allow_management: bool = False) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in columns if isinstance(columns, list) else []:
        column = _clean_custom_column_name(raw, allow_management=allow_management)
        if not column or column in seen:
            continue
        seen.add(column)
        out.append(column)
    return out


def _clean_custom_set_name(value: Any) -> str:
    return _clean_custom_token(value)


def _custom_file_path_for_name(value: Any) -> tuple[Path, str]:
    name = _clean_custom_set_name(value)
    safe_name = safe_id(name) if name else ""
    if not name or not safe_name or safe_name.casefold() in _INVALID_CUSTOM_TOKENS:
        raise HTTPException(400, "custom name required")
    return PLAN_DIR / f"custom_{safe_name}.json", name


def _custom_file_stem_invalid(path: Path) -> bool:
    stem = path.stem
    raw = stem[len("custom_"):] if stem.startswith("custom_") else stem
    return not _clean_custom_set_name(raw)


def _sanitize_custom_record(record: Any, path: Path | None = None, *, persist: bool = False) -> dict | None:
    if not isinstance(record, dict):
        return None
    if path is not None and _custom_file_stem_invalid(path):
        return None
    name = _clean_custom_set_name(record.get("name"))
    if not name:
        return None
    columns = _clean_custom_columns(record.get("columns") or [])
    if not columns:
        return None
    cleaned = {**record, "name": name, "columns": columns}
    if persist and path is not None and cleaned != record:
        save_json(path, cleaned)
    return cleaned


def _clean_overlay_store_data(data: Any, *, allow_management: bool = True) -> tuple[dict, bool]:
    if not isinstance(data, dict):
        return {"columns": [], "values": {}, "colors": {}}, False
    changed = False
    cleaned_cols = []
    for raw in data.get("columns") if isinstance(data.get("columns"), list) else []:
        if not isinstance(raw, dict):
            changed = True
            continue
        column = _clean_custom_column_name(raw.get("column"), allow_management=allow_management)
        if not column:
            changed = True
            continue
        entry = {**raw, "column": column}
        if entry != raw:
            changed = True
        cleaned_cols.append(entry)
    cleaned_values = {}
    values = data.get("values") if isinstance(data.get("values"), dict) else {}
    for raw_key, value in values.items():
        parts = str(raw_key or "").split("|", 3)
        if len(parts) != 4:
            changed = True
            continue
        column = _clean_custom_column_name(parts[3], allow_management=allow_management)
        if not column:
            changed = True
            continue
        parts[3] = column
        key = "|".join(parts)
        if key != raw_key:
            changed = True
        cleaned_values[key] = value
    cleaned_colors = {}
    colors = data.get("colors") if isinstance(data.get("colors"), dict) else {}
    for raw_key, raw_color in colors.items():
        parts = str(raw_key or "").split("|", 3)
        if len(parts) != 4:
            changed = True
            continue
        column = _clean_custom_column_name(parts[3], allow_management=allow_management)
        color = str(raw_color or "").strip().lower()
        if not column or color not in CUSTOM_TAG_COLOR_PALETTE:
            changed = True
            continue
        parts[3] = column
        key = "|".join(parts)
        if key != raw_key or color != raw_color:
            changed = True
        cleaned_colors[key] = color
    return {"columns": cleaned_cols, "values": cleaned_values, "colors": cleaned_colors}, changed


def _load_prefixes():
    prefixes = load_json_cached(PREFIX_CFG, DEFAULT_PREFIXES)
    if not isinstance(prefixes, list):
        prefixes = list(DEFAULT_PREFIXES)
    out = [str(p).strip().upper() for p in prefixes if str(p).strip()]
    if CUSTOM_TAG_PREFIX not in out:
        out.append(CUSTOM_TAG_PREFIX)
    return out


def _cast_cats_lazy(lf):
    """Cast Categorical to Utf8 in a LazyFrame."""
    try:
        schema = lf.collect_schema()
    except Exception:
        schema = lf.schema
    casts = [pl.col(n).cast(_STR, strict=False) for n, d in schema.items() if is_cat(d)]
    out = lf.with_columns(casts) if casts else lf
    try:
        from core.utils import filter_valid_wafer_ids_lazy
        return filter_valid_wafer_ids_lazy(out, list(schema.keys()))
    except Exception:
        return out


def _scan_cast_options():
    try:
        return pl.ScanCastOptions(categorical_to_string="allow")
    except Exception:
        return None


# ── parquet 스키마 메모이즈 ───────────────────────────────────────────────
# `collect_schema()` 는 파일 footer 를 파싱하므로 컬럼 수에 비례해 비싸다
# (4000컬럼 실측 9.2ms/회). 그런데 cold 검색 1건이 이 호출을 16~25회 하고,
# 같은 파일에 매번 같은 답을 다시 묻는다 — 넓은 테이블에서는 검색 시간의
# 대부분이 데이터 읽기(21ms)가 아니라 이 반복 조회(~164ms)였다.
# 파일 mtime+size 로 키를 잡아 내용이 바뀌면 자동 무효화된다.
_SCAN_SCHEMA_CACHE: OrderedDict[tuple, tuple] = OrderedDict()
_SCAN_SCHEMA_CACHE_LOCK = threading.Lock()
_SCAN_SCHEMA_CACHE_MAX = 256


def _scan_schema_cache_key(source, hive_partitioning) -> tuple | None:
    """(경로, mtime, size, hive) 키. stat 실패(원격/미존재)면 None = 캐시 미사용."""
    try:
        p = Path(str(source))
        st = p.stat()
        return (str(p), st.st_mtime, st.st_size, bool(hive_partitioning))
    except Exception:
        return None


def _cached_scan_schema(source, hive_partitioning=None):
    """(schema_override, column_names) 반환. schema_override 는 Categorical→String
    드리프트 보정이 필요할 때만 dict, 아니면 None (기존 계약 유지)."""
    key = _scan_schema_cache_key(source, hive_partitioning)
    if key is not None:
        with _SCAN_SCHEMA_CACHE_LOCK:
            hit = _SCAN_SCHEMA_CACHE.get(key)
            if hit is not None:
                _SCAN_SCHEMA_CACHE.move_to_end(key)
                return hit
    try:
        kwargs = {}
        if hive_partitioning is not None:
            kwargs["hive_partitioning"] = hive_partitioning
        schema = pl.scan_parquet(str(source), **kwargs).collect_schema()
    except Exception:
        return None, None
    out = {}
    changed = False
    for name, dtype in schema.items():
        if is_cat(dtype):
            out[name] = _STR
            changed = True
        else:
            out[name] = dtype
    value = (out if changed else None, list(schema.names()))
    if key is not None:
        with _SCAN_SCHEMA_CACHE_LOCK:
            _SCAN_SCHEMA_CACHE[key] = value
            _SCAN_SCHEMA_CACHE.move_to_end(key)
            while len(_SCAN_SCHEMA_CACHE) > _SCAN_SCHEMA_CACHE_MAX:
                _SCAN_SCHEMA_CACHE.popitem(last=False)
    return value


def _first_scan_schema_with_string_cats(source, hive_partitioning=None):
    if not isinstance(source, (list, tuple)) or not source:
        return None
    return _cached_scan_schema(source[0], hive_partitioning)[0]


def _scan_parquet_compat(source, **kwargs):
    """Scan parquet while accepting String/Categorical drift across partitions."""
    scan_kwargs = dict(kwargs)
    hive = scan_kwargs.get("hive_partitioning")
    names = None
    if "schema" not in scan_kwargs:
        is_multi = isinstance(source, (list, tuple))
        first = (source[0] if source else None) if is_multi else source
        if first is not None and not isinstance(first, (list, tuple)):
            schema, names = _cached_scan_schema(first, hive_partitioning=hive)
            # Categorical 드리프트 보정은 원래 계약대로 다중 파티션 스캔에만 적용.
            # 단일 파일은 드리프트가 없고, names 캐시는 양쪽 다 재사용한다.
            if schema and is_multi:
                scan_kwargs["schema"] = schema
    if hive:
        # hive 파티션 컬럼은 개별 파일 스키마에 없다 — 컬럼 목록이 불완전할 수
        # 있으므로 이 경우엔 넘기지 않고 기존처럼 lf 에서 직접 읽게 둔다.
        names = None
    opts = _scan_cast_options()
    if opts is not None and "cast_options" not in scan_kwargs:
        scan_kwargs["cast_options"] = opts
    try:
        lf = pl.scan_parquet(source, **scan_kwargs)
    except TypeError:
        scan_kwargs.pop("cast_options", None)
        lf = pl.scan_parquet(source, **scan_kwargs)
    try:
        from core.utils import filter_valid_wafer_ids_lazy
        # 방금 구한 컬럼 이름을 넘겨 두 번째 collect_schema 를 없앤다.
        # (hive 파티션 컬럼은 스캔 뒤에만 보이지만, wafer 컬럼 탐지 용도라 무관)
        return filter_valid_wafer_ids_lazy(lf, names)
    except Exception:
        return lf


import re as _re
_NUM_RE = _re.compile(r"(\d+(?:\.\d+)?)")
_PREFIX_NUM_RE = _re.compile(r"^(\d+(?:\.\d+)*)(?:[_\s-]|$)")


def _version_num_key(raw: str) -> tuple:
    try:
        return tuple(int(p) for p in str(raw).split("."))
    except Exception:
        return (float("inf"),)

def _natural_param_key(name: str):
    """v8.4.4 — prefix 뒤 숫자(정수/소수) 기준 자연 정렬 키 생성.
    예: 'KNOB_12.0_ASV_FOO' → (12.0, '_ASV_FOO', 'KNOB')
    숫자가 없으면 prefix 를 뺀 본문 natural token 기준으로 후순.
    v8.8.14: 내부 문자열 tail 도 자연 정렬(숫자/비숫자 분리)로 안정화 →
      `KNOB_10_FOO` 뒤에 `KNOB_2_FOO` 가 오는 오작동 방지.
    v9.0.3: prefix 는 정렬 tie-breaker 로만 사용.
      여러 prefix(KNOB/MASK/INLINE/VM...)를 같이 볼 때 prefix 그룹이 먼저 묶이지 않고
      prefix 를 제거한 항목명/순번 기준으로 자연정렬된다.
    v9.5.x: 요청에 따라 prefix 를 다시 최상위 정렬 기준으로 복원.
      정렬 순서: KNOB -> MASK -> FAB -> INLINE -> VM -> 기타
    v10.0.x: TAG_ 는 사용자가 공정 번호를 붙여 만든 주석 열이라 예외다.
      앞머리 숫자가 있으면 그 숫자 위치(KNOB 과 같은 rank)로 끼워 넣고,
      숫자가 없을 때만 기타로 뒤에 붙인다 — prefix 최우선 복원 때 TAG 가
      통째로 맨 뒤로 밀려 "번호 위치 유지" 기능이 죽어 있었다.
    """
    if not name: return (99, 1, (), (), "")
    raw = str(name)
    parts = raw.split("_", 1)
    pfx = parts[0] if len(parts) > 1 else ""
    rest = parts[1] if len(parts) > 1 else raw
    # split rest into natural tokens (numbers → version tuple, others → lowercased str)
    tail: list = []
    for tok in _NUM_RE.split(rest):
        if tok == "":
            continue
        if _NUM_RE.fullmatch(tok):
            tail.append(("n", _version_num_key(tok)))
        else:
            tail.append(("s", tok.lower()))

    _PREFIX_RANK = {
        "knob": 0,
        "mask": 1,
        "fab": 2,
        "inline": 3,
        "vm": 4,
    }
    pfx_lower = pfx.lower()
    rank = _PREFIX_RANK.get(pfx_lower, 99)

    # Only the immediate segment after the prefix is the primary process/order
    # key. Numbers buried later in the feature name must not split 1.0/2.0/2.1
    # process-order groups.
    m = _PREFIX_NUM_RE.search(rest)
    if m:
        if pfx_lower == "tag":
            rank = _PREFIX_RANK["knob"]
        return (rank, 0, _version_num_key(m.group(1)), tuple(tail), pfx_lower)
    return (rank, 1, (), tuple(tail), pfx_lower)


# v8.8.14/v9.0.7: ML_TABLE 컬럼 display rename.
#   KNOB 는 원래 feature 명(예: KNOB_1.0 STI)을 유지한다. 적용 공정 정보는
#   ppid_knob.csv + Vehicle_matching.csv detail panel 에서만 보여준다.
#   규칙:
#     KNOB_<feature>   → 표시명 유지
#     INLINE_<item_id>               → 표시명 유지
#     VM_<step_desc>_<item_id>       → 표시명 유지
#   display 이름과 원본 col 이름(`_param`) 을 그대로 보존 → plan/notes/
#   knob_meta lookup 이 깨지지 않음.
def _safe_step_segment(s: str) -> str:
    """step_desc/function_step 값에서 공백/특수문자 제거 → 컬럼명 조각으로 안전하게."""
    if not s:
        return ""
    return _re.sub(r"[^A-Za-z0-9]+", "_", str(s)).strip("_")


_RULE_ORDER_RE = _re.compile(r"^R(\d+)$", _re.I)


def _rule_order_label(raw: object, fallback_idx: int = 1) -> str:
    text = str(raw or "").strip().upper()
    if text == "RO":
        return "RO"
    m = _RULE_ORDER_RE.match(text)
    if m:
        return f"R{int(m.group(1))}"
    if text.isdigit():
        return f"R{int(text)}"
    return text or f"R{max(1, fallback_idx)}"


def _rule_order_sort_key(label: object) -> tuple[int, int, str]:
    text = str(label or "").strip().upper()
    if text == "RO":
        return (1, 0, text)
    m = _RULE_ORDER_RE.match(text)
    if m:
        return (0, int(m.group(1)), text)
    if text.isdigit():
        return (0, int(text), text)
    return (2, 0, text)


def _split_product_core(product: object) -> str:
    text = str(product or "").strip().replace("\\", "/")
    if not text:
        return ""
    text = Path(text).name
    m = _re.search(r"ML_TABLE_", text, flags=_re.I)
    if m:
        text = text[m.end():]
    ext = _re.search(r"\.(?:parquet|csv)(?=$|[\s,，、()[\]{}])", text, flags=_re.I)
    if ext:
        text = text[:ext.start()]
    return text.strip(" \t\r\n,，、()[]{}")


def _product_aliases(product: str) -> set[str]:
    """Soft-landing product matcher for registry/rulebook joins."""
    raw = str(product or "").strip()
    if not raw:
        return set()
    out = {raw.upper()}
    core = _split_product_core(raw)
    if core:
        out.add(core.upper())
    up = core.upper()
    if up.startswith("PRODUCT_A"):
        if up.endswith("0"):
            out.update({"PRODA", "PRODA0", "PRODUCT_A0"})
        elif up.endswith("1"):
            out.update({"PRODA", "PRODA1", "PRODUCT_A1"})
        else:
            out.update({"PRODA", "PRODA0", "PRODA1", "PRODUCT_A0", "PRODUCT_A1"})
    elif up.startswith("PRODUCT_B"):
        out.update({"PRODB", "PRODUCT_B"})
    elif up == "PRODA0":
        out.update({"PRODA", "PRODUCT_A0"})
    elif up == "PRODA1":
        out.update({"PRODA", "PRODUCT_A1"})
    elif up == "PRODA":
        out.update({"PRODA0", "PRODA1", "PRODUCT_A0", "PRODUCT_A1"})
    elif up == "PRODB":
        out.update({"PRODUCT_B"})
    return out


def _product_alias_keys(product: str) -> set[str]:
    return {str(alias or "").strip().casefold() for alias in _product_aliases(product) if str(alias or "").strip()}


def _product_value_matches(product: str, row_product: object, *, allow_common: bool = True) -> bool:
    """Case-insensitive product/alias match for rulebook rows."""
    if not str(product or "").strip():
        return True
    row_values = _product_cell_tokens(row_product)
    if not row_values:
        return allow_common
    product_keys = _product_alias_keys(product)
    return any(row_value.casefold() in product_keys for row_value in row_values)


def _step_matching_product_alias_keys(product: str) -> set[str]:
    raw = str(product or "").strip()
    if not raw:
        return set()
    core = _split_product_core(raw)
    aliases = {raw, core}
    if core:
        aliases.add(f"ML_TABLE_{core}")
    up = core.upper()
    if up == "PRODA0":
        aliases.add("PRODUCT_A0")
    elif up == "PRODA1":
        aliases.add("PRODUCT_A1")
    elif up == "PRODUCT_A0":
        aliases.add("PRODA0")
    elif up == "PRODUCT_A1":
        aliases.add("PRODA1")
    elif up == "PRODB":
        aliases.add("PRODUCT_B")
    elif up == "PRODUCT_B":
        aliases.add("PRODB")
    return {str(alias or "").strip().casefold() for alias in aliases if str(alias or "").strip()}


def _product_cell_tokens(row_product: object) -> list[str]:
    row_value = str(row_product or "").strip()
    if not row_value:
        return []
    return [part.strip() for part in _re.split(r"[,，、]", row_value) if part.strip()]


def _step_matching_product_matches(product: str, row_product: object, *, allow_common: bool = True) -> bool:
    if not str(product or "").strip():
        return True
    row_values = _product_cell_tokens(row_product)
    if not row_values:
        return allow_common
    product_keys = _step_matching_product_alias_keys(product)
    for row_value in row_values:
        row_keys = _step_matching_product_alias_keys(row_value)
        if row_keys and any(key in product_keys for key in row_keys):
            return True
    return False


def _step_desc_match_key(value: object) -> str:
    return _re.sub(r"\s+", " ", str(value or "").strip()).casefold()


_PRODUCT_FILE_EXTS = (".parquet", ".csv")


def _canonical_mltable_product_name(product: str, allow_bare: bool = False) -> str:
    """Return the canonical SplitTable product id for an ML_TABLE file/name."""
    raw = str(product or "").strip()
    if not raw:
        return ""
    if raw.casefold().startswith("ml_table_"):
        tail = raw[len("ML_TABLE_"):].strip()
    elif allow_bare:
        tail = raw
    else:
        return ""
    return f"ML_TABLE_{tail}".upper() if tail else ""


def _is_mltable_product_file(path: Path) -> bool:
    return (
        path.is_file()
        and path.suffix.lower() in _PRODUCT_FILE_EXTS
        and bool(_canonical_mltable_product_name(path.stem))
    )


def _lot_override_for(cfg: dict, product: str) -> dict:
    """Resolve lot_overrides by product name with case-insensitive ML_TABLE matching."""
    overrides = (cfg or {}).get("lot_overrides") or {}
    if not isinstance(overrides, dict):
        return {}
    keys = [str(product or "").strip()]
    canonical = _canonical_mltable_product_name(product, allow_bare=True)
    if canonical:
        keys.append(canonical)
    for key in keys:
        if key and isinstance(overrides.get(key), dict):
            return overrides.get(key) or {}
    folded = {k.casefold() for k in keys if k}
    for key, value in overrides.items():
        if str(key or "").casefold() in folded and isinstance(value, dict):
            return value
    return {}


def _build_col_rename_map(selected_cols: list, product: str) -> dict:
    """raw column name → display name. 매칭 없으면 원본 반환(맵에 키 없음)."""
    return {}


def _color_for_value(val, uniq_map, palette):
    """UI 와 동일하게 unique 값 별 색상 (RGB hex, openpyxl fgColor 포맷 - no #).
    palette 는 색 리스트, uniq_map 은 {value: index}.
    """
    if val is None or val == "":
        return None
    idx = uniq_map.get(val)
    if idx is None: return None
    return palette[idx % len(palette)]


def _detect_lot_wafer(lf, product: str = ""):
    """v8.4.4: product 별 source_config.json 의 lot_overrides 를 우선 참조.
    override 가 실제 schema 에 존재할 때만 사용 (소프트랜딩). 아니면 기본 감지.
    """
    if product:
        try:
            cfg = load_json(SOURCE_CFG, {"lot_overrides": {}}) if SOURCE_CFG.exists() else {}
            ov = _lot_override_for(cfg, product)
            schema_names_list = lf.collect_schema().names() if hasattr(lf, "collect_schema") else list(lf.schema.keys())
            root_col = _ci_resolve_in(ov.get("root_col") or "", schema_names_list) or None
            wf_col = _ci_resolve_in(ov.get("wf_col") or "", schema_names_list) or None
            if root_col or wf_col:
                # Fill missing with auto-detect
                auto_r, auto_w = find_lot_wafer_cols(schema_names_list)
                return (root_col or auto_r, wf_col or auto_w)
        except Exception:
            pass
    try:
        schema_names = lf.collect_schema().names()
    except Exception:
        schema_names = lf.schema
    return find_lot_wafer_cols(schema_names)


# _product_path 결과 메모이즈. 이 함수는 한 요청에서 2~5회 호출되고 1회가 ~3ms 다
# — 파일이 base_root 에 없으면 확장자마다 _find_ci_path(디렉터리 전체 스캔)를 돌린
# 뒤에야 db_base 로 넘어가기 때문이다. 실측에서 **캐시 히트 응답 시간의 42%** 가
# 이 헛도는 디렉터리 스캔이었고, 순수 파이썬이라 GIL 을 쥔 채 돌아 동시 검색에서
# 5배로 증폭됐다.
#
# 무효화는 TTL + 루트 시그니처 2중이다. TTL 만으로는 "제품 파일을 방금 넣었는데
# 404" 가 최대 TTL 만큼 남고, 루트 stat 만으로는 같은 디렉터리에 다른 파일이
# 생겨도 전량 무효화된다. 둘을 같이 보면 새 파일 투입은 루트 mtime 변화로 즉시
# 잡히고, 그 외에는 TTL 안에서 스캔을 건너뛴다.
_PRODUCT_PATH_CACHE: dict[str, tuple[float, tuple, Path]] = {}
_PRODUCT_PATH_CACHE_LOCK = threading.Lock()
_PRODUCT_PATH_TTL = 3.0
_PRODUCT_PATH_CACHE_MAX = 256


def _product_path_roots_sig() -> tuple:
    """base/db 루트의 (mtime, size) — 파일이 새로 들어오면 디렉터리 mtime 이 바뀐다."""
    sig = []
    for root in (_base_root(), _db_base()):
        try:
            st = root.stat()
            sig.append((str(root), st.st_mtime, st.st_size))
        except Exception:
            sig.append((str(root), 0.0, 0))
    return tuple(sig)


def _product_path(product: str):
    """Find product file. v8.4.3 — Base scope (ML_TABLE_PRODA/B etc.) 우선,
    이후 DB 루트(legacy) 로 폴백. ML 중심 설계로 전환.

    결과는 짧게 메모이즈한다(위 _PRODUCT_PATH_CACHE 주석 참고). 404 는 캐시하지
    않는다 — 파일을 넣자마자 다시 물었을 때 없다고 답하면 안 된다.
    """
    cache_key = str(product or "")
    now = time.monotonic()
    roots_sig = _product_path_roots_sig()
    with _PRODUCT_PATH_CACHE_LOCK:
        hit = _PRODUCT_PATH_CACHE.get(cache_key)
        if hit is not None and (now - hit[0]) < _PRODUCT_PATH_TTL and hit[1] == roots_sig:
            return hit[2]

    fp = _product_path_uncached(product)

    with _PRODUCT_PATH_CACHE_LOCK:
        if len(_PRODUCT_PATH_CACHE) >= _PRODUCT_PATH_CACHE_MAX:
            _PRODUCT_PATH_CACHE.clear()
        _PRODUCT_PATH_CACHE[cache_key] = (now, roots_sig, fp)
    return fp


def _clear_product_path_cache() -> None:
    """제품 파일 배치가 바뀌었을 때(업로드/삭제/루트 전환) 즉시 비운다."""
    with _PRODUCT_PATH_CACHE_LOCK:
        _PRODUCT_PATH_CACHE.clear()


def _product_path_uncached(product: str):
    raw = str(product or "").strip()
    canonical = _canonical_mltable_product_name(raw, allow_bare=True)
    names = []
    for name in (raw, canonical):
        if name and name not in names:
            names.append(name)
    base_root = _base_root()
    db_base = _db_base()
    for root in (base_root, db_base):
        if not root or not root.exists():
            continue
        for name in names:
            for ext in _PRODUCT_FILE_EXTS:
                fp = root / f"{name}{ext}"
                if fp.exists():
                    return fp
                ci = _find_ci_path(root, f"{name}{ext}")
                if ci is not None and ci.is_file():
                    return ci
        try:
            targets = {n.casefold() for n in names if n}
            for fp in sorted(root.iterdir(), key=lambda p: p.name.lower()):
                if fp.is_file() and fp.suffix.lower() in _PRODUCT_FILE_EXTS and fp.stem.casefold() in targets:
                    return fp
        except Exception:
            pass
    raise HTTPException(404, f"Product not found: {product}")


def _scan_product_base(product: str):
    """Scan the ML_TABLE file only, without FAB override joins."""
    product = _canonical_mltable_product_name(product, allow_bare=True) or str(product or "").strip()
    ram_lf = _product_ram_cache_lazyframe(product)
    if ram_lf is not None:
        return ram_lf
    fp = _product_path(product)
    if fp.suffix.lower() == ".csv":
        return _cast_cats_lazy(pl.scan_csv(str(fp), infer_schema_length=5000))
    return _cast_cats_lazy(_scan_parquet_compat(str(fp)))


def _scan_product_base_lookup_cache(
    product: str,
    root_lot_id: str = "",
    wafer_ids: str = "",
    runtime_profile: dict | None = None,
):
    """Use the ML_TABLE root lookup cache when a lot-scoped view is available."""
    if not str(root_lot_id or "").strip():
        return None
    fp = _product_path(product)
    _ml_table_lookup.record_root_access(fp, root_lot_id)
    ram_lf = _product_ram_cache_lazyframe(product)
    if ram_lf is not None:
        if runtime_profile is not None:
            runtime_profile["product_cache_hit"] = True
            runtime_profile["root_data_source"] = "product_ram"
        try:
            lot_col, wf_col = _detect_lot_wafer(ram_lf, product)
            return _filter_lot_wafer(ram_lf, lot_col, wf_col, root_lot_id, wafer_ids)
        except Exception as exc:
            logger.debug("Product RAM cache scope filter unavailable product=%s root=%s: %s", product, root_lot_id, exc)
            return ram_lf
    try:
        if fp.suffix.lower() != ".parquet":
            return None
        lf, status = _ml_table_lookup.scan_root_lot_cache(fp, root_lot_id, wafer_ids=wafer_ids, profile=runtime_profile)
        if runtime_profile is not None:
            runtime_profile["root_cache_status"] = status.get("status") or ""
            runtime_profile["root_cache_hit"] = lf is not None
        if lf is not None:
            return _cast_cats_lazy(lf)
    except Exception as exc:
        logger.debug("ML_TABLE lookup cache unavailable product=%s root=%s: %s", product, root_lot_id, exc)
    return None


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _truthy_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    return False


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except Exception:
        return default


def _split_view_raw_fallback_max_bytes() -> int:
    mb = max(0.0, _env_float("FLOW_SPLITTABLE_VIEW_RAW_FALLBACK_MAX_MB", _VIEW_RAW_FALLBACK_MAX_MB_DEFAULT))
    return int(mb * 1024 * 1024)


def _lookup_cache_public_meta(status: dict | None, queued: dict | None = None) -> dict:
    status = status or {}
    queued = queued or {}
    queued_status = str(queued.get("status") or "").strip()
    queued_flag = bool(queued.get("ok") or queued.get("queued") or queued_status in {"queued", "running"})
    meta = status.get("meta") or {}
    return {
        "status": queued_status if queued_flag else str(status.get("status") or ""),
        "has_cache": bool(status.get("has_cache")),
        "source_stale": bool(status.get("source_stale")),
        "job_status": status.get("job_status") or "",
        "queued": queued_flag,
        "root_lot_id_count": int(status.get("root_lot_id_count") or meta.get("root_lot_id_count") or 0),
        "candidate_index": bool(
            status.get("candidate_index")
            or (meta.get("candidate_index") or {}).get("has_index")
        ),
        # 산출물이 스스로 들고 있는 완료 시각/소요. 이벤트 로그가 잘리거나
        # (7일 창) 오프로드·스킵으로 완료 줄이 안 남아도 이 둘은 남는다.
        "built_at": str(meta.get("built_at") or ""),
        "build_seconds": float(meta.get("build_seconds") or 0.0),
    }


def _split_view_should_defer_raw_fallback(fp: Path) -> bool:
    if Path(fp).suffix.lower() != ".parquet":
        return False
    threshold = _split_view_raw_fallback_max_bytes()
    if threshold <= 0:
        return True
    try:
        source_size = int(Path(fp).stat().st_size)
    except Exception:
        source_size = 0
    return bool(source_size >= threshold)


def _lookup_cache_public_meta_for(product: str) -> dict:
    """제품의 lookup 캐시 상태만 (후보 목록 없이) 읽는다.

    `_root_lot_lookup_cache_candidates` 와 달리 candidate index 를 통째로
    읽지 않는다 — 상태 플래그만 필요할 때 쓴다.
    """
    try:
        fp = _product_path(product)
    except Exception:
        return {}
    if Path(fp).suffix.lower() != ".parquet":
        return {}
    try:
        return _lookup_cache_public_meta(_ml_table_lookup.cache_status(fp))
    except Exception:
        return {}


def _root_lot_lookup_cache_candidates(product: str, prefix: str = "", limit: int = 500) -> dict | None:
    try:
        fp = _product_path(product)
    except Exception:
        return None
    if fp.suffix.lower() != ".parquet":
        return None
    # 후보 목록은 stale-while-revalidate가 안전하다. stale 인덱스는 직전 ML_TABLE의
    # 완성 스냅샷이므로 새 lookup 빌드 중 빈 드롭다운만 보여주는 것보다 낫고,
    # 실제 SplitTable 데이터 조회는 여전히 별도의 fresh/stale 정책을 따른다.
    out = _ml_table_lookup.root_lot_candidates_from_lookup_cache(
        fp, prefix=prefix, limit=limit, allow_stale=True)
    out["source_fp"] = fp
    return out


def _product_ram_cache_available() -> bool:
    # 제품 전체 DataFrame RAM 상주는 검색 선행 계층(pivot)을 우회하지 못하면서
    # 메모리만 크게 점유하므로 자동/수동/env opt-in을 모두 폐기한다.
    return False


def _product_ram_cache_scheduler_enabled() -> bool:
    return False


def _product_ram_cache_refresh_minutes() -> int:
    raw = os.environ.get("FLOW_SPLITTABLE_PRODUCT_RAM_CACHE_REFRESH_MINUTES", "")
    if raw == "":
        raw = _match_cache_refresh_minutes()
    try:
        value = int(raw)
    except Exception:
        value = PRODUCT_RAM_CACHE_REFRESH_MINUTES_DEFAULT
    return max(PRODUCT_RAM_CACHE_REFRESH_MINUTES_MIN, min(PRODUCT_RAM_CACHE_REFRESH_MINUTES_MAX, value))


def _product_ram_cache_max_bytes() -> int:
    raw = os.environ.get("FLOW_SPLITTABLE_PRODUCT_RAM_CACHE_MAX_GB", "")
    if raw == "":
        # 톱니바퀴 설정(관리자 UI): 운영/개발 분리 — product_ram_gb(_dev)>0 이면 사용.
        try:
            from core import cache_settings
            _is_dev = _ml_table_lookup._root_ram_cache_use_dev()
            _gb_s = cache_settings.get_float_role("product_ram_gb", _is_dev)
            if _gb_s is not None and _gb_s > 0:
                raw = str(_gb_s)
        except Exception:
            pass
    try:
        gb = float(raw or PRODUCT_RAM_CACHE_MAX_GB_DEFAULT)
    except Exception:
        gb = PRODUCT_RAM_CACHE_MAX_GB_DEFAULT
    if gb <= 0:
        return 0
    budget = int(gb * 1024 * 1024 * 1024)
    try:
        from core import cache_budget
        # 명시값은 이 캐시의 희망 상한이며, 전체 캐시 안전 풀은 항상 적용한다.
        budget = cache_budget.capped("splittable_product_ram", budget)
    except Exception:
        pass
    return budget


def emergency_evict_view_cache(max_bytes: int) -> int:
    """메모리 워치독 긴급 축출 — 오래된 view payload 부터 최대 max_bytes 제거.

    다음 검색은 재계산으로 채워지므로 정확성 영향 없음. 반환: 회수 추정 바이트."""
    global _VIEW_CACHE_BYTES
    if max_bytes <= 0:
        return 0
    freed = 0
    with _VIEW_CACHE_LOCK:
        while _VIEW_CACHE and freed < max_bytes:
            _key, (_hard, _soft, _payload, approx) = _VIEW_CACHE.popitem(last=False)
            approx = int(approx or 0)
            freed += approx
            _VIEW_CACHE_BYTES = max(0, _VIEW_CACHE_BYTES - approx)
    return freed


def emergency_evict_product_ram(max_bytes: int) -> int:
    """메모리 워치독 긴급 축출 — 제품 RAM 캐시를 오래된 순으로 최대 max_bytes 제거.

    상태(_PRODUCT_RAM_CACHE_STATUS)는 남겨 스케줄러가 다음 주기에 재적재한다.
    반환: 회수 추정 바이트."""
    if max_bytes <= 0:
        return 0
    freed = 0
    with _PRODUCT_RAM_CACHE_LOCK:
        while _PRODUCT_RAM_CACHE and freed < max_bytes:
            product = next(iter(_PRODUCT_RAM_CACHE))
            entry = _PRODUCT_RAM_CACHE.pop(product, None) or {}
            try:
                freed += int(entry.get("estimated_bytes") or 0)
            except Exception:
                pass
    return freed


# ── 워치독이 못 보던 보조 캐시들 ────────────────────────────────────────────
# view payload / 제품 RAM / root RAM 세 tier 만 축출 대상이었다. 그런데 제품 RAM
# 은 `_product_ram_cache_available()` 이 False 로 굳어 항상 비어 있고, 검색 경로가
# 실제로 키우는 것은 아래 보조 캐시들이다 — 특히 `_CSV_ROWS_CACHE` 는 CSV 전체
# 행을 dict 로 32개까지 들고 있어 GB 단위가 된다. 축출이 `freed 0.0MB [nothing]`
# 만 반복하던 주된 이유가 이것이다.
_SCRATCH_CACHE_MAX_PROBE_NODES = 20000


def _approx_obj_bytes(obj, *, budget: int = _SCRATCH_CACHE_MAX_PROBE_NODES) -> int:
    """컨테이너를 제한된 노드 수까지만 훑는 근사 크기(bytes).

    정확할 필요는 없다 — 축출 tier 가 "얼마나 회수했는지" 를 과대평가하지만
    않으면 된다. budget 을 넘으면 그때까지 본 평균으로 나머지를 외삽한다.
    """
    import sys

    seen = 0
    total = 0

    def _walk(value) -> int:
        nonlocal seen, total
        if seen >= budget:
            return 0
        seen += 1
        try:
            size = sys.getsizeof(value)
        except Exception:
            return 64
        if isinstance(value, (str, bytes, bytearray, int, float, bool, type(None))):
            return size
        if isinstance(value, dict):
            n = len(value)
            if n and seen + n * 2 > budget:
                # 표본 몇 개로 항목당 평균을 잡고 외삽 — 큰 dict 를 통째로 안 훑는다.
                sample = 0
                taken = 0
                for k, v in value.items():
                    sample += _walk(k) + _walk(v)
                    taken += 1
                    if taken >= 16:
                        break
                return size + (sample // max(1, taken)) * n
            for k, v in value.items():
                size += _walk(k) + _walk(v)
            return size
        if isinstance(value, (list, tuple, set, frozenset)):
            n = len(value)
            if n and seen + n > budget:
                sample = 0
                taken = 0
                for item in value:
                    sample += _walk(item)
                    taken += 1
                    if taken >= 16:
                        break
                return size + (sample // max(1, taken)) * n
            for item in value:
                size += _walk(item)
            return size
        return size

    total = _walk(obj)
    return int(total)


def _scratch_cache_registry() -> list[tuple[str, dict, object]]:
    """(이름, 컨테이너, lock) — 축출해도 재계산으로 복구되는 보조 캐시들.

    순서는 회수 비용이 낮은 순이다. 전부 TTL/시그니처 기반이라 비워도 정확성에
    영향이 없고, 다음 조회가 다시 채운다.
    """
    return [
        ("csv_rows", _CSV_ROWS_CACHE, None),
        ("plan_root_index", _PLAN_ROOT_INDEX_CACHE, _PLAN_ROOT_INDEX_LOCK),
        ("ram_cache_lot_status", _RAM_CACHE_LOT_STATUS_CACHE, _RAM_CACHE_LOT_STATUS_LOCK),
        ("root_latest_step", _ROOT_LATEST_STEP_CACHE, _ROOT_LATEST_STEP_LOCK),
        ("step_order_ctx", _STEP_ORDER_CTX_CACHE, _STEP_ORDER_CTX_LOCK),
        ("latest_status_stats", _LATEST_STATUS_STATS_CACHE, _LATEST_STATUS_STATS_LOCK),
        ("lot_lookup", _LOT_LOOKUP_CACHE, None),
        ("view_product_sig", _VIEW_PRODUCT_SIG_CACHE, _VIEW_PRODUCT_SIG_LOCK),
        ("scan_schema", _SCAN_SCHEMA_CACHE, _SCAN_SCHEMA_CACHE_LOCK),
        ("schema_columns", _SCHEMA_COLUMNS_CACHE, None),
        ("plan_risk", _PLAN_RISK_CACHE, _PLAN_RISK_CACHE_LOCK),
        ("rglob", _RGLOB_CACHE, None),
        ("first_data_file", _FIRST_DATA_FILE_CACHE, None),
        ("db_roots", _DB_ROOTS_CACHE, None),
        ("latest_idx_fresh", _LATEST_IDX_FRESH_CACHE, _LATEST_IDX_FRESH_LOCK),
    ]


def emergency_evict_scratch_caches(max_bytes: int) -> int:
    """메모리 워치독 긴급 축출 — 검색 경로의 보조 캐시를 오래된 순으로 비운다.

    전부 재계산 가능한 파생 캐시다. 반환: 회수 추정 바이트."""
    if max_bytes <= 0:
        return 0
    freed = 0
    for _name, container, lock in _scratch_cache_registry():
        if freed >= max_bytes:
            break
        try:
            ctx = lock if lock is not None else contextlib.nullcontext()
            with ctx:
                # lock 없는 캐시는 다른 스레드가 동시에 넣고 뺀다 — 키 스냅샷을
                # 먼저 뜨고 pop 한다(iterator 중 크기 변경 예외 회피).
                for key in list(container.keys()):
                    if freed >= max_bytes:
                        break
                    value = container.pop(key, None)
                    if value is not None:
                        freed += _approx_obj_bytes(value)
        except Exception as exc:
            logger.warning("scratch cache evict failed (%s): %s", _name, exc)
    return freed


def scratch_cache_sizes() -> dict:
    """관리자 화면/진단용 — 보조 캐시가 지금 얼마나 들고 있는지."""
    out: dict = {}
    for name, container, _lock in _scratch_cache_registry():
        try:
            # 항목마다 새 budget 으로 잰다. 컨테이너를 통째로 넘기면 첫 항목이
            # budget 을 다 쓰고 나머지가 0 으로 잡혀 크게 과소평가된다.
            total = sum(_approx_obj_bytes(value) for value in list(container.values()))
            out[name] = {"entries": len(container), "bytes": int(total)}
        except Exception:
            out[name] = {"entries": 0, "bytes": 0}
    return out


def _product_ram_cache_products(product: str = "") -> list[str]:
    raw = str(product or "").strip()
    if raw:
        return [_canonical_mltable_product_name(raw, allow_bare=True) or raw]
    try:
        return [p.get("name") for p in list_products().get("products", []) if p.get("name")]
    except Exception:
        return []


def _product_ram_cache_source(product: str) -> tuple[str, Path]:
    canonical = _canonical_mltable_product_name(product, allow_bare=True) or str(product or "").strip()
    return canonical, _product_path(canonical)


def _product_ram_cache_estimated_bytes(df: pl.DataFrame) -> int:
    try:
        return int(df.estimated_size())
    except Exception:
        try:
            return int(df.height) * max(1, len(df.columns)) * 16
        except Exception:
            return 0


def _product_ram_cache_source_memory_estimate(fp: Path) -> int:
    """collect 전에 계산하는 보수적 메모리 추정치.

    parquet 파일 크기는 압축 크기라 200MB 파일이 RAM에서 수 GB로 커질 수 있다.
    가능하면 row-group의 uncompressed byte 합을 쓰고 약간의 DataFrame overhead를
    더한다. metadata를 읽을 수 없을 때만 파일 크기로 폴백한다.
    """
    try:
        source_bytes = int(Path(fp).stat().st_size)
    except Exception:
        source_bytes = 0
    uncompressed = 0
    if Path(fp).suffix.lower() == ".parquet":
        try:
            import pyarrow.parquet as pq  # type: ignore

            meta = pq.ParquetFile(str(fp)).metadata
            for row_group_idx in range(meta.num_row_groups):
                row_group = meta.row_group(row_group_idx)
                for col_idx in range(row_group.num_columns):
                    uncompressed += int(row_group.column(col_idx).total_uncompressed_size or 0)
        except Exception:
            uncompressed = 0
    overhead = max(1.0, min(3.0, _env_float("FLOW_SPLITTABLE_PRODUCT_RAM_ESTIMATE_FACTOR", 1.25)))
    return int(max(source_bytes, uncompressed) * overhead)


def _product_ram_cache_total_bytes_locked(exclude_product: str = "") -> int:
    exclude = str(exclude_product or "").strip().upper()
    total = 0
    for product, entry in _PRODUCT_RAM_CACHE.items():
        if exclude and product.upper() == exclude:
            continue
        try:
            total += int(entry.get("estimated_bytes") or 0)
        except Exception:
            pass
    return total


def _read_product_ram_cache_frame(fp: Path) -> pl.DataFrame:
    if fp.suffix.lower() == ".csv":
        lf = pl.scan_csv(str(fp), infer_schema_length=5000)
    else:
        lf = _scan_parquet_compat(str(fp))
    return _cast_cats_lazy(lf).collect()


def _product_ram_cache_is_refreshing(product: str = "") -> bool:
    raw = str(product or "").strip()
    with _PRODUCT_RAM_CACHE_LOCK:
        if not raw:
            return bool(_PRODUCT_RAM_CACHE_REFRESHING)
        canonical = _canonical_mltable_product_name(raw, allow_bare=True) or raw
        return canonical in _PRODUCT_RAM_CACHE_REFRESHING or "*" in _PRODUCT_RAM_CACHE_REFRESHING


def _product_ram_cache_mark_status(product: str, updates: dict) -> None:
    canonical = _canonical_mltable_product_name(product, allow_bare=True) or str(product or "").strip()
    if not canonical:
        return
    with _PRODUCT_RAM_CACHE_LOCK:
        cur = dict(_PRODUCT_RAM_CACHE_STATUS.get(canonical) or {})
        cur.update(updates)
        cur["product"] = canonical
        _PRODUCT_RAM_CACHE_STATUS[canonical] = cur


def _product_ram_cache_entry(product: str) -> dict | None:
    if not _product_ram_cache_available():
        return None
    canonical = _canonical_mltable_product_name(product, allow_bare=True) or str(product or "").strip()
    if not canonical:
        return None
    with _PRODUCT_RAM_CACHE_LOCK:
        return _PRODUCT_RAM_CACHE.get(canonical)


def _product_ram_cache_lazyframe(product: str):
    entry = _product_ram_cache_entry(product)
    if not entry:
        return None
    df = entry.get("df")
    if df is None:
        return None
    try:
        return _cast_cats_lazy(df.lazy())
    except Exception as exc:
        logger.debug("Product RAM cache lazyframe failed product=%s: %s", product, exc)
        return None


def _product_ram_cache_public_meta(product: str, *, include_detail: bool = False) -> dict:
    canonical = _canonical_mltable_product_name(product, allow_bare=True) or str(product or "").strip()
    source_sig = ("", 0.0, 0)
    source_path = ""
    source_fp = None
    try:
        canonical, fp = _product_ram_cache_source(canonical)
        source_fp = fp
        source_path = str(fp)
        source_sig = _path_cache_sig(fp)
    except Exception:
        pass
    with _PRODUCT_RAM_CACHE_LOCK:
        entry = _PRODUCT_RAM_CACHE.get(canonical)
        status = dict(_PRODUCT_RAM_CACHE_STATUS.get(canonical) or {})
    stale = bool(entry and entry.get("source_sig") != source_sig)
    out = {
        "product": canonical,
        "enabled": _product_ram_cache_available(),
        "hit": bool(entry and _product_ram_cache_available()),
        "stale": stale,
        "refreshing": _product_ram_cache_is_refreshing(canonical),
        "loaded_at": entry.get("loaded_at", "") if entry else "",
        "row_count": int(entry.get("row_count") or 0) if entry else 0,
        "estimated_mb": round(float(entry.get("estimated_bytes") or 0) / (1024 * 1024), 3) if entry else 0.0,
        "source_mtime": source_sig[1] or None,
        "source_size": source_sig[2] or 0,
        "skipped": bool(status.get("skipped")) if not entry else False,
        "root_lot_cache": _ml_table_lookup.root_ram_cache_status(source_fp, include_detail=False),
    }
    if include_detail:
        out.update({
            "source_path": source_path,
            "source_sig": source_sig,
            "cache_source_sig": entry.get("source_sig") if entry else None,
            "error": status.get("error") or "",
            "skip_reason": status.get("skip_reason") or "",
            "last_refresh_at": status.get("last_refresh_at") or "",
            "root_lot_cache": _ml_table_lookup.root_ram_cache_status(source_fp, include_detail=True),
        })
    return out


def _product_ram_cache_response_meta(product: str) -> dict:
    meta = _product_ram_cache_public_meta(product, include_detail=False)
    return {
        "hit": meta.get("hit", False),
        "stale": meta.get("stale", False),
        "refreshing": meta.get("refreshing", False),
        "loaded_at": meta.get("loaded_at", ""),
        "row_count": meta.get("row_count", 0),
        "estimated_mb": meta.get("estimated_mb", 0.0),
        "source_mtime": meta.get("source_mtime"),
        "root_lot_cache": meta.get("root_lot_cache") or {},
    }


def _product_ram_cache_view_signature(product: str) -> tuple:
    if not _product_ram_cache_available():
        return ("product_ram_cache", "disabled")
    canonical = _canonical_mltable_product_name(product, allow_bare=True) or str(product or "").strip()
    with _PRODUCT_RAM_CACHE_LOCK:
        entry = _PRODUCT_RAM_CACHE.get(canonical)
        status = dict(_PRODUCT_RAM_CACHE_STATUS.get(canonical) or {})
    if entry:
        return (
            "product_ram_cache",
            canonical,
            entry.get("source_sig"),
            entry.get("loaded_epoch"),
            entry.get("estimated_bytes"),
        )
    return (
        "product_ram_cache",
        canonical,
        status.get("last_refresh_epoch"),
        status.get("skipped"),
        status.get("skip_reason"),
    )


def _refresh_product_ram_cache_products(products: list[str], force: bool = False) -> dict:
    products = [p for p in products if str(p or "").strip()]
    results: list[dict] = []
    if not _product_ram_cache_available():
        return {
            "ok": False,
            "products": [{"product": p, "ok": False, "skipped": True, "reason": "disabled"} for p in products],
            "interval_minutes": _product_ram_cache_refresh_minutes(),
        }
    max_bytes = _product_ram_cache_max_bytes()
    with _PRODUCT_RAM_CACHE_BUILD_LOCK:
        for raw_product in products:
            # 사용자 요청이 진행 중이면 다음 제품 로드를 미룬다 (백그라운드 양보).
            from core import request_priority
            request_priority.yield_to_users(max_wait_sec=15.0)
            canonical = _canonical_mltable_product_name(raw_product, allow_bare=True) or str(raw_product or "").strip()
            result = {"product": canonical, "ok": False, "skipped": False, "row_count": 0}
            try:
                canonical, fp = _product_ram_cache_source(canonical)
                result["product"] = canonical
                result["source_path"] = str(fp)
                source_sig = _path_cache_sig(fp)
                with _PRODUCT_RAM_CACHE_LOCK:
                    current = _PRODUCT_RAM_CACHE.get(canonical)
                    if current and not force and current.get("source_sig") == source_sig:
                        result.update({
                            "ok": True,
                            "skipped": True,
                            "reason": "fresh",
                            "row_count": int(current.get("row_count") or 0),
                            "estimated_mb": round(float(current.get("estimated_bytes") or 0) / (1024 * 1024), 3),
                        })
                        results.append(result)
                        continue
                    used_bytes = _product_ram_cache_total_bytes_locked(exclude_product=canonical)
                estimated_source_bytes = _product_ram_cache_source_memory_estimate(fp)
                remaining_budget = max(0, max_bytes - used_bytes) if max_bytes else 0
                if max_bytes and estimated_source_bytes > remaining_budget:
                    reason = "memory_budget_precheck"
                    _product_ram_cache_mark_status(canonical, {
                        "skipped": True,
                        "skip_reason": reason,
                        "error": "",
                        "last_refresh_at": datetime.datetime.now().isoformat(timespec="seconds"),
                        "last_refresh_epoch": time.time(),
                    })
                    result.update({
                        "skipped": True,
                        "reason": reason,
                        "estimated_mb": round(estimated_source_bytes / (1024 * 1024), 3),
                    })
                    results.append(result)
                    continue
                try:
                    from core.runtime_limits import process_memory_high, system_memory_snapshot
                    if process_memory_high():
                        reason = "process_memory_high"
                        _product_ram_cache_mark_status(canonical, {
                            "skipped": True,
                            "skip_reason": reason,
                            "error": "",
                            "last_refresh_at": datetime.datetime.now().isoformat(timespec="seconds"),
                            "last_refresh_epoch": time.time(),
                        })
                        result.update({"skipped": True, "reason": reason})
                        results.append(result)
                        continue
                    memory = system_memory_snapshot()
                    available_gb = float(memory.get("system_memory_available_gb") or 0.0)
                    reserve_gb = float(memory.get("system_memory_min_available_gb") or 2.0)
                    headroom_bytes = int(max(0.0, available_gb - reserve_gb) * (1024 ** 3))
                    if headroom_bytes > 0 and estimated_source_bytes > headroom_bytes:
                        reason = "host_memory_headroom_precheck"
                        _product_ram_cache_mark_status(canonical, {
                            "skipped": True,
                            "skip_reason": reason,
                            "error": "",
                            "last_refresh_at": datetime.datetime.now().isoformat(timespec="seconds"),
                            "last_refresh_epoch": time.time(),
                        })
                        result.update({
                            "skipped": True,
                            "reason": reason,
                            "estimated_mb": round(estimated_source_bytes / (1024 * 1024), 3),
                        })
                        results.append(result)
                        continue
                except Exception:
                    pass
                with _PRODUCT_RAM_CACHE_LOCK:
                    _PRODUCT_RAM_CACHE_REFRESHING.add(canonical)
                    # stale 확정(여기 도달 = fresh 아님) — 새 프레임을 읽기 전에
                    # 옛 프레임을 먼저 놓는다. 예전에는 old df + new df 가 로드
                    # 구간 동안 공존해 제품당 2배 스파이크가 났다 (예산 계산은
                    # exclude_product 로 old 를 빼고 보는데 실제로는 남아 있었음).
                    # 해제 후 로드 실패 시 다음 주기까지 캐시 미스 → 디스크 스캔
                    # 폴백이라 정확성 영향 없음.
                    _PRODUCT_RAM_CACHE.pop(canonical, None)
                try:
                    gc.collect()
                except Exception:
                    pass
                df = None
                try:
                    df = _read_product_ram_cache_frame(fp)
                    estimated_bytes = _product_ram_cache_estimated_bytes(df)
                    with _PRODUCT_RAM_CACHE_LOCK:
                        used_bytes = _product_ram_cache_total_bytes_locked(exclude_product=canonical)
                    if max_bytes and used_bytes + estimated_bytes > max_bytes:
                        reason = "memory_budget"
                        _product_ram_cache_mark_status(canonical, {
                            "skipped": True,
                            "skip_reason": reason,
                            "error": "",
                            "last_refresh_at": datetime.datetime.now().isoformat(timespec="seconds"),
                            "last_refresh_epoch": time.time(),
                        })
                        result.update({
                            "skipped": True,
                            "reason": reason,
                            "estimated_mb": round(estimated_bytes / (1024 * 1024), 3),
                        })
                        results.append(result)
                        continue
                    now = time.time()
                    loaded_at = datetime.datetime.fromtimestamp(now).isoformat(timespec="seconds")
                    entry = {
                        "version": PRODUCT_RAM_CACHE_VERSION,
                        "product": canonical,
                        "source_path": str(fp),
                        "source_sig": source_sig,
                        "df": df,
                        "row_count": int(df.height),
                        "estimated_bytes": int(estimated_bytes),
                        "loaded_at": loaded_at,
                        "loaded_epoch": now,
                        "error": "",
                        "skipped": False,
                    }
                    with _PRODUCT_RAM_CACHE_LOCK:
                        _PRODUCT_RAM_CACHE[canonical] = entry
                        _PRODUCT_RAM_CACHE_STATUS[canonical] = {
                            "product": canonical,
                            "skipped": False,
                            "skip_reason": "",
                            "error": "",
                            "last_refresh_at": loaded_at,
                            "last_refresh_epoch": now,
                        }
                    result.update({
                        "ok": True,
                        "row_count": int(df.height),
                        "estimated_mb": round(estimated_bytes / (1024 * 1024), 3),
                        "loaded_at": loaded_at,
                    })
                    df = None
                    _LOT_LOOKUP_CACHE.clear()
                    _invalidate_root_lot_pool()
                    _clear_split_view_cache_product(canonical)
                finally:
                    with _PRODUCT_RAM_CACHE_LOCK:
                        _PRODUCT_RAM_CACHE_REFRESHING.discard(canonical)
                    if df is not None:
                        try:
                            del df
                        except Exception:
                            pass
                results.append(result)
            except Exception as e:
                with _PRODUCT_RAM_CACHE_LOCK:
                    _PRODUCT_RAM_CACHE_REFRESHING.discard(canonical)
                reason = f"{type(e).__name__}: {e}"
                logger.warning("SplitTable product RAM cache refresh failed (product=%s) %s", canonical, reason, exc_info=True)
                _product_ram_cache_mark_status(canonical, {
                    "skipped": False,
                    "skip_reason": "",
                    "error": reason,
                    "last_refresh_at": datetime.datetime.now().isoformat(timespec="seconds"),
                    "last_refresh_epoch": time.time(),
                })
                result["reason"] = reason
                results.append(result)
            try:
                gc.collect()
            except Exception:
                pass
        # 제품 목록에서 빠진(삭제/개명) 항목은 갱신 대상이 다시 오지 않아 영구
        # 잔존한다 — 전체 갱신일 때만 정리 (단일 제품 호출이 남을 지우면 안 됨).
        if len(products) > 1:
            keep = {str(r.get("product") or "") for r in results}
            with _PRODUCT_RAM_CACHE_LOCK:
                for stale_key in [k for k in _PRODUCT_RAM_CACHE if k not in keep]:
                    _PRODUCT_RAM_CACHE.pop(stale_key, None)
                    _PRODUCT_RAM_CACHE_STATUS.pop(stale_key, None)
    return {
        "ok": any(r.get("ok") for r in results),
        "products": results,
        "interval_minutes": _product_ram_cache_refresh_minutes(),
        "max_gb": round(_product_ram_cache_max_bytes() / (1024 ** 3), 3) if _product_ram_cache_max_bytes() else 0,
    }


def refresh_product_ram_cache(product: str = "", force: bool = False) -> dict:
    return _refresh_product_ram_cache_products(_product_ram_cache_products(product), force=force)


def _product_ram_cache_job_status() -> dict:
    with _PRODUCT_RAM_CACHE_JOB_LOCK:
        out = dict(_PRODUCT_RAM_CACHE_JOB_STATE)
        out["products"] = [dict(r) for r in (_PRODUCT_RAM_CACHE_JOB_STATE.get("products") or [])]
        out["order"] = list(_PRODUCT_RAM_CACHE_JOB_STATE.get("order") or [])
    return out


def _product_ram_cache_job_update(**updates) -> None:
    with _PRODUCT_RAM_CACHE_JOB_LOCK:
        _PRODUCT_RAM_CACHE_JOB_STATE.update(updates)


def _begin_product_ram_cache_job(products: list[str], force: bool, reason: str) -> tuple[bool, dict]:
    now = datetime.datetime.now().isoformat(timespec="seconds")
    with _PRODUCT_RAM_CACHE_JOB_LOCK:
        if _PRODUCT_RAM_CACHE_JOB_STATE.get("running"):
            status = dict(_PRODUCT_RAM_CACHE_JOB_STATE)
            status["products"] = [dict(r) for r in (_PRODUCT_RAM_CACHE_JOB_STATE.get("products") or [])]
            status["order"] = list(_PRODUCT_RAM_CACHE_JOB_STATE.get("order") or [])
            return False, status
        _PRODUCT_RAM_CACHE_JOB_STATE.clear()
        _PRODUCT_RAM_CACHE_JOB_STATE.update({
            "running": True,
            "queued": True,
            "force": bool(force),
            "reason": reason or "manual",
            "started_at": now,
            "finished_at": "",
            "current_product": "",
            "total": len(products),
            "done": 0,
            "ok_count": 0,
            "failed_count": 0,
            "skipped_count": 0,
            "last_error": "",
            "products": [],
            "order": list(products),
        })
        status = dict(_PRODUCT_RAM_CACHE_JOB_STATE)
        status["products"] = []
        status["order"] = list(products)
        return True, status


def _append_product_ram_cache_job_rows(rows: list[dict]) -> None:
    if not rows:
        return
    with _PRODUCT_RAM_CACHE_JOB_LOCK:
        current = [dict(r) for r in (_PRODUCT_RAM_CACHE_JOB_STATE.get("products") or [])]
        current.extend(dict(r) for r in rows)
        _PRODUCT_RAM_CACHE_JOB_STATE["products"] = current[-500:]
        _PRODUCT_RAM_CACHE_JOB_STATE["done"] = int(_PRODUCT_RAM_CACHE_JOB_STATE.get("done") or 0) + len(rows)
        _PRODUCT_RAM_CACHE_JOB_STATE["ok_count"] = int(_PRODUCT_RAM_CACHE_JOB_STATE.get("ok_count") or 0) + len([r for r in rows if r.get("ok")])
        _PRODUCT_RAM_CACHE_JOB_STATE["failed_count"] = int(_PRODUCT_RAM_CACHE_JOB_STATE.get("failed_count") or 0) + len([r for r in rows if not r.get("ok") and not r.get("skipped")])
        _PRODUCT_RAM_CACHE_JOB_STATE["skipped_count"] = int(_PRODUCT_RAM_CACHE_JOB_STATE.get("skipped_count") or 0) + len([r for r in rows if r.get("skipped")])
        for row in reversed(rows):
            if row.get("reason"):
                _PRODUCT_RAM_CACHE_JOB_STATE["last_error"] = str(row.get("reason") or "")
                break


def _run_started_product_ram_cache_job(products: list[str], force: bool, reason: str = "manual") -> dict:
    try:
        for product in products:
            if _PRODUCT_RAM_CACHE_STOP.is_set():
                break
            _product_ram_cache_job_update(current_product=product, queued=False)
            result = _refresh_product_ram_cache_products([product], force=force)
            _append_product_ram_cache_job_rows(result.get("products") or [])
    finally:
        _product_ram_cache_job_update(
            running=False,
            queued=False,
            current_product="",
            finished_at=datetime.datetime.now().isoformat(timespec="seconds"),
        )
    status = _product_ram_cache_job_status()
    return {
        "ok": bool(status.get("ok_count")),
        "queued": False,
        "products": status.get("products") or [],
        "interval_minutes": _product_ram_cache_refresh_minutes(),
        "job": status,
        "reason": reason,
    }


def enqueue_product_ram_cache_refresh(product: str = "", force: bool = True, reason: str = "manual") -> dict:
    """제품 원본 RAM 캐시 갱신을 서버 스캔 게이트에 넣고 즉시 반환한다.

    job state 는 큐에서 꺼내 실제로 시작할 때 잡는다 — 대기 중 running 표시로
    다른 스캔이 '이미 실행 중'으로 오판하는 것을 막는다."""
    products = _product_ram_cache_products(product)
    label = f"제품 원본 RAM 캐시 갱신 ({product or '전체 제품'})"

    def _start() -> dict:
        started, status = _begin_product_ram_cache_job(products, force=force, reason=reason)
        if not started:
            return {
                "ok": True,
                "skipped": True,
                "job": status,
                "detail": "SplitTable product RAM cache refresh is already running.",
            }
        return _run_started_product_ram_cache_job(products, force, reason)

    out = _submit_scan("product_ram", label, _start, product=product,
                       source="scheduler" if reason == "scheduler" else "manual",
                       dedupe_key=f"product_ram:{reason}:{product}")
    return {
        **out,
        "products": [{"product": p, "queued": True} for p in products],
        "interval_minutes": _product_ram_cache_refresh_minutes(),
        "job": _product_ram_cache_job_status(),
    }


def _product_ram_cache_loop() -> None:
    global _PRODUCT_RAM_CACHE_NEXT_TICK_AT
    while not _PRODUCT_RAM_CACHE_STOP.is_set():
        try:
            # 예약 갱신도 스캔 게이트를 통과한다 — 예전엔 job state 조차 잡지 않고
            # 바로 적재를 시작해서, 수동 스캔 2/3 단계와 그대로 겹칠 수 있었다.
            if _product_ram_cache_products(""):
                enqueue_product_ram_cache_refresh(product="", force=False, reason="scheduler")
        except Exception as e:
            logger.warning("SplitTable product RAM cache scheduler tick failed: %s", e)
        wait_s = max(60.0, _product_ram_cache_refresh_minutes() * 60.0)
        _PRODUCT_RAM_CACHE_NEXT_TICK_AT = datetime.datetime.fromtimestamp(
            time.time() + wait_s).isoformat(timespec="seconds")
        while wait_s > 0 and not _PRODUCT_RAM_CACHE_STOP.is_set():
            step = min(wait_s, 60.0)
            _PRODUCT_RAM_CACHE_STOP.wait(step)
            wait_s -= step


def start_product_ram_cache_scheduler() -> bool:
    global _PRODUCT_RAM_CACHE_THREAD, _PRODUCT_RAM_CACHE_STARTED
    if _PRODUCT_RAM_CACHE_STARTED:
        return False
    # Product rotation owns the complete persistent-cache sweep.  Keeping the
    # legacy product-RAM timer alive can insert an unrelated product between
    # two stages of the active product and makes the queue look cache-kind
    # driven again.
    if _auto_product_cache_enabled():
        logger.info("SplitTable product RAM timer retired; product rotation owns refresh")
        return False
    if not _product_ram_cache_scheduler_enabled():
        logger.info("SplitTable product RAM cache scheduler disabled")
        return False
    _PRODUCT_RAM_CACHE_STOP.clear()
    _PRODUCT_RAM_CACHE_THREAD = threading.Thread(
        target=_product_ram_cache_loop,
        name="splittable-product-ram-cache",
        daemon=True,
    )
    _PRODUCT_RAM_CACHE_THREAD.start()
    _PRODUCT_RAM_CACHE_STARTED = True
    logger.info("SplitTable product RAM cache scheduler started (interval=%sm)", _product_ram_cache_refresh_minutes())
    return True


# ── KNOB view payload 프리워밍 ────────────────────────────────────────────
# SplitTable 에서 압도적으로 많이 보는 것이 KNOB prefix 이고, 우선 lot 은 이미
# 관리자가 등록해 둔다. 그 조합(우선 lot × prefix=KNOB)의 view payload 를 미리
# 계산해 두면 가장 흔한 검색이 **cold 계산 자체를 건너뛰고** 캐시 HIT 로 끝난다.
# HIT 는 cold 레인에 줄서지 않으므로(self-gated 경로) 동시 사용자 대기도 함께 준다.
# 계산을 빠르게 하는 게 아니라 없애는 접근이라 다중 조회에서 효과가 가장 크다.
_KNOB_PREWARM_STOP = threading.Event()
_KNOB_PREWARM_THREAD = None
_KNOB_PREWARM_STARTED = False
_KNOB_PREWARM_LAST: dict = {"at": "", "warmed": 0, "skipped": 0, "failed": 0, "reason": ""}


def _knob_prewarm_enabled() -> bool:
    return str(os.environ.get("FLOW_SPLITTABLE_KNOB_PREWARM", "1")).strip().lower() not in {"0", "false", "no", "off"}


def _knob_prewarm_interval_sec() -> float:
    return max(60.0, min(24 * 3600.0, _env_float("FLOW_SPLITTABLE_KNOB_PREWARM_INTERVAL_SEC", 1800.0)))


def _knob_prewarm_max_lots() -> int:
    try:
        n = int(float(os.environ.get("FLOW_SPLITTABLE_KNOB_PREWARM_MAX_LOTS", "") or 50))
    except Exception:
        n = 50
    return max(1, min(500, n))


def _knob_prewarm_targets() -> list[tuple[str, str]]:
    """(product, root_lot_id) 목록 — 캐싱 활성화된 우선 lot 만, 상한 적용."""
    out: list[tuple[str, str]] = []
    try:
        data = _load_priority_lots_file()
    except Exception:
        return out
    limit = _knob_prewarm_max_lots()
    for product, lots in (data or {}).items():
        if not isinstance(lots, list):
            continue
        for entry in lots:
            if not isinstance(entry, dict):
                continue
            if entry.get("cache_enabled") is False:
                continue
            lot = str(entry.get("lot_id") or "").strip()
            if not lot:
                continue
            out.append((str(product or "").strip(), lot))
            if len(out) >= limit:
                return out
    return out


def _knob_prewarm_requests() -> list[tuple[str, str, str]]:
    """PI's managed FAB lots first, then legacy root-lot priorities.

    Preserve the identifier kind: truncating a FAB lot into a root makes the
    cache key differ from the actual LOT-management request.
    """
    out = []
    seen = set()
    limit = _knob_prewarm_max_lots()
    tables = PATHS.data_root / "lot_management" / "tables"
    for path in sorted(tables.glob("*.json")):
        doc = load_json_cached(path, {})
        if not isinstance(doc, dict):
            continue
        product = str(doc.get("product") or "").strip()
        if not product:
            continue
        for row in doc.get("rows") or []:
            if not isinstance(row, dict) or not isinstance(row.get("values"), dict):
                continue
            lot = str(row["values"].get("lot_id") or "").strip()
            target = (product, "", lot)
            if lot and target not in seen:
                seen.add(target)
                out.append(target)
                if len(out) >= limit:
                    return out
    for product, root in _knob_prewarm_targets():
        target = (product, root, "")
        if target not in seen:
            seen.add(target)
            out.append(target)
            if len(out) >= limit:
                break
    return out


def _knob_prewarm_once() -> dict:
    warmed = skipped = failed = 0
    reason = ""
    targets = _knob_prewarm_requests()
    if not targets:
        reason = "우선 lot 없음"
    for product, root, fab_lot in targets:
        if _KNOB_PREWARM_STOP.is_set():
            reason = "중지 요청"
            break
        try:
            from core.runtime_limits import process_memory_high
            if process_memory_high():
                skipped += len(targets) - (warmed + skipped + failed)
                reason = "메모리 압박으로 중단"
                break
        except Exception:
            pass
        try:
            key = _split_view_cache_key(product, root, "", "KNOB", "", "all", "all", fab_lot, "")
            hard, soft = _split_view_cache_dep_signature(product)
            freshness, cached = _split_view_cache_get(key, hard, soft)
            if cached is not None and freshness == "fresh":
                skipped += 1
                continue
            params = {
                "product": product, "root_lot_id": root, "wafer_ids": "", "prefix": "KNOB",
                "custom_name": "", "view_mode": "all", "history_mode": "all",
                "fab_lot_id": fab_lot, "custom_cols": "",
            }
            result = _view_revalidate_execute(key, params) or {}
            hard, soft = _split_view_cache_dep_signature(product)
            _freshness, warmed_payload = _split_view_cache_get(key, hard, soft)
            if warmed_payload is not None:
                warmed += 1
            elif result.get("deferred") or result.get("queued"):
                skipped += 1
                reason = "개발 worker 큐에서 대기 중"
            else:
                failed += 1
        except Exception as exc:
            failed += 1
            logger.debug("KNOB prewarm 실패 (%s/%s): %s", product, root, exc)
    result = {
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
        "warmed": warmed, "skipped": skipped, "failed": failed,
        "targets": len(targets), "reason": reason,
    }
    _KNOB_PREWARM_LAST.update(result)
    if warmed or failed:
        try:
            from core import cache_event_log
            cache_event_log.record(
                "cache_op", "knob_prewarm", ok=(failed == 0),
                detail={"warmed": warmed, "skipped": skipped, "failed": failed,
                        "targets": len(targets), "reason": reason})
        except Exception:
            pass
    return result


def _knob_prewarm_loop() -> None:
    # 기동 직후는 캐시/스케줄러가 자리를 잡을 시간을 준다.
    if _KNOB_PREWARM_STOP.wait(_env_float("FLOW_SPLITTABLE_KNOB_PREWARM_START_DELAY_SEC", 180.0)):
        return
    while not _KNOB_PREWARM_STOP.is_set():
        try:
            _knob_prewarm_once()
        except Exception as exc:
            logger.warning("KNOB prewarm loop 오류: %s", exc)
        if _KNOB_PREWARM_STOP.wait(_knob_prewarm_interval_sec()):
            return


def start_knob_prewarmer() -> bool:
    global _KNOB_PREWARM_THREAD, _KNOB_PREWARM_STARTED
    if _KNOB_PREWARM_STARTED:
        return False
    if not _knob_prewarm_enabled():
        logger.info("SplitTable KNOB prewarmer disabled (FLOW_SPLITTABLE_KNOB_PREWARM=0)")
        return False
    _KNOB_PREWARM_STOP.clear()
    _KNOB_PREWARM_THREAD = threading.Thread(
        target=_knob_prewarm_loop, name="splittable-knob-prewarm", daemon=True)
    _KNOB_PREWARM_THREAD.start()
    _KNOB_PREWARM_STARTED = True
    logger.info("SplitTable KNOB prewarmer started (interval=%.0fs, max %d lots)",
                _knob_prewarm_interval_sec(), _knob_prewarm_max_lots())
    return True


def _strip_non_authoritative_fab_fields(lf, product: str):
    """Hide FAB-only identifiers from ML tables unless they came from FAB source.

    `fab_lot_id` is an operational FAB identifier. If FAB override/source is off or
    failed, SplitTable should not surface a stale ML-side copy because users assume
    it came from live/real FAB lineage.  Do not synthesize it from ML_TABLE LOT_ID.
    """
    if not product or not str(product).casefold().startswith("ml_table_"):
        return lf
    try:
        names = lf.collect_schema().names()
    except Exception:
        return lf
    drop_cols = [n for n in names if n.casefold() == "fab_lot_id"]
    return lf.drop(drop_cols) if drop_cols else lf


def _view_identity_cols(schema_names, lot_col: str, wf_col: str, fab_lot_col: str = "") -> set[str]:
    """행이 아니라 헤더로 쓰이는 식별 컬럼(root lot / wafer / fab lot).

    fab lot 컬럼은 wafer 헤더 그룹으로 이미 나가는데 데이터 컬럼 목록에도 남아 있어서
    prefix=FAB 조회에 `fab_lot_id` 행이 섞여 나왔다. 헤더에서 keep 하는 fab 컬럼과
    같은 규칙으로 걸러 두 곳이 어긋나지 않게 한다.
    """
    names = list(schema_names or [])
    out = {c for c in (lot_col, wf_col) if c}
    fab_ident = "fab_lot_id" if "fab_lot_id" in names else (
        _ci_resolve_in(fab_lot_col, names)
        or _pick_first_present_ci(_FAB_COL_CANDIDATES, names)
        or ""
    )
    if fab_ident:
        out.add(fab_ident)
    return out


def _view_data_columns(schema_names, lot_col: str, wf_col: str, fab_lot_col: str = "") -> list:
    """SplitTable 행/컬럼 선택기의 후보 = 식별 컬럼을 뺀 나머지."""
    identity = _view_identity_cols(schema_names, lot_col, wf_col, fab_lot_col)
    return [c for c in (schema_names or []) if c not in identity]


def _known_column_prefix_tokens() -> set[str]:
    """등록된 prefix(톱니바퀴) + 내장 overlay prefix 집합. 모두 대문자."""
    out = set(DEFAULT_PREFIXES)
    out.update(getattr(_ml_table_lookup, "CANDIDATE_COLUMN_PREFIXES", ()))
    out.add(CUSTOM_TAG_PREFIX)
    out.add(MANAGEMENT_ROW_PREFIX)
    try:
        out.update(_load_prefixes())
    except Exception:
        pass
    return {p for p in out if p}


def _has_known_prefixed_columns(all_data_cols) -> bool:
    """원천 컬럼이 prefix 체계(KNOB_/VM_/ET_ ...)를 쓰고 있는지."""
    tokens = _known_column_prefix_tokens()
    if not tokens:
        return False
    for col in all_data_cols or []:
        head = str(col).split("_", 1)
        if len(head) == 2 and head[0].upper() in tokens:
            return True
    return False


def _select_columns(all_data_cols, custom_name: str, prefix: str, max_fallback: int = 50,
                    custom_cols: str = ""):
    """Multi-prefix ("KNOB,MASK") or ALL or custom-name/custom-cols based column selection.

    v8.8.16: CUSTOM 모드는 사용자가 저장한 columns 를 **그대로** 반환한다.
      - 기존: `all_data_cols` 에 없으면 걸러내어 → 값이 null 인 컬럼이 LOT 뷰에서 사라지는 문제.
      - 변경: custom 에 저장된 column 명을 있는 그대로 반환. view_split 이 null row 를
              자연스럽게 생성 (컬럼이 실제 df 에 없으면 모든 셀이 None, 컬럼명은 유지).
      - 빈 리스트면 기존 폴백 (상위 max_fallback) 유지.
    v8.8.33: `custom_cols` 쉼표 구분 문자열 지원 — 저장된 set 없이도 체크만 한 컬럼을 전송해
             즉시 view 에 반영. custom_name 보다 우선 (ad-hoc 입력 우선).
    v10.0.8: prefix 를 지정했는데 매칭 컬럼이 하나도 없으면 예전에는 상위 N개를 그대로
             돌려줘서 **다른 prefix 컬럼이 섞여 나왔다** (ET 선택인데 KNOB/VM 행이 보임).
             원천이 prefix 체계를 쓰고 있으면 빈 결과가 정답이므로 폴백하지 않는다.
             prefix 가 아예 없는(=bare 컬럼) 제품에서만 기존 상위 N개 폴백을 유지한다.
    """
    # ad-hoc custom_cols 우선
    if custom_cols:
        return _clean_custom_columns(custom_cols.split(","))
    if custom_name:
        try:
            cfp, _clean_name = _custom_file_path_for_name(custom_name)
        except HTTPException:
            return []
        data = load_json(cfp, {})
        cleaned = _sanitize_custom_record(data, cfp, persist=True)
        if cleaned:
            return list(cleaned.get("columns") or [])
        return all_data_cols[:max_fallback]
    if prefix.upper() == "ALL":
        return all_data_cols[: max_fallback * 4]
    pref_list = [p.strip().upper() + "_" for p in prefix.split(",") if p.strip()]
    if pref_list:
        sel = [c for c in all_data_cols if any(c.upper().startswith(p) for p in pref_list)]
        if sel:
            return sel
        if _has_known_prefixed_columns(all_data_cols):
            return []
    return all_data_cols[:max_fallback]


# ── Custom tag columns: runtime-only SplitTable overlay ───────────────
