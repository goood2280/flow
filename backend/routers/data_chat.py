"""Administrator-only home data chat, independent of the retired agent routes."""
import sqlite3
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel, Field
from core import audit, auth, data_chat, chat_conversations, chat_prompts, flowi_gate, flowi_personalization, flowi_quota, flowi_turn
from core import ai_semantic, home_model_status, llm_adapter, chat_feedback, chat_table

router = APIRouter(prefix="/api/home-agent", tags=["data-chat"])


PROMPT_MAX_CHARS = 4000


class ChatRequest(BaseModel):
    # Typed questions stay within PROMPT_MAX_CHARS; a range pasted from Excel
    # (tab-separated) may be longer — checked in orchestrate().
    prompt: str = Field(min_length=1, max_length=chat_table.MAX_TABLE_PROMPT_CHARS)
    context: dict = Field(default_factory=dict)
    top_k: int = 1
    history: list[dict[str, str]] = Field(default_factory=list, max_length=20)
    conversation_id: UUID | None = None
    skill_id: UUID | None = None


class SkillSaveRequest(BaseModel):
    conversation_id: UUID
    message_id: UUID
    title: str = Field(min_length=1, max_length=80)
    procedure: str = Field(min_length=10, max_length=1000)
    shared: bool = False


class SkillUpdateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    procedure: str = Field(min_length=10, max_length=1000)
    shared: bool = False


class FeedbackRequest(BaseModel):
    conversation_id: UUID
    message_id: UUID
    rating: Literal["up", "down"]
    correction: str = Field(default="", max_length=1000)


class _QuotaDenied(Exception):
    """Raised inside the conversation turn so nothing is saved for a refused request."""

    def __init__(self, payload: dict):
        super().__init__(payload.get("answer", ""))
        self.payload = payload


def require_flowi_user(request: Request) -> dict:
    """Keep the API gate identical to the home button's ``flowi`` permission."""
    user = auth.current_user(request)
    if not flowi_gate.access_allowed(user):
        raise HTTPException(403, flowi_gate.denied_message(user))
    return user


@router.post("/orchestrate")
def orchestrate(body: ChatRequest, request: Request, _user=Depends(require_flowi_user)):
    if not body.prompt.strip():
        raise HTTPException(400, "메시지를 입력하세요.")
    if len(body.prompt) > PROMPT_MAX_CHARS and not chat_table.parse(body.prompt):
        raise HTTPException(400, f"메시지는 {PROMPT_MAX_CHARS:,}자 이내로 입력하세요. 엑셀에서 복사한 표는 "
                                 f"{chat_table.MAX_TABLE_PROMPT_CHARS:,}자까지 붙여 넣을 수 있습니다.")
    failure = None
    success = None
    reservation = None
    try:
        with chat_conversations.turn(_user["username"], body.conversation_id) as state:
            existing = bool(state["messages"])
            # Per-user question quota. Replies to a choice the stored conversation
            # is waiting for (human in the loop) continue a counted question.
            reservation = flowi_quota.consume(
                _user, len(flowi_turn.split_questions(body.prompt)),
                followup=existing and flowi_quota.awaiting_input(state["context"], state["messages"]))
            if not reservation["allowed"]:
                raise _QuotaDenied(flowi_quota.denied_payload(reservation))
            history = chat_conversations.history(state) if existing else [
                {"role": item["role"], "content": item.get("content", "")[:4000]}
                for item in body.history if item.get("role") in {"user", "assistant"}
            ]
            context = dict(state["context"] if existing else body.context)
            if not existing:
                context.pop("confirmed_product", None)
                context.pop("pending_product_prompt", None)
                context.pop("pending_teg_selection", None)
                context.pop("pending_semantic_selection", None)
                context.pop("semantic_scope", None)
                context.pop("semantic_split_prompt", None)
                context.pop("pending_split_query", None)
                context.pop("pending_custom_selection", None)
                context.pop("pending_split_choice", None)
                context.pop("split_query", None)
                context.pop("pending_eta", None)
                context.pop("eta_query", None)
                context.pop("pending_inline", None)
                context.pop("inline_query", None)
                context.pop("pending_inline_chart", None)
                context.pop("inline_chart_query", None)
                for key in ("pending_por", "pending_ml_chart", "ml_chart_query", "pending_et_chart", "et_chart_query", "pending_dashboard", "dashboard_query"):
                    context.pop(key, None)
                context.pop("pending_wafer_map", None)
                context.pop("wafer_map_query", None)
                context.pop("pending_et", None)
                context.pop("et_query", None)
                context.pop("pending_semantic_update", None)
                context.pop("pending_semantic_request", None)
            # Chart selection is an explicit client input, unlike server-owned
            # product confirmations and pending approval identifiers.
            if "selected_report_charts" in body.context:
                from core.data_chat_report import _selected_chart_snapshots
                try:
                    context["selected_report_charts"] = _selected_chart_snapshots(body.context)
                except (ValueError, HTTPException) as exc:
                    raise HTTPException(400, str(getattr(exc, "detail", exc))) from exc
            # These keys are server-owned, refreshed for this turn only.
            context.pop("personalization", None)
            context.pop("selected_skill", None)
            context.pop("user_feedback", None)
            # Only persisted feedback owned by the authenticated caller is eligible.
            # No client-supplied notes, previous-turn notes, or implicit HITL learning.
            if chat_feedback.has_feedback():
                products = data_chat.available_product_names()
                matches = data_chat.product_candidates(body.prompt, products)
                product = matches[0] if len(matches) == 1 else context.get("confirmed_product", "") if not matches else ""
                context["user_feedback"] = chat_feedback.prompt_context(_user["username"], body.prompt, product)
            if body.skill_id:
                try:
                    context["selected_skill"] = flowi_personalization.resolve_skill_context(_user["username"], body.skill_id)
                except (FileNotFoundError, ValueError) as exc:
                    raise HTTPException(404, "스킬을 찾을 수 없습니다.") from exc
            else:
                automatic_skill = flowi_personalization.resolve_skill_for_prompt(_user["username"], body.prompt)
                if automatic_skill:
                    context["selected_skill"] = automatic_skill
            state["context"] = context
            if not existing:
                # Preserve the bounded legacy tab history on its first save.
                for item in history:
                    chat_conversations.append(state, item["role"], item["content"])
            chat_conversations.append(state, "user", body.prompt)
            try:
                result = flowi_turn.execute(body.prompt, context, request, history=history)
            except Exception as exc:
                failure = exc
                flowi_quota.refund(_user, reservation)
                context.pop("user_feedback", None)
                chat_conversations.append(state, "assistant", "요청 처리에 실패했습니다. 다시 시도해 주세요.", error=True,
                    response={"ok": False, "routing_trace": {"question": body.prompt, "status": "failed", "events": []}})
            else:
                success = chat_prompts.completed_origin(state["messages"], result)
                if success:
                    result["success_prompt"] = success["prompt"]
                result.get("context", context).pop("user_feedback", None)
                state["context"] = result.get("context", context)
                display = {key: value for key, value in result.items() if key != "context"}
                answer = result.get("reply") or result.get("answer") or (result.get("tool") or {}).get("answer") or "응답이 없습니다."
                chat_conversations.append(state, "assistant", str(answer), response=display)
                result["conversation_id"] = state["id"]
                result["message_id"] = state["messages"][-1]["id"]
    except _QuotaDenied as denied:
        audit.record(request, "home:rate-limited", detail=str(denied.payload["quota"].get("reason") or ""), tab="home")
        return denied.payload
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower():
            raise HTTPException(409, "이 대화에서 다른 요청을 처리 중입니다. 잠시 후 다시 시도해 주세요.") from exc
        raise
    if failure is not None:
        raise failure
    if success:
        tool = result.get("tool") or {}
        chat_prompts.record_success(success["prompt"], user=_user["username"], category=tool.get("feature") or "")
    result["quota"] = {**flowi_quota.snapshot(_user), "charged": reservation.get("charged", 0),
                       "free_followup": bool(reservation.get("free_followup"))}
    audit.record(request, "home:data-chat", detail=str(result.get("conversation_id") or ""), tab="home")
    return result


@router.get("/conversations")
def conversations(_user=Depends(require_flowi_user)):
    return {"conversations": chat_conversations.list_conversations(_user["username"])}


@router.post("/feedback")
def save_feedback(body: FeedbackRequest, request: Request, _user=Depends(require_flowi_user)):
    try:
        feedback = chat_feedback.save(_user["username"], body.conversation_id, body.message_id, body.rating, body.correction)
    except FileNotFoundError as exc:
        raise HTTPException(404, "대화를 찾을 수 없습니다.") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    audit.record(request, "home:feedback", detail=str(body.message_id), tab="home")
    return {"feedback": feedback}


@router.get("/feedback/{conversation_id}/{message_id}")
def read_feedback(conversation_id: UUID, message_id: UUID, _user=Depends(require_flowi_user)):
    try:
        return {"feedback": chat_feedback.get(_user["username"], conversation_id, message_id)}
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, "답변을 찾을 수 없습니다.") from exc


@router.delete("/feedback/{conversation_id}/{message_id}")
def delete_feedback(conversation_id: UUID, message_id: UUID, request: Request, _user=Depends(require_flowi_user)):
    try:
        chat_feedback.delete(_user["username"], conversation_id, message_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, "답변을 찾을 수 없습니다.") from exc
    audit.record(request, "home:feedback-delete", detail=str(message_id), tab="home")
    return {"ok": True}


@router.get("/recent-results")
def recent_results(limit: int = 8, _user=Depends(require_flowi_user)):
    return {"results": chat_conversations.recent_results(_user["username"], limit=max(1, min(20, int(limit))))}


@router.get("/conversations/{conversation_id}")
def conversation(conversation_id: UUID, _user=Depends(require_flowi_user)):
    try:
        return chat_conversations.read(_user["username"], conversation_id)
    except FileNotFoundError:
        raise HTTPException(404, "대화를 찾을 수 없습니다.")


@router.get("/personal-skills")
def personal_skills(_user=Depends(require_flowi_user)):
    return {"skills": flowi_personalization.list_skills(_user["username"])}


@router.get("/personal-skills/draft/{conversation_id}/{message_id}")
def personal_skill_draft(conversation_id: UUID, message_id: UUID, _user=Depends(require_flowi_user)):
    try:
        return {"draft": flowi_personalization.draft_from_result(_user["username"], conversation_id, str(message_id))}
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/personal-skills")
def personal_skill_save(body: SkillSaveRequest, request: Request, _user=Depends(require_flowi_user)):
    try:
        skill = flowi_personalization.save_from_result(_user["username"], body.conversation_id, str(body.message_id), body.title, body.procedure, body.shared)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    audit.record(request, "home:skill-save", detail=skill["id"], tab="home")
    return {"skill": skill}


@router.put("/personal-skills/{skill_id}")
def personal_skill_update(skill_id: UUID, body: SkillUpdateRequest, request: Request, _user=Depends(require_flowi_user)):
    try:
        skill = flowi_personalization.update_skill(_user["username"], skill_id, body.title, body.procedure, body.shared)
    except FileNotFoundError as exc:
        raise HTTPException(404, "스킬을 찾을 수 없습니다.") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    audit.record(request, "home:skill-update", detail=skill["id"], tab="home")
    return {"skill": skill}


@router.delete("/personal-skills/{skill_id}")
def personal_skill_delete(skill_id: UUID, request: Request, _user=Depends(require_flowi_user)):
    try:
        flowi_personalization.delete_skill(_user["username"], skill_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "스킬을 찾을 수 없습니다.") from exc
    audit.record(request, "home:skill-delete", detail=str(skill_id), tab="home")
    return {"ok": True}


@router.get("/status")
def status(_user=Depends(require_flowi_user)):
    return {"model": home_model_status.snapshot(), "semantic": ai_semantic.snapshot(), "quota": flowi_quota.snapshot(_user)}


@router.post("/probe")
def probe(_user=Depends(require_flowi_user)):
    result = None
    if llm_adapter.is_available():
        result = llm_adapter.complete("Reply with OK only.", timeout=15, probe=True)
    payload = status(_user)
    if result is not None and not result.get("ok"):
        payload["model"].update(status="disconnected", message="연결 검사 실패: URL·인증·서버 상태와 호출 한도를 확인하세요.")
    return payload
