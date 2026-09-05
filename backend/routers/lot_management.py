"""Product-scoped LOT management tables with snapshot version history."""
from __future__ import annotations

import datetime as dt
import hashlib
import re
import threading
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from core.audit import record as audit_record
from core.auth import current_user, require_page_manager
from core.paths import PATHS
from core import product_order as _product_order
from core.utils import load_json, save_json


router = APIRouter(prefix="/api/lot-management", tags=["lot-management"])

STORE_DIR = PATHS.data_root / "lot_management"
TABLE_DIR = STORE_DIR / "tables"
VERSION_DIR = STORE_DIR / "versions"
for _directory in (TABLE_DIR, VERSION_DIR):
    _directory.mkdir(parents=True, exist_ok=True)

DEFAULT_COLUMNS = [
    {"id": "purpose", "label": "purpose"},
    {"id": "lot_id", "label": "lot_id"},
    {"id": "current_step_id", "label": "현step_id"},
    {"id": "alert_step_id", "label": "알람 step_id"},
    {"id": "step_desc", "label": "step_desc"},
    {"id": "qty", "label": "Qty"},
    {"id": "comment", "label": "comment"},
]
REQUIRED_COLUMN_IDS = {column["id"] for column in DEFAULT_COLUMNS}
COMPUTED_COLUMN_IDS = {"current_step_id", "step_desc", "qty"}
PALETTE = {
    "#ffffff", "#f3f4f6", "#d1d5db", "#fecaca", "#fed7aa",
    "#fef3c7", "#d9f99d", "#bbf7d0", "#99f6e4", "#a5f3fc",
    "#bfdbfe", "#c7d2fe", "#ddd6fe", "#e9d5ff", "#f5d0fe",
    "#fbcfe8", "#fee2e2", "#ffedd5", "#ecfccb", "#e0f2fe",
}
MAX_COLUMNS = 100
MAX_ROWS = 5000
VERSION_CAP = 50
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


@router.get("/product-order")
def get_product_order():
    return {"product_order": _product_order.load_product_order()}


@router.post("/product-order")
def save_product_order(req: dict, _perm=Depends(require_page_manager("lotmanage"))):
    order = _product_order.save_product_order(req.get("product_order"))
    return {"ok": True, "product_order": order}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _key(product: str) -> str:
    value = str(product or "").strip()
    if not value:
        raise HTTPException(400, "product is required")
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]
    return digest


def _paths(product: str):
    key = _key(product)
    return TABLE_DIR / f"{key}.json", VERSION_DIR / key


def _is_step_reached_or_exceeded(current_step: str, alert_step: str) -> bool:
    curr = str(current_step or "").strip()
    alert = str(alert_step or "").strip()
    if not curr or not alert:
        return False
    if curr.upper() == alert.upper():
        return True
    curr_digits = re.findall(r"\d+", curr)
    alert_digits = re.findall(r"\d+", alert)
    if curr_digits and alert_digits:
        try:
            return int(curr_digits[0]) >= int(alert_digits[0])
        except Exception:
            pass
    return curr.upper() >= alert.upper()


def _lock(product: str) -> threading.RLock:
    key = _key(product)
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _empty(product: str) -> dict:
    return {
        "product": str(product).strip(),
        "version": 0,
        "columns": [dict(c) for c in DEFAULT_COLUMNS],
        "rows": [],
        "colors": {},
        "updated_at": "",
        "updated_by": "",
        "note": "",
    }


def _load(product: str) -> dict:
    path, _ = _paths(product)
    raw = load_json(path, None)
    if not isinstance(raw, dict):
        return _empty(product)
    doc = _empty(product)
    doc.update(raw)
    doc["columns"] = _ensure_required_columns(doc.get("columns"))
    return doc


def _ensure_required_columns(raw: Any) -> list[dict]:
    """Keep the fixed LOT columns first and preserve user-defined columns."""
    custom_columns = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        column_id = str(item.get("id") or "").strip()
        if column_id and column_id not in REQUIRED_COLUMN_IDS:
            custom_columns.append(dict(item))
    return [dict(column) for column in DEFAULT_COLUMNS] + custom_columns


def _clean_columns(raw: Any) -> list[dict]:
    if not isinstance(raw, list) or not raw:
        raise HTTPException(400, "at least one column is required")
    out, seen = [], set()
    for index, item in enumerate(raw[:MAX_COLUMNS]):
        if isinstance(item, dict):
            column_id = str(item.get("id") or "").strip()
            label = str(item.get("label") or column_id).strip()
        else:
            column_id = str(item or "").strip()
            label = column_id
        if not column_id:
            column_id = f"column_{index + 1}"
        column_id = column_id[:80]
        if column_id in seen:
            raise HTTPException(400, f"duplicate column id: {column_id}")
        seen.add(column_id)
        out.append({"id": column_id, "label": (label or column_id)[:120]})
    return out


def _clean_rows(raw: Any, column_ids: set[str]) -> list[dict]:
    if not isinstance(raw, list):
        raise HTTPException(400, "rows must be a list")
    if len(raw) > MAX_ROWS:
        raise HTTPException(400, f"rows exceed limit ({MAX_ROWS})")
    out, seen = [], set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        row_id = str(item.get("id") or uuid.uuid4().hex).strip()[:80]
        if not row_id or row_id in seen:
            row_id = uuid.uuid4().hex
        seen.add(row_id)
        values = item.get("values") if isinstance(item.get("values"), dict) else item
        clean_values = {cid: str(values.get(cid, "") if values.get(cid) is not None else "")[:20000]
                        for cid in column_ids}
        for column_id in COMPUTED_COLUMN_IDS:
            if column_id in clean_values:
                clean_values[column_id] = ""
        out.append({"id": row_id, "values": clean_values})
    return out


def _latest_status_by_lot(product: str, lot_ids: list[str]) -> dict[str, dict]:
    from core.lot_progress_cache import canonical_lot_progress_summaries

    # Keep this source identical to Dashboard > WIP.  The legacy scanner JSON
    # cache has a separate owner/refresh cycle and can be empty while the
    # dashboard's canonical latest-lot parquet already has the LOT.
    summaries = canonical_lot_progress_summaries(lot_ids, product=product)
    return _attach_step_descriptions(summaries, product)


def _summary_qty(summary: dict | None) -> int | None:
    """Return a positive unique-wafer count, or None for the UI dash."""
    try:
        count = int((summary or {}).get("wafer_count") or 0)
    except (TypeError, ValueError):
        count = 0
    return count if count > 0 else None


def _attach_step_descriptions(
    summaries: dict[str, dict],
    product: str,
    *,
    index: tuple[dict, dict] | None = None,
) -> dict[str, dict]:
    """Attach the exact product + step_id value from Vehicle_matching.csv."""
    from core import fab_reference
    from core.latest_lot_cache_format import normalize_product
    from core.lot_wip import step_desc_index

    # Lot Management must reflect a newly edited Vehicle_matching.csv on the
    # next table load.  Bypass lot_wip's five-minute memo and build the small
    # index from the current master file.  Do not use the global step-only
    # fallback when a product is known: the same step_id may have a different
    # description in another product.
    step_index = (
        index
        if index is not None
        else step_desc_index(rows=fab_reference.vehicle_matching_rows())
    )
    by_product_step, by_step = step_index
    product_key = normalize_product(product)
    out = {}
    for lot_id, raw_summary in (summaries or {}).items():
        summary = dict(raw_summary or {})
        step_id = str(summary.get("step_id") or "").strip()
        step_key = step_id.upper()
        vehicle_entry = (
            by_product_step.get((product_key, step_key), {})
            if product_key
            else by_step.get(step_key, {})
        )
        summary["step_desc"] = (
            str(vehicle_entry.get("step_desc") or "").strip()
            or str(summary.get("func_step") or "").strip()
        )
        out[lot_id] = summary
    return out


def _with_latest_cache_fields(doc: dict) -> dict:
    """Overlay read-only current step and wafer quantity from the latest cache."""
    result = dict(doc)
    result["columns"] = _ensure_required_columns(doc.get("columns"))
    source_rows = [row for row in (doc.get("rows") or []) if isinstance(row, dict)]
    lot_ids = []
    for row in source_rows:
        values = row.get("values") if isinstance(row.get("values"), dict) else {}
        lot_id = str(values.get("lot_id") or "").strip()
        if lot_id:
            lot_ids.append(lot_id)
    try:
        summaries = _latest_status_by_lot(str(doc.get("product") or ""), lot_ids)
    except Exception:
        summaries = {}
    rows = []
    for row in source_rows:
        next_row = dict(row)
        values = dict(row.get("values") if isinstance(row.get("values"), dict) else {})
        lot_id = str(values.get("lot_id") or "").strip()
        summary = summaries.get(lot_id.upper()) or {}
        values["current_step_id"] = str(summary.get("step_id") or "")
        values["step_desc"] = str(summary.get("step_desc") or "")
        qty = _summary_qty(summary)
        values["qty"] = str(qty) if qty is not None else "-"
        next_row["values"] = values
        rows.append(next_row)
    result["rows"] = rows
    return result


def _clean_colors(raw: Any, row_ids: set[str], column_ids: set[str]) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, value in raw.items():
        row_id, sep, column_id = str(key).partition(":")
        color = str(value or "").lower()
        if sep and row_id in row_ids and column_id in column_ids and color in PALETTE:
            out[f"{row_id}:{column_id}"] = color
    return out


def _write_snapshot(product: str, doc: dict) -> None:
    _, version_dir = _paths(product)
    version_dir.mkdir(parents=True, exist_ok=True)
    version = int(doc.get("version") or 0)
    save_json(version_dir / f"v{version:08d}.json", doc, indent=2)
    snapshots = sorted(version_dir.glob("v*.json"), reverse=True)
    for stale in snapshots[VERSION_CAP:]:
        try:
            stale.unlink()
        except OSError:
            pass


def _version_diff(previous: dict, current: dict) -> list[dict]:
    """Return user-facing changes between two LOT table snapshots."""
    changes: list[dict] = []
    prev_columns = {str(c.get("id") or ""): c for c in (previous.get("columns") or []) if isinstance(c, dict)}
    cur_columns = {str(c.get("id") or ""): c for c in (current.get("columns") or []) if isinstance(c, dict)}
    for column_id, column in cur_columns.items():
        if column_id not in prev_columns:
            changes.append({"type": "column_added", "column": column.get("label") or column_id, "old": "", "new": column.get("label") or column_id})
        elif str(prev_columns[column_id].get("label") or column_id) != str(column.get("label") or column_id):
            changes.append({"type": "column_renamed", "column": column_id, "old": prev_columns[column_id].get("label") or column_id, "new": column.get("label") or column_id})
    for column_id, column in prev_columns.items():
        if column_id not in cur_columns:
            changes.append({"type": "column_removed", "column": column.get("label") or column_id, "old": column.get("label") or column_id, "new": ""})

    prev_rows = {str(r.get("id") or ""): r for r in (previous.get("rows") or []) if isinstance(r, dict)}
    cur_rows = {str(r.get("id") or ""): r for r in (current.get("rows") or []) if isinstance(r, dict)}
    for row_id, row in cur_rows.items():
        values = row.get("values") if isinstance(row.get("values"), dict) else {}
        lot_id = str(values.get("lot_id") or "")
        if row_id not in prev_rows:
            changes.append({"type": "row_added", "row_id": row_id, "lot_id": lot_id, "column": "", "old": "", "new": lot_id or "행 추가"})
            continue
        old_values = prev_rows[row_id].get("values") if isinstance(prev_rows[row_id].get("values"), dict) else {}
        all_column_ids = list(dict.fromkeys([*prev_columns.keys(), *cur_columns.keys()]))
        for column_id in all_column_ids:
            old_value = str(old_values.get(column_id, "") if old_values.get(column_id) is not None else "")
            new_value = str(values.get(column_id, "") if values.get(column_id) is not None else "")
            if old_value != new_value:
                column = cur_columns.get(column_id) or prev_columns.get(column_id) or {}
                changes.append({"type": "cell_changed", "row_id": row_id, "lot_id": lot_id or str(old_values.get("lot_id") or ""), "column": column.get("label") or column_id, "old": old_value, "new": new_value})
    for row_id, row in prev_rows.items():
        if row_id not in cur_rows:
            values = row.get("values") if isinstance(row.get("values"), dict) else {}
            lot_id = str(values.get("lot_id") or "")
            changes.append({"type": "row_removed", "row_id": row_id, "lot_id": lot_id, "column": "", "old": lot_id or "행", "new": ""})

    prev_colors = previous.get("colors") if isinstance(previous.get("colors"), dict) else {}
    cur_colors = current.get("colors") if isinstance(current.get("colors"), dict) else {}
    for cell_key in sorted(set(prev_colors) | set(cur_colors)):
        old_color, new_color = str(prev_colors.get(cell_key) or "#ffffff"), str(cur_colors.get(cell_key) or "#ffffff")
        if old_color == new_color:
            continue
        row_id, _, column_id = cell_key.partition(":")
        row = cur_rows.get(row_id) or prev_rows.get(row_id) or {}
        values = row.get("values") if isinstance(row.get("values"), dict) else {}
        column = cur_columns.get(column_id) or prev_columns.get(column_id) or {}
        changes.append({"type": "color_changed", "row_id": row_id, "lot_id": str(values.get("lot_id") or ""), "column": column.get("label") or column_id, "old": old_color, "new": new_color})
    return changes


class TableSaveRequest(BaseModel):
    product: str
    columns: list[Any] = Field(default_factory=list)
    rows: list[Any] = Field(default_factory=list)
    colors: dict[str, str] = Field(default_factory=dict)
    expected_version: int | None = None
    note: str = ""


class RollbackRequest(BaseModel):
    product: str
    version: int
    expected_version: int | None = None
    note: str = ""


class StatusBatchRequest(BaseModel):
    product: str
    lot_ids: list[str] = Field(default_factory=list)


@router.get("/table")
def get_table(
    request: Request,
    product: str = Query(...),
    include_status: bool = Query(True),
):
    current_user(request)
    doc = _load(product)
    # The current-WIP cache can be expensive to hydrate on the first request
    # after a process restart.  Lot Management asks for the persisted table
    # first, then fills these read-only fields through /statuses so the whole
    # page does not wait for that cold cache read.
    return _with_latest_cache_fields(doc) if include_status else doc


@router.post("/statuses")
def get_statuses(req: StatusBatchRequest, request: Request):
    current_user(request)
    product = str(req.product or "").strip()
    if not product:
        raise HTTPException(400, "product is required")
    lot_ids: list[str] = []
    seen: set[str] = set()
    for raw_lot_id in req.lot_ids[:MAX_ROWS]:
        lot_id = str(raw_lot_id or "").strip()
        key = lot_id.upper()
        if not key or key in seen:
            continue
        seen.add(key)
        lot_ids.append(lot_id)
    try:
        summaries = _latest_status_by_lot(product, lot_ids)
    except Exception:
        summaries = {}
    statuses = {}
    for lot_id in lot_ids:
        key = lot_id.upper()
        summary = summaries.get(key) or {}
        statuses[key] = {
            "current_step_id": str(summary.get("step_id") or ""),
            "step_desc": str(summary.get("step_desc") or ""),
            "qty": _summary_qty(summary),
        }
    return {"product": product, "statuses": statuses}


@router.get("/lot-status")
def get_lot_status(request: Request, product: str = Query(...), lot_id: str = Query(...)):
    current_user(request)
    clean_lot_id = str(lot_id or "").strip()
    if not clean_lot_id:
        return {"product": product, "lot_id": "", "current_step_id": "", "step_desc": "", "qty": None}
    try:
        summary = _latest_status_by_lot(product, [clean_lot_id]).get(clean_lot_id.upper()) or {}
    except Exception:
        summary = {}
    return {
        "product": product,
        "lot_id": clean_lot_id,
        "current_step_id": str(summary.get("step_id") or ""),
        "step_desc": str(summary.get("step_desc") or ""),
        "qty": _summary_qty(summary),
    }


@router.get("/purposes")
def get_purposes(request: Request, product: str = Query(...), lot_ids: str = Query("")):
    """Return plain LOT_ID/purpose lines for a SplitTable root-lot view."""
    current_user(request)
    wanted = {token.strip().casefold() for token in str(lot_ids or "").split(",") if token.strip()}
    if not wanted:
        return {"product": product, "purposes": []}
    doc = _load(product)
    purposes = []
    for row in doc.get("rows") or []:
        if not isinstance(row, dict):
            continue
        values = row.get("values") if isinstance(row.get("values"), dict) else {}
        lot_id = str(values.get("lot_id") or "").strip()
        purpose = str(values.get("purpose") or "").strip()
        if lot_id and purpose and lot_id.casefold() in wanted:
            purposes.append({"lot_id": lot_id, "purpose": purpose})
    return {"product": product, "version": int(doc.get("version") or 0), "purposes": purposes}


@router.post("/table/save")
def save_table(req: TableSaveRequest, request: Request):
    user = current_user(request)
    product = req.product.strip()
    with _lock(product):
        current = _load(product)
        current_version = int(current.get("version") or 0)
        if req.expected_version is not None and req.expected_version != current_version:
            raise HTTPException(409, f"table changed on server (current version {current_version})")
        columns = _ensure_required_columns(_clean_columns(req.columns))
        column_ids = {c["id"] for c in columns}
        rows = _clean_rows(req.rows, column_ids)
        row_ids = {r["id"] for r in rows}
        next_doc = {
            "product": product,
            "version": current_version + 1,
            "columns": columns,
            "rows": rows,
            "colors": _clean_colors(req.colors, row_ids, column_ids),
            "updated_at": _now(),
            "updated_by": str(user.get("username") or ""),
            "note": str(req.note or "")[:500],
        }
        path, _ = _paths(product)
        save_json(path, next_doc, indent=2)
        _write_snapshot(product, next_doc)

        # 관심랏 알람 발행
        try:
            from core.notify import emit_event
            from core import watchlist as _wl
            changes = _version_diff(current, next_doc)
            changed_lots = set()
            for c in changes:
                lid = str(c.get("lot_id") or "").strip().upper()
                if lid:
                    changed_lots.add(lid)
            actor = str(user.get("username") or "")
            for lid in changed_lots:
                lot_changes = [c for c in changes if str(c.get("lot_id") or "").strip().upper() == lid]
                has_comment = any(c.get("column") == "comment" for c in lot_changes)
                has_purpose = any(c.get("column") == "purpose" for c in lot_changes)

                if has_comment:
                    c_item = next(c for c in lot_changes if c.get("column") == "comment")
                    title = f"[주요랏 코멘트 변경] Lot {lid}"
                    body = f"{actor} 님이 코멘트를 변경했습니다: {c_item.get('new') or '(비움)'}"
                    badge = "코멘트 변경"
                elif has_purpose:
                    p_item = next(c for c in lot_changes if c.get("column") == "purpose")
                    title = f"[주요랏 purpose 변경] Lot {lid}"
                    body = f"{actor} 님이 purpose를 변경했습니다: {p_item.get('new') or '(비움)'}"
                    badge = "용도 변경"
                else:
                    title = f"[주요랏 랏관리 갱신] Lot {lid}"
                    body = f"{actor} 님이 {product} 랏관리 표를 갱신했습니다 (v{next_doc['version']})."
                    badge = "랏관리 갱신"

                for watcher in _wl.get_users_watching_lot(lid):
                    emit_event(
                        "watched_lot_management_updated",
                        actor=actor,
                        target_user=watcher,
                        title=title,
                        body=body,
                        payload={
                            "product": product,
                            "lot_id": lid,
                            "target_tab": "lot_management",
                            "category": "관심랏",
                            "badge": badge,
                            "version": next_doc["version"],
                            "allow_self": True,
                        },
                        allow_self=True,
                    )

                # 기준 step_id 도달/초과 알람 검출
                try:
                    all_lids = [str(r.get("values", {}).get("lot_id") or "").strip() for r in next_doc.get("rows") or []]
                    latest_statuses = _latest_status_by_lot(product, [l for l in all_lids if l])
                except Exception:
                    latest_statuses = {}
                for row in next_doc.get("rows") or []:
                    vals = row.get("values") if isinstance(row.get("values"), dict) else {}
                    lid_row = str(vals.get("lot_id") or "").strip().upper()
                    alert_step = str(vals.get("alert_step_id") or "").strip()
                    curr_step = str(latest_statuses.get(lid_row, {}).get("step_id") or vals.get("current_step_id") or "").strip()
                    if lid_row and alert_step and curr_step and _is_step_reached_or_exceeded(curr_step, alert_step):
                        for watcher in _wl.get_users_watching_lot(lid_row):
                            emit_event(
                                "lot_step_threshold_reached",
                                actor=actor,
                                target_user=watcher,
                                title=f"[기준 Step 도달] Lot {lid_row} ({curr_step} >= {alert_step})",
                                body=f"{product} Lot {lid_row} 가 설정된 기준 Step {alert_step} 에 도달/초과했습니다 (현재: {curr_step}).",
                                payload={
                                    "product": product,
                                    "lot_id": lid_row,
                                    "current_step_id": curr_step,
                                    "alert_step_id": alert_step,
                                    "target_tab": "lot_management",
                                    "category": "랏관리",
                                    "badge": "기준 Step 도달",
                                    "allow_self": True,
                                },
                                allow_self=True,
                            )
        except Exception:
            pass

    audit_record(request, "lot-management:save", f"product={product} version={next_doc['version']}", "lotmanage")
    return {"ok": True, "table": _with_latest_cache_fields(next_doc)}


@router.get("/versions")
def list_versions(request: Request, product: str = Query(...)):
    current_user(request)
    _, version_dir = _paths(product)
    versions = []
    for path in sorted(version_dir.glob("v*.json"), reverse=True)[:VERSION_CAP] if version_dir.is_dir() else []:
        doc = load_json(path, {})
        if not isinstance(doc, dict):
            continue
        versions.append({
            "version": int(doc.get("version") or 0),
            "updated_at": doc.get("updated_at") or "",
            "updated_by": doc.get("updated_by") or "",
            "note": doc.get("note") or "",
            "row_count": len(doc.get("rows") or []),
            "column_count": len(doc.get("columns") or []),
        })
    return {"product": product, "versions": versions}


@router.get("/versions/{version}/diff")
def version_diff(request: Request, version: int, product: str = Query(...)):
    current_user(request)
    if version < 1:
        raise HTTPException(400, "invalid version")
    _, version_dir = _paths(product)
    current = load_json(version_dir / f"v{version:08d}.json", None)
    if not isinstance(current, dict):
        raise HTTPException(404, "version not found")
    previous_path = version_dir / f"v{version - 1:08d}.json"
    previous = load_json(previous_path, None) if version > 1 else _empty(product)
    comparison_available = isinstance(previous, dict)
    if not comparison_available:
        previous = _empty(product)
    changes = _version_diff(previous, current)
    return {
        "product": product,
        "version": version,
        "previous_version": version - 1 if comparison_available and version > 1 else None,
        "comparison_available": comparison_available,
        "change_count": len(changes),
        "changes": changes[:1000],
        "truncated": len(changes) > 1000,
    }


@router.post("/rollback")
def rollback(req: RollbackRequest, request: Request):
    user = current_user(request)
    product = req.product.strip()
    with _lock(product):
        current = _load(product)
        current_version = int(current.get("version") or 0)
        if req.expected_version is not None and req.expected_version != current_version:
            raise HTTPException(409, f"table changed on server (current version {current_version})")
        _, version_dir = _paths(product)
        source = version_dir / f"v{int(req.version):08d}.json"
        snapshot = load_json(source, None)
        if not isinstance(snapshot, dict):
            raise HTTPException(404, "version not found")
        restored = dict(snapshot)
        restored.update({
            "product": product,
            "version": current_version + 1,
            "updated_at": _now(),
            "updated_by": str(user.get("username") or ""),
            "note": str(req.note or f"Rollback to v{req.version}")[:500],
        })
        restored["columns"] = _ensure_required_columns(restored.get("columns"))
        column_ids = {column["id"] for column in restored["columns"]}
        restored["rows"] = _clean_rows(restored.get("rows") or [], column_ids)
        row_ids = {row["id"] for row in restored["rows"]}
        restored["colors"] = _clean_colors(restored.get("colors"), row_ids, column_ids)
        path, _ = _paths(product)
        save_json(path, restored, indent=2)
        _write_snapshot(product, restored)
    audit_record(request, "lot-management:rollback", f"product={product} from={req.version} version={restored['version']}", "lotmanage")
    return {"ok": True, "table": _with_latest_cache_fields(restored), "rolled_back_to": req.version}


@router.get("/my-lots")
def get_my_lots(request: Request):
    """현재 사용자가 등록한 주요랏(관심랏)들의 랏관리 현황 및 실시간 위치/Qty/설명을 통합 반환합니다."""
    me = current_user(request)
    username = me.get("username", "")
    from core import watchlist as _wl
    watched_lots = _wl.get_user_watchlist(username)
    if not watched_lots:
        return {"ok": True, "rows": [], "total": 0, "watched_lots": []}

    watched_set = {str(l).strip().upper() for l in watched_lots if str(l).strip()}
    all_matched_rows = []
    seen_matched_lots = set()

    # 1. 존재하는 모든 제품의 랏관리 테이블 파일 스캔
    if TABLE_DIR.is_dir():
        for path in TABLE_DIR.glob("*.json"):
            prod = path.stem
            try:
                doc = load_json(path, {})
                source_rows = [r for r in (doc.get("rows") or []) if isinstance(r, dict)]
                target_rows = []
                for row in source_rows:
                    vals = row.get("values") if isinstance(row.get("values"), dict) else {}
                    lid = str(vals.get("lot_id") or "").strip().upper()
                    if lid in watched_set:
                        target_rows.append(row)
                        seen_matched_lots.add(lid)
                if target_rows:
                    hydrated = _with_latest_cache_fields({"product": prod, "columns": doc.get("columns"), "rows": target_rows})
                    for r in (hydrated.get("rows") or []):
                        r_copy = dict(r)
                        r_copy["product"] = prod
                        all_matched_rows.append(r_copy)
            except Exception:
                continue

    # 2. 랏관리 테이블에는 아직 없지만 관심 등록된 랏들도 상태 조회하여 포함
    remaining_lots = [l for l in watched_lots if l.upper() not in seen_matched_lots]
    if remaining_lots:
        try:
            summaries = _latest_status_by_lot("", remaining_lots)
        except Exception:
            summaries = {}
        for l in remaining_lots:
            key = l.upper()
            summary = summaries.get(key) or {}
            qty_val = _summary_qty(summary)
            all_matched_rows.append({
                "id": f"my_{key}",
                "product": str(summary.get("product") or "-"),
                "values": {
                    "purpose": "-",
                    "lot_id": l,
                    "current_step_id": str(summary.get("step_id") or "-"),
                    "step_desc": str(summary.get("step_desc") or "-"),
                    "qty": str(qty_val) if qty_val is not None else "-",
                    "comment": "(랏관리 미등록)",
                }
            })

    return {
        "ok": True,
        "rows": all_matched_rows,
        "total": len(all_matched_rows),
        "watched_lots": watched_lots,
    }
