"""Home landing lists completed Flow-i results, not questions awaiting input."""
from __future__ import annotations


def test_recent_results_summarise_completed_outputs(tmp_path, monkeypatch):
    from core import chat_conversations
    from core.paths import PATHS

    monkeypatch.setattr(PATHS, "data_root", tmp_path)
    with chat_conversations.turn("eng") as state:
        chat_conversations.append(state, "user", "PRODA INLINE CD 추이 차트 그려줘")
        chat_conversations.append(state, "assistant", "측정 항목을 골라 주세요.", response={
            "tool": {"feature": "chart", "clarification": {"kind": "inline_measure"}}})
        chat_conversations.append(state, "user", "2")
        chat_conversations.append(state, "assistant", "차트를 만들었습니다.", response={
            "tool": {"feature": "chart", "chart_result": {"title": "PRODA CD Trend", "chart_type": "scatter"},
                     "query_scope": {"product": "PRODA"}, "saved_chart": {"id": "chart_1"}}})
        chat_conversations.append(state, "user", "A1001 스플릿테이블 보여줘")
        chat_conversations.append(state, "assistant", "실패", error=True, response={"tool": {"table": {"rows": []}}})

    rows = chat_conversations.recent_results("eng")
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "chart"
    assert row["title"] == "PRODA CD Trend"
    assert row["question"] == "PRODA INLINE CD 추이 차트 그려줘"  # the choice "2" is not the question
    assert row["product"] == "PRODA"
    assert row["saved_chart_id"] == "chart_1"
    assert row["conversation_id"] and row["message_id"]


def test_recent_results_empty_for_new_user(tmp_path, monkeypatch):
    from core import chat_conversations
    from core.paths import PATHS

    monkeypatch.setattr(PATHS, "data_root", tmp_path)
    assert chat_conversations.recent_results("nobody") == []
