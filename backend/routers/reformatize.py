"""routers/reformatize.py — 업무 탭 "ET 다운로드": DB ET → vehicle reformatter index 추출.

auto report 의 reformatize 흐름을 flow 화면으로 제공한다.
제품(DB ET 폴더)을 고르면 `data_root/reformatter/<vehicle>_reformatter.csv`
규칙으로 shot 단위 index 값을 계산해 페이지 단위로 반환/다운로드한다.

Endpoints:
  GET  /api/reformatize/products      — DB ET 제품 목록 + 매칭된 vehicle CSV
  GET  /api/reformatize/settings      — 페이지 행 수 등 설정 조회
  POST /api/reformatize/settings      — 설정 저장 (톱니바퀴)
  POST /api/reformatize/run           — index 계산 후 offset/limit 페이지 반환
  GET  /api/reformatize/download      — 즉시 CSV 다운로드 (호환 경로, 대기열 미사용)
  POST /api/reformatize/download/start  — 다운로드 작업 대기열 등록 (job_id 즉시 반환)
  GET  /api/reformatize/download/status — 작업 진행 폴링 (대기 순번·단계·행수)
  GET  /api/reformatize/download/queue  — 내 작업 + 서버 대기열 현황
  GET  /api/reformatize/download/file   — 완료된 작업 CSV 전송 (downloads.jsonl 기록)
  POST /api/reformatize/download/cancel — 작업 취소
  POST /api/reformatize/visibility    — (admin) reformatter 별 유저 비공개 항목 저장
  GET  /api/reformatize/formula-help  — (admin) 수식 함수/참조 컬럼 도움말
  POST /api/reformatize/test          — (admin) 새 ADDP 수식 테스트 미리보기
  POST /api/reformatize/test/download — (admin) 테스트 결과 CSV (downloads.jsonl 기록)
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import multiprocessing
import os
import queue
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import polars as pl
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app_v2.shared.source_adapter import resolve_named_child
from core.auth import current_user, is_page_manager, require_admin
from core.paths import PATHS
from core.utils import (
    download_content_disposition, download_filename,
    jsonl_append, jsonl_read, load_json, safe_filename,
    save_json, serialize_rows,
)
from core.vehicle_reformatter import (
    FORMULA_HELP, PIVOT_KEY_COLS, PIVOT_META_COLS, _MA_WINDOW_SUFFIXES,
    apply_addp_rows, build_dependency_tree, find_vehicle_csv, formula_refs,
    load_vehicle_table, reformatize, resolve_needed_items,
    rowwise_function_help,
)

logger = logging.getLogger("flow.reformatize")
router = APIRouter(prefix="/api/reformatize", tags=["reformatize"])

VEHICLE_DIR = PATHS.data_root / "reformatter"
SETTINGS_FILE = PATHS.data_root / "reformatize_settings.json"
VISIBILITY_FILE = PATHS.data_root / "reformatize_visibility.json"
HISTORY_FILE = PATHS.data_root / "reformatize_history.jsonl"
PINS_FILE = PATHS.data_root / "reformatize_pins.json"
LIKES_FILE = PATHS.data_root / "reformatize_likes.json"
REFORMATIZE_VISIBLE_RECENT = 500
_REFORMATIZE_HISTORY_LOCK = threading.Lock()
_REFORMATIZE_PIN_LOCK = threading.Lock()
_REFORMATIZE_LIKE_LOCK = threading.Lock()
DL_LOG = PATHS.download_log
ET_ROOT_NAME = "ET"


def _hidden_aliases(vehicle_csv: str) -> set[str]:
    """관리자가 유저에게 비공개로 정한 alias 집합 — vehicle CSV 파일명 기준.

    파일이 없거나 항목이 없으면 빈 집합 = **기본 전부 공개**."""
    if not vehicle_csv:
        return set()
    raw = load_json(VISIBILITY_FILE, {}) or {}
    vals = raw.get(str(vehicle_csv)) or []
    return {str(v).strip() for v in vals if str(v).strip()}


def _find_csv(product: str) -> Path | None:
    """reformatter CSV 탐색 — DB 루트 reformatter 폴더 우선, data_root 폴백.

    사내 DB 구조에서 reformatter CSV 를 DB 트리 아래 두는 운영을 지원한다.
    """
    try:
        db_ref_dir = PATHS.db_root / "reformatter"
        if db_ref_dir.is_dir():
            fp = find_vehicle_csv(db_ref_dir, product)
            if fp is not None:
                return fp
    except Exception:
        pass
    return find_vehicle_csv(VEHICLE_DIR, product)

# value_col: ET 원본의 측정값 열 이름. "" = 자동 감지(value/et_value/...).
# scale_applied: 원본 값에 reformatter REAL scale 이 이미 곱해져 있는 소스
#   (예: auto report 가 만든 제품_시간.parquet 의 et_value). True 면 REAL 단계에서
#   scale 을 다시 곱하지 않는다 — ADDP 수식은 우리가 계산하므로 그대로 적용.
DEFAULT_SETTINGS = {"page_rows": 500, "max_download_mb": 500, "max_download_rows": 100_000,
                    "value_col": "", "scale_applied": False, "share_base_url": ""}
PAGE_ROWS_MAX = 5_000
DOWNLOAD_MB_MAX = 10_000
DOWNLOAD_ROWS_MAX = 1_000_000

# pivot+index 결과 캐시 — (product, 필터 조합) 별로 (full_sig 일치 시) 재사용.
# 값: (full_sig, wide_full_df, out_cols, rule_errors, vehicle_csv_name, vehicle_table, raw_rows, est_bytes)
# wide_full 은 raw item 컬럼을 포함 — 관리자 수식 테스트가 참조.
_CACHE: dict[tuple, tuple] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 8

# raw(long) ET df 캐시.
# 키: (product, 필터키, item_id 집합 해시) — raw 를 필터까지 적용해서 읽으므로
# 제품 단위가 아니라 "이 조회 조건" 단위로 보관한다. 제품 전체를 통째로 들고
# 있지 않기 때문에 항목 수를 늘려도 예산 안에서 안전하다.
# 값: (full_sig, df, est_bytes)
_RAW_CACHE: dict[tuple, tuple] = {}
_RAW_CACHE_MAX = 4


def _env_cache_mb(name: str, default_mb: float, budget_name: str = "") -> int:
    raw = os.environ.get(name, "")
    pinned = raw not in (None, "")
    try:
        mb = float(raw) if pinned else default_mb
    except Exception:
        mb = default_mb
        pinned = False
    budget = int(max(0.0, min(65536.0, mb)) * 1024 * 1024)
    # 운영자 env 핀도 전체 안전 풀은 우회하지 않는다. explicit=True 는 개발
    # 역할의 자동 축소만 제외하고 호스트 총량 기반 상한은 그대로 적용한다.
    if budget_name:
        try:
            from core import cache_budget

            budget = cache_budget.capped(budget_name, budget, explicit=pinned)
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
        mb_val = raw.get("max_download_mb")
        if mb_val is None:
            mb_val = DEFAULT_SETTINGS["max_download_mb"]
        out["max_download_mb"] = max(10, min(int(mb_val), DOWNLOAD_MB_MAX))
    except Exception:
        pass
    try:
        out["max_download_rows"] = max(100, min(int(raw.get("max_download_rows", out["max_download_mb"] * 1000)), DOWNLOAD_ROWS_MAX))
    except Exception:
        pass
    out["value_col"] = str(raw.get("value_col", "") or "").strip()[:64]
    out["scale_applied"] = bool(raw.get("scale_applied", False))
    try:
        from core.mail import get_share_base_url
        shared_url = get_share_base_url()
    except Exception:
        shared_url = ""
    out["share_base_url"] = shared_url or str(raw.get("share_base_url", "") or "").strip()
    return out


def _ensure_size_within_limit(df: pl.DataFrame, max_mb: int | None, context: str = "조회/다운로드") -> None:
    """설정 한도를 넘는 wide pivot/CSV 생성을 용량(MB) 단위로 차단한다."""
    if max_mb is None or max_mb <= 0:
        return
    est_bytes = _df_est_bytes(df)
    est_mb = est_bytes / (1024 * 1024)
    if est_mb > max_mb:
        raise HTTPException(
            400,
            f"용량초과: {context} 결과 용량({est_mb:.1f}MB)이 설정 한도({max_mb:,}MB)를 초과합니다. "
            "필터(기간·lot 등)를 추가해 범위를 좁히거나 ⚙ 설정에서 최대 용량(MB)을 늘려주세요.",
        )


def _ensure_raw_rows_within_limit(raw_rows: int, max_raw_rows: int | None) -> None:
    """하위 호환성 유지용 (용량 MB 제어로 전환되어 행 수 컷은 미사용)."""
    pass


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


# ── 파일 단위 기간 pruning ────────────────────────────────────────────
# ET raw 는 제품 폴더 아래 1일 1파일(`<PRODUCT>_2025_07_23.parquet`) 이고 하루치도
# 무겁다. 파일명(또는 hive `date=`)의 날짜만으로 기간 밖 파일은 열지도 않는다.
# 이 pruning 이 없으면 "최근 3일" 요청에도 제품 전체(수백 파일)를 읽고 나서야
# 기간 필터가 적용돼 워커가 메모리/시간 초과로 죽는다(502).
# 겹치는 후보까지 모두 찾도록 lookahead 안에 담는다. `A1234_2025_07_23` 처럼
# 제품명 숫자가 앞쪽에서 먼저 매칭돼 진짜 날짜를 가리는 일을 막는다.
_FILE_DATE_RE = re.compile(r"(?=((?<!\d)(\d{4})[-_.]?(\d{2})[-_.]?(\d{2})(?!\d)))")
_FILE_DATE_MARGIN_DAYS = 1     # 파일명 날짜와 tkout_time 경계의 오차 흡수


def _file_date_from_text(text: str) -> dt.date | None:
    """문자열 안의 날짜 토큰 — 여러 후보가 있으면 뒤쪽(진짜 날짜)을 택한다."""
    found = None
    for m in _FILE_DATE_RE.finditer(text):
        try:
            found = dt.date(int(m.group(2)), int(m.group(3)), int(m.group(4)))
        except ValueError:
            continue
    return found


def _file_date(fp: Path) -> dt.date | None:
    """파일 경로에서 데이터 날짜 추출 — 파일명 우선, 없으면 hive `date=` 파티션."""
    stem = fp.stem
    for text in (stem, *[p[len("date="):] for p in fp.parts[:-1] if p.startswith("date=")]):
        hit = _file_date_from_text(text)
        if hit is not None:
            return hit
    return None


def _iso_date(value: str) -> dt.date | None:
    """YYYY-MM-DD → date. 형식이 틀리면 None (eager `_tkout_filter` 가 400 으로 알림)."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


def _prune_files_by_date(files: list[Path], f: "Filters") -> list[Path]:
    """기간 필터 밖의 파일을 목록에서 제거. 날짜를 못 읽는 파일은 보수적으로 유지."""
    days = max(0, int(f.days or 0))
    date_from, date_to = f.date_from.strip(), f.date_to.strip()
    if not files or (not days and not date_from and not date_to):
        return files
    dated = [(fp, _file_date(fp)) for fp in files]
    known = [(fp, d) for fp, d in dated if d is not None]
    if not known:
        return files
    lo = hi = None
    if days:
        # days 는 "가장 최근 tkout 기준 N일". 최신 파일은 항상 남기므로
        # eager `_tkout_filter` 가 잡는 anchor 값이 pruning 전후로 같다.
        anchor = max(d for _, d in known)
        lo = anchor - dt.timedelta(days=days + _FILE_DATE_MARGIN_DAYS)
    else:
        d_lo, d_hi = _iso_date(date_from), _iso_date(date_to)
        if d_lo:
            lo = d_lo - dt.timedelta(days=_FILE_DATE_MARGIN_DAYS)
        if d_hi:
            hi = d_hi + dt.timedelta(days=_FILE_DATE_MARGIN_DAYS)
    if lo is None and hi is None:
        return files
    keep = []
    for fp, d in dated:
        if d is None:
            keep.append(fp)
            continue
        if lo is not None and d < lo:
            continue
        if hi is not None and d > hi:
            continue
        keep.append(fp)
    return keep


def _partition_value(fp: Path, name: str) -> str:
    """Hive 경로의 ``name=value`` 값을 반환한다(대소문자 무시)."""
    prefix = str(name or "").strip().lower() + "="
    for part in fp.parts:
        text = str(part)
        if text.lower().startswith(prefix):
            return text[len(prefix):]
    return ""


def _prune_files_by_filters(files: list[Path], f: "Filters") -> list[Path]:
    """경로 partition으로 판정 가능한 lot/step/step_seq/wafer 파일만 남긴다.

    partition이 없는 파일은 보수적으로 유지한다. 따라서 flat 일단위 파일의
    정확성은 그대로이고, root_lot_id/step_id/wafer_id hive 구조에서는 관련
    없는 parquet을 열기 전에 제외한다.
    """
    specs = (
        ("root_lot_id", f.lot_filter),
        ("step_id", f.step_filter),
        ("step_seq", f.step_seq_filter),
        ("wafer_id", f.wafer_filter),
    )
    out = list(files)
    for col, raw in specs:
        tokens = [v.strip().upper() for v in str(raw or "").split(",") if v.strip()]
        if not tokens:
            continue
        next_files = []
        for fp in out:
            value = _partition_value(fp, col)
            if not value or any(token in value.upper() for token in tokens):
                next_files.append(fp)
        out = next_files
    return out


# ── 열(column) projection ─────────────────────────────────────────────
# ET raw 는 컬럼이 20개 안팎이지만 실제로 쓰이는 건 pivot key/meta·item/value·
# 필터 대상뿐이다. 나머지(part_id·probe_card_id·subitem_id·temperature 등)는
# 읽는 순간부터 메모리를 먹기만 하므로 스캔 단계에서 잘라낸다.
_ITEM_COL_CANDIDATES = ("item_id", "itemid", "item")
_VALUE_COL_CANDIDATES = ("value", "et_value", "meas_value", "measure_value")


def _value_col_cands() -> set[str]:
    """value 열 후보 (lower) — 톱니바퀴 설정의 열 이름을 후보에 합친다."""
    cands = set(_VALUE_COL_CANDIDATES)
    cfg = _settings().get("value_col", "")
    if cfg:
        cands.add(cfg.lower())
    return cands


def _resolve_value_col(names) -> str:
    """df 컬럼에서 실제 value 열을 고른다 — 설정 열 이름 우선, 없으면 후보 순서.

    설정에 열 이름이 지정돼 있으면 그 열만 인정한다(잘못 지정 시 "" 반환 →
    호출부가 명확한 400 을 낸다). 자동 감지는 _VALUE_COL_CANDIDATES 순서.
    """
    lower = {str(c).lower(): str(c) for c in names}
    cfg = _settings().get("value_col", "")
    if cfg:
        return lower.get(cfg.lower(), "")
    for cand in _VALUE_COL_CANDIDATES:
        if cand in lower:
            return lower[cand]
    return ""


def _needed_columns(names: list[str]) -> list[str] | None:
    """다운스트림이 실제로 참조하는 컬럼만 고른다. 스키마를 못 알아보면 None(전체)."""
    from core.utils import WAFER_COLUMN_CANDIDATES
    value_cands = _value_col_cands()
    keys = {c.lower() for c in PIVOT_KEY_COLS}
    keep = set(keys)
    keep |= {c.lower() for c in PIVOT_META_COLS}
    keep |= set(_ITEM_COL_CANDIDATES) | value_cands
    keep |= {c.lower() for c in WAFER_COLUMN_CANDIDATES}
    keep |= {"total_site_cnt"}
    sel = [str(c) for c in names if str(c).lower() in keep]
    lower = {str(c).lower() for c in sel}
    if not (lower & set(_ITEM_COL_CANDIDATES)):
        return None
    if not (lower & value_cands):
        return None
    if not (lower & keys):
        return None
    return sel


def _pushdown_exprs(names: list[str], needed_ids: set[str] | None,
                    f: "Filters | None") -> list[pl.Expr]:
    """스캔 단계에서 바로 적용할 행 필터 — 전부 Utf8 캐스팅 후 기준.

    `_apply_filters` 와 같은 의미를 갖되, 컬럼이 없거나 값 형식이 틀리면 조용히
    건너뛴다. 정확한 판정(및 400 에러)은 collect 후 `_apply_filters` 가 다시 한다.
    """
    lower = {str(c).lower(): str(c) for c in names}
    out: list[pl.Expr] = []
    item_col = next((lower[c] for c in _ITEM_COL_CANDIDATES if c in lower), "")
    if needed_ids and item_col:
        out.append(pl.col(item_col).is_in(sorted(needed_ids)))
    if f is None:
        return out

    # 기간 — days 는 전역 tkout 최대값이 기준이라 파일 pruning 으로만 좁힌다.
    tk = lower.get("tkout_time")
    if tk and not f.days:
        d_lo, d_hi = _iso_date(f.date_from), _iso_date(f.date_to)
        if d_lo or d_hi:
            ts = pl.col(tk).cast(pl.Utf8, strict=False).str.to_datetime(strict=False)
            if d_lo:
                out.append(ts >= dt.datetime.combine(d_lo, dt.time.min))
            if d_hi:
                out.append(ts < dt.datetime.combine(d_hi, dt.time.min) + dt.timedelta(days=1))

    for spec, col, fallbacks in ((f.lot_filter, "root_lot_id", ("lot_id",)),
                                 (f.step_filter, "step_id", ()),
                                 (f.step_seq_filter, "step_seq", ()),
                                 (f.wafer_filter, "wafer_id", ())):
        vals = [v.strip().upper() for v in str(spec or "").split(",") if v.strip()]
        if not vals:
            continue
        use = next((lower[c] for c in (col, *fallbacks) if c in lower), "")
        if not use:
            continue
        base = pl.col(use).cast(pl.Utf8, strict=False).str.to_uppercase()
        expr = base.str.contains(vals[0], literal=True)
        for v in vals[1:]:
            expr = expr | base.str.contains(v, literal=True)
        out.append(expr)

    site_vals = [v.strip() for v in str(f.site_cnt_filter or "").split(",") if v.strip()]
    if site_vals and "total_site_cnt" in lower:
        try:
            nums = [int(float(v)) for v in site_vals]
        except ValueError:
            nums = []
        if nums:
            out.append(pl.col(lower["total_site_cnt"]).cast(pl.Int64, strict=False).is_in(nums))
    return out


def _expr_roots(expr) -> set[str]:
    """식이 참조하는 원본 컬럼 이름. 알 수 없으면 빈 집합(=보수적으로 취급)."""
    try:
        return {str(c) for c in expr.meta.root_names()}
    except Exception:
        return set()


def _split_pushdown_exprs(names: list[str], needed_ids: set[str] | None,
                          f: "Filters | None") -> tuple[list, list, list]:
    """`_pushdown_exprs` 를 적용 시점별로 나눈다 — (pre, post, probe).

    - **pre**: wafer 정규화 전에, 원본 dtype 그대로 걸 수 있는 식. lot/step/item/기간.
      스캔 직후에 걸어야 polars 가 parquet 리더까지 밀어넣고 row group 을 건너뛴다.
    - **post**: wafer_id 를 참조하는 식. `filter_valid_wafer_ids_lazy` 가 물리 wafer
      번호로 정규화한 **뒤** 걸어야 한다 (raw 1026 → wafer 1). 순서를 바꾸면 결과가
      달라지므로 pre 로 올리지 않는다.
    - **probe**: 파일 사전판정용 — pre 중에서 tkout_time 파싱을 빼고 식별자 비교만.
      기간은 이미 `_prune_files_by_date` 가 파일 단위로 처리했고, 전 행 datetime
      파싱은 '이 파일에 그 lot 이 있나' 를 묻는 값싼 질문과 어울리지 않는다.
    """
    lower = {str(c).lower(): str(c) for c in names}
    wafer_col = lower.get("wafer_id", "")
    time_col = lower.get("tkout_time", "")
    pre, post, probe = [], [], []
    for expr in _pushdown_exprs(names, needed_ids, f):
        roots = _expr_roots(expr)
        if wafer_col and wafer_col in roots:
            post.append(expr)
            continue
        pre.append(expr)
        if roots and not (time_col and time_col in roots):
            probe.append(expr)
    return pre, post, probe


def _scan_one_et_file(fp: Path, needed_ids: set[str] | None,
                      f: "Filters | None") -> pl.DataFrame | None:
    """한 파일을 lazy 스캔 — 열 projection + 행 필터를 읽는 단계에서 적용.

    순서가 성능의 전부다. 예전에는 wafer 정규화(전 행 정규식) → 전 열 Utf8 캐스팅 →
    필터 순이라, **가장 선택적인 조건이 맨 마지막**에 걸렸다. 그래서 A9999 랏 하나를
    찾는 조회도 파일 전체를 정규화·캐스팅한 뒤에야 버렸다. 지금은 식별자 필터를
    스캔 직후에 걸고, 살아남은 행에만 정규화·캐스팅을 한다.

    실패하면 None 을 돌려 호출부가 기존 eager 경로로 폴백한다.
    """
    from core.parquet_perf import collect_streaming, scan_parquet_relaxed
    from core.utils import filter_valid_wafer_ids_lazy
    try:
        lf = scan_parquet_relaxed(str(fp)) if fp.suffix != ".csv" else None
        if lf is None:
            from core.utils import scan_one_file
            lf = scan_one_file(fp)
            if lf is None:
                return None
            names = [str(c) for c in lf.collect_schema().names()]
            sel = _needed_columns(names)
            if sel:
                lf = lf.select(sel)
                names = sel
            lf = lf.with_columns([pl.col(c).cast(pl.Utf8, strict=False) for c in names])
            for expr in _pushdown_exprs(names, needed_ids, f):
                lf = lf.filter(expr)
            return collect_streaming(lf)
        names = [str(c) for c in lf.collect_schema().names()]
        sel = _needed_columns(names)
        if sel:
            lf = lf.select(sel)
            names = sel
        pre, post, _probe = _split_pushdown_exprs(names, needed_ids, f)
        for expr in pre:
            lf = lf.filter(expr)
        # wafer 정규화(raw 1026 → 1)는 wafer 필터보다 먼저여야 한다.
        lf = filter_valid_wafer_ids_lazy(lf, names)
        for expr in post:
            lf = lf.filter(expr)
        # concat 시 스키마가 어긋나지 않도록 기존 eager 경로와 같이 전부 Utf8.
        lf = lf.with_columns([pl.col(c).cast(pl.Utf8, strict=False) for c in names])
        return collect_streaming(lf)
    except Exception:
        logger.warning("reformatize: '%s' lazy 스캔 실패 — eager 폴백", fp.name, exc_info=True)
        return None


def _probe_enabled() -> bool:
    return str(os.environ.get("FLOW_REFORMATIZE_PROBE", "1")).strip().lower() not in {"0", "false", "no", "off"}


def _candidate_files(files: list[Path], needed_ids: set[str] | None,
                     f: "Filters | None", progress=None) -> list[Path]:
    """읽기 전에 "이 파일에 그 lot/step/item 이 있나" 만 값싸게 판정한다.

    기간을 안 준 조회(예: root_lot_id 하나만)는 `_prune_files_by_date` 가 아무것도
    못 걸러서 제품 전체(수백 일치)를 열게 된다. 하지만 parquet 은 columnar 라
    **필터 열 하나만 읽는 비용은 전체 읽기의 수십분의 1**이다. 먼저 그 열만 훑어
    후보 파일을 좁히면 실제로 여는 파일이 몇백 개에서 몇 개로 줄어든다.

    판정할 수 없으면(열 없음·스캔 실패) 그 파일은 **남긴다** — 사전판정 실수로
    데이터가 조용히 빠지는 쪽이 느린 것보다 훨씬 나쁘다.
    """
    if not files or f is None or not _probe_enabled():
        return files
    from core.parquet_perf import scan_parquet_relaxed
    report = progress or _noop_progress
    keep: list[Path] = []
    total = len(files)
    for i, fp in enumerate(files):
        if fp.suffix == ".csv":
            keep.append(fp)
            continue
        # 파일명까지 실어야 "지금 어디를 뒤지고 있나"가 보인다 — 수백 일치를
        # 훑는 조회에서 숫자만 올라가면 멈춘 것과 구분이 안 된다.
        report(f"대상 parquet 찾는 중: {fp.name} ({i + 1}/{total}개)", i, total)
        try:
            lf = scan_parquet_relaxed(str(fp))
            names = [str(c) for c in lf.collect_schema().names()]
            _pre, _post, probe = _split_pushdown_exprs(names, needed_ids, f)
            if not probe:
                keep.append(fp)
                continue
            cols: set[str] = set()
            for expr in probe:
                cols |= _expr_roots(expr)
            if not cols or not cols <= set(names):
                keep.append(fp)
                continue
            lf = lf.select([pl.col(c) for c in sorted(cols)])
            for expr in probe:
                lf = lf.filter(expr)
            if lf.head(1).collect().height:
                keep.append(fp)
        except Exception:
            logger.warning("reformatize: '%s' 사전판정 실패 — 그대로 읽는다", fp.name, exc_info=True)
            keep.append(fp)
    if len(keep) != total:
        logger.info("reformatize: 대상 파일 %d/%d개로 좁힘 (filter=[%s])",
                    len(keep), total, _filter_desc(f) or "없음")
    return keep


def _noop_progress(phase: str, done: int | None = None, total: int | None = None) -> None:
    """진행 보고가 없는 호출(스크립트·테스트)용 기본값."""


# ── 조회(/run) 진행 상황 ─────────────────────────────────────────────
# 다운로드는 대기열 job 이라 화면이 `/download/status` 를 폴링해 "지금 무슨
# 단계" 를 보여준다. 그런데 **조회도 오래 걸리는 구간은 똑같다** — 수백 개
# parquet 중 어디를 뒤지고 있는지. /run 은 동기 POST 라 폴링할 job 이 없어서
# 여태 "ET index 계산 중" 한 줄만 돌았고, 사용자 입장에서는 멈춘 것과 구분이
# 안 됐다. 클라이언트가 만든 토큰을 키로 진행 상황만 따로 남기고 화면이 그걸
# 폴링한다 (계산 결과는 그대로 /run 응답으로 돌아간다).
_RUN_PROGRESS_TTL_SEC = 180.0
_RUN_PROGRESS_MAX = 64
_RUN_PROGRESS: dict[str, tuple[float, dict]] = {}
_RUN_PROGRESS_LOCK = threading.Lock()

from core import cache_sweeper as _cache_sweeper

_cache_sweeper.register_ttl_dict("reformatize._RUN_PROGRESS", _RUN_PROGRESS,
                                 _RUN_PROGRESS_TTL_SEC, lock=_RUN_PROGRESS_LOCK,
                                 clock=time.monotonic)


def _run_progress_token(raw: str) -> str:
    """클라이언트 토큰 정규화 — 키가 되므로 길이·문자를 제한한다."""
    text = str(raw or "").strip()[:64]
    return "".join(ch for ch in text if ch.isalnum() or ch in "-_")


def _run_progress_reporter(token: str):
    """`/run` 계산에 넘길 진행 보고 콜백. 토큰이 없으면 아무것도 하지 않는다."""
    if not token:
        return _noop_progress

    def report(phase: str, done: int | None = None, total: int | None = None) -> None:
        entry = {"phase": str(phase or ""), "done": done, "total": total}
        with _RUN_PROGRESS_LOCK:
            if token not in _RUN_PROGRESS and len(_RUN_PROGRESS) >= _RUN_PROGRESS_MAX:
                # 만료 청소는 sweeper 가 하지만, 폭주 시 상한을 즉시 지킨다.
                oldest = min(_RUN_PROGRESS, key=lambda k: _RUN_PROGRESS[k][0])
                _RUN_PROGRESS.pop(oldest, None)
            _RUN_PROGRESS[token] = (time.monotonic(), entry)

    return report


def _run_progress_clear(token: str) -> None:
    if not token:
        return
    with _RUN_PROGRESS_LOCK:
        _RUN_PROGRESS.pop(token, None)


def _read_et_files(files: list[Path],
                   needed_ids: set[str] | None = None,
                   f: "Filters | None" = None,
                   progress=None) -> pl.DataFrame:
    """파일 목록을 읽어 read_source 와 동일한 정규화를 적용한 long df.

    파일마다 **열 projection + item_id/행 필터를 읽는 단계에서** 적용하므로
    concat 전에 이미 필요한 조각만 남는다. 스캔이 실패한 파일만 기존 eager
    경로(read_one_file)로 폴백한다.

    ``progress`` 는 다운로드 대기열이 넘기는 진행 보고 콜백 — 파일 단위로
    "3/12 파일" 을 화면에 흘려보낸다(가장 오래 걸리는 구간이라 여기서 보고).
    """
    from core.utils import cast_all_str, filter_valid_wafer_ids_df, normalize_source_df, read_one_file
    report = progress or _noop_progress
    dfs = []
    total_files = len(files)
    for i, fp in enumerate(files):
        report(f"ET parquet 읽는 중: {fp.name} ({i + 1}/{total_files}개)", i, total_files)
        d = _scan_one_et_file(fp, needed_ids, f)
        if d is None:
            d = read_one_file(fp)
            if d is not None and d.height > 0:
                d = cast_all_str(d)
                sel = _needed_columns(list(d.columns))
                if sel:
                    d = d.select(sel)
                if needed_ids and "item_id" in d.columns:
                    d = d.filter(pl.col("item_id").is_in(list(needed_ids)))
        if d is not None and d.height > 0:
            dfs.append(d)
    report(f"ET parquet 읽기 완료 ({total_files}개 파일)", total_files, total_files)
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


def _ids_key(needed_ids: set[str] | None) -> str:
    if not needed_ids:
        return ""
    blob = ",".join(sorted(needed_ids)).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()[:16]


def _max_scan_bytes() -> int:
    """한 번의 조회에서 열어도 되는 ET parquet 총량 (기본 4GB)."""
    try:
        mb = float(os.environ.get("FLOW_REFORMATIZE_MAX_SCAN_MB", "") or 4096.0)
    except Exception:
        mb = 4096.0
    return int(max(64.0, mb) * 1024 * 1024)


def _load_raw(product: str, product_sig: tuple, full_sig: tuple,
              needed_ids: set[str] | None = None,
              f: "Filters | None" = None,
              row_budget: int | None = None,
              progress=None) -> tuple[pl.DataFrame, str]:
    """조회 조건까지 반영된 raw(long) ET df — 파일 시그니처가 같으면 캐시 재사용.

    ``product_sig`` 는 데이터 파일만의 (path, mtime, size) 튜플 — 파일 목록용.
    ``full_sig`` 는 데이터+CSV 시그니처 — 캐시 무효화 키.
    ``needed_ids`` 는 vehicle 테이블이 요구하는 ITEMID, ``f`` 는 사용자 필터.
    ``row_budget`` 이 있으면(조회 화면 경량 모드) **최신 파일부터** 그 행 수를
    채울 만큼만 읽고, 못 읽은 과거 파일은 안내 문구로 알린다 — 에러 대신
    부분 결과를 준다. None(다운로드)이면 기존처럼 전량 읽되 스캔 상한 초과는 400.

    반환: (df, notice) — notice 는 부분 읽기 안내("" = 전량 읽음).

    핵심: 기간 필터로 **파일을 먼저 버리고**, 남은 파일도 필요한 열/행만
    읽는다. 제품 전체를 메모리에 올리지 않는다.
    """
    report = progress or _noop_progress
    # value_col 설정은 열 projection(_needed_columns)에 영향 → 캐시 키에 포함.
    key = (product, _filters_key(f) if f is not None else (), _ids_key(needed_ids),
           int(row_budget or 0), _settings().get("value_col", ""))
    with _CACHE_LOCK:
        hit = _RAW_CACHE.get(key)
        if hit and hit[0] == full_sig:
            report("캐시된 ET parquet 결과 불러오는 중")
            return hit[1], hit[2]

    size_by_path = {p: s for p, _mtime, s in product_sig}
    all_files = [Path(p) for p, _mtime, _size in product_sig]
    files = _prune_files_by_date(all_files, f) if f is not None else all_files
    if f is not None:
        files = _prune_files_by_filters(files, f)
    if not files:
        raise HTTPException(400, "선택한 기간에 해당하는 ET 데이터 파일이 없습니다 — "
                                 "기간(tkout_time)을 넓혀 주세요")
    # 기간을 안 준 조회(root_lot_id 하나만 등)는 위 pruning 이 아무것도 못 거른다.
    # 필터 열만 훑어 후보 파일을 좁힌다 — 스캔 예산 판정도 이 결과 위에서 한다.
    dated_files = files
    files = _candidate_files(files, needed_ids, f, progress=progress)
    if not files:
        raise HTTPException(
            404,
            f"조건에 맞는 ET 데이터가 없습니다 (파일 {len(dated_files):,}개 확인, "
            f"filter=[{_filter_desc(f) if f is not None else ''}]). "
            "lot/step/wafer 값을 확인해 주세요.",
        )

    scan_bytes = sum(size_by_path.get(str(p), 0) for p in files)
    budget = _max_scan_bytes()
    notice = ""
    if row_budget is not None:
        # 조회 경량 모드 — 최신 파일부터 row_budget 행/스캔 상한 안에서만 읽는다.
        # 파일 날짜를 모르는 파일은 뒤로 미뤄, 예산이 남을 때만 읽는다.
        dated = [(fp, _file_date(fp)) for fp in files]
        known = sorted((t for t in dated if t[1] is not None), key=lambda t: t[1], reverse=True)
        ordered = [fp for fp, _ in known] + [fp for fp, d in dated if d is None]
        dfs, rows, read_bytes, skipped = [], 0, 0, 0
        for i, fp in enumerate(ordered):
            fsize = size_by_path.get(str(fp), 0)
            if dfs and (rows >= row_budget or read_bytes + fsize > budget):
                skipped = len(ordered) - i
                break
            report(f"ET parquet 읽는 중: {fp.name} ({i + 1}/{len(ordered)}개)", i, len(ordered))
            try:
                d = _read_et_files([fp], needed_ids=needed_ids, f=f)
            except HTTPException:
                continue                     # 한 파일이 못 읽혀도 조회는 계속
            read_bytes += fsize
            if d.height:
                dfs.append(d)
                rows += d.height
        if not dfs:
            raise HTTPException(404, "읽을 수 있는 ET 데이터 파일이 없습니다")
        df = dfs[0] if len(dfs) == 1 else pl.concat(dfs, how="diagonal")
        if skipped:
            notice = (f"데이터가 커서 최신 파일 {len(ordered) - skipped}/{len(ordered)}개만 "
                      f"읽었습니다 (과거 {skipped}개 생략)")
        logger.info("reformatize raw [%s]: 경량 조회 파일 %d/%d개(%dMB), item %d개, filter=[%s]",
                    product, len(ordered) - skipped, len(ordered), read_bytes >> 20,
                    len(needed_ids or ()), (_filter_desc(f) if f is not None else "") or "없음")
    else:
        if scan_bytes > budget:
            dates = sorted(d for d in (_file_date(p) for p in all_files) if d)
            span = f" (데이터 보유 기간 {dates[0]} ~ {dates[-1]})" if dates else ""
            raise HTTPException(
                400,
                f"조회 조건으로 열어야 할 ET 원본이 {scan_bytes >> 20:,}MB / {len(files):,}개 파일로 "
                f"상한 {budget >> 20:,}MB 를 넘습니다{span}. 기간(최근 N일 또는 시작/종료일)을 "
                "좁혀 주세요.",
            )
        logger.info("reformatize raw [%s]: 파일 %d/%d개(%dMB), item %d개, filter=[%s]",
                    product, len(files), len(all_files), scan_bytes >> 20,
                    len(needed_ids or ()), (_filter_desc(f) if f is not None else "") or "없음")
        df = _read_et_files(files, needed_ids=needed_ids, f=f, progress=progress)
    est = _df_est_bytes(df)
    with _CACHE_LOCK:
        _RAW_CACHE.pop(key, None)
        if _evict_cache_locked(_RAW_CACHE, _RAW_CACHE_MAX, _raw_cache_max_bytes(), est):
            _RAW_CACHE[key] = (full_sig, df, notice, est)
    return df, notice


def _trim_recent(df: pl.DataFrame, max_rows: int) -> pl.DataFrame:
    """tkout_time 최신 순으로 max_rows 행만 남긴다 — 조회 화면 경량화용."""
    if "tkout_time" not in df.columns:
        return df.head(max_rows)
    dtype = df.schema["tkout_time"]
    ts = (pl.col("tkout_time").str.to_datetime(strict=False) if dtype == pl.Utf8
          else pl.col("tkout_time").cast(pl.Datetime("us"), strict=False))
    return (df.with_columns(ts.alias("__ts"))
              .sort("__ts", descending=True, nulls_last=True)
              .head(max_rows)
              .drop("__ts"))


def _compute(product: str, f: Filters,
             selected_items: list[str] | None = None,
             max_mb: int | None = None,
             auto_trim: bool = False,
             progress=None,
             max_raw_rows: int | None = None,
             ) -> tuple[pl.DataFrame, list[str], list[str], str, list[dict], int, str]:
    """제품의 reformatize 결과 (full wide, 출력 컬럼, 규칙 에러, csv 이름, 규칙 테이블,
    필터된 raw 행수, 안내 문구).

    핵심 흐름:
      1) vehicle 테이블 파싱 → 필요한 ITEMID 집합(needed_ids) 결정
      2) raw ET 데이터 로드 — needed_ids 로 **파일별 조기 필터** (OOM 방지)
      3) 사용자 필터(기간·lot 등) 적용
      4) reformatize (pivot + REAL/ADDP 계산)
      5) 용량(MB) 단위 한도 검증 (_ensure_size_within_limit)

    ``selected_items`` 가 주어지면 ADDP 의존성을 재귀 해소해 필요한 ITEMID 만
    raw 에서 필터+pivot 한다. None 이면 테이블 전체 항목 기준.

    ``progress`` 는 다운로드 대기열 작업이 넘기는 진행 보고 콜백(없으면 무시).
    캐시 HIT 이면 아무 단계도 보고되지 않고 곧바로 끝난다.
    """
    report = progress or _noop_progress
    report("규칙 CSV 확인 중")
    csv_fp = _find_csv(product)
    if csv_fp is None:
        raise HTTPException(400, f"'{product}' 에 매칭되는 vehicle reformatter CSV 가 없습니다 "
                                 f"(DB루트/reformatter 또는 {VEHICLE_DIR} 안에 "
                                 f"<vehicle>_reformatter.csv 를 두세요)")

    # ── 1) 시그니처·캐시 키 ──
    product_sig = _product_sig(product)          # 데이터 파일만
    csv_st = csv_fp.stat()
    full_sig = product_sig + ((str(csv_fp), csv_st.st_mtime, csv_st.st_size),)
    sel_key = tuple(sorted(set(selected_items))) if selected_items else ()
    sett = _settings()
    effective_max_mb = max_mb if max_mb is not None else sett.get("max_download_mb", 500)
    key = (product, _filters_key(f), sel_key, bool(auto_trim), int(effective_max_mb or 0),
           sett.get("value_col", ""), bool(sett.get("scale_applied")))
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and hit[0] == full_sig:
            report("캐시된 Index 계산 결과 불러오는 중")
            _ensure_size_within_limit(hit[1], effective_max_mb, context="조회" if auto_trim else "조회/다운로드")
            return hit[1], hit[2], hit[3], hit[4], hit[5], hit[6], hit[7]

    # ── 2) vehicle 테이블 → 필요 ITEMID 결정 (raw 로딩 전!) ──
    table = load_vehicle_table(csv_fp)
    if not table:
        raise HTTPException(400, f"{csv_fp.name}: 유효한 REAL/ADDP 행이 없습니다")
    if selected_items:
        _, needed_ids = resolve_needed_items(table, selected_items)
    else:
        all_aliases = [r["alias"] for r in table]
        _, needed_ids = resolve_needed_items(table, all_aliases)

    # ── 3) raw 로딩 — 기간으로 파일을 먼저 버리고, 남은 파일에서도
    #      필요한 열 / needed_ids / 행 필터만 읽는다 (OOM·타임아웃 방지) ──
    df, notice = _load_raw(product, product_sig, full_sig, needed_ids=needed_ids, f=f,
                           row_budget=None, progress=progress)
    if df.height == 0:
        raise HTTPException(400, f"'{product}' ET 데이터가 비어 있습니다")
    # pushdown 은 근사(컬럼 없음·형식 오류는 건너뜀)이므로 정확한 판정을 다시 한다.
    # 이미 걸러진 행에 같은 조건을 적용하는 것이라 결과는 동일하고 비용은 작다.
    df = _apply_filters(df, f)
    if df.height == 0:
        raise HTTPException(400, "필터 조건에 맞는 ET 데이터가 없습니다 — 기간·lot 등 필터를 조정하세요")
    raw_rows = df.height

    # 원본 메모리 용량 가드 (최종 허용 용량의 2배 초과 시 pivot 전 차단)
    _ensure_size_within_limit(df, (effective_max_mb or 500) * 2, context="ET 원본 데이터")

    # 크기 가드 — 필터 후에도 너무 크면 pivot 전에 차단
    _MAX_RAW_FOR_PIVOT = 20_000_000
    if raw_rows > _MAX_RAW_FOR_PIVOT:
        raise HTTPException(
            400,
            f"용량초과: 필터 후 raw 데이터가 {raw_rows:,}행으로 너무 큽니다 "
            f"(상한 {_MAX_RAW_FOR_PIVOT:,}). 기간·lot 등 필터를 더 좁혀 주세요.",
        )
    logger.info("reformatize [%s]: raw %d행, items=%s, filters=%s",
                product, raw_rows,
                f"{len(selected_items)}개" if selected_items else "전체",
                _filter_desc(f) or "없음")
    calc_names = selected_items or [r["alias"] for r in table]
    calc_preview = ", ".join(calc_names[:4]) + (" …" if len(calc_names) > 4 else "")
    report(f"Index 계산 중: {calc_preview} ({len(calc_names)}개, raw {raw_rows:,}행 pivot)")
    # 실제 열 이름 해석 — 실 DB 는 value 가 아니라 et_value 인 경우가 많다.
    # 톱니바퀴 설정(value_col)이 있으면 그 열만 인정한다.
    item_col = next((str(c) for c in df.columns
                     if str(c).lower() in _ITEM_COL_CANDIDATES), "item_id")
    value_col = _resolve_value_col(df.columns)
    if not value_col:
        cfg = sett.get("value_col", "")
        hint = (f"설정된 value 열 '{cfg}' 이" if cfg
                else f"value 열({'/'.join(_VALUE_COL_CANDIDATES)})이")
        raise HTTPException(400, f"{hint} ET 데이터에 없습니다 — "
                                 f"톱니바퀴 설정의 value 열 이름을 확인하세요 "
                                 f"(현재 열: {', '.join(map(str, df.columns))})")
    try:
        wide, out_cols, errors = reformatize(
            df, table,
            item_col=item_col, value_col=value_col,
            selected_aliases=selected_items if selected_items else None,
            skip_real_scale=bool(sett.get("scale_applied")),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    report(f"Index 계산 완료 ({wide.height:,}행)")
    _ensure_size_within_limit(wide, effective_max_mb, context="조회" if auto_trim else "조회/다운로드")
    est = _df_est_bytes(wide)
    with _CACHE_LOCK:
        _CACHE.pop(key, None)
        if _evict_cache_locked(_CACHE, _CACHE_MAX, _cache_max_bytes(), est):
            _CACHE[key] = (full_sig, wide, out_cols, errors, csv_fp.name, table, raw_rows, notice, est)
    return wide, out_cols, errors, csv_fp.name, table, raw_rows, notice


# ── 조회/다운로드 필터 ───────────────────────────────────────────────
# 필터는 raw(long) ET 데이터에 적용된 뒤 reformatize 로 넘어간다 —
# total_site_cnt 처럼 raw 에만 있는 컬럼도 그대로 필터 가능하다.
class Filters(BaseModel):
    lot_filter: str = ""        # root_lot_id 포함 검색 (쉼표 = OR)
    step_filter: str = ""       # step_id 포함 검색 (쉼표 = OR)
    step_seq_filter: str = ""   # step_seq 포함 검색 (쉼표 = OR)
    wafer_filter: str = ""      # wafer_id 포함 검색 (쉼표 = OR)
    site_cnt_filter: str = ""   # total_site_cnt 정확 일치 (쉼표 = OR)
    point_cnt_filter: str = ""  # 계산된 PGM(pt) 포인트 수 정확 일치 (쉼표 = OR)
    days: int = 0               # tkout_time 최신값 기준 최근 N일 — 지정 시 date_from/to 무시
    date_from: str = ""         # YYYY-MM-DD, tkout_time ≥ 해당일 00:00
    date_to: str = ""           # YYYY-MM-DD, tkout_time < 익일 00:00 (해당일 포함)


def _filters_key(f: Filters) -> tuple:
    """raw/wide 계산 캐시 키. PGM point 필터는 wide 계산 뒤 적용하므로 제외."""
    return (f.lot_filter.strip().upper(), f.step_filter.strip().upper(),
            f.step_seq_filter.strip().upper(),
            f.wafer_filter.strip().upper(), f.site_cnt_filter.strip(),
            max(0, int(f.days or 0)), f.date_from.strip(), f.date_to.strip())


def _has_filter(f: Filters) -> bool:
    return bool(f.lot_filter.strip() or f.step_filter.strip() or f.step_seq_filter.strip()
                or f.wafer_filter.strip()
                or f.site_cnt_filter.strip() or f.point_cnt_filter.strip() or f.days > 0
                or f.date_from.strip() or f.date_to.strip())


def _filter_desc(f: Filters) -> str:
    parts = []
    if f.days > 0:
        parts.append(f"days={f.days}")
    elif f.date_from.strip() or f.date_to.strip():
        parts.append(f"tkout={f.date_from.strip() or '…'}~{f.date_to.strip() or '…'}")
    for k, v in (("lot", f.lot_filter), ("step", f.step_filter),
                 ("step_seq", f.step_seq_filter),
                 ("wafer", f.wafer_filter), ("site_cnt", f.site_cnt_filter),
                 ("pgm_pt", f.point_cnt_filter)):
        if v.strip():
            parts.append(f"{k}={v.strip()}")
    return ", ".join(parts)


def _download_name(product: str, f: Filters, username: str, *, unique_id: str = "",
                   agg: str = "", suffix: str = "") -> str:
    context = [product, _filter_desc(f) or "filtered"]
    if agg:
        context.append(f"agg-{agg}")
    if suffix:
        context.append(suffix)
    return download_filename(
        "ET-download", username=username, unique_id=unique_id,
        context=context, extension="csv",
    )


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


def _exact_count_values(spec: str, label: str) -> list[int]:
    vals = [v.strip() for v in str(spec or "").split(",") if v.strip()]
    if not vals:
        return []
    nums: list[int] = []
    for value in vals:
        try:
            numeric = float(value)
            integer = int(numeric)
        except (TypeError, ValueError, OverflowError):
            raise HTTPException(400, f"{label} 필터는 정수만 허용됩니다: {spec}")
        if numeric != integer or integer < 0:
            raise HTTPException(400, f"{label} 필터는 0 이상의 정수만 허용됩니다: {spec}")
        nums.append(integer)
    return nums


def _site_cnt_filter(df: pl.DataFrame, spec: str) -> pl.DataFrame:
    nums = _exact_count_values(spec, "total_site_cnt")
    if not nums:
        return df
    if "total_site_cnt" not in df.columns:
        raise HTTPException(400, "total_site_cnt 컬럼이 데이터에 없어 필터를 적용할 수 없습니다")
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
    df = _contains_any(df, "step_seq", f.step_seq_filter)
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
        csv_fp = _find_csv(name)
        out.append({"product": name, "vehicle_csv": csv_fp.name if csv_fp else ""})
    return {"products": out, "vehicle_dir": str(VEHICLE_DIR)}


@router.get("/items")
def list_items(product: str = Query(...), user=Depends(current_user)):
    """제품 vehicle CSV 의 REAL/ADDP 항목 목록 — index 선택 UI 용.

    REAL 은 raw ITEMID·abs·scale factor, ADDP 는 ADDP Form 과 참조 컬럼을 함께 반환.
    데이터를 읽지 않고 규칙 CSV 만 파싱하므로 가볍다.

    행 순서는 CSV 파일 순서 그대로 유지한다 (재정렬 금지 — 유저는 auto report
    팀이 관리하는 원본 순서로 본다). 관리자가 비공개로 정한 항목은 일반 유저
    목록에서 제외하고, 관리자에게는 hidden 플래그로 표시한다 (기본 전부 공개).
    """
    csv_fp = _find_csv(product)
    if csv_fp is None:
        raise HTTPException(400, f"'{product}' 에 매칭되는 vehicle reformatter CSV 가 없습니다")
    table = load_vehicle_table(csv_fp)
    hidden = _hidden_aliases(csv_fp.name)
    is_admin = user.get("role") == "admin"
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
        "hidden": r["alias"] in hidden,
    } for r in table if is_admin or r["alias"] not in hidden]
    return {"product": product, "vehicle_csv": csv_fp.name, "items": items}


class VisibilityReq(BaseModel):
    product: str
    hidden: list[str] = []


@router.post("/visibility")
def visibility_save(req: VisibilityReq, admin=Depends(require_admin)):
    """관리자: reformatter(vehicle CSV)별 유저 비공개 항목 저장 — 기본 전부 공개.

    CSV 에 실존하는 alias 만 저장하고, 빈 목록이면 항목 자체를 지워 기본
    (전부 공개)으로 되돌린다."""
    csv_fp = _find_csv(req.product)
    if csv_fp is None:
        raise HTTPException(400, f"'{req.product}' 에 매칭되는 vehicle reformatter CSV 가 없습니다")
    known = {r["alias"] for r in load_vehicle_table(csv_fp)}
    hidden = sorted({str(a).strip() for a in req.hidden if str(a).strip()} & known)
    raw = load_json(VISIBILITY_FILE, {}) or {}
    if hidden:
        raw[csv_fp.name] = hidden
    else:
        raw.pop(csv_fp.name, None)
    save_json(VISIBILITY_FILE, raw)
    from core.audit import record_user as _audit_user
    _audit_user(admin.get("username", ""), "reformatize:visibility",
                detail=f"vehicle={csv_fp.name} hidden={','.join(hidden) or '없음'}",
                tab="reformatize")
    return {"vehicle_csv": csv_fp.name, "hidden": hidden}


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
    # Only suffix a PGM when this wafer really has duplicate packages.  The old
    # global step-family window let duplicates on another wafer force ``_1``.
    w = w.with_columns(pl.col("_dup").max().over(dup_keys).alias("_dupmax"))
    label = pl.col("step_seq").cast(pl.Utf8) + "(" + pl.col("_pt").cast(pl.Utf8) + "pt)"
    return w.with_columns(
        pl.when(pl.col("_dupmax") > 1)
        .then(label + "_" + pl.col("_dup").cast(pl.Utf8))
        .otherwise(label)
        .alias("pgm")
    ).drop(["_pt", "_dup", "_dupmax"])


def _aggregation_frame(wide: pl.DataFrame) -> tuple[pl.DataFrame, list[str]]:
    """Return a frame with the exact root-lot/wafer/step/PGM grain.

    ``lot_id`` is accepted only as the legacy root-lot fallback.  Missing grain
    columns must fail loudly; aggregating across wafers or steps produces a
    plausible but incorrect P10, which is worse than a visible input error.
    """
    w = wide
    if "root_lot_id" not in w.columns and "lot_id" in w.columns:
        w = w.with_columns(pl.col("lot_id").alias("root_lot_id"))
    w = _ensure_pgm(w)
    keys = ["root_lot_id", "wafer_id", "step_id", "pgm"]
    missing = [key for key in keys if key not in w.columns]
    if missing:
        raise HTTPException(
            400,
            "집계 키(root_lot_id/wafer_id/step_id/PGM)가 부족합니다: " + ", ".join(missing),
        )
    has_pgm = w.select(
        pl.col("pgm").cast(pl.Utf8, strict=False).fill_null("").str.strip_chars().ne("").any()
    ).item()
    if not has_pgm:
        raise HTTPException(400, "PGM 컬럼이 비어 있고 step_seq도 없어 PGM별 집계를 만들 수 없습니다")
    return w, keys


def _point_cnt_filter(wide: pl.DataFrame, spec: str) -> pl.DataFrame:
    """Filter whole PGM packages by the displayed ``PGM(npt)`` point count."""
    nums = _exact_count_values(spec, "PGM point 수")
    if not nums:
        return wide
    w, keys = _aggregation_frame(wide)
    marker = "__flow_pgm_point_count"
    return (
        w.with_columns(pl.len().over(keys).alias(marker))
        .filter(pl.col(marker).is_in(nums))
        .drop(marker)
    )


def _aggregate(wide: pl.DataFrame, alias_cols: list[str], method: str) -> pl.DataFrame:
    """wide(shot 단위) → (root_lot_id, wafer_id, step_id, pgm) 그룹 집계."""
    method = str(method or "").strip().lower()
    if method not in _AGG_METHODS:
        raise HTTPException(400, f"지원하지 않는 집계 방식 '{method}' — 허용: {', '.join(_AGG_METHODS)}")
    w, keys = _aggregation_frame(wide)
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


def _resolve_output_cols(
    out_cols: list[str],
    table: list[dict],
    wanted: list[str],
    wide_full: pl.DataFrame,
    *,
    include_raw: bool = True,
) -> list[str]:
    """Return fixed, requested, dependency, then referenced raw columns.

    REAL alias 는 `ITEMID × scale factor` 라서, alias 열이 이미 나가는데 raw
    ITEMID 열까지 실으면 같은 값을 배율만 빼고 한 번 더 싣는 중복이다. scale 이
    1.0 인 항목에서는 아예 같은 숫자가 두 열로 나온다. 그래서 **출력에 REAL
    alias 가 있는 ITEMID 의 raw 열은 내보내지 않는다.**

    alias 로 노출되지 않는 ITEMID(관리자가 비공개 처리했거나, ADDP 수식이 REAL
    행 없이 ITEMID 를 직접 참조하는 경우)는 그대로 남긴다 — 그걸 빼면 계산
    근거가 통째로 사라진다.
    """
    aliases = [r["alias"] for r in table]
    roots = list(dict.fromkeys(wanted)) if wanted else aliases
    resolved_rows, needed_itemids = resolve_needed_items(table, roots)
    resolved_aliases = [r["alias"] for r in resolved_rows]

    # UI sends the Set insertion order. Preserve it instead of reverting to
    # vehicle-table or alphabetical order.
    selected_aliases = [a for a in roots if a in wide_full.columns]
    dependency_aliases = [
        a for a in resolved_aliases
        if a not in selected_aliases and a in wide_full.columns
    ]
    derived_by_alias = {
        alias: [
            f"{alias}_{suffix}" for suffix in _MA_WINDOW_SUFFIXES
            if f"{alias}_{suffix}" in wide_full.columns
        ]
        for alias in selected_aliases + dependency_aliases
    }

    raw_cols: list[str] = []
    if include_raw:
        row_by_alias = {r["alias"]: r for r in resolved_rows}
        # 이미 scale 이 곱해진 REAL alias 로 나가는 ITEMID — raw 열을 생략한다.
        scaled_itemids = {
            str(row_by_alias[alias].get("itemid", "")).strip()
            for alias in selected_aliases + dependency_aliases
            if str(row_by_alias.get(alias, {}).get("category", "")).strip().lower() == "real"
        }
        scaled_itemids.discard("")
        for itemid in sorted(needed_itemids):
            if itemid in scaled_itemids:
                continue
            if itemid in wide_full.columns and itemid not in raw_cols:
                raw_cols.append(itemid)

    fixed = set(PIVOT_KEY_COLS) | set(PIVOT_META_COLS)
    cols = [c for c in out_cols if c in fixed and c in wide_full.columns]
    for alias in selected_aliases:
        cols.append(alias)
        cols.extend(derived_by_alias.get(alias, []))
    for alias in dependency_aliases:
        cols.append(alias)
        cols.extend(derived_by_alias.get(alias, []))

    # Keep future custom calculated columns before the raw source values.
    known = set(cols) | set(raw_cols)
    cols.extend(c for c in out_cols if c in wide_full.columns and c not in known)
    cols.extend(raw_cols)
    return list(dict.fromkeys(cols))


@router.get("/settings")
def settings_get(_user=Depends(current_user)):
    return _settings()


class SettingsReq(BaseModel):
    page_rows: int = DEFAULT_SETTINGS["page_rows"]
    max_download_mb: int = DEFAULT_SETTINGS["max_download_mb"]
    max_download_rows: int | None = None
    value_col: str = ""          # "" = 자동 감지 (value/et_value/...)
    scale_applied: bool = False  # 원본 값에 REAL scale 이 이미 곱해진 소스
    share_base_url: str | None = None


@router.post("/settings")
def settings_save(req: SettingsReq, user=Depends(current_user)):
    share_base_url = _settings()["share_base_url"] if req.share_base_url is None else req.share_base_url.strip()
    if share_base_url:
        try:
            from core.mail import validate_share_base_url
            share_base_url = validate_share_base_url(share_base_url)
        except ValueError:
            raise HTTPException(400, "공유 기본 주소는 쿼리·인증정보 없이 http:// 또는 https:// 주소로 입력하세요.")
    if req.share_base_url is not None:
        try:
            from core.mail import set_share_base_url
            set_share_base_url(share_base_url)
        except Exception as e:
            logger.warning("Failed to sync share_base_url: %s", e)
    mb_val = req.max_download_mb
    data = {
        "page_rows": max(10, min(int(req.page_rows), PAGE_ROWS_MAX)),
        "max_download_mb": max(10, min(int(mb_val), DOWNLOAD_MB_MAX)),
        "max_download_rows": max(100, min(int(req.max_download_rows or (mb_val * 1000)), DOWNLOAD_ROWS_MAX)),
        "value_col": str(req.value_col or "").strip()[:64],
        "scale_applied": bool(req.scale_applied),
        "share_base_url": share_base_url,
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
    # 화면이 만든 1회용 토큰 — /run/progress 로 진행 상황을 폴링할 때 쓴다.
    progress_token: str = ""
    history_id: str = ""


@router.get("/run/progress")
def run_progress(token: str = Query(""), _user=Depends(current_user)):
    """조회 진행 상황 — 화면 로딩창의 "지금 무슨 파일" 문구가 여기서 나온다.

    토큰은 화면이 만든 1회용 값이고 계산이 끝나면 지워진다. 아직/이미 없는
    토큰은 404 가 아니라 빈 phase 로 답한다 — 폴링이 계산보다 먼저 도착하는
    것은 정상이고, 그때마다 콘솔에 오류가 찍히면 진짜 문제를 가린다.
    """
    entry = None
    key = _run_progress_token(token)
    if key:
        with _RUN_PROGRESS_LOCK:
            hit = _RUN_PROGRESS.get(key)
            entry = dict(hit[1]) if hit else None
    return {"ok": True, "active": entry is not None, **(entry or {"phase": "", "done": None, "total": None})}


@router.post("/run")
def run(req: RunReq, user=Depends(current_user)):
    t0 = time.monotonic()
    cfg = _settings()
    # 관리자가 비공개로 정한 항목은 유저 요청에서 서버측에서도 걸러낸다 —
    # UI 를 우회한 직접 API 호출로도 비공개 index 를 뽑을 수 없게.
    is_admin = user.get("role") == "admin"
    hidden = set() if is_admin else _hidden_aliases((_find_csv(req.product) or Path("")).name)
    req_items = [a for a in (req.items or []) if a not in hidden]
    if not is_admin and not (req.items or []) and hidden:
        req_items = _visible_aliases_for(req.product, hidden)
        if not req_items:
            raise HTTPException(403, "관리자가 현재 제품의 ET 다운로드 항목 전체를 비공개로 설정했습니다")
    if (req.items or []) and not req_items:
        raise HTTPException(403, "선택한 항목은 관리자가 비공개로 설정했습니다")
    token = _run_progress_token(req.progress_token)
    report = _run_progress_reporter(token)
    # 진행 문구는 계산이 끝난 뒤에도 남은 후처리(컬럼 결정·집계·직렬화) 동안
    # 유효해야 한다. 그래서 토큰 정리는 응답 직전 한 곳에서만 한다.
    try:
        return _run_compute(req, user, cfg, hidden, req_items, report, t0)
    finally:
        _run_progress_clear(token)


def _run_compute(req: "RunReq", user: dict, cfg: dict, hidden: set,
                 req_items: list[str], report, t0: float) -> dict:
    """`/run` 본체 — 진행 보고 토큰 정리는 호출자가 맡는다."""
    try:
        wide_full, out_cols, errors, vehicle_csv, table, _raw_rows, notice = _compute(
            req.product, req, selected_items=req_items or None,
            max_mb=cfg.get("max_download_mb", 500), auto_trim=True, progress=report)
    except HTTPException as exc:
        try:
            _save_or_increment_reformatize_history(
                req.product,
                items=req_items,
                filters=req,
                agg=req.agg,
                username=str(user.get("username") or ""),
                history_id=req.history_id,
                status="error",
                error_message=str(exc.detail or ""),
                elapsed_ms=round((time.monotonic() - t0) * 1000),
            )
        except Exception as h_err:
            logger.warning("Failed to record failed reformatize history: %s", h_err)
        raise
    except Exception as e:
        logger.exception("reformatize run failed: product=%s", req.product)
        err_msg = f"계산 실패: {e}"
        try:
            _save_or_increment_reformatize_history(
                req.product,
                items=req_items,
                filters=req,
                agg=req.agg,
                username=str(user.get("username") or ""),
                history_id=req.history_id,
                status="error",
                error_message=err_msg,
                elapsed_ms=round((time.monotonic() - t0) * 1000),
            )
        except Exception as h_err:
            logger.warning("Failed to record failed reformatize history: %s", h_err)
        raise HTTPException(500, err_msg)

    # ── 의존성 트리 구축 + 출력 컬럼 결정 ──
    report("결과 표 준비 중")
    dep_tree = []
    selected_set = set(req_items)
    out_cols = _resolve_output_cols(out_cols, table, req_items, wide_full)
    if hidden:
        # 전체 조회(선택 없음)나 의존성(dep) 경로로도 비공개 alias 컬럼은 내보내지 않는다
        out_cols = [c for c in out_cols if c not in hidden]
    if req_items:
        dep_tree = build_dependency_tree(table, req_items)
    wide = wide_full.select(out_cols) if out_cols else wide_full
    wide = _point_cnt_filter(wide, req.point_cnt_filter)
    if req.agg.strip():
        report(f"{req.agg.strip().upper()} 집계 중")
        fixed = set(PIVOT_KEY_COLS) | set(PIVOT_META_COLS)
        value_cols = [c for c in out_cols if c not in fixed]
        wide = _aggregate(wide, value_cols, req.agg)
    limit = req.limit if 0 < req.limit <= PAGE_ROWS_MAX else cfg["page_rows"]
    offset = max(0, int(req.offset))
    page = wide.slice(offset, limit)
    requested_aliases = req_items or [r["alias"] for r in table]
    index_cols = [a for a in requested_aliases if a in wide.columns]
    # spec: 헤더 클릭 시 "이 index 가 어떻게 계산됐는지" 를 보여주기 위한 규칙 상세.
    spec = {r["alias"]: {"unit": r["unit"], "speclow": r["speclow"],
                         "spechigh": r["spechigh"], "target": r["target"],
                         "category": r["category"],
                         "itemid": r["itemid"], "abs": r["absolute"], "scale": r["scale"],
                         "addp_form": r["addp_form"],
                         "refs": formula_refs(r["addp_form"]) if r["category"] == "addp" else []}
            for r in table if r["alias"] in wide.columns}
    # 컬럼 역할 분류 (프론트엔드 표시용)
    fixed_set = set(PIVOT_KEY_COLS) | set(PIVOT_META_COLS) | {"shot_count"}
    col_roles = {}
    for c in page.columns:
        if c in fixed_set:
            col_roles[c] = "key"
        elif c in selected_set:
            col_roles[c] = "selected"
        elif c in spec:
            col_roles[c] = "dep"         # 의존성 ADDP/REAL
        else:
            col_roles[c] = "raw"         # raw ITEMID 원본
    # 활동 대시보드: 어떤 index 를 어떤 필터/집계로 조회했는지.
    from core.audit import record_user as _audit_user
    _items_txt = ",".join(req_items[:8]) + ("…" if len(req_items) > 8 else "") if req_items else "all"
    _audit_user(user.get("username", ""), "reformatize:run",
                detail=f"product={req.product} items={_items_txt} agg={req.agg.strip() or 'raw'} "
                       f"rows={wide.height} filter=[{_filter_desc(req) or '없음'}]",
                tab="reformatize")
    elapsed = round((time.monotonic() - t0) * 1000)
    try:
        _save_or_increment_reformatize_history(
            req.product,
            items=req_items,
            filters=req,
            agg=req.agg,
            username=str(user.get("username") or ""),
            history_id=req.history_id,
            status="success",
            error_message="",
            row_count=wide.height,
            elapsed_ms=elapsed,
        )
    except Exception as exc:
        logger.warning("Failed to auto-record reformatize history: %s", exc)
    return {
        "product": req.product,
        "vehicle_csv": vehicle_csv,
        "columns": list(page.columns),
        "index_columns": index_cols,
        "spec": spec,
        "col_roles": col_roles,
        "dep_tree": dep_tree,
        "rows": serialize_rows(page.to_dicts()),
        "offset": offset,
        "limit": limit,
        "total_rows": wide.height,
        "rule_errors": errors,
        "notice": notice,
        "elapsed_ms": round((time.monotonic() - t0) * 1000),
    }


# ── CSV 스트리밍 ────────────────────────────────────────────────────
# 전체 CSV 를 문자열로 한 번에 만들면 결과 df 와 별개로 문자열+bytes 두 벌이
# 더 잡히고, 그 사이 프록시로 한 바이트도 나가지 않아 응답 대기가 502 로
# 끊긴다. 행 조각 단위로 인코딩해 즉시 흘려보낸다.
_CSV_CHUNK_ROWS = 20_000


def _csv_chunk_bytes(df: pl.DataFrame, header: bool) -> bytes:
    try:
        return df.write_csv(include_header=header).encode("utf-8")
    except TypeError:                                     # polars < 0.19.14
        return df.write_csv(has_header=header).encode("utf-8")


def _csv_stream_response(df: pl.DataFrame, filename: str, on_done=None):
    """행 조각 단위로 CSV 를 흘려보내는 응답. `on_done(sent_bytes)` 는 항상 1회 호출."""
    from fastapi.responses import StreamingResponse

    fname = safe_filename(filename)
    if not fname.endswith(".csv"):
        fname += ".csv"
    BOM = b"\xef\xbb\xbf"

    def _gen():
        sent = 0
        try:
            if df.height == 0:
                head = _csv_chunk_bytes(df.head(0), True)
                sent += len(head)
                yield BOM + head
                return
            first = True
            for off in range(0, df.height, _CSV_CHUNK_ROWS):
                part = _csv_chunk_bytes(df.slice(off, _CSV_CHUNK_ROWS), first)
                sent += len(part)
                yield (BOM + part) if first else part
                first = False
        finally:
            if on_done is not None:
                try:
                    on_done(sent)
                except Exception:
                    logger.exception("reformatize: 다운로드 이력 기록 실패")

    return StreamingResponse(
        _gen(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": download_content_disposition(fname)},
    )


def _hidden_for(product: str, is_admin: bool) -> set[str]:
    return set() if is_admin else _hidden_aliases((_find_csv(product) or Path("")).name)


def _visible_aliases_for(product: str, hidden: set[str]) -> list[str]:
    csv_fp = _find_csv(product)
    if csv_fp is None:
        return []
    return [
        str(row.get("alias") or "")
        for row in load_vehicle_table(csv_fp)
        if str(row.get("alias") or "") and str(row.get("alias") or "") not in hidden
    ]


def _check_download_request(product: str, f: Filters, wanted: list[str],
                            is_admin: bool) -> list[str]:
    """다운로드 전제 조건 검증 — 실제로 뽑을 alias 목록을 돌려준다.

    필터(날짜·lot 등) 미지정도 허용하며, 최종 결과 용량이 초과되면 계산 시 차단된다.
    비공개 항목 규칙은 UI 우회 호출에도 그대로 적용된다.
    """
    hidden = _hidden_for(product, is_admin)
    if not is_admin and not wanted and hidden:
        visible = _visible_aliases_for(product, hidden)
        if not visible:
            raise HTTPException(403, "관리자가 현재 제품의 ET 다운로드 항목 전체를 비공개로 설정했습니다")
        return visible
    kept = [a for a in wanted if a not in hidden]
    if wanted and not kept:
        raise HTTPException(403, "선택한 항목은 관리자가 비공개로 설정했습니다")
    return kept


def _build_download_frame(product: str, f: Filters, wanted: list[str], agg: str,
                          is_admin: bool, progress=None) -> tuple[pl.DataFrame, str, int]:
    """다운로드용 결과 프레임 — (wide, vehicle_csv, 필터된 raw 행수).

    조회와 다운로드는 완전히 같은 열을 제공한다. REAL 은 scale factor 가
    곱해진 alias 열만 싣고, alias 로 노출되지 않는 ITEMID 만 raw 로 남겨
    ADDP 계산 근거를 재현할 수 있게 한다 (`_resolve_output_cols` 참조).
    """
    hidden = _hidden_for(product, is_admin)
    cfg = _settings()
    max_mb = cfg.get("max_download_mb", 500)
    wide_full, out_cols, _errors, vehicle_csv, table, raw_rows, _notice = _compute(
        product, f, selected_items=wanted or None,
        max_mb=max_mb, progress=progress)
    # Match the screen ordering and retain all raw ITEMIDs used by calculations.
    out_cols = _resolve_output_cols(out_cols, table, wanted, wide_full, include_raw=True)
    if hidden:
        out_cols = [c for c in out_cols if c not in hidden]
    wide = wide_full.select(out_cols) if out_cols else wide_full
    wide = _point_cnt_filter(wide, f.point_cnt_filter)
    agg_method = str(agg or "").strip().lower()
    if agg_method:
        (progress or _noop_progress)(f"{agg_method.upper()} 집계 중")
        fixed = set(PIVOT_KEY_COLS) | set(PIVOT_META_COLS)
        value_cols = [c for c in out_cols if c not in fixed]
        wide = _aggregate(wide, value_cols, agg_method)
    _ensure_size_within_limit(wide, max_mb, context="CSV 다운로드")
    return wide, vehicle_csv, raw_rows


def _record_download(username: str, product: str, f: Filters, wanted: list[str], agg: str,
                     vehicle_csv: str, rows: int, cols: int, raw_rows: int,
                     sent_bytes: int) -> None:
    """다운로드 이력(관리자 모니터) + 활동 로그. 대기열/직접 다운로드 공통."""
    agg_method = str(agg or "").strip().lower()
    size_mb = round(sent_bytes / 1e6, 2)
    jsonl_append(DL_LOG, {
        "source": "reformatize",
        "username": username,
        "product": product,
        "sql": _filter_desc(f) + (f", agg={agg_method}(root_lot·wafer·step·pgm)" if agg_method else ""),
        "agg": agg_method,
        "rows": rows, "cols": cols, "raw_rows": raw_rows,
        "select_cols": vehicle_csv + (" | " + ",".join(wanted) if wanted else " | all"),
        "size_mb": size_mb,
    })
    # 활동 대시보드: 어떤 index 를 어떤 필터/집계로 CSV 로 뽑았는지.
    from core.audit import record_user as _audit_user
    _items_txt = ",".join(wanted[:8]) + ("…" if len(wanted) > 8 else "") if wanted else "all"
    _audit_user(username, "reformatize:download",
                detail=f"product={product} items={_items_txt} agg={agg_method or 'raw'} "
                       f"rows={rows} cols={cols} size_mb={size_mb} "
                       f"filter=[{_filter_desc(f)}]",
                tab="reformatize")


@router.get("/download")
def download(product: str = Query(...), lot_filter: str = Query(""),
             step_filter: str = Query(""), step_seq_filter: str = Query(""),
             wafer_filter: str = Query(""),
             site_cnt_filter: str = Query(""), point_cnt_filter: str = Query(""),
             days: int = Query(0),
             date_from: str = Query(""), date_to: str = Query(""),
             items: str = Query(""), agg: str = Query(""), user=Depends(current_user)):
    """즉시(동기) CSV 다운로드 — 스크립트/외부 호출용 호환 경로.

    화면은 `/download/start` 대기열을 쓴다. 이 경로는 계산이 끝날 때까지
    응답이 시작되지 않으므로 큰 조회에서는 프록시 타임아웃에 걸릴 수 있다.
    """
    f = Filters(lot_filter=lot_filter, step_filter=step_filter, step_seq_filter=step_seq_filter,
                wafer_filter=wafer_filter,
                site_cnt_filter=site_cnt_filter, point_cnt_filter=point_cnt_filter,
                days=days, date_from=date_from, date_to=date_to)
    is_admin = user.get("role") == "admin"
    wanted = _check_download_request(
        product, f, [s.strip() for s in items.split(",") if s.strip()], is_admin)
    wide, vehicle_csv, raw_rows = _build_download_frame(product, f, wanted, agg, is_admin)

    def _log(sent_bytes: int):
        _record_download(user.get("username", ""), product, f, wanted, agg, vehicle_csv,
                         wide.height, wide.width, raw_rows, sent_bytes)

    return _csv_stream_response(
        wide,
        _download_name(product, f, user.get("username", ""), agg=agg),
        on_done=_log,
    )


# ── 다운로드 대기열 ──────────────────────────────────────────────────
# 여러 사람이 같은 시각에 ET 다운로드를 걸면 무거운 pivot 계산이 동시에 시작돼
# 서버가 OOM/502 로 넘어간다. 그래서 다운로드는 job 으로 만들어 한 건씩
# 처리하고(core/download_queue.py), 화면은 job 상태를 폴링해 "대기 2번째 /
# 원본 읽는 중 3/12 / CSV 생성 중" 을 계속 보여준다. 느린 건 괜찮지만
# **아무 표시 없이 멈춰 보이는 건 안 된다**는 게 이 설계의 이유다.

class DownloadJobReq(Filters):
    product: str
    items: list[str] = []
    agg: str = ""
    history_id: str = ""


class JobRef(BaseModel):
    job_id: str = ""


def _filters_dump(f: Filters) -> dict:
    """Filters 필드만 뽑아 job meta 에 보관 — 나중에 이력 기록에 그대로 복원한다.

    RunReq/DownloadJobReq 처럼 상속 모델이 들어와도 Filters 밖 필드는 담지 않는다.
    """
    return {"lot_filter": f.lot_filter, "step_filter": f.step_filter,
            "step_seq_filter": f.step_seq_filter,
            "wafer_filter": f.wafer_filter, "site_cnt_filter": f.site_cnt_filter,
            "point_cnt_filter": f.point_cnt_filter,
            "days": int(f.days or 0), "date_from": f.date_from, "date_to": f.date_to}


def _job_csv_path(job_id: str, product: str) -> Path:
    from core import download_queue
    return download_queue.tmp_dir() / f"{safe_filename(job_id)}_{safe_filename(product)}.csv"


def _write_csv_file(df: pl.DataFrame, path: Path, progress=None) -> None:
    """결과를 임시 파일로 기록 — 완성본을 메모리에 들고 있지 않는다."""
    report = progress or _noop_progress
    BOM = b"\xef\xbb\xbf"
    total = df.height
    with open(path, "wb") as fh:
        fh.write(BOM)
        if total == 0:
            fh.write(_csv_chunk_bytes(df.head(0), True))
            return
        first = True
        for off in range(0, total, _CSV_CHUNK_ROWS):
            fh.write(_csv_chunk_bytes(df.slice(off, _CSV_CHUNK_ROWS), first))
            first = False
            done = min(off + _CSV_CHUNK_ROWS, total)
            report(f"CSV 파일 만드는 중 ({done:,}/{total:,}행)", done, total)


def _isolated_download_enabled() -> bool:
    return str(os.environ.get("FLOW_REFORMATIZE_PROCESS_ISOLATION", "1")).strip().lower() not in {"0", "false", "no", "off"}


def _terminate_download_process(proc) -> None:
    if proc is None or not proc.is_alive():
        return
    proc.terminate()
    proc.join(timeout=3.0)
    if proc.is_alive() and hasattr(proc, "kill"):
        proc.kill()
        proc.join(timeout=2.0)


def _run_download_isolated(ctx, *, product: str, f: Filters, wanted: list[str],
                           agg: str, is_admin: bool, path: Path) -> dict:
    """Run ET pivot in a killable process and enforce the 180s/memory guard.

    진입점은 `core.reformatize_child` — 자식이 polars 를 import 하기 전에 스레드
    상한(기본 1코어)을 심기 위해서다. 여기서 직접 target 을 잡으면 spawn 이
    `routers.reformatize` 를 먼저 import 하면서 폴라스 풀이 호스트 코어 수로
    고정돼 버린다.
    """
    from core.reformatize_child import download_entry, download_threads

    mp = multiprocessing.get_context("spawn")
    messages = mp.Queue()
    logger.info("ET 다운로드 계산 프로세스 시작 (threads=%d)", download_threads())
    proc = mp.Process(
        target=download_entry,
        kwargs={
            "result_queue": messages,
            "product": product,
            "filters": _filters_dump(f),
            "wanted": list(wanted or []),
            "agg": str(agg or ""),
            "is_admin": bool(is_admin),
            "path": str(path),
        },
        name=f"flow-et-download-{ctx.job_id}",
        daemon=True,
    )
    terminal = None
    try:
        proc.start()
        while terminal is None:
            if ctx.canceled:
                from core import download_queue
                raise download_queue.JobCanceled()
            ctx.guard()
            try:
                message = messages.get(timeout=0.5)
            except queue.Empty:
                if not proc.is_alive():
                    raise RuntimeError(f"ET 다운로드 계산 프로세스가 비정상 종료했습니다 (exit={proc.exitcode})")
                continue
            if message.get("type") == "progress":
                ctx.progress(message.get("phase") or "계산 중", message.get("done"), message.get("total"))
            else:
                terminal = message
        proc.join(timeout=3.0)
        if terminal.get("type") == "error":
            raise HTTPException(int(terminal.get("status") or 500), terminal.get("error") or "ET 다운로드 계산 실패")
        return terminal
    finally:
        _terminate_download_process(proc)
        try:
            messages.close()
        except Exception:
            pass


def _download_job_runner(ctx, *, product: str, f: Filters, wanted: list[str], agg: str,
                         is_admin: bool, username: str):
    """대기열 워커가 실행하는 실제 작업 — 계산 → 임시 CSV 파일."""
    from core import download_queue

    path = _job_csv_path(ctx.job_id, product)
    try:
        ctx.guard(force=True)
        if _isolated_download_enabled():
            result = _run_download_isolated(
                ctx, product=product, f=f, wanted=wanted, agg=agg,
                is_admin=is_admin, path=path)
            vehicle_csv = str(result.get("vehicle_csv") or "")
            raw_rows = int(result.get("raw_rows") or 0)
            rows = int(result.get("rows") or 0)
            cols = int(result.get("cols") or 0)
        else:
            wide, vehicle_csv, raw_rows = _build_download_frame(
                product, f, wanted, agg, is_admin, progress=ctx.progress)
            ctx.guard(force=True)
            if ctx.canceled:
                raise download_queue.JobCanceled()
            ctx.progress(f"CSV 파일 만드는 중 (0/{wide.height:,}행)", 0, wide.height)
            _write_csv_file(wide, path, progress=ctx.progress)
            rows, cols = wide.height, wide.width
        ctx.guard(force=True)
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            logger.debug("partial ET download cleanup failed: %s", path, exc_info=True)
        raise
    return {
        "path": path,
        "filename": _download_name(product, f, username, unique_id=ctx.job_id, agg=agg),
        "rows": rows,
        "cols": cols,
        "meta": {"vehicle_csv": vehicle_csv, "raw_rows": raw_rows,
                 "product": product, "agg": str(agg or "").strip().lower(),
                 "items": wanted, "filters": _filters_dump(f)},
    }


@router.post("/download/start")
def download_start(req: DownloadJobReq, user=Depends(current_user)):
    """다운로드 작업을 대기열에 넣고 job 상태를 즉시 돌려준다(블로킹 없음)."""
    from core import download_queue

    is_admin = user.get("role") == "admin"
    username = user.get("username", "")
    agg_method = str(req.agg or "").strip().lower()
    try:
        wanted = _check_download_request(req.product, req, list(req.items or []), is_admin)
        if _find_csv(req.product) is None:
            raise HTTPException(400, f"'{req.product}' 에 매칭되는 vehicle reformatter CSV 가 없습니다")
    except HTTPException as exc:
        try:
            _save_or_increment_reformatize_history(
                req.product,
                items=list(req.items or []),
                filters=req,
                agg=agg_method,
                username=username,
                history_id=req.history_id,
                status="error",
                error_message=str(exc.detail or ""),
            )
        except Exception:
            pass
        raise
    dedupe = "|".join([req.product, str(_filters_key(req)), ",".join(sorted(wanted)), agg_method])

    def _run(ctx):
        return _download_job_runner(ctx, product=req.product, f=req, wanted=wanted,
                                    agg=agg_method, is_admin=is_admin, username=username)

    view = download_queue.submit(
        "reformatize", username, f"{req.product} ET 다운로드", _run,
        product=req.product, dedupe_key=dedupe,
        meta={"items": wanted, "agg": agg_method},
        max_runtime_sec=180.0,
        memory_guard=True,
    )
    if not view.get("ok"):
        raise HTTPException(429, view.get("detail") or "다운로드 대기열이 가득 찼습니다")
    try:
        _save_or_increment_reformatize_history(
            req.product,
            items=wanted,
            filters=req,
            agg=agg_method,
            username=username,
            history_id=req.history_id,
            status="success",
            error_message="",
        )
    except Exception as exc:
        logger.warning("Failed to auto-record reformatize history on download: %s", exc)
    return view


def _job_for_user(job_id: str, user: dict):
    from core import download_queue

    job = download_queue.get(job_id)
    if job is None:
        raise HTTPException(404, "다운로드 작업을 찾을 수 없습니다 (만료되었거나 서버가 재시작됨) — "
                                 "다시 시도해 주세요")
    if job.get("username") != user.get("username", "") and user.get("role") != "admin":
        raise HTTPException(403, "다른 사용자의 다운로드 작업입니다")
    return job


@router.get("/download/status")
def download_status(job_id: str = Query(...), user=Depends(current_user)):
    """진행 상황 폴링 — 화면의 로딩창(모래시계) 문구가 여기서 나온다."""
    from core import download_queue

    _job_for_user(job_id, user)
    view = download_queue.status(job_id)
    if view is None:
        raise HTTPException(404, "다운로드 작업을 찾을 수 없습니다")
    return view


@router.post("/download/cancel")
def download_cancel(req: JobRef, user=Depends(current_user)):
    from core import download_queue

    job_id = str(req.job_id or "")
    _job_for_user(job_id, user)
    view = download_queue.cancel(job_id)
    if view is None:
        raise HTTPException(404, "다운로드 작업을 찾을 수 없습니다")
    return view


@router.get("/download/queue")
def download_queue_status(user=Depends(current_user)):
    """내 작업 + 서버 대기열 길이 — 페이지 진입 시 진행 중인 작업 복구용."""
    from core import download_queue

    return download_queue.snapshot(username=user.get("username", ""))


@router.get("/download/file")
def download_file(job_id: str = Query(...), user=Depends(current_user)):
    """완료된 작업의 CSV 파일 전송 — 첫 전송 때 downloads.jsonl 에 기록."""
    from core import download_queue
    from fastapi.responses import StreamingResponse

    job = _job_for_user(job_id, user)
    state = job.get("state")
    if state in download_queue.ACTIVE_STATES:
        raise HTTPException(409, "아직 준비 중입니다 — 완료 후 다시 시도하세요")
    if state == "error":
        raise HTTPException(int(job.get("error_status") or 400), job.get("error") or "다운로드 실패")
    if state == "canceled":
        raise HTTPException(410, "취소된 다운로드입니다")
    result = job.get("result") or {}
    path = Path(str(result.get("path") or ""))
    if not result.get("path") or not path.is_file():
        raise HTTPException(410, "결과 파일이 만료되었습니다 — 다시 조회해 주세요")

    already = download_queue.fetched_once(job_id)
    meta = result.get("meta") or {}
    username = job.get("username", "")
    fname = safe_filename(str(result.get("filename") or "download.csv"))

    def _gen():
        sent = 0
        try:
            with open(path, "rb") as fh:
                while True:
                    chunk = fh.read(1 << 20)
                    if not chunk:
                        break
                    sent += len(chunk)
                    yield chunk
        finally:
            try:
                download_queue.mark_fetched(job_id, sent)
                if not already:
                    filters = Filters(**(meta.get("filters") or {}))
                    _record_download(username, str(meta.get("product") or ""), filters,
                                     list(meta.get("items") or []), str(meta.get("agg") or ""),
                                     str(meta.get("vehicle_csv") or ""),
                                     int(result.get("rows") or 0), int(result.get("cols") or 0),
                                     int(meta.get("raw_rows") or 0), sent)
            except Exception:
                logger.exception("reformatize: 다운로드 이력 기록 실패")

    return StreamingResponse(
        _gen(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": download_content_disposition(fname)},
    )


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
    agg: str = ""


def _run_test(product: str, items: list[TestItem], f: Filters, auto_trim: bool = False,
              agg: str = ""):
    """필터된 base wide + 테스트 ADDP 적용 → (결과 df[key+meta+참조+테스트], 테스트 alias, 에러, csv, notice)."""
    cfg = _settings()
    max_mb = cfg.get("max_download_mb", 500)
    wide_full, out_cols, _base_errors, vehicle_csv, _table, _raw_rows, notice = _compute(
        product, f, max_mb=max_mb, auto_trim=auto_trim)
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
    out = _point_cnt_filter(out, f.point_cnt_filter)
    agg_method = str(agg or "").strip().lower()
    if agg_method:
        value_cols = [c for c in refs + test_aliases if c in out.columns]
        out = _aggregate(out, value_cols, agg_method)
    return out, test_aliases, errors, vehicle_csv, notice


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
        # 도움말은 참조 가능한 컬럼 이름만 필요하다 — 경량(auto_trim) 조회로 충분.
        wide_full, out_cols, _e, vehicle_csv, table, _raw_rows, _notice = _compute(
            product, Filters(), max_mb=_settings().get("max_download_mb", 500), auto_trim=True)
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
    out, test_aliases, errors, vehicle_csv, notice = _run_test(
        req.product, req.items, req, auto_trim=True, agg=req.agg)
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
        "notice": notice,
        "elapsed_ms": round((time.monotonic() - t0) * 1000),
    }


@router.post("/test/download")
def test_download(req: TestReq, admin=Depends(require_admin)):
    out, test_aliases, _errors, vehicle_csv, _notice = _run_test(
        req.product, req.items, req, agg=req.agg)
    cfg = _settings()
    _ensure_size_within_limit(out, cfg.get("max_download_mb", 500), context="테스트 CSV 다운로드")

    def _log(sent_bytes: int):
        jsonl_append(DL_LOG, {
            "source": "reformatize_test",
            "username": admin.get("username", ""),
            "product": req.product,
            "sql": _filter_desc(req) + (f", agg={req.agg.strip().lower()}(root_lot·wafer·step·pgm)" if req.agg.strip() else ""),
            "agg": req.agg.strip().lower(),
            "rows": out.height, "cols": out.width,
            "select_cols": ", ".join(f"{i.alias}={i.addp_form}" for i in req.items if str(i.alias or "").strip()),
            "size_mb": round(sent_bytes / 1e6, 2),
        })

    return _csv_stream_response(
        out,
        _download_name(req.product, req, admin.get("username", ""), agg=req.agg, suffix="ADDP-test"),
        on_done=_log,
    )


# ── ET 다운로드 검색식 및 히스토리 관리 ───────────────────────────────────

def _normalize_reformatize_filters(filters: dict | Filters | None) -> dict:
    if filters is None:
        return {
            "lot_filter": "", "step_filter": "", "step_seq_filter": "",
            "wafer_filter": "", "site_cnt_filter": "", "point_cnt_filter": "",
            "days": 0, "date_from": "", "date_to": "",
        }
    if isinstance(filters, dict):
        return {
            "lot_filter": str(filters.get("lot_filter") or "").strip(),
            "step_filter": str(filters.get("step_filter") or "").strip(),
            "step_seq_filter": str(filters.get("step_seq_filter") or "").strip(),
            "wafer_filter": str(filters.get("wafer_filter") or "").strip(),
            "site_cnt_filter": str(filters.get("site_cnt_filter") or "").strip(),
            "point_cnt_filter": str(filters.get("point_cnt_filter") or "").strip(),
            "days": int(filters.get("days") or 0),
            "date_from": str(filters.get("date_from") or "").strip(),
            "date_to": str(filters.get("date_to") or "").strip(),
        }
    return {
        "lot_filter": str(getattr(filters, "lot_filter", "") or "").strip(),
        "step_filter": str(getattr(filters, "step_filter", "") or "").strip(),
        "step_seq_filter": str(getattr(filters, "step_seq_filter", "") or "").strip(),
        "wafer_filter": str(getattr(filters, "wafer_filter", "") or "").strip(),
        "site_cnt_filter": str(getattr(filters, "site_cnt_filter", "") or "").strip(),
        "point_cnt_filter": str(getattr(filters, "point_cnt_filter", "") or "").strip(),
        "days": int(getattr(filters, "days", 0) or 0),
        "date_from": str(getattr(filters, "date_from", "") or "").strip(),
        "date_to": str(getattr(filters, "date_to", "") or "").strip(),
    }


def _format_reformatize_expression(
    product: str,
    items: list[str],
    filters: dict | Filters | None,
    agg: str,
) -> str:
    norm_filters = _normalize_reformatize_filters(filters)
    clean_product = str(product or "").strip().upper()
    clean_items = sorted(str(it).strip() for it in (items or []) if str(it).strip())
    clean_agg = str(agg or "").strip().lower()

    lines = [
        "Q1",
        "TABLE = et",
        f"PRODUCT = {clean_product}",
        "REFORMATTER = true",
    ]
    if clean_items:
        lines.append(f"ITEMS = {', '.join(clean_items)}")
    else:
        lines.append("ITEMS = ALL")

    sql_parts = ["SELECT root_lot_id, wafer_id, tkout_time, value"]
    where_parts = []
    if norm_filters.get("date_from"):
        where_parts.append(f"tkout_time >= '{norm_filters['date_from']}'")
    if norm_filters.get("date_to"):
        where_parts.append(f"tkout_time <= '{norm_filters['date_to']}'")
    if where_parts:
        sql_parts.append(f"WHERE {' AND '.join(where_parts)}")
    lines.append(f"SQL = {' '.join(sql_parts)}")

    days = norm_filters.get("days", 0)
    if days > 0:
        lines.append(f"RECENT_DAYS = {days}")

    if norm_filters.get("lot_filter"):
        lines.append(f"ROOT_LOTS = {norm_filters['lot_filter']}")

    if norm_filters.get("wafer_filter"):
        lines.append(f"WAFERS = {norm_filters['wafer_filter']}")

    if norm_filters.get("step_filter"):
        lines.append(f"FILTER = step_id | operator=in | values={norm_filters['step_filter']}")

    if norm_filters.get("step_seq_filter"):
        lines.append(f"FILTER = step_seq | operator=in | values={norm_filters['step_seq_filter']}")

    if norm_filters.get("site_cnt_filter"):
        lines.append(f"FILTER = total_site_cnt | operator=in | values={norm_filters['site_cnt_filter']}")

    if norm_filters.get("point_cnt_filter"):
        lines.append(f"FILTER = shot_count | operator=in | values={norm_filters['point_cnt_filter']}")

    if clean_agg:
        lines.append(f"# AGG = {clean_agg.upper()}")

    return "\n".join(lines)


def _reformatize_expression_hash(
    product: str,
    items: list[str],
    filters: dict | Filters | None,
    agg: str,
) -> str:
    norm_filters = _normalize_reformatize_filters(filters)
    clean_product = str(product or "").strip().upper()
    clean_items = sorted(str(it).strip() for it in (items or []) if str(it).strip())
    clean_agg = str(agg or "").strip().lower()
    payload = {
        "product": clean_product,
        "items": clean_items,
        "filters": {
            k: v.upper() if isinstance(v, str) and k not in ("date_from", "date_to") else v
            for k, v in norm_filters.items()
        },
        "agg": clean_agg,
    }
    raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _reformatize_pins() -> dict[str, dict]:
    raw = load_json(PINS_FILE, {}) or {}
    return raw.get("pins") if isinstance(raw, dict) and isinstance(raw.get("pins"), dict) else {}


def _save_reformatize_pins(pins: dict[str, dict]) -> None:
    save_json(PINS_FILE, {"version": 1, "pins": pins})


def _reformatize_likes() -> dict[str, list[str]]:
    raw = load_json(LIKES_FILE, {}) or {}
    return raw.get("likes") if isinstance(raw, dict) and isinstance(raw.get("likes"), dict) else {}


def _save_reformatize_likes(likes: dict[str, list[str]]) -> None:
    save_json(LIKES_FILE, {"version": 1, "likes": likes})


def _save_or_increment_reformatize_history(
    product: str,
    items: list[str],
    filters: dict | Filters | None,
    agg: str,
    username: str,
    name: str | None = None,
    force_new: bool = False,
    history_id: str | None = None,
    status: str = "success",
    error_message: str = "",
    row_count: int = 0,
    elapsed_ms: int = 0,
) -> dict:
    clean_product = str(product or "").strip().upper()
    if not clean_product:
        raise ValueError("제품명이 비어 있습니다.")
    clean_items = sorted(str(it).strip() for it in (items or []) if str(it).strip())
    norm_filters = _normalize_reformatize_filters(filters)
    clean_agg = str(agg or "").strip().lower()
    user = str(username or "anonymous").strip()
    clean_history_id = str(history_id or "").strip()
    expr_hash = _reformatize_expression_hash(clean_product, clean_items, norm_filters, clean_agg)
    expression = _format_reformatize_expression(clean_product, clean_items, norm_filters, clean_agg)
    now = dt.datetime.now(dt.timezone.utc).isoformat()

    with _REFORMATIZE_HISTORY_LOCK:
        entries = jsonl_read(
            HISTORY_FILE,
            limit=0,
            filter_fn=lambda e: isinstance(e, dict) and e.get("event") == "history",
        )
        existing_idx = None
        if not force_new:
            if clean_history_id:
                for idx, e in enumerate(entries):
                    if (str(e.get("history_id") or "").casefold() == clean_history_id.casefold()
                            and str(e.get("expression_hash") or "") == expr_hash):
                        existing_idx = idx
                        break
            if existing_idx is None:
                for idx, e in enumerate(entries):
                    if str(e.get("expression_hash") or "") == expr_hash:
                        existing_idx = idx
                        break

        if existing_idx is not None:
            entry = dict(entries[existing_idx])
            entry["reuse_count"] = max(0, int(entry.get("reuse_count") or 0)) + 1
            entry["last_used_at"] = now
            entry["status"] = status
            entry["error_message"] = str(error_message or "")
            if row_count:
                entry["row_count"] = row_count
            if elapsed_ms:
                entry["elapsed_ms"] = elapsed_ms
            if name and name.strip():
                entry["name"] = name.strip()
            entries[existing_idx] = entry
            with open(HISTORY_FILE, "w", encoding="utf-8") as fh:
                for row in entries:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            return entry
        else:
            uid_seed = f"{expr_hash}_{time.time()}_{len(entries)}"
            history_id = f"RH-{hashlib.md5(uid_seed.encode()).hexdigest()[:8].upper()}"
            if name and name.strip():
                entry_name = name.strip()
            else:
                items_summary = ", ".join(clean_items[:2]) + (" 등" if len(clean_items) > 2 else "") if clean_items else "전체 Index"
                filter_parts = []
                if norm_filters["days"]:
                    filter_parts.append(f"{norm_filters['days']}일")
                elif norm_filters["date_from"] or norm_filters["date_to"]:
                    filter_parts.append(f"{norm_filters['date_from']}~{norm_filters['date_to']}")
                if norm_filters["lot_filter"]:
                    filter_parts.append(norm_filters["lot_filter"])
                filter_suffix = f" ({', '.join(filter_parts)})" if filter_parts else ""
                entry_name = f"{clean_product} · {items_summary}{filter_suffix}"

            entry = {
                "event": "history",
                "history_id": history_id,
                "name": entry_name,
                "product": clean_product,
                "items": clean_items,
                "filters": norm_filters,
                "agg": clean_agg,
                "expression": expression,
                "expression_hash": expr_hash,
                "username": user,
                "timestamp": now,
                "last_used_at": now,
                "reuse_count": 1,
                "status": status,
                "error_message": str(error_message or ""),
                "row_count": row_count,
                "elapsed_ms": elapsed_ms,
            }
            jsonl_append(HISTORY_FILE, entry)
            return entry


def _reformatize_visible_history_entries(*, recent_limit: int = 500) -> list[dict]:
    entries = jsonl_read(
        HISTORY_FILE,
        limit=0,
        filter_fn=lambda e: isinstance(e, dict) and e.get("event") == "history",
    )
    pins = _reformatize_pins()
    likes = _reformatize_likes()
    normalized = []
    for entry in entries:
        row = dict(entry)
        history_id = str(row.get("history_id") or "")
        pin = pins.get(history_id) or {}
        liked_users = likes.get(history_id) or []
        row.update({
            "pinned": bool(pin),
            "pinned_at": str(pin.get("pinned_at") or ""),
            "pinned_by": str(pin.get("pinned_by") or ""),
            "likes_count": len(liked_users),
            "liked_users": liked_users,
            "reuse_count": max(0, int(row.get("reuse_count") or 0)),
            "status": str(row.get("status") or "success"),
            "error_message": str(row.get("error_message") or ""),
            "row_count": int(row.get("row_count") or 0),
            "elapsed_ms": int(row.get("elapsed_ms") or 0),
        })
        normalized.append(row)

    # 1. 처음 수행한 검색이 1번이 되도록 생성 시각(timestamp) 기준 오름차순으로 seq 번호 부여
    normalized.sort(key=lambda e: str(e.get("timestamp") or ""))
    for i, row in enumerate(normalized, 1):
        row["seq"] = i

    # 2. 최근 검색이 제일 위에 오도록 last_used_at/timestamp 내림차순 정렬
    pinned = [e for e in normalized if e.get("pinned")]
    pinned.sort(
        key=lambda e: str(e.get("last_used_at") or e.get("pinned_at") or e.get("timestamp") or ""),
        reverse=True,
    )
    unpinned = [e for e in normalized if not e.get("pinned")]
    unpinned.sort(
        key=lambda e: str(e.get("last_used_at") or e.get("timestamp") or ""),
        reverse=True,
    )
    max_count = max(1, min(1000, int(recent_limit or 500)))
    recent = unpinned[:max_count]
    return [*pinned, *recent]


def _toggle_reformatize_like(history_id: str, *, username: str, liked: bool | None = None) -> dict:
    clean_id = str(history_id or "").strip()
    user = str(username or "").strip()
    if not clean_id:
        raise ValueError("ID가 비어 있습니다.")
    with _REFORMATIZE_LIKE_LOCK:
        likes = _reformatize_likes()
        users = set(likes.get(clean_id) or [])
        currently_liked = user in users if user else False
        if liked is None:
            new_state = not currently_liked
        else:
            new_state = bool(liked)

        if new_state and user:
            users.add(user)
        elif not new_state and user:
            users.discard(user)

        likes[clean_id] = sorted(users)
        _save_reformatize_likes(likes)
        return {
            "history_id": clean_id,
            "liked": new_state,
            "likes_count": len(likes[clean_id]),
        }


def _set_reformatize_pin(history_id: str, *, pinned: bool, username: str) -> dict:
    clean_id = str(history_id or "").strip()
    if not clean_id:
        raise ValueError("ID가 비어 있습니다.")
    with _REFORMATIZE_PIN_LOCK:
        pins = _reformatize_pins()
        if pinned:
            pins[clean_id] = {
                "pinned_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "pinned_by": str(username or ""),
            }
        else:
            pins.pop(clean_id, None)
        _save_reformatize_pins(pins)
        return {"history_id": clean_id, "pinned": pinned}


class ReformatizeSaveReq(BaseModel):
    product: str
    items: list[str] = []
    filters: dict = {}
    agg: str = ""
    name: str = ""


class ReformatizePinReq(BaseModel):
    pinned: bool = True


class ReformatizeLikeReq(BaseModel):
    liked: bool | None = None


@router.get("/history")
def reformatize_history(
    request: Request,
    limit: int = Query(500, ge=1, le=1000),
    q: str = Query(""),
    history_id: str = Query(""),
    user=Depends(current_user),
):
    """Return pinned and popular/recent Reformatize search/extraction history."""
    username = str(user.get("username") or "")
    history_id = str(history_id or "").strip() if isinstance(history_id, str) else ""
    if history_id and not re.fullmatch(r"RH-[0-9A-F]{8}", history_id, flags=re.I):
        raise HTTPException(400, "Invalid Reformatize history key")
    if history_id:
        entries = jsonl_read(
            HISTORY_FILE,
            limit=1,
            filter_fn=lambda entry: (
                isinstance(entry, dict)
                and entry.get("event") == "history"
                and str(entry.get("history_id") or "").casefold() == history_id.casefold()
            ),
        )
        if entries:
            entry = dict(entries[-1])
            pin = _reformatize_pins().get(str(entry.get("history_id") or "")) or {}
            liked_users = _reformatize_likes().get(str(entry.get("history_id") or "")) or []
            entry.update({
                "pinned": bool(pin),
                "pinned_at": str(pin.get("pinned_at") or ""),
                "pinned_by": str(pin.get("pinned_by") or ""),
                "likes_count": len(liked_users),
                "liked_users": liked_users,
                "reuse_count": max(0, int(entry.get("reuse_count") or 0)),
            })
            entries = [entry]
    else:
        entries = _reformatize_visible_history_entries(recent_limit=limit)
    for entry in entries:
        liked_users = entry.get("liked_users") or []
        entry["liked"] = bool(username and username in liked_users)
    query = str(q or "").strip().casefold()
    if query:
        entries = [e for e in entries if query in json.dumps(e, ensure_ascii=False).casefold()]
    return {
        "ok": True,
        "history": entries,
        "recent_limit": limit,
        "pinned_count": sum(1 for e in entries if e.get("pinned")),
        "recent_count": sum(1 for e in entries if not e.get("pinned")),
        "can_manage": bool(is_page_manager(user, "reformatize")),
    }


@router.post("/history/save")
def reformatize_history_save(req: ReformatizeSaveReq, user=Depends(current_user)):
    username = str(user.get("username") or "")
    entry = _save_or_increment_reformatize_history(
        req.product,
        items=req.items,
        filters=req.filters,
        agg=req.agg,
        username=username,
        name=req.name.strip() if req.name else None,
        force_new=bool(req.name.strip()),
    )
    return {"ok": True, "entry": entry}


@router.post("/history/{history_id}/pin")
def reformatize_history_pin(history_id: str, req: ReformatizePinReq, user=Depends(current_user)):
    if not is_page_manager(user, "reformatize"):
        raise HTTPException(403, "관리자 또는 Reformatize 담당자만 고정할 수 있습니다.")
    result = _set_reformatize_pin(history_id, pinned=req.pinned, username=str(user.get("username") or ""))
    return {"ok": True, **result}


@router.post("/history/{history_id}/like")
def reformatize_history_like(history_id: str, req: ReformatizeLikeReq, user=Depends(current_user)):
    username = str(user.get("username") or "")
    if not username:
        raise HTTPException(401, "로그인이 필요합니다.")
    result = _toggle_reformatize_like(history_id, username=username, liked=req.liked)
    return {"ok": True, "history": result}


@router.post("/history/{history_id}/reuse")
def reformatize_history_reuse(history_id: str, user=Depends(current_user)):
    clean_id = str(history_id or "").strip()
    with _REFORMATIZE_HISTORY_LOCK:
        entries = jsonl_read(
            HISTORY_FILE,
            limit=0,
            filter_fn=lambda e: isinstance(e, dict) and e.get("event") == "history",
        )
        found = False
        target_entry = None
        for idx, e in enumerate(entries):
            if str(e.get("history_id") or "") == clean_id:
                entry = dict(e)
                entry["reuse_count"] = max(0, int(entry.get("reuse_count") or 0)) + 1
                entry["last_used_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
                entries[idx] = entry
                target_entry = entry
                found = True
                break
        if not found:
            raise HTTPException(404, "이력을 찾을 수 없습니다.")
        with open(HISTORY_FILE, "w", encoding="utf-8") as fh:
            for row in entries:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return {"ok": True, "entry": target_entry}
