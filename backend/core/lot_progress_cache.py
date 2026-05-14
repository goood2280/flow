"""LOT_WF current FAB progress cache.

The cache is intentionally file-backed so SplitTable, Inform, Tracker, and
agents can read the same current-lot position without rescanning FAB parquet
for every UI request.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import logging
import os
import re
import socket
import threading
from pathlib import Path
from typing import Iterable

from core.paths import PATHS

logger = logging.getLogger("flow.lot_progress_cache")

LOT_PROGRESS_DEFAULT_SOURCE_ROOTS = ("1.RAWDATA_DB", "FAB", "1.RAWDATA_DB_FAB")
CACHE_VERSION = 1
CACHE_REFRESH_MINUTES_DEFAULT = 30
CACHE_REFRESH_MINUTES_MIN = 1
CACHE_REFRESH_MINUTES_MAX = 1440
SOURCE_ROOT_SETTING_KEY = "lot_progress_source_root"
COLUMN_MAPPING_SETTING_KEY = "lot_progress_column_mapping"
LOT_PROGRESS_CANONICAL_COLUMNS = (
    "root_lot_id", "lot_id", "wafer_id", "step_id", "process_id",
    "tkin_time", "tkout_time", "time", "update_time", "eqp_id", "chamber_id", "ppid",
)
DEFAULT_LOT_PROGRESS_COLUMN_MAPPING = {col: col for col in LOT_PROGRESS_CANONICAL_COLUMNS}
STEP_MAPPING_FILENAMES = (
    "Vehicle_matching.csv",
    "vehicle_matching.csv",
    "step_matching.csv",
    "matching_step.csv",
    "step_function.csv",
)

_CACHE_LOCK = threading.Lock()
_CACHE_STATE: dict | None = None
_CACHE_STARTED = False
_CACHE_STOP = threading.Event()
_CACHE_THREAD: threading.Thread | None = None
_CACHE_RUNNING = False
_CACHE_LAST_SKIPPED_BY_LOCK = False


def _cache_dir() -> Path:
    path = PATHS.cache_dir / "lot_progress"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_file() -> Path:
    return _cache_dir() / "lot_wf_current.json"


def cache_parquet_file() -> Path:
    return _cache_dir() / "lot_wf_current.parquet"


def filebrowser_cache_parquet_file() -> Path:
    fp = PATHS.db_cache_dir / "lot_progress_latest_lot_by_root_wafer.parquet"
    fp.parent.mkdir(parents=True, exist_ok=True)
    return fp


def metadata() -> dict:
    """Static operational metadata for the FileBrowser LOT progress cache."""
    column_mapping = lot_progress_column_mapping()
    return {
        "product_binding": {
            "rule": "product는 FAB DB root 바로 아래 제품 폴더명으로 고정합니다.",
            "example_path_shape": "<db_root>/<effective_db_root>/<product>/.../*.parquet",
            "source_column": "product_dir.name",
            "code_location": "backend/core/lot_progress_cache.py product folder rule",
        },
        "latest_key_columns": ["product", "LOT_WF(root_lot_id + wafer_id)"],
        "latest_order_columns": ["update_time", "tkout_time", "tkin_time", "time"],
        "lot_id_source_column": column_mapping.get("lot_id", "lot_id"),
        "root_lot_id_source_column": column_mapping.get("root_lot_id", "root_lot_id"),
        "wafer_id_source_column": f"{column_mapping.get('wafer_id', 'wafer_id')} (normalized, e.g. W01/#01 -> 1)",
        "column_mapping_setting": f"settings.json.{COLUMN_MAPPING_SETTING_KEY}",
        "column_mapping": column_mapping,
        "column_mapping_defaults": dict(DEFAULT_LOT_PROGRESS_COLUMN_MAPPING),
        "step_mapping_sources": list(STEP_MAPPING_FILENAMES),
        "manual_change_points": {
            "db_root": "settings.json.lot_progress_source_root",
            "column_mapping": f"settings.json.{COLUMN_MAPPING_SETTING_KEY}",
            "product_binding": "backend/core/lot_progress_cache.py product folder rule",
            "latest_rule": "backend/core/lot_progress_cache.py _sort_time and latest key creation",
            "step_mapping": "root-level matching CSV files",
        },
    }


def lot_status_cache_file() -> Path:
    fp = PATHS.data_root / "tracker" / "lot_status_cache.json"
    fp.parent.mkdir(parents=True, exist_ok=True)
    return fp


def refresh_lock_file() -> Path:
    fp = PATHS.data_root / "locks" / "lot_progress_cache.lock"
    fp.parent.mkdir(parents=True, exist_ok=True)
    return fp


def refresh_log_file() -> Path:
    fp = PATHS.data_root / "logs" / "lot_progress_cache_refresh.jsonl"
    fp.parent.mkdir(parents=True, exist_ok=True)
    return fp


def _now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _append_refresh_log(entry: dict) -> None:
    try:
        fp = refresh_log_file()
        row = {
            "ts": _now_iso(),
            "pid": os.getpid(),
            "host": socket.gethostname(),
            **(entry or {}),
        }
        with fp.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("LOT progress refresh log write failed: %s", exc)


def _read_refresh_log(limit: int = 500) -> list[dict]:
    fp = refresh_log_file()
    if not fp.is_file():
        return []
    try:
        lines = fp.read_text(encoding="utf-8").splitlines()[-max(1, int(limit)):]
    except Exception:
        return []
    out: list[dict] = []
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _latest_refresh_log(status: str | None = None) -> dict | None:
    for row in reversed(_read_refresh_log()):
        if status is None or str(row.get("status") or "") == status:
            return row
    return None


def _parse_iso_seconds(value: str) -> dt.datetime | None:
    text = _safe_text(value)
    if not text:
        return None
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _try_acquire_refresh_lock():
    fh = refresh_lock_file().open("a+", encoding="utf-8")
    try:
        import fcntl  # type: ignore
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            fh.seek(0)
            payload = fh.read(1000)
            fh.close()
            return None, payload
    except ImportError:
        pass
    fh.seek(0)
    fh.truncate()
    fh.write(json.dumps({
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "started_at": _now_iso(),
    }, ensure_ascii=False))
    fh.flush()
    try:
        os.fsync(fh.fileno())
    except Exception:
        pass
    return fh, ""


def _release_refresh_lock(fh) -> None:
    try:
        import fcntl  # type: ignore
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        fh.close()
    except Exception:
        pass


def _lock_state() -> dict:
    fp = refresh_lock_file()
    if _CACHE_RUNNING:
        return {"locked": True, "running": True, "path": str(fp), "owner": ""}
    owner = ""
    locked = False
    if fp.is_file():
        try:
            with fp.open("a+", encoding="utf-8") as fh:
                fh.seek(0)
                owner = fh.read(1000)
                try:
                    import fcntl  # type: ignore
                    try:
                        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                    except BlockingIOError:
                        locked = True
                except ImportError:
                    locked = False
        except Exception:
            locked = False
    return {"locked": locked, "running": locked, "path": str(fp), "owner": owner}


def _freshness_state(last_success_at: str, *, running: bool = False, error: bool = False) -> str:
    if running:
        return "running"
    parsed = _parse_iso_seconds(last_success_at)
    if parsed is None:
        return "error" if error else "never"
    age = (dt.datetime.now() - parsed).total_seconds()
    return "ok" if age <= 6 * 3600 else "stale"


def _state_with_runtime(state: dict | None, *, skipped_by_lock: bool = False, error: bool = False) -> dict:
    out = dict(state or {})
    latest_attempt = _latest_refresh_log()
    latest_success = _latest_refresh_log("success")
    last_attempt_at = _safe_text((latest_attempt or {}).get("ts") or (latest_attempt or {}).get("started_at"))
    last_success_at = _safe_text((latest_success or {}).get("ts") or (latest_success or {}).get("generated_at"))
    if not last_success_at:
        last_success_at = _safe_text(out.get("generated_at"))
    lock_state = _lock_state()
    running = bool(_CACHE_RUNNING or lock_state.get("running"))
    out.update({
        "last_success_at": last_success_at,
        "last_attempt_at": last_attempt_at,
        "freshness_state": _freshness_state(last_success_at, running=running, error=error),
        "refresh_log_path": str(refresh_log_file()),
        "lock_state": lock_state,
        "running": running,
        "skipped_by_lock": bool(skipped_by_lock or _CACHE_LAST_SKIPPED_BY_LOCK),
        "row_count": int(out.get("count") or len(out.get("items") or []) or 0),
    })
    return out


def lot_progress_cache_refresh_minutes() -> int:
    settings_path = PATHS.data_root / "settings.json"
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.is_file() else {}
    except Exception:
        data = {}
    if isinstance(data, dict):
        raw = data.get(
            "lot_progress_refresh_minutes",
            data.get("splittable_match_refresh_minutes", CACHE_REFRESH_MINUTES_DEFAULT),
        )
    else:
        raw = CACHE_REFRESH_MINUTES_DEFAULT
    try:
        value = int(raw)
    except Exception:
        value = CACHE_REFRESH_MINUTES_DEFAULT
    return max(CACHE_REFRESH_MINUTES_MIN, min(CACHE_REFRESH_MINUTES_MAX, value))


def lot_progress_cache_refresh_seconds() -> int:
    return max(60, lot_progress_cache_refresh_minutes() * 60)


def lot_progress_cache_source_root() -> str:
    settings_path = PATHS.data_root / "settings.json"
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.is_file() else {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        return ""
    raw = data.get(SOURCE_ROOT_SETTING_KEY, "")
    return _clean_source_root_hint(raw)


def _clean_column_mapping_name(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"[\x00\r\n\t]+", " ", text)
    return text[:120].strip()


def normalize_lot_progress_column_mapping(value=None) -> dict[str, str]:
    mapping = dict(DEFAULT_LOT_PROGRESS_COLUMN_MAPPING)
    if not isinstance(value, dict):
        return mapping
    for canonical in LOT_PROGRESS_CANONICAL_COLUMNS:
        raw = value.get(canonical)
        clean = _clean_column_mapping_name(raw)
        if clean:
            mapping[canonical] = clean
    return mapping


def lot_progress_column_mapping() -> dict[str, str]:
    settings_path = PATHS.data_root / "settings.json"
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.is_file() else {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        return dict(DEFAULT_LOT_PROGRESS_COLUMN_MAPPING)
    return normalize_lot_progress_column_mapping(data.get(COLUMN_MAPPING_SETTING_KEY))


def _safe_text(value) -> str:
    if value is None:
        return ""
    try:
        text = str(value)
    except Exception:
        return ""
    if text.lower() in {"nan", "nat", "none", "null"}:
        return ""
    return text.strip()


def _norm_key(value) -> str:
    return _safe_text(value).upper()


def _norm_wafer(value) -> str:
    text = _safe_text(value).upper()
    if not text:
        return ""
    core = re.sub(r"^(?:#|WAFER|WF|W)\s*", "", text, flags=re.I).strip()
    try:
        number = float(core)
    except Exception:
        return text
    if number.is_integer():
        return str(int(number))
    return text


def _sort_time(row: dict) -> str:
    return _safe_text(row.get("update_time") or row.get("tkout_time") or row.get("tkin_time") or row.get("time"))


def _lot_status_time(row: dict) -> str:
    return _safe_text(row.get("update_time") or row.get("time") or row.get("last_checked_at") or row.get("last_move_at") or row.get("tkout_time") or row.get("tkin_time"))


def _lot_status_key(row: dict) -> tuple[str, str, str]:
    return (
        _norm_key(row.get("root_lot_id")),
        _norm_key(row.get("lot_id")),
        _norm_key(row.get("wafer_id")),
    )


def _tracker_status_row(row: dict, *, source: str = "tracker") -> dict | None:
    if not isinstance(row, dict):
        return None
    lot_id = _safe_text(row.get("lot_id") or row.get("fab_lot_id") or row.get("root_lot_id"))
    if not lot_id:
        return None
    wafer_id = _norm_wafer(row.get("wafer_id"))
    if not wafer_id:
        return None
    step_id = _safe_text(
        row.get("step_id")
        or row.get("current_step")
        or row.get("current_step_id")
    )
    function_step = _safe_text(
        row.get("function_step")
        or row.get("current_function_step")
        or row.get("func_step")
        or row.get("et_last_function_step")
        or ""
    )
    time_value = _lot_status_time(row)
    return {
        "root_lot_id": _safe_text(row.get("root_lot_id")),
        "wafer_id": wafer_id,
        "lot_id": lot_id,
        "step_id": step_id,
        "func_step": function_step,
        "update_time": time_value,
    }


def _load_tracker_lot_status_state() -> dict:
    fp = lot_status_cache_file()
    if not fp.is_file():
        return {"version": CACHE_VERSION, "generated_at": "", "items": [], "count": 0}
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return {"version": CACHE_VERSION, "generated_at": "", "items": [], "count": 0}


def upsert_tracker_lot_status_rows(rows: list[dict], source: str = "tracker") -> dict:
    fp = lot_status_cache_file()
    state = _load_tracker_lot_status_state()
    merged: dict[tuple[str, str, str], dict] = {}
    for row in state.get("items") or []:
        row_norm = _tracker_status_row(dict(row), source="tracker")
        if row_norm is None:
            continue
        merged[_lot_status_key(row_norm)] = row_norm
    for row in rows or []:
        row_norm = _tracker_status_row(dict(row), source=source)
        if row_norm is None:
            continue
        key = _lot_status_key(row_norm)
        current = merged.get(key)
        if current is None or _lot_status_time(row_norm) >= _lot_status_time(current):
            merged[key] = row_norm
    out = sorted(
        merged.values(),
        key=lambda row: (_lot_status_time(row), _norm_key(row.get("root_lot_id")), _norm_key(row.get("lot_id")), _wafer_sort_value(row.get("wafer_id"))),
        reverse=True,
    )
    state = {
        "version": CACHE_VERSION,
        "generated_at": _now_iso(),
        "count": len(out),
        "items": out,
    }
    tmp = fp.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(fp)
    return state


def _save_tracker_lot_status_cache(rows: list[dict], source: str = "tracker") -> dict:
    return upsert_tracker_lot_status_rows(rows, source=source)


def _wafer_sort_value(value) -> int:
    try:
        return int(_norm_wafer(value) or 0)
    except Exception:
        return 999999


def compress_wafer_ids(values) -> str:
    numeric: list[int] = []
    labels: list[str] = []
    seen_labels: set[str] = set()
    for value in values or []:
        wafer = _norm_wafer(value)
        if not wafer:
            continue
        if re.fullmatch(r"\d+", wafer):
            numeric.append(int(wafer))
            continue
        key = wafer.upper()
        if key not in seen_labels:
            seen_labels.add(key)
            labels.append(wafer)
    numbers = sorted(set(numeric))
    parts: list[str] = []
    idx = 0
    while idx < len(numbers):
        start = numbers[idx]
        end = start
        while idx + 1 < len(numbers) and numbers[idx + 1] == end + 1:
            idx += 1
            end = numbers[idx]
        parts.append(str(start) if start == end else f"{start}~{end}")
        idx += 1
    parts.extend(labels)
    return f"#{','.join(parts)}" if parts else ""


def _lot_progress_parquet_rows(state: dict) -> list[dict]:
    generated_at = _safe_text((state or {}).get("generated_at"))
    rows: list[dict] = []
    for item in (state or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        lot_id = _safe_text(item.get("lot_id"))
        function_step = _safe_text(item.get("function_step") or item.get("func_step"))
        rows.append({
            "product": _safe_text(item.get("product")),
            "root_lot_id": _safe_text(item.get("root_lot_id")),
            "wafer_id": _norm_wafer(item.get("wafer_id")),
            "lot_id": lot_id,
            "step_id": _safe_text(item.get("step_id")),
            "function_step": function_step,
            "tkout_time": _safe_text(item.get("tkout_time")),
            "update_time": generated_at,
        })
    rows.sort(key=lambda row: (_norm_key(row.get("product")), _norm_key(row.get("root_lot_id")), _wafer_sort_value(row.get("wafer_id"))))
    return rows


def _write_lot_progress_parquet(target: Path, rows: list[dict]) -> None:
    import polars as pl  # type: ignore

    columns = [
        "product", "root_lot_id", "wafer_id", "lot_id",
        "step_id", "function_step", "tkout_time", "update_time",
    ]
    target.parent.mkdir(parents=True, exist_ok=True)
    if rows:
        df = pl.DataFrame(rows).select(columns)
    else:
        df = pl.DataFrame({col: [] for col in columns})
    tmp = target.with_suffix(target.suffix + ".tmp")
    df.write_parquet(tmp)
    tmp.replace(target)


def export_lot_progress_parquet(state: dict | None = None) -> dict:
    """Export the JSON LOT_WF cache as viewable parquet files."""
    if state is None:
        fp = cache_file()
        if fp.is_file():
            try:
                state = json.loads(fp.read_text(encoding="utf-8"))
            except Exception:
                state = None
        if not isinstance(state, dict):
            state = load_lot_progress_cache()
    rows = _lot_progress_parquet_rows(state or {})
    paths = [filebrowser_cache_parquet_file()]
    written: list[str] = []
    for target in paths:
        _write_lot_progress_parquet(target, rows)
        written.append(str(target))
    return {"ok": True, "rows": len(rows), "paths": written}


def _step_matching_paths() -> list[Path]:
    roots = []
    for root in (PATHS.db_root, PATHS.base_root, PATHS.data_root / "Fab"):
        try:
            p = Path(root)
        except Exception:
            continue
        if p not in roots:
            roots.append(p)
    out: list[Path] = []
    for root in roots:
        for name in STEP_MAPPING_FILENAMES:
            path = root / name
            if path not in out:
                out.append(path)
    return out


def _row_ci(row: dict, *names: str):
    lookup = {str(k or "").strip().lower(): v for k, v in (row or {}).items()}
    for name in names:
        key = str(name or "").strip().lower()
        if key in lookup:
            return lookup.get(key)
    return ""


def load_step_matching() -> tuple[dict[tuple[str, str], str], dict[str, str]]:
    by_product: dict[tuple[str, str], str] = {}
    by_step: dict[str, str] = {}
    for path in _step_matching_paths():
        if not path.is_file():
            continue
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    product = _norm_key(_row_ci(row, "product", "process_id", "prod"))
                    step_id = _norm_key(_row_ci(row, "step_id", "raw_step_id", "step"))
                    function_step = _safe_text(
                        _row_ci(row, "func_step", "function_step", "func step", "canonical_step", "step_function")
                    )
                    if not step_id or not function_step:
                        continue
                    if product:
                        by_product[(product, step_id)] = function_step
                    by_step.setdefault(step_id, function_step)
        except Exception as exc:
            logger.warning("step matching load failed: %s (%s)", path, exc)
    return by_product, by_step


_FAB_PROGRESS_COLUMNS = list(LOT_PROGRESS_CANONICAL_COLUMNS)


def _mapped_fab_progress_columns(column_mapping: dict | None = None) -> list[str]:
    mapping = normalize_lot_progress_column_mapping(column_mapping)
    out: list[str] = []
    seen: set[str] = set()
    for canonical in _FAB_PROGRESS_COLUMNS:
        source = _clean_column_mapping_name(mapping.get(canonical) or canonical)
        if source and source not in seen:
            seen.add(source)
            out.append(source)
    return out


def _available_fab_progress_columns(path: Path, column_mapping: dict | None = None) -> list[str]:
    mapped_columns = _mapped_fab_progress_columns(column_mapping)
    try:
        import polars as pl  # type: ignore
        schema = pl.read_parquet_schema(str(path))
        names = set(schema.keys() if hasattr(schema, "keys") else schema)
        return [col for col in mapped_columns if col in names]
    except Exception:
        pass
    try:
        import pyarrow.parquet as pq  # type: ignore
        names = set(pq.ParquetFile(str(path)).schema.names)
        return [col for col in mapped_columns if col in names]
    except Exception:
        return mapped_columns


def _fill_missing_progress_columns(row: dict, column_mapping: dict | None = None) -> dict:
    mapping = normalize_lot_progress_column_mapping(column_mapping)
    raw = dict(row or {})
    lower_lookup = {str(key).casefold(): value for key, value in raw.items()}
    out: dict = {}
    for canonical in _FAB_PROGRESS_COLUMNS:
        source = mapping.get(canonical) or canonical
        if source in raw:
            out[canonical] = raw.get(source)
        else:
            out[canonical] = lower_lookup.get(str(source).casefold())
    return out


def _read_parquet_rows(path: Path, column_mapping: dict | None = None) -> Iterable[dict]:
    mapping = normalize_lot_progress_column_mapping(column_mapping)
    columns = _available_fab_progress_columns(path, mapping)
    if not columns:
        return
    try:
        import polars as pl  # type: ignore
        df = pl.read_parquet(str(path), columns=columns)
        for row in df.iter_rows(named=True):
            yield _fill_missing_progress_columns(row, mapping)
        return
    except Exception:
        pass
    try:
        import pandas as pd  # type: ignore
        df = pd.read_parquet(str(path), columns=columns)
        for row in df.to_dict(orient="records"):
            yield _fill_missing_progress_columns(row, mapping)
        return
    except Exception:
        pass
    try:
        import pyarrow.parquet as pq  # type: ignore
        table = pq.read_table(str(path), columns=columns)
        for row in table.to_pylist():
            yield _fill_missing_progress_columns(row, mapping)
    except Exception as exc:
        logger.warning("FAB parquet read failed: %s (%s)", path, exc)


def _fab_product_dirs(fab_root: Path) -> Iterable[Path]:
    if not fab_root.is_dir():
        return []
    try:
        return [p for p in fab_root.iterdir() if p.is_dir()]
    except Exception:
        return []


def _path_key(path: Path) -> str:
    try:
        return str(path.resolve())
    except Exception:
        return str(path)


def _clean_source_root_hint(value: str = "") -> str:
    text = _safe_text(value).strip().strip("/\\")
    if not text:
        return ""
    if text.casefold() in {"auto", "default", "자동"}:
        return ""
    path = Path(text)
    if path.is_absolute():
        return str(path)
    parts = [part for part in re.split(r"[\\/]+", text) if part and part not in {".", ".."}]
    if not parts:
        return ""
    first = parts[0]
    first_upper = first.upper()
    if first_upper.startswith("1.RAWDATA_DB") or first_upper == "FAB":
        return first
    return text


def normalize_lot_progress_source_root(value: str = "") -> str:
    return _clean_source_root_hint(value)


def _candidate_source_root_path(db_root: Path, root_name: str) -> Path | None:
    name = _clean_source_root_hint(root_name)
    if not name:
        return None
    path = Path(name)
    if path.is_absolute():
        return path
    try:
        from app_v2.shared.source_adapter import resolve_named_child
        if "/" not in name and "\\" not in name:
            resolved = resolve_named_child(db_root, name)
            if resolved is not None and _path_key(resolved) != _path_key(db_root):
                return resolved
    except Exception:
        pass
    return db_root / name


def _resolve_fab_root(db_root: Path, root_name: str) -> Path | None:
    path = _candidate_source_root_path(db_root, root_name)
    return path if path is not None and path.is_dir() else None


def _source_root_name(path: Path, db_root: Path) -> str:
    try:
        rel = path.relative_to(db_root)
        if rel.parts:
            return "/".join(rel.parts)
    except Exception:
        pass
    return path.name or str(path)


def _has_product_parquet_dirs(root: Path) -> bool:
    for product_dir in _fab_product_dirs(root):
        try:
            next(product_dir.rglob("*.parquet"))
            return True
        except StopIteration:
            continue
        except Exception:
            continue
    return False


def lot_progress_source_root_candidates(db_root: Path | None = None, source_root: str = "") -> list[dict]:
    db_root = db_root or PATHS.db_root
    candidates: list[dict] = []
    seen: set[str] = set()

    def _add(path: Path | None, name: str, origin: str) -> None:
        if path is None:
            return
        exists = path.is_dir()
        key = _path_key(path) if exists else str(path).casefold()
        if key in seen:
            return
        seen.add(key)
        candidates.append({
            "source_root": _source_root_name(path, db_root) if exists else name,
            "path": str(path),
            "exists": bool(exists),
            "origin": origin,
        })

    hint = _clean_source_root_hint(source_root)
    if hint:
        _add(_candidate_source_root_path(db_root, hint), hint, "configured")
    for name in LOT_PROGRESS_DEFAULT_SOURCE_ROOTS:
        _add(_candidate_source_root_path(db_root, name), name, "auto")
    db_name = db_root.name
    db_upper = db_name.upper()
    if db_upper.startswith("1.RAWDATA_DB"):
        _add(db_root, db_name, "db_root")
    if not any(candidate.get("exists") for candidate in candidates) and _has_product_parquet_dirs(db_root):
        _add(db_root, db_name or str(db_root), "db_root")
    return candidates


def _fab_source_roots(db_root: Path, source_root: str = "") -> list[dict]:
    roots: list[dict] = []
    seen: set[str] = set()

    def _add(path: Path | None) -> None:
        if path is None or not path.is_dir():
            return
        key = _path_key(path)
        if key in seen:
            return
        seen.add(key)
        roots.append({"path": path, "source_root": _source_root_name(path, db_root)})

    hint = _clean_source_root_hint(source_root)
    if hint:
        _add(_resolve_fab_root(db_root, hint))
        return roots
    for candidate in lot_progress_source_root_candidates(db_root):
        if candidate.get("exists"):
            _add(Path(str(candidate.get("path") or "")))
    return roots


def _fab_root_names_for_error(source_root: str = "") -> list[str]:
    names: list[str] = []
    hint = _clean_source_root_hint(source_root)
    if hint:
        names.append(hint)
        return names
    for name in LOT_PROGRESS_DEFAULT_SOURCE_ROOTS:
        if name not in names:
            names.append(name)
    return names


def _source_ref_matches(left: str, right: str) -> bool:
    a = _clean_source_root_hint(left)
    b = _clean_source_root_hint(right)
    if not a or not b:
        return False
    if a.casefold() == b.casefold():
        return True
    try:
        return str(Path(a).resolve()).casefold() == str(Path(b).resolve()).casefold()
    except Exception:
        return False


def _state_source_root_matches(state: dict | None, source_root: str = "") -> bool:
    hint = _clean_source_root_hint(source_root)
    if not hint:
        if not isinstance(state, dict):
            return True
        return not _clean_source_root_hint(state.get("configured_source_root", ""))
    if not isinstance(state, dict):
        return False
    candidates: list[str] = []
    for key in ("source_root", "configured_source_root"):
        value = state.get(key)
        if value:
            candidates.append(str(value))
    for key in ("source_roots", "fab_roots"):
        for value in state.get(key) or []:
            if value:
                candidates.append(str(value))
    return any(_source_ref_matches(candidate, hint) for candidate in candidates)


def _state_column_mapping_matches(state: dict | None, column_mapping: dict | None = None) -> bool:
    expected = normalize_lot_progress_column_mapping(column_mapping)
    if not isinstance(state, dict):
        return False
    stored = state.get("column_mapping")
    if not isinstance(stored, dict):
        return expected == dict(DEFAULT_LOT_PROGRESS_COLUMN_MAPPING)
    return normalize_lot_progress_column_mapping(stored) == expected


def refresh_lot_progress_cache(force: bool = False, source_root: str = "") -> dict:
    """Rebuild the LOT_WF current-position cache from FAB parquet."""
    global _CACHE_STATE, _CACHE_RUNNING, _CACHE_LAST_SKIPPED_BY_LOCK
    source_root_hint = _clean_source_root_hint(source_root) or lot_progress_cache_source_root()
    column_mapping = lot_progress_column_mapping()
    with _CACHE_LOCK:
        cache_path = cache_file()
        max_age_seconds = lot_progress_cache_refresh_seconds()
        if (
            not force
            and _CACHE_STATE
            and _state_source_root_matches(_CACHE_STATE, source_root_hint)
            and _state_column_mapping_matches(_CACHE_STATE, column_mapping)
        ):
            generated_at = _safe_text(_CACHE_STATE.get("generated_at"))
            try:
                age = (dt.datetime.now() - dt.datetime.fromisoformat(generated_at)).total_seconds()
            except Exception:
                age = max_age_seconds + 1
            if age <= max_age_seconds:
                _CACHE_LAST_SKIPPED_BY_LOCK = False
                return _state_with_runtime(_CACHE_STATE)

        lock_fh, lock_owner = _try_acquire_refresh_lock()
        if lock_fh is None:
            _CACHE_LAST_SKIPPED_BY_LOCK = True
            _append_refresh_log({
                "status": "skipped_by_lock",
                "reason": "another refresh is running",
                "lock_owner": lock_owner,
            })
            state = _CACHE_STATE
            if state is None and cache_path.is_file():
                try:
                    loaded = json.loads(cache_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        state = loaded
                        _CACHE_STATE = loaded
                except Exception:
                    state = None
            if state is None:
                state = {
                    "version": CACHE_VERSION,
                    "generated_at": "",
                    "cache_file": str(cache_path),
                    "count": 0,
                    "files_scanned": 0,
                    "rows_seen": 0,
                    "errors": ["refresh skipped because another process holds the cache lock"],
                    "items": [],
                }
            return _state_with_runtime(state, skipped_by_lock=True)

        _CACHE_RUNNING = True
        _CACHE_LAST_SKIPPED_BY_LOCK = False
        started_at = _now_iso()
        _append_refresh_log({
            "status": "started",
            "started_at": started_at,
            "force": bool(force),
            "source_root_hint": source_root_hint,
        })
        try:
            db_root = PATHS.db_root
            fab_roots = _fab_source_roots(db_root, source_root_hint)
            step_by_product, step_by_id = load_step_matching()
            latest: dict[tuple[str, str], dict] = {}
            files_scanned = 0
            rows_seen = 0
            errors: list[str] = []

            if not fab_roots:
                tried = ", ".join(_fab_root_names_for_error(source_root_hint))
                errors.append(f"FAB rawdata root not found under {db_root}; tried {tried}")

            for root_info in fab_roots:
                fab_root = root_info["path"]
                root_name = root_info["source_root"]
                product_dirs = list(_fab_product_dirs(fab_root))
                if not product_dirs and len(errors) < 20:
                    errors.append(f"No product folders found under {fab_root}")
                for product_dir in product_dirs:
                    # Product comes from the FAB DB product folder, not from a parquet column.
                    product = product_dir.name
                    for parquet in product_dir.rglob("*.parquet"):
                        files_scanned += 1
                        try:
                            rows = _read_parquet_rows(parquet, column_mapping)
                            for raw in rows:
                                rows_seen += 1
                                root_lot_id = _safe_text(raw.get("root_lot_id"))
                                lot_id = _safe_text(raw.get("lot_id"))
                                wafer_id = _norm_wafer(raw.get("wafer_id"))
                                step_id = _safe_text(raw.get("step_id"))
                                if not (root_lot_id and wafer_id and step_id):
                                    continue
                                process_id = _safe_text(raw.get("process_id"))
                                product_key = _norm_key(product)
                                step_key = _norm_key(step_id)
                                function_step = (
                                    step_by_product.get((product_key, step_key))
                                    or step_by_product.get((_norm_key(process_id), step_key))
                                    or step_by_id.get(step_key)
                                    or ""
                                )
                                lot_wf = f"{root_lot_id}_{wafer_id}"
                                item = {
                                    "product": product,
                                    "process_id": process_id,
                                    "root_lot_id": root_lot_id,
                                    "lot_id": lot_id,
                                    "wafer_id": wafer_id,
                                    "LOT_WF": lot_wf,
                                    "lot_wf": lot_wf,
                                    "step_id": step_id,
                                    "function_step": function_step,
                                    "func_step": function_step,
                                    "tkin_time": _safe_text(raw.get("tkin_time")),
                                    "tkout_time": _safe_text(raw.get("tkout_time")),
                                    "time": _safe_text(raw.get("time") or raw.get("tkout_time") or raw.get("tkin_time")),
                                    "update_time": _safe_text(raw.get("update_time") or raw.get("tkout_time") or raw.get("tkin_time") or raw.get("time")),
                                    "eqp_id": _safe_text(raw.get("eqp_id")),
                                    "chamber_id": _safe_text(raw.get("chamber_id")),
                                    "ppid": _safe_text(raw.get("ppid")),
                                    "source_root": root_name,
                                }
                                key = (_norm_key(product), _norm_key(lot_wf))
                                prev = latest.get(key)
                                if prev is None or _sort_time(item) > _sort_time(prev):
                                    latest[key] = item
                        except Exception as exc:
                            if len(errors) < 20:
                                errors.append(f"{parquet}: {exc}")

            if fab_roots and files_scanned == 0 and len(errors) < 20:
                roots_text = ", ".join(str(root["path"]) for root in fab_roots)
                errors.append(f"No FAB parquet files found under {roots_text}")

            items = sorted(
                latest.values(),
                key=lambda row: (_norm_key(row.get("product")), _norm_key(row.get("root_lot_id")), _wafer_sort_value(row.get("wafer_id"))),
            )
            source_roots = [root["source_root"] for root in fab_roots]
            fab_root_paths = [str(root["path"]) for root in fab_roots]
            source_root_candidates = lot_progress_source_root_candidates(db_root, source_root_hint)
            state = {
                "version": CACHE_VERSION,
                "generated_at": _now_iso(),
                "db_root": str(db_root),
                "fab_root": fab_root_paths[0] if fab_root_paths else "",
                "fab_roots": fab_root_paths,
                "configured_source_root": source_root_hint,
                "source_root": source_roots[0] if source_roots else "",
                "source_roots": source_roots,
                "effective_source_roots": source_roots,
                "source_root_candidates": source_root_candidates,
                "column_mapping": column_mapping,
                "cache_file": str(cache_path),
                "count": len(items),
                "files_scanned": files_scanned,
                "rows_seen": rows_seen,
                "errors": errors,
                "items": items,
            }
            _save_tracker_lot_status_cache(items, source="lot_progress_cache")
            tmp = cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(cache_path)
            try:
                export_lot_progress_parquet(state)
            except Exception as exc:
                logger.warning("LOT_WF parquet export failed: %s", exc)
            _CACHE_STATE = state
            _append_refresh_log({
                "status": "success",
                "started_at": started_at,
                "generated_at": state.get("generated_at"),
                "files_scanned": files_scanned,
                "rows_seen": rows_seen,
                "row_count": len(items),
                "errors": len(errors),
                "source_roots": source_roots,
            })
            _CACHE_RUNNING = False
            _release_refresh_lock(lock_fh)
            lock_fh = None
            return _state_with_runtime(state)
        except Exception as exc:
            _append_refresh_log({
                "status": "failure",
                "started_at": started_at,
                "error": f"{type(exc).__name__}: {exc}",
            })
            raise
        finally:
            _CACHE_RUNNING = False
            if lock_fh is not None:
                _release_refresh_lock(lock_fh)


def load_lot_progress_cache(max_age_seconds: int | None = None) -> dict:
    """Load cache from memory/file and refresh when stale."""
    global _CACHE_STATE
    if max_age_seconds is None:
        max_age_seconds = lot_progress_cache_refresh_seconds()
    source_root_hint = lot_progress_cache_source_root()
    column_mapping = lot_progress_column_mapping()
    should_refresh = False
    with _CACHE_LOCK:
        if (
            _CACHE_STATE
            and _state_source_root_matches(_CACHE_STATE, source_root_hint)
            and _state_column_mapping_matches(_CACHE_STATE, column_mapping)
        ):
            generated_at = _safe_text(_CACHE_STATE.get("generated_at"))
            try:
                age = (dt.datetime.now() - dt.datetime.fromisoformat(generated_at)).total_seconds()
            except Exception:
                age = max_age_seconds + 1
            if age <= max_age_seconds:
                return dict(_CACHE_STATE)
        path = cache_file()
        if path.is_file():
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
                generated_at = _safe_text(state.get("generated_at"))
                age = (dt.datetime.now() - dt.datetime.fromisoformat(generated_at)).total_seconds()
                if (
                    age <= max_age_seconds
                    and _state_source_root_matches(state, source_root_hint)
                    and _state_column_mapping_matches(state, column_mapping)
                ):
                    _CACHE_STATE = state
                    return dict(state)
            except Exception:
                pass
        should_refresh = True
    if should_refresh:
        return refresh_lot_progress_cache(force=True, source_root=source_root_hint)
    return refresh_lot_progress_cache(force=True, source_root=source_root_hint)


def _matches(item: dict, *, product: str = "", lot_id: str = "", root_lot_id: str = "", wafer_id: str = "", lot_wf: str = "") -> bool:
    if product and _norm_key(item.get("product")) != _norm_key(product) and _norm_key(item.get("process_id")) != _norm_key(product):
        return False
    if lot_wf and _norm_key(item.get("lot_wf")) != _norm_key(lot_wf):
        return False
    if root_lot_id and _norm_key(item.get("root_lot_id")) != _norm_key(root_lot_id):
        return False
    if lot_id:
        needle = _norm_key(lot_id)
        if needle not in {_norm_key(item.get("lot_id")), _norm_key(item.get("root_lot_id"))}:
            return False
    if wafer_id and _norm_wafer(wafer_id) and _norm_wafer(item.get("wafer_id")) != _norm_wafer(wafer_id):
        return False
    return True


def lookup_lot_progress(
    *,
    product: str = "",
    lot_id: str = "",
    root_lot_id: str = "",
    wafer_id: str = "",
    lot_wf: str = "",
    limit: int = 50,
    max_age_seconds: int | None = None,
) -> list[dict]:
    state = load_lot_progress_cache(max_age_seconds=max_age_seconds)
    rows = [
        dict(item)
        for item in state.get("items") or []
        if isinstance(item, dict)
        and _matches(item, product=product, lot_id=lot_id, root_lot_id=root_lot_id, wafer_id=wafer_id, lot_wf=lot_wf)
    ]
    rows.sort(key=_sort_time, reverse=True)
    try:
        cap = max(1, min(int(limit), 500))
    except Exception:
        cap = 50
    return rows[:cap]


def lot_progress_summary(
    *,
    lot_id: str = "",
    root_lot_id: str = "",
    product: str = "",
    limit: int = 500,
    max_age_seconds: int | None = None,
) -> dict:
    rows = lookup_lot_progress(
        product=product,
        lot_id=lot_id,
        root_lot_id=root_lot_id,
        limit=limit,
        max_age_seconds=max_age_seconds,
    )
    rows = sorted(rows, key=lambda row: _wafer_sort_value(row.get("wafer_id")))
    lean_rows = []
    for row in rows:
        lean_rows.append({
            "product": _safe_text(row.get("product") or row.get("process_id")),
            "root_lot_id": _safe_text(row.get("root_lot_id")),
            "wafer_id": _norm_wafer(row.get("wafer_id")),
            "lot_id": _safe_text(row.get("lot_id")),
            "step_id": _safe_text(row.get("step_id")),
            "func_step": _safe_text(row.get("func_step") or row.get("function_step")),
            "update_time": _lot_status_time(row),
        })
    wafer_ids: list[str] = []
    seen_wafers: set[str] = set()
    for row in lean_rows:
        wafer = row.get("wafer_id") or ""
        if not wafer or wafer in seen_wafers:
            continue
        seen_wafers.add(wafer)
        wafer_ids.append(wafer)
    latest = sorted(lean_rows, key=lambda row: _lot_status_time(row), reverse=True)[0] if lean_rows else {}
    root_values = [r.get("root_lot_id") for r in lean_rows if r.get("root_lot_id")]
    lot_values = [r.get("lot_id") for r in lean_rows if r.get("lot_id")]
    product_values = [r.get("product") for r in lean_rows if r.get("product")]
    return {
        "ok": True,
        "lot_id": lot_values[0] if lot_values else _safe_text(lot_id),
        "root_lot_id": root_values[0] if root_values and len(set(root_values)) == 1 else "",
        "product": product_values[0] if product_values and len(set(product_values)) == 1 else "",
        "wafer_count": len(wafer_ids),
        "wafer_ids": wafer_ids,
        "wafer_label": compress_wafer_ids(wafer_ids),
        "step_id": latest.get("step_id") or "",
        "func_step": latest.get("func_step") or "",
        "update_time": latest.get("update_time") or "",
        "rows": lean_rows,
    }


def lot_id_candidates(
    *,
    product: str = "",
    prefix: str = "",
    limit: int = 200,
    max_age_seconds: int | None = None,
) -> list[dict]:
    state = load_lot_progress_cache(max_age_seconds=max_age_seconds)
    prod = _norm_key(product)
    pref = _norm_key(prefix)
    out: list[dict] = []
    seen: set[str] = set()
    rows = sorted(
        [item for item in state.get("items") or [] if isinstance(item, dict)],
        key=_sort_time,
        reverse=True,
    )
    for item in rows:
        if prod and _norm_key(item.get("product")) != prod and _norm_key(item.get("process_id")) != prod:
            continue
        lot_id = _safe_text(item.get("lot_id"))
        if not lot_id:
            continue
        key = _norm_key(lot_id)
        if pref and not key.startswith(pref):
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "value": lot_id,
            "type": "lot_id",
            "lot_id": lot_id,
            "fab_lot_id": lot_id,
            "source_root": item.get("source_root") or "",
            "product": item.get("product") or "",
            "process_id": item.get("process_id") or "",
            "root_lot_id": item.get("root_lot_id") or "",
            "step_id": item.get("step_id") or "",
            "function_step": item.get("function_step") or item.get("func_step") or "",
            "time": item.get("update_time") or item.get("time") or item.get("tkout_time") or item.get("tkin_time") or "",
            "cache": "lot_progress",
        })
        if len(out) >= limit:
            break
    return out


def lot_progress_snapshot(
    *,
    product: str = "",
    root_lot_id: str = "",
    lot_id: str = "",
    wafer_id: str = "",
    lot_wf: str = "",
    max_age_seconds: int | None = None,
) -> dict:
    rows = lookup_lot_progress(
        product=product,
        root_lot_id=root_lot_id,
        lot_id=lot_id,
        wafer_id=wafer_id,
        lot_wf=lot_wf,
        limit=1,
        max_age_seconds=max_age_seconds,
    )
    if not rows:
        return {"fab": {}, "et": [], "cache": {"hit": False}}
    row = rows[0]
    fab = {
        **row,
        "time": row.get("update_time") or row.get("time") or row.get("tkout_time") or row.get("tkin_time") or "",
        "cache_source": "lot_progress_cache",
    }
    return {"fab": fab, "et": [], "cache": {"hit": True, "generated_at": load_lot_progress_cache(max_age_seconds=max_age_seconds).get("generated_at")}}


def cache_status() -> dict:
    configured_source_root = lot_progress_cache_source_root()
    source_root_candidates = lot_progress_source_root_candidates(PATHS.db_root, configured_source_root)
    try:
        state = load_lot_progress_cache(max_age_seconds=lot_progress_cache_refresh_seconds())
    except Exception as exc:
        runtime = _state_with_runtime({}, error=True)
        return {
            "ok": False,
            "error": str(exc),
            "cache_file": str(cache_file()),
            "configured_source_root": configured_source_root,
            "source_root": "",
            "source_roots": [],
            "effective_source_roots": [],
            "source_root_candidates": source_root_candidates,
            "last_success_at": runtime.get("last_success_at", ""),
            "last_attempt_at": runtime.get("last_attempt_at", ""),
            "freshness_state": runtime.get("freshness_state", "error"),
            "refresh_log_path": runtime.get("refresh_log_path", str(refresh_log_file())),
            "lock_state": runtime.get("lock_state") or {},
            "running": bool(runtime.get("running")),
            "skipped_by_lock": bool(runtime.get("skipped_by_lock")),
            **metadata(),
        }
    runtime = _state_with_runtime(state)
    source_roots = list(state.get("source_roots") or [])
    return {
        "ok": True,
        "version": state.get("version"),
        "generated_at": state.get("generated_at"),
        "count": state.get("count", len(state.get("items") or [])),
        "row_count": runtime.get("row_count", state.get("count", len(state.get("items") or []))),
        "files_scanned": state.get("files_scanned", 0),
        "rows_seen": state.get("rows_seen", 0),
        "errors": state.get("errors") or [],
        "cache_file": str(cache_file()),
        "scheduler_started": _CACHE_STARTED,
        "interval_minutes": lot_progress_cache_refresh_minutes(),
        "interval_seconds": lot_progress_cache_refresh_seconds(),
        "configured_source_root": configured_source_root,
        "source_root": state.get("source_root") or "",
        "source_roots": source_roots,
        "effective_source_roots": list(state.get("effective_source_roots") or source_roots),
        "source_root_candidates": list(state.get("source_root_candidates") or source_root_candidates),
        "last_success_at": runtime.get("last_success_at", ""),
        "last_attempt_at": runtime.get("last_attempt_at", ""),
        "freshness_state": runtime.get("freshness_state", "never"),
        "refresh_log_path": runtime.get("refresh_log_path", str(refresh_log_file())),
        "lock_state": runtime.get("lock_state") or {},
        "running": bool(runtime.get("running")),
        "skipped_by_lock": bool(runtime.get("skipped_by_lock")),
        **metadata(),
    }


def _scheduler_loop() -> None:
    while not _CACHE_STOP.is_set():
        try:
            load_lot_progress_cache(max_age_seconds=lot_progress_cache_refresh_seconds())
        except Exception as exc:
            logger.warning("LOT progress cache refresh failed: %s", exc)
        _CACHE_STOP.wait(lot_progress_cache_refresh_seconds())


def start_lot_progress_cache_scheduler() -> bool:
    global _CACHE_STARTED, _CACHE_THREAD
    if _CACHE_STARTED:
        return False
    _CACHE_STARTED = True
    _CACHE_THREAD = threading.Thread(target=_scheduler_loop, name="lot-progress-cache", daemon=True)
    _CACHE_THREAD.start()
    return True
