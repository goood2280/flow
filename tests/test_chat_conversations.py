from uuid import uuid4
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from core import chat_conversations as store
from routers import data_chat


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store.PATHS, "data_root", tmp_path)
    app = FastAPI()
    app.include_router(data_chat.router)
    app.dependency_overrides[data_chat.require_flowi_user] = lambda: {"username": "alice", "role": "admin"}
    return TestClient(app, raise_server_exceptions=False)


def test_persist_restore_and_owner_isolation(client, monkeypatch):
    calls = []
    def execute(prompt, context, request, history):
        calls.append((context, history))
        return {"reply": "saved answer", "context": {"product": "P1"}}
    monkeypatch.setattr(data_chat.flowi_turn, "execute", execute)
    cid = str(uuid4())
    url = "/api/home-agent"
    assert client.post(url + "/orchestrate", json={"prompt": "first", "conversation_id": cid}).status_code == 200
    assert client.post(url + "/orchestrate", json={"prompt": "second", "conversation_id": cid,
        "context": {"product": "tampered"}, "history": [{"role": "user", "content": "tampered"}]}).status_code == 200
    assert calls[1] == ({"product": "P1"}, [{"role": "user", "content": "first"}, {"role": "assistant", "content": "saved answer"}])
    saved = client.get(url + "/conversations/" + cid).json()
    assert len(saved["messages"]) == 4
    assert saved["context"] == {"product": "P1"}
    assert client.get(url + "/conversations").json()["conversations"][0]["title"] == "first"
    client.app.dependency_overrides[data_chat.require_flowi_user] = lambda: {"username": "bob", "role": "admin"}
    assert client.get(url + "/conversations/" + cid).status_code == 404
    assert client.get(url + "/conversations").json() == {"conversations": []}
    assert client.post(url + "/orchestrate", json={"prompt": "separate", "conversation_id": cid}).status_code == 200
    assert calls[-1] == ({}, [])


def test_orchestrate_drops_response_profile_and_does_not_audit_prompt(client, monkeypatch):
    seen_contexts = []
    audit_calls = []

    def execute(prompt, context, request, history):
        seen_contexts.append(dict(context))
        return {"reply": "공통 응답", "context": context}

    monkeypatch.setattr(data_chat.flowi_turn, "execute", execute)
    monkeypatch.setattr(data_chat.audit, "record", lambda *args, **kwargs: audit_calls.append((args, kwargs)))
    secret = "private prompt only alice knows"
    response = client.post("/api/home-agent/orchestrate", json={
        "prompt": secret,
        "context": {"personalization": {"response_style": "detailed", "preferred_format": "table"}},
    })
    assert response.status_code == 200
    assert seen_contexts == [{}]
    assert audit_calls[0][1]["detail"] == response.json()["conversation_id"]
    assert secret not in str(audit_calls)


def test_failure_saved_and_new_chat_independent(client, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("internal secret")
    monkeypatch.setattr(data_chat.flowi_turn, "execute", fail)
    cid = str(uuid4())
    assert client.post("/api/home-agent/orchestrate", json={"prompt": "keep question", "conversation_id": cid}).status_code == 500
    state = store.read("alice", cid)
    assert state["messages"][0]["content"] == "keep question"
    assert state["messages"][1]["error"] is True
    assert "secret" not in str(state)
    assert store.history(state) == [{"role": "user", "content": "keep question"}]
    with store.turn("alice") as fresh:
        assert fresh["messages"] == []
        store.append(fresh, "user", "new")
    assert len(store.list_conversations("alice")) == 2


def test_concurrent_turn_rejected_without_lost_messages(client):
    cid = str(uuid4())
    with store.turn("alice", cid) as state:
        store.append(state, "user", "one")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            with store.turn("alice", cid):
                pass
    assert len(store.read("alice", cid)["messages"]) == 1


def test_archive_permissions_and_invalid_ids(client):
    assert client.get("/api/home-agent/conversations/not-a-uuid").status_code == 422
    client.app.dependency_overrides.clear()
    assert client.get("/api/home-agent/conversations").status_code == 401
    assert client.get("/api/home-agent/conversations/" + str(uuid4())).status_code == 401
