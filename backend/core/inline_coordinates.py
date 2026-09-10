"""Rule-driven INLINE subitem filtering and ET-coordinate translation.

The rulebook owns which coordinate table applies to a product/step/item.  The
TEG inline-map setting owns the table contents.  Keeping those responsibilities
separate means new products and measurement layouts are data changes, not code
changes.
"""
from __future__ import annotations

import csv
import json
import math
import re
from pathlib import Path
from typing import Iterable


SUMMARY_SUBITEM_IDS = frozenset({
    "avg", "average", "mean", "med", "median", "std", "stdev", "stddev",
    "min", "minimum", "max", "maximum", "q1", "q3", "quartile1", "quartile3",
})
DEFAULT_RULEBOOK_NAME = "inline_shot_matching.csv"
LEGACY_RULEBOOK_NAME = "inline_matching.csv"

STEP_ALIASES = ("step_id", "step", "process_id", "stepid", "step_no")
ITEM_ALIASES = ("item_id", "item", "rawitem_id", "itemid", "param_name", "parameter")
MAP_ALIASES = ("map", "map_name", "matching_table", "table_name", "mapname", "map_file")
PRODUCT_ALIASES = ("product", "prod", "vehicle", "product_id", "device")


def normalize_key(value: object) -> str:
    return str(value or "").strip().casefold()


def normalize_subitem_id(value: object) -> str:
    """Normalize only for comparison; the raw identifier remains untouched."""
    return re.sub(r"[\s_.-]+", "", normalize_key(value))


NORMALIZED_SUMMARY_SUBITEM_IDS = frozenset(normalize_subitem_id(v) for v in SUMMARY_SUBITEM_IDS)


def is_summary_subitem(value: object) -> bool:
    """True for pre-calculated INLINE statistic rows that must not be re-aggregated."""
    return normalize_subitem_id(value) in NORMALIZED_SUMMARY_SUBITEM_IDS


def summary_subitem_sql_values() -> tuple[str, ...]:
    """Uppercase normalized values used by the DuckDB chart path."""
    return tuple(sorted(v.upper() for v in NORMALIZED_SUMMARY_SUBITEM_IDS))


def _find_col(row: dict[str, str], candidates: tuple[str, ...]) -> str:
    """Case-insensitive column value lookup."""
    lower_map = {k.strip().lower(): str(v or "").strip() for k, v in row.items() if k}
    for cand in candidates:
        val = lower_map.get(cand.lower())
        if val:
            return val
    return ""


def _infer_product_from_filename(
    stem: str,
    row_product: str = "",
    target_products: set[str] | None = None,
) -> str:
    """Infer product from filename when not explicitly provided in row."""
    if row_product:
        return row_product
    stem_cf = stem.casefold()
    if target_products:
        for p in target_products:
            p_norm = p.casefold()
            if stem_cf == p_norm:
                return stem
            if stem_cf.startswith(f"{p_norm}_"):
                return stem[:len(p_norm)]
    for suffix in (
        "_inline_shotmatching", "_inline_shot_matching",
        "_inline_matching", "_shotmatching", "_matching",
    ):
        if stem_cf.endswith(suffix):
            return stem[:-len(suffix)]
    if "_" in stem:
        return stem.split("_", 1)[0]
    return stem


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    except (OSError, UnicodeError, csv.Error):
        return []


def _read_tables(path: Path) -> dict[str, dict]:
    """Read inline map settings JSON and build multi-alias index."""
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    tables = raw.get("tables", []) if isinstance(raw, dict) else []
    if isinstance(tables, dict):
        tables = list(tables.values())
    indexed: dict[str, dict] = {}
    for table in tables:
        if not isinstance(table, dict):
            continue
        raw_name = str(table.get("table_name") or "").strip()
        if not raw_name:
            continue
        k_full = normalize_key(raw_name)
        k_name = normalize_key(Path(raw_name).name)
        k_stem = normalize_key(Path(raw_name).stem)
        for key in (k_full, k_name, k_stem):
            if key and key not in indexed:
                indexed[key] = table
    return indexed


def _find_table(tables: dict[str, dict], map_name: str) -> dict | None:
    """Find a table object by name, supporting extensions like Normal.jpg <-> Normal."""
    if not map_name:
        return None
    k_full = normalize_key(map_name)
    if k_full in tables:
        return tables[k_full]
    k_name = normalize_key(Path(map_name).name)
    if k_name in tables:
        return tables[k_name]
    k_stem = normalize_key(Path(map_name).stem)
    if k_stem in tables:
        return tables[k_stem]
    for ext in (".jpg", ".map", ".png", ".jpeg"):
        if k_full + ext in tables:
            return tables[k_full + ext]
    return None


def _default_inline_map_settings_path(base_root: Path) -> Path:
    confidential_path = base_root / "confidential" / "inline_map_settings.json"
    if confidential_path.is_file():
        return confidential_path
    legacy_path = base_root / "credential" / "inline_map_settings.json"
    if legacy_path.is_file():
        return legacy_path
    return confidential_path


def find_matching_rulebook_files(
    base_root: Path,
    rulebook_name: str = DEFAULT_RULEBOOK_NAME,
) -> list[tuple[Path, str]]:
    """Discover all product/inline matching CSVs across confidential and root.

    Returns a list of (Path, default_product_hint) pairs.
    """
    discovered: list[tuple[Path, str]] = []
    seen_paths: set[Path] = set()

    def _add_if_new(path: Path, default_prod: str = "") -> None:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if path.is_file() and resolved not in seen_paths:
            seen_paths.add(resolved)
            discovered.append((path, default_prod))

    # 1. confidential/ directory (*.csv)
    confidential_dir = base_root / "confidential"
    if confidential_dir.is_dir():
        for child in sorted(confidential_dir.glob("*.csv"), key=lambda p: p.name.casefold()):
            _add_if_new(child)

    # 2. credential/ directory (*.csv fallback)
    credential_dir = base_root / "credential"
    if credential_dir.is_dir():
        for child in sorted(credential_dir.glob("*.csv"), key=lambda p: p.name.casefold()):
            if "matching" in child.name.casefold() or "shot" in child.name.casefold():
                _add_if_new(child)

    # 3. Explicit requested rulebook or default files in base_root
    target = base_root / rulebook_name
    if target.is_file():
        _add_if_new(target)
    elif rulebook_name == DEFAULT_RULEBOOK_NAME and (base_root / LEGACY_RULEBOOK_NAME).is_file():
        _add_if_new(base_root / LEGACY_RULEBOOK_NAME)

    for pattern in ("*_inline_shotmatching.csv", "*_inline_matching.csv"):
        for child in sorted(base_root.glob(pattern), key=lambda p: p.name.casefold()):
            _add_if_new(child)

    return discovered


def rulebook_and_settings_signature(base_root: Path) -> list[tuple[str, int, int]]:
    """Compute cache signature for all discovered matching CSVs and settings."""
    sig: list[tuple[str, int, int]] = []
    for path, _ in find_matching_rulebook_files(base_root):
        try:
            st = path.stat()
            sig.append((f"csv:{path.name}", int(st.st_mtime_ns), int(st.st_size)))
        except OSError:
            sig.append((f"csv:{path.name}", 0, 0))
    for json_path in (
        base_root / "confidential" / "inline_map_settings.json",
        base_root / "credential" / "inline_map_settings.json",
    ):
        try:
            st = json_path.stat()
            sig.append((f"json:{json_path.parent.name}/{json_path.name}", int(st.st_mtime_ns), int(st.st_size)))
        except OSError:
            pass
    return sorted(sig)


def load_matching_rules(
    base_root: Path,
    *,
    products: Iterable[str] = (),
    item_ids: Iterable[str] = (),
    rulebook_name: str = DEFAULT_RULEBOOK_NAME,
    settings_path: Path | None = None,
) -> list[dict[str, object]]:
    """List ITEM-specific matching tables and whether their TEG map exists."""
    product_scope = {normalize_key(v) for v in products if normalize_key(v)}
    item_scope = {normalize_key(v) for v in item_ids if normalize_key(v)}
    settings = settings_path or _default_inline_map_settings_path(base_root)
    tables = _read_tables(settings)
    out: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()

    files = find_matching_rulebook_files(base_root, rulebook_name)
    for file_path, default_prod in files:
        for rule in _read_csv(file_path):
            raw_prod = _find_col(rule, PRODUCT_ALIASES)
            product = _infer_product_from_filename(
                file_path.stem, raw_prod or default_prod, product_scope or None,
            )
            step_id = _find_col(rule, STEP_ALIASES)
            item_id = _find_col(rule, ITEM_ALIASES)
            table_name = _find_col(rule, MAP_ALIASES)
            if not product or not step_id or not item_id or not table_name:
                continue
            if product_scope and normalize_key(product) not in product_scope:
                continue
            if item_scope and normalize_key(item_id) not in item_scope:
                continue
            key = (normalize_key(product), normalize_key(step_id), normalize_key(item_id))
            if key in seen:
                continue
            seen.add(key)
            table = _find_table(tables, table_name)
            valid_shots = [
                s for s in ((table or {}).get("shots") or [])
                if isinstance(s, dict) and not is_summary_subitem(s.get("subitem_id") or s.get("name"))
            ]
            out.append({
                "product": product,
                "step_id": step_id,
                "item_id": item_id,
                "matching_table": table_name,
                "available": table is not None,
                "vehicle": str((table or {}).get("vehicle") or ""),
                "shot_count": len(valid_shots),
            })
    return sorted(out, key=lambda row: tuple(
        normalize_key(row.get(key)) for key in ("product", "item_id", "step_id", "matching_table")
    ))


def load_coordinate_mapping(
    base_root: Path,
    *,
    products: Iterable[str] = (),
    item_ids: Iterable[str] = (),
    rulebook_name: str = DEFAULT_RULEBOOK_NAME,
    settings_path: Path | None = None,
) -> dict[str, object]:
    """Return mapping status and flattened ET shot-coordinate rows.

    Incomplete rules and unknown table names are intentionally ignored.  Once a
    rule selects a table, only subitems explicitly present in that table are
    emitted; source summary rows therefore cannot leak into shot matching.
    """
    product_scope = {normalize_key(v) for v in products if normalize_key(v)}
    item_scope = {normalize_key(v) for v in item_ids if normalize_key(v)}
    settings = settings_path or _default_inline_map_settings_path(base_root)
    tables = _read_tables(settings)
    out: list[dict[str, object]] = []
    configured_tables: set[str] = set()
    missing_tables: set[str] = set()
    seen: set[tuple[object, ...]] = set()

    files = find_matching_rulebook_files(base_root, rulebook_name)
    for file_path, default_prod in files:
        for rule in _read_csv(file_path):
            raw_prod = _find_col(rule, PRODUCT_ALIASES)
            product = _infer_product_from_filename(
                file_path.stem, raw_prod or default_prod, product_scope or None,
            )
            step_id = _find_col(rule, STEP_ALIASES)
            item_id = _find_col(rule, ITEM_ALIASES)
            raw_map_name = _find_col(rule, MAP_ALIASES)
            if not step_id or not item_id or not raw_map_name:
                continue
            norm_prod = normalize_key(product)
            norm_step = normalize_key(step_id)
            norm_item = normalize_key(item_id)
            if product_scope and norm_prod not in product_scope:
                continue
            if item_scope and norm_item not in item_scope:
                continue
            configured_tables.add(raw_map_name)
            table = _find_table(tables, raw_map_name)
            if not table:
                missing_tables.add(raw_map_name)
                continue
            canonical_table_name = str(table.get("table_name") or raw_map_name).strip()
            for shot in table.get("shots", []):
                if not isinstance(shot, dict) or is_summary_subitem(shot.get("subitem_id") or shot.get("name")):
                    continue
                subitem_id = normalize_key(shot.get("subitem_id") or shot.get("name"))
                try:
                    shot_x = float(shot.get("shot_x"))
                    shot_y = float(shot.get("shot_y"))
                except (TypeError, ValueError):
                    continue
                if not subitem_id or not all(math.isfinite(v) for v in (shot_x, shot_y)):
                    continue
                key = (norm_prod, norm_step, norm_item, subitem_id, shot_x, shot_y)
                if key in seen:
                    continue
                seen.add(key)
                out.append({
                    "product": norm_prod,
                    "step_id": norm_step,
                    "item_id": norm_item,
                    "subitem_id": subitem_id,
                    "shot_x": shot_x,
                    "shot_y": shot_y,
                    "matching_table": canonical_table_name,
                    "vehicle": str(table.get("vehicle") or ""),
                    "rule_file": file_path.name,
                })
    return {
        "configured": bool(configured_tables),
        "configured_tables": sorted(configured_tables, key=str.casefold),
        "missing_tables": sorted(missing_tables, key=str.casefold),
        "rows": out,
    }


def load_coordinate_rows(
    base_root: Path,
    *,
    products: Iterable[str] = (),
    item_ids: Iterable[str] = (),
    rulebook_name: str = DEFAULT_RULEBOOK_NAME,
    settings_path: Path | None = None,
) -> list[dict[str, object]]:
    """Compatibility helper returning only flattened coordinate rows."""
    return load_coordinate_mapping(
        base_root,
        products=products,
        item_ids=item_ids,
        rulebook_name=rulebook_name,
        settings_path=settings_path,
    )["rows"]
