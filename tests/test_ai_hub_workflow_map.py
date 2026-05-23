from __future__ import annotations

import sys
from pathlib import Path

_FLOW_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _FLOW_ROOT / "backend"
for p in (_BACKEND, _FLOW_ROOT):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)


def test_ai_hub_workflow_map_links_tools_to_knowledge(monkeypatch):
    from core import ai_hub_workflow_map, tool_registry
    from routers import ai_hub

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

    focused = ai_hub.workflow_map(days=30, limit=10, reference_limit=80, focus_tag="knob")
    focused_nodes = {node["id"] for node in focused["nodes"]}
    assert "tool:find_lots_by_knob_value" in focused_nodes
    assert "tool:filebrowser" not in focused_nodes

    n8n = ai_hub.workflow_map_export(format="n8n", days=30, limit=10, reference_limit=80, focus_tag="")
    assert n8n["format"] == "n8n"
    workflow = n8n["workflow"]
    assert workflow["name"] == "Flow AI Hub workflow map"
    assert any(node["id"] == "tool:filebrowser" for node in workflow["nodes"])
    assert workflow["connections"]["stage:policy"]["main"][0][0]["node"] == "stage:execute"

    obsidian = ai_hub.workflow_map_export(format="obsidian", days=30, limit=10, reference_limit=80, focus_tag="")
    assert obsidian["format"] == "obsidian"
    assert any(row["path"] == "Flow AI Hub Workflow Map.md" for row in obsidian["files"])
    filebrowser_note = next(row for row in obsidian["files"] if row["path"] == "nodes/tool-filebrowser.md")
    assert "# FileBrowser" in filebrowser_note["body"]
    assert "[[wiki-filebrowser-schema-manual|filebrowser_schema_manual]]" in filebrowser_note["body"]
