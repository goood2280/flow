"""routers/groups.py v8.5.0 — User groups for Dashboard/Tracker visibility + LOT watch.

스키마 ({data_root}/groups/groups.json):
  [{id, name, owner, members:[username], watched_lots:[lot_id], created, updated}]

규약:
  - admin 은 모든 그룹 조회/수정 가능.
  - 일반 유저는 자기가 owner 이거나 member 인 그룹만 조회·LOT watch 편집 가능.
  - 생성·삭제·멤버 편집은 owner 또는 admin.
  - 감사 로그: groups_audit.jsonl (actor, action, group_id, timestamp, detail).

가시성 필터 헬퍼 (다른 라우터가 import):
  filter_by_visibility(items, username, role, key="group_ids")
    - admin 은 모두 통과.
    - item 에 group_ids 가 비어있으면 public → 통과.
    - group_ids 가 있으면 유저가 최소 1개 그룹의 member 여야 통과.
"""
import datetime
import uuid
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request, Depends, Query
from pydantic import BaseModel

from core.paths import PATHS
from core.utils import load_json, save_json, jsonl_append
from core.auth import current_user, require_admin

router = APIRouter(prefix="/api/groups", tags=["groups"])

GROUPS_DIR = PATHS.data_root / "groups"
GROUPS_DIR.mkdir(parents=True, exist_ok=True)
GROUPS_FILE = GROUPS_DIR / "groups.json"
AUDIT_FILE = GROUPS_DIR / "groups_audit.jsonl"


def _load() -> list:
    data = load_json(GROUPS_FILE, [])
    if not isinstance(data, list):
        return []
    return data


def _save(groups: list) -> None:
    save_json(GROUPS_FILE, groups, indent=2)


def _audit(actor: str, action: str, group_id: str, detail: str = "") -> None:
    jsonl_append(
        AUDIT_FILE,
        {
            "actor": actor,
            "action": action,
            "group_id": group_id,
            "detail": detail,
            "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        },
    )


def _find(groups: list, gid: str) -> Optional[dict]:
    return next((g for g in groups if g.get("id") == gid), None)


def _can_view(g: dict, username: str, role: str) -> bool:
    if role == "admin":
        return True
    if g.get("owner") == username:
        return True
    return username in (g.get("members") or [])


def _can_edit(g: dict, username: str, role: str) -> bool:
    if role == "admin":
        return True
    return g.get("owner") == username


# ── Visibility filter (다른 라우터가 import) ─────────────────────────
def user_group_ids(username: str, role: str) -> set:
    """해당 유저가 속한 group id set. admin 은 *모두 포함* 간주 → 전역 통과."""
    if role == "admin":
        return {"__admin__"}
    groups = _load()
    return {
        g.get("id", "")
        for g in groups
        if g.get("owner") == username or username in (g.get("members") or [])
    }


def filter_by_visibility(items: list, username: str, role: str, key: str = "group_ids") -> list:
    """item.group_ids 가 비어있으면 public (통과). 값이 있으면 유저 그룹과 교집합 필요.
    admin 은 항상 전부 통과."""
    if role == "admin":
        return items
    my = user_group_ids(username, role)
    out = []
    for it in items:
        gids = it.get(key) or []
        if not gids:
            out.append(it)
            continue
        if any(g in my for g in gids):
            out.append(it)
    return out


# ── Pydantic ────────────────────────────────────────────────────────
class GroupCreate(BaseModel):
    name: str
    members: List[str] = []
    watched_lots: List[str] = []


class GroupUpdate(BaseModel):
    name: Optional[str] = None
    members: Optional[List[str]] = None
    watched_lots: Optional[List[str]] = None


class MemberReq(BaseModel):
    username: str


class LotReq(BaseModel):
    lot_id: str


# ── Endpoints ───────────────────────────────────────────────────────
@router.get("/list")
def list_groups(request: Request):
    """내가 속한(또는 owner) 그룹 목록. admin 은 전체."""
    me = current_user(request)
    groups = _load()
    if me.get("role") == "admin":
        return {"groups": groups}
    vis = [g for g in groups if _can_view(g, me["username"], me.get("role", "user"))]
    return {"groups": vis}


@router.get("/mine")
def my_group_ids(request: Request):
    """내가 속한 그룹 id 배열. Dashboard/Tracker visibility UI 용."""
    me = current_user(request)
    if me.get("role") == "admin":
        # admin 은 모든 그룹을 선택 가능
        return {"group_ids": [g.get("id") for g in _load()], "admin": True}
    groups = _load()
    mine = [g for g in groups if _can_view(g, me["username"], me.get("role", "user"))]
    return {"group_ids": [g.get("id") for g in mine], "admin": False}


@router.post("/create")
def create_group(req: GroupCreate, request: Request):
    me = current_user(request)
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    groups = _load()
    if any(g.get("name") == name for g in groups):
        raise HTTPException(409, "group name already exists")
    gid = f"grp_{datetime.datetime.now().strftime('%y%m%d')}_{uuid.uuid4().hex[:6]}"
    now = datetime.datetime.now().isoformat(timespec="seconds")
    # owner 는 기본적으로 member 에 포함
    members = sorted(set([me["username"]] + (req.members or [])))
    g = {
        "id": gid,
        "name": name,
        "owner": me["username"],
        "members": members,
        "watched_lots": sorted(set(req.watched_lots or [])),
        "created": now,
        "updated": now,
    }
    groups.append(g)
    _save(groups)
    _audit(me["username"], "create", gid, name)
    return {"ok": True, "group": g}


@router.post("/update")
def update_group(req: GroupUpdate, request: Request, id: str = Query(...)):
    me = current_user(request)
    groups = _load()
    g = _find(groups, id)
    if not g:
        raise HTTPException(404)
    if not _can_edit(g, me["username"], me.get("role", "user")):
        raise HTTPException(403, "Only owner or admin can edit")
    if req.name is not None:
        name = req.name.strip()
        if not name:
            raise HTTPException(400, "name empty")
        if any(x.get("name") == name and x.get("id") != id for x in groups):
            raise HTTPException(409, "group name already exists")
        g["name"] = name
    if req.members is not None:
        # owner 는 항상 포함 (accidental-lockout 방지)
        g["members"] = sorted(set([g.get("owner", me["username"])] + list(req.members)))
    if req.watched_lots is not None:
        g["watched_lots"] = sorted(set(req.watched_lots))
    g["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    _save(groups)
    _audit(me["username"], "update", id, g.get("name", ""))
    return {"ok": True, "group": g}


@router.post("/delete")
def delete_group(request: Request, id: str = Query(...)):
    me = current_user(request)
    groups = _load()
    g = _find(groups, id)
    if not g:
        raise HTTPException(404)
    if not _can_edit(g, me["username"], me.get("role", "user")):
        raise HTTPException(403, "Only owner or admin can delete")
    groups = [x for x in groups if x.get("id") != id]
    _save(groups)
    _audit(me["username"], "delete", id, g.get("name", ""))
    return {"ok": True}


@router.post("/members/add")
def add_member(req: MemberReq, request: Request, id: str = Query(...)):
    me = current_user(request)
    groups = _load()
    g = _find(groups, id)
    if not g:
        raise HTTPException(404)
    if not _can_edit(g, me["username"], me.get("role", "user")):
        raise HTTPException(403)
    members = set(g.get("members") or [])
    members.add(req.username)
    g["members"] = sorted(members)
    g["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    _save(groups)
    _audit(me["username"], "member_add", id, req.username)
    return {"ok": True, "members": g["members"]}


@router.post("/members/remove")
def remove_member(req: MemberReq, request: Request, id: str = Query(...)):
    me = current_user(request)
    groups = _load()
    g = _find(groups, id)
    if not g:
        raise HTTPException(404)
    if not _can_edit(g, me["username"], me.get("role", "user")):
        raise HTTPException(403)
    if req.username == g.get("owner"):
        raise HTTPException(400, "Cannot remove owner")
    g["members"] = sorted([m for m in (g.get("members") or []) if m != req.username])
    g["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    _save(groups)
    _audit(me["username"], "member_remove", id, req.username)
    return {"ok": True, "members": g["members"]}


@router.post("/lots/add")
def add_lot(req: LotReq, request: Request, id: str = Query(...)):
    """그룹의 관심 LOT_WF 목록에 추가 (member 도 가능 — 공유 와치리스트)."""
    me = current_user(request)
    groups = _load()
    g = _find(groups, id)
    if not g:
        raise HTTPException(404)
    if not _can_view(g, me["username"], me.get("role", "user")):
        raise HTTPException(403)
    lots = set(g.get("watched_lots") or [])
    lots.add(req.lot_id)
    g["watched_lots"] = sorted(lots)
    g["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    _save(groups)
    _audit(me["username"], "lot_add", id, req.lot_id)
    return {"ok": True, "watched_lots": g["watched_lots"]}


@router.post("/lots/remove")
def remove_lot(req: LotReq, request: Request, id: str = Query(...)):
    me = current_user(request)
    groups = _load()
    g = _find(groups, id)
    if not g:
        raise HTTPException(404)
    if not _can_view(g, me["username"], me.get("role", "user")):
        raise HTTPException(403)
    g["watched_lots"] = sorted([x for x in (g.get("watched_lots") or []) if x != req.lot_id])
    g["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    _save(groups)
    _audit(me["username"], "lot_remove", id, req.lot_id)
    return {"ok": True, "watched_lots": g["watched_lots"]}


@router.get("/audit")
def audit_log(limit: int = 200, _admin=Depends(require_admin)):
    from core.utils import jsonl_read
    return {"entries": jsonl_read(AUDIT_FILE, limit)}
