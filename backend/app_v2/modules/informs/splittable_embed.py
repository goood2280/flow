from __future__ import annotations

import re
from typing import Any, Callable, Iterable

from fastapi import HTTPException


ViewLoader = Callable[..., dict[str, Any]]
PlanColumnLoader = Callable[[str, str], list[str]]


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


def _first_lot_from_view(view: dict[str, Any]) -> str:
    if not isinstance(view, dict):
        return ""
    for group in view.get("header_groups") or []:
        if not isinstance(group, dict):
            continue
        label = str(group.get("label") or "").strip()
        if label and label not in {"-", "—"}:
            return label
    for value in view.get("wafer_fab_list") or []:
        label = str(value or "").strip()
        if label and label not in {"-", "—"}:
            return label
    return ""


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


def _merge_cols(*groups: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for value in group or []:
            text = str(value or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            out.append(text)
    return out


def _plan_columns_for_root(ml_product: str, root_lot_id: str, limit: int = 80) -> list[str]:
    root = str(root_lot_id or "").strip()
    if not root:
        return []
    try:
        from core.utils import load_json
        from routers.splittable import PLAN_DIR

        plans = load_json(PLAN_DIR / f"{ml_product}.json", {}).get("plans", {})
    except Exception:
        return []
    if not isinstance(plans, dict):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for cell_key, info in plans.items():
        parts = str(cell_key or "").split("|", 2)
        if len(parts) != 3 or parts[0] != root:
            continue
        value = info.get("value") if isinstance(info, dict) else None
        if not _has_st_value(value):
            continue
        col = parts[2].strip()
        if col and col not in seen:
            seen.add(col)
            out.append(col)
            if len(out) >= limit:
                break
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


def _has_st_value(value: Any) -> bool:
    text = "" if value is None else str(value)
    return bool(text and text not in {"None", "null"})


def _row_has_plan(row: dict[str, Any]) -> bool:
    if not isinstance(row, dict):
        return False
    cells = row.get("_cells") if isinstance(row.get("_cells"), dict) else {}
    return any(
        isinstance(cell, dict) and _has_st_value(cell.get("plan"))
        for cell in cells.values()
    )


def _view_plan_cell_count(view: dict[str, Any], cols: Iterable[str] | None = None) -> int:
    if not isinstance(view, dict):
        return 0
    wanted = {str(c or "").strip() for c in (cols or []) if str(c or "").strip()}
    count = 0
    for row in view.get("rows") or []:
        if not isinstance(row, dict):
            continue
        if wanted and str(row.get("_param") or "") not in wanted:
            continue
        cells = row.get("_cells") if isinstance(row.get("_cells"), dict) else {}
        count += sum(
            1
            for cell in cells.values()
            if isinstance(cell, dict) and _has_st_value(cell.get("plan"))
        )
    return count


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
        head = rows_all[:120]
        seen = {str(row.get("_param") or "") for row in head if isinstance(row, dict)}
        plan_tail = []
        for row in rows_all[120:]:
            if not isinstance(row, dict):
                continue
            param = str(row.get("_param") or "")
            if param in seen or not _row_has_plan(row):
                continue
            seen.add(param)
            plan_tail.append(row)
        rows_all = [*head, *plan_tail[:80]]

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
    plan_column_loader: PlanColumnLoader | None = None,
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
    def load_for(cols: list[str], *, root_scope: str = "", fab_scope: str = "") -> dict[str, Any]:
        use_fab_scope = fab_scope if fab_scope or root_scope else (lot if fab_input else "")
        use_root_scope = root_scope if root_scope or fab_scope else ("" if fab_input else lot)
        return loader(
            product=ml_product,
            root_lot_id=use_root_scope,
            wafer_ids="",
            prefix="ALL",
            custom_name="",
            view_mode="all",
            history_mode="all",
            fab_lot_id=use_fab_scope,
            custom_cols=",".join(cols),
        )

    view = load_for(custom)
    effective_custom = list(custom)
    extra_plan_cols: list[str] = []
    if custom and (view_loader is None or plan_column_loader is not None):
        root_key = str(view.get("root_lot_id") or _root_fallback(lot)).strip()
        extra_plan_cols = (plan_column_loader or _plan_columns_for_root)(ml_product, root_key)
        merged = _merge_cols(custom, extra_plan_cols)
        if len(merged) > len(effective_custom):
            view = load_for(merged)
            effective_custom = merged
        if fab_input and root_key and extra_plan_cols:
            root_view = load_for(effective_custom, root_scope=root_key, fab_scope="")
            if _view_plan_cell_count(root_view, extra_plan_cols) > _view_plan_cell_count(view, extra_plan_cols):
                view = root_view

    return _embed_from_view(ml_product, lot, effective_custom, fab_input, view)


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
    derived_from_view = False
    if not ml_product:
        raise HTTPException(400, "product is required")
    if not lot:
        lot = _first_lot_from_view(view)
        derived_from_view = bool(lot)
    if not lot:
        raise HTTPException(400, "lot_id is required")

    custom = _clean_custom_cols(custom_cols)
    fab_input = (True if derived_from_view else looks_like_fab_lot(lot)) if is_fab_lot is None else bool(is_fab_lot)
    return _embed_from_view(
        ml_product,
        lot,
        custom,
        fab_input,
        view,
        scope_label="CURRENT",
        current_view=True,
    )
