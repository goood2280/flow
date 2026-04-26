from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


TaskStatus = Literal["pending", "running", "completed", "failed", "skipped"]
TaskKind = Literal[
    "data_adapter",
    "source_contract_review",
    "step_auto_classification",
    "clean_split_review",
    "process_ml_review",
    "et_report_review",
    "plan_generation",
    "publish_mail",
    "publish_json",
]


class AgentTaskRequest(BaseModel):
    version: str = "1.0"
    run_id: str = ""
    task_id: str = ""
    task_kind: TaskKind
    title: str = ""
    product: str = ""
    root_lot_id: str = ""
    fab_lot_id: str = ""
    wafer_id: str = ""
    target_y: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentTaskResult(BaseModel):
    version: str = "1.0"
    run_id: str = ""
    task_id: str = ""
    task_kind: TaskKind
    status: TaskStatus = "completed"
    summary: str = ""
    findings: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    next_tasks: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)


class ActionTarget(BaseModel):
    step_id: str = ""
    function_step: str = ""
    module: str = ""
    parameter: str = ""
    current_value: str = ""
    planned_value: str = ""


class ActionProposal(BaseModel):
    version: str = "1.0"
    action_type: Literal["apply_knob_plan", "publish_report", "create_inform", "post_external_json"] = "apply_knob_plan"
    product: str = ""
    root_lot_id: str = ""
    fab_lot_id: str = ""
    target_y: str = ""
    confidence: float = 0.0
    evidence: list[str] = Field(default_factory=list)
    targets: list[ActionTarget] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)


class OrchestrationPlan(BaseModel):
    version: str = "1.0"
    run_id: str = ""
    goal: str = ""
    tasks: list[AgentTaskRequest] = Field(default_factory=list)
    actions: list[ActionProposal] = Field(default_factory=list)


class OrchestrationRun(BaseModel):
    version: str = "1.0"
    run_id: str = ""
    goal: str = ""
    status: TaskStatus = "pending"
    context: dict[str, Any] = Field(default_factory=dict)
    plan: OrchestrationPlan | None = None
    results: list[AgentTaskResult] = Field(default_factory=list)
