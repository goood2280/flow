"""Home scenarios through the router actually loaded in the deployment bundle."""
import logging
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import data_chat, flowi_routing, flowi_turn, llm_adapter, lot_progress_cache, product_semantics
from core.paths import PATHS
from routers import data_chat as api, splittable
from app_v2.runtime.router_loader import include_router_modules


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(PATHS, "data_root", tmp_path)
    monkeypatch.setattr(flowi_routing, "PATHS", SimpleNamespace(db_root=tmp_path))
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)
    monkeypatch.setattr(product_semantics, "resolve_terms", lambda *a, **k: [])
    monkeypatch.setattr(data_chat, "available_product_names", lambda: ["REAL_ALPHA"])
    monkeypatch.setattr(data_chat, "split_table_product", lambda p: "ML_TABLE_REAL_ALPHA" if p == "REAL_ALPHA" else "")
    monkeypatch.setattr(splittable, "list_products", lambda: {"products": [{"name": "ML_TABLE_REAL_ALPHA"}]})
    monkeypatch.setattr(splittable, "list_customs", lambda: {"customs": []})
    monkeypatch.setattr(lot_progress_cache, "lookup_lot_progress", lambda **k: [])
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.require_flowi_user] = lambda: {"username": "alice", "role": "admin"}
    return TestClient(app)


def test_loader_selects_active_home_route():
    app = FastAPI()
    loaded, failed = include_router_modules(app, Path(__file__).resolve().parents[1] / "backend" / "routers",
                                           logging.getLogger(__name__), only=["data_chat", "home_agent"])
    assert not failed
    assert loaded == ["data_chat"]
    routes = [r for r in app.routes if r.path == "/api/home-agent/orchestrate"]
    assert len(routes) == 1
    assert routes[0].endpoint.__module__ == "routers.data_chat"


def test_api_product_lot_chain_evidence_and_restore(client, monkeypatch):
    calls = []
    monkeypatch.setattr(splittable, "view_split", lambda **kw: calls.append(kw) or {
        "headers": ["AZAAA|1"], "wafer_keys": ["1"],
        "rows": [{"_param": "KNOB_A", "_cells": {"0": {"actual": "A"}}}],
    })
    cid = str(uuid4())
    def send(prompt):
        response = client.post("/api/home-agent/orchestrate", json={"prompt": prompt, "conversation_id": cid})
        assert response.status_code == 200, response.text
        return response.json()
    first = send("스플릿테이블 보여줘")
    assert first["routing_trace"]["status"] == "needs_input"
    assert first["evidence"]["steps"][0]["status"] == "needs_input"
    assert not calls
    second = send("REAL_ALPHA")
    assert second["tool"]["missing"] == ["lot"]
    final = send("AZAAA.1")
    assert calls[0]["fab_lot_id"] == "AZAAA.1"
    assert final["tool"]["split_view"]["headers"] == ["AZAAA|1"]
    evidence = final["evidence"]["steps"][0]
    assert evidence["status"] == "completed"
    assert evidence["targets"]["product"] == "REAL_ALPHA"
    assert evidence["sources"] == final["tool"]["sources"]
    assert evidence["query"] == {}  # illustrative view_split is not executed SQL
    assert final["success_prompt"] == "스플릿테이블 보여줘"
    restored = client.get(f"/api/home-agent/conversations/{cid}").json()
    assert len(restored["messages"]) == 6
    assert restored["messages"][-1]["response"]["evidence"] == final["evidence"]


def test_batch_stops_for_confirmation_and_exposes_observed_steps(client):
    result = client.post("/api/home-agent/orchestrate", json={
        "prompt": "스플릿테이블 보여줘\nREAL_ALPHA 랏관리 보여줘"}).json()
    assert [q["status"] for q in result["questions"]] == ["needs_input", "deferred"]
    assert len(result["evidence"]["steps"]) == 1
    assert "success_prompt" not in result


def test_evidence_never_promotes_illustrative_sql():
    result = {"ok": True, "tool": {"feature": "chart", "sources": ["actual.parquet"],
              "executed_sql": "SELECT count(*) FROM source",
              "execution_trace": {"illustrative_query": "invented", "sources": ["invented"]}}}
    step = flowi_turn._evidence(result)["steps"][0]
    assert step["query"]["sql"] == "SELECT count(*) FROM source"
    assert step["sources"] == ["actual.parquet"]


def test_needs_input_without_missing_is_not_success():
    assert flowi_turn._status({"ok": True, "tool": {"needs_input": True}}) == "needs_input"
