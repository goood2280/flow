"""Best-effort glue between feature routers and the semantic learning queue.

Each router calls `submit_meeting` / `submit_inform` / `submit_tracker_comment`
at the end of its save handler. Failures are swallowed — the host save must
not fail because the learning pipeline had trouble.

Approval is wired in `routers/agent.py` so the admin queue endpoint converts
an approved proposal into an `upsert_alias_group` on the runtime lexicon.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List

logger = logging.getLogger("flow.semantic_learning.hooks")


def _safe_alias_groups() -> Dict[str, List[str]]:
    try:
        from app_v2.modules.agent_runtime.semantic import _ALIAS_GROUPS  # type: ignore
        from app_v2.modules.semantic_lexicon import effective_alias_groups
        return effective_alias_groups(dict(_ALIAS_GROUPS))
    except Exception as exc:
        logger.debug("alias_groups load failed: %s", exc)
        return {}


def _safe_column_catalog_keys() -> set[str]:
    try:
        from app_v2.modules.agent_runtime.semantic import _catalog_rows  # type: ignore
        return {str(row.get("column") or "") for row in _catalog_rows() if row.get("column")}
    except Exception as exc:
        logger.debug("column catalog load failed: %s", exc)
        return set()


def _enqueue_terms(terms: Iterable[str], *, origin_kind: str, origin_ref: str) -> List[Dict[str, Any]]:
    if not terms:
        return []
    try:
        from app_v2.modules.semantic_learning import classify_proposal, enqueue_proposal
    except Exception as exc:
        logger.debug("semantic_learning import failed: %s", exc)
        return []
    alias_groups = _safe_alias_groups()
    column_keys = _safe_column_catalog_keys()
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for raw in terms:
        term = str(raw or "").strip()
        if not term or term in seen:
            continue
        seen.add(term)
        try:
            classified = classify_proposal(term, alias_groups=alias_groups, column_catalog_keys=column_keys)
        except Exception as exc:
            logger.debug("classify_proposal failed for %r: %s", term, exc)
            continue
        if classified.get("category") == "reject":
            continue
        try:
            row = enqueue_proposal({
                **classified,
                "origin": {"kind": origin_kind, "ref": origin_ref},
            })
            out.append(row)
        except Exception as exc:
            logger.debug("enqueue_proposal failed for %r: %s", term, exc)
            continue
    return out


def submit_meeting(meeting_row: Dict[str, Any]) -> int:
    """Extract → classify → enqueue terms from a meeting save. Returns count enqueued."""
    if not isinstance(meeting_row, dict):
        return 0
    try:
        from app_v2.modules.semantic_learning import extract_from_meeting
        terms = extract_from_meeting(meeting_row)
    except Exception as exc:
        logger.debug("extract_from_meeting failed: %s", exc)
        return 0
    ref = str(meeting_row.get("id") or meeting_row.get("meeting_id") or meeting_row.get("title") or "")
    enqueued = _enqueue_terms(terms, origin_kind="meeting", origin_ref=ref)
    return len(enqueued)


def submit_inform(inform_row: Dict[str, Any]) -> int:
    if not isinstance(inform_row, dict):
        return 0
    try:
        from app_v2.modules.semantic_learning import extract_from_inform
        terms = extract_from_inform(inform_row)
    except Exception as exc:
        logger.debug("extract_from_inform failed: %s", exc)
        return 0
    ref = str(inform_row.get("id") or inform_row.get("inform_id") or "")
    enqueued = _enqueue_terms(terms, origin_kind="inform", origin_ref=ref)
    return len(enqueued)


def submit_tracker_comment(comment_row: Dict[str, Any]) -> int:
    if not isinstance(comment_row, dict):
        return 0
    try:
        from app_v2.modules.semantic_learning import extract_from_tracker_comment
        terms = extract_from_tracker_comment(comment_row)
    except Exception as exc:
        logger.debug("extract_from_tracker_comment failed: %s", exc)
        return 0
    ref = str(comment_row.get("id") or comment_row.get("issue_id") or "")
    enqueued = _enqueue_terms(terms, origin_kind="tracker_comment", origin_ref=ref)
    return len(enqueued)


def submit_activity_log_batch(jsonl_path: Path, *, since_iso: str | None = None) -> int:
    """Daily batch — scan flowi_activity.jsonl for low-coverage tokens."""
    try:
        from app_v2.modules.semantic_learning import extract_from_activity_log
        terms = extract_from_activity_log(Path(jsonl_path), since_iso=since_iso)
    except Exception as exc:
        logger.debug("extract_from_activity_log failed: %s", exc)
        return 0
    enqueued = _enqueue_terms(terms, origin_kind="activity_log", origin_ref=str(jsonl_path))
    return len(enqueued)
