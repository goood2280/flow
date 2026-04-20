"""routers/splittable.py v4.1.0 - multi-prefix transposed view + history + Base features.

v4.1 (2026-04-19, adapter-engineer slice):
  - Module-level DB_BASE removed. All route handlers now call PATHS.db_root /
    PATHS.base_root at request time, so `FABCANVAS_*` env overrides and the
    admin_settings.json `data_roots` block land without a process restart.
  - New endpoint `GET /api/splittable/features` — joins
      `data/Base/features_et_wafer.parquet` (wafer-level ET, 750 rows)
      + `data/Base/features_inline_agg.parquet` (wafer-level INLINE aggregate, 50 rows)
    on (lot_id, wafer_id, product) via ET-left-join (Q005 default — preserves
    wafer coverage, INLINE-side cols are null when an ET wafer has no inline
    data). Returns wide-table metadata + columns + sample rows.
  - New endpoint `GET /api/splittable/uniques` — proxies
      `data/Base/_uniques.json` unchanged, for frontend feature-select
    autocomplete catalog.
"""
import json, datetime, io, csv as csv_mod
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import List
import polars as pl
from core.paths import PATHS
from core.utils import (
    _STR, is_cat, find_lot_wafer_cols, load_json, save_json, safe_id,
    csv_response, csv_writer_bytes,
)

router = APIRouter(prefix="/api/splittable", tags=["splittable"])


def _db_base() -> Path:
    """Resolve DB root at call time so runtime overrides take effect."""
    return PATHS.db_root


def _base_root() -> Path:
    """Resolve Base root at call time (env / admin_settings / default chain)."""
    return PATHS.base_root


PLAN_DIR = PATHS.data_root / "splittable"
PLAN_DIR.mkdir(parents=True, exist_ok=True)
PREFIX_CFG = PLAN_DIR / "prefix_config.json"
DEFAULT_PREFIXES = ["KNOB", "MASK", "INLINE", "VM", "FAB"]
PLAN_ALLOWED_PREFIXES = ["KNOB", "MASK", "FAB"]  # Only these can have plan values
# v8.4.9: 엑셀 메모/태그 저장소 — wafer 단위(tag) + parameter 단위(memo) 공용.
#   scope="wafer": target_key = "{product}__{root_lot_id}__W{wafer_id}"
#   scope="param": target_key = "{product}__{root_lot_id}__W{wafer_id}__{param}"
# 각 항목은 {id, text, username, created_at} 를 보관하고 작성자/관리자만 삭제 가능.
NOTES_FILE = PLAN_DIR / "notes.json"


def _load_prefixes():
    return load_json(PREFIX_CFG, DEFAULT_PREFIXES)


def _cast_cats_lazy(lf):
    """Cast Categorical to Utf8 in a LazyFrame."""
    casts = [pl.col(n).cast(_STR, strict=False) for n, d in lf.schema.items() if is_cat(d)]
    return lf.with_columns(casts) if casts else lf


import re as _re
_NUM_RE = _re.compile(r"(\d+(?:\.\d+)?)")

def _natural_param_key(name: str):
    """v8.4.4 — prefix 뒤 숫자(정수/소수) 기준 자연 정렬 키 생성.
    예: 'KNOB_12.0_ASV_FOO' → ('KNOB', 12.0, '_ASV_FOO')
    숫자가 없으면 ('KNOB', inf, rest) 순으로 후순.
    """
    if not name: return ("", float("inf"), "")
    parts = name.split("_", 1)
    pfx = parts[0]
    rest = parts[1] if len(parts) > 1 else ""
    m = _NUM_RE.search(rest)
    if m:
        try:
            num = float(m.group(1))
        except Exception:
            num = float("inf")
        return (pfx, num, rest)
    return (pfx, float("inf"), rest)


def _color_for_value(val, uniq_map, palette):
    """UI 와 동일하게 unique 값 별 색상 (RGB hex, openpyxl fgColor 포맷 - no #).
    palette 는 색 리스트, uniq_map 은 {value: index}.
    """
    if val is None or val == "":
        return None
    idx = uniq_map.get(val)
    if idx is None: return None
    return palette[idx % len(palette)]


def _detect_lot_wafer(lf, product: str = ""):
    """v8.4.4: product 별 source_config.json 의 lot_overrides 를 우선 참조.
    override 가 실제 schema 에 존재할 때만 사용 (소프트랜딩). 아니면 기본 감지.
    """
    if product:
        try:
            cfg = load_json(SOURCE_CFG, {"lot_overrides": {}}) if SOURCE_CFG.exists() else {}
            ov = (cfg.get("lot_overrides") or {}).get(product) or {}
            schema_names = set(lf.collect_schema().names() if hasattr(lf, "collect_schema") else lf.schema.keys())
            root_col = ov.get("root_col") if ov.get("root_col") in schema_names else None
            wf_col = ov.get("wf_col") if ov.get("wf_col") in schema_names else None
            if root_col or wf_col:
                # Fill missing with auto-detect
                auto_r, auto_w = find_lot_wafer_cols(lf.schema)
                return (root_col or auto_r, wf_col or auto_w)
        except Exception:
            pass
    return find_lot_wafer_cols(lf.schema)


def _product_path(product: str):
    """Find product file. v8.4.3 — Base scope (ML_TABLE_PRODA/B etc.) 우선,
    이후 DB 루트(legacy) 로 폴백. ML 중심 설계로 전환.
    """
    base_root = _base_root()
    db_base = _db_base()
    for root in (base_root, db_base):
        if not root or not root.exists():
            continue
        for ext in (".parquet", ".csv"):
            fp = root / f"{product}{ext}"
            if fp.exists():
                return fp
    raise HTTPException(404, f"Product not found: {product}")


def _select_columns(all_data_cols, custom_name: str, prefix: str, max_fallback: int = 50):
    """Multi-prefix ("KNOB,MASK") or ALL or custom-name based column selection."""
    if custom_name:
        cfp = PLAN_DIR / f"custom_{custom_name}.json"
        data = load_json(cfp, {})
        cols = [c for c in data.get("columns", []) if c in all_data_cols]
        return cols if cols else all_data_cols[:max_fallback]
    if prefix.upper() == "ALL":
        return all_data_cols[: max_fallback * 4]
    pref_list = [p.strip().upper() + "_" for p in prefix.split(",") if p.strip()]
    if pref_list:
        sel = [c for c in all_data_cols if any(c.upper().startswith(p) for p in pref_list)]
        if sel:
            return sel
    return all_data_cols[:max_fallback]


# ── Products / schema ──
@router.get("/products")
def list_products():
    """Split-table source listing. Surfaces BOTH root-level single-files
    (legacy layout) AND Hive-flat source tables (FAB/INLINE/ET/EDS/LOTS)
    + Base-scope wide parquet features so the admin "Visible Sources"
    panel has real rows to toggle. v8.3.x+ ships Hive-flat so the old
    root-iterdir loop returned nothing — this expansion fixes that."""
    products = []
    db_base = _db_base()
    if db_base.exists():
        # (1) Legacy: root-level parquet/csv files (old deployments).
        for f in sorted(db_base.iterdir()):
            if f.is_file() and f.suffix in (".parquet", ".csv"):
                products.append({"name": f.stem, "file": f.name, "size": f.stat().st_size,
                                 "root": "", "type": f.suffix[1:]})
        # (2) Hive-flat: each <root>/<table>/product=<P>/ aggregates to a
        # single logical "source" entry keyed by table name.
        for root_dir in sorted(db_base.iterdir()):
            if not root_dir.is_dir():
                continue
            for table_dir in sorted(root_dir.iterdir()):
                if not table_dir.is_dir():
                    continue
                parts = list(table_dir.glob("product=*"))
                if not parts:
                    # Table with direct file (e.g. LOTS/lots/part-0.csv).
                    files = [p for p in table_dir.glob("*.csv")] + [p for p in table_dir.glob("*.parquet")]
                    if files:
                        products.append({"name": table_dir.name, "file": files[0].name,
                                         "size": sum(p.stat().st_size for p in files),
                                         "root": root_dir.name, "type": files[0].suffix[1:]})
                else:
                    size = 0
                    for part in parts:
                        for f in part.glob("*"):
                            if f.is_file():
                                size += f.stat().st_size
                    products.append({"name": table_dir.name, "file": f"product=*/part-0.*",
                                     "size": size, "root": root_dir.name, "type": "hive"})
    # (3) Base-scope wide-form feature parquets.
    try:
        base = _base_root()
        if base.exists():
            for f in sorted(base.iterdir()):
                if f.is_file() and f.suffix == ".parquet":
                    products.append({"name": f.stem, "file": f.name, "size": f.stat().st_size,
                                     "root": "Base", "type": "parquet"})
    except Exception:
        pass
    return {"products": products}


@router.get("/schema")
def get_schema(product: str = Query(...)):
    fp = _product_path(product)
    if fp.suffix == ".csv":
        lf = pl.scan_csv(str(fp), infer_schema_length=5000)
    else:
        lf = pl.scan_parquet(str(fp))
    return {
        "columns": [{"name": n, "dtype": str(d)} for n, d in lf.schema.items()],
        "total": len(lf.schema),
    }


# ── v4.1 Base-scope feature join (adapter-engineer slice) ─────────────────
_ET_FILE = "features_et_wafer.parquet"
_INLINE_FILE = "features_inline_agg.parquet"
_UNIQUES_FILE = "_uniques.json"
_JOIN_KEYS = ["lot_id", "wafer_id", "product"]


def _read_et_and_inline():
    """Read both wide-feature parquets from Base root (lazy→collect).

    Returns (et_df, inline_df). Raises HTTPException(404) if a file is missing.
    """
    base = _base_root()
    et_fp = base / _ET_FILE
    inl_fp = base / _INLINE_FILE
    missing = [f.name for f in (et_fp, inl_fp) if not f.is_file()]
    if missing:
        raise HTTPException(
            404,
            f"Base feature file(s) not found under {base}: {', '.join(missing)}",
        )
    try:
        et = pl.read_parquet(str(et_fp))
        inl = pl.read_parquet(str(inl_fp))
    except Exception as e:
        raise HTTPException(500, f"Failed to read Base parquet: {e}")
    return et, inl


def _join_features(et: pl.DataFrame, inl: pl.DataFrame) -> pl.DataFrame:
    """ET-left-join INLINE on (lot_id, wafer_id, product).

    Default per Q005 — ET has 750 rows (wafer coverage), INLINE has 50.
    Left join keeps the ET row count and nulls out inline-side columns for
    wafers without INLINE aggregation.
    """
    # Sanity: all join keys must exist on both sides
    keys = [k for k in _JOIN_KEYS if k in et.columns and k in inl.columns]
    if len(keys) < 2:
        raise HTTPException(
            500,
            f"Insufficient common join keys (need subset of {_JOIN_KEYS}, "
            f"found {keys}). ET cols: {et.columns[:5]}… INLINE cols: {inl.columns[:5]}…",
        )
    return et.join(inl, on=keys, how="left")


@router.get("/features", deprecated=True)
def get_features_deprecated(rows: int = Query(50), cols: int = Query(40)):
    """v8.4.3 deprecated — ET+INLINE join 기반 features 는 ML_TABLE_PROD* 로 통합.
    임시로 빈 응답 유지 (기존 프론트 호환). 다음 frontend 릴리즈에서 호출 제거.
    """
    return {
        "join": "deprecated",
        "join_keys": [],
        "total_rows": 0, "total_cols": 0,
        "columns": [], "all_columns": [], "dtypes": {}, "sample": [],
        "deprecated": True,
        "replacement": "Use /api/splittable/view with product=ML_TABLE_PRODA|ML_TABLE_PRODB",
    }


def _get_features_legacy_stub(rows: int = 50, cols: int = 40):
    """Return the wide feature table from ET ⋈ INLINE (ET left join).

    Query params:
      - rows: sample rows to serialize (default 50, max 500)
      - cols: sample columns to serialize (default 40, max 200).
              `all_columns` is always full schema regardless of cols trim.

    Response shape (short):
      {
        "join": "et_left_inline",
        "join_keys": ["lot_id","wafer_id","product"],
        "total_rows": <int>,
        "total_cols": <int>,
        "et_rows":  <int>, "et_cols":  <int>,
        "inline_rows": <int>, "inline_cols": <int>,
        "columns":  [<first `cols` column names>],
        "all_columns": [<full list>],
        "dtypes":   {name: dtype_str, ...},
        "sample":   [ {col: val, ...}, ... ]   # first `rows` rows
      }
    """
    rows = max(1, min(500, int(rows)))
    cols = max(1, min(200, int(cols)))

    et, inl = _read_et_and_inline()
    joined = _join_features(et, inl)

    all_cols = list(joined.columns)
    schema = {n: str(d) for n, d in joined.schema.items()}
    show_cols = all_cols[:cols]
    sample = joined.head(rows).select(show_cols)

    # polars → JSON-safe rows (None passes through)
    data = sample.to_dicts()
    # Cast any non-JSON-friendly scalars to str as a defensive measure
    for r in data:
        for k, v in list(r.items()):
            if v is None or isinstance(v, (int, float, str, bool)):
                continue
            r[k] = str(v)

    return {
        "join": "et_left_inline",
        "join_keys": [k for k in _JOIN_KEYS if k in et.columns and k in inl.columns],
        "total_rows": joined.height,
        "total_cols": len(all_cols),
        "et_rows": et.height,
        "et_cols": et.width,
        "inline_rows": inl.height,
        "inline_cols": inl.width,
        "columns": show_cols,
        "all_columns": all_cols,
        "dtypes": schema,
        "sample": data,
        "base_root": str(_base_root()),
    }


@router.get("/uniques")
def get_uniques():
    """Proxy `data/Base/_uniques.json` verbatim for feature-select catalogs.

    Returns the parsed JSON body + a small meta header. If the file is missing
    we return `{"uniques": {}, "exists": False, ...}` rather than 404 so the
    frontend can display a graceful empty state.
    """
    base = _base_root()
    fp = base / _UNIQUES_FILE
    if not fp.is_file():
        return {
            "exists": False,
            "path": str(fp),
            "uniques": {},
            "size": 0,
        }
    try:
        with open(fp, "r", encoding="utf-8") as f:
            parsed = json.load(f)
    except Exception as e:
        raise HTTPException(500, f"_uniques.json parse error: {e}")
    return {
        "exists": True,
        "path": str(fp),
        "size": fp.stat().st_size,
        "top_keys": list(parsed.keys()) if isinstance(parsed, dict) else None,
        "uniques": parsed,
    }


# ── Source visibility config (admin) ──
SOURCE_CFG = PLAN_DIR / "source_config.json"

@router.get("/source-config")
def get_source_config():
    cfg = load_json(SOURCE_CFG, {"enabled": []})
    cfg.setdefault("enabled", [])
    cfg.setdefault("lot_overrides", {})  # v8.4.4: product-scoped {root_col, fab_col, fab_source, ts_col, join_keys}
    return cfg

class SourceConfigReq(BaseModel):
    enabled: List[str] = []
    lot_overrides: dict = {}  # v8.4.4

@router.post("/source-config/save")
def save_source_config(req: SourceConfigReq):
    cur = load_json(SOURCE_CFG, {"enabled": [], "lot_overrides": {}})
    cur["enabled"] = req.enabled
    if req.lot_overrides:
        cur.setdefault("lot_overrides", {}).update(req.lot_overrides)
    save_json(SOURCE_CFG, cur)
    return {"ok": True}


# ── Prefixes ──
@router.get("/prefixes")
def get_prefixes():
    return {"prefixes": _load_prefixes()}


# ── KNOB metadata (v8.4.7) ───────────────────────────────────────────
# Reverse-lookup helper used by SplitTable UI:
#   knob_ppid.csv:      feature_name, function_step, rule_order, ppid, operator, category, use
#   step_matching.csv:  step_id, func_step
# For each KNOB feature_name (product-scoped), we group the knob_ppid rules in
# rule_order, expand each function_step back to its matching step_ids, and
# produce both a structured `groups` payload and a ready-to-render `label`:
#   GATE_PATTERN (AA200030/AA200040/AA200050) + PC_ETCH (AA200100/AA200110)
# Combine operator for `label` follows knob_ppid.operator (empty = terminator).
def _load_csv_rows(fp: Path) -> list[dict]:
    if not fp.is_file():
        return []
    try:
        with open(fp, "r", encoding="utf-8") as f:
            return list(csv_mod.DictReader(f))
    except Exception:
        return []


def _build_knob_meta(product: str = "") -> dict:
    base = _base_root()
    matching = _load_csv_rows(base / "step_matching.csv")
    knob_rules = _load_csv_rows(base / "knob_ppid.csv")

    # func_step → [step_id, ...] (ordered, dedup)
    step_map: dict[str, list[str]] = {}
    for r in matching:
        # product 컬럼이 있으면 필터, 없으면 공용 매핑으로 취급
        if product and r.get("product") and r.get("product") != product:
            continue
        fs = (r.get("func_step") or "").strip()
        sid = (r.get("step_id") or r.get("raw_step_id") or "").strip()
        if not fs or not sid:
            continue
        lst = step_map.setdefault(fs, [])
        if sid not in lst:
            lst.append(sid)

    # feature_name → groups (sorted by rule_order)
    feats: dict[str, list[dict]] = {}
    for r in knob_rules:
        if (r.get("use") or "Y").strip().upper() == "N":
            continue
        if product and r.get("product") and r.get("product") != product:
            continue
        fname = (r.get("feature_name") or "").strip()
        fstep = (r.get("function_step") or "").strip()
        if not fname or not fstep:
            continue
        try:
            order = int(r.get("rule_order") or 0)
        except Exception:
            order = 0
        feats.setdefault(fname, []).append({
            "func_step": fstep,
            "rule_order": order,
            "ppid": (r.get("ppid") or "").strip(),
            "operator": (r.get("operator") or "").strip(),
            "category": (r.get("category") or "").strip(),
            "step_ids": list(step_map.get(fstep, [])),
        })

    # Sort each feature's groups by rule_order + build a human label
    out: dict[str, dict] = {}
    for fname, groups in feats.items():
        groups.sort(key=lambda g: g["rule_order"])
        parts: list[str] = []
        for i, g in enumerate(groups):
            sids = g["step_ids"]
            if len(sids) == 0:
                seg = g["func_step"]
            elif len(sids) == 1:
                seg = f"{g['func_step']} ({sids[0]})"
            else:
                seg = f"{g['func_step']} ({'/'.join(sids)})"
            parts.append(seg)
            # operator 은 "다음 그룹과의 결합 연산자" — 마지막 그룹은 종결자라 무시
            if i < len(groups) - 1:
                op = (g.get("operator") or "+").strip() or "+"
                parts.append(f" {op} ")
        out[fname] = {
            "groups": groups,
            "label": "".join(parts),
        }
    return out


@router.get("/knob-meta")
def knob_meta(product: str = Query("")):
    """v8.4.7: KNOB feature_name → func_step(step_id) 역산 맵.

    응답 스키마:
      {
        "features": {
          "KNOB_GATE_PPID": {
            "groups": [
              {"func_step":"GATE_PATTERN","step_ids":["AA200030","AA200040","AA200050"],
               "ppid":"PP_GATE_01","operator":"+","rule_order":1,"category":"gate"},
              {"func_step":"PC_ETCH","step_ids":["AA200100","AA200110"],
               "ppid":"PP_PC_01","operator":"","rule_order":2,"category":"gate"}
            ],
            "label": "GATE_PATTERN (AA200030/AA200040/AA200050) + PC_ETCH (AA200100/AA200110)"
          },
          ...
        }
      }
    product 필터는 선택 — Base CSV 에 product 컬럼이 있으면 적용, 없으면 공용 룰로 취급.
    """
    return {"features": _build_knob_meta(product)}


class PrefixSaveReq(BaseModel):
    prefixes: List[str]


@router.post("/prefixes/save")
def save_prefixes(req: PrefixSaveReq):
    save_json(PREFIX_CFG, req.prefixes)
    return {"ok": True}


# ── Cell decimal precision (v8.1.1) ──
# Per-prefix decimal places for numeric cell display. Only INLINE/VM default;
# any prefix key can be added here. Admin-configurable.
PRECISION_CFG = PLAN_DIR / "precision_config.json"
DEFAULT_PRECISION = {"INLINE": 2, "VM": 2}


@router.get("/precision")
def get_precision():
    return {"precision": load_json(PRECISION_CFG, DEFAULT_PRECISION)}


class PrecisionReq(BaseModel):
    precision: dict   # {"INLINE": 2, "VM": 3, ...}


@router.post("/precision/save")
def save_precision(req: PrecisionReq):
    # Sanitize: ensure int 0..10 per prefix
    out = {}
    for k, v in (req.precision or {}).items():
        if not isinstance(k, str) or not k.strip():
            continue
        try:
            n = int(v)
        except Exception:
            continue
        n = max(0, min(10, n))
        out[k.strip().upper()] = n
    save_json(PRECISION_CFG, out)
    return {"ok": True, "precision": out}


# ── Customs ──
@router.get("/customs")
def list_customs():
    customs = []
    for f in sorted(PLAN_DIR.glob("custom_*.json")):
        c = load_json(f, None)
        if c:
            c["_file"] = f.name
            customs.append(c)
    return {"customs": customs}


class CustomSaveReq(BaseModel):
    name: str
    username: str
    columns: List[str]


@router.post("/customs/save")
def save_custom(req: CustomSaveReq):
    fp = PLAN_DIR / f"custom_{safe_id(req.name)}.json"
    now = datetime.datetime.now().isoformat()
    save_json(fp, {
        "name": req.name, "username": req.username, "columns": req.columns,
        "created": now, "updated": now,
    })
    return {"ok": True}


class CustomDeleteReq(BaseModel):
    name: str
    username: str


@router.post("/customs/delete")
def delete_custom(req: CustomDeleteReq):
    fp = PLAN_DIR / f"custom_{safe_id(req.name)}.json"
    if not fp.exists():
        raise HTTPException(404)
    data = load_json(fp, {})
    # Permission: creator or admin
    try:
        from routers.auth import read_users
        is_admin = any(u["username"] == req.username and u.get("role") == "admin"
                       for u in read_users())
        if data.get("username") != req.username and not is_admin:
            raise HTTPException(403, "Only creator or admin can delete")
    except HTTPException:
        raise
    except Exception:
        pass
    fp.unlink(missing_ok=True)
    return {"ok": True}


def _scan_product(product: str):
    fp = _product_path(product)
    if fp.suffix == ".csv":
        return _cast_cats_lazy(pl.scan_csv(str(fp), infer_schema_length=5000))
    return _cast_cats_lazy(pl.scan_parquet(str(fp)))

@router.get("/lot-ids")
def get_lot_ids(product: str = Query(...), limit: int = Query(200)):
    lf = _scan_product(product)
    lot_col, _ = _detect_lot_wafer(lf)
    lots = (
        lf.select(pl.col(lot_col).cast(_STR, strict=False))
        .unique().sort(lot_col).head(limit).collect()
    )
    return {"lot_col": lot_col, "lot_ids": lots[lot_col].to_list()}


@router.get("/lot-candidates")
def get_lot_candidates(
    product: str = Query(...),
    col: str = Query("root_lot_id"),
    prefix: str = Query(""),
    limit: int = Query(30),
):
    """Autocomplete 후보 반환. col 은 'root_lot_id' 또는 'fab_lot_id'. prefix 가
    비어있으면 최신/정렬 상위 N개, 아니면 prefix 포함 매칭을 정렬 순 top N.
    """
    lf = _scan_product(product)
    schema_names = lf.collect_schema().names()
    # 스키마에 col 존재 여부 확인 (하이픈 변형/대소문자 차이 보정)
    if col not in schema_names:
        # fallback — root 이면 auto-detect lot col, fab 는 그대로
        if col == "root_lot_id":
            lot_col, _ = _detect_lot_wafer(lf)
            col = lot_col or col
        if col not in schema_names:
            return {"col": col, "candidates": [], "available_cols": schema_names[:20]}

    q = lf.select(pl.col(col).cast(_STR, strict=False).alias("v")).unique()
    if prefix.strip():
        q = q.filter(pl.col("v").str.contains(prefix.strip(), literal=True))
    rows = q.sort("v").head(limit).collect()
    return {"col": col, "candidates": rows["v"].to_list(), "prefix": prefix}


@router.get("/column-values")
def get_column_values(product: str = Query(...), col: str = Query(...), limit: int = Query(200)):
    """빈셀 dbl-click edit suggestion — col 값의 unique 리스트 (전체 데이터셋 범위) +
    해당 product 의 plan 에 등록된 값 union. null/빈값 제외.
    """
    out: list[str] = []
    seen: set[str] = set()
    try:
        lf = _scan_product(product)
        schema_names = lf.collect_schema().names()
        if col in schema_names:
            rows = (lf.select(pl.col(col).cast(_STR, strict=False).alias("v"))
                    .drop_nulls().unique().sort("v").head(limit).collect())
            for v in rows["v"].to_list():
                if v is None: continue
                s = str(v).strip()
                if not s or s in ("None", "null"): continue
                if s in seen: continue
                seen.add(s); out.append(s)
    except Exception:
        pass
    # Union with plan values stored under this column
    try:
        plans = load_json(PLAN_DIR / f"{product}.json", {}).get("plans", {})
        for ck, pv in plans.items():
            # ck format: root_lot_id|wafer_id|col_name
            parts = str(ck).split("|")
            if len(parts) >= 3 and parts[2] == col:
                v = pv.get("value") if isinstance(pv, dict) else pv
                if v is None: continue
                s = str(v).strip()
                if not s or s in ("None", "null"): continue
                if s in seen: continue
                seen.add(s); out.append(s)
    except Exception:
        pass
    return {"col": col, "values": out, "count": len(out)}


def _filter_lot_wafer(lf, lot_col, wf_col, root_lot_id: str, wafer_ids: str,
                      fab_lot_id: str = "", fab_lot_col: str = "fab_lot_id"):
    """Apply lot + (optional) wafer filter to LazyFrame. v8.4.3 — fab_lot_id
    경로 추가. root_lot_id / fab_lot_id 중 하나로 조회 가능.
    """
    if fab_lot_id and fab_lot_id.strip() and fab_lot_col in lf.collect_schema().names():
        lf = lf.filter(pl.col(fab_lot_col).cast(_STR, strict=False) == fab_lot_id.strip())
    elif root_lot_id.strip():
        lf = lf.filter(pl.col(lot_col).cast(_STR, strict=False) == root_lot_id.strip())
    if wafer_ids.strip() and wf_col:
        wf_list = [w.strip() for w in wafer_ids.split(",") if w.strip()]
        try:
            wf_ints = [int(w) for w in wf_list]
            # Build all possible formats: 1 → ["1", "01", "W01", "W1"]
            wf_strs = set()
            for n in wf_ints:
                wf_strs.update([str(n), f"{n:02d}", f"W{n}", f"W{n:02d}"])
            lf = lf.filter(
                pl.col(wf_col).cast(_STR, strict=False).is_in(list(wf_strs))
                | pl.col(wf_col).cast(pl.Int64, strict=False).is_in(wf_ints)
            )
        except ValueError:
            lf = lf.filter(pl.col(wf_col).cast(_STR, strict=False).is_in(wf_list))
    return lf


# ── View ──
@router.get("/view")
def view_split(product: str = Query(...), root_lot_id: str = Query(""),
               wafer_ids: str = Query(""), prefix: str = Query("KNOB"),
               custom_name: str = Query(""), view_mode: str = Query("all"),
               fab_lot_id: str = Query("")):
    fp = _product_path(product)
    try:
        lf = _scan_product(product)
        lot_col, wf_col = _detect_lot_wafer(lf, product)
        # v8.4.4: fab_lot_col override (기본 "fab_lot_id")
        fab_lot_col = "fab_lot_id"
        try:
            _cfg = load_json(SOURCE_CFG, {}) or {}
            _ov = (_cfg.get("lot_overrides") or {}).get(product) or {}
            _fc = _ov.get("fab_col")
            if _fc and _fc in lf.collect_schema().names():
                fab_lot_col = _fc
        except Exception:
            pass

        if not root_lot_id.strip() and not fab_lot_id.strip():
            return {"product": product, "lot_col": lot_col, "wf_col": wf_col,
                    "headers": [], "rows": [], "prefixes": _load_prefixes(),
                    "msg": "Enter a Root Lot ID or Fab Lot ID to view"}

        lf = _filter_lot_wafer(lf, lot_col, wf_col, root_lot_id, wafer_ids,
                               fab_lot_id=fab_lot_id, fab_lot_col=fab_lot_col)

        df = lf.collect()
        if df.height == 0:
            return {"product": product, "lot_col": lot_col, "wf_col": wf_col,
                    "headers": [], "rows": [], "prefixes": _load_prefixes(), "msg": "No data"}
        if df.height > 500:
            df = df.head(500)

        all_data_cols = [c for c in df.columns if c != lot_col and c != wf_col]
        selected = _select_columns(all_data_cols, custom_name, prefix, max_fallback=50)
        # v8.4.4: natural sort by trailing number (KNOB_12.0_... → 12.0 기준)
        selected = sorted(selected, key=_natural_param_key)

        # Wafer header list + fab_lot_id grouping (v8.4.4)
        fab_col = "fab_lot_id" if "fab_lot_id" in df.columns else None
        if wf_col and wf_col in df.columns:
            # Try numeric wafer IDs first, fall back to string
            wf_raw_int = df[wf_col].cast(pl.Int64, strict=False).to_list()
            non_null = [v for v in wf_raw_int if v is not None]
            if non_null:
                wf_raw = wf_raw_int
            else:
                wf_raw = [str(v) for v in df[wf_col].to_list()]
            # Per-wafer fab_lot_id (first non-null occurrence per wafer)
            wf2fab: dict = {}
            if fab_col:
                fab_vals = [(None if v is None else str(v)) for v in df[fab_col].to_list()]
                for w, f in zip(wf_raw, fab_vals):
                    if w is None: continue
                    if w not in wf2fab and f and f not in ("None", "null"):
                        wf2fab[w] = f
            # Sort: (fab_lot_id, wafer_id) — fab_lot 미정이면 "" 로 후순위
            wf_uniq = [w for w in dict.fromkeys(wf_raw) if w is not None and w != "None" and w != "null"]
            wf_sorted = sorted(wf_uniq, key=lambda w: (wf2fab.get(w, "~"), w))
            headers = [f"#{v}" for v in wf_sorted]
            wf_idx = {v: i for i, v in enumerate(wf_sorted)}
            # Build header_groups: consecutive same-fab_lot segments
            wafer_fab_list = [wf2fab.get(w, "") for w in wf_sorted]
            header_groups = []
            if fab_col:
                cur = None; span = 0
                for f in wafer_fab_list:
                    if f == cur:
                        span += 1
                    else:
                        if span > 0: header_groups.append({"label": cur or "—", "span": span})
                        cur = f; span = 1
                if span > 0: header_groups.append({"label": cur or "—", "span": span})
        else:
            wf_raw = list(range(df.height))
            wf_sorted = list(range(df.height))
            headers = [f"#{i}" for i in wf_sorted]
            wf_idx = {i: i for i in wf_sorted}
            wafer_fab_list = []
            header_groups = []

        # Load plans
        plans = load_json(PLAN_DIR / f"{product}.json", {}).get("plans", {})

        rows = []
        for col_name in selected:
            row_vals = [None] * len(wf_sorted)
            plan_vals = [None] * len(wf_sorted)
            try:
                col_data = df[col_name].to_list()
                for i, val in enumerate(col_data):
                    key = wf_raw[i] if i < len(wf_raw) else None
                    idx = wf_idx.get(key)
                    if idx is not None:
                        row_vals[idx] = val
                        ck = f"{root_lot_id}|{key}|{col_name}"
                        pv = plans.get(ck, {}).get("value")
                        if pv is not None:
                            plan_vals[idx] = pv
            except Exception:
                pass

            # Build _cells dict keyed by column index
            # Check if this column allows plan editing
            col_upper = col_name.upper()
            can_plan = any(col_upper.startswith(p + "_") for p in PLAN_ALLOWED_PREFIXES)
            _cells = {}
            for ci, wf_key in enumerate(wf_sorted):
                actual = row_vals[ci]
                plan = plan_vals[ci]
                actual_str = None if actual is None else str(actual)
                if actual_str in ("None", "null"):
                    actual_str = None
                ck = f"{root_lot_id}|{wf_key}|{col_name}"
                mismatch = False
                if plan and actual_str and str(plan) != actual_str:
                    mismatch = True
                _cells[str(ci)] = {"actual": actual_str, "plan": plan, "key": ck,
                                   "can_plan": can_plan, "mismatch": mismatch}
            rows.append({"_param": col_name, "_cells": _cells})

        if view_mode == "diff":
            rows = [r for r in rows
                    if len(set(c.get("actual") for c in r["_cells"].values()
                               if c.get("actual") is not None)) > 1]

        # Detect mismatches and send notifications to plan owners
        mismatches = []
        for r in rows:
            for ci, cell in r["_cells"].items():
                if cell.get("mismatch"):
                    plan_info = plans.get(cell["key"], {})
                    mismatches.append({
                        "param": r["_param"], "key": cell["key"],
                        "plan": cell["plan"], "actual": cell["actual"],
                        "plan_user": plan_info.get("user", ""),
                    })
        # Send notifications for mismatches (fire-and-forget)
        if mismatches:
            try:
                from core.notify import send_notify
                notified_users = set()
                for mm in mismatches[:20]:  # limit to avoid spam
                    pu = mm.get("plan_user")
                    if pu and pu not in notified_users:
                        send_notify(pu, f"Plan mismatch in {product}: {mm['param']} — plan={mm['plan']}, actual={mm['actual']}")
                        notified_users.add(pu)
            except Exception:
                pass

        return {
            "product": product, "lot_col": lot_col, "wf_col": wf_col,
            "headers": headers, "rows": rows,
            "header_groups": header_groups, "wafer_fab_list": wafer_fab_list,
            "prefixes": _load_prefixes(), "precision": load_json(PRECISION_CFG, DEFAULT_PRECISION), "root_lot_id": root_lot_id,
            "all_columns": all_data_cols, "selected_count": len(selected),
            "prefix": prefix or (custom_name if custom_name else ""),
            "plan_allowed_prefixes": PLAN_ALLOWED_PREFIXES,
            "mismatch_count": len(mismatches),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"View error: {str(e)}")


# ── Plans ──
class PlanReq(BaseModel):
    product: str
    plans: dict
    username: str = "unknown"
    root_lot_id: str = ""


@router.post("/plan")
def save_plan(req: PlanReq):
    # Validate: only KNOB/MASK/FAB columns can have plans
    rejected = []
    for ck in list(req.plans.keys()):
        col_name = ck.split("|")[-1] if "|" in ck else ck
        col_upper = col_name.upper()
        if not any(col_upper.startswith(p + "_") for p in PLAN_ALLOWED_PREFIXES):
            rejected.append(col_name)
            del req.plans[ck]
    if rejected and not req.plans:
        raise HTTPException(400, f"Plan not allowed for: {', '.join(rejected)}. Only {'/'.join(PLAN_ALLOWED_PREFIXES)} columns.")

    pf = PLAN_DIR / f"{req.product}.json"
    data = load_json(pf, {"plans": {}, "history": []})
    data.setdefault("history", [])
    now = datetime.datetime.now().isoformat()
    for ck, val in req.plans.items():
        old = data["plans"].get(ck, {}).get("value")
        data["plans"][ck] = {"value": val, "user": req.username, "updated": now}
        data["history"].append({
            "cell": ck, "old": old, "new": val, "user": req.username,
            "time": now, "action": "set", "root_lot_id": req.root_lot_id,
        })
    data["history"] = data["history"][-1000:]
    save_json(pf, data)
    return {"ok": True, "saved": len(req.plans), "rejected": rejected}


class PlanDeleteReq(BaseModel):
    product: str
    cell_keys: list
    username: str = "unknown"


@router.post("/plan/delete")
def delete_plan(req: PlanDeleteReq):
    pf = PLAN_DIR / f"{req.product}.json"
    if not pf.exists():
        raise HTTPException(404)
    data = load_json(pf, {})
    now = datetime.datetime.now().isoformat()
    for ck in req.cell_keys:
        if ck in data.get("plans", {}):
            old = data["plans"][ck].get("value")
            del data["plans"][ck]
            data.setdefault("history", []).append({
                "cell": ck, "old": old, "new": None,
                "user": req.username, "time": now, "action": "delete",
            })
    save_json(pf, data)
    return {"ok": True}


@router.get("/history")
def get_history(product: str = Query(...), root_lot_id: str = Query(""),
                limit: int = Query(500)):
    pf = PLAN_DIR / f"{product}.json"
    if not pf.exists():
        return {"history": []}
    data = load_json(pf, {})
    hist = data.get("history", [])
    if root_lot_id:
        hist = [h for h in hist
                if h.get("root_lot_id") == root_lot_id
                or h.get("cell", "").startswith(root_lot_id + "|")]
    return {"history": hist[-limit:]}


@router.get("/history-csv")
def download_history_csv(product: str = Query(...)):
    """Admin: download full history as CSV."""
    pf = PLAN_DIR / f"{product}.json"
    if not pf.exists():
        raise HTTPException(404, "No history")
    hist = load_json(pf, {}).get("history", [])
    if not hist:
        raise HTTPException(404, "No history entries")

    header = ["time", "user", "action", "root_lot_id", "wafer_id",
              "column", "old_value", "new_value"]

    def _rows():
        for h in hist:
            parts = h.get("cell", "").split("|")
            lot = parts[0] if len(parts) > 0 else ""
            wf = parts[1] if len(parts) > 1 else ""
            col = parts[2] if len(parts) > 2 else ""
            yield [h.get("time", ""), h.get("user", ""), h.get("action", ""),
                   lot, wf, col, h.get("old", ""), h.get("new", "")]

    return csv_response(csv_writer_bytes(header, _rows()), f"{product}_history.csv")


# ── Transposed CSV ──
@router.get("/download-csv")
def download_csv(product: str = Query(...), root_lot_id: str = Query(""),
                 wafer_ids: str = Query(""), prefix: str = Query("KNOB"),
                 custom_name: str = Query(""), transposed: str = Query("true"),
                 username: str = Query("")):
    fp = _product_path(product)
    lf = _scan_product(product)
    lot_col, wf_col = _detect_lot_wafer(lf)
    lf = _filter_lot_wafer(lf, lot_col, wf_col, root_lot_id, wafer_ids)
    df = lf.collect()

    all_data_cols = [c for c in df.columns if c != lot_col and c != wf_col]
    selected = _select_columns(all_data_cols, custom_name, prefix, max_fallback=200)
    selected = sorted(selected, key=_natural_param_key)

    if transposed.lower() == "true" and wf_col and wf_col in df.columns:
        # Resolve wafer values (handle W01 format)
        wf_raw_int = df[wf_col].cast(pl.Int64, strict=False).to_list()
        non_null = [v for v in wf_raw_int if v is not None]
        if non_null:
            wf_vals = wf_raw_int
        else:
            wf_vals = [str(v) for v in df[wf_col].to_list()]
        # v8.4.4: fab_lot_id 로 1차 정렬, wafer 로 2차 정렬 — UI 그룹 순서와 일치
        fab_col = "fab_lot_id" if "fab_lot_id" in df.columns else None
        wf2fab: dict = {}
        if fab_col:
            fab_vals = [(None if v is None else str(v)) for v in df[fab_col].to_list()]
            for w, f in zip(wf_vals, fab_vals):
                if w is None: continue
                if w not in wf2fab and f and f not in ("None","null"):
                    wf2fab[w] = f
        wf_uniq = [w for w in dict.fromkeys(wf_vals) if w is not None and w != "None" and w != "null"]
        wf_sorted = sorted(wf_uniq, key=lambda w: (wf2fab.get(w, "~"), w))
        headers = [f"#{v}" for v in wf_sorted]
        fab_row = [wf2fab.get(w, "") for w in wf_sorted]
        wf_idx = {v: i for i, v in enumerate(wf_sorted)}

        plans = load_json(PLAN_DIR / f"{product}.json", {}).get("plans", {})

        output = io.StringIO()
        writer = csv_mod.writer(output)
        # Header rows (v8.4.4b): downloaded_at, username, root_lot_id, fab_lot_id, Parameter
        download_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        writer.writerow(["downloaded_at", download_ts])
        writer.writerow(["username", username or ""])
        writer.writerow(["root_lot_id", root_lot_id or ""])
        if fab_col:
            writer.writerow(["fab_lot_id"] + fab_row)
        writer.writerow(["Parameter"] + headers)
        for col_name in selected:
            row_data = [""] * len(wf_sorted)
            vals = df[col_name].to_list()
            for i, v in enumerate(vals):
                wk = wf_vals[i] if i < len(wf_vals) else None
                idx = wf_idx.get(wk)
                if idx is not None:
                    sv = str(v) if v is not None and str(v) not in ("None", "null") else ""
                    ck = f"{root_lot_id}|{wk}|{col_name}"
                    pv = plans.get(ck, {}).get("value")
                    row_data[idx] = pv if pv and not sv else sv
            writer.writerow([col_name] + row_data)
        # v8.4.4: Excel 한글 깨짐 방지 — UTF-8 BOM prefix
        csv_bytes = b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")
    else:
        csv_bytes = b"\xef\xbb\xbf" + df.write_csv().encode("utf-8")

    return csv_response(csv_bytes, f"{product}_{root_lot_id or 'all'}.csv")


@router.get("/download-xlsx")
def download_xlsx(product: str = Query(...), root_lot_id: str = Query(""),
                  wafer_ids: str = Query(""), prefix: str = Query("KNOB"),
                  custom_name: str = Query(""), username: str = Query("")):
    """v8.4.4 — XLSX 내보내기. fab_lot_id 행이 동일 값 구간별로 셀 병합되어
    UI 의 그룹 헤더와 동일하게 표시.
    """
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
        from openpyxl.utils import get_column_letter
    except ImportError as e:
        import sys
        raise HTTPException(500, f"openpyxl not installed at {sys.executable}: {e}")

    lf = _scan_product(product)
    lot_col, wf_col = _detect_lot_wafer(lf, product)
    lf = _filter_lot_wafer(lf, lot_col, wf_col, root_lot_id, wafer_ids)
    df = lf.collect()

    all_data_cols = [c for c in df.columns if c != lot_col and c != wf_col]
    selected = _select_columns(all_data_cols, custom_name, prefix, max_fallback=200)
    # v8.4.4: natural sort — prefix 뒤 숫자 (정수+소수) 기준. 숫자 없으면 알파벳 순.
    selected = sorted(selected, key=_natural_param_key)

    wf_raw_int = df[wf_col].cast(pl.Int64, strict=False).to_list() if wf_col else []
    non_null = [v for v in wf_raw_int if v is not None]
    if non_null:
        wf_vals = wf_raw_int
    else:
        wf_vals = [str(v) for v in df[wf_col].to_list()] if wf_col else []
    fab_col = "fab_lot_id" if "fab_lot_id" in df.columns else None
    wf2fab: dict = {}
    if fab_col:
        fab_vals = [(None if v is None else str(v)) for v in df[fab_col].to_list()]
        for w, f in zip(wf_vals, fab_vals):
            if w is None: continue
            if w not in wf2fab and f and f not in ("None","null"):
                wf2fab[w] = f
    wf_uniq = [w for w in dict.fromkeys(wf_vals) if w is not None and w != "None" and w != "null"]
    wf_sorted = sorted(wf_uniq, key=lambda w: (wf2fab.get(w, "~"), w))
    wf_idx = {v: i for i, v in enumerate(wf_sorted)}

    plans = load_json(PLAN_DIR / f"{product}.json", {}).get("plans", {})

    wb = Workbook()
    ws = wb.active
    ws.title = product[:31]
    hdr_fill = PatternFill("solid", fgColor="1f2937")
    fab_fill = PatternFill("solid", fgColor="f59e0b")
    param_fill = PatternFill("solid", fgColor="374151")
    white = Font(color="FFFFFF", bold=True)
    # v8.4.4b: fab_lot_id 텍스트를 진한 남색/검정 으로 (오렌지 배경에 대비 확보)
    fab_font = Font(color="111827", bold=True, name="Consolas", size=12)
    center = Alignment(horizontal="center", vertical="center")
    thin = Side(style="thin", color="555555")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    download_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    n_wafers = len(wf_sorted)
    last_col = 1 + n_wafers
    # v8.4.4c — downloaded_at / username: 병합하지 않고 label+value 2칸만 표시
    c_ts = ws.cell(row=1, column=1, value="downloaded_at"); c_ts.font = white; c_ts.fill = hdr_fill
    ws.cell(row=1, column=2, value=download_ts)
    # username
    c1 = ws.cell(row=2, column=1, value="username"); c1.font = white; c1.fill = hdr_fill
    ws.cell(row=2, column=2, value=username or "")
    # root_lot_id (v8.4.5c — 병합 복원: wafer 컬럼 전체 colspan)
    c2 = ws.cell(row=3, column=1, value="root_lot_id"); c2.font = white; c2.fill = hdr_fill
    c2v = ws.cell(row=3, column=2, value=root_lot_id or "")
    c2v.alignment = center; c2v.fill = hdr_fill
    c2v.font = Font(color="fbbf24", bold=True, name="Consolas", size=13)
    if n_wafers > 1:
        ws.merge_cells(start_row=3, start_column=2, end_row=3, end_column=last_col)
    # Row 4: fab_lot_id (merged by contiguous groups)
    FAB_ROW = 4
    if fab_col and wf_sorted:
        ws.cell(row=FAB_ROW, column=1, value="fab_lot_id").font = white
        ws.cell(row=FAB_ROW, column=1).fill = hdr_fill
        cur = None; start = 0
        for i, w in enumerate(wf_sorted):
            f = wf2fab.get(w, "")
            if f != cur:
                if cur is not None and i - start > 0:
                    c = ws.cell(row=FAB_ROW, column=2+start, value=cur)
                    c.font = fab_font; c.fill = fab_fill; c.alignment = center; c.border = border
                    if i - start > 1:
                        ws.merge_cells(start_row=FAB_ROW, start_column=2+start, end_row=FAB_ROW, end_column=2+i-1)
                cur = f; start = i
        if cur is not None and len(wf_sorted) - start > 0:
            c = ws.cell(row=FAB_ROW, column=2+start, value=cur)
            c.font = fab_font; c.fill = fab_fill; c.alignment = center; c.border = border
            if len(wf_sorted) - start > 1:
                ws.merge_cells(start_row=FAB_ROW, start_column=2+start,
                               end_row=FAB_ROW, end_column=2+len(wf_sorted)-1)

    # Row 5: Parameter | #1 #2 ...
    param_row = 5 if fab_col else 4
    ws.cell(row=param_row, column=1, value="Parameter").font = white
    ws.cell(row=param_row, column=1).fill = param_fill
    for i, w in enumerate(wf_sorted):
        c = ws.cell(row=param_row, column=2+i, value=f"#{w}")
        c.font = white; c.fill = param_fill; c.alignment = center; c.border = border

    # v8.4.4c: UI 와 동일한 7-색 팔레트 (CELL_COLORS). KNOB_ / MASK_ prefix 행만 컬러링.
    CELL_PALETTE = [
        ("C6EFCE", "006100"),  # green
        ("FFEB9C", "9C5700"),  # yellow
        ("FBE5D6", "BF4E00"),  # orange
        ("BDD7EE", "1F4E79"),  # blue
        ("E2BFEE", "7030A0"),  # purple
        ("B4DED4", "0B5345"),  # teal
        ("F4CCCC", "75194C"),  # pink
    ]
    COLOR_PREFIXES = ("KNOB_", "MASK_")

    for r_off, col_name in enumerate(selected):
        rr = param_row + 1 + r_off
        ws.cell(row=rr, column=1, value=col_name).font = Font(bold=True)
        up = (col_name or "").upper()
        should_color = any(up.startswith(p) for p in COLOR_PREFIXES)
        vals = df[col_name].to_list()
        # Build unique-value map — include plan values in palette assignment
        row_values_ordered = []  # preserve column order for uniq index
        actual_by_idx = {}
        plan_by_idx = {}
        for i, v in enumerate(vals):
            wk = wf_vals[i] if i < len(wf_vals) else None
            idx = wf_idx.get(wk)
            if idx is None: continue
            sv = str(v) if v is not None and str(v) not in ("None","null") else ""
            ck = f"{root_lot_id}|{wk}|{col_name}"
            pv = plans.get(ck, {}).get("value")
            if sv: actual_by_idx[idx] = sv
            if pv: plan_by_idx[idx] = str(pv)
        for idx in sorted(set(list(actual_by_idx.keys()) + list(plan_by_idx.keys()))):
            if idx in actual_by_idx: row_values_ordered.append(actual_by_idx[idx])
            elif idx in plan_by_idx: row_values_ordered.append(plan_by_idx[idx])
        uniq_vals = list(dict.fromkeys(row_values_ordered))
        uniq_map = {v: i for i, v in enumerate(uniq_vals)}

        # v8.4.5b: plan 전용 — 진한 주황 테두리 4면 + 이탤릭
        orange_side = Side(style="medium", color="ea580c")
        plan_border = Border(left=orange_side, right=orange_side,
                             top=orange_side, bottom=orange_side)
        red_side = Side(style="medium", color="ef4444")
        mismatch_border = Border(left=red_side, right=red_side,
                                 top=red_side, bottom=red_side)
        for idx in sorted(set(list(actual_by_idx.keys()) + list(plan_by_idx.keys()))):
            sv = actual_by_idx.get(idx, "")
            pv = plan_by_idx.get(idx, "")
            cell_val = sv or pv
            is_plan_only = (not sv) and bool(pv)
            is_mismatch = bool(sv) and bool(pv) and sv != pv
            cell = ws.cell(row=rr, column=2+idx, value=cell_val)
            cell.alignment = center
            cell.border = border
            if should_color and cell_val in uniq_map:
                bg, fg = CELL_PALETTE[uniq_map[cell_val] % len(CELL_PALETTE)]
                cell.fill = PatternFill("solid", fgColor=bg)
                if is_plan_only:
                    cell.font = Font(color=fg, italic=True, bold=True, size=11, name="Consolas")
                else:
                    cell.font = Font(color=fg, bold=True, size=11, name="Consolas")
            elif is_plan_only:
                cell.fill = PatternFill("solid", fgColor="fef3c7")
                cell.font = Font(color="ea580c", bold=True, italic=True, name="Consolas")
            # Plan-only: 진한 주황 테두리 4면 — 눈에 확 띄도록
            if is_plan_only:
                cell.border = plan_border
                # 📌 prefix 접두로 plan 임을 한 번 더 명시
                if not str(cell_val).startswith("📌 "):
                    cell.value = "📌 " + str(cell_val)
            elif is_mismatch:
                cell.border = mismatch_border

    # Column widths
    ws.column_dimensions["A"].width = 28
    for i in range(len(wf_sorted)):
        ws.column_dimensions[get_column_letter(2+i)].width = 14

    # Freeze panes at param_row+1, B
    ws.freeze_panes = f"B{param_row+1}"

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    from fastapi.responses import StreamingResponse
    fname = f"{product}_{root_lot_id or 'all'}.xlsx"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get("/plans-csv")
def export_plans_csv(product: str = Query(...)):
    pf = PLAN_DIR / f"{product}.json"
    if not pf.exists():
        raise HTTPException(404, "No plans")
    plans = load_json(pf, {}).get("plans", {})
    if not plans:
        raise HTTPException(404, "No plans saved")

    header = ["root_lot_id", "wafer_id", "column", "plan_value", "user", "updated"]

    def _rows():
        for cell_key, info in plans.items():
            parts = cell_key.split("|")
            lot = parts[0] if len(parts) > 0 else ""
            wf = parts[1] if len(parts) > 1 else ""
            col = parts[2] if len(parts) > 2 else cell_key
            yield [lot, wf, col, info.get("value", ""),
                   info.get("user", ""), info.get("updated", "")]

    return csv_response(csv_writer_bytes(header, _rows()), f"{product}_plans.csv")
