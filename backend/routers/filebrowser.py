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
import time
from pathlib import Path
import sys
import shutil
import math
import functools

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
from core import matching_cache as _matching_cache
from core import s3_sync as _s3
from core.paths import PATHS
from app_v2.shared.source_adapter import resolve_existing_root, resolve_named_child
from core.utils import (
    cast_cats, read_source, lazy_read_source, read_one_file, scan_one_file, apply_sql_like, serialize_rows,
    jsonl_append, jsonl_read, csv_response, safe_filename,
    DATA_EXTENSIONS, count_data_files, iter_source_product_dirs,
    data_files_limited, source_data_files, load_json, save_json,
)
from app_v2.shared.contracts import FileVersionMeta

logger = logging.getLogger("flow.fb")
router = APIRouter(prefix="/api/filebrowser", tags=["filebrowser"])
# v4.1.1 (2026-04-19): module-level DB_BASE removed. Every route handler now
# reads `PATHS.db_root` / `PATHS.base_root` at request time so env overrides
# (FLOW_*) and admin_settings.json data_roots land without reload.
DL_LOG = PATHS.download_log
MAX_CSV_DOWNLOAD_BYTES = 100_000_000
DEFAULT_CSV_DOWNLOAD_MAX_ROWS = 100_000
MAX_CSV_DOWNLOAD_MAX_ROWS = 500_000
DEFAULT_FILEBROWSER_CSV_DOWNLOAD_ROWS = MAX_CSV_DOWNLOAD_MAX_ROWS
MAX_CSV_DOWNLOAD_AUTO_COLUMNS = 200
BASE_FILE_EDIT_MAX_BYTES = 25_000_000
BASE_FILE_EDIT_MAX_ROWS = 200_000
BASE_EDIT_ALLOWED_EXTENSIONS = {".csv", ".parquet"}
BASE_EDIT_HISTORY_DIR = ".history"
BASE_EDIT_RESERVED_PREFIXES = {"product_config", "reformatter", "uploads", "cache"}
BASE_VERSION_DIR = PATHS.data_root / "file_versions"
BASE_VERSION_CAP = 50
EDM_VERSION_MAX_CSV_BYTES = 5_000_000
SINGLE_FILE_FOLDER_TEXT_EXTENSIONS = {".json", ".yaml", ".yml", ".md", ".txt"}
SCHEMA_PROFILE_DIR = PATHS.data_root / "schema_profiles"
SCHEMA_PROFILE_CAP = 30
LATEST_PREVIEW_ROWS = 200
LATEST_PREVIEW_MAX_FILES = 4
LIST_CACHE_TTL_SEC = 5.0
MAX_WAFER_ID = 25
_SINGLE_FILE_STEP_CACHE_DIR = "cache"
_SINGLE_FILE_FOLDER_MAX_FILES = 1000
_SINGLE_FILE_STEP_CACHE_FILE = "latest_step_by_lot.parquet"
_SINGLE_FILE_STEP_CACHE_VERSION = 2
_SINGLE_FILE_LATEST_LOT_CACHE_FILE = "latest_lot_by_root_wafer.parquet"
_SINGLE_FILE_LATEST_LOT_CACHE_VERSION = 1
_CANONICAL_LOT_PROGRESS_CACHE_FILE = "lot_progress_latest_lot_by_root_wafer.parquet"
_SINGLE_FILE_PREVIEW_MAX_BYTES = 64 * 1024 * 1024
_SORT_STR = getattr(pl, "Utf8", None) or getattr(pl, "String", pl.Object)
_LIST_CACHE: dict[tuple, tuple[float, object]] = {}
FILEBROWSER_SETTINGS_FILE = "filebrowser_settings.json"
FILEBROWSER_AGENT_PROMPTS_FILE = "filebrowser_agent_prompts.json"
FILEBROWSER_AGENT_PROMPTS_DEFAULT_FILE = _BACKEND_ROOT / "core" / "filebrowser_agent_prompts.default.json"
DEFAULT_CSV_FULL_READ_MAX_BYTES = 10 * 1024 * 1024
MAX_CSV_FULL_READ_MAX_BYTES = 100 * 1024 * 1024
DEFAULT_FILEBROWSER_SETTINGS = {
    "csv_full_read_max_bytes": DEFAULT_CSV_FULL_READ_MAX_BYTES,
    "csv_download_max_rows": DEFAULT_FILEBROWSER_CSV_DOWNLOAD_ROWS,
    "csv_rules": {},
    "hidden_db_dirs": ["cache", "reformatter"],
    "versioned_single_file_dirs": ["reformatter"],
    "auto_s3_upload_on_save": False,
}

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
        "description": "INLINE item명/step_id 를 function_step 으로 연결",
        "order": 31,
    },
    "knob_ppid.csv": {
        "role": "FAB PPID -> KNOB",
        "description": "Legacy FAB ppid 를 knob_name/knob_value 로 변환",
        "order": 40,
    },
    "ppid_knob.csv": {
        "role": "KNOB -> function_step",
        "description": "SplitTable KNOB feature 를 적용 function_step 으로 연결",
        "order": 41,
    },
    "mask.csv": {
        "role": "RETICLE -> MASK",
        "description": "reticle_id 를 mask_version/mask_vendor 로 변환",
        "order": 50,
    },
    "vm_matching.csv": {
        "role": "VM -> step_id",
        "description": "VM feature/step_desc 를 step_id/function_step 으로 연결",
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


def _single_file_folder_names(settings: dict | None = None) -> set[str]:
    settings = settings or _load_filebrowser_settings()
    names = {_SINGLE_FILE_STEP_CACHE_DIR}
    names.update(_hidden_db_dir_names(settings))
    clean: set[str] = set()
    for raw in names:
        name = str(raw or "").strip().strip("/\\").casefold()
        if not name or name in {".", ".."} or "/" in name or "\\" in name:
            continue
        clean.add(name)
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
    try:
        raw_candidates = [
            fp for fp in folder.rglob("*")
            if fp.is_file() and fp.suffix.lower() in _single_file_folder_extensions(folder_key)
        ]
        if folder_key == _SINGLE_FILE_STEP_CACHE_DIR:
            folder_resolved = folder.resolve()
            raw_candidates = [
                fp for fp in raw_candidates
                if fp.name == _CANONICAL_LOT_PROGRESS_CACHE_FILE and fp.parent.resolve() == folder_resolved
            ]
        candidates = sorted(raw_candidates, key=lambda p: str(p.relative_to(root)).lower())
    except Exception:
        candidates = []
    for fp in candidates[:_SINGLE_FILE_FOLDER_MAX_FILES]:
        try:
            stat = fp.stat()
            rel = "/".join(fp.relative_to(root).parts)
        except Exception:
            continue
        meta = _single_file_folder_meta(fp, folder_key)
        out.append({
            "name": rel,
            "path": rel,
            "size": stat.st_size,
            "modified": stat.st_mtime,
            "ext": fp.suffix.lower().lstrip("."),
            "kind": "file",
            "source": "cache" if folder_key == _SINGLE_FILE_STEP_CACHE_DIR else "single_file_dir",
            "source_root": source_root,
            "source_path": str(fp),
            "role": meta["role"],
            "description": meta["description"],
            "order": meta["order"],
            "editable": False if folder_key == _SINGLE_FILE_STEP_CACHE_DIR else bool(folder_key in versioned_dirs),
            "versioned": bool(folder_key in versioned_dirs),
        })
    return out


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



def _parse_tab_or_csv(text: str, delimiter: str) -> tuple[list[list[str]], str]:
    normalized = str(text or "")
    if not normalized:
        return [], delimiter

    def _read(d: str, strict: bool = True) -> list[list[str]]:
        try:
            reader = csv.reader(io.StringIO(normalized), delimiter=d, quotechar='"', doublequote=True)
            rows = [list(r) for r in reader]
        except Exception:
            if strict:
                raise
            return []
        while rows and all(str(v or "").strip() == "" for v in rows[-1]):
            rows.pop()
        return rows

    requested = (delimiter or "auto").lower()
    if requested in {"tab", "\t", "\\t"}:
        return _read("\t"), "tab"
    if requested in {"comma", ",", "csv"}:
        return _read(","), "comma"

    # auto: 우선 탭 파서, 실패/의미없는 분리면 CSV 파서로 폴백.
    try:
        tab_rows = _read("\t")
    except Exception:
        tab_rows = []
    if ("\t" in normalized) or any(len(r) > 1 for r in tab_rows):
        return tab_rows, "tab"
    return _read("," , strict=False), "comma"


def _normalize_rows(rows: list[list[str]], width: int, fill: str = "") -> tuple[list[list[str]], int]:
    norm: list[list[str]] = []
    for r in rows:
        rr = ["" if v is None else str(v) for v in (r or [])]
        if len(rr) < width:
            rr = rr + [fill] * (width - len(rr))
        elif len(rr) > width:
            rr = rr[:width]
        norm.append(rr)
    return norm, width


def _resolve_base_file_for_edit(file: str) -> Path:
    name = (file or "").strip()
    if not name:
        raise HTTPException(400, "file is required")
    rel = Path(name)
    if rel.is_absolute() or any(p in {"", ".", ".."} for p in rel.parts):
        raise HTTPException(400, "Invalid file path")
    settings = _load_filebrowser_settings()
    versioned_dirs = _versioned_single_file_dir_names(settings)
    folder_fp = _resolve_single_file_folder_data_path(file, (_base_root(), _db_root()), versioned_dirs)
    if folder_fp is not None:
        return folder_fp
    if rel.parts and str(rel.parts[0]).casefold() in _single_file_folder_names(settings):
        raise HTTPException(400, f"This folder is read-only in File Browser: {rel.parts[0]}")
    if rel.parts and rel.parts[0] in BASE_EDIT_RESERVED_PREFIXES:
        raise HTTPException(400, f"Editing scope mismatch: {rel.parts[0]}/* is not a single Base/DB file")

    base_root = _base_root()
    db_root = _db_root()
    for candidate_root in (base_root, db_root):
        if not candidate_root.is_dir():
            continue
        cand = (candidate_root / rel).resolve()
        try:
            cand.relative_to(candidate_root.resolve())
        except ValueError:
            continue
        if cand.suffix.lower() not in BASE_EDIT_ALLOWED_EXTENSIONS:
            raise HTTPException(400, f"Unsupported file type: {cand.suffix}")
        if cand.is_file():
            return cand

    raise HTTPException(404, f"Base file not found in Base/DB root: {file}")


def _resolve_base_file_for_version(file: str) -> Path:
    name = (file or "").strip()
    if not name:
        raise HTTPException(400, "file is required")
    rel = Path(name)
    if rel.is_absolute() or any(p in {"", ".", ".."} for p in rel.parts):
        raise HTTPException(400, "Invalid file path")
    settings = _load_filebrowser_settings()
    folder_fp = _resolve_single_file_folder_data_path(file, (_base_root(), _db_root()), _single_file_folder_names(settings))
    if folder_fp is not None:
        return folder_fp
    if rel.parts and rel.parts[0] == "product_config":
        if len(rel.parts) != 2 or rel.parts[1].startswith("."):
            raise HTTPException(400, "Invalid product config path")
        root = (PATHS.data_root / "product_config").resolve()
        cand = (root / rel.parts[1]).resolve()
        try:
            cand.relative_to(root)
        except ValueError:
            raise HTTPException(400, "Invalid product config path")
        if cand.is_file() and cand.suffix.lower() in PRODUCT_CONFIG_EXTENSIONS:
            return cand
        raise HTTPException(404, f"Product config not found: {file}")
    if rel.parts and rel.parts[0] == "reformatter":
        if len(rel.parts) != 2 or rel.parts[1].startswith("."):
            raise HTTPException(400, "Invalid reformatter path")
        root = (PATHS.data_root / "reformatter").resolve()
        requested = rel.parts[1]
        candidates = [root / requested]
        if requested.lower().endswith(".csv"):
            candidates.append(root / (Path(requested).stem + ".json"))
        for cand0 in candidates:
            cand = cand0.resolve()
            try:
                cand.relative_to(root)
            except ValueError:
                continue
            if cand.is_file() and cand.suffix.lower() in {".csv", ".json"}:
                return cand
        raise HTTPException(404, f"Reformatter file not found: {file}")
    return _resolve_base_file_for_edit(file)


def _filebrowser_settings_path() -> Path:
    return PATHS.data_root / FILEBROWSER_SETTINGS_FILE


def _filebrowser_agent_prompts_path() -> Path:
    return PATHS.data_root / FILEBROWSER_AGENT_PROMPTS_FILE


def _read_json_file_safe(path: Path) -> dict:
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        logger.warning("json read failed: %s", path)
    return {}


def _deep_merge_dict(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base) if isinstance(base, dict) else {}
    if not isinstance(override, dict):
        return out
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge_dict(out.get(key) or {}, value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _load_filebrowser_agent_prompts() -> dict:
    default = _read_json_file_safe(FILEBROWSER_AGENT_PROMPTS_DEFAULT_FILE)
    runtime = _read_json_file_safe(_filebrowser_agent_prompts_path())
    return _deep_merge_dict(default, runtime)


def _filebrowser_agent_prompt(key: str, fallback: str) -> str:
    cfg = _load_filebrowser_agent_prompts()
    raw = cfg.get(key)
    if raw is None and "." in key:
        section, field = key.split(".", 1)
        node = cfg.get(section)
        if isinstance(node, dict):
            raw = node.get(field)
    text = str(raw or "").strip()
    return text or fallback


def _clean_rule_file_key(file: str) -> str:
    name = str(file or "").strip().replace("\\", "/")
    if not name:
        raise HTTPException(400, "CSV rule file key is required")
    rel = Path(name)
    if rel.is_absolute() or any(p in {"", ".", ".."} for p in rel.parts):
        raise HTTPException(400, f"Invalid CSV rule file key: {file}")
    return "/".join(rel.parts)


def _clean_string_list(value, *, lower: bool = False) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = re.split(r"[,\n]", value)
    elif isinstance(value, (list, tuple, set)):
        raw = list(value)
    else:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item or "").strip()
        if not text:
            continue
        key = text.lower() if lower else text
        if key in seen:
            continue
        seen.add(key)
        out.append(key if lower else text)
    return out


def _normalize_unique_keys(value) -> list[list[str]]:
    if value is None:
        return []
    raw = value
    if isinstance(raw, str):
        raw = [line for line in raw.splitlines() if line.strip()]
    if not isinstance(raw, list):
        return []
    out: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for item in raw:
        if isinstance(item, str):
            cols = _clean_string_list(item)
        elif isinstance(item, (list, tuple)):
            cols = _clean_string_list(list(item))
        elif isinstance(item, dict):
            cols = _clean_string_list(item.get("columns") or item.get("keys") or [])
        else:
            cols = []
        if not cols:
            continue
        key = tuple(cols)
        if key in seen:
            continue
        seen.add(key)
        out.append(cols)
    return out


def _normalize_enums(value) -> dict[str, list[str]]:
    if not value:
        return {}
    raw: dict = {}
    if isinstance(value, dict):
        raw = value
    elif isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            col = str(item.get("column") or "").strip()
            if col:
                raw[col] = item.get("values") or item.get("allowed") or []
    out: dict[str, list[str]] = {}
    for col, vals in raw.items():
        name = str(col or "").strip()
        if not name:
            continue
        if isinstance(vals, str):
            allowed = [v.strip() for v in re.split(r"[|,\n]", vals) if v.strip()]
        elif isinstance(vals, (list, tuple, set)):
            allowed = [str(v).strip() for v in vals if str(v).strip()]
        else:
            allowed = []
        if allowed:
            out[name] = allowed
    return out


def _normalize_numeric(value) -> dict[str, dict]:
    if not value:
        return {}
    raw: dict = {}
    if isinstance(value, dict):
        raw = value
    elif isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            col = str(item.get("column") or "").strip()
            if col:
                raw[col] = item
    out: dict[str, dict] = {}
    for col, spec in raw.items():
        name = str(col or "").strip()
        if not name:
            continue
        if not isinstance(spec, dict):
            spec = {}
        clean: dict = {}
        for key in ("min", "max"):
            raw_v = spec.get(key)
            if raw_v in (None, ""):
                continue
            try:
                clean[key] = float(raw_v)
            except Exception:
                raise HTTPException(400, f"Invalid numeric.{name}.{key}: {raw_v}")
        if bool(spec.get("integer")):
            clean["integer"] = True
        out[name] = clean
    return out


def _normalize_regex(value) -> dict[str, str]:
    if not value:
        return {}
    raw: dict = {}
    if isinstance(value, dict):
        raw = value
    elif isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            col = str(item.get("column") or "").strip()
            if col:
                raw[col] = item.get("pattern") or item.get("regex") or ""
    out: dict[str, str] = {}
    for col, pattern in raw.items():
        name = str(col or "").strip()
        pat = str(pattern.get("pattern") if isinstance(pattern, dict) else pattern or "").strip()
        if not name or not pat:
            continue
        try:
            re.compile(pat)
        except re.error as e:
            raise HTTPException(400, f"Invalid regex for {name}: {e}")
        out[name] = pat
    return out


def _normalize_date_columns(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, dict):
        return _clean_string_list(list(value.keys()))
    if isinstance(value, list):
        cols = []
        for item in value:
            if isinstance(item, dict):
                cols.append(item.get("column") or item.get("col") or "")
            else:
                cols.append(item)
        return _clean_string_list(cols)
    return _clean_string_list(value)


def _normalize_conditions(value) -> list[dict]:
    if not value:
        return []
    raw = value
    if isinstance(raw, str):
        raw = [line for line in raw.splitlines() if line.strip()]
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if isinstance(item, str):
            expr, msg = item, ""
        elif isinstance(item, dict):
            expr = item.get("expr") or item.get("where") or item.get("selector") or ""
            msg = item.get("message") or item.get("label") or ""
        else:
            continue
        expr = str(expr or "").strip()
        if not expr:
            continue
        if ";" in expr or "__" in expr:
            raise HTTPException(400, f"Unsafe condition expression: {expr}")
        try:
            pl.sql_expr(expr)
        except Exception as e:
            raise HTTPException(400, f"Invalid condition expression '{expr}': {e}")
        out.append({"expr": expr, "message": str(msg or "").strip()})
    return out


_ORDER_SPEC_TYPES = {"string", "text", "numeric", "number", "integer", "date", "datetime", "leading_number", "rule_order"}


def _normalize_order_specs(value, *, label: str) -> list[dict]:
    if not value:
        return []
    raw = value
    if isinstance(raw, str):
        raw = [line for line in raw.splitlines() if line.strip()]
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if isinstance(item, str):
            parts = [p for p in re.split(r"[\s,]+", item.strip()) if p]
            spec = {
                "column": parts[0] if len(parts) >= 1 else "",
                "direction": parts[1] if len(parts) >= 2 else "asc",
                "type": parts[2] if len(parts) >= 3 else "string",
                "nulls": parts[3] if len(parts) >= 4 else "last",
            }
        elif isinstance(item, dict):
            spec = item
        else:
            continue
        col = str(spec.get("column") or spec.get("col") or "").strip()
        if not col:
            continue
        direction = str(spec.get("direction") or spec.get("dir") or "asc").strip().lower()
        typ = str(spec.get("type") or "string").strip().lower()
        nulls = str(spec.get("nulls") or "last").strip().lower()
        if direction not in {"asc", "ascending", "desc", "descending"}:
            raise HTTPException(400, f"Invalid {label} direction for {col}: {direction}")
        if typ not in _ORDER_SPEC_TYPES:
            raise HTTPException(400, f"Invalid {label} type for {col}: {typ}")
        if nulls not in {"first", "last", "nulls_first", "nulls_last"}:
            raise HTTPException(400, f"Invalid {label} nulls for {col}: {nulls}")
        if typ in {"number", "integer"}:
            typ = "numeric"
        elif typ == "datetime":
            typ = "date"
        elif typ == "text":
            typ = "string"
        out.append({
            "column": col,
            "direction": "desc" if direction.startswith("desc") else "asc",
            "type": typ,
            "nulls": "first" if nulls.endswith("first") else "last",
        })
    return out


def _normalize_sort(value) -> list[dict]:
    return _normalize_order_specs(value, label="sort")


def _normalize_ordered_by(value) -> dict:
    if not value:
        return {}
    if isinstance(value, dict):
        specs = _normalize_order_specs(
            value.get("keys") or value.get("sort") or value.get("order") or [],
            label="ordered_by",
        )
        group_by = _clean_string_list(value.get("group_by") or value.get("groups"))
    else:
        specs = _normalize_order_specs(value, label="ordered_by")
        group_by = []
    if not specs:
        return {}
    out = {"keys": specs}
    if group_by:
        out["group_by"] = group_by
    return out


def _normalize_csv_rule(raw) -> dict:
    if not isinstance(raw, dict):
        return {}
    rule: dict = {}
    for key in ("required_columns", "not_empty"):
        vals = _clean_string_list(raw.get(key))
        if vals:
            rule[key] = vals
    unique_keys = _normalize_unique_keys(raw.get("unique_keys"))
    if unique_keys:
        rule["unique_keys"] = unique_keys
    enums = _normalize_enums(raw.get("enums"))
    if enums:
        rule["enums"] = enums
    numeric = _normalize_numeric(raw.get("numeric"))
    if numeric:
        rule["numeric"] = numeric
    date_cols = _normalize_date_columns(raw.get("date"))
    if date_cols:
        rule["date"] = date_cols
    regexes = _normalize_regex(raw.get("regex"))
    if regexes:
        rule["regex"] = regexes
    conditions = _normalize_conditions(raw.get("conditions"))
    if conditions:
        rule["conditions"] = conditions
    sort_rule = _normalize_sort(raw.get("sort"))
    if sort_rule:
        rule["sort"] = sort_rule
    ordered_by = _normalize_ordered_by(raw.get("ordered_by"))
    if ordered_by:
        rule["ordered_by"] = ordered_by
    return rule


def _normalize_csv_rules(raw_rules) -> dict[str, dict]:
    if not isinstance(raw_rules, dict):
        return {}
    out: dict[str, dict] = {}
    for file, rule in raw_rules.items():
        key = _clean_rule_file_key(str(file or ""))
        clean = _normalize_csv_rule(rule)
        if clean:
            out[key] = clean
    return out


def _normalize_filebrowser_settings(raw) -> dict:
    data = copy.deepcopy(DEFAULT_FILEBROWSER_SETTINGS)
    if not isinstance(raw, dict):
        return data
    try:
        max_bytes = int(raw.get("csv_full_read_max_bytes", data["csv_full_read_max_bytes"]))
    except Exception:
        raise HTTPException(400, "csv_full_read_max_bytes must be an integer")
    data["csv_full_read_max_bytes"] = max(0, min(MAX_CSV_FULL_READ_MAX_BYTES, max_bytes))
    try:
        max_rows = int(raw.get("csv_download_max_rows", data["csv_download_max_rows"]))
    except Exception:
        raise HTTPException(400, "csv_download_max_rows must be an integer")
    data["csv_download_max_rows"] = max(1, min(MAX_CSV_DOWNLOAD_MAX_ROWS, max_rows))
    data["csv_rules"] = _normalize_csv_rules(raw.get("csv_rules") or {})
    data["auto_s3_upload_on_save"] = bool(raw.get("auto_s3_upload_on_save", data.get("auto_s3_upload_on_save", False)))
    hidden = _clean_string_list(raw.get("hidden_db_dirs"), lower=True)
    data["hidden_db_dirs"] = hidden if hidden else list(DEFAULT_FILEBROWSER_SETTINGS["hidden_db_dirs"])
    raw_versioned = raw.get("versioned_single_file_dirs", data["versioned_single_file_dirs"])
    versioned = [
        name for name in _clean_string_list(raw_versioned, lower=True)
        if name and name != _SINGLE_FILE_STEP_CACHE_DIR and "/" not in name and "\\" not in name
    ]
    data["versioned_single_file_dirs"] = versioned
    return data


def _load_filebrowser_settings() -> dict:
    path = _filebrowser_settings_path()
    if not path.is_file():
        return copy.deepcopy(DEFAULT_FILEBROWSER_SETTINGS)
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception:
        logger.warning("filebrowser settings read failed: %s", path)
        return copy.deepcopy(DEFAULT_FILEBROWSER_SETTINGS)
    try:
        return _normalize_filebrowser_settings(raw)
    except HTTPException:
        logger.warning("filebrowser settings invalid, using defaults: %s", path)
        return copy.deepcopy(DEFAULT_FILEBROWSER_SETTINGS)


def _save_filebrowser_settings(settings: dict) -> None:
    path = _filebrowser_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(_normalize_filebrowser_settings(settings), ensure_ascii=False, indent=2)
    _write_text_atomic(path, payload + "\n")


def _filebrowser_auto_s3_upload_enabled(settings: dict | None = None) -> bool:
    settings = settings or _load_filebrowser_settings()
    return bool(settings.get("auto_s3_upload_on_save"))


def _filebrowser_s3_sync_for_saved_path(path: Path) -> dict:
    if not _filebrowser_auto_s3_upload_enabled():
        return {
            "ok": True,
            "status": "disabled_by_filebrowser_setting",
            "path": str(path),
        }
    try:
        return _s3.sync_saved_path(PATHS.data_root, PATHS.db_root, path)
    except Exception as exc:
        logger.warning("filebrowser auto S3 sync failed path=%s: %s", path, exc)
        return {"ok": False, "status": "error", "path": str(path), "error": str(exc)}


def _hidden_db_dir_names(settings: dict | None = None) -> set[str]:
    settings = settings or _load_filebrowser_settings()
    return {str(v or "").strip().casefold() for v in (settings.get("hidden_db_dirs") or []) if str(v or "").strip()}


def _csv_rule_for_file(file: str, settings: dict | None = None) -> dict:
    settings = settings or _load_filebrowser_settings()
    rules = settings.get("csv_rules") or {}
    try:
        key = _clean_rule_file_key(file)
    except HTTPException:
        return {}
    return copy.deepcopy(rules.get(key) or rules.get(Path(key).name) or {})


def _csv_rule_summary(rule: dict) -> dict | None:
    if not rule:
        return None
    return {
        "required_columns": len(rule.get("required_columns") or []),
        "not_empty": len(rule.get("not_empty") or []),
        "unique_keys": len(rule.get("unique_keys") or []),
        "enums": len(rule.get("enums") or {}),
        "numeric": len(rule.get("numeric") or {}),
        "date": len(rule.get("date") or []),
        "regex": len(rule.get("regex") or {}),
        "conditions": len(rule.get("conditions") or []),
        "ordered_by": len((rule.get("ordered_by") or {}).get("keys") or []),
        "sort": len(rule.get("sort") or []),
    }


_CSV_RULE_ALLOWED_KEYS = {
    "required_columns", "not_empty", "unique_keys", "enums", "numeric",
    "date", "regex", "conditions", "ordered_by", "sort",
}
_SQL_EXPR_IGNORE_TOKENS = {
    "and", "or", "not", "is", "in", "null", "true", "false", "none",
    "case", "when", "then", "else", "end", "as", "cast", "between",
    "like", "ilike", "str", "int", "float", "date", "datetime",
    "abs", "round", "ceil", "floor", "min", "max", "sum", "mean", "avg",
    "lower", "upper", "contains", "starts_with", "ends_with", "is_null",
    "is_not_null", "fill_null", "strptime", "len",
}


def _settings_context_columns(columns, sample_rows=None) -> list[str]:
    out = _clean_string_list(columns)
    seen = {c.casefold() for c in out}
    if not out and isinstance(sample_rows, list):
        for row in sample_rows[:5]:
            if not isinstance(row, dict):
                continue
            for key in row.keys():
                text = str(key or "").strip()
                if text and text.casefold() not in seen:
                    seen.add(text.casefold())
                    out.append(text)
    return out[:500]


def _column_lookup(columns: list[str]) -> dict[str, str]:
    return {str(c).casefold(): str(c) for c in columns or [] if str(c or "").strip()}


def _draft_warning(warnings: list[str], message: str) -> None:
    text = str(message or "").strip()
    if text and text not in warnings:
        warnings.append(text)


def _canon_rule_column(column: str, lookup: dict[str, str], warnings: list[str], context: str) -> str:
    text = str(column or "").strip()
    if not text:
        return ""
    if not lookup:
        return text
    hit = lookup.get(text.casefold())
    if hit:
        return hit
    _draft_warning(warnings, f"{context}: unknown column removed: {text}")
    return ""


def _filter_rule_column_list(values, lookup: dict[str, str], warnings: list[str], context: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        col = _canon_rule_column(value, lookup, warnings, context)
        key = col.casefold()
        if col and key not in seen:
            seen.add(key)
            out.append(col)
    return out


def _filter_rule_dict_by_column(value: dict, lookup: dict[str, str], warnings: list[str], context: str) -> dict:
    out: dict = {}
    for col, spec in (value or {}).items():
        clean = _canon_rule_column(col, lookup, warnings, context)
        if clean:
            out[clean] = spec
    return out


def _filter_unique_keys(value: list[list[str]], lookup: dict[str, str], warnings: list[str]) -> list[list[str]]:
    out: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for combo in value or []:
        cols: list[str] = []
        missing = False
        for col in combo or []:
            clean = _canon_rule_column(col, lookup, warnings, "unique_keys")
            if not clean:
                missing = True
            elif clean not in cols:
                cols.append(clean)
        if missing:
            _draft_warning(warnings, f"unique_keys: combo removed because it referenced a missing column: {combo}")
            continue
        key = tuple(cols)
        if cols and key not in seen:
            seen.add(key)
            out.append(cols)
    return out


def _condition_references_missing_columns(expr: str, lookup: dict[str, str]) -> list[str]:
    if not lookup:
        return []
    scrubbed = re.sub(r"'[^']*'|\"[^\"]*\"", " ", str(expr or ""))
    tokens = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", scrubbed)
    missing: list[str] = []
    for token in tokens:
        key = token.casefold()
        if key in lookup or key in _SQL_EXPR_IGNORE_TOKENS:
            continue
        if token not in missing:
            missing.append(token)
    return missing


def _filter_conditions(value: list[dict], lookup: dict[str, str], warnings: list[str]) -> list[dict]:
    out: list[dict] = []
    for item in value or []:
        expr = str((item or {}).get("expr") or "").strip()
        missing = _condition_references_missing_columns(expr, lookup)
        if missing:
            _draft_warning(warnings, f"conditions: expression removed because columns were not found: {', '.join(missing)}")
            continue
        out.append(item)
    return out


def _filter_order_specs(value: list[dict], lookup: dict[str, str], warnings: list[str], context: str) -> list[dict]:
    out: list[dict] = []
    for item in value or []:
        clean = _canon_rule_column((item or {}).get("column") or "", lookup, warnings, context)
        if clean:
            out.append({**item, "column": clean})
    return out


def _normalize_csv_rule_draft(raw, *, columns=None) -> tuple[dict, list[str]]:
    warnings: list[str] = []
    if not isinstance(raw, dict):
        return {}, ["LLM draft did not return a csv_rules object."]
    lookup = _column_lookup(_settings_context_columns(columns))
    unknown = sorted(str(k) for k in raw.keys() if str(k) not in _CSV_RULE_ALLOWED_KEYS)
    for key in unknown:
        _draft_warning(warnings, f"unsupported key removed: {key}")

    rule: dict = {}
    for key in ("required_columns", "not_empty"):
        vals = _filter_rule_column_list(_clean_string_list(raw.get(key)), lookup, warnings, key)
        if vals:
            rule[key] = vals

    try:
        unique_keys = _filter_unique_keys(_normalize_unique_keys(raw.get("unique_keys")), lookup, warnings)
        if unique_keys:
            rule["unique_keys"] = unique_keys
    except HTTPException as exc:
        _draft_warning(warnings, str(exc.detail))

    try:
        enums = _filter_rule_dict_by_column(_normalize_enums(raw.get("enums")), lookup, warnings, "enums")
        if enums:
            rule["enums"] = enums
    except HTTPException as exc:
        _draft_warning(warnings, str(exc.detail))

    try:
        numeric = _filter_rule_dict_by_column(_normalize_numeric(raw.get("numeric")), lookup, warnings, "numeric")
        if numeric:
            rule["numeric"] = numeric
    except HTTPException as exc:
        _draft_warning(warnings, str(exc.detail))

    date_cols = _filter_rule_column_list(_normalize_date_columns(raw.get("date")), lookup, warnings, "date")
    if date_cols:
        rule["date"] = date_cols

    try:
        regexes = _filter_rule_dict_by_column(_normalize_regex(raw.get("regex")), lookup, warnings, "regex")
        if regexes:
            rule["regex"] = regexes
    except HTTPException as exc:
        _draft_warning(warnings, str(exc.detail))

    try:
        conditions = _filter_conditions(_normalize_conditions(raw.get("conditions")), lookup, warnings)
        if conditions:
            rule["conditions"] = conditions
    except HTTPException as exc:
        _draft_warning(warnings, str(exc.detail))

    try:
        sort_rule = _filter_order_specs(_normalize_sort(raw.get("sort")), lookup, warnings, "sort")
        if sort_rule:
            rule["sort"] = sort_rule
    except HTTPException as exc:
        _draft_warning(warnings, str(exc.detail))

    try:
        ordered_by = _normalize_ordered_by(raw.get("ordered_by"))
        if ordered_by:
            keys = _filter_order_specs(ordered_by.get("keys") or [], lookup, warnings, "ordered_by")
            group_by = _filter_rule_column_list(ordered_by.get("group_by") or [], lookup, warnings, "ordered_by.group_by")
            if keys:
                rule["ordered_by"] = {"keys": keys, **({"group_by": group_by} if group_by else {})}
    except HTTPException as exc:
        _draft_warning(warnings, str(exc.detail))

    return rule, warnings


def _safe_sample_rows(rows, *, max_rows: int = 5, max_cols: int = 40, max_value_len: int = 120) -> list[dict]:
    out: list[dict] = []
    if not isinstance(rows, list):
        return out
    for row in rows[:max_rows]:
        if not isinstance(row, dict):
            continue
        clean: dict = {}
        for idx, (key, value) in enumerate(row.items()):
            if idx >= max_cols:
                break
            text = str(value if value is not None else "")
            clean[str(key)[:120]] = text[:max_value_len]
        out.append(clean)
    return out


def _settings_column_profiles(columns: list[str], sample_rows: list[dict]) -> list[dict]:
    profiles: list[dict] = []
    for col in columns[:80]:
        values: list[str] = []
        for row in sample_rows[:10]:
            if not isinstance(row, dict):
                continue
            value = row.get(col)
            if value is None:
                for key, raw in row.items():
                    if str(key).casefold() == col.casefold():
                        value = raw
                        break
            text = str(value if value is not None else "").strip()
            if text:
                values.append(text[:80])
        unique = []
        seen = set()
        for value in values:
            key = value.casefold()
            if key not in seen:
                seen.add(key)
                unique.append(value)
        numeric_count = 0
        integer_count = 0
        for value in values:
            try:
                parsed = float(value)
            except Exception:
                continue
            numeric_count += 1
            if parsed.is_integer():
                integer_count += 1
        inferred = "string"
        if values and numeric_count == len(values):
            inferred = "integer" if integer_count == len(values) else "numeric"
        elif re.search(r"(date|time|_dt$|^dt_|created|updated|start|end)", col, flags=re.I):
            inferred = "date"
        profiles.append({
            "column": col,
            "sample_values": unique[:8],
            "non_empty_sample_count": len(values),
            "sample_unique_count": len(unique),
            "inferred_type": inferred,
        })
    return profiles


def _settings_llm_rule_candidate(plan: dict, file_key: str) -> dict:
    if not isinstance(plan, dict):
        return {}
    csv_rules = plan.get("csv_rules")
    if isinstance(csv_rules, dict):
        for key in (file_key, Path(file_key).name):
            item = csv_rules.get(key)
            if isinstance(item, dict):
                return item
        for item in csv_rules.values():
            if isinstance(item, dict):
                return item
    for key in ("draft", "rule", "csv_rule"):
        item = plan.get(key)
        if isinstance(item, dict):
            return item
    if any(key in plan for key in _CSV_RULE_ALLOWED_KEYS):
        return plan
    return {}


def _settings_prompt_has_duplicate_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    return any(term in low or term in text for term in (
        "unique", "duplicate", "duplicated", "dedupe", "same row", "same combination",
        "중복", "유니크", "같은 행", "똑같은 행", "동일한 행", "같은 조합", "조합이 중복",
    ))


def _prompt_identifier_tokens(prompt: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_]*", str(prompt or "")):
        key = token.casefold()
        if key not in seen:
            seen.add(key)
            out.append(token)
    return out


def _resolve_prompt_rule_columns(prompt: str, columns: list[str]) -> tuple[list[str], list[str]]:
    lookup = _column_lookup(columns)
    aliases = {
        "product": ("product", "product_id", "prod_id", "prod"),
        "prod": ("product", "product_id", "prod_id", "prod"),
        "lot": ("lot_id", "fab_lot_id", "lot", "lotid"),
        "lot_id": ("lot_id", "fab_lot_id", "lot", "lotid"),
        "fab_lot": ("fab_lot_id", "lot_id", "fab_lot", "lot"),
        "fab_lot_id": ("fab_lot_id", "lot_id", "fab_lot", "lot"),
        "wafer": ("wafer_id", "wf_id", "wafer"),
        "wafer_id": ("wafer_id", "wf_id", "wafer"),
        "wf": ("wafer_id", "wf_id", "wafer"),
        "wf_id": ("wafer_id", "wf_id", "wafer"),
        "root_lot": ("root_lot_id", "root_lot", "lot_root_id"),
        "root_lot_id": ("root_lot_id", "root_lot", "lot_root_id"),
    }
    resolved: list[str] = []
    missing: list[str] = []
    for token in _prompt_identifier_tokens(prompt):
        key = token.casefold()
        candidates = (key, key.replace(" ", "_"), *(aliases.get(key) or ()))
        hit = ""
        for cand in candidates:
            if cand in lookup:
                hit = lookup[cand]
                break
        if hit:
            if hit not in resolved:
                resolved.append(hit)
            continue
        if "_" in token or key in aliases:
            missing.append(token)
    return resolved, missing


def _settings_prompt_has_enum_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    english = bool(re.search(r"\b(enum|allowed|allowlist|only)\b", low) or "one of" in low or "must be" in low)
    korean = any(term in text for term in ("허용", "허용값", "중 하나", "중에", "만 있어야", "만 가능", "만 허용", " 또는 "))
    return english or korean


def _prompt_enum_values(prompt: str, target_column: str, columns: list[str]) -> list[str]:
    text = str(prompt or "")
    tail = text
    for needle in (target_column, target_column.replace("_", " ")):
        m = re.search(re.escape(needle), text, flags=re.I)
        if m:
            tail = text[m.end():]
            break
    column_tokens = {c.casefold() for c in columns}
    column_tokens.update(c.casefold().replace("_", " ") for c in columns)
    stop = {
        "or", "and", "only", "must", "be", "one", "of", "in", "value", "values",
        "enum", "allowed", "allowlist", "operator", "column", "col",
    }
    out: list[str] = []
    seen: set[str] = set()
    for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_.-]*", tail):
        clean = token.strip().strip(".,;:()[]{}")
        key = clean.casefold()
        if not clean or key in stop or key in column_tokens:
            continue
        if key not in seen:
            seen.add(key)
            out.append(clean)
    return out[:50]


def _has_not_empty_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    return any(term in low or term in text for term in (
        "not empty", "non-empty", "not blank", "blank", "empty",
        "빈 값", "비어", "비면", "공백", "값이 있", "값은 있",
    ))


def _has_required_column_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    return any(term in low or term in text for term in (
        "required column", "must exist", "must have", "column must", "required",
        "필수 컬럼", "컬럼은 반드시", "컬럼이 반드시", "컬럼 있어야", "컬럼은 있어야",
    ))


def _has_numeric_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    if re.search(r"\b(numeric|number|integer|float|min|max)\b", low) or ">=" in text or "<=" in text:
        return True
    if "정수" in text:
        return True
    if re.search(r"-?\d+(?:\.\d+)?\s*(?:이상|이하|초과|미만)", text):
        return True
    return any(term in text for term in (
        "숫자여야", "숫자 이어야", "숫자이어야", "숫자로", "숫자 값", "숫자값", "숫자 컬럼", "숫자만",
    ))


def _numeric_rule_from_prompt(prompt: str, target: str) -> dict:
    text = str(prompt or "")
    low = text.lower()
    spec: dict = {}
    if "integer" in low or "정수" in text:
        spec["integer"] = True
    min_match = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:이상|>=|부터)", text)
    max_match = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:이하|<=|까지)", text)
    if min_match:
        val = float(min_match.group(1))
        spec["min"] = int(val) if val.is_integer() else val
    if max_match:
        val = float(max_match.group(1))
        spec["max"] = int(val) if val.is_integer() else val
    if spec or _has_numeric_intent(prompt):
        return {target: spec}
    return {}


def _has_regex_format_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    return bool(re.search(r"\b(regex|regexp|pattern|format)\b", low)) or any(term in text for term in (
        "정규식", "패턴", "형식", "포맷", "같은", "처럼", "이어야", "있어야",
    ))


def _regex_rule_from_prompt(prompt: str, resolved: list[str]) -> dict:
    text = str(prompt or "")
    low = text.lower()
    out: dict = {}
    for col in resolved:
        key = col.casefold()
        has_rule_pattern = (
            "r숫자" in low
            or "r 숫자" in low
            or (
                _has_regex_format_intent(prompt)
                and bool(re.search(r"\bR\d+\b", text, flags=re.I))
                and bool(re.search(r"\bRO\b", text, flags=re.I))
            )
        )
        if key == "rule_order" and has_rule_pattern:
            out[col] = r"R\d+|RO"
        elif key in {"feature_name", "feature"} and any(term in text for term in ("앞에", "선행", "첫")) and "숫자" in text:
            out[col] = r"\d+(?:\.\d+)?\s+.+"
        elif key == "category" and "ppid" in low and "숫자" in text:
            out[col] = r"^PPID_\d+_\d+$"
        elif key == "function_step" and "대문자" in text and ("underscore" in low or "언더" in text or "_" in text):
            out[col] = r"^[A-Z_]+$"
    return out


def _condition_rules_from_prompt(prompt: str, resolved: list[str]) -> list[dict]:
    text = str(prompt or "")
    if len(resolved) < 2:
        return []
    if any(term in text for term in ("빠르면 안", "보다 빠르", "이전이면 안", "작으면 안")):
        left = resolved[0]
        right = resolved[1]
        return [{"expr": f"{left} >= {right}", "message": f"{left} must be >= {right}"}]
    return []


def _sort_rule_from_prompt(prompt: str, columns: list[str], resolved: list[str]) -> dict:
    text = str(prompt or "")
    low = text.lower()
    has_sort = bool(re.search(r"\b(sort|ordered|order by)\b", low))
    has_sort = has_sort or any(term in text for term in (
        "정렬", "순서대로", "오름차순", "내림차순", "정렬되어", "정렬됐", "정렬되었",
    ))
    has_sort = has_sort or (
        any(term in text for term in ("앞에 숫자", "앞 숫자", "선행 숫자", "숫자에 따라서", "숫자 기준"))
        and any(term in text for term in ("해줘", "정렬", "오름차순", "내림차순", "기준", "따라서"))
    )
    if not has_sort:
        return {}
    specs = _fallback_sort_specs(prompt, resolved or columns, expert=False)
    if not specs:
        return {}
    if any(term in low or term in text for term in ("검증", "validate", "현재 행 순서", "정렬되어")) and not any(term in text for term in ("저장", "save")):
        return {"ordered_by": {"keys": specs}}
    return {"sort": specs}


def _settings_prompt_explicit_rule(prompt: str, columns: list[str], current_rule: dict,
                                   warnings: list[str]) -> dict | None:
    rule: dict = {}
    explicit_seen = False
    resolved, missing = _resolve_prompt_rule_columns(prompt, columns)

    if _settings_prompt_has_duplicate_intent(prompt):
        if resolved or missing:
            explicit_seen = True
            if missing:
                _draft_warning(warnings, f"unique_keys prompt referenced missing column(s): {', '.join(missing)}")
            if len(resolved) >= 2:
                rule["unique_keys"] = [resolved]
            else:
                _draft_warning(warnings, "duplicate prompt did not resolve to a usable unique key.")

    if resolved and _has_required_column_intent(prompt):
        explicit_seen = True
        rule["required_columns"] = resolved

    if resolved and _has_not_empty_intent(prompt):
        explicit_seen = True
        rule["not_empty"] = resolved

    numeric_cols: set[str] = set()
    if resolved and _has_numeric_intent(prompt):
        explicit_seen = True
        target = resolved[0]
        numeric = _numeric_rule_from_prompt(prompt, target)
        if numeric:
            rule.setdefault("numeric", {}).update(numeric)
            numeric_cols.add(target)

    sort_rule = _sort_rule_from_prompt(prompt, columns, resolved)
    regex_rules = {} if sort_rule and not _has_regex_format_intent(prompt) else _regex_rule_from_prompt(prompt, resolved)
    if regex_rules:
        explicit_seen = True
        rule.setdefault("regex", {}).update(regex_rules)

    conditions = _condition_rules_from_prompt(prompt, resolved)
    if conditions:
        explicit_seen = True
        rule["conditions"] = conditions

    if sort_rule:
        explicit_seen = True
        rule.update(sort_rule)

    if _settings_prompt_has_enum_intent(prompt) and resolved:
        target = resolved[0]
        if target not in numeric_cols and target not in regex_rules:
            explicit_seen = True
            values = _prompt_enum_values(prompt, target, columns)
            if values:
                rule.setdefault("enums", {})[target] = values
            else:
                _draft_warning(warnings, f"enums prompt did not include allowed values for {target}.")

    return rule if explicit_seen else None


def _fallback_sort_direction(prompt: str) -> str:
    text = str(prompt or "")
    low = text.lower()
    desc_terms = (
        "desc", "descending", "내림차순", "역순", "큰순", "큰 순",
        "높은순", "높은 순", "많은순", "많은 순",
    )
    if any(term in low or term in text for term in desc_terms):
        return "desc"
    return "asc"


def _fallback_sort_type(prompt: str, column: str) -> str:
    text = str(prompt or "")
    low = text.lower()
    column_l = str(column or "").casefold()
    if column_l in {"rule_order", "ruleorder", "order", "sort_order"}:
        return "rule_order"
    leading_number_terms = (
        "leading number", "prefix number", "prefix numeric",
        "앞에 숫자", "앞 숫자", "선행 숫자", "첫 숫자", "숫자에 따라서", "숫자 기준",
    )
    if column_l in {"feature_name", "feature", "step_name", "function_step"}:
        return "leading_number"
    return "string"


def _settings_prompt_wants_expert(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    return any(term in low or term in text for term in (
        "expert", "comprehensive", "detailed", "strict", "all possible", "as much as possible",
        "전문가", "상세", "자세", "가능한", "가능한거", "가능한 것", "전체", "꼼꼼", "강하게",
        "다 짜", "다 만들어", "최대한",
    ))


def _sample_values_for_column(sample_rows: list[dict] | None, column: str) -> list[str]:
    values: list[str] = []
    for row in (sample_rows or [])[:20]:
        if not isinstance(row, dict):
            continue
        raw = row.get(column)
        if raw is None:
            for key, value in row.items():
                if str(key).casefold() == str(column).casefold():
                    raw = value
                    break
        text = str(raw if raw is not None else "").strip()
        if text:
            values.append(text)
    return values


def _sample_unique_values(values: list[str], limit: int = 20) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _numeric_spec_from_values(values: list[str]) -> dict | None:
    parsed: list[float] = []
    for value in values:
        try:
            parsed.append(float(str(value).strip()))
        except Exception:
            return None
    if not parsed:
        return None
    out: dict = {"integer": all(v.is_integer() for v in parsed)}
    out["min"] = int(min(parsed)) if out["integer"] else min(parsed)
    out["max"] = int(max(parsed)) if out["integer"] else max(parsed)
    return out


def _looks_date_like_column(column: str) -> bool:
    return bool(re.search(r"(date|time|_dt$|^dt_|created|updated|start|end)", str(column or ""), flags=re.I))


def _fallback_sort_specs(prompt: str, columns: list[str], *, expert: bool = False) -> list[dict]:
    lookup = _column_lookup(columns)
    low = str(prompt or "").casefold()
    direction = _fallback_sort_direction(prompt)
    mentioned = [
        col for col in columns
        if col.casefold() in low or col.casefold().replace("_", " ") in low
    ]
    if not mentioned and any(term in str(prompt or "") for term in ("앞에 숫자", "앞 숫자", "선행 숫자")):
        feature_col = lookup.get("feature_name")
        if feature_col:
            mentioned = [feature_col]
    if mentioned:
        candidates = mentioned
    elif expert:
        candidates = [lookup[col] for col in ("product", "feature_name", "rule_order") if col in lookup]
        if not candidates:
            candidates = [lookup[col] for col in ("rank", "order", "sort_order", "seq", "sequence", "priority") if col in lookup]
    else:
        candidates = [lookup[col] for col in ("product", "feature_name", "rule_order") if col in lookup]
    specs: list[dict] = []
    seen: set[str] = set()
    for col in candidates:
        key = col.casefold()
        if key in seen:
            continue
        seen.add(key)
        specs.append({
            "column": col,
            "direction": direction,
            "type": _fallback_sort_type(prompt, col),
            "nulls": "last",
        })
    return specs


def _fallback_unique_keys(columns: list[str]) -> list[list[str]]:
    lookup = _column_lookup(columns)
    combos: list[list[str]] = []
    for combo in (
        ("id",),
        ("key",),
        ("product", "feature_name", "rule_order"),
        ("product", "lot_id", "wafer_id"),
        ("root_lot_id", "wafer_id"),
        ("lot_id", "wafer_id"),
        ("product", "feature_name"),
    ):
        cols = [lookup[c] for c in combo if c in lookup]
        if len(cols) == len(combo):
            combos.append(cols)
    return combos[:2]


def _fallback_regex_rules(columns: list[str], sample_rows: list[dict] | None) -> dict[str, str]:
    lookup = _column_lookup(columns)
    regexes: dict[str, str] = {}
    feature_col = lookup.get("feature_name")
    if feature_col:
        values = _sample_values_for_column(sample_rows, feature_col)
        if not values or any(re.match(r"^\d+(?:\.\d+)?\s+\S+", value) for value in values):
            regexes[feature_col] = r"\d+(?:\.\d+)?\s+.+"
    rule_col = lookup.get("rule_order")
    if rule_col:
        values = _sample_values_for_column(sample_rows, rule_col)
        if not values or all(re.match(r"^R\d+$|^RO$", value, flags=re.I) for value in values):
            regexes[rule_col] = r"R\d+|RO"
    return regexes


def _fallback_numeric_rules(columns: list[str], sample_rows: list[dict] | None) -> dict[str, dict]:
    out: dict[str, dict] = {}
    numeric_name_re = re.compile(r"(rank|order|sort|seq|count|cnt|qty|num|number|idx|index|priority|score|value|rate|ratio|pct|percent|min|max|limit)", re.I)
    for col in columns:
        values = _sample_values_for_column(sample_rows, col)
        spec = _numeric_spec_from_values(values)
        if spec and (numeric_name_re.search(col) or values):
            out[col] = spec
    return out


def _fallback_enum_rules(columns: list[str], sample_rows: list[dict] | None) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    enum_name_re = re.compile(r"(status|state|type|category|cat|operator|mode|flag|yn|use|enabled|result|pass|fail)", re.I)
    for col in columns:
        if not enum_name_re.search(col):
            continue
        values = _sample_unique_values(_sample_values_for_column(sample_rows, col), limit=16)
        if values and len(values) <= 12:
            out[col] = values
    return out


def _fallback_condition_rules(columns: list[str]) -> list[dict]:
    lookup = _column_lookup(columns)
    conditions: list[dict] = []
    for start_key, end_key in (
        ("start_time", "end_time"),
        ("start_date", "end_date"),
        ("from_time", "to_time"),
        ("begin_time", "end_time"),
    ):
        if start_key in lookup and end_key in lookup:
            conditions.append({
                "expr": f"{lookup[end_key]} >= {lookup[start_key]}",
                "message": f"{lookup[end_key]} must be >= {lookup[start_key]}",
            })
            break
    return conditions


def _settings_draft_fallback_rule(prompt: str, columns: list[str], current_rule: dict, warnings: list[str],
                                  sample_rows: list[dict] | None = None) -> dict:
    rule = copy.deepcopy(current_rule) if isinstance(current_rule, dict) else {}
    low = str(prompt or "").lower()
    expert = _settings_prompt_wants_expert(prompt)
    if not columns:
        _draft_warning(warnings, "No columns were supplied, so only schema-level cleanup was applied.")
        return rule
    if expert or any(token in low for token in ("required", "필수", "must have")):
        rule["required_columns"] = columns
    if expert or any(token in low for token in ("not empty", "non-empty", "blank", "빈 값", "비어")):
        if sample_rows and expert:
            non_empty_cols = [col for col in columns if _sample_values_for_column(sample_rows, col)]
            rule["not_empty"] = non_empty_cols or columns
        else:
            rule["not_empty"] = columns
    if (expert or any(token in low for token in ("unique", "duplicate", "중복", "유니크"))) and not rule.get("unique_keys"):
        unique_keys = _fallback_unique_keys(columns)
        if unique_keys:
            rule["unique_keys"] = unique_keys
    if expert:
        enums = _fallback_enum_rules(columns, sample_rows)
        if enums and not rule.get("enums"):
            rule["enums"] = enums
        numeric = _fallback_numeric_rules(columns, sample_rows)
        if numeric and not rule.get("numeric"):
            rule["numeric"] = numeric
        date_cols = [col for col in columns if _looks_date_like_column(col)]
        if date_cols and not rule.get("date"):
            rule["date"] = date_cols
        regexes = _fallback_regex_rules(columns, sample_rows)
        if regexes and not rule.get("regex"):
            rule["regex"] = regexes
        conditions = _fallback_condition_rules(columns)
        if conditions and not rule.get("conditions"):
            rule["conditions"] = conditions
    if (expert or any(token in low or token in str(prompt or "") for token in (
        "sort", "order", "정렬", "순서", "오름차순", "내림차순", "앞에 숫자", "앞 숫자", "선행 숫자",
    ))) and not rule.get("sort") and not rule.get("ordered_by"):
        specs = _fallback_sort_specs(prompt, columns, expert=expert)
        if specs:
            rule["ordered_by"] = {"keys": specs}
            rule["sort"] = specs
    if not rule:
        _draft_warning(warnings, "LLM unavailable or empty; no deterministic draft could be inferred.")
    else:
        _draft_warning(warnings, "LLM unavailable or empty; deterministic keyword draft was used.")
    return rule


def _can_manage_filebrowser(me: dict) -> bool:
    if (me.get("role") or "") == "admin":
        return True
    try:
        from core.auth import is_page_admin
        return is_page_admin(me.get("username") or "", "filebrowser")
    except Exception:
        return False


def _require_filebrowser_user(request: Request | None) -> dict:
    if request is None:
        return {}
    from core.auth import current_user
    return current_user(request)


def _require_filebrowser_admin(request: Request | None) -> dict:
    me = _require_filebrowser_user(request)
    if me and me.get("role") != "admin":
        raise HTTPException(403, "admin only")
    return me


def _require_filebrowser_manager(request: Request) -> dict:
    from core.auth import current_user
    me = current_user(request)
    if not _can_manage_filebrowser(me):
        raise HTTPException(403, "Admin or delegated filebrowser admin only")
    return me


def _parse_datetime_like(value: str) -> datetime.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    iso = text.replace("Z", "+00:00")
    try:
        return datetime.datetime.fromisoformat(iso)
    except Exception:
        pass
    for fmt in (
        "%Y-%m-%d", "%Y/%m/%d", "%Y%m%d",
        "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M",
        "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.datetime.strptime(text, fmt)
        except Exception:
            continue
    return None


def _csv_rows_to_frame(header: list[str], data_rows: list[list[str]]) -> pl.DataFrame:
    cols = [str(c or "").strip() for c in header]
    data = {
        col: [row[i] if i < len(row) else "" for row in data_rows]
        for i, col in enumerate(cols)
        if col
    }
    if not data:
        return pl.DataFrame()
    return pl.DataFrame(data)


def _csv_validation_error(errors: list[dict], rule: str, message: str, *,
                          row: int | None = None, column: str = "", value=None,
                          max_errors: int = 200) -> None:
    if len(errors) >= max_errors:
        return
    item = {"rule": rule, "message": message}
    if row is not None:
        item["row"] = int(row)
    if column:
        item["column"] = column
    if value is not None:
        item["value"] = "" if value is None else str(value)
    errors.append(item)


def _validate_csv_rule(header: list[str], data_rows: list[list[str]], rule: dict) -> dict:
    header = [str(c or "").strip() for c in header]
    data_rows, _ = _normalize_rows(data_rows, len(header), "")
    errors: list[dict] = []
    seen_header: set[str] = set()
    for col in header:
        if col in seen_header:
            _csv_validation_error(errors, "columns", f"Duplicate column: {col}", column=col)
        seen_header.add(col)
    columns = set(header)

    def _missing(col: str, rule_name: str) -> bool:
        if col in columns:
            return False
        _csv_validation_error(errors, rule_name, f"Missing column: {col}", column=col)
        return True

    for col in rule.get("required_columns") or []:
        _missing(str(col), "required_columns")

    col_idx = {c: i for i, c in enumerate(header)}
    for col in rule.get("not_empty") or []:
        col = str(col)
        if _missing(col, "not_empty"):
            continue
        idx = col_idx[col]
        for row_no, row in enumerate(data_rows, start=1):
            val = row[idx] if idx < len(row) else ""
            if str(val or "").strip() == "":
                _csv_validation_error(errors, "not_empty", f"{col} must not be empty", row=row_no, column=col, value=val)

    for combo in rule.get("unique_keys") or []:
        cols = [str(c) for c in (combo or [])]
        if any(_missing(c, "unique_keys") for c in cols):
            continue
        indexes = [col_idx[c] for c in cols]
        seen: dict[tuple[str, ...], int] = {}
        for row_no, row in enumerate(data_rows, start=1):
            key = tuple(str(row[i] if i < len(row) else "").strip() for i in indexes)
            if all(v == "" for v in key):
                continue
            if key in seen:
                _csv_validation_error(
                    errors,
                    "unique_keys",
                    f"Duplicate key {cols}: first row {seen[key]}, duplicate row {row_no}",
                    row=row_no,
                    column=",".join(cols),
                    value="|".join(key),
                )
            else:
                seen[key] = row_no

    for col, allowed in (rule.get("enums") or {}).items():
        col = str(col)
        if _missing(col, "enums"):
            continue
        idx = col_idx[col]
        allowed_set = {str(v) for v in (allowed or [])}
        for row_no, row in enumerate(data_rows, start=1):
            val = str(row[idx] if idx < len(row) else "").strip()
            if val == "":
                continue
            if val not in allowed_set:
                _csv_validation_error(errors, "enums", f"{col} must be one of {sorted(allowed_set)}", row=row_no, column=col, value=val)

    for col, spec in (rule.get("numeric") or {}).items():
        col = str(col)
        if _missing(col, "numeric"):
            continue
        idx = col_idx[col]
        spec = spec or {}
        for row_no, row in enumerate(data_rows, start=1):
            val = str(row[idx] if idx < len(row) else "").strip()
            if val == "":
                continue
            try:
                num = float(val)
            except Exception:
                _csv_validation_error(errors, "numeric", f"{col} must be numeric", row=row_no, column=col, value=val)
                continue
            if not math.isfinite(num):
                _csv_validation_error(errors, "numeric", f"{col} must be finite", row=row_no, column=col, value=val)
                continue
            if spec.get("integer") and not num.is_integer():
                _csv_validation_error(errors, "numeric", f"{col} must be an integer", row=row_no, column=col, value=val)
            if spec.get("min") is not None and num < float(spec["min"]):
                _csv_validation_error(errors, "numeric", f"{col} must be >= {spec['min']}", row=row_no, column=col, value=val)
            if spec.get("max") is not None and num > float(spec["max"]):
                _csv_validation_error(errors, "numeric", f"{col} must be <= {spec['max']}", row=row_no, column=col, value=val)

    for col in rule.get("date") or []:
        col = str(col)
        if _missing(col, "date"):
            continue
        idx = col_idx[col]
        for row_no, row in enumerate(data_rows, start=1):
            val = str(row[idx] if idx < len(row) else "").strip()
            if val == "":
                continue
            if _parse_datetime_like(val) is None:
                _csv_validation_error(errors, "date", f"{col} must parse as a date/time", row=row_no, column=col, value=val)

    for col, pattern in (rule.get("regex") or {}).items():
        col = str(col)
        if _missing(col, "regex"):
            continue
        idx = col_idx[col]
        compiled = re.compile(str(pattern))
        for row_no, row in enumerate(data_rows, start=1):
            val = str(row[idx] if idx < len(row) else "")
            if val == "":
                continue
            if compiled.fullmatch(val) is None:
                _csv_validation_error(errors, "regex", f"{col} does not match /{pattern}/", row=row_no, column=col, value=val)

    if rule.get("conditions"):
        try:
            df = _csv_rows_to_frame(header, data_rows)
            if df.height:
                df = df.with_row_index("__row_nr", offset=1)
        except Exception as e:
            df = pl.DataFrame()
            _csv_validation_error(errors, "conditions", f"Cannot build condition frame: {e}")
        for condition in rule.get("conditions") or []:
            expr = str((condition or {}).get("expr") or "").strip()
            if not expr or df.is_empty():
                continue
            try:
                checked = df.with_columns(pl.sql_expr(expr).alias("__condition_ok"))
                violated = checked.filter(~pl.col("__condition_ok").fill_null(False)).select("__row_nr").head(200)
                for item in violated.to_dicts():
                    row_no = int(item.get("__row_nr") or 0)
                    _csv_validation_error(
                        errors,
                        "conditions",
                        (condition or {}).get("message") or f"Condition must be true: {expr}",
                        row=row_no,
                    )
            except Exception as e:
                _csv_validation_error(errors, "conditions", f"Condition failed '{expr}': {e}")

    ordered_by = rule.get("ordered_by") or {}
    order_specs = ordered_by.get("keys") or []
    if order_specs:
        group_by = [str(c) for c in (ordered_by.get("group_by") or [])]
        needed_cols = [str(item.get("column") or "") for item in order_specs] + group_by
        missing = [c for c in needed_cols if c and c not in col_idx]
        for col in missing:
            _csv_validation_error(errors, "ordered_by", f"Missing column: {col}", column=col)
        if not missing:
            prev_row = None
            prev_row_no = 0
            prev_group = None
            group_idx = [col_idx[c] for c in group_by]
            for row_no, row in enumerate(data_rows, start=1):
                group_key = tuple(str(row[i] if i < len(row) else "") for i in group_idx) if group_idx else None
                if prev_row is not None and (not group_idx or group_key == prev_group):
                    comp = _compare_rows_by_specs(col_idx, prev_row, row, order_specs)
                    if comp > 0:
                        _csv_validation_error(
                            errors,
                            "ordered_by",
                            f"Rows must be ordered by {', '.join(str(s.get('column') or '') for s in order_specs)}",
                            row=row_no,
                            column=",".join(str(s.get("column") or "") for s in order_specs),
                            value=f"previous row {prev_row_no}",
                        )
                prev_row = row
                prev_row_no = row_no
                prev_group = group_key

    return {
        "ok": not errors,
        "errors": errors,
        "error_count": len(errors),
        "truncated": len(errors) >= 200,
        "rows": len(data_rows),
        "columns": len(header),
    }


def _sort_cast_value(value: str, typ: str):
    text = str(value or "").strip()
    if text == "":
        return None
    if typ == "numeric":
        try:
            num = float(text)
            return num if math.isfinite(num) else None
        except Exception:
            return None
    if typ == "date":
        return _parse_datetime_like(text)
    if typ == "leading_number":
        m = re.match(r"^\s*([+-]?\d+(?:\.\d+)?)", text)
        if not m:
            return None
        try:
            num = float(m.group(1))
            return num if math.isfinite(num) else None
        except Exception:
            return None
    if typ == "rule_order":
        up = text.upper()
        if up == "RO":
            return (1, 0)
        m = re.fullmatch(r"R(\d+)", up)
        if not m:
            return None
        try:
            return (0, int(m.group(1)))
        except Exception:
            return None
    return text


def _compare_values(left, right) -> int:
    if left == right:
        return 0
    try:
        return -1 if left < right else 1
    except Exception:
        ls, rs = str(left), str(right)
        if ls == rs:
            return 0
        return -1 if ls < rs else 1


def _compare_rows_by_specs(col_idx: dict[str, int], left_row: list[str], right_row: list[str], specs: list[dict]) -> int:
    for spec in specs:
        col = str(spec.get("column") or "")
        idx = col_idx[col]
        typ = str(spec.get("type") or "string")
        nulls = str(spec.get("nulls") or "last")
        direction = str(spec.get("direction") or "asc")
        lv = _sort_cast_value(left_row[idx] if idx < len(left_row) else "", typ)
        rv = _sort_cast_value(right_row[idx] if idx < len(right_row) else "", typ)
        lnull, rnull = lv is None, rv is None
        if lnull or rnull:
            if lnull and rnull:
                continue
            comp = -1 if (lnull and nulls == "first") or (rnull and nulls == "last") else 1
        else:
            comp = _compare_values(lv, rv)
        if comp:
            return -comp if direction == "desc" else comp
    return 0


def _apply_csv_sort_rule(header: list[str], data_rows: list[list[str]], rule: dict) -> list[list[str]]:
    sort_rule = rule.get("sort") or []
    if not sort_rule:
        return data_rows
    header = [str(c or "").strip() for c in header]
    data_rows, _ = _normalize_rows(data_rows, len(header), "")
    col_idx = {c: i for i, c in enumerate(header)}
    missing = [str(item.get("column") or "") for item in sort_rule if str(item.get("column") or "") not in col_idx]
    if missing:
        raise HTTPException(400, f"Sort column not found: {', '.join(missing)}")

    def _cmp(left_item, right_item):
        left_i, left_row = left_item
        right_i, right_row = right_item
        comp = _compare_rows_by_specs(col_idx, left_row, right_row, sort_rule)
        if comp:
            return comp
        return left_i - right_i

    return [row for _, row in sorted(enumerate(data_rows), key=functools.cmp_to_key(_cmp))]


def _validate_and_sort_csv_rows(file: str, header: list[str], data_rows: list[list[str]]) -> tuple[list[list[str]], dict]:
    rule = _csv_rule_for_file(file)
    if not rule:
        return data_rows, {
            "ok": True,
            "errors": [],
            "error_count": 0,
            "truncated": False,
            "rows": len(data_rows),
            "columns": len(header),
            "rule_applied": False,
            "rule_summary": None,
        }
    validation = _validate_csv_rule(header, data_rows, rule)
    validation["rule_applied"] = True
    validation["rule_summary"] = _csv_rule_summary(rule)
    if not validation.get("ok"):
        return data_rows, validation
    sorted_rows = _apply_csv_sort_rule(header, data_rows, rule)
    validation["sorted"] = bool(rule.get("sort"))
    return sorted_rows, validation


def _rows_to_csv_text(header: list[str], data_rows: list[list[str]], delimiter: str, include_header: bool = True) -> str:
    out = io.StringIO()
    writer = csv.writer(out, delimiter="\t" if delimiter == "tab" else ",", lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    if include_header:
        writer.writerow(["" if v is None else str(v) for v in header])
    for row in data_rows:
        writer.writerow(["" if v is None else str(v) for v in row])
    return out.getvalue()


def _base_file_versioned(file: str, target: Path | None = None) -> bool:
    rel = str(file or "").strip().replace("\\", "/").lower()
    name = Path(rel).name.lower()
    parts = Path(rel).parts
    folder = str(parts[0]).casefold() if parts else ""
    if folder == _SINGLE_FILE_STEP_CACHE_DIR:
        return False
    if folder and folder in _versioned_single_file_dir_names():
        if target is None:
            return True
        if not target.is_file():
            return False
        if target.suffix.lower() == ".csv":
            try:
                return target.stat().st_size <= EDM_VERSION_MAX_CSV_BYTES
            except Exception:
                return False
        return target.suffix.lower() in {".parquet", ".json", ".yaml", ".yml", ".md", ".txt"}
    if rel == "product_config/products.yaml":
        return True
    if rel.startswith("reformatter/") and name.endswith(".json"):
        return True
    if target is not None and target.is_file() and target.suffix.lower() == ".csv":
        try:
            return target.stat().st_size <= EDM_VERSION_MAX_CSV_BYTES
        except Exception:
            return False
    if target is None and name in EDM_VERSIONED_SINGLE_FILES:
        # Compatibility fallback for callers that only ask by legacy file name.
        return True
    return False


def _version_file_id(file: str) -> str:
    rel = str(file or "").strip().replace("\\", "/")
    rel = rel.strip("/")
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "__", rel)
    return safe.strip("._-") or "base_file"


def _version_dir(file: str) -> Path:
    return BASE_VERSION_DIR / _version_file_id(file)


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _next_file_version(vdir: Path) -> int:
    try:
        nums = []
        for fp in vdir.glob("v*.meta.json"):
            stem = fp.name.split(".", 1)[0]
            try:
                nums.append(int(stem.lstrip("v")))
            except ValueError:
                pass
        return (max(nums) if nums else 0) + 1
    except Exception:
        return 1


def _next_semver(vdir: Path, *, rows: int | None = None, columns: int | None = None) -> str:
    metas = []
    try:
        for fp in vdir.glob("v*.meta.json"):
            try:
                meta = json.loads(fp.read_text(encoding="utf-8"))
                sem = str(meta.get("display_version") or "")
                m = re.match(r"^v(\d+)\.(\d+)$", sem)
                if m:
                    metas.append((int(m.group(1)), int(m.group(2)), meta))
            except Exception:
                continue
    except Exception:
        metas = []
    if not metas:
        return "v1.0"
    major, minor, latest = sorted(metas, key=lambda x: (x[0], x[1]))[-1]
    prev_rows = latest.get("rows")
    prev_cols = latest.get("columns")
    if (rows is not None and prev_rows is not None and rows != prev_rows) or (columns is not None and prev_cols is not None and columns != prev_cols):
        return f"v{major + 1}.0"
    return f"v{major}.{minor + 1}"


def _cap_file_versions(vdir: Path) -> None:
    try:
        metas = sorted(vdir.glob("v*.meta.json"), key=lambda p: p.stat().st_mtime)
        excess = len(metas) - BASE_VERSION_CAP
        if excess <= 0:
            return
        for meta_fp in metas[:excess]:
            try:
                meta = json.loads(meta_fp.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
            content = meta.get("content_file") or meta_fp.name.replace(".meta.json", meta_fp.suffix)
            for fp in (vdir / str(content), meta_fp):
                try:
                    if fp.exists():
                        fp.unlink()
                except Exception:
                    pass
    except Exception:
        pass


def _file_shape(path: Path) -> tuple[int | None, int | None]:
    try:
        ext = path.suffix.lower()
        if ext in {".csv", ".parquet"}:
            lf = scan_one_file(path)
            if lf is None:
                return None, None
            cols = list(lf.collect_schema().names())
            rows = int(lf.select(pl.len()).collect().item())
            return rows, len(cols)
    except Exception:
        pass
    return None, None


def _file_profile(path: Path) -> dict:
    profile = {
        "size": None,
        "checksum": "",
        "rows": None,
        "columns": [],
        "column_count": None,
    }
    try:
        profile["size"] = path.stat().st_size
        profile["checksum"] = _file_sha256(path)
    except Exception:
        pass
    try:
        if path.suffix.lower() in {".csv", ".parquet"}:
            lf = scan_one_file(path)
            if lf is not None:
                cols = list(lf.collect_schema().names())
                profile["columns"] = cols
                profile["column_count"] = len(cols)
                profile["rows"] = int(lf.select(pl.len()).collect().item())
    except Exception:
        pass
    return profile


def _profile_diff(current: dict, version: dict) -> dict:
    cur_cols = [str(c) for c in (current.get("columns") or [])]
    ver_cols = [str(c) for c in (version.get("columns") or [])]
    cur_set = set(cur_cols)
    ver_set = set(ver_cols)
    cur_size = current.get("size")
    ver_size = version.get("size")
    cur_rows = current.get("rows")
    ver_rows = version.get("rows")
    return {
        "checksum_equal": bool(current.get("checksum") and current.get("checksum") == version.get("checksum")),
        "size_delta": (cur_size - ver_size) if isinstance(cur_size, int) and isinstance(ver_size, int) else None,
        "rows_delta": (cur_rows - ver_rows) if isinstance(cur_rows, int) and isinstance(ver_rows, int) else None,
        "columns_delta": len(cur_cols) - len(ver_cols) if cur_cols or ver_cols else None,
        "added_columns_in_current": [c for c in cur_cols if c not in ver_set],
        "removed_columns_from_current": [c for c in ver_cols if c not in cur_set],
    }


def _latest_version_content(vdir: Path) -> Path | None:
    metas = []
    try:
        for fp in vdir.glob("v*.meta.json"):
            try:
                meta = json.loads(fp.read_text(encoding="utf-8"))
                m = re.match(r"^v(\d+)$", str(meta.get("version") or fp.name.split(".", 1)[0]))
                idx = int(m.group(1)) if m else 0
                content = vdir / str(meta.get("content_file") or "")
                if content.exists():
                    metas.append((idx, content))
            except Exception:
                continue
    except Exception:
        return None
    return sorted(metas, key=lambda x: x[0])[-1][1] if metas else None


def _snapshot_change_summary(current: Path, previous: Path | None) -> dict:
    if previous is None or not previous.exists():
        return {"label": "초기 버전", "rows_delta": None, "columns_delta": None, "changed_cells": None, "added_rows": 0, "deleted_rows": 0, "modified_rows": 0}
    cur_profile = _file_profile(current)
    prev_profile = _file_profile(previous)
    diff = _profile_diff(cur_profile, prev_profile)
    table_diff = _diff_table_between(current, previous)
    counts = table_diff.get("counts") if isinstance(table_diff, dict) else {}
    added_rows = int(counts.get("added") or 0) if isinstance(counts, dict) else 0
    deleted_rows = int(counts.get("deleted") or 0) if isinstance(counts, dict) else 0
    modified_rows = int(counts.get("modified") or 0) if isinstance(counts, dict) else 0
    changed_cells = None
    try:
        if current.suffix.lower() in {".csv", ".parquet"} and previous.suffix.lower() in {".csv", ".parquet"}:
            cur = read_one_file(current)
            prev = read_one_file(previous)
            if cur is not None and prev is not None:
                common_cols = [c for c in cur.columns if c in prev.columns]
                h = min(cur.height, prev.height)
                changed = 0
                if common_cols and h:
                    cur_s = cur.select([pl.col(c).cast(pl.Utf8, strict=False).alias(c) for c in common_cols]).head(h)
                    prev_s = prev.select([pl.col(c).cast(pl.Utf8, strict=False).alias(c) for c in common_cols]).head(h)
                    for c in common_cols:
                        changed += int((cur_s[c] != prev_s[c]).sum())
                changed_cells = changed
    except Exception:
        changed_cells = None
    parts = []
    if modified_rows:
        parts.append(f"수정 {modified_rows}행")
    if added_rows:
        parts.append(f"추가 {added_rows}행")
    if deleted_rows:
        parts.append(f"삭제 {deleted_rows}행")
    if diff.get("columns_delta") not in (None, 0):
        parts.append(("+" if diff["columns_delta"] > 0 else "") + f"{diff['columns_delta']}열")
    if diff.get("added_columns_in_current"):
        parts.append("컬럼추가 " + ",".join(diff["added_columns_in_current"][:3]))
    if diff.get("removed_columns_from_current"):
        parts.append("컬럼삭제 " + ",".join(diff["removed_columns_from_current"][:3]))
    return {
        "label": " / ".join(parts) if parts else "내용 수정" if not diff.get("checksum_equal") else "변경 없음",
        "rows_delta": diff.get("rows_delta"),
        "columns_delta": diff.get("columns_delta"),
        "changed_cells": changed_cells,
        "added_rows": added_rows,
        "deleted_rows": deleted_rows,
        "modified_rows": modified_rows,
        "added_columns": diff.get("added_columns_in_current") or [],
        "removed_columns": diff.get("removed_columns_from_current") or [],
        "checksum_equal": diff.get("checksum_equal"),
    }


def _version_number(version: str) -> int:
    m = re.match(r"^v(\d+)$", str(version or ""))
    return int(m.group(1)) if m else 0


def _previous_version_content(file: str, storage_version: str) -> Path | None:
    target_num = _version_number(storage_version)
    if target_num <= 1:
        return None
    vdir = _version_dir(file)
    candidates = []
    for meta_fp in vdir.glob("v*.meta.json"):
        try:
            meta = json.loads(meta_fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        version = str(meta.get("version") or meta_fp.name.split(".", 1)[0])
        num = _version_number(version)
        content = vdir / str(meta.get("content_file") or "")
        if num and num < target_num and content.exists():
            candidates.append((num, content))
    return sorted(candidates, key=lambda x: x[0])[-1][1] if candidates else None


def _table_rows_for_diff(path: Path, limit: int = 20000) -> tuple[list[str], list[dict[str, str]]]:
    df = read_one_file(path)
    if df is None:
        return [], []
    if df.height > limit:
        df = df.head(limit)
    cols = [str(c) for c in df.columns]
    df = df.select([pl.col(c).cast(pl.Utf8, strict=False).fill_null("").alias(c) for c in cols])
    rows = [{c: str(row.get(c) or "") for c in cols} for row in df.to_dicts()]
    return cols, rows


def _infer_diff_keys(columns: list[str]) -> list[str]:
    by_lower = {c.lower(): c for c in columns}
    candidates = [
        ["product", "step_id"],
        ["product", "ppid"],
        ["product", "item_id"],
        ["process_id", "item_id"],
        ["product", "feature_name"],
        ["step_id"],
        ["item_id"],
    ]
    for keys in candidates:
        if all(k in by_lower for k in keys):
            return [by_lower[k] for k in keys]
    return [columns[0]] if columns else []


def _diff_table_between(current: Path, previous: Path | None, max_changes: int = 1000) -> dict | None:
    if previous is None or not previous.exists():
        return None
    if current.suffix.lower() not in {".csv", ".parquet"} or previous.suffix.lower() not in {".csv", ".parquet"}:
        return None
    try:
        cur_cols, cur_rows = _table_rows_for_diff(current)
        prev_cols, prev_rows = _table_rows_for_diff(previous)
    except Exception:
        return None
    all_cols = list(dict.fromkeys([*cur_cols, *prev_cols]))
    if not all_cols:
        return None
    key_cols = _infer_diff_keys(all_cols)
    if not key_cols:
        key_cols = ["__row_index"]

    def row_key(row: dict[str, str], idx: int):
        if key_cols == ["__row_index"]:
            return (idx,)
        return tuple(row.get(k, "") for k in key_cols)

    cur_map = {row_key(r, i): r for i, r in enumerate(cur_rows)}
    prev_map = {row_key(r, i): r for i, r in enumerate(prev_rows)}
    keys = list(dict.fromkeys([*cur_map.keys(), *prev_map.keys()]))
    out_rows = []
    counts = {"added": 0, "deleted": 0, "modified": 0, "unchanged": 0}
    for key in keys:
        cur = cur_map.get(key)
        prev = prev_map.get(key)
        if cur is not None and prev is None:
            counts["added"] += 1
            row = {"rev": "추가", "changed_cols": "ALL", **{c: cur.get(c, "") for c in all_cols}, "_changed_cols": all_cols}
        elif cur is None and prev is not None:
            counts["deleted"] += 1
            row = {"rev": "삭제", "changed_cols": "ALL", **{c: prev.get(c, "") for c in all_cols}, "_changed_cols": all_cols}
        else:
            changed = [c for c in all_cols if (cur or {}).get(c, "") != (prev or {}).get(c, "")]
            if not changed:
                counts["unchanged"] += 1
                continue
            counts["modified"] += 1
            row = {"rev": "수정", "changed_cols": ", ".join(changed[:12]), **{c: (cur or {}).get(c, "") for c in all_cols}, "_changed_cols": changed}
        out_rows.append(row)
        if len(out_rows) >= max_changes:
            break
    return {
        "kind": "version_diff_table",
        "title": "직전 버전 대비 변경점",
        "columns": ["rev", "changed_cols", *all_cols],
        "key_columns": key_cols,
        "rows": out_rows,
        "counts": counts,
        "truncated": len(out_rows) >= max_changes,
    }


def _snapshot_base_file_version(
    target: Path,
    file: str,
    *,
    actor: str = "",
    action: str = "edit",
    note: str = "",
) -> dict | None:
    if not target.exists() or not _base_file_versioned(file, target):
        return None
    vdir = _version_dir(file)
    vdir.mkdir(parents=True, exist_ok=True)
    vnum = _next_file_version(vdir)
    version = f"v{vnum}"
    previous_content = _latest_version_content(vdir)
    content_name = f"{version}{target.suffix.lower() or '.bin'}"
    content_fp = vdir / content_name
    shutil.copy2(target, content_fp)
    rows, cols = _file_shape(target)
    display_version = _next_semver(vdir, rows=rows, columns=cols)
    change_summary = _snapshot_change_summary(target, previous_content)
    meta = {
        "version": version,
        "display_version": display_version,
        "file": file,
        "artifact_type": "edm_single_file",
        "actor": actor or "",
        "action": action,
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "size": target.stat().st_size,
        "rows": rows,
        "columns": cols,
        "checksum": _file_sha256(target),
        "source_path": str(target),
        "content_file": content_name,
        "note": note or "",
        "change_summary": change_summary,
    }
    (vdir / f"{version}.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    _cap_file_versions(vdir)
    return meta


def _attach_post_save_change_summary(file: str, version_meta: dict | None, after_path: Path) -> dict | None:
    if not isinstance(version_meta, dict):
        return version_meta
    vdir = _version_dir(file)
    content_fp = vdir / str(version_meta.get("content_file") or "")
    if not content_fp.exists():
        return version_meta
    try:
        change_summary = _snapshot_change_summary(after_path, content_fp)
        diff_table = _diff_table_between(after_path, content_fp)
        version_meta = {
            **version_meta,
            "change_summary": change_summary,
            "save_diff_table": diff_table,
            "post_save_profile": _file_profile(after_path),
        }
        meta_fp = vdir / f"{version_meta.get('version')}.meta.json"
        if meta_fp.is_file():
            _write_text_atomic(meta_fp, json.dumps(version_meta, ensure_ascii=False, indent=2))
    except Exception as exc:
        logger.warning("base-file post-save diff skipped file=%s: %s", after_path, exc)
    return version_meta


def _list_base_file_versions(file: str) -> list[dict]:
    vdir = _version_dir(file)
    if not vdir.is_dir():
        return []
    rows = []
    for meta_fp in sorted(vdir.glob("v*.meta.json"), key=lambda p: (p.stat().st_mtime, p.name), reverse=True):
        try:
            meta = json.loads(meta_fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        storage_version = meta.get("version") or meta_fp.name.split(".", 1)[0]
        change_summary = meta.get("change_summary") or {}
        if not any(change_summary.get(k) for k in ("added_rows", "deleted_rows", "modified_rows")):
            content_fp = vdir / str(meta.get("content_file") or "")
            try:
                diff_table = _diff_table_between(content_fp, _previous_version_content(file, storage_version))
                counts = diff_table.get("counts") if isinstance(diff_table, dict) else {}
                if isinstance(counts, dict):
                    added_rows = int(counts.get("added") or 0)
                    deleted_rows = int(counts.get("deleted") or 0)
                    modified_rows = int(counts.get("modified") or 0)
                    parts = []
                    if modified_rows:
                        parts.append(f"수정 {modified_rows}행")
                    if added_rows:
                        parts.append(f"추가 {added_rows}행")
                    if deleted_rows:
                        parts.append(f"삭제 {deleted_rows}행")
                    if parts:
                        change_summary = {
                            **change_summary,
                            "label": " / ".join(parts),
                            "added_rows": added_rows,
                            "deleted_rows": deleted_rows,
                            "modified_rows": modified_rows,
                        }
            except Exception:
                pass
        rows.append(FileVersionMeta(**{
            "version": meta.get("display_version") or meta.get("version") or meta_fp.name.split(".", 1)[0],
            "storage_version": storage_version,
            "file": meta.get("file") or file,
            "artifact_type": meta.get("artifact_type") or "edm_single_file",
            "actor": meta.get("actor") or "",
            "action": meta.get("action") or "edit",
            "created_at": meta.get("created_at") or "",
            "size": meta.get("size"),
            "rows": meta.get("rows"),
            "columns": meta.get("columns"),
            "checksum": meta.get("checksum") or "",
            "note": meta.get("note") or "",
            "change_summary": change_summary,
        }).dict())
    return rows


def _legacy_history_versions(target: Path, file: str) -> list[dict]:
    hist_dir = target.parent / BASE_EDIT_HISTORY_DIR
    if not hist_dir.is_dir():
        return []
    rows = []
    suffix = "_" + target.name
    for fp in sorted(hist_dir.iterdir(), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
        if not fp.is_file() or not fp.name.endswith(suffix):
            continue
        ts_token = fp.name[: -len(suffix)]
        created = ""
        try:
            created = datetime.datetime.strptime(ts_token, "%Y%m%d-%H%M%S").isoformat(timespec="seconds")
        except Exception:
            created = datetime.datetime.fromtimestamp(fp.stat().st_mtime).isoformat(timespec="seconds")
        rows_count, cols_count = _file_shape(fp)
        try:
            checksum = _file_sha256(fp)
            size = fp.stat().st_size
        except Exception:
            checksum = ""
            size = None
        rows.append(FileVersionMeta(**{
            "version": "legacy_" + fp.name,
            "storage_version": "legacy_" + fp.name,
            "file": file,
            "artifact_type": "legacy_history",
            "actor": "",
            "action": "legacy-backup",
            "created_at": created,
            "size": size,
            "rows": rows_count,
            "columns": cols_count,
            "checksum": checksum,
            "note": "Legacy .history backup",
        }).dict())
    return rows


def _resolve_base_version_content(file: str, version: str, target: Path) -> tuple[Path, dict]:
    clean_version = safe_filename(version)
    if re.match(r"^v\d+\.\d+$", clean_version):
        vdir = _version_dir(file)
        for meta_fp in vdir.glob("v*.meta.json"):
            try:
                meta = json.loads(meta_fp.read_text(encoding="utf-8"))
            except Exception:
                continue
            if str(meta.get("display_version") or "") == clean_version:
                clean_version = str(meta.get("version") or meta_fp.name.split(".", 1)[0])
                break
    if clean_version.startswith("legacy_"):
        legacy_name = clean_version[len("legacy_"):]
        hist_root = (target.parent / BASE_EDIT_HISTORY_DIR).resolve()
        cand = (hist_root / legacy_name).resolve()
        try:
            cand.relative_to(hist_root)
        except ValueError:
            raise HTTPException(400, "Invalid legacy version path")
        if not cand.is_file() or not cand.name.endswith("_" + target.name):
            raise HTTPException(404, f"Legacy version not found: {version}")
        meta = next((v for v in _legacy_history_versions(target, file) if v.get("version") == clean_version), None) or {
            "version": clean_version,
            "file": file,
            "artifact_type": "legacy_history",
            "action": "legacy-backup",
        }
        return cand, meta
    vdir = _version_dir(file)
    meta_fp = vdir / f"{clean_version}.meta.json"
    if not meta_fp.exists():
        raise HTTPException(404, f"Version not found: {version}")
    try:
        meta = json.loads(meta_fp.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(500, "Cannot read version metadata")
    content_fp = vdir / str(meta.get("content_file") or "")
    if not content_fp.exists():
        raise HTTPException(404, "Version content missing")
    return content_fp, meta


def _migrate_legacy_history(target: Path, file: str, *, actor: str = "", note: str = "") -> dict:
    if not _base_file_versioned(file, target):
        raise HTTPException(400, "This file is not configured for EDM version migration")
    hist_dir = target.parent / BASE_EDIT_HISTORY_DIR
    if not hist_dir.is_dir():
        return {"migrated": 0, "skipped": 0}
    existing_checksums = {str(v.get("checksum") or "") for v in _list_base_file_versions(file)}
    migrated = 0
    skipped = 0
    suffix = "_" + target.name
    vdir = _version_dir(file)
    vdir.mkdir(parents=True, exist_ok=True)
    for fp in sorted(hist_dir.iterdir(), key=lambda p: p.stat().st_mtime if p.exists() else 0):
        if not fp.is_file() or not fp.name.endswith(suffix):
            continue
        checksum = _file_sha256(fp)
        if checksum in existing_checksums:
            skipped += 1
            continue
        version = f"v{_next_file_version(vdir)}"
        content_name = f"{version}{target.suffix.lower() or '.bin'}"
        shutil.copy2(fp, vdir / content_name)
        rows, cols = _file_shape(fp)
        display_version = _next_semver(vdir, rows=rows, columns=cols)
        try:
            ts_token = fp.name[: -len(suffix)]
            created_at = datetime.datetime.strptime(ts_token, "%Y%m%d-%H%M%S").isoformat(timespec="seconds")
        except Exception:
            created_at = datetime.datetime.fromtimestamp(fp.stat().st_mtime).isoformat(timespec="seconds")
        meta = {
            "version": version,
            "display_version": display_version,
            "file": file,
            "artifact_type": "edm_single_file",
            "actor": actor or "",
            "action": "system_import",
            "created_at": created_at,
            "size": fp.stat().st_size,
            "rows": rows,
            "columns": cols,
            "checksum": checksum,
            "source_path": str(target),
            "content_file": content_name,
            "note": note or f"Migrated from legacy .history: {fp.name}",
            "change_summary": {"label": "migrated legacy backup"},
        }
        (vdir / f"{version}.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        existing_checksums.add(checksum)
        migrated += 1
    _cap_file_versions(vdir)
    return {"migrated": migrated, "skipped": skipped}


def _write_text_atomic(target: Path, payload: str):
    payload_bytes = payload.encode("utf-8")
    if len(payload_bytes) > BASE_FILE_EDIT_MAX_BYTES:
        raise HTTPException(400, f"Replace payload too large: {len(payload_bytes):,} bytes (max {BASE_FILE_EDIT_MAX_BYTES:,})")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as fp_out:
            fp_out.write(payload_bytes)
        os.replace(tmp_name, target)
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except Exception:
            pass


def _write_parquet_atomic(target: Path, df: "pl.DataFrame"):
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        os.close(fd)
        df.write_parquet(tmp_name)
        os.replace(tmp_name, target)
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except Exception:
            pass


def _ensure_base_file_backup(target: Path) -> str | None:
    try:
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_root = target.parent / BASE_EDIT_HISTORY_DIR
        backup_root.mkdir(exist_ok=True)
        backup = backup_root / f"{ts}_{target.name}"
        shutil.copy2(target, backup)
        return str(backup)
    except Exception as e:
        logger.warning("base-file/save backup skipped file=%s: %s", target, e)
        return None


def _db_root():
    return resolve_existing_root("db", PATHS.db_root)


def _base_root():
    return resolve_existing_root("base", PATHS.base_root)


def _date_key_from_text(text: str) -> str:
    m = _DATE_TOKEN_RE.search(str(text or ""))
    if not m:
        return ""
    return "".join(m.groups())


def _date_label_from_key(key: str) -> str:
    key = str(key or "")
    if len(key) != 8:
        return key
    return f"{key[:4]}-{key[4:6]}-{key[6:]}"


def _date_key_from_path(path: Path) -> str:
    for part in path.parts:
        if part.startswith("date="):
            key = _date_key_from_text(part[len("date="):])
            if key:
                return key
    return _date_key_from_text(path.name)


def _latest_date_label_for_dir(directory: Path) -> str:
    keys: list[str] = []
    try:
        for d in directory.iterdir():
            if d.is_dir() and d.name.startswith("date="):
                key = _date_key_from_path(d)
                if key:
                    keys.append(key)
    except Exception:
        pass
    if not keys:
        try:
            for d in directory.rglob("date=*"):
                if d.is_dir():
                    key = _date_key_from_path(d)
                    if key:
                        keys.append(key)
                    if len(keys) >= 200:
                        break
        except Exception:
            pass
    if not keys:
        for fp in data_files_limited(directory, limit=2000):
            key = _date_key_from_path(fp)
            if key:
                keys.append(key)
    return _date_label_from_key(max(keys)) if keys else ""


def _latest_order_column(columns: list[str]) -> str:
    by_lower = {str(c).lower(): str(c) for c in columns}
    for name in _LATEST_COLUMN_PRIORITY:
        if name in by_lower:
            return by_lower[name]
    for name in columns:
        low = str(name).lower()
        if "tkout" in low or "timestamp" in low:
            return str(name)
    for name in columns:
        low = str(name).lower()
        if low.endswith("time") or low.endswith("date") or "datetime" in low:
            return str(name)
    return ""


def _log_dl(username, product, sql, rows, cols, select_cols="", size_bytes=0):
    jsonl_append(DL_LOG, {
        "username": username, "product": product, "sql": sql or "",
        "rows": rows, "cols": cols, "select_cols": select_cols,
        "size_mb": round(size_bytes / 1e6, 2),
    })


def _list_cache_get(key: tuple):
    cached = _LIST_CACHE.get(key)
    if not cached:
        return None
    ts, payload = cached
    if time.monotonic() - ts > LIST_CACHE_TTL_SEC:
        _LIST_CACHE.pop(key, None)
        return None
    return copy.deepcopy(payload)


def _list_cache_set(key: tuple, payload):
    if len(_LIST_CACHE) > 128:
        _LIST_CACHE.clear()
    _LIST_CACHE[key] = (time.monotonic(), copy.deepcopy(payload))
    return payload


def _path_sig(path: Path) -> tuple:
    try:
        st = path.stat()
        return (str(path.resolve()), st.st_mtime, st.st_size)
    except Exception:
        return (str(path), 0.0, 0)


@router.get("/domain")
def domain_info(request: Request = None):
    """v7.2: Expose canonical domain model to frontend (level hierarchy, granularity, DB registry)."""
    _require_filebrowser_user(request)
    from core.domain import DB_REGISTRY, VISIBLE_CANONICAL, LEVEL_ORDER
    return {
        "dbs": {k: v for k, v in DB_REGISTRY.items() if k in VISIBLE_CANONICAL or k == "ML_TABLE"},
        "level_order": LEVEL_ORDER,
        "visible": sorted(list(VISIBLE_CANONICAL)),
    }


@router.get("/roots")
def list_roots(request: Request = None, all: bool = Query(False)):
    """v7.1: only canonical whitelisted DBs (FAB/VM/MASK/KNOB/INLINE/ET/YLD/ML_TABLE).

    Pass ?all=1 to bypass the whitelist (admin diagnostics).

    v8.7.6 fix: hive/flat 파티션 구조를 가진 임의 디렉토리도 DB 섹션에 노출.
    판단 규칙 — 디렉토리 자체 또는 하위에 parquet/csv 데이터 파일이 존재하면
    whitelist 바깥이어도 DB 로 간주. 루트의 단일 파일은 (신규 정책) Base 섹션에서만 보여줌.
    """
    _require_filebrowser_user(request)
    from core.utils import detect_structure
    from core.domain import is_visible_root, is_visible_file, canonical_name, DB_REGISTRY
    result = []
    DB_BASE = _db_root()
    if not DB_BASE.exists():
        return {"roots": []}
    hidden_db_dirs = _hidden_db_dir_names()
    cache_key = ("roots", bool(all), _path_sig(DB_BASE), tuple(sorted(hidden_db_dirs)))
    cached = _list_cache_get(cache_key)
    if cached is not None:
        return cached
    for d in sorted(DB_BASE.iterdir()):
        # v8.1.2: explicit file skip — root-level single files go via Base only (v8.7.6).
        if not d.is_dir():
            continue
        if d.name.casefold() in hidden_db_dirs:
            continue
        # v8.8.7: 숨김/시스템 폴더 스킵 (.trash, .git, __pycache__, 밑줄 시작 관리자용 등).
        if d.name.startswith(".") or d.name.startswith("__") or d.name.startswith("_"):
            continue
        # v8.7.6: whitelist 바깥이어도 데이터가 있으면 표시 (hive/flat 인식).
        file_count = count_data_files(d)
        whitelisted = is_visible_root(d.name)
        if not all and not whitelisted and file_count == 0:
            continue
        canon = canonical_name(d.name) if whitelisted else d.name
        meta = DB_REGISTRY.get(canon, {}) if whitelisted else {}
        structure = "directory"
        try:
            for sub in d.iterdir():
                if sub.is_dir():
                    structure = detect_structure(sub)
                    break
        except Exception:
            pass
        # v8.7.6: parquet 이 루트 직속에만 있어도 flat/hive 로 간주 → DB 노드로 노출
        if structure == "directory" and file_count > 0:
            structure = detect_structure(d) or "flat"
        result.append({
            "name": d.name,
            "canonical": canon,
            "level": meta.get("level", ""),
            "granularity": meta.get("granularity", ""),
            "icon": meta.get("icon", ""),
            "description": meta.get("description", "") if whitelisted else "(auto-detected hive/flat)",
            "path": str(d),
            "structure": structure,
            "dir_count": sum(1 for x in d.iterdir() if x.is_dir()),
            "parquet_count": file_count,
            "whitelisted": whitelisted,
        })
    # v8.1.1: root-level single files are now served ONLY by /root-parquets (sidebar "Root Parquets" section).
    # Keeping them here caused duplication with the DB list section.
    # Sort: directories first by level (L0→L3→wide), then rulebooks
    level_order = {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "wide": 4, "rulebook": 5, "": 6}
    result.sort(key=lambda r: (level_order.get(r.get("level", ""), 99), r["name"]))
    return _list_cache_set(cache_key, {"roots": result})


@router.get("/scopes")
def list_scopes(request: Request = None):
    """v4.1: Enumerate top-level data scopes for the sidebar switcher.

    Returns `DB` (Hive-flat source tree) and `Files` (DB root-level files).
    The API key remains "Base" for frontend compatibility.
    """
    _require_filebrowser_user(request)
    scopes = []
    db_root = _db_root()
    scopes.append({
        "key": "DB",
        "label": "DB",
        "description": "Hive-flat source tree — FAB/VM/MASK/KNOB/INLINE/ET/YLD + wafer_maps",
        "path": str(db_root),
        "exists": db_root.is_dir(),
        "icon": "🗄️",
    })
    base_root = _base_root()
    scopes.append({
        "key": "Base",
        "label": "Files",
        "description": "DB root-level single files (rulebooks / ML_TABLE / features)",
        "path": str(base_root),
        "exists": base_root.is_dir(),
        "icon": "📚",
    })
    return {"scopes": scopes}


@router.get("/scopes/roots")
def list_scope_roots(request: Request = None):
    """Backward-compat path for clients calling `/scopes/roots`.

    Some mobile/automation callers still target this legacy route shape. Keep it
    aligned with `/roots` behavior to avoid 404 regressions while preserving the
    newer API surface.
    """
    return list_roots(request=request)


class CacheMatchRefreshReq(BaseModel):
    target: str = "lot_progress"
    product: str = ""
    source_root: str = ""
    force: bool = True


class CacheMatchSettingsReq(BaseModel):
    target: str = "lot_progress"
    interval_minutes: int = 30
    auto_s3_upload_on_save: bool | None = None
    source_root: str | None = None


class CacheLlmRefreshReq(BaseModel):
    prompt: str = ""
    product: str = ""
    source_root: str = ""
    force: bool = True


class CacheCleanupReq(BaseModel):
    paths: list[str] = []


def _cache_match_target(raw: str) -> str:
    target = str(raw or "").strip().lower()
    if target in {
        "lot_progress", "progress", "latest", "latest_lot",
        "latest_lot_by_root_wafer", "lot_progress_latest_lot_by_root_wafer",
        "current_lot", "current_step", "lot_wf", "lot_wf_current",
    }:
        return "lot_progress"
    raise HTTPException(400, "Only lot_progress cache is supported in FileBrowser.")


def _cache_settings_file() -> Path:
    return PATHS.data_root / "settings.json"


def _lot_progress_source_root_setting() -> str:
    current = load_json(_cache_settings_file(), {})
    if not isinstance(current, dict):
        return ""
    try:
        from core import lot_progress_cache as _lot_progress_cache
        key = getattr(_lot_progress_cache, "SOURCE_ROOT_SETTING_KEY", "lot_progress_source_root")
        return _lot_progress_cache.normalize_lot_progress_source_root(current.get(key, ""))
    except Exception:
        return _cache_safe_text(current.get("lot_progress_source_root", ""), 160)


def _clamp_lot_progress_interval(value) -> int:
    try:
        from core import lot_progress_cache as _lot_progress_cache
        lo = int(getattr(_lot_progress_cache, "CACHE_REFRESH_MINUTES_MIN", 1))
        hi = int(getattr(_lot_progress_cache, "CACHE_REFRESH_MINUTES_MAX", 1440))
        default = int(getattr(_lot_progress_cache, "CACHE_REFRESH_MINUTES_DEFAULT", 30))
    except Exception:
        lo, hi, default = 1, 1440, 30
    try:
        minutes = int(value)
    except Exception:
        minutes = default
    return max(lo, min(hi, minutes))


def _cache_safe_text(value, max_len: int = 160) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"[\x00\r\n]+", " ", text)
    return text[:max(1, max_len)].strip()


def _cache_mtime_iso(fp: Path) -> str:
    try:
        if fp.is_file():
            return datetime.datetime.fromtimestamp(fp.stat().st_mtime).isoformat(timespec="seconds")
    except Exception:
        pass
    return ""


def _lot_progress_cache_status() -> dict:
    from core import lot_progress_cache as _lot_progress_cache

    json_fp = _lot_progress_cache.cache_file()
    parquet_fp = _lot_progress_cache.filebrowser_cache_parquet_file()
    core_status = _lot_progress_cache.cache_status()
    configured_source_root = _lot_progress_source_root_setting() or str(core_status.get("configured_source_root") or "")
    state = load_json(json_fp, {}) if json_fp.is_file() else {}
    if not isinstance(state, dict):
        state = {}
    row_count = int(state.get("count") or 0)
    products: list[str] = []
    updated_at = str(state.get("generated_at") or "")
    if parquet_fp.is_file():
        try:
            lf = pl.scan_parquet(str(parquet_fp))
            names = lf.collect_schema().names()
            row_count = int(lf.select(pl.len().alias("row_count")).collect().item(0, 0) or row_count)
            if "product" in names:
                prod_df = (
                    lf.select(pl.col("product").cast(_SORT_STR, strict=False).alias("product"))
                    .filter(pl.col("product").is_not_null() & (pl.col("product") != ""))
                    .unique()
                    .sort("product")
                    .head(500)
                    .collect()
                )
                products = [str(v) for v in prod_df["product"].to_list() if str(v or "").strip()]
            if "update_time" in names:
                value = lf.select(pl.col("update_time").cast(_SORT_STR, strict=False).max().alias("updated_at")).collect().item(0, 0)
                if value:
                    updated_at = str(value)
        except Exception as e:
            return {
                "ok": False,
                "target": "lot_progress",
                "mode": "scheduled",
                "unit_action": "filebrowser.cache.lot_progress.status",
                "enabled": True,
                "manual_enabled": True,
                "schedule_enabled": True,
                "scheduler_enabled": bool(core_status.get("scheduler_started")),
                "interval_minutes": int(core_status.get("interval_minutes") or _lot_progress_cache.lot_progress_cache_refresh_minutes()),
                "cache_path": str(parquet_fp),
                "json_cache_path": str(json_fp),
                "cache_exists": parquet_fp.is_file(),
                "configured_source_root": configured_source_root,
                "source_root": state.get("source_root") or core_status.get("source_root") or "",
                "source_roots": list(state.get("source_roots") or core_status.get("source_roots") or []),
                "fab_roots": list(state.get("fab_roots") or []),
                "row_count": row_count,
                "products": products,
                "updated_at": updated_at or _cache_mtime_iso(parquet_fp),
                "error": f"{type(e).__name__}: {e}",
                "last_success_at": core_status.get("last_success_at") or "",
                "last_attempt_at": core_status.get("last_attempt_at") or "",
                "freshness_state": core_status.get("freshness_state") or "error",
                "refresh_log_path": core_status.get("refresh_log_path") or "",
                "lock_state": core_status.get("lock_state") or {},
                "running": bool(core_status.get("running")),
                "skipped_by_lock": bool(core_status.get("skipped_by_lock")),
                "files_scanned": int(core_status.get("files_scanned") or 0),
                "rows_seen": int(core_status.get("rows_seen") or 0),
                "auto_s3_upload_on_save": _filebrowser_auto_s3_upload_enabled(),
            }
    if not updated_at:
        updated_at = _cache_mtime_iso(parquet_fp) or _cache_mtime_iso(json_fp)
    interval_minutes = int(core_status.get("interval_minutes") or _lot_progress_cache.lot_progress_cache_refresh_minutes())
    next_refresh_at = ""
    if updated_at:
        try:
            next_refresh_at = (
                datetime.datetime.fromisoformat(updated_at)
                + datetime.timedelta(minutes=interval_minutes)
            ).isoformat(timespec="seconds")
        except Exception:
            next_refresh_at = ""
    return {
        "ok": True,
        "target": "lot_progress",
        "mode": "scheduled",
        "unit_action": "filebrowser.cache.lot_progress.status",
        "enabled": True,
        "manual_enabled": True,
        "schedule_enabled": True,
        "scheduler_enabled": bool(core_status.get("scheduler_started")),
        "interval_minutes": interval_minutes,
        "interval_min": interval_minutes,
        "interval_min_minutes": int(getattr(_lot_progress_cache, "CACHE_REFRESH_MINUTES_MIN", 1)),
        "interval_max_minutes": int(getattr(_lot_progress_cache, "CACHE_REFRESH_MINUTES_MAX", 1440)),
        "next_refresh_at": next_refresh_at,
        "cache_path": str(parquet_fp),
        "json_cache_path": str(json_fp),
        "cache_exists": parquet_fp.is_file(),
        "configured_source_root": configured_source_root,
        "source_root": state.get("source_root") or core_status.get("source_root") or "",
        "source_roots": list(state.get("source_roots") or core_status.get("source_roots") or []),
        "fab_roots": list(state.get("fab_roots") or []),
        "row_count": row_count,
        "total_row_count": row_count,
        "products": products,
        "updated_at": updated_at,
        "latest_updated_at": updated_at,
        "last_success_at": core_status.get("last_success_at") or "",
        "last_attempt_at": core_status.get("last_attempt_at") or "",
        "freshness_state": core_status.get("freshness_state") or "never",
        "refresh_log_path": core_status.get("refresh_log_path") or "",
        "lock_state": core_status.get("lock_state") or {},
        "running": bool(core_status.get("running")),
        "skipped_by_lock": bool(core_status.get("skipped_by_lock")),
        "files_scanned": int(core_status.get("files_scanned") or 0),
        "rows_seen": int(core_status.get("rows_seen") or 0),
        "auto_s3_upload_on_save": _filebrowser_auto_s3_upload_enabled(),
    }


def _refresh_filebrowser_cache_target(target: str, *, product: str = "", source_root: str = "",
                                      force: bool = True, reason: str = "filebrowser") -> dict:
    target = _cache_match_target(target)
    product = _cache_safe_text(product, 120)
    source_root = _cache_safe_text(source_root, 160)
    from core import lot_progress_cache as _lot_progress_cache
    state = _lot_progress_cache.refresh_lot_progress_cache(force=bool(force), source_root=source_root)
    export = _lot_progress_cache.export_lot_progress_parquet(state)
    row_count = int((state or {}).get("count") or export.get("rows") or 0)
    s3_sync = _filebrowser_s3_sync_for_saved_path(_lot_progress_cache.filebrowser_cache_parquet_file())
    return {
        "ok": True,
        "target": "lot_progress",
        "mode": "scheduled",
        "manual_enabled": True,
        "schedule_enabled": True,
        "unit_action": "filebrowser.cache.lot_progress.refresh",
        "row_count": row_count,
        "total_row_count": row_count,
        "updated_at": (state or {}).get("generated_at") or "",
        "cache_path": str(_lot_progress_cache.filebrowser_cache_parquet_file()),
        "json_cache_path": str(_lot_progress_cache.cache_file()),
        "paths": export.get("paths") or [],
        "configured_source_root": (state or {}).get("configured_source_root") or _lot_progress_source_root_setting(),
        "source_root": (state or {}).get("source_root") or "",
        "source_roots": list((state or {}).get("source_roots") or []),
        "fab_roots": list((state or {}).get("fab_roots") or []),
        "files_scanned": int((state or {}).get("files_scanned") or 0),
        "rows_seen": int((state or {}).get("rows_seen") or 0),
        "errors": list((state or {}).get("errors") or [])[:20],
        "last_success_at": (state or {}).get("last_success_at") or (state or {}).get("generated_at") or "",
        "last_attempt_at": (state or {}).get("last_attempt_at") or "",
        "freshness_state": (state or {}).get("freshness_state") or "ok",
        "refresh_log_path": (state or {}).get("refresh_log_path") or "",
        "lock_state": (state or {}).get("lock_state") or {},
        "running": bool((state or {}).get("running")),
        "skipped_by_lock": bool((state or {}).get("skipped_by_lock")),
        "s3_sync": s3_sync,
    }


def _cache_llm_json(text: str) -> dict:
    raw = str(text or "").strip()
    if not raw:
        return {}
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I | re.S).strip()
    candidates = [raw]
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        candidates.append(m.group(0))
    for item in candidates:
        try:
            parsed = json.loads(item)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _cache_prompt_target(prompt: str) -> str:
    text = str(prompt or "")
    low = text.lower()
    if any(token in low or token in text for token in (
        "lot_progress", "lot progress", "lot_wf_current", "latest_lot", "latest lot",
        "현재 step", "현재 스텝", "최신 lot", "최신 랏", "진행 캐시",
    )):
        return "lot_progress"
    if "캐시" in text and any(token in low or token in text for token in ("rawdata", "fab", "lot", "랏", "제품")):
        return "lot_progress"
    return ""


def _cache_prompt_source_root(prompt: str) -> str:
    text = str(prompt or "")
    m = re.search(r"1\.RAWDATA_DB(?:_FAB)?", text, flags=re.I)
    return m.group(0) if m else ""


def _normalize_cache_plan_target(raw: str) -> str:
    try:
        return _cache_match_target(raw)
    except HTTPException:
        return ""


def _cache_llm_plan(prompt: str, *, product: str = "", source_root: str = "") -> dict:
    prompt = _cache_safe_text(prompt, 2000)
    product = _cache_safe_text(product, 120)
    source_root = _cache_safe_text(source_root, 160)
    plan: dict = {}
    llm_info = {"available": False, "used": False, "error": ""}
    try:
        from core import llm_adapter
        llm_info["available"] = bool(llm_adapter.is_available())
        if prompt and llm_info["available"]:
            system = _filebrowser_agent_prompt("cache_refresh.system", (
                "You classify a Flow FileBrowser cache refresh request. "
                "Return only JSON. The only allowed target value is: lot_progress. "
                "lot_progress means lot_progress_latest_lot_by_root_wafer. "
                "If no explicit FAB source root is requested, omit source_root so the saved FileBrowser cache setting is used. "
                "Do not invent paths, DB names, or schedules."
            ))
            ask = json.dumps({
                "user_prompt": prompt,
                "product_hint": product,
                "source_root_hint": source_root,
                "schema": {"target": "lot_progress", "product": "optional", "source_root": "optional", "reason": "short"},
            }, ensure_ascii=False)
            out = llm_adapter.complete(ask, system=system, timeout=8)
            llm_info["used"] = bool(out.get("ok") and out.get("text"))
            if out.get("error"):
                llm_info["error"] = str(out.get("error") or "")
            if out.get("text"):
                plan = _cache_llm_json(str(out.get("text") or ""))
    except Exception as e:
        llm_info["error"] = f"{type(e).__name__}: {e}"
    target = _normalize_cache_plan_target(str(plan.get("target") or ""))
    fallback_target = _cache_prompt_target(prompt)
    if not target:
        target = fallback_target
    source_root_hint = _cache_prompt_source_root(prompt)
    return {
        "target": target,
        "product": _cache_safe_text(plan.get("product") or product, 120),
        "source_root": _cache_safe_text(plan.get("source_root") or source_root or source_root_hint, 160),
        "reason": _cache_safe_text(plan.get("reason") or ("deterministic fallback" if fallback_target else ""), 240),
        "llm": llm_info,
        "raw_plan": {k: plan.get(k) for k in ("target", "product", "source_root", "reason") if k in plan},
    }


@router.get("/cache/match/status")
def cache_match_status(request: Request, target: str = Query("lot_progress"), product: str = Query(""), source_root: str = Query("")):
    _require_filebrowser_user(request)
    target = _cache_match_target(target)
    return _lot_progress_cache_status()


@router.post("/cache/match/settings")
def cache_match_settings(req: CacheMatchSettingsReq, request: Request):
    me = _require_filebrowser_admin(request)
    target = _cache_match_target(req.target)
    minutes = _clamp_lot_progress_interval(req.interval_minutes)
    settings_path = _cache_settings_file()
    current = load_json(settings_path, {})
    if not isinstance(current, dict):
        current = {}
    current["lot_progress_refresh_minutes"] = minutes
    if req.source_root is not None:
        try:
            from core import lot_progress_cache as _lot_progress_cache
            source_root = _lot_progress_cache.normalize_lot_progress_source_root(req.source_root)
            source_root_key = getattr(_lot_progress_cache, "SOURCE_ROOT_SETTING_KEY", "lot_progress_source_root")
        except Exception:
            source_root = _cache_safe_text(req.source_root, 160)
            source_root_key = "lot_progress_source_root"
        current[source_root_key] = source_root
    save_json(settings_path, current, indent=2)
    if req.auto_s3_upload_on_save is not None:
        fb_settings = _load_filebrowser_settings()
        fb_settings["auto_s3_upload_on_save"] = bool(req.auto_s3_upload_on_save)
        _save_filebrowser_settings(fb_settings)
    jsonl_append(PATHS.activity_log, {
        "username": me.get("username") or "",
        "action": "filebrowser:cache-settings:save",
        "tab": "filebrowser",
        "detail": f"lot_progress_refresh_minutes={minutes} lot_progress_source_root={current.get('lot_progress_source_root', '')} auto_s3_upload_on_save={_filebrowser_auto_s3_upload_enabled()}",
    })
    return cache_match_status(request=request, target="lot_progress")


@router.post("/cache/match/refresh")
def cache_match_refresh(req: CacheMatchRefreshReq, request: Request):
    _require_filebrowser_admin(request)
    target = _cache_match_target(req.target)
    return _refresh_filebrowser_cache_target(
        target,
        product=req.product or "",
        source_root=req.source_root or "",
        force=bool(req.force),
        reason="filebrowser",
    )


@router.get("/cache/cleanup-candidates")
def cache_cleanup_candidates(request: Request):
    _require_filebrowser_admin(request)
    return {
        "ok": True,
        "canonical": _CANONICAL_LOT_PROGRESS_CACHE_FILE,
        "candidates": _cache_cleanup_candidates(),
    }


@router.post("/cache/cleanup")
def cache_cleanup(req: CacheCleanupReq, request: Request):
    me = _require_filebrowser_admin(request)
    paths = [str(p or "").strip() for p in (req.paths or []) if str(p or "").strip()]
    if not paths:
        raise HTTPException(400, "paths are required")
    deleted: list[dict] = []
    errors: list[dict] = []
    for raw in paths:
        try:
            target = _resolve_cache_cleanup_path(raw)
            size = target.stat().st_size if target.is_file() else 0
            target.unlink()
            deleted.append({"path": str(target), "size": size})
        except HTTPException:
            raise
        except Exception as exc:
            errors.append({"path": raw, "error": str(exc)})
    try:
        jsonl_append(PATHS.activity_log, {
            "username": me.get("username") or "",
            "action": "filebrowser:cache-cleanup",
            "tab": "filebrowser",
            "detail": f"deleted={len(deleted)} errors={len(errors)}",
        })
    except Exception:
        pass
    return {
        "ok": not errors,
        "deleted": deleted,
        "errors": errors,
        "canonical": _CANONICAL_LOT_PROGRESS_CACHE_FILE,
        "candidates": _cache_cleanup_candidates(),
    }


@router.post("/cache/llm/refresh")
def cache_llm_refresh(req: CacheLlmRefreshReq, request: Request):
    me = _require_filebrowser_admin(request)
    prompt = _cache_safe_text(req.prompt, 2000)
    if not prompt:
        raise HTTPException(400, "prompt is required")
    plan = _cache_llm_plan(prompt, product=req.product or "", source_root=req.source_root or "")
    target = plan.get("target") or ""
    if not target:
        raise HTTPException(400, "LLM/cache prompt must resolve to lot_progress")
    result = _refresh_filebrowser_cache_target(
        target,
        product=plan.get("product") or req.product or "",
        source_root=plan.get("source_root") or req.source_root or "",
        force=bool(req.force),
        reason="filebrowser_llm",
    )
    try:
        jsonl_append(PATHS.activity_log, {
            "username": me.get("username") or "",
            "action": "filebrowser:cache-llm-refresh",
            "tab": "filebrowser",
            "detail": f"target={target} product={plan.get('product') or ''}",
        })
    except Exception:
        pass
    return {
        **result,
        "ok": bool(result.get("ok", True)),
        "unit_action": "filebrowser.cache.llm.refresh",
        "target": target,
        "plan": plan,
        "llm": plan.get("llm") or {},
        "result": result,
    }


@router.get("/base-files")
def base_files(request: Request = None):
    """v4.1: List top-level files under the Base root (single-file layout).

    Returns only the operational files needed by the current ML_TABLE workflow:
    ML_TABLE_*.parquet, the small matching CSVs, and product_config/products.yaml.
    Directories and legacy helper files remain on disk but are not surfaced here.
    """
    _require_filebrowser_user(request)
    base_root = _base_root()
    db_root = _db_root()
    settings = _load_filebrowser_settings()
    single_file_folders = _single_file_folder_names(settings)
    versioned_dirs = _versioned_single_file_dir_names(settings)
    _ensure_single_file_cache_dirs(base_root, db_root)
    if hasattr(PATHS, "cache_dir") and hasattr(PATHS, "db_cache_dir"):
        try:
            from core import lot_progress_cache as _lot_progress_cache
            _lot_progress_cache.export_lot_progress_parquet()
        except Exception as e:
            logger.warning("lot-progress parquet cache export skipped: %s", e)
    _refresh_single_file_step_caches(base_root)
    if db_root != base_root:
        _refresh_single_file_step_caches(db_root)
    cache_key = (
        "base_files",
        tuple(sorted(single_file_folders)),
        tuple(sorted(versioned_dirs)),
        _path_sig(base_root),
        _path_sig(_db_root()),
        _single_file_folder_sigs(base_root, single_file_folders),
        _single_file_folder_sigs(db_root, single_file_folders),
        _path_sig(PATHS.upload_dir),
    )
    cached = _list_cache_get(cache_key)
    if cached is not None:
        return cached
    files, dirs = [], []
    seen_folder_paths: set[str] = set()
    seen_dir_paths: set[str] = set()

    def _add_single_file_folder_entries(root: Path, source_root: str) -> None:
        if not root.is_dir():
            return
        for folder_name in sorted(single_file_folders):
            entries = _single_file_folder_entries(
                root,
                source_root,
                folder_name,
                versioned_dirs=versioned_dirs,
            )
            if not entries:
                continue
            dir_entry = _single_file_folder_dir_entry(root, source_root, folder_name, entries)
            if dir_entry:
                dir_key = str(dir_entry.get("path") or "").lower()
                if dir_key and dir_key not in seen_dir_paths:
                    dirs.append(dir_entry)
                    seen_dir_paths.add(dir_key)
            for entry in entries:
                entry_key = str(entry.get("path") or "").lower()
                if entry_key in seen_folder_paths:
                    continue
                files.append(entry)
                seen_folder_paths.add(entry_key)

    _add_single_file_folder_entries(base_root, "base_root")
    if base_root.is_dir():
        for f in sorted(base_root.iterdir(), key=lambda p: (not p.is_file(), p.name.lower())):
            try:
                stat = f.stat()
            except OSError:
                continue
            if f.is_file():
                if not _visible_single_file(f):
                    continue
                ext = f.suffix.lower()
                meta = _core_file_meta(f.name)
                files.append({
                    "name": f.name,
                    "path": f.name,
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                    "ext": ext.lstrip("."),
                    "kind": "file",
                    "source": "base_root",
                    "role": meta["role"],
                    "description": meta["description"],
                    "order": meta["order"],
                })
            elif f.is_dir():
                continue
    # v8.7.5: DB 루트에 있는 단일 CSV 는 "Base" 로 분류 (물리적 위치와 무관하게 의미적 Base).
    # v8.7.6: 단일 parquet 도 동일 — 폴더(hive/flat) 구조만 DB 섹션에 노출됨.
    # v8.7.7: 같은 파일명이 base_root 와 db_root 양쪽에 있으면 dedup. UI 에 소스 태그
    # (db) 를 노출하던 것도 제거 — 사용자 입장에서 Base 단일 파일은 "한 번만" 보여야 함.
    if db_root.is_dir() and db_root != base_root:
        _add_single_file_folder_entries(db_root, "db_root")
    seen_names = {f["name"].lower() for f in files if f.get("source") != "cache"}
    if db_root.is_dir() and db_root.resolve() != base_root.resolve():
        for f in sorted(db_root.iterdir()):
            if not f.is_file():
                continue
            if not _visible_single_file(f):
                continue
            ext = f.suffix.lower()
            meta = _core_file_meta(f.name)
            if f.name.lower() in seen_names:
                continue
            try:
                stat = f.stat()
            except OSError:
                continue
            files.append({
                "name": f.name,
                "path": f.name,
                "size": stat.st_size,
                "modified": stat.st_mtime,
                "ext": ext.lstrip("."),
                "kind": "file",
                # v8.7.7: source 는 내부적으로만 유지 (preview 라우팅에 필요), UI 태그는 제거.
                "source": "db_root",
                "role": meta["role"],
                "description": meta["description"],
                "order": meta["order"],
            })
            seen_names.add(f.name.lower())
    files.sort(key=lambda x: (x.get("order", 999), x["name"].lower()))
    deduped_dirs = {}
    for d in dirs:
        deduped_dirs.setdefault(str(d.get("name") or "").lower(), d)
    dirs = list(deduped_dirs.values())
    dirs.sort(key=lambda x: (x.get("source", ""), x["name"]))
    return _list_cache_set(cache_key, {"files": dirs + files, "dirs": dirs,
            "path": str(base_root) if base_root.is_dir() else "",
            "exists": base_root.is_dir() or bool(files)})


@router.get("/base-file-view")
def base_file_view(file: str = Query(...), sql: str = Query(""),
                   rows: int = Query(200), cols: int = Query(10),
                   select_cols: str = Query(""),
                   engine: str = Query("auto"),
                   meta_only: bool = Query(False),
                   page: int = Query(0, ge=0),
                   page_size: int = Query(200, ge=1, le=1000),
                   request: Request = None):
    """v4.1: Preview a file under the Base root.

    Parquet/CSV use the same lazy reader path as `/root-parquet-view`; JSON
    files are returned as-is (truncated to first 2KB preview + full size) so
    `_uniques.json` can be inspected.
    """
    _require_filebrowser_user(request)
    rows = rows if isinstance(rows, int) else 200
    cols = cols if isinstance(cols, int) else 10
    page, page_size, _offset = _preview_page_args(rows, page_size)
    rows = page_size
    # Guard against path traversal — allow base_root, and also db_root-level
    # single files (CSV/Parquet). v8.7.7: parquet 도 허용 (base-files 에 노출되므로
    # 미리보기도 가능해야 함).
    base_root = _base_root()
    db_root = _db_root()
    fp = None
    rel = Path(file)
    settings = _load_filebrowser_settings()
    single_file_folders = _single_file_folder_names(settings)
    if rel.parts and str(rel.parts[0]).casefold() in single_file_folders:
        fp = _resolve_single_file_folder_data_path(file, (base_root, db_root), single_file_folders)
        if fp is None:
            raise HTTPException(404, f"Single-file folder item not found: {file}")
    if fp is None and rel.parts and rel.parts[0] == "product_config":
        if len(rel.parts) != 2 or rel.parts[1].startswith(".") or rel.parts[1] in ("", ".", ".."):
            raise HTTPException(400, "Invalid product config path")
        pc_root = (PATHS.data_root / "product_config").resolve()
        cand = (pc_root / rel.parts[1]).resolve()
        try:
            cand.relative_to(pc_root)
        except ValueError:
            raise HTTPException(400, "Invalid product config path")
        if cand.is_file() and cand.suffix.lower() in PRODUCT_CONFIG_EXTENSIONS:
            fp = cand
        else:
            raise HTTPException(404, f"Product config not found: {file}")
    elif fp is None and rel.parts and rel.parts[0] == "uploads":
        if len(rel.parts) != 2 or rel.parts[1].startswith(".") or rel.parts[1] in ("", ".", ".."):
            raise HTTPException(400, "Invalid uploads path")
        up_root = PATHS.upload_dir.resolve()
        cand = (up_root / rel.parts[1]).resolve()
        try:
            cand.relative_to(up_root)
        except ValueError:
            raise HTTPException(400, "Invalid uploads path")
        if cand.is_file() and cand.suffix.lower() in (".csv", ".json", ".txt"):
            fp = cand
        else:
            raise HTTPException(404, f"Registered file not found: {file}")
    elif fp is None and rel.parts and rel.parts[0] == "reformatter":
        suffix = Path(rel.parts[1]).suffix.lower()
        if len(rel.parts) != 2 or rel.parts[1].startswith(".") or rel.parts[1] in ("", ".", "..") or suffix not in (".csv", ".json"):
            raise HTTPException(400, "Invalid reformatter path")
        rf_root = (PATHS.data_root / "reformatter").resolve()
        product = Path(rel.parts[1]).stem
        csv_cand = (rf_root / f"{product}.csv").resolve()
        json_cand = (rf_root / f"{product}.json").resolve()
        cand = csv_cand if csv_cand.is_file() else json_cand
        try:
            cand.relative_to(rf_root)
        except ValueError:
            raise HTTPException(400, "Invalid reformatter path")
        if cand.is_file():
            try:
                from core.reformatter import REFORMATTER_TABLE_COLUMNS, load_rules, rules_to_reformatter_table
                if cand.suffix.lower() == ".csv":
                    df = pl.read_csv(str(cand), infer_schema_length=5000, try_parse_dates=False)
                    page, page_size, offset = 0, df.height, 0
                    rows_out = serialize_rows(df.to_dicts())
                    columns = list(df.columns)
                    total_rows = df.height
                    dtypes = {c: str(df.schema[c]) for c in columns}
                else:
                    rows_all = rules_to_reformatter_table(load_rules(rf_root, product))
                    page, page_size, offset = 0, len(rows_all), 0
                    rows_out = rows_all
                    columns = REFORMATTER_TABLE_COLUMNS
                    total_rows = len(rows_all)
                    dtypes = {c: "str" for c in columns}
                return {
                    "kind": "table",
                    "file": file,
                    "product": product,
                    "columns": columns,
                    "all_columns": columns,
                    "total_cols": len(columns),
                    "data": rows_out,
                    "showing": len(rows_out),
                    "showing_cols": columns,
                    "total_rows": total_rows,
                    "page": page,
                    "page_size": page_size,
                    "has_more": offset + len(rows_out) < total_rows,
                    "dtypes": dtypes,
                    "source_path": str(cand),
                    "source_modified": cand.stat().st_mtime,
                    "source_format": cand.suffix.lower().lstrip("."),
                }
            except Exception as e:
                raise HTTPException(400, f"Cannot read reformatter: {e}")
        raise HTTPException(404, f"Reformatter not found: {file}")
    for candidate_root in (base_root, db_root):
        if fp is not None:
            break
        if not candidate_root.is_dir():
            continue
        cand = (candidate_root / file).resolve()
        try:
            cand.relative_to(candidate_root.resolve())
        except ValueError:
            continue
        if cand.is_file():
            # v8.7.7: db_root 도 CSV + parquet 모두 Base 단일 파일로 취급.
            if candidate_root == db_root and cand.suffix.lower() not in (".csv", ".parquet"):
                continue
            fp = cand
            break
    if fp is None:
        raise HTTPException(404, f"File not found in Base or DB root: {file}")

    ext = fp.suffix.lower()
    if ext == ".json":
        try:
            text = fp.read_text(encoding="utf-8")
        except Exception as e:
            raise HTTPException(400, f"Cannot read JSON: {e}")
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
        return {
            "kind": "json",
            "file": file,
            "size": fp.stat().st_size,
            "preview": text,
            "truncated": False,
            "parsed_top_keys": list(parsed.keys()) if isinstance(parsed, dict) else None,
        }
    if ext == ".md":
        try:
            text = fp.read_text(encoding="utf-8")
        except Exception as e:
            raise HTTPException(400, f"Cannot read md: {e}")
        return {"kind": "md", "file": file, "size": fp.stat().st_size, "text": text,
                "truncated": False}
    if ext in PRODUCT_CONFIG_EXTENSIONS:
        try:
            text = fp.read_text(encoding="utf-8")
        except Exception as e:
            raise HTTPException(400, f"Cannot read yaml: {e}")
        parsed_keys = None
        try:
            from core import product_config as _pc
            parsed = _pc.parse_text(text)
            parsed_keys = list(parsed.keys()) if isinstance(parsed, dict) else None
        except Exception:
            parsed_keys = None
        return {"kind": "yaml", "file": file, "size": fp.stat().st_size, "text": text,
                "truncated": False, "parsed_top_keys": parsed_keys}
    if ext not in DATA_EXTENSIONS:
        raise HTTPException(400, f"Unsupported ext for preview: {ext}")
    # v8.4.3 OOM-aware — lazy scan 동일.
    try:
        if meta_only and ext == ".parquet":
            try:
                from core.parquet_perf import read_meta
                cached_meta = read_meta(fp)
            except Exception:
                cached_meta = None
            cached_schema = (cached_meta or {}).get("schema") or {}
            if cached_schema:
                all_cols_full = list(cached_schema.keys())
                schema_full = {n: str(cached_schema[n]) for n in all_cols_full}
                return {
                    "kind": "table", "file": file,
                    "all_columns": all_cols_full, "total_cols": len(all_cols_full),
                    "columns": all_cols_full[:cols], "dtypes": schema_full,
                    "data": [], "showing": 0, "showing_cols": [],
                    "total_rows": int((cached_meta or {}).get("row_count") or 0),
                    "meta_only": True,
                    "page": page, "page_size": page_size, "has_more": False,
                    "meta_cached": True,
                    "source_path": str(fp),
                    "source_size": fp.stat().st_size,
                    "source_modified": fp.stat().st_mtime,
                    "csv_rule_summary": None,
                }
        lf = scan_one_file(fp)
        if lf is None:
            raise HTTPException(400, f"Cannot read: {file}")
        full_schema_obj = lf.collect_schema()
        all_cols_full = list(full_schema_obj.names())
        schema_full = {n: str(full_schema_obj[n]) for n in all_cols_full}
        # v8.8.16: meta_only 빠른 경로 — 스키마만 돌려주고 collect 없음.
        if meta_only:
            cached_meta = None
            if ext == ".parquet":
                try:
                    from core.parquet_perf import read_meta
                    cached_meta = read_meta(fp)
                except Exception:
                    cached_meta = None
            return {
                "kind": "table", "file": file,
                "all_columns": all_cols_full, "total_cols": len(all_cols_full),
                "columns": all_cols_full[:cols], "dtypes": schema_full,
                "data": [], "showing": 0, "showing_cols": [],
                "total_rows": int((cached_meta or {}).get("row_count") or 0),
                "meta_only": True,
                "page": page, "page_size": page_size, "has_more": False,
                "meta_cached": bool(cached_meta),
                "csv_rule_summary": _csv_rule_summary(_csv_rule_for_file(file)) if ext == ".csv" else None,
            }
        cached_meta = None
        if ext == ".parquet":
            try:
                from core.parquet_perf import read_meta
                cached_meta = read_meta(fp)
            except Exception:
                cached_meta = None
        ml_table = _is_ml_table_file(fp)
        csv_rule_summary = _csv_rule_summary(_csv_rule_for_file(file, settings)) if ext == ".csv" else None
        csv_full_read = False
        if ext == ".csv":
            try:
                csv_full_read = fp.stat().st_size <= int(settings.get("csv_full_read_max_bytes") or 0)
            except Exception:
                csv_full_read = False
        # CSV under the configured byte threshold is safe to read fully for
        # editing only on the initial open. SQL/column selection uses the same
        # capped preview path as DB sources so the page stays responsive.
        full_single_file = csv_full_read and not _has_view_filter(sql, select_cols)
        if full_single_file:
            resp = _run_view_lazy_full(
                lf, sql, select_cols,
                preview_cols=cols if ml_table else None,
            )
            resp["all_columns"] = all_cols_full
            resp["total_cols"] = len(all_cols_full)
            resp["dtypes"] = schema_full
            resp["kind"] = "table"
            resp["file"] = file
            resp["source_path"] = str(fp)
            resp["source_size"] = fp.stat().st_size
            resp["source_modified"] = fp.stat().st_mtime
            resp["csv_full_read_max_bytes"] = settings.get("csv_full_read_max_bytes")
            resp["csv_rule_summary"] = csv_rule_summary
            return resp
        if duckdb_engine.should_use_duckdb([fp], engine=engine, sql=sql, select_cols=select_cols):
            try:
                resp = _run_view_duckdb(
                    [fp], sql, select_cols, rows,
                    page=page, page_size=page_size, preview_cols=cols,
                    cached_meta=cached_meta,
                )
                resp["kind"] = "table"
                resp["file"] = file
                resp["source_path"] = str(fp)
                resp["source_size"] = fp.stat().st_size
                resp["source_modified"] = fp.stat().st_mtime
                resp["csv_full_read_max_bytes"] = settings.get("csv_full_read_max_bytes")
                resp["csv_rule_summary"] = csv_rule_summary
                return _mark_preview_capped(resp)
            except Exception as e:
                if str(engine or "").lower() in {"duckdb", "on", "true", "1"}:
                    raise HTTPException(400, f"DuckDB query failed: {e}")
                logger.warning("duckdb base-file-view fallback file=%s: %s", file, e)
        resp = _run_view_lazy(
            lf, sql, select_cols, rows,
            page=page, page_size=page_size, cached_meta=cached_meta,
            preview_cols=cols,
        )
        resp["all_columns"] = all_cols_full
        resp["total_cols"] = len(all_cols_full)
        resp["dtypes"] = schema_full
        resp["kind"] = "table"
        resp["file"] = file
        resp["source_path"] = str(fp)
        resp["source_size"] = fp.stat().st_size
        resp["source_modified"] = fp.stat().st_mtime
        resp["csv_full_read_max_bytes"] = settings.get("csv_full_read_max_bytes")
        resp["csv_rule_summary"] = csv_rule_summary
        return _mark_preview_capped(resp)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Error: {str(e)}")


@router.get("/products")
def list_products(root: str = Query(...)):
    """List products available under a root.

    v8.2.2 — Hive-partitioned layout support:
      If the root's immediate subdirs are NOT `product=<P>/` (e.g. the FAB
      root contains `fab_history/` which then contains `product=<P>/`),
      walk one level deeper so the sidebar shows real product names
      (PRODUCT_A0, PRODUCT_A1, ...) instead of table names (fab_history,
      et_wafer, ...).  For tables in multi-table roots we aggregate the
      parquet count across all tables hosting that product.
    """
    db_root = _db_root()
    rp = resolve_named_child(db_root, root) or (db_root / root)
    if not rp.is_dir():
        raise HTTPException(404)
    cache_key = ("products", str(root), _path_sig(rp))
    cached = _list_cache_get(cache_key)
    if cached is not None:
        return cached

    # 1. Collect every `product=<P>` directory at depth 1 or 2.
    direct_hive = [d for d in rp.iterdir()
                   if d.is_dir() and d.name.startswith("product=")]
    nested_hive = []
    for sub in rp.iterdir():
        if not sub.is_dir() or sub.name.startswith("product="):
            continue
        for inner in sub.iterdir():
            if inner.is_dir() and inner.name.startswith("product="):
                nested_hive.append(inner)
    hive_dirs = direct_hive + nested_hive

    if hive_dirs:
        # Group partitions by the product value (strip `product=` prefix).
        by_name: dict[str, list] = {}
        for d in hive_dirs:
            name = d.name[len("product="):]
            by_name.setdefault(name, []).append(d)
        prods = []
        for name in sorted(by_name):
            parts = by_name[name]
            total_files = 0
            latest_dates = []
            for p in parts:
                total_files += count_data_files(p)
                latest = _latest_date_label_for_dir(p)
                if latest:
                    latest_dates.append(latest)
            prods.append({
                "name": name,
                "date_count": 0,
                "parquet_count": total_files,
                "latest_date": max(latest_dates) if latest_dates else "",
                "structure": "hive",
            })
        return _list_cache_set(cache_key, {"products": prods})

    # 2. Legacy fallback — emit each subdir as a "product" (pre-v8.2.2 behaviour).
    prods = []
    for d in sorted(rp.iterdir()):
        if not d.is_dir():
            continue
        data_file_count = count_data_files(d)
        if not data_file_count:
            continue
        has_hive = any(x.is_dir() and x.name.startswith("date=") for x in d.iterdir())
        structure = "hive" if has_hive else "flat"
        dates = sorted([x.name.replace("date=", "")
                        for x in d.iterdir()
                        if x.is_dir() and x.name.startswith("date=")])
        latest_date = _date_label_from_key(_date_key_from_text(dates[-1])) if dates else _latest_date_label_for_dir(d)
        prods.append({
            "name": d.name, "date_count": len(dates), "parquet_count": data_file_count,
            "latest_date": latest_date, "structure": structure,
        })
    return _list_cache_set(cache_key, {"products": prods})


def _page_args(page: int = 0, page_size: int = 200) -> tuple[int, int, int]:
    try:
        page = max(0, int(page or 0))
    except Exception:
        page = 0
    try:
        page_size = max(1, min(1000, int(page_size or 200)))
    except Exception:
        page_size = 200
    return page, page_size, page * page_size


def _preview_page_args(rows: int = LATEST_PREVIEW_ROWS, page_size: int = LATEST_PREVIEW_ROWS) -> tuple[int, int, int]:
    try:
        capped = min(LATEST_PREVIEW_ROWS, max(1, int(page_size or rows or LATEST_PREVIEW_ROWS)))
    except Exception:
        capped = LATEST_PREVIEW_ROWS
    return 0, capped, 0


def _mark_preview_capped(resp: dict) -> dict:
    if not isinstance(resp, dict):
        return resp
    resp["page"] = 0
    resp["has_more"] = False
    resp["preview_row_limit"] = LATEST_PREVIEW_ROWS
    resp["download_max_rows"] = MAX_CSV_DOWNLOAD_MAX_ROWS
    resp["download_max_bytes"] = MAX_CSV_DOWNLOAD_BYTES
    return resp


def _resolve_product_dir_fast(root: str, product: str) -> Path | None:
    """Resolve a logical product folder without recursively listing all files."""
    root_path = (_db_root() / root).resolve()
    if not root_path.is_dir():
        return None
    direct = root_path / product
    if direct.is_dir():
        return direct
    ci = resolve_named_child(root_path, product)
    if ci is not None and ci.is_dir():
        return ci
    target = str(product or "").casefold()
    try:
        for name, path, _structure in iter_source_product_dirs(root_path):
            if str(name or "").casefold() == target:
                return path
    except Exception:
        return None
    return None


def _first_data_file(directory: Path, suffixes: tuple[str, ...]) -> Path | None:
    suffix_set = {s.lower() for s in suffixes}
    try:
        for fp in directory.rglob("*"):
            if fp.is_file() and fp.suffix.lower() in suffix_set:
                return fp
    except Exception:
        return None
    return None


def _fast_product_meta_response(root: str, product: str, cols: int,
                                page: int = 0, page_size: int = 200) -> dict | None:
    """Return schema-only metadata for huge DB products without scanning every partition."""
    prod_dir = _resolve_product_dir_fast(root, product)
    if prod_dir is None:
        return None
    fp = _first_data_file(prod_dir, (".parquet",)) or _first_data_file(prod_dir, (".csv",))
    if fp is None:
        return None
    cached_meta = None
    schema_full = {}
    if fp.suffix.lower() == ".parquet":
        try:
            from core.parquet_perf import read_meta
            cached_meta = read_meta(fp)
        except Exception:
            cached_meta = None
        cached_schema = (cached_meta or {}).get("schema") or {}
        if cached_schema:
            schema_full = {str(k): str(v) for k, v in cached_schema.items()}
        else:
            lf = scan_one_file(fp)
            if lf is None:
                return None
            schema_obj = lf.collect_schema()
            schema_full = {n: str(schema_obj[n]) for n in schema_obj.names()}
    else:
        lf = scan_one_file(fp)
        if lf is None:
            return None
        schema_obj = lf.collect_schema()
        schema_full = {n: str(schema_obj[n]) for n in schema_obj.names()}
    if "INLINE" in str(root or "").upper():
        schema_full = {
            str(k): str(v)
            for k, v in schema_full.items()
            if str(k).lower() not in {"shot_x", "shot_y"}
        }
    all_cols_full = list(schema_full.keys())
    _, page_size, _ = _page_args(page, page_size)
    try:
        st = fp.stat()
        source_modified = st.st_mtime
        source_size = st.st_size
    except Exception:
        source_modified = None
        source_size = None
    return {
        "kind": "table",
        "root": root,
        "product": product,
        "all_columns": all_cols_full,
        "total_cols": len(all_cols_full),
        "columns": all_cols_full[:_preview_cols_limit(cols)],
        "dtypes": schema_full,
        "data": [],
        "showing": 0,
        "showing_cols": [],
        "total_rows": int((cached_meta or {}).get("row_count") or 0),
        "meta_only": True,
        "page": page,
        "page_size": page_size,
        "has_more": False,
        "meta_cached": bool(cached_meta),
        "meta_sample_file": fp.name,
        "source_path": str(prod_dir),
        "source_size": source_size,
        "source_modified": source_modified,
    }


def _preview_cols_limit(raw: int | None = None) -> int:
    try:
        return max(1, min(200, int(raw or 20)))
    except Exception:
        return 20


def _is_ml_table_file(fp_or_name) -> bool:
    try:
        stem = Path(str(fp_or_name or "")).stem
    except Exception:
        stem = str(fp_or_name or "")
    return stem.upper().startswith("ML_TABLE_")


def _has_view_filter(sql: str, select_cols: str) -> bool:
    return bool(str(sql or "").strip() or str(select_cols or "").strip())


def _selected_columns(all_columns: list[str], select_cols: str, preview_cols: int | None = None) -> tuple[list[str], bool]:
    if select_cols and select_cols.strip():
        allowed = set(all_columns)
        selected = [c.strip() for c in select_cols.split(",") if c.strip() in allowed]
        return selected, False
    limit = _preview_cols_limit(preview_cols)
    return all_columns[:limit], len(all_columns) > limit


def _lazy_filter_expr(sql: str, columns: list[str]):
    s = _normalize_wafer_sql_filter(sql, columns)
    if not s:
        return None
    try:
        return pl.sql_expr(s)
    except Exception as sql_err:
        try:
            ns = {c: pl.col(c) for c in columns}
            return eval(s, {"__builtins__": {}, "pl": pl}, ns)  # noqa: S307
        except Exception as eval_err:
            raise HTTPException(400, f"SQL error: {sql_err} | expr error: {eval_err}")


_AI_SQL_FORBIDDEN_RE = re.compile(
    r";|--|/\*|\*/|\b("
    r"ATTACH|CALL|COPY|CREATE|DELETE|DETACH|DROP|EXPORT|FROM|GROUP\s+BY|"
    r"IMPORT|INSERT|INSTALL|JOIN|LIMIT|LOAD|OFFSET|ORDER\s+BY|PRAGMA|"
    r"SELECT|SET|TRUNCATE|UPDATE|VACUUM|WITH"
    r")\b",
    re.I,
)
_AI_SQL_IGNORE_TOKENS = {
    *_SQL_EXPR_IGNORE_TOKENS,
    "and", "or", "not", "like", "ilike", "between", "in", "is",
    "null", "true", "false", "where", "is_in", "is_null", "is_not_null",
    "str", "contains", "starts_with", "ends_with", "cast", "try_cast", "as",
    "bigint", "int64", "integer", "int", "double", "float",
}


def _strip_sql_literals(expr: str) -> str:
    return re.sub(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"", " ", str(expr or ""))


def _canonicalize_sql_columns(expr: str, columns: list[str]) -> str:
    lookup = _column_lookup(columns)
    if not lookup:
        return str(expr or "").strip()
    parts = re.split(r"('(?:''|[^'])*'|\"(?:\"\"|[^\"])*\")", str(expr or ""))
    for idx in range(0, len(parts), 2):
        parts[idx] = re.sub(
            r"\b[A-Za-z_][A-Za-z0-9_]*\b",
            lambda m: lookup.get(m.group(0).casefold(), m.group(0)),
            parts[idx],
        )
    return "".join(parts).strip()


def _sql_missing_columns(expr: str, columns: list[str]) -> list[str]:
    lookup = _column_lookup(columns)
    if not lookup:
        return []
    missing: list[str] = []
    for token in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", _strip_sql_literals(expr)):
        key = token.casefold()
        if key in lookup or key in _AI_SQL_IGNORE_TOKENS:
            continue
        if token not in missing:
            missing.append(token)
    return missing


_AI_SQL_COMPARE_RE = re.compile(
    r"\b(?P<col>[A-Za-z_][A-Za-z0-9_]*)\s*"
    r"(?P<op>>=|<=|<>|!=|==|=|>|<)\s*"
    r"(?P<rhs>'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|"
    r"\d{4}[-/.]\d{1,2}(?:[-/.]\d{1,2})?(?:[T\s]\d{1,2}:\d{1,2}(?::\d{1,2})?)?|"
    r"-?\d+(?:\.\d+)?)",
    re.I,
)


def _unquote_ai_sql_literal(raw: str) -> tuple[str, bool]:
    text = str(raw or "").strip()
    if len(text) >= 2 and text[0] == "'" and text[-1] == "'":
        return text[1:-1].replace("''", "'"), True
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return text[1:-1].replace('""', '"'), True
    return text, False


def _validate_ai_sql_date_literals(sql: str, columns: list[str]) -> None:
    lookup = _column_lookup(columns)
    date_cols = {
        lookup.get(str(col).casefold(), str(col)).casefold()
        for col in columns
        if _looks_date_like_column(str(col))
    }
    if not date_cols:
        return
    for match in _AI_SQL_COMPARE_RE.finditer(str(sql or "")):
        col = lookup.get(match.group("col").casefold(), match.group("col"))
        if col.casefold() not in date_cols:
            continue
        value, quoted = _unquote_ai_sql_literal(match.group("rhs"))
        compact_or_partial = bool(re.fullmatch(r"\d{4}(?:[-/.]?\d{1,2})?", value))
        compact_ymd = bool(re.fullmatch(r"\d{8}", value))
        slash_or_dot_date = bool(re.fullmatch(r"\d{4}[/.]\d{1,2}[/.]\d{1,2}(?:[T\s].*)?", value))
        bare_number = (not quoted) and bool(re.fullmatch(r"-?\d+(?:\.\d+)?", value))
        bare_date = (not quoted) and bool(re.fullmatch(r"\d{4}[-/.]\d{1,2}(?:[-/.]\d{1,2})?(?:[T\s].*)?", value))
        if compact_or_partial or compact_ymd or slash_or_dot_date or bare_number or bare_date:
            raise ValueError(
                "AI SQL date/time filters must use complete quoted ISO literals "
                "such as '2024-04-20' or '2024-04-20T13:30:00'"
            )


def _extract_llm_sql_text(raw_text: str, plan: dict) -> str:
    if isinstance(plan, dict):
        for key in ("sql", "filter", "where", "expression", "expr"):
            val = plan.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    text = str(raw_text or "").strip()
    text = re.sub(r"^```(?:sql|json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    return text


def _validate_ai_sql_filter(raw_sql: str, columns: list[str]) -> tuple[str, list[str]]:
    warnings: list[str] = []
    sql = str(raw_sql or "").strip()
    sql = re.sub(r"^where\s+", "", sql, flags=re.I).strip()
    if not sql:
        raise ValueError("LLM did not return a SQL filter expression")
    if _AI_SQL_FORBIDDEN_RE.search(sql):
        raise ValueError("AI SQL must be a read-only filter expression, not a full SQL statement")
    sql = _canonicalize_sql_columns(sql, columns)
    missing = _sql_missing_columns(sql, columns)
    if missing:
        raise ValueError("AI SQL referenced unknown column(s): " + ", ".join(missing[:8]))
    _validate_ai_sql_date_literals(sql, columns)
    duckdb_engine.normalize_filter_expr(sql)
    _lazy_filter_expr(sql, columns or ["value"])
    return sql, warnings


def _read_sql_token(sql: str, start: int) -> tuple[int, int, str] | None:
    text = str(sql or "")
    idx = start
    while idx < len(text) and text[idx].isspace():
        idx += 1
    if idx >= len(text):
        return None
    if text[idx] in {"'", '"'}:
        quote = text[idx]
        end = idx + 1
        value_chars: list[str] = []
        while end < len(text):
            ch = text[end]
            if ch == quote:
                if end + 1 < len(text) and text[end + 1] == quote:
                    value_chars.append(quote)
                    end += 2
                    continue
                return idx, end + 1, "".join(value_chars)
            value_chars.append(ch)
            end += 1
        return None
    match = re.match(r"[#A-Za-z0-9_.+-]+", text[idx:])
    if not match:
        return None
    return idx, idx + match.end(), match.group(0)


def _mask_sql_literals(sql: str) -> str:
    text = str(sql or "")
    out = list(text)
    idx = 0
    while idx < len(text):
        if text[idx] not in {"'", '"'}:
            idx += 1
            continue
        quote = text[idx]
        idx += 1
        while idx < len(text):
            out[idx] = " "
            if text[idx] == quote:
                if idx + 1 < len(text) and text[idx + 1] == quote:
                    out[idx + 1] = " "
                    idx += 2
                    continue
                idx += 1
                break
            idx += 1
    return "".join(out)


def _wafer_literal_number(raw: str) -> int | None:
    text = str(raw or "").strip().strip("'\"").upper()
    text = re.sub(r"^(?:#|WAFER|WF|W)\s*", "", text)
    if not re.fullmatch(r"\d+", text):
        return None
    value = int(text)
    return value if value >= 1 else None


def _split_sql_list_values(body: str) -> list[str]:
    values: list[str] = []
    idx = 0
    text = str(body or "")
    while idx < len(text):
        while idx < len(text) and text[idx] in {" ", "\t", "\n", "\r", ","}:
            idx += 1
        token = _read_sql_token(text, idx)
        if token is None:
            return []
        _start, end, value = token
        values.append(value)
        idx = end
        while idx < len(text) and text[idx].isspace():
            idx += 1
        if idx < len(text) and text[idx] == ",":
            idx += 1
            continue
        if idx < len(text):
            return []
    return values


def _overlaps(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(not (end <= old_start or start >= old_end) for old_start, old_end in spans)


def _normalize_wafer_sql_filter(sql: str, columns: list[str] | tuple[str, ...] | None) -> str:
    text = str(sql or "").strip()
    wafer_col = _wafer_column(list(columns or []))
    if not text or not wafer_col:
        return text
    col_pat = re.escape(wafer_col)
    cast_col = lambda col: f"CAST({col} AS BIGINT)"
    mask = _mask_sql_literals(text)
    replacements: list[tuple[int, int, str]] = []
    occupied: list[tuple[int, int]] = []

    for match in re.finditer(rf"(?<![A-Za-z0-9_])(?P<col>{col_pat})(?![A-Za-z0-9_])\s+(?P<neg>NOT\s+)?IN\s*\((?P<body>[^)]*)\)", mask, flags=re.I):
        span = match.span()
        if _overlaps(span, occupied):
            continue
        body_start, body_end = match.span("body")
        values = _split_sql_list_values(text[body_start:body_end])
        nums = [_wafer_literal_number(value) for value in values]
        if not nums or any(num is None for num in nums):
            continue
        op = "NOT IN" if match.group("neg") else "IN"
        replacement = f"{cast_col(match.group('col'))} {op} ({', '.join(str(num) for num in nums if num is not None)})"
        replacements.append((span[0], span[1], replacement))
        occupied.append(span)

    for match in re.finditer(rf"(?<![A-Za-z0-9_])(?P<col>{col_pat})(?![A-Za-z0-9_])\s+BETWEEN\s+", mask, flags=re.I):
        start = match.start()
        first = _read_sql_token(text, match.end())
        if not first:
            continue
        and_match = re.match(r"\s+AND\s+", mask[first[1]:], flags=re.I)
        if not and_match:
            continue
        second = _read_sql_token(text, first[1] + and_match.end())
        if not second:
            continue
        nums = [_wafer_literal_number(first[2]), _wafer_literal_number(second[2])]
        if any(num is None for num in nums):
            continue
        span = (start, second[1])
        if _overlaps(span, occupied):
            continue
        replacement = f"{cast_col(match.group('col'))} BETWEEN {nums[0]} AND {nums[1]}"
        replacements.append((span[0], span[1], replacement))
        occupied.append(span)

    for match in re.finditer(rf"(?<![A-Za-z0-9_])(?P<col>{col_pat})(?![A-Za-z0-9_])\s*(?P<op>>=|<=|<>|!=|==|=|>|<)\s*", mask, flags=re.I):
        token = _read_sql_token(text, match.end())
        if not token:
            continue
        num = _wafer_literal_number(token[2])
        if num is None:
            continue
        span = (match.start(), token[1])
        if _overlaps(span, occupied):
            continue
        replacement = f"{cast_col(match.group('col'))} {match.group('op')} {num}"
        replacements.append((span[0], span[1], replacement))
        occupied.append(span)

    if not replacements:
        return text
    out = text
    for start, end, replacement in sorted(replacements, key=lambda item: item[0], reverse=True):
        out = out[:start] + replacement + out[end:]
    return out.strip()


_AI_SQL_COLUMN_ALIASES = {
    "product": ("product", "제품"),
    "lot_id": ("lot_id", "lot id", "랏"),
    "root_lot_id": ("root_lot_id", "root lot", "root_lot", "루트 랏", "루트랏"),
    "wafer_id": ("wafer_id", "wafer", "wf", "웨이퍼"),
    "step_id": ("step_id", "step id", "스텝"),
    "function_step": ("function_step", "function step", "func step"),
    "ppid": ("ppid",),
    "feature_name": ("feature_name", "feature name", "feature"),
    "knob_name": ("knob_name", "knob name"),
    "knob_value": ("knob_value", "knob value"),
    "category": ("category",),
    "item_id": ("item_id", "item id"),
    "item_desc": ("item_desc", "item desc", "description", "desc"),
    "subitem_id": ("subitem_id", "subitem id", "subitem"),
    "shot_x": ("shot_x", "shot x"),
    "shot_y": ("shot_y", "shot y"),
    "value": ("value", "값"),
    "rank": ("rank", "순위"),
    "lsl": ("lsl",),
    "usl": ("usl",),
    "tkout_time": ("tkout_time", "tkout time"),
    "update_time": ("update_time", "update time"),
    "measure_time": ("measure_time", "measure time", "측정 시간"),
}


def _all_ai_sql_alias_tokens() -> set[str]:
    out: set[str] = set()
    for aliases in _AI_SQL_COLUMN_ALIASES.values():
        for alias in aliases:
            text = str(alias).casefold()
            out.add(text)
            out.update(part for part in re.split(r"[^a-z0-9_]+", text) if part)
    return out


def _ai_sql_column_term(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _resolve_ai_sql_prompt_columns(prompt: str, columns: list[str]) -> tuple[list[str], list[str]]:
    if not columns:
        return [], []
    lookup = _column_lookup(columns)
    alias_lookup: dict[str, str] = {}
    for col in columns:
        canonical = lookup.get(str(col).casefold(), str(col))
        aliases = {str(col), str(col).replace("_", " ")}
        aliases.update(_AI_SQL_COLUMN_ALIASES.get(str(col).casefold(), ()))
        for alias in aliases:
            alias_text = str(alias or "").casefold()
            if not alias_text:
                continue
            alias_lookup[alias_text] = canonical
            alias_norm = _ai_sql_column_term(alias_text)
            if alias_norm:
                alias_lookup[alias_norm] = canonical
            for part in re.split(r"[^a-z0-9_]+", alias_text):
                if len(part) >= 3:
                    alias_lookup.setdefault(part, canonical)
    col_norms = [(_ai_sql_column_term(col), lookup.get(str(col).casefold(), str(col))) for col in columns]
    resolved: list[str] = []
    unknown: list[str] = []
    for token in _prompt_identifier_tokens(prompt):
        key = token.casefold()
        if key in _AI_SQL_IGNORE_TOKENS:
            continue
        norm = _ai_sql_column_term(token)
        hit = lookup.get(key) or alias_lookup.get(key) or alias_lookup.get(norm)
        if not hit and len(norm) >= 3:
            matches = [col for col_norm, col in col_norms if col_norm and (norm in col_norm or col_norm in norm)]
            unique = []
            for col in matches:
                if col not in unique:
                    unique.append(col)
            if len(unique) == 1:
                hit = unique[0]
        if hit:
            if hit not in resolved:
                resolved.append(hit)
            continue
        if "_" in token and token not in unknown:
            unknown.append(token)
    return resolved, unknown


def _alias_span(prompt: str, alias: str) -> tuple[int, int] | None:
    alias = str(alias or "")
    if not alias:
        return None
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_ ]*", alias):
        pattern = r"(?<![A-Za-z0-9_])" + re.escape(alias) + r"(?![A-Za-z0-9_])"
        match = re.search(pattern, prompt, flags=re.I)
        return match.span() if match else None
    idx = prompt.casefold().find(alias.casefold())
    return (idx, idx + len(alias)) if idx >= 0 else None


def _sql_literal_for_filter(value: str, columns: list[str]) -> str:
    text = str(value or "").strip().strip("'\"")
    if not text:
        return "''"
    if text.casefold() in {c.casefold() for c in columns}:
        return _column_lookup(columns).get(text.casefold(), text)
    if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return text
    return "'" + text.replace("'", "''") + "'"


def _ai_sql_time_from_suffix(text: str) -> tuple[int, int, int] | None:
    suffix = str(text or "")[:64]
    cut = re.search(r"(?:이후|이전|부터|까지|만|행|필터|그리고|또|,|;|\n)", suffix, flags=re.I)
    if cut:
        suffix = suffix[:cut.start()]
    meridiem_match = re.search(r"\b(AM|PM|A\.M\.|P\.M\.)\b|오전|오후", suffix, flags=re.I)
    meridiem = meridiem_match.group(0).casefold().replace(".", "") if meridiem_match else ""
    match = re.search(r"(?<!\d)(\d{1,2})\s*:\s*(\d{1,2})(?:\s*:\s*(\d{1,2}))?(?!\d)", suffix)
    if not match:
        match = re.search(r"(?<!\d)(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분?)?(?:\s*(\d{1,2})\s*초)?", suffix)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    second = int(match.group(3) or 0)
    if meridiem in {"pm", "오후"} and hour < 12:
        hour += 12
    if meridiem in {"am", "오전"} and hour == 12:
        hour = 0
    if hour > 23 or minute > 59 or second > 59:
        return None
    return hour, minute, second


def _ai_sql_datetime_value(year: int, month: int = 1, day: int = 1,
                           time_value: tuple[int, int, int] | None = None) -> str | None:
    try:
        if time_value:
            hour, minute, second = time_value
            return datetime.datetime(year, month, day, hour, minute, second).isoformat(timespec="seconds")
        return datetime.date(year, month, day).isoformat()
    except ValueError:
        return None


def _extract_ai_sql_datetime_values(text: str) -> list[str]:
    src = str(text or "")
    candidates: list[tuple[int, int, int, str]] = []

    def add(start: int, end: int, precision: int, value: str | None) -> None:
        if value:
            candidates.append((start, end, precision, value))

    for match in re.finditer(r"(?<!\d)((?:19|20|21)\d{2})\s*년\s*(?:(\d{1,2})\s*월\s*)?(?:(\d{1,2})\s*일)?", src):
        year = int(match.group(1))
        month = int(match.group(2) or 1)
        day = int(match.group(3) or 1)
        time_value = _ai_sql_time_from_suffix(src[match.end():]) if match.group(3) else None
        precision = 4 if time_value else (3 if match.group(3) else (2 if match.group(2) else 1))
        add(match.start(), match.end(), precision, _ai_sql_datetime_value(year, month, day, time_value))

    for match in re.finditer(r"(?<!\d)((?:19|20|21)\d{2})[-/.](\d{1,2})(?:[-/.](\d{1,2}))?(?!\d)", src):
        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3) or 1)
        time_value = _ai_sql_time_from_suffix(src[match.end():]) if match.group(3) else None
        precision = 4 if time_value else (3 if match.group(3) else 2)
        add(match.start(), match.end(), precision, _ai_sql_datetime_value(year, month, day, time_value))

    for match in re.finditer(r"(?<!\d)((?:19|20|21)\d{2})(\d{2})(\d{2})(?!\d)", src):
        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3))
        time_value = _ai_sql_time_from_suffix(src[match.end():])
        precision = 4 if time_value else 3
        add(match.start(), match.end(), precision, _ai_sql_datetime_value(year, month, day, time_value))

    for match in re.finditer(r"(?<![A-Za-z0-9_])((?:19|20|21)\d{2})(?:\s*년)?(?![A-Za-z0-9_])", src):
        year = int(match.group(1))
        add(match.start(), match.end(), 1, _ai_sql_datetime_value(year))

    selected: list[tuple[int, int, str]] = []
    for start, end, _precision, value in sorted(candidates, key=lambda item: (-item[2], item[0], item[1])):
        if any(not (end <= old_start or start >= old_end) for old_start, old_end, _old_value in selected):
            continue
        selected.append((start, end, value))
    out: list[str] = []
    for _start, _end, value in sorted(selected, key=lambda item: item[0]):
        if value not in out:
            out.append(value)
    return out


def _fallback_values(text: str, columns: list[str]) -> list[str]:
    blocked = {c.casefold() for c in columns}
    blocked.update(_AI_SQL_IGNORE_TOKENS)
    blocked.update(_all_ai_sql_alias_tokens())
    blocked.update({"and", "or", "null", "not", "like", "true", "false"})
    values: list[str] = []
    for raw in re.findall(r"'([^']+)'|\"([^\"]+)\"|(\d{4}-\d{2}-\d{2})|([A-Za-z][A-Za-z0-9_.-]*|-?\d+(?:\.\d+)?)", text):
        val = next((item for item in raw if item), "")
        if not val:
            continue
        if val.casefold() in blocked:
            continue
        if val not in values:
            values.append(val)
    return values[:4]


def _fallback_column_hits(prompt: str, columns: list[str]) -> list[str]:
    lookup = _column_lookup(columns)
    hits: list[str] = []
    for col in columns:
        canonical = lookup.get(str(col).casefold(), str(col))
        aliases = _AI_SQL_COLUMN_ALIASES.get(str(col).casefold(), (str(col),))
        matched = False
        for alias in aliases:
            span = _alias_span(prompt, alias)
            if span is None:
                continue
            if str(col).casefold() == "lot_id":
                prefix = prompt[max(0, span[0] - 8):span[0]].casefold().strip()
                if prefix.endswith("root") or prefix.endswith("루트"):
                    continue
            matched = True
            break
        if matched:
            if canonical not in hits:
                hits.append(canonical)
    return hits


def _fallback_window(prompt: str, column: str) -> str:
    aliases = _AI_SQL_COLUMN_ALIASES.get(str(column).casefold(), (str(column),))
    spans = [span for alias in aliases for span in [_alias_span(prompt, alias)] if span is not None]
    if not spans:
        return prompt
    start, end = min(spans, key=lambda item: item[0])
    tail = prompt[end:end + 120]
    cut = re.search(r"(?:이고|이면서|그리고|또|,|;|\n|\bAND\b|\bOR\b)", tail, flags=re.I)
    if cut:
        tail = tail[:cut.start()]
    return prompt[start:end] + tail


def _fallback_ai_sql(prompt: str, columns: list[str]) -> str:
    prompt = str(prompt or "").strip()
    if not prompt or not columns:
        return ""
    hits = _fallback_column_hits(prompt, columns)
    if not hits:
        return ""
    low = prompt.casefold()
    clauses: list[str] = []
    for col in hits:
        window = _fallback_window(prompt, col)
        wlow = window.casefold()
        date_values = _extract_ai_sql_datetime_values(window) if _looks_date_like_column(col) else []
        values = date_values or _fallback_values(window, columns)
        less_match = re.search(r"(-?\d+(?:\.\d+)?)\s*보다\s*(?:작|낮)", window)
        greater_match = re.search(r"(-?\d+(?:\.\d+)?)\s*보다\s*(?:큰|크|높)", window)
        le_match = re.search(r"(-?\d+(?:\.\d+)?)\s*이하", window)
        ge_match = re.search(r"(-?\d+(?:\.\d+)?)\s*이상", window)
        if less_match and greater_match:
            clauses.append(f"{col} < {_sql_literal_for_filter(less_match.group(1), columns)}")
            clauses.append(f"{col} > {_sql_literal_for_filter(greater_match.group(1), columns)}")
            continue
        if greater_match and le_match:
            clauses.append(f"{col} > {_sql_literal_for_filter(greater_match.group(1), columns)}")
            clauses.append(f"{col} <= {_sql_literal_for_filter(le_match.group(1), columns)}")
            continue
        if ge_match and le_match:
            clauses.append(f"{col} >= {_sql_literal_for_filter(ge_match.group(1), columns)}")
            clauses.append(f"{col} <= {_sql_literal_for_filter(le_match.group(1), columns)}")
            continue
        if "null이 아닌" in wlow or "비어있지" in wlow or "not null" in wlow:
            clauses.append(f"{col} IS NOT NULL")
            continue
        if "null" in wlow and ("아닌" in wlow or "not" in wlow):
            clauses.append(f"{col} IS NOT NULL")
            continue
        if col.casefold() == "value":
            other_cols = [c for c in ("lsl", "usl") if c in {x.casefold() for x in columns} and c in wlow]
            if other_cols and ("보다 작은" in wlow or "작은" in wlow or "less" in wlow):
                clauses.append(f"{col} < {_column_lookup(columns).get(other_cols[0], other_cols[0])}")
                continue
            if other_cols and ("보다 큰" in wlow or "큰" in wlow or "greater" in wlow):
                clauses.append(f"{col} > {_column_lookup(columns).get(other_cols[0], other_cols[0])}")
                continue
        if ("포함" in wlow or "들어가" in wlow or "contains" in wlow) and values:
            safe = values[0].replace("'", "''")
            clauses.append(f"{col} LIKE '%{safe}%'")
            continue
        if ("시작" in wlow or "starts" in wlow) and values:
            safe = values[0].replace("'", "''")
            clauses.append(f"{col} LIKE '{safe}%'")
            continue
        if ("또는" in wlow or " or " in wlow) and len(values) >= 2:
            vals = ", ".join(_sql_literal_for_filter(v, columns) for v in values[:4])
            clauses.append(f"{col} IN ({vals})")
            continue
        if ("이상" in wlow or ">=" in wlow) and values:
            clauses.append(f"{col} >= {_sql_literal_for_filter(values[0], columns)}")
            if ("이하" in wlow or "<=" in wlow) and len(values) >= 2:
                clauses.append(f"{col} <= {_sql_literal_for_filter(values[1], columns)}")
            continue
        if ("이하" in wlow or "<=" in wlow) and values:
            clauses.append(f"{col} <= {_sql_literal_for_filter(values[0], columns)}")
            continue
        if ("보다 큰" in wlow or "초과" in wlow or ">" in wlow or "greater" in wlow) and values:
            clauses.append(f"{col} > {_sql_literal_for_filter(values[0], columns)}")
            continue
        if ("보다 작은" in wlow or "미만" in wlow or "<" in wlow or "less" in wlow) and values:
            clauses.append(f"{col} < {_sql_literal_for_filter(values[0], columns)}")
            continue
        if ("이후" in wlow or "after" in wlow) and values:
            clauses.append(f"{col} >= {_sql_literal_for_filter(values[0], columns)}")
            continue
        if ("이전" in wlow or "before" in wlow) and values:
            clauses.append(f"{col} <= {_sql_literal_for_filter(values[0], columns)}")
            continue
        if ("아닌" in wlow or "!=" in wlow or "not " in wlow) and values:
            clauses.append(f"{col} != {_sql_literal_for_filter(values[0], columns)}")
            continue
        if values:
            clauses.append(f"{col} = {_sql_literal_for_filter(values[0], columns)}")
    unique: list[str] = []
    for clause in clauses:
        if clause not in unique:
            unique.append(clause)
    if not unique:
        return ""
    joiner = " OR " if " 또는 " in low and len(unique) <= 2 else " AND "
    return joiner.join(unique)


def _ai_sql_context_columns(columns: list[str], dtypes: dict | None, sample_rows: list[dict] | None) -> list[dict]:
    dtype_map = {str(k): str(v) for k, v in (dtypes or {}).items()} if isinstance(dtypes, dict) else {}
    samples: dict[str, list[str]] = {c: [] for c in columns}
    for row in (sample_rows or [])[:20]:
        if not isinstance(row, dict):
            continue
        for col in columns:
            if col not in row:
                continue
            text = _cache_safe_text(row.get(col), 80)
            if text and text not in samples[col]:
                samples[col].append(text)
    return [
        {"name": col, "dtype": dtype_map.get(col, ""), "sample_values": samples.get(col, [])[:5]}
        for col in columns[:200]
    ]


def _plan_value_terms(plan: dict, prompt: str, columns: list[str]) -> tuple[list[str], list[str]]:
    value_terms = _fallback_values(prompt, columns)
    for value in _extract_ai_sql_datetime_values(prompt):
        if value not in value_terms:
            value_terms.append(value)
    resolved: list[str] = []
    if isinstance(plan, dict):
        raw = plan.get("resolved_values") or plan.get("value_terms") or plan.get("values") or []
        if isinstance(raw, str):
            raw = [raw]
        if isinstance(raw, list):
            for item in raw:
                text = _cache_safe_text(item, 120)
                if text and text not in resolved:
                    resolved.append(text)
    return resolved[:20], value_terms[:20]


def _draft_filebrowser_ai_sql(*, natural_language: str, columns: list[str],
                              dtypes: dict | None = None,
                              sample_rows: list[dict] | None = None,
                              current_sql: str = "", scope: str = "",
                              root: str = "", product: str = "",
                              file: str = "") -> dict:
    prompt = _cache_safe_text(natural_language, 2000)
    if not prompt:
        raise HTTPException(400, "natural_language is required")
    columns = _settings_context_columns(columns)
    current_sql = _cache_safe_text(current_sql, 1000)
    context = {
        "scope": _cache_safe_text(scope, 80),
        "root": _cache_safe_text(root, 160),
        "product": _cache_safe_text(product, 160),
        "file": _cache_safe_text(file, 240),
    }
    column_context = _ai_sql_context_columns(columns, dtypes, sample_rows)
    warnings: list[str] = []
    resolved_columns, unknown_column_terms = _resolve_ai_sql_prompt_columns(prompt, columns)
    if unknown_column_terms:
        warnings.append("Unknown column-like terms: " + ", ".join(unknown_column_terms[:8]))
    llm_info = {"available": False, "used": False, "error": ""}
    raw_text = ""
    plan: dict = {}
    try:
        from core import llm_adapter
        llm_info["available"] = bool(llm_adapter.is_available())
        if llm_info["available"]:
            system = _filebrowser_agent_prompt("sql_draft.system", (
                "You write Flow FileBrowser SQL filter expressions. Return only JSON. "
                "The output must be a single read-only WHERE/filter expression in the sql field. "
                "Use only provided columns. Do not return SELECT, FROM, JOIN, ORDER BY, LIMIT, "
                "DDL, DML, comments, semicolons, markdown, or explanation. "
                "Prefer SQL syntax: =, !=, >, >=, <, <=, LIKE, NOT LIKE, IN (...), "
                "IS NULL, IS NOT NULL, AND, OR."
            ))
            ask = json.dumps({
                "natural_language": prompt,
                "current_sql": current_sql,
                "columns": columns[:200],
                "schema": column_context,
                "sample_rows": _safe_sample_rows(sample_rows or [], max_rows=5, max_cols=40, max_value_len=120),
                "context": context,
                "response_schema": {
                    "sql": "column = 'value' AND other_col > 0",
                    "resolved_columns": ["column"],
                    "resolved_values": ["value"],
                    "notes": "optional short note",
                },
            }, ensure_ascii=False)
            out = llm_adapter.complete(ask, system=system, timeout=20)
            raw_text = str(out.get("text") or "")
            llm_info["used"] = bool(out.get("ok") and raw_text.strip())
            if out.get("error"):
                llm_info["error"] = str(out.get("error") or "")
            if raw_text:
                plan = _cache_llm_json(raw_text)
        else:
            warnings.append("LLM is not configured.")
    except Exception as exc:
        llm_info["error"] = f"{type(exc).__name__}: {exc}"
    if llm_info.get("error"):
        warnings.append(f"LLM failed: {llm_info['error']}")
    raw_sql = _extract_llm_sql_text(raw_text, plan)
    resolved_values, value_terms = _plan_value_terms(plan, prompt, columns)
    try:
        sql, validate_warnings = _validate_ai_sql_filter(raw_sql, columns)
        warnings.extend(validate_warnings)
    except Exception as exc:
        fallback = _fallback_ai_sql(prompt, columns)
        if fallback:
            try:
                sql, validate_warnings = _validate_ai_sql_filter(fallback, columns)
                return {
                    "ok": True,
                    "saved": False,
                    "unit_action": "filebrowser.sql.llm.draft",
                    "sql": sql,
                    "warnings": [*warnings, f"LLM draft was not usable: {exc}", "deterministic fallback used"],
                    "columns": columns,
                    "resolved_columns": resolved_columns,
                    "unknown_column_terms": unknown_column_terms,
                    "resolved_values": resolved_values,
                    "value_terms": value_terms,
                    "llm": llm_info,
                    "fallback": True,
                }
            except Exception as fallback_exc:
                warnings.append(f"deterministic fallback failed: {fallback_exc}")
        return {
            "ok": False,
            "saved": False,
            "unit_action": "filebrowser.sql.llm.draft",
            "sql": "",
            "warnings": [*warnings, str(exc)],
            "columns": columns,
            "resolved_columns": resolved_columns,
            "unknown_column_terms": unknown_column_terms,
            "resolved_values": resolved_values,
            "value_terms": value_terms,
            "llm": llm_info,
        }
    return {
        "ok": True,
        "saved": False,
        "unit_action": "filebrowser.sql.llm.draft",
        "sql": sql,
        "warnings": warnings,
        "columns": columns,
        "resolved_columns": resolved_columns,
        "unknown_column_terms": unknown_column_terms,
        "resolved_values": resolved_values,
        "value_terms": value_terms,
        "llm": llm_info,
        "fallback": False,
    }


def _run_view(df, sql: str, select_cols: str, rows: int,
              page: int = 0, page_size: int | None = None, preview_cols: int | None = None,
              latest_first: bool = False, latest_preview: bool = False):
    """Apply select + sql + head; return standard response dict. Legacy DataFrame path."""
    all_columns = list(df.columns)
    schema = {n: str(d) for n, d in df.schema.items()}
    df, wafer_filtered = _filter_valid_wafers_df(df)
    total = df.height
    page_size = int(page_size or rows or 200)
    page, page_size, offset = _page_args(page, page_size)

    sel, truncated_cols = _selected_columns(all_columns, select_cols, preview_cols)
    if sql and sql.strip():
        df = apply_sql_like(df, _normalize_wafer_sql_filter(sql, all_columns))
        total = df.height
    latest_order_col = _latest_order_column(all_columns) if latest_first else ""
    if latest_order_col and latest_order_col in df.columns:
        df = df.sort(
            pl.col(latest_order_col).cast(_SORT_STR, strict=False),
            descending=True,
            nulls_last=True,
        )
    if sel:
        df = df.select(sel)
    show = df.slice(offset, page_size)
    return {
        "total_rows": total, "total_cols": len(all_columns),
        "columns": list(show.columns), "all_columns": all_columns,
        "dtypes": schema, "showing_cols": list(show.columns),
        "selected_cols": select_cols.strip() or None,
        "data": serialize_rows(show.to_dicts()), "showing": len(show),
        "page": page, "page_size": page_size,
        "has_more": offset + len(show) < total,
        "preview_cols": len(show.columns),
        "truncated_cols": truncated_cols,
        "latest_order_col": latest_order_col or None,
        "latest_preview": bool(latest_preview),
        "wafer_filter": {"max": MAX_WAFER_ID} if wafer_filtered else None,
    }


def _run_view_duckdb(files: list[Path], sql: str, select_cols: str, rows: int,
                     page: int = 0, page_size: int | None = None,
                     preview_cols: int | None = None,
                     latest_first: bool = False, latest_preview: bool = False,
                     cached_meta: dict | None = None):
    """Apply the same preview contract through DuckDB for large read-only sources."""
    all_columns, schema = duckdb_engine.inspect_files(files)
    page_size = int(page_size or rows or 200)
    page, page_size, offset = _page_args(page, page_size)
    sel, truncated_cols = _selected_columns(all_columns, select_cols, preview_cols)
    latest_order_col = _latest_order_column(all_columns) if latest_first else ""
    wafer_where = _duckdb_valid_wafer_where(all_columns)
    user_where = _normalize_wafer_sql_filter(sql, all_columns)
    show_plus, _all_cols, _schema = duckdb_engine.query_files(
        files,
        where=_combine_where(user_where, wafer_where),
        select_cols=sel,
        limit=page_size + 1,
        offset=offset,
        order_by=latest_order_col,
        descending=bool(latest_order_col),
    )
    has_more = show_plus.height > page_size
    show = show_plus.head(page_size) if has_more else show_plus
    total = offset + show.height + (1 if has_more else 0)
    return {
        "total_rows": total,
        "total_cols": len(all_columns),
        "columns": list(show.columns),
        "all_columns": all_columns,
        "dtypes": schema,
        "showing_cols": list(show.columns),
        "selected_cols": select_cols.strip() or None,
        "data": serialize_rows(show.to_dicts()),
        "showing": len(show),
        "page": page,
        "page_size": page_size,
        "has_more": has_more,
        "preview_cols": len(show.columns),
        "truncated_cols": truncated_cols,
        "latest_order_col": latest_order_col or None,
        "latest_preview": bool(latest_preview),
        "engine": "duckdb",
        "source_file_count": len(files),
        "source_size": duckdb_engine.total_size(files),
        "total_rows_exact": False,
        "meta_cached": bool(cached_meta),
        "wafer_filter": {"max": MAX_WAFER_ID} if wafer_where else None,
    }


def _run_view_lazy(lf, sql: str, select_cols: str, rows: int, meta_only: bool = False,
                   page: int = 0, page_size: int | None = None, cached_meta: dict | None = None,
                   preview_cols: int | None = None, latest_first: bool = False,
                   latest_preview: bool = False,
                   allow_eager_sql_fallback: bool = False):
    """v8.4.3 OOM-aware: lazy 스캔 + projection pushdown + head + (필요 시) SQL.

    - 컬럼 선택 / head 은 lazy 에서 처리 → parquet reader 에서 필요한 컬럼·행만 읽음
    - SQL 필터도 lazy filter 로 밀어 넣고 첫 페이지 + 1행만 collect
    - 초기 미리보기(SQL/select 없음) 는 page 단위 slice 로 10GB 파일도 필요한 행만 로드
    - v8.8.16: meta_only=True 는 컬럼 스키마만 반환 (collect 없음) → 클릭 즉시 반응.
              실제 행 조회는 SQL 실행 / 컬럼 선택 적용 시점으로 이연.
    """
    schema_obj = lf.collect_schema()
    all_columns = list(schema_obj.names())
    schema = {n: str(schema_obj[n]) for n in all_columns}
    preview_cols = _preview_cols_limit(preview_cols)
    page_size = int(page_size or rows or 200)
    page, page_size, offset = _page_args(page, page_size)
    latest_order_col = _latest_order_column(all_columns) if latest_first else ""
    lf, wafer_filtered = _filter_valid_wafers_lazy(lf, all_columns)

    if meta_only:
        # 스키마만 — 어떤 collect() 도 하지 않음. 큰 parquet/CSV 도 수 ms.
        total_rows = int((cached_meta or {}).get("row_count") or 0)
        return {
            "total_rows": total_rows, "total_cols": len(all_columns),
            "columns": all_columns[:preview_cols], "all_columns": all_columns,
            "dtypes": schema, "showing_cols": [],
            "selected_cols": select_cols.strip() or None,
            "data": [], "showing": 0, "meta_only": True,
            "page": page, "page_size": page_size, "has_more": False,
            "meta_cached": bool(cached_meta),
            "total_rows_exact": bool(cached_meta) and not wafer_filtered,
            "preview_cols": min(len(all_columns), preview_cols),
            "truncated_cols": len(all_columns) > preview_cols,
            "latest_order_col": latest_order_col or None,
            "wafer_filter": {"max": MAX_WAFER_ID} if wafer_filtered else None,
        }

    # Keep SQL filtering on the full source schema.  Projection is applied only
    # after the filter, so users can filter by a column that is not selected for
    # display/download.
    sel, truncated_cols = _selected_columns(all_columns, select_cols, preview_cols)

    if sql and sql.strip():
        # Keep SQL lazy. Exact counts and eager fallback are intentionally
        # avoided on production-size parquet because they double-scan or OOM.
        try:
            from core.parquet_perf import collect_streaming
            filtered = lf.filter(_lazy_filter_expr(sql, all_columns))
            if latest_order_col:
                filtered = filtered.sort(
                    pl.col(latest_order_col).cast(_SORT_STR, strict=False),
                    descending=True,
                    nulls_last=True,
                )
            show_lf = filtered.select(sel) if sel else filtered
            show_plus = collect_streaming(show_lf.slice(offset, page_size + 1))
            has_more = show_plus.height > page_size
            show = show_plus.head(page_size) if has_more else show_plus
            total = offset + show.height + (1 if has_more else 0)
            total_exact = False
        except Exception:
            if not allow_eager_sql_fallback:
                raise
            try:
                from core.parquet_perf import collect_streaming
                df = collect_streaming(lf)
            except Exception:
                df = lf.collect()
            df = apply_sql_like(df, _normalize_wafer_sql_filter(sql, all_columns))
            total = df.height
            if latest_order_col and latest_order_col in df.columns:
                df = df.sort(
                    pl.col(latest_order_col).cast(_SORT_STR, strict=False),
                    descending=True,
                    nulls_last=True,
                )
            if sel:
                df = df.select(sel)
            show = df.slice(offset, page_size)
            has_more = offset + len(show) < total
            total_exact = True
    else:
        # Page path: parquet scan + lazy slice → only fetches the rows we need.
        if latest_order_col:
            lf = lf.sort(
                pl.col(latest_order_col).cast(_SORT_STR, strict=False),
                descending=True,
                nulls_last=True,
            )
        if sel:
            lf = lf.select(sel)
        try:
            from core.parquet_perf import collect_streaming
            show_plus = collect_streaming(lf.slice(offset, page_size + 1 if wafer_filtered else page_size))
        except Exception:
            show_plus = lf.slice(offset, page_size + 1 if wafer_filtered else page_size).collect()
        if wafer_filtered:
            has_more = show_plus.height > page_size
            show = show_plus.head(page_size) if has_more else show_plus
            total = offset + show.height + (1 if has_more else 0)
            total_exact = False
        else:
            show = show_plus
            total = int((cached_meta or {}).get("row_count") or 0) or (offset + show.height)
            has_more = show.height == page_size if not cached_meta else offset + show.height < total
            total_exact = bool(cached_meta)

    return {
        "total_rows": total, "total_cols": len(all_columns),
        "columns": list(show.columns), "all_columns": all_columns,
        "dtypes": schema, "showing_cols": list(show.columns),
        "selected_cols": select_cols.strip() or None,
        "data": serialize_rows(show.to_dicts()), "showing": len(show),
        "page": page, "page_size": page_size, "has_more": has_more,
        "meta_cached": bool(cached_meta),
        "total_rows_exact": total_exact,
        "preview_cols": len(show.columns),
        "truncated_cols": truncated_cols,
        "latest_order_col": latest_order_col or None,
        "latest_preview": bool(latest_preview),
        "wafer_filter": {"max": MAX_WAFER_ID} if wafer_filtered else None,
    }


def _run_view_lazy_full(lf, sql: str, select_cols: str, preview_cols: int | None = None,
                        latest_first: bool = False):
    """Collect a single lightweight file fully after optional SQL/projection."""
    schema_obj = lf.collect_schema()
    all_columns = list(schema_obj.names())
    schema = {n: str(schema_obj[n]) for n in all_columns}
    latest_order_col = _latest_order_column(all_columns) if latest_first else ""
    lf, wafer_filtered = _filter_valid_wafers_lazy(lf, all_columns)

    if sql and sql.strip():
        lf = lf.filter(_lazy_filter_expr(sql, all_columns))
    if latest_order_col:
        lf = lf.sort(
            pl.col(latest_order_col).cast(_SORT_STR, strict=False),
            descending=True,
            nulls_last=True,
        )
    if preview_cols is None:
        sel, truncated_cols = _selected_columns(all_columns, select_cols, len(all_columns) or 1)
    else:
        sel, truncated_cols = _selected_columns(all_columns, select_cols, preview_cols)
    if sel:
        lf = lf.select(sel)
    try:
        from core.parquet_perf import collect_streaming
        show = collect_streaming(lf)
    except Exception:
        show = lf.collect()
    return {
        "total_rows": show.height, "total_cols": len(all_columns),
        "columns": list(show.columns), "all_columns": all_columns,
        "dtypes": schema, "showing_cols": list(show.columns),
        "selected_cols": select_cols.strip() or None,
        "data": serialize_rows(show.to_dicts()), "showing": show.height,
        "page": 0, "page_size": show.height, "has_more": False,
        "meta_cached": False,
        "total_rows_exact": True,
        "preview_cols": len(show.columns),
        "truncated_cols": truncated_cols,
        "latest_order_col": latest_order_col or None,
        "latest_preview": False,
        "single_file_full_read": True,
        "wafer_filter": {"max": MAX_WAFER_ID} if wafer_filtered else None,
    }


def _csv_download_max_rows(raw: int | None = None) -> int:
    try:
        return max(1, min(MAX_CSV_DOWNLOAD_MAX_ROWS, int(raw or DEFAULT_CSV_DOWNLOAD_MAX_ROWS)))
    except Exception:
        return DEFAULT_CSV_DOWNLOAD_MAX_ROWS


def _download_lazy_csv(lf: pl.LazyFrame, sql: str, select_cols: str, max_rows: int) -> tuple[pl.DataFrame, bytes]:
    schema_obj = lf.collect_schema()
    all_columns = list(schema_obj.names())
    lf, _wafer_filtered = _filter_valid_wafers_lazy(lf, all_columns)
    requested = [c.strip() for c in str(select_cols or "").split(",") if c.strip()]
    selected = [c for c in requested if c in set(all_columns)]
    if not selected and len(all_columns) > MAX_CSV_DOWNLOAD_AUTO_COLUMNS:
        raise HTTPException(
            400,
            f"CSV 대상이 {len(all_columns)}열입니다. 컬럼 탭에서 필요한 열을 선택한 뒤 다운로드하세요.",
        )
    if sql and sql.strip():
        try:
            lf = lf.filter(_lazy_filter_expr(sql, all_columns))
        except Exception as e:
            raise HTTPException(400, f"CSV download SQL error: {e}")
    if selected:
        lf = lf.select(selected)
    try:
        from core.parquet_perf import collect_streaming
        df = collect_streaming(lf.head(max_rows + 1))
    except Exception:
        df = lf.head(max_rows + 1).collect()
    if df.height > max_rows:
        raise HTTPException(
            400,
            f"CSV 다운로드는 최대 {max_rows:,}행까지 허용됩니다. SQL 필터를 추가하거나 max_rows를 조정하세요.",
        )
    csv_bytes = df.write_csv().encode("utf-8")
    if len(csv_bytes) > MAX_CSV_DOWNLOAD_BYTES:
        raise HTTPException(400, "CSV too large (>100MB). 컬럼/SQL 필터를 줄여주세요.")
    return df, csv_bytes


def _is_dtype_mismatch_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".casefold()
    return any(token in text for token in (
        "data type mismatch",
        "dtype mismatch",
        "schema mismatch",
        "schemaerror",
    ))


def _download_duckdb_csv(files: list[Path], sql: str, select_cols: str, max_rows: int) -> tuple[pl.DataFrame, bytes]:
    if not files:
        raise ValueError("no source files for DuckDB download")
    all_columns, _schema = duckdb_engine.inspect_files(files)
    requested = [c.strip() for c in str(select_cols or "").split(",") if c.strip()]
    selected = [c for c in requested if c in set(all_columns)]
    if not selected and len(all_columns) > MAX_CSV_DOWNLOAD_AUTO_COLUMNS:
        raise HTTPException(
            400,
            f"CSV 대상이 {len(all_columns)}열입니다. 컬럼 탭에서 필요한 열을 선택한 뒤 다운로드하세요.",
        )
    where = _combine_where(
        _normalize_wafer_sql_filter(sql, all_columns),
        _duckdb_valid_wafer_where(all_columns),
    )
    df, _columns, _schema = duckdb_engine.query_files(
        files,
        where=where,
        select_cols=selected,
        limit=max_rows + 1,
    )
    if df.height > max_rows:
        raise HTTPException(
            400,
            f"CSV 다운로드는 최대 {max_rows:,}행까지 허용됩니다. SQL 필터를 추가하거나 max_rows를 조정하세요.",
        )
    csv_bytes = df.write_csv().encode("utf-8")
    if len(csv_bytes) > MAX_CSV_DOWNLOAD_BYTES:
        raise HTTPException(400, "CSV too large (>100MB). 컬럼/SQL 필터를 줄여주세요.")
    return df, csv_bytes


@router.get("/view")
def view_product(root: str = Query(...), product: str = Query(...),
                 sql: str = Query(""), rows: int = Query(200),
                 cols: int = Query(20, ge=1, le=200),
                 select_cols: str = Query(""),
                 meta_only: bool = Query(False),
                 all_partitions: bool = Query(False),
                 engine: str = Query("auto"),
                 page: int = Query(0, ge=0),
                 page_size: int = Query(200, ge=1, le=1000)):
    # v8.4.3 OOM-aware: Hive-flat 도 lazy_read_source 로 scan. Polars 가 projection +
    # head 를 parquet reader 로 pushdown → 메모리 수 GB 제품도 안전.
    # v8.8.16: meta_only=True 는 스키마만 — 사이드바 제품 클릭 즉시 반응.
    # v8.8.33: SQL 에 date 필터가 있거나 all_partitions=True 면 파티션 pruning 생략.
    #          그 외에는 최근 30일 파티션만 스캔 → 30~60GB 대응.
    try:
        from core.utils import lazy_read_source
        from core.parquet_perf import has_date_filter
        page, page_size, _offset = _preview_page_args(rows, page_size)
        rows = page_size
        if meta_only:
            fast_meta = _fast_product_meta_response(root, product, cols, page=page, page_size=page_size)
            if fast_meta is not None:
                return fast_meta
        # SQL 검색/컬럼 SELECT 는 사용자가 명시적으로 DB 를 조회하는 동작이다.
        # 제품 클릭 기본 화면은 전체 스캔 대신 최신 파티션/파일에서 200행만 보여준다.
        full_scan = (
            all_partitions
            or bool(sql and sql.strip())
            or bool(select_cols and select_cols.strip())
            or has_date_filter(sql)
        )
        recent = None if full_scan else 30
        latest_preview = not full_scan and not meta_only
        if latest_preview:
            rows = min(int(rows or LATEST_PREVIEW_ROWS), LATEST_PREVIEW_ROWS)
            page_size = min(int(page_size or LATEST_PREVIEW_ROWS), LATEST_PREVIEW_ROWS)
        if full_scan and not meta_only and duckdb_engine.is_available() and "INLINE" not in str(root or "").upper():
            files = source_data_files(root=root, product=product)
            if duckdb_engine.should_use_duckdb(files, engine=engine, sql=sql, select_cols=select_cols):
                try:
                    return _mark_preview_capped(_run_view_duckdb(
                        files, sql, select_cols, rows,
                        page=page, page_size=page_size, preview_cols=cols,
                        latest_first=False, latest_preview=False,
                    ))
                except Exception as e:
                    if str(engine or "").lower() in {"duckdb", "on", "true", "1"}:
                        raise HTTPException(400, f"DuckDB query failed: {e}")
                    logger.warning("duckdb product view fallback root=%s product=%s: %s", root, product, e)
        lf = lazy_read_source(
            root=root, product=product,
            recent_days=recent, max_files=None if full_scan else LATEST_PREVIEW_MAX_FILES,
            latest_only=latest_preview,
        )
        if lf is not None:
            return _mark_preview_capped(_run_view_lazy(lf, sql, select_cols, rows, meta_only=meta_only,
                                                       page=page, page_size=page_size, preview_cols=cols,
                                                       latest_first=latest_preview, latest_preview=latest_preview))
        # Fallback — legacy DF 경로
        df = read_source(root=root, product=product)
        if meta_only:
            cols_all = list(df.columns)
            return {
                "total_rows": 0, "total_cols": len(cols_all),
                "columns": cols_all[:10], "all_columns": cols_all,
                "dtypes": {n: str(d) for n, d in df.schema.items()},
                "showing_cols": [], "selected_cols": None,
                "data": [], "showing": 0, "meta_only": True,
                "page": page, "page_size": page_size, "has_more": False,
            }
        return _mark_preview_capped(_run_view(df, sql, select_cols, rows, page=page, page_size=page_size,
                                              preview_cols=cols, latest_first=latest_preview, latest_preview=latest_preview))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"view {root}/{product}: {e}", exc_info=True)
        raise HTTPException(400, f"Error: {str(e)}")


@router.get("/root-parquets")
def root_parquets():
    """List root-level data files.
    v8.7.6 정책 변경: DB 루트의 단일 parquet 도 Base 로 분류 권장. 이 엔드포인트는
    하위호환용으로만 유지하며 빈 배열을 반환해 UI 에서 별도 섹션이 사라지도록 한다.
    (/api/filebrowser/base-files 가 db_root 의 단일 parquet 을 통합 노출한다.)"""
    return {"files": []}


@router.get("/parquet-meta")
def parquet_meta(request: Request, root: str = Query(""), product: str = Query(""),
                 file: str = Query("")):
    """v8.8.33: parquet 파일의 row_count / schema 를 즉답.
    .meta.json 사이드카 캐시가 있으면 scan 없이 반환, 없으면 1회 계산 후 기록.
    30~60GB 스케일에서 FileBrowser 클릭 반응성을 위해 스키마-최초 호출에 사용.
    v8.8.33 보안: 세션 토큰 필수. file 파라미터는 디렉터리 traversal 방어.
    """
    from core.auth import current_user
    from core.parquet_perf import get_or_compute_meta
    _ = current_user(request)
    # file 파라미터 사전 정규화 — ".." 제거
    if file:
        from pathlib import Path as _P
        safe_parts = [p for p in _P(file).parts if p not in ("..", ".")]
        file = str(_P(*safe_parts)) if safe_parts else ""
    db_root = _db_root()
    base_root = _base_root()
    if file and not product:
        # DB 루트 단일 파일 또는 Base 파일
        candidates = [db_root / file, base_root / file]
    elif root and product:
        prod_path = db_root / root / product
        if not prod_path.is_dir():
            raise HTTPException(404, f"Not found: {root}/{product}")
        pq_files = sorted(prod_path.rglob("*.parquet"))
        if not pq_files:
            raise HTTPException(404, "No parquet files")
        # 디렉토리 기반 — 대표 파일(가장 최근)의 meta + 파일 수 요약
        rep = pq_files[-1]
        meta = get_or_compute_meta(rep)
        total = 0
        files_meta = []
        for f in pq_files[-30:]:  # 최근 30개 파일만 샘플링
            m = get_or_compute_meta(f)
            files_meta.append({"name": f.name, "rows": m.get("row_count", 0),
                               "size_bytes": m.get("size_bytes")})
            total += int(m.get("row_count") or 0)
        return {
            "schema": meta.get("schema"),
            "rep_file": rep.name,
            "files_sampled": len(files_meta),
            "files_meta": files_meta,
            "total_rows_sampled": total,
            "total_files": len(pq_files),
        }
    else:
        raise HTTPException(400, "specify (root,product) or file")

    for fp in candidates:
        try:
            fp_resolved = fp.resolve()
            if fp_resolved.is_file() and fp_resolved.suffix == ".parquet":
                return get_or_compute_meta(fp_resolved)
        except Exception:
            continue
    raise HTTPException(404, f"parquet not found: {file}")


@router.post("/parquet-meta/invalidate")
def parquet_meta_invalidate(request: Request, root: str = Query(""), product: str = Query(""),
                            file: str = Query("")):
    """v8.8.33: meta 사이드카 강제 재계산. admin 전용."""
    from core.auth import current_user
    from core.parquet_perf import invalidate_meta
    me = current_user(request)
    if me.get("role") != "admin":
        raise HTTPException(403, "admin only")
    db_root = _db_root()
    count = 0
    if file and not product:
        fp = (db_root / file).resolve()
        if fp.is_file():
            if invalidate_meta(fp):
                count += 1
    elif root and product:
        prod_path = db_root / root / product
        if prod_path.is_dir():
            for f in prod_path.rglob("*.parquet"):
                if invalidate_meta(f):
                    count += 1
    return {"invalidated": count}


@router.get("/root-parquet-view")
def view_root_parquet(file: str = Query(...), sql: str = Query(""),
                      rows: int = Query(200), cols: int = Query(10),
                      select_cols: str = Query(""),
                      meta_only: bool = Query(False),
                      engine: str = Query("auto"),
                      page: int = Query(0, ge=0),
                      page_size: int = Query(200, ge=1, le=1000)):
    # v8.4.6: path traversal 방어 — db_root 밖 파일 접근 차단
    db_root = _db_root()
    fp = (db_root / file).resolve()
    try:
        fp.relative_to(db_root.resolve())
    except ValueError:
        raise HTTPException(400, "Path escapes DB root")
    if not fp.is_file():
        raise HTTPException(404)
    try:
        page, page_size, _offset = _preview_page_args(rows, page_size)
        rows = page_size
        # v8.4.3 OOM-aware: lazy scan — full read 회피. 10GB+ parquet 도 안전.
        lf = scan_one_file(fp)
        if lf is None:
            raise HTTPException(400, f"Cannot read: {file}")
        full_schema_obj = lf.collect_schema()
        all_cols_full = list(full_schema_obj.names())
        schema_full = {n: str(full_schema_obj[n]) for n in all_cols_full}
        # v8.8.16: meta_only 빠른 경로.
        if meta_only:
            try:
                from core.parquet_perf import read_meta
                cached_meta = read_meta(fp)
            except Exception:
                cached_meta = None
            return {
                "all_columns": all_cols_full, "total_cols": len(all_cols_full),
                "columns": all_cols_full[:cols], "dtypes": schema_full,
                "data": [], "showing": 0, "showing_cols": [],
                "total_rows": int((cached_meta or {}).get("row_count") or 0),
                "meta_only": True,
                "page": page, "page_size": page_size, "has_more": False,
                "meta_cached": bool(cached_meta),
            }
        try:
            from core.parquet_perf import read_meta
            cached_meta = read_meta(fp)
        except Exception:
            cached_meta = None
        if duckdb_engine.should_use_duckdb([fp], engine=engine, sql=sql, select_cols=select_cols):
            try:
                return _mark_preview_capped(_run_view_duckdb(
                    [fp], sql, select_cols, rows,
                    page=page, page_size=page_size, cached_meta=cached_meta,
                    preview_cols=cols,
                ))
            except Exception as e:
                if str(engine or "").lower() in {"duckdb", "on", "true", "1"}:
                    raise HTTPException(400, f"DuckDB query failed: {e}")
                logger.warning("duckdb root-parquet-view fallback file=%s: %s", file, e)
        resp = _run_view_lazy(
            lf, sql, select_cols, rows,
            page=page, page_size=page_size, cached_meta=cached_meta,
            preview_cols=cols,
        )
        resp["all_columns"] = all_cols_full
        resp["total_cols"] = len(all_cols_full)
        resp["dtypes"] = schema_full
        return _mark_preview_capped(resp)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Error: {str(e)}")


@router.get("/download-csv")
def download_csv(request: Request, root: str = Query(""), product: str = Query(""),
                 file: str = Query(""), sql: str = Query(""),
                 select_cols: str = Query(""), username: str = Query(""),
                 apply_reformatter: bool = Query(True),
                 max_rows: int = Query(DEFAULT_CSV_DOWNLOAD_MAX_ROWS, ge=1, le=MAX_CSV_DOWNLOAD_MAX_ROWS)):
    """v7.2: If apply_reformatter=True and a per-product rules file exists,
    derived indices (VTH_IDX, CD_RANGE, poly2 window width, etc.) are appended
    to the download — matching what engineers actually need, not raw VALUE.
    v8.8.33 보안: 세션 토큰 필수 + username 서버 세션 기준 강제 (spoof 방지)."""
    from core.auth import current_user
    me = current_user(request)
    username = me.get("username") or "anonymous"
    try:
        max_rows = _csv_download_max_rows(max_rows)
        lazy_lf = None
        source_files: list[Path] = []
        if file:
            rel = Path(file)
            if rel.parts and rel.parts[0] == "reformatter":
                suffix = Path(rel.parts[1]).suffix.lower() if len(rel.parts) == 2 else ""
                if len(rel.parts) != 2 or rel.parts[1].startswith(".") or suffix not in (".csv", ".json"):
                    raise HTTPException(400, "Invalid reformatter path")
                product_name = Path(rel.parts[1]).stem
                rf_root = (PATHS.data_root / "reformatter").resolve()
                csv_fp = (rf_root / f"{product_name}.csv").resolve()
                json_fp = (rf_root / f"{product_name}.json").resolve()
                try:
                    (csv_fp if csv_fp.is_file() else json_fp).relative_to(rf_root)
                except ValueError:
                    raise HTTPException(400, "Invalid reformatter path")
                if csv_fp.is_file():
                    df = read_one_file(csv_fp)
                    if df is None:
                        raise HTTPException(400, f"Cannot read: {file}")
                elif json_fp.is_file():
                    from core.reformatter import REFORMATTER_TABLE_COLUMNS, load_rules, rules_to_reformatter_table
                    rows = rules_to_reformatter_table(load_rules(rf_root, product_name))
                    df = pl.DataFrame(rows) if rows else pl.DataFrame({c: [] for c in REFORMATTER_TABLE_COLUMNS})
                    for c in REFORMATTER_TABLE_COLUMNS:
                        if c not in df.columns:
                            df = df.with_columns(pl.lit("").alias(c))
                    df = df.select(REFORMATTER_TABLE_COLUMNS)
                else:
                    raise HTTPException(404, f"Reformatter not found: {file}")
                label = f"reformatter/{product_name}.csv"
            else:
                # v8.4.6: traversal 방어. Base Files can originate from base_root
                # or db_root, so resolve against both but never outside either root.
                fp = None
                for candidate_root in (_base_root(), _db_root()):
                    if not candidate_root.is_dir():
                        continue
                    cand = (candidate_root / file).resolve()
                    try:
                        cand.relative_to(candidate_root.resolve())
                    except ValueError:
                        continue
                    if cand.is_file() and cand.suffix.lower() in DATA_EXTENSIONS:
                        fp = cand
                        break
                if fp is None:
                    raise HTTPException(404)
                source_files = [fp]
                lazy_lf = scan_one_file(fp)
                if lazy_lf is None:
                    raise HTTPException(400, f"Cannot read: {file}")
                label = file
        elif root and product:
            label = f"{root}/{product}"
            reformatter_rules = []
            if apply_reformatter and product:
                try:
                    from core.reformatter import load_rules
                    reformatter_rules = load_rules(PATHS.data_root / "reformatter", product)
                except Exception:
                    reformatter_rules = []
            if reformatter_rules:
                df = read_source(root=root, product=product, max_files=None if sql.strip() else 40)
            else:
                candidate_files = source_data_files(
                    root=root,
                    product=product,
                    max_files=None if sql.strip() else 40,
                )
                parquet_files = [fp for fp in candidate_files if fp.suffix.lower() == ".parquet"]
                csv_files = [fp for fp in candidate_files if fp.suffix.lower() == ".csv"]
                source_files = parquet_files or csv_files
                lazy_lf = lazy_read_source(
                    root=root,
                    product=product,
                    max_files=None if sql.strip() else 40,
                    recent_days=None if sql.strip() else 30,
                )
                if lazy_lf is None:
                    df = read_source(root=root, product=product, max_files=None if sql.strip() else 40)
        else:
            raise HTTPException(400, "Specify file or root+product")

        if lazy_lf is not None:
            try:
                df, csv_bytes = _download_lazy_csv(lazy_lf, sql, select_cols, max_rows)
            except HTTPException:
                raise
            except Exception as e:
                if not _is_dtype_mismatch_error(e) or not source_files or not duckdb_engine.is_available():
                    raise
                logger.warning("polars download fallback to duckdb label=%s: %s", label, e)
                df, csv_bytes = _download_duckdb_csv(source_files, sql, select_cols, max_rows)
            _log_dl(username, label, sql, df.height, df.width,
                    select_cols=select_cols, size_bytes=len(csv_bytes))
            return csv_response(csv_bytes, label)

        # v7.2: Apply reformatter rules BEFORE select/sql so derived cols can be selected/filtered.
        # This dataframe path is retained for reformatter-derived columns and small config files.
        rf_applied = []
        if apply_reformatter and product:
            try:
                from core.reformatter import load_rules, apply_rules
                BASE = PATHS.data_root / "reformatter"
                rules = load_rules(BASE, product)
                if rules:
                    orig = set(df.columns)
                    df = apply_rules(df, rules, enabled_only=True)
                    rf_applied = [c for c in df.columns if c not in orig]
                    logger.info(f"Reformatter applied {len(rules)} rules → {len(rf_applied)} derived cols")
            except Exception as e:
                logger.warning(f"Reformatter skipped: {e}")

        df, _wafer_filtered = _filter_valid_wafers_df(df)
        if sql.strip():
            df = apply_sql_like(df, _normalize_wafer_sql_filter(sql, list(df.columns)))
        if select_cols.strip():
            sel = [c.strip() for c in select_cols.split(",") if c.strip() in set(df.columns)]
            if sel:
                df = df.select(sel)
        if df.height > max_rows:
            raise HTTPException(
                400,
                f"CSV 다운로드는 최대 {max_rows:,}행까지 허용됩니다. SQL 필터를 추가하거나 max_rows를 조정하세요.",
            )
        if not select_cols.strip() and df.width > MAX_CSV_DOWNLOAD_AUTO_COLUMNS:
            raise HTTPException(
                400,
                f"CSV 대상이 {df.width}열입니다. 컬럼 탭에서 필요한 열을 선택한 뒤 다운로드하세요.",
            )

        csv_bytes = df.write_csv().encode("utf-8")
        if len(csv_bytes) > MAX_CSV_DOWNLOAD_BYTES:
            raise HTTPException(400, "CSV too large (>100MB). 컬럼/SQL 필터를 줄여주세요.")
        _log_dl(username, label, sql, df.height, df.width,
                select_cols=select_cols, size_bytes=len(csv_bytes))
        return csv_response(csv_bytes, label)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Download failed: {str(e)}")


@router.get("/download-history")
def download_history(request: Request, username: str = Query(""), limit: int = Query(100)):
    """v8.8.33 보안: admin 이면 전체, 일반 유저는 본인만."""
    from core.auth import current_user
    me = current_user(request)
    if me.get("role") != "admin":
        username = me.get("username") or ""
    f = (lambda e: e.get("username") == username) if username else None
    return {"logs": jsonl_read(DL_LOG, limit, f)}


class BaseDeleteReq(BaseModel):
    file: str
    username: str = ""


class BaseFileSaveReq(BaseModel):
    file: str
    mode: str = "replace"
    csv_text: str = ""
    delimiter: str = "auto"
    include_header: bool = True
    note: str = ""


class FileBrowserSettingsReq(BaseModel):
    csv_full_read_max_bytes: int = DEFAULT_CSV_FULL_READ_MAX_BYTES
    csv_download_max_rows: int = DEFAULT_FILEBROWSER_CSV_DOWNLOAD_ROWS
    csv_rules: dict = {}
    hidden_db_dirs: list[str] = DEFAULT_FILEBROWSER_SETTINGS["hidden_db_dirs"]
    versioned_single_file_dirs: list[str] = DEFAULT_FILEBROWSER_SETTINGS["versioned_single_file_dirs"]
    auto_s3_upload_on_save: bool = False


class FileBrowserSettingsLlmDraftReq(BaseModel):
    file: str = ""
    prompt: str = ""
    columns: list[str] = []
    sample_rows: list[dict] = []
    current_rule: dict = {}


class FileBrowserSqlLlmDraftReq(BaseModel):
    natural_language: str = ""
    columns: list[str] = []
    dtypes: dict[str, str] = {}
    sample_rows: list[dict] = []
    current_sql: str = ""
    scope: str = ""
    root: str = ""
    product: str = ""
    file: str = ""


class BaseFileValidateReq(BaseModel):
    file: str
    csv_text: str = ""
    delimiter: str = "auto"
    include_header: bool = True


class BaseTextFileSaveReq(BaseModel):
    file: str
    text: str = ""
    username: str = ""
    note: str = ""


class BaseHistoryMigrateReq(BaseModel):
    file: str
    username: str = ""
    note: str = ""


class SchemaSnapshotReq(BaseModel):
    source_type: str = ""
    root: str = ""
    product: str = ""
    file: str = ""
    columns: list[str] = []
    dtypes: dict[str, str] = {}
    grain: str = ""
    join_keys: list[str] = []
    total_rows: int | None = None
    username: str = ""
    note: str = ""


class BaseFileRollbackReq(BaseModel):
    file: str
    version: str
    username: str = ""
    note: str = ""


@router.get("/settings")
def filebrowser_settings(request: Request):
    from core.auth import current_user
    me = current_user(request)
    settings = _load_filebrowser_settings()
    return {
        **settings,
        "can_manage": _can_manage_filebrowser(me),
        "max_csv_full_read_max_bytes": MAX_CSV_FULL_READ_MAX_BYTES,
        "max_csv_download_max_rows": MAX_CSV_DOWNLOAD_MAX_ROWS,
    }


@router.post("/settings/llm/draft")
def filebrowser_settings_llm_draft(req: FileBrowserSettingsLlmDraftReq, request: Request):
    _require_filebrowser_manager(request)
    file_key = _clean_rule_file_key(req.file)
    prompt = _cache_safe_text(req.prompt, 2000)
    if not prompt:
        raise HTTPException(400, "prompt is required")
    sample_rows = _safe_sample_rows(req.sample_rows)
    columns = _settings_context_columns(req.columns, sample_rows)
    current_rule, current_warnings = _normalize_csv_rule_draft(req.current_rule or {}, columns=columns)
    warnings: list[str] = list(current_warnings)
    llm_info = {"available": False, "used": False, "error": ""}
    plan: dict = {}
    try:
        from core import llm_adapter
        llm_info["available"] = bool(llm_adapter.is_available())
        if llm_info["available"]:
            system = _filebrowser_agent_prompt("settings_draft.system", (
                "You are an expert Flow FileBrowser CSV rule designer. Return only JSON. "
                "Use only supplied columns and only csv_rules keys: required_columns, not_empty, "
                "unique_keys, enums, numeric, date, regex, conditions, ordered_by, sort. "
                "Draft the most detailed safe rule set the prompt supports. "
                "ordered_by validates existing row order; sort physically reorders rows on save. "
                "Order spec type must be one of string, numeric, date, leading_number, rule_order. "
                "conditions must be simple Polars SQL boolean expressions over supplied columns. "
                "Do not write files, source code, paths, shell commands, or unsupported keys."
            ))
            ask = json.dumps({
                "file": file_key,
                "user_prompt": prompt,
                "expert_mode": _settings_prompt_wants_expert(prompt),
                "columns": columns[:200],
                "column_profiles": _settings_column_profiles(columns, sample_rows),
                "sample_rows": sample_rows,
                "current_rule": current_rule,
                "rule_engine_capabilities": {
                    "required_columns": "listed columns must exist",
                    "not_empty": "listed columns cannot be blank",
                    "unique_keys": "each listed column combo must be unique",
                    "enums": "column value must be one of the listed strings",
                    "numeric": "min/max/integer checks",
                    "date": "date/time parse check",
                    "regex": "Python regex full-row value pattern check",
                    "conditions": "AND-style row pass conditions; every expression must be true",
                    "ordered_by": "validate current CSV row order; keys may include group_by",
                    "sort": "reorder rows during save using the same key shape",
                },
                "response_schema": {
                    "csv_rules": {
                        file_key: {
                            "required_columns": ["column"],
                            "not_empty": ["column"],
                            "unique_keys": [["column_a", "column_b"]],
                            "enums": {"column": ["allowed"]},
                            "numeric": {"column": {"min": 0, "max": 1, "integer": False}},
                            "date": ["column"],
                            "regex": {"column": "pattern"},
                            "conditions": [{"expr": "column != ''", "message": "message"}],
                            "ordered_by": {"keys": [{"column": "column", "direction": "asc", "type": "string", "nulls": "last"}]},
                            "sort": [{"column": "column", "direction": "asc", "type": "string", "nulls": "last"}],
                        }
                    },
                    "warnings": ["optional warning"],
                },
            }, ensure_ascii=False)
            out = llm_adapter.complete(ask, system=system, timeout=30)
            llm_info["used"] = bool(out.get("ok") and out.get("text"))
            if out.get("error"):
                llm_info["error"] = str(out.get("error") or "")
            if out.get("text"):
                plan = _cache_llm_json(str(out.get("text") or ""))
    except Exception as exc:
        llm_info["error"] = f"{type(exc).__name__}: {exc}"
    if llm_info.get("available") and not llm_info.get("used") and llm_info.get("error"):
        _draft_warning(warnings, f"LLM failed: {llm_info['error']}")
    for item in (plan.get("warnings") if isinstance(plan, dict) else []) or []:
        _draft_warning(warnings, str(item))
    explicit_rule = _settings_prompt_explicit_rule(prompt, columns, current_rule, warnings)
    if explicit_rule is not None:
        raw_rule = explicit_rule
    else:
        raw_rule = _settings_llm_rule_candidate(plan, file_key)
        if not raw_rule:
            raw_rule = _settings_draft_fallback_rule(prompt, columns, current_rule, warnings, sample_rows)
    draft, draft_warnings = _normalize_csv_rule_draft(raw_rule, columns=columns)
    for item in draft_warnings:
        _draft_warning(warnings, item)
    return {
        "ok": True,
        "saved": False,
        "file": file_key,
        "unit_action": "filebrowser.settings.llm.draft",
        "draft": draft,
        "csv_rules": {file_key: draft} if draft else {},
        "warnings": warnings,
        "columns": columns,
        "llm": llm_info,
        "raw_plan": {k: plan.get(k) for k in ("csv_rules", "draft", "rule", "warnings") if isinstance(plan, dict) and k in plan},
    }


@router.post("/sql/llm/draft")
def filebrowser_sql_llm_draft(req: FileBrowserSqlLlmDraftReq, request: Request):
    _require_filebrowser_user(request)
    return _draft_filebrowser_ai_sql(
        natural_language=req.natural_language,
        columns=req.columns or [],
        dtypes=req.dtypes or {},
        sample_rows=req.sample_rows or [],
        current_sql=req.current_sql,
        scope=req.scope,
        root=req.root,
        product=req.product,
        file=req.file,
    )


@router.post("/settings")
def save_filebrowser_settings(req: FileBrowserSettingsReq, request: Request):
    me = _require_filebrowser_manager(request)
    dump = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    settings = _normalize_filebrowser_settings(dump)
    _save_filebrowser_settings(settings)
    jsonl_append(PATHS.activity_log, {
        "username": me.get("username") or "",
        "action": "filebrowser:settings:save",
        "tab": "filebrowser",
        "detail": f"csv_rules={len(settings.get('csv_rules') or {})} hidden_db_dirs={len(settings.get('hidden_db_dirs') or [])} versioned_dirs={len(settings.get('versioned_single_file_dirs') or [])} csv_full_read_max_bytes={settings.get('csv_full_read_max_bytes')} csv_download_max_rows={settings.get('csv_download_max_rows')}",
    })
    return {**settings, "ok": True, "can_manage": True}


@router.post("/base-file/validate")
def validate_base_file_csv(req: BaseFileValidateReq, request: Request):
    _require_filebrowser_manager(request)
    fp = _resolve_base_file_for_edit(req.file)
    if fp.suffix.lower() != ".csv":
        raise HTTPException(400, "CSV validation is available for .csv files only")
    text = req.csv_text or ""
    if not text:
        try:
            if fp.stat().st_size > BASE_FILE_EDIT_MAX_BYTES:
                raise HTTPException(413, f"CSV too large for validation (max {BASE_FILE_EDIT_MAX_BYTES:,} bytes)")
            text = fp.read_text(encoding="utf-8")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, f"Cannot read CSV: {e}")
    rows, used_delim = _parse_tab_or_csv(text, req.delimiter)
    if req.include_header and rows:
        header = [str(x).strip() for x in rows[0]]
        data_rows = rows[1:]
    else:
        try:
            lf = scan_one_file(fp)
            header = list(lf.collect_schema().names()) if lf is not None else []
        except Exception:
            header = []
        data_rows = rows
    if not header:
        header = [f"col_{i + 1}" for i in range(max((len(r) for r in data_rows), default=1))]
    data_rows, _ = _normalize_rows(data_rows, len(header), "")
    sorted_rows, result = _validate_and_sort_csv_rows(req.file, header, data_rows)
    result.update({
        "file": req.file,
        "delimiter": used_delim,
        "columns_list": header,
        "preview_rows": [dict(zip(header, row)) for row in sorted_rows[:20]],
        "sorted_csv_text": _rows_to_csv_text(header, sorted_rows, used_delim, include_header=req.include_header) if result.get("ok") else "",
    })
    return result


def _save_base_file(req: BaseFileSaveReq, request: Request):
    from core.auth import current_user, is_page_admin
    me = current_user(request)
    if (me.get("role") or "") != "admin" and not is_page_admin(me.get("username") or "", "filebrowser"):
        raise HTTPException(403, "Admin or delegated filebrowser admin only")

    if (req.mode or "").strip().lower() != "replace":
        raise HTTPException(400, "Only mode='replace' is supported")
    text = (req.csv_text or "").strip()
    if not text and req.include_header is False:
        raise HTTPException(400, "csv_text is required")

    if len((req.csv_text or "").encode("utf-8")) > BASE_FILE_EDIT_MAX_BYTES:
        raise HTTPException(
            413,
            f"CSV payload too large: {len((req.csv_text or '').encode('utf-8')):,} bytes (max {BASE_FILE_EDIT_MAX_BYTES:,})",
        )

    fp = _resolve_base_file_for_edit(req.file)
    ext = fp.suffix.lower()
    if ext not in BASE_EDIT_ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported file type: {ext}")

    rows, used_delim = _parse_tab_or_csv(req.csv_text or "", req.delimiter)
    if req.include_header and rows:
        header = [str(x).strip() for x in rows[0]]
        data_rows = rows[1:]
    else:
        header = []
        data_rows = rows

    schema_rows = []
    try:
        lf = scan_one_file(fp)
        if lf is not None:
            schema_rows = list(lf.collect_schema().names())
    except Exception:
        schema_rows = []

    if not header and schema_rows:
        header = list(schema_rows)
    if not header:
        header = [f"col_{i + 1}" for i in range(max((len(r) for r in data_rows), default=1))]

    data_rows, _ = _normalize_rows(data_rows, len(header), "")
    if len(data_rows) > BASE_FILE_EDIT_MAX_ROWS:
        raise HTTPException(413, f"Row count too large: {len(data_rows):,} rows (max {BASE_FILE_EDIT_MAX_ROWS:,})")
    csv_validation = {
        "ok": True,
        "rule_applied": False,
        "rule_summary": None,
        "sorted": False,
        "errors": [],
        "error_count": 0,
    }
    if ext == ".csv":
        data_rows, csv_validation = _validate_and_sort_csv_rows(req.file, header, data_rows)
        if not csv_validation.get("ok"):
            raise HTTPException(400, {
                "message": "CSV validation failed",
                "file": req.file,
                "errors": csv_validation.get("errors") or [],
                "error_count": csv_validation.get("error_count") or 0,
                "truncated": bool(csv_validation.get("truncated")),
                "rule_summary": csv_validation.get("rule_summary"),
            })

    backup = None
    version_meta = None
    try:
        backup = _ensure_base_file_backup(fp)
    except Exception:
        backup = None
    try:
        version_meta = _snapshot_base_file_version(
            fp,
            req.file,
            actor=me.get("username") or "",
            action="edit",
            note=req.note or "FileBrowser single-file edit",
        )
    except Exception as e:
        logger.warning("base-file/save version snapshot skipped file=%s: %s", fp, e)

    try:
        if ext == ".csv":
            _write_text_atomic(fp, _rows_to_csv_text(header, data_rows, used_delim, include_header=req.include_header))
        else:
            data_map = {col: [r[i] if i < len(r) else "" for r in data_rows] for i, col in enumerate(header)}
            df = pl.DataFrame(data_map if data_map else {col: pl.Series([]) for col in header})
            if header:
                for col in header:
                    df = df.with_columns(pl.col(col).cast(pl.Utf8, strict=False))
            _write_parquet_atomic(fp, df)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Save failed: {e}")

    version_meta = _attach_post_save_change_summary(req.file, version_meta, fp)

    try:
        cache_result = None
        if _matching_cache.is_matching_file(fp):
            cache_result = _matching_cache.refresh_matching_csv(fp)
            if not cache_result.get("ok", False):
                logger.warning("filebrowser base-file/save cache refresh failed: %s", cache_result)
        sync_result = _filebrowser_s3_sync_for_saved_path(fp)
        jsonl_append(PATHS.activity_log, {
            "username": me.get("username") or "",
            "action": "filebrowser:base-file:save",
            "tab": "filebrowser",
            "detail": f"file={req.file} rows={len(data_rows)} cols={len(header)} version={(version_meta or {}).get('version', '')}",
        })
        return {
            "ok": True,
            "file": req.file,
            "backup": backup,
            "source_path": str(fp),
            "source_modified": fp.stat().st_mtime,
            "delimiter": used_delim,
            "rows": len(data_rows),
            "cols": len(header),
            "version": version_meta,
            "cache_rows": (cache_result or {}).get("rows"),
            "step_cache_rows": None,
            "s3_sync": sync_result,
            "csv_validation": csv_validation,
        }
    except Exception as e:
        raise HTTPException(500, f"Failed to read result after save: {e}")


@router.post("/base-file/save")
@router.post("/base-file/save/")
@router.post("/base-file-save")
def save_base_file(req: BaseFileSaveReq, request: Request):
    """Replace a Base-scope single CSV/Parquet file with pasted text."""
    return _save_base_file(req, request)


@router.post("/base-file/text-save")
def save_base_text_file(req: BaseTextFileSaveReq, request: Request):
    from core.auth import current_user, is_page_admin
    me = current_user(request)
    if (me.get("role") or "") != "admin" and not is_page_admin(me.get("username") or "", "filebrowser"):
        raise HTTPException(403, "Admin or delegated filebrowser admin only")
    target = _resolve_base_file_for_version(req.file)
    if not _base_file_versioned(req.file, target):
        raise HTTPException(400, "This file is not configured for EDM text editing")
    if target.suffix.lower() not in {".json", ".yaml", ".yml", ".md", ".txt", ".csv"}:
        raise HTTPException(400, f"Unsupported text file type: {target.suffix}")
    if len((req.text or "").encode("utf-8")) > BASE_FILE_EDIT_MAX_BYTES:
        raise HTTPException(413, f"Text payload too large (max {BASE_FILE_EDIT_MAX_BYTES:,} bytes)")
    if target.suffix.lower() == ".json":
        try:
            json.loads(req.text or "")
        except Exception as e:
            raise HTTPException(400, f"Invalid JSON: {e}")
    if target.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
            yaml.safe_load(req.text or "")
        except ImportError:
            pass
        except Exception as e:
            raise HTTPException(400, f"Invalid YAML: {e}")
    version_meta = _snapshot_base_file_version(
        target,
        req.file,
        actor=req.username or me.get("username") or "",
        action="edit",
        note=req.note or "FileBrowser raw text edit",
    )
    try:
        _write_text_atomic(target, req.text or "")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Text save failed: {e}")
    version_meta = _attach_post_save_change_summary(req.file, version_meta, target)
    sync_result = _filebrowser_s3_sync_for_saved_path(target)
    jsonl_append(PATHS.activity_log, {
        "username": req.username or me.get("username") or "",
        "action": "filebrowser:base-file:text-save",
        "tab": "filebrowser",
        "detail": f"file={req.file} version={(version_meta or {}).get('version', '')}",
    })
    return {
        "ok": True,
        "file": req.file,
        "source_path": str(target),
        "source_modified": target.stat().st_mtime,
        "version": version_meta,
        "size": target.stat().st_size,
        "s3_sync": sync_result,
    }


@router.get("/base-file/versions")
def base_file_versions(request: Request, file: str = Query(...)):
    from core.auth import current_user
    current_user(request)
    fp = _resolve_base_file_for_version(file)
    versioned = _base_file_versioned(file, fp)
    versions = _list_base_file_versions(file) if versioned else []
    versions.sort(key=lambda v: str(v.get("created_at") or ""), reverse=True)
    profile = _file_profile(fp)
    try:
        modified_at = datetime.datetime.fromtimestamp(fp.stat().st_mtime).isoformat(timespec="seconds")
    except Exception:
        modified_at = ""
    return {
        "ok": True,
        "file": file,
        "versioned": versioned,
        "cap": BASE_VERSION_CAP,
        "versions": versions[:BASE_VERSION_CAP],
        "current_profile": {
            "rows": profile.get("rows"),
            "columns": profile.get("column_count"),
            "size": profile.get("size"),
            "modified_at": modified_at,
            "checksum": profile.get("checksum") or "",
        },
    }


@router.get("/base-file/version-content")
def base_file_version_content(request: Request, file: str = Query(...), version: str = Query(...)):
    from core.auth import current_user
    current_user(request)
    target = _resolve_base_file_for_version(file)
    clean_version = safe_filename(version)
    content_fp, meta = _resolve_base_version_content(file, clean_version, target)
    storage_version = str(meta.get("version") or clean_version)
    previous_fp = _previous_version_content(file, storage_version)
    ext = content_fp.suffix.lower()
    out = {"ok": True, "file": file, "version": clean_version, "meta": meta, "kind": ext.lstrip(".")}
    out["current_profile"] = _file_profile(target)
    out["version_profile"] = _file_profile(content_fp)
    out["diff"] = _profile_diff(out["current_profile"], out["version_profile"])
    out["diff_table"] = meta.get("save_diff_table") or _diff_table_between(content_fp, previous_fp)
    if ext in {".csv", ".txt", ".json", ".yaml", ".yml", ".md"}:
        raw = content_fp.read_text(encoding="utf-8", errors="replace")
        out["text"] = raw[:100_000]
        out["truncated"] = len(raw) > 100_000
    elif ext == ".parquet":
        lf = scan_one_file(content_fp)
        cols = list(lf.collect_schema().names()) if lf is not None else []
        sample = lf.head(50).collect().to_dicts() if lf is not None else []
        out["columns"] = cols
        out["rows"] = serialize_rows(sample)
    return out


@router.post("/base-file/rollback")
def rollback_base_file(req: BaseFileRollbackReq, request: Request):
    from core.auth import current_user, is_page_admin
    me = current_user(request)
    if (me.get("role") or "") != "admin" and not is_page_admin(me.get("username") or "", "filebrowser"):
        raise HTTPException(403, "Admin or delegated filebrowser admin only")
    target = _resolve_base_file_for_version(req.file)
    if not _base_file_versioned(req.file, target):
        raise HTTPException(400, "This file is not configured for EDM version rollback")
    clean_version = safe_filename(req.version)
    content_fp, meta = _resolve_base_version_content(req.file, clean_version, target)
    if content_fp.suffix.lower() != target.suffix.lower():
        raise HTTPException(400, "Version file type does not match current file")
    pre = _snapshot_base_file_version(
        target,
        req.file,
        actor=req.username or me.get("username") or "",
        action="pre-rollback",
        note=f"Before rollback to {clean_version}",
    )
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.rollback.", suffix=".tmp", dir=str(target.parent))
    try:
        os.close(fd)
        shutil.copy2(content_fp, tmp_name)
        os.replace(tmp_name, target)
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except Exception:
            pass
    applied = _snapshot_base_file_version(
        target,
        req.file,
        actor=req.username or me.get("username") or "",
        action="rollback",
        note=req.note or f"Rolled back to {clean_version}",
    )
    sync_result = _filebrowser_s3_sync_for_saved_path(target)
    jsonl_append(PATHS.activity_log, {
        "username": req.username or me.get("username") or "",
        "action": "filebrowser:base-file:rollback",
        "tab": "filebrowser",
        "detail": f"file={req.file} version={clean_version}",
    })
    return {"ok": True, "file": req.file, "rolled_back_to": clean_version, "pre_rollback": pre, "version": applied, "s3_sync": sync_result}


@router.post("/base-file/migrate-history")
def migrate_base_file_history(req: BaseHistoryMigrateReq, request: Request):
    from core.auth import current_user, is_page_admin
    me = current_user(request)
    if (me.get("role") or "") != "admin" and not is_page_admin(me.get("username") or "", "filebrowser"):
        raise HTTPException(403, "Admin or delegated filebrowser admin only")
    target = _resolve_base_file_for_version(req.file)
    result = _migrate_legacy_history(
        target,
        req.file,
        actor=req.username or me.get("username") or "",
        note=req.note or "",
    )
    jsonl_append(PATHS.activity_log, {
        "username": req.username or me.get("username") or "",
        "action": "filebrowser:base-file:migrate-history",
        "tab": "filebrowser",
        "detail": f"file={req.file} migrated={result.get('migrated')} skipped={result.get('skipped')}",
    })
    return {"ok": True, "file": req.file, **result}


def _schema_source_id(req: SchemaSnapshotReq) -> str:
    parts = [
        str(req.source_type or "").strip() or "source",
        str(req.root or "").strip(),
        str(req.product or "").strip(),
        str(req.file or "").strip(),
    ]
    raw = "::".join(p for p in parts if p)
    return re.sub(r"[^A-Za-z0-9_.-]+", "__", raw).strip("._-")[:180] or "source"


def _schema_diff(current_cols: list[str], previous_cols: list[str]) -> dict:
    cur = [str(c) for c in current_cols if str(c).strip()]
    prev = [str(c) for c in previous_cols if str(c).strip()]
    prev_set = set(prev)
    cur_set = set(cur)
    return {
        "added_columns": [c for c in cur if c not in prev_set],
        "removed_columns": [c for c in prev if c not in cur_set],
        "column_count_delta": len(cur) - len(prev),
        "unchanged": cur == prev,
    }


def _schema_snapshot_diff(current: dict | None, previous: dict | None) -> dict:
    current = current or {}
    previous = previous or {}
    diff = _schema_diff(current.get("columns", []) or [], previous.get("columns", []) or [])
    cur_dtypes = current.get("dtypes") if isinstance(current.get("dtypes"), dict) else {}
    prev_dtypes = previous.get("dtypes") if isinstance(previous.get("dtypes"), dict) else {}
    common = [c for c in (current.get("columns") or []) if c in prev_dtypes]
    diff["dtype_changes"] = [
        {"column": c, "before": prev_dtypes.get(c), "after": cur_dtypes.get(c)}
        for c in common
        if cur_dtypes.get(c) is not None and prev_dtypes.get(c) is not None and cur_dtypes.get(c) != prev_dtypes.get(c)
    ]
    cur_keys = [str(x) for x in (current.get("join_keys") or [])]
    prev_keys = [str(x) for x in (previous.get("join_keys") or [])]
    diff["added_join_keys"] = [k for k in cur_keys if k not in set(prev_keys)]
    diff["removed_join_keys"] = [k for k in prev_keys if k not in set(cur_keys)]
    diff["grain_changed"] = bool(previous and str(current.get("grain") or "") != str(previous.get("grain") or ""))
    diff["unchanged"] = bool(
        diff.get("unchanged")
        and not diff["dtype_changes"]
        and not diff["added_join_keys"]
        and not diff["removed_join_keys"]
        and not diff["grain_changed"]
    )
    return diff


@router.post("/schema/snapshot")
def save_schema_snapshot(req: SchemaSnapshotReq, request: Request):
    from core.auth import current_user
    me = current_user(request)
    cols = []
    seen = set()
    for col in req.columns or []:
        name = str(col or "").strip()
        if name and name not in seen:
            seen.add(name)
            cols.append(name)
    if not cols:
        raise HTTPException(400, "columns are required")
    sid = _schema_source_id(req)
    SCHEMA_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    fp = SCHEMA_PROFILE_DIR / f"{sid}.json"
    try:
        payload = json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else {"source_id": sid, "snapshots": []}
    except Exception:
        payload = {"source_id": sid, "snapshots": []}
    snapshots = payload.get("snapshots") if isinstance(payload.get("snapshots"), list) else []
    previous = snapshots[0] if snapshots else None
    snap = {
        "schema_version": f"s{len(snapshots) + 1}",
        "source_id": sid,
        "source_type": req.source_type,
        "root": req.root,
        "product": req.product,
        "file": req.file,
        "columns": cols,
        "dtypes": {str(k): str(v) for k, v in (req.dtypes or {}).items() if str(k).strip()},
        "grain": req.grain,
        "join_keys": [str(k) for k in (req.join_keys or []) if str(k).strip()],
        "column_count": len(cols),
        "total_rows": req.total_rows,
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "actor": req.username or me.get("username") or "",
        "note": req.note or "",
        "checksum": "sha256:" + hashlib.sha256("\n".join(cols).encode("utf-8")).hexdigest(),
    }
    diff = _schema_snapshot_diff(snap, previous if isinstance(previous, dict) else None)
    payload["source_id"] = sid
    payload["snapshots"] = [snap] + snapshots[: SCHEMA_PROFILE_CAP - 1]
    _write_text_atomic(fp, json.dumps(payload, ensure_ascii=False, indent=2))
    jsonl_append(PATHS.activity_log, {
        "username": req.username or me.get("username") or "",
        "action": "filebrowser:schema:snapshot",
        "tab": "filebrowser",
        "detail": f"source={sid} columns={len(cols)} added={len(diff['added_columns'])} removed={len(diff['removed_columns'])}",
    })
    return {"ok": True, "source_id": sid, "snapshot": snap, "previous": previous, "diff": diff, "count": len(payload["snapshots"])}


@router.get("/schema/snapshots")
def schema_snapshots(
    request: Request,
    source_type: str = Query(""),
    root: str = Query(""),
    product: str = Query(""),
    file: str = Query(""),
):
    from core.auth import current_user
    current_user(request)
    req = SchemaSnapshotReq(source_type=source_type, root=root, product=product, file=file)
    sid = _schema_source_id(req)
    fp = SCHEMA_PROFILE_DIR / f"{sid}.json"
    try:
        payload = json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else {"source_id": sid, "snapshots": []}
    except Exception:
        payload = {"source_id": sid, "snapshots": []}
    snapshots = payload.get("snapshots") if isinstance(payload.get("snapshots"), list) else []
    latest = snapshots[0] if snapshots else None
    previous = snapshots[1] if len(snapshots) > 1 else None
    diff = _schema_snapshot_diff(latest if isinstance(latest, dict) else None, previous if isinstance(previous, dict) else None)
    return {"ok": True, "source_id": sid, "snapshots": snapshots, "latest": latest, "previous": previous, "diff": diff}


@router.post("/base-file/delete")
def delete_base_file(req: BaseDeleteReq, request: Request):
    """Delete only Files/upload single files. DB root is read-only for everyone."""
    from core.auth import current_user, is_page_admin
    me = current_user(request)
    if (me.get("role") or "") != "admin" and not is_page_admin(me.get("username") or "", "filebrowser"):
        raise HTTPException(403, "Admin or delegated filebrowser admin only")
    name = (req.file or "").strip()
    if not name or "/" in name or "\\" in name or ".." in name or name.startswith("."):
        raise HTTPException(400, "Invalid filename")

    allowed_ext = {".csv", ".json", ".txt"}
    host_root = PATHS.upload_dir
    fp = (host_root / name).resolve()
    try:
        fp.relative_to(host_root.resolve())
    except ValueError:
        raise HTTPException(400, "Invalid filename")
    if not fp.is_file():
        raise HTTPException(404, f"Not found in Files uploads: {name}")
    if fp.suffix.lower() not in allowed_ext:
        raise HTTPException(400, f"Unsupported file type: {fp.suffix}")

    try:
        trash = host_root / ".trash"
        trash.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        archived = trash / f"{ts}_{name}"
        fp.rename(archived)
        logger.info(f"base-file/delete uploads: {name} → {archived} (by {me.get('username')})")
        return {"ok": True, "file": name, "archived": str(archived), "host": host_root.name}
    except Exception as e:
        raise HTTPException(500, f"Delete failed: {e}")


@router.get("/sql-guide")
def sql_guide():
    return {"examples": [
        {"desc": "Equal", "sql": "col_name == 'value'"},
        {"desc": "LIKE", "sql": "col_name LIKE '%pattern%'"},
        {"desc": "NOT LIKE", "sql": "col_name NOT LIKE '%X%'"},
        {"desc": "IN", "sql": "col_name.is_in(['A','B'])"},
        {"desc": "AND", "sql": "(col_a > 1) & (col_b == 'X')"},
        {"desc": "BETWEEN", "sql": "(col >= 0.1) & (col <= 0.9)"},
    ]}
