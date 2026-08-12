from pathlib import Path

from backend.core import lot_progress_cache
from backend.routers import lot_management


def test_lot_progress_summaries_counts_unique_wafers_and_uses_latest_step(monkeypatch):
    state = {
        "generated_at": "2026-08-11T00:00:00",
        "count": 3,
        "items": [
            {"product": "PROD_A", "lot_id": "LOT.1", "root_lot_id": "LOT", "wafer_id": "1", "step_id": "STEP_A", "update_time": "2026-08-11T01:00:00"},
            {"product": "PROD_A", "lot_id": "LOT.1", "root_lot_id": "LOT", "wafer_id": "2", "step_id": "STEP_B", "update_time": "2026-08-11T02:00:00"},
            {"product": "PROD_B", "lot_id": "LOT.1", "root_lot_id": "LOT", "wafer_id": "3", "step_id": "OTHER", "update_time": "2026-08-11T03:00:00"},
        ],
    }
    monkeypatch.setattr(lot_progress_cache, "read_lot_progress_cache", lambda **kwargs: state)

    summaries = lot_progress_cache.lot_progress_summaries(["lot.1"], product="PROD_A")

    assert summaries["LOT.1"]["wafer_count"] == 2
    assert summaries["LOT.1"]["step_id"] == "STEP_B"


def test_lot_management_required_columns_and_cache_overlay(monkeypatch):
    columns = lot_management._ensure_required_columns([
        {"id": "purpose", "label": "old purpose"},
        {"id": "lot_id", "label": "old lot"},
        {"id": "comment", "label": "old comment"},
        {"id": "owner", "label": "owner"},
    ])
    assert [column["id"] for column in columns] == [
        "purpose", "lot_id", "current_step_id", "step_desc", "qty", "comment", "owner",
    ]

    monkeypatch.setattr(
        lot_management,
        "_latest_status_by_lot",
        lambda product, lot_ids: {"LOT.1": {"step_id": "STEP_NOW", "step_desc": "ETCH", "wafer_count": 7}},
    )
    doc = {
        "product": "PROD_A",
        "columns": columns,
        "rows": [{"id": "row-1", "values": {"purpose": "ENG", "lot_id": "lot.1", "comment": "memo"}}],
        "colors": {},
    }

    hydrated = lot_management._with_latest_cache_fields(doc)

    assert hydrated["rows"][0]["values"]["current_step_id"] == "STEP_NOW"
    assert hydrated["rows"][0]["values"]["step_desc"] == "ETCH"
    assert hydrated["rows"][0]["values"]["qty"] == "7"
    assert "current_step_id" not in doc["rows"][0]["values"]


def test_computed_values_are_not_persisted():
    rows = lot_management._clean_rows(
        [{"id": "row-1", "values": {"lot_id": "LOT.1", "current_step_id": "STALE", "step_desc": "STALE DESC", "qty": "99"}}],
        {"lot_id", "current_step_id", "step_desc", "qty"},
    )

    assert rows[0]["values"]["current_step_id"] == ""
    assert rows[0]["values"]["step_desc"] == ""
    assert rows[0]["values"]["qty"] == ""


def test_vehicle_matching_step_desc_is_exact_and_unmatched_is_blank():
    index = (
        {("PROD_A", "STEP_NOW"): {"step_desc": "ETCH", "vehicle": "V1"}},
        {"STEP_NOW": {"step_desc": "ETCH", "vehicle": "V1"}},
    )

    attached = lot_management._attach_step_descriptions(
        {
            "LOT.1": {"step_id": "STEP_NOW", "wafer_count": 2},
            "LOT.2": {"step_id": "NOT_REGISTERED", "wafer_count": 1},
        },
        "PROD_A",
        index=index,
    )

    assert attached["LOT.1"]["step_desc"] == "ETCH"
    assert attached["LOT.2"]["step_desc"] == ""


def test_frontend_uses_context_palette_for_purpose_and_lot_id_backgrounds():
    source = (Path(__file__).parents[1] / "frontend" / "src" / "pages" / "My_LotManagement.jsx").read_text(encoding="utf-8")

    assert 'const COLORABLE_COLUMNS = new Set(["purpose", "lot_id"])' in source
    assert "openCellColorPicker(event,row.id,column.id)" in source
    assert 'aria-label="셀 배경색 팔레트"' in source
    assert "setCellColor(color)" in source
    assert "background:cellColor" in source
    assert "cycleCellColor" not in source
    assert "셀 오른쪽 원" not in source
    assert "borderRadius:\"50%\"" not in source


def test_frontend_searches_purpose_by_partial_case_insensitive_match():
    source = (Path(__file__).parents[1] / "frontend" / "src" / "pages" / "My_LotManagement.jsx").read_text(encoding="utf-8")

    assert 'type="search" value={purposeSearch}' in source
    assert 'placeholder="purpose 검색 (예: CS)"' in source
    assert '.toLowerCase().includes(query)' in source
    assert "purposeOptions" not in source
    assert "<select value={purposeFilter}" not in source
