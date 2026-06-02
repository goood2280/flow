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

import configparser
import json
import logging
import os
import re
import subprocess
import threading
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse
from typing import Any, Dict, Optional

from core.paths import PATHS
from core.utils import load_json

logger = logging.getLogger("flow.llm")

ADMIN_SETTINGS_FILE = PATHS.data_root / "admin_settings.json"
_DOTENV_FILE = PATHS.app_root / ".env"
_DOTENV_LOCK = threading.RLock()
_DOTENV_CACHE: Dict[str, Any] = {"path": "", "mtime": None, "values": {}}

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

# --- LLM health circuit breaker -------------------------------------------
# When a live LLM call fails or times out, open a short breaker so the rest of
# a chat turn (and near-future turns) fail fast instead of stacking slow
# timeouts.  The home agent depends on the LLM for node decisions, so when the
# endpoint cannot answer we want a clear, immediate "not connected" result —
# never a multi-minute hang.
_LLM_HEALTH_LOCK = threading.RLock()
_LLM_HEALTH: Dict[str, Any] = {
    "status": "unknown",          # unknown | healthy | unhealthy
    "unhealthy_until": 0.0,
    "last_error": "",
    "last_latency_ms": 0,
    "last_ok_at": 0.0,
    "last_check_at": 0.0,
}


def _llm_breaker_cooldown_s() -> float:
    raw = str(os.environ.get("FLOW_LLM_BREAKER_COOLDOWN_S", "") or "").strip()
    try:
        value = float(raw) if raw else 60.0
    except (TypeError, ValueError):
        value = 60.0
    return max(5.0, min(600.0, value))


def _mark_llm_healthy(latency_ms: int = 0) -> None:
    now = time.time()
    with _LLM_HEALTH_LOCK:
        _LLM_HEALTH.update({
            "status": "healthy",
            "unhealthy_until": 0.0,
            "last_error": "",
            "last_latency_ms": int(latency_ms or 0),
            "last_ok_at": now,
            "last_check_at": now,
        })


def _mark_llm_unhealthy(error: str, latency_ms: int = 0) -> None:
    now = time.time()
    with _LLM_HEALTH_LOCK:
        _LLM_HEALTH.update({
            "status": "unhealthy",
            "unhealthy_until": now + _llm_breaker_cooldown_s(),
            "last_error": str(error or "")[:240],
            "last_latency_ms": int(latency_ms or 0),
            "last_check_at": now,
        })


def should_attempt_llm() -> bool:
    """False while the breaker is open.  Callers gate enhancement/node LLM
    calls on this so one failure short-circuits the rest of a turn."""
    with _LLM_HEALTH_LOCK:
        return time.time() >= float(_LLM_HEALTH.get("unhealthy_until") or 0.0)


def health_snapshot() -> Dict[str, Any]:
    """PII-safe LLM health for verify/status surfaces."""
    now = time.time()
    with _LLM_HEALTH_LOCK:
        unhealthy_until = float(_LLM_HEALTH.get("unhealthy_until") or 0.0)
        return {
            "status": str(_LLM_HEALTH.get("status") or "unknown"),
            "last_error": str(_LLM_HEALTH.get("last_error") or ""),
            "last_latency_ms": int(_LLM_HEALTH.get("last_latency_ms") or 0),
            "breaker_open": now < unhealthy_until,
            "cooldown_remaining_s": max(0, int(unhealthy_until - now)),
        }


def reset_llm_health() -> None:
    """Close the breaker immediately (used by an explicit live verify probe)."""
    with _LLM_HEALTH_LOCK:
        _LLM_HEALTH.update({
            "status": "unknown",
            "unhealthy_until": 0.0,
            "last_error": "",
            "last_latency_ms": 0,
        })


_EXTERNAL_AI_BLOCK_PATHS = (
    "/config/work",
    "/config/work/sharedworkspace",
    "/config/work/sharedworkspace/flow-data",
    "/config/work/sharedworkspace/DB",
)
_OPENAI_FALLBACK_MODEL = "gpt-4o-mini"
_VERTEX_FALLBACK_MODEL = "google/gemini-2.5-flash"
_VERTEX_FALLBACK_LOCATION = "us-central1"
_GOOGLE_ADC_TOKEN_DEFAULT_TTL_S = 45 * 60
_GOOGLE_ADC_TOKEN_MIN_TTL_S = 60
try:
    # gcloud cold start on Windows routinely needs several seconds; a 3s cap
    # meant the token never cached and every call paid the cost then failed.
    _GOOGLE_ADC_TOKEN_MAX_TIMEOUT_S = max(2.0, min(30.0, float(os.environ.get("FLOW_GOOGLE_ADC_TOKEN_TIMEOUT_S") or 8.0)))
except (TypeError, ValueError):
    _GOOGLE_ADC_TOKEN_MAX_TIMEOUT_S = 8.0
_GOOGLE_ADC_TOKEN_CACHE_LOCK = threading.RLock()
_GOOGLE_ADC_TOKEN_CACHE: Dict[str, Any] = {
    "token": "",
    "source": "",
    "fetched_at": 0.0,
    "expires_at": 0.0,
    "last_status": "empty",
}
_GOOGLE_ADC_WARMUP_LOCK = threading.RLock()
_GOOGLE_ADC_WARMUP_ACTIVE = False


def _dotenv_values() -> Dict[str, str]:
    path = _DOTENV_FILE
    try:
        stat = path.stat()
    except OSError:
        return {}
    cache_key = str(path)
    mtime = stat.st_mtime
    with _DOTENV_LOCK:
        if _DOTENV_CACHE.get("path") == cache_key and _DOTENV_CACHE.get("mtime") == mtime:
            return dict(_DOTENV_CACHE.get("values") or {})
        values: Dict[str, str] = {}
        try:
            for raw_line in path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if not key or key.startswith("export "):
                    key = key.removeprefix("export ").strip()
                if not key or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
                    continue
                text = value.strip()
                if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
                    text = text[1:-1]
                values[key] = text
        except Exception as exc:
            logger.debug("local .env unavailable: %s", exc)
            values = {}
        _DOTENV_CACHE.update({"path": cache_key, "mtime": mtime, "values": dict(values)})
        return values


def _env_first(*names: str) -> str:
    local_env: Dict[str, str] | None = None
    for name in names:
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value
        if local_env is None:
            local_env = _dotenv_values()
        value = str((local_env or {}).get(name) or "").strip()
        if value:
            return value
    return ""


def _env_truthy(name: str) -> bool:
    return _env_first(name).lower() in {"1", "true", "yes", "on"}


def _path_exists(path: str) -> bool:
    try:
        text = str(path or "")
        # The external-AI block paths are Linux container locations (e.g.
        # "/config/work").  On Windows a POSIX-absolute path resolves
        # drive-relative (e.g. D:\config\work) and can false-match, wrongly
        # disabling the LLM.  Never honor a POSIX-absolute path on a non-POSIX host.
        if os.name != "posix" and (text.startswith("/") or text.startswith("\\")):
            return False
        return Path(text).exists()
    except Exception:
        return False


def _path_under_config_work(value: Any) -> bool:
    text = str(value or "").strip().replace("\\", "/").rstrip("/")
    return text == "/config/work" or text.startswith("/config/work/")


def _work_config_block_reason() -> str:
    for path in _EXTERNAL_AI_BLOCK_PATHS:
        if _path_exists(path):
            return f"{path} exists"
    for attr in ("data_root", "db_root"):
        value = getattr(PATHS, attr, "")
        if _path_under_config_work(value):
            return f"{value} configured"
    return ""


def _profile_is_playground_connected(profile: Dict[str, Any], *, active: bool = False) -> bool:
    provider = str(profile.get("provider") or "").strip().lower()
    url = str(profile.get("api_url") or "").strip().lower()
    auth_mode = str(profile.get("auth_mode") or "").strip().lower()
    system_name = str(profile.get("system_name") or "").strip().lower()
    enabled = bool(profile.get("enabled"))
    has_connection = bool(url or str(profile.get("admin_token") or "").strip())
    if active and provider == "playground":
        return True
    if provider == "playground":
        return enabled or has_connection
    if "playground" in url or system_name == "playground" or auth_mode == "dep_ticket":
        return enabled or has_connection
    return False


def _playground_profile_block_reason(admin_settings: Dict[str, Any], active_cfg: Dict[str, Any]) -> str:
    if _profile_is_playground_connected(active_cfg, active=True):
        return "playground profile active"
    profiles = admin_settings.get("llm_profiles") if isinstance(admin_settings.get("llm_profiles"), dict) else {}
    for key, raw_profile in profiles.items():
        if not isinstance(raw_profile, dict):
            continue
        profile = dict(raw_profile)
        profile.setdefault("provider", str(key or "").strip().lower())
        if _profile_is_playground_connected(profile):
            return "playground profile configured"
    return ""


def _external_ai_block_reason(admin_settings: Dict[str, Any], active_cfg: Dict[str, Any]) -> str:
    return _work_config_block_reason() or _playground_profile_block_reason(admin_settings, active_cfg)


def _is_external_ai_config(cfg: Dict[str, Any]) -> bool:
    provider = str(cfg.get("provider") or "").strip().lower()
    if provider in {"openai", "vertex_gemini"}:
        return True
    url = str(cfg.get("api_url") or "").strip().lower()
    host = urlparse(url).hostname or ""
    return host.endswith("api.openai.com") or host.endswith("aiplatform.googleapis.com") or host.endswith("generativelanguage.googleapis.com")


def _annotate_external_policy(cfg: Dict[str, Any], reason: str) -> Dict[str, Any]:
    if reason:
        cfg["external_ai_blocked"] = True
        cfg["external_ai_block_reason"] = reason
    else:
        cfg["external_ai_blocked"] = False
        cfg["external_ai_block_reason"] = ""
    return cfg


def _blocked_external_config(cfg: Dict[str, Any], reason: str) -> Dict[str, Any]:
    out = dict(cfg)
    out["enabled"] = False
    out["api_url"] = ""
    out["external_ai_blocked"] = True
    out["external_ai_block_reason"] = reason
    out["blocked_provider"] = str(cfg.get("provider") or "")
    return out


_ALLOWED_PROVIDERS = {"generic", "openai", "openai_compatible", "local", "playground", "vertex_gemini"}


def _normalize_runtime_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    merged = dict(_DEFAULT)
    merged.update({k: v for k, v in raw.items() if k in _DEFAULT})
    merged["enabled"] = bool(merged.get("enabled"))
    merged["api_url"] = str(merged.get("api_url") or "").strip()
    merged["model"] = str(merged.get("model") or "").strip()
    merged["mode"] = str(merged.get("mode") or "fast").strip() or "fast"
    merged["admin_token"] = str(merged.get("admin_token") or "").strip()
    provider = str(merged.get("provider") or "generic").strip().lower() or "generic"
    if provider not in _ALLOWED_PROVIDERS:
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
    merged["format"] = str(merged.get("format") or "openai").strip() or "openai"
    try:
        merged["timeout_s"] = int(merged.get("timeout_s") or 20)
    except Exception:
        merged["timeout_s"] = 20
    if not isinstance(merged.get("headers"), dict):
        merged["headers"] = {}
    if not isinstance(merged.get("extra_body"), dict):
        merged["extra_body"] = {}
    return merged


def _is_connected_internal_ai_config(cfg: Dict[str, Any]) -> bool:
    if not bool(cfg.get("enabled")) or not str(cfg.get("api_url") or "").strip():
        return False
    provider = str(cfg.get("provider") or "").strip().lower()
    if provider not in {"generic", "openai_compatible", "local", "playground"}:
        return False
    return not _is_external_ai_config(cfg)


def _connected_internal_profile(admin_settings: Dict[str, Any]) -> Dict[str, Any]:
    profiles = admin_settings.get("llm_profiles") if isinstance(admin_settings.get("llm_profiles"), dict) else {}
    ordered_keys = ["openai_compatible", "local", "generic"]
    ordered_keys.extend([str(key or "").strip().lower() for key in profiles.keys() if str(key or "").strip().lower() not in ordered_keys])
    for key in ordered_keys:
        raw_profile = profiles.get(key)
        if not isinstance(raw_profile, dict):
            continue
        profile = dict(raw_profile)
        profile["provider"] = str(profile.get("provider") or key).strip().lower()
        normalized = _normalize_runtime_config(profile)
        if normalized.get("provider") == "playground":
            continue
        if _is_connected_internal_ai_config(normalized):
            return normalized
    return {}


def _internal_profile_override(internal_cfg: Dict[str, Any], blocked_cfg: Dict[str, Any], reason: str) -> Dict[str, Any]:
    out = dict(internal_cfg)
    out["source"] = str(out.get("source") or "internal_profile")
    out["dev_ai_blocked"] = True
    out["dev_ai_block_reason"] = reason
    out["blocked_provider"] = str(blocked_cfg.get("provider") or "")
    return out


def _google_credentials_project() -> str:
    path = _env_first("GOOGLE_APPLICATION_CREDENTIALS")
    if not path:
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return str(data.get("project_id") or data.get("quota_project_id") or "").strip()
    except Exception as exc:
        logger.debug("google credentials project unavailable: %s", exc)
    return ""


def _gcloud_config_project() -> str:
    roots: list[Path] = []
    cloud_config = _env_first("CLOUDSDK_CONFIG")
    if cloud_config:
        roots.append(Path(cloud_config).expanduser())
    roots.append(Path.home() / ".config" / "gcloud")
    seen: set[str] = set()
    for root in roots:
        root_key = str(root)
        if root_key in seen:
            continue
        seen.add(root_key)
        try:
            active = (root / "active_config").read_text(encoding="utf-8").strip() or "default"
        except Exception:
            active = "default"
        active = active.removeprefix("config_") or "default"
        candidates = [
            root / "configurations" / f"config_{active}",
            root / "configurations" / "config_default",
        ]
        for path in candidates:
            try:
                parser = configparser.ConfigParser()
                parser.read(path, encoding="utf-8")
                project = str(parser.get("core", "project", fallback="") or "").strip()
                if project and project != "(unset)":
                    return project
            except Exception as exc:
                logger.debug("gcloud config project unavailable from %s: %s", path, exc)
    return ""


def _normalize_vertex_model(model: str) -> str:
    text = str(model or "").strip() or _VERTEX_FALLBACK_MODEL
    if text.startswith("gemini"):
        return f"google/{text}"
    return text


def _openai_env_fallback_config() -> Dict[str, Any]:
    token = _env_first("FLOW_OPENAI_API_KEY", "OPENAI_API_KEY")
    if not token:
        return {}
    cfg = dict(_DEFAULT)
    cfg.update({
        "enabled": True,
        "api_url": _env_first("FLOW_OPENAI_API_URL", "OPENAI_BASE_URL", "OPENAI_API_BASE") or "https://api.openai.com/v1",
        "model": _env_first("FLOW_OPENAI_MODEL", "OPENAI_MODEL", "FLOW_LLM_MODEL") or _OPENAI_FALLBACK_MODEL,
        "admin_token": token,
        "provider": "openai",
        "auth_mode": "bearer",
        "format": "openai",
        "timeout_s": 20,
        "source": "env_fallback",
    })
    return cfg


def _vertex_env_fallback_config() -> Dict[str, Any]:
    project = _env_first(
        "FLOW_VERTEX_PROJECT",
        "VERTEX_PROJECT",
        "GOOGLE_CLOUD_PROJECT",
        "GCLOUD_PROJECT",
        "CLOUDSDK_CORE_PROJECT",
    ) or _google_credentials_project() or _gcloud_config_project()
    if not project:
        return {}
    location = _env_first("FLOW_VERTEX_LOCATION", "VERTEX_LOCATION", "GOOGLE_CLOUD_LOCATION") or _VERTEX_FALLBACK_LOCATION
    model = _normalize_vertex_model(_env_first("FLOW_VERTEX_MODEL", "VERTEX_MODEL", "GOOGLE_VERTEX_MODEL", "FLOW_LLM_MODEL") or _VERTEX_FALLBACK_MODEL)
    cfg = dict(_DEFAULT)
    cfg.update({
        "enabled": True,
        "api_url": _env_first("FLOW_VERTEX_API_URL", "VERTEX_OPENAI_API_URL") or (
            f"https://aiplatform.googleapis.com/v1beta1/projects/{project}/locations/{location}/endpoints/openapi/chat/completions"
        ),
        "model": model,
        "admin_token": "",
        "provider": "vertex_gemini",
        "auth_mode": "google_adc",
        "format": "openai",
        "timeout_s": 30,
        "source": "env_fallback",
    })
    return cfg


def _env_fallback_config(block_reason: str) -> Dict[str, Any]:
    # Opt-in only.  An empty admin api_url must NOT silently enable an external
    # endpoint just because a Google project / OpenAI key happens to be present
    # in the environment — that unavailable->available flip is exactly what made
    # the home agent start hanging.  Operators set FLOW_LLM_ENABLE_ENV_FALLBACK=1
    # once the endpoint is confirmed reachable.
    if block_reason or not _env_truthy("FLOW_LLM_ENABLE_ENV_FALLBACK"):
        return {}
    provider = str(os.environ.get("FLOW_LLM_PROVIDER") or "").strip().lower()
    if provider in {"vertex", "vertex_gemini", "gemini", "google"}:
        return _vertex_env_fallback_config() or _openai_env_fallback_config()
    if provider in {"openai", "gpt", "gpt_mini", "gpt-mini"}:
        return _openai_env_fallback_config() or _vertex_env_fallback_config()
    return _openai_env_fallback_config() or _vertex_env_fallback_config()


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
    if not isinstance(cfg, dict):
        cfg = {}
    admin_settings = cfg
    llm = cfg.get("llm") or {}
    if not isinstance(llm, dict):
        llm = {}
    merged = _normalize_runtime_config(llm)
    block_reason = _external_ai_block_reason(admin_settings, merged)
    internal = _connected_internal_profile(admin_settings)
    if internal and not _is_connected_internal_ai_config(merged):
        out = _internal_profile_override(internal, merged, "connected internal AI profile configured")
        return _annotate_external_policy(out, block_reason)
    if block_reason and _is_external_ai_config(merged) and merged.get("api_url"):
        return _blocked_external_config(merged, block_reason)
    if merged.get("api_url"):
        return _annotate_external_policy(merged, block_reason)
    fallback = _env_fallback_config(block_reason)
    if fallback:
        if internal:
            out = _internal_profile_override(internal, fallback, "connected internal AI profile configured")
            return _annotate_external_policy(out, block_reason)
        return _annotate_external_policy(fallback, "")
    return _annotate_external_policy(merged, block_reason)


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


def list_profiles() -> list[str]:
    """Provider keys with a saved profile in admin_settings.json.

    Returns distinct provider names from `llm_profiles` (if present) and the
    active `llm.provider` (if set). Used by the LLM config UI to render which
    profiles already have a saved entry. Never returns secret values — only
    the key names.
    """
    try:
        adm = load_json(ADMIN_SETTINGS_FILE, {}) or {}
    except Exception:
        adm = {}
    seen: list[str] = []
    profiles = adm.get("llm_profiles") if isinstance(adm.get("llm_profiles"), dict) else {}
    for key in profiles.keys():
        name = str(key or "").strip().lower()
        if name and name not in seen:
            seen.append(name)
    legacy = adm.get("llm") if isinstance(adm.get("llm"), dict) else {}
    active = str(legacy.get("provider") or "").strip().lower()
    if active and active not in seen:
        seen.append(active)
    return seen


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


def _google_adc_bounded_timeout(timeout_s: Any = None) -> float:
    try:
        value = float(timeout_s or _GOOGLE_ADC_TOKEN_MAX_TIMEOUT_S)
    except Exception:
        value = _GOOGLE_ADC_TOKEN_MAX_TIMEOUT_S
    return max(0.5, min(value, _GOOGLE_ADC_TOKEN_MAX_TIMEOUT_S))


def _clear_google_adc_token_cache() -> None:
    with _GOOGLE_ADC_TOKEN_CACHE_LOCK:
        _GOOGLE_ADC_TOKEN_CACHE.update({
            "token": "",
            "source": "",
            "fetched_at": 0.0,
            "expires_at": 0.0,
            "last_status": "empty",
        })


def _google_adc_cached_token(now: float | None = None) -> str:
    now = float(now or time.time())
    with _GOOGLE_ADC_TOKEN_CACHE_LOCK:
        token = str(_GOOGLE_ADC_TOKEN_CACHE.get("token") or "").strip()
        expires_at = float(_GOOGLE_ADC_TOKEN_CACHE.get("expires_at") or 0.0)
        if token and expires_at - now > _GOOGLE_ADC_TOKEN_MIN_TTL_S:
            _GOOGLE_ADC_TOKEN_CACHE["last_status"] = "hit"
            return token
        if token:
            _GOOGLE_ADC_TOKEN_CACHE["last_status"] = "expired"
        else:
            _GOOGLE_ADC_TOKEN_CACHE["last_status"] = "empty"
    return ""


def _store_google_adc_token(token: str, source: str, ttl_s: int = _GOOGLE_ADC_TOKEN_DEFAULT_TTL_S) -> str:
    clean = str(token or "").strip()
    if not clean:
        return ""
    now = time.time()
    with _GOOGLE_ADC_TOKEN_CACHE_LOCK:
        _GOOGLE_ADC_TOKEN_CACHE.update({
            "token": clean,
            "source": str(source or "")[:80],
            "fetched_at": now,
            "expires_at": now + max(_GOOGLE_ADC_TOKEN_MIN_TTL_S + 1, int(ttl_s or _GOOGLE_ADC_TOKEN_DEFAULT_TTL_S)),
            "last_status": "refreshed",
        })
    return clean


def _google_adc_token_cache_status() -> dict[str, Any]:
    now = time.time()
    with _GOOGLE_ADC_WARMUP_LOCK:
        warmup_active = bool(_GOOGLE_ADC_WARMUP_ACTIVE)
    with _GOOGLE_ADC_TOKEN_CACHE_LOCK:
        token = str(_GOOGLE_ADC_TOKEN_CACHE.get("token") or "").strip()
        expires_at = float(_GOOGLE_ADC_TOKEN_CACHE.get("expires_at") or 0.0)
        source = str(_GOOGLE_ADC_TOKEN_CACHE.get("source") or "")
        status = str(_GOOGLE_ADC_TOKEN_CACHE.get("last_status") or "empty")
    valid = bool(token and expires_at - now > _GOOGLE_ADC_TOKEN_MIN_TTL_S)
    return {
        "cached": valid,
        "status": "hit" if valid and status == "hit" else ("cached" if valid else status),
        "source": source if token else "",
        "expires_in_s": max(0, int(expires_at - now)) if token else 0,
        "warmup_active": warmup_active,
    }


def warm_google_adc_token_cache(timeout_s: int | float = 8) -> bool:
    """Start a best-effort ADC token warm-up without blocking request handling."""
    global _GOOGLE_ADC_WARMUP_ACTIVE
    if _google_adc_cached_token():
        return False
    with _GOOGLE_ADC_WARMUP_LOCK:
        if _GOOGLE_ADC_WARMUP_ACTIVE:
            return False
        _GOOGLE_ADC_WARMUP_ACTIVE = True

    def _worker() -> None:
        global _GOOGLE_ADC_WARMUP_ACTIVE
        try:
            # Background warm-up gets the full cap so a slow gcloud cold start on
            # Windows still populates the cache; the request path stays tight.
            _google_adc_access_token(timeout_s=max(2.0, min(float(timeout_s or 8), _GOOGLE_ADC_TOKEN_MAX_TIMEOUT_S)))
        finally:
            with _GOOGLE_ADC_WARMUP_LOCK:
                _GOOGLE_ADC_WARMUP_ACTIVE = False

    thread = threading.Thread(target=_worker, name="flow-google-adc-token-warmup", daemon=True)
    thread.start()
    return True


def _google_auth_adc_access_token(timeout_s: int | float = 8) -> str:
    """Return a Google OAuth token from google-auth ADC, if available."""
    try:
        import google.auth  # type: ignore
        from google.auth.transport.requests import Request  # type: ignore

        creds, _project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        base_request = Request()
        refresh_timeout = _google_adc_bounded_timeout(timeout_s)

        def request_with_timeout(url, method="GET", body=None, headers=None, **kwargs):  # type: ignore[no-untyped-def]
            kwargs.setdefault("timeout", refresh_timeout)
            return base_request(url=url, method=method, body=body, headers=headers, **kwargs)

        creds.refresh(request_with_timeout)
        return str(getattr(creds, "token", "") or "").strip()
    except Exception as import_or_refresh_error:
        logger.debug("google-auth ADC unavailable: %s", import_or_refresh_error)
        return ""


def _gcloud_access_token(args: list[str], *, timeout_s: int, label: str) -> str:
    """Return a gcloud token without logging stdout or other secret-bearing values."""
    timeout_i = _google_adc_bounded_timeout(timeout_s)
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
    cached = _google_adc_cached_token()
    if cached:
        return cached
    timeout_i = _google_adc_bounded_timeout(timeout_s)
    deadline = time.monotonic() + timeout_i

    def remaining_timeout() -> float:
        return max(0.0, min(_GOOGLE_ADC_TOKEN_MAX_TIMEOUT_S, deadline - time.monotonic()))

    token = _google_auth_adc_access_token(timeout_s=remaining_timeout() or timeout_i)
    if token:
        return _store_google_adc_token(token, "google-auth")
    timeout_left = remaining_timeout()
    if timeout_left <= 0.05:
        with _GOOGLE_ADC_TOKEN_CACHE_LOCK:
            _GOOGLE_ADC_TOKEN_CACHE["last_status"] = "miss"
        return ""
    token = _gcloud_access_token(
        ["auth", "application-default", "print-access-token"],
        timeout_s=timeout_left,
        label="gcloud application-default",
    )
    if token:
        return _store_google_adc_token(token, "gcloud application-default")
    timeout_left = remaining_timeout()
    if timeout_left <= 0.05:
        with _GOOGLE_ADC_TOKEN_CACHE_LOCK:
            _GOOGLE_ADC_TOKEN_CACHE["last_status"] = "miss"
        return ""
    token = _gcloud_access_token(
        ["auth", "print-access-token"],
        timeout_s=timeout_left,
        label="gcloud user",
    )
    if token:
        return _store_google_adc_token(token, "gcloud user")
    with _GOOGLE_ADC_TOKEN_CACHE_LOCK:
        _GOOGLE_ADC_TOKEN_CACHE["last_status"] = "miss"
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


def _call_summary(cfg: Dict[str, Any], *, prompt_chars: int = 0, response_chars: int = 0,
                  started_at: float = 0.0, ok: bool = False, error: str = "") -> Dict[str, Any]:
    """Safe call metadata for the thought trace. Never includes prompt/response text."""
    elapsed = max(0.0, time.monotonic() - started_at) if started_at else 0.0
    return {
        "invoked": True,
        "ok": bool(ok),
        "model": str(cfg.get("model") or "").strip(),
        "profile": str(cfg.get("provider") or "").strip(),
        "provider": str(cfg.get("provider") or "").strip(),
        "prompt_chars": int(prompt_chars or 0),
        "response_chars": int(response_chars or 0),
        "latency_ms": int(elapsed * 1000),
        "error": str(error or "")[:200],
    }


def complete(prompt: str, *, system: Optional[str] = None,
             timeout: Optional[int] = None,
             auth_token: Optional[str] = None,
             probe: bool = False) -> Dict[str, Any]:
    """단일 프롬프트 완성.  실패 시 {"ok":False, "error":...} 반환 (절대 throw 하지 않음).

    사내 LLM 이 `openai` 호환이면 messages 형식으로 POST.  `raw` 면 {"prompt": ...}.
    extra_body 로 temperature/top_p 등 추가 가능.

    응답에는 PII-safe `meta` 필드가 함께 담긴다 — prompt_chars / response_chars /
    latency_ms / model / provider 만 노출하고 본문은 절대 넣지 않는다.
    """
    if not prompt or not isinstance(prompt, str):
        return {"ok": False, "text": "", "error": "empty prompt",
                "meta": {"invoked": False, "ok": False, "model": "", "profile": "", "provider": "",
                         "prompt_chars": 0, "response_chars": 0, "latency_ms": 0, "error": "empty prompt"}}
    cfg = _raw_config()
    if not cfg.get("enabled"):
        return {"ok": False, "text": "", "error": "llm disabled",
                "meta": _call_summary(cfg, prompt_chars=len(prompt), error="llm disabled")}
    if not probe and not should_attempt_llm():
        # Breaker open: a recent live call failed or timed out.  Fail fast so one
        # chat turn doesn't stack several slow timeouts.  An explicit verify probe
        # (probe=True) bypasses this to re-test whether the endpoint recovered.
        with _LLM_HEALTH_LOCK:
            reason = str(_LLM_HEALTH.get("last_error") or "recent llm failure")
        return {"ok": False, "text": "", "error": ("llm circuit breaker open: " + reason)[:240],
                "meta": _call_summary(cfg, prompt_chars=len(prompt), error="llm circuit breaker open")}
    fmt = cfg.get("format") or "openai"
    url = _openai_chat_url(cfg.get("api_url") or "", fmt)
    if not url:
        return {"ok": False, "text": "", "error": "llm api_url missing",
                "meta": _call_summary(cfg, prompt_chars=len(prompt), error="llm api_url missing")}
    body = _build_request_body(cfg, prompt, system)
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    to = int(timeout or cfg.get("timeout_s") or 20)
    hdrs = _build_request_headers(cfg, auth_token=auth_token, timeout_s=to)
    if str(cfg.get("auth_mode") or "").strip().lower() == "google_adc" and "Authorization" not in hdrs:
        _mark_llm_unhealthy("google adc token unavailable")
        return {"ok": False, "text": "", "error": "google adc token unavailable",
                "meta": _call_summary(cfg, prompt_chars=len(prompt), error="google adc token unavailable")}
    last_error = ""
    started_at = time.monotonic()
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
            with urllib.request.urlopen(req, timeout=to) as resp:
                raw = resp.read(1024 * 1024).decode("utf-8", errors="replace")
            _mark_llm_healthy(int((time.monotonic() - started_at) * 1000))
            try:
                obj = json.loads(raw)
            except Exception:
                return {"ok": True, "text": raw, "raw": raw,
                        "meta": _call_summary(cfg, prompt_chars=len(prompt), response_chars=len(raw),
                                              started_at=started_at, ok=True)}
            text = _extract_response_text(obj)
            return {"ok": True, "text": text, "raw": obj,
                    "meta": _call_summary(cfg, prompt_chars=len(prompt), response_chars=len(text or ""),
                                          started_at=started_at, ok=True)}
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
            _mark_llm_unhealthy(last_error, int((time.monotonic() - started_at) * 1000))
            return {"ok": False, "text": "", "error": last_error, "status_code": e.code,
                    "meta": _call_summary(cfg, prompt_chars=len(prompt), started_at=started_at, error=last_error)}
        except Exception as e:
            last_error = _redact_error_text(e)
            logger.warning("llm error: %s", last_error)
            _mark_llm_unhealthy(last_error, int((time.monotonic() - started_at) * 1000))
            return {"ok": False, "text": "", "error": last_error,
                    "meta": _call_summary(cfg, prompt_chars=len(prompt), started_at=started_at, error=last_error)}
    _mark_llm_unhealthy(last_error or "llm request failed", int((time.monotonic() - started_at) * 1000))
    return {"ok": False, "text": "", "error": last_error or "llm request failed",
            "meta": _call_summary(cfg, prompt_chars=len(prompt), started_at=started_at,
                                  error=last_error or "llm request failed")}


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
