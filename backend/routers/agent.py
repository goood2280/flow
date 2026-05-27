from __future__ import annotations

from typing import Any
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core import home_orchestrator
from core.auth import current_user
from core.flowi_units import all_unit_ais, get_unit_ai
from core.flowi_units.filebrowser_ai_sql_runtime import (
    UNIT_AI_KEY as FILEBROWSER_AI_SQL_UNIT_KEY,
    filebrowser_ai_sql_graph,
    run_filebrowser_ai_sql_runtime,
)
from core.flowi_units.inform_registration_runtime import (
    UNIT_AI_KEY as INFORM_REGISTRATION_UNIT_KEY,
    inform_registration_graph,
    list_inform_registration_history,
    run_inform_registration_runtime,
)

router = APIRouter(prefix="/api/agent", tags=["agent"])


class FileBrowserAiSqlRuntimeRunReq(BaseModel):
    natural_language: str = ""
    scope: str = "db_product"
    root: str = ""
    product: str = ""
    file: str = ""
    columns: list[str] = Field(default_factory=list)
    dtypes: dict[str, str] = Field(default_factory=dict)
    sample_rows: list[dict[str, Any]] = Field(default_factory=list)
    preferred_selected_columns: list[str] = Field(default_factory=list)


class UnitAiRuntimeRunReq(BaseModel):
    model_config = {"extra": "allow"}

    prompt: str = ""
    session_id: str = ""
    action: str = "continue"
    slot_overrides: dict[str, Any] = Field(default_factory=dict)


def _unit_catalog_item(unit) -> dict[str, Any]:
    return {
        "key": unit.key(),
        "title": unit.title(),
        "description": unit.description(),
        "llm_profile": unit.llm_profile(),
        "feature_md_path": str(unit.feature_md_path()),
        "prompt_template_path": str(unit.prompt_template_path() or ""),
        "input_schema": unit.input_schema(),
        "output_schema": unit.output_schema(),
        "examples": unit.examples(),
        "handler_entry": {
            "module": unit.handler_entry().module,
            "function": unit.handler_entry().function,
            "description": unit.handler_entry().description,
        },
        "data_sources": [
            {
                "kind": source.kind,
                "path": source.path,
                "description": source.description,
            }
            for source in unit.data_sources()
        ],
    }


@router.get("/status")
def agent_reset_status() -> dict[str, Any]:
    return {
        "ok": True,
        "status": "archived_for_rebuild",
        "settings_endpoint": "/api/llm/status",
        "unit_ai_endpoint": "/api/agent/unit-ai/catalog",
    }


@router.get("/unit-ai/catalog")
def unit_ai_catalog(request: Request) -> dict[str, Any]:
    current_user(request)
    units = [_unit_catalog_item(unit) for unit in all_unit_ais()]
    return {
        "ok": True,
        "units": units,
    }


@router.get("/unit-ai/filebrowser_ai_sql/runtime/graph")
def filebrowser_ai_sql_runtime_graph(request: Request) -> dict[str, Any]:
    current_user(request)
    unit = get_unit_ai(FILEBROWSER_AI_SQL_UNIT_KEY)
    if unit is None:
        raise HTTPException(status_code=404, detail="filebrowser_ai_sql unit is not registered")
    return {
        "ok": True,
        "unit_ai": FILEBROWSER_AI_SQL_UNIT_KEY,
        "graph": filebrowser_ai_sql_graph(),
    }


@router.post("/unit-ai/filebrowser_ai_sql/runtime/run")
def filebrowser_ai_sql_runtime_run(req: FileBrowserAiSqlRuntimeRunReq, request: Request) -> dict[str, Any]:
    from routers import filebrowser as filebrowser_router

    me = filebrowser_router._require_filebrowser_user(request)
    unit = get_unit_ai(FILEBROWSER_AI_SQL_UNIT_KEY)
    if unit is None:
        raise HTTPException(status_code=404, detail="filebrowser_ai_sql unit is not registered")
    payload = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    result = run_filebrowser_ai_sql_runtime(payload, username=(me or {}).get("username") or "")
    try:
        filebrowser_router._record_filebrowser_ai_sql_history(
            (me or {}).get("username") or "",
            source="agent_test_prompt",
            request_payload=payload,
            result_payload=result,
        )
    except Exception:
        pass
    return result


@router.get("/unit-ai/inform_registration/runtime/graph")
def inform_registration_runtime_graph(request: Request) -> dict[str, Any]:
    current_user(request)
    unit = get_unit_ai(INFORM_REGISTRATION_UNIT_KEY)
    if unit is None:
        raise HTTPException(status_code=404, detail="inform_registration unit is not registered")
    return {
        "ok": True,
        "unit_ai": INFORM_REGISTRATION_UNIT_KEY,
        "graph": inform_registration_graph(),
    }


@router.post("/unit-ai/inform_registration/runtime/run")
def inform_registration_runtime_run(req: UnitAiRuntimeRunReq, request: Request) -> dict[str, Any]:
    me = current_user(request)
    unit = get_unit_ai(INFORM_REGISTRATION_UNIT_KEY)
    if unit is None:
        raise HTTPException(status_code=404, detail="inform_registration unit is not registered")
    payload = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    return run_inform_registration_runtime(
        payload,
        username=(me or {}).get("username") or "",
        request=request,
    )


@router.get("/unit-ai/inform_registration/runtime/history")
def inform_registration_runtime_history(request: Request, limit: int = 50) -> dict[str, Any]:
    me = current_user(request)
    unit = get_unit_ai(INFORM_REGISTRATION_UNIT_KEY)
    if unit is None:
        raise HTTPException(status_code=404, detail="inform_registration unit is not registered")
    return {
        "ok": True,
        "unit_ai": INFORM_REGISTRATION_UNIT_KEY,
        "history": list_inform_registration_history(limit=limit, username=(me or {}).get("username") or ""),
    }


@router.get("/unit-ai/{unit_key}/runtime/graph")
def unit_ai_runtime_graph(unit_key: str, request: Request) -> dict[str, Any]:
    current_user(request)
    unit = get_unit_ai(unit_key)
    if unit is None:
        raise HTTPException(status_code=404, detail=f"{unit_key} unit is not registered")
    if unit_key == FILEBROWSER_AI_SQL_UNIT_KEY:
        graph = filebrowser_ai_sql_graph()
    elif unit_key == INFORM_REGISTRATION_UNIT_KEY:
        graph = inform_registration_graph()
    else:
        raise HTTPException(status_code=404, detail=f"{unit_key} runtime is not available")
    return {
        "ok": True,
        "unit_ai": unit_key,
        "graph": graph,
    }


@router.post("/unit-ai/{unit_key}/runtime/run")
def unit_ai_runtime_run(unit_key: str, req: UnitAiRuntimeRunReq, request: Request) -> dict[str, Any]:
    unit = get_unit_ai(unit_key)
    if unit is None:
        raise HTTPException(status_code=404, detail=f"{unit_key} unit is not registered")
    payload = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    if unit_key == FILEBROWSER_AI_SQL_UNIT_KEY:
        from routers import filebrowser as filebrowser_router

        me = filebrowser_router._require_filebrowser_user(request)
        if not payload.get("natural_language") and payload.get("prompt"):
            payload["natural_language"] = payload.get("prompt")
        result = run_filebrowser_ai_sql_runtime(payload, username=(me or {}).get("username") or "")
        try:
            filebrowser_router._record_filebrowser_ai_sql_history(
                (me or {}).get("username") or "",
                source="agent_test_prompt",
                request_payload=payload,
                result_payload=result,
            )
        except Exception:
            pass
        return result
    if unit_key == INFORM_REGISTRATION_UNIT_KEY:
        me = current_user(request)
        return run_inform_registration_runtime(
            payload,
            username=(me or {}).get("username") or "",
            request=request,
        )
    raise HTTPException(status_code=404, detail=f"{unit_key} runtime is not available")


@router.get("/unit-ai/{unit_key}/runtime/history")
def unit_ai_runtime_history(unit_key: str, request: Request, limit: int = 50) -> dict[str, Any]:
    me = current_user(request)
    unit = get_unit_ai(unit_key)
    if unit is None:
        raise HTTPException(status_code=404, detail=f"{unit_key} unit is not registered")
    if unit_key != INFORM_REGISTRATION_UNIT_KEY:
        raise HTTPException(status_code=404, detail=f"{unit_key} history is not available")
    return {
        "ok": True,
        "unit_ai": unit_key,
        "history": list_inform_registration_history(limit=limit, username=(me or {}).get("username") or ""),
    }


@router.get("/home-flowi/runtime/graph")
def home_flowi_runtime_graph(request: Request) -> dict[str, Any]:
    current_user(request)
    return {
        "ok": True,
        "graph": home_orchestrator.build_home_runtime_graph(),
    }


@router.get("/home-flowi/runtime/runs")
def home_flowi_runtime_runs(request: Request, limit: int = 20) -> dict[str, Any]:
    current_user(request)
    return {
        "ok": True,
        "runs": home_orchestrator.list_home_runtime_runs(limit=limit),
    }


@router.get("/home-flowi/runtime/runs/{run_id}")
def home_flowi_runtime_run(run_id: str, request: Request) -> dict[str, Any]:
    current_user(request)
    run = home_orchestrator.load_home_runtime_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="home Flow-i runtime run not found")
    return {
        "ok": True,
        "run": run,
    }


def _query_limit(request: Request, default: int) -> int:
    raw = request.query_params.get("limit")
    try:
        limit = int(raw or default)
    except (TypeError, ValueError):
        return default
    return max(1, min(200, limit))


def _active_agent_get_fallback(path: str, request: Request) -> dict[str, Any] | None:
    """Serve active Agent GET endpoints even if the archived catch-all is hit."""
    if request.method != "GET":
        return None
    normalized = path.strip("/")
    if normalized == "unit-ai/catalog":
        return unit_ai_catalog(request)
    if normalized == "home-flowi/runtime/graph":
        return home_flowi_runtime_graph(request)
    if normalized == "home-flowi/runtime/runs":
        return home_flowi_runtime_runs(request, limit=_query_limit(request, 20))
    if normalized.startswith("home-flowi/runtime/runs/"):
        run_id = unquote(normalized.removeprefix("home-flowi/runtime/runs/"))
        if run_id:
            return home_flowi_runtime_run(run_id, request)

    parts = normalized.split("/")
    if len(parts) == 4 and parts[0] == "unit-ai" and parts[2] == "runtime":
        unit_key = unquote(parts[1])
        if parts[3] == "graph":
            return unit_ai_runtime_graph(unit_key, request)
        if parts[3] == "history":
            return unit_ai_runtime_history(unit_key, request, limit=_query_limit(request, 50))
    return None


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def archived_agent_endpoint(path: str, request: Request) -> dict[str, Any] | None:
    active_payload = _active_agent_get_fallback(path, request)
    if active_payload is not None:
        return active_payload
    raise HTTPException(status_code=410, detail="Agent implementation is archived for rebuild.")
