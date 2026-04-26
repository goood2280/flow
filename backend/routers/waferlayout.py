from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from core.auth import require_admin
from core.paths import PATHS
from core import product_config as _pc

router = APIRouter(prefix="/api/waferlayout", tags=["waferlayout"])

PC_ROOT = PATHS.data_root

DEF = {
    "waferRadius": 150,
    "wfCenterX": 0,
    "wfCenterY": 0,
    "refShotX": 0,
    "refShotY": 0,
    "refShotCenterX": 0,
    "refShotCenterY": 0,
    "shotPitchX": 28,
    "shotPitchY": 30,
    "shotSizeX": 27.2,
    "shotSizeY": 29.2,
    "tegSizeX": 1.2,
    "tegSizeY": 0.6,
    "chipWidth": 3.6,
    "chipHeight": 4.8,
    "chipCols": 3,
    "chipRows": 2,
    "offsetXMm": 0,
    "offsetYMm": 0,
    "edgeExclusionMm": 3,
    "scribePattern": [
        {"positionRow": 0, "type": "full"},
        {"positionRow": 1, "type": "full"},
        {"positionRow": 2, "type": "full"},
    ],
}


def _num(v: Any, default: float = 0.0) -> float:
    try:
        out = float(v)
    except Exception:
        return default
    return out if math.isfinite(out) else default


def _build_cfg(saved: dict | None) -> dict:
    src = saved if isinstance(saved, dict) else {}
    out = dict(DEF)
    alias_map = {
        "chip_radius_mm": "waferRadius",
        "wafer_radius_mm": "waferRadius",
        "shot_pitch_x_mm": "shotPitchX",
        "shot_pitch_y_mm": "shotPitchY",
        "shot_size_w_mm": "shotSizeX",
        "shot_size_h_mm": "shotSizeY",
        "teg_size_w_mm": "tegSizeX",
        "teg_size_h_mm": "tegSizeY",
        "chip_w_mm": "chipWidth",
        "chip_h_mm": "chipHeight",
        "offset_x_mm": "offsetXMm",
        "offset_y_mm": "offsetYMm",
    }
    for src_key, target_key in alias_map.items():
        if src.get(src_key) not in (None, "") and src.get(target_key) in (None, ""):
            src[target_key] = src.get(src_key)
    for key in DEF:
        if src.get(key) is not None and src.get(key) != "":
            out[key] = src[key]
    out["edgeExclusionMm"] = max(0.0, _num(out.get("edgeExclusionMm"), 3.0))
    out["chipCols"] = max(1, int(_num(out.get("chipCols"), 3)))
    out["chipRows"] = max(1, int(_num(out.get("chipRows"), 2)))
    out["scribePattern"] = _normalize_scribe_pattern(out.get("scribePattern"), out["chipRows"])
    return out


def _normalize_scribe_pattern(rows: list[dict] | None, chip_rows: int) -> list[dict]:
    out = []
    for idx in range(max(1, int(chip_rows)) + 1):
        row = next((item for item in (rows or []) if int(_num(item.get("positionRow"), idx)) == idx), None)
        out.append({
            "positionRow": idx,
            "type": "half" if (row or {}).get("type") == "half" else "full",
        })
    return out


def _normalize_tegs(rows: list[dict] | None) -> list[dict]:
    out = []
    for idx, row in enumerate(rows or []):
        out.append({
            "id": str(row.get("id") or row.get("name") or row.get("label") or f"TEG_{idx + 1}"),
            "label": str(row.get("label") or row.get("name") or f"TEG_{idx + 1}"),
            "dx_mm": _num(row.get("dx_mm", row.get("x", 0.0)), 0.0),
            "dy_mm": _num(row.get("dy_mm", row.get("y", 0.0)), 0.0),
        })
    return out


def _shot_center(shot_x: int, shot_y: int, cfg: dict) -> tuple[float, float]:
    return (
        _num(cfg.get("refShotCenterX")) + (shot_x - int(_num(cfg.get("refShotX")))) * _num(cfg.get("shotPitchX"), 28.0),
        _num(cfg.get("refShotCenterY")) + (shot_y - int(_num(cfg.get("refShotY")))) * _num(cfg.get("shotPitchY"), 30.0),
    )


def _in_wafer(x: float, y: float, cfg: dict, radius_key: str = "waferRadius") -> bool:
    dx = x - _num(cfg.get("wfCenterX"))
    dy = y - _num(cfg.get("wfCenterY"))
    return math.hypot(dx, dy) <= _num(cfg.get(radius_key), 150.0)


def _rect_inside_wafer(rect: dict, cfg: dict, radius_key: str = "waferRadius") -> bool:
    corners = [
        (rect["x"], rect["y"]),
        (rect["x"] + rect["w"], rect["y"]),
        (rect["x"], rect["y"] + rect["h"]),
        (rect["x"] + rect["w"], rect["y"] + rect["h"]),
    ]
    return all(_in_wafer(x, y, cfg, radius_key=radius_key) for x, y in corners)


def _rect_intersects_wafer(rect: dict, cfg: dict) -> bool:
    points = [
        (rect["x"], rect["y"]),
        (rect["x"] + rect["w"], rect["y"]),
        (rect["x"], rect["y"] + rect["h"]),
        (rect["x"] + rect["w"], rect["y"] + rect["h"]),
        (rect["x"] + rect["w"] / 2, rect["y"] + rect["h"] / 2),
    ]
    if any(_in_wafer(x, y, cfg) for x, y in points):
        return True
    dx = max(abs((rect["x"] + rect["w"] / 2) - _num(cfg.get("wfCenterX"))) - rect["w"] / 2, 0)
    dy = max(abs((rect["y"] + rect["h"] / 2) - _num(cfg.get("wfCenterY"))) - rect["h"] / 2, 0)
    return math.hypot(dx, dy) <= _num(cfg.get("waferRadius"), 150.0)


def _collect_shots(cfg: dict) -> list[dict]:
    wafer_radius = _num(cfg.get("waferRadius"), 150.0)
    max_shot_x = math.ceil((wafer_radius + abs(_num(cfg.get("refShotCenterX")) - _num(cfg.get("wfCenterX")))) / max(1.0, _num(cfg.get("shotPitchX"), 28.0))) + 2
    max_shot_y = math.ceil((wafer_radius + abs(_num(cfg.get("refShotCenterY")) - _num(cfg.get("wfCenterY")))) / max(1.0, _num(cfg.get("shotPitchY"), 30.0))) + 2
    usable_cfg = dict(cfg)
    usable_cfg["waferRadius"] = max(0.0, wafer_radius - _num(cfg.get("edgeExclusionMm"), 3.0))
    shots = []
    for shot_y in range(-max_shot_y, max_shot_y + 1):
        for shot_x in range(-max_shot_x, max_shot_x + 1):
            center_x, center_y = _shot_center(shot_x, shot_y, cfg)
            shot_body = {
                "x": center_x - _num(cfg.get("shotSizeX"), 27.2) / 2,
                "y": center_y - _num(cfg.get("shotSizeY"), 29.2) / 2,
                "w": _num(cfg.get("shotSizeX"), 27.2),
                "h": _num(cfg.get("shotSizeY"), 29.2),
            }
            pitch_rect = {
                "x": center_x - _num(cfg.get("shotPitchX"), 28.0) / 2,
                "y": center_y - _num(cfg.get("shotPitchY"), 30.0) / 2,
                "w": _num(cfg.get("shotPitchX"), 28.0),
                "h": _num(cfg.get("shotPitchY"), 30.0),
            }
            keep = _rect_inside_wafer(shot_body, cfg) or _rect_intersects_wafer(pitch_rect, cfg) or _in_wafer(center_x, center_y, cfg)
            if not keep:
                continue
            shots.append({
                "shotX": shot_x,
                "shotY": shot_y,
                "centerX": center_x,
                "centerY": center_y,
                "shotBody": shot_body,
                "completely_inside": _rect_inside_wafer(shot_body, usable_cfg),
            })
    x_keys = sorted({s["shotX"] for s in shots})
    y_keys = sorted({s["shotY"] for s in shots}, reverse=True)
    labeled = []
    for shot in shots:
        labeled.append({
            **shot,
            "gridShotX": x_keys.index(shot["shotX"]) + 1,
            "gridShotY": y_keys.index(shot["shotY"]) + 1,
        })
    return labeled


def _load_product_wafer_layout(product: str) -> dict:
    config = _pc.load(PC_ROOT, product)
    raw_layout = dict(config.get("wafer_layout") or {})
    wafer_layout = _build_cfg(raw_layout)
    teg_definitions = _normalize_tegs(raw_layout.get("teg_definitions") or raw_layout.get("tegs") or [])
    wafer_layout["teg_definitions"] = teg_definitions
    wafer_layout.update({
        "chip_radius_mm": _num(wafer_layout.get("waferRadius"), 150.0),
        "wafer_radius_mm": _num(wafer_layout.get("waferRadius"), 150.0),
        "shot_pitch_x_mm": _num(wafer_layout.get("shotPitchX"), 28.0),
        "shot_pitch_y_mm": _num(wafer_layout.get("shotPitchY"), 30.0),
        "shot_size_w_mm": _num(wafer_layout.get("shotSizeX"), 27.2),
        "shot_size_h_mm": _num(wafer_layout.get("shotSizeY"), 29.2),
        "teg_size_w_mm": _num(wafer_layout.get("tegSizeX"), 1.2),
        "teg_size_h_mm": _num(wafer_layout.get("tegSizeY"), 0.6),
        "chip_w_mm": _num(wafer_layout.get("chipWidth"), 3.6),
        "chip_h_mm": _num(wafer_layout.get("chipHeight"), 4.8),
        "offset_x_mm": _num(wafer_layout.get("offsetXMm"), 0.0),
        "offset_y_mm": _num(wafer_layout.get("offsetYMm"), 0.0),
        "scribe_pattern": wafer_layout.get("scribePattern") or [],
    })
    return wafer_layout


class WaferGridReq(BaseModel):
    product: str
    waferRadius: float | None = None
    shotPitchX: float | None = None
    shotPitchY: float | None = None
    shotSizeX: float | None = None
    shotSizeY: float | None = None
    tegSizeX: float | None = None
    tegSizeY: float | None = None
    chipWidth: float | None = None
    chipHeight: float | None = None
    chipCols: int | None = None
    chipRows: int | None = None
    offsetXMm: float | None = None
    offsetYMm: float | None = None
    edgeExclusionMm: float | None = None
    scribePattern: list[dict] | None = None
    teg_definitions: list[dict] = []


def _validate_grid(cfg: dict) -> list[str]:
    errs = []
    for key in ("waferRadius", "shotPitchX", "shotPitchY", "shotSizeX", "shotSizeY", "tegSizeX", "tegSizeY", "chipWidth", "chipHeight"):
        if _num(cfg.get(key), 0.0) <= 0:
            errs.append(f"{key} must be > 0")
    for key in ("chipCols", "chipRows"):
        if int(_num(cfg.get(key), 0)) <= 0:
            errs.append(f"{key} must be >= 1")
    if _num(cfg.get("shotSizeX"), 0.0) > _num(cfg.get("shotPitchX"), 0.0):
        errs.append("shotSizeX must be <= shotPitchX")
    if _num(cfg.get("shotSizeY"), 0.0) > _num(cfg.get("shotPitchY"), 0.0):
        errs.append("shotSizeY must be <= shotPitchY")
    total_scribe = sum(_num(row.get("type") == "half" and _num(cfg.get("tegSizeY"), 0.6) / 2 or _num(cfg.get("tegSizeY"), 0.6), 0.0) for row in _normalize_scribe_pattern(cfg.get("scribePattern"), int(_num(cfg.get("chipRows"), 1))))
    stack_height = total_scribe + _num(cfg.get("chipHeight"), 0.0) * int(_num(cfg.get("chipRows"), 1))
    if stack_height > _num(cfg.get("shotSizeY"), 0.0):
        errs.append("chip rows + scribe lanes exceed shotSizeY")
    if _num(cfg.get("chipWidth"), 0.0) * int(_num(cfg.get("chipCols"), 1)) > _num(cfg.get("shotSizeX"), 0.0):
        errs.append("chip columns exceed shotSizeX")
    return errs


@router.get("/grid")
def get_wafer_grid(product: str = Query("")):
    product = (product or "").strip()
    if not product:
        raise HTTPException(400, "product required")
    wafer_layout = _load_product_wafer_layout(product)
    return {"ok": True, "product": product, "wafer_layout": wafer_layout}


@router.put("/grid")
def save_wafer_grid(req: WaferGridReq, _admin=Depends(require_admin)):
    product = (req.product or "").strip()
    if not product:
        raise HTTPException(400, "product required")
    config = _pc.load(PC_ROOT, product)
    wafer_layout = _build_cfg(dict(config.get("wafer_layout") or {}))
    req_data = req.model_dump()
    for key in ("waferRadius", "shotPitchX", "shotPitchY", "shotSizeX", "shotSizeY", "tegSizeX", "tegSizeY", "chipWidth", "chipHeight", "chipCols", "chipRows", "offsetXMm", "offsetYMm", "edgeExclusionMm"):
        if req_data.get(key) is not None:
            wafer_layout[key] = req_data[key]
    wafer_layout["scribePattern"] = _normalize_scribe_pattern(req.scribePattern, int(_num(wafer_layout.get("chipRows"), 2)))
    teg_definitions = _normalize_tegs(req.teg_definitions)
    wafer_layout["teg_definitions"] = teg_definitions
    wafer_layout["tegs"] = [
        {"no": idx + 1, "name": row["label"], "x": row["dx_mm"], "y": row["dy_mm"], "flat": 0}
        for idx, row in enumerate(teg_definitions)
    ]
    validation_errors = _validate_grid(wafer_layout)
    if validation_errors:
        raise HTTPException(400, {"errors": validation_errors})
    config["wafer_layout"] = wafer_layout
    errors = _pc.validate(config)
    _pc.save(PC_ROOT, product, config)
    return {"ok": True, "errors": errors, "product": product, "teg_definitions": teg_definitions, "wafer_layout": _load_product_wafer_layout(product)}


@router.get("/edge-shots")
def edge_shots(product: str = Query(""), teg_ids: str = Query("")):
    product = (product or "").strip()
    if not product:
        raise HTTPException(400, "product required")
    wafer_layout = _load_product_wafer_layout(product)
    cfg = _build_cfg(wafer_layout)
    selected_ids = {x.strip() for x in teg_ids.split(",") if x.strip()}
    teg_definitions = [row for row in _normalize_tegs(wafer_layout.get("teg_definitions")) if not selected_ids or row["id"] in selected_ids or row["label"] in selected_ids]
    shots = _collect_shots(cfg)
    completely_inside = []
    edge_candidates = []
    for shot in shots:
        payload = {
            "shot_x": shot["gridShotX"],
            "shot_y": shot["gridShotY"],
            "raw_shot_x": shot["shotX"],
            "raw_shot_y": shot["shotY"],
        }
        if shot["completely_inside"]:
            completely_inside.append(payload)
            continue
        matched = []
        for teg in teg_definitions:
            teg_x = shot["centerX"] + teg["dx_mm"]
            teg_y = shot["centerY"] + teg["dy_mm"]
            if _in_wafer(teg_x, teg_y, cfg):
                matched.append(teg["id"])
        if matched:
            edge_candidates.append({**payload, "teg_ids": matched})
    return {
        "product": product,
        "teg_ids": [row["id"] for row in teg_definitions],
        "completely_inside": completely_inside,
        "edge_candidates": edge_candidates,
    }
