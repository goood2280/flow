import os
import tempfile
import pytest
from types import SimpleNamespace
from fastapi.testclient import TestClient

from app import app
from core import chat_prompts
from core.auth import issue_token


def test_chat_prompts_crud(monkeypatch, tmp_path):
    """Test sample prompts lifecycle: seed loading, record success, pin/unpin, delete."""
    temp_json = tmp_path / "chat_sample_prompts.json"
    monkeypatch.setattr(chat_prompts, "PROMPTS_FILE", temp_json)

    # 1. Initial load should yield seed pinned prompts
    data = chat_prompts.get_sample_prompts()
    assert len(data.get("pinned", [])) >= 5
    assert any("위치" in p["text"] for p in data["pinned"])
    assert len(data.get("successful", [])) == 0

    # 2. Record success for an ad-hoc query
    q = "PRODA A1001 현위치 어디야"
    chat_prompts.record_success(q, category="랏위치")
    data2 = chat_prompts.get_sample_prompts()
    assert any(s["text"] == q for s in data2["successful"])
    assert any(s["count"] == 1 for s in data2["successful"] if s["text"] == q)

    # Record second time -> count incremented
    chat_prompts.record_success(q, category="랏위치")
    data3 = chat_prompts.get_sample_prompts()
    item = next(s for s in data3["successful"] if s["text"] == q)
    assert item["count"] == 2

    # 3. Pin prompt from admin
    promoted = chat_prompts.pin_prompt(q, category="고정추천", pinned=True, user="admin")
    assert len(promoted.get("pinned", [])) > 0
    data4 = chat_prompts.get_sample_prompts()
    assert any(p["text"] == q for p in data4["pinned"])

    # 4. Unpin prompt
    unpinned = chat_prompts.pin_prompt(q, pinned=False)
    data5 = chat_prompts.get_sample_prompts()
    assert not any(p["text"] == q for p in data5["pinned"])

    # 5. Delete prompt
    deleted = chat_prompts.delete_prompt(text=q)
    assert deleted["ok"] is True
    data6 = chat_prompts.get_sample_prompts()
    assert not any(s["text"] == q for s in data6["successful"])


def test_chat_prompts_api_endpoints(monkeypatch, tmp_path):
    """Test /api/home-agent/sample-prompts HTTP endpoints via TestClient."""
    temp_json = tmp_path / "chat_sample_prompts.json"
    monkeypatch.setattr(chat_prompts, "PROMPTS_FILE", temp_json)

    token, _ = issue_token("admin_test", "admin")
    client = TestClient(app, headers={"x-session-token": token})

    # 1. GET sample-prompts
    resp = client.get("/api/home-agent/sample-prompts")
    assert resp.status_code == 200
    res_json = resp.json()
    assert "pinned" in res_json
    assert "successful" in res_json

    # 2. POST record-success
    rec_resp = client.post("/api/home-agent/sample-prompts/record-success", json={"text": "신규 실제 테스트 질의", "category": "테스트"})
    assert rec_resp.status_code == 200
    assert rec_resp.json().get("ok") is True

    # Check that it appears in recent_success
    resp2 = client.get("/api/home-agent/sample-prompts")
    assert any(s["text"] == "신규 실제 테스트 질의" for s in resp2.json().get("recent_success", []))

    # 3. POST pin
    pin_resp = client.post("/api/home-agent/sample-prompts/pin", json={"text": "신규 실제 테스트 질의", "category": "테스트", "pinned": True})
    assert pin_resp.status_code == 200
    resp3 = client.get("/api/home-agent/sample-prompts")
    assert any(p["text"] == "신규 실제 테스트 질의" for p in resp3.json().get("all_pinned", []))

    # 4. DELETE sample-prompts
    del_resp = client.delete("/api/home-agent/sample-prompts?text=" + "신규 실제 테스트 질의")
    assert del_resp.status_code == 200
    resp4 = client.get("/api/home-agent/sample-prompts")
    assert not any(s["text"] == "신규 실제 테스트 질의" for s in resp4.json().get("successful", []))
    assert not any(p["text"] == "신규 실제 테스트 질의" for p in resp4.json().get("pinned", []))
