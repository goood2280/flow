"""Administrator-only home data chat, independent of the retired agent routes."""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from core.auth import require_admin
from core import audit, data_chat

router = APIRouter(prefix="/api/home-agent", tags=["data-chat"])


class ChatRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    context: dict = Field(default_factory=dict)
    top_k: int = 1
    history: list[dict[str, str]] = Field(default_factory=list, max_length=20)


@router.post("/orchestrate")
def orchestrate(body: ChatRequest, request: Request, _admin=Depends(require_admin)):
    result = data_chat.execute(body.prompt, body.context, request, history=[
        {"role": item.get("role", "user"), "content": item.get("content", "")[:4000]}
        for item in body.history if item.get("role") in {"user", "assistant"}
    ])
    audit.record(request,"home:data-chat",detail=body.prompt[:200],tab="home")
    return result
