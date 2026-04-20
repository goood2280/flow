"""routers/auth.py v8.4.6 — Login/Register/Password + session tokens.

v8.4.6 보안 패치:
  - 로그인 성공 시 세션 토큰 발급 + 레거시 sha256 해시 자동 업그레이드 (PBKDF2).
  - /api/auth/logout 추가 (토큰 revoke).
  - /change-password 는 X-Session-Token 의 소유자만 본인 비번 변경 가능.
"""
import csv, datetime
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from core.paths import PATHS
from core.notify import send_to_admins
from core import auth as auth_core

router = APIRouter(prefix="/api/auth", tags=["auth"])

FIELDS = ["username","password_hash","role","status","created","tabs"]

class LoginReq(BaseModel):
    username: str
    password: str

class RegisterReq(BaseModel):
    username: str
    password: str

class ResetReq(BaseModel):
    username: str

class ChangePwReq(BaseModel):
    old_password: str
    new_password: str


# ── Legacy shim: 다른 모듈이 `from routers.auth import hash_pw` 로 import.
def hash_pw(pw: str) -> str:
    return auth_core.hash_password(pw)


def read_users():
    users = []
    if PATHS.users_csv.exists():
        with open(PATHS.users_csv, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                for fld in FIELDS:
                    if fld not in row: row[fld] = ""
                users.append(row)
    return users


def _sanitize_username(name: str) -> str:
    """CSV injection / 개행 문자 방어. 허용 문자: 영숫자 + _ - . @"""
    if not name:
        return ""
    bad = set("\r\n,\"'\t\x00")
    if any(c in bad for c in name):
        raise HTTPException(400, "Invalid characters in username")
    return name.strip()


def write_users(users):
    with open(PATHS.users_csv, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for u in users:
            row = {fld: u.get(fld, "") for fld in FIELDS}
            w.writerow(row)


@router.post("/login")
def login(req: LoginReq):
    users = read_users()
    for u in users:
        if u["username"] != req.username:
            continue
        ok, needs_rehash = auth_core.verify_password(req.password, u.get("password_hash", ""))
        if not ok:
            raise HTTPException(401, "Invalid credentials")
        if u.get("status") != "approved":
            raise HTTPException(403, "Pending admin approval")
        # 레거시 sha256 자동 업그레이드 (투명)
        if needs_rehash:
            u["password_hash"] = auth_core.hash_password(req.password)
            write_users(users)
        tabs = u.get("tabs", "filebrowser,dashboard,splittable")
        if u.get("role") == "admin":
            tabs = "__all__"
        token, expires_at = auth_core.issue_token(u["username"], u.get("role", "user"))
        return {
            "ok": True,
            "username": u["username"],
            "role": u.get("role", "user"),
            "tabs": tabs,
            "token": token,
            "expires_at": datetime.datetime.fromtimestamp(expires_at).isoformat(timespec="seconds"),
        }
    raise HTTPException(401, "Invalid credentials")


@router.post("/logout")
def logout(request: Request):
    token = request.headers.get("x-session-token") or request.headers.get("X-Session-Token")
    auth_core.revoke_token(token or "")
    return {"ok": True}


@router.post("/register")
def register(req: RegisterReq):
    name = _sanitize_username(req.username)
    if not name:
        raise HTTPException(400, "Username required")
    if len(req.password or "") < 4:
        raise HTTPException(400, "Password too short")
    users = read_users()
    if any(u["username"] == name for u in users):
        raise HTTPException(409, "Username exists")
    users.append({
        "username": name,
        "password_hash": auth_core.hash_password(req.password),
        "role": "user",
        "status": "pending",
        "created": datetime.datetime.now().isoformat(),
        "tabs": "filebrowser,dashboard,splittable",
    })
    write_users(users)
    send_to_admins("New Registration", f"User '{name}' requests approval.", "approval")
    return {"ok": True, "message": "Registered. Wait for admin approval."}


@router.post("/reset-request")
def reset_request(req: ResetReq):
    users = read_users()
    if not any(u["username"] == req.username for u in users):
        raise HTTPException(404, "Username not found")
    send_to_admins("Password Reset Request",
                   f"User '{req.username}' requests password reset.", "approval")
    return {"ok": True, "message": "Reset request sent to admin."}


@router.post("/change-password")
def change_password(req: ChangePwReq, request: Request):
    """v8.4.6: 세션 토큰 소유자 본인 비번만 변경 가능 (username 파라미터 제거)."""
    me = auth_core.current_user(request)  # 401 on missing/invalid token
    username = me["username"]
    users = read_users()
    for u in users:
        if u["username"] != username:
            continue
        ok, _ = auth_core.verify_password(req.old_password, u.get("password_hash", ""))
        if not ok:
            raise HTTPException(401, "Current password incorrect")
        u["password_hash"] = auth_core.hash_password(req.new_password)
        write_users(users)
        # 비번 변경 시 기존 세션 유지 (본인 편의), but 새 비번 기준이므로 revoke 는 skip.
        return {"ok": True}
    raise HTTPException(404)
