"""DC layer ↔ step_id 공용 매핑 API.

flow 전체(ET 추적 · 분석의뢰 · 홈 챗 결과/계획)가 같은 파일(``DB/dc_layer_step_mapping.csv``)을 본다.
auto report 의 ``dc_step_to_ids`` dict 를 그대로 붙여넣어 저장할 수 있다(core.dc_layer_mapping.parse_mapping_text).
읽기는 로그인 사용자, 저장은 관리자와 이 매핑을 쓰는 업무 페이지의 위임자.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core import dc_layer_mapping
from core.auth import current_user, is_page_manager

router = APIRouter(prefix="/api/dc-layers", tags=["dc-layers"])

EDITOR_PAGES = ("analysisrequest", "tracker", "autoreport", "ettime")


class DcLayerRow(BaseModel):
    dc_layer: str = Field(default="", max_length=40)
    step_ids: list[str] | str = Field(default_factory=list)


class DcLayerSave(BaseModel):
    rows: list[DcLayerRow] = Field(default_factory=list, max_length=500)
    text: str = Field(default="", max_length=200_000)
    mode: Literal["replace", "merge"] = "replace"


def _can_edit(user: dict) -> bool:
    return user.get("role") == "admin" or any(is_page_manager(user, page) for page in EDITOR_PAGES)


@router.get("")
def get_mapping(request: Request):
    user = current_user(request)
    data = dc_layer_mapping.load_mapping()
    return {**data, "can_edit": _can_edit(user)}


@router.post("/parse")
def parse_mapping(payload: DcLayerSave, request: Request):
    """저장 전 미리보기 — 붙여넣은 dict 를 어떻게 읽었는지 보여준다."""
    current_user(request)
    rows = dc_layer_mapping.parse_mapping_text(payload.text)
    if not rows:
        raise HTTPException(400, "DC layer ↔ step_id 매핑을 읽지 못했습니다. {'M1DC': ['step_id', ...]} 형식으로 붙여넣어 주세요.")
    return {"rows": rows, "layers": len(rows), "step_ids": sum(len(r["step_ids"]) for r in rows)}


@router.post("")
def save_mapping(payload: DcLayerSave, request: Request):
    user = current_user(request)
    if not _can_edit(user):
        raise HTTPException(403, "DC layer 매핑은 관리자와 분석의뢰·ET 추적·Auto report·ET 측정시간 위임자만 바꿀 수 있습니다")
    incoming = dc_layer_mapping.parse_mapping_text(payload.text) if payload.text.strip() else \
        dc_layer_mapping.normalize_rows([row.model_dump() if hasattr(row, "model_dump") else row.dict() for row in payload.rows])
    if not incoming:
        raise HTTPException(400, "저장할 DC layer 매핑이 없습니다")
    if payload.mode == "merge":
        merged = {row["dc_layer"]: row for row in dc_layer_mapping.load_mapping()["rows"]}
        for row in incoming:
            merged[row["dc_layer"]] = row
        incoming = list(merged.values())
    result = dc_layer_mapping.save_mapping(incoming)
    try:
        from core.audit import record_user
        record_user(user.get("username") or "", "dc_layers:save",
                    detail=f"layers={len(result['rows'])} mode={payload.mode}")
    except Exception:
        pass
    return {**result, "can_edit": True}
