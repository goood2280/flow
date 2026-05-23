from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


EventStatus = Literal["pending", "running", "completed", "failed", "skipped"]
RunStatus = Literal["queued", "running", "completed", "failed"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SemanticResolveRequest(BaseModel):
    goal: str = Field(default="", min_length=1, max_length=4000)
    max_terms: int = Field(default=24, ge=1, le=80)


class SemanticCandidate(BaseModel):
    token: str = ""
    normalized: str = ""
    relation_id: str = ""
    column: str = ""
    canonical_alias: str = ""
    meaning: str = ""
    source: str = ""
    unit: str = ""
    sample_values: list[Any] = Field(default_factory=list)
    used_by: list[str] = Field(default_factory=list)
    wiki_doc_id: str = ""
    score: float = 0.0


class ActivationRow(BaseModel):
    """One scored token→column hit from the semantic resolver.

    Surfaces in `ThoughtTrace.activation_table` so the UI can show *why* a
    user word ended up bound to a specific column/source.
    """
    token: str = ""
    normalized: str = ""
    relation_id: str = ""
    column: str = ""
    canonical_alias: str = ""
    source: str = ""
    score: float = 0.0
    used_by: list[str] = Field(default_factory=list)


class IntentDecision(BaseModel):
    """Score breakdown of `_INTENT_HINTS` evaluation for one prompt."""
    chosen: str = "general_orchestration"
    rationale: str = ""
    scores: list[dict[str, Any]] = Field(default_factory=list)


class UnitAiSelection(BaseModel):
    """One row of the dispatcher's "which unit AI did we try" log.

    Populated by `_run_flowi_chat` when it calls `try_dispatch`. Status is
    `delegated` when the unit AI handled the request, `skipped` when its
    `handle()` returned None.
    """
    key: str = ""
    title: str = ""
    status: str = "skipped"
    reason: str = ""


class LlmCallSummary(BaseModel):
    """Safe summary of one LLM call. Never contains prompt/response text."""
    invoked: bool = False
    ok: bool = False
    model: str = ""
    profile: str = ""
    provider: str = ""
    prompt_chars: int = 0
    response_chars: int = 0
    latency_ms: int = 0
    error: str = ""


class ThoughtTrace(BaseModel):
    """Per-prompt sketch of how the agent thought through term resolution.

    The Agent tab's ExecutionFlowTab renders this so users can see which
    tokens activated which columns/Wiki nodes, how intent was chosen, and
    which unit AI ended up handling the request.
    """
    activation_table: list[ActivationRow] = Field(default_factory=list)
    intent_decision: IntentDecision = Field(default_factory=IntentDecision)
    slot_extraction: dict[str, Any] = Field(default_factory=dict)
    unit_ai_selection: list[UnitAiSelection] = Field(default_factory=list)
    llm_call_summary: LlmCallSummary = Field(default_factory=LlmCallSummary)


class SemanticFrame(BaseModel):
    goal: str = ""
    tokens: list[str] = Field(default_factory=list)
    normalized_terms: dict[str, str] = Field(default_factory=dict)
    intent: str = "general_orchestration"
    slots: dict[str, Any] = Field(default_factory=dict)
    candidates: list[SemanticCandidate] = Field(default_factory=list)
    coverage: float = 0.0
    warnings: list[str] = Field(default_factory=list)
    polars_profile: dict[str, Any] = Field(default_factory=dict)
    thought: ThoughtTrace = Field(default_factory=ThoughtTrace)


class UnitAgentSpec(BaseModel):
    agent_id: str
    title: str
    role: str
    outputs: list[str] = Field(default_factory=list)


class UnitAgentPlan(BaseModel):
    agent_id: str
    title: str
    status: EventStatus = "pending"
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    unit_ai: str = ""
    action: str = ""
    policy: str = "read_only"
    approval_required: bool = False
    endpoint: str = ""
    missing_slots: list[str] = Field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)


class UnitAgentResult(BaseModel):
    agent_id: str
    status: EventStatus = "completed"
    summary: str = ""
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    handled: bool = False
    guardrail: dict[str, Any] = Field(default_factory=dict)
    tool: dict[str, Any] = Field(default_factory=dict)
    table: dict[str, Any] = Field(default_factory=dict)
    chart_result: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class AgentRuntimeRequest(SemanticResolveRequest):
    context: dict[str, Any] = Field(default_factory=dict)
    use_llm: bool = False
    unit_ai_scope: str = ""


class AgentRuntimeEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    event_id: str = ""
    event: str = "status"
    run_id: str = ""
    stage: str = ""
    agent_id: str = ""
    unit_ai: str = ""
    action: str = ""
    status: EventStatus = "running"
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    ts: str = Field(default_factory=utc_now)


class AgentRuntimeResult(BaseModel):
    run_id: str = ""
    status: RunStatus = "completed"
    goal: str = ""
    semantic: SemanticFrame = Field(default_factory=SemanticFrame)
    plan: list[UnitAgentPlan] = Field(default_factory=list)
    results: list[UnitAgentResult] = Field(default_factory=list)
    conclusion: dict[str, Any] = Field(default_factory=dict)
    events: list[AgentRuntimeEvent] = Field(default_factory=list)
    langsmith: dict[str, Any] = Field(default_factory=dict)
    langgraph: dict[str, Any] = Field(default_factory=dict)
