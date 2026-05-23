from __future__ import annotations

import sys
from pathlib import Path

_FLOW_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _FLOW_ROOT / "backend"
for p in (_BACKEND, _FLOW_ROOT):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)


def test_ai_hub_workflow_map_links_tools_to_knowledge(monkeypatch, tmp_path):
    from core import ai_hub_workflow_map, flowi_workflow_templates as wf_templates, tool_registry

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
    monkeypatch.setattr(wf_templates, "_DIR", tmp_path)

    out = ai_hub_workflow_map.build_workflow_map(days=30, limit=10)

    assert out["ok"] is True
    assert out["counts"]["tools_visible"] == 2
    assert out["counts"]["tools_disabled_visible"] == 1
    nodes = {node["id"]: node for node in out["nodes"]}
    assert nodes["stage:trigger"]["type"] == "stage"
    assert nodes["tool:filebrowser"]["stage"] == "execute"
    assert nodes["wiki:filebrowser_schema_manual"]["stage"] == "evidence"
    assert nodes["relation:FAB.current_progress"]["type"] == "relation"
    edges = {(edge["from"], edge["to"], edge["label"]) for edge in out["edges"]}
    assert ("stage:policy", "tool:filebrowser", "enabled") in edges
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
    assert workflow["connections"]["stage:policy"]["main"][0][0]["node"] == "stage:execute"

    obsidian = ai_hub_workflow_map.export_workflow_map(export_format="obsidian", days=30, limit=10, reference_limit=80, focus_tag="")
    assert obsidian["format"] == "obsidian"
    assert any(row["path"] == "Flow AI Hub Workflow Map.md" for row in obsidian["files"])
    filebrowser_note = next(row for row in obsidian["files"] if row["path"] == "nodes/tool-filebrowser.md")
    assert "# FileBrowser" in filebrowser_note["body"]
    assert "[[wiki-filebrowser-schema-manual|filebrowser_schema_manual]]" in filebrowser_note["body"]


def test_ai_hub_workflow_map_links_workflow_templates_to_step_tools(monkeypatch, tmp_path):
    from core import ai_hub_workflow_map, flowi_workflow_templates as wf_templates, tool_registry

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
    monkeypatch.setattr(wf_templates, "_DIR", tmp_path)

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
    assert nodes["workflow:ops_knob_lotwf_review"]["actions"][0]["endpoint"] == "/api/agent/workflows/execute"
    assert nodes["workflow:ops_knob_lotwf_review"]["actions"][0]["body"]["dry_run"] is True
    assert nodes["workflow:personal_lot_step"]["shared"] is False
    assert "workflow:other_private" not in nodes
    edges = {(edge["from"], edge["to"], edge["label"], edge["kind"]) for edge in out["edges"]}
    assert ("stage:trigger", "workflow:ops_knob_lotwf_review", "template", "workflow") in edges
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

    obsidian = ai_hub_workflow_map.export_workflow_map(
        export_format="obsidian",
        username="operator",
        days=30,
        limit=10,
        reference_limit=80,
    )
    assert any(row["path"] == "nodes/workflow-ops-knob-lotwf-review.md" for row in obsidian["files"])
    index_note = next(row for row in obsidian["files"] if row["path"] == "Flow AI Hub Workflow Map.md")
    assert "## Workflows" in index_note["body"]
