"""AI Hub workflow map.

Builds a read-only n8n/Obsidian-style graph from the existing tool catalog
and saved workflow templates. The graph is intentionally derived data: no
extra runtime store, no writes.
"""
from __future__ import annotations

import io
import json
import zipfile
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from core import ai_hub_deep_eval
from core import audit
from core import flowi_workflow_templates as wf_templates
from core import tool_registry


STAGES: list[dict[str, str]] = [
    {
        "id": "trigger",
        "title": "Prompt / Signal",
        "detail": "Home prompt, Agent runtime, saved skill, workflow template",
    },
    {
        "id": "policy",
        "title": "Policy Gate",
        "detail": "enabled 상태, read-only/write approval, raw-data write guard",
    },
    {
        "id": "execute",
        "title": "Unit / Function",
        "detail": "Unit AI dispatcher 또는 Flow-i function-call 실행",
    },
    {
        "id": "evidence",
        "title": "Wiki / Schema",
        "detail": "Agent Wiki, relation_id, column catalog, feature doc 근거",
    },
    {
        "id": "improve",
        "title": "Improve",
        "detail": "feedback, semantic proposal, workflow template, skill candidate",
    },
]


def build_workflow_map(
    *,
    username: str = "",
    days: int = 30,
    limit: int = 40,
    reference_limit: int = 160,
    focus_tag: str = "",
) -> dict[str, Any]:
    """Return a visual management graph for AI Hub.

    `limit` applies to visible tools. `reference_limit` caps wiki/schema/doc
    nodes so the UI stays readable even when Unit AI metadata grows.
    """
    days = max(1, min(365, int(days or 30)))
    limit = max(1, min(120, int(limit or 40)))
    reference_limit = max(20, min(400, int(reference_limit or 160)))
    username = str(username or "")
    focus_tag = str(focus_tag or "").strip()

    all_tools = tool_registry.list_tools(include_stats=True, days=days)
    tool_by_name = {
        str(row.get("name") or ""): dict(row)
        for row in all_tools
        if str(row.get("name") or "").strip()
    }
    visible_tools = [
        dict(row) for row in all_tools
        if not focus_tag or focus_tag in (row.get("tags") or [])
    ]
    visible_tools.sort(key=_tool_sort_key)
    visible_tools = visible_tools[:limit]

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen_nodes: set[str] = set()
    seen_edges: set[tuple[str, str, str]] = set()
    ref_count = 0
    tool_node_ids: set[str] = set()
    tool_refs_loaded: set[str] = set()
    tools_without_refs: set[str] = set()
    workflow_missing_tools: set[str] = set()
    workflow_empty_templates: set[str] = set()
    workflow_incomplete_steps: set[str] = set()
    workflow_step_edges = 0

    def add_node(node: dict[str, Any]) -> bool:
        node_id = str(node.get("id") or "")
        if not node_id or node_id in seen_nodes:
            return False
        seen_nodes.add(node_id)
        nodes.append(node)
        return True

    def add_edge(source: str, target: str, label: str = "", kind: str = "flow") -> None:
        if not source or not target:
            return
        key = (source, target, kind + ":" + label)
        if key in seen_edges:
            return
        seen_edges.add(key)
        edges.append({"from": source, "to": target, "label": label, "kind": kind})

    def add_tool_node(tool: dict[str, Any], *, include_refs: bool = True) -> str:
        nonlocal ref_count
        name = str(tool.get("name") or "")
        if not name:
            return ""
        tool_id = f"tool:{name}"
        tags = [str(tag) for tag in (tool.get("tags") or []) if str(tag).strip()]
        refs = tool.get("knowledge_refs") if isinstance(tool.get("knowledge_refs"), dict) else {}
        ref_rows = _reference_rows(refs)
        if not ref_rows:
            tools_without_refs.add(name)

        if tool_id not in tool_node_ids:
            add_node({
                "id": tool_id,
                "type": "tool",
                "stage": "execute",
                "label": str(tool.get("title") or name),
                "detail": str(tool.get("description") or name),
                "tool_name": name,
                "kind": str(tool.get("kind") or ""),
                "enabled": bool(tool.get("enabled")),
                "tone": _tool_tone(tool),
                "tags": tags,
                "metrics": {
                    "count": int(tool.get("count_30d") or 0),
                    "users": int(tool.get("user_count_30d") or 0),
                    "last_run": str(tool.get("last_run") or ""),
                },
            })
            tool_node_ids.add(tool_id)
            add_edge("stage:policy", tool_id, "enabled" if tool.get("enabled") else "disabled", "policy")
            add_edge(tool_id, "stage:improve", "feedback", "improve")

        if include_refs and tool_id not in tool_refs_loaded:
            for ref in ref_rows:
                if ref_count >= reference_limit:
                    break
                if add_node(ref):
                    ref_count += 1
                add_edge(tool_id, ref["id"], ref["edge_label"], "evidence")
            tool_refs_loaded.add(tool_id)
        return tool_id

    for stage in STAGES:
        add_node({
            "id": f"stage:{stage['id']}",
            "type": "stage",
            "stage": stage["id"],
            "label": stage["title"],
            "detail": stage["detail"],
            "tone": "info",
        })
    add_edge("stage:trigger", "stage:policy", "resolve", "stage")
    add_edge("stage:policy", "stage:execute", "route", "stage")
    add_edge("stage:execute", "stage:evidence", "ground", "stage")
    add_edge("stage:evidence", "stage:improve", "learn", "stage")

    deep_eval = ai_hub_deep_eval.load_latest_report()
    deep_eval_node = _deep_eval_node(deep_eval)
    add_node(deep_eval_node)
    add_edge("stage:evidence", deep_eval_node["id"], "verify", "deep_eval")
    add_edge(deep_eval_node["id"], "stage:improve", "backlog", "improve")

    for tool in visible_tools:
        add_tool_node(tool)

    workflow_all_rows = _workflow_rows(username=username, focus_tag="", tool_by_name=tool_by_name)
    workflow_rows = [
        row for row in workflow_all_rows
        if _workflow_matches_focus(row, focus_tag, tool_by_name)
    ]
    workflow_runs_by_key = _workflow_run_summaries(days=days)
    workflow_limit = min(80, max(20, limit))
    visible_workflows = workflow_rows[:workflow_limit]
    for workflow in visible_workflows:
        key = str(workflow.get("key") or "").strip()
        if not key:
            continue
        steps = _workflow_step_summaries(workflow)
        run_summary = workflow_runs_by_key.get(key) or {}
        workflow_id = f"workflow:{key}"
        add_node({
            "id": workflow_id,
            "type": "workflow",
            "stage": "trigger",
            "label": str(workflow.get("title") or key),
            "detail": _workflow_detail(workflow, steps),
            "workflow_key": key,
            "owner": str(workflow.get("owner") or ""),
            "shared": bool(workflow.get("shared")),
            "tone": _workflow_tone(steps, run_summary),
            "tags": _workflow_tags(workflow, steps),
            "metrics": {
                "steps": len(steps),
                "run_count": int(run_summary.get("run_count") or 0),
                "warning_count": int(run_summary.get("warning_count") or 0),
                "last_run": str(run_summary.get("last_run") or ""),
                "last_status": str(run_summary.get("last_status") or ""),
            },
            "steps": steps,
            "actions": _workflow_actions(key),
        })
        add_edge("stage:trigger", workflow_id, "template", "workflow")
        add_edge(workflow_id, "stage:policy", "guardrail", "workflow")
        if not steps:
            workflow_empty_templates.add(key)
            add_edge(workflow_id, "stage:improve", "fill steps", "workflow")
            continue
        for step in steps:
            unit_ai = str(step.get("unit_ai") or "").strip()
            action = str(step.get("action") or "").strip()
            step_index = int(step.get("index") or 0) + 1
            step_id = f"workflow_step:{key}:{step_index}"
            step_has_issue = False
            if not unit_ai or not action:
                workflow_incomplete_steps.add(f"{key}#{step_index}")
                step_has_issue = True
            tool = tool_by_name.get(unit_ai) if unit_ai else None
            if unit_ai and not tool:
                step_has_issue = True
            add_node(_workflow_step_node(workflow, step, step_id, step_has_issue=step_has_issue))
            add_edge(workflow_id, step_id, f"step {step_index}", "workflow_step")
            if not unit_ai:
                add_edge(step_id, "stage:improve", "fill unit_ai", "workflow_step")
                continue
            tool = tool or _missing_tool_row(unit_ai)
            tool_id = add_tool_node(tool)
            if tool.get("_missing"):
                workflow_missing_tools.add(unit_ai)
            if tool_id:
                label = str(step.get("action") or f"step {step.get('index', 0) + 1}")[:56]
                add_edge(step_id, tool_id, label, "uses_tool")
                add_edge(workflow_id, tool_id, label, "workflow_step")
                workflow_step_edges += 1

    counts = {
        "tools_total": len(all_tools),
        "tools_visible": len(tool_node_ids),
        "tools_selected_visible": len(visible_tools),
        "tools_disabled_visible": sum(1 for node in nodes if node.get("type") == "tool" and not node.get("enabled")),
        "tools_without_refs_visible": len(tools_without_refs),
        "workflow_templates_total": len(workflow_all_rows),
        "workflow_templates_visible": len(visible_workflows),
        "workflow_templates_shared": sum(1 for row in visible_workflows if row.get("shared")),
        "workflow_templates_personal": sum(1 for row in visible_workflows if not row.get("shared")),
        "workflow_step_edges": workflow_step_edges,
        "workflow_runs_recent": sum(int(row.get("run_count") or 0) for row in workflow_runs_by_key.values()),
        "workflow_run_warnings": sum(int(row.get("warning_count") or 0) for row in workflow_runs_by_key.values()),
        "workflow_missing_tools": len(workflow_missing_tools),
        "workflow_empty_templates": len(workflow_empty_templates),
        "workflow_incomplete_steps": len(workflow_incomplete_steps),
        "deep_eval_exists": 1 if deep_eval.get("exists") else 0,
        "deep_eval_total": int((deep_eval.get("summary") or {}).get("total") or 0) if isinstance(deep_eval.get("summary"), dict) else 0,
        "deep_eval_failed": int((deep_eval.get("summary") or {}).get("failed") or 0) if isinstance(deep_eval.get("summary"), dict) else 0,
        "nodes": len(nodes),
        "edges": len(edges),
        "reference_nodes": ref_count,
    }
    tag_counts = Counter(
        str(tag)
        for row in all_tools
        for tag in (row.get("tags") or [])
        if str(tag).strip()
    )
    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": days,
        "limit": limit,
        "reference_limit": reference_limit,
        "focus_tag": focus_tag,
        "counts": counts,
        "stages": STAGES,
        "top_tags": [
            {"tag": tag, "count": count}
            for tag, count in tag_counts.most_common(24)
        ],
        "nodes": nodes,
        "edges": edges,
        "warnings": _warnings(
            counts,
            sorted(tools_without_refs),
            sorted(workflow_missing_tools),
            sorted(workflow_empty_templates),
            sorted(workflow_incomplete_steps),
            deep_eval,
        ),
    }


def export_workflow_map(
    *,
    export_format: str = "n8n",
    username: str = "",
    days: int = 30,
    limit: int = 40,
    reference_limit: int = 160,
    focus_tag: str = "",
) -> dict[str, Any]:
    graph = build_workflow_map(
        username=username,
        days=days,
        limit=limit,
        reference_limit=reference_limit,
        focus_tag=focus_tag,
    )
    fmt = str(export_format or "n8n").strip().lower()
    if fmt in {"n8n", "n8n_json", "json"}:
        return _export_n8n(graph)
    if fmt in {"obsidian", "markdown", "md"}:
        return _export_obsidian(graph)
    raise ValueError(f"unsupported export format: {export_format}")


def export_obsidian_zip(export_payload: dict[str, Any]) -> bytes:
    files = export_payload.get("files") if isinstance(export_payload.get("files"), list) else []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for row in files:
            if not isinstance(row, dict):
                continue
            path = _safe_zip_path(str(row.get("path") or ""))
            body = str(row.get("body") or "")
            if path:
                zf.writestr(path, body)
    return buf.getvalue()


def _export_n8n(graph: dict[str, Any]) -> dict[str, Any]:
    """Return an n8n-compatible-ish workflow document.

    The exported nodes are documentation/operation nodes, not executable n8n
    integrations. This preserves Flow's guardrails while making the workflow
    portable for review and external orchestration planning.
    """
    graph_nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    graph_edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
    stage_order = {row["id"]: idx for idx, row in enumerate(STAGES)}
    stage_counts: Counter[str] = Counter()
    nodes: list[dict[str, Any]] = []
    for idx, node in enumerate(graph_nodes):
        node_id = str(node.get("id") or f"node:{idx}")
        stage = str(node.get("stage") or "execute")
        stage_idx = stage_order.get(stage, 0)
        lane_idx = stage_counts[stage]
        stage_counts[stage] += 1
        nodes.append({
            "id": node_id,
            "name": str(node.get("label") or node_id),
            "type": "n8n-nodes-base.stickyNote",
            "typeVersion": 1,
            "position": [stage_idx * 320, lane_idx * 140],
            "parameters": {
                "content": _n8n_node_content(node),
                "height": 110,
                "width": 260,
            },
        })

    connections: dict[str, dict[str, list[list[dict[str, Any]]]]] = {}
    for edge in graph_edges:
        source = str(edge.get("from") or "")
        target = str(edge.get("to") or "")
        if not source or not target:
            continue
        connections.setdefault(source, {"main": [[]]})
        connections[source]["main"][0].append({
            "node": target,
            "type": "main",
            "index": 0,
            "metadata": {
                "label": str(edge.get("label") or ""),
                "kind": str(edge.get("kind") or ""),
            },
        })

    return {
        "ok": True,
        "format": "n8n",
        "filename": "flow-ai-hub-workflow-map.n8n.json",
        "workflow": {
            "name": "Flow AI Hub workflow map",
            "nodes": nodes,
            "connections": connections,
            "settings": {"executionOrder": "v1"},
            "staticData": {
                "source": "flow.ai_hub.workflow_map",
                "generated_at": graph.get("generated_at"),
                "counts": graph.get("counts") or {},
                "focus_tag": graph.get("focus_tag") or "",
            },
        },
    }


def _export_obsidian(graph: dict[str, Any]) -> dict[str, Any]:
    graph_nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    graph_edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
    warnings = graph.get("warnings") if isinstance(graph.get("warnings"), list) else []
    counts = graph.get("counts") if isinstance(graph.get("counts"), dict) else {}
    nodes_by_id = {str(node.get("id") or ""): node for node in graph_nodes}
    slug_by_id = {node_id: _slug(node_id) for node_id in nodes_by_id}
    files: list[dict[str, str]] = []

    index_lines = [
        "# Flow AI Hub Workflow Map",
        "",
        f"- generated_at: `{graph.get('generated_at') or ''}`",
        f"- focus_tag: `{graph.get('focus_tag') or ''}`",
        f"- nodes: `{len(graph_nodes)}`",
        f"- edges: `{len(graph_edges)}`",
        f"- warnings: `{len(warnings)}`",
        "",
    ]
    if warnings:
        index_lines.extend(["", "## Warnings", "", "| key | tone | items | message |", "|---|---|---:|---|"])
        for row in warnings:
            if not isinstance(row, dict):
                continue
            items = row.get("items") if isinstance(row.get("items"), list) else []
            index_lines.append(
                f"| {_md_cell(row.get('key'))} | {_md_cell(row.get('tone'))} | "
                f"{len(items)} | {_md_cell(row.get('message'))} |"
            )
        index_lines.append("")
    index_lines.append("## Stages")
    for stage in STAGES:
        index_lines.append(f"- [[{slug_by_id.get('stage:' + stage['id'], _slug('stage:' + stage['id']))}|{stage['title']}]]")
    index_lines.extend(["", "## Workflows"])
    for node in graph_nodes:
        if node.get("type") == "workflow":
            index_lines.append(f"- [[{slug_by_id.get(str(node.get('id') or ''), '')}|{node.get('label') or node.get('id')}]]")
    index_lines.extend(["", "## Tools"])
    for node in graph_nodes:
        if node.get("type") == "tool":
            index_lines.append(f"- [[{slug_by_id.get(str(node.get('id') or ''), '')}|{node.get('label') or node.get('id')}]]")
    files.append({
        "path": "Flow AI Hub Workflow Map.md",
        "body": "\n".join(index_lines).rstrip() + "\n",
    })

    for node_id, node in nodes_by_id.items():
        inbound = [edge for edge in graph_edges if str(edge.get("to") or "") == node_id]
        outbound = [edge for edge in graph_edges if str(edge.get("from") or "") == node_id]
        body = _obsidian_node_body(node, inbound, outbound, slug_by_id, nodes_by_id)
        files.append({"path": f"nodes/{slug_by_id[node_id]}.md", "body": body})

    return {
        "ok": True,
        "format": "obsidian",
        "filename": "flow-ai-hub-workflow-map.obsidian.json",
        "counts": counts,
        "warnings": warnings,
        "files": files,
        "graph": {
            "nodes": graph_nodes,
            "edges": graph_edges,
        },
    }


def _tool_sort_key(row: dict[str, Any]) -> tuple[int, int, str, str]:
    disabled_rank = 0 if not row.get("enabled") else 1
    count_rank = -int(row.get("count_30d") or 0)
    return (disabled_rank, count_rank, str(row.get("kind") or ""), str(row.get("title") or row.get("name") or ""))


def _n8n_node_content(node: dict[str, Any]) -> str:
    lines = [
        f"## {node.get('label') or node.get('id')}",
        f"- id: `{node.get('id') or ''}`",
        f"- type: `{node.get('type') or ''}`",
        f"- stage: `{node.get('stage') or ''}`",
    ]
    if "enabled" in node:
        lines.append(f"- enabled: `{bool(node.get('enabled'))}`")
    if node.get("type") == "workflow":
        metrics = node.get("metrics") if isinstance(node.get("metrics"), dict) else {}
        lines.append(f"- shared: `{bool(node.get('shared'))}`")
        owner = str(node.get("owner") or "")
        if owner:
            lines.append(f"- owner: `{owner}`")
        lines.append(f"- steps: `{metrics.get('steps') or 0}`")
        if metrics.get("last_run"):
            lines.append(f"- last_run: `{metrics.get('last_run')}`")
        if metrics.get("last_status"):
            lines.append(f"- last_status: `{metrics.get('last_status')}`")
    kind = str(node.get("kind") or "")
    if kind:
        lines.append(f"- kind: `{kind}`")
    tags = [str(tag) for tag in (node.get("tags") or []) if str(tag).strip()]
    if tags:
        lines.append("- tags: " + ", ".join(f"`{tag}`" for tag in tags[:8]))
    detail = str(node.get("detail") or "").strip()
    if detail:
        lines.extend(["", detail[:500]])
    return "\n".join(lines)


def _obsidian_node_body(
    node: dict[str, Any],
    inbound: list[dict[str, Any]],
    outbound: list[dict[str, Any]],
    slug_by_id: dict[str, str],
    nodes_by_id: dict[str, dict[str, Any]],
) -> str:
    node_id = str(node.get("id") or "")
    lines = [
        "---",
        f"id: \"{_yaml_escape(node_id)}\"",
        f"type: \"{_yaml_escape(str(node.get('type') or ''))}\"",
        f"stage: \"{_yaml_escape(str(node.get('stage') or ''))}\"",
        f"tone: \"{_yaml_escape(str(node.get('tone') or ''))}\"",
        "---",
        "",
        f"# {node.get('label') or node_id}",
        "",
        str(node.get("detail") or "").strip(),
        "",
    ]
    if node.get("type") == "tool":
        metrics = node.get("metrics") if isinstance(node.get("metrics"), dict) else {}
        lines.extend([
            "## Tool",
            "",
            f"- tool_name: `{node.get('tool_name') or ''}`",
            f"- kind: `{node.get('kind') or ''}`",
            f"- enabled: `{bool(node.get('enabled'))}`",
            f"- calls: `{metrics.get('count') or 0}`",
            f"- users: `{metrics.get('users') or 0}`",
            "",
        ])
    if node.get("type") == "workflow":
        metrics = node.get("metrics") if isinstance(node.get("metrics"), dict) else {}
        lines.extend([
            "## Workflow",
            "",
            f"- workflow_key: `{node.get('workflow_key') or ''}`",
            f"- owner: `{node.get('owner') or ''}`",
            f"- shared: `{bool(node.get('shared'))}`",
            f"- steps: `{metrics.get('steps') or 0}`",
            f"- run_count: `{metrics.get('run_count') or 0}`",
            f"- warning_count: `{metrics.get('warning_count') or 0}`",
            f"- last_run: `{metrics.get('last_run') or ''}`",
            f"- last_status: `{metrics.get('last_status') or ''}`",
            "",
        ])
        steps = node.get("steps") if isinstance(node.get("steps"), list) else []
        if steps:
            lines.extend(["## Steps", ""])
            for step in steps:
                index = int(step.get("index") or 0) + 1
                unit_ai = str(step.get("unit_ai") or "")
                action = str(step.get("action") or "")
                lines.append(f"- `{index}` `{unit_ai}`.`{action}`")
            lines.append("")
    if node.get("type") == "workflow_step":
        lines.extend([
            "## Workflow Step",
            "",
            f"- workflow_key: `{node.get('workflow_key') or ''}`",
            f"- step_index: `{node.get('step_index') or ''}`",
            f"- unit_ai: `{node.get('unit_ai') or ''}`",
            f"- action: `{node.get('action') or ''}`",
            "",
        ])
    if node.get("type") == "deep_eval":
        metrics = node.get("metrics") if isinstance(node.get("metrics"), dict) else {}
        lines.extend([
            "## Agent Deep Eval",
            "",
            f"- status: `{metrics.get('status') or ''}`",
            f"- passed: `{metrics.get('passed') or 0}`",
            f"- failed: `{metrics.get('failed') or 0}`",
            f"- total: `{metrics.get('total') or 0}`",
            f"- generated_at: `{metrics.get('generated_at') or ''}`",
            f"- path: `{metrics.get('path') or ''}`",
            "",
        ])
    tags = [str(tag) for tag in (node.get("tags") or []) if str(tag).strip()]
    if tags:
        lines.extend(["## Tags", "", *[f"- #{_tag_slug(tag)}" for tag in tags], ""])
    lines.extend(["## Incoming", ""])
    lines.extend(_obsidian_edge_lines(inbound, "from", slug_by_id, nodes_by_id))
    lines.extend(["", "## Outgoing", ""])
    lines.extend(_obsidian_edge_lines(outbound, "to", slug_by_id, nodes_by_id))
    return "\n".join(lines).rstrip() + "\n"


def _obsidian_edge_lines(
    edges: list[dict[str, Any]],
    other_key: str,
    slug_by_id: dict[str, str],
    nodes_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    if not edges:
        return ["- none"]
    lines: list[str] = []
    for edge in edges:
        other_id = str(edge.get(other_key) or "")
        other = nodes_by_id.get(other_id) or {}
        label = str(edge.get("label") or edge.get("kind") or "link")
        title = str(other.get("label") or other_id)
        slug = slug_by_id.get(other_id) or _slug(other_id)
        lines.append(f"- `{label}` [[{slug}|{title}]]")
    return lines


def _slug(value: str) -> str:
    out = []
    for ch in str(value or "").strip().lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in {":", "/", "\\", ".", "_", "-", " "}:
            out.append("-")
    slug = "".join(out).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "node"


def _safe_zip_path(value: str) -> str:
    normalized = str(value or "").replace("\\", "/").lstrip("/")
    parts = [part for part in normalized.split("/") if part and part not in {".", ".."}]
    return "/".join(parts) or "Flow AI Hub Workflow Map.md"


def _tag_slug(value: str) -> str:
    return _slug(value).replace("-", "_")


def _yaml_escape(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"')


def _md_cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ").strip()


def _tool_tone(tool: dict[str, Any]) -> str:
    if tool.get("_missing"):
        return "warn"
    if not tool.get("enabled"):
        return "bad"
    if int(tool.get("count_30d") or 0) > 0:
        return "ok"
    return "neutral"


def _workflow_rows(
    *,
    username: str,
    focus_tag: str,
    tool_by_name: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = [
        row for row in wf_templates.list_templates(username, include_shared=True)
        if isinstance(row, dict) and _workflow_matches_focus(row, focus_tag, tool_by_name)
    ]
    rows.sort(key=_workflow_sort_key)
    return rows


def _workflow_sort_key(row: dict[str, Any]) -> tuple[int, str, str]:
    shared_rank = 0 if row.get("shared") else 1
    updated = str(row.get("updated_at") or row.get("created_at") or "")
    return (shared_rank, updated, str(row.get("title") or row.get("key") or ""))


def _workflow_matches_focus(
    row: dict[str, Any],
    focus_tag: str,
    tool_by_name: dict[str, dict[str, Any]],
) -> bool:
    if not focus_tag:
        return True
    focus = str(focus_tag or "").strip().lower()
    if not focus:
        return True
    haystack = [
        str(row.get("key") or ""),
        str(row.get("title") or ""),
        str(row.get("owner") or ""),
    ]
    trigger = row.get("trigger") if isinstance(row.get("trigger"), dict) else {}
    haystack.extend(str(v) for v in _listify(trigger.get("intent_in")))
    haystack.extend(str(v) for v in _listify(trigger.get("prompt_contains")))
    haystack.extend(str(v) for v in _listify(trigger.get("slots_required")))
    if any(focus in value.lower() for value in haystack):
        return True
    for unit_ai in _workflow_tool_names(row):
        tool = tool_by_name.get(unit_ai) or {}
        if focus in [str(tag).lower() for tag in (tool.get("tags") or [])]:
            return True
    return False


def _workflow_tool_names(row: dict[str, Any]) -> list[str]:
    out: list[str] = []
    steps = row.get("steps") if isinstance(row.get("steps"), list) else []
    for step in steps:
        if not isinstance(step, dict):
            continue
        unit_ai = str(step.get("unit_ai") or "").strip()
        if unit_ai and unit_ai not in out:
            out.append(unit_ai)
    return out


def _workflow_step_summaries(row: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    steps = row.get("steps") if isinstance(row.get("steps"), list) else []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        unit_ai = str(step.get("unit_ai") or "").strip()
        action = str(step.get("action") or "").strip()
        if not unit_ai and not action:
            continue
        out.append({
            "index": index,
            "unit_ai": unit_ai,
            "action": action,
            "bind_slots": [str(v) for v in _listify(step.get("bind_slots"))],
            "fixed_slots": dict(step.get("fixed_slots") or {}) if isinstance(step.get("fixed_slots"), dict) else {},
        })
    return out


def _workflow_step_node(
    workflow: dict[str, Any],
    step: dict[str, Any],
    step_id: str,
    *,
    step_has_issue: bool,
) -> dict[str, Any]:
    key = str(workflow.get("key") or "")
    step_index = int(step.get("index") or 0) + 1
    unit_ai = str(step.get("unit_ai") or "")
    action = str(step.get("action") or "")
    bind_slots = [str(v) for v in _listify(step.get("bind_slots"))]
    fixed_slots = step.get("fixed_slots") if isinstance(step.get("fixed_slots"), dict) else {}
    return {
        "id": step_id,
        "type": "workflow_step",
        "stage": "execute",
        "label": f"{step_index}. {unit_ai or 'unit_ai?'}.{action or 'action?'}",
        "detail": _workflow_step_detail(key, step_index, unit_ai, action, bind_slots, fixed_slots),
        "workflow_key": key,
        "step_index": step_index,
        "unit_ai": unit_ai,
        "action": action,
        "tone": "warn" if step_has_issue else "neutral",
        "tags": [tag for tag in ("workflow_step", key, unit_ai, action) if tag],
        "metrics": {
            "bind_slots": len(bind_slots),
            "fixed_slots": len(fixed_slots),
        },
    }


def _workflow_step_detail(
    key: str,
    step_index: int,
    unit_ai: str,
    action: str,
    bind_slots: list[str],
    fixed_slots: dict[str, Any],
) -> str:
    fixed_keys = [str(k) for k in fixed_slots.keys() if str(k).strip()]
    return "\n".join([
        f"workflow={key}",
        f"step={step_index}",
        f"unit_ai={unit_ai}",
        f"action={action}",
        "bind_slots=" + ",".join(bind_slots),
        "fixed_slots=" + ",".join(fixed_keys),
    ]).strip()


def _workflow_run_summaries(*, days: int) -> dict[str, dict[str, Any]]:
    log = audit.ACTIVITY_LOG
    if not log.exists():
        return {}
    cutoff = datetime.now(timezone.utc).timestamp() - max(1, days) * 86400
    summaries: dict[str, dict[str, Any]] = {}
    try:
        lines = log.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    for line in lines:
        try:
            rec = json.loads(line)
        except Exception:
            continue
        action = str(rec.get("action") or "")
        if not action.startswith("ai_hub_run:workflow:"):
            continue
        ts = _parse_ts(str(rec.get("timestamp") or ""))
        if ts and ts.timestamp() < cutoff:
            continue
        key = action.split("ai_hub_run:workflow:", 1)[-1].strip()
        if not key:
            continue
        detail = _json_detail(rec.get("detail"))
        statuses = detail.get("statuses") if isinstance(detail.get("statuses"), dict) else {}
        warning_count = sum(int(statuses.get(name) or 0) for name in ("error", "blocked", "missing_slots", "confirm_required", "no_handler"))
        row = summaries.setdefault(key, {
            "run_count": 0,
            "warning_count": 0,
            "last_run": "",
            "last_status": "",
        })
        row["run_count"] = int(row.get("run_count") or 0) + 1
        row["warning_count"] = int(row.get("warning_count") or 0) + warning_count
        timestamp = str(rec.get("timestamp") or "")
        if timestamp >= str(row.get("last_run") or ""):
            row["last_run"] = timestamp
            row["last_status"] = _status_label(statuses, bool(detail.get("dry_run")))
    return summaries


def _status_label(statuses: dict[str, Any], dry_run: bool) -> str:
    if statuses:
        return ",".join(f"{key}:{value}" for key, value in statuses.items())
    return "dry_run" if dry_run else "executed"


def _workflow_tone(steps: list[dict[str, Any]], run_summary: dict[str, Any]) -> str:
    if not steps:
        return "warn"
    if int(run_summary.get("warning_count") or 0) > 0:
        return "warn"
    return "ok"


def _workflow_tags(row: dict[str, Any], steps: list[dict[str, Any]]) -> list[str]:
    tags = ["workflow", "shared" if row.get("shared") else "personal"]
    trigger = row.get("trigger") if isinstance(row.get("trigger"), dict) else {}
    tags.extend(str(v) for v in _listify(trigger.get("intent_in")))
    tags.extend(str(step.get("unit_ai") or "") for step in steps if str(step.get("unit_ai") or "").strip())
    seen: set[str] = set()
    out: list[str] = []
    for tag in tags:
        value = str(tag or "").strip()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _workflow_detail(row: dict[str, Any], steps: list[dict[str, Any]]) -> str:
    trigger = row.get("trigger") if isinstance(row.get("trigger"), dict) else {}
    intents = ",".join(str(v) for v in _listify(trigger.get("intent_in")))
    contains = ",".join(str(v) for v in _listify(trigger.get("prompt_contains")))
    slots = ",".join(str(v) for v in _listify(trigger.get("slots_required")))
    parts = [
        f"trigger intent={intents}",
        f"contains={contains}",
        f"slots={slots}",
    ]
    step_lines = [
        f"{int(step.get('index') or 0) + 1}. {step.get('unit_ai') or ''}.{step.get('action') or ''}"
        for step in steps[:8]
    ]
    if len(steps) > 8:
        step_lines.append(f"+{len(steps) - 8} more")
    return "\n".join(parts + ["steps:"] + step_lines).strip()


def _workflow_actions(key: str) -> list[dict[str, Any]]:
    return [{
        "id": "dry_run",
        "label": "Dry-run",
        "method": "POST",
        "endpoint": "/api/agent/workflows/execute",
        "body": {
            "key": key,
            "slots": {},
            "dry_run": True,
        },
    }]


def _missing_tool_row(name: str) -> dict[str, Any]:
    return {
        "kind": "unit_ai",
        "name": str(name or "").strip(),
        "title": str(name or "").strip(),
        "description": "워크플로우 템플릿 step에 있지만 ToolRegistry에 등록되지 않은 unit_ai입니다.",
        "enabled": False,
        "tags": ["workflow", "missing_tool"],
        "count_30d": 0,
        "user_count_30d": 0,
        "knowledge_refs": {},
        "_missing": True,
    }


def _deep_eval_node(report: dict[str, Any]) -> dict[str, Any]:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    groups = report.get("groups") if isinstance(report.get("groups"), dict) else {}
    failed = int(summary.get("failed") or 0)
    total = int(summary.get("total") or 0)
    passed = int(summary.get("passed") or 0)
    status = str(report.get("status") or "missing")
    group_text = ", ".join(
        f"{name} {int((row or {}).get('passed') or 0)}/{int((row or {}).get('total') or 0)}"
        for name, row in groups.items()
        if isinstance(row, dict)
    )
    detail = "\n".join([
        f"status={status}",
        f"summary={passed}/{total} passed, {failed} failed",
        f"groups={group_text}",
        f"doc_id={report.get('doc_id') or ''}",
        f"path={report.get('path') or ''}",
    ]).strip()
    return {
        "id": "deep_eval:latest",
        "type": "deep_eval",
        "stage": "improve",
        "label": "Agent deep eval",
        "detail": detail,
        "tone": _deep_eval_tone(report),
        "tags": ["deep_eval", "semantic", "wiki", "sql", "validation"],
        "metrics": {
            "status": status,
            "passed": passed,
            "failed": failed,
            "total": total,
            "generated_at": str(report.get("generated_at") or report.get("updated_at") or ""),
            "path": str(report.get("path") or ""),
            "age_seconds": int(report.get("age_seconds") or 0),
        },
        "actions": [{
            "id": "run_deep_eval",
            "label": "재검증",
            "method": "POST",
            "endpoint": "/api/ai-hub/deep-eval-report/run",
            "body": {
                "cleanup_knowledge": False,
                "min_cases": 80,
            },
        }],
    }


def _deep_eval_tone(report: dict[str, Any]) -> str:
    if not report.get("exists"):
        return "warn"
    if report.get("status") == "pass":
        return "ok"
    return "bad"


def _json_detail(value: Any) -> dict[str, Any]:
    try:
        out = json.loads(str(value or "{}"))
    except Exception:
        return {}
    return out if isinstance(out, dict) else {}


def _parse_ts(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _reference_rows(refs: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for value in _listify(refs.get("wiki_doc_ids")):
        rows.append(_ref_node("wiki", value, "wiki"))
    for value in _listify(refs.get("relation_ids")):
        rows.append(_ref_node("relation", value, "relation"))
    for value in _listify(refs.get("column_catalog_keys")):
        rows.append(_ref_node("column", value, "column"))
    for value in _listify(refs.get("required_args")):
        rows.append(_ref_node("arg", value, "arg"))
    for value in _listify(refs.get("graph_node_ids")):
        rows.append(_ref_node("graph", value, "graph"))
    feature_md = str(refs.get("feature_md") or "").strip()
    if feature_md:
        rows.append(_ref_node("feature", feature_md, "feature"))
    return rows


def _ref_node(ref_type: str, raw_value: Any, edge_label: str) -> dict[str, Any]:
    value = str(raw_value or "").strip()
    return {
        "id": f"{ref_type}:{value}",
        "type": ref_type,
        "stage": "evidence",
        "label": value,
        "detail": value,
        "tone": "info" if ref_type in {"wiki", "feature", "arg"} else "neutral",
        "edge_label": edge_label,
    }


def _listify(value: Any) -> list[Any]:
    if isinstance(value, list):
        return [v for v in value if str(v or "").strip()]
    if isinstance(value, tuple):
        return [v for v in value if str(v or "").strip()]
    if value in (None, ""):
        return []
    return [value]


def _warnings(
    counts: dict[str, Any],
    tools_without_refs: list[str],
    workflow_missing_tools: list[str],
    workflow_empty_templates: list[str],
    workflow_incomplete_steps: list[str],
    deep_eval: dict[str, Any],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if counts["tools_disabled_visible"]:
        out.append({
            "key": "disabled_tools",
            "tone": "bad",
            "message": f"비활성 도구 {counts['tools_disabled_visible']}개가 지도에 포함되어 있습니다.",
            "action": "AI Hub 도구 카탈로그에서 비활성 도구의 필요 여부를 확인하고 필요한 도구를 활성화하세요.",
            "route": "/api/ai-hub/tools",
        })
    if tools_without_refs:
        out.append({
            "key": "missing_evidence",
            "tone": "warn",
            "message": f"Wiki/schema 근거가 비어 있는 도구 {len(tools_without_refs)}개가 있습니다.",
            "items": tools_without_refs[:12],
            "action": "Agent Wiki source 또는 schema ref를 보강하고 도구 knowledge_refs에 연결하세요.",
            "route": "/api/ai-hub/wiki-health",
        })
    if workflow_missing_tools:
        out.append({
            "key": "workflow_missing_tools",
            "tone": "warn",
            "message": f"workflow step이 참조하지만 등록되지 않은 도구 {len(workflow_missing_tools)}개가 있습니다.",
            "items": workflow_missing_tools[:12],
            "action": "workflow step의 unit_ai 값을 등록된 도구명으로 수정하거나 필요한 도구를 등록하세요.",
            "route": "/api/ai-hub/workflow-runbook",
        })
    if workflow_empty_templates:
        out.append({
            "key": "workflow_empty_templates",
            "tone": "warn",
            "message": f"step이 비어 있는 workflow template {len(workflow_empty_templates)}개가 있습니다.",
            "items": workflow_empty_templates[:12],
            "action": "workflow template에 실행 step을 추가하거나 starter workflow를 재생성하세요.",
            "route": "/api/ai-hub/workflow-runbook",
        })
    if workflow_incomplete_steps:
        out.append({
            "key": "workflow_incomplete_steps",
            "tone": "warn",
            "message": f"unit_ai/action이 불완전한 workflow step {len(workflow_incomplete_steps)}개가 있습니다.",
            "items": workflow_incomplete_steps[:12],
            "action": "비어 있는 unit_ai/action을 채운 뒤 Runbook에서 dry-run으로 재검증하세요.",
            "route": "/api/ai-hub/workflow-runbook",
        })
    if not deep_eval.get("exists"):
        out.append({
            "key": "deep_eval_missing",
            "tone": "warn",
            "message": "Agent deep-eval 최신 리포트가 없습니다.",
            "items": [str(deep_eval.get("path") or "reports/flowi_agent_deep_eval_latest.json")],
            "action": "Agent 검증 리포트를 생성해 semantic/knowledge/sql 회귀 상태를 확인하세요.",
            "route": "/api/ai-hub/deep-eval-report",
        })
    elif deep_eval.get("status") != "pass":
        failed = 0
        if isinstance(deep_eval.get("summary"), dict):
            failed = int((deep_eval.get("summary") or {}).get("failed") or 0)
        out.append({
            "key": "deep_eval_failed",
            "tone": "bad",
            "message": f"Agent deep-eval 상태가 {deep_eval.get('status')} 입니다. failed={failed}",
            "items": [
                str(row.get("name") or "")
                for row in (deep_eval.get("failed_results") or [])[:8]
                if isinstance(row, dict)
            ],
            "action": "실패 케이스의 지식, SQL, semantic 근거를 보강하고 deep-eval을 재실행하세요.",
            "route": "/api/ai-hub/deep-eval-report",
        })
    return out
