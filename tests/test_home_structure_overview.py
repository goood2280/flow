from core import data_chat, product_semantics, structure_model


def _scene_doc():
    doc = structure_model._empty()
    doc["products"] = {
        name: {"profiles": {"logic/6T": {"parameters": {"ns1_width_nm": width},
            "anchors": {}, "dimension_anchors": {}, "shape_profiles": {}}}}
        for name, width in (("DEMO_ALPHA", 30), ("DEMO_BETA", 50))
    }
    return doc


def test_generic_product_structure_uses_confirmed_product_model(monkeypatch):
    doc = _scene_doc()
    monkeypatch.setattr(data_chat, "available_product_names", lambda: list(doc["products"]))
    monkeypatch.setattr(structure_model, "read", lambda: doc)
    monkeypatch.setattr(product_semantics, "resolve_terms", lambda *args: [])
    monkeypatch.setattr(product_semantics, "load_inline_matching_rows", lambda *args: [])
    from core import (data_chat_split_query, data_chat_eta, data_chat_inline,
                      data_chat_inline_chart, data_chat_wafer_map, data_chat_ml_chart)
    for module in (data_chat_split_query, data_chat_eta, data_chat_inline,
                   data_chat_inline_chart, data_chat_wafer_map, data_chat_ml_chart):
        monkeypatch.setattr(module, "dispatch", lambda *args: None)
    missing = data_chat.execute("제품 구조 보여줘", {}, None)
    assert missing["tool"]["missing"] == ["product"]
    alpha = data_chat.execute("DEMO_ALPHA 제품 구조 보여줘", {}, None)
    beta = data_chat.execute("DEMO_BETA 제품 구조 보여줘", {}, None)
    assert alpha["tool"]["feature"] == beta["tool"]["feature"] == "product.knowledge"
    assert alpha["tool"]["semantic_reference"]["scope"] == "DEMO_ALPHA"
    assert beta["tool"]["semantic_reference"]["scope"] == "DEMO_BETA"
    assert alpha["tool"]["table"]["rows"][0]["폭 (nm)"] == 30
    assert beta["tool"]["table"]["rows"][0]["폭 (nm)"] == 50
    assert "실측 결과는 아닙니다" in alpha["reply"]
    assert "DEMO_BETA" not in str(alpha)


def test_generic_product_structure_requires_variant_when_ambiguous(monkeypatch):
    doc = _scene_doc()
    doc["products"]["DEMO_ALPHA"]["profiles"]["sram/HD"] = {
        "parameters": {}, "anchors": {}, "dimension_anchors": {}, "shape_profiles": {}}
    monkeypatch.setattr(data_chat, "available_product_names", lambda: list(doc["products"]))
    monkeypatch.setattr(structure_model, "read", lambda: doc)
    monkeypatch.setattr(product_semantics, "resolve_terms", lambda *args: [])
    monkeypatch.setattr(product_semantics, "load_inline_matching_rows", lambda *args: [])
    from core import (data_chat_split_query, data_chat_eta, data_chat_inline,
                      data_chat_inline_chart, data_chat_wafer_map, data_chat_ml_chart)
    for module in (data_chat_split_query, data_chat_eta, data_chat_inline,
                   data_chat_inline_chart, data_chat_wafer_map, data_chat_ml_chart):
        monkeypatch.setattr(module, "dispatch", lambda *args: None)
    result = data_chat.execute("DEMO_ALPHA 제품 구조 보여줘", {}, None)
    assert result["tool"]["missing"] == ["structure_variant"]
    assert {row["변형"] for row in result["tool"]["table"]["rows"]} == {"logic/6T", "sram/HD"}


def test_inline_item_description_does_not_silently_change_size(monkeypatch):
    doc = _scene_doc()
    rows = {
        "DEMO_ALPHA": [{"module": "FEOL", "step_id": "A1", "item_id": "CD", "item_desc": "Gate CD 30 nm"}],
        "DEMO_BETA": [{"module": "FEOL", "step_id": "B1", "item_id": "CD", "item_desc": "Gate CD 50 nm"}],
    }
    monkeypatch.setattr(product_semantics, "load_inline_matching_rows", lambda product: rows.get(product, []))
    for name, step in (("DEMO_ALPHA", "A1"), ("DEMO_BETA", "B1")):
        doc["products"][name]["profiles"]["logic/6T"]["parameters"] = {}
        doc["products"][name]["profiles"]["logic/6T"]["anchors"] = {
            "gate": {"module": "FEOL", "step_id": step, "item_id": "CD"}}
    alpha = structure_model.build_scene(doc, "DEMO_ALPHA")
    beta = structure_model.build_scene(doc, "DEMO_BETA")
    assert alpha["anchors"]["gate"]["status"] == beta["anchors"]["gate"]["status"] == "matched"
    assert alpha["anchors"]["gate"]["step_id"] == "A1"
    assert beta["anchors"]["gate"]["step_id"] == "B1"
    assert alpha["nanosheet_dimensions_nm"]["sheets"][0]["width"] == beta["nanosheet_dimensions_nm"]["sheets"][0]["width"]
    doc["products"]["DEMO_ALPHA"]["profiles"]["logic/6T"]["parameters"]["ns1_width_nm"] = 30
    doc["products"]["DEMO_BETA"]["profiles"]["logic/6T"]["parameters"]["ns1_width_nm"] = 50
    alpha = structure_model.build_scene(doc, "DEMO_ALPHA")
    beta = structure_model.build_scene(doc, "DEMO_BETA")
    assert alpha["nanosheet_dimensions_nm"]["sheets"][0]["width"] == 30
    assert beta["nanosheet_dimensions_nm"]["sheets"][0]["width"] == 50
