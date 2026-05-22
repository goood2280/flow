"""Disk store for semantic lexicon.

File layout under `PATHS.data_root / "semantic"`:

    alias_groups.json   — {"version":1, "updated_at": "<iso>",
                           "groups": {"canonical_key": ["alias1", ...]},
                           "audit":  {"canonical_key": {"author": str, "updated_at": iso}}}
    intent_hints.json   — same shape with `intents` instead of `groups`
    changes.jsonl       — append-only audit log of edits

All IO is best-effort: missing files yield empty dicts; write failures are
logged but never raise to the caller. This keeps semantic resolution alive
even when runtime storage is read-only.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from core.paths import PATHS
from core.utils import jsonl_append, jsonl_read, load_json, save_json

logger = logging.getLogger("flow.semantic_lexicon")

LEXICON_DIR = PATHS.data_root / "semantic"
ALIAS_FILE = LEXICON_DIR / "alias_groups.json"
INTENT_FILE = LEXICON_DIR / "intent_hints.json"
CHANGES_FILE = LEXICON_DIR / "changes.jsonl"


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _clean_aliases(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _read_doc(path: Path, group_key: str) -> Dict[str, Any]:
    raw = load_json(path, {})
    if not isinstance(raw, dict):
        return {"version": 1, "groups": {}, "audit": {}, group_key: {}}
    groups = raw.get(group_key) if isinstance(raw.get(group_key), dict) else {}
    audit = raw.get("audit") if isinstance(raw.get("audit"), dict) else {}
    clean_groups: Dict[str, List[str]] = {}
    for key, aliases in groups.items():
        canonical = str(key or "").strip()
        if not canonical:
            continue
        clean_groups[canonical] = _clean_aliases(aliases)
    return {
        "version": 1,
        "updated_at": str(raw.get("updated_at") or ""),
        group_key: clean_groups,
        "audit": {str(k): v for k, v in audit.items() if isinstance(v, dict)},
    }


def _write_doc(path: Path, group_key: str, groups: Dict[str, List[str]], audit: Dict[str, Dict[str, Any]]) -> None:
    payload = {
        "version": 1,
        "updated_at": _now_iso(),
        group_key: {k: _clean_aliases(v) for k, v in (groups or {}).items() if k},
        "audit": {str(k): v for k, v in (audit or {}).items() if isinstance(v, dict)},
    }
    try:
        save_json(path, payload, indent=2)
    except Exception as exc:
        logger.warning("semantic_lexicon write failed for %s: %s", path.name, exc)


def load_alias_groups() -> Dict[str, List[str]]:
    """Return alias groups stored on disk. Empty dict when file is absent."""
    return _read_doc(ALIAS_FILE, "groups").get("groups", {})


def save_alias_groups(groups: Dict[str, List[str]], *, by: str = "") -> None:
    """Atomic overwrite of the alias groups file.

    Caller is responsible for any merge with the in-code seed — this function
    persists the disk state as-is. Audit metadata (when provided as
    `__audit__` key inside `groups`) is stripped to a separate audit map.
    """
    doc = _read_doc(ALIAS_FILE, "groups")
    audit = doc.get("audit") or {}
    iso = _now_iso()
    for key in (groups or {}).keys():
        audit[str(key)] = {"author": str(by or audit.get(str(key), {}).get("author") or ""), "updated_at": iso}
    _write_doc(ALIAS_FILE, "groups", groups, audit)


def load_intent_hints() -> Dict[str, List[str]]:
    return _read_doc(INTENT_FILE, "intents").get("intents", {})


def save_intent_hints(intents: Dict[str, List[str]], *, by: str = "") -> None:
    doc = _read_doc(INTENT_FILE, "intents")
    audit = doc.get("audit") or {}
    iso = _now_iso()
    for key in (intents or {}).keys():
        audit[str(key)] = {"author": str(by or audit.get(str(key), {}).get("author") or ""), "updated_at": iso}
    _write_doc(INTENT_FILE, "intents", intents, audit)


def append_change(*, scope: str, key: str, before: List[str], after: List[str], by: str) -> None:
    """Append one change record to changes.jsonl. Best-effort."""
    try:
        jsonl_append(
            CHANGES_FILE,
            {
                "scope": str(scope or ""),
                "key": str(key or ""),
                "before": _clean_aliases(before),
                "after": _clean_aliases(after),
                "by": str(by or ""),
            },
        )
    except Exception as exc:
        logger.debug("semantic_lexicon append_change failed: %s", exc)


def list_changes(limit: int = 100) -> List[Dict[str, Any]]:
    try:
        return jsonl_read(CHANGES_FILE, limit=max(1, int(limit or 0)))
    except Exception:
        return []
