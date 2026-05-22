"""Semantic lexicon service — effective merge + upsert/delete with audit.

The agent_runtime semantic resolver calls `effective_alias_groups(seed)` /
`effective_intent_hints(seed)` to obtain a merged view of (hardcoded seed)
∪ (disk file). Disk values override the seed for the same canonical key.
Disk-only keys (new admin-added vocabulary) are appended.

Upsert/delete write the new state to disk and append an audit record so the
SemanticLayerTab "어휘 사전" view can render a change timeline.
"""
from __future__ import annotations

from typing import Dict, List

from .store import (
    append_change,
    load_alias_groups,
    load_intent_hints,
    save_alias_groups,
    save_intent_hints,
)


def _merge(seed: Dict[str, List[str]], disk: Dict[str, List[str]]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {k: list(v or []) for k, v in (seed or {}).items()}
    for key, aliases in (disk or {}).items():
        canonical = str(key or "").strip()
        if not canonical:
            continue
        cleaned = [str(a).strip() for a in (aliases or []) if str(a).strip()]
        out[canonical] = cleaned
    return out


def effective_alias_groups(seed: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """Seed dictionary merged with the disk-stored alias groups (disk wins)."""
    return _merge(seed or {}, load_alias_groups())


def effective_intent_hints(seed: Dict[str, List[str]]) -> Dict[str, List[str]]:
    return _merge(seed or {}, load_intent_hints())


def upsert_alias_group(
    canonical: str,
    aliases: List[str],
    *,
    by: str,
    seed: Dict[str, List[str]] | None = None,
) -> Dict[str, List[str]]:
    """Add or replace one alias group on disk and audit the change.

    `seed` is unused for the write path (disk is the override layer) but the
    audit log records the *before* state as the merged effective view so the
    timeline shows what actually changed for end-users.
    """
    canonical = str(canonical or "").strip()
    if not canonical:
        raise ValueError("canonical key required")
    cleaned = [str(a).strip() for a in (aliases or []) if str(a).strip()]
    disk = load_alias_groups()
    before_disk = list(disk.get(canonical) or [])
    before_effective = list(_merge(seed or {}, {canonical: before_disk}).get(canonical) or [])
    disk[canonical] = cleaned
    save_alias_groups(disk, by=by)
    append_change(scope="alias_groups", key=canonical, before=before_effective, after=cleaned, by=by)
    return disk


def delete_alias_group(canonical: str, *, by: str) -> bool:
    """Remove an alias group from the disk override layer.

    Seed-only canonicals cannot be deleted (returns False). To "neutralize" a
    seed key, upsert it with an empty list (the disk override wins).
    """
    canonical = str(canonical or "").strip()
    if not canonical:
        return False
    disk = load_alias_groups()
    if canonical not in disk:
        return False
    before = list(disk.get(canonical) or [])
    del disk[canonical]
    save_alias_groups(disk, by=by)
    append_change(scope="alias_groups", key=canonical, before=before, after=[], by=by)
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
