"""routers/mail_groups.py v8.7.7 — 회의/인폼 메일 발송용 공용 메일 그룹.

groups.py (Dashboard/Tracker/인폼 가시성) 과 구분되는 별도 스토어.  핵심 차이:
  - 모든 로그인 유저가 생성/수정/삭제 가능 (admin 전용 아님).
  - 한 유저가 여러 그룹에 속할 수 있음 (N:N).
  - 그룹은 "공용" — 누가 만들었든 모든 유저가 회의 메일 발송 시 선택 가능.
  - 멤버: {username} list + 선택적 extra_emails (팀 외부 고정 수신자).

스키마 ({data_root}/mail_groups.json):
  [{id, name, created_by, members:[username], extra_emails:[email], note, created, updated}]

Endpoints (모두 로그인 유저):
  GET  /api/mail-groups/list
  POST /api/mail-groups/create
  POST /api/mail-groups/update?id=
  POST /api/mail-groups/delete?id=
  POST /api/mail-groups/members/add?id=
  POST /api/mail-groups/members/remove?id=
"""
from __future__ import annotations

import datetime
import uuid
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from core.paths import PATHS
from core.utils import load_json, save_json
from core.auth import current_user
from core.audit import record as _audit


router = APIRouter(prefix="/api/mail-groups", tags=["mail_groups"])

MG_FILE = PATHS.data_root / "mail_groups.json"


def _load() -> list:
    data = load_json(MG_FILE, [])
    if not isinstance(data, list):
        return []
    out = []
    for g in data:
        if not isinstance(g, dict):
            continue
        g.setdefault("members", [])
        g.setdefault("extra_emails", [])
        g.setdefault("note", "")
        out.append(g)
    return out


def _save(items: list) -> None:
    save_json(MG_FILE, items, indent=2)


def _find(items: list, gid: str):
    for i, g in enumerate(items):
        if g.get("id") == gid:
            return i, g
    return -1, None


def _new_gid() -> str:
    return f"mg_{datetime.datetime.now().strftime('%y%m%d')}_{uuid.uuid4().hex[:6]}"


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _clean_emails(raw) -> list:
    if not isinstance(raw, list):
        return []
    out = []
    seen = set()
    for e in raw:
        s = str(e).strip()
        if s and "@" in s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _clean_members(raw) -> list:
    """v8.8.1: admin 계정 + "test" 포함 username 은 멤버 풀에서 제외."""
    if not isinstance(raw, list):
        return []
    # lazy import to avoid circular
    try:
        from routers.groups import _is_blocked_member, _load_users_by_name
        users_by_name = _load_users_by_name()
    except Exception:
        _is_blocked_member = None
        users_by_name = {}
    out = []
    seen = set()
    for u in raw:
        s = str(u).strip()
        if not s or s in seen:
            continue
        if _is_blocked_member and _is_blocked_member(s, users_by_name):
            continue
        seen.add(s)
        out.append(s)
    return out


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
    _ = current_user(request)
    items = _load()
    # sort by name asc
    items.sort(key=lambda g: (g.get("name") or "").lower())
    return {"groups": items}


@router.post("/create")
def create_group(req: MGCreate, request: Request):
    me = current_user(request)
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    if len(name) > 80:
        raise HTTPException(400, "name too long (max 80)")
    items = _load()
    if any((g.get("name") or "").strip() == name for g in items):
        raise HTTPException(409, "mail group name already exists")
    now = _now()
    members = _clean_members(req.members or [])
    # v8.8.1: 생성자 자동 포함 X (admin 이 만든 경우 메일 발송 대상이 아님).
    # creator 가 스스로를 members 에 넣고 싶으면 명시적으로 req.members 에 포함해야 함.
    g = {
        "id": _new_gid(),
        "name": name,
        "created_by": me["username"],
        "members": members,
        "extra_emails": _clean_emails(req.extra_emails or []),
        "note": (req.note or "").strip()[:400],
        "created": now,
        "updated": now,
    }
    items.append(g)
    _save(items)
    _audit(request, "mail_groups:create",
           detail=f"id={g['id']} name={name}", tab="mail_groups")
    return {"ok": True, "group": g}


@router.post("/update")
def update_group(req: MGUpdate, request: Request, id: str = Query(...)):
    me = current_user(request)
    items = _load()
    idx, g = _find(items, id)
    if not g:
        raise HTTPException(404, "mail group not found")
    # 공용 그룹 정책: 모든 유저가 편집 가능. 대신 변경자는 감사 로그에 기록.
    changed = []
    if req.name is not None:
        name = (req.name or "").strip()
        if not name:
            raise HTTPException(400, "name empty")
        if any((x.get("name") or "").strip() == name and x.get("id") != id for x in items):
            raise HTTPException(409, "mail group name already exists")
        if name != g.get("name"):
            g["name"] = name
            changed.append("name")
    if req.members is not None:
        g["members"] = _clean_members(req.members)
        changed.append("members")
    if req.extra_emails is not None:
        g["extra_emails"] = _clean_emails(req.extra_emails)
        changed.append("extra_emails")
    if req.note is not None:
        g["note"] = (req.note or "").strip()[:400]
        changed.append("note")
    if not changed:
        return {"ok": True, "group": g, "noop": True}
    g["updated"] = _now()
    items[idx] = g
    _save(items)
    _audit(request, "mail_groups:update",
           detail=f"id={id} by={me['username']} fields={','.join(changed)}",
           tab="mail_groups")
    return {"ok": True, "group": g}


@router.post("/delete")
def delete_group(request: Request, id: str = Query(...)):
    me = current_user(request)
    items = _load()
    idx, g = _find(items, id)
    if not g:
        raise HTTPException(404, "mail group not found")
    items.pop(idx)
    _save(items)
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
    items = _load()
    idx, g = _find(items, id)
    if not g:
        raise HTTPException(404)
    members = _clean_members(g.get("members") or [])
    if un not in members:
        members.append(un)
    g["members"] = members
    g["updated"] = _now()
    items[idx] = g
    _save(items)
    _audit(request, "mail_groups:member_add",
           detail=f"id={id} username={un} by={me['username']}",
           tab="mail_groups")
    return {"ok": True, "group": g}


@router.post("/members/remove")
def remove_member(req: MGMember, request: Request, id: str = Query(...)):
    me = current_user(request)
    un = (req.username or "").strip()
    items = _load()
    idx, g = _find(items, id)
    if not g:
        raise HTTPException(404)
    g["members"] = [m for m in (g.get("members") or []) if m != un]
    g["updated"] = _now()
    items[idx] = g
    _save(items)
    _audit(request, "mail_groups:member_remove",
           detail=f"id={id} username={un} by={me['username']}",
           tab="mail_groups")
    return {"ok": True, "group": g}
