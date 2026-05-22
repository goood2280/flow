"""Persistent admin queue for semantic learning proposals.

Storage: `PATHS.data_root / "semantic" / "proposals" / <id>.json` (one JSON
per proposal). Append-style — proposals are immutable once decided, the
status field tracks the lifecycle.
"""
from __future__ import annotations

import datetime as _dt
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List

from core.paths import PATHS
from core.utils import load_json, save_json

logger = logging.getLogger("flow.semantic_learning")

INBOX_DIR = PATHS.data_root / "semantic" / "proposals"

_VALID_STATUS = {"pending", "approved", "rejected"}
_VALID_CATEGORIES = {"mapping", "new_canonical", "conflict", "reject"}


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _short_id() -> str:
    return uuid.uuid4().hex[:12]


def _ensure_dir() -> None:
    INBOX_DIR.mkdir(parents=True, exist_ok=True)


def _path_for(proposal_id: str) -> Path:
    safe = "".join(c for c in str(proposal_id or "") if c.isalnum() or c in {"-", "_"})
    return INBOX_DIR / f"{safe[:60] or 'proposal'}.json"


def _read(path: Path) -> Dict[str, Any] | None:
    row = load_json(path, None)
    return row if isinstance(row, dict) else None


def _origin_ref(origin: Any) -> str:
    if isinstance(origin, dict):
        return str(origin.get("ref") or "")
    return ""


def _find_pending_duplicate(term_norm: str, origin_ref: str) -> Dict[str, Any] | None:
    if not INBOX_DIR.exists():
        return None
    for fp in INBOX_DIR.glob("*.json"):
        row = _read(fp)
        if not row:
            continue
        if row.get("status") != "pending":
            continue
        existing_term = str(row.get("term") or "").strip().lower()
        if existing_term != term_norm:
            continue
        if _origin_ref(row.get("origin")) != origin_ref:
            continue
        return row
    return None


def enqueue_proposal(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Persist a proposal. Idempotent on (term, origin.ref) for pending items."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dict")
    term = str(payload.get("term") or "").strip()
    if not term:
        raise ValueError("term required")
    category = str(payload.get("category") or "").strip().lower()
    if category not in _VALID_CATEGORIES:
        raise ValueError(f"invalid category: {category!r}")

    _ensure_dir()
    origin = payload.get("origin") if isinstance(payload.get("origin"), dict) else {}
    origin_ref = str(origin.get("ref") or "")
    term_norm = term.lower()
    duplicate = _find_pending_duplicate(term_norm, origin_ref)
    if duplicate:
        return duplicate

    iso = _now_iso()
    proposal_id = _short_id()
    record: Dict[str, Any] = {
        "id": proposal_id,
        "term": term,
        "category": category,
        "canonical_match": payload.get("canonical_match"),
        "confidence": float(payload.get("confidence") or 0.0),
        "rationale": str(payload.get("rationale") or ""),
        "origin": {
            "kind": str(origin.get("kind") or "unknown"),
            "ref": origin_ref,
        },
        "status": "pending",
        "created_at": iso,
        "updated_at": iso,
        "decided_by": None,
    }
    try:
        save_json(_path_for(proposal_id), record, indent=2)
    except Exception as exc:
        logger.warning("semantic_learning enqueue failed: %s", exc)
    return record


def list_proposals(*, status: str | None = None, limit: int = 200) -> List[Dict[str, Any]]:
    if not INBOX_DIR.exists():
        return []
    status_filter = (status or "").strip().lower() or None
    rows: List[Dict[str, Any]] = []
    for fp in INBOX_DIR.glob("*.json"):
        row = _read(fp)
        if not row:
            continue
        if status_filter and str(row.get("status") or "") != status_filter:
            continue
        rows.append(row)
    rows.sort(
        key=lambda r: (0 if r.get("status") == "pending" else 1, str(r.get("created_at") or "")),
        reverse=False,
    )
    # Pending first (asc), then decided (asc by created_at). Then trim.
    return rows[: max(0, int(limit or 0)) or len(rows)]


def update_proposal_status(proposal_id: str, *, status: str, by: str) -> Dict[str, Any] | None:
    status_l = (status or "").strip().lower()
    if status_l not in {"approved", "rejected"}:
        raise ValueError(f"invalid status: {status!r}")
    path = _path_for(proposal_id)
    row = _read(path)
    if not row:
        return None
    if row.get("status") != "pending":
        # Idempotent — already decided.
        return row
    row["status"] = status_l
    row["decided_by"] = str(by or "")
    row["updated_at"] = _now_iso()
    try:
        save_json(path, row, indent=2)
    except Exception as exc:
        logger.warning("semantic_learning update_proposal_status failed: %s", exc)
        return None
    return row
