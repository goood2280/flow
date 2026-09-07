import asyncio
import io
from pathlib import Path

import polars as pl
import pytest
from fastapi import HTTPException


def _response_bytes(response):
    async def collect():
        return b"".join([chunk async for chunk in response.body_iterator])

    return asyncio.run(collect())


def test_purpose_is_builtin_first_and_cannot_be_deleted(monkeypatch):
    from routers import splittable

    store = {
        "columns": [
            {"product": "P1", "column": "TAG_zeta", "label": "zeta"},
            {"product": "P1", "column": "TAG_alpha", "label": "alpha"},
        ],
        "values": {},
        "colors": {},
    }
    monkeypatch.setattr(splittable, "_load_custom_tags_data", lambda: store)

    columns = splittable._custom_tag_columns_for_product("P1")

    assert columns[0]["column"] == "TAG_purpose"
    assert columns[0]["label"] == "purpose"
    assert columns[0]["builtin"] is True
    assert [column["column"] for column in columns[1:]] == ["TAG_zeta", "TAG_alpha"]
    ordered = sorted(
        ["TAG_zeta", "KNOB_A", "TAG_purpose", "TAG_alpha"],
        key=lambda column: splittable._step_order_sort_key(column, column, {}),
    )
    assert ordered[:3] == ["TAG_purpose", "TAG_alpha", "TAG_zeta"]
    assert splittable._with_default_custom_tag(["KNOB_A"]) == ["TAG_purpose", "KNOB_A"]

    with pytest.raises(HTTPException) as error:
        splittable.delete_custom_tag_column(
            splittable.CustomTagColumnDeleteReq(product="P1", column="TAG_purpose")
        )
    assert error.value.status_code == 400


def test_purpose_value_and_color_are_saved_per_wafer_and_expanded(monkeypatch):
    from routers import splittable

    store = {"columns": [], "values": {}, "colors": {}}
    monkeypatch.setattr(splittable, "_load_custom_tags_data", lambda: store)
    monkeypatch.setattr(splittable, "_save_custom_tags_data", lambda data: None)

    result = splittable.save_custom_tag_values(
        splittable.CustomTagValuesReq(
            product="P1",
            values={"L1|1|TAG_purpose": "DOE"},
            colors={"L1|1|TAG_purpose": "#fecaca"},
            username="tester",
        )
    )

    assert result["saved"] == 1
    assert result["colors_saved"] == 1
    assert splittable._custom_tag_values_for_root("P1", "L1") == {
        "L1|1|TAG_purpose": "DOE"
    }
    assert splittable._custom_tag_colors_for_root("P1", "L1") == {
        "L1|1|TAG_purpose": "#fecaca"
    }

    expanded = splittable._expand_view_rows({
        "root_lot_id": "L1",
        "wafer_keys": ["1"],
        "rows_compact": [{
            "_param": "TAG_purpose",
            "_display": "TAG_purpose",
            "a": ["DOE"],
            "tag": True,
            "tc": {"0": "#fecaca"},
        }],
    })
    cell = expanded["rows"][0]["_cells"]["0"]
    assert cell["actual"] == "DOE"
    assert cell["tag_color"] == "#fecaca"
    assert cell["is_custom_tag"] is True


def test_purpose_is_in_csv_and_xlsx_keeps_its_background(monkeypatch):
    from openpyxl import load_workbook
    from routers import splittable

    frame = pl.DataFrame({
        "root_lot_id": ["L1"],
        "fab_lot_id": ["L1.1"],
        "wafer_id": [1],
        "KNOB_A": ["PP_A"],
    })
    monkeypatch.setattr(splittable, "_product_path", lambda *args, **kwargs: None)
    monkeypatch.setattr(splittable, "_scan_product", lambda *args, **kwargs: frame.lazy())
    monkeypatch.setattr(splittable, "_load_plan_data", lambda *args, **kwargs: {"plans": {}})
    # Even a legacy/empty TAG catalog must not make the built-in purpose row
    # disappear from a KNOB-only selection or its exports.
    monkeypatch.setattr(splittable, "_custom_tag_label_map", lambda *args, **kwargs: {})
    monkeypatch.setattr(splittable, "_custom_tag_values_for_root", lambda *args, **kwargs: {"L1|1|TAG_purpose": "DOE"})
    monkeypatch.setattr(splittable, "_custom_tag_colors_for_root", lambda *args, **kwargs: {"L1|1|TAG_purpose": "#fecaca"})
    monkeypatch.setattr(splittable, "_management_row_label_map", lambda *args, **kwargs: {})
    monkeypatch.setattr(splittable, "_management_row_values_for_root", lambda *args, **kwargs: {})
    monkeypatch.setattr(splittable, "_split_step_order_context", lambda *args, **kwargs: {"param_rank": {}})
    monkeypatch.setattr(splittable, "_split_step_progress", lambda *args, **kwargs: {})
    monkeypatch.setattr(splittable, "_log_split_table_download", lambda *args, **kwargs: None)

    csv_response = splittable.download_csv(
        product="P1", root_lot_id="L1", wafer_ids="", prefix="KNOB",
        custom_name="", transposed="true", username="u", custom_cols="",
        step_labels="", exclude_not_null="1",
    )
    csv_text = _response_bytes(csv_response).decode("utf-8-sig")
    assert "purpose,DOE" in csv_text

    xlsx_response = splittable.download_xlsx(
        product="P1", root_lot_id="L1", wafer_ids="", prefix="KNOB",
        custom_name="", username="u", custom_cols="", display_mode="",
        step_labels="", exclude_not_null="1",
    )
    sheet = load_workbook(io.BytesIO(_response_bytes(xlsx_response))).active
    assert sheet.cell(4, 1).value == "purpose"
    assert sheet.cell(4, 2).value == "DOE"
    assert sheet.cell(4, 2).fill.fill_type == "solid"
    assert sheet.cell(4, 2).fill.fgColor.rgb.endswith("FECACA")
    assert sheet.cell(5, 1).value == "fab_lot_id"
    assert sheet.cell(6, 1).value == "Parameter"


def test_split_and_pems_views_keep_purpose_as_a_fixed_header_row():
    root = Path(__file__).resolve().parents[1]
    page = (root / "frontend/src/features/splittable/My_SplitTable.jsx").read_text("utf-8")
    snapshot = (root / "frontend/src/components/SplitTableSnapshotView.jsx").read_text("utf-8")

    assert "purpose_row:purposeViewRow" in page
    assert "purpose_row: purposeRow || undefined" in snapshot
    assert "{hasPurposeRow && (" in snapshot
    assert "const splitSourceRows = rows.filter(row => !isPurposeTagRow(row));" in snapshot


def test_split_check_xlsx_includes_purpose_header_row_with_colors_and_no_body_tag(monkeypatch):
    from openpyxl import load_workbook
    from routers import splittable

    frame = pl.DataFrame({
        "root_lot_id": ["L1", "L1"],
        "fab_lot_id": ["L1.1", "L1.1"],
        "wafer_id": [1, 2],
        "KNOB_A": ["PP_A", "PP_B"],
    })
    monkeypatch.setattr(splittable, "_product_path", lambda *args, **kwargs: None)
    monkeypatch.setattr(splittable, "_scan_product", lambda *args, **kwargs: frame.lazy())
    monkeypatch.setattr(splittable, "_load_plan_data", lambda *args, **kwargs: {"plans": {}})
    monkeypatch.setattr(splittable, "_custom_tag_label_map", lambda *args, **kwargs: {})
    monkeypatch.setattr(splittable, "_custom_tag_values_for_root", lambda *args, **kwargs: {
        "L1|1|TAG_purpose": "DOE_ALPHA",
    })
    monkeypatch.setattr(splittable, "_custom_tag_colors_for_root", lambda *args, **kwargs: {
        "L1|1|TAG_purpose": "#d9f99d",
    })
    monkeypatch.setattr(splittable, "_management_row_label_map", lambda *args, **kwargs: {})
    monkeypatch.setattr(splittable, "_management_row_values_for_root", lambda *args, **kwargs: {})
    monkeypatch.setattr(splittable, "_split_step_order_context", lambda *args, **kwargs: {"param_rank": {}})
    monkeypatch.setattr(splittable, "_split_step_progress", lambda *args, **kwargs: {})
    monkeypatch.setattr(splittable, "_log_split_table_download", lambda *args, **kwargs: None)

    xlsx_response = splittable.download_xlsx(
        product="P1", root_lot_id="L1", wafer_ids="", prefix="KNOB",
        custom_name="", username="u", custom_cols="", display_mode="split_check",
        step_labels="1", exclude_not_null="1",
    )
    sheet = load_workbook(io.BytesIO(_response_bytes(xlsx_response))).active

    # Row 3: root_lot_id
    assert sheet.cell(3, 1).value == "root_lot_id"
    # Row 4: purpose header row
    assert sheet.cell(4, 1).value == "purpose"
    assert sheet.cell(4, 6).value == "DOE_ALPHA"
    assert sheet.cell(4, 6).fill.fill_type == "solid"
    assert sheet.cell(4, 6).fill.fgColor.rgb.endswith("D9F99D")
    # Row 5: fab_lot_id
    assert sheet.cell(5, 1).value == "fab_lot_id"
    # Row 6: columns headers
    assert [sheet.cell(6, col).value for col in range(1, 8)] == [
        "step_id", "step_desc", "항목", "값", "Split", "#1", "#2",
    ]
    # Data rows start at 7 and TAG_purpose must NOT appear in body rows
    body_items = [sheet.cell(r, 3).value for r in range(7, sheet.max_row + 1) if sheet.cell(r, 3).value]
    assert "TAG_purpose" not in body_items
    assert "A" in body_items


def test_xlsx_and_csv_fall_back_to_lot_management_purpose(monkeypatch):
    from openpyxl import load_workbook
    from routers import splittable
    import routers.lot_management

    frame = pl.DataFrame({
        "root_lot_id": ["L1"],
        "fab_lot_id": ["L1.1"],
        "wafer_id": [1],
        "KNOB_A": ["PP_A"],
    })
    monkeypatch.setattr(splittable, "_product_path", lambda *args, **kwargs: None)
    monkeypatch.setattr(splittable, "_scan_product", lambda *args, **kwargs: frame.lazy())
    monkeypatch.setattr(splittable, "_load_plan_data", lambda *args, **kwargs: {"plans": {}})
    monkeypatch.setattr(splittable, "_custom_tag_label_map", lambda *args, **kwargs: {})
    monkeypatch.setattr(splittable, "_custom_tag_values_for_root", lambda *args, **kwargs: {})
    monkeypatch.setattr(splittable, "_custom_tag_colors_for_root", lambda *args, **kwargs: {})
    monkeypatch.setattr(splittable, "_management_row_label_map", lambda *args, **kwargs: {})
    monkeypatch.setattr(splittable, "_management_row_values_for_root", lambda *args, **kwargs: {})
    monkeypatch.setattr(splittable, "_split_step_order_context", lambda *args, **kwargs: {"param_rank": {}})
    monkeypatch.setattr(splittable, "_split_step_progress", lambda *args, **kwargs: {})
    monkeypatch.setattr(splittable, "_log_split_table_download", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        routers.lot_management, "_load",
        lambda prod: {"rows": [{"values": {"lot_id": "L1.1", "purpose": "POR_TEST_LOT"}}]},
    )

    xlsx_response = splittable.download_xlsx(
        product="P1", root_lot_id="L1", wafer_ids="", prefix="KNOB",
        custom_name="", username="u", custom_cols="", display_mode="split_check",
        step_labels="1", exclude_not_null="1",
    )
    sheet = load_workbook(io.BytesIO(_response_bytes(xlsx_response))).active
    assert sheet.cell(4, 1).value == "purpose"
    assert sheet.cell(4, 6).value == "POR_TEST_LOT"

    csv_response = splittable.download_csv(
        product="P1", root_lot_id="L1", wafer_ids="", prefix="KNOB",
        custom_name="", transposed="true", username="u", custom_cols="",
        step_labels="", exclude_not_null="1",
    )
    csv_text = _response_bytes(csv_response).decode("utf-8-sig")
    assert "purpose,POR_TEST_LOT" in csv_text

