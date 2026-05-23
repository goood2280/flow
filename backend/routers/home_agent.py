"""routers/home_agent.py — 홈 에이전트 오케스트레이터 라우터.

기존 /api/llm/flowi/chat 은 무수정. 이 라우터는 사용자 prompt 를 받아
ToolRegistry 의 도구들 중 적합한 것을 자동 선택하고 chain 실행한 후
trace 와 함께 응답한다.

엔드포인트:
  POST /api/home-agent/orchestrate    { prompt, top_k? } → { trace, reply, meta }
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core import audit, home_orchestrator
from core.auth import current_user

logger = logging.getLogger("flow.home_agent")

router = APIRouter(prefix="/api/home-agent", tags=["home-agent"])


class OrchestrateRequest(BaseModel):
    prompt: str
    top_k: int | None = 2


@router.post("/orchestrate")
def orchestrate(request: Request, body: OrchestrateRequest):
    me = current_user(request)
    if not me:
        raise HTTPException(status_code=401, detail="auth required")
    if not (body.prompt or "").strip():
        raise HTTPException(status_code=400, detail="prompt 가 비어 있습니다")

    out = home_orchestrator.orchestrate(
        body.prompt,
        user=me,
        top_k=max(1, min(4, int(body.top_k or 2))),
    )
    audit.record(
        request,
        action="home_agent:orchestrate",
        detail=f"prompt={body.prompt[:80]} picked={out.get('picked_count', 0)}",
        tab="home",
    )
    return out
