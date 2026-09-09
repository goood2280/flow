"""Read-only Flow feature calls available to the home data chat planner.

This module deliberately calls a small, named set of router functions instead
of accepting URLs or function names from the model.  Passing the original
request keeps each feature's existing authentication and visibility checks in
the execution path.
"""
from __future__ import annotations

from typing import Any


def _object_schema(properties: dict[str, dict], required: list[str] | None = None) -> dict:
    schema = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


ACTIONS = {
    "lot_management.table": {
        "description": "Read one product's Lot Management table, including its current WIP status unless disabled.",
        "parameters": _object_schema(
            {
                "product": {"type": "string", "description": "Exact product name."},
                "lot_id": {"type": "string", "description": "Optional exact lot_id or root_lot_id filter.", "default": ""},
                "include_status": {"type": "boolean", "default": True},
            },
            ["product"],
        ),
    },
    "lot_management.my_lots": {
        "description": "Read the signed-in user's watched lots and their Lot Management/WIP status.",
        "parameters": _object_schema({}),
    },
    "lot_management.status": {
        "description": "Read the current step, step description, and wafer quantity for one lot in a product.",
        "parameters": _object_schema(
            {
                "product": {"type": "string", "description": "Exact product name."},
                "lot_id": {"type": "string", "description": "Exact lot ID."},
            },
            ["product", "lot_id"],
        ),
    },
    "dashboard.summary": {
        "description": "Read the Dashboard TAT, DPML, WIP, slow-lot, and stuck-lot summary.",
        "parameters": _object_schema(
            {"product": {"type": "string", "description": "Product filter; empty means all visible products.", "default": ""}}
        ),
    },
    "dashboard.stuck_lots": {
        "description": "Read Dashboard lots whose elapsed time is at least the requested stuck-hour threshold.",
        "parameters": _object_schema(
            {
                "product": {"type": "string", "default": ""},
                "days": {"type": "integer", "minimum": 1, "maximum": 365, "default": 30},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                "hours": {"type": "integer", "minimum": 0, "maximum": 8760, "default": 24},
            }
        ),
    },
    "dashboard.charts": {
        "description": "List Dashboard charts visible to the signed-in user, including IDs needed to fetch chart data.",
        "parameters": _object_schema({}),
    },
    "dashboard.chart_data": {
        "description": "Read one visible Dashboard chart's saved configuration and actual snapshot data by chart ID.",
        "parameters": _object_schema(
            {"chart_id": {"type": "string", "description": "Exact ID returned by dashboard.charts."}},
            ["chart_id"],
        ),
    },
    "lot_progress.lookup": {
        "description": "Look up the signed-in user's current cached WIP rows by product, lot, root lot, wafer, or LOT_WF.",
        "parameters": _object_schema(
            {
                "product": {"type": "string", "default": ""},
                "lot_id": {"type": "string", "default": ""},
                "root_lot_id": {"type": "string", "default": ""},
                "wafer_id": {"type": "string", "default": ""},
                "lot_wf": {"type": "string", "default": ""},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            }
        ),
    },
    "lot_requests.list": {
        "description": "List visible Lot Request board entries with optional filters; results shown inline are capped at 200 rows.",
        "parameters": _object_schema(
            {
                "status": {"type": "string", "default": ""},
                "request_type": {"type": "string", "default": ""},
                "product": {"type": "string", "default": ""},
                "requester_team": {"type": "string", "default": ""},
                "author": {"type": "string", "default": ""},
                "q": {"type": "string", "default": ""},
                "mine": {"type": "boolean", "default": False},
            }
        ),
    },
}


def _validate_params(action: str, params: Any) -> dict:
    if action not in ACTIONS:
        raise ValueError(f"unsupported feature action: {action}")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ValueError("feature params must be an object")
    allowed = set(ACTIONS[action]["parameters"]["properties"])
    unexpected = sorted(set(params) - allowed)
    if unexpected:
        raise ValueError(f"unsupported parameters for {action}: {', '.join(unexpected)}")
    return params


def _text(params: dict, name: str, *, required: bool = False, default: str = "") -> str:
    value = params.get(name, default)
    if value is None:
        value = default
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{name} is required")
    if len(value) > 300:
        raise ValueError(f"{name} is too long")
    return value


def _boolean(params: dict, name: str, default: bool) -> bool:
    value = params.get(name, default)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _integer(params: dict, name: str, default: int, minimum: int, maximum: int) -> int:
    value = params.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _display_cell(value: Any) -> Any:
    """Unwrap common cell envelopes while leaving JSON-safe values intact."""
    if isinstance(value, dict):
        for key in ("display", "value", "actual", "text"):
            candidate = value.get(key)
            if candidate is not None and not isinstance(candidate, (dict, list)):
                return candidate
    return value


def _lot_table(doc: dict, *, default_product: str = "") -> dict:
    raw_columns = doc.get("columns") if isinstance(doc, dict) else []
    column_ids: list[str] = []
    for column in raw_columns or []:
        column_id = str(column.get("id") or "").strip() if isinstance(column, dict) else str(column or "").strip()
        if column_id and column_id not in column_ids:
            column_ids.append(column_id)

    rows: list[dict] = []
    has_product = False
    for raw in (doc.get("rows") or []) if isinstance(doc, dict) else []:
        if not isinstance(raw, dict):
            continue
        values = raw.get("values") if isinstance(raw.get("values"), dict) else raw
        row = {str(key): _display_cell(value) for key, value in values.items() if key not in {"id", "values", "colors"}}
        product = str(raw.get("product") or default_product or "").strip()
        if product:
            row = {"product": product, **row}
            has_product = True
        rows.append(row)

    for row in rows:
        for key in row:
            if key != "product" and key not in column_ids:
                column_ids.append(key)
    columns = (["product"] if has_product else []) + column_ids
    return {"rows": rows, "columns": columns, "total": int(doc.get("total") or len(rows))}


def _plain_table(rows: Any, *, total: int | None = None, columns: list[str] | None = None) -> dict:
    clean_rows = [dict(row) for row in (rows or []) if isinstance(row, dict)]
    if columns is None:
        columns = []
        for row in clean_rows:
            for key in row:
                if key not in columns:
                    columns.append(key)
    return {"rows": clean_rows, "columns": columns, "total": len(clean_rows) if total is None else int(total)}


def _base_tool(feature: str, action: str, source: str) -> dict:
    return {"feature": feature, "action": action, "sources": [source], "warnings": []}


def execute_feature(action: str, params: dict | None, request: Any) -> dict:
    """Execute one whitelisted, read-only feature operation."""
    action = str(action or "").strip()
    params = _validate_params(action, params)

    if action == "lot_management.table":
        from routers import lot_management

        product = _text(params, "product", required=True)
        lot_id = _text(params, "lot_id")
        include_status = _boolean(params, "include_status", True)
        data = lot_management.get_table(request=request, product=product, include_status=include_status)
        tool = _base_tool("lot_management", action, "/api/lot-management/table")
        tool["table"] = _lot_table(data, default_product=product)
        if lot_id:
            wanted = lot_id.casefold()
            filtered = [
                row for row in tool["table"]["rows"]
                if any(str(row.get(key) or "").strip().casefold() == wanted for key in ("lot_id", "root_lot_id"))
            ]
            tool["table"]["rows"] = filtered
            tool["table"]["total"] = len(filtered)
        tool["context"] = {
            "product": product,
            "lot_management_version": int(data.get("version") or 0),
            **({"lot_id": lot_id} if lot_id else {}),
        }
        return tool

    if action == "lot_management.my_lots":
        from routers import lot_management

        data = lot_management.get_my_lots(request=request)
        tool = _base_tool("lot_management", action, "/api/lot-management/my-lots")
        tool["table"] = _lot_table(data)
        tool["context"] = {"watched_lots": list(data.get("watched_lots") or [])}
        return tool

    if action == "lot_management.status":
        from routers import lot_management

        product = _text(params, "product", required=True)
        lot_id = _text(params, "lot_id", required=True)
        data = lot_management.get_lot_status(request=request, product=product, lot_id=lot_id)
        tool = _base_tool("lot_management", action, "/api/lot-management/lot-status")
        tool["table"] = _plain_table([data])
        tool["context"] = {"product": product, "lot_id": lot_id}
        return tool

    if action == "dashboard.summary":
        from routers import dashboard

        product = _text(params, "product")
        data = dashboard.dashboard_summary(request=request, product=product)
        tool = _base_tool("dashboard", action, "/api/dashboard/summary")
        tool["table"] = _plain_table([data])
        tool["context"] = {"product": str(data.get("product") or product)}
        if data.get("note"):
            tool["warnings"].append(str(data["note"]))
        return tool

    if action == "dashboard.stuck_lots":
        from routers import dashboard

        product = _text(params, "product")
        days = _integer(params, "days", 30, 1, 365)
        limit = _integer(params, "limit", 50, 1, 200)
        hours = _integer(params, "hours", 24, 0, 8760)
        data = dashboard.stuck_lots(
            request=request,
            product=product,
            days=days,
            limit=limit,
            hours=hours,
        )
        tool = _base_tool("dashboard", action, "/api/dashboard/stuck-lots")
        tool["table"] = _plain_table(data.get("lots"), total=int(data.get("count") or 0))
        tool["context"] = {
            "product": str(data.get("product") or product),
            "days": days,
            "hours": int(data.get("hours") if data.get("hours") is not None else hours),
        }
        return tool

    if action == "dashboard.charts":
        from routers import dashboard

        data = dashboard.get_charts(request=request)
        columns = ["id", "title", "chart_type", "product", "source_type", "x_col", "y_expr"]
        rows = [{key: chart.get(key) for key in columns} for chart in (data.get("charts") or []) if isinstance(chart, dict)]
        tool = _base_tool("dashboard", action, "/api/dashboard/charts")
        tool["table"] = _plain_table(rows, columns=columns)
        tool["context"] = {"charts": [{"id": row.get("id"), "title": row.get("title")} for row in rows]}
        return tool

    if action == "dashboard.chart_data":
        from routers import dashboard

        chart_id = _text(params, "chart_id", required=True)
        data = dashboard.get_chart_data(request=request, chart_id=chart_id)
        config = data.get("config") if isinstance(data.get("config"), dict) else {}
        chart_result = {**config, **data}
        chart_result["chart_id"] = str(data.get("chart_id") or chart_id)
        chart_result["chart_type"] = str(data.get("chart_type") or config.get("chart_type") or "scatter")
        tool = _base_tool("dashboard", action, "/api/dashboard/data")
        tool["chart_result"] = chart_result
        points = data.get("points") if isinstance(data.get("points"), list) else []
        if points:
            tool["table"] = _plain_table(points, total=int(data.get("total") or len(points)))
        if data.get("error"):
            tool["warnings"].append(str(data["error"]))
        tool["context"] = {
            "chart_id": chart_result["chart_id"],
            "chart_title": str(config.get("title") or data.get("title") or ""),
        }
        return tool

    if action == "lot_progress.lookup":
        from routers import lot_progress

        filters = {
            name: _text(params, name)
            for name in ("product", "lot_id", "root_lot_id", "wafer_id", "lot_wf")
        }
        if not any(filters.values()):
            raise ValueError("lot_progress.lookup requires at least one lookup filter")
        limit = _integer(params, "limit", 50, 1, 200)
        data = lot_progress.lookup(request=request, limit=limit, **filters)
        rows = data.get("items") if isinstance(data.get("items"), list) else []
        tool = _base_tool("lot_progress", action, "/api/lot-progress/lookup")
        tool["table"] = _plain_table(rows, total=int(data.get("count") or len(rows)))
        tool["context"] = {
            key: value for key, value in filters.items() if value
        }
        return tool

    if action == "lot_requests.list":
        from routers import lot_requests

        filters = {
            name: _text(params, name)
            for name in ("status", "request_type", "product", "requester_team", "author", "q")
        }
        mine = _boolean(params, "mine", False)
        data = lot_requests.list_requests(request=request, mine=mine, **filters)
        rows = [row for row in (data.get("requests") or []) if isinstance(row, dict)]
        tool = _base_tool("lot_requests", action, "/api/lot-requests")
        tool["table"] = _plain_table(rows[:200], total=int(data.get("total") or len(rows)))
        tool["context"] = {
            **{key: value for key, value in filters.items() if value},
            "mine": mine,
            "can_process": bool(data.get("can_process")),
        }
        if len(rows) > 200:
            tool["warnings"].append("표시는 최신 200개 요청으로 제한했습니다.")
        return tool

    # _validate_params makes this unreachable and keeps dispatch exhaustive.
    raise ValueError(f"unsupported feature action: {action}")
