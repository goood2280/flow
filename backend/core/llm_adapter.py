"""core/llm_adapter.py v8.7.7 — 사내 LLM API 선택적 어댑터 (infrastructure only).

핵심 정책:
  - LLM 은 100% 옵션.  설정이 없거나 연결 실패해도 앱은 정상 동작.
  - 사내 LLM 은 오픈소스 파인튜닝 수준이라 성능이 낮음 → 프롬프트는 최대한 단순하게 쓰고,
    caller 는 항상 수동 fallback 을 준비해야 함.
  - 설정 저장 위치: {data_root}/admin_settings.json 의 "llm" 블록.

설정 스키마 (admin_settings.json → "llm"):
  {
    "enabled":   bool,
    "api_url":   str,            # POST 대상 (예: https://llm.internal/v1/chat)
    "model":     str,            # e.g. "gpt-oss-120b"
    "mode":      str,            # e.g. "fast"
    "admin_token": str,           # admin-managed credential shared by users
    "provider":  "generic"|"openai"|"openai_compatible"|"local"|"playground"|"vertex_gemini",
    "auth_mode": "bearer"|"dep_ticket"|"google_adc"|"none",
    "system_name": str,           # playground header Send-System-Name
    "user_id":   str,             # playground header User-Id
    "user_type": str,             # playground header User-Type
    "headers":   {k: v, ...},    # 인증 헤더 등
    "format":    "openai"|"raw"|"vertex_gemini", # 요청 body 스키마.  default "openai" (messages:[{role,content}])
    "extra_body":{k: v, ...},    # POST body 병합 (예: {"temperature":0.2})
    "timeout_s": int,            # 기본 20
  }

모듈 API:
  is_available() -> bool                          설정/활성화 여부만 (실제 연결 검사는 안 함)
  get_config()   -> dict                          redacted 설정 (headers 값 masked)
  set_config(cfg: dict)                           admin 이 POST /api/admin/settings/save 로만 호출
  complete(prompt: str, *, system=None, timeout=None) -> {"ok":bool, "text":str, "error":str}
  complete_json(prompt: str, *, system=None, schema=None, timeout=None) -> {"ok":bool, "obj":dict, ...}
                                                  실패 시 {"ok":False,"error":...}, text 는 빈 문자열.

caller 규약:
  - UI 에서 LLM 관련 버튼/패널은 is_available() 이 True 일 때만 노출.
  - 실패/미설정 상태는 throw 가 아니라 {"ok":False} 응답으로 처리.
  - 반드시 수동 fallback (유저가 직접 입력) 을 제공.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import time
import uuid
import urllib.error
import urllib.request
from urllib.parse import urlparse
from typing import Any, Dict, Optional

from core.paths import PATHS
from core.utils import load_json

logger = logging.getLogger("flow.llm")

ADMIN_SETTINGS_FILE = PATHS.data_root / "admin_settings.json"

_DEFAULT: Dict[str, Any] = {
    "enabled": False,
    "api_url": "",
    "model": "",
    "mode": "fast",
    "admin_token": "",
    "provider": "generic",
    "auth_mode": "",
    "system_name": "",
    "user_id": "",
    "user_type": "",
    "headers": {},
    "format": "openai",
    "extra_body": {},
    "timeout_s": 20,
}


def _is_vertex_openai_compatible_config(cfg: Dict[str, Any]) -> bool:
    """Detect Google Vertex OpenAI-compatible Gemini endpoint profiles."""
    url = str(cfg.get("api_url") or "").strip().lower()
    model = str(cfg.get("model") or "").strip().lower()
    return (
        "aiplatform.googleapis.com" in url
        and "/openapi/" in url
        and (model.startswith("google/gemini") or model.startswith("gemini"))
    )


def _raw_config() -> Dict[str, Any]:
    try:
        cfg = load_json(ADMIN_SETTINGS_FILE, {}) or {}
    except Exception:
        cfg = {}
    llm = cfg.get("llm") or {}
    if not isinstance(llm, dict):
        llm = {}
    merged = dict(_DEFAULT)
    merged.update({k: v for k, v in llm.items() if k in _DEFAULT})
    # ensure types
    merged["enabled"] = bool(merged.get("enabled"))
    merged["api_url"] = str(merged.get("api_url") or "").strip()
    merged["model"] = str(merged.get("model") or "").strip()
    merged["mode"] = str(merged.get("mode") or "fast").strip() or "fast"
    merged["admin_token"] = str(merged.get("admin_token") or "").strip()
    provider = str(merged.get("provider") or "generic").strip().lower() or "generic"
    if provider not in {"generic", "openai", "openai_compatible", "local", "playground", "vertex_gemini"}:
        provider = "generic"
    merged["provider"] = provider
    auth_mode = str(merged.get("auth_mode") or "").strip().lower()
    if not auth_mode:
        if provider == "playground":
            auth_mode = "dep_ticket"
        elif provider == "local":
            auth_mode = "none"
        elif provider == "vertex_gemini":
            auth_mode = "google_adc"
        else:
            auth_mode = "bearer"
    if auth_mode not in {"bearer", "dep_ticket", "google_adc", "none"}:
        auth_mode = "bearer"
    merged["auth_mode"] = auth_mode
    merged["system_name"] = str(merged.get("system_name") or "").strip()
    if provider == "playground" and not merged["system_name"]:
        merged["system_name"] = "playground"
    if provider in {"local", "openai_compatible"} and not merged["model"]:
        merged["model"] = "gpt-oss-120b"
    if provider == "vertex_gemini" and not merged["model"]:
        merged["model"] = "google/gemini-2.5-flash"
    if _is_vertex_openai_compatible_config(merged):
        # Vertex's OpenAI-compatible Gemini endpoint must use a fresh Google
        # OAuth token. A persisted bearer token expires quickly and causes
        # Home Flow-i verification to fail with HTTP 401.
        merged["auth_mode"] = "google_adc"
        merged["format"] = "openai"
        if provider == "generic":
            merged["provider"] = "openai_compatible"
        merged["admin_token"] = ""
    merged["user_id"] = str(merged.get("user_id") or "").strip()
    merged["user_type"] = str(merged.get("user_type") or "").strip()
    merged["format"] = (merged.get("format") or "openai").strip() or "openai"
    try:
        merged["timeout_s"] = int(merged.get("timeout_s") or 20)
    except Exception:
        merged["timeout_s"] = 20
    if not isinstance(merged.get("headers"), dict):
        merged["headers"] = {}
    if not isinstance(merged.get("extra_body"), dict):
        merged["extra_body"] = {}
    return merged


def is_available() -> bool:
    """활성 + URL 이 있어야 available.  실제 요청은 complete() 에서만 수행."""
    cfg = _raw_config()
    return bool(cfg.get("enabled")) and bool(cfg.get("api_url"))


def get_config(*, redact: bool = True) -> Dict[str, Any]:
    cfg = _raw_config()
    if redact:
        # 헤더 값은 민감할 수 있으므로 key 는 노출하고 값은 masking.
        cfg = dict(cfg)
        cfg["headers"] = {k: ("****" if v else "") for k, v in (cfg.get("headers") or {}).items()}
        cfg["admin_token"] = "****" if cfg.get("admin_token") else ""
    return cfg


def has_admin_token() -> bool:
    """True when an admin-managed token is configured."""
    return bool(_raw_config().get("admin_token"))


def _openai_chat_url(url: str, fmt: str) -> str:
    """Accept either a full OpenAI-compatible endpoint or a `/v1` base URL."""
    url = str(url or "").strip()
    if (fmt or "openai") != "openai":
        return url
    clean = url.rstrip("/")
    if clean.endswith("/v1"):
        return clean + "/chat/completions"
    parsed = urlparse(clean)
    if parsed.path in ("", "/"):
        return clean + "/v1/chat/completions"
    return url


def _content_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item or ""))
        return "".join(parts)
    return str(value)


def _extract_response_text(obj: Any) -> str:
    if not isinstance(obj, dict):
        return _content_text(obj).strip()
    try:
        ch = obj.get("choices") or []
        if ch:
            first = ch[0] or {}
            msg = first.get("message") or first.get("delta") or {}
            text = _content_text(msg.get("content") if isinstance(msg, dict) else "")
            if not text:
                text = _content_text(first.get("text"))
            if text:
                return text.strip()
    except Exception:
        pass
    text = _content_text(obj.get("output_text") or obj.get("text") or obj.get("response"))
    if text:
        return text.strip()
    out = obj.get("output") or []
    if isinstance(out, list):
        parts = []
        for item in out:
            if not isinstance(item, dict):
                continue
            for content in item.get("content") or []:
                if isinstance(content, dict):
                    parts.append(_content_text(content.get("text") or content.get("content")))
        if parts:
            return "".join(parts).strip()
    candidates = obj.get("candidates") or []
    if isinstance(candidates, list):
        parts = []
        for cand in candidates:
            if not isinstance(cand, dict):
                continue
            content = cand.get("content") if isinstance(cand.get("content"), dict) else {}
            for part in content.get("parts") or []:
                if isinstance(part, dict):
                    parts.append(_content_text(part.get("text")))
        if parts:
            return "".join(parts).strip()
    return ""


def _set_header(headers: Dict[str, str], name: str, value: Any) -> None:
    text = str(value or "").strip()
    if not text:
        return
    for key in list(headers.keys()):
        if key.lower() == name.lower() and key != name:
            headers.pop(key, None)
    headers[name] = text


def _replace_header_tokens(value: Any, *, token: str, prompt_msg_id: str,
                           completion_msg_id: str, cfg: Dict[str, Any]) -> str:
    text = str(value)
    replacements = {
        "{token}": token,
        "{prompt_msg_id}": prompt_msg_id,
        "{completion_msg_id}": completion_msg_id,
        "{system_name}": str(cfg.get("system_name") or ""),
        "{user_id}": str(cfg.get("user_id") or ""),
        "{user_type}": str(cfg.get("user_type") or ""),
    }
    for key, val in replacements.items():
        text = text.replace(key, val)
    return text


def _google_auth_adc_access_token() -> str:
    """Return a Google OAuth token from google-auth ADC, if available."""
    try:
        import google.auth  # type: ignore
        from google.auth.transport.requests import Request  # type: ignore

        creds, _project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(Request())
        return str(getattr(creds, "token", "") or "").strip()
    except Exception as import_or_refresh_error:
        logger.debug("google-auth ADC unavailable: %s", import_or_refresh_error)
        return ""


def _gcloud_access_token(args: list[str], *, timeout_s: int, label: str) -> str:
    """Return a gcloud token without logging stdout or other secret-bearing values."""
    timeout_i = max(2, min(int(timeout_s or 8), 8))
    try:
        proc = subprocess.run(
            ["gcloud", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_i,
        )
        if proc.returncode == 0:
            return str(proc.stdout or "").strip()
        logger.debug("%s token failed: %s", label, (proc.stderr or "")[:200])
    except Exception as cli_error:
        logger.debug("%s unavailable: %s", label, cli_error)
    return ""


def _google_adc_access_token(timeout_s: int = 8) -> str:
    """Return a Google OAuth access token from ADC, without requiring google-auth at import time."""
    timeout_i = max(2, min(int(timeout_s or 8), 8))
    token = _google_auth_adc_access_token()
    if token:
        return token
    token = _gcloud_access_token(
        ["auth", "application-default", "print-access-token"],
        timeout_s=timeout_i,
        label="gcloud application-default",
    )
    if token:
        return token
    token = _gcloud_access_token(
        ["auth", "print-access-token"],
        timeout_s=timeout_i,
        label="gcloud user",
    )
    if token:
        return token
    return ""


def _build_request_headers(cfg: Dict[str, Any], *,
                           auth_token: Optional[str] = None,
                           prompt_msg_id: Optional[str] = None,
                           completion_msg_id: Optional[str] = None,
                           timeout_s: Optional[int] = None) -> Dict[str, str]:
    """Build outbound LLM headers while keeping credentials server-side."""
    prompt_id = prompt_msg_id or str(uuid.uuid4())
    completion_id = completion_msg_id or str(uuid.uuid4())
    auth_mode = str(cfg.get("auth_mode") or "bearer").strip().lower()
    token = str(auth_token or ("" if auth_mode == "google_adc" else cfg.get("admin_token")) or "").strip()
    headers: Dict[str, str] = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    for k, v in (cfg.get("headers") or {}).items():
        if not k:
            continue
        _set_header(
            headers,
            str(k),
            _replace_header_tokens(
                v,
                token=token,
                prompt_msg_id=prompt_id,
                completion_msg_id=completion_id,
                cfg=cfg,
            ),
        )

    if auth_mode == "bearer" and token:
        _set_header(headers, "Authorization", f"Bearer {token}")
    elif auth_mode == "dep_ticket" and token:
        _set_header(headers, "x-dep-ticket", token)
    elif auth_mode == "google_adc":
        for key in [k for k in headers if k.lower() == "authorization"]:
            headers.pop(key, None)
        google_token = str(auth_token or "").strip() or _google_adc_access_token(
            timeout_s=timeout_s or cfg.get("timeout_s") or 8
        )
        if google_token:
            _set_header(headers, "Authorization", f"Bearer {google_token}")

    if str(cfg.get("provider") or "").strip().lower() == "playground":
        _set_header(headers, "Send-System-Name", cfg.get("system_name") or "playground")
        _set_header(headers, "User-Id", cfg.get("user_id") or "")
        _set_header(headers, "User-Type", cfg.get("user_type") or "")
        _set_header(headers, "Prompt-Msg-Id", prompt_id)
        _set_header(headers, "Completion-Msg-Id", completion_id)
    return headers


def _build_request_body(cfg: Dict[str, Any], prompt: str,
                        system: Optional[str] = None) -> Dict[str, Any]:
    fmt = cfg.get("format") or "openai"
    provider = str(cfg.get("provider") or "generic").strip().lower()
    model = cfg.get("model") or ""
    mode = str(cfg.get("mode") or "").strip()
    body: Dict[str, Any] = dict(cfg.get("extra_body") or {})
    if provider == "playground":
        body.setdefault("temperature", 0.5)
        body.setdefault("stream", False)
    elif provider == "generic" and mode and "mode" not in body:
        body["mode"] = mode
    if fmt == "vertex_gemini":
        body["contents"] = [{"role": "user", "parts": [{"text": prompt}]}]
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        body.setdefault("generationConfig", {})
        return body
    if fmt == "openai":
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": prompt})
        body["messages"] = msgs
        if model:
            body["model"] = model
    else:
        body["prompt"] = prompt
        if system:
            body["system"] = system
        if model:
            body["model"] = model
    return body


def _parse_json_object(text: str, *, required: Optional[list[str]] = None,
                       keys: Optional[list[str]] = None) -> tuple[Optional[Dict[str, Any]], str]:
    raw = str(text or "").strip()
    if not raw:
        return None, "empty"
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I | re.S).strip()
    candidates = [raw]
    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        candidates.append(match.group(0))
    last_error = "not json"
    for candidate in candidates:
        try:
            obj = json.loads(candidate)
        except Exception as exc:
            last_error = f"json parse error: {exc}"
            continue
        if not isinstance(obj, dict):
            last_error = "not object"
            continue
        missing = [k for k in (required or []) if k not in obj]
        if missing:
            return None, "missing " + ", ".join(missing)
        if keys:
            obj = {k: obj.get(k) for k in keys if k in obj}
        return obj, ""
    return None, last_error


def _redact_error_text(text: Any) -> str:
    """Keep adapter errors useful without returning credential-like strings."""
    out = str(text or "")
    if not out:
        return ""
    out = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]{12,}", "Bearer <redacted>", out, flags=re.I)
    out = re.sub(r"ya29\.[A-Za-z0-9._~+/=-]+", "ya29.<redacted>", out)
    out = re.sub(r"sk-[A-Za-z0-9._~+/=-]{12,}", "sk-<redacted>", out)
    return out[:240]


def complete(prompt: str, *, system: Optional[str] = None,
             timeout: Optional[int] = None,
             auth_token: Optional[str] = None) -> Dict[str, Any]:
    """단일 프롬프트 완성.  실패 시 {"ok":False, "error":...} 반환 (절대 throw 하지 않음).

    사내 LLM 이 `openai` 호환이면 messages 형식으로 POST.  `raw` 면 {"prompt": ...}.
    extra_body 로 temperature/top_p 등 추가 가능.
    """
    if not prompt or not isinstance(prompt, str):
        return {"ok": False, "text": "", "error": "empty prompt"}
    cfg = _raw_config()
    if not cfg.get("enabled"):
        return {"ok": False, "text": "", "error": "llm disabled"}
    fmt = cfg.get("format") or "openai"
    url = _openai_chat_url(cfg.get("api_url") or "", fmt)
    if not url:
        return {"ok": False, "text": "", "error": "llm api_url missing"}
    body = _build_request_body(cfg, prompt, system)
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    to = int(timeout or cfg.get("timeout_s") or 20)
    hdrs = _build_request_headers(cfg, auth_token=auth_token, timeout_s=to)
    if str(cfg.get("auth_mode") or "").strip().lower() == "google_adc" and "Authorization" not in hdrs:
        return {"ok": False, "text": "", "error": "google adc token unavailable"}
    last_error = ""
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
            with urllib.request.urlopen(req, timeout=to) as resp:
                raw = resp.read(1024 * 1024).decode("utf-8", errors="replace")
            try:
                obj = json.loads(raw)
            except Exception:
                return {"ok": True, "text": raw, "raw": raw}
            text = _extract_response_text(obj)
            return {"ok": True, "text": text, "raw": obj}
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read(512).decode("utf-8", errors="replace")
            except Exception:
                pass
            safe_detail = _redact_error_text(detail)
            last_error = f"HTTP {e.code}: {safe_detail}"
            logger.warning("llm HTTPError %s: %s", e.code, safe_detail)
            if e.code == 429 and attempt == 0:
                try:
                    delay = max(0.2, min(2.5, float(e.headers.get("Retry-After") or 0.8)))
                except Exception:
                    delay = 0.8
                time.sleep(delay)
                continue
            return {"ok": False, "text": "", "error": last_error, "status_code": e.code}
        except Exception as e:
            last_error = _redact_error_text(e)
            logger.warning("llm error: %s", last_error)
            return {"ok": False, "text": "", "error": last_error}
    return {"ok": False, "text": "", "error": last_error or "llm request failed"}


def complete_json(prompt: str, *, system: Optional[str] = None,
                  schema: Optional[Dict[str, Any]] = None,
                  timeout: Optional[int] = None,
                  max_retries: int = 1) -> Dict[str, Any]:
    """Complete a prompt and return a schema-checked JSON object or a safe failure."""
    if not prompt or not isinstance(prompt, str):
        return {"ok": False, "obj": {}, "text": "", "error": "empty prompt", "attempts": 0}
    schema = schema if isinstance(schema, dict) else {}
    keys = list((schema.get("properties") or {}).keys()) or list(schema.get("keys") or [])
    required = list(schema.get("required") or [])
    system_text = (system or "") + "\n\nReturn only one valid JSON object. No prose. No markdown fences."
    last_error = ""
    raw_text = ""
    attempts = max(0, int(max_retries or 0)) + 1
    for attempt in range(attempts):
        ask = prompt
        if attempt:
            ask = (
                "Repair the previous answer. It was invalid because: "
                f"{last_error or 'schema mismatch'}. Return only valid JSON with keys {keys}.\n\n"
                f"Original request:\n{prompt}\n\nPrevious answer:\n{raw_text[:2000]}"
            )
        out = complete(ask, system=system_text, timeout=timeout)
        if not out.get("ok") or not out.get("text"):
            last_error = str(out.get("error") or "empty")
            return {"ok": False, "obj": {}, "text": "", "error": last_error, "attempts": attempt + 1}
        raw_text = str(out.get("text") or "")
        obj, parse_error = _parse_json_object(raw_text, required=required, keys=keys)
        if obj is not None:
            return {
                "ok": True,
                "obj": obj,
                "text": raw_text,
                "error": "",
                "attempts": attempt + 1,
                "repaired": attempt > 0,
            }
        last_error = parse_error
    return {"ok": False, "obj": {}, "text": raw_text, "error": last_error or "json schema validation failed", "attempts": attempts}
