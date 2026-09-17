"""Actual DB reference management, independent of retired Flow-i learning."""
from fastapi import APIRouter, HTTPException, Request

from core import audit, flowi_db_reference
from core.auth import current_user

router = APIRouter(prefix="/api/flowi-learning", tags=["flowi-reference"])


def _require_admin(request):
    me = current_user(request)
    if not me or me.get("role") != "admin":
        raise HTTPException(403, "admin only")
    return me


@router.get("/db-reference")
def db_reference_status(request: Request):
    _require_admin(request)
    return flowi_db_reference.status()


@router.post("/db-reference/generate")
def generate_db_reference(request: Request):
    _require_admin(request)
    try:
        result = flowi_db_reference.generate_reference()
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except (RuntimeError, OSError) as exc:
        raise HTTPException(503, str(exc)) from exc
    audit.record(request, action="flowi_reference:generate", tab="admin")
    return result
