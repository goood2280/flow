"""Business tab API for queued Auto report PPT generation."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core import auto_report
from core.audit import record
from core.auth import current_user

router = APIRouter(prefix="/api/auto-report", tags=["auto-report"])
match_router = APIRouter(prefix="/api/autoreport", tags=["auto-report-compat"])


class GenerateReq(BaseModel):
    key: str


@router.get("/config")
def config(_user=Depends(current_user)):
    from core import worker_dispatch

    payload = auto_report.preflight()
    worker = worker_dispatch.status()
    payload["execution"] = {
        "server_role": worker.get("role"),
        "worker_alive": bool(worker.get("worker_alive")),
        "queue_depth": int(worker.get("queue_depth") or 0),
    }
    payload["history"] = auto_report.history_status()
    return payload


@router.get("/history")
def history(_user=Depends(current_user)):
    return auto_report.history_status()


@router.get("/jobs")
def jobs(limit: int = 100, user=Depends(current_user)):
    return {
        "jobs": auto_report.list_jobs(
            str(user.get("username") or ""),
            is_admin=user.get("role") == "admin",
            limit=limit,
        )
    }


@router.get("/jobs/{job_id}")
def job_status(job_id: str, user=Depends(current_user)):
    row = auto_report.refresh_job(auto_report.read_job(job_id))
    if not row:
        raise HTTPException(404, "작업을 찾을 수 없습니다")
    if user.get("role") != "admin" and row.get("username") != user.get("username"):
        raise HTTPException(403, "다른 사용자의 작업입니다")
    return {"job": auto_report.public_job(row)}


@router.post("/jobs")
def generate(req: GenerateReq, request: Request, user=Depends(current_user)):
    try:
        row = auto_report.enqueue(req.key, str(user.get("username") or ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(409, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    record(request, "auto-report:enqueue", detail=str(row.get("key") or ""), tab="autoreport")
    return {"ok": True, "job": row}


@router.get("/jobs/{job_id}/download")
def download(job_id: str, request: Request, user=Depends(current_user)):
    try:
        row, path = auto_report.output_for(
            job_id,
            str(user.get("username") or ""),
            is_admin=user.get("role") == "admin",
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    auto_report.record_download(row, path, str(user.get("username") or ""))
    record(request, "auto-report:download", detail=str(row.get("key") or ""), tab="autoreport")
    return FileResponse(
        str(path),
        filename=path.name,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Cache-Control": "no-store"},
    )


# Compatibility path for proxies/deployments that normalized the original
# hyphenated segment. Both surfaces use the exact same authenticated handlers.
match_router.add_api_route("/config", config, methods=["GET"])
match_router.add_api_route("/history", history, methods=["GET"])
match_router.add_api_route("/jobs", jobs, methods=["GET"])
match_router.add_api_route("/jobs", generate, methods=["POST"])
match_router.add_api_route("/jobs/{job_id}", job_status, methods=["GET"])
match_router.add_api_route("/jobs/{job_id}/download", download, methods=["GET"])
