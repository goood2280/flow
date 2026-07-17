"""routers/reformatize.py — 업무 탭 "ET Index 다운로드": DB ET → vehicle reformatter index 추출.

auto report 의 reformatize 흐름을 flow 화면으로 제공한다.
제품(DB ET 폴더)을 고르면 `data_root/reformatter/<vehicle>_reformatter.csv`
규칙으로 shot 단위 index 값을 계산해 페이지 단위로 반환/다운로드한다.

Endpoints:
  GET  /api/reformatize/products      — DB ET 제품 목록 + 매칭된 vehicle CSV
  GET  /api/reformatize/settings      — 페이지 행 수 등 설정 조회
  POST /api/reformatize/settings      — 설정 저장 (톱니바퀴)
  POST /api/reformatize/run           — index 계산 후 offset/limit 페이지 반환
  GET  /api/reformatize/download      — 전체 결과 CSV 다운로드 (downloads.jsonl 기록)
  GET  /api/reformatize/formula-help  — (admin) 수식 함수/참조 컬럼 도움말
  POST /api/reformatize/test          — (admin) 새 ADDP 수식 테스트 미리보기
  POST /api/reformatize/test/download — (admin) 테스트 결과 CSV (downloads.jsonl 기록)
"""
from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path

import polars as pl
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app_v2.shared.source_adapter import resolve_named_child
from core.auth import current_user, require_admin
from core.paths import PATHS
from core.utils import (
    csv_response, jsonl_append, load_json, read_source, safe_filename,
    save_json, serialize_rows,
)
from core.vehicle_reformatter import (
    FORMULA_HELP, PIVOT_KEY_COLS, PIVOT_META_COLS, apply_addp_rows,
    find_vehicle_csv, load_vehicle_table, reformatize,
)

logger = logging.getLogger("flow.reformatize")
router = APIRouter(prefix="/api/reformatize", tags=["reformatize"])

VEHICLE_DIR = PATHS.data_root / "reformatter"
SETTINGS_FILE = PATHS.data_root / "reformatize_settings.json"
DL_LOG = PATHS.download_log
ET_ROOT_NAME = "ET"

DEFAULT_SETTINGS = {"page_rows": 500, "max_download_rows": 100_000}
PAGE_ROWS_MAX = 5_000
DOWNLOAD_ROWS_MAX = 1_000_000

# 제품별 pivot+index 결과 캐시 — (product, 파일 시그니처) 가 같으면 재사용.
# 값: (sig, wide_full_df, out_cols, rule_errors, vehicle_csv_name, vehicle_table)
# wide_full 은 raw item 컬럼을 포함 — 관리자 수식 테스트가 참조.
_CACHE: dict[str, tuple] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 4


def _settings() -> dict:
    raw = load_json(SETTINGS_FILE, DEFAULT_SETTINGS) or {}
    out = dict(DEFAULT_SETTINGS)
    try:
        out["page_rows"] = max(10, min(int(raw.get("page_rows", out["page_rows"])), PAGE_ROWS_MAX))
    except Exception:
        pass
    try:
        out["max_download_rows"] = max(100, min(int(raw.get("max_download_rows", out["max_download_rows"])), DOWNLOAD_ROWS_MAX))
    except Exception:
        pass
    return out


def _et_root() -> Path:
    rp = resolve_named_child(PATHS.db_root, ET_ROOT_NAME)
    if rp is None or not rp.is_dir():
        raise HTTPException(404, "DB ET 루트를 찾을 수 없습니다")
    return rp


def _product_sig(product: str) -> tuple:
    """제품 폴더 내 데이터 파일들의 (path, mtime, size) 시그니처."""
    rp = _et_root()
    pd = rp / product
    if not pd.is_dir():
        hive = list(rp.rglob(f"product={product}"))
        if not hive:
            raise HTTPException(404, f"ET 제품 없음: {product}")
        pd = hive[0]
    sig = []
    for fp in sorted(pd.rglob("*")):
        if fp.is_file() and fp.suffix.lower() in (".parquet", ".csv"):
            st = fp.stat()
            sig.append((str(fp), st.st_mtime, st.st_size))
    return tuple(sig)


def _compute(product: str) -> tuple[pl.DataFrame, list[str], list[str], str, list[dict]]:
    """제품의 reformatize 결과 (full wide, 출력 컬럼, 규칙 에러, csv 이름, 규칙 테이블). 캐시 사용."""
    csv_fp = find_vehicle_csv(VEHICLE_DIR, product)
    if csv_fp is None:
        raise HTTPException(400, f"'{product}' 에 매칭되는 vehicle reformatter CSV 가 없습니다 "
                                 f"({VEHICLE_DIR} 안에 <vehicle>_reformatter.csv 를 두세요)")
    st = csv_fp.stat()
    sig = _product_sig(product) + ((str(csv_fp), st.st_mtime, st.st_size),)
    with _CACHE_LOCK:
        hit = _CACHE.get(product)
        if hit and hit[0] == sig:
            return hit[1], hit[2], hit[3], hit[4], hit[5]
    table = load_vehicle_table(csv_fp)
    if not table:
        raise HTTPException(400, f"{csv_fp.name}: 유효한 REAL/ADDP 행이 없습니다")
    df = read_source("flat", _et_root().name, product, "", max_files=None)
    if df.height == 0:
        raise HTTPException(400, f"'{product}' ET 데이터가 비어 있습니다")
    try:
        wide, out_cols, errors = reformatize(df, table)
    except ValueError as e:
        raise HTTPException(400, str(e))
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)), None)
        _CACHE[product] = (sig, wide, out_cols, errors, csv_fp.name, table)
    return wide, out_cols, errors, csv_fp.name, table


def _apply_lot_filter(df: pl.DataFrame, lot_filter: str) -> pl.DataFrame:
    needle = str(lot_filter or "").strip()
    if not needle:
        return df
    for col in ("root_lot_id", "lot_id"):
        if col in df.columns:
            return df.filter(
                pl.col(col).cast(pl.Utf8, strict=False)
                .str.to_uppercase().str.contains(needle.upper(), literal=True)
            )
    return df


@router.get("/products")
def products(_user=Depends(current_user)):
    rp = _et_root()
    names: set[str] = set()
    for child in sorted(rp.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith("product="):
            names.add(child.name[len("product="):])
        else:
            hive = [d.name[len("product="):] for d in child.iterdir()
                    if d.is_dir() and d.name.startswith("product=")]
            if hive:
                names.update(hive)
            else:
                names.add(child.name)
    out = []
    for name in sorted(names):
        csv_fp = find_vehicle_csv(VEHICLE_DIR, name)
        out.append({"product": name, "vehicle_csv": csv_fp.name if csv_fp else ""})
    return {"products": out, "vehicle_dir": str(VEHICLE_DIR)}


@router.get("/settings")
def settings_get(_user=Depends(current_user)):
    return _settings()


class SettingsReq(BaseModel):
    page_rows: int = DEFAULT_SETTINGS["page_rows"]
    max_download_rows: int = DEFAULT_SETTINGS["max_download_rows"]


@router.post("/settings")
def settings_save(req: SettingsReq, user=Depends(current_user)):
    data = {
        "page_rows": max(10, min(int(req.page_rows), PAGE_ROWS_MAX)),
        "max_download_rows": max(100, min(int(req.max_download_rows), DOWNLOAD_ROWS_MAX)),
        "updated_by": user.get("username", ""),
    }
    save_json(SETTINGS_FILE, data)
    return {"ok": True, **_settings()}


class RunReq(BaseModel):
    product: str
    offset: int = 0
    limit: int = 0          # 0 → settings.page_rows
    lot_filter: str = ""


@router.post("/run")
def run(req: RunReq, _user=Depends(current_user)):
    t0 = time.monotonic()
    wide_full, out_cols, errors, vehicle_csv, table = _compute(req.product)
    wide = _apply_lot_filter(wide_full.select(out_cols), req.lot_filter)
    cfg = _settings()
    limit = req.limit if 0 < req.limit <= PAGE_ROWS_MAX else cfg["page_rows"]
    offset = max(0, int(req.offset))
    page = wide.slice(offset, limit)
    index_cols = [r["alias"] for r in table if r["alias"] in wide.columns]
    spec = {r["alias"]: {"unit": r["unit"], "speclow": r["speclow"],
                         "spechigh": r["spechigh"], "target": r["target"],
                         "category": r["category"]}
            for r in table if r["alias"] in wide.columns}
    return {
        "product": req.product,
        "vehicle_csv": vehicle_csv,
        "columns": list(page.columns),
        "index_columns": index_cols,
        "spec": spec,
        "rows": serialize_rows(page.to_dicts()),
        "offset": offset,
        "limit": limit,
        "total_rows": wide.height,
        "rule_errors": errors,
        "elapsed_ms": round((time.monotonic() - t0) * 1000),
    }


@router.get("/download")
def download(product: str = Query(...), lot_filter: str = Query(""),
             user=Depends(current_user)):
    wide_full, out_cols, _errors, vehicle_csv, _table = _compute(product)
    wide = _apply_lot_filter(wide_full.select(out_cols), lot_filter)
    cfg = _settings()
    if wide.height > cfg["max_download_rows"]:
        raise HTTPException(400, f"다운로드는 최대 {cfg['max_download_rows']:,}행까지 허용됩니다. "
                                 f"lot 필터를 걸거나 톱니바퀴 설정에서 상한을 조정하세요.")
    csv_bytes = wide.write_csv().encode("utf-8")
    jsonl_append(DL_LOG, {
        "source": "reformatize",
        "username": user.get("username", ""),
        "product": product,
        "sql": (f"lot_filter={lot_filter}" if lot_filter else ""),
        "rows": wide.height, "cols": wide.width,
        "select_cols": vehicle_csv,
        "size_mb": round(len(csv_bytes) / 1e6, 2),
    })
    return csv_response(csv_bytes, f"{safe_filename(product)}_reformatize")


# ── 관리자 전용: 새 ADDP 수식 테스트 ─────────────────────────────────
# vehicle CSV 를 고치기 전에 새 ADDP ITEM(alias)+ADDP Form 을 실제 ET 데이터로
# 계산해 보고, 결과를 CSV 로도 내려받을 수 있다. 수식은 기존 alias 와 raw item
# (예: {VTH_IDX}, {VTH}) 를 모두 참조 가능.
class TestItem(BaseModel):
    alias: str
    addp_form: str


class TestReq(BaseModel):
    product: str
    items: list[TestItem]
    lot_filter: str = ""
    offset: int = 0
    limit: int = 0


def _run_test(product: str, items: list[TestItem], lot_filter: str):
    """base wide + 테스트 ADDP 적용 → (결과 df[key+meta+참조+테스트], 테스트 alias, 에러)."""
    wide_full, out_cols, _base_errors, vehicle_csv, _table = _compute(product)
    rows = []
    seen_alias: set[str] = set()
    for it in items:
        alias = str(it.alias or "").strip()
        if not alias:
            continue
        if alias in wide_full.columns or alias in seen_alias:
            raise HTTPException(400, f"alias '{alias}' 는 이미 존재하는 컬럼입니다 — 다른 이름을 쓰세요")
        seen_alias.add(alias)
        rows.append({"alias": alias, "addp_form": it.addp_form})
    if not rows:
        raise HTTPException(400, "테스트할 ADDP 항목이 없습니다 (alias + ADDP Form 입력)")
    wide, errors = apply_addp_rows(wide_full, rows)
    test_aliases = [r["alias"] for r in rows]
    # 표시 컬럼: key/meta + 수식이 참조한 기존 컬럼 + 테스트 alias
    refs: list[str] = []
    for it in items:
        for m in re.findall(r"\{([^{}]+)\}", str(it.addp_form or "")):
            name = m.strip()
            if name in wide.columns and name not in refs and name not in test_aliases:
                refs.append(name)
    keys = [c for c in PIVOT_KEY_COLS if c in wide.columns]
    metas = [c for c in PIVOT_META_COLS if c in wide.columns]
    cols, seen = [], set()
    for c in keys + metas + refs + test_aliases:
        if c in wide.columns and c not in seen:
            cols.append(c)
            seen.add(c)
    out = _apply_lot_filter(wide.select(cols), lot_filter)
    return out, test_aliases, errors, vehicle_csv


@router.get("/formula-help")
def formula_help(product: str = Query(""), _admin=Depends(require_admin)):
    """수식 작성 도움말: 함수 목록 + (제품 지정 시) 참조 가능한 컬럼."""
    out = {"functions": FORMULA_HELP, "columns": {}}
    if product:
        wide_full, out_cols, _e, vehicle_csv, table = _compute(product)
        aliases = [r["alias"] for r in table if r["alias"] in wide_full.columns]
        keys = [c for c in PIVOT_KEY_COLS if c in wide_full.columns]
        metas = [c for c in PIVOT_META_COLS if c in wide_full.columns]
        raw_items = [c for c in wide_full.columns
                     if c not in aliases and c not in keys and c not in metas]
        out["columns"] = {"aliases": aliases, "raw_items": raw_items}
        out["vehicle_csv"] = vehicle_csv
    return out


@router.post("/test")
def test_run(req: TestReq, _admin=Depends(require_admin)):
    t0 = time.monotonic()
    out, test_aliases, errors, vehicle_csv = _run_test(req.product, req.items, req.lot_filter)
    cfg = _settings()
    limit = req.limit if 0 < req.limit <= PAGE_ROWS_MAX else cfg["page_rows"]
    offset = max(0, int(req.offset))
    page = out.slice(offset, limit)
    return {
        "product": req.product,
        "vehicle_csv": vehicle_csv,
        "columns": list(page.columns),
        "test_columns": test_aliases,
        "rows": serialize_rows(page.to_dicts()),
        "offset": offset,
        "limit": limit,
        "total_rows": out.height,
        "rule_errors": errors,
        "elapsed_ms": round((time.monotonic() - t0) * 1000),
    }


@router.post("/test/download")
def test_download(req: TestReq, admin=Depends(require_admin)):
    out, test_aliases, _errors, vehicle_csv = _run_test(req.product, req.items, req.lot_filter)
    cfg = _settings()
    if out.height > cfg["max_download_rows"]:
        raise HTTPException(400, f"다운로드는 최대 {cfg['max_download_rows']:,}행까지 허용됩니다. "
                                 f"lot 필터를 걸거나 톱니바퀴 설정에서 상한을 조정하세요.")
    csv_bytes = out.write_csv().encode("utf-8")
    jsonl_append(DL_LOG, {
        "source": "reformatize_test",
        "username": admin.get("username", ""),
        "product": req.product,
        "sql": (f"lot_filter={req.lot_filter}" if req.lot_filter else ""),
        "rows": out.height, "cols": out.width,
        "select_cols": ", ".join(f"{i.alias}={i.addp_form}" for i in req.items if str(i.alias or "").strip()),
        "size_mb": round(len(csv_bytes) / 1e6, 2),
    })
    return csv_response(csv_bytes, f"{safe_filename(req.product)}_addp_test")
