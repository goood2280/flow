"""routers/informs_extra.py v8.8.3 — 인폼 댓글 + 수정 이력 전용 엔드포인트.

TODO #10 이월분. informs.py 는 이미 1480+ 라인이라 분리하는 편이 유지보수에 유리.
같은 prefix `/api/informs` 에 댓글/이력 엔드포인트만 추가로 얹는다.

스키마 확장 (informs.json 각 엔트리):
  {
    ...기존 필드,
    comments: [{id, author, at, text, edited_at?}],
    edit_history: [{at, actor, field, before, after, note?}],
  }

엔드포인트:
  GET  /api/informs/{id}/comments            — 댓글 목록
  POST /api/informs/{id}/comments            — 댓글 추가 ({text})
  POST /api/informs/{id}/comments/{cid}/edit — 댓글 수정 (작성자·admin)
  POST /api/informs/{id}/comments/{cid}/delete — 댓글 삭제 (작성자·admin)
  GET  /api/informs/{id}/history             — 수정 이력 목록 (status_history + edit_history 병합)

정책:
  - 댓글: 로그인 유저 누구나 추가. 수정/삭제는 작성자 본인 또는 admin.
  - 수정이력: informs.py 쪽 set_status/check/update 가 기록한 status_history 와
    본 모듈이 관리하는 edit_history 를 합쳐 시간순 반환. FE 에서 타임라인 통일 렌더.
"""
from __future__ import annotations

import datetime
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.paths import PATHS
from core.utils import load_json, save_json
from core.auth import current_user

router = APIRouter(prefix="/api/informs", tags=["informs-extra"])

INFORMS_DIR = PATHS.data_root / "informs"
INFORMS_FILE = INFORMS_DIR / "informs.json"


# ─────────────── helpers ───────────────

def _load() -> List[Dict[str, Any]]:
    data = load_json(INFORMS_FILE, [])
    if not isinstance(data, list):
        return []
    return data


def _save(items: List[Dict[str, Any]]) -> None:
    INFORMS_DIR.mkdir(parents=True, exist_ok=True)
    save_json(INFORMS_FILE, items)


def _find(items: List[Dict[str, Any]], inform_id: str) -> Optional[Dict[str, Any]]:
    for it in items:
        if it.get("id") == inform_id:
            return it
    return None


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _is_admin(role: str) -> bool:
    return (role or "").lower() == "admin"


# ─────────────── schemas ───────────────

class CommentCreate(BaseModel):
    text: str


class CommentEdit(BaseModel):
    text: str


# ─────────────── comments ───────────────

@router.get("/{inform_id}/comments")
def list_comments(inform_id: str, request: Request):
    """댓글 목록. 로그인만 확인 (공개범위는 상위 인폼에서 이미 보장)."""
    me = current_user(request)  # noqa: F841  — 로그인 게이트
    items = _load()
    target = _find(items, inform_id)
    if not target:
        raise HTTPException(404, "Inform not found")
    return {"comments": target.get("comments") or []}


@router.post("/{inform_id}/comments")
def add_comment(inform_id: str, body: CommentCreate, request: Request):
    """댓글 추가. 텍스트는 trim + 1~4000 chars."""
    me = current_user(request)
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "text required")
    if len(text) > 4000:
        raise HTTPException(400, "text too long (max 4000)")
    items = _load()
    target = _find(items, inform_id)
    if not target:
        raise HTTPException(404, "Inform not found")
    comments = target.get("comments") or []
    entry = {
        "id": uuid.uuid4().hex[:12],
        "author": me["username"],
        "at": _now(),
        "text": text,
    }
    comments.append(entry)
    target["comments"] = comments

    # edit_history 에도 "comment_added" 행을 남겨 타임라인에서 보이게.
    hist = target.get("edit_history") or []
    hist.append({
        "at": entry["at"], "actor": me["username"],
        "field": "comment_added", "before": "", "after": entry["id"],
        "note": text[:80],
    })
    target["edit_history"] = hist[-200:]

    _save(items)
    return {"ok": True, "comment": entry}


@router.post("/{inform_id}/comments/{cid}/edit")
def edit_comment(inform_id: str, cid: str, body: CommentEdit, request: Request):
    """댓글 수정. 작성자 본인 또는 admin 만."""
    me = current_user(request)
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "text required")
    if len(text) > 4000:
        raise HTTPException(400, "text too long (max 4000)")
    items = _load()
    target = _find(items, inform_id)
    if not target:
        raise HTTPException(404, "Inform not found")
    comments = target.get("comments") or []
    for c in comments:
        if c.get("id") == cid:
            if c.get("author") != me["username"] and not _is_admin(me.get("role", "")):
                raise HTTPException(403, "Only author or admin can edit")
            before = c.get("text", "")
            c["text"] = text
            c["edited_at"] = _now()
            target["comments"] = comments
            hist = target.get("edit_history") or []
            hist.append({
                "at": c["edited_at"], "actor": me["username"],
                "field": "comment_edited", "before": before[:200], "after": text[:200],
                "note": f"comment {cid}",
            })
            target["edit_history"] = hist[-200:]
            _save(items)
            return {"ok": True, "comment": c}
    raise HTTPException(404, "Comment not found")


@router.post("/{inform_id}/comments/{cid}/delete")
def delete_comment(inform_id: str, cid: str, request: Request):
    """댓글 삭제. 작성자 본인 또는 admin."""
    me = current_user(request)
    items = _load()
    target = _find(items, inform_id)
    if not target:
        raise HTTPException(404, "Inform not found")
    comments = target.get("comments") or []
    for i, c in enumerate(comments):
        if c.get("id") == cid:
            if c.get("author") != me["username"] and not _is_admin(me.get("role", "")):
                raise HTTPException(403, "Only author or admin can delete")
            removed = comments.pop(i)
            target["comments"] = comments
            hist = target.get("edit_history") or []
            hist.append({
                "at": _now(), "actor": me["username"],
                "field": "comment_deleted", "before": removed.get("text", "")[:200],
                "after": "", "note": f"comment {cid}",
            })
            target["edit_history"] = hist[-200:]
            _save(items)
            return {"ok": True, "removed": removed}
    raise HTTPException(404, "Comment not found")


# ─────────────── history ───────────────

@router.get("/{inform_id}/history")
def get_history(inform_id: str, request: Request, limit: int = 200):
    """수정 이력 — status_history + edit_history 병합 (시간순)."""
    me = current_user(request)  # noqa: F841
    items = _load()
    target = _find(items, inform_id)
    if not target:
        raise HTTPException(404, "Inform not found")
    merged: List[Dict[str, Any]] = []
    for e in (target.get("status_history") or []):
        merged.append({
            "kind": "status",
            "at": e.get("at") or "",
            "actor": e.get("actor") or "",
            "field": "flow_status",
            "before": e.get("prev") or "",
            "after": e.get("status") or "",
            "note": e.get("note") or "",
        })
    for e in (target.get("edit_history") or []):
        merged.append({
            "kind": "edit",
            "at": e.get("at") or "",
            "actor": e.get("actor") or "",
            "field": e.get("field") or "",
            "before": e.get("before") or "",
            "after": e.get("after") or "",
            "note": e.get("note") or "",
        })
    # 최신순 정렬 (빈 at 은 뒤로).
    merged.sort(key=lambda x: x.get("at") or "", reverse=True)
    if isinstance(limit, int) and limit > 0:
        merged = merged[:limit]
    return {"history": merged, "count": len(merged)}
