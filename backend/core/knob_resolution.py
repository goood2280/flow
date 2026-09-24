"""Observed KNOB names and user-confirmed alias preferences, scoped by product."""
from collections import Counter, deque
from difflib import SequenceMatcher
import hashlib
import json
import re

from core.paths import PATHS
from core.utils import jsonl_append


def norm(value):
    return re.sub(r"[\s_]+", " ", str(value or "")).strip().casefold()


def alias_from_prompt(product, text):
    clean = re.sub(re.escape(product), " ", str(text), flags=re.I)
    clean = re.sub(r"^\s*(?:이|해당|같은)\s*", "", clean)
    match = re.search(r"(.+?)\s*(?:split|스플릿|knob|노브|조건)(?=\s|으로|로|별|$)", clean, re.I)
    if match:
        clean = match[1]
    clean = re.sub(r"(?:으로|로)?\s*(?:컬러링|색상|색칠|분류|구분|color|group).*$", "", clean, flags=re.I)
    return norm(re.sub(r"^(?:KNOB|SPLIT)[_\s]+", "", clean, flags=re.I))[:160]


def _path(username):
    key = hashlib.sha256(str(username or "").encode()).hexdigest()[:32]
    return PATHS.data_root / "home_preferences" / f"knob_{key}.jsonl"


def counts(username, product, alias):
    path = _path(username)
    result = Counter()
    if not username or not alias or not path.is_file():
        return result
    with path.open(encoding="utf-8") as stream:
        for line in deque(stream, maxlen=2000):
            try:
                row = json.loads(line)
            except (ValueError, TypeError):
                continue
            if row.get("product", "").casefold() == product.casefold() and row.get("alias") == norm(alias):
                result[row.get("column", "")] += 1
    return result


def remember(username, product, alias, column, columns):
    if username and alias and column in columns and column.upper().startswith("KNOB_"):
        jsonl_append(_path(username), {"product": product, "alias": norm(alias), "column": column})


def resolve(product, text, columns, username=""):
    columns = [c for c in columns if c.upper().startswith("KNOB_")]
    alias = alias_from_prompt(product, text)
    compact = lambda value: re.sub(r"[\W_]+", "", norm(value))
    bare = lambda value: re.sub(r"^\d+(?:\.\d+)?\s*", "", norm(re.sub(r"^KNOB[_\s]+", "", value, flags=re.I)))
    exact = [c for c in columns if norm(c) == norm(text.strip()) or norm(re.sub(r"^KNOB[_\s]+", "", c, flags=re.I)) == alias or bare(c) == alias]
    learned = counts(username, product, alias)
    scored = []
    for col in columns:
        target, needle = compact(bare(col)), compact(alias)
        similarity = SequenceMatcher(None, needle, target).ratio() if needle else 0
        substring = bool(needle and needle in target)
        if col in exact or substring or similarity >= .5 or learned[col]:
            scored.append((col, 2 if col in exact else 1 if substring else similarity))
    scored.sort(key=lambda item: (-int(item[0] in exact), -learned[item[0]], -item[1], norm(item[0])))
    options = [{"value": col, "label": col + (f" · 이전 선택 {learned[col]}회" if learned[col] else ""), "selected_count": learned[col]}
               for col, _ in scored[:30]]
    return {"alias": alias, "exact": exact[0] if len(exact) == 1 else "", "options": options}
