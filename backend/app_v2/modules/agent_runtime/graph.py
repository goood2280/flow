from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import Any, TypedDict

from core import llm_adapter

from .actions import (
    build_action_blueprint,
    build_action_plans,
    compact_plan_rows,
    execute_action_plan,
    guardrail_summary_from_plans,
)
from .schemas import (
    AgentRuntimeEvent,
    AgentRuntimeRequest,
    AgentRuntimeResult,
    UnitAgentPlan,
    UnitAgentResult,
    UnitAgentSpec,
    utc_now,
)
from .semantic import resolve_semantic_frame

try:
    from langgraph.graph import END, StateGraph

    LANGGRAPH_AVAILABLE = True
except Exception:
    END = None
    StateGraph = None
    LANGGRAPH_AVAILABLE = False

try:
    from langsmith import traceable as _langsmith_traceable

    LANGSMITH_AVAILABLE = True
except Exception:
    _langsmith_traceable = None
    LANGSMITH_AVAILABLE = False


class RuntimeState(TypedDict, total=False):
    run_id: str
    goal: str
    username: str
    context: dict[str, Any]
    use_llm: bool
    max_terms: int
    semantic: dict[str, Any]
    plan: list[dict[str, Any]]
    results: list[dict[str, Any]]
    conclusion: dict[str, Any]
    events: list[dict[str, Any]]
    status: str


UNIT_AGENT_SPECS = [
    UnitAgentSpec(
        agent_id="semantic_interpreter",
        title="Semantic Interpreter",
        role="Normalize user words into product, lot, wafer, step, source, and column terms.",
        outputs=["semantic frame", "slot map", "column candidates"],
    ),
    UnitAgentSpec(
        agent_id="task_planner",
        title="Task Planner",
        role="Convert the abstract goal into ordered unit-agent work.",
        outputs=["unit-agent plan", "dependency order"],
    ),
    UnitAgentSpec(
        agent_id="data_contract",
        title="Data Contract Agent",
        role="Check source, schema, join-key, and data-readiness assumptions before execution.",
        outputs=["contract warnings", "required sources"],
    ),
    UnitAgentSpec(
        agent_id="executor",
        title="Execution Agent",
        role="Call allowed Flow feature APIs or prepare a read-only execution package.",
        outputs=["tool payloads", "execution artifacts"],
    ),
    UnitAgentSpec(
        agent_id="critic",
        title="Critic Agent",
        role="Review ambiguity, missing slots, and unsafe assumptions.",
        outputs=["risk review", "clarifying questions"],
    ),
    UnitAgentSpec(
        agent_id="conclusion",
        title="Conclusion Agent",
        role="Produce the final result summary and next action.",
        outputs=["final answer", "next actions"],
    ),
]


def _traceable(*args, **kwargs):
    if _langsmith_traceable is not None:
        return _langsmith_traceable(*args, **kwargs)

    def decorator(fn):
        return fn

    return decorator


def _new_run_id() -> str:
    return "agent_" + uuid.uuid4().hex[:12]


def _event(run_id: str, stage: str, status: str, message: str, data: dict[str, Any] | None = None, event: str = "status") -> dict[str, Any]:
    return AgentRuntimeEvent(
        event_id=uuid.uuid4().hex[:12],
        event=event,
        run_id=run_id,
        stage=stage,
        status=status,  # type: ignore[arg-type]
        message=message,
        data=data or {},
        ts=utc_now(),
    ).model_dump()


def _append_event(state: RuntimeState, stage: str, status: str, message: str, data: dict[str, Any] | None = None, event: str = "status") -> list[dict[str, Any]]:
    events = list(state.get("events") or [])
    events.append(_event(str(state.get("run_id") or ""), stage, status, message, data, event))
    return events


def _append_to_events(events: list[dict[str, Any]], state: RuntimeState, stage: str, status: str, message: str, data: dict[str, Any] | None = None, event: str = "status") -> None:
    events.append(_event(str(state.get("run_id") or ""), stage, status, message, data, event))


def _base_plan_rows(semantic: dict[str, Any]) -> list[UnitAgentPlan]:
    intent = str(semantic.get("intent") or "general_orchestration")
    return [
        UnitAgentPlan(
            agent_id="semantic_interpreter",
            title="Semantic Interpreter",
            status="completed",
            inputs={"goal": semantic.get("goal") or ""},
            outputs=["semantic frame", "slot map", "column candidates"],
            unit_ai="agent_runtime",
            action="resolve_semantic",
            policy="read_only",
            endpoint="backend/app_v2/modules/agent_runtime/semantic.py",
        ),
        UnitAgentPlan(
            agent_id="task_planner",
            title="Task Planner",
            status="completed",
            inputs={"intent": intent, "slots": semantic.get("slots") or {}},
            outputs=["unit action plan", "policy", "guardrail"],
            depends_on=["semantic_interpreter"],
            unit_ai="agent_runtime",
            action="plan",
            policy="read_only",
            endpoint="backend/app_v2/modules/agent_runtime/actions.py",
        ),
    ]


def _final_plan_rows() -> list[UnitAgentPlan]:
    return [
        UnitAgentPlan(
            agent_id="critic",
            title="Critic Agent",
            inputs={},
            outputs=["risk review", "missing slots", "approval needs"],
            unit_ai="agent_runtime",
            action="review_guardrail",
            policy="read_only",
        ),
        UnitAgentPlan(
            agent_id="conclusion",
            title="Conclusion Agent",
            inputs={},
            outputs=["final answer", "next actions"],
            depends_on=["critic"],
            unit_ai="agent_runtime",
            action="conclude",
            policy="read_only",
        ),
    ]


def _with_unit_selection(semantic: dict[str, Any], plans: list[UnitAgentPlan]) -> dict[str, Any]:
    out = dict(semantic or {})
    thought = dict(out.get("thought") or {})
    selections = []
    for plan in plans:
        if plan.unit_ai in {"", "agent_runtime"} and plan.action in {"resolve_semantic", "plan", "review_guardrail", "conclude"}:
            continue
        selections.append({
            "key": plan.unit_ai,
            "title": plan.title,
            "status": "approval_required" if plan.approval_required else ("blocked" if plan.policy == "blocked" else "planned"),
            "reason": f"{plan.action} · {plan.policy}",
        })
    thought["unit_ai_selection"] = selections
    out["thought"] = thought
    return out


def langsmith_status() -> dict[str, Any]:
    tracing_raw = os.environ.get("LANGSMITH_TRACING") or os.environ.get("LANGCHAIN_TRACING_V2") or ""
    project = os.environ.get("LANGSMITH_PROJECT") or os.environ.get("LANGCHAIN_PROJECT") or "flow-agent-runtime"
    endpoint = os.environ.get("LANGSMITH_ENDPOINT") or os.environ.get("LANGCHAIN_ENDPOINT") or "https://api.smith.langchain.com"
    return {
        "available": LANGSMITH_AVAILABLE,
        "enabled": LANGSMITH_AVAILABLE and tracing_raw.lower() in {"1", "true", "yes", "on"},
        "project": project,
        "endpoint": endpoint,
        "api_key_configured": bool(os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY")),
    }


def langgraph_status() -> dict[str, Any]:
    return {
        "available": LANGGRAPH_AVAILABLE,
        "stream_mode": "updates",
        "fallback": not LANGGRAPH_AVAILABLE,
    }


def _select_plan(state: RuntimeState) -> list[UnitAgentPlan]:
    semantic = state.get("semantic") or {}
    action_plans, _meta = build_action_plans(
        goal=str(state.get("goal") or semantic.get("goal") or ""),
        semantic=semantic,
        username=str(state.get("username") or ""),
    )
    plans = _base_plan_rows(semantic) + action_plans + _final_plan_rows()
    previous = ""
    for plan in plans:
        if not plan.depends_on and previous:
            plan.depends_on = [previous]
        previous = plan.agent_id
    return plans


@_traceable(name="flow.agent.semantic_layer", run_type="chain")
async def semantic_node(state: RuntimeState) -> RuntimeState:
    frame = resolve_semantic_frame(str(state.get("goal") or ""), max_terms=int(state.get("max_terms") or 24))
    events = _append_event(
        state,
        "semantic_layer",
        "completed",
        f"semantic coverage {int(frame.coverage * 100)}%, intent={frame.intent}",
        {
            "intent": frame.intent,
            "coverage": frame.coverage,
            "tokens": frame.tokens,
            "candidate_count": len(frame.candidates),
            "warnings": frame.warnings,
            "semantic": frame.model_dump(mode="json"),
        },
    )
    return {"semantic": frame.model_dump(), "events": events, "status": "running"}


@_traceable(name="flow.agent.task_planner", run_type="chain")
async def planning_node(state: RuntimeState) -> RuntimeState:
    plan = _select_plan(state)
    semantic = _with_unit_selection(state.get("semantic") or {}, plan)
    action_rows = compact_plan_rows(plan)
    guardrail = guardrail_summary_from_plans(plan)
    events = _append_event(
        state,
        "task_planner",
        "completed",
        f"{len(action_rows)} unit actions planned",
        {
            "agents": [p.agent_id for p in plan],
            "plan": [p.model_dump(mode="json") for p in plan],
            "actions": action_rows,
            "guardrail": guardrail,
        },
    )
    return {"semantic": semantic, "plan": [p.model_dump() for p in plan], "events": events, "status": "running"}


@_traceable(name="flow.agent.unit_execution", run_type="chain")
async def execution_node(state: RuntimeState) -> RuntimeState:
    await asyncio.sleep(0)
    semantic = state.get("semantic") or {}
    results: list[UnitAgentResult] = []
    events = list(state.get("events") or [])
    for raw_plan in state.get("plan") or []:
        plan = UnitAgentPlan(**raw_plan) if isinstance(raw_plan, dict) else raw_plan
        if not isinstance(plan, UnitAgentPlan):
            continue
        if not plan.unit_ai or plan.unit_ai == "agent_runtime":
            continue
        _append_to_events(
            events,
            state,
            "unit_agent_start",
            "running",
            f"{plan.unit_ai}.{plan.action} policy={plan.policy}",
            {"plan": plan.model_dump(mode="json")},
        )
        result = await asyncio.to_thread(
            execute_action_plan,
            plan,
            goal=str(state.get("goal") or ""),
            semantic=semantic,
            username=str(state.get("username") or ""),
            context=state.get("context") if isinstance(state.get("context"), dict) else {},
        )
        results.append(result)
        if result.guardrail.get("status") == "approval_required":
            _append_to_events(
                events,
                state,
                "approval_required",
                "completed",
                f"{plan.unit_ai}.{plan.action} requires approval",
                {"plan": plan.model_dump(mode="json"), "result": result.model_dump(mode="json")},
            )
        _append_to_events(
            events,
            state,
            "unit_agent_done",
            result.status,
            result.summary or f"{plan.unit_ai}.{plan.action} completed",
            {"plan": plan.model_dump(mode="json"), "result": result.model_dump(mode="json")},
        )
    _append_to_events(
        events,
        state,
        "unit_agents",
        "completed",
        f"{len(results)} unit-agent result(s) prepared",
        {
            "results": [r.model_dump(mode="json") for r in results],
            "guardrail": guardrail_summary_from_plans(state.get("plan") or []),
        },
    )
    return {"results": [r.model_dump() for r in results], "events": events, "status": "running"}


def _deterministic_conclusion(state: RuntimeState) -> dict[str, Any]:
    semantic = state.get("semantic") or {}
    slots = semantic.get("slots") if isinstance(semantic.get("slots"), dict) else {}
    warnings = list(semantic.get("warnings") or [])
    results = state.get("results") if isinstance(state.get("results"), list) else []
    plan = state.get("plan") if isinstance(state.get("plan"), list) else []
    guardrail = guardrail_summary_from_plans(plan)
    missing = list(guardrail.get("missing_slots") or [])
    if not any(slots.get(key) for key in ("products", "root_lot_ids", "fab_lot_ids")):
        if "product_or_lot" not in missing:
            missing.append("product_or_lot")
    if semantic.get("intent") in {"filebrowser_ai_sql", "knob_analysis", "chart_analysis"} and not semantic.get("candidates"):
        missing.append("column_or_metric")
    handled = [row for row in results if isinstance(row, dict) and row.get("handled")]
    approvals = [row for row in results if isinstance(row, dict) and (row.get("guardrail") or {}).get("status") == "approval_required"]
    blocked = [row for row in results if isinstance(row, dict) and (row.get("guardrail") or {}).get("status") == "blocked"]
    next_actions = [
        "missing slot이 있으면 product/lot/wafer/step을 보완한 뒤 다시 실행",
        "approval_required 작업은 해당 기능 화면의 확인 절차로 넘겨 실행",
        "semantic coverage가 낮으면 Agent 탭에서 alias/column doc을 보강",
    ]
    if missing:
        warnings.append("missing: " + ", ".join(missing))
    if approvals:
        warnings.append("approval_required: " + str(len(approvals)))
    if blocked:
        warnings.append("blocked_by_policy: " + str(len(blocked)))
    if blocked:
        answer = "요청에 raw DB/file 직접 수정 성격이 있어 실행을 차단했습니다. Flow deterministic API의 승인 절차로만 처리할 수 있습니다."
    elif approvals:
        answer = "저장성 작업은 자동 실행하지 않았습니다. 승인 제안을 만들었고, 기능 화면의 확인 절차에서 이어갈 수 있습니다."
    elif handled:
        answer = f"read-only 단위 기능 {len(handled)}개를 실행했습니다. semantic/plan/guardrail trace에서 근거와 handler 결과를 확인하세요."
    elif missing:
        answer = "실행 전 필요한 입력값이 부족합니다. missing slot을 보완하면 같은 plan으로 다시 실행할 수 있습니다."
    else:
        answer = "semantic 해석과 unit action plan을 만들었지만 처리 가능한 read-only handler는 아직 연결되지 않았습니다."
    return {
        "answer": answer,
        "intent": semantic.get("intent") or "general_orchestration",
        "missing": missing,
        "warnings": warnings,
        "next_actions": next_actions,
        "semantic_coverage": semantic.get("coverage") or 0,
        "guardrail": guardrail,
        "handled_count": len(handled),
        "approval_required_count": len(approvals),
        "blocked_count": len(blocked),
    }


@_traceable(name="flow.agent.conclusion", run_type="chain")
async def conclusion_node(state: RuntimeState) -> RuntimeState:
    conclusion = _deterministic_conclusion(state)
    llm = {"available": llm_adapter.is_available(), "used": False, "error": ""}
    if state.get("use_llm") and llm_adapter.is_available():
        prompt = (
            "Summarize this Flow agent runtime result in Korean in 4 concise lines. "
            "Do not invent facts.\n\n"
            + json.dumps({
                "goal": state.get("goal"),
                "semantic": state.get("semantic"),
                "plan": state.get("plan"),
                "results": state.get("results"),
                "conclusion": conclusion,
            }, ensure_ascii=False, default=str)[:5000]
        )
        out = await asyncio.to_thread(
            llm_adapter.complete,
            prompt,
            system="You write concise operational summaries for an internal manufacturing analytics app.",
            timeout=12,
        )
        llm["used"] = bool(out.get("ok"))
        llm["error"] = str(out.get("error") or "")
        if out.get("ok") and str(out.get("text") or "").strip():
            conclusion["answer"] = str(out.get("text") or "").strip()[:1600]
    conclusion["llm"] = llm
    events = _append_event(
        state,
        "conclusion",
        "completed",
        "final conclusion prepared",
        {"conclusion": conclusion},
        event="final",
    )
    return {"conclusion": conclusion, "events": events, "status": "completed"}


def _build_graph():
    if not LANGGRAPH_AVAILABLE or StateGraph is None or END is None:
        return None
    graph = StateGraph(RuntimeState)
    graph.add_node("semantic_layer", semantic_node)
    graph.add_node("task_planner", planning_node)
    graph.add_node("unit_agents", execution_node)
    graph.add_node("conclusion", conclusion_node)
    graph.set_entry_point("semantic_layer")
    graph.add_edge("semantic_layer", "task_planner")
    graph.add_edge("task_planner", "unit_agents")
    graph.add_edge("unit_agents", "conclusion")
    graph.add_edge("conclusion", END)
    return graph.compile()


def build_runtime_blueprint() -> dict[str, Any]:
    action_blueprint = build_action_blueprint()
    return {
        "ok": True,
        "runtime": "agent_runtime",
        "graph": {
            "nodes": ["semantic_layer", "task_planner", "unit_agents", "conclusion"],
            "edges": [
                ["semantic_layer", "task_planner"],
                ["task_planner", "unit_agents"],
                ["unit_agents", "conclusion"],
            ],
            **langgraph_status(),
        },
        "langsmith": langsmith_status(),
        "unit_agents": [spec.model_dump() for spec in UNIT_AGENT_SPECS],
        "actions": action_blueprint["actions"],
        "policies": action_blueprint["policies"],
        "workflow_enabled": action_blueprint["workflow_enabled"],
        "endpoints": {
            "semantic": "POST /api/agent/runtime/semantic/resolve",
            "run": "POST /api/agent/runtime/run",
            "stream": "GET /api/agent/runtime/stream?goal=...",
        },
        "llm": {"available": llm_adapter.is_available(), "config": llm_adapter.get_config(redact=True)},
    }


def _initial_state(req: AgentRuntimeRequest, username: str) -> RuntimeState:
    return {
        "run_id": _new_run_id(),
        "goal": req.goal.strip(),
        "username": username,
        "context": req.context if isinstance(req.context, dict) else {},
        "use_llm": bool(req.use_llm),
        "max_terms": int(req.max_terms or 24),
        "events": [],
        "status": "queued",
    }


def _trace_config(state: RuntimeState) -> dict[str, Any]:
    return {
        "run_name": "flow-agent-runtime",
        "metadata": {
            "run_id": state.get("run_id"),
            "username": state.get("username"),
            "goal": state.get("goal"),
            "flow_surface": "agent",
        },
        "tags": ["flow", "agent-runtime", "langgraph"],
    }


def _merge_state(state: RuntimeState, update: dict[str, Any]) -> RuntimeState:
    merged = dict(state)
    for key, value in update.items():
        merged[key] = value
    return merged  # type: ignore[return-value]


async def _fallback_astream(state: RuntimeState):
    for name, node in [
        ("semantic_layer", semantic_node),
        ("task_planner", planning_node),
        ("unit_agents", execution_node),
        ("conclusion", conclusion_node),
    ]:
        update = await node(state)
        state = _merge_state(state, update)
        yield {name: update}


async def stream_agent_runtime(req: AgentRuntimeRequest, username: str):
    state = _initial_state(req, username)
    start = _event(
        str(state["run_id"]),
        "start",
        "running",
        "agent runtime stream opened",
        {"langgraph": langgraph_status(), "langsmith": langsmith_status()},
    )
    yield AgentRuntimeEvent(**start)
    seen = {start["event_id"]}
    graph = _build_graph()
    stream = graph.astream(state, config=_trace_config(state), stream_mode="updates") if graph is not None else _fallback_astream(state)
    async for update in stream:
        if not isinstance(update, dict):
            continue
        for node_update in update.values():
            if not isinstance(node_update, dict):
                continue
            state = _merge_state(state, node_update)
            for raw_event in node_update.get("events") or []:
                event_id = str(raw_event.get("event_id") or "")
                if event_id and event_id in seen:
                    continue
                if event_id:
                    seen.add(event_id)
                yield AgentRuntimeEvent(**raw_event)
    done = _event(str(state["run_id"]), "done", "completed", "agent runtime stream closed", {}, event="done")
    yield AgentRuntimeEvent(**done)


async def run_agent_runtime_once(req: AgentRuntimeRequest, username: str) -> AgentRuntimeResult:
    events: list[AgentRuntimeEvent] = []
    latest: AgentRuntimeEvent | None = None
    async for event in stream_agent_runtime(req, username):
        events.append(event)
        latest = event
    run_id = events[0].run_id if events else _new_run_id()
    final_data = {}
    result_rows: list[dict[str, Any]] = []
    for event in reversed(events):
        if event.event == "final":
            final_data = event.data.get("conclusion") if isinstance(event.data, dict) else {}
            break
    semantic_data: dict[str, Any] = {}
    plan_rows: list[dict[str, Any]] = []
    for event in events:
        if event.stage == "semantic_layer" and isinstance(event.data, dict) and isinstance(event.data.get("semantic"), dict):
            semantic_data = event.data.get("semantic") or semantic_data
        if event.stage == "task_planner" and isinstance(event.data, dict):
            plan_rows = event.data.get("plan") if isinstance(event.data.get("plan"), list) else plan_rows
        if event.stage == "unit_agents" and isinstance(event.data, dict):
            result_rows = event.data.get("results") if isinstance(event.data.get("results"), list) else result_rows
    semantic = resolve_semantic_frame(req.goal, max_terms=req.max_terms)
    plans = [UnitAgentPlan(**row) for row in plan_rows] if plan_rows else _select_plan({"semantic": semantic.model_dump(), "goal": req.goal, "username": username})
    if semantic_data:
        try:
            semantic = type(semantic)(**semantic_data)
        except Exception:
            semantic = resolve_semantic_frame(req.goal, max_terms=req.max_terms)
    semantic = type(semantic)(**_with_unit_selection(semantic.model_dump(), plans))
    return AgentRuntimeResult(
        run_id=run_id,
        status="completed" if latest and latest.status == "completed" else "running",
        goal=req.goal,
        semantic=semantic,
        plan=plans,
        results=[UnitAgentResult(**row) for row in result_rows],
        conclusion=final_data or _deterministic_conclusion({"semantic": semantic.model_dump(), "goal": req.goal, "plan": [p.model_dump() for p in plans], "results": result_rows}),
        events=events,
        langsmith=langsmith_status(),
        langgraph=langgraph_status(),
    )


def encode_sse_event(event: AgentRuntimeEvent) -> str:
    payload = event.model_dump(mode="json")
    return f"event: {event.event}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
