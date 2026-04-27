from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from routers import splittable  # noqa: E402


def test_root_lot_candidates_come_from_configured_fab_source():
    result = splittable.get_lot_candidates(
        product="ML_TABLE_PRODA",
        col="root_lot_id",
        prefix="A00",
        limit=20,
        source="auto",
        root_lot_id="",
    )

    assert result["source"] == "fab_source_history"
    assert result["fab_source"] == "1.RAWDATA_DB_FAB/PRODA"
    assert "A0001" in result["candidates"]


def test_lot_ids_merge_configured_fab_roots_before_mltable_roots():
    result = splittable.get_lot_ids(product="ML_TABLE_PRODA", limit=20)

    assert result["fallback"] == "fab_source"
    assert result["fab_source"] == "1.RAWDATA_DB_FAB/PRODA"
    assert "A0001" in result["lot_ids"]
