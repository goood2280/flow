"""AI Hub workflow map.

Builds a read-only n8n/Obsidian-style graph from the existing tool catalog.
The graph is intentionally derived data: no extra runtime store, no writes.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

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
    focus_tag = str(focus_tag or "").strip()

    all_tools = tool_registry.list_tools(include_stats=True, days=days)
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
    tools_without_refs: list[str] = []

    def add_node(node: dict[str, Any]) -> None:
        node_id = str(node.get("id") or "")
        if not node_id or node_id in seen_nodes:
            return
        seen_nodes.add(node_id)
        nodes.append(node)

    def add_edge(source: str, target: str, label: str = "", kind: str = "flow") -> None:
        if not source or not target:
            return
        key = (source, target, kind + ":" + label)
        if key in seen_edges:
            return
        seen_edges.add(key)
        edges.append({"from": source, "to": target, "label": label, "kind": kind})

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
        name = str(tool.get("name") or "")
        if not name:
            continue
        tool_id = f"tool:{name}"
        tags = [str(tag) for tag in (tool.get("tags") or []) if str(tag).strip()]
        refs = tool.get("knowledge_refs") if isinstance(tool.get("knowledge_refs"), dict) else {}
        ref_rows = _reference_rows(refs)
        if not ref_rows:
            tools_without_refs.append(name)

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
        add_edge("stage:policy", tool_id, "enabled" if tool.get("enabled") else "disabled", "policy")
        add_edge(tool_id, "stage:improve", "feedback", "improve")

        for ref in ref_rows:
            if ref_count >= reference_limit:
                break
            ref_id = ref["id"]
            add_node(ref)
            add_edge(tool_id, ref_id, ref["edge_label"], "evidence")
            ref_count += 1

    counts = {
        "tools_total": len(all_tools),
        "tools_visible": len(visible_tools),
        "tools_disabled_visible": sum(1 for row in visible_tools if not row.get("enabled")),
        "tools_without_refs_visible": len(tools_without_refs),
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
        "warnings": _warnings(counts, tools_without_refs),
    }


def _tool_sort_key(row: dict[str, Any]) -> tuple[int, int, str, str]:
    disabled_rank = 0 if not row.get("enabled") else 1
    count_rank = -int(row.get("count_30d") or 0)
    return (disabled_rank, count_rank, str(row.get("kind") or ""), str(row.get("title") or row.get("name") or ""))


def _tool_tone(tool: dict[str, Any]) -> str:
    if not tool.get("enabled"):
        return "bad"
    if int(tool.get("count_30d") or 0) > 0:
        return "ok"
    return "neutral"


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


def _warnings(counts: dict[str, Any], tools_without_refs: list[str]) -> list[dict[str, Any]]:
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
    return out
