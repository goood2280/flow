"""routers/meetings.py v8.8.0 — 회의관리 (Meeting Management).

스키마 ({data_root}/meetings/meetings.json):
  [{
    id, title, owner, scheduled_at,        # 회의 메타
    status: "scheduled"|"in_progress"|"completed"|"cancelled",
    agendas: [{ id, title, description, owner, link, created_at, updated_at }],
    minutes: { body, decisions:[str], action_items:[{text,owner,due}],
               author, updated_at } | null,
    created_by, created_at, updated_at,
  }]

권한:
  - 회의 생성: 로그인 유저 누구나. 생성자=주관자 기본값(필요시 변경).
  - 회의 메타 수정/삭제: 주관자(owner) 또는 admin.
  - 아젠다 추가: 누구나 (담당자=본인). 수정/삭제: 아젠다 담당자 / 회의 주관자 / admin.
  - 회의록 저장: 회의 주관자 또는 admin.

Endpoints:
  GET  /api/meetings/list?status=&owner=
  GET  /api/meetings/{mid}
  POST /api/meetings/create
  POST /api/meetings/update
  POST /api/meetings/delete?id=
  POST /api/meetings/agenda/add
  POST /api/meetings/agenda/update
  POST /api/meetings/agenda/delete?meeting_id=&agenda_id=
  POST /api/meetings/minutes/save
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

router = APIRouter(prefix="/api/meetings", tags=["meetings"])

MEET_DIR = PATHS.data_root / "meetings"
MEET_DIR.mkdir(parents=True, exist_ok=True)
MEET_FILE = MEET_DIR / "meetings.json"

VALID_STATUS = {"scheduled", "in_progress", "completed", "cancelled"}


# ── persistence ─────────────────────────────────────────────────────
def _load() -> list:
    data = load_json(MEET_FILE, [])
    return data if isinstance(data, list) else []


def _save(items: list) -> None:
    save_json(MEET_FILE, items, indent=2)


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _new_mid() -> str:
    return f"mt_{datetime.datetime.now().strftime('%y%m%d')}_{uuid.uuid4().hex[:6]}"


def _new_aid() -> str:
    return f"ag_{uuid.uuid4().hex[:8]}"


def _find(items: list, mid: str) -> tuple:
    for i, m in enumerate(items):
        if m.get("id") == mid:
            return i, m
    return -1, None


def _validate_status(s: str) -> str:
    s = (s or "").strip()
    if s and s not in VALID_STATUS:
        raise HTTPException(400, f"Invalid status: {s}")
    return s


def _normalize_dt(s: str) -> str:
    """Accept 'YYYY-MM-DDTHH:MM' or full ISO; return canonical ISO seconds."""
    s = (s or "").strip()
    if not s:
        return ""
    try:
        # tolerate trailing Z, missing seconds, etc.
        if s.endswith("Z"):
            s = s[:-1]
        if len(s) == 16:  # YYYY-MM-DDTHH:MM
            s = s + ":00"
        d = datetime.datetime.fromisoformat(s)
        return d.isoformat(timespec="seconds")
    except Exception:
        raise HTTPException(400, "Invalid scheduled_at (expected YYYY-MM-DDTHH:MM)")


# ── pydantic models ─────────────────────────────────────────────────
class MeetingCreate(BaseModel):
    title: str
    owner: Optional[str] = None
    scheduled_at: Optional[str] = ""


class MeetingUpdate(BaseModel):
    id: str
    title: Optional[str] = None
    owner: Optional[str] = None
    scheduled_at: Optional[str] = None
    status: Optional[str] = None


class AgendaAdd(BaseModel):
    meeting_id: str
    title: str
    description: Optional[str] = ""
    link: Optional[str] = ""
    owner: Optional[str] = None  # default = current user


class AgendaUpdate(BaseModel):
    meeting_id: str
    agenda_id: str
    title: Optional[str] = None
    description: Optional[str] = None
    link: Optional[str] = None
    owner: Optional[str] = None


class ActionItem(BaseModel):
    text: str
    owner: Optional[str] = ""
    due: Optional[str] = ""


class MinutesSave(BaseModel):
    meeting_id: str
    body: Optional[str] = ""
    decisions: Optional[List[str]] = None
    action_items: Optional[List[ActionItem]] = None


# ── permission helpers ──────────────────────────────────────────────
def _is_admin(me: dict) -> bool:
    return (me or {}).get("role") == "admin"


def _can_edit_meeting(me: dict, meeting: dict) -> bool:
    return _is_admin(me) or meeting.get("owner") == me["username"]


def _can_edit_agenda(me: dict, meeting: dict, agenda: dict) -> bool:
    if _is_admin(me):
        return True
    if meeting.get("owner") == me["username"]:
        return True
    return agenda.get("owner") == me["username"]


# ── endpoints ───────────────────────────────────────────────────────
@router.get("/list")
def list_meetings(
    status: Optional[str] = Query(None),
    owner: Optional[str] = Query(None),
):
    items = _load()
    if status:
        items = [m for m in items if (m.get("status") or "scheduled") == status]
    if owner:
        items = [m for m in items if m.get("owner") == owner]
    # sort: scheduled_at desc (most-recent first); fall back to created_at
    items.sort(
        key=lambda m: (m.get("scheduled_at") or "", m.get("created_at") or ""),
        reverse=True,
    )
    return {"meetings": items}


@router.get("/{mid}")
def get_meeting(mid: str):
    items = _load()
    _, m = _find(items, mid)
    if not m:
        raise HTTPException(404)
    return {"meeting": m}


@router.post("/create")
def create_meeting(req: MeetingCreate, request: Request):
    me = current_user(request)
    title = (req.title or "").strip()
    if not title:
        raise HTTPException(400, "title required")
    sched = _normalize_dt(req.scheduled_at or "")
    owner = (req.owner or me["username"]).strip()
    items = _load()
    now = _now()
    entry = {
        "id": _new_mid(),
        "title": title,
        "owner": owner,
        "scheduled_at": sched,
        "status": "scheduled",
        "agendas": [],
        "minutes": None,
        "created_by": me["username"],
        "created_at": now,
        "updated_at": now,
    }
    items.append(entry)
    _save(items)
    _audit(request, "meetings:create", detail=f"id={entry['id']} title={title[:60]}", tab="meetings")
    return {"ok": True, "meeting": entry}


@router.post("/update")
def update_meeting(req: MeetingUpdate, request: Request):
    me = current_user(request)
    items = _load()
    idx, m = _find(items, req.id)
    if not m:
        raise HTTPException(404)
    if not _can_edit_meeting(me, m):
        raise HTTPException(403, "Only owner or admin can edit this meeting")
    changed = []
    if req.title is not None:
        t = (req.title or "").strip()
        if not t:
            raise HTTPException(400, "title cannot be empty")
        if t != m.get("title"):
            m["title"] = t
            changed.append("title")
    if req.owner is not None:
        o = (req.owner or "").strip()
        if o and o != m.get("owner"):
            m["owner"] = o
            changed.append("owner")
    if req.scheduled_at is not None:
        s = _normalize_dt(req.scheduled_at)
        if s != m.get("scheduled_at"):
            m["scheduled_at"] = s
            changed.append("scheduled_at")
    if req.status is not None:
        st = _validate_status(req.status)
        if st and st != m.get("status"):
            m["status"] = st
            changed.append("status")
    if not changed:
        return {"ok": True, "meeting": m, "noop": True}
    m["updated_at"] = _now()
    items[idx] = m
    _save(items)
    _audit(request, "meetings:update", detail=f"id={m['id']} fields={','.join(changed)}", tab="meetings")
    return {"ok": True, "meeting": m}


@router.post("/delete")
def delete_meeting(request: Request, id: str = Query(...)):
    me = current_user(request)
    items = _load()
    idx, m = _find(items, id)
    if not m:
        raise HTTPException(404)
    if not _can_edit_meeting(me, m):
        raise HTTPException(403, "Only owner or admin can delete")
    items.pop(idx)
    _save(items)
    _audit(request, "meetings:delete", detail=f"id={id} title={(m.get('title') or '')[:60]}", tab="meetings")
    return {"ok": True}


@router.post("/agenda/add")
def add_agenda(req: AgendaAdd, request: Request):
    me = current_user(request)
    title = (req.title or "").strip()
    if not title:
        raise HTTPException(400, "agenda title required")
    items = _load()
    idx, m = _find(items, req.meeting_id)
    if not m:
        raise HTTPException(404, "meeting not found")
    now = _now()
    ag = {
        "id": _new_aid(),
        "title": title,
        "description": (req.description or "").strip(),
        "link": (req.link or "").strip(),
        "owner": (req.owner or me["username"]).strip(),
        "created_at": now,
        "updated_at": now,
    }
    m.setdefault("agendas", []).append(ag)
    m["updated_at"] = now
    items[idx] = m
    _save(items)
    _audit(request, "meetings:agenda_add",
           detail=f"meeting={m['id']} agenda={ag['id']} title={title[:60]}", tab="meetings")
    return {"ok": True, "meeting": m, "agenda": ag}


@router.post("/agenda/update")
def update_agenda(req: AgendaUpdate, request: Request):
    me = current_user(request)
    items = _load()
    idx, m = _find(items, req.meeting_id)
    if not m:
        raise HTTPException(404, "meeting not found")
    agendas = m.get("agendas") or []
    aidx = next((i for i, a in enumerate(agendas) if a.get("id") == req.agenda_id), -1)
    if aidx < 0:
        raise HTTPException(404, "agenda not found")
    ag = agendas[aidx]
    if not _can_edit_agenda(me, m, ag):
        raise HTTPException(403, "Only agenda owner / meeting owner / admin can edit")
    changed = []
    for fld in ("title", "description", "link", "owner"):
        v = getattr(req, fld, None)
        if v is None:
            continue
        v = (v or "").strip()
        if fld == "title" and not v:
            raise HTTPException(400, "agenda title cannot be empty")
        if ag.get(fld, "") != v:
            ag[fld] = v
            changed.append(fld)
    if not changed:
        return {"ok": True, "meeting": m, "noop": True}
    ag["updated_at"] = _now()
    agendas[aidx] = ag
    m["agendas"] = agendas
    m["updated_at"] = ag["updated_at"]
    items[idx] = m
    _save(items)
    _audit(request, "meetings:agenda_update",
           detail=f"meeting={m['id']} agenda={ag['id']} fields={','.join(changed)}", tab="meetings")
    return {"ok": True, "meeting": m, "agenda": ag}


@router.post("/agenda/delete")
def delete_agenda(
    request: Request,
    meeting_id: str = Query(...),
    agenda_id: str = Query(...),
):
    me = current_user(request)
    items = _load()
    idx, m = _find(items, meeting_id)
    if not m:
        raise HTTPException(404, "meeting not found")
    agendas = m.get("agendas") or []
    ag = next((a for a in agendas if a.get("id") == agenda_id), None)
    if not ag:
        raise HTTPException(404, "agenda not found")
    if not _can_edit_agenda(me, m, ag):
        raise HTTPException(403, "Only agenda owner / meeting owner / admin can delete")
    m["agendas"] = [a for a in agendas if a.get("id") != agenda_id]
    m["updated_at"] = _now()
    items[idx] = m
    _save(items)
    _audit(request, "meetings:agenda_delete",
           detail=f"meeting={meeting_id} agenda={agenda_id}", tab="meetings")
    return {"ok": True, "meeting": m}


@router.post("/minutes/save")
def save_minutes(req: MinutesSave, request: Request):
    me = current_user(request)
    items = _load()
    idx, m = _find(items, req.meeting_id)
    if not m:
        raise HTTPException(404, "meeting not found")
    if not _can_edit_meeting(me, m):
        raise HTTPException(403, "Only meeting owner or admin can write minutes")
    now = _now()
    decisions = [str(x).strip() for x in (req.decisions or []) if str(x).strip()]
    ai_clean = []
    for ai in (req.action_items or []):
        text = (ai.text or "").strip() if hasattr(ai, "text") else ""
        if not text:
            continue
        ai_clean.append({
            "text": text,
            "owner": (getattr(ai, "owner", "") or "").strip(),
            "due": (getattr(ai, "due", "") or "").strip(),
        })
    m["minutes"] = {
        "body": (req.body or "").strip(),
        "decisions": decisions,
        "action_items": ai_clean,
        "author": me["username"],
        "updated_at": now,
    }
    # Auto-promote to completed once minutes are written (unless cancelled)
    if (m.get("status") or "scheduled") not in ("completed", "cancelled"):
        m["status"] = "completed"
    m["updated_at"] = now
    items[idx] = m
    _save(items)
    _audit(request, "meetings:minutes",
           detail=f"meeting={m['id']} decisions={len(decisions)} actions={len(ai_clean)}", tab="meetings")
    return {"ok": True, "meeting": m}
