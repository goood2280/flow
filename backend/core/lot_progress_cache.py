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
    return _safe_text(row.get("tkout_time") or row.get("tkin_time") or row.get("time"))


def _wafer_sort_value(value) -> int:
    try:
        return int(_norm_wafer(value) or 0)
    except Exception:
        return 999999


def _step_matching_paths() -> list[Path]:
    roots = []
    for root in (PATHS.db_root, PATHS.base_root, PATHS.data_root / "Fab"):
        try:
            p = Path(root)
        except Exception:
            continue
        if p not in roots:
            roots.append(p)
    names = ["step_matching.csv", "matching_step.csv", "step_function.csv"]
    out: list[Path] = []
    for root in roots:
        for name in names:
            path = root / name
            if path not in out:
                out.append(path)
    return out


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
                    product = _norm_key(row.get("product") or row.get("process_id") or row.get("prod"))
                    step_id = _norm_key(row.get("step_id") or row.get("step"))
                    function_step = _safe_text(
                        row.get("function_step") or row.get("func_step") or row.get("step_function")
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
        tmp = cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(cache_path)
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
            "time": item.get("time") or item.get("tkout_time") or item.get("tkin_time") or "",
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
        "time": row.get("time") or row.get("tkout_time") or row.get("tkin_time") or "",
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
