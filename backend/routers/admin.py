"""routers/admin.py v6.1.0 - Admin: users/permissions/logs/notify/downloads + batch dismiss + global settings + data_roots (v830)"""
import os
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional, Dict
from core.paths import PATHS
from core.utils import jsonl_append, jsonl_read, load_json, save_json
from core.notify import (
    send_notify, get_notifications, mark_all_read, send_to_admins,
    dismiss_notification, dismiss_by_ids, mark_read_by_ids,
)
from routers.auth import read_users, write_users

router = APIRouter(prefix="/api/admin", tags=["admin"])
DL_LOG = PATHS.download_log
ACTIVITY_LOG = PATHS.activity_log
SETTINGS_FILE = PATHS.data_root / "settings.json"
# v8.3.0: data_roots runtime overrides live in a separate file that core/roots.py
# read-peeks. Kept distinct from settings.json so legacy UI/refresh settings and
# root-path overrides have independent schemas.
ADMIN_SETTINGS_FILE = PATHS.data_root / "admin_settings.json"
DEFAULT_SETTINGS = {
    "dashboard_refresh_minutes": 10,  # auto-refresh interval (frontend)
    "dashboard_bg_refresh_minutes": 10,  # backend scheduled recompute (if any)
}

# Map UI-facing long keys to admin_settings.json short keys used by core/roots.py
_DR_KEY_MAP = {
    "db_root":        "db",
    "base_root":      "base",
    "wafer_map_root": "wafer_map",
}
_DR_ENV_MAP = {
    "db_root":        ("FABCANVAS_DB_ROOT", "HOL_DB_ROOT"),
    "base_root":      ("FABCANVAS_BASE_ROOT",),
    "wafer_map_root": ("FABCANVAS_WAFER_MAP_ROOT",),
}


def _resolver_snapshot() -> Dict[str, str]:
    """Call core.roots.snapshot() if available; else env+default fallback."""
    try:
        from core import roots as _roots  # type: ignore
        snap = _roots.snapshot()
        return {
            "db_root":        snap.get("db_root", ""),
            "base_root":      snap.get("base_root", ""),
            "wafer_map_root": snap.get("wafer_map_root", ""),
        }
    except Exception:
        # Fallback: env var > data_root default. Does NOT consult admin_settings.
        db = os.environ.get("FABCANVAS_DB_ROOT") or os.environ.get("HOL_DB_ROOT") \
            or str(PATHS.db_root)
        base = os.environ.get("FABCANVAS_BASE_ROOT") or str(PATHS.data_root / "Base")
        wm = os.environ.get("FABCANVAS_WAFER_MAP_ROOT") or str(Path(db) / "wafer_maps")
        return {"db_root": db, "base_root": base, "wafer_map_root": wm}


def _data_root_source(ui_key: str) -> str:
    """Classify where the effective value came from: env | settings | default."""
    for env_name in _DR_ENV_MAP.get(ui_key, ()):
        if os.environ.get(env_name):
            return "env"
    short = _DR_KEY_MAP.get(ui_key)
    if short:
        cfg = load_json(ADMIN_SETTINGS_FILE, {}) or {}
        dr = cfg.get("data_roots") or {}
        v = dr.get(short)
        if isinstance(v, str) and v.strip():
            return "settings"
    return "default"


def _load_admin_settings() -> dict:
    data = load_json(ADMIN_SETTINGS_FILE, {})
    return data if isinstance(data, dict) else {}


def _save_admin_settings(data: dict) -> None:
    save_json(ADMIN_SETTINGS_FILE, data)


class ApproveReq(BaseModel):
    username: str
class PermReq(BaseModel):
    username: str
    tabs: list
class MessageReq(BaseModel):
    to_user: str
    message: str
class LogEntry(BaseModel):
    username: str = ""
    action: str = ""
    tab: str = ""
    detail: str = ""
class DismissReq(BaseModel):
    username: str
    index: int
class BatchDismissReq(BaseModel):
    username: str
    ids: List[str]
class MarkReadReq(BaseModel):
    username: str
    ids: List[str]


# ── Users ──
@router.get("/users")
def list_users():
    return {"users": read_users()}


@router.post("/approve")
def approve_user(req: ApproveReq):
    users = read_users()
    for u in users:
        if u["username"] == req.username:
            u["status"] = "approved"
            if not u.get("tabs"):
                u["tabs"] = "filebrowser,dashboard,splittable"
            write_users(users)
            send_notify(req.username, "Account Approved",
                        "Your account has been approved.", "info")
            return {"ok": True}
    raise HTTPException(404)


@router.post("/reject")
def reject_user(req: ApproveReq):
    users = [u for u in read_users() if u["username"] != req.username]
    write_users(users)
    return {"ok": True}


@router.post("/reset-password")
def reset_password(req: ApproveReq):
    from routers.auth import hash_pw
    users = read_users()
    for u in users:
        if u["username"] == req.username:
            u["password_hash"] = hash_pw("1111")
            write_users(users)
            send_notify(req.username, "Password Reset",
                        "Your password has been reset to default.", "info")
            return {"ok": True, "new_password": "1111"}
    raise HTTPException(404)


@router.post("/delete-user")
def delete_user(req: ApproveReq):
    users = [u for u in read_users() if u["username"] != req.username]
    write_users(users)
    return {"ok": True}


# ── Permissions ──
@router.post("/set-tabs")
def set_tabs(req: PermReq):
    users = read_users()
    for u in users:
        if u["username"] == req.username:
            u["tabs"] = ",".join(req.tabs)
            write_users(users)
            return {"ok": True}
    raise HTTPException(404)


@router.get("/user-tabs")
def get_user_tabs(username: str = Query(...)):
    for u in read_users():
        if u["username"] == username:
            if u.get("role") == "admin":
                return {"tabs": "__all__"}
            return {"tabs": u.get("tabs", "filebrowser,dashboard,splittable")}
    raise HTTPException(404)


# ── Messaging ──
@router.post("/send-message")
def send_message(req: MessageReq):
    send_notify(req.to_user, "Message from Admin", req.message, "message")
    return {"ok": True}


class InquiryReq(BaseModel):
    username: str
    message: str


@router.post("/send-inquiry")
def send_inquiry(req: InquiryReq):
    """User sends inquiry to all admins."""
    send_to_admins(
        f"Inquiry from {req.username}",
        req.message,
        "message",
    )
    # Also notify the user that their inquiry was sent
    send_notify(req.username, "Inquiry Sent", "Your message has been sent to admin.", "info")
    return {"ok": True}


@router.post("/broadcast")
def broadcast(req: MessageReq):
    for u in read_users():
        if u["status"] == "approved":
            send_notify(u["username"], "Broadcast", req.message, "message")
    return {"ok": True}


# ── Notifications ──
@router.get("/my-notifications")
def my_notifications(username: str = Query(...)):
    notifs = get_notifications(username, unread_only=True)
    return {"notifications": notifs, "count": len(notifs)}


@router.get("/all-notifications")
def all_notifications(username: str = Query(...)):
    return {"notifications": get_notifications(username)}


@router.post("/mark-read")
def mark_read(req: ApproveReq):
    mark_all_read(req.username)
    return {"ok": True}


@router.post("/dismiss")
def dismiss(req: DismissReq):
    dismiss_notification(req.username, req.index)
    return {"ok": True}


@router.post("/dismiss-batch")
def dismiss_batch(req: BatchDismissReq):
    dismiss_by_ids(req.username, req.ids)
    return {"ok": True}


@router.post("/mark-read-batch")
def mark_read_batch(req: MarkReadReq):
    mark_read_by_ids(req.username, req.ids)
    return {"ok": True}


# ── Activity Logging ──
@router.post("/log")
def write_log(entry: LogEntry):
    jsonl_append(ACTIVITY_LOG, entry.dict())
    return {"ok": True}


@router.get("/logs")
def get_logs(limit: int = 200, username: str = ""):
    f = (lambda e: e.get("username") == username) if username else None
    return {"logs": jsonl_read(ACTIVITY_LOG, limit, f)}


# ── Download History ──
@router.get("/download-history")
def download_history(limit: int = Query(200)):
    return {"logs": jsonl_read(DL_LOG, limit)}


# ── Global Settings (v8.1.5) ──
@router.get("/settings")
def get_settings():
    """Readable by anyone — UI (Dashboard) needs to read refresh interval.

    v8.3.0: also returns a `data_roots` block with effective paths and the
    source classification (env | settings | default) for each root. The
    effective paths come from core.roots resolver if available (Agent A); if
    the resolver is missing we fall back to env vars + PATHS defaults.
    """
    data = load_json(SETTINGS_FILE, {})
    merged = {**DEFAULT_SETTINGS, **(data if isinstance(data, dict) else {})}
    # Effective data_roots (soft-landing). Never raise — settings GET must
    # stay readable even if roots.py is broken.
    try:
        eff = _resolver_snapshot()
        merged["data_roots"] = {
            "db_root":        eff.get("db_root", ""),
            "base_root":      eff.get("base_root", ""),
            "wafer_map_root": eff.get("wafer_map_root", ""),
            "sources": {
                "db_root":        _data_root_source("db_root"),
                "base_root":      _data_root_source("base_root"),
                "wafer_map_root": _data_root_source("wafer_map_root"),
            },
        }
    except Exception as e:
        merged["data_roots"] = {
            "db_root": "", "base_root": "", "wafer_map_root": "",
            "sources": {"db_root": "default", "base_root": "default", "wafer_map_root": "default"},
            "error": f"resolver unavailable: {e}",
        }
    return merged


class DataRootsReq(BaseModel):
    db_root: Optional[str] = None
    base_root: Optional[str] = None
    wafer_map_root: Optional[str] = None


class SettingsSaveReq(BaseModel):
    dashboard_refresh_minutes: int = 10
    dashboard_bg_refresh_minutes: int = 10
    data_roots: Optional[DataRootsReq] = None


@router.post("/settings/save")
def save_settings(req: SettingsSaveReq):
    """Admin-only via UI gating; backend saves whatever is sent (schema-validated).

    Two stores:
    - settings.json       — refresh intervals etc (legacy schema)
    - admin_settings.json — data_roots.{db,base,wafer_map} (core/roots.py reads)
    """
    data = req.dict(exclude_none=False)
    dr_in = data.pop("data_roots", None)
    # Clamp to sane bounds: 1..240 minutes
    for k in ("dashboard_refresh_minutes", "dashboard_bg_refresh_minutes"):
        v = data.get(k, 10)
        try:
            v = int(v)
        except Exception:
            v = 10
        data[k] = max(1, min(240, v))
    save_json(SETTINGS_FILE, data)

    # data_roots → admin_settings.json (merge; empty string → remove override)
    if dr_in is not None:
        current = _load_admin_settings()
        dr = dict(current.get("data_roots") or {})
        for ui_key, short_key in _DR_KEY_MAP.items():
            if ui_key not in dr_in:
                continue
            val = dr_in.get(ui_key)
            if val is None or (isinstance(val, str) and not val.strip()):
                # Empty → clear override so resolver falls back to env/default
                dr.pop(short_key, None)
            else:
                dr[short_key] = str(val).strip()
        current["data_roots"] = dr
        _save_admin_settings(current)

    return {"ok": True, "settings": data, "data_roots": (_resolver_snapshot() if dr_in is not None else None)}
