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
# Valve 룰북 순환의 마스터 매칭테이블 (vehicle, product, step_id, step_desc)
VEHICLE_MATCHING_FILE = "Vehicle_matching.csv"

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
# step_id 모양 토큰 (AA100000, A00000, AB100000EC 등) — 알려진 id 미스 시 폴백 추출.
_STEP_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Z]{1,3}\d{5,6}[A-Z0-9]{0,4}(?![A-Za-z0-9_])")


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


def vehicle_matching_rows() -> list[dict[str, str]]:
    """Vehicle_matching.csv 전체 행 (vehicle, product, step_id, step_desc).

    step_id 를 사람이 읽는 이름으로 바꿔야 하는 다른 모듈(예: `core.lot_wip` 의
    WIP 현재위치 답변)이 같은 원본을 쓰도록 공개한 진입점이다.
    """
    return _read_rows(VEHICLE_MATCHING_FILE)


def lookup_step(text: str, product: str = "", *,
                rows: list[dict[str, str]] | None = None,
                vehicle_rows: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """step_matching.csv + Vehicle_matching.csv 기준 step 조회.

    text 안의 알려진 step_id 가 있으면 그 function_step(flow 내부) 과
    step_desc(vehicle matching) 를, 없고 이름(function_step/step_desc)이 있으면
    해당 step_id 목록을 반환. product 옵션으로 범위 제한.

    rows 를 명시 주입하면(hermetic 테스트) vehicle_rows 도 주입분만 사용한다.
    """
    injected = rows is not None
    rows = _read_rows(STEP_MATCHING_FILE) if rows is None else rows
    if vehicle_rows is None:
        vehicle_rows = [] if injected else _read_rows(VEHICLE_MATCHING_FILE)
    scope = _filter_product(rows, product)
    vscope = _filter_product(vehicle_rows, product)
    if not scope and not vscope:
        return {"found": False, "reason": "no_data", "matches": [], "answer": ""}
    step_ids = ({r["step_id"] for r in scope if r.get("step_id")}
                | {r["step_id"] for r in vscope if r.get("step_id")})
    func_steps = {r["function_step"] for r in scope if r.get("function_step")}
    step_descs = {r["step_desc"] for r in vscope if r.get("step_desc")}
    id_hits = {h.upper() for h in _find_known_tokens(text, step_ids)}
    name_hits = {h.upper() for h in _find_known_tokens(text, func_steps | step_descs)}

    if id_hits:
        merged: dict[tuple[str, str], dict[str, str]] = {}

        def _slot(r: dict[str, str]) -> dict[str, str]:
            key = (r.get("product", ""), r.get("step_id", ""))
            return merged.setdefault(key, {
                "product": r.get("product", ""), "step_id": r.get("step_id", ""),
                "function_step": "", "step_desc": "", "vehicle": "",
            })

        for r in scope:
            if r.get("step_id", "").upper() in id_hits:
                _slot(r)["function_step"] = r.get("function_step", "")
        for r in vscope:
            if r.get("step_id", "").upper() in id_hits:
                m = _slot(r)
                m["step_desc"] = r.get("step_desc", "")
                m["vehicle"] = r.get("vehicle", "")
        matches = list(merged.values())
        return {"found": True, "direction": "id_to_step", "matches": matches, "answer": _answer_id_to_step(matches)}

    if name_hits:
        matches = [
            {"product": r.get("product", ""), "step_id": r.get("step_id", ""),
             "function_step": r.get("function_step", ""), "step_desc": "", "vehicle": ""}
            for r in scope
            if r.get("function_step", "").upper() in name_hits and r.get("step_id")
        ]
        matches += [
            {"product": r.get("product", ""), "step_id": r.get("step_id", ""),
             "function_step": "", "step_desc": r.get("step_desc", ""), "vehicle": r.get("vehicle", "")}
            for r in vscope
            if r.get("step_desc", "").upper() in name_hits and r.get("step_id")
        ]
        return {"found": True, "direction": "step_to_id", "matches": matches, "answer": _answer_step_to_id(matches)}

    return {"found": False, "reason": "no_match", "matches": [], "answer": ""}


def _answer_id_to_step(matches: list[dict[str, str]]) -> str:
    if not matches:
        return ""
    parts = []
    for m in matches:
        names = " / ".join(dict.fromkeys(
            x for x in (m.get("function_step"), m.get("step_desc")) if x))
        vehicle = f" [vehicle: {m['vehicle']}]" if m.get("vehicle") else ""
        parts.append(
            f"{m['step_id']}는 {m['product'] + '의 ' if m.get('product') else ''}{names} step입니다.{vehicle}")
    return "\n".join(parts)


def _answer_step_to_id(matches: list[dict[str, str]]) -> str:
    if not matches:
        return ""
    by_step: dict[str, dict[str, list[str]]] = {}
    for m in matches:
        name = m.get("function_step") or m.get("step_desc") or ""
        by_step.setdefault(name, {}).setdefault(m.get("product", ""), []).append(m["step_id"])
    lines: list[str] = []
    for fstep, by_prod in by_step.items():
        segs = [f"{prod}: {', '.join(ids)}" if prod else ", ".join(ids) for prod, ids in by_prod.items()]
        lines.append(f"{fstep}의 step_id → " + " / ".join(segs))
    return "\n".join(lines)


def extract_step_tokens(text: str) -> list[str]:
    """text 에서 step_id 모양(대문자 1~3 + 숫자 5~6 + 선택적 suffix) 토큰을 추출."""
    return list(dict.fromkeys(m.group(0).upper() for m in _STEP_TOKEN_RE.finditer(str(text or "").upper())))


def _step_base(step_id: str) -> str:
    """suffix 변형 정규화 — AB100000EC -> AB100000 (뒤 문자군 제거)."""
    return re.sub(r"[A-Z]+$", "", str(step_id or "").upper())


def suggest_similar_steps(token: str, product: str = "", *,
                          rows: list[dict[str, str]] | None = None,
                          limit: int = 10) -> list[dict[str, str]]:
    """정확 일치가 없을 때 유사 step_id 후보 — suffix 정규화 일치 > 접두 일치 순."""
    rows = _read_rows(STEP_MATCHING_FILE) if rows is None else rows
    scope = _filter_product(rows, product)
    token_u = str(token or "").upper()
    token_base = _step_base(token_u)
    ranked: list[tuple[int, dict[str, str]]] = []
    seen: set[tuple[str, str]] = set()
    for r in scope:
        sid = str(r.get("step_id") or "").upper()
        if not sid:
            continue
        key = (r.get("product", ""), sid)
        if key in seen:
            continue
        rank = 0
        if token_base and _step_base(sid) == token_base:
            rank = 2  # 같은 base — suffix 변형 관계
        elif token_u and (sid.startswith(token_u) or token_u.startswith(sid)):
            rank = 1  # 접두 관계
        if rank:
            seen.add(key)
            ranked.append((rank, {"product": r.get("product", ""), "step_id": r.get("step_id", ""), "function_step": r.get("function_step", "")}))
    ranked.sort(key=lambda x: (-x[0], x[1]["step_id"]))
    return [m for _rank, m in ranked[:limit]]


def search_related_files(token: str, *, limit_per_file: int = 3) -> list[dict[str, Any]]:
    """db_root 의 알려진 매칭 CSV 들에서 token 이 등장하는 파일/열을 찾는다.

    Files(단일 파일) 관리 대상인 룰북/매칭테이블 안에서 step_id 나 용어가 어디에
    쓰이는지 횡단 검색해 준다. 결과는 파일별 {file, hit_rows, columns, samples}.
    """
    token_u = str(token or "").strip().upper()
    if not token_u:
        return []
    try:
        from core.matching_cache import SUPPORTED_MATCHING_FILES
        filenames = sorted(SUPPORTED_MATCHING_FILES)
    except Exception:
        filenames = [STEP_MATCHING_FILE, PPID_KNOB_FILE]
    boundary = re.compile(r"(?<![A-Z0-9_])" + re.escape(token_u) + r"(?![A-Z0-9_])")
    out: list[dict[str, Any]] = []
    for filename in filenames:
        if not (PATHS.db_root / filename).is_file():
            continue
        rows = _read_rows(filename)
        if not rows:
            continue
        hit_rows = 0
        columns: list[str] = []
        samples: list[dict[str, str]] = []
        for r in rows:
            hit_cols = [c for c, v in r.items() if v and boundary.search(str(v).upper())]
            if not hit_cols:
                continue
            hit_rows += 1
            for c in hit_cols:
                if c not in columns:
                    columns.append(c)
            if len(samples) < limit_per_file:
                samples.append(dict(r))
        if hit_rows:
            out.append({"file": filename, "hit_rows": hit_rows, "columns": columns, "samples": samples})
    return out


def _read_rows_any_root(filename: str) -> list[dict[str, str]]:
    """db_root 우선, 없으면 base_root 에서 단일 CSV 를 읽는다."""
    rows = _read_rows(filename)
    if rows:
        return rows
    try:
        path = PATHS.base_root / filename
        if not path.is_file():
            return []
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            reader = _csv.DictReader(fh)
            return [{_norm(k): _norm(v) for k, v in (row or {}).items() if k is not None} for row in reader]
    except Exception:
        return []


def search_in_files(token: str, filenames: list[str], *, limit_per_file: int = 3) -> list[dict[str, Any]]:
    """지정된 단일 파일(CSV)들에서 token 이 등장하는 행/열을 찾는다.

    파일 설명문 카탈로그(flowi_file_docs)가 고른 파일들을 대상으로 쓴다.
    parquet 등 CSV 가 아닌 파일은 건너뛴다 (FileBrowser AI SQL 경로 사용).
    """
    token_u = str(token or "").strip().upper()
    if not token_u:
        return []
    boundary = re.compile(r"(?<![A-Z0-9_])" + re.escape(token_u) + r"(?![A-Z0-9_])")
    out: list[dict[str, Any]] = []
    for filename in filenames:
        name = str(filename or "").strip()
        if not name or not name.lower().endswith(".csv"):
            continue
        rows = _read_rows_any_root(name)
        if not rows:
            continue
        hit_rows = 0
        columns: list[str] = []
        samples: list[dict[str, str]] = []
        for r in rows:
            hit_cols = [c for c, v in r.items() if v and boundary.search(str(v).upper())]
            if not hit_cols:
                continue
            hit_rows += 1
            for c in hit_cols:
                if c not in columns:
                    columns.append(c)
            if len(samples) < limit_per_file:
                samples.append(dict(r))
        if hit_rows:
            out.append({"file": name, "hit_rows": hit_rows, "columns": columns, "samples": samples})
    return out


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
    # 미스 경로는 기존처럼 step 의도 키워드가 있을 때만 개입한다 — step_id 모양
    # 토큰만으로 가로채면 root lot(AZ123456류) 질문을 오염시킬 수 있다.
    if not _STEP_INTENT_RE.search(text or ""):
        return None
    step_tokens = extract_step_tokens(text)
    # 정확 일치는 없지만 step 의도가 있음 — 유사 후보를 제시한다.
    token = step_tokens[0] if step_tokens else ""
    similar = suggest_similar_steps(token, product, rows=rows) if token else []
    if similar:
        lines = [f"'{token}' 정확 일치는 step_matching.csv 에 없습니다. 유사 step_id {len(similar)}건:"]
        lines += [f"- {m['step_id']} ({m['product']}): {m['function_step']}" for m in similar[:6]]
        answer = "\n".join(lines)
    else:
        answer = "해당 step_id / function_step 을 step_matching.csv 에서 찾지 못했습니다."
    return {**result, "token": token, "similar": similar, "answer": answer}


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


# ── Split(feature) 규칙 나열 ──────────────────────────────────────────────────
_RULE_INTENT_RE = re.compile(r"(규칙|룰\s|룰이|룰은|룰을|룰북|\brules?\b)", re.IGNORECASE)
_SPLIT_CONTEXT_RE = re.compile(r"(split|스플릿|knob|노브|분류)", re.IGNORECASE)


def _rule_sort_key(rule: dict[str, str]) -> tuple[int, float]:
    order = _norm(rule.get("rule_order")).upper()
    if order == "RO":
        return (1, 0.0)
    m = re.search(r"(\d+)", order)
    return (0, float(m.group(1)) if m else 9e9)


def list_feature_rules(name: str, *, rows: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """ppid_knob.csv 에서 Split(feature) 이름으로 규칙 목록을 나열.

    이름은 feature_name 정확 일치 > function_step 정확 일치 > feature_name
    부분 일치 순으로 해석. 규칙은 R1..Rn 순 + RO(rest-of) 마지막.
    """
    rows = _read_rows(PPID_KNOB_FILE) if rows is None else rows
    if not rows:
        return {"found": False, "reason": "no_data", "features": [], "rules": [], "answer": ""}
    target = _norm(name).upper()
    feature_names = list(dict.fromkeys(r.get("feature_name", "") for r in rows if r.get("feature_name")))

    feats = [f for f in feature_names if f.upper() == target]
    if not feats:
        feats = list(dict.fromkeys(
            r.get("feature_name", "") for r in rows
            if _norm(r.get("function_step")).upper() == target and r.get("feature_name")))
    if not feats and len(target) >= 2:
        feats = [f for f in feature_names if target in f.upper()]
    if not feats:
        avail = ", ".join(feature_names[:12]) + (" …" if len(feature_names) > 12 else "")
        return {"found": False, "reason": "no_match", "features": [], "rules": [],
                "answer": (f"'{name}' Split 규칙을 ppid_knob.csv 에서 찾지 못했습니다.\n"
                           f"등록된 Split(feature): {avail}")}

    out_rules: list[dict[str, str]] = []
    lines: list[str] = []
    for feat in feats:
        grp = sorted((r for r in rows if r.get("feature_name") == feat), key=_rule_sort_key)
        # 파일의 완전 중복 행은 표시에서 제거 (룰 의미는 동일).
        seen: set[tuple] = set()
        grp = [r for r in grp
               if (key := (r.get("rule_order"), r.get("operator"), r.get("value"),
                           r.get("category"), r.get("function_step"))) not in seen
               and not seen.add(key)]
        lines.append(f"'{feat}' Split 규칙 ({len(grp)}건, ppid_knob.csv):")
        for r in grp:
            order = _norm(r.get("rule_order")).upper()
            rule = {"feature_name": feat, "rule_order": r.get("rule_order", ""),
                    "operator": r.get("operator", ""), "value": r.get("value", ""),
                    "category": r.get("category", ""), "function_step": r.get("function_step", "")}
            out_rules.append(rule)
            fs = f" ({rule['function_step']})" if rule["function_step"] else ""
            if order == "RO":
                cat = rule["category"] or "raw ppid 유지 (미분류 → 알람 대상)"
                lines.append(f"- RO: 나머지 → {cat}{fs}")
            else:
                lines.append(f"- {rule['rule_order']}: {rule['operator'] or 'eq'} "
                             f"{rule['value']} → {rule['category']}{fs}")
    return {"found": True, "features": feats, "rules": out_rules, "answer": "\n".join(lines)}


def list_rules_in_text(text: str, product: str = "", *, rows: list[dict[str, str]] | None = None) -> dict[str, Any] | None:
    """홈 에이전트 handle() 용. 규칙 의도 + Split 이름이 있을 때만 결과, 아니면 None."""
    rows = _read_rows(PPID_KNOB_FILE) if rows is None else rows
    if not rows:
        return None
    text = str(text or "")
    if not _RULE_INTENT_RE.search(text):
        return None
    known = {r.get("feature_name", "") for r in rows if r.get("feature_name")}
    known |= {r.get("function_step", "") for r in rows if r.get("function_step")}
    hits = _find_known_tokens(text, known)
    if hits:
        return list_feature_rules(hits[0], rows=rows)
    # 알려진 이름이 없으면 '<이름> (split) 규칙' 패턴에서 후보 추출 —
    # split/knob 맥락이 있을 때만 개입한다 (일반 '규칙' 질문 오염 방지).
    if not _SPLIT_CONTEXT_RE.search(text):
        return None
    m = re.search(
        r"([A-Za-z0-9_.\-]{2,40}(?:\s+[A-Za-z0-9_.\-]{1,40})?)\s*(?:의)?\s*(?:split|스플릿)?\s*(?:규칙|룰|rule)",
        text, re.IGNORECASE)
    candidate = m.group(1).strip() if m else ""
    candidate = re.sub(r"(?i)\b(split|스플릿)\b\s*$", "", candidate).strip()
    if not candidate:
        return None
    return list_feature_rules(candidate, rows=rows)


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
