"""AI Hub Obsidian operations export.

Builds a point-in-time vault bundle from existing AI Hub derived views:
readiness, deep-eval, workflow runbook, timeline, and the workflow map. No
runtime state is created or modified.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core import ai_hub_deep_eval
from core import ai_hub_readiness
from core import ai_hub_timeline
from core import ai_hub_wiki_health
from core import ai_hub_workflow_map
from core import ai_hub_workflow_runbook


def build_obsidian_export(
    *,
    username: str = "",
    days: int = 30,
    limit: int = 40,
    reference_limit: int = 160,
    focus_tag: str = "",
) -> dict[str, Any]:
    days = max(1, min(365, int(days or 30)))
    limit = max(1, min(120, int(limit or 40)))
    reference_limit = max(20, min(400, int(reference_limit or 160)))
    username = str(username or "")
    focus_tag = str(focus_tag or "").strip()

    readiness = ai_hub_readiness.build_readiness(username=username, days=days)
    deep_eval = ai_hub_deep_eval.load_latest_report()
    wiki_health = ai_hub_wiki_health.build_wiki_health(limit=limit)
    runbook = ai_hub_workflow_runbook.build_runbook(username=username, days=days, limit=limit, focus_tag=focus_tag)
    timeline = ai_hub_timeline.build_timeline(days=days, limit=min(80, max(30, limit)))
    workflow_export = ai_hub_workflow_map.export_workflow_map(
        export_format="obsidian",
        username=username,
        days=days,
        limit=limit,
        reference_limit=reference_limit,
        focus_tag=focus_tag,
    )
    workflow_warnings = _workflow_warnings(workflow_export)
    files = [
        {"path": "Flow AI Hub Operations.md", "body": _index_note(readiness, deep_eval, wiki_health, runbook, timeline, workflow_export)},
        {"path": "operations/readiness.md", "body": _readiness_note(readiness)},
        {"path": "operations/deep-eval.md", "body": _deep_eval_note(deep_eval)},
        {"path": "operations/wiki-health.md", "body": _wiki_health_note(wiki_health)},
        {"path": "operations/workflow-runbook.md", "body": _workflow_runbook_note(runbook)},
        {"path": "operations/workflow-map-warnings.md", "body": _workflow_warnings_note(workflow_export)},
        {"path": "operations/timeline.md", "body": _timeline_note(timeline)},
    ]
    files.extend([row for row in workflow_export.get("files") or [] if isinstance(row, dict)])
    return {
        "ok": True,
        "format": "obsidian_ops",
        "filename": "flow-ai-hub-operations.obsidian.json",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": days,
        "limit": limit,
        "reference_limit": reference_limit,
        "focus_tag": focus_tag,
        "counts": {
            "files": len(files),
            "readiness_backlog": len(readiness.get("backlog") or []),
            "deep_eval_failed": int(((deep_eval.get("summary") or {}) if isinstance(deep_eval.get("summary"), dict) else {}).get("failed") or 0),
            "wiki_lint_issues": int(((wiki_health.get("counts") or {}) if isinstance(wiki_health.get("counts"), dict) else {}).get("lint_issues") or 0),
            "runbook_workflows": int(((runbook.get("counts") or {}) if isinstance(runbook.get("counts"), dict) else {}).get("workflows") or 0),
            "runbook_next_actions": int(((runbook.get("counts") or {}) if isinstance(runbook.get("counts"), dict) else {}).get("next_actions") or 0),
            "workflow_map_warnings": len(workflow_warnings),
            "timeline_items": len(timeline.get("items") or []),
            "workflow_files": len(workflow_export.get("files") or []),
        },
        "sources": {
            "readiness": "/api/ai-hub/readiness",
            "deep_eval": "/api/ai-hub/deep-eval-report",
            "wiki_health": "/api/ai-hub/wiki-health",
            "workflow_runbook": "/api/ai-hub/workflow-runbook",
            "timeline": "/api/ai-hub/timeline",
            "workflow_map": "/api/ai-hub/workflow-map",
        },
        "files": files,
    }


def build_n8n_export(
    *,
    username: str = "",
    days: int = 30,
    limit: int = 40,
    focus_tag: str = "",
) -> dict[str, Any]:
    days = max(1, min(365, int(days or 30)))
    limit = max(1, min(120, int(limit or 40)))
    username = str(username or "")
    focus_tag = str(focus_tag or "").strip()
    readiness = ai_hub_readiness.build_readiness(username=username, days=days)
    deep_eval = ai_hub_deep_eval.load_latest_report()
    wiki_health = ai_hub_wiki_health.build_wiki_health(limit=limit)
    runbook = ai_hub_workflow_runbook.build_runbook(username=username, days=days, limit=limit, focus_tag=focus_tag)
    timeline = ai_hub_timeline.build_timeline(days=days, limit=min(40, max(20, limit)))
    workflow = ai_hub_workflow_map.build_workflow_map(
        username=username,
        days=days,
        limit=limit,
        reference_limit=160,
        focus_tag=focus_tag,
    )
    workflow_warnings = _workflow_warnings(workflow)

    nodes: list[dict[str, Any]] = []
    nodes.append(_n8n_note("ops:index", "Flow AI Hub Operations", _n8n_index_content(readiness, deep_eval, wiki_health, runbook, timeline, workflow), 0, 0))
    nodes.append(_n8n_note("ops:readiness", "Readiness", _n8n_readiness_content(readiness), 340, 0))
    nodes.append(_n8n_note("ops:runbook", "Workflow Runbook", _n8n_runbook_content(runbook), 680, 0))
    nodes.append(_n8n_note("ops:deep_eval", "Agent Deep Eval", _n8n_deep_eval_content(deep_eval), 1020, 0))
    nodes.append(_n8n_note("ops:wiki_health", "Agent Wiki Health", _n8n_wiki_health_content(wiki_health), 1360, 0))
    nodes.append(_n8n_note("ops:timeline", "Timeline", _n8n_timeline_content(timeline), 1700, 0))
    nodes.append(_n8n_note("ops:workflow_map", "Workflow Map", _n8n_workflow_content(workflow), 2040, 0))
    nodes.append(_n8n_note("ops:workflow_warnings", "Workflow Map Warnings", _n8n_workflow_warnings_content(workflow), 2380, 0))
    for idx, row in enumerate((readiness.get("backlog") if isinstance(readiness.get("backlog"), list) else [])[:10]):
        if not isinstance(row, dict):
            continue
        nodes.append(_n8n_note(
            f"ops:backlog:{idx + 1}",
            f"Backlog {idx + 1}",
            _n8n_backlog_content(row),
            340 + (idx % 5) * 260,
            220 + (idx // 5) * 180,
            width=230,
        ))

    connections: dict[str, dict[str, list[list[dict[str, Any]]]]] = {
        "ops:index": {"main": [[{"node": "ops:readiness", "type": "main", "index": 0}]]},
        "ops:readiness": {"main": [[{"node": "ops:runbook", "type": "main", "index": 0}]]},
        "ops:runbook": {"main": [[{"node": "ops:deep_eval", "type": "main", "index": 0}]]},
        "ops:deep_eval": {"main": [[{"node": "ops:wiki_health", "type": "main", "index": 0}]]},
        "ops:wiki_health": {"main": [[{"node": "ops:timeline", "type": "main", "index": 0}]]},
        "ops:timeline": {"main": [[{"node": "ops:workflow_map", "type": "main", "index": 0}]]},
        "ops:workflow_map": {"main": [[{"node": "ops:workflow_warnings", "type": "main", "index": 0}]]},
    }
    backlog_count = len([node for node in nodes if str(node.get("id") or "").startswith("ops:backlog:")])
    if backlog_count:
        connections.setdefault("ops:readiness", {"main": [[]]})
        for idx in range(backlog_count):
            connections["ops:readiness"]["main"][0].append({"node": f"ops:backlog:{idx + 1}", "type": "main", "index": 0})

    return {
        "ok": True,
        "format": "n8n_ops",
        "filename": "flow-ai-hub-operations.n8n.json",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": days,
        "limit": limit,
        "focus_tag": focus_tag,
        "workflow": {
            "name": "Flow AI Hub operations",
            "nodes": nodes,
            "connections": connections,
            "settings": {"executionOrder": "v1"},
            "staticData": {
                "flow_kind": "ai_hub_operations_review",
                "readiness_score": readiness.get("score"),
                "deep_eval_status": deep_eval.get("status"),
                "wiki_health_status": wiki_health.get("status"),
                "wiki_lint_issues": (wiki_health.get("counts") or {}).get("lint_issues") if isinstance(wiki_health.get("counts"), dict) else 0,
                "runbook_workflows": (runbook.get("counts") or {}).get("workflows") if isinstance(runbook.get("counts"), dict) else 0,
                "runbook_ready": (runbook.get("counts") or {}).get("ready") if isinstance(runbook.get("counts"), dict) else 0,
                "runbook_next_actions": (runbook.get("counts") or {}).get("next_actions") if isinstance(runbook.get("counts"), dict) else 0,
                "timeline_items": len(timeline.get("items") or []),
                "workflow_nodes": len(workflow.get("nodes") or []),
                "workflow_warnings": len(workflow_warnings),
            },
        },
    }


def export_obsidian_zip(export_payload: dict[str, Any]) -> bytes:
    return ai_hub_workflow_map.export_obsidian_zip(export_payload)


def _n8n_note(node_id: str, name: str, content: str, x: int, y: int, *, width: int = 300) -> dict[str, Any]:
    return {
        "id": node_id,
        "name": name,
        "type": "n8n-nodes-base.stickyNote",
        "typeVersion": 1,
        "position": [x, y],
        "parameters": {
            "content": content,
            "height": 150,
            "width": width,
        },
    }


def _n8n_index_content(readiness: dict[str, Any], deep_eval: dict[str, Any], wiki_health: dict[str, Any], runbook: dict[str, Any], timeline: dict[str, Any], workflow: dict[str, Any]) -> str:
    summary = deep_eval.get("summary") if isinstance(deep_eval.get("summary"), dict) else {}
    wiki_counts = wiki_health.get("counts") if isinstance(wiki_health.get("counts"), dict) else {}
    runbook_counts = runbook.get("counts") if isinstance(runbook.get("counts"), dict) else {}
    lines = [
        "## Flow AI Hub Operations",
        f"- readiness: {readiness.get('score') or 0} / {readiness.get('level') or ''}",
        f"- deep-eval: {deep_eval.get('status') or 'missing'} {summary.get('passed') or 0}/{summary.get('total') or 0}",
        f"- wiki-health: {wiki_health.get('status') or 'missing'} docs={wiki_counts.get('docs') or 0} lint={wiki_counts.get('lint_issues') or 0}",
        f"- workflow-runbook: {runbook_counts.get('ready') or 0}/{runbook_counts.get('workflows') or 0} ready",
        f"- runbook actions: {runbook_counts.get('next_actions') or 0}",
        f"- timeline: {len(timeline.get('items') or [])} items",
        f"- workflow graph: {len(workflow.get('nodes') or [])} nodes",
        f"- workflow warnings: {len(_workflow_warnings(workflow))}",
        "",
    ]
    queue = _runbook_queue(runbook)
    if queue:
        lines.append("Runbook action queue:")
        for action in queue[:4]:
            lines.append(f"- {action.get('title') or action.get('key')}: {action.get('count') or 0}")
        lines.append("")
    lines.append("Review-only export. Flow guardrails and approvals stay inside Flow.")
    return "\n".join(lines)


def _n8n_readiness_content(readiness: dict[str, Any]) -> str:
    lines = [
        "## Readiness",
        f"score: {readiness.get('score') or 0}",
        f"level: {readiness.get('level') or ''}",
        f"backlog: {len(readiness.get('backlog') or [])}",
        "",
    ]
    for row in (readiness.get("checks") if isinstance(readiness.get("checks"), list) else [])[:6]:
        if isinstance(row, dict):
            lines.append(f"- {row.get('label')}: {row.get('score')} ({row.get('detail')})")
    return "\n".join(lines)


def _n8n_deep_eval_content(report: dict[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    return "\n".join([
        "## Agent Deep Eval",
        f"status: {report.get('status') or 'missing'}",
        f"passed: {summary.get('passed') or 0}",
        f"failed: {summary.get('failed') or 0}",
        f"total: {summary.get('total') or 0}",
        f"path: {report.get('path') or ''}",
    ])


def _n8n_wiki_health_content(health: dict[str, Any]) -> str:
    counts = health.get("counts") if isinstance(health.get("counts"), dict) else {}
    return "\n".join([
        "## Agent Wiki Health",
        f"status: {health.get('status') or 'missing'}",
        f"docs: {counts.get('docs') or 0}",
        f"agent_wiki: {counts.get('agent_wiki_pages') or 0}",
        f"sources: {counts.get('sources') or 0}",
        f"graph: {counts.get('graph_nodes') or 0}/{counts.get('graph_edges') or 0}",
        f"lint issues: {counts.get('lint_issues') or 0}",
    ])


def _n8n_runbook_content(runbook: dict[str, Any]) -> str:
    counts = runbook.get("counts") if isinstance(runbook.get("counts"), dict) else {}
    queue = _runbook_queue(runbook)
    lines = [
        "## Workflow Runbook",
        f"workflows: {counts.get('workflows') or 0}",
        f"ready: {counts.get('ready') or 0}",
        f"attention: {counts.get('attention') or 0}",
        f"blocked: {counts.get('blocked') or 0}",
        f"next_actions: {counts.get('next_actions') or 0}",
        "",
    ]
    if queue:
        lines.append("next_action_queue:")
        for action in queue[:6]:
            lines.append(f"- {action.get('title') or action.get('key')}: {action.get('count') or 0} workflows route={action.get('route') or '-'}")
        lines.append("")
    for row in (runbook.get("items") if isinstance(runbook.get("items"), list) else [])[:8]:
        if not isinstance(row, dict):
            continue
        next_action = _next_action_text(row)
        suffix = f" next={next_action}" if next_action else ""
        lines.append(f"- {row.get('status')}: {row.get('title') or row.get('key')} steps={row.get('step_count') or 0} last={row.get('last_status') or '-'}{suffix}")
    if not (runbook.get("items") if isinstance(runbook.get("items"), list) else []):
        lines.append("- no workflow templates")
    return "\n".join(lines)


def _n8n_timeline_content(timeline: dict[str, Any]) -> str:
    lines = ["## Timeline", f"days: {timeline.get('days') or 0}", ""]
    for row in (timeline.get("items") if isinstance(timeline.get("items"), list) else [])[:8]:
        if isinstance(row, dict):
            lines.append(f"- {row.get('category')}: {row.get('title') or row.get('action')} ({row.get('username')})")
    if len(lines) == 3:
        lines.append("- no recent AI Hub events")
    return "\n".join(lines)


def _n8n_workflow_content(workflow: dict[str, Any]) -> str:
    counts = workflow.get("counts") if isinstance(workflow.get("counts"), dict) else {}
    warnings = _workflow_warnings(workflow)
    lines = [
        "## Workflow Map",
        f"tools: {counts.get('tools_visible') or 0}/{counts.get('tools_total') or 0}",
        f"workflows: {counts.get('workflow_templates_visible') or 0}",
        f"nodes: {counts.get('nodes') or 0}",
        f"edges: {counts.get('edges') or 0}",
        f"warnings: {len(warnings)}",
        "",
    ]
    for row in warnings[:5]:
        lines.append(f"- {row.get('key')}: {row.get('message') or ''}")
    if not warnings:
        lines.append("- no workflow map warnings")
    return "\n".join(lines)


def _n8n_workflow_warnings_content(workflow: dict[str, Any]) -> str:
    warnings = _workflow_warnings(workflow)
    lines = ["## Workflow Map Warnings", f"count: {len(warnings)}", ""]
    if not warnings:
        lines.append("- none")
    for row in warnings[:8]:
        item_text = ", ".join(str(item) for item in row.get("items", [])[:4]) or "-"
        lines.append(f"- {row.get('tone')}: {row.get('key')} items={row.get('item_count') or 0} route={row.get('route') or '-'}")
        lines.append(f"  action: {row.get('action') or ''}")
        lines.append(f"  {row.get('message') or ''}")
        lines.append(f"  examples: {item_text}")
    return "\n".join(lines)


def _n8n_backlog_content(row: dict[str, Any]) -> str:
    return "\n".join([
        f"## {row.get('title') or 'Backlog'}",
        f"severity: {row.get('severity') or ''}",
        f"target: {row.get('target') or ''}",
        "",
        str(row.get("detail") or ""),
        "",
        f"action: {row.get('action') or ''}",
    ])


def _index_note(readiness: dict[str, Any], deep_eval: dict[str, Any], wiki_health: dict[str, Any], runbook: dict[str, Any], timeline: dict[str, Any], workflow_export: dict[str, Any]) -> str:
    summary = deep_eval.get("summary") if isinstance(deep_eval.get("summary"), dict) else {}
    wiki_counts = wiki_health.get("counts") if isinstance(wiki_health.get("counts"), dict) else {}
    runbook_counts = runbook.get("counts") if isinstance(runbook.get("counts"), dict) else {}
    workflow_files = workflow_export.get("files") if isinstance(workflow_export.get("files"), list) else []
    workflow_warnings = _workflow_warnings(workflow_export)
    lines = [
        "---",
        'title: "Flow AI Hub Operations"',
        'kind: "ai_hub_ops_export"',
        f'generated_at: "{_cell(readiness.get("generated_at") or "")}"',
        "---",
        "",
        "# Flow AI Hub Operations",
        "",
        "## Snapshot",
        "",
        f"- readiness: `{readiness.get('score') or 0}` / `{readiness.get('level') or ''}`",
        f"- deep_eval: `{deep_eval.get('status') or 'missing'}` `{summary.get('passed') or 0}/{summary.get('total') or 0}`",
        f"- wiki_health: `{wiki_health.get('status') or 'missing'}` docs `{wiki_counts.get('docs') or 0}` lint `{wiki_counts.get('lint_issues') or 0}`",
        f"- workflow_runbook: ready `{runbook_counts.get('ready') or 0}/{runbook_counts.get('workflows') or 0}` blocked `{runbook_counts.get('blocked') or 0}`",
        f"- runbook_next_actions: `{runbook_counts.get('next_actions') or 0}`",
        f"- workflow_map_warnings: `{len(workflow_warnings)}`",
        f"- timeline_items: `{len(timeline.get('items') or [])}`",
        f"- workflow_notes: `{len(workflow_files)}`",
        "",
    ]
    queue = _runbook_queue(runbook)
    if queue:
        lines.extend([
            "## Runbook Action Queue",
            "",
            "| action | workflows | route | examples |",
            "|---|---:|---|---|",
        ])
        for action in queue[:8]:
            workflows = action.get("workflows") if isinstance(action.get("workflows"), list) else []
            examples = ", ".join(_cell(row.get("title") or row.get("key")) for row in workflows[:3] if isinstance(row, dict))
            lines.append(
                f"| {_cell(action.get('title') or action.get('key'))} | {_cell(action.get('count'))} | "
                f"{_cell(action.get('route'))} | {examples or '-'} |"
            )
        lines.append("")
    if workflow_warnings:
        lines.extend([
            "## Workflow Map Warnings",
            "",
            "| key | tone | items | action | message |",
            "|---|---|---:|---|---|",
        ])
        for row in workflow_warnings[:8]:
            lines.append(
                f"| {_cell(row.get('key'))} | {_cell(row.get('tone'))} | "
                f"{_cell(row.get('item_count'))} | {_cell(row.get('action'))} | {_cell(row.get('message'))} |"
            )
        lines.append("")
    lines.extend([
        "## Notes",
        "",
        "- [[operations/readiness|Readiness]]",
        "- [[operations/deep-eval|Agent Deep Eval]]",
        "- [[operations/wiki-health|Agent Wiki Health]]",
        "- [[operations/workflow-runbook|Workflow Runbook]]",
        "- [[operations/workflow-map-warnings|Workflow Map Warnings]]",
        "- [[operations/timeline|Operations Timeline]]",
        "- [[Flow AI Hub Workflow Map|Workflow Map]]",
        "",
    ])
    return "\n".join(lines)


def _readiness_note(readiness: dict[str, Any]) -> str:
    lines = [
        "---",
        'title: "AI Hub Readiness"',
        'kind: "ai_hub_readiness"',
        "---",
        "",
        "# AI Hub Readiness",
        "",
        f"- score: `{readiness.get('score') or 0}`",
        f"- level: `{readiness.get('level') or ''}`",
        f"- generated_at: `{readiness.get('generated_at') or ''}`",
        "",
        "## Checks",
        "",
        "| key | label | score | detail |",
        "|---|---|---:|---|",
    ]
    for row in readiness.get("checks") or []:
        if not isinstance(row, dict):
            continue
        lines.append(f"| {_cell(row.get('key'))} | {_cell(row.get('label'))} | {_cell(row.get('score'))} | {_cell(row.get('detail'))} |")
    lines.extend(["", "## Backlog", ""])
    backlog = readiness.get("backlog") if isinstance(readiness.get("backlog"), list) else []
    if not backlog:
        lines.append("- none")
    for row in backlog[:30]:
        if not isinstance(row, dict):
            continue
        lines.append(f"- `{_cell(row.get('severity'))}` **{_cell(row.get('title'))}** `{_cell(row.get('target'))}` - {_cell(row.get('detail'))}")
    lines.append("")
    return "\n".join(lines)


def _deep_eval_note(report: dict[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = [
        "---",
        'title: "Agent Deep Eval"',
        'kind: "ai_hub_deep_eval"',
        "---",
        "",
        "# Agent Deep Eval",
        "",
        f"- status: `{report.get('status') or 'missing'}`",
        f"- passed: `{summary.get('passed') or 0}`",
        f"- failed: `{summary.get('failed') or 0}`",
        f"- total: `{summary.get('total') or 0}`",
        f"- generated_at: `{report.get('generated_at') or ''}`",
        f"- path: `{report.get('path') or ''}`",
        "",
        "## Groups",
        "",
        "| group | passed | failed | total |",
        "|---|---:|---:|---:|",
    ]
    groups = report.get("groups") if isinstance(report.get("groups"), dict) else {}
    for name, row in groups.items():
        if not isinstance(row, dict):
            continue
        lines.append(f"| {_cell(name)} | {_cell(row.get('passed'))} | {_cell(row.get('failed'))} | {_cell(row.get('total'))} |")
    lines.extend(["", "## Failed Assertions", ""])
    failed = report.get("failed_results") if isinstance(report.get("failed_results"), list) else []
    if not failed:
        lines.append("- none")
    for row in failed[:20]:
        if isinstance(row, dict):
            lines.append(f"- `{_cell(row.get('name'))}` - {_cell(row.get('detail'))}")
    lines.extend(["", "## Case Samples", ""])
    samples = report.get("result_samples") if isinstance(report.get("result_samples"), list) else []
    for row in samples[:40]:
        if isinstance(row, dict):
            mark = "PASS" if row.get("ok") else "FAIL"
            lines.append(f"- `{mark}` `{_cell(row.get('group'))}` {_cell(row.get('name'))}")
    if not samples:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def _wiki_health_note(health: dict[str, Any]) -> str:
    counts = health.get("counts") if isinstance(health.get("counts"), dict) else {}
    lint = health.get("lint") if isinstance(health.get("lint"), dict) else {}
    lint_counts = lint.get("counts") if isinstance(lint.get("counts"), dict) else {}
    lines = [
        "---",
        'title: "Agent Wiki Health"',
        'kind: "ai_hub_wiki_health"',
        "---",
        "",
        "# Agent Wiki Health",
        "",
        f"- status: `{health.get('status') or 'missing'}`",
        f"- docs: `{counts.get('docs') or 0}`",
        f"- agent_wiki_pages: `{counts.get('agent_wiki_pages') or 0}`",
        f"- schema_docs: `{counts.get('schema_docs') or 0}`",
        f"- sources: `{counts.get('sources') or 0}`",
        f"- graph: `{counts.get('graph_nodes') or 0}/{counts.get('graph_edges') or 0}`",
        f"- lint_issues: `{counts.get('lint_issues') or 0}`",
        f"- generated_at: `{health.get('generated_at') or ''}`",
        "",
        "## Lint Counts",
        "",
        "| key | count |",
        "|---|---:|",
    ]
    for key in ("broken_links", "missing_sources", "orphan_pages", "stale_summaries", "contradiction_candidates"):
        lines.append(f"| {_cell(key)} | {_cell(lint_counts.get(key) or counts.get('lint_' + key) or 0)} |")
    lines.extend(["", "## Recent Pages", "", "| doc_id | kind | title | updated_at |", "|---|---|---|---|"])
    for row in (health.get("recent_pages") if isinstance(health.get("recent_pages"), list) else [])[:30]:
        if isinstance(row, dict):
            lines.append(f"| {_cell(row.get('doc_id'))} | {_cell(row.get('kind'))} | {_cell(row.get('title'))} | {_cell(row.get('updated_at'))} |")
    lines.extend(["", "## Recent Sources", "", "| source_id | type | title | actor |", "|---|---|---|---|"])
    for row in (health.get("recent_sources") if isinstance(health.get("recent_sources"), list) else [])[:30]:
        if isinstance(row, dict):
            lines.append(f"| {_cell(row.get('source_id'))} | {_cell(row.get('source_type'))} | {_cell(row.get('title'))} | {_cell(row.get('actor'))} |")
    lines.extend(["", "## Recent Wiki Log", ""])
    log_rows = health.get("recent_log") if isinstance(health.get("recent_log"), list) else []
    if not log_rows:
        lines.append("- none")
    for row in log_rows[:30]:
        if isinstance(row, dict):
            lines.append(f"- `{_cell(row.get('action'))}` `{_cell(row.get('doc_id'))}` {_cell(row.get('message') or row.get('title'))}")
    lines.append("")
    return "\n".join(lines)


def _workflow_runbook_note(runbook: dict[str, Any]) -> str:
    counts = runbook.get("counts") if isinstance(runbook.get("counts"), dict) else {}
    lines = [
        "---",
        'title: "Agent Workflow Runbook"',
        'kind: "ai_hub_workflow_runbook"',
        "---",
        "",
        "# Agent Workflow Runbook",
        "",
        f"- workflows: `{counts.get('workflows') or 0}`",
        f"- ready: `{counts.get('ready') or 0}`",
        f"- attention: `{counts.get('attention') or 0}`",
        f"- blocked: `{counts.get('blocked') or 0}`",
        f"- checked: `{counts.get('checked') or 0}`",
        f"- next_actions: `{counts.get('next_actions') or 0}`",
        "",
        "## Next Action Queue",
        "",
        "| action | tone | workflows | route | examples |",
        "|---|---|---:|---|---|",
    ]
    queue = _runbook_queue(runbook)
    if not queue:
        lines.append("| none | - | 0 | - | - |")
    for action in queue[:20]:
        workflows = action.get("workflows") if isinstance(action.get("workflows"), list) else []
        examples = ", ".join(_cell(row.get("title") or row.get("key")) for row in workflows[:4] if isinstance(row, dict))
        lines.append(
            f"| {_cell(action.get('title') or action.get('key'))} | {_cell(action.get('tone'))} | "
            f"{_cell(action.get('count'))} | {_cell(action.get('route'))} | {examples or '-'} |"
        )
    lines.extend([
        "",
        "| status | workflow | scope | steps | tools | last check | issues | next action |",
        "|---|---|---|---:|---|---|---|---|",
    ])
    rows = runbook.get("items") if isinstance(runbook.get("items"), list) else []
    if not rows:
        lines.append("| none | - | - | 0 | - | - | - | - |")
    for row in rows[:80]:
        if not isinstance(row, dict):
            continue
        issue_text = ", ".join(_cell(issue.get("label")) for issue in (row.get("issues") or []) if isinstance(issue, dict)) or "ready"
        next_action = _next_action_text(row) or "-"
        scope = "shared" if row.get("shared") else "personal"
        tools = ", ".join(_cell(tool) for tool in (row.get("tool_names") or [])[:8])
        lines.append(
            f"| {_cell(row.get('status'))} | {_cell(row.get('title') or row.get('key'))} | {scope} | "
            f"{_cell(row.get('step_count'))} | {tools} | {_cell(row.get('last_status') or row.get('last_run'))} | {issue_text} | {_cell(next_action)} |"
        )
    lines.extend(["", "## Steps", ""])
    for row in rows[:40]:
        if not isinstance(row, dict):
            continue
        lines.append(f"### {_cell(row.get('title') or row.get('key'))}")
        steps = row.get("steps") if isinstance(row.get("steps"), list) else []
        if not steps:
            lines.append("- no steps")
        for step in steps[:20]:
            if isinstance(step, dict):
                lines.append(f"- `{_cell(step.get('index'))}` `{_cell(step.get('unit_ai'))}`.`{_cell(step.get('action'))}`")
        lines.append("")
    return "\n".join(lines)


def _next_action_text(row: dict[str, Any]) -> str:
    actions = row.get("next_actions") if isinstance(row.get("next_actions"), list) else []
    first = next((item for item in actions if isinstance(item, dict)), None)
    if not first:
        return ""
    title = str(first.get("title") or "").strip()
    detail = str(first.get("detail") or "").strip()
    if title and detail:
        return f"{title}: {detail}"
    return title or detail


def _runbook_queue(runbook: dict[str, Any]) -> list[dict[str, Any]]:
    queue = runbook.get("next_action_queue")
    return queue if isinstance(queue, list) else []


def _workflow_warnings(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    raw = workflow.get("warnings") if isinstance(workflow.get("warnings"), list) else []
    rows: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        items = row.get("items") if isinstance(row.get("items"), list) else []
        key = str(row.get("key") or "")
        rows.append({
            "key": key,
            "tone": str(row.get("tone") or "neutral"),
            "message": str(row.get("message") or "")[:260],
            "action": str(row.get("action") or _workflow_warning_action(key))[:260],
            "item_count": len(items),
            "items": [str(item) for item in items[:12]],
            "route": str(row.get("route") or "/api/ai-hub/workflow-map"),
        })
    return rows


def _workflow_warning_action(key: str) -> str:
    return {
        "disabled_tools": "AI Hub 도구 카탈로그에서 비활성 도구의 필요 여부를 확인하고 필요한 도구를 활성화하세요.",
        "missing_evidence": "Agent Wiki source 또는 schema ref를 보강하고 도구 knowledge_refs에 연결하세요.",
        "workflow_missing_tools": "workflow step의 unit_ai 값을 등록된 도구명으로 수정하거나 필요한 도구를 등록하세요.",
        "workflow_empty_templates": "workflow template에 실행 step을 추가하거나 starter workflow를 재생성하세요.",
        "workflow_incomplete_steps": "비어 있는 unit_ai/action을 채운 뒤 Runbook에서 dry-run으로 재검증하세요.",
        "deep_eval_missing": "Agent 검증 리포트를 생성해 semantic/knowledge/sql 회귀 상태를 확인하세요.",
        "deep_eval_failed": "실패 케이스의 지식, SQL, semantic 근거를 보강하고 deep-eval을 재실행하세요.",
    }.get(str(key or ""), "워크플로우 지도에서 경고 대상과 연결 근거를 확인하세요.")


def _workflow_warnings_note(workflow: dict[str, Any]) -> str:
    warnings = _workflow_warnings(workflow)
    lines = [
        "---",
        'title: "Workflow Map Warnings"',
        'kind: "ai_hub_workflow_map_warnings"',
        "---",
        "",
        "# Workflow Map Warnings",
        "",
        f"- count: `{len(warnings)}`",
        f"- route: `/api/ai-hub/workflow-map`",
        "",
        "| key | tone | items | examples | action | message |",
        "|---|---|---:|---|---|---|",
    ]
    if not warnings:
        lines.append("| none | - | 0 | - | - | - |")
    for row in warnings[:40]:
        examples = ", ".join(_cell(item) for item in row.get("items", [])[:6]) or "-"
        lines.append(
            f"| {_cell(row.get('key'))} | {_cell(row.get('tone'))} | {_cell(row.get('item_count'))} | "
            f"{examples} | {_cell(row.get('action'))} | {_cell(row.get('message'))} |"
        )
    lines.append("")
    return "\n".join(lines)


def _timeline_note(timeline: dict[str, Any]) -> str:
    lines = [
        "---",
        'title: "AI Hub Operations Timeline"',
        'kind: "ai_hub_timeline"',
        "---",
        "",
        "# AI Hub Operations Timeline",
        "",
        f"- days: `{timeline.get('days') or 0}`",
        f"- items: `{len(timeline.get('items') or [])}`",
        "",
        "| time | category | user | title | detail |",
        "|---|---|---|---|---|",
    ]
    for row in (timeline.get("items") if isinstance(timeline.get("items"), list) else [])[:80]:
        if not isinstance(row, dict):
            continue
        lines.append(
            f"| {_cell(row.get('timestamp'))} | {_cell(row.get('category'))} | {_cell(row.get('username'))} | "
            f"{_cell(row.get('title'))} | {_cell(row.get('detail') or row.get('meta'))} |"
        )
    lines.append("")
    return "\n".join(lines)


def _cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ").strip()
