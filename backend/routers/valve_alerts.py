# -*- coding: utf-8 -*-
"""routers/valve_alerts.py — Valve 파이프라인 알람 판정 API.

Valve 가 S3 로 발행한 알람(valve-alerts/pipeline/*.json)을 조회하고,
엔지니어 판정을 룰북 csv 에 반영한다 (core/valve_alerts.py).

  GET  /api/valve-alerts                알람 목록 (ack/판정 이력 병합)
  GET  /api/valve-alerts/decisions      판정 이력
  GET  /api/valve-alerts/config         전송 설정 조회 (+저장소 연결 상태)
  PUT  /api/valve-alerts/config         전송 설정 저장 (S3 bucket/prefix, local_root, 폴링)
  POST /api/valve-alerts/classify-ppid  ro_ppid → ppid_knob.csv 다음 Rule 로 추가
  POST /api/valve-alerts/match-step     unmatched_step → Vehicle_matching.csv 추가
  POST /api/valve-alerts/ack            보류(미확인예정)/반영불필요/해제(active)
  POST /api/valve-alerts/poll           수동 폴링 (신규 알람 벨 알림 체크)

쓰기 권한: admin 또는 page manager('valve').
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from core import valve_alerts as _va
from core.auth import current_user, require_page_manager

router = APIRouter(prefix="/api/valve-alerts", tags=["valve-alerts"])

_require_manager = require_page_manager("valve")


@router.get("")
def alerts_list(_user=Depends(current_user)):
    data = _va.list_alerts()
    data["config"] = _va.load_cfg()
    return data


@router.get("/decisions")
def decisions(limit: int = Query(200, ge=1, le=2000), _user=Depends(current_user)):
    return {"ok": True, "decisions": _va.list_decisions(limit)}


def _config_payload() -> dict:
    cfg = _va.load_cfg()
    ok, where = _va._Store(cfg).available()
    return {"ok": True, "config": cfg, "store_ok": ok, "store": where}


@router.get("/config")
def config_get(_user=Depends(current_user)):
    return _config_payload()


class ConfigReq(BaseModel):
    enabled: bool | None = None
    poll_seconds: int | None = None
    alerts_prefix: str | None = None
    artifacts_prefix: str | None = None
    local_root: str | None = None
    s3: dict | None = None  # {bucket, region, profile, endpoint_url}


@router.put("/config")
def config_put(req: ConfigReq, user=Depends(_require_manager)):
    try:
        _va.save_cfg({k: v for k, v in req.model_dump().items() if v is not None})
    except (TypeError, ValueError) as e:
        raise HTTPException(400, f"설정값 오류: {e}")
    from core.audit import record_user as _audit_user
    _audit_user(user.get("username", ""), "valve-alerts:config_save")
    return _config_payload()


class ClassifyReq(BaseModel):
    id: str
    category: str
    feature_name: str = ""
    note: str = ""


@router.post("/classify-ppid")
def classify_ppid(req: ClassifyReq, user=Depends(_require_manager)):
    try:
        return _va.classify_ro_ppid(req.id, req.category,
                                    feature_name=req.feature_name,
                                    note=req.note, username=user.get("username", ""))
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


class MatchStepReq(BaseModel):
    id: str
    step_desc: str = ""
    note: str = ""


@router.post("/match-step")
def match_step(req: MatchStepReq, user=Depends(_require_manager)):
    try:
        return _va.match_step(req.id, step_desc=req.step_desc,
                              note=req.note, username=user.get("username", ""))
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


class AckReq(BaseModel):
    id: str
    status: str  # 미확인예정 | 반영불필요 | active
    note: str = ""


@router.post("/ack")
def ack(req: AckReq, user=Depends(_require_manager)):
    try:
        return _va.hold_alert(req.id, req.status, note=req.note,
                              username=user.get("username", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/poll")
def poll(_user=Depends(current_user)):
    return _va.poll_once()
