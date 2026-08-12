"""routers/auth.py — Login/Register/Password 의 HTTP 표면 + users.csv 저장소.

레이어 경계:
  - 자격증명 검증(누구인가)  → `core/auth_providers.py` 의 provider
  - 세션 토큰 발급/검증       → `core/auth.py`
  - 이 파일                   → HTTP 라우트 + users.csv 읽기/쓰기

SSO(OIDC/SAML)를 붙일 때는 `core/auth_providers.py` 에 provider 를 등록하고
`/api/auth/sso/<name>/{start,callback}` 라우터를 새로 만든다. callback 이
`auth_providers.start_session(identity)` 를 부르면 비밀번호 로그인과 **완전히 같은**
세션이 나온다. 이 파일의 login/logout/me 는 손대지 않아도 된다.

v8.4.6 보안 패치:
  - 로그인 성공 시 세션 토큰 발급 + 레거시 sha256 해시 자동 업그레이드 (PBKDF2).
  - /api/auth/logout 추가 (토큰 revoke).
  - /change-password 는 X-Session-Token 의 소유자만 본인 비번 변경 가능.
"""
import csv, datetime, html, io, secrets, threading
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from core.paths import PATHS
from core.utils import atomic_write_text
from core.notify import send_to_admins
from core import auth as auth_core
from core import auth_providers
from core import mail as _mail
from core.audit import record_user as _audit_user
from core.mail import send_mail as _send_mail

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


# ── users.csv 파싱 메모이즈 ──────────────────────────────────────────
# read_users() 는 mail/informs/meetings/messages/admin 의 요청당 경로에서 30곳
# 넘게 불린다(한 요청 안에서 3번 부르는 핸들러도 있다). 매번 CSV 를 열고 순수
# 파이썬으로 전량 파싱하느라 GIL 을 쥐고 돌아, 계정과 동시 사용자가 늘수록 그대로
# 직렬화됐다. core/utils.py 의 load_json_cached 와 같은 mtime+size 키를 쓴다.
_USERS_CACHE_LOCK = threading.Lock()
_USERS_CACHE_SIG: tuple | None = None
_USERS_CACHE_ROWS: list[dict] | None = None


def _users_csv_sig() -> tuple:
    try:
        st = PATHS.users_csv.stat()
    except OSError:
        return (0.0, -1)
    return (st.st_mtime, st.st_size)


def _parse_users() -> list[dict]:
    users = []
    if PATHS.users_csv.exists():
        with open(PATHS.users_csv, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                for fld in FIELDS:
                    if fld not in row: row[fld] = ""
                users.append(row)
    return users


def invalidate_users_cache() -> None:
    """write_users 직후 호출. mtime 해상도가 1초인 파일시스템에서 같은 초 안의
    재저장을 sig 가 놓치는 경우까지 막는 이중 안전장치다."""
    global _USERS_CACHE_SIG, _USERS_CACHE_ROWS
    with _USERS_CACHE_LOCK:
        _USERS_CACHE_SIG = None
        _USERS_CACHE_ROWS = None


def read_users():
    """users.csv 를 파싱해 반환. 파싱 결과만 메모이즈한다.

    호출부 다수가 반환된 row 를 그 자리에서 수정하고 write_users 로 되쓴다
    (로그인 시 레거시 해시 업그레이드, set-name, admin 편집 등). 그래서 캐시본을
    그대로 넘기지 않고 **항상 얕은 복사본**을 준다 — row 값은 전부 문자열이라
    얕은 복사로 충분하고, 호출부 semantics 는 기존과 완전히 같다.
    """
    global _USERS_CACHE_SIG, _USERS_CACHE_ROWS
    sig = _users_csv_sig()
    with _USERS_CACHE_LOCK:
        if _USERS_CACHE_ROWS is not None and _USERS_CACHE_SIG == sig:
            return [dict(row) for row in _USERS_CACHE_ROWS]
    # 파싱은 락 밖에서 — 경합 시 중복 파싱은 나도 결과는 같고, 공유 드라이브
    # I/O 동안 다른 요청을 막지 않는 편이 낫다.
    rows = _parse_users()
    with _USERS_CACHE_LOCK:
        _USERS_CACHE_SIG = sig
        _USERS_CACHE_ROWS = rows
    return [dict(row) for row in rows]


def _sanitize_username(name: str) -> str:
    """Validate the account id before it reaches CSV or per-user paths."""
    try:
        return auth_core.validate_username(name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def find_user_rows(users, username: str) -> list[dict]:
    """같은 계정을 가리키는 행 전부. 정확도 순(완전 일치 > canonical 일치) 정렬.

    가입 때 `hong` 과 `hong@corp.com` 을 섞어 쓴 탓에 한 사람 앞으로 행이 둘
    이상 남아 있을 수 있다(정규화 도입 전 데이터). 그래서 하나만 고르지 않고
    후보를 다 준다 — 로그인은 비밀번호가 맞는 쪽을 고른다.

    대소문자는 구분한다. `Hong` 과 `hong` 은 서로 다른 사람의 아이디일 수 있으므로
    적힌 그대로 분류한다 (auth_core.canonical_username).
    """
    raw = (username or "").strip()
    domains = auth_core.login_domains()          # 설정은 한 번만 읽는다
    key = auth_core.canonical_username(raw, domains)
    if not key:
        return []
    exact, canon = [], []
    for u in users:
        stored = (u.get("username") or "").strip()
        if stored == raw:
            exact.append(u)
        elif auth_core.canonical_username(stored, domains) == key:
            canon.append(u)
    return exact + canon


def _find_user_by_username(users, username: str):
    rows = find_user_rows(users, username)
    return rows[0] if rows else None


def _forgot_password_mail_recipient(username: str) -> str:
    """Use the reset login id as the mail API recipient id, then apply the configured domain."""
    login_id = (username or "").strip()
    if not login_id or "@" in login_id:
        return login_id
    try:
        domain = (_mail.load_mail_cfg().get("domain") or "").strip().lstrip("@")
    except Exception:
        domain = ""
    return f"{login_id}@{domain}" if domain else login_id


def _auth_mail_sender(fallback: str = "flow") -> str:
    try:
        from_addr = (_mail.load_mail_cfg().get("from_addr") or "").strip()
    except Exception:
        from_addr = ""
    return from_addr or fallback


def write_users(users):
    """계정 파일 저장 — 원자적으로. 여기서 크래시가 나면 전 직원이 로그인 불가가
    되므로, 임시 파일에 다 쓴 뒤 교체한다 (core.utils.atomic_write_text)."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS, lineterminator="\r\n")
    w.writeheader()
    for u in users:
        w.writerow({fld: u.get(fld, "") for fld in FIELDS})
    atomic_write_text(PATHS.users_csv, buf.getvalue())
    invalidate_users_cache()


@router.get("/providers")
def providers():
    """사용 가능한 인증 수단 목록. 로그인 화면이 로그인 **전에** 호출한다.

    지금은 password 하나뿐이라 프런트가 굳이 부를 필요는 없지만, SSO provider 를
    등록하면 여기 자동으로 나타난다 — 로그인 페이지에 SSO 버튼을 붙일 때 백엔드를
    다시 손대지 않게 하려는 계약이다.
    """
    return {"providers": auth_providers.describe_providers()}


@router.post("/login")
def login(req: LoginReq):
    """ID/PW 로그인. 자격증명 검증은 password provider, 세션 발급은 start_session.

    SSO callback 도 같은 `start_session` 을 쓰므로 두 방식의 세션은 완전히 동일하다
    (수명·저장소·응답 스키마). 로그인 방식을 늘려도 이 엔드포인트는 그대로다.
    """
    return auth_providers.login_with("password", req)


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
    raw = _sanitize_username(req.username)
    if not raw:
        raise HTTPException(400, "Username required")
    # v9.5.x: 가입 화면에 `hong` 을 적는 사람과 `hong@corp.com` 을 적는 사람이 섞인다.
    #   저장은 사내 id 하나로 통일하고(로그인은 어느 쪽을 적어도 통과), 이미 다른
    #   형태로 가입한 사람이 또 가입하는 것은 중복으로 막는다. 대소문자는 적힌
    #   그대로 — `Hong` 과 `hong` 은 다른 아이디다.
    name = auth_core.canonical_username(raw)
    if not name:
        raise HTTPException(400, "Username required")
    if len(req.password or "") < 4:
        raise HTTPException(400, "Password too short")
    users = read_users()
    existing = _find_user_by_username(users, raw)
    if existing is not None:
        stored = (existing.get("username") or "").strip()
        if stored != raw:
            raise HTTPException(409, f"Username exists (registered as '{stored}')")
        raise HTTPException(409, "Username exists")
    # v8.8.27: name 은 선택 필드지만 FE 가 권장. 공백 trimmed.
    human_name = (req.name or "").strip()
    # 전체 주소로 적었으면 그 주소를 email 컬럼에 남긴다 — 메일 수신자 해석이
    # 도메인 합성(core.mail._apply_domain)에 기대지 않고 적힌 주소를 그대로 쓴다.
    typed_email = raw if _mail.looks_like_email(raw) else ""
    users.append({
        "username": name,
        "password_hash": auth_core.hash_password(req.password),
        "role": "user",
        "status": "pending",
        "created": datetime.datetime.now().isoformat(),
        # SSO 자동 프로비저닝(auth_providers.resolve_identity)과 같은 기본값을 쓴다.
        "tabs": auth_providers.DEFAULT_TABS,
        "email": typed_email,
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
    """현재 로그인 유저 정보. Bootstrap probe 이므로 무효 세션은 200/false 로 반환."""
    token = request.headers.get("x-session-token") or request.headers.get("X-Session-Token")
    me = auth_core.validate_token(token or "")
    if not me:
        return {"authenticated": False}
    users = read_users()
    for u in users:
        if u["username"] == me["username"]:
            return {
                "authenticated": True,
                "username": u["username"],
                "role": u.get("role", "user"),
                "name": u.get("name", ""),
                "email": u.get("email", ""),
                "tabs": "__all__" if u.get("role") == "admin" else u.get("tabs", ""),
            }
    return {"authenticated": False}


@router.post("/reset-request")
def reset_request(req: ResetReq):
    users = read_users()
    u = _find_user_by_username(users, req.username)
    if u is None:
        raise HTTPException(404, "Username not found")
    # 관리자에게는 users.csv 에 저장된 형태로 알린다 (요청자가 적은 형태가 아니라).
    target = (u.get("username") or req.username).strip()
    send_to_admins("Password Reset Request",
                   f"User '{target}' requests password reset.", "approval")
    return {"ok": True, "message": "Reset request sent to admin."}


@router.post("/forgot-password")
def forgot_password(req: ForgotPasswordReq):
    username_input = _sanitize_username(req.username)
    if not username_input:
        raise HTTPException(400, "Username required")

    users = read_users()
    u = _find_user_by_username(users, username_input)
    # 계정 존재 여부는 과하게 노출하지 않는다.
    generic = {"ok": True, "message": "If the account exists, a temporary password has been sent."}
    if not u or u.get("status") != "approved":
        return generic

    username = (u.get("username") or "").strip()
    old_hash = u.get("password_hash", "")
    temp_pw = "TMP-" + secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:10]
    u["password_hash"] = auth_core.hash_password(temp_pw)
    write_users(users)
    title = "[flow] Temporary Password"
    safe_username = html.escape(username)
    safe_temp_pw = html.escape(temp_pw)
    content = (
        "<div style='font-family:Arial,sans-serif;font-size:14px;line-height:1.6'>"
        "<p>Your temporary password has been issued.</p>"
        f"<p><b>Username</b>: {safe_username}<br/>"
        f"<b>Temporary Password</b>: {safe_temp_pw}</p>"
        "<p>Please sign in and change your password immediately.</p>"
        "<p style='color:#666;font-size:12px'>If you did not request this, contact the administrator.</p>"
        "</div>"
    )
    res = _send_mail(
        sender_username=_auth_mail_sender("flow"),
        receiver_usernames=[_forgot_password_mail_recipient(username)],
        title=title,
        content=content,
        files=[],
    )
    if not res.get("ok"):
        # 메일 발송 실패 시 temp 비번만 바뀌어 계정 잠김이 되지 않도록 롤백.
        for row in users:
            if (row.get("username") or "").strip() == username:
                row["password_hash"] = old_hash
                break
        write_users(users)
        _audit_user(username, "auth:forgot-password-mail-failed", detail=f"reason={res.get('reason','')}", tab="auth")
        raise HTTPException(503, res.get("reason") or "Temporary password email failed")

    revoked = auth_core.revoke_user_tokens(username)
    mail_to = ",".join(res.get("to") or [])
    _audit_user(username, "auth:forgot-password-issued", detail=f"revoked={revoked};to={mail_to}", tab="auth")
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
            raise HTTPException(400, "Current password incorrect")
        u["password_hash"] = auth_core.hash_password(req.new_password)
        write_users(users)
        # 비번 변경 시 기존 세션 유지 (본인 편의), but 새 비번 기준이므로 revoke 는 skip.
        return {"ok": True}
    raise HTTPException(404)
