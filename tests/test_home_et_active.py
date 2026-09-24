from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from core import auth, flowi_routing, llm_adapter
from core.paths import PATHS
from routers import data_chat as api, et_time, reformatize


@pytest.fixture
def env(monkeypatch, tmp_path):
    user = {"username": "et_tester", "role": "admin"}
    monkeypatch.setattr(PATHS, "data_root", tmp_path)
    monkeypatch.setattr(flowi_routing, "PATHS", SimpleNamespace(db_root=tmp_path))
    monkeypatch.setattr(auth, "current_user", lambda req: user)
    monkeypatch.setattr(auth, "effective_permissions", lambda u: {"tabs": []})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)
    monkeypatch.setattr(reformatize, "products", lambda u: {"products": [{"product": "PRODA"}, {"product": "PRODB"}]})
    monkeypatch.setattr(reformatize, "list_items", lambda p, u: {"items": [{"alias": "VTH_IDX"}, {"alias": "IDS_IDX"}]})
    monkeypatch.setattr(et_time, "et_time_products", lambda *a, **k: {"products": ["PRODA", "PRODB"]})
    jobs, measures = [], []
    monkeypatch.setattr(reformatize, "download_start", lambda req, u: jobs.append(req) or {"job_id": "qa-job", "status": "queued"})
    monkeypatch.setattr(et_time, "et_time_measure", lambda req, **k: measures.append(k) or {"rows": [{"step_id": "ET100", "duration_sec": 42}]})
    monkeypatch.setattr(et_time, "et_time_trend", lambda req, **k: {"trend": {"ET100": [{"month": "2026-09", "avg_duration_sec": 42, "wafers": 2}]}})
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.require_flowi_user] = lambda: user
    client = TestClient(app)
    cid = str(uuid4())
    def send(prompt):
        response = client.post("/api/home-agent/orchestrate", json={"prompt": prompt, "conversation_id": cid})
        assert response.status_code == 200, response.text
        return response.json()
    return send, jobs, measures, user


def test_download_plain_text_clarification_chain(env):
    send, jobs, _, _ = env
    assert send("ET DATA 뽑아줘")["tool"]["missing"] == ["product"]
    assert send("PRODA")["tool"]["missing"] == ["scope"]
    assert send("최근 5일")["tool"]["missing"] == ["item"]
    assert not jobs
    result = send("VTH_IDX")
    assert result["tool"]["download_job"]["job_id"] == "qa-job"
    assert jobs[0].product == "PRODA" and jobs[0].days == 5 and jobs[0].items == ["VTH_IDX"]
    assert "pending_et" not in result["context"]
    assert result["success_prompt"] == "ET DATA 뽑아줘"


def test_time_lot_followup_preserves_exact_sublot(env):
    send, _, measures, _ = env
    assert send("PRODA ET 측정시간 보여줘")["tool"]["missing"] == ["lot"]
    result = send("AZAAA.2")
    assert result["tool"]["action"] == "et_time.measure"
    assert measures == [{"product": "PRODA", "root_lot_id": "AZAAA", "lot_id": "AZAAA.2"}]


def test_trend_uses_month_labels(env):
    result = env[0]("PRODA ET 측정시간 최근 3개월 추이 보여줘")
    assert result["tool"]["chart_result"]["points"][0]["x"] == "2026-09"
    assert result["tool"]["chart_result"]["points"][0]["color_value"] == "ET100"
    assert result["tool"]["chart_result"]["x_type"] == "category"
    assert result["evidence"]["steps"][0]["sources"] == ["/api/et-time/trend"]


def test_invalid_item_never_queues_then_cancel(env):
    send, jobs, _, _ = env
    result = send("PRODA ET DATA 최근 5일 UNKNOWN 뽑아줘")
    assert result["tool"]["missing"] == ["item"]
    assert send("__ET_ITEM=UNKNOWN")["tool"]["missing"] == ["item"]
    assert not jobs
    assert "pending_et" not in send("취소")["context"]


def test_product_switch_resets_download_scope(env):
    send, jobs, _, _ = env
    send("PRODA ET DATA 최근 5일 뽑아줘")
    assert send("PRODB")["tool"]["missing"] == ["scope"]
    assert not jobs


def test_et_feature_permission_is_enforced(env):
    send, jobs, _, user = env
    user["role"] = "user"
    result = send("PRODA ET DATA VTH_IDX 최근 5일 뽑아줘")
    assert result["tool"]["blocked"]
    assert not jobs


def test_ambiguous_item_choice_does_not_repeat_old_ambiguity(env):
    send, jobs, _, _ = env
    assert send("PRODA ET DATA VTH_IDX IDS_IDX 최근 5일 뽑아줘")["tool"]["missing"] == ["item"]
    result = send("2번")
    assert result["tool"]["action"] == "reformatize.download.start"
    assert jobs[0].items == ["IDS_IDX"]


def test_ambiguous_lot_choice_resolves(env):
    send, _, measures, _ = env
    assert send("PRODA ET 측정시간 AZAAA.1 AZBBB.2 보여줘")["tool"]["missing"] == ["lot"]
    send("2번")
    assert measures[0]["lot_id"] == "AZBBB.2"


def test_unknown_product_does_not_inherit_pending_product(env):
    send, jobs, _, _ = env
    send("PRODA ET DATA 최근 5일 뽑아줘")
    assert send("PRODUNKNOWN VTH_IDX")["tool"]["missing"] == ["product"]
    assert not jobs


def test_letters_only_root_lot_is_supported(env):
    send, _, measures, _ = env
    result = send("PRODA AZAAA ET 측정시간 보여줘")
    assert result["tool"]["action"] == "et_time.measure"
    assert measures == [{"product": "PRODA", "root_lot_id": "AZAAA", "lot_id": ""}]


@pytest.mark.parametrize("prompt", ["lot management 보여줘", "lot request 보여줘", "watchlist 보여줘", "lot tracker 보여줘"])
def test_new_intent_leaves_pending_et(prompt):
    from core.data_chat_et import dispatch
    context = {"pending_et": {"query": {"mode": "download", "product": "PRODA"}, "field": "item"}}
    assert dispatch(prompt, context) is None
    assert "pending_et" not in context
