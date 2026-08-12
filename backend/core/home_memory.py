"""Server-side Home Flow-i conversation memory.

The Home UI already sends recent client-side messages. This module keeps a
small server-side Q/A trail per user so follow-up prompts still have context
after refreshes, API calls, or other clients.
"""
from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.paths import PATHS
from core.utils import jsonl_append, jsonl_read, jsonl_trim


MEMORY_FILE: Path = PATHS.data_root / "home_agent_memory" / "conversation.jsonl"
MAX_MEMORY_ROWS = 1000
MAX_CONTEXT_MESSAGES = 12

_TOOL_CONTEXT_KEYS = {
    "feature",
    "action",
    "intent",
    "blocked",
    "requires_confirmation",
    "missing",
    "arguments",
    "arguments_partial",
    "arguments_choices",
    "missing_freetext",
    "pending_prompt",
    "last_partial_prompt",
    "created_record",
    "slots",
    "filters",
    "workflow_state",
    "chart_session_id",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(value: Any, limit: int = 1200) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text[: max(1, limit)]


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _clean_text(value, 600)
    if depth >= 3:
        return _clean_text(value, 300)
    if isinstance(value, list):
        return [_safe_value(item, depth=depth + 1) for item in value[:20]]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= 30:
                break
            if str(key) in {"rows", "sample_rows", "data", "blocks"}:
                continue
            out[_clean_text(key, 120)] = _safe_value(item, depth=depth + 1)
        return out
    return _clean_text(value, 300)


def _safe_tool_summary(tool: Any) -> dict[str, Any]:
    if not isinstance(tool, dict):
        return {}
    out = {key: _safe_value(tool.get(key)) for key in _TOOL_CONTEXT_KEYS if key in tool}
    table = tool.get("table") if isinstance(tool.get("table"), dict) else {}
    if table:
        out["table_kind"] = _clean_text(table.get("kind") or "", 120)
        out["table_total"] = table.get("total")
    split_view = tool.get("split_view") if isinstance(tool.get("split_view"), dict) else {}
    if split_view:
        out["split_view_kind"] = _clean_text(split_view.get("kind") or "", 120)
        out["split_view_total"] = split_view.get("total")
    chart = tool.get("chart_result") if isinstance(tool.get("chart_result"), dict) else {}
    if chart:
        out["chart_session_id"] = _clean_text(chart.get("chart_session_id") or tool.get("chart_session_id") or "", 160)
        out["chart_kind"] = _clean_text(chart.get("kind") or chart.get("chart_type") or "", 120)
    return out


def _username(value: Any) -> str:
    return _clean_text(value, 80) or "user"


def _memory_id(username: str, prompt: str, answer: str, created_at: str) -> str:
    raw = f"{username}|{created_at}|{prompt}|{answer}".encode("utf-8")
    return "home_mem_" + hashlib.sha1(raw).hexdigest()[:16]


def remember_turn(
    *,
    username: str,
    prompt: str,
    answer: str,
    tool: dict[str, Any] | None = None,
    source: str = "home_flowi",
    run_id: str = "",
) -> dict[str, Any] | None:
    prompt_text = _clean_text(prompt, 2000)
    answer_text = _clean_text(answer, 2000)
    if not prompt_text and not answer_text:
        return None
    tool_summary = _safe_tool_summary(tool or {})
    if tool_summary.get("intent") == "home_memory_recall":
        return None
    actor = _username(username)
    created_at = _now_iso()
    row = {
        "memory_id": _memory_id(actor, prompt_text, answer_text, created_at),
        "timestamp": created_at,
        "username": actor,
        "source": _clean_text(source, 80),
        "run_id": _clean_text(run_id, 160),
        "prompt": prompt_text,
        "answer": answer_text,
        "tool": tool_summary,
    }
    jsonl_append(MEMORY_FILE, row, add_timestamp=False)
    jsonl_trim(MEMORY_FILE, MAX_MEMORY_ROWS)
    return row


def recent_turns(username: str, *, limit: int = 6) -> list[dict[str, Any]]:
    actor = _username(username)

    def visible(row: dict[str, Any]) -> bool:
        return not row.get("username") or row.get("username") == actor

    rows = jsonl_read(MEMORY_FILE, limit=500, filter_fn=visible)
    rows.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
    return rows[: max(0, min(int(limit or 0), 50))]


def _messages_from_turn(row: dict[str, Any]) -> list[dict[str, Any]]:
    prompt = _clean_text(row.get("prompt"), 1200)
    answer = _clean_text(row.get("answer"), 1200)
    memory_id = _clean_text(row.get("memory_id"), 80)
    tool = row.get("tool") if isinstance(row.get("tool"), dict) else {}
    messages: list[dict[str, Any]] = []
    if prompt:
        messages.append({
            "role": "user",
            "prompt": prompt,
            "text": prompt,
            "source": "server_memory",
            "memory_id": memory_id,
        })
    assistant = {
        "role": "assistant",
        "prompt": prompt,
        "text": answer,
        "answer_excerpt": answer[:600],
        "source": "server_memory",
        "memory_id": memory_id,
    }
    for key, value in tool.items():
        assistant[key] = deepcopy(value)
    if answer or len(assistant) > 5:
        messages.append(assistant)
    return messages


def recent_context_messages(username: str, *, turns: int = 4) -> list[dict[str, Any]]:
    rows = list(reversed(recent_turns(username, limit=turns)))
    messages: list[dict[str, Any]] = []
    for row in rows:
        messages.extend(_messages_from_turn(row))
    return messages


def _message_key(message: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        _clean_text(message.get("role"), 30),
        _clean_text(message.get("prompt"), 400),
        _clean_text(message.get("text") or message.get("answer_excerpt"), 400),
        _clean_text(message.get("feature") or message.get("intent"), 120),
    )


def merge_agent_context(
    agent_context: dict[str, Any] | None,
    *,
    username: str,
    max_messages: int = MAX_CONTEXT_MESSAGES,
) -> dict[str, Any]:
    ctx = deepcopy(agent_context) if isinstance(agent_context, dict) else {}
    provided = [m for m in (ctx.get("messages") or []) if isinstance(m, dict)]
    memory_messages = recent_context_messages(username, turns=4)
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for msg in memory_messages + provided:
        key = _message_key(msg)
        if key in seen:
            continue
        seen.add(key)
        merged.append(deepcopy(msg))
    limit = max(2, min(int(max_messages or MAX_CONTEXT_MESSAGES), 24))
    ctx["messages"] = merged[-limit:]
    ctx["server_memory"] = {
        "enabled": True,
        "message_count": len(memory_messages),
        "merged_message_count": len(ctx["messages"]),
        "source": "FLOW_DATA_ROOT/home_agent_memory/conversation.jsonl",
    }
    return ctx


def is_memory_recall_prompt(prompt: str) -> bool:
    text = str(prompt or "").strip()
    low = text.lower()
    if not text:
        return False
    has_anchor = any(term in low or term in text for term in (
        "아까",
        "이전",
        "직전",
        "방금",
        "전에",
        "previous",
        "last",
        "memory",
        "context",
    ))
    has_recall = any(term in low or term in text for term in (
        "뭐",
        "무엇",
        "질문",
        "답변",
        "기억",
        "물어",
        "asked",
        "answer",
        "remember",
    ))
    return has_anchor and has_recall


def _turns_from_context(agent_context: dict[str, Any] | None, *, limit: int = 4) -> list[dict[str, Any]]:
    messages = [m for m in ((agent_context or {}).get("messages") or []) if isinstance(m, dict)]
    turns: list[dict[str, Any]] = []
    current_prompt = ""
    for msg in messages:
        role = str(msg.get("role") or "")
        if role == "user":
            current_prompt = _clean_text(msg.get("prompt") or msg.get("text"), 1200)
            continue
        if role == "assistant":
            answer = _clean_text(msg.get("answer_excerpt") or msg.get("text") or msg.get("answer"), 1200)
            prompt = _clean_text(msg.get("prompt") or current_prompt, 1200)
            if prompt or answer:
                turns.append({
                    "memory_id": _clean_text(msg.get("memory_id"), 80),
                    "prompt": prompt,
                    "answer": answer,
                    "source": msg.get("source") or "context",
                })
    capped = max(0, min(int(limit or 0), 20))
    return turns[-capped:] if capped else []


def recall_answer(
    *,
    prompt: str,
    username: str,
    agent_context: dict[str, Any] | None = None,
    limit: int = 3,
) -> dict[str, Any]:
    turns = recent_turns(username, limit=limit)
    if not turns:
        turns = list(reversed(_turns_from_context(agent_context, limit=limit)))
    if not turns:
        answer = "현재 사용자 메모리에서 이전 질문과 답변을 찾지 못했습니다."
        return {
            "handled": True,
            "intent": "home_memory_recall",
            "action": "home_memory.recall",
            "feature": "home",
            "answer": answer,
            "blocked": True,
            "memory": {"turn_count": 0},
        }
    lines = ["최근 Home Agent 메모리에 남아있는 질문/답변입니다."]
    for idx, row in enumerate(turns, 1):
        lines.append(f"{idx}. 질문: {_clean_text(row.get('prompt'), 220)}")
        answer = _clean_text(row.get("answer"), 260)
        if answer:
            lines.append(f"   답변: {answer}")
    answer_text = "\n".join(lines).strip()
    return {
        "handled": True,
        "intent": "home_memory_recall",
        "action": "home_memory.recall",
        "feature": "home",
        "answer": answer_text,
        "memory": {
            "turn_count": len(turns),
            "source": "server_memory" if turns and turns[0].get("memory_id") else "context",
        },
    }
