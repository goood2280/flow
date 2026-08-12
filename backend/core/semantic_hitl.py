"""Human-in-the-loop semantic resolutions for Flow-i.

Confirmed manufacturing aliases are shared across users as append-only audit
data. Conflicting mappings are resolved by distinct-user votes; a tie is
treated as ambiguous and sent back to HITL instead of being guessed.
"""
from __future__ import annotations

import base64
import json
import re
import unicodedata
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.paths import PATHS
from core.utils import jsonl_append


CHOICE_MARKER = "__FLOWI_SEMANTIC_CHOICE__:"
_MAX_RECORDS_READ = 5000


def resolution_file() -> Path:
    return PATHS.data_root / "semantic" / "hitl_resolutions.jsonl"


def normalize_term(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    return re.sub(r"[\s_\-./:]+", "", text)


def _clean(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _safe_evidence(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    allowed = {"recent_days", "row_count", "candidate_score", "files_scanned"}
    return {
        str(key)[:80]: item
        for key, item in value.items()
        if key in allowed and isinstance(item, (str, int, float, bool, type(None)))
    }


def record_resolution(
    *,
    username: str,
    term: str,
    source_type: str,
    item_id: str,
    product: str = "",
    step_id: str = "",
    original_prompt: str = "",
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist one user-confirmed semantic mapping and return the safe record."""
    record = {
        "version": 2,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "username": _clean(username, 120) or "flowi",
        "term": _clean(term, 240),
        "normalized_term": normalize_term(term),
        "source_type": _clean(source_type, 24).upper(),
        "product": _clean(product, 120).upper(),
        "item_id": _clean(item_id, 240),
        "step_id": _clean(step_id, 160),
        "original_prompt": _clean(original_prompt, 2000),
        "evidence": _safe_evidence(evidence),
        "scope": "shared",
        "status": "confirmed",
    }
    if not record["normalized_term"] or not record["item_id"]:
        raise ValueError("term and item_id are required")
    if record["source_type"] not in {"INLINE", "ET", "VM", "EDS", "FAB"}:
        raise ValueError("unsupported source_type")
    jsonl_append(resolution_file(), record, add_timestamp=False, max_lines=100_000)
    return record


def _records(statuses: set[str] | None = None) -> list[dict[str, Any]]:
    path = resolution_file()
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as handle:
            lines = list(deque(handle, maxlen=_MAX_RECORDS_READ))
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict) and row.get("status") in (statuses or {"confirmed"}):
            out.append(row)
    return out


def record_rejection(*, username: str, term: str, source_type: str, item_id: str, product: str = "") -> dict[str, Any]:
    """Suspend an auto-applied mapping for this user until it is confirmed again."""
    record = {
        "version": 2, "created_at": datetime.now(timezone.utc).isoformat(),
        "username": _clean(username, 120) or "flowi", "term": _clean(term, 240),
        "normalized_term": normalize_term(term), "source_type": _clean(source_type, 24).upper(),
        "product": _clean(product, 120).upper(), "item_id": _clean(item_id, 240),
        "step_id": "", "scope": "user", "status": "rejected",
    }
    if not record["normalized_term"] or not record["item_id"]:
        raise ValueError("term and item_id are required")
    jsonl_append(resolution_file(), record, add_timestamp=False, max_lines=100_000)
    return record


def find_resolution(
    prompt_or_term: str,
    *,
    username: str,
    source_type: str = "",
    product: str = "",
    step_id: str = "",
) -> dict[str, Any] | None:
    """Return a shared consensus mapping, or ``None`` when mappings conflict."""
    normalized_input = normalize_term(prompt_or_term)
    if not normalized_input:
        return None
    wanted_user = _clean(username, 120).casefold() or "flowi"
    wanted_source = _clean(source_type, 24).upper()
    wanted_product = _clean(product, 120).upper()
    wanted_step = _clean(step_id, 160).upper()
    matches: list[dict[str, Any]] = []
    all_rows = _records({"confirmed", "rejected"})
    rejection_at = ""
    for row in reversed(all_rows):
        if row.get("status") != "rejected":
            continue
        if _clean(row.get("username"), 120).casefold() != wanted_user:
            continue
        if wanted_source and _clean(row.get("source_type"), 24).upper() != wanted_source:
            continue
        row_product = _clean(row.get("product"), 120).upper()
        if wanted_product and row_product and row_product != wanted_product:
            continue
        term = normalize_term(row.get("normalized_term") or row.get("term"))
        if len(term) >= 2 and (term == normalized_input or term in normalized_input):
            rejection_at = _clean(row.get("created_at"), 80)
            break
    for row in reversed(all_rows):
        if row.get("status") != "confirmed":
            continue
        if rejection_at and _clean(row.get("created_at"), 80) <= rejection_at:
            continue
        scope = _clean(row.get("scope"), 24).casefold() or "user"
        row_user = _clean(row.get("username"), 120).casefold()
        # Version-1 records remain private; version-2 shared records are visible
        # to every user.
        if scope != "shared" and row_user != wanted_user:
            continue
        if wanted_source and _clean(row.get("source_type"), 24).upper() != wanted_source:
            continue
        row_product = _clean(row.get("product"), 120).upper()
        if wanted_product and row_product and row_product != wanted_product:
            continue
        row_step = _clean(row.get("step_id"), 160).upper()
        if wanted_step and row_step and row_step != wanted_step:
            continue
        term = normalize_term(row.get("normalized_term") or row.get("term"))
        if len(term) >= 2 and (term == normalized_input or term in normalized_input):
            matches.append(row)
    if not matches:
        return None
    # Prefer the most specific alias before voting. This prevents a short alias
    # from shadowing a more precise plant term contained in the same prompt.
    specificity = max(len(normalize_term(row.get("term"))) for row in matches)
    matches = [row for row in matches if len(normalize_term(row.get("term"))) == specificity]

    # A user's newest selection is one vote. Repeated clicks by the same person
    # do not outweigh another engineer's confirmation.
    latest_by_voter: dict[str, dict[str, Any]] = {}
    for row in matches:  # already newest-first
        voter = _clean(row.get("username"), 120).casefold() or "flowi"
        latest_by_voter.setdefault(voter, row)
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in latest_by_voter.values():
        key = (
            _clean(row.get("product"), 120).upper(),
            _clean(row.get("step_id"), 160).upper(),
            _clean(row.get("item_id"), 240).upper(),
        )
        groups.setdefault(key, []).append(row)
    ranked = sorted(groups.values(), key=lambda rows: len(rows), reverse=True)
    if len(ranked) > 1 and len(ranked[0]) == len(ranked[1]):
        return None
    chosen = dict(ranked[0][0])
    chosen["scope"] = "shared" if _clean(chosen.get("scope"), 24).casefold() == "shared" else "user"
    chosen["shared_votes"] = len(ranked[0])
    chosen["shared_conflict_count"] = max(0, len(ranked) - 1)
    chosen_key = (
        _clean(chosen.get("product"), 120).upper(),
        _clean(chosen.get("step_id"), 160).upper(),
        _clean(chosen.get("item_id"), 240).upper(),
    )
    chosen["confirmation_count"] = sum(
        1 for row in matches
        if (
            _clean(row.get("product"), 120).upper(),
            _clean(row.get("step_id"), 160).upper(),
            _clean(row.get("item_id"), 240).upper(),
        ) == chosen_key
    )
    return chosen


def encode_choice(payload: dict[str, Any]) -> str:
    safe = {
        "term": _clean(payload.get("term"), 240),
        "source_type": _clean(payload.get("source_type"), 24).upper(),
        "product": _clean(payload.get("product"), 120),
        "item_id": _clean(payload.get("item_id"), 240),
        "step_id": _clean(payload.get("step_id"), 160),
        "original_prompt": _clean(payload.get("original_prompt"), 2000),
        "evidence": _safe_evidence(payload.get("evidence")),
    }
    raw = json.dumps(safe, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return CHOICE_MARKER + base64.urlsafe_b64encode(raw).decode("ascii")


def decode_choice(prompt: str) -> dict[str, Any] | None:
    text = str(prompt or "").strip()
    if not text.startswith(CHOICE_MARKER):
        return None
    token = text[len(CHOICE_MARKER):].strip().split()[0]
    if not token or len(token) > 12_000:
        return None
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
        value = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    if not isinstance(value, dict) or not value.get("term") or not value.get("item_id"):
        return None
    return value


def consume_choice(prompt: str, *, username: str) -> dict[str, Any] | None:
    payload = decode_choice(prompt)
    if payload is None:
        return None
    try:
        learned = record_resolution(username=username, **payload)
    except (TypeError, ValueError):
        return None
    return {
        "learned": learned,
        "original_prompt": _clean(payload.get("original_prompt"), 2000),
    }
