"""routers/messages.py v8.1.6 — User↔Admin 1:1 messages + Admin→All broadcast notices.

Storage:
  {data_root}/messages/threads/<user>.json   1:1 thread per user
  {data_root}/messages/notices.json          broadcast notice list

Thread schema:
  {
    "user": "<username>",
    "messages": [ {id, from, text, created_at}, ... ],
    "last_read_by_user":  "<iso>",   # any msg created_at > this & from != user  => unread
    "last_read_by_admin": "<iso>",   # any msg created_at > this & from == user  => unread for admin
    "updated_at": "<iso>"
  }

Notice schema:
  {id, author, title, body, created_at, read_by: [usernames...]}
"""
import datetime, uuid
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from core.paths import PATHS
from core.utils import load_json, save_json
from core.notify import send_notify, send_to_admins
from routers.auth import read_users

router = APIRouter(prefix="/api/messages", tags=["messages"])

MSG_DIR = PATHS.data_root / "messages"
THREADS_DIR = MSG_DIR / "threads"
NOTICES_FILE = MSG_DIR / "notices.json"
for _d in (MSG_DIR, THREADS_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _now():
    return datetime.datetime.now().isoformat()


def _new_id():
    return uuid.uuid4().hex[:10]


def _is_admin(username: str) -> bool:
    if not username:
        return False
    for u in read_users():
        if u["username"] == username and u.get("role") == "admin":
            return True
    return False


def _thread_path(user: str):
    safe = "".join(c for c in (user or "") if c.isalnum() or c in "_-.")[:60]
    if not safe:
        raise HTTPException(400, "Invalid username")
    return THREADS_DIR / f"{safe}.json"


def _empty_thread(user: str) -> dict:
    return {
        "user": user,
        "messages": [],
        "last_read_by_user": "",
        "last_read_by_admin": "",
        "updated_at": "",
    }


def _load_thread(user: str) -> dict:
    fp = _thread_path(user)
    if not fp.exists():
        return _empty_thread(user)
    t = load_json(fp, _empty_thread(user))
    for k, v in _empty_thread(user).items():
        if k not in t:
            t[k] = v
    if not t.get("user"):
        t["user"] = user
    return t


def _save_thread(user: str, thread: dict):
    save_json(_thread_path(user), thread, indent=2)


def _load_notices() -> list:
    data = load_json(NOTICES_FILE, [])
    return data if isinstance(data, list) else []


def _save_notices(notices: list):
    save_json(NOTICES_FILE, notices, indent=2)


def _thread_unread_for_user(thread: dict) -> int:
    last_read = thread.get("last_read_by_user") or ""
    user = thread.get("user") or ""
    cnt = 0
    for m in thread.get("messages", []):
        if m.get("from") == user:
            continue
        if (m.get("created_at") or "") > last_read:
            cnt += 1
    return cnt


def _thread_unread_for_admin(thread: dict) -> int:
    last_read = thread.get("last_read_by_admin") or ""
    user = thread.get("user") or ""
    cnt = 0
    for m in thread.get("messages", []):
        if m.get("from") != user:
            continue
        if (m.get("created_at") or "") > last_read:
            cnt += 1
    return cnt


# ─── Pydantic ───
class SendReq(BaseModel):
    username: str
    text: str


class UserOnlyReq(BaseModel):
    username: str


class AdminReplyReq(BaseModel):
    admin: str
    to_user: str
    text: str


class AdminThreadReq(BaseModel):
    admin: str
    to_user: str


class NoticeReadReq(BaseModel):
    username: str
    ids: Optional[List[str]] = None  # None = mark all unread


class NoticeCreateReq(BaseModel):
    author: str
    title: str = ""
    body: str = ""


class NoticeDeleteReq(BaseModel):
    admin: str
    id: str


# ─── User endpoints ───
@router.get("/thread")
def get_thread(username: str = Query(...)):
    return _load_thread(username)


@router.post("/send")
def user_send(req: SendReq):
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(400, "Empty message")
    if len(text) > 5000:
        raise HTTPException(400, "Too long (max 5000 chars)")
    t = _load_thread(req.username)
    msg = {"id": _new_id(), "from": req.username, "text": text, "created_at": _now()}
    t["messages"].append(msg)
    t["updated_at"] = msg["created_at"]
    t["last_read_by_user"] = msg["created_at"]  # sender implicitly caught up
    _save_thread(req.username, t)
    # Bell-notify all admins
    try:
        send_to_admins(
            f"Message from {req.username}",
            text[:200] + ("…" if len(text) > 200 else ""),
            "message",
        )
    except Exception:
        pass
    return {"ok": True, "message": msg}


@router.get("/unread")
def unread_count(username: str = Query(...)):
    """Unread summary for user — used for nav badge and home popup."""
    t = _load_thread(username)
    thread_n = _thread_unread_for_user(t)
    notices = _load_notices()
    notice_items = []
    for n in notices:
        if n.get("author") == username:
            continue
        if username in (n.get("read_by") or []):
            continue
        notice_items.append({
            "id": n["id"],
            "title": n.get("title", ""),
            "body": (n.get("body") or "")[:200],
            "author": n.get("author", ""),
            "created_at": n.get("created_at", ""),
        })
    # preview of most recent unread reply (last one)
    last_reply = None
    last_read = t.get("last_read_by_user") or ""
    user = t.get("user") or username
    for m in t.get("messages", []):
        if m.get("from") == user:
            continue
        if (m.get("created_at") or "") <= last_read:
            continue
        last_reply = {
            "id": m["id"],
            "from": m.get("from", ""),
            "text": (m.get("text") or "")[:200],
            "created_at": m.get("created_at", ""),
        }
    return {
        "thread_unread": thread_n,
        "notice_unread": len(notice_items),
        "total": thread_n + len(notice_items),
        "last_reply": last_reply,
        "unread_notices": notice_items,
    }


@router.post("/mark_read")
def user_mark_read(req: UserOnlyReq):
    t = _load_thread(req.username)
    t["last_read_by_user"] = _now()
    _save_thread(req.username, t)
    return {"ok": True}


@router.get("/notices")
def list_notices(username: str = Query("")):
    notices = _load_notices()
    out = []
    for n in notices:
        out.append({
            "id": n.get("id", ""),
            "author": n.get("author", ""),
            "title": n.get("title", ""),
            "body": n.get("body", ""),
            "created_at": n.get("created_at", ""),
            "read": username in (n.get("read_by") or []),
        })
    out.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return {"notices": out}


@router.post("/notice_read")
def notice_read(req: NoticeReadReq):
    notices = _load_notices()
    target = set(req.ids) if req.ids else None
    for n in notices:
        if target is not None and n.get("id") not in target:
            continue
        rb = list(n.get("read_by") or [])
        if req.username and req.username not in rb:
            rb.append(req.username)
            n["read_by"] = rb
    _save_notices(notices)
    return {"ok": True}


# ─── Admin endpoints ───
@router.get("/admin/threads")
def admin_threads(admin: str = Query(...)):
    if not _is_admin(admin):
        raise HTTPException(403, "Admin only")
    out = []
    for fp in sorted(THREADS_DIR.glob("*.json")):
        try:
            t = load_json(fp, None)
            if not t:
                continue
            user = t.get("user") or fp.stem
            msgs = t.get("messages") or []
            last_msg = msgs[-1] if msgs else {}
            out.append({
                "user": user,
                "total": len(msgs),
                "unread_for_admin": _thread_unread_for_admin(t),
                "last_at": t.get("updated_at") or last_msg.get("created_at", ""),
                "last_from": last_msg.get("from", ""),
                "last_preview": (last_msg.get("text") or "")[:120],
            })
        except Exception:
            continue
    out.sort(key=lambda x: (-x["unread_for_admin"], x["last_at"]), reverse=False)
    # After the above key, we want unread first (desc), then most recent first.
    out.sort(key=lambda x: (x["unread_for_admin"] == 0, -len(x["last_at"]), x["last_at"]), reverse=False)
    # Simpler: just two-pass
    out.sort(key=lambda x: x["last_at"], reverse=True)
    out.sort(key=lambda x: x["unread_for_admin"], reverse=True)
    return {"threads": out}


@router.get("/admin/thread")
def admin_get_thread(admin: str = Query(...), user: str = Query(...)):
    if not _is_admin(admin):
        raise HTTPException(403, "Admin only")
    return _load_thread(user)


@router.post("/admin/reply")
def admin_reply(req: AdminReplyReq):
    if not _is_admin(req.admin):
        raise HTTPException(403, "Admin only")
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(400, "Empty reply")
    if len(text) > 5000:
        raise HTTPException(400, "Too long (max 5000 chars)")
    t = _load_thread(req.to_user)
    msg = {"id": _new_id(), "from": req.admin, "text": text, "created_at": _now()}
    t["messages"].append(msg)
    t["updated_at"] = msg["created_at"]
    t["last_read_by_admin"] = msg["created_at"]
    _save_thread(req.to_user, t)
    try:
        send_notify(
            req.to_user,
            "Admin replied",
            text[:200] + ("…" if len(text) > 200 else ""),
            "message",
        )
    except Exception:
        pass
    return {"ok": True, "message": msg}


@router.post("/admin/mark_read")
def admin_mark_read(req: AdminThreadReq):
    if not _is_admin(req.admin):
        raise HTTPException(403, "Admin only")
    t = _load_thread(req.to_user)
    t["last_read_by_admin"] = _now()
    _save_thread(req.to_user, t)
    return {"ok": True}


@router.get("/admin/unread")
def admin_unread(admin: str = Query(...)):
    """Total unread replies across all threads, for admin dashboard."""
    if not _is_admin(admin):
        raise HTTPException(403, "Admin only")
    total = 0
    for fp in THREADS_DIR.glob("*.json"):
        t = load_json(fp, None)
        if t:
            total += _thread_unread_for_admin(t)
    return {"total": total}


@router.get("/admin/notices")
def admin_list_notices(admin: str = Query(...)):
    if not _is_admin(admin):
        raise HTTPException(403, "Admin only")
    notices = _load_notices()
    notices_sorted = sorted(
        notices, key=lambda x: x.get("created_at", ""), reverse=True
    )
    # attach recipient count
    try:
        approved = [u for u in read_users() if u.get("status") == "approved"]
        total_recipients = max(0, len(approved) - 1)  # exclude author roughly
    except Exception:
        total_recipients = 0
    for n in notices_sorted:
        n["read_count"] = len(n.get("read_by") or [])
        n["total_recipients"] = total_recipients
    return {"notices": notices_sorted}


@router.post("/admin/notice_create")
def admin_notice_create(req: NoticeCreateReq):
    if not _is_admin(req.author):
        raise HTTPException(403, "Admin only")
    title = (req.title or "").strip()[:200]
    body = (req.body or "").strip()[:5000]
    if not title and not body:
        raise HTTPException(400, "Empty notice")
    notices = _load_notices()
    notice = {
        "id": _new_id(),
        "author": req.author,
        "title": title,
        "body": body,
        "created_at": _now(),
        "read_by": [req.author],  # author has "read" their own
    }
    notices.append(notice)
    _save_notices(notices)
    # Bell-notify all approved non-admin users
    try:
        for u in read_users():
            if u.get("status") == "approved" and u.get("role") != "admin":
                send_notify(
                    u["username"],
                    "New Notice",
                    title or (body[:120] if body else "(no content)"),
                    "message",
                )
    except Exception:
        pass
    return {"ok": True, "notice": notice}


@router.post("/admin/notice_delete")
def admin_notice_delete(req: NoticeDeleteReq):
    if not _is_admin(req.admin):
        raise HTTPException(403, "Admin only")
    notices = [n for n in _load_notices() if n.get("id") != req.id]
    _save_notices(notices)
    return {"ok": True}
