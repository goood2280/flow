"""`X_new` 를 참조하는 Index 는 `_new` 를 뗀 짝 원값 `X` 도 함께 내보낸다."""

import polars as pl

from backend.core.vehicle_reformatter import new_suffix_base, reformatize, resolve_needed_items
from backend.routers.reformatize import _resolve_output_cols


def _row(alias, category, itemid="", form="", order=None):
    return {
        "num": 0, "category": category, "itemid": itemid, "alias": alias,
        "absolute": False, "scale": 1.0, "addp_form": form, "unit": "",
        "speclow": None, "spechigh": None, "target": None,
        "report_order": order, "cat1": "", "cat2": "",
    }


def _long():
    rows = []
    for shot, (win, win_new, other) in enumerate([(1.0, 1.5, 9.0), (2.0, 2.5, 9.0)]):
        for item, value in (("WINDOW", win), ("WINDOW_new", win_new), ("OTHER", other)):
            rows.append({
                "root_lot_id": "LOT1", "wafer_id": "1", "step_id": "S1",
                "shot_x": shot, "shot_y": 0, "item_id": item, "value": value,
            })
    return pl.DataFrame(rows)


def test_new_suffix_base():
    assert new_suffix_base("WINDOW_new") == "WINDOW"
    assert new_suffix_base("WINDOW_NEW") == "WINDOW"
    assert new_suffix_base("WINDOW") is None
    assert new_suffix_base("_new") is None


def test_formula_ref_to_new_loads_base_raw_item():
    table = [_row("WIN_MAX", "addp", form="rmax({WINDOW_new})", order=1)]
    _, needed = resolve_needed_items(table, ["WIN_MAX"])
    assert {"WINDOW_new", "WINDOW"} <= needed
    assert "OTHER" not in needed


def test_new_pair_is_output_next_to_new_column():
    table = [_row("WIN_MAX", "addp", form="rmax({WINDOW_new})", order=1)]
    wide, out_cols, errors = reformatize(_long(), table, selected_aliases=["WIN_MAX"])
    assert not errors

    cols = _resolve_output_cols(out_cols, table, ["WIN_MAX"], wide)

    assert "WINDOW" in cols and "WINDOW_new" in cols
    assert cols.index("WINDOW") + 1 == cols.index("WINDOW_new")
    assert "OTHER" not in cols
    assert wide.select(cols)["WINDOW"].to_list() == [1.0, 2.0]


def test_real_alias_ending_in_new_pulls_base_alias():
    table = [
        _row("WIN", "real", itemid="WINDOW", order=2),
        _row("WIN_new", "real", itemid="WINDOW_new", order=1),
    ]
    wide, out_cols, _ = reformatize(_long(), table, selected_aliases=["WIN_new"])
    cols = _resolve_output_cols(out_cols, table, ["WIN_new"], wide)

    assert cols.index("WIN") + 1 == cols.index("WIN_new")
