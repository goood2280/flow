# -*- coding: utf-8 -*-
"""routers/teg_map.py — TEG 위치 조회 API.

chip layout(Chip_Radius)·Teg_location 파일로 WF MAP geometry 를 fit 하고
TEG 실좌표/radius 를 계산한다 (core/teg_map.py). 설정·vehicle 그림은
DB root 의 teg_location/ 폴더에 저장.

  GET    /api/teg-map/config              설정 조회 (+파일 존재 여부, 후보 파일 목록)
  PUT    /api/teg-map/config              설정 저장 (파일 경로/배율/TEG 기본크기/vehicle 표시)
  GET    /api/teg-map/vehicles            layout 의 vehicle 목록
  GET    /api/teg-map/map?vehicle=        WF MAP payload (geometry+shots+tegs+표시설정)
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

from core import teg_map as _tm
from core.auth import current_user, require_page_manager

router = APIRouter(prefix="/api/teg-map", tags=["teg-map"])

_require_manager = require_page_manager("teg")


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


@router.get("/map")
def wf_map(vehicle: str = Query(...), _user=Depends(current_user)):
    try:
        return _tm.map_payload(vehicle)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except LookupError as e:
        raise HTTPException(404, str(e))


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
