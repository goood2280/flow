import hashlib
import json
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from core import ai_semantic, llm_adapter, home_model_status
from routers import data_chat


def test_permissions():
    app = FastAPI()
    app.include_router(data_chat.router)
    client = TestClient(app)
    for method, path in [("get", "/status"), ("post", "/probe"), ("post", "/orchestrate")]:
        response = getattr(client, method)("/api/home-agent" + path, **({"json": {"prompt": "test"}} if method == "post" else {}))
        assert response.status_code == 401


def test_semantic_hot_reload_and_integrity(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_semantic, "PATHS", SimpleNamespace(db_root=tmp_path))
    assert ai_semantic.snapshot()["status"] == "missing"
    root = tmp_path / "AI"
    file = root / "releases/v1/terms.md"
    file.parent.mkdir(parents=True)
    file.write_text("WIP means current position", encoding="utf-8")
    doc = {"path": "releases/v1/terms.md", "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
    manifest = {"schema_version": 1, "release": {"version": "v1", "approved": True, "documents": [doc]}}
    def save():
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    save()
    assert ai_semantic.prompt_context("WIP")["documents"]
    file.write_text("changed", encoding="utf-8")
    assert ai_semantic.snapshot()["status"] == "invalid"
    doc["sha256"] = hashlib.sha256(file.read_bytes()).hexdigest()
    save()
    assert ai_semantic.prompt_context("changed")["documents"][0]["text"] == "changed"
    doc["path"] = "releases/v1/../../outside.md"
    save()
    assert not ai_semantic.prompt_context("WIP")["documents"]


@pytest.fixture
def configured(monkeypatch):
    cfg = {**llm_adapter._DEFAULT, "enabled": True, "api_url": "https://internal.invalid/v1", "model": "test", "auth_mode": "none"}
    monkeypatch.setattr(llm_adapter, "_raw_config", lambda: dict(cfg))
    llm_adapter.reset_llm_health()
    yield cfg
    llm_adapter.reset_llm_health()


@pytest.mark.parametrize("body,ok", [(b"<html>login</html>", False), (b'{"choices":[]}', False), (b'{"choices":[{"message":{"content":"OK"}}]}', True)])
def test_actual_model_response(configured, monkeypatch, body, ok):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, limit): return body
    monkeypatch.setattr(llm_adapter.urllib.request, "urlopen", lambda *a, **k: Response())
    with llm_adapter.request_execution_scope({"role": "admin"}, "/api/home-agent/probe"):
        result = llm_adapter.complete("OK", probe=True)
    assert result["ok"] is ok
    assert home_model_status.snapshot()["status"] == ("connected" if ok else "disconnected")


def test_config_change_invalidates_breaker(configured):
    llm_adapter._mark_llm_unhealthy("failed")
    assert not llm_adapter.should_attempt_llm()
    configured["model"] = "new"
    assert llm_adapter.should_attempt_llm()
    assert home_model_status.snapshot()["status"] == "unknown"


def test_status_does_not_call_model(configured, monkeypatch):
    monkeypatch.setattr(llm_adapter, "complete", lambda *a, **k: pytest.fail("status called model"))
    monkeypatch.setattr(ai_semantic, "snapshot", lambda: {"status": "missing"})
    assert data_chat.status({"role": "admin"})["model"]["status"] == "unknown"


def test_non_admin_forbidden(monkeypatch):
    from core import auth
    monkeypatch.setattr(auth, "current_user", lambda request: {"role": "user"})
    app = FastAPI()
    app.include_router(data_chat.router)
    client = TestClient(app)
    for method, path in [("get", "/status"), ("post", "/probe"), ("post", "/orchestrate")]:
        response = getattr(client, method)("/api/home-agent" + path, **({"json": {"prompt": "test"}} if method == "post" else {}))
        assert response.status_code == 403


def test_planner_receives_reference_only_context(monkeypatch):
    from core import data_chat as service, data_chat_features
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(ai_semantic, "prompt_context", lambda text: {"version": "v3", "reference_only": True, "documents": [{"text": "term"}]})
    captured = {}
    def complete(prompt, **kwargs):
        captured.update(json.loads(prompt))
        return {"ok": True, "obj": {"action": "clarify", "params": {}}}
    monkeypatch.setattr(llm_adapter, "complete_json", complete)
    service._feature_plan("unrecognized terminology", {}, [], data_chat_features)
    assert captured["semantic_reference"]["version"] == "v3"
    assert captured["semantic_reference"]["reference_only"] is True



def test_json_second_brain_alias_and_binding(tmp_path, monkeypatch):
    from core import data_chat as service
    monkeypatch.setattr(ai_semantic, "PATHS", SimpleNamespace(db_root=tmp_path))
    root = tmp_path / "AI"
    file = root / "releases/v2/knowledge.json"
    file.parent.mkdir(parents=True)
    common = {"status": "active", "created_at": "2026-09-10T00:00:00Z", "updated_at": "2026-09-10T00:00:00Z", "source_ids": ["s1"]}
    records = [{**common, "id": "p1", "kind": "product", "canonical_name": "EXAMPLE_X0", "aliases": ["Xzero"]},
               {**common, "id": "b1", "kind": "measurement_binding", "product_id": "p1", "concept": "gate CD", "step_id": "SX100", "item": "CD_A"}]
    def save():
        file.write_text(json.dumps(records), encoding="utf-8")
        (root / "manifest.json").write_text(json.dumps({"schema_version": 2, "release": {"version": "v2", "approved": True, "documents": [{"path": "releases/v2/knowledge.json", "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}]}}), encoding="utf-8")
    save()
    assert service.product_candidates("Xzero 위치", ["EXAMPLE_X0"]) == ["EXAMPLE_X0"]
    assert service.product_candidates("Xzero 위치", ["UNKNOWN"]) == []
    context = ai_semantic.prompt_context("Xzero gate CD")
    assert any(json.loads(d["text"])["id"] == "b1" for d in context["documents"])
    records[1]["product_id"] = "missing"
    save()
    assert ai_semantic.snapshot()["status"] == "invalid"
    records[1]["product_id"] = "p1"
    records.append({**records[0], "id": "p2", "canonical_name": "EXAMPLE_Y0"})
    save()
    assert service.product_candidates("Xzero", ["EXAMPLE_X0", "EXAMPLE_Y0"]) == ["EXAMPLE_X0", "EXAMPLE_Y0"]
    records[2]["aliases"] = ["Yzero"]
    records.append({**records[1], "id": "b2", "product_id": "p2"})
    save()
    scoped = ai_semantic.prompt_context("Xzero gate CD")
    assert not any(json.loads(d["text"])["id"] == "b2" for d in scoped["documents"])
    records[2]["id"] = "p1"
    save()
    assert ai_semantic.snapshot()["status"] == "invalid"
