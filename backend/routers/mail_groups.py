"""routers/mail_groups.py v8.8.23 — 공용 메일 그룹 = Admin 그룹 통합 어댑터.

v8.8.23 대수술:
  - 이제 mail_groups.json 이라는 별도 저장소를 유지하지 않는다.
  - 모든 엔드포인트는 routers/groups.py 의 groups.json 에 위임한다.
  - 레거시 mail_groups.json 은 groups.py 의 `_load()` 가 일회성으로 merge → rename.
  - 스키마는 `mail_groups` 스펙({id,name,members,extra_emails,note}) 을 그대로 노출하기
    위해 groups.py 레코드를 투영(note = description).

  이 결과로 Admin "그룹" 탭에서 만든 그룹이 인폼 메일 수신 그룹 드롭다운,
  회의 mail_group_ids 선택, 이슈추적 그룹 선택 모두에서 동일하게 노출된다.

Endpoints (모두 로그인 유저):
  GET  /api/mail-groups/list                — 공용 그룹 목록
  POST /api/mail-groups/create              — 신규 그룹 (groups.json 에 생성)
  POST /api/mail-groups/update?id=          — 편집
  POST /api/mail-groups/delete?id=          — 삭제 (owner/admin)
  POST /api/mail-groups/members/add?id=
  POST /api/mail-groups/members/remove?id=
"""
from __future__ import annotations

import datetime
import uuid
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from core.auth import current_user
from core.audit import record as _audit

# groups.py 내부 도우미 직접 재사용 — 단일 진실원.
from routers.groups import (
    _load as _groups_load,
    _save as _groups_save,
    _find as _groups_find,
    _sanitize_members,
    _clean_emails_for_group,
    _can_edit,
    _is_blocked_member,
    _load_users_by_name,
)

router = APIRouter(prefix="/api/mail-groups", tags=["mail_groups"])


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _project(g: dict) -> dict:
    """groups.json 레코드 → mail_groups 응답 shape (note = description).
    FE 호환성 확보 — description/owner/modules 등도 함께 노출해 Admin 그룹 정보를 볼 수 있게.
    """
    if not isinstance(g, dict):
        return {}
    return {
        "id": g.get("id", ""),
        "name": g.get("name", ""),
        "created_by": g.get("owner") or g.get("created_by") or "",
        "members": list(g.get("members") or []),
        "extra_emails": list(g.get("extra_emails") or []),
        "note": g.get("description") or g.get("note") or "",
        # 원본 필드도 노출 (읽기 전용).
        "description": g.get("description") or "",
        "modules": list(g.get("modules") or []),
        "watched_lots": list(g.get("watched_lots") or []),
        "owner": g.get("owner", ""),
        "created": g.get("created") or "",
        "updated": g.get("updated") or "",
    }


# v8.8.23: 하위 호환 — meetings.py 등이 `from routers.mail_groups import _load` 하던 것을
# 깨뜨리지 않도록 동일 이름으로 groups.json 을 읽어 mail-spec 투영 리스트를 돌려준다.
def _load() -> list:
    """하위 호환. groups.json → mail-groups shape 투영 리스트."""
    try:
        return [_project(g) for g in (_groups_load() or []) if isinstance(g, dict)]
    except Exception:
        return []


# ── Pydantic ────────────────────────────────────────────────────────
class MGCreate(BaseModel):
    name: str
    members: Optional[List[str]] = None
    extra_emails: Optional[List[str]] = None
    note: Optional[str] = ""


class MGUpdate(BaseModel):
    name: Optional[str] = None
    members: Optional[List[str]] = None
    extra_emails: Optional[List[str]] = None
    note: Optional[str] = None


class MGMember(BaseModel):
    username: str


# ── Endpoints ───────────────────────────────────────────────────────
@router.get("/list")
def list_groups(request: Request):
    """v8.8.23: 모든 그룹(groups.json) 을 mail 스펙으로 투영해 노출.
    권한: 공용 — 로그인 유저 전원.  visibility 필터는 적용하지 않음 (메일 수신자 선택 UX).
    """
    _ = current_user(request)
    items = _groups_load()
    out = [_project(g) for g in items if isinstance(g, dict)]
    out.sort(key=lambda g: (g.get("name") or "").lower())
    return {"groups": out}


@router.post("/create")
def create_group(req: MGCreate, request: Request):
    me = current_user(request)
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    if len(name) > 80:
        raise HTTPException(400, "name too long (max 80)")
    items = _groups_load()
    if any((g.get("name") or "").strip() == name for g in items):
        raise HTTPException(409, "group name already exists")
    now = _now()
    users_by_name = _load_users_by_name()
    members = _sanitize_members(req.members or [], users_by_name)
    gid = f"grp_{datetime.datetime.now().strftime('%y%m%d')}_{uuid.uuid4().hex[:6]}"
    g = {
        "id": gid,
        "name": name,
        "description": (req.note or "").strip()[:400] or None,
        "owner": me["username"],
        "members": sorted(set(members)),
        "watched_lots": [],
        "modules": [],
        "extra_emails": _clean_emails_for_group(req.extra_emails or []),
        "created": now,
        "updated": now,
    }
    items.append(g)
    _groups_save(items)
    _audit(request, "mail_groups:create",
           detail=f"id={g['id']} name={name} (unified→groups.json)", tab="mail_groups")
    return {"ok": True, "group": _project(g)}


@router.post("/update")
def update_group(req: MGUpdate, request: Request, id: str = Query(...)):
    me = current_user(request)
    items = _groups_load()
    g = _groups_find(items, id)
    if not g:
        raise HTTPException(404, "mail group not found")
    # 공용 편집 허용 — 단 name/delete 는 owner/admin 만.
    changed = []
    if req.name is not None:
        if not _can_edit(g, me["username"], me.get("role", "user")):
            raise HTTPException(403, "Only owner or admin can rename")
        name = (req.name or "").strip()
        if not name:
            raise HTTPException(400, "name empty")
        if any((x.get("name") or "").strip() == name and x.get("id") != id for x in items):
            raise HTTPException(409, "group name already exists")
        if name != g.get("name"):
            g["name"] = name
            changed.append("name")
    if req.members is not None:
        users_by_name = _load_users_by_name()
        g["members"] = sorted(set(_sanitize_members(req.members, users_by_name)))
        changed.append("members")
    if req.extra_emails is not None:
        g["extra_emails"] = _clean_emails_for_group(req.extra_emails)
        changed.append("extra_emails")
    if req.note is not None:
        g["description"] = (req.note or "").strip()[:400] or None
        changed.append("note")
    if not changed:
        return {"ok": True, "group": _project(g), "noop": True}
    g["updated"] = _now()
    # 인덱스 기반 교체 — find 는 참조이지만 명시적으로 저장.
    for i, x in enumerate(items):
        if x.get("id") == id:
            items[i] = g
            break
    _groups_save(items)
    _audit(request, "mail_groups:update",
           detail=f"id={id} by={me['username']} fields={','.join(changed)}",
           tab="mail_groups")
    return {"ok": True, "group": _project(g)}


@router.post("/delete")
def delete_group(request: Request, id: str = Query(...)):
    me = current_user(request)
    items = _groups_load()
    g = _groups_find(items, id)
    if not g:
        raise HTTPException(404, "mail group not found")
    if not _can_edit(g, me["username"], me.get("role", "user")):
        raise HTTPException(403, "Only owner or admin can delete")
    items = [x for x in items if x.get("id") != id]
    _groups_save(items)
    _audit(request, "mail_groups:delete",
           detail=f"id={id} by={me['username']} name={g.get('name','')}",
           tab="mail_groups")
    return {"ok": True}


@router.post("/members/add")
def add_member(req: MGMember, request: Request, id: str = Query(...)):
    me = current_user(request)
    un = (req.username or "").strip()
    if not un:
        raise HTTPException(400, "username required")
    items = _groups_load()
    g = _groups_find(items, id)
    if not g:
        raise HTTPException(404)
    users_by_name = _load_users_by_name()
    if _is_blocked_member(un, users_by_name):
        raise HTTPException(400, "blocked member (test 계정 등)")
    members = set(g.get("members") or [])
    members.add(un)
    g["members"] = sorted(_sanitize_members(list(members), users_by_name))
    g["updated"] = _now()
    for i, x in enumerate(items):
        if x.get("id") == id:
            items[i] = g
            break
    _groups_save(items)
    _audit(request, "mail_groups:member_add",
           detail=f"id={id} username={un} by={me['username']}",
           tab="mail_groups")
    return {"ok": True, "group": _project(g)}


@router.post("/members/remove")
def remove_member(req: MGMember, request: Request, id: str = Query(...)):
    me = current_user(request)
    un = (req.username or "").strip()
    items = _groups_load()
    g = _groups_find(items, id)
    if not g:
        raise HTTPException(404)
    g["members"] = sorted([m for m in (g.get("members") or []) if m != un])
    g["updated"] = _now()
    for i, x in enumerate(items):
        if x.get("id") == id:
            items[i] = g
            break
    _groups_save(items)
    _audit(request, "mail_groups:member_remove",
           detail=f"id={id} username={un} by={me['username']}",
           tab="mail_groups")
    return {"ok": True, "group": _project(g)}
