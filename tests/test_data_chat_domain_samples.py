import copy
import pytest
from types import SimpleNamespace

from core import data_chat, data_chat_split, data_chat_teg
from routers import splittable


def test_resolve_lot_scope_domain_rules():
    """Verify left 5 root lot ID derivation for various In-Fab lot formats."""
    assert data_chat.resolve_lot_scope("A1001") == ("A1001", "A1001")
    assert data_chat.resolve_lot_scope("A1005.1") == ("A1005.1", "A1005")
    assert data_chat.resolve_lot_scope("AZXXX") == ("AZXXX", "AZXXX")
    assert data_chat.resolve_lot_scope("A4XXX") == ("A4XXX", "A4XXX")
    assert data_chat.resolve_lot_scope("AZXXX.1") == ("AZXXX.1", "AZXXX")
    assert data_chat.resolve_lot_scope("AZXXXA.1") == ("AZXXXA.1", "AZXXX")
    assert data_chat.resolve_lot_scope("A4123B.2") == ("A4123B.2", "A4123")


def test_product_candidates_aliases():
    """Verify prodA, prodB, pro1, product1, prod0 EVT aliases."""
    products = ["ML_TABLE_PRODA", "ML_TABLE_PRODB", "PRODUCT_EVT1", "PRODUCT_EVT0"]
    assert data_chat.product_candidates("prodA A1005.1", products) == ["ML_TABLE_PRODA"]
    assert data_chat.product_candidates("proA A1005.1", products) == ["ML_TABLE_PRODA"]
    assert data_chat.product_candidates("prodB GATE TEG", products) == ["ML_TABLE_PRODB"]
    assert data_chat.product_candidates("pro1 위치 어디야", products) == ["PRODUCT_EVT1"]
    assert data_chat.product_candidates("product1 어디있어", products) == ["PRODUCT_EVT1"]
    assert data_chat.product_candidates("prod0 진도 확인", products) == ["PRODUCT_EVT0"]


def test_sample_1_lot_location(monkeypatch):
    """[샘플 1] 발화: PRODA A1001 지금 어디에 있어? -> location action, A1001"""
    request = SimpleNamespace(state=SimpleNamespace(user={"username": "qa_admin", "role": "admin"}), headers={})
    res = data_chat.execute("PRODA A1001 지금 어디에 있어?", {}, request)
    assert res["ok"] is True
    assert res["tool"].get("feature") == "location"
    assert res["context"].get("root_lot_id") == "A1001"
    assert res["context"].get("product") in ("PRODA", "ML_TABLE_PRODA")


def test_sample_2_split_assignment(tmp_path, monkeypatch):
    """[샘플 2] 발화: prodA A1005.1 5.0 PC 스플릿 wafer 1~6 ABC 넣고 나머지는 ABB로 깔아줘"""
    monkeypatch.setattr(data_chat_split.PATHS, "data_root", tmp_path)
    monkeypatch.setattr(splittable, "PLAN_DIR", tmp_path / "plans")
    monkeypatch.setattr(splittable, "_plan_product_name", lambda product: product)
    monkeypatch.setattr(splittable, "list_products", lambda: {"products": [{"name": "PRODA"}, {"name": "PRODB"}]})
    monkeypatch.setattr(splittable, "_knob_current_s0_for_product", lambda *a: {"KNOB_5.0 PC": {"ppid": "ABC"}})
    monkeypatch.setattr(splittable, "_archive_plan_history", lambda *a: None)
    monkeypatch.setattr(splittable, "_invalidate_plan_risk_cache", lambda *a: None)
    monkeypatch.setattr(splittable, "_audit_user", lambda *a, **kw: None)
    monkeypatch.setattr(splittable, "_append_splittable_plan_knowledge", lambda **kw: None)
    monkeypatch.setattr(splittable, "_actual_value_for_plan_cell", lambda *a: None)
    monkeypatch.setattr(splittable, "_plan_actual_mismatch", lambda *a: False)
    from core import notify
    monkeypatch.setattr(notify, "emit_event", lambda *a, **kw: False)

    view = {
        "product": "PRODA",
        "root_lot_id": "A1005",
        "wafer_keys": [str(i) for i in range(1, 9)],
        "headers": [str(i) for i in range(1, 9)],
        "s0_by_knob": {"KNOB_5.0 PC": {"ppid": "ABC"}},
        "s0_edit_by_knob": {"KNOB_5.0 PC": {"ppid": "ABC"}},
        "rows": [{
            "_param": "KNOB_5.0 PC",
            "_cells": {str(i - 1): {"key": f"A1005|{i}|KNOB_5.0 PC", "actual": "ABC", "plan": None} for i in range(1, 9)}
        }]
    }
    monkeypatch.setattr(splittable, "view_split", lambda **kw: copy.deepcopy(view))
    request = SimpleNamespace(state=SimpleNamespace(user={"username": "qa_admin", "role": "admin"}), headers={})

    query = "prodA A1005.1 5.0 PC 스플릿 wafer 1~6 ABC 넣고 나머지는 ABB로 깔아줘"
    preview = data_chat.execute(query, {}, request)
    assert preview["ok"] is True, preview
    assert preview["tool"].get("feature") == "splittable.plan"
    assert preview["tool"].get("approval", {}).get("status") == "pending"
    assert preview["context"].get("root_lot_id") == "A1005"
    assert preview["context"].get("product") == "PRODA"

    rows = preview["tool"]["table"]["rows"]
    assert len(rows) == 8
    # Wafers 1~6 should have change to ABC, 7~8 to ABB
    for r in rows[:6]:
        assert r["변경 후 계획"] == "ABC"
    for r in rows[6:]:
        assert r["변경 후 계획"] == "ABB"


def test_sample_3_teg_location(monkeypatch):
    """[샘플 3] 발화: prodB GATE TEG 어디있는지 보여줘"""
    request = SimpleNamespace(state=SimpleNamespace(user={"username": "qa_admin", "role": "admin"}), headers={})
    res = data_chat.execute("prodB GATE TEG 어디있는지 보여줘", {}, request)
    assert res["ok"] is True, res
    assert res["tool"].get("action") == "teg.locations"
    assert res["context"].get("product") == "PRODB"
    assert res["context"].get("teg_product") == "VH_PRODB"
    assert "TEG_GATE" in res["context"].get("teg_names", [])


def test_sample_4_splittable_view(monkeypatch):
    """[샘플 4] 발화: prodA A1005.1 스플릿테이블 보여줘"""
    request = SimpleNamespace(state=SimpleNamespace(user={"username": "qa_admin", "role": "admin"}), headers={})
    view = {
        "product": "PRODA",
        "root_lot_id": "A1005",
        "wafer_keys": ["1", "2"],
        "rows": [{"_param": "KNOB_M1", "_cells": {"0": {"actual": "A"}}}]
    }
    monkeypatch.setattr(splittable, "view_split", lambda **kw: copy.deepcopy(view))
    res = data_chat.execute("prodA A1005.1 스플릿테이블 보여줘", {}, request)
    assert res["ok"] is True, res
    assert res["tool"].get("feature") == "splittable"
    assert res["context"].get("root_lot_id") == "A1005"


def test_sample_4_extension_pc_custom_set(monkeypatch):
    """[샘플 4 확장] 발화: prodA A1005.1 PC CUSTOM SET 스플릿테이블 보여줘 or PC 커스텀 세트로 보여줘"""
    request = SimpleNamespace(state=SimpleNamespace(user={"username": "qa_admin", "role": "admin"}), headers={})
    captured = {}
    def mock_view(**kw):
        captured.update(kw)
        return {"product": "PRODA", "root_lot_id": "A1005", "wafer_keys": ["1"], "rows": [{"_param": "KNOB_5.0 PC", "_cells": {}}]}
    monkeypatch.setattr(splittable, "view_split", mock_view)

    # 1. Custom set keyword in prompt
    res1 = data_chat.execute("prodA A1005.1 PC CUSTOM SET 스플릿테이블 보여줘", {}, request)
    assert res1["ok"] is True, res1
    assert "PC" in res1["reply"]
    assert len(res1["tool"]["table"]["rows"]) == 1

    # 2. PC 커스텀 세트
    res2 = data_chat.execute("prodA A1005.1 PC 커스텀 세트로 보여줘", {}, request)
    assert res2["ok"] is True, res2
    assert "PC" in res2["reply"]
    assert len(res2["tool"]["table"]["rows"]) == 1


def test_domain_translation_guide(monkeypatch):
    """Verify that domain translation guide is prepended to replies and help commands work."""
    request = SimpleNamespace(state=SimpleNamespace(user={"username": "qa_admin", "role": "admin"}), headers={})
    
    # Help guide query
    guide_res = data_chat.execute("가이드", {}, request)
    assert guide_res["ok"] is True
    assert "[도메인 해석 가이드]" in guide_res["reply"]
    assert "Left 5" in guide_res["reply"]

    # Translation guide attached to location query
    loc_res = data_chat.execute("PRODA A1001 지금 어디에 있어?", {}, request)
    assert "[도메인 해석 가이드]" in loc_res["reply"]
    assert "A1001" in loc_res["reply"]
    assert "랏 현재 위치 및 공정 진도 확인" in loc_res["reply"]
