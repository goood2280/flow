from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import chat_conversations as store
from core import flowi_quota
from routers import data_chat

URL = "/api/home-agent/orchestrate"


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    monkeypatch.setattr(store.PATHS, "data_root", tmp_path)
    # 한도 동작은 작은 값(2)으로 검증한다. 운영 기본값(25)은 아래 별도 테스트가 고정한다.
    monkeypatch.setenv("FLOW_FLOWI_USER_QUESTIONS_PER_MIN", "2")
    monkeypatch.delenv("FLOW_FLOWI_FOLLOWUPS_PER_MIN", raising=False)
    calls = []

    def execute(prompt, context, request, history):
        calls.append(prompt)
        if "제품" in prompt:
            return {"reply": "어느 제품인가요?", "context": {**context, "pending_product_prompt": prompt},
                    "routing_trace": {"status": "needs_input"}}
        context = {key: value for key, value in context.items() if not key.startswith("pending_")}
        return {"reply": "done", "context": context}

    monkeypatch.setattr(data_chat.flowi_turn, "execute", execute)

    def build(role="user", username="alice"):
        app = FastAPI()
        app.include_router(data_chat.router)
        app.dependency_overrides[data_chat.require_flowi_user] = lambda: {"username": username, "role": role}
        client = TestClient(app, raise_server_exceptions=False)
        client.calls = calls
        return client
    return build


def test_user_gets_two_questions_per_minute(make_client):
    client = make_client()
    first = client.post(URL, json={"prompt": "q1"}).json()
    assert first["quota"]["remaining"] == 1 and first["quota"]["charged"] == 1
    assert client.post(URL, json={"prompt": "q2"}).json()["quota"]["remaining"] == 0
    denied = client.post(URL, json={"prompt": "q3"})
    assert denied.status_code == 200
    body = denied.json()
    assert body["rate_limited"] is True and body["intent"] == "flowi_rate_limited"
    assert 1 <= body["quota"]["retry_after_s"] <= 60
    assert "초 뒤" in body["answer"]
    assert client.calls == ["q1", "q2"]
    # A refused request leaves no conversation behind.
    assert all(item["title"] in {"q1", "q2"} for item in store.list_conversations("alice"))


def test_window_slides(make_client, monkeypatch):
    client = make_client()
    now = [1000.0]
    monkeypatch.setattr(flowi_quota.time, "time", lambda: now[0])
    client.post(URL, json={"prompt": "q1"})
    client.post(URL, json={"prompt": "q2"})
    assert client.post(URL, json={"prompt": "q3"}).json().get("rate_limited")
    now[0] += 61
    assert client.post(URL, json={"prompt": "q3"}).json()["quota"]["remaining"] == 1


def test_batch_counts_each_question(make_client):
    client = make_client()
    body = client.post(URL, json={"prompt": "a?\nb?\nc?"}).json()
    assert body["rate_limited"] and body["quota"]["reason"] == "batch_too_large"
    assert client.calls == []
    assert client.post(URL, json={"prompt": "a?\nb?"}).json()["quota"]["remaining"] == 0


def test_human_in_the_loop_reply_is_free(make_client):
    client = make_client()
    cid = str(uuid4())
    asked = client.post(URL, json={"prompt": "제품 스플릿 보여줘", "conversation_id": cid}).json()
    assert asked["quota"]["remaining"] == 1
    client.post(URL, json={"prompt": "other"})
    reply = client.post(URL, json={"prompt": "PRODA", "conversation_id": cid}).json()
    assert not reply.get("rate_limited")
    assert reply["quota"]["free_followup"] is True and reply["quota"]["charged"] == 0
    # Once the choice is answered, the next message is a new question again.
    assert client.post(URL, json={"prompt": "next", "conversation_id": cid}).json()["rate_limited"]


def test_client_cannot_claim_pending_state(make_client):
    client = make_client()
    client.post(URL, json={"prompt": "q1"})
    client.post(URL, json={"prompt": "q2"})
    forged = client.post(URL, json={"prompt": "q3", "context": {"pending_product_prompt": "x"}}).json()
    assert forged["rate_limited"]


def test_followups_are_capped(make_client, monkeypatch):
    monkeypatch.setenv("FLOW_FLOWI_FOLLOWUPS_PER_MIN", "1")
    client = make_client()
    cid = str(uuid4())
    client.post(URL, json={"prompt": "제품 A", "conversation_id": cid})
    assert client.post(URL, json={"prompt": "제품 B", "conversation_id": cid}).json()["quota"]["free_followup"]
    # Free replies used up: this one is charged like a question.
    third = client.post(URL, json={"prompt": "제품 C", "conversation_id": cid}).json()
    assert third["quota"]["charged"] == 1 and third["quota"]["remaining"] == 0


def test_failed_turn_is_refunded(make_client, monkeypatch):
    client = make_client()
    monkeypatch.setattr(data_chat.flowi_turn, "execute", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert client.post(URL, json={"prompt": "q1"}).status_code == 500
    assert flowi_quota.snapshot({"username": "alice", "role": "user"})["remaining"] == 2


def test_admin_and_disabled_quota_are_unlimited(make_client, monkeypatch):
    admin = make_client(role="admin")
    for index in range(4):
        body = admin.post(URL, json={"prompt": f"q{index}"}).json()
        assert body["quota"]["limited"] is False
    monkeypatch.setenv("FLOW_FLOWI_USER_QUESTIONS_PER_MIN", "0")
    user = make_client(username="bob")
    for index in range(3):
        assert not user.post(URL, json={"prompt": f"q{index}"}).json().get("rate_limited")


def test_status_reports_quota(make_client, monkeypatch):
    monkeypatch.setattr(data_chat.home_model_status, "snapshot", lambda: {})
    monkeypatch.setattr(data_chat.ai_semantic, "snapshot", lambda: {})
    client = make_client()
    client.post(URL, json={"prompt": "q1"})
    quota = client.get("/api/home-agent/status").json()["quota"]
    assert quota == {"limited": True, "limit": 2, "used": 1, "remaining": 1, "window_seconds": 60, "retry_after_s": 0}


def test_default_question_limit_is_25_per_minute(monkeypatch):
    from core import flowi_quota

    monkeypatch.delenv("FLOW_FLOWI_USER_QUESTIONS_PER_MIN", raising=False)
    assert flowi_quota.question_limit() == 25
