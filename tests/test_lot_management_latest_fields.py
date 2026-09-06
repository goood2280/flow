import threading
from pathlib import Path

import pytest

from backend.core import lot_progress_cache
from backend.routers import lot_management


def test_lot_progress_read_does_not_wait_for_background_refresh(monkeypatch):
    """A long FAB scan must not make hot cache readers wait for its lock."""
    state = {
        "version": 1,
        "generated_at": "2026-08-20T00:00:00",
        "count": 1,
        "configured_source_root": lot_progress_cache.lot_progress_cache_source_root(),
        "column_mapping": lot_progress_cache.lot_progress_column_mapping(),
        "items": [{
            "product": "PROD_A",
            "lot_id": "LOT.1",
            "root_lot_id": "LOT",
            "wafer_id": "1",
            "step_id": "STEP_A",
        }],
    }
    lot_progress_cache._set_cache_state(state)

    refresh_entered = threading.Event()
    release_refresh = threading.Event()
    reader_finished = threading.Event()
    reader_result = []

    monkeypatch.setattr(lot_progress_cache, "_fresh_existing_cache_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(lot_progress_cache, "_try_acquire_refresh_lock", lambda: (object(), {}))
    monkeypatch.setattr(lot_progress_cache, "_release_refresh_lock", lambda _handle: None)
    monkeypatch.setattr(lot_progress_cache, "_append_refresh_log", lambda _entry: None)

    def block_refresh(*_args, **_kwargs):
        refresh_entered.set()
        release_refresh.wait(timeout=2)
        raise RuntimeError("stop test refresh")

    monkeypatch.setattr(lot_progress_cache, "_fab_source_roots", block_refresh)

    def run_refresh():
        try:
            lot_progress_cache.refresh_lot_progress_cache(force=True)
        except RuntimeError:
            pass

    def run_reader():
        reader_result.append(lot_progress_cache.read_lot_progress_cache(allow_stale=True))
        reader_finished.set()

    refresh_thread = threading.Thread(target=run_refresh)
    reader_thread = threading.Thread(target=run_reader)
    refresh_thread.start()
    assert refresh_entered.wait(timeout=1)
    reader_thread.start()
    read_completed_during_refresh = reader_finished.wait(timeout=0.5)

    release_refresh.set()
    refresh_thread.join(timeout=2)
    reader_thread.join(timeout=2)

    assert read_completed_during_refresh
    assert reader_result[0]["items"][0]["lot_id"] == "LOT.1"


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


def test_canonical_lot_progress_summaries_reads_dashboard_wip_cache(tmp_path, monkeypatch):
    pl = pytest.importorskip("polars")
    cache_path = tmp_path / "lot_progress_latest_lot_by_root_wafer.parquet"
    pl.DataFrame([
        {
            "cache_format_version": 4,
            "cache_source": "splittable_match_cache",
            "product": "PROD_A",
            "src_product": "PROD_A",
            "root_lot_id": "ROOT.1",
            "wafer_id": "1",
            "lot_id": "LOT.1",
            "step_id": "STEP_OLD",
            "function_step": "OLD STEP",
            "lot_type": "ENG",
            "tkout_time": "2026-08-11T01:00:00",
            "update_time": "2026-08-20T00:00:00",
        },
        {
            "cache_format_version": 4,
            "cache_source": "splittable_match_cache",
            "product": "PROD_A",
            "src_product": "PROD_A",
            "root_lot_id": "ROOT.1",
            "wafer_id": "2",
            "lot_id": "LOT.1",
            "step_id": "STEP_NOW",
            "function_step": "CURRENT STEP",
            "lot_type": "ENG",
            "tkout_time": "2026-08-11T02:00:00",
            "update_time": "2026-08-20T00:00:00",
        },
        {
            "cache_format_version": 4,
            "cache_source": "splittable_match_cache",
            "product": "OTHER",
            "src_product": "OTHER",
            "root_lot_id": "ROOT.1",
            "wafer_id": "3",
            "lot_id": "LOT.1",
            "step_id": "WRONG_PRODUCT",
            "function_step": "WRONG PRODUCT",
            "lot_type": "ENG",
            "tkout_time": "2026-08-11T03:00:00",
            "update_time": "2026-08-20T00:00:00",
        },
    ]).write_parquet(cache_path)
    monkeypatch.setattr(lot_progress_cache, "filebrowser_cache_parquet_file", lambda: cache_path)

    summaries = lot_progress_cache.canonical_lot_progress_summaries(
        ["lot.1"],
        product="ML_TABLE_PROD_A",
    )

    assert summaries["LOT.1"]["wafer_count"] == 2
    assert summaries["LOT.1"]["step_id"] == "STEP_NOW"
    assert summaries["LOT.1"]["func_step"] == "CURRENT STEP"


def test_canonical_lot_progress_qty_matches_exact_lot_id_not_root_lot(tmp_path, monkeypatch):
    pl = pytest.importorskip("polars")
    cache_path = tmp_path / "lot_progress_latest_lot_by_root_wafer.parquet"
    common = {
        "cache_format_version": 4,
        "cache_source": "splittable_match_cache",
        "product": "PROD_A",
        "src_product": "PROD_A",
        "step_id": "STEP_NOW",
        "function_step": "CURRENT STEP",
        "lot_type": "ENG",
        "tkout_time": "2026-08-11T02:00:00",
        "update_time": "2026-08-20T00:00:00",
    }
    pl.DataFrame([
        {**common, "root_lot_id": "ROOT.1", "lot_id": "LOT.1", "wafer_id": "1"},
        {**common, "root_lot_id": "ROOT.1", "lot_id": "LOT.1", "wafer_id": "1"},
        {**common, "root_lot_id": "ROOT.1", "lot_id": "LOT.1", "wafer_id": "2"},
        {**common, "root_lot_id": "LOT.1", "lot_id": "SIBLING.1", "wafer_id": "3"},
    ]).write_parquet(cache_path)
    monkeypatch.setattr(lot_progress_cache, "filebrowser_cache_parquet_file", lambda: cache_path)

    summaries = lot_progress_cache.canonical_lot_progress_summaries(
        ["LOT.1"],
        product="ML_TABLE_PROD_A",
    )

    assert summaries["LOT.1"]["wafer_count"] == 2
    assert summaries["LOT.1"]["wafer_ids"] == ["1", "2"]


def test_lot_management_required_columns_and_cache_overlay(monkeypatch):
    columns = lot_management._ensure_required_columns([
        {"id": "purpose", "label": "old purpose"},
        {"id": "lot_id", "label": "old lot"},
        {"id": "comment", "label": "old comment"},
        {"id": "owner", "label": "owner"},
    ])
    assert [column["id"] for column in columns] == [
        "purpose", "lot_id", "current_step_id", "alert_step_id", "step_desc", "qty", "comment", "owner",
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


def test_lot_management_can_return_persisted_table_without_status_overlay(monkeypatch):
    doc = {"product": "PROD_A", "columns": [], "rows": [], "colors": {}}
    monkeypatch.setattr(lot_management, "current_user", lambda _request: {"username": "tester"})
    monkeypatch.setattr(lot_management, "_load", lambda _product: doc)
    monkeypatch.setattr(
        lot_management,
        "_with_latest_cache_fields",
        lambda _doc: (_ for _ in ()).throw(AssertionError("status overlay should be deferred")),
    )

    result = lot_management.get_table(object(), "PROD_A", include_status=False)

    assert result is doc


def test_lot_management_batch_statuses_deduplicate_lot_ids(monkeypatch):
    monkeypatch.setattr(lot_management, "current_user", lambda _request: {"username": "tester"})
    monkeypatch.setattr(
        lot_management,
        "_latest_status_by_lot",
        lambda product, lot_ids: {
            "LOT.1": {"step_id": "STEP_NOW", "step_desc": "ETCH", "wafer_count": 7},
        },
    )

    result = lot_management.get_statuses(
        lot_management.StatusBatchRequest(product="PROD_A", lot_ids=["lot.1", "LOT.1", ""]),
        object(),
    )

    assert result["statuses"] == {
        "LOT.1": {"current_step_id": "STEP_NOW", "step_desc": "ETCH", "qty": 7},
    }


def test_lot_management_missing_qty_is_null_for_dash(monkeypatch):
    monkeypatch.setattr(lot_management, "current_user", lambda _request: {"username": "tester"})
    monkeypatch.setattr(
        lot_management,
        "_latest_status_by_lot",
        lambda product, lot_ids: {"MISSING.1": {"step_id": "", "step_desc": "", "wafer_count": 0}},
    )

    result = lot_management.get_statuses(
        lot_management.StatusBatchRequest(product="PROD_A", lot_ids=["MISSING.1"]),
        object(),
    )

    assert result["statuses"]["MISSING.1"]["qty"] is None


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


def test_vehicle_matching_normalizes_product_and_falls_back_to_cached_function_step():
    index = (
        {("PROD_A", "STEP_NOW"): {"step_desc": "ETCH", "vehicle": "V1"}},
        {},
    )

    attached = lot_management._attach_step_descriptions(
        {
            "LOT.1": {"step_id": "STEP_NOW", "func_step": "CACHE DESC", "wafer_count": 2},
            "LOT.2": {"step_id": "NOT_REGISTERED", "func_step": "CACHE DESC", "wafer_count": 1},
        },
        "ML_TABLE_PROD_A",
        index=index,
    )

    assert attached["LOT.1"]["step_desc"] == "ETCH"
    assert attached["LOT.2"]["step_desc"] == "CACHE DESC"


def test_vehicle_matching_is_reloaded_and_does_not_leak_another_product(monkeypatch):
    from core import fab_reference

    monkeypatch.setattr(
        fab_reference,
        "vehicle_matching_rows",
        lambda: [
            {"vehicle": "V1", "product": "PROD_A", "step_id": "STEP_NOW", "step_desc": "ETCH_A"},
            {"vehicle": "V2", "product": "PROD_B", "step_id": "STEP_NOW", "step_desc": "ETCH_B"},
            {"vehicle": "V2", "product": "PROD_B", "step_id": "ONLY_B", "step_desc": "B_ONLY"},
        ],
    )

    attached = lot_management._attach_step_descriptions(
        {
            "LOT.1": {"step_id": "STEP_NOW", "func_step": "CACHE_A"},
            "LOT.2": {"step_id": "ONLY_B", "func_step": "CACHE_FALLBACK"},
        },
        "ML_TABLE_PROD_A",
    )

    assert attached["LOT.1"]["step_desc"] == "ETCH_A"
    assert attached["LOT.2"]["step_desc"] == "CACHE_FALLBACK"


def test_frontend_uses_context_palette_for_purpose_and_lot_id_backgrounds():
    source = (
        Path(__file__).parents[1]
        / "frontend"
        / "src"
        / "features"
        / "lotmanagement"
        / "My_LotManagement.jsx"
    ).read_text(encoding="utf-8")

    assert 'const COLORABLE_COLUMNS = new Set(["purpose", "lot_id"])' in source
    assert "openCellColorPicker(event,row.id,column.id)" in source
    assert 'aria-label="셀 배경색 팔레트"' in source
    assert "setCellColor(color)" in source
    assert "background:cellColor" in source
    assert "cycleCellColor" not in source
    assert "셀 오른쪽 원" not in source
    assert "borderRadius:\"50%\"" not in source


def test_frontend_searches_purpose_by_partial_case_insensitive_match():
    source = (
        Path(__file__).parents[1]
        / "frontend"
        / "src"
        / "features"
        / "lotmanagement"
        / "My_LotManagement.jsx"
    ).read_text(encoding="utf-8")

    assert 'type="search" value={purposeSearch}' in source
    assert 'placeholder="purpose 검색 (예: CS)"' in source
    assert '.toLowerCase().includes(query)' in source
    assert "purposeOptions" not in source
    assert "<select value={purposeFilter}" not in source


def test_frontend_lot_table_loading_has_timeout_error_and_retry_states():
    source = (
        Path(__file__).parents[1]
        / "frontend"
        / "src"
        / "features"
        / "lotmanagement"
        / "My_LotManagement.jsx"
    ).read_text(encoding="utf-8")

    assert "const TABLE_LOAD_TIMEOUT_MS = 20_000" in source
    assert "setTableError(message)" in source
    assert "setTableReloadToken(value => value + 1)" in source
    assert "setProductsError(message)" in source
    assert 'loading || !work ? <Loading text="랏 관리 표 로딩..."' not in source


def test_frontend_defers_status_candidates_and_split_table_bundle():
    source = (
        Path(__file__).parents[1]
        / "frontend"
        / "src"
        / "features"
        / "lotmanagement"
        / "My_LotManagement.jsx"
    ).read_text(encoding="utf-8")

    assert 'const loadSplitTableModule = () => import("../splittable/My_SplitTable")' in source
    assert "const LazySplitTable = lazy(loadSplitTableModule)" in source
    assert "onMouseEnter={preloadSplitTable}" in source
    assert "include_status=false" in source
    assert 'sf(`${API}/statuses`' in source
    assert "const LOT_CANDIDATE_PREVIEW_LIMIT = 300" in source
    assert "limit=50000" not in source
    assert "onSearch={searchLotCandidates}" in source


def test_lot_id_click_opens_lot_notes_modal_without_split_view():
    root = Path(__file__).parents[1] / "frontend" / "src" / "features"
    lot_source = (root / "lotmanagement" / "My_LotManagement.jsx").read_text(encoding="utf-8")
    notes_modal_source = (root / "lotmanagement" / "LotNotesModal.jsx").read_text(encoding="utf-8")

    assert 'onClick={!editing&&column.id==="lot_id"' in lot_source
    assert "openNotesModal" in lot_source
    assert "<LotNotesModal" in lot_source
    assert "openSplitView(value,true)" not in lot_source
    assert '<th title="SplitTable 보기" style={{width:70}}>View</th>' in lot_source
    assert 'title="공유 노트 열기"' not in lot_source
    assert "/notes/save" in notes_modal_source
    assert "/notes/delete" in notes_modal_source


def test_frontend_qty_uses_dash_until_positive_wafer_count_arrives():
    source = (
        Path(__file__).parents[1]
        / "frontend"
        / "src"
        / "features"
        / "lotmanagement"
        / "My_LotManagement.jsx"
    ).read_text(encoding="utf-8")

    assert 'const formatQty = value => Number(value) > 0 ? String(Number(value)) : "-"' in source
    assert "qty:formatQty(status.qty)" in source
    assert "qty:String(status.qty ?? 0)" not in source
