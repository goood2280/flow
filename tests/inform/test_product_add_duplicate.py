from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from backend.routers import informs  # noqa: E402
from core import lot_progress_cache  # noqa: E402


def test_product_add_is_cache_managed_compat_noop(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"products": ["PRODA", " proda "]}), encoding="utf-8")

    monkeypatch.setattr(informs, "CONFIG_FILE", cfg_file)
    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "admin", "username": "tester"})
    monkeypatch.setattr(informs, "_lot_progress_cache_products", lambda: ["PRODA"])

    resp = informs.add_product(informs.ProductReq(product="ML_TABLE_PRODA"), object())

    assert resp["products"] == ["PRODA"]
    assert resp["source"] == "lot_progress_cache"
    assert json.loads(cfg_file.read_text(encoding="utf-8"))["products"] == ["PRODA", " proda "]


def test_product_add_collection_post_compat(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"products": []}), encoding="utf-8")

    monkeypatch.setattr(informs, "CONFIG_FILE", cfg_file)
    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "admin", "username": "tester"})
    monkeypatch.setattr(informs, "_lot_progress_cache_products", lambda: ["PRODA"])

    resp = informs.add_product_collection_compat(informs.ProductReq(product="ML_TABLE_PRODA"), object())

    assert resp["products"] == ["PRODA"]


def test_inform_config_product_candidates_include_latest_lot_cache(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"products": ["LEGACY_ONLY"]}), encoding="utf-8")
    fab_root = tmp_path / "Fab" / "1.RAWDATA_DB_FAB"
    (fab_root / "ML_TABLE_PRODA").mkdir(parents=True)
    (fab_root / "PRODB").mkdir(parents=True)
    data_root = tmp_path / "flow-data"

    class DummyPaths:
        def __init__(self):
            self.db_root = tmp_path / "Fab"
            self.base_root = tmp_path / "Fab"
            self.data_root = data_root
            self.cache_dir = data_root / "cache"
            self.db_cache_dir = tmp_path / "Fab" / "cache"

    monkeypatch.setattr(informs, "CONFIG_FILE", cfg_file)
    paths = DummyPaths()
    monkeypatch.setattr(informs, "PATHS", paths)
    monkeypatch.setattr(lot_progress_cache, "PATHS", paths)
    monkeypatch.setattr(lot_progress_cache, "_CACHE_STATE", None)
    lot_progress_cache.cache_file().write_text(json.dumps({
        "generated_at": "2026-05-17T10:00:00",
        "count": 3,
        "items": [
            {"product": "PRODA", "root_lot_id": "R1000", "wafer_id": "1", "lot_id": "F1000A.1"},
            {"product": "PRODC", "root_lot_id": "R2000", "wafer_id": "1", "lot_id": "F2000A.1"},
            {"product": "ML_TABLE_PRODD", "root_lot_id": "R3000", "wafer_id": "1", "lot_id": "F3000A.1"},
        ],
    }), encoding="utf-8")
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

    assert cfg["products"] == ["PRODA", "PRODC", "PRODD"]
    assert {row["product"] for row in products} == {"PRODA", "PRODC", "PRODD"}
    assert {row["product"] for row in sidebar["products"]} == {"PRODA", "PRODC", "PRODD"}
