"""The human, never a model default or a lucky lot match, selects a product."""
import json
import logging
from pathlib import Path

import pytest
from fastapi import FastAPI

from core import data_chat, data_chat_features, llm_adapter, flowi_db_reference


@pytest.fixture
def grounded(monkeypatch):
    monkeypatch.setattr(data_chat, "available_product_names", lambda: ["REAL_ALPHA", "REAL_BETA"])
    monkeypatch.setattr(data_chat, "available_product_catalog", lambda: [
        {"product": "REAL_ALPHA", "tables": ["ML_TABLE_REAL_ALPHA"]},
        {"product": "REAL_BETA", "tables": ["ML_TABLE_REAL_BETA"]},
    ])
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)
    calls = []
    def execute(action, params, request):
        calls.append((action, params))
        return {"feature": "lot_management", "table": {"rows": [{"lot_id": "L1001"}]}, "message": "실제 결과입니다."}
    monkeypatch.setattr(data_chat_features, "execute_feature", execute)
    return calls


def test_missing_product_pauses_and_resumes_original_request(grounded):
    first = data_chat.execute("랏관리 보여줘", {}, None)
    assert first["tool"]["missing"] == ["product"]
    assert grounded == []
    assert first["tool"]["table"]["rows"] == [{"product": "REAL_ALPHA"}, {"product": "REAL_BETA"}]
    second = data_chat.execute("REAL_BETA", first["context"], None)
    assert grounded == [("lot_management.table", {"product": "REAL_BETA"})]
    assert second["context"]["confirmed_product"] == "REAL_BETA"
    assert not second["context"].get("pending_product_prompt")


@pytest.mark.parametrize("prompt", ["PRODA 랏관리 보여줘", "제품 UNKNOWN 랏관리 보여줘", "product=UNKNOWN 랏관리 보여줘"])
def test_absent_product_never_falls_back_to_previous(prompt, grounded):
    out = data_chat.execute(prompt, {"product": "REAL_ALPHA", "confirmed_product": "REAL_ALPHA"}, None)
    assert out["tool"]["missing"] == ["product"]
    assert grounded == []


def test_lot_location_does_not_lookup_without_human_product(grounded, monkeypatch):
    from core import lot_progress_cache
    monkeypatch.setattr(lot_progress_cache, "canonical_lot_progress_summaries", lambda *a, **kw: pytest.fail("queried before confirmation"))
    out = data_chat.execute("L1001 지금 어디야", {}, None)
    assert out["tool"]["missing"] == ["product"]


def test_model_cannot_supply_an_existing_but_unrequested_product(grounded, monkeypatch):
    monkeypatch.setattr(data_chat, "_feature_plan", lambda *a: ("lot_management.table", {"product": "REAL_ALPHA"}))
    out = data_chat.execute("현재 처리 현황을 알려줘", {}, None)
    assert out["tool"]["missing"] == ["product"]
    assert grounded == []


def test_confirmed_product_is_rechecked_against_current_db(grounded):
    out = data_chat.execute("랏관리 다시 보여줘", {"product": "REMOVED", "confirmed_product": "REMOVED"}, None)
    assert out["tool"]["missing"] == ["product"]
    assert not out["context"].get("confirmed_product")
    assert grounded == []


def test_confirmed_product_structure_uses_scoped_model_value(grounded, monkeypatch):
    from core import structure_model, product_semantics
    monkeypatch.setattr(product_semantics, "resolve_terms", lambda *args: [])
    calls = []
    def scoped(product, text, **kwargs):
        calls.append(product)
        return {"source": "관리자 GAA 구조 모델 + 선택 제품 오버라이드",
                "type": "logic", "variant": "6T", "sheet_dimensions_nm": {
                    "count_per_stack": 3, "center_pitch": 15, "active_stack_height": 35,
                    "sheets": [{"index": 1, "width": 30, "thickness": 5},
                               {"index": 2, "width": 34, "thickness": 5},
                               {"index": 3, "width": 38, "thickness": 5}]}}
    monkeypatch.setattr(structure_model, "prompt_context", scoped)
    out = data_chat.execute("REAL_ALPHA NS 2층 폭 얼마야", {}, None)
    assert calls == ["REAL_ALPHA"]
    assert "2층 폭 34 nm" in out["reply"]
    assert "실측 결과는 아닙니다" in out["reply"]


def test_cancel_pending_product_request(grounded):
    first = data_chat.execute("랏관리 보여줘", {}, None)
    out = data_chat.execute("취소", first["context"], None)
    assert not out["context"].get("pending_product_prompt")
    assert grounded == []


def test_selected_skill_reports_missing_model_instead_of_silently_ignoring_it(grounded):
    out = data_chat.execute("다시 확인해 줘", {"selected_skill": {"title": "조회 절차"}}, None)
    assert out["tool"]["missing"] == ["llm_connection"]
    assert grounded == []


def test_chart_cannot_execute_unconfirmed_or_absent_product(grounded, monkeypatch):
    from routers import filebrowser
    monkeypatch.setattr(filebrowser, "chart_builder_run", lambda *a, **k: pytest.fail("queried an unconfirmed product"))
    code = "Q1\nTABLE = ET\nPRODUCT = PRODA\nSQL = SELECT lot, value\n\nCHART\nTYPE = bar\nX = lot\nY = value\n"
    first = data_chat.execute("Q1 실행", {"definition_code": code}, None)
    assert first["tool"]["missing"] == ["product"]
    second = data_chat.execute("Q1 실행", {"definition_code": code, "product": "REAL_ALPHA", "confirmed_product": "REAL_ALPHA"}, None)
    assert second["tool"]["missing"] == ["chart_product"]


def test_legacy_preferences_do_not_change_common_response(grounded):
    concise = data_chat.execute("REAL_ALPHA 랏관리 보여줘", {"personalization": {"response_style": "concise"}}, None)
    detailed = data_chat.execute("REAL_ALPHA 랏관리 보여줘", {"personalization": {"response_style": "detailed"}}, None)
    assert concise["reply"] == detailed["reply"]
    assert "실제 결과입니다." in concise["reply"]
    assert "personalization" not in concise["context"]
    assert concise["tool"]["table"] == detailed["tool"]["table"]
    assert concise["tool"]["execution_trace"]


def test_planner_receives_actual_inventory_and_generated_md(grounded, monkeypatch):
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(flowi_db_reference, "load_reference_context", lambda: "actual schema reference")
    received = {}
    def plan(prompt, **kwargs):
        received.update(json.loads(prompt))
        return {"ok": True, "obj": {"action": "clarify", "params": {}}}
    monkeypatch.setattr(llm_adapter, "complete_json", plan)
    data_chat.execute("처리 현황을 알려줘", {}, None)
    assert received["actual_products"][0]["product"] == "REAL_ALPHA"
    assert received["db_reference"] == "actual schema reference"


def test_reference_router_is_loaded_without_retired_learning():
    from app_v2.runtime.router_loader import include_router_modules
    app = FastAPI()
    routers = Path(__file__).resolve().parents[1] / "backend" / "routers"
    loaded, failed = include_router_modules(app, routers, logging.getLogger(__name__), only=["flowi_reference", "flowi_learning"])
    assert not failed
    assert "flowi_reference" in loaded and "flowi_learning" not in loaded
    paths = {route.path for route in app.routes}
    assert "/api/flowi-learning/db-reference/generate" in paths
    assert "/api/flowi-learning/fewshots/save" not in paths
