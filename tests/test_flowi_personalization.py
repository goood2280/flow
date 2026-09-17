from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import chat_conversations, flowi_personalization
from routers import data_chat


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_conversations.PATHS, "data_root", tmp_path)
    app = FastAPI()
    app.include_router(data_chat.router)
    app.dependency_overrides[data_chat.require_flowi_user] = lambda: {"username": "alice", "role": "admin"}
    return TestClient(app)


def test_automatic_shared_template_never_contains_private_prompt(client):
    secret = "PRIVATE_PRODUCT PRIVATE_LOT 선두 위치 조회해줘"
    skill = flowi_personalization.auto_create_skill_from_prompt(secret, "location")
    assert skill["shared"]
    assert "PRIVATE_PRODUCT" not in str(skill)
    assert "PRIVATE_LOT" not in str(skill)


def test_admin_learning_reviews_all_users_but_home_stays_owner_scoped(client, monkeypatch):
    from routers import flowi_routes as flowi_learning
    monkeypatch.setattr(flowi_learning, "_require_admin", lambda request: {"username": "bob", "role": "admin"})
    with chat_conversations.turn("alice", None) as state:
        chat_conversations.append(state, "user", "PRIVATE_QUESTION")
        identifier = state["id"]
    assert flowi_learning.list_conversations(None)["conversations"][0]["username"] == "alice"
    assert flowi_learning.read_conversation(None, identifier)["messages"][0]["content"] == "PRIVATE_QUESTION"
    assert chat_conversations.list_conversations("bob") == []
    with pytest.raises(FileNotFoundError):
        chat_conversations.read("bob", identifier)


def test_private_conversation_common_response_and_explicit_skill_use(client, monkeypatch):
    seen = []
    def execute(prompt, context, request, history):
        seen.append(dict(context))
        return {"reply": "done", "context": context, "tool": {"feature": "dashboard", "action": "dashboard.summary", "table": {"rows": [{"secret": "lot-123"}]}}}
    monkeypatch.setattr(data_chat.flowi_turn, "execute", execute)
    cid = str(uuid4())
    first = client.post("/api/home-agent/orchestrate", json={"conversation_id": cid, "prompt": "summary", "context": {
        "personalization": {"response_style": "detailed"}, "selected_skill": {"procedure": "evil"},
        "confirmed_product": "forged", "pending_product_prompt": "forged"}})
    assert first.status_code == 200
    assert "personalization" not in seen[0]
    assert "selected_skill" not in seen[0]
    assert "confirmed_product" not in seen[0]
    assert "pending_product_prompt" not in seen[0]
    message_id = first.json()["message_id"]
    draft = client.get(f"/api/home-agent/personal-skills/draft/{cid}/{message_id}")
    assert draft.status_code == 200
    assert "lot-123" not in str(draft.json())
    assert draft.json()["draft"]["action"] == "dashboard.summary"
    assert "TAT" in draft.json()["draft"]["procedure"]
    payload = {"conversation_id": cid, "message_id": message_id, "title": "요약 절차",
               "procedure": "질문에서 조회 대상을 확인하고 대시보드 요약을 설명한다.", "shared": False}
    saved = client.post("/api/home-agent/personal-skills", json=payload)
    assert saved.status_code == 200
    skill_id = saved.json()["skill"]["id"]
    assert "lot-123" not in str(saved.json())
    second = client.post("/api/home-agent/orchestrate", json={"conversation_id": cid, "prompt": "repeat", "skill_id": skill_id})
    assert second.status_code == 200
    assert seen[1]["selected_skill"]["procedure"] == payload["procedure"]
    assert seen[1]["selected_skill"]["action"] == "dashboard.summary"
    assert client.post("/api/home-agent/orchestrate", json={"conversation_id": cid, "prompt": "ordinary"}).status_code == 200
    assert "selected_skill" not in seen[2]

    client.app.dependency_overrides[data_chat.require_flowi_user] = lambda: {"username": "bob", "role": "admin"}
    assert client.get("/api/home-agent/profile").status_code == 404
    assert client.get("/api/home-agent/personal-skills").json()["skills"] == []
    assert client.post("/api/home-agent/orchestrate", json={"prompt": "stolen", "skill_id": skill_id}).status_code == 404
    assert client.get(f"/api/home-agent/personal-skills/draft/{cid}/{message_id}").status_code == 400
    common = client.post("/api/home-agent/orchestrate", json={"prompt": "summary", "context": {
        "personalization": {"response_style": "concise", "preferred_format": "text"}}})
    assert common.status_code == 200
    assert common.json()["reply"] == first.json()["reply"] == "done"
    assert "personalization" not in seen[-1]


def test_shared_skill_is_explicit_and_editable(client, monkeypatch):
    cid = str(uuid4())
    with chat_conversations.turn("alice", cid) as state:
        chat_conversations.append(state, "user", "summary")
        chat_conversations.append(state, "assistant", "secret result", response={"tool": {"feature": "dashboard", "table": {"rows": [{"product": "PROD1"}]}}})
        message_id = state["messages"][-1]["id"]
    base = {"conversation_id": cid, "message_id": message_id, "title": "범용 요약",
            "procedure": "사용자가 요청한 대상을 확인하고 대시보드 요약을 설명한다.", "shared": False}
    saved = client.post("/api/home-agent/personal-skills", json=base)
    skill_id = saved.json()["skill"]["id"]
    assert not saved.json()["skill"]["shared"]
    assert client.put(f"/api/home-agent/personal-skills/{skill_id}", json={**base, "shared": True,
        "procedure": "사용자가 지정한 범위를 확인하고 대시보드 요약을 설명한다."}).status_code == 200
    monkeypatch.setattr(data_chat.data_chat, "available_product_names", lambda: ["PRODA"])
    assert client.put(f"/api/home-agent/personal-skills/{skill_id}", json={**base, "shared": True,
        "procedure": "PRODA 제품을 조회하고 결과를 설명한다."}).status_code == 400
    assert client.put(f"/api/home-agent/personal-skills/{skill_id}", json={**base, "shared": True,
        "procedure": "30days 기간과 S0 조건을 확인하고 결과를 설명한다."}).status_code == 200
    assert client.post("/api/home-agent/personal-skills", json={**base, "procedure": "PROD1 제품을 조회하고 결과를 설명한다."}).status_code == 400
    client.app.dependency_overrides[data_chat.require_flowi_user] = lambda: {"username": "bob", "role": "admin"}
    visible = client.get("/api/home-agent/personal-skills").json()["skills"]
    assert len(visible) == 1 and visible[0]["shared"]
    assert "secret result" not in str(visible) and "PROD1" not in str(visible)
    assert client.put(f"/api/home-agent/personal-skills/{skill_id}", json={**base, "shared": True}).status_code == 404
    assert client.delete(f"/api/home-agent/personal-skills/{skill_id}").status_code == 404
    assert client.post("/api/home-agent/personal-skills", json={**base, "title": "SELECT * FROM lots"}).status_code == 400


def test_skill_source_must_be_successful_and_owned(client):
    cid = str(uuid4())
    with chat_conversations.turn("alice", cid) as state:
        chat_conversations.append(state, "assistant", "failed", error=True, response={"tool": {"feature": "dashboard"}})
        bad_id = state["messages"][-1]["id"]
    payload = {"conversation_id": cid, "message_id": bad_id, "title": "bad", "procedure": "사용자가 지정한 범위를 확인하고 결과를 설명한다."}
    assert client.post("/api/home-agent/personal-skills", json=payload).status_code == 400


def test_skill_is_selected_automatically_only_for_matching_request(client):
    skill = flowi_personalization.save_curated_skill(
        owner="system",
        title="SplitTable 계획 검토",
        procedure="질문의 제품과 wafer 범위를 확인하고 SplitTable 계획을 검토한다.",
        feature="splittable",
        shared=True,
        auto=True,
    )
    selected = flowi_personalization.resolve_skill_for_prompt("alice", "이 제품 스플릿 계획을 확인해줘")
    assert selected["id"] == skill["id"]
    assert selected["selection"] == "automatic"
    assert flowi_personalization.resolve_skill_for_prompt("alice", "오늘 알람만 보여줘") is None
