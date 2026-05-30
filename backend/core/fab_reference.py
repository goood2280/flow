"""core/fab_reference.py — deterministic single-file FAB reference lookups.

홈 에이전트(Flow-i)가 두 가지 결정적 질문에 답하기 위한 순수 조회 로직.
LLM 없이 동작하며, CSV 는 `matching_cache` 를 통해 DuckDB 캐시로 읽는다.

데이터 소스 (모두 `PATHS.db_root` 루트레벨, FLOW_DB_ROOT 미설정 시 data/Fab):
  - step_matching.csv  (product, step_id, function_step)
        step_id  <-> function_step 양방향 매칭.
  - ppid_knob.csv      (feature_name, function_step, rule_order, operator, value, category)
        `operator` 규칙으로 `value`(= SplitTable cell ppid)를
        `category`(= knob/split 이름)로 분류한다. 현재 operator 는 eq 만 지원.

모든 함수는 `rows` 를 주입받을 수 있어(테스트 hermetic) 디스크 의존 없이 검증 가능하다.
"""
from __future__ import annotations

import csv as _csv
import re
from typing import Any, Iterable

from core.paths import PATHS

STEP_MATCHING_FILE = "step_matching.csv"
PPID_KNOB_FILE = "ppid_knob.csv"

# knob/step 의도 키워드 — handle() 에서 무관한 질문 가로채기 방지용(질문형만 매칭).
# step_id 토큰 자체가 있으면 의도 키워드 없이도 강한 신호로 처리한다.
_STEP_INTENT_RE = re.compile(
    r"(step[_\s-]?id|function[_\s-]?step|무슨\s*step|어떤\s*step|어느\s*step|무슨\s*스텝|어떤\s*스텝|무슨\s*공정|어떤\s*공정)",
    re.IGNORECASE,
)
_KNOB_INTENT_RE = re.compile(
    r"(분류|어떤\s*knob|무슨\s*knob|어느\s*knob|어떤\s*노브|무슨\s*노브|어떤\s*split|무슨\s*split|knob\s*으?로|노브\s*로)",
    re.IGNORECASE,
)
# ppid 토큰 후보 (PP_PRODA0_03, PPID_24_3 등) — 알려진 value 미스 시 폴백 추출.
_PPID_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])P[A-Za-z]*ID?[A-Za-z0-9_]*\d[A-Za-z0-9_]*(?![A-Za-z0-9_])")


def _norm(value: Any) -> str:
    return ("" if value is None else str(value)).strip()


def _read_rows(filename: str) -> list[dict[str, str]]:
    """매칭 CSV 를 [{col: str}] 로 읽는다. matching_cache 우선, 실패 시 plain CSV."""
    path = PATHS.db_root / filename
    try:
        from core import matching_cache

        df = matching_cache.read_matching_csv(path)
        if df is not None:
            return [{_norm(k): _norm(v) for k, v in row.items()} for row in df.to_dicts()]
    except Exception:
        pass
    try:
        if not path.is_file():
            return []
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            reader = _csv.DictReader(fh)
            return [{_norm(k): _norm(v) for k, v in (row or {}).items() if k is not None} for row in reader]
    except Exception:
        return []


def _find_known_tokens(text: str, tokens: Iterable[str]) -> list[str]:
    """text 안에 등장하는 알려진 토큰을 경계 기준으로 찾는다(대소문자 무시, 등장 순서 유지)."""
    up = str(text or "").upper()
    out: list[str] = []
    for tok in tokens:
        tok = (tok or "").upper()
        if not tok or tok in out:
            continue
        if re.search(r"(?<![A-Z0-9_])" + re.escape(tok) + r"(?![A-Z0-9_])", up):
            out.append(tok)
    return out


# ── Step ID <-> function_step ────────────────────────────────────────────────
def _filter_product(rows: list[dict[str, str]], product: str) -> list[dict[str, str]]:
    if not product:
        return rows
    pu = product.upper()
    return [r for r in rows if r.get("product", "").upper() == pu]


def lookup_step(text: str, product: str = "", *, rows: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """step_matching.csv 기준 step_id <-> function_step 조회.

    text 안의 알려진 step_id 가 있으면 그 function_step 을, 없고 function_step 이
    있으면 해당 step_id 목록을 반환. product 옵션으로 범위 제한.
    """
    rows = _read_rows(STEP_MATCHING_FILE) if rows is None else rows
    scope = _filter_product(rows, product)
    if not scope:
        return {"found": False, "reason": "no_data", "matches": [], "answer": ""}
    step_ids = {r["step_id"] for r in scope if r.get("step_id")}
    func_steps = {r["function_step"] for r in scope if r.get("function_step")}
    id_hits = {h.upper() for h in _find_known_tokens(text, step_ids)}
    fs_hits = {h.upper() for h in _find_known_tokens(text, func_steps)}

    if id_hits:
        matches: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for r in scope:
            if r.get("step_id", "").upper() in id_hits:
                key = (r.get("product", ""), r.get("step_id", ""))
                if key in seen:
                    continue
                seen.add(key)
                matches.append({"product": r.get("product", ""), "step_id": r.get("step_id", ""), "function_step": r.get("function_step", "")})
        return {"found": True, "direction": "id_to_step", "matches": matches, "answer": _answer_id_to_step(matches)}

    if fs_hits:
        matches = [
            {"product": r.get("product", ""), "step_id": r.get("step_id", ""), "function_step": r.get("function_step", "")}
            for r in scope
            if r.get("function_step", "").upper() in fs_hits and r.get("step_id")
        ]
        return {"found": True, "direction": "step_to_id", "matches": matches, "answer": _answer_step_to_id(matches)}

    return {"found": False, "reason": "no_match", "matches": [], "answer": ""}


def _answer_id_to_step(matches: list[dict[str, str]]) -> str:
    if not matches:
        return ""
    parts = [
        f"{m['step_id']}는 {m['product'] + '의 ' if m.get('product') else ''}{m['function_step']} step입니다."
        for m in matches
    ]
    return "\n".join(parts)


def _answer_step_to_id(matches: list[dict[str, str]]) -> str:
    if not matches:
        return ""
    by_step: dict[str, dict[str, list[str]]] = {}
    for m in matches:
        by_step.setdefault(m["function_step"], {}).setdefault(m.get("product", ""), []).append(m["step_id"])
    lines: list[str] = []
    for fstep, by_prod in by_step.items():
        segs = [f"{prod}: {', '.join(ids)}" if prod else ", ".join(ids) for prod, ids in by_prod.items()]
        lines.append(f"{fstep}의 step_id → " + " / ".join(segs))
    return "\n".join(lines)


def lookup_step_in_text(text: str, product: str = "", *, rows: list[dict[str, str]] | None = None) -> dict[str, Any] | None:
    """홈 에이전트 handle() 용. step 의도 + 매칭이 있을 때만 결과, 아니면 None."""
    result = lookup_step(text, product, rows=rows)
    if result.get("found"):
        # step_id 히트는 강한 신호라 그대로 처리하지만, function_step 이름만 등장한
        # 경우(step_to_id)는 step 의도 키워드가 있어야 가로챈다(과잉 클레임 방지).
        if result.get("direction") == "step_to_id" and not _STEP_INTENT_RE.search(text or ""):
            return None
        return result
    if result.get("reason") == "no_data":
        return None
    if _STEP_INTENT_RE.search(text or ""):
        return {**result, "answer": "해당 step_id / function_step 을 step_matching.csv 에서 찾지 못했습니다."}
    return None


# ── PPID(value) -> knob(category) ─────────────────────────────────────────────
def classify_ppid_knob(value: str, product: str = "", *, rows: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """ppid_knob.csv 의 operator(eq) 규칙으로 value(ppid)를 category(knob/split)로 분류."""
    rows = _read_rows(PPID_KNOB_FILE) if rows is None else rows
    if not rows:
        return {"found": False, "reason": "no_data", "matches": [], "answer": ""}
    target = _norm(value).upper()
    matches: list[dict[str, str]] = []
    has_any_value = False
    for r in rows:
        rule_value = r.get("value", "")
        if rule_value:
            has_any_value = True
        operator = (r.get("operator") or "eq").strip().lower()
        if operator not in ("", "eq"):
            continue  # 현재 eq 만 지원 — 다른 operator 는 추후 확장.
        if rule_value and rule_value.upper() == target:
            matches.append({
                "value": rule_value,
                "category": r.get("category", ""),
                "feature_name": r.get("feature_name", ""),
                "function_step": r.get("function_step", ""),
                "rule_order": r.get("rule_order", ""),
                "operator": operator or "eq",
            })
    if matches:
        # category 중복 제거하면서 순서 유지.
        seen: set[str] = set()
        unique = [m for m in matches if not (m["category"] in seen or seen.add(m["category"]))]
        return {"found": True, "matches": unique, "answer": _answer_ppid_knob(value, unique)}
    reason = "no_match" if has_any_value else "value_unset"
    return {"found": False, "reason": reason, "matches": [], "answer": _answer_ppid_unmatched(value, reason)}


def _answer_ppid_knob(value: str, matches: list[dict[str, str]]) -> str:
    parts: list[str] = []
    for m in matches:
        rule = " / ".join(x for x in (m.get("feature_name"), m.get("function_step")) if x)
        suffix = f" (rule: {rule})" if rule else ""
        parts.append(f"{value}은(는) knob(split) '{m['category']}'(으)로 분류됩니다.{suffix}")
    return "\n".join(parts)


def _answer_ppid_unmatched(value: str, reason: str) -> str:
    if reason == "value_unset":
        return f"{value}: ppid_knob.csv 의 value 열이 비어 있어 분류 규칙이 아직 설정되지 않았습니다. (담당자가 ppid↔category 매핑을 채워야 합니다.)"
    return f"{value}에 매칭되는 knob 분류 규칙을 ppid_knob.csv 에서 찾지 못했습니다."


def classify_ppid_in_text(text: str, product: str = "", *, rows: list[dict[str, str]] | None = None) -> dict[str, Any] | None:
    """홈 에이전트 handle() 용. knob 의도 + ppid 토큰이 있을 때만 결과, 아니면 None."""
    rows = _read_rows(PPID_KNOB_FILE) if rows is None else rows
    if not rows:
        return None
    if not _KNOB_INTENT_RE.search(text or ""):
        return None
    known = {r.get("value", "") for r in rows if r.get("value")}
    known |= {r.get("category", "") for r in rows if r.get("category")}
    hits = _find_known_tokens(text, known)
    candidate = hits[0] if hits else None
    if candidate is None:
        token_match = _PPID_TOKEN_RE.search(text or "")
        candidate = token_match.group(0) if token_match else None
    if candidate is None:
        return None
    return classify_ppid_knob(candidate, product, rows=rows)
