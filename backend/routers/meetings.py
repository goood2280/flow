"""routers/meetings.py v8.7.4 — 회의관리 (Meeting + Recurrence + Sessions).

변경점 (v8.7.4):
  - 회의(Meeting) 아래 **차수(Session)** 개념 도입. 각 차수가 독립적 scheduled_at /
    status / agendas / minutes 를 갖는다. 기존 v8.7.2 스키마(agendas/minutes 가
    meeting 레벨) 는 자동 마이그레이션 ─ 1 개의 session 으로 래핑.
  - 반복(recurrence) 메타 추가: {type: "none"|"weekly", count_per_week,
    weekday: [0..6], note}. FE 가 다음 차수 일정을 제안할 때 참고.
  - 시드 "hol" 기본 소유자 제거.  owner 는 명시 + 없으면 생성자 username.

스키마 ({data_root}/meetings/meetings.json):
  [{
    id, title, owner,
    recurrence: { type, count_per_week, weekday:[int], note },
    status: "active"|"archived"|"cancelled",
    sessions: [{
      id, idx, scheduled_at,
      status: "scheduled"|"in_progress"|"completed"|"cancelled",
      agendas: [{ id, title, description, owner, link, created_at, updated_at }],
      minutes: { body, decisions, action_items, author, updated_at } | null,
      created_at, updated_at,
    }],
    created_by, created_at, updated_at,
  }]

권한:
  - 회의 생성: 로그인 유저 누구나. 생성자 = 주관자 기본값.
  - 회의 메타/반복 수정·삭제: 주관자 또는 admin.
  - 차수 추가/수정/삭제: 주관자 또는 admin.
  - 아젠다 추가: 로그인 유저 누구나 (담당자 = 본인).
  - 아젠다 수정/삭제: 아젠다 담당자 / 회의 주관자 / admin.
  - 회의록 저장: 회의 주관자 또는 admin.

Endpoints:
  GET  /api/meetings/list?status=&owner=
  GET  /api/meetings/{mid}
  POST /api/meetings/create
  POST /api/meetings/update
  POST /api/meetings/delete?id=
  POST /api/meetings/session/add                 body: {meeting_id, scheduled_at?}
  POST /api/meetings/session/update              body: {meeting_id, session_id, scheduled_at?, status?}
  POST /api/meetings/session/delete?meeting_id=&session_id=
  POST /api/meetings/agenda/add                  body: {meeting_id, session_id, title, ...}
  POST /api/meetings/agenda/update               body: {meeting_id, session_id, agenda_id, ...}
  POST /api/meetings/agenda/delete?meeting_id=&session_id=&agenda_id=
  POST /api/meetings/minutes/save                body: {meeting_id, session_id, body, decisions, action_items}
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


def _calendar_remove_meeting(meeting_id: str) -> None:
    try:
        from routers.calendar import remove_events_for_meeting
        remove_events_for_meeting(meeting_id)
    except Exception:
        pass


def _calendar_remove_session(meeting_id: str, session_id: str) -> None:
    try:
        from routers.calendar import remove_events_for_session
        remove_events_for_session(meeting_id, session_id)
    except Exception:
        pass


# For calendar→meeting status mirror (called from calendar router).
def mirror_action_item_status(meeting_id: str, session_id: str,
                              action_item_id: str, status: str) -> None:
    items = _load()
    midx, m = _find(items, meeting_id)
    if midx < 0 or not m:
        return
    sidx, s = _find_session(m, session_id)
    if sidx < 0:
        return
    minutes = s.get("minutes") or {}
    ai_list = minutes.get("action_items") or []
    ch = False
    for ai in ai_list:
        if isinstance(ai, dict) and ai.get("id") == action_item_id:
            if ai.get("status") != status:
                ai["status"] = status
                ch = True
    if ch:
        s["minutes"]["action_items"] = ai_list
        s["updated_at"] = _now()
        m["sessions"][sidx] = s
        m["updated_at"] = s["updated_at"]
        items[midx] = m
        _save(items)


def _new_did() -> str:
    return f"dec_{uuid.uuid4().hex[:8]}"


def _ensure_decision_objects(dlist: list) -> list:
    """v8.7.5: decisions 가 문자열/객체 혼재할 때 객체 list 로 정규화."""
    out = []
    seen = set()
    for d in (dlist or []):
        if isinstance(d, str):
            s = d.strip()
            if not s:
                continue
            did = _new_did()
            while did in seen:
                did = _new_did()
            seen.add(did)
            out.append({"id": did, "text": s, "due": "",
                        "calendar_pushed": False, "calendar_event_id": "",
                        "calendar_pushed_by": "", "calendar_pushed_at": ""})
        elif isinstance(d, dict):
            s = (d.get("text") or "").strip()
            if not s:
                continue
            did = d.get("id") or _new_did()
            while did in seen:
                did = _new_did()
            seen.add(did)
            out.append({
                "id": did,
                "text": s,
                "due": (d.get("due") or "").strip(),
                "calendar_pushed": bool(d.get("calendar_pushed")),
                "calendar_event_id": d.get("calendar_event_id") or "",
                "calendar_pushed_by": d.get("calendar_pushed_by") or "",
                "calendar_pushed_at": d.get("calendar_pushed_at") or "",
            })
    return out


def _ensure_action_item_ids(ai_list: list) -> list:
    """각 action_item 에 안정적인 id 부여 — calendar sync 의 키."""
    out = []
    seen = set()
    for ai in (ai_list or []):
        if not isinstance(ai, dict):
            continue
        aid = ai.get("id") or f"ai_{uuid.uuid4().hex[:8]}"
        while aid in seen:
            aid = f"ai_{uuid.uuid4().hex[:8]}"
        seen.add(aid)
        ai["id"] = aid
        ai.setdefault("status", "pending")
        out.append(ai)
    return out

router = APIRouter(prefix="/api/meetings", tags=["meetings"])

MEET_DIR = PATHS.data_root / "meetings"
MEET_DIR.mkdir(parents=True, exist_ok=True)
MEET_FILE = MEET_DIR / "meetings.json"

VALID_SESSION_STATUS = {"scheduled", "in_progress", "completed", "cancelled"}
VALID_MEETING_STATUS = {"active", "archived", "cancelled"}
VALID_RECURRENCE_TYPE = {"none", "weekly"}


# ── persistence ─────────────────────────────────────────────────────
def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _new_mid() -> str:
    return f"mt_{datetime.datetime.now().strftime('%y%m%d')}_{uuid.uuid4().hex[:6]}"


def _new_sid() -> str:
    return f"ss_{uuid.uuid4().hex[:8]}"


def _new_aid() -> str:
    return f"ag_{uuid.uuid4().hex[:8]}"


def _default_recurrence() -> dict:
    return {"type": "none", "count_per_week": 0, "weekday": [], "note": ""}


def _migrate_entry(m: dict) -> dict:
    """v8.7.2 → v8.7.4 one-shot migration. Mutates m and returns it."""
    if "sessions" in m and isinstance(m.get("sessions"), list):
        # Ensure recurrence exists
        if "recurrence" not in m or not isinstance(m.get("recurrence"), dict):
            m["recurrence"] = _default_recurrence()
        # Meeting-level status mapping: old session status -> meeting status
        m_status = m.get("status") or "active"
        if m_status not in VALID_MEETING_STATUS:
            m["status"] = "active"
        return m

    # Legacy: agendas/minutes at meeting level → wrap into 1 session.
    now = m.get("updated_at") or _now()
    session = {
        "id": _new_sid(),
        "idx": 1,
        "scheduled_at": m.get("scheduled_at") or "",
        "status": m.get("status") or "scheduled",
        "agendas": m.get("agendas") or [],
        "minutes": m.get("minutes"),
        "created_at": m.get("created_at") or now,
        "updated_at": now,
    }
    # Map old session status to meeting status
    if session["status"] == "cancelled":
        meeting_status = "cancelled"
    else:
        meeting_status = "active"
    m2 = {
        "id": m.get("id") or _new_mid(),
        "title": m.get("title") or "",
        "owner": m.get("owner") or m.get("created_by") or "",
        "recurrence": _default_recurrence(),
        "status": meeting_status,
        "sessions": [session],
        "created_by": m.get("created_by") or m.get("owner") or "",
        "created_at": m.get("created_at") or now,
        "updated_at": now,
    }
    # remove legacy keys just in case
    for k in ("agendas", "minutes", "scheduled_at"):
        m2.pop(k, None)
    return m2


def _normalize_minutes(minutes):
    if not isinstance(minutes, dict):
        return minutes
    # Decisions: string → object list.
    if "decisions" in minutes:
        minutes["decisions"] = _ensure_decision_objects(minutes.get("decisions") or [])
    return minutes


def _load() -> list:
    data = load_json(MEET_FILE, [])
    if not isinstance(data, list):
        return []
    out = []
    for m in data:
        if not isinstance(m, dict):
            continue
        entry = _migrate_entry(dict(m))
        for s in (entry.get("sessions") or []):
            if s.get("minutes"):
                s["minutes"] = _normalize_minutes(s["minutes"])
        out.append(entry)
    return out


def _save(items: list) -> None:
    save_json(MEET_FILE, items, indent=2)


def _find(items: list, mid: str) -> tuple:
    for i, m in enumerate(items):
        if m.get("id") == mid:
            return i, m
    return -1, None


def _find_session(m: dict, sid: str) -> tuple:
    for i, s in enumerate(m.get("sessions") or []):
        if s.get("id") == sid:
            return i, s
    return -1, None


def _validate_session_status(s: str) -> str:
    s = (s or "").strip()
    if s and s not in VALID_SESSION_STATUS:
        raise HTTPException(400, f"Invalid session status: {s}")
    return s


def _validate_meeting_status(s: str) -> str:
    s = (s or "").strip()
    if s and s not in VALID_MEETING_STATUS:
        raise HTTPException(400, f"Invalid meeting status: {s}")
    return s


def _normalize_dt(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    try:
        if s.endswith("Z"):
            s = s[:-1]
        if len(s) == 16:
            s = s + ":00"
        d = datetime.datetime.fromisoformat(s)
        return d.isoformat(timespec="seconds")
    except Exception:
        raise HTTPException(400, "Invalid datetime (expected YYYY-MM-DDTHH:MM)")


def _normalize_recurrence(raw: Optional[dict]) -> dict:
    if not raw or not isinstance(raw, dict):
        return _default_recurrence()
    rtype = (raw.get("type") or "none").strip()
    if rtype not in VALID_RECURRENCE_TYPE:
        rtype = "none"
    try:
        cpw = int(raw.get("count_per_week") or 0)
    except Exception:
        cpw = 0
    cpw = max(0, min(7, cpw))
    wd_raw = raw.get("weekday") or []
    weekday: list = []
    if isinstance(wd_raw, list):
        for x in wd_raw:
            try:
                v = int(x)
                if 0 <= v <= 6 and v not in weekday:
                    weekday.append(v)
            except Exception:
                continue
    weekday.sort()
    note = (raw.get("note") or "").strip()[:200]
    return {"type": rtype, "count_per_week": cpw, "weekday": weekday, "note": note}


# ── pydantic models ─────────────────────────────────────────────────
class RecurrenceReq(BaseModel):
    type: Optional[str] = "none"
    count_per_week: Optional[int] = 0
    weekday: Optional[List[int]] = None
    note: Optional[str] = ""


class MeetingCreate(BaseModel):
    title: str
    owner: Optional[str] = None
    first_scheduled_at: Optional[str] = ""
    recurrence: Optional[RecurrenceReq] = None
    category: Optional[str] = ""  # calendar 카테고리 (색상)


class MeetingUpdate(BaseModel):
    id: str
    title: Optional[str] = None
    owner: Optional[str] = None
    status: Optional[str] = None
    recurrence: Optional[RecurrenceReq] = None
    category: Optional[str] = None


class SessionAdd(BaseModel):
    meeting_id: str
    scheduled_at: Optional[str] = ""


class SessionUpdate(BaseModel):
    meeting_id: str
    session_id: str
    scheduled_at: Optional[str] = None
    status: Optional[str] = None


class AgendaAdd(BaseModel):
    meeting_id: str
    session_id: str
    title: str
    description: Optional[str] = ""
    link: Optional[str] = ""
    owner: Optional[str] = None


class AgendaUpdate(BaseModel):
    meeting_id: str
    session_id: str
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
    session_id: str
    body: Optional[str] = ""
    # v8.7.5: 문자열 또는 {id,text,due} 객체 list 둘 다 수용.
    decisions: Optional[List] = None
    action_items: Optional[List[ActionItem]] = None


# ── permission helpers ─────────────────────────────────────────────
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


def _next_session_idx(m: dict) -> int:
    ss = m.get("sessions") or []
    if not ss:
        return 1
    try:
        return max(int(s.get("idx") or 0) for s in ss) + 1
    except Exception:
        return len(ss) + 1


# ── endpoints ──────────────────────────────────────────────────────
@router.get("/list")
def list_meetings(
    status: Optional[str] = Query(None),
    owner: Optional[str] = Query(None),
):
    items = _load()
    if status:
        items = [m for m in items if (m.get("status") or "active") == status]
    if owner:
        items = [m for m in items if m.get("owner") == owner]
    # sort by last session scheduled_at desc, fallback to created_at
    def _sort_key(m):
        ss = m.get("sessions") or []
        latest = max((s.get("scheduled_at") or "" for s in ss), default="")
        return (latest, m.get("created_at") or "")
    items.sort(key=_sort_key, reverse=True)
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
    owner = (req.owner or me["username"]).strip() or me["username"]
    rec = _normalize_recurrence(req.recurrence.dict() if req.recurrence else None)
    first_dt = _normalize_dt(req.first_scheduled_at or "")
    now = _now()
    first_session = {
        "id": _new_sid(),
        "idx": 1,
        "scheduled_at": first_dt,
        "status": "scheduled",
        "agendas": [],
        "minutes": None,
        "created_at": now,
        "updated_at": now,
    }
    entry = {
        "id": _new_mid(),
        "title": title,
        "owner": owner,
        "recurrence": rec,
        "status": "active",
        "sessions": [first_session],
        "created_by": me["username"],
        "created_at": now,
        "updated_at": now,
    }
    items = _load()
    items.append(entry)
    _save(items)
    _audit(request, "meetings:create",
           detail=f"id={entry['id']} title={title[:60]} rec={rec['type']}",
           tab="meetings")
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
    if req.status is not None:
        st = _validate_meeting_status(req.status)
        if st and st != m.get("status"):
            m["status"] = st
            changed.append("status")
    if req.recurrence is not None:
        rec = _normalize_recurrence(req.recurrence.dict())
        if rec != m.get("recurrence"):
            m["recurrence"] = rec
            changed.append("recurrence")
    if not changed:
        return {"ok": True, "meeting": m, "noop": True}
    m["updated_at"] = _now()
    items[idx] = m
    _save(items)
    _audit(request, "meetings:update",
           detail=f"id={m['id']} fields={','.join(changed)}", tab="meetings")
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
    _calendar_remove_meeting(id)
    _audit(request, "meetings:delete",
           detail=f"id={id} title={(m.get('title') or '')[:60]}", tab="meetings")
    return {"ok": True}


# ── sessions ──────────────────────────────────────────────────────
@router.post("/session/add")
def add_session(req: SessionAdd, request: Request):
    me = current_user(request)
    items = _load()
    idx, m = _find(items, req.meeting_id)
    if not m:
        raise HTTPException(404, "meeting not found")
    if not _can_edit_meeting(me, m):
        raise HTTPException(403, "Only owner or admin can add sessions")
    sched = _normalize_dt(req.scheduled_at or "")
    now = _now()
    new_s = {
        "id": _new_sid(),
        "idx": _next_session_idx(m),
        "scheduled_at": sched,
        "status": "scheduled",
        "agendas": [],
        "minutes": None,
        "created_at": now,
        "updated_at": now,
    }
    m.setdefault("sessions", []).append(new_s)
    m["updated_at"] = now
    items[idx] = m
    _save(items)
    _audit(request, "meetings:session_add",
           detail=f"meeting={m['id']} session={new_s['id']} idx={new_s['idx']}",
           tab="meetings")
    return {"ok": True, "meeting": m, "session": new_s}


@router.post("/session/update")
def update_session(req: SessionUpdate, request: Request):
    me = current_user(request)
    items = _load()
    idx, m = _find(items, req.meeting_id)
    if not m:
        raise HTTPException(404, "meeting not found")
    if not _can_edit_meeting(me, m):
        raise HTTPException(403, "Only owner or admin can edit sessions")
    sidx, s = _find_session(m, req.session_id)
    if sidx < 0:
        raise HTTPException(404, "session not found")
    changed = []
    if req.scheduled_at is not None:
        dt = _normalize_dt(req.scheduled_at)
        if dt != s.get("scheduled_at"):
            s["scheduled_at"] = dt
            changed.append("scheduled_at")
    if req.status is not None:
        st = _validate_session_status(req.status)
        if st and st != s.get("status"):
            s["status"] = st
            changed.append("status")
    if not changed:
        return {"ok": True, "meeting": m, "session": s, "noop": True}
    s["updated_at"] = _now()
    m["sessions"][sidx] = s
    m["updated_at"] = s["updated_at"]
    items[idx] = m
    _save(items)
    _audit(request, "meetings:session_update",
           detail=f"meeting={m['id']} session={s['id']} fields={','.join(changed)}",
           tab="meetings")
    return {"ok": True, "meeting": m, "session": s}


@router.post("/session/delete")
def delete_session(request: Request,
                   meeting_id: str = Query(...),
                   session_id: str = Query(...)):
    me = current_user(request)
    items = _load()
    idx, m = _find(items, meeting_id)
    if not m:
        raise HTTPException(404, "meeting not found")
    if not _can_edit_meeting(me, m):
        raise HTTPException(403, "Only owner or admin can delete sessions")
    sessions = m.get("sessions") or []
    if len(sessions) <= 1:
        raise HTTPException(400, "cannot delete the only session — delete the meeting instead")
    new_sessions = [s for s in sessions if s.get("id") != session_id]
    if len(new_sessions) == len(sessions):
        raise HTTPException(404, "session not found")
    m["sessions"] = new_sessions
    m["updated_at"] = _now()
    items[idx] = m
    _save(items)
    _calendar_remove_session(meeting_id, session_id)
    _audit(request, "meetings:session_delete",
           detail=f"meeting={meeting_id} session={session_id}", tab="meetings")
    return {"ok": True, "meeting": m}


# ── agendas (now per-session) ─────────────────────────────────────
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
    sidx, s = _find_session(m, req.session_id)
    if sidx < 0:
        raise HTTPException(404, "session not found")
    now = _now()
    ag = {
        "id": _new_aid(),
        "title": title,
        "description": (req.description or "").strip(),
        "link": (req.link or "").strip(),
        "owner": (req.owner or me["username"]).strip() or me["username"],
        "created_at": now,
        "updated_at": now,
    }
    s.setdefault("agendas", []).append(ag)
    s["updated_at"] = now
    m["sessions"][sidx] = s
    m["updated_at"] = now
    items[idx] = m
    _save(items)
    _audit(request, "meetings:agenda_add",
           detail=f"meeting={m['id']} session={s['id']} agenda={ag['id']} title={title[:60]}",
           tab="meetings")
    return {"ok": True, "meeting": m, "session": s, "agenda": ag}


@router.post("/agenda/update")
def update_agenda(req: AgendaUpdate, request: Request):
    me = current_user(request)
    items = _load()
    idx, m = _find(items, req.meeting_id)
    if not m:
        raise HTTPException(404, "meeting not found")
    sidx, s = _find_session(m, req.session_id)
    if sidx < 0:
        raise HTTPException(404, "session not found")
    agendas = s.get("agendas") or []
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
        return {"ok": True, "meeting": m, "session": s, "noop": True}
    ag["updated_at"] = _now()
    agendas[aidx] = ag
    s["agendas"] = agendas
    s["updated_at"] = ag["updated_at"]
    m["sessions"][sidx] = s
    m["updated_at"] = ag["updated_at"]
    items[idx] = m
    _save(items)
    _audit(request, "meetings:agenda_update",
           detail=f"meeting={m['id']} session={s['id']} agenda={ag['id']} fields={','.join(changed)}",
           tab="meetings")
    return {"ok": True, "meeting": m, "session": s, "agenda": ag}


@router.post("/agenda/delete")
def delete_agenda(
    request: Request,
    meeting_id: str = Query(...),
    session_id: str = Query(...),
    agenda_id: str = Query(...),
):
    me = current_user(request)
    items = _load()
    idx, m = _find(items, meeting_id)
    if not m:
        raise HTTPException(404, "meeting not found")
    sidx, s = _find_session(m, session_id)
    if sidx < 0:
        raise HTTPException(404, "session not found")
    agendas = s.get("agendas") or []
    ag = next((a for a in agendas if a.get("id") == agenda_id), None)
    if not ag:
        raise HTTPException(404, "agenda not found")
    if not _can_edit_agenda(me, m, ag):
        raise HTTPException(403, "Only agenda owner / meeting owner / admin can delete")
    s["agendas"] = [a for a in agendas if a.get("id") != agenda_id]
    s["updated_at"] = _now()
    m["sessions"][sidx] = s
    m["updated_at"] = s["updated_at"]
    items[idx] = m
    _save(items)
    _audit(request, "meetings:agenda_delete",
           detail=f"meeting={meeting_id} session={session_id} agenda={agenda_id}",
           tab="meetings")
    return {"ok": True, "meeting": m, "session": s}


# ── minutes (per-session) ─────────────────────────────────────────
@router.post("/minutes/save")
def save_minutes(req: MinutesSave, request: Request):
    me = current_user(request)
    items = _load()
    idx, m = _find(items, req.meeting_id)
    if not m:
        raise HTTPException(404, "meeting not found")
    if not _can_edit_meeting(me, m):
        raise HTTPException(403, "Only meeting owner or admin can write minutes")
    sidx, s = _find_session(m, req.session_id)
    if sidx < 0:
        raise HTTPException(404, "session not found")
    now = _now()
    # v8.7.5: decisions 는 {id,text,due} 객체 list 로 유지. 기존 calendar 상태 보존.
    prev_dec = ((s.get("minutes") or {}).get("decisions")) or []
    prev_dec_by_id = {d.get("id"): d for d in prev_dec if isinstance(d, dict) and d.get("id")}
    new_dec = _ensure_decision_objects(req.decisions or [])
    # inherit calendar_pushed state from prev by id
    for d in new_dec:
        pv = prev_dec_by_id.get(d["id"]) or {}
        if pv:
            d["calendar_pushed"] = bool(pv.get("calendar_pushed"))
            d["calendar_event_id"] = pv.get("calendar_event_id") or ""
            d["calendar_pushed_by"] = pv.get("calendar_pushed_by") or ""
            d["calendar_pushed_at"] = pv.get("calendar_pushed_at") or ""
    # decisions removed by this save → unpush calendar events
    kept_dids = {d["id"] for d in new_dec}
    for old in prev_dec:
        if isinstance(old, dict) and old.get("id") not in kept_dids and old.get("calendar_pushed"):
            try:
                from routers.calendar import unpush_action_item
                unpush_action_item(m["id"], s["id"], old["id"])
            except Exception:
                pass
    decisions = new_dec
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
    # Preserve existing ids / calendar_* fields when text matches by id
    prev_ai = ((s.get("minutes") or {}).get("action_items")) or []
    prev_by_id = {a.get("id"): a for a in prev_ai if isinstance(a, dict) and a.get("id")}
    merged = []
    for ai in ai_clean:
        aid = ai.get("id") or f"ai_{uuid.uuid4().hex[:8]}"
        prev = prev_by_id.get(aid) or {}
        merged.append({
            "id": aid,
            "text": ai["text"], "owner": ai["owner"], "due": ai["due"],
            "status": prev.get("status", "pending"),
            "calendar_pushed": bool(prev.get("calendar_pushed")),
            "calendar_event_id": prev.get("calendar_event_id") or "",
            "calendar_pushed_by": prev.get("calendar_pushed_by") or "",
            "calendar_pushed_at": prev.get("calendar_pushed_at") or "",
        })
    # Any previously-pushed action_items removed by this save → unpush & drop calendar event
    kept_ids = {a["id"] for a in merged}
    for old in prev_ai:
        if isinstance(old, dict) and old.get("id") not in kept_ids and old.get("calendar_pushed"):
            try:
                from routers.calendar import unpush_action_item
                unpush_action_item(m["id"], s["id"], old["id"])
            except Exception:
                pass
    s["minutes"] = {
        "body": (req.body or "").strip(),
        "decisions": decisions,
        "action_items": merged,
        "author": me["username"],
        "updated_at": now,
    }
    # Sync text/due changes to already-pushed calendar events.
    for ai in merged:
        if ai.get("calendar_pushed"):
            try:
                from routers.calendar import push_action_item
                push_action_item(m, s, ai, actor=ai.get("calendar_pushed_by") or me["username"],
                                 meeting_category=m.get("category") or "")
            except Exception:
                pass
    if (s.get("status") or "scheduled") not in ("completed", "cancelled"):
        s["status"] = "completed"
    s["updated_at"] = now
    m["sessions"][sidx] = s
    m["updated_at"] = now
    items[idx] = m
    _save(items)
    _audit(request, "meetings:minutes",
           detail=f"meeting={m['id']} session={s['id']} decisions={len(decisions)} actions={len(merged)}",
           tab="meetings")
    return {"ok": True, "meeting": m, "session": s}


# ── action_item ↔ calendar push/unpush ─────────────────────────
class ActionPushReq(BaseModel):
    meeting_id: str
    session_id: str
    action_item_id: str


@router.post("/action/push")
def push_action(req: ActionPushReq, request: Request):
    me = current_user(request)
    items = _load()
    midx, m = _find(items, req.meeting_id)
    if midx < 0 or not m:
        raise HTTPException(404, "meeting not found")
    sidx, s = _find_session(m, req.session_id)
    if sidx < 0:
        raise HTTPException(404, "session not found")
    ai_list = ((s.get("minutes") or {}).get("action_items")) or []
    ai = next((x for x in ai_list if isinstance(x, dict) and x.get("id") == req.action_item_id), None)
    if ai is None:
        raise HTTPException(404, "action_item not found")
    if not (ai.get("text") or "").strip() or not (ai.get("due") or "").strip():
        raise HTTPException(400, "action_item must have both text and due date to push")
    from routers.calendar import push_action_item
    ev = push_action_item(m, s, ai, actor=me["username"],
                          meeting_category=m.get("category") or "")
    if not ev:
        raise HTTPException(400, "calendar event could not be created")
    now = _now()
    ai["calendar_pushed"] = True
    ai["calendar_event_id"] = ev["id"]
    ai["calendar_pushed_by"] = me["username"]
    ai["calendar_pushed_at"] = now
    s["minutes"]["action_items"] = ai_list
    s["updated_at"] = now
    m["sessions"][sidx] = s
    m["updated_at"] = now
    items[midx] = m
    _save(items)
    _audit(request, "meetings:action_push",
           detail=f"meeting={m['id']} session={s['id']} ai={ai['id']} event={ev['id']}",
           tab="meetings")
    return {"ok": True, "meeting": m, "session": s, "event": ev}


# ── decision ↔ calendar push/unpush (v8.7.5) ─────────────
class DecisionPushReq(BaseModel):
    meeting_id: str
    session_id: str
    decision_id: str
    due: Optional[str] = ""  # YYYY-MM-DD; if empty, fallback to session scheduled_at or today


@router.post("/decision/push")
def push_decision(req: DecisionPushReq, request: Request):
    me = current_user(request)
    items = _load()
    midx, m = _find(items, req.meeting_id)
    if midx < 0 or not m:
        raise HTTPException(404, "meeting not found")
    sidx, s = _find_session(m, req.session_id)
    if sidx < 0:
        raise HTTPException(404, "session not found")
    minutes = s.get("minutes") or {}
    dec_list = minutes.get("decisions") or []
    # 다시 한 번 객체화 (문자열 형태로 저장된 legacy 대비)
    dec_list = _ensure_decision_objects(dec_list)
    target = next((d for d in dec_list if d.get("id") == req.decision_id), None)
    if target is None:
        raise HTTPException(404, "decision not found")
    due = (req.due or target.get("due") or "").strip()
    if not due:
        # fallback: session scheduled_at (date 부분) 또는 오늘
        sa = (s.get("scheduled_at") or "")[:10]
        due = sa or datetime.date.today().isoformat()
    from routers.calendar import push_action_item
    # action_item 과 동일한 함수 재사용 — id 는 decision_id 를 그대로 사용.
    synthetic = {"id": target["id"], "text": "[결정] " + (target.get("text") or ""),
                 "owner": "", "due": due}
    ev = push_action_item(m, s, synthetic, actor=me["username"],
                          meeting_category=m.get("category") or "")
    if not ev:
        raise HTTPException(400, "calendar event could not be created")
    target["calendar_pushed"] = True
    target["calendar_event_id"] = ev["id"]
    target["calendar_pushed_by"] = me["username"]
    target["calendar_pushed_at"] = _now()
    target["due"] = due
    # replace in list
    dec_list = [target if d.get("id") == target["id"] else d for d in dec_list]
    minutes["decisions"] = dec_list
    s["minutes"] = minutes
    s["updated_at"] = _now()
    m["sessions"][sidx] = s
    m["updated_at"] = s["updated_at"]
    items[midx] = m
    _save(items)
    _audit(request, "meetings:decision_push",
           detail=f"meeting={m['id']} session={s['id']} dec={target['id']}",
           tab="meetings")
    return {"ok": True, "meeting": m, "session": s, "event": ev}


@router.post("/decision/unpush")
def unpush_decision(req: DecisionPushReq, request: Request):
    me = current_user(request)
    items = _load()
    midx, m = _find(items, req.meeting_id)
    if midx < 0 or not m:
        raise HTTPException(404, "meeting not found")
    sidx, s = _find_session(m, req.session_id)
    if sidx < 0:
        raise HTTPException(404, "session not found")
    minutes = s.get("minutes") or {}
    dec_list = _ensure_decision_objects(minutes.get("decisions") or [])
    target = next((d for d in dec_list if d.get("id") == req.decision_id), None)
    if target is None:
        raise HTTPException(404, "decision not found")
    from routers.calendar import unpush_action_item
    unpush_action_item(m["id"], s["id"], target["id"])
    target["calendar_pushed"] = False
    target["calendar_event_id"] = ""
    dec_list = [target if d.get("id") == target["id"] else d for d in dec_list]
    minutes["decisions"] = dec_list
    s["minutes"] = minutes
    s["updated_at"] = _now()
    m["sessions"][sidx] = s
    m["updated_at"] = s["updated_at"]
    items[midx] = m
    _save(items)
    _audit(request, "meetings:decision_unpush",
           detail=f"meeting={m['id']} session={s['id']} dec={target['id']}",
           tab="meetings")
    return {"ok": True, "meeting": m, "session": s}


@router.post("/action/unpush")
def unpush_action(req: ActionPushReq, request: Request):
    me = current_user(request)
    items = _load()
    midx, m = _find(items, req.meeting_id)
    if midx < 0 or not m:
        raise HTTPException(404, "meeting not found")
    sidx, s = _find_session(m, req.session_id)
    if sidx < 0:
        raise HTTPException(404, "session not found")
    ai_list = ((s.get("minutes") or {}).get("action_items")) or []
    ai = next((x for x in ai_list if isinstance(x, dict) and x.get("id") == req.action_item_id), None)
    if ai is None:
        raise HTTPException(404, "action_item not found")
    from routers.calendar import unpush_action_item
    unpush_action_item(m["id"], s["id"], ai["id"])
    now = _now()
    ai["calendar_pushed"] = False
    ai["calendar_event_id"] = ""
    s["minutes"]["action_items"] = ai_list
    s["updated_at"] = now
    m["sessions"][sidx] = s
    m["updated_at"] = now
    items[midx] = m
    _save(items)
    _audit(request, "meetings:action_unpush",
           detail=f"meeting={m['id']} session={s['id']} ai={ai['id']}",
           tab="meetings")
    return {"ok": True, "meeting": m, "session": s}
