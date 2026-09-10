"""Administrator-only home data chat, independent of the retired agent routes."""
import sqlite3
from uuid import UUID
from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel, Field
from core.auth import require_admin
from core import audit, data_chat, chat_conversations
from core import ai_semantic, home_model_status, llm_adapter

router = APIRouter(prefix="/api/home-agent", tags=["data-chat"])


class ChatRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    context: dict = Field(default_factory=dict)
    top_k: int = 1
    history: list[dict[str, str]] = Field(default_factory=list, max_length=20)
    conversation_id: UUID | None = None


@router.post("/orchestrate")
def orchestrate(body: ChatRequest, request: Request, _admin=Depends(require_admin)):
    if not body.prompt.strip():
        raise HTTPException(400, "메시지를 입력하세요.")
    failure = None
    try:
        with chat_conversations.turn(_admin["username"], body.conversation_id) as state:
            existing = bool(state["messages"])
            history = chat_conversations.history(state) if existing else [
                {"role": item["role"], "content": item.get("content", "")[:4000]}
                for item in body.history if item.get("role") in {"user", "assistant"}
            ]
            context = state["context"] if existing else body.context
            state["context"] = context
            if not existing:
                # Preserve the bounded legacy tab history on its first save.
                for item in history:
                    chat_conversations.append(state, item["role"], item["content"])
            chat_conversations.append(state, "user", body.prompt)
            try:
                result = data_chat.execute(body.prompt, context, request, history=history)
            except Exception as exc:
                failure = exc
                chat_conversations.append(state, "assistant", "요청 처리에 실패했습니다. 다시 시도해 주세요.", error=True)
            else:
                state["context"] = result.get("context", context)
                display = {key: value for key, value in result.items() if key != "context"}
                answer = result.get("reply") or result.get("answer") or (result.get("tool") or {}).get("answer") or "응답이 없습니다."
                chat_conversations.append(state, "assistant", str(answer), response=display)
                result["conversation_id"] = state["id"]
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower():
            raise HTTPException(409, "이 대화에서 다른 요청을 처리 중입니다. 잠시 후 다시 시도해 주세요.") from exc
        raise
    if failure is not None:
        raise failure
    audit.record(request,"home:data-chat",detail=body.prompt[:200],tab="home")
    return result


@router.get("/conversations")
def conversations(_admin=Depends(require_admin)):
    return {"conversations": chat_conversations.list_conversations(_admin["username"])}


@router.get("/conversations/{conversation_id}")
def conversation(conversation_id: UUID, _admin=Depends(require_admin)):
    try:
        return chat_conversations.read(_admin["username"], conversation_id)
    except FileNotFoundError:
        raise HTTPException(404, "대화를 찾을 수 없습니다.")


@router.get("/status")
def status(_admin=Depends(require_admin)):
    return {"model": home_model_status.snapshot(), "semantic": ai_semantic.snapshot()}


@router.post("/probe")
def probe(_admin=Depends(require_admin)):
    result = None
    if llm_adapter.is_available():
        result = llm_adapter.complete("Reply with OK only.", timeout=15, probe=True)
    payload = status(_admin)
    if result is not None and not result.get("ok"):
        payload["model"].update(status="disconnected", message="연결 검사 실패: URL·인증·서버 상태와 호출 한도를 확인하세요.")
    return payload
