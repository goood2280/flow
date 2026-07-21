"""core/wiki_event_hooks.py — Tracker / Meeting / Inform mutation 시점에 호출되는
KnowledgeEvent append helper.

각 라우터는 mutation 후 try/except 로 본 모듈의 함수를 호출하면 된다.
실패해도 본 흐름은 진행되도록 best-effort 로 동작 (예외를 절대 위로 던지지 않는다).
이벤트는 `wiki_draft_queue` (Phase 6) 가 grouping 의 근거로 사용한다.

기존 tracker._append_tracker_knowledge_events 와 meetings._append_meeting_knowledge_events
는 `knowledge_impact.append_candidates_from_text` 경로로 키워드 매칭 시에만 event 를
기록한다. 본 모듈은 키워드와 무관하게 **모든 mutation** 에 대해 generic event 를
한 줄씩 기록한다. 두 경로는 source_id prefix 로 구분되며 중복은 wiki_draft_queue 의
그룹화 시 정규화한다.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("flow.wiki_event_hooks")


def _safe_append(payload: dict[str, Any]) -> None:
    try:
        from app_v2.shared.contracts import KnowledgeEvent
        from core import knowledge_vault
        knowledge_vault.append_event(KnowledgeEvent(**payload))
    except Exception as exc:
        logger.info("wiki_event_hooks append failed: %s", exc)


def _truncate(text: str, limit: int = 400) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def emit_issue_comment_event(
    *,
    issue_id: str,
    title: str,
    text: str,
    actor: str,
    product: str = "",
    root_lot_id: str = "",
    wafer_id: str = "",
) -> None:
    """Tracker comment 가 추가될 때 1개 generic event 를 남긴다.

    Wiki draft queue 는 동일 `issue/<issue_id>` 대상으로 N개 이상 모이면 draft 카드를
    제안한다. 기존 keyword 기반 lot_anomaly/split_impact event 는 그대로 유지된다.
    """
    if not issue_id:
        return
    _safe_append({
        "event_type": "generic",
        "source_type": "issue",
        "source_id": f"{issue_id}:comment:{actor}",
        "title": _truncate(title or f"issue {issue_id}", 120),
        "summary": _truncate(text, 400),
        "actor": actor or "",
        "entity": {"product": product, "root_lot_id": root_lot_id, "wafer_id": wafer_id},
        "tags": ["tracker", "comment"],
        "payload": {"issue_id": issue_id},
        "wiki_targets": [f"issue/{issue_id}"],
    })


def emit_issue_status_event(
    *,
    issue_id: str,
    title: str,
    new_status: str,
    actor: str,
    product: str = "",
    root_lot_id: str = "",
    wafer_id: str = "",
) -> None:
    """Tracker 이슈 상태 변경 시 호출."""
    if not issue_id:
        return
    _safe_append({
        "event_type": "generic",
        "source_type": "issue",
        "source_id": f"{issue_id}:status:{new_status}",
        "title": _truncate(f"[{new_status}] {title or issue_id}", 120),
        "summary": f"status → {new_status}",
        "actor": actor or "",
        "entity": {"product": product, "root_lot_id": root_lot_id, "wafer_id": wafer_id},
        "tags": ["tracker", "status", new_status],
        "payload": {"issue_id": issue_id, "status": new_status},
        "wiki_targets": [f"issue/{issue_id}"],
    })


def emit_meeting_minutes_event(
    *,
    meeting_id: str,
    session_id: str,
    title: str,
    decisions: list[Any] | None,
    action_items: list[Any] | None,
    actor: str,
) -> None:
    """회의록 저장 시 호출. decisions/action_items 핵심 텍스트를 요약으로 남긴다."""
    if not meeting_id:
        return
    decision_texts = []
    for d in decisions or []:
        if isinstance(d, dict):
            t = d.get("text") or ""
        else:
            t = str(d)
        if t:
            decision_texts.append(_truncate(t, 80))
    action_texts = []
    for a in action_items or []:
        if isinstance(a, dict):
            t = a.get("text") or ""
        else:
            t = str(a)
        if t:
            action_texts.append(_truncate(t, 80))
    summary_lines = []
    if decision_texts:
        summary_lines.append("결정: " + " · ".join(decision_texts[:5]))
    if action_texts:
        summary_lines.append("액션: " + " · ".join(action_texts[:5]))
    _safe_append({
        "event_type": "generic",
        "source_type": "meeting",
        "source_id": f"{meeting_id}:{session_id}:minutes",
        "title": _truncate(title or meeting_id, 120),
        "summary": _truncate("\n".join(summary_lines) or "회의록 저장", 600),
        "actor": actor or "",
        "tags": ["meeting", "minutes"],
        "payload": {"meeting_id": meeting_id, "session_id": session_id,
                    "decision_count": len(decision_texts), "action_count": len(action_texts)},
        "wiki_targets": [f"meeting/{meeting_id}"],
    })


def emit_inform_event(
    *,
    inform_id: str,
    parent_id: str | None,
    title: str,
    text: str,
    module: str,
    actor: str,
    product: str = "",
    root_lot_id: str = "",
    wafer_id: str = "",
    status: str = "",
) -> None:
    """Inform 생성/상태변경 시 호출."""
    if not inform_id:
        return
    target = f"inform/{parent_id or inform_id}"
    suffix = f"status:{status}" if status else "create"
    _safe_append({
        "event_type": "generic",
        "source_type": "manual",  # inform 은 contracts 에 없으므로 manual 로 분류.
        "source_id": f"{inform_id}:{suffix}",
        "title": _truncate(title or f"inform {inform_id}", 120),
        "summary": _truncate(text, 400),
        "actor": actor or "",
        "entity": {"product": product, "root_lot_id": root_lot_id, "wafer_id": wafer_id},
        "tags": ["inform"] + ([module] if module else []) + ([status] if status else []),
        "payload": {"inform_id": inform_id, "parent_id": parent_id, "module": module, "status": status},
        "wiki_targets": [target],
    })
