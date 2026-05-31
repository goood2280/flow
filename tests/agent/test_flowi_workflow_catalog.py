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
    assert all("A1001" not in " ".join(row["examples"]) for row in workflows)
    assert {"split_base", "fab_db", "step_matching", "rulebook"}.issubset(
        {role for row in workflows for role in row["source_roles"]}
    )
    custom_set = next(row for row in workflows if row["id"] == "wf_split_table_root_lot_knob_custom_set")
    assert "{root_lot_id} {knob_name} Split(or Knob) 보여줘" in custom_set["examples"]


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
