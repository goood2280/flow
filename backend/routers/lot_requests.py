"""Lot assignment / handling request board.

The board is intentionally separate from Tracker: every request, response and
status transition has its own author/timestamp trail.  Content ownership is
strict (even a global admin cannot edit somebody else's text); processing
status is controlled by the delegated ``lotrequest`` page managers.
"""
from __future__ import annotations

import copy
import datetime as dt
import base64
import html
import mimetypes
import re
import threading
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from core.auth import current_user, is_page_manager, parse_tab_tokens
from core.paths import PATHS
from core.rich_text import rich_text_has_content, sanitize_rich_html
from core.utils import jsonl_append, load_json, save_json


router = APIRouter(prefix="/api/lot-requests", tags=["lot-requests"])

STORE_DIR = PATHS.data_root / "lot_requests"
REQUESTS_FILE = STORE_DIR / "requests.json"
AUDIT_FILE = STORE_DIR / "audit.jsonl"
CONFIG_FILE = STORE_DIR / "config.json"
UPLOADS_DIR = STORE_DIR / "uploads"
STORE_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
_LOCK = threading.RLock()

REQUEST_TYPES = {"lot_assignment", "hot_grade", "other"}
STATUSES = {"registered", "completed", "rejected"}
PRIORITIES = {"normal", "high", "urgent"}
DEFAULT_REQUEST_TYPES = ["랏 배정", "Hot grade", "기타 요청"]
# 요청한 팀은 기본값을 두지 않는다 — 관리자가 톱니바퀴에서 등록한 목록이 유일한 출처다.
LEGACY_REQUEST_TYPES = {"lot_assignment": "랏 배정", "hot_grade": "Hot grade", "other": "기타 요청"}


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _clean(value: object, *, max_len: int = 4000) -> str:
    return str(value or "").strip()[:max_len]


def _required(value: object, label: str, *, max_len: int) -> str:
    cleaned = _clean(value, max_len=max_len)
    if not cleaned:
        raise HTTPException(400, f"{label}을(를) 입력해주세요")
    return cleaned


def _required_rich(value: object, label: str) -> str:
    cleaned = sanitize_rich_html(value)
    if not rich_text_has_content(cleaned):
        raise HTTPException(400, f"{label}을(를) 입력해주세요")
    return cleaned


def _load() -> list[dict]:
    raw = load_json(REQUESTS_FILE, [])
    return raw if isinstance(raw, list) else []


def _effective_status(value: object) -> str:
    """Legacy in-progress requests return to registered; their history remains intact."""
    status = str(value or "registered")
    return "registered" if status == "in_progress" else status


def _save(rows: list[dict]) -> None:
    save_json(REQUESTS_FILE, rows, indent=2)


def _config() -> dict:
    raw = load_json(CONFIG_FILE, {})
    raw = raw if isinstance(raw, dict) else {}
    request_types = list(dict.fromkeys(_clean(value, max_len=80) for value in raw.get("request_types", []) if _clean(value, max_len=80)))
    request_teams = list(dict.fromkeys(_clean(value, max_len=120) for value in raw.get("request_teams", []) if _clean(value, max_len=120)))
    return {
        "request_types": request_types or list(DEFAULT_REQUEST_TYPES),
        "request_teams": request_teams,
    }


def _audit(action: str, actor: str, request_id: str, **detail) -> None:
    jsonl_append(AUDIT_FILE, {
        "at": _now(), "action": action, "actor": actor,
        "request_id": request_id, **detail,
    }, add_timestamp=False)


def _activity(item: dict, action: str, actor: str, *, note: str = "", **detail) -> None:
    item.setdefault("activity_history", []).append({
        "at": _now(), "action": action, "actor": actor,
        "note": _clean(note, max_len=2000), **detail,
    })


def _user(request: Request) -> dict:
    user = current_user(request)
    if user.get("role") == "admin" or is_page_manager(user, "lotrequest"):
        return user
    tabs, _ = parse_tab_tokens(user.get("tabs", ""))
    if "lotrequest" not in tabs:
        raise HTTPException(403, "랏 배정/요청 탭 권한이 필요합니다")
    return user


def _can_process(user: dict) -> bool:
    """Replies, mail and status transitions: lotrequest page delegates + global admins.

    Until v9.5.x this used ``is_page_admin`` (username only), which cannot see the
    ``admin`` role — so a global admin could open the board but not answer a single
    request.  ``is_page_manager`` covers both.
    """
    return is_page_manager(user or {}, "lotrequest")


def _require_processor(user: dict) -> None:
    if not _can_process(user):
        raise HTTPException(403, "랏 배정/요청 페이지 위임자와 관리자만 처리할 수 있습니다")


def _find(rows: list[dict], request_id: str) -> dict:
    item = next((row for row in rows if row.get("id") == request_id and not row.get("deleted_at")), None)
    if not item:
        raise HTTPException(404, "요청을 찾을 수 없습니다")
    return item


def _owner(item: dict, username: str, noun: str = "요청") -> None:
    if str(item.get("author") or "") != username:
        raise HTTPException(403, f"{noun} 작성자만 수정하거나 삭제할 수 있습니다")


def _public(item: dict, user: dict, *, detail: bool = False) -> dict:
    out = copy.deepcopy(item)
    out["status"] = _effective_status(out.get("status"))
    responses = out.get("responses") if isinstance(out.get("responses"), list) else []
    out["response_count"] = len(responses)
    out["latest_response_at"] = responses[-1].get("created_at", "") if responses else ""
    if not detail:
        out.pop("responses", None)
        out.pop("edit_history", None)
    mine = str(out.get("author") or "") == str(user.get("username") or "")
    out["permissions"] = {
        "can_edit": mine,
        "can_delete": mine,
        "can_respond": _can_process(user),
        "can_mail": _can_process(user),
        "can_process": _can_process(user),
    }
    if detail:
        activity = out.get("activity_history") if isinstance(out.get("activity_history"), list) else []
        if not activity:
            activity = [{"at": out.get("created_at", ""), "action": "request_created",
                         "actor": out.get("author", ""), "note": "요청 등록"}]
            for edit in out.get("edit_history") or []:
                activity.append({"at": edit.get("at", ""), "action": "request_updated",
                                 "actor": edit.get("actor", ""), "note": "요청 내용 수정"})
            for event in out.get("status_history") or []:
                if event.get("from"):
                    activity.append({"at": event.get("at", ""), "action": "status_changed",
                                     "actor": event.get("actor", ""), "note": event.get("note", ""),
                                     "from": event.get("from", ""), "to": event.get("to", "")})
            for response in responses:
                activity.append({"at": response.get("created_at", ""), "action": "response_created",
                                 "actor": response.get("author", ""), "note": response.get("assigned_lots", "")})
                for edit in response.get("edit_history") or []:
                    activity.append({"at": edit.get("at", ""), "action": "response_updated",
                                     "actor": edit.get("actor", response.get("author", "")), "note": "처리 답변 수정"})
        out["activity_history"] = list(reversed(sorted(activity, key=lambda event: str(event.get("at") or ""))))
        for response in responses:
            response["permissions"] = {
                "can_edit": _can_process(user) and response.get("author") == user.get("username"),
                "can_delete": _can_process(user) and response.get("author") == user.get("username"),
            }
    return out


class RequestCreate(BaseModel):
    request_type: str = Field(default="랏 배정", min_length=1, max_length=80)
    product: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=240)
    details: str = Field(min_length=1, max_length=200000)
    target_lots: str = Field(default="", max_length=4000)
    requester_team: str = Field(default="", max_length=120)
    priority: Literal["normal", "high", "urgent"] = "normal"


class RequestUpdate(BaseModel):
    request_type: str = Field(min_length=1, max_length=80)
    product: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=240)
    details: str = Field(min_length=1, max_length=200000)
    # 화면에서 없어진 칸이라 요청 본문에 담기지 않는다. 그때는 기존 값을 지우지 않고
    # 그대로 둔다 — 예전 요청을 수정하는 것만으로 대상 랏 기록이 사라지면 안 된다.
    target_lots: str | None = Field(default=None, max_length=4000)
    requester_team: str = Field(default="", max_length=120)
    priority: Literal["normal", "high", "urgent"] = "normal"


class StatusUpdate(BaseModel):
    status: Literal["registered", "completed", "rejected"]
    note: str = Field(default="", max_length=2000)


class ResponseWrite(BaseModel):
    body: str = Field(min_length=1, max_length=200000)
    # 답글 화면에서 없어진 칸이다. target_lots 와 같은 규칙 — 안 오면 기존 값을 둔다.
    assigned_lots: str | None = Field(default=None, max_length=4000)


class MailWrite(BaseModel):
    to_users: list[str] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)
    extra_emails: list[str] = Field(default_factory=list)
    subject: str = Field(default="", max_length=500)
    body: str = Field(default="", max_length=20000)


class ConfigWrite(BaseModel):
    request_types: list[str] = Field(default_factory=list, max_length=50)
    request_teams: list[str] = Field(default_factory=list, max_length=100)


@router.get("")
def list_requests(
    request: Request,
    status: str = Query(""), request_type: str = Query(""),
    product: str = Query(""), requester_team: str = Query(""), author: str = Query(""),
    q: str = Query(""), mine: bool = Query(False),
):
    user = _user(request)
    username = user.get("username") or ""
    query = q.strip().lower()
    with _LOCK:
        rows = [row for row in _load() if isinstance(row, dict) and not row.get("deleted_at")]
        if status in STATUSES:
            rows = [row for row in rows if _effective_status(row.get("status")) == status]
        if request_type in REQUEST_TYPES:
            rows = [row for row in rows if row.get("request_type") == request_type]
        elif request_type.strip():
            wanted = request_type.strip().casefold()
            rows = [row for row in rows if LEGACY_REQUEST_TYPES.get(str(row.get("request_type") or ""), str(row.get("request_type") or "")).casefold() == wanted]
        if product.strip():
            wanted_product = product.strip().casefold()
            rows = [row for row in rows if str(row.get("product") or "").strip().casefold() == wanted_product]
        if requester_team.strip():
            wanted_team = requester_team.strip().casefold()
            rows = [row for row in rows if str(row.get("requester_team") or "").strip().casefold() == wanted_team]
        if author.strip():
            wanted_author = author.strip().casefold()
            rows = [row for row in rows if str(row.get("author") or "").strip().casefold() == wanted_author]
        if mine:
            rows = [row for row in rows if row.get("author") == username]
        if query:
            fields = ("title", "details", "product", "target_lots", "author", "requester_team")
            rows = [row for row in rows if any(query in str(row.get(key) or "").lower() for key in fields)]
        rows.sort(key=lambda row: (row.get("updated_at") or row.get("created_at") or ""), reverse=True)
        return {"requests": [_public(row, user) for row in rows], "total": len(rows),
                "can_process": _can_process(user)}


@router.get("/stats")
def request_stats(
    request: Request, product: str = Query(""), request_type: str = Query(""),
    requester_team: str = Query(""), author: str = Query(""), q: str = Query(""),
    mine: bool = Query(False),
):
    user = _user(request)
    with _LOCK:
        all_rows = [row for row in _load() if isinstance(row, dict) and not row.get("deleted_at")]
    rows = list(all_rows)
    if product.strip():
        product_key = product.strip().casefold()
        rows = [row for row in rows if str(row.get("product") or "").strip().casefold() == product_key]
    if request_type.strip():
        if request_type in REQUEST_TYPES:
            rows = [row for row in rows if row.get("request_type") == request_type]
        else:
            type_key = request_type.strip().casefold()
            rows = [row for row in rows if LEGACY_REQUEST_TYPES.get(str(row.get("request_type") or ""), str(row.get("request_type") or "")).casefold() == type_key]
    if requester_team.strip():
        team_key = requester_team.strip().casefold()
        rows = [row for row in rows if str(row.get("requester_team") or "").strip().casefold() == team_key]
    if author.strip():
        author_key = author.strip().casefold()
        rows = [row for row in rows if str(row.get("author") or "").strip().casefold() == author_key]
    if mine:
        rows = [row for row in rows if row.get("author") == user.get("username")]
    if q.strip():
        query = q.strip().casefold()
        fields = ("title", "details", "product", "target_lots", "author", "requester_team")
        rows = [row for row in rows if any(query in str(row.get(key) or "").casefold() for key in fields)]
    counts = {status: 0 for status in STATUSES}
    for row in rows:
        effective = _effective_status(row.get("status"))
        if effective in counts:
            counts[effective] += 1
    authors = {}
    for row in all_rows:
        username = str(row.get("author") or "").strip()
        if username:
            authors[username] = str(row.get("author_name") or "").strip()
    return {"total": len(rows), "status": counts,
            "mine": len([row for row in rows if row.get("author") == user.get("username")]),
            "facets": {
                "products": sorted({str(row.get("product") or "").strip() for row in all_rows if str(row.get("product") or "").strip()}),
                "requester_teams": sorted({str(row.get("requester_team") or "").strip() for row in all_rows if str(row.get("requester_team") or "").strip()}),
                "authors": [{"username": username, "name": authors[username]} for username in sorted(authors)],
            }}


@router.get("/config")
def get_board_config(request: Request):
    user = _user(request)
    return {**_config(), "can_edit": bool(is_page_manager(user, "lotrequest"))}


@router.post("/config")
def save_board_config(payload: ConfigWrite, request: Request):
    user = _user(request)
    if not is_page_manager(user, "lotrequest"):
        raise HTTPException(403, "랏 배정/요청 페이지 관리자만 설정을 변경할 수 있습니다")
    request_types = list(dict.fromkeys(_clean(value, max_len=80) for value in payload.request_types if _clean(value, max_len=80)))
    request_teams = list(dict.fromkeys(_clean(value, max_len=120) for value in payload.request_teams if _clean(value, max_len=120)))
    if not request_types:
        raise HTTPException(400, "요청 유형을 한 개 이상 등록해주세요")
    if not request_teams:
        raise HTTPException(400, "요청한 팀을 한 개 이상 등록해주세요")
    config = {"request_types": request_types, "request_teams": request_teams,
              "updated_at": _now(), "updated_by": user.get("username") or ""}
    save_json(CONFIG_FILE, config, indent=2)
    _audit("config_updated", user.get("username") or "", "", request_types=len(request_types), request_teams=len(request_teams))
    return {**config, "can_edit": True}


@router.get("/{request_id}")
def get_request(request_id: str, request: Request):
    user = _user(request)
    with _LOCK:
        return _public(_find(_load(), request_id), user, detail=True)


@router.post("")
def create_request(payload: RequestCreate, request: Request):
    user = _user(request)
    actor = user.get("username") or ""
    now = _now()
    item = {
        "id": uuid.uuid4().hex,
        "request_type": _required(payload.request_type, "요청 유형", max_len=80),
        "product": _required(payload.product, "제품", max_len=120),
        "title": _required(payload.title, "제목", max_len=240),
        "details": _required_rich(payload.details, "상세 요청"),
        "target_lots": _clean(payload.target_lots),
        "requester_team": _clean(payload.requester_team, max_len=120),
        "priority": payload.priority,
        "status": "registered",
        "author": actor,
        "author_name": _clean(user.get("name"), max_len=120),
        "created_at": now,
        "updated_at": now,
        "processed_at": "",
        "processed_by": "",
        "status_history": [{"from": "", "to": "registered", "actor": actor, "at": now, "note": "요청 등록"}],
        "edit_history": [],
        "activity_history": [{"at": now, "action": "request_created", "actor": actor, "note": "요청 등록"}],
        "mail_history": [],
        "responses": [],
    }
    with _LOCK:
        rows = _load()
        rows.append(item)
        _save(rows)
        _audit("request_created", actor, item["id"], status="registered")
    _notify("lot_request_created", actor, "", item, f"새 요청 · {item['product']}")
    return _public(item, user, detail=True)


@router.put("/{request_id}")
def update_request(request_id: str, payload: RequestUpdate, request: Request):
    user = _user(request)
    actor = user.get("username") or ""
    with _LOCK:
        rows = _load()
        item = _find(rows, request_id)
        _owner(item, actor)
        item.setdefault("edit_history", []).append({
            "at": _now(), "actor": actor,
            "snapshot": {key: item.get(key, "") for key in
                         ("request_type", "product", "title", "details", "target_lots", "requester_team", "priority")},
        })
        item["request_type"] = _required(payload.request_type, "요청 유형", max_len=80)
        item["product"] = _required(payload.product, "제품", max_len=120)
        item["title"] = _required(payload.title, "제목", max_len=240)
        item["details"] = _required_rich(payload.details, "상세 요청")
        if payload.target_lots is not None:
            item["target_lots"] = _clean(payload.target_lots)
        item["requester_team"] = _clean(payload.requester_team, max_len=120)
        item["priority"] = payload.priority
        item["updated_at"] = _now()
        _activity(item, "request_updated", actor, note="요청 내용 수정")
        _save(rows)
        _audit("request_updated", actor, request_id)
        return _public(item, user, detail=True)


@router.delete("/{request_id}")
def delete_request(request_id: str, request: Request):
    user = _user(request)
    actor = user.get("username") or ""
    with _LOCK:
        rows = _load()
        item = _find(rows, request_id)
        _owner(item, actor)
        item["deleted_at"] = _now()
        item["deleted_by"] = actor
        _activity(item, "request_deleted", actor, note="요청 삭제")
        _save(rows)
        _audit("request_deleted", actor, request_id, title=item.get("title", ""))
    return {"ok": True}


@router.post("/{request_id}/status")
def update_status(request_id: str, payload: StatusUpdate, request: Request):
    user = _user(request)
    _require_processor(user)
    actor = user.get("username") or ""
    with _LOCK:
        rows = _load()
        item = _find(rows, request_id)
        old = item.get("status") or "registered"
        if old != payload.status:
            now = _now()
            item["status"] = payload.status
            item["updated_at"] = now
            item["processed_by"] = actor
            item["processed_at"] = now
            item.setdefault("status_history", []).append({
                "from": old, "to": payload.status, "actor": actor, "at": now,
                "note": _clean(payload.note, max_len=2000),
            })
            _activity(item, "status_changed", actor, note=payload.note,
                      **{"from": old, "to": payload.status})
            _save(rows)
            _audit("status_changed", actor, request_id, before=old, after=payload.status, note=payload.note)
        result = _public(item, user, detail=True)
    if old != payload.status:
        _notify("lot_request_status", actor, item.get("author") or "", item,
                f"{old} → {payload.status}")
    return result


@router.post("/{request_id}/responses")
def create_response(request_id: str, payload: ResponseWrite, request: Request):
    user = _user(request)
    _require_processor(user)
    actor = user.get("username") or ""
    now = _now()
    response = {
        "id": uuid.uuid4().hex, "body": _required_rich(payload.body, "처리 답변"),
        "assigned_lots": _clean(payload.assigned_lots or ""), "author": actor,
        "author_name": _clean(user.get("name"), max_len=120),
        "created_at": now, "updated_at": now, "edit_history": [],
    }
    with _LOCK:
        rows = _load()
        item = _find(rows, request_id)
        item.setdefault("responses", []).append(response)
        item["updated_at"] = now
        _activity(item, "response_created", actor, note=response.get("assigned_lots", ""), response_id=response["id"])
        _save(rows)
        _audit("response_created", actor, request_id, response_id=response["id"])
    _notify("lot_request_response", actor, item.get("author") or "", item, response["body"][:100])
    return _public(item, user, detail=True)


@router.put("/{request_id}/responses/{response_id}")
def update_response(request_id: str, response_id: str, payload: ResponseWrite, request: Request):
    user = _user(request)
    _require_processor(user)
    actor = user.get("username") or ""
    with _LOCK:
        rows = _load()
        item = _find(rows, request_id)
        response = next((row for row in item.get("responses", []) if row.get("id") == response_id), None)
        if not response:
            raise HTTPException(404, "답변을 찾을 수 없습니다")
        _owner(response, actor, "답변")
        response.setdefault("edit_history", []).append({
            "at": _now(), "actor": actor,
            "snapshot": {"body": response.get("body", ""), "assigned_lots": response.get("assigned_lots", "")},
        })
        response["body"] = _required_rich(payload.body, "처리 답변")
        if payload.assigned_lots is not None:
            response["assigned_lots"] = _clean(payload.assigned_lots)
        response["updated_at"] = _now()
        item["updated_at"] = response["updated_at"]
        _activity(item, "response_updated", actor, note="처리 답변 수정", response_id=response_id)
        _save(rows)
        _audit("response_updated", actor, request_id, response_id=response_id)
        return _public(item, user, detail=True)


@router.delete("/{request_id}/responses/{response_id}")
def delete_response(request_id: str, response_id: str, request: Request):
    user = _user(request)
    _require_processor(user)
    actor = user.get("username") or ""
    with _LOCK:
        rows = _load()
        item = _find(rows, request_id)
        responses = item.get("responses") if isinstance(item.get("responses"), list) else []
        idx = next((i for i, row in enumerate(responses) if row.get("id") == response_id), -1)
        if idx < 0:
            raise HTTPException(404, "답변을 찾을 수 없습니다")
        _owner(responses[idx], actor, "답변")
        deleted = responses.pop(idx)
        item["updated_at"] = _now()
        _activity(item, "response_deleted", actor, note="처리 답변 삭제", response_id=response_id)
        _save(rows)
        _audit("response_deleted", actor, request_id, response_id=response_id,
               body_preview=str(deleted.get("body") or "")[:160])
    return {"ok": True}


def _mail_targets(payload: MailWrite) -> tuple[list[str], list[str]]:
    users = [_clean(value, max_len=200) for value in payload.to_users if _clean(value, max_len=200)]
    extras = [_clean(value, max_len=320) for value in payload.extra_emails if "@" in str(value or "")]
    wanted = {str(value or "").strip().casefold() for value in payload.groups if str(value or "").strip()}
    if wanted:
        try:
            from routers.groups import _load as load_groups
            for group in load_groups() or []:
                if str(group.get("name") or "").strip().casefold() not in wanted:
                    continue
                users.extend(str(value or "").strip() for value in group.get("members") or [])
                extras.extend(str(value or "").strip() for value in group.get("extra_emails") or [])
        except Exception:
            pass
    return list(dict.fromkeys(filter(None, users))), list(dict.fromkeys(filter(None, extras)))


def _inline_mail_images(value: object) -> str:
    safe = sanitize_rich_html(value)

    def replace(match: re.Match) -> str:
        uid, name = match.group(1), _safe_filename(match.group(2))
        target = (UPLOADS_DIR / uid / name).resolve()
        try:
            target.relative_to(UPLOADS_DIR.resolve())
        except Exception:
            return ""
        if not target.is_file() or target.stat().st_size > MAX_UPLOAD_BYTES:
            return ""
        mime = mimetypes.guess_type(str(target))[0] or "image/png"
        encoded = base64.b64encode(target.read_bytes()).decode("ascii")
        return f'src="data:{mime};base64,{encoded}"'

    return re.sub(r'src="/api/lot-requests/files/([A-Za-z0-9]+)/([^"?]+)(?:\?[^\"]*)?"', replace, safe)


def _mail_html(item: dict, custom_body: str = "") -> str:
    type_label = {"lot_assignment": "랏 배정", "hot_grade": "Hot grade", "other": "기타 요청"}.get(item.get("request_type"), "요청")
    status_label = {"registered": "등록", "in_progress": "처리중", "completed": "처리완료", "rejected": "반려"}.get(item.get("status"), item.get("status", ""))
    intro = "<p>" + html.escape(custom_body).replace("\n", "<br>") + "</p>" if custom_body.strip() else ""
    responses = []
    for response in item.get("responses") or []:
        responses.append(
            '<div class="reply"><div class="reply-head">'
            + html.escape(str(response.get("author_name") or response.get("author") or "-"))
            + " · " + html.escape(str(response.get("created_at") or "").replace("T", " "))
            + "</div>" + _inline_mail_images(response.get("body"))
            + ("<div class=\"lots\"><b>배정/처리 랏</b><br>" + html.escape(str(response.get("assigned_lots"))).replace("\n", "<br>") + "</div>" if response.get("assigned_lots") else "")
            + "</div>"
        )
    history_rows = []
    for event in sorted(item.get("activity_history") or [], key=lambda row: str(row.get("at") or ""), reverse=True):
        action = {
            "request_created": "요청 등록", "request_updated": "요청 수정", "status_changed": "상태 변경",
            "response_created": "답변 등록", "response_updated": "답변 수정", "response_deleted": "답변 삭제",
            "mail_sent": "메일 발송",
        }.get(event.get("action"), str(event.get("action") or "이력"))
        detail = str(event.get("note") or "")
        if event.get("action") == "status_changed":
            detail = f"{event.get('from', '')} → {event.get('to', '')}" + (f" · {detail}" if detail else "")
        history_rows.append("<tr><td>" + html.escape(str(event.get("at") or "").replace("T", " ")) + "</td><td>" + html.escape(str(event.get("actor") or "-")) + "</td><td>" + html.escape(action) + "</td><td>" + html.escape(detail) + "</td></tr>")
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
body{{font-family:Arial,'Malgun Gothic',sans-serif;color:#1f2937;line-height:1.55;font-size:14px}}h2{{margin:0 0 6px}}.meta{{color:#6b7280;margin-bottom:16px}}.box,.reply{{border:1px solid #d1d5db;border-radius:7px;padding:14px;margin:10px 0}}.reply-head{{font-weight:700;color:#4b5563;margin-bottom:8px}}.lots{{background:#f3f4f6;padding:9px;margin-top:9px}}table{{border-collapse:collapse;width:100%;margin-top:8px}}th,td{{border:1px solid #d1d5db;padding:6px 8px;text-align:left}}th{{background:#f3f4f6}}img{{max-width:100%;height:auto}}.content table{{width:auto;max-width:100%}}
</style></head><body>{intro}<h2>[{html.escape(str(item.get('product') or ''))}] {html.escape(str(item.get('title') or ''))}</h2>
<div class="meta">{html.escape(type_label)} · {html.escape(str(item.get('requester_team') or '부서/모듈 미지정'))} · 상태 {html.escape(status_label)} · 요청자 {html.escape(str(item.get('author_name') or item.get('author') or '-'))}</div>
<div class="box content">{_inline_mail_images(item.get('details'))}</div>
{('<div class="lots"><b>대상/요청 랏</b><br>' + html.escape(str(item.get('target_lots'))).replace(chr(10), '<br>') + '</div>') if item.get('target_lots') else ''}
<h3>PI 처리 답변 ({len(responses)}건)</h3>{''.join(responses) or '<p>등록된 처리 답변이 없습니다.</p>'}
<h3>업무 히스토리</h3><table><thead><tr><th>시각</th><th>사용자</th><th>작업</th><th>내용</th></tr></thead><tbody>{''.join(history_rows)}</tbody></table>
<hr style="border:none;border-top:1px solid #e5e7eb;margin:18px 0 8px"><div style="color:#6b7280">상세 확인 및 후속 조치는 <a href="http://go/flow_process-request" style="color:#374151;font-weight:700">go/flow_process-request</a>를 참고해 주세요.</div>
</body></html>"""


@router.post("/{request_id}/mail-preview")
def preview_mail(request_id: str, payload: MailWrite, request: Request):
    user = _user(request)
    _require_processor(user)
    with _LOCK:
        item = _public(_find(_load(), request_id), user, detail=True)
    users, extras = _mail_targets(payload)
    try:
        from core.mail import resolve_usernames_to_emails
        resolved, skipped = resolve_usernames_to_emails(users)
    except Exception:
        resolved, skipped = [], []
    resolved.extend(email for email in extras if email.casefold() not in {value.casefold() for value in resolved})
    subject = payload.subject.strip() or f"[랏 배정/요청] {item.get('product', '')} · {item.get('title', '')}"
    return {"subject": subject, "html": _mail_html(item, payload.body), "resolved_recipients": resolved,
            "skipped": skipped, "sender": user.get("username") or ""}


@router.post("/{request_id}/send-mail")
def send_request_mail(request_id: str, payload: MailWrite, request: Request):
    user = _user(request)
    _require_processor(user)
    actor = user.get("username") or ""
    with _LOCK:
        rows = _load()
        item = _find(rows, request_id)
        snapshot = _public(item, user, detail=True)
    users, extras = _mail_targets(payload)
    if not users and not extras:
        raise HTTPException(400, "메일 수신자를 선택해주세요")
    subject = payload.subject.strip() or f"[랏 배정/요청] {snapshot.get('product', '')} · {snapshot.get('title', '')}"
    from core.mail import send_mail
    result = send_mail(actor, users, subject, _mail_html(snapshot, payload.body), extra_emails=extras)
    if not result.get("ok"):
        raise HTTPException(502, result.get("reason") or "메일 발송에 실패했습니다")
    now = _now()
    with _LOCK:
        rows = _load()
        item = _find(rows, request_id)
        record = {"at": now, "actor": actor, "subject": subject, "to": result.get("to") or [],
                  "status": result.get("status", 0), "dry_run": bool(result.get("dry_run"))}
        item.setdefault("mail_history", []).append(record)
        _activity(item, "mail_sent", actor, note=f"{len(record['to'])}명 · {subject}")
        _save(rows)
        _audit("mail_sent", actor, request_id, recipients=len(record["to"]), subject=subject,
               dry_run=record["dry_run"])
    return {"ok": True, **record}


ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
MAX_UPLOAD_BYTES = 8 * 1024 * 1024


def _safe_filename(value: str) -> str:
    name = Path(str(value or "image")).name
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:160] or "image.png"


@router.post("/upload")
async def upload_image(request: Request):
    user = _user(request)
    try:
        form = await request.form()
    except Exception as exc:
        raise HTTPException(500, f"이미지 업로드 파서를 사용할 수 없습니다: {exc}")
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(400, "file 필드가 필요합니다")
    filename = _safe_filename(getattr(upload, "filename", "") or "image.png")
    content_type = str(getattr(upload, "content_type", "") or "")
    data_or_coro = upload.read()
    data = await data_or_coro if hasattr(data_or_coro, "__await__") else data_or_coro
    data = bytes(data or b"")
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTS and content_type.startswith("image/"):
        ext = ".jpg" if content_type == "image/jpeg" else f".{content_type.split('/', 1)[1]}"
        filename = _safe_filename(Path(filename).stem + ext)
    if ext not in ALLOWED_IMAGE_EXTS:
        raise HTTPException(400, "PNG, JPG, GIF, WEBP, BMP 이미지만 붙여넣을 수 있습니다")
    if not data:
        raise HTTPException(400, "빈 이미지입니다")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "이미지가 너무 큽니다 (최대 8MB)")
    uid = uuid.uuid4().hex[:12]
    target_dir = UPLOADS_DIR / uid
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / filename
    target.write_bytes(data)
    url = f"/api/lot-requests/files/{uid}/{filename}"
    return {"ok": True, "filename": filename, "url": url, "size": len(data),
            "uploaded_by": user.get("username") or ""}


@router.get("/files/{uid}/{name}")
def serve_image(uid: str, name: str, request: Request):
    _user(request)
    if not re.fullmatch(r"[A-Za-z0-9]+", uid):
        raise HTTPException(400, "잘못된 이미지 경로입니다")
    safe = _safe_filename(name)
    target = (UPLOADS_DIR / uid / safe).resolve()
    try:
        target.relative_to(UPLOADS_DIR.resolve())
    except Exception:
        raise HTTPException(403, "잘못된 이미지 경로입니다")
    if not target.is_file():
        raise HTTPException(404, "이미지를 찾을 수 없습니다")
    mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    return FileResponse(str(target), media_type=mime)


def _notify(kind: str, actor: str, target: str, item: dict, body: str) -> None:
    """Best-effort in-app notification; persistence must never depend on it."""
    try:
        from core.notify import emit_event
        emit_event(kind, actor=actor, target_user=target or "", title=f"[랏 배정/요청] {item.get('title')}",
                   body=body, payload={"request_id": item.get("id")})
    except Exception:
        pass
