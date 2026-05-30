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
from core import semantic_source_catalog
from core import semantic_measure_catalog


class SemanticFrame(TypedDict, total=False):
    natural_language: str
    resolved_columns: list[str]
    unknown_column_terms: list[str]
    value_terms: list[str]
    synonyms: dict[str, list[str]]
    step_mapping: dict[str, Any]
    alias_hits: list[dict[str, Any]]
    alias_group_meta: dict[str, dict[str, Any]]
    slot_hints: dict[str, Any]
    unknown_terms: list[dict[str, Any]]
    unknown_term_texts: list[str]
    value_catalog_matches: list[dict[str, Any]]
    source_catalog_matches: list[dict[str, Any]]
    measurement_term_matches: list[dict[str, Any]]
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


def _effective_alias_group_entries() -> dict[str, dict[str, Any]]:
    try:
        return semantic_lexicon_service.effective_alias_group_entries(_INFORM_SEMANTIC_ALIAS_SEED)
    except Exception:
        return {key: {"aliases": list(value or [])} for key, value in deepcopy(_INFORM_SEMANTIC_ALIAS_SEED).items()}


def _alias_group_meta(alias_entries: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for canonical, entry in (alias_entries or {}).items():
        meta = {
            key: entry.get(key)
            for key in ("semantic_class", "normalization", "value_domain")
            if key in entry
        }
        if meta:
            out[str(canonical)] = meta
    return out


def _effective_intent_hints() -> dict[str, list[str]]:
    try:
        return semantic_lexicon_service.effective_intent_hints({})
    except Exception:
        return {}


def _alias_hits(prompt: str, alias_groups: dict[str, list[str]], alias_entries: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], set[str]]:
    prompt_norm = _norm_token(prompt)
    hits: list[dict[str, Any]] = []
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
                hit: dict[str, Any] = {"canonical": canonical_text, "alias": alias_text}
                for key, value in _alias_group_meta(alias_entries).get(canonical_text, {}).items():
                    hit[key] = deepcopy(value)
                hits.append(hit)
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


def _source_label(source_ref: dict[str, Any] | None, sample_profile: dict[str, Any] | None = None) -> str:
    if isinstance(source_ref, dict):
        scope = _clean_text(source_ref.get("scope"), 80)
        root = _clean_text(source_ref.get("root"), 120)
        product = _clean_text(source_ref.get("product"), 120)
        file = _clean_text(source_ref.get("file"), 200)
        if root and product:
            return f"{scope or 'db_product'}:{root}/{product}"
        if file:
            return f"{scope or 'file'}:{file}"
    if isinstance(sample_profile, dict):
        return _clean_text(sample_profile.get("source"), 240)
    return ""


def _profile_value_matches(prompt: str, sample_profile: dict[str, Any] | None, limit: int = 20) -> list[dict[str, Any]]:
    if not isinstance(sample_profile, dict):
        return []
    prompt_norm = _norm_token(prompt)
    out: list[dict[str, Any]] = []
    for item in sample_profile.get("columns") or []:
        if not isinstance(item, dict):
            continue
        column = _clean_text(item.get("name"), 160)
        if not column:
            continue
        for value in item.get("sample_values") or []:
            text = _clean_text(value, 160)
            norm = _norm_token(text)
            if len(norm) < 2 or norm not in prompt_norm:
                continue
            out.append({
                "column": column,
                "value": text,
                "source": "sample_profile",
                "confidence": 0.62,
                "dtype": _clean_text(item.get("dtype"), 80),
            })
            if len(out) >= limit:
                return out
    return out


def _value_catalog_matches(
    prompt: str,
    columns: list[str],
    dtypes: dict[str, str] | None,
    sample_profile: dict[str, Any] | None,
    source_ref: dict[str, Any] | None,
    explicit_catalog: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    if isinstance(explicit_catalog, list):
        return [deepcopy(item) for item in explicit_catalog[:40] if isinstance(item, dict)]
    if not source_ref and not sample_profile:
        return []
    try:
        matches = _fb()._ai_sql_value_catalog_matches(
            prompt=prompt,
            columns=columns,
            dtypes=dtypes or {},
            sample_profile=sample_profile or {},
            source_ref=source_ref or {},
        )
        if isinstance(matches, list) and matches:
            return [deepcopy(item) for item in matches[:40] if isinstance(item, dict)]
    except Exception:
        pass
    return _profile_value_matches(prompt, sample_profile)


def _unknown_search_priority(
    term: str,
    *,
    columns: list[str],
    dtypes: dict[str, str] | None,
    value_catalog_matches: list[dict[str, Any]],
    source_ref: dict[str, Any] | None,
    sample_profile: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    term_norm = _norm_token(term)
    source = _source_label(source_ref, sample_profile)
    priorities: list[dict[str, Any]] = []
    if term_norm:
        for col in columns[:300]:
            col_text = _clean_text(col, 160)
            col_norm = _norm_token(col_text)
            if len(col_norm) >= 2 and (term_norm in col_norm or col_norm in term_norm):
                priorities.append({
                    "location": "column_name",
                    "table_file": source,
                    "column": col_text,
                    "confidence": 0.86,
                })
                break
        dtype_map = dtypes or {}
        for match in value_catalog_matches:
            value_norm = _norm_token(match.get("value"))
            column = _clean_text(match.get("column"), 160)
            if value_norm and (term_norm == value_norm or term_norm in value_norm or value_norm in term_norm):
                priorities.append({
                    "location": "value_catalog",
                    "table_file": source or _clean_text(match.get("source"), 160),
                    "column": column,
                    "value": _clean_text(match.get("value"), 160),
                    "confidence": float(match.get("confidence") or 0.74),
                })
                if column:
                    priorities.append({
                        "location": "enum_dtype_value",
                        "table_file": source,
                        "column": column,
                        "dtype": _clean_text(dtype_map.get(column), 80),
                        "confidence": 0.64,
                    })
                break
    priorities.extend(semantic_source_catalog.search_priorities_for_term(
        term,
        source_ref=source_ref,
        sample_profile=sample_profile,
    ))
    priorities.append({
        "location": "glossary",
        "table_file": "FLOW_DATA_ROOT semantic/glossary or curated knowledge",
        "confidence": 0.22,
    })
    priorities.sort(key=lambda item: float(item.get("confidence") or 0), reverse=True)
    return priorities[:5]


def _structured_unknown_terms(
    terms: list[str],
    *,
    columns: list[str],
    dtypes: dict[str, str] | None,
    value_catalog_matches: list[dict[str, Any]],
    source_ref: dict[str, Any] | None,
    sample_profile: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    return [
        {
            "term": term,
            "search_priority": _unknown_search_priority(
                term,
                columns=columns,
                dtypes=dtypes,
                value_catalog_matches=value_catalog_matches,
                source_ref=source_ref,
                sample_profile=sample_profile,
            ),
        }
        for term in terms
    ]


def resolve(
    prompt: str,
    columns: list[str] | tuple[str, ...] | None = None,
    product: str = "",
    dtypes: dict[str, str] | None = None,
    sample_profile: dict[str, Any] | None = None,
    source_ref: dict[str, Any] | None = None,
    value_catalog: list[dict[str, Any]] | None = None,
) -> SemanticFrame:
    """Resolve prompt terms into the shared Agent semantic frame.

    ``sample_profile`` and ``source_ref`` are optional FileBrowser-owned context
    hooks. When present, the resolver adds read-only value catalog matches from
    the selected source without changing source data.
    """
    prompt_text = _clean_text(prompt, 2000)
    column_list = _string_list(list(columns or []), limit=300)
    resolved_columns, unknown_column_terms, value_terms, step_mapping = _resolve_columns(
        prompt_text,
        column_list,
        _clean_text(product, 160),
    )

    alias_groups = _effective_alias_groups()
    alias_entries = _effective_alias_group_entries()
    intent_hints = _effective_intent_hints()
    alias_hits, matched_norms = _alias_hits(prompt_text, alias_groups, alias_entries)
    slot_hints = _slot_hints(prompt_text, alias_hits, alias_groups)
    catalog_matches = _value_catalog_matches(
        prompt_text,
        column_list,
        dtypes,
        sample_profile,
        source_ref,
        value_catalog,
    )
    source_matches = semantic_source_catalog.source_catalog_matches(
        prompt_text,
        source_ref=source_ref,
        sample_profile=sample_profile,
    )
    measurement_matches = semantic_measure_catalog.match_terms(
        prompt_text,
        product=_clean_text(product, 160),
        limit=8,
    )
    if measurement_matches:
        seen_sources = {str(row.get("source_id") or "") for row in source_matches if isinstance(row, dict)}
        for match in measurement_matches:
            source_id = f"{str(match.get('source_type') or '').strip().lower()}_db"
            if source_id in seen_sources:
                continue
            source_matches.extend(semantic_source_catalog.source_catalog_matches(
                str(match.get("source_type") or ""),
                limit=2,
            ))
            seen_sources = {str(row.get("source_id") or "") for row in source_matches if isinstance(row, dict)}
    ignored_values = [value for value in slot_hints.values() if isinstance(value, str)]
    ignored_values.extend(_string_list(slot_hints.get("snapshot_custom_cols"), limit=80))
    ignored_values.extend(str(match.get("value") or "") for match in catalog_matches if isinstance(match, dict))
    ignored_values.extend(str(match.get("term") or "") for match in measurement_matches if isinstance(match, dict))
    for match in measurement_matches:
        if isinstance(match, dict):
            ignored_values.extend(_string_list(match.get("aliases"), limit=20))
    unknown_term_texts = _unknown_terms(prompt_text, alias_groups, matched_norms, ignored_values)
    unknown_terms = _structured_unknown_terms(
        unknown_term_texts,
        columns=column_list,
        dtypes=dtypes,
        value_catalog_matches=catalog_matches,
        source_ref=source_ref,
        sample_profile=sample_profile,
    )
    hit_canonicals = {str(hit.get("canonical") or "") for hit in alias_hits}
    intent_matches = {
        intent: required
        for intent, required in (intent_hints or {}).items()
        if required and all(str(item) in hit_canonicals for item in required)
    }
    warnings: list[str] = []
    if unknown_term_texts:
        warnings.append("Unmapped semantic terms: " + ", ".join(unknown_term_texts[:8]))

    return {
        "natural_language": prompt_text,
        "resolved_columns": resolved_columns,
        "unknown_column_terms": unknown_column_terms,
        "value_terms": value_terms,
        "synonyms": _semantic_aliases(column_list, resolved_columns),
        "step_mapping": step_mapping,
        "alias_hits": alias_hits,
        "alias_group_meta": _alias_group_meta(alias_entries),
        "slot_hints": deepcopy(slot_hints),
        "unknown_terms": unknown_terms,
        "unknown_term_texts": unknown_term_texts,
        "value_catalog_matches": catalog_matches,
        "source_catalog_matches": source_matches,
        "measurement_term_matches": measurement_matches,
        "intent_matches": intent_matches,
        "warnings": warnings,
    }
