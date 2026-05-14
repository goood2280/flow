from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import polars as pl
from fastapi import HTTPException

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

    with pytest.raises(HTTPException) as excinfo:
        informs.add_product(informs.ProductReq(product="ML_TABLE_PRODA"), object())

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["code"] == "duplicate_product"
    assert excinfo.value.detail["existing_product"] == "PRODA"


def test_product_add_collection_post_compat(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"products": []}), encoding="utf-8")

    monkeypatch.setattr(informs, "CONFIG_FILE", cfg_file)
    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "admin", "username": "tester"})

    resp = informs.add_product_collection_compat(informs.ProductReq(product="ML_TABLE_PRODA"), object())

    assert resp["products"] == ["PRODA"]


def test_inform_config_product_candidates_include_latest_lot_cache(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"products": ["LEGACY_ONLY"]}), encoding="utf-8")
    fab_root = tmp_path / "Fab" / "1.RAWDATA_DB_FAB"
    (fab_root / "ML_TABLE_PRODA").mkdir(parents=True)
    (fab_root / "PRODB").mkdir(parents=True)
    cache_root = tmp_path / "Fab" / "cache"
    cache_root.mkdir(parents=True)
    pl.DataFrame({
        "product": ["PRODA", "PRODC", "ML_TABLE_PRODD"],
        "root_lot_id": ["R1000", "R2000", "R3000"],
        "wafer_id": ["1", "1", "1"],
        "lot_id": ["F1000A.1", "F2000A.1", "F3000A.1"],
    }).write_parquet(cache_root / "lot_progress_latest_lot_by_root_wafer.parquet")

    class DummyPaths:
        db_root = tmp_path / "Fab"
        db_cache_dir = cache_root

    monkeypatch.setattr(informs, "CONFIG_FILE", cfg_file)
    monkeypatch.setattr(informs, "PATHS", DummyPaths())
    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "admin", "username": "tester"})
    monkeypatch.setattr(informs, "_effective_modules", lambda _username, _role: {"__all__"})
    monkeypatch.setattr(informs, "_load_upgraded", lambda: [
        {"product": "PRODA", "created_at": "2026-05-01T00:00:00", "id": "a"},
        {"product": "LEGACY_ONLY", "created_at": "2026-05-02T00:00:00", "id": "b"},
    ])

    cfg = informs.get_config()
    products = informs.list_products(object())["products"]
    sidebar = informs._sidebar_payload(
        [{"product": "PRODA", "created_at": "2026-05-01T00:00:00"}, {"product": "LEGACY_ONLY", "created_at": "2026-05-02T00:00:00"}],
        {"username": "tester", "role": "admin"},
        {"__all__"},
    )

    assert cfg["products"] == ["PRODA", "PRODB", "PRODC", "PRODD"]
    assert [row["product"] for row in products] == ["PRODA", "PRODB"]
    assert {row["product"] for row in sidebar["products"]} == {"PRODA", "PRODB"}
