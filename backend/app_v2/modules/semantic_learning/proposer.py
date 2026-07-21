"""Classify extracted terms against the existing lexicon.

Categories:
    mapping        — term normalizes to an existing canonical or one of its aliases.
                     Suggest adding the raw term as a new alias to that group.
    new_canonical  — term is meaningful (length, character class) and has no
                     overlap with any existing canonical/alias.
    conflict       — term normalizes into multiple distinct canonicals (rare —
                     usually short forms that collide).
    reject         — too short, digit-only, stop-word-like.

Confidence is a hint for the UI sort order, not a hard threshold. Values
range in (0, 1]; reject items return 0.0.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List


def _norm(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", str(value or "").lower())


def _is_too_short(term_norm: str) -> bool:
    if len(term_norm) < 2:
        return True
    # Mixed numeric + 1 letter (e.g., "x1") is too thin
    if len(term_norm) == 2 and term_norm.isalnum() and any(c.isdigit() for c in term_norm) and any(c.isalpha() for c in term_norm):
        return True
    return False


def _matches_alias_list(term_norm: str, aliases: Iterable[str]) -> bool:
    for alias in aliases or []:
        alias_norm = _norm(alias)
        if alias_norm and alias_norm == term_norm:
            return True
    return False


def classify_proposal(
    term: str,
    *,
    alias_groups: Dict[str, List[str]],
    column_catalog_keys: set[str] | None = None,
) -> Dict[str, Any]:
    """Classify a candidate term and return a proposal record dict."""
    raw = str(term or "").strip()
    term_norm = _norm(raw)
    column_catalog_keys = column_catalog_keys or set()

    result: Dict[str, Any] = {
        "term": raw,
        "category": "reject",
        "canonical_match": None,
        "confidence": 0.0,
        "rationale": "",
    }

    if not raw or not term_norm:
        result["rationale"] = "empty after normalization"
        return result
    if _is_too_short(term_norm):
        result["rationale"] = "too short"
        return result
    if term_norm.isdigit():
        result["rationale"] = "digits only"
        return result

    matched_canonicals: List[str] = []
    for canonical, aliases in (alias_groups or {}).items():
        canonical_norm = _norm(canonical)
        if canonical_norm and canonical_norm == term_norm:
            matched_canonicals.append(canonical)
            continue
        if _matches_alias_list(term_norm, aliases):
            matched_canonicals.append(canonical)

    if len(matched_canonicals) > 1:
        result["category"] = "conflict"
        result["canonical_match"] = matched_canonicals[0]
        result["confidence"] = 0.4
        result["rationale"] = f"matches multiple canonicals: {', '.join(matched_canonicals)}"
        return result

    if len(matched_canonicals) == 1:
        result["category"] = "mapping"
        canonical = matched_canonicals[0]
        result["canonical_match"] = canonical
        # Higher confidence when raw term differs from existing aliases —
        # otherwise it's a duplicate.
        existing = {_norm(a) for a in alias_groups.get(canonical, []) or []}
        existing.add(_norm(canonical))
        result["confidence"] = 0.85 if term_norm not in existing else 0.55
        result["rationale"] = f"normalizes to existing canonical '{canonical}'"
        return result

    # No canonical match. Maybe it shows up in column_catalog as an unmapped column?
    if term_norm in {_norm(k) for k in column_catalog_keys}:
        result["category"] = "new_canonical"
        result["canonical_match"] = None
        result["confidence"] = 0.7
        result["rationale"] = "matches a column catalog key but no alias group"
        return result

    # Genuinely new vocabulary candidate.
    result["category"] = "new_canonical"
    result["canonical_match"] = None
    result["confidence"] = 0.55
    result["rationale"] = "no existing canonical or alias matches"
    return result
