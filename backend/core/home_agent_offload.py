"""Run home agent (Flow-i data chat) turns on the development worker when free.

A home turn is dominated by LLM round trips and raw DB reads (Inline/ET charts,
wafer maps, reports). Those move to the worker so the production API keeps its
CPU and memory for SplitTable and interactive screens. Light turns that are
answered from production's warm caches (SplitTable view, WIP location) and
approvals stay local.

Production never depends on the worker: offline, overloaded, a different
code version, or a missing LLM connection on the worker all run the same turn
locally. Conversation state is persisted by the API caller, and every side
effect of a turn lives in the shared data_root, so either server can serve it.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException

TASK_TYPE = "home_agent_turn"
_DECISION = re.compile(r"승인|반영|삭제|취소|\b(?:approve|apply|delete|commit|cancel)\b", re.I)
_SPLIT_VIEW = re.compile(r"스플릿\s*테이블|split\s*table|splittable", re.I)
_HEAVY_TERMS = re.compile(
    r"차트|그려|그래프|chart|plot|추이|trend|상관|corr|분포|산포|box|scatter|맵|map|리포트|report|"
    r"분석|원인|비교|inline|et\b|수율|yield|shot|측정|평균|median|통계", re.I)


def _disabled() -> bool:
    return str(os.environ.get("FLOW_HOME_AGENT_OFFLOAD", "1")).strip().lower() in {"0", "false", "no", "off"}


def _timeout_sec() -> float:
    try:
        value = float(os.environ.get("FLOW_HOME_AGENT_OFFLOAD_TIMEOUT_SEC", "") or 600.0)
    except ValueError:
        value = 600.0
    return max(30.0, min(3600.0, value))


@lru_cache(maxsize=1)
def code_version() -> str:
    """Release version of this checkout; a worker on another version must not answer."""
    root = Path(__file__).resolve().parents[2]
    for name in ("VERSION.json", "version.json"):
        try:
            value = json.loads((root / name).read_text(encoding="utf-8")).get("version")
        except (OSError, ValueError, AttributeError):
            continue
        if value:
            return str(value)
    return "unknown"


def execution_class(prompt: str) -> tuple[str, str]:
    """Split a turn into production-local (light) and worker-first (heavy)."""
    text = re.sub(r"\s+", " ", str(prompt or "")).strip()
    if _DECISION.search(text):
        return "light", "변경 승인·취소는 운영 서버에서 처리"
    try:
        from core import data_chat_semantic_admin
        if data_chat_semantic_admin.is_candidate(prompt):
            return "light", "시맨틱 별칭 변경·확인은 운영 서버에서 처리"
    except Exception:
        pass
    heavy = bool(_HEAVY_TERMS.search(text))
    if _SPLIT_VIEW.search(text) and not heavy:
        return "light", "SplitTable 조회는 운영 캐시 사용"
    try:
        from core import lot_wip
        if not heavy and lot_wip.is_wip_prompt(text):
            return "light", "WIP 현재 위치는 운영 latest cache 사용"
    except Exception:
        pass
    if heavy:
        return "heavy", "차트·분석·원본 데이터 조회"
    return "heavy", "LLM 해석과 데이터 조회"


def _public_user(user: dict) -> dict:
    return {key: value for key, value in (user or {}).items()
            if "token" not in str(key).lower() and key not in {"issued_at", "last_seen"}}


def _server_role() -> str:
    try:
        from core import worker_dispatch
        return str(worker_dispatch.server_role() or "api")
    except Exception:
        return "api"


def run_turn(prompt: str, context: dict, request, history: list | None = None, *, user: dict | None = None) -> dict:
    """Execute one home turn, preferring the development worker for heavy turns."""
    from core import flowi_turn

    history = list(history or [])
    local_called = False

    def _local() -> dict:
        nonlocal local_called
        local_called = True
        return {"ok": True, "result": flowi_turn.execute(prompt, context, request, history=history)}

    klass, reason = execution_class(prompt)
    role = _server_role()
    if _disabled() or klass == "light" or role != "api":
        envelope = _local()
    else:
        from core import worker_dispatch
        envelope = worker_dispatch.run_heavy(
            TASK_TYPE,
            {
                "prompt": prompt,
                "context": context,
                "history": history,
                "user": _public_user(user or {}),
                "code_version": code_version(),
            },
            _local,
            timeout_sec=_timeout_sec(),
            label="home_agent_turn",
            priority="interactive",
        ) or {}
    http_error = envelope.get("http_error") if isinstance(envelope, dict) else None
    if isinstance(http_error, dict) and http_error.get("status"):
        raise HTTPException(int(http_error["status"]), http_error.get("detail"))
    result = envelope.get("result") if isinstance(envelope, dict) else None
    if not isinstance(result, dict):
        result = flowi_turn.execute(prompt, context, request, history=history)
        local_called = True
    if role == "worker":
        target = "development_worker"
    elif klass == "light" or _disabled():
        target = "production_api"
    else:
        target = "production_api_fallback" if local_called else "development_worker"
    result["execution"] = {"class": klass, "target": target, "reason": reason}
    return result


def _synthetic_request(user: dict):
    from starlette.requests import Request

    request = Request({
        "type": "http", "method": "POST", "path": "/api/home-agent/orchestrate",
        "raw_path": b"/api/home-agent/orchestrate", "root_path": "", "scheme": "http",
        "query_string": b"", "headers": [(b"user-agent", b"flow-worker/home-agent")],
        "client": ("development-worker", 0), "server": ("development-worker", 0),
    })
    # auth.current_user() and audit resolve the user from request.state first.
    request.state.user = dict(user)
    return request


def execute_payload(payload: dict) -> dict:
    """Worker-side entry: run the turn exactly as the API would."""
    if str(payload.get("code_version") or "") != code_version():
        return {"ok": False, "error": "version_mismatch"}
    user = payload.get("user") or {}
    if not isinstance(user, dict) or not user.get("username"):
        return {"ok": False, "error": "missing_user"}
    from core import flowi_turn, llm_adapter

    if not llm_adapter.is_available():
        return {"ok": False, "error": "llm_unavailable_on_worker"}
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    history = payload.get("history") if isinstance(payload.get("history"), list) else []
    try:
        result = flowi_turn.execute(str(payload.get("prompt") or ""), context,
                                    _synthetic_request(user), history=history)
    except HTTPException as exc:
        return {"ok": True, "http_error": {"status": int(exc.status_code), "detail": exc.detail}}
    # The result file is plain JSON; anything the API could not serialize
    # either would otherwise lose the reply and make the caller wait.
    return {"ok": True, "result": json.loads(json.dumps(result, ensure_ascii=False, default=str))}
