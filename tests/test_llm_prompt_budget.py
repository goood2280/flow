"""Reference context sent to the on-premise LLM stays relevant and bounded."""
from __future__ import annotations

import json


def test_search_terms_strip_korean_particles():
    from core import llm_prompt_budget as budget

    terms = budget.search_terms("나노시트 두께가 VTH 영향은? 게이트랑 보여줘")
    assert {"나노시트", "두께", "vth", "영향", "게이트"} <= terms
    assert "보여줘" not in terms


def test_fit_keeps_relevant_items_first_within_budget():
    from core import llm_prompt_budget as budget

    items = [{"item_id": f"X{i}", "item_desc": "filler " * 5} for i in range(200)]
    items.insert(150, {"item_id": "CD_GATE", "item_desc": "Gate CD"})
    kept, info = budget.fit(items, 800, terms=budget.search_terms("게이트 CD_GATE 보여줘"))
    assert kept[0]["item_id"] == "CD_GATE"
    assert budget.compact_size(kept) <= 800
    assert info["total"] == 201 and info["omitted"] == 201 - len(kept)


def test_domain_knowledge_prompt_prefers_relevant_sections(monkeypatch):
    from core import domain_knowledge

    body = "\n\n".join(
        [f"## 무관한 절 {i}\n" + ("공정 일반 설명 " * 120) for i in range(8)]
        + ["## 나노시트\n나노시트 두께는 채널 높이를 결정한다."]
    )
    monkeypatch.setattr(domain_knowledge, "read_document", lambda version=None: {
        "title": "기본지식", "body": body, "version": 3})
    out = domain_knowledge.prompt_context("나노시트 두께가 궁금해", max_chars=2000)
    assert "나노시트 두께는" in out["body"]
    assert len(out["body"]) <= 2000
    assert out["partial"] is True


def test_long_section_is_split_into_subsections(monkeypatch):
    from core import domain_knowledge

    body = "## 구조\n" + "\n".join(f"### 소절 {i}\n" + ("설명 " * 400) for i in range(4)) + "\n### Gate\n게이트 CD 정의"
    monkeypatch.setattr(domain_knowledge, "read_document", lambda version=None: {
        "title": "기본지식", "body": body, "version": 1})
    out = domain_knowledge.prompt_context("Gate CD", max_chars=1500)
    assert "## 구조\n### Gate" in out["body"]


def test_gemma4_timeout_floor_and_structured_temperature(monkeypatch):
    from core import llm_adapter

    cfg = llm_adapter._normalize_runtime_config({"enabled": True, "provider": "gemma4", "timeout_s": 20,
                                                "api_url": "http://gemma", "admin_token": "t", "system_name": "flow"})
    assert cfg["timeout_s"] >= llm_adapter.GEMMA4_MIN_TIMEOUT_S

    sent = {}
    monkeypatch.setattr(llm_adapter, "_raw_config", lambda: cfg)

    def fake_complete(prompt, **kwargs):
        sent.update(kwargs)
        return {"ok": True, "text": json.dumps({"action": "clarify", "params": {}})}

    monkeypatch.setattr(llm_adapter, "complete", fake_complete)
    out = llm_adapter.complete_json("q", schema={"type": "object", "properties": {"action": {"type": "string"},
                                                                                  "params": {"type": "object"}},
                                                 "required": ["action", "params"]})
    assert out["ok"] is True
    assert sent["request_overrides"] == {"temperature": llm_adapter.STRUCTURED_TEMPERATURE}
