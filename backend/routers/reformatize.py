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

import datetime as dt
import logging
import os
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
    csv_response, jsonl_append, load_json, safe_filename,
    save_json, serialize_rows,
)
from core.vehicle_reformatter import (
    FORMULA_HELP, PIVOT_KEY_COLS, PIVOT_META_COLS, apply_addp_rows,
    find_vehicle_csv, formula_refs, load_vehicle_table, reformatize,
    rowwise_function_help,
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

# pivot+index 결과 캐시 — (product, 필터 조합) 별로 (sig 일치 시) 재사용.
# 값: (sig, wide_full_df, out_cols, rule_errors, vehicle_csv_name, vehicle_table, est_bytes)
# wide_full 은 raw item 컬럼을 포함 — 관리자 수식 테스트가 참조.
_CACHE: dict[tuple, tuple] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 8

# raw(long) ET df 캐시 — 필터 조합이 바뀔 때마다 파일을 다시 읽지 않도록 제품별 보관.
# 값: (sig, df, est_bytes)
_RAW_CACHE: dict[str, tuple] = {}
_RAW_CACHE_MAX = 2


def _env_cache_mb(name: str, default_mb: float, budget_name: str = "") -> int:
    raw = os.environ.get(name, "")
    pinned = raw not in (None, "")
    try:
        mb = float(raw) if pinned else default_mb
    except Exception:
        mb = default_mb
        pinned = False
    budget = int(max(0.0, min(65536.0, mb)) * 1024 * 1024)
    # 운영자 env 핀이 없을 때만 전체 캐시 풀 상한 적용 — 개별 예산 합이
    # 호스트 메모리를 넘지 않게 한다.
    if not pinned and budget_name:
        try:
            from core import cache_budget

            budget = cache_budget.capped(budget_name, budget)
        except Exception:
            pass
    return budget


def _cache_max_bytes() -> int:
    """wide 결과 캐시 byte 예산 (기본 512MB). count 캡과 별개로 적용."""
    return _env_cache_mb("FLOW_REFORMATIZE_CACHE_MAX_MB", 512.0, "reformatize_wide")


def _raw_cache_max_bytes() -> int:
    """raw(long) ET df 캐시 byte 예산 (기본 1024MB)."""
    return _env_cache_mb("FLOW_REFORMATIZE_RAW_CACHE_MAX_MB", 1024.0, "reformatize_raw")


def _df_est_bytes(df: pl.DataFrame) -> int:
    try:
        return int(df.estimated_size())
    except Exception:
        return 0


def _evict_cache_locked(cache: dict, max_entries: int, max_bytes: int, incoming_bytes: int) -> bool:
    """oldest-first 로 count/byte 예산 안으로 줄인다. _CACHE_LOCK 하에서 호출.

    항목 튜플의 마지막 원소가 est_bytes 라는 계약. incoming 단독으로 예산을
    넘으면 False (저장 스킵 — 결과는 그대로 반환되고 캐시만 안 한다).
    """
    if max_bytes > 0 and incoming_bytes > max_bytes:
        return False

    def _total() -> int:
        return sum(int(v[-1] or 0) for v in cache.values())

    while cache and (
        len(cache) >= max_entries
        or (max_bytes > 0 and _total() + incoming_bytes > max_bytes)
    ):
        cache.pop(next(iter(cache)), None)
    return True


def cache_stats() -> dict:
    """reformatize 캐시 현황 (관리자 메모리 종합 현황용) — 메타데이터만."""
    with _CACHE_LOCK:
        wide_bytes = sum(int(v[-1] or 0) for v in _CACHE.values())
        raw_bytes = sum(int(v[-1] or 0) for v in _RAW_CACHE.values())
        wide_entries = len(_CACHE)
        raw_entries = len(_RAW_CACHE)
    return {
        "wide": {"entries": wide_entries, "bytes": wide_bytes, "budget_bytes": _cache_max_bytes()},
        "raw": {"entries": raw_entries, "bytes": raw_bytes, "budget_bytes": _raw_cache_max_bytes()},
    }


def emergency_evict(max_bytes: int) -> int:
    """메모리 워치독 긴급 축출 — raw(큰 쪽)부터 오래된 순으로 최대 max_bytes 제거.

    다음 조회는 파일에서 다시 읽으므로 정확성 영향 없음. 반환: 회수 추정 바이트."""
    if max_bytes <= 0:
        return 0
    freed = 0
    with _CACHE_LOCK:
        for cache in (_RAW_CACHE, _CACHE):
            while cache and freed < max_bytes:
                key = next(iter(cache))
                value = cache.pop(key, None)
                if value is not None:
                    freed += int(value[-1] or 0)
    return freed


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
    """ET DB 루트 — ET 측정시간(lot_step)과 동일한 해석 규칙을 사용.

    lot_step 은 관리자 설정 루트·이름 토큰 매칭·하위 폴더 fallback 을 지원해
    사내 DB 구조(중첩/변형 이름)에서도 루트를 찾는다. 실패 시에만 기존
    resolve_named_child 방식으로 폴백.
    """
    try:
        from core.lot_step import ET_ROOT, _resolve_source_root_dirs
        dirs = _resolve_source_root_dirs("et", ET_ROOT)
        if dirs:
            return dirs[0]
    except Exception:
        pass
    rp = resolve_named_child(PATHS.db_root, ET_ROOT_NAME)
    if rp is None or not rp.is_dir():
        raise HTTPException(404, "DB ET 루트를 찾을 수 없습니다")
    return rp


def _product_files(product: str) -> list[Path]:
    """제품의 ET 데이터 파일 목록 — lot_step 과 동일한 탐색.

    제품 폴더(<PRODUCT>/제품명_시간.parquet), hive(product=/date=) 파티션,
    루트 플랫 파일명(제품명_날짜.parquet) 레이아웃을 모두 흡수한다.
    """
    from core.lot_step import ET_ROOT, _parquet_files
    files = [fp for fp in _parquet_files(ET_ROOT, product, source="et") if fp.is_file()]
    return sorted(set(files))


def _product_sig(product: str) -> tuple:
    """제품 데이터 파일들의 (path, mtime, size) 시그니처."""
    files = _product_files(product)
    if not files:
        raise HTTPException(404, f"ET 제품 없음: {product}")
    sig = []
    for fp in files:
        st = fp.stat()
        sig.append((str(fp), st.st_mtime, st.st_size))
    return tuple(sig)


def _read_et_files(files: list[Path]) -> pl.DataFrame:
    """파일 목록을 직접 읽어 read_source 와 동일한 정규화를 적용한 long df.

    read_source 는 db_root/<루트>/<제품>/ 고정 경로만 지원하므로, lot_step 이
    찾은 파일 목록(중첩 루트·date= hive 포함)을 그대로 읽는 경로를 둔다.
    """
    from core.utils import cast_all_str, filter_valid_wafer_ids_df, normalize_source_df, read_one_file
    dfs = []
    for fp in files:
        d = read_one_file(fp)
        if d is not None and d.height > 0:
            dfs.append(cast_all_str(d))
    if not dfs:
        raise HTTPException(404, "읽을 수 있는 ET 데이터 파일이 없습니다")
    if len(dfs) == 1:
        df = dfs[0]
    else:
        all_cols, seen = [], set()
        for d in dfs:
            for c in d.columns:
                if c not in seen:
                    all_cols.append(c)
                    seen.add(c)
        unified = []
        for d in dfs:
            missing = [c for c in all_cols if c not in d.columns]
            if missing:
                d = d.with_columns([pl.lit(None).cast(pl.Utf8).alias(c) for c in missing])
            unified.append(d.select(all_cols))
        df = pl.concat(unified, how="vertical")
    return normalize_source_df(filter_valid_wafer_ids_df(df), root=_et_root().name, file="")


def _load_raw(product: str, sig: tuple) -> pl.DataFrame:
    """제품의 raw(long) ET df — 파일 시그니처가 같으면 캐시 재사용."""
    with _CACHE_LOCK:
        hit = _RAW_CACHE.get(product)
        if hit and hit[0] == sig:
            return hit[1]
    df = _read_et_files([Path(p) for p, _mtime, _size in sig])
    est = _df_est_bytes(df)
    with _CACHE_LOCK:
        _RAW_CACHE.pop(product, None)
        if _evict_cache_locked(_RAW_CACHE, _RAW_CACHE_MAX, _raw_cache_max_bytes(), est):
            _RAW_CACHE[product] = (sig, df, est)
    return df


def _compute(product: str, f: Filters) -> tuple[pl.DataFrame, list[str], list[str], str, list[dict], int]:
    """제품의 reformatize 결과 (full wide, 출력 컬럼, 규칙 에러, csv 이름, 규칙 테이블, 필터된 raw 행수).

    필터는 raw(long) ET 데이터에 먼저 적용한 뒤 reformatize 한다 — ADDP 의
    그룹 통계(std/avg)도 필터된 모집단 기준으로 계산된다.
    결과는 (product, 필터 조합) 단위로 캐시 (파일 시그니처 일치 시 재사용).
    raw 행수는 다운로드 상한(max_download_rows) 판정 기준.
    """
    csv_fp = find_vehicle_csv(VEHICLE_DIR, product)
    if csv_fp is None:
        raise HTTPException(400, f"'{product}' 에 매칭되는 vehicle reformatter CSV 가 없습니다 "
                                 f"({VEHICLE_DIR} 안에 <vehicle>_reformatter.csv 를 두세요)")
    st = csv_fp.stat()
    sig = _product_sig(product) + ((str(csv_fp), st.st_mtime, st.st_size),)
    key = (product, _filters_key(f))
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and hit[0] == sig:
            return hit[1], hit[2], hit[3], hit[4], hit[5], hit[6]
    table = load_vehicle_table(csv_fp)
    if not table:
        raise HTTPException(400, f"{csv_fp.name}: 유효한 REAL/ADDP 행이 없습니다")
    df = _load_raw(product, sig)
    if df.height == 0:
        raise HTTPException(400, f"'{product}' ET 데이터가 비어 있습니다")
    df = _apply_filters(df, f)
    if df.height == 0:
        raise HTTPException(400, "필터 조건에 맞는 ET 데이터가 없습니다 — 기간·lot 등 필터를 조정하세요")
    raw_rows = df.height
    try:
        wide, out_cols, errors = reformatize(df, table)
    except ValueError as e:
        raise HTTPException(400, str(e))
    est = _df_est_bytes(wide)
    with _CACHE_LOCK:
        _CACHE.pop(key, None)
        if _evict_cache_locked(_CACHE, _CACHE_MAX, _cache_max_bytes(), est):
            _CACHE[key] = (sig, wide, out_cols, errors, csv_fp.name, table, raw_rows, est)
    return wide, out_cols, errors, csv_fp.name, table, raw_rows


# ── 조회/다운로드 필터 ───────────────────────────────────────────────
# 필터는 raw(long) ET 데이터에 적용된 뒤 reformatize 로 넘어간다 —
# total_site_cnt 처럼 raw 에만 있는 컬럼도 그대로 필터 가능하다.
class Filters(BaseModel):
    lot_filter: str = ""        # root_lot_id 포함 검색 (쉼표 = OR)
    step_filter: str = ""       # step_id 포함 검색 (쉼표 = OR)
    wafer_filter: str = ""      # wafer_id 포함 검색 (쉼표 = OR)
    site_cnt_filter: str = ""   # total_site_cnt 정확 일치 (쉼표 = OR)
    days: int = 0               # tkout_time 최신값 기준 최근 N일 — 지정 시 date_from/to 무시
    date_from: str = ""         # YYYY-MM-DD, tkout_time ≥ 해당일 00:00
    date_to: str = ""           # YYYY-MM-DD, tkout_time < 익일 00:00 (해당일 포함)


def _filters_key(f: Filters) -> tuple:
    """캐시 키용 정규화 필터 튜플 — 같은 조합이면 같은 캐시 엔트리."""
    return (f.lot_filter.strip().upper(), f.step_filter.strip().upper(),
            f.wafer_filter.strip().upper(), f.site_cnt_filter.strip(),
            max(0, int(f.days or 0)), f.date_from.strip(), f.date_to.strip())


def _has_filter(f: Filters) -> bool:
    return bool(f.lot_filter.strip() or f.step_filter.strip() or f.wafer_filter.strip()
                or f.site_cnt_filter.strip() or f.days > 0
                or f.date_from.strip() or f.date_to.strip())


def _filter_desc(f: Filters) -> str:
    parts = []
    if f.days > 0:
        parts.append(f"days={f.days}")
    elif f.date_from.strip() or f.date_to.strip():
        parts.append(f"tkout={f.date_from.strip() or '…'}~{f.date_to.strip() or '…'}")
    for k, v in (("lot", f.lot_filter), ("step", f.step_filter),
                 ("wafer", f.wafer_filter), ("site_cnt", f.site_cnt_filter)):
        if v.strip():
            parts.append(f"{k}={v.strip()}")
    return ", ".join(parts)


def _contains_any(df: pl.DataFrame, col: str, spec: str,
                  fallback_cols: tuple[str, ...] = ()) -> pl.DataFrame:
    vals = [v.strip().upper() for v in str(spec or "").split(",") if v.strip()]
    if not vals:
        return df
    use = next((c for c in (col, *fallback_cols) if c in df.columns), None)
    if use is None:
        raise HTTPException(400, f"'{col}' 컬럼이 데이터에 없어 필터를 적용할 수 없습니다")
    base = pl.col(use).cast(pl.Utf8, strict=False).str.to_uppercase()
    expr = base.str.contains(vals[0], literal=True)
    for v in vals[1:]:
        expr = expr | base.str.contains(v, literal=True)
    return df.filter(expr)


def _site_cnt_filter(df: pl.DataFrame, spec: str) -> pl.DataFrame:
    vals = [v.strip() for v in str(spec or "").split(",") if v.strip()]
    if not vals:
        return df
    if "total_site_cnt" not in df.columns:
        raise HTTPException(400, "total_site_cnt 컬럼이 데이터에 없어 필터를 적용할 수 없습니다")
    try:
        nums = [int(float(v)) for v in vals]
    except ValueError:
        raise HTTPException(400, f"total_site_cnt 필터는 숫자만 허용됩니다: {spec}")
    return df.filter(pl.col("total_site_cnt").cast(pl.Int64, strict=False).is_in(nums))


def _tkout_filter(df: pl.DataFrame, days: int, date_from: str, date_to: str) -> pl.DataFrame:
    days = max(0, int(days or 0))
    date_from = str(date_from or "").strip()
    date_to = str(date_to or "").strip()
    if not days and not date_from and not date_to:
        return df
    if "tkout_time" not in df.columns:
        raise HTTPException(400, "tkout_time 컬럼이 데이터에 없어 기간 필터를 적용할 수 없습니다")
    dtype = df.schema["tkout_time"]
    ts = (pl.col("tkout_time").str.to_datetime(strict=False) if dtype == pl.Utf8
          else pl.col("tkout_time").cast(pl.Datetime("us"), strict=False))
    if days:
        anchor = df.select(ts.max().alias("m")).item()
        if anchor is None:
            raise HTTPException(400, "tkout_time 값을 날짜로 해석할 수 없습니다")
        return df.filter(ts >= anchor - dt.timedelta(days=days))

    def _parse(name: str, s: str) -> dt.datetime:
        try:
            return dt.datetime.combine(dt.date.fromisoformat(s), dt.time.min)
        except ValueError:
            raise HTTPException(400, f"{name} 은 YYYY-MM-DD 형식이어야 합니다: {s}")

    cond = None
    if date_from:
        cond = ts >= _parse("date_from", date_from)
    if date_to:
        c2 = ts < _parse("date_to", date_to) + dt.timedelta(days=1)
        cond = c2 if cond is None else (cond & c2)
    return df.filter(cond)


def _apply_filters(df: pl.DataFrame, f: Filters) -> pl.DataFrame:
    df = _tkout_filter(df, f.days, f.date_from, f.date_to)
    df = _contains_any(df, "root_lot_id", f.lot_filter, fallback_cols=("lot_id",))
    df = _contains_any(df, "step_id", f.step_filter)
    df = _contains_any(df, "wafer_id", f.wafer_filter)
    df = _site_cnt_filter(df, f.site_cnt_filter)
    return df


@router.get("/products")
def products(_user=Depends(current_user)):
    # ET 측정시간과 동일한 탐색(db_product_candidates) — 제품 폴더·hive·플랫
    # 파일명·parquet product 컬럼 스캔까지 흡수. 실패 시 기존 폴더 나열 폴백.
    names: set[str] = set()
    try:
        from core.lot_step import ET_ROOT, db_product_candidates
        names.update(db_product_candidates(source_root=ET_ROOT, source="et", limit=1000))
    except Exception:
        logger.exception("reformatize products: db_product_candidates failed")
    if not names:
        rp = _et_root()
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


@router.get("/items")
def list_items(product: str = Query(...), _user=Depends(current_user)):
    """제품 vehicle CSV 의 REAL/ADDP 항목 목록 — index 선택 UI 용.

    REAL 은 raw ITEMID·abs·scale factor, ADDP 는 ADDP Form 과 참조 컬럼을 함께 반환.
    데이터를 읽지 않고 규칙 CSV 만 파싱하므로 가볍다.
    """
    csv_fp = find_vehicle_csv(VEHICLE_DIR, product)
    if csv_fp is None:
        raise HTTPException(400, f"'{product}' 에 매칭되는 vehicle reformatter CSV 가 없습니다")
    table = load_vehicle_table(csv_fp)
    items = [{
        "alias": r["alias"],
        "category": r["category"],
        "itemid": r["itemid"],
        "abs": r["absolute"],
        "scale": r["scale"],
        "addp_form": r["addp_form"],
        "refs": formula_refs(r["addp_form"]) if r["category"] == "addp" else [],
        "unit": r["unit"],
        "speclow": r["speclow"], "spechigh": r["spechigh"], "target": r["target"],
        "report_order": r["report_order"],
    } for r in table]
    items.sort(key=lambda x: (x["report_order"] if x["report_order"] is not None else 9999, x["alias"]))
    return {"product": product, "vehicle_csv": csv_fp.name, "items": items}


# ── 집계(aggregation) — shot 단위 wide 를 (root_lot_id, wafer_id, step_id,
#    PGM(pt)) 그룹으로 요약해서 뽑는 옵션 ─────────────────────────────
_AGG_METHODS = ("max", "min", "median", "avg", "std", "p90", "p10")


def _agg_expr(col: str, method: str) -> pl.Expr:
    e = pl.col(col).cast(pl.Float64, strict=False)
    if method == "max":
        return e.max()
    if method == "min":
        return e.min()
    if method == "median":
        return e.median()
    if method == "avg":
        return e.mean()
    if method == "std":
        return e.std()
    if method == "p90":
        return e.quantile(0.9, "linear")
    return e.quantile(0.1, "linear")  # p10


def _ensure_pgm(wide: pl.DataFrame) -> pl.DataFrame:
    """pgm 컬럼이 없으면 ET 측정시간과 동일 규칙으로 파생.

    PGM(pt) = step_seq(측정 shot 수 pt), 같은 (step_id, step_seq, pt) 조합에
    재측정(tkout_time dense rank ≥ 2)이 있을 때만 _차수 접미사.
    """
    if "pgm" in wide.columns:
        return wide
    if "step_seq" not in wide.columns:
        return wide.with_columns(pl.lit("").alias("pgm"))
    pkg = [c for c in ("root_lot_id", "wafer_id", "step_id", "step_seq", "tkout_time") if c in wide.columns]
    w = wide.with_columns(pl.len().over(pkg).alias("_pt"))
    if "tkout_time" in w.columns:
        dup_keys = [c for c in ("root_lot_id", "wafer_id", "step_id", "step_seq", "_pt") if c in w.columns]
        w = w.with_columns(pl.col("tkout_time").rank("dense").over(dup_keys).cast(pl.Int64).alias("_dup"))
    else:
        w = w.with_columns(pl.lit(1, dtype=pl.Int64).alias("_dup"))
    fam_keys = [c for c in ("step_id", "step_seq", "_pt") if c in w.columns]
    w = w.with_columns(pl.col("_dup").max().over(fam_keys).alias("_dupmax"))
    label = pl.col("step_seq").cast(pl.Utf8) + "(" + pl.col("_pt").cast(pl.Utf8) + "pt)"
    return w.with_columns(
        pl.when(pl.col("_dupmax") > 1)
        .then(label + "_" + pl.col("_dup").cast(pl.Utf8))
        .otherwise(label)
        .alias("pgm")
    ).drop(["_pt", "_dup", "_dupmax"])


def _aggregate(wide: pl.DataFrame, alias_cols: list[str], method: str) -> pl.DataFrame:
    """wide(shot 단위) → (root_lot_id, wafer_id, step_id, pgm) 그룹 집계."""
    method = str(method or "").strip().lower()
    if method not in _AGG_METHODS:
        raise HTTPException(400, f"지원하지 않는 집계 방식 '{method}' — 허용: {', '.join(_AGG_METHODS)}")
    w = _ensure_pgm(wide)
    keys = [c for c in ("root_lot_id", "wafer_id", "step_id", "pgm") if c in w.columns]
    if not keys:
        raise HTTPException(400, "집계 키(root_lot_id/wafer_id/step_id/PGM) 컬럼이 데이터에 없습니다")
    cols = [c for c in alias_cols if c in w.columns]
    if not cols:
        raise HTTPException(400, "집계할 index 컬럼이 없습니다 — Index 항목을 선택하세요")
    aggs = [pl.len().alias("shot_count")] + [_agg_expr(c, method).alias(c) for c in cols]
    return w.group_by(keys, maintain_order=True).agg(aggs).sort(keys)


def _select_aliases(out_cols: list[str], wanted: list[str]) -> list[str]:
    """out_cols(key+meta+alias 순서)에서 key/meta 는 유지하고 alias 는 wanted 만 남긴다."""
    fixed = set(PIVOT_KEY_COLS) | set(PIVOT_META_COLS)
    want = {str(a).strip() for a in wanted if str(a).strip()}
    if not want:
        return out_cols
    return [c for c in out_cols if c in fixed or c in want]


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


class RunReq(Filters):
    product: str
    offset: int = 0
    limit: int = 0          # 0 → settings.page_rows
    items: list[str] = []   # 선택된 index alias — 비우면 전체
    agg: str = ""           # ""=shot raw, 또는 max/min/median/avg/std/p90/p10


@router.post("/run")
def run(req: RunReq, _user=Depends(current_user)):
    t0 = time.monotonic()
    wide_full, out_cols, errors, vehicle_csv, table, _raw_rows = _compute(req.product, req)
    out_cols = _select_aliases(out_cols, req.items)
    wide = wide_full.select(out_cols)
    if req.agg.strip():
        wide = _aggregate(wide, [r["alias"] for r in table if r["alias"] in wide.columns], req.agg)
    cfg = _settings()
    limit = req.limit if 0 < req.limit <= PAGE_ROWS_MAX else cfg["page_rows"]
    offset = max(0, int(req.offset))
    page = wide.slice(offset, limit)
    index_cols = [r["alias"] for r in table if r["alias"] in wide.columns]
    # spec: 헤더 클릭 시 "이 index 가 어떻게 계산됐는지" 를 보여주기 위한 규칙 상세.
    spec = {r["alias"]: {"unit": r["unit"], "speclow": r["speclow"],
                         "spechigh": r["spechigh"], "target": r["target"],
                         "category": r["category"],
                         "itemid": r["itemid"], "abs": r["absolute"], "scale": r["scale"],
                         "addp_form": r["addp_form"],
                         "refs": formula_refs(r["addp_form"]) if r["category"] == "addp" else []}
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
             step_filter: str = Query(""), wafer_filter: str = Query(""),
             site_cnt_filter: str = Query(""), days: int = Query(0),
             date_from: str = Query(""), date_to: str = Query(""),
             items: str = Query(""), agg: str = Query(""), user=Depends(current_user)):
    f = Filters(lot_filter=lot_filter, step_filter=step_filter, wafer_filter=wafer_filter,
                site_cnt_filter=site_cnt_filter, days=days, date_from=date_from, date_to=date_to)
    if not _has_filter(f):
        raise HTTPException(400, "필터 없이 전체 다운로드는 허용되지 않습니다 — "
                                 "기간(tkout_time)·root_lot_id·step_id·wafer_id·total_site_cnt "
                                 "중 하나 이상을 지정하세요")
    wide_full, out_cols, _errors, vehicle_csv, table, raw_rows = _compute(product, f)
    cfg = _settings()
    # 상한 판정은 필터된 raw(long) 행수 기준 — 집계/pivot 으로 결과 행이 줄어도
    # raw 가 상한을 넘으면 차단.
    if raw_rows > cfg["max_download_rows"]:
        raise HTTPException(400, f"필터된 raw 데이터 {raw_rows:,}행이 다운로드 최대 "
                                 f"{cfg['max_download_rows']:,}행을 초과합니다. 기간·lot 등 필터를 "
                                 f"조정해 행을 줄이거나 톱니바퀴 설정에서 상한을 올리세요.")
    wanted = [s.strip() for s in items.split(",") if s.strip()]
    out_cols = _select_aliases(out_cols, wanted)
    wide = wide_full.select(out_cols)
    agg_method = str(agg or "").strip().lower()
    if agg_method:
        wide = _aggregate(wide, [r["alias"] for r in table if r["alias"] in wide.columns], agg_method)
    csv_bytes = wide.write_csv().encode("utf-8")
    jsonl_append(DL_LOG, {
        "source": "reformatize",
        "username": user.get("username", ""),
        "product": product,
        "sql": _filter_desc(f) + (f", agg={agg_method}(root_lot·wafer·step·pgm)" if agg_method else ""),
        "agg": agg_method,
        "rows": wide.height, "cols": wide.width, "raw_rows": raw_rows,
        "select_cols": vehicle_csv + (" | " + ",".join(wanted) if wanted else " | all"),
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


class TestReq(Filters):
    product: str
    items: list[TestItem]
    offset: int = 0
    limit: int = 0


def _run_test(product: str, items: list[TestItem], f: Filters):
    """필터된 base wide + 테스트 ADDP 적용 → (결과 df[key+meta+참조+테스트], 테스트 alias, 에러)."""
    wide_full, out_cols, _base_errors, vehicle_csv, _table, _raw_rows = _compute(product, f)
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
    out = wide.select(cols)
    return out, test_aliases, errors, vehicle_csv


@router.get("/formula-help")
def formula_help(product: str = Query(""), _admin=Depends(require_admin)):
    """수식 작성 도움말: 함수 목록 + 매뉴얼(row 단위) 함수 + (제품 지정 시) 참조 컬럼."""
    out = {
        "functions": FORMULA_HELP,
        "manual_functions": rowwise_function_help(),
        "manual_file": str(PATHS.data_root / "reformatter" / "manual_functions.py"),
        "columns": {},
    }
    if product:
        wide_full, out_cols, _e, vehicle_csv, table, _raw_rows = _compute(product, Filters())
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
    out, test_aliases, errors, vehicle_csv = _run_test(req.product, req.items, req)
    cfg = _settings()
    limit = req.limit if 0 < req.limit <= PAGE_ROWS_MAX else cfg["page_rows"]
    offset = max(0, int(req.offset))
    page = out.slice(offset, limit)
    spec = {str(i.alias).strip(): {"category": "addp", "addp_form": i.addp_form,
                                   "refs": formula_refs(i.addp_form),
                                   "unit": "", "speclow": None, "spechigh": None, "target": None,
                                   "itemid": "", "abs": False, "scale": 1.0}
            for i in req.items if str(i.alias or "").strip()}
    return {
        "product": req.product,
        "vehicle_csv": vehicle_csv,
        "columns": list(page.columns),
        "test_columns": test_aliases,
        "spec": spec,
        "rows": serialize_rows(page.to_dicts()),
        "offset": offset,
        "limit": limit,
        "total_rows": out.height,
        "rule_errors": errors,
        "elapsed_ms": round((time.monotonic() - t0) * 1000),
    }


@router.post("/test/download")
def test_download(req: TestReq, admin=Depends(require_admin)):
    if not _has_filter(req):
        raise HTTPException(400, "필터 없이 전체 다운로드는 허용되지 않습니다 — "
                                 "기간(tkout_time)·root_lot_id·step_id·wafer_id·total_site_cnt "
                                 "중 하나 이상을 지정하세요")
    out, test_aliases, _errors, vehicle_csv = _run_test(req.product, req.items, req)
    cfg = _settings()
    if out.height > cfg["max_download_rows"]:
        raise HTTPException(400, f"결과 {out.height:,}행이 다운로드 최대 {cfg['max_download_rows']:,}행을 "
                                 f"초과합니다. 기간·lot 등 필터를 조정해 행을 줄이거나 "
                                 f"톱니바퀴 설정에서 상한을 올리세요.")
    csv_bytes = out.write_csv().encode("utf-8")
    jsonl_append(DL_LOG, {
        "source": "reformatize_test",
        "username": admin.get("username", ""),
        "product": req.product,
        "sql": _filter_desc(req),
        "rows": out.height, "cols": out.width,
        "select_cols": ", ".join(f"{i.alias}={i.addp_form}" for i in req.items if str(i.alias or "").strip()),
        "size_mb": round(len(csv_bytes) / 1e6, 2),
    })
    return csv_response(csv_bytes, f"{safe_filename(req.product)}_addp_test")
