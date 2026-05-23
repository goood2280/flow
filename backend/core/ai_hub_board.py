"""AI Hub operations board.

Read-only aggregation for the AI Hub top surface. It intentionally reuses the
existing stores instead of creating another queue:

- tool_registry_state.json for enabled/disabled tools
- skills/_candidates for skill mining approvals
- semantic/proposals for semantic learning approvals
- flowi_workflow_templates for saved workflow templates
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app_v2.modules.semantic_learning import inbox as semantic_inbox
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

    shared_workflows = [row for row in workflows if row.get("shared")]
    personal_workflows = [row for row in workflows if not row.get("shared")]
    enabled_tools = [row for row in tools if row.get("enabled")]

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
    return {
        "id": str(row.get("id") or ""),
        "title": str(row.get("term") or ""),
        "status": str(row.get("status") or "pending"),
        "meta": str(row.get("category") or ""),
        "detail": str(row.get("rationale") or origin.get("ref") or ""),
        "updated_at": str(row.get("updated_at") or row.get("created_at") or ""),
    }


def _skill_candidate_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("key") or ""),
        "title": str(row.get("title") or row.get("key") or ""),
        "status": "pending",
        "meta": f"freq {row.get('freq') or 0} · users {len(row.get('users') or [])}",
        "detail": str(row.get("description") or ""),
        "updated_at": str(row.get("last_seen") or row.get("created_at") or ""),
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


def _tool_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("name") or ""),
        "title": str(row.get("title") or row.get("name") or ""),
        "status": "disabled",
        "meta": str(row.get("kind") or ""),
        "detail": str(row.get("description") or ""),
        "updated_at": str(row.get("last_run") or ""),
    }
