"""Shared Agent semantic resolver.

This module keeps the deterministic prompt-to-semantic-frame logic in one
place while letting each Unit AI expose its existing public frame shape.
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, TypedDict

from app_v2.modules.semantic_learning import extractor as semantic_extractor
from app_v2.modules.semantic_lexicon import service as semantic_lexicon_service


class SemanticFrame(TypedDict, total=False):
    natural_language: str
    resolved_columns: list[str]
    unknown_column_terms: list[str]
    value_terms: list[str]
    synonyms: dict[str, list[str]]
    step_mapping: dict[str, Any]
    alias_hits: list[dict[str, str]]
    slot_hints: dict[str, Any]
    unknown_terms: list[str]
    intent_matches: dict[str, list[str]]
    warnings: list[str]


_INFORM_SEMANTIC_ALIAS_SEED: dict[str, list[str]] = {
    "product": ["product", "prod", "제품", "제품명"],
    "lot_id": ["lot", "lot_id", "LOT", "로트"],
    "module": ["module", "mod", "모듈", "담당모듈"],
    "note": ["note", "text", "내용", "노트", "메시지"],
    "mail_target": ["mail", "email", "to", "담당자", "수신자", "메일"],
    "snapshot_custom_cols": ["snapshot", "knob", "custom", "split table", "splittable", "스냅샷", "노브", "세트"],
}
_SLOT_HINT_KEYS = {"product", "lot_id", "module", "note"}
_SEMANTIC_VALUE_STOPWORDS = {
    "알려줘",
    "알려주세요",
    "등록",
    "생성",
    "추가",
    "확인",
    "요청",
}
_SNAPSHOT_COL_RE = re.compile(r"\b(?:KNOB|CUSTOM|INLINE|VM)_[A-Za-z0-9_]+\b", re.IGNORECASE)


def _clean_text(value: Any, max_len: int = 2000) -> str:
    return str(value or "").replace("\x00", " ").strip()[: max(1, max_len)]


def _string_list(value: Any, limit: int = 100) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = re.split(r"[,;\n]+", value)
    elif isinstance(value, (list, tuple, set)):
        raw = value
    else:
        raw = [value]
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = _clean_text(item, 160)
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _norm_token(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", str(value or "").casefold())


def _semantic_aliases(columns: list[str], resolved_columns: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for col in resolved_columns[:20]:
        aliases = [col]
        spaced = col.replace("_", " ")
        if spaced != col:
            aliases.append(spaced)
        compact = col.replace("_", "")
        if compact and compact not in aliases:
            aliases.append(compact)
        out[col] = aliases
    if not out:
        for col in columns[:8]:
            out[col] = [col, col.replace("_", " ")]
    return out


def _fb():
    from routers import filebrowser as fb

    return fb


def _resolve_columns(prompt: str, columns: list[str], product: str) -> tuple[list[str], list[str], list[str], dict[str, Any]]:
    if not columns:
        return [], [], [], {}
    try:
        resolved_columns, unknown_terms = _fb()._resolve_ai_sql_prompt_columns(prompt, columns)
    except Exception:
        resolved_columns, unknown_terms = [], []
    try:
        value_terms = _fb()._ai_sql_prompt_priority_values(prompt, columns)[:20]
    except Exception:
        value_terms = []
    try:
        step_mapping = _fb()._public_ai_sql_step_mapping_context(
            _fb()._ai_sql_step_mapping_context(prompt, columns, product)
        )
    except Exception:
        step_mapping = {}
    return list(resolved_columns or []), list(unknown_terms or []), list(value_terms or []), step_mapping


def _effective_alias_groups() -> dict[str, list[str]]:
    try:
        return semantic_lexicon_service.effective_alias_groups(_INFORM_SEMANTIC_ALIAS_SEED)
    except Exception:
        return deepcopy(_INFORM_SEMANTIC_ALIAS_SEED)


def _effective_intent_hints() -> dict[str, list[str]]:
    try:
        return semantic_lexicon_service.effective_intent_hints({})
    except Exception:
        return {}


def _alias_hits(prompt: str, alias_groups: dict[str, list[str]]) -> tuple[list[dict[str, str]], set[str]]:
    prompt_norm = _norm_token(prompt)
    hits: list[dict[str, str]] = []
    matched_norms: set[str] = set()
    if not prompt_norm:
        return hits, matched_norms
    for canonical, aliases in (alias_groups or {}).items():
        canonical_text = str(canonical or "").strip()
        if not canonical_text:
            continue
        for alias in [canonical_text, *list(aliases or [])]:
            alias_text = str(alias or "").strip()
            alias_norm = _norm_token(alias_text)
            if len(alias_norm) < 2:
                continue
            if alias_norm and alias_norm in prompt_norm:
                hits.append({"canonical": canonical_text, "alias": alias_text})
                matched_norms.add(alias_norm)
                break
    return hits, matched_norms


def _first_regex(patterns: list[str], text: str, max_len: int = 160) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            return _clean_text(match.group(1), max_len).strip(" ,;")
    return ""


def _snapshot_requested(prompt: str, slots: dict[str, Any]) -> bool:
    if bool(slots.get("wants_snapshot")):
        return True
    if re.search(r"\b(knob|custom|split\s*table|splittable|snapshot|set)\b", prompt, re.IGNORECASE):
        return True
    return any(token in prompt for token in ("노브", "세트", "스냅샷"))


def _value_after_alias(prompt: str, aliases: list[str], max_len: int = 160) -> str:
    for alias in aliases:
        alias_text = str(alias or "").strip()
        if len(_norm_token(alias_text)) < 2:
            continue
        pattern = rf"{re.escape(alias_text)}\s*[:=]?\s*([A-Za-z0-9가-힣_.@/\-]+)"
        value = _first_regex([pattern], prompt, max_len=max_len)
        if value and _norm_token(value) not in _SEMANTIC_VALUE_STOPWORDS:
            return value
    return ""


def _slot_hints(prompt: str, alias_hits: list[dict[str, str]], alias_groups: dict[str, list[str]]) -> dict[str, Any]:
    hit_canonicals = {str(hit.get("canonical") or "") for hit in alias_hits}
    hints: dict[str, Any] = {}
    for key in sorted(_SLOT_HINT_KEYS & hit_canonicals):
        aliases = [key, *list(alias_groups.get(key) or [])]
        value = _value_after_alias(prompt, aliases, max_len=5000 if key == "note" else 160)
        if value:
            hints[key] = value
    if "snapshot_custom_cols" in hit_canonicals or _snapshot_requested(prompt, hints):
        hints["wants_snapshot"] = True
        cols = _string_list(_SNAPSHOT_COL_RE.findall(prompt), limit=80)
        if cols:
            hints["snapshot_custom_cols"] = cols
    return hints


def _unknown_terms(
    prompt: str,
    alias_groups: dict[str, list[str]],
    matched_norms: set[str],
    ignored_values: list[Any] | None = None,
) -> list[str]:
    known_norms: set[str] = set(matched_norms)
    for canonical, aliases in (alias_groups or {}).items():
        for value in [canonical, *list(aliases or [])]:
            norm = _norm_token(value)
            if norm:
                known_norms.add(norm)
    for value in ignored_values or []:
        norm = _norm_token(value)
        if norm:
            known_norms.add(norm)
    out: list[str] = []
    for term in semantic_extractor.extract_terms(prompt):
        norm = _norm_token(term)
        if not norm or norm in known_norms:
            continue
        if any(norm in known or known in norm for known in known_norms if len(known) >= 3):
            continue
        out.append(term)
        if len(out) >= 20:
            break
    return out


def resolve(
    prompt: str,
    columns: list[str] | tuple[str, ...] | None = None,
    product: str = "",
    dtypes: dict[str, str] | None = None,
) -> SemanticFrame:
    """Resolve prompt terms into the shared Agent semantic frame.

    ``dtypes`` is accepted for the shared API contract; current deterministic
    resolution only needs column names and product for FileBrowser step mapping.
    """
    del dtypes
    prompt_text = _clean_text(prompt, 2000)
    column_list = _string_list(list(columns or []), limit=300)
    resolved_columns, unknown_column_terms, value_terms, step_mapping = _resolve_columns(
        prompt_text,
        column_list,
        _clean_text(product, 160),
    )

    alias_groups = _effective_alias_groups()
    intent_hints = _effective_intent_hints()
    alias_hits, matched_norms = _alias_hits(prompt_text, alias_groups)
    slot_hints = _slot_hints(prompt_text, alias_hits, alias_groups)
    ignored_values = [value for value in slot_hints.values() if isinstance(value, str)]
    ignored_values.extend(_string_list(slot_hints.get("snapshot_custom_cols"), limit=80))
    unknown_terms = _unknown_terms(prompt_text, alias_groups, matched_norms, ignored_values)
    hit_canonicals = {str(hit.get("canonical") or "") for hit in alias_hits}
    intent_matches = {
        intent: required
        for intent, required in (intent_hints or {}).items()
        if required and all(str(item) in hit_canonicals for item in required)
    }
    warnings: list[str] = []
    if unknown_terms:
        warnings.append("Unmapped semantic terms: " + ", ".join(unknown_terms[:8]))

    return {
        "natural_language": prompt_text,
        "resolved_columns": resolved_columns,
        "unknown_column_terms": unknown_column_terms,
        "value_terms": value_terms,
        "synonyms": _semantic_aliases(column_list, resolved_columns),
        "step_mapping": step_mapping,
        "alias_hits": alias_hits,
        "slot_hints": deepcopy(slot_hints),
        "unknown_terms": unknown_terms,
        "intent_matches": intent_matches,
        "warnings": warnings,
    }
