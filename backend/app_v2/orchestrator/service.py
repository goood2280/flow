from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from .registry import AGENT_REGISTRY
from .schemas import (
    ActionProposal,
    AgentTaskRequest,
    AgentTaskResult,
    OrchestrationPlan,
    OrchestrationRun,
)


def _new_run_id() -> str:
    return "orc_" + dt.datetime.now().strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:8]


class OrchestratorService:
    """Scaffold for future internal-API / local-agent orchestration.

    Current role:
    - create explicit plans
    - keep JSON contracts stable
    - allow local deterministic services + future weak-model agents to share one protocol
    """

    def build_lot_analysis_plan(
        self,
        *,
        product: str,
        root_lot_id: str = "",
        fab_lot_id: str = "",
        target_y: str = "",
    ) -> OrchestrationRun:
        run_id = _new_run_id()
        tasks = [
            AgentTaskRequest(
                run_id=run_id,
                task_id=f"{run_id}_t1",
                task_kind="data_adapter",
                title="Normalize current product/lot datasets",
                product=product,
                root_lot_id=root_lot_id,
                fab_lot_id=fab_lot_id,
                target_y=target_y,
            ),
            AgentTaskRequest(
                run_id=run_id,
                task_id=f"{run_id}_t2",
                task_kind="source_contract_review",
                title="Validate source contracts and join keys",
                product=product,
                root_lot_id=root_lot_id,
                fab_lot_id=fab_lot_id,
                target_y=target_y,
            ),
            AgentTaskRequest(
                run_id=run_id,
                task_id=f"{run_id}_t3",
                task_kind="step_auto_classification",
                title="Auto-classify raw step_id into function_step/module",
                product=product,
                root_lot_id=root_lot_id,
                fab_lot_id=fab_lot_id,
                target_y=target_y,
            ),
            AgentTaskRequest(
                run_id=run_id,
                task_id=f"{run_id}_t4",
                task_kind="clean_split_review",
                title="Review clean split quality inside lots/modules",
                product=product,
                root_lot_id=root_lot_id,
                fab_lot_id=fab_lot_id,
                target_y=target_y,
            ),
            AgentTaskRequest(
                run_id=run_id,
                task_id=f"{run_id}_t5",
                task_kind="process_ml_review",
                title="Rank reliable upstream/process features",
                product=product,
                root_lot_id=root_lot_id,
                fab_lot_id=fab_lot_id,
                target_y=target_y,
            ),
            AgentTaskRequest(
                run_id=run_id,
                task_id=f"{run_id}_t6",
                task_kind="et_report_review",
                title="Summarize ET report/time and seq bottlenecks",
                product=product,
                root_lot_id=root_lot_id,
                fab_lot_id=fab_lot_id,
                target_y=target_y,
            ),
            AgentTaskRequest(
                run_id=run_id,
                task_id=f"{run_id}_t7",
                task_kind="plan_generation",
                title="Generate step-ready knob/action plan",
                product=product,
                root_lot_id=root_lot_id,
                fab_lot_id=fab_lot_id,
                target_y=target_y,
            ),
            AgentTaskRequest(
                run_id=run_id,
                task_id=f"{run_id}_t8",
                task_kind="publish_mail",
                title="Prepare mail payload",
                product=product,
                root_lot_id=root_lot_id,
                fab_lot_id=fab_lot_id,
                target_y=target_y,
            ),
            AgentTaskRequest(
                run_id=run_id,
                task_id=f"{run_id}_t9",
                task_kind="publish_json",
                title="Prepare JSON payload for internal system API",
                product=product,
                root_lot_id=root_lot_id,
                fab_lot_id=fab_lot_id,
                target_y=target_y,
            ),
        ]
        plan = OrchestrationPlan(
            run_id=run_id,
            goal="Analyze process flow, find reliable knob evidence, and generate action-ready outputs.",
            tasks=tasks,
            actions=[],
        )
        return OrchestrationRun(
            run_id=run_id,
            goal=plan.goal,
            status="pending",
            context={
                "product": product,
                "root_lot_id": root_lot_id,
                "fab_lot_id": fab_lot_id,
                "target_y": target_y,
            },
            plan=plan,
            results=[],
        )

    def stub_result(self, task: AgentTaskRequest, *, summary: str = "", output: dict[str, Any] | None = None) -> AgentTaskResult:
        return AgentTaskResult(
            run_id=task.run_id,
            task_id=task.task_id,
            task_kind=task.task_kind,
            status="completed",
            summary=summary or AGENT_REGISTRY[task.task_kind]["purpose"],
            output=output or {},
        )

    def build_action_proposal(
        self,
        *,
        product: str,
        root_lot_id: str = "",
        fab_lot_id: str = "",
        target_y: str = "",
        confidence: float = 0.0,
        evidence: list[str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> ActionProposal:
        return ActionProposal(
            product=product,
            root_lot_id=root_lot_id,
            fab_lot_id=fab_lot_id,
            target_y=target_y,
            confidence=confidence,
            evidence=evidence or [],
            payload=payload or {},
        )
