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
  GET    /api/teg-map/generate?vehicle=   Mapfile용 좌표 생성 (정답지 → PCHK=(0,0) 상대좌표 표)
  GET    /api/teg-map/main-grid?vehicle=&mains=  MAIN die 내부 TEG 자리 격자 (기본 TEG 사이즈 분할)
  GET    /api/teg-map/radius?vehicle=&teg= TEG 좌하단 shot 별 radius 표
  GET    /api/teg-map/image?vehicle=      vehicle 그림 파일
  POST   /api/teg-map/image?vehicle=      vehicle 그림 업로드 (multipart `file`)
  DELETE /api/teg-map/image?vehicle=      vehicle 그림 삭제
  GET    /api/teg-map/image/shapes?vehicle=  그림에서 인식한 die 사각형

쓰기 권한: admin 또는 page manager('teg').
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core import teg_check as _tc
from core import teg_map as _tm
from core import teg_shape as _teg_shape
from core.auth import canonical_tab_token, current_user, require_admin, require_page_manager

router = APIRouter(prefix="/api/teg-map", tags=["teg-map"])

_require_manager = require_page_manager("teg")

# 일반 사용자 TEG 동시 선택 상한 — 전체를 한 번에 그리면 브라우저가 죽어(502/OOM) 방어.
# 관리자(admin) / teg 페이지 관리자는 제한 없음.
MAX_TEG_SELECTION = 30


def _is_teg_manager(user: dict) -> bool:
    return (user.get("role") == "admin"
            or "teg" in (user.get("page_manager") or []))


def _require_teg_user(user=Depends(current_user)) -> dict:
    if _is_teg_manager(user):
        return user
    raw = user.get("tabs") or []
    values = raw if isinstance(raw, list) else str(raw).split(",")
    if any(canonical_tab_token(value) == "teg" for value in values):
        return user
    raise HTTPException(403, "TEG page permission required")


def _config_payload() -> dict:
    cfg = _tm.load_cfg()
    # 설정 파일명이 개발 경로나 대소문자 차이로 운영에서 그대로 존재하지 않아도
    # core가 찾아낸 실제 유효 layout 경로를 상태 화면에 보여준다.
    layout, lay_path = _tm.load_layout()
    teg_path = _tm.resolve_path(cfg["teg_file"])
    chip_path = _tm.resolve_path(cfg["main_chip_file"])
    return {
        "ok": True,
        "config": cfg,
        "layout_ok": layout is not None and not layout.empty,
        "teg_ok": teg_path.is_file(),
        "main_chip_ok": chip_path.is_file(),
        "layout_path": str(lay_path),
        "teg_path": str(teg_path),
        "main_chip_path": str(chip_path),
        "teg_dir": str(_tm.teg_dir()),
        "files": _tm.candidate_files(),
    }


@router.get("/config")
def config_get(_user=Depends(current_user)):
    return _config_payload()


class ConfigReq(BaseModel):
    layout_file: str | None = None
    teg_file: str | None = None
    main_chip_file: str | None = None   # MAIN(die) 크기표 — vehicle/chip_name/chipsize_x,y (µm)
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


class ReferenceFileSaveReq(BaseModel):
    kind: str
    columns: list[str]
    rows: list[list]
    note: str = ""
    expected_modified_ns: int | None = None


@router.get("/reference-files")
def reference_files_get(_user=Depends(_require_teg_user)):
    """TEG 페이지 전용 allowlist. FileBrowser 권한 없이 세 기준 파일만 노출."""
    try:
        return _tm.reference_files_payload()
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/reference-file")
def reference_file_get(kind: str = Query(...), _user=Depends(_require_teg_user)):
    try:
        return _tm.read_reference_file(kind)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put("/reference-file")
def reference_file_put(req: ReferenceFileSaveReq, user=Depends(_require_teg_user)):
    try:
        out = _tm.save_reference_file(req.kind, req.columns, req.rows,
                                      user.get("username", ""), req.note, req.expected_modified_ns)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    from core.audit import record_user as _audit_user
    _audit_user(user.get("username", ""), "teg-map:reference-save",
                detail=f"kind={req.kind} rows={len(req.rows)}", tab="teg")
    return out


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


class InlineMapShotReq(BaseModel):
    shot_x: float
    shot_y: float
    name: str


class InlineMapTableReq(BaseModel):
    table_name: str
    vehicle: str
    shots: list[InlineMapShotReq]


@router.get("/inline-map-settings")
def inline_map_settings_get(_admin=Depends(require_admin)):
    """DB root/credential 데이터지만 global admin에게만 반환한다."""
    out = _tm.load_inline_map_settings()
    return {"ok": True, **out}


@router.put("/inline-map-settings")
def inline_map_settings_put(req: InlineMapTableReq, admin=Depends(require_admin)):
    try:
        out = _tm.save_inline_map_table(
            req.table_name,
            req.vehicle,
            [item.model_dump() for item in req.shots],
            admin.get("username", ""),
        )
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    from core.audit import record_user as _audit_user
    _audit_user(admin.get("username", ""), f"teg-map:inline-map-save:{req.table_name}")
    return {"ok": True, **out}


@router.delete("/inline-map-settings")
def inline_map_settings_delete(table_name: str = Query(...), admin=Depends(require_admin)):
    try:
        out = _tm.delete_inline_map_table(table_name)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    from core.audit import record_user as _audit_user
    _audit_user(admin.get("username", ""), f"teg-map:inline-map-delete:{table_name}")
    return {"ok": True, **out}


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
    # 활동 대시보드: 어떤 제품(vehicle)의 WF MAP 을 봤는지.
    from core.audit import record_user as _audit_user
    _audit_user(user.get("username", ""), "teg-map:view", detail=f"vehicle={vehicle}", tab="teg")
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


@router.get("/generate")
def generate(vehicle: str = Query(...), include_all: bool = Query(False),
             _user=Depends(current_user)):
    """Mapfile용 좌표 생성 — 체크 대상 TEG 를 기준 PCHK=(0,0) 상대좌표로 되돌린 **표**.

    Mapfile 체크의 역방향(정답지 → 설비 좌표)이다. flat(Horizontal/Vertical(R))
    별로 따로 만들며, include_all=True 면 direction 이 다른 TEG 도 함께 넣는다.
    설비 원문 문자열은 내보내지 않는다 — 좌표표만 돌려준다.
    """
    veh = str(vehicle or "").strip()
    if not veh:
        raise HTTPException(400, "vehicle 이 비어 있습니다")
    return _tc.build_mapfile(veh, include_all=include_all)


@router.get("/main-grid")
def main_grid(vehicle: str = Query(...), mains: str = Query(""),
              gap_x: float = Query(0.0), gap_y: float = Query(0.0),
              _user=Depends(current_user)):
    """MAIN die 안의 TEG 자리 생성 — die 를 기본 TEG 사이즈 격자로 나눈 표.

    MAIN 안의 TEG 는 정답지에 없어 좌표를 만들 근거가 없다. die 를 격자로 나눠
    자리를 만들고, 사용자가 칸에 이름을 적으면 그 자리의 Mapfile 상대좌표가 된다.
    `mains` 는 쉼표 구분(여러 MAIN 동시), `gap_x`/`gap_y` 는 TEG 사이 거리(mm)다.
    비워 보내면 선택 가능한 MAIN 목록(available)만 돌려준다.
    """
    veh = str(vehicle or "").strip()
    if not veh:
        raise HTTPException(400, "vehicle 이 비어 있습니다")
    names = [t.strip() for t in str(mains or "").split(",") if t.strip()]
    return _tc.build_main_grid(veh, names, gap_x=gap_x, gap_y=gap_y)


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
    old = _tm.image_path(vehicle)
    try:
        name = _tm.save_image(vehicle, data, ext)
    except ValueError as e:
        raise HTTPException(400, str(e))
    # die 사각형 캐시는 (mtime, size)로 무효화되지만, 공유 드라이브의 mtime 해상도가
    # 거친 경우가 있어 교체 시점에 명시적으로도 버린다.
    _teg_shape.invalidate(old)
    _teg_shape.invalidate(_tm.image_path(vehicle))
    from core.audit import record_user as _audit_user
    _audit_user(user.get("username", ""), f"teg-map:image_upload:{vehicle}")
    return {"ok": True, "image": name, "shapes": _image_shapes(vehicle)}


@router.delete("/image")
def image_delete(vehicle: str = Query(...), user=Depends(_require_manager)):
    _teg_shape.invalidate(_tm.image_path(vehicle))
    _tm.delete_image(vehicle)
    return {"ok": True}


def _image_shapes(vehicle: str) -> dict:
    """shot 확대에 그릴 die 사각형 — 그림 격자와 개발 격자를 **둘 다** 돌려준다.

    화면이 표시 방식을 바로 바꿔 볼 수 있어야 하므로 한쪽만 고르지 않는다.
      · image_cells — 그림에서 인식한 사각형(그림과 겹쳐 보이는 격자)
      · dev_cells   — MAIN TEG 좌표(die 좌하단) + Main_chip_info 의 chip 크기
      · cells       — 이 vehicle 의 현재 표시 방식에 해당하는 것 (기존 계약 유지)
    """
    path = _tm.image_path(vehicle)
    det = _teg_shape.detect(path) if path is not None else {}
    out = {"ok": bool(det.get("ok")) if path is not None else False,
           "reason": (det.get("reason") or "") if path is not None else "no_image",
           "source": det.get("source") or "",
           "count": int(det.get("count") or 0), "rects": det.get("rects") or [],
           "cells": [], "image_cells": [], "dev_cells": [], "align": {}}
    try:
        payload = _tm.map_payload(vehicle) or {}
    except Exception:
        payload = {}
    geo = payload.get("geometry") or {}
    mode = str((payload.get("display") or {}).get("mode") or "")
    anchors = _tm.main_anchors(payload)
    if geo.get("fit") == "radius":
        W, H = float(geo["shot_w_mm"]), float(geo["shot_h_mm"])
        cd = _teg_shape.shot_cells_detail(path or "", W, H, anchors, dev_grid=True)
        out.update({"shot_w_mm": W, "shot_h_mm": H,
                    "image_cells": cd["image_cells"], "dev_cells": cd["cells"],
                    "align": cd["align"],
                    "cells": cd["cells"] if mode == "dev_grid" else cd["image_cells"]})
    return out


@router.get("/image/shapes")
def image_shapes(vehicle: str = Query(...), _user=Depends(current_user)):
    return _image_shapes(vehicle)
