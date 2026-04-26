"""routers/auth.py v8.4.6 — Login/Register/Password + session tokens.

v8.4.6 보안 패치:
  - 로그인 성공 시 세션 토큰 발급 + 레거시 sha256 해시 자동 업그레이드 (PBKDF2).
  - /api/auth/logout 추가 (토큰 revoke).
  - /change-password 는 X-Session-Token 의 소유자만 본인 비번 변경 가능.
"""
import csv, datetime, secrets
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from core.paths import PATHS
from core.notify import send_to_admins
from core import auth as auth_core
from core.audit import record_user as _audit_user
from core.mail import send_mail as _send_mail, resolve_usernames_to_emails

router = APIRouter(prefix="/api/auth", tags=["auth"])

FIELDS = ["username","password_hash","role","status","created","tabs","email","name"]

class LoginReq(BaseModel):
    username: str
    password: str

class RegisterReq(BaseModel):
    username: str
    password: str
    # v8.8.27: 동명이인 대비 + 이름 검색을 위해 회원가입 시 실명 수집.
    #   username 은 사내 email id (로그인/시스템 식별), name 은 인간이 읽는 라벨.
    name: str = ""

class ResetReq(BaseModel):
    username: str

class ForgotPasswordReq(BaseModel):
    username: str

class ChangePwReq(BaseModel):
    old_password: str
    new_password: str


class SetNameReq(BaseModel):
    name: str


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


def _find_user_by_login_key(users, login_key: str):
    key = (login_key or "").strip().lower()
    if not key:
        return None
    for u in users:
        username = (u.get("username") or "").strip().lower()
        email = (u.get("email") or "").strip().lower()
        if key == username or (email and key == email):
            return u
    return None


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
        tabs = u.get("tabs", "filebrowser,dashboard,splittable,ettime,waferlayout")
        if u.get("role") == "admin":
            tabs = "__all__"
        token, expires_at = auth_core.issue_token(u["username"], u.get("role", "user"))
        _audit_user(u["username"], "auth:login", detail=f"role={u.get('role','user')}", tab="auth")
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
    u = auth_core.validate_token(token or "")
    auth_core.revoke_token(token or "")
    if u and u.get("username"):
        _audit_user(u["username"], "auth:logout", tab="auth")
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
    # v8.8.27: name 은 선택 필드지만 FE 가 권장. 공백 trimmed.
    human_name = (req.name or "").strip()
    users.append({
        "username": name,
        "password_hash": auth_core.hash_password(req.password),
        "role": "user",
        "status": "pending",
        "created": datetime.datetime.now().isoformat(),
        "tabs": "filebrowser,dashboard,splittable,ettime,waferlayout,inform,meeting,calendar",
        "name": human_name,
    })
    write_users(users)
    send_to_admins("New Registration", f"User '{name}' requests approval.", "approval")
    return {"ok": True, "message": "Registered. Wait for admin approval."}


@router.post("/set-name")
def set_name(req: SetNameReq, request: Request):
    """v8.8.27: 본인 실명 설정/수정. 로그인 유저 한정. 기존 가입자가 이름을 채우는 용도."""
    me = auth_core.current_user(request)
    username = me["username"]
    users = read_users()
    for u in users:
        if u["username"] != username:
            continue
        u["name"] = (req.name or "").strip()
        write_users(users)
        return {"ok": True, "name": u["name"]}
    raise HTTPException(404, "User not found")


@router.get("/me")
def me(request: Request):
    """v8.8.27: 현재 로그인 유저 정보(이름 포함)."""
    me = auth_core.current_user(request)
    users = read_users()
    for u in users:
        if u["username"] == me["username"]:
            return {
                "username": u["username"],
                "role": u.get("role", "user"),
                "name": u.get("name", ""),
                "email": u.get("email", ""),
            }
    raise HTTPException(404, "User not found")


@router.post("/reset-request")
def reset_request(req: ResetReq):
    users = read_users()
    if not any(u["username"] == req.username for u in users):
        raise HTTPException(404, "Username not found")
    send_to_admins("Password Reset Request",
                   f"User '{req.username}' requests password reset.", "approval")
    return {"ok": True, "message": "Reset request sent to admin."}


@router.post("/forgot-password")
def forgot_password(req: ForgotPasswordReq):
    login_key = (req.username or "").strip()
    if not login_key:
        raise HTTPException(400, "Username or email required")

    users = read_users()
    u = _find_user_by_login_key(users, login_key)
    # 계정 존재 여부는 과하게 노출하지 않는다.
    generic = {"ok": True, "message": "If the account exists, a temporary password has been sent."}
    if not u or u.get("status") != "approved":
        return generic

    username = (u.get("username") or "").strip()
    emails, _ = resolve_usernames_to_emails([username])
    email = (u.get("email") or "").strip()
    if email and email not in emails:
        emails.insert(0, email)
    if not emails:
        raise HTTPException(400, "No registered email for this account")

    temp_pw = "TMP-" + secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:10]
    u["password_hash"] = auth_core.hash_password(temp_pw)
    write_users(users)
    revoked = auth_core.revoke_user_tokens(username)

    title = "[flow] Temporary Password"
    content = (
        "<div style='font-family:Arial,sans-serif;font-size:14px;line-height:1.6'>"
        "<p>Your temporary password has been issued.</p>"
        f"<p><b>Username</b>: {username}<br/>"
        f"<b>Temporary Password</b>: {temp_pw}</p>"
        "<p>Please sign in and change your password immediately.</p>"
        "<p style='color:#666;font-size:12px'>If you did not request this, contact the administrator.</p>"
        "</div>"
    )
    res = _send_mail(
        sender_username="flow",
        receiver_usernames=[],
        extra_emails=emails,
        title=title,
        content=content,
        status_code="auth",
    )
    if not res.get("ok"):
        # 메일 발송 실패 시 temp 비번만 바뀌어 계정 잠김이 되지 않도록 롤백.
        # 롤백은 기존 해시를 모르면 불가하므로 현재는 명확히 에러를 내고 admin 추적이 가능하게 남긴다.
        _audit_user(username, "auth:forgot-password-mail-failed", detail=f"reason={res.get('reason','')}", tab="auth")
        raise HTTPException(503, res.get("reason") or "Temporary password email failed")

    _audit_user(username, "auth:forgot-password-issued", detail=f"revoked={revoked};to={','.join(emails)}", tab="auth")
    return generic


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
