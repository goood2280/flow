from types import SimpleNamespace

import pytest

from core import data_chat, data_chat_teg, llm_adapter, product_semantics
from core import lot_progress_cache
from routers import splittable


def _offline(monkeypatch, products):
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)
    monkeypatch.setattr(product_semantics, "resolve_terms", lambda *args, **kwargs: [])
    monkeypatch.setattr(data_chat, "available_product_names", lambda: list(products))
    monkeypatch.setattr(
        splittable,
        "list_products",
        lambda: {"products": [{"name": product} for product in products]},
    )


@pytest.mark.parametrize("custom_name", ["PC CUSTOM", "PC"])
def test_example_split_table_resolves_registered_custom_name(monkeypatch, custom_name):
    _offline(monkeypatch, ["PRODA"])
    monkeypatch.setattr(splittable, "list_customs", lambda: {"customs": [{"name": custom_name}]})
    monkeypatch.setattr(lot_progress_cache, "lookup_lot_progress", lambda **kwargs: [])
    captured = {}

    def view_split(**kwargs):
        captured.update(kwargs)
        return {
            "product": "PRODA",
            "root_lot_id": "AZBVC",
            "wafer_keys": ["1"],
            "rows": [{"_param": "KNOB_5.0 PC", "_cells": {"0": {"actual": "A"}}}],
        }

    monkeypatch.setattr(splittable, "view_split", view_split)

    result = data_chat.execute(
        "PRODA AZBVC PC CUSTOM 세트 스플릿 테이블 보여줘", {}, None
    )

    assert result["ok"] is True, result
    assert result["tool"]["feature"] == "splittable"
    assert result["context"]["product"] == "PRODA"
    assert result["context"]["root_lot_id"] == "AZBVC"
    assert captured["custom_name"] == custom_name
    assert captured["root_lot_id"] == "AZBVC"


def test_example_teg_source_alias_and_location_followups(monkeypatch):
    _offline(monkeypatch, ["PROBB"])
    payload = {
        "vehicle": "VH_PROBB",
        "geometry": {"fit": "radius"},
        "shots": [],
        "tegs": [
            {
                "teg": "H_HOL01",
                "teg_src": "HOL01",
                "ebeam_x": 1.0,
                "ebeam_y": 2.0,
                "teg_w": 0.2,
                "teg_h": 0.3,
                "flat_zone": "h",
            }
        ],
    }
    monkeypatch.setattr(data_chat_teg.teg_map, "product_catalog", lambda: [{"vehicle": "VH_PROBB"}])
    monkeypatch.setattr(data_chat_teg.teg_map, "map_payload", lambda product: payload)

    first = data_chat.execute("PROBB HOL01 TEG 보여줘", {}, None)
    assert first["ok"] is True, first
    assert first["tool"]["action"] == "teg.locations"
    assert first["context"]["product"] == "PROBB"
    assert first["context"]["teg_product"] == "VH_PROBB"
    assert first["context"]["teg_names"] == ["H_HOL01"]

    for prompt in ("위치 보여줘", "어디에 있어"):
        followup = data_chat.execute(prompt, first["context"], None)
        assert followup["ok"] is True, followup
        assert followup["tool"]["action"] == "teg.locations"
        assert followup["context"]["product"] == "PROBB"
        assert followup["context"]["teg_product"] == "VH_PROBB"
        assert followup["context"]["teg_names"] == ["H_HOL01"]


def test_example_lot_location_followup_reuses_product_and_root_lot(monkeypatch):
    _offline(monkeypatch, ["PRODC"])
    calls = []

    def summaries(lots, *, product, match_root):
        calls.append((list(lots), product, match_root))
        return {
            "ABVCC": {
                "product": "PRODC",
                "rows": [{"lot_id": "ABVCC", "root_lot_id": "ABVCC", "step_id": "STEP10"}],
            }
        }

    monkeypatch.setattr(lot_progress_cache, "canonical_lot_progress_summaries", summaries)

    first = data_chat.execute("PRODC ABVCC 지금 어디에 있어?", {}, None)
    assert first["ok"] is True, first
    assert first["tool"]["feature"] == "location"
    assert first["context"]["product"] == "PRODC"
    assert first["context"]["root_lot_id"] == "ABVCC"

    second = data_chat.execute("위치가 어디야?", first["context"], None)
    assert second["ok"] is True, second
    assert second["tool"]["feature"] == "location"
    assert second["context"]["product"] == "PRODC"
    assert second["context"]["root_lot_id"] == "ABVCC"
    assert second["tool"]["table"]["rows"][0]["step_id"] == "STEP10"
    assert calls == [(["ABVCC"], "PRODC", True), (["ABVCC"], "PRODC", True)]
