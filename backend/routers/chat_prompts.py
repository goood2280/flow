"""Data Chat sample prompt APIs, independent of retired Flow-i routers."""
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from core.auth import current_user

router = APIRouter(prefix="/api/home-agent", tags=["chat-prompts"])


class RecordSuccessRequest(BaseModel):
    prompt: str | None = None
    text: str | None = None
    category: str | None = ""


class PinPromptRequest(BaseModel):
    prompt: str | None = None
    text: str | None = None
    pinned: bool = True
    category: str | None = ""


@router.get("/sample-prompts")
def get_sample_prompts(request: Request):
    """Return pinned questions and successful questions."""
    from core import chat_prompts
    return chat_prompts.get_sample_prompts()


@router.post("/sample-prompts/record-success")
def record_prompt_success(request: Request, body: RecordSuccessRequest):
    """Record a successfully answered query."""
    from core import chat_prompts
    me = current_user(request) or {}
    q = (body.prompt or body.text or "").strip()
    return chat_prompts.record_success(q, user=me.get("username") or "", category=body.category or "")


@router.post("/sample-prompts/pin")
def pin_prompt(request: Request, body: PinPromptRequest):
    """Pin or unpin a prompt for admin curation."""
    from core import chat_prompts
    me = current_user(request)
    if not me or me.get("role") != "admin":
        raise HTTPException(status_code=403, detail="admin only")
    q = (body.prompt or body.text or "").strip()
    return chat_prompts.pin_prompt(q, pinned=body.pinned, user=me.get("username") or "admin", category=body.category or "")


@router.delete("/sample-prompts")
def delete_sample_prompt(
    request: Request,
    prompt: str | None = Query(None),
    text: str | None = Query(None),
    id: str | None = Query(None),
    kind: str = Query("all"),
):
    """Delete a prompt from pinned or successful lists."""
    from core import chat_prompts
    me = current_user(request)
    if not me or me.get("role") != "admin":
        raise HTTPException(status_code=403, detail="admin only")
    target_q = (prompt or text or "").strip()
    target_id = (id or "").strip()
    if not target_q and not target_id:
        raise HTTPException(status_code=400, detail="prompt, text, or id parameter is required")
    return chat_prompts.delete_prompt(prompt=target_q, item_id=target_id, kind=kind)
