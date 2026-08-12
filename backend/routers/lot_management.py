"""Product-scoped LOT management tables with snapshot version history."""
from __future__ import annotations

import datetime as dt
import hashlib
import threading
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from core.audit import record as audit_record
from core.auth import current_user
from core.paths import PATHS
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
    from core.lot_progress_cache import lot_progress_summaries

    summaries = lot_progress_summaries(lot_ids, product=product, refresh_if_missing=False)
    return _attach_step_descriptions(summaries, product)


def _attach_step_descriptions(
    summaries: dict[str, dict],
    product: str,
    *,
    index: tuple[dict, dict] | None = None,
) -> dict[str, dict]:
    """Attach exact Vehicle_matching step_desc values; unmatched steps stay blank."""
    from core.lot_wip import describe_step, step_desc_index

    step_index = index if index is not None else step_desc_index()
    out = {}
    for lot_id, raw_summary in (summaries or {}).items():
        summary = dict(raw_summary or {})
        step_id = str(summary.get("step_id") or "").strip()
        summary["step_desc"] = describe_step(step_id, product, index=step_index).get("step_desc") or ""
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
        values["qty"] = str(int(summary.get("wafer_count") or 0)) if lot_id else ""
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


@router.get("/table")
def get_table(request: Request, product: str = Query(...)):
    current_user(request)
    return _with_latest_cache_fields(_load(product))


@router.get("/lot-status")
def get_lot_status(request: Request, product: str = Query(...), lot_id: str = Query(...)):
    current_user(request)
    clean_lot_id = str(lot_id or "").strip()
    if not clean_lot_id:
        return {"product": product, "lot_id": "", "current_step_id": "", "step_desc": "", "qty": 0}
    try:
        summary = _latest_status_by_lot(product, [clean_lot_id]).get(clean_lot_id.upper()) or {}
    except Exception:
        summary = {}
    return {
        "product": product,
        "lot_id": clean_lot_id,
        "current_step_id": str(summary.get("step_id") or ""),
        "step_desc": str(summary.get("step_desc") or ""),
        "qty": int(summary.get("wafer_count") or 0),
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
