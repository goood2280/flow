"""DCOP 검사 공용 규칙 저장 API.

규칙은 배포 소스와 분리된 ``FLOW_DATA_ROOT/dcop/settings.json``에 저장한다.
따라서 setup.py 업데이트/재배포 뒤에도 그대로 유지된다. 읽기는 로그인 사용자,
쓰기는 global admin 또는 dcop page manager만 허용한다.
"""
from __future__ import annotations

import datetime
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.audit import record_user as _audit_user
from core.auth import current_user, is_page_manager, require_page_manager
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
    return {
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
