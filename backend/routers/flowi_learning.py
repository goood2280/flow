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

from core import audit, flowi_fewshots, flowi_file_docs
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
