"""core/lot_step.py v1.0.0 (v8.8.33)
트래커 Lot 의 진행/측정 추적 헬퍼.

두 가지 소스:
  1. FAB history (`1.RAWDATA_DB_FAB`) — lot/wafer 의 최신 step_id (max(tkout_time))
  2. ET long     (`1.RAWDATA_DB_ET`)  — wafer 의 측정 패키지 (step_id × step_seq × flat_zone × tkout_time)

실제 공정 의미:
  - FAB: lot 단위 진행 — root_lot_id 가 5자리(standard) 면 root 기준, 그 외엔 lot_id 기준
  - ET:  shot 단위 numerical 측정 — 같은 (step_id, step_seq, flat_zone) + shot_x/shot_y 셋이 "한 측정 패키지"
  - step_seq: 같은 step_id 안에서 순서. 같은 step_seq 도 측정 pt 갯수/타이밍이 다르면 tkout_time 으로 분리.

트래킹 룰 (트래커 카테고리별):
  - category 에 source="fab"  → lot 의 latest_step_id 가 target_step_id 이상이면 알림
  - category 에 source="et"   → 새 측정 패키지가 나타나면 알림 (직전 last_observed 와 비교)
  - category 에 source="both" → FAB 갱신 OR ET 신규 측정 중 어느 쪽이든 알림
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import logging
import re
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Iterable
from core.latest_lot_cache_format import (
    FILE_NAME as LOT_PROGRESS_LATEST_CACHE_FILE,
    FORMAT_COLUMN as LATEST_CACHE_FORMAT_COLUMN,
    FORMAT_VERSION as LATEST_CACHE_FORMAT_VERSION,
)

logger = logging.getLogger("flow.lot_step")

FAB_ROOT = "1.RAWDATA_DB_FAB"
ET_ROOT = "1.RAWDATA_DB_ET"
# ET 측정시간 화면(routers/et_time.ET_SCAN_FILE_LIMIT)과 같은 스캔 범위 —
# ET DB 는 하루 1파일이라 최근 60일치를 연다.
ET_PACKAGE_FILE_LIMIT = 60
# ET DB 파일이 담는 날짜 — 파일명(PRODA_2026-07-17.parquet) 우선, 없으면 상위
# 파티션 폴더명(date=20260717). ET 추적 스캔이 "이미 본 날짜" 이후만 다시 읽는
# 기준이라, 못 읽어내면 그 파일은 항상 스캔 대상으로 남긴다(누락보다 재조회).
_PARQUET_DATE_RE = re.compile(r"(20\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])")
LOT_STEP_MAX_WAFER_ID = 25
DEFAULT_MONITOR_CATEGORY = "Monitor"
DEFAULT_ANALYSIS_CATEGORY = "Analysis"
_STEP_META_CACHE: dict = {}
ET_LOT_CACHE_VERSION = 1
ET_HISTORY_CACHE_VERSION = 1
ET_HISTORY_RECENT_DAYS = 3
ET_LOT_CACHE_DEFAULT_MINUTES = 30
ET_LOT_CACHE_MIN_MINUTES = 30
ET_LOT_CACHE_MAX_MINUTES = 60
_ET_LOT_CACHE_THREAD: threading.Thread | None = None
_ET_LOT_CACHE_STARTED = False
_ET_LOT_CACHE_STOP = threading.Event()
_ET_LOT_CACHE_LOCK = threading.Lock()
_ET_HISTORY_CACHE_LOCK = threading.Lock()


def _get_db_root() -> Path:
    from core.paths import PATHS
    try:
        from app_v2.shared.source_adapter import resolve_existing_root
        return resolve_existing_root("db", PATHS.db_root)
    except Exception:
        return PATHS.db_root


def _latest_cache_product_values(product: str = "") -> set[str]:
    raw = str(product or "").strip().upper()
    if not raw:
        return set()
    values = {raw}
    if raw.startswith("ML_TABLE_"):
        bare = raw[len("ML_TABLE_"):].strip()
        if bare:
            values.add(bare)
    else:
        values.add(f"ML_TABLE_{raw}")
    for alias in _product_aliases(raw):
        values.add(alias.upper())
        if not alias.upper().startswith("ML_TABLE_"):
            values.add(f"ML_TABLE_{alias.upper()}")
    return values


def _lot_progress_cache_root_matches(cache_module) -> bool:
    try:
        return Path(cache_module.PATHS.db_root).resolve() == _get_db_root().resolve()
    except Exception:
        return False


def _format_lot_progress_cache_row(row: dict, product: str = "") -> dict:
    step_id = str(row.get("step_id") or "").strip()
    if not step_id:
        return {}
    meta = lookup_step_meta(product=product or row.get("product") or "", step_id=step_id)
    function_step = str(row.get("function_step") or meta.get("function_step") or meta.get("func_step") or "").strip()
    return {
        "step_id": step_id,
        "time": row.get("tkout_time") or row.get("update_time") or row.get("time") or row.get("tkin_time"),
        "product": row.get("product"),
        "lot_id": row.get("lot_id"),
        "fab_lot_id": row.get("lot_id"),
        "root_lot_id": row.get("root_lot_id"),
        "wafer_id": row.get("wafer_id"),
        "tkout_time": row.get("tkout_time"),
        "update_time": row.get("update_time"),
        **meta,
        **({"function_step": function_step, "func_step": function_step} if function_step else {}),
        "source": "lot_progress_latest_cache",
    }


def _latest_fab_step_from_lot_progress_parquet(product: str = "", root_lot_id: str = "",
                                               lot_id: str = "", wafer_id: str = "") -> dict:
    fp = _get_db_root() / "cache" / LOT_PROGRESS_LATEST_CACHE_FILE
    if not fp.is_file():
        return {}
    try:
        import polars as pl
    except Exception:
        return {}
    try:
        lf = pl.scan_parquet(str(fp))
        schema = lf.collect_schema().names()
    except Exception:
        return {}
    if (
        "step_id" not in schema
        or "root_lot_id" not in schema
        or LATEST_CACHE_FORMAT_COLUMN not in schema
    ):
        return {}
    lf = lf.filter(
        pl.col(LATEST_CACHE_FORMAT_COLUMN).cast(pl.Int64, strict=False)
        == LATEST_CACHE_FORMAT_VERSION
    )
    filters = []
    product_values = _latest_cache_product_values(product)
    if product_values and "product" in schema:
        filters.append(pl.col("product").cast(pl.Utf8, strict=False).str.to_uppercase().is_in(sorted(product_values)))
    root_text = str(root_lot_id or "").strip()
    lot_text = str(lot_id or "").strip()
    if root_text:
        filters.append(pl.col("root_lot_id").cast(pl.Utf8, strict=False) == root_text)
    elif lot_text:
        lot_filters = [pl.col("root_lot_id").cast(pl.Utf8, strict=False) == lot_text]
        if "lot_id" in schema:
            lot_filters.append(pl.col("lot_id").cast(pl.Utf8, strict=False) == lot_text)
        expr = lot_filters[0]
        for item in lot_filters[1:]:
            expr = expr | item
        filters.append(expr)
    wafer_text = str(wafer_id or "").strip()
    wafer_values = parse_wafer_selection(wafer_text)
    if wafer_text and not _is_all_wafer_id(wafer_text) and not wafer_values:
        return {}
    if wafer_values and "wafer_id" in schema:
        wafer_expr = _wafer_filter_expr(pl, "wafer_id", wafer_values)
        if wafer_expr is not None:
            filters.append(wafer_expr)
    if filters:
        expr = filters[0]
        for item in filters[1:]:
            expr = expr & item
        lf = lf.filter(expr)
    select_cols = [
        c for c in (
            "product", "root_lot_id", "wafer_id", "lot_id", "step_id", "function_step",
            "tkout_time", "update_time", "time", "tkin_time",
        )
        if c in schema
    ]
    if not select_cols:
        return {}
    q = lf.select(select_cols).filter(pl.col("step_id").is_not_null() & (pl.col("step_id").cast(pl.Utf8, strict=False) != ""))
    for time_col in ("update_time", "tkout_time", "time", "tkin_time"):
        if time_col in select_cols:
            q = q.sort(time_col, descending=True, nulls_last=True)
            break
    try:
        df = q.head(1).collect()
    except Exception:
        return {}
    if df.is_empty():
        return {}
    return _format_lot_progress_cache_row(df.to_dicts()[0], product=product)


def _latest_fab_step_from_lot_progress_cache(product: str = "", root_lot_id: str = "",
                                             lot_id: str = "", wafer_id: str = "") -> dict:
    try:
        from core import lot_progress_cache
    except Exception:
        lot_progress_cache = None

    if lot_progress_cache is not None and _lot_progress_cache_root_matches(lot_progress_cache):
        product_candidates = sorted(_latest_cache_product_values(product)) if product else [""]
        for product_candidate in product_candidates:
            try:
                snapshot = lot_progress_cache.lot_progress_snapshot(
                    product=product_candidate,
                    root_lot_id=root_lot_id,
                    lot_id=lot_id,
                    wafer_id=wafer_id,
                    refresh_if_missing=False,
                )
            except Exception:
                snapshot = {}
            candidate = snapshot.get("fab") if isinstance(snapshot, dict) else {}
            if isinstance(candidate, dict) and candidate.get("step_id"):
                return _format_lot_progress_cache_row(candidate, product=product)
    return _latest_fab_step_from_lot_progress_parquet(
        product=product,
        root_lot_id=root_lot_id,
        lot_id=lot_id,
        wafer_id=wafer_id,
    )


def _settings_file() -> Path:
    from core.paths import PATHS
    return PATHS.data_root / "settings.json"


def _et_lot_cache_dir() -> Path:
    from core.paths import PATHS
    return PATHS.data_root / "tracker" / "et_lot_cache"


def _et_history_cache_dir() -> Path:
    from core.paths import PATHS
    return PATHS.data_root / "tracker" / "et_history_cache"


def _safe_id(value: str, max_len: int = 80) -> str:
    try:
        from core.utils import safe_id
        return safe_id(value, max_len=max_len).strip() or "product"
    except Exception:
        return re.sub(r"[^A-Za-z0-9 _-]+", "", str(value or ""))[:max_len].strip() or "product"


def et_lot_cache_refresh_minutes() -> int:
    try:
        from core.utils import load_json
        settings = load_json(_settings_file(), {})
    except Exception:
        settings = {}
    raw = settings.get("tracker_et_match_refresh_minutes", ET_LOT_CACHE_DEFAULT_MINUTES) if isinstance(settings, dict) else ET_LOT_CACHE_DEFAULT_MINUTES
    try:
        value = int(raw)
    except Exception:
        value = ET_LOT_CACHE_DEFAULT_MINUTES
    return max(ET_LOT_CACHE_MIN_MINUTES, min(ET_LOT_CACHE_MAX_MINUTES, value))


def tracker_db_sources_config() -> dict:
    """Tracker page-configured DB folders for Monitor/Analysis.

    Values are db_root-relative folder names such as `1.RAWDATA_DB_FAB`.
    """
    try:
        from core.utils import load_json
        settings = load_json(_settings_file(), {})
    except Exception:
        settings = {}
    tracker = settings.get("tracker") if isinstance(settings.get("tracker"), dict) else {}
    raw = settings.get("tracker_db_sources") if isinstance(settings.get("tracker_db_sources"), dict) else {}
    raw = {**(tracker.get("db_sources") if isinstance(tracker.get("db_sources"), dict) else {}), **raw}
    monitor = str(raw.get("monitor") or raw.get("fab") or FAB_ROOT).strip() or FAB_ROOT
    analysis = str(raw.get("analysis") or raw.get("et") or ET_ROOT).strip() or ET_ROOT
    return {
        "monitor": monitor,
        "analysis": analysis,
        "fab": monitor,
        "et": analysis,
    }


def tracker_role_names_config() -> dict:
    """Tracker category names that behave as Monitor/Analysis roles."""
    try:
        from core.utils import load_json
        settings = load_json(_settings_file(), {})
    except Exception:
        settings = {}
    tracker = settings.get("tracker") if isinstance(settings.get("tracker"), dict) else {}
    raw = settings.get("tracker_role_names") if isinstance(settings.get("tracker_role_names"), dict) else {}
    raw = {**(tracker.get("role_names") if isinstance(tracker.get("role_names"), dict) else {}), **raw}
    monitor = str(raw.get("monitor") or raw.get("monitor_name") or DEFAULT_MONITOR_CATEGORY).strip() or DEFAULT_MONITOR_CATEGORY
    analysis = str(raw.get("analysis") or raw.get("analysis_name") or DEFAULT_ANALYSIS_CATEGORY).strip() or DEFAULT_ANALYSIS_CATEGORY
    return {
        "monitor": monitor,
        "analysis": analysis,
    }


def source_root_for_context(source: str = "auto", category: str = "") -> str:
    cfg = tracker_db_sources_config()
    roles = tracker_role_names_config()
    cat = str(category or "").strip().lower()
    if cat == str(roles.get("monitor") or DEFAULT_MONITOR_CATEGORY).strip().lower():
        return cfg["monitor"]
    if cat == str(roles.get("analysis") or DEFAULT_ANALYSIS_CATEGORY).strip().lower():
        return cfg["analysis"]
    src = str(source or "").strip().lower()
    if src == "et":
        return cfg["analysis"]
    if src == "fab":
        return cfg["monitor"]
    return ""


def list_db_source_roots() -> list[str]:
    db_root = _get_db_root()
    if not db_root.is_dir():
        return []
    out = []
    for p in _top_level_data_roots(db_root):
        label = _root_label(p, db_root)
        if label and label not in out:
            out.append(label)
    for configured in tracker_db_sources_config().values():
        for p in _resolve_source_root_dirs("auto", configured, allow_fallback=False):
            label = _root_label(p, db_root)
            if label and label not in out:
                out.append(label)
    if not out:
        out.extend([FAB_ROOT, ET_ROOT])
    return out


def _is_root_lot_id(v: str) -> bool:
    """5자리 영숫자면 root_lot_id 로 해석. 그 외는 lot_id."""
    if not isinstance(v, str):
        return False
    v = v.strip()
    return len(v) == 5 and v.isalnum()


def _is_all_wafer_id(v: str) -> bool:
    return str(v or "").strip().lower() in {"all", "*", "전체"}


def _normalize_wafer_id(raw, *, max_wafer: int = LOT_STEP_MAX_WAFER_ID) -> str:
    text = str(raw or "").strip().upper()
    if not text:
        return ""
    core = re.sub(r"^(?:#|WAFER|WF|W)\s*", "", text, flags=re.I).strip()
    try:
        num = float(core)
    except Exception:
        return ""
    if not num.is_integer():
        return ""
    n = int(num)
    return str(n) if 1 <= n <= max_wafer else ""


def parse_wafer_selection(wafer_id: str) -> list[str]:
    """Parse tracker wafer input.

    - ""      → []
    - "1"     → ["1"]
    - "1,2,3" → ["1", "2", "3"]
    - "1~3"   → ["1", "2", "3"]
    - "all"   → [] here; caller should discover actual wafers from DB.
    """
    text = str(wafer_id or "").strip()
    if not text or _is_all_wafer_id(text):
        return []
    parts = []
    for token in text.replace(";", ",").split(","):
        item = token.strip()
        if not item:
            continue
        if "~" in item:
            left, right = item.split("~", 1)
            try:
                start = int(left.strip())
                end = int(right.strip())
            except Exception:
                norm = _normalize_wafer_id(item)
                if norm:
                    parts.append(norm)
                continue
            step = 1 if end >= start else -1
            parts.extend(str(v) for v in range(start, end + step, step) if 1 <= v <= LOT_STEP_MAX_WAFER_ID)
        else:
            norm = _normalize_wafer_id(item)
            if norm:
                parts.append(norm)
    out = []
    seen = set()
    for item in parts:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _wafer_filter_parts(values: Iterable[str]) -> tuple[list[str], list[int]]:
    strings: set[str] = set()
    ints: set[int] = set()
    for raw in values or []:
        norm = _normalize_wafer_id(raw)
        if not norm:
            continue
        n = int(norm)
        ints.add(n)
        strings.update({
            str(n),
            f"{n:02d}",
            f"W{n}",
            f"W{n:02d}",
            f"WF{n}",
            f"WF{n:02d}",
        })
    return sorted(strings), sorted(ints)


def _wafer_filter_expr(pl_module, col: str, values: Iterable[str]):
    strings, ints = _wafer_filter_parts(values)
    expr = None
    if strings:
        expr = pl_module.col(col).cast(pl_module.Utf8, strict=False).str.to_uppercase().is_in(strings)
    if ints:
        int_expr = pl_module.col(col).cast(pl_module.Int64, strict=False).is_in(ints)
        expr = int_expr if expr is None else (expr | int_expr)
    return expr


def _wafer_sort_key(v) -> tuple[int, str]:
    text = str(v or "").strip()
    try:
        return (0, f"{int(text):06d}")
    except Exception:
        return (1, text)


def _source_roots(source: str, source_root: str = "") -> list[str]:
    if str(source_root or "").strip():
        return [str(source_root or "").strip()]
    cfg = tracker_db_sources_config()
    src = str(source or "auto").lower().strip()
    if src == "fab":
        return [cfg.get("monitor") or FAB_ROOT]
    if src == "et":
        return [cfg.get("analysis") or ET_ROOT]
    roots = [cfg.get("analysis") or ET_ROOT, cfg.get("monitor") or FAB_ROOT]
    out = []
    seen = set()
    for root in roots:
        text = str(root or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _path_has_data(root: Path) -> bool:
    for pattern in ("*.parquet", "*.csv"):
        try:
            next(root.rglob(pattern))
            return True
        except StopIteration:
            continue
        except Exception:
            return False
    return False


def _top_level_data_roots(db_root: Path) -> list[Path]:
    if not db_root.is_dir():
        return []
    db_up = db_root.name.upper()
    db_tokens = _name_tokens(db_up)
    source_roots: list[Path] = []
    data_roots: list[Path] = []
    try:
        for p in sorted(db_root.iterdir(), key=lambda x: x.name.lower()):
            if not p.is_dir() or p.name.startswith((".", "_", "__")):
                continue
            up = p.name.upper()
            tokens = _name_tokens(up)
            if up.startswith("1.RAWDATA_DB") or up in {"FAB", "ET", "EDS", "INLINE"} or ({"FAB", "ET", "EDS", "INLINE"} & tokens):
                source_roots.append(p)
            elif _path_has_data(p):
                data_roots.append(p)
    except Exception:
        return []
    if source_roots:
        return source_roots
    if (
        db_up.startswith("1.RAWDATA_DB")
        or db_up in {"FAB", "ET", "EDS", "INLINE"}
        or {"FAB", "ET", "EDS", "INLINE"} & db_tokens
    ) and _path_has_data(db_root):
        return [db_root]
    if _path_has_data(db_root):
        return [db_root]
    return data_roots


def _root_label(root: Path, db_root: Path | None = None) -> str:
    db_root = db_root or _get_db_root()
    try:
        rel = root.resolve().relative_to(db_root.resolve())
        return "." if str(rel) == "." else rel.as_posix()
    except Exception:
        return root.name


def _casefold_child_path(parent: Path, rel: str) -> Path | None:
    text = str(rel or "").strip().strip("/\\")
    if not text or not parent.exists():
        return None
    exact = parent / text
    if exact.exists():
        return exact
    cur = parent
    for part in [p for p in text.replace("\\", "/").split("/") if p]:
        target = part.casefold()
        found = None
        try:
            for child in cur.iterdir():
                if child.name.casefold() == target:
                    found = child
                    break
        except Exception:
            return None
        if found is None:
            return None
        cur = found
    return cur if cur.exists() else None


def _resolve_named_db_child(db_root: Path, root_name: str) -> Path | None:
    text = str(root_name or "").strip().strip("/\\")
    if not text:
        return None
    p = Path(text)
    if p.is_absolute():
        return p if p.exists() else None
    try:
        from app_v2.shared.source_adapter import resolve_named_child
        if "/" not in text and "\\" not in text:
            resolved = resolve_named_child(db_root, text)
            if resolved is not None and resolved.exists():
                return resolved
    except Exception:
        pass
    return _casefold_child_path(db_root, text)


def _name_tokens(value: str) -> set[str]:
    return {t for t in re.split(r"[^A-Z0-9]+", str(value or "").upper()) if t}


def _source_kind(source: str = "auto", root_name: str = "") -> str:
    src = str(source or "auto").strip().lower()
    if src in {"fab", "monitor"}:
        return "fab"
    if src in {"et", "analysis"}:
        return "et"
    tokens = _name_tokens(root_name)
    if "ET" in tokens or "EDS" in tokens or "ANALYSIS" in tokens:
        return "et"
    if "FAB" in tokens or "MONITOR" in tokens:
        return "fab"
    return "auto"


def _source_root_rank(root: Path, kind: str) -> int:
    name = root.name.upper()
    tokens = _name_tokens(name)
    if kind == "fab":
        if name == FAB_ROOT.upper():
            return 0
        if "FAB" in tokens or "MONITOR" in tokens:
            return 1
        if name == "1.RAWDATA_DB":
            return 2
        if "RAWDATA" in tokens and not ({"ET", "EDS", "INLINE", "VM", "MASK", "YLD"} & tokens):
            return 3
        return 90
    if kind == "et":
        if name == ET_ROOT.upper():
            return 0
        if "ET" in tokens or "EDS" in tokens or "ANALYSIS" in tokens:
            return 1
        if name == "1.RAWDATA_DB":
            return 3
        return 90
    if name == ET_ROOT.upper():
        return 0
    if name == FAB_ROOT.upper():
        return 1
    if name == "1.RAWDATA_DB":
        return 2
    if "ET" in tokens or "FAB" in tokens or "RAWDATA" in tokens:
        return 3
    return 80


def _fallback_source_root_dirs(kind: str) -> list[Path]:
    roots = _top_level_data_roots(_get_db_root())
    ranked = [(p, _source_root_rank(p, kind)) for p in roots]
    ranked = [(p, rank) for p, rank in ranked if rank < 90]
    ranked.sort(key=lambda item: (item[1], item[0].name.lower()))
    return [p for p, _rank in ranked]


def _resolve_source_root_dirs(source: str = "auto", source_root: str = "", allow_fallback: bool = True) -> list[Path]:
    db_root = _get_db_root()
    out: list[Path] = []
    seen = set()

    def _add(p: Path | None) -> None:
        if p is None or not p.is_dir():
            return
        key = str(p.resolve())
        if key in seen:
            return
        seen.add(key)
        out.append(p)

    for root_name in _source_roots(source, source_root):
        _add(_resolve_named_db_child(db_root, root_name))

    if out or not allow_fallback:
        return out

    kind = _source_kind(source, source_root)
    for p in _fallback_source_root_dirs(kind):
        _add(p)
    return out


def _product_aliases(product: str = "") -> set[str]:
    raw = str(product or "").strip().upper()
    if not raw:
        return set()
    out = {raw}
    if raw.startswith("ML_TABLE_"):
        raw = raw[len("ML_TABLE_"):].strip()
        out.add(raw)
    if raw == "PRODA":
        out.update({"PRODA0", "PRODA1", "PRODUCT_A0", "PRODUCT_A1"})
    elif raw == "PRODA0":
        out.update({"PRODA", "PRODUCT_A0"})
    elif raw == "PRODA1":
        out.update({"PRODA", "PRODUCT_A1"})
    elif raw.startswith("PRODUCT_A"):
        if raw.endswith("0"):
            out.update({"PRODA", "PRODA0", "PRODUCT_A0"})
        elif raw.endswith("1"):
            out.update({"PRODA", "PRODA1", "PRODUCT_A1"})
        else:
            out.update({"PRODA", "PRODA0", "PRODA1", "PRODUCT_A0", "PRODUCT_A1"})
    elif raw == "PRODB":
        out.update({"PRODUCT_B"})
    elif raw.startswith("PRODUCT_B"):
        out.update({"PRODB", "PRODUCT_B"})
    return out


def _product_cell_tokens(product: object) -> list[str]:
    raw = str(product or "").strip().upper()
    if not raw:
        return []
    return [part.strip() for part in re.split(r"[,，、]", raw) if part.strip()]


def _product_match_keys(product: str = "") -> set[str]:
    keys = set(_product_aliases(product))
    for key in list(keys):
        if key.startswith("ML_TABLE_"):
            keys.add(key[len("ML_TABLE_"):].strip())
        else:
            keys.add(f"ML_TABLE_{key}")
    return {key for key in keys if key}


def _data_product_values(product: str = "") -> set[str]:
    raw = str(product or "").strip().upper()
    if raw.startswith("ML_TABLE_"):
        raw = raw[len("ML_TABLE_"):].strip()
    if not raw:
        return set()
    if raw == "PRODA":
        return {"PRODA", "PRODA0", "PRODA1", "PRODUCT_A0", "PRODUCT_A1"}
    if raw == "PRODA0":
        return {"PRODA0", "PRODA", "PRODUCT_A0"}
    if raw == "PRODA1":
        return {"PRODA1", "PRODA", "PRODUCT_A1"}
    if raw == "PRODB":
        return {"PRODB", "PRODUCT_B"}
    if raw.startswith("PRODUCT_A"):
        if raw.endswith("0"):
            return {raw, "PRODA", "PRODA0"}
        if raw.endswith("1"):
            return {raw, "PRODA", "PRODA1"}
        return {raw, "PRODA", "PRODA0", "PRODA1"}
    if raw.startswith("PRODUCT_B"):
        return {raw, "PRODB"}
    return {raw}


def _product_names_under_root(root_dir: Path) -> list[str]:
    """Discover product names from legacy and hive-table source roots.

    This stays structural and bounded so Tracker dropdowns do not trigger a
    broad parquet scan just to populate products.
    """
    names: list[str] = []
    seen: set[str] = set()

    def _add(value: str) -> None:
        text = str(value or "").strip()
        if text.startswith("product="):
            text = text[len("product="):].strip()
        key = text.upper()
        if not text or key in seen:
            return
        seen.add(key)
        names.append(text)

    try:
        children = [p for p in sorted(root_dir.iterdir(), key=lambda x: x.name.lower()) if p.is_dir()]
    except Exception:
        return names

    try:
        for fp in sorted(root_dir.iterdir(), key=lambda x: x.name.lower()):
            if fp.is_file() and fp.suffix.lower() in (".parquet", ".csv"):
                _add(_product_from_data_file(fp))
    except Exception:
        pass

    for child in children:
        if child.name.startswith((".", "_", "__")):
            continue
        if child.name.startswith("product="):
            try:
                has_structured_data = any(
                    (p.is_dir() and p.name.startswith("date=")) or (p.is_file() and p.suffix.lower() in (".parquet", ".csv"))
                    for p in child.iterdir()
                )
            except Exception:
                has_structured_data = False
            if has_structured_data or _path_has_data(child):
                _add(child.name)
            continue
        try:
            has_product_parts = any(p.is_dir() and p.name.startswith("product=") for p in child.iterdir())
        except Exception:
            has_product_parts = False
        if has_product_parts:
            continue
        try:
            has_structured_data = any(
                (p.is_dir() and p.name.startswith("date=")) or (p.is_file() and p.suffix.lower() in (".parquet", ".csv"))
                for p in child.iterdir()
            )
        except Exception:
            has_structured_data = False
        if has_structured_data or _path_has_data(child):
            _add(child.name)

    for table_dir in children:
        if table_dir.name.startswith((".", "_", "__", "product=")):
            continue
        try:
            for fp in sorted(table_dir.iterdir(), key=lambda x: x.name.lower()):
                if fp.is_file() and fp.suffix.lower() in (".parquet", ".csv"):
                    _add(_product_from_data_file(fp))
        except Exception:
            pass
        try:
            parts = [p for p in table_dir.iterdir() if p.is_dir() and p.name.startswith("product=")]
        except Exception:
            continue
        for part in parts:
            try:
                has_structured_data = any(
                    (p.is_dir() and p.name.startswith("date=")) or (p.is_file() and p.suffix.lower() in (".parquet", ".csv"))
                    for p in part.iterdir()
                )
            except Exception:
                has_structured_data = False
            if has_structured_data or _path_has_data(part):
                _add(part.name)
    return names


def _product_dirs_under_root(root_dir: Path, product: str) -> list[Path]:
    raw = str(product or "").strip().upper()
    if raw.startswith("ML_TABLE_"):
        raw = raw[len("ML_TABLE_"):].strip()
    aliases = sorted(_product_aliases(raw) or {raw})
    dirs: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path | None) -> None:
        if path is None or not path.is_dir():
            return
        key = str(path.resolve())
        if key in seen:
            return
        seen.add(key)
        dirs.append(path)

    for alias in aliases:
        _add(_casefold_child_path(root_dir, alias))
        _add(_casefold_child_path(root_dir, f"product={alias}"))

    try:
        children = [p for p in root_dir.iterdir() if p.is_dir()]
    except Exception:
        return dirs
    for child in children:
        if child.name.startswith((".", "_", "__", "product=")):
            continue
        for alias in aliases:
            _add(_casefold_child_path(child, f"product={alias}"))
    return dirs


def _product_from_data_file(path: Path) -> str:
    """Infer product from flat source filenames such as PRODA_2024-04-23.parquet."""
    if not path or path.suffix.lower() not in {".parquet", ".csv"}:
        return ""
    stem = path.stem.strip()
    if not stem or stem.lower().startswith("part"):
        return ""
    if stem.startswith("product="):
        stem = stem[len("product="):].strip()
    if stem.upper().startswith("ML_TABLE_"):
        stem = stem[len("ML_TABLE_"):].strip()
    stem = re.split(r"[_-](?:19|20)\d{2}(?:[-_]?\d{2}){0,2}", stem, maxsplit=1)[0].strip()
    return stem


def _product_files_under_root(root_dir: Path, product: str) -> list[Path]:
    raw = str(product or "").strip().upper()
    if raw.startswith("ML_TABLE_"):
        raw = raw[len("ML_TABLE_"):].strip()
    aliases = _product_aliases(raw) or {raw}
    files: list[Path] = []
    try:
        children = sorted(root_dir.iterdir(), key=lambda x: x.name.lower())
    except Exception:
        return files
    for fp in children:
        if not fp.is_file() or fp.suffix.lower() != ".parquet":
            continue
        inferred = _product_from_data_file(fp).upper()
        if inferred and inferred in aliases:
            files.append(fp)
    return files


def _apply_lot_filters(lf, schema: list[str], product: str = "", root_lot_id: str = "", lot_id: str = ""):
    try:
        import polars as pl
    except Exception:
        return lf
    filters = []
    prod_values = _data_product_values(product)
    if prod_values and "product" in schema:
        filters.append(pl.col("product").cast(pl.Utf8).str.to_uppercase().is_in(sorted(prod_values)))
    if root_lot_id and "root_lot_id" in schema:
        filters.append(pl.col("root_lot_id").cast(pl.Utf8) == str(root_lot_id))
    elif lot_id:
        lot_filters = [
            pl.col(c).cast(pl.Utf8) == str(lot_id)
            for c in ("lot_id", "fab_lot_id")
            if c in schema
        ]
        if lot_filters:
            expr = lot_filters[0]
            for e in lot_filters[1:]:
                expr = expr | e
            filters.append(expr)
    if filters:
        expr = filters[0]
        for e in filters[1:]:
            expr = expr & e
        lf = lf.filter(expr)
    return lf


def _filter_valid_wafers(lf):
    try:
        from core.utils import filter_valid_wafer_ids_lazy
        return filter_valid_wafer_ids_lazy(lf)
    except Exception:
        return lf


def _scan_source_files(root_name: str, product: str = "", source: str = "auto"):
    try:
        import polars as pl
    except Exception:
        return None
    files = _parquet_files(root_name, product, source=source)
    if not files:
        return None
    try:
        return _filter_valid_wafers(pl.scan_parquet([str(f) for f in files[-30:]], hive_partitioning=True))
    except Exception:
        try:
            return _filter_valid_wafers(pl.scan_parquet([str(f) for f in files[-30:]]))
        except Exception:
            return None


def _scan_source_files_all(root_name: str, product: str = "", source: str = "auto"):
    try:
        import polars as pl
    except Exception:
        return None
    files = _parquet_files(root_name, product, source=source)
    if not files:
        return None
    try:
        return _filter_valid_wafers(pl.scan_parquet([str(f) for f in files], hive_partitioning=True))
    except Exception:
        try:
            return _filter_valid_wafers(pl.scan_parquet([str(f) for f in files]))
        except Exception:
            return None


def _ci_col(cols: list[str], *candidates: str) -> str:
    by_lower = {str(c).lower(): c for c in cols}
    for cand in candidates:
        hit = by_lower.get(str(cand).lower())
        if hit:
            return hit
    return ""


def _cache_product_name(product: str) -> str:
    raw = str(product or "").strip()
    if raw.upper().startswith("ML_TABLE_"):
        raw = raw[len("ML_TABLE_"):].strip()
    return raw


def _et_lot_cache_path(product: str, source_root: str = "") -> Path:
    name = _safe_id(f"{_cache_product_name(product)}__{source_root or tracker_db_sources_config().get('analysis') or ET_ROOT}")
    return _et_lot_cache_dir() / f"{name}.parquet"


def _et_lot_cache_meta_path(product: str, source_root: str = "") -> Path:
    return _et_lot_cache_path(product, source_root).with_suffix(".json")


def _et_lot_cache_config_key(product: str, source_root: str = "") -> str:
    import json
    payload = {
        "version": ET_LOT_CACHE_VERSION,
        "product": _cache_product_name(product).upper(),
        "source_root": str(source_root or tracker_db_sources_config().get("analysis") or ET_ROOT).strip(),
        "db_root": str(_get_db_root()),
    }
    try:
        return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        return str(payload)


def _et_lot_cache_current(product: str, source_root: str = "") -> dict | None:
    prod = _cache_product_name(product)
    if not prod:
        return None
    root = str(source_root or tracker_db_sources_config().get("analysis") or ET_ROOT).strip() or ET_ROOT
    fp = _et_lot_cache_path(prod, root)
    meta_fp = _et_lot_cache_meta_path(prod, root)
    if not fp.is_file() or not meta_fp.is_file():
        return None
    try:
        from core.utils import load_json
        meta = load_json(meta_fp, {})
    except Exception:
        meta = {}
    if not isinstance(meta, dict) or meta.get("version") != ET_LOT_CACHE_VERSION:
        return None
    if meta.get("config_key") != _et_lot_cache_config_key(prod, root):
        return None
    try:
        import polars as pl
        lf = _filter_valid_wafers(pl.scan_parquet(str(fp)))
    except Exception as e:
        logger.warning("ET lot cache scan failed product=%s source=%s: %s", prod, root, e)
        return None
    return {"product": prod, "source_root": root, "path": fp, "meta": meta, "lf": lf}


def _sort_cache_values(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: (
        str(r.get("value") or "").upper(),
        str(r.get("type") or ""),
    ))


_LOT_CANDIDATE_LIMIT_MAX = 50000


def _wanted_lot_cols(cols, default: tuple) -> list[str]:
    """cols 인자를 정규화한다. 비어 있으면 default 순서를 그대로 쓴다."""
    if isinstance(cols, str):
        cols = cols.split(",")
    wanted = [str(c).strip() for c in (cols or ()) if str(c or "").strip()]
    return wanted or list(default)


def et_lot_candidates_from_cache(product: str = "", source_root: str = "", prefix: str = "",
                                 limit: int = 200, cols=None) -> list[dict]:
    """cols 를 주면 해당 컬럼 후보만 반환한다 (예: ["root_lot_id"]). 기본은 3종 전부."""
    current = _et_lot_cache_current(product, source_root)
    if not current:
        return []
    try:
        import polars as pl
    except Exception:
        return []
    try:
        limit = max(1, min(_LOT_CANDIDATE_LIMIT_MAX, int(limit or 200)))
    except Exception:
        limit = 200
    needle = str(prefix or "").strip().upper()
    out: list[dict] = []
    seen = set()
    lf = current["lf"]

    def add_col(col: str, typ: str) -> None:
        nonlocal out
        if len(out) >= limit:
            return
        try:
            names = lf.collect_schema().names()
        except Exception:
            return
        if col not in names:
            return
        try:
            q = (
                lf.select(pl.col(col).cast(pl.Utf8).alias("value"))
                .filter(pl.col("value").is_not_null() & (pl.col("value") != ""))
            )
            if needle:
                q = q.filter(pl.col("value").str.to_uppercase().str.starts_with(needle))
            df = q.unique().head(max(1, limit - len(out))).collect()
        except Exception:
            return
        for row in df.to_dicts():
            value = str(row.get("value") or "").strip()
            if not value:
                continue
            key = (typ, value.upper())
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "value": value,
                "type": typ,
                "source_root": current["source_root"],
                "cache": "et_lot",
                "cache_built_at": current["meta"].get("built_at", ""),
            })
            if len(out) >= limit:
                return

    for col in _wanted_lot_cols(cols, ("root_lot_id", "fab_lot_id", "lot_id")):
        add_col(col, col)
    return _sort_cache_values(out)[:limit]


def refresh_et_lot_cache(product: str = "", source_root: str = "", force: bool = False) -> dict:
    """Persist ET root_lot_id/fab_lot_id/lot_id candidates for Tracker Analysis."""
    try:
        import polars as pl
        from core.utils import save_json, load_json
    except Exception as e:
        return {"ok": False, "products": [], "error": f"import failed: {e}"}

    root = str(source_root or tracker_db_sources_config().get("analysis") or ET_ROOT).strip() or ET_ROOT
    raw_prod = _cache_product_name(product)
    products = [raw_prod] if raw_prod else []
    if not products:
        try:
            products = db_product_candidates(source_root=root, source="et", limit=500)
        except Exception:
            products = []
    products = [_cache_product_name(p) for p in products if _cache_product_name(p)]
    results: list[dict] = []
    with _ET_LOT_CACHE_LOCK:
        _et_lot_cache_dir().mkdir(parents=True, exist_ok=True)
        for prod in products:
            fp = _et_lot_cache_path(prod, root)
            meta_fp = _et_lot_cache_meta_path(prod, root)
            config_key = _et_lot_cache_config_key(prod, root)
            result = {"product": prod, "source_root": root, "ok": False, "skipped": False, "row_count": 0}
            try:
                old_meta = load_json(meta_fp, {}) if meta_fp.is_file() else {}
                if not force and fp.is_file() and isinstance(old_meta, dict) and old_meta.get("config_key") == config_key:
                    age_s = time.time() - float(old_meta.get("built_epoch") or 0)
                    if age_s < et_lot_cache_refresh_minutes() * 60:
                        result.update({"ok": True, "skipped": True, "row_count": int(old_meta.get("row_count") or 0)})
                        results.append(result)
                        continue
                lf = _scan_source_files_all(root, prod, source="et")
                if lf is None:
                    result["reason"] = "ET source parquet not found"
                    results.append(result)
                    continue
                cols = lf.collect_schema().names()
                product_col = _ci_col(cols, "product", "PRODUCT")
                root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
                fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
                lot_col = _ci_col(cols, "lot_id", "LOT_ID")
                wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
                ts_col = _ci_col(cols, "time", "TIME", "tkout_time", "TKOUT_TIME", "end_ts", "END_TS", "start_ts", "START_TS", "measure_time", "MEASURE_TIME", "timestamp", "TIMESTAMP")
                if not (root_col or fab_col or lot_col):
                    result["reason"] = "root_lot_id/fab_lot_id/lot_id columns missing"
                    result["columns"] = cols[:80]
                    results.append(result)
                    continue
                aliases = _data_product_values(prod)
                if aliases and product_col:
                    lf = lf.filter(pl.col(product_col).cast(pl.Utf8).str.to_uppercase().is_in(sorted(aliases)))
                exprs = []
                if root_col:
                    exprs.append(pl.col(root_col).cast(pl.Utf8, strict=False).alias("root_lot_id"))
                else:
                    exprs.append(pl.lit("").alias("root_lot_id"))
                if fab_col:
                    exprs.append(pl.col(fab_col).cast(pl.Utf8, strict=False).alias("fab_lot_id"))
                elif lot_col:
                    exprs.append(pl.col(lot_col).cast(pl.Utf8, strict=False).alias("fab_lot_id"))
                else:
                    exprs.append(pl.lit("").alias("fab_lot_id"))
                if lot_col:
                    exprs.append(pl.col(lot_col).cast(pl.Utf8, strict=False).alias("lot_id"))
                else:
                    exprs.append(pl.lit("").alias("lot_id"))
                if wafer_col:
                    exprs.append(pl.col(wafer_col).cast(pl.Utf8, strict=False).alias("wafer_id"))
                else:
                    exprs.append(pl.lit("").alias("wafer_id"))
                if ts_col:
                    exprs.append(pl.col(ts_col).cast(pl.Utf8, strict=False).alias("ts"))
                else:
                    exprs.append(pl.lit("").alias("ts"))
                q = lf.select(exprs)
                q = q.filter(
                    (pl.col("root_lot_id") != "")
                    | (pl.col("fab_lot_id") != "")
                    | (pl.col("lot_id") != "")
                )
                q = q.sort("ts", descending=True, nulls_last=True).unique(
                    subset=["root_lot_id", "fab_lot_id", "lot_id", "wafer_id"],
                    keep="first",
                    maintain_order=True,
                )
                df = q.collect()
                tmp = fp.with_suffix(fp.suffix + ".tmp")
                df.write_parquet(tmp)
                tmp.replace(fp)
                meta = {
                    "version": ET_LOT_CACHE_VERSION,
                    "product": prod,
                    "source_root": root,
                    "config_key": config_key,
                    "built_at": dt.datetime.now().isoformat(timespec="seconds"),
                    "built_epoch": time.time(),
                    "row_count": int(df.height),
                    "columns": {
                        "product": product_col,
                        "root_lot_id": root_col,
                        "fab_lot_id": fab_col,
                        "lot_id": lot_col,
                        "wafer_id": wafer_col,
                        "ts": ts_col,
                    },
                }
                save_json(meta_fp, meta)
                result.update({"ok": True, "row_count": int(df.height), "columns": meta["columns"]})
            except Exception as e:
                logger.warning("ET lot cache build failed product=%s source=%s: %s", prod, root, e, exc_info=True)
                result["reason"] = f"{type(e).__name__}: {e}"
            results.append(result)
    return {"ok": any(r.get("ok") for r in results), "products": results, "interval_minutes": et_lot_cache_refresh_minutes(), "source_root": root}


def et_lot_cache_status(product: str = "", source_root: str = "") -> dict:
    try:
        from core.runtime_limits import tracker_et_lot_cache_enabled
        enabled = tracker_et_lot_cache_enabled()
    except Exception:
        enabled = False
    root = str(source_root or tracker_db_sources_config().get("analysis") or ET_ROOT).strip() or ET_ROOT
    prod = _cache_product_name(product)
    rows = []
    if prod:
        current = _et_lot_cache_current(prod, root)
        if current:
            meta = current["meta"]
            rows.append({
                "product": prod,
                "source_root": root,
                "path": str(current["path"]),
                "built_at": meta.get("built_at", ""),
                "row_count": int(meta.get("row_count") or 0),
            })
    else:
        cache_dir = _et_lot_cache_dir()
        try:
            metas = sorted(cache_dir.glob("*.json"))
        except Exception:
            metas = []
        try:
            from core.utils import load_json
        except Exception:
            load_json = None
        for fp in metas[:500]:
            meta = load_json(fp, {}) if load_json else {}
            if not isinstance(meta, dict) or meta.get("version") != ET_LOT_CACHE_VERSION:
                continue
            rows.append({
                "product": meta.get("product", ""),
                "source_root": meta.get("source_root", ""),
                "path": str(fp.with_suffix(".parquet")),
                "built_at": meta.get("built_at", ""),
                "row_count": int(meta.get("row_count") or 0),
            })
    return {
        "ok": True,
        "enabled": enabled,
        "interval_minutes": et_lot_cache_refresh_minutes(),
        "source_root": root,
        "products": rows,
    }


def _et_lot_cache_loop() -> None:
    while not _ET_LOT_CACHE_STOP.is_set():
        try:
            refresh_et_lot_cache(force=False)
        except Exception as e:
            logger.warning("ET lot cache scheduler tick failed: %s", e)
        wait_s = max(60.0, et_lot_cache_refresh_minutes() * 60.0)
        while wait_s > 0 and not _ET_LOT_CACHE_STOP.is_set():
            step = min(wait_s, 60.0)
            _ET_LOT_CACHE_STOP.wait(step)
            wait_s -= step


def start_et_lot_cache_scheduler() -> bool:
    global _ET_LOT_CACHE_THREAD, _ET_LOT_CACHE_STARTED
    if _ET_LOT_CACHE_STARTED:
        return False
    try:
        from core.runtime_limits import tracker_et_lot_cache_enabled
        if not tracker_et_lot_cache_enabled():
            logger.info("Tracker ET lot cache scheduler disabled")
            return False
    except Exception:
        pass
    _ET_LOT_CACHE_STOP.clear()
    _ET_LOT_CACHE_THREAD = threading.Thread(target=_et_lot_cache_loop, name="tracker-et-lot-cache", daemon=True)
    _ET_LOT_CACHE_THREAD.start()
    _ET_LOT_CACHE_STARTED = True
    logger.info("Tracker ET lot cache scheduler started (interval=%sm)", et_lot_cache_refresh_minutes())
    return True


def db_product_candidates(source_root: str = "", source: str = "auto", prefix: str = "",
                          limit: int = 500) -> list[str]:
    """Return product candidates visible under the selected Tracker DB root."""
    needle = str(prefix or "").strip().upper()
    values: list[str] = []
    seen = set()

    def _add(value):
        text = str(value or "").strip()
        if not text:
            return
        if needle and not text.upper().startswith(needle):
            return
        key = text.upper()
        if key in seen:
            return
        seen.add(key)
        values.append(text)

    for root_name in _source_roots(source, source_root):
        root_structured = False
        for root_dir in _resolve_source_root_dirs(source, root_name):
            try:
                for product_name in _product_names_under_root(root_dir):
                    root_structured = True
                    _add(product_name)
                    if len(values) >= limit:
                        return values[:limit]
            except Exception:
                pass
        if root_structured and not needle:
            continue
        try:
            import polars as pl
            lf = _scan_source_files(root_name, "", source=source)
            if lf is None:
                continue
            schema = lf.collect_schema().names()
            if "product" not in schema:
                continue
            q = lf.select(pl.col("product").cast(pl.Utf8).alias("product")).filter(pl.col("product").is_not_null())
            if needle:
                q = q.filter(pl.col("product").str.to_uppercase().str.starts_with(needle))
            df = q.unique().head(max(1, limit - len(values))).collect()
            for row in df.to_dicts():
                _add(row.get("product"))
                if len(values) >= limit:
                    return values[:limit]
        except Exception:
            continue
    return values[:limit]


def lot_id_candidates(product: str = "", source_root: str = "", source: str = "auto",
                      prefix: str = "", limit: int = 200, cols=None) -> list[dict]:
    """Return root_lot_id/fab_lot_id/lot_id candidates for Tracker row entry.

    cols 를 주면 그 컬럼 후보만 반환한다 (예: ["root_lot_id"] → root lot 전용 목록).
    """
    if _source_kind(source, source_root) == "et":
        cached = et_lot_candidates_from_cache(
            product=product,
            source_root=source_root or tracker_db_sources_config().get("analysis") or ET_ROOT,
            prefix=prefix,
            limit=limit,
            cols=cols,
        )
        if cached:
            return cached
    try:
        import polars as pl
    except Exception:
        return []
    needle = str(prefix or "").strip().upper()
    out: list[dict] = []
    seen = set()
    for root_name in _source_roots(source, source_root):
        lf = _scan_source_files(root_name, product, source=source)
        if lf is None:
            continue
        try:
            schema = lf.collect_schema().names()
        except Exception:
            continue
        lf_filtered = _apply_lot_filters(lf, schema, product=product)
        for col in _wanted_lot_cols(cols, ("fab_lot_id", "lot_id", "root_lot_id")):
            if col not in schema:
                continue
            try:
                q = (
                    lf_filtered
                    .select(pl.col(col).cast(pl.Utf8).alias("value"))
                    .filter(pl.col("value").is_not_null())
                )
                if needle:
                    q = q.filter(pl.col("value").str.to_uppercase().str.starts_with(needle))
                remaining = max(1, limit - len(out))
                df = q.unique().head(remaining).collect()
            except Exception:
                continue
            for row in df.to_dicts():
                value = str(row.get("value") or "").strip()
                if not value:
                    continue
                key = (col, value)
                if key in seen:
                    continue
                seen.add(key)
                out.append({"value": value, "type": col, "source_root": root_name})
                if len(out) >= limit:
                    return out
    return out[:limit]


def discover_wafer_ids(product: str = "", root_lot_id: str = "", lot_id: str = "",
                       source: str = "auto", source_root: str = "", limit: int = 200) -> list[str]:
    """Return actual wafer_id values for a lot/product from ET/FAB DB."""
    try:
        import polars as pl
    except Exception:
        return []
    values = []
    seen = set()
    for root_name in _source_roots(source, source_root):
        lf = _scan_source_files(root_name, product, source=source)
        if lf is None:
            continue
        try:
            schema = lf.collect_schema().names()
        except Exception:
            continue
        if "wafer_id" not in schema:
            continue
        lf = _apply_lot_filters(lf, schema, product=product, root_lot_id=root_lot_id, lot_id=lot_id)
        try:
            df = (
                lf.select(pl.col("wafer_id").cast(pl.Utf8).alias("wafer_id"))
                .filter(pl.col("wafer_id").is_not_null())
                .unique()
                .head(limit)
                .collect()
            )
        except Exception:
            continue
        for row in df.to_dicts():
            wafer = str(row.get("wafer_id") or "").strip()
            if not wafer or wafer in seen:
                continue
            seen.add(wafer)
            values.append(wafer)
    return sorted(values, key=_wafer_sort_key)


def resolve_wafer_selection(product: str = "", root_lot_id: str = "", lot_id: str = "",
                            wafer_id: str = "", source: str = "auto", source_root: str = "") -> list[str]:
    """Resolve tracker wafer input into concrete wafer values.

    For "all", actual wafer IDs are discovered from the selected source DB. If discovery
    fails, the original value is returned so the caller keeps the row visible.
    """
    text = str(wafer_id or "").strip()
    explicit = parse_wafer_selection(text)
    if explicit:
        return explicit
    if _is_all_wafer_id(text):
        found = discover_wafer_ids(
            product=product,
            root_lot_id=root_lot_id,
            lot_id=lot_id,
            source=source,
            source_root=source_root,
        )
        return found or [text]
    return [] if text else [""]


_WATCH_WAFER_STATE_KEYS = {
    "last_observed_step",
    "last_observed_et_count",
    "last_observed_et_step_keys",
    "et_step_states",
    "notified_new_et_step_keys",
    "et_watch_initialized",
    "fired_target_step_ids",
    "last_fired_at",
    "last_fired_step_id",
    "last_fired_et_signature",
}


def reset_watch_state_for_wafer_expansion(watch: dict) -> dict:
    """Keep user watch preferences but reset observed state after one row becomes many wafers."""
    if not isinstance(watch, dict):
        return watch
    return {k: v for k, v in watch.items() if k not in _WATCH_WAFER_STATE_KEYS}


def expand_lot_row_for_wafer_selection(lot: dict, *, product: str = "", root_lot_id: str = "",
                                       lot_id: str = "", wafer_id: str = "",
                                       source: str = "auto", source_root: str = "") -> list[dict]:
    wafers = resolve_wafer_selection(
        product=product,
        root_lot_id=root_lot_id,
        lot_id=lot_id,
        wafer_id=wafer_id,
        source=source,
        source_root=source_root,
    )
    current = str(wafer_id or "").strip()
    should_expand = len(wafers) > 1 or (_is_all_wafer_id(current) and wafers and wafers[0] != current)
    if not should_expand:
        return [dict(lot or {})]
    out = []
    for wafer in wafers:
        row = dict(lot or {})
        row["wafer_id"] = wafer
        if isinstance(row.get("watch"), dict):
            row["watch"] = reset_watch_state_for_wafer_expansion(row.get("watch") or {})
        out.append(row)
    return out


def _parquet_files(root_name: str, product: str = "", source: str = "auto") -> list[Path]:
    root_dirs = _resolve_source_root_dirs(source, root_name)
    if not root_dirs:
        return []
    raw = str(product or "").strip().upper()
    if raw.startswith("ML_TABLE_"):
        raw = raw[len("ML_TABLE_"):].strip()
    files: list[Path] = []
    if raw:
        for root_dir in root_dirs:
            files.extend(_product_files_under_root(root_dir, raw))
            dirs = _product_dirs_under_root(root_dir, raw)
            for d in dirs:
                files.extend(_product_files_under_root(d, raw))
                files.extend(sorted(d.rglob("*.parquet")))
            try:
                for child in root_dir.iterdir():
                    if child.is_dir() and not child.name.startswith((".", "_", "__", "product=")):
                        files.extend(_product_files_under_root(child, raw))
            except Exception:
                pass
        return _dedupe_paths(files)
    for root_dir in root_dirs:
        files.extend(sorted(root_dir.rglob("*.parquet")))
    return _dedupe_paths(files)


def parquet_file_date(path) -> str:
    """parquet 1개가 담는 날짜 → 'YYYY-MM-DD'. 판단 불가면 ''.

    파일명을 먼저 보고(하루 1파일 규칙), 없으면 바로 위 두 단계 폴더명을 본다
    (hive 파티션 `date=20260427`). 루트 폴더명까지 뒤지지는 않는다 — 경로 어딘가의
    숫자를 날짜로 오인해 최신 파일을 건너뛰는 쪽이 훨씬 위험하기 때문이다."""
    p = Path(path)
    for text in [p.stem] + [parent.name for parent in list(p.parents)[:2]]:
        m = _PARQUET_DATE_RE.search(str(text or ""))
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return ""


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    out: list[Path] = []
    seen = set()
    for p in paths:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _step_meta_paths() -> list[Path]:
    db_root = _get_db_root()
    roots = []
    for root in (db_root, db_root / "Fab"):
        if root not in roots:
            roots.append(root)
    for root in _resolve_source_root_dirs("fab", FAB_ROOT):
        if root not in roots:
            roots.append(root)
    paths = []
    for root in roots:
        for name in ("Vehicle_matching.csv", "vehicle_matching.csv", "step_matching.csv", "matching_step.csv"):
            fp = root / name
            if fp.is_file() and fp not in paths:
                paths.append(fp)
    return paths


def _row_ci(row: dict, *names: str):
    lookup = {str(k or "").strip().lower(): v for k, v in (row or {}).items()}
    for name in names:
        key = str(name or "").strip().lower()
        if key in lookup:
            return lookup.get(key)
    return ""


def _read_step_meta_rows() -> list[dict]:
    paths = _step_meta_paths()
    sig = tuple((str(fp), fp.stat().st_mtime) for fp in paths)
    cached = _STEP_META_CACHE.get("rows")
    if cached and cached.get("sig") == sig:
        return cached.get("rows") or []
    rows: list[dict] = []
    for fp in paths:
        try:
            with open(fp, "r", encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    r = {str(k or "").strip(): (v if v is not None else "") for k, v in (row or {}).items()}
                    step_id = str(_row_ci(r, "step_id", "raw_step_id", "step") or "").strip()
                    func_step = (
                        _row_ci(r, "func_step", "function_step", "func step", "canonical_step", "step_function")
                        or ""
                    )
                    func_step = str(func_step or "").strip()
                    if not step_id or not func_step:
                        continue
                    rows.append({
                        "step_id": step_id,
                        "product": str(_row_ci(r, "product", "process_id", "prod") or "").strip(),
                        "function_step": func_step,
                        "func_step": func_step,
                        "canonical_step": str(_row_ci(r, "canonical_step") or "").strip(),
                        "module": str(_row_ci(r, "module", "area") or "").strip(),
                        "area": str(_row_ci(r, "area") or "").strip(),
                        "step_class": str(_row_ci(r, "step_class", "step_type") or "").strip(),
                    })
        except Exception as e:
            logger.warning(f"step meta CSV load failed {fp}: {e}")
    _STEP_META_CACHE["rows"] = {"sig": sig, "rows": rows}
    return rows


def lookup_step_meta(product: str = "", step_id: str = "") -> dict:
    """Return function-step metadata for a raw FAB/ET step_id.

    Preferred source is step_matching.csv(step_id, func_step). matching_step.csv
    is accepted as a compatibility fallback.
    """
    sid = str(step_id or "").strip()
    if not sid:
        return {}
    aliases = _product_match_keys(product)
    fallback = None
    for row in _read_step_meta_rows():
        if str(row.get("step_id") or "").strip() != sid:
            continue
        row_products = _product_cell_tokens(row.get("product"))
        if aliases and row_products and not any(row_product in aliases for row_product in row_products):
            if fallback is None:
                fallback = row
            continue
        return {k: v for k, v in row.items() if k not in ("step_id", "product") and v}
    if fallback:
        return {k: v for k, v in fallback.items() if k not in ("step_id", "product") and v}
    return {}


def latest_fab_step(product: str = "", root_lot_id: str = "", lot_id: str = "",
                    wafer_id: str = "", source_root: str = "") -> dict:
    """주어진 lot/wafer 의 FAB 최신 step_id. polars scan 에서 max(tkout_time/time).
    반환: {step_id, time, lot_id, root_lot_id, wafer_id} 또는 {} when not found.
    """
    try:
        import polars as pl
    except Exception:
        return {}
    cached = _latest_fab_step_from_lot_progress_cache(
        product=product,
        root_lot_id=root_lot_id,
        lot_id=lot_id,
        wafer_id=wafer_id,
    )
    if cached:
        return cached
    root_name = str(source_root or "").strip() or FAB_ROOT
    files = _parquet_files(root_name, product, source="fab")
    if not files:
        return {}
    try:
        lf = pl.scan_parquet([str(f) for f in files[-30:]], hive_partitioning=True)
    except Exception:
        try:
            lf = pl.scan_parquet([str(f) for f in files[-30:]])
        except Exception:
            return {}
    lf = _filter_valid_wafers(lf)
    schema = lf.collect_schema().names()
    filters = []
    prod_values = _data_product_values(product)
    if prod_values and "product" in schema:
        filters.append(pl.col("product").cast(pl.Utf8).str.to_uppercase().is_in(sorted(prod_values)))
    if root_lot_id and "root_lot_id" in schema:
        filters.append(pl.col("root_lot_id").cast(pl.Utf8) == str(root_lot_id))
    elif lot_id:
        lot_filters = [
            pl.col(c).cast(pl.Utf8) == str(lot_id)
            for c in ("lot_id", "fab_lot_id")
            if c in schema
        ]
        if lot_filters:
            expr = lot_filters[0]
            for e in lot_filters[1:]:
                expr = expr | e
            filters.append(expr)
    wafer_text = str(wafer_id or "").strip()
    wafer_values = parse_wafer_selection(wafer_text)
    if wafer_text and not _is_all_wafer_id(wafer_text) and not wafer_values:
        return {}
    if wafer_values and "wafer_id" in schema:
        wafer_expr = _wafer_filter_expr(pl, "wafer_id", wafer_values)
        if wafer_expr is not None:
            filters.append(wafer_expr)
    if filters:
        expr = filters[0]
        for e in filters[1:]:
            expr = expr & e
        lf = lf.filter(expr)
    time_col = "time" if "time" in schema else ("tkout_time" if "tkout_time" in schema else "tkin_time")
    cols = [c for c in ("step_id", time_col, "product", "lot_id", "fab_lot_id", "root_lot_id", "wafer_id") if c in schema]
    if "step_id" not in cols or time_col not in cols:
        return {}
    lf = lf.select(cols).sort(time_col, descending=True).head(1)
    try:
        df = lf.collect()
    except Exception:
        return {}
    if df.is_empty():
        return {}
    row = df.to_dicts()[0]
    step_id = row.get("step_id")
    meta = lookup_step_meta(product=product, step_id=step_id)
    return {
        "step_id": row.get("step_id"),
        "time": row.get(time_col),
        "product": row.get("product"),
        "lot_id": row.get("lot_id"),
        "fab_lot_id": row.get("fab_lot_id"),
        "root_lot_id": row.get("root_lot_id"),
        "wafer_id": row.get("wafer_id"),
        **meta,
    }


def _et_text_col(pl_module, col: str):
    """ET DB 의 lot 열 비교용 정규화 — 공백/대소문자 차이를 흡수한다."""
    return pl_module.col(col).cast(pl_module.Utf8, strict=False).str.strip_chars().str.to_uppercase()


def _et_lot_match_expr(pl_module, schema: list, root_text: str, lot_text: str):
    """root/lot 어느 칸에 적혀 있어도 찾는 lot 매칭식.

    ET DB 는 root_lot_id 가 비어 있거나, 사용자가 root 칸에 fab lot 을 적는
    경우가 있다. ET 측정시간 화면이 재조회로 흡수하던 호환을 여기서는 한 번의
    OR 로 처리한다 — 같은 lot 행에서 온 값이라 결과 범위는 root 기준과 같다.
    """
    values = [v for v in (str(root_text or "").strip(), str(lot_text or "").strip()) if v]
    if not values:
        return None
    exprs = []
    for col in ("root_lot_id", "lot_id", "fab_lot_id"):
        if col not in schema:
            continue
        base = _et_text_col(pl_module, col)
        for value in values:
            exprs.append(base == value.upper())
    if not exprs:
        return None
    expr = exprs[0]
    for e in exprs[1:]:
        expr = expr | e
    return expr


def _et_history_cache_path(product: str, source_root: str = "") -> Path:
    root = str(source_root or tracker_db_sources_config().get("analysis") or ET_ROOT).strip() or ET_ROOT
    name = _safe_id(f"{_cache_product_name(product)}__{root}")
    return _et_history_cache_dir() / f"{name}.parquet"


def _et_history_cache_meta_path(product: str, source_root: str = "") -> Path:
    return _et_history_cache_path(product, source_root).with_suffix(".json")


def _et_history_cache_config_key(product: str, source_root: str = "") -> str:
    import json
    payload = {
        "version": ET_HISTORY_CACHE_VERSION,
        "product": _cache_product_name(product).upper(),
        "source_root": str(source_root or tracker_db_sources_config().get("analysis") or ET_ROOT).strip(),
        "db_root": str(_get_db_root()),
        "recent_days": ET_HISTORY_RECENT_DAYS,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _et_history_cache_current(product: str, source_root: str = "") -> dict | None:
    prod = _cache_product_name(product)
    if not prod:
        return None
    root = str(source_root or tracker_db_sources_config().get("analysis") or ET_ROOT).strip() or ET_ROOT
    fp = _et_history_cache_path(prod, root)
    meta_fp = _et_history_cache_meta_path(prod, root)
    if not fp.is_file() or not meta_fp.is_file():
        if root != ET_ROOT:
            return _et_history_cache_current(prod, ET_ROOT)
        return None
    try:
        from core.utils import load_json
        meta = load_json(meta_fp, {})
    except Exception:
        meta = {}
    if not isinstance(meta, dict) or meta.get("version") != ET_HISTORY_CACHE_VERSION:
        return None
    if meta.get("config_key") != _et_history_cache_config_key(prod, root):
        return None
    try:
        import polars as pl
        lf = pl.scan_parquet(str(fp))
        schema = lf.collect_schema().names()
    except Exception as e:
        logger.warning("ET history cache open failed product=%s source=%s: %s", prod, root, e)
        return None
    required = {"step_id", "time", "pt_count"}
    if not required.issubset(set(schema)):
        return None
    return {"product": prod, "source_root": root, "path": fp, "meta": meta, "lf": lf}


def _et_history_source_signature(files: list[Path]) -> str:
    digest = hashlib.sha1()
    for fp in files:
        try:
            st = fp.stat()
            token = f"{fp}|{st.st_size}|{st.st_mtime_ns}\n"
        except OSError:
            token = f"{fp}|missing\n"
        digest.update(token.encode("utf-8", errors="replace"))
    return digest.hexdigest()


def _et_history_source_files(product: str, source_root: str) -> tuple[str, list[Path]]:
    root = str(source_root or "").strip() or ET_ROOT
    files = _parquet_files(root, product, source="et")
    if not files and root != ET_ROOT:
        fallback = _parquet_files(ET_ROOT, product, source="et")
        if fallback:
            logger.warning("ET history cache: source_root '%s' 에 파일이 없어 '%s' 로 폴백", root, ET_ROOT)
            return ET_ROOT, fallback
    return root, files


def _build_et_history_rows(product: str, files: list[Path]):
    """Aggregate source point rows into reusable product/LOT ET packages."""
    import polars as pl
    if not files:
        return pl.DataFrame()
    scan_files = [str(fp) for fp in files]
    try:
        lf = pl.scan_parquet(scan_files, hive_partitioning=True)
    except Exception:
        lf = pl.scan_parquet(scan_files)
    lf = _filter_valid_wafers(lf)
    schema = lf.collect_schema().names()
    product_col = _ci_col(schema, "product")
    aliases = _data_product_values(product)
    if aliases and product_col:
        lf = lf.filter(_et_text_col(pl, product_col).is_in(sorted(aliases)))

    source_cols = {
        "root_lot_id": _ci_col(schema, "root_lot_id"),
        "lot_id": _ci_col(schema, "lot_id"),
        "fab_lot_id": _ci_col(schema, "fab_lot_id"),
        "wafer_id": _ci_col(schema, "wafer_id", "wf_id"),
        "step_id": _ci_col(schema, "step_id"),
        "step_seq": _ci_col(schema, "step_seq"),
        "flat": _ci_col(schema, "flat", "flat_zone"),
        "time": _ci_col(schema, "time", "tkout_time", "tkin_time", "measure_time", "timestamp"),
    }
    if not source_cols["step_id"] or not source_cols["time"]:
        raise ValueError("ET DB schema requires step_id and time/tkout_time")
    if not any(source_cols[key] for key in ("root_lot_id", "lot_id", "fab_lot_id")):
        raise ValueError("ET DB schema requires root_lot_id/lot_id/fab_lot_id")

    exprs = []
    for canonical, source_col in source_cols.items():
        if source_col:
            exprs.append(pl.col(source_col).cast(pl.Utf8, strict=False).fill_null("").alias(canonical))
        else:
            exprs.append(pl.lit("").alias(canonical))
    point_cols: list[str] = []
    chip_x = _ci_col(schema, "chip_x_pos")
    chip_y = _ci_col(schema, "chip_y_pos")
    shot_x = _ci_col(schema, "shot_x")
    shot_y = _ci_col(schema, "shot_y")
    subitem = _ci_col(schema, "subitem_id")
    if chip_x and chip_y:
        exprs.extend([
            pl.col(chip_x).cast(pl.Utf8, strict=False).alias("_point_x"),
            pl.col(chip_y).cast(pl.Utf8, strict=False).alias("_point_y"),
        ])
        point_cols = ["_point_x", "_point_y"]
        if subitem:
            exprs.append(pl.col(subitem).cast(pl.Utf8, strict=False).alias("_subitem"))
            point_cols.append("_subitem")
    elif shot_x and shot_y:
        exprs.extend([
            pl.col(shot_x).cast(pl.Utf8, strict=False).alias("_point_x"),
            pl.col(shot_y).cast(pl.Utf8, strict=False).alias("_point_y"),
        ])
        point_cols = ["_point_x", "_point_y"]

    q = lf.select(exprs).filter(pl.col("step_id") != "")
    keys = ["root_lot_id", "lot_id", "fab_lot_id", "wafer_id", "step_id", "step_seq", "flat", "time"]
    pt_expr = (pl.struct(point_cols).n_unique() if point_cols else pl.len()).alias("pt_count")
    grouped = q.group_by(keys).agg(pt_expr)
    try:
        return grouped.collect(engine="streaming")
    except TypeError:
        return grouped.collect()


def refresh_et_history_cache(product: str, source_root: str = "", force_full: bool = False,
                             cancel_check: Callable[[], bool] | None = None) -> dict:
    """Build one product history once, then merge only the latest three source days.

    A missing/invalid cache always triggers a complete source build. Existing
    caches compare the source signature and, when changed, re-aggregate the
    latest three source dates (plus undated files) and replace matching package
    keys. Tracker scans can then filter/copy packages without reopening ET raw.
    """
    prod = _cache_product_name(product)
    if not prod:
        return {"ok": False, "error": "product is required"}
    if cancel_check and cancel_check():
        return {"ok": False, "cancelled": True, "product": prod,
                "error": "ET history cache build cancelled before scan"}
    requested_root = str(source_root or tracker_db_sources_config().get("analysis") or ET_ROOT).strip() or ET_ROOT
    actual_root, files = _et_history_source_files(prod, requested_root)
    if not files:
        return {"ok": False, "product": prod, "source_root": actual_root, "error": "ET source parquet not found"}
    source_sig = _et_history_source_signature(files)
    with _ET_HISTORY_CACHE_LOCK:
        current = _et_history_cache_current(prod, actual_root)
        meta_fp = _et_history_cache_meta_path(prod, actual_root)
        fp = _et_history_cache_path(prod, actual_root)
        old_meta = current.get("meta") if current else {}
        if current and not force_full and old_meta.get("source_signature") == source_sig:
            return {
                "ok": True, "product": prod, "source_root": actual_root,
                "path": str(fp), "skipped": True, "mode": "current",
                "row_count": int(old_meta.get("row_count") or 0),
                "max_file_date": old_meta.get("max_file_date", ""),
            }

        file_dates = {str(file): parquet_file_date(file) for file in files}
        known_dates = sorted(value for value in file_dates.values() if value)
        max_file_date = known_dates[-1] if known_dates else ""
        full_build = bool(force_full or current is None)
        cutoff = ""
        selected = list(files)
        if not full_build and max_file_date:
            latest = dt.date.fromisoformat(max_file_date)
            cutoff = (latest - dt.timedelta(days=ET_HISTORY_RECENT_DAYS - 1)).isoformat()
            selected = [
                file for file in files
                if not file_dates.get(str(file)) or file_dates[str(file)] >= cutoff
            ]
        try:
            if cancel_check and cancel_check():
                return {"ok": False, "cancelled": True, "product": prod,
                        "source_root": actual_root,
                        "error": "ET history cache build cancelled before scan"}
            recent_df = _build_et_history_rows(prod, selected)
            # Polars collect itself cannot be interrupted safely.  Honour the
            # request before promoting the newly collected frame so a stopped
            # job never publishes a partial or unwanted cache artifact.
            if cancel_check and cancel_check():
                return {"ok": False, "cancelled": True, "product": prod,
                        "source_root": actual_root,
                        "error": "ET history cache build cancelled after current scan batch"}
            if full_build:
                merged = recent_df
            else:
                import polars as pl
                old_df = pl.read_parquet(str(fp))
                keys = ["root_lot_id", "lot_id", "fab_lot_id", "wafer_id", "step_id", "step_seq", "flat", "time"]
                if recent_df.height:
                    remaining = old_df.join(recent_df.select(keys).unique(), on=keys, how="anti")
                    merged = pl.concat([remaining, recent_df], how="diagonal_relaxed")
                else:
                    merged = old_df
            if "time" in merged.columns:
                merged = merged.sort("time", descending=True, nulls_last=True)
            if cancel_check and cancel_check():
                return {"ok": False, "cancelled": True, "product": prod,
                        "source_root": actual_root,
                        "error": "ET history cache build cancelled before publish"}
            _et_history_cache_dir().mkdir(parents=True, exist_ok=True)
            tmp = fp.with_suffix(fp.suffix + ".tmp")
            merged.write_parquet(tmp)
            tmp.replace(fp)
            from core.utils import save_json
            meta = {
                "version": ET_HISTORY_CACHE_VERSION,
                "config_key": _et_history_cache_config_key(prod, actual_root),
                "product": prod,
                "source_root": actual_root,
                "built_at": dt.datetime.now().isoformat(timespec="seconds"),
                "built_epoch": time.time(),
                "mode": "full" if full_build else "recent_merge",
                "recent_days": ET_HISTORY_RECENT_DAYS,
                "recent_cutoff": cutoff,
                "source_signature": source_sig,
                "source_file_count": len(files),
                "scanned_file_count": len(selected),
                "max_file_date": max_file_date,
                "row_count": int(merged.height),
            }
            save_json(meta_fp, meta)
            return {"ok": True, "path": str(fp), **meta}
        except Exception as e:
            logger.warning("ET history cache build failed product=%s source=%s: %s", prod, actual_root, e, exc_info=True)
            return {"ok": False, "product": prod, "source_root": actual_root, "error": f"{type(e).__name__}: {e}"}


def et_history_packages_multi(product: str, specs: list, *, limit: int = 50,
                              source_root: str = "", since_date: str = "",
                              diag: dict | None = None) -> list[list] | None:
    """Read package histories from the product cache; ``None`` means no cache."""
    info = diag if isinstance(diag, dict) else {}
    current = _et_history_cache_current(product, source_root)
    if not current:
        return None
    try:
        import polars as pl
        lf = current["lf"]
        schema = lf.collect_schema().names()
        since = str(since_date or "").strip()[:10]
        if since and "time" in schema:
            lf = lf.filter(pl.col("time").cast(pl.Utf8, strict=False).str.slice(0, 10) >= since)
        lot_exprs = []
        for spec in specs or []:
            expr = _et_lot_match_expr(pl, schema, spec.get("root_lot_id", ""), spec.get("lot_id", ""))
            if expr is not None:
                lot_exprs.append(expr)
        if lot_exprs:
            combined = lot_exprs[0]
            for expr in lot_exprs[1:]:
                combined = combined | expr
            lf = lf.filter(combined)
        df = lf.collect()
        out: list[list] = []
        for spec in specs or []:
            sub = df.lazy()
            expr = _et_lot_match_expr(pl, schema, spec.get("root_lot_id", ""), spec.get("lot_id", ""))
            if expr is not None:
                sub = sub.filter(expr)
            wafers = parse_wafer_selection(str(spec.get("wafer_id") or ""))
            if wafers and "wafer_id" in schema:
                wafer_expr = _wafer_filter_expr(pl, "wafer_id", wafers)
                if wafer_expr is not None:
                    sub = sub.filter(wafer_expr)
            rows = sub.sort("time", descending=True).head(limit).collect().to_dicts()
            out.append([
                {
                    "wafer_id": row.get("wafer_id"), "step_id": row.get("step_id"),
                    "step_seq": row.get("step_seq"), "flat": row.get("flat"),
                    "time": row.get("time"), "pt_count": int(row.get("pt_count") or 0),
                    **lookup_step_meta(product=product, step_id=row.get("step_id")),
                }
                for row in rows
            ])
        meta = current["meta"]
        info.update({
            "cache_hit": True, "cache": "et_history", "files": 0,
            "source_root": current["source_root"], "max_file_date": meta.get("max_file_date", ""),
            "history_built_at": meta.get("built_at", ""), "history_mode": meta.get("mode", ""),
            "error": "",
        })
        if since and meta.get("max_file_date") and str(meta.get("max_file_date")) < since:
            info["incremental_skip"] = True
        return out
    except Exception as e:
        info["error"] = f"ET history cache 조회 실패: {e}"
        logger.warning("ET history cache query failed product=%s: %s", product, e, exc_info=True)
        return None


def et_history_packages(product: str = "", root_lot_id: str = "", lot_id: str = "",
                        wafer_id: str = "", limit: int = 50, source_root: str = "",
                        diag: dict | None = None, since_date: str = "") -> list | None:
    rows = et_history_packages_multi(
        product,
        [{"root_lot_id": root_lot_id, "lot_id": lot_id, "wafer_id": wafer_id}],
        limit=limit, source_root=source_root, since_date=since_date, diag=diag,
    )
    if rows is None:
        return None
    return rows[0] if rows else []


def et_history_cache_status(product: str = "", source_root: str = "") -> dict:
    if product:
        current = _et_history_cache_current(product, source_root)
        if current:
            return {"ok": True, "ready": True, "path": str(current["path"]), **current["meta"]}
        return {"ok": True, "ready": False, "product": _cache_product_name(product), "source_root": source_root}
    rows = []
    try:
        from core.utils import load_json
        for meta_fp in sorted(_et_history_cache_dir().glob("*.json")):
            meta = load_json(meta_fp, {})
            if not isinstance(meta, dict) or meta.get("version") != ET_HISTORY_CACHE_VERSION:
                continue
            rows.append({"path": str(meta_fp.with_suffix(".parquet")), **meta})
    except Exception:
        rows = []
    return {"ok": True, "ready": bool(rows), "recent_days": ET_HISTORY_RECENT_DAYS, "products": rows}


def _et_scan_context(pl, product: str, source_root: str, since_date: str, info: dict):
    """ET DB 파일 선정 + LazyFrame/스키마 준비. 실패/스킵이면 None.

    et_packages 와 et_packages_multi 가 공유한다 — 파일 목록·워터마크 스킵·
    폴백 루트 규칙이 두 벌로 갈리면 증분 스캔이 조용히 어긋난다."""
    info["files"] = 0
    info.setdefault("error", "")
    prod_text = str(product or "").strip()
    root_name = str(source_root or "").strip() or ET_ROOT
    files = _parquet_files(root_name, prod_text, source="et")
    if not files and root_name != ET_ROOT:
        # 톱니바퀴의 ET DB 폴더 설정이 실제 폴더와 어긋나도 ET 측정시간 화면과
        # 같은 기본 루트로 한 번 더 찾는다 — 설정 오타 하나로 추적 전체가
        # 조용히 "측정 없음" 이 되던 경로.
        files = _parquet_files(ET_ROOT, prod_text, source="et")
        if files:
            logger.warning("et_packages: source_root '%s' 에 파일이 없어 '%s' 로 폴백", root_name, ET_ROOT)
            root_name = ET_ROOT
    info["source_root"] = root_name
    if not files:
        info["error"] = (f"ET DB '{root_name}' 에서 product "
                         f"'{prod_text or '(미지정)'}' 파일을 찾지 못했습니다")
        return None
    file_dates = {str(f): parquet_file_date(f) for f in files}
    known_dates = sorted(d for d in file_dates.values() if d)
    info["max_file_date"] = known_dates[-1] if known_dates else ""
    since_text = str(since_date or "").strip()[:10]
    info["since_date"] = since_text
    info["files_skipped"] = 0
    if since_text:
        fresh = [
            f for f in files
            # 날짜를 못 읽은 파일은 건너뛰지 않는다 — 스캔 비용보다 누락이 비싸다.
            if not file_dates.get(str(f)) or str(file_dates.get(str(f))) >= since_text
        ]
        info["files_skipped"] = len(files) - len(fresh)
        files = fresh
        if not files:
            # 워터마크 이후 파일이 없다 = 새로 들어온 측정이 없다. parquet 을 열지
            # 않고 끝낸다 — 이 경로가 증분 스캔의 실제 이득이다.
            info["files"] = 0
            info["incremental_skip"] = True
            return None
    scan_files = [str(f) for f in files[-ET_PACKAGE_FILE_LIMIT:]]
    info["files"] = len(scan_files)
    try:
        lf = pl.scan_parquet(scan_files, hive_partitioning=True)
    except Exception:
        try:
            lf = pl.scan_parquet(scan_files)
        except Exception as e:
            logger.warning("et_packages scan_parquet 실패 product=%s: %s", prod_text, e)
            info["error"] = f"parquet 열기 실패: {e}"
            return None
    lf = _filter_valid_wafers(lf)
    try:
        schema = list(lf.collect_schema().names())
    except Exception as e:
        logger.warning("et_packages 스키마 조회 실패 product=%s: %s", prod_text, e)
        info["error"] = f"스키마 조회 실패: {e}"
        return None
    prod_values = _data_product_values(prod_text)
    if prod_values and "product" in schema:
        lf = lf.filter(_et_text_col(pl, "product").is_in(sorted(prod_values)))
    return lf, schema


def _et_package_grain(schema: list) -> tuple[list, list, str, str]:
    """(group_cols, point_cols, flat_col, time_col) — 패키지 집계 grain."""
    flat_col = "flat" if "flat" in schema else ("flat_zone" if "flat_zone" in schema else "")
    time_col = "time" if "time" in schema else ("tkout_time" if "tkout_time" in schema else "tkin_time")
    group_cols = []
    # wafer_id is part of the package grain.  Without it an ``all``/range query
    # merged equal step packages from multiple wafers into one history entry.
    for c in ("wafer_id", "step_id", "step_seq", flat_col, time_col):
        if c in schema:
            group_cols.append(c)
    if {"chip_x_pos", "chip_y_pos"} <= set(schema):
        point_cols = ["chip_x_pos", "chip_y_pos"]
        if "subitem_id" in schema:
            point_cols.append("subitem_id")
    elif {"shot_x", "shot_y"} <= set(schema):
        point_cols = ["shot_x", "shot_y"]
    else:
        point_cols = []
    return group_cols, point_cols, flat_col, time_col


def _et_keep_columns(schema: list) -> list:
    group_cols, point_cols, _flat, _time = _et_package_grain(schema)
    return list(dict.fromkeys(group_cols + point_cols))


def _et_aggregate_packages(pl, lf, schema: list, product: str, limit: int,
                           info: dict | None = None) -> list:
    """필터가 끝난 프레임 → 패키지 목록. 집계 grain 은 한 곳에서만 정의한다."""
    group_cols, point_cols, flat_col, time_col = _et_package_grain(schema)
    if not group_cols:
        return []
    keep = list(dict.fromkeys(group_cols + point_cols))
    # 집계에 실제로 쓰는 열만 남긴다 — ET 원본은 item_id/et_value 등 넓은 long
    # 포맷이라 projection 을 명시해야 읽는 양이 실제로 줄어든다.
    lf = lf.select([pl.col(c) for c in keep])
    pt_expr = (pl.struct(point_cols).n_unique() if point_cols else pl.len()).alias("pt_count")
    lf_grp = lf.group_by(group_cols).agg(pt_expr)
    try:
        sort_col = time_col if time_col in group_cols else group_cols[0]
        df = lf_grp.sort(sort_col, descending=True).head(limit).collect()
    except Exception as e:
        logger.warning("et_packages 집계 실패 product=%s: %s", product, e)
        # "측정 없음" 과 "조회 실패" 를 호출자가 구분해야 한다 (ET 추적 스캔).
        if isinstance(info, dict):
            info["error"] = f"ET DB 조회 실패: {e}"
        return []
    if df.is_empty():
        return []
    out = []
    for r in df.to_dicts():
        meta = lookup_step_meta(product=product, step_id=r.get("step_id"))
        out.append({
            "wafer_id": r.get("wafer_id"),
            "step_id": r.get("step_id"),
            "step_seq": r.get("step_seq"),
            "flat": r.get(flat_col),
            "time": r.get(time_col),
            "pt_count": int(r.get("pt_count") or 0),
            **meta,
        })
    return out


def et_packages_multi(product: str, specs: list, *, limit: int = 50,
                      source_root: str = "", since_date: str = "",
                      diag: dict | None = None) -> list[list]:
    """여러 lot 행을 ET DB **한 번만 읽고** 처리한다. 반환값은 spec 순서대로의
    ``et_packages`` 결과 리스트(내용 동일).

    ET DB 의 lot 비교는 cast+strip+upper 정규화라 parquet predicate pushdown 이
    걸리지 않는다 — lot 하나만 조회해도 대상 파일 전체를 읽는다. 그래서 이슈에
    lot 행이 N 개면 예전 경로는 그 전체 읽기를 **N 번** 반복했고, 사내처럼 ET DB
    가 큰 환경에서 이슈 하나 스캔이 몇 분씩 걸리던 주원인이 이것이다.

    여기서는 모든 lot 을 OR 로 묶어 한 번만 읽어 메모리에 올린 뒤, lot 별
    필터·집계는 **같은 식으로 메모리에서** 다시 수행한다. 집계 grain 을 건드리지
    않으므로 결과는 per-lot 조회와 동일하다.

    ``specs``: [{"root_lot_id": .., "lot_id": .., "wafer_id": ..}, ...]
    실패하면 빈 리스트를 반환하고 호출자가 per-lot 경로로 폴백한다."""
    info = diag if isinstance(diag, dict) else {}
    specs = [s for s in (specs or []) if isinstance(s, dict)]
    if not specs:
        return []
    try:
        import polars as pl
    except Exception as e:
        info["error"] = f"polars 미설치: {e}"
        return []
    ctx = _et_scan_context(pl, product, source_root, since_date, info)
    if ctx is None:
        return []
    lf, schema = ctx

    lot_exprs = []
    for spec in specs:
        expr = _et_lot_match_expr(pl, schema,
                                  str(spec.get("root_lot_id") or ""),
                                  str(spec.get("lot_id") or ""))
        if expr is not None:
            lot_exprs.append(expr)
    if lot_exprs:
        combined = lot_exprs[0]
        for e in lot_exprs[1:]:
            combined = combined | e
        lf = lf.filter(combined)

    # lot 별 재필터에 필요한 열까지 포함해 한 번만 읽는다. 집계는 하지 않는다 —
    # grain 을 바꾸지 않기 위해 per-lot 집계를 아래에서 그대로 다시 돌린다.
    keep = _et_keep_columns(schema)
    lot_cols = [c for c in ("root_lot_id", "lot_id", "fab_lot_id") if c in schema]
    select_cols = list(dict.fromkeys([*keep, *lot_cols]))
    try:
        df = lf.select([pl.col(c) for c in select_cols]).collect()
    except Exception as e:
        logger.warning("et_packages_multi 배치 수집 실패 product=%s: %s", product, e)
        info["error"] = f"ET DB 조회 실패: {e}"
        return []

    out: list[list] = []
    for spec in specs:
        sub = df.lazy()
        expr = _et_lot_match_expr(pl, schema,
                                  str(spec.get("root_lot_id") or ""),
                                  str(spec.get("lot_id") or ""))
        if expr is not None:
            sub = sub.filter(expr)
        wafer_values = parse_wafer_selection(str(spec.get("wafer_id") or ""))
        if wafer_values and "wafer_id" in schema:
            wafer_expr = _wafer_filter_expr(pl, "wafer_id", wafer_values)
            if wafer_expr is not None:
                sub = sub.filter(wafer_expr)
        out.append(_et_aggregate_packages(pl, sub, schema, product, limit, info))
    return out


def et_packages(product: str = "", root_lot_id: str = "", lot_id: str = "",
                wafer_id: str = "", limit: int = 50, source_root: str = "",
                diag: dict | None = None, since_date: str = "") -> list:
    """ET 측정 패키지 목록. 같은 (step_id, step_seq, flat_zone, tkout_time) 튜플을 하나로 묶고 pt 수 집계.
    반환: [{step_id, step_seq, flat, time, pt_count}] 시간 역순.

    ET 측정시간 화면과 같은 범위(최근 60개 파일)와 Point 정의를 사용한다.
    Point 는 고유 (chip_x_pos, chip_y_pos, subitem_id), 구 스키마에서는
    (shot_x, shot_y) 수이며 좌표가 없을 때만 행 수를 사용한다.

    ``diag`` dict 를 넘기면 조회 진단(스캔한 파일 수·실패 사유)을 채워 돌려준다.
    ET 추적 스캔이 "측정 없음" 과 "조회 실패" 를 구분하기 위해 쓴다 — 예전에는
    어느 실패든 빈 리스트라 화면에 이유 없이 아무것도 안 나왔다.

    ``since_date`` ('YYYY-MM-DD') 를 주면 그 날짜 **이후** 파일만 읽는다. ET 측정은
    쌓이기만 하고 지워지지 않으므로, 이미 읽은 날짜를 매번 다시 여는 것은 낭비다.
    날짜를 못 읽는 파일은 항상 포함한다. 남는 파일이 하나도 없으면 parquet 을
    아예 열지 않고 빈 리스트 + ``diag['incremental_skip']=True`` 로 끝낸다 —
    호출자는 이것을 "조회 실패" 가 아니라 "새 측정 없음" 으로 읽어야 한다.
    ``diag['max_file_date']`` 는 (건너뛴 것 포함) 후보 파일 중 가장 최신 날짜로,
    다음 스캔의 워터마크가 된다.
    """
    info = diag if isinstance(diag, dict) else {}
    info["files"] = 0
    info["error"] = ""
    try:
        import polars as pl
    except Exception as e:
        info["error"] = f"polars 미설치: {e}"
        return []
    ctx = _et_scan_context(pl, product, source_root, since_date, info)
    if ctx is None:
        return []
    lf, schema = ctx
    root_text = str(root_lot_id or "").strip()
    lot_text = str(lot_id or "").strip()
    lot_expr = _et_lot_match_expr(pl, schema, root_text, lot_text)
    if lot_expr is not None:
        lf = lf.filter(lot_expr)
    elif root_text or lot_text:
        info["error"] = "ET DB 스키마에 root_lot_id/lot_id/fab_lot_id 열이 없습니다"
        return []
    wafer_text = str(wafer_id or "").strip()
    wafer_values = parse_wafer_selection(wafer_text)
    if wafer_text and not _is_all_wafer_id(wafer_text) and not wafer_values:
        info["error"] = f"wafer 선택 '{wafer_text}' 을 해석하지 못했습니다"
        return []
    if wafer_values and "wafer_id" in schema:
        wafer_expr = _wafer_filter_expr(pl, "wafer_id", wafer_values)
        if wafer_expr is not None:
            lf = lf.filter(wafer_expr)
    if not _et_package_grain(schema)[0]:
        info["error"] = "ET DB 스키마에 step_id/step_seq/시간 열이 없습니다"
        return []
    return _et_aggregate_packages(pl, lf, schema, product, limit, info)


def _seq_sort_key(v) -> tuple[int, str]:
    try:
        return (0, f"{int(v):06d}")
    except Exception:
        return (1, str(v or ""))


def summarize_et_steps(packages: list) -> list[dict]:
    grouped: dict[tuple[str, str], dict] = {}
    for pkg in packages or []:
        step_id = str(pkg.get("step_id") or "").strip()
        func = str(pkg.get("function_step") or pkg.get("func_step") or "").strip()
        key = (step_id, func)
        row = grouped.setdefault(key, {
            "step_id": step_id,
            "function_step": func,
            "func_step": func,
            "step_seqs": set(),
            "seq_points": {},
            "flats": set(),
            "pt_count": 0,
            "package_count": 0,
            "last_time": "",
        })
        seq = pkg.get("step_seq")
        if seq is not None and seq != "":
            row["step_seqs"].add(seq)
            seq_key = str(seq)
            row["seq_points"][seq_key] = int(row["seq_points"].get(seq_key) or 0) + int(pkg.get("pt_count") or 0)
        flat = pkg.get("flat")
        if flat:
            row["flats"].add(str(flat))
        row["pt_count"] += int(pkg.get("pt_count") or 0)
        row["package_count"] += 1
        cur_time = str(pkg.get("time") or "")
        if cur_time and cur_time > str(row.get("last_time") or ""):
            row["last_time"] = cur_time
    out = []
    for row in grouped.values():
        if int(row.get("pt_count") or 0) <= 0:
            continue
        seqs = sorted(row.pop("step_seqs"), key=_seq_sort_key)
        seq_point_map = row.pop("seq_points", {}) or {}
        seq_points = [
            {"seq": seq, "pt_count": int(seq_point_map.get(str(seq)) or 0)}
            for seq in seqs
            if int(seq_point_map.get(str(seq)) or 0) > 0
        ]
        seqs = [p["seq"] for p in seq_points]
        flats = sorted(row.pop("flats"))
        seq_combo = ", ".join(str(x) for x in seqs)
        func = row.get("function_step") or ""
        label = f"{row.get('step_id') or '-'} > {func or 'function step 미등록'}"
        display_label = f"{func}({row.get('step_id') or '-'})" if func else str(row.get("step_id") or "-")
        seq_pt_combo = ",".join(f"seq{x['seq']}({x['pt_count']}pt)" for x in seq_points)
        out.append({
            **row,
            "step_seqs": seqs,
            "seq_points": seq_points,
            "step_seq_combo": seq_combo,
            "seq_pt_combo": seq_pt_combo,
            "flats": flats,
            "flat_combo": ", ".join(flats),
            "label": label,
            "display_label": display_label,
        })
    return sorted(out, key=lambda r: str(r.get("last_time") or ""), reverse=True)


def format_et_packages(packages: list, limit: int = 5) -> str:
    parts = []
    for row in summarize_et_steps(packages)[:limit]:
        seq = row.get("seq_pt_combo") or "step_seq 상세 없음"
        parts.append(f"{row.get('display_label') or row.get('label') or '-'} {seq}".strip())
    return "  ".join(parts)


def check_et_measured(root_lot_id: str = "", product: str = "", lot_id: str = "",
                      wafer_id: str = "", source_root: str = "") -> dict:
    packages = et_packages(
        product=product,
        root_lot_id=root_lot_id,
        lot_id=lot_id,
        wafer_id=wafer_id,
        limit=20,
        source_root=source_root,
    )
    latest = packages[0] if packages else {}
    summary = summarize_et_steps(packages)
    return {
        "et_measured": bool(latest),
        "et_last_seq": latest.get("step_seq"),
        "et_last_time": latest.get("time"),
        "et_last_step": latest.get("step_id"),
        "et_last_function_step": latest.get("function_step") or latest.get("func_step") or "",
        "et_step_summary": summary,
        "et_step_seq_summary": "; ".join(
            f"{r.get('label')} · seq {r.get('step_seq_combo') or '-'}"
            for r in summary[:5]
        ),
        "et_recent_formatted": format_et_packages(packages),
    }


def lot_step_snapshot(product: str = "", root_lot_id: str = "", lot_id: str = "",
                     wafer_id: str = "", source: str = "auto", source_root: str = "") -> dict:
    """카테고리 소스(fab/et/both/auto) 별 snapshot.
    auto: 둘 다 시도.
    """
    src = (source or "auto").lower()
    out = {}
    if src in ("fab", "both", "auto"):
        fab = latest_fab_step(product=product, root_lot_id=root_lot_id,
                              lot_id=lot_id, wafer_id=wafer_id,
                              source_root=source_root)
        if fab:
            out["fab"] = fab
    if src in ("et", "both", "auto"):
        et = et_packages(product=product, root_lot_id=root_lot_id,
                         lot_id=lot_id, wafer_id=wafer_id, limit=20,
                         source_root=source_root)
        if et:
            out["et"] = et
    return out


def snapshot_row_fields(snapshot: dict) -> dict:
    """Tracker LOT_WF row 렌더용 요약 필드."""
    snap = snapshot or {}
    fab = (snap.get("fab") or {})
    et = (snap.get("et") or [])
    latest_et = et[0] if et else {}
    current_step = fab.get("step_id") or latest_et.get("step_id") or ""
    current_function_step = (
        fab.get("function_step") or fab.get("func_step")
        or latest_et.get("function_step") or latest_et.get("func_step")
        or ""
    )
    step_seq = latest_et.get("step_seq")
    et_summary = summarize_et_steps(et)
    last_move_at = fab.get("time") or latest_et.get("time") or ""
    return {
        "current_step": current_step,
        "current_function_step": current_function_step,
        "function_step": current_function_step,
        "func_step": current_function_step,
        "current_step_seq": step_seq,
        "step_seq": step_seq,
        "et_measured": bool(latest_et),
        "et_last_seq": latest_et.get("step_seq"),
        "et_last_time": latest_et.get("time"),
        "et_last_step": latest_et.get("step_id"),
        "et_last_function_step": latest_et.get("function_step") or latest_et.get("func_step") or "",
        "et_step_summary": et_summary,
        "et_step_seq_summary": "; ".join(
            f"{r.get('label')} · seq {r.get('step_seq_combo') or '-'}"
            for r in et_summary[:5]
        ),
        "et_recent_formatted": format_et_packages(et),
        "last_move_at": last_move_at,
        "et_package_count": len(et),
    }


_STEP_ID_RE = re.compile(r"^([A-Z]{2})(?:(\d{6}))?(\d{6})$")


def _parse_step_id(s: str):
    """step_id 포맷 '대문자2 + 숫자6 + 숫자6' 파싱.
    반환: (prefix, head_num, tail_num) or None (형식이 다르면).
    비교 시 prefix+head 가 같을 때 tail 6자리 숫자로 진행도 판정.
    """
    if not isinstance(s, str):
        return None
    m = _STEP_ID_RE.match(s.strip())
    if not m:
        return None
    family = int(m.group(2)) if m.group(2) else None
    return (m.group(1), family, int(m.group(3)))


def _fab_step_reached(current: str, target: str) -> bool:
    """current step_id 가 target step_id 이상인지 — '대문자2+숫자6+숫자6' 포맷일 때 뒤 6자리 숫자 비교.
    prefix+head 가 다르면 (= 완전히 다른 단계) 무시 — False 반환.
    포맷 이탈 시 문자열 equality 로 폴백.
    """
    cp = _parse_step_id(current)
    tp = _parse_step_id(target)
    if cp and tp:
        # 앞 prefix(+family) 가 같을 때만 비교. AA100150 같은 단순형은 prefix 기준.
        same_family = cp[1] is None or tp[1] is None or cp[1] == tp[1]
        if cp[0] == tp[0] and same_family:
            return cp[2] >= tp[2]
        return False
    # 폴백 — 문자열 equality.
    return (current or "") == (target or "") and bool(current)


def _parse_iso(value: str) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", ""))
    except Exception:
        return None


def _minutes_since(value: str, now: dt.datetime) -> float:
    prev = _parse_iso(value)
    if not prev:
        return 0.0
    return max(0.0, (now - prev).total_seconds() / 60.0)


def _normalize_seq(value) -> str:
    text = str(value or "").strip()
    text = text.strip("%").strip()
    if text.lower().startswith("step_seq"):
        text = text[len("step_seq"):].strip()
    if text.lower().startswith("seq"):
        text = text[3:].strip()
    text = text.strip("%").strip()
    return text


def _parse_et_seq_filter(value) -> list[list[str]]:
    if isinstance(value, (list, tuple, set)):
        raw = []
        for item in value:
            raw.extend(str(item or "").replace(";", ",").replace(" ", ",").split(","))
        groups = [raw]
    else:
        text = str(value or "").strip()
        groups = re.split(r"(?i)\bOR\b|\|\|", text) if text else []
    out = []
    for group in groups:
        clause = str(group or "")
        clause = re.sub(r"(?i)\bAND\b", ",", clause)
        raw = clause.replace(";", ",").replace("+", ",").replace("%", "").replace("(", " ").replace(")", " ").split(",")
        parts = []
        for item in raw:
            parts.extend(str(item or "").split())
        seqs = []
        seen = set()
        for item in parts:
            seq = _normalize_seq(item)
            if not seq or seq in seen:
                continue
            seen.add(seq)
            seqs.append(seq)
        if seqs:
            out.append(seqs)
    return out


def _parse_et_step_filter(value) -> list[str]:
    out = []
    seen = set()
    for item in str(value or "").replace(";", ",").split(","):
        token = item.strip().lower()
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _matches_et_step_filter(row: dict, target_step: str) -> bool:
    tokens = _parse_et_step_filter(target_step)
    if not tokens:
        return True
    candidates = [
        str(row.get("step_id") or "").strip().lower(),
        str(row.get("function_step") or row.get("func_step") or "").strip().lower(),
        str(row.get("display_label") or "").strip().lower(),
    ]
    return any(any(token == cand or token in cand for cand in candidates if cand) for token in tokens)


def _seq_points_for_row(row: dict) -> list[dict]:
    points = row.get("seq_points") or []
    if points:
        return [
            {"seq": p.get("seq"), "pt_count": int(p.get("pt_count") or 0)}
            for p in points
            if int(p.get("pt_count") or 0) > 0
        ]
    seqs = row.get("step_seqs") or []
    total = int(row.get("pt_count") or 0)
    if len(seqs) == 1:
        return [{"seq": seqs[0], "pt_count": total}] if total > 0 else []
    return []


def _selected_seq_points(row: dict, seq_filter: list[list[str]]) -> list[dict]:
    points = _seq_points_for_row(row)
    if not seq_filter:
        return points
    by_seq = {_normalize_seq(p.get("seq")): p for p in points}
    for group in seq_filter:
        if all(seq in by_seq for seq in group):
            return [by_seq[seq] for seq in group]
    return []


def _format_et_summary_row(row: dict, seq_points: list[dict] | None = None) -> str:
    label = row.get("display_label")
    if not label:
        func = row.get("function_step") or row.get("func_step") or ""
        label = f"{func}({row.get('step_id') or '-'})" if func else str(row.get("step_id") or "-")
    points = seq_points if seq_points is not None else _seq_points_for_row(row)
    seq_text = ",".join(f"seq{p.get('seq')}({int(p.get('pt_count') or 0)}pt)" for p in points)
    if not seq_text:
        seq_text = row.get("seq_pt_combo") or ""
    return f"{label} {seq_text}".strip()


def _et_state_step_key(row: dict) -> str:
    return str(row.get("step_id") or row.get("function_step") or row.get("func_step") or "").strip()


def _et_state_seq_key(seq_points: list[dict]) -> str:
    return ",".join(
        f"{_normalize_seq(p.get('seq'))}:{int(p.get('pt_count') or 0)}"
        for p in seq_points
    )


def _evaluate_et_watch(
    snapshot: dict,
    watch: dict,
    *,
    now_iso: str = "",
    stable_delay_minutes: int = 180,
) -> dict:
    now_iso = now_iso or dt.datetime.now().isoformat(timespec="seconds")
    now = _parse_iso(now_iso) or dt.datetime.now()
    try:
        delay = int((watch or {}).get("et_stable_delay_minutes") or stable_delay_minutes or 180)
    except Exception:
        delay = 180
    delay = max(1, min(24 * 60, delay))
    et = (snapshot or {}).get("et") or []
    summary = summarize_et_steps(et)
    target_step = (watch or {}).get("target_et_step_id") or ""
    target_seqs = _parse_et_seq_filter((watch or {}).get("target_et_seqs") or "")

    states = dict((watch or {}).get("et_step_states") or {})
    notified_new = set(str(x or "") for x in ((watch or {}).get("notified_new_et_step_keys") or []))
    initialized = bool((watch or {}).get("et_watch_initialized"))
    fire = False
    reasons = []
    fired_step_id = ""
    observed_step_keys = []

    for row in summary:
        if not _matches_et_step_filter(row, target_step):
            continue
        seq_points = _selected_seq_points(row, target_seqs)
        if target_seqs and not seq_points:
            continue
        step_key = _et_state_step_key(row)
        if not step_key:
            continue
        seq_key = _et_state_seq_key(seq_points)
        if not seq_key:
            continue
        observed_step_keys.append(step_key)
        prev = states.get(step_key) if isinstance(states.get(step_key), dict) else {}
        if not prev:
            states[step_key] = {
                "seq_key": seq_key,
                "first_seen_at": now_iso,
                "last_changed_at": now_iso,
                "last_seen_at": now_iso,
                "summary": _format_et_summary_row(row, seq_points),
                "stable_fired_seq_keys": [],
            }
            if initialized and step_key not in notified_new:
                fire = True
                notified_new.add(step_key)
                fired_step_id = str(row.get("step_id") or fired_step_id or "")
                reasons.append(f"new ET step detected: {_format_et_summary_row(row, seq_points)}")
            continue
        if str(prev.get("seq_key") or "") != seq_key:
            states[step_key] = {
                **prev,
                "seq_key": seq_key,
                "last_changed_at": now_iso,
                "last_seen_at": now_iso,
                "summary": _format_et_summary_row(row, seq_points),
            }
            continue
        stable_fired = set(str(x or "") for x in (prev.get("stable_fired_seq_keys") or []))
        if seq_key not in stable_fired and _minutes_since(prev.get("last_changed_at") or "", now) >= delay:
            fire = True
            stable_fired.add(seq_key)
            fired_step_id = str(row.get("step_id") or fired_step_id or "")
            reasons.append(f"ET measurement stable {delay}m: {_format_et_summary_row(row, seq_points)}")
            prev["stable_fired_seq_keys"] = list(stable_fired)
            prev["last_stable_fired_at"] = now_iso
        prev["last_seen_at"] = now_iso
        prev["summary"] = _format_et_summary_row(row, seq_points)
        states[step_key] = prev

    # Keep watch state bounded; recent observed steps first, then existing order.
    keep_keys = list(dict.fromkeys(observed_step_keys + list(states.keys())))[:50]
    states = {k: states[k] for k in keep_keys if k in states}
    updates = {
        "last_observed_et_count": len(et),
        "last_observed_et_step_keys": observed_step_keys,
        "et_step_states": states,
        "notified_new_et_step_keys": list(notified_new)[-100:],
        "et_watch_initialized": True,
    }
    if fire:
        updates["last_fired_at"] = now_iso
        updates["last_fired_step_id"] = fired_step_id
        updates["last_fired_et_signature"] = "; ".join(reasons)
    return {
        "fire": fire,
        "reason": "; ".join(reasons),
        "new_step_id": fired_step_id or None,
        "et_count": len(et),
        "et_recent_formatted": format_et_packages(et) if et else "",
        "et_step_summary": summary,
        "watch_updates": updates,
    }


def compare_to_watch(
    snapshot: dict,
    watch: dict,
    *,
    now_iso: str = "",
    et_stable_delay_minutes: int = 180,
) -> dict:
    """snapshot 결과와 watch 기준을 비교해 fire 여부 판정.
    watch: {source: "fab"|"et", target_step_id?, fired_target_step_ids?,
            last_observed_step?, last_observed_et_count?}
    v9.0.0:
      - FAB 모드: step_id '대문자2+숫자6+숫자6' 중 prefix+head 동일 + tail 숫자가 target 이상이면 fire.
        완전히 다른 step_id 로 바뀐 경우(prefix/head 다름) 무시.
      - ET 모드: 새 step_id 는 1회 알림, 동일 step 의 seq/pt 구성이 설정된 시간 동안
        변하지 않으면 "측정 완료" 알림. target_et_step_id/target_et_seqs 로 필터 가능.
    """
    source = ((watch or {}).get("source") or "fab").lower()
    fire = False
    reasons = []
    fab = (snapshot or {}).get("fab") or {}
    target_step = (watch or {}).get("target_step_id") or ""
    last_step = (watch or {}).get("last_observed_step") or ""
    cur_step = fab.get("step_id") or ""
    fired_targets = {str(v or "").strip().upper() for v in ((watch or {}).get("fired_target_step_ids") or [])}
    target_key = str(target_step or "").strip().upper()

    if source == "fab":
        if target_step:
            if target_key not in fired_targets and _fab_step_reached(cur_step, target_step):
                fire = True
                reasons.append(f"FAB step reached: {cur_step} ≥ {target_step}")
        elif cur_step and last_step and cur_step != last_step:
            # target 미지정이어도 step 이 진행되면 알림 (선택적).
            fire = True
            reasons.append(f"FAB step changed: {last_step} → {cur_step}")
    else:  # et
        return _evaluate_et_watch(
            snapshot,
            watch,
            now_iso=now_iso,
            stable_delay_minutes=et_stable_delay_minutes,
        )

    et_all = (snapshot or {}).get("et") or []
    return {
        "fire": fire,
        "reason": "; ".join(reasons) if reasons else "",
        "new_step_id": cur_step if fire and source == "fab" else None,
        "et_count": len(et_all),
        "et_recent_formatted": format_et_packages(et_all) if et_all else "",
        "et_step_summary": summarize_et_steps(et_all),
        "watch_updates": {},
    }
