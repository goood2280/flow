"""Inline item_desc 별칭 + 값 조회 + Radius plot 엔진.

- 별칭 테이블은 flow-data 정본(`data_root/semantic/inline_desc_aliases.json`)에
  저장한다. setup.py 는 data//flow-data 를 건드리지 않으므로 재설치에 유지된다.
- "PC BCD" 같은 별칭 → (step_id, item_id) 후보 → 해당 lot 의 실측 행으로
  1개 확정 → 1.RAWDATA_DB_INLINE 조회 → wafer별 avg / Radius scatter.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

from core.paths import PATHS
from core.file_transaction import file_transaction
from core.utils import save_json

logger = logging.getLogger("flow.inline_alias")

STORE_NAME = "inline_desc_aliases.json"
MAX_ALIASES_PER_DESC = 50

# SITE 필터 (Inline DB 안의 shot 외 SUM/RANGE 행 제외용).
# 열 이름은 나중에 확정되면 env 로 교체한다.
def site_column() -> str:
    return str(os.environ.get("FLOW_INLINE_SITE_COLUMN") or "SITE").strip() or "SITE"


def site_value() -> str:
    return str(os.environ.get("FLOW_INLINE_SITE_VALUE") or "SITE").strip() or "SITE"


def store_path() -> Path:
    return PATHS.data_root / "semantic" / STORE_NAME


def _norm(value: Any) -> str:
    return re.sub(r"[\s_-]+", "", str(value or "")).upper()


def _load() -> dict:
    path = store_path()
    if not path.is_file():
        return {"version": 1, "aliases": []}
    try:
        import json
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict) and isinstance(value.get("aliases"), list):
            return value
    except (OSError, ValueError, UnicodeError):
        logger.debug("desc alias load failed: %s", path, exc_info=True)
    return {"version": 1, "aliases": []}


def _save(doc: dict) -> dict:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_transaction(path):
        save_json(path, doc)
    try:
        from core import product_semantics
        product_semantics.export_aliases_backup()
    except Exception:
        logger.debug("alias backup export failed", exc_info=True)
    return doc


def list_desc_aliases(product: str = "") -> list[dict[str, Any]]:
    key = str(product or "").casefold()
    rows = _load().get("aliases") or []
    if key:
        rows = [r for r in rows if isinstance(r, dict) and str(r.get("product") or "").casefold() == key]
    return [dict(r) for r in rows if isinstance(r, dict)]


def save_desc_alias(product: str, desc: str, step_id: str, item_id: str, actor: str = "") -> dict[str, Any]:
    """별칭(desc) → (step_id, item_id) 연결 1건 등록. 같은 desc 의 복수 연결 허용."""
    product = str(product or "").strip()
    desc = str(desc or "").strip()
    step_id = str(step_id or "").strip()
    item_id = str(item_id or "").strip()
    if not product or not desc or not step_id or not item_id:
        raise ValueError("제품·별칭·step_id·item_id 를 모두 입력하세요.")
    if max(len(product), len(desc), len(step_id), len(item_id)) > 200:
        raise ValueError("각 값은 최대 200자입니다.")
    try:
        from core import data_product_catalog
        names = data_product_catalog.product_names(PATHS.db_root)
        if names and not any(n.casefold() == product.casefold() for n in names):
            raise ValueError("실제 DB에 등록된 제품을 선택하세요.")
    except ValueError:
        raise
    except Exception:
        pass
    doc = _load()
    rows = [r for r in (doc.get("aliases") or []) if isinstance(r, dict)]
    key = (_norm(product), _norm(desc), step_id.strip(), item_id.strip())
    rows = [r for r in rows
            if (_norm(r.get("product")), _norm(r.get("desc")), str(r.get("step_id") or "").strip(), str(r.get("item_id") or "").strip()) != key]
    same_desc = [r for r in rows if _norm(r.get("product")) == key[0] and _norm(r.get("desc")) == key[1]]
    if len(same_desc) >= MAX_ALIASES_PER_DESC:
        raise ValueError("하나의 별칭에는 최대 50개 연결까지 가능합니다.")
    from core import product_wiki as wiki
    entry = {"product": wiki.product_name(product), "desc": desc,
             "step_id": step_id, "item_id": item_id,
             "by": str(actor or ""), "at": wiki.now()}
    rows.append(entry)
    doc["aliases"] = rows
    try:
        doc["updated_at"] = wiki.now()
    except Exception:
        pass
    _save(doc)
    return entry


def delete_desc_alias(product: str, desc: str, step_id: str = "", item_id: str = "") -> int:
    doc = _load()
    rows = [r for r in (doc.get("aliases") or []) if isinstance(r, dict)]
    before = len(rows)
    step_id, item_id = str(step_id or "").strip(), str(item_id or "").strip()
    rows = [r for r in rows if not (
        _norm(r.get("product")) == _norm(product) and _norm(r.get("desc")) == _norm(desc)
        and (not step_id or str(r.get("step_id") or "").strip() == step_id)
        and (not item_id or str(r.get("item_id") or "").strip() == item_id))]
    if len(rows) != before:
        doc["aliases"] = rows
        _save(doc)
    return before - len(rows)


def _mentioned(needle: str, text: str) -> bool:
    try:
        from core.product_semantics import _mentioned as _sem_mentioned
        return bool(_sem_mentioned(needle, text))
    except Exception:
        n, t = _norm(needle), _norm(text)
        return bool(n) and n in t


def resolve_candidates(product: str, text: str) -> list[dict[str, Any]]:
    """별칭 언급 + Inline_matching item_desc 언급 → (step_id, item_id) 후보."""
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in list_desc_aliases(product):
        if _mentioned(str(row.get("desc") or ""), text):
            key = (str(row.get("step_id") or ""), str(row.get("item_id") or ""))
            if key[0] and key not in seen:
                seen.add(key)
                out.append({"step_id": key[0], "item_id": key[1], "desc": str(row.get("desc") or ""),
                            "item_desc": "", "via": "desc_alias"})
    try:
        from core import product_semantics
        for row in product_semantics.load_inline_matching_rows(product):
            if not isinstance(row, dict):
                continue
            step_id = str(row.get("step_id") or "")
            item_id = str(row.get("item_id") or "")
            if not step_id or (step_id, item_id) in seen:
                continue
            if _mentioned(str(row.get("item_desc") or ""), text) or _mentioned(item_id, text):
                seen.add((step_id, item_id))
                out.append({"step_id": step_id, "item_id": item_id,
                            "desc": str(row.get("item_desc") or ""),
                            "item_desc": str(row.get("item_desc") or ""),
                            "via": "inline_matching"})
    except Exception:
        logger.debug("inline matching resolve failed", exc_info=True)
    return out


def inline_product_dir(product: str) -> Path | None:
    """1.RAWDATA_DB_INLINE/<product> 디렉토리 (대소문자 무시 탐색)."""
    try:
        base = PATHS.db_root / "1.RAWDATA_DB_INLINE"
    except Exception:
        return None
    if not base.is_dir():
        return None
    want = str(product or "").strip()
    for child in sorted(base.iterdir()):
        if child.is_dir() and child.name.casefold() == want.casefold():
            return child
    return None


def scan_inline(product: str):
    """RAW INLINE parquet LazyFrame (원본 컬럼 유지: shot_x/y 포함)."""
    import polars as pl
    directory = inline_product_dir(product)
    if directory is None:
        return None
    files = sorted(directory.glob("**/*.parquet"))
    if not files:
        return None
    try:
        return pl.scan_parquet(list(files), hive_partitioning=True)
    except Exception:
        logger.debug("inline scan failed: %s", directory, exc_info=True)
        return None


def _upper(frame, column: str):
    import polars as pl
    return pl.col(column).cast(pl.String, strict=False).fill_null("").str.to_uppercase()


_ROOT_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9]{3,})(?![A-Za-z0-9_])")


def find_root_in_text(product: str, text: str) -> str:
    """DB 실측 root_lot_id 와 대조해 텍스트 속 root lot 을 찾는다.

    점 없는 bare root(A1021 등)까지 잡기 위한 확정 매칭 — DB에 없는 토큰은
    절대 lot 으로 취급하지 않는다.
    """
    frame = scan_inline(product)
    if frame is None:
        return ""
    try:
        cols = set(frame.collect_schema().names())
        if "root_lot_id" not in cols:
            return ""
        import polars as pl
        roots = {str(v or "").upper() for v in
                 frame.select("root_lot_id").unique().collect()["root_lot_id"].to_list()}
        roots.discard("")
        tokens = {m.group(1).upper() for m in _ROOT_TOKEN_RE.finditer(str(text or ""))}
        for token in sorted(tokens, key=len, reverse=True):
            if token and token in roots:
                return token
    except Exception:
        logger.debug("root lot match failed", exc_info=True)
    return ""


def disambiguate_by_lot(product: str, root_lot: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """후보 (step,item) 중 해당 lot 실측 행이 있는 것만 남긴다 (건수 첨부)."""
    frame = scan_inline(product)
    if frame is None:
        return [dict(c, lot_rows=0) for c in candidates]
    try:
        import polars as pl
        cols = set(frame.collect_schema().names())
        if "root_lot_id" not in cols:
            return [dict(c, lot_rows=0) for c in candidates]
        lot = frame.filter(_upper(frame, "root_lot_id") == str(root_lot or "").upper())
        out = []
        for cand in candidates:
            filt = lot
            if "step_id" in cols:
                filt = filt.filter(_upper(filt, "step_id") == str(cand.get("step_id") or "").upper())
            if "item_id" in cols:
                filt = filt.filter(_upper(filt, "item_id") == str(cand.get("item_id") or "").upper())
            try:
                count = filt.select(pl.len()).collect().item()
            except Exception:
                count = 0
            out.append({**cand, "lot_rows": int(count)})
        return out
    except Exception:
        logger.debug("lot disambiguation failed", exc_info=True)
        return [dict(c, lot_rows=0) for c in candidates]


def apply_site_filter(frame, *, warnings: list[str] | None = None):
    """SITE 열이 있으면 SITE 값만, 없으면 전체 + 경고 플래그."""
    column, value = site_column(), site_value()
    try:
        cols = set(frame.collect_schema().names())
    except Exception:
        return frame, False
    match = next((c for c in cols if c.strip().casefold() == column.casefold()), "")
    if not match:
        if warnings is not None:
            warnings.append(f"SITE 열({column})이 없어 전체 subitem을 사용합니다.")
        return frame, False
    import polars as pl
    return frame.filter(pl.col(match).cast(pl.String, strict=False).str.to_uppercase() == value.upper()), True


def query_values(product: str, root_lot: str, step_id: str, item_id: str,
                 wafer_id: str = "", max_rows: int = 5000) -> dict[str, Any]:
    """INLINE 실측 행 + wafer별 avg. SITE 필터 적용."""
    import polars as pl
    frame = scan_inline(product)
    warnings: list[str] = []
    if frame is None:
        return {"ok": False, "error": f"{product} INLINE DB를 찾지 못했습니다.", "warnings": warnings}
    cols = set(frame.collect_schema().names())
    filt = frame.filter(_upper(frame, "root_lot_id") == str(root_lot or "").upper()) if "root_lot_id" in cols else frame
    if wafer_id and "wafer_id" in cols:
        filt = filt.filter(_upper(filt, "wafer_id") == str(wafer_id).upper())
    if step_id and "step_id" in cols:
        filt = filt.filter(_upper(filt, "step_id") == str(step_id).upper())
    if item_id and "item_id" in cols:
        filt = filt.filter(_upper(filt, "item_id") == str(item_id).upper())
    filt, site_ok = apply_site_filter(filt, warnings=warnings)
    value_col = "value" if "value" in cols else ""
    try:
        selected = filt.collect()
    except Exception as exc:
        return {"ok": False, "error": f"INLINE 조회 실패: {exc}", "warnings": warnings}
    rows: list[dict[str, Any]] = []
    for row in selected.to_dicts():
        value = None
        if value_col:
            try:
                number = float(row.get(value_col)) if row.get(value_col) not in (None, "") else None
                value = number if number is not None and number == number else None
            except (TypeError, ValueError):
                value = None
        rows.append({
            "root_lot_id": str(row.get("root_lot_id") or ""),
            "wafer_id": str(row.get("wafer_id") or ""),
            "lot_id": str(row.get("lot_id") or ""),
            "step_id": str(row.get("step_id") or ""),
            "item_id": str(row.get("item_id") or ""),
            "subitem_id": str(row.get("subitem_id") or ""),
            "shot_x": row.get("shot_x"), "shot_y": row.get("shot_y"),
            "fab_value": value,
            "tkout_time": str(row.get("tkout_time") or ""),
        })
    rows = [r for r in rows if r["fab_value"] is not None]
    by_wafer: dict[str, list[float]] = {}
    for row in rows:
        by_wafer.setdefault(row["wafer_id"] or "-", []).append(row["fab_value"])
    avg_rows = [{"wafer_id": wafer, "n": len(values),
                 "avg": round(sum(values) / len(values), 6)} for wafer, values in sorted(by_wafer.items())]
    if len(selected) >= max_rows:
        warnings.append(f"최대 {max_rows}행까지만 집계했습니다.")
    return {"ok": True, "rows": rows[:max_rows], "total": len(rows),
            "avg_rows": avg_rows, "site_filtered": site_ok, "warnings": warnings,
            "filters": {"product": product, "root_lot_id": root_lot, "wafer_id": wafer_id,
                        "step_id": step_id, "item_id": item_id,
                        "site": f"{site_column()}={site_value()}" if site_ok else ""}}


def available_maps(product: str, step_id: str = "", item_id: str = "") -> dict[str, Any]:
    """(product,step,item)에 쓸 수 있는 map 후보: 지정 매칭 우선 + 전체 테이블."""
    suggested: list[str] = []
    try:
        from core import teg_map
        for row in (teg_map.load_inline_shot_matching().get("rows") or []):
            if not isinstance(row, dict):
                continue
            if str(row.get("product") or "").casefold() != str(product or "").casefold():
                continue
            if step_id and str(row.get("step_id") or "").upper() != str(step_id).upper():
                continue
            if item_id and str(row.get("item_id") or "").upper() != str(item_id).upper():
                continue
            name = str(row.get("map_name") or "").strip()
            if name and name not in suggested:
                suggested.append(name)
    except Exception:
        logger.debug("shot matching load failed", exc_info=True)
    tables: list[dict[str, Any]] = []
    try:
        from core import teg_map
        for table in (teg_map.load_inline_map_settings().get("tables") or []):
            if isinstance(table, dict) and table.get("table_name"):
                tables.append({"table_name": table["table_name"], "vehicle": table.get("vehicle") or "",
                               "shots": len(table.get("shots") or [])})
    except Exception:
        logger.debug("map settings load failed", exc_info=True)
    return {"suggested": suggested, "tables": tables}


def load_map_shots(map_name: str) -> dict[str, Any]:
    """map_name → {subitem_norm: (shot_x, shot_y), vehicle}."""
    try:
        from core import teg_map
        for table in (teg_map.load_inline_map_settings().get("tables") or []):
            if isinstance(table, dict) and str(table.get("table_name") or "") == str(map_name or ""):
                shots = {}
                for shot in table.get("shots") or []:
                    key = _norm(shot.get("subitem_id"))
                    if key and key not in shots:
                        try:
                            shots[key] = (float(shot.get("shot_x")), float(shot.get("shot_y")))
                        except (TypeError, ValueError):
                            continue
                return {"vehicle": str(table.get("vehicle") or ""), "shots": shots}
    except Exception:
        logger.debug("map shots load failed", exc_info=True)
    return {"vehicle": "", "shots": {}}


def load_chip_radius(vehicle: str = "", product: str = "") -> dict[tuple[float, float], float]:
    """Chip_Radius.csv → {(x, y): radius 중앙값}. Mask≒vehicle 매칭."""
    from core import matching_store
    rows, _path = matching_store.read_csv_rows("Chip_Radius.csv")
    if not rows:
        return {}
    lower = [{str(k).strip().lower(): v for k, v in r.items()} for r in rows]
    def col(*names: str) -> str:
        for name in names:
            for key in (lower[0] if lower else {}):
                if key == name:
                    return key
        return ""
    mask_col = col("mask", "vehicle")
    x_col = col("chip_x_adj", "chip_x", "shot_x")
    y_col = col("chip_y_adj", "chip_y", "shot_y")
    r_col = col("chip_radius", "radius")
    if not (x_col and y_col and r_col):
        return {}
    wants = {str(vehicle or "").upper(), str(product or "").upper(),
             re.sub(r"^VH_", "", str(vehicle or "").upper()), re.sub(r"^VH_", "", str(product or "").upper())}
    wants.discard("")
    buckets: dict[tuple[float, float], list[float]] = {}
    for row in lower:
        if mask_col and wants and str(row.get(mask_col) or "").upper() not in wants:
            continue
        try:
            key = (round(float(row.get(x_col)), 6), round(float(row.get(y_col)), 6))
            radius = float(row.get(r_col))
        except (TypeError, ValueError):
            continue
        if radius == radius:
            buckets.setdefault(key, []).append(radius)
    out = {}
    for key, values in buckets.items():
        values.sort()
        out[key] = values[len(values) // 2]
    return out


def radius_panels(product: str, root_lot: str, step_id: str, item_id: str,
                  map_name: str, wafer_id: str = "") -> dict[str, Any]:
    """wafer별 scatter 패널: x=Radius, y=fab_value. cubic fit은 프론트가 계산."""
    result = query_values(product, root_lot, step_id, item_id, wafer_id=wafer_id, max_rows=20000)
    if not result.get("ok"):
        return result
    mapping = load_map_shots(map_name)
    if not mapping["shots"]:
        return {"ok": False, "error": f"map '{map_name}' 에 shot 정보가 없습니다.",
                "warnings": result.get("warnings", [])}
    radii = load_chip_radius(vehicle=mapping.get("vehicle"), product=product)
    if not radii:
        return {"ok": False, "error": "Chip_Radius 에서 반경을 찾지 못했습니다 (Mask/vehicle 확인).",
                "warnings": result.get("warnings", [])}
    by_wafer: dict[str, list[dict[str, Any]]] = {}
    unmatched = 0
    for row in result["rows"]:
        key = _norm(row.get("subitem_id"))
        shot = mapping["shots"].get(key)
        if shot is None:
            unmatched += 1
            continue
        radius = radii.get((round(shot[0], 6), round(shot[1], 6)))
        if radius is None:
            unmatched += 1
            continue
        by_wafer.setdefault(row["wafer_id"] or "-", []).append({
            "x": radius, "y": row["fab_value"], "shot": f"{shot[0]:g},{shot[1]:g}",
            "subitem_id": row.get("subitem_id") or "", "wafer_id": row["wafer_id"],
            "radius_shot": radius, "source_shot": f"{shot[0]:g},{shot[1]:g}",
        })
    panels = []
    for wafer in sorted(by_wafer):
        points = sorted(by_wafer[wafer], key=lambda p: p["x"])
        panels.append({
            "wafer_id": wafer, "n": len(points),
            "title": f"wafer {wafer} — Radius vs fab_value (n={len(points)})",
            "chart": {"kind": "scatter", "chart_type": "scatter", "cubic_fit": True,
                      "title": f"wafer {wafer} — Radius vs fab_value",
                      "x_label": "Radius", "y_label": "fab_value", "points": points},
        })
    warnings = list(result.get("warnings", []))
    if unmatched:
        warnings.append(f"map/radius 미매칭 {unmatched}행 제외")
    return {"ok": True, "panels": panels, "total_points": sum(p["n"] for p in panels),
            "map": map_name, "warnings": warnings, "filters": result.get("filters", {})}
