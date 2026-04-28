from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from backend.routers import informs  # noqa: E402


def test_product_add_duplicate_returns_409(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"products": ["PRODA", " proda "]}), encoding="utf-8")

    monkeypatch.setattr(informs, "CONFIG_FILE", cfg_file)
    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "admin", "username": "tester"})

    app = FastAPI()
    app.include_router(informs.router)
    client = TestClient(app)

    resp = client.post("/api/informs/products/add", json={"product": "ML_TABLE_PRODA"})

    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"]["code"] == "duplicate_product"
    assert body["detail"]["existing_product"] == "PRODA"


def test_product_add_collection_post_compat(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"products": []}), encoding="utf-8")

    monkeypatch.setattr(informs, "CONFIG_FILE", cfg_file)
    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "admin", "username": "tester"})

    app = FastAPI()
    app.include_router(informs.router)
    client = TestClient(app)

    resp = client.post("/api/informs/products", json={"product": "ML_TABLE_PRODA"})

    assert resp.status_code == 200
    assert resp.json()["products"] == ["PRODA"]
