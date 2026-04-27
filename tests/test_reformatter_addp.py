from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core.reformatter import apply_rules, reformatter_table_to_rules, rules_to_reformatter_table  # noqa: E402


def test_addp_table_uses_formula_placeholders_not_item_id():
    rules = reformatter_table_to_rules([
        {
            "no": 1,
            "addp": "addp",
            "item_id": "LEGACY_SHOULD_NOT_BE_USED",
            "alias": "STI_INDEX",
            "addp_form": "max({RAW_A}, {RAW_B}) + abs({RAW_A} - {RAW_B})",
            "use": "Y",
        }
    ])

    rule = rules[0]

    assert rule["type"] == "shot_formula"
    assert rule["rawitem_id"] == ""
    assert set(rule["item_map"].values()) == {"RAW_A", "RAW_B"}
    assert "LEGACY_SHOULD_NOT_BE_USED" not in rule["item_map"].values()
    assert rule["expr"] == "max(RAW_A, RAW_B) + abs(RAW_A - RAW_B)"


def test_addp_table_export_leaves_item_id_blank():
    rows = rules_to_reformatter_table([
        {
            "name": "STI_INDEX",
            "type": "shot_formula",
            "addp": "addp",
            "rawitem_id": "RAW_A/RAW_B",
            "alias": "STI_INDEX",
            "addp_form": "max({RAW_A}, {RAW_B})",
            "item_map": {"RAW_A": "RAW_A", "RAW_B": "RAW_B"},
            "expr": "max(RAW_A, RAW_B)",
            "item_col": "item_id",
            "value_col": "value",
        }
    ])

    assert rows[0]["addp"] == "addp"
    assert rows[0]["item_id"] == ""
    assert rows[0]["addp_form"] == "max({RAW_A}, {RAW_B})"


def test_addp_formula_calculates_per_wafer_step_shot_flat_group():
    rules = reformatter_table_to_rules([
        {
            "no": 1,
            "addp": "addp",
            "item_id": "",
            "alias": "STI_INDEX",
            "addp_form": "max({RAW_A}, {RAW_B})",
            "use": "Y",
        }
    ])
    df = pl.DataFrame([
        {"product": "PRODA0", "root_lot_id": "A10001", "wafer_id": "1", "step_id": "STI", "step_seq": 1, "shot_x": 0, "shot_y": 0, "flat": "N", "item_id": "RAW_A", "value": 3.0},
        {"product": "PRODA0", "root_lot_id": "A10001", "wafer_id": "1", "step_id": "STI", "step_seq": 1, "shot_x": 0, "shot_y": 0, "flat": "N", "item_id": "RAW_B", "value": 10.0},
        {"product": "PRODA0", "root_lot_id": "A10001", "wafer_id": "1", "step_id": "STI", "step_seq": 1, "shot_x": 0, "shot_y": 0, "flat": "S", "item_id": "RAW_A", "value": 100.0},
        {"product": "PRODA0", "root_lot_id": "A10001", "wafer_id": "1", "step_id": "STI", "step_seq": 1, "shot_x": 0, "shot_y": 0, "flat": "S", "item_id": "RAW_B", "value": 120.0},
    ])

    out = apply_rules(df, rules)
    got = (
        out.select(["flat", "STI_INDEX"])
        .unique()
        .sort("flat")
        .to_dicts()
    )

    assert got == [{"flat": "N", "STI_INDEX": 10.0}, {"flat": "S", "STI_INDEX": 120.0}]
