from __future__ import annotations

import re
from typing import Any

import polars as pl

from core.flowi_units import UNIT_AIS
from core.paths import PATHS
from core.utils import load_json

from .schemas import SemanticCandidate, SemanticFrame


SCHEMA_RELATION_FILE = PATHS.data_root / "schema_relations.json"


_ALIAS_GROUPS: dict[str, list[str]] = {
    "filebrowser": ["filebrowser", "file browser", "파일탐색기", "파일 탐색기", "파일", "parquet", "csv", "raw data", "원본"],
    "ai_sql": ["ai sql", "sql", "쿼리", "필터", "정렬", "aggregate", "집계"],
    "semantic_layer": ["semantic", "semantic layer", "시멘틱", "시멘틱레이어", "단어", "용어", "의미"],
    "langgraph": ["langgraph", "랭그래프", "그래프", "orchestration", "오케스트레이션"],
    "langsmith": ["langsmith", "랭스미스", "trace", "tracing", "추적", "개선"],
    "root_lot_id": ["root_lot_id", "root lot", "root lot id", "rootlot", "루트랏", "루트 lot", "루트"],
    "fab_lot_id": ["fab_lot_id", "fab lot", "lot id", "lot_id", "랏", "lot"],
    "wafer_id": ["wafer_id", "wafer", "wf", "웨이퍼"],
    "step_id": ["step_id", "step", "스텝", "공정", "공정스텝"],
    "function_step": ["function_step", "function step", "기능공정", "모듈공정"],
    "product": ["product", "제품", "제품명"],
    "knob": ["knob", "split", "ppid", "놉", "스플릿", "분기"],
    "inform": ["inform", "인폼", "메일", "mail", "알림"],
    "meeting": ["meeting", "회의", "회의록"],
    "tracker": ["tracker", "issue", "이슈", "트래커"],
    "chart": ["chart", "trend", "scatter", "차트", "트렌드", "그래프"],
}


_INTENT_HINTS: dict[str, list[str]] = {
    "filebrowser_ai_sql": ["ai_sql", "filebrowser"],
    "semantic_inspection": ["semantic_layer", "schema", "wiki"],
    "traceable_orchestration": ["langgraph", "langsmith"],
    "inform_draft": ["inform"],
    "meeting_recall": ["meeting"],
    "tracker_lookup": ["tracker"],
    "chart_analysis": ["chart"],
    "knob_analysis": ["knob"],
}


def _norm(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", str(value or "").lower())


def _tokens(goal: str, max_terms: int) -> list[str]:
    raw = re.findall(r"#[0-9]+|[A-Za-z][A-Za-z0-9_.-]*|\d+(?:\.\d+)?|[가-힣]{2,}", goal or "")
    out: list[str] = []
    for token in raw:
        text = str(token or "").strip()
        if text and text not in out:
            out.append(text)
        if len(out) >= max_terms:
            break
    return out


def _normalized_terms(goal: str, tokens: list[str]) -> dict[str, str]:
    goal_norm = _norm(goal)
    terms: dict[str, str] = {}
    for token in tokens:
        t_norm = _norm(token)
        for canonical, aliases in _ALIAS_GROUPS.items():
            alias_norms = [_norm(x) for x in aliases]
            if t_norm == _norm(canonical) or t_norm in alias_norms:
                terms[token] = canonical
                break
        if token in terms:
            continue
        for canonical, aliases in _ALIAS_GROUPS.items():
            if any(alias and alias in goal_norm for alias in [_norm(x) for x in aliases if len(_norm(x)) >= 3]):
                if t_norm and any(t_norm in _norm(x) or _norm(x) in t_norm for x in aliases):
                    terms[token] = canonical
                    break
    for canonical, aliases in _ALIAS_GROUPS.items():
        if canonical in terms.values():
            continue
        if any(alias and alias in goal_norm for alias in [_norm(x) for x in aliases if len(_norm(x)) >= 4]):
            terms[canonical] = canonical
    return terms


def _schema_payload() -> dict[str, Any]:
    data = load_json(SCHEMA_RELATION_FILE, {"relations": [], "column_catalog": []})
    return data if isinstance(data, dict) else {"relations": [], "column_catalog": []}


def _catalog_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in _schema_payload().get("column_catalog") or []:
        if not isinstance(item, dict):
            continue
        column = str(item.get("column") or "").strip()
        if not column:
            continue
        rows.append({
            "relation_id": str(item.get("relation_id") or ""),
            "column": column,
            "canonical_alias": str(item.get("canonical_alias") or column),
            "raw_names": item.get("raw_names") if isinstance(item.get("raw_names"), list) else [],
            "meaning": str(item.get("meaning") or item.get("summary") or ""),
            "source": "schema_relations.column_catalog",
            "unit": str(item.get("unit") or ""),
            "sample_values": item.get("sample_values") if isinstance(item.get("sample_values"), list) else [],
            "used_by": [],
            "wiki_doc_id": str(item.get("wiki_doc_id") or ""),
        })
    for unit in UNIT_AIS.values():
        unit_key = unit.key()
        for ds in unit.data_sources():
            relation_id = str(ds.path or unit_key)
            for col in ds.columns:
                column = str(col.name or "").strip()
                if not column:
                    continue
                rows.append({
                    "relation_id": relation_id,
                    "column": column,
                    "canonical_alias": column,
                    "raw_names": [column],
                    "meaning": str(col.meaning or ""),
                    "source": "unit_ai.column_doc",
                    "unit": str(col.unit or ""),
                    "sample_values": list(col.sample_values or []),
                    "used_by": [unit_key],
                    "wiki_doc_id": str(col.wiki_doc_id or ""),
                })
    return rows


def _score_row(token: str, normalized: str, row: dict[str, Any]) -> float:
    token_norm = _norm(token)
    canonical_norm = _norm(normalized)
    column_norm = _norm(row.get("column"))
    alias_norm = _norm(row.get("canonical_alias"))
    raw_norms = [_norm(x) for x in row.get("raw_names") or []]
    sample_norms = [_norm(x) for x in row.get("sample_values") or []]
    meaning_norm = _norm(row.get("meaning"))
    if not token_norm and not canonical_norm:
        return 0.0
    needles = [x for x in [token_norm, canonical_norm] if x]
    if any(n and n in {column_norm, alias_norm, *raw_norms} for n in needles):
        return 1.0 if token_norm in {column_norm, alias_norm, *raw_norms} else 0.94
    if any(n and (n in column_norm or n in alias_norm) for n in needles if len(n) >= 3):
        return 0.82
    if any(n and any(n in raw or raw in n for raw in raw_norms if len(raw) >= 3) for n in needles if len(n) >= 3):
        return 0.78
    if any(n and any(n in sample for sample in sample_norms) for n in needles if len(n) >= 2):
        return 0.72
    if any(n and n in meaning_norm for n in needles if len(n) >= 3):
        return 0.62
    return 0.0


def _slot_extract(goal: str) -> dict[str, Any]:
    text = goal or ""
    upper = text.upper()
    products = sorted(set(re.findall(r"\bPROD[A-Z0-9_]*\b", upper)))
    products.extend([m for m in re.findall(r"\bML_TABLE_([A-Z0-9_]+)\b", upper) if m not in products])
    lots = sorted(set(re.findall(r"\b[A-Z]\d{4,}[A-Z]?(?:\.\d+)?\b", upper)))
    fab_lots = [lot for lot in lots if "." in lot]
    root_lots = sorted(set([lot.split(".", 1)[0] for lot in lots]))
    wafers = sorted(set(int(m) for m in re.findall(r"(?:#|WF|WAFER\s*)(\d{1,2})\b", upper)))
    steps = sorted(set(re.findall(r"\b\d{1,3}\.\d+\s*[A-Z]{2,}\b|\bAA\d{4,}\b", upper)))
    knobs = sorted(set(re.findall(r"\b(?:KNOB|PPID)[A-Z0-9_.-]*\b", upper)))
    return {
        "products": products,
        "root_lot_ids": root_lots,
        "fab_lot_ids": fab_lots,
        "wafer_ids": wafers,
        "steps": steps,
        "knobs": knobs,
    }


def _infer_intent(normalized_terms: dict[str, str], goal: str) -> str:
    terms = set(normalized_terms.values())
    goal_norm = _norm(goal)
    if "schema" in goal_norm or "wiki" in goal_norm or "지식" in goal:
        terms.add("wiki")
        terms.add("schema")
    best = "general_orchestration"
    best_score = 0
    for intent, hints in _INTENT_HINTS.items():
        score = sum(1 for hint in hints if hint in terms)
        if score > best_score:
            best = intent
            best_score = score
    return best


def _alias_candidate(token: str, normalized: str) -> SemanticCandidate | None:
    if not normalized:
        return None
    column_like = normalized if normalized in {
        "root_lot_id", "fab_lot_id", "wafer_id", "step_id", "function_step", "product", "knob"
    } else ""
    return SemanticCandidate(
        token=token,
        normalized=normalized,
        column=column_like,
        canonical_alias=normalized,
        meaning="semantic alias",
        source="runtime_alias",
        score=0.45,
    )


def resolve_semantic_frame(goal: str, *, max_terms: int = 24) -> SemanticFrame:
    tokens = _tokens(goal, max_terms)
    normalized_terms = _normalized_terms(goal, tokens)
    rows = _catalog_rows()
    scored: list[dict[str, Any]] = []
    for token in tokens + [x for x in normalized_terms.values() if x not in tokens]:
        normalized = normalized_terms.get(token, token)
        for row in rows:
            score = _score_row(token, normalized, row)
            if score <= 0:
                continue
            scored.append({**row, "token": token, "normalized": normalized, "score": score})
    candidates: list[SemanticCandidate] = []
    profile: dict[str, Any] = {"catalog_rows": len(rows), "candidate_count": 0, "by_source": []}
    if scored:
        df = pl.DataFrame(scored)
        df = df.sort(["score", "source", "relation_id", "column"], descending=[True, False, False, False])
        df = df.unique(subset=["token", "normalized", "relation_id", "column", "source"], keep="first")
        limited = df.head(max(12, max_terms * 3))
        candidates = [SemanticCandidate(**row) for row in limited.to_dicts()]
        profile["candidate_count"] = len(candidates)
        profile["by_source"] = (
            df.group_by("source")
            .len()
            .sort("len", descending=True)
            .rename({"len": "count"})
            .to_dicts()
        )
    seen_candidate_tokens = {c.token for c in candidates}
    for token, normalized in normalized_terms.items():
        if token not in seen_candidate_tokens:
            fallback = _alias_candidate(token, normalized)
            if fallback:
                candidates.append(fallback)
    slots = _slot_extract(goal)
    resolved_tokens = {c.token for c in candidates}
    for key, value in slots.items():
        if value:
            resolved_tokens.add(key)
    denominator = max(1, len(tokens))
    coverage = min(1.0, len(resolved_tokens.intersection(set(tokens))) / denominator)
    warnings: list[str] = []
    if not rows:
        warnings.append("column catalog is empty; semantic layer is using aliases only")
    if coverage < 0.35 and tokens:
        warnings.append("low semantic coverage; add schema column docs or wiki aliases")
    return SemanticFrame(
        goal=goal,
        tokens=tokens,
        normalized_terms=normalized_terms,
        intent=_infer_intent(normalized_terms, goal),
        slots=slots,
        candidates=sorted(candidates, key=lambda c: c.score, reverse=True)[: max(12, max_terms * 3)],
        coverage=round(coverage, 3),
        warnings=warnings,
        polars_profile=profile,
    )
