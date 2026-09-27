"""Read-only views of the Product Wiki for search and the home chat.

Engineers register issues in the Product Wiki; these helpers turn the saved
records back into searchable text, ranked matches and readable change lines.
Nothing here writes to the Wiki or adds content: every value comes from a
saved record or its audit history.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
import re

from core import llm_prompt_budget as budget
from core import product_wiki as wiki

_BLOCK_TAGS = {"p", "div", "br", "li", "ul", "ol", "tr", "table", "thead", "tbody", "tfoot", "h1", "h2", "h3",
               "h4", "h5", "h6", "blockquote", "pre", "hr", "section", "article", "figure", "figcaption"}
_SKIP_TAGS = {"style", "script", "head", "title"}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0
        self.in_row = False

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self.skip += 1
        elif tag in {"td", "th"}:
            if self.in_row:
                self.parts.append(" | ")
            self.in_row = True
        elif tag == "img":
            self.parts.append(" [이미지] ")
        elif tag == "tr":
            self.parts.append("\n")
            self.in_row = False
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
        elif tag in {"tr", "table"}:
            self.parts.append("\n")
            self.in_row = False
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def plain_text(value) -> str:
    """Readable text of a saved (possibly HTML) issue body.

    Plain submissions are returned unchanged so verbatim grounding checks
    keep working on exactly what the engineer typed.
    """
    text = str(value or "")
    if "<" not in text and "&" not in text:
        return text
    parser = _TextExtractor()
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        return re.sub(r"<[^>]+>", " ", text)
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in "".join(parser.parts).splitlines()]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def short_id(entry_id) -> str:
    return str(entry_id or "")[:8]


def status_label(entry) -> str:
    status = str(entry.get("status") or "open")
    return wiki.STATUS_LABELS.get(status, status)


def kind_label(entry) -> str:
    kind = str(entry.get("kind") or "")
    return wiki.LABELS.get(kind, kind)


def entry_prose(entry) -> str:
    """The record's readable content: AI prose when present, else the original."""
    return plain_text(entry.get("body") or entry.get("source_text") or "")


def digest(entry, limit=160) -> str:
    text = str(entry.get("summary") or "").strip() or re.sub(r"\s+", " ", entry_prose(entry)).strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _conditions_text(entry) -> str:
    rows = entry.get("conditions") or []
    return "\n".join(" / ".join(str(row.get(key) or "") for key in ("condition", "purpose", "result"))
                     + " " + " ".join(row.get("lot_ids") or []) for row in rows if isinstance(row, dict))


def entry_text(entry) -> str:
    """Everything an engineer could search for in one record."""
    prose = entry_prose(entry)
    source = plain_text(entry.get("source_text") or "")
    parts = [entry.get("title"), entry.get("summary"), " ".join(entry.get("tags") or []), entry.get("structure"),
             entry.get("split"), " ".join(entry.get("lot_ids") or []), entry.get("purpose"),
             entry.get("expected_effect"), entry.get("observed_effect"), entry.get("evidence"),
             entry.get("occurred_on"), _conditions_text(entry), prose, source if source != prose else "",
             entry.get("author"), entry.get("updated_by"), status_label(entry), kind_label(entry)]
    return "\n".join(str(part) for part in parts if part)


# Words that only say what to do with the Wiki, not what to look for.
_GENERIC = {
    "위키", "wiki", "제품", "이슈", "issue", "issues", "기록", "기록들", "등록", "등록된", "내용", "관련", "대해", "대한",
    "대해서", "전체", "모든", "최근", "요즘", "현재", "지금", "목록", "리스트", "현황", "개요", "정리", "요약", "설명",
    "변경", "변경된", "변경사항", "바뀐", "이력", "히스토리", "상세", "자세히", "간단히", "정도", "무슨", "어떤", "뭐가",
    "무엇", "뭐야", "뭐였어", "뭐였지", "있어", "있었어", "있는지", "알려", "알려줘", "해줘", "해주세요", "줘", "좀",
    "보여줘", "보여", "찾아", "찾아줘", "검색", "검색해줘", "열린", "열려", "진행", "진행중", "미해결", "완료", "종료",
    "해결된", "오픈", "open", "closed", "summary", "summarize", "explain", "list", "please", "the", "of", "in",
    "for", "about", "what", "which", "show", "me", "all", "recent", "changes", "지난", "이번", "동안", "개월", "주간",
}
_INTENT_STEM = re.compile(r"^(?:요약|정리|설명|알려|보여|찾아|검색|말해|비교|분석|확인|파악|조회)")
_PERIOD = re.compile(r"^\d+(?:일|주|개월|달|시간)$")


def query_terms(text, product="", aliases=()) -> list[str]:
    names = {str(product or "").casefold(), *(str(a).casefold() for a in aliases or ())}
    names.discard("")
    terms = []
    for term in sorted(budget.search_terms(text)):
        if (term in _GENERIC or term in names or _INTENT_STEM.match(term) or _PERIOD.match(term)
                or re.fullmatch(r"[0-9a-f]{8}", term) or re.fullmatch(r"\d+번?", term)):
            continue
        terms.append(term)
    return terms


def mentioned_ids(text, entries) -> list[str]:
    """Record IDs typed in the question (8 hex chars, as shown in the Wiki)."""
    shorts = {short_id(e.get("id")).casefold(): e.get("id") for e in entries}
    found = []
    for token in re.findall(r"(?<![0-9A-Za-z])([0-9a-fA-F]{8})(?![0-9A-Za-z])", str(text or "")):
        full = shorts.get(token.casefold())
        if full and full not in found:
            found.append(full)
    return found


_ACTIVE_FIRST = {"open": 0, "investigating": 0, "validated": 1, "closed": 2}


def search(entries, terms, *, ids=()) -> list[dict]:
    """Rank records by the query terms: title > digest fields > body.

    Returns ``[{"entry", "score", "hits"}]``. Without terms (and IDs) every
    record is returned, open/investigating first, then by last update.
    """
    ids = list(ids or [])
    if not terms and not ids:
        ordered = sorted(entries, key=lambda e: str(e.get("updated_at") or ""), reverse=True)
        ordered.sort(key=lambda e: _ACTIVE_FIRST.get(str(e.get("status") or "open"), 3))
        return [{"entry": e, "score": 0, "hits": 0} for e in ordered]
    fields = []
    for entry in entries:
        strong = " ".join(str(part or "") for part in (
            entry.get("summary"), " ".join(entry.get("tags") or []), entry.get("structure"), entry.get("split"),
            " ".join(entry.get("lot_ids") or []))).casefold()
        fields.append((entry, str(entry.get("title") or "").casefold(), strong, entry_text(entry).casefold()))
    # A word found in most records ("불량", "결과") says little about which
    # record is meant; it still counts, at a quarter of the weight.
    common = {term for term in terms if len(fields) >= 3
              and sum(1 for _, _, _, rest in fields if term in rest) * 2 > len(fields)}
    ranked = []
    for entry, title, strong, rest in fields:
        score = hits = 0
        for term in terms:
            weight = 0.25 if term in common else 1.0
            if term in title:
                score += 3 * weight
            elif term in strong:
                score += 2 * weight
            elif term in rest:
                score += weight
            else:
                continue
            hits += 0 if term in common else 1
        if entry.get("id") in ids:
            score, hits = score + 100, hits + 1
        if score:
            ranked.append({"entry": entry, "score": score, "hits": hits})
    ranked.sort(key=lambda row: (row["score"], str(row["entry"].get("updated_at") or "")), reverse=True)
    if ranked:
        floor = max(1.0, ranked[0]["score"] * 0.35) if ranked[0]["score"] < 100 else 100
        ranked = [row for row in ranked if row["score"] >= floor]
    return ranked


def _parse_time(value):
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def updated_within(entry, days) -> bool:
    moment = _parse_time(entry.get("updated_at"))
    return bool(moment and moment >= datetime.now(timezone.utc) - timedelta(days=days))


def change_log(product, *, days=None, entry_ids=None, limit=30) -> list[dict]:
    """Readable audit lines (newest first) from the Wiki history."""
    changes = wiki.summarize_changes(wiki.history(product))
    if days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        changes = [c for c in changes if (moment := _parse_time(c["at"])) and moment >= cutoff]
    if entry_ids is not None:
        wanted = set(entry_ids)
        changes = [c for c in changes if c["entry_id"] in wanted]
    return changes[:limit]


def prompt_record(entry, *, text_limit=1500) -> dict:
    """What a model may read about one record: content fields, no audit IDs."""
    record = {"id": short_id(entry.get("id")), "title": entry.get("title") or "",
              "status": status_label(entry), "kind": kind_label(entry)}
    for key in ("structure", "split", "occurred_on", "summary", "purpose", "expected_effect",
                "observed_effect", "evidence"):
        if entry.get(key):
            record[key] = str(entry[key])[:1500]
    if entry.get("lot_ids"):
        record["lot_ids"] = list(entry["lot_ids"])[:30]
    if entry.get("tags"):
        record["tags"] = list(entry["tags"])[:10]
    if entry.get("conditions"):
        record["conditions"] = [{k: row.get(k) for k in ("condition", "purpose", "lot_ids", "result") if row.get(k)}
                                for row in entry["conditions"][:20] if isinstance(row, dict)]
    prose = entry_prose(entry)
    record["text"] = prose if len(prose) <= text_limit else prose[:text_limit] + "…(이하 생략)"
    record["author"] = entry.get("author") or ""
    record["created"] = str(entry.get("created_at") or "")[:10]
    record["updated"] = str(entry.get("updated_at") or "")[:10]
    record["updated_by"] = entry.get("updated_by") or ""
    return record
