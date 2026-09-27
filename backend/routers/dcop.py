"""DCOP 검사 공용 규칙 저장 API.

규칙은 배포 소스와 분리된 ``FLOW_DATA_ROOT/dcop/settings.json``에 저장한다.
따라서 setup.py 업데이트/재배포 뒤에도 그대로 유지된다. 읽기는 로그인 사용자,
쓰기는 global admin 또는 dcop page manager만 허용한다.

연결된 LLM으로 규칙 초안을 만드는 ``/rules/llm/draft``는 global admin 전용이다.
초안은 저장하지 않고 돌려주기만 하며, 관리자가 톱니바퀴에서 골라 추가하면
기존 ``PUT /settings`` 경로로 저장된다(``source: "llm"``으로 출처를 남긴다).
"""
from __future__ import annotations

import datetime
import json
import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.audit import record_user as _audit_user
from core.auth import current_user, is_page_manager, require_admin, require_page_manager
from core.paths import PATHS
from core.utils import load_json, save_json

router = APIRouter(prefix="/api/dcop", tags=["dcop"])

SCHEMA_VERSION = 1
MAX_RULES = 500
MAX_TEXT = 10_000
ALLOWED_OPERATORS = {
    "blank", "unique", "max_length", "comma_count_equals_column", "numeric",
    "max_decimal_places", "allowed_values", "not_blank", "equals", "not_equals",
    "contains", "not_contains", "in", "gt", "gte", "lt", "lte", "regex",
    "order_asc", "unclosed_quotes",
}
MULTI_COLUMN_OPERATORS = {"blank", "unique", "order_asc"}


def settings_file():
    return PATHS.data_root / "dcop" / "settings.json"


def _text(value: Any, limit: int = MAX_TEXT) -> str:
    return str(value or "").strip()[:limit]


def _enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() not in {"", "0", "false", "no", "off"}


def _normalize_rule(raw: Any, seen_ids: set[str]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    operator = _text(raw.get("operator"), 80)
    if operator not in ALLOWED_OPERATORS:
        return None
    rule_id = _text(raw.get("id"), 200)
    if not rule_id or rule_id in seen_ids:
        rule_id = uuid.uuid4().hex
    seen_ids.add(rule_id)
    unique_columns: list[str] = []
    unique_seen: set[str] = set()
    source_columns = raw.get("uniqueColumns")
    if not isinstance(source_columns, list):
        source_columns = []
    for value in source_columns[:100]:
        column = _text(value, 500)
        key = column.casefold()
        if column and key not in unique_seen:
            unique_seen.add(key)
            unique_columns.append(column)
    unique_text = _text(raw.get("uniqueColumnsText"), 10_000)
    if operator in MULTI_COLUMN_OPERATORS and not unique_columns and unique_text:
        for value in unique_text.split(",")[:100]:
            column = _text(value, 500)
            key = column.casefold()
            if column and key not in unique_seen:
                unique_seen.add(key)
                unique_columns.append(column)
    severity = _text(raw.get("severity"), 20).casefold()
    rule = {
        "id": rule_id,
        "column": _text(raw.get("column"), 500),
        "operator": operator,
        "value": _text(raw.get("value")),
        "compareColumn": _text(raw.get("compareColumn"), 500),
        "uniqueColumns": unique_columns,
        "uniqueColumnsText": unique_text or ", ".join(unique_columns),
        "severity": severity if severity in {"warning", "fail"} else "fail",
        "message": _text(raw.get("message")),
        "enabled": _enabled(raw.get("enabled", True)),
    }
    # 출처 표시는 AI 초안에서 추가한 규칙에만 남긴다(수동 규칙 모양은 그대로).
    if _text(raw.get("source"), 20) == "llm":
        rule["source"] = "llm"
    return rule


def normalize_settings(raw: Any) -> dict[str, list[dict[str, Any]]]:
    source = raw if isinstance(raw, dict) else {}
    rules = source.get("rules")
    if not isinstance(rules, list):
        rules = []
    seen_ids: set[str] = set()
    normalized = []
    for raw_rule in rules[:MAX_RULES]:
        rule = _normalize_rule(raw_rule, seen_ids)
        if rule is not None:
            normalized.append(rule)
    return {"rules": normalized}


def load_document() -> tuple[dict[str, Any], bool]:
    path = settings_file()
    exists = path.is_file()
    raw = load_json(path, {}) if exists else {}
    # 초기 개발판에서 settings 자체를 최상위에 저장한 파일도 읽는다.
    source = raw.get("settings") if isinstance(raw, dict) and isinstance(raw.get("settings"), dict) else raw
    return {
        "schema_version": SCHEMA_VERSION,
        "settings": normalize_settings(source),
        "updated_at": _text(raw.get("updated_at")) if isinstance(raw, dict) else "",
        "updated_by": _text(raw.get("updated_by"), 200) if isinstance(raw, dict) else "",
    }, exists


def settings_payload(user: dict) -> dict[str, Any]:
    document, exists = load_document()
    return {
        "ok": True,
        "exists": exists,
        "can_edit": is_page_manager(user, "dcop"),
        "store": "flow-data/dcop/settings.json",
        **document,
    }


class SettingsReq(BaseModel):
    settings: dict[str, Any]


@router.get("/settings")
def settings_get(user=Depends(current_user)):
    return settings_payload(user)


@router.put("/settings")
def settings_put(req: SettingsReq, user=Depends(require_page_manager("dcop"))):
    raw_rules = req.settings.get("rules") if isinstance(req.settings, dict) else None
    if not isinstance(raw_rules, list):
        raise HTTPException(400, "settings.rules는 배열이어야 합니다")
    if len(raw_rules) > MAX_RULES:
        raise HTTPException(400, f"DCOP 검사 규칙은 최대 {MAX_RULES}개까지 저장할 수 있습니다")
    settings = normalize_settings(req.settings)
    if len(settings["rules"]) != len(raw_rules):
        raise HTTPException(400, "지원하지 않거나 형식이 잘못된 DCOP 검사 규칙이 있습니다")
    document = {
        "schema_version": SCHEMA_VERSION,
        "settings": settings,
        "updated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "updated_by": _text(user.get("username"), 200),
    }
    save_json(settings_file(), document, indent=2)
    _audit_user(document["updated_by"], "dcop:settings_save",
                detail=f"rules={len(settings['rules'])}", tab="dcop")
    return {
        "ok": True,
        "exists": True,
        "can_edit": True,
        "store": "flow-data/dcop/settings.json",
        **document,
    }


# ── 연결된 LLM으로 규칙 초안 만들기 (global admin 전용) ─────────────────────
# 화면(My_DcopCheck.jsx)의 OPERATORS 와 같은 뜻이어야 한다. 모든 연산자는
# "위반 조건"이다 — 조건에 걸린 행이 FAIL/WARNING 이 된다.
OPERATOR_GUIDE = {
    "blank": "columns 중 하나라도 비어 있으면 위반 (필수값)",
    "unique": "columns 조합 값이 두 행 이상에서 같으면 위반 (유일성)",
    "max_length": "column 글자 수가 value(정수)보다 길면 위반",
    "comma_count_equals_column": "column 의 쉼표 개수+1 이 compareColumn 의 정수값과 다르면 위반",
    "numeric": "column 값이 숫자 형식이 아니면 위반",
    "max_decimal_places": "column 소수 자릿수가 value(정수)보다 많으면 위반",
    "allowed_values": "column 값이 value(쉼표 구분 허용값 목록)에 없으면 위반",
    "not_blank": "column 에 값이 있으면 위반 (비워 둬야 하는 열)",
    "equals": "column 값이 value 와 같으면 위반 (대소문자 무시)",
    "not_equals": "column 값이 value 와 다르면 위반 (대소문자 무시)",
    "contains": "column 값이 value 를 포함하면 위반",
    "not_contains": "column 값이 value 를 포함하지 않으면 위반",
    "in": "column 값이 value(쉼표 구분 목록) 중 하나이면 위반 (금지값 목록)",
    "gt": "column 숫자가 value 보다 크면 위반",
    "gte": "column 숫자가 value 이상이면 위반",
    "lt": "column 숫자가 value 보다 작으면 위반",
    "lte": "column 숫자가 value 이하이면 위반",
    "regex": "column 값이 value 정규식과 일치하면 위반 (허용 패턴이면 부정 lookahead 로 쓴다)",
    "order_asc": "columns 를 적힌 순서대로 숫자로 비교해 A≤B≤C 가 아니면 위반 (2개 이상)",
    "unclosed_quotes": "column 값에 닫히지 않은 큰따옴표 또는 작은따옴표가 있으면 위반",
}
NUMERIC_VALUE_OPERATORS = {"max_length", "max_decimal_places", "gt", "gte", "lt", "lte"}
INTEGER_VALUE_OPERATORS = {"max_length", "max_decimal_places"}
LIST_VALUE_OPERATORS = {"allowed_values", "in"}
VALUE_OPERATORS = NUMERIC_VALUE_OPERATORS | LIST_VALUE_OPERATORS | {
    "equals", "not_equals", "contains", "not_contains", "regex",
}
MAX_DRAFT_RULES = 30
MAX_PROMPT = 4000
LLM_DRAFT_SCHEMA = {
    "type": "object",
    "required": ["rules"],
    "properties": {
        "rules": {"type": "array"},
        "warnings": {"type": "array"},
    },
}
LLM_DRAFT_SYSTEM = (
    "당신은 반도체 양산 DCOP 작성표의 데이터 품질 검사 규칙 설계자입니다. "
    "사용자 설명을 Flow DCOP 검사 규칙 JSON 으로 바꿉니다. 모든 규칙은 '위반 조건'입니다. "
    "operator 는 operator_guide 의 키만 쓰고, 열 이름은 가능하면 columns 에 있는 이름을 그대로 씁니다. "
    "여러 열을 받는 operator(blank, unique, order_asc)는 columns 배열을, 나머지는 column 을 채웁니다. "
    "value 는 문자열이며 숫자 기준값은 숫자만 씁니다. severity 는 fail 또는 warning 입니다. "
    "message 는 위반 시 사용자에게 보일 짧은 한국어 문장입니다. "
    "existing_rules 와 같은 규칙은 다시 만들지 않습니다. "
    "설명으로 만들 수 없는 조건은 규칙 대신 warnings 에 이유를 적습니다. "
    "파일, 코드, 경로, 명령은 쓰지 않습니다."
)


class RuleDraftReq(BaseModel):
    prompt: str
    columns: list[str] = Field(default_factory=list)
    sample_rows: list[list[Any]] = Field(default_factory=list)


def _rule_signature(rule: dict[str, Any]) -> str:
    operator = rule.get("operator")
    columns = rule.get("uniqueColumns") if operator in MULTI_COLUMN_OPERATORS else None
    if not columns:
        columns = [rule.get("column")]
    names = [str(c or "").strip().casefold() for c in columns]
    if operator in {"blank", "unique"}:
        names = sorted(names)
    return json.dumps([
        operator, names,
        str(rule.get("value") or "").strip().casefold(),
        str(rule.get("compareColumn") or "").strip().casefold(),
    ], ensure_ascii=False)


def _column_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value[:200]:
        name = _text(item, 500)
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            out.append(name)
    return out


def _is_number(value: str) -> bool:
    try:
        return float(value) == float(value)  # NaN 거부
    except (TypeError, ValueError, OverflowError):
        return False


def draft_rule_from_llm(raw: Any, known_columns: list[str], warnings: list[str]) -> dict[str, Any] | None:
    """LLM 이 낸 규칙 1개를 화면 규칙 모양으로 바꾼다. 실행할 수 없으면 None."""
    if not isinstance(raw, dict):
        return None
    operator = _text(raw.get("operator"), 80)
    if operator not in ALLOWED_OPERATORS:
        warnings.append(f"지원하지 않는 검사 방식이라 제외: {operator or '(비어 있음)'}")
        return None
    columns = _column_list(raw.get("columns") or raw.get("uniqueColumns"))[:100]
    column = _text(raw.get("column"), 500)
    if operator in MULTI_COLUMN_OPERATORS:
        if not columns and column:
            columns = [column]
        if not columns or (operator == "order_asc" and len(columns) < 2):
            warnings.append(f"{operator}: 검사할 열 이름이 부족해 제외")
            return None
        column = columns[0]
    else:
        column = column or (columns[0] if columns else "")
        columns = []
        if not column:
            warnings.append(f"{operator}: 검사할 열 이름이 없어 제외")
            return None

    value_raw = raw.get("value")
    if isinstance(value_raw, list):
        value = ",".join(_text(item, 500) for item in value_raw if _text(item, 500))
    elif isinstance(value_raw, float):
        value = format(value_raw, "g")
    elif isinstance(value_raw, int) and not isinstance(value_raw, bool):
        value = str(value_raw)
    else:
        value = _text(value_raw)
    compare_column = _text(raw.get("compareColumn") or raw.get("compare_column"), 500)
    if operator in VALUE_OPERATORS and not value:
        warnings.append(f"{column}: {operator} 기준값이 없어 제외")
        return None
    if operator in NUMERIC_VALUE_OPERATORS and not _is_number(value):
        warnings.append(f"{column}: {operator} 기준값 '{value}'이 숫자가 아니라 제외")
        return None
    if operator in INTEGER_VALUE_OPERATORS:
        number = float(value)
        if number < 0 or number != int(number):
            warnings.append(f"{column}: {operator} 기준값은 0 이상의 정수여야 해 제외")
            return None
        value = str(int(number))
    if operator == "regex":
        try:
            re.compile(value)
        except re.error as exc:
            warnings.append(f"{column}: 정규식이 올바르지 않아 제외 ({exc})")
            return None
    if operator == "comma_count_equals_column" and not compare_column:
        warnings.append(f"{column}: 비교할 열(compareColumn)이 없어 제외")
        return None
    if operator not in VALUE_OPERATORS:
        value = ""
    if operator != "comma_count_equals_column":
        compare_column = ""

    if known_columns:
        known = {name.strip().casefold() for name in known_columns}
        used = (columns or [column]) + ([compare_column] if compare_column else [])
        missing = [name for name in used if name.strip().casefold() not in known]
        if missing:
            warnings.append(f"현재 표에 없는 열 이름 사용: {', '.join(missing)} — 같은 이름의 열이 들어올 때만 검사합니다")

    return _normalize_rule({
        "column": column,
        "operator": operator,
        "value": value,
        "compareColumn": compare_column,
        "uniqueColumns": columns,
        "uniqueColumnsText": ", ".join(columns),
        "severity": raw.get("severity"),
        "message": _text(raw.get("message"), 300),
        "enabled": True,
        "source": "llm",
    }, set())


def _sample_rows(columns: list[str], rows: list[list[Any]]) -> list[dict[str, str]]:
    out = []
    for row in rows[:5]:
        if not isinstance(row, list):
            continue
        out.append({
            columns[index]: _text(cell, 80)
            for index, cell in enumerate(row[:len(columns)])
            if _text(cell, 80)
        })
    return out


@router.post("/rules/llm/draft")
def rules_llm_draft(req: RuleDraftReq, user=Depends(require_admin)):
    prompt = _text(req.prompt, MAX_PROMPT)
    if not prompt:
        raise HTTPException(400, "만들 규칙을 설명해 주세요")
    columns = _column_list(req.columns)
    existing = load_document()[0]["settings"]["rules"]

    from core import llm_adapter

    if not llm_adapter.is_available():
        return {"ok": False, "used": False, "rules": [], "warnings": [], "reason": "llm_not_connected",
                "message": "연결된 LLM이 없습니다. 관리자 설정에서 LLM을 연결해 주세요."}
    ask = json.dumps({
        "user_prompt": prompt,
        "columns": columns,
        "sample_rows": _sample_rows(columns, req.sample_rows),
        "operator_guide": OPERATOR_GUIDE,
        "existing_rules": [
            {key: rule[key] for key in ("column", "operator", "value", "compareColumn", "uniqueColumns", "severity")}
            for rule in existing[:100]
        ],
        "response_schema": {
            "rules": [{
                "operator": "operator_guide 키",
                "column": "단일 열 operator 의 열 이름",
                "columns": ["blank/unique/order_asc 의 열 이름들"],
                "value": "기준값 문자열",
                "compareColumn": "comma_count_equals_column 의 비교 열",
                "severity": "fail | warning",
                "message": "위반 시 보일 한국어 문장",
            }],
            "warnings": ["규칙으로 만들지 못한 요청과 이유"],
        },
    }, ensure_ascii=False)
    out = llm_adapter.complete_json(ask, system=LLM_DRAFT_SYSTEM, schema=LLM_DRAFT_SCHEMA,
                                    timeout=60, max_retries=1)
    try:
        model = _text(llm_adapter.get_config(redact=True).get("model"), 120)
    except Exception:
        model = ""
    if not out.get("ok"):
        return {"ok": False, "used": False, "rules": [], "warnings": [], "model": model,
                "reason": "llm_call_failed",
                "message": f"LLM 호출 실패 · {_text(out.get('error'), 240) or '응답 없음'}"}

    obj = out.get("obj") or {}
    raw_warnings = obj.get("warnings") if isinstance(obj.get("warnings"), list) else []
    warnings = [_text(item, 300) for item in raw_warnings[:20] if _text(item, 300)]
    seen = {_rule_signature(rule) for rule in existing}
    rules: list[dict[str, Any]] = []
    for raw in (obj.get("rules") or [])[:MAX_DRAFT_RULES]:
        rule = draft_rule_from_llm(raw, columns, warnings)
        if rule is None:
            continue
        signature = _rule_signature(rule)
        if signature in seen:
            warnings.append(f"이미 있는 규칙과 같아 제외: {rule['column']} · {rule['operator']}")
            continue
        seen.add(signature)
        rules.append(rule)
    _audit_user(_text(user.get("username"), 200), "dcop:rules_llm_draft",
                detail=f"rules={len(rules)} warnings={len(warnings)}", tab="dcop")
    return {"ok": True, "used": True, "rules": rules, "warnings": list(dict.fromkeys(warnings))[:30],
            "model": model, "reason": "" if rules else "no_rules",
            "message": "" if rules else "설명에서 추가할 규칙을 만들지 못했습니다."}
