"""Rule-driven INLINE subitem filtering and ET-coordinate translation.

The rulebook owns which coordinate table applies to a product/step/item.  The
TEG inline-map setting owns the table contents.  Keeping those responsibilities
separate means new products and measurement layouts are data changes, not code
changes.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Iterable


SUMMARY_SUBITEM_IDS = frozenset({
    "avg", "average", "mean", "med", "median", "std", "stdev", "stddev",
    "min", "minimum", "max", "maximum", "q1", "q3", "quartile1", "quartile3",
})


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


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    except (OSError, UnicodeError, csv.Error):
        return []


def _read_tables(path: Path) -> dict[str, dict]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    tables = raw.get("tables", []) if isinstance(raw, dict) else []
    if isinstance(tables, dict):
        tables = list(tables.values())
    return {
        normalize_key(table.get("table_name")): table
        for table in tables
        if isinstance(table, dict) and normalize_key(table.get("table_name"))
    }


def load_coordinate_mapping(
    base_root: Path,
    *,
    products: Iterable[str] = (),
    item_ids: Iterable[str] = (),
    rulebook_name: str = "inline_matching.csv",
    settings_path: Path | None = None,
) -> dict[str, object]:
    """Return mapping status and flattened ET shot-coordinate rows.

    Incomplete rules and unknown table names are intentionally ignored.  Once a
    rule selects a table, only subitems explicitly present in that table are
    emitted; source summary rows therefore cannot leak into shot matching.
    """
    product_scope = {normalize_key(v) for v in products if normalize_key(v)}
    item_scope = {normalize_key(v) for v in item_ids if normalize_key(v)}
    settings = settings_path or (base_root / "credential" / "inline_map_settings.json")
    tables = _read_tables(settings)
    out: list[dict[str, object]] = []
    configured_tables: set[str] = set()
    missing_tables: set[str] = set()
    seen: set[tuple[object, ...]] = set()
    for rule in _read_csv(base_root / rulebook_name):
        product = normalize_key(rule.get("product"))
        step_id = normalize_key(rule.get("step_id"))
        item_id = normalize_key(rule.get("item_id"))
        table_name = normalize_key(rule.get("matching_table"))
        if not step_id or not item_id or not table_name:
            continue
        if product_scope and product not in product_scope:
            continue
        if item_scope and item_id not in item_scope:
            continue
        configured_tables.add(str(rule.get("matching_table") or "").strip())
        table = tables.get(table_name)
        if not table:
            missing_tables.add(str(rule.get("matching_table") or "").strip())
            continue
        for shot in table.get("shots", []):
            if not isinstance(shot, dict) or is_summary_subitem(shot.get("name")):
                continue
            subitem_id = normalize_key(shot.get("name"))
            try:
                shot_x = float(shot.get("shot_x"))
                shot_y = float(shot.get("shot_y"))
            except (TypeError, ValueError):
                continue
            if not subitem_id:
                continue
            key = (product, step_id, item_id, subitem_id, shot_x, shot_y)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "product": product,
                "step_id": step_id,
                "item_id": item_id,
                "subitem_id": subitem_id,
                "shot_x": shot_x,
                "shot_y": shot_y,
                "matching_table": str(table.get("table_name") or rule.get("matching_table") or "").strip(),
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
    rulebook_name: str = "inline_matching.csv",
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
