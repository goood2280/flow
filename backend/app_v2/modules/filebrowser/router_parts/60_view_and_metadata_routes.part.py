class FileBrowserViewCancelReq(BaseModel):
    query_session: str
    query_id: str = ""


@router.post("/view/cancel")
def cancel_view_query(req: FileBrowserViewCancelReq, request: Request):
    me = current_user(request)
    session_id = str(req.query_session or "").strip()[:120]
    if not session_id:
        raise HTTPException(400, "query_session is required")
    result = _sql_queue.cancel(
        username=str(me.get("username") or ""),
        session_id=session_id,
        query_id=str(req.query_id or "").strip()[:120],
        reason="page_left",
    )
    result["queue"] = _sql_queue.snapshot()
    return result


@router.get("/root-parquets")
def root_parquets():
    """List root-level data files.
    v8.7.6 정책 변경: DB 루트의 단일 parquet 도 Base 로 분류 권장. 이 엔드포인트는
    하위호환용으로만 유지하며 빈 배열을 반환해 UI 에서 별도 섹션이 사라지도록 한다.
    (/api/filebrowser/base-files 가 db_root 의 단일 parquet 을 통합 노출한다.)"""
    return {"files": []}


def _resolve_data_file_for_schema(file: str, settings: dict | None = None) -> Path | None:
    name = str(file or "").strip()
    if not name:
        return None
    rel = Path(name)
    if rel.is_absolute() or ".." in rel.parts:
        raise HTTPException(400, "Invalid file path")
    settings = settings or _load_filebrowser_settings()
    single_file_folders = _single_file_folder_names(settings)
    base_root = _base_root()
    db_root = _db_root()
    if rel.parts and str(rel.parts[0]).casefold() in single_file_folders:
        return _resolve_single_file_folder_data_path(name, (base_root, db_root), single_file_folders)
    if rel.parts and rel.parts[0] == "uploads" and len(rel.parts) == 2:
        cand = (PATHS.upload_dir / rel.parts[1]).resolve()
        try:
            cand.relative_to(PATHS.upload_dir.resolve())
        except ValueError:
            raise HTTPException(400, "Invalid uploads path")
        return cand if cand.is_file() else None
    if rel.parts and rel.parts[0] == "reformatter" and len(rel.parts) == 2:
        product_name = Path(rel.parts[1]).stem
        rf_root = (PATHS.data_root / "reformatter").resolve()
        for suffix in (Path(rel.parts[1]).suffix.lower(), ".csv", ".json"):
            if not suffix:
                continue
            cand = (rf_root / f"{product_name}{suffix}").resolve()
            try:
                cand.relative_to(rf_root)
            except ValueError:
                continue
            if cand.is_file():
                return cand
        return None
    if rel.parts and rel.parts[0] == "product_config" and len(rel.parts) == 2:
        pc_root = (PATHS.data_root / "product_config").resolve()
        cand = (pc_root / rel.parts[1]).resolve()
        try:
            cand.relative_to(pc_root)
        except ValueError:
            raise HTTPException(400, "Invalid product config path")
        return cand if cand.is_file() else None
    for candidate_root in (base_root, db_root):
        if not candidate_root.is_dir():
            continue
        cand = (candidate_root / rel).resolve()
        try:
            cand.relative_to(candidate_root.resolve())
        except ValueError:
            continue
        if cand.is_file():
            return cand
    return None


def _schema_for_data_file(fp: Path) -> dict[str, str]:
    if not fp or not fp.is_file():
        return {}
    if fp.suffix.lower() == ".parquet":
        try:
            from core.parquet_perf import read_meta
            cached = read_meta(fp) or {}
            schema = cached.get("schema") or {}
            if schema:
                return {str(k): str(v) for k, v in schema.items()}
        except Exception:
            pass
    lf = scan_one_file(fp)
    if lf is None:
        return {}
    schema_obj = lf.collect_schema()
    return {n: str(schema_obj[n]) for n in schema_obj.names()}


def _schema_for_product_source(root: str, product: str) -> tuple[dict[str, str], int]:
    if str(root or "").strip().upper() == YIELD_SHOT_ROOT:
        from core import yield_map as _yield_map
        frame = _yield_map.shot_yield_frame(product)
        return {str(name): str(dtype) for name, dtype in frame.schema.items()}, 0
    if str(root or "").strip().upper() == "SPLITTABLE":
        files = source_data_files(root=root, product=product)
        if not files:
            return {}, 0
        try:
            columns, schema = duckdb_engine.inspect_files(files)
            source_size = sum(path.stat().st_size for path in files if path.is_file())
            return {str(column): str(schema.get(column, "")) for column in columns}, source_size
        except Exception:
            return {}, 0
    prod_dir = _resolve_product_dir_fast(root, product)
    if prod_dir is None:
        return {}, 0
    fp = _first_data_file(prod_dir, (".parquet",)) or _first_data_file(prod_dir, (".csv",))
    if fp is None:
        return {}, 0
    try:
        size = fp.stat().st_size
    except Exception:
        size = 0
    return _schema_for_data_file(fp), size


@router.get("/columns/search")
def search_columns(request: Request, root: str = Query(""), product: str = Query(""),
                   file: str = Query(""), q: str = Query(""),
                   limit: int = Query(200, ge=1, le=500),
                   offset: int = Query(0, ge=0), access_scope: str = Query("")):
    if file:
        _require_base_file_access(request, file, access_scope)
    else:
        _require_filebrowser_user(request)
    settings = _load_filebrowser_settings()
    schema: dict[str, str] = {}
    source_size = 0
    if file:
        fp = _resolve_data_file_for_schema(file, settings)
        if fp is None:
            raise HTTPException(404, f"File not found: {file}")
        schema = _schema_for_data_file(fp)
        try:
            source_size = fp.stat().st_size
        except Exception:
            source_size = 0
    elif root and product:
        schema, source_size = _schema_for_product_source(root, product)
    else:
        raise HTTPException(400, "Specify file or root+product")
    columns = list(schema.keys())
    needle = str(q or "").strip().casefold()
    matches = [c for c in columns if not needle or needle in c.casefold()]
    try:
        limit = int(limit)
    except Exception:
        limit = DEFAULT_SCHEMA_COLUMN_PAGE_SIZE
    try:
        offset = max(0, int(offset))
    except Exception:
        offset = 0
    limit = min(limit, _settings_schema_column_page_size(settings))
    page = matches[offset:offset + limit]
    return {
        "ok": True,
        "columns": page,
        "dtypes": {c: schema.get(c, "") for c in page},
        "query": q,
        "offset": offset,
        "limit": limit,
        "matched": len(matches),
        "total_cols": len(columns),
        "has_more": offset + len(page) < len(matches),
        "source_size": source_size,
    }


@router.get("/parquet-meta")
def parquet_meta(request: Request, root: str = Query(""), product: str = Query(""),
                 file: str = Query("")):
    """v8.8.33: parquet 파일의 row_count / schema 를 즉답.
    .meta.json 사이드카 캐시가 있으면 scan 없이 반환, 없으면 1회 계산 후 기록.
    30~60GB 스케일에서 FileBrowser 클릭 반응성을 위해 스키마-최초 호출에 사용.
    v8.8.33 보안: 세션 토큰 필수. file 파라미터는 디렉터리 traversal 방어.
    """
    from core.auth import current_user
    from core.parquet_perf import get_or_compute_meta
    _ = current_user(request)
    # file 파라미터 사전 정규화 — ".." 제거
    if file:
        from pathlib import Path as _P
        safe_parts = [p for p in _P(file).parts if p not in ("..", ".")]
        file = str(_P(*safe_parts)) if safe_parts else ""
    db_root = _db_root()
    base_root = _base_root()
    if file and not product:
        # DB 루트 단일 파일 또는 Base 파일
        candidates = [db_root / file, base_root / file]
    elif root and product:
        prod_path = db_root / root / product
        if not prod_path.is_dir():
            raise HTTPException(404, f"Not found: {root}/{product}")
        pq_files = sorted(prod_path.rglob("*.parquet"))
        if not pq_files:
            raise HTTPException(404, "No parquet files")
        # 디렉토리 기반 — 대표 파일(가장 최근)의 meta + 파일 수 요약
        rep = pq_files[-1]
        meta = get_or_compute_meta(rep)
        total = 0
        files_meta = []
        for f in pq_files[-30:]:  # 최근 30개 파일만 샘플링
            m = get_or_compute_meta(f)
            files_meta.append({"name": f.name, "rows": m.get("row_count", 0),
                               "size_bytes": m.get("size_bytes")})
            total += int(m.get("row_count") or 0)
        return {
            "schema": meta.get("schema"),
            "rep_file": rep.name,
            "files_sampled": len(files_meta),
            "files_meta": files_meta,
            "total_rows_sampled": total,
            "total_files": len(pq_files),
        }
    else:
        raise HTTPException(400, "specify (root,product) or file")

    for fp in candidates:
        try:
            fp_resolved = fp.resolve()
            if fp_resolved.is_file() and fp_resolved.suffix == ".parquet":
                return get_or_compute_meta(fp_resolved)
        except Exception:
            continue
    raise HTTPException(404, f"parquet not found: {file}")


@router.post("/parquet-meta/invalidate")
def parquet_meta_invalidate(request: Request, root: str = Query(""), product: str = Query(""),
                            file: str = Query("")):
    """v8.8.33: meta 사이드카 강제 재계산. admin 전용."""
    from core.auth import current_user
    from core.parquet_perf import invalidate_meta
    me = current_user(request)
    if me.get("role") != "admin":
        raise HTTPException(403, "admin only")
    db_root = _db_root()
    count = 0
    if file and not product:
        fp = (db_root / file).resolve()
        if fp.is_file():
            if invalidate_meta(fp):
                count += 1
    elif root and product:
        prod_path = db_root / root / product
        if prod_path.is_dir():
            for f in prod_path.rglob("*.parquet"):
                if invalidate_meta(f):
                    count += 1
    return {"invalidated": count}


@router.get("/root-parquet-view")
def view_root_parquet(file: str = Query(...), sql: str = Query(""),
                      rows: int = Query(LATEST_PREVIEW_ROWS), cols: int = Query(10),
                      select_cols: str = Query(""),
                      sort_column: str = Query(""),
                      sort_direction: str = Query("asc"),
                      sort_nulls: str = Query("last"),
                      agg_func: str = Query(""),
                      agg_column: str = Query(""),
                      agg_group_by: str = Query(""),
                      meta_only: bool = Query(True),
                      engine: str = Query("auto"),
                      page: int = Query(0, ge=0),
                      page_size: int = Query(LATEST_PREVIEW_ROWS, ge=1, le=1000),
                      request: Request = None):
    # 활동 대시보드: 실제 데이터 조회만 기록 (스키마 로드/페이지 넘김 제외).
    if not meta_only and page == 0:
        from core.audit import record as _fb_audit
        _fb_audit(request, "filebrowser:view",
                  detail=f"target={file} cols={select_cols or 'all'} sql={sql.strip()}",
                  tab="filebrowser")
    # v8.4.6: path traversal 방어 — db_root 밖 파일 접근 차단
    db_root = _db_root()
    fp = (db_root / file).resolve()
    try:
        fp.relative_to(db_root.resolve())
    except ValueError:
        raise HTTPException(400, "Path escapes DB root")
    if not fp.is_file():
        raise HTTPException(404)
    try:
        settings = _load_filebrowser_settings()
        sort_spec = _view_sort_query(sort_column, sort_direction, sort_nulls)
        aggregate_spec = _view_aggregate_query(agg_func, agg_column, agg_group_by)
        cols = _preview_cols_limit(cols or _settings_preview_max_columns(settings))
        page, page_size, _offset = _preview_page_args(rows, page_size)
        rows = page_size

        def _compute() -> dict:
            # v8.4.3 OOM-aware: lazy scan — full read 회피. 10GB+ parquet 도 안전.
            lf = scan_one_file(fp)
            if lf is None:
                raise HTTPException(400, f"Cannot read: {file}")
            full_schema_obj = lf.collect_schema()
            all_cols_full = list(full_schema_obj.names())
            schema_full = {n: str(full_schema_obj[n]) for n in all_cols_full}
            # v8.8.16: meta_only 빠른 경로.
            if meta_only:
                try:
                    from core.parquet_perf import read_meta
                    cached_meta_local = read_meta(fp)
                except Exception:
                    cached_meta_local = None
                return _finalize_preview_response({
                    "all_columns": all_cols_full, "total_cols": len(all_cols_full),
                    "columns": all_cols_full[:cols], "dtypes": schema_full,
                    "data": [], "showing": 0, "showing_cols": [],
                    "total_rows": int((cached_meta_local or {}).get("row_count") or 0),
                    "meta_only": True,
                    "page": page, "page_size": page_size, "has_more": False,
                    "meta_cached": bool(cached_meta_local),
                    "row_count_unknown": not bool(cached_meta_local),
                    "source_path": str(fp),
                    "source_size": fp.stat().st_size,
                    "source_modified": fp.stat().st_mtime,
                }, settings)
            try:
                from core.parquet_perf import read_meta
                cached_meta = read_meta(fp)
            except Exception:
                cached_meta = None
            if not aggregate_spec and duckdb_engine.should_use_duckdb([fp], engine=engine, sql=sql, select_cols=select_cols):
                try:
                    return _finalize_preview_response(_run_view_duckdb(
                        [fp], sql, select_cols, rows,
                        page=page, page_size=page_size, cached_meta=cached_meta,
                        preview_cols=cols,
                        settings=settings,
                        sort_spec=sort_spec,
                    ), settings)
                except Exception as e:
                    if str(engine or "").lower() in {"duckdb", "on", "true", "1"}:
                        raise HTTPException(400, f"DuckDB query failed: {e}")
                    logger.warning("duckdb root-parquet-view fallback file=%s: %s", file, e)
            resp = _run_view_lazy(
                lf, sql, select_cols, rows,
                page=page, page_size=page_size, cached_meta=cached_meta,
                preview_cols=cols,
                source_size=fp.stat().st_size,
                settings=settings,
                sort_spec=sort_spec,
                aggregate_spec=aggregate_spec,
            )
            resp["all_columns"] = all_cols_full
            resp["total_cols"] = len(all_cols_full)
            resp["dtypes"] = schema_full
            return _finalize_preview_response(resp, settings)

        if _fbcache.is_enabled(settings):
            source_stat = _fbcache.stat_for_file(fp)
            if source_stat is not None:
                sql_str = sql if isinstance(sql, str) else ""
                sc_str = select_cols if isinstance(select_cols, str) else ""
                key_payload = {
                    "sql_norm": sql_str.strip(),
                    "select_cols_norm": ",".join(sorted(c.strip() for c in sc_str.split(",") if c.strip())),
                    "sort_column": _cache_safe_text(sort_column, 120).casefold(),
                    "sort_direction": _cache_safe_text(sort_direction, 20).casefold(),
                    "sort_nulls": _cache_safe_text(sort_nulls, 20).casefold(),
                    "agg_func": _cache_safe_text(agg_func, 40).casefold(),
                    "agg_column": _cache_safe_text(agg_column, 120).casefold(),
                    "agg_group_by": ",".join(sorted(c.casefold() for c in _clean_string_list(agg_group_by))),
                    "meta_only": bool(meta_only),
                    "page": int(page),
                    "page_size": int(page_size),
                    "preview_cols": int(cols),
                    "settings_sig": _fbcache.settings_signature(settings),
                }
                return _fbcache.get_or_compute(
                    endpoint="root-parquet-view", source=source_stat,
                    key_payload=key_payload, compute=_compute,
                )
        return _compute()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Error: {str(e)}")


def _chart_builder_radius_layout(product: str) -> dict:
    root = _db_root()
    path = next((candidate for candidate in root.iterdir()
                 if candidate.is_file() and candidate.name.casefold() == "chip_radius.csv"), None)
    if path is None:
        raise HTTPException(404, f"Chip_Radius.csv 파일을 찾을 수 없습니다: {root}")
    try:
        frame = pl.read_csv(path, infer_schema_length=2000, encoding="utf8-lossy")
    except Exception as exc:
        raise HTTPException(400, f"Chip_Radius.csv 읽기 실패: {exc}") from exc
    lookup = {str(column).strip().casefold(): column for column in frame.columns}
    mask_col = lookup.get("mask") or lookup.get("vehicle")
    x_col = lookup.get("chip_x_adj") or lookup.get("chip_x")
    y_col = lookup.get("chip_y_adj") or lookup.get("chip_y")
    radius_col = lookup.get("chip_radius") or lookup.get("radius")
    if not all((mask_col, x_col, y_col, radius_col)):
        raise HTTPException(400, "Chip_Radius.csv 필수 열이 없습니다: Mask, chip_x_adj, chip_y_adj, Chip_Radius")

    def product_key(value: str) -> str:
        normalized = re.sub(r"[^A-Z0-9]", "", str(value or "").upper())
        return re.sub(r"^(?:MLTABLE|VEHICLE|VH)", "", normalized)

    masks = frame.select(pl.col(mask_col).cast(pl.String, strict=False)).drop_nulls().unique().to_series().to_list()
    wanted = product_key(product)
    matched_mask = next((mask for mask in masks if product_key(mask) == wanted), None)
    if matched_mask is None:
        raise HTTPException(404, f"Chip_Radius.csv에 {product} 제품 Mask가 없습니다")
    layout = (
        frame.filter(pl.col(mask_col).cast(pl.String, strict=False) == str(matched_mask))
        .select([
            pl.col(x_col).cast(pl.Float64, strict=False).alias("shot_x"),
            pl.col(y_col).cast(pl.Float64, strict=False).alias("shot_y"),
            pl.col(radius_col).cast(pl.Float64, strict=False).alias("radius"),
        ])
        .drop_nulls()
        .group_by(["shot_x", "shot_y"])
        .agg(pl.col("radius").median())
        .sort(["shot_y", "shot_x"])
    )
    if layout.is_empty():
        raise HTTPException(404, f"Chip_Radius.csv의 {matched_mask} shot 정보가 비어 있습니다")
    geometry = {}
    try:
        from core.teg_map import fit_geometry_diagnosed
        fitted, _ = fit_geometry_diagnosed(layout["shot_x"], layout["shot_y"], layout["radius"])
        if fitted:
            geometry = {key: round(float(value), 8) for key, value in fitted.items()}
    except Exception:
        geometry = {}
    return {
        "ok": True,
        "product": str(product),
        "mask": str(matched_mask),
        "file": path.name,
        "rows": serialize_rows(layout.to_dicts()),
        "row_count": layout.height,
        "geometry": geometry,
    }


def _chart_builder_history_path() -> Path:
    return PATHS.data_root / CHART_BUILDER_HISTORY_FILE


def _chart_builder_name_base(value: dict | ChartBuilderRunReq) -> str:
    """Return an engineer-facing base name for a saved chart."""
    if isinstance(value, dict):
        requested = value.get("name") or value.get("chart_name") or ""
        chart = value.get("chart") if isinstance(value.get("chart"), dict) else {}
        sources = value.get("sources") if isinstance(value.get("sources"), list) else []
    else:
        requested = value.chart_name
        chart = value.chart if isinstance(value.chart, dict) else {}
        sources = [_chart_builder_model_dict(source) for source in (value.sources or [])]
    requested = _cache_safe_text(requested, 120)
    if requested:
        return requested
    axes = [str(chart.get(key) or "").strip() for key in ("x", "y")]
    axes = [axis for axis in axes if axis]
    if axes:
        return _cache_safe_text(" × ".join(axes), 120)
    product = next((str(source.get("product") or "").strip() for source in sources if isinstance(source, dict) and source.get("product")), "")
    return _cache_safe_text(f"{product} chart" if product else "Chart", 120)


def _chart_builder_unique_name(base: str, used: set[str]) -> str:
    clean = _cache_safe_text(base, 120) or "Chart"
    candidate = clean
    number = 2
    while candidate.casefold() in used:
        suffix = f" ({number})"
        candidate = f"{clean[:max(1, 120 - len(suffix))]}{suffix}"
        number += 1
    return candidate


def _chart_builder_history_entries() -> list[dict]:
    """Return chronological history with unique names, including legacy rows."""
    entries = jsonl_read(
        _chart_builder_history_path(),
        limit=1000,
        filter_fn=lambda entry: isinstance(entry, dict) and entry.get("event") == "history",
    )
    used = {
        str(entry.get("history_id") or "").strip().casefold()
        for entry in entries
        if entry.get("history_id")
    }
    normalized = []
    for entry in entries:
        row = dict(entry)
        name = _chart_builder_unique_name(_chart_builder_name_base(row), used)
        row["name"] = name
        used.add(name.casefold())
        normalized.append(row)
    return normalized


def _chart_builder_model_dict(value) -> dict:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return dict(value) if isinstance(value, dict) else {}


def _chart_builder_resolve_root_name(value: str) -> str:
    """Accept both physical DB directory names and engineer-facing aliases."""
    requested = str(value or "").strip()
    if not requested:
        return requested
    db_root = _db_root()
    direct = db_root / requested
    if direct.is_dir():
        return requested
    try:
        from core.domain import canonical_name
        settings = _load_filebrowser_settings()
        folded = requested.casefold()
        for child in db_root.iterdir():
            if not child.is_dir() or _is_filebrowser_hidden_dir_name(child.name):
                continue
            aliases = {child.name, canonical_name(child.name), _db_display_name(child.name, settings)}
            if any(str(alias or "").strip().casefold() == folded for alias in aliases):
                return child.name
    except Exception:
        pass
    return requested


def _chart_builder_runtime_values(values) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    raw_values = values if isinstance(values, (list, tuple, set)) else re.split(r"[,\n]+", str(values or ""))
    for raw in raw_values:
        value = str(raw or "").strip()[:160]
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            out.append(value)
        if len(out) >= 200:
            break
    return out


def _chart_builder_runtime_column(columns: list[str], requested: str) -> str:
    folded = str(requested or "").strip().casefold()
    return next((column for column in columns if str(column).casefold() == folded), "")


def _chart_builder_runtime_where(
    columns: list[str], source: ChartBuilderSourceReq, source_id: str, warnings: list[str],
) -> str:
    clauses: list[str] = []
    for requested, values, label in (
        ("root_lot_id", source.runtime_root_lot_ids, "Root Lot"),
        ("wafer_id", source.runtime_wafer_ids, "Wafer"),
    ):
        clean = _chart_builder_runtime_values(values)
        if not clean:
            continue
        column = _chart_builder_runtime_column(columns, requested)
        if not column:
            warnings.append(f"{source_id}: {label} 공통 필터 열({requested})이 없어 이 Query에는 적용하지 않았습니다.")
            continue
        quoted = duckdb_engine.quote_ident(column)
        normalized = f"UPPER(TRIM(CAST({quoted} AS VARCHAR)))"
        if requested == "wafer_id":
            for pattern in ("^#\\s*", "^WAFER\\s*", "^WF\\s*", "^W\\s*"):
                normalized = f"REGEXP_REPLACE({normalized}, '{pattern}', '')"
            clean = [re.sub(r"^(?:#|WAFER|WF|W)\s*", "", value, flags=re.I) for value in clean]
            clean = [value for value in clean if value]
            if not clean:
                continue
        literals = ", ".join(duckdb_engine.sql_literal(value.upper()) for value in clean)
        clauses.append(f"{normalized} IN ({literals})")
    return " AND ".join(f"({clause})" for clause in clauses)


def _chart_builder_filter_frame(
    frame: pl.DataFrame, source: ChartBuilderSourceReq, source_id: str, warnings: list[str],
) -> pl.DataFrame:
    out = frame
    columns = list(frame.columns)
    runtime_days = max(0, min(3650, int(source.runtime_recent_days or 0)))
    if runtime_days:
        requested_date_column = str(source.runtime_date_column or "tkout_time").strip()
        date_column = _chart_builder_runtime_column(columns, requested_date_column)
        if date_column:
            # cast(pl.Datetime)은 timezone-naive이므로 비교 literal도 naive로 맞춘다.
            cutoff = datetime.datetime.now() - datetime.timedelta(days=runtime_days)
            parsed_time = pl.col(date_column).cast(pl.String, strict=False).str.to_datetime(strict=False)
            out = out.filter(parsed_time >= cutoff)
        else:
            warnings.append(f"{source_id}: 최근 {runtime_days}일 필터 열({requested_date_column})이 없어 원래 조건으로 조회했습니다.")
    for requested, values, label in (
        ("root_lot_id", source.runtime_root_lot_ids, "Root Lot"),
        ("wafer_id", source.runtime_wafer_ids, "Wafer"),
    ):
        clean = _chart_builder_runtime_values(values)
        if not clean:
            continue
        column = _chart_builder_runtime_column(columns, requested)
        if not column:
            warnings.append(f"{source_id}: {label} 공통 필터 열({requested})이 없어 이 Query에는 적용하지 않았습니다.")
            continue
        expr = pl.col(column).cast(pl.String, strict=False).str.strip_chars().str.to_uppercase()
        if requested == "wafer_id":
            expr = expr.str.replace(r"^(?:#|WAFER|WF|W)\s*", "")
            clean = [re.sub(r"^(?:#|WAFER|WF|W)\s*", "", value, flags=re.I) for value in clean]
            clean = [value for value in clean if value]
            if not clean:
                continue
        out = out.filter(expr.is_in([value.upper() for value in clean]))
    return out


def _chart_builder_yield_shot_frame(
    source: ChartBuilderSourceReq, *, max_rows: int,
) -> tuple[pl.DataFrame, str, list[str], dict]:
    """Read the Yield Map full-shot virtual table and apply Chart Builder SQL."""
    from core import yield_map as _yield_map

    frame = _yield_map.shot_yield_frame(source.product)
    runtime_warnings: list[str] = []
    frame = _chart_builder_filter_frame(frame, source, str(source.id or "query"), runtime_warnings)
    columns = list(frame.columns)
    where_sql, selected_text, sort_spec = _merge_display_sql_into_args(
        source.sql, source.select_cols, {}, columns
    )
    normalized = _validate_where_expression(where_sql, columns)
    active_sort, _ = _resolve_view_sort_spec(sort_spec, columns)
    view = _run_view(
        frame, source.sql, source.select_cols,
        rows=max_rows + 1, page_size=max_rows + 1,
        preview_cols=max(1, len(columns)),
    )
    shown_rows = view.get("data") if isinstance(view.get("data"), list) else []
    shown_columns = [str(column) for column in (view.get("columns") or [])]
    shown = pl.DataFrame(shown_rows) if shown_rows else pl.DataFrame(
        schema={column: frame.schema.get(column, pl.String) for column in shown_columns}
    )
    selected = [column.strip() for column in str(selected_text or "").split(",") if column.strip() in set(columns)]
    if not selected:
        selected = shown_columns
    display_sql = _build_ai_sql_display_sql(selected, normalized, active_sort)
    return shown, display_sql, runtime_warnings, {
        "virtual_source": True,
        "grain": "full_shot",
        "yield_definition": "good_die / expected_die × 100",
        "runtime_root_lot_ids": _chart_builder_runtime_values(source.runtime_root_lot_ids),
        "runtime_wafer_ids": _chart_builder_runtime_values(source.runtime_wafer_ids),
    }


def _chart_builder_reformatter_frame(
    source: ChartBuilderSourceReq,
    *,
    max_rows: int,
    user: dict,
) -> tuple[pl.DataFrame, str, list[str], dict]:
    """Run the same REAL/ADDP engine as ET 다운로드, then apply ChartBuilder SQL."""
    if "ET" not in str(source.root or "").upper():
        raise HTTPException(400, "ET reformatter는 ET DB Query에서만 사용할 수 있습니다.")
    from routers import reformatize as et_reformatize

    requested_items = [item.strip() for item in str(source.reformatter_items or "").split(",") if item.strip()]
    if not requested_items:
        try:
            csv_path = et_reformatize._find_csv(source.product)
            table = et_reformatize.load_vehicle_table(csv_path) if csv_path else []
            aliases = {str(row.get("alias") or "") for row in table}
            _where, selected_from_sql = _parse_ai_sql_select_prefix(source.sql, None)
            requested_items = [column for column in selected_from_sql if column in aliases]
        except Exception:
            requested_items = []

    request_limit = min(5000, max_rows + 1)
    reformat_request = et_reformatize.RunReq(
        product=source.product,
        items=requested_items,
        offset=0,
        limit=request_limit,
        days=max(0, min(3650, int(source.runtime_recent_days or 0))),
        lot_filter=",".join(_chart_builder_runtime_values(source.runtime_root_lot_ids)),
        wafer_filter=",".join(_chart_builder_runtime_values(source.runtime_wafer_ids)),
    )
    reformatted = et_reformatize.run(reformat_request, user=user)
    rows = reformatted.get("rows") if isinstance(reformatted.get("rows"), list) else []
    columns = [str(column) for column in (reformatted.get("columns") or [])]
    df = pl.DataFrame(rows) if rows else pl.DataFrame(schema={column: pl.String for column in columns})
    runtime_warnings: list[str] = []
    df = _chart_builder_filter_frame(df, source, str(source.id or "query"), runtime_warnings)
    where_sql, selected_text, sort_spec = _merge_display_sql_into_args(
        source.sql, source.select_cols, {}, columns
    )
    normalized = _validate_where_expression(where_sql, columns)
    active_sort, _ = _resolve_view_sort_spec(sort_spec, columns)
    view = _run_view(
        df,
        source.sql,
        source.select_cols,
        rows=request_limit,
        page_size=request_limit,
        preview_cols=max(1, len(columns)),
    )
    shown_rows = view.get("data") if isinstance(view.get("data"), list) else []
    shown_columns = [str(column) for column in (view.get("columns") or [])]
    shown = pl.DataFrame(shown_rows) if shown_rows else pl.DataFrame(schema={column: pl.String for column in shown_columns})
    selected = [column.strip() for column in str(selected_text or "").split(",") if column.strip() in set(columns)]
    if not selected:
        selected = shown_columns
    display_sql = _build_ai_sql_display_sql(selected, normalized, active_sort)
    warnings = [*runtime_warnings, *[str(item) for item in (reformatted.get("rule_errors") or []) if str(item).strip()]]
    if reformatted.get("notice"):
        warnings.append(str(reformatted["notice"]))
    if int(reformatted.get("total_rows") or shown.height) > request_limit:
        warnings.append(f"ET reformatter 결과는 화면 안전 한도 {request_limit:,}행까지만 사용했습니다.")
    meta = {
        "vehicle_csv": reformatted.get("vehicle_csv") or "",
        "reformatter_items": requested_items,
        "index_columns": reformatted.get("index_columns") or [],
        "selected_text": ",".join(selected),
        "total_rows": int(view.get("total_rows") or shown.height),
        "runtime_root_lot_ids": _chart_builder_runtime_values(source.runtime_root_lot_ids),
        "runtime_wafer_ids": _chart_builder_runtime_values(source.runtime_wafer_ids),
    }
    return shown, display_sql, warnings, meta


def _record_chart_builder_history(*, username: str, req: ChartBuilderRunReq, result: dict) -> dict:
    sources = [_chart_builder_model_dict(source) for source in (req.sources or [])]
    joins = [_chart_builder_model_dict(join) for join in (req.joins or [])]
    canonical_code = format_chart_builder_definition(sources, joins, req.max_rows, req.chart or {})
    source_results = result.get("sources") if isinstance(result.get("sources"), list) else []
    joined = result.get("joined") if isinstance(result.get("joined"), dict) else {}
    history_id = f"chart_hist_{uuid.uuid4().hex[:12]}"
    with _CHART_BUILDER_HISTORY_LOCK:
        existing = _chart_builder_history_entries()
        used = {
            str(value).casefold()
            for row in existing
            for value in (row.get("history_id"), row.get("name"))
            if value
        }
        used.add(history_id.casefold())
        chart_name = _chart_builder_unique_name(_chart_builder_name_base(req), used)
        entry = {
            "event": "history",
            "history_id": history_id,
            "name": chart_name,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "username": _cache_safe_text(username, 80) or "anonymous",
            "definition_code": canonical_code[:100_000],
            "query_ids": [str(source.get("id") or "") for source in sources],
            "source_count": len(sources),
            "join_count": len(joins),
            "sources": [
                {
                    "id": str(source.get("id") or ""),
                    "root": str(source.get("root") or ""),
                    "product": str(source.get("product") or ""),
                    "row_count": int(source.get("row_count") or 0),
                }
                for source in source_results[:10]
                if isinstance(source, dict)
            ],
            "joins": joins[:20],
            "chart": req.chart if isinstance(req.chart, dict) else {},
            "max_rows": max(1, min(10000, int(req.max_rows or 10000))),
            "row_count": int(joined.get("row_count") or 0),
            "warnings": [str(item)[:500] for item in (result.get("warnings") or [])[:20]],
        }
        jsonl_append(_chart_builder_history_path(), entry, max_lines=1000)
        try:
            jsonl_trim(_chart_builder_history_path(), 1000)
        except Exception:
            pass
    return entry


_CHART_ASSISTANT_TYPES = {
    "scatter", "line", "box", "bar", "bar_horizontal", "pie", "donut", "radius", "wafer_map",
}
_CHART_ASSISTANT_FIELDS = {
    "type", "x", "y", "color", "trellis", "width", "height", "highlight", "color_rules", "color_else",
}
_CHART_ASSISTANT_JOIN_FIELDS = {"left", "right", "left_on", "right_on", "how"}
_CHART_ASSISTANT_JOIN_HOWS = {"left", "inner", "full", "semi", "anti"}
_CHART_ASSISTANT_COLORS = {
    "빨간색": "red", "빨강": "red", "red": "red",
    "파란색": "blue", "파랑": "blue", "blue": "blue",
    "초록색": "green", "초록": "green", "green": "green",
    "주황색": "orange", "주황": "orange", "orange": "orange",
    "보라색": "purple", "보라": "purple", "purple": "purple",
    "회색": "gray", "gray": "gray", "grey": "gray",
    "검정색": "black", "검정": "black", "black": "black",
}


def _chart_assistant_column(value, columns: list[str]) -> str:
    requested = str(value or "").strip()
    if not requested:
        return ""
    by_fold = {str(column).casefold(): str(column) for column in columns if str(column).strip()}
    return by_fold.get(requested.casefold(), "")


def _chart_assistant_deterministic_operations(
    instruction: str, current: dict, columns: list[str],
) -> list[dict]:
    """Handle frequent visual tweaks without making a network LLM call.

    These commands should remain available when the optional company LLM is
    unavailable. More involved changes fall through to the structured LLM
    planner below and are still validated by the same patch contract.
    """
    prompt = str(instruction or "").strip()
    folded = prompt.casefold()
    chart = current.get("chart") if isinstance(current.get("chart"), dict) else {}
    operations: list[dict] = []

    def set_chart(field: str, value) -> None:
        operations.append({"scope": "chart", "field": field, "value": value})

    def explicit_size(labels: tuple[str, ...]) -> int | None:
        joined = "|".join(re.escape(label) for label in labels)
        match = re.search(rf"(?:{joined})\s*(?:를|을|은|는|=|:)?\s*(\d{{3,4}})\s*(?:px|픽셀)?", prompt, re.IGNORECASE)
        return int(match.group(1)) if match else None

    width = explicit_size(("width", "넓이", "가로"))
    height = explicit_size(("height", "높이", "세로"))
    grow = any(word in folded for word in ("키워", "늘려", "크게", "확대"))
    shrink = any(word in folded for word in ("줄여", "작게", "축소"))
    size_context = any(word in folded for word in ("차트", "그래프", "width", "height", "넓이", "높이", "가로", "세로"))
    if width is not None:
        set_chart("width", width)
    elif size_context and (grow or shrink) and any(word in folded for word in ("width", "넓이", "가로")):
        base = int(chart.get("width") or 1100)
        set_chart("width", round(base * (1.2 if grow else 0.8)))
    if height is not None:
        set_chart("height", height)
    elif size_context and (grow or shrink) and any(word in folded for word in ("height", "높이", "세로")):
        base = int(chart.get("height") or 600)
        set_chart("height", round(base * (1.2 if grow else 0.8)))
    if size_context and (grow or shrink) and width is None and height is None and not any(
        word in folded for word in ("width", "height", "넓이", "높이", "가로", "세로")
    ):
        set_chart("width", round(int(chart.get("width") or 1100) * (1.2 if grow else 0.8)))
        set_chart("height", round(int(chart.get("height") or 600) * (1.2 if grow else 0.8)))

    mentioned_columns = [column for column in columns if str(column).casefold() in folded]
    if "trellis" in folded or "트렐리스" in folded:
        if any(word in folded for word in ("없애", "해제", "끄기", "빼줘")):
            set_chart("trellis", "")
        elif len(mentioned_columns) == 1:
            set_chart("trellis", mentioned_columns[0])

    color_context = any(word in folded for word in ("color", "컬러", "색상", "색으로"))
    if color_context:
        named_color = next((value for label, value in _CHART_ASSISTANT_COLORS.items() if label in folded), "")
        hex_match = re.search(r"#[0-9a-fA-F]{6}\b", prompt)
        if hex_match or named_color:
            set_chart("color", "custom")
            set_chart("color_rules", [])
            set_chart("color_else", hex_match.group(0) if hex_match else named_color)
        elif any(word in folded for word in ("없애", "해제", "끄기", "빼줘")):
            set_chart("color", "")
        elif len(mentioned_columns) == 1:
            set_chart("color", mentioned_columns[0])

    join_context = "join" in folded or "조인" in folded
    if join_context:
        # ASCII JOIN 키워드 뒤에 한글 조사(또는 레거시 mojibake)가 바로 붙어도
        # Python의 Unicode \b가 이를 한 단어로 보지 않도록 ASCII 경계를 쓴다.
        how = next((value for value in _CHART_ASSISTANT_JOIN_HOWS if re.search(
            rf"(?<![A-Za-z0-9_]){value}(?![A-Za-z0-9_])", folded,
        )), "")
        if how and current.get("joins"):
            operations.append({"scope": "join", "index": 0, "field": "how", "value": how})
    return operations


def _chart_assistant_llm_operations(
    instruction: str, current: dict, columns: list[str],
) -> tuple[list[dict], str, dict]:
    llm_info = {"available": False, "used": False, "error": ""}
    try:
        from core import llm_adapter

        llm_info["available"] = bool(llm_adapter.is_available())
        if not llm_info["available"]:
            return [], "", llm_info
        system = """You edit an existing Flow ChartBuilder definition by returning a minimal patch.
Never rewrite unrelated settings and never invent a column, query id, or join.
Allowed operations:
- {scope:'chart', field:type|x|y|color|trellis|width|height|highlight|color_rules|color_else, value:any}
- {scope:'join', index:zero-based integer, field:left|right|left_on|right_on|how, value:any}
If the request is ambiguous, return no operations and ask one short clarification in message.
For a solid color set chart color='custom', color_rules=[], and color_else to the requested CSS color.
JOIN how must be left, inner, full, semi, or anti. Use only supplied columns and query ids."""
        payload = {
            "instruction": str(instruction or "")[:2000],
            "current_definition": {
                "sources": current.get("sources") or [],
                "joins": current.get("joins") or [],
                "chart": current.get("chart") or {},
                "max_rows": current.get("max_rows") or 10000,
            },
            "available_columns": columns[:200],
            "response": {"message": "short Korean response", "operations": []},
        }
        out = llm_adapter.complete_json(
            json.dumps(payload, ensure_ascii=False),
            system=system,
            timeout=30,
            max_retries=1,
            schema={
                "keys": ["message", "operations"],
                "required": ["message", "operations"],
                "properties": {"message": {}, "operations": {}},
            },
        )
        llm_info["used"] = bool(out.get("ok"))
        llm_info["error"] = str(out.get("error") or "")
        obj = out.get("obj") if isinstance(out.get("obj"), dict) else {}
        operations = obj.get("operations") if isinstance(obj.get("operations"), list) else []
        return operations[:20], _cache_safe_text(obj.get("message"), 300), llm_info
    except Exception as exc:
        llm_info["error"] = f"{type(exc).__name__}: {exc}"
        return [], "", llm_info


def _chart_assistant_apply_operations(
    current: dict, operations: list[dict], columns: list[str],
) -> tuple[dict, list[dict], list[str], bool]:
    updated = copy.deepcopy(current)
    updated.setdefault("chart", {})
    updated.setdefault("joins", [])
    sources = updated.get("sources") if isinstance(updated.get("sources"), list) else []
    query_ids = {str(source.get("id") or "") for source in sources if isinstance(source, dict)}
    applied: list[dict] = []
    warnings: list[str] = []
    requires_rerun = False

    for raw in operations[:20]:
        if not isinstance(raw, dict):
            continue
        scope = str(raw.get("scope") or "").strip().lower()
        field = str(raw.get("field") or "").strip().lower()
        value = raw.get("value")
        if scope == "chart" and field in _CHART_ASSISTANT_FIELDS:
            old = updated["chart"].get(field, "")
            if field == "type":
                value = str(value or "").strip().lower()
                if value not in _CHART_ASSISTANT_TYPES:
                    warnings.append(f"지원하지 않는 차트 종류: {value}")
                    continue
            elif field in {"x", "y", "trellis"}:
                requested = str(value or "").strip()
                value = _chart_assistant_column(requested, columns) if requested else ""
                if requested and columns and not value:
                    warnings.append(f"결과에 없는 열은 {field.upper()}로 지정하지 않았습니다: {requested}")
                    continue
            elif field == "color":
                requested = str(value or "").strip()
                if requested.casefold() in {"custom", "__custom__"}:
                    value = "custom"
                elif requested:
                    value = _chart_assistant_column(requested, columns)
                    if columns and not value:
                        warnings.append(f"결과에 없는 열은 COLOR로 지정하지 않았습니다: {requested}")
                        continue
                else:
                    value = ""
            elif field == "width":
                try:
                    value = max(320, min(2400, int(float(value))))
                except Exception:
                    warnings.append("Width는 320~2400 숫자로 입력해 주세요.")
                    continue
            elif field == "height":
                try:
                    value = max(240, min(1600, int(float(value))))
                except Exception:
                    warnings.append("Height는 240~1600 숫자로 입력해 주세요.")
                    continue
            elif field == "highlight":
                value = value if isinstance(value, bool) else str(value or "").strip().lower() in {"1", "true", "yes", "on"}
            elif field == "color_rules":
                if not isinstance(value, list):
                    warnings.append("색상 규칙은 목록 형식이어야 합니다.")
                    continue
                value = [_cache_safe_text(item, 500) for item in value[:20] if _cache_safe_text(item, 500)]
            elif field == "color_else":
                value = _cache_safe_text(value, 40)
                if not value:
                    warnings.append("기본 색상이 비어 있어 반영하지 않았습니다.")
                    continue
            if old != value:
                updated["chart"][field] = value
                applied.append({"scope": "chart", "field": field, "from": old, "to": value})
            continue

        if scope == "join" and field in _CHART_ASSISTANT_JOIN_FIELDS:
            try:
                index = int(raw.get("index", 0))
            except Exception:
                index = -1
            if index < 0 or index >= len(updated["joins"]):
                warnings.append("수정할 JOIN 번호를 찾지 못했습니다.")
                continue
            join = updated["joins"][index]
            old = join.get(field, "")
            if field == "how":
                value = str(value or "").strip().lower()
                if value not in _CHART_ASSISTANT_JOIN_HOWS:
                    warnings.append(f"지원하지 않는 JOIN 방식: {value}")
                    continue
            elif field in {"left", "right"}:
                value = str(value or "").strip()
                if value not in query_ids:
                    warnings.append(f"없는 Query ID는 JOIN에 사용할 수 없습니다: {value}")
                    continue
            else:
                value = ", ".join(part.strip() for part in str(value or "").split(",") if part.strip())
                if not value or not all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", part.strip()) for part in value.split(",")):
                    warnings.append("JOIN key는 쉼표로 구분한 열 이름이어야 합니다.")
                    continue
            if old != value:
                join[field] = value
                applied.append({"scope": "join", "index": index, "field": field, "from": old, "to": value})
                requires_rerun = True
            continue
        warnings.append(f"허용되지 않은 Assistant 변경은 건너뛰었습니다: {scope}.{field}")

    canonical = format_chart_builder_definition(
        updated.get("sources") or [],
        updated.get("joins") or [],
        updated.get("max_rows") or 10000,
        updated.get("chart") or {},
    )
    # The canonical parser is the final contract check before the patch leaves
    # the server. This catches malformed custom color rules and JOIN syntax.
    validated = parse_chart_builder_definition(canonical)
    return validated, applied, warnings, requires_rerun


def _chart_builder_assistant_plan(req: ChartBuilderAssistantReq) -> dict:
    instruction = _cache_safe_text(req.instruction, 2000)
    if not instruction:
        raise HTTPException(400, "Assistant에게 수정할 내용을 입력해 주세요.")
    try:
        current = parse_chart_builder_definition(req.definition_code)
    except ChartBuilderDefinitionError as exc:
        raise HTTPException(400, f"현재 차트 코드를 먼저 확인해 주세요: {exc}") from exc
    columns = []
    seen_columns: set[str] = set()
    for raw in req.columns or []:
        column = _cache_safe_text(raw, 160)
        if column and column.casefold() not in seen_columns:
            seen_columns.add(column.casefold())
            columns.append(column)

    operations = _chart_assistant_deterministic_operations(instruction, current, columns)
    message = ""
    llm_info = {"available": False, "used": False, "error": ""}
    if not operations:
        operations, message, llm_info = _chart_assistant_llm_operations(instruction, current, columns)
    updated, changes, warnings, requires_rerun = _chart_assistant_apply_operations(current, operations, columns)
    if changes:
        summary = ", ".join(
            f"{change['scope']}.{change['field']} → {change['to']}" for change in changes[:6]
        )
        message = message or f"요청대로 {summary}로 바꿨습니다."
    elif not message:
        message = "수정 대상을 정확히 찾지 못했습니다. 예: ‘높이 720’, ‘wafer_id로 trellis’, ‘첫 JOIN을 inner로’처럼 알려 주세요."
    return {
        "ok": True,
        "changed": bool(changes),
        "message": message,
        "changes": changes,
        "warnings": warnings,
        "requires_rerun": requires_rerun,
        "execution_target": "operating_api",
        "llm": llm_info,
        **updated,
    }


@router.post("/chart-builder/assistant")
def chart_builder_assistant(req: ChartBuilderAssistantReq, request: Request):
    """Apply a validated, minimal natural-language patch on the operating API."""
    _require_filebrowser_user(request)
    return _chart_builder_assistant_plan(req)
