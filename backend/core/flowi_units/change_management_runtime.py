"""Agent-visible Change Management Flow-i runtime graph."""
from __future__ import annotations

import datetime as _dt
import re
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request

from core import agent_feedback_penalties
from core.auth import current_user
from core.paths import PATHS
from core.utils import jsonl_append, jsonl_read, jsonl_trim


UNIT_AI_KEY = "change_management"

GRAPH_NODES: tuple[dict[str, str], ...] = (
    {"id": "context_scope", "label": "Visible scope", "phase": "context"},
    {"id": "meeting_reference", "label": "회의 참조 해석", "phase": "semantic"},
    {"id": "clarification_gate", "label": "Clarification gate", "phase": "hitl"},
    {"id": "evidence_pack", "label": "회의/변경점 근거 수집", "phase": "read"},
    {"id": "answer_compose", "label": "Plain text 답변", "phase": "render"},
)

GRAPH_EDGES: tuple[dict[str, str], ...] = (
    {"source": "context_scope", "target": "meeting_reference"},
    {"source": "meeting_reference", "target": "clarification_gate"},
    {"source": "clarification_gate", "target": "evidence_pack"},
    {"source": "evidence_pack", "target": "answer_compose"},
)

ANSWER_SYSTEM_PROMPT = (
    "당신은 Flow 변경점 관리 질의 도우미다. 제공된 visible 회의/캘린더 데이터 안에서만 답한다. "
    "근거가 없거나 대상 회의가 애매하면 없거나 애매하다고 말한다. "
    "마크다운 강조, 별표 굵게, heading 기호 없이 간결한 plain text로 답한다."
)

STATE_DESIGN: dict[str, dict[str, Any]] = {
    "run_id": {
        "description": "Runtime execution id for this Agent unit run.",
        "producer": "runtime",
        "public": True,
    },
    "request": {
        "description": "Sanitized user prompt and optional explicit meeting/session selector.",
        "producer": "runtime",
        "public": True,
    },
    "context_scope": {
        "description": "Visible meeting and change-management event counts after existing permission filters.",
        "producer": "context_scope",
        "public": True,
    },
    "meeting_reference": {
        "description": "Resolved meeting focus or clarification candidates when the prompt is ambiguous.",
        "producer": "meeting_reference",
        "public": True,
    },
    "clarification_gate": {
        "description": "Human-facing gate that blocks evidence collection when the meeting reference is ambiguous.",
        "producer": "clarification_gate",
        "public": True,
    },
    "evidence": {
        "description": "Compact summaries of agendas, minutes, decisions, action items, and calendar events.",
        "producer": "evidence_pack",
        "public": True,
    },
    "answer_pack": {
        "description": "Plain text answer, LLM usage metadata, sources, and warnings.",
        "producer": "answer_compose",
        "public": True,
    },
    "trace": {
        "description": "Append-only public node trace rows for Agent UI inspection.",
        "producer": "runtime",
        "public": True,
    },
}

NODE_METADATA: dict[str, dict[str, Any]] = {
    "context_scope": {
        "persona": "Loads only meetings and calendar events visible to the current user.",
        "prompt": {"system": "", "mode": "deterministic"},
        "reads": ["request.user", "meetings visibility", "calendar visibility"],
        "writes": ["context_scope"],
        "shared_state": ["visible_meetings", "visible_calendar_events"],
        "answer_attach_rule": "Expose counts and candidate labels only; do not bypass meeting/calendar visibility.",
    },
    "meeting_reference": {
        "persona": "Interprets which meeting the prompt refers to and asks for clarification when ambiguous.",
        "prompt": {"system": "", "mode": "deterministic"},
        "reads": ["request.prompt", "request.meeting_id", "visible_meetings"],
        "writes": ["meeting_reference"],
        "shared_state": ["focus_meeting_id", "needs_clarification", "candidates"],
        "answer_attach_rule": "Attach selected meeting metadata or clarification candidates; do not guess one of several matches.",
    },
    "clarification_gate": {
        "persona": "Stops the runtime before evidence collection when the user needs to choose a meeting candidate.",
        "prompt": {"system": "", "mode": "deterministic_hitl"},
        "reads": ["meeting_reference.needs_clarification", "meeting_reference.candidates"],
        "writes": ["clarification_gate"],
        "shared_state": ["clarification_gate.action_required", "clarification_gate.candidates"],
        "answer_attach_rule": "Attach visible candidates and action_required status only; evidence_pack stays skipped until clarified.",
    },
    "evidence_pack": {
        "persona": "Builds compact read-only evidence from agendas, minutes, decisions, action items, and calendar events.",
        "prompt": {"system": "", "mode": "deterministic"},
        "reads": ["visible_meetings", "visible_calendar_events", "meeting_reference"],
        "writes": ["evidence"],
        "shared_state": ["meeting summaries", "calendar event summaries", "sources"],
        "answer_attach_rule": "Attach compact summaries and source counts only; never write meeting or calendar records.",
    },
    "answer_compose": {
        "persona": "Composes a concise plain-text answer grounded only in the evidence pack.",
        "prompt": {"system": ANSWER_SYSTEM_PROMPT, "mode": "llm_with_deterministic_fallback"},
        "reads": ["request.prompt", "evidence"],
        "writes": ["answer_pack"],
        "shared_state": ["answer", "llm", "warnings"],
        "answer_attach_rule": "Strip markdown decoration and state missing/ambiguous evidence plainly.",
    },
}


def change_management_graph(statuses: dict[str, str] | None = None) -> dict[str, Any]:
    statuses = statuses or {}
    return {
        "layout": {"rankdir": "LR"},
        "nodes": [
            {
                **node,
                **deepcopy(NODE_METADATA.get(node["id"], {})),
                "state_io": {
                    "reads": list(NODE_METADATA.get(node["id"], {}).get("reads") or []),
                    "writes": list(NODE_METADATA.get(node["id"], {}).get("writes") or []),
                },
                "status": statuses.get(node["id"], "pending"),
            }
            for node in GRAPH_NODES
        ],
        "edges": [dict(edge) for edge in GRAPH_EDGES],
        "state_design": deepcopy(STATE_DESIGN),
    }


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _history_path() -> Path:
    return PATHS.data_root / "agent_unit_ai_sessions" / UNIT_AI_KEY / "history.jsonl"


def _clean_text(value: Any, limit: int = 5000) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return text[: max(1, limit)].strip()


def _string_list(value: Any, limit: int = 20) -> list[str]:
    if value is None:
        return []
    raw = value if isinstance(value, (list, tuple, set)) else [value]
    out: list[str] = []
    for item in raw:
        text = _clean_text(item, 120)
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _plain_answer_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    text = re.sub(r"(?m)^\s*[-*]\s+", "  ", text)
    text = re.sub(r"(?m)^\s*[-*]{3,}\s*$", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _trace_row(
    node_id: str,
    status: str,
    output: Any,
    warnings: list[str],
    started: float,
    input_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    label = next((node["label"] for node in GRAPH_NODES if node["id"] == node_id), node_id)
    return {
        "node_id": node_id,
        "label": label,
        "status": status,
        "input_summary": input_summary or {},
        "output": output,
        "warnings": list(warnings or []),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }


def _status_from_warnings(warnings: list[str], default: str = "success") -> str:
    return "warning" if warnings else default


def _meeting_title(meeting: dict[str, Any]) -> str:
    return _clean_text(meeting.get("title") or meeting.get("id"), 160)


def _candidate_rows(meetings: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    rows = []
    for meeting in meetings[:limit]:
        sessions = meeting.get("sessions") if isinstance(meeting.get("sessions"), list) else []
        rows.append({
            "meeting_id": meeting.get("id") or "",
            "title": _meeting_title(meeting),
            "sessions": len(sessions),
            "status": meeting.get("status") or "",
        })
    return rows


def _clarification_answer(message: str, candidates: list[dict[str, Any]]) -> str:
    lines = [
        _clean_text(message, 300) or "질문이 가리키는 회의가 애매합니다.",
        "회의관리 또는 변경점 관리에서 회의명을 확인한 뒤 다시 질문해 주세요.",
    ]
    if candidates:
        lines.extend(["", "후보"])
        for row in candidates[:8]:
            title = row.get("title") or row.get("meeting_title") or row.get("meeting_id") or "-"
            meeting_id = row.get("meeting_id") or row.get("id") or ""
            label = f"{title}{(' / ' + meeting_id) if meeting_id else ''}"
            lines.append(f"  {label}")
    return "\n".join(lines).strip()


def _load_visible_context(request: Request) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    from routers import meetings

    me = current_user(request)
    username = str(me.get("username") or "")
    role = str(me.get("role") or "user")
    my_gids = meetings._my_meeting_group_ids(username, role)
    visible_meetings = [
        m for m in meetings._load()
        if isinstance(m, dict) and meetings._meeting_visible(m, username, role, my_gids)
    ]
    visible_ids = {str(m.get("id") or "") for m in visible_meetings if m.get("id")}
    visible_events = meetings._calendar_events_for_meeting_ask(username, role, visible_ids)
    return me, visible_meetings, visible_events


def _resolve_reference(
    prompt: str,
    visible_meetings: list[dict[str, Any]],
    payload: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str]]:
    from routers import meetings

    warnings: list[str] = []
    meeting_id = _clean_text(payload.get("meeting_id"), 120)
    if meeting_id:
        meeting = next((m for m in visible_meetings if str(m.get("id") or "") == meeting_id), None)
        if not meeting:
            return None, {
                "reason": "meeting_not_found",
                "message": "요청한 meeting_id를 볼 수 없거나 찾지 못했습니다.",
                "candidates": _candidate_rows(visible_meetings),
            }, warnings
        return meeting, None, warnings

    focus_meeting, clarification = meetings._ask_resolve_meeting_reference(prompt, visible_meetings)
    if clarification:
        return None, clarification, warnings
    if not focus_meeting and len(visible_meetings) > 1:
        warnings.append("특정 회의명이 없어 현재 볼 수 있는 회의/변경점 전체를 범위로 사용했습니다.")
    return focus_meeting, None, warnings


def _build_evidence(
    prompt: str,
    visible_meetings: list[dict[str, Any]],
    visible_events: list[dict[str, Any]],
    focus_meeting: dict[str, Any] | None,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    from routers import meetings

    warnings: list[str] = []
    selected_meetings = [focus_meeting] if focus_meeting else visible_meetings
    focus_meeting_id = str((focus_meeting or {}).get("id") or "")
    session_id = _clean_text(payload.get("session_id"), 120)
    if session_id and focus_meeting:
        filtered: list[dict[str, Any]] = []
        for meeting in selected_meetings:
            sessions = [
                session for session in (meeting.get("sessions") or [])
                if isinstance(session, dict) and str(session.get("id") or "") == session_id
            ]
            if sessions:
                next_meeting = deepcopy(meeting)
                next_meeting["sessions"] = sessions
                filtered.append(next_meeting)
        if filtered:
            selected_meetings = filtered
        else:
            warnings.append("요청한 session_id와 일치하는 회의 차수를 찾지 못해 회의 전체를 사용했습니다.")

    calendar_events = list(visible_events)
    if focus_meeting_id:
        calendar_events = meetings._filter_calendar_events_for_focus(
            calendar_events,
            focus_meeting_id,
            include_manual=meetings._ask_question_mentions_calendar(prompt),
        )
    summary = meetings._build_workspace_ask_summary(
        selected_meetings,
        calendar_events,
        focus_meeting_id=focus_meeting_id,
    )
    sources = meetings._meeting_ask_session_sources(summary, include_meeting=True)
    return {
        "summary": summary,
        "sources": sources,
        "meeting_count": len(summary.get("meetings") or []),
        "calendar_event_count": len(summary.get("calendar_events") or []),
        "focus_meeting_id": focus_meeting_id,
        "session_id": session_id,
    }, warnings


def _compose_answer(prompt: str, evidence: dict[str, Any]) -> tuple[str, dict[str, Any], list[str]]:
    from routers import meetings

    warnings: list[str] = []
    summary = evidence.get("summary") if isinstance(evidence.get("summary"), dict) else {}
    answer, llm_info = meetings._meeting_ask_llm_answer(prompt, summary)
    answer = _plain_answer_text(answer)
    if not answer:
        answer = (
            "저장된 회의/변경점 근거로 답변을 만들 수 없습니다.\n"
            "회의관리의 해당 회의 상세와 변경점 관리 캘린더 이벤트를 확인해 주세요."
        )
        warnings.append("empty_answer")
    if llm_info.get("error"):
        warnings.append(str(llm_info.get("error")))
    return answer, llm_info, warnings


def _history_entry(
    *,
    run_id: str,
    username: str,
    prompt: str,
    status: str,
    answer: str,
    needs_clarification: bool,
    meeting_reference: dict[str, Any],
    clarification_gate: dict[str, Any],
    evidence: dict[str, Any],
    llm: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    summary = evidence.get("summary") if isinstance(evidence.get("summary"), dict) else {}
    return {
        "history_id": run_id,
        "run_id": run_id,
        "timestamp": _now_iso(),
        "username": _clean_text(username, 80),
        "prompt": prompt,
        "natural_language": prompt,
        "status": status,
        "answer": answer,
        "needs_clarification": needs_clarification,
        "meeting_reference": deepcopy(meeting_reference or {}),
        "clarification_gate": deepcopy(clarification_gate or {}),
        "meeting": deepcopy((summary.get("meetings") or [{}])[0].get("meeting") if meeting_reference.get("focus_meeting_id") else {}),
        "meetings": [deepcopy(m.get("meeting") or {}) for m in (summary.get("meetings") or [])],
        "sources": deepcopy(evidence.get("sources") or []),
        "calendar_events": deepcopy(summary.get("calendar_events") or []),
        "llm": deepcopy(llm or {}),
        "warnings": list(warnings or []),
    }


def _append_history(row: dict[str, Any]) -> None:
    path = _history_path()
    jsonl_append(path, row, add_timestamp=False)
    jsonl_trim(path, 500)


def run_change_management_runtime(
    payload: dict[str, Any],
    *,
    username: str = "",
    request: Request | None = None,
    agent_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    del agent_context
    if request is None:
        raise HTTPException(status_code=400, detail="request is required for Change Management Flow-i")
    body = deepcopy(payload or {})
    prompt = _clean_text(body.get("prompt") or body.get("natural_language"), 5000)
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")
    run_id = "agent_change_" + uuid.uuid4().hex[:12]
    trace: list[dict[str, Any]] = []
    warnings: list[str] = []
    request_payload = {
        "prompt": prompt,
        "meeting_id": _clean_text(body.get("meeting_id"), 120),
        "session_id": _clean_text(body.get("session_id"), 120),
    }

    started = time.perf_counter()
    me, visible_meetings, visible_events = _load_visible_context(request)
    actor = username or str(me.get("username") or "")
    context_scope = {
        "username": actor,
        "role": str(me.get("role") or "user"),
        "visible_meeting_count": len(visible_meetings),
        "visible_calendar_event_count": len(visible_events),
        "meeting_candidates": _candidate_rows(visible_meetings),
    }
    trace.append(_trace_row(
        "context_scope",
        "success",
        context_scope,
        [],
        started,
        {"prompt_chars": len(prompt)},
    ))

    started = time.perf_counter()
    focus_meeting, clarification, ref_warnings = _resolve_reference(prompt, visible_meetings, body)
    warnings.extend(ref_warnings)
    meeting_reference = {
        "focus_meeting_id": str((focus_meeting or {}).get("id") or ""),
        "focus_meeting_title": _meeting_title(focus_meeting or {}),
        "needs_clarification": bool(clarification),
        "reason": str((clarification or {}).get("reason") or ""),
        "message": str((clarification or {}).get("message") or ""),
        "candidates": deepcopy((clarification or {}).get("candidates") or []),
    }
    trace.append(_trace_row(
        "meeting_reference",
        "warning" if clarification or ref_warnings else "success",
        meeting_reference,
        ref_warnings,
        started,
        {"meeting_id": request_payload["meeting_id"]},
    ))

    started = time.perf_counter()
    clarification_gate = {
        "action_required": bool(clarification),
        "needs_clarification": bool(clarification),
        "reason": meeting_reference.get("reason") or "",
        "message": meeting_reference.get("message") or "",
        "candidates": deepcopy(meeting_reference.get("candidates") or []),
        "focus_meeting_id": meeting_reference.get("focus_meeting_id") or "",
    }
    gate_warnings = ["회의 참조가 애매해 후보 선택이 필요합니다."] if clarification else []
    trace.append(_trace_row(
        "clarification_gate",
        "action_required" if clarification else "success",
        clarification_gate,
        gate_warnings,
        started,
        {"needs_clarification": bool(clarification), "candidates": len(clarification_gate["candidates"])},
    ))

    evidence: dict[str, Any] = {"summary": {}, "sources": []}
    llm_info: dict[str, Any] = {"available": False, "used": False}
    answer = ""
    needs_clarification = bool(clarification)
    status = "success"

    if needs_clarification:
        status = "needs_clarification"
        candidates = meeting_reference.get("candidates") if isinstance(meeting_reference.get("candidates"), list) else []
        answer = _clarification_answer(meeting_reference.get("message") or "", candidates)
        started = time.perf_counter()
        trace.append(_trace_row(
            "evidence_pack",
            "skipped",
            {"reason": "needs_clarification"},
            [],
            started,
            {"focus_meeting_id": ""},
        ))
        started = time.perf_counter()
        trace.append(_trace_row(
            "answer_compose",
            "warning",
            {"answer": answer, "llm": llm_info, "needs_clarification": True},
            ["회의 참조가 애매해 근거 수집을 보류했습니다."],
            started,
            {"needs_clarification": True},
        ))
        warnings.append("회의 참조가 애매합니다.")
    else:
        started = time.perf_counter()
        evidence, evidence_warnings = _build_evidence(prompt, visible_meetings, visible_events, focus_meeting, body)
        warnings.extend(evidence_warnings)
        evidence_output = {
            "meeting_count": evidence.get("meeting_count") or 0,
            "calendar_event_count": evidence.get("calendar_event_count") or 0,
            "focus_meeting_id": evidence.get("focus_meeting_id") or "",
            "session_id": evidence.get("session_id") or "",
            "sources": evidence.get("sources") or [],
        }
        trace.append(_trace_row(
            "evidence_pack",
            _status_from_warnings(evidence_warnings),
            evidence_output,
            evidence_warnings,
            started,
            {"focus_meeting_id": evidence.get("focus_meeting_id") or ""},
        ))

        started = time.perf_counter()
        answer, llm_info, answer_warnings = _compose_answer(prompt, evidence)
        warnings.extend(answer_warnings)
        trace.append(_trace_row(
            "answer_compose",
            _status_from_warnings(answer_warnings),
            {
                "answer": answer,
                "llm": llm_info,
                "sources": evidence.get("sources") or [],
                "calendar_event_count": evidence.get("calendar_event_count") or 0,
            },
            answer_warnings,
            started,
            {"llm_available": bool(llm_info.get("available"))},
        ))

    statuses = {str(row.get("node_id")): str(row.get("status") or "pending") for row in trace}
    graph = change_management_graph(statuses)
    history = _history_entry(
        run_id=run_id,
        username=actor,
        prompt=prompt,
        status=status,
        answer=answer,
        needs_clarification=needs_clarification,
        meeting_reference=meeting_reference,
        clarification_gate=clarification_gate,
        evidence=evidence,
        llm=llm_info,
        warnings=warnings,
    )
    try:
        _append_history(history)
    except Exception:
        pass
    summary = evidence.get("summary") if isinstance(evidence.get("summary"), dict) else {}
    result = {
        "ok": True,
        "unit_ai": UNIT_AI_KEY,
        "run_id": run_id,
        "status": status,
        "answer": answer,
        "needs_clarification": needs_clarification,
        "context_scope": context_scope,
        "meeting_reference": meeting_reference,
        "clarification_gate": clarification_gate,
        "evidence": {
            "meeting_count": evidence.get("meeting_count") or 0,
            "calendar_event_count": evidence.get("calendar_event_count") or 0,
            "focus_meeting_id": evidence.get("focus_meeting_id") or "",
            "session_id": evidence.get("session_id") or "",
            "sources": evidence.get("sources") or [],
        },
        "answer_pack": {
            "answer": answer,
            "llm": llm_info,
            "warnings": warnings,
            "needs_clarification": needs_clarification,
        },
        "meeting": (summary.get("meetings") or [{}])[0].get("meeting") if meeting_reference.get("focus_meeting_id") else {},
        "meetings": [m.get("meeting") or {} for m in (summary.get("meetings") or [])],
        "calendar_events": summary.get("calendar_events") or [],
        "sources": evidence.get("sources") or [],
        "llm": llm_info,
        "graph": graph,
        "trace": trace,
        "warnings": warnings,
    }
    return agent_feedback_penalties.annotate_result(UNIT_AI_KEY, result)


def list_change_management_history(limit: int = 50, *, username: str = "") -> list[dict[str, Any]]:
    def _visible(row: dict[str, Any]) -> bool:
        return not username or not row.get("username") or row.get("username") == username

    rows = jsonl_read(
        _history_path(),
        limit=max(1, min(int(limit or 50), 500)),
        filter_fn=_visible,
    )
    rows.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
    return rows[: max(1, min(int(limit or 50), 200))]
