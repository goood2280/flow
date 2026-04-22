"""core/mail.py — v8.8.17 사내 메일 API 공통 헬퍼.

목적:
  - 인폼로그 / 회의관리 등 여러 라우터에서 공통으로 쓸 수 있는 간단 인터페이스.
  - admin_settings.json 의 `mail` 섹션(api_url/headers/from_addr/extra_data/status_code)
    을 자동 참조. 설정이 비어 있거나 enabled=False 이면 ok=False 로 조용히 fail.

사내 메일 API 스펙 (기준):
  POST <api_url>  (multipart/form-data)
    data  = JSON string {
      "content":           <HTML body>,
      "receiverList":      [{"email": "...", "recipientType": "To", "seq": 1}, ...],
      "senderMailaddress": <from>,
      "statusCode":        <status>,
      "title":             <subject>,
      ... admin.extra_data 병합 ...
    }
    files = 0..N 개의 바이너리 파트 (각 파트 name="files").

사용법 (가장 흔한 1줄):

    from core.mail import send_mail
    res = send_mail(
        sender_username = "sender@example.com",
        receiver_usernames = ["user1@example.com", "admin", "test"],
        title   = "회의 알림",
        content = "<div>본문</div>",
    )
    # res = {"ok": True/False, "status": 200, "to": [...], "dry_run": bool,
    #        "reason": str (실패 시), "payload": dict (dry-run 시)}

규약:
  - username 이 곧 사내 email 이므로 '@' 를 포함하지 않는 username (admin/test 등)
    은 자동 제외되고 res["skipped"] 에 기록된다.
  - api_url == "dry-run" 이면 실제 호출 없이 payload 만 반환 (구성 검증용).
  - 첨부파일: files = [("filename.ext", b"...", "mime/type"), ...] 형식.
"""
from __future__ import annotations

import json as _json
import logging
import mimetypes
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from core.paths import PATHS

logger = logging.getLogger("holweb.mail")

CONTENT_MAX = 2 * 1024 * 1024          # 2 MB HTML
ATTACH_MAX  = 10 * 1024 * 1024         # 10 MB total
MAX_RECIPIENTS = 199                   # 사내 API 제약

File = Tuple[str, bytes, Optional[str]]   # (filename, content, mime)


def _admin_settings_path() -> Path:
    return PATHS.data_root / "admin_settings.json"


def load_mail_cfg() -> dict:
    """admin_settings.json 의 `mail` 섹션. 없으면 기본 비활성 dict.
    v8.8.19: `domain` 필드 추가 — username 이 '@' 를 포함하지 않을 때 자동으로
      `<username>@<domain>` 으로 조합해서 이메일 발송 대상/발신자에 사용.
    """
    p = _admin_settings_path()
    try:
        if p.is_file():
            data = _json.loads(p.read_text("utf-8"))
            if isinstance(data, dict):
                m = data.get("mail") or {}
                if isinstance(m, dict):
                    return {
                        "enabled": bool(m.get("enabled")),
                        "api_url": str(m.get("api_url") or "").strip(),
                        "from_addr": str(m.get("from_addr") or "").strip(),
                        "domain": str(m.get("domain") or "").strip().lstrip("@"),
                        "status_code": str(m.get("status_code") or "").strip(),
                        "headers": m.get("headers") if isinstance(m.get("headers"), dict) else {},
                        "extra_data": m.get("extra_data") if isinstance(m.get("extra_data"), dict) else {},
                    }
    except Exception as e:
        logger.warning(f"load_mail_cfg failed: {e}")
    return {"enabled": False, "api_url": "", "from_addr": "",
            "domain": "", "status_code": "", "headers": {}, "extra_data": {}}


def _apply_domain(s: str, domain: str) -> str:
    """v8.8.19: username 에 '@' 가 없으면 `<s>@<domain>` 으로 조합. domain 비어있거나
    이미 email 포맷이면 그대로 반환."""
    v = (s or "").strip()
    if not v:
        return v
    if "@" in v:
        return v
    d = (domain or "").strip().lstrip("@")
    if not d:
        return v
    return f"{v}@{d}"


def _looks_like_email(s: str) -> bool:
    """간단한 email 포맷 체크. '@' 포함 + '@' 이후에 '.' 존재."""
    if not s or "@" not in s:
        return False
    local, _, domain = s.partition("@")
    return bool(local) and "." in domain


def resolve_usernames_to_emails(usernames: Iterable[str]) -> Tuple[List[str], List[str]]:
    """usernames → (emails, skipped_usernames).
    우선순위: users.csv 의 email 필드 > username 자체(이메일 포맷) >
      v8.8.19: admin 메일 설정 `domain` 이 있으면 `<username>@<domain>` 자동 조합 > skip.
    """
    out: List[str] = []
    skipped: List[str] = []
    seen: set = set()
    try:
        from routers.auth import read_users
        all_users = {u.get("username", ""): u for u in read_users()}
    except Exception:
        all_users = {}
    # v8.8.19: 도메인 fallback
    try:
        _domain = load_mail_cfg().get("domain", "") or ""
    except Exception:
        _domain = ""
    for un in usernames:
        un = (un or "").strip()
        if not un:
            continue
        u = all_users.get(un) or {}
        em = (u.get("email") or "").strip()
        if _looks_like_email(em):
            key = em.lower()
            if key not in seen:
                seen.add(key); out.append(em)
            continue
        if _looks_like_email(un):
            key = un.lower()
            if key not in seen:
                seen.add(key); out.append(un)
            continue
        # v8.8.19: domain fallback — 설정된 도메인이 있으면 <un>@<domain> 으로 조합.
        if _domain:
            combined = _apply_domain(un, _domain)
            if _looks_like_email(combined):
                key = combined.lower()
                if key not in seen:
                    seen.add(key); out.append(combined)
                continue
        skipped.append(un)
    return out, skipped


def _encode_multipart(fields: dict, files: Sequence[File]) -> Tuple[bytes, str]:
    """multipart/form-data 인코딩. files = [(name, content, mime), ...]."""
    boundary = "----flowMail" + uuid.uuid4().hex
    chunks: List[bytes] = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n'.encode())
        chunks.append(b"Content-Type: text/plain; charset=utf-8\r\n\r\n")
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    for (filename, content, mime) in files:
        mime = mime or (mimetypes.guess_type(filename)[0] or "application/octet-stream")
        safe_fn = (filename or "file.bin").replace('"', '').replace("\r", "").replace("\n", "")
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(
            f'Content-Disposition: form-data; name="files"; filename="{safe_fn}"\r\n'.encode()
        )
        chunks.append(f"Content-Type: {mime}\r\n\r\n".encode())
        chunks.append(content)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def send_mail(
    sender_username: str,
    receiver_usernames: Iterable[str],
    title: str,
    content: str,
    *,
    files: Optional[Sequence[File]] = None,
    extra_emails: Optional[Iterable[str]] = None,
    status_code: str = "",
    cfg_override: Optional[dict] = None,
) -> dict:
    """사내 메일 API 를 통해 메일 1건 발송. 설정 실패/누락은 ok=False 로 조용히 fail.

    Args:
      sender_username:     발신자 username (이메일 포맷이면 그대로, 아니면 admin 설정 from_addr 사용).
      receiver_usernames:  수신자 username 리스트. admin/test 등 이메일 포맷 아닌 계정은 자동 제외.
      title:               메일 제목.
      content:             HTML 본문.
      files:               [(filename, bytes, mime)] — optional.
      extra_emails:        username 해석 우회하고 직접 받을 이메일 주소들.
      status_code:         사내 API statusCode 오버라이드 (빈 문자열이면 admin 설정값).
      cfg_override:        테스트용 — admin_settings.mail 대신 사용할 dict.

    Returns:
      {
        "ok":         bool,
        "status":     int (0 = 네트워크 실패),
        "to":         [email, ...],
        "skipped":    [username, ...],
        "dry_run":    bool,
        "reason":     str (실패 시 사유),
        "response":   str (API 응답 일부),
        "payload":    dict (dry_run 시 실제 전송되었을 data_obj),
      }
    """
    cfg = cfg_override if cfg_override is not None else load_mail_cfg()
    if not cfg.get("enabled") or not cfg.get("api_url"):
        return {"ok": False, "status": 0, "to": [], "skipped": [],
                "reason": "메일 API 가 설정되지 않았습니다 (Admin > 메일 API)."}

    emails, skipped = resolve_usernames_to_emails(list(receiver_usernames or []))
    for em in (extra_emails or []):
        em = (em or "").strip()
        if _looks_like_email(em) and em.lower() not in {e.lower() for e in emails}:
            emails.append(em)
    if not emails:
        return {"ok": False, "status": 0, "to": [], "skipped": skipped,
                "reason": "수신자 이메일이 없습니다 (이메일 포맷 username 필요)."}
    if len(emails) > MAX_RECIPIENTS:
        return {"ok": False, "status": 0, "to": emails[:MAX_RECIPIENTS], "skipped": skipped,
                "reason": f"수신자는 최대 {MAX_RECIPIENTS}명 (현재 {len(emails)}명)."}

    content_bytes = (content or "").encode("utf-8")
    if len(content_bytes) > CONTENT_MAX:
        return {"ok": False, "status": 0, "to": emails, "skipped": skipped,
                "reason": f"본문이 {CONTENT_MAX // (1024*1024)}MB 한도를 초과."}

    attach_list: List[File] = []
    if files:
        total = 0
        for f in files:
            total += len(f[1])
            if total > ATTACH_MAX:
                return {"ok": False, "status": 0, "to": emails, "skipped": skipped,
                        "reason": f"첨부 총 용량이 {ATTACH_MAX // (1024*1024)}MB 초과."}
            attach_list.append(f)

    receiver_list = [{"email": em, "recipientType": "To", "seq": i + 1}
                     for i, em in enumerate(emails)]

    # sender: username 이 이메일 포맷이면 그대로, 아니면 v8.8.19 의 `domain` 에 합성.
    #   마지막 폴백으로 admin.from_addr 사용.
    sender_addr = ""
    if _looks_like_email(sender_username or ""):
        sender_addr = sender_username
    else:
        combined = _apply_domain(sender_username or "", cfg.get("domain", ""))
        if _looks_like_email(combined):
            sender_addr = combined
        else:
            sender_addr = cfg.get("from_addr", "")

    # 사내 스펙: senderMailAddress (camelCase). 일부 구버전 서버는 senderMailaddress
    # 로 파싱하기도 해서 호환을 위해 양쪽 키 모두 주입.
    data_obj: dict = {
        "content":           content or "",
        "receiverList":      receiver_list,
        "senderMailAddress": sender_addr,
        "senderMailaddress": sender_addr,
        "statusCode":        (status_code or cfg.get("status_code", "")).strip(),
        "title":             (title or "").strip(),
    }
    # admin extra_data merge (예약 키는 덮어쓰지 않음).
    extra = cfg.get("extra_data") or {}
    if isinstance(extra, dict):
        for k, v in extra.items():
            if k and k not in data_obj:
                data_obj[k] = v

    url = (cfg.get("api_url") or "").strip()
    headers = dict(cfg.get("headers") or {})
    # v8.8.21: 사내 메일 API 규약 — data 필드 안에 `mailSendString` 키로 실제 payload 를
    #   한 번 더 JSON 문자열로 감싸야 한다. 이전까지 flat 하게 보내던 구조를 교체.
    mail_send_string = _json.dumps(data_obj, ensure_ascii=False)
    if url.lower() == "dry-run":
        return {"ok": True, "status": 200, "to": emails, "skipped": skipped,
                "dry_run": True, "payload": data_obj,
                "payload_wrapped": {"mailSendString": mail_send_string},
                "response": "",
                "attachments": [{"name": f[0], "bytes": len(f[1])} for f in attach_list]}

    fields = {"data": _json.dumps({"mailSendString": mail_send_string}, ensure_ascii=False)}
    body_bytes, content_type = _encode_multipart(fields, attach_list)
    hdrs_out = {str(k): str(v) for k, v in headers.items()}
    hdrs_out["Content-Type"] = content_type
    try:
        r = urllib.request.Request(url, data=body_bytes, headers=hdrs_out, method="POST")
        with urllib.request.urlopen(r, timeout=15) as resp:
            status = resp.status
            text = resp.read(2048).decode("utf-8", errors="replace")
        return {"ok": 200 <= status < 300, "status": status, "to": emails,
                "skipped": skipped, "dry_run": False, "response": text[:512]}
    except urllib.error.HTTPError as e:
        try:
            detail = e.read(512).decode("utf-8", errors="replace")
        except Exception:
            detail = ""
        return {"ok": False, "status": e.code, "to": emails, "skipped": skipped,
                "dry_run": False, "reason": f"HTTP {e.code}", "response": detail[:512]}
    except Exception as e:
        return {"ok": False, "status": 0, "to": emails, "skipped": skipped,
                "dry_run": False, "reason": f"{type(e).__name__}: {e}"}
