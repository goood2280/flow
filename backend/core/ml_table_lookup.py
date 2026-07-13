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
import threading
import time
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
    process_memory_snapshot,
)

logger = logging.getLogger("flow.ml_table_lookup")

CACHE_VERSION = 1
MAX_RESULT_ROWS = 25
LOOKUP_CACHE_DIRNAME = "ml_table_lookup"
META_FILE = "_meta.json"
CANDIDATE_INDEX_FILE = "_candidate_index.json"
CANDIDATE_INDEX_VERSION = 1
CANDIDATE_COLUMN_PREFIXES = ("KNOB", "MASK", "INLINE", "VM", "TAG", "MGMT")
BUILD_LOCK_STALE_SECONDS = 30 * 60
LOOKUP_CACHE_MEMORY_WAIT_SECONDS_DEFAULT = 5.0
LOOKUP_CACHE_PARTITION_MAX_ROWS_DEFAULT = 250_000
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
ROOT_RAM_CACHE_PREFIXES_DEFAULT = ("AZ",)
ROOT_RAM_CACHE_PREFIX_ROOTS_DEFAULT = 5000
ROOT_RAM_CACHE_SEARCHED_ROOTS_DEFAULT = 50
ROOT_RAM_CACHE_ROOTS_MAX = 50000
ROOT_RAM_CACHE_BUILD_MAX_MB_DEFAULT = 512.0
ROOT_RAM_CACHE_CPU_CORES_DEFAULT = 2.0
ROOT_RAM_CACHE_RESOURCE_CHECK_SEC = 1.0
ROOT_RAM_CACHE_SETTINGS_FILE = PATHS.data_root / "splittable" / "source_config.json"
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
_ROOT_RAM_RESOURCE_STATE: dict[str, Any] = {
    "checked_epoch": 0.0,
    "reason": "",
    "snapshot": {},
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


def _normalize_prefixes(raw: Any) -> list[str]:
    if isinstance(raw, str):
        parts = raw.replace("\n", ",").split(",")
    elif isinstance(raw, (list, tuple, set)):
        parts = list(raw)
    else:
        parts = []
    out: list[str] = []
    seen: set[str] = set()
    for item in parts:
        prefix = str(item or "").strip().upper()
        if not prefix or prefix in seen:
            continue
        seen.add(prefix)
        out.append(prefix)
    return out


def root_ram_cache_available() -> bool:
    return not _env_bool("FLOW_DISABLE_SPLITTABLE_ROOT_LOT_RAM_CACHE", False)


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


def _root_ram_cache_prefixes() -> list[str]:
    raw = os.environ.get("FLOW_SPLITTABLE_ROOT_LOT_RAM_CACHE_PREFIXES")
    if raw is None:
        raw = _root_ram_settings().get("prefixes")
    prefixes = _normalize_prefixes(raw)
    return prefixes or list(ROOT_RAM_CACHE_PREFIXES_DEFAULT)


def _root_ram_cache_prefix_limit() -> int:
    if "FLOW_SPLITTABLE_ROOT_LOT_RAM_CACHE_PREFIX_ROOTS" in os.environ:
        return _env_int(
            "FLOW_SPLITTABLE_ROOT_LOT_RAM_CACHE_PREFIX_ROOTS",
            ROOT_RAM_CACHE_PREFIX_ROOTS_DEFAULT,
            0,
            ROOT_RAM_CACHE_ROOTS_MAX,
        )
    return _bounded_int(
        _root_ram_settings().get("prefix_limit"),
        ROOT_RAM_CACHE_PREFIX_ROOTS_DEFAULT,
        0,
        ROOT_RAM_CACHE_ROOTS_MAX,
    )


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


def root_ram_cache_settings() -> dict[str, Any]:
    return {
        "prefixes": _root_ram_cache_prefixes(),
        "prefix_limit": _root_ram_cache_prefix_limit(),
        "searched_limit": _root_ram_cache_searched_limit(),
        "recent_roots": _root_ram_cache_recent_limit(),
        "frequent_roots": _root_ram_cache_frequent_limit(),
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
        if bool(snap.get("process_memory_over_limit")) or process_memory_high():
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


def _root_ram_cache_max_bytes() -> int:
    gb = _env_float("FLOW_SPLITTABLE_ROOT_LOT_RAM_CACHE_MAX_GB", ROOT_RAM_CACHE_MAX_GB_DEFAULT, 0.0, 64.0)
    return int(gb * 1024 * 1024 * 1024)


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


def _evict_root_ram_locked(reserve_bytes: int = 0, keep_keys: set[tuple[str, str]] | None = None) -> None:
    keep_keys = keep_keys or set()
    max_bytes = _root_ram_cache_max_bytes()
    if max_bytes <= 0:
        _ROOT_RAM_CACHE.clear()
        return
    while _ROOT_RAM_CACHE and _root_ram_total_bytes_locked() + max(0, reserve_bytes) > max_bytes:
        evicted = False
        for key in list(_ROOT_RAM_CACHE.keys()):
            if key in keep_keys and len(_ROOT_RAM_CACHE) > len(keep_keys):
                continue
            _ROOT_RAM_CACHE.pop(key, None)
            evicted = True
            break
        if not evicted:
            _ROOT_RAM_CACHE.clear()
            return


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
            _ROOT_RAM_CACHE.pop(key, None)
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


def _root_matches_prefix(root_lot_id: str, prefixes: list[str]) -> bool:
    root = str(root_lot_id or "").strip().upper()
    if not root:
        return False
    normalized = [str(prefix or "").strip().upper() for prefix in prefixes or [] if str(prefix or "").strip()]
    return any(root.startswith(prefix) for prefix in normalized)


def _root_ram_cache_group(root_lot_id: str, prefixes: list[str]) -> str:
    return "prefix" if _root_matches_prefix(root_lot_id, prefixes) else "other"


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
    key = _root_cache_key(fp, root)
    source_key = _root_ram_source_key(status)
    part_sig = _partition_sig(files)
    try:
        df = _load_root_ram_cache_frame(files)
    except Exception as exc:
        logger.debug("ML_TABLE root RAM cache load failed source=%s root=%s: %s", fp, root, exc)
        return None
    estimated_bytes = _estimated_df_bytes(df)
    max_bytes = _root_ram_cache_max_bytes()
    if max_bytes and estimated_bytes > max_bytes:
        return None
    now = time.time()
    group = cache_group or _root_ram_cache_group(root, _root_ram_cache_prefixes())
    sources = [str(source or "").strip() for source in (cache_sources or []) if str(source or "").strip()]
    with _ROOT_RAM_CACHE_LOCK:
        _evict_root_ram_locked(reserve_bytes=estimated_bytes, keep_keys={key})
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
        _evict_root_ram_locked()
    return df.lazy()


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


def _recent_root_lot_ids_from_latest_parquet(fp: Path, limit: int) -> list[str]:
    cache_fp = _latest_lot_by_root_wafer_path()
    if limit <= 0 or not cache_fp.is_file():
        return []
    keys = _product_match_keys(fp)
    try:
        lf = pl.scan_parquet(str(cache_fp))
        cols = lf.collect_schema().names()
    except Exception:
        return []
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    if not root_col:
        return []
    product_col = _ci_col(cols, "product", "process_id", "PRODUCT", "PROCESS_ID")
    time_col = _ci_col(cols, "update_time", "tkout_time", "tkin_time", "time", "timestamp", "datetime")
    q = lf
    if product_col and keys:
        q = q.filter(pl.col(product_col).cast(_STR, strict=False).str.strip_chars().str.to_uppercase().is_in(sorted(keys)))
    q = q.select([
        pl.col(root_col).cast(_STR, strict=False).str.strip_chars().str.to_uppercase().alias("root_lot_id"),
        (
            pl.col(time_col).cast(_STR, strict=False).alias("__time")
            if time_col else pl.lit("").alias("__time")
        ),
    ]).filter(pl.col("root_lot_id").is_not_null() & (pl.col("root_lot_id") != ""))
    if time_col:
        q = q.sort("__time", descending=True, nulls_last=True)
    try:
        rows = q.unique(subset=["root_lot_id"], keep="first", maintain_order=True).head(limit).collect()
    except Exception:
        return []
    return [str(v or "").strip().upper() for v in rows["root_lot_id"].to_list() if str(v or "").strip()]


def _recent_root_lot_ids_from_latest_cache(fp: Path, limit: int) -> list[str]:
    if limit <= 0:
        return []
    out: list[str] = []
    seen: set[str] = set()

    def add(root_lot_id: str) -> None:
        root = str(root_lot_id or "").strip().upper()
        if not root or root in seen or len(out) >= limit:
            return
        seen.add(root)
        out.append(root)

    for root in _recent_root_lot_ids_from_latest_parquet(fp, limit):
        add(root)
    if len(out) >= limit:
        return out
    try:
        from core import lot_progress_cache
        state = lot_progress_cache.read_lot_progress_cache(allow_stale=True)
    except Exception:
        return out
    keys = _product_match_keys(fp)
    rows = [row for row in (state.get("items") or []) if isinstance(row, dict)]
    rows.sort(key=_row_time_text, reverse=True)
    for row in rows:
        product = str(row.get("product") or row.get("process_id") or "").strip().upper()
        if keys and product and product not in keys:
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


def _prefix_root_lot_ids_from_lookup_cache(fp: Path, prefixes: list[str], limit: int) -> list[str]:
    prefixes = [str(p or "").strip().upper() for p in prefixes or [] if str(p or "").strip()]
    if not prefixes or limit <= 0:
        return []
    cache_dir = cache_dir_for(fp)
    try:
        children = sorted(cache_dir.iterdir(), key=lambda p: p.name.upper())
    except Exception:
        return []
    out: list[str] = []
    seen: set[str] = set()
    marker = "root_lot_id="
    for child in children:
        name = child.name
        if not child.is_dir() or not name.startswith(marker):
            continue
        root = name[len(marker):].strip().upper()
        if not root or root in seen:
            continue
        if not _root_matches_prefix(root, prefixes):
            continue
        seen.add(root)
        out.append(root)
        if len(out) >= limit:
            break
    return out


def _root_ram_cache_candidates(
    *,
    prefixes: list[str],
    prefix_roots: list[str],
    latest_roots: list[str],
    searched_roots: list[str],
) -> list[dict[str, Any]]:
    candidates: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def add(root_lot_id: str, source: str) -> None:
        root = str(root_lot_id or "").strip().upper()
        if not root:
            return
        group = _root_ram_cache_group(root, prefixes)
        current = candidates.get(root)
        if current is None:
            candidates[root] = {
                "root_lot_id": root,
                "cache_group": group,
                "cache_sources": [source],
            }
            return
        if group == "prefix":
            current["cache_group"] = group
        sources = current.setdefault("cache_sources", [])
        if source not in sources:
            sources.append(source)

    for root in prefix_roots:
        add(root, "prefix")
    for root in latest_roots:
        add(root, "latest")
    for root in searched_roots:
        add(root, "searched")

    rows = list(candidates.values())
    prefix_rows = [row for row in rows if row.get("cache_group") == "prefix"]
    other_rows = [row for row in rows if row.get("cache_group") != "prefix"]
    return [*prefix_rows, *other_rows]


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


def _ensure_lookup_cache_ready_for_root_ram(fp: Path, status: dict[str, Any]) -> bool:
    if status.get("has_cache") and not status.get("source_stale"):
        return True
    try:
        source_size = int(_source_sig(fp).get("source_size") or 0)
    except Exception:
        source_size = 0
    max_build_bytes = _root_ram_cache_build_max_bytes()
    if max_build_bytes and source_size and source_size <= max_build_bytes:
        enqueue_build(fp)
    return False


def refresh_root_lot_ram_cache(product: str = "", file: str = "", *, force: bool = False) -> dict[str, Any]:
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
    prefixes = settings["prefixes"]
    prefix_limit = int(settings["prefix_limit"])
    recent_limit = int(settings["recent_roots"])
    searched_limit = int(settings["searched_limit"])
    rows: list[dict[str, Any]] = []
    with _ROOT_RAM_REFRESH_LOCK:
        for fp in files:
            if fp is None:
                continue
            status = cache_status(fp)
            if not _ensure_lookup_cache_ready_for_root_ram(fp, status):
                rows.append({
                    "file": Path(fp).name,
                    "ok": False,
                    "skipped": True,
                    "reason": status.get("status") or "missing",
                    "cache_status": status.get("status") or "",
                })
                continue
            roots: list[str] = []
            seen: set[str] = set()
            prefix_roots = _prefix_root_lot_ids_from_lookup_cache(fp, prefixes, prefix_limit)
            latest_roots = _recent_root_lot_ids_from_latest_cache(fp, recent_limit)
            searched_roots = _searched_root_lot_ids(fp, searched_limit)
            candidates = _root_ram_cache_candidates(
                prefixes=prefixes,
                prefix_roots=prefix_roots,
                latest_roots=latest_roots,
                searched_roots=searched_roots,
            )
            for candidate in candidates:
                root = str(candidate.get("root_lot_id") or "").strip().upper()
                if root and root not in seen:
                    seen.add(root)
                    roots.append(root)
            prefix_target_roots = len([row for row in candidates if row.get("cache_group") == "prefix"])
            other_target_roots = len(candidates) - prefix_target_roots
            cached = 0
            missing = 0
            resource_skipped = 0
            last_skip_reason = ""
            for idx, candidate in enumerate(candidates):
                root = str(candidate.get("root_lot_id") or "").strip().upper()
                cache_group = str(candidate.get("cache_group") or "")
                cache_sources = [str(source) for source in (candidate.get("cache_sources") or [])]
                part_files = _partition_files(cache_dir_for(fp), root)
                if not part_files:
                    missing += 1
                    continue
                if not force and _root_ram_cache_get(fp, root, part_files, status) is not None:
                    _root_ram_cache_update_metadata(
                        fp,
                        root,
                        cache_group=cache_group,
                        cache_sources=cache_sources,
                    )
                    cached += 1
                    continue
                guard_reason, _snap = _root_ram_cache_resource_guard_reason()
                if guard_reason:
                    resource_skipped += len(candidates) - idx
                    last_skip_reason = guard_reason
                    break
                # 사용자 요청이 진행 중이면 예열을 잠시 멈춰 API 응답을 우선한다.
                from core import request_priority
                request_priority.yield_to_users(max_wait_sec=10.0)
                if _root_ram_cache_put(
                    fp,
                    root,
                    part_files,
                    status,
                    cache_group=cache_group,
                    cache_sources=cache_sources,
                ) is not None:
                    cached += 1
            rows.append({
                "file": Path(fp).name,
                "ok": True,
                "target_roots": len(roots),
                "cached_roots": cached,
                "missing_roots": missing,
                "prefix_roots": len(prefix_roots),
                "prefix_target_roots": prefix_target_roots,
                "other_target_roots": other_target_roots,
                "latest_roots": len(latest_roots),
                "searched_roots": len(searched_roots),
                "resource_skipped_roots": resource_skipped,
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
    return {
        "ok": any(row.get("ok") for row in rows),
        "enabled": True,
        "products": rows,
        "interval_minutes": root_ram_cache_refresh_minutes(),
        "prefixes": prefixes,
        "prefix_roots": prefix_limit,
        "recent_roots": recent_limit,
        "searched_roots": searched_limit,
        "frequent_roots": searched_limit,
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
    entry_groups = [_root_ram_cache_group(key[1], settings["prefixes"]) for key, _entry in entries]
    out = {
        "enabled": root_ram_cache_available(),
        "hit_roots": len(entries),
        "prefix_hit_roots": len([group for group in entry_groups if group == "prefix"]),
        "other_hit_roots": len([group for group in entry_groups if group != "prefix"]),
        "estimated_mb": round(total_bytes / (1024 * 1024), 3),
        "max_gb": round(_root_ram_cache_max_bytes() / (1024 ** 3), 3) if _root_ram_cache_max_bytes() else 0,
        "cpu_budget_cores": round(_root_ram_cache_cpu_budget_cores(), 3),
        "polars_threads": os.environ.get("POLARS_MAX_THREADS") or os.environ.get("FLOW_POLARS_MAX_THREADS") or "",
        "prefixes": settings["prefixes"],
        "prefix_roots": settings["prefix_limit"],
        "searched_roots": settings["searched_limit"],
        "recent_roots": settings["recent_roots"],
        "frequent_roots": settings["searched_limit"],
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
                "cache_group": _root_ram_cache_group(key[1], settings["prefixes"]),
                "cache_sources": list(entry.get("cache_sources") or []),
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
    while not _ROOT_RAM_STOP.is_set():
        try:
            refresh_root_lot_ram_cache(force=False)
        except Exception as exc:
            logger.warning("ML_TABLE root RAM cache scheduler tick failed: %s", exc)
            with _ROOT_RAM_CACHE_LOCK:
                _ROOT_RAM_STATUS["last_error"] = f"{type(exc).__name__}: {exc}"
        wait_s = max(60.0, root_ram_cache_refresh_minutes() * 60.0)
        while wait_s > 0 and not _ROOT_RAM_STOP.is_set():
            step = min(wait_s, 60.0)
            _ROOT_RAM_STOP.wait(step)
            wait_s -= step


def start_root_lot_ram_cache_scheduler() -> bool:
    global _ROOT_RAM_THREAD, _ROOT_RAM_STARTED
    if _ROOT_RAM_STARTED:
        return False
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
    logger.info("ML_TABLE root RAM cache scheduler started (interval=%sm)", root_ram_cache_refresh_minutes())
    return True


def _cache_root() -> Path:
    return PATHS.db_cache_dir / LOOKUP_CACHE_DIRNAME


def cache_dir_for(fp: Path) -> Path:
    return _cache_root() / _safe_product_token(fp.stem)


def meta_path_for(fp: Path) -> Path:
    return cache_dir_for(fp) / META_FILE


def candidate_index_path_for(fp: Path) -> Path:
    return cache_dir_for(fp) / CANDIDATE_INDEX_FILE


def _read_meta(fp: Path) -> dict[str, Any]:
    meta_fp = meta_path_for(fp)
    if not meta_fp.is_file():
        return {}
    try:
        data = json.loads(meta_fp.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_meta(fp: Path, meta: dict[str, Any]) -> None:
    meta_fp = meta_path_for(fp)
    meta_fp.parent.mkdir(parents=True, exist_ok=True)
    tmp = meta_fp.with_suffix(meta_fp.suffix + ".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(meta_fp)


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


def _try_acquire_build_lock(fp: Path) -> tuple[int | None, Path, str]:
    lock_fp = _build_lock_path(fp)
    payload = json.dumps({
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
            if attempt == 0 and age > BUILD_LOCK_STALE_SECONDS:
                try:
                    lock_fp.unlink()
                    continue
                except Exception:
                    pass
            try:
                owner = lock_fp.read_text(encoding="utf-8")[:1000]
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
    has_cache = bool(meta and cdir.is_dir() and _partition_files(cdir))
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


# ── latest-lot-by-root/wafer 캐시: root_lot_id 파티션 배치 순회(메모리 안전) ──
# 카노니컬 파일 `lot_progress_latest_lot_by_root_wafer.parquet` 의 스키마(splittable
# LATEST_LOT_STEP_CACHE_COLUMNS 와 동일)를 그대로 따른다.
LATEST_LOT_BY_ROOT_WAFER_COLUMNS = [
    "product", "root_lot_id", "wafer_id", "lot_id",
    "step_id", "function_step", "tkout_time", "update_time",
]

_LATEST_LOT_TIME_COL_CANDIDATES = (
    "tkout_time", "update_time", "time", "tkin_time",
    "timestamp", "datetime", "date",
)


def _latest_lot_root_batch_size() -> int:
    """한 번에 스캔할 root_lot_id 파티션 수. 작을수록 피크 메모리↓(대신 느려짐)."""
    return _env_int("FLOW_ML_TABLE_LATEST_LOT_ROOT_BATCH", 200, 1, 5000)


def _collect_low_memory(lf: pl.LazyFrame) -> pl.DataFrame:
    try:
        from core.parquet_perf import collect_streaming
        return collect_streaming(lf)
    except Exception:
        return lf.collect()


def _latest_lot_rows_for_root_batch(
    cache_dir: Path,
    roots: list[str],
    product_label: str,
    cols: dict[str, str],
) -> pl.DataFrame | None:
    """root 배치의 파티션만 스캔해 (root_lot_id, wafer_id) 당 최신 lot 한 행을 반환."""
    files: list[Path] = []
    for root in roots:
        files.extend(_partition_files(cache_dir, root))
    if not files:
        return None
    lf = pl.scan_parquet([str(p) for p in files], hive_partitioning=True)
    try:
        names = lf.collect_schema().names()
    except Exception:
        return None
    lot_col = cols.get("lot") or ""
    if "root_lot_id" not in names or not lot_col or lot_col not in names:
        return None
    wafer_col = cols.get("wafer") or ""
    step_col = cols.get("step") or ""
    func_col = cols.get("func") or ""
    time_col = cols.get("time") or ""
    has_time = bool(time_col and time_col in names)
    sel = [
        pl.col("root_lot_id").cast(_STR, strict=False).str.strip_chars().str.to_uppercase().alias("root_lot_id"),
        (pl.col(wafer_col).cast(_STR, strict=False).str.strip_chars() if wafer_col and wafer_col in names else pl.lit("")).alias("wafer_id"),
        pl.col(lot_col).cast(_STR, strict=False).str.strip_chars().alias("lot_id"),
        (pl.col(step_col).cast(_STR, strict=False) if step_col and step_col in names else pl.lit("")).alias("step_id"),
        (pl.col(func_col).cast(_STR, strict=False) if func_col and func_col in names else pl.lit("")).alias("function_step"),
        (pl.col(time_col).cast(_STR, strict=False) if has_time else pl.lit("")).alias("tkout_time"),
    ]
    q = lf.select(sel).filter(
        pl.col("root_lot_id").is_not_null() & (pl.col("root_lot_id") != "")
        & pl.col("lot_id").is_not_null() & (pl.col("lot_id") != "")
    )
    # 시간 컬럼이 있으면 최신 tkout 기준, 없으면 lot_id 사전순 최대를 '최신'으로 본다.
    order_key = "tkout_time" if has_time else "lot_id"
    # group 이 (root_lot_id, wafer_id) 라 그룹당 행수가 작다 → 전역 정렬보다 메모리 안전.
    q = q.group_by(["root_lot_id", "wafer_id"]).agg([
        pl.col("lot_id").sort_by(order_key, nulls_last=True).last().alias("lot_id"),
        pl.col("step_id").sort_by(order_key, nulls_last=True).last().alias("step_id"),
        pl.col("function_step").sort_by(order_key, nulls_last=True).last().alias("function_step"),
        pl.col("tkout_time").sort_by(order_key, nulls_last=True).last().alias("tkout_time"),
    ])
    try:
        df = _collect_low_memory(q)
    except Exception as exc:
        logger.warning("latest-lot batch collect 실패 dir=%s roots=%d: %s", cache_dir, len(roots), exc)
        return None
    if df.is_empty():
        return None
    return df.with_columns(pl.lit(product_label).alias("product"))


def build_latest_lot_by_root_wafer(
    *, files: list[Path] | None = None, force_source_build: bool = False
) -> dict[str, Any]:
    """root_lot_id 파티션을 배치로 순회해 latest-lot-by-root/wafer 캐시를 만든다.

    기존 방식은 ML_TABLE 전체를 한 번에 메모리로 로드해 root 별 최신 lot 을 뽑느라
    큰 제품에서 OOM 이 났다. lookup 캐시는 이미 root_lot_id 로 hive 파티셔닝돼
    있으므로, 파티션을 작은 root 배치로만 스캔하고 (root_lot_id, wafer_id) 당 최신
    lot_id 한 행만 남긴다. 피크 메모리는 ML_TABLE 전체가 아니라 한 배치(수백 root)
    뿐이고, 누적되는 것은 결과 크기(root+wafer 당 1행)뿐이다.
    """
    started = time.monotonic()
    cache_updated_at = _utc_now()
    sources = [Path(f) for f in files] if files else _discover_ml_table_files()
    batch = _latest_lot_root_batch_size()
    frames: list[pl.DataFrame] = []
    processed: list[str] = []
    skipped: list[dict[str, Any]] = []
    total_roots = 0
    for raw_fp in sources:
        fp = Path(raw_fp)
        status = cache_status(fp)
        if not status.get("has_cache") or (force_source_build and status.get("source_stale")):
            try:
                build_lookup_cache(fp, force=bool(force_source_build))
            except Exception as exc:
                skipped.append({"source": str(fp), "reason": f"lookup_build_failed: {type(exc).__name__}: {exc}"})
                continue
        cdir = cache_dir_for(fp)
        roots = _root_lot_ids_from_cache_dir(cdir)
        if not roots:
            skipped.append({"source": str(fp), "reason": "no_partitions"})
            continue
        first_files = _partition_files(cdir, roots[0])
        if not first_files:
            skipped.append({"source": str(fp), "reason": "empty_partition"})
            continue
        try:
            names = pl.scan_parquet([str(p) for p in first_files], hive_partitioning=True).collect_schema().names()
        except Exception as exc:
            skipped.append({"source": str(fp), "reason": f"schema_failed: {type(exc).__name__}"})
            continue
        cols = {
            "wafer": _ci_col(names, "wafer_id", "wf_id", "wafer"),
            "lot": _ci_col(names, "lot_id", "fab_lot_id", "lot"),
            "step": _ci_col(names, "step_id", "step"),
            "func": _ci_col(names, "function_step", "func_step"),
            "time": _ci_col(names, *_LATEST_LOT_TIME_COL_CANDIDATES),
        }
        if not cols["lot"]:
            skipped.append({"source": str(fp), "reason": "missing_lot_id"})
            continue
        product_label = fp.stem
        for i in range(0, len(roots), batch):
            if process_memory_high():
                _wait_for_lookup_cache_memory(fp)
            df = _latest_lot_rows_for_root_batch(cdir, roots[i:i + batch], product_label, cols)
            if df is not None and not df.is_empty():
                frames.append(df)
        processed.append(str(fp))
        total_roots += len(roots)
    out_fp = _latest_lot_by_root_wafer_path()
    out_fp.parent.mkdir(parents=True, exist_ok=True)
    if frames:
        combined = pl.concat(frames, how="diagonal_relaxed").with_columns(
            pl.lit(cache_updated_at).alias("update_time")
        )
        combined = (
            combined.sort("tkout_time", descending=True, nulls_last=True)
            .unique(subset=["product", "root_lot_id", "wafer_id"], keep="first", maintain_order=True)
            .sort(["product", "root_lot_id", "wafer_id"])
            .select(LATEST_LOT_BY_ROOT_WAFER_COLUMNS)
        )
    else:
        combined = pl.DataFrame({c: [] for c in LATEST_LOT_BY_ROOT_WAFER_COLUMNS})
    tmp = out_fp.with_suffix(out_fp.suffix + ".tmp")
    try:
        tmp.unlink(missing_ok=True)
    except Exception:
        pass
    combined.write_parquet(tmp)
    tmp.replace(out_fp)
    return {
        "ok": True,
        "path": str(out_fp),
        "row_count": int(combined.height),
        "sources": processed,
        "skipped": skipped,
        "root_lot_id_count": total_roots,
        "cache_updated_at": cache_updated_at,
        "build_seconds": round(time.monotonic() - started, 3),
    }


_LATEST_LOT_CACHE_REFRESH_LOCK = threading.Lock()
_LATEST_LOT_CACHE_LAST_BUILD_MONO = 0.0


def _latest_lot_cache_refresh_throttle_sec() -> float:
    return _env_float("FLOW_ML_TABLE_LATEST_LOT_REFRESH_THROTTLE_SEC", 600.0, 0.0, 86400.0)


def refresh_latest_lot_cache_after_build(fp: Path | None = None, *, force: bool = False) -> dict[str, Any]:
    """lookup 캐시 빌드 완료 훅 — 카노니컬 latest-lot 캐시를 메모리 안전하게 재생성.

    버스트 빌드(여러 ML_TABLE 이 연달아 빌드)에서 매번 전체를 재생성하지 않도록
    throttle 하고, 다른 스레드가 재생성 중이면 스킵한다. lookup 캐시가 방금
    빌드됐으므로 여기서는 소스 재빌드 없이 파티션만 순회한다.
    """
    global _LATEST_LOT_CACHE_LAST_BUILD_MONO
    if not _LATEST_LOT_CACHE_REFRESH_LOCK.acquire(blocking=False):
        return {"ok": True, "skipped": True, "reason": "another_refresh_running"}
    try:
        throttle = _latest_lot_cache_refresh_throttle_sec()
        now = time.monotonic()
        if (
            not force
            and throttle > 0
            and _LATEST_LOT_CACHE_LAST_BUILD_MONO
            and (now - _LATEST_LOT_CACHE_LAST_BUILD_MONO) < throttle
        ):
            return {"ok": True, "skipped": True, "reason": "throttled"}
        result = build_latest_lot_by_root_wafer()
        _LATEST_LOT_CACHE_LAST_BUILD_MONO = time.monotonic()
        return result
    except Exception as exc:
        logger.warning("latest-lot 캐시 재생성 실패: %s", exc, exc_info=True)
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
    finally:
        _LATEST_LOT_CACHE_REFRESH_LOCK.release()


def _lookup_cache_memory_wait_seconds() -> float:
    return _env_float("FLOW_ML_TABLE_LOOKUP_CACHE_MEMORY_WAIT_SECONDS", LOOKUP_CACHE_MEMORY_WAIT_SECONDS_DEFAULT, 0.0, 300.0)


def _lookup_cache_partition_max_rows() -> int:
    return _env_int(
        "FLOW_ML_TABLE_LOOKUP_CACHE_MAX_ROWS_PER_FILE",
        LOOKUP_CACHE_PARTITION_MAX_ROWS_DEFAULT,
        10_000,
        2_000_000,
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
        _sink_lookup_cache_partitions(lf, tmp_dir)
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
            if cache_status(fp).get("status") != "fresh":
                if not _wait_for_lookup_cache_memory(fp):
                    with _BUILD_LOCK:
                        _BUILD_STATE["last_error"] = "memory_wait_timeout"
                        _BUILD_STATE["last_source"] = str(fp.resolve())
                        _BUILD_STATE["finished_at"] = _utc_now()
                    continue
            build_lookup_cache(fp, force=False)
            with _BUILD_LOCK:
                _BUILD_STATE["last_source"] = str(fp.resolve())
                _BUILD_STATE["finished_at"] = _utc_now()
            # 빌드 완료 → 소비 캐시(SplitTable view payload) 무효화 훅 실행.
            # 이렇게 해야 stale 파티션으로 렌더한 payload 가 fresh 데이터로 수렴.
            _run_build_complete_hooks(fp)
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


def scan_root_lot_cache(fp: Path, root_lot_id: str, wafer_ids: str = "", *, allow_stale: bool = False) -> tuple[pl.LazyFrame | None, dict[str, Any]]:
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
        return None, status
    lf = _root_ram_cache_get(fp, root, files, status)
    if lf is None:
        lf = _root_ram_cache_put(fp, root, files, status)
    if lf is None:
        lf = pl.scan_parquet([str(p) for p in files], hive_partitioning=True)
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
