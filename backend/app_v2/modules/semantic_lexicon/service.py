"""Semantic lexicon service — effective merge + upsert/delete with audit.

The agent_runtime semantic resolver calls `effective_alias_groups(seed)` /
`effective_intent_hints(seed)` to obtain a merged view of (hardcoded seed)
∪ (disk file). Disk values override the seed for the same canonical key.
Disk-only keys (new admin-added vocabulary) are appended.

Upsert/delete write the new state to disk and append an audit record so the
SemanticLayerTab "어휘 사전" view can render a change timeline.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .store import (
    append_change,
    load_alias_group_entries,
    load_alias_groups,
    load_intent_hints,
    save_alias_group_entries,
    save_alias_groups,
    save_intent_hints,
)


ALIAS_META_KEYS = ("semantic_class", "normalization", "value_domain")


def _clean_aliases(value: Any) -> List[str]:
    return [str(a).strip() for a in (value or []) if str(a).strip()]


def _clean_alias_entry(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        entry: Dict[str, Any] = {"aliases": _clean_aliases(value.get("aliases", []))}
        for key in ALIAS_META_KEYS:
            if key in value:
                entry[key] = value.get(key)
        return entry
    return {"aliases": _clean_aliases(value)}


def _entry_meta(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {key: entry.get(key) for key in ALIAS_META_KEYS if key in entry}


def _merge(seed: Dict[str, List[str]], disk: Dict[str, List[str]]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {k: list(v or []) for k, v in (seed or {}).items()}
    for key, aliases in (disk or {}).items():
        canonical = str(key or "").strip()
        if not canonical:
            continue
        cleaned = [str(a).strip() for a in (aliases or []) if str(a).strip()]
        out[canonical] = cleaned
    return out


def _merge_entries(seed: Dict[str, Any], disk: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {
        str(k): _clean_alias_entry(v)
        for k, v in (seed or {}).items()
        if str(k or "").strip()
    }
    for key, value in (disk or {}).items():
        canonical = str(key or "").strip()
        if not canonical:
            continue
        out[canonical] = _clean_alias_entry(value)
    return out


def effective_alias_groups(seed: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """Seed dictionary merged with the disk-stored alias groups (disk wins)."""
    return {
        key: list((entry or {}).get("aliases") or [])
        for key, entry in effective_alias_group_entries(seed or {}).items()
    }


def effective_alias_group_entries(seed: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Seed dictionary merged with disk alias entries, including metadata."""
    return _merge_entries(seed or {}, load_alias_group_entries())


def effective_alias_group_meta(seed: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        key: _entry_meta(entry)
        for key, entry in effective_alias_group_entries(seed or {}).items()
        if _entry_meta(entry)
    }


def effective_intent_hints(seed: Dict[str, List[str]]) -> Dict[str, List[str]]:
    return _merge(seed or {}, load_intent_hints())


def upsert_alias_group(
    canonical: str,
    aliases: List[str],
    *,
    by: str,
    seed: Dict[str, List[str]] | None = None,
    meta: Dict[str, Any] | None = None,
) -> Dict[str, List[str]]:
    """Add or replace one alias group on disk and audit the change.

    `seed` is unused for the write path (disk is the override layer) but the
    audit log records the *before* state as the merged effective view so the
    timeline shows what actually changed for end-users.
    """
    canonical = str(canonical or "").strip()
    if not canonical:
        raise ValueError("canonical key required")
    cleaned = _clean_aliases(aliases)
    disk_entries = load_alias_group_entries()
    before_entry = effective_alias_group_entries(seed or {}).get(canonical) or {}
    entry = dict(disk_entries.get(canonical) or {})
    entry["aliases"] = cleaned
    for key, value in (meta or {}).items():
        if key in ALIAS_META_KEYS:
            entry[key] = value
    disk_entries[canonical] = entry
    save_alias_group_entries(disk_entries, by=by)
    append_change(
        scope="alias_groups",
        key=canonical,
        before=list(before_entry.get("aliases") or []),
        after=cleaned,
        by=by,
        before_meta=_entry_meta(before_entry),
        after_meta=_entry_meta(entry),
    )
    return {key: list((value or {}).get("aliases") or []) for key, value in disk_entries.items()}


def delete_alias_group(canonical: str, *, by: str) -> bool:
    """Remove an alias group from the disk override layer.

    Seed-only canonicals cannot be deleted (returns False). To "neutralize" a
    seed key, upsert it with an empty list (the disk override wins).
    """
    canonical = str(canonical or "").strip()
    if not canonical:
        return False
    disk_entries = load_alias_group_entries()
    if canonical not in disk_entries:
        return False
    before_entry = dict(disk_entries.get(canonical) or {})
    del disk_entries[canonical]
    save_alias_group_entries(disk_entries, by=by)
    append_change(
        scope="alias_groups",
        key=canonical,
        before=list(before_entry.get("aliases") or []),
        after=[],
        by=by,
        before_meta=_entry_meta(before_entry),
        after_meta={},
    )
    return True


def upsert_intent_hint(
    intent: str,
    required_canonicals: List[str],
    *,
    by: str,
    seed: Dict[str, List[str]] | None = None,
) -> Dict[str, List[str]]:
    intent = str(intent or "").strip()
    if not intent:
        raise ValueError("intent key required")
    cleaned = [str(c).strip() for c in (required_canonicals or []) if str(c).strip()]
    disk = load_intent_hints()
    before_disk = list(disk.get(intent) or [])
    before_effective = list(_merge(seed or {}, {intent: before_disk}).get(intent) or [])
    disk[intent] = cleaned
    save_intent_hints(disk, by=by)
    append_change(scope="intent_hints", key=intent, before=before_effective, after=cleaned, by=by)
    return disk


def delete_intent_hint(intent: str, *, by: str) -> bool:
    intent = str(intent or "").strip()
    if not intent:
        return False
    disk = load_intent_hints()
    if intent not in disk:
        return False
    before = list(disk.get(intent) or [])
    del disk[intent]
    save_intent_hints(disk, by=by)
    append_change(scope="intent_hints", key=intent, before=before, after=[], by=by)
    return True
