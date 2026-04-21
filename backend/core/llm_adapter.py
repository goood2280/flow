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
    "model":     str,            # e.g. "internal-7b"
    "headers":   {k: v, ...},    # 인증 헤더 등
    "format":    "openai"|"raw", # 요청 body 스키마.  default "openai" (messages:[{role,content}])
    "extra_body":{k: v, ...},    # POST body 병합 (예: {"temperature":0.2})
    "timeout_s": int,            # 기본 20
  }

모듈 API:
  is_available() -> bool                          설정/활성화 여부만 (실제 연결 검사는 안 함)
  get_config()   -> dict                          redacted 설정 (headers 값 masked)
  set_config(cfg: dict)                           admin 이 POST /api/admin/settings/save 로만 호출
  complete(prompt: str, *, system=None, timeout=None) -> {"ok":bool, "text":str, "error":str}
                                                  실패 시 {"ok":False,"error":...}, text 는 빈 문자열.

caller 규약:
  - UI 에서 LLM 관련 버튼/패널은 is_available() 이 True 일 때만 노출.
  - 실패/미설정 상태는 throw 가 아니라 {"ok":False} 응답으로 처리.
  - 반드시 수동 fallback (유저가 직접 입력) 을 제공.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from core.paths import PATHS
from core.utils import load_json

logger = logging.getLogger("holweb.llm")

ADMIN_SETTINGS_FILE = PATHS.data_root / "admin_settings.json"

_DEFAULT: Dict[str, Any] = {
    "enabled": False,
    "api_url": "",
    "model": "",
    "headers": {},
    "format": "openai",
    "extra_body": {},
    "timeout_s": 20,
}


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
    return cfg


def complete(prompt: str, *, system: Optional[str] = None,
             timeout: Optional[int] = None) -> Dict[str, Any]:
    """단일 프롬프트 완성.  실패 시 {"ok":False, "error":...} 반환 (절대 throw 하지 않음).

    사내 LLM 이 `openai` 호환이면 messages 형식으로 POST.  `raw` 면 {"prompt": ...}.
    extra_body 로 temperature/top_p 등 추가 가능.
    """
    if not prompt or not isinstance(prompt, str):
        return {"ok": False, "text": "", "error": "empty prompt"}
    cfg = _raw_config()
    if not cfg.get("enabled"):
        return {"ok": False, "text": "", "error": "llm disabled"}
    url = cfg.get("api_url") or ""
    if not url:
        return {"ok": False, "text": "", "error": "llm api_url missing"}
    fmt = cfg.get("format") or "openai"
    model = cfg.get("model") or ""
    body: Dict[str, Any] = dict(cfg.get("extra_body") or {})
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
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    hdrs = {"Content-Type": "application/json"}
    for k, v in (cfg.get("headers") or {}).items():
        if k:
            hdrs[str(k)] = str(v)
    to = int(timeout or cfg.get("timeout_s") or 20)
    try:
        req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
        with urllib.request.urlopen(req, timeout=to) as resp:
            raw = resp.read(1024 * 1024).decode("utf-8", errors="replace")
        try:
            obj = json.loads(raw)
        except Exception:
            return {"ok": True, "text": raw, "raw": raw}
        # openai compatibility
        text = ""
        try:
            ch = (obj.get("choices") or [])
            if ch:
                msg = ch[0].get("message") or {}
                text = (msg.get("content") or ch[0].get("text") or "").strip()
        except Exception:
            text = ""
        if not text:
            text = str(obj.get("text") or obj.get("response") or "")
        return {"ok": True, "text": text, "raw": obj}
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read(512).decode("utf-8", errors="replace")
        except Exception:
            pass
        logger.warning("llm HTTPError %s: %s", e.code, detail[:200])
        return {"ok": False, "text": "", "error": f"HTTP {e.code}: {detail[:200]}"}
    except Exception as e:
        logger.warning("llm error: %s", e)
        return {"ok": False, "text": "", "error": f"{e}"}
