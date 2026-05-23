"""AI Hub daily operations snapshot.

Read-only aggregate over existing AI Hub operator views. It does not create
runtime state or execute Agent tools.
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


def build_snapshot(*, username: str = "", days: int = 30, limit: int = 8) -> dict[str, Any]:
    days = max(1, min(365, int(days or 30)))
    limit = max(1, min(30, int(limit or 8)))
    username = str(username or "")

    readiness = ai_hub_readiness.build_readiness(username=username, days=days)
    deep_eval = ai_hub_deep_eval.load_latest_report()
    wiki_health = ai_hub_wiki_health.build_wiki_health(limit=max(12, limit))
    runbook = ai_hub_workflow_runbook.build_runbook(username=username, days=days, limit=max(12, limit))
    workflow_map = ai_hub_workflow_map.build_workflow_map(username=username, days=days, limit=40, reference_limit=160)
    timeline = ai_hub_timeline.build_timeline(days=days, limit=max(12, limit))

    cards = [
        _readiness_card(readiness),
        _runbook_card(runbook),
        _workflow_map_card(workflow_map),
        _deep_eval_card(deep_eval),
        _wiki_card(wiki_health),
        _timeline_card(timeline),
    ]
    status = _overall_status(cards)
    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": days,
        "limit": limit,
        "status": status,
        "headline": _headline(status),
        "summary_cards": cards,
        "top_actions": _top_actions(readiness.get("backlog"), limit=limit),
        "runbook_action_queue": _runbook_action_queue(runbook, limit=limit),
        "workflow_map_warnings": _workflow_map_warnings(workflow_map, limit=limit),
        "recent_events": _recent_events(timeline.get("items"), limit=limit),
        "export_links": _export_links(days),
        "counts": {
            "backlog": len(readiness.get("backlog") if isinstance(readiness.get("backlog"), list) else []),
            "runbook_workflows": _runbook_count(runbook, "workflows"),
            "runbook_ready": _runbook_count(runbook, "ready"),
            "runbook_attention": _runbook_count(runbook, "attention"),
            "runbook_blocked": _runbook_count(runbook, "blocked"),
            "runbook_next_actions": _runbook_count(runbook, "next_actions"),
            "workflow_map_nodes": _workflow_map_count(workflow_map, "nodes"),
            "workflow_map_edges": _workflow_map_count(workflow_map, "edges"),
            "workflow_map_warnings": len(workflow_map.get("warnings") if isinstance(workflow_map.get("warnings"), list) else []),
            "timeline_items": len(timeline.get("items") if isinstance(timeline.get("items"), list) else []),
            "summary_cards": len(cards),
        },
        "sources": {
            "readiness": "/api/ai-hub/readiness",
            "deep_eval_report": "/api/ai-hub/deep-eval-report",
            "wiki_health": "/api/ai-hub/wiki-health",
            "workflow_runbook": "/api/ai-hub/workflow-runbook",
            "workflow_map": "/api/ai-hub/workflow-map",
            "timeline": "/api/ai-hub/timeline",
            "ops_export": "/api/ai-hub/ops-export/download",
        },
    }


def _readiness_card(readiness: dict[str, Any]) -> dict[str, Any]:
    score = _int(readiness.get("score"))
    backlog = readiness.get("backlog") if isinstance(readiness.get("backlog"), list) else []
    return {
        "key": "readiness",
        "label": "운영 준비도",
        "value": f"{score}점",
        "tone": _score_tone(score),
        "detail": f"{readiness.get('level') or '-'} · 개선 {len(backlog)}건",
        "route": "/api/ai-hub/readiness",
    }


def _deep_eval_card(report: dict[str, Any]) -> dict[str, Any]:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    total = _int(summary.get("total"))
    passed = _int(summary.get("passed"))
    failed = _int(summary.get("failed"))
    status = str(report.get("status") or "missing")
    value = f"{passed}/{total}" if total else status
    detail = f"failed {failed}"
    if report.get("age_seconds") is not None:
        detail += f" · age {_age_text(_int(report.get('age_seconds')))}"
    elif report.get("message"):
        detail = str(report.get("message"))[:120]
    return {
        "key": "deep_eval",
        "label": "Agent 검증",
        "value": value,
        "tone": _deep_eval_tone(status, failed),
        "detail": detail,
        "route": "/api/ai-hub/deep-eval-report",
    }


def _runbook_card(runbook: dict[str, Any]) -> dict[str, Any]:
    workflows = _runbook_count(runbook, "workflows")
    ready = _runbook_count(runbook, "ready")
    attention = _runbook_count(runbook, "attention")
    blocked = _runbook_count(runbook, "blocked")
    unchecked = _runbook_count(runbook, "unchecked")
    next_actions = _runbook_count(runbook, "next_actions")
    return {
        "key": "workflow_runbook",
        "label": "Workflow Runbook",
        "value": f"{ready}/{workflows}",
        "tone": _runbook_tone(workflows=workflows, blocked=blocked, attention=attention, unchecked=unchecked),
        "detail": f"blocked {blocked} · attention {attention} · unchecked {unchecked} · actions {next_actions}",
        "route": "/api/ai-hub/workflow-runbook",
    }


def _wiki_card(health: dict[str, Any]) -> dict[str, Any]:
    counts = health.get("counts") if isinstance(health.get("counts"), dict) else {}
    docs = _int(counts.get("docs"))
    sources = _int(counts.get("sources"))
    lint = _int(counts.get("lint_issues"))
    nodes = _int(counts.get("graph_nodes"))
    edges = _int(counts.get("graph_edges"))
    return {
        "key": "wiki",
        "label": "Agent Wiki",
        "value": f"{docs} docs",
        "tone": _status_tone(str(health.get("status") or "missing")),
        "detail": f"sources {sources} · lint {lint} · graph {nodes}/{edges}",
        "route": "/api/ai-hub/wiki-health",
    }


def _workflow_map_card(workflow_map: dict[str, Any]) -> dict[str, Any]:
    counts = workflow_map.get("counts") if isinstance(workflow_map.get("counts"), dict) else {}
    warnings = workflow_map.get("warnings") if isinstance(workflow_map.get("warnings"), list) else []
    nodes = _int(counts.get("nodes"))
    edges = _int(counts.get("edges"))
    workflows = _int(counts.get("workflow_templates_visible"))
    tools_visible = _int(counts.get("tools_visible"))
    tools_total = _int(counts.get("tools_total"))
    issue_count = len(warnings)
    return {
        "key": "workflow_map",
        "label": "워크플로우 지도",
        "value": f"{nodes}/{edges}",
        "tone": _workflow_map_tone(warnings),
        "detail": f"workflows {workflows} · tools {tools_visible}/{tools_total} · warnings {issue_count}",
        "route": "/api/ai-hub/workflow-map",
    }


def _timeline_card(timeline: dict[str, Any]) -> dict[str, Any]:
    items = timeline.get("items") if isinstance(timeline.get("items"), list) else []
    counts = timeline.get("counts") if isinstance(timeline.get("counts"), dict) else {}
    detail = ", ".join(f"{key} {value}" for key, value in sorted(counts.items())[:4])
    if not detail:
        detail = "recent events 0"
    return {
        "key": "timeline",
        "label": "운영 이벤트",
        "value": str(len(items)),
        "tone": "neutral",
        "detail": detail,
        "route": "/api/ai-hub/timeline",
    }


def _top_actions(value: Any, *, limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in value if isinstance(value, list) else []:
        if not isinstance(row, dict):
            continue
        rows.append({
            "id": str(row.get("id") or ""),
            "severity": str(row.get("severity") or ""),
            "tone": _severity_tone(str(row.get("severity") or "")),
            "title": str(row.get("title") or ""),
            "target": str(row.get("target") or ""),
            "detail": str(row.get("detail") or "")[:220],
            "action": str(row.get("action") or "")[:220],
            "route": str(row.get("route") or ""),
        })
        if len(rows) >= limit:
            break
    return rows


def _runbook_action_queue(runbook: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    queue = runbook.get("next_action_queue") if isinstance(runbook.get("next_action_queue"), list) else []
    for row in queue:
        if not isinstance(row, dict):
            continue
        workflows = row.get("workflows") if isinstance(row.get("workflows"), list) else []
        rows.append({
            "key": str(row.get("key") or ""),
            "tone": str(row.get("tone") or "neutral"),
            "title": str(row.get("title") or row.get("key") or ""),
            "detail": str(row.get("detail") or "")[:220],
            "route": str(row.get("route") or "/api/ai-hub/workflow-runbook"),
            "count": _int(row.get("count")),
            "workflow_keys": [str(v) for v in (row.get("workflow_keys") if isinstance(row.get("workflow_keys"), list) else [])[:12]],
            "workflows": [
                {
                    "key": str(item.get("key") or ""),
                    "title": str(item.get("title") or item.get("key") or ""),
                    "status": str(item.get("status") or ""),
                    "tone": str(item.get("tone") or ""),
                }
                for item in workflows[:4]
                if isinstance(item, dict)
            ],
        })
        if len(rows) >= limit:
            break
    return rows


def _workflow_map_warnings(workflow_map: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    warnings = workflow_map.get("warnings") if isinstance(workflow_map.get("warnings"), list) else []
    for row in warnings:
        if not isinstance(row, dict):
            continue
        items = row.get("items") if isinstance(row.get("items"), list) else []
        key = str(row.get("key") or "")
        rows.append({
            "key": key,
            "tone": str(row.get("tone") or "neutral"),
            "title": str(row.get("title") or key or "workflow map warning"),
            "message": str(row.get("message") or "")[:220],
            "action": str(row.get("action") or _workflow_warning_action(key))[:220],
            "item_count": len(items),
            "items": [str(item) for item in items[:8]],
            "route": str(row.get("route") or "/api/ai-hub/workflow-map"),
        })
        if len(rows) >= limit:
            break
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


def _workflow_map_count(workflow_map: dict[str, Any], key: str) -> int:
    counts = workflow_map.get("counts") if isinstance(workflow_map.get("counts"), dict) else {}
    return _int(counts.get(key))


def _recent_events(value: Any, *, limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in value if isinstance(value, list) else []:
        if not isinstance(row, dict):
            continue
        rows.append({
            "id": str(row.get("id") or ""),
            "timestamp": str(row.get("timestamp") or ""),
            "category": str(row.get("category") or ""),
            "tone": str(row.get("tone") or "neutral"),
            "title": str(row.get("title") or row.get("action") or ""),
            "username": str(row.get("username") or ""),
            "meta": str(row.get("meta") or ""),
            "detail": str(row.get("detail") or "")[:220],
            "action": str(row.get("action") or ""),
            "workflow_key": str(row.get("workflow_key") or ""),
            "doc_id": str(row.get("doc_id") or ""),
        })
        if len(rows) >= limit:
            break
    return rows


def _export_links(days: int) -> list[dict[str, str]]:
    return [
        {
            "key": "obsidian",
            "label": "운영 ZIP",
            "format": "obsidian",
            "href": f"/api/ai-hub/ops-export/download?format=obsidian&days={days}&limit=40&reference_limit=160",
            "filename": "flow-ai-hub-operations.obsidian.zip",
        },
        {
            "key": "n8n",
            "label": "운영 n8n",
            "format": "n8n",
            "href": f"/api/ai-hub/ops-export/download?format=n8n&days={days}&limit=40",
            "filename": "flow-ai-hub-operations.n8n.json",
        },
    ]


def _overall_status(cards: list[dict[str, Any]]) -> str:
    tones = {str(card.get("tone") or "") for card in cards}
    if "bad" in tones:
        return "bad"
    if "warn" in tones:
        return "warn"
    return "ok"


def _headline(status: str) -> str:
    if status == "ok":
        return "Agent 운영 상태 정상"
    if status == "bad":
        return "Agent 운영 문제 확인 필요"
    return "Agent 운영 점검 필요"


def _score_tone(score: int) -> str:
    if score >= 85:
        return "ok"
    if score >= 65:
        return "warn"
    return "bad"


def _deep_eval_tone(status: str, failed: int) -> str:
    if status == "pass" and failed <= 0:
        return "ok"
    if status in {"missing", "invalid"}:
        return "warn"
    return "bad"


def _workflow_map_tone(warnings: list[Any]) -> str:
    tones = {str(row.get("tone") or "") for row in warnings if isinstance(row, dict)}
    if "bad" in tones:
        return "bad"
    if warnings:
        return "warn"
    return "ok"


def _runbook_tone(*, workflows: int, blocked: int, attention: int, unchecked: int) -> str:
    if blocked > 0:
        return "bad"
    if workflows <= 0 or attention > 0 or unchecked > 0:
        return "warn"
    return "ok"


def _status_tone(status: str) -> str:
    if status == "pass":
        return "ok"
    if status == "missing":
        return "warn"
    if status == "warn":
        return "warn"
    return "bad"


def _severity_tone(severity: str) -> str:
    if severity == "high":
        return "bad"
    if severity == "medium":
        return "warn"
    return "neutral"


def _age_text(seconds: int) -> str:
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _runbook_count(runbook: dict[str, Any], key: str) -> int:
    counts = runbook.get("counts") if isinstance(runbook.get("counts"), dict) else {}
    return _int(counts.get(key))
