from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core import knowledge_cards as kc  # noqa: E402


SEED_CARD = """---
term: ppid_knob.csv
kind: rulebook
aliases: [KNOB 룰북, split 규칙]
trigger_terms: [knob 분류]
answered_by: ppid_knob
sources:
  - file: ppid_knob.csv
    role: rulebook
related: [split-question-playbook]
status: active
---
split 규칙은 ppid_knob.csv 에 있다.
"""

PPID_ROWS = [
    {"feature_name": "3.0 VTN", "function_step": "VTN_IMPLANT", "rule_order": "R1", "operator": "eq", "value": "PPID_03_1", "category": "VTN_A"},
    {"feature_name": "3.0 VTN", "function_step": "VTN_IMPLANT", "rule_order": "RO", "operator": "eq", "value": "", "category": "VTN_REST"},
    {"feature_name": "4.0 GATE_OX", "function_step": "GATE_OX", "rule_order": "R1", "operator": "eq", "value": "PPID_04_1", "category": "GOX_A"},
]


def _isolate(monkeypatch, tmp_path, *, seed_texts=None, local_texts=None):
    seed_dir = tmp_path / "seeds"
    local_dir = tmp_path / "local"
    seed_dir.mkdir()
    local_dir.mkdir()
    for i, text in enumerate(seed_texts or []):
        (seed_dir / f"seed{i}.md").write_text(text, encoding="utf-8")
    for i, text in enumerate(local_texts or []):
        (local_dir / f"local{i}.md").write_text(text, encoding="utf-8")
    monkeypatch.setattr(kc, "SEED_DIR", seed_dir)
    monkeypatch.setattr(kc, "LOCAL_DIR", local_dir)
    monkeypatch.setattr(kc, "FILL_QUESTIONS_FILE", tmp_path / "fill_questions.json")
    monkeypatch.setattr(kc, "_generated_cards", lambda rows=None: [])
    monkeypatch.setattr(kc, "_adapter_cards", lambda: [])
    monkeypatch.setattr(kc, "_fill_evidence", lambda max_chars=1800: "")
    monkeypatch.setattr(kc, "_CACHE", {"built_at": 0.0, "sig": None, "cards": []})


# ── frontmatter 파서 ─────────────────────────────────────────────────────────
def test_parse_card_text_full():
    card = kc.parse_card_text(SEED_CARD, origin="seed", path="x.md")
    assert card["term"] == "ppid_knob.csv"
    assert card["kind"] == "rulebook"
    assert card["aliases"] == ["KNOB 룰북", "split 규칙"]
    assert card["trigger_terms"] == ["knob 분류"]
    assert card["answered_by"] == "ppid_knob"
    assert card["sources"] == [{"file": "ppid_knob.csv", "role": "rulebook"}]
    assert card["related"] == ["split-question-playbook"]
    assert "split 규칙은" in card["body"]


def test_parse_card_text_requires_frontmatter_and_term():
    assert kc.parse_card_text("no frontmatter") is None
    assert kc.parse_card_text("---\nkind: x\n---\nbody") is None


def test_render_parse_roundtrip():
    card = kc.parse_card_text(SEED_CARD, origin="local")
    text = kc.render_card_text(card)
    back = kc.parse_card_text(text, origin="local")
    for key in ("term", "kind", "aliases", "trigger_terms", "answered_by", "sources", "related"):
        assert back[key] == card[key], key


# ── 병합/조회 ────────────────────────────────────────────────────────────────
def test_local_overrides_seed(monkeypatch, tmp_path):
    local = SEED_CARD.replace("split 규칙은 ppid_knob.csv 에 있다.", "사내 교정 본문")
    _isolate(monkeypatch, tmp_path, seed_texts=[SEED_CARD], local_texts=[local])
    cards = kc.load_cards(force=True)
    assert len(cards) == 1
    assert cards[0]["origin"] == "local"
    assert "사내 교정 본문" in cards[0]["body"]


def test_resolve_matches_term_alias_and_korean_trigger(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path, seed_texts=[SEED_CARD])
    assert kc.resolve("ppid_knob.csv 어디서 봐")[0]["matched"] == "ppid_knob.csv"
    assert kc.resolve("PPID_08_0 knob 분류 알려줘")[0]["matched"] == "knob 분류"
    # 영문 토큰은 경계 매칭 — 다른 단어 내부에 파묻힌 경우 미매칭
    assert kc.resolve("아무 관련 없는 질문") == []


def test_resolve_skips_todo_and_disabled(monkeypatch, tmp_path):
    todo = SEED_CARD.replace("status: active", "status: todo").replace("term: ppid_knob.csv", "term: 채움틀")
    _isolate(monkeypatch, tmp_path, seed_texts=[SEED_CARD, todo])
    assert all(c["term"] != "채움틀" for c in kc.resolve("채움틀 알려줘"))


def test_unit_hints_and_reorder(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path, seed_texts=[SEED_CARD])
    prompt = "PPID_08_0 knob 분류 알려줘"
    assert kc.unit_hints(prompt) == ["ppid_knob"]
    units = ["split_nav", "step_lookup", "ppid_knob", "filebrowser"]
    assert kc.reorder_units(prompt, units) == ["ppid_knob", "split_nav", "step_lookup", "filebrowser"]
    # 힌트가 없으면 순서 무변경
    assert kc.reorder_units("아무 질문", units) == units


def test_prompt_block_contains_source_and_unit(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path, seed_texts=[SEED_CARD])
    block = kc.prompt_block("knob 분류 규칙 알려줘")
    assert "ppid_knob.csv" in block
    assert "담당 유닛: ppid_knob" in block
    assert kc.prompt_block("전혀 무관한 질문") == ""


# ── 생성 지식 ────────────────────────────────────────────────────────────────
def test_generated_cards_from_rulebook_rows():
    cards = kc._generated_cards(rows=PPID_ROWS)
    by_term = {c["term"]: c for c in cards}
    assert set(by_term) == {"3.0 VTN", "4.0 GATE_OX"}
    vtn = by_term["3.0 VTN"]
    assert vtn["answered_by"] == "ppid_knob"
    assert vtn["origin"] == "generated"
    assert "규칙 2건" in vtn["body"]
    assert "VTN_A" in vtn["body"] and "VTN_REST" in vtn["body"]
    assert vtn["aliases"] == ["VTN_IMPLANT"]


_ORIG_GENERATED = kc._generated_cards


def test_generated_card_resolves_in_prompt(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(kc, "_generated_cards", lambda rows=None: _ORIG_GENERATED(rows=PPID_ROWS))
    cards = kc.load_cards(force=True)
    assert any(c["term"] == "3.0 VTN" for c in cards)
    hit = kc.resolve("A1002 스플릿테이블 3.0 VTN, 4.0 GATE_OX 만 보여줘")
    terms = {c["term"] for c in hit}
    assert {"3.0 VTN", "4.0 GATE_OX"} <= terms


# ── AI 채움 → draft → 승인 HITL ──────────────────────────────────────────────
TODO_CARD = """---
term: 사내 용어 사전
kind: glossary
status: todo
---
- 자주 쓰는 은어를 채워주세요.
"""


def test_fill_todo_cards_creates_hidden_draft_then_approve(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path, seed_texts=[TODO_CARD])
    result = kc.fill_todo_cards(complete_fn=lambda p, s: "- 겔징: 게이트 형성 이후 징후 점검을 뜻한다. (확인 필요)")
    assert result["ok"] and result["filled"] == ["사내 용어 사전"]
    assert result["remaining_todo"] == 0
    # draft 는 resolve 에 노출되지 않는다 (승인 전 미사용)
    assert kc.resolve("사내 용어 사전 알려줘") == []
    assert kc.cards_by_status("draft") and kc.cards_by_status("draft")[0]["origin"] == "local"
    # 승인 → active → 즉시 조회 가능
    assert kc.set_status("사내 용어 사전", "active", by="admin")
    hit = kc.resolve("사내 용어 사전 알려줘")
    assert hit and "겔징" in hit[0]["body"]


def test_fill_todo_cards_short_answer_fails_and_todo_remains(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path, seed_texts=[TODO_CARD])
    result = kc.fill_todo_cards(complete_fn=lambda p, s: "짧음")
    assert result["ok"] and result["filled"] == [] and result["failed"] == ["사내 용어 사전"]
    assert result["remaining_todo"] == 1


def test_reject_draft_restores_seed_todo(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path, seed_texts=[TODO_CARD])
    kc.fill_todo_cards(complete_fn=lambda p, s: "- 초안 본문입니다. 검토용으로 충분히 긴 내용.")
    assert kc.cards_by_status("draft")
    assert kc.forget_card("사내 용어 사전") is True
    assert kc.cards_by_status("draft") == []
    assert [c["term"] for c in kc.cards_by_status("todo")] == ["사내 용어 사전"]


def test_fill_records_open_questions_and_answer_merges(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path, seed_texts=[TODO_CARD])
    draft = "- 확인된 사실입니다.\n\n## 남은 질문\n- ET DB 파일 위치가 어디인가요?\n- 조인 키는 무엇인가요?"
    result = kc.fill_todo_cards(complete_fn=lambda p, s: draft)
    assert result["questions"] == {"사내 용어 사전": ["ET DB 파일 위치가 어디인가요?", "조인 키는 무엇인가요?"]}
    assert kc.pending_fill_questions()["사내 용어 사전"]
    # "지식 답변: <term> <답>" — 최장 일치 term + 병합 + 질문 소진
    merged = kc.answer_fill_question("사내 용어 사전 ET DB는 db_root/ET 폴더의 parquet입니다", by="admin")
    assert merged and merged["term"] == "사내 용어 사전"
    assert merged["remaining_questions"] == ["조인 키는 무엇인가요?"]
    card = kc.find_card("사내 용어 사전")
    assert "## 사용자 답변" in card["body"] and "db_root/ET" in card["body"]
    assert card["status"] == "draft"  # 답변해도 승인 전까지 draft 유지
    # 형식 오류/모르는 term 은 None
    assert kc.answer_fill_question("없는카드 답변입니다") is None


def test_extract_open_questions_fallback_to_confirm_needed():
    body = "- 숫자는 공정 순서로 보인다 (확인 필요)\n- 확정 사실."
    qs = kc._extract_open_questions(body)
    assert qs == ["숫자는 공정 순서로 보인다 (확인 필요)"]


def test_set_status_preserves_trigger_terms_and_related(monkeypatch, tmp_path):
    # 시드 카드(trigger_terms/related 보유)를 승인/재활성해도 값이 소실되지 않아야 한다.
    seed = """---
term: 차트 질문 플레이북
kind: playbook
trigger_terms: [그려, 차트, 추세]
related: [et-index-chart, commonality]
status: active
---
차트는 dashboard 유닛이 그린다.
"""
    _isolate(monkeypatch, tmp_path, seed_texts=[seed])
    # 승인(active 재기록) → local 카드로 재작성되지만 trigger_terms/related 유지
    saved = kc.set_status("차트 질문 플레이북", "active", by="admin")
    assert saved and saved["origin"] == "local"
    card = kc.find_card("차트 질문 플레이북")
    assert card["origin"] == "local"  # local 이 seed 를 가림
    assert card["trigger_terms"] == ["그려", "차트", "추세"]
    assert card["related"] == ["et-index-chart", "commonality"]
    # trigger_term 으로 여전히 매칭돼야 한다 (회귀 방지 핵심)
    assert kc.resolve("이거 추세 좀 봐줘")


def test_answer_fill_preserves_trigger_terms(monkeypatch, tmp_path):
    seed = """---
term: ET Index
kind: concept
trigger_terms: [ET 그려]
related: [reformatter]
status: draft
---
## 남은 질문
- 대표값 규칙은?
"""
    _isolate(monkeypatch, tmp_path, seed_texts=[seed])
    kc._save_fill_questions({"ET Index": ["대표값 규칙은?"]})
    merged = kc.answer_fill_question("ET Index 대표값은 wafer median 을 쓴다", by="admin")
    assert merged
    card = kc.find_card("ET Index")
    assert card["trigger_terms"] == ["ET 그려"]
    assert card["related"] == ["reformatter"]


def test_status_summary(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path, seed_texts=[SEED_CARD, TODO_CARD])
    summary = kc.status_summary()
    assert summary["counts"].get("active") == 1
    assert summary["todo"] == ["사내 용어 사전"]


# ── 티칭 (local 쓰기) ────────────────────────────────────────────────────────
def test_teach_and_forget_card(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path, seed_texts=[SEED_CARD])
    saved = kc.teach_card("CA_RS", "contact resistance 항목. ET 소스에서 본다.", by="tester",
                          sources=[{"file": "et_db.parquet"}])
    assert saved and saved["origin"] == "local"
    cards = kc.load_cards(force=True)
    assert any(c["term"] == "CA_RS" and c["origin"] == "local" for c in cards)
    hit = kc.resolve("CA_RS 가 뭐야")
    assert hit and hit[0]["term"] == "CA_RS"
    assert kc.knowledge_sources(hit) == ["et_db.parquet"]
    assert kc.teach_card("", "body") is None
    assert kc.forget_card("CA_RS") is True
    assert kc.forget_card("CA_RS") is False
    assert all(c["term"] != "CA_RS" for c in kc.load_cards(force=True))
