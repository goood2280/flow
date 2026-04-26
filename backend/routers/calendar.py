"""routers/calendar.py v8.7.8 — 변경점 기록 달력 + 회의 결정/액션아이템 auto-sync.

스키마 ({data_root}/calendar/events.json):
  [{id, version, date, end_date?, title, body, category, author,
    status: "pending"|"in_progress"|"done",
    source_type: "manual"|"meeting_decision"|"meeting_action",   # v8.7.8
    meeting_ref: { meeting_id, session_id, action_item_id, meeting_title } | null,
    created_at, updated_at, history:[{ts, actor, action, before:{...}}]}]

- date: 시작 ISO 'YYYY-MM-DD'.
- end_date: 선택 ISO 'YYYY-MM-DD' — 구간 이벤트(액션아이템). 없으면 date 단일.
- source_type:
    manual — 사용자가 달력에서 직접 만든 이벤트.
    meeting_decision — 회의 결정사항 (FE 는 filled 스타일, 회의 일자에 단일 표시).
    meeting_action — 회의 액션아이템 (FE 는 outline 스타일, 회의일 ~ due 구간 표시).
- meeting_ref.meeting_title: FE 필터/라벨용. sync 시 항상 current title 로 갱신.
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
  GET  /api/calendar/meetings                 # distinct meeting refs (for 필터)
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
    if "source_type" not in e:
        ref = e.get("meeting_ref") or {}
        if ref.get("meeting_id"):
            # Legacy — decision push used "[결정]" prefix; action push didn't.
            if (e.get("title") or "").startswith("[결정]"):
                e["source_type"] = "meeting_decision"
            else:
                e["source_type"] = "meeting_action"
        else:
            e["source_type"] = "manual"
    if "end_date" not in e:
        e["end_date"] = ""
    # v8.8.2: 이벤트 공개범위 — 비어있으면 전원 공개, 그룹 ID 지정 시 해당 그룹 멤버만 열람.
    if "group_ids" not in e:
        e["group_ids"] = []
    return e


def _event_visible(event: dict, username: str, role: str, my_group_ids: set) -> bool:
    """v8.8.2: group_ids 필터. admin 은 항상 가시. 본인 작성 이벤트는 항상 가시."""
    if role == "admin":
        return True
    gids = event.get("group_ids") or []
    if not gids:
        return True
    if event.get("author") == username:
        return True
    for g in gids:
        if g in my_group_ids:
            return True
    return False


def _my_group_ids(username: str, role: str) -> set:
    if role == "admin":
        try:
            from routers.groups import _load as _load_groups
            return {g.get("id") for g in _load_groups() if g.get("id")}
        except Exception:
            return set()
    try:
        from routers.groups import _load as _load_groups, _can_view
        return {g.get("id") for g in _load_groups()
                if g.get("id") and _can_view(g, username, role)}
    except Exception:
        return set()


def _load_events() -> list:
    data = load_json(EVENTS_FILE, [])
    if not isinstance(data, list):
        return []
    return [_upgrade_event(x) for x in data if isinstance(x, dict)]


def _save_events(items: list) -> None:
    save_json(EVENTS_FILE, items, indent=2)


# ── public helpers for meetings router ─────────────────────────
def _upsert_meeting_event(meeting: dict, session: dict, *,
                          ref_item_id: str,
                          source_type: str,
                          title: str,
                          date_s: str,
                          end_date: str,
                          body: str,
                          actor: str,
                          category: str) -> Optional[dict]:
    meeting_id = meeting.get("id") or ""
    session_id = session.get("id") or ""
    if not (meeting_id and session_id and ref_item_id and title and date_s):
        return None
    cat = (category or meeting.get("category") or "회의 결정사항").strip() or "회의 결정사항"
    meeting_title = (meeting.get("title") or "").strip()[:120]
    meeting_color = (meeting.get("color") or "").strip()  # v8.7.9 palette color
    items = _load_events()
    now = _now_iso()
    for i, e in enumerate(items):
        ref = e.get("meeting_ref") or {}
        if (ref.get("meeting_id") == meeting_id and ref.get("session_id") == session_id
                and ref.get("action_item_id") == ref_item_id):
            before = {}
            updates = {
                "title": title, "date": date_s, "end_date": end_date or "",
                "body": body, "category": cat, "source_type": source_type,
            }
            for fld, val in updates.items():
                if e.get(fld) != val:
                    before[fld] = e.get(fld); e[fld] = val
            # refresh meeting_title + color so filter labels and colors stay in sync
            if ref.get("meeting_title") != meeting_title or ref.get("color") != meeting_color:
                ref["meeting_title"] = meeting_title
                ref["color"] = meeting_color
                e["meeting_ref"] = ref
            if before:
                e["version"] = int(e.get("version") or 1) + 1
                e["updated_at"] = now
                hist = e.get("history") or []
                hist.append({"ts": now, "actor": actor, "action": "meeting_sync_update", "before": before})
                e["history"] = hist[-HIST_CAP:]
                items[i] = e
                _save_events(items)
            return e
    new_event = {
        "id": _new_id(),
        "version": 1,
        "date": date_s,
        "end_date": end_date or "",
        "title": title,
        "body": body,
        "category": cat,
        "author": actor,
        "status": "pending",
        "source_type": source_type,
        "meeting_ref": {"meeting_id": meeting_id, "session_id": session_id,
                        "action_item_id": ref_item_id,
                        "meeting_title": meeting_title,
                        "color": meeting_color},
        "created_at": now,
        "updated_at": now,
        "history": [{"ts": now, "actor": actor, "action": "meeting_sync_create", "before": {}}],
    }
    items.append(new_event)
    _save_events(items)
    return new_event


def push_action_item(meeting: dict, session: dict, action_item: dict,
                     actor: str, meeting_category: str = "") -> Optional[dict]:
    """Push a single action_item to calendar.

    v8.7.9: action_item = SINGLE-DAY pin on the due date (no range bar).
    If due missing, not pushed (skip).
    v8.8.3: title 앞에 담당자 이름을 명시 — 달력에서 한눈에 누구의 액션인지 보이게.
            예: "[담당:홍길동] 설계 리뷰 반영" (owner 없으면 기존 title 그대로).
    """
    raw_title = (action_item.get("text") or "").strip()
    owner = (action_item.get("owner") or "").strip()
    # title 길이 제한은 owner 포함 후 기준 — 최대 120자.
    if owner:
        title = f"[담당:{owner}] {raw_title}"[:120]
    else:
        title = raw_title[:120]
    due = _safe_date(action_item.get("due"))
    if not (title and due):
        return None
    body_parts = []
    if owner:
        body_parts.append(f"담당: {owner}")
    body_parts.append(f"마감: {due}")
    body = " · ".join(body_parts)
    return _upsert_meeting_event(
        meeting, session,
        ref_item_id=action_item.get("id") or "",
        source_type="meeting_action",
        title=title,
        date_s=due,
        end_date="",  # no range; pin on due date
        body=body,
        actor=actor,
        category=meeting_category,
    )


def push_decision_event(meeting: dict, session: dict, decision: dict,
                        actor: str, meeting_category: str = "") -> Optional[dict]:
    """Decision → single-day event on the session date.

    v8.7.9: title = "{N}차 회의 결정사항: {text}" so the session number is visible.
    """
    title = (decision.get("text") or "").strip()[:120]
    if not title:
        return None
    date_s = _safe_date(decision.get("due")) or _session_date(session)
    if not date_s:
        date_s = datetime.date.today().isoformat()
    sidx = session.get("idx")
    prefix = f"{sidx}차 회의 결정사항: " if sidx not in (None, "", 0) else "[결정] "
    return _upsert_meeting_event(
        meeting, session,
        ref_item_id=decision.get("id") or "",
        source_type="meeting_decision",
        title=f"{prefix}{title}",
        date_s=date_s,
        end_date="",
        body="",
        actor=actor,
        category=meeting_category,
    )


def _session_date(session: dict) -> str:
    return _safe_date((session.get("scheduled_at") or "")[:10])


def end_before(a: str, b: str) -> bool:
    try:
        return datetime.date.fromisoformat(b) < datetime.date.fromisoformat(a)
    except Exception:
        return False


def sync_session_to_calendar(meeting: dict, session: dict, actor: str) -> dict:
    """Replace all meeting_decision / meeting_action events for (meeting, session)
    with the current session's decisions + action_items. Called from meetings.save_minutes.

    Returns {created, updated, removed} counts.
    """
    meeting_id = meeting.get("id") or ""
    session_id = session.get("id") or ""
    if not (meeting_id and session_id):
        return {"created": 0, "updated": 0, "removed": 0}
    minutes = session.get("minutes") or {}
    decisions = [d for d in (minutes.get("decisions") or []) if isinstance(d, dict) and (d.get("text") or "").strip()]
    actions_all = [a for a in (minutes.get("action_items") or []) if isinstance(a, dict)]
    # only actions with a due become range events
    actions = [a for a in actions_all if (a.get("due") or "").strip() and (a.get("text") or "").strip()]
    category = meeting.get("category") or ""

    expected_ids: set = set()
    for d in decisions:
        expected_ids.add(d.get("id") or "")
    for a in actions:
        expected_ids.add(a.get("id") or "")

    # Remove stale meeting_* events for this session
    items = _load_events()
    keep = []
    removed = 0
    for e in items:
        ref = e.get("meeting_ref") or {}
        if (ref.get("meeting_id") == meeting_id and ref.get("session_id") == session_id
                and (e.get("source_type") or "").startswith("meeting_")):
            if ref.get("action_item_id") not in expected_ids:
                removed += 1
                continue
        keep.append(e)
    if removed:
        _save_events(keep)

    before_count = len(_load_events())
    created = updated = 0
    for d in decisions:
        ev = push_decision_event(meeting, session, d, actor=actor, meeting_category=category)
        if ev is None:
            continue
        # crude created/updated differentiation: new length delta
    for a in actions:
        push_action_item(meeting, session, a, actor=actor, meeting_category=category)
    after_count = len(_load_events())
    created = max(0, after_count - before_count)
    updated = max(0, len(decisions) + len(actions) - created)
    return {"created": created, "updated": updated, "removed": removed}


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
    end_date: Optional[str] = ""
    group_ids: List[str] = []        # v8.8.2: 공개범위 — 비우면 전원 공개


class EventUpdate(BaseModel):
    id: str
    version: int
    date: Optional[str] = None
    end_date: Optional[str] = None
    title: Optional[str] = None
    body: Optional[str] = None
    category: Optional[str] = None
    group_ids: Optional[List[str]] = None   # v8.8.2


class CategoriesSave(BaseModel):
    categories: List[dict]


@router.get("/events")
def list_events(request: Request, month: Optional[str] = Query(None), all: bool = Query(False)):
    """month=YYYY-MM → 해당 월(상하 14일 여유 포함). all=True 면 전체.
    range 이벤트(end_date)도 window 와 겹치면 포함.
    v8.8.2: group_ids 기반 가시성 필터."""
    me = current_user(request)
    role = me.get("role", "user")
    my_gids = _my_group_ids(me["username"], role)
    items = _load_events()
    items = [x for x in items if _event_visible(x, me["username"], role, my_gids)]
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
    out = []
    for x in items:
        s = (x.get("date") or "")
        e = (x.get("end_date") or "") or s
        if not s:
            continue
        # overlap: [s, e] ∩ [lo, hi) non-empty
        if e >= lo and s < hi:
            out.append(x)
    out.sort(key=lambda x: (x.get("date", ""), x.get("created_at", "")))
    return {"events": out}


@router.get("/meetings")
def list_meeting_refs():
    """달력에 등록된 회의별 이벤트 수 (FE 필터 드롭다운용)."""
    items = _load_events()
    agg: dict = {}
    for e in items:
        ref = e.get("meeting_ref") or {}
        mid = ref.get("meeting_id")
        if not mid:
            continue
        if mid not in agg:
            agg[mid] = {"meeting_id": mid,
                        "meeting_title": ref.get("meeting_title") or "",
                        "color": ref.get("color") or "",
                        "count": 0,
                        "decisions": 0,
                        "actions": 0}
        elif (ref.get("color") or "") and not agg[mid].get("color"):
            agg[mid]["color"] = ref.get("color") or ""
        agg[mid]["count"] += 1
        st = e.get("source_type")
        if st == "meeting_decision":
            agg[mid]["decisions"] += 1
        elif st == "meeting_action":
            agg[mid]["actions"] += 1
    out = sorted(agg.values(), key=lambda x: (x["meeting_title"] or "", x["meeting_id"]))
    return {"meetings": out}


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
    end_s = ""
    if (req.end_date or "").strip():
        end_s = _validate_date(req.end_date)
        if end_s < date_s:
            date_s, end_s = end_s, date_s
    entry = {
        "id": _new_id(),
        "version": 1,
        "date": date_s,
        "end_date": end_s,
        "title": title,
        "body": (req.body or "").strip(),
        "category": (req.category or "").strip(),
        "author": me["username"],
        "source_type": "manual",
        "meeting_ref": None,
        "status": "pending",
        "created_at": now,
        "updated_at": now,
        "history": [],
        "group_ids": [str(g).strip() for g in (req.group_ids or []) if g and str(g).strip()],
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
    for fld in ("date", "end_date", "title", "body", "category"):
        new_v = getattr(req, fld, None)
        if new_v is None:
            continue
        if fld == "date":
            new_v = _validate_date(new_v)
        elif fld == "end_date":
            new_v = _validate_date(new_v) if (new_v or "").strip() else ""
        else:
            new_v = (new_v or "").strip()
        if cur.get(fld, "") != new_v:
            before[fld] = cur.get(fld, "")
            cur[fld] = new_v
            changed = True
    # v8.8.2: group_ids 변경 반영.
    if req.group_ids is not None:
        new_gids = [str(g).strip() for g in (req.group_ids or []) if g and str(g).strip()]
        if sorted(cur.get("group_ids") or []) != sorted(new_gids):
            before["group_ids"] = cur.get("group_ids") or []
            cur["group_ids"] = new_gids
            changed = True
    # normalize: end_date >= date
    if cur.get("end_date") and cur.get("end_date") < cur.get("date"):
        cur["date"], cur["end_date"] = cur["end_date"], cur["date"]
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
