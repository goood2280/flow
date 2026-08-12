"""Fail-closed Agent action planner after the Agent reset."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ArchivedPlan:
    agent_id: str = "agent_archived"
    unit_ai: str = "agent_runtime"
    action: str = "archived"
    title: str = "Agent Archived"
    policy: str = "blocked"
    endpoint: str = "archive/agent_reset_2026_05_26"
    approval_required: bool = False

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


def action_policy_for(unit_ai: str = "", action: str = "", **_: Any) -> dict[str, Any]:
    return {
        "policy": "blocked",
        "approval_required": False,
        "reason": "agent_runtime_archived",
    }


def build_action_plans(goal: str = "", semantic: dict[str, Any] | None = None, username: str = "", **_: Any) -> tuple[list[ArchivedPlan], dict[str, Any]]:
    plan = ArchivedPlan()
    return [plan], {"guardrail": guardrail_summary_from_plans([plan])}


def compact_plan_rows(plans: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for plan in plans or []:
        if hasattr(plan, "model_dump"):
            rows.append(plan.model_dump())
        elif isinstance(plan, dict):
            rows.append(dict(plan))
    return rows


def guardrail_summary_from_plans(plans: list[Any]) -> dict[str, Any]:
    return {
        "status": "archived",
        "blocked": True,
        "approval_required": False,
        "reason": "agent_runtime_archived",
        "plan_count": len(plans or []),
    }
