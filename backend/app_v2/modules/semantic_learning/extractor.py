"""Candidate term extraction from meeting/inform/tracker/activity log inputs.

Pure regex-based extraction — no Polars/NLP dependencies. The intent is to
surface "new vocabulary" that admin can decide to fold into the lexicon, not
to do robust NER.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List

# Tokens we care about:
#   - Korean noun phrases: 2+ consecutive hangul characters
#   - English identifiers (incl. ML/PPID-like uppercase IDs): A-Z/a-z + digit + _
#   - Special prefix tokens (KNOB_, MTS_, PPID_, AA####, etc.) — caught by the
#     general identifier rule but listed here for the reader.
_TOKEN_PATTERN = re.compile(
    r"[A-Z][A-Z0-9_]{2,}"           # uppercase identifiers (KNOB_FOO, AA1234, MTS_X)
    r"|[A-Za-z][A-Za-z0-9_]{2,}"    # mixed-case identifiers (gpt_oss_120b)
    r"|[가-힣]{2,}",                # korean noun phrases (산화막, 인폼)
)

# Common short Korean stop-words we never want to learn as canonicals.
_HANGUL_STOPWORDS: set[str] = {
    "이거", "저거", "그거", "여기", "거기", "저기", "어디",
    "지금", "오늘", "어제", "내일", "이번", "다음", "이전",
    "있는", "없는", "있다", "없다", "되는", "되다", "하는", "하다",
    "확인", "수정", "삭제", "추가", "변경", "검토", "참고", "관련",
    "내용", "결과", "요청", "처리",
}


def _norm(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", str(value or "").lower())


def _filter_stop(tokens: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for raw in tokens:
        text = str(raw or "").strip()
        if not text:
            continue
        norm = _norm(text)
        if not norm:
            continue
        if norm.isdigit():
            continue
        if text in _HANGUL_STOPWORDS:
            continue
        if norm in seen:
            continue
        seen.add(norm)
        out.append(text)
    return out


def extract_terms(text: str) -> List[str]:
    """Return distinct candidate terms from a free-form text blob."""
    if not text:
        return []
    raw = _TOKEN_PATTERN.findall(str(text))
    return _filter_stop(raw)


def _join_fields(row: Dict[str, Any], keys: Iterable[str]) -> str:
    parts: List[str] = []
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            parts.extend(str(v) for v in value if v is not None)
        elif isinstance(value, dict):
            parts.append(json.dumps(value, ensure_ascii=False))
        else:
            parts.append(str(value))
    return " \n ".join(parts)


def extract_from_meeting(meeting_row: Dict[str, Any]) -> List[str]:
    """Pull candidate terms from meeting title/agendas/minutes/decisions/action items."""
    if not isinstance(meeting_row, dict):
        return []
    text_parts: List[str] = [str(meeting_row.get("title") or ""), str(meeting_row.get("summary") or "")]
    for key in ("agendas", "minutes", "decisions", "action_items"):
        value = meeting_row.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    text_parts.append(_join_fields(item, ("title", "text", "body", "content", "note", "owner")))
                else:
                    text_parts.append(str(item or ""))
        elif isinstance(value, str):
            text_parts.append(value)
    return extract_terms("\n".join(text_parts))


def extract_from_inform(inform_row: Dict[str, Any]) -> List[str]:
    if not isinstance(inform_row, dict):
        return []
    text = _join_fields(inform_row, ("module", "reason", "message", "title", "subject"))
    return extract_terms(text)


def extract_from_tracker_comment(comment_row: Dict[str, Any]) -> List[str]:
    if not isinstance(comment_row, dict):
        return []
    text = _join_fields(comment_row, ("title", "body", "content", "comment", "text"))
    return extract_terms(text)


def extract_from_activity_log(jsonl_path: Path, *, since_iso: str | None = None) -> List[str]:
    """Return unresolved-coverage tokens from a flowi_activity.jsonl tail.

    Each entry is best-effort — malformed lines are skipped silently. Only
    entries that look like Flow-i traces with `coverage<0.35` warning or with
    unresolved tokens contribute.
    """
    path = Path(jsonl_path)
    if not path.exists() or not path.is_file():
        return []
    out: List[str] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if since_iso and str(entry.get("timestamp") or entry.get("ts") or "") < str(since_iso):
                    continue
                trace = entry.get("trace") or entry.get("flow_trace") or {}
                if not isinstance(trace, dict):
                    continue
                semantic = trace.get("semantic") if isinstance(trace.get("semantic"), dict) else {}
                coverage = semantic.get("coverage")
                warnings = semantic.get("warnings") or []
                low_coverage = isinstance(coverage, (int, float)) and float(coverage) < 0.35
                low_warn = any("coverage" in str(w).lower() for w in warnings)
                if not (low_coverage or low_warn):
                    continue
                tokens = semantic.get("tokens")
                if not isinstance(tokens, list):
                    continue
                normalized = semantic.get("normalized_terms")
                resolved_keys = set()
                if isinstance(normalized, dict):
                    resolved_keys = {str(k) for k in normalized.keys()}
                for token in tokens:
                    token_text = str(token or "").strip()
                    if not token_text:
                        continue
                    if token_text in resolved_keys:
                        continue
                    out.append(token_text)
    except OSError:
        return []
    return _filter_stop(out)
