@router.get("/products")
def list_products():
    """Base/DB root 직하의 ML_TABLE_* 단일 파일만 노출. 다른 소스는 fab_source 자동 매칭 전용.
    Source 가시성(enabled) 토글은 여전히 이 리스트 기준."""
    products = []
    roots: list[tuple[str, str, Path]] = []
    for label, resolver in (("Base", _base_root), ("DB", _db_base)):
        try:
            root = resolver()
        except Exception:
            continue
        try:
            root_key = str(root.resolve())
        except Exception:
            root_key = str(root)
        if not root or any(existing_key == root_key for _, existing_key, _ in roots):
            continue
        roots.append((label, root_key, root))
    for label, _root_key, root in roots:
        try:
            if not root.exists():
                continue
            for f in sorted(root.iterdir(), key=lambda p: p.name.lower()):
                if not _is_mltable_product_file(f):
                    continue
                products.append({"name": _canonical_mltable_product_name(f.stem), "file": f.name, "size": f.stat().st_size,
                                 "root": label, "type": f.suffix.lower().lstrip("."), "source_type": "base_file"})
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
    product_order = _product_order.load_product_order()
    dedup = _product_order.order_products(dedup, name=lambda p: p.get("name"), product_order=product_order)
    return {"products": dedup, "product_order": product_order}


@router.get("/product-order")
def get_product_order():
    return {"product_order": _product_order.load_product_order()}


@router.post("/product-order")
def save_product_order(req: dict, _perm=Depends(require_page_manager("splittable"))):
    order = _product_order.save_product_order(req.get("product_order"))
    return {"ok": True, "product_order": order}


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
            found = _first_data_file_ci(sub, (".parquet",)) is not None
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
                # 리스트 화면은 "제품으로 볼 수 있는가"만 필요하다. 실데이터에서
                # 전체 rglob+sort 는 수만 파티션을 훑으므로 첫 파일만 확인한다.
                f = _first_data_file_ci(prod_dir, (".parquet", ".csv"))
                has_data = f is not None
                if has_data:
                    try: total_size += f.stat().st_size
                    except Exception: pass
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
        "match_cache": _match_cache_response_meta(p),
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
    root_lot_id = root_lot_id if isinstance(root_lot_id, str) else ""
    fab_lot_id = fab_lot_id if isinstance(fab_lot_id, str) else ""
    wafer_ids = wafer_ids if isinstance(wafer_ids, str) else ""
    cols = []
    candidate_cache = ""
    knob_columns: list[str] = []
    try:
        # 제품 전체 스키마/KNOB 목록은 lookup 빌드가 이미 저장해 둔다. 제품 선택
        # 때 원천 공유 parquet의 metadata조차 다시 열지 않고 작은 JSON 두 개로 끝낸다.
        if not (root_lot_id or fab_lot_id or wafer_ids):
            fp = _product_path(product)
            status = _ml_table_lookup.cache_status(fp)
            schema_map = (status.get("meta") or {}).get("schema") or {}
            index = _ml_table_lookup.read_candidate_index(fp)
            if (
                status.get("status") == "fresh"
                and isinstance(schema_map, dict) and schema_map
                and int(index.get("version") or 0) == _ml_table_lookup.CANDIDATE_INDEX_VERSION
            ):
                cols = [{"name": name, "dtype": str(dtype)} for name, dtype in schema_map.items()]
                knob_columns = list((index.get("columns_by_prefix") or {}).get("KNOB") or [])
                candidate_cache = "lookup_index"
    except Exception:
        cols = []
    try:
        if cols:
            pass
        elif root_lot_id or fab_lot_id or wafer_ids:
            lf = _scan_product(product, root_lot_id=root_lot_id,
                               fab_lot_id=fab_lot_id, wafer_ids=wafer_ids)
            schema = lf.collect_schema()
            cols = [{"name": n, "dtype": str(d)} for n, d in schema.items()]
        else:
            lf = _scan_product_base(product)
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
    existing_cols = {str(c.get("name") or "") for c in cols}
    for tag_col in _custom_tag_columns_for_product(product):
        column = tag_col.get("column")
        if column and column not in existing_cols:
            cols.append({"name": column, "dtype": "custom_tag", "label": tag_col.get("label") or column})
            existing_cols.add(column)
    for mgmt_col in _management_row_columns_for_product(product):
        column = mgmt_col.get("column")
        if column and column not in existing_cols:
            cols.append({"name": column, "dtype": "management_row", "label": mgmt_col.get("label") or column})
            existing_cols.add(column)
    # 오버라이드에서 실제로 join 된 컬럼 목록 (FE 가 검색 pool 에서 '숨김 해제' 할 기준).
    override_cols_present: list = []
    try:
        meta = _resolve_override_meta_light(product)
        if meta.get("enabled"):
            override_cols_present = list(meta.get("override_cols_present") or meta.get("override_cols") or [])
    except Exception:
        pass
    return {
        "columns": cols,
        "total": len(cols),
        "override_cols_present": override_cols_present,
        "knob_columns": knob_columns,
        "candidate_cache": candidate_cache,
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
        from core.utils import filter_valid_wafer_ids_df
        et = filter_valid_wafer_ids_df(pl.read_parquet(str(et_fp)))
        inl = filter_valid_wafer_ids_df(pl.read_parquet(str(inl_fp)))
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


def _long_pivot_source(source: str) -> str:
    src = str(source or "").strip().lower()
    if src not in {"fab", "inline", "et"}:
        raise HTTPException(400, "source must be fab|inline|et")
    return src


def _long_pivot_product(product: str) -> str:
    prod = str(product or "").strip()
    if prod.upper().startswith("ML_TABLE_"):
        prod = prod[len("ML_TABLE_"):]
    return prod


def _long_pivot_key(source: str, product: str) -> str:
    return f"{_long_pivot_source(source)}:{_long_pivot_product(product).upper()}"


def _long_pivot_cache_path(source: str, product: str) -> Path:
    name = f"{_long_pivot_source(source)}_{safe_id(_long_pivot_product(product) or 'product')}.parquet"
    return _LONG_PIVOT_CACHE_DIR / name


def _long_pivot_meta_path(source: str, product: str) -> Path:
    return _long_pivot_cache_path(source, product).with_suffix(".json")


def _long_pivot_source_dir(source: str, product: str) -> Path:
    src = _long_pivot_source(source)
    folder = {
        "fab": "1.RAWDATA_DB_FAB",
        "inline": "1.RAWDATA_DB_INLINE",
        "et": "1.RAWDATA_DB_ET",
    }[src]
    return _db_base() / folder / _long_pivot_product(product)


def _long_pivot_source_signature(source: str, product: str) -> dict:
    root = _long_pivot_source_dir(source, product)
    count = 0
    total_size = 0
    max_mtime = 0.0
    try:
        files = sorted(root.rglob("*.parquet")) if root.is_dir() else []
    except Exception:
        files = []
    for fp in files:
        try:
            st = fp.stat()
        except Exception:
            continue
        count += 1
        total_size += int(st.st_size)
        max_mtime = max(max_mtime, float(st.st_mtime))
    return {
        "root": str(root),
        "file_count": count,
        "total_size": total_size,
        "max_mtime": max_mtime,
    }


def _read_long_pivot_meta(source: str, product: str) -> dict:
    fp = _long_pivot_meta_path(source, product)
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_long_pivot_meta(source: str, product: str, meta: dict) -> None:
    fp = _long_pivot_meta_path(source, product)
    fp.parent.mkdir(parents=True, exist_ok=True)
    tmp = fp.with_suffix(fp.suffix + ".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(fp)


def _long_pivot_job_status(source: str, product: str) -> str:
    target = _long_pivot_key(source, product)
    with _LONG_PIVOT_JOB_LOCK:
        if _LONG_PIVOT_JOB_STATE.get("running") and _LONG_PIVOT_JOB_STATE.get("current") == target:
            return "running"
        queued = {_long_pivot_key(src, prod) for src, prod, _force in _LONG_PIVOT_QUEUE}
    return "queued" if target in queued else ""


def _long_pivot_cache_status(source: str, product: str) -> dict:
    src = _long_pivot_source(source)
    prod = _long_pivot_product(product)
    cache_fp = _long_pivot_cache_path(src, prod)
    meta = _read_long_pivot_meta(src, prod)
    source_sig = _long_pivot_source_signature(src, prod)
    has_cache = bool(cache_fp.is_file() and meta.get("version") == LONG_PIVOT_CACHE_VERSION)
    stale = bool(has_cache and meta.get("source_signature") != source_sig)
    status = "fresh" if has_cache and not stale else ("stale" if has_cache else "missing")
    job = _long_pivot_job_status(src, prod)
    if job and status != "fresh":
        status = job
    return {
        "ok": True,
        "source": src,
        "product": prod,
        "status": status,
        "has_cache": has_cache,
        "source_stale": stale,
        "source_exists": int(source_sig.get("file_count") or 0) > 0,
        "source_signature": source_sig,
        "cache_path": str(cache_fp),
        "meta_path": str(_long_pivot_meta_path(src, prod)),
        "job_status": job,
        "meta": meta,
    }


def _long_pivot_cache_public(status: dict | None, queued: dict | None = None) -> dict:
    status = status or {}
    queued = queued or {}
    queued_status = str(queued.get("status") or "").strip()
    queued_flag = bool(queued.get("queued") or queued_status in {"queued", "running"})
    meta = status.get("meta") or {}
    return {
        "status": queued_status if queued_flag else str(status.get("status") or ""),
        "hit": str(status.get("status") or "") == "fresh",
        "queued": queued_flag,
        "has_cache": bool(status.get("has_cache")),
        "source_stale": bool(status.get("source_stale")),
        "source_exists": bool(status.get("source_exists")),
        "row_count": int(meta.get("row_count") or 0),
        "built_at": meta.get("built_at") or "",
    }


def _scan_long_pivot_source(source: str, product: str):
    from core.long_pivot import scan_long_fab, scan_long_inline, scan_long_et

    src = _long_pivot_source(source)
    prod = _long_pivot_product(product)
    db_root = _db_base()
    if src == "fab":
        return scan_long_fab(prod, db_root)
    if src == "inline":
        return scan_long_inline(prod, db_root)
    return scan_long_et(prod, db_root)


def _long_pivot_function(source: str):
    from core.long_pivot import pivot_fab_wide, pivot_inline_wafer, pivot_et_wafer

    src = _long_pivot_source(source)
    if src == "fab":
        return pivot_fab_wide
    if src == "inline":
        return pivot_inline_wafer
    return pivot_et_wafer


def _build_long_pivot_cache(source: str, product: str, *, force: bool = False) -> dict:
    src = _long_pivot_source(source)
    prod = _long_pivot_product(product)
    status = _long_pivot_cache_status(src, prod)
    if status.get("status") == "fresh" and not force:
        return {"ok": True, "skipped": True, "reason": "fresh", "pivot_cache": _long_pivot_cache_public(status)}
    try:
        from core.runtime_limits import process_memory_high
        if process_memory_high():
            return {"ok": False, "skipped": True, "reason": "process_memory_high", "pivot_cache": _long_pivot_cache_public(status)}
    except Exception:
        pass
    lf = _scan_long_pivot_source(src, prod)
    if lf is None:
        return {"ok": False, "skipped": True, "reason": "source_missing", "pivot_cache": _long_pivot_cache_public(status)}
    pivot = _long_pivot_function(src)
    cache_fp = _long_pivot_cache_path(src, prod)
    tmp = cache_fp.with_suffix(cache_fp.suffix + ".tmp")
    cache_fp.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    wide = None
    try:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        wide = pivot(lf)
        wide.write_parquet(str(tmp))
        tmp.replace(cache_fp)
        meta = {
            "version": LONG_PIVOT_CACHE_VERSION,
            "source": src,
            "product": prod,
            "source_signature": _long_pivot_source_signature(src, prod),
            "row_count": int(wide.height),
            "total_cols": len(wide.columns),
            "schema": {col: str(wide.schema[col]) for col in wide.columns},
            "built_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "build_seconds": round(time.monotonic() - started, 3),
        }
        _write_long_pivot_meta(src, prod, meta)
        return {"ok": True, "cache_path": str(cache_fp), "meta": meta}
    finally:
        if wide is not None:
            try:
                del wide
            except Exception:
                pass
        try:
            gc.collect()
        except Exception:
            pass


def _long_pivot_worker_loop() -> None:
    while True:
        with _LONG_PIVOT_JOB_LOCK:
            if not _LONG_PIVOT_QUEUE:
                _LONG_PIVOT_JOB_STATE.update({"running": False, "queued": False, "current": ""})
                return
            source, product, force = _LONG_PIVOT_QUEUE.popleft()
            key = _long_pivot_key(source, product)
            _LONG_PIVOT_JOB_STATE.update({
                "running": True,
                "queued": bool(_LONG_PIVOT_QUEUE),
                "current": key,
                "started_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "last_error": "",
            })
        try:
            result = _build_long_pivot_cache(source, product, force=force)
            with _LONG_PIVOT_JOB_LOCK:
                _LONG_PIVOT_JOB_STATE["last_source"] = key
                if result.get("reason") and not result.get("ok"):
                    _LONG_PIVOT_JOB_STATE["last_error"] = str(result.get("reason") or "")
        except Exception as exc:
            logger.warning("SplitTable long pivot cache build failed source=%s product=%s: %s", source, product, exc, exc_info=True)
            with _LONG_PIVOT_JOB_LOCK:
                _LONG_PIVOT_JOB_STATE["last_error"] = f"{type(exc).__name__}: {exc}"
                _LONG_PIVOT_JOB_STATE["last_source"] = key
        finally:
            with _LONG_PIVOT_JOB_LOCK:
                _LONG_PIVOT_JOB_STATE["finished_at"] = datetime.datetime.now().isoformat(timespec="seconds")


def enqueue_long_pivot_cache(source: str, product: str, *, force: bool = False) -> dict:
    global _LONG_PIVOT_JOB_THREAD
    src = _long_pivot_source(source)
    prod = _long_pivot_product(product)
    target = _long_pivot_key(src, prod)
    with _LONG_PIVOT_JOB_LOCK:
        current = str(_LONG_PIVOT_JOB_STATE.get("current") or "")
        queued = {_long_pivot_key(q_src, q_prod) for q_src, q_prod, _force in _LONG_PIVOT_QUEUE}
        if target != current and target not in queued:
            _LONG_PIVOT_QUEUE.append((src, prod, bool(force)))
        _LONG_PIVOT_JOB_STATE["queued"] = bool(_LONG_PIVOT_QUEUE)
        if _LONG_PIVOT_JOB_THREAD is None or not _LONG_PIVOT_JOB_THREAD.is_alive():
            _LONG_PIVOT_JOB_THREAD = threading.Thread(target=_long_pivot_worker_loop, name="splittable-long-pivot-cache", daemon=True)
            _LONG_PIVOT_JOB_THREAD.start()
        state = dict(_LONG_PIVOT_JOB_STATE)
    status = "running" if state.get("running") and state.get("current") == target else "queued"
    return {"ok": True, "queued": True, "status": status, "job": state}


def _long_pivot_inline_resource_guard() -> tuple[str, dict]:
    try:
        from core import runtime_limits
        if runtime_limits.process_memory_high():
            return "process_memory_high", runtime_limits.process_memory_snapshot()
        cpu = runtime_limits.process_cpu_snapshot()
        if bool(cpu.get("process_cpu_over_limit")):
            return "process_cpu_high", cpu
    except Exception:
        return "", {}
    return "", {}


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
    src = _long_pivot_source(source)
    prod = _long_pivot_product(product)
    try:
        limit = max(1, min(500, int(limit or 20)))
    except Exception:
        limit = 20
    status = _long_pivot_cache_status(src, prod)
    if status.get("status") == "fresh":
        wide = pl.scan_parquet(status["cache_path"]).head(limit).collect()
        return {
            "source": src,
            "product": prod,
            "columns": wide.columns,
            "rows": wide.to_dicts(),
            "total_preview": wide.height,
            "pivot_cache": _long_pivot_cache_public(status),
        }
    guard_reason, guard_snapshot = _long_pivot_inline_resource_guard()
    if guard_reason:
        queued = enqueue_long_pivot_cache(src, prod, force=False) if status.get("source_exists") else {}
        return {
            "source": src,
            "product": prod,
            "columns": [],
            "rows": [],
            "total_preview": 0,
            "note": "Pivot cache is preparing in the background.",
            "pivot_cache": _long_pivot_cache_public(status, queued),
            "resource_guard": {"reason": guard_reason, **guard_snapshot},
        }
    lf = _scan_long_pivot_source(src, prod)
    if lf is None:
        return {
            "source": src,
            "product": prod,
            "rows": [],
            "columns": [],
            "note": "원천 hive 경로 미존재",
            "pivot_cache": _long_pivot_cache_public(_long_pivot_cache_status(src, prod)),
        }
    queued = enqueue_long_pivot_cache(src, prod, force=False)
    pivot = _long_pivot_function(src)
    wide = None
    try:
        wide = pivot(lf)
        preview = wide.head(limit)
        return {
            "source": src,
            "product": prod,
            "columns": preview.columns,
            "rows": preview.to_dicts(),
            "total_preview": preview.height,
            "note": "Pivot cache is preparing in the background.",
            "pivot_cache": _long_pivot_cache_public(status, queued),
        }
    finally:
        if wide is not None:
            try:
                del wide
            except Exception:
                pass
        try:
            gc.collect()
        except Exception:
            pass


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


# ── 쿼리 병렬 워커 수 관리 ──────────────────────────────────────────
# 운영 SplitTable Polars 풀은 기본 4코어, 1~4 범위에서 저장 가능하다.
# 개발은 1코어 고정이다. DuckDB 전역 설정은 건드리지 않아 파일탐색기 SQL과
# Flow-i가 SplitTable 튜닝에 함께 제한되지 않게 한다. Polars 풀은 시작 시 고정돼
# 운영값 변경은 서버 재시작 후 적용된다.

def _normalize_query_workers(value: Any) -> int:
    """운영 설정을 1~4 및 실제 CPU 범위로 클램프. 개발은 항상 1."""
    if _ml_table_lookup._root_ram_cache_use_dev():
        return 1
    try:
        n = int(value)
    except Exception:
        n = 4
    if n <= 0:
        n = 4
    from core.runtime_limits import effective_cpu_count
    max_cpu = max(1, min(4, int(effective_cpu_count())))
    return max(1, min(n, max_cpu))


def _apply_query_workers(workers: int) -> None:
    """Polars 풀은 런타임 재크기 불가. 저장만 하고 재시작 적용임을 명시한다.

    과거처럼 FLOW_DUCKDB_THREADS 를 바꾸지 않는다. 그 값은 파일탐색기 SQL 등
    SplitTable 밖의 DuckDB 사용자까지 제한해 개발 서버 우선순위와 충돌했다.
    """
    return None


def _query_workers_key() -> str:
    """운영 저장 키. 개발은 파일에 값을 쓰지 않는 고정 정책이다."""
    return "fixed_dev_1" if _ml_table_lookup._root_ram_cache_use_dev() else "query_workers"


def _current_query_workers() -> int:
    """운영 저장값(기본 4). 개발 서버는 저장 내용과 무관하게 1."""
    if _ml_table_lookup._root_ram_cache_use_dev():
        return 1
    try:
        cfg = load_json(SOURCE_CFG, {})
        return _normalize_query_workers(cfg.get("query_workers", 4))
    except Exception:
        return _normalize_query_workers(4)


def _current_query_workers_status() -> dict:
    """현재 쿼리 병렬화 상태를 반환."""
    from core.runtime_limits import effective_cpu_count
    configured = _current_query_workers()
    cpu_count = int(effective_cpu_count())
    is_dev = _ml_table_lookup._root_ram_cache_use_dev()
    desired = 1 if is_dev else configured
    try:
        effective = max(1, int(os.environ.get("POLARS_MAX_THREADS", desired)))
    except Exception:
        effective = desired
    # essential 세마포어 동시성 — 동시에 몇 명이 조회할 수 있는지
    essential_concurrency = int(os.environ.get(
        "FLOW_ESSENTIAL_REQUEST_CONCURRENCY", "0")) or None
    return {
        "configured": configured,
        "effective": effective,
        "auto_value": 1 if is_dev else min(4, max(1, cpu_count)),
        "cpu_count": cpu_count,
        "cpu_budget": desired,
        # 이 서버가 개발(dev) 몫인지와 저장에 쓰는 키 — 화면이 "지금 어느 서버를
        # 튜닝 중인지"를 명시해, 개발 값을 바꾼다는 게 운영에 영향 없음을 보인다.
        "is_dev": is_dev,
        "config_key": _query_workers_key(),
        # Polars 는 시작 시 역할 기반(api=4/worker=3/standalone=auto)으로 1회 고정 —
        # query_workers 변경에 영향받지 않고 재시작해야 바뀐다.
        "polars_threads": os.environ.get("POLARS_MAX_THREADS", ""),
        "polars_runtime_fixed": True,
        "fixed": is_dev,
        "restart_required": effective != desired,
        "desired": desired,
        "duckdb_threads": os.environ.get("FLOW_DUCKDB_THREADS", ""),
        # 예열 워커는 검색 부하와 분리해 1코어 고정(env 로만 조정).
        "ram_cache_workers": _ml_table_lookup._root_ram_cache_load_workers(),
        "essential_concurrency": essential_concurrency,
    }


@router.get("/query-workers")
def get_query_workers():
    """현재 쿼리 병렬 워커 설정과 상태를 반환."""
    return _current_query_workers_status()


@router.post("/query-workers/save")
def save_query_workers(req: dict, _perm=Depends(require_page_manager("splittable"))):
    """쿼리 병렬 워커 수를 저장하고 즉시 반영.

    운영 서버만 1~4 값을 저장한다. 개발 서버는 1코어 고정이며 운영 설정을
    건드리지 않는다. Polars 풀 특성상 운영 저장값은 재시작 후 적용된다."""
    if _ml_table_lookup._root_ram_cache_use_dev():
        # 개발은 정책상 1코어 고정. 공유 설정 파일의 운영값을 건드리지 않는다.
        return _current_query_workers_status()
    raw = req.get("query_workers", 4)
    workers = _normalize_query_workers(raw)
    cur = load_json(SOURCE_CFG, {"enabled": [], "lot_overrides": {}})
    cur[_query_workers_key()] = workers
    save_json(SOURCE_CFG, cur)
    return _current_query_workers_status()


# 실제 Polars 크기는 router import 전 core.runtime_limits 에서 적용된다.

@router.get("/source-config")
def get_source_config():
    cfg = load_json(SOURCE_CFG, {"enabled": []})
    cfg.setdefault("enabled", [])
    cfg.setdefault("lot_overrides", {})  # v8.4.4: product-scoped {root_col, fab_col, fab_source, ts_col, join_keys}
    cfg.setdefault("root_lot_cache", _ml_table_lookup.root_ram_cache_settings())
    # 지정 팀 수신자 폐기 — 수신 대상은 계획 작성자 + 제품 동명 그룹 멤버로 고정.
    cfg.pop("mismatch_alert_recipients", None)
    cfg.setdefault("mismatch_mail_enabled", False)  # plan/actual 불일치 메일 발송 (기본 off)
    cfg.setdefault("query_workers", 4)  # 운영 기본 4, 개발은 별도 정책으로 1 고정
    # v8.8.21: 응답 단에서도 root:~~ 남은 값은 표시 안 되게 정리.
    _migrate_legacy_root_prefix(cfg)
    return cfg

class SourceConfigReq(BaseModel):
    enabled: List[str] = []
    lot_overrides: dict = {}  # v8.4.4
    root_lot_cache: dict | None = None
    # plan/actual 불일치 알람을 메일로도 발송할지 여부 (기본 off).
    mismatch_mail_enabled: bool | None = None
    # 쿼리 병렬 워커 수 (Polars/DuckDB/RAM 캐시 예열). 0 = 자동(호스트 CPU 비례).
    query_workers: int | None = None


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


def _normalize_root_lot_cache_settings(raw: dict | None, prev: dict | None = None) -> dict:
    data = raw if isinstance(raw, dict) else {}
    step_raw = data.get("step_ids")
    if isinstance(step_raw, str):
        step_parts = step_raw.replace("\n", ",").split(",")
    elif isinstance(step_raw, (list, tuple, set)):
        step_parts = list(step_raw)
    else:
        step_parts = []
    step_ids = []
    seen = set()
    for item in step_parts:
        step = str(item or "").strip().upper()
        if not step or step in seen:
            continue
        seen.add(step)
        step_ids.append(step)

    def _num(key: str, default: int) -> int:
        try:
            value = int(data.get(key))
        except Exception:
            value = default
        return max(0, min(ROOT_LOT_CACHE_LIMIT_MAX, value))

    out = {
        "step_ids": step_ids,
        "searched_limit": _num("searched_limit", 1000),
        "target_roots": _num("target_roots", 1000),
    }
    # AZ prefix 우선 적재 설정 — 요청에 없으면 기존 저장값 보존(전체 교체 시 소실 방지).
    prefix_raw = data.get("priority_root_prefix")
    if prefix_raw is None and isinstance(prev, dict):
        prefix_raw = prev.get("priority_root_prefix")
    if prefix_raw is not None:
        out["priority_root_prefix"] = str(prefix_raw or "").strip().upper()
    return out


@router.post("/source-config/save")
def save_source_config(req: SourceConfigReq, _perm=Depends(require_page_manager("splittable"))):
    cur = load_json(SOURCE_CFG, {"enabled": [], "lot_overrides": {}})
    cur["enabled"] = req.enabled
    if req.lot_overrides:
        cur.setdefault("lot_overrides", {}).update(req.lot_overrides)
    if req.root_lot_cache is not None:
        cur["root_lot_cache"] = _normalize_root_lot_cache_settings(req.root_lot_cache, cur.get("root_lot_cache"))
    # 지정 팀 수신자 폐기 — 저장 파일에서도 제거.
    cur.pop("mismatch_alert_recipients", None)
    if req.mismatch_mail_enabled is not None:
        cur["mismatch_mail_enabled"] = bool(req.mismatch_mail_enabled)
    if req.query_workers is not None and not _ml_table_lookup._root_ram_cache_use_dev():
        cur["query_workers"] = _normalize_query_workers(req.query_workers)
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
#   ppid_knob.csv:      feature_name, rule_order, step_desc, operator, value, category
#                        value = SplitTable cell value such as PPID_01_2
#   Vehicle_matching.csv: product, step_id, step_desc (preferred)
#   step_matching.csv:    product, step_id, function_step (legacy fallback)
# For each KNOB feature_name, we keep product-common ppid_knob CSV rule rows in
# rule_order, expand each step_desc through the current product's matching
# step_ids, and produce both a structured `groups` payload and a label:
#   GATE_PATTERN (AA200030/AA200040/AA200050) + PC_ETCH (AA200100/AA200110)
def _load_csv_rows(fp: Path) -> list[dict]:
    if not fp.is_file():
        return []
    try:
        st = fp.stat()
        key = str(fp.resolve())
        cached = _CSV_ROWS_CACHE.get(key)
        if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
            return [dict(row) for row in cached[2]]
        with open(fp, "r", encoding="utf-8-sig") as f:
            reader = csv_mod.DictReader(f)
            rows = []
            for row in reader:
                clean = {}
                for col_key, value in (row or {}).items():
                    if col_key is None:
                        continue
                    clean[str(col_key).lstrip("\ufeff").strip()] = value
                rows.append(clean)
        # 무한 성장 방지 — 파일 단위 키가 계속 늘 수 있어 오래된 것부터 정리.
        while len(_CSV_ROWS_CACHE) >= _CSV_ROWS_CACHE_MAX:
            _CSV_ROWS_CACHE.pop(next(iter(_CSV_ROWS_CACHE)), None)
        _CSV_ROWS_CACHE[key] = (st.st_mtime, st.st_size, [dict(row) for row in rows])
        return rows
    except Exception:
        return []


def _canonical_product_name(product: str) -> str:
    raw = str(product or "").strip()
    return _split_product_core(raw) or raw


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
                while len(_SCHEMA_COLUMNS_CACHE) >= _SCHEMA_COLUMNS_CACHE_MAX:
                    _SCHEMA_COLUMNS_CACHE.pop(next(iter(_SCHEMA_COLUMNS_CACHE)), None)
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


def _first_row_value(row: dict, *cols: str) -> str:
    for col in cols:
        if not col:
            continue
        value = str((row or {}).get(col) or "").strip()
        if value:
            return value
    return ""


def _row_step_desc(row: dict, schema: dict) -> str:
    return _first_row_value(
        row,
        schema.get("step_desc_col", "step_desc"),
        schema.get("func_step_col", "function_step"),
        "step_desc",
        "function_step",
        "func_step",
    )


def _knob_step_matching_path(base: Path | None = None) -> Path:
    return _rulebook_path_for_base("step_matching", base)


def _load_knob_step_matching_rows(base: Path | None = None) -> list[dict]:
    primary_path = _knob_step_matching_path(base)
    rows = _load_csv_rows(primary_path)
    root = base or _base_root()
    meta = _RULEBOOK_FILES.get("step_matching", {})
    legacy_fn = meta.get("legacy_filename")
    if legacy_fn and primary_path.name.casefold() != str(legacy_fn).casefold():
        legacy_path = root / str(legacy_fn)
        if legacy_path.is_file():
            legacy_rows = _load_csv_rows(legacy_path)
            seen_pairs = {
                (str(r.get("product") or "").strip().casefold(),
                 str(r.get("step_desc") or r.get("function_step") or "").strip().casefold())
                for r in rows
            }
            for lr in legacy_rows:
                pair = (
                    str(lr.get("product") or "").strip().casefold(),
                    str(lr.get("step_desc") or lr.get("function_step") or "").strip().casefold()
                )
                if pair not in seen_pairs:
                    rows.append(lr)
                    seen_pairs.add(pair)
    return rows


# module 의 유일한 원천은 Vehicle_matching.csv 의 module 열이다.
# step_desc 로 공정 구간을 추측하던 폴백(classify_process_area)은 제거했다 —
# 파일에 module 열이 없는 환경에서도 값이 만들어져 SplitTable 좌측 module 열이
# 근거 없이 나타났다. 열이 없으면 빈 값이고, 그러면 프런트가 열 자체를 안 붙인다.
def _vehicle_module_of(row: dict, sm: dict) -> str:
    for col in (sm.get("module_col", "module"), "module", "area"):
        if not col:
            continue
        value = str(row.get(col) or "").strip()
        if value:
            return value
    return ""


def _product_step_map_by_desc(product: str, base: Path | None = None) -> dict[str, list[dict]]:
    matching = _load_knob_step_matching_rows(base)
    sm = _sch("step_matching")
    step_map: dict[str, list[dict]] = {}
    p_col = sm.get("product_col", "product")
    has_product_col = any(p_col in r or "product" in r for r in matching)
    for r in matching:
        row_prod = r.get(p_col)
        if row_prod is None and p_col != "product":
            row_prod = r.get("product")
        if not _step_matching_product_matches(product, row_prod, allow_common=not has_product_col):
            continue
        step_desc = _row_step_desc(r, sm)
        step_desc_key = _step_desc_match_key(step_desc)
        step_id = (r.get(sm.get("step_id_col", "step_id")) or r.get("raw_step_id") or "").strip()
        if not step_desc_key or not step_id:
            continue
        item = {
            "step_desc": step_desc,
            "step_id": step_id,
            "module": _vehicle_module_of(r, sm),
        }
        for k in _dedup_list([
            step_desc_key,
            step_desc_key.replace("_", " "),
            step_desc_key.replace(" ", "_"),
            step_id.casefold(),
        ]):
            bucket = step_map.setdefault(k, [])
            if not any(str(x.get("step_id") or "").strip().casefold() == step_id.casefold() for x in bucket):
                bucket.append(item)
    return step_map


def _stage_steps_by_major(product: str) -> dict[int, list[dict]]:
    matching = _load_knob_step_matching_rows()
    sm = _sch("step_matching")
    exact_has_numeric = False
    p_col = sm.get("product_col", "product")
    has_product_col = any(p_col in r or "product" in r for r in matching)
    for r in matching:
        row_prod = r.get(p_col)
        if row_prod is None and p_col != "product":
            row_prod = r.get("product")
        if not _step_matching_product_matches(product, row_prod, allow_common=not has_product_col):
            continue
        if _stage_major(_row_step_desc(r, sm)) is not None:
            exact_has_numeric = True
            break
    out: dict[int, list[dict]] = {}
    seen: dict[int, set[tuple[str, str]]] = {}
    for r in matching:
        row_prod = r.get(p_col)
        if row_prod is None and p_col != "product":
            row_prod = r.get("product")
        row_prod = str(row_prod or "").strip()
        if exact_has_numeric:
            if not _step_matching_product_matches(product, row_prod, allow_common=False):
                continue
        elif not _step_matching_product_matches(product, row_prod, allow_common=not has_product_col):
            continue
        fs = _row_step_desc(r, sm)
        sid = (r.get(sm.get("step_id_col", "step_id")) or r.get("raw_step_id") or "").strip()
        major = _stage_major(fs)
        if not fs:
            continue
        item = {
            "func_step": fs,
            "step_id": sid,
            "module": _vehicle_module_of(r, sm),
            # 표시용 module 과 달리 이건 **매칭 전용 추측값**이다. step_desc 로 어느
            # 공정 구간인지 짐작해 _stage_steps_for_tail 의 module 우선 매칭 단계를
            # 살려둔다(M1 이 M2_OVL_M1 에 붙는 걸 막는 tier). 화면의 module 열은
            # 이 값을 절대 쓰지 않는다 — Vehicle_matching 의 module 열만 근거다.
            "area_guess": classify_process_area(fs) or "",
            "step_class": str(r.get("step_class") or "").strip(),
        }
        key = (item["func_step"], item["step_id"])
        # 실제 Vehicle_matching의 step_desc는 `4.0 GATE_OX`처럼 번호가 붙는 경우뿐
        # 아니라 `GATE_ETCH`처럼 이름만 있는 경우도 많다. 번호 없는 공정도 공통
        # bucket에 보존해야 FAB/MASK 등의 stage 이름을 alias/function으로 연결할 수 있다.
        bucket_major = major if major is not None else -1
        bucket_seen = seen.setdefault(bucket_major, set())
        if key in bucket_seen:
            continue
        bucket_seen.add(key)
        out.setdefault(bucket_major, []).append(item)
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
    # Vehicle_matching 의 module 열이 없으면 step_desc 추측값(area_guess)으로 본다 —
    # 이 tier 는 매칭 품질용이라 열 유무와 무관하게 유지되어야 한다.
    def _item_area(item: dict) -> str:
        return _norm_stage_text(item.get("module", "") or item.get("area_guess", ""))

    module_hits = _collect(lambda item: any(
        alias == _item_area(item) or (alias and alias in _item_area(item))
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
        # INLINE 은 module 로 묶지 않는다 (_build_inline_meta 와 같은 규칙).
        modules = [] if pref == "INLINE" else _dedup_list([x.get("module", "") for x in steps])
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
    ppid_knob_fp = base / "ppid_knob.csv"
    knob_rules = _load_csv_rows(ppid_knob_fp if ppid_knob_fp.is_file() else base / "knob_ppid.csv")
    # v8.8.10: 역할→컬럼명 매핑 soft-landing. 사내 CSV 의 컬럼 이름이 달라도 schema 만 바꾸면 됨.
    km = _sch("knob_ppid")

    # step_desc → [{step_id,module}, ...] (ordered, dedup)
    step_map = _product_step_map_by_desc(product, base)
    step_by_id = {
        str(item.get("step_id") or "").strip().casefold(): item
        for items in step_map.values() for item in items
        if str(item.get("step_id") or "").strip()
    }

    def _multi_values(value) -> list[str]:
        return _dedup_list(_re.split(r"[,;|]+", str(value or "")))

    requested_product = _canonical_product_name(product).casefold()

    # feature_name → CSV rule row groups (sorted by rule_order)
    feats: dict[str, list[dict]] = {}
    for r in knob_rules:
        # 매칭 채우기가 만든 product/step_id/step_desc가 있으면 FAB에서 확인된 직접
        # 매핑을 우선한다. 열이 없는 예전 rulebook은 기존 step_desc→Vehicle 경로를 쓴다.
        row_products = _multi_values(r.get("product"))
        matched_product_indexes = [
            index for index, value in enumerate(row_products)
            if _canonical_product_name(value).casefold() == requested_product
        ] if requested_product and row_products else []
        if requested_product and row_products and not matched_product_indexes:
            continue
        all_direct_ids = _multi_values(r.get("step_id"))
        all_direct_descs = _multi_values(r.get("step_desc"))
        direct_ids = (
            [all_direct_ids[index] for index in matched_product_indexes if index < len(all_direct_ids)]
            if matched_product_indexes and len(all_direct_ids) == len(row_products)
            else all_direct_ids
        )
        direct_descs = (
            [all_direct_descs[index] for index in matched_product_indexes if index < len(all_direct_descs)]
            if matched_product_indexes and len(all_direct_descs) == len(row_products)
            else all_direct_descs
        )
        direct_steps = [step_by_id.get(sid.casefold()) for sid in direct_ids]
        direct_steps = [item for item in direct_steps if item]
        fname = (r.get(km.get("feature_col", "feature_name")) or "").strip()
        step_desc = next((desc for desc in direct_descs if desc), "")
        if not step_desc:
            step_desc = next((str(item.get("step_desc") or "").strip() for item in direct_steps
                              if str(item.get("step_desc") or "").strip()), "")
        if not step_desc:
            step_desc = _row_step_desc(r, km)
        step_desc_key = _step_desc_match_key(step_desc)
        if not direct_ids and step_desc:
            if step_desc.casefold() in step_by_id:
                direct_ids = [step_desc]
                direct_steps = [step_by_id[step_desc.casefold()]]
            elif _re.match(r"^[A-Za-z]{2}\d{4,}", step_desc):
                direct_ids = [step_desc]
        value = _first_row_value(
            r,
            km.get("value_col", "value"),
            km.get("ppid_col", "ppid"),
            "value",
            "ppid",
            "category",
        )
        if not fname or (not step_desc_key and not direct_ids):
            continue
        matched_steps = direct_steps if direct_ids else step_map.get(step_desc_key, [])
        order_label = _rule_order_label(r.get(km.get("rule_order_col", "rule_order")), len(feats.get(fname, [])) + 1)
        feats.setdefault(fname, []).append({
            "func_step": step_desc,
            "step_desc": step_desc,
            "rule_order": order_label,
            "rule_order_sort": _rule_order_sort_key(order_label),
            "ppid": value,
            "value": value,
            "operator": (r.get(km.get("operator_col", "operator")) or "").strip(),
            "category": (r.get(km.get("category_col", "category")) or "").strip(),
            "step_ids": direct_ids or [str(x.get("step_id") or "").strip() for x in matched_steps if str(x.get("step_id") or "").strip()],
            "modules": [str(x.get("module") or "").strip() for x in matched_steps if str(x.get("module") or "").strip()],
        })

    # Sort each feature's groups by rule_order + build a human label
    out: dict[str, dict] = {}
    for fname, groups in feats.items():
        groups.sort(key=lambda g: g.get("rule_order_sort") or _rule_order_sort_key(g.get("rule_order")))
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
                seg = g["step_desc"]
            elif len(sids) == 1:
                seg = f"{g['step_desc']} ({sids[0]})"
            else:
                seg = f"{g['step_desc']} ({'/'.join(sids)})"
            parts.append(seg)
            if i < len(groups) - 1:
                parts.append(" + ")
        feature_entry = {
            "groups": groups,
            "label": "".join(parts),
            "modules": feat_modules,
        }
        clean_split = _re.sub(r"_split$", "", fname, flags=_re.I)
        for base_k in _dedup_list([
            fname,
            clean_split,
            fname.replace(" ", "_"),
            clean_split.replace(" ", "_"),
            fname.replace("_", " "),
            clean_split.replace("_", " "),
        ]):
            out[base_k] = feature_entry
            out[f"KNOB_{base_k}"] = feature_entry
            out[f"{base_k}_Split"] = feature_entry
            out[f"KNOB_{base_k}_Split"] = feature_entry
    for key, meta in _inferred_stage_meta(product, "KNOB").items():
        out.setdefault(key, meta)
    return out


# ── SplitTable 공정 순서(step order) 컨텍스트 ──
#   ① 표시 순서: 여러 prefix를 함께 볼 때 KNOB/FAB/MASK/INLINE/VM끼리 묶지 않고,
#      각 parameter가 연결된 Vehicle_matching.csv step_id의 공정 순서(파일 행 순서)로
#      한 줄에 섞어 정렬한다. 여러 step_id에 걸친 parameter는 마지막 step을 대표로
#      쓰고, 매핑이 없는 행만 기존 자연 정렬로 뒤에 붙는다.
#   ② 진행 셰이딩: 검색 root 의 latest-lot 캐시 step_id 보다 뒤(미진행) 공정에
#      해당하는 행 목록을 payload.step_progress 로 내려 FE 가 회색으로 칠한다.
_STEP_ORDER_CTX_CACHE: OrderedDict = OrderedDict()
_STEP_ORDER_CTX_TTL_SEC = 60.0
_STEP_ORDER_CTX_MAX = 32
_STEP_ORDER_CTX_LOCK = threading.Lock()
_STEP_ID_PREFIX_NUM_RE = _re.compile(r"^([A-Za-z]+)(\d+)")


def _step_order_rank_from_meta(meta: dict, seq_rank: dict[str, int]):
    """메타가 가리키는 step 중 가장 뒤 공정을 대표 rank로 반환한다."""
    if not isinstance(meta, dict):
        return None, ""
    step_ids: list[str] = []
    for group in meta.get("groups") if isinstance(meta.get("groups"), list) else []:
        if not isinstance(group, dict):
            continue
        ids = group.get("step_ids") if isinstance(group.get("step_ids"), list) else []
        step_ids.extend(str(s or "").strip() for s in ids if str(s or "").strip())
        sid = str(group.get("step_id") or "").strip()
        if sid:
            step_ids.append(sid)
    ids = meta.get("step_ids") if isinstance(meta.get("step_ids"), list) else []
    step_ids.extend(str(s or "").strip() for s in ids if str(s or "").strip())
    sid = str(meta.get("step_id") or "").strip()
    if sid:
        step_ids.append(sid)
    ranked = [(seq_rank.get(raw.upper()), raw.upper()) for raw in _dedup_list(step_ids)]
    ranked = [(rank, raw) for rank, raw in ranked if rank is not None]
    if not ranked:
        return None, ""
    return max(ranked, key=lambda item: item[0])


def _register_step_order_meta(param_rank: dict[str, int], param_step: dict[str, str],
                              meta_map: dict, prefix: str, seq_rank: dict[str, int],
                              *, overwrite: bool = False) -> None:
    """bare/full parameter 이름을 동일 공정 rank에 등록한다."""
    pref = str(prefix or "").strip().upper()
    for raw_name, meta in (meta_map or {}).items():
        rank, sid = _step_order_rank_from_meta(meta, seq_rank)
        if rank is None:
            continue
        name = str(raw_name or "").strip()
        if not name:
            continue
        tail = name.split("_", 1)[1] if pref and name.upper().startswith(pref + "_") else name
        cand_list = [name, tail, f"{pref}_{tail}" if pref else tail]
        variants = set()
        for cand in cand_list:
            if not cand:
                continue
            clean_split = _re.sub(r"_split$", "", cand, flags=_re.I)
            for b in (cand, clean_split):
                variants.add(b)
                variants.add(f"{b}_Split")
                variants.add(b.replace(" ", "_"))
                variants.add(f"{b.replace(' ', '_')}_Split")
                variants.add(b.replace("_", " "))
                variants.add(f"{b.replace('_', ' ')}_Split")
        for alias in variants:
            key = str(alias or "").strip().upper()
            if not key:
                continue
            if overwrite or key not in param_rank:
                param_rank[key] = rank
                param_step[key] = sid


def _split_step_order_context(product: str) -> dict:
    """product 의 공정 순서 컨텍스트.

    seq_rank:     step_id(upper) → Vehicle_matching 행 순서 rank
    prefix_steps: 영문 프리픽스 → [(step 번호, rank)] (근사 매칭용, 번호 오름차순)
    param_rank:   전체 prefix parameter 명(물리 컬럼명/bare 명, upper) → rank
    param_step:   같은 키 → 대표 step_id
    """
    key = str(product or "").strip().upper()
    now = time.monotonic()
    with _STEP_ORDER_CTX_LOCK:
        hit = _STEP_ORDER_CTX_CACHE.get(key)
        if hit and now - hit[0] <= _STEP_ORDER_CTX_TTL_SEC:
            return hit[1]
    ctx = {"seq_rank": {}, "prefix_steps": {}, "param_rank": {}, "param_step": {}}
    try:
        matching = _load_knob_step_matching_rows()
        sm = _sch("step_matching")
        p_col = sm.get("product_col", "product")
        has_product_col = any(p_col in r or "product" in r for r in matching)
        seq_rank: dict[str, int] = {}
        prefix_steps: dict[str, list[tuple[int, int]]] = {}
        for r in matching:
            row_prod = r.get(p_col)
            if row_prod is None and p_col != "product":
                row_prod = r.get("product")
            if not _step_matching_product_matches(product, row_prod, allow_common=not has_product_col):
                continue
            sid = str(r.get(sm.get("step_id_col", "step_id")) or r.get("raw_step_id") or "").strip().upper()
            if not sid or sid in seq_rank:
                continue
            rank = len(seq_rank)
            seq_rank[sid] = rank
            m = _STEP_ID_PREFIX_NUM_RE.match(sid)
            if m:
                prefix_steps.setdefault(m.group(1).upper(), []).append((int(m.group(2)), rank))
        for steps in prefix_steps.values():
            steps.sort()
        param_rank: dict[str, int] = {}
        param_step: dict[str, str] = {}

        # FAB/MASK 등 별도 matching CSV가 없는 prefix는 ML_TABLE 컬럼명과
        # Vehicle step_desc/module의 stage 추론 결과로 먼저 등록한다.
        schema_cols = _mltable_schema_columns(product)
        schema_prefixes = {
            str(col).split("_", 1)[0].strip().upper()
            for col in schema_cols if "_" in str(col)
        }
        configured_prefixes = {
            str(pref or "").strip().upper() for pref in (_load_prefixes() or [])
            if str(pref or "").strip()
        }
        for pref in sorted(schema_prefixes | configured_prefixes):
            # MASK는 자체 step_id/matching 원천이 없으므로 이름만 보고 공정을 만들지 않는다.
            if pref in {"KNOB", "INLINE", "VM", "MASK"}:
                continue
            _register_step_order_meta(
                param_rank, param_step, _inferred_stage_meta(product, pref), pref, seq_rank,
            )

        # 명시적 matching 메타는 추론값보다 우선한다. KNOB 메타 안에는 매칭 CSV에
        # 없는 컬럼의 추론 fallback도 함께 들어 있어 KNOB 전체를 한 번에 처리한다.
        for pref, meta_builder in (
            ("KNOB", _build_knob_meta),
            ("INLINE", _build_inline_meta),
            ("VM", _build_vm_meta),
        ):
            _register_step_order_meta(
                param_rank, param_step, meta_builder(product) or {}, pref, seq_rank,
                overwrite=True,
            )
        ctx = {"seq_rank": seq_rank, "prefix_steps": prefix_steps,
               "param_rank": param_rank, "param_step": param_step}
    except Exception as e:
        logger.warning("split step-order context 실패 (product=%s): %s", product, e)
    with _STEP_ORDER_CTX_LOCK:
        _STEP_ORDER_CTX_CACHE[key] = (now, ctx)
        while len(_STEP_ORDER_CTX_CACHE) > _STEP_ORDER_CTX_MAX:
            _STEP_ORDER_CTX_CACHE.popitem(last=False)
    return ctx


def _step_order_sort_key(col_name: str, display: str, param_rank: dict):
    """공정 순서 rank 우선, 같은 rank/매핑 없음은 기존 자연 정렬 유지.

    TAG_* 는 엔지니어가 직접 만든 주석 열이라 공정 순서와 무관하다. 표 맨 위에
    고정하고, 기본 purpose를 첫 줄에 둔 뒤 나머지 TAG를 기존 자연 정렬로 붙인다.
    """
    tag_pfx = f"{CUSTOM_TAG_PREFIX}_"
    if (str(col_name or "").strip().upper().startswith(tag_pfx)
            or str(display or "").strip().upper().startswith(tag_pfx)):
        is_purpose = str(col_name or "").strip().upper() == DEFAULT_CUSTOM_TAG_COLUMN.upper()
        return (-1, 0 if is_purpose else 1, _natural_param_key(display or col_name))
    rank = param_rank.get(str(col_name or "").strip().upper())
    if rank is None:
        rank = param_rank.get(str(display or "").strip().upper())
    if rank is None and col_name:
        clean = _re.sub(r"_split$", "", str(col_name).strip(), flags=_re.I).upper()
        rank = param_rank.get(clean) or param_rank.get(clean.replace(" ", "_")) or param_rank.get(clean.replace("_", " "))
    if rank is None and display:
        clean_disp = _re.sub(r"_split$", "", str(display).strip(), flags=_re.I).upper()
        rank = param_rank.get(clean_disp) or param_rank.get(clean_disp.replace(" ", "_")) or param_rank.get(clean_disp.replace("_", " "))
    nat = _natural_param_key(display or col_name)
    if rank is None:
        return (1, 0, nat)
    return (0, rank, nat)


def _step_rank_for_progress(ctx: dict, step_id: str):
    """현재 진행 step_id 의 공정 순서 rank. 정확 매칭이 없으면(중간 step 등)
    같은 영문 프리픽스에서 step 번호가 작거나 같은 main step 중 가장 뒤의 rank."""
    sid = str(step_id or "").strip().upper()
    if not sid:
        return None
    rank = (ctx.get("seq_rank") or {}).get(sid)
    if rank is not None:
        return rank
    m = _STEP_ID_PREFIX_NUM_RE.match(sid)
    if not m:
        return None
    best = None
    for num, cand_rank in (ctx.get("prefix_steps") or {}).get(m.group(1).upper(), []):
        if num <= int(m.group(2)):
            best = cand_rank
        else:
            break
    return best


_ROOT_LATEST_STEP_CACHE: OrderedDict = OrderedDict()
_ROOT_LATEST_STEP_TTL_SEC = 180.0
_ROOT_LATEST_STEP_MAX = 64
_ROOT_LATEST_STEP_LOCK = threading.Lock()


def _root_latest_step_state(product: str, root_lot_id: str) -> dict:
    """Latest step for the root and independently for each physical wafer.

    per-root 파티션 우선, 없으면 monolithic 1회 필터 스캔(메모리 압박 시 생략).
    """
    root = str(root_lot_id or "").strip().upper()
    if not root:
        return {"step_id": "", "by_wafer": {}}
    key = (str(product or "").strip().upper(), root)
    now = time.monotonic()
    with _ROOT_LATEST_STEP_LOCK:
        hit = _ROOT_LATEST_STEP_CACHE.get(key)
        if hit and now - hit[0] <= _ROOT_LATEST_STEP_TTL_SEC:
            cached = hit[1]
            if isinstance(cached, dict):
                return {"step_id": str(cached.get("step_id") or ""),
                        "by_wafer": dict(cached.get("by_wafer") or {})}
            return {"step_id": str(cached or ""), "by_wafer": {}}
    state = {"step_id": "", "by_wafer": {}}
    try:
        lf = _latest_lot_index_partition_lf(product, root)
        streaming = False
        if lf is None:
            from core.runtime_limits import process_memory_high
            if not process_memory_high():
                lf = _latest_lot_step_cache_lf(product)
                streaming = lf is not None
        if lf is not None:
            names = lf.collect_schema().names()
            want = [c for c in ("root_lot_id", "wafer_id", "step_id", "tkout_time") if c in names]
            if "step_id" in want:
                q = lf.select(want)
                if "root_lot_id" in want:
                    q = q.filter(
                        pl.col("root_lot_id").cast(_STR, strict=False).str.to_uppercase() == root)
                if streaming:
                    from core.parquet_perf import collect_streaming
                    df = collect_streaming(q)
                else:
                    df = q.collect()
                if df.height:
                    if "tkout_time" in df.columns:
                        df = df.sort("tkout_time", descending=True, nulls_last=True)
                    rows = df.iter_rows(named=True)
                    first_sid = ""
                    by_wafer: dict[str, str] = {}
                    for row in rows:
                        sid = str(row.get("step_id") or "").strip().upper()
                        if not sid:
                            continue
                        if not first_sid:
                            first_sid = sid
                        wafer = _normalize_wafer_id(row.get("wafer_id")) if "wafer_id" in df.columns else ""
                        if wafer and wafer not in by_wafer:
                            by_wafer[wafer] = sid
                    state = {"step_id": first_sid, "by_wafer": by_wafer}
    except Exception as e:
        logger.warning("root latest step 조회 실패 (%s/%s): %s", product, root, e)
    with _ROOT_LATEST_STEP_LOCK:
        _ROOT_LATEST_STEP_CACHE[key] = (now, state)
        while len(_ROOT_LATEST_STEP_CACHE) > _ROOT_LATEST_STEP_MAX:
            _ROOT_LATEST_STEP_CACHE.popitem(last=False)
    return {"step_id": state["step_id"], "by_wafer": dict(state["by_wafer"])}


def _root_latest_step_id(product: str, root_lot_id: str) -> str:
    """Backward-compatible root-level latest step accessor."""
    return str(_root_latest_step_state(product, root_lot_id).get("step_id") or "")


def _split_step_progress(product: str, root_lot_id: str, selected: list[str],
                         wafer_keys: list | None = None,
                         fab_present: bool | None = None) -> dict:
    """Progress shading metadata, unique by product/root/wafer.

    ``fab_present=False`` is only supplied after an authoritative FAB lookup
    completed with zero matches.  That is different from a cache/source read
    failure: a confirmed not-yet-in-FAB root has no current step, so every
    displayed process is not reached and must be grey.
    """
    out = {"step_id": "", "not_reached": [], "by_wafer": {}, "fab_missing": False}
    if fab_present is False:
        all_selected = list(dict.fromkeys(str(col) for col in (selected or []) if str(col)))
        out["fab_missing"] = True
        out["not_reached"] = all_selected
        for raw in wafer_keys or []:
            wafer = _normalize_wafer_id(raw)
            if wafer and wafer not in out["by_wafer"]:
                out["by_wafer"][wafer] = {
                    "step_id": "",
                    "not_reached": list(all_selected),
                }
        return out
    try:
        ctx = _split_step_order_context(product)
        param_rank = ctx.get("param_rank") or {}
        if not param_rank or not str(root_lot_id or "").strip():
            return out
        latest = _root_latest_step_state(product, root_lot_id)
        cur_sid = str(latest.get("step_id") or "")
        out["step_id"] = cur_sid
        cur_rank = _step_rank_for_progress(ctx, cur_sid)
        if cur_rank is not None:
            for col in selected or []:
                rank = param_rank.get(str(col or "").strip().upper())
                if rank is not None and rank > cur_rank:
                    out["not_reached"].append(col)

        latest_by_wafer = latest.get("by_wafer") or {}
        requested_wafers: list[str] = []
        for raw in wafer_keys or latest_by_wafer.keys():
            wafer = _normalize_wafer_id(raw)
            if wafer and wafer not in requested_wafers:
                requested_wafers.append(wafer)
        for wafer in requested_wafers:
            wafer_sid = str(latest_by_wafer.get(wafer) or "")
            wafer_rank = _step_rank_for_progress(ctx, wafer_sid)
            not_reached = []
            if wafer_rank is not None:
                for col in selected or []:
                    rank = param_rank.get(str(col or "").strip().upper())
                    if rank is not None and rank > wafer_rank:
                        not_reached.append(col)
            out["by_wafer"][wafer] = {
                "step_id": wafer_sid,
                "not_reached": not_reached,
            }
    except Exception as e:
        logger.warning("split step-progress 계산 실패 (%s/%s): %s", product, root_lot_id, e)
    return out


# v8.7.5/v8.8.10: INLINE / VM_ prefix 매칭 메타 — schema 매핑 기반.
def _build_inline_meta(product: str = "") -> dict:
    """inline_matching.csv (product, step_id, item_id, optional map table)."""
    base = _base_root()
    rows = _load_csv_rows(base / "inline_matching.csv")
    im = _sch("inline_matching")
    sid_to_desc: dict[str, str] = {}
    for steps in _product_step_map_by_desc(product, base).values():
        for step in steps:
            sid = str(step.get("step_id") or "").strip()
            desc = str(step.get("step_desc") or "").strip()
            if sid and desc:
                sid_to_desc.setdefault(sid.casefold(), desc)
    # module 은 inline_matching.csv 에 없다. step_id 로 Vehicle_matching 을 눌러
    # 채우던 보강은 뺐다 — INLINE 은 module 로 따로 묶지 않고 '—' 로 둔다.
    grouped: dict[str, list[dict]] = {}
    p_col = im.get("product_col", "product")
    has_product_col = any(p_col in r or "product" in r for r in rows)
    for r in rows:
        row_prod = r.get(p_col)
        if row_prod is None and p_col != "product":
            row_prod = r.get("product")
        if not _step_matching_product_matches(product, row_prod, allow_common=not has_product_col):
            continue
        iid = (r.get(im.get("item_id_col", "item_id")) or "").strip()
        sid = (r.get(im.get("step_id_col", "step_id")) or "").strip()
        process_id = (r.get(im.get("process_id_col", "process_id")) or "").strip()
        desc = (r.get(im.get("item_desc_col", "item_desc")) or "").strip()
        matching_table = (r.get(im.get("matching_table_col", "matching_table")) or "").strip()
        func_step = (r.get("function_step") or "").strip() or sid_to_desc.get(sid.casefold(), "")
        if not iid or not sid:
            continue
        grouped.setdefault(iid, []).append({
            "step_id": sid,
            "process_id": process_id,
            "item_id": iid,
            "item_desc": desc,
            "matching_table": matching_table,
            "function_step": func_step,
            "step_desc": func_step,
            "module": "",
        })
    out: dict[str, dict] = {}
    for iid, items in grouped.items():
        dedup = []
        seen = set()
        for item in items:
            key = (item.get("function_step", ""), item.get("step_id", ""), item.get("item_desc", ""), item.get("matching_table", ""))
            if key in seen:
                continue
            seen.add(key)
            dedup.append(item)
        step_ids = [x["step_id"] for x in dedup if x.get("step_id")]
        item_desc = next((x.get("item_desc") for x in dedup if x.get("item_desc")), "") or iid
        function_steps = [x["function_step"] for x in dedup if x.get("function_step")]
        process_ids = [x["process_id"] for x in dedup if x.get("process_id")]
        matching_tables = _dedup_list([x.get("matching_table", "") for x in dedup if x.get("matching_table")])
        modules = _dedup_list([x.get("module", "") for x in dedup if x.get("module")])
        out[iid] = {
            "item_id": iid,
            "item_desc": item_desc,
            "modules": modules,
            "module": modules[0] if len(modules) == 1 else "",
            "process_id": process_ids[0] if len(process_ids) == 1 else "",
            "process_ids": process_ids,
            "matching_table": matching_tables[0] if len(matching_tables) == 1 else "",
            "matching_tables": matching_tables,
            "step_id": step_ids[0] if len(step_ids) == 1 else "",
            "step_ids": step_ids,
            "function_step": function_steps[0] if len(function_steps) == 1 else "",
            "function_steps": function_steps,
            "groups": dedup,
            "label": item_desc,
            "sub": "/".join(step_ids) if step_ids else iid,
        }
    return out


def _build_vm_meta(product: str = "") -> dict:
    """vm_matching.csv has step_desc + item_id; step_id comes from Vehicle_matching.csv."""
    base = _base_root()
    rows = _load_csv_rows(base / "vm_matching.csv")
    vm = _sch("vm_matching")
    step_map = _product_step_map_by_desc(product, base)
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        step_desc = _row_step_desc(r, vm)
        item_id = _first_row_value(
            r,
            vm.get("item_id_col", "item_id"),
            "item_id",
            vm.get("feature_col", "feature_name"),
            "feature_name",
        )
        if not step_desc or not item_id:
            continue
        steps = step_map.get(_step_desc_match_key(step_desc), [])
        if not steps:
            continue
        name = f"{step_desc}_{item_id}"
        step_ids = _dedup_list([str(x.get("step_id") or "").strip() for x in steps])
        modules = _dedup_list([str(x.get("module") or "").strip() for x in steps])
        grouped.setdefault(name, []).append({
            "feature_name": name,
            "item_id": item_id,
            "step_desc": step_desc,
            "step_id": step_ids[0] if len(step_ids) == 1 else "",
            "step_ids": step_ids,
            "function_step": step_desc,
            "function_steps": [step_desc],
            "modules": modules,
            "module": modules[0] if len(modules) == 1 else "",
        })
    out: dict[str, dict] = {}
    for fname, items in grouped.items():
        dedup = []
        seen = set()
        for item in items:
            key = (item.get("step_desc", ""), item.get("item_id", ""), tuple(item.get("step_ids") or []))
            if key in seen:
                continue
            seen.add(key)
            dedup.append(item)
        step_ids = _dedup_list([sid for x in dedup for sid in (x.get("step_ids") or [])])
        step_desc = next((x.get("step_desc") for x in dedup if x.get("step_desc")), "") or fname
        item_id = next((x.get("item_id") for x in dedup if x.get("item_id")), "")
        function_steps = _dedup_list([x["function_step"] for x in dedup if x.get("function_step")])
        modules = _dedup_list([mod for x in dedup for mod in (x.get("modules") or [])])
        out[fname] = {
            "feature_name": fname,
            "item_id": item_id,
            "step_desc": step_desc,
            "step_id": step_ids[0] if len(step_ids) == 1 else "",
            "step_ids": step_ids,
            "function_step": function_steps[0] if len(function_steps) == 1 else "",
            "function_steps": function_steps,
            "modules": modules,
            "module": modules[0] if len(modules) == 1 else "",
            "groups": dedup,
            "label": fname,
            "sub": "/".join(step_ids),
        }
    return out


# ── 적용 공정 정보(step label) 표기 ──────────────────────────────────────
#   화면의 "적용 공정 정보" 체크박스와 같은 규약을 서버에서 재현한다.
#     KNOB        → rule_order 별 step_id (서로 다른 step_desc 조건만 `&`)
#     INLINE / VM → `step_id | item_id`
#   표시할 공정이 하나도 없는 매칭 행은 화면에서 감추므로 내보내기에서도 뺀다.
#   TAG/관리 행처럼 매칭 규칙이 없는 행은 원래 이름 그대로 남는다.
#   frontend My_SplitTable.jsx 의 matchStepLines / knobStepLines 와 한 쌍이다.
def _step_label_match_kind(param: str) -> str:
    u = str(param or "").strip().upper()
    if u.startswith("KNOB_") or u == "KNOB":
        return "knob_ppid"
    if u.startswith("INLINE_") or u == "INLINE":
        return "inline_matching"
    if u.startswith("VM_") or u == "VM":
        return "vm_matching"
    return ""


def _step_label_meta_lookup(meta_map: dict, param: str, prefix: str) -> dict:
    if not param or not isinstance(meta_map, dict):
        return {}
    full = str(param or "").strip()
    tail = _re.sub(rf"^{prefix}_", "", full, flags=_re.I).strip()
    if isinstance(meta_map.get(full), dict):
        return meta_map[full]
    if isinstance(meta_map.get(tail), dict):
        return meta_map[tail]
    for k, v in meta_map.items():
        if str(k or "").strip().casefold() in {full.casefold(), tail.casefold()} and isinstance(v, dict):
            return v
    return {}


def _step_label_group_ids(group: dict) -> list[str]:
    ids = group.get("step_ids") if isinstance(group, dict) else None
    if isinstance(ids, list):
        return [str(v or "").strip() for v in ids if str(v or "").strip()]
    sid = str((group or {}).get("step_id") or "").strip()
    return [sid] if sid else []


def _step_label_knob_lines(groups, exclude_not_null: bool = True) -> list[str]:
    by_order: dict[str, list[dict]] = {}
    order_seq: list[str] = []
    for idx, g in enumerate(groups if isinstance(groups, list) else []):
        if not isinstance(g, dict):
            continue
        order = str(g.get("rule_order") or f"R{idx + 1}").strip() or f"R{idx + 1}"
        if order not in by_order:
            by_order[order] = []
            order_seq.append(order)
        by_order[order].append(g)
    lines: list[str] = []
    seen: set[str] = set()
    for order in order_seq:
        by_desc: dict[str, dict] = {}
        for g in by_order[order]:
            operator = _re.sub(r"[\s-]+", "_", str(g.get("operator") or "").strip().lower())
            if operator == "is_null":
                continue
            if exclude_not_null and operator == "not_null":
                continue
            desc = str(g.get("step_desc") or g.get("func_step") or "").strip()
            if not desc:
                continue
            bucket = by_desc.setdefault(desc.casefold(), {"step_ids": [], "seen": set()})
            for sid in _step_label_group_ids(g):
                if sid.casefold() not in bucket["seen"]:
                    bucket["seen"].add(sid.casefold())
                    bucket["step_ids"].append(sid)
        desc_groups = [item for item in by_desc.values() if item["step_ids"]]
        if not desc_groups:
            continue
        line = "\n&\n".join("\n".join(item["step_ids"]) for item in desc_groups)
        if line not in seen:
            seen.add(line)
            lines.append(line)
    return lines


def _step_label_item_lines(meta: dict) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    fallback_item = str((meta or {}).get("item_id") or "").strip()

    def push(sid, item_id):
        s = str(sid or "").strip()
        if not s:
            return
        i = str(item_id or fallback_item or "").strip()
        line = f"{s} | {i}" if i else s
        if line not in seen:
            seen.add(line)
            out.append(line)

    groups = (meta or {}).get("groups")
    for g in groups if isinstance(groups, list) else []:
        if not isinstance(g, dict):
            continue
        ids = _step_label_group_ids(g)
        if ids:
            for sid in ids:
                push(sid, g.get("item_id"))
        else:
            push(g.get("step_id"), g.get("item_id"))
    if not out:
        for sid in ((meta or {}).get("step_ids") or []):
            push(sid, fallback_item)
    return out


def _step_label_metas(product: str) -> dict:
    out = {"knob": {}, "inline": {}, "vm": {}}
    for key, fn in (("knob", _build_knob_meta), ("inline", _build_inline_meta), ("vm", _build_vm_meta)):
        try:
            meta = fn(product)
            out[key] = meta if isinstance(meta, dict) else {}
        except Exception:
            out[key] = {}
    return out


def _step_label_lines_for_param(param: str, metas: dict, exclude_not_null: bool = True) -> tuple[str, list[str]]:
    kind = _step_label_match_kind(param)
    if not kind:
        return "", []
    if kind == "knob_ppid":
        meta = _step_label_meta_lookup(metas.get("knob") or {}, param, "KNOB")
        return kind, _step_label_knob_lines((meta or {}).get("groups") or [], exclude_not_null)
    if kind == "inline_matching":
        return kind, _step_label_item_lines(_step_label_meta_lookup(metas.get("inline") or {}, param, "INLINE"))
    return kind, _step_label_item_lines(_step_label_meta_lookup(metas.get("vm") or {}, param, "VM"))


def _step_process_columns_for_param(param: str, metas: dict,
                                    exclude_not_null: bool = True) -> dict[str, str]:
    """Return the two display/export columns without replacing the parameter label."""
    kind = _step_label_match_kind(param)
    if not kind:
        return {"step_id": "", "step_desc": ""}
    if kind == "knob_ppid":
        meta = _step_label_meta_lookup(metas.get("knob") or {}, param, "KNOB")
        groups = (meta or {}).get("groups") or []
        by_order: dict[str, list[dict]] = {}
        order_seq: list[str] = []
        for idx, group in enumerate(groups if isinstance(groups, list) else []):
            if not isinstance(group, dict):
                continue
            order = str(group.get("rule_order") or f"R{idx + 1}").strip() or f"R{idx + 1}"
            if order not in by_order:
                by_order[order] = []
                order_seq.append(order)
            by_order[order].append(group)
        id_blocks: list[str] = []
        desc_blocks: list[str] = []
        for order in order_seq:
            by_desc: dict[str, dict] = {}
            for group in by_order[order]:
                operator = _re.sub(r"[\s-]+", "_", str(group.get("operator") or "").strip().lower())
                if operator == "is_null" or (exclude_not_null and operator == "not_null"):
                    continue
                desc = str(group.get("step_desc") or group.get("func_step") or "").strip()
                if not desc:
                    continue
                bucket = by_desc.setdefault(desc.casefold(), {"desc": desc, "ids": [], "seen": set()})
                for sid in _step_label_group_ids(group):
                    if sid.casefold() not in bucket["seen"]:
                        bucket["seen"].add(sid.casefold())
                        bucket["ids"].append(sid)
            valid = [item for item in by_desc.values() if item["ids"]]
            if valid:
                id_blocks.append("\n&\n".join("\n".join(item["ids"]) for item in valid))
                desc_blocks.append("\n&\n".join(item["desc"] for item in valid))
        return {"step_id": "\n".join(id_blocks), "step_desc": "\n".join(desc_blocks)}

    meta_key = "inline" if kind == "inline_matching" else "vm"
    prefix = "INLINE" if kind == "inline_matching" else "VM"
    meta = _step_label_meta_lookup(metas.get(meta_key) or {}, param, prefix)
    ids: list[str] = []
    descs: list[str] = []
    seen_ids: set[str] = set()
    seen_descs: set[str] = set()
    fallback_desc = str((meta or {}).get("step_desc") or (meta or {}).get("function_step") or "").strip()
    for group in ((meta or {}).get("groups") or []):
        if not isinstance(group, dict):
            continue
        desc = str(group.get("step_desc") or group.get("function_step") or fallback_desc).strip()
        for sid in _step_label_group_ids(group):
            if sid.casefold() not in seen_ids:
                seen_ids.add(sid.casefold())
                ids.append(sid)
        if desc and desc.casefold() not in seen_descs:
            seen_descs.add(desc.casefold())
            descs.append(desc)
    if not ids:
        for sid in ((meta or {}).get("step_ids") or []):
            sid = str(sid or "").strip()
            if sid and sid.casefold() not in seen_ids:
                seen_ids.add(sid.casefold())
                ids.append(sid)
    if fallback_desc and fallback_desc.casefold() not in seen_descs:
        descs.append(fallback_desc)
    return {"step_id": "\n".join(ids), "step_desc": "\n".join(descs)}


def _build_step_process_columns(product: str, selected: list[str],
                                exclude_not_null: bool = True) -> dict[str, dict[str, str]]:
    metas = _step_label_metas(product)
    return {
        str(column): _step_process_columns_for_param(str(column), metas, exclude_not_null)
        for column in (selected or [])
    }


def _apply_step_label_columns(product: str, selected: list[str], col_rename: dict,
                              exclude_not_null: bool = True) -> tuple[list[str], dict]:
    """내보내기 항목 라벨을 적용 공정 표기로 바꾸고, 표시할 공정이 없는 매칭 행은 뺀다."""
    metas = _step_label_metas(product)
    kept: list[str] = []
    rename = dict(col_rename or {})
    for col in selected or []:
        kind, lines = _step_label_lines_for_param(col, metas, exclude_not_null)
        if not kind:
            kept.append(col)
            continue
        if not lines:
            continue
        rename[col] = "\n".join(lines)
        kept.append(col)
    return kept, rename


# ── 통합(병합) 표시 대상 ────────────────────────────────────────────────
# 옆칸과 같은 값을 하나로 묶는 표시는 split 조건 열에만 의미가 있다.
# INLINE/VM 은 wafer 별 실측값이고 TAG/관리 행은 자유 입력이라, 값이 우연히
# 같다고 묶으면 wafer 별 값이 몇 개인지 읽을 수 없게 된다.
MERGE_VIEW_PREFIXES = ("KNOB", "FAB", "MASK")


def _merge_view_allowed_param(param: str) -> bool:
    u = str(param or "").strip().upper()
    return any(u == p or u.startswith(f"{p}_") for p in MERGE_VIEW_PREFIXES)


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
    보안: admin 또는 page_manager('splittable') 만 실행 가능 (rulebook CSV 쓰기 보호)."""
    me = current_user(request)
    if not is_page_manager(me, "splittable"):
        raise HTTPException(403, "admin or splittable page manager only")
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
    rulebook_meta = _RULEBOOK_FILES["inline_matching" if kind == "inline" else "vm_matching"]
    existing = _load_csv_rows(csv_fp)
    sid_to_step_desc = {}
    if kind == "vm":
        for steps in _product_step_map_by_desc(product, base).values():
            for step in steps:
                sid = str(step.get("step_id") or "").strip()
                desc = str(step.get("step_desc") or "").strip()
                if sid and desc:
                    sid_to_step_desc.setdefault(sid.casefold(), desc)
    added = []
    for iid, sid in winners.items():
        iid = str(iid or "").strip()
        sid = str(sid or "").strip()
        if not iid:
            continue
        if kind == "inline":
            if (product, iid) in {(str(r.get("product") or "").strip(), str(r.get("item_id") or "").strip()) for r in existing}:
                continue
            existing.append({"product": product, "item_id": iid, "step_id": sid, "item_desc": ""})
            added.append((iid, sid))
        else:
            step_desc = sid_to_step_desc.get(sid.casefold(), "")
            if not step_desc:
                continue
            existing_key = (step_desc.casefold(), iid.casefold())
            if existing_key in {
                (str(r.get("step_desc") or r.get("function_step") or "").strip().casefold(),
                 str(r.get("item_id") or r.get("feature_name") or "").strip().casefold())
                for r in existing
            }:
                continue
            existing.append({"step_desc": step_desc, "item_id": iid})
            added.append((iid, step_desc))
    if not added:
        return {"ok": True, "added": 0, "total": len(winners), "note": "모두 기존에 등록됨"}
    try:
        final_rows, dedupe_rows = _matching_cache.dedupe_rows(
            existing,
            key_cols=[k for k in rulebook_meta.get("cols", []) if k],
            required_cols=rulebook_meta.get("required", []),
            strict_required=True,
        )
    except ValueError as e:
        raise HTTPException(400, f"validation failed: {e}")

    # write back — header = union of all keys
    import csv as _csv
    all_keys: list = []
    for r in final_rows:
        for k in r.keys():
            if k not in all_keys:
                all_keys.append(k)
    try:
        csv_fp.parent.mkdir(parents=True, exist_ok=True)
        with open(csv_fp, "w", encoding="utf-8", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=all_keys)
            w.writeheader()
            for r in final_rows:
                w.writerow({k: r.get(k, "") for k in all_keys})
        cache_result = _matching_cache.refresh_matching_csv(csv_fp)
        if not cache_result.get("ok", False):
            logger.warning("infer_step_mapping cache refresh failed: %s", cache_result)
    except Exception as e:
        raise HTTPException(500, f"csv write failed: {e}")
    return {
        "ok": True,
        "added": len(added),
        "deduped_rows": dedupe_rows,
        "total": len(winners),
        "cache_rows": cache_result.get("rows"),
        "csv": str(csv_fp.name),
        "sample_added": added[:10],
    }


# v8.8.10: Rulebook "컬럼 역할 → 실제 컬럼명" 매핑 저장소 (soft-landing).
#   사내 CSV 의 컬럼 이름이 기본값과 다를 때 admin 이 여기서 매핑만 바꾸면 _build_knob_meta /
#   _build_vm_meta / _build_inline_meta 가 그대로 동작. rulebook 파일 자체는 손대지 않음.
#
# v9.5.71: 이 파일에 있던 스키마 상수·로더·파일명 정리 함수의 **복사본을 제거**하고
#   `app_v2.modules.splittable.rulebook_repository` 한 곳으로 모았다. 같은 파일
#   (`{data_root}/splittable/rulebook_schema.json`)을 가리키는 정의가 두 벌이라
#   한쪽만 바꾸면 조용히 갈라졌고, 테스트에서도 한쪽만 격리돼 실제 운영 파일을
#   읽는 사고가 있었다. 이름은 기존 호출부 호환을 위해 얇은 위임으로 남긴다.
from app_v2.modules.splittable import rulebook_repository as _rulebook_repository
# pivot 빌드 청크 설정(캐시관리 톱니바퀴)에서 기본값/상한을 읽는다. 빌더 자체는
# 무거운 호출부에서 지연 import 하지만, 설정 payload 는 상수만 보므로 모듈 별칭으로
# 둔다 (cache_builder 는 routers 를 import 하지 않아 순환이 없다).
from app_v2.modules.splittable import cache_builder as _pivot_cache_builder

RULEBOOK_SCHEMA_FILE = _rulebook_repository.RULEBOOK_SCHEMA_FILE
_DEFAULT_RULEBOOK_SCHEMA = rulebook_repo.get_default_schema()


def _load_rulebook_schema() -> dict:
    return rulebook_repo.load_schema()


def _save_rulebook_schema(schema: dict) -> None:
    rulebook_repo.save_schema(schema)


def _sch(kind: str) -> dict:
    return rulebook_repo.get_sch(kind)


def _clean_rulebook_filename(value: object, default: str) -> str:
    return rulebook_repo.clean_rulebook_filename(value, default)


@router.get("/rulebook/schema")
def get_rulebook_schema():
    """현재 역할→컬럼명 매핑 + 기본값 같이 반환. FE 에서 diff 표시 가능."""
    return {"schema": rulebook_repo.load_schema(), "defaults": rulebook_repo.get_default_schema()}


class RulebookSchemaReq(BaseModel):
    kind: str
    mapping: dict
    username: str = ""


@router.post("/rulebook/schema/save")
def save_rulebook_schema(
    req: RulebookSchemaReq,
    request: Request,
    _perm=Depends(require_page_manager("splittable")),
):
    me = current_user(request)
    if req.kind not in rulebook_repo.get_default_schema():
        raise HTTPException(400, f"unknown rulebook: {req.kind}")
    cur = rulebook_repo.load_schema()
    defm = rulebook_repo.get_default_schema()[req.kind]
    new_map = {}
    for role, _dfl in defm.items():
        v = (req.mapping or {}).get(role, _dfl)
        if role == "file_name":
            v = rulebook_repo.clean_rulebook_filename(v, _dfl)
        else:
            v = str(v or "").strip() or _dfl
        new_map[role] = v
    cur[req.kind] = new_map
    rulebook_repo.save_schema(cur)
    _audit_user(req.username or (me.get("username") if isinstance(me, dict) else ""),
                "splittable:rulebook_schema_save",
                detail=f"kind={req.kind} mapping={new_map}")
    return {"ok": True, "kind": req.kind, "mapping": new_map}


# v8.8.7: Rulebook (knob_ppid.csv + Vehicle_matching.csv/step_matching.csv) admin 인라인 편집 CRUD.
#   admin 만 수정 가능. 저장 시 row 정규화 + 빈 행 제거 + 원자적 교체.
#   스키마는 _build_knob_meta 가 읽는 컬럼과 동일해야 함.
_RULEBOOK_FILES = {
    "knob_ppid": {
        "filename": "ppid_knob.csv",
        "legacy_filename": "knob_ppid.csv",
        "cols": ["feature_name", "rule_order", "step_desc", "operator", "value", "category"],
        "required": ["feature_name", "step_desc"],
    },
    "step_matching": {
        "filename": "Vehicle_matching.csv",
        "legacy_filename": "step_matching.csv",
        "cols": ["product", "step_id", "step_desc"],
        "required": ["product", "step_id", "step_desc"],
    },
    # v8.8.9: INLINE / VM 매칭도 동일 CRUD 로 관리.
    #   inline_matching.csv: (product, step_id, item_id, item_desc) — INLINE_<item_id> 측정 메타.
    "inline_matching": {
        "filename": "inline_matching.csv",
        "cols": ["product", "step_id", "item_id", "item_desc", "matching_table"],
        "required": ["product", "step_id", "item_id"],
    },
    #   vm_matching.csv: (step_desc, item_id) — VM_<step_desc>_<item_id>, step_id 는 Vehicle_matching.csv 에서 확장.
    "vm_matching": {
        "filename": "vm_matching.csv",
        "cols": ["step_desc", "item_id"],
        "required": ["step_desc", "item_id"],
    },
}


def _normalize_rulebook_rows(kind: str, rows: list[dict]) -> list[dict]:
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        r = dict(row)
        if kind in {"knob_ppid", "step_matching", "vm_matching"} and not str(r.get("step_desc") or "").strip():
            r["step_desc"] = _row_step_desc(r, _sch(kind))
        if kind == "knob_ppid" and not str(r.get("value") or "").strip():
            r["value"] = _first_row_value(r, _sch(kind).get("value_col", "value"), "ppid", "category")
        if kind == "vm_matching" and not str(r.get("item_id") or "").strip():
            r["item_id"] = _first_row_value(r, _sch(kind).get("item_id_col", "item_id"), "feature_name")
        out.append(r)
    return out


def _rulebook_path_for_base(kind: str, base: Path | None = None) -> Path:
    meta = _RULEBOOK_FILES.get(kind)
    if not meta:
        raise HTTPException(400, f"unknown rulebook: {kind}")
    root = base or _base_root()
    configured = _clean_rulebook_filename(_sch(kind).get("file_name"), meta["filename"])
    primary = root / configured
    if configured != meta["filename"] or primary.exists() or not meta.get("legacy_filename"):
        return primary
    legacy = root / str(meta.get("legacy_filename") or "")
    return legacy if legacy.exists() else primary


def _rulebook_path(kind: str) -> Path:
    return _rulebook_path_for_base(kind)


def _rulebook_row_matches_product(kind: str, row: dict, product: str, *, allow_common: bool = True) -> bool:
    p_col = _sch(kind).get("product_col", "product")
    row_product = (row or {}).get(p_col)
    if row_product is None and p_col != "product":
        row_product = (row or {}).get("product")
    if kind in {"step_matching", "inline_matching"}:
        return _step_matching_product_matches(product, row_product, allow_common=allow_common)
    return _product_value_matches(product, row_product, allow_common=allow_common)


@router.get("/rulebook")
def get_rulebook(kind: str = Query("knob_ppid"), product: str = Query("")):
    """v8.8.7: rulebook CSV 를 JSON 으로 반환.

    KNOB and VM item rows are product-common. Step/INLINE matching rows remain product-scoped.
    """
    return rulebook_service.get_rulebook(kind, product)


class RulebookSaveReq(BaseModel):
    kind: str               # "knob_ppid" | "step_matching" | "inline_matching" | "vm_matching"
    rows: List[dict]        # 전체 대체 (혹은 product 스코프 대체)
    product: str = ""       # 주어지면 해당 제품 rows 만 대체, 빈값이면 파일 전체 대체
    username: str = ""


@router.post("/rulebook/save")
def save_rulebook(req: RulebookSaveReq, request: Request, _perm=Depends(require_page_manager("splittable"))):
    """Admin 또는 splittable page manager 전용. product 스코프면 해당 제품 행만 교체."""
    me = current_user(request)
    username = req.username or (me.get("username") if isinstance(me, dict) else "")
    return rulebook_service.save_rulebook(req.kind, req.rows, req.product, username)


@router.get("/knob-meta")
def knob_meta(product: str = Query("")):
    """v8.4.7: KNOB feature_name → step_desc(step_id) 역산 맵.

    응답 스키마:
      {
        "features": {
          "KNOB_GATE_PPID": {
            "groups": [
              {"step_desc":"GATE_PATTERN","step_ids":["AA200030","AA200040","AA200050"],
               "value":"PP_GATE_01","operator":"+","rule_order":"R1","category":"gate"},
              {"step_desc":"PC_ETCH","step_ids":["AA200100","AA200110"],
               "value":"PP_PC_01","operator":"","rule_order":"R2","category":"gate"}
            ],
            "label": "GATE_PATTERN (AA200030/AA200040/AA200050) + PC_ETCH (AA200100/AA200110)"
          },
          ...
        }
      }
    ppid_knob.csv는 product 없는 공용 룰북으로 읽고, product별 step_id 확장만 Vehicle_matching.csv에서 적용한다.
    """
    return {"features": _build_knob_meta(product)}


class PrefixSaveReq(BaseModel):
    prefixes: List[str]


@router.post("/prefixes/save")
def save_prefixes(req: PrefixSaveReq, _perm=Depends(require_page_manager("splittable"))):
    save_json(PREFIX_CFG, req.prefixes)
    return {"ok": True}


# ── Cell decimal precision (v8.1.1) ──
# Per-prefix decimal places for numeric cell display. Only INLINE/VM default;
# any prefix key can be added here. Admin-configurable.
