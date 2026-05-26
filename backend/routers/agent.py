from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core.auth import current_user
from core.flowi_units import all_unit_ais, get_unit_ai
from core.flowi_units.filebrowser_ai_sql_runtime import (
    UNIT_AI_KEY as FILEBROWSER_AI_SQL_UNIT_KEY,
    filebrowser_ai_sql_graph,
    run_filebrowser_ai_sql_runtime,
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
    return run_filebrowser_ai_sql_runtime(payload, username=(me or {}).get("username") or "")


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def archived_agent_endpoint(path: str) -> None:
    raise HTTPException(status_code=410, detail="Agent implementation is archived for rebuild.")
