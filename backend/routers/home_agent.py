"""routers/home_agent.py — 홈 에이전트 오케스트레이터 라우터.

기존 /api/llm/flowi/chat 은 무수정. 이 라우터는 사용자 prompt 를 받아
ToolRegistry 의 도구들 중 적합한 것을 자동 선택하고 chain 실행한 후
trace 와 함께 응답한다.

엔드포인트:
  POST /api/home-agent/orchestrate         { prompt, top_k? } → { trace, reply, meta }
  GET  /api/home-agent/orchestrate/stream  ?prompt=&top_k=  → SSE 스트림
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core import agent_feedback, agent_feedback_penalties, audit, flowi_gate, home_orchestrator, tool_registry
from core.auth import current_user

logger = logging.getLogger("flow.home_agent")

router = APIRouter(prefix="/api/home-agent", tags=["home-agent"])


class OrchestrateRequest(BaseModel):
    prompt: str
    top_k: int | None = 2


class RunToolRequest(BaseModel):
    tool: str
    kind: str | None = None  # "unit_ai" 또는 "function" — 비우면 카탈로그에서 추론
    input: dict[str, Any] | None = None


class FeedbackRequest(BaseModel):
    prompt: str
    rating: str  # "up" | "down" | "correction"
    suggested_tool: str | None = ""
    note: str | None = ""
    trace_summary: list[dict[str, Any]] | None = None


class AliasRequest(BaseModel):
    pattern: str
    tool: str
    note: str | None = ""


def _denied_reply(me: dict) -> dict[str, Any]:
    payload = flowi_gate.denied_payload(me)
    return {"ok": True, "prompt": "", "trace": [], "reply": payload["answer"],
            "meta": {"planner": "gate", "blocked": "flowi_access_denied"}}


def _busy_reply(busy: "flowi_gate.FlowiBusy") -> dict[str, Any]:
    return {"ok": True, "prompt": "", "trace": [], "reply": busy.message,
            "meta": {"planner": "gate", "blocked": "flowi_busy", "busy": dict(busy.info)}}


@router.post("/orchestrate")
def orchestrate(request: Request, body: OrchestrateRequest):
    me = current_user(request)
    if not me:
        raise HTTPException(status_code=401, detail="auth required")
    if not (body.prompt or "").strip():
        raise HTTPException(status_code=400, detail="prompt 가 비어 있습니다")
    if not flowi_gate.access_allowed(me):
        return _denied_reply(me)

    try:
        with flowi_gate.slot(username=me.get("username") or "", role=me.get("role") or "user"):
            out = home_orchestrator.orchestrate(
                body.prompt,
                user=me,
                top_k=max(1, min(10, int(body.top_k or 2))),
                request=request,
            )
    except flowi_gate.FlowiBusy as busy:
        return _busy_reply(busy)
    audit.record(
        request,
        action="home_agent:orchestrate",
        detail=f"prompt={body.prompt[:80]} picked={out.get('picked_count', 0)} planner={out.get('meta', {}).get('planner')}",
        tab="home",
    )
    return out


def _sse_pack(event: str, payload: dict[str, Any]) -> bytes:
    data = json.dumps(payload, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {data}\n\n".encode("utf-8")


@router.get("/orchestrate/stream")
def orchestrate_stream(
    request: Request,
    prompt: str = Query(..., min_length=1),
    top_k: int = Query(2, ge=1, le=10),
    t: str | None = Query(None, description="session token fallback for EventSource"),
):
    me = current_user(request)
    if not me:
        raise HTTPException(status_code=401, detail="auth required")
    if not (prompt or "").strip():
        raise HTTPException(status_code=400, detail="prompt 가 비어 있습니다")

    def event_stream():
        last_picked = 0
        last_planner = ""
        if not flowi_gate.access_allowed(me):
            yield _sse_pack("reply", {"type": "reply", **_denied_reply(me)})
            return
        try:
            with flowi_gate.slot(username=me.get("username") or "", role=me.get("role") or "user"):
                for chunk in home_orchestrator.orchestrate_stream(prompt, user=me, top_k=top_k, request=request):
                    etype = str(chunk.get("type") or "message")
                    if etype == "reply":
                        last_picked = int(chunk.get("picked_count") or len(chunk.get("trace") or []))
                    if etype == "plan":
                        last_planner = str((chunk.get("meta") or {}).get("planner") or "")
                    yield _sse_pack(etype, chunk)
        except flowi_gate.FlowiBusy as busy:
            yield _sse_pack("reply", {"type": "reply", **_busy_reply(busy)})
        except Exception as e:
            logger.exception("orchestrate_stream failed")
            yield _sse_pack("reply", {"type": "reply", "ok": False, "error": str(e), "trace": []})
        finally:
            try:
                audit.record(
                    request,
                    action="home_agent:orchestrate_stream",
                    detail=f"prompt={prompt[:80]} picked={last_picked} planner={last_planner}",
                    tab="home",
                )
            except Exception:
                pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/feedback")
def post_feedback(request: Request, body: FeedbackRequest):
    me = current_user(request)
    if not me:
        raise HTTPException(status_code=401, detail="auth required")
    rating = (body.rating or "").strip().lower()
    if rating not in ("up", "down", "correction"):
        raise HTTPException(status_code=400, detail="rating must be up/down/correction")
    row = agent_feedback.append_feedback(
        prompt=body.prompt or "",
        rating=rating,
        suggested_tool=(body.suggested_tool or "").strip(),
        note=(body.note or "").strip(),
        trace_summary=body.trace_summary,
        actor=me.get("username") or "",
    )
    audit.record(
        request,
        action=f"home_agent:feedback:{rating}",
        detail=f"prompt={body.prompt[:60]} tool={body.suggested_tool or '-'}",
        tab="home",
    )
    if rating in {"up", "down"}:
        trace = body.trace_summary if isinstance(body.trace_summary, list) else []
        first = next((item for item in trace if isinstance(item, dict)), {})
        raw_tool_name = (body.suggested_tool or first.get("tool") or "").strip()
        unit_key = agent_feedback_penalties.home_feedback_unit_key(
            first,
            suggested_tool=raw_tool_name,
        )
        feedback_tool = unit_key or raw_tool_name
        try:
            agent_feedback_penalties.record_home_feedback(
                rating=rating,
                planner=str(first.get("source") or ""),
                tool=feedback_tool,
                source="home_agent",
                reason=body.note or first.get("reason") or "",
                actor=me.get("username") or "",
            )
            tool = tool_registry.get_tool(unit_key) if unit_key else (tool_registry.get_tool(raw_tool_name) if raw_tool_name else None)
            if tool and tool.get("kind") == "unit_ai":
                agent_feedback_penalties.record_feedback(
                    tool.get("name") or unit_key or raw_tool_name,
                    rating,
                    run_id=str(first.get("run_id") or ""),
                    reason=body.note or first.get("reason") or "",
                    actor=me.get("username") or "",
                )
        except Exception:
            logger.debug("home feedback penalty update failed", exc_info=True)
    # 싫어요 + 교정 코멘트 → few-shot 교정: 코멘트에 "X는 Y" / "X -> Y" / "정답은 Y"
    # 형태가 있으면 학습 저장소에 반영해 다음부터 그 답을 쓴다.
    correction = None
    if rating in {"down", "correction"} and (body.note or "").strip():
        try:
            from core import fab_reference, flowi_fewshots
            note = (body.note or "").strip()
            m = re.match(r"^\s*(?P<term>[A-Za-z0-9_\-.]{2,120})\s*(?:->|→|=|은\s|는\s|:)\s*(?P<ans>.+)$", note)
            if m:
                correction = flowi_fewshots.teach(
                    m.group("term"), m.group("ans").strip(),
                    by=me.get("username") or "", source="feedback",
                )
            else:
                ans_m = re.match(r"^\s*정답은?\s*[:]?\s*(?P<ans>.+)$", note)
                tokens = fab_reference.extract_step_tokens(body.prompt or "")
                if ans_m and tokens:
                    correction = flowi_fewshots.teach(
                        tokens[0], ans_m.group("ans").strip(),
                        by=me.get("username") or "", source="feedback",
                    )
        except Exception:
            logger.debug("feedback correction fewshot save failed", exc_info=True)
    out = {"ok": True, "feedback": row}
    if correction:
        out["correction"] = {"term": correction.get("term"), "answer": correction.get("answer")}
    return out


@router.get("/feedback")
def list_feedback(request: Request, days: int = 30, limit: int = 200):
    me = current_user(request)
    if not me:
        raise HTTPException(status_code=401, detail="auth required")
    rows = agent_feedback.list_feedback(days=days, limit=limit)
    return {"rows": rows}


@router.get("/alias")
def list_aliases(request: Request):
    me = current_user(request)
    if not me:
        raise HTTPException(status_code=401, detail="auth required")
    return {"aliases": agent_feedback.list_aliases()}


@router.post("/alias")
def post_alias(request: Request, body: AliasRequest):
    me = current_user(request)
    if not me:
        raise HTTPException(status_code=401, detail="auth required")
    name = (body.tool or "").strip()
    tool = tool_registry.get_tool(name) if name else None
    if not tool:
        raise HTTPException(status_code=404, detail=f"tool not found: {name}")
    try:
        row = agent_feedback.set_alias(
            pattern=body.pattern or "",
            tool=name,
            actor=me.get("username") or "",
            note=body.note or "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audit.record(
        request,
        action=f"home_agent:alias:set:{name}",
        detail=f"pattern={body.pattern[:60]}",
        tab="home",
    )
    return {"ok": True, "alias": row}


@router.delete("/alias/{alias_id}")
def delete_alias(request: Request, alias_id: str):
    me = current_user(request)
    if not me:
        raise HTTPException(status_code=401, detail="auth required")
    removed = agent_feedback.delete_alias(alias_id)
    if not removed:
        raise HTTPException(status_code=404, detail="alias not found")
    audit.record(
        request,
        action=f"home_agent:alias:delete:{alias_id}",
        detail="",
        tab="home",
    )
    return {"ok": True}


@router.post("/run-tool")
def run_tool(request: Request, body: RunToolRequest):
    """단일 도구를 inspector 폼 값으로 실행. 캔버스 노드 클릭 → 단독 실행 경로."""
    me = current_user(request)
    if not me:
        raise HTTPException(status_code=401, detail="auth required")
    name = (body.tool or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="tool 이름이 비어 있습니다")
    tool = tool_registry.get_tool(name)
    if not tool:
        raise HTTPException(status_code=404, detail=f"tool not found: {name}")
    if not tool.get("enabled"):
        raise HTTPException(status_code=409, detail=f"tool disabled: {name}")
    step_input = body.input or {}
    if "prompt" not in step_input:
        step_input["prompt"] = step_input.get("query") or ""
    exec_out = home_orchestrator._execute_step(tool, step_input, request=request, user=me)
    audit.record(
        request,
        action=f"home_agent:run-tool:{name}",
        detail=f"ok={exec_out.get('ok')} ms={exec_out.get('ms', 0)}",
        tab="home",
    )
    return {
        "ok": bool(exec_out.get("ok")),
        "tool": name,
        "kind": tool.get("kind"),
        "title": tool.get("title"),
        "input": step_input,
        "ms": exec_out.get("ms", 0),
        "feedback_penalty": exec_out.get("feedback_penalty") or {},
        "result_preview": exec_out.get("result_preview", ""),
        "result": exec_out.get("result"),
    }
