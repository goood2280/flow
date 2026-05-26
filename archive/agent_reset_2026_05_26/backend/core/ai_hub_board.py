"""AI Hub operations board.

Read-only aggregation for the AI Hub top surface. It intentionally reuses the
existing stores instead of creating another queue:

- tool_registry_state.json for enabled/disabled tools
- skills/_candidates for skill mining approvals
- semantic/proposals for semantic learning approvals
- flowi_workflow_templates for saved workflow templates
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app_v2.modules.semantic_learning import inbox as semantic_inbox
from core import audit
from core import flowi_workflow_templates as workflow_templates
from core import skills_repo, tool_registry


def build_board(*, username: str = "", days: int = 30, limit: int = 8) -> dict[str, Any]:
    days = max(1, min(365, int(days or 30)))
    limit = max(1, min(50, int(limit or 8)))

    tools = tool_registry.list_tools(include_stats=True, days=days)
    disabled_tools = [row for row in tools if not row.get("enabled")]
    skill_candidates = skills_repo.list_candidates()
    skills = skills_repo.list_skills()
    workflows = workflow_templates.list_templates(username, include_shared=True)
    semantic_proposals = semantic_inbox.list_proposals(status="pending", limit=200)
    workflow_runs = _workflow_run_items(days=days, limit=limit)

    shared_workflows = [row for row in workflows if row.get("shared")]
    personal_workflows = [row for row in workflows if not row.get("shared")]
    enabled_tools = [row for row in tools if row.get("enabled")]
    workflow_run_failures = [
        row for row in workflow_runs
        if row.get("tone") in {"bad", "warn"}
    ]

    counts = {
        "tools_total": len(tools),
        "tools_enabled": len(enabled_tools),
        "tools_disabled": len(disabled_tools),
        "skill_candidates": len(skill_candidates),
        "skills": len(skills),
        "workflows": len(workflows),
        "shared_workflows": len(shared_workflows),
        "personal_workflows": len(personal_workflows),
        "semantic_proposals_pending": len(semantic_proposals),
        "workflow_runs_recent": len(workflow_runs),
        "workflow_run_warnings": len(workflow_run_failures),
    }

    lanes = [
        {
            "id": "semantic_proposals",
            "title": "시멘틱 제안",
            "count": counts["semantic_proposals_pending"],
            "tone": "warn" if semantic_proposals else "ok",
            "target": "/api/agent/semantic/proposals",
            "items": [_semantic_item(row) for row in semantic_proposals[:limit]],
        },
        {
            "id": "skill_candidates",
            "title": "스킬 후보",
            "count": counts["skill_candidates"],
            "tone": "warn" if skill_candidates else "ok",
            "target": "/api/skills/candidates",
            "items": [_skill_candidate_item(row) for row in _recent(skill_candidates, limit)],
        },
        {
            "id": "workflow_templates",
            "title": "워크플로우",
            "count": counts["workflows"],
            "tone": "info" if workflows else "neutral",
            "target": "/api/agent/workflows",
            "items": [_workflow_item(row) for row in _recent(workflows, limit)],
        },
        {
            "id": "workflow_runs",
            "title": "검증 이력",
            "count": counts["workflow_runs_recent"],
            "tone": "warn" if workflow_run_failures else ("info" if workflow_runs else "neutral"),
            "target": "/api/agent/workflows/execute",
            "items": workflow_runs,
        },
        {
            "id": "disabled_tools",
            "title": "비활성 도구",
            "count": counts["tools_disabled"],
            "tone": "bad" if disabled_tools else "ok",
            "target": "/api/ai-hub/tools",
            "items": [_tool_item(row) for row in disabled_tools[:limit]],
        },
    ]

    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": days,
        "limit": limit,
        "counts": counts,
        "health": [
            {
                "key": "tools",
                "label": "도구 활성",
                "value": f"{counts['tools_enabled']}/{counts['tools_total']}",
                "tone": "bad" if disabled_tools else "ok",
            },
            {
                "key": "semantic_proposals",
                "label": "시멘틱 대기",
                "value": counts["semantic_proposals_pending"],
                "tone": "warn" if semantic_proposals else "ok",
            },
            {
                "key": "skill_candidates",
                "label": "스킬 후보",
                "value": counts["skill_candidates"],
                "tone": "warn" if skill_candidates else "ok",
            },
            {
                "key": "workflows",
                "label": "워크플로우",
                "value": counts["workflows"],
                "tone": "info" if workflows else "neutral",
            },
            {
                "key": "workflow_runs",
                "label": "검증 이력",
                "value": counts["workflow_runs_recent"],
                "tone": "warn" if workflow_run_failures else ("info" if workflow_runs else "neutral"),
            },
        ],
        "lanes": lanes,
    }


def _recent(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    return sorted(
        [row for row in rows if isinstance(row, dict)],
        key=lambda row: str(row.get("updated_at") or row.get("last_seen") or row.get("created_at") or ""),
        reverse=True,
    )[:limit]


def _semantic_item(row: dict[str, Any]) -> dict[str, Any]:
    origin = row.get("origin") if isinstance(row.get("origin"), dict) else {}
    proposal_id = str(row.get("id") or "")
    return {
        "id": proposal_id,
        "title": str(row.get("term") or ""),
        "status": str(row.get("status") or "pending"),
        "meta": str(row.get("category") or ""),
        "detail": str(row.get("rationale") or origin.get("ref") or ""),
        "updated_at": str(row.get("updated_at") or row.get("created_at") or ""),
        "actions": [
            {
                "id": "approve",
                "label": "승인",
                "tone": "ok",
                "method": "POST",
                "endpoint": "/api/agent/semantic/proposals/decide",
                "body": {"id": proposal_id, "status": "approved"},
            },
            {
                "id": "reject",
                "label": "거부",
                "tone": "bad",
                "method": "POST",
                "endpoint": "/api/agent/semantic/proposals/decide",
                "body": {"id": proposal_id, "status": "rejected"},
                "confirm": True,
            },
        ] if proposal_id else [],
    }


def _skill_candidate_item(row: dict[str, Any]) -> dict[str, Any]:
    key = str(row.get("key") or "")
    return {
        "id": key,
        "title": str(row.get("title") or row.get("key") or ""),
        "status": "pending",
        "meta": f"freq {row.get('freq') or 0} · users {len(row.get('users') or [])}",
        "detail": str(row.get("description") or ""),
        "updated_at": str(row.get("last_seen") or row.get("created_at") or ""),
        "actions": [
            {
                "id": "approve",
                "label": "승인",
                "tone": "ok",
                "method": "POST",
                "endpoint": f"/api/skills/candidates/{key}/approve",
                "body": {"title": str(row.get("title") or key)},
            },
            {
                "id": "reject",
                "label": "거부",
                "tone": "bad",
                "method": "POST",
                "endpoint": f"/api/skills/candidates/{key}/reject",
                "body": {},
                "confirm": True,
            },
        ] if key else [],
    }


def _workflow_item(row: dict[str, Any]) -> dict[str, Any]:
    trigger = row.get("trigger") if isinstance(row.get("trigger"), dict) else {}
    prompt_contains = [str(v) for v in (trigger.get("prompt_contains") or []) if str(v).strip()]
    return {
        "id": str(row.get("key") or ""),
        "title": str(row.get("title") or row.get("key") or ""),
        "status": "shared" if row.get("shared") else "personal",
        "meta": f"{len(row.get('steps') or [])} steps",
        "detail": ", ".join(prompt_contains[:4]),
        "updated_at": str(row.get("updated_at") or row.get("created_at") or ""),
    }


def _workflow_run_items(*, days: int, limit: int) -> list[dict[str, Any]]:
    log = audit.ACTIVITY_LOG
    if not log.exists():
        return []
    cutoff = datetime.now(timezone.utc).timestamp() - max(1, days) * 86400
    rows: list[dict[str, Any]] = []
    try:
        lines = log.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in reversed(lines):
        if len(rows) >= limit:
            break
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
        rows.append(_workflow_run_item(rec))
    return rows


def _workflow_run_item(rec: dict[str, Any]) -> dict[str, Any]:
    action = str(rec.get("action") or "")
    key = action.split("ai_hub_run:workflow:", 1)[-1] if "ai_hub_run:workflow:" in action else action
    detail = _json_detail(rec.get("detail"))
    statuses = detail.get("statuses") if isinstance(detail.get("statuses"), dict) else {}
    status_text = ", ".join(f"{k}:{v}" for k, v in statuses.items()) or str(rec.get("detail") or "")
    warn_count = sum(int(statuses.get(k) or 0) for k in ("error", "blocked", "missing_slots", "confirm_required", "no_handler"))
    dry_run = bool(detail.get("dry_run"))
    steps = int(detail.get("steps") or 0)
    return {
        "id": f"{rec.get('timestamp') or ''}:{key}",
        "title": str(detail.get("title") or key),
        "workflow_key": str(detail.get("workflow") or key),
        "status": "dry-run" if dry_run else "executed",
        "meta": f"{steps} steps",
        "detail": status_text,
        "updated_at": str(rec.get("timestamp") or ""),
        "tone": "warn" if warn_count else "info",
    }


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


def _tool_item(row: dict[str, Any]) -> dict[str, Any]:
    name = str(row.get("name") or "")
    return {
        "id": name,
        "title": str(row.get("title") or row.get("name") or ""),
        "status": "disabled",
        "meta": str(row.get("kind") or ""),
        "detail": str(row.get("description") or ""),
        "updated_at": str(row.get("last_run") or ""),
        "actions": [
            {
                "id": "enable",
                "label": "활성화",
                "tone": "ok",
                "method": "POST",
                "endpoint": f"/api/ai-hub/tools/{name}/toggle",
                "body": {"enabled": True},
            },
        ] if name else [],
    }
