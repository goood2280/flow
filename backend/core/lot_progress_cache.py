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
import re
import threading
from pathlib import Path
from typing import Iterable

from core.paths import PATHS

logger = logging.getLogger("flow.lot_progress_cache")

FAB_ROOT = "1.RAWDATA_DB_FAB"
CACHE_VERSION = 1
CACHE_REFRESH_MINUTES_DEFAULT = 30
CACHE_REFRESH_MINUTES_MIN = 1
CACHE_REFRESH_MINUTES_MAX = 1440

_CACHE_LOCK = threading.Lock()
_CACHE_STATE: dict | None = None
_CACHE_STARTED = False
_CACHE_STOP = threading.Event()
_CACHE_THREAD: threading.Thread | None = None


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


def lot_status_cache_file() -> Path:
    fp = PATHS.data_root / "tracker" / "lot_status_cache.json"
    fp.parent.mkdir(parents=True, exist_ok=True)
    return fp


def _now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def lot_progress_cache_refresh_minutes() -> int:
    settings_path = PATHS.data_root / "settings.json"
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.is_file() else {}
    except Exception:
        data = {}
    raw = data.get("splittable_match_refresh_minutes", CACHE_REFRESH_MINUTES_DEFAULT) if isinstance(data, dict) else CACHE_REFRESH_MINUTES_DEFAULT
    try:
        value = int(raw)
    except Exception:
        value = CACHE_REFRESH_MINUTES_DEFAULT
    return max(CACHE_REFRESH_MINUTES_MIN, min(CACHE_REFRESH_MINUTES_MAX, value))


def lot_progress_cache_refresh_seconds() -> int:
    return max(60, lot_progress_cache_refresh_minutes() * 60)


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
    paths = [cache_parquet_file(), filebrowser_cache_parquet_file()]
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
    names = [
        "Vehicle_matching.csv",
        "vehicle_matching.csv",
        "step_matching.csv",
        "matching_step.csv",
        "step_function.csv",
    ]
    out: list[Path] = []
    for root in roots:
        for name in names:
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


def _read_parquet_rows(path: Path) -> Iterable[dict]:
    columns = [
        "root_lot_id", "lot_id", "wafer_id", "process_id", "step_id",
        "tkin_time", "tkout_time", "eqp_id", "chamber_id", "ppid",
    ]
    try:
        import polars as pl  # type: ignore
        df = pl.read_parquet(str(path), columns=columns)
        for row in df.iter_rows(named=True):
            yield dict(row)
        return
    except Exception:
        pass
    try:
        import pandas as pd  # type: ignore
        df = pd.read_parquet(str(path), columns=columns)
        for row in df.to_dict(orient="records"):
            yield dict(row)
        return
    except Exception:
        pass
    try:
        import pyarrow.parquet as pq  # type: ignore
        table = pq.read_table(str(path), columns=columns)
        for row in table.to_pylist():
            yield dict(row)
    except Exception as exc:
        logger.warning("FAB parquet read failed: %s (%s)", path, exc)


def _fab_product_dirs(fab_root: Path) -> Iterable[Path]:
    if not fab_root.is_dir():
        return []
    try:
        return [p for p in fab_root.iterdir() if p.is_dir()]
    except Exception:
        return []


def refresh_lot_progress_cache(force: bool = False) -> dict:
    """Rebuild the LOT_WF current-position cache from FAB parquet."""
    with _CACHE_LOCK:
        global _CACHE_STATE
        cache_path = cache_file()
        max_age_seconds = lot_progress_cache_refresh_seconds()
        if not force and _CACHE_STATE:
            generated_at = _safe_text(_CACHE_STATE.get("generated_at"))
            try:
                age = (dt.datetime.now() - dt.datetime.fromisoformat(generated_at)).total_seconds()
            except Exception:
                age = max_age_seconds + 1
            if age <= max_age_seconds:
                return dict(_CACHE_STATE)

        db_root = PATHS.db_root
        fab_root = db_root / FAB_ROOT
        step_by_product, step_by_id = load_step_matching()
        latest: dict[tuple[str, str], dict] = {}
        files_scanned = 0
        rows_seen = 0
        errors: list[str] = []

        for product_dir in _fab_product_dirs(fab_root):
            product = product_dir.name
            for parquet in product_dir.rglob("*.parquet"):
                files_scanned += 1
                try:
                    rows = _read_parquet_rows(parquet)
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
                            "time": _safe_text(raw.get("tkout_time") or raw.get("tkin_time")),
                            "update_time": _safe_text(raw.get("tkout_time") or raw.get("tkin_time")),
                            "eqp_id": _safe_text(raw.get("eqp_id")),
                            "chamber_id": _safe_text(raw.get("chamber_id")),
                            "ppid": _safe_text(raw.get("ppid")),
                            "source_root": FAB_ROOT,
                        }
                        key = (_norm_key(product), _norm_key(lot_wf))
                        prev = latest.get(key)
                        if prev is None or _sort_time(item) >= _sort_time(prev):
                            latest[key] = item
                except Exception as exc:
                    if len(errors) < 20:
                        errors.append(f"{parquet}: {exc}")

        items = sorted(
            latest.values(),
            key=lambda row: (_norm_key(row.get("product")), _norm_key(row.get("root_lot_id")), _wafer_sort_value(row.get("wafer_id"))),
        )
        state = {
            "version": CACHE_VERSION,
            "generated_at": _now_iso(),
            "db_root": str(db_root),
            "fab_root": str(fab_root),
            "source_root": FAB_ROOT,
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
        return dict(state)


def load_lot_progress_cache(max_age_seconds: int | None = None) -> dict:
    """Load cache from memory/file and refresh when stale."""
    global _CACHE_STATE
    if max_age_seconds is None:
        max_age_seconds = lot_progress_cache_refresh_seconds()
    should_refresh = False
    with _CACHE_LOCK:
        if _CACHE_STATE:
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
                if age <= max_age_seconds:
                    _CACHE_STATE = state
                    return dict(state)
            except Exception:
                pass
        should_refresh = True
    if should_refresh:
        return refresh_lot_progress_cache(force=True)
    return refresh_lot_progress_cache(force=True)


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
            "source_root": item.get("source_root") or FAB_ROOT,
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
    try:
        state = load_lot_progress_cache(max_age_seconds=lot_progress_cache_refresh_seconds())
    except Exception as exc:
        return {"ok": False, "error": str(exc), "cache_file": str(cache_file())}
    return {
        "ok": True,
        "version": state.get("version"),
        "generated_at": state.get("generated_at"),
        "count": state.get("count", len(state.get("items") or [])),
        "files_scanned": state.get("files_scanned", 0),
        "rows_seen": state.get("rows_seen", 0),
        "errors": state.get("errors") or [],
        "cache_file": str(cache_file()),
        "scheduler_started": _CACHE_STARTED,
        "interval_minutes": lot_progress_cache_refresh_minutes(),
        "interval_seconds": lot_progress_cache_refresh_seconds(),
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
