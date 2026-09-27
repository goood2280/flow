"""분석의뢰 게시판 API.

게시판 규칙은 랏 배정/요청(routers/lot_requests.py)과 같다 — 의뢰·답글 본문은 작성자만 고치고,
답글·처리 상태는 ``analysisrequest`` 페이지 위임자와 관리자가 쓴다.

의뢰서는 의뢰 내용과 대상 Lot 만 받는다. 실제 진행·측정이 의뢰와 맞는지는 엔지니어가
``/{id}/split-view``(SplitTable 과 같은 표 + 아래 DC layer 별 ET 측정 행)에서 확인하고,
Template Report 로 만든 결과(PPTX)를 답글에 첨부한다. 답글이 달리면 의뢰자에게 답글 본문과
첨부가 들어간 메일을 보낸다. 조회·판정 로직은 core/analysis_requests.py (ET 추적 스캔도 같은 코드).
"""
from __future__ import annotations

import base64
import copy
import html
import json
import mimetypes
import re
import threading
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from core import analysis_requests as store
from core.auth import current_user, is_page_manager, user_tab_tokens
from core.rich_text import rich_text_has_content, sanitize_rich_html


router = APIRouter(prefix="/api/analysis-requests", tags=["analysis-requests"])

PAGE_ID = "analysisrequest"
# 분석의뢰는 랏 배정/요청 흐름을 확장한 게시판이라, 그 탭 권한 사용자는 재저장 없이
# 바로 쓴다(프론트 permissions.js INHERITED_TAB_ACCESS 와 같은 규칙).
INHERITED_FROM = ("lotrequest",)
STATUSES = {"registered", "in_progress", "completed", "rejected"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
ATTACHMENT_EXTS = IMAGE_EXTS | {".pptx", ".ppt", ".xlsx", ".xls", ".csv", ".pdf", ".docx", ".doc", ".zip", ".txt"}
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_ATTACHMENT_BYTES = 40 * 1024 * 1024
MAX_ATTACHMENTS = 10
MAIL_ATTACH_BUDGET = 9 * 1024 * 1024      # core.mail ATTACH_MAX(10MB) 안에서 여유를 둔다
MAIL_BODY_BUDGET = 1_800_000              # core.mail CONTENT_MAX(2MB) 안에서 여유를 둔다


def _user(request: Request) -> dict:
    user = current_user(request)
    if user.get("role") == "admin" or is_page_manager(user, PAGE_ID):
        return user
    tabs, _ = user_tab_tokens(user)
    if PAGE_ID not in tabs and not any(tab in tabs for tab in INHERITED_FROM):
        raise HTTPException(403, "분석의뢰 탭 권한이 필요합니다")
    return user


def _can_process(user: dict) -> bool:
    return is_page_manager(user or {}, PAGE_ID)


def _require_processor(user: dict) -> None:
    if not _can_process(user):
        raise HTTPException(403, "분석의뢰 페이지 위임자와 관리자만 처리할 수 있습니다")


def _required(value: object, label: str, *, max_len: int) -> str:
    cleaned = store.clean(value, max_len=max_len)
    if not cleaned:
        raise HTTPException(400, f"{label}을(를) 입력해주세요")
    return cleaned


def _required_rich(value: object, label: str) -> str:
    cleaned = sanitize_rich_html(value)
    if not rich_text_has_content(cleaned):
        raise HTTPException(400, f"{label}을(를) 입력해주세요")
    return cleaned


def _find(rows: list[dict], request_id: str) -> dict:
    item = next((row for row in rows if row.get("id") == request_id and not row.get("deleted_at")), None)
    if not item:
        raise HTTPException(404, "분석의뢰를 찾을 수 없습니다")
    return item


def _owner(item: dict, username: str, noun: str = "의뢰") -> None:
    if str(item.get("author") or "") != username:
        raise HTTPException(403, f"{noun} 작성자만 수정하거나 삭제할 수 있습니다")


def _activity(item: dict, action: str, actor: str, *, note: str = "", **detail) -> None:
    item.setdefault("activity_history", []).append({
        "at": store.now_iso(), "action": action, "actor": actor,
        "note": store.clean(note, max_len=2000), **detail,
    })


def _public(item: dict, user: dict, *, detail: bool = False) -> dict:
    out = copy.deepcopy(item)
    responses = out.get("responses") if isinstance(out.get("responses"), list) else []
    out["response_count"] = len(responses)
    progress = store.build_progress(item)
    lots = store.request_lots(item)
    out["lots"] = lots
    out["lots_text"] = store.lots_text(lots)
    out["summary"] = progress["summary"]
    tracking = out.pop("tracking", None) or {}
    out.pop("plan_rows", None)
    if detail:
        out["tracking"] = progress["tracking"]
        out["tracking"]["split_unknown_columns"] = tracking.get("split_unknown_columns") or []
        out["report_handoff"] = store.report_handoff(item, progress)
        out["activity_history"] = list(reversed(sorted(
            out.get("activity_history") or [], key=lambda event: str(event.get("at") or ""))))
        for response in responses:
            mine = response.get("author") == user.get("username")
            response["permissions"] = {"can_edit": _can_process(user) and mine,
                                       "can_delete": _can_process(user) and mine}
    else:
        out.pop("responses", None)
        out.pop("edit_history", None)
        out.pop("details", None)
        out["tracking_status"] = {"split": tracking.get("split_status", ""), "et": tracking.get("et_status", ""),
                                  "checked_at": tracking.get("et_checked_at", "")}
    mine = str(out.get("author") or "") == str(user.get("username") or "")
    out["permissions"] = {
        "can_edit": mine, "can_delete": mine,
        "can_respond": _can_process(user), "can_process": _can_process(user),
        "can_change_view": mine or _can_process(user),
    }
    return out


class RequestWrite(BaseModel):
    request_type: str = Field(default="", max_length=80)
    product: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=240)
    details: str = Field(min_length=1, max_length=200000)
    requester_team: str = Field(default="", max_length=120)
    priority: Literal["normal", "high", "urgent"] = "normal"
    lots: str | list = Field(default="")
    split_columns: list[str] = Field(default_factory=list, max_length=store.MAX_SPLIT_COLUMNS)
    report_template_id: str = Field(default="", max_length=80)
    template_id: str = Field(default="", max_length=40)
    copied_from: str = Field(default="", max_length=40)


class ViewWrite(BaseModel):
    """엔지니어가 상세 화면에서 바꾸는 조회 조건 — 대상 Lot 과 표에 보일 열."""
    lots: str | list | None = None
    split_columns: list[str] | None = Field(default=None, max_length=store.MAX_SPLIT_COLUMNS)


class StatusUpdate(BaseModel):
    status: Literal["registered", "in_progress", "completed", "rejected"]
    note: str = Field(default="", max_length=2000)


class AttachmentRef(BaseModel):
    uid: str = Field(min_length=1, max_length=40)
    name: str = Field(default="", max_length=200)


class ResponseWrite(BaseModel):
    body: str = Field(default="", max_length=200000)
    attachments: list[AttachmentRef] = Field(default_factory=list, max_length=MAX_ATTACHMENTS)


class TemplateWrite(BaseModel):
    id: str = Field(default="", max_length=40)
    name: str = Field(min_length=1, max_length=80)
    request_type: str = Field(default="", max_length=80)
    title: str = Field(default="", max_length=240)
    details: str = Field(default="", max_length=200000)
    split_columns: list[str] = Field(default_factory=list, max_length=store.MAX_SPLIT_COLUMNS)
    report_template_id: str = Field(default="", max_length=80)


class ConfigWrite(BaseModel):
    request_types: list[str] = Field(default_factory=list, max_length=50)
    request_teams: list[str] = Field(default_factory=list, max_length=100)
    templates: list[TemplateWrite] = Field(default_factory=list, max_length=30)
    default_template_id: str = Field(default="", max_length=40)
    notify_on_response: bool = True


class ReportDraftWrite(BaseModel):
    request_id: str = Field(min_length=1, max_length=40)
    template_id: str = Field(default="", max_length=80)
    note: str = Field(default="", max_length=600)


class ReportLinkWrite(BaseModel):
    template_id: str = Field(min_length=1, max_length=80)


def _dump(model) -> dict:
    return model.model_dump() if hasattr(model, "model_dump") else model.dict()


def _request_fields(payload: RequestWrite, config: dict) -> dict:
    return {
        "request_type": store.clean(payload.request_type, max_len=80) or config["request_types"][0],
        "product": store.clean_product(_required(payload.product, "제품", max_len=120)),
        "title": _required(payload.title, "제목", max_len=240),
        "details": _required_rich(payload.details, "의뢰 내용"),
        "requester_team": store.clean(payload.requester_team, max_len=120),
        "priority": payload.priority,
        "lots": store.normalize_lots(payload.lots),
        "split_columns": store.normalize_split_columns(payload.split_columns),
        "report_template_id": store.clean(payload.report_template_id, max_len=80),
        "template_id": store.clean(payload.template_id, max_len=40),
    }


# 조회 범위를 바꾸는 필드 — 바뀌면 revision 을 올려 진행 중인 갱신 결과를 버리게 한다.
_TRACKED_FIELDS = ("product", "lots", "split_columns")


def _refresh_quietly(request_id: str, actor: str, fallback: dict) -> dict:
    try:
        return store.refresh_request(request_id, actor=actor) or fallback
    except Exception as exc:
        store.logger.warning("analysis request refresh failed: %s", exc)
        return fallback


@router.get("")
def list_requests(
    request: Request,
    status: str = Query(""), product: str = Query(""), requester_team: str = Query(""),
    request_type: str = Query(""), author: str = Query(""), q: str = Query(""), mine: bool = Query(False),
):
    user = _user(request)
    username = user.get("username") or ""
    with store.LOCK:
        all_rows = [row for row in store.load_rows() if not row.get("deleted_at")]
    rows = list(all_rows)
    if status in STATUSES:
        rows = [row for row in rows if row.get("status") == status]
    for key, value in (("product", product), ("requester_team", requester_team),
                       ("request_type", request_type), ("author", author)):
        if value.strip():
            wanted = value.strip().casefold()
            rows = [row for row in rows if str(row.get(key) or "").strip().casefold() == wanted]
    if mine:
        rows = [row for row in rows if row.get("author") == username]
    query = q.strip().casefold()
    if query:
        def haystack(row: dict) -> str:
            lots = " ".join(lot["root_lot_id"] for lot in store.request_lots(row))
            return " ".join(str(row.get(key) or "") for key in
                            ("title", "details", "product", "author", "requester_team", "request_type")) + " " + lots
        rows = [row for row in rows if query in haystack(row).casefold()]
    rows.sort(key=lambda row: (row.get("updated_at") or row.get("created_at") or ""), reverse=True)
    counts = {key: 0 for key in STATUSES}
    authors: dict[str, str] = {}
    for row in all_rows:
        if row.get("status") in counts:
            counts[row.get("status")] += 1
        if row.get("author"):
            authors[str(row["author"])] = str(row.get("author_name") or "")
    return {
        "requests": [_public(row, user) for row in rows],
        "total": len(rows), "status": counts, "all_total": len(all_rows),
        "can_process": _can_process(user),
        "facets": {
            "products": sorted({str(r.get("product") or "") for r in all_rows if r.get("product")}),
            "requester_teams": sorted({str(r.get("requester_team") or "") for r in all_rows if r.get("requester_team")}),
            "authors": [{"username": u, "name": authors[u]} for u in sorted(authors)],
        },
    }


@router.get("/config")
def get_config(request: Request):
    user = _user(request)
    return {**store.load_config(), "can_edit": bool(is_page_manager(user, PAGE_ID))}


@router.post("/config")
def save_config(payload: ConfigWrite, request: Request):
    user = _user(request)
    if not is_page_manager(user, PAGE_ID):
        raise HTTPException(403, "분석의뢰 페이지 관리자만 설정을 변경할 수 있습니다")
    config = store.save_config(_dump(payload), user.get("username") or "")
    store.audit("config_updated", user.get("username") or "", "", templates=len(config["templates"]))
    return {**config, "can_edit": True}


@router.get("/ml-columns")
def ml_columns(request: Request, product: str = Query(...), q: str = Query(""), limit: int = Query(300)):
    """표에 보일 ML_TABLE 열 — 파일 메타데이터(스키마)만 읽는다."""
    _user(request)
    names = [n for n in store.ml_table_columns(product) if n.casefold() not in set(store.IDENTITY_KEYS)]
    if not names:
        return {"ok": False, "columns": [], "matched": 0, "total": 0, "prefixes": {},
                "error": f"ML_TABLE_{store.clean_product(product)} 파일을 찾지 못했습니다"}
    needle = q.strip().casefold()
    matched = [n for n in names if not needle or needle in n.casefold()]
    prefixes: dict[str, int] = {}
    for name in names:
        prefix = name.split("_", 1)[0].upper() if "_" in name else "기타"
        prefixes[prefix] = prefixes.get(prefix, 0) + 1
    limit = max(1, min(1000, int(limit or 300)))
    return {"ok": True, "file": f"ML_TABLE_{store.clean_product(product)}", "columns": matched[:limit],
            "matched": len(matched), "total": len(names), "prefixes": prefixes}


# LLM 을 부르는 유일한 경로 — llm_adapter._DATA_TASK_PATHS 와 upstream_proxy._AI_PROXY_PATHS_DEFAULT 가
# 정확한 경로로만 허용하므로 id 를 경로가 아니라 본문으로 받는다.
@router.post("/report-draft")
def report_draft(payload: ReportDraftWrite, request: Request):
    """연결된 Template 을 이 의뢰(랏 · wafer slot · split 열)에 맞게 고친 초안 + 검증 결과. 저장하지 않는다.

    1) 규칙 치환 — 랏 · lot_id · split 열(같은 공정 FAB_/MASK_ 포함) · wafer slot · split 값 · 측정 매수 ·
       변경 번호. wafer slot 은 대표 split 열(의뢰의 첫 표시 열)의 실제 값으로 나눈다. LLM 이 없어도 동작한다.
    2) LLM 다듬기(선택) — 연결된 LLM 이 있고 Template Report 관리자면, 규칙이 못 바꾸는 설명 문장만 다듬는다.
    3) 검증 — 원본 Template 과 대조. LLM 결과가 검증에 실패하고 규칙 결과는 통과하면 규칙 결과를 쓴다."""
    user = _user(request)
    request_id = payload.request_id
    with store.LOCK:
        item = copy.deepcopy(_find(store.load_rows(), request_id))
    template_id = store.clean(payload.template_id, max_len=80) or str(item.get("report_template_id") or "")
    if not template_id:
        raise HTTPException(400, "먼저 의뢰에 보고서 Template 을 연결해 주세요")
    from routers import template_report as reports
    original = reports._template_or_404(template_id)
    current = reports._normalize_code_template(reports.TemplateSaveReq(**original), user)
    progress = store.build_progress(item)
    if not progress["rows"]:
        raise HTTPException(400, "대상 Lot 의 wafer 가 아직 확인되지 않았습니다 — '지금 갱신' 후 다시 시도해 주세요")
    with store.LOCK:
        source = store.source_request_for(item, str(original.get("id") or template_id))
    swapped, rule_changes = store.rule_swap_template(current, item, progress, source)
    swapped = reports._normalize_code_template(reports.TemplateSaveReq(**swapped), user)
    rule_check = store.verify_report_template(current, swapped, item, progress)
    instruction = store.report_instruction(item, swapped, progress, payload.note, rule_changes)
    llm_info: dict = {"available": False, "used": False}
    message = "규칙으로 랏 · wafer slot · split 값을 바꿨습니다."
    final, verification, mode, llm_check = swapped, rule_check, "rule", None
    if is_page_manager(user, "templatereport"):
        result = reports.run_template_assistant(swapped, instruction, user, include_available_charts=False)
        llm_info = result.get("llm") or llm_info
        if llm_info.get("used"):
            llm_check = store.verify_report_template(current, result["template"], item, progress)
            if llm_check["ok"] or not rule_check["ok"]:
                final, verification, mode = result["template"], llm_check, "rule+llm"
                message = result.get("message") or "규칙 치환 후 AI 가 설명 문장을 다듬었습니다."
            else:
                message = "AI 결과가 검증을 통과하지 못해 규칙 결과를 씁니다."
        else:
            message += " (AI 다듬기 생략: " + str(result.get("message") or "연결된 AI 없음")[:160] + ")"
    else:
        message += " (AI 다듬기는 Template Report 관리자만 쓸 수 있습니다)"
    if mode == "rule" and any(change.startswith("split 열") for change in rule_changes):
        verification["checks"].append({"level": "warn", "label": "설명 문장",
                                       "detail": "split 열이 바뀌었습니다 — 목적·배경 문장을 확인하세요"})
    store.audit("report_draft", user.get("username") or "", request_id, template_id=template_id,
                mode=mode, verified=bool(verification["ok"]))
    return {
        "mode": mode, "instruction": instruction, "rule_changes": rule_changes, "message": message,
        "changed": final != current, "llm": llm_info, "template": final,
        "verification": verification, "llm_verification": llm_check,
        "source_request": {"id": source.get("id"), "title": source.get("title")} if source else None,
        "source_template": {"id": original.get("id"), "name": original.get("name")},
    }


@router.get("/{request_id}")
def get_request(request_id: str, request: Request):
    user = _user(request)
    with store.LOCK:
        return _public(_find(store.load_rows(), request_id), user, detail=True)


@router.get("/{request_id}/split-view")
def split_view(request_id: str, request: Request):
    """SplitTable 과 같은 표(랏별 wafer 열) + 아래 DC layer 별 ET 측정 행."""
    _user(request)
    with store.LOCK:
        item = copy.deepcopy(_find(store.load_rows(), request_id))
    return store.split_view(item)


@router.post("")
def create_request(payload: RequestWrite, request: Request):
    user = _user(request)
    actor = user.get("username") or ""
    now = store.now_iso()
    item = {
        "id": uuid.uuid4().hex,
        **_request_fields(payload, store.load_config()),
        "copied_from": store.clean(payload.copied_from, max_len=40),
        "status": "registered",
        "author": actor, "author_name": store.clean(user.get("name"), max_len=120),
        "created_at": now, "updated_at": now, "processed_at": "", "processed_by": "",
        "revision": 1,
        "status_history": [{"from": "", "to": "registered", "actor": actor, "at": now, "note": "의뢰 등록"}],
        "edit_history": [],
        "activity_history": [{"at": now, "action": "request_created", "actor": actor,
                              "note": "의뢰 등록" + (" (기존 의뢰 복제)" if payload.copied_from else "")}],
        "responses": [], "tracking": {},
    }
    with store.LOCK:
        rows = store.load_rows()
        rows.append(item)
        store.save_rows(rows)
    store.audit("request_created", actor, item["id"], copied_from=item["copied_from"])
    # 등록 직후 한 번 읽어 둔다 — 캐시만 읽으므로 빠르고, 실패해도 등록은 유지된다.
    if item["lots"]:
        item = _refresh_quietly(item["id"], actor, item)
    _notify("analysis_request_created", actor, "", item, f"새 분석의뢰 · {item['product']}")
    return _public(item, user, detail=True)


@router.put("/{request_id}")
def update_request(request_id: str, payload: RequestWrite, request: Request):
    user = _user(request)
    actor = user.get("username") or ""
    fields = _request_fields(payload, store.load_config())
    with store.LOCK:
        rows = store.load_rows()
        item = _find(rows, request_id)
        _owner(item, actor)
        item.setdefault("edit_history", []).append({
            "at": store.now_iso(), "actor": actor,
            "snapshot": {key: copy.deepcopy(item.get(key)) for key in fields},
        })
        changed = any(item.get(key) != fields[key] for key in _TRACKED_FIELDS)
        item.update(fields)
        item.pop("plan_rows", None)
        if changed:
            item["revision"] = int(item.get("revision") or 0) + 1
        item["updated_at"] = store.now_iso()
        _activity(item, "request_updated", actor, note="의뢰 내용 수정" + (" · 대상 Lot/열 변경" if changed else ""))
        store.save_rows(rows)
    store.audit("request_updated", actor, request_id, scope_changed=changed)
    if changed:
        item = _refresh_quietly(request_id, actor, item)
    return _public(item, user, detail=True)


@router.post("/{request_id}/view")
def update_view(request_id: str, payload: ViewWrite, request: Request):
    """상세 화면의 조회 조건(대상 Lot · 표시 열) — 의뢰 작성자와 처리 담당자가 바꾼다."""
    user = _user(request)
    actor = user.get("username") or ""
    with store.LOCK:
        rows = store.load_rows()
        item = _find(rows, request_id)
        if str(item.get("author") or "") != actor and not _can_process(user):
            raise HTTPException(403, "의뢰 작성자나 처리 담당자만 조회 조건을 바꿀 수 있습니다")
        before = {key: copy.deepcopy(item.get(key)) for key in ("lots", "split_columns")}
        if payload.lots is not None:
            item["lots"] = store.normalize_lots(payload.lots)
        if payload.split_columns is not None:
            item["split_columns"] = store.normalize_split_columns(payload.split_columns)
        item.pop("plan_rows", None) if payload.lots is not None else None
        changed = any(before[key] != item.get(key) for key in before)
        if changed:
            item["revision"] = int(item.get("revision") or 0) + 1
            item["updated_at"] = store.now_iso()
            _activity(item, "view_updated", actor, note=f"대상 Lot {store.lots_text(store.request_lots(item)) or '-'} · 표시 열 {len(item.get('split_columns') or [])}개")
            store.save_rows(rows)
    if changed:
        item = _refresh_quietly(request_id, actor, item)
    return _public(item, user, detail=True)


@router.delete("/{request_id}")
def delete_request(request_id: str, request: Request):
    user = _user(request)
    actor = user.get("username") or ""
    with store.LOCK:
        rows = store.load_rows()
        item = _find(rows, request_id)
        _owner(item, actor)
        item["deleted_at"] = store.now_iso()
        item["deleted_by"] = actor
        _activity(item, "request_deleted", actor, note="의뢰 삭제")
        store.save_rows(rows)
    store.audit("request_deleted", actor, request_id, title=item.get("title", ""))
    return {"ok": True}


@router.post("/{request_id}/refresh")
def refresh(request_id: str, request: Request):
    """wafer 목록 · 표시 열 값(ML_TABLE 조회 캐시) · ET 측정(ET history 캐시)을 지금 다시 읽는다."""
    user = _user(request)
    with store.LOCK:
        _find(store.load_rows(), request_id)
    item = store.refresh_request(request_id, actor=user.get("username") or "")
    if item is None:
        raise HTTPException(404, "분석의뢰를 찾을 수 없습니다")
    return _public(item, user, detail=True)


@router.post("/{request_id}/report-template")
def link_report_template(request_id: str, payload: ReportLinkWrite, request: Request):
    """새로 저장한 Template 을 이 의뢰의 보고서 Template 으로 연결한다(작성자·처리자)."""
    user = _user(request)
    actor = user.get("username") or ""
    with store.LOCK:
        rows = store.load_rows()
        item = _find(rows, request_id)
        if str(item.get("author") or "") != actor and not _can_process(user):
            raise HTTPException(403, "의뢰 작성자나 처리 담당자만 보고서 Template 을 바꿀 수 있습니다")
        previous = str(item.get("report_template_id") or "")
        item["report_template_id"] = store.clean(payload.template_id, max_len=80)
        item["updated_at"] = store.now_iso()
        _activity(item, "report_template_linked", actor, note=f"{previous or '-'} → {item['report_template_id']}")
        store.save_rows(rows)
        result = _public(item, user, detail=True)
    store.audit("report_template_linked", actor, request_id, before=previous, after=payload.template_id)
    return result


@router.post("/{request_id}/status")
def update_status(request_id: str, payload: StatusUpdate, request: Request):
    user = _user(request)
    _require_processor(user)
    actor = user.get("username") or ""
    with store.LOCK:
        rows = store.load_rows()
        item = _find(rows, request_id)
        old = item.get("status") or "registered"
        if old != payload.status:
            now = store.now_iso()
            item.update({"status": payload.status, "updated_at": now, "processed_by": actor, "processed_at": now})
            item.setdefault("status_history", []).append({
                "from": old, "to": payload.status, "actor": actor, "at": now,
                "note": store.clean(payload.note, max_len=2000),
            })
            _activity(item, "status_changed", actor, note=payload.note, **{"from": old, "to": payload.status})
            store.save_rows(rows)
        result = _public(item, user, detail=True)
    if old != payload.status:
        store.audit("status_changed", actor, request_id, before=old, after=payload.status)
        _notify("analysis_request_status", actor, item.get("author") or "", item, f"{old} → {payload.status}")
    return result


# ─────────────────────────── 첨부 파일 ───────────────────────────

def _safe_filename(value: str) -> str:
    name = Path(str(value or "file")).name
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:160] or "file"


def _display_name(value: str) -> str:
    name = Path(str(value or "file")).name.replace("\x00", "")
    return re.sub(r"[\r\n\t/\\]+", "_", name).strip()[:160] or "file"


def _store_file(data: bytes, filename: str, user: dict, *, kind: str) -> dict:
    uid = uuid.uuid4().hex[:12]
    target_dir = store.uploads_dir() / uid
    target_dir.mkdir(parents=True, exist_ok=True)
    stored = _safe_filename(filename)
    (target_dir / stored).write_bytes(data)
    meta = {"uid": uid, "name": _display_name(filename), "stored": stored, "size": len(data), "kind": kind,
            "mime": mimetypes.guess_type(stored)[0] or "application/octet-stream",
            "uploaded_by": user.get("username") or "", "at": store.now_iso()}
    (target_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return {**meta, "url": f"/api/analysis-requests/files/{uid}/{stored}"}


def _attachment_meta(uid: str) -> dict | None:
    if not re.fullmatch(r"[A-Za-z0-9]+", str(uid or "")):
        return None
    meta_fp = store.uploads_dir() / uid / "meta.json"
    try:
        meta = json.loads(meta_fp.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not (store.uploads_dir() / uid / str(meta.get("stored") or "")).is_file():
        return None
    return {**meta, "url": f"/api/analysis-requests/files/{uid}/{meta['stored']}"}


def _resolve_attachments(refs: list[AttachmentRef]) -> list[dict]:
    out: list[dict] = []
    for ref in refs:
        meta = _attachment_meta(ref.uid)
        if not meta:
            raise HTTPException(400, f"첨부 파일을 찾을 수 없습니다: {ref.name or ref.uid}")
        out.append({key: meta[key] for key in ("uid", "name", "stored", "size", "mime", "url")})
    return out


async def _read_upload(request: Request) -> tuple[str, str, bytes]:
    try:
        form = await request.form()
    except Exception as exc:
        raise HTTPException(500, f"업로드 파서를 사용할 수 없습니다: {exc}")
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(400, "file 필드가 필요합니다")
    data_or_coro = upload.read()
    data = await data_or_coro if hasattr(data_or_coro, "__await__") else data_or_coro
    return str(getattr(upload, "filename", "") or "file"), str(getattr(upload, "content_type", "") or ""), bytes(data or b"")


@router.post("/upload")
async def upload_image(request: Request):
    """본문 편집기에 붙여넣은 그림."""
    user = _user(request)
    filename, content_type, data = await _read_upload(request)
    ext = Path(filename).suffix.lower()
    if ext not in IMAGE_EXTS and content_type.startswith("image/"):
        ext = ".jpg" if content_type == "image/jpeg" else f".{content_type.split('/', 1)[1]}"
        filename = Path(filename).stem + ext
    if ext not in IMAGE_EXTS:
        raise HTTPException(400, "PNG, JPG, GIF, WEBP, BMP 이미지만 붙여넣을 수 있습니다")
    if not data:
        raise HTTPException(400, "빈 이미지입니다")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "이미지가 너무 큽니다 (최대 8MB)")
    meta = _store_file(data, filename, user, kind="image")
    return {"ok": True, "filename": meta["name"], "url": meta["url"], "size": meta["size"], "uploaded_by": meta["uploaded_by"]}


@router.post("/upload-file")
async def upload_attachment(request: Request):
    """답글 첨부(PPTX · Excel · PDF · 그림 …) — Template Report 결과를 내려받아 고친 파일도 여기로 올린다."""
    user = _user(request)
    filename, _content_type, data = await _read_upload(request)
    if Path(filename).suffix.lower() not in ATTACHMENT_EXTS:
        raise HTTPException(400, "PPTX · XLSX · CSV · PDF · DOCX · ZIP · TXT · 그림 파일만 첨부할 수 있습니다")
    if not data:
        raise HTTPException(400, "빈 파일입니다")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(413, f"첨부 파일은 {MAX_ATTACHMENT_BYTES // (1024 * 1024)}MB 이하여야 합니다")
    meta = _store_file(data, filename, user, kind="attachment")
    return {"ok": True, **{key: meta[key] for key in ("uid", "name", "size", "url", "mime")}}


@router.post("/{request_id}/report-attachment")
def report_attachment(request_id: str, payload: dict, request: Request):
    """Template Report 실행 결과를 PPTX 로 만들어 이 의뢰의 답글 첨부로 보관한다(답글 등록은 따로).

    본문은 Template Report ``/export/pptx`` 와 같은 ExportReq — 같은 파일이 만들어진다."""
    user = _user(request)
    _require_processor(user)
    with store.LOCK:
        _find(store.load_rows(), request_id)
    from routers import template_report as reports
    try:
        req = reports.ExportReq(**(payload or {}))
    except Exception as exc:
        raise HTTPException(400, f"보고서 내보내기 요청이 올바르지 않습니다: {exc}")
    filename, data, template = reports.build_pptx_export(req, user)
    meta = _store_file(data, filename, user, kind="report")
    store.audit("report_attachment", user.get("username") or "", request_id,
                template_id=req.template_id, size=len(data))
    return {"ok": True, **{key: meta[key] for key in ("uid", "name", "size", "url", "mime")},
            "template": {"id": template.get("id"), "name": template.get("name")}}


@router.get("/files/{uid}/{name}")
def serve_file(uid: str, name: str, request: Request):
    _user(request)
    if not re.fullmatch(r"[A-Za-z0-9]+", uid):
        raise HTTPException(400, "잘못된 파일 경로입니다")
    base = store.uploads_dir().resolve()
    target = (base / uid / _safe_filename(name)).resolve()
    try:
        target.relative_to(base)
    except Exception:
        raise HTTPException(403, "잘못된 파일 경로입니다")
    if not target.is_file():
        raise HTTPException(404, "파일을 찾을 수 없습니다")
    meta = _attachment_meta(uid) or {}
    media_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    if target.suffix.lower() in IMAGE_EXTS:
        return FileResponse(str(target), media_type=media_type)
    return FileResponse(str(target), media_type=media_type, filename=meta.get("name") or target.name)


# ─────────────────────────── 답글 + 의뢰자 메일 ───────────────────────────

def _response_body(payload: ResponseWrite, attachments: list[dict]) -> str:
    body = sanitize_rich_html(payload.body)
    if not rich_text_has_content(body) and not attachments:
        raise HTTPException(400, "답글 내용이나 첨부 파일을 넣어주세요")
    return body if rich_text_has_content(body) else ""


def _inline_images(value: str, budget: list[int]) -> str:
    """본문의 분석의뢰 그림 URL 을 data URI 로 바꾼다(메일 본문 예산 안에서만)."""
    base = store.uploads_dir().resolve()

    def replace(match: re.Match) -> str:
        uid, name = match.group(1), _safe_filename(match.group(2))
        target = (base / uid / name).resolve()
        try:
            target.relative_to(base)
            raw = target.read_bytes()
        except Exception:
            return match.group(0)
        encoded = base64.b64encode(raw).decode("ascii")
        if len(encoded) > budget[0]:
            return 'src="" data-omitted="1"'
        budget[0] -= len(encoded)
        return f'src="data:{mimetypes.guess_type(str(target))[0] or "image/png"};base64,{encoded}"'

    return re.sub(r'src="/api/analysis-requests/files/([A-Za-z0-9]+)/([^"?]+)(?:\?[^"]*)?"', replace, value)


def _app_link(item: dict) -> str:
    try:
        from core.et_tracker import et_tracker_config
        base = str(et_tracker_config().get("app_base_url") or "").strip().rstrip("/")
    except Exception:
        base = ""
    return f"{base}/analysisrequest?request={item.get('id')}" if base else ""


def response_mail(item: dict, response: dict) -> dict:
    """답글 메일 — 제목 · 본문 HTML · 첨부 파일 목록. 첨부가 메일 한도를 넘으면 링크로만 알린다."""
    budget = [MAIL_BODY_BUDGET]
    reply_html = _inline_images(sanitize_rich_html(response.get("body") or ""), budget) or "<p>(본문 없음 · 첨부 파일 참조)</p>"
    files: list[tuple[str, bytes, str]] = []
    rows: list[str] = []
    used = 0
    link = _app_link(item)
    for attachment in response.get("attachments") or []:
        path = store.uploads_dir() / attachment["uid"] / attachment["stored"]
        size = int(attachment.get("size") or 0)
        attached = False
        if path.is_file() and used + size <= MAIL_ATTACH_BUDGET:
            files.append((attachment["name"], path.read_bytes(), attachment.get("mime") or "application/octet-stream"))
            used += size
            attached = True
        rows.append(f"<li>{html.escape(attachment['name'])} · {size / 1_048_576:.1f}MB"
                    f"{'' if attached else ' — 메일 용량 한도로 첨부하지 못했습니다. flow 에서 내려받아 주세요'}</li>")
    omitted = ' <span style="color:#b45309">(일부 그림은 메일 용량 한도로 뺐습니다)</span>' if "data-omitted" in reply_html else ""
    details = _inline_images(sanitize_rich_html(item.get("details") or ""), budget)
    subject = f"[분석의뢰 답글] {item.get('product', '')} · {item.get('title', '')}"
    body = f"""<!doctype html><html><head><meta charset="utf-8"><style>
body{{font-family:Arial,'Malgun Gothic',sans-serif;color:#1f2937;line-height:1.55;font-size:14px}}h2{{margin:0 0 4px;font-size:18px}}
.meta{{color:#6b7280;margin-bottom:14px}}.box{{border:1px solid #d1d5db;border-radius:4px;padding:14px;margin:10px 0}}
.reply{{border-left:4px solid #E25822}}table{{border-collapse:collapse}}td,th{{border:1px solid #d1d5db;padding:5px 7px}}img{{max-width:100%;height:auto}}
.muted{{color:#6b7280;font-size:12px}}</style></head><body>
<h2>[{html.escape(str(item.get('product') or ''))}] {html.escape(str(item.get('title') or ''))}</h2>
<div class="meta">답글 {html.escape(str(response.get('author_name') or response.get('author') or '-'))} · {html.escape(str(response.get('created_at') or '').replace('T', ' ')[:16])}
 · 대상 Lot {html.escape(store.lots_text(store.request_lots(item)) or '-')}</div>
<div class="box reply">{reply_html}{omitted}</div>
{('<h3 style="font-size:14px">첨부 파일</h3><ul>' + ''.join(rows) + '</ul>') if rows else ''}
<details open><summary class="muted">의뢰 내용</summary><div class="box">{details}</div></details>
<hr style="border:none;border-top:1px solid #e5e7eb;margin:16px 0 8px">
<div class="muted">{('flow 에서 보기: <a href="' + html.escape(link) + '">' + html.escape(link) + '</a>') if link else 'flow › 업무 › 분석의뢰에서 확인할 수 있습니다.'}</div>
</body></html>"""
    return {"subject": subject, "html": body, "files": files}


def _send_response_mail(request_id: str, response_id: str, actor: str) -> None:
    """답글이 달리면 의뢰자에게 메일 — 본문과 첨부를 메일 안에서 본다. 결과는 답글에 남긴다."""
    with store.LOCK:
        item = next((row for row in store.load_rows() if row.get("id") == request_id), None)
    response = next((r for r in (item or {}).get("responses") or [] if r.get("id") == response_id), None)
    if not item or not response:
        return
    target = str(item.get("author") or "")
    record = {"at": store.now_iso(), "to": [], "ok": False, "reason": ""}
    if not target or target == actor:
        record["reason"] = "의뢰자 본인 답글이라 메일을 보내지 않았습니다"
    else:
        try:
            from core.mail import send_mail
            mail = response_mail(item, response)
            result = send_mail(actor, [target], mail["subject"], mail["html"], files=mail["files"] or None)
            record.update({"ok": bool(result.get("ok")), "to": result.get("to") or [],
                           "reason": str(result.get("reason") or ""), "dry_run": bool(result.get("dry_run"))})
        except Exception as exc:
            record["reason"] = f"메일 발송 실패: {exc}"
    with store.LOCK:
        rows = store.load_rows()
        live = next((row for row in rows if row.get("id") == request_id), None)
        live_response = next((r for r in (live or {}).get("responses") or [] if r.get("id") == response_id), None)
        if live_response is not None:
            live_response["mail"] = record
            _activity(live, "mail_sent" if record["ok"] else "mail_failed", actor,
                      note=(f"의뢰자 메일 · {', '.join(record['to'])}" if record["ok"] else record["reason"]))
            store.save_rows(rows)
    store.audit("response_mail", actor, request_id, response_id=response_id, ok=record["ok"], reason=record["reason"][:200])


@router.post("/{request_id}/responses")
def create_response(request_id: str, payload: ResponseWrite, request: Request):
    user = _user(request)
    _require_processor(user)
    actor = user.get("username") or ""
    attachments = _resolve_attachments(payload.attachments)
    now = store.now_iso()
    response = {
        "id": uuid.uuid4().hex, "body": _response_body(payload, attachments), "attachments": attachments,
        "author": actor, "author_name": store.clean(user.get("name"), max_len=120),
        "created_at": now, "updated_at": now, "edit_history": [],
    }
    with store.LOCK:
        rows = store.load_rows()
        item = _find(rows, request_id)
        item.setdefault("responses", []).append(response)
        item["updated_at"] = now
        _activity(item, "response_created", actor, response_id=response["id"],
                  note=f"첨부 {len(attachments)}개" if attachments else "")
        store.save_rows(rows)
        result = _public(item, user, detail=True)
    store.audit("response_created", actor, request_id, response_id=response["id"], attachments=len(attachments))
    _notify("analysis_request_response", actor, item.get("author") or "", item, "답글이 등록되었습니다")
    if store.load_config().get("notify_on_response", True):
        threading.Thread(target=_send_response_mail, args=(request_id, response["id"], actor),
                         name="analysis-request-mail", daemon=True).start()
    return result


@router.put("/{request_id}/responses/{response_id}")
def update_response(request_id: str, response_id: str, payload: ResponseWrite, request: Request):
    user = _user(request)
    _require_processor(user)
    actor = user.get("username") or ""
    attachments = _resolve_attachments(payload.attachments)
    with store.LOCK:
        rows = store.load_rows()
        item = _find(rows, request_id)
        response = next((row for row in item.get("responses", []) if row.get("id") == response_id), None)
        if not response:
            raise HTTPException(404, "답글을 찾을 수 없습니다")
        _owner(response, actor, "답글")
        response.setdefault("edit_history", []).append({"at": store.now_iso(), "actor": actor, "snapshot": {
            "body": response.get("body", ""), "attachments": copy.deepcopy(response.get("attachments") or [])}})
        response["body"] = _response_body(payload, attachments)
        response["attachments"] = attachments
        response["updated_at"] = store.now_iso()
        item["updated_at"] = response["updated_at"]
        _activity(item, "response_updated", actor, response_id=response_id)
        store.save_rows(rows)
        return _public(item, user, detail=True)


@router.delete("/{request_id}/responses/{response_id}")
def delete_response(request_id: str, response_id: str, request: Request):
    user = _user(request)
    _require_processor(user)
    actor = user.get("username") or ""
    with store.LOCK:
        rows = store.load_rows()
        item = _find(rows, request_id)
        responses = item.get("responses") if isinstance(item.get("responses"), list) else []
        idx = next((i for i, row in enumerate(responses) if row.get("id") == response_id), -1)
        if idx < 0:
            raise HTTPException(404, "답글을 찾을 수 없습니다")
        _owner(responses[idx], actor, "답글")
        responses.pop(idx)
        item["updated_at"] = store.now_iso()
        _activity(item, "response_deleted", actor, response_id=response_id)
        store.save_rows(rows)
    store.audit("response_deleted", actor, request_id, response_id=response_id)
    return {"ok": True}


def _notify(kind: str, actor: str, target: str, item: dict, body: str) -> None:
    """Best-effort in-app notification; persistence must never depend on it."""
    try:
        from core.notify import emit_event
        emit_event(kind, actor=actor, target_user=target or "", title=f"[분석의뢰] {item.get('title')}",
                   body=body, payload={"request_id": item.get("id")})
    except Exception:
        pass
