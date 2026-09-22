"""LOT Tracker API."""
from fastapi import APIRouter, HTTPException, Query, Request

from core.auth import current_user, is_page_manager, parse_tab_tokens
from core.lot_tracker import track_lot

router = APIRouter(prefix="/api/lot-tracker", tags=["lot-tracker"])


@router.get("")
def get_lot_tracker(
    request: Request,
    lot_id: str = Query("", max_length=100),
    reference_lot_id: str = Query("", max_length=500),
    target_step_id: str = Query("", max_length=100),
    product: str = Query("", max_length=100),
):
    user = current_user(request)
    tabs, _ = parse_tab_tokens(user.get("tabs", ""))
    if user.get("role") != "admin" and "lottracker" not in tabs and not is_page_manager(user, "lottracker"):
        raise HTTPException(403, "LOT Tracker access is not granted")
    try:
        return track_lot(lot_id, reference_lot_id, target_step_id, product)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
