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
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from typing import List
import polars as pl
from core.paths import PATHS
from core.audit import record_user as _audit_user
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
# v8.8.6: paste 세트 공유 저장소 — LocalStorage 대신 BE 에 올려 팀 공용 풀 + CUSTOM 탭 연동.
PASTE_SETS_FILE = PLAN_DIR / "paste_sets.json"
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
    v8.8.14: 내부 문자열 tail 도 자연 정렬(숫자/비숫자 분리)로 안정화 →
      `KNOB_10_FOO` 뒤에 `KNOB_2_FOO` 가 오는 오작동 방지.
    """
    if not name: return ("", float("inf"), ())
    parts = name.split("_", 1)
    pfx = parts[0]
    rest = parts[1] if len(parts) > 1 else ""
    # split rest into natural tokens (numbers → float, others → lowercased str)
    tail: list = []
    for tok in _NUM_RE.split(rest):
        if tok == "":
            continue
        try:
            tail.append(("n", float(tok)))
        except Exception:
            tail.append(("s", tok.lower()))
    m = _NUM_RE.search(rest)
    if m:
        try:
            num = float(m.group(1))
        except Exception:
            num = float("inf")
        return (pfx, num, tuple(tail))
    return (pfx, float("inf"), tuple(tail))


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


def _build_col_rename_map(selected_cols: list, product: str) -> dict:
    """raw column name → display name. 매칭 없으면 원본 반환(맵에 키 없음)."""
    out: dict[str, str] = {}
    try:
        knob_meta = _build_knob_meta(product)
    except Exception:
        knob_meta = {}
    try:
        inline_meta = _build_inline_meta(product)
    except Exception:
        inline_meta = {}
    try:
        vm_meta = _build_vm_meta(product)
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
            meta = knob_meta.get(col) or knob_meta.get(tail)
            if not meta:
                continue
            groups = meta.get("groups") or []
            if not groups:
                continue
            try:
                rule_order = min(int(g.get("rule_order") or 0) for g in groups)
            except Exception:
                rule_order = 0
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
            out[col] = f"KNOB_{float(rule_order):.1f}_{step_label}_{tail}"
        elif pfx_u == "INLINE":
            meta = inline_meta.get(col) or inline_meta.get(tail)
            if not meta:
                continue
            sid = _safe_step_segment(meta.get("step_id") or "")
            if not sid:
                continue
            out[col] = f"INLINE_{sid}_{tail}"
        elif pfx_u == "VM":
            meta = vm_meta.get(col) or vm_meta.get(tail)
            if not meta:
                continue
            sid = _safe_step_segment(meta.get("step_id") or "")
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
    """Multi-prefix ("KNOB,MASK") or ALL or custom-name based column selection.

    v8.8.16: CUSTOM 모드는 사용자가 저장한 columns 를 **그대로** 반환한다.
      - 기존: `all_data_cols` 에 없으면 걸러내어 → 값이 null 인 컬럼이 LOT 뷰에서 사라지는 문제.
      - 변경: custom 에 저장된 column 명을 있는 그대로 반환. view_split 이 null row 를
              자연스럽게 생성 (컬럼이 실제 df 에 없으면 모든 셀이 None, 컬럼명은 유지).
      - 빈 리스트면 기존 폴백 (상위 max_fallback) 유지.
    """
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
            for f in sorted(base.iterdir()):
                if not (f.is_file() and f.suffix == ".parquet"):
                    continue
                if not f.stem.startswith("ML_TABLE_"):
                    continue
                products.append({"name": f.stem, "file": f.name, "size": f.stat().st_size,
                                 "root": "Base", "type": "parquet", "source_type": "base_file"})
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
_LEGACY_SHORT_ROOTS = {"FAB", "INLINE", "ET", "EDS"}

def _is_db_root_dir(p) -> bool:
    if not p.is_dir():
        return False
    n = p.name
    if n == _RAWDATA_EXACT:
        return True
    if n.upper() in _LEGACY_SHORT_ROOTS:
        return True
    return False

def _list_db_roots():
    """사내/레거시 공통 DB 상위 폴더 후보 스캔. 반환 순서 = 우선순위.
    - FAB 힌트(이름에 'FAB' 포함) 이 먼저, 이후 INLINE/ET/EDS, 그 외.
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
    # Case 1: db_base itself is a 1.RAWDATA_DB* folder.
    if _is_db_root_dir(db_base):
        return [db_base]
    # Case 2: children match
    cands = [p for p in db_base.iterdir() if _is_db_root_dir(p)]
    if cands:
        def _rank(p):
            up = p.name.upper()
            if "FAB" in up: return 0
            if "INLINE" in up: return 1
            if "ET" in up: return 2
            if "EDS" in up: return 3
            return 4
        cands.sort(key=lambda p: (_rank(p), p.name))
        return cands
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
            for f in sub.rglob("*.parquet"):
                found = True
                break
            if found:
                has_product = True
                break
        if has_product:
            return [db_base]
    except Exception:
        pass
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
                for f in prod_dir.rglob("*"):
                    if f.is_file() and f.suffix in (".parquet", ".csv"):
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
def ml_table_match(product: str = Query(...)):
    """v8.7.8/v8.8.5: ML_TABLE_<PROD> 에서 PROD 추출 → `1.RAWDATA_DB*` / 레거시 짧은 이름 상위폴더 내 <PROD>/ 매칭.
    Ex) product=ML_TABLE_PRODA → {"matches": [{"root":"1.RAWDATA_DB_FAB","product":"PRODA","path":"1.RAWDATA_DB_FAB/PRODA"}, ...]}
    v8.8.3: 자동으로 선택된 fab_source (_auto_derive_fab_source) 와 현재 override 상태도 같이 반환.
    """
    pro = ""
    p = (product or "").strip()
    if p.startswith("ML_TABLE_"):
        pro = p[len("ML_TABLE_"):].strip()
    elif "_" in p:
        pro = p.rsplit("_", 1)[-1]
    else:
        pro = p
    matches = []
    if pro:
        for root_dir in _list_db_roots():
            sub = root_dir / pro
            if sub.is_dir():
                matches.append({
                    "root": root_dir.name,
                    "product": pro,
                    "path": f"{root_dir.name}/{pro}",
                })
    auto_path = _auto_derive_fab_source(p)
    manual_ov = {}
    try:
        cfg = load_json(SOURCE_CFG, {}) or {}
        manual_ov = (cfg.get("lot_overrides") or {}).get(p) or {}
    except Exception:
        pass
    effective = (manual_ov.get("fab_source") or "").strip() or auto_path
    # v8.8.5: 현재 적용 중인 오버라이드 resolve 세부정보. FE 에서 "어디서 읽어옴?" 에 바로 답변 가능.
    override_meta = _resolve_override_meta(p)
    return {
        "product": p,
        "derived_product": pro,
        "matches": matches,
        "auto_path": auto_path,
        "manual_override": bool(manual_ov.get("fab_source")),
        "effective_fab_source": effective,
        "override": override_meta,
    }


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
    # v8.8.10: 역할→컬럼명 매핑 soft-landing. 사내 CSV 의 컬럼 이름이 달라도 schema 만 바꾸면 됨.
    sm = _sch("step_matching")
    km = _sch("knob_ppid")

    # func_step → [step_id, ...] (ordered, dedup)
    step_map: dict[str, list[str]] = {}
    for r in matching:
        # product 컬럼이 있으면 필터, 없으면 공용 매핑으로 취급
        p_col = sm.get("product_col", "product")
        if product and r.get(p_col) and r.get(p_col) != product:
            continue
        fs = (r.get(sm.get("func_step_col", "func_step")) or "").strip()
        sid = (r.get(sm.get("step_id_col", "step_id")) or r.get("raw_step_id") or "").strip()
        if not fs or not sid:
            continue
        lst = step_map.setdefault(fs, [])
        if sid not in lst:
            lst.append(sid)

    # feature_name → groups (sorted by rule_order)
    feats: dict[str, list[dict]] = {}
    for r in knob_rules:
        use_val = (r.get(km.get("use_col", "use")) or "Y").strip().upper()
        if use_val == "N":
            continue
        p_col = km.get("product_col", "product")
        if product and r.get(p_col) and r.get(p_col) != product:
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


# v8.7.5/v8.8.10: INLINE / VM_ prefix 매칭 메타 — schema 매핑 기반.
def _build_inline_meta(product: str = "") -> dict:
    """inline_matching.csv (schema: item_id_col/step_id_col/item_desc_col/product_col)."""
    base = _base_root()
    rows = _load_csv_rows(base / "inline_matching.csv")
    im = _sch("inline_matching")
    out: dict[str, dict] = {}
    for r in rows:
        p_col = im.get("product_col", "product")
        if product and r.get(p_col) and r.get(p_col) != product:
            continue
        iid = (r.get(im.get("item_id_col", "item_id")) or "").strip()
        sid = (r.get(im.get("step_id_col", "step_id")) or "").strip()
        desc = (r.get(im.get("item_desc_col", "item_desc")) or "").strip()
        if not iid:
            continue
        out[iid] = {"step_id": sid, "item_id": iid, "item_desc": desc,
                    "label": desc or iid, "sub": f"{sid}/{iid}" if sid else iid}
    return out


def _build_vm_meta(product: str = "") -> dict:
    """v8.8.7/v8.8.10: vm_matching.csv 컬럼 매핑 schema 기반."""
    base = _base_root()
    rows = _load_csv_rows(base / "vm_matching.csv")
    vm = _sch("vm_matching")
    out: dict[str, dict] = {}
    for r in rows:
        p_col = vm.get("product_col", "product")
        if product and r.get(p_col) and r.get(p_col) != product:
            continue
        fname = (r.get(vm.get("feature_col", "feature_name")) or r.get(vm.get("step_desc_col", "step_desc")) or "").strip()
        sd = (r.get(vm.get("step_desc_col", "step_desc")) or "").strip()
        sid = (r.get(vm.get("step_id_col", "step_id")) or "").strip()
        if not fname:
            continue
        out[fname] = {"step_desc": sd, "step_id": sid, "label": sd or fname, "sub": sid}
    return out


@router.get("/inline-meta")
def inline_meta(product: str = Query("")):
    """v8.7.5/v8.8.15: INLINE prefix 항목 매칭 메타. product 필터 추가."""
    return {"items": _build_inline_meta(product)}


@router.get("/vm-meta")
def vm_meta(product: str = Query("")):
    """v8.7.5/v8.8.7: VM_ prefix 항목 매칭 메타. product 필터 추가."""
    return {"items": _build_vm_meta(product)}


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
    },
    "inline_matching": {
        "step_id_col":   "step_id",
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
        "cols": ["feature_name", "function_step", "rule_order", "ppid",
                 "operator", "category", "use", "product"],
        "required": ["feature_name", "function_step", "product"],
    },
    "step_matching": {
        "filename": "step_matching.csv",
        "cols": ["step_id", "func_step", "product"],
        "required": ["step_id", "func_step", "product"],
    },
    # v8.8.9: INLINE / VM 매칭도 동일 CRUD 로 관리.
    #   inline_matching.csv: (step_id, item_id, item_desc) — INLINE_<item_id> 가 해당 step 에서 측정됨.
    "inline_matching": {
        "filename": "inline_matching.csv",
        "cols": ["step_id", "item_id", "item_desc", "product"],
        "required": ["step_id", "item_id", "product"],
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
    return {"ok": True, "kind": req.kind, "product": req.product, "saved_rows": len(final)}


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


def _scan_fab_source(fab_source: str):
    """v8.8.0: fab_source 가 가리키는 DB 경로를 LazyFrame 으로 스캔.
    - "FAB/PRODA" 같은 디렉토리면 그 아래 모든 *.parquet 을 union 으로 스캔 (hive flat 호환).
    - 단일 .parquet/.csv 파일이면 그 파일을 스캔.
    - "root:FAB" (상위폴더만 지정) 인 경우 FAB 아래 모든 제품 폴더를 합집합 스캔.
    실패 시 None 반환 (조용히 폴백).
    """
    if not fab_source:
        return None
    db_base = _db_base()
    base_root = _base_root()
    fp = None
    # "root:FAB" 형태 — 상위폴더 아래 전체 parquet 합집합 (신규 매칭에서 쓰임).
    if fab_source.startswith("root:"):
        rn = fab_source[len("root:"):].strip()
        if rn and db_base.exists():
            cand = db_base / rn
            if cand.is_dir():
                fp = cand
    if fp is None:
        for root in (db_base, base_root):
            if not root or not root.exists():
                continue
            cand = root / fab_source
            if cand.exists():
                fp = cand
                break
            for ext in (".parquet", ".csv"):
                cand2 = root / f"{fab_source}{ext}"
                if cand2.exists():
                    fp = cand2
                    break
            if fp:
                break
    if not fp:
        return None
    try:
        if fp.is_dir():
            parquets = sorted(fp.rglob("*.parquet"))
            if not parquets:
                return None
            # v8.8.5: 사내 `PRODA/date=YYYYMMDD/part_*.parquet` hive 레이아웃 대응.
            # hive_partitioning 을 켜서 경로의 `date=...` 를 컬럼으로 노출 → ts_col 자동 추론 시
            # `date` 후보가 적중해 "가장 최신 date 의 fab_col" join 이 자동으로 동작.
            try:
                return _cast_cats_lazy(pl.scan_parquet([str(p) for p in parquets],
                                                        hive_partitioning=True))
            except TypeError:
                # polars 구버전 — 파라미터 미지원 시 폴백 (경로 기반 파티션 컬럼 없음).
                return _cast_cats_lazy(pl.scan_parquet([str(p) for p in parquets]))
        if fp.suffix == ".csv":
            return _cast_cats_lazy(pl.scan_csv(str(fp), infer_schema_length=5000))
        return _cast_cats_lazy(pl.scan_parquet(str(fp)))
    except Exception:
        return None


# v8.8.3/v8.8.5: ML_TABLE_<PROD> → DB 상위폴더 자동 매칭.
#   `_list_db_roots()` 에 위임 — 사내 `1.RAWDATA_DB*` 접두 폴더도 인식 (FAB 힌트 우선).
# v8.8.17: root_dir 이 db_base 자체일 때(Case 1/3) 는 제품명만 반환 —
#   `_scan_fab_source` 에서 `db_base / fab_source` 로 해석하므로 prefix 중복 방지.
def _auto_derive_fab_source(product: str) -> str:
    """Return a fab_source path like "1.RAWDATA_DB_FAB/PRODA" (or legacy "FAB/PRODA") if auto-matchable, else "".
    ML_TABLE_ prefix 가 아니면 "" 반환 (오버라이드 off)."""
    p = (product or "").strip()
    if not p.startswith("ML_TABLE_"):
        return ""
    pro = p[len("ML_TABLE_"):].strip()
    if not pro:
        return ""
    db_base = _db_base()
    for root_dir in _list_db_roots():
        cand = root_dir / pro
        if cand.is_dir():
            # If root_dir is db_base itself (Case 1/3), just the product name —
            # else scan_fab_source would try db_base/db_base.name/pro (wrong).
            try:
                if root_dir.resolve() == db_base.resolve():
                    return pro
            except Exception:
                pass
            return f"{root_dir.name}/{pro}"
    return ""


# v8.8.3/v8.8.5: ts_col / fab_col 자동 추론 — 매뉴얼 override 없을 때 흔한 이름 스캔.
#   `date` 를 맨 앞에 추가: hive 파티션 키(`date=YYYYMMDD`) 가 scan_parquet 의 hive_partitioning 덕에
#   컬럼으로 노출되는 경우를 최우선 취급 — 파일 안 ts 컬럼이 없어도 파티션 단위로 최신 판정 가능.
_TS_COL_CANDIDATES = ("date", "out_ts", "ts", "timestamp", "created_at", "log_ts", "event_ts", "update_ts")
_FAB_COL_CANDIDATES = ("fab_lot_id", "lot_id", "fab_lotid", "fab_lot")
# v8.8.16: hive 원천에서 끌어와 ML_TABLE 값을 덮어쓸 기본 컬럼 집합.
#   사내 `1.RAWDATA_DB*/<PROD>/date=*/*.parquet` 레이아웃에서 이 이름이 있으면 소스값으로 교체.
#   fab_col(보통 fab_lot_id) 는 레거시 단일 필드와 병합되어 override_cols 에 합류.
_DEFAULT_OVERRIDE_COLS = ("root_lot_id", "wafer_id", "lot_id", "tkout_time")


def _resolve_override_meta(product: str) -> dict:
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
        # v8.8.16: hive 원천에서 끌어오기로 한 override 컬럼 목록 + 실제 스키마에 존재하는 것만.
        "override_cols": [], "override_cols_present": [], "override_cols_missing": [],
    }
    try:
        cfg = load_json(SOURCE_CFG, {}) if SOURCE_CFG.exists() else {}
        ov = (cfg.get("lot_overrides") or {}).get(product) or {}
        manual = (ov.get("fab_source") or "").strip()
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
            if product.startswith("ML_TABLE_"):
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

        # locate fab_source folder/file to list scanned files.
        fp = None
        tried = []
        for root in (db_base, base_root):
            if not root or not root.exists():
                tried.append(f"{root} (not exist)" if root else "(None)")
                continue
            cand = root / fab_source
            tried.append(str(cand))
            if cand.exists():
                fp = cand
                break
        if fp is None:
            meta["tried_candidates"] = tried
            meta["error"] = (
                f"fab_source 경로를 찾을 수 없음: '{fab_source}'. "
                f"탐색 경로: {tried}. db_root='{db_base}' base_root='{base_root}'."
            )
            return meta
        if fp.is_dir():
            parquets = sorted(fp.rglob("*.parquet"))
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

        fab_lf = _scan_fab_source(fab_source)
        if fab_lf is None:
            meta["error"] = f"스캔 실패 (parquet 없음 또는 읽기 불가): {fab_source}"
            return meta
        fab_names = fab_lf.collect_schema().names()

        # join keys
        join_keys = ov.get("join_keys") or []
        if isinstance(join_keys, str):
            join_keys = [k.strip() for k in join_keys.split(",") if k.strip()]
        if not join_keys:
            try:
                main_names = pl.scan_parquet(str(_product_path(product))).collect_schema().names()
            except Exception:
                main_names = []
            for cand in ("root_lot_id", "wafer_id", "lot_id", "product"):
                if cand in main_names and cand in fab_names:
                    join_keys.append(cand)
        join_keys = [k for k in join_keys if k in fab_names]
        meta["join_keys"] = join_keys

        # fab_col / ts_col 추론
        meta["fab_col"] = (ov.get("fab_col") or "").strip() or _pick_first_present(_FAB_COL_CANDIDATES, fab_names) or "fab_lot_id"
        meta["ts_col"] = (ov.get("ts_col") or "").strip() or _pick_first_present(_TS_COL_CANDIDATES, fab_names) or ""

        # v8.8.16: override_cols — 기본 (_DEFAULT_OVERRIDE_COLS) + manual ov.override_cols + 레거시 fab_col 병합.
        raw_oc = ov.get("override_cols")
        if isinstance(raw_oc, str):
            raw_oc = [c.strip() for c in raw_oc.split(",") if c.strip()]
        if not raw_oc:
            raw_oc = list(_DEFAULT_OVERRIDE_COLS)
        # 레거시 fab_col 도 합류 (중복 제거).
        if meta["fab_col"] and meta["fab_col"] not in raw_oc:
            raw_oc = list(raw_oc) + [meta["fab_col"]]
        meta["override_cols"] = list(raw_oc)
        meta["override_cols_present"] = [c for c in raw_oc if c in fab_names]
        meta["override_cols_missing"] = [c for c in raw_oc if c not in fab_names]

        if meta["fab_col"] not in fab_names:
            meta["error"] = f"fab_col '{meta['fab_col']}' 이 소스 스키마에 없음. 소스 컬럼: {fab_names[:20]}"
            return meta
        if not join_keys:
            meta["error"] = f"공통 join key 없음. 소스 컬럼: {fab_names[:20]}"
            return meta

        # row count + sample
        try:
            rc = fab_lf.select(pl.len()).collect()
            meta["row_count"] = int(rc.item()) if rc.height > 0 else 0
        except Exception as e:
            meta["row_count"] = -1
        try:
            sample_cols = [c for c in (join_keys + [meta["fab_col"]] + ([meta["ts_col"]] if meta["ts_col"] else [])) if c in fab_names]
            sample = fab_lf.select(sample_cols)
            if meta["ts_col"] and meta["ts_col"] in fab_names:
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

def _pick_first_present(candidates, available_names):
    av = set(available_names)
    for c in candidates:
        if c in av:
            return c
    return ""


def _scan_product(product: str):
    fp = _product_path(product)
    if fp.suffix == ".csv":
        lf = _cast_cats_lazy(pl.scan_csv(str(fp), infer_schema_length=5000))
    else:
        lf = _cast_cats_lazy(pl.scan_parquet(str(fp)))

    # v8.8.3: 오버라이드 로직 근본 재정리.
    #   1) 매뉴얼 config(lot_overrides[product].fab_source) 가 있으면 그 값을 사용.
    #   2) 없으면 ML_TABLE_<PROD> → DB/<root>/<PROD> 자동 매칭 시도.
    #   3) ts_col / fab_col 도 매뉴얼 > 자동 추론 순.
    #   4) 조인은 항상 "ts_col 기준 최신 레코드만" join keys 별로 picking 후 left-join.
    try:
        cfg = load_json(SOURCE_CFG, {}) if SOURCE_CFG.exists() else {}
        ov = (cfg.get("lot_overrides") or {}).get(product) or {}
        fab_source = (ov.get("fab_source") or "").strip()
        auto_matched = False
        if not fab_source:
            fab_source = _auto_derive_fab_source(product)
            auto_matched = bool(fab_source)
        if not fab_source:
            return lf
        fab_lf = _scan_fab_source(fab_source)
        if fab_lf is None:
            return lf
        main_names = set(lf.collect_schema().names())
        fab_schema_names = fab_lf.collect_schema().names()
        fab_names = set(fab_schema_names)
        join_keys = ov.get("join_keys") or []
        if isinstance(join_keys, str):
            join_keys = [k.strip() for k in join_keys.split(",") if k.strip()]
        if not join_keys:
            # fallback: pick natural common keys (root_lot_id / wafer_id / product)
            for cand in ("root_lot_id", "wafer_id", "lot_id", "product"):
                if cand in main_names and cand in fab_names:
                    join_keys.append(cand)
        join_keys = [k for k in join_keys if k in main_names and k in fab_names]
        if not join_keys:
            return lf
        # fab_col / ts_col — 매뉴얼 > 자동 추론 순.
        fab_col = (ov.get("fab_col") or "").strip() or _pick_first_present(_FAB_COL_CANDIDATES, fab_schema_names)
        if not fab_col:
            fab_col = "fab_lot_id"  # 최후 기본값
        ts_col = (ov.get("ts_col") or "").strip() or _pick_first_present(_TS_COL_CANDIDATES, fab_schema_names)
        # v8.8.16: 다수 컬럼 오버라이드 지원 — 단일 fab_col 만 끌어오던 것을 확장.
        #   1.RAWDATA_DB hive 원천에서 root_lot_id/wafer_id/lot_id/tkout_time 등도 최신값으로 교체.
        #   manual ov.override_cols (list 또는 "a,b,c") > _DEFAULT_OVERRIDE_COLS.
        raw_oc = ov.get("override_cols")
        if isinstance(raw_oc, str):
            raw_oc = [c.strip() for c in raw_oc.split(",") if c.strip()]
        if not raw_oc:
            raw_oc = list(_DEFAULT_OVERRIDE_COLS)
        # 레거시 fab_col 도 항상 포함 (중복 제거).
        if fab_col and fab_col not in raw_oc:
            raw_oc = list(raw_oc) + [fab_col]
        # 실제 소스 스키마에 있는 것만 유효. join_key 는 매칭용이라 override 에서 제외 (값 유지).
        override_cols = [c for c in dict.fromkeys(raw_oc)
                         if c in fab_names and c not in join_keys]
        # Bring: join keys + override_cols + ts_col (optional). Avoid wide explosion.
        wanted = list(dict.fromkeys(join_keys + override_cols + ([ts_col] if ts_col else [])))
        wanted = [c for c in wanted if c in fab_names]
        # 쓸모 있는 컬럼이 하나도 없으면 (join_key 만으로는 override 무의미) 조용히 폴백.
        if not override_cols:
            return lf
        fab_proj = fab_lf.select(wanted)
        # Cast join keys to Utf8 on both sides for safety.
        fab_proj = fab_proj.with_columns([pl.col(k).cast(_STR, strict=False) for k in join_keys])
        lf = lf.with_columns([pl.col(k).cast(_STR, strict=False) for k in join_keys])
        # ── 핵심: ts_col 기준 최신 레코드만 join keys 별로 선택.
        #   - ts_col 이 존재하면 desc 정렬 후 첫 행 유지.
        #   - ts_col 이 없으면 keep="last" 유지 (레거시 호환).
        if ts_col and ts_col in fab_names:
            fab_proj = fab_proj.sort(ts_col, descending=True, nulls_last=True)
            fab_proj = fab_proj.unique(subset=join_keys, keep="first", maintain_order=True)
        else:
            fab_proj = fab_proj.unique(subset=join_keys, keep="last")
        # main 에서 override 컬럼을 미리 drop 해야 left-join 결과가 소스 값으로 교체된다.
        to_drop = [c for c in override_cols if c in main_names]
        if to_drop:
            lf = lf.drop(to_drop)
        lf = lf.join(fab_proj, on=join_keys, how="left")
        return lf
    except Exception:
        return lf

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
    source: str = Query("auto"),   # v8.8.19: auto|override|mltable
):
    """Autocomplete 후보 반환. col 은 'root_lot_id' 또는 'fab_lot_id'. prefix 가
    비어있으면 최신/정렬 상위 N개, 아니면 prefix 포함 매칭을 정렬 순 top N.

    v8.8.19: `source` 인자 추가.
      - "override": ML_TABLE_<PROD> 가 아닌 오버라이드 hive fab_source (`1.RAWDATA_DB/<PROD>/`)
          에서 직접 lot 후보를 뽑는다. 최신 date partition 의 실제 유통 lot 을 그대로 보여주어
          인폼로그에서 "지금 DB 에 찍혀있는 그 lot" 을 고르기 쉽게 한다.
      - "mltable": 기존 동작 (ML_TABLE_<PROD>.parquet 스캔).
      - "auto" (기본): ML_TABLE_ 제품이면 override 가 유효할 때 override → 실패 시 mltable.
    """
    use_override = False
    lf = None
    if source in ("override", "auto") and product.startswith("ML_TABLE_"):
        try:
            meta = _resolve_override_meta(product)
            fab_source = (meta.get("fab_source") or "").strip()
            if fab_source and not meta.get("error"):
                fab_lf = _scan_fab_source(fab_source)
                if fab_lf is not None:
                    lf = fab_lf
                    use_override = True
        except Exception:
            lf = None
    if lf is None:
        if source == "override":
            return {"col": col, "candidates": [], "source": "override",
                    "note": "override 비활성 또는 fab_source 없음"}
        lf = _scan_product(product)

    schema_names = lf.collect_schema().names()
    # 스키마에 col 존재 여부 확인 (하이픈 변형/대소문자 차이 보정)
    if col not in schema_names:
        # fallback — root 이면 auto-detect lot col, fab 는 그대로
        if col == "root_lot_id":
            lot_col, _ = _detect_lot_wafer(lf)
            col = lot_col or col
        if col not in schema_names:
            return {"col": col, "candidates": [], "available_cols": schema_names[:20],
                    "source": "override" if use_override else "mltable"}

    q = lf.select(pl.col(col).cast(_STR, strict=False).alias("v")).unique()
    if prefix.strip():
        q = q.filter(pl.col("v").str.contains(prefix.strip(), literal=True))
    rows = q.sort("v").head(limit).collect()
    return {"col": col, "candidates": rows["v"].to_list(), "prefix": prefix,
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
        # v8.4.4/v8.8.3: fab_lot_col — 매뉴얼 override > 자동 추론 > "fab_lot_id".
        fab_lot_col = "fab_lot_id"
        try:
            schema_names = lf.collect_schema().names()
            _cfg = load_json(SOURCE_CFG, {}) or {}
            _ov = (_cfg.get("lot_overrides") or {}).get(product) or {}
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
        # v8.8.14: rule_order + func_step 를 컬럼명에 끼워 넣어 display-rename.
        #   원본 col 이름은 `_param` 으로 그대로 두고, 렌더용 `_display` 를 별도로 내려보냄.
        #   정렬은 display 이름 기준으로 → `KNOB_<rule_order>_<func_step>_<feat>` 가
        #   rule_order 숫자 기준 자연 정렬된다.
        col_rename = _build_col_rename_map(selected, product)
        # v8.4.4/v8.8.14: natural sort — rename 후 이름이 있으면 그걸 기준,
        #   없으면 원본 이름 기준. KNOB_12.0_... 포맷이므로 _natural_param_key 가 rule_order 를 잡아낸다.
        selected = sorted(selected, key=lambda c: _natural_param_key(col_rename.get(c, c)))

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
        override_meta = _resolve_override_meta(product)
        return {
            "product": product, "lot_col": lot_col, "wf_col": wf_col,
            "headers": headers, "rows": rows,
            "header_groups": header_groups, "wafer_fab_list": wafer_fab_list,
            "prefixes": _load_prefixes(), "precision": load_json(PRECISION_CFG, DEFAULT_PRECISION), "root_lot_id": root_lot_id,
            "all_columns": all_data_cols, "selected_count": len(selected),
            "prefix": prefix or (custom_name if custom_name else ""),
            "plan_allowed_prefixes": PLAN_ALLOWED_PREFIXES,
            "mismatch_count": len(mismatches),
            "override": override_meta,
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
    # v8.7.0: 인폼 로그에 자동 기록 (plan 변경 별건으로 루트 인폼 생성).
    try:
        from routers.informs import auto_log_splittable_change
        for ck, old, val in auto_entries:
            if old != val:
                auto_log_splittable_change(
                    author=req.username, product=req.product,
                    lot_id=req.root_lot_id, cell_key=ck,
                    old_value=old, new_value=val, action="set",
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
            auto_log_splittable_change(
                author=req.username, product=req.product, lot_id="",
                cell_key=ck, old_value=old, new_value=None, action="delete",
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
            vals = df[col_name].to_list()
            for i, v in enumerate(vals):
                wk = wf_vals[i] if i < len(wf_vals) else None
                idx = wf_idx.get(wk)
                if idx is not None:
                    sv = str(v) if v is not None and str(v) not in ("None", "null") else ""
                    ck = f"{root_lot_id}|{wk}|{col_name}"
                    pv = plans.get(ck, {}).get("value")
                    row_data[idx] = pv if pv and not sv else sv
            writer.writerow([col_rename.get(col_name, col_name)] + row_data)
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
        # v8.8.14: display rename 된 이름을 표기 (원본 col_name 으로는 여전히 df 조회).
        display_name = col_rename.get(col_name, col_name)
        ws.cell(row=rr, column=1, value=display_name).font = Font(bold=True)
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
