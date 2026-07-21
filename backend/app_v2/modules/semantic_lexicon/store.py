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


ALIAS_META_KEYS = ("semantic_class", "normalization", "value_domain")


def _clean_aliases(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _json_meta_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_json_meta_value(item) for item in value[:100]]
    if isinstance(value, dict):
        return {str(k): _json_meta_value(v) for k, v in value.items() if str(k).strip()}
    return str(value)


def _clean_alias_entry(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        aliases = _clean_aliases(value.get("aliases", value.get("values", [])))
        entry: Dict[str, Any] = {"aliases": aliases}
        for key in ALIAS_META_KEYS:
            if key in value:
                entry[key] = _json_meta_value(value.get(key))
        return entry
    return {"aliases": _clean_aliases(value)}


def _alias_entries_to_groups(entries: Dict[str, Dict[str, Any]]) -> Dict[str, List[str]]:
    return {str(k): _clean_aliases((v or {}).get("aliases")) for k, v in (entries or {}).items()}


def _read_doc(path: Path, group_key: str) -> Dict[str, Any]:
    raw = load_json(path, {})
    if not isinstance(raw, dict):
        return {"version": 1, "groups": {}, "audit": {}, group_key: {}}
    groups = raw.get(group_key) if isinstance(raw.get(group_key), dict) else {}
    audit = raw.get("audit") if isinstance(raw.get("audit"), dict) else {}
    clean_groups: Dict[str, List[str]] = {}
    clean_entries: Dict[str, Dict[str, Any]] = {}
    for key, aliases in groups.items():
        canonical = str(key or "").strip()
        if not canonical:
            continue
        if group_key == "groups":
            entry = _clean_alias_entry(aliases)
            clean_entries[canonical] = entry
            clean_groups[canonical] = _clean_aliases(entry.get("aliases"))
        else:
            clean_groups[canonical] = _clean_aliases(aliases)
    return {
        "version": 1,
        "updated_at": str(raw.get("updated_at") or ""),
        group_key: clean_groups,
        "entries": clean_entries,
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


def _write_alias_doc(path: Path, entries: Dict[str, Dict[str, Any]], audit: Dict[str, Dict[str, Any]]) -> None:
    payload = {
        "version": 2,
        "updated_at": _now_iso(),
        "groups": {
            str(k): {
                alias_key: _json_meta_value(alias_value)
                for alias_key, alias_value in _clean_alias_entry(v).items()
                if alias_key == "aliases" or alias_key in ALIAS_META_KEYS
            }
            for k, v in (entries or {}).items()
            if str(k).strip()
        },
        "audit": {str(k): v for k, v in (audit or {}).items() if isinstance(v, dict)},
    }
    try:
        save_json(path, payload, indent=2)
    except Exception as exc:
        logger.warning("semantic_lexicon write failed for %s: %s", path.name, exc)


def load_alias_groups() -> Dict[str, List[str]]:
    """Return alias groups stored on disk. Empty dict when file is absent."""
    return _read_doc(ALIAS_FILE, "groups").get("groups", {})


def load_alias_group_entries() -> Dict[str, Dict[str, Any]]:
    """Return alias groups with optional per-canonical metadata.

    Backward-compatible files whose values are plain alias lists are surfaced as
    ``{"aliases": [...]}`` entries.
    """
    doc = _read_doc(ALIAS_FILE, "groups")
    entries = doc.get("entries") if isinstance(doc.get("entries"), dict) else {}
    if entries:
        return {str(k): _clean_alias_entry(v) for k, v in entries.items() if str(k).strip()}
    return {str(k): {"aliases": v} for k, v in (doc.get("groups") or {}).items() if str(k).strip()}


def save_alias_group_entries(entries: Dict[str, Dict[str, Any]], *, by: str = "") -> None:
    doc = _read_doc(ALIAS_FILE, "groups")
    audit = doc.get("audit") or {}
    iso = _now_iso()
    for key in (entries or {}).keys():
        audit[str(key)] = {"author": str(by or audit.get(str(key), {}).get("author") or ""), "updated_at": iso}
    _write_alias_doc(ALIAS_FILE, entries, audit)


def save_alias_groups(groups: Dict[str, List[str]], *, by: str = "") -> None:
    """Atomic overwrite of the alias groups file.

    Caller is responsible for any merge with the in-code seed — this function
    persists the disk state as-is. Audit metadata (when provided as
    `__audit__` key inside `groups`) is stripped to a separate audit map.
    """
    existing = load_alias_group_entries()
    entries: Dict[str, Dict[str, Any]] = {}
    for key, aliases in (groups or {}).items():
        canonical = str(key or "").strip()
        if not canonical:
            continue
        entry = dict(existing.get(canonical) or {})
        entry["aliases"] = _clean_aliases(aliases)
        entries[canonical] = entry
    save_alias_group_entries(entries, by=by)


def load_intent_hints() -> Dict[str, List[str]]:
    return _read_doc(INTENT_FILE, "intents").get("intents", {})


def save_intent_hints(intents: Dict[str, List[str]], *, by: str = "") -> None:
    doc = _read_doc(INTENT_FILE, "intents")
    audit = doc.get("audit") or {}
    iso = _now_iso()
    for key in (intents or {}).keys():
        audit[str(key)] = {"author": str(by or audit.get(str(key), {}).get("author") or ""), "updated_at": iso}
    _write_doc(INTENT_FILE, "intents", intents, audit)


def append_change(
    *,
    scope: str,
    key: str,
    before: List[str],
    after: List[str],
    by: str,
    before_meta: Dict[str, Any] | None = None,
    after_meta: Dict[str, Any] | None = None,
) -> None:
    """Append one change record to changes.jsonl. Best-effort."""
    try:
        jsonl_append(
            CHANGES_FILE,
            {
                "scope": str(scope or ""),
                "key": str(key or ""),
                "before": _clean_aliases(before),
                "after": _clean_aliases(after),
                "before_meta": before_meta or {},
                "after_meta": after_meta or {},
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
