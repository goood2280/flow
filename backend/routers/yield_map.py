"""Yield Map API — product TEG products + BIN/MSR table coloring."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from core import teg_map as _teg_map
from core import yield_map as _ym
from core.auth import canonical_tab_token, current_user, require_page_manager


router = APIRouter(prefix="/api/yield-map", tags=["yield-map"])
_require_manager = require_page_manager("yieldmap")


def _require_user(user=Depends(current_user)) -> dict:
    if user.get("role") == "admin" or "yieldmap" in (user.get("page_manager") or []):
        return user
    raw = user.get("tabs") or []
    values = raw if isinstance(raw, list) else str(raw).split(",")
    if any(canonical_tab_token(value) == "yieldmap" for value in values):
        return user
    raise HTTPException(403, "Yield Map page permission required")


class ProductConfigReq(BaseModel):
    source: str = ""
    fields: dict = {}
    bin_map: list[dict] = []
    bin_colors: dict = {}


@router.get("/bootstrap")
def bootstrap(user=Depends(_require_user)):
    products = _teg_map.vehicles()
    configs = _ym.load_config().get("products") or {}
    return {
        "ok": True, "products": products, "sources": _ym.discover_sources(),
        "configs": configs,
        "can_edit": user.get("role") == "admin" or "yieldmap" in (user.get("page_manager") or []),
    }


@router.get("/preview")
def preview(source: str = Query(...), product: str = Query(""), _user=Depends(_require_user)):
    try:
        return {"ok": True, **_ym.preview(source, product)}
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/config/{product}")
def config_put(product: str, req: ProductConfigReq, _user=Depends(_require_manager)):
    try:
        return {"ok": True, "product": product,
                "config": _ym.save_product_config(product, req.model_dump())}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/map")
def get_map(product: str = Query(...), lot_id: str = Query(""),
            wafer_id: str = Query(""), _user=Depends(_require_user)):
    try:
        return {"ok": True, **_ym.map_data(product, lot_id=lot_id, wafer_id=wafer_id)}
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
