"""routers/filebrowser.py v4.1.1 (v8.8.3) - lazy parquet + CSV + SQL, single DB root.

Root-level DB files (matching_step.csv, ppid_knob.csv, ML_TABLE_*.parquet,
features_*.parquet, _uniques.json) are exposed through the legacy "base_file"
source type. Internally PATHS.base_root is a compatibility alias to PATHS.db_root.

v8.8.3: /base-file/delete 가 db_root 의 단일 CSV/parquet(=의미적 Base 파일)까지 삭제.
        FE 에서 Base 섹션 목록에 뜨는 파일이면 admin 이 항상 삭제할 수 있게 함.

New endpoints:
  - GET /api/filebrowser/scopes        → list of active scopes (DB + root files)
  - GET /api/filebrowser/roots?scope=  → scope-parameterised roots listing
                                          (`?scope=Base` returns root-level file leaves
                                          rather than canonical DB registry)
  - GET /api/filebrowser/base-files    → top-level file listing under DB root
  - GET /api/filebrowser/base-file-view → preview one root-level DB file

Legacy `/roots` (no `scope` param) keeps its v7.1 shape — DB-canonical only.
"""
import json
import logging
import datetime
import csv
import io
import os
import re
import copy
import hashlib
import tempfile
import threading
import time
from pathlib import Path
import sys
import shutil
import math
import functools
import uuid
from collections import OrderedDict

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_APP_ROOT = _BACKEND_ROOT.parent
for _path in (_APP_ROOT, _BACKEND_ROOT):
    _raw = str(_path)
    sys.path[:] = [p for p in sys.path if p != _raw]
    sys.path.insert(0, _raw)

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
import polars as pl
from core import duckdb_engine
from core import filebrowser_query_queue as _sql_queue
from core import filebrowser_cache as _fbcache
from core import matching_cache as _matching_cache
from core import ml_table_lookup as _ml_table_lookup
from core import s3_sync as _s3
from core.chart_builder_definition import (
    ChartBuilderDefinitionError,
    format_chart_builder_definition,
    parse_chart_builder_definition,
)
from core.paths import PATHS
from core.latest_lot_cache_format import (
    FILE_NAME as _CANONICAL_LOT_PROGRESS_CACHE_FILE,
    FORMAT_VERSION as _CANONICAL_LOT_PROGRESS_CACHE_FORMAT_VERSION,
)
from core.auth import current_user
from app_v2.shared.source_adapter import resolve_existing_root, resolve_named_child
from core.utils import (
    cast_cats, read_source, lazy_read_source, read_one_file, scan_one_file, apply_sql_like, serialize_rows,
    jsonl_append, jsonl_read, jsonl_trim, csv_response, safe_filename,
    DATA_EXTENSIONS, count_data_files, iter_source_product_dirs,
    data_files_limited, source_data_files, load_json, save_json,
)
from app_v2.shared.contracts import FileVersionMeta

logger = logging.getLogger("flow.fb")
router = APIRouter(prefix="/api/filebrowser", tags=["filebrowser"])
YIELD_SHOT_ROOT = "YIELD_SHOT"
# v4.1.1 (2026-04-19): module-level DB_BASE removed. Every route handler now
# reads `PATHS.db_root` / `PATHS.base_root` at request time so env overrides
# (FLOW_*) and admin_settings.json data_roots land without reload.
DL_LOG = PATHS.download_log
MAX_CSV_DOWNLOAD_BYTES = 100_000_000
DEFAULT_CSV_DOWNLOAD_MAX_BYTES = MAX_CSV_DOWNLOAD_BYTES
DEFAULT_CSV_DOWNLOAD_MAX_ROWS = 100_000
MAX_CSV_DOWNLOAD_MAX_ROWS = 500_000
DEFAULT_FILEBROWSER_CSV_DOWNLOAD_ROWS = MAX_CSV_DOWNLOAD_MAX_ROWS


def _filebrowser_sql_offload_min_bytes() -> int:
    """Minimum source size worth paying the cross-server queue round trip."""
    try:
        value = int(os.environ.get("FLOW_FILEBROWSER_SQL_OFFLOAD_MIN_BYTES", "") or 256 * 1024 * 1024)
    except (TypeError, ValueError):
        value = 256 * 1024 * 1024
    return max(0, value)


def _should_offload_filebrowser_sql(
    *, source_size: int, all_partitions: bool, aggregate: bool,
) -> bool:
    return bool(
        all_partitions
        or aggregate
        or int(source_size or 0) >= _filebrowser_sql_offload_min_bytes()
    )
MAX_CSV_DOWNLOAD_AUTO_COLUMNS = 200
DEFAULT_SQL_QUERY_MAX_SOURCE_BYTES = 5 * 1024 * 1024 * 1024
MAX_SQL_QUERY_MAX_SOURCE_BYTES = 500 * 1024 * 1024 * 1024
DEFAULT_PREVIEW_MAX_COLUMNS = 100
MAX_PREVIEW_MAX_COLUMNS = 200
DEFAULT_SCHEMA_COLUMN_PAGE_SIZE = 200
MAX_SCHEMA_COLUMN_PAGE_SIZE = 500
BASE_FILE_EDIT_MAX_BYTES = 25_000_000
BASE_FILE_EDIT_MAX_ROWS = 200_000
BASE_EDIT_ALLOWED_EXTENSIONS = {".csv", ".parquet"}
BASE_EDIT_HISTORY_DIR = ".history"
BASE_EDIT_RESERVED_PREFIXES = {"product_config", "reformatter", "uploads", "cache"}
BASE_VERSION_DIR = PATHS.data_root / "file_versions"
BASE_VERSION_CAP = 20
# 버전 이력은 최근 BASE_VERSION_CAP 개만 남는다 — 20번 고치면 그 앞은 사라진다.
# 그래서 N번째 저장마다 별도 폴더에 원본 사본을 하나씩 떨궈 장기 보관한다.
# 파일명은 `<원본이름>_<시각><확장자>` (예: Vehicle_matching_20260803-174512.csv).
BASE_EDIT_BACKUP_DIR_NAME = "DB BACKUP"
BASE_EDIT_BACKUP_EVERY = 10
EDM_VERSION_MAX_CSV_BYTES = 5_000_000
SINGLE_FILE_FOLDER_TEXT_EXTENSIONS = {".json", ".yaml", ".yml", ".md", ".txt"}
# drop-in 폴더 = 사용자가 DB/Base 루트에 그대로 넣어둔, parquet/CSV 가 없는 폴더.
# 등록 없이 Files 에 노출하되 탐지 비용은 아래 상한으로 묶는다.
_DROP_IN_FOLDER_SCAN_LIMIT = 400
_DROP_IN_FOLDER_MAX_DEPTH = 3
_DROP_IN_FOLDER_CACHE_TTL_SEC = 30.0
_DROP_IN_FOLDER_CACHE: dict[tuple, tuple[float, tuple, tuple[str, ...]]] = {}
SCHEMA_PROFILE_DIR = PATHS.data_root / "schema_profiles"
SCHEMA_PROFILE_CAP = 30
LATEST_PREVIEW_ROWS = 100
# DB(hive) 제품의 기본 preview 는 최신 date 파티션에서만 읽으므로 더 많은 행을
# 안전하게 보여줄 수 있다. SQL/컬럼 선택/집계가 있는 조회는 기존 100행 계약 유지.
DB_LATEST_PREVIEW_ROWS = 500
LATEST_PREVIEW_MAX_FILES = 4
# 최신 date 파티션이 비어 있을 때(빈 parquet 등) 이전 날짜 파일로 확대해
# 500행 샘플을 채우는 fallback 스캔의 파일 수 상한.
DB_PREVIEW_FALLBACK_MAX_FILES = 16
AI_SQL_DEFAULT_SAMPLE_ROWS = 20
AI_SQL_MAX_SAMPLE_ROWS = 50
AI_SQL_PROFILE_VALUE_LIMIT = 3
AI_SQL_MAX_PROFILE_COLUMNS = 80
LIST_CACHE_TTL_SEC = 5.0
MAX_WAFER_ID = 25
_SINGLE_FILE_STEP_CACHE_DIR = "cache"
_SINGLE_FILE_FOLDER_MAX_FILES = 1000
_SINGLE_FILE_STEP_CACHE_FILE = "latest_step_by_lot.parquet"
_SINGLE_FILE_STEP_CACHE_VERSION = 2
_SINGLE_FILE_LATEST_LOT_CACHE_FILE = "latest_lot_by_root_wafer.parquet"
_SINGLE_FILE_LATEST_LOT_CACHE_VERSION = 1
_SINGLE_FILE_PREVIEW_MAX_BYTES = 64 * 1024 * 1024
_SORT_STR = getattr(pl, "Utf8", None) or getattr(pl, "String", pl.Object)
_LIST_CACHE: dict[tuple, tuple[float, object]] = {}
_AI_SQL_VALUE_CATALOG_CACHE: dict[tuple, tuple[float, list[dict]]] = {}
FILEBROWSER_SETTINGS_FILE = "filebrowser_settings.json"
FILEBROWSER_AGENT_PROMPTS_FILE = "filebrowser_agent_prompts.json"
FILEBROWSER_AI_SQL_FEEDBACK_FILE = "filebrowser_ai_sql_feedback.jsonl"
FILEBROWSER_AI_SQL_HISTORY_FILE = "filebrowser_ai_sql_history.jsonl"
CHART_BUILDER_HISTORY_FILE = "chart_builder_history.jsonl"
_CHART_BUILDER_HISTORY_LOCK = threading.Lock()
_CHART_BUILDER_CACHE_LOCK = threading.Lock()
_CHART_BUILDER_RESULT_CACHE: OrderedDict[str, tuple[float, bytes]] = OrderedDict()
try:
    _CHART_BUILDER_CONCURRENCY = max(1, min(5, int(os.environ.get("FLOW_CHART_BUILDER_CONCURRENCY", "2") or 2)))
except (TypeError, ValueError):
    _CHART_BUILDER_CONCURRENCY = 2
_CHART_BUILDER_QUERY_GATE = threading.BoundedSemaphore(_CHART_BUILDER_CONCURRENCY)
FILEBROWSER_AGENT_PROMPTS_DEFAULT_FILE = _BACKEND_ROOT / "core" / "filebrowser_agent_prompts.default.json"
DEFAULT_CSV_FULL_READ_MAX_BYTES = 10 * 1024 * 1024
MAX_CSV_FULL_READ_MAX_BYTES = 100 * 1024 * 1024
DEFAULT_FILEBROWSER_SETTINGS = {
    "csv_full_read_max_bytes": DEFAULT_CSV_FULL_READ_MAX_BYTES,
    "csv_download_max_rows": DEFAULT_FILEBROWSER_CSV_DOWNLOAD_ROWS,
    "csv_download_max_bytes": DEFAULT_CSV_DOWNLOAD_MAX_BYTES,
    "sql_query_max_source_bytes": DEFAULT_SQL_QUERY_MAX_SOURCE_BYTES,
    "preview_max_columns": DEFAULT_PREVIEW_MAX_COLUMNS,
    "preview_max_rows": LATEST_PREVIEW_ROWS,
    "schema_column_page_size": DEFAULT_SCHEMA_COLUMN_PAGE_SIZE,
    "csv_rules": {},
    "hidden_db_dirs": ["reformatter"],
    "db_name_aliases": {},
    "versioned_single_file_dirs": ["reformatter"],
    "auto_s3_upload_on_save": False,
    "preview_cache_enabled": True,
}

# credential/teg_location은 DB root 아래에 있어도 인증정보·도메인 내부 설정을 담는
# 예약 폴더다. teg_location의 기준 CSV 자체는 DB root의 단일 파일로 계속 보이지만,
# 설정 JSON과 제품 이미지를 담는 소문자 teg_location/ 폴더는 탐색기에 노출하지 않는다.
# 관리자 설정에서 표시 폴더로 추가해도 Files/DB 탐색 API에는 절대 노출하지 않는다.
# 필요한 관리자 기능은 각 도메인의 require_admin API를 통해서만 접근한다.
_FILEBROWSER_ALWAYS_HIDDEN_DIRS = {"cache", "credential", "teg_location"}
_RAW_DB_DISPLAY_RE = re.compile(r"^1\.RAWDATA_DB(?:_(.+))?$", re.IGNORECASE)


def _is_filebrowser_hidden_dir_name(name: str) -> bool:
    """Hide internal/cache/backup directories from both DB and Files trees."""
    clean = str(name or "").strip()
    folded = clean.casefold()
    return (
        not clean
        or clean.startswith(".")
        or clean.startswith("__")
        or clean.startswith("_")
        or folded in _FILEBROWSER_ALWAYS_HIDDEN_DIRS
        or "backup" in folded
    )


def _looks_like_db_root_fast(root: Path, max_entries: int = 256) -> bool:
    """Bounded two-level probe for custom DB roots without recursive counting."""
    scanned = 0
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack and scanned < max_entries:
        current, depth = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    scanned += 1
                    if scanned > max_entries:
                        break
                    try:
                        if entry.is_file(follow_symlinks=False):
                            if Path(entry.name).suffix.lower() in DATA_EXTENSIONS:
                                return True
                        elif (
                            depth < 2
                            and entry.is_dir(follow_symlinks=False)
                            and not _is_filebrowser_hidden_dir_name(entry.name)
                        ):
                            stack.append((Path(entry.path), depth + 1))
                    except OSError:
                        continue
        except OSError:
            continue
    return False


def _default_db_display_name(name: str) -> str:
    clean = str(name or "").strip()
    matched = _RAW_DB_DISPLAY_RE.fullmatch(clean)
    if not matched:
        return clean
    suffix = str(matched.group(1) or "").strip(" _-")
    return suffix or "FAB"


def _normalize_db_name_aliases(raw) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    aliases: dict[str, str] = {}
    for source, display in raw.items():
        source_name = str(source or "").strip()
        display_name = str(display or "").strip()
        if (
            not source_name
            or not display_name
            or len(source_name) > 160
            or len(display_name) > 80
            or "/" in source_name
            or "\\" in source_name
            or _is_filebrowser_hidden_dir_name(source_name)
        ):
            continue
        aliases[source_name] = display_name
    return aliases


def _db_display_name(name: str, settings: dict | None = None) -> str:
    settings = settings or _load_filebrowser_settings()
    aliases = settings.get("db_name_aliases") or {}
    folded = str(name or "").casefold()
    for source, display in aliases.items():
        if str(source).casefold() == folded and str(display or "").strip():
            return str(display).strip()
    return _default_db_display_name(name)


def _discovered_db_name_aliases(settings: dict | None = None) -> dict[str, str]:
    settings = settings or _load_filebrowser_settings()
    aliases = dict(settings.get("db_name_aliases") or {})
    root = _db_root()
    if root.is_dir():
        try:
            for entry in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
                if entry.is_dir() and not _is_filebrowser_hidden_dir_name(entry.name):
                    aliases.setdefault(entry.name, _default_db_display_name(entry.name))
        except OSError:
            pass
    return aliases

_DATE_TOKEN_RE = re.compile(r"(?<!\d)(\d{4})[-_]?(\d{2})[-_]?(\d{2})(?!\d)")
_LATEST_COLUMN_PRIORITY = (
    "tkout_time",
    "time",
    "timestamp",
    "datetime",
    "date",
    "tkin_time",
    "updated_at",
    "modified_at",
    "created_at",
)

_WAFER_COLUMN_CANDIDATES = ("wafer_id", "wf_id")
_AI_SQL_IDENTITY_COLUMN_HINTS = {
    "product", "product_id", "prod_id", "root_lot_id", "lot_id", "fab_lot_id",
    "wafer_id", "wf_id", "step_id", "function_step", "process_id", "item_id",
    "eqp_id", "chamber_id", "ppid",
}
_AI_SQL_TIME_COLUMN_TERMS = (
    "time", "date", "timestamp", "updated", "created", "tkin", "tkout",
)
_AI_SQL_VALUE_COLUMN_TERMS = (
    "value", "val", "measure", "measurement", "result", "score", "count",
    "qty", "amount", "avg", "mean", "median", "min", "max", "sum", "rate",
)


def _wafer_column(columns: list[str] | tuple[str, ...] | None) -> str | None:
    lookup = {str(c).lower(): str(c) for c in (columns or [])}
    for name in _WAFER_COLUMN_CANDIDATES:
        if name in lookup:
            return lookup[name]
    return None


def _wafer_number_expr(column: str) -> pl.Expr:
    text = (
        pl.col(column)
        .cast(_SORT_STR, strict=False)
        .str.strip_chars()
        .str.to_uppercase()
        .str.replace(r"^(?:#|WAFER|WF|W)\s*", "")
    )
    return text.cast(pl.Float64, strict=False)


def _valid_wafer_expr(column: str) -> pl.Expr:
    num = _wafer_number_expr(column)
    as_int = num.cast(pl.Int64, strict=False).cast(pl.Float64, strict=False)
    return ((num >= 1) & (num == as_int)).fill_null(False)


def _physical_wafer_expr(column: str) -> pl.Expr:
    num = _wafer_number_expr(column).cast(pl.Int64, strict=False)
    return (((num - 1) % MAX_WAFER_ID) + 1).cast(_SORT_STR, strict=False)


def _filter_valid_wafers_df(df: pl.DataFrame) -> tuple[pl.DataFrame, bool]:
    wafer_col = _wafer_column(list(df.columns))
    if not wafer_col:
        return df, False
    return df.filter(_valid_wafer_expr(wafer_col)).with_columns(_physical_wafer_expr(wafer_col).alias(wafer_col)), True


def _filter_valid_wafers_lazy(lf: pl.LazyFrame, columns: list[str]) -> tuple[pl.LazyFrame, bool]:
    wafer_col = _wafer_column(columns)
    if not wafer_col:
        return lf, False
    return lf.filter(_valid_wafer_expr(wafer_col)).with_columns(_physical_wafer_expr(wafer_col).alias(wafer_col)), True


def _duckdb_valid_wafer_where(columns: list[str]) -> str:
    wafer_col = _wafer_column(columns)
    if not wafer_col:
        return ""
    raw = f"UPPER(TRIM(CAST({duckdb_engine.quote_ident(wafer_col)} AS VARCHAR)))"
    cleaned = raw
    for pattern in ("^#\\s*", "^WAFER\\s*", "^WF\\s*", "^W\\s*"):
        cleaned = f"REGEXP_REPLACE({cleaned}, '{pattern}', '')"
    num = f"TRY_CAST({cleaned} AS DOUBLE)"
    return f"({num} >= 1 AND {num} = FLOOR({num}))"


def _combine_where(left: str, right: str) -> str:
    left = str(left or "").strip()
    right = str(right or "").strip()
    if left and right:
        return f"({left}) AND ({right})"
    return left or right

# Files scope policy: keep only the operational artifacts engineers actually
# maintain for ML_TABLE / SplitTable matching.  Physical files are not deleted;
# the File Browser simply stops surfacing legacy helper files by default.
BASE_EXTENSIONS = set(DATA_EXTENSIONS)
PRODUCT_CONFIG_EXTENSIONS = {".yaml", ".yml"}
CORE_BASE_FILES = {
    "inline_subitem_pos.csv": {
        "role": "INLINE/ET shot map",
        "description": "INLINE subitem 좌표를 ET shot_x/shot_y 로 연결",
        "order": 20,
    },
    "inline_item_map.csv": {
        "role": "INLINE item map",
        "description": "INLINE item_id 를 canonical/function item 으로 연결",
        "order": 30,
    },
    "inline_matching.csv": {
        "role": "INLINE function item",
        "description": "SplitTable INLINE item_id 를 product별 step_id 에 연결",
        "order": 31,
    },
    "knob_ppid.csv": {
        "role": "FAB PPID -> KNOB",
        "description": "Legacy FAB ppid 를 knob_name/knob_value 로 변환",
        "order": 40,
    },
    "ppid_knob.csv": {
        "role": "KNOB common rulebook",
        "description": "SplitTable KNOB feature 를 공용 룰로 분류하고 step_desc 는 Vehicle_matching.csv 에서 제품별 step_id 로 확장",
        "order": 41,
    },
    "mask.csv": {
        "role": "RETICLE -> MASK",
        "description": "reticle_id 를 mask_version/mask_vendor 로 변환",
        "order": 50,
    },
    "mask_info.csv": {
        "role": "RETICLE -> MASK (2열)",
        "description": "제품 구분 없이 reticle_id 를 mask 이름으로 변환 — FAB 매칭알람이 갱신",
        "order": 51,
    },
    "vm_matching.csv": {
        "role": "VM item by step_desc",
        "description": "VM step_desc/item_id 를 정의하고 step_id 는 Vehicle_matching.csv 에서 product별 확장",
        "order": 60,
    },
    "step_matching.csv": {
        "role": "step_id -> func_step",
        "description": "step_id 를 func_step/module 로 정규화",
        "order": 70,
    },
}

EDM_VERSIONED_SINGLE_FILES = {
    "inline_subitem_pos.csv",
    "inline_item_map.csv",
    "inline_matching.csv",
    "knob_ppid.csv",
    "ppid_knob.csv",
    "mask.csv",
    "mask_info.csv",
    "vm_matching.csv",
    "step_matching.csv",
}


def _core_file_meta(name: str) -> dict | None:
    low = name.lower()
    if low.startswith("ml_table_") and low.endswith(".parquet"):
        return {
            "role": "ML_TABLE parquet",
            "description": "제품별 wafer-level ML_TABLE parquet",
            "order": 10,
        }
    if low.startswith("features_") and low.endswith(".parquet"):
        return {
            "role": "Feature parquet",
            "description": "제품/공정 feature 단일 parquet",
            "order": 15,
        }
    if low.endswith(".parquet"):
        return {
            "role": "Parquet file",
            "description": "DB root-level 단일 parquet",
            "order": 85,
        }
    if low.endswith(".csv"):
        return {
            "role": "CSV file",
            "description": "DB root-level 단일 CSV",
            "order": 86,
        }
    return CORE_BASE_FILES.get(low)


def _visible_single_file(path: Path) -> bool:
    """Only expose physical files that actually exist in DB/Base root."""
    if not path.is_file():
        return False
    ext = path.suffix.lower()
    if ext not in BASE_EXTENSIONS:
        return False
    return _core_file_meta(path.name) is not None


def _single_file_cache_dir(root: Path) -> Path:
    return root / _SINGLE_FILE_STEP_CACHE_DIR


def _single_file_cache_stem(path: Path) -> str:
    stem = path.name if path.is_file() else str(path)
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(stem).strip()).strip("._-") or "single_file"


def _single_file_step_cache_parquet(fp: Path) -> Path:
    return _single_file_cache_dir(fp.parent) / f"{_single_file_cache_stem(fp)}.{_SINGLE_FILE_STEP_CACHE_FILE}"


def _single_file_step_cache_meta(fp: Path) -> Path:
    return _single_file_step_cache_parquet(fp).with_suffix(".meta.json")


def _single_file_latest_lot_cache_parquet(fp: Path) -> Path:
    return _single_file_cache_dir(fp.parent) / f"{_single_file_cache_stem(fp)}.{_SINGLE_FILE_LATEST_LOT_CACHE_FILE}"


def _single_file_latest_lot_cache_meta(fp: Path) -> Path:
    return _single_file_latest_lot_cache_parquet(fp).with_suffix(".meta.json")


def _single_file_col(columns: list[str], candidates: tuple[str, ...] | list[str]) -> str:
    by_lower = {str(c).lower(): str(c) for c in columns}
    for candidate in candidates:
        key = by_lower.get(str(candidate).lower())
        if key:
            return key
    return ""


def _single_file_cache_state(fp: Path) -> dict | None:
    meta_fp = _single_file_step_cache_meta(fp)
    if not meta_fp.is_file():
        return None
    try:
        state = json.loads(meta_fp.read_text(encoding="utf-8"))
    except Exception:
        return None
    return state if isinstance(state, dict) else None


def _single_file_latest_lot_cache_state(fp: Path) -> dict | None:
    meta_fp = _single_file_latest_lot_cache_meta(fp)
    if not meta_fp.is_file():
        return None
    try:
        state = json.loads(meta_fp.read_text(encoding="utf-8"))
    except Exception:
        return None
    return state if isinstance(state, dict) else None


def _cache_entry_meta(fp: Path) -> dict:
    name = fp.name
    if name == _CANONICAL_LOT_PROGRESS_CACHE_FILE:
        return {
            "role": "latest lot/step cache",
            "description": "root_lot_id/wafer_id별 최신 lot_id/step_id 공용 parquet 캐시",
            "order": 0,
        }
    return {
        "role": "cache parquet",
        "description": "File Browser에서 열람 가능한 parquet 캐시",
        "order": 5,
    }


def _is_drop_in_folder(folder: Path) -> bool:
    """parquet/CSV 없이 json/yaml/md/txt 만 든 폴더인가.

    이런 폴더는 `count_data_files` 가 0 이라 `/roots`(DB 스코프)에서 걸러지고,
    톱니바퀴 "Files에 표시할 폴더" 에 등록하기 전에는 Files 에서도 안 보였다.
    즉 사용자가 루트에 통째로 넣어둔 묶음(예: Valve 매칭알람 valve-alerts/pipeline/*.json)이
    화면 어디에도 나타나지 않았다. 스캔은 depth/entry 수로 제한한다 — 사진 수만 장짜리
    폴더에서 전체 walk 를 돌지 않기 위함.
    """
    seen_text = False
    scanned = 0
    stack: list[tuple[Path, int]] = [(folder, 0)]
    while stack and scanned < _DROP_IN_FOLDER_SCAN_LIMIT:
        current, depth = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    scanned += 1
                    if scanned > _DROP_IN_FOLDER_SCAN_LIMIT:
                        break
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if depth + 1 < _DROP_IN_FOLDER_MAX_DEPTH:
                                stack.append((Path(entry.path), depth + 1))
                            continue
                    except OSError:
                        continue
                    ext = Path(entry.name).suffix.lower()
                    if ext in DATA_EXTENSIONS:
                        return False  # DB 스코프가 이미 보여주는 폴더
                    if ext in SINGLE_FILE_FOLDER_TEXT_EXTENSIONS:
                        seen_text = True
        except OSError:
            return False
    return seen_text


def _drop_in_folder_names(roots: tuple[Path, ...], registered: set[str]) -> set[str]:
    """등록 없이 Files 에 노출할 최상위 폴더 이름 (root 별 TTL 캐시)."""
    out: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        cache_key = (str(root), tuple(sorted(registered)))
        sig = _path_sig(root)
        now = time.monotonic()
        cached = _DROP_IN_FOLDER_CACHE.get(cache_key)
        if cached and cached[1] == sig and now - cached[0] < _DROP_IN_FOLDER_CACHE_TTL_SEC:
            out.update(cached[2])
            continue
        found: list[str] = []
        try:
            with os.scandir(root) as it:
                for entry in it:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    name = entry.name
                    if _is_filebrowser_hidden_dir_name(name):
                        continue
                    if name.casefold() in registered:
                        continue
                    if _is_drop_in_folder(Path(entry.path)):
                        found.append(name.casefold())
        except OSError:
            found = []
        if len(_DROP_IN_FOLDER_CACHE) > 32:
            _DROP_IN_FOLDER_CACHE.clear()
        _DROP_IN_FOLDER_CACHE[cache_key] = (now, sig, tuple(found))
        out.update(found)
    return out


def _single_file_folder_names(settings: dict | None = None) -> set[str]:
    settings = settings or _load_filebrowser_settings()
    names = set(_hidden_db_dir_names(settings))
    clean: set[str] = set()
    for raw in names:
        name = str(raw or "").strip().strip("/\\").casefold()
        if not name or name in {".", ".."} or "/" in name or "\\" in name or _is_filebrowser_hidden_dir_name(name):
            continue
        clean.add(name)
    # Files 폴더는 톱니바퀴의 "표시할 폴더"에 명시적으로 등록된 항목만 노출한다.
    # 예전의 drop-in 자동 탐지는 미등록 JSON/텍스트 폴더까지 화면에 나타나게 해
    # 관리자가 정한 허용 목록과 실제 화면이 달라지는 원인이었다.
    return clean


def _versioned_single_file_dir_names(settings: dict | None = None) -> set[str]:
    settings = settings or _load_filebrowser_settings()
    names = _clean_string_list(settings.get("versioned_single_file_dirs"), lower=True)
    return {
        name
        for name in names
        if name and name != _SINGLE_FILE_STEP_CACHE_DIR and "/" not in name and "\\" not in name
    }


def _single_file_folder_meta(fp: Path, folder_name: str) -> dict:
    if folder_name == _SINGLE_FILE_STEP_CACHE_DIR:
        return _cache_entry_meta(fp)
    return {
        "role": "single-file data",
        "description": f"Admin 설정 폴더({folder_name}) 안의 단일 운영 파일",
        "order": 8,
    }


def _single_file_folder_extensions(folder_name: str) -> set[str]:
    folder_key = str(folder_name or "").strip().casefold()
    if folder_key == _SINGLE_FILE_STEP_CACHE_DIR:
        return set(DATA_EXTENSIONS)
    return set(DATA_EXTENSIONS) | SINGLE_FILE_FOLDER_TEXT_EXTENSIONS


def _single_file_folder_path(root: Path, folder_name: str) -> Path:
    folder_key = str(folder_name or "").strip()
    direct = root / folder_key
    if direct.is_dir():
        return direct
    ci = resolve_named_child(root, folder_key)
    if ci is not None and ci.is_dir():
        return ci
    return direct


def _fast_scandir_entries(folder: Path, exts: set[str], root: Path, limit: int) -> list[tuple[os.DirEntry, str]]:
    entries = []
    stack = [folder]
    count = 0
    while stack and count < limit:
        current_dir = stack.pop()
        try:
            with os.scandir(current_dir) as it:
                for entry in it:
                    if count >= limit:
                        break
                    if entry.is_dir(follow_symlinks=False):
                        if _is_filebrowser_hidden_dir_name(entry.name):
                            continue
                        rel_path = "/".join(Path(entry.path).relative_to(root).parts)
                        entries.append((entry, rel_path))
                        stack.append(Path(entry.path))
                        count += 1
                    elif entry.is_file(follow_symlinks=False):
                        ext = Path(entry.name).suffix.lower()
                        if ext in exts:
                            rel_path = "/".join(Path(entry.path).relative_to(root).parts)
                            entries.append((entry, rel_path))
                            count += 1
        except OSError:
            pass
    return entries

def _single_file_folder_entries(
    root: Path,
    source_root: str,
    folder_name: str,
    *,
    versioned_dirs: set[str] | None = None,
) -> list[dict]:
    folder_key = str(folder_name or "").strip().casefold()
    if not folder_key:
        return []
    versioned_dirs = versioned_dirs or set()
    folder = _single_file_folder_path(root, folder_key)
    if not folder.is_dir():
        return []
    out: list[dict] = []
    exts = _single_file_folder_extensions(folder_key)

    try:
        raw_candidates = _fast_scandir_entries(folder, exts, root, _SINGLE_FILE_FOLDER_MAX_FILES)
        if folder_key == _SINGLE_FILE_STEP_CACHE_DIR:
            folder_resolved = folder.resolve()

            def _keep_cache_entry(e):
                # os.DirEntry.is_dir 만 follow_symlinks 를 받는다. Path.is_dir 은
                # 3.13+ 에서만 받아서, 운영(3.10)에서는 여기서 TypeError 가 나고
                # 바깥 except 가 삼켜 **cache 폴더 목록이 통째로 비어** 보였다.
                ep = Path(e.path)
                if e.is_dir(follow_symlinks=False):
                    return True
                if ep.parent.resolve() != folder_resolved:
                    return True
                if e.name == _CANONICAL_LOT_PROGRESS_CACHE_FILE:
                    return True
                return False

            raw_candidates = [
                (e, r) for e, r in raw_candidates
                if _keep_cache_entry(e)
            ]
        candidates = sorted(raw_candidates, key=lambda x: x[1].lower())
    except Exception:
        candidates = []

    for entry, rel in candidates[:_SINGLE_FILE_FOLDER_MAX_FILES]:
        try:
            stat = entry.stat()
            fp = Path(entry.path)
        except Exception:
            continue
        is_dir = entry.is_dir(follow_symlinks=False)
        meta = _single_file_folder_meta(fp, folder_key) if not is_dir else {"role": "directory", "description": "Folder", "order": 99}
        out.append({
            "name": rel,
            "path": rel,
            "size": 0 if is_dir else stat.st_size,
            "modified": stat.st_mtime,
            "ext": "dir" if is_dir else Path(entry.name).suffix.lower().lstrip("."),
            "kind": "dir" if is_dir else "file",
            "source": "cache" if folder_key == _SINGLE_FILE_STEP_CACHE_DIR else "single_file_dir",
            "source_root": source_root,
            "source_path": entry.path,
            "role": meta["role"],
            "description": meta["description"],
            "order": meta["order"],
            "editable": False if folder_key == _SINGLE_FILE_STEP_CACHE_DIR else bool(folder_key in versioned_dirs),
            "versioned": bool(folder_key in versioned_dirs),
        })
    return out


# 폴더 하나를 열 때 돌려주는 항목 수 상한. 전체 트리를 한 번에 싣던
# _SINGLE_FILE_FOLDER_MAX_FILES(1000) 와 달리 **디렉터리 한 칸** 기준이라 넉넉하다.
_SINGLE_FILE_DIR_MAX_ENTRIES = 5000


def _single_file_dir_children(
    root: Path,
    source_root: str,
    rel_path: str,
    *,
    versioned_dirs: set[str] | None = None,
    folder_names: set[str] | None = None,
) -> tuple[list[dict], bool]:
    """rel_path 폴더의 **바로 아래** 항목만 나열한다. 반환 (entries, truncated).

    `/base-files` 는 single-file 폴더 전체를 재귀로 한 번에 싣고 1000개에서
    자른다. 그 스캔은 DFS(LIFO)라 예산을 한 갈래에서 다 써 버리고, 결과적으로
    캐시처럼 큰 트리에서는 **형제 폴더가 목록엔 보이는데 열면 비어 있는** 상태가
    된다(운영 캐시는 제품 × root 파티션이라 수만 개다). 폴더를 열 때 그 칸만
    읽으면 깊이 제한 없이 끝까지 내려갈 수 있다.
    """
    versioned_dirs = versioned_dirs or set()
    rel = str(rel_path or "").strip().strip("/").replace("\\", "/")
    if not rel:
        return [], False
    parts = [p for p in rel.split("/") if p]
    if any(p in {".", ".."} for p in parts):
        raise HTTPException(400, "Invalid folder path")
    if any(_is_filebrowser_hidden_dir_name(p) for p in parts):
        return [], False
    folder_key = str(parts[0] or "").casefold()
    if folder_names is not None and folder_key not in folder_names:
        return [], False
    base_folder = _single_file_folder_path(root, folder_key)
    if not base_folder.is_dir():
        return [], False
    target = base_folder.joinpath(*parts[1:]) if len(parts) > 1 else base_folder
    try:
        target_resolved = target.resolve()
        root_resolved = root.resolve()
        target_resolved.relative_to(root_resolved)
    except Exception:
        raise HTTPException(400, "Invalid folder path")
    if not target_resolved.is_dir():
        return [], False
    exts = _single_file_folder_extensions(folder_key)
    is_cache_root = (
        folder_key == _SINGLE_FILE_STEP_CACHE_DIR
        and target_resolved == base_folder.resolve()
    )
    out: list[dict] = []
    truncated = False
    try:
        with os.scandir(target_resolved) as it:
            for entry in it:
                if len(out) >= _SINGLE_FILE_DIR_MAX_ENTRIES:
                    truncated = True
                    break
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if is_dir and _is_filebrowser_hidden_dir_name(entry.name):
                    continue
                if not is_dir:
                    if Path(entry.name).suffix.lower() not in exts:
                        continue
                    # 캐시 최상단의 낱개 파일은 기존 목록 규칙과 동일하게 숨긴다
                    # (canonical lot progress parquet 만 예외).
                    if is_cache_root and entry.name != _CANONICAL_LOT_PROGRESS_CACHE_FILE:
                        continue
                try:
                    stat = entry.stat()
                except OSError:
                    continue
                fp = Path(entry.path)
                try:
                    rel_out = "/".join(fp.relative_to(root_resolved).parts)
                except ValueError:
                    rel_out = "/".join((*parts, entry.name))
                meta = (
                    {"role": "directory", "description": "Folder", "order": 99}
                    if is_dir else _single_file_folder_meta(fp, folder_key)
                )
                out.append({
                    "name": rel_out,
                    "path": rel_out,
                    "size": 0 if is_dir else stat.st_size,
                    "modified": stat.st_mtime,
                    "ext": "dir" if is_dir else Path(entry.name).suffix.lower().lstrip("."),
                    "kind": "dir" if is_dir else "file",
                    "source": "cache" if folder_key == _SINGLE_FILE_STEP_CACHE_DIR else "single_file_dir",
                    "source_root": source_root,
                    "source_path": entry.path,
                    "role": meta["role"],
                    "description": meta["description"],
                    "order": meta["order"],
                    "editable": False if folder_key == _SINGLE_FILE_STEP_CACHE_DIR else bool(folder_key in versioned_dirs),
                    "versioned": bool(folder_key in versioned_dirs),
                })
    except OSError:
        return [], False
    out.sort(key=lambda e: (e["kind"] != "dir", str(e["path"]).lower()))
    return out, truncated


def _single_file_folder_dir_entry(root: Path, source_root: str, folder_name: str, entries: list[dict]) -> dict | None:
    if not entries:
        return None
    folder_key = str(folder_name or "").strip().casefold()
    folder = _single_file_folder_path(root, folder_key)
    try:
        stat = folder.stat()
    except Exception:
        return None
    return {
        "name": folder_key,
        "path": folder_key,
        "size": 0,
        "modified": stat.st_mtime,
        "ext": "dir",
        "kind": "dir",
        "source": source_root,
        "source_path": str(root),
        "description": "single-file folder",
        "role": "cache" if folder_key == _SINGLE_FILE_STEP_CACHE_DIR else "single-file folder",
        "order": 0 if folder_key == _SINGLE_FILE_STEP_CACHE_DIR else 7,
    }


def _single_file_folder_sigs(root: Path, folder_names: set[str]) -> tuple:
    return tuple((name, _path_sig(_single_file_folder_path(root, name))) for name in sorted(folder_names))


def _resolve_single_file_folder_data_path(file: str, roots: tuple[Path, ...], folder_names: set[str]) -> Path | None:
    rel = Path(str(file or "").strip())
    if not rel.parts:
        return None
    folder_key = str(rel.parts[0] or "").casefold()
    if folder_key not in folder_names:
        return None
    if rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
        raise HTTPException(400, "Invalid single-file folder path")
    if rel.suffix.lower() not in _single_file_folder_extensions(folder_key):
        return None
    for root in roots:
        if not root.is_dir():
            continue
        folder = _single_file_folder_path(root, folder_key)
        cand = (folder / Path(*rel.parts[1:])).resolve()
        try:
            cand.relative_to(root.resolve())
        except ValueError:
            continue
        if cand.is_file():
            return cand
    return None


def _cleanup_legacy_single_file_cache(root: Path) -> None:
    cache = _single_file_cache_dir(root)
    if not cache.is_dir():
        return
    nested = cache / _SINGLE_FILE_STEP_CACHE_DIR
    if nested.is_dir():
        try:
            shutil.rmtree(nested)
        except Exception as e:
            logger.warning("legacy nested cache cleanup skipped (%s): %s", nested, e)
    for fp in list(cache.iterdir()):
        if not fp.is_file():
            continue
        name = fp.name
        if name == _CANONICAL_LOT_PROGRESS_CACHE_FILE:
            continue
        try:
            fp.unlink()
        except Exception as e:
            logger.warning("cache cleanup skipped (%s): %s", fp, e)


def _cache_cleanup_roots() -> list[Path]:
    roots: list[Path] = []
    for raw in (PATHS.base_root, PATHS.db_root):
        try:
            root = Path(raw)
        except Exception:
            continue
        try:
            key = root.resolve()
        except Exception:
            key = root
        if any((existing.resolve() if existing.exists() else existing) == key for existing in roots):
            continue
        roots.append(root)
    return roots


def _cache_cleanup_allowed_dirs() -> list[Path]:
    dirs: list[Path] = []
    for root in _cache_cleanup_roots():
        cache_dir = _single_file_cache_dir(root)
        if cache_dir.is_dir():
            dirs.append(cache_dir)
    return dirs


def _cache_cleanup_candidate_file(path: Path, cache_dir: Path) -> bool:
    if not path.is_file():
        return False
    if path.name == _CANONICAL_LOT_PROGRESS_CACHE_FILE and path.parent.resolve() == cache_dir.resolve():
        return False
    suffix = path.suffix.lower()
    if suffix in {".parquet", ".csv", ".json", ".jsonl", ".txt", ".meta"}:
        return True
    if path.name.endswith(".meta.json"):
        return True
    return False


def _cache_cleanup_candidates() -> list[dict]:
    out: list[dict] = []
    for root in _cache_cleanup_roots():
        cache_dir = _single_file_cache_dir(root)
        if not cache_dir.is_dir():
            continue
        try:
            files = sorted(cache_dir.rglob("*"), key=lambda p: str(p.relative_to(root)).lower())
        except Exception:
            files = []
        for fp in files:
            try:
                if not _cache_cleanup_candidate_file(fp, cache_dir):
                    continue
                stat = fp.stat()
                rel = "/".join(fp.relative_to(root).parts)
                out.append({
                    "path": str(fp),
                    "relpath": rel,
                    "root": str(root),
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                    "reason": "legacy_or_noncanonical_cache",
                })
            except Exception:
                continue
    out.sort(key=lambda row: str(row.get("relpath") or row.get("path") or "").lower())
    return out


def _resolve_cache_cleanup_path(raw_path: str) -> Path:
    raw = str(raw_path or "").strip()
    if not raw:
        raise HTTPException(400, "cleanup path is required")
    cand = Path(raw)
    allowed_dirs = _cache_cleanup_allowed_dirs()
    if not allowed_dirs:
        raise HTTPException(400, "No cache directory is available")
    candidates = [cand] if cand.is_absolute() else []
    if not cand.is_absolute():
        for root in _cache_cleanup_roots():
            candidates.append(root / cand)
            candidates.append(_single_file_cache_dir(root) / cand)
    for item in candidates:
        try:
            resolved = item.resolve()
        except Exception:
            continue
        for cache_dir in allowed_dirs:
            try:
                cache_resolved = cache_dir.resolve()
                resolved.relative_to(cache_resolved)
            except Exception:
                continue
            if not _cache_cleanup_candidate_file(resolved, cache_resolved):
                raise HTTPException(400, f"Cleanup target is not allowed: {raw}")
            return resolved
    raise HTTPException(400, f"Cleanup target must be inside an allowed cache directory: {raw}")


def _single_file_cache_entries(root: Path, source_root: str) -> list[dict]:
    return _single_file_folder_entries(root, source_root, _SINGLE_FILE_STEP_CACHE_DIR)


def _is_inside_single_file_cache(path: Path) -> bool:
    try:
        return path.parent.name == _SINGLE_FILE_STEP_CACHE_DIR or path.parent.parent.name == _SINGLE_FILE_STEP_CACHE_DIR
    except Exception:
        return False


def _single_file_step_cache_candidate(path: Path) -> bool:
    if not path.is_file() or path.suffix.lower() not in {".csv", ".parquet"}:
        return False
    if _is_inside_single_file_cache(path):
        return False
    meta = _core_file_meta(path.name)
    if not meta:
        return False
    return meta.get("role") in {"ML_TABLE parquet", "Feature parquet", "Parquet file", "CSV file"}


def _refresh_single_file_step_caches(root: Path) -> None:
    return None


def _ensure_single_file_cache_dirs(base_root: Path, db_root: Path) -> None:
    return None


def cleanup_legacy_cache_roots() -> dict:
    roots: list[Path] = []
    for raw in (PATHS.base_root, PATHS.db_root):
        try:
            root = Path(raw)
        except Exception:
            continue
        if root in roots:
            continue
        roots.append(root)
        _cleanup_legacy_single_file_cache(root)
    return {
        "ok": True,
        "roots": [str(root) for root in roots],
        "canonical": _CANONICAL_LOT_PROGRESS_CACHE_FILE,
    }


def _build_single_file_step_cache(fp: Path, force: bool = False) -> dict:
    if fp.suffix.lower() not in {".csv", ".parquet"}:
        return {"ok": False, "ready": False, "reason": "unsupported extension"}
    try:
        st = fp.stat()
    except Exception:
        return {"ok": False, "ready": False, "reason": "file stat failed"}
    cache_fp = _single_file_step_cache_parquet(fp)
    meta_fp = _single_file_step_cache_meta(fp)
    try:
        cache_fp.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        return {"ok": False, "ready": False, "reason": "cache dir create failed"}
    if not force:
        state = _single_file_cache_state(fp)
        if state and state.get("version") == _SINGLE_FILE_STEP_CACHE_VERSION:
            if state.get("source_size") == st.st_size and state.get("source_mtime_ns") == int(st.st_mtime_ns):
                if state.get("ready"):
                    return {"ok": True, "ready": True, "cached": True, "rows": int(state.get("rows") or 0)}
                return {"ok": False, "ready": False, "cached": True, "reason": state.get("reason")}
    lf = scan_one_file(fp)
    if lf is None:
        return {"ok": False, "ready": False, "reason": "scan failed"}
    try:
        schema = lf.collect_schema()
        columns = list(schema.names())
    except Exception as e:
        return {"ok": False, "ready": False, "reason": f"schema failed: {e}"}
    product_col = _single_file_col(columns, ("product", "product_id", "prod_id", "productid"))
    lot_col = _single_file_col(columns, ("lot", "lot_id", "lotid", "lot_no", "root_lot_id", "fab_lot_id"))
    step_col = _single_file_col(columns, ("step_id", "step", "function_step", "func_step"))
    time_col = _single_file_col(columns, _LATEST_COLUMN_PRIORITY)
    if not (lot_col and step_col):
        state = {
            "version": _SINGLE_FILE_STEP_CACHE_VERSION,
            "ready": False,
            "reason": "missing columns",
            "source_path": str(fp),
            "source_size": st.st_size,
            "source_mtime_ns": int(st.st_mtime_ns),
            "source_columns": columns,
            "cache_generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        _write_text_atomic(meta_fp, json.dumps(state, ensure_ascii=False, indent=2))
        return {"ok": False, "ready": False, "reason": "missing columns"}
    try:
        cache_updated_at = datetime.datetime.now().isoformat(timespec="seconds")
        select_exprs = [
            pl.col(product_col).cast(pl.Utf8, strict=False).fill_null("").str.strip_chars().alias("product")
            if product_col
            else pl.lit(_single_file_product_label(fp)).alias("product"),
            pl.col(lot_col).cast(pl.Utf8, strict=False).str.strip_chars().alias("lot_id"),
            pl.col(step_col).cast(pl.Utf8, strict=False).str.strip_chars().alias("step_id"),
        ]
        if time_col:
            select_exprs.append(pl.col(time_col).cast(pl.Utf8, strict=False).fill_null("").str.strip_chars().alias("updated_at"))
        else:
            select_exprs.append(pl.lit("").alias("updated_at"))
        q = lf.select(select_exprs)
        q = q.filter(
            pl.col("product").is_not_null() & (pl.col("product") != "")
            & pl.col("lot_id").is_not_null() & (pl.col("lot_id") != "")
            & pl.col("step_id").is_not_null() & (pl.col("step_id") != "")
        )
        if time_col:
            q = q.sort(["product", "lot_id", "updated_at", "step_id"])
            q = q.group_by(["product", "lot_id"]).agg([
                pl.col("step_id").last().alias("latest_step_id"),
                pl.col("updated_at").last().alias("updated_at"),
            ])
        else:
            q = q.group_by(["product", "lot_id"]).agg([
                pl.col("step_id").max().alias("latest_step_id"),
                pl.lit("").first().alias("updated_at"),
            ])
        q = q.with_columns(pl.lit(cache_updated_at).alias("cache_updated_at")).sort(["product", "lot_id"])
        try:
            from core.parquet_perf import collect_streaming
            df = collect_streaming(q)
        except Exception:
            df = q.collect()
        df = df.select(["product", "lot_id", "latest_step_id", "updated_at", "cache_updated_at"])
    except Exception as e:
        return {"ok": False, "ready": False, "reason": f"build failed: {e}"}
    try:
        _write_parquet_atomic(cache_fp, df)
        state = {
            "version": _SINGLE_FILE_STEP_CACHE_VERSION,
            "ready": True,
            "source_path": str(fp),
            "source_size": st.st_size,
            "source_mtime_ns": int(st.st_mtime_ns),
            "rows": int(df.height),
            "cache_path": str(cache_fp),
            "cache_file": cache_fp.name,
            "cache_generated_at": cache_updated_at,
            "product_col": product_col,
            "lot_col": lot_col,
            "step_col": step_col,
            "time_col": time_col,
        }
        _write_text_atomic(meta_fp, json.dumps(state, ensure_ascii=False, indent=2))
        return {"ok": True, "ready": True, "cached": False, "rows": int(df.height)}
    except Exception as e:
        return {"ok": False, "ready": False, "reason": f"write failed: {e}"}


def _single_file_product_label(fp: Path) -> str:
    stem = str(fp.stem or "").strip()
    if stem.upper().startswith("ML_TABLE_"):
        return stem[len("ML_TABLE_"):]
    parent = str(fp.parent.name or "").strip()
    if parent and parent != _SINGLE_FILE_STEP_CACHE_DIR:
        return parent
    return stem


def _build_single_file_latest_lot_cache(fp: Path, force: bool = False) -> dict:
    if fp.suffix.lower() not in {".csv", ".parquet"}:
        return {"ok": False, "ready": False, "reason": "unsupported extension"}
    try:
        st = fp.stat()
    except Exception:
        return {"ok": False, "ready": False, "reason": "file stat failed"}
    cache_fp = _single_file_latest_lot_cache_parquet(fp)
    meta_fp = _single_file_latest_lot_cache_meta(fp)
    try:
        cache_fp.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        return {"ok": False, "ready": False, "reason": "cache dir create failed"}
    if not force:
        state = _single_file_latest_lot_cache_state(fp)
        if state and state.get("version") == _SINGLE_FILE_LATEST_LOT_CACHE_VERSION:
            if state.get("source_size") == st.st_size and state.get("source_mtime_ns") == int(st.st_mtime_ns):
                if state.get("ready"):
                    return {"ok": True, "ready": True, "cached": True, "rows": int(state.get("rows") or 0)}
                return {"ok": False, "ready": False, "cached": True, "reason": state.get("reason")}
    lf = scan_one_file(fp)
    if lf is None:
        return {"ok": False, "ready": False, "reason": "scan failed"}
    try:
        schema = lf.collect_schema()
        columns = list(schema.names())
    except Exception as e:
        return {"ok": False, "ready": False, "reason": f"schema failed: {e}"}
    product_col = _single_file_col(columns, ("product", "product_id", "prod_id", "productid"))
    root_col = _single_file_col(columns, ("root_lot_id", "root_lot", "lot_root_id", "root_lotid"))
    wafer_col = _single_file_col(columns, ("wafer_id", "wf_id", "wafer"))
    lot_col = _single_file_col(columns, ("lot_id", "fab_lot_id", "lot", "lotid", "fab_lot"))
    time_col = _single_file_col(columns, _LATEST_COLUMN_PRIORITY)
    if not (root_col and wafer_col and lot_col):
        state = {
            "version": _SINGLE_FILE_LATEST_LOT_CACHE_VERSION,
            "ready": False,
            "reason": "missing columns",
            "source_path": str(fp),
            "source_size": st.st_size,
            "source_mtime_ns": int(st.st_mtime_ns),
            "source_columns": columns,
            "cache_generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        _write_text_atomic(meta_fp, json.dumps(state, ensure_ascii=False, indent=2))
        return {"ok": False, "ready": False, "reason": "missing columns"}
    try:
        cache_updated_at = datetime.datetime.now().isoformat(timespec="seconds")
        product_expr = (
            pl.col(product_col).cast(pl.Utf8, strict=False).fill_null("").str.strip_chars().alias("product")
            if product_col
            else pl.lit(_single_file_product_label(fp)).alias("product")
        )
        select_exprs = [
            product_expr,
            pl.col(root_col).cast(pl.Utf8, strict=False).fill_null("").str.strip_chars().alias("root_lot_id"),
            pl.col(wafer_col).cast(pl.Utf8, strict=False).fill_null("").str.strip_chars().alias("wafer_id"),
            pl.col(lot_col).cast(pl.Utf8, strict=False).fill_null("").str.strip_chars().alias("lot_id"),
        ]
        if time_col:
            select_exprs.append(pl.col(time_col).cast(pl.Utf8, strict=False).fill_null("").str.strip_chars().alias("updated_at"))
        else:
            select_exprs.append(pl.lit("").alias("updated_at"))
        q = lf.select(select_exprs)
        q = q.filter(
            pl.col("root_lot_id").is_not_null() & (pl.col("root_lot_id") != "")
            & pl.col("wafer_id").is_not_null() & (pl.col("wafer_id") != "")
            & pl.col("lot_id").is_not_null() & (pl.col("lot_id") != "")
        )
        if time_col:
            q = q.sort(["product", "root_lot_id", "wafer_id", "updated_at", "lot_id"])
            q = q.group_by(["product", "root_lot_id", "wafer_id"]).agg([
                pl.col("lot_id").last().alias("lot_id"),
                pl.col("updated_at").last().alias("updated_at"),
            ])
        else:
            q = q.sort(["product", "root_lot_id", "wafer_id", "lot_id"])
            q = q.group_by(["product", "root_lot_id", "wafer_id"]).agg([
                pl.col("lot_id").last().alias("lot_id"),
                pl.lit("").first().alias("updated_at"),
            ])
        q = q.with_columns([
            pl.col("lot_id").alias("latest_lot_id"),
            pl.lit(cache_updated_at).alias("cache_updated_at"),
            pl.col("wafer_id").cast(pl.Int64, strict=False).alias("__wafer_sort"),
        ]).sort(["product", "root_lot_id", "__wafer_sort", "wafer_id"]).drop("__wafer_sort")
        try:
            from core.parquet_perf import collect_streaming
            df = collect_streaming(q)
        except Exception:
            df = q.collect()
        df = df.select(["product", "root_lot_id", "wafer_id", "lot_id", "latest_lot_id", "updated_at", "cache_updated_at"])
    except Exception as e:
        return {"ok": False, "ready": False, "reason": f"build failed: {e}"}
    try:
        _write_parquet_atomic(cache_fp, df)
        state = {
            "version": _SINGLE_FILE_LATEST_LOT_CACHE_VERSION,
            "ready": True,
            "source_path": str(fp),
            "source_size": st.st_size,
            "source_mtime_ns": int(st.st_mtime_ns),
            "rows": int(df.height),
            "cache_path": str(cache_fp),
            "cache_file": cache_fp.name,
            "cache_generated_at": cache_updated_at,
            "product_col": product_col,
            "root_col": root_col,
            "wafer_col": wafer_col,
            "lot_col": lot_col,
            "time_col": time_col,
        }
        _write_text_atomic(meta_fp, json.dumps(state, ensure_ascii=False, indent=2))
        return {"ok": True, "ready": True, "cached": False, "rows": int(df.height)}
    except Exception as e:
        return {"ok": False, "ready": False, "reason": f"write failed: {e}"}
