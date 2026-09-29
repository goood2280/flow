"""Per-user question quota for the home agent (Flow-i data chat).

Administrators are unlimited. Every other user with the ``flowi`` permission may
ask ``FLOW_FLOWI_USER_QUESTIONS_PER_MIN`` questions (default 25) in any sliding
60-second window. A request that bundles several questions (line breaks, "?")
spends one per question.

Human-in-the-loop replies are free: when the stored conversation is waiting for
the user's choice (product, TEG, custom set, approval ...), the answer continues
an already-counted question. The pending state is read from the server-side
conversation, never from the client, and free replies are themselves capped
(``FLOW_FLOWI_FOLLOWUPS_PER_MIN``, default 10) so a loop cannot bypass the quota.

The counter lives in data_root like core.llm_usage, so hosts that share the data
root share one budget.
"""
from __future__ import annotations

import json
import math
import os
import time
from typing import Any

from core import llm_usage
from core.paths import PATHS
from core.utils import save_json

WINDOW_SECONDS = 60
DEFAULT_QUESTIONS_PER_MIN = 25
DEFAULT_FOLLOWUPS_PER_MIN = 10


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(os.environ.get(name, "") or default)
    except ValueError:
        value = default
    return max(lo, min(hi, value))


def question_limit() -> int:
    """0 disables the quota."""
    return _env_int("FLOW_FLOWI_USER_QUESTIONS_PER_MIN", DEFAULT_QUESTIONS_PER_MIN, 0, 60)


def followup_limit() -> int:
    return _env_int("FLOW_FLOWI_FOLLOWUPS_PER_MIN", DEFAULT_FOLLOWUPS_PER_MIN, 0, 120)


def exempt(user: dict | None) -> bool:
    return str((user or {}).get("role") or "") == "admin" or question_limit() == 0


def _path():
    return PATHS.data_root / "llm" / "flowi_user_quota.json"


def _stamps(value: Any, now: float) -> list[float]:
    if not isinstance(value, list):
        return []
    return sorted(float(t) for t in value
                  if isinstance(t, (int, float)) and not isinstance(t, bool) and math.isfinite(t)
                  and now - WINDOW_SECONDS < t <= now + WINDOW_SECONDS)


def _load(path, now: float) -> dict[str, dict[str, list[float]]]:
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    users = raw.get("users") if isinstance(raw, dict) else None
    if not isinstance(users, dict):
        return {}
    out = {}
    for name, row in users.items():
        if not isinstance(row, dict):
            continue
        entry = {kind: _stamps(row.get(kind), now) for kind in ("questions", "followups")}
        if entry["questions"] or entry["followups"]:
            out[str(name)] = entry
    return out


def awaiting_input(context: dict | None, messages: list | None = None) -> bool:
    """Whether the stored conversation is waiting for the user's choice."""
    if any(str(key).startswith("pending_") and value for key, value in (context or {}).items()):
        return True
    last = next((m for m in reversed(messages or []) if isinstance(m, dict) and m.get("role") == "assistant"), None)
    response = (last or {}).get("response") or {}
    if (response.get("routing_trace") or {}).get("status") == "needs_input":
        return True
    return any(isinstance(q, dict) and q.get("status") == "needs_input" for q in response.get("questions") or [])


def _view(questions: list[float], now: float, limit: int, **extra) -> dict[str, Any]:
    used = len(questions)
    retry = max(1, math.ceil(questions[used - limit] + WINDOW_SECONDS - now)) if limit and used >= limit else 0
    return {"limited": True, "limit": limit, "used": min(used, limit), "remaining": max(0, limit - used),
            "window_seconds": WINDOW_SECONDS, "retry_after_s": retry, **extra}


def unlimited_view() -> dict[str, Any]:
    return {"limited": False, "limit": 0, "used": 0, "remaining": None, "window_seconds": WINDOW_SECONDS, "retry_after_s": 0}


def snapshot(user: dict | None) -> dict[str, Any]:
    if exempt(user):
        return unlimited_view()
    now = time.time()
    try:
        entry = _load(_path(), now).get(str((user or {}).get("username") or ""), {})
    except OSError:
        entry = {}
    return _view(entry.get("questions", []), now, question_limit())


def consume(user: dict | None, questions: int, *, followup: bool) -> dict[str, Any]:
    """Reserve quota for one request. ``allowed`` False means: do not run it.

    Returns the quota view plus ``allowed``, ``charged`` (questions spent) and
    ``free_followup`` (a HITL reply that spent nothing)."""
    if exempt(user):
        return {**unlimited_view(), "allowed": True, "charged": 0, "free_followup": False}
    username = str((user or {}).get("username") or "").strip() or "user"
    limit = question_limit()
    cost = max(1, int(questions or 1))
    path = _path()
    with llm_usage._store_lock(path):
        now = time.time()
        users = _load(path, now)
        entry = users.setdefault(username, {"questions": [], "followups": []})
        free = followup and cost == 1 and len(entry["followups"]) < followup_limit()
        if free:
            entry["followups"].append(now)
            result = {"allowed": True, "charged": 0, "free_followup": True}
        elif cost > limit:
            result = {"allowed": False, "charged": 0, "free_followup": False, "reason": "batch_too_large", "requested": cost}
        elif len(entry["questions"]) + cost > limit:
            result = {"allowed": False, "charged": 0, "free_followup": False, "reason": "rate_limited", "requested": cost}
        else:
            entry["questions"].extend([now] * cost)
            result = {"allowed": True, "charged": cost, "free_followup": False}
        if result["allowed"]:
            save_json(path, {"users": users})
        view = _view(entry["questions"], now, limit)
        if not result["allowed"] and result["reason"] == "rate_limited":
            # Seconds until enough of the window frees up for this request.
            need = len(entry["questions"]) + cost - limit
            view["retry_after_s"] = max(1, math.ceil(entry["questions"][need - 1] + WINDOW_SECONDS - now))
        return {**view, **result}


def refund(user: dict | None, reservation: dict | None) -> None:
    """Give back a reservation whose turn failed on the server side."""
    if not reservation or not reservation.get("allowed") or exempt(user):
        return
    username = str((user or {}).get("username") or "").strip() or "user"
    path = _path()
    try:
        with llm_usage._store_lock(path):
            now = time.time()
            users = _load(path, now)
            entry = users.get(username)
            if not entry:
                return
            kind, count = ("followups", 1) if reservation.get("free_followup") else ("questions", int(reservation.get("charged") or 0))
            if count:
                entry[kind] = entry[kind][:-count]
            save_json(path, {"users": users})
    except (OSError, TimeoutError):
        pass


def denied_message(quota: dict[str, Any]) -> str:
    limit = quota.get("limit")
    if quota.get("reason") == "batch_too_large":
        return (f"일반 사용자는 1분에 질문 {limit}개까지 사용할 수 있어 한 번에 {quota.get('requested')}개 질문은 보낼 수 없습니다.\n"
                f"질문을 {limit}개 이하로 나누어 보내 주세요. 아직 실행하지 않았습니다.")
    return (f"질문 한도(1분에 {limit}개)를 모두 사용했습니다. {quota.get('retry_after_s')}초 뒤에 다시 질문해 주세요.\n"
            "선택·확인을 묻는 질문에 대한 답은 한도와 관계없이 계속 보낼 수 있습니다. 아직 실행하지 않았습니다.")


def denied_payload(quota: dict[str, Any]) -> dict[str, Any]:
    """Chat-shaped reply for a request the quota stopped (not an HTTP error)."""
    public = {key: value for key, value in quota.items() if key not in {"allowed", "charged", "free_followup"}}
    return {
        "ok": False,
        "type": "answer",
        "handled": True,
        "intent": "flowi_rate_limited",
        "answer": denied_message(quota),
        "rate_limited": True,
        "quota": public,
    }
