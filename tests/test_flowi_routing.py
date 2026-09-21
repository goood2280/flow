from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import data_chat, flowi_routing, flowi_turn, llm_usage


@pytest.fixture
def route_db(monkeypatch, tmp_path):
    monkeypatch.setattr(flowi_routing, "PATHS", SimpleNamespace(db_root=tmp_path))
    return tmp_path


def rule(**overrides):
    payload = {
        "title": "Lot location",
        "question": "where is {product} {lot}",
        "normalized_question": "show location for {product} {lot}",
        "route": "yield_map.map",
        "segments": [
            {"kind": "product", "text": "{product}", "value": "{product}"},
            {"kind": "lot", "text": "{lot}", "value": "{lot}"},
        ],
        "enabled": True,
    }
    payload.update(overrides)
    return payload


def test_rule_crud_disabled_exact_match_and_db_root(route_db):
    saved = flowi_routing.save_rule(rule(), "alice")
    assert flowi_routing.storage_path().parent == route_db / "flowi"
    assert flowi_routing.resolve("where is PROD_A LOT01")["matched"]
    assert flowi_routing.resolve("where is PROD_A LOT01 extra")["matched"] is False
    saved["enabled"] = False
    flowi_routing.save_rule(saved, "alice")
    assert flowi_routing.resolve("where is PROD_A LOT01")["matched"] is False


def test_rule_validation_preserves_variables_and_rejects_conflicts(route_db):
    with pytest.raises(ValueError):
        flowi_routing.save_rule(rule(normalized_question="show location for {product}"), "alice")
    with pytest.raises(ValueError):
        flowi_routing.save_rule(rule(question="where is {product} {product}"), "alice")
    with pytest.raises(ValueError):
        flowi_routing.save_rule(rule(route="arbitrary.sql"), "alice")
    saved = flowi_routing.save_rule(rule(), "alice")
    assert flowi_routing.delete_rule(saved["id"]) is None
    with pytest.raises(FileNotFoundError):
        flowi_routing.delete_rule(saved["id"])


def test_turn_budget_caps_actual_provider_attempts(route_db, monkeypatch):
    calls = []
    with llm_usage.turn_budget(6) as usage:
        for _ in range(7):
            error = llm_usage.reserve_attempt()
            calls.append(error)
    assert calls[:6] == [""] * 6
    assert "call limit" in calls[6]
    assert usage == {"llm_calls_used": 6, "llm_call_limit": 6}
    assert llm_usage.snapshot()["minute_calls_used"] == 6


def test_multi_question_cap_and_deferred_questions(monkeypatch):
    seen = []

    def fake_one(question, context, request, history):
        seen.append(question)
        return {"ok": True, "reply": question, "context": context, "tool": {"feature": "location"}}

    monkeypatch.setattr(flowi_turn, "_one", fake_one)
    result = flowi_turn.execute("q1? q2? q3? q4? q5?", {}, None)
    assert result["ok"] is False
    assert result["tool"]["missing"] == ["question_limit"]
    assert seen == []

    result = flowi_turn.execute("q1? q2? q3? q4?", {}, None)
    assert result["batch"] == {"total": 4, "completed": 4, "max_questions": 4}
    assert result["usage"]["llm_calls_used"] == 0
    assert [item["status"] for item in result["questions"]] == ["completed"] * 4


def test_approved_route_uses_current_product_and_does_not_reuse_old_lot(route_db, monkeypatch):
    flowi_routing.save_rule(rule(question="show {product} location", normalized_question="show {product} location", segments=[]), "alice")
    monkeypatch.setattr(data_chat, "available_product_names", lambda: ["PROD_A", "PROD_B"])
    monkeypatch.setattr(data_chat, "extract_lot_tokens", lambda text, products=None: [])
    captured = []

    def fake_execute(prompt, context, request, history=None, approved_plan=None):
        captured.append((prompt, approved_plan))
        return {"ok": True, "reply": "done", "context": context, "tool": {"feature": "location"}}

    monkeypatch.setattr(data_chat, "execute", fake_execute)
    result = flowi_turn._one("show PROD_B location", {"confirmed_product": "PROD_A", "lot_id": "OLD01"}, None, [])
    assert result["routing_trace"]["rule_id"]
    assert captured[0][1][1]["product"] == "PROD_B"
    assert "lot_id" not in captured[0][1][1]


def test_unknown_product_is_rejected_by_real_route(route_db, monkeypatch):
    flowi_routing.save_rule(rule(question="show {product} location", normalized_question="show {product} location", segments=[]), "alice")
    monkeypatch.setattr(data_chat, "available_product_names", lambda: ["PROD_A"])
    result = flowi_turn._one("show PROD_UNKNOWN location", {}, None, [])
    assert result["ok"] is False
    assert "product" in result["tool"]["missing"]


def test_routing_admin_endpoints_are_protected(route_db):
    from routers import flowi_routes as flowi_learning

    app = FastAPI()
    app.include_router(flowi_learning.router)
    with TestClient(app) as client:
        assert client.get("/api/flowi-learning/routing").status_code in {401, 403}
        assert client.post("/api/flowi-learning/routing/preview", json={"question": "x"}).status_code in {401, 403}


def test_overlapping_rules_do_not_choose_latest_or_execute(route_db, monkeypatch):
    flowi_routing.save_rule(rule(), "alice")
    flowi_routing.save_rule(rule(title="duplicate"), "bob")
    assert flowi_routing.resolve("where is PROD_A LOT01")["ambiguous"]
    monkeypatch.setattr(data_chat, "execute", lambda *a, **k: pytest.fail("ambiguous rule executed"))
    monkeypatch.setattr(data_chat, "available_product_names", lambda: ["PROD_A"])
    out = flowi_turn.execute("where is PROD_A LOT01", {}, None)
    assert out["tool"]["missing"] == ["routing_rule"]


def test_captured_identifiers_cannot_be_replaced_with_old_values(route_db):
    with pytest.raises(ValueError, match="변수"):
        flowi_routing.save_rule(rule(segments=[{"kind": "lot", "text": "{lot}", "value": "OLDLOT"}]), "alice")
    flowi_routing.save_rule(rule(), "alice")
    out = flowi_routing.resolve("where is PROD_B NEWLOT")
    assert out["bindings"] == {"product": "PROD_B", "lot": "NEWLOT"}
    assert out["normalized_question"] == "show location for PROD_B NEWLOT"


def test_missing_input_and_failure_keep_later_questions_visible(monkeypatch):
    seen = []
    def execute(question, context, request, history):
        seen.append(question)
        return data_chat.reply("제품?", context={"pending_product_prompt": question}, tool={"missing": ["product"]}, ok=False)
    monkeypatch.setattr(flowi_turn, "_one", execute)
    out = flowi_turn.execute("위치?\n트래커?", {}, None)
    assert seen == ["위치?"]
    assert [r["status"] for r in out["questions"]] == ["needs_input", "deferred"]
    assert out["context"]["pending_product_prompt"] == "위치?"
    def fail(*args):
        raise RuntimeError("sensitive internal detail")
    monkeypatch.setattr(flowi_turn, "_one", fail)
    out = flowi_turn.execute("위치?\n트래커?", {}, None)
    assert [r["status"] for r in out["questions"]] == ["failed", "deferred"]
    assert "sensitive" not in str(out)


@pytest.mark.parametrize("decision", ["진행", "네", "예", "저장해", "승인", "취소"])
def test_batch_cannot_approve_a_pending_write(decision, monkeypatch):
    monkeypatch.setattr(flowi_turn, "_one", lambda *a, **k: pytest.fail("batch approval executed"))
    out = flowi_turn.execute(f"{decision}\n대시보드", {"pending_split_id": "pending"}, None)
    assert out["questions"][0]["response"]["tool"]["missing"] == ["separate_approval"]
    assert out["questions"][1]["status"] == "deferred"


@pytest.mark.parametrize("original,normalized", [("승인 기록 보여줘", "승인"), ("처리해", "네"), ("잘 됐어", "저장해")])
def test_rule_rewrite_never_becomes_write_approval(route_db, monkeypatch, original, normalized):
    flowi_routing.save_rule(rule(question=original, normalized_question=normalized, route="auto", segments=[]), "alice")
    monkeypatch.setattr(data_chat, "available_product_names", lambda: [])
    monkeypatch.setattr(data_chat, "execute", lambda *a, **k: pytest.fail("rule approved write"))
    out = flowi_turn.execute(original, {"pending_split_id": "pending"}, None)
    assert out["tool"]["missing"] == ["routing_rule"]


def test_budget_global_rejection_not_charged_and_requests_reset(monkeypatch):
    monkeypatch.setenv("FLOW_LLM_MINUTE_CALL_LIMIT", "1")
    with llm_usage.turn_budget() as usage:
        assert llm_usage.reserve_attempt() == ""
        assert "minute call limit" in llm_usage.reserve_attempt()
        assert usage["llm_calls_used"] == 1
        with llm_usage.turn_budget() as nested:
            assert nested is usage
    with llm_usage.turn_budget() as fresh:
        assert fresh["llm_calls_used"] == 0


def test_rule_question_punctuation_and_sql_lines_are_preserved():
    assert flowi_turn.split_questions("위치? 이슈?") == ["위치?", "이슈?"]
    assert flowi_turn.split_questions("Q1\nSQL = SELECT * FROM ET\nCHART\nTYPE = bar") == ["Q1\nSQL = SELECT * FROM ET\nCHART\nTYPE = bar"]


def test_trace_missing_result_never_claims_previous_execution(monkeypatch):
    monkeypatch.setattr(data_chat, "available_product_catalog", lambda: [])
    trace = data_chat.extract_execution_trace("위치", {"context": {"last_action": "splittable"}, "tool": {"missing": ["product"]}})
    assert trace["feature"] == ""
    assert trace["query"] == trace["illustrative_query"] == ""
    assert trace["sources"] == []


def test_exact_observed_product_wins_over_evt_alias():
    assert data_chat.product_candidates("PROD0EVT5 위치", ["PROD05", "PROD0EVT5"]) == ["PROD0EVT5"]


def test_unknown_bound_product_cannot_reuse_confirmed_previous_product(route_db, monkeypatch):
    flowi_routing.save_rule(rule(question="{product} 현황", normalized_question="{product} 현황", segments=[]), "alice")
    monkeypatch.setattr(data_chat, "available_product_names", lambda: ["REAL_ALPHA"])
    monkeypatch.setattr(data_chat, "execute", lambda *a, **k: pytest.fail("queried previous product"))
    out = flowi_turn.execute("UNKNOWN_X 현황", {"confirmed_product": "REAL_ALPHA"}, None)
    assert out["tool"]["missing"] == ["product"]


def test_admin_rule_api_persists_and_real_dispatch_uses_current_identifiers(route_db, monkeypatch):
    from core import data_chat_features
    from routers import flowi_routes as flowi_learning
    monkeypatch.setattr(flowi_learning, "_require_admin", lambda request: {"username": "reviewer", "role": "admin"})
    app = FastAPI()
    app.include_router(flowi_learning.router)
    with TestClient(app) as client:
        payload = rule(question="{product} {lot} 현황", normalized_question="{product} {lot} 현황", route="lot_management.status")
        response = client.post("/api/flowi-learning/routing/rules", json=payload)
        assert response.status_code == 200
        saved = response.json()["rule"]
        assert saved["actor"] == "reviewer"
        assert client.get("/api/flowi-learning/routing").json()["rules"][0]["id"] == saved["id"]
        preview = client.post("/api/flowi-learning/routing/preview", json={"question": "REAL_ALPHA A1001 현황"}).json()
        assert preview["bindings"] == {"product": "REAL_ALPHA", "lot": "A1001"}
    monkeypatch.setattr(data_chat, "available_product_names", lambda: ["REAL_ALPHA"])
    monkeypatch.setattr(data_chat, "available_product_catalog", lambda: [{"product": "REAL_ALPHA", "tables": []}])
    from core import product_semantics
    monkeypatch.setattr(product_semantics, "resolve_terms", lambda *a: [])
    calls = []
    def execute(action, params, request):
        calls.append((action, params))
        return {"feature": "lot_management", "action": action, "message": "실제 조회", "sources": ["fixture"], "table": {"rows": [{"lot": params["lot_id"]}]}}
    monkeypatch.setattr(data_chat_features, "execute_feature", execute)
    out = flowi_turn.execute("REAL_ALPHA A1001 현황", {}, None)
    assert calls == [("lot_management.status", {"product": "REAL_ALPHA", "lot_id": "A1001"})]
    assert out["routing_trace"]["sources"] == ["fixture"]
    assert [e["stage"] for e in out["routing_trace"]["events"]] == ["product_scope", "approved_route", "execute_feature"]
    assert out["usage"]["llm_calls_used"] == 0


def test_current_routers_are_discovered_and_packaged_without_retired_agent():
    import ast
    import importlib.util
    import logging
    from pathlib import Path
    from app_v2.runtime.router_loader import include_router_modules
    root = Path(__file__).resolve().parents[1]
    app = FastAPI()
    loaded, failed = include_router_modules(app, root / "backend/routers", logging.getLogger("test"), only=["flowi_routes"])
    assert loaded == ["flowi_routes"] and failed == []
    assert "/api/flowi-learning/routing" in {r.path for r in app.routes}
    home_app = FastAPI()
    loaded, failed = include_router_modules(home_app, root / "backend/routers", logging.getLogger("test"), only=["data_chat"])
    assert loaded == ["data_chat"] and failed == []
    assert {
        "/api/home-agent/status",
        "/api/home-agent/probe",
        "/api/home-agent/conversations",
    } <= {r.path for r in home_app.routes}
    spec = importlib.util.spec_from_file_location("flowi_test_builder", root / "_build_setup.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    files = {p.relative_to(root).as_posix() for p in builder.gather_files()}
    assert {
        "backend/routers/data_chat.py",
        "backend/routers/flowi_routes.py",
        "backend/core/flowi_gate.py",
        "backend/core/flowi_routing.py",
        "backend/core/flowi_turn.py",
        "frontend/src/features/admin/FlowiRoutesPanel.jsx",
    } <= files
    assert "backend/routers/flowi_learning.py" not in files
    app_tree = ast.parse((root / "backend/app.py").read_text(encoding="utf-8"))
    required_sources = next(
        ast.literal_eval(node.value)
        for node in app_tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_REQUIRED_BUNDLED_BACKEND_SOURCES" for target in node.targets)
    )
    assert "backend/core/flowi_gate.py" in required_sources
