"""core/lot_wip.py — "지금 어디 있어?" 결정적 WIP 현재위치 답변.

`lot_progress_cache` 의 latest cache 는 이미 **FAB DB 전 제품의 LOT_WF 현재
위치**(product, root_lot_id, wafer_id, step_id, function_step, 최종 이동시각)를
들고 있다. 이 모듈은 그 캐시만 읽어 parquet 재스캔 없이 두 가지 질문에 답한다.

  - lot 단위 — "A1000 지금 어디 있어", "A1000 #3 어느 step 이야"
  - product 단위 — "AAAAA 지금 어디 있어", "AAAAA WIP 어디까지 갔어"

step_id 는 항상 `Vehicle_matching.csv` 의 `step_desc` 와 합쳐 답한다. 사용자가
입으로 쓰는 이름은 flow 내부 `function_step` 이 아니라 매칭표의 `step_desc` 인
경우가 많아서, 둘 중 하나만 말하면 "그게 어디냐"는 되물음이 다시 온다.

**모든 답변에는 적재 지연 고지가 붙는다.** 이 값은 BigQuery 적재 → FAB DB 반영
→ flow 캐시 갱신을 차례로 거친 결과라 설비 실시간 현황이 아니다. 고지를 빼면
사용자가 이 답을 실시간으로 오해하고 설비 앞에서 잘못된 판단을 한다.
"""
from __future__ import annotations

import datetime as dt
import re
import threading
from typing import Any

from core import lot_progress_cache

# ── 의도 감지 ────────────────────────────────────────────────────────────────
# "어디" 계열은 그 자체로 현재 시점을 함의하므로 시제어 없이도 통과시킨다.
_LOCATION_TERMS = ("어디", "위치", "where", "location", "wip", "재공")
_STEP_TERMS = ("step", "스텝", "공정", "진행", "progress")
_NOW_TERMS = ("지금", "현재", "current", "now", "최신", "latest")
_ASK_TERMS = ("무슨", "어느", "어떤", "which")
# step_id ↔ function_step 매칭표 질문은 step_lookup 유닛의 몫이다. "step" 이 들어
# 있다는 이유로 WIP 조회가 가로채지 않도록 여기서 먼저 뺀다.
_MAPPING_QUESTION_RE = re.compile(
    r"(step[_\s-]?id|function[_\s-]?step|step[_\s-]?desc)\s*(가|이|는|은|을|를)?\s*(뭐|무엇|알려|찾)",
    re.IGNORECASE,
)

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]{2,}")
# 화면/기능 이름이 대상 후보로 잡혀 "그런 제품 없습니다" 를 뱉지 않도록 하는 제외어.
_NON_TARGET_TOKENS = {
    "SPLITTABLE", "SPLIT", "INFORM", "TRACKER", "DASHBOARD", "FILEBROWSER", "FLOW",
    "FLOWI", "STEP", "STEP_ID", "WAFER", "LOT", "LOT_ID", "ROOT", "ROOT_LOT_ID",
    "PRODUCT", "CACHE", "FAB", "WIP", "MEETING", "CALENDAR", "TABLEMAP", "REPORT",
}
_WAFER_RE = re.compile(r"(?:#|웨이퍼\s*|wafer\s*|wf\s*|slot\s*|슬롯\s*)0*(\d{1,2})(?![0-9])", re.IGNORECASE)

# 적재 지연 고지 — 문구를 바꿀 때는 tests/agent/test_lot_wip.py 도 같이 본다.
DELAY_NOTICE_HEAD = (
    "※ 이 값은 BigQuery 적재 → FAB DB 반영 → flow latest cache 갱신을 차례로 거친 "
    "결과라 설비 실시간 현황보다 늦습니다."
)
# FAB parquet 를 직접 훑는 경로(캐시 미경유)용 — 지연 사슬이 한 단계 짧다.
DELAY_NOTICE_HEAD_FAB = (
    "※ 이 값은 BigQuery 적재 → FAB DB 반영을 거친 값이라 설비 실시간 현황보다 늦습니다."
)

_VEHICLE_TTL_SEC = 300.0
_VEHICLE_LOCK = threading.Lock()
_VEHICLE_CACHE: tuple[float, dict, dict] | None = None

_TARGET_LOCK = threading.Lock()
_TARGET_CACHE: tuple[tuple[str, int], dict] | None = None


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        out = str(value)
    except Exception:
        return ""
    if out.strip().lower() in {"nan", "nat", "none", "null"}:
        return ""
    return out.strip()


def _up(value: Any) -> str:
    return _text(value).upper()


def _cell(row: dict, *names: str) -> str:
    """CSV 헤더 대소문자/공백 차이를 흡수하는 값 조회."""
    lookup = {str(key).strip().lower(): value for key, value in (row or {}).items()}
    for name in names:
        value = lookup.get(str(name).strip().lower())
        if _text(value):
            return _text(value)
    return ""


def is_wip_prompt(prompt: str) -> bool:
    """현재 위치/현재 step 을 묻는 질문인지."""
    text = _text(prompt)
    low = text.lower()
    if _MAPPING_QUESTION_RE.search(text):
        return False
    has_location = any(term in low for term in _LOCATION_TERMS)
    has_step = any(term in low for term in _STEP_TERMS)
    has_now = any(term in low for term in _NOW_TERMS)
    has_ask = any(term in low for term in _ASK_TERMS)
    return bool(has_location or (has_step and (has_now or has_ask)))


# ── Vehicle_matching.csv step_desc 색인 ──────────────────────────────────────
def _build_step_desc_index(rows: list[dict] | None = None) -> tuple[dict, dict]:
    if rows is None:
        try:
            from core import fab_reference
            rows = fab_reference.vehicle_matching_rows()
        except Exception:
            rows = []
    by_product_step: dict[tuple[str, str], dict[str, str]] = {}
    by_step: dict[str, dict[str, str]] = {}
    for row in rows or []:
        step_id = _up(_cell(row, "step_id", "raw_step_id", "step"))
        if not step_id:
            continue
        entry = {
            "step_desc": _cell(row, "step_desc", "step description", "step_description"),
            "vehicle": _cell(row, "vehicle", "vehicle_id"),
        }
        if not entry["step_desc"] and not entry["vehicle"]:
            continue
        for product in _product_cell_keys(_cell(row, "product", "process_id", "prod")):
            by_product_step[(product, step_id)] = entry
        by_step.setdefault(step_id, entry)
    return by_product_step, by_step


def _product_cell_keys(value: str) -> list[str]:
    text = _up(value)
    if not text:
        return []
    return [part.strip() for part in re.split(r"[,，、]", text) if part.strip()]


def step_desc_index(*, rows: list[dict] | None = None) -> tuple[dict, dict]:
    """(by (product, step_id), by step_id) 색인. 5분 TTL 메모.

    rows 를 명시 주입하면(hermetic 테스트) 캐시를 건너뛴다.
    """
    if rows is not None:
        return _build_step_desc_index(rows)
    global _VEHICLE_CACHE
    now = dt.datetime.now().timestamp()
    with _VEHICLE_LOCK:
        if _VEHICLE_CACHE and now - _VEHICLE_CACHE[0] <= _VEHICLE_TTL_SEC:
            return _VEHICLE_CACHE[1], _VEHICLE_CACHE[2]
    by_product_step, by_step = _build_step_desc_index(None)
    with _VEHICLE_LOCK:
        _VEHICLE_CACHE = (now, by_product_step, by_step)
    return by_product_step, by_step


def describe_step(step_id: str, product: str = "", *, index: tuple[dict, dict] | None = None) -> dict[str, str]:
    """step_id → {'step_desc', 'vehicle'}. 없으면 빈 문자열."""
    by_product_step, by_step = index or step_desc_index()
    key = _up(step_id)
    if not key:
        return {"step_desc": "", "vehicle": ""}
    entry = by_product_step.get((_up(product), key)) or by_step.get(key) or {}
    return {"step_desc": entry.get("step_desc", ""), "vehicle": entry.get("vehicle", "")}


def step_label(step_id: str, function_step: str = "", step_desc: str = "") -> str:
    """`AA100090 (SD_EPI · Gate Poly Etch)` 형태의 사람이 읽는 step 표기."""
    names = [name for name in (_text(function_step), _text(step_desc)) if name]
    # function_step 과 step_desc 가 같은 값이면 한 번만 쓴다.
    unique: list[str] = []
    for name in names:
        if name.upper() not in {u.upper() for u in unique}:
            unique.append(name)
    base = _text(step_id) or "-"
    return f"{base} ({' · '.join(unique)})" if unique else base


# ── 대상 해석 (제품/랏을 캐시 실데이터에 맞춰본다) ───────────────────────────
def _target_index(state: dict) -> dict:
    key = (_text(state.get("generated_at")), int(state.get("count") or len(state.get("items") or []) or 0))
    global _TARGET_CACHE
    with _TARGET_LOCK:
        if _TARGET_CACHE and _TARGET_CACHE[0] == key:
            return _TARGET_CACHE[1]
    products: dict[str, str] = {}
    roots: set[str] = set()
    lots: set[str] = set()
    lot_wfs: set[str] = set()
    for item in state.get("items") or []:
        if not isinstance(item, dict):
            continue
        product = _text(item.get("product"))
        if product:
            products.setdefault(product.upper(), product)
        process_id = _text(item.get("process_id"))
        if process_id:
            products.setdefault(process_id.upper(), process_id)
        if _up(item.get("root_lot_id")):
            roots.add(_up(item.get("root_lot_id")))
        if _up(item.get("lot_id")):
            lots.add(_up(item.get("lot_id")))
        if _up(item.get("lot_wf")):
            lot_wfs.add(_up(item.get("lot_wf")))
    index = {"products": products, "roots": roots, "lots": lots, "lot_wfs": lot_wfs}
    with _TARGET_LOCK:
        _TARGET_CACHE = (key, index)
    return index


def _wafer_ids(prompt: str) -> list[str]:
    out: list[str] = []
    for match in _WAFER_RE.finditer(_text(prompt)):
        value = str(int(match.group(1)))
        if value not in out:
            out.append(value)
    return out


def resolve_target(prompt: str, state: dict, product_hint: str = "") -> dict[str, Any]:
    """prompt 안의 토큰을 캐시 실데이터(제품/랏 목록)에 맞춰 대상을 정한다.

    휴리스틱 정규식으로 랏처럼 생겼는지 추측하지 않고 캐시에 실제로 있는 값과
    대조한다 — 제품명과 root_lot_id 가 둘 다 5자 영숫자라 모양만으로는 구분이
    안 되기 때문이다.
    """
    index = _target_index(state)
    raw_tokens = _TOKEN_RE.findall(_text(prompt))
    tokens = [tok.upper() for tok in raw_tokens]
    target = {
        "kind": "",
        "value": "",
        "product": _text(product_hint),
        "root_lot_id": "",
        "lot_id": "",
        "lot_wf": "",
        "wafer_ids": _wafer_ids(prompt),
        "unmatched_tokens": [],
    }
    for token in tokens:
        if token in index["lot_wfs"]:
            target.update({"kind": "lot", "value": token, "lot_wf": token})
            return target
    for token in tokens:
        if token in index["lots"]:
            target.update({"kind": "lot", "value": token, "lot_id": token})
            return target
    for token in tokens:
        if token in index["roots"]:
            target.update({"kind": "lot", "value": token, "root_lot_id": token})
            return target
    for token in tokens:
        if token in index["products"]:
            target.update({"kind": "product", "value": index["products"][token],
                           "product": index["products"][token]})
            return target
    if target["product"] and _up(target["product"]) in index["products"]:
        target.update({"kind": "product", "value": index["products"][_up(target["product"])]})
        return target
    target["unmatched_tokens"] = _identifier_candidates(raw_tokens)
    return target


def _identifier_candidates(raw_tokens: list[str]) -> list[str]:
    """식별자처럼 생긴 토큰만 남긴다.

    "SplitTable 어디서 봐?" 같은 기능 안내 질문을 "그런 제품 없습니다" 로 가로채지
    않으려는 필터다. 숫자를 포함하거나(A1000, AA100090) 원문이 전부 대문자인
    (AAAAA) 4자 이상 토큰만 대상 후보로 본다.
    """
    out: list[str] = []
    for raw in raw_tokens or []:
        token = raw.upper()
        if len(token) < 4 or token in _NON_TARGET_TOKENS or token in out:
            continue
        if not (any(ch.isdigit() for ch in token) or raw.isupper()):
            continue
        out.append(token)
        if len(out) >= 5:
            break
    return out


# ── 지연 고지 ────────────────────────────────────────────────────────────────
def _parse_time(value: Any) -> dt.datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y%m%d%H%M%S", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(text[:len(fmt) + 4], fmt)
        except Exception:
            continue
    return None


def _age_text(value: Any, *, now: dt.datetime | None = None) -> str:
    parsed = _parse_time(value)
    if parsed is None:
        return ""
    seconds = ((now or dt.datetime.now()) - parsed).total_seconds()
    if seconds < 0:
        return "방금"
    minutes = int(seconds // 60)
    if minutes < 1:
        return "방금"
    if minutes < 60:
        return f"{minutes}분 전"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}시간 {minutes % 60}분 전"
    return f"{hours // 24}일 {hours % 24}시간 전"


def ingest_delay_notice(
    *,
    source: str = "cache",
    cache_generated_at: str = "",
    latest_move: str = "",
    now: dt.datetime | None = None,
) -> str:
    """현재위치 답변에 붙는 적재 지연 고지.

    `source="cache"` 는 flow latest cache 를 읽은 답(캐시 기준시각·갱신주기 포함),
    `source="fab"` 은 FAB parquet 를 직접 훑은 답이다. FAB 경로도 BigQuery 적재
    지연은 그대로 안고 있으므로 고지를 생략하지 않는다.
    """
    from_cache = str(source or "cache").lower() != "fab"
    parts = [DELAY_NOTICE_HEAD if from_cache else DELAY_NOTICE_HEAD_FAB]
    if from_cache:
        if cache_generated_at:
            age = _age_text(cache_generated_at, now=now)
            parts.append(f"캐시 기준시각 {cache_generated_at}{f' ({age})' if age else ''}")
        else:
            parts.append("캐시가 아직 한 번도 만들어지지 않았습니다")
    if latest_move:
        age = _age_text(latest_move, now=now)
        label = "이 랏의 최종 이동시각" if from_cache else "이 행의 최종 tkout_time"
        parts.append(f"{label} {latest_move}{f' ({age})' if age else ''}")
    if from_cache:
        try:
            interval = lot_progress_cache.lot_progress_cache_refresh_minutes()
            parts.append(f"캐시 갱신주기 {interval}분")
        except Exception:
            pass
    parts.append("실시간 확인이 필요하면 FAB 시스템을 직접 보세요")
    return " · ".join(parts) + "."


def delay_notice(state: dict, *, latest_move: str = "", now: dt.datetime | None = None) -> str:
    """latest cache 기반 답변용 지연 고지."""
    return ingest_delay_notice(
        source="cache",
        cache_generated_at=_text((state or {}).get("generated_at")),
        latest_move=latest_move,
        now=now,
    )


# ── 해석 문장 · 재현 SQL ─────────────────────────────────────────────────────
# 답변 본문에 step 을 몇 개까지 적을지. 나머지는 표와 재현 SQL 로 넘긴다.
_TOP_STEPS = 8


def _vehicle_matching_file() -> str:
    try:
        from core import fab_reference
        from core.paths import PATHS
        # VEHICLE_MATCHING_FILE 은 파일명 상수라 db_root 를 붙여야 출처가 된다.
        return str(PATHS.db_root / fab_reference.VEHICLE_MATCHING_FILE)
    except Exception:
        return ""


def _sql_literal(value: Any) -> str:
    return "'" + _text(value).replace("'", "''") + "'"


def target_conditions(target: dict) -> list[str]:
    """대상 해석을 사람이 읽는 조건 목록으로. 해석 문장과 SQL WHERE 가 같은 곳에서 나온다."""
    out: list[str] = []
    if _text(target.get("lot_wf")):
        out.append(f"LOT_WF={_text(target['lot_wf'])}")
    elif _text(target.get("lot_id")):
        out.append(f"lot_id={_text(target['lot_id'])}")
    elif _text(target.get("root_lot_id")):
        out.append(f"root_lot_id={_text(target['root_lot_id'])}")
    elif _text(target.get("product")):
        out.append(f"product={_text(target['product'])}")
    wafers = [str(w) for w in (target.get("wafer_ids") or [])]
    if wafers:
        out.append("wafer_id=" + ",".join(wafers))
    return out


def interpretation_line(target: dict) -> str:
    """"질문을 이렇게 알아들었다" 한 줄. 대상을 잘못 잡았을 때 사용자가 바로 안다."""
    conditions = target_conditions(target)
    if not conditions:
        return ""
    scope = "제품 단위" if target.get("kind") == "product" else "랏 단위"
    return f"해석 — {' · '.join(conditions)} ({scope}) 로 보고 latest cache 의 현재 위치를 찾았습니다."


# IN 목록이 이보다 길어지면 SQL 이 읽을 수 없게 된다 — 그때는 상위 조건으로 되돌린다.
_SQL_IN_LIMIT = 20


def _column_values(rows: list[dict] | None, column: str) -> list[str]:
    return sorted({_text(row.get(column)) for row in (rows or []) if _text(row.get(column))})


def _sql_in(column: str, values: list[str]) -> str:
    if len(values) == 1:
        return f"{column} = {_sql_literal(values[0])}"
    return f"{column} IN (" + ", ".join(_sql_literal(v) for v in values) + ")"


def _sql_where(target: dict, rows: list[dict] | None = None) -> str:
    """조회된 행에 실제로 있는 값으로 WHERE 를 만든다.

    **parquet export 에는 `process_id` 도 `lot_wf` 도 없다** (product, root_lot_id,
    wafer_id, lot_id, step_id, function_step, tkout_time, update_time, lot_type).
    그래서 대상 토큰을 그대로 옮겨 적으면 실행되지 않는 SQL 이 나온다 — 매칭된
    행의 컬럼 값으로 옮겨야 실행도 되고 답변과 같은 집합이 나온다.
    """
    clauses: list[str] = []
    if target.get("kind") == "product":
        products = _column_values(rows, "product")
        clauses.append(_sql_in("product", products) if 0 < len(products) <= _SQL_IN_LIMIT
                       else f"product = {_sql_literal(target.get('product'))}")
    elif _text(target.get("lot_id")):
        lots = _column_values(rows, "lot_id")
        clauses.append(_sql_in("lot_id", lots) if 0 < len(lots) <= _SQL_IN_LIMIT
                       else f"lot_id = {_sql_literal(target['lot_id'])}")
    else:
        # lot_wf(A1000_3) 도 결국 root + wafer 로 풀어 써야 parquet 에서 돈다.
        roots = _column_values(rows, "root_lot_id") or [_text(target.get("root_lot_id"))]
        roots = [r for r in roots if r]
        if roots and len(roots) <= _SQL_IN_LIMIT:
            clauses.append(_sql_in("root_lot_id", roots))
    wafers = [str(w) for w in (target.get("wafer_ids") or [])]
    if not wafers and _text(target.get("lot_wf")):
        wafers = _column_values(rows, "wafer_id")
    if wafers and len(wafers) <= _SQL_IN_LIMIT:
        clauses.append(_sql_in("wafer_id", wafers))
    return "\n  AND ".join(clause for clause in clauses if clause)


def reproduce_sql(target: dict, rows: list[dict] | None = None) -> str:
    """이 답을 그대로 재현하는 read-only SQL.

    latest cache 는 JSON 이지만 같은 내용이 `lot_wf_current.parquet` 로 export 되어
    있어서, 사용자가 FileBrowser 에서 그대로 돌려 검증할 수 있다. 답변 근거를
    "캐시가 그렇다더라"로 끝내지 않으려는 것.

    **parquet 의 `update_time` 은 캐시를 만든 시각이고 실제 이동시각은 `tkout_time`
    이다.** 답변의 "최종 이동" 과 같은 값이 나오도록 tkout_time 을 쓴다.
    """
    where = _sql_where(target, rows)
    if not where:
        return ""
    try:
        path = str(lot_progress_cache.cache_parquet_file()).replace("\\", "/")
    except Exception:
        return ""
    if target.get("kind") == "product":
        return (
            "SELECT step_id, function_step,\n"
            "       COUNT(DISTINCT root_lot_id) AS lot_count,\n"
            "       COUNT(*) AS wafer_count,\n"
            "       MAX(tkout_time) AS latest_move\n"
            f"FROM read_parquet('{path}')\n"
            f"WHERE {where}\n"
            "GROUP BY step_id, function_step\n"
            "ORDER BY wafer_count DESC"
        )
    return (
        "SELECT product, root_lot_id, lot_id, wafer_id, step_id, function_step,\n"
        "       tkout_time AS latest_move\n"
        f"FROM read_parquet('{path}')\n"
        f"WHERE {where}\n"
        "ORDER BY root_lot_id, TRY_CAST(wafer_id AS INTEGER)"
    )


# ── 답변 생성 ────────────────────────────────────────────────────────────────
def _row_time(row: dict) -> str:
    return _text(row.get("update_time") or row.get("tkout_time") or row.get("tkin_time") or row.get("time"))


def _wafer_sort(value: Any) -> int:
    try:
        return int(re.sub(r"[^0-9]", "", _text(value)) or 0)
    except Exception:
        return 999999


def _matching_rows(state: dict, target: dict) -> list[dict]:
    rows: list[dict] = []
    lot_wf = _up(target.get("lot_wf"))
    lot_id = _up(target.get("lot_id"))
    root = _up(target.get("root_lot_id"))
    product = _up(target.get("product"))
    wafers = {str(w) for w in (target.get("wafer_ids") or [])}
    for item in state.get("items") or []:
        if not isinstance(item, dict):
            continue
        if lot_wf:
            if _up(item.get("lot_wf")) != lot_wf:
                continue
        elif lot_id:
            if lot_id not in {_up(item.get("lot_id")), _up(item.get("root_lot_id"))}:
                continue
        elif root:
            if _up(item.get("root_lot_id")) != root:
                continue
        elif product:
            if product not in {_up(item.get("product")), _up(item.get("process_id"))}:
                continue
        else:
            continue
        if wafers and _text(item.get("wafer_id")) not in wafers:
            continue
        rows.append(item)
    return rows


def _step_groups(rows: list[dict], index: tuple[dict, dict]) -> list[dict]:
    groups: dict[tuple[str, str], dict] = {}
    for row in rows:
        step_id = _text(row.get("step_id"))
        product = _text(row.get("product"))
        desc = describe_step(step_id, product, index=index)
        key = (_up(step_id), _up(product))
        group = groups.setdefault(key, {
            "step_id": step_id,
            "product": product,
            "function_step": _text(row.get("function_step") or row.get("func_step")),
            "step_desc": desc["step_desc"],
            "vehicle": desc["vehicle"],
            "wafer_ids": [],
            "root_lot_ids": [],
            "latest_move": "",
        })
        wafer = _text(row.get("wafer_id"))
        if wafer:
            group["wafer_ids"].append(wafer)
        root = _text(row.get("root_lot_id"))
        if root and root not in group["root_lot_ids"]:
            group["root_lot_ids"].append(root)
        moved = _row_time(row)
        if moved > group["latest_move"]:
            group["latest_move"] = moved
    out = list(groups.values())
    for group in out:
        group["wafer_ids"].sort(key=_wafer_sort)
        group["wafer_count"] = len(group["wafer_ids"])
        group["lot_count"] = len(group["root_lot_ids"])
        group["wafer_label"] = lot_progress_cache.compress_wafer_ids(group["wafer_ids"])
        group["step_label"] = step_label(group["step_id"], group["function_step"], group["step_desc"])
    out.sort(key=lambda g: (-g["wafer_count"], g["step_id"]))
    return out


def _lot_answer(target: dict, groups: list[dict]) -> str:
    label = target.get("value") or target.get("root_lot_id") or target.get("lot_id") or "해당 랏"
    if len(groups) == 1:
        group = groups[0]
        bits = [f"{label} 은(는) 지금 {group['step_label']} 에 있습니다"]
        if group["wafer_label"]:
            bits.append(f"웨이퍼 {group['wafer_label']} ({group['wafer_count']}장)")
        if group["vehicle"]:
            bits.append(f"vehicle {group['vehicle']}")
        if group["latest_move"]:
            bits.append(f"최종 이동 {group['latest_move']}")
        return ", ".join(bits) + "."
    # 여러 step 에 걸친 랏은 **웨이퍼 번호를 나열하지 않는다** — step 이 수십 개면
    # 화면이 번호로 뒤덮여 정작 어디에 몰려 있는지가 안 보인다. 요약은 건수까지,
    # 웨이퍼 단위 상세는 아래 표(그리고 재현 SQL)의 몫이다.
    wafers = sum(group["wafer_count"] for group in groups)
    lines = [
        f"{label} 은(는) 웨이퍼 {wafers:,}장이 {len(groups)}개 step 에 나뉘어 있습니다. "
        "웨이퍼가 많은 step 순으로:"
    ]
    for group in groups[:_TOP_STEPS]:
        detail = f"- {group['step_label']}: {group['wafer_count']:,}장"
        if group["latest_move"]:
            detail += f", 최종 이동 {group['latest_move']}"
        lines.append(detail)
    if len(groups) > _TOP_STEPS:
        lines.append(f"… 외 {len(groups) - _TOP_STEPS}개 step (웨이퍼 단위 상세는 아래 표)")
    return "\n".join(lines)


def _product_answer(target: dict, rows: list[dict], groups: list[dict], top: int) -> str:
    label = target.get("value") or target.get("product") or "해당 제품"
    lots = {_up(row.get("root_lot_id")) for row in rows if _up(row.get("root_lot_id"))}
    lines = [
        f"{label} 의 현재 WIP 은 {len(lots):,} lot / {len(rows):,} wafer 이고, "
        f"{len(groups):,}개 step 에 퍼져 있습니다."
    ]
    for group in groups[:top]:
        lines.append(
            f"- {group['step_label']}: {group['lot_count']} lot / {group['wafer_count']} wafer"
            + (f", 최종 이동 {group['latest_move']}" if group["latest_move"] else "")
        )
    if len(groups) > top:
        lines.append(f"… 외 {len(groups) - top}개 step")
    return "\n".join(lines)


def _lot_table(rows: list[dict], index: tuple[dict, dict], max_rows: int) -> dict:
    ordered = sorted(rows, key=lambda row: (_up(row.get("root_lot_id")), _wafer_sort(row.get("wafer_id"))))
    out_rows = []
    for row in ordered[:max_rows]:
        desc = describe_step(row.get("step_id"), row.get("product"), index=index)
        out_rows.append({
            "product": _text(row.get("product")),
            "root_lot_id": _text(row.get("root_lot_id")),
            "lot_id": _text(row.get("lot_id")),
            "wafer_id": _text(row.get("wafer_id")),
            "step_id": _text(row.get("step_id")),
            "function_step": _text(row.get("function_step") or row.get("func_step")),
            "step_desc": desc["step_desc"],
            "vehicle": desc["vehicle"],
            "update_time": _row_time(row),
        })
    columns = ["product", "root_lot_id", "lot_id", "wafer_id", "step_id",
               "function_step", "step_desc", "vehicle", "update_time"]
    columns = [col for col in columns if any(row.get(col) for row in out_rows)] or columns
    return {
        "kind": "lot_wip_location",
        "title": "현재 위치 (latest cache)",
        "placement": "below",
        "columns": columns,
        "rows": [{col: row.get(col, "") for col in columns} for row in out_rows],
        "total": len(rows),
        "source": "lot_progress_latest_cache",
    }


def _product_table(groups: list[dict], max_rows: int) -> dict:
    columns = ["step_id", "function_step", "step_desc", "vehicle", "lot_count", "wafer_count", "latest_move"]
    out_rows = [
        {
            "step_id": group["step_id"],
            "function_step": group["function_step"],
            "step_desc": group["step_desc"],
            "vehicle": group["vehicle"],
            "lot_count": group["lot_count"],
            "wafer_count": group["wafer_count"],
            "latest_move": group["latest_move"],
        }
        for group in groups[:max_rows]
    ]
    columns = [col for col in columns if any(_text(row.get(col)) for row in out_rows)] or columns
    return {
        "kind": "lot_wip_product",
        "title": "제품 WIP 분포 (latest cache)",
        "placement": "below",
        "columns": columns,
        "rows": [{col: row.get(col, "") for col in columns} for row in out_rows],
        "total": len(groups),
        "source": "lot_progress_latest_cache",
    }


def answer_wip(
    prompt: str,
    *,
    product: str = "",
    max_rows: int = 30,
    state: dict | None = None,
    vehicle_rows: list[dict] | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any] | None:
    """WIP 현재위치 질문에 대한 결정적 답변. WIP 질문이 아니면 None.

    대상을 못 찾으면 `low_confidence=True` 를 붙여 돌려준다 — 호출측(오케스트
    레이터/handler chain)이 다른 도구에 양보할 수 있게 하되, 아무도 못 받으면
    지연 고지가 붙은 안내라도 남게 하려는 것이다.
    """
    if not is_wip_prompt(prompt):
        return None
    if state is None:
        # allow_stale=True — 요청 안에서 FAB 풀스캔을 트리거하지 않는다.
        state = lot_progress_cache.read_lot_progress_cache(allow_stale=True)
    index = step_desc_index(rows=vehicle_rows)
    target = resolve_target(prompt, state, product_hint=product)
    base = {
        "handled": True,
        "type": "answer",
        "intent": "lot_wip_location",
        "action": "query_lot_wip_from_latest_cache",
        "feature": "filebrowser",
        "unit_ai": "lot_wip",
        "cache_generated_at": _text(state.get("generated_at")),
        "source": "lot_progress_latest_cache",
        # step_desc 는 Vehicle_matching.csv 에서 붙인 값이라 출처에 같이 밝힌다.
        "source_ids": [str(path) for path in (lot_progress_cache.cache_file(), _vehicle_matching_file()) if path],
        "source_detail": {
            "kind": "latest_cache",
            "path": str(lot_progress_cache.cache_file()),
            "generated_at": _text(state.get("generated_at")),
        },
    }

    if not (state.get("items") or []):
        if not target["unmatched_tokens"]:
            return None
        return {
            **base,
            "answer": "전 제품 WIP latest cache 가 아직 비어 있습니다. 관리자 화면에서 "
                      "LOT progress 캐시를 갱신한 뒤 다시 물어봐 주세요.\n\n"
                      + delay_notice(state, now=now),
            "delay_notice": delay_notice(state, now=now),
            "table": {},
            "low_confidence": True,
        }

    if not target["kind"]:
        if not target["unmatched_tokens"]:
            # 대상 후보조차 없는 질문("어디서 보나요?" 류)은 기능 안내 쪽 일이다.
            return None
        hint = ", ".join(target["unmatched_tokens"])
        return {
            **base,
            "answer": f"latest cache 에서 '{hint}' 에 해당하는 제품이나 랏을 찾지 못했습니다. "
                      "제품명(예: 제품 폴더명)이나 root_lot_id / fab lot_id 로 다시 물어봐 주세요.\n\n"
                      + delay_notice(state, now=now),
            "delay_notice": delay_notice(state, now=now),
            "table": {},
            "target": target,
            "low_confidence": True,
        }

    rows = _matching_rows(state, target)
    if not rows:
        notice = delay_notice(state, now=now)
        return {
            **base,
            "answer": f"{target['value']} 은(는) latest cache 에 현재 위치 행이 없습니다. "
                      "이미 out 되었거나 아직 적재되지 않았을 수 있습니다.\n\n" + notice,
            "delay_notice": notice,
            "table": {},
            "target": target,
            "low_confidence": True,
        }

    groups = _step_groups(rows, index)
    latest_move = max((_row_time(row) for row in rows), default="")
    notice = delay_notice(state, latest_move=latest_move if target["kind"] == "lot" else "", now=now)

    if target["kind"] == "lot":
        answer = _lot_answer(target, groups)
        table = _lot_table(rows, index, max_rows)
        lot_list = [
            {
                "product": _text(row.get("product")),
                "root_lot": _text(row.get("root_lot_id")),
                "fab_lot": _text(row.get("lot_id")),
                "wafer": _text(row.get("wafer_id")),
                "current_step": _text(row.get("step_id")),
                "current_function_step": _text(row.get("function_step") or row.get("func_step")),
                "current_step_desc": describe_step(row.get("step_id"), row.get("product"), index=index)["step_desc"],
                "tkout_time": _row_time(row),
            }
            for row in sorted(rows, key=lambda r: _wafer_sort(r.get("wafer_id")))[:max_rows]
        ]
    else:
        answer = _product_answer(target, rows, groups, top=_TOP_STEPS)
        table = _product_table(groups, max_rows)
        lot_list = []

    interpretation = interpretation_line(target)
    sql = reproduce_sql(target, rows)
    payload = {
        **base,
        "answer": "\n\n".join(part for part in (interpretation, answer, notice) if part),
        "interpretation": interpretation,
        "sql": sql,
        "delay_notice": notice,
        "target": target,
        "table": table,
        "step_groups": [
            {key: group[key] for key in
             ("step_id", "function_step", "step_desc", "vehicle", "wafer_count", "lot_count", "latest_move", "step_label")}
            for group in groups[:max_rows]
        ],
        "filters": {
            "product": target.get("product") or "",
            "root_lot_ids": [target["root_lot_id"]] if target.get("root_lot_id") else [],
            "lot_ids": [target["lot_id"]] if target.get("lot_id") else [],
            "wafer_ids": list(target.get("wafer_ids") or []),
            "source": "lot_progress_latest_cache",
            # 카드의 "출처" 블록이 filters.sql 을 재현 SQL 로 렌더링한다
            # (frontend/src/pages/My_Home.jsx FlowiSourceEvidence).
            "sql": sql,
        },
    }
    if lot_list:
        payload["lot_list"] = lot_list
    return payload
