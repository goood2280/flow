"""AI Hub Obsidian operations export.

Builds a point-in-time vault bundle from existing AI Hub derived views:
readiness, deep-eval, timeline, and the workflow map. No runtime state is
created or modified.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core import ai_hub_deep_eval
from core import ai_hub_readiness
from core import ai_hub_timeline
from core import ai_hub_workflow_map


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
        {"path": "Flow AI Hub Operations.md", "body": _index_note(readiness, deep_eval, timeline, workflow_export)},
        {"path": "operations/readiness.md", "body": _readiness_note(readiness)},
        {"path": "operations/deep-eval.md", "body": _deep_eval_note(deep_eval)},
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
            "timeline_items": len(timeline.get("items") or []),
            "workflow_files": len(workflow_export.get("files") or []),
        },
        "sources": {
            "readiness": "/api/ai-hub/readiness",
            "deep_eval": "/api/ai-hub/deep-eval-report",
            "timeline": "/api/ai-hub/timeline",
            "workflow_map": "/api/ai-hub/workflow-map",
        },
        "files": files,
    }


def export_obsidian_zip(export_payload: dict[str, Any]) -> bytes:
    return ai_hub_workflow_map.export_obsidian_zip(export_payload)


def _index_note(readiness: dict[str, Any], deep_eval: dict[str, Any], timeline: dict[str, Any], workflow_export: dict[str, Any]) -> str:
    summary = deep_eval.get("summary") if isinstance(deep_eval.get("summary"), dict) else {}
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
        f"- timeline_items: `{len(timeline.get('items') or [])}`",
        f"- workflow_notes: `{len(workflow_files)}`",
        "",
        "## Notes",
        "",
        "- [[operations/readiness|Readiness]]",
        "- [[operations/deep-eval|Agent Deep Eval]]",
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
