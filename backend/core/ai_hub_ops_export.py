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
    files = [
        {"path": "Flow AI Hub Operations.md", "body": _index_note(readiness, deep_eval, wiki_health, runbook, timeline, workflow_export)},
        {"path": "operations/readiness.md", "body": _readiness_note(readiness)},
        {"path": "operations/deep-eval.md", "body": _deep_eval_note(deep_eval)},
        {"path": "operations/wiki-health.md", "body": _wiki_health_note(wiki_health)},
        {"path": "operations/workflow-runbook.md", "body": _workflow_runbook_note(runbook)},
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

    nodes: list[dict[str, Any]] = []
    nodes.append(_n8n_note("ops:index", "Flow AI Hub Operations", _n8n_index_content(readiness, deep_eval, wiki_health, runbook, timeline, workflow), 0, 0))
    nodes.append(_n8n_note("ops:readiness", "Readiness", _n8n_readiness_content(readiness), 340, 0))
    nodes.append(_n8n_note("ops:runbook", "Workflow Runbook", _n8n_runbook_content(runbook), 680, 0))
    nodes.append(_n8n_note("ops:deep_eval", "Agent Deep Eval", _n8n_deep_eval_content(deep_eval), 1020, 0))
    nodes.append(_n8n_note("ops:wiki_health", "Agent Wiki Health", _n8n_wiki_health_content(wiki_health), 1360, 0))
    nodes.append(_n8n_note("ops:timeline", "Timeline", _n8n_timeline_content(timeline), 1700, 0))
    nodes.append(_n8n_note("ops:workflow_map", "Workflow Map", _n8n_workflow_content(workflow), 2040, 0))
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
                "timeline_items": len(timeline.get("items") or []),
                "workflow_nodes": len(workflow.get("nodes") or []),
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
    return "\n".join([
        "## Flow AI Hub Operations",
        f"- readiness: {readiness.get('score') or 0} / {readiness.get('level') or ''}",
        f"- deep-eval: {deep_eval.get('status') or 'missing'} {summary.get('passed') or 0}/{summary.get('total') or 0}",
        f"- wiki-health: {wiki_health.get('status') or 'missing'} docs={wiki_counts.get('docs') or 0} lint={wiki_counts.get('lint_issues') or 0}",
        f"- workflow-runbook: {runbook_counts.get('ready') or 0}/{runbook_counts.get('workflows') or 0} ready",
        f"- timeline: {len(timeline.get('items') or [])} items",
        f"- workflow graph: {len(workflow.get('nodes') or [])} nodes",
        "",
        "Review-only export. Flow guardrails and approvals stay inside Flow.",
    ])


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
    lines = [
        "## Workflow Runbook",
        f"workflows: {counts.get('workflows') or 0}",
        f"ready: {counts.get('ready') or 0}",
        f"attention: {counts.get('attention') or 0}",
        f"blocked: {counts.get('blocked') or 0}",
        "",
    ]
    for row in (runbook.get("items") if isinstance(runbook.get("items"), list) else [])[:8]:
        if not isinstance(row, dict):
            continue
        lines.append(f"- {row.get('status')}: {row.get('title') or row.get('key')} steps={row.get('step_count') or 0} last={row.get('last_status') or '-'}")
    if len(lines) == 6:
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
    return "\n".join([
        "## Workflow Map",
        f"tools: {counts.get('tools_visible') or 0}/{counts.get('tools_total') or 0}",
        f"workflows: {counts.get('workflow_templates_visible') or 0}",
        f"nodes: {counts.get('nodes') or 0}",
        f"edges: {counts.get('edges') or 0}",
        f"warnings: {len(workflow.get('warnings') or [])}",
    ])


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
    return "\n".join([
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
        f"- timeline_items: `{len(timeline.get('items') or [])}`",
        f"- workflow_notes: `{len(workflow_files)}`",
        "",
        "## Notes",
        "",
        "- [[operations/readiness|Readiness]]",
        "- [[operations/deep-eval|Agent Deep Eval]]",
        "- [[operations/wiki-health|Agent Wiki Health]]",
        "- [[operations/workflow-runbook|Workflow Runbook]]",
        "- [[operations/timeline|Operations Timeline]]",
        "- [[Flow AI Hub Workflow Map|Workflow Map]]",
        "",
    ])


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
        "",
        "| status | workflow | scope | steps | tools | last check | issues |",
        "|---|---|---|---:|---|---|---|",
    ]
    rows = runbook.get("items") if isinstance(runbook.get("items"), list) else []
    if not rows:
        lines.append("| none | - | - | 0 | - | - | - |")
    for row in rows[:80]:
        if not isinstance(row, dict):
            continue
        issue_text = ", ".join(_cell(issue.get("label")) for issue in (row.get("issues") or []) if isinstance(issue, dict)) or "ready"
        scope = "shared" if row.get("shared") else "personal"
        tools = ", ".join(_cell(tool) for tool in (row.get("tool_names") or [])[:8])
        lines.append(
            f"| {_cell(row.get('status'))} | {_cell(row.get('title') or row.get('key'))} | {scope} | "
            f"{_cell(row.get('step_count'))} | {tools} | {_cell(row.get('last_status') or row.get('last_run'))} | {issue_text} |"
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
