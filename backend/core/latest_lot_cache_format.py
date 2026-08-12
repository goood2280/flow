"""Canonical latest-lot cache format shared by writers and readers.

Legacy cache files are intentionally left on disk. Readers accept only the
current embedded format version so an older cache cannot silently reintroduce
the former mixed product naming convention.
"""
from __future__ import annotations


FILE_NAME = "lot_progress_latest_lot_by_root_wafer.parquet"
FORMAT_COLUMN = "cache_format_version"
FORMAT_VERSION = 4
SOURCE_COLUMN = "cache_source"
SOURCE_SPLITTABLE = "splittable_match_cache"


def normalize_product(value: object) -> str:
    raw = str(value or "").strip()
    if raw.upper().startswith("ML_TABLE_"):
        raw = raw[len("ML_TABLE_"):].strip()
    return raw.upper()
