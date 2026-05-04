from __future__ import annotations

import re
from typing import Any, Callable, Iterable

from fastapi import HTTPException


ViewLoader = Callable[..., dict[str, Any]]


def strip_ml_prefix(value: str) -> str:
    text = str(value or "").strip()
    return text[len("ML_TABLE_"):] if text.startswith("ML_TABLE_") else text


def ml_product_name(product: str) -> str:
    text = str(product or "").strip()
    if not text:
        return ""
    return text if text.startswith("ML_TABLE_") else f"ML_TABLE_{text}"


def looks_like_fab_lot(lot_id: str) -> bool:
    text = str(lot_id or "").strip()
    return bool(text and re.search(r"[._\-/]", text))


def _root_fallback(lot_id: str) -> str:
    text = str(lot_id or "").strip()
    if not text:
        return ""
    if looks_like_fab_lot(text):
        first = re.split(r"[._\-/]", text, maxsplit=1)[0].strip()
        return first or text[:5]
    return text


def _clean_custom_cols(values: Iterable[str] | str | None) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        raw = values.split(",")
    else:
        raw = values
    out: list[str] = []
    seen: set[str] = set()
    for value in raw:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _cell_text(cell: dict[str, Any]) -> str:
    actual = cell.get("actual")
    plan = cell.get("plan")
    actual_text = "" if actual is None else str(actual)
    plan_text = "" if plan is None else str(plan)
    if plan_text and actual_text and plan_text != actual_text:
        return f"{actual_text} → {plan_text}"
    if plan_text and actual_text and plan_text == actual_text:
        return f"✓ {plan_text} (plan 적용)"
    return plan_text or actual_text


def _embed_from_view(
    ml_product: str,
    lot: str,
    custom: list[str],
    fab_input: bool,
    view: dict[str, Any],
    *,
    scope_label: str | None = None,
    current_view: bool = False,
) -> dict[str, Any]:
    if not isinstance(view, dict):
        view = {}

    headers = list(view.get("headers") or [])
    rows_all = list(view.get("rows") or [])
    root_key = str(view.get("root_lot_id") or _root_fallback(lot)).strip()

    if custom:
        keep = set(custom)
        by_param = {
            str(row.get("_param") or ""): row
            for row in rows_all
            if isinstance(row, dict) and str(row.get("_param") or "") in keep
        }
        rows_all = [by_param.get(col) or {"_param": col, "_cells": {}} for col in custom]
    else:
        rows_all = rows_all[:120]

    legacy_rows = []
    for row in rows_all:
        if not isinstance(row, dict):
            continue
        cells = row.get("_cells") if isinstance(row.get("_cells"), dict) else {}
        legacy_row = [str(row.get("_param") or "")]
        for idx, _header in enumerate(headers):
            cell = cells.get(str(idx)) or cells.get(idx) or {}
            legacy_row.append(_cell_text(cell if isinstance(cell, dict) else {}))
        legacy_rows.append(legacy_row)

    label = scope_label or (f"CUSTOM({len(custom)})" if custom else "ALL")
    lot_label = f"fab_lot={lot}" if fab_input else f"root_lot={lot}"
    note = view.get("msg") or f"{len(rows_all)} params · {lot_label} · scope={label}"
    st_scope = {
        "prefix": "" if custom else "ALL",
        "custom_name": "",
        "inline_cols": custom,
    }
    if current_view:
        st_scope["snapshot_source"] = "current_splittable"
        st_scope["lot_id"] = lot

    return {
        "source": f"SplitTable/{strip_ml_prefix(ml_product)} @ {lot} · {label}",
        "columns": ["parameter", *headers],
        "rows": legacy_rows,
        "note": str(note or "")[:500],
        "st_view": {
            "headers": headers,
            "rows": rows_all,
            "wafer_fab_list": list(view.get("wafer_fab_list") or []),
            "header_groups": list(view.get("header_groups") or []),
            "row_labels": dict(view.get("row_labels") or {}),
            "root_lot_id": root_key,
        },
        "st_scope": st_scope,
    }


def _load_view(**kwargs) -> dict[str, Any]:
    from routers.splittable import view_split

    return view_split(**kwargs)


def build_splittable_embed(
    product: str,
    lot_id: str,
    custom_cols: Iterable[str] | str | None = None,
    is_fab_lot: bool | None = None,
    view_loader: ViewLoader | None = None,
) -> dict[str, Any]:
    """Build the exact Inform embed payload from the SplitTable view pipeline."""

    ml_product = ml_product_name(product)
    lot = str(lot_id or "").strip()
    if not ml_product:
        raise HTTPException(400, "product is required")
    if not lot:
        raise HTTPException(400, "lot_id is required")

    custom = _clean_custom_cols(custom_cols)
    fab_input = looks_like_fab_lot(lot) if is_fab_lot is None else bool(is_fab_lot)
    loader = view_loader or _load_view
    view = loader(
        product=ml_product,
        root_lot_id="" if fab_input else lot,
        wafer_ids="",
        prefix="ALL",
        custom_name="",
        view_mode="all",
        history_mode="all",
        fab_lot_id=lot if fab_input else "",
        custom_cols=",".join(custom),
    )
    return _embed_from_view(ml_product, lot, custom, fab_input, view)


def build_splittable_embed_from_view(
    product: str,
    lot_id: str,
    view: dict[str, Any],
    custom_cols: Iterable[str] | str | None = None,
    is_fab_lot: bool | None = None,
) -> dict[str, Any]:
    """Build an Inform embed from the already-rendered SplitTable view payload."""

    ml_product = ml_product_name(product)
    lot = str(lot_id or "").strip()
    if not ml_product:
        raise HTTPException(400, "product is required")
    if not lot:
        raise HTTPException(400, "lot_id is required")

    custom = _clean_custom_cols(custom_cols)
    fab_input = looks_like_fab_lot(lot) if is_fab_lot is None else bool(is_fab_lot)
    return _embed_from_view(
        ml_product,
        lot,
        custom,
        fab_input,
        view,
        scope_label="CURRENT",
        current_view=True,
    )
