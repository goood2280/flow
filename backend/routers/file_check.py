"""Read-only matching CSV consistency report."""
from fastapi import APIRouter, Request

from core import file_check
from core.auth import current_user

router = APIRouter(prefix="/api/file-check", tags=["file-check"])


@router.get("/report")
def report(request: Request):
    current_user(request)
    return file_check.build_report()
