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
    data_files_limited, source_data_files,
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
MAX_CSV_DOWNLOAD_AUTO_COLUMNS = 200
BASE_FILE_EDIT_MAX_BYTES = 25_000_000
BASE_FILE_EDIT_MAX_ROWS = 200_000
BASE_EDIT_ALLOWED_EXTENSIONS = {".csv", ".parquet"}
BASE_EDIT_HISTORY_DIR = ".history"
BASE_EDIT_RESERVED_PREFIXES = {"product_config", "reformatter", "uploads", "cache"}
BASE_VERSION_DIR = PATHS.data_root / "file_versions"
BASE_VERSION_CAP = 50
SCHEMA_PROFILE_DIR = PATHS.data_root / "schema_profiles"
SCHEMA_PROFILE_CAP = 30
LATEST_PREVIEW_ROWS = 200
LATEST_PREVIEW_MAX_FILES = 4
LIST_CACHE_TTL_SEC = 5.0
MAX_WAFER_ID = 25
_SINGLE_FILE_STEP_CACHE_DIR = "cache"
_SINGLE_FILE_STEP_CACHE_FILE = "latest_step_by_lot.parquet"
_SINGLE_FILE_STEP_CACHE_VERSION = 2
_SINGLE_FILE_PREVIEW_MAX_BYTES = 64 * 1024 * 1024
_SORT_STR = getattr(pl, "Utf8", None) or getattr(pl, "String", pl.Object)
_LIST_CACHE: dict[tuple, tuple[float, object]] = {}

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


def _single_file_cache_entries(root: Path, source_root: str) -> list[dict]:
    cache = _single_file_cache_dir(root)
    if not cache.is_dir():
        return []
    out: list[dict] = []
    pattern = f"*.{_SINGLE_FILE_STEP_CACHE_FILE}"
    for fp in sorted(cache.glob(pattern), key=lambda p: p.name.lower()):
        if not fp.is_file():
            continue
        try:
            stat = fp.stat()
        except OSError:
            continue
        rel = f"{_SINGLE_FILE_STEP_CACHE_DIR}/{fp.name}"
        out.append({
            "name": rel,
            "path": rel,
            "size": stat.st_size,
            "modified": stat.st_mtime,
            "ext": fp.suffix.lower().lstrip("."),
            "kind": "file",
            "source": "cache",
            "source_root": source_root,
            "source_path": str(fp),
            "role": "latest step cache",
            "description": "product/lot_id별 latest_step_id와 updated_at 캐시",
            "order": 1,
            "editable": False,
        })
    return out


def _single_file_step_cache_candidate(path: Path) -> bool:
    if not path.is_file() or path.suffix.lower() not in {".csv", ".parquet"}:
        return False
    meta = _core_file_meta(path.name)
    if not meta:
        return False
    return meta.get("role") in {"ML_TABLE parquet", "Feature parquet", "Parquet file", "CSV file"}


def _refresh_single_file_step_caches(root: Path) -> None:
    if not root.is_dir():
        return
    try:
        items = sorted(root.iterdir(), key=lambda p: p.name.lower())
    except Exception:
        return
    for item in items:
        if not _visible_single_file(item) or not _single_file_step_cache_candidate(item):
            continue
        try:
            _build_single_file_step_cache(item)
        except Exception as e:
            logger.warning("single-file latest-step cache skipped (%s): %s", item, e)


def _ensure_single_file_cache_dirs(base_root: Path, db_root: Path) -> None:
    for root in (base_root, db_root):
        if not root.is_dir():
            continue
        has_single = False
        try:
            for item in root.iterdir():
                if item.is_file() and _visible_single_file(item):
                    has_single = True
                    break
        except Exception:
            continue
        if has_single:
            try:
                _single_file_cache_dir(root).mkdir(parents=True, exist_ok=True)
            except Exception:
                pass


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
    if not (product_col and lot_col and step_col):
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
            pl.col(product_col).cast(pl.Utf8, strict=False).str.strip_chars().alias("product"),
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


def _base_file_versioned(file: str, target: Path | None = None) -> bool:
    rel = str(file or "").strip().replace("\\", "/").lower()
    name = Path(rel).name.lower()
    if name in EDM_VERSIONED_SINGLE_FILES:
        return True
    if rel == "product_config/products.yaml":
        return True
    if rel.startswith("reformatter/") and name.endswith((".csv", ".json")):
        return True
    if target is not None and target.name.lower() in EDM_VERSIONED_SINGLE_FILES:
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
def domain_info():
    """v7.2: Expose canonical domain model to frontend (level hierarchy, granularity, DB registry)."""
    from core.domain import DB_REGISTRY, VISIBLE_CANONICAL, LEVEL_ORDER
    return {
        "dbs": {k: v for k, v in DB_REGISTRY.items() if k in VISIBLE_CANONICAL or k == "ML_TABLE"},
        "level_order": LEVEL_ORDER,
        "visible": sorted(list(VISIBLE_CANONICAL)),
    }


@router.get("/roots")
def list_roots(all: bool = Query(False)):
    """v7.1: only canonical whitelisted DBs (FAB/VM/MASK/KNOB/INLINE/ET/YLD/ML_TABLE).

    Pass ?all=1 to bypass the whitelist (admin diagnostics).

    v8.7.6 fix: hive/flat 파티션 구조를 가진 임의 디렉토리도 DB 섹션에 노출.
    판단 규칙 — 디렉토리 자체 또는 하위에 parquet/csv 데이터 파일이 존재하면
    whitelist 바깥이어도 DB 로 간주. 루트의 단일 파일은 (신규 정책) Base 섹션에서만 보여줌.
    """
    from core.utils import detect_structure
    from core.domain import is_visible_root, is_visible_file, canonical_name, DB_REGISTRY
    result = []
    DB_BASE = _db_root()
    if not DB_BASE.exists():
        return {"roots": []}
    cache_key = ("roots", bool(all), _path_sig(DB_BASE))
    cached = _list_cache_get(cache_key)
    if cached is not None:
        return cached
    for d in sorted(DB_BASE.iterdir()):
        # v8.1.2: explicit file skip — root-level single files go via Base only (v8.7.6).
        if not d.is_dir():
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
def list_scopes():
    """v4.1: Enumerate top-level data scopes for the sidebar switcher.

    Returns `DB` (Hive-flat source tree) and `Files` (DB root-level files).
    The API key remains "Base" for frontend compatibility.
    """
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
def list_scope_roots():
    """Backward-compat path for clients calling `/scopes/roots`.

    Some mobile/automation callers still target this legacy route shape. Keep it
    aligned with `/roots` behavior to avoid 404 regressions while preserving the
    newer API surface.
    """
    return list_roots()


@router.get("/base-files")
def base_files():
    """v4.1: List top-level files under the Base root (single-file layout).

    Returns only the operational files needed by the current ML_TABLE workflow:
    ML_TABLE_*.parquet, the small matching CSVs, and product_config/products.yaml.
    Directories and legacy helper files remain on disk but are not surfaced here.
    """
    base_root = _base_root()
    db_root = _db_root()
    _ensure_single_file_cache_dirs(base_root, db_root)
    _refresh_single_file_step_caches(base_root)
    if db_root != base_root:
        _refresh_single_file_step_caches(db_root)
    cache_key = (
        "base_files",
        _path_sig(base_root),
        _path_sig(_db_root()),
        _path_sig(_single_file_cache_dir(base_root)),
        _path_sig(_single_file_cache_dir(db_root)),
        _path_sig(PATHS.upload_dir),
    )
    cached = _list_cache_get(cache_key)
    if cached is not None:
        return cached
    files, dirs = [], []
    if base_root.is_dir():
        cache = _single_file_cache_dir(base_root)
        if cache.is_dir():
            try:
                cst = cache.stat()
                dirs.append({
                    "name": _SINGLE_FILE_STEP_CACHE_DIR,
                    "path": f"base_root:{_SINGLE_FILE_STEP_CACHE_DIR}",
                    "size": 0,
                    "modified": cst.st_mtime,
                    "ext": "dir",
                    "kind": "dir",
                    "source": "base_root",
                    "source_path": str(base_root),
                    "description": "single-file step cache",
                    "role": "cache",
                    "order": 0,
                })
            except Exception:
                pass
        files.extend(_single_file_cache_entries(base_root, "base_root"))
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
        cache = _single_file_cache_dir(db_root)
        if cache.is_dir():
            try:
                cst = cache.stat()
                dirs.append({
                    "name": _SINGLE_FILE_STEP_CACHE_DIR,
                    "path": f"db_root:{_SINGLE_FILE_STEP_CACHE_DIR}",
                    "size": 0,
                    "modified": cst.st_mtime,
                    "ext": "dir",
                    "kind": "dir",
                    "source": "db_root",
                    "source_path": str(db_root),
                    "description": "single-file step cache",
                    "role": "cache",
                    "order": 0,
                })
            except Exception:
                pass
        seen_cache_paths = {f["path"].lower() for f in files if f.get("source") == "cache"}
        for entry in _single_file_cache_entries(db_root, "db_root"):
            if entry["path"].lower() in seen_cache_paths:
                continue
            files.append(entry)
            seen_cache_paths.add(entry["path"].lower())
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
                   page_size: int = Query(200, ge=1, le=1000)):
    """v4.1: Preview a file under the Base root.

    Parquet/CSV use the same lazy reader path as `/root-parquet-view`; JSON
    files are returned as-is (truncated to first 2KB preview + full size) so
    `_uniques.json` can be inspected.
    """
    rows = rows if isinstance(rows, int) else 200
    cols = cols if isinstance(cols, int) else 10
    # Guard against path traversal — allow base_root, and also db_root-level
    # single files (CSV/Parquet). v8.7.7: parquet 도 허용 (base-files 에 노출되므로
    # 미리보기도 가능해야 함).
    base_root = _base_root()
    db_root = _db_root()
    fp = None
    rel = Path(file)
    if rel.parts and rel.parts[0] == "product_config":
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
    elif rel.parts and rel.parts[0] == "uploads":
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
    elif rel.parts and rel.parts[0] == "reformatter":
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
        if _single_file_step_cache_candidate(fp):
            try:
                _build_single_file_step_cache(fp)
            except Exception:
                pass
        if ext == ".csv":
            try:
                st = fp.stat()
                if st.st_size >= _SINGLE_FILE_PREVIEW_MAX_BYTES:
                    _build_single_file_step_cache(fp)
            except Exception:
                pass
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
            }
        cached_meta = None
        if ext == ".parquet":
            try:
                from core.parquet_perf import read_meta
                cached_meta = read_meta(fp)
            except Exception:
                cached_meta = None
        ml_table = _is_ml_table_file(fp)
        full_single_file = (not ml_table) and ext != ".csv"
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
            return resp
        rows = min(int(rows or 200), 200)
        page_size = min(int(page_size or 200), 200)
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
                return resp
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
        return resp
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
    s = (sql or "").strip()
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
        df = apply_sql_like(df, sql)
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
    show_plus, _all_cols, _schema = duckdb_engine.query_files(
        files,
        where=_combine_where(sql, wafer_where),
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
            df = apply_sql_like(df, sql)
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
                    return _run_view_duckdb(
                        files, sql, select_cols, rows,
                        page=page, page_size=page_size, preview_cols=cols,
                        latest_first=False, latest_preview=False,
                    )
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
            return _run_view_lazy(lf, sql, select_cols, rows, meta_only=meta_only,
                                  page=page, page_size=page_size, preview_cols=cols,
                                  latest_first=latest_preview, latest_preview=latest_preview)
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
        return _run_view(df, sql, select_cols, rows, page=page, page_size=page_size,
                         preview_cols=cols, latest_first=latest_preview, latest_preview=latest_preview)
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
        ml_table = _is_ml_table_file(fp)
        full_single_file = (not ml_table) or _has_view_filter(sql, select_cols)
        if full_single_file:
            return _run_view_lazy_full(
                lf, sql, select_cols,
                preview_cols=cols if ml_table else None,
            )
        rows = min(int(rows or 200), 200)
        page_size = min(int(page_size or 200), 200)
        if duckdb_engine.should_use_duckdb([fp], engine=engine, sql=sql, select_cols=select_cols):
            try:
                return _run_view_duckdb(
                    [fp], sql, select_cols, rows,
                    page=page, page_size=page_size, cached_meta=cached_meta,
                    preview_cols=cols,
                )
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
        return resp
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
            df, csv_bytes = _download_lazy_csv(lazy_lf, sql, select_cols, max_rows)
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
            df = apply_sql_like(df, sql)
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
            out = io.StringIO()
            writer = csv.writer(out, delimiter="\t" if used_delim == "tab" else ",", lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
            out_rows = [header] if req.include_header else []
            out_rows.extend(data_rows)
            for row in out_rows:
                writer.writerow(["" if v is None else str(v) for v in row])
            _write_text_atomic(fp, out.getvalue())
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

    try:
        cache_result = None
        if _matching_cache.is_matching_file(fp):
            cache_result = _matching_cache.refresh_matching_csv(fp)
            if not cache_result.get("ok", False):
                logger.warning("filebrowser base-file/save cache refresh failed: %s", cache_result)
        step_cache_result = None
        if _single_file_step_cache_candidate(fp):
            step_cache_result = _build_single_file_step_cache(fp, force=True)
        sync_result = _s3.sync_saved_path(PATHS.data_root, PATHS.db_root, fp)
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
            "step_cache_rows": (step_cache_result or {}).get("rows"),
            "s3_sync": sync_result,
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
    sync_result = _s3.sync_saved_path(PATHS.data_root, PATHS.db_root, target)
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
    versions = _list_base_file_versions(file)
    versions.sort(key=lambda v: str(v.get("created_at") or ""), reverse=True)
    return {
        "ok": True,
        "file": file,
        "versioned": _base_file_versioned(file, fp),
        "cap": BASE_VERSION_CAP,
        "versions": versions[:BASE_VERSION_CAP],
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
    out["diff_table"] = _diff_table_between(content_fp, previous_fp)
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
    sync_result = _s3.sync_saved_path(PATHS.data_root, PATHS.db_root, target)
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
