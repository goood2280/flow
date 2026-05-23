"""AI Hub workflow map.

Builds a read-only n8n/Obsidian-style graph from the existing tool catalog
and saved workflow templates. The graph is intentionally derived data: no
extra runtime store, no writes.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

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

    for tool in visible_tools:
        add_tool_node(tool)

    workflow_all_rows = _workflow_rows(username=username, focus_tag="", tool_by_name=tool_by_name)
    workflow_rows = [
        row for row in workflow_all_rows
        if _workflow_matches_focus(row, focus_tag, tool_by_name)
    ]
    workflow_limit = min(80, max(20, limit))
    visible_workflows = workflow_rows[:workflow_limit]
    for workflow in visible_workflows:
        key = str(workflow.get("key") or "").strip()
        if not key:
            continue
        steps = _workflow_step_summaries(workflow)
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
            "tone": "ok" if steps else "warn",
            "tags": _workflow_tags(workflow, steps),
            "metrics": {"steps": len(steps)},
            "steps": steps,
        })
        add_edge("stage:trigger", workflow_id, "template", "workflow")
        add_edge(workflow_id, "stage:policy", "guardrail", "workflow")
        if not steps:
            add_edge(workflow_id, "stage:improve", "fill steps", "workflow")
            continue
        for step in steps:
            unit_ai = str(step.get("unit_ai") or "").strip()
            if not unit_ai:
                continue
            tool = tool_by_name.get(unit_ai) or _missing_tool_row(unit_ai)
            tool_id = add_tool_node(tool)
            if tool.get("_missing"):
                workflow_missing_tools.add(unit_ai)
            if tool_id:
                label = str(step.get("action") or f"step {step.get('index', 0) + 1}")[:56]
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
        "workflow_missing_tools": len(workflow_missing_tools),
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
        "warnings": _warnings(counts, sorted(tools_without_refs), sorted(workflow_missing_tools)),
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
        "",
        "## Stages",
    ]
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


def _tag_slug(value: str) -> str:
    return _slug(value).replace("-", "_")


def _yaml_escape(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"')


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
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if counts["tools_disabled_visible"]:
        out.append({
            "key": "disabled_tools",
            "tone": "bad",
            "message": f"비활성 도구 {counts['tools_disabled_visible']}개가 지도에 포함되어 있습니다.",
        })
    if tools_without_refs:
        out.append({
            "key": "missing_evidence",
            "tone": "warn",
            "message": f"Wiki/schema 근거가 비어 있는 도구 {len(tools_without_refs)}개가 있습니다.",
            "items": tools_without_refs[:12],
        })
    if workflow_missing_tools:
        out.append({
            "key": "workflow_missing_tools",
            "tone": "warn",
            "message": f"workflow step이 참조하지만 등록되지 않은 도구 {len(workflow_missing_tools)}개가 있습니다.",
            "items": workflow_missing_tools[:12],
        })
    return out
