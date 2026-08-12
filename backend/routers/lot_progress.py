"""Shared LOT_WF current progress API."""
from __future__ import annotations

from fastapi import APIRouter, Query, Request, Depends

from core.auth import current_user, require_admin
from core.lot_progress_cache import (
    cache_status,
    load_lot_progress_cache,
    lookup_lot_progress,
    lot_progress_snapshot,
    refresh_lot_progress_cache,
    start_lot_progress_cache_scheduler,
)

router = APIRouter(prefix="/api/lot-progress", tags=["lot-progress"])

start_lot_progress_cache_scheduler()


@router.get("/status")
def status(request: Request):
    current_user(request)
    return cache_status()


@router.get("/lookup")
def lookup(
    request: Request,
    product: str = Query(""),
    lot_id: str = Query(""),
    root_lot_id: str = Query(""),
    wafer_id: str = Query(""),
    lot_wf: str = Query(""),
    limit: int = Query(50),
):
    current_user(request)
    items = lookup_lot_progress(
        product=product,
        lot_id=lot_id,
        root_lot_id=root_lot_id,
        wafer_id=wafer_id,
        lot_wf=lot_wf,
        limit=limit,
    )
    return {"ok": True, "items": items, "best": items[0] if items else None, "count": len(items)}


@router.get("/table")
def table(
    request: Request,
    product: str = Query(""),
    lot_id: str = Query(""),
    root_lot_id: str = Query(""),
    wafer_id: str = Query(""),
    prefix: str = Query(""),
    limit: int = Query(1000),
):
    current_user(request)
    state = load_lot_progress_cache()
    prod = str(product or "").strip().upper()
    lid = str(lot_id or "").strip().upper()
    root = str(root_lot_id or "").strip().upper()
    wf = str(wafer_id or "").strip()
    pref = str(prefix or "").strip().upper()
    try:
        cap = max(1, min(int(limit or 1000), 5000))
    except Exception:
        cap = 1000
    rows = []
    for item in state.get("items") or []:
        if not isinstance(item, dict):
            continue
        item_product = str(item.get("product") or "").strip().upper()
        item_process = str(item.get("process_id") or "").strip().upper()
        item_lot = str(item.get("lot_id") or "").strip()
        item_root = str(item.get("root_lot_id") or "").strip()
        item_wf = str(item.get("wafer_id") or "").strip()
        if prod and prod not in {item_product, item_process}:
            continue
        if lid and item_lot.upper() != lid:
            continue
        if root and item_root.upper() != root:
            continue
        if wf and item_wf != wf:
            continue
        if pref and not (item_lot.upper().startswith(pref) or item_root.upper().startswith(pref)):
            continue
        rows.append({
            "product": item.get("product") or "",
            "root_lot_id": item_root,
            "wafer_id": item_wf,
            "lot_id": item_lot,
            "step_id": item.get("step_id") or "",
            "func_step": item.get("function_step") or item.get("func_step") or "",
            "function_step": item.get("function_step") or item.get("func_step") or "",
            "time": item.get("time") or item.get("tkout_time") or item.get("tkin_time") or "",
        })
        if len(rows) >= cap:
            break
    return {
        "ok": True,
        "generated_at": state.get("generated_at"),
        "count": len(rows),
        "total": state.get("count", len(state.get("items") or [])),
        "items": rows,
    }


@router.get("/snapshot")
def snapshot(
    request: Request,
    product: str = Query(""),
    lot_id: str = Query(""),
    root_lot_id: str = Query(""),
    wafer_id: str = Query(""),
    lot_wf: str = Query(""),
):
    current_user(request)
    return {"ok": True, "snapshot": lot_progress_snapshot(
        product=product,
        lot_id=lot_id,
        root_lot_id=root_lot_id,
        wafer_id=wafer_id,
        lot_wf=lot_wf,
    )}


@router.post("/refresh")
def refresh(request: Request, _a=Depends(require_admin)):
    current_user(request)
    state = refresh_lot_progress_cache(force=True)
    return {
        "ok": True,
        "generated_at": state.get("generated_at"),
        "count": state.get("count", len(state.get("items") or [])),
        "files_scanned": state.get("files_scanned", 0),
        "rows_seen": state.get("rows_seen", 0),
        "errors": state.get("errors") or [],
    }
