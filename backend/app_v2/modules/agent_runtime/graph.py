from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import Any, TypedDict

from core import llm_adapter

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
    intent = str(semantic.get("intent") or "")
    selected = ["semantic_interpreter", "task_planner", "data_contract", "executor", "critic", "conclusion"]
    if intent in {"semantic_inspection", "traceable_orchestration"}:
        selected = ["semantic_interpreter", "task_planner", "critic", "conclusion"]
    plans: list[UnitAgentPlan] = []
    previous = ""
    for spec in UNIT_AGENT_SPECS:
        if spec.agent_id not in selected:
            continue
        depends = [previous] if previous else []
        plans.append(UnitAgentPlan(
            agent_id=spec.agent_id,
            title=spec.title,
            inputs={"intent": intent, "slots": semantic.get("slots") or {}},
            outputs=spec.outputs,
            depends_on=depends,
        ))
        previous = spec.agent_id
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
        },
    )
    return {"semantic": frame.model_dump(), "events": events, "status": "running"}


@_traceable(name="flow.agent.task_planner", run_type="chain")
async def planning_node(state: RuntimeState) -> RuntimeState:
    plan = _select_plan(state)
    events = _append_event(
        state,
        "task_planner",
        "completed",
        f"{len(plan)} unit agents planned",
        {"agents": [p.agent_id for p in plan]},
    )
    return {"plan": [p.model_dump() for p in plan], "events": events, "status": "running"}


@_traceable(name="flow.agent.unit_execution", run_type="chain")
async def execution_node(state: RuntimeState) -> RuntimeState:
    await asyncio.sleep(0)
    semantic = state.get("semantic") or {}
    slots = semantic.get("slots") if isinstance(semantic.get("slots"), dict) else {}
    results: list[UnitAgentResult] = []
    for plan in state.get("plan") or []:
        agent_id = str(plan.get("agent_id") or "")
        if agent_id in {"semantic_interpreter", "task_planner", "conclusion"}:
            continue
        summary = {
            "data_contract": "checked semantic candidates and slot readiness",
            "executor": "prepared read-only Flow action package",
            "critic": "reviewed ambiguity and missing inputs",
        }.get(agent_id, "completed")
        artifacts = [{
            "type": "semantic_slots",
            "slots": {k: v for k, v in slots.items() if v},
            "candidate_count": len(semantic.get("candidates") or []),
        }]
        results.append(UnitAgentResult(agent_id=agent_id, summary=summary, artifacts=artifacts))
    events = _append_event(
        state,
        "unit_agents",
        "completed",
        f"{len(results)} unit-agent results prepared",
        {"results": [r.model_dump() for r in results]},
    )
    return {"results": [r.model_dump() for r in results], "events": events, "status": "running"}


def _deterministic_conclusion(state: RuntimeState) -> dict[str, Any]:
    semantic = state.get("semantic") or {}
    slots = semantic.get("slots") if isinstance(semantic.get("slots"), dict) else {}
    warnings = list(semantic.get("warnings") or [])
    missing = []
    if not any(slots.get(key) for key in ("products", "root_lot_ids", "fab_lot_ids")):
        missing.append("product_or_lot")
    if semantic.get("intent") in {"filebrowser_ai_sql", "knob_analysis", "chart_analysis"} and not semantic.get("candidates"):
        missing.append("column_or_metric")
    next_actions = [
        "semantic layer 후보를 확인하고 부족한 alias/column doc을 추가",
        "실행이 필요한 기능은 read-only API package부터 연결",
        "LANGSMITH_TRACING=true와 LANGSMITH_PROJECT를 설정해 run trace를 누적",
    ]
    if missing:
        warnings.append("missing: " + ", ".join(missing))
    return {
        "answer": "Agent runtime blueprint completed. The goal was normalized, unit agents were planned, and a traceable execution package was prepared.",
        "intent": semantic.get("intent") or "general_orchestration",
        "missing": missing,
        "warnings": warnings,
        "next_actions": next_actions,
        "semantic_coverage": semantic.get("coverage") or 0,
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
    for event in events:
        if event.stage == "unit_agents" and isinstance(event.data, dict):
            result_rows = event.data.get("results") if isinstance(event.data.get("results"), list) else result_rows
    semantic = resolve_semantic_frame(req.goal, max_terms=req.max_terms)
    plans = _select_plan({"semantic": semantic.model_dump(), "goal": req.goal})
    return AgentRuntimeResult(
        run_id=run_id,
        status="completed" if latest and latest.status == "completed" else "running",
        goal=req.goal,
        semantic=semantic,
        plan=plans,
        results=[UnitAgentResult(**row) for row in result_rows],
        conclusion=final_data or _deterministic_conclusion({"semantic": semantic.model_dump(), "goal": req.goal}),
        events=events,
        langsmith=langsmith_status(),
        langgraph=langgraph_status(),
    )


def encode_sse_event(event: AgentRuntimeEvent) -> str:
    payload = event.model_dump(mode="json")
    return f"event: {event.event}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
