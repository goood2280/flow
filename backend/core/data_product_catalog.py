"""Discover Flow product names from the physical DB layout.

The chat must not infer a product from demo names, TEG vehicle names, or a
synthetic ``ML_TABLE_<token>``.  This catalog only reports products that have
an observable source under ``FLOW_DB_ROOT``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.paths import PATHS


_DATA_SUFFIXES = {".parquet", ".csv"}
_SOURCE_ROOT_NAMES = {"FAB", "ET", "INLINE", "VM", "EDS", "BIN", "MSR", "QTIME"}
_IGNORED_DIR_NAMES = {
    "cache", "history", "logs", "reports", "temp", "tmp", "_backups", "backups",
}


def _clean_product(value: Any) -> str:
    raw = str(value or "").strip()
    upper = raw.upper()
    if upper.startswith("ML_TABLE_"):
        raw = raw[len("ML_TABLE_"):].strip()
    if raw.lower().startswith("product="):
        raw = raw.split("=", 1)[1].strip()
    return raw


def _is_source_root(path: Path) -> bool:
    name = path.name.upper()
    return path.is_dir() and ("RAWDATA_DB" in name or name in _SOURCE_ROOT_NAMES)


def _is_product_dir(path: Path) -> bool:
    name = str(path.name or "").strip()
    lower = name.casefold()
    if not name or name.startswith((".", "_")) or lower in _IGNORED_DIR_NAMES:
        return False
    if lower.startswith(("date=", "dt=", "year=", "month=", "day=")):
        return False
    return True


def discover_product_catalog(db_root: Path | None = None) -> list[dict[str, Any]]:
    """Return canonical products and the exact physical table/source names.

    Supported layouts are intentionally metadata-only:

    - ``DB_ROOT/ML_TABLE_<product>.parquet``
    - ``DB_ROOT/<source>/<product>/...``
    - ``DB_ROOT/<source>/product=<product>/...``
    - ``DB_ROOT/<source>/<table>/product=<product>/...``

    No data file is opened, so product detection stays cheap on large DBs.
    """
    root = Path(db_root) if db_root is not None else PATHS.db_root
    if not root.is_dir():
        return []

    records: dict[str, dict[str, Any]] = {}

    def add(product: str, *, table: str = "", source_root: str = "", split_table: str = "") -> None:
        clean = _clean_product(product)
        if not clean:
            return
        key = clean.casefold()
        row = records.setdefault(key, {
            "product": clean,
            "tables": [],
            "source_roots": [],
            "split_table": "",
        })
        if table and table not in row["tables"]:
            row["tables"].append(table)
        if source_root and source_root not in row["source_roots"]:
            row["source_roots"].append(source_root)
        if split_table:
            row["split_table"] = split_table

    try:
        root_entries = sorted(root.iterdir(), key=lambda item: item.name.casefold())
    except OSError:
        return []

    for path in root_entries:
        if not path.is_file() or path.suffix.lower() not in _DATA_SUFFIXES:
            continue
        if path.stem.upper().startswith("ML_TABLE_"):
            add(path.stem, table=path.stem, source_root="DB", split_table=path.stem)

    for source_root in (path for path in root_entries if _is_source_root(path)):
        try:
            children = sorted(source_root.iterdir(), key=lambda item: item.name.casefold())
        except OSError:
            continue

        # A product-partition directly under the source root.
        for child in children:
            if child.is_dir() and child.name.casefold().startswith("product="):
                add(child.name, table=source_root.name, source_root=source_root.name)

        for child in (item for item in children if item.is_dir() and _is_product_dir(item)):
            try:
                partitions = [
                    item for item in child.iterdir()
                    if item.is_dir() and item.name.casefold().startswith("product=")
                ]
            except OSError:
                partitions = []
            if partitions:
                # Hive layout: <source>/<table>/product=<actual product>.
                for partition in partitions:
                    add(partition.name, table=child.name, source_root=source_root.name)
            else:
                # Flat/hive-by-product layout: <source>/<actual product>/...
                add(child.name, table=source_root.name, source_root=source_root.name)

    rows = list(records.values())
    for row in rows:
        row["tables"].sort(key=str.casefold)
        row["source_roots"].sort(key=str.casefold)
    return sorted(rows, key=lambda row: str(row["product"]).casefold())


def product_names(db_root: Path | None = None) -> list[str]:
    return [str(row["product"]) for row in discover_product_catalog(db_root)]


def product_record(product: str, db_root: Path | None = None) -> dict[str, Any] | None:
    key = _clean_product(product).casefold()
    if not key:
        return None
    return next((row for row in discover_product_catalog(db_root)
                 if str(row.get("product") or "").casefold() == key), None)
