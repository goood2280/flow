import time
from pathlib import Path
from typing import Any

from core.paths import PATHS
from core.utils import load_json, safe_id


PLAN_DIR = PATHS.data_root / "splittable"
PASTE_SETS_FILE = PLAN_DIR / "paste_sets.json"
TTL_SECONDS = 300.0
_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_INVALID_CUSTOM_TOKENS = {"undefined", "null"}
_MANAGEMENT_ROW_PREFIX = "MGMT"


def _product_key(product: str) -> str:
    raw = str(product or "").strip()
    if raw.startswith("ML_TABLE_"):
        raw = raw[len("ML_TABLE_"):]
    return raw.casefold()


def _canonical_product(product: str) -> str:
    raw = str(product or "").strip()
    if raw.startswith("ML_TABLE_"):
        return raw
    return f"ML_TABLE_{raw}" if raw else ""


def _stamp(row: dict) -> str:
    return str(row.get("updated") or row.get("updated_at") or row.get("created") or row.get("created_at") or "")


def _clean_custom_token(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    token = value.strip()
    if not token or token.casefold() in _INVALID_CUSTOM_TOKENS:
        return ""
    return token


def _clean_custom_column_name(value: Any) -> str:
    column = _clean_custom_token(value)
    if not column:
        return ""
    if column.upper().startswith(f"{_MANAGEMENT_ROW_PREFIX}_"):
        return ""
    return column


def _clean_custom_columns(columns: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in columns if isinstance(columns, list) else []:
        column = _clean_custom_column_name(raw)
        if not column or column in seen:
            continue
        seen.add(column)
        out.append(column)
    return out


def _custom_file_stem_invalid(path: Path) -> bool:
    stem = path.stem
    raw = stem[len("custom_"):] if stem.startswith("custom_") else stem
    return not _clean_custom_token(raw)


def _sanitize_custom_set(record: Any, path: Path) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    if _custom_file_stem_invalid(path):
        return None
    name = _clean_custom_token(record.get("name"))
    if not name:
        return None
    columns = _clean_custom_columns(record.get("columns") or [])
    if not columns:
        return None
    return {**record, "name": name, "columns": columns}


def _paste_sets_for(product: str) -> list[dict[str, Any]]:
    want = _product_key(product)
    items = load_json(PASTE_SETS_FILE, [])
    if not isinstance(items, list):
        return []
    out = []
    for row in items:
        if not isinstance(row, dict):
            continue
        row_product = str(row.get("product") or "").strip()
        if want and row_product and _product_key(row_product) != want:
            continue
        columns = [str(c) for c in (row.get("columns") or [])]
        rows = row.get("rows") if isinstance(row.get("rows"), list) else []
        out.append({
            "id": str(row.get("id") or f"paste:{safe_id(str(row.get('name') or 'paste'))}"),
            "name": str(row.get("name") or "paste"),
            "product": row_product,
            "source": "paste",
            "columns_count": len(columns),
            "wafer_count": len(rows),
            "updated_at": _stamp(row),
            "owner": str(row.get("username") or row.get("owner") or ""),
            "columns": columns,
            "rows": rows,
        })
    return out


def _custom_sets() -> list[dict[str, Any]]:
    out = []
    for fp in sorted(PLAN_DIR.glob("custom_*.json")):
        row = _sanitize_custom_set(load_json(fp, None), fp)
        if not row:
            continue
        name = str(row.get("name") or "")
        columns = list(row.get("columns") or [])
        out.append({
            "id": f"custom:{safe_id(name)}",
            "name": name,
            "product": "",
            "source": "custom",
            "columns_count": len(columns),
            "wafer_count": 0,
            "updated_at": _stamp(row),
            "owner": str(row.get("username") or row.get("owner") or ""),
            "columns": columns,
            "rows": [],
        })
    return out


def list_sets(product: str = "") -> dict[str, Any]:
    product = _canonical_product(product)
    if not product:
        sets = [*_paste_sets_for(""), *_custom_sets()]
        sets.sort(key=lambda row: (row.get("updated_at") or "", row.get("name") or ""), reverse=True)
        return {"product": "", "sets": sets, "cached": False}

    key = _product_key(product)
    now = time.time()
    cached = _CACHE.get(key)
    if cached and now - cached[0] < TTL_SECONDS:
        return {"product": product, "sets": [dict(x) for x in cached[1]], "cached": True}

    sets = [*_paste_sets_for(product), *_custom_sets()]
    sets.sort(key=lambda row: (row.get("updated_at") or "", row.get("name") or ""), reverse=True)
    _CACHE[key] = (now, [dict(x) for x in sets])
    return {"product": product, "sets": sets, "cached": False}


def invalidate(product: str = "") -> None:
    if not product:
        _CACHE.clear()
        return
    _CACHE.pop(_product_key(product), None)
