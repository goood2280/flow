"""routers/calendar.py v8.7.4 — 변경점 기록 달력 + 회의 액션아이템 연동.

스키마 ({data_root}/calendar/events.json):
  [{id, version, date, title, body, category, author,
    status: "pending"|"in_progress"|"done",   # v8.7.4
    meeting_ref: { meeting_id, session_id, action_item_id } | null,  # v8.7.4
    created_at, updated_at, history:[{ts, actor, action, before:{...}}]}]

- date: ISO 'YYYY-MM-DD'.
- category: 자유 문자열 (FE 가 색을 입힘). 카테고리 팔레트는 별도 파일.
- meeting_ref: meetings.py 의 `minutes.action_items` 에서 자동 생성된 경우 설정.
  수동 생성 이벤트는 null.  meetings 쪽 변경 시 sync_meeting_session() 호출.
- 낙관적 잠금: 저장 시 client 가 보낸 version 이 서버 version 과 일치해야 PUT 성공.
- 추적 관리: title/body/category/date/status 변경 시 history 에 before 누적.

Endpoints:
  GET  /api/calendar/events?month=YYYY-MM
  GET  /api/calendar/events/search?q=...
  GET  /api/calendar/event/{id}
  POST /api/calendar/event
  POST /api/calendar/event/update
  POST /api/calendar/event/status   {id, status}
  POST /api/calendar/event/delete?id=...
  GET  /api/calendar/categories
  POST /api/calendar/categories/save
"""
import datetime
import uuid
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from core.paths import PATHS
from core.utils import load_json, save_json
from core.auth import current_user, require_admin
from core.audit import record as _audit

router = APIRouter(prefix="/api/calendar", tags=["calendar"])

CAL_DIR = PATHS.data_root / "calendar"
CAL_DIR.mkdir(parents=True, exist_ok=True)
EVENTS_FILE = CAL_DIR / "events.json"
CATS_FILE = CAL_DIR / "categories.json"

DEFAULT_CATEGORIES = [
    {"name": "회의 결정사항", "color": "#3b82f6"},
    {"name": "공정 변경", "color": "#f59e0b"},
    {"name": "장비 PM", "color": "#ef4444"},
    {"name": "릴리즈", "color": "#22c55e"},
    {"name": "기타", "color": "#6b7280"},
]

HIST_CAP = 50
VALID_EVENT_STATUS = {"pending", "in_progress", "done"}


def _upgrade_event(e: dict) -> dict:
    if "status" not in e:
        e["status"] = "pending"
    if "meeting_ref" not in e:
        e["meeting_ref"] = None
    return e


def _load_events() -> list:
    data = load_json(EVENTS_FILE, [])
    if not isinstance(data, list):
        return []
    return [_upgrade_event(x) for x in data if isinstance(x, dict)]


def _save_events(items: list) -> None:
    save_json(EVENTS_FILE, items, indent=2)


# ── public helpers for meetings router ─────────────────────────
def push_action_item(meeting: dict, session: dict, action_item: dict,
                     actor: str, meeting_category: str = "") -> Optional[dict]:
    """Push a single action_item to calendar. Returns created event dict or None.

    If already pushed (an event exists with same meeting_ref), just update it.
    Date is required (action_item.due); text becomes title; owner goes to body.
    """
    meeting_id = meeting.get("id") or ""
    session_id = session.get("id") or ""
    aid = action_item.get("id")
    if not (meeting_id and session_id and aid):
        return None
    title = (action_item.get("text") or "").strip()[:120]
    date_s = _safe_date(action_item.get("due"))
    if not (title and date_s):
        return None
    cat = (meeting_category or meeting.get("category") or "회의 결정사항").strip() or "회의 결정사항"
    body = (action_item.get("owner") and f"담당: {action_item['owner']}") or ""
    items = _load_events()
    now = _now_iso()
    # existing?
    for i, e in enumerate(items):
        ref = e.get("meeting_ref") or {}
        if (ref.get("meeting_id") == meeting_id and ref.get("session_id") == session_id
                and ref.get("action_item_id") == aid):
            before = {}
            for fld, val in (("title", title), ("date", date_s), ("body", body), ("category", cat)):
                if e.get(fld) != val:
                    before[fld] = e.get(fld); e[fld] = val
            if before:
                e["version"] = int(e.get("version") or 1) + 1
                e["updated_at"] = now
                hist = e.get("history") or []
                hist.append({"ts": now, "actor": actor, "action": "meeting_push_update", "before": before})
                e["history"] = hist[-HIST_CAP:]
                items[i] = e
                _save_events(items)
            return e
    new_event = {
        "id": _new_id(),
        "version": 1,
        "date": date_s,
        "title": title,
        "body": body,
        "category": cat,
        "author": actor,
        "status": "pending",
        "meeting_ref": {"meeting_id": meeting_id, "session_id": session_id, "action_item_id": aid},
        "created_at": now,
        "updated_at": now,
        "history": [{"ts": now, "actor": actor, "action": "meeting_push_create", "before": {}}],
    }
    items.append(new_event)
    _save_events(items)
    return new_event


def unpush_action_item(meeting_id: str, session_id: str, action_item_id: str) -> bool:
    items = _load_events()
    new_items = [e for e in items
                 if not ((e.get("meeting_ref") or {}).get("meeting_id") == meeting_id
                         and (e.get("meeting_ref") or {}).get("session_id") == session_id
                         and (e.get("meeting_ref") or {}).get("action_item_id") == action_item_id)]
    if len(new_items) != len(items):
        _save_events(new_items)
        return True
    return False


def find_pushed_event(meeting_id: str, session_id: str, action_item_id: str) -> Optional[dict]:
    for e in _load_events():
        ref = e.get("meeting_ref") or {}
        if (ref.get("meeting_id") == meeting_id and ref.get("session_id") == session_id
                and ref.get("action_item_id") == action_item_id):
            return e
    return None


def remove_events_for_meeting(meeting_id: str) -> None:
    items = _load_events()
    new_items = [e for e in items if (e.get("meeting_ref") or {}).get("meeting_id") != meeting_id]
    if len(new_items) != len(items):
        _save_events(new_items)


def remove_events_for_session(meeting_id: str, session_id: str) -> None:
    items = _load_events()
    new_items = []
    removed = 0
    for e in items:
        ref = e.get("meeting_ref") or {}
        if ref.get("meeting_id") == meeting_id and ref.get("session_id") == session_id:
            removed += 1
            continue
        new_items.append(e)
    if removed:
        _save_events(new_items)


def _safe_date(s: str) -> str:
    try:
        return datetime.date.fromisoformat((s or "")[:10]).isoformat()
    except Exception:
        return ""


def _load_cats() -> list:
    data = load_json(CATS_FILE, None)
    if not isinstance(data, list) or not data:
        return DEFAULT_CATEGORIES
    return data


def _save_cats(items: list) -> None:
    save_json(CATS_FILE, items, indent=2)


def _new_id() -> str:
    return f"cal_{datetime.datetime.now().strftime('%y%m%d')}_{uuid.uuid4().hex[:6]}"


def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _validate_date(s: str) -> str:
    s = (s or "").strip()
    try:
        # accept full ISO; truncate to YYYY-MM-DD
        return datetime.date.fromisoformat(s[:10]).isoformat()
    except Exception:
        raise HTTPException(400, "Invalid date (expected YYYY-MM-DD)")


class EventCreate(BaseModel):
    date: str
    title: str
    body: str = ""
    category: str = ""


class EventUpdate(BaseModel):
    id: str
    version: int
    date: Optional[str] = None
    title: Optional[str] = None
    body: Optional[str] = None
    category: Optional[str] = None


class CategoriesSave(BaseModel):
    categories: List[dict]


@router.get("/events")
def list_events(month: Optional[str] = Query(None), all: bool = Query(False)):
    """month=YYYY-MM → 해당 월(상하 14일 여유 포함). all=True 면 전체."""
    items = _load_events()
    if all or not month:
        items.sort(key=lambda x: (x.get("date", ""), x.get("created_at", "")))
        return {"events": items}
    try:
        y, m = month.split("-")
        first = datetime.date(int(y), int(m), 1)
    except Exception:
        raise HTTPException(400, "Invalid month (expected YYYY-MM)")
    if first.month == 12:
        next_first = datetime.date(first.year + 1, 1, 1)
    else:
        next_first = datetime.date(first.year, first.month + 1, 1)
    lo = (first - datetime.timedelta(days=14)).isoformat()
    hi = (next_first + datetime.timedelta(days=14)).isoformat()
    out = [x for x in items if lo <= (x.get("date") or "") < hi]
    out.sort(key=lambda x: (x.get("date", ""), x.get("created_at", "")))
    return {"events": out}


@router.get("/events/search")
def search_events(q: str = Query(..., min_length=1), limit: int = Query(100, ge=1, le=500)):
    needle = q.lower()
    items = _load_events()
    out = []
    for x in items:
        hay = " ".join([
            x.get("title", ""), x.get("body", ""), x.get("author", ""),
            x.get("category", ""), x.get("date", ""),
        ]).lower()
        if needle in hay:
            out.append(x)
    out.sort(key=lambda x: x.get("date", ""), reverse=True)
    return {"events": out[:limit]}


@router.get("/event/{eid}")
def get_event(eid: str):
    items = _load_events()
    e = next((x for x in items if x.get("id") == eid), None)
    if not e:
        raise HTTPException(404)
    return {"event": e}


@router.post("/event")
def create_event(req: EventCreate, request: Request):
    me = current_user(request)
    title = (req.title or "").strip()
    if not title:
        raise HTTPException(400, "title required")
    date_s = _validate_date(req.date)
    items = _load_events()
    now = _now_iso()
    entry = {
        "id": _new_id(),
        "version": 1,
        "date": date_s,
        "title": title,
        "body": (req.body or "").strip(),
        "category": (req.category or "").strip(),
        "author": me["username"],
        "created_at": now,
        "updated_at": now,
        "history": [],
    }
    items.append(entry)
    _save_events(items)
    _audit(request, "calendar:create", detail=f"id={entry['id']} date={date_s} title={title[:60]}", tab="calendar")
    return {"ok": True, "event": entry}


@router.post("/event/update")
def update_event(req: EventUpdate, request: Request):
    me = current_user(request)
    items = _load_events()
    idx = next((i for i, x in enumerate(items) if x.get("id") == req.id), -1)
    if idx < 0:
        raise HTTPException(404)
    cur = items[idx]
    if me.get("role") != "admin" and cur.get("author") != me["username"]:
        raise HTTPException(403, "Only author or admin can edit")
    server_v = int(cur.get("version", 1))
    if int(req.version or 0) != server_v:
        # 충돌 — 최신 데이터 반환
        return {
            "ok": False,
            "conflict": True,
            "server_version": server_v,
            "event": cur,
            "detail": "Version conflict — another user has modified this event.",
        }
    # diff & history
    before = {}
    changed = False
    for fld in ("date", "title", "body", "category"):
        new_v = getattr(req, fld, None)
        if new_v is None:
            continue
        if fld == "date":
            new_v = _validate_date(new_v)
        else:
            new_v = (new_v or "").strip()
        if cur.get(fld, "") != new_v:
            before[fld] = cur.get(fld, "")
            cur[fld] = new_v
            changed = True
    if not changed:
        return {"ok": True, "event": cur, "noop": True}
    cur["version"] = server_v + 1
    cur["updated_at"] = _now_iso()
    hist = cur.get("history") or []
    hist.append({
        "ts": cur["updated_at"], "actor": me["username"],
        "action": "update", "before": before,
    })
    cur["history"] = hist[-HIST_CAP:]
    items[idx] = cur
    _save_events(items)
    _audit(request, "calendar:update", detail=f"id={cur['id']} fields={','.join(before.keys())}", tab="calendar")
    return {"ok": True, "event": cur}


class EventStatusReq(BaseModel):
    id: str
    status: str


@router.post("/event/status")
def set_event_status(req: EventStatusReq, request: Request):
    me = current_user(request)
    st = (req.status or "").strip()
    if st not in VALID_EVENT_STATUS:
        raise HTTPException(400, f"Invalid status: {st}")
    items = _load_events()
    idx = next((i for i, x in enumerate(items) if x.get("id") == req.id), -1)
    if idx < 0:
        raise HTTPException(404)
    cur = items[idx]
    # Anyone can update status (progress tracking is collaborative).
    if cur.get("status") == st:
        return {"ok": True, "event": cur, "noop": True}
    before = {"status": cur.get("status")}
    cur["status"] = st
    cur["version"] = int(cur.get("version") or 1) + 1
    cur["updated_at"] = _now_iso()
    hist = cur.get("history") or []
    hist.append({"ts": cur["updated_at"], "actor": me["username"],
                 "action": "status", "before": before})
    cur["history"] = hist[-HIST_CAP:]
    items[idx] = cur
    _save_events(items)
    _audit(request, "calendar:status", detail=f"id={cur['id']} -> {st}", tab="calendar")
    # If this event is synced from a meeting action_item, mirror status back.
    ref = cur.get("meeting_ref") or {}
    if ref.get("meeting_id") and ref.get("session_id") and ref.get("action_item_id"):
        try:
            from routers.meetings import mirror_action_item_status as _mirror  # lazy import
            _mirror(ref["meeting_id"], ref["session_id"], ref["action_item_id"], st)
        except Exception:
            pass
    return {"ok": True, "event": cur}


@router.post("/event/delete")
def delete_event(request: Request, id: str = Query(...)):
    me = current_user(request)
    items = _load_events()
    target = next((x for x in items if x.get("id") == id), None)
    if not target:
        raise HTTPException(404)
    if me.get("role") != "admin" and target.get("author") != me["username"]:
        raise HTTPException(403, "Only author or admin can delete")
    items = [x for x in items if x.get("id") != id]
    _save_events(items)
    _audit(request, "calendar:delete", detail=f"id={id} title={(target.get('title') or '')[:60]}", tab="calendar")
    return {"ok": True}


@router.get("/categories")
def get_categories():
    return {"categories": _load_cats()}


@router.post("/categories/save")
def save_categories(req: CategoriesSave, request: Request):
    require_admin(request)
    cats = []
    seen = set()
    for c in req.categories or []:
        n = (c.get("name") or "").strip()
        col = (c.get("color") or "").strip()
        if not n or n in seen:
            continue
        seen.add(n)
        cats.append({"name": n, "color": col or "#6b7280"})
    if not cats:
        raise HTTPException(400, "At least one category required")
    _save_cats(cats)
    return {"ok": True, "categories": cats}
