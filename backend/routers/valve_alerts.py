# -*- coding: utf-8 -*-
"""routers/valve_alerts.py — 개발 서버 FAB 매칭 검사 API.

개발 worker가 제품별 FAB를 순차 검사해 만든 신규 step_id/ppid 목록을 조회하고,
엔지니어 판정을 룰북·매칭테이블 csv에 반영한다.

  GET  /api/valve-alerts                알람 목록 (ack/판정 이력 병합)
  GET  /api/valve-alerts/decisions      판정 이력
  GET  /api/valve-alerts/config         개발 worker 검사 설정/현황
  PUT  /api/valve-alerts/config         자동 검사 사용 여부와 제품 간격 저장
  POST /api/valve-alerts/classify-ppid  ro_ppid → ppid_knob.csv 다음 Rule 로 추가
  POST /api/valve-alerts/match-step     unmatched_step → Vehicle_matching.csv 추가
  POST /api/valve-alerts/add-mask       missing_reticle → mask_info.csv 에 reticle_id,mask 추가
  GET  /api/valve-alerts/plan-anomalies SplitTable KNOB plan/actual 불일치 목록
  POST /api/valve-alerts/plan-anomalies/apply 선택 PPID를 plan 이름으로 ppid_knob.csv 반영
  POST /api/valve-alerts/ack            반영불필요/해제(active)
  POST /api/valve-alerts/poll           개발 worker에 다음 제품 검사 요청

쓰기 권한: admin 또는 page manager('valve').
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from core import fab_matching_alerts as _va
from core.auth import current_user, require_page_manager

router = APIRouter(prefix="/api/valve-alerts", tags=["valve-alerts"])

_require_manager = require_page_manager("valve")


@router.get("")
def alerts_list(_user=Depends(current_user)):
    data = _va.list_alerts()
    data["config"] = _va.load_cfg()
    data["plan_anomalies"] = _va.list_plan_knob_anomalies()
    return data


@router.get("/decisions")
def decisions(limit: int = Query(200, ge=1, le=2000), _user=Depends(current_user)):
    return {"ok": True, "decisions": _va.list_decisions(limit)}


def _config_payload() -> dict:
    cfg = _va.load_cfg()
    scan = _va.list_alerts().get("scanner") or {}
    return {"ok": True, "config": cfg, "store_ok": True,
            "store": "개발 서버 FAB 직접 검사", "scanner": scan}


@router.get("/config")
def config_get(_user=Depends(current_user)):
    return _config_payload()


class ConfigReq(BaseModel):
    enabled: bool | None = None
    poll_seconds: int | None = None
    scan_interval_seconds: int | None = None
    step_exceptions: list[dict] | None = None


@router.put("/config")
def config_put(req: ConfigReq, user=Depends(_require_manager)):
    try:
        _va.save_cfg({k: v for k, v in req.model_dump().items() if v is not None})
        # Enabling (or saving an enabled interval) should wake a worker that is
        # still sleeping on the old interval. Disabling never forces a scan.
        if req.step_exceptions is not None or req.enabled is True:
            _va.request_scan()
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
        out = _va.classify_ro_ppid(req.id, req.category,
                                   feature_name=req.feature_name,
                                   note=req.note, username=user.get("username", ""))
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    # 활동 대시보드: 어떤 RO ppid 알람을 어떻게 판정했는지.
    from core.audit import record_user as _audit_user
    _audit_user(user.get("username", ""), "valve-alerts:classify_ppid",
                detail=f"id={req.id} category={req.category} feature={req.feature_name or ''}",
                tab="valve")
    return out


class MatchStepReq(BaseModel):
    id: str
    step_desc: str = ""
    note: str = ""


@router.post("/match-step")
def match_step(req: MatchStepReq, user=Depends(_require_manager)):
    try:
        out = _va.match_step(req.id, step_desc=req.step_desc,
                             note=req.note, username=user.get("username", ""))
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    # 활동 대시보드: 어떤 미매칭 step 알람을 판정했는지.
    from core.audit import record_user as _audit_user
    _audit_user(user.get("username", ""), "valve-alerts:match_step",
                detail=f"id={req.id} step_desc={req.step_desc[:60]}",
                tab="valve")
    return out


class AddMaskReq(BaseModel):
    id: str
    mask: str
    note: str = ""


@router.post("/add-mask")
def add_mask(req: AddMaskReq, user=Depends(_require_manager)):
    try:
        out = _va.add_mask(req.id, req.mask, note=req.note,
                           username=user.get("username", ""))
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    # 활동 대시보드: 어떤 reticle 을 어떤 mask 이름으로 등록했는지.
    from core.audit import record_user as _audit_user
    _audit_user(user.get("username", ""), "valve-alerts:add_mask",
                detail=f"id={req.id} mask={req.mask[:60]}",
                tab="valve")
    return out


class BatchChangeReq(BaseModel):
    type: str
    id: str
    category: str = ""
    feature_name: str = ""
    step_desc: str = ""
    mask: str = ""
    note: str = ""


class BatchApplyReq(BaseModel):
    changes: list[BatchChangeReq]
    note: str = ""


class PlanAnomalyApplyReq(BaseModel):
    ids: list[str]
    note: str = ""


@router.post("/batch-apply")
def batch_apply(req: BatchApplyReq, user=Depends(_require_manager)):
    """Apply several alert decisions as one logical, versioned batch."""
    try:
        out = _va.apply_batch(
            [change.model_dump() for change in req.changes],
            note=req.note,
            username=user.get("username", ""),
        )
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    from core.audit import record_user as _audit_user
    _audit_user(
        user.get("username", ""), "valve-alerts:batch_apply",
        detail=f"batch={out.get('batch_id')} changes={len(req.changes)}",
        tab="valve",
    )
    return out


@router.get("/plan-anomalies")
def plan_anomalies(_user=Depends(current_user)):
    return _va.list_plan_knob_anomalies()


@router.post("/plan-anomalies/apply")
def apply_plan_anomalies(req: PlanAnomalyApplyReq, user=Depends(_require_manager)):
    """Map checked SplitTable actual PPIDs to their plan names in ppid_knob."""
    try:
        out = _va.apply_plan_knob_anomalies(
            req.ids, note=req.note, username=user.get("username", ""))
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    from core.audit import record_user as _audit_user
    _audit_user(
        user.get("username", ""), "valve-alerts:plan_knob_apply",
        detail=f"batch={out.get('batch_id')} changes={out.get('count')} comment={req.note[:120]}",
        tab="valve",
    )
    return out


class AckReq(BaseModel):
    id: str
    status: str  # 미확인예정 | 반영불필요 | active
    note: str = ""


@router.post("/ack")
def ack(req: AckReq, user=Depends(_require_manager)):
    try:
        out = _va.hold_alert(req.id, req.status, note=req.note,
                             username=user.get("username", ""))
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    # 활동 대시보드: 알람 판정(보류/반영불필요/복귀) 기록.
    from core.audit import record_user as _audit_user
    _audit_user(user.get("username", ""), "valve-alerts:ack",
                detail=f"id={req.id} status={req.status}",
                tab="valve")
    return out


@router.post("/poll")
def poll(_user=Depends(_require_manager)):
    return _va.poll_once()


# ── function step 추천 (미매칭 step) ────────────────────────────────
class RecommendReq(BaseModel):
    id: str = ""          # 비우면 판정 대기 중 미검사 건 일괄
    force: bool = False   # 이미 검사한 step 재검사


@router.get("/recommendations")
def recommendations(_user=Depends(current_user)):
    """검사 기록 전체 — 화면은 목록 API 에 병합된 값을 쓰고, 이건 점검용."""
    from core import valve_step_advisor as _adv
    return {"ok": True, "settings": _adv.settings(), "records": _adv.load_records()}


@router.post("/recommend")
def recommend(req: RecommendReq, _user=Depends(current_user)):
    """추천 실행. 동일 area 안에서 FAB PPID/EQP 우선순위로 결과를 돌려준다."""
    from core import valve_step_advisor as _adv
    if not req.id:
        return _adv.recommend_pending(_va.list_alerts().get("alerts") or [], force=req.force)
    alert = _va._find_alert(req.id)
    if not alert:
        raise HTTPException(404, f"알람을 찾을 수 없음: {req.id}")
    if alert.get("type") != "unmatched_step":
        raise HTTPException(400, f"미매칭 step 알람이 아님: {req.id}")
    return {"ok": True, "checked": 1, "records": [_adv.recommend(alert, force=req.force)]}
