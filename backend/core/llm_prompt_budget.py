"""Relevance-first character budgets for LLM reference context.

The home agent sends admin knowledge, the product Wiki and the 3D structure
model as advisory context. Sending every section and every measurement made a
single planning prompt 20k-80k characters, which the on-premise Gemma4 has to
prefill on every turn: slow first tokens, timeouts that open the LLM circuit
breaker, and context-length rejections. Here each reference list keeps the
items that mention the question first and stops at a character budget, and
reports how much was left out so the model does not assume completeness.

Budgets are characters of compact JSON (a stable proxy for tokens in mixed
Korean/English text) and can be tuned with FLOW_LLM_CONTEXT_SCALE.
"""
from __future__ import annotations

import json
import os
import re

# Common Korean particles/endings glued to nouns ("두께가", "영향은", "CD랑").
_PARTICLES = re.compile(
    r"(?:으로써|으로서|에서는|에서도|이라고|라고|이랑|으로|에서|에게|까지|부터|처럼|보다|하고|"
    r"은|는|이|가|을|를|의|에|와|과|로|랑|도|만|요)$")
_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_.+-]*|[0-9]+(?:\.[0-9]+)?[A-Za-z_]*|[가-힣]+")
_STOP = {"그리고", "알려줘", "보여줘", "그려줘", "해줘", "관련", "어떻게", "무엇", "뭐야", "있어", "차트", "조회"}


def scale() -> float:
    try:
        return max(0.25, min(4.0, float(os.environ.get("FLOW_LLM_CONTEXT_SCALE", "1") or 1)))
    except ValueError:
        return 1.0


def budget(chars: int) -> int:
    return int(chars * scale())


def search_terms(text: str) -> set[str]:
    """Case-folded terms with Korean particles removed; single letters dropped."""
    terms: set[str] = set()
    for token in _TOKEN.findall(str(text or "")):
        folded = token.casefold()
        if re.fullmatch(r"[가-힣]+", token):
            stripped = _PARTICLES.sub("", token)
            if len(stripped) >= 2:
                folded = stripped
        if len(folded) >= 2 and folded not in _STOP:
            terms.add(folded)
    return terms


def relevance(value, terms: set[str]) -> int:
    if not terms:
        return 0
    haystack = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    haystack = haystack.casefold()
    return sum(1 for term in terms if term in haystack)


def compact_size(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str))


def fit(items, max_chars: int, *, terms: set[str] | None = None, text_of=None,
        relevant_only: bool = False, keep_order: bool = False):
    """Keep the most relevant items whose compact JSON fits in ``max_chars``.

    Returns ``(kept, info)`` where info carries total/kept/omitted counts.
    Ties keep the original order, so callers that already rank items keep
    their ranking. ``keep_order`` restores the source order for the output.
    """
    items = list(items or [])
    terms = terms or set()
    scored = [(relevance(text_of(item) if text_of else item, terms), index, item)
              for index, item in enumerate(items)]
    if terms:
        scored.sort(key=lambda row: (-row[0], row[1]))
    kept, size = [], 2
    for score, index, item in scored:
        if relevant_only and terms and score == 0:
            continue
        cost = compact_size(item) + 1
        if size + cost > max_chars:
            continue
        kept.append((index, item))
        size += cost
    if keep_order:
        kept.sort(key=lambda row: row[0])
    out = [item for _, item in kept]
    return out, {"total": len(items), "kept": len(out), "omitted": len(items) - len(out)}


def dumps(value) -> str:
    """Compact JSON for prompts (about 8% fewer characters than the default)."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
