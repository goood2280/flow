"""routers/flowi_learning.py — Flow-i human-in-the-loop 학습 데이터 관리 (Admin).

Home 채팅에서 쌓이는 두 학습 저장소를 Admin 페이지에서 조회/수정/삭제한다:
  - few-shot 용어 매핑 (core/flowi_fewshots — "기억해:" 티칭 + 피드백 교정)
  - 파일 설명 카탈로그 (core/flowi_file_docs — "파일 설명:" 등록)

엔드포인트:
  GET  /api/flowi-learning/fewshots            목록
  POST /api/flowi-learning/fewshots/save       {term, answer}
  POST /api/flowi-learning/fewshots/delete     {term}
  GET  /api/flowi-learning/file-docs           목록
  POST /api/flowi-learning/file-docs/save      {file, description}
  POST /api/flowi-learning/file-docs/delete    {file}
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core import audit, flowi_fewshots, flowi_file_docs, semantic_measure_catalog, semantic_source_catalog, chat_conversations, flowi_personalization
from core.auth import current_user

router = APIRouter(prefix="/api/flowi-learning", tags=["flowi-learning"])


def _require_admin(request: Request) -> dict[str, Any]:
    me = current_user(request)
    if not me or me.get("role") != "admin":
        raise HTTPException(status_code=403, detail="admin only")
    return me


class FewshotSaveReq(BaseModel):
    term: str
    answer: str


class FewshotDeleteReq(BaseModel):
    term: str


class FileDocSaveReq(BaseModel):
    file: str
    description: str


class FileDocDeleteReq(BaseModel):
    file: str


class SemanticPreviewReq(BaseModel):
    text: str
    use_llm: bool = True


class SemanticSaveReq(BaseModel):
    text: str = ""
    mapping: dict[str, Any] | None = None
    use_llm: bool = True


@router.get("/semantic/overview")
def semantic_overview(request: Request):
    _require_admin(request)
    catalog = semantic_measure_catalog.load_catalog(ensure=True)
    return {
        "terms": catalog.get("terms") or [],
        "sources": list(semantic_source_catalog.catalog_sources().values()),
        "files": flowi_file_docs.list_docs(),
        "catalog_path": catalog.get("path") or "",
    }


@router.post("/semantic/preview")
def preview_semantic_mapping(request: Request, body: SemanticPreviewReq):
    _require_admin(request)
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="학습 문장을 입력하세요")
    return semantic_measure_catalog.parse_natural_mapping(body.text, use_llm=body.use_llm)


@router.post("/semantic/save")
def save_semantic_mapping(request: Request, body: SemanticSaveReq):
    me = _require_admin(request)
    parsed = semantic_measure_catalog.parse_natural_mapping(body.text, use_llm=body.use_llm) if body.text.strip() else {}
    mapping = dict(body.mapping or parsed.get("mapping") or {})
    missing = [key for key in ("term", "product", "source_type", "step_id", "item_id") if not str(mapping.get(key) or "").strip()]
    if missing:
        raise HTTPException(status_code=400, detail=f"필수 값이 없습니다: {', '.join(missing)}")
    evidence = list(mapping.get("evidence") or [])
    evidence.append({
        "type": "admin_approved",
        "label": body.text.strip() or "관리자가 미리보기 매핑을 승인함",
        "source": "admin_flowi_learning",
    })
    mapping["evidence"] = evidence
    saved = semantic_measure_catalog.save_term(mapping, actor=me.get("username") or "admin")
    audit.record(request, action=f"flowi_learning:semantic_save:{saved.get('id')}", tab="admin")
    return {
        "ok": True,
        "term": saved,
        "sql_template": semantic_measure_catalog.build_reproducible_sql(saved, root_lot_id="<ROOT_LOT_ID>"),
    }


@router.get("/fewshots")
def list_fewshots(request: Request):
    _require_admin(request)
    return {"items": flowi_fewshots.list_entries()}


@router.post("/fewshots/save")
def save_fewshot(request: Request, body: FewshotSaveReq):
    me = _require_admin(request)
    entry = flowi_fewshots.teach(body.term, body.answer, by=me.get("username") or "admin", source="admin")
    if not entry:
        raise HTTPException(status_code=400, detail="잘못된 용어/답 형식")
    audit.record(request, action=f"flowi_learning:fewshot_save:{entry.get('term')}", tab="admin")
    return {"ok": True, "entry": entry}


@router.post("/fewshots/delete")
def delete_fewshot(request: Request, body: FewshotDeleteReq):
    _require_admin(request)
    ok = flowi_fewshots.forget(body.term)
    if not ok:
        raise HTTPException(status_code=404, detail=f"term not found: {body.term}")
    audit.record(request, action=f"flowi_learning:fewshot_delete:{body.term}", tab="admin")
    return {"ok": True}


@router.get("/file-docs")
def list_file_docs(request: Request):
    _require_admin(request)
    return {"items": flowi_file_docs.list_docs()}


@router.post("/file-docs/save")
def save_file_doc(request: Request, body: FileDocSaveReq):
    me = _require_admin(request)
    entry = flowi_file_docs.set_doc(body.file, body.description, by=me.get("username") or "admin")
    if not entry:
        raise HTTPException(status_code=400, detail="잘못된 파일명/설명 형식")
    audit.record(request, action=f"flowi_learning:file_doc_save:{entry.get('file')}", tab="admin")
    return {"ok": True, "entry": entry}


@router.post("/file-docs/delete")
def delete_file_doc(request: Request, body: FileDocDeleteReq):
    _require_admin(request)
    ok = flowi_file_docs.delete_doc(body.file)
    if not ok:
        raise HTTPException(status_code=404, detail=f"file not found: {body.file}")
    audit.record(request, action=f"flowi_learning:file_doc_delete:{body.file}", tab="admin")
    return {"ok": True}


class SkillCurateReq(BaseModel):
    title: str
    procedure: str
    feature: str = "table"
    action: str = ""
    shared: bool = True
    auto: bool = False


class SkillUpdateReq(BaseModel):
    skill_id: str
    title: str
    procedure: str
    shared: bool = True


class SkillDeleteReq(BaseModel):
    skill_id: str


@router.get("/conversations")
def list_conversations(request: Request, limit: int = 100):
    me = _require_admin(request)
    return {"conversations": chat_conversations.list_conversations(me["username"])[:max(1, min(limit, 100))]}


@router.get("/conversations/{conversation_id}")
def read_conversation(request: Request, conversation_id: str):
    me = _require_admin(request)
    try:
        return chat_conversations.read(me["username"], conversation_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="대화를 찾을 수 없습니다.") from exc


@router.get("/skills")
def list_skills(request: Request):
    me = _require_admin(request)
    return {"skills": flowi_personalization.list_skills(me["username"])}


@router.get("/skills/draft/{conversation_id}/{message_id}")
def draft_skill(request: Request, conversation_id: str, message_id: str):
    me = _require_admin(request)
    try:
        draft = flowi_personalization.draft_from_result(me["username"], conversation_id, message_id)
        return {"draft": draft}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/skills/curate")
def curate_skill(request: Request, body: SkillCurateReq):
    me = _require_admin(request)
    try:
        skill = flowi_personalization.save_curated_skill(
            owner=me.get("username") or "admin",
            title=body.title,
            procedure=body.procedure,
            feature=body.feature,
            action=body.action,
            shared=body.shared,
            auto=body.auto,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.record(request, action=f"flowi_learning:skill_curate:{skill.get('id')}", tab="admin")
    return {"ok": True, "skill": skill}


@router.post("/skills/update")
def update_skill_admin(request: Request, body: SkillUpdateReq):
    me = _require_admin(request)
    try:
        skill = flowi_personalization.update_skill(
            username=me["username"],
            skill_id=body.skill_id,
            title=body.title,
            procedure=body.procedure,
            shared=body.shared,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="스킬을 찾을 수 없습니다.") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.record(request, action=f"flowi_learning:skill_update:{body.skill_id}", tab="admin")
    return {"ok": True, "skill": skill}


@router.post("/skills/delete")
def delete_skill_admin(request: Request, body: SkillDeleteReq):
    me = _require_admin(request)
    try:
        flowi_personalization.delete_skill(me["username"], body.skill_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="스킬을 찾을 수 없습니다.") from exc
    audit.record(request, action=f"flowi_learning:skill_delete:{body.skill_id}", tab="admin")
    return {"ok": True}
