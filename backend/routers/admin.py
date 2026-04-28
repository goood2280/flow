"""routers/admin.py v8.4.6 - Admin: users/permissions/logs/notify/downloads + batch dismiss + global settings + data_roots.

v8.4.6 보안 패치:
  - 모든 admin 전용 엔드포인트에 Depends(require_admin) 추가 → curl 로 role 우회 불가.
  - /users 응답에서 password_hash 제거.
  - /reset-password 는 임시 랜덤 비번 발급 (응답엔 포함하고 호출자에게 전달 책임).
  - /my-notifications · /user-tabs · /log 은 본인 또는 admin 만 접근 (verify_owner).
  - /settings 의 data_roots 는 admin 요청에만 노출 (일반 유저는 숨김).

v8.7.3 hotfix:
  - MailCfgReq.extra_data 의 `Dict[str, Any]` 가 `Any` 미-import 로 import-time
    NameError 를 일으켜 admin 라우터 로딩이 실패하던 문제 수정. `Any` 를 typing
    import 에 추가.
"""
import os, secrets, subprocess, sys
import datetime as dt
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query, Depends, Request
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from core.paths import PATHS
from core.utils import jsonl_append, jsonl_read, load_json, save_json
from core.notify import (
    send_notify, get_notifications, mark_all_read, send_to_admins,
    dismiss_notification, dismiss_by_ids, mark_read_by_ids,
)
from routers.auth import read_users, write_users
from core.auth import require_admin, current_user, verify_owner
from core.audit import record as _audit
from core import s3_sync as _s3
from core import root_profile
from core.tracker_schema import migrate_tracker_issues_file

router = APIRouter(prefix="/api/admin", tags=["admin"])
FLOW_ROOT = Path(__file__).resolve().parents[2]
QA_REPORT_FILE = PATHS.data_root / "qa_report.json"
QA_SCRIPT = FLOW_ROOT / "scripts" / "e2e_qa.py"


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
ET_DL_LOG = PATHS.data_root / "logs" / "ettime_downloads.jsonl"
ACTIVITY_LOG = PATHS.activity_log
SETTINGS_FILE = PATHS.data_root / "settings.json"
# v8.3.0: data_roots runtime overrides live in a separate file that core/roots.py
# read-peeks. Kept distinct from settings.json so legacy UI/refresh settings and
# root-path overrides have independent schemas.
ADMIN_SETTINGS_FILE = PATHS.data_root / "admin_settings.json"
DEFAULT_SETTINGS = {
    "dashboard_refresh_minutes": 10,  # auto-refresh interval (frontend)
    "dashboard_bg_refresh_minutes": 10,  # backend scheduled recompute (if any)
    # Dashboard section visibility for non-admin users. Admin always sees all.
    "dashboard_sections": {"charts": True, "progress": False, "alerts": False},
}

# Map UI-facing long keys to admin_settings.json short keys used by core/roots.py
_DR_KEY_MAP = {
    "db_root":        "db",
}
_DR_ENV_MAP = {
    "db_root":        ("FLOW_DB_ROOT",),
    "data_root":      ("FLOW_DATA_ROOT",),
}


def _resolver_snapshot() -> Dict[str, str]:
    """Call core.roots.snapshot() if available; else env+default fallback."""
    try:
        from core import roots as _roots  # type: ignore
        snap = _roots.snapshot()
        return {
            "db_root":        snap.get("db_root", ""),
        }
    except Exception:
        # Fallback: env var > data_root default. Does NOT consult admin_settings.
        db = os.environ.get("FLOW_DB_ROOT") or str(PATHS.db_root)
        return {"db_root": db}


def _root_source(ui_key: str) -> str:
    """Classify where the effective value came from: env | settings | default."""
    for env_name in _DR_ENV_MAP.get(ui_key, ()):
        if os.environ.get(env_name):
            return "env"
    profile = root_profile.read_profile()
    if ui_key == "data_root":
        if profile.get("mode") in ("local", "shared", "custom") or profile.get("data_root"):
            return "profile"
        return "default"
    if ui_key == "db_root" and str(profile.get("db_root") or "").strip():
        return "profile"
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
# v8.8.14: per-page admin delegation + scheduled backup payload 스키마.
class PageAdminsReq(BaseModel):
    page_id: str
    usernames: List[str] = []
class BackupScheduleReq(BaseModel):
    at: str = ""            # ISO datetime — 비우면 취소
    reason: str = "pre-maintenance"
class BackupRestoreReq(BaseModel):
    filename: str
    restore_db_root_files: bool = False
class BulkUsersReq(BaseModel):
    text: str
    default_password: str = "1111"


@router.post("/tracker-schema-migrate")
def tracker_schema_migrate(request: Request, _admin=Depends(require_admin)):
    result = migrate_tracker_issues_file(reason="admin_button", actor=(current_user(request).get("username") or "admin"))
    _audit(request, "admin:tracker-schema-migrate", detail=f"changed={result.get('changed')} lots={result.get('lots_updated')}", tab="admin")
    return result


# ── Users ──
@router.get("/users")
def list_users(_admin=Depends(require_admin)):
    """v8.4.6: admin only. password_hash 는 응답에서 제거."""
    return {"users": [_scrub_user(u) for u in read_users()]}


@router.post("/approve")
def approve_user(req: ApproveReq, request: Request, _admin=Depends(require_admin)):
    users = read_users()
    for u in users:
        if u["username"] == req.username:
            u["status"] = "approved"
            if not u.get("tabs"):
                # v8.8.3: 신규 승인 시 inform/meeting/calendar 기본 포함.
                u["tabs"] = "filebrowser,dashboard,splittable,ettime,waferlayout,inform,meeting,calendar"
            write_users(users)
            send_notify(req.username, "Account Approved",
                        "Your account has been approved.", "info")
            _audit(request, "admin:approve", detail=f"user={req.username}", tab="admin")
            return {"ok": True}
    raise HTTPException(404)


@router.post("/reject")
def reject_user(req: ApproveReq, request: Request, _admin=Depends(require_admin)):
    users = [u for u in read_users() if u["username"] != req.username]
    write_users(users)
    _audit(request, "admin:reject", detail=f"user={req.username}", tab="admin")
    return {"ok": True}


@router.post("/reset-password")
def reset_password(req: ApproveReq, request: Request, _admin=Depends(require_admin)):
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
            _audit(request, "admin:reset-password", detail=f"user={req.username}", tab="admin")
            return {"ok": True, "new_password": new_pw}
    raise HTTPException(404)


class EmailReq(BaseModel):
    username: str
    email: str = ""


@router.post("/set-email")
def set_email(req: EmailReq, request: Request, _admin=Depends(require_admin)):
    """v8.7.2: admin sets/clears a user's email (used for 인폼 메일 수신자)."""
    email = (req.email or "").strip()
    if email and "@" not in email:
        raise HTTPException(400, "Invalid email format")
    users = read_users()
    for u in users:
        if u["username"] == req.username:
            u["email"] = email
            write_users(users)
            _audit(request, "admin:set-email", detail=f"user={req.username} email={email or '(clear)'}", tab="admin")
            return {"ok": True}
    raise HTTPException(404)


class NameReq(BaseModel):
    # v8.8.27: admin 이 특정 유저의 실명(name) 을 설정/수정.
    username: str
    name: str = ""


@router.post("/set-name")
def set_name(req: NameReq, request: Request, _admin=Depends(require_admin)):
    """v8.8.27: admin 이 유저의 실명을 설정/수정. 기존 가입자 일괄 채움용."""
    nm = (req.name or "").strip()
    users = read_users()
    for u in users:
        if u["username"] == req.username:
            u["name"] = nm
            write_users(users)
            _audit(request, "admin:set-name", detail=f"user={req.username} name={nm or '(clear)'}", tab="admin")
            return {"ok": True}
    raise HTTPException(404)


@router.post("/delete-user")
def delete_user(req: ApproveReq, request: Request, _admin=Depends(require_admin)):
    from core.auth import revoke_user_tokens
    users = [u for u in read_users() if u["username"] != req.username]
    write_users(users)
    revoke_user_tokens(req.username)
    _audit(request, "admin:delete-user", detail=f"user={req.username}", tab="admin")
    return {"ok": True}


@router.post("/bulk-users")
def bulk_create_users(req: BulkUsersReq, request: Request, _admin=Depends(require_admin)):
    from core.auth import hash_password
    raw = str(req.text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln for ln in raw.split("\n") if ln.strip()]
    if not lines:
        raise HTTPException(400, "No rows provided")
    default_pw = str(req.default_password or "1111")
    if len(default_pw) < 4:
        raise HTTPException(400, "default_password too short")

    def _split_row(line: str) -> list[str]:
        if "\t" in line:
            return line.split("\t")
        return line.split(",")

    rows = [_split_row(ln) for ln in lines]
    header = [str(x or "").strip().lower() for x in rows[0]]
    has_header = any(x in {"name", "username", "email", "role", "tabs"} for x in header)
    body = rows[1:] if has_header else rows
    users = read_users()
    existing = {str(u.get("username") or "").strip().lower() for u in users}
    created = []
    skipped = []
    default_tabs = "filebrowser,dashboard,splittable,ettime,waferlayout,inform,meeting,calendar"

    for idx, parts in enumerate(body, start=1):
      vals = [str(x or "").strip() for x in parts]
      if has_header:
          data = {header[i]: vals[i] if i < len(vals) else "" for i in range(len(header))}
          username = (data.get("username") or "").strip()
          name = (data.get("name") or "").strip()
          email = (data.get("email") or "").strip()
          role = (data.get("role") or "user").strip() or "user"
          tabs = (data.get("tabs") or default_tabs).strip() or default_tabs
      else:
          name = vals[0] if len(vals) >= 1 else ""
          username = vals[1] if len(vals) >= 2 else (vals[0] if len(vals) >= 1 else "")
          # Preferred no-header order is now: name, username, role.
          # Keep the old name, username, email, role, tabs form working if col 3
          # clearly contains an email address.
          third = vals[2] if len(vals) >= 3 else ""
          if "@" in third:
              email = third
              role = vals[3] if len(vals) >= 4 and vals[3] else "user"
              tabs = vals[4] if len(vals) >= 5 and vals[4] else default_tabs
          else:
              email = ""
              role = third or "user"
              tabs = vals[3] if len(vals) >= 4 and vals[3] and "," in vals[3] else default_tabs
      username = username.strip()
      if not username:
          skipped.append({"row": idx, "reason": "missing username"})
          continue
      key = username.lower()
      if key in existing:
          skipped.append({"row": idx, "username": username, "reason": "already exists"})
          continue
      if email and "@" not in email:
          email = ""
      role = role if role in {"user", "admin"} else "user"
      user_row = {
          "username": username,
          "password_hash": hash_password(default_pw),
          "role": role,
          "status": "approved",
          "created": dt.datetime.now().isoformat(),
          "tabs": tabs,
          "email": email,
          "name": name,
      }
      users.append(user_row)
      existing.add(key)
      created.append({"username": username, "name": name, "role": role})

    write_users(users)
    _audit(request, "admin:bulk-users", detail=f"created={len(created)} skipped={len(skipped)} pw={default_pw}", tab="admin")
    return {"ok": True, "created": created, "skipped": skipped, "default_password": default_pw}


# ── Permissions ──
@router.post("/set-tabs")
def set_tabs(req: PermReq, request: Request, _admin=Depends(require_admin)):
    users = read_users()
    for u in users:
        if u["username"] == req.username:
            u["tabs"] = ",".join(req.tabs)
            write_users(users)
            _audit(request, "admin:set-tabs", detail=f"user={req.username} tabs={u['tabs']}", tab="admin")
            return {"ok": True}
    raise HTTPException(404)


@router.get("/user-tabs")
def get_user_tabs(request: Request, username: str = Query(...)):
    """v8.4.6: 본인 또는 admin 만.
    v8.8.3: inform/meeting/calendar 하위호환 — 기존 유저의 tabs 에 누락됐으면 자동 추가.
    v9.0.x: ET 레포트/WF Layout 은 데이터 기본 화면이므로 기존 유저에게도 기본 노출."""
    # v8.8.3: 새로 추가된 탭 — 기존 유저는 기본 허용.
    _NEW_DEFAULT_TABS = {"inform", "meeting", "calendar", "ettime", "waferlayout"}
    verify_owner(request, username)
    for u in read_users():
        if u["username"] == username:
            if u.get("role") == "admin":
                return {"tabs": "__all__"}
            raw = u.get("tabs", "")
            if not raw:
                tabs_list = ["filebrowser", "dashboard", "splittable",
                             "ettime", "waferlayout", "inform", "meeting", "calendar"]
            else:
                tabs_list = [t.strip() for t in raw.split(",") if t.strip()]
                # 기존 유저가 저장된 tabs 에 신규 탭을 갖고 있지 않으면 자동 추가 (하위호환).
                for nt in _NEW_DEFAULT_TABS:
                    if nt not in tabs_list:
                        tabs_list.append(nt)
            return {"tabs": ",".join(tabs_list)}
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


# v8.8.33: 유저별 notify 구독 룰.
@router.get("/notify-rules")
def get_notify_rules(request: Request, username: str = Query("")):
    from core.notify import list_rules, event_catalog
    me = current_user(request)
    target = (username or me.get("username") or "").strip()
    if target != me.get("username") and me.get("role") != "admin":
        raise HTTPException(403, "self or admin only")
    return {"rules": list_rules(target), "catalog": event_catalog()}


class NotifyRulesReq(BaseModel):
    username: str = ""
    rules: dict = {}


@router.post("/notify-rules")
def save_notify_rules(req: NotifyRulesReq, request: Request):
    from core.notify import save_rules, list_rules
    me = current_user(request)
    target = (req.username or me.get("username") or "").strip()
    if target != me.get("username") and me.get("role") != "admin":
        raise HTTPException(403, "self or admin only")
    save_rules(target, req.rules or {})
    return {"ok": True, "rules": list_rules(target)}


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
def get_logs(request: Request, limit: int = 200, username: str = "", action: str = "", tab: str = ""):
    """v8.4.6: 전체 로그 열람은 admin. 본인 로그는 누구나.
    v8.7.1: action/tab 키워드 부분일치 필터 추가 (admin activity log UI 용)."""
    me = current_user(request)
    if me.get("role") != "admin":
        username = me["username"]
    act = (action or "").strip().lower()
    tbf = (tab or "").strip().lower()

    def _filt(e):
        if username and e.get("username") != username:
            return False
        if act and act not in (e.get("action", "") or "").lower():
            return False
        if tbf and tbf not in (e.get("tab", "") or "").lower():
            return False
        return True

    return {"logs": jsonl_read(ACTIVITY_LOG, limit, _filt)}


@router.get("/logs/users")
def get_log_users(_admin=Depends(require_admin)):
    """Admin activity log 유저 드롭다운용: 활동 로그에 등장한 distinct username."""
    entries = jsonl_read(ACTIVITY_LOG, limit=5000)
    seen = {}
    for e in entries:
        u = e.get("username") or ""
        if not u:
            continue
        s = seen.setdefault(u, {"username": u, "count": 0, "last": ""})
        s["count"] += 1
        ts = e.get("timestamp", "")
        if ts > s["last"]:
            s["last"] = ts
    arr = sorted(seen.values(), key=lambda v: v["last"], reverse=True)
    return {"users": arr}


# ── Download History ──
@router.get("/download-history")
def download_history(limit: int = Query(200), _admin=Depends(require_admin)):
    return {"logs": jsonl_read(DL_LOG, limit)}


@router.get("/ettime/download-log")
def ettime_download_log(limit: int = Query(500), _admin=Depends(require_admin)):
    return {"logs": jsonl_read(ET_DL_LOG, limit)}


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
    raw_sections = (data or {}).get("dashboard_sections") if isinstance(data, dict) else {}
    if not isinstance(raw_sections, dict):
        raw_sections = {}
    merged["dashboard_sections"] = {
        **DEFAULT_SETTINGS["dashboard_sections"],
        **{k: bool(v) for k, v in raw_sections.items() if k in DEFAULT_SETTINGS["dashboard_sections"]},
    }
    adm = _load_admin_settings()
    devguide_users = adm.get("devguide_user") or []
    if not isinstance(devguide_users, list):
        devguide_users = []
    merged["devguide_allowed"] = me.get("role") == "admin" or me.get("username") in devguide_users
    # v8.7.0: backup 설정 admin 에게 노출.
    if me.get("role") == "admin":
        try:
            from core.backup import get_settings as _bk_get
            merged["backup"] = _bk_get()
        except Exception:
            merged["backup"] = None
        # v8.7.2: 메일 API 설정 admin 에게 노출
        try:
            merged["mail"] = adm.get("mail") or {
                "api_url": "", "headers": {}, "from_addr": "", "status_code": "",
                "extra_data": {}, "recipient_groups": {}, "enabled": False,
            }
        except Exception:
            merged["mail"] = None
        # v8.7.7: LLM 설정도 admin 에게만 노출 (unredacted — 편집을 위해).
        try:
            merged["llm"] = adm.get("llm") or {
                "enabled": False, "api_url": "", "model": "", "mode": "fast",
                "admin_token": "", "headers": {}, "format": "openai", "extra_body": {}, "timeout_s": 20,
            }
        except Exception:
            merged["llm"] = None
        merged["devguide_user"] = [str(u).strip() for u in devguide_users if str(u).strip()]
    # v8.4.6: data_roots (내부 파일시스템 경로) 는 admin 에게만 노출.
    if me.get("role") == "admin":
        try:
            eff = _resolver_snapshot()
            profile = root_profile.snapshot()
            merged["data_roots"] = {
                "data_root":     str(PATHS.data_root),
                "db_root":        eff.get("db_root", ""),
                "profile":        profile,
                "restart_note":   "mode/data_root changes apply after server restart; db_root applies to new requests.",
                "sources": {
                    "data_root":      _root_source("data_root"),
                    "db_root":        _root_source("db_root"),
                },
            }
        except Exception as e:
            merged["data_roots"] = {
                "data_root": "",
                "db_root": "",
                "profile": {},
                "sources": {"data_root": "default", "db_root": "default"},
                "error": f"resolver unavailable: {e}",
            }
    return merged


class DataRootsReq(BaseModel):
    mode: Optional[str] = None
    data_root: Optional[str] = None
    db_root: Optional[str] = None
    prod_app_roots: Optional[List[str]] = None


class BackupCfgReq(BaseModel):
    path: Optional[str] = None
    interval_hours: Optional[int] = None
    keep: Optional[int] = None
    enabled: Optional[bool] = None


class MailCfgReq(BaseModel):
    # v8.7.2: 사내 메일 API 연동 설정.
    api_url: Optional[str] = None
    headers: Optional[Dict[str, str]] = None      # {"Authorization":"...", ...}
    from_addr: Optional[str] = None               # → senderMailaddress
    status_code: Optional[str] = None             # → statusCode (default for sends)
    extra_data: Optional[Dict[str, Any]] = None   # merged into outgoing `data` block
    recipient_groups: Optional[Dict[str, List[str]]] = None  # {"group": ["email1", ...]}
    enabled: Optional[bool] = None
    dep_ticket: Optional[str] = None              # v8.8.17: headers["x-dep-ticket"] shortcut
    domain: Optional[str] = None                  # v8.8.19: company email domain (예: "company.co.kr") — username-only 값 뒤에 자동 합성


class LLMCfgReq(BaseModel):
    # v8.7.7: 사내 LLM API 선택적 어댑터 설정.  전부 optional — 저장된 값과 병합.
    enabled: Optional[bool] = None
    api_url: Optional[str] = None
    model: Optional[str] = None
    mode: Optional[str] = None
    admin_token: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    format: Optional[str] = None              # "openai" | "raw"
    extra_body: Optional[Dict[str, Any]] = None
    timeout_s: Optional[int] = None


class SettingsSaveReq(BaseModel):
    dashboard_refresh_minutes: int = 10
    dashboard_bg_refresh_minutes: int = 10
    dashboard_sections: Optional[Dict[str, bool]] = None
    data_roots: Optional[DataRootsReq] = None
    backup: Optional[BackupCfgReq] = None
    mail: Optional[MailCfgReq] = None
    llm: Optional[LLMCfgReq] = None
    devguide_user: Optional[List[str]] = None


@router.post("/settings/save")
def save_settings(req: SettingsSaveReq, request: Request, _admin=Depends(require_admin)):
    """Admin-only via UI gating; backend saves whatever is sent (schema-validated).

    Two stores:
    - settings.json       — refresh intervals etc (legacy schema)
    - admin_settings.json — data_roots.db (core/roots.py reads)
    """
    data = req.dict(exclude_none=True)
    dr_in = data.pop("data_roots", None)
    bk_in = data.pop("backup", None)
    mail_in = data.pop("mail", None)
    llm_in = data.pop("llm", None)
    devguide_in = data.pop("devguide_user", None)
    # Clamp to sane bounds: 1..240 minutes
    for k in ("dashboard_refresh_minutes", "dashboard_bg_refresh_minutes"):
        v = data.get(k, 10)
        try:
            v = int(v)
        except Exception:
            v = 10
        data[k] = max(1, min(240, v))
    if "dashboard_sections" in data:
        raw_sections = data.get("dashboard_sections") or {}
        data["dashboard_sections"] = {
            **DEFAULT_SETTINGS["dashboard_sections"],
            **{k: bool(v) for k, v in raw_sections.items() if k in DEFAULT_SETTINGS["dashboard_sections"]},
        }
    current_settings = load_json(SETTINGS_FILE, {})
    if not isinstance(current_settings, dict):
        current_settings = {}
    save_json(SETTINGS_FILE, {**current_settings, **data})

    # data_roots → admin_settings.json (merge; empty string → remove override)
    if dr_in is not None:
        current = _load_admin_settings()
        dr = dict(current.get("data_roots") or {})
        profile_update: Dict[str, Any] = {}
        mode = dr_in.get("mode")
        if mode is not None:
            mode = str(mode or "auto").strip().lower()
            if mode not in root_profile.VALID_MODES:
                raise HTTPException(400, f"mode must be one of {sorted(root_profile.VALID_MODES)}")
            profile_update["mode"] = mode
        data_root_val = dr_in.get("data_root")
        if data_root_val is not None:
            if isinstance(data_root_val, str) and data_root_val.strip():
                p = Path(data_root_val.strip()).expanduser()
                if not p.exists() or not p.is_dir():
                    raise HTTPException(400, "data_root must be an existing directory. Create it first, then save.")
                profile_update["data_root"] = str(p)
            else:
                profile_update["data_root"] = ""
        prod_roots = dr_in.get("prod_app_roots")
        if prod_roots is not None:
            clean_roots = []
            for raw in prod_roots or []:
                s = str(raw or "").strip()
                if s:
                    clean_roots.append(s)
            profile_update["prod_app_roots"] = clean_roots
        # v9.0.3: Base/Wafer-map roots are no longer separate Admin-managed roots.
        # Root-level rulebooks/ML_TABLE files are read from DB root; product WF
        # Layout is stored in product config, not an external map library.
        dr.pop("base", None)
        dr.pop("wafer_map", None)
        for ui_key, short_key in _DR_KEY_MAP.items():
            if ui_key not in dr_in:
                continue
            val = dr_in.get(ui_key)
            if val is None or (isinstance(val, str) and not val.strip()):
                # Empty → clear override so resolver falls back to env/default
                dr.pop(short_key, None)
            else:
                p = Path(str(val).strip()).expanduser()
                if not p.exists() or not p.is_dir():
                    raise HTTPException(
                        400,
                        f"{ui_key} must be an existing directory. "
                        "Create/select the DB root shown in File Browser instead of saving a hidden fallback."
                    )
                dr[short_key] = str(p)
                profile_update["db_root"] = str(p)
            if val is None or (isinstance(val, str) and not val.strip()):
                profile_update["db_root"] = ""
        current["data_roots"] = dr
        _save_admin_settings(current)
        if profile_update:
            root_profile.write_profile(profile_update)

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

    # v8.7.2: 메일 API 설정 저장 — admin_settings.json.mail
    # v8.8.17: `dep_ticket` 단일 필드 편의 — admin 이 헤더 dict 대신 티켓값 한 칸만 넣어도
    #   headers["x-dep-ticket"] 에 자동으로 반영. 기존 headers 맵도 여전히 지원 (merge).
    if mail_in is not None:
        current = _load_admin_settings()
        mail_cur = dict(current.get("mail") or {})
        # v8.8.19: `domain` 추가 — username-only 값 뒤에 @domain 자동 합성.
        for k in ("api_url", "from_addr", "status_code", "dep_ticket", "domain"):
            if mail_in.get(k) is not None:
                v = str(mail_in.get(k) or "").strip()
                if k == "domain":
                    v = v.lstrip("@")   # 허용: "company.co.kr" 또는 "@company.co.kr"
                mail_cur[k] = v
        # headers merge + dep_ticket 자동 반영.
        hdrs_out = dict(mail_cur.get("headers") or {})
        if mail_in.get("headers") is not None:
            hdrs = mail_in.get("headers") or {}
            hdrs_out = {str(k): str(v) for k, v in hdrs.items() if k}
        dt = str(mail_cur.get("dep_ticket") or "").strip()
        if dt:
            hdrs_out["x-dep-ticket"] = dt
        elif "x-dep-ticket" in hdrs_out and mail_in.get("dep_ticket") == "":
            hdrs_out.pop("x-dep-ticket", None)
        mail_cur["headers"] = hdrs_out
        if mail_in.get("extra_data") is not None:
            ed = mail_in.get("extra_data") or {}
            mail_cur["extra_data"] = ed if isinstance(ed, dict) else {}
        if mail_in.get("recipient_groups") is not None:
            rg = mail_in.get("recipient_groups") or {}
            clean: Dict[str, List[str]] = {}
            for gname, emails in rg.items():
                if not gname or not isinstance(emails, list):
                    continue
                clean[str(gname)] = [str(e).strip() for e in emails if str(e).strip() and "@" in str(e)]
            mail_cur["recipient_groups"] = clean
        if mail_in.get("enabled") is not None:
            mail_cur["enabled"] = bool(mail_in.get("enabled"))
        current["mail"] = mail_cur
        _save_admin_settings(current)

    # v8.7.7: 사내 LLM 어댑터 설정 저장 (옵션 기능).
    if llm_in is not None:
        current = _load_admin_settings()
        llm_cur = dict(current.get("llm") or {})
        for k in ("api_url", "model", "mode", "format", "admin_token"):
            if llm_in.get(k) is not None:
                llm_cur[k] = str(llm_in.get(k) or "").strip()
        if llm_in.get("headers") is not None:
            hdrs = llm_in.get("headers") or {}
            llm_cur["headers"] = {str(k): str(v) for k, v in hdrs.items() if k}
        if llm_in.get("extra_body") is not None:
            eb = llm_in.get("extra_body") or {}
            llm_cur["extra_body"] = eb if isinstance(eb, dict) else {}
        if llm_in.get("timeout_s") is not None:
            try:
                llm_cur["timeout_s"] = max(3, min(120, int(llm_in.get("timeout_s"))))
            except Exception:
                llm_cur["timeout_s"] = 20
        if llm_in.get("enabled") is not None:
            llm_cur["enabled"] = bool(llm_in.get("enabled"))
        current["llm"] = llm_cur
        _save_admin_settings(current)

    if devguide_in is not None:
        current = _load_admin_settings()
        approved = {u["username"] for u in read_users() if u.get("status") == "approved"}
        current["devguide_user"] = sorted({str(u).strip() for u in (devguide_in or []) if str(u).strip() in approved})
        _save_admin_settings(current)

    _audit(request, "admin:settings-save",
           detail=f"refresh={data.get('dashboard_refresh_minutes')} data_roots={'yes' if dr_in else 'no'} backup={'yes' if bk_in else 'no'} mail={'yes' if mail_in else 'no'} llm={'yes' if llm_in else 'no'} devguide={'yes' if devguide_in is not None else 'no'}",
           tab="admin")
    return {"ok": True, "settings": data, "data_roots": (_resolver_snapshot() if dr_in is not None else None)}


# ── Backup (v8.7.0) ────────────────────────────────────────────────
@router.get("/backup/status")
def backup_status(_admin=Depends(require_admin)):
    from core.backup import get_settings, list_backups
    return {"settings": get_settings(), "backups": list_backups()}


@router.post("/backup/run")
def backup_run(request: Request, _admin=Depends(require_admin)):
    from core.backup import run_backup
    info = run_backup(reason="manual")
    _audit(request, "admin:backup-run",
           detail=f"ok={info.get('ok')} size={info.get('bytes')} err={info.get('error','')[:80]}",
           tab="admin")
    return info


@router.post("/backup/restore")
def backup_restore(req: BackupRestoreReq, request: Request, _admin=Depends(require_admin)):
    from core.backup import restore_backup
    info = restore_backup(req.filename, restore_db_root_files=req.restore_db_root_files)
    _audit(request, "admin:backup-restore",
           detail=f"ok={info.get('ok')} file={req.filename} restored={info.get('restored')} err={info.get('error','')[:80]}",
           tab="admin")
    if not info.get("ok"):
        raise HTTPException(400, info.get("error") or "restore failed")
    return info


# ── v8.8.14: Scheduled one-off backup ──────────────────────────────────
# 서버 점검 예정 시 admin 이 "특정 시각에 백업 실행" 을 예약. 스케줄러가 1분 단위로
# admin_settings.backup.scheduled_at 를 폴링해서 시각이 지나면 실행하고 필드 비운다.
@router.post("/backup/schedule")
def backup_schedule(req: BackupScheduleReq, request: Request, _admin=Depends(require_admin)):
    """`at` 이 비어있으면 예약 취소. ISO datetime (예: 2026-04-22T23:30:00) 필요."""
    import datetime as _dt
    cfg = load_json(ADMIN_SETTINGS_FILE, {})
    bk = dict(cfg.get("backup") or {})
    at = (req.at or "").strip()
    if not at:
        bk.pop("scheduled_at", None); bk.pop("scheduled_reason", None)
        cfg["backup"] = bk
        save_json(ADMIN_SETTINGS_FILE, cfg)
        _audit(request, "admin:backup-schedule-cancel", tab="admin")
        return {"ok": True, "scheduled_at": None}
    # Parse ISO for validation (Python 3.11+; polyfill for offset)
    try:
        _ = _dt.datetime.fromisoformat(at.replace("Z", "+00:00"))
    except Exception:
        raise HTTPException(400, f"Invalid ISO datetime: {at!r}")
    bk["scheduled_at"] = at
    bk["scheduled_reason"] = (req.reason or "pre-maintenance").strip()[:40] or "pre-maintenance"
    cfg["backup"] = bk
    save_json(ADMIN_SETTINGS_FILE, cfg)
    _audit(request, "admin:backup-schedule", detail=f"at={at} reason={bk['scheduled_reason']}", tab="admin")
    return {"ok": True, "scheduled_at": at, "reason": bk["scheduled_reason"]}


# ── v8.8.14: Per-page admin delegation ─────────────────────────────────
@router.get("/page-admins")
def page_admins_get(_admin=Depends(require_admin)):
    """현재 admin_settings 의 page_admins 맵 전체. Admin UI 에서 편집용."""
    from core.auth import get_page_admins
    return {"page_admins": get_page_admins()}


@router.post("/page-admins")
def page_admins_set(req: PageAdminsReq, request: Request, _admin=Depends(require_admin)):
    """page_id → usernames 목록을 설정 (빈 리스트면 해당 페이지 위임 제거)."""
    page_id = (req.page_id or "").strip()
    if not page_id:
        raise HTTPException(400, "page_id required")
    valid_users = {u["username"] for u in read_users() if u.get("status") == "approved"}
    users = [u for u in (req.usernames or []) if u in valid_users]
    data = load_json(ADMIN_SETTINGS_FILE, {})
    pa = dict(data.get("page_admins") or {})
    if users:
        pa[page_id] = sorted(set(users))
    else:
        pa.pop(page_id, None)
    data["page_admins"] = pa
    save_json(ADMIN_SETTINGS_FILE, data)
    _audit(request, "admin:page-admins-set",
           detail=f"page={page_id} users={','.join(users) or '(clear)'}", tab="admin")
    return {"ok": True, "page_admins": pa}


@router.get("/my-page-admin")
def my_page_admin(request: Request):
    """현재 유저가 위임받은 page 목록. global admin 은 전체 True + is_global_admin=true 반환."""
    u = current_user(request)
    from core.auth import get_page_admins
    pa = get_page_admins()
    uname = u.get("username", "")
    pages = sorted([pid for pid, lst in pa.items() if uname in (lst or [])])
    return {
        "username": uname,
        "role": u.get("role", "user"),
        "is_global_admin": u.get("role") == "admin",
        "pages": pages,
    }


# ── v8.8.14: Activity dashboard — 누가 / 어떤 기능을 / 얼마나 썼는지 ──
@router.get("/activity/summary")
def activity_summary(days: int = Query(7), _admin=Depends(require_admin)):
    """최근 N 일 activity.jsonl 을 집계.
    반환:
      - total: 총 이벤트 수
      - by_user: { username: count } (top 20)
      - by_action: { action: count } (top 30)
      - by_tab:    { tab: count }
      - by_day:    { "YYYY-MM-DD": count }
      - recent:    최근 50건 (내림차순)
    """
    import datetime as _dt, collections
    try:
        days = max(1, min(90, int(days)))
    except Exception:
        days = 7
    rows = list(jsonl_read(ACTIVITY_LOG) or [])
    cutoff = _dt.datetime.now() - _dt.timedelta(days=days)
    by_user = collections.Counter()
    by_action = collections.Counter()
    by_tab = collections.Counter()
    by_day = collections.Counter()
    filtered: list = []
    for r in rows:
        ts = (r.get("timestamp") or r.get("time") or "").strip()
        try:
            dt = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
        except Exception:
            continue
        if dt < cutoff:
            continue
        filtered.append(r)
        u = (r.get("username") or r.get("actor") or "anonymous") or "anonymous"
        by_user[u] += 1
        a = (r.get("action") or "") or "(unknown)"
        by_action[a] += 1
        t = (r.get("tab") or "") or "(none)"
        by_tab[t] += 1
        by_day[dt.strftime("%Y-%m-%d")] += 1
    filtered.sort(key=lambda r: r.get("timestamp") or r.get("time") or "", reverse=True)
    return {
        "window_days": days,
        "total": len(filtered),
        "by_user": dict(by_user.most_common(20)),
        "by_action": dict(by_action.most_common(30)),
        "by_tab": dict(by_tab.most_common()),
        "by_day": dict(sorted(by_day.items())),
        "recent": filtered[:50],
    }


@router.get("/activity/features")
def activity_features(days: int = Query(30), _admin=Depends(require_admin)):
    """`action` prefix 단위로 기능 사용 현황. 각 기능(=action prefix)의 first_seen /
    last_seen / users(사용한 유저 집합) / count 를 반환. admin 이 "어떤 기능이 활성화
    되어 있는지" 한눈에 파악하는 용도.
    """
    import datetime as _dt, collections
    try:
        days = max(1, min(365, int(days)))
    except Exception:
        days = 30
    rows = list(jsonl_read(ACTIVITY_LOG) or [])
    cutoff = _dt.datetime.now() - _dt.timedelta(days=days)
    features: dict = {}
    for r in rows:
        ts = (r.get("timestamp") or r.get("time") or "").strip()
        try:
            dt = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
        except Exception:
            continue
        if dt < cutoff:
            continue
        a = (r.get("action") or "").strip()
        if not a:
            continue
        # prefix = "domain:verb" 같이 ':' 로 구분된 앞부분 (예: inform:create / splittable:plan)
        key = a.split(":", 1)[0] if ":" in a else a
        ent = features.setdefault(key, {
            "name": key, "count": 0, "users": set(),
            "first_seen": ts, "last_seen": ts,
            "sample_actions": collections.Counter(),
        })
        ent["count"] += 1
        ent["users"].add((r.get("username") or r.get("actor") or "anonymous") or "anonymous")
        if ts < ent["first_seen"]:
            ent["first_seen"] = ts
        if ts > ent["last_seen"]:
            ent["last_seen"] = ts
        ent["sample_actions"][a] += 1
    out = []
    for k, v in sorted(features.items(), key=lambda kv: -kv[1]["count"]):
        out.append({
            "feature": k,
            "count": v["count"],
            "user_count": len(v["users"]),
            "users": sorted(v["users"])[:20],
            "first_seen": v["first_seen"],
            "last_seen": v["last_seen"],
            "top_actions": dict(v["sample_actions"].most_common(5)),
        })
    return {"window_days": days, "features": out, "feature_count": len(out)}


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
    # v8.7.5: INLINE prefix 항목 매칭 — SplitTable 에서 item_desc 로 표시.
    "inline_matching": {
        "columns": ["process_id", "item_id", "item_desc", "step_id"],
        "unique_key": ["item_id", "process_id"],
    },
    # v8.7.5: VM_ prefix 항목 매칭 — SplitTable 에서 step_id 서브텍스트로 표시.
    "vm_matching": {
        "columns": ["step_desc", "step_id"],
        "unique_key": ["step_desc", "step_id"],
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
    sync_result = _s3.sync_saved_path(PATHS.data_root, PATHS.db_root, fp)
    return {"ok": True, "rows_saved": len(cleaned), "path": str(fp), "s3_sync": sync_result}


@router.get("/qa/report")
def qa_report(_admin=Depends(require_admin)):
    data = load_json(QA_REPORT_FILE, {"runs": []})
    runs = data.get("runs") if isinstance(data, dict) else []
    return {"ok": True, "report": data if isinstance(data, dict) else {"runs": []}, "latest": (runs[0] if isinstance(runs, list) and runs else None)}


@router.post("/qa/trigger")
def qa_trigger(_admin=Depends(require_admin)):
    if not QA_SCRIPT.exists():
        raise HTTPException(404, "e2e_qa.py not found")
    proc = subprocess.run(
        [sys.executable, str(QA_SCRIPT)],
        cwd=str(PATHS.data_root.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    payload = {
        "ok": proc.returncode == 0,
        "code": proc.returncode,
        "stdout": (proc.stdout or "").strip()[:4000],
        "stderr": (proc.stderr or "").strip()[:4000],
    }
    if proc.returncode != 0:
        raise HTTPException(500, payload)
    return payload
