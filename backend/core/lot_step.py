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
import logging
import re
import threading
import time
from pathlib import Path
from typing import Optional, Iterable

logger = logging.getLogger("flow.lot_step")

FAB_ROOT = "1.RAWDATA_DB_FAB"
ET_ROOT = "1.RAWDATA_DB_ET"
DEFAULT_MONITOR_CATEGORY = "Monitor"
DEFAULT_ANALYSIS_CATEGORY = "Analysis"
_STEP_META_CACHE: dict = {}
ET_LOT_CACHE_VERSION = 1
ET_LOT_CACHE_DEFAULT_MINUTES = 30
ET_LOT_CACHE_MIN_MINUTES = 30
ET_LOT_CACHE_MAX_MINUTES = 60
_ET_LOT_CACHE_THREAD: threading.Thread | None = None
_ET_LOT_CACHE_STARTED = False
_ET_LOT_CACHE_STOP = threading.Event()
_ET_LOT_CACHE_LOCK = threading.Lock()


def _get_db_root() -> Path:
    from core.paths import PATHS
    try:
        from app_v2.shared.source_adapter import resolve_existing_root
        return resolve_existing_root("db", PATHS.db_root)
    except Exception:
        return PATHS.db_root


def _settings_file() -> Path:
    from core.paths import PATHS
    return PATHS.data_root / "settings.json"


def _et_lot_cache_dir() -> Path:
    from core.paths import PATHS
    return PATHS.data_root / "tracker" / "et_lot_cache"


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
                parts.append(item)
                continue
            step = 1 if end >= start else -1
            parts.extend(str(v) for v in range(start, end + step, step))
        else:
            parts.append(item)
    out = []
    seen = set()
    for item in parts:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


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


def _scan_source_files(root_name: str, product: str = "", source: str = "auto"):
    try:
        import polars as pl
    except Exception:
        return None
    files = _parquet_files(root_name, product, source=source)
    if not files:
        return None
    try:
        return pl.scan_parquet([str(f) for f in files[-30:]], hive_partitioning=True)
    except Exception:
        try:
            return pl.scan_parquet([str(f) for f in files[-30:]])
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
        return pl.scan_parquet([str(f) for f in files], hive_partitioning=True)
    except Exception:
        try:
            return pl.scan_parquet([str(f) for f in files])
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
        lf = pl.scan_parquet(str(fp))
    except Exception as e:
        logger.warning("ET lot cache scan failed product=%s source=%s: %s", prod, root, e)
        return None
    return {"product": prod, "source_root": root, "path": fp, "meta": meta, "lf": lf}


def _sort_cache_values(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: (
        str(r.get("value") or "").upper(),
        str(r.get("type") or ""),
    ))


def et_lot_candidates_from_cache(product: str = "", source_root: str = "", prefix: str = "",
                                 limit: int = 200) -> list[dict]:
    current = _et_lot_cache_current(product, source_root)
    if not current:
        return []
    try:
        import polars as pl
    except Exception:
        return []
    try:
        limit = max(1, min(500, int(limit or 200)))
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

    add_col("root_lot_id", "root_lot_id")
    add_col("fab_lot_id", "fab_lot_id")
    add_col("lot_id", "lot_id")
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
        from core.runtime_limits import heavy_background_jobs_enabled
        if not heavy_background_jobs_enabled():
            logger.info("Tracker ET lot cache scheduler disabled by resource profile")
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
                      prefix: str = "", limit: int = 200) -> list[dict]:
    """Return root_lot_id/fab_lot_id/lot_id candidates for Tracker row entry."""
    if _source_kind(source, source_root) == "et":
        cached = et_lot_candidates_from_cache(
            product=product,
            source_root=source_root or tracker_db_sources_config().get("analysis") or ET_ROOT,
            prefix=prefix,
            limit=limit,
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
        for col in ("root_lot_id", "fab_lot_id", "lot_id"):
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
    return [text] if text else [""]


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
        for name in ("step_matching.csv", "matching_step.csv"):
            fp = root / name
            if fp.is_file() and fp not in paths:
                paths.append(fp)
    return paths


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
                    step_id = (r.get("step_id") or r.get("raw_step_id") or "").strip()
                    func_step = (
                        r.get("func_step")
                        or r.get("function_step")
                        or r.get("canonical_step")
                        or ""
                    ).strip()
                    if not step_id or not func_step:
                        continue
                    rows.append({
                        "step_id": step_id,
                        "product": (r.get("product") or "").strip(),
                        "function_step": func_step,
                        "func_step": func_step,
                        "canonical_step": (r.get("canonical_step") or "").strip(),
                        "module": (r.get("module") or r.get("area") or "").strip(),
                        "area": (r.get("area") or "").strip(),
                        "step_class": (r.get("step_class") or r.get("step_type") or "").strip(),
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
    aliases = _product_aliases(product)
    fallback = None
    for row in _read_step_meta_rows():
        if str(row.get("step_id") or "").strip() != sid:
            continue
        row_product = str(row.get("product") or "").strip().upper()
        if aliases and row_product and row_product not in aliases:
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
    wafer_values = parse_wafer_selection(wafer_id)
    if wafer_values and "wafer_id" in schema:
        filters.append(pl.col("wafer_id").cast(pl.Utf8).is_in(wafer_values))
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


def et_packages(product: str = "", root_lot_id: str = "", lot_id: str = "",
                wafer_id: str = "", limit: int = 50, source_root: str = "") -> list:
    """ET 측정 패키지 목록. 같은 (step_id, step_seq, flat_zone, tkout_time) 튜플을 하나로 묶고 pt 수 집계.
    반환: [{step_id, step_seq, flat, time, pt_count}] 시간 역순.
    """
    try:
        import polars as pl
    except Exception:
        return []
    root_name = str(source_root or "").strip() or ET_ROOT
    files = _parquet_files(root_name, product, source="et")
    if not files:
        return []
    try:
        lf = pl.scan_parquet([str(f) for f in files[-30:]], hive_partitioning=True)
    except Exception:
        try:
            lf = pl.scan_parquet([str(f) for f in files[-30:]])
        except Exception:
            return []
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
    wafer_values = parse_wafer_selection(wafer_id)
    if wafer_values and "wafer_id" in schema:
        filters.append(pl.col("wafer_id").cast(pl.Utf8).is_in(wafer_values))
    if filters:
        expr = filters[0]
        for e in filters[1:]:
            expr = expr & e
        lf = lf.filter(expr)
    # step_id/step_seq/flat_zone/time 이 schema 에 없을 수도 있음 (구 스키마 호환) — 있으면 alias 로 반환.
    flat_col = "flat" if "flat" in schema else ("flat_zone" if "flat_zone" in schema else "")
    time_col = "time" if "time" in schema else ("tkout_time" if "tkout_time" in schema else "tkin_time")
    group_cols = []
    for c in ("step_id", "step_seq", flat_col, time_col):
        if c in schema:
            group_cols.append(c)
    if not group_cols:
        return []
    lf_grp = lf.group_by(group_cols).agg(pl.len().alias("pt_count"))
    try:
        sort_col = time_col if time_col in group_cols else group_cols[0]
        df = lf_grp.sort(sort_col, descending=True).head(limit).collect()
    except Exception:
        return []
    if df.is_empty():
        return []
    out = []
    for r in df.to_dicts():
        step_id = r.get("step_id")
        meta = lookup_step_meta(product=product, step_id=step_id)
        out.append({
            "step_id": r.get("step_id"),
            "step_seq": r.get("step_seq"),
            "flat": r.get(flat_col),
            "time": r.get(time_col),
            "pt_count": int(r.get("pt_count") or 0),
            **meta,
        })
    return out


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
