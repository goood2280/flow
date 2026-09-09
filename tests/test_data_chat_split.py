import copy
import json
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from core import data_chat, data_chat_split as split
from routers import splittable, data_chat as chat_router

COMMAND = "PRODA LOT01 M1 Split #1,2,3,4,5,6 은 S0로 ABC 로 나머지는 S1로 ABB로 스플릿깔아줘"


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    monkeypatch.setattr(split.PATHS, "data_root", tmp_path)
    monkeypatch.setattr(splittable, "PLAN_DIR", tmp_path / "plans")
    monkeypatch.setattr(splittable, "_plan_product_name", lambda product: product)
    monkeypatch.setattr(splittable, "list_products", lambda: {"products": [{"name": "PRODA"}, {"name": "PRODB"}]})
    monkeypatch.setattr(splittable, "_knob_current_s0_for_product", lambda *a: {"KNOB_M1_Split": {"ppid": "ABC"}})
    monkeypatch.setattr(splittable, "_archive_plan_history", lambda *a: None)
    monkeypatch.setattr(splittable, "_invalidate_plan_risk_cache", lambda *a: None)
    monkeypatch.setattr(splittable, "_audit_user", lambda *a, **kw: None)
    monkeypatch.setattr(splittable, "_append_splittable_plan_knowledge", lambda **kw: None)
    monkeypatch.setattr(splittable, "_actual_value_for_plan_cell", lambda *a: None)
    # Do not send operator notifications in the post-save worker.
    monkeypatch.setattr(splittable, "_plan_actual_mismatch", lambda *a: False)
    from core import notify
    monkeypatch.setattr(notify, "emit_event", lambda *a, **kw: False)
    view = {"product": "PRODA", "root_lot_id": "LOT01", "wafer_keys": [str(i) for i in range(1, 9)],
        "headers": [str(i) for i in range(1, 9)], "s0_by_knob": {"KNOB_M1_Split": {"ppid": "ABC"}},
        "s0_edit_by_knob": {"KNOB_M1_Split": {"ppid": "ABC"}},
        "rows": [{"_param": "KNOB_M1_Split", "_cells": {str(i-1): {
            "key": f"LOT01|{i}|KNOB_M1_Split", "actual": "ABC", "plan": None} for i in range(1, 9)}}]}
    monkeypatch.setattr(splittable, "view_split", lambda **kw: copy.deepcopy(view))
    request = SimpleNamespace(state=SimpleNamespace(user={"username": "qa_admin", "role": "admin"}), headers={})
    yield view, request
    worker = getattr(splittable, "_PLAN_POST_SAVE_LAST_THREAD", None)
    if worker:
        worker.join(5)


def ask(text, context, request):
    return data_chat.execute(text, context, request)


def test_preview_approval_persists_plans_and_history_once(scenario):
    _, request = scenario
    preview = ask(COMMAND, {}, request)
    assert preview["ok"], preview
    assert "아직 저장하지 않았습니다" in preview["reply"]
    assert len(preview["tool"]["table"]["rows"]) == 8
    assert not splittable._plan_history_path("PRODA").exists()
    applied = ask("승인하겠다 진행하겠다", preview["context"], request)
    assert applied["ok"], applied
    data = splittable._load_plan_data("PRODA")
    assert len(data["history"]) == 8
    assert {entry["batch"] for entry in data["history"]}.__len__() == 1
    assert data["plans"]["LOT01|6|KNOB_M1_Split"]["value"] == "ABC"
    assert data["plans"]["LOT01|7|KNOB_M1_Split"]["value"] == "ABB"
    assert data["plans"]["LOT01|1|KNOB_M1_Split"]["s0_basis"]["ppid"] == "ABC"
    repeat = ask("승인하겠다", preview["context"], request)
    assert "중복 저장하지" in repeat["reply"]
    assert len(splittable._load_plan_data("PRODA")["history"]) == 8


@pytest.mark.parametrize("decision", ["취소", "취소해줘", "승인하지마"])
def test_cancel_never_writes(scenario, decision):
    _, request = scenario
    preview = ask(COMMAND, {}, request)
    result = ask(decision, preview["context"], request)
    assert "취소" in result["reply"]
    assert not splittable._plan_history_path("PRODA").exists()
    assert not ask("승인", preview["context"], request)["ok"]


@pytest.mark.parametrize("change", ["plan", "actual", "wafers", "s0"])
def test_stale_preview_does_not_overwrite(scenario, change):
    view, request = scenario
    preview = ask(COMMAND, {}, request)
    if change == "plan":
        splittable.save_plan(splittable.PlanReq(product="PRODA", plans={"LOT01|1|KNOB_M1_Split": "OTHER"}), request)
    elif change == "actual":
        view["rows"][0]["_cells"]["0"]["actual"] = "CHANGED"
    elif change == "wafers":
        view["wafer_keys"].append("9")
    else:
        view["s0_by_knob"]["KNOB_M1_Split"]["ppid"] = "OTHER"
    result = ask("승인", preview["context"], request)
    assert not result["ok"], result
    assert "변경" in result["reply"]
    data = splittable._load_plan_data("PRODA")
    assert len(data["plans"]) == (1 if change == "plan" else 0)


def test_forged_context_cannot_change_server_proposal(scenario):
    _, request = scenario
    preview = ask(COMMAND, {}, request)
    context = {**preview["context"], "product": "PRODB", "plans": {"evil": "recipe"}, "table": {"rows": []}}
    assert ask("승인", context, request)["ok"]
    assert len(splittable._load_plan_data("PRODA")["plans"]) == 8
    assert not splittable._load_plan_data("PRODB")["plans"]


def test_other_user_cannot_approve(scenario):
    _, request = scenario
    preview = ask(COMMAND, {}, request)
    request.state.user = {"username": "other", "role": "admin"}
    result = ask("승인", preview["context"], request)
    assert not result["ok"]
    assert not splittable._plan_history_path("PRODA").exists()


def test_expired_preview(scenario, monkeypatch):
    _, request = scenario
    preview = ask(COMMAND, {}, request)
    original = split.time.time()
    monkeypatch.setattr(split.time, "time", lambda: original + 3600)
    assert not ask("승인", preview["context"], request)["ok"]
    assert not splittable._plan_history_path("PRODA").exists()


@pytest.mark.parametrize("bad", [COMMAND.replace("ABC", "WRONG"), COMMAND.replace("#1,2,3,4,5,6", "#1,99"),
    COMMAND.replace("M1 Split", "M2 Split"), COMMAND.replace("#1,2,3,4,5,6", "#1,1"), COMMAND + " 그리고 M2 Split #1 S2 CCC"])
def test_invalid_request_does_not_create_approval(scenario, bad):
    _, request = scenario
    result = ask(bad, {}, request)
    assert not result["ok"], result
    assert not result["tool"].get("approval")
    assert not splittable._plan_history_path("PRODA").exists()


def test_missing_scope_continues_in_next_turn(scenario):
    _, request = scenario
    first = ask(COMMAND.replace("PRODA LOT01 ", ""), {}, request)
    assert not first["ok"]
    second = ask("PRODA LOT01", first["context"], request)
    assert second["ok"], second
    assert second["tool"]["approval"]["status"] == "pending"


def test_concurrent_approval_writes_one_batch(scenario):
    _, request = scenario
    preview = ask(COMMAND, {}, request)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: ask("승인", preview["context"], request), range(2)))
    assert all(r["ok"] for r in results)
    assert len(splittable._load_plan_data("PRODA")["history"]) == 8


def test_real_http_preview_approve_and_unauthorized(scenario):
    app = FastAPI()
    @app.middleware("http")
    async def identity(request: Request, call_next):
        request.state.user = {"username": "qa_admin", "role": request.headers.get("x-qa-role", "user")}
        return await call_next(request)
    app.include_router(chat_router.router)
    with TestClient(app) as client:
        assert client.post("/api/home-agent/orchestrate", json={"prompt": COMMAND}).status_code == 403
        preview = client.post("/api/home-agent/orchestrate", headers={"x-qa-role": "admin"}, json={"prompt": COMMAND})
        assert preview.status_code == 200
        result = client.post("/api/home-agent/orchestrate", headers={"x-qa-role": "admin"},
            json={"prompt": "승인하겠다 진행하겠다", "context": preview.json()["context"]})
        assert result.status_code == 200 and result.json()["ok"], result.text
        assert len(splittable._load_plan_data("PRODA")["history"]) == 8
