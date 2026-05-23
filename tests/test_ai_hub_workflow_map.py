from __future__ import annotations

import json
import sys
from pathlib import Path

_FLOW_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _FLOW_ROOT / "backend"
for p in (_BACKEND, _FLOW_ROOT):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)


def test_ai_hub_workflow_map_links_tools_to_knowledge(monkeypatch, tmp_path):
    from core import ai_hub_deep_eval, ai_hub_workflow_map, flowi_workflow_templates as wf_templates, tool_registry

    def fake_list_tools(include_stats=True, days=30):
        assert include_stats is True
        return [
            {
                "kind": "unit_ai",
                "name": "filebrowser",
                "title": "FileBrowser",
                "description": "raw data preview",
                "enabled": True,
                "tags": ["filebrowser", "read"],
                "count_30d": 3,
                "user_count_30d": 2,
                "last_run": "2026-05-24T00:00:00+00:00",
                "knowledge_refs": {
                    "wiki_doc_ids": ["filebrowser_schema_manual"],
                    "relation_ids": ["FAB.current_progress"],
                    "column_catalog_keys": ["FAB.current_progress.step_id"],
                    "feature_md": "docs/features/filebrowser.md",
                },
            },
            {
                "kind": "function",
                "name": "find_lots_by_knob_value",
                "title": "Knob Lot Finder",
                "description": "find lot_wf by knob",
                "enabled": False,
                "tags": ["knob", "splittable"],
                "count_30d": 0,
                "user_count_30d": 0,
                "knowledge_refs": {
                    "wiki_doc_ids": ["proda_sort_knob_split_rule"],
                    "relation_ids": ["ML_TABLE_PRODA"],
                },
            },
        ]

    monkeypatch.setattr(tool_registry, "list_tools", fake_list_tools)
    monkeypatch.setattr(ai_hub_deep_eval, "load_latest_report", _passing_deep_eval)
    monkeypatch.setattr(wf_templates, "_DIR", tmp_path)
    monkeypatch.setattr(ai_hub_workflow_map.audit, "ACTIVITY_LOG", tmp_path / "activity.jsonl")

    out = ai_hub_workflow_map.build_workflow_map(days=30, limit=10)

    assert out["ok"] is True
    assert out["counts"]["tools_visible"] == 2
    assert out["counts"]["tools_disabled_visible"] == 1
    nodes = {node["id"]: node for node in out["nodes"]}
    assert nodes["stage:trigger"]["type"] == "stage"
    assert nodes["deep_eval:latest"]["type"] == "deep_eval"
    assert nodes["deep_eval:latest"]["metrics"]["status"] == "pass"
    assert nodes["deep_eval:latest"]["actions"][0]["endpoint"] == "/api/ai-hub/deep-eval-report/run"
    assert nodes["deep_eval:latest"]["actions"][0]["body"] == {"cleanup_knowledge": False, "min_cases": 80}
    assert nodes["tool:filebrowser"]["stage"] == "execute"
    assert nodes["wiki:filebrowser_schema_manual"]["stage"] == "evidence"
    assert nodes["relation:FAB.current_progress"]["type"] == "relation"
    edges = {(edge["from"], edge["to"], edge["label"]) for edge in out["edges"]}
    assert ("stage:policy", "tool:filebrowser", "enabled") in edges
    assert ("stage:evidence", "deep_eval:latest", "verify") in edges
    assert ("deep_eval:latest", "stage:improve", "backlog") in edges
    assert ("stage:policy", "tool:find_lots_by_knob_value", "disabled") in edges
    assert ("tool:filebrowser", "wiki:filebrowser_schema_manual", "wiki") in edges

    focused = ai_hub_workflow_map.build_workflow_map(days=30, limit=10, reference_limit=80, focus_tag="knob")
    focused_nodes = {node["id"] for node in focused["nodes"]}
    assert "tool:find_lots_by_knob_value" in focused_nodes
    assert "tool:filebrowser" not in focused_nodes

    n8n = ai_hub_workflow_map.export_workflow_map(export_format="n8n", days=30, limit=10, reference_limit=80, focus_tag="")
    assert n8n["format"] == "n8n"
    workflow = n8n["workflow"]
    assert workflow["name"] == "Flow AI Hub workflow map"
    assert any(node["id"] == "tool:filebrowser" for node in workflow["nodes"])
    assert any(node["id"] == "deep_eval:latest" for node in workflow["nodes"])
    assert workflow["connections"]["stage:policy"]["main"][0][0]["node"] == "stage:execute"

    obsidian = ai_hub_workflow_map.export_workflow_map(export_format="obsidian", days=30, limit=10, reference_limit=80, focus_tag="")
    assert obsidian["format"] == "obsidian"
    assert any(row["path"] == "Flow AI Hub Workflow Map.md" for row in obsidian["files"])
    deep_eval_note = next(row for row in obsidian["files"] if row["path"] == "nodes/deep-eval-latest.md")
    assert "## Agent Deep Eval" in deep_eval_note["body"]
    filebrowser_note = next(row for row in obsidian["files"] if row["path"] == "nodes/tool-filebrowser.md")
    assert "# FileBrowser" in filebrowser_note["body"]
    assert "[[wiki-filebrowser-schema-manual|filebrowser_schema_manual]]" in filebrowser_note["body"]


def test_ai_hub_workflow_map_links_workflow_templates_to_step_tools(monkeypatch, tmp_path):
    from core import ai_hub_deep_eval, ai_hub_workflow_map, flowi_workflow_templates as wf_templates, tool_registry

    def fake_list_tools(include_stats=True, days=30):
        assert include_stats is True
        return [
            {
                "kind": "unit_ai",
                "name": "filebrowser",
                "title": "FileBrowser",
                "description": "raw data preview",
                "enabled": True,
                "tags": ["filebrowser", "raw_data", "lot"],
                "count_30d": 3,
                "user_count_30d": 2,
                "knowledge_refs": {
                    "wiki_doc_ids": ["filebrowser_schema_manual"],
                    "relation_ids": ["FAB.current_progress"],
                },
            },
            {
                "kind": "unit_ai",
                "name": "splittable",
                "title": "Split Table",
                "description": "knob and lot_wf analysis",
                "enabled": True,
                "tags": ["splittable", "knob", "lot_wf"],
                "count_30d": 1,
                "user_count_30d": 1,
                "knowledge_refs": {
                    "wiki_doc_ids": ["proda_sort_knob_split_rule"],
                    "relation_ids": ["ML_TABLE_PRODA"],
                },
            },
            {
                "kind": "unit_ai",
                "name": "tracker",
                "title": "Tracker",
                "description": "issue lookup",
                "enabled": True,
                "tags": ["tracker", "lot"],
                "count_30d": 0,
                "user_count_30d": 0,
                "knowledge_refs": {"feature_md": "docs/features/tracker.md"},
            },
        ]

    monkeypatch.setattr(tool_registry, "list_tools", fake_list_tools)
    monkeypatch.setattr(ai_hub_deep_eval, "load_latest_report", _passing_deep_eval)
    monkeypatch.setattr(wf_templates, "_DIR", tmp_path)
    activity_log = tmp_path / "activity.jsonl"
    activity_log.write_text(
        json.dumps({
            "timestamp": "2099-01-01T00:00:00+00:00",
            "username": "operator",
            "action": "ai_hub_run:workflow:ops_knob_lotwf_review",
            "tab": "ai_hub",
            "detail": json.dumps({
                "workflow": "ops_knob_lotwf_review",
                "title": "KNOB 기반 lot_wf 영향 확인",
                "dry_run": True,
                "steps": 2,
                "confirm_required": False,
                "statuses": {"dry_run": 2},
            }, ensure_ascii=False),
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ai_hub_workflow_map.audit, "ACTIVITY_LOG", activity_log)

    wf_templates.save_template({
        "key": "ops_knob_lotwf_review",
        "title": "KNOB 기반 lot_wf 영향 확인",
        "trigger": {
            "intent_in": ["knob_analysis"],
            "prompt_contains": ["knob"],
            "slots_required": ["product", "knobs"],
        },
        "steps": [
            {"unit_ai": "splittable", "action": "knob_impact", "bind_slots": ["product", "knobs"]},
            {"unit_ai": "filebrowser", "action": "query", "bind_slots": ["product", "knobs"]},
        ],
        "shared": True,
    }, by="admin", is_admin=True)
    wf_templates.save_template({
        "key": "personal_lot_step",
        "title": "내 LOT step 확인",
        "trigger": {
            "intent_in": ["filebrowser_ai_sql"],
            "prompt_contains": ["step"],
            "slots_required": ["product", "root_lot_ids"],
        },
        "steps": [
            {"unit_ai": "filebrowser", "action": "current_step", "bind_slots": ["product", "root_lot_ids"]},
            {"unit_ai": "tracker", "action": "lookup", "bind_slots": ["product", "root_lot_ids"]},
        ],
    }, by="operator", is_admin=False)
    wf_templates.save_template({
        "key": "other_private",
        "title": "다른 사용자 개인 템플릿",
        "steps": [{"unit_ai": "tracker", "action": "lookup"}],
    }, by="other", is_admin=False)

    out = ai_hub_workflow_map.build_workflow_map(
        username="operator",
        days=30,
        limit=10,
        reference_limit=80,
    )

    assert out["counts"]["workflow_templates_visible"] == 2
    assert out["counts"]["workflow_templates_shared"] == 1
    assert out["counts"]["workflow_templates_personal"] == 1
    assert out["counts"]["workflow_step_edges"] == 4
    nodes = {node["id"]: node for node in out["nodes"]}
    assert nodes["workflow:ops_knob_lotwf_review"]["type"] == "workflow"
    assert nodes["workflow:ops_knob_lotwf_review"]["metrics"]["steps"] == 2
    assert nodes["workflow:ops_knob_lotwf_review"]["metrics"]["run_count"] == 1
    assert nodes["workflow:ops_knob_lotwf_review"]["metrics"]["last_status"] == "dry_run:2"
    assert nodes["workflow:ops_knob_lotwf_review"]["actions"][0]["endpoint"] == "/api/agent/workflows/execute"
    assert nodes["workflow:ops_knob_lotwf_review"]["actions"][0]["body"]["dry_run"] is True
    assert nodes["workflow_step:ops_knob_lotwf_review:1"]["type"] == "workflow_step"
    assert nodes["workflow_step:ops_knob_lotwf_review:1"]["unit_ai"] == "splittable"
    assert nodes["workflow_step:ops_knob_lotwf_review:1"]["action"] == "knob_impact"
    assert nodes["workflow:personal_lot_step"]["shared"] is False
    assert "workflow:other_private" not in nodes
    edges = {(edge["from"], edge["to"], edge["label"], edge["kind"]) for edge in out["edges"]}
    assert ("stage:trigger", "workflow:ops_knob_lotwf_review", "template", "workflow") in edges
    assert ("workflow:ops_knob_lotwf_review", "workflow_step:ops_knob_lotwf_review:1", "step 1", "workflow_step") in edges
    assert ("workflow_step:ops_knob_lotwf_review:1", "tool:splittable", "knob_impact", "uses_tool") in edges
    assert ("workflow:ops_knob_lotwf_review", "tool:splittable", "knob_impact", "workflow_step") in edges
    assert ("workflow:ops_knob_lotwf_review", "tool:filebrowser", "query", "workflow_step") in edges
    assert ("workflow:personal_lot_step", "tool:tracker", "lookup", "workflow_step") in edges

    focused = ai_hub_workflow_map.build_workflow_map(
        username="operator",
        days=30,
        limit=10,
        reference_limit=80,
        focus_tag="knob",
    )
    focused_nodes = {node["id"] for node in focused["nodes"]}
    assert "workflow:ops_knob_lotwf_review" in focused_nodes
    assert "workflow:personal_lot_step" not in focused_nodes
    assert "tool:filebrowser" in focused_nodes

    n8n = ai_hub_workflow_map.export_workflow_map(
        export_format="n8n",
        username="operator",
        days=30,
        limit=10,
        reference_limit=80,
    )
    assert any(node["id"] == "workflow:ops_knob_lotwf_review" for node in n8n["workflow"]["nodes"])
    assert any(node["id"] == "workflow_step:ops_knob_lotwf_review:1" for node in n8n["workflow"]["nodes"])

    obsidian = ai_hub_workflow_map.export_workflow_map(
        export_format="obsidian",
        username="operator",
        days=30,
        limit=10,
        reference_limit=80,
    )
    workflow_note = next(row for row in obsidian["files"] if row["path"] == "nodes/workflow-ops-knob-lotwf-review.md")
    assert "`1` `splittable`.`knob_impact`" in workflow_note["body"]
    assert "`2` `filebrowser`.`query`" in workflow_note["body"]
    step_note = next(row for row in obsidian["files"] if row["path"] == "nodes/workflow-step-ops-knob-lotwf-review-1.md")
    assert "## Workflow Step" in step_note["body"]
    index_note = next(row for row in obsidian["files"] if row["path"] == "Flow AI Hub Workflow Map.md")
    assert "## Workflows" in index_note["body"]


def test_ai_hub_workflow_map_warns_broken_workflow_templates(monkeypatch, tmp_path):
    from core import ai_hub_deep_eval, ai_hub_workflow_map, flowi_workflow_templates as wf_templates, tool_registry

    def fake_list_tools(include_stats=True, days=30):
        assert include_stats is True
        return [{
            "kind": "unit_ai",
            "name": "filebrowser",
            "title": "FileBrowser",
            "description": "raw data preview",
            "enabled": True,
            "tags": ["filebrowser"],
            "count_30d": 0,
            "user_count_30d": 0,
            "knowledge_refs": {"wiki_doc_ids": ["filebrowser_schema_manual"]},
        }]

    monkeypatch.setattr(tool_registry, "list_tools", fake_list_tools)
    monkeypatch.setattr(ai_hub_deep_eval, "load_latest_report", _passing_deep_eval)
    monkeypatch.setattr(wf_templates, "_DIR", tmp_path)
    monkeypatch.setattr(ai_hub_workflow_map.audit, "ACTIVITY_LOG", tmp_path / "activity.jsonl")

    wf_templates.save_template({
        "key": "empty_workflow",
        "title": "비어 있는 workflow",
        "shared": True,
    }, by="admin", is_admin=True)
    wf_templates.save_template({
        "key": "incomplete_step",
        "title": "action 누락 workflow",
        "steps": [{"unit_ai": "filebrowser", "action": ""}],
        "shared": True,
    }, by="admin", is_admin=True)
    wf_templates.save_template({
        "key": "missing_tool",
        "title": "미등록 unit_ai workflow",
        "steps": [{"unit_ai": "ghost_unit", "action": "lookup"}],
        "shared": True,
    }, by="admin", is_admin=True)

    out = ai_hub_workflow_map.build_workflow_map(username="operator", days=30, limit=10)

    assert out["counts"]["workflow_empty_templates"] == 1
    assert out["counts"]["workflow_incomplete_steps"] == 1
    assert out["counts"]["workflow_missing_tools"] == 1
    warnings = {row["key"]: row for row in out["warnings"]}
    assert warnings["workflow_empty_templates"]["items"] == ["empty_workflow"]
    assert warnings["workflow_incomplete_steps"]["items"] == ["incomplete_step#1"]
    assert warnings["workflow_missing_tools"]["items"] == ["ghost_unit"]


def test_ai_hub_workflow_map_warns_failed_deep_eval(monkeypatch, tmp_path):
    from core import ai_hub_deep_eval, ai_hub_workflow_map, flowi_workflow_templates as wf_templates, tool_registry

    monkeypatch.setattr(tool_registry, "list_tools", lambda include_stats=True, days=30: [])
    monkeypatch.setattr(wf_templates, "_DIR", tmp_path)
    monkeypatch.setattr(ai_hub_workflow_map.audit, "ACTIVITY_LOG", tmp_path / "activity.jsonl")
    monkeypatch.setattr(ai_hub_deep_eval, "load_latest_report", lambda: {
        "ok": True,
        "exists": True,
        "status": "fail",
        "path": "reports/flowi_agent_deep_eval_latest.json",
        "summary": {"passed": 130, "failed": 2, "total": 132},
        "groups": {"sql": {"passed": 16, "failed": 1, "total": 17}},
        "failed_results": [{"name": "sql/raw join/rows", "detail": "bad rows"}],
    })

    out = ai_hub_workflow_map.build_workflow_map(days=30, limit=10)

    nodes = {node["id"]: node for node in out["nodes"]}
    assert nodes["deep_eval:latest"]["tone"] == "bad"
    assert nodes["deep_eval:latest"]["metrics"]["failed"] == 2
    assert out["counts"]["deep_eval_failed"] == 2
    warnings = {row["key"]: row for row in out["warnings"]}
    assert warnings["deep_eval_failed"]["items"] == ["sql/raw join/rows"]


def _passing_deep_eval():
    return {
        "ok": True,
        "exists": True,
        "status": "pass",
        "path": "reports/flowi_agent_deep_eval_latest.json",
        "generated_at": "2026-05-24T01:30:00+00:00",
        "summary": {"passed": 131, "failed": 0, "total": 131},
        "groups": {
            "semantic": {"passed": 108, "failed": 0, "total": 108},
            "knowledge": {"passed": 5, "failed": 0, "total": 5},
            "sql": {"passed": 17, "failed": 0, "total": 17},
        },
        "doc_id": "agent_deep_eval_semiconductor_terms",
        "failed_results": [],
        "age_seconds": 60,
    }
