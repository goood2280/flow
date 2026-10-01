"""홈 계획 LLM 프롬프트는 고정 내용이 앞, 질문이 맨 뒤 — 서버 prefix cache 재사용."""
import json


def test_planner_prompt_puts_static_keys_first_and_request_last(monkeypatch):
    from core import data_chat, data_chat_features, llm_adapter

    calls = []
    monkeypatch.setattr(data_chat, "available_product_names", lambda: ["PRODA", "PRODB"])
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(llm_adapter, "complete_json",
                        lambda prompt, **k: calls.append(prompt) or {"ok": True, "obj": {"action": "clarify", "params": {}}})
    action, _params = data_chat._feature_plan("PRODB A2002 요즘 어떻게 돼가", {}, [], data_chat_features)
    assert action == "clarify" and len(calls) == 1
    keys = list(json.loads(calls[0]))
    assert keys[:2] == ["tools", "actual_products"]
    assert keys[-3:] == ["context", "history", "request"]
