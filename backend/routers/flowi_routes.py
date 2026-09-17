"""Admin routing review for the current home harness, independent of retired agents."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from core import audit, chat_conversations
from core.auth import current_user

router = APIRouter(prefix="/api/flowi-learning", tags=["flowi-routing"])


def _require_admin(request):
    me = current_user(request)
    if not me or me.get("role") != "admin":
        raise HTTPException(403, "admin only")
    return me


@router.get("/conversations")
def list_conversations(request: Request, limit: int = 100):
    _require_admin(request)
    return {"conversations": chat_conversations.list_all_conversations(max(1, min(limit, 100)))}


@router.get("/conversations/{conversation_id}")
def read_conversation(request: Request, conversation_id: str):
    _require_admin(request)
    try:
        return chat_conversations.read_any(conversation_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, "대화를 찾을 수 없습니다.") from exc


class RoutingRuleReq(BaseModel):
    id: str = ""
    title: str = Field(min_length=1, max_length=100)
    question: str = Field(min_length=2, max_length=2000)
    normalized_question: str = Field(min_length=2, max_length=2000)
    route: str = "auto"
    segments: list[dict[str, str]] = Field(default_factory=list, max_length=20)
    notes: str = Field(default="", max_length=1000)
    enabled: bool = False
    conversation_id: str = ""
    message_id: str = ""


class RoutingPreviewReq(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


class RoutingDeleteReq(BaseModel):
    id: str


@router.get("/routing")
def routing_overview(request: Request):
    _require_admin(request)
    from core import flowi_routing
    return flowi_routing.overview()


@router.post("/routing/preview")
def routing_preview(request: Request, body: RoutingPreviewReq):
    _require_admin(request)
    from core import flowi_routing
    return flowi_routing.resolve(body.question)


@router.post("/routing/rules")
def routing_save(request: Request, body: RoutingRuleReq):
    me = _require_admin(request)
    from core import flowi_routing
    try:
        rule = flowi_routing.save_rule(body.model_dump(), me["username"])
    except FileNotFoundError as exc:
        raise HTTPException(404, "규칙을 찾을 수 없습니다.") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    audit.record(request, action="flowi_learning:routing_save", detail=rule["id"], tab="admin")
    return {"ok": True, "rule": rule}


@router.post("/routing/rules/delete")
def routing_delete(request: Request, body: RoutingDeleteReq):
    _require_admin(request)
    from core import flowi_routing
    try:
        flowi_routing.delete_rule(body.id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "규칙을 찾을 수 없습니다.") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    audit.record(request, action="flowi_learning:routing_delete", detail=body.id, tab="admin")
    return {"ok": True}
