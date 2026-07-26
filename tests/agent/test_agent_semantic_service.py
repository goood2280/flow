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
    assert "source_catalog_matches" in frame


def test_resolve_adds_value_catalog_matches_and_unknown_routes():
    frame = agent_semantic_service.resolve(
        "A1000 FOOBAR만 확인",
        columns=["root_lot_id", "item_id"],
        sample_profile={
            "source": "hive:FAB/PRODA",
            "columns": [
                {"name": "root_lot_id", "dtype": "String", "sample_values": ["A1000", "B2000"]},
            ],
        },
        source_ref={"scope": "db_product", "root": "FAB", "product": "PRODA"},
    )

    assert any(match.get("column") == "root_lot_id" for match in frame["value_catalog_matches"])
    assert any(match.get("source_id") == "fab_db" for match in frame["source_catalog_matches"])
    unknown = frame["unknown_terms"]
    assert unknown and "term" in unknown[0]
    assert unknown[0]["search_priority"]
    assert any(priority.get("source_id") == "fab_db" for priority in unknown[0]["search_priority"])


def test_resolve_adds_catalog_backed_step_and_rulebook_priorities():
    frame = agent_semantic_service.resolve(
        "mystery step_id PPID knob rulebook 확인",
        source_ref={"file": "ppid_knob.csv"},
    )

    match_ids = {row.get("source_id") for row in frame["source_catalog_matches"]}
    assert {"rulebook", "step_matching"}.issubset(match_ids)

    priorities = [
        priority
        for term in frame["unknown_terms"]
        for priority in term.get("search_priority", [])
    ]
    assert any(priority.get("source_id") == "rulebook" for priority in priorities)
    assert any(priority.get("source_id") == "step_matching" for priority in priorities)
    assert all(
        priority.get("table_file") != "FLOW_DB_ROOT/Vehicle_matching.csv, step_matching.csv, ppid_knob.csv"
        for priority in priorities
    )
