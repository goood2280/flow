from copy import deepcopy
from types import SimpleNamespace

import pytest

from core import chat_conversations, chat_prompts, data_chat, llm_adapter, product_semantics
from routers import data_chat as chat_router, splittable


def user(text):
    return {"role": "user", "content": text}


def assistant(response):
    return {"role": "assistant", "response": response}


DONE = {"ok": True, "tool": {"feature": "splittable", "table": {"rows": [{"항목": "KNOB_A"}]}}}
PRODUCT = {"ok": False, "tool": {"missing": ["product"]}}
LOT = {"tool": {"missing": ["lot"]}}


def test_clarification_chain_is_not_a_success_example():
    # Asking back for the product/lot is human-in-the-loop: neither the
    # incomplete question nor the answers are reusable example questions.
    messages = [user("스플릿테이블 보여줘"), assistant(PRODUCT), user("REAL_ALPHA"), assistant(LOT), user("AZAAA.1")]
    assert chat_prompts.completed_origin(messages, DONE, products=["REAL_ALPHA"]) is None
    direct = chat_prompts.completed_origin([user("REAL_ALPHA AZAAA.1 스플릿테이블 보여줘")], DONE)
    assert direct["prompt"] == "REAL_ALPHA AZAAA.1 스플릿테이블 보여줘"
    assert chat_prompts.completed_origin(messages, LOT) is None
    assert chat_prompts.completed_origin(messages, {"ok": False, "tool": {"feature": "splittable"}}) is None
    assert chat_prompts.completed_origin(messages[:-1] + [user("취소")], DONE) is None


def test_approval_preview_is_not_success_but_completed_chain_is():
    pending = {"tool": {"feature": "splittable.plan", "approval": {"status": "pending"}}}
    assert chat_prompts.completed_origin([user("스플릿 수정해줘")], pending) is None
    result = chat_prompts.completed_origin([user("스플릿 수정해줘"), assistant(pending), user("승인")], DONE)
    assert result["prompt"] == "스플릿 수정해줘"


@pytest.mark.parametrize("answer", ["취소하겠다", "반영하지마", "저장하지마", "취소 " + "a" * 32])
def test_all_supported_cancel_answers_are_not_success(answer):
    messages = [user("스플릿 수정해줘"), assistant({"tool": {"approval": {"status": "pending"}}}), user(answer)]
    assert chat_prompts.completed_origin(messages, DONE) is None


def test_unfinished_batch_is_not_a_completed_starting_question():
    pending = {**PRODUCT, "questions": [{"status": "needs_input"}, {"status": "deferred"}]}
    assert chat_prompts.completed_origin([user("조회 A 그리고 조회 B"), assistant(pending), user("REAL_ALPHA")], DONE) is None


@pytest.fixture
def private_store(monkeypatch, tmp_path):
    monkeypatch.setattr(chat_conversations.PATHS, "data_root", tmp_path)
    monkeypatch.setattr(chat_prompts, "PROMPTS_FILE", tmp_path / "sample.json")


def test_server_skips_clarification_chains_and_records_direct_questions(private_store, monkeypatch):
    outputs = iter([PRODUCT, LOT, DONE, DONE])
    monkeypatch.setattr(chat_router.flowi_turn, "execute", lambda *a, **k: deepcopy(next(outputs)))
    monkeypatch.setattr(chat_router.flowi_personalization, "resolve_skill_for_prompt", lambda *a: None)
    monkeypatch.setattr(chat_router.audit, "record", lambda *a, **k: None)
    request = SimpleNamespace()
    first = chat_router.orchestrate(chat_router.ChatRequest(prompt="스플릿테이블 보여줘"), request, _user={"username": "alice"})
    assert not chat_prompts.get_sample_prompts("alice")["successful"]
    second = chat_router.orchestrate(chat_router.ChatRequest(prompt="REAL_ALPHA", conversation_id=first["conversation_id"]), request, _user={"username": "alice"})
    assert not chat_prompts.get_sample_prompts("alice")["successful"]
    third = chat_router.orchestrate(chat_router.ChatRequest(prompt="AZAAA.1", conversation_id=second["conversation_id"]), request, _user={"username": "alice"})
    assert "success_prompt" not in third
    assert not chat_prompts.get_sample_prompts("alice")["successful"]
    direct = chat_router.orchestrate(chat_router.ChatRequest(prompt="REAL_ALPHA AZAAA.1 스플릿테이블 보여줘"), request, _user={"username": "alice"})
    assert direct["success_prompt"] == "REAL_ALPHA AZAAA.1 스플릿테이블 보여줘"
    assert [row["prompt"] for row in chat_prompts.get_sample_prompts("alice")["successful"]] == ["REAL_ALPHA AZAAA.1 스플릿테이블 보여줘"]


def test_existing_hitl_records_are_dropped_using_owned_history(private_store, monkeypatch):
    monkeypatch.setattr(chat_prompts, "_product_names", lambda: ["REAL_ALPHA"])
    with chat_conversations.turn("alice") as state:
        for role, content, response in [("user", "스플릿테이블 보여줘", None), ("assistant", "제품 선택", PRODUCT),
                                         ("user", "REAL_ALPHA", None), ("assistant", "완료", DONE)]:
            chat_conversations.append(state, role, content, **({"response": response} if response else {}))
    for prompt in ("REAL_ALPHA", "스플릿테이블 보여줘", "기록에 없는 질문"):
        chat_prompts.record_success(prompt, user="alice")
    chat_prompts.record_success("REAL_ALPHA", user="bob")
    # Answer and chain start are gone; a record without chat history stays.
    assert [row["prompt"] for row in chat_prompts.get_sample_prompts("alice")["successful"]] == ["기록에 없는 질문"]
    assert [row["prompt"] for row in chat_prompts.get_sample_prompts("bob")["successful"]] == ["REAL_ALPHA"]


def test_legacy_pending_only_question_is_not_success(private_store):
    with chat_conversations.turn("alice") as state:
        chat_conversations.append(state, "user", "REAL_ALPHA 스플릿테이블 보여줘")
        chat_conversations.append(state, "assistant", "Lot 입력", response=LOT)
    chat_prompts.record_success("REAL_ALPHA 스플릿테이블 보여줘", user="alice")
    assert chat_prompts.get_sample_prompts("alice")["successful"] == []


def test_question_that_also_completed_directly_is_kept(private_store, monkeypatch):
    monkeypatch.setattr(chat_prompts, "_product_names", lambda: ["REAL_ALPHA"])
    for chain in ([("user", "wafer 몇 장 있어", None), ("assistant", "제품 선택", PRODUCT),
                   ("user", "REAL_ALPHA", None), ("assistant", "완료", DONE)],
                  [("user", "wafer 몇 장 있어", None), ("assistant", "완료", DONE)]):
        with chat_conversations.turn("alice") as state:
            for role, content, response in chain:
                chat_conversations.append(state, role, content, **({"response": response} if response else {}))
    chat_prompts.record_success("wafer 몇 장 있어", user="alice")
    assert [row["prompt"] for row in chat_prompts.get_sample_prompts("alice")["successful"]] == ["wafer 몇 장 있어"]


def test_followup_that_relied_on_earlier_answer_is_not_recorded():
    messages = [user("REAL_ALPHA 대시보드 보여줘"), assistant(DONE), user("최근 1000일로 다시 보여줘")]
    assert chat_prompts.completed_origin(messages, DONE, products=["REAL_ALPHA"]) is None
    named = messages[:2] + [user("REAL_ALPHA ET 트렌드 보여줘")]
    assert chat_prompts.completed_origin(named, DONE, products=["REAL_ALPHA"])["prompt"] == "REAL_ALPHA ET 트렌드 보여줘"


def test_pasted_tables_and_admin_alias_changes_are_not_examples():
    table = user("PRODA Inline 별칭 업데이트\nstep_id\titem_id\talias\nP10\tI1\t선폭")
    assert chat_prompts.completed_origin([table], DONE, products=[]) is None
    alias = {"ok": True, "tool": {"feature": "semantic.alias_update", "table": {"rows": [{"a": 1}]}}}
    assert chat_prompts.completed_origin([user("PRODA 이름 프로드A도 인식하게 해줘")], alias, products=[]) is None


@pytest.fixture
def split_inventory(monkeypatch):
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)
    monkeypatch.setattr(product_semantics, "resolve_terms", lambda *a: [])
    monkeypatch.setattr(splittable, "list_products", lambda: {"products": [{"name": "ML_TABLE_REAL_ALPHA"}]})
    monkeypatch.setattr(data_chat, "available_product_names", lambda: ["REAL_ALPHA", "ET_ONLY"])
    monkeypatch.setattr(data_chat, "split_table_product", lambda p: "ML_TABLE_REAL_ALPHA" if p == "REAL_ALPHA" else "")
    monkeypatch.setattr(splittable, "list_customs", lambda: {"customs": []})
    from core import lot_progress_cache
    monkeypatch.setattr(lot_progress_cache, "lookup_lot_progress", lambda **k: [])


def test_split_choice_inventory_stays_scoped_across_retries(split_inventory):
    first = data_chat.execute("스플릿테이블 보여줘", {}, None)
    assert first["tool"]["clarification"]["options"] == [{"label": "REAL_ALPHA", "value": "REAL_ALPHA"}]
    invalid = data_chat.execute("ET_ONLY", first["context"], None)
    assert invalid["tool"]["missing"] == ["product"]
    assert invalid["tool"]["table"]["rows"] == [{"product": "REAL_ALPHA"}]
    selected = data_chat.execute("REAL_ALPHA", invalid["context"], None)
    assert selected["tool"]["missing"] == ["lot"]
    assert selected["context"]["confirmed_product"] == "REAL_ALPHA"


def test_existing_non_split_product_is_not_reused_for_split(split_inventory):
    out = data_chat.execute("스플릿테이블 보여줘", {"product": "ET_ONLY", "confirmed_product": "ET_ONLY"}, None)
    assert out["tool"]["missing"] == ["product"]
    assert not out["context"].get("confirmed_product")


def test_native_split_view_preserves_headers_cells_and_grouping(split_inventory, monkeypatch):
    view = {"headers": ["AZAAA|1"], "wafer_keys": ["1"], "prefix_columns": ["MODULE", "PARAM"],
            "root_lot_ids": ["AZAAA"], "s0_by_knob": {"KNOB_A": "S0"}, "warnings": [],
            "rows": [{"_param": "KNOB_A", "MODULE": "PHOTO", "_cells": {"0": {"actual": "A", "plan": "B", "not_reached": True}}}]}
    monkeypatch.setattr(splittable, "view_split", lambda **k: deepcopy(view))
    result = data_chat.execute("REAL_ALPHA AZAAA.1 스플릿테이블 보여줘", {}, None)
    assert result["tool"]["split_view"] == view
    assert result["tool"]["table"]["rows"] == [{"항목": "KNOB_A", "1": "A", "1 계획": "B"}]
    assert result["tool"]["context"]["product"] == "REAL_ALPHA"


def test_custom_query_survives_product_and_lot_clarification(split_inventory, monkeypatch):
    monkeypatch.setattr(splittable, "list_customs", lambda: {"customs": [{"name": "PC CUSTOM SET"}, {"name": "PC"}]})
    calls = []
    monkeypatch.setattr(splittable, "view_split", lambda **kw: calls.append(kw) or {"headers": [], "rows": []})
    first = data_chat.execute("PC CUSTOM SET으로 스플릿 보여줘", {}, None)
    second = data_chat.execute("REAL_ALPHA", first["context"], None)
    assert second["tool"]["missing"] == ["lot"]
    third = data_chat.execute("AZAAA.1", second["context"], None)
    assert third["ok"]
    assert calls[0]["custom_name"] == "PC CUSTOM SET"
    assert calls[0]["root_lot_id"] == "AZAAA"
    assert calls[0]["fab_lot_id"] == "AZAAA.1"


def test_unknown_custom_requires_choice_instead_of_prefix_fallback(split_inventory, monkeypatch):
    monkeypatch.setattr(splittable, "list_customs", lambda: {"customs": [{"name": "PC"}, {"name": "한글 세트"}]})
    calls = []
    monkeypatch.setattr(splittable, "view_split", lambda **kw: calls.append(kw) or {"headers": [], "rows": []})
    first = data_chat.execute("REAL_ALPHA AZAAA.1 UNKNOWN 커스텀 세트로 스플릿 보여줘", {}, None)
    assert first["tool"]["missing"] == ["custom_set"]
    assert len(first["tool"]["clarification"]["options"]) == 2
    assert calls == []
    result = data_chat.execute("한글 세트", first["context"], None)
    assert result["ok"]
    assert calls[0]["custom_name"] == "한글 세트"
    assert calls[0]["root_lot_id"] == "AZAAA"


def test_lot_history_after_clarification_never_calls_write_handler(split_inventory, monkeypatch):
    from core import auth, data_chat_split
    monkeypatch.setattr(auth, "current_user", lambda req: {"username": "tester", "role": "admin"})
    monkeypatch.setattr(data_chat_split, "handle", lambda *a: pytest.fail("history was routed to write handler"))
    calls = []
    monkeypatch.setattr(splittable, "get_history", lambda **kw: calls.append(kw) or {
        "history": [{"cell": "AZAAA|3|KNOB_A", "old": "A", "new": "B", "user": "tester", "time": "2026-09-21", "reason": "실험", "action": "set"}], "total": 1})
    first = data_chat.execute("스플릿테이블 수정 이력 보여줘", {}, None)
    second = data_chat.execute("REAL_ALPHA", first["context"], None)
    assert second["tool"]["missing"] == ["lot"]
    out = data_chat.execute("AZAAA.1", second["context"], None)
    assert out["tool"]["feature"] == "splittable.history"
    assert calls[0]["product"] == "ML_TABLE_REAL_ALPHA"
    assert calls[0]["root_lot_id"] == "AZAAA"
    assert out["tool"]["table"]["rows"][0]["변경 전"] == "A"
    assert out["tool"]["table"]["rows"][0]["Wafer"] == "3"
