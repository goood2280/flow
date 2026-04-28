"""routers/splittable.py v4.1.0 - multi-prefix transposed view + history + Base features.

v4.1 (2026-04-19, adapter-engineer slice):
  - Module-level DB_BASE removed. All route handlers now call PATHS.db_root /
    PATHS.base_root at request time, so `FLOW_*` env overrides and the
    admin_settings.json `data_roots` block land without a process restart.
  - New endpoint `GET /api/splittable/features` — joins
      `<db_root>/features_et_wafer.parquet` (wafer-level ET, 750 rows)
      + `<db_root>/features_inline_agg.parquet` (wafer-level INLINE aggregate, 50 rows)
    on (lot_id, wafer_id, product) via ET-left-join (Q005 default — preserves
    wafer coverage, INLINE-side cols are null when an ET wafer has no inline
    data). Returns wide-table metadata + columns + sample rows.
  - New endpoint `GET /api/splittable/uniques` — proxies
      `<db_root>/_uniques.json` unchanged, for frontend feature-select
    autocomplete catalog.
"""
import json, datetime, io, csv as csv_mod, logging, time
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from typing import List
import polars as pl
from core.paths import PATHS
from app_v2.shared.source_adapter import resolve_existing_root, resolve_column
from core.audit import record_user as _audit_user
from core.auth import current_user
from core.domain import classify_process_area
from core import s3_sync as _s3
from core.utils import (
    _STR, is_cat, find_lot_wafer_cols, load_json, save_json, safe_id,
    csv_response, csv_writer_bytes,
)

# v8.8.26: override CI 매칭 진단 — 실패 경로/스키마 mismatch 를 로그로 가시화.
logger = logging.getLogger("flow.splittable")

router = APIRouter(prefix="/api/splittable", tags=["splittable"])

_DISCOVERY_CACHE_TTL_SEC = 30.0
_RGLOB_CACHE: dict[tuple[str, tuple[str, ...]], tuple[float, list[Path]]] = {}
_DB_ROOTS_CACHE: dict[str, tuple[float, list[Path]]] = {}
_LOT_LOOKUP_CACHE_TTL_SEC = 60.0
_LOT_LOOKUP_CACHE_MAX = 256
_LOT_LOOKUP_CACHE: dict[tuple, tuple[float, dict]] = {}
_CSV_ROWS_CACHE: dict[str, tuple[float, int, list[dict]]] = {}
_SCHEMA_COLUMNS_CACHE: dict[str, tuple[float, int, list[str]]] = {}


def _db_base() -> Path:
    """Resolve DB root at call time so runtime overrides take effect."""
    return resolve_existing_root("db", PATHS.db_root)


def _base_root() -> Path:
    """Resolve Base root at call time (env / admin_settings / default chain)."""
    return resolve_existing_root("base", PATHS.base_root)


def _path_cache_sig(path: Path | None):
    if path is None:
        return ("", 0.0, 0)
    try:
        st = path.stat()
        return (str(path.resolve()), st.st_mtime, st.st_size)
    except Exception:
        return (str(path), 0.0, 0)


def _lot_lookup_cache_sig(product: str = "") -> tuple:
    try:
        product_sig = _path_cache_sig(_product_path(product)) if product else ("", 0.0, 0)
    except Exception:
        product_sig = (str(product or ""), 0.0, 0)
    return (
        _path_cache_sig(_db_base()),
        _path_cache_sig(_base_root()),
        _path_cache_sig(SOURCE_CFG if "SOURCE_CFG" in globals() else None),
        product_sig,
    )


def _clone_lookup_payload(payload: dict | None) -> dict | None:
    if payload is None:
        return None
    out = {}
    for key, value in payload.items():
        if isinstance(value, list):
            out[key] = list(value)
        elif isinstance(value, dict):
            out[key] = dict(value)
        else:
            out[key] = value
    return out


def _lot_lookup_cache_get(key: tuple) -> dict | None:
    now = time.monotonic()
    cached = _LOT_LOOKUP_CACHE.get(key)
    if cached and now - cached[0] < _LOT_LOOKUP_CACHE_TTL_SEC:
        return _clone_lookup_payload(cached[1])
    if cached:
        _LOT_LOOKUP_CACHE.pop(key, None)
    return None


def _lot_lookup_cache_set(key: tuple, payload: dict) -> dict:
    if len(_LOT_LOOKUP_CACHE) >= _LOT_LOOKUP_CACHE_MAX:
        try:
            _LOT_LOOKUP_CACHE.pop(next(iter(_LOT_LOOKUP_CACHE)))
        except Exception:
            _LOT_LOOKUP_CACHE.clear()
    _LOT_LOOKUP_CACHE[key] = (time.monotonic(), _clone_lookup_payload(payload) or {})
    return payload


PLAN_DIR = PATHS.data_root / "splittable"
PLAN_DIR.mkdir(parents=True, exist_ok=True)
PREFIX_CFG = PLAN_DIR / "prefix_config.json"
DEFAULT_PREFIXES = ["KNOB", "MASK", "INLINE", "VM", "FAB"]
PLAN_ALLOWED_PREFIXES = ["KNOB", "MASK", "FAB"]  # Only these can have plan values
# v8.8.6: paste 세트 공유 저장소 — LocalStorage 대신 BE 에 올려 팀 공용 풀 + CUSTOM 탭 연동.
PASTE_SETS_FILE = PLAN_DIR / "paste_sets.json"
# v8.4.9: 엑셀 메모/태그 저장소 — wafer 단위(tag) + parameter 단위(memo) 공용.
#   scope="wafer": target_key = "{product}__{root_lot_id}__W{wafer_id}"
#   scope="param": target_key = "{product}__{root_lot_id}__W{wafer_id}__{param}"
# 각 항목은 {id, text, username, created_at} 를 보관하고 작성자/관리자만 삭제 가능.
NOTES_FILE = PLAN_DIR / "notes.json"
TRACKER_ISSUES_FILE = PATHS.data_root / "tracker" / "issues.json"
INFORMS_FILE = PATHS.data_root / "informs" / "informs.json"


def _load_prefixes():
    return load_json(PREFIX_CFG, DEFAULT_PREFIXES)


def _cast_cats_lazy(lf):
    """Cast Categorical to Utf8 in a LazyFrame."""
    try:
        schema = lf.collect_schema()
    except Exception:
        schema = lf.schema
    casts = [pl.col(n).cast(_STR, strict=False) for n, d in schema.items() if is_cat(d)]
    return lf.with_columns(casts) if casts else lf


def _scan_cast_options():
    try:
        return pl.ScanCastOptions(categorical_to_string="allow")
    except Exception:
        return None


def _first_scan_schema_with_string_cats(source, hive_partitioning=None):
    if not isinstance(source, (list, tuple)) or not source:
        return None
    try:
        kwargs = {}
        if hive_partitioning is not None:
            kwargs["hive_partitioning"] = hive_partitioning
        schema = pl.scan_parquet(str(source[0]), **kwargs).collect_schema()
    except Exception:
        return None
    out = {}
    changed = False
    for name, dtype in schema.items():
        if is_cat(dtype):
            out[name] = _STR
            changed = True
        else:
            out[name] = dtype
    return out if changed else None


def _scan_parquet_compat(source, **kwargs):
    """Scan parquet while accepting String/Categorical drift across partitions."""
    scan_kwargs = dict(kwargs)
    if "schema" not in scan_kwargs:
        schema = _first_scan_schema_with_string_cats(
            source, hive_partitioning=scan_kwargs.get("hive_partitioning")
        )
        if schema:
            scan_kwargs["schema"] = schema
    opts = _scan_cast_options()
    if opts is not None and "cast_options" not in scan_kwargs:
        scan_kwargs["cast_options"] = opts
    try:
        return pl.scan_parquet(source, **scan_kwargs)
    except TypeError:
        scan_kwargs.pop("cast_options", None)
        return pl.scan_parquet(source, **scan_kwargs)


import re as _re
_NUM_RE = _re.compile(r"(\d+(?:\.\d+)?)")
_PREFIX_NUM_RE = _re.compile(r"^(\d+(?:\.\d+)*)(?:[_\s-]|$)")


def _version_num_key(raw: str) -> tuple:
    try:
        return tuple(int(p) for p in str(raw).split("."))
    except Exception:
        return (float("inf"),)

def _natural_param_key(name: str):
    """v8.4.4 — prefix 뒤 숫자(정수/소수) 기준 자연 정렬 키 생성.
    예: 'KNOB_12.0_ASV_FOO' → (12.0, '_ASV_FOO', 'KNOB')
    숫자가 없으면 prefix 를 뺀 본문 natural token 기준으로 후순.
    v8.8.14: 내부 문자열 tail 도 자연 정렬(숫자/비숫자 분리)로 안정화 →
      `KNOB_10_FOO` 뒤에 `KNOB_2_FOO` 가 오는 오작동 방지.
    v9.0.3: prefix 는 정렬 tie-breaker 로만 사용.
      여러 prefix(KNOB/MASK/INLINE/VM...)를 같이 볼 때 prefix 그룹이 먼저 묶이지 않고
      prefix 를 제거한 항목명/순번 기준으로 자연정렬된다.
    """
    if not name: return (1, (), (), "")
    raw = str(name)
    parts = raw.split("_", 1)
    pfx = parts[0] if len(parts) > 1 else ""
    rest = parts[1] if len(parts) > 1 else raw
    # split rest into natural tokens (numbers → version tuple, others → lowercased str)
    tail: list = []
    for tok in _NUM_RE.split(rest):
        if tok == "":
            continue
        if _NUM_RE.fullmatch(tok):
            tail.append(("n", _version_num_key(tok)))
        else:
            tail.append(("s", tok.lower()))
    # Only the immediate segment after the prefix is the primary process/order
    # key. Numbers buried later in the feature name must not split 1.0/2.0/2.1
    # process-order groups.
    m = _PREFIX_NUM_RE.search(rest)
    if m:
        return (0, _version_num_key(m.group(1)), tuple(tail), pfx.lower())
    return (1, (), tuple(tail), pfx.lower())


# v8.8.14: ML_TABLE 컬럼 display rename — rule_order + func_step 을 feature 앞에
#   끼워 넣어 SplitTable 헤더에서 "어느 공정 step 의 feature 인지" 한눈에 보이게 함.
#   규칙:
#     KNOB_<feature>   + knob_meta 매칭 시 → KNOB_{rule_order:.1f}_{func_step_label}_<feature>
#     INLINE_<item_id> + inline_meta 매칭 시 → INLINE_{step_id}_<item_id>     (step_id 가 숫자일 때 자연 정렬 유리)
#     VM_<feature>     + vm_meta   매칭 시 → VM_{step_id}_<feature>
#     매칭 실패/메타 없음 → 원본 그대로.
#   rule_order 가 여러 group 이면 min() 값, func_step 은 '+' 로 join (중복 제거).
#   display 이름만 바꾸고 원본 col 이름(`_param`) 은 그대로 보존 → plan/notes/
#   knob_meta lookup 이 깨지지 않음.
def _safe_step_segment(s: str) -> str:
    """func_step 값에서 공백/특수문자 제거 → 컬럼명 조각으로 안전하게."""
    if not s:
        return ""
    return _re.sub(r"[^A-Za-z0-9]+", "_", str(s)).strip("_")


def _product_aliases(product: str) -> set[str]:
    """Soft-landing product matcher for registry/rulebook joins."""
    raw = str(product or "").strip()
    if not raw:
        return set()
    out = {raw.upper()}
    core = raw
    if raw.upper().startswith("ML_TABLE_"):
        core = raw[len("ML_TABLE_"):].strip()
        if core:
            out.add(core.upper())
    up = core.upper()
    if up.startswith("PRODUCT_A"):
        if up.endswith("0"):
            out.update({"PRODA", "PRODA0", "PRODUCT_A0"})
        elif up.endswith("1"):
            out.update({"PRODA", "PRODA1", "PRODUCT_A1"})
        else:
            out.update({"PRODA", "PRODA0", "PRODA1", "PRODUCT_A0", "PRODUCT_A1"})
    elif up.startswith("PRODUCT_B"):
        out.update({"PRODB", "PRODUCT_B"})
    elif up == "PRODA0":
        out.update({"PRODA", "PRODUCT_A0"})
    elif up == "PRODA1":
        out.update({"PRODA", "PRODUCT_A1"})
    elif up == "PRODA":
        out.update({"PRODA0", "PRODA1", "PRODUCT_A0", "PRODUCT_A1"})
    elif up == "PRODB":
        out.update({"PRODUCT_B"})
    return out


_PRODUCT_FILE_EXTS = (".parquet", ".csv")


def _canonical_mltable_product_name(product: str, allow_bare: bool = False) -> str:
    """Return the canonical SplitTable product id for an ML_TABLE file/name."""
    raw = str(product or "").strip()
    if not raw:
        return ""
    if raw.casefold().startswith("ml_table_"):
        tail = raw[len("ML_TABLE_"):].strip()
    elif allow_bare:
        tail = raw
    else:
        return ""
    return f"ML_TABLE_{tail}".upper() if tail else ""


def _is_mltable_product_file(path: Path) -> bool:
    return (
        path.is_file()
        and path.suffix.lower() in _PRODUCT_FILE_EXTS
        and bool(_canonical_mltable_product_name(path.stem))
    )


def _lot_override_for(cfg: dict, product: str) -> dict:
    """Resolve lot_overrides by product name with case-insensitive ML_TABLE matching."""
    overrides = (cfg or {}).get("lot_overrides") or {}
    if not isinstance(overrides, dict):
        return {}
    keys = [str(product or "").strip()]
    canonical = _canonical_mltable_product_name(product, allow_bare=True)
    if canonical:
        keys.append(canonical)
    for key in keys:
        if key and isinstance(overrides.get(key), dict):
            return overrides.get(key) or {}
    folded = {k.casefold() for k in keys if k}
    for key, value in overrides.items():
        if str(key or "").casefold() in folded and isinstance(value, dict):
            return value
    return {}


def _first_meta_step(meta: dict) -> str:
    """Return a representative step token for display renaming."""
    if not isinstance(meta, dict):
        return ""
    for key in ("step_id",):
        value = str(meta.get(key) or "").strip()
        if value:
            return value
    for key in ("step_ids", "function_steps"):
        values = meta.get(key) or []
        if isinstance(values, list):
            for value in values:
                value = str(value or "").strip()
                if value:
                    return value
    for key in ("function_step",):
        value = str(meta.get(key) or "").strip()
        if value:
            return value
    for group in meta.get("groups") or []:
        if not isinstance(group, dict):
            continue
        for key in ("step_id",):
            value = str(group.get(key) or "").strip()
            if value:
                return value
        values = group.get("step_ids") or []
        if isinstance(values, list):
            for value in values:
                value = str(value or "").strip()
                if value:
                    return value
        for key in ("function_step", "func_step"):
            value = str(group.get(key) or "").strip()
            if value:
                return value
    return ""


def _build_col_rename_map(selected_cols: list, product: str) -> dict:
    """raw column name → display name. 매칭 없으면 원본 반환(맵에 키 없음)."""
    out: dict[str, str] = {}
    def _meta_get(meta_map: dict, full_name: str, tail_name: str):
        if not meta_map:
            return None
        if full_name in meta_map:
            return meta_map.get(full_name)
        if tail_name in meta_map:
            return meta_map.get(tail_name)
        full_l = str(full_name or "").lower()
        tail_l = str(tail_name or "").lower()
        for k, v in meta_map.items():
            key = str(k or "").lower()
            if key == full_l or key == tail_l:
                return v
        return None
    needed_prefixes = {
        str(col or "").partition("_")[0].upper()
        for col in selected_cols
        if col and "_" in str(col)
    }
    try:
        knob_meta = _build_knob_meta(product) if "KNOB" in needed_prefixes else {}
    except Exception:
        knob_meta = {}
    try:
        inline_meta = _build_inline_meta(product) if "INLINE" in needed_prefixes else {}
    except Exception:
        inline_meta = {}
    try:
        vm_meta = _build_vm_meta(product) if "VM" in needed_prefixes else {}
    except Exception:
        vm_meta = {}

    for col in selected_cols:
        if not col or "_" not in col:
            continue
        pfx, _, tail = col.partition("_")
        pfx_u = pfx.upper()
        # knob_meta / inline_meta / vm_meta 는 feature_name / item_id / feature_name 키로 저장되는데,
        # 사내 CSV 는 "KNOB_FOO" 같은 prefix 포함 형태와 "FOO" 같은 prefix 없는 형태가 혼재할 수 있음.
        # 두 가지 모두 시도 (full col → tail 순).
        if pfx_u == "KNOB":
            meta = _meta_get(knob_meta, col, tail)
            if not meta:
                continue
            if meta.get("inferred"):
                continue
            groups = meta.get("groups") or []
            if not groups:
                continue
            step_segs: list = []
            seen: set = set()
            for g in groups:
                seg = _safe_step_segment(g.get("func_step") or "")
                if seg and seg not in seen:
                    seen.add(seg)
                    step_segs.append(seg)
            if not step_segs:
                continue
            step_label = "+".join(step_segs)
            out[col] = f"KNOB_{step_label}_{tail}"
        elif pfx_u == "INLINE":
            meta = _meta_get(inline_meta, col, tail)
            if not meta:
                continue
            if meta.get("inferred"):
                continue
            sid = _safe_step_segment(_first_meta_step(meta))
            if not sid:
                continue
            out[col] = f"INLINE_{sid}_{tail}"
        elif pfx_u == "VM":
            meta = _meta_get(vm_meta, col, tail)
            if not meta:
                continue
            if meta.get("inferred"):
                continue
            sid = _safe_step_segment(_first_meta_step(meta))
            if not sid:
                continue
            out[col] = f"VM_{sid}_{tail}"
        # 그 외 prefix (FAB / MASK / ET / QTIME …) 는 rename 대상 아님.
    return out


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
            ov = _lot_override_for(cfg, product)
            schema_names_list = lf.collect_schema().names() if hasattr(lf, "collect_schema") else list(lf.schema.keys())
            root_col = _ci_resolve_in(ov.get("root_col") or "", schema_names_list) or None
            wf_col = _ci_resolve_in(ov.get("wf_col") or "", schema_names_list) or None
            if root_col or wf_col:
                # Fill missing with auto-detect
                auto_r, auto_w = find_lot_wafer_cols(schema_names_list)
                return (root_col or auto_r, wf_col or auto_w)
        except Exception:
            pass
    try:
        schema_names = lf.collect_schema().names()
    except Exception:
        schema_names = lf.schema
    return find_lot_wafer_cols(schema_names)


def _product_path(product: str):
    """Find product file. v8.4.3 — Base scope (ML_TABLE_PRODA/B etc.) 우선,
    이후 DB 루트(legacy) 로 폴백. ML 중심 설계로 전환.
    """
    raw = str(product or "").strip()
    canonical = _canonical_mltable_product_name(raw, allow_bare=True)
    names = []
    for name in (raw, canonical):
        if name and name not in names:
            names.append(name)
    base_root = _base_root()
    db_base = _db_base()
    for root in (base_root, db_base):
        if not root or not root.exists():
            continue
        for name in names:
            for ext in _PRODUCT_FILE_EXTS:
                fp = root / f"{name}{ext}"
                if fp.exists():
                    return fp
                ci = _find_ci_path(root, f"{name}{ext}")
                if ci is not None and ci.is_file():
                    return ci
        try:
            targets = {n.casefold() for n in names if n}
            for fp in sorted(root.iterdir(), key=lambda p: p.name.lower()):
                if fp.is_file() and fp.suffix.lower() in _PRODUCT_FILE_EXTS and fp.stem.casefold() in targets:
                    return fp
        except Exception:
            pass
    raise HTTPException(404, f"Product not found: {product}")


def _scan_product_base(product: str):
    """Scan the ML_TABLE file only, without FAB override joins."""
    product = _canonical_mltable_product_name(product, allow_bare=True) or str(product or "").strip()
    fp = _product_path(product)
    if fp.suffix.lower() == ".csv":
        return _cast_cats_lazy(pl.scan_csv(str(fp), infer_schema_length=5000))
    return _cast_cats_lazy(_scan_parquet_compat(str(fp)))


def _strip_non_authoritative_fab_fields(lf, product: str):
    """Hide FAB-only identifiers from ML tables unless they came from FAB source.

    `fab_lot_id` is an operational FAB identifier. If FAB override/source is off or
    failed, SplitTable should not surface a stale ML-side copy because users assume
    it came from live/real FAB lineage.  Do not synthesize it from ML_TABLE LOT_ID.
    """
    if not product or not str(product).casefold().startswith("ml_table_"):
        return lf
    try:
        names = lf.collect_schema().names()
    except Exception:
        return lf
    drop_cols = [n for n in names if n.casefold() == "fab_lot_id"]
    return lf.drop(drop_cols) if drop_cols else lf


def _select_columns(all_data_cols, custom_name: str, prefix: str, max_fallback: int = 50,
                    custom_cols: str = ""):
    """Multi-prefix ("KNOB,MASK") or ALL or custom-name/custom-cols based column selection.

    v8.8.16: CUSTOM 모드는 사용자가 저장한 columns 를 **그대로** 반환한다.
      - 기존: `all_data_cols` 에 없으면 걸러내어 → 값이 null 인 컬럼이 LOT 뷰에서 사라지는 문제.
      - 변경: custom 에 저장된 column 명을 있는 그대로 반환. view_split 이 null row 를
              자연스럽게 생성 (컬럼이 실제 df 에 없으면 모든 셀이 None, 컬럼명은 유지).
      - 빈 리스트면 기존 폴백 (상위 max_fallback) 유지.
    v8.8.33: `custom_cols` 쉼표 구분 문자열 지원 — 저장된 set 없이도 체크만 한 컬럼을 전송해
             즉시 view 에 반영. custom_name 보다 우선 (ad-hoc 입력 우선).
    """
    # ad-hoc custom_cols 우선
    if custom_cols:
        ad_hoc = [c.strip() for c in custom_cols.split(",") if c.strip()]
        if ad_hoc:
            return ad_hoc
    if custom_name:
        cfp = PLAN_DIR / f"custom_{custom_name}.json"
        data = load_json(cfp, {})
        saved = list(data.get("columns", []) or [])
        if saved:
            return saved
        return all_data_cols[:max_fallback]
    if prefix.upper() == "ALL":
        return all_data_cols[: max_fallback * 4]
    pref_list = [p.strip().upper() + "_" for p in prefix.split(",") if p.strip()]
    if pref_list:
        sel = [c for c in all_data_cols if any(c.upper().startswith(p) for p in pref_list)]
        if sel:
            return sel
    return all_data_cols[:max_fallback]


# ── Notes (v8.4.9-b): 검색된 wafer 태그 + 파라미터 메모 ───────────────
# 스키마: {data_root}/splittable/notes.json
#   { "entries": [
#       { "id": "n_xxxxxx",
#         "scope": "wafer" | "param",
#         "key":  "{product}__{root_lot_id}__W{wafer_id}"
#               | "{product}__{root_lot_id}__W{wafer_id}__{param_name}",
#         "text": "...",
#         "username": "hol",
#         "created_at": "2026-04-21T10:00:00" }
#     ] }
# 작성자 또는 admin 만 삭제 가능. 수정은 지원하지 않음 (메모 히스토리 유지).
def _load_notes() -> list:
    data = load_json(NOTES_FILE, {"entries": []})
    if isinstance(data, dict):
        return data.get("entries", [])
    return data if isinstance(data, list) else []


def _save_notes(entries: list) -> None:
    save_json(NOTES_FILE, {"entries": entries})


def _new_note_id() -> str:
    import secrets as _secrets
    return "n_" + _secrets.token_hex(5)


def _notes_key_wafer(product: str, root_lot_id: str, wafer_id) -> str:
    return f"{product}__{root_lot_id}__W{wafer_id}"


def _notes_key_param(product: str, root_lot_id: str, wafer_id, param: str) -> str:
    return f"{product}__{root_lot_id}__W{wafer_id}__{param}"


def _notes_key_lot(product: str, root_lot_id: str) -> str:
    """v8.7.8: LOT 단위 노트 (해당 root_lot_id 전역). param 태그와 달리 lot 에 묶임."""
    return f"{product}__LOT__{root_lot_id}"


def _notes_key_param_global(product: str, param: str) -> str:
    """v8.7.8: parameter 전역 태그 — product 내 모든 LOT 에서 동일 parameter 에 노출."""
    return f"{product}__PARAM__{param}"


def _notes_lot_prefix(product: str, root_lot_id: str) -> str:
    return f"{product}__{root_lot_id}__"


def _notes_product_param_prefix(product: str) -> str:
    return f"{product}__PARAM__"


def _notes_product_lot_prefix(product: str) -> str:
    return f"{product}__LOT__"


class NoteSaveReq(BaseModel):
    scope: str                 # "wafer" | "param" | "lot" | "param_global"
    product: str = ""
    root_lot_id: str = ""
    wafer_id: str = ""
    param: str = ""            # scope == "param" / "param_global" 일 때
    text: str
    username: str = ""


class NoteDeleteReq(BaseModel):
    id: str
    username: str = ""


@router.get("/notes")
def list_notes(product: str = Query(""), root_lot_id: str = Query(""), username: str = Query("")):
    """필터:
      - product+root_lot_id → (wafer + param + lot) for that lot
        PLUS param_global for the product (전역 태그는 모든 LOT 에서 공통 노출)
      - product only → product 전역 (param_global + lot 전체)
      - 없으면 전체
    """
    entries = _load_notes()
    if product and root_lot_id:
        lot_pfx = _notes_lot_prefix(product, root_lot_id)
        lot_key = _notes_key_lot(product, root_lot_id)
        pg_pfx = _notes_product_param_prefix(product)
        def _match(e):
            k = str(e.get("key", ""))
            sc = e.get("scope")
            if sc == "wafer" and k.startswith(lot_pfx):
                return True
            if sc == "param" and k.startswith(lot_pfx):
                return True
            if sc == "lot" and k == lot_key:
                return True
            if sc == "param_global" and k.startswith(pg_pfx):
                return True
            return False
        entries = [e for e in entries if _match(e)]
    elif product:
        pg_pfx = _notes_product_param_prefix(product)
        lot_pfx = _notes_product_lot_prefix(product)
        entries = [e for e in entries
                   if str(e.get("key", "")).startswith(pg_pfx) or str(e.get("key", "")).startswith(lot_pfx)]
    entries.sort(key=lambda e: e.get("created_at", ""), reverse=True)
    return {"notes": entries, "total": len(entries)}


@router.post("/notes/save")
def save_note(req: NoteSaveReq, request: Request):
    from core.auth import current_user as _cu
    me = _cu(request)
    username = me.get("username") or req.username or "anonymous"
    scope = (req.scope or "").strip()
    if scope not in ("wafer", "param", "lot", "param_global"):
        raise HTTPException(400, "scope must be 'wafer'|'param'|'lot'|'param_global'")
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(400, "empty text")
    if len(text) > 2000:
        raise HTTPException(400, "text too long (max 2000 chars)")
    if not req.product:
        raise HTTPException(400, "product required")
    if scope == "wafer":
        if not req.root_lot_id or not str(req.wafer_id or "").strip():
            raise HTTPException(400, "root_lot_id/wafer_id required for wafer scope")
        key = _notes_key_wafer(req.product, req.root_lot_id, req.wafer_id)
    elif scope == "param":
        if not req.root_lot_id or not str(req.wafer_id or "").strip() or not req.param:
            raise HTTPException(400, "root_lot_id/wafer_id/param required for param scope")
        key = _notes_key_param(req.product, req.root_lot_id, req.wafer_id, req.param)
    elif scope == "lot":
        if not req.root_lot_id:
            raise HTTPException(400, "root_lot_id required for lot scope")
        key = _notes_key_lot(req.product, req.root_lot_id)
    else:  # param_global
        if not req.param:
            raise HTTPException(400, "param required for param_global scope")
        key = _notes_key_param_global(req.product, req.param)
    entry = {
        "id": _new_note_id(),
        "scope": scope,
        "key": key,
        "text": text,
        "username": username,
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    entries = _load_notes()
    entries.append(entry)
    _save_notes(entries)
    return {"ok": True, "entry": entry}


@router.post("/notes/delete")
def delete_note(req: NoteDeleteReq, request: Request):
    from core.auth import current_user as _cu
    me = _cu(request)
    username = me.get("username") or ""
    role = me.get("role") or ""
    entries = _load_notes()
    target = next((e for e in entries if e.get("id") == req.id), None)
    if not target:
        raise HTTPException(404, "note not found")
    if role != "admin" and target.get("username") != username:
        raise HTTPException(403, "only author or admin can delete")
    entries = [e for e in entries if e.get("id") != req.id]
    _save_notes(entries)
    return {"ok": True}


def _wafer_filter_set(raw: str) -> set[str]:
    out = set()
    for part in (raw or "").split(","):
        s = str(part).strip()
        if not s:
            continue
        out.add(s)
        if s.upper().startswith("W"):
            core = s[1:]
            out.add(core)
            try:
                n = int(core)
                out.add(str(n))
                out.add(f"{n:02d}")
                out.add(f"W{n}")
                out.add(f"W{n:02d}")
            except Exception:
                pass
        else:
            out.add("W" + s)
            try:
                n = int(s)
                out.add(str(n))
                out.add(f"{n:02d}")
                out.add(f"W{n}")
                out.add(f"W{n:02d}")
            except Exception:
                pass
    return {v for v in out if v not in ("", "None", "null")}


def _wafer_matches(wafer_value, wafer_set: set[str]) -> bool:
    if not wafer_set:
        return True
    s = ("" if wafer_value is None else str(wafer_value).strip())
    return s in wafer_set or s.upper() in wafer_set


def _scope_label(has_wafer: bool) -> str:
    return "wafer" if has_wafer else "lot"


def _load_operational_history(product: str, root_lot_id: str, wafer_ids: str,
                              username: str, role: str) -> list[dict]:
    if not root_lot_id:
        return []
    wafer_set = _wafer_filter_set(wafer_ids)
    out: list[dict] = []
    try:
        from routers.groups import filter_by_visibility
    except Exception:
        def filter_by_visibility(items, username, role, key="group_ids"):
            return items

    tracker_items = filter_by_visibility(load_json(TRACKER_ISSUES_FILE, []), username, role, key="group_ids")
    for issue in tracker_items or []:
        matched_rows = []
        for row in (issue.get("lots") or []):
            rid = (row.get("root_lot_id") or "")[:5]
            if rid != root_lot_id:
                continue
            wafer_val = str(row.get("wafer_id") or "").strip()
            if wafer_val and not _wafer_matches(wafer_val, wafer_set):
                continue
            if not wafer_val and wafer_set:
                continue
            matched_rows.append(row)
        if not matched_rows:
            continue
        for row in matched_rows:
            out.append({
                "source": "tracker",
                "scope": _scope_label(bool(str(row.get("wafer_id") or "").strip())),
                "time": issue.get("updated_at") or issue.get("created") or issue.get("timestamp") or "",
                "author": issue.get("username") or "",
                "title": issue.get("title") or "(untitled issue)",
                "detail": row.get("comment") or "",
                "status": issue.get("status") or "",
                "category": issue.get("category") or "",
                "root_lot_id": root_lot_id,
                "wafer_id": str(row.get("wafer_id") or ""),
                "lot_id": row.get("lot_id") or "",
                "ref_id": issue.get("id") or "",
            })
        for cm in (issue.get("comments") or []):
            for row in matched_rows:
                out.append({
                    "source": "tracker_comment",
                    "scope": _scope_label(bool(str(row.get("wafer_id") or "").strip())),
                    "time": cm.get("created_at") or "",
                    "author": cm.get("username") or "",
                    "title": issue.get("title") or "(issue comment)",
                    "detail": cm.get("text") or "",
                    "status": issue.get("status") or "",
                    "category": issue.get("category") or "",
                    "root_lot_id": root_lot_id,
                    "wafer_id": str(row.get("wafer_id") or ""),
                    "lot_id": row.get("lot_id") or "",
                    "ref_id": issue.get("id") or "",
                })

    inform_items = filter_by_visibility(load_json(INFORMS_FILE, []), username, role, key="group_ids")
    for inf in inform_items or []:
        inf_root = (inf.get("root_lot_id") or (inf.get("lot_id") or "")[:5] or "")[:5]
        inf_fab = str(inf.get("fab_lot_id_at_save") or inf.get("lot_id") or "").strip()
        if inf_root != root_lot_id and not inf_fab.startswith(root_lot_id):
            continue
        inf_wafer = str(inf.get("wafer_id") or "").strip()
        if inf_wafer and not _wafer_matches(inf_wafer, wafer_set):
            continue
        if not inf_wafer and wafer_set:
            continue
        out.append({
            "source": "inform",
            "scope": _scope_label(bool(inf_wafer)),
            "time": inf.get("created_at") or "",
            "author": inf.get("author") or "",
            "title": f"{inf.get('module') or 'INFO'} · {inf.get('reason') or ''}".strip(" ·"),
            "detail": inf.get("text") or "",
            "status": inf.get("flow_status") or ("completed" if inf.get("checked") else "received"),
            "category": "inform",
            "root_lot_id": root_lot_id,
            "wafer_id": inf_wafer,
            "lot_id": inf.get("lot_id") or "",
            "ref_id": inf.get("id") or "",
        })
    out.sort(key=lambda x: x.get("time") or "", reverse=True)
    return out[:300]


# ── Products / schema ──
# v8.8.3: SplitTable 의 "제품" = 오직 Base 의 ML_TABLE_* 파일로 한정.
#   - 기존에는 DB hive 테이블(FAB/INLINE/ET/EDS)과 레거시 루트 파일도 노출되어
#     실제 검색 가능한 테이블셋이 혼탁했다.
#   - 신규 요청: "검색되는 테이블셋 = ML_TABLE_~~" prefix 로 시작하는 Base 파일만.
#   - DB 하위 제품 폴더는 /fab-roots / /ml-table-match 가 따로 노출 → 오버라이드용 소스.
@router.get("/products")
def list_products():
    """v8.8.3: Base 의 ML_TABLE_* parquet 만 노출. 다른 소스는 fab_source 자동 매칭 전용.
    Source 가시성(enabled) 토글은 여전히 이 리스트 기준."""
    products = []
    try:
        base = _base_root()
        if base.exists():
            for f in sorted(base.iterdir(), key=lambda p: p.name.lower()):
                if not _is_mltable_product_file(f):
                    continue
                products.append({"name": _canonical_mltable_product_name(f.stem), "file": f.name, "size": f.stat().st_size,
                                 "root": "Base", "type": f.suffix.lower().lstrip("."), "source_type": "base_file"})
    except Exception:
        pass
    # dedup 은 불필요하지만 안정성을 위해 이름 기준 중복 제거.
    seen = set()
    dedup = []
    for p in products:
        n = p.get("name") or ""
        if n in seen:
            continue
        seen.add(n)
        dedup.append(p)
    dedup.sort(key=lambda p: (p.get("name") or ""))
    return {"products": dedup}


# v8.8.5: 사내 실데이터 구조 대응.
#   - base_root == db_root (동일 폴더).
#   - 상위 DB 폴더 이름이 `1.RAWDATA_DB*` prefix (예: `1.RAWDATA_DB`, `1.RAWDATA_DB_FAB`, `1.RAWDATA_DB_INLINE`).
#   - 제품 폴더 안은 hive 파티션: `PRODA/date=YYYYMMDD/part_*.parquet`.
#   - 동시에 Base 단일 파일 `ML_TABLE_<PROD>.parquet` 도 같은 폴더 레벨에 있음.
# v8.8.18: `1.RAWDATA_DB` 는 exact match — `_INLINE`/`_FAB` 등 suffix 붙은 변형은
#   별도 폴더로 취급 (override 소스로 자동 매칭하지 않음). 명시적 legacy 짧은 이름은 유지.
#   사용자가 직접 lot_overrides[product].fab_source 로 `1.RAWDATA_DB_INLINE/<PROD>` 를
#   지정하면 그 경로는 존중.
_RAWDATA_EXACT = "1.RAWDATA_DB"
_RAWDATA_FAB = "1.RAWDATA_DB_FAB"
_LEGACY_SHORT_ROOTS = {"FAB", "INLINE", "ET", "EDS"}

def _is_db_root_dir(p) -> bool:
    if not p.is_dir():
        return False
    n = p.name
    up = n.upper()
    if n == _RAWDATA_EXACT or up == _RAWDATA_FAB.upper():
        return True
    if up.startswith(_RAWDATA_EXACT.upper() + "_"):
        return True
    if up in _LEGACY_SHORT_ROOTS:
        return True
    return False


def _rank_db_root_name(name: str) -> tuple[int, str]:
    up = str(name or "").upper()
    if up == _RAWDATA_EXACT.upper():
        return (0, up)
    if up == _RAWDATA_FAB.upper():
        return (1, up)
    if up.startswith(_RAWDATA_EXACT.upper() + "_"):
        return (2, up)
    if "FAB" in up:
        return (3, up)
    if "INLINE" in up:
        return (4, up)
    if "ET" in up:
        return (5, up)
    if "EDS" in up:
        return (6, up)
    return (7, up)


# v8.8.22: case-insensitive 제품 폴더 lookup.
#   ML_TABLE_PRODA → DB/1.RAWDATA_DB/ProdA/ · proda/ · PRODA/ 모두 동일하게 매칭.
#   exact match 우선, 없으면 casefold 동등 비교.
def _find_ci_child(parent, name: str):
    """parent 아래에서 name 과 case-insensitive 동등한 디렉토리를 반환 (없으면 None)."""
    if not name or not parent or not parent.exists():
        return None
    try:
        exact = parent / name
        if exact.is_dir():
            return exact
    except Exception:
        pass
    try:
        target = name.casefold()
        for child in parent.iterdir():
            if child.is_dir() and child.name.casefold() == target:
                return child
    except Exception:
        pass
    return None


def _find_ci_path(root, rel: str):
    """root 아래의 쉼표 없는 상대경로 rel 을 case-insensitive 하게 찾아 반환.
    rel 이 '1.RAWDATA_DB/ProdA' 같이 슬래시 포함 시 각 세그먼트별로 CI 매칭 시도.
    파일이 아닌 경우에도 마지막 세그먼트가 .parquet/.csv 일 수 있어 is_file 도 허용.
    """
    if not rel or not root or not root.exists():
        return None
    # exact first
    try:
        exact = root / rel
        if exact.exists():
            return exact
    except Exception:
        pass
    parts = [p for p in rel.replace("\\", "/").split("/") if p]
    cur = root
    for i, seg in enumerate(parts):
        is_last = (i == len(parts) - 1)
        try:
            nxt = cur / seg
            if nxt.exists():
                cur = nxt
                continue
        except Exception:
            pass
        target = seg.casefold()
        found = None
        try:
            for child in cur.iterdir():
                if child.name.casefold() == target:
                    found = child
                    break
        except Exception:
            return None
        if found is None:
            return None
        cur = found
    return cur

def _list_db_roots():
    """사내/레거시 공통 DB 상위 폴더 후보 스캔. 반환 순서 = 우선순위.
    - 자동 연결은 `1.RAWDATA_DB` → `1.RAWDATA_DB_FAB` → 기타 `1.RAWDATA_DB_*` 순 우선.
    - 같은 우선군 안에서는 이름 오름차순.

    v8.8.17: db_root 자체가 `1.RAWDATA_DB*` 또는 그 안에 `1.RAWDATA_DB*/` 가
      없을 때도 작동하도록 확장.
        1) db_base 가 바로 `1.RAWDATA_DB*` 디렉토리면 → [db_base]
        2) db_base 아래에 `1.RAWDATA_DB*` 자식이 있으면 → 그 자식들 (기존 동작)
        3) 위 둘 다 아니고 db_base 바로 아래에 제품 폴더(parquet 포함) 가 있으면
           → [db_base] 자체를 rawdata 루트로 취급 (사용자가 rawdata 하위를 직접 지정한 경우).
    """
    db_base = _db_base()
    if not db_base.exists():
        return []
    try:
        cache_key = str(db_base.resolve())
    except Exception:
        cache_key = str(db_base)
    now = time.monotonic()
    cached = _DB_ROOTS_CACHE.get(cache_key)
    if cached and now - cached[0] < _DISCOVERY_CACHE_TTL_SEC:
        return list(cached[1])
    # Case 1: children match — legacy `Fab/` 아래의 `1.RAWDATA_DB_*` 구조를 우선 존중.
    cands = [p for p in db_base.iterdir() if _is_db_root_dir(p)]
    if cands:
        cands.sort(key=lambda p: _rank_db_root_name(p.name))
        _DB_ROOTS_CACHE[cache_key] = (now, list(cands))
        return cands
    # Case 2: db_base itself is a direct rawdata root (or a legacy short root with no rawdata children).
    if _is_db_root_dir(db_base):
        out = [db_base]
        _DB_ROOTS_CACHE[cache_key] = (now, out)
        return out
    # Case 3: db_base has no 1.RAWDATA_DB* children, but has product-like subfolders
    # (any subfolder that contains at least one parquet, possibly under hive date=* part).
    try:
        has_product = False
        for sub in db_base.iterdir():
            if not sub.is_dir():
                continue
            # Peek: is there any parquet under this subfolder (any depth ≤ 3)?
            for depth in range(3):
                pattern = "/".join(["*"] * depth) + ("/" if depth else "") + "*.parquet"
                # fall back to simple rglob
            found = False
            found = bool(_rglob_files_ci(sub, (".parquet",)))
            if found:
                has_product = True
                break
        if has_product:
            out = [db_base]
            _DB_ROOTS_CACHE[cache_key] = (now, out)
            return out
    except Exception:
        pass
    _DB_ROOTS_CACHE[cache_key] = (now, [])
    return []


@router.get("/fab-roots")
def list_fab_roots():
    """v8.7.8/v8.8.5: DB 최상위 폴더 목록. `1.RAWDATA_DB*` 접두 폴더 + 레거시 FAB/INLINE/ET/EDS 짧은 이름 모두 인식.
    Returns: {roots: [{name, products: [...], total_size}], ...}
    """
    out = []
    for root_dir in _list_db_roots():
        products = []
        total_size = 0
        try:
            for prod_dir in sorted(root_dir.iterdir()):
                if not prod_dir.is_dir():
                    continue
                has_data = False
                # hive 파티션 포함해서 탐색 — 하위 어디에든 parquet/csv 있으면 "제품" 으로 간주.
                for f in _rglob_files_ci(prod_dir, (".parquet", ".csv")):
                    has_data = True
                    try: total_size += f.stat().st_size
                    except Exception: pass
                    break
                if has_data:
                    products.append(prod_dir.name)
        except Exception:
            continue
        if products:
            out.append({"name": root_dir.name, "products": products, "total_size": total_size})
    return {"roots": out}


@router.get("/ml-table-match")
def ml_table_match(product: str = Query(...), detail: bool = False):
    """v8.7.8/v8.8.5: ML_TABLE_<PROD> 에서 PROD 추출 → `1.RAWDATA_DB*` / 레거시 짧은 이름 상위폴더 내 <PROD>/ 매칭.
    Ex) product=ML_TABLE_PRODA → {"matches": [{"root":"1.RAWDATA_DB_FAB","product":"PRODA","path":"1.RAWDATA_DB_FAB/PRODA"}, ...]}
    v8.8.3: 자동으로 선택된 fab_source (_auto_derive_fab_source) 와 현재 override 상태도 같이 반환.
    """
    pro = ""
    p = (product or "").strip()
    if p.casefold().startswith("ml_table_"):
        pro = p[len("ML_TABLE_"):].strip()
    elif "_" in p:
        pro = p.rsplit("_", 1)[-1]
    else:
        pro = p
    matches = []
    if pro:
        for root_dir in _list_db_roots():
            # v8.8.22: case-insensitive — ProdA/proda/PRODA 모두 같은 제품으로 매칭.
            sub = _find_ci_child(root_dir, pro)
            if sub is not None:
                matches.append({
                    "root": root_dir.name,
                    "product": sub.name,  # 실제 폴더 이름 (대소문자 반영)
                    "path": f"{root_dir.name}/{sub.name}",
                })
    auto_path = _auto_derive_fab_source(p)
    manual_ov = {}
    try:
        cfg = load_json(SOURCE_CFG, {}) or {}
        manual_ov = _lot_override_for(cfg, p)
    except Exception:
        pass
    manual_fs = _normalize_fab_source_path((manual_ov.get("fab_source") or "").strip())
    effective = manual_fs or auto_path
    # Default to the light resolver.  The full resolver scans FAB parquet just
    # to populate diagnostics, which made product switching feel slow.
    override_meta = _resolve_override_meta(p, include_diagnostics=False) if detail else _resolve_override_meta_light(p)
    return {
        "product": p,
        "derived_product": pro,
        "matches": matches,
        "auto_path": auto_path,
        "manual_override": bool(manual_fs),
        "effective_fab_source": effective,
        "override": override_meta,
    }


@router.get("/override-link-preview")
def override_link_preview(
    product: str = Query(...),
    fab_root: str = Query(""),
    fab_source: str = Query(""),
    limit: int = Query(5, ge=1, le=20),
):
    """Preview a manual FAB link before persisting it.

    UI flow:
      1. select DB top folder (`fab_root`) or a full `fab_source`
      2. inspect detected columns / recommended fields
      3. preview most recent fab_lot_id values
      4. save into source-config only after confirmation
    """
    p = (product or "").strip()
    if not p:
        raise HTTPException(400, "product required")

    derived = ""
    if p.casefold().startswith("ml_table_"):
        derived = p[len("ML_TABLE_"):].strip()
    elif "_" in p:
        derived = p.rsplit("_", 1)[-1]
    else:
        derived = p

    selected_root = ""
    source = _normalize_fab_source_path(fab_source)
    if fab_root and not source:
        selected_root = str(fab_root or "").strip()
        root_dir = next((r for r in _list_db_roots() if r.name.casefold() == selected_root.casefold()), None)
        if root_dir is None:
            raise HTTPException(404, f"DB top folder not found: {fab_root}")
        prod_dir = _find_ci_child(root_dir, derived) if derived else None
        if prod_dir is None:
            return {
                "product": p,
                "derived_product": derived,
                "fab_root": root_dir.name,
                "fab_source": "",
                "matched_product_dir": "",
                "columns": [],
                "latest_fab_lot_ids": [],
                "recommended": {},
                "error": f"{root_dir.name} 아래에서 제품 폴더 '{derived}' 를 찾지 못했습니다.",
            }
        source = f"{root_dir.name}/{prod_dir.name}"
    elif source:
        selected_root = source.split("/", 1)[0]

    if not source:
        return {
            "product": p,
            "derived_product": derived,
            "fab_root": selected_root,
            "fab_source": "",
            "matched_product_dir": "",
            "columns": [],
            "latest_fab_lot_ids": [],
            "recommended": {},
            "error": "fab_root 또는 fab_source 가 필요합니다.",
        }

    raw_lf = _scan_fab_source_raw(source)
    fab_lf = _scan_fab_source(source)
    if fab_lf is None:
        return {
            "product": p,
            "derived_product": derived,
            "fab_root": selected_root,
            "fab_source": source,
            "matched_product_dir": source.split("/", 1)[1] if "/" in source else "",
            "columns": [],
            "raw_columns": [],
            "column_aliases": {},
            "schema_mode": "unknown",
            "latest_fab_lot_ids": [],
            "recommended": {},
            "error": f"소스를 읽지 못했습니다: {source}",
        }

    try:
        main_names = _scan_parquet_compat(str(_product_path(p))).collect_schema().names()
    except Exception:
        main_names = []
    fab_lf, fab_names = _ci_align_fab_to_main(fab_lf, main_names)
    if not fab_names:
        try:
            fab_names = fab_lf.collect_schema().names()
        except Exception:
            fab_names = []
    try:
        raw_names = raw_lf.collect_schema().names() if raw_lf is not None else []
    except Exception:
        raw_names = []
    column_aliases = _detect_source_column_aliases(raw_names, fab_names)
    schema_mode = "adapted" if column_aliases else "raw"

    root_col, wf_col = find_lot_wafer_cols(fab_names)
    fab_col = _pick_first_present_ci(_FAB_COL_CANDIDATES, fab_names) or ""
    ts_col = _pick_ts_col(fab_names) or ""
    join_keys = _default_override_join_keys(main_names, fab_names)

    latest_fab_lot_ids: list[str] = []
    if fab_col and fab_col in fab_names:
        try:
            q = fab_lf
            if ts_col and ts_col in fab_names:
                q = q.sort(ts_col, descending=True, nulls_last=True)
            latest = (
                q.select([pl.col(fab_col).cast(_STR, strict=False)])
                 .filter(pl.col(fab_col).is_not_null() & (pl.col(fab_col).cast(_STR, strict=False) != ""))
                 .unique(maintain_order=True)
                 .head(limit)
                 .collect()
            )
            latest_fab_lot_ids = [str(v) for v in latest[fab_col].to_list() if v not in (None, "")]
        except Exception:
            latest_fab_lot_ids = []

    recommended_override_cols = []
    for c in list(_DEFAULT_OVERRIDE_COLS) + ([fab_col] if fab_col else []):
        actual = _resolve_source_col_name(c, fab_names)
        if actual and actual not in recommended_override_cols and actual not in join_keys:
            recommended_override_cols.append(actual)
    recommended_override_cols = [
        _prefer_raw_schema_name(c, raw_names, fab_names) for c in recommended_override_cols
    ]
    join_keys_preview = [_prefer_raw_schema_name(k, raw_names, fab_names) for k in join_keys]

    return {
        "product": p,
        "derived_product": derived,
        "fab_root": selected_root,
        "fab_source": source,
        "matched_product_dir": source.split("/", 1)[1] if "/" in source else "",
        "columns": fab_names,
        "raw_columns": raw_names or fab_names,
        "column_aliases": column_aliases,
        "schema_mode": schema_mode,
        "latest_fab_lot_ids": latest_fab_lot_ids,
        "recommended": {
            "root_col": _prefer_raw_schema_name(root_col or "", raw_names, fab_names),
            "wf_col": _prefer_raw_schema_name(wf_col or "", raw_names, fab_names),
            "fab_col": _prefer_raw_schema_name(fab_col, raw_names, fab_names),
            "ts_col": _prefer_raw_schema_name(ts_col, raw_names, fab_names),
            "join_keys": join_keys_preview,
            "override_cols": recommended_override_cols,
        },
        "recommended_runtime": {
            "root_col": root_col or "",
            "wf_col": wf_col or "",
            "fab_col": fab_col,
            "ts_col": ts_col,
            "join_keys": join_keys,
            "override_cols": [
                _resolve_source_col_name(c, fab_names) for c in recommended_override_cols
                if _resolve_source_col_name(c, fab_names)
            ],
        },
        "error": None,
    }


# v8.8.26: override 조인이 왜 실패했는지 진단용 — main vs fab 스키마/샘플/조인 결과를
#   한 번의 호출로 끝까지 보여줘 FE/운영자가 root cause 를 즉시 파악할 수 있게.
@router.get("/override-debug")
def override_debug(product: str = Query(...)):
    """진단 엔드포인트. override 조인이 비어있게 나올 때 어디서 문제가 났는지
    한 번에 확인하기 위한 용도. 반환:
      - meta: _resolve_override_meta (fab_source / join_keys / override_cols_*)
      - main_schema / main_schema_types (첫 30개)
      - fab_raw_schema / fab_raw_types (CI align 전, 첫 30개)
      - fab_aligned_schema (CI align 후, 첫 30개)
      - join_keys_resolved (main/fab 양쪽에 존재하는 것)
      - main_sample / fab_sample (join_keys + override_cols 각 3행)
      - main_lot_nonnull (main 의 root_lot_id 계열 컬럼 non-null 카운트)
      - join_probe_row_count (슬라이스 조인 결과 행 수)
    """
    out: dict = {"product": product, "error": None}
    try:
        fp = _product_path(product)
        if fp.suffix.lower() == ".csv":
            main_lf = _cast_cats_lazy(pl.scan_csv(str(fp), infer_schema_length=5000))
        else:
            main_lf = _cast_cats_lazy(_scan_parquet_compat(str(fp)))
        main_schema = main_lf.collect_schema()
        main_names = main_schema.names()
        out["main_schema"] = main_names[:30]
        out["main_schema_types"] = [str(main_schema[n]) for n in main_names[:30]]
    except Exception as e:
        out["error"] = f"main 스키마 조회 실패: {type(e).__name__}: {e}"
        return out

    meta = _resolve_override_meta(product)
    out["meta"] = meta
    fab_source = (meta.get("fab_source") or "").strip()
    if not fab_source:
        out["note"] = "fab_source 비어있음 → override off."
        return out

    fab_lf_raw = _scan_fab_source(fab_source)
    if fab_lf_raw is None:
        out["error"] = "_scan_fab_source 가 None 반환."
        return out
    try:
        raw_schema = fab_lf_raw.collect_schema()
        raw_names = raw_schema.names()
        out["fab_raw_schema"] = raw_names[:30]
        out["fab_raw_types"] = [str(raw_schema[n]) for n in raw_names[:30]]
    except Exception as e:
        out["error"] = f"fab raw 스키마 조회 실패: {type(e).__name__}: {e}"
        return out

    fab_lf_aligned, aligned_names = _ci_align_fab_to_main(fab_lf_raw, main_names)
    try:
        aligned_names = fab_lf_aligned.collect_schema().names()
    except Exception as e:
        out["align_error"] = f"{type(e).__name__}: {e}"
    out["fab_aligned_schema"] = aligned_names[:30]

    join_keys = list(meta.get("join_keys") or [])
    join_keys_resolved = [k for k in join_keys
                          if k in main_names and k in aligned_names]
    out["join_keys_resolved"] = join_keys_resolved

    override_cols = [c for c in (meta.get("override_cols_present") or [])
                     if c not in join_keys_resolved]
    out["override_cols_effective"] = override_cols

    # 샘플 행 — 에러나도 반환값은 유지.
    try:
        keep_main = [c for c in join_keys_resolved if c in main_names]
        if keep_main:
            ms = main_lf.select([pl.col(c).cast(_STR, strict=False) for c in keep_main]) \
                        .head(3).collect()
            out["main_sample"] = ms.to_dicts()
        else:
            out["main_sample"] = []
    except Exception as e:
        out["main_sample_error"] = f"{type(e).__name__}: {e}"
    try:
        keep_fab = list(dict.fromkeys(join_keys_resolved + override_cols[:5]))
        keep_fab = [c for c in keep_fab if c in aligned_names]
        if keep_fab:
            fs = fab_lf_aligned.select([pl.col(c).cast(_STR, strict=False) for c in keep_fab]) \
                               .head(3).collect()
            out["fab_sample"] = fs.to_dicts()
        else:
            out["fab_sample"] = []
    except Exception as e:
        out["fab_sample_error"] = f"{type(e).__name__}: {e}"

    # main lot 계열 컬럼의 non-null 카운트 (root_lot_id / lot_id CI).
    try:
        lot_candidates = []
        for n in main_names:
            if n.casefold() in ("root_lot_id", "lot_id"):
                lot_candidates.append(n)
        nonnull = {}
        if lot_candidates:
            row = main_lf.select(
                [pl.col(c).cast(_STR, strict=False).is_not_null().sum().alias(c)
                 for c in lot_candidates]
            ).collect()
            for c in lot_candidates:
                try:
                    nonnull[c] = int(row[c][0])
                except Exception:
                    nonnull[c] = None
        out["main_lot_nonnull"] = nonnull
    except Exception as e:
        out["main_lot_nonnull_error"] = f"{type(e).__name__}: {e}"

    # probe join: 작은 슬라이스로 실제 조인 결과가 나오는지 확인.
    try:
        if join_keys_resolved and override_cols:
            probe = _scan_product(product).select(
                join_keys_resolved + override_cols[:3]
            ).head(20).collect()
            out["join_probe_row_count"] = int(probe.height)
            out["join_probe_sample"] = probe.head(3).to_dicts()
        else:
            out["join_probe_row_count"] = 0
            out["join_probe_note"] = "join_keys_resolved 또는 override_cols 가 비어있음."
    except Exception as e:
        out["join_probe_error"] = f"{type(e).__name__}: {e}"

    return out



@router.get("/schema")
def get_schema(product: str = Query(...), root_lot_id: str = Query(""),
               fab_lot_id: str = Query(""), wafer_ids: str = Query("")):
    """v8.8.23: 오버라이드 조인을 포함한 실제 view 컬럼과 동일한 스키마를 반환.
       기존에는 ML_TABLE 원본 parquet 컬럼만 반환 → CUSTOM 선택 pool 에 root_lot_id 등
       오버라이드 컬럼이 들어가지 못해 검색/필터 드롭다운에서 누락. `_scan_product` 로
       post-join LazyFrame 스키마를 계산하고, `override_cols` (실제 join 성공한 오버라이드 컬럼)
       을 별도 필드로도 내려 FE 가 '오버라이드 제공' 뱃지를 표시할 수 있게 한다.
    """
    try:
        lf = _scan_product(product, root_lot_id=root_lot_id,
                           fab_lot_id=fab_lot_id, wafer_ids=wafer_ids)
        schema = lf.collect_schema()
        cols = [{"name": n, "dtype": str(d)} for n, d in schema.items()]
    except Exception:
        # fallback — 조인 실패해도 원본 컬럼은 반환.
        fp = _product_path(product)
        if fp.suffix.lower() == ".csv":
            lf = pl.scan_csv(str(fp), infer_schema_length=5000)
        else:
            lf = _scan_parquet_compat(str(fp))
        cols = [{"name": n, "dtype": str(d)} for n, d in lf.schema.items()]
    # 오버라이드에서 실제로 join 된 컬럼 목록 (FE 가 검색 pool 에서 '숨김 해제' 할 기준).
    override_cols_present: list = []
    try:
        meta = _resolve_override_meta(product, include_diagnostics=False)
        if meta.get("enabled"):
            override_cols_present = list(meta.get("override_cols_present") or [])
    except Exception:
        pass
    return {
        "columns": cols,
        "total": len(cols),
        "override_cols_present": override_cols_present,
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


# FAB/INLINE/ET datalake 진단 엔드포인트.
#   FAB 는 wafer 단위 공정이력이고, INLINE/ET 는 item/value 계측 long format 이다.
#   FAB preview 는 canonical 공정이력 컬럼을 보여주고, INLINE/ET 는 wide pivot sample 을 보여준다.
@router.get("/long-items")
def long_items(source: str = Query(..., description="fab|inline|et"),
               product: str = Query(..., description="PRODA 등 (ML_TABLE_ prefix 없이)")):
    """INLINE/ET item_id 레지스트리. FAB 는 공정이력이라 item_id 목록이 없을 수 있다."""
    from core.long_pivot import scan_long_fab, scan_long_inline, scan_long_et, list_items
    prod = product.replace("ML_TABLE_", "").strip()
    db_root = _db_base()
    lf = None
    if source == "fab":
        lf = scan_long_fab(prod, db_root)
    elif source == "inline":
        lf = scan_long_inline(prod, db_root)
    elif source == "et":
        lf = scan_long_et(prod, db_root)
    else:
        raise HTTPException(400, "source must be fab|inline|et")
    if lf is None:
        return {"source": source, "product": prod, "items": [],
                "note": f"hive 경로가 없음: {db_root} 에 1.RAWDATA_DB_{source.upper()}/{prod}/ 확인"}
    items = list_items(lf)
    note = "FAB 는 wafer 단위 공정이력이라 item_id 레지스트리가 비어 있을 수 있습니다." if source == "fab" and not items else ""
    return {"source": source, "product": prod, "items": items, "note": note}


@router.get("/long-wide-preview")
def long_wide_preview(source: str = Query(..., description="fab|inline|et"),
                      product: str = Query(...),
                      limit: int = Query(20)):
    """FAB 공정이력 또는 INLINE/ET pivot 결과 상위 N 행 미리보기."""
    from core.long_pivot import (scan_long_fab, scan_long_inline, scan_long_et,
                                  pivot_fab_wide, pivot_inline_wafer, pivot_et_wafer)
    prod = product.replace("ML_TABLE_", "").strip()
    db_root = _db_base()
    if source == "fab":
        lf = scan_long_fab(prod, db_root); pivot = pivot_fab_wide
    elif source == "inline":
        lf = scan_long_inline(prod, db_root); pivot = pivot_inline_wafer
    elif source == "et":
        lf = scan_long_et(prod, db_root); pivot = pivot_et_wafer
    else:
        raise HTTPException(400, "source must be fab|inline|et")
    if lf is None:
        return {"source": source, "product": prod, "rows": [], "columns": [],
                "note": "원천 hive 경로 미존재"}
    wide = pivot(lf).head(limit)
    return {
        "source": source, "product": prod,
        "columns": wide.columns,
        "rows": wide.to_dicts(),
        "total_preview": wide.height,
    }


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
    """Proxy `<db_root>/_uniques.json` verbatim for feature-select catalogs.

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
    # v8.8.21: 응답 단에서도 root:~~ 남은 값은 표시 안 되게 정리.
    _migrate_legacy_root_prefix(cfg)
    return cfg

class SourceConfigReq(BaseModel):
    enabled: List[str] = []
    lot_overrides: dict = {}  # v8.4.4


def _normalize_fab_source_path(v: str) -> str:
    s = str(v or "").strip().replace("\\", "/")
    if not s:
        return ""
    while s.startswith("./"):
        s = s[2:]
    if s.lower().startswith("db/"):
        s = s[3:]
    elif s.lower().startswith("base/"):
        s = s[5:]
    if s.startswith("/"):
        s = s.lstrip("/")
    return s

def _migrate_legacy_root_prefix(cfg: dict) -> dict:
    """Normalize stored fab_source values to db-relative paths."""
    try:
        lo = cfg.get("lot_overrides") or {}
        for _p, _ov in list(lo.items()):
            if not isinstance(_ov, dict):
                continue
            fs = str(_ov.get("fab_source") or "").strip()
            if fs.startswith("root:"):
                _ov["fab_source"] = ""
            else:
                _ov["fab_source"] = _normalize_fab_source_path(fs)
    except Exception:
        pass
    return cfg


@router.post("/source-config/save")
def save_source_config(req: SourceConfigReq):
    cur = load_json(SOURCE_CFG, {"enabled": [], "lot_overrides": {}})
    cur["enabled"] = req.enabled
    if req.lot_overrides:
        cur.setdefault("lot_overrides", {}).update(req.lot_overrides)
    # v8.8.21: legacy root:~~ 삭제.
    _migrate_legacy_root_prefix(cur)
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
        st = fp.stat()
        key = str(fp.resolve())
        cached = _CSV_ROWS_CACHE.get(key)
        if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
            return [dict(row) for row in cached[2]]
        with open(fp, "r", encoding="utf-8") as f:
            rows = list(csv_mod.DictReader(f))
        _CSV_ROWS_CACHE[key] = (st.st_mtime, st.st_size, [dict(row) for row in rows])
        return rows
    except Exception:
        return []


def _canonical_product_name(product: str) -> str:
    raw = str(product or "").strip()
    if raw.upper().startswith("ML_TABLE_"):
        return raw[len("ML_TABLE_"):].strip()
    return raw


def _mltable_schema_columns(product: str, prefix: str = "") -> list[str]:
    core = _canonical_product_name(product)
    if not core:
        return []
    names = [f"ML_TABLE_{core}.parquet"]
    for alias in sorted(_product_aliases(core)):
        if alias.startswith("ML_TABLE_"):
            names.append(f"{alias}.parquet")
        else:
            names.append(f"ML_TABLE_{alias}.parquet")
    seen_names = []
    for name in names:
        if name not in seen_names:
            seen_names.append(name)
    pref = str(prefix or "").strip().upper()
    for name in seen_names:
        fp = _base_root() / name
        if not fp.is_file():
            continue
        try:
            st = fp.stat()
            key = str(fp.resolve())
            cached = _SCHEMA_COLUMNS_CACHE.get(key)
            if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
                cols = list(cached[2])
            else:
                cols = _scan_parquet_compat(str(fp)).collect_schema().names()
                _SCHEMA_COLUMNS_CACHE[key] = (st.st_mtime, st.st_size, list(cols))
        except Exception:
            continue
        if pref:
            return [c for c in cols if str(c).upper().startswith(pref + "_")]
        return list(cols)
    return []


def _stage_major(text: str):
    tail = str(text or "").strip()
    if "_" in tail and tail.split("_", 1)[0].upper() in {"KNOB", "INLINE", "VM"}:
        tail = tail.split("_", 1)[1].strip()
    m = _re.match(r"^\s*(\d+(?:\.\d+)?)", tail)
    if not m:
        return None
    try:
        return int(float(m.group(1)))
    except Exception:
        return None


def _dedup_list(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        s = str(value or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _stage_steps_by_major(product: str) -> dict[int, list[dict]]:
    matching = _load_csv_rows(_base_root() / "step_matching.csv")
    sm = _sch("step_matching")
    prod_aliases = _product_aliases(product)
    exact_product = _canonical_product_name(product).upper()
    exact_has_numeric = False
    for r in matching:
        if str(r.get(sm.get("product_col", "product")) or "").strip().upper() != exact_product:
            continue
        if _stage_major(r.get(sm.get("func_step_col", "func_step")) or "") is not None:
            exact_has_numeric = True
            break
    out: dict[int, list[dict]] = {}
    seen: dict[int, set[tuple[str, str]]] = {}
    for r in matching:
        p_col = sm.get("product_col", "product")
        row_prod = str(r.get(p_col) or "").strip()
        row_prod_u = row_prod.upper()
        if exact_has_numeric:
            if row_prod_u != exact_product:
                continue
        elif prod_aliases and row_prod and row_prod_u not in prod_aliases:
            continue
        fs = (r.get(sm.get("func_step_col", "func_step")) or "").strip()
        sid = (r.get(sm.get("step_id_col", "step_id")) or r.get("raw_step_id") or "").strip()
        major = _stage_major(fs)
        if major is None or not fs:
            continue
        module = (
            r.get(sm.get("module_col", "module"))
            or r.get("area")
            or r.get("module")
            or classify_process_area(fs)
            or ""
        )
        item = {
            "func_step": fs,
            "step_id": sid,
            "module": str(module or "").strip(),
            "step_class": str(r.get("step_class") or "").strip(),
        }
        key = (item["func_step"], item["step_id"])
        bucket_seen = seen.setdefault(major, set())
        if key in bucket_seen:
            continue
        bucket_seen.add(key)
        out.setdefault(major, []).append(item)
    return out


def _stage_token(text: str) -> str:
    tail = str(text or "").strip()
    if "_" in tail and tail.split("_", 1)[0].upper() in {"KNOB", "INLINE", "VM"}:
        tail = tail.split("_", 1)[1].strip()
    tail = _re.sub(r"^\s*\d+(?:\.\d+)?[A-Za-z]?\s*", "", tail).strip()
    return tail


def _norm_stage_text(text: str) -> str:
    return _re.sub(r"[^A-Z0-9]+", "", str(text or "").upper())


def _stage_aliases(token: str) -> list[str]:
    key = _norm_stage_text(token)
    aliases = {
        "WELL": ["WELL", "NWELL", "PWELL"],
        "VTN": ["VT", "VTN", "VTP", "WELL"],
        "GATEOX": ["GATEOX", "GATE_OX", "GATE", "HKMG"],
        "PC": ["PC", "POLYCONTACT", "GATE"],
        "SDEPI": ["SDEPI", "SD_EPI", "EPI"],
        "SILICIDE": ["SILICIDE", "SILI"],
        "CONTACT": ["CONTACT", "CT", "MOL"],
        "M0": ["MOL", "M0", "V0"],
        "VIA0": ["VIA0", "V0", "MOL"],
        "M1": ["BEOLM1", "M1"],
        "VIA1": ["VIA1", "BEOLM2", "M2"],
        "M2": ["BEOLM2", "M2"],
        "VIA2": ["VIA2", "BEOLM3", "M3"],
        "M3": ["BEOLM3", "M3"],
        "VIA3": ["VIA3", "BEOLM4", "M4"],
        "M4": ["BEOLM4", "M4"],
        "PAD": ["PAD", "PASSIVATION"],
        "PASSIVATION": ["PASSIVATION", "PAD"],
        "ETESTPREP": ["ETEST", "ET", "SORT"],
        "RELIABILITY": ["RELIABILITY", "REL"],
        "SORT": ["SORT", "ET"],
    }
    raw = [key]
    raw.extend(aliases.get(key, []))
    return _dedup_list([_norm_stage_text(x) for x in raw])


def _stage_steps_for_tail(tail: str, steps_by_major: dict[int, list[dict]]) -> list[dict]:
    token = _stage_token(tail)
    aliases = [a for a in _stage_aliases(token) if a]
    all_steps = [item for bucket in steps_by_major.values() for item in bucket]
    def _collect(match_fn):
        hits: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for item in all_steps:
            if not match_fn(item):
                continue
            key = (item.get("func_step", ""), item.get("step_id", ""))
            if key in seen:
                continue
            seen.add(key)
            hits.append(item)
        return hits

    major = _stage_major(tail)
    stage_hits = _collect(lambda item:
        str(item.get("step_class") or "").strip().lower() == "stage"
        and _stage_major(item.get("func_step", "")) == major
        and _norm_stage_text(_stage_token(item.get("func_step", ""))) == _norm_stage_text(token)
    )
    if stage_hits:
        return stage_hits

    # Prefer module-level matches. This keeps e.g. M1 from matching M2_OVL_M1.
    module_hits = _collect(lambda item: any(
        alias == _norm_stage_text(item.get("module", ""))
        or alias in _norm_stage_text(item.get("module", ""))
        for alias in aliases
    ))
    if module_hits:
        return module_hits

    def _func_match(item):
        body = _norm_stage_text(_stage_token(item.get("func_step", "")))
        return any(alias and body.startswith(alias) for alias in aliases)

    func_hits = _collect(_func_match)
    if func_hits:
        return func_hits
    if major is not None and major <= 8:
        return list(steps_by_major.get(major, []))
    return []


def _inferred_stage_meta(product: str, prefix: str) -> dict[str, dict]:
    pref = str(prefix or "").strip().upper()
    cols = _mltable_schema_columns(product, pref)
    if not cols:
        return {}
    steps_by_major = _stage_steps_by_major(product)
    out: dict[str, dict] = {}
    for full in cols:
        _, _, tail = str(full).partition("_")
        tail = tail.strip()
        if not tail:
            continue
        major = _stage_major(tail)
        steps = _stage_steps_for_tail(tail, steps_by_major)
        step_ids = _dedup_list([x.get("step_id", "") for x in steps])
        function_steps = _dedup_list([x.get("func_step", "") for x in steps])
        modules = _dedup_list([x.get("module", "") for x in steps])
        if pref == "KNOB":
            groups = [{
                "func_step": tail,
                "rule_order": major or 0,
                "ppid": "",
                "operator": "",
                "category": modules[0] if len(modules) == 1 else "",
                "step_ids": step_ids,
                "modules": modules,
                "module": modules[0] if len(modules) == 1 else "",
                "inferred": True,
            }]
            meta = {
                "groups": groups,
                "label": f"{tail} ({'/'.join(step_ids)})" if step_ids else tail,
                "modules": modules,
                "inferred": True,
            }
        else:
            group = {
                "function_step": tail,
                "step_id": step_ids[0] if len(step_ids) == 1 else "",
                "step_ids": step_ids,
                "function_steps": function_steps,
                "modules": modules,
                "module": modules[0] if len(modules) == 1 else "",
                "inferred": True,
            }
            if pref == "INLINE":
                group.update({"item_id": tail, "item_desc": tail})
                meta = {
                    "item_id": tail,
                    "item_desc": tail,
                    "step_id": step_ids[0] if len(step_ids) == 1 else "",
                    "step_ids": step_ids,
                    "function_step": tail,
                    "function_steps": function_steps,
                    "groups": [group],
                    "label": tail,
                    "sub": "/".join(step_ids) if step_ids else tail,
                    "inferred": True,
                }
            else:
                group.update({"feature_name": tail, "step_desc": tail})
                meta = {
                    "step_desc": tail,
                    "step_id": step_ids[0] if len(step_ids) == 1 else "",
                    "step_ids": step_ids,
                    "function_step": tail,
                    "function_steps": function_steps,
                    "groups": [group],
                    "label": tail,
                    "sub": "/".join(step_ids) if step_ids else tail,
                    "inferred": True,
                }
        out.setdefault(tail, meta)
        out.setdefault(str(full), meta)
    return out


def _build_knob_meta(product: str = "") -> dict:
    base = _base_root()
    matching = _load_csv_rows(base / "step_matching.csv")
    knob_rules = _load_csv_rows(base / "knob_ppid.csv")
    # v8.8.10: 역할→컬럼명 매핑 soft-landing. 사내 CSV 의 컬럼 이름이 달라도 schema 만 바꾸면 됨.
    sm = _sch("step_matching")
    km = _sch("knob_ppid")
    prod_aliases = _product_aliases(product)

    # func_step → [{step_id,module}, ...] (ordered, dedup)
    step_map: dict[str, list[dict]] = {}
    for r in matching:
        # product 컬럼이 있으면 필터, 없으면 공용 매핑으로 취급
        p_col = sm.get("product_col", "product")
        row_prod = str(r.get(p_col) or "").strip()
        if prod_aliases and row_prod and row_prod.upper() not in prod_aliases:
            continue
        fs = (r.get(sm.get("func_step_col", "func_step")) or "").strip()
        sid = (r.get(sm.get("step_id_col", "step_id")) or r.get("raw_step_id") or "").strip()
        if not fs or not sid:
            continue
        module = (
            r.get(sm.get("module_col", "module"))
            or r.get("area")
            or r.get("module")
            or classify_process_area(fs)
            or ""
        )
        module = str(module or "").strip()
        lst = step_map.setdefault(fs, [])
        if not any(str(item.get("step_id") or "").strip() == sid for item in lst):
            lst.append({"step_id": sid, "module": module})

    # feature_name → groups (sorted by rule_order)
    feats: dict[str, list[dict]] = {}
    for r in knob_rules:
        use_val = (r.get(km.get("use_col", "use")) or "Y").strip().upper()
        if use_val == "N":
            continue
        p_col = km.get("product_col", "product")
        row_prod = str(r.get(p_col) or "").strip()
        if prod_aliases and row_prod and row_prod.upper() not in prod_aliases:
            continue
        fname = (r.get(km.get("feature_col", "feature_name")) or "").strip()
        fstep = (r.get(km.get("func_step_col", "function_step")) or "").strip()
        if not fname or not fstep:
            continue
        try:
            order = int(r.get(km.get("rule_order_col", "rule_order")) or 0)
        except Exception:
            order = 0
        feats.setdefault(fname, []).append({
            "func_step": fstep,
            "rule_order": order,
            "ppid": (r.get(km.get("ppid_col", "ppid")) or "").strip(),
            "operator": (r.get(km.get("operator_col", "operator")) or "").strip(),
            "category": (r.get(km.get("category_col", "category")) or "").strip(),
            "step_ids": [str(x.get("step_id") or "").strip() for x in step_map.get(fstep, []) if str(x.get("step_id") or "").strip()],
            "modules": [str(x.get("module") or "").strip() for x in step_map.get(fstep, []) if str(x.get("module") or "").strip()],
        })

    # Sort each feature's groups by rule_order + build a human label
    out: dict[str, dict] = {}
    for fname, groups in feats.items():
        groups.sort(key=lambda g: g["rule_order"])
        parts: list[str] = []
        feat_modules: list[str] = []
        for i, g in enumerate(groups):
            sids = g["step_ids"]
            mods = []
            for mod in (g.get("modules") or []):
                mod = str(mod or "").strip()
                if mod and mod not in mods:
                    mods.append(mod)
                if mod and mod not in feat_modules:
                    feat_modules.append(mod)
            g["module"] = mods[0] if len(mods) == 1 else ""
            g["modules"] = mods
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
            "modules": feat_modules,
        }
    for key, meta in _inferred_stage_meta(product, "KNOB").items():
        out.setdefault(key, meta)
    return out


# v8.7.5/v8.8.10: INLINE / VM_ prefix 매칭 메타 — schema 매핑 기반.
def _build_inline_meta(product: str = "") -> dict:
    """inline_matching.csv (schema: item_id/process_id, optional step_id, product)."""
    base = _base_root()
    rows = _load_csv_rows(base / "inline_matching.csv")
    im = _sch("inline_matching")
    prod_aliases = _product_aliases(product)
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        p_col = im.get("product_col", "product")
        row_prod = str(r.get(p_col) or "").strip()
        if prod_aliases and row_prod and row_prod.upper() not in prod_aliases:
            continue
        iid = (r.get(im.get("item_id_col", "item_id")) or "").strip()
        sid = (r.get(im.get("step_id_col", "step_id")) or "").strip()
        process_id = (r.get(im.get("process_id_col", "process_id")) or "").strip()
        desc = (r.get(im.get("item_desc_col", "item_desc")) or "").strip()
        func_step = (r.get("function_step") or "").strip()
        if not iid:
            continue
        grouped.setdefault(iid, []).append({
            "step_id": sid,
            "process_id": process_id,
            "item_id": iid,
            "item_desc": desc,
            "function_step": func_step,
        })
    out: dict[str, dict] = {}
    for iid, items in grouped.items():
        dedup = []
        seen = set()
        for item in items:
            key = (item.get("function_step", ""), item.get("step_id", ""), item.get("item_desc", ""))
            if key in seen:
                continue
            seen.add(key)
            dedup.append(item)
        step_ids = [x["step_id"] for x in dedup if x.get("step_id")]
        item_desc = next((x.get("item_desc") for x in dedup if x.get("item_desc")), "") or iid
        function_steps = [x["function_step"] for x in dedup if x.get("function_step")]
        process_ids = [x["process_id"] for x in dedup if x.get("process_id")]
        out[iid] = {
            "item_id": iid,
            "item_desc": item_desc,
            "process_id": process_ids[0] if len(process_ids) == 1 else "",
            "process_ids": process_ids,
            "step_id": step_ids[0] if len(step_ids) == 1 else "",
            "step_ids": step_ids,
            "function_step": function_steps[0] if len(function_steps) == 1 else "",
            "function_steps": function_steps,
            "groups": dedup,
            "label": item_desc,
            "sub": "/".join(step_ids) if step_ids else iid,
        }
    for key, meta in _inferred_stage_meta(product, "INLINE").items():
        out.setdefault(key, meta)
    return out


def _build_vm_meta(product: str = "") -> dict:
    """v8.8.7/v8.8.10: vm_matching.csv 컬럼 매핑 schema 기반."""
    base = _base_root()
    rows = _load_csv_rows(base / "vm_matching.csv")
    vm = _sch("vm_matching")
    prod_aliases = _product_aliases(product)
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        p_col = vm.get("product_col", "product")
        row_prod = str(r.get(p_col) or "").strip()
        if prod_aliases and row_prod and row_prod.upper() not in prod_aliases:
            continue
        fname = (r.get(vm.get("feature_col", "feature_name")) or r.get(vm.get("step_desc_col", "step_desc")) or "").strip()
        sd = (r.get(vm.get("step_desc_col", "step_desc")) or "").strip()
        sid = (r.get(vm.get("step_id_col", "step_id")) or "").strip()
        func_step = (r.get("function_step") or "").strip()
        if not fname:
            continue
        grouped.setdefault(fname, []).append({
            "feature_name": fname,
            "step_desc": sd,
            "step_id": sid,
            "function_step": func_step,
        })
    out: dict[str, dict] = {}
    for fname, items in grouped.items():
        dedup = []
        seen = set()
        for item in items:
            key = (item.get("function_step", ""), item.get("step_id", ""), item.get("step_desc", ""))
            if key in seen:
                continue
            seen.add(key)
            dedup.append(item)
        step_ids = [x["step_id"] for x in dedup if x.get("step_id")]
        step_desc = next((x.get("step_desc") for x in dedup if x.get("step_desc")), "") or fname
        function_steps = [x["function_step"] for x in dedup if x.get("function_step")]
        out[fname] = {
            "step_desc": step_desc,
            "step_id": step_ids[0] if len(step_ids) == 1 else "",
            "step_ids": step_ids,
            "function_step": function_steps[0] if len(function_steps) == 1 else "",
            "function_steps": function_steps,
            "groups": dedup,
            "label": step_desc or fname,
            "sub": "/".join(step_ids) if step_ids else fname,
        }
    for key, meta in _inferred_stage_meta(product, "VM").items():
        out.setdefault(key, meta)
    return out


def _virtual_columns_for_prefix(product: str, prefix: str) -> list[str]:
    pref = str(prefix or "").strip().upper()
    if not pref:
        return []
    out: list[str] = []
    seen: set[str] = set()

    def _push(name: str, pref_name: str):
        raw = str(name or "").strip()
        if not raw:
            return
        full = raw if raw.upper().startswith(pref_name + "_") else f"{pref_name}_{raw}"
        if full not in seen:
            seen.add(full)
            out.append(full)

    try:
        if pref == "KNOB":
            for key in (_build_knob_meta(product) or {}).keys():
                _push(key, "KNOB")
        elif pref == "INLINE":
            for key in (_build_inline_meta(product) or {}).keys():
                _push(key, "INLINE")
        elif pref == "VM":
            for key in (_build_vm_meta(product) or {}).keys():
                _push(key, "VM")
    except Exception:
        return out
    return out


@router.get("/inline-meta")
def inline_meta(product: str = Query("")):
    """v8.7.5/v8.8.15: INLINE prefix 항목 매칭 메타. product 필터 추가."""
    return {"items": _build_inline_meta(product)}


@router.get("/vm-meta")
def vm_meta(product: str = Query("")):
    """v8.7.5/v8.8.7: VM_ prefix 항목 매칭 메타. product 필터 추가."""
    return {"items": _build_vm_meta(product)}


@router.post("/infer-step-mapping")
def infer_step_mapping(request: Request, product: str = Query(...), kind: str = Query("inline")):
    """v8.8.33: FAB 공정이력을 활용해 INLINE / VM 의 step_id 자동 추론.
    보안: admin 또는 page_admin('splittable') 만 실행 가능 (rulebook CSV 쓰기 보호)."""
    from core.auth import current_user, is_page_admin
    me = current_user(request)
    if me.get("role") != "admin" and not is_page_admin(me.get("username") or "", "splittable"):
        raise HTTPException(403, "admin or splittable page_admin only")
    # 전략: INLINE 의 (lot_id, wafer_id, item_id, tkout_time/time) 에 대해 FAB 에서
    #   같은 (lot_id, wafer_id) 의 step_id 중 INLINE 측정 직전의 step_id 매칭.
    # 결과를 inline_matching.csv (or vm_matching.csv) 에 upsert. 수동 편집분은 보존.
    import polars as pl
    if not product:
        raise HTTPException(400, "product required")
    if kind not in ("inline", "vm"):
        raise HTTPException(400, "kind must be inline|vm")
    db_root = PATHS.db_root
    fab_root = db_root / "1.RAWDATA_DB_FAB" / product
    src_root = db_root / ("1.RAWDATA_DB_INLINE" if kind == "inline" else "1.RAWDATA_DB_VM") / product
    if not fab_root.is_dir():
        raise HTTPException(404, f"FAB folder not found: {fab_root}")
    if not src_root.is_dir():
        raise HTTPException(404, f"{kind.upper()} folder not found: {src_root}")
    fab_files = _rglob_files_ci(fab_root, (".parquet",))[-30:]
    src_files = _rglob_files_ci(src_root, (".parquet",))[-30:]
    if not fab_files or not src_files:
        raise HTTPException(404, "no parquet files")
    try:
        fab_lf = _scan_parquet_compat([str(f) for f in fab_files], hive_partitioning=True)
        src_lf = _scan_parquet_compat([str(f) for f in src_files], hive_partitioning=True)
    except Exception as e:
        raise HTTPException(500, f"scan error: {e}")
    fab_schema = fab_lf.collect_schema().names()
    src_schema = src_lf.collect_schema().names()
    if "step_id" not in fab_schema:
        raise HTTPException(400, "FAB has no step_id column")
    if "item_id" not in src_schema:
        raise HTTPException(400, f"{kind.upper()} has no item_id column")
    fab_time_col = "time" if "time" in fab_schema else ("tkout_time" if "tkout_time" in fab_schema else "tkin_time")
    src_time_col = "time" if "time" in src_schema else ("tkout_time" if "tkout_time" in src_schema else "tkin_time")
    if fab_time_col not in fab_schema:
        raise HTTPException(400, "FAB has no time/tkout_time/tkin_time column")
    if src_time_col not in src_schema:
        raise HTTPException(400, f"{kind.upper()} has no time/tkout_time/tkin_time column")
    fab_exprs = [pl.col(c) for c in ("lot_id", "wafer_id", "step_id") if c in fab_schema]
    fab_exprs.append(pl.col(fab_time_col).alias("time"))
    src_exprs = [pl.col(c) for c in ("item_id", "lot_id", "wafer_id") if c in src_schema]
    src_exprs.append(pl.col(src_time_col).alias("time"))
    fab_df = fab_lf.select(fab_exprs).collect()
    src_df = src_lf.select(src_exprs).collect()
    if fab_df.is_empty() or src_df.is_empty():
        raise HTTPException(404, "no rows after select")
    for label, df_name in (("FAB", "fab_df"), (kind.upper(), "src_df")):
        df = fab_df if df_name == "fab_df" else src_df
        if df.schema.get("time") != pl.Datetime:
            try:
                df = df.with_columns(pl.col("time").str.strptime(pl.Datetime, strict=False))
            except Exception:
                pass
            if df_name == "fab_df":
                fab_df = df
            else:
                src_df = df
    # item_id 별로 최빈 step_id.
    # 단순화: FAB 의 (lot_id, wafer_id) 그룹 내 max(time, step_id) 를 각 INLINE row 와 join_asof.
    try:
        fab_sorted = fab_df.sort(["lot_id", "wafer_id", "time"])
        src_sorted = src_df.sort(["lot_id", "wafer_id", "time"])
        joined = src_sorted.join_asof(
            fab_sorted, on="time", by=["lot_id", "wafer_id"], strategy="backward",
        )
    except Exception as e:
        raise HTTPException(500, f"join_asof failed: {e}")
    if "step_id" not in joined.columns:
        raise HTTPException(500, "step_id missing after join")
    joined = joined.filter(pl.col("step_id").is_not_null())
    if joined.is_empty():
        raise HTTPException(404, "no matched rows")
    # item_id 별로 가장 많이 붙은 step_id 선정.
    counts = (
        joined.group_by(["item_id", "step_id"])
              .agg(pl.len().alias("n"))
              .sort("n", descending=True)
    )
    winners: dict[str, str] = {}
    for r in counts.to_dicts():
        iid = r.get("item_id")
        if iid and iid not in winners:
            winners[str(iid)] = str(r.get("step_id") or "")
    # CSV upsert.
    base = _base_root()
    csv_name = "inline_matching.csv" if kind == "inline" else "vm_matching.csv"
    csv_fp = base / csv_name
    existing = _load_csv_rows(csv_fp)
    existing_keys = set()
    for r in existing:
        iid = (r.get("item_id") or r.get("feature_name") or "").strip()
        p_col = (r.get("product") or "").strip()
        if iid:
            existing_keys.add((iid, p_col))
    added = []
    for iid, sid in winners.items():
        if (iid, product) in existing_keys:
            continue
        if kind == "inline":
            existing.append({"product": product, "item_id": iid, "step_id": sid, "item_desc": ""})
        else:
            existing.append({"product": product, "feature_name": iid, "step_id": sid, "step_desc": ""})
        added.append((iid, sid))
    if not added:
        return {"ok": True, "added": 0, "total": len(winners), "note": "모두 기존에 등록됨"}
    # write back — header = union of all keys
    import csv as _csv
    all_keys: list = []
    for r in existing:
        for k in r.keys():
            if k not in all_keys:
                all_keys.append(k)
    try:
        csv_fp.parent.mkdir(parents=True, exist_ok=True)
        with open(csv_fp, "w", encoding="utf-8", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=all_keys)
            w.writeheader()
            for r in existing:
                w.writerow({k: r.get(k, "") for k in all_keys})
    except Exception as e:
        raise HTTPException(500, f"csv write failed: {e}")
    return {"ok": True, "added": len(added), "total": len(winners),
            "csv": str(csv_fp.name), "sample_added": added[:10]}


# v8.8.10: Rulebook "컬럼 역할 → 실제 컬럼명" 매핑 저장소 (soft-landing).
#   사내 CSV 의 컬럼 이름이 기본값과 다를 때 admin 이 여기서 매핑만 바꾸면 _build_knob_meta /
#   _build_vm_meta / _build_inline_meta 가 그대로 동작. rulebook 파일 자체는 손대지 않음.
RULEBOOK_SCHEMA_FILE = PLAN_DIR / "rulebook_schema.json"
_DEFAULT_RULEBOOK_SCHEMA = {
    "knob_ppid": {
        "feature_col":    "feature_name",
        "func_step_col":  "function_step",
        "rule_order_col": "rule_order",
        "ppid_col":       "ppid",
        "operator_col":   "operator",
        "category_col":   "category",
        "use_col":        "use",
        "product_col":    "product",
    },
    "step_matching": {
        "step_id_col":   "step_id",
        "func_step_col": "func_step",
        "product_col":   "product",
        "module_col":    "module",
    },
    "inline_matching": {
        "step_id_col":   "step_id",
        "process_id_col": "process_id",
        "item_id_col":   "item_id",
        "item_desc_col": "item_desc",
        "product_col":   "product",
    },
    "vm_matching": {
        "feature_col":   "feature_name",
        "step_desc_col": "step_desc",
        "step_id_col":   "step_id",
        "product_col":   "product",
    },
}


def _load_rulebook_schema() -> dict:
    try:
        data = load_json(RULEBOOK_SCHEMA_FILE, {})
    except Exception:
        data = {}
    # merge with defaults so missing keys fall back.
    out = {}
    for k, defmap in _DEFAULT_RULEBOOK_SCHEMA.items():
        cur = (data or {}).get(k) if isinstance(data, dict) else {}
        cur = cur if isinstance(cur, dict) else {}
        out[k] = {**defmap, **{kk: (vv or defmap.get(kk, "")) for kk, vv in cur.items() if isinstance(kk, str)}}
    return out


def _save_rulebook_schema(schema: dict) -> None:
    save_json(RULEBOOK_SCHEMA_FILE, schema, indent=2)


def _sch(kind: str) -> dict:
    return _load_rulebook_schema().get(kind, _DEFAULT_RULEBOOK_SCHEMA.get(kind, {}))


@router.get("/rulebook/schema")
def get_rulebook_schema():
    """현재 역할→컬럼명 매핑 + 기본값 같이 반환. FE 에서 diff 표시 가능."""
    return {"schema": _load_rulebook_schema(), "defaults": _DEFAULT_RULEBOOK_SCHEMA}


class RulebookSchemaReq(BaseModel):
    kind: str
    mapping: dict
    username: str = ""


@router.post("/rulebook/schema/save")
def save_rulebook_schema(req: RulebookSchemaReq, request: Request):
    try:
        from core.auth import current_user
        me = current_user(request)
        if (me.get("role") or "") != "admin":
            raise HTTPException(403, "Admin only")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(403, "Auth required")
    if req.kind not in _DEFAULT_RULEBOOK_SCHEMA:
        raise HTTPException(400, f"unknown rulebook: {req.kind}")
    cur = _load_rulebook_schema()
    defm = _DEFAULT_RULEBOOK_SCHEMA[req.kind]
    new_map = {}
    for role, _dfl in defm.items():
        v = (req.mapping or {}).get(role, _dfl)
        v = str(v or "").strip() or _dfl
        new_map[role] = v
    cur[req.kind] = new_map
    _save_rulebook_schema(cur)
    _audit_user(req.username or (me.get("username") if isinstance(me, dict) else ""),
                "splittable:rulebook_schema_save",
                detail=f"kind={req.kind} mapping={new_map}")
    return {"ok": True, "kind": req.kind, "mapping": new_map}


# v8.8.7: Rulebook (knob_ppid.csv + step_matching.csv) admin 인라인 편집 CRUD.
#   admin 만 수정 가능. 저장 시 row 정규화 + 빈 행 제거 + 원자적 교체.
#   스키마는 _build_knob_meta 가 읽는 컬럼과 동일해야 함.
_RULEBOOK_FILES = {
    "knob_ppid": {
        "filename": "knob_ppid.csv",
        "cols": ["product", "ppid", "knob_name", "knob_value", "feature_name",
                 "function_step", "rule_order", "operator", "category", "use"],
        "required": ["product", "ppid"],
    },
    "step_matching": {
        "filename": "step_matching.csv",
        "cols": ["step_id", "func_step", "product", "module"],
        "required": ["step_id", "func_step", "product"],
    },
    # v8.8.9: INLINE / VM 매칭도 동일 CRUD 로 관리.
    #   inline_matching.csv: (process_id, item_id, item_desc) — INLINE_<item_id> 측정 메타.
    "inline_matching": {
        "filename": "inline_matching.csv",
        "cols": ["process_id", "item_id", "item_desc", "product", "step_id"],
        "required": ["item_id", "product"],
    },
    #   vm_matching.csv: (feature_name, step_desc, step_id) — VM_<feature_name> 이 해당 step 에서 예측됨.
    "vm_matching": {
        "filename": "vm_matching.csv",
        "cols": ["feature_name", "step_desc", "step_id", "product"],
        "required": ["feature_name", "step_id", "product"],
    },
}


def _rulebook_path(kind: str) -> Path:
    meta = _RULEBOOK_FILES.get(kind)
    if not meta:
        raise HTTPException(400, f"unknown rulebook: {kind}")
    return _base_root() / meta["filename"]


@router.get("/rulebook")
def get_rulebook(kind: str = Query("knob_ppid"), product: str = Query("")):
    """v8.8.7: rulebook CSV 를 JSON 으로 반환. product 주어지면 그 제품 행만 + 공용 (product 빈값)."""
    meta = _RULEBOOK_FILES.get(kind)
    if not meta:
        raise HTTPException(400, f"unknown rulebook: {kind}")
    rows = _load_csv_rows(_rulebook_path(kind))
    if product:
        rows = [r for r in rows if not r.get("product") or r.get("product") == product]
    return {
        "kind": kind, "file": meta["filename"],
        "columns": meta["cols"], "rows": rows, "count": len(rows),
    }


class RulebookSaveReq(BaseModel):
    kind: str               # "knob_ppid" | "step_matching"
    rows: List[dict]        # 전체 대체 (혹은 product 스코프 대체)
    product: str = ""       # 주어지면 해당 제품 rows 만 대체, 빈값이면 파일 전체 대체
    username: str = ""


@router.post("/rulebook/save")
def save_rulebook(req: RulebookSaveReq, request: Request):
    """admin 전용. product 스코프면 해당 제품 행만 교체, 아니면 파일 전체 교체."""
    # admin check
    try:
        from core.auth import current_user
        me = current_user(request)
        if (me.get("role") or "") != "admin":
            raise HTTPException(403, "Admin only")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(403, "Auth required")

    meta = _RULEBOOK_FILES.get(req.kind)
    if not meta:
        raise HTTPException(400, f"unknown rulebook: {req.kind}")
    fp = _rulebook_path(req.kind)
    cols = meta["cols"]
    req_cols = meta["required"]

    # normalize incoming rows — drop empty, enforce required fields.
    cleaned = []
    for r in (req.rows or []):
        if not isinstance(r, dict):
            continue
        nr = {c: str(r.get(c, "") or "").strip() for c in cols}
        if any(not nr.get(c) for c in req_cols):
            continue
        cleaned.append(nr)

    # merge with existing if product-scoped.
    if req.product:
        existing = _load_csv_rows(fp)
        kept = [r for r in existing if r.get("product") != req.product]
        # product 컬럼 없는 공용 행은 유지, 요청 product 의 행만 교체.
        for c in cleaned:
            c["product"] = req.product
        final = kept + cleaned
    else:
        final = cleaned

    # ensure column order
    import io as _io
    buf = _io.StringIO()
    w = csv_mod.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in final:
        w.writerow({c: r.get(c, "") for c in cols})
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(buf.getvalue(), encoding="utf-8", newline="")
    _audit_user(req.username or (me.get("username") if isinstance(me, dict) else ""),
                "splittable:rulebook_save",
                detail=f"kind={req.kind} product={req.product} rows={len(final)}")
    sync_result = _s3.sync_saved_path(PATHS.data_root, PATHS.db_root, fp)
    return {"ok": True, "kind": req.kind, "product": req.product, "saved_rows": len(final), "s3_sync": sync_result}


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


# ── v8.8.6: Paste sets (팀 공용 — 인폼·SplitTable paste 공유) ──────────────
# Schema: [{id, name, product, columns:[...], rows:[[...]], username, created, updated}]
#   - CUSTOM 탭에서 paste 세트를 직접 columns 로 취급 → as-is 뷰 (SplitTable custom 과 별개 보관).
#   - FE 는 로컬스토리지 대신 이 엔드포인트에서 읽고 씀. 로컬 폴백은 FE 가 알아서.
def _load_paste_sets() -> list:
    data = load_json(PASTE_SETS_FILE, [])
    return data if isinstance(data, list) else []

def _save_paste_sets(items: list) -> None:
    save_json(PASTE_SETS_FILE, items, indent=2)


class PasteSetSaveReq(BaseModel):
    name: str
    product: str = ""
    columns: List[str]
    rows: List[List] = []
    username: str = ""


@router.get("/paste-sets")
def list_paste_sets(product: str = Query("")):
    """팀 공용 paste 세트 목록. product 가 주어지면 해당 product 또는 빈 product(공용) 만 반환."""
    items = _load_paste_sets()
    if product:
        items = [s for s in items if not s.get("product") or s.get("product") == product]
    # recent first
    items = sorted(items, key=lambda s: s.get("updated", s.get("created", "")), reverse=True)
    return {"sets": items}


@router.post("/paste-sets/save")
def save_paste_set(req: PasteSetSaveReq):
    import secrets as _secrets
    nm = (req.name or "").strip()
    if not nm:
        raise HTTPException(400, "name required")
    cols = [str(c) for c in (req.columns or []) if c]
    if not cols:
        raise HTTPException(400, "columns required")
    now = datetime.datetime.now().isoformat(timespec="seconds")
    items = _load_paste_sets()
    # upsert by (name, product) — 같은 이름·제품이면 덮어쓰기.
    existing = next((s for s in items if s.get("name") == nm and s.get("product", "") == (req.product or "")), None)
    if existing:
        existing.update({
            "columns": cols, "rows": req.rows or [], "username": req.username or existing.get("username", ""),
            "updated": now,
        })
    else:
        items.append({
            "id": "ps_" + _secrets.token_hex(5),
            "name": nm, "product": req.product or "",
            "columns": cols, "rows": req.rows or [],
            "username": req.username or "",
            "created": now, "updated": now,
        })
    _save_paste_sets(items)
    return {"ok": True, "count": len(items)}


class PasteSetDeleteReq(BaseModel):
    id: str = ""
    name: str = ""
    product: str = ""
    username: str = ""


@router.post("/paste-sets/delete")
def delete_paste_set(req: PasteSetDeleteReq):
    items = _load_paste_sets()
    before = len(items)
    if req.id:
        items = [s for s in items if s.get("id") != req.id]
    elif req.name:
        items = [s for s in items if not (s.get("name") == req.name and s.get("product", "") == (req.product or ""))]
    else:
        raise HTTPException(400, "id or name required")
    if len(items) == before:
        raise HTTPException(404, "paste set not found")
    _save_paste_sets(items)
    return {"ok": True, "removed": before - len(items)}


@router.post("/paste-sets/to-custom")
def paste_set_to_custom(req: PasteSetDeleteReq):
    """paste 세트의 columns 를 CUSTOM 커스텀 뷰로 승격.
    CUSTOM 탭에서 바로 선택 가능하게 `custom_<safe_name>.json` 생성."""
    items = _load_paste_sets()
    src = None
    if req.id:
        src = next((s for s in items if s.get("id") == req.id), None)
    elif req.name:
        src = next((s for s in items if s.get("name") == req.name and s.get("product", "") == (req.product or "")), None)
    if not src:
        raise HTTPException(404, "paste set not found")
    name = src.get("name") or "paste_custom"
    fp = PLAN_DIR / f"custom_{safe_id(name)}.json"
    now = datetime.datetime.now().isoformat(timespec="seconds")
    existing = load_json(fp, None) if fp.exists() else None
    save_json(fp, {
        "name": name, "username": req.username or src.get("username", ""),
        "columns": list(src.get("columns") or []),
        "created": (existing or {}).get("created", now),
        "updated": now,
        "version": int((existing or {}).get("version", 0)) + 1,
        "source": "paste-set", "paste_id": src.get("id", ""),
    })
    return {"ok": True, "custom_name": name}


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
    # v8.6.1: 낙관적 잠금 — 동일 name 의 기존 커스텀이 있으면 expected_version 일치 시에만 덮어쓴다.
    # 신규(처음 저장)면 0 또는 None.
    expected_version: int | None = None


@router.post("/customs/save")
def save_custom(req: CustomSaveReq):
    fp = PLAN_DIR / f"custom_{safe_id(req.name)}.json"
    now = datetime.datetime.now().isoformat()
    existing = load_json(fp, None) if fp.exists() else None
    if existing:
        cur_v = int(existing.get("version", 1))
        # 클라가 보낸 expected_version 이 None 이면 강제 덮어쓰기 (legacy).
        # 정수면 일치해야 함. 불일치 → conflict 응답.
        if req.expected_version is not None and int(req.expected_version) != cur_v:
            return {
                "ok": False, "conflict": True,
                "server_version": cur_v, "current": existing,
                "detail": "Version conflict — another user has saved this custom view.",
            }
        new_v = cur_v + 1
        created = existing.get("created", now)
    else:
        new_v = 1
        created = now
    save_json(fp, {
        "name": req.name, "username": req.username, "columns": req.columns,
        "created": created, "updated": now, "version": new_v,
    })
    return {"ok": True, "version": new_v}


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


def _resolve_fab_source_target(fab_source: str):
    """Resolve a db-relative fab_source to an existing file or directory."""
    fab_source = _normalize_fab_source_path(fab_source)
    if not fab_source:
        return None, fab_source
    if fab_source.startswith("root:"):
        return None, fab_source
    aliases = [fab_source]
    parts = [p for p in fab_source.split("/") if p]
    if parts:
        head = parts[0].casefold()
        tail = "/".join(parts[1:])
        if head == _RAWDATA_FAB.casefold():
            aliases.append(_RAWDATA_EXACT + (f"/{tail}" if tail else ""))
        elif head == _RAWDATA_EXACT.casefold():
            aliases.append(_RAWDATA_FAB + (f"/{tail}" if tail else ""))
    db_base = _db_base()
    base_root = _base_root()
    fp = None
    matched = fab_source
    for root in (db_base, base_root):
        if not root or not root.exists():
            continue
        for rel in aliases:
            # v8.8.22: CI 경로 매칭 — fab_source 내 제품 폴더 대소문자 무시.
            # v9.0.6: 1.RAWDATA_DB_FAB/<PROD> 와 1.RAWDATA_DB/<PROD> 는 둘 다 FAB
            # history 로 취급한다. 운영 환경은 exact 이름만 쓰는 경우가 있다.
            cand = _find_ci_path(root, rel)
            if cand is not None and cand.exists():
                fp = cand
                matched = rel
                break
            for ext in (".parquet", ".csv"):
                cand2 = _find_ci_path(root, f"{rel}{ext}")
                if cand2 is not None and cand2.exists():
                    fp = cand2
                    matched = rel
                    break
            if fp:
                break
        if fp:
            break
    return fp, matched


def _rglob_files_ci(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    suffix_set = {s.casefold() for s in suffixes}
    try:
        cache_key = (str(root.resolve()), tuple(sorted(suffix_set)))
    except Exception:
        cache_key = (str(root), tuple(sorted(suffix_set)))
    now = time.monotonic()
    cached = _RGLOB_CACHE.get(cache_key)
    if cached and now - cached[0] < _DISCOVERY_CACHE_TTL_SEC:
        return list(cached[1])
    try:
        out = sorted(
            [p for p in root.rglob("*") if p.is_file() and p.suffix.casefold() in suffix_set],
            key=lambda p: str(p).casefold(),
        )
        _RGLOB_CACHE[cache_key] = (now, out)
        return list(out)
    except Exception:
        return []


def _scan_fab_source_raw(fab_source: str):
    """Scan a fab_source without applying the long-format compatibility adapter."""
    fp, fab_source = _resolve_fab_source_target(fab_source)
    if not fp:
        return None
    try:
        if fp.is_dir():
            parquets = _rglob_files_ci(fp, (".parquet",))
            if not parquets:
                return None
            # v8.8.5: 사내 `PRODA/date=YYYYMMDD/part_*.parquet` hive 레이아웃 대응.
            # hive_partitioning 을 켜서 경로의 `date=...` 를 컬럼으로 노출 → ts_col 자동 추론 시
            # `date` 후보가 적중해 "가장 최신 date 의 fab_col" join 이 자동으로 동작.
            try:
                return _cast_cats_lazy(_scan_parquet_compat([str(p) for p in parquets],
                                                            hive_partitioning=True))
            except TypeError:
                # polars 구버전 — 파라미터 미지원 시 폴백 (경로 기반 파티션 컬럼 없음).
                return _cast_cats_lazy(_scan_parquet_compat([str(p) for p in parquets]))
        if fp.suffix.lower() == ".csv":
            return _cast_cats_lazy(pl.scan_csv(str(fp), infer_schema_length=5000))
        return _cast_cats_lazy(_scan_parquet_compat(str(fp)))
    except Exception:
        return None


def _scan_fab_source(fab_source: str):
    """v8.8.0: fab_source 가 가리키는 DB 경로를 LazyFrame 으로 스캔.
    - "FAB/PRODA" / "1.RAWDATA_DB/PRODA" 같은 디렉토리면 그 아래 모든 *.parquet 을 union 으로 스캔.
    - 단일 .parquet/.csv 파일이면 그 파일을 스캔.
    v8.8.21: "root:<name>" legacy prefix 는 제품 scope 를 넘어서므로 더 이상 지원하지 않음.
      저장된 값이 있어도 무시 → 호출측이 _auto_derive_fab_source 로 자동 매칭하도록 None 반환.
    실패 시 None 반환 (조용히 폴백).
    """
    lf_raw = _scan_fab_source_raw(fab_source)
    if lf_raw is None:
        return None
    # FAB canonical adapter:
    #   - 정식 FAB 는 wafer 단위 공정이력(root_lot_id/lot_id/wafer_id/step_id/tkin_time/tkout_time/eqp_id...).
    #   - 구 demo alias(eqp/chamber/time)가 섞여 있으면 runtime schema 에서만 정규화한다.
    #   - 아주 오래된 item/value FAB demo data 만 기존 최신 row adapter 로 축약한다.
    try:
        raw_names = lf_raw.collect_schema().names()
        from core.long_pivot import normalize_fab_history
        lf_raw = normalize_fab_history(lf_raw)
        names = lf_raw.collect_schema().names()
    except Exception:
        return lf_raw
    process_markers = {"eqp_id", "chamber_id", "ppid", "reticle_id", "tkout_time", "tkin_time"}
    legacy_process_aliases = {"eqp", "chamber", "ppid", "reticle_id", "tkout_time", "tkin_time"}
    raw_has_process_history = bool((process_markers | legacy_process_aliases) & set(raw_names))
    if "item_id" in names and "value" in names and "lot_id" in names and not raw_has_process_history:
        logger.info("_scan_fab_source: long-format 감지 → fab_lot_id adapter 적용 (source=%s)", fab_source)
        keep = [c for c in ("product", "root_lot_id", "lot_id", "wafer_id", "time") if c in names]
        lf_adapt = lf_raw.select(keep)
        if "time" in keep:
            lf_adapt = lf_adapt.sort("time", descending=True, nulls_last=True)
        renames = {}
        if "lot_id" in keep:
            renames["lot_id"] = "fab_lot_id"
        if "time" in keep:
            renames["time"] = "tkout_time"
        if renames:
            lf_adapt = lf_adapt.rename(renames)
        key_cols = [c for c in ("root_lot_id", "wafer_id") if c in keep]
        if key_cols:
            lf_adapt = lf_adapt.unique(subset=key_cols, keep="first", maintain_order=True)
        return lf_adapt
    return lf_raw


# v8.8.3/v8.8.5: ML_TABLE_<PROD> → DB 상위폴더 자동 매칭.
#   `_list_db_roots()` 에 위임 — 사내 `1.RAWDATA_DB*` 접두 폴더도 인식 (FAB 힌트 우선).
# v8.8.17: root_dir 이 db_base 자체일 때(Case 1/3) 는 제품명만 반환 —
#   `_scan_fab_source` 에서 `db_base / fab_source` 로 해석하므로 prefix 중복 방지.
def _auto_derive_fab_source(product: str) -> str:
    """Return a fab_source path like "1.RAWDATA_DB_FAB/PRODA" (or legacy "FAB/PRODA") if auto-matchable, else "".
    ML_TABLE_ prefix 가 아니면 "" 반환 (오버라이드 off)."""
    p = _canonical_mltable_product_name(product)
    if not p:
        return ""
    pro = p[len("ML_TABLE_"):].strip()
    if not pro:
        return ""
    db_base = _db_base()
    roots = _list_db_roots()
    roots.sort(key=lambda r: _rank_db_root_name(r.name))
    for root_dir in roots:
        up = root_dir.name.upper()
        if up not in (_RAWDATA_EXACT.upper(), _RAWDATA_FAB.upper()) and not up.startswith(_RAWDATA_EXACT.upper() + "_"):
            continue
        # v8.8.22: CI 매칭 — 폴더가 ProdA/proda/PRODA 중 무엇이든 인식.
        cand = _find_ci_child(root_dir, pro)
        if cand is not None:
            actual = cand.name
            try:
                if root_dir.resolve() == db_base.resolve():
                    return actual
            except Exception:
                pass
            return f"{root_dir.name}/{actual}"
    return ""


# v8.8.3/v8.8.5/v9.0.4: ts_col / fab_col 자동 추론.
#   - 사용자가 기대하는 실사용 우선순위: tkout_time > time 계열 > date.
#   - date 는 hive 파티션 키(`date=YYYYMMDD`) 전용 마지막 fallback.
_TS_COL_CANDIDATES = ("tkout_time", "time", "out_ts", "ts", "timestamp", "created_at", "log_ts", "event_ts", "update_ts")
_FAB_COL_CANDIDATES = ("fab_lot_id", "lot_id", "fab_lotid", "fab_lot")
_RAW_TO_RUNTIME_ALIAS_CANDIDATES = {
    "lot_id": "fab_lot_id",
    "time": "tkout_time",
    "eqp": "eqp_id",
    "chamber": "chamber_id",
}


# v8.8.22: case-insensitive 컬럼 정렬.
#   ML_TABLE 은 대문자(ROOT_LOT_ID/WAFER_ID), hive 원천은 소문자(root_lot_id/wafer_id) 로
#   다르게 찍히는 경우가 있음. casefold 같으면 같은 컬럼으로 취급해야 join/override 가 동작.
#   → fab_lf 의 컬럼을 main_lf 쪽 casing 으로 rename 하여 이후 로직이 그대로 exact 매칭되게.
# v8.8.26: 충돌 가드 단순화 + rename 후 실제 스키마 재확인 (rename 이 lazy 상 silently 실패하는 사례 방지).
def _ci_align_fab_to_main(fab_lf, main_names):
    """Rename fab_lf columns to match main_names casing when casefold is equal.

    규칙:
      - fab 의 컬럼 fn (casefold=key) 이 main 의 target 과 casefold 일치하고 casing 만 다르면
        rename[fn] = target.
      - target 이 이미 fab 에 (별도의 distinct 컬럼으로) 존재하면 rename 을 skip (clobber 방지).
      - target 이 이번 rename 맵의 다른 항목에 의해 이미 소비됐으면 skip.
      - rename 후 실제 schema 를 재조회해 실패 여부 확인 — 실패 시 경고 로깅.

    Returns (aligned_lf, new_fab_names_list).
    """
    if fab_lf is None:
        return fab_lf, []
    try:
        fab_names = fab_lf.collect_schema().names()
    except Exception as e:
        logger.warning("_ci_align_fab_to_main: fab schema 조회 실패 %s: %s", type(e).__name__, e)
        return fab_lf, []
    main_ci = {n.casefold(): n for n in main_names}
    fab_set = set(fab_names)
    rename: dict = {}
    used_targets: set = set()
    for fn in fab_names:
        key = fn.casefold()
        target = main_ci.get(key)
        if not target or target == fn:
            continue
        # 단순화된 충돌 가드: target 이 fab 에 별개 컬럼으로 존재하면 rename 불가 (clobber).
        if target in fab_set:
            continue
        if target in used_targets:
            continue
        rename[fn] = target
        used_targets.add(target)
    if rename:
        try:
            fab_lf = fab_lf.rename(rename)
        except Exception as e:
            logger.warning("_ci_align_fab_to_main: rename 실패 %s: %s (rename=%s)",
                           type(e).__name__, e, rename)
            # rename 실패 시 원본 이름 유지
            return fab_lf, list(fab_names)
        # rename 이 적용됐는지 실제 스키마로 재확인.
        try:
            post = fab_lf.collect_schema().names()
            missing = [t for t in rename.values() if t not in post]
            if missing:
                logger.warning("_ci_align_fab_to_main: rename 후 target 누락 %s (post=%s...)",
                               missing, post[:20])
            return fab_lf, post
        except Exception as e:
            logger.warning("_ci_align_fab_to_main: post-schema 조회 실패 %s: %s",
                           type(e).__name__, e)
    new_names = [rename.get(n, n) for n in fab_names]
    return fab_lf, new_names


def _ci_resolve_in(name: str, pool):
    """Return the actual column name from pool matching `name` case-insensitively (exact first)."""
    if not name:
        return ""
    resolved = resolve_column(list(pool), name)
    return resolved.matched if resolved else ""


def _default_override_join_keys(main_names, fab_names):
    """Prefer root_lot_id + wafer_id by default; fall back only when necessary."""
    main_ci = {str(n).casefold(): n for n in (main_names or [])}
    fab_ci = {str(n).casefold(): n for n in (fab_names or [])}
    preferred = []
    for cand in ("root_lot_id", "wafer_id"):
        key = cand.casefold()
        if key in main_ci and key in fab_ci:
            preferred.append(main_ci[key])
    if preferred:
        return preferred
    fallback = []
    for cand in ("lot_id", "wafer_id"):
        key = cand.casefold()
        if key in main_ci and key in fab_ci:
            fallback.append(main_ci[key])
    return fallback


def _join_key_expr(col_name: str):
    """Normalize join key values so main/fab joins are case-insensitive and trim-safe."""
    return (
        pl.col(col_name)
        .cast(_STR, strict=False)
        .str.strip_chars()
        .str.to_uppercase()
    )


def _contains_literal_ci_expr(col_name: str, needle: str):
    """Case-insensitive literal contains for LazyFrame autocomplete filters."""
    return (
        pl.col(col_name)
        .cast(_STR, strict=False)
        .str.to_uppercase()
        .str.contains(str(needle or "").strip().upper(), literal=True)
    )


def _apply_fab_scope_filters(fab_lf, fab_names, ov: dict, root_lot_id: str = "",
                             fab_lot_id: str = "", wafer_ids: str = "",
                             fab_col: str = ""):
    """Limit FAB source rows before latest-row picking and join."""
    root_scope = str(root_lot_id or "").strip()
    fab_scope = str(fab_lot_id or "").strip()
    wafer_scope = str(wafer_ids or "").strip()
    if root_scope:
        root_col = _resolve_source_col_name((ov.get("root_col") or "").strip(), fab_names) \
                   or _ci_resolve_in("root_lot_id", fab_names)
        if root_col:
            fab_lf = fab_lf.filter(_join_key_expr(root_col) == root_scope.upper())
    if fab_scope:
        target_fab_col = fab_col if fab_col in fab_names else _pick_first_present_ci(_FAB_COL_CANDIDATES, fab_names)
        if target_fab_col:
            fab_lf = fab_lf.filter(_join_key_expr(target_fab_col) == fab_scope.upper())
    if wafer_scope:
        wf_col = _resolve_source_col_name((ov.get("wf_col") or ov.get("wafer_col") or "").strip(), fab_names) \
                 or _pick_first_present_ci(("wafer_id", "wafer"), fab_names)
        if wf_col:
            wf_list = [w.strip() for w in wafer_scope.split(",") if w.strip()]
            try:
                wf_ints = [int(w) for w in wf_list]
                wf_strs = set()
                for n in wf_ints:
                    wf_strs.update([str(n), f"{n:02d}", f"W{n}", f"W{n:02d}"])
                fab_lf = fab_lf.filter(
                    pl.col(wf_col).cast(_STR, strict=False).is_in(list(wf_strs))
                    | pl.col(wf_col).cast(pl.Int64, strict=False).is_in(wf_ints)
                )
            except ValueError:
                fab_lf = fab_lf.filter(pl.col(wf_col).cast(_STR, strict=False).is_in(wf_list))
    return fab_lf


# v8.8.16: hive 원천에서 끌어와 ML_TABLE 값을 덮어쓸 기본 컬럼 집합.
#   사내 `1.RAWDATA_DB*/<PROD>/date=*/*.parquet` 레이아웃에서 이 이름이 있으면 소스값으로 교체.
#   fab_col(보통 fab_lot_id) 는 레거시 단일 필드와 병합되어 override_cols 에 합류.
_DEFAULT_OVERRIDE_COLS = (
    "root_lot_id", "lot_id", "wafer_id", "line_id", "process_id", "step_id",
    "tkin_time", "tkout_time", "eqp_id", "chamber_id", "reticle_id", "ppid",
)


def _resolve_override_meta(product: str, include_diagnostics: bool = True) -> dict:
    """v8.8.5: view / ml-table-match 양쪽에서 공용. 현재 product 에 대해 적용된 오버라이드 설정 요약.

    Returns (모든 필드 optional, 에러 시 error 로 이유 표기):
      {
        "enabled": bool,              # 조인 실제 수행 여부
        "manual_override": bool,      # SOURCE_CFG 에 명시된 fab_source 사용 여부
        "fab_source": str,            # 사용된 fab_source 경로 (e.g. "1.RAWDATA_DB_FAB/PRODA")
        "fab_col": str,               # 실제 join 하는 fab 컬럼 이름
        "ts_col": str,                # 최신도 판정에 쓰는 ts 컬럼 (빈 문자열이면 레거시 keep=last)
        "join_keys": [str],
        "scanned_files": [str],       # fab_source 아래 발견된 parquet 들 (최대 20)
        "scanned_count": int,         # 실제 파일 개수
        "row_count": int,             # fab_source LazyFrame 전체 row 수 (scanned)
        "sample_fab_values": [str],   # head(5) 의 fab_col 값 — "어디서 읽어옴?" 답변용
        "error": str | None,
      }
    """
    meta = {
        "enabled": False, "manual_override": False,
        "fab_source": "", "fab_col": "", "ts_col": "",
        "join_keys": [], "scanned_files": [], "scanned_count": 0,
        "row_count": 0, "sample_fab_values": [], "error": None,
        "raw_columns": [], "runtime_columns": [], "column_aliases": {}, "schema_mode": "unknown",
        # v8.8.16: hive 원천에서 끌어오기로 한 override 컬럼 목록 + 실제 스키마에 존재하는 것만.
        "override_cols": [], "override_cols_present": [], "override_cols_missing": [],
    }
    try:
        product = _canonical_mltable_product_name(product, allow_bare=True) or str(product or "").strip()
        cfg = load_json(SOURCE_CFG, {}) if SOURCE_CFG.exists() else {}
        ov = _lot_override_for(cfg, product)
        manual = (ov.get("fab_source") or "").strip()
        # v8.8.21: root:~~ 는 deprecated — 저장된 값이 남아있어도 무시하고 auto-derive 로 재매칭.
        if manual.startswith("root:"):
            manual = ""
        fab_source = manual or _auto_derive_fab_source(product)
        meta["manual_override"] = bool(manual)
        meta["fab_source"] = fab_source
        # v8.8.19: 진단 정보 — 어떤 data_root/DB 에서 어떤 후보를 탐색했는지 노출.
        db_base = _db_base()
        base_root = _base_root()
        meta["db_root"] = str(db_base)
        meta["base_root"] = str(base_root)
        meta["db_root_exists"] = bool(db_base.exists())
        meta["searched_db_roots"] = [p.name for p in _list_db_roots()]

        if not fab_source:
            if product.casefold().startswith("ml_table_"):
                pro = product[len("ML_TABLE_"):].strip()
                # 실제로 탐색한 후보 경로를 모두 리스트업
                tried = []
                for root_dir in _list_db_roots():
                    tried.append(f"{root_dir.name}/{pro}")
                if not _list_db_roots():
                    tried.append(f"(db_root 비어있거나 '1.RAWDATA_DB' 하위 제품 폴더 없음: {db_base})")
                meta["error"] = (
                    f"자동 매칭 실패: product='{product}' → pro='{pro}'. "
                    f"db_root='{db_base}'. "
                    f"후보 탐색: {tried if tried else '(없음)'}. "
                    f"권장 해결: data_root/DB 아래 '1.RAWDATA_DB/{pro}/' 가 존재하거나, "
                    f"수동으로 lot_overrides.{product}.fab_source 를 지정."
                )
                meta["tried_candidates"] = tried
            else:
                meta["error"] = "ML_TABLE_ prefix 아님 — 오버라이드 off."
            return meta

        # locate fab_source folder/file to list scanned files.  The resolver also
        # treats 1.RAWDATA_DB_FAB/<PROD> and 1.RAWDATA_DB/<PROD> as equivalent
        # FAB-history roots for production soft landing.
        fp, resolved_fab_source = _resolve_fab_source_target(fab_source)
        tried = []
        for root in (db_base, base_root):
            if not root or not root.exists():
                tried.append(f"{root} (not exist)" if root else "(None)")
                continue
            for rel in dict.fromkeys([fab_source, resolved_fab_source]):
                if rel:
                    tried.append(str(root / rel) + ("" if fp is not None else "  (not found)"))
        if fp is None:
            meta["tried_candidates"] = tried
            meta["error"] = (
                f"fab_source 경로를 찾을 수 없음: '{fab_source}'. "
                f"탐색 경로: {tried}. db_root='{db_base}' base_root='{base_root}'. "
                f"fab_source 는 데모/운영 모두 db_root 기준 상대경로만 사용하세요 "
                f"(예: '1.RAWDATA_DB_FAB/PRODA', not 'DB/1.RAWDATA_DB_FAB/PRODA')."
            )
            return meta
        if resolved_fab_source:
            meta["fab_source"] = resolved_fab_source
            fab_source = resolved_fab_source
        if fp.is_dir():
            parquets = _rglob_files_ci(fp, (".parquet",))
            base_for_rel = fp.parent if fp.parent.exists() else fp
            rels = []
            for p in parquets:
                try:
                    rels.append(str(p.relative_to(_db_base())))
                except Exception:
                    try:
                        rels.append(str(p.relative_to(_base_root())))
                    except Exception:
                        rels.append(str(p))
            meta["scanned_count"] = len(parquets)
            meta["scanned_files"] = [r.replace("\\", "/") for r in rels[:20]]
        else:
            meta["scanned_count"] = 1
            try:
                meta["scanned_files"] = [str(fp.relative_to(_db_base())).replace("\\", "/")]
            except Exception:
                meta["scanned_files"] = [str(fp)]

        raw_lf = _scan_fab_source_raw(fab_source)
        fab_lf = _scan_fab_source(fab_source)
        if fab_lf is None:
            meta["error"] = f"스캔 실패 (parquet 없음 또는 읽기 불가): {fab_source}"
            return meta
        # v8.8.22: CI 정렬 — ML_TABLE 대문자 vs hive 소문자 컬럼 이름 차이를 흡수.
        try:
            main_fp = _product_path(product)
            if main_fp.suffix.lower() == ".csv":
                main_names_list = pl.scan_csv(str(main_fp), infer_schema_length=5000).collect_schema().names()
            else:
                main_names_list = _scan_parquet_compat(str(main_fp)).collect_schema().names()
        except Exception:
            main_names_list = []
        fab_lf, fab_schema_names = _ci_align_fab_to_main(fab_lf, main_names_list)
        fab_names = fab_schema_names  # list after rename
        main_names = main_names_list
        try:
            raw_names = raw_lf.collect_schema().names() if raw_lf is not None else []
        except Exception:
            raw_names = []
        meta["raw_columns"] = raw_names
        meta["runtime_columns"] = list(fab_names)
        meta["column_aliases"] = _detect_source_column_aliases(raw_names, fab_names)
        meta["schema_mode"] = "adapted" if meta["column_aliases"] else "raw"

        # join keys
        join_keys = ov.get("join_keys") or []
        if isinstance(join_keys, str):
            join_keys = [k.strip() for k in join_keys.split(",") if k.strip()]
        # 유저가 지정한 키도 CI 로 실제 컬럼명에 매핑.
        if join_keys:
            mapped = []
            for k in join_keys:
                actual = _ci_resolve_in(k, main_names) or _resolve_source_col_name(k, fab_names)
                if actual:
                    mapped.append(actual)
            join_keys = mapped
        if not join_keys:
            join_keys = _default_override_join_keys(main_names, fab_names)
        join_keys = [k for k in join_keys if k in fab_names]
        meta["join_keys"] = join_keys

        # fab_col / ts_col 추론 (v8.8.22: CI 매칭 — fab_lf 는 이미 main casing 으로 align 됨).
        fc_raw = (ov.get("fab_col") or "").strip()
        meta["fab_col"] = (_resolve_source_col_name(fc_raw, fab_names) if fc_raw else "") \
                         or _pick_first_present_ci(_FAB_COL_CANDIDATES, fab_names) \
                         or "fab_lot_id"
        tc_raw = (ov.get("ts_col") or "").strip()
        meta["ts_col"] = (_resolve_source_col_name(tc_raw, fab_names) if tc_raw else "") \
                         or _pick_ts_col(fab_names) \
                         or ""

        # v8.8.16: override_cols — 기본 (_DEFAULT_OVERRIDE_COLS) + manual ov.override_cols + 레거시 fab_col 병합.
        raw_oc = ov.get("override_cols")
        if isinstance(raw_oc, str):
            raw_oc = [c.strip() for c in raw_oc.split(",") if c.strip()]
        if not raw_oc:
            raw_oc = list(_DEFAULT_OVERRIDE_COLS)
        # 레거시 fab_col 도 합류 (중복 제거).
        if meta["fab_col"] and meta["fab_col"] not in raw_oc:
            raw_oc = list(raw_oc) + [meta["fab_col"]]
        # v8.8.22: CI 매칭 — 사용자가 소문자로 적었어도 실제 스키마의 casing 으로 맵핑.
        resolved_oc = []
        for c in raw_oc:
            actual = _resolve_source_col_name(c, fab_names)
            resolved_oc.append(actual or c)
        meta["override_cols"] = list(resolved_oc)
        meta["override_cols_present"] = [c for c in resolved_oc if c in fab_names]
        meta["override_cols_missing"] = [c for c in resolved_oc if c not in fab_names]

        if meta["fab_col"] not in fab_names:
            meta["error"] = f"fab_col '{meta['fab_col']}' 이 소스 스키마에 없음. 소스 컬럼: {fab_names[:20]}"
            return meta
        if not join_keys:
            meta["error"] = f"공통 join key 없음. 소스 컬럼: {fab_names[:20]}"
            return meta

        # row count + sample
        if include_diagnostics:
            try:
                rc = fab_lf.select(pl.len()).collect()
                meta["row_count"] = int(rc.item()) if rc.height > 0 else 0
            except Exception as e:
                meta["row_count"] = -1
        try:
            sample_cols = [c for c in (join_keys + [meta["fab_col"]] + ([meta["ts_col"]] if meta["ts_col"] else [])) if c in fab_names]
            sample = fab_lf.select(sample_cols)
            if include_diagnostics and meta["ts_col"] and meta["ts_col"] in fab_names:
                sample = sample.sort(meta["ts_col"], descending=True, nulls_last=True)
            vals = sample.head(5).collect()
            if meta["fab_col"] in vals.columns:
                meta["sample_fab_values"] = [
                    ("" if v is None else str(v)) for v in vals[meta["fab_col"]].to_list()
                ]
        except Exception as e:
            pass
        meta["enabled"] = True
    except Exception as e:
        meta["error"] = f"resolve 중 예외: {type(e).__name__}: {e}"
    return meta


def _resolve_override_meta_light(product: str) -> dict:
    """Cheap view badge metadata; avoid rescanning FAB source after /view already did."""
    meta = {
        "enabled": False, "manual_override": False,
        "fab_source": "", "fab_col": "fab_lot_id", "ts_col": "",
        "root_col": "", "wf_col": "", "join_keys": [], "override_cols": [],
        "scanned_count": 0, "row_count": 0, "sample_fab_values": [],
        "raw_columns": [], "runtime_columns": [], "column_aliases": {},
        "error": None,
    }
    try:
        product = _canonical_mltable_product_name(product, allow_bare=True) or str(product or "").strip()
        cfg = load_json(SOURCE_CFG, {}) if SOURCE_CFG.exists() else {}
        ov = _lot_override_for(cfg, product)
        manual = _normalize_fab_source_path((ov.get("fab_source") or "").strip())
        if manual.startswith("root:"):
            manual = ""
        fab_source = manual or _auto_derive_fab_source(product)
        meta["manual_override"] = bool(manual)
        meta["fab_source"] = fab_source
        meta["enabled"] = bool(fab_source)
        meta["root_col"] = (ov.get("root_col") or "").strip()
        meta["wf_col"] = (ov.get("wf_col") or ov.get("wafer_col") or "").strip()
        meta["fab_col"] = (ov.get("fab_col") or "fab_lot_id").strip() or "fab_lot_id"
        meta["ts_col"] = (ov.get("ts_col") or "").strip()
        join_keys = ov.get("join_keys") or []
        if isinstance(join_keys, str):
            join_keys = [k.strip() for k in join_keys.split(",") if k.strip()]
        meta["join_keys"] = list(join_keys)
        raw_oc = ov.get("override_cols")
        if isinstance(raw_oc, str):
            raw_oc = [c.strip() for c in raw_oc.split(",") if c.strip()]
        meta["override_cols"] = list(raw_oc or _DEFAULT_OVERRIDE_COLS)
        if not fab_source and product.casefold().startswith("ml_table_"):
            meta["error"] = "FAB source not matched"
    except Exception as e:
        meta["error"] = f"{type(e).__name__}: {e}"
    return meta

def _pick_first_present(candidates, available_names):
    av = set(available_names)
    for c in candidates:
        if c in av:
            return c
    return ""


def _pick_first_present_ci(candidates, available_names):
    """v8.8.22: case-insensitive 버전. 실제 스키마의 정확한 casing 을 반환."""
    ci = {n.casefold(): n for n in available_names}
    for c in candidates:
        actual = ci.get(c.casefold())
        if actual:
            return actual
    return ""


def _pick_ts_col(available_names):
    """Pick the most-likely time column from a FAB source."""
    primary = _pick_first_present_ci(_TS_COL_CANDIDATES, available_names)
    if primary:
        return primary
    for name in (available_names or []):
        low = str(name).casefold()
        if "time" in low or "timestamp" in low or low.endswith("_ts") or low.startswith("ts_"):
            return name
    return _pick_first_present_ci(("date",), available_names)


def _resolve_source_col_name(name: str, available_names):
    """Resolve user-facing raw/runtime column names against runtime source schema."""
    actual = _ci_resolve_in(name, available_names)
    if actual:
        return actual
    folded = str(name or "").strip().casefold()
    if not folded:
        return ""
    ci = {str(n).casefold(): n for n in (available_names or [])}
    for raw_name, runtime_name in _RAW_TO_RUNTIME_ALIAS_CANDIDATES.items():
        if folded == raw_name.casefold():
            actual = ci.get(runtime_name.casefold())
            if actual:
                return actual
        if folded == runtime_name.casefold():
            actual = ci.get(raw_name.casefold())
            if actual:
                return actual
    return ""


def _detect_source_column_aliases(raw_names, runtime_names):
    """Return raw->runtime aliases introduced by source adaptation."""
    raw_ci = {str(n).casefold(): n for n in (raw_names or [])}
    runtime_ci = {str(n).casefold(): n for n in (runtime_names or [])}
    out = {}
    for raw_name, runtime_name in _RAW_TO_RUNTIME_ALIAS_CANDIDATES.items():
        raw_actual = raw_ci.get(raw_name.casefold())
        runtime_actual = runtime_ci.get(runtime_name.casefold())
        if raw_actual and runtime_actual and runtime_name.casefold() not in raw_ci:
            out[raw_actual] = runtime_actual
    return out


def _prefer_raw_schema_name(name: str, raw_names, runtime_names):
    """Map runtime alias names back to physical raw schema names for UI display."""
    actual_raw = _ci_resolve_in(name, raw_names)
    if actual_raw:
        return actual_raw
    aliases = _detect_source_column_aliases(raw_names, runtime_names)
    runtime_to_raw = {str(v).casefold(): k for k, v in aliases.items()}
    return runtime_to_raw.get(str(name or "").strip().casefold(), name)


def _fab_source_context(product: str) -> dict:
    """Return the active FAB history source and resolved key columns for a product."""
    p = (product or "").strip()
    if not p:
        return {}
    ml_product = _canonical_mltable_product_name(p, allow_bare=True)
    try:
        cfg = load_json(SOURCE_CFG, {}) if SOURCE_CFG.exists() else {}
        ov = _lot_override_for(cfg, ml_product)
        fab_source = (ov.get("fab_source") or "").strip()
        if fab_source.startswith("root:"):
            fab_source = ""
        if not fab_source:
            fab_source = _auto_derive_fab_source(ml_product)
        if not fab_source:
            return {}
        _, resolved_fab_source = _resolve_fab_source_target(fab_source)
        if resolved_fab_source:
            fab_source = resolved_fab_source
        fab_lf = _scan_fab_source(fab_source)
        if fab_lf is None:
            return {}
        try:
            main_fp = _product_path(ml_product)
            if main_fp.suffix.lower() == ".csv":
                main_names = pl.scan_csv(str(main_fp), infer_schema_length=5000).collect_schema().names()
            else:
                main_names = _scan_parquet_compat(str(main_fp)).collect_schema().names()
        except Exception:
            main_names = []
        fab_lf, fab_names = _ci_align_fab_to_main(fab_lf, main_names)
        try:
            fab_names = fab_lf.collect_schema().names()
        except Exception:
            pass
        root_col = _resolve_source_col_name((ov.get("root_col") or "").strip(), fab_names) \
                   or _pick_first_present_ci(("root_lot_id",), fab_names)
        wafer_col = _resolve_source_col_name((ov.get("wf_col") or ov.get("wafer_col") or "").strip(), fab_names) \
                    or _pick_first_present_ci(("wafer_id", "wafer"), fab_names)
        fab_col = _resolve_source_col_name((ov.get("fab_col") or "").strip(), fab_names) \
                  or _pick_first_present_ci(_FAB_COL_CANDIDATES, fab_names)
        if not root_col or not fab_col:
            return {}
        return {
            "lf": fab_lf,
            "source": fab_source,
            "root_col": root_col,
            "wafer_col": wafer_col,
            "fab_col": fab_col,
            "columns": fab_names,
        }
    except Exception as e:
        logger.warning("_fab_source_context 실패 (product=%s) %s: %s",
                       product, type(e).__name__, e)
        return {}


def _clean_str(v) -> str:
    s = "" if v is None else str(v).strip()
    return "" if s in ("", "None", "null") else s


def _wafer_sort_key(v: str):
    s = str(v or "").strip()
    try:
        return (0, int(s.upper().lstrip("W")))
    except Exception:
        return (1, s.upper())


def _merge_wafer_scope(user_wafer_ids: str, source_wafers: list[str]) -> str:
    """Intersect user wafer filter with FAB-source wafer scope when both exist."""
    source = [_clean_str(w) for w in (source_wafers or [])]
    source = [w for w in source if w]
    if not source:
        return user_wafer_ids or ""
    user = [w.strip() for w in str(user_wafer_ids or "").split(",") if w.strip()]
    if not user:
        return ",".join(sorted(dict.fromkeys(source), key=_wafer_sort_key))

    def norm(w):
        s = str(w or "").strip().upper()
        try:
            return str(int(s.lstrip("W")))
        except Exception:
            return s

    user_norm = {norm(w) for w in user}
    kept = [w for w in source if norm(w) in user_norm]
    if not kept:
        return "__NO_WAFER_MATCH__"
    return ",".join(sorted(dict.fromkeys(kept), key=_wafer_sort_key))


def _fab_history_scope(product: str, root_lot_id: str = "", fab_lot_id: str = "",
                       prefix: str = "", limit: int = 500) -> dict:
    """Query raw FAB history without collapsing to latest row per wafer."""
    root_lot_id = root_lot_id if isinstance(root_lot_id, str) else ""
    fab_lot_id = fab_lot_id if isinstance(fab_lot_id, str) else ""
    prefix = prefix if isinstance(prefix, str) else ""
    try:
        limit = int(limit)
    except Exception:
        limit = 500
    cache_key = (
        "fab_history_scope",
        _lot_lookup_cache_sig(product),
        str(product or "").strip(),
        root_lot_id.strip(),
        fab_lot_id.strip(),
        prefix.strip(),
        limit,
    )
    cached = _lot_lookup_cache_get(cache_key)
    if cached is not None:
        return cached

    def finish(payload: dict) -> dict:
        return _lot_lookup_cache_set(cache_key, payload)

    ctx = _fab_source_context(product)
    if not ctx:
        return finish({"candidates": [], "root_ids": [], "wafer_ids": [], "source": ""})
    root_col = ctx["root_col"]
    fab_col = ctx["fab_col"]
    wafer_col = ctx.get("wafer_col") or ""
    select_exprs = [
        pl.col(root_col).cast(_STR, strict=False).alias("root"),
        pl.col(fab_col).cast(_STR, strict=False).alias("fab"),
    ]
    if wafer_col:
        select_exprs.append(pl.col(wafer_col).cast(_STR, strict=False).alias("wafer"))
    q = ctx["lf"].select(select_exprs)
    q = q.filter(pl.col("root").is_not_null() & pl.col("fab").is_not_null())
    root_scope = (root_lot_id or "").strip()
    fab_scope = (fab_lot_id or "").strip()
    if root_scope:
        q = q.filter(_join_key_expr("root") == root_scope.upper())
    if fab_scope:
        q = q.filter(_join_key_expr("fab") == fab_scope.upper())
    elif prefix.strip():
        q = q.filter(_contains_literal_ci_expr("fab", prefix))
    try:
        fabs = _limited_unique_values(
            q, "fab", prefix="", limit=limit,
            preview_only=not bool(root_scope or fab_scope or prefix.strip()),
        )
        roots: list[str] = [root_scope] if root_scope else []
        wafers: list[str] = []
        # Exact fab lookup is used by /view to infer the root and wafer scope.
        # Keep that metadata precise, but avoid collecting it for broad previews.
        if fab_scope and fabs:
            meta_cols = [pl.col("root")]
            if wafer_col:
                meta_cols.append(pl.col("wafer"))
            meta_df = q.select(meta_cols).unique().collect()
            roots = sorted({s for s in (_clean_str(v) for v in meta_df["root"].to_list()) if s})
            if "wafer" in meta_df.columns:
                wafers = sorted({s for s in (_clean_str(v) for v in meta_df["wafer"].to_list()) if s}, key=_wafer_sort_key)
    except Exception as e:
        logger.warning("_fab_history_scope 조회 실패 (product=%s) %s: %s",
                       product, type(e).__name__, e)
        return finish({"candidates": [], "root_ids": [], "wafer_ids": [], "source": ctx.get("source", "")})
    if not fabs:
        return finish({"candidates": [], "root_ids": [], "wafer_ids": [], "source": ctx.get("source", "")})
    return finish({
        "candidates": fabs,
        "root_ids": roots,
        "wafer_ids": wafers,
        "source": ctx.get("source", ""),
    })


def _fab_history_root_candidates(product: str, prefix: str = "", limit: int = 500) -> dict:
    """Return root_lot_id candidates from the configured FAB DB source.

    SplitTable's editable source is ML_TABLE_*, but operators choose lots from
    the live FAB history.  Use the configured fab_source first so the dropdown
    follows the same DB path that /view and fab_lot_id matching use.
    """
    try:
        limit = max(1, int(limit or 500))
    except Exception:
        limit = 500
    cache_key = (
        "fab_history_root_candidates",
        _lot_lookup_cache_sig(product),
        str(product or "").strip(),
        str(prefix or "").strip(),
        limit,
    )
    cached = _lot_lookup_cache_get(cache_key)
    if cached is not None:
        return cached

    def finish(payload: dict) -> dict:
        return _lot_lookup_cache_set(cache_key, payload)

    ctx = _fab_source_context(product)
    if not ctx:
        return finish({"candidates": [], "source": ""})
    root_col = ctx.get("root_col") or ""
    if not root_col:
        return finish({"candidates": [], "source": ctx.get("source", "")})
    try:
        values = _limited_unique_values(ctx["lf"], root_col, prefix=prefix, limit=limit)
    except Exception as e:
        logger.warning("_fab_history_root_candidates 실패 (product=%s) %s: %s",
                       product, type(e).__name__, e)
        return finish({"candidates": [], "source": ctx.get("source", "")})
    return finish({"candidates": values, "source": ctx.get("source", "")})


def _merge_candidate_values(*groups, limit: int = 500) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    try:
        limit = max(1, int(limit or 500))
    except Exception:
        limit = 500
    for group in groups:
        for value in group or []:
            text = _clean_str(value)
            if not text:
                continue
            key = text.upper()
            if key in seen:
                continue
            seen.add(key)
            out.append(text)
            if len(out) >= limit:
                return out
    return out


def _candidate_values_from_frame(rows, value_col: str = "v", limit: int = 500) -> list[str]:
    """Return clean string autocomplete values from a collected Polars frame."""
    values: list[str] = []
    seen: set[str] = set()
    try:
        limit = max(1, int(limit or 500))
    except Exception:
        limit = 500
    if rows is None or value_col not in rows.columns:
        return values
    for value in rows[value_col].to_list():
        text = _clean_str(value)
        if not text:
            continue
        key = text.upper()
        if key in seen:
            continue
        seen.add(key)
        values.append(text)
        if len(values) >= limit:
            break
    return values


def _limited_unique_values(lf, col: str, prefix: str = "", limit: int = 500,
                           preview_only: bool = True) -> list[str]:
    """Return bounded autocomplete values without scanning broad empty-prefix lists.

    Empty dropdowns only need a preview.  Once a user types, prefix filtering must
    search the full source so values outside the preview are still discoverable.
    """
    try:
        limit = max(1, int(limit or 500))
    except Exception:
        limit = 500
    prefix = prefix if isinstance(prefix, str) else ""
    q = (
        lf.select(pl.col(col).cast(_STR, strict=False).alias("v"))
        .filter(pl.col("v").is_not_null())
    )
    if prefix.strip():
        q = q.filter(_contains_literal_ci_expr("v", prefix))
        rows = q.unique().sort("v").head(limit).collect()
    elif not preview_only:
        rows = q.unique().sort("v").head(limit).collect()
    else:
        sample_limit = max(limit, min(limit * 20, 10000))
        rows = q.head(sample_limit).unique(maintain_order=True).head(limit).collect()
    values = _candidate_values_from_frame(rows, "v", limit)
    return sorted(values, key=lambda s: str(s).upper())


def _main_table_candidates(product: str, col: str = "root_lot_id", prefix: str = "",
                           limit: int = 500, root_lot_id: str = "") -> dict:
    """Return candidates from the actual SplitTable render source.

    FAB history can contain operational roots that are not present in the
    current ML_TABLE. Those roots are useful for lineage, but they produce an
    empty SplitTable view. Autocomplete should therefore prefer values that can
    actually render in /view.
    """
    try:
        limit = max(1, int(limit or 500))
    except Exception:
        limit = 500
    cache_key = (
        "main_table_candidates",
        _lot_lookup_cache_sig(product),
        str(product or "").strip(),
        str(col or "").strip(),
        str(prefix or "").strip(),
        str(root_lot_id or "").strip(),
        limit,
    )
    cached = _lot_lookup_cache_get(cache_key)
    if cached is not None:
        return cached

    def finish(payload: dict) -> dict:
        return _lot_lookup_cache_set(cache_key, payload)

    try:
        lf = _scan_product_base(product)
        schema_names = lf.collect_schema().names()
        lot_col, _ = _detect_lot_wafer(lf, product)
        target = ""
        if str(col or "").casefold() == "root_lot_id":
            target = lot_col or _ci_resolve_in("root_lot_id", schema_names)
        elif str(col or "").casefold() in {c.casefold() for c in _FAB_COL_CANDIDATES}:
            target = (
                _ci_resolve_in("fab_lot_id", schema_names)
                or _ci_resolve_in("lot_id", schema_names)
                or _pick_first_present_ci(_FAB_COL_CANDIDATES, schema_names)
            )
        else:
            target = _ci_resolve_in(col, schema_names)
        if not target or target not in schema_names:
            return finish({"candidates": [], "source_col": target or col, "root_ids": []})

        root_scope = _clean_str(root_lot_id)
        if root_scope:
            root_col = lot_col or _ci_resolve_in("root_lot_id", schema_names)
            if root_col and root_col in schema_names:
                lf = lf.filter(_join_key_expr(root_col) == root_scope.upper())

        values = _limited_unique_values(lf, target, prefix=prefix, limit=limit,
                                        preview_only=not bool(root_scope))
        return finish({"candidates": values, "source_col": target, "root_ids": values if str(col or "").casefold() == "root_lot_id" else []})
    except Exception as e:
        logger.warning("_main_table_candidates 실패 (product=%s col=%s) %s: %s",
                       product, col, type(e).__name__, e)
        return finish({"candidates": [], "source_col": col, "root_ids": []})


def _scan_product(product: str, root_lot_id: str = "", fab_lot_id: str = "",
                  wafer_ids: str = ""):
    """Scan ML_TABLE_<PROD>.parquet + hive override join.

    v8.8.26: 실패 경로마다 logger.warning 로 가시화 (이전 blanket except 제거).
      - CI align 이후 fab schema 를 **재조회** 해서 rename 이 실제로 적용됐는지 확인.
      - override_cols 가 join_keys 만 남으면 경고 후 raw lf 반환.
    """
    product = _canonical_mltable_product_name(product, allow_bare=True) or str(product or "").strip()
    lf = _scan_product_base(product)

    # v8.8.3: 오버라이드 로직 근본 재정리.
    #   1) 매뉴얼 config(lot_overrides[product].fab_source) 가 있으면 그 값을 사용.
    #   2) 없으면 ML_TABLE_<PROD> → DB/<root>/<PROD> 자동 매칭 시도.
    #   3) ts_col / fab_col 도 매뉴얼 > 자동 추론 순.
    #   4) 조인은 항상 "ts_col 기준 최신 레코드만" join keys 별로 picking 후 left-join.
    try:
        cfg = load_json(SOURCE_CFG, {}) if SOURCE_CFG.exists() else {}
        ov = _lot_override_for(cfg, product)
        fab_source = (ov.get("fab_source") or "").strip()
        # v8.8.21: legacy root:~~ 무시 → auto-derive.
        if fab_source.startswith("root:"):
            fab_source = ""
        if not fab_source:
            fab_source = _auto_derive_fab_source(product)
        if not fab_source:
            return _strip_non_authoritative_fab_fields(lf, product)
        fab_lf = _scan_fab_source(fab_source)
        if fab_lf is None:
            logger.warning("_scan_product: _scan_fab_source 가 None (product=%s fab_source=%s)",
                           product, fab_source)
            return _strip_non_authoritative_fab_fields(lf, product)

        try:
            main_names_list = lf.collect_schema().names()
        except Exception as e:
            logger.warning("_scan_product: main schema 조회 실패 (product=%s) %s: %s",
                           product, type(e).__name__, e)
            return lf
        if root_lot_id or wafer_ids:
            try:
                main_lot_col, main_wf_col = _detect_lot_wafer(lf, product)
                lf = _filter_lot_wafer(
                    lf, main_lot_col, main_wf_col,
                    root_lot_id=root_lot_id,
                    wafer_ids=wafer_ids,
                )
            except Exception as e:
                logger.warning("_scan_product: main scope filter 실패 (product=%s root=%s wafer=%s) %s: %s",
                               product, root_lot_id, wafer_ids, type(e).__name__, e)

        # v8.8.22: CI 정렬 — fab_lf 컬럼명을 main 쪽 casing 으로 rename.
        #   ex) ML_TABLE 의 ROOT_LOT_ID ↔ hive root_lot_id → join 성공.
        fab_lf, _ = _ci_align_fab_to_main(fab_lf, main_names_list)
        # v8.8.26: rename 이 silently 실패할 수 있으므로 schema 를 재조회 — 신뢰 가능한 true state.
        try:
            fab_schema_names = fab_lf.collect_schema().names()
        except Exception as e:
            logger.warning("_scan_product: fab post-align schema 조회 실패 (product=%s) %s: %s",
                           product, type(e).__name__, e)
            return lf
        main_names = set(main_names_list)
        fab_names = set(fab_schema_names)

        join_keys = ov.get("join_keys") or []
        if isinstance(join_keys, str):
            join_keys = [k.strip() for k in join_keys.split(",") if k.strip()]
        if join_keys:
            mapped = []
            for k in join_keys:
                actual = _ci_resolve_in(k, main_names_list) or _resolve_source_col_name(k, fab_schema_names)
                if actual:
                    mapped.append(actual)
            join_keys = mapped
        if not join_keys:
            join_keys = _default_override_join_keys(main_names_list, fab_schema_names)
        join_keys = [k for k in join_keys if k in main_names and k in fab_names]
        if not join_keys:
            logger.warning(
                "_scan_product: 공통 join key 없음 (product=%s fab_source=%s main=%s fab=%s)",
                product, fab_source, main_names_list[:20], fab_schema_names[:20],
            )
            return lf

        fc_raw = (ov.get("fab_col") or "").strip()
        fab_col = (_resolve_source_col_name(fc_raw, fab_schema_names) if fc_raw else "") \
                  or _pick_first_present_ci(_FAB_COL_CANDIDATES, fab_schema_names)
        if not fab_col:
            fab_col = "fab_lot_id"
        tc_raw = (ov.get("ts_col") or "").strip()
        ts_col = (_resolve_source_col_name(tc_raw, fab_schema_names) if tc_raw else "") \
                 or _pick_ts_col(fab_schema_names)
        fab_lf = _apply_fab_scope_filters(
            fab_lf, fab_schema_names, ov,
            root_lot_id=root_lot_id,
            fab_lot_id=fab_lot_id,
            wafer_ids=wafer_ids,
            fab_col=fab_col,
        )

        raw_oc = ov.get("override_cols")
        if isinstance(raw_oc, str):
            raw_oc = [c.strip() for c in raw_oc.split(",") if c.strip()]
        if not raw_oc:
            raw_oc = list(_DEFAULT_OVERRIDE_COLS)
        if fab_col and fab_col not in raw_oc:
            raw_oc = list(raw_oc) + [fab_col]
        resolved_oc = []
        for c in raw_oc:
            actual = _resolve_source_col_name(c, fab_schema_names)
            resolved_oc.append(actual or c)
        override_cols = [c for c in dict.fromkeys(resolved_oc)
                         if c in fab_names and c not in join_keys]
        wanted = list(dict.fromkeys(join_keys + override_cols + ([ts_col] if ts_col else [])))
        wanted = [c for c in wanted if c in fab_names]
        if not override_cols:
            logger.warning(
                "_scan_product: override_cols 가 비어있음 — join 없이 raw lf 반환 "
                "(product=%s fab_source=%s raw_oc=%s fab_names=%s)",
                product, fab_source, raw_oc, fab_schema_names[:20],
            )
            return lf

        fab_proj = fab_lf.select(wanted)
        join_aliases = [(k, f"__join_key_{i}") for i, k in enumerate(join_keys)]
        fab_proj = fab_proj.with_columns([_join_key_expr(k).alias(tmp) for k, tmp in join_aliases])
        lf = lf.with_columns([_join_key_expr(k).alias(tmp) for k, tmp in join_aliases])
        join_tmp_keys = [tmp for _, tmp in join_aliases]
        fab_proj = fab_proj.select(list(dict.fromkeys(join_tmp_keys + override_cols + ([ts_col] if ts_col else []))))
        if ts_col and ts_col in fab_names:
            fab_proj = fab_proj.sort(ts_col, descending=True, nulls_last=True)
            fab_proj = fab_proj.unique(subset=join_tmp_keys, keep="first", maintain_order=True)
        else:
            fab_proj = fab_proj.unique(subset=join_tmp_keys, keep="last")
        # v9.0.1: main 에 이미 존재하는 override_cols 를 drop 하면 fab_source 에 매칭 행이
        #   없는 row 의 fab_lot_id 가 NULL 이 되어 wafer_fab_list / available_fab_lots /
        #   /lot-candidates root_join 이 모두 빈 결과가 됨. 사용자 시드/사내 ML_TABLE 은
        #   이미 정확한 root↔fab 매핑을 가지고 있으므로 **main 원본을 우선**, fab_source 는
        #   main 이 비었을 때만 보충 (coalesce(main, fab)).
        backup_cols: list = []
        for c in override_cols:
            if c in main_names:
                bk = f"__main_bk_{c}"
                lf = lf.with_columns(pl.col(c).alias(bk))
                backup_cols.append((c, bk))
                lf = lf.drop(c)
        lf = lf.join(fab_proj, on=join_tmp_keys, how="left").drop(join_tmp_keys)
        for c, bk in backup_cols:
            if c.casefold() == "fab_lot_id":
                # fab_lot_id must be authoritative from DB FAB only. If the DB FAB
                # row did not match, leave it null instead of falling back to ML_TABLE.
                lf = lf.drop(bk)
            else:
                # Non-FAB operational attributes may still fall back to ML-side values
                # when the joined FAB row is missing.
                lf = lf.with_columns(pl.coalesce([pl.col(c), pl.col(bk)]).alias(c)).drop(bk)
        return lf
    except Exception as e:
        # v8.8.26: blanket except 유지하되 반드시 로그를 남겨 진단 가능하게.
        logger.warning("_scan_product: 예상치 못한 예외 (product=%s) %s: %s",
                       product, type(e).__name__, e, exc_info=True)
        return _strip_non_authoritative_fab_fields(lf, product)

@router.get("/lot-ids")
def get_lot_ids(product: str = Query(...), limit: int = Query(200)):
    lf = _scan_product(product)
    lot_col, _ = _detect_lot_wafer(lf)
    lots_list: list = []
    fallback_used = False
    try:
        lots_list = _limited_unique_values(lf, lot_col, limit=limit)
    except Exception as e:
        logger.warning("/lot-ids: main lf 조회 실패 (product=%s) %s: %s",
                       product, type(e).__name__, e)
        lots_list = []
    fab_roots: list[str] = []
    fab_source = ""
    try:
        hist = _fab_history_root_candidates(product, limit=limit)
        fab_roots = hist.get("candidates") or []
        fab_source = hist.get("source") or ""
    except Exception as e:
        logger.warning("/lot-ids: FAB root 후보 조회 실패 (product=%s) %s: %s",
                       product, type(e).__name__, e)
    if fab_roots:
        # Keep the dropdown aligned with what /view can render.  If ML_TABLE has
        # roots, only append FAB roots that are also present there; otherwise a
        # user can pick a valid FAB history root and still get an empty table.
        if lots_list:
            main_keys = {str(v).upper() for v in lots_list}
            fab_roots = [v for v in fab_roots if str(v).upper() in main_keys]
            lots_list = _merge_candidate_values(lots_list, fab_roots, limit=limit)
        else:
            lots_list = _merge_candidate_values(fab_roots, limit=limit)
            fallback_used = True
    # v8.8.26: main 이 all-null 이거나 비어있으면 override fab_source 로 폴백.
    if not lots_list:
        try:
            meta = _resolve_override_meta(product, include_diagnostics=False)
            fab_source = (meta.get("fab_source") or "").strip()
            if fab_source and not meta.get("error"):
                fab_lf = _scan_fab_source(fab_source)
                if fab_lf is not None:
                    fab_names = fab_lf.collect_schema().names()
                    # CI 매칭으로 root_lot_id 를 찾는다.
                    target = next((n for n in fab_names
                                   if n.casefold() == "root_lot_id"), None)
                    if target:
                        lots_list = _limited_unique_values(fab_lf, target, limit=limit)
                        if lots_list:
                            fallback_used = True
                            lot_col = target
        except Exception as e:
            logger.warning("/lot-ids: override 폴백 실패 (product=%s) %s: %s",
                           product, type(e).__name__, e)
    return {"lot_col": lot_col, "lot_ids": lots_list,
            "fallback": "fab_source" if fallback_used else "",
            "fab_source": fab_source}


@router.get("/lot-candidates")
def get_lot_candidates(
    product: str = Query(...),
    col: str = Query("root_lot_id"),
    prefix: str = Query(""),
    limit: int = Query(30),
    source: str = Query("auto"),   # v8.8.19: auto|override|mltable
    root_lot_id: str = Query(""),  # v9.0.0 (Q1): fab_lot_id 드롭다운을 특정 root 로 제한
):
    """Autocomplete 후보 반환. col 은 'root_lot_id' 또는 'fab_lot_id'. prefix 가
    비어있으면 최신/정렬 상위 N개, 아니면 prefix 포함 매칭을 정렬 순 top N.

    v8.8.19: `source` 인자 추가.
    v9.0.0: `root_lot_id` 파라미터 추가 — fab_lot_id 후보를 해당 root (앞 5자) 로 제한.
      (예: root_lot_id=A0001 → A0001 로 시작하는 fab_lot_id 만 반환)
    """
    # v9.0.5: fab_lot_id 후보는 DB FAB 원천 이력의 정확한 root/fab 매칭만 허용.
    #   DB FAB 에 없으면 ML_TABLE LOT_ID, starts_with, 전체 후보 fallback 으로 회피하지 않는다.
    root_scope = (root_lot_id or "").strip()
    if col.casefold() == "root_lot_id":
        main = _main_table_candidates(product, "root_lot_id", prefix=prefix, limit=limit)
        hist = _fab_history_root_candidates(product, prefix=prefix, limit=limit)
        main_candidates = main.get("candidates") or []
        hist_candidates = hist.get("candidates") or []
        if main_candidates:
            main_keys = {str(v).upper() for v in main_candidates}
            hist_candidates = [v for v in hist_candidates if str(v).upper() in main_keys]
        merged = _merge_candidate_values(main_candidates, hist_candidates, limit=limit)
        if merged:
            return {
                "col": "root_lot_id",
                "candidates": merged,
                "prefix": prefix,
                "root_scope": root_scope,
                "match_mode": "splittable_roots",
                "source": "mltable",
                "fab_source": hist.get("source", ""),
                "strict": False,
            }
        if hist.get("candidates"):
            return {
                "col": "root_lot_id",
                "candidates": hist.get("candidates") or [],
                "prefix": prefix,
                "root_scope": root_scope,
                "match_mode": "fab_history_roots",
                "source": "fab_source_history",
                "fab_source": hist.get("source", ""),
                "strict": True,
            }
        fallback = get_lot_ids(product=product, limit=limit)
        fallback_candidates = _merge_candidate_values(fallback.get("lot_ids") or [], limit=limit)
        if prefix.strip():
            fallback_candidates = [v for v in fallback_candidates if prefix.strip().upper() in str(v).upper()]
        if fallback_candidates:
            return {
                "col": "root_lot_id",
                "candidates": fallback_candidates,
                "prefix": prefix,
                "root_scope": root_scope,
                "match_mode": "detected_lot_col_fallback",
                "source": "lot_ids",
                "source_col": fallback.get("lot_col", ""),
                "fab_source": fallback.get("fab_source", ""),
                "strict": False,
            }
    if col.casefold() in {c.casefold() for c in _FAB_COL_CANDIDATES}:
        main = _main_table_candidates(product, col, prefix=prefix, limit=limit, root_lot_id=root_scope)
        hist = _fab_history_scope(product, root_lot_id=root_scope, prefix=prefix, limit=limit)
        main_candidates = main.get("candidates") or []
        hist_candidates = hist.get("candidates") or []
        if main_candidates:
            main_keys = {str(v).upper() for v in main_candidates}
            hist_candidates = [v for v in hist_candidates if str(v).upper() in main_keys]
        merged = _merge_candidate_values(main_candidates, hist_candidates, limit=limit)
        if merged:
            return {
                "col": col,
                "candidates": merged,
                "prefix": prefix,
                "root_scope": root_scope,
                "match_mode": "splittable_fab_lots" if root_scope else "splittable_fab_lots_all",
                "source": "mltable",
                "fab_source": hist.get("source", ""),
                "strict": False,
            }
        return {
            "col": col,
            "candidates": hist.get("candidates") or [],
            "prefix": prefix,
            "root_scope": root_scope,
            "match_mode": "fab_history_root" if root_scope else "fab_history",
            "source": "fab_source_history",
            "fab_source": hist.get("source", ""),
            "strict": True,
        }
    use_override = False
    lf = None
    if source == "override" and product.casefold().startswith("ml_table_"):
        try:
            meta = _resolve_override_meta(product, include_diagnostics=False)
            fab_source = (meta.get("fab_source") or "").strip()
            if fab_source and not meta.get("error"):
                fab_lf = _scan_fab_source(fab_source)
                if fab_lf is not None:
                    lf = fab_lf
                    use_override = True
        except Exception:
            lf = None
        if lf is None:
            return {"col": col, "candidates": [], "source": "override",
                    "note": "override 비활성 또는 fab_source 없음"}
    if lf is None:
        lf = _scan_product(
            product,
            root_lot_id=root_scope if col.casefold() != "root_lot_id" else "",
        )

    schema_names = lf.collect_schema().names()
    # v8.8.26: CI 매칭 — FE 가 "ROOT_LOT_ID"(ML_TABLE casing) 로 요청해도 raw 소스의
    # "root_lot_id" 로 정확히 매핑 (이전에는 exact match 만 되어 override 경로에서 누락).
    if col not in schema_names:
        col_ci = next((n for n in schema_names if n.casefold() == col.casefold()), None)
        if col_ci:
            col = col_ci
        else:
            # fallback — root 이면 auto-detect lot col, fab 는 그대로
            if col.casefold() == "root_lot_id":
                lot_col, _ = _detect_lot_wafer(lf)
                col = lot_col or col
            if col not in schema_names:
                return {"col": col, "candidates": [], "available_cols": schema_names[:20],
                        "source": "override" if use_override else "mltable"}

    match_mode = "all"
    fallback_used = False

    # v9.0.1: root_scope + fab_lot_id 조회 시 데이터-중심 매칭.
    #   데이터에서 root_lot_id 와 fab_lot_id 의 앞 5자가 자연 일치하지 않는 케이스 (예:
    #   ML_TABLE root=A0015 → fab_lot=A0005B.1) 에서 단순 starts_with 가 0건을 반환하던 문제.
    #   1) main lf 에서 root_lot_id 컬럼을 CI 매칭으로 찾고, 같은 row 의 fab_lot_id 를 unique 추출.
    #   2) (1) 결과가 비면 → 기존 starts_with 폴백.
    #   3) (2) 도 비면 → root_scope 무시하고 전체 후보 반환 (sentinel: fallback_used=True).
    if root_scope and col.casefold() != "root_lot_id":
        root_col = next((n for n in schema_names if n.casefold() == "root_lot_id"), None)
        if root_col:
            try:
                q_join = (lf.filter(_join_key_expr(root_col) == root_scope.strip().upper())
                            .select(pl.col(col).cast(_STR, strict=False).alias("v"))
                            .drop_nulls().unique())
                if prefix.strip():
                    q_join = q_join.filter(_contains_literal_ci_expr("v", prefix))
                rows_join = q_join.sort("v").head(limit).collect()
                cand_join = [v for v in rows_join["v"].to_list()
                             if v and str(v).strip() not in ("", "None", "null")]
                if cand_join:
                    return {"col": col, "candidates": cand_join, "prefix": prefix,
                            "root_scope": root_scope, "match_mode": "root_join",
                            "source": "override" if use_override else "mltable"}
            except Exception as e:
                logger.warning("/lot-candidates: root_join 실패 (product=%s) %s: %s",
                               product, type(e).__name__, e)

    q = lf.select(pl.col(col).cast(_STR, strict=False).alias("v")).drop_nulls().unique()
    if prefix.strip():
        q = q.filter(_contains_literal_ci_expr("v", prefix))
    if root_scope and col.casefold() != "root_lot_id":
        # 폴백 1: starts_with 5자 prefix
        try:
            q_sw = q.filter(pl.col("v").str.starts_with(root_scope[:5]))
            rows_sw = q_sw.sort("v").head(limit).collect()
            if rows_sw.height > 0:
                match_mode = "starts_with"
                return {"col": col, "candidates": rows_sw["v"].to_list(), "prefix": prefix,
                        "root_scope": root_scope, "match_mode": match_mode,
                        "source": "override" if use_override else "mltable"}
        except Exception:
            pass
        fallback_used = True
        match_mode = "all_fallback"
    rows = q.sort("v").head(limit).collect()
    return {"col": col, "candidates": rows["v"].to_list(), "prefix": prefix,
            "root_scope": root_scope, "match_mode": match_mode,
            "root_scope_fallback": fallback_used,
            "source": "override" if use_override else "mltable"}


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
    root_scope = root_lot_id.strip()
    fab_scope = fab_lot_id.strip()
    schema_names = lf.collect_schema().names()
    if root_scope and lot_col and lot_col in schema_names:
        lf = lf.filter(_join_key_expr(lot_col) == root_scope.upper())
    if fab_scope and fab_lot_col in schema_names:
        lf = lf.filter(_join_key_expr(fab_lot_col) == fab_lot_id.strip().upper())
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


def _ml_product_name(product: str) -> str:
    p = str(product or "").strip()
    if not p:
        return ""
    return _canonical_mltable_product_name(p, allow_bare=True)


def resolve_fab_lot_snapshot(product: str, root_lot_id: str, wafer_id: str = "") -> str:
    """Return the fab_lot_id from the same coalesced SplitTable data users see."""
    ml_product = _ml_product_name(product)
    root = str(root_lot_id or "").strip()
    if not ml_product or not root:
        return ""
    try:
        lf = _scan_product(ml_product, root_lot_id=root, wafer_ids=str(wafer_id or ""))
        lot_col, wf_col = _detect_lot_wafer(lf, ml_product)
        if not lot_col:
            return ""
        names = lf.collect_schema().names()
        fab_col = "fab_lot_id" if "fab_lot_id" in names else ""
        if not fab_col:
            fab_col = _pick_first_present_ci(_FAB_COL_CANDIDATES, names) or ""
        if not fab_col:
            return ""
        lf = _filter_lot_wafer(lf, lot_col, wf_col, root, str(wafer_id or ""),
                               fab_lot_col=fab_col)
        df = (
            lf.select(pl.col(fab_col).cast(_STR, strict=False).alias("fab_lot_id"))
            .drop_nulls()
            .unique()
            .sort("fab_lot_id")
            .head(1)
            .collect()
        )
        if df.height == 0:
            return ""
        return str(df.item(0, 0) or "").strip()
    except Exception as e:
        logger.warning("resolve_fab_lot_snapshot 실패 (product=%s root=%s wafer=%s) %s: %s",
                       product, root_lot_id, wafer_id, type(e).__name__, e)
        return ""


def _resolve_fab_lot_for_cell(product: str, cell_key: str, root_lot_id: str = "") -> str:
    parts = str(cell_key or "").split("|")
    root = str(root_lot_id or (parts[0] if len(parts) >= 1 else "") or "").strip()
    wafer = str(parts[1] if len(parts) >= 2 else "").strip()
    return resolve_fab_lot_snapshot(product, root, wafer)


# ── View ──
@router.get("/view")
def view_split(product: str = Query(...), root_lot_id: str = Query(""),
               wafer_ids: str = Query(""), prefix: str = Query("KNOB"),
               custom_name: str = Query(""), view_mode: str = Query("all"),
               history_mode: str = Query("all"),
               fab_lot_id: str = Query(""),
               custom_cols: str = Query("")):
    # v8.8.33: custom_cols (쉼표 구분) 추가 — Save 없이 체크만 한 컬럼을 ad-hoc 으로 전달.
    # v9.0.3: 한 root_lot_id 아래 여러 fab_lot_id 가 정상이다. FAB 공정 진행 중
    #   fab_lot_id 가 바뀔 수 있으므로 앞 5자 일치 여부를 검증/경고 기준으로 쓰지 않는다.
    _history_mode = (history_mode or "all").strip().lower() or "all"
    if _history_mode not in ("all", "final", "lot_all"):
        raise HTTPException(400, "history_mode must be one of: all, final, lot_all")
    _lot_warn = ""
    fp = _product_path(product)
    try:
        lf = _scan_product(product, root_lot_id=root_lot_id,
                           fab_lot_id=fab_lot_id, wafer_ids=wafer_ids)
        lot_col, wf_col = _detect_lot_wafer(lf, product)
        # v8.4.4/v8.8.3: fab_lot_col — 매뉴얼 override > 자동 추론 > "fab_lot_id".
        fab_lot_col = "fab_lot_id"
        try:
            schema_names = lf.collect_schema().names()
            _cfg = load_json(SOURCE_CFG, {}) or {}
            _ov = _lot_override_for(_cfg, product)
            _fc = (_ov.get("fab_col") or "").strip()
            if _fc and _fc in schema_names:
                fab_lot_col = _fc
            elif "fab_lot_id" not in schema_names:
                # 자동 보강된 컬럼 이름 중 하나로 대체.
                for c in _FAB_COL_CANDIDATES:
                    if c in schema_names:
                        fab_lot_col = c
                        break
        except Exception:
            pass

        if not root_lot_id.strip() and not fab_lot_id.strip():
            return {"product": product, "lot_col": lot_col, "wf_col": wf_col,
                    "headers": [], "rows": [], "prefixes": _load_prefixes(),
                    "msg": "Enter a Root Lot ID or Fab Lot ID to view"}

        fab_scope = {}
        fab_filter_for_join = fab_lot_id
        if fab_lot_id.strip():
            # v9.0.5: fab_lot_id 는 DB FAB 원천에서 정확히 매칭될 때만 유효하다.
            # v9.0.6: 다만 사내/데모 파일이 이미 ML_TABLE 안에 fab/lot 값을 가진 경우도
            # 있으므로 FAB history scope 가 없다고 즉시 종료하지 않고 coalesced /view
            # 데이터에서 한 번 더 필터한다.
            fab_scope = _fab_history_scope(product, root_lot_id=root_lot_id,
                                           fab_lot_id=fab_lot_id, limit=5000)
            src_wafers = fab_scope.get("wafer_ids") or []
            if src_wafers:
                if not root_lot_id.strip() and fab_scope.get("root_ids"):
                    root_lot_id = fab_scope["root_ids"][0]
                wafer_ids = _merge_wafer_scope(wafer_ids, src_wafers)
                fab_filter_for_join = ""

        lf = _filter_lot_wafer(lf, lot_col, wf_col, root_lot_id, wafer_ids,
                               fab_lot_id=fab_filter_for_join, fab_lot_col=fab_lot_col)

        def _prepare_view_frame(view_lf):
            view_schema = view_lf.collect_schema().names()
            all_data = [c for c in view_schema if c != lot_col and c != wf_col]
            sel = _select_columns(all_data, custom_name, prefix,
                                  max_fallback=50, custom_cols=custom_cols)
            if not custom_name and not custom_cols:
                for raw_pref in [p.strip() for p in str(prefix or "").split(",") if p.strip()]:
                    for virt in _virtual_columns_for_prefix(product, raw_pref):
                        if virt not in sel:
                            sel.append(virt)
            rename = _build_col_rename_map(sel, product)
            sel = sorted(sel, key=lambda c: _natural_param_key(rename.get(c, c)))
            keep_cols = []
            for c in (lot_col, wf_col):
                if c and c in view_schema and c not in keep_cols:
                    keep_cols.append(c)
            keep_fab_col = "fab_lot_id" if "fab_lot_id" in view_schema else None
            if not keep_fab_col:
                keep_fab_col = (
                    _ci_resolve_in(fab_lot_col, view_schema)
                    or _pick_first_present_ci(_FAB_COL_CANDIDATES, view_schema)
                    or None
                )
            if keep_fab_col and keep_fab_col in view_schema and keep_fab_col not in keep_cols:
                keep_cols.append(keep_fab_col)
            for c in sel:
                if c in view_schema and c not in keep_cols:
                    keep_cols.append(c)
            q = view_lf.select(keep_cols) if keep_cols else view_lf
            return q.head(500).collect(), all_data, sel, rename

        df, all_data_cols, selected, col_rename = _prepare_view_frame(lf)
        if df.height == 0 and root_lot_id.strip() and fab_lot_id.strip():
            # If the UI carries a stale Fab Lot while the operator searches a
            # valid root lot, do not let the stale secondary field hide the
            # renderable SplitTable rows. Root remains the primary scope.
            try:
                root_only_lf = _scan_product(product, root_lot_id=root_lot_id,
                                             wafer_ids=wafer_ids)
                root_only_df = _filter_lot_wafer(
                    root_only_lf, lot_col, wf_col, root_lot_id, wafer_ids,
                    fab_lot_col=fab_lot_col,
                )
                root_only_df, all_data_cols, selected, col_rename = _prepare_view_frame(root_only_df)
                if root_only_df.height > 0:
                    df = root_only_df
                    _lot_warn = "Fab Lot ID와 Root Lot ID 조합이 없어 Root Lot ID 기준으로 조회했습니다."
            except Exception as e:
                logger.warning("view_split root-only fallback 실패 (product=%s root=%s fab=%s) %s: %s",
                               product, root_lot_id, fab_lot_id, type(e).__name__, e)
        if df.height == 0:
            # Operators often paste the FAB lot value they found in File Browser
            # into the Root Lot field. Treat that as a fab_lot_id lookup before
            # declaring the SplitTable empty.
            root_input = root_lot_id.strip()
            if root_input and not fab_lot_id.strip():
                try:
                    fallback_lf = _scan_product(product, fab_lot_id=root_input,
                                                wafer_ids=wafer_ids)
                    fallback_names = fallback_lf.collect_schema().names()
                    fallback_fab_col = (
                        _ci_resolve_in(fab_lot_col, fallback_names)
                        or _pick_first_present_ci(_FAB_COL_CANDIDATES, fallback_names)
                    )
                    if fallback_fab_col:
                        fallback_lf = _filter_lot_wafer(
                            fallback_lf, lot_col, wf_col, "",
                            wafer_ids, fab_lot_id=root_input,
                            fab_lot_col=fallback_fab_col,
                        )
                        fallback_df, all_data_cols, selected, col_rename = _prepare_view_frame(fallback_lf)
                        if fallback_df.height > 0:
                            df = fallback_df
                            fab_lot_id = root_input
                            root_lot_id = ""
                            _lot_warn = "입력한 Root Lot ID를 fab_lot_id로 해석해 조회했습니다."
                except Exception as e:
                    logger.warning("view_split fab_lot fallback 실패 (product=%s input=%s) %s: %s",
                                   product, root_input, type(e).__name__, e)
        if df.height == 0:
            return {"product": product, "lot_col": lot_col, "wf_col": wf_col,
                    "headers": [], "rows": [], "prefixes": _load_prefixes(), "msg": "No data"}
        if not root_lot_id.strip() and lot_col and lot_col in df.columns:
            roots = []
            for v in df[lot_col].cast(_STR, strict=False).to_list():
                s = str(v or "").strip()
                if s and s not in ("None", "null") and s not in roots:
                    roots.append(s)
            if roots:
                root_lot_id = sorted(roots)[0]

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
            # Sort: (fab_lot_id 그룹, wafer_id 숫자-aware) — fab_lot 미정이면 "~" 로 후순위.
            # v8.8.3: wafer_id 가 문자열일 때 "10" < "2" 오작동 → 숫자 가능하면 int 로 cast 해서 secondary 키.
            wf_uniq = [w for w in dict.fromkeys(wf_raw) if w is not None and w != "None" and w != "null"]
            def _wf_sort_key(w):
                primary = wf2fab.get(w, "~")
                try:
                    n = int(w)
                    return (primary, 0, n)
                except (TypeError, ValueError):
                    s = str(w)
                    # 선행 'W' 제거 후 숫자 시도
                    if s.upper().startswith("W"):
                        try:
                            return (primary, 0, int(s[1:]))
                        except ValueError:
                            pass
                    return (primary, 1, s)
            wf_sorted = sorted(wf_uniq, key=_wf_sort_key)
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
        df_cols_set = set(df.columns)
        for col_name in selected:
            row_vals = [None] * len(wf_sorted)
            plan_vals = [None] * len(wf_sorted)
            # v8.8.16: CUSTOM 에 저장된 컬럼이 현재 df 에 없더라도 빈 행으로 표시.
            #   (e.g. plan 전용 가상 컬럼, 다른 제품에서 저장된 컬럼 등). plan 값은 여전히 lookup.
            if col_name in df_cols_set:
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
            else:
                # 가상 컬럼 — plan 값만 확인.
                for ci, wf_key in enumerate(wf_sorted):
                    ck = f"{root_lot_id}|{wf_key}|{col_name}"
                    pv = plans.get(ck, {}).get("value")
                    if pv is not None:
                        plan_vals[ci] = pv

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
            # v8.8.14: _display — rule_order + func_step 을 포함한 렌더용 이름.
            #   없으면 원본과 동일. FE 는 _display 를 사용하고 prefix strip 후 표시.
            rows.append({"_param": col_name, "_display": col_rename.get(col_name, col_name), "_cells": _cells})

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

        # v8.8.5: view 응답에 오버라이드 resolve 결과 동봉 — FE 상단 배지에 "어디서 읽어왔는지" 바로 표시.
        override_meta = _resolve_override_meta_light(product)
        # v9.0.5: FAB 후보는 DB FAB 원천의 정확한 root 매칭만 노출한다.
        #   DB FAB 에 없는 root 는 ML_TABLE LOT_ID / joined null fallback 을 쓰지 않는다.
        available_fab_lots = sorted(
            {str(v).strip() for v in wafer_fab_list if str(v or "").strip()},
            key=lambda s: s.upper(),
        )
        if not available_fab_lots:
            hist_lots = _fab_history_scope(product, root_lot_id=root_lot_id, limit=1000)
            if hist_lots.get("candidates"):
                available_fab_lots = hist_lots["candidates"]
        return {
            "product": product, "lot_col": lot_col, "wf_col": wf_col,
            "headers": headers, "rows": rows,
            "header_groups": header_groups, "wafer_fab_list": wafer_fab_list,
            "available_fab_lots": available_fab_lots,
            "prefixes": _load_prefixes(), "precision": load_json(PRECISION_CFG, DEFAULT_PRECISION), "root_lot_id": root_lot_id,
            "all_columns": all_data_cols, "selected_count": len(selected),
            "prefix": prefix or (custom_name if custom_name else ""),
            "history_mode": _history_mode,
            "plan_allowed_prefixes": PLAN_ALLOWED_PREFIXES,
            "mismatch_count": len(mismatches),
            "override": override_meta,
            "lot_warn": _lot_warn,
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
    auto_entries = []
    # v8.8.33: my_plan_changed 이벤트 대상자 수집.
    #   같은 cell 에 과거 plan 이 있었으면 그 plan 을 만든 user 에게 "내 plan 이 변경됨" 알림.
    original_owners: dict[str, str] = {}
    for ck in req.plans.keys():
        prev_user = (data["plans"].get(ck) or {}).get("user")
        if prev_user:
            original_owners[ck] = prev_user
    for ck, val in req.plans.items():
        old = data["plans"].get(ck, {}).get("value")
        data["plans"][ck] = {"value": val, "user": req.username, "updated": now}
        data["history"].append({
            "cell": ck, "old": old, "new": val, "user": req.username,
            "time": now, "action": "set", "root_lot_id": req.root_lot_id,
        })
        auto_entries.append((ck, old, val))
    data["history"] = data["history"][-1000:]
    save_json(pf, data)
    # v8.8.33: notify 이벤트 — 본인이 아닌 원 소유자에게만.
    try:
        from core.notify import emit_event
        for ck, old, val in auto_entries:
            if old == val:
                continue
            target = original_owners.get(ck)
            if not target or target == req.username:
                continue
            parts = (ck or "").split("|")
            emit_event(
                "my_plan_changed",
                actor=req.username,
                target_user=target,
                title="[plan 변경]",
                body=f"{req.username} 가 {req.product}/{parts[0] if parts else ''} plan 을 변경",
                payload={
                    "product": req.product,
                    "cell": ck,
                    "root_lot_id": req.root_lot_id or (parts[0] if parts else ""),
                    "wafer_id": parts[1] if len(parts) > 1 else "",
                    "column": parts[2] if len(parts) > 2 else "",
                    "old": old, "new": val,
                },
            )
    except Exception:
        pass
    # v8.7.0: 인폼 로그에 자동 기록 (plan 변경 별건으로 루트 인폼 생성).
    try:
        from routers.informs import auto_log_splittable_change
        fab_cache: dict[tuple[str, str], str] = {}
        for ck, old, val in auto_entries:
            if old != val:
                parts = str(ck or "").split("|")
                cache_key = (req.root_lot_id or (parts[0] if parts else ""), parts[1] if len(parts) > 1 else "")
                if cache_key not in fab_cache:
                    fab_cache[cache_key] = _resolve_fab_lot_for_cell(req.product, ck, req.root_lot_id)
                auto_log_splittable_change(
                    author=req.username, product=req.product,
                    lot_id=req.root_lot_id, cell_key=ck,
                    old_value=old, new_value=val, action="set",
                    fab_lot_id=fab_cache.get(cache_key, ""),
                )
    except Exception:
        pass
    _audit_user(req.username, "splittable:plan_save",
                detail=f"product={req.product} saved={len(req.plans)} rejected={len(rejected)}",
                tab="splittable")
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
    deleted = []
    for ck in req.cell_keys:
        if ck in data.get("plans", {}):
            old = data["plans"][ck].get("value")
            del data["plans"][ck]
            data.setdefault("history", []).append({
                "cell": ck, "old": old, "new": None,
                "user": req.username, "time": now, "action": "delete",
            })
            deleted.append((ck, old))
    save_json(pf, data)
    try:
        from routers.informs import auto_log_splittable_change
        for ck, old in deleted:
            parts = str(ck or "").split("|")
            root_lot = parts[0] if parts else ""
            auto_log_splittable_change(
                author=req.username, product=req.product, lot_id=root_lot,
                cell_key=ck, old_value=old, new_value=None, action="delete",
                fab_lot_id=_resolve_fab_lot_for_cell(req.product, ck, root_lot),
            )
    except Exception:
        pass
    _audit_user(req.username, "splittable:plan_delete",
                detail=f"product={req.product} deleted={len(deleted)}",
                tab="splittable")
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


@router.get("/operational-history")
def get_operational_history(request: Request, product: str = Query(...),
                            root_lot_id: str = Query(""), wafer_ids: str = Query("")):
    me = current_user(request)
    items = _load_operational_history(
        product=product,
        root_lot_id=root_lot_id,
        wafer_ids=wafer_ids,
        username=me.get("username", ""),
        role=me.get("role", "user"),
    )
    return {"items": items, "total": len(items)}


@router.get("/history/final")
def get_history_final(request: Request, product: str = Query(...), root_lot_id: str = Query(""),
                      include_deleted: bool = Query(False)):
    # v8.8.33 보안: 세션 토큰 필수 (plan history 내 username 노출 방지).
    from core.auth import current_user
    _ = current_user(request)
    """v8.8.33: final-plan-only 뷰.
    각 cell 의 최종 상태(가장 최근 set 또는 delete)만 반환 + plan drift 경고.

    drift 판정:
      - 같은 cell 에 set 이 2회 이상이고 old != new 가 섞임 → drift_level="multi"
      - 서로 다른 user 가 set → drift_level="multi_user"
      - 둘 다 → "multi_user_multi_change"
    """
    pf = PLAN_DIR / f"{product}.json"
    if not pf.exists():
        return {"final": [], "drift": [], "total_cells": 0}
    data = load_json(pf, {})
    hist = data.get("history", [])
    if root_lot_id:
        hist = [h for h in hist
                if h.get("root_lot_id") == root_lot_id
                or h.get("cell", "").startswith(root_lot_id + "|")]
    # cell 별로 시간순 그룹핑
    per_cell: dict[str, list] = {}
    for h in hist:
        ck = h.get("cell")
        if not ck:
            continue
        per_cell.setdefault(ck, []).append(h)
    final_rows = []
    drift_rows = []
    for ck, entries in per_cell.items():
        entries.sort(key=lambda x: x.get("time", ""))
        last = entries[-1]
        action = last.get("action") or "set"
        if action == "delete" and not include_deleted:
            continue
        sets = [e for e in entries if (e.get("action") or "set") == "set"]
        distinct_values = list({e.get("new") for e in sets if e.get("new") is not None})
        distinct_users = list({e.get("user") for e in sets if e.get("user")})
        set_count = len(sets)
        delete_count = sum(1 for e in entries if e.get("action") == "delete")
        drift_flags = []
        if set_count >= 2 and len(distinct_values) >= 2:
            drift_flags.append("multi_change")
        if len(distinct_users) >= 2:
            drift_flags.append("multi_user")
        if delete_count >= 1 and set_count >= 1:
            drift_flags.append("reinstated")
        parts = (ck or "").split("|")
        lot = parts[0] if len(parts) > 0 else ""
        wf = parts[1] if len(parts) > 1 else ""
        col = parts[2] if len(parts) > 2 else ""
        row = {
            "cell": ck,
            "root_lot_id": lot,
            "wafer_id": wf,
            "column": col,
            "final_value": last.get("new"),
            "final_action": action,
            "final_user": last.get("user"),
            "final_time": last.get("time"),
            "set_count": set_count,
            "delete_count": delete_count,
            "distinct_values": distinct_values,
            "distinct_users": distinct_users,
            "drift": drift_flags,
        }
        final_rows.append(row)
        if drift_flags:
            drift_rows.append(row)
    # 최신 시각 순
    final_rows.sort(key=lambda r: r.get("final_time") or "", reverse=True)
    drift_rows.sort(key=lambda r: r.get("final_time") or "", reverse=True)
    return {
        "final": final_rows,
        "drift": drift_rows,
        "drift_count": len(drift_rows),
        "total_cells": len(final_rows),
    }


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
                 username: str = Query(""),
                 custom_cols: str = Query("")):
    fp = _product_path(product)
    lf = _scan_product(product, root_lot_id=root_lot_id, wafer_ids=wafer_ids)
    lot_col, wf_col = _detect_lot_wafer(lf)
    lf = _filter_lot_wafer(lf, lot_col, wf_col, root_lot_id, wafer_ids)
    df = lf.collect()

    all_data_cols = [c for c in df.columns if c != lot_col and c != wf_col]
    selected = _select_columns(all_data_cols, custom_name, prefix,
                               max_fallback=200, custom_cols=custom_cols)
    if not custom_name and not custom_cols:
        for raw_pref in [p.strip() for p in str(prefix or "").split(",") if p.strip()]:
            for virt in _virtual_columns_for_prefix(product, raw_pref):
                if virt not in selected:
                    selected.append(virt)
    if not custom_name and not custom_cols:
        for raw_pref in [p.strip() for p in str(prefix or "").split(",") if p.strip()]:
            for virt in _virtual_columns_for_prefix(product, raw_pref):
                if virt not in selected:
                    selected.append(virt)
    # v8.8.14: display rename (rule_order + func_step) + natural sort on display name.
    col_rename = _build_col_rename_map(selected, product)
    selected = sorted(selected, key=lambda c: _natural_param_key(col_rename.get(c, c)))

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
        # v8.8.3: fab_lot 그룹 → wafer_id 숫자-aware 정렬 (view 와 동일 로직).
        def _wf_sort_key2(w):
            primary = wf2fab.get(w, "~")
            try:
                return (primary, 0, int(w))
            except (TypeError, ValueError):
                s = str(w)
                if s.upper().startswith("W"):
                    try:
                        return (primary, 0, int(s[1:]))
                    except ValueError:
                        pass
                return (primary, 1, s)
        wf_sorted = sorted(wf_uniq, key=_wf_sort_key2)
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
            if col_name in df.columns:
                vals = df[col_name].to_list()
                for i, v in enumerate(vals):
                    wk = wf_vals[i] if i < len(wf_vals) else None
                    idx = wf_idx.get(wk)
                    if idx is not None:
                        sv = str(v) if v is not None and str(v) not in ("None", "null") else ""
                        ck = f"{root_lot_id}|{wk}|{col_name}"
                        pv = plans.get(ck, {}).get("value")
                        row_data[idx] = pv if pv and not sv else sv
            else:
                for idx, wk in enumerate(wf_sorted):
                    ck = f"{root_lot_id}|{wk}|{col_name}"
                    pv = plans.get(ck, {}).get("value")
                    row_data[idx] = "" if pv is None else str(pv)
            writer.writerow([col_rename.get(col_name, col_name)] + row_data)
        # v8.4.4: Excel 한글 깨짐 방지 — UTF-8 BOM prefix
        csv_bytes = b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")
    else:
        csv_bytes = b"\xef\xbb\xbf" + df.write_csv().encode("utf-8")

    return csv_response(csv_bytes, f"{product}_{root_lot_id or 'all'}.csv")


@router.get("/download-xlsx")
def download_xlsx(product: str = Query(...), root_lot_id: str = Query(""),
                  wafer_ids: str = Query(""), prefix: str = Query("KNOB"),
                  custom_name: str = Query(""), username: str = Query(""),
                  custom_cols: str = Query("")):
    """v8.4.4 — XLSX 내보내기. fab_lot_id 행이 동일 값 구간별로 셀 병합되어
    UI 의 그룹 헤더와 동일하게 표시.
    v8.8.33: custom_cols 추가 — save 없이 체크만 한 ad-hoc 컬럼.
    """
    openpyxl_error = None
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
        from openpyxl.utils import get_column_letter
    except Exception as e:
        openpyxl_error = e

    lf = _scan_product(product, root_lot_id=root_lot_id, wafer_ids=wafer_ids)
    lot_col, wf_col = _detect_lot_wafer(lf, product)
    lf = _filter_lot_wafer(lf, lot_col, wf_col, root_lot_id, wafer_ids)
    df = lf.collect()

    all_data_cols = [c for c in df.columns if c != lot_col and c != wf_col]
    selected = _select_columns(all_data_cols, custom_name, prefix,
                               max_fallback=200, custom_cols=custom_cols)
    # v8.4.4: natural sort — prefix 뒤 숫자 (정수+소수) 기준. 숫자 없으면 알파벳 순.
    # v8.8.14: display rename (rule_order + func_step) 적용 + 그 이름 기준 정렬.
    col_rename = _build_col_rename_map(selected, product)
    selected = sorted(selected, key=lambda c: _natural_param_key(col_rename.get(c, c)))

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

    if openpyxl_error is not None:
        try:
            from core.simple_xlsx import build_workbook
            from fastapi.responses import StreamingResponse
        except Exception as e:
            import sys
            raise HTTPException(
                500,
                f"XLSX export unavailable at {sys.executable}: openpyxl={openpyxl_error}; fallback={e}",
            )

        download_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        n_wafers = len(wf_sorted)
        last_col = 1 + n_wafers
        rows = [
            ["downloaded_at", download_ts],
            ["username", username or ""],
            ["root_lot_id", root_lot_id or "", *["" for _ in range(max(0, n_wafers - 1))]],
        ]
        merges = []
        if n_wafers > 1:
            merges.append((3, 2, 3, last_col))

        has_fab_row = bool(fab_col and wf_sorted)
        if has_fab_row:
            fab_row = ["fab_lot_id", *["" for _ in wf_sorted]]
            cur = None
            start = 0
            for i, w in enumerate(wf_sorted):
                f = wf2fab.get(w, "")
                if f != cur:
                    if cur is not None and i - start > 0:
                        fab_row[1 + start] = cur
                        if i - start > 1:
                            merges.append((4, 2 + start, 4, 2 + i - 1))
                    cur = f
                    start = i
            if cur is not None and len(wf_sorted) - start > 0:
                fab_row[1 + start] = cur
                if len(wf_sorted) - start > 1:
                    merges.append((4, 2 + start, 4, 2 + len(wf_sorted) - 1))
            rows.append(fab_row)

        rows.append(["Parameter", *[f"#{w}" for w in wf_sorted]])
        for col_name in selected:
            display_name = col_rename.get(col_name, col_name)
            vals = df[col_name].to_list() if col_name in df.columns else []
            actual_by_idx = {}
            plan_by_idx = {}
            for i, v in enumerate(vals):
                wk = wf_vals[i] if i < len(wf_vals) else None
                idx = wf_idx.get(wk)
                if idx is None:
                    continue
                sv = str(v) if v is not None and str(v) not in ("None", "null") else ""
                ck = f"{root_lot_id}|{wk}|{col_name}"
                pv = plans.get(ck, {}).get("value")
                if sv:
                    actual_by_idx[idx] = sv
                if pv:
                    plan_by_idx[idx] = str(pv)
            if col_name not in df.columns:
                for idx, wk in enumerate(wf_sorted):
                    ck = f"{root_lot_id}|{wk}|{col_name}"
                    pv = plans.get(ck, {}).get("value")
                    if pv:
                        plan_by_idx[idx] = str(pv)
            out = [display_name, *["" for _ in wf_sorted]]
            for idx in sorted(set(list(actual_by_idx.keys()) + list(plan_by_idx.keys()))):
                sv = actual_by_idx.get(idx, "")
                pv = plan_by_idx.get(idx, "")
                if sv and pv and sv != pv:
                    out[1 + idx] = f"{sv} != {pv}"
                elif pv and not sv:
                    out[1 + idx] = f"PLAN: {pv}"
                else:
                    out[1 + idx] = sv or pv
            rows.append(out)

        data = build_workbook([{"title": product[:31], "rows": rows, "merges": merges}])
        fname = f"{product}_{root_lot_id or 'all'}.xlsx"
        return StreamingResponse(
            iter([data]),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )

    wb = Workbook()
    ws = wb.active
    ws.title = product[:31]
    hdr_fill = PatternFill("solid", fgColor="1f2937")
    fab_fill = PatternFill("solid", fgColor="374151")
    param_fill = PatternFill("solid", fgColor="374151")
    white = Font(color="FFFFFF", bold=True)
    # fab_lot_id 헤더는 어두운 배경 + 흰 글자로 고정해 노란색 대비 문제를 피한다.
    fab_font = Font(color="FFFFFF", bold=True, name="Consolas", size=12)
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
        # v8.8.14: display rename 된 이름을 표기 (원본 col_name 으로는 여전히 df 조회).
        display_name = col_rename.get(col_name, col_name)
        ws.cell(row=rr, column=1, value=display_name).font = Font(bold=True)
        up = (col_name or "").upper()
        should_color = any(up.startswith(p) for p in COLOR_PREFIXES)
        vals = df[col_name].to_list() if col_name in df.columns else []
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
        if col_name not in df.columns:
            for idx, wk in enumerate(wf_sorted):
                ck = f"{root_lot_id}|{wk}|{col_name}"
                pv = plans.get(ck, {}).get("value")
                if pv:
                    plan_by_idx[idx] = str(pv)
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

    # v8.8.13: 전체 그리드 테두리 보강 — 값 없는 빈 셀·헤더 셀까지 기본 border 적용.
    # plan_border / mismatch_border 처럼 특수 스타일이 이미 들어간 셀은 건너뜀.
    last_row = param_row + len(selected)
    for row_cells in ws.iter_rows(min_row=1, max_row=last_row, min_col=1, max_col=last_col):
        for c in row_cells:
            b = c.border
            if not (b and b.left and b.left.style):
                c.border = border

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
