"""core/knowledge_cards.py — Flow-i 단일 지식 레이어 (지식 카드 canonical).

흩어져 있던 지식 저장소(fewshots/file_docs/wiki/source_catalog)를 "카드" 하나의
포맷으로 수렴시키는 읽기 레이어. 카드는 3종 + 어댑터로 구성된다:

  - seed:      backend/core/knowledge_seed_cards/*.md
               코드 트리에 있으므로 setup.py 번들로 사내에 그대로 배포된다.
               구조 지식("split 규칙은 ppid_knob.csv를 본다") 전용 — 값 무관이라
               집에서 선주입해도 사내에서 100% 유효하다.
  - generated: 런타임에 로컬 룰북(ppid_knob.csv)에서 결정적으로 생성.
               환경(집/사내)이 바뀌면 그 환경의 CSV 기준으로 자동 재생성된다.
  - local:     PATHS.data_root/knowledge/cards_local/*.md
               사내에서 티칭/교정으로 쌓이는 카드. data_root라 재배포에도 보존.
  - adapter:   기존 flowi_file_docs / flowi_fewshots 항목을 카드 뷰로 노출
               (읽기 전용 — 마이그레이션 없이 조회 시점에 통일).

병합 우선순위: local > seed > generated > adapter (같은 term 기준).

소비 지점 (routers/llm.py):
  - resolve(prompt)      → 매칭 카드 목록 (unit 힌트/출처 포함)
  - reorder_units(...)   → 카드 answered_by 힌트로 unit dispatch 순서 조정
  - prompt_block(prompt) → GPT OSS 등 LLM 프롬프트에 넣는 컴팩트 지식 블록
  - teach_card(...)      → cards_local 에 카드 저장 (HITL)

카드 파일 포맷 — 마크다운 + 미니 frontmatter (yaml 의존 없음):

    ---
    term: 3.0 VTN
    kind: split-knob
    aliases: [VTN, KNOB_3.0_VTN]
    trigger_terms: [split 구성, 규칙]
    answered_by: ppid_knob
    sources:
      - file: ppid_knob.csv
        role: rulebook
    related: [ppid-knob-rulebook]
    status: active
    ---
    본문 (자유 마크다운)
"""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.paths import PATHS

SEED_DIR = Path(__file__).with_name("knowledge_seed_cards")
LOCAL_DIR = PATHS.data_root / "knowledge" / "cards_local"

_CACHE_TTL_S = 20.0
_MAX_LOCAL_CARDS = 2000
_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {"built_at": 0.0, "sig": None, "cards": []}

_SLUG_RE = re.compile(r"[^a-z0-9가-힣]+")
_LIST_FIELDS = {"aliases", "trigger_terms", "related"}
_SCALAR_FIELDS = {"term", "kind", "answered_by", "status", "updated_by", "updated_at"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── frontmatter (미니 파서 — yaml 미의존) ─────────────────────────────────────
def _parse_inline_list(raw: str) -> list[str]:
    body = raw.strip()
    if body.startswith("[") and body.endswith("]"):
        body = body[1:-1]
    return [p.strip().strip("'\"") for p in body.split(",") if p.strip().strip("'\"")]


def parse_card_text(text: str, *, origin: str = "seed", path: str = "") -> dict[str, Any] | None:
    """카드 마크다운 → dict. frontmatter 가 없거나 term 이 없으면 None."""
    raw = str(text or "")
    m = re.match(r"\s*---\s*\n(.*?)\n\s*---\s*\n?", raw, flags=re.DOTALL)
    if not m:
        return None
    body = raw[m.end():].strip()
    card: dict[str, Any] = {
        "term": "", "kind": "", "aliases": [], "trigger_terms": [],
        "answered_by": "", "sources": [], "related": [], "status": "active",
        "body": body, "origin": origin, "path": path,
    }
    lines = m.group(1).splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        km = re.match(r"^(\w[\w_]*)\s*:\s*(.*)$", line.strip())
        if not km or line[:1] in (" ", "\t", "-"):
            i += 1
            continue
        key, value = km.group(1), km.group(2).strip()
        if key == "sources":
            i += 1
            entry: dict[str, str] = {}
            while i < len(lines):
                sub = lines[i]
                if not sub.strip():
                    i += 1
                    continue
                if not sub.startswith((" ", "\t")):
                    break
                sm = re.match(r"^\s*-\s*(\w[\w_]*)\s*:\s*(.*)$", sub)
                cm = re.match(r"^\s+(\w[\w_]*)\s*:\s*(.*)$", sub)
                if sm:
                    if entry:
                        card["sources"].append(entry)
                    entry = {sm.group(1): sm.group(2).strip().strip("'\"")}
                elif cm:
                    entry[cm.group(1)] = cm.group(2).strip().strip("'\"")
                else:
                    break
                i += 1
            if entry:
                card["sources"].append(entry)
            continue
        if key in _LIST_FIELDS:
            if value:
                card[key] = _parse_inline_list(value)
            else:
                items: list[str] = []
                i += 1
                while i < len(lines):
                    sub = lines[i]
                    im = re.match(r"^\s*-\s*(.+)$", sub)
                    if not (sub.startswith((" ", "\t")) and im):
                        break
                    items.append(im.group(1).strip().strip("'\""))
                    i += 1
                card[key] = items
                continue
        elif key in _SCALAR_FIELDS:
            card[key] = value.strip("'\"")
        i += 1
    if not str(card.get("term") or "").strip():
        return None
    card["term"] = str(card["term"]).strip()
    return card


def render_card_text(card: dict[str, Any]) -> str:
    """dict → 카드 마크다운 (teach_card 저장용)."""
    out = ["---", f"term: {card.get('term', '')}"]
    for key in ("kind", "answered_by", "status", "updated_by", "updated_at"):
        if card.get(key):
            out.append(f"{key}: {card[key]}")
    for key in sorted(_LIST_FIELDS):
        values = [str(v) for v in (card.get(key) or []) if str(v).strip()]
        if values:
            out.append(f"{key}: [{', '.join(values)}]")
    sources = [s for s in (card.get("sources") or []) if isinstance(s, dict) and s]
    if sources:
        out.append("sources:")
        for src in sources:
            items = [(k, str(v)) for k, v in src.items() if str(v).strip()]
            if not items:
                continue
            first_k, first_v = items[0]
            out.append(f"  - {first_k}: {first_v}")
            out.extend(f"    {k}: {v}" for k, v in items[1:])
    out.append("---")
    out.append(str(card.get("body") or "").strip())
    return "\n".join(out) + "\n"


# ── 카드 로딩 ────────────────────────────────────────────────────────────────
def _read_dir_cards(root: Path, origin: str) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    try:
        files = sorted(root.glob("*.md"))
    except Exception:
        return cards
    for fp in files:
        try:
            card = parse_card_text(fp.read_text(encoding="utf-8"), origin=origin, path=fp.name)
        except Exception:
            card = None
        if card:
            cards.append(card)
    return cards


def _generated_cards(rows: list[dict[str, str]] | None = None) -> list[dict[str, Any]]:
    """생성 지식 — 로컬 ppid_knob.csv 룰북에서 feature(=split/knob)별 카드를 만든다.

    사내로 이동하면 사내 CSV 기준으로 자동 재생성되므로 값 수준 지식의
    이식성 문제가 없다. 실패는 조용히 빈 목록 (룰북이 없는 환경 허용).
    """
    try:
        from core import fab_reference
        rows = fab_reference._read_rows(fab_reference.PPID_KNOB_FILE) if rows is None else rows
    except Exception:
        return []
    if not rows:
        return []
    by_feature: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        feat = str(row.get("feature_name") or "").strip()
        if feat:
            by_feature.setdefault(feat, []).append(row)
    cards: list[dict[str, Any]] = []
    for feat, grp in list(by_feature.items())[:400]:
        steps = sorted({str(r.get("function_step") or "").strip() for r in grp} - {""})
        categories = sorted({str(r.get("category") or "").strip() for r in grp} - {""})
        lines = [
            f"'{feat}' split 은 ppid_knob.csv 룰북 기준 규칙 {len(grp)}건으로 구성된다.",
            f"분류 카테고리(knob): {', '.join(categories[:12]) or '(미설정)'}",
        ]
        if steps:
            lines.append(f"적용 function_step: {', '.join(steps[:8])}")
        lines.append("규칙 상세/wafer 배정은 ppid_knob 유닛과 ML_TABLE KNOB_* 컬럼으로 조회한다.")
        cards.append({
            "term": feat,
            "kind": "split-knob",
            "aliases": steps,
            "trigger_terms": [],
            "answered_by": "ppid_knob",
            "sources": [{"file": "ppid_knob.csv", "role": "rulebook"}],
            "related": ["ppid-knob-rulebook"],
            "status": "active",
            "body": "\n".join(lines),
            "origin": "generated",
            "path": "",
        })
    return cards


def _adapter_cards() -> list[dict[str, Any]]:
    """기존 저장소(file_docs / fewshots)를 카드 뷰로 노출 — 마이그레이션 없는 통일."""
    cards: list[dict[str, Any]] = []
    try:
        from core import flowi_file_docs
        for entry in flowi_file_docs.list_docs()[:200]:
            fname = str(entry.get("file") or "").strip()
            desc = str(entry.get("description") or "").strip()
            if fname and desc:
                cards.append({
                    "term": fname, "kind": "file-doc", "aliases": [], "trigger_terms": [],
                    "answered_by": "", "sources": [{"file": fname}], "related": [],
                    "status": "active", "body": desc, "origin": "adapter", "path": "",
                })
    except Exception:
        pass
    try:
        from core import flowi_fewshots
        for entry in flowi_fewshots.list_entries()[:400]:
            term = str(entry.get("term") or "").strip()
            answer = str(entry.get("answer") or "").strip()
            if term and answer:
                cards.append({
                    "term": term, "kind": "taught-term", "aliases": [], "trigger_terms": [],
                    "answered_by": "", "sources": [], "related": [],
                    "status": "active", "body": answer, "origin": "adapter", "path": "",
                })
    except Exception:
        pass
    return cards


_ORIGIN_RANK = {"local": 0, "seed": 1, "generated": 2, "adapter": 3}


def _dir_sig(root: Path) -> tuple:
    try:
        return tuple(sorted((p.name, p.stat().st_mtime_ns) for p in root.glob("*.md")))
    except Exception:
        return ()


def load_cards(*, force: bool = False) -> list[dict[str, Any]]:
    """전체 카드 (병합 후). 같은 term(casefold)이면 local > seed > generated > adapter."""
    sig = (_dir_sig(SEED_DIR), _dir_sig(LOCAL_DIR))
    with _LOCK:
        fresh = (time.monotonic() - _CACHE["built_at"]) < _CACHE_TTL_S
        if not force and fresh and _CACHE["sig"] == sig:
            return list(_CACHE["cards"])
    merged: dict[str, dict[str, Any]] = {}
    pools = (
        _read_dir_cards(LOCAL_DIR, "local")
        + _read_dir_cards(SEED_DIR, "seed")
        + _generated_cards()
        + _adapter_cards()
    )
    for card in pools:
        key = card["term"].casefold()
        prev = merged.get(key)
        if prev is None or _ORIGIN_RANK.get(card["origin"], 9) < _ORIGIN_RANK.get(prev["origin"], 9):
            merged[key] = card
    cards = list(merged.values())
    with _LOCK:
        _CACHE.update({"built_at": time.monotonic(), "sig": sig, "cards": list(cards)})
    return cards


# ── 조회 ────────────────────────────────────────────────────────────────────
def _term_in_text(term: str, text_upper: str, raw_text: str) -> bool:
    tok = str(term or "").strip()
    if len(tok) < 2:
        return False
    if re.search(r"[가-힣]", tok):
        return tok.casefold() in raw_text.casefold()
    pat = r"(?<![A-Z0-9_])" + re.escape(tok.upper()).replace(r"\ ", r"[\s_]*") + r"(?![A-Z0-9_])"
    return re.search(pat, text_upper) is not None


def resolve(prompt: str, *, limit: int = 6) -> list[dict[str, Any]]:
    """prompt 에 등장하는 카드(용어/별칭/트리거)를 찾는다.

    반환 카드에는 matched(무엇으로 매칭됐는지)가 붙는다. 정렬: 매칭 토큰이
    긴 것(구체적) → origin 우선순위.
    """
    raw = str(prompt or "")
    if not raw.strip():
        return []
    up = raw.upper()
    hits: list[tuple[int, int, dict[str, Any]]] = []
    for card in load_cards():
        if str(card.get("status") or "active") in {"disabled", "todo", "draft"}:
            continue  # todo=빈 틀, draft=AI 초안(승인 전) — 조회/프롬프트에 노출하지 않음
        matched = ""
        for tok in [card["term"], *card.get("aliases", []), *card.get("trigger_terms", [])]:
            if _term_in_text(tok, up, raw):
                matched = str(tok)
                break
        if not matched:
            continue
        hits.append((-len(matched), _ORIGIN_RANK.get(card["origin"], 9), {**card, "matched": matched}))
    hits.sort(key=lambda h: (h[0], h[1]))
    return [h[2] for h in hits[: max(1, min(limit, 12))]]


def unit_hints(prompt: str) -> list[str]:
    """카드 answered_by → unit key 목록 (등장 순서 유지, 중복 제거)."""
    out: list[str] = []
    for card in resolve(prompt):
        unit = str(card.get("answered_by") or "").strip()
        if unit and unit not in out:
            out.append(unit)
    return out


def reorder_units(prompt: str, units: list[str]) -> list[str]:
    """지식 기반 unit dispatch 순서 조정 — 카드가 지목한 유닛을 앞으로 (안정 정렬).

    카드에 없는 유닛의 상대 순서는 유지되므로, 힌트가 없으면 완전 무변경.
    """
    hints = [u for u in unit_hints(prompt) if u in units]
    if not hints:
        return list(units)
    return hints + [u for u in units if u not in hints]


def knowledge_sources(cards: list[dict[str, Any]]) -> list[str]:
    """카드 목록 → 답변 출처 표기용 파일 목록."""
    out: list[str] = []
    for card in cards:
        for src in card.get("sources") or []:
            label = str((src or {}).get("file") or "").strip()
            if label and label not in out:
                out.append(label)
    return out


def prompt_block(prompt: str, *, max_chars: int = 1600) -> str:
    """LLM(GPT OSS 등) 프롬프트 증강용 컴팩트 지식 블록. 매칭 없으면 빈 문자열."""
    cards = resolve(prompt)
    if not cards:
        return ""
    lines = ["[도메인 지식 카드 — 아래 사실을 우선 신뢰하고, 출처 파일을 근거로 답하라]"]
    for card in cards:
        body = re.sub(r"\s+", " ", str(card.get("body") or "")).strip()
        srcs = ", ".join(knowledge_sources([card])[:3])
        unit = str(card.get("answered_by") or "").strip()
        line = f"- {card['term']}"
        if card.get("kind"):
            line += f" ({card['kind']})"
        line += f": {body[:400]}"
        if srcs:
            line += f" [출처: {srcs}]"
        if unit:
            line += f" [담당 유닛: {unit}]"
        lines.append(line)
        if sum(len(x) for x in lines) > max_chars:
            break
    return "\n".join(lines)[: max_chars + 200]


# ── 티칭 (HITL 쓰기) ─────────────────────────────────────────────────────────
def _slug(term: str) -> str:
    slug = _SLUG_RE.sub("-", str(term or "").strip().casefold()).strip("-")
    return slug[:80] or "card"


def teach_card(
    term: str,
    body: str,
    *,
    by: str = "",
    kind: str = "taught",
    answered_by: str = "",
    sources: list[dict[str, str]] | None = None,
    aliases: list[str] | None = None,
    trigger_terms: list[str] | None = None,
    related: list[str] | None = None,
    status: str = "active",
) -> dict[str, Any] | None:
    """cards_local 에 카드를 저장(같은 term 이면 덮어씀 — local 이 최우선이므로
    시드 교정도 이 함수 하나로 된다). 성공 시 저장된 카드 dict.

    trigger_terms/related 는 시드 카드를 재작성(승인/답변/채움)할 때 원본 값을
    그대로 넘겨 소실을 막아야 한다 — local>seed 병합이라 빠뜨리면 영구히 가려진다."""
    term = str(term or "").strip()
    body = str(body or "").strip()
    if not term or not body or len(term) > 120:
        return None
    card = {
        "term": term, "kind": kind, "aliases": list(aliases or []),
        "trigger_terms": list(trigger_terms or []),
        "answered_by": answered_by, "sources": list(sources or []),
        "related": list(related or []),
        "status": status if status in {"active", "todo", "draft", "disabled"} else "active",
        "body": body[:4000], "origin": "local",
        "updated_by": by, "updated_at": _now(),
    }
    try:
        LOCAL_DIR.mkdir(parents=True, exist_ok=True)
        existing = list(LOCAL_DIR.glob("*.md"))
        if len(existing) >= _MAX_LOCAL_CARDS:
            return None
        path = LOCAL_DIR / f"{_slug(term)}.md"
        path.write_text(render_card_text(card), encoding="utf-8")
        card["path"] = path.name
    except Exception:
        return None
    with _LOCK:
        _CACHE["built_at"] = 0.0  # 다음 조회에서 재빌드
    return card


def forget_card(term: str) -> bool:
    """cards_local 에서 카드 삭제 (시드/생성 카드는 지우지 않는다)."""
    try:
        path = LOCAL_DIR / f"{_slug(term)}.md"
        if not path.is_file():
            return False
        path.unlink()
    except Exception:
        return False
    with _LOCK:
        _CACHE["built_at"] = 0.0
    return True


# ── AI 채움 (관리자 HITL: 채움 → draft → 승인 → active) ─────────────────────
def cards_by_status(status: str) -> list[dict[str, Any]]:
    return [c for c in load_cards() if str(c.get("status") or "active") == status]


def status_summary() -> dict[str, Any]:
    counts: dict[str, int] = {}
    origins: dict[str, int] = {}
    for card in load_cards():
        counts[str(card.get("status") or "active")] = counts.get(str(card.get("status") or "active"), 0) + 1
        origins[str(card.get("origin") or "")] = origins.get(str(card.get("origin") or ""), 0) + 1
    return {
        "counts": counts,
        "origins": origins,
        "todo": [c["term"] for c in cards_by_status("todo")],
        "draft": [c["term"] for c in cards_by_status("draft")],
    }


def find_card(term: str) -> dict[str, Any] | None:
    key = str(term or "").strip().casefold()
    if not key:
        return None
    for card in load_cards():
        if card["term"].casefold() == key:
            return card
    return None


def set_status(term: str, status: str, *, by: str = "") -> dict[str, Any] | None:
    """카드 status 변경 — cards_local 에 기록되므로 seed todo 도 이 함수로 활성화된다."""
    card = find_card(term)
    if card is None or status not in {"active", "todo", "draft", "disabled"}:
        return None
    return teach_card(
        card["term"], card.get("body") or "(내용 없음)", by=by,
        kind=card.get("kind") or "concept", answered_by=card.get("answered_by") or "",
        sources=card.get("sources"), aliases=card.get("aliases"),
        trigger_terms=card.get("trigger_terms"), related=card.get("related"), status=status,
    )


_FILL_SYSTEM = (
    "당신은 반도체 fab 데이터 앱 Flow 의 도메인 지식 카드 작성자다. "
    "질문 틀 카드의 각 질문에 '환경 증거'와 참고 지식으로 답해 카드 본문을 작성한다. "
    "환경 증거로 확인된 것만 사실로 단정하고, 확인 못 한 내용은 본문에 넣지 말고 "
    "본문 마지막의 '## 남은 질문' 섹션에 관리자에게 물어볼 질문 불릿으로 남긴다. "
    "사실 위주의 짧은 마크다운 불릿, 머리말/맺음말 없이 본문만 출력한다."
)

FILL_QUESTIONS_FILE = PATHS.data_root / "knowledge" / "fill_questions.json"


def _fill_evidence(max_chars: int = 2800) -> str:
    """채움 전 환경 실조사 (MCP식 도구 수행) — 파일 목록/컬럼 헤더/제품 목록.

    LLM 이 추측 대신 실측 근거로 쓰도록 결정적 스캔 결과를 제공한다. 실패 항목은 건너뜀."""
    lines: list[str] = []
    try:
        root = Path(PATHS.db_root)
        csvs = sorted(p.name for p in root.glob("*.csv"))
        if csvs:
            lines.append("db_root CSV 파일: " + ", ".join(csvs[:30]))
        products = sorted(p.stem[len("ML_TABLE_"):] for p in root.glob("ML_TABLE_*.parquet"))
        if products:
            lines.append("ML_TABLE 제품: " + ", ".join(products[:20]))
        import csv as _csv
        for name in ("ppid_knob.csv", "Vehicle_matching.csv", "step_matching.csv",
                     "inline_matching.csv", "vm_matching.csv"):
            fp = root / name
            try:
                if fp.is_file():
                    with open(fp, "r", encoding="utf-8-sig", newline="") as fh:
                        header = next(_csv.reader(fh), [])
                    lines.append(f"{name} 컬럼: {', '.join(h.strip() for h in header[:12])}")
            except Exception:
                continue
        rf = root / "reformatter"
        if rf.is_dir():
            names = sorted(p.name for p in rf.glob("*.csv"))
            lines.append("reformatter 파일: " + (", ".join(names[:10]) or "(없음)"))
        # step_desc 샘플 + 공정 구간 자동 추정 — process-stage 채움 카드의 증거.
        # Vehicle_matching.csv 행 순서(=flow 순서 가정)를 유지한 채 step_desc 를
        # domain.classify_process_area 로 분류해 제품별 연속 구간으로 압축한다.
        try:
            from core.domain import classify_process_area
            fp = root / "Vehicle_matching.csv"
            if fp.is_file():
                with open(fp, "r", encoding="utf-8-sig", newline="") as fh:
                    rows = list(_csv.DictReader(fh))
                descs: list[str] = []
                for r in rows:
                    d = str(r.get("step_desc") or "").strip()
                    if d and d not in descs:
                        descs.append(d)
                    if len(descs) >= 10:
                        break
                if descs:
                    lines.append("step_desc 예: " + ", ".join(descs))
                by_product: dict[str, list[dict]] = {}
                for r in rows:
                    p = str(r.get("product") or r.get("vehicle") or "").strip()
                    if p:
                        by_product.setdefault(p, []).append(r)
                for pname in list(by_product)[:2]:
                    ranges: list[list[str]] = []  # [area, start_step_id, end_step_id]
                    for r in by_product[pname]:
                        area = classify_process_area(str(r.get("step_desc") or "")) or "?"
                        sid = str(r.get("step_id") or "").strip()
                        if not sid:
                            continue
                        if ranges and ranges[-1][0] == area:
                            ranges[-1][2] = sid
                        else:
                            ranges.append([area, sid, sid])
                    if ranges:
                        parts = [(f"{a} {s}~{e}" if s != e else f"{a} {s}") for a, s, e in ranges[:14]]
                        more = f" (+{len(ranges) - 14}구간)" if len(ranges) > 14 else ""
                        lines.append(f"{pname} 공정 구간 추정(step_desc 기반, 미확정): "
                                     + " / ".join(parts) + more)
        except Exception:
            pass
        if products:
            try:
                import polars as pl
                schema = pl.scan_parquet(root / f"ML_TABLE_{products[0]}.parquet").collect_schema()
                cols = schema.names() if hasattr(schema, "names") else list(schema)
                knobs = [c for c in cols if str(c).upper().startswith(("KNOB_", "MASK_"))]
                lines.append(f"ML_TABLE_{products[0]} 컬럼 예: "
                             + ", ".join([c for c in cols if not str(c).upper().startswith(('KNOB_', 'MASK_'))][:10])
                             + (f" / KNOB·MASK {len(knobs)}개: " + ", ".join(knobs[:8]) if knobs else ""))
            except Exception:
                pass
    except Exception:
        pass
    return "\n".join(lines)[:max_chars]


def _extract_open_questions(body: str) -> list[str]:
    """draft 본문의 '남은 질문' 섹션(없으면 '(확인 필요)' 줄)에서 질문 불릿 추출."""
    text = str(body or "")
    m = re.search(r"#+\s*남은\s*질문\s*\n(.*?)(?:\n#+\s|\Z)", text, flags=re.DOTALL)
    section = m.group(1) if m else ""
    questions = [q.strip(" -*\t") for q in section.splitlines() if q.strip(" -*\t")]
    if not questions:
        questions = [ln.strip(" -*\t") for ln in text.splitlines() if "(확인 필요)" in ln]
    return [q[:300] for q in questions if len(q) >= 5][:10]


def _load_fill_questions() -> dict[str, list[str]]:
    try:
        import json
        data = json.loads(FILL_QUESTIONS_FILE.read_text(encoding="utf-8"))
        return {str(k): [str(q) for q in v] for k, v in data.items() if isinstance(v, list)}
    except Exception:
        return {}


def _save_fill_questions(data: dict[str, list[str]]) -> None:
    try:
        import json
        FILL_QUESTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        FILL_QUESTIONS_FILE.write_text(
            json.dumps({k: v for k, v in data.items() if v}, ensure_ascii=False, indent=1),
            encoding="utf-8")
    except Exception:
        pass


def pending_fill_questions() -> dict[str, list[str]]:
    """카드별 관리자 답변 대기 질문 — "지식 답변: <term> <답>" 으로 채운다."""
    return _load_fill_questions()


def answer_fill_question(term_and_answer: str, *, by: str = "") -> dict[str, Any] | None:
    """"지식 답변: <term> <답>" 처리 — 대기 질문 term 을 앞부분 최장 일치로 찾고,
    답을 해당 카드 본문의 '## 사용자 답변' 섹션에 병합한다 (draft/active 모두 가능)."""
    text = str(term_and_answer or "").strip()
    if not text:
        return None
    pending = _load_fill_questions()
    candidates = sorted(set(pending) | {c["term"] for c in load_cards()}, key=len, reverse=True)
    term = next((t for t in candidates
                 if text.casefold().startswith(t.casefold()) and len(text) > len(t) + 2), "")
    if not term:
        return None
    answer = text[len(term):].strip(" :：,->→은는\t")
    if len(answer) < 2:
        return None
    card = find_card(term)
    if card is None:
        return None
    body = str(card.get("body") or "").rstrip()
    if "## 사용자 답변" in body:
        body += f"\n- {answer}"
    else:
        body += f"\n\n## 사용자 답변\n- {answer}"
    saved = teach_card(
        card["term"], body, by=by, kind=card.get("kind") or "concept",
        answered_by=card.get("answered_by") or "", sources=card.get("sources"),
        aliases=card.get("aliases"), trigger_terms=card.get("trigger_terms"),
        related=card.get("related"), status=str(card.get("status") or "active"),
    )
    if saved is None:
        return None
    if term in pending:
        remaining = pending[term][1:]  # 앞 질문부터 소진
        pending[term] = remaining
        _save_fill_questions(pending)
    return {"term": card["term"], "answer": answer,
            "remaining_questions": pending.get(term, []), "status": saved.get("status")}


def _fill_context(max_chars: int = 2400) -> str:
    """채움 프롬프트에 줄 참고 지식 — active 시드 카드 본문 + 로컬 룰북 feature 목록."""
    parts: list[str] = []
    features: list[str] = []
    for card in load_cards():
        if card.get("origin") == "seed" and str(card.get("status") or "active") == "active":
            body = re.sub(r"\s+", " ", str(card.get("body") or "")).strip()
            parts.append(f"- {card['term']}: {body[:300]}")
        elif card.get("origin") == "generated":
            features.append(card["term"])
    if features:
        parts.append(f"- 이 환경의 룰북 split/knob feature 목록: {', '.join(features[:40])}")
    text = "\n".join(parts)
    return text[:max_chars]


def fill_prompt(card: dict[str, Any], context: str = "", evidence: str = "") -> str:
    return (
        f"# 채울 지식 카드 틀\nterm: {card.get('term')}\nkind: {card.get('kind') or 'concept'}\n"
        f"질문:\n{card.get('body') or ''}\n\n"
        + (f"# 환경 증거 (이 환경의 실제 파일/컬럼 스캔 결과 — 이것만 사실로 단정 가능)\n{evidence}\n\n" if evidence else "")
        + (f"# 참고 지식 (앱에 이미 등록된 카드)\n{context}\n\n" if context else "")
        + "# 지시\n증거로 답할 수 있는 것은 본문 불릿으로 쓰고, 확인 못 한 것은 "
          "'## 남은 질문' 섹션에 관리자에게 물어볼 질문으로 남겨라."
    )


def fill_todo_cards(
    *,
    complete_fn: Any = None,
    limit: int = 4,
    by: str = "ai-draft",
) -> dict[str, Any]:
    """todo 카드를 연결된 LLM(GPT OSS 등)으로 초안 작성 → status=draft 로 저장.

    draft 는 resolve/prompt 에 노출되지 않으며, 관리자가 "지식 승인: <term>" 으로
    active 전환해야 사용된다. complete_fn(prompt, system)->str 주입 가능(테스트).
    """
    if complete_fn is None:
        try:
            from core import llm_adapter
        except Exception:
            return {"ok": False, "error": "llm_unavailable", "filled": [], "failed": []}
        if not (llm_adapter.is_available() and llm_adapter.should_attempt_llm()):
            return {"ok": False, "error": "llm_unavailable", "filled": [], "failed": []}

        def complete_fn(prompt: str, system: str) -> str:
            out = llm_adapter.complete(prompt, system=system, timeout=45)
            return str(out.get("text") or "") if out.get("ok") else ""

    todos = cards_by_status("todo")[: max(1, min(int(limit or 4), 12))]
    if not todos:
        return {"ok": True, "filled": [], "failed": [], "remaining_todo": 0, "questions": {}}
    context = _fill_context()
    evidence = _fill_evidence()
    filled: list[str] = []
    failed: list[str] = []
    questions: dict[str, list[str]] = {}
    for card in todos:
        try:
            text = str(complete_fn(fill_prompt(card, context, evidence), _FILL_SYSTEM) or "").strip()
        except Exception:
            text = ""
        # 한국어 답변은 압축적이라 최소 길이는 낮게 — 빈/한두 단어 응답만 걸러낸다.
        if len(text) < 20:
            failed.append(card["term"])
            continue
        saved = teach_card(
            card["term"], text, by=by, kind=card.get("kind") or "concept",
            answered_by=card.get("answered_by") or "", sources=card.get("sources"),
            aliases=card.get("aliases"), trigger_terms=card.get("trigger_terms"),
            related=card.get("related"), status="draft",
        )
        if saved:
            filled.append(card["term"])
            open_qs = _extract_open_questions(text)
            if open_qs:
                questions[card["term"]] = open_qs
        else:
            failed.append(card["term"])
    if questions:
        merged = _load_fill_questions()
        merged.update(questions)
        _save_fill_questions(merged)
    return {
        "ok": True,
        "filled": filled,
        "failed": failed,
        "remaining_todo": len(cards_by_status("todo")),
        "questions": questions,
    }
