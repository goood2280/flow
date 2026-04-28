"""routers/meetings.py v8.7.4 — 회의관리 (Meeting + Recurrence + Sessions).

변경점 (v8.7.4):
  - 회의(Meeting) 아래 **차수(Session)** 개념 도입. 각 차수가 독립적 scheduled_at /
    status / agendas / minutes 를 갖는다. 기존 v8.7.2 스키마(agendas/minutes 가
    meeting 레벨) 는 자동 마이그레이션 ─ 1 개의 session 으로 래핑.
  - 반복(recurrence) 메타 추가: {type: "none"|"weekly", count_per_week,
    weekday: [0..6], note}. FE 가 다음 차수 일정을 제안할 때 참고.
  - 시드 "hol" 기본 소유자 제거.  owner 는 명시 + 없으면 생성자 username.

스키마 ({data_root}/meetings/meetings.json):
  [{
    id, title, owner,
    recurrence: { type, count_per_week, weekday:[int], note },
    status: "active"|"archived"|"cancelled",
    sessions: [{
      id, idx, scheduled_at,
      status: "scheduled"|"in_progress"|"completed"|"cancelled",
      agendas: [{ id, title, description, owner, link, created_at, updated_at }],
      minutes: { body, decisions, action_items, author, updated_at } | null,
      created_at, updated_at,
    }],
    created_by, created_at, updated_at,
  }]

권한:
  - 회의 생성: 로그인 유저 누구나. 생성자 = 주관자 기본값.
  - 회의 메타/반복 수정·삭제: 주관자 또는 admin.
  - 차수 추가/수정/삭제: 주관자 또는 admin.
  - 아젠다 추가: 로그인 유저 누구나 (담당자 = 본인).
  - 아젠다 수정/삭제: 아젠다 담당자 / 회의 주관자 / admin.
  - 회의록 저장: 회의 주관자 또는 admin.

Endpoints:
  GET  /api/meetings/list?status=&owner=
  GET  /api/meetings/{mid}
  POST /api/meetings/create
  POST /api/meetings/update
  POST /api/meetings/delete?id=
  POST /api/meetings/session/add                 body: {meeting_id, scheduled_at?}
  POST /api/meetings/session/update              body: {meeting_id, session_id, scheduled_at?, status?}
  POST /api/meetings/session/delete?meeting_id=&session_id=
  POST /api/meetings/agenda/add                  body: {meeting_id, session_id, title, ...}
  POST /api/meetings/agenda/update               body: {meeting_id, session_id, agenda_id, ...}
  POST /api/meetings/agenda/delete?meeting_id=&session_id=&agenda_id=
  POST /api/meetings/minutes/save                body: {meeting_id, session_id, body, decisions, action_items}
"""
from __future__ import annotations

import datetime
from pathlib import Path
import sys
import uuid
from typing import List, Optional

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_APP_ROOT = _BACKEND_ROOT.parent
for _path in (_APP_ROOT, _BACKEND_ROOT):
    _raw = str(_path)
    sys.path[:] = [p for p in sys.path if p != _raw]
    sys.path.insert(0, _raw)

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.paths import PATHS
from core.utils import load_json, save_json
from core.auth import current_user
from core.audit import record as _audit
from app_v2.modules.meetings.repository import MeetingRepository
from app_v2.modules.meetings.service import MeetingService

# v8.8.6: 간단한 in-memory SSE broadcast — 회의록 동시편집 MVP.
#   pub 은 save_minutes 호출 직후. sub 은 /api/meetings/stream?meeting_id=X 로 EventSource 연결.
#   2+ 유저가 동시 편집 시 각자가 저장하는 순간 나머지에게 "외부 저장 알림" → FE 가 재조회.
import asyncio as _asyncio
import json as _json
_mtg_subscribers: dict = {}  # meeting_id → set[asyncio.Queue]
_mtg_lock = _asyncio.Lock()
MEETING_SERVICE = MeetingService(MeetingRepository(PATHS.data_root / "meetings" / "meetings.json"))

async def _mtg_subscribe(meeting_id: str):
    q: _asyncio.Queue = _asyncio.Queue(maxsize=32)
    async with _mtg_lock:
        _mtg_subscribers.setdefault(meeting_id, set()).add(q)
    return q

async def _mtg_unsubscribe(meeting_id: str, q):
    async with _mtg_lock:
        s = _mtg_subscribers.get(meeting_id) or set()
        s.discard(q)
        if not s and meeting_id in _mtg_subscribers:
            _mtg_subscribers.pop(meeting_id, None)

def _mtg_publish(meeting_id: str, payload: dict) -> None:
    """sync-safe publisher — 실패해도 save 는 성공."""
    try:
        try:
            loop = _asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        subs = list((_mtg_subscribers.get(meeting_id) or set()))
        for q in subs:
            try:
                if loop:
                    loop.call_soon_threadsafe(q.put_nowait, payload)
                else:
                    q.put_nowait(payload)
            except Exception:
                pass
    except Exception:
        pass


def _calendar_remove_meeting(meeting_id: str) -> None:
    try:
        from routers.calendar import remove_events_for_meeting
        remove_events_for_meeting(meeting_id)
    except Exception:
        pass


def _calendar_remove_session(meeting_id: str, session_id: str) -> None:
    try:
        from routers.calendar import remove_events_for_session
        remove_events_for_session(meeting_id, session_id)
    except Exception:
        pass


# For calendar→meeting status mirror (called from calendar router).
def mirror_action_item_status(meeting_id: str, session_id: str,
                              action_item_id: str, status: str) -> None:
    items = _load()
    midx, m = _find(items, meeting_id)
    if midx < 0 or not m:
        return
    sidx, s = _find_session(m, session_id)
    if sidx < 0:
        return
    minutes = s.get("minutes") or {}
    ai_list = minutes.get("action_items") or []
    ch = False
    for ai in ai_list:
        if isinstance(ai, dict) and ai.get("id") == action_item_id:
            if ai.get("status") != status:
                ai["status"] = status
                ch = True
    if ch:
        s["minutes"]["action_items"] = ai_list
        s["updated_at"] = _now()
        m["sessions"][sidx] = s
        m["updated_at"] = s["updated_at"]
        items[midx] = m
        _save(items)


def _new_did() -> str:
    return f"dec_{uuid.uuid4().hex[:8]}"


def _ensure_decision_objects(dlist: list) -> list:
    """v8.7.5: decisions 가 문자열/객체 혼재할 때 객체 list 로 정규화."""
    out = []
    seen = set()
    for d in (dlist or []):
        if isinstance(d, str):
            s = d.strip()
            if not s:
                continue
            did = _new_did()
            while did in seen:
                did = _new_did()
            seen.add(did)
            out.append({"id": did, "text": s, "due": "",
                        "calendar_pushed": False, "calendar_event_id": "",
                        "calendar_pushed_by": "", "calendar_pushed_at": ""})
        elif isinstance(d, dict):
            s = (d.get("text") or "").strip()
            if not s:
                continue
            did = d.get("id") or _new_did()
            while did in seen:
                did = _new_did()
            seen.add(did)
            out.append({
                "id": did,
                "text": s,
                "due": (d.get("due") or "").strip(),
                "calendar_pushed": bool(d.get("calendar_pushed")),
                "calendar_event_id": d.get("calendar_event_id") or "",
                "calendar_pushed_by": d.get("calendar_pushed_by") or "",
                "calendar_pushed_at": d.get("calendar_pushed_at") or "",
            })
    return out


def _ensure_action_item_ids(ai_list: list) -> list:
    """각 action_item 에 안정적인 id 부여 — calendar sync 의 키."""
    out = []
    seen = set()
    for ai in (ai_list or []):
        if not isinstance(ai, dict):
            continue
        aid = ai.get("id") or f"ai_{uuid.uuid4().hex[:8]}"
        while aid in seen:
            aid = f"ai_{uuid.uuid4().hex[:8]}"
        seen.add(aid)
        ai["id"] = aid
        ai.setdefault("status", "pending")
        out.append(ai)
    return out

# v8.7.6: 회의록 메일 발송 (사내 메일 API relay) ──────────────
import html as _html
import json as _json
import mimetypes
import re as _re
import urllib.error
import urllib.request
from pathlib import Path as _Path

MAIL_CONTENT_MAX = 2 * 1024 * 1024      # 2MB HTML body
MAIL_ATTACH_MAX  = 10 * 1024 * 1024     # 10MB total attachments
MAIL_MAX_RECIPIENTS = 199
MEETING_AGENDA_MAIL_TEXT_MAX = 700


def _load_mail_cfg() -> dict:
    from core.paths import PATHS as _P
    cfg = load_json(_P.data_root / "admin_settings.json", {})
    if not isinstance(cfg, dict):
        return {}
    m = cfg.get("mail") or {}
    return m if isinstance(m, dict) else {}


def _resolve_mail_group_ids_to_emails(mg_ids: List[str]) -> List[str]:
    """v8.8.3: FE 가 병합해서 보내는 id 처리.
    - "mg:<rawId>" → mail_groups.json 에서 조회 (extra_emails 포함).
    - "grp:<rawId>" → groups.json 에서 조회 (members 만, extra_emails 없음).
    - prefix 없는 raw id → 하위 호환: mail_groups.json 에서 먼저 조회.
    """
    if not mg_ids:
        return []
    try:
        from routers.mail_groups import _load as _mg_load
        from routers.groups import _load as _grp_load
        from routers.auth import read_users
    except Exception:
        return []

    mg_by_id = {g.get("id"): g for g in _mg_load() if isinstance(g, dict)}
    grp_by_id = {g.get("id"): g for g in _grp_load() if isinstance(g, dict)}
    all_users = {u.get("username", ""): u for u in read_users()}

    usernames: set = set()
    direct_emails: List[str] = []

    for prefixed_id in mg_ids:
        if prefixed_id.startswith("mg:"):
            raw = prefixed_id[3:]
            g = mg_by_id.get(raw)
            if not g:
                continue
            for m in (g.get("members") or []):
                if m:
                    usernames.add(m)
            for em in (g.get("extra_emails") or []):
                em = str(em).strip()
                if em and "@" in em:
                    direct_emails.append(em)
        elif prefixed_id.startswith("grp:"):
            raw = prefixed_id[4:]
            g = grp_by_id.get(raw)
            if not g:
                continue
            # groups 에는 owner + members 를 모두 수신 대상으로 포함
            for m in (g.get("members") or []):
                if m:
                    usernames.add(m)
            if g.get("owner"):
                usernames.add(g["owner"])
        else:
            # legacy: prefix 없음 → mail_groups 에서 raw id 조회
            g = mg_by_id.get(prefixed_id)
            if not g:
                continue
            for m in (g.get("members") or []):
                if m:
                    usernames.add(m)
            for em in (g.get("extra_emails") or []):
                em = str(em).strip()
                if em and "@" in em:
                    direct_emails.append(em)

    out: List[str] = list(direct_emails)
    for un in usernames:
        u = all_users.get(un)
        if u and u.get("email") and "@" in u.get("email", ""):
            out.append(u["email"])
    return out


def _resolve_group_members_to_emails(group_ids: List[str]) -> List[str]:
    """groups.py 의 그룹 id 리스트 → 멤버 username → email list."""
    if not group_ids:
        return []
    try:
        from routers.groups import _load as _grp_load
        from routers.auth import read_users
    except Exception:
        return []
    all_groups = {g.get("id"): g for g in _grp_load() if isinstance(g, dict)}
    usernames: set = set()
    for gid in group_ids:
        g = all_groups.get(gid)
        if not g:
            continue
        if g.get("owner"):
            usernames.add(g["owner"])
        for m in (g.get("members") or []):
            if m:
                usernames.add(m)
    all_users = {u.get("username", ""): u for u in read_users()}
    out: List[str] = []
    for un in usernames:
        u = all_users.get(un)
        if u and u.get("email") and "@" in u.get("email", ""):
            out.append(u["email"])
    return out


def _resolve_users_to_emails(usernames: List[str]) -> List[str]:
    if not usernames:
        return []
    try:
        from routers.auth import read_users
    except Exception:
        return []
    all_users = {u.get("username", ""): u for u in read_users()}
    out: List[str] = []
    for un in usernames:
        u = all_users.get(un)
        if u and u.get("email") and "@" in u.get("email", ""):
            out.append(u["email"])
    return out


def _plain_text_from_html(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    text = _re.sub(r"<\s*br\s*/?\s*>", "\n", text, flags=_re.I)
    text = _re.sub(r"</\s*(p|div|li|tr|h[1-6])\s*>", "\n", text, flags=_re.I)
    text = _re.sub(r"<[^>]+>", " ", text)
    text = _html.unescape(text)
    text = _re.sub(r"[ \t\r\f\v]+", " ", text)
    text = _re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def _brief_text(value: str, limit: int = MEETING_AGENDA_MAIL_TEXT_MAX) -> str:
    text = _plain_text_from_html(value)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _go_flow_link(issue_id: str = "") -> str:
    # 사내 short-link 기준. 직접 deep link 가 보장되지 않아 go/flow 로 안내한다.
    return "http://go/flow"


def _hydrate_tracker_issue_ref(issue_ref: Optional[dict]) -> Optional[dict]:
    """Keep imported tracker issue text/images with the agenda snapshot."""
    if not isinstance(issue_ref, dict):
        return None
    issue_id = str(issue_ref.get("issue_id") or issue_ref.get("id") or "").strip()
    if not issue_id:
        return None
    snap = dict(issue_ref)
    snap["issue_id"] = issue_id
    snap["id"] = snap.get("id") or issue_id
    try:
        from routers import tracker as tracker_router
        issues = tracker_router._load()
        iss = next((row for row in issues if str(row.get("id") or "") == issue_id), None)
        if iss:
            desc = str(iss.get("description") or "")
            images = list(iss.get("images") or [])
            snap.update({
                "title": iss.get("title") or snap.get("title") or "",
                "status": iss.get("status") or snap.get("status") or "",
                "category": iss.get("category") or snap.get("category") or "",
                "priority": iss.get("priority") or snap.get("priority") or "",
                "username": iss.get("username") or snap.get("username") or "",
                "description": desc or snap.get("description") or "",
                "description_html": tracker_router._render_description(desc) or snap.get("description_html") or "",
                "images": images or list(snap.get("images") or []),
                "image_count": len(images or snap.get("images") or []),
                "links": list(iss.get("links") or snap.get("links") or []),
                "lots": list(iss.get("lots") or snap.get("lots") or []),
                "lot_count": len(iss.get("lots") or snap.get("lots") or []),
                "comment_count": len(iss.get("comments") or []),
                "updated_at": iss.get("updated_at") or iss.get("created") or iss.get("timestamp") or snap.get("updated_at") or "",
            })
    except Exception:
        snap["images"] = list(snap.get("images") or [])
        snap["image_count"] = int(snap.get("image_count") or len(snap.get("images") or []) or 0)
    return snap


def _agenda_issue_mail_html(a: dict) -> str:
    """Brief issue block for meeting mail.

    Meeting UI keeps imported issue text/images in `issue_ref`; mail intentionally
    omits agenda images and gives a compact text summary plus go/flow link.
    """
    issue = a.get("issue_ref") if isinstance(a.get("issue_ref"), dict) else {}
    issue_id = str(issue.get("issue_id") or issue.get("id") or "").strip()
    if not issue_id:
        return ""
    title = str(issue.get("title") or "").strip()
    raw_desc = issue.get("description_html") or issue.get("description") or a.get("description") or ""
    desc = _brief_text(raw_desc)
    image_count = int(issue.get("image_count") or len(issue.get("images") or []) or 0)
    tracker_link = _go_flow_link(issue_id)
    parts = [
        "<div style='margin-top:7px;padding:8px 10px;border:1px solid #ede9fe;border-radius:6px;background:#faf5ff;'>",
        "<div style='font-size:11px;color:#7c3aed;font-weight:700;margin-bottom:3px;'>연결 이슈</div>",
        f"<div style='font-size:12px;color:#1f2937;font-weight:700;'>{_html.escape(title or issue_id)}</div>",
    ]
    if desc:
        parts.append(f"<div style='font-size:11px;color:#4b5563;line-height:1.45;margin-top:4px;'>{_html.escape(desc)}</div>")
    if image_count:
        parts.append(
            "<div style='font-size:10px;color:#92400e;margin-top:5px;'>"
            f"이미지 {image_count}개는 메일 용량 관리를 위해 제외했습니다."
            "</div>"
        )
    parts.append(
        f"<div style='font-size:11px;margin-top:5px;'><a href='{_html.escape(tracker_link)}' "
        f"style='color:#7c3aed;text-decoration:underline;'>go/flow에서 상세 보기</a>"
        f" <span style='color:#9ca3af'>#{_html.escape(issue_id)}</span></div>"
    )
    parts.append("</div>")
    return "".join(parts)


def _meeting_mail_html(meeting: dict, session: dict, mail_body: str = "") -> str:
    """아젠다 + (선택) 메일 본문 + 결정사항 + 액션아이템 단일 HTML 메일 조립.

    v8.8.16: 공동 작성된 minutes.body 를 자동으로 끌어오던 것을 제거.
      - 주관자가 mail_body 파라미터로 명시적으로 작성한 텍스트만 사용.
      - mail_body 가 비면 메일 본문 섹션 자체가 생략된다 (아젠다/결정/액션만 발송).
    """
    esc = _html.escape
    agendas = session.get("agendas") or []
    minutes = session.get("minutes") or {}
    decisions = minutes.get("decisions") or []
    actions = minutes.get("action_items") or []
    rows_ag = ""
    for i, a in enumerate(agendas, 1):
        link = a.get("link") or ""
        link_html = f'<br/><a href="{esc(link)}" style="font-size:11px;color:#ea580c;">🔗 {esc(link)}</a>' if link else ""
        issue_html = _agenda_issue_mail_html(a if isinstance(a, dict) else {})
        rows_ag += (
            f"<tr><td style='padding:6px 10px;border-bottom:1px solid #e5e7eb;width:26px;'>#{i}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb;'>"
            f"<b>{esc(a.get('title',''))}</b>"
            + (f"<div style='font-size:11px;color:#6b7280;margin-top:2px;line-height:1.45'>{esc(_brief_text(a.get('description',''), 500))}</div>" if a.get('description') else "")
            + link_html
            + issue_html
            + f"</td><td style='padding:6px 10px;border-bottom:1px solid #e5e7eb;font-family:monospace;font-size:11px;color:#374151;'>{esc(a.get('owner',''))}</td></tr>"
        )
    ag_tbl = (
        "<h3 style='font-size:13px;margin:14px 0 6px;color:#374151;'>📋 아젠다</h3>"
        "<table style='width:100%;border-collapse:collapse;border:1px solid #e5e7eb;'>"
        "<thead><tr style='background:#f3f4f6;font-size:11px;color:#6b7280;'>"
        "<th style='text-align:left;padding:6px 10px;'>#</th>"
        "<th style='text-align:left;padding:6px 10px;'>제목 · 설명</th>"
        "<th style='text-align:left;padding:6px 10px;width:100px;'>담당</th>"
        f"</tr></thead><tbody>{rows_ag or '<tr><td colspan=3 style=padding:10px;color:#9ca3af;>(아젠다 없음)</td></tr>'}</tbody></table>"
    )
    body_html = ""
    # v9.0.1: 회의록 정리 본문(공동 편집 minutes.body) 을 메일에 자동 포함 — 사용자 요구.
    #   주관자가 mail_body 를 명시적으로 적었으면 그게 우선, 비어있으면 minutes.body 폴백.
    mail_body_clean = (mail_body or "").strip()
    if not mail_body_clean:
        minutes_body = (minutes.get("body") or "").strip()
        if minutes_body:
            mail_body_clean = minutes_body
    if mail_body_clean:
        body_lines = mail_body_clean.splitlines()
        body_html = (
            "<h3 style='font-size:13px;margin:14px 0 6px;color:#374151;'>📝 메일 본문</h3>"
            "<div style='padding:10px 12px;border:1px solid #e5e7eb;border-radius:6px;background:#fafafa;font-size:12px;line-height:1.55;'>"
            + "<br/>".join(esc(ln) for ln in body_lines) + "</div>"
        )
    dec_html = ""
    if decisions:
        dec_rows = ""
        for d in decisions:
            if isinstance(d, str):
                dec_rows += f"<li style='margin:4px 0'>{esc(d)}</li>"
            elif isinstance(d, dict):
                due = f" · <span style='color:#6b7280'>마감 {esc(d.get('due',''))}</span>" if d.get('due') else ""
                dec_rows += f"<li style='margin:4px 0'>{esc(d.get('text',''))}{due}</li>"
        dec_html = f"<h3 style='font-size:13px;margin:14px 0 6px;color:#374151;'>⚡ 결정사항</h3><ul style='margin:0;padding-left:20px;font-size:12px;'>{dec_rows}</ul>"
    act_html = ""
    if actions:
        rows_a = ""
        for a in actions:
            rows_a += (
                f"<tr><td style='padding:6px 10px;border-bottom:1px solid #e5e7eb;'>{esc(a.get('text',''))}</td>"
                f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb;font-family:monospace;font-size:11px;'>{esc(a.get('owner','') or '—')}</td>"
                f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb;font-family:monospace;font-size:11px;'>{esc(a.get('due','') or '—')}</td></tr>"
            )
        act_html = (
            "<h3 style='font-size:13px;margin:14px 0 6px;color:#374151;'>✅ 액션 아이템</h3>"
            "<table style='width:100%;border-collapse:collapse;border:1px solid #e5e7eb;'>"
            "<thead><tr style='background:#f3f4f6;font-size:11px;color:#6b7280;'>"
            "<th style='text-align:left;padding:6px 10px;'>할 일</th>"
            "<th style='text-align:left;padding:6px 10px;width:100px;'>담당</th>"
            "<th style='text-align:left;padding:6px 10px;width:100px;'>마감</th>"
            f"</tr></thead><tbody>{rows_a}</tbody></table>"
        )
    sched = session.get("scheduled_at") or ""
    return (
        "<div style='font-family:-apple-system,Segoe UI,Arial,sans-serif;color:#1f2937;max-width:720px;'>"
        f"<h2 style='font-size:16px;margin:0 0 4px;color:#ea580c;'>flow · 회의록 공유</h2>"
        f"<div style='font-size:12px;color:#6b7280;margin-bottom:8px;'>"
        f"<b>{esc(meeting.get('title',''))}</b> · {session.get('idx','?')}차"
        + (f" · {esc(sched).replace('T',' ')[:16]}" if sched else "")
        + f" · 주관 {esc(meeting.get('owner','—'))}</div>"
        + ag_tbl + body_html + dec_html + act_html
        + "<hr style='border:none;border-top:1px solid #e5e7eb;margin:18px 0 8px 0;'/>"
        "<div style='font-size:10px;color:#9ca3af;'>Sent by flow · 자동 전송된 메일입니다.</div>"
        "</div>"
    )


def _encode_multipart(fields: Dict[str, str], files: List[tuple]) -> tuple:
    boundary = "----flowMeeting" + uuid.uuid4().hex
    chunks: List[bytes] = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n'.encode())
        chunks.append(b"Content-Type: text/plain; charset=utf-8\r\n\r\n")
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    for (fname_field, filename, content, mime) in files:
        chunks.append(f"--{boundary}\r\n".encode())
        safe_fn = filename.replace('"', '').replace("\r", "").replace("\n", "")
        chunks.append(
            f'Content-Disposition: form-data; name="{fname_field}"; filename="{safe_fn}"\r\n'.encode()
        )
        chunks.append(f"Content-Type: {mime}\r\n\r\n".encode())
        chunks.append(content)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _send_minutes_mail(meeting: dict, session: dict, *,
                        to_addrs: List[str], subject: str, actor: str,
                        mail_body: str = "") -> dict:
    """사내 메일 API 로 회의록 HTML 전송. 설정 미비/에러 시 {ok:False, error} 반환.

    v8.8.16: mail_body 를 명시적으로 받아 _meeting_mail_html 에 전달.
      호출자가 비워 두면 메일 본문 섹션 없이 아젠다/결정/액션만 포함된다.
    """
    cfg = _load_mail_cfg()
    if not cfg.get("enabled") or not (cfg.get("api_url") or "").strip():
        return {"ok": False, "error": "메일 API 가 설정되지 않았습니다 (Admin > 메일 API)."}
    uniq: List[str] = []
    seen: set = set()
    for em in to_addrs:
        em = (em or "").strip()
        if em and "@" in em and em not in seen:
            seen.add(em)
            uniq.append(em)
    if not uniq:
        return {"ok": False, "error": "수신자 이메일이 없습니다."}
    if len(uniq) > MAIL_MAX_RECIPIENTS:
        return {"ok": False, "error": f"수신자는 최대 {MAIL_MAX_RECIPIENTS}명까지 허용됩니다 (현재 {len(uniq)}명)."}
    html_body = _meeting_mail_html(meeting, session, mail_body=mail_body)
    if len(html_body.encode("utf-8")) > MAIL_CONTENT_MAX:
        return {"ok": False, "error": "메일 본문이 2MB 한도를 초과했습니다."}
    receiver_list = [{"email": em, "recipientType": "To", "seq": i + 1} for i, em in enumerate(uniq)]
    _sender_addr = (cfg.get("from_addr") or "").strip()
    data_obj: Dict[str, Any] = {
        "content":           html_body,
        "receiverList":      receiver_list,
        # v8.8.24: camelCase 표준 + legacy 소문자 양쪽 주입 (core/mail.send_mail 과 통일).
        "senderMailAddress": _sender_addr,
        "senderMailaddress": _sender_addr,
        "statusCode":        (cfg.get("status_code") or "").strip(),
        "title":             subject or f"[flow 회의록] {meeting.get('title','')} · {session.get('idx','')}차",
    }
    extra = cfg.get("extra_data") or {}
    if isinstance(extra, dict):
        for k, v in extra.items():
            if k and k not in data_obj:
                data_obj[k] = v
    headers = {}
    cfg_headers = cfg.get("headers") or {}
    if isinstance(cfg_headers, dict):
        for k, v in cfg_headers.items():
            if k:
                headers[str(k)] = str(v)
    url = cfg.get("api_url").strip()
    # v9.0.0: 사내 메일 API 규약 — 소문자 `mailsendString` 을 multipart top-level
    #   form field 로 직접 전송. 이전에는 flat `data` 필드에 data_obj 를 그대로
    #   보냈는데, 서버 스펙이 `mailSendString` 키를 요구해 회의록 메일이 누락되던
    #   문제가 있었음 (informs 쪽은 v8.8.21 부터 래핑했지만 meetings 는 미적용).
    mail_send_string = _json.dumps(data_obj, ensure_ascii=False)
    if url.lower() == "dry-run":
        return {"ok": True, "dry_run": True, "to": uniq,
                "subject": data_obj["title"], "preview_data": data_obj,
                "preview_data_wrapped": {"mailSendString": mail_send_string}}
    fields = {"mailSendString": mail_send_string}
    body_bytes, content_type = _encode_multipart(fields, [])
    hdrs_out = dict(headers); hdrs_out["Content-Type"] = content_type
    try:
        r = urllib.request.Request(url, data=body_bytes, headers=hdrs_out, method="POST")
        with urllib.request.urlopen(r, timeout=15) as resp:
            status = resp.status
            text = resp.read(2048).decode("utf-8", errors="replace")
        return {"ok": status < 400, "status": status, "response": text[:512], "to": uniq,
                "subject": data_obj["title"]}
    except urllib.error.HTTPError as e:
        det = ""
        try: det = e.read(512).decode("utf-8", errors="replace")
        except Exception: pass
        return {"ok": False, "error": f"메일 API HTTP {e.code}: {det[:200]}"}
    except Exception as e:
        return {"ok": False, "error": f"메일 전송 실패: {e}"}


# Any 는 typing 으로 이미 import 되어 있지 않음 — meetings.py 위쪽 import 에 추가 필요.
from typing import Any  # noqa: E402

router = APIRouter(prefix="/api/meetings", tags=["meetings"])


@router.get("/categories")
def meeting_categories_compat():
    """Compatibility alias: meeting settings share the calendar category palette."""
    from routers.calendar import get_categories
    return get_categories()


@router.post("/categories/save")
def save_meeting_categories_compat(req: dict, request: Request):
    """Compatibility alias for older Meeting PageGear builds."""
    from routers.calendar import CategoriesSave, save_categories
    return save_categories(CategoriesSave(categories=req.get("categories") or []), request)

MEET_DIR = PATHS.data_root / "meetings"
MEET_DIR.mkdir(parents=True, exist_ok=True)
MEET_FILE = MEET_DIR / "meetings.json"

VALID_SESSION_STATUS = {"scheduled", "in_progress", "completed", "cancelled"}
VALID_MEETING_STATUS = {"active", "archived", "cancelled"}
VALID_RECURRENCE_TYPE = {"none", "weekly"}


# ── persistence ─────────────────────────────────────────────────────
def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _new_mid() -> str:
    return f"mt_{datetime.datetime.now().strftime('%y%m%d')}_{uuid.uuid4().hex[:6]}"


def _new_sid() -> str:
    return f"ss_{uuid.uuid4().hex[:8]}"


def _new_aid() -> str:
    return f"ag_{uuid.uuid4().hex[:8]}"


def _default_recurrence() -> dict:
    return {"type": "none", "count_per_week": 0, "weekday": [], "note": ""}


def _migrate_entry(m: dict) -> dict:
    """v8.7.2 → v8.7.4 one-shot migration. Mutates m and returns it."""
    if "sessions" in m and isinstance(m.get("sessions"), list):
        # Ensure recurrence exists
        if "recurrence" not in m or not isinstance(m.get("recurrence"), dict):
            m["recurrence"] = _default_recurrence()
        # Meeting-level status mapping: old session status -> meeting status
        m_status = m.get("status") or "active"
        if m_status not in VALID_MEETING_STATUS:
            m["status"] = "active"
        return m

    # Legacy: agendas/minutes at meeting level → wrap into 1 session.
    now = m.get("updated_at") or _now()
    session = {
        "id": _new_sid(),
        "idx": 1,
        "scheduled_at": m.get("scheduled_at") or "",
        "status": m.get("status") or "scheduled",
        "agendas": m.get("agendas") or [],
        "minutes": m.get("minutes"),
        "created_at": m.get("created_at") or now,
        "updated_at": now,
    }
    # Map old session status to meeting status
    if session["status"] == "cancelled":
        meeting_status = "cancelled"
    else:
        meeting_status = "active"
    m2 = {
        "id": m.get("id") or _new_mid(),
        "title": m.get("title") or "",
        "owner": m.get("owner") or m.get("created_by") or "",
        "recurrence": _default_recurrence(),
        "status": meeting_status,
        "sessions": [session],
        "created_by": m.get("created_by") or m.get("owner") or "",
        "created_at": m.get("created_at") or now,
        "updated_at": now,
    }
    # remove legacy keys just in case
    for k in ("agendas", "minutes", "scheduled_at"):
        m2.pop(k, None)
    return m2


def _normalize_minutes(minutes):
    if not isinstance(minutes, dict):
        return minutes
    # Decisions: string → object list.
    if "decisions" in minutes:
        minutes["decisions"] = _ensure_decision_objects(minutes.get("decisions") or [])
    return minutes


# v8.7.9: meeting palette — each meeting locks in a color at creation time
# (sequential round-robin). Legacy meetings get a color lazily on first load.
MEETING_PALETTE = [
    "#3b82f6",  # blue
    "#10b981",  # emerald
    "#f59e0b",  # amber
    "#ec4899",  # pink
    "#8b5cf6",  # violet
    "#06b6d4",  # cyan
    "#f97316",  # orange
    "#22c55e",  # green
    "#ef4444",  # red
    "#a855f7",  # purple
    "#eab308",  # yellow
    "#14b8a6",  # teal
    "#6366f1",  # indigo
    "#d946ef",  # fuchsia
    "#0ea5e9",  # sky
]


def _backfill_meeting_colors(items: list) -> bool:
    """Assign palette color to any meeting missing one, preserving existing.
    Returns True if any mutation happened (caller may persist)."""
    used = [m.get("color") for m in items if isinstance(m, dict) and m.get("color")]
    used_set = set(used)
    # Keep creation-order stability — sort by created_at when backfilling.
    without = [m for m in items if isinstance(m, dict) and not m.get("color")]
    without.sort(key=lambda x: x.get("created_at") or "")
    mutated = False
    for m in without:
        for i in range(len(MEETING_PALETTE)):
            cand = MEETING_PALETTE[(len(used_set) + i) % len(MEETING_PALETTE)]
            if cand not in used_set or len(used_set) >= len(MEETING_PALETTE):
                m["color"] = cand
                used_set.add(cand)
                mutated = True
                break
    return mutated


def _load() -> list:
    data = load_json(MEET_FILE, [])
    if not isinstance(data, list):
        return []
    out = []
    for m in data:
        if not isinstance(m, dict):
            continue
        entry = _migrate_entry(dict(m))
        for s in (entry.get("sessions") or []):
            if s.get("minutes"):
                s["minutes"] = _normalize_minutes(s["minutes"])
        out.append(entry)
    # v8.7.9: lazy backfill of meeting colors.
    if _backfill_meeting_colors(out):
        try:
            _save(out)
        except Exception:
            pass
    return out


def _save(items: list) -> None:
    save_json(MEET_FILE, items, indent=2)


def _find(items: list, mid: str) -> tuple:
    for i, m in enumerate(items):
        if m.get("id") == mid:
            return i, m
    return -1, None


def _find_session(m: dict, sid: str) -> tuple:
    for i, s in enumerate(m.get("sessions") or []):
        if s.get("id") == sid:
            return i, s
    return -1, None


def _validate_session_status(s: str) -> str:
    s = (s or "").strip()
    if s and s not in VALID_SESSION_STATUS:
        raise HTTPException(400, f"Invalid session status: {s}")
    return s


def _validate_meeting_status(s: str) -> str:
    s = (s or "").strip()
    if s and s not in VALID_MEETING_STATUS:
        raise HTTPException(400, f"Invalid meeting status: {s}")
    return s


def _normalize_dt(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    try:
        if s.endswith("Z"):
            s = s[:-1]
        if len(s) == 16:
            s = s + ":00"
        d = datetime.datetime.fromisoformat(s)
        return d.isoformat(timespec="seconds")
    except Exception:
        raise HTTPException(400, "Invalid datetime (expected YYYY-MM-DDTHH:MM)")


def _normalize_recurrence(raw: Optional[dict]) -> dict:
    if not raw or not isinstance(raw, dict):
        return _default_recurrence()
    rtype = (raw.get("type") or "none").strip()
    if rtype not in VALID_RECURRENCE_TYPE:
        rtype = "none"
    try:
        cpw = int(raw.get("count_per_week") or 0)
    except Exception:
        cpw = 0
    cpw = max(0, min(7, cpw))
    wd_raw = raw.get("weekday") or []
    weekday: list = []
    if isinstance(wd_raw, list):
        for x in wd_raw:
            try:
                v = int(x)
                if 0 <= v <= 6 and v not in weekday:
                    weekday.append(v)
            except Exception:
                continue
    weekday.sort()
    note = (raw.get("note") or "").strip()[:200]
    return {"type": rtype, "count_per_week": cpw, "weekday": weekday, "note": note}


# ── pydantic models ─────────────────────────────────────────────────
class RecurrenceReq(BaseModel):
    type: Optional[str] = "none"
    count_per_week: Optional[int] = 0
    weekday: Optional[List[int]] = None
    note: Optional[str] = ""


class MeetingCreate(BaseModel):
    title: str
    owner: Optional[str] = None
    first_scheduled_at: Optional[str] = ""
    recurrence: Optional[RecurrenceReq] = None
    category: Optional[str] = ""  # calendar 카테고리 (색상)
    group_ids: Optional[List[str]] = None   # v8.8.2: 공개범위 — 비우면 전원 공개


class MeetingUpdate(BaseModel):
    id: str
    title: Optional[str] = None
    owner: Optional[str] = None
    status: Optional[str] = None
    recurrence: Optional[RecurrenceReq] = None
    category: Optional[str] = None
    group_ids: Optional[List[str]] = None   # v8.8.2


class SessionAdd(BaseModel):
    meeting_id: str
    scheduled_at: Optional[str] = ""


class SessionUpdate(BaseModel):
    meeting_id: str
    session_id: str
    scheduled_at: Optional[str] = None
    status: Optional[str] = None


class AgendaAdd(BaseModel):
    meeting_id: str
    session_id: str
    title: str
    description: Optional[str] = ""
    link: Optional[str] = ""
    owner: Optional[str] = None
    issue_ref: Optional[dict] = None


class AgendaUpdate(BaseModel):
    meeting_id: str
    session_id: str
    agenda_id: str
    title: Optional[str] = None
    description: Optional[str] = None
    link: Optional[str] = None
    owner: Optional[str] = None
    issue_ref: Optional[dict] = None


class ActionItem(BaseModel):
    # v8.7.9: id preserved across saves so calendar events stay stable.
    id: Optional[str] = ""
    text: str
    owner: Optional[str] = ""
    due: Optional[str] = ""
    # v8.7.6: 그룹 단위 담당자. owner(개인) 과 병행. 메일 발송 시 그룹 멤버 email 로 확산.
    group_ids: Optional[List[str]] = None


class MinutesAppendReq(BaseModel):
    # v8.8.13: 그룹 멤버 공동 작성 — 본문 append 전용 (수정/삭제 불가).
    meeting_id: str
    session_id: str
    text: str


class MinutesAppendDeleteReq(BaseModel):
    meeting_id: str
    session_id: str
    append_id: str


class MinutesSave(BaseModel):
    meeting_id: str
    session_id: str
    body: Optional[str] = ""
    # v8.7.5: 문자열 또는 {id,text,due} 객체 list 둘 다 수용.
    decisions: Optional[List] = None
    action_items: Optional[List[ActionItem]] = None
    # v8.7.6: 저장과 동시에 사내 메일로 아젠다+회의록+액션아이템 전송
    send_mail: Optional[bool] = False
    mail_to_users: Optional[List[str]] = None     # username list
    mail_groups: Optional[List[str]] = None       # admin recipient_groups names (legacy)
    mail_group_ids: Optional[List[str]] = None    # v8.7.7: mail_groups.json 의 그룹 id
    mail_to: Optional[List[str]] = None           # direct email list
    mail_subject: Optional[str] = ""
    # v8.8.15: OT-lite — 클라이언트가 보고 있던 revision. 저장 시점 서버 rev 과 다르면 409.
    #   FE 는 409 응답의 current_body/rev 로 머지하거나 user 에게 경고한 뒤 다시 저장.
    base_rev: Optional[int] = None
    # v8.8.16: 메일 전용 본문 — 공동 작성된 minutes.body 와 분리해 주관자가 직접 작성.
    #   비우고 send_mail=True 면 메일에 본문 섹션 없이 아젠다/결정/액션만 나감.
    mail_body: Optional[str] = ""


# ── permission helpers ─────────────────────────────────────────────
def _is_admin(me: dict) -> bool:
    return (me or {}).get("role") == "admin"


def _can_edit_meeting(me: dict, meeting: dict) -> bool:
    return _is_admin(me) or meeting.get("owner") == me["username"]


def _can_append_minutes(me: dict, meeting: dict) -> bool:
    """v8.8.13: 공동 작성 허용 — owner/admin 은 물론, 회의 공개 범위 그룹 멤버도 본문 append 가능.
    meeting.group_ids 가 비어있으면(전체 공개) 로그인 유저 누구나 허용."""
    if _can_edit_meeting(me, meeting):
        return True
    gids = meeting.get("group_ids") or []
    if not gids:
        return True
    try:
        from routers.groups import _load as _load_groups
        uname = (me or {}).get("username") or ""
        for g in _load_groups():
            if g.get("id") not in gids:
                continue
            if g.get("owner") == uname or uname in (g.get("members") or []):
                return True
    except Exception:
        pass
    return False


def _can_edit_agenda(me: dict, meeting: dict, agenda: dict) -> bool:
    if _is_admin(me):
        return True
    if meeting.get("owner") == me["username"]:
        return True
    return agenda.get("owner") == me["username"]


def _next_session_idx(m: dict) -> int:
    ss = m.get("sessions") or []
    if not ss:
        return 1
    try:
        return max(int(s.get("idx") or 0) for s in ss) + 1
    except Exception:
        return len(ss) + 1


# ── endpoints ──────────────────────────────────────────────────────
def _meeting_visible(m: dict, username: str, role: str, my_gids: set) -> bool:
    """v8.8.2: group_ids 기반 가시성. admin/owner/creator 는 항상 가시."""
    if role == "admin":
        return True
    if m.get("owner") == username or m.get("created_by") == username:
        return True
    gids = m.get("group_ids") or []
    if not gids:
        return True
    for g in gids:
        if g in my_gids:
            return True
    return False


def _my_meeting_group_ids(username: str, role: str) -> set:
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


@router.get("/list")
def list_meetings(
    request: Request,
    status: Optional[str] = Query(None),
    owner: Optional[str] = Query(None),
):
    me = current_user(request)
    role = me.get("role", "user")
    my_gids = _my_meeting_group_ids(me["username"], role)
    items = _load()
    items = [m for m in items if _meeting_visible(m, me["username"], role, my_gids)]
    if status:
        items = [m for m in items if (m.get("status") or "active") == status]
    if owner:
        items = [m for m in items if m.get("owner") == owner]
    # sort by last session scheduled_at desc, fallback to created_at
    def _sort_key(m):
        ss = m.get("sessions") or []
        latest = max((s.get("scheduled_at") or "" for s in ss), default="")
        return (latest, m.get("created_at") or "")
    items.sort(key=_sort_key, reverse=True)
    return {"meetings": items}


@router.get("/{mid}")
def get_meeting(mid: str, request: Request):
    me = current_user(request)
    role = me.get("role", "user")
    my_gids = _my_meeting_group_ids(me["username"], role)
    items = _load()
    _, m = _find(items, mid)
    if not m:
        raise HTTPException(404)
    if not _meeting_visible(m, me["username"], role, my_gids):
        raise HTTPException(403, "이 회의를 볼 수 없습니다.")
    return {"meeting": m}


@router.post("/create")
def create_meeting(req: MeetingCreate, request: Request):
    me = current_user(request)
    title = (req.title or "").strip()
    if not title:
        raise HTTPException(400, "title required")
    owner = (req.owner or me["username"]).strip() or me["username"]
    rec = _normalize_recurrence(req.recurrence.dict() if req.recurrence else None)
    first_dt = _normalize_dt(req.first_scheduled_at or "")
    now = _now()
    first_session = {
        "id": _new_sid(),
        "idx": 1,
        "scheduled_at": first_dt,
        "status": "scheduled",
        "agendas": [],
        "minutes": None,
        "created_at": now,
        "updated_at": now,
    }
    items = _load()
    # v8.7.9: pick a color for this meeting from the palette — preserve existing
    # assignments so previously-created meetings keep their color.
    used_colors = {m.get("color") for m in items if isinstance(m, dict) and m.get("color")}
    new_color = ""
    for i in range(len(MEETING_PALETTE)):
        cand = MEETING_PALETTE[(len(items) + i) % len(MEETING_PALETTE)]
        if cand not in used_colors:
            new_color = cand
            break
    if not new_color:
        new_color = MEETING_PALETTE[len(items) % len(MEETING_PALETTE)]
    entry = {
        "id": _new_mid(),
        "title": title,
        "owner": owner,
        "recurrence": rec,
        "status": "active",
        "color": new_color,
        "sessions": [first_session],
        "created_by": me["username"],
        "created_at": now,
        "updated_at": now,
        # v8.8.2: 공개범위 그룹.
        "group_ids": [str(g).strip() for g in (req.group_ids or []) if g and str(g).strip()],
    }
    result = MEETING_SERVICE.create_meeting(entry)
    if not result.ok:
        raise HTTPException(400, result.error)
    _audit(request, "meetings:create",
           detail=f"id={entry['id']} title={title[:60]} rec={rec['type']}",
           tab="meetings")
    return {"ok": True, "meeting": entry}


@router.post("/update")
def update_meeting(req: MeetingUpdate, request: Request):
    me = current_user(request)
    items = _load()
    idx, m = _find(items, req.id)
    if not m:
        raise HTTPException(404)
    if not _can_edit_meeting(me, m):
        raise HTTPException(403, "Only owner or admin can edit this meeting")
    changed = []
    if req.title is not None:
        t = (req.title or "").strip()
        if not t:
            raise HTTPException(400, "title cannot be empty")
        if t != m.get("title"):
            m["title"] = t
            changed.append("title")
    if req.owner is not None:
        o = (req.owner or "").strip()
        if o and o != m.get("owner"):
            # v8.7.7: 주관자 변경은 "만든 유저(created_by) 또는 admin" 만 가능.
            # 이미 주관자이더라도 원 생성자가 아니면 주관자 이양 불가.
            creator = m.get("created_by") or m.get("owner") or ""
            if not _is_admin(me) and me["username"] != creator:
                raise HTTPException(403, "주관자 변경은 회의 생성자 또는 admin 만 가능합니다.")
            m["owner"] = o
            changed.append("owner")
    if req.status is not None:
        st = _validate_meeting_status(req.status)
        if st and st != m.get("status"):
            m["status"] = st
            changed.append("status")
    if req.recurrence is not None:
        rec = _normalize_recurrence(req.recurrence.dict())
        if rec != m.get("recurrence"):
            m["recurrence"] = rec
            changed.append("recurrence")
    # v8.8.2: group_ids 변경.
    if req.group_ids is not None:
        new_gids = [str(g).strip() for g in (req.group_ids or []) if g and str(g).strip()]
        if sorted(m.get("group_ids") or []) != sorted(new_gids):
            m["group_ids"] = new_gids
            changed.append("group_ids")
    if not changed:
        return {"ok": True, "meeting": m, "noop": True}
    updated_at = _now()
    m["updated_at"] = updated_at
    result = MEETING_SERVICE.update_meeting(m["id"], m)
    if not result.ok:
        raise HTTPException(404, result.error)
    m = result.data["meeting"]
    _audit(request, "meetings:update",
           detail=f"id={m['id']} fields={','.join(changed)}", tab="meetings")
    return {"ok": True, "meeting": m}


@router.post("/delete")
def delete_meeting(request: Request, id: str = Query(...)):
    me = current_user(request)
    items = _load()
    idx, m = _find(items, id)
    if not m:
        raise HTTPException(404)
    if not _can_edit_meeting(me, m):
        raise HTTPException(403, "Only owner or admin can delete")
    result = MEETING_SERVICE.delete_meeting(id)
    if not result.ok:
        raise HTTPException(404, result.error)
    _calendar_remove_meeting(id)
    _audit(request, "meetings:delete",
           detail=f"id={id} title={(m.get('title') or '')[:60]}", tab="meetings")
    return {"ok": True}


# ── sessions ──────────────────────────────────────────────────────
@router.post("/session/add")
def add_session(req: SessionAdd, request: Request):
    me = current_user(request)
    items = _load()
    idx, m = _find(items, req.meeting_id)
    if not m:
        raise HTTPException(404, "meeting not found")
    if not _can_edit_meeting(me, m):
        raise HTTPException(403, "Only owner or admin can add sessions")
    sched = _normalize_dt(req.scheduled_at or "")
    now = _now()
    new_s = {
        "id": _new_sid(),
        "idx": _next_session_idx(m),
        "scheduled_at": sched,
        "status": "scheduled",
        "agendas": [],
        "minutes": None,
        "created_at": now,
        "updated_at": now,
    }
    result = MEETING_SERVICE.add_session(m["id"], new_s, now)
    if not result.ok:
        raise HTTPException(404, result.error)
    m = result.data["meeting"]
    _audit(request, "meetings:session_add",
           detail=f"meeting={m['id']} session={new_s['id']} idx={new_s['idx']}",
           tab="meetings")
    return {"ok": True, "meeting": m, "session": new_s}


@router.post("/session/update")
def update_session(req: SessionUpdate, request: Request):
    me = current_user(request)
    items = _load()
    idx, m = _find(items, req.meeting_id)
    if not m:
        raise HTTPException(404, "meeting not found")
    if not _can_edit_meeting(me, m):
        raise HTTPException(403, "Only owner or admin can edit sessions")
    sidx, s = _find_session(m, req.session_id)
    if sidx < 0:
        raise HTTPException(404, "session not found")
    changed = []
    if req.scheduled_at is not None:
        dt = _normalize_dt(req.scheduled_at)
        if dt != s.get("scheduled_at"):
            s["scheduled_at"] = dt
            changed.append("scheduled_at")
    if req.status is not None:
        st = _validate_session_status(req.status)
        if st and st != s.get("status"):
            s["status"] = st
            changed.append("status")
    if not changed:
        return {"ok": True, "meeting": m, "session": s, "noop": True}
    updated_at = _now()
    s["updated_at"] = updated_at
    result = MEETING_SERVICE.update_session(m["id"], s["id"], s, updated_at)
    if not result.ok:
        code = 404 if result.error in ("meeting not found", "session not found") else 400
        raise HTTPException(code, result.error)
    m = result.data["meeting"]
    s = result.data["session"]
    _audit(request, "meetings:session_update",
           detail=f"meeting={m['id']} session={s['id']} fields={','.join(changed)}",
           tab="meetings")
    return {"ok": True, "meeting": m, "session": s}


@router.post("/session/delete")
def delete_session(request: Request,
                   meeting_id: str = Query(...),
                   session_id: str = Query(...)):
    me = current_user(request)
    items = _load()
    idx, m = _find(items, meeting_id)
    if not m:
        raise HTTPException(404, "meeting not found")
    if not _can_edit_meeting(me, m):
        raise HTTPException(403, "Only owner or admin can delete sessions")
    sessions = m.get("sessions") or []
    if len(sessions) <= 1:
        raise HTTPException(400, "cannot delete the only session — delete the meeting instead")
    new_sessions = [s for s in sessions if s.get("id") != session_id]
    if len(new_sessions) == len(sessions):
        raise HTTPException(404, "session not found")
    updated_at = _now()
    result = MEETING_SERVICE.delete_session(meeting_id, session_id, updated_at)
    if not result.ok:
        raise HTTPException(404, result.error)
    m = result.data["meeting"]
    _calendar_remove_session(meeting_id, session_id)
    _audit(request, "meetings:session_delete",
           detail=f"meeting={meeting_id} session={session_id}", tab="meetings")
    return {"ok": True, "meeting": m}


# ── agendas (now per-session) ─────────────────────────────────────
@router.post("/agenda/add")
def add_agenda(req: AgendaAdd, request: Request):
    me = current_user(request)
    title = (req.title or "").strip()
    if not title:
        raise HTTPException(400, "agenda title required")
    items = _load()
    idx, m = _find(items, req.meeting_id)
    if not m:
        raise HTTPException(404, "meeting not found")
    sidx, s = _find_session(m, req.session_id)
    if sidx < 0:
        raise HTTPException(404, "session not found")
    now = _now()
    issue_ref = _hydrate_tracker_issue_ref(req.issue_ref)
    description = (req.description or "").strip()
    if issue_ref and not description:
        description = _plain_text_from_html(issue_ref.get("description_html") or issue_ref.get("description") or "")
    ag = {
        "id": _new_aid(),
        "title": title,
        "description": description,
        "link": (req.link or "").strip(),
        "owner": (req.owner or me["username"]).strip() or me["username"],
        "issue_ref": issue_ref,
        "created_at": now,
        "updated_at": now,
    }
    result = MEETING_SERVICE.add_agenda(m["id"], s["id"], ag, now)
    if not result.ok:
        raise HTTPException(404, result.error)
    m = result.data["meeting"]
    s = result.data["session"]
    _audit(request, "meetings:agenda_add",
           detail=f"meeting={m['id']} session={s['id']} agenda={ag['id']} title={title[:60]}",
           tab="meetings")
    return {"ok": True, "meeting": m, "session": s, "agenda": ag}


@router.post("/agenda/update")
def update_agenda(req: AgendaUpdate, request: Request):
    me = current_user(request)
    items = _load()
    idx, m = _find(items, req.meeting_id)
    if not m:
        raise HTTPException(404, "meeting not found")
    sidx, s = _find_session(m, req.session_id)
    if sidx < 0:
        raise HTTPException(404, "session not found")
    agendas = s.get("agendas") or []
    aidx = next((i for i, a in enumerate(agendas) if a.get("id") == req.agenda_id), -1)
    if aidx < 0:
        raise HTTPException(404, "agenda not found")
    ag = agendas[aidx]
    if not _can_edit_agenda(me, m, ag):
        raise HTTPException(403, "Only agenda owner / meeting owner / admin can edit")
    changed = []
    for fld in ("title", "description", "link", "owner"):
        v = getattr(req, fld, None)
        if v is None:
            continue
        v = (v or "").strip()
        if fld == "title" and not v:
            raise HTTPException(400, "agenda title cannot be empty")
        if ag.get(fld, "") != v:
            ag[fld] = v
            changed.append(fld)
    if req.issue_ref is not None:
        next_issue_ref = _hydrate_tracker_issue_ref(req.issue_ref)
    else:
        next_issue_ref = None
    if req.issue_ref is not None and ag.get("issue_ref") != next_issue_ref:
        ag["issue_ref"] = next_issue_ref
        changed.append("issue_ref")
    if not changed:
        return {"ok": True, "meeting": m, "session": s, "noop": True}
    updated_at = _now()
    ag["updated_at"] = updated_at
    result = MEETING_SERVICE.update_agenda(m["id"], s["id"], ag["id"], ag, updated_at)
    if not result.ok:
        raise HTTPException(404, result.error)
    m = result.data["meeting"]
    s = result.data["session"]
    ag = result.data["agenda"]
    _audit(request, "meetings:agenda_update",
           detail=f"meeting={m['id']} session={s['id']} agenda={ag['id']} fields={','.join(changed)}",
           tab="meetings")
    return {"ok": True, "meeting": m, "session": s, "agenda": ag}


@router.post("/agenda/delete")
def delete_agenda(
    request: Request,
    meeting_id: str = Query(...),
    session_id: str = Query(...),
    agenda_id: str = Query(...),
):
    me = current_user(request)
    items = _load()
    idx, m = _find(items, meeting_id)
    if not m:
        raise HTTPException(404, "meeting not found")
    sidx, s = _find_session(m, session_id)
    if sidx < 0:
        raise HTTPException(404, "session not found")
    agendas = s.get("agendas") or []
    ag = next((a for a in agendas if a.get("id") == agenda_id), None)
    if not ag:
        raise HTTPException(404, "agenda not found")
    if not _can_edit_agenda(me, m, ag):
        raise HTTPException(403, "Only agenda owner / meeting owner / admin can delete")
    updated_at = _now()
    result = MEETING_SERVICE.delete_agenda(meeting_id, session_id, agenda_id, updated_at)
    if not result.ok:
        raise HTTPException(404, result.error)
    m = result.data["meeting"]
    s = result.data["session"]
    _audit(request, "meetings:agenda_delete",
           detail=f"meeting={meeting_id} session={session_id} agenda={agenda_id}",
           tab="meetings")
    return {"ok": True, "meeting": m, "session": s}


# ── minutes (per-session) ─────────────────────────────────────────
@router.post("/minutes/save")
def save_minutes(req: MinutesSave, request: Request):
    me = current_user(request)
    items = _load()
    idx, m = _find(items, req.meeting_id)
    if not m:
        raise HTTPException(404, "meeting not found")
    if not _can_edit_meeting(me, m):
        raise HTTPException(403, "Only meeting owner or admin can write minutes")
    sidx, s = _find_session(m, req.session_id)
    if sidx < 0:
        raise HTTPException(404, "session not found")
    # v8.8.15: OT-lite revision check — 클라이언트 base_rev 이 서버 rev 과 다르면 conflict.
    #   base_rev 가 None 이면 레거시/저장자 의도적 overwrite 로 간주하고 그대로 진행 (하위호환).
    cur_rev = int(((s.get("minutes") or {}).get("rev")) or 0)
    if req.base_rev is not None and int(req.base_rev) != cur_rev:
        cur_min = s.get("minutes") or {}
        raise HTTPException(status_code=409, detail={
            "code": "minutes_rev_conflict",
            "message": "다른 사용자가 회의록을 수정했습니다. 최신 내용을 불러와 재시도하세요.",
            "server_rev": cur_rev,
            "client_rev": int(req.base_rev or 0),
            "current_body": cur_min.get("body") or "",
            "current_author": cur_min.get("author") or "",
            "current_updated_at": cur_min.get("updated_at") or "",
        })
    now = _now()
    # v8.7.5: decisions 는 {id,text,due} 객체 list 로 유지. 기존 calendar 상태 보존.
    prev_dec = ((s.get("minutes") or {}).get("decisions")) or []
    prev_dec_by_id = {d.get("id"): d for d in prev_dec if isinstance(d, dict) and d.get("id")}
    new_dec = _ensure_decision_objects(req.decisions or [])
    # inherit calendar_pushed state from prev by id
    for d in new_dec:
        pv = prev_dec_by_id.get(d["id"]) or {}
        if pv:
            d["calendar_pushed"] = bool(pv.get("calendar_pushed"))
            d["calendar_event_id"] = pv.get("calendar_event_id") or ""
            d["calendar_pushed_by"] = pv.get("calendar_pushed_by") or ""
            d["calendar_pushed_at"] = pv.get("calendar_pushed_at") or ""
    # decisions removed by this save → unpush calendar events
    kept_dids = {d["id"] for d in new_dec}
    for old in prev_dec:
        if isinstance(old, dict) and old.get("id") not in kept_dids and old.get("calendar_pushed"):
            try:
                from routers.calendar import unpush_action_item
                unpush_action_item(m["id"], s["id"], old["id"])
            except Exception:
                pass
    decisions = new_dec
    ai_clean = []
    for ai in (req.action_items or []):
        text = (ai.text or "").strip() if hasattr(ai, "text") else ""
        if not text:
            continue
        gids = getattr(ai, "group_ids", None) or []
        ai_clean.append({
            "id": (getattr(ai, "id", "") or "").strip(),
            "text": text,
            "owner": (getattr(ai, "owner", "") or "").strip(),
            "due": (getattr(ai, "due", "") or "").strip(),
            "group_ids": [str(g).strip() for g in gids if g and str(g).strip()],
        })
    # v8.7.9: Preserve ids across saves by explicit id OR text match — prevents calendar churn
    prev_ai = ((s.get("minutes") or {}).get("action_items")) or []
    prev_by_id = {a.get("id"): a for a in prev_ai if isinstance(a, dict) and a.get("id")}
    prev_by_text = {(a.get("text") or "").strip(): a for a in prev_ai if isinstance(a, dict)}
    merged = []
    for ai in ai_clean:
        aid = ai.get("id") or ""
        if not aid:
            tmatch = prev_by_text.get(ai["text"])
            if tmatch and tmatch.get("id"):
                aid = tmatch["id"]
        if not aid:
            aid = f"ai_{uuid.uuid4().hex[:8]}"
        prev = prev_by_id.get(aid) or {}
        merged.append({
            "id": aid,
            "text": ai["text"], "owner": ai["owner"], "due": ai["due"],
            "group_ids": ai.get("group_ids") or [],
            "status": prev.get("status", "pending"),
            "calendar_pushed": bool(prev.get("calendar_pushed")),
            "calendar_event_id": prev.get("calendar_event_id") or "",
            "calendar_pushed_by": prev.get("calendar_pushed_by") or "",
            "calendar_pushed_at": prev.get("calendar_pushed_at") or "",
        })
    # Any previously-pushed action_items removed by this save → unpush & drop calendar event
    kept_ids = {a["id"] for a in merged}
    for old in prev_ai:
        if isinstance(old, dict) and old.get("id") not in kept_ids and old.get("calendar_pushed"):
            try:
                from routers.calendar import unpush_action_item
                unpush_action_item(m["id"], s["id"], old["id"])
            except Exception:
                pass
    # v8.8.13: body_appendix 보존 (그룹 멤버가 공동으로 append 한 항목은 owner 저장 때도 유지).
    prev_appendix = ((s.get("minutes") or {}).get("body_appendix")) or []
    # v8.8.15: rev counter 증분.
    new_rev = cur_rev + 1
    s["minutes"] = {
        "body": (req.body or "").strip(),
        "decisions": decisions,
        "action_items": merged,
        "author": me["username"],
        "updated_at": now,
        "body_appendix": prev_appendix,
        "rev": new_rev,
    }
    s["minutes"]["decisions"] = decisions
    s["minutes"]["action_items"] = merged
    # v8.7.9: auto-sync ALL decisions + action_items to calendar (no manual push 필요).
    #   - decisions → single-day event on session date (filled style)
    #   - action_items → range event from session date → due (outline style)
    # Only mark calendar_pushed=True after successful sync; log errors loudly.
    sync_result = {"created": 0, "updated": 0, "removed": 0, "ok": False, "error": ""}
    try:
        from routers.calendar import sync_session_to_calendar
        sync_result = sync_session_to_calendar(m, s, actor=me["username"]) or sync_result
        sync_result["ok"] = True
        for d in decisions:
            d["calendar_pushed"] = True
        for ai in merged:
            if (ai.get("due") or "").strip():
                ai["calendar_pushed"] = True
    except Exception as ex:
        import traceback
        sync_result["ok"] = False
        sync_result["error"] = f"{type(ex).__name__}: {ex}"
        try:
            print("[meetings.save_minutes] calendar sync FAILED:",
                  sync_result["error"], traceback.format_exc()[:800], flush=True)
        except Exception:
            pass
    if (s.get("status") or "scheduled") not in ("completed", "cancelled"):
        s["status"] = "completed"
    s["updated_at"] = now
    m["sessions"][sidx] = s
    m["updated_at"] = now
    items[idx] = m
    _save(items)
    _audit(request, "meetings:minutes",
           detail=f"meeting={m['id']} session={s['id']} decisions={len(decisions)} actions={len(merged)}",
           tab="meetings")

    # v8.8.6: 동시편집 broadcast — 다른 subscribers 에게 변경 알림.
    # v8.8.15: payload 에 rev 포함 → FE 가 자기 local rev 과 비교해 dirty 없으면 auto-refresh, 있으면 banner.
    _mtg_publish(m["id"], {
        "type": "minutes_saved",
        "meeting_id": m["id"], "session_id": s["id"],
        "author": me["username"], "at": now,
        "decisions": len(decisions), "actions": len(merged),
        "rev": new_rev,
    })

    # v8.7.6: 저장 직후 메일 발송 (옵션). action_items.group_ids 멤버·직접 유저·그룹·이메일 병합.
    mail_result = None
    if req.send_mail:
        to_addrs: List[str] = []
        for em in (req.mail_to or []):
            if em and "@" in em:
                to_addrs.append(em)
        to_addrs += _resolve_users_to_emails(list(req.mail_to_users or []))
        # ActionItem 당 group_ids 멤버 이메일도 수신자에 추가
        gids_collected: set = set()
        for ai in merged:
            for gid in (ai.get("group_ids") or []):
                gids_collected.add(gid)
        to_addrs += _resolve_group_members_to_emails(list(gids_collected))
        # v8.7.7: 신규 mail_groups (모든 유저 공유) 기반 수신자
        to_addrs += _resolve_mail_group_ids_to_emails(list(req.mail_group_ids or []))
        # admin 측 recipient_groups (username 또는 email list) 지원
        cfg_rg = (_load_mail_cfg().get("recipient_groups") or {})
        if isinstance(cfg_rg, dict):
            for gname in (req.mail_groups or []):
                members = cfg_rg.get(gname) or []
                if isinstance(members, list):
                    for em in members:
                        em = str(em).strip()
                        if em and "@" in em:
                            to_addrs.append(em)
        subject = (req.mail_subject or "").strip()
        mail_result = _send_minutes_mail(m, s, to_addrs=to_addrs, subject=subject,
                                          actor=me["username"],
                                          mail_body=(req.mail_body or ""))
        _audit(request, "meetings:minutes_mail",
               detail=f"meeting={m['id']} session={s['id']} ok={mail_result.get('ok')} n={len(to_addrs)}",
               tab="meetings")

    return {"ok": True, "meeting": m, "session": s, "mail": mail_result, "calendar_sync": sync_result, "rev": new_rev}


# v8.8.13: 공동 본문 append ─────────────────────────────────────
@router.post("/minutes/append")
def append_minutes(req: MinutesAppendReq, request: Request):
    """회의록 본문 append. owner/admin 또는 회의 공개 그룹 멤버 모두 가능.
    append 된 블록은 {id, author, at, text} 로 기록되어 본문 아래에 자신의 이름과 함께 노출된다."""
    me = current_user(request)
    items = _load()
    idx, m = _find(items, req.meeting_id)
    if not m:
        raise HTTPException(404, "meeting not found")
    if not _can_append_minutes(me, m):
        raise HTTPException(403, "이 회의에 본문을 추가할 권한이 없습니다 (그룹 멤버만).")
    sidx, s = _find_session(m, req.session_id)
    if sidx < 0:
        raise HTTPException(404, "session not found")
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(400, "text is required")
    now = _now()
    minutes = s.get("minutes") or {}
    appendix = list(minutes.get("body_appendix") or [])
    entry = {
        "id": f"bap_{uuid.uuid4().hex[:8]}",
        "author": me["username"],
        "at": now,
        "text": text,
    }
    appendix.append(entry)
    minutes["body_appendix"] = appendix
    # body 등 다른 필드가 없으면 기본값으로 채움 (담당자가 한 번도 저장 안 한 경우 대비).
    if "body" not in minutes: minutes["body"] = ""
    if "decisions" not in minutes: minutes["decisions"] = []
    if "action_items" not in minutes: minutes["action_items"] = []
    minutes["updated_at"] = now
    # v8.8.15: append 도 rev 증분 — body_appendix 변경 역시 동시편집자에게 중요 이벤트.
    minutes["rev"] = int(minutes.get("rev") or 0) + 1
    s["minutes"] = minutes
    s["updated_at"] = now
    m["sessions"][sidx] = s
    m["updated_at"] = now
    items[idx] = m
    _save(items)
    _audit(request, "meetings:minutes_append",
           detail=f"meeting={m['id']} session={s['id']} by={me['username']}",
           tab="meetings")
    _mtg_publish(m["id"], {
        "type": "minutes_appended",
        "meeting_id": m["id"], "session_id": s["id"],
        "author": me["username"], "at": now, "append_id": entry["id"],
        "rev": minutes["rev"],
    })
    # v8.8.33: 회의 주관자/owner 에게 "내 회의에 회의록 추가됨" 알림.
    try:
        from core.notify import emit_event
        targets = set()
        # owner / organizer / chair / creator 중 존재하는 필드 수집.
        for field in ("owner", "organizer", "chair", "created_by", "username"):
            v = m.get(field)
            if isinstance(v, str) and v:
                targets.add(v)
        # session 수준 담당자도 포함
        for field in ("owner", "organizer", "chair"):
            v = s.get(field)
            if isinstance(v, str) and v:
                targets.add(v)
        preview = (text or "")[:80]
        for tgt in targets:
            emit_event(
                "my_meeting_minutes_added",
                actor=me["username"],
                target_user=tgt,
                title=f"[회의록 갱신] {m.get('title') or m.get('id')}",
                body=f"{me['username']} · {preview}",
                payload={"meeting_id": m["id"], "session_id": s["id"], "text": preview},
            )
    except Exception:
        pass
    return {"ok": True, "entry": entry, "session": s}


@router.post("/minutes/append/delete")
def delete_minutes_append(req: MinutesAppendDeleteReq, request: Request):
    """append 블록 삭제. owner/admin 또는 해당 블록의 작성자 본인."""
    me = current_user(request)
    items = _load()
    idx, m = _find(items, req.meeting_id)
    if not m:
        raise HTTPException(404, "meeting not found")
    sidx, s = _find_session(m, req.session_id)
    if sidx < 0:
        raise HTTPException(404, "session not found")
    minutes = s.get("minutes") or {}
    appendix = list(minutes.get("body_appendix") or [])
    target = next((a for a in appendix if a.get("id") == req.append_id), None)
    if not target:
        raise HTTPException(404, "append entry not found")
    is_owner_or_admin = _can_edit_meeting(me, m)
    is_author = target.get("author") == me["username"]
    if not (is_owner_or_admin or is_author):
        raise HTTPException(403, "작성자 본인 또는 회의 담당자(admin)만 삭제할 수 있습니다.")
    appendix = [a for a in appendix if a.get("id") != req.append_id]
    minutes["body_appendix"] = appendix
    minutes["updated_at"] = _now()
    minutes["rev"] = int(minutes.get("rev") or 0) + 1
    s["minutes"] = minutes
    s["updated_at"] = _now()
    m["sessions"][sidx] = s
    items[idx] = m
    _save(items)
    _audit(request, "meetings:minutes_append_delete",
           detail=f"meeting={m['id']} session={s['id']} append_id={req.append_id} by={me['username']}",
           tab="meetings")
    _mtg_publish(m["id"], {
        "type": "minutes_append_deleted",
        "meeting_id": m["id"], "session_id": s["id"],
        "by": me["username"], "append_id": req.append_id,
        "rev": minutes["rev"],
    })
    return {"ok": True, "session": s}


# ── action_item ↔ calendar push/unpush ─────────────────────────
class ActionPushReq(BaseModel):
    meeting_id: str
    session_id: str
    action_item_id: str


@router.post("/action/push")
def push_action(req: ActionPushReq, request: Request):
    me = current_user(request)
    items = _load()
    midx, m = _find(items, req.meeting_id)
    if midx < 0 or not m:
        raise HTTPException(404, "meeting not found")
    sidx, s = _find_session(m, req.session_id)
    if sidx < 0:
        raise HTTPException(404, "session not found")
    ai_list = ((s.get("minutes") or {}).get("action_items")) or []
    ai = next((x for x in ai_list if isinstance(x, dict) and x.get("id") == req.action_item_id), None)
    if ai is None:
        raise HTTPException(404, "action_item not found")
    if not (ai.get("text") or "").strip() or not (ai.get("due") or "").strip():
        raise HTTPException(400, "action_item must have both text and due date to push")
    from routers.calendar import push_action_item
    ev = push_action_item(m, s, ai, actor=me["username"],
                          meeting_category=m.get("category") or "")
    if not ev:
        raise HTTPException(400, "calendar event could not be created")
    now = _now()
    ai["calendar_pushed"] = True
    ai["calendar_event_id"] = ev["id"]
    ai["calendar_pushed_by"] = me["username"]
    ai["calendar_pushed_at"] = now
    s["minutes"]["action_items"] = ai_list
    s["updated_at"] = now
    m["sessions"][sidx] = s
    m["updated_at"] = now
    items[midx] = m
    _save(items)
    _audit(request, "meetings:action_push",
           detail=f"meeting={m['id']} session={s['id']} ai={ai['id']} event={ev['id']}",
           tab="meetings")
    return {"ok": True, "meeting": m, "session": s, "event": ev}


# ── decision ↔ calendar push/unpush (v8.7.5) ─────────────
class DecisionPushReq(BaseModel):
    meeting_id: str
    session_id: str
    decision_id: str
    due: Optional[str] = ""  # YYYY-MM-DD; if empty, fallback to session scheduled_at or today


@router.post("/decision/push")
def push_decision(req: DecisionPushReq, request: Request):
    me = current_user(request)
    items = _load()
    midx, m = _find(items, req.meeting_id)
    if midx < 0 or not m:
        raise HTTPException(404, "meeting not found")
    sidx, s = _find_session(m, req.session_id)
    if sidx < 0:
        raise HTTPException(404, "session not found")
    minutes = s.get("minutes") or {}
    dec_list = minutes.get("decisions") or []
    # 다시 한 번 객체화 (문자열 형태로 저장된 legacy 대비)
    dec_list = _ensure_decision_objects(dec_list)
    target = next((d for d in dec_list if d.get("id") == req.decision_id), None)
    if target is None:
        raise HTTPException(404, "decision not found")
    due = (req.due or target.get("due") or "").strip()
    if not due:
        # fallback: session scheduled_at (date 부분) 또는 오늘
        sa = (s.get("scheduled_at") or "")[:10]
        due = sa or datetime.date.today().isoformat()
    from routers.calendar import push_action_item
    # action_item 과 동일한 함수 재사용 — id 는 decision_id 를 그대로 사용.
    synthetic = {"id": target["id"], "text": "[결정] " + (target.get("text") or ""),
                 "owner": "", "due": due}
    ev = push_action_item(m, s, synthetic, actor=me["username"],
                          meeting_category=m.get("category") or "")
    if not ev:
        raise HTTPException(400, "calendar event could not be created")
    target["calendar_pushed"] = True
    target["calendar_event_id"] = ev["id"]
    target["calendar_pushed_by"] = me["username"]
    target["calendar_pushed_at"] = _now()
    target["due"] = due
    # replace in list
    dec_list = [target if d.get("id") == target["id"] else d for d in dec_list]
    minutes["decisions"] = dec_list
    s["minutes"] = minutes
    s["updated_at"] = _now()
    m["sessions"][sidx] = s
    m["updated_at"] = s["updated_at"]
    items[midx] = m
    _save(items)
    _audit(request, "meetings:decision_push",
           detail=f"meeting={m['id']} session={s['id']} dec={target['id']}",
           tab="meetings")
    return {"ok": True, "meeting": m, "session": s, "event": ev}


@router.post("/decision/unpush")
def unpush_decision(req: DecisionPushReq, request: Request):
    me = current_user(request)
    items = _load()
    midx, m = _find(items, req.meeting_id)
    if midx < 0 or not m:
        raise HTTPException(404, "meeting not found")
    sidx, s = _find_session(m, req.session_id)
    if sidx < 0:
        raise HTTPException(404, "session not found")
    minutes = s.get("minutes") or {}
    dec_list = _ensure_decision_objects(minutes.get("decisions") or [])
    target = next((d for d in dec_list if d.get("id") == req.decision_id), None)
    if target is None:
        raise HTTPException(404, "decision not found")
    from routers.calendar import unpush_action_item
    unpush_action_item(m["id"], s["id"], target["id"])
    target["calendar_pushed"] = False
    target["calendar_event_id"] = ""
    dec_list = [target if d.get("id") == target["id"] else d for d in dec_list]
    minutes["decisions"] = dec_list
    s["minutes"] = minutes
    s["updated_at"] = _now()
    m["sessions"][sidx] = s
    m["updated_at"] = s["updated_at"]
    items[midx] = m
    _save(items)
    _audit(request, "meetings:decision_unpush",
           detail=f"meeting={m['id']} session={s['id']} dec={target['id']}",
           tab="meetings")
    return {"ok": True, "meeting": m, "session": s}


@router.post("/action/unpush")
def unpush_action(req: ActionPushReq, request: Request):
    me = current_user(request)
    items = _load()
    midx, m = _find(items, req.meeting_id)
    if midx < 0 or not m:
        raise HTTPException(404, "meeting not found")
    sidx, s = _find_session(m, req.session_id)
    if sidx < 0:
        raise HTTPException(404, "session not found")
    ai_list = ((s.get("minutes") or {}).get("action_items")) or []
    ai = next((x for x in ai_list if isinstance(x, dict) and x.get("id") == req.action_item_id), None)
    if ai is None:
        raise HTTPException(404, "action_item not found")
    from routers.calendar import unpush_action_item
    unpush_action_item(m["id"], s["id"], ai["id"])
    now = _now()
    ai["calendar_pushed"] = False
    ai["calendar_event_id"] = ""
    s["minutes"]["action_items"] = ai_list
    s["updated_at"] = now
    m["sessions"][sidx] = s
    m["updated_at"] = now
    items[midx] = m
    _save(items)
    _audit(request, "meetings:action_unpush",
           detail=f"meeting={m['id']} session={s['id']} ai={ai['id']}",
           tab="meetings")
    return {"ok": True, "meeting": m, "session": s}


# v8.7.7: 차수별 독립 메일 발송 (회의록 저장 분리 — 이미 저장된 차수를 그냥 다시 보내고 싶을 때).
class SessionSendMailReq(BaseModel):
    meeting_id: str
    session_id: str
    mail_group_ids: Optional[List[str]] = None   # mail_groups.json id 목록
    mail_to_users: Optional[List[str]] = None    # 개별 username
    mail_to: Optional[List[str]] = None          # 직접 이메일
    mail_subject: Optional[str] = ""
    # v8.8.16: 메일 전용 본문 (공동 작성된 minutes.body 와 분리).
    mail_body: Optional[str] = ""


@router.post("/session/send-mail")
def session_send_mail(req: SessionSendMailReq, request: Request):
    me = current_user(request)
    items = _load()
    midx, m = _find(items, req.meeting_id)
    if midx < 0 or not m:
        raise HTTPException(404, "meeting not found")
    if not _can_edit_meeting(me, m):
        raise HTTPException(403, "Only meeting owner or admin can send session mail")
    sidx, s = _find_session(m, req.session_id)
    if sidx < 0:
        raise HTTPException(404, "session not found")
    to_addrs: List[str] = []
    for em in (req.mail_to or []):
        if em and "@" in em:
            to_addrs.append(em)
    to_addrs += _resolve_users_to_emails(list(req.mail_to_users or []))
    to_addrs += _resolve_mail_group_ids_to_emails(list(req.mail_group_ids or []))
    subject = (req.mail_subject or "").strip()
    result = _send_minutes_mail(m, s, to_addrs=to_addrs, subject=subject,
                                actor=me["username"],
                                mail_body=(req.mail_body or ""))
    _audit(request, "meetings:session_send_mail",
           detail=f"meeting={m['id']} session={s['id']} ok={result.get('ok')} n={len(to_addrs)}",
           tab="meetings")
    return {"ok": bool(result.get("ok")), "mail": result}


# v8.8.6: 회의록 동시편집 — SSE 스트림. 브라우저 EventSource 는 커스텀 헤더 불가 →
# `?t=<session_token>` fallback 을 app.py `_QUERY_TOKEN_PREFIXES` 에서 허용함.
@router.get("/stream")
async def stream_minutes(request: Request, meeting_id: str = Query(...)):
    me = current_user(request)
    items = _load()
    _, m = _find(items, meeting_id)
    if not m:
        raise HTTPException(404, "meeting not found")
    if not _meeting_visible(m, me):
        raise HTTPException(403, "not visible")

    async def _gen():
        q = await _mtg_subscribe(meeting_id)
        try:
            yield f"event: hello\ndata: {_json.dumps({'meeting_id': meeting_id, 'viewer': me['username']})}\n\n"
            while True:
                try:
                    payload = await _asyncio.wait_for(q.get(), timeout=25.0)
                    yield f"event: update\ndata: {_json.dumps(payload)}\n\n"
                except _asyncio.TimeoutError:
                    # keep-alive ping (25s) — proxy 중간 끊김 방지.
                    yield "event: ping\ndata: {}\n\n"
                if await request.is_disconnected():
                    break
        finally:
            await _mtg_unsubscribe(meeting_id, q)

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
