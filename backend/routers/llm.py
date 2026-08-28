"""Small, feature-neutral LLM endpoints kept while Flow-i is parked."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from core import llm_adapter
from core.auth import current_user, require_admin


router = APIRouter(prefix="/api/llm", tags=["llm"])
logger = logging.getLogger("flow.llm.router")


class ErrorExplainReq(BaseModel):
    status: int | None = None
    method: str = ""
    url: str = ""
    page: str = ""
    raw_error: str = ""
    body: Any = None
    context: str = ""


class LLMTestReq(BaseModel):
    prompt: str
    system: str | None = None
    probe_capabilities: bool = True


class DcopSummaryReq(BaseModel):
    findings: list[dict[str, Any]] = Field(default_factory=list)


ERROR_EXPLAIN_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "where": {"type": "string"},
        "cause": {"type": "string"},
        "how_to_fix": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "where", "cause", "how_to_fix"],
    "additionalProperties": False,
}

DCOP_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}


def _clip(value: Any, limit: int = 4000) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        try:
            value = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            value = str(value)
    text = str(value).replace("\r\n", "\n").replace("\r", "\n").strip()
    text = re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;\"']+", r"\1<redacted>", text)
    text = re.sub(r"(?i)(x-session-token\s*[:=]\s*)[^\s,;\"']+", r"\1<redacted>", text)
    text = re.sub(
        r"(?i)(admin_token|access_token|refresh_token|api_key|password|passwd|token)([\"'\s:=]+)([^\"'\s,;&]+)",
        r"\1\2<redacted>",
        text,
    )
    return text if len(text) <= limit else text[:limit].rstrip() + "\n...<truncated>"


def _fallback_error(req: ErrorExplainReq, raw_error: str) -> dict[str, Any]:
    first_line = next((line.strip() for line in raw_error.splitlines() if line.strip()), "")
    location = [part for part in (
        f"화면 {req.page.strip()}" if req.page.strip() else "",
        f"API {req.method.strip().upper()} {req.url.strip()}" if req.url.strip() else "",
        f"HTTP {req.status}" if isinstance(req.status, int) else "",
    ) if part]
    return {
        "summary": first_line[:220] or (f"HTTP {req.status} 오류" if req.status else "앱 요청 처리 오류"),
        "where": " / ".join(location) or "오류가 발생한 화면 또는 API를 확인해야 합니다.",
        "cause": "서버가 요청을 정상 처리하지 못했습니다. 원문 에러를 기준으로 확인해야 합니다.",
        "how_to_fix": [
            "같은 동작을 다시 시도해 재현되는지 확인하세요.",
            "입력값, 선택 대상, 권한과 세션 상태를 확인하세요.",
            "반복되면 발생 위치와 원문 에러를 관리자에게 전달하세요.",
        ],
        "raw_error": raw_error,
    }


def _clean_explanation(value: Any, fallback: dict[str, Any], raw_error: str) -> dict[str, Any]:
    obj = value if isinstance(value, dict) else {}

    def line(key: str, limit: int) -> str:
        text = " ".join(str(obj.get(key) or "").split())
        return text[:limit] or str(fallback[key])

    raw_fixes = obj.get("how_to_fix")
    if isinstance(raw_fixes, str):
        raw_fixes = raw_fixes.splitlines()
    fixes = [" ".join(str(item).split())[:220] for item in (raw_fixes or []) if str(item).strip()][:4]
    return {
        "summary": line("summary", 220),
        "where": line("where", 320),
        "cause": line("cause", 360),
        "how_to_fix": fixes or fallback["how_to_fix"],
        "raw_error": raw_error,
    }


def _message(explanation: dict[str, Any]) -> str:
    fixes = "\n".join(f"- {item}" for item in explanation.get("how_to_fix") or [])
    return (
        f"AI 오류 해석\n\n문제: {explanation['summary']}\n\n"
        f"발생 위치: {explanation['where']}\n\n가능한 원인: {explanation['cause']}\n\n"
        f"해결 방법:\n{fixes}\n\n원문 에러:\n{explanation['raw_error']}"
    )


@router.post("/error/explain")
def explain_error(req: ErrorExplainReq, request: Request):
    current_user(request)
    raw_error = _clip(req.raw_error or req.body)
    fallback = _fallback_error(req, raw_error)
    if not llm_adapter.is_available() or not llm_adapter.should_attempt_llm():
        return {"ok": True, "llm": {"available": llm_adapter.is_available(), "used": False}, "explanation": fallback, "message": raw_error}
    prompt = "Flow 웹앱 오류를 한국어로 설명하고 해결 방법을 제시하세요. 제공된 사실만 사용하세요.\n" + json.dumps({
        "status": req.status,
        "method": req.method,
        "url": req.url,
        "page": req.page,
        "context": _clip(req.context, 1000),
        "body": _clip(req.body, 2500),
        "raw_error": raw_error,
    }, ensure_ascii=False)
    try:
        out = llm_adapter.complete_json(
            prompt,
            system="Return only JSON with summary, where, cause, and how_to_fix. Do not reveal secrets.",
            schema=ERROR_EXPLAIN_SCHEMA,
            timeout=8,
            max_retries=1,
        )
        if not out.get("ok"):
            return {"ok": True, "llm": {"available": True, "used": False}, "explanation": fallback, "message": raw_error}
        explanation = _clean_explanation(out.get("obj"), fallback, raw_error)
        return {"ok": True, "llm": {"available": True, "used": True}, "explanation": explanation, "message": _message(explanation)}
    except Exception:
        logger.warning("error explanation failed", exc_info=True)
        return {"ok": True, "llm": {"available": True, "used": False}, "explanation": fallback, "message": raw_error}


@router.get("/status")
def status(request: Request):
    me = current_user(request)
    try:
        config = llm_adapter.get_config(redact=True)
        available = llm_adapter.is_available()
    except Exception:
        logger.warning("llm status failed", exc_info=True)
        config, available = {}, False
    return {
        "available": available,
        "config": config,
        "native_capabilities": llm_adapter.native_capability_snapshot(),
        "admin": (me.get("role") or "user") == "admin",
    }


@router.post("/test")
def test(req: LLMTestReq, _admin=Depends(require_admin)):
    if not llm_adapter.is_available():
        raise HTTPException(400, "LLM 이 설정되어 있지 않거나 비활성화됨")
    return llm_adapter.complete(req.prompt.strip(), system=req.system)


def _is_gpt_oss_120b() -> bool:
    try:
        model = re.sub(r"[^a-z0-9]", "", str(llm_adapter.get_config(redact=True).get("model") or "").lower())
    except Exception:
        return False
    return "gptoss120b" in model


@router.post("/dcop/summary")
def dcop_summary(req: DcopSummaryReq, request: Request):
    current_user(request)
    if not llm_adapter.is_available() or not _is_gpt_oss_120b() or not llm_adapter.should_attempt_llm():
        return {"ok": True, "used": False, "summary": "", "reason": "gpt_oss_120b_not_connected"}
    findings = [row for row in req.findings[:100] if isinstance(row, dict) and str(row.get("severity") or "").lower() in {"fail", "warning"}]
    if not findings:
        return {"ok": True, "used": False, "summary": "", "reason": "no_findings"}
    out = llm_adapter.complete_json(
        "다음 DCOP FAIL/WARNING을 사실만 사용해 한국어 3문장 이내로 요약하세요.\n" + json.dumps(findings, ensure_ascii=False, default=str),
        system="반도체 DCOP 데이터 품질 검사 결과를 정확하고 짧게 요약합니다.",
        schema=DCOP_SUMMARY_SCHEMA,
        timeout=12,
        max_retries=1,
    )
    summary = str((out.get("obj") or {}).get("summary") or "").strip()
    if not out.get("ok") or not summary:
        return {"ok": True, "used": False, "summary": "", "reason": "llm_call_failed"}
    return {"ok": True, "used": True, "summary": summary[:1200], "model": "gpt-oss-120b"}
