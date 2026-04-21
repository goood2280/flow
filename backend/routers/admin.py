"""routers/admin.py v8.4.6 - Admin: users/permissions/logs/notify/downloads + batch dismiss + global settings + data_roots.

v8.4.6 보안 패치:
  - 모든 admin 전용 엔드포인트에 Depends(require_admin) 추가 → curl 로 role 우회 불가.
  - /users 응답에서 password_hash 제거.
  - /reset-password 는 임시 랜덤 비번 발급 (응답엔 포함하고 호출자에게 전달 책임).
  - /my-notifications · /user-tabs · /log 은 본인 또는 admin 만 접근 (verify_owner).
  - /settings 의 data_roots 는 admin 요청에만 노출 (일반 유저는 숨김).
"""
import os, secrets
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query, Depends, Request
from pydantic import BaseModel
from typing import List, Optional, Dict
from core.paths import PATHS
from core.utils import jsonl_append, jsonl_read, load_json, save_json
from core.notify import (
    send_notify, get_notifications, mark_all_read, send_to_admins,
    dismiss_notification, dismiss_by_ids, mark_read_by_ids,
)
from routers.auth import read_users, write_users
from core.auth import require_admin, current_user, verify_owner

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _is_admin(username: str) -> bool:
    """다른 라우터(filebrowser, splittable 등) 가 import 해서 씀. Back-compat."""
    if not username:
        return False
    try:
        for u in read_users():
            if u.get("username") == username and u.get("role") == "admin":
                return True
    except Exception:
        pass
    return False


def _scrub_user(u: dict) -> dict:
    """응답 직렬화 시 password_hash 제거."""
    return {k: v for k, v in u.items() if k != "password_hash"}
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
def list_users(_admin=Depends(require_admin)):
    """v8.4.6: admin only. password_hash 는 응답에서 제거."""
    return {"users": [_scrub_user(u) for u in read_users()]}


@router.post("/approve")
def approve_user(req: ApproveReq, _admin=Depends(require_admin)):
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
def reject_user(req: ApproveReq, _admin=Depends(require_admin)):
    users = [u for u in read_users() if u["username"] != req.username]
    write_users(users)
    return {"ok": True}


@router.post("/reset-password")
def reset_password(req: ApproveReq, _admin=Depends(require_admin)):
    """v8.4.6: 임시 랜덤 비번 (12자) 발급. 기존 '1111' 하드코딩 제거.
    응답에 평문 포함 — admin 이 해당 유저에게 별도 채널로 전달 책임."""
    from core.auth import hash_password, revoke_user_tokens
    users = read_users()
    for u in users:
        if u["username"] == req.username:
            new_pw = secrets.token_urlsafe(9)  # ≈12 chars
            u["password_hash"] = hash_password(new_pw)
            write_users(users)
            revoke_user_tokens(req.username)  # 기존 세션 강제 로그아웃
            send_notify(req.username, "Password Reset",
                        "Your password has been reset by admin. "
                        "Contact admin to receive the new temporary password.", "info")
            return {"ok": True, "new_password": new_pw}
    raise HTTPException(404)


@router.post("/delete-user")
def delete_user(req: ApproveReq, _admin=Depends(require_admin)):
    from core.auth import revoke_user_tokens
    users = [u for u in read_users() if u["username"] != req.username]
    write_users(users)
    revoke_user_tokens(req.username)
    return {"ok": True}


# ── Permissions ──
@router.post("/set-tabs")
def set_tabs(req: PermReq, _admin=Depends(require_admin)):
    users = read_users()
    for u in users:
        if u["username"] == req.username:
            u["tabs"] = ",".join(req.tabs)
            write_users(users)
            return {"ok": True}
    raise HTTPException(404)


@router.get("/user-tabs")
def get_user_tabs(request: Request, username: str = Query(...)):
    """v8.4.6: 본인 또는 admin 만."""
    verify_owner(request, username)
    for u in read_users():
        if u["username"] == username:
            if u.get("role") == "admin":
                return {"tabs": "__all__"}
            return {"tabs": u.get("tabs", "filebrowser,dashboard,splittable")}
    raise HTTPException(404)


# ── Messaging ──
@router.post("/send-message")
def send_message(req: MessageReq, _admin=Depends(require_admin)):
    send_notify(req.to_user, "Message from Admin", req.message, "message")
    return {"ok": True}


class InquiryReq(BaseModel):
    username: str
    message: str


@router.post("/send-inquiry")
def send_inquiry(req: InquiryReq, request: Request):
    """User sends inquiry to all admins. 본인 이름으로만 보낼 수 있음."""
    verify_owner(request, req.username)
    send_to_admins(
        f"Inquiry from {req.username}",
        req.message,
        "message",
    )
    # Also notify the user that their inquiry was sent
    send_notify(req.username, "Inquiry Sent", "Your message has been sent to admin.", "info")
    return {"ok": True}


@router.post("/broadcast")
def broadcast(req: MessageReq, _admin=Depends(require_admin)):
    for u in read_users():
        if u["status"] == "approved":
            send_notify(u["username"], "Broadcast", req.message, "message")
    return {"ok": True}


# ── Notifications ──
@router.get("/my-notifications")
def my_notifications(request: Request, username: str = Query(...)):
    verify_owner(request, username)
    notifs = get_notifications(username, unread_only=True)
    return {"notifications": notifs, "count": len(notifs)}


@router.get("/all-notifications")
def all_notifications(request: Request, username: str = Query(...)):
    verify_owner(request, username)
    return {"notifications": get_notifications(username)}


@router.post("/mark-read")
def mark_read(req: ApproveReq, request: Request):
    verify_owner(request, req.username)
    mark_all_read(req.username)
    return {"ok": True}


@router.post("/dismiss")
def dismiss(req: DismissReq, request: Request):
    verify_owner(request, req.username)
    dismiss_notification(req.username, req.index)
    return {"ok": True}


@router.post("/dismiss-batch")
def dismiss_batch(req: BatchDismissReq, request: Request):
    verify_owner(request, req.username)
    dismiss_by_ids(req.username, req.ids)
    return {"ok": True}


@router.post("/mark-read-batch")
def mark_read_batch(req: MarkReadReq, request: Request):
    verify_owner(request, req.username)
    mark_read_by_ids(req.username, req.ids)
    return {"ok": True}


# ── Activity Logging ──
@router.post("/log")
def write_log(entry: LogEntry, request: Request):
    """v8.4.6: entry.username 은 세션 소유자로 강제 (spoof 방지)."""
    me = current_user(request)
    data = entry.dict()
    data["username"] = me["username"]
    jsonl_append(ACTIVITY_LOG, data)
    return {"ok": True}


@router.get("/logs")
def get_logs(request: Request, limit: int = 200, username: str = ""):
    """v8.4.6: 전체 로그 열람은 admin. 본인 로그는 누구나."""
    me = current_user(request)
    if me.get("role") != "admin":
        username = me["username"]
    f = (lambda e: e.get("username") == username) if username else None
    return {"logs": jsonl_read(ACTIVITY_LOG, limit, f)}


# ── Download History ──
@router.get("/download-history")
def download_history(limit: int = Query(200), _admin=Depends(require_admin)):
    return {"logs": jsonl_read(DL_LOG, limit)}


# ── Global Settings (v8.1.5) ──
@router.get("/settings")
def get_settings(request: Request):
    """Readable by anyone — UI (Dashboard) needs to read refresh interval.

    v8.3.0: also returns a `data_roots` block with effective paths and the
    source classification (env | settings | default) for each root. The
    effective paths come from core.roots resolver if available (Agent A); if
    the resolver is missing we fall back to env vars + PATHS defaults.
    """
    me = current_user(request)
    data = load_json(SETTINGS_FILE, {})
    merged = {**DEFAULT_SETTINGS, **(data if isinstance(data, dict) else {})}
    # v8.7.0: backup 설정 admin 에게 노출.
    if me.get("role") == "admin":
        try:
            from core.backup import get_settings as _bk_get
            merged["backup"] = _bk_get()
        except Exception:
            merged["backup"] = None
    # v8.4.6: data_roots (내부 파일시스템 경로) 는 admin 에게만 노출.
    if me.get("role") == "admin":
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


class BackupCfgReq(BaseModel):
    path: Optional[str] = None
    interval_hours: Optional[int] = None
    keep: Optional[int] = None
    enabled: Optional[bool] = None


class SettingsSaveReq(BaseModel):
    dashboard_refresh_minutes: int = 10
    dashboard_bg_refresh_minutes: int = 10
    data_roots: Optional[DataRootsReq] = None
    backup: Optional[BackupCfgReq] = None


@router.post("/settings/save")
def save_settings(req: SettingsSaveReq, _admin=Depends(require_admin)):
    """Admin-only via UI gating; backend saves whatever is sent (schema-validated).

    Two stores:
    - settings.json       — refresh intervals etc (legacy schema)
    - admin_settings.json — data_roots.{db,base,wafer_map} (core/roots.py reads)
    """
    data = req.dict(exclude_none=False)
    dr_in = data.pop("data_roots", None)
    bk_in = data.pop("backup", None)
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

    # v8.7.0: backup 설정 저장.
    if bk_in is not None:
        try:
            from core.backup import set_settings as _bk_set
            _bk_set(
                path=bk_in.get("path"),
                interval_hours=bk_in.get("interval_hours"),
                keep=bk_in.get("keep"),
                enabled=bk_in.get("enabled"),
            )
        except Exception:
            pass

    return {"ok": True, "settings": data, "data_roots": (_resolver_snapshot() if dr_in is not None else None)}


# ── Backup (v8.7.0) ────────────────────────────────────────────────
@router.get("/backup/status")
def backup_status(_admin=Depends(require_admin)):
    from core.backup import get_settings, list_backups
    return {"settings": get_settings(), "backups": list_backups()}


@router.post("/backup/run")
def backup_run(_admin=Depends(require_admin)):
    from core.backup import run_backup
    info = run_backup(reason="manual")
    jsonl_append(ACTIVITY_LOG, {
        "actor": "admin", "action": "backup_run",
        "ok": info.get("ok"), "path": info.get("path"),
        "size": info.get("bytes"), "error": info.get("error"),
        "time": info.get("at"),
    })
    return info


# ── Base CSV editor (v8.5.2) ──
# Admin only. step_matching.csv / knob_ppid.csv 를 직접 표로 편집.
import csv as _csv
BASE_CSV_SCHEMAS = {
    "step_matching": {
        "columns": ["step_id", "func_step"],
        "unique_key": ["step_id"],
    },
    "knob_ppid": {
        "columns": ["feature_name", "function_step", "rule_order", "ppid", "operator", "category", "use"],
        "unique_key": ["feature_name", "function_step", "rule_order"],
    },
}


def _base_csv_path(name: str) -> Path:
    from core.paths import PATHS
    # v8.4.6 이슈: path traversal 방어 — name 은 whitelist 화.
    if name not in BASE_CSV_SCHEMAS:
        raise HTTPException(400, f"Unknown csv: {name}")
    base = Path(str(PATHS.base_root)).resolve()
    fp = (base / f"{name}.csv").resolve()
    try:
        fp.relative_to(base)
    except ValueError:
        raise HTTPException(400, "Invalid path")
    return fp


@router.get("/base-csv")
def base_csv_get(name: str = Query(...), _admin=Depends(require_admin)):
    fp = _base_csv_path(name)
    schema = BASE_CSV_SCHEMAS[name]
    rows: List[List[str]] = []
    if fp.exists():
        with open(fp, "r", encoding="utf-8-sig", newline="") as f:
            reader = _csv.reader(f)
            header = next(reader, None)
            for r in reader:
                # pad/trim to match schema length
                if len(r) < len(schema["columns"]):
                    r = r + [""] * (len(schema["columns"]) - len(r))
                rows.append(r[: len(schema["columns"])])
    return {
        "name": name,
        "columns": schema["columns"],
        "unique_key": schema["unique_key"],
        "rows": rows,
    }


class BaseCsvSaveReq(BaseModel):
    name: str
    rows: List[List[str]] = []


@router.put("/base-csv")
def base_csv_save(req: BaseCsvSaveReq, _admin=Depends(require_admin)):
    if req.name not in BASE_CSV_SCHEMAS:
        raise HTTPException(400, f"Unknown csv: {req.name}")
    schema = BASE_CSV_SCHEMAS[req.name]
    cols = schema["columns"]
    fp = _base_csv_path(req.name)

    # validation: drop empty rows + check unique key
    cleaned: List[List[str]] = []
    seen_keys = set()
    for raw in req.rows:
        r = [(x if x is not None else "").strip() for x in raw]
        if len(r) < len(cols):
            r = r + [""] * (len(cols) - len(r))
        r = r[: len(cols)]
        if all(not v for v in r):
            continue  # skip fully-empty
        # unique key
        key_idx = [cols.index(k) for k in schema["unique_key"]]
        key = tuple(r[i] for i in key_idx)
        if any(not k for k in key):
            raise HTTPException(400, f"unique key empty: {schema['unique_key']}")
        if key in seen_keys:
            raise HTTPException(400, f"duplicate unique key: {key}")
        seen_keys.add(key)
        # `use` 필드 검증 (knob_ppid)
        if req.name == "knob_ppid":
            u = r[cols.index("use")].upper()
            if u not in ("", "Y", "N", "0", "1"):
                raise HTTPException(400, f"invalid use value: {u}")
            r[cols.index("use")] = u or "Y"
        cleaned.append(r)

    # atomic write (UTF-8 w/ BOM for Excel compat)
    tmp = fp.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        writer = _csv.writer(f)
        writer.writerow(cols)
        writer.writerows(cleaned)
    tmp.replace(fp)

    # audit
    from core.auth import current_user
    from fastapi import Request as _Req  # noqa
    jsonl_append(ACTIVITY_LOG, {
        "username": "admin",
        "action": f"base-csv:save:{req.name}",
        "tab": "admin",
        "detail": f"rows={len(cleaned)}",
        "timestamp": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
    })
    return {"ok": True, "rows_saved": len(cleaned), "path": str(fp)}
