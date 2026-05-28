from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core import agent_semantic_service  # noqa: E402


def test_resolve_filebrowser_shape_matches_unit_semantic_frame():
    frame = agent_semantic_service.resolve(
        "A1000 wafer_id value만 보여줘",
        columns=["root_lot_id", "wafer_id", "value", "step_id"],
        product="PRODA",
        dtypes={"value": "float"},
    )

    assert set(["resolved_columns", "value_terms", "synonyms", "step_mapping"]).issubset(frame.keys())
    assert "wafer_id" in frame["resolved_columns"]
    assert frame["synonyms"]["wafer_id"]
    assert isinstance(frame["value_terms"], list)
    assert isinstance(frame["step_mapping"], dict)


def test_resolve_inform_alias_slot_shape():
    frame = agent_semantic_service.resolve(
        "product PRODA lot R1000 module GATE note IOFF drift to alice@example.test"
    )

    assert frame["alias_hits"]
    assert frame["slot_hints"]["product"] == "PRODA"
    assert frame["slot_hints"]["lot_id"] == "R1000"
    assert "unknown_terms" in frame
    assert "intent_matches" in frame
