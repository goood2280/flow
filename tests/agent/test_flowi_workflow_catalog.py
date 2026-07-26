from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for p in (BACKEND, ROOT):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

from core import flowi_workflow_catalog as catalog  # noqa: E402


def test_default_flowi_workflow_catalog_shape():
    workflows = catalog.default_workflows()

    assert len(workflows) == catalog.DEFAULT_TARGET_COUNT
    assert all(row["id"].startswith("wf_") for row in workflows)
    assert all(row["examples"] for row in workflows)
    assert all(row["source_roles"] for row in workflows)
    assert {"split_base", "fab_db", "step_matching", "rulebook"}.issubset(
        {role for row in workflows for role in row["source_roles"]}
    )
    first_examples = {row["id"]: row["examples"][0] for row in workflows[:10]}
    assert first_examples == {
        "wf_split_table_root_lot_knob_custom_set": "A1001 1.0 STI Split(or Knob) 보여줘",
        "wf_split_table_root_lot": "A1001 스플릿테이블 보여줘",
        "wf_fab_current_location": "A1001 #3 지금 어디에 있어?",
        "wf_rulebook_knob_rules": "1.6.0 LDD Knob 어떻게 룰 구성되어있어?",
        "wf_step_id_desc_lookup": "AA100250는 무슨 step이야?",
        "wf_split_raw_data_download": "PRODA MASK_1.0 STI raw data 공유해줘",
        "wf_leading_lot_by_knob_value": "PRODA에서 1.0 STI가 PPID_1인 leading lot이 뭐야?",
        "wf_item_trend_chart_with_optional_knob_color": "Inline 15.0 M2의 trend를 그려줘",
        "wf_source_trend_chart_generic": "ET VTH trend 그려줘",
        "wf_inline_et_corr_chart": "Inline 15.0 M2랑 ET VTH Corr. Chart 그려줘",
    }
    custom_set = next(row for row in workflows if row["id"] == "wf_split_table_root_lot_knob_custom_set")
    assert "A1001 1.0 STI Split(or Knob) 보여줘" in custom_set["examples"]
    assert custom_set["question_template"] == "{root_lot_id} {knob_name} Split(or Knob) 보여줘"
    assert custom_set["orchestration"][0] == "root_lot_id와 knob_name 후보를 분리한다."
    rulebook = next(row for row in workflows if row["id"] == "wf_rulebook_knob_rules")
    assert rulebook["examples"][0] == "1.6.0 LDD Knob 어떻게 룰 구성되어있어?"
    assert rulebook["steps"][0] == "ppid_knob.csv에서 feature_name이 knob_name과 같은 row를 찾는다."


def _example_prompt(workflow: dict) -> str:
    prompt = (workflow.get("examples") or [""])[0]
    slot_examples = {
        "product": "PRODA",
        "root_lot_id": "A1001",
        "wafer_id": "#3",
        "knob_name": "1.0 STI",
        "knob_name_2": "1.1 STI",
        "step_id": "AA100090",
        "function_step": "SD_EPI",
        "measurement_term": "CA BCD",
        "item_id": "CA_BCD",
        "inline_item": "CA_BCD",
        "et_item": "PCCB_CHAIN",
        "source_type": "INLINE",
        "metric_name": "CA_BCD",
        "ppid": "PPID_03_0",
        "fab_lot_id": "F1001",
        "split_set": "SET_A",
        "columns": "root_lot_id wafer_id",
        "chart_session_id": "chart_demo",
    }
    for slot in workflow.get("slots") or []:
        name = str((slot or {}).get("name") or "")
        if name and slot.get("example"):
            slot_examples[name] = str(slot.get("example"))
    for key, value in slot_examples.items():
        prompt = prompt.replace("{" + key + "}", value)
    return prompt.replace("{source_a}", "Inline").replace("{source_b}", "ET").replace("{item_a}", "CA_BCD").replace("{item_b}", "PCCB_CHAIN")


def test_default_flowi_workflow_examples_are_matchable(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "RUNTIME_CATALOG_FILE", tmp_path / "flowi_workflows.json")
    monkeypatch.setattr(catalog, "CHANGE_LOG_FILE", tmp_path / "flowi_workflows.changes.jsonl")

    misses = []
    for workflow in catalog.default_workflows():
        prompt = _example_prompt(workflow)
        matches = catalog.match_workflows(prompt, limit=8)
        if not any(row["id"] == workflow["id"] for row in matches):
            misses.append((workflow["id"], prompt, [row["id"] for row in matches]))

    assert misses == []


@pytest.mark.parametrize(
    ("expected_id", "prompts"),
    [
        ("wf_split_table_root_lot", [
            "A1001 스플릿테이블 보여줘",
            "A1002 split table 보여줘",
            "A1003 knob 테이블 보여줘",
        ]),
        ("wf_split_table_root_lot_knob_custom_set", [
            "A1001 1.0 STI Split(or Knob) 보여줘",
            "A1002 1.0 STI knob 보여줘",
            "A1003 1.0 STI Split 보여줘",
        ]),
        ("wf_fab_current_location", [
            "A1001 #3 지금 어디에 있어?",
            "A1002 wafer 7 current location",
            "A1003 #12 현재 FAB 위치 알려줘",
        ]),
        ("wf_rulebook_knob_rules", [
            "1.6.0 LDD Knob 어떻게 룰 구성되어있어?",
            "1.0 STI knob rulebook 보여줘",
            "2.0 PC Knob 룰 구성 알려줘",
        ]),
        ("wf_step_id_desc_lookup", [
            "AA100250는 무슨 step이야?",
            "AA100251 step_desc 알려줘",
            "AA100252 function step 뭐야?",
        ]),
        ("wf_split_raw_data_download", [
            "PRODA MASK_1.0 STI raw data 공유해줘",
            "PRODA A1001 #3 MASK_1.0 STI raw data csv",
            "PRODB 1.0 STI raw data 다운로드",
        ]),
        ("wf_leading_lot_by_knob_value", [
            "PRODA에서 1.0 STI가 PPID_1인 leading lot이 뭐야?",
            "PRODB 특정 knob 값 PPID_2 leading lot 찾아줘",
            "PRODA knob 1.0 STI value PC인 리딩랏 뭐야?",
        ]),
        ("wf_item_trend_chart_with_optional_knob_color", [
            "Inline 15.0 M2의 trend를 그려줘",
            "INLINE CA_BCD trend chart",
            "15.0 M2 trend를 1.0 STI Knob으로 컬러링해줘",
        ]),
        ("wf_source_trend_chart_generic", [
            "ET VTH trend 그려줘",
            "FAB step progress trend chart",
            "VM VMIN 추이 보여줘",
        ]),
        ("wf_inline_et_corr_chart", [
            "Inline 15.0 M2랑 ET VTH Corr. Chart 그려줘",
            "Inline CA_BCD와 ET PCCB_CHAIN 상관 차트",
            "INLINE CD ET VTH scatter R2 fitting line",
        ]),
        ("wf_chart_knob_coloring_followup", [
            "chart_demo chart 1.0 STI knob coloring",
            "방금 차트 1.0 STI Knob으로 컬러링해줘",
            "위 chart raw data에 1.0 STI join해서 다시 그려줘",
        ]),
        ("wf_chart_raw_data_followup", [
            "chart_demo chart raw data download",
            "방금 차트 raw data 줘",
            "이 차트 raw data csv로 내려줘",
        ]),
        ("wf_chart_raw_data_provenance_followup", [
            "chart_demo chart raw data SQL explain",
            "이 chart raw data 어떻게 뽑았어?",
            "방금 차트 어느 DB Files SQL로 뽑았어?",
        ]),
    ],
)
def test_default_flowi_workflow_user_scenarios_match_variations(expected_id, prompts, tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "RUNTIME_CATALOG_FILE", tmp_path / "flowi_workflows.json")
    monkeypatch.setattr(catalog, "CHANGE_LOG_FILE", tmp_path / "flowi_workflows.changes.jsonl")

    misses = []
    for prompt in prompts:
        matches = catalog.match_workflows(prompt, limit=3)
        if not any(row["id"] == expected_id for row in matches):
            misses.append((prompt, [row["id"] for row in matches]))

    assert misses == []


def test_ensure_runtime_catalog_merges_defaults_without_overwriting(tmp_path, monkeypatch):
    runtime_file = tmp_path / "flowi_workflows.json"
    change_log = tmp_path / "flowi_workflows.changes.jsonl"
    monkeypatch.setattr(catalog, "RUNTIME_CATALOG_FILE", runtime_file)
    monkeypatch.setattr(catalog, "CHANGE_LOG_FILE", change_log)

    custom = {
        "version": 1,
        "workflows": [
            {
                "id": "wf_custom_inline_review",
                "title": "Custom Inline review",
                "unit_ai": "dashboard_agent",
                "action": "plot_item_trend",
                "examples": ["{source_type} {item_id} custom"],
                "trigger_terms": ["custom inline"],
                "source_roles": ["inline_db"],
                "slots": [{"name": "item_id", "type": "item", "required": True}],
                "steps": ["custom step preserved"],
            }
        ],
    }
    runtime_file.write_text(json.dumps(custom, ensure_ascii=False), encoding="utf-8")

    out = catalog.ensure_runtime_catalog(actor="test")
    ids = {row["id"] for row in out["workflows"]}

    assert "wf_custom_inline_review" in ids
    assert "wf_split_table_root_lot" in ids
    preserved = next(row for row in out["workflows"] if row["id"] == "wf_custom_inline_review")
    assert preserved["steps"] == ["custom step preserved"]


def test_ensure_runtime_catalog_refreshes_default_seed_workflows(tmp_path, monkeypatch):
    runtime_file = tmp_path / "flowi_workflows.json"
    change_log = tmp_path / "flowi_workflows.changes.jsonl"
    monkeypatch.setattr(catalog, "RUNTIME_CATALOG_FILE", runtime_file)
    monkeypatch.setattr(catalog, "CHANGE_LOG_FILE", change_log)

    stale_default = {
        "id": "wf_rulebook_knob_rules",
        "title": "Knob rulebook 구성 조회",
        "unit_ai": "ppid_knob",
        "action": "query_knob_rulebook",
        "examples": ["{knob_name} Knob 어떻게 룰 구성되어있어?"],
        "trigger_terms": ["룰", "rulebook", "knob"],
        "source_roles": ["rulebook"],
        "slots": [{"name": "knob_name", "type": "knob", "required": True, "example": "1.6.0 LDD"}],
        "steps": ["old default step"],
        "updated_by": "default_seed",
    }
    runtime_file.write_text(json.dumps({"version": 1, "workflows": [stale_default]}, ensure_ascii=False), encoding="utf-8")

    out = catalog.ensure_runtime_catalog(actor="test")

    refreshed = next(row for row in out["workflows"] if row["id"] == "wf_rulebook_knob_rules")
    assert refreshed["examples"][0] == "1.6.0 LDD Knob 어떻게 룰 구성되어있어?"
    assert refreshed["steps"][0] == "ppid_knob.csv에서 feature_name이 knob_name과 같은 row를 찾는다."


@pytest.mark.parametrize(
    ("prompt", "expected_id", "unit_ai", "roles"),
    [
        ("A1001 스플릿테이블 보여줘", "wf_split_table_root_lot", "splittable", {"split_base"}),
        ("A1001 1.0 STI Split(or Knob) 보여줘", "wf_split_table_root_lot_knob_custom_set", "splittable", {"split_base", "rulebook"}),
        ("A1001 #3 지금 어디에 있어?", "wf_fab_current_location", "fab_reference", {"fab_db", "step_matching"}),
        ("1.6.0 LDD Knob 어떻게 룰 구성되어있어?", "wf_rulebook_knob_rules", "ppid_knob", {"rulebook"}),
        ("AA100250는 무슨 step이야?", "wf_step_id_desc_lookup", "step_lookup", {"step_matching"}),
        ("PRODA MASK_1.0 STI raw data 공유해줘", "wf_split_raw_data_download", "filebrowser_ai_sql", {"split_base"}),
        ("PRODA에서 1.0 STI가 PPID_1인 leading lot이 뭐야?", "wf_leading_lot_by_knob_value", "home_sql_join_dashboard", {"split_base", "fab_db"}),
        ("Inline 15.0 M2의 trend를 그려줘", "wf_item_trend_chart_with_optional_knob_color", "dashboard_agent", {"split_base"}),
        ("ET VTH trend 그려줘", "wf_source_trend_chart_generic", "dashboard_agent", {"split_base"}),
        ("Inline 특정값이랑 ET 특정값이랑 Corr. Chart 그려줘", "wf_inline_et_corr_chart", "home_sql_join_dashboard", {"split_base"}),
        ("PRODA A1001 CA BCD 값 몇이야", "wf_semantic_measurement_value_lookup", "filebrowser_ai_sql", {"inline_db", "et_db"}),
        ("chart_demo chart 1.0 STI knob coloring", "wf_chart_knob_coloring_followup", "dashboard_agent", {"split_base"}),
        ("chart_demo chart raw data download", "wf_chart_raw_data_followup", "dashboard_agent", {"inline_db", "et_db"}),
        ("chart_demo chart raw data SQL explain", "wf_chart_raw_data_provenance_followup", "dashboard_agent", {"inline_db", "et_db"}),
    ],
)
def test_flowi_workflow_scenario_matching(prompt, expected_id, unit_ai, roles, tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "RUNTIME_CATALOG_FILE", tmp_path / "flowi_workflows.json")
    monkeypatch.setattr(catalog, "CHANGE_LOG_FILE", tmp_path / "flowi_workflows.changes.jsonl")

    match = catalog.match_workflows(prompt, limit=1)[0]

    assert match["id"] == expected_id
    assert match["unit_ai"] == unit_ai
    assert roles.issubset(set(match["source_roles"]))


def test_flowi_workflow_draft_shapes_new_requests():
    draft = catalog.draft_workflow("PRODA에서 특정 knob 값인 leading lot을 찾고 리스트 다운로드 제공")

    assert draft["id"].startswith("wf_")
    assert draft["unit_ai"] in catalog.KNOWN_UNIT_AIS
    assert draft["slots"]
    assert draft["source_roles"]
    assert draft["question_template"]
    assert draft["orchestration"]


def test_flowi_workflow_few_shots_include_templates_and_orchestration(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "RUNTIME_CATALOG_FILE", tmp_path / "flowi_workflows.json")
    monkeypatch.setattr(catalog, "CHANGE_LOG_FILE", tmp_path / "flowi_workflows.changes.jsonl")

    rows = catalog.workflow_few_shots(limit=3)
    custom_set = next(row for row in rows if row["workflow_id"] == "wf_split_table_root_lot_knob_custom_set")

    assert custom_set["question_template"] == "{root_lot_id} {knob_name} Split(or Knob) 보여줘"
    assert custom_set["orchestration"][0] == "root_lot_id와 knob_name 후보를 분리한다."
    assert custom_set["prompt"] == "A1001 1.0 STI Split(or Knob) 보여줘"
