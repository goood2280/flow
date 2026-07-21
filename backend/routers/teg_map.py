# -*- coding: utf-8 -*-
"""routers/teg_map.py — TEG 위치 조회 API.

chip layout(Chip_Radius)·Teg_location 파일로 WF MAP geometry 를 fit 하고
TEG 실좌표/radius 를 계산한다 (core/teg_map.py). 설정·vehicle 그림은
DB root 의 teg_location/ 폴더에 저장.

  GET    /api/teg-map/config              설정 조회 (+파일 존재 여부, 후보 파일 목록)
  PUT    /api/teg-map/config              설정 저장 (파일 경로/배율/TEG 기본크기/vehicle 표시)
  GET    /api/teg-map/vehicles            layout 의 vehicle 목록
  GET    /api/teg-map/check-targets?vehicle=  Mapfile 체크 대상 TEG + teg 목록
  PUT    /api/teg-map/check-targets       Mapfile 체크 대상 TEG 저장 (manager)
  GET    /api/teg-map/map?vehicle=        WF MAP payload (geometry+shots+tegs+표시설정)
  POST   /api/teg-map/inspect             설비 원문 검사 (파싱+flat 변환+Teg_location 대조)
  GET    /api/teg-map/radius?vehicle=&teg= TEG 좌하단 shot 별 radius 표
  GET    /api/teg-map/image?vehicle=      vehicle 그림 파일
  POST   /api/teg-map/image?vehicle=      vehicle 그림 업로드 (multipart `file`)
  DELETE /api/teg-map/image?vehicle=      vehicle 그림 삭제

쓰기 권한: admin 또는 page manager('teg').
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core import teg_check as _tc
from core import teg_map as _tm
from core.auth import current_user, require_page_manager

router = APIRouter(prefix="/api/teg-map", tags=["teg-map"])

_require_manager = require_page_manager("teg")

# 일반 사용자 TEG 동시 선택 상한 — 전체를 한 번에 그리면 브라우저가 죽어(502/OOM) 방어.
# 관리자(admin) / teg 페이지 관리자는 제한 없음.
MAX_TEG_SELECTION = 30


def _is_teg_manager(user: dict) -> bool:
    return (user.get("role") == "admin"
            or "teg" in (user.get("page_manager") or []))


def _config_payload() -> dict:
    cfg = _tm.load_cfg()
    lay_path = _tm.resolve_path(cfg["layout_file"])
    teg_path = _tm.resolve_path(cfg["teg_file"])
    return {
        "ok": True,
        "config": cfg,
        "layout_ok": lay_path.is_file(),
        "teg_ok": teg_path.is_file(),
        "layout_path": str(lay_path),
        "teg_path": str(teg_path),
        "teg_dir": str(_tm.teg_dir()),
        "files": _tm.candidate_files(),
    }


@router.get("/config")
def config_get(_user=Depends(current_user)):
    return _config_payload()


class ConfigReq(BaseModel):
    layout_file: str | None = None
    teg_file: str | None = None
    ebeam_scale: float | None = None
    wafer_radius_mm: float | None = None
    wafer_edge_mm: float | None = None
    teg_default_w: float | None = None
    teg_default_h: float | None = None
    vehicles: dict | None = None
    check: dict | None = None   # TEG Mapfile 체크 — flat 기본/모듈별 오프셋, v_R 회전 offset


@router.put("/config")
def config_put(req: ConfigReq, user=Depends(_require_manager)):
    try:
        _tm.save_cfg({k: v for k, v in req.model_dump().items() if v is not None})
    except (TypeError, ValueError) as e:
        raise HTTPException(400, f"설정값 오류: {e}")
    from core.audit import record_user as _audit_user
    _audit_user(user.get("username", ""), "teg-map:config_save")
    return _config_payload()


@router.get("/vehicles")
def vehicles(_user=Depends(current_user)):
    return {"ok": True, "vehicles": _tm.vehicles()}


@router.get("/check-targets")
def check_targets_get(vehicle: str = Query(...), _user=Depends(current_user)):
    """vehicle 의 teg 목록 + 현재 Mapfile 체크 대상 TEG 선택 (읽기 — 누구나)."""
    return _tm.teg_target_options(vehicle)


class CheckTargetsReq(BaseModel):
    vehicle: str
    # 명시적 대상 목록. None 이면 해당 vehicle 설정을 지우고 기본값(H_/V_)으로 되돌림.
    targets: list[str] | None = None


@router.put("/check-targets")
def check_targets_put(req: CheckTargetsReq, user=Depends(_require_manager)):
    """Mapfile 체크 대상 TEG 저장 (admin / teg page manager 만)."""
    veh = str(req.vehicle or "").strip()
    if not veh:
        raise HTTPException(400, "vehicle 이 비어 있습니다")
    _tm.save_cfg({"check_targets": {veh: req.targets}})
    from core.audit import record_user as _audit_user
    _audit_user(user.get("username", ""), f"teg-map:check_targets:{veh}")
    return _tm.teg_target_options(veh)


@router.get("/map")
def wf_map(vehicle: str = Query(...), user=Depends(current_user)):
    try:
        payload = _tm.map_payload(vehicle)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except LookupError as e:
        raise HTTPException(404, str(e))
    # 일반 사용자는 최대 MAX_TEG_SELECTION 개까지만 동시 선택 (전체 렌더 방지). 관리자는 무제한.
    payload["max_selection"] = None if _is_teg_manager(user) else MAX_TEG_SELECTION
    return payload


class InspectReq(BaseModel):
    vehicle: str = ""
    text: str
    flat: str | None = None   # "h" | "v_R" | None(자동 감지, 기본 h)
    # 기준 PCHK 이 내장 마커로 안 잡힐 때 사용자 지정 flat 마커
    # {"h": ["H_TPCHK", ...], "v_R": ["V_TPCHK", ...]}
    markers: dict | None = None
    # 행별 module 이름 재지정 {idx: 이름} — 엔지니어마다 이름 위치가 달라
    # 자동 인식(꼬리표 첫 토큰 > module~( 이름)이 틀린 행을 UI 에서 바로잡는다
    name_overrides: dict | None = None


@router.post("/inspect")
def inspect(req: InspectReq, _user=Depends(current_user)):
    if not req.text.strip():
        raise HTTPException(400, "원문(text)이 비어 있습니다")
    return _tc.inspect(req.vehicle, req.text, req.flat, custom_markers=req.markers,
                       name_overrides=req.name_overrides)


class MainOverlayApplyReq(BaseModel):
    vehicle: str
    # [{group, tegs: [{teg, x, y}]}] — teg_check inspect 의 teg.main_groups 형식
    groups: list
    # 같은 그룹이 이미 반영돼 있으면 False 응답의 exists 로 알려주고,
    # UI 확인 후 overwrite=True 로 재요청해야 덮어쓴다
    overwrite: bool = False


@router.get("/main-overlay")
def main_overlay(vehicle: str = Query(...), _user=Depends(current_user)):
    return {"ok": True, "overlays": _tm.get_main_overlays(vehicle)}


@router.post("/main-overlay/apply")
def main_overlay_apply(req: MainOverlayApplyReq, _user=Depends(current_user)):
    return _tm.apply_main_overlays(req.vehicle, req.groups, overwrite=req.overwrite)


@router.delete("/main-overlay")
def main_overlay_delete(vehicle: str = Query(...), group: str = Query(...),
                        _user=Depends(current_user)):
    if not _tm.delete_main_overlay(vehicle, group):
        raise HTTPException(404, f"overlay 없음: {vehicle} / {group}")
    return {"ok": True}


@router.get("/radius")
def radius(vehicle: str = Query(...), teg: str = Query(...), _user=Depends(current_user)):
    try:
        return _tm.teg_radius_table(vehicle, teg)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/image")
def image_get(vehicle: str = Query(...), _user=Depends(current_user)):
    p = _tm.image_path(vehicle)
    if p is None:
        raise HTTPException(404, "등록된 그림이 없습니다")
    return FileResponse(p)


async def _read_upload_file(request: Request) -> tuple[str, bytes]:
    """multipart `file` 필드 읽기 — informs 와 동일하게 python-multipart 의존을
    런타임으로 미뤄서, 패키지 없는 환경에서도 라우터 로드는 성공하게 한다."""
    import sys
    try:
        form = await request.form()
    except Exception as exc:
        raise HTTPException(
            500,
            "파일 업로드 파서가 준비되지 않았습니다. "
            f"`{sys.executable} -m pip install python-multipart` 실행이 필요합니다: {exc}",
        )
    file = form.get("file")
    if file is None or not hasattr(file, "read"):
        raise HTTPException(400, "file 필드가 필요합니다.")
    filename = str(getattr(file, "filename", "") or "")
    data_or_coro = file.read()
    data = await data_or_coro if hasattr(data_or_coro, "__await__") else data_or_coro
    if isinstance(data, str):
        data = data.encode("utf-8")
    return filename, bytes(data or b"")


@router.post("/image")
async def image_upload(request: Request, vehicle: str = Query(...),
                       user=Depends(_require_manager)):
    filename, data = await _read_upload_file(request)
    ext = Path(filename).suffix.lower()
    try:
        name = _tm.save_image(vehicle, data, ext)
    except ValueError as e:
        raise HTTPException(400, str(e))
    from core.audit import record_user as _audit_user
    _audit_user(user.get("username", ""), f"teg-map:image_upload:{vehicle}")
    return {"ok": True, "image": name}


@router.delete("/image")
def image_delete(vehicle: str = Query(...), user=Depends(_require_manager)):
    _tm.delete_image(vehicle)
    return {"ok": True}
