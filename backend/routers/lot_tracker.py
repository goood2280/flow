"""LOT Tracker API."""
from fastapi import APIRouter, HTTPException, Query, Request

from core.auth import current_user, is_page_manager, user_tab_tokens
from core.lot_tracker import track_lot
from core.paths import PATHS
from core.utils import load_json, save_json

router = APIRouter(prefix="/api/lot-tracker", tags=["lot-tracker"])

MAX_PRESET_STEPS = 200
MAX_STEP_DESC_LEN = 200


def _require_view(user: dict) -> None:
    tabs, _ = user_tab_tokens(user)
    if user.get("role") != "admin" and "lottracker" not in tabs and not is_page_manager(user, "lottracker"):
        raise HTTPException(403, "LOT Tracker access is not granted")


def _preset_path():
    store_dir = PATHS.data_root / "lot_tracker"
    store_dir.mkdir(parents=True, exist_ok=True)
    return store_dir / "preset_steps.json"


def _load_preset_doc() -> dict:
    data = load_json(_preset_path(), {})
    return data if isinstance(data, dict) else {}


def _clean_preset_steps(raw) -> list[dict]:
    """Normalize preset [{step_id, step_desc}] — strip, dedupe, cap."""
    if not isinstance(raw, list):
        raise HTTPException(400, "steps must be a list")
    cleaned: list[dict] = []
    seen: set[str] = set()
    for item in raw[:MAX_PRESET_STEPS]:
        if not isinstance(item, dict):
            continue
        step_id = str(item.get("step_id") or "").strip().upper()
        if not step_id or step_id in seen:
            continue
        seen.add(step_id)
        step_desc = str(item.get("step_desc") or "").strip()[:MAX_STEP_DESC_LEN]
        cleaned.append({"step_id": step_id, "step_desc": step_desc})
    return cleaned


@router.get("")
def get_lot_tracker(
    request: Request,
    lot_id: str = Query("", max_length=100),
    reference_lot_id: str = Query("", max_length=500),
    target_step_id: str = Query("", max_length=100),
    product: str = Query("", max_length=100),
):
    user = current_user(request)
    _require_view(user)
    try:
        return track_lot(lot_id, reference_lot_id, target_step_id, product)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/preset-steps")
def get_preset_steps(request: Request, product: str = Query("", max_length=100)):
    """Return the saved per-product target-step presets for the suggestion list."""
    user = current_user(request)
    _require_view(user)
    key = str(product or "").strip()
    if not key:
        raise HTTPException(400, "product is required")
    steps = _load_preset_doc().get(key) or []
    return {"product": key, "steps": steps if isinstance(steps, list) else []}


@router.post("/preset-steps")
def save_preset_steps(payload: dict, request: Request):
    """Save the per-product target-step presets (admin or lottracker manager)."""
    user = current_user(request)
    if user.get("role") != "admin" and not is_page_manager(user, "lottracker"):
        raise HTTPException(403, "Admin or page manager (lottracker) only")
    if not isinstance(payload, dict):
        raise HTTPException(400, "invalid request")
    key = str(payload.get("product") or "").strip()
    if not key:
        raise HTTPException(400, "product is required")
    steps = _clean_preset_steps(payload.get("steps"))
    doc = _load_preset_doc()
    doc[key] = steps
    save_json(_preset_path(), doc)
    return {"ok": True, "product": key, "steps": steps}
