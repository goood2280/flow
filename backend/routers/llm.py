"""routers/llm.py v8.7.7 — 선택적 사내 LLM 어댑터 노출 (infrastructure only).

- GET  /api/llm/status     is_available + redacted config (모든 유저 조회 가능 — UI 가시성용)
- POST /api/llm/test       admin 전용.  prompt 1건 실행해 연결 확인.

caller 주의: LLM 은 옵션. UI 는 status.available == false 면 관련 버튼을 숨겨야 함.
설정 편집은 /api/admin/settings/save 에서 llm 블록으로 수행.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from core.auth import current_user, require_admin
from core import llm_adapter


router = APIRouter(prefix="/api/llm", tags=["llm"])


@router.get("/status")
def status(request: Request):
    _ = current_user(request)
    cfg = llm_adapter.get_config(redact=True)
    return {
        "available": llm_adapter.is_available(),
        "config": cfg,
    }


class LLMTestReq(BaseModel):
    prompt: str
    system: str | None = None


@router.post("/test")
def test(req: LLMTestReq, _admin=Depends(require_admin)):
    if not llm_adapter.is_available():
        raise HTTPException(400, "LLM 이 설정되어 있지 않거나 비활성화됨")
    out = llm_adapter.complete((req.prompt or "").strip(), system=req.system)
    return out
