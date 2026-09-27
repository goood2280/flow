"""Home chat: Excel table paste and administrator alias updates with approval."""
import json
from types import SimpleNamespace

import pytest

from core import chat_table, data_chat, data_chat_semantic_admin as admin, flowi_turn, llm_adapter
from core import product_semantics as sem, product_wiki as wiki


ADMIN = SimpleNamespace(state=SimpleNamespace(user={"username": "boss", "role": "admin"}), headers={})
VIEWER = SimpleNamespace(state=SimpleNamespace(user={"username": "viewer", "role": "user"}), headers={})


@pytest.fixture
def semantics(tmp_path, monkeypatch):
    paths = SimpleNamespace(data_root=tmp_path / "data", db_root=tmp_path / "db")
    for module in (wiki, sem, admin):
        monkeypatch.setattr(module, "PATHS", paths)
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)
    snap = {"product_names": ["PRODA", "PRODB"], "products": [{"product": "PRODA"}, {"product": "PRODB"}],
            "steps": [], "measurements": [
                {"product": "PRODA", "module": "PC", "source_type": "INLINE", "step_id": "P10", "item_id": "I1", "item_desc": "Gate CD"},
                {"product": "PRODA", "module": "PC", "source_type": "INLINE", "step_id": "P20", "item_id": "I2", "item_desc": "Gate THK"},
                {"product": "PRODA", "module": "", "source_type": "ET", "step_id": "", "item_id": "VTH_IDX", "item_desc": "VTH"},
            ]}
    sem._snapshot_path().parent.mkdir(parents=True)
    sem._snapshot_path().write_text(json.dumps(snap), encoding="utf-8")
    monkeypatch.setattr(data_chat, "available_product_names", lambda: ["PRODA", "PRODB"])
    monkeypatch.setattr(data_chat, "available_split_product_names", lambda: ["PRODA", "PRODB"])
    return paths


def turn(prompt, context, request=ADMIN):
    result = data_chat.execute(prompt, context, request)
    return result, result.get("context", context)


TABLE = "PRODA Inline 별칭 업데이트하고 싶어\nstep_id\titem_id\talias\np10\tI1\t게이트CD, 선폭\nP20\tI2\t게이트두께\nP99\tI9\t없는항목\n"


def test_excel_paste_is_one_question_with_rows():
    table = chat_table.parse('설명\nA\tB\n"x\ty"\t2\n\n')
    assert table["intro"] == "설명"
    assert table["rows"] == [["A", "B"], ["x y", "2"]]
    assert flowi_turn.split_questions(TABLE) == [TABLE.strip()]
    assert flowi_turn.split_questions("질문1\n질문2") == ["질문1", "질문2"]


def test_item_alias_table_preview_then_update_with_log(semantics):
    preview, context = turn(TABLE, {})
    tool = preview["tool"]
    assert tool["feature"] == "semantic.alias_update"
    assert tool["approval"]["status"] == "pending"
    assert preview["reply"].startswith("아래와 같이 인식되었습니다")
    rows = {row["Item ID"]: row for row in tool["table"]["rows"]}
    assert rows["I1"]["Step ID"] == "P10" and rows["I1"]["결과"].startswith("추가 예정")
    assert rows["I9"]["결과"].startswith("매칭표에 없는")
    assert not sem.item_aliases("PRODA")  # nothing written before approval
    assert flowi_turn._status(preview) == "needs_input"

    done, context = turn("업데이트 해줘", context)
    assert done["ok"] and done["tool"]["approval"]["status"] == "applied"
    saved = {row["item_id"]: row["aliases"] for row in sem.item_aliases("PRODA")}
    assert saved == {"I1": ["게이트CD", "선폭"], "I2": ["게이트두께"]}
    assert "pending_semantic_update" not in context
    assert sem.resolve_terms("PRODA", "게이트CD 추이")[0]["item_id"] == "I1"

    log = sem.alias_log("PRODA")
    assert {(entry["item_id"], entry["via"], entry["actor"]) for entry in log} == {("I1", "chat", "boss"), ("I2", "chat", "boss")}
    assert next(entry for entry in log if entry["item_id"] == "I1")["added"] == ["게이트CD", "선폭"]

    again, _ = turn(TABLE, {})
    assert again["ok"] is False and "새로 추가할 별칭이 없습니다" in again["reply"]


def test_aliases_are_merged_not_replaced(semantics):
    sem.save_item_alias("PRODA", "P10", "I1", ["기존별칭"], "admin")
    _, context = turn("PRODA 별칭\nstep_id\titem_id\talias\nP10\tI1\t새별칭", {})
    turn("승인", context)
    assert sem.item_aliases("PRODA")[0]["aliases"] == ["기존별칭", "새별칭"]


def test_cancel_and_other_question_abandon_preview(semantics):
    _, context = turn(TABLE, {})
    cancelled, context = turn("취소", context)
    assert "취소" in cancelled["reply"] and not sem.item_aliases("PRODA")
    _, context = turn(TABLE, {})
    identifier = context["pending_semantic_update"]
    admin.handle("PRODA 대시보드 보여줘", context, ADMIN)
    assert "pending_semantic_update" not in context
    stale = admin.handle(f"승인 {identifier}", context, ADMIN)
    assert stale["ok"] is False and not sem.item_aliases("PRODA")


def test_table_without_product_asks_then_uses_answer(semantics):
    table = "Inline 별칭 추가\nstep_id\titem_id\talias\nP10\tI1\t선폭"
    asked, context = turn(table, {})
    assert asked["tool"]["missing"] == ["product"]
    assert asked["tool"]["clarification"]["kind"] == "product"
    preview, context = turn("PRODA", context)
    assert preview["tool"]["approval"]["status"] == "pending"


def test_et_two_column_table(semantics):
    _, context = turn("PRODA ET 별칭 업데이트\nVTH_IDX\t문턱전압", {})
    turn("네", context)
    assert sem.item_aliases("PRODA", "ET")[0]["aliases"] == ["문턱전압"]


def test_product_alias_sentence(semantics):
    preview, context = turn("prodA 이름 프로드A도 인식하게 해줘", {})
    assert preview["tool"]["approval"]["status"] == "pending"
    assert [row["추가 별칭"] for row in preview["tool"]["table"]["rows"]] == ["프로드A"]
    assert not sem.product_aliases()
    done, _ = turn("응 추가해줘", context)
    assert done["ok"]
    assert sem.product_aliases()[0]["aliases"] == ["프로드A"]
    assert data_chat.product_candidates("프로드A 스플릿 보여줘", ["PRODA", "PRODB"]) == ["PRODA"]
    assert sem.alias_log(kind="product")[0]["via"] == "chat"


def test_product_alias_rejects_other_product_name(semantics):
    preview, _ = turn('PRODA 별칭 "PRODB" 추가해줘', {})
    assert preview["ok"] is False
    assert "실제 제품명" in preview["tool"]["table"]["rows"][0]["결과"]


def test_non_manager_cannot_update(semantics):
    denied, _ = turn(TABLE, {}, request=VIEWER)
    assert denied["ok"] is False and denied["tool"]["blocked"]
    assert not sem.item_aliases("PRODA")


def test_unrelated_requests_pass_through(semantics):
    for prompt in ("범례 이름 추가해줘", "PRODA 별칭 뭐야", "차트 제목 바꿔줘"):
        assert admin.handle(prompt, {}, ADMIN) is None
    assert admin.handle("x\ty\n1\t2", {}, ADMIN) is None  # a table without alias columns


def test_admin_ui_saves_are_logged(semantics):
    sem.save_product_aliases("PRODA", ["P-A"], "kim")
    sem.save_product_aliases("PRODA", ["P-A"], "kim", sem.product_aliases()[0]["updated_at"])  # no change, no log
    entries = sem.alias_log("PRODA")
    assert len(entries) == 1 and entries[0]["via"] == "admin" and entries[0]["added"] == ["P-A"]
    assert sem.alias_log("PRODB") == []
