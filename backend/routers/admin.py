"""routers/admin.py v8.4.6 - Admin: users/permissions/logs/notify/downloads + batch dismiss + global settings + data_roots.

v8.4.6 보안 패치:
  - 모든 admin 전용 엔드포인트에 Depends(require_admin) 추가 → curl 로 role 우회 불가.
  - /users 응답에서 password_hash 제거.
  - /reset-password 는 임시 랜덤 비번 발급 후 설정된 메일 도메인으로 사용자에게 발송.
  - /my-notifications · /user-tabs · /log 은 본인 또는 admin 만 접근 (verify_owner).
  - /settings 의 data_roots 는 admin 요청에만 노출 (일반 유저는 숨김).

v8.7.3 hotfix:
  - MailCfgReq.extra_data 의 `Dict[str, Any]` 가 `Any` 미-import 로 import-time
    NameError 를 일으켜 admin 라우터 로딩이 실패하던 문제 수정. `Any` 를 typing
    import 에 추가.
"""
import html, os, secrets, subprocess, sys
import datetime as dt
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query, Depends, Request
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from core.paths import PATHS
from core.utils import jsonl_read, load_json, save_json
from core.notify import (
    send_notify, get_notifications, mark_all_read, send_to_admins,
    dismiss_notification, dismiss_by_ids, mark_read_by_ids,
)
from routers.auth import read_users, write_users
from core.auth import canonical_page_id, canonical_tab_token, canonical_username, effective_permissions, get_page_admins, require_admin, current_user, validate_username, verify_owner
from core.audit import ACTIVITY_LOG_MAX_BYTES, append_activity, record as _audit
from core import s3_sync as _s3
from core import root_profile
from core.tracker_schema import migrate_tracker_issues_file

router = APIRouter(prefix="/api/admin", tags=["admin"])
FLOW_ROOT = Path(__file__).resolve().parents[2]
QA_REPORT_FILE = PATHS.data_root / "qa_report.json"
QA_SCRIPT = FLOW_ROOT / "scripts" / "e2e_qa.py"


def _is_admin(username: str) -> bool:
    """다른 라우터(filebrowser, splittable 등) 가 import 해서 씀. Back-compat."""
    if not username:
        return False
    try:
        for u in read_users():
            if u.get("username") == username and u.get("role") == "admin":
                return True
    except Exception:
        pass
    return False


def _scrub_user(u: dict) -> dict:
    """응답 직렬화 시 password_hash 제거."""
    out = {k: v for k, v in u.items() if k != "password_hash"}
    out["effective_permissions"] = effective_permissions(u)
    return out
DL_LOG = PATHS.download_log
ACTIVITY_LOG = PATHS.activity_log
SETTINGS_FILE = PATHS.data_root / "settings.json"
# v8.3.0: data_roots runtime overrides live in a separate file that core/roots.py
# read-peeks. Kept distinct from settings.json so legacy UI/refresh settings and
# root-path overrides have independent schemas.
ADMIN_SETTINGS_FILE = PATHS.data_root / "admin_settings.json"
DEFAULT_SETTINGS = {
    "dashboard_refresh_minutes": 10,  # auto-refresh interval (frontend)
    "dashboard_bg_refresh_minutes": 10,  # backend scheduled recompute (if any)
    "lot_progress_refresh_minutes": 30,  # LOT progress latest cache rebuild interval
    "splittable_match_refresh_minutes": 30,  # legacy compatibility only
    "tracker_et_match_refresh_minutes": 30,  # ET root/fab_lot cache rebuild interval
    "dashboard_fab_progress": {
        "reference_step_id": "AA200000",
        "sample_lots": 3,
        "days": 30,
    },
    # Dashboard section visibility for non-admin users. Admin always sees all.
    "dashboard_sections": {"charts": True, "progress": False, "alerts": False},
}
FLOWI_DEFAULT_SETTINGS = {
    "chart_defaults": {
        "surface": "home_flowi",
        "scatter": {"grain": "wafer_agg", "max_points": 500, "inline_agg": "avg", "et_agg": "median"},
        "line": {"grain": "wafer_agg", "max_points_per_series": 120},
        "bar": {"top_n": 12, "other_bucket": True},
        "pie": {"max_slices": 6, "other_bucket": True},
        "box": {"max_groups": 12, "min_n": 3},
    },
    "feedback_policy": {
        "auto_apply_to_rag": False,
        "review_required": True,
        "promotion_target": "golden_cases",
    },
    "engineer_knowledge": {
        "rag_update_requires_marker": True,
        "admin_review_required": True,
        "custom_knowledge_append_only": True,
    },
    # Home Flow-i 에이전틱 오케스트레이션 — env FLOW_LLM_TOOL_CALL /
    # FLOW_LLM_REACT_LOOP 가 설정돼 있으면 env 가 우선한다.
    "agentic": {
        "tool_call_enabled": False,
        "react_loop_enabled": False,
    },
}

LLM_PROFILE_KEYS = (
    "enabled", "api_url", "model", "mode", "admin_token", "provider", "auth_mode",
    "system_name", "user_id", "user_type", "headers", "format", "extra_body", "timeout_s",
)
LLM_PROVIDER_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "openai": {
        "enabled": False, "api_url": "", "model": "",
        "mode": "fast", "admin_token": "", "provider": "openai", "auth_mode": "bearer",
        "system_name": "", "user_id": "", "user_type": "", "headers": {},
        "format": "openai", "extra_body": {}, "timeout_s": 20,
    },
    "openai_compatible": {
        "enabled": False, "api_url": "", "model": "gpt-oss-120b", "mode": "fast",
        "admin_token": "", "provider": "openai_compatible", "auth_mode": "bearer",
        "system_name": "", "user_id": "", "user_type": "", "headers": {},
        "format": "openai", "extra_body": {}, "timeout_s": 60,
    },
    "local": {
        "enabled": False, "api_url": "", "model": "GPT-OSS-120B", "mode": "fast",
        "admin_token": "", "provider": "local", "auth_mode": "none",
        "system_name": "", "user_id": "", "user_type": "", "headers": {},
        "format": "openai", "extra_body": {}, "timeout_s": 60,
    },
    "generic": {
        "enabled": False, "api_url": "", "model": "", "mode": "fast",
        "admin_token": "", "provider": "generic", "auth_mode": "bearer",
        "system_name": "", "user_id": "", "user_type": "", "headers": {},
        "format": "openai", "extra_body": {}, "timeout_s": 20,
    },
    "playground": {
        "enabled": False, "api_url": "", "model": "gpt-oss-120b", "mode": "fast",
        "admin_token": "", "provider": "playground", "auth_mode": "dep_ticket",
        "system_name": "playground", "user_id": "", "user_type": "", "headers": {},
        "format": "openai", "extra_body": {}, "timeout_s": 60,
    },
}
LLM_ALLOWED_PROVIDERS = set(LLM_PROVIDER_DEFAULTS)

# Named LLM presets — surfaced via GET /api/admin/llm/presets so the UI can
# offer a one-click base configuration. These contain NO secrets (no api_url,
# no admin_token). Admin still fills in the endpoint and credential.
LLM_NAMED_PRESETS: List[Dict[str, Any]] = [
    {
        "key": "gpt_oss_120b_internal",
        "label": "GPT OSS 120B (사내)",
        "description": "사내 운영 GPT OSS 120B endpoint. Flow 기본 LLM 백본.",
        "provider": "playground",
        "model": "gpt-oss-120b",
        "auth_mode": "dep_ticket",
        "format": "openai",
        "timeout_s": 60,
        "api_url_hint": "https://llm.internal/v1/chat/completions",
        "is_default": True,
    },
    {
        "key": "dev_openai_mini",
        "label": "Dev: OpenAI 5.4-mini",
        "description": "로컬/외부 개발 fallback. OpenAI 호환 mini 모델.",
        "provider": "openai",
        "model": "gpt-5.4-mini",
        "auth_mode": "bearer",
        "format": "openai",
        "timeout_s": 20,
        "api_url_hint": "https://api.openai.com/v1/chat/completions",
        "is_default": False,
    },
]

# Map UI-facing long keys to admin_settings.json short keys used by core/roots.py
_DR_KEY_MAP = {
    "db_root":        "db",
}
_DR_ENV_MAP = {
    "db_root":        ("FLOW_DB_ROOT",),
    "data_root":      ("FLOW_DATA_ROOT",),
}


def _resolver_snapshot() -> Dict[str, str]:
    """Call core.roots.snapshot() if available; else env+default fallback."""
    try:
        from core import roots as _roots  # type: ignore
        snap = _roots.snapshot()
        return {
            "db_root":        snap.get("db_root", ""),
        }
    except Exception:
        # Fallback: env var > data_root default. Does NOT consult admin_settings.
        db = os.environ.get("FLOW_DB_ROOT") or str(PATHS.db_root)
        return {"db_root": db}


def _root_source(ui_key: str) -> str:
    """Classify where the effective value came from: env | settings | default."""
    for env_name in _DR_ENV_MAP.get(ui_key, ()):
        if os.environ.get(env_name):
            return "env"
    profile = root_profile.read_profile()
    if ui_key == "data_root":
        if profile.get("mode") in ("local", "shared", "custom") or profile.get("data_root"):
            return "profile"
        return "default"
    if ui_key == "db_root" and str(profile.get("db_root") or "").strip():
        return "profile"
    short = _DR_KEY_MAP.get(ui_key)
    if short:
        cfg = load_json(ADMIN_SETTINGS_FILE, {}) or {}
        dr = cfg.get("data_roots") or {}
        v = dr.get(short)
        if isinstance(v, str) and v.strip():
            return "settings"
    return "default"


def _load_admin_settings() -> dict:
    data = load_json(ADMIN_SETTINGS_FILE, {})
    return data if isinstance(data, dict) else {}


def _save_admin_settings(data: dict) -> None:
    save_json(ADMIN_SETTINGS_FILE, data)


def _merge_nested(base: Dict[str, Any], override: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        k: _merge_nested(v, {}) if isinstance(v, dict) else v
        for k, v in (base or {}).items()
    }
    if not isinstance(override, dict):
        return out
    for key, value in override.items():
        if isinstance(out.get(key), dict) and isinstance(value, dict):
            out[key] = _merge_nested(out[key], value)
        else:
            out[key] = value
    return out


def _int_between(raw: Any, default: int, lo: int, hi: int) -> int:
    try:
        val = int(raw)
    except Exception:
        val = default
    return max(lo, min(hi, val))


def _flowi_default_settings(raw: Any = None) -> Dict[str, Any]:
    merged = _merge_nested(FLOWI_DEFAULT_SETTINGS, raw or {})
    charts = merged.get("chart_defaults") if isinstance(merged.get("chart_defaults"), dict) else {}
    scatter = charts.get("scatter") if isinstance(charts.get("scatter"), dict) else {}
    if scatter.get("grain") not in {"wafer_agg", "shot", "die", "map"}:
        scatter["grain"] = "wafer_agg"
    scatter["max_points"] = _int_between(scatter.get("max_points"), 500, 50, 5000)
    # 차트 집계 확장: avg/median 외 p90/p10/max 도 기본값으로 저장 허용 (llm._CHART_AGG_VALUES 와 동일).
    _chart_aggs = {"avg", "median", "p90", "p10", "max"}
    if scatter.get("inline_agg") not in _chart_aggs:
        scatter["inline_agg"] = "avg"
    if scatter.get("et_agg") not in _chart_aggs:
        scatter["et_agg"] = "median"
    charts["scatter"] = scatter
    line = charts.get("line") if isinstance(charts.get("line"), dict) else {}
    line["grain"] = "wafer_agg" if line.get("grain") not in {"wafer_agg", "shot", "die", "map"} else line.get("grain")
    line["max_points_per_series"] = _int_between(line.get("max_points_per_series"), 120, 20, 1000)
    charts["line"] = line
    bar = charts.get("bar") if isinstance(charts.get("bar"), dict) else {}
    bar["top_n"] = _int_between(bar.get("top_n"), 12, 3, 50)
    bar["other_bucket"] = bool(bar.get("other_bucket", True))
    charts["bar"] = bar
    pie = charts.get("pie") if isinstance(charts.get("pie"), dict) else {}
    pie["max_slices"] = _int_between(pie.get("max_slices"), 6, 3, 20)
    pie["other_bucket"] = bool(pie.get("other_bucket", True))
    charts["pie"] = pie
    box = charts.get("box") if isinstance(charts.get("box"), dict) else {}
    box["max_groups"] = _int_between(box.get("max_groups"), 12, 3, 50)
    box["min_n"] = _int_between(box.get("min_n"), 3, 1, 30)
    charts["box"] = box
    charts["surface"] = str(charts.get("surface") or "home_flowi").strip()[:80] or "home_flowi"
    merged["chart_defaults"] = charts
    policy = merged.get("feedback_policy") if isinstance(merged.get("feedback_policy"), dict) else {}
    policy["auto_apply_to_rag"] = False
    policy["review_required"] = bool(policy.get("review_required", True))
    policy["promotion_target"] = str(policy.get("promotion_target") or "golden_cases").strip()[:80] or "golden_cases"
    merged["feedback_policy"] = policy
    knowledge = merged.get("engineer_knowledge") if isinstance(merged.get("engineer_knowledge"), dict) else {}
    knowledge["rag_update_requires_marker"] = bool(knowledge.get("rag_update_requires_marker", True))
    knowledge["admin_review_required"] = bool(knowledge.get("admin_review_required", True))
    knowledge["custom_knowledge_append_only"] = bool(knowledge.get("custom_knowledge_append_only", True))
    merged["engineer_knowledge"] = knowledge
    agentic = merged.get("agentic") if isinstance(merged.get("agentic"), dict) else {}
    agentic["tool_call_enabled"] = bool(agentic.get("tool_call_enabled", False))
    agentic["react_loop_enabled"] = bool(agentic.get("react_loop_enabled", False))
    merged["agentic"] = agentic
    return merged


def _llm_provider(raw: Any) -> str:
    provider = str(raw or "generic").strip().lower() or "generic"
    return provider if provider in LLM_ALLOWED_PROVIDERS else "generic"


def _llm_defaults(provider: str = "generic") -> Dict[str, Any]:
    p = _llm_provider(provider)
    base = LLM_PROVIDER_DEFAULTS.get(p) or LLM_PROVIDER_DEFAULTS["generic"]
    return {
        k: (_merge_nested(v, {}) if isinstance(v, dict) else v)
        for k, v in base.items()
    }


def _normalize_llm_profile(raw: Any = None, provider_hint: str = "") -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    provider = _llm_provider(raw.get("provider") or provider_hint)
    out = _llm_defaults(provider)
    for key in LLM_PROFILE_KEYS:
        if key in raw:
            out[key] = raw.get(key)
    out["provider"] = provider
    for key in ("api_url", "model", "mode", "admin_token", "auth_mode", "system_name", "user_id", "user_type", "format"):
        out[key] = str(out.get(key) or "").strip()
    if not out["mode"]:
        out["mode"] = "fast"
    if not out["format"]:
        out["format"] = "openai"
    if not out["auth_mode"]:
        if provider == "playground":
            out["auth_mode"] = "dep_ticket"
        elif provider == "local":
            out["auth_mode"] = "none"
        else:
            out["auth_mode"] = "bearer"
    if out["auth_mode"] not in {"bearer", "dep_ticket", "none"}:
        out["auth_mode"] = str(_llm_defaults(provider).get("auth_mode") or "bearer")
    if provider == "playground" and not out["system_name"]:
        out["system_name"] = "playground"
    if provider in {"local", "openai_compatible"} and not out["model"]:
        out["model"] = "gpt-oss-120b"
    out["enabled"] = bool(out.get("enabled"))
    if not isinstance(out.get("headers"), dict):
        out["headers"] = {}
    else:
        out["headers"] = {str(k): str(v) for k, v in out["headers"].items() if k}
    if not isinstance(out.get("extra_body"), dict):
        out["extra_body"] = {}
    try:
        out["timeout_s"] = max(3, min(120, int(out.get("timeout_s") or _llm_defaults(provider).get("timeout_s") or 20)))
    except Exception:
        out["timeout_s"] = int(_llm_defaults(provider).get("timeout_s") or 20)
    return out


def _llm_profiles_from_admin(adm: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    raw_profiles = adm.get("llm_profiles") if isinstance(adm.get("llm_profiles"), dict) else {}
    profiles: Dict[str, Dict[str, Any]] = {}
    for provider, payload in raw_profiles.items():
        p = _llm_provider(provider)
        if isinstance(payload, dict):
            profiles[p] = _normalize_llm_profile({**payload, "provider": p}, p)
    legacy = adm.get("llm") if isinstance(adm.get("llm"), dict) else {}
    if legacy:
        p = _llm_provider(legacy.get("provider"))
        profiles.setdefault(p, _normalize_llm_profile(legacy, p))
    return profiles


def _llm_active_from_admin(adm: Dict[str, Any]) -> Dict[str, Any]:
    raw = adm.get("llm") if isinstance(adm.get("llm"), dict) else {}
    return _normalize_llm_profile(raw, raw.get("provider") if isinstance(raw, dict) else "generic")


class ApproveReq(BaseModel):
    username: str
class PermReq(BaseModel):
    username: str
    tabs: list
class MessageReq(BaseModel):
    to_user: str
    message: str
class LogEntry(BaseModel):
    username: str = ""
    action: str = ""
    tab: str = ""
    detail: str = ""
class DismissReq(BaseModel):
    username: str
    index: int
class BatchDismissReq(BaseModel):
    username: str
    ids: List[str]
class MarkReadReq(BaseModel):
    username: str
    ids: List[str]
# v8.8.14: per-page admin delegation + scheduled backup payload 스키마.
class PageAdminsReq(BaseModel):
    page_id: str
    usernames: List[str] = []
class BackupScheduleReq(BaseModel):
    at: str = ""            # ISO datetime — 비우면 취소
    reason: str = "pre-maintenance"
class BackupRestoreReq(BaseModel):
    filename: str
    restore_db_root_files: bool = False
class BulkUsersReq(BaseModel):
    text: str = ""  # legacy API compatibility
    rows: List[Dict[str, Any]] = []
    default_password: str = ""
    # 하위 호환 입력 필드. 신규 계정 권한은 서버에서 항상 없음으로 강제한다.
    default_tabs: Any = None

# ── 워커 분산 (v9.4.x — docs/WORKER_DISPATCH.md) ──────────────────────────────
# 관리자 탭(모니터 → 워커 서버)에서 역할 확인/변경, 개발서버 신호등, 원격 기동.

class WorkerRoleReq(BaseModel):
    role: str  # api | worker


@router.get("/worker")
def worker_status(_admin=Depends(require_admin)):
    """워커 분산 상태 — 역할(+출처), 워커/워치독 생존, 큐 깊이, 오프로드 통계."""
    from core import worker_dispatch
    return worker_dispatch.status()


@router.post("/worker/role")
def worker_set_role(req: WorkerRoleReq, request: Request, _admin=Depends(require_admin)):
    """이 서버의 역할을 마커 파일 + server_role.json 에 저장 — 재시작 없이 즉시 반영.

    개발은 shared data root의 호스트별 worker 마커를 만들고, 운영은 역할 마커를
    지운다 — 마커가 없는 서버는 항상 운영(api)으로 뜬다.
    FLOW_SERVER_ROLE env 로 고정된 배포에서는 UI 변경을 거부한다 (env 우선)."""
    from core import worker_dispatch
    out = worker_dispatch.set_role(req.role)
    if not out.get("ok"):
        status = 500 if out.get("code") in {"write_failed", "marker_locked"} else 400
        raise HTTPException(status, out.get("error") or "role change failed")
    _audit(request, "admin:worker-role-set", detail=str(req.role), tab="admin")
    return worker_dispatch.status()


@router.post("/worker/start")
def worker_remote_start(request: Request, _admin=Depends(require_admin)):
    """개발서버(워커) 원격 기동 요청 — shared workspace 의 start_request 파일을
    개발서버 상주 워치독(scripts/worker_watchdog.py)이 소비해 uvicorn 을 띄운다."""
    from core import worker_dispatch
    me = current_user(request)
    out = worker_dispatch.request_worker_start(requested_by=(me or {}).get("username") or "")
    _audit(request, "admin:worker-start", detail=str(out.get("ok")), tab="admin")
    return {**out, "status": worker_dispatch.status()}


@router.post("/tracker-schema-migrate")
def tracker_schema_migrate(request: Request, _admin=Depends(require_admin)):
    result = migrate_tracker_issues_file(reason="admin_button", actor=(current_user(request).get("username") or "admin"))
    _audit(request, "admin:tracker-schema-migrate", detail=f"changed={result.get('changed')} lots={result.get('lots_updated')}", tab="admin")
    return result


# ── Users ──
@router.get("/users")
def list_users(_admin=Depends(require_admin)):
    """v8.4.6: admin only. password_hash 는 응답에서 제거."""
    return {"users": [_scrub_user(u) for u in read_users()]}


@router.post("/approve")
def approve_user(req: ApproveReq, request: Request, _admin=Depends(require_admin)):
    users = read_users()
    for u in users:
        if u["username"] == req.username:
            u["status"] = "approved"
            write_users(users)
            send_notify(req.username, "Account Approved",
                        "Your account has been approved.", "info")
            # 가입 승인 메일 — 비밀번호 찾기 메일과 같은 경로. 발송 실패해도 승인은 유지.
            try:
                from core.mail import load_mail_cfg, send_mail
                try:
                    _sender = (load_mail_cfg().get("from_addr") or "").strip()
                except Exception:
                    _sender = ""
                _mres = send_mail(
                    sender_username=_sender or "flow",
                    receiver_usernames=[req.username],
                    extra_emails=[],
                    title="[flow] 가입 승인 완료",
                    content=(
                        "<div style='font-family:Arial,sans-serif;font-size:14px;line-height:1.6'>"
                        f"<p><b>{html.escape(req.username)}</b> 님, flow 가입이 승인되었습니다.</p>"
                        "<p>로그인 후 서비스를 이용하실 수 있습니다.</p>"
                        "</div>"
                    ),
                )
                _audit(request, "admin:approve-mail", detail=f"user={req.username};ok={_mres.get('ok')};reason={_mres.get('reason','')}", tab="admin")
            except Exception:
                pass
            _audit(request, "admin:approve", detail=f"user={req.username}", tab="admin")
            return {"ok": True}
    raise HTTPException(404)


@router.post("/reject")
def reject_user(req: ApproveReq, request: Request, _admin=Depends(require_admin)):
    users = [u for u in read_users() if u["username"] != req.username]
    write_users(users)
    _audit(request, "admin:reject", detail=f"user={req.username}", tab="admin")
    return {"ok": True}


@router.post("/reset-password")
def reset_password(req: ApproveReq, request: Request, _admin=Depends(require_admin)):
    """v8.4.6: 임시 랜덤 비번 (12자) 발급. 기존 '1111' 하드코딩 제거.
    v9.x: admin 메일 설정(domain 포함)을 사용해 사용자에게 임시 비번을 발송."""
    from core.auth import hash_password, revoke_user_tokens
    from core.mail import load_mail_cfg, send_mail
    users = read_users()
    try:
        actor = (current_user(request).get("username") or "flow-admin").strip()
    except Exception:
        actor = "flow-admin"
    try:
        mail_sender = (load_mail_cfg().get("from_addr") or "").strip()
    except Exception:
        mail_sender = ""
    for u in users:
        if u["username"] == req.username:
            new_pw = secrets.token_urlsafe(9)  # ≈12 chars
            old_hash = u.get("password_hash", "")
            u["password_hash"] = hash_password(new_pw)
            write_users(users)
            safe_username = html.escape(req.username)
            content = (
                "<div style='font-family:Arial,sans-serif;font-size:14px;line-height:1.6'>"
                "<p>Your password has been reset by an administrator.</p>"
                f"<p><b>Username</b>: {safe_username}<br/>"
                f"<b>Temporary Password</b>: {html.escape(new_pw)}</p>"
                "<p>Please sign in and change your password immediately.</p>"
                "<p style='color:#666;font-size:12px'>If you did not expect this, contact the administrator.</p>"
                "</div>"
            )
            try:
                mail_res = send_mail(
                    sender_username=mail_sender or actor or "flow-admin",
                    receiver_usernames=[req.username],
                    title="[flow] Password Reset",
                    content=content,
                    files=[],
                )
            except Exception as e:
                mail_res = {"ok": False, "reason": f"{type(e).__name__}: {e}", "to": [], "skipped": [req.username]}
            if not mail_res.get("ok"):
                u["password_hash"] = old_hash
                write_users(users)
                reason = mail_res.get("reason") or "Password reset email failed"
                _audit(request, "admin:reset-password-mail-failed",
                       detail=f"user={req.username};reason={reason}", tab="admin")
                raise HTTPException(503, reason)
            revoked = revoke_user_tokens(req.username)  # 기존 세션 강제 로그아웃
            send_notify(req.username, "Password Reset",
                        "Your temporary password was sent to your configured email.", "info")
            mail_to = mail_res.get("to") or []
            _audit(request, "admin:reset-password",
                   detail=f"user={req.username};revoked={revoked};mail_to={','.join(mail_to)}", tab="admin")
            return {
                "ok": True,
                "mail_sent": True,
                "mail_to": mail_to,
                "mail_skipped": mail_res.get("skipped") or [],
            }
    raise HTTPException(404)


class EmailReq(BaseModel):
    username: str
    email: str = ""


@router.post("/set-email")
def set_email(req: EmailReq, request: Request, _admin=Depends(require_admin)):
    """v8.7.2: admin sets/clears a user's email (used for 인폼 메일 수신자)."""
    email = (req.email or "").strip()
    if email and "@" not in email:
        raise HTTPException(400, "Invalid email format")
    users = read_users()
    for u in users:
        if u["username"] == req.username:
            u["email"] = email
            write_users(users)
            _audit(request, "admin:set-email", detail=f"user={req.username} email={email or '(clear)'}", tab="admin")
            return {"ok": True}
    raise HTTPException(404)


class NameReq(BaseModel):
    # v8.8.27: admin 이 특정 유저의 실명(name) 을 설정/수정.
    username: str
    name: str = ""


@router.post("/set-name")
def set_name(req: NameReq, request: Request, _admin=Depends(require_admin)):
    """v8.8.27: admin 이 유저의 실명을 설정/수정. 기존 가입자 일괄 채움용."""
    nm = (req.name or "").strip()
    users = read_users()
    for u in users:
        if u["username"] == req.username:
            u["name"] = nm
            write_users(users)
            _audit(request, "admin:set-name", detail=f"user={req.username} name={nm or '(clear)'}", tab="admin")
            return {"ok": True}
    raise HTTPException(404)


@router.post("/delete-user")
def delete_user(req: ApproveReq, request: Request, _admin=Depends(require_admin)):
    from core.auth import revoke_user_tokens
    users = [u for u in read_users() if u["username"] != req.username]
    write_users(users)
    _, removed_members = _prune_perm_groups_for_users(users)
    revoke_user_tokens(req.username)
    removed_groups = removed_members.get(req.username, [])
    detail = f"user={req.username}"
    if removed_groups:
        detail += f";permission_groups={','.join(removed_groups)}"
    _audit(request, "admin:delete-user", detail=detail, tab="admin")
    return {"ok": True, "removed_from_permission_groups": removed_groups}


@router.post("/bulk-users")
def bulk_create_users(req: BulkUsersReq, request: Request, _admin=Depends(require_admin)):
    from core.auth import hash_password
    default_pw = str(req.default_password or "")
    if len(default_pw) < 10:
        raise HTTPException(400, "default_password must be explicitly set and at least 10 characters")
    if default_pw.strip().casefold() in {"1111", "1234", "password", "password1", "hol12345!"}:
        raise HTTPException(400, "default_password is too weak")

    def _split_row(line: str) -> list[str]:
        if "\t" in line:
            return line.split("\t")
        return line.split(",")

    input_rows: list[tuple[int, dict[str, str]]] = []
    if req.rows:
        for idx, row in enumerate(req.rows, start=1):
            if not isinstance(row, dict):
                continue
            input_rows.append((idx, {
                "name": str(row.get("name") or "").strip(),
                "username": str(row.get("username") or "").strip(),
                "email": str(row.get("email") or "").strip(),
                "role": str(row.get("role") or "user").strip() or "user",
                "tabs": str(row.get("tabs") or "").strip(),
            }))
    else:
        # Legacy callers may still send tab/comma separated text. The Admin UI
        # now sends structured table rows instead.
        raw = str(req.text or "").replace("\r\n", "\n").replace("\r", "\n")
        lines = [ln for ln in raw.split("\n") if ln.strip()]
        if lines:
            parsed_rows = [_split_row(ln) for ln in lines]
            header = [str(x or "").strip().lower() for x in parsed_rows[0]]
            has_header = any(x in {"name", "username", "email", "role", "tabs"} for x in header)
            body = parsed_rows[1:] if has_header else parsed_rows
            for idx, parts in enumerate(body, start=1):
                vals = [str(x or "").strip() for x in parts]
                if has_header:
                    data = {header[i]: vals[i] if i < len(vals) else "" for i in range(len(header))}
                else:
                    third = vals[2] if len(vals) >= 3 else ""
                    data = {
                        "name": vals[0] if len(vals) >= 1 else "",
                        "username": vals[1] if len(vals) >= 2 else (vals[0] if vals else ""),
                        "email": third if "@" in third else "",
                        "role": (vals[3] if len(vals) >= 4 else "user") if "@" in third else (third or "user"),
                        "tabs": (vals[4] if len(vals) >= 5 else "") if "@" in third else (vals[3] if len(vals) >= 4 and "," in vals[3] else ""),
                    }
                input_rows.append((idx, data))
    if not input_rows:
        raise HTTPException(400, "No rows provided")
    users = read_users()
    # `hong` 과 `hong@corp.com` 은 같은 계정 — 중복 판정도 canonical 키로 한다.
    existing = {canonical_username(str(u.get("username") or "")) for u in users}
    existing.discard("")
    created = []
    skipped = []
    for idx, data in input_rows:
      username = (data.get("username") or "").strip()
      name = (data.get("name") or "").strip()
      email = (data.get("email") or "").strip()
      username = username.strip()
      if not username:
          skipped.append({"row": idx, "reason": "missing username"})
          continue
      try:
          username = validate_username(username)
      except ValueError as exc:
          skipped.append({"row": idx, "username": username, "reason": str(exc)})
          continue
      key = canonical_username(username)
      if key in existing:
          skipped.append({"row": idx, "username": username, "reason": "already exists"})
          continue
      if not email and "@" in username:
          email = username          # 전체 주소로 적어 왔으면 메일 주소로도 남긴다
      username = key or username    # 저장은 사내 id 형태로 통일
      if email and "@" not in email:
          email = ""
      # 관리자 화면/API에서 처음 만든 계정도 일반 가입과 동일하게 시작한다.
      # 요청에 legacy role/tabs/default_tabs 값이 들어와도 초기 권한을 부여하지 않는다.
      role = "user"
      tabs = ""
      user_row = {
          "username": username,
          "password_hash": hash_password(default_pw),
          "role": role,
          "status": "approved",
          "created": dt.datetime.now().isoformat(),
          "last_login": "",
          "tabs": tabs,
          "email": email,
          "name": name,
          "sso_id": "",
          "department": "",
          "permission_source": "",
      }
      users.append(user_row)
      existing.add(key)
      created.append({"username": username, "name": name, "role": role, "tabs": tabs})

    write_users(users)
    _audit(request, "admin:bulk-users", detail=f"created={len(created)} skipped={len(skipped)}", tab="admin")
    return {"ok": True, "created": created, "skipped": skipped}


# ── Permissions ──
@router.post("/set-tabs")
def set_tabs(req: PermReq, request: Request, _admin=Depends(require_admin)):
    # v9.1.x: "tab" 또는 "tab:subtab" 토큰 허용 — 유효하지 않은 토큰은 제거.
    tokens: list = []
    for part in req.tabs:
        token = canonical_tab_token(part)
        if token and token not in tokens:
            tokens.append(token)
    users = read_users()
    for u in users:
        if u["username"] == req.username:
            if str(u.get("role") or "user").strip() == "admin":
                changed = (u.get("tabs") != "__all__" or u.get("permission_source") != "admin")
                u["tabs"] = "__all__"
                u["permission_source"] = "admin"
                if changed:
                    write_users(users)
                removed = _remove_from_perm_groups(req.username)
                _audit(
                    request,
                    "admin:set-tabs",
                    detail=f"user={req.username} admin=all removed={','.join(removed) or '(없음)'}",
                    tab="admin",
                )
                return {
                    "ok": True,
                    "admin_all_permissions": True,
                    "removed_from_groups": removed,
                }
            u["tabs"] = ",".join(tokens)
            # An explicit save must continue to win over an SSO department
            # default, including when the explicit selection is "no tabs".
            u["permission_source"] = "individual"
            write_users(users)
            # 개별 지정은 권한 그룹보다 우선 — 속해 있던 권한 그룹에서 자동 제외
            # (그룹에 남겨두면 다음 그룹 저장 때 그룹 권한이 다시 덮어쓰므로).
            removed = _remove_from_perm_groups(req.username)
            detail = f"user={req.username} tabs={u['tabs']}"
            if removed:
                detail += f" (권한그룹 {','.join(removed)} 에서 제외)"
            _audit(request, "admin:set-tabs", detail=detail, tab="admin")
            return {"ok": True, "removed_from_groups": removed}
    raise HTTPException(404)


class SetRoleReq(BaseModel):
    username: str
    role: str  # "user" | "admin"


@router.post("/set-role")
def set_role(req: SetRoleReq, request: Request, _admin=Depends(require_admin)):
    """admin 이 특정 유저의 역할(admin/user)을 변경. 강등/승격 모두 지원.
    - 'admin' 이면 모든 권한 그룹에서 제외하고 전체 권한을 명시적으로 저장한다.
    - 'user' 로 강등하면 관리자 전체 권한을 제거해 이후 그룹/개별 권한으로 다시 지정한다.
    - 변경 즉시 해당 유저의 세션 토큰을 무효화해 재로그인 시 새 역할이 적용된다.
    - 마지막 남은 admin 을 강등하면 잠금되므로 차단한다."""
    from core.auth import revoke_user_tokens
    new_role = str(req.role or "").strip().lower()
    if new_role not in {"user", "admin"}:
        raise HTTPException(400, "role must be 'user' or 'admin'")
    users = read_users()
    for u in users:
        if u["username"] == req.username:
            old_role = str(u.get("role") or "user").strip() or "user"
            # 마지막 admin 강등 방지 (전체 잠금 방지).
            if old_role == "admin" and new_role != "admin":
                admin_count = sum(1 for x in users if str(x.get("role") or "").strip() == "admin")
                if admin_count <= 1:
                    raise HTTPException(400, "마지막 관리자는 강등할 수 없습니다. 다른 관리자를 먼저 지정하세요.")

            old_tabs = str(u.get("tabs") or "")
            old_source = str(u.get("permission_source") or "")
            u["role"] = new_role
            if new_role == "admin":
                u["tabs"] = "__all__"
                u["permission_source"] = "admin"
            elif old_role == "admin":
                # 관리자에게만 유효한 전체 권한이 일반 계정에 남지 않도록
                # 최소 권한 상태로 되돌린 뒤 명시적인 재지정을 기다린다.
                u["tabs"] = ""
                u["permission_source"] = ""

            changed = (
                old_role != new_role
                or old_tabs != str(u.get("tabs") or "")
                or old_source != str(u.get("permission_source") or "")
            )
            if changed:
                write_users(users)

            # 역할 변경은 그룹 파일 정리의 성공 여부와 무관하게 즉시 세션에
            # 반영되어야 한다. 먼저 기존 토큰을 폐기하고 보조 권한을 정리한다.
            revoked = revoke_user_tokens(req.username) if changed else 0
            # 기존 데이터가 이미 엉킨 경우와 관리자 강등 모두에서 오래된
            # 그룹 권한을 남기지 않는다. 권한 그룹 편집은 이후 명시적으로 한다.
            removed = _remove_from_perm_groups(req.username) if (new_role == "admin" or old_role == "admin") else []
            _audit(request, "admin:set-role",
                   detail=(f"user={req.username};{old_role}->{new_role};revoked={revoked};"
                           f"removed_groups={','.join(removed) or '(없음)'};changed={changed}"), tab="admin")
            return {
                "ok": True,
                "username": req.username,
                "role": new_role,
                "revoked_sessions": revoked,
                "removed_from_permission_groups": removed,
                "unchanged": not changed and not removed,
            }
    raise HTTPException(404)


@router.get("/user-tabs")
def get_user_tabs(request: Request, username: str = Query(...)):
    """v8.4.6: 본인 또는 admin 만.
    v9.0.x: archived tabs are filtered out of existing saved preferences."""
    # v9.5.x: ettime 은 "ET 측정시간" 탭으로 부활 — archived 목록에서 제외.
    _ARCHIVED_TABS = {"waferlayout"}
    verify_owner(request, username)
    for u in read_users():
        if u["username"] == username:
            if u.get("role") == "admin":
                return {"tabs": "__all__"}
            raw = u.get("tabs", "")
            if not raw:
                tabs_list = []
            else:
                # v9.1.x: "tab:subtab" 토큰 유지 — archived 판정은 main tab 기준.
                tabs_list = [t.strip() for t in raw.split(",")
                             if t.strip() and t.strip().split(":")[0] not in _ARCHIVED_TABS]
            return {"tabs": ",".join(tabs_list)}
    raise HTTPException(404)


# ── 권한 그룹 (permission groups) ─────────────────────────────────────
# 그룹탭(소셜 그룹, data_root/groups/groups.json)과는 별개의 권한 전용 그룹.
# 그룹에 tabs 권한을 지정하고 사용자를 멤버로 넣으면 그 사용자의 users.csv
# tabs 가 그룹 권한으로 즉시 덮어써진다(materialize). 한 사용자는 하나의
# 권한 그룹에만 속한다. 권한 그룹에는 SSO 부서명을 연결할 수도 있으며, 개인 또는
# 명시 그룹 권한이 없는 사용자는 그 부서의 기본 권한을 자동 상속한다.
# 개별 set-tabs 로 권한을 따로 지정하면 그룹에서 빠지고 부서 기본값보다 우선한다.
PERM_GROUPS_FILE = PATHS.data_root / "perm_groups.json"


def _department_key(value: Any) -> str:
    """Case-insensitive key while preserving the SSO/display spelling in files."""
    return " ".join(str(value or "").strip().split()).casefold()


def _clean_departments(values: Any) -> list[str]:
    if isinstance(values, str):
        values = values.replace("\r", "\n").replace(",", "\n").split("\n")
    out: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        label = " ".join(str(value or "").strip().split())
        key = _department_key(label)
        if label and key not in seen:
            seen.add(key)
            out.append(label)
    return out


def _load_perm_groups() -> list:
    data = load_json(PERM_GROUPS_FILE, {"groups": []}) or {}
    raw = data.get("groups") if isinstance(data, dict) else data
    out = []
    for g in raw or []:
        if not isinstance(g, dict):
            continue
        name = str(g.get("name") or "").strip()
        if not name:
            continue
        tabs = [str(t or "").strip() for t in (g.get("tabs") or []) if str(t or "").strip()]
        members = [str(m or "").strip() for m in (g.get("members") or []) if str(m or "").strip()]
        departments = _clean_departments(g.get("departments") or [])
        out.append({"name": name, "tabs": tabs, "members": members, "departments": departments})
    return out


def _save_perm_groups(groups: list) -> None:
    save_json(PERM_GROUPS_FILE, {"groups": groups})


def _prune_perm_groups_for_users(users: list) -> tuple[list, dict[str, list[str]]]:
    """Remove missing users and admins from permission-group membership.

    users.csv is the account and role source of truth. Admins always have full
    access independently of groups, so retaining them as members creates two
    conflicting permission sources. Returning removed usernames and group names
    lets deletion/audit callers report exactly what was cleaned up.
    """
    existing = {
        str(u.get("username") or "").strip()
        for u in users
        if isinstance(u, dict) and str(u.get("username") or "").strip()
    }
    admins = {
        str(u.get("username") or "").strip()
        for u in users
        if isinstance(u, dict)
        and str(u.get("username") or "").strip()
        and str(u.get("role") or "user").strip() == "admin"
    }
    groups = _load_perm_groups()
    removed: dict[str, list[str]] = {}
    changed = False
    for group in groups:
        kept: list[str] = []
        for username in group["members"]:
            if username in existing and username not in admins:
                kept.append(username)
                continue
            removed.setdefault(username, []).append(group["name"])
            changed = True
        group["members"] = kept
    if changed:
        _save_perm_groups(groups)
    return groups, removed


def _remove_from_perm_groups(username: str) -> list:
    """개별 권한 지정 시 호출 — 사용자가 속한 권한 그룹에서 제외. 제외된 그룹명 반환."""
    groups = _load_perm_groups()
    removed = []
    for g in groups:
        if username in g["members"]:
            g["members"] = [m for m in g["members"] if m != username]
            removed.append(g["name"])
    if removed:
        _save_perm_groups(groups)
    return removed


def _apply_department_permission_defaults(
    users: list,
    groups: list | None = None,
    *,
    only_username: str = "",
) -> int:
    """Materialize explicit-group and SSO-department permissions into users.csv rows.

    Precedence is deliberately stable: admin > explicit individual > explicit
    permission-group member > department default. Legacy non-empty ``tabs`` rows
    are treated as individual assignments so enabling this feature never silently
    replaces an existing user's access.
    """
    groups = groups if groups is not None else _load_perm_groups()
    member_groups: dict[str, dict] = {}
    department_groups: dict[str, dict] = {}
    for group in groups:
        for username in group.get("members") or []:
            member_groups[str(username)] = group
        for department in group.get("departments") or []:
            key = _department_key(department)
            if key:
                department_groups[key] = group

    changed = 0
    for user in users:
        username = str(user.get("username") or "").strip()
        if only_username and username != only_username:
            continue
        if str(user.get("role") or "user") == "admin":
            continue

        before = (str(user.get("tabs") or ""), str(user.get("permission_source") or ""))
        explicit_group = member_groups.get(username)
        if explicit_group is not None:
            user["tabs"] = ",".join(explicit_group.get("tabs") or [])
            user["permission_source"] = f"group:{explicit_group.get('name') or ''}"
        else:
            source = str(user.get("permission_source") or "").strip()
            # A user removed from an explicit group keeps the last materialized
            # access as an individual assignment, matching the legacy behavior.
            if source.startswith("group:"):
                source = "individual"
                user["permission_source"] = source
            elif not source and str(user.get("tabs") or "").strip():
                source = "individual"
                user["permission_source"] = source

            if source.startswith("department:") or not source:
                department = _department_key(user.get("department"))
                default_group = department_groups.get(department)
                if default_group is not None:
                    user["tabs"] = ",".join(default_group.get("tabs") or [])
                    user["permission_source"] = f"department:{default_group.get('name') or ''}"
                elif source.startswith("department:"):
                    # Department changed or its mapping was removed: do not retain
                    # permissions inherited from the old department.
                    user["tabs"] = ""
                    user["permission_source"] = "department:"

        after = (str(user.get("tabs") or ""), str(user.get("permission_source") or ""))
        if after != before:
            changed += 1
    return changed


def apply_sso_department_permissions(users: list, username: str) -> int:
    """Runtime hook used by the SSO provider after claims are synced to a row."""
    return _apply_department_permission_defaults(users, only_username=username)


class PermGroupReq(BaseModel):
    name: str
    tabs: list = []
    members: List[str] = []
    departments: List[str] = []
    rename_from: str = ""   # 그룹 이름 변경 시 기존 이름


class PermGroupDeleteReq(BaseModel):
    name: str


@router.get("/perm-groups")
def perm_groups_list(_admin=Depends(require_admin)):
    groups, _ = _prune_perm_groups_for_users(read_users())
    return {"groups": groups}


@router.post("/perm-groups")
def perm_groups_save(req: PermGroupReq, request: Request, _admin=Depends(require_admin)):
    name = str(req.name or "").strip()
    if not name:
        raise HTTPException(400, "그룹 이름을 입력하세요")
    tokens: list = []
    for part in req.tabs:
        token = canonical_tab_token(part)
        if token and token not in tokens:
            tokens.append(token)
    users = read_users()
    by_name = {u["username"]: u for u in users}
    members: list = []
    for m in req.members:
        m = str(m or "").strip()
        if not m or m in members:
            continue
        u = by_name.get(m)
        if u is None:
            raise HTTPException(400, f"없는 사용자: {m}")
        if u.get("role") == "admin":
            raise HTTPException(400, f"admin 계정({m})은 권한 그룹에 넣을 수 없습니다 — 항상 전체 권한")
        members.append(m)
    old_name = str(req.rename_from or "").strip() or name
    departments = _clean_departments(req.departments)
    current_groups, _ = _prune_perm_groups_for_users(users)
    groups = [g for g in current_groups if g["name"] not in (name, old_name)]
    # 한 사용자는 하나의 권한 그룹에만 — 다른 그룹에서 자동 제거.
    for g in groups:
        g["members"] = [m for m in g["members"] if m not in members]
        # 한 부서도 하나의 기본 권한 그룹에만 연결한다.
        claimed = {_department_key(value) for value in departments}
        g["departments"] = [
            value for value in (g.get("departments") or [])
            if _department_key(value) not in claimed
        ]
    groups.append({"name": name, "tabs": tokens, "members": members, "departments": departments})
    groups.sort(key=lambda g: g["name"])
    _save_perm_groups(groups)
    # 명시 멤버 + 부서 기본 대상에게 실적용. 개인 권한은 건드리지 않는다.
    csv_tabs = ",".join(tokens)
    applied = _apply_department_permission_defaults(users, groups)
    if applied:
        write_users(users)
    _audit(request, "admin:perm-group-save",
           detail=f"group={name} tabs={csv_tabs or '(없음)'} members={','.join(members) or '(없음)'} departments={','.join(departments) or '(없음)'} applied={applied}",
           tab="admin")
    return {"ok": True, "applied": applied, "groups": _load_perm_groups()}


@router.post("/perm-groups/delete")
def perm_groups_delete(req: PermGroupDeleteReq, request: Request, _admin=Depends(require_admin)):
    name = str(req.name or "").strip()
    groups = _load_perm_groups()
    remain = [g for g in groups if g["name"] != name]
    if len(remain) == len(groups):
        raise HTTPException(404, f"권한 그룹 없음: {name}")
    _save_perm_groups(remain)
    users = read_users()
    applied = _apply_department_permission_defaults(users, remain)
    if applied:
        write_users(users)
    _audit(request, "admin:perm-group-delete", detail=f"group={name} (멤버 권한은 유지)", tab="admin")
    return {"ok": True, "groups": remain, "applied": applied}


@router.post("/use-department-default")
def use_department_default(req: ApproveReq, request: Request, _admin=Depends(require_admin)):
    """Clear an explicit override and immediately re-apply the user's SSO department default."""
    users = read_users()
    for user in users:
        if user.get("username") != req.username:
            continue
        removed = _remove_from_perm_groups(req.username)
        user["permission_source"] = "department:"
        applied = _apply_department_permission_defaults(users, only_username=req.username)
        write_users(users)
        _audit(
            request,
            "admin:use-department-default",
            detail=f"user={req.username};department={user.get('department') or '(없음)'};removed={','.join(removed) or '(없음)'}",
            tab="admin",
        )
        return {"ok": True, "applied": applied, "tabs": user.get("tabs") or "", "permission_source": user.get("permission_source") or ""}
    raise HTTPException(404)


# ── Messaging ──
@router.post("/send-message")
def send_message(req: MessageReq, _admin=Depends(require_admin)):
    send_notify(req.to_user, "Message from Admin", req.message, "message")
    return {"ok": True}


class InquiryReq(BaseModel):
    username: str
    message: str


@router.post("/send-inquiry")
def send_inquiry(req: InquiryReq, request: Request):
    """User sends inquiry to all admins. 본인 이름으로만 보낼 수 있음."""
    verify_owner(request, req.username)
    send_to_admins(
        f"Inquiry from {req.username}",
        req.message,
        "message",
    )
    # Also notify the user that their inquiry was sent
    send_notify(req.username, "Inquiry Sent", "Your message has been sent to admin.", "info")
    return {"ok": True}


@router.post("/broadcast")
def broadcast(req: MessageReq, _admin=Depends(require_admin)):
    for u in read_users():
        if u["status"] == "approved":
            send_notify(u["username"], "Broadcast", req.message, "message")
    return {"ok": True}


# ── Notifications ──
@router.get("/my-notifications")
def my_notifications(request: Request, username: str = Query(...)):
    verify_owner(request, username)
    notifs = get_notifications(username, unread_only=True)
    return {"notifications": notifs, "count": len(notifs)}


@router.get("/all-notifications")
def all_notifications(request: Request, username: str = Query(...)):
    verify_owner(request, username)
    return {"notifications": get_notifications(username)}


@router.post("/mark-read")
def mark_read(req: ApproveReq, request: Request):
    verify_owner(request, req.username)
    mark_all_read(req.username)
    return {"ok": True}


@router.post("/dismiss")
def dismiss(req: DismissReq, request: Request):
    verify_owner(request, req.username)
    dismiss_notification(req.username, req.index)
    return {"ok": True}


@router.post("/dismiss-batch")
def dismiss_batch(req: BatchDismissReq, request: Request):
    verify_owner(request, req.username)
    dismiss_by_ids(req.username, req.ids)
    return {"ok": True}


# v8.8.33: 유저별 notify 구독 룰.
@router.get("/notify-rules")
def get_notify_rules(request: Request, username: str = Query("")):
    from core.notify import list_rules, event_catalog
    me = current_user(request)
    target = (username or me.get("username") or "").strip()
    if target != me.get("username") and me.get("role") != "admin":
        raise HTTPException(403, "self or admin only")
    return {"rules": list_rules(target), "catalog": event_catalog()}


class NotifyRulesReq(BaseModel):
    username: str = ""
    rules: dict = {}


@router.post("/notify-rules")
def save_notify_rules(req: NotifyRulesReq, request: Request):
    from core.notify import save_rules, list_rules
    me = current_user(request)
    target = (req.username or me.get("username") or "").strip()
    if target != me.get("username") and me.get("role") != "admin":
        raise HTTPException(403, "self or admin only")
    save_rules(target, req.rules or {})
    return {"ok": True, "rules": list_rules(target)}


@router.post("/mark-read-batch")
def mark_read_batch(req: MarkReadReq, request: Request):
    verify_owner(request, req.username)
    mark_read_by_ids(req.username, req.ids)
    return {"ok": True}


# ── Activity Logging ──
@router.post("/log")
def write_log(entry: LogEntry, request: Request):
    """v8.4.6: entry.username 은 세션 소유자로 강제 (spoof 방지)."""
    me = current_user(request)
    data = entry.dict()
    data["username"] = me["username"]
    append_activity(data)
    return {"ok": True}


@router.get("/logs")
def get_logs(request: Request, limit: int = 200, username: str = "", action: str = "", tab: str = ""):
    """v8.4.6: 전체 로그 열람은 admin. 본인 로그는 누구나.
    v8.7.1: action/tab 키워드 부분일치 필터 추가 (admin activity log UI 용)."""
    me = current_user(request)
    is_admin = me.get("role") == "admin"
    if not is_admin:
        username = me["username"]
    user_query = (username or "").strip().lower()
    act = (action or "").strip().lower()
    tbf = (tab or "").strip().lower()

    def _filt(e):
        event_username = str(e.get("username") or "")
        if user_query:
            if is_admin and user_query not in event_username.lower():
                return False
            if not is_admin and event_username != username:
                return False
        if act and act not in (e.get("action", "") or "").lower():
            return False
        if tbf and tbf not in (e.get("tab", "") or "").lower():
            return False
        return True

    return {"logs": jsonl_read(ACTIVITY_LOG, limit, _filt)}


@router.get("/logs/users")
def get_log_users(_admin=Depends(require_admin)):
    """Admin activity log 유저 드롭다운용: 활동 로그에 등장한 distinct username."""
    entries = jsonl_read(ACTIVITY_LOG, limit=5000)
    seen = {}
    for e in entries:
        u = e.get("username") or ""
        if not u:
            continue
        s = seen.setdefault(u, {"username": u, "count": 0, "last": ""})
        s["count"] += 1
        ts = e.get("timestamp", "")
        if ts > s["last"]:
            s["last"] = ts
    arr = sorted(seen.values(), key=lambda v: v["last"], reverse=True)
    return {"users": arr}


# ── Download History ──
@router.get("/download-history")
def download_history(limit: int = Query(200), _admin=Depends(require_admin)):
    return {"logs": jsonl_read(DL_LOG, limit)}


# ── Global Settings (v8.1.5) ──
@router.get("/settings")
def get_settings(request: Request):
    """Readable by anyone — UI (Dashboard) needs to read refresh interval.

    v8.3.0: also returns a `data_roots` block with effective paths and the
    source classification (env | settings | default) for each root. The
    effective paths come from core.roots resolver if available (Agent A); if
    the resolver is missing we fall back to env vars + PATHS defaults.
    """
    me = current_user(request)
    data = load_json(SETTINGS_FILE, {})
    merged = {**DEFAULT_SETTINGS, **(data if isinstance(data, dict) else {})}
    raw_sections = (data or {}).get("dashboard_sections") if isinstance(data, dict) else {}
    if not isinstance(raw_sections, dict):
        raw_sections = {}
    merged["dashboard_sections"] = {
        **DEFAULT_SETTINGS["dashboard_sections"],
        **{k: bool(v) for k, v in raw_sections.items() if k in DEFAULT_SETTINGS["dashboard_sections"]},
    }
    raw_fab = (data or {}).get("dashboard_fab_progress") if isinstance(data, dict) else {}
    if not isinstance(raw_fab, dict):
        raw_fab = {}
    merged["dashboard_fab_progress"] = {
        **DEFAULT_SETTINGS["dashboard_fab_progress"],
        **{
            k: raw_fab[k]
            for k in DEFAULT_SETTINGS["dashboard_fab_progress"]
            if k in raw_fab
        },
    }
    adm = _load_admin_settings()
    # v8.7.0: backup 설정 admin 에게 노출.
    if me.get("role") == "admin":
        try:
            from core.backup import get_settings as _bk_get
            merged["backup"] = _bk_get()
        except Exception:
            merged["backup"] = None
        # v8.7.2: 메일 API 설정 admin 에게 노출
        try:
            merged["mail"] = adm.get("mail") or {
                "api_url": "", "headers": {}, "from_addr": "", "status_code": "",
                "extra_data": {}, "recipient_groups": {}, "enabled": False,
            }
        except Exception:
            merged["mail"] = None
        # v8.7.7: LLM 설정도 admin 에게만 노출 (unredacted — 편집을 위해).
        try:
            merged["llm"] = _llm_active_from_admin(adm)
            merged["llm_profiles"] = _llm_profiles_from_admin(adm)
            merged["llm_profile_defaults"] = {
                p: _llm_defaults(p) for p in sorted(LLM_ALLOWED_PROVIDERS)
            }
        except Exception:
            merged["llm"] = None
            merged["llm_profiles"] = {}
            merged["llm_profile_defaults"] = {}
        merged["flowi_defaults"] = _flowi_default_settings(adm.get("flowi_defaults") or {})
        merged["flowi_persona"] = adm.get("flowi_persona") if isinstance(adm.get("flowi_persona"), dict) else {}
    # v8.4.6: data_roots (내부 파일시스템 경로) 는 admin 에게만 노출.
    if me.get("role") == "admin":
        try:
            eff = _resolver_snapshot()
            profile = root_profile.snapshot()
            merged["data_roots"] = {
                "data_root":     str(PATHS.data_root),
                "db_root":        eff.get("db_root", ""),
                "profile":        profile,
                "restart_note":   "mode/data_root changes apply after server restart; db_root applies to new requests.",
                "sources": {
                    "data_root":      _root_source("data_root"),
                    "db_root":        _root_source("db_root"),
                },
            }
        except Exception as e:
            merged["data_roots"] = {
                "data_root": "",
                "db_root": "",
                "profile": {},
                "sources": {"data_root": "default", "db_root": "default"},
                "error": f"resolver unavailable: {e}",
            }
    return merged


class DataRootsReq(BaseModel):
    mode: Optional[str] = None
    data_root: Optional[str] = None
    db_root: Optional[str] = None
    prod_app_roots: Optional[List[str]] = None


class BackupCfgReq(BaseModel):
    path: Optional[str] = None
    interval_hours: Optional[int] = None
    keep: Optional[int] = None
    enabled: Optional[bool] = None


class MailCfgReq(BaseModel):
    # v8.7.2: 사내 메일 API 연동 설정.
    api_url: Optional[str] = None
    headers: Optional[Dict[str, str]] = None      # {"Authorization":"...", ...}
    from_addr: Optional[str] = None               # → senderMailaddress
    status_code: Optional[str] = None             # → statusCode (default for sends)
    extra_data: Optional[Dict[str, Any]] = None   # merged into outgoing `data` block
    recipient_groups: Optional[Dict[str, List[str]]] = None  # {"group": ["email1", ...]}
    enabled: Optional[bool] = None
    dep_ticket: Optional[str] = None              # v8.8.17: headers["x-dep-ticket"] shortcut
    domain: Optional[str] = None                  # v8.8.19: company email domain (예: "company.co.kr") — username-only 값 뒤에 자동 합성


class LLMCfgReq(BaseModel):
    # v8.7.7: 사내 LLM API 선택적 어댑터 설정.  전부 optional — 저장된 값과 병합.
    enabled: Optional[bool] = None
    api_url: Optional[str] = None
    model: Optional[str] = None
    mode: Optional[str] = None
    admin_token: Optional[str] = None
    provider: Optional[str] = None
    auth_mode: Optional[str] = None
    system_name: Optional[str] = None
    user_id: Optional[str] = None
    user_type: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    format: Optional[str] = None              # "openai" | "raw"
    extra_body: Optional[Dict[str, Any]] = None
    timeout_s: Optional[int] = None


class FlowiDefaultsReq(BaseModel):
    chart_defaults: Optional[Dict[str, Any]] = None
    feedback_policy: Optional[Dict[str, Any]] = None
    engineer_knowledge: Optional[Dict[str, Any]] = None
    agentic: Optional[Dict[str, Any]] = None


class SettingsSaveReq(BaseModel):
    dashboard_refresh_minutes: int = 10
    dashboard_bg_refresh_minutes: int = 10
    lot_progress_refresh_minutes: Optional[int] = None
    splittable_match_refresh_minutes: Optional[int] = None
    tracker_et_match_refresh_minutes: Optional[int] = None
    dashboard_sections: Optional[Dict[str, bool]] = None
    dashboard_fab_progress: Optional[Dict[str, Any]] = None
    data_roots: Optional[DataRootsReq] = None
    backup: Optional[BackupCfgReq] = None
    mail: Optional[MailCfgReq] = None
    llm: Optional[LLMCfgReq] = None
    flowi_defaults: Optional[FlowiDefaultsReq] = None
    flowi_persona: Optional[Dict[str, Any]] = None


@router.post("/settings/save")
def save_settings(req: SettingsSaveReq, request: Request, _admin=Depends(require_admin)):
    """Admin-only via UI gating; backend saves whatever is sent (schema-validated).

    Two stores:
    - settings.json       — refresh intervals etc (legacy schema)
    - admin_settings.json — data_roots.db (core/roots.py reads)
    """
    data = req.dict(exclude_none=True)
    dr_in = data.pop("data_roots", None)
    bk_in = data.pop("backup", None)
    mail_in = data.pop("mail", None)
    llm_in = data.pop("llm", None)
    flowi_defaults_in = data.pop("flowi_defaults", None)
    flowi_persona_in = data.pop("flowi_persona", None)
    # Clamp to sane bounds: dashboard 1..240 minutes, LOT progress cache 1..1440 minutes.
    for k in ("dashboard_refresh_minutes", "dashboard_bg_refresh_minutes"):
        v = data.get(k, 10)
        try:
            v = int(v)
        except Exception:
            v = 10
        data[k] = max(1, min(240, v))
    if "lot_progress_refresh_minutes" in data:
        try:
            lot_progress_minutes = int(data.get("lot_progress_refresh_minutes", 30))
        except Exception:
            lot_progress_minutes = 30
        data["lot_progress_refresh_minutes"] = max(1, min(1440, lot_progress_minutes))
    if "splittable_match_refresh_minutes" in data:
        try:
            st_match = int(data.get("splittable_match_refresh_minutes", 30))
        except Exception:
            st_match = 30
        data["splittable_match_refresh_minutes"] = max(30, min(60, st_match))
    if "tracker_et_match_refresh_minutes" in data:
        try:
            et_match = int(data.get("tracker_et_match_refresh_minutes", 30))
        except Exception:
            et_match = 30
        data["tracker_et_match_refresh_minutes"] = max(30, min(60, et_match))
    if "dashboard_sections" in data:
        raw_sections = data.get("dashboard_sections") or {}
        data["dashboard_sections"] = {
            **DEFAULT_SETTINGS["dashboard_sections"],
            **{k: bool(v) for k, v in raw_sections.items() if k in DEFAULT_SETTINGS["dashboard_sections"]},
        }
    if "dashboard_fab_progress" in data:
        raw_fab = data.get("dashboard_fab_progress") or {}
        if not isinstance(raw_fab, dict):
            raw_fab = {}
        ref = str(raw_fab.get("reference_step_id") or DEFAULT_SETTINGS["dashboard_fab_progress"]["reference_step_id"]).strip().upper()[:80]
        try:
            lots = int(raw_fab.get("sample_lots", DEFAULT_SETTINGS["dashboard_fab_progress"]["sample_lots"]))
        except Exception:
            lots = DEFAULT_SETTINGS["dashboard_fab_progress"]["sample_lots"]
        try:
            days = int(raw_fab.get("days", DEFAULT_SETTINGS["dashboard_fab_progress"]["days"]))
        except Exception:
            days = DEFAULT_SETTINGS["dashboard_fab_progress"]["days"]
        data["dashboard_fab_progress"] = {
            "reference_step_id": ref or DEFAULT_SETTINGS["dashboard_fab_progress"]["reference_step_id"],
            "sample_lots": max(1, min(50, lots)),
            "days": max(1, min(365, days)),
        }
    current_settings = load_json(SETTINGS_FILE, {})
    if not isinstance(current_settings, dict):
        current_settings = {}
    save_json(SETTINGS_FILE, {**current_settings, **data})

    # data_roots → admin_settings.json (merge; empty string → remove override)
    if dr_in is not None:
        current = _load_admin_settings()
        dr = dict(current.get("data_roots") or {})
        profile_update: Dict[str, Any] = {}
        mode = dr_in.get("mode")
        if mode is not None:
            mode = str(mode or "auto").strip().lower()
            if mode not in root_profile.VALID_MODES:
                raise HTTPException(400, f"mode must be one of {sorted(root_profile.VALID_MODES)}")
            profile_update["mode"] = mode
        data_root_val = dr_in.get("data_root")
        if data_root_val is not None:
            if isinstance(data_root_val, str) and data_root_val.strip():
                p = Path(data_root_val.strip()).expanduser()
                if not p.exists() or not p.is_dir():
                    raise HTTPException(400, "data_root must be an existing directory. Create it first, then save.")
                profile_update["data_root"] = str(p)
            else:
                profile_update["data_root"] = ""
        prod_roots = dr_in.get("prod_app_roots")
        if prod_roots is not None:
            clean_roots = []
            for raw in prod_roots or []:
                s = str(raw or "").strip()
                if s:
                    clean_roots.append(s)
            profile_update["prod_app_roots"] = clean_roots
        # v9.0.3: Base/Wafer-map roots are no longer separate Admin-managed roots.
        # Root-level rulebooks/ML_TABLE files are read from DB root; product WF
        # Layout is stored in product config, not an external map library.
        dr.pop("base", None)
        dr.pop("wafer_map", None)
        for ui_key, short_key in _DR_KEY_MAP.items():
            if ui_key not in dr_in:
                continue
            val = dr_in.get(ui_key)
            if val is None or (isinstance(val, str) and not val.strip()):
                # Empty → clear override so resolver falls back to env/default
                dr.pop(short_key, None)
            else:
                p = Path(str(val).strip()).expanduser()
                if not p.exists() or not p.is_dir():
                    raise HTTPException(
                        400,
                        f"{ui_key} must be an existing directory. "
                        "Create/select the DB root shown in File Browser instead of saving a hidden fallback."
                    )
                dr[short_key] = str(p)
                profile_update["db_root"] = str(p)
            if val is None or (isinstance(val, str) and not val.strip()):
                profile_update["db_root"] = ""
        current["data_roots"] = dr
        _save_admin_settings(current)
        if profile_update:
            root_profile.write_profile(profile_update)

    # v8.7.0: backup 설정 저장.
    if bk_in is not None:
        try:
            from core.backup import set_settings as _bk_set
            _bk_set(
                path=bk_in.get("path"),
                interval_hours=bk_in.get("interval_hours"),
                keep=bk_in.get("keep"),
                enabled=bk_in.get("enabled"),
            )
        except Exception:
            pass

    # v8.7.2: 메일 API 설정 저장 — admin_settings.json.mail
    # v8.8.17: `dep_ticket` 단일 필드 편의 — admin 이 헤더 dict 대신 티켓값 한 칸만 넣어도
    #   headers["x-dep-ticket"] 에 자동으로 반영. 기존 headers 맵도 여전히 지원 (merge).
    if mail_in is not None:
        current = _load_admin_settings()
        mail_cur = dict(current.get("mail") or {})
        # v8.8.19: `domain` 추가 — username-only 값 뒤에 @domain 자동 합성.
        for k in ("api_url", "from_addr", "status_code", "dep_ticket", "domain"):
            if mail_in.get(k) is not None:
                v = str(mail_in.get(k) or "").strip()
                if k == "domain":
                    v = v.lstrip("@")   # 허용: "company.co.kr" 또는 "@company.co.kr"
                mail_cur[k] = v
        # headers merge + dep_ticket 자동 반영.
        hdrs_out = dict(mail_cur.get("headers") or {})
        if mail_in.get("headers") is not None:
            hdrs = mail_in.get("headers") or {}
            hdrs_out = {str(k): str(v) for k, v in hdrs.items() if k}
        dt = str(mail_cur.get("dep_ticket") or "").strip()
        if dt:
            hdrs_out["x-dep-ticket"] = dt
        elif "x-dep-ticket" in hdrs_out and mail_in.get("dep_ticket") == "":
            hdrs_out.pop("x-dep-ticket", None)
        mail_cur["headers"] = hdrs_out
        if mail_in.get("extra_data") is not None:
            ed = mail_in.get("extra_data") or {}
            mail_cur["extra_data"] = ed if isinstance(ed, dict) else {}
        if mail_in.get("recipient_groups") is not None:
            rg = mail_in.get("recipient_groups") or {}
            clean: Dict[str, List[str]] = {}
            for gname, emails in rg.items():
                if not gname or not isinstance(emails, list):
                    continue
                clean[str(gname)] = [str(e).strip() for e in emails if str(e).strip() and "@" in str(e)]
            mail_cur["recipient_groups"] = clean
        if mail_in.get("enabled") is not None:
            mail_cur["enabled"] = bool(mail_in.get("enabled"))
        current["mail"] = mail_cur
        _save_admin_settings(current)

    # v8.7.7: 사내 LLM 어댑터 설정 저장 (옵션 기능).
    if llm_in is not None:
        current = _load_admin_settings()
        current_llm = current.get("llm") if isinstance(current.get("llm"), dict) else {}
        requested_provider = _llm_provider(llm_in.get("provider") or current_llm.get("provider"))
        raw_profiles = current.get("llm_profiles") if isinstance(current.get("llm_profiles"), dict) else {}
        base = raw_profiles.get(requested_provider) if isinstance(raw_profiles.get(requested_provider), dict) else {}
        if not base and _llm_provider(current_llm.get("provider")) == requested_provider:
            base = current_llm
        incoming: Dict[str, Any] = {
            k: llm_in.get(k)
            for k in LLM_PROFILE_KEYS
            if llm_in.get(k) is not None
        }
        incoming["provider"] = requested_provider
        llm_cur = _normalize_llm_profile({**base, **incoming}, requested_provider)
        profiles = _llm_profiles_from_admin(current)
        profiles[requested_provider] = llm_cur
        current["llm_profiles"] = profiles
        current["llm"] = llm_cur
        _save_admin_settings(current)

    # v9.0.6: Flow-i home LLM defaults — admin only, read by routers/llm.py at runtime.
    if flowi_defaults_in is not None:
        current = _load_admin_settings()
        cur_defaults = current.get("flowi_defaults") or {}
        current["flowi_defaults"] = _flowi_default_settings(_merge_nested(cur_defaults, flowi_defaults_in))
        _save_admin_settings(current)

    if flowi_persona_in is not None:
        current = _load_admin_settings()
        raw = flowi_persona_in if isinstance(flowi_persona_in, dict) else {}
        current["flowi_persona"] = {
            "enabled": True,
            "system_prompt": str(raw.get("system_prompt") or "").strip()[:12000],
            "must_not": str(raw.get("must_not") or "").strip()[:8000],
            "notes": str(raw.get("notes") or "").strip()[:2000],
            "updated_by": current_user(request).get("username") or "admin",
            "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        _save_admin_settings(current)

    _audit(request, "admin:settings-save",
           detail=f"refresh={data.get('dashboard_refresh_minutes')} data_roots={'yes' if dr_in else 'no'} backup={'yes' if bk_in else 'no'} mail={'yes' if mail_in else 'no'} llm={'yes' if llm_in else 'no'} flowi_defaults={'yes' if flowi_defaults_in is not None else 'no'} flowi_persona={'yes' if flowi_persona_in is not None else 'no'}",
           tab="admin")
    return {"ok": True, "settings": data, "data_roots": (_resolver_snapshot() if dr_in is not None else None)}


# ── LLM presets (P1) ──────────────────────────────────────────────
# Admin-only safe metadata for one-click LLM profile selection. Returns the
# `gpt_oss_120b_internal` (Flow 기본) plus dev fallback presets. Secret fields
# (api_url, admin_token) are NEVER returned — admin fills them in the panel.
@router.get("/llm/presets")
def admin_llm_presets(_admin=Depends(require_admin)):
    return {
        "presets": LLM_NAMED_PRESETS,
        "default_key": next((p["key"] for p in LLM_NAMED_PRESETS if p.get("is_default")), ""),
        "saved_profiles": _llm_profiles_from_admin(_load_admin_settings()),
    }


# ── Backup (v8.7.0) ────────────────────────────────────────────────
@router.get("/backup/status")
def backup_status(_admin=Depends(require_admin)):
    from core.backup import get_settings, list_backups
    return {"settings": get_settings(), "backups": list_backups()}


@router.post("/backup/run")
def backup_run(request: Request, _admin=Depends(require_admin)):
    from core.backup import run_backup
    info = run_backup(reason="manual")
    _audit(request, "admin:backup-run",
           detail=f"ok={info.get('ok')} size={info.get('bytes')} err={info.get('error','')[:80]}",
           tab="admin")
    return info


@router.post("/backup/restore")
def backup_restore(req: BackupRestoreReq, request: Request, _admin=Depends(require_admin)):
    from core.backup import restore_backup
    info = restore_backup(req.filename, restore_db_root_files=req.restore_db_root_files)
    _audit(request, "admin:backup-restore",
           detail=f"ok={info.get('ok')} file={req.filename} restored={info.get('restored')} err={info.get('error','')[:80]}",
           tab="admin")
    if not info.get("ok"):
        raise HTTPException(400, info.get("error") or "restore failed")
    return info


# ── v8.8.14: Scheduled one-off backup ──────────────────────────────────
# 서버 점검 예정 시 admin 이 "특정 시각에 백업 실행" 을 예약. 스케줄러가 1분 단위로
# admin_settings.backup.scheduled_at 를 폴링해서 시각이 지나면 실행하고 필드 비운다.
@router.post("/backup/schedule")
def backup_schedule(req: BackupScheduleReq, request: Request, _admin=Depends(require_admin)):
    """`at` 이 비어있으면 예약 취소. ISO datetime (예: 2026-04-22T23:30:00) 필요."""
    import datetime as _dt
    cfg = load_json(ADMIN_SETTINGS_FILE, {})
    bk = dict(cfg.get("backup") or {})
    at = (req.at or "").strip()
    if not at:
        bk.pop("scheduled_at", None); bk.pop("scheduled_reason", None)
        cfg["backup"] = bk
        save_json(ADMIN_SETTINGS_FILE, cfg)
        _audit(request, "admin:backup-schedule-cancel", tab="admin")
        return {"ok": True, "scheduled_at": None}
    # Parse ISO for validation (Python 3.11+; polyfill for offset)
    try:
        _ = _dt.datetime.fromisoformat(at.replace("Z", "+00:00"))
    except Exception:
        raise HTTPException(400, f"Invalid ISO datetime: {at!r}")
    bk["scheduled_at"] = at
    bk["scheduled_reason"] = (req.reason or "pre-maintenance").strip()[:40] or "pre-maintenance"
    cfg["backup"] = bk
    save_json(ADMIN_SETTINGS_FILE, cfg)
    _audit(request, "admin:backup-schedule", detail=f"at={at} reason={bk['scheduled_reason']}", tab="admin")
    return {"ok": True, "scheduled_at": at, "reason": bk["scheduled_reason"]}


# ── v8.8.14: Per-page admin delegation ─────────────────────────────────
@router.get("/page-admins")
def page_admins_get(_admin=Depends(require_admin)):
    """현재 admin_settings 의 page_admins 맵 전체. Admin UI 에서 편집용."""
    return {"page_admins": get_page_admins()}


@router.post("/page-admins")
def page_admins_set(req: PageAdminsReq, request: Request, _admin=Depends(require_admin)):
    """page_id → usernames 목록을 설정 (빈 리스트면 해당 페이지 위임 제거)."""
    page_id = canonical_page_id(req.page_id)
    if not page_id:
        raise HTTPException(400, "page_id required")
    before = get_page_admins()
    valid_users = {u["username"] for u in read_users() if u.get("status") == "approved"}
    users = [u for u in (req.usernames or []) if u in valid_users]
    data = load_json(ADMIN_SETTINGS_FILE, {})
    pa = dict(before)
    if users:
        pa[page_id] = sorted(set(users))
    else:
        pa.pop(page_id, None)
    data["page_admins"] = pa
    save_json(ADMIN_SETTINGS_FILE, data)
    _audit(request, "admin:page-admins-set",
           detail=f"actor={current_user(request).get('username') or ''};page={page_id};before={before.get(page_id) or []};after={pa.get(page_id) or []}", tab="admin")
    return {"ok": True, "page_admins": pa}


# ── v9.1.x: S3 전역 마스터 스위치 ─────────────────────────────────────
class S3MasterReq(BaseModel):
    enabled: bool = True


@router.get("/s3-master")
def s3_master_get(_admin=Depends(require_admin)):
    """S3 전역 스위치 상태. admin_settings.json `s3_master_enabled` (기본 True)."""
    from core.s3_sync import master_enabled
    return {"enabled": master_enabled()}


@router.post("/s3-master")
def s3_master_set(req: S3MasterReq, request: Request, _admin=Depends(require_admin)):
    """S3 전체 켜기/끄기 — 주기 스케줄·아티팩트 업로드·수동 run/push 모두 통제.
    공유 flow-data 에 저장되므로 개발/운영 서버에 함께 적용된다."""
    data = load_json(ADMIN_SETTINGS_FILE, {})
    data["s3_master_enabled"] = bool(req.enabled)
    save_json(ADMIN_SETTINGS_FILE, data)
    _audit(request, "admin:s3-master", detail=f"enabled={bool(req.enabled)}", tab="admin")
    return {"ok": True, "enabled": bool(req.enabled)}


@router.get("/my-page-admin")
def my_page_admin(request: Request):
    """현재 유저가 위임받은 page 목록. global admin 은 전체 True + is_global_admin=true 반환."""
    u = current_user(request)
    pa = get_page_admins()
    uname = u.get("username", "")
    pages = sorted([pid for pid, lst in pa.items() if uname in (lst or [])])
    return {
        "username": uname,
        "role": u.get("role", "user"),
        "is_global_admin": u.get("role") == "admin",
        "pages": pages,
    }


# ── v8.8.14: Activity dashboard — 누가 / 어떤 기능을 / 얼마나 썼는지 ──
@router.get("/activity/summary")
def activity_summary(days: int = Query(7), _admin=Depends(require_admin)):
    """최근 N 일 activity.jsonl 을 집계.
    반환:
      - total: 총 이벤트 수
      - by_user: { username: count } (top 20)
      - by_action: { action: count } (top 30)
      - by_tab:    { tab: count }
      - by_day:    { "YYYY-MM-DD": count }
      - active_users_by_day:   최근 30일의 일별 순 사용자 수
      - active_users_by_month: 최근 12개월의 월별 순 사용자 수
      - recent:    최근 3000건 (내림차순)
    """
    import datetime as _dt, collections
    try:
        days = max(1, min(90, int(days)))
    except Exception:
        days = 7
    # limit=0 → 전체 로드. 기본 limit(200)이면 최근 200건만 필터 대상이라, 바쁜 서버에서
    # 그 200건이 전부 오늘치가 되어 1/7/30일 을 늘려도 '오늘 것만' 보이던 버그. jsonl_read 는
    # 어차피 전체 파일을 읽어 모든 줄을 파싱한 뒤 슬라이스하므로 limit=0 이어도 추가 비용 없음.
    rows = list(jsonl_read(ACTIVITY_LOG, limit=0) or [])
    cutoff = _dt.datetime.now() - _dt.timedelta(days=days)
    by_user = collections.Counter()
    by_action = collections.Counter()
    by_tab = collections.Counter()
    by_day = collections.Counter()
    active_users_by_day: dict[str, set[str]] = collections.defaultdict(set)
    active_users_by_month: dict[str, set[str]] = collections.defaultdict(set)
    filtered: list = []
    now = _dt.datetime.now()
    active_day_start = now.date() - _dt.timedelta(days=29)
    active_month_start_index = now.year * 12 + now.month - 12
    for r in rows:
        ts = (r.get("timestamp") or r.get("time") or "").strip()
        try:
            dt = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
        except Exception:
            continue
        # 활동 사용자 차트는 요약 기간과 독립적으로 최근 30일/12개월을 고정 제공한다.
        # 그래야 대시보드의 1·7·30·90일 이벤트 필터를 바꿔도 월별 차트가 한 달짜리로
        # 축소되지 않는다. 인증되지 않은 시스템 기록은 사용자 수에서 제외한다.
        username = str(r.get("username") or r.get("actor") or "").strip()
        is_authenticated_user = bool(username and username.lower() != "anonymous")
        if is_authenticated_user and active_day_start <= dt.date() <= now.date():
            active_users_by_day[dt.strftime("%Y-%m-%d")].add(username)
        month_index = dt.year * 12 + dt.month - 1
        if is_authenticated_user and active_month_start_index <= month_index <= (now.year * 12 + now.month - 1):
            active_users_by_month[dt.strftime("%Y-%m")].add(username)
        if dt < cutoff:
            continue
        filtered.append(r)
        u = username or "anonymous"
        by_user[u] += 1
        a = (r.get("action") or "") or "(unknown)"
        by_action[a] += 1
        t = (r.get("tab") or "") or "(none)"
        by_tab[t] += 1
        by_day[dt.strftime("%Y-%m-%d")] += 1
    filtered.sort(key=lambda r: r.get("timestamp") or r.get("time") or "", reverse=True)
    daily_user_counts = {}
    for offset in range(30):
        key = (active_day_start + _dt.timedelta(days=offset)).strftime("%Y-%m-%d")
        daily_user_counts[key] = len(active_users_by_day.get(key, set()))
    monthly_user_counts = {}
    for offset in range(12):
        index = active_month_start_index + offset
        key = f"{index // 12:04d}-{index % 12 + 1:02d}"
        monthly_user_counts[key] = len(active_users_by_month.get(key, set()))
    return {
        "window_days": days,
        "total": len(filtered),
        "by_user": dict(by_user.most_common(20)),
        "by_action": dict(by_action.most_common(30)),
        "by_tab": dict(by_tab.most_common()),
        "by_day": dict(sorted(by_day.items())),
        "active_users_by_day": daily_user_counts,
        "active_users_by_month": monthly_user_counts,
        "recent": filtered[:3000],
        "activity_storage": {
            "path": str(ACTIVITY_LOG),
            "relative_path": "flow-data/logs/activity.jsonl",
            "size_bytes": ACTIVITY_LOG.stat().st_size if ACTIVITY_LOG.exists() else 0,
            "max_bytes": ACTIVITY_LOG_MAX_BYTES,
        },
    }


@router.get("/activity/features")
def activity_features(days: int = Query(30), _admin=Depends(require_admin)):
    """`action` prefix 단위로 기능 사용 현황. 각 기능(=action prefix)의 first_seen /
    last_seen / users(사용한 유저 집합) / count 를 반환. admin 이 "어떤 기능이 활성화
    되어 있는지" 한눈에 파악하는 용도.
    """
    import datetime as _dt, collections
    try:
        days = max(1, min(365, int(days)))
    except Exception:
        days = 30
    # limit=0 → 전체 로드 (기본 200 이면 최근 200건만 집계되어 오래된 날짜가 빠짐).
    rows = list(jsonl_read(ACTIVITY_LOG, limit=0) or [])
    cutoff = _dt.datetime.now() - _dt.timedelta(days=days)
    features: dict = {}
    for r in rows:
        ts = (r.get("timestamp") or r.get("time") or "").strip()
        try:
            dt = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
        except Exception:
            continue
        if dt < cutoff:
            continue
        a = (r.get("action") or "").strip()
        if not a:
            continue
        # prefix = "domain:verb" 같이 ':' 로 구분된 앞부분 (예: inform:create / splittable:plan)
        key = a.split(":", 1)[0] if ":" in a else a
        ent = features.setdefault(key, {
            "name": key, "count": 0, "users": set(),
            "first_seen": ts, "last_seen": ts,
            "sample_actions": collections.Counter(),
        })
        ent["count"] += 1
        ent["users"].add((r.get("username") or r.get("actor") or "anonymous") or "anonymous")
        if ts < ent["first_seen"]:
            ent["first_seen"] = ts
        if ts > ent["last_seen"]:
            ent["last_seen"] = ts
        ent["sample_actions"][a] += 1
    out = []
    for k, v in sorted(features.items(), key=lambda kv: -kv[1]["count"]):
        out.append({
            "feature": k,
            "count": v["count"],
            "user_count": len(v["users"]),
            "users": sorted(v["users"])[:20],
            "first_seen": v["first_seen"],
            "last_seen": v["last_seen"],
            "top_actions": dict(v["sample_actions"].most_common(5)),
        })
    return {"window_days": days, "features": out, "feature_count": len(out)}


# ── Base CSV editor (v8.5.2) ──
# Admin only. step_matching.csv / knob_ppid.csv 를 직접 표로 편집.
import csv as _csv
BASE_CSV_SCHEMAS = {
    "step_matching": {
        "columns": ["step_id", "func_step"],
        "unique_key": ["step_id"],
    },
    "knob_ppid": {
        "columns": ["feature_name", "function_step", "rule_order", "ppid", "operator", "category", "use"],
        "unique_key": ["feature_name", "function_step", "rule_order"],
    },
    # v8.7.5: INLINE prefix 항목 매칭 — SplitTable 에서 item_desc 로 표시.
    "inline_matching": {
        "columns": ["product", "step_id", "item_id", "item_desc", "matching_table"],
        "unique_key": ["product", "step_id", "item_id"],
    },
    # v8.7.5: VM_ prefix 항목 매칭 — step_id 는 Vehicle_matching.csv 에서 product+step_desc 로 확장.
    "vm_matching": {
        "columns": ["step_desc", "item_id"],
        "unique_key": ["step_desc", "item_id"],
    },
}


def _base_csv_path(name: str) -> Path:
    from core.paths import PATHS
    # v8.4.6 이슈: path traversal 방어 — name 은 whitelist 화.
    if name not in BASE_CSV_SCHEMAS:
        raise HTTPException(400, f"Unknown csv: {name}")
    base = Path(str(PATHS.base_root)).resolve()
    fp = (base / f"{name}.csv").resolve()
    try:
        fp.relative_to(base)
    except ValueError:
        raise HTTPException(400, "Invalid path")
    return fp


@router.get("/base-csv")
def base_csv_get(name: str = Query(...), _admin=Depends(require_admin)):
    fp = _base_csv_path(name)
    schema = BASE_CSV_SCHEMAS[name]
    rows: List[List[str]] = []
    if fp.exists():
        with open(fp, "r", encoding="utf-8-sig", newline="") as f:
            reader = _csv.reader(f)
            header = next(reader, None)
            for r in reader:
                # pad/trim to match schema length
                if len(r) < len(schema["columns"]):
                    r = r + [""] * (len(schema["columns"]) - len(r))
                rows.append(r[: len(schema["columns"])])
    return {
        "name": name,
        "columns": schema["columns"],
        "unique_key": schema["unique_key"],
        "rows": rows,
    }


class BaseCsvSaveReq(BaseModel):
    name: str
    rows: List[List[str]] = []


@router.put("/base-csv")
def base_csv_save(req: BaseCsvSaveReq, _admin=Depends(require_admin)):
    if req.name not in BASE_CSV_SCHEMAS:
        raise HTTPException(400, f"Unknown csv: {req.name}")
    schema = BASE_CSV_SCHEMAS[req.name]
    cols = schema["columns"]
    fp = _base_csv_path(req.name)

    # validation: drop empty rows + check unique key
    cleaned: List[List[str]] = []
    seen_keys = set()
    for raw in req.rows:
        r = [(x if x is not None else "").strip() for x in raw]
        if len(r) < len(cols):
            r = r + [""] * (len(cols) - len(r))
        r = r[: len(cols)]
        if all(not v for v in r):
            continue  # skip fully-empty
        # unique key
        key_idx = [cols.index(k) for k in schema["unique_key"]]
        key = tuple(r[i] for i in key_idx)
        if any(not k for k in key):
            raise HTTPException(400, f"unique key empty: {schema['unique_key']}")
        if key in seen_keys:
            raise HTTPException(400, f"duplicate unique key: {key}")
        seen_keys.add(key)
        # `use` 필드 검증 (knob_ppid)
        if req.name == "knob_ppid":
            u = r[cols.index("use")].upper()
            if u not in ("", "Y", "N", "0", "1"):
                raise HTTPException(400, f"invalid use value: {u}")
            r[cols.index("use")] = u or "Y"
        cleaned.append(r)

    # atomic write (UTF-8 w/ BOM for Excel compat)
    tmp = fp.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        writer = _csv.writer(f)
        writer.writerow(cols)
        writer.writerows(cleaned)
    tmp.replace(fp)

    # audit
    from core.auth import current_user
    from fastapi import Request as _Req  # noqa
    append_activity({
        "username": "admin",
        "action": f"base-csv:save:{req.name}",
        "tab": "admin",
        "detail": f"rows={len(cleaned)}",
        "timestamp": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
    })
    sync_result = _s3.sync_saved_path(PATHS.data_root, PATHS.db_root, fp)
    return {"ok": True, "rows_saved": len(cleaned), "path": str(fp), "s3_sync": sync_result}


@router.get("/qa/report")
def qa_report(_admin=Depends(require_admin)):
    data = load_json(QA_REPORT_FILE, {"runs": []})
    runs = data.get("runs") if isinstance(data, dict) else []
    return {"ok": True, "report": data if isinstance(data, dict) else {"runs": []}, "latest": (runs[0] if isinstance(runs, list) and runs else None)}


@router.post("/qa/trigger")
def qa_trigger(_admin=Depends(require_admin)):
    if not QA_SCRIPT.exists():
        raise HTTPException(404, "e2e_qa.py not found")
    proc = subprocess.run(
        [sys.executable, str(QA_SCRIPT)],
        cwd=str(PATHS.data_root.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    payload = {
        "ok": proc.returncode == 0,
        "code": proc.returncode,
        "stdout": (proc.stdout or "").strip()[:4000],
        "stderr": (proc.stderr or "").strip()[:4000],
    }
    if proc.returncode != 0:
        raise HTTPException(500, payload)
    return payload
