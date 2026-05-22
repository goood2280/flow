"""Semantic lexicon — runtime-editable alias groups and intent hints.

Wave 1 / Track B: store + service module only. No router wire-up yet.

The hardcoded alias dictionary in `agent_runtime.semantic._ALIAS_GROUPS` is the
default seed. On disk, admin/page managers can override or extend the lexicon
via `data/flow-data/semantic/alias_groups.json` and `intent_hints.json`.
Changes are audited in `data/flow-data/semantic/changes.jsonl` (append-only).
"""
from .service import (
    delete_alias_group,
    delete_intent_hint,
    effective_alias_groups,
    effective_intent_hints,
    upsert_alias_group,
    upsert_intent_hint,
)
from .store import (
    append_change,
    list_changes,
    load_alias_groups,
    load_intent_hints,
    save_alias_groups,
    save_intent_hints,
)

__all__ = [
    "append_change",
    "delete_alias_group",
    "delete_intent_hint",
    "effective_alias_groups",
    "effective_intent_hints",
    "list_changes",
    "load_alias_groups",
    "load_intent_hints",
    "save_alias_groups",
    "save_intent_hints",
    "upsert_alias_group",
    "upsert_intent_hint",
]
