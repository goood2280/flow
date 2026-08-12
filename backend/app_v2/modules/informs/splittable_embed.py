from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable, Iterable

from fastapi import HTTPException


ViewLoader = Callable[..., dict[str, Any]]


def strip_ml_prefix(value: str) -> str:
    text = str(value or "").strip()
    return text[len("ML_TABLE_"):] if text.casefold().startswith("ml_table_") else text


def ml_product_name(product: str) -> str:
    text = str(product or "").strip()
    if not text:
        return ""
    tail = text[len("ML_TABLE_"):] if text.casefold().startswith("ml_table_") else text
    return f"ML_TABLE_{tail}".upper()


def looks_like_fab_lot(lot_id: str) -> bool:
    text = str(lot_id or "").strip()
    if not text:
        return False
    if re.search(r"[._\-/]", text):
        return True
    # Root/FAB token styles often include dot/hash-like suffixes after root.
    # Keep this in sync with frontend heuristics so both paths behave similarly.
    if re.search(r"(?i)^[a-z0-9]+[._\-/][a-z0-9]+$", text):
        return True
    return False


def _root_fallback(lot_id: str) -> str:
    text = str(lot_id or "").strip()
    if not text:
        return ""
    if looks_like_fab_lot(text):
        first = re.split(r"[._\-/]", text, maxsplit=1)[0].strip()
        return first or text[:5]
    return text


def _tokenize_lot_id(value: str) -> tuple[str, str]:
    text = str(value or "").strip()
    if not text:
        return "", ""
    if re.search(r"[._\-/]", text):
        root = re.split(r"[._\-/]", text, maxsplit=1)[0].strip()
        return root, text[len(root):].strip()
    return text, ""


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


def _normalize_root(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text in {"-", "—"}:
        return ""
    return _root_fallback(text).upper()


def _plan_root_candidates(lot: str, view: dict[str, Any]) -> list[str]:
    """Resolve plan lookup roots robustly for mixed root/fab inputs."""
    out: list[str] = []
    seen: set[str] = set()
    root_hint, lot_suffix = _tokenize_lot_id(lot)
    explicit_root = _root_fallback(lot)

    def add(candidate: Any) -> None:
        root = _normalize_root(candidate)
        if not root or root in seen:
            return
        seen.add(root)
        out.append(root)

    add(view.get("root_lot_id") if isinstance(view, dict) else "")
    add(explicit_root)
    if lot_suffix:
        add(root_hint)
    add(_first_lot_from_view(view) if isinstance(view, dict) else "")
    if isinstance(view, dict):
        for value in view.get("wafer_fab_list") or []:
            add(value)
    if out and out[0]:
        # legacy alias: root 자체가 root/lot 둘 다 있을 때 양쪽 키로도 탐색.
        return out + [_normalize_root(r) for r in out if _normalize_root(r) not in seen]
    return out


def _plans_for_roots(ml_product: str, roots: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for root in roots:
        for cell_key, value in _plans_for_root(ml_product, root).items():
            for key in _plan_key_aliases(cell_key):
                if key not in out:
                    out[key] = value
    return out


def _plan_key_aliases(cell_key: Any) -> list[str]:
    text = str(cell_key or "").strip()
    if not text:
        return []
    out = [text]
    parts = text.split("|", 2)
    if len(parts) == 3:
        root = _normalize_root(parts[0])
        wafer = _wafer_key_from_header(parts[1]) or str(parts[1] or "").strip()
        param = str(parts[2] or "").strip()
        for key in (
            f"{root}|{wafer}|{param}" if root and wafer and param else "",
            f"{root}|{parts[1]}|{param}" if root and parts[1] and param else "",
        ):
            if key and key not in out:
                out.append(key)
    return out


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


def _plans_for_root(ml_product: str, root_lot_id: str) -> dict[str, Any]:
    root = _normalize_root(root_lot_id)
    if not root:
        return {}
    try:
        from routers.splittable import _load_plan_data

        plans = _load_plan_data(ml_product).get("plans", {})
    except Exception:
        return {}
    if not isinstance(plans, dict):
        return {}
    out: dict[str, Any] = {}
    for cell_key, info in plans.items():
        parts = str(cell_key or "").split("|", 2)
        if len(parts) != 3 or _normalize_root(parts[0]) != root:
            continue
        value = info.get("value") if isinstance(info, dict) else None
        if _has_st_value(value):
            out[str(cell_key)] = value
    return out


def _wafer_key_from_header(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^(?:#|WAFER|WF|W)\s*", "", text, flags=re.I).strip()
    if re.fullmatch(r"\d+", text or ""):
        try:
            return str(int(text))
        except Exception:
            return text
    return text


def _apply_saved_plans(
    ml_product: str,
    lot: str,
    view: dict[str, Any],
    *,
    append_missing_plan_rows: bool = True,
) -> dict[str, Any]:
    """Ensure Inform snapshots carry the saved SplitTable plan layer."""

    if not isinstance(view, dict):
        return view
    root_key = _normalize_root(view.get("root_lot_id") or lot)
    roots = _plan_root_candidates(lot, view)
    plans = _plans_for_roots(ml_product, roots)
    if not plans:
        return view

    headers = list(view.get("headers") or [])
    rows = view.get("rows") if isinstance(view.get("rows"), list) else []
    lot_root_hint, _ = _tokenize_lot_id(lot)
    inferred_root_hint = _normalize_root(lot_root_hint or root_key or "")
    for row in rows:
        if not isinstance(row, dict):
            continue
        param = str(row.get("_param") or "").strip()
        if not param:
            continue
        cells = row.get("_cells") if isinstance(row.get("_cells"), dict) else {}
        if not isinstance(row.get("_cells"), dict):
            row["_cells"] = cells
        for ci, header in enumerate(headers):
            idx_key = str(ci)
            cell = cells.get(idx_key) or cells.get(ci)
            if not isinstance(cell, dict):
                cell = {}
                cells[idx_key] = cell
            elif idx_key not in cells:
                cells[idx_key] = cell
            if _has_st_value(cell.get("plan")):
                continue
            cell_key = str(cell.get("key") or "").strip()
            plan = plans.get(cell_key) if cell_key else None
            if plan is None:
                wafer_key = _wafer_key_from_header(header)
                if wafer_key:
                    fallback_root = root_key or (roots[0] if roots else "")
                    if not fallback_root:
                        fallback_root = inferred_root_hint
                    cell_key = f"{fallback_root}|{wafer_key}|{param}" if fallback_root else ""
                    plan = plans.get(cell_key)
            if plan is None:
                continue
            cell["plan"] = plan
            if cell_key and not cell.get("key"):
                cell["key"] = cell_key
            actual = cell.get("actual")
            cell["mismatch"] = bool(_has_st_value(actual) and str(actual) != str(plan))
    if append_missing_plan_rows:
        _append_missing_plan_rows(view, plans)
    return view


def _append_missing_plan_rows(view: dict[str, Any], plans: dict[str, Any], limit: int = 80) -> None:
    headers = list(view.get("headers") or [])
    rows = view.get("rows") if isinstance(view.get("rows"), list) else []
    if not headers or not isinstance(rows, list):
        return
    existing = {
        str(row.get("_param") or "").strip()
        for row in rows
        if isinstance(row, dict) and str(row.get("_param") or "").strip()
    }
    header_to_idx: dict[str, str] = {}
    for ci, header in enumerate(headers):
        wafer = _wafer_key_from_header(header)
        if wafer:
            header_to_idx.setdefault(wafer, str(ci))
    if not header_to_idx:
        return
    by_param: dict[str, dict[str, dict[str, Any]]] = {}
    for raw_key, plan in plans.items():
        if not _has_st_value(plan):
            continue
        parts = str(raw_key or "").split("|", 2)
        if len(parts) != 3:
            continue
        param = parts[2].strip()
        if not param or param in existing:
            continue
        wafer = _wafer_key_from_header(parts[1])
        idx = header_to_idx.get(wafer)
        if idx is None:
            continue
        bucket = by_param.setdefault(param, {})
        bucket[idx] = {
            "actual": None,
            "plan": plan,
            "key": f"{parts[0]}|{wafer}|{param}",
            "can_plan": True,
            "mismatch": False,
        }
    appended = 0
    for param, cells in by_param.items():
        if not cells:
            continue
        rows.append({"_param": param, "_display": param, "_cells": cells})
        existing.add(param)
        appended += 1
        if appended >= limit:
            break


# ── SplitTable 화면과 같은 표시 규약 ────────────────────────────────────────
# 인폼 스냅샷(FE SplitTableSnapshotView)과 메일 본문(BE _render_embed_table_html)은
# SplitTable 그리드와 같은 모습이어야 한다. 규약이 세 곳으로 갈라지지 않게 여기 모은다.
#   항목명  : prefix + KNOB 꼬리 `_Split` 제거      (FE splitParamDisplayName)
#   숫자    : prefix 별 소수 자리수                  (FE formatCell)
#   미진행  : step_progress 기준 회색                (FE waferProgressAt)
#   열 너비 : 항목 288px / wafer 115px, Split 체크는 240·140·80px
SPLIT_FIRST_COL_PX = 288
SPLIT_DATA_COL_PX = 115
SPLIT_CHECK_PREFIX_PX = (240, 140, 80)


def split_param_display_name(name: Any, raw_param: Any = "") -> str:
    """FE splitParamDisplayName 과 같은 규약. raw `_param` 은 키로 계속 쓰이므로 라벨만 다듬는다."""
    raw = str(name or "").strip()
    if not raw:
        return ""
    source = str(raw_param or "").strip() or raw
    is_knob = source.upper().startswith("KNOB_") or raw.upper().startswith("KNOB_")
    out = re.sub(r"^[A-Za-z]+_", "", raw, count=1)
    if is_knob:
        out = re.sub(r"_Split$", "", out, flags=re.I)
    return out.strip() or raw


def format_split_cell_value(value: Any, param: Any, precision: Any = None) -> str:
    """FE formatCell 과 같은 규약. prefix 별 소수 자리수, 숫자가 아니면 원본 그대로."""
    text = "" if value is None else str(value)
    if not text or text in {"None", "null", "NaN"}:
        return text
    try:
        num = float(text)
    except (TypeError, ValueError):
        return text
    if num != num or num in (float("inf"), float("-inf")):
        return text
    pn = str(param or "").upper()
    for prefix, digits in (precision or {}).items():
        if not pn.startswith(str(prefix or "").upper() + "_"):
            continue
        if isinstance(digits, bool) or not isinstance(digits, (int, float)):
            continue
        if 0 <= digits <= 10:
            # JS toFixed 는 정확히 반값일 때 올림한다 — 파이썬 기본(half-even)과 갈라지지 않게 맞춘다.
            quant = Decimal(1).scaleb(-int(digits))
            return str(Decimal(num).quantize(quant, rounding=ROUND_HALF_UP))
    return text


def normalize_wafer_key(value: Any) -> str:
    text = re.sub(r"^(?:#|WAFER|WF|W)\s*", "", str(value or ""), flags=re.I)
    return re.sub(r"^0+(?=\d)", "", text)


class NotReachedLookup:
    """step_progress(랏/wafer 별 최신 step) 기준 미진행 칸 판정 — FE 그리드와 같은 우선순위."""

    def __init__(self, st_view: dict[str, Any] | None):
        view = st_view if isinstance(st_view, dict) else {}
        progress = view.get("step_progress")
        progress = progress if isinstance(progress, dict) else {}
        by_wafer_raw = progress.get("by_wafer") if isinstance(progress.get("by_wafer"), dict) else {}
        self._by_wafer: dict[str, set[str]] = {
            normalize_wafer_key(wafer): {str(v) for v in ((meta or {}).get("not_reached") or [])}
            for wafer, meta in by_wafer_raw.items()
            if isinstance(meta, dict)
        }
        self._root: set[str] = {str(v) for v in (progress.get("not_reached") or [])}
        headers = view.get("headers") if isinstance(view.get("headers"), list) else []
        keys = view.get("wafer_keys") if isinstance(view.get("wafer_keys"), list) else []
        self._col_keys = [
            normalize_wafer_key(keys[i] if i < len(keys) else (headers[i] if i < len(headers) else ""))
            for i in range(len(headers))
        ]
        self.has_wafer = bool(self._by_wafer)
        self.active = bool(self._by_wafer or self._root)

    def cell(self, param: Any, index: int) -> bool:
        name = str(param or "")
        if not name:
            return False
        if not self.has_wafer:
            return name in self._root
        if index < 0 or index >= len(self._col_keys):
            return False
        return name in self._by_wafer.get(self._col_keys[index], set())

    def row(self, param: Any, column_count: int) -> bool:
        name = str(param or "")
        if not name:
            return False
        if not self.has_wafer:
            return name in self._root
        if column_count <= 0:
            return False
        return all(self.cell(name, i) for i in range(column_count))


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
    step_labels = bool(view.get("step_labels"))
    applied_processes = [
        dict(item)
        for item in (view.get("applied_processes") or [])
        if isinstance(item, dict)
    ]

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
        # 적용 공정 모드의 첫 열은 raw parameter가 아니라 화면에서 확정한 공정 표기다.
        legacy_row = [str(row.get("_display") or row.get("_param") or "") if step_labels
                      else str(row.get("_param") or "")]
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

    embed = {
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
            # 화면과 같은 모습으로 그리려면 표시 규약도 스냅샷에 실어야 한다.
            # (소수 자리수 / 미진행 회색 / wafer 열 매핑)
            "precision": dict(view.get("precision") or {}),
            "step_progress": dict(view.get("step_progress") or {}) if isinstance(view.get("step_progress"), dict) else {},
            "wafer_keys": [str(v) for v in (view.get("wafer_keys") or [])],
            "step_labels": step_labels,
            "applied_processes": applied_processes,
        },
        "st_scope": st_scope,
    }
    if step_labels:
        embed["step_labels"] = True
        embed["applied_processes"] = applied_processes
    return embed


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
    def load_for(cols: list[str], *, root_scope: str = "", fab_scope: str = "") -> dict[str, Any]:
        use_fab_scope = fab_scope if fab_scope or root_scope else (lot if fab_input else "")
        use_root_scope = root_scope if root_scope or fab_scope else ("" if fab_input else lot)
        view = loader(
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
        if view_loader is None:
            view = _apply_saved_plans(
                ml_product,
                lot,
                view,
                append_missing_plan_rows=not bool(cols),
            )
        return view

    view = load_for(custom)
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
    snapshot_view = view if isinstance(view, dict) else {}
    if not isinstance(snapshot_view, dict):
        snapshot_view = {}
    if isinstance(snapshot_view.get("headers"), list):
        snapshot_view["headers"] = list(snapshot_view.get("headers") or [])
    else:
        snapshot_view["headers"] = []
    if not isinstance(snapshot_view.get("rows"), list):
        snapshot_view["rows"] = []
    if not isinstance(snapshot_view.get("wafer_fab_list"), list):
        snapshot_view["wafer_fab_list"] = []
    if not isinstance(snapshot_view.get("header_groups"), list):
        snapshot_view["header_groups"] = []
    if not isinstance(snapshot_view.get("row_labels"), dict):
        snapshot_view["row_labels"] = {}
    if not snapshot_view.get("root_lot_id"):
        inferred = _normalize_root(snapshot_view.get("root_lot_id") or _first_lot_from_view(snapshot_view) or lot)
        snapshot_view["root_lot_id"] = inferred
    snapshot_view["root_lot_id"] = _normalize_root(snapshot_view.get("root_lot_id"))
    snapshot_view = _apply_saved_plans(
        ml_product,
        lot,
        snapshot_view,
        append_missing_plan_rows=not bool(custom),
    )
    return _embed_from_view(
        ml_product,
        lot,
        custom,
        fab_input,
        snapshot_view,
        scope_label="CURRENT",
        current_view=True,
    )
