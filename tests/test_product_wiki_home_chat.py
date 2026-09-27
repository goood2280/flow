"""Product Wiki organisation and its use from the home chat."""
import json
from types import SimpleNamespace

import pytest

from core import data_chat, data_chat_wiki, llm_adapter
from core import product_semantics as sem
from core import product_wiki as wiki
from core import product_wiki_knowledge as knowledge


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    paths = SimpleNamespace(db_root=tmp_path / "db", data_root=tmp_path / "data")
    paths.db_root.mkdir()
    paths.data_root.mkdir()
    monkeypatch.setattr(wiki, "PATHS", paths)
    monkeypatch.setattr(sem, "PATHS", paths)
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)
    return paths


GATE = {"kind": "issue", "title": "Gate CD 산포 과다", "status": "investigating", "structure": "Gate",
        "summary": "Edge Gate CD가 31.2nm까지 상향 이탈해 ESC 온도 Knob를 평가 중이다.",
        "body": "Gate Poly Etch 뒤 Edge Gate CD가 Target 28.0nm 대비 31.2nm로 이탈했다.",
        "tags": ["Gate CD", "ESC"], "lot_ids": ["LOT-FA201"],
        "conditions": [{"condition": "ESC Edge -1.5도", "purpose": "Edge CD 보정", "lot_ids": ["LOT-FA201"],
                        "result": "Edge CD 편차 1.1nm 이내"}]}
CONTACT = {"kind": "split", "title": "Contact 저항 Anneal 스플릿", "status": "closed",
           "body": "1050도 1.5초 조건에서 Sheet Resistance 82.4 ohm/sq로 POR 갱신.",
           "lot_ids": ["LOT-FA301"]}


def _seed(product="PRODA"):
    first = wiki.save_entry(product, 0, dict(GATE), "kim")
    second = wiki.save_entry(product, first["revision"], dict(CONTACT), "lee")
    gate = next(e for e in second["entries"] if e["title"] == GATE["title"])
    contact = next(e for e in second["entries"] if e["title"] == CONTACT["title"])
    return gate, contact, second["revision"]


def _ask(text, context=None):
    state = {"confirmed_product": "PRODA", **(context or {})}
    return data_chat_wiki.dispatch(text, state), state


def test_plain_text_reads_editor_html_and_keeps_plain_input():
    html = ('<p>Gate &amp; CD</p><table><tbody><tr><th>조건</th><th>결과</th></tr>'
            '<tr><td>A</td><td>1.1nm</td></tr></tbody></table><img src="/x.png" alt="paste_1.png"><style>p{}</style>')
    text = knowledge.plain_text(html)
    assert "Gate & CD" in text and "조건 | 결과" in text and "A | 1.1nm" in text
    assert "[이미지]" in text and "<" not in text and "p{}" not in text
    plain = "원문 첫 줄\n  들여쓴 줄"
    assert knowledge.plain_text(plain) == plain


def test_document_has_overview_recent_changes_and_record_digest(isolated):
    gate, contact, revision = _seed()
    updated = wiki.save_entry("PRODA", revision, {**GATE, "id": gate["id"], "status": "validated"}, "park")
    markdown = updated["wiki_document"]
    assert "## 1. 개요" in markdown and "## 2. 최근 변경" in markdown
    assert "등록된 제품 기록 2건 · 검증됨 1 · 종료 1." in markdown
    assert "park · 수정 · Gate CD 산포 과다 · 상태 조사 중→검증됨" in markdown
    assert "상태: 검증됨 · 종류: 이슈 · 구조: Gate" in markdown
    assert f"요약: {GATE['summary']}" in markdown
    assert "| ESC Edge -1.5도 | Edge CD 보정 | LOT-FA201 | Edge CD 편차 1.1nm 이내 |" in markdown
    toc_ids = {item["id"] for item in updated["wiki_toc"]}
    assert f"entry-{gate['id'][:8]}" in toc_ids and f"entry-{contact['id'][:8]}" in toc_ids
    ok, reason = wiki._validate_single_entry_document(markdown, wiki.document("PRODA")["entries"])
    assert ok, reason


def test_document_from_older_builder_is_reassembled_on_read(isolated):
    gate, _, _ = _seed()
    stored = wiki.document("PRODA")["wiki_document"]
    assert wiki.WIKI_FORMAT_MARK in stored
    with wiki.database() as db:
        db.execute("UPDATE products SET wiki_document=?, wiki_toc='[]' WHERE key='proda'",
                   ("# PRODA\n\n## 7. 종합 및 향후 관리 방안\n여러 이슈를 합친 서술",))
    doc = wiki.document("PRODA")
    assert "종합 및 향후 관리 방안" not in doc["wiki_document"]
    assert f"entry-{gate['id'][:8]}" in {item["id"] for item in doc["wiki_toc"]}


def test_body_cannot_open_a_heading_and_pasted_tables_stay_visible(isolated):
    source = "<p>검토</p><table><tr><td>A</td><td>1</td></tr></table>"
    saved = wiki.save_entry("PRODA", 0, {"kind": "issue", "title": "표 이슈", "body": "# 결론\n정리한 글",
                                         "source_text": source}, "kim")
    markdown = saved["wiki_document"]
    assert not any(line.strip() == "# 결론" for line in markdown.splitlines())
    assert "결론" not in json.dumps(saved["wiki_toc"], ensure_ascii=False)
    assert "원문 첨부 (표·이미지):" in markdown and "<table><tr><td>A</td><td>1</td></tr></table>" in markdown


def test_intake_sends_readable_text_and_validates_digest_fields(isolated, monkeypatch):
    captured = {}
    source = ("<p>Gate CD 31.2nm 이탈. ESC Edge -1.5도 조건을 LOT-FA201에 적용.</p>"
              "<table><tr><td>조건</td><td>결과</td></tr><tr><td>ESC</td><td>1.1nm</td></tr></table>")

    def complete(prompt, **kwargs):
        captured["prompt"] = prompt
        return {"ok": True, "text": json.dumps({
            "title": "Gate CD", "kind": "issue", "status": "investigating", "body": "Gate CD가 31.2nm로 이탈했다.",
            "summary": "Gate CD 31.2nm 이탈, 9.9nm 개선", "tags": ["Gate CD", "없는태그", "ESC"],
            "conditions": [{"condition": "ESC Edge -1.5도", "lot_ids": ["LOT-FA201", "LOT-FAKE"], "result": "1.1nm"},
                           {"condition": "없는 조건 7.7도", "result": "2.2nm"}],
            "lot_ids": ["LOT-FA201"], "evidence": ""}, ensure_ascii=False)}

    monkeypatch.setattr(llm_adapter, "complete", complete)
    saved = wiki.intake_entry("PRODA", 0, source, "kim")["entries"][0]
    sent = json.loads(captured["prompt"])["source_text"]
    assert "<td>" not in sent and "조건 | 결과" in sent
    assert saved["source_text"] == source
    assert saved["summary"] == ""  # 9.9nm is not in the original
    assert saved["tags"] == ["Gate CD", "ESC"]
    assert saved["conditions"] == [{"condition": "ESC Edge -1.5도", "purpose": "", "result": "1.1nm",
                                    "lot_ids": ["LOT-FA201"]}]


def test_structured_save_without_digest_keeps_saved_digest(isolated):
    gate, _, revision = _seed()
    from routers import product_wiki as api
    entry = api.Entry(id=gate["id"], kind="issue", title="Gate CD 산포 과다 (수정)", body=GATE["body"])
    saved = wiki.save_entry("PRODA", revision, entry.model_dump(), "kim")
    kept = next(e for e in saved["entries"] if e["id"] == gate["id"])
    assert kept["summary"] == GATE["summary"] and kept["conditions"] == GATE["conditions"]


def test_home_summary_without_model_lists_records_by_rule(isolated):
    gate, contact, _ = _seed()
    result, state = _ask("PRODA 위키 요약해줘")
    tool = result["tool"]
    assert tool["feature"] == "product.wiki" and tool["action"] == "product_wiki.summary"
    assert tool["answer_mode"] == "basic"
    assert f"[{gate['id'][:8]}]" in result["reply"] and f"[{contact['id'][:8]}]" in result["reply"]
    # open/investigating first
    assert result["reply"].index(gate["title"]) < result["reply"].index(contact["title"])
    assert {row["기록"] for row in tool["table"]["rows"]} == {gate["id"][:8], contact["id"][:8]}
    assert tool["wiki_link"] == "/productwiki?product=PRODA"
    assert state["wiki_last_ids"] == [gate["id"], contact["id"]]
    assert any(d["label"] == "답변 방식" and "AI 연결이 없어" in d["value"] for d in tool["interpretation"]["details"])


def test_home_explain_uses_grounded_model_answer(isolated, monkeypatch):
    gate, _, _ = _seed()
    captured = {}

    def complete(prompt, **kwargs):
        captured["payload"] = json.loads(prompt)
        return {"ok": True, "text": f"Edge Gate CD가 31.2nm로 이탈해 ESC 온도 조건을 조사 중입니다 [{gate['id'][:8]}]."}

    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(llm_adapter, "complete", complete)
    result, _ = _ask("Gate CD 이슈 설명해줘")
    assert result["tool"]["action"] == "product_wiki.explain"
    assert result["tool"]["answer_mode"] == "ai"
    assert result["reply"].startswith("Edge Gate CD가 31.2nm")
    records = captured["payload"]["records"]
    assert [r["id"] for r in records] == [gate["id"][:8]]
    assert records[0]["conditions"][0]["condition"] == "ESC Edge -1.5도"


@pytest.mark.parametrize("answer", [
    "Edge CD는 29.9nm로 개선됐습니다 [{gate}].",       # number not in any record
    "Edge CD가 31.2nm로 이탈했습니다 [deadbeef].",       # cites a record that was not given
    "Edge CD가 31.2nm로 이탈했습니다.",                  # no citation at all
])
def test_home_rejects_ungrounded_model_answer(isolated, monkeypatch, answer):
    gate, _, _ = _seed()
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(llm_adapter, "complete", lambda *a, **k: {"ok": True, "text": answer.format(gate=gate["id"][:8])})
    result, _ = _ask("Gate CD 이슈 설명해줘")
    assert result["tool"]["answer_mode"] == "basic"
    assert "29.9" not in result["reply"] and result["reply"].startswith("Gate CD 산포 과다 [")
    assert "조건: ESC Edge -1.5도 / 목적 Edge CD 보정 / LOT LOT-FA201 / 결과 Edge CD 편차 1.1nm 이내" in result["reply"]


def test_home_change_summary_and_ordinal_follow_up(isolated):
    gate, contact, revision = _seed()
    wiki.save_entry("PRODA", revision, {**GATE, "id": gate["id"], "status": "validated"}, "park")
    changes, _ = _ask("PRODA 위키 최근 변경 정리해줘")
    assert changes["tool"]["action"] == "product_wiki.changes"
    assert "상태 조사 중→검증됨" in changes["reply"] and "신규 등록" in changes["reply"]
    assert changes["tool"]["table"]["rows"][0]["변경 내용"] == "상태 조사 중→검증됨"

    listing, state = _ask("PRODA 위키 요약해줘")
    second = state["wiki_last_ids"][1]
    follow, _ = _ask("2번 설명해줘", {"last_feature": "product.wiki", "wiki_last_ids": state["wiki_last_ids"]})
    assert follow["tool"]["action"] == "product_wiki.explain"
    assert follow["tool"]["cited_records"] == [second[:8]]


def test_home_leaves_other_features_their_questions(isolated):
    _seed()
    for text in ("ET 트래커 이슈 목록 보여줘", "PRODA A1001 지금 어디 있어?", "PRODA Gate CD 추이 차트 그려줘",
                 "PRODA Gate CD 별칭 알려줘", "PRODA A1001 스플릿테이블 변경 이력 보여줘"):
        assert _ask(text)[0] is None, text
    # A topic question for a product without Wiki records keeps its old route.
    assert data_chat_wiki.dispatch("이슈 목록 알려줘", {"confirmed_product": "PRODB"}) is None
    # No product confirmed yet: nothing to answer from.
    assert data_chat_wiki.dispatch("위키 요약해줘", {}) is None


def test_home_no_match_shows_recent_records(isolated):
    _seed()
    result, _ = _ask("PRODA 위키에서 Via 누설 이슈 찾아줘")
    assert result["tool"]["action"] == "product_wiki.no_match"
    assert "관련 기록을 찾지 못했습니다" in result["reply"] and len(result["tool"]["table"]["rows"]) == 2


def test_execute_routes_wiki_questions_and_asks_for_product(isolated, monkeypatch):
    gate, _, _ = _seed()
    monkeypatch.setattr(data_chat, "available_product_names", lambda: ["PRODA", "PRODB"])
    from core import (data_chat_split_query, data_chat_eta, data_chat_inline,
                      data_chat_inline_chart, data_chat_wafer_map, data_chat_ml_chart)
    for module in (data_chat_split_query, data_chat_eta, data_chat_inline,
                   data_chat_inline_chart, data_chat_wafer_map, data_chat_ml_chart):
        monkeypatch.setattr(module, "dispatch", lambda *args: None)
    missing = data_chat.execute("위키 요약해줘", {}, None)
    assert missing["tool"]["missing"] == ["product"]
    answered = data_chat.execute("PRODA 위키 요약해줘", {}, None)
    assert answered["tool"]["feature"] == "product.wiki"
    assert f"[{gate['id'][:8]}]" in answered["reply"]
    assert answered["context"]["last_feature"] == "product.wiki"
    follow = data_chat.execute("1번 설명해줘", answered["context"], None)
    assert follow["tool"]["action"] == "product_wiki.explain"
    assert follow["tool"]["cited_records"] == [gate["id"][:8]]
