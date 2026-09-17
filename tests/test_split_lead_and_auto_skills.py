import pytest
from unittest.mock import MagicMock
from core import split_lead_tracker, flowi_personalization, chat_prompts, chat_conversations
from routers import flowi_learning


def test_split_lead_tracker_direct():
    # 1. Test column resolution
    cols = split_lead_tracker.get_product_split_columns("PRODA")
    assert len(cols) > 0
    resolved_col, matches = split_lead_tracker.resolve_split_column("PRODA", "5.0 PC")
    assert resolved_col == "KNOB_5.0 PC"
    assert "KNOB_5.0 PC" in matches

    # 2. Test split leading lot lookup
    result = split_lead_tracker.find_split_leading_lot("PRODA", "KNOB_5.0 PC", "PPID_05_1_S0")
    assert result["ok"] is True
    assert result["lead_lot"] is not None
    assert "lot_id" in result["lead_lot"]
    assert "step_id" in result["lead_lot"]
    assert len(result["table"]["rows"]) > 0

    # 3. Test split distribution chart
    dist = split_lead_tracker.get_split_distribution_chart("PRODA", "KNOB_5.0 PC")
    assert dist["ok"] is True
    assert dist["chart_result"] is not None
    assert dist["chart_result"]["type"] == "pie"
    assert len(dist["chart_result"]["groups"]) > 0


def test_handle_split_chat_query():
    # 1. Full query with product, column, value -> Leading lot
    r1 = split_lead_tracker.handle_split_chat_query("PRODA 5.0 PC PPID_05_1_S0 선두랏이 뭐야")
    assert r1 is not None
    assert r1["ok"] is True
    assert "선두 랏" in r1["reply"] or "가장 앞선 랏" in r1["reply"]
    assert r1["tool"]["feature"] == "splittable"

    # 2. Ambiguous query (no value specified) -> HITL candidates
    r2 = split_lead_tracker.handle_split_chat_query("PRODA 5.0 PC 선행하는 랏 뭐야")
    assert r2 is not None
    assert r2["ok"] is True
    assert "split_candidates" in r2["tool"]
    assert len(r2["tool"]["split_candidates"]) > 0

    # 3. Split distribution query -> Pie chart
    r3 = split_lead_tracker.handle_split_chat_query("PRODA 5.0 PC split 분포 보여줘")
    assert r3 is not None
    assert r3["ok"] is True
    assert r3["tool"]["feature"] == "dashboard"
    assert r3["tool"]["chart_result"]["type"] == "pie"


def test_auto_skill_creation_on_20_usages(monkeypatch, tmp_path):
    temp_json = tmp_path / "chat_sample_prompts.json"
    monkeypatch.setattr(chat_prompts, "PROMPTS_FILE", temp_json)

    test_prompt = "테스트분석 랏 선행 추적 질문 999"
    created_skills = []

    def mock_auto_create(prompt, category=""):
        created_skills.append((prompt, category))
        return {"id": "auto_999", "title": prompt}

    monkeypatch.setattr(flowi_personalization, "auto_create_skill_from_prompt", mock_auto_create)

    # 19 times across users
    for i in range(19):
        chat_prompts.record_success(user=f"user_{i}", prompt=test_prompt)
    assert len(created_skills) == 0

    # 20th time -> should trigger auto skill
    chat_prompts.record_success(user="user_20", prompt=test_prompt)
    assert len(created_skills) == 1
    assert created_skills[0][0] == test_prompt

    # 21st time -> should not re-trigger because auto_skilled=True
    chat_prompts.record_success(user="user_21", prompt=test_prompt)
    assert len(created_skills) == 1


def test_flowi_learning_admin_functions(monkeypatch):
    mock_req = MagicMock()
    monkeypatch.setattr(flowi_learning, "_require_admin", lambda req: {"username": "admin", "role": "admin"})

    # 1. Curate skill
    curate_body = flowi_learning.SkillCurateReq(
        title="단위테스트 스킬",
        procedure="PRODA 5.0 PC 선두랏 조회 상세 절차 및 실행 규칙",
        feature="table",
        action="split_lead",
        shared=True,
        auto=False,
    )
    res1 = flowi_learning.curate_skill(mock_req, curate_body)
    assert res1["ok"] is True
    skill_id = res1["skill"]["id"]
    assert res1["skill"]["title"] == "단위테스트 스킬"

    # 2. List skills
    res2 = flowi_learning.list_skills(mock_req)
    assert any(s["id"] == skill_id for s in res2["skills"])

    # 3. Update skill
    update_body = flowi_learning.SkillUpdateReq(
        skill_id=skill_id,
        title="단위테스트 스킬 (수정됨)",
        procedure="수정된 절차 내용 및 가이드라인 규칙 상세",
        shared=True,
    )
    res3 = flowi_learning.update_skill_admin(mock_req, update_body)
    assert res3["ok"] is True
    assert res3["skill"]["title"] == "단위테스트 스킬 (수정됨)"

    # 4. List conversations
    res4 = flowi_learning.list_conversations(mock_req, limit=10)
    assert "conversations" in res4
    assert isinstance(res4["conversations"], list)

    # 5. Delete skill
    delete_body = flowi_learning.SkillDeleteReq(skill_id=skill_id)
    res5 = flowi_learning.delete_skill_admin(mock_req, delete_body)
    assert res5["ok"] is True
