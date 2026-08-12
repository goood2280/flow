# -*- coding: utf-8 -*-
"""routers/matching_fill.py — 매칭 CSV `product` 열 채우기 API.

  GET    /api/matching-fill/targets            대상 3종 + CSV/DB 존재 여부 + 제품 폴더
  GET    /api/matching-fill/settings           step_id prefix 규칙 / module 구간표 / 스캔 상한
  POST   /api/matching-fill/settings           규칙 저장 (manager)
  POST   /api/matching-fill/scan               검사 실행 → 제안 생성 (manager, 파일 변경 없음)
  GET    /api/matching-fill/proposal?target=   현재 제안
  POST   /api/matching-fill/apply              제안 반영 (**admin/manager 확인 단계**)
  POST   /api/matching-fill/discard            제안 폐기

읽기는 로그인 사용자, 쓰기는 admin 또는 page manager('matchfill').
스캔은 파일을 만지지 않는다 — 실제 CSV 쓰기는 apply 하나뿐이다.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from core import matching_fill as _mf
from core.auth import current_user, require_page_manager

router = APIRouter(prefix="/api/matching-fill", tags=["matching-fill"])

_require_manager = require_page_manager("matchfill")


class SettingsReq(BaseModel):
    prefix_rules: list[dict] | None = None
    module_rules: list[dict] | None = None
    max_files_per_product: int | None = None


class ScanReq(BaseModel):
    target: str
    column: str = "product"          # product = DB 스캔 / module = step 번호 구간표


class ApplyReq(BaseModel):
    target: str
    column: str = "product"
    skip_rows: list[int] = []


@router.get("/targets")
def targets(request: Request):
    current_user(request)
    out = []
    for key, spec in _mf.TARGETS.items():
        cols, rows = _mf._read_csv(key)
        out.append({
            "target": key,
            "label": spec["label"],
            "file": spec["file"],
            "csv_exists": bool(cols),
            "csv_rows": len(rows),
            "columns": cols,
            "has_product_col": _mf.PRODUCT_COL in cols,
            "keys": [k for k in spec["keys"] if k in cols],
            "products": _mf.list_products(key),
            "has_module_col": _mf.MODULE_COL in cols,
            "has_step_col": "step_id" in cols,
            "has_step_desc_col": any(str(c).strip().casefold() == "step_desc" for c in cols),
            # 이 대상에서 채울 수 있는 열. vm_matching 은 product 를 채우지 않는다
            # (제품 귀속은 Vehicle_matching.csv 가 step_desc 로 이미 정한다).
            "fill_columns": _mf.target_fill_columns(key),
            "module_source": str(spec.get("module_source") or "step_range"),
            "proposals": {c: _proposal_summary(_mf.get_proposal(key, c)) for c in _mf.FILL_COLUMNS},
        })
    return {"targets": out, "db_root": str(_mf._db_root())}


def _proposal_summary(proposal: dict | None) -> dict | None:
    if not proposal:
        return None
    return {
        "counts": proposal.get("counts"),
        "scanned_at": proposal.get("scanned_at"),
        "scanned_by": proposal.get("scanned_by"),
        "applied": bool(proposal.get("applied")),
        "applied_at": proposal.get("applied_at"),
        "rows": len(proposal.get("rows") or []),
        "add_column": bool(proposal.get("add_column")),
    }


@router.get("/settings")
def get_settings(request: Request):
    current_user(request)
    return _mf.settings()


@router.post("/settings")
def save_settings(req: SettingsReq, request: Request, me=Depends(_require_manager)):
    patch = {k: v for k, v in req.model_dump().items() if v is not None}
    return _mf.save_settings(patch, username=(me or {}).get("username", ""))


@router.post("/scan")
def scan(req: ScanReq, request: Request, me=Depends(_require_manager)):
    try:
        proposal = _mf.scan(req.target, username=(me or {}).get("username", ""), column=req.column)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "proposal": proposal}


@router.get("/proposal")
def proposal(request: Request, target: str = Query(""), column: str = Query("product")):
    current_user(request)
    if target not in _mf.TARGETS:
        raise HTTPException(400, f"알 수 없는 대상: {target}")
    if column not in _mf.FILL_COLUMNS:
        raise HTTPException(400, f"알 수 없는 열: {column}")
    return {"proposal": _mf.get_proposal(target, column)}


@router.post("/apply")
def apply(req: ApplyReq, request: Request, me=Depends(_require_manager)):
    if req.target not in _mf.TARGETS:
        raise HTTPException(400, f"알 수 없는 대상: {req.target}")
    try:
        return _mf.apply_proposal(req.target, username=(me or {}).get("username", ""),
                                  skip_rows=req.skip_rows, column=req.column)
    except (LookupError, ValueError) as e:
        raise HTTPException(400, str(e))


@router.post("/discard")
def discard(req: ScanReq, request: Request, me=Depends(_require_manager)):
    if req.target not in _mf.TARGETS:
        raise HTTPException(400, f"알 수 없는 대상: {req.target}")
    _mf.discard(req.target, req.column)
    return {"ok": True}
