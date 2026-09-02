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


_CHART_BUILDER_INLINE_VIRTUAL_SCHEMA = {
    "shot_x": "Float64 · TEG Inline map",
    "shot_y": "Float64 · TEG Inline map",
    "inline_map_name": "String · TEG Inline map",
    "inline_vehicle": "String · TEG product",
}


def _chart_builder_is_inline_root(root: str) -> bool:
    """True only for the raw INLINE DB, never for SplitTable INLINE columns."""
    token = re.sub(r"[^A-Z0-9]+", "_", str(root or "").strip().upper()).strip("_")
    return bool(token and "INLINE" in token and token not in {"SPLITTABLE", "YIELD_SHOT"})


def _chart_builder_inline_assist(root: str, product: str) -> dict:
    if not _chart_builder_is_inline_root(root):
        return {}
    try:
        rules = inline_coordinates.load_matching_rules(
            PATHS.base_root,
            products=[product] if str(product or "").strip() else (),
        )
    except Exception:
        rules = []
    return {
        "kind": "inline",
        "virtual_columns": list(_CHART_BUILDER_INLINE_VIRTUAL_SCHEMA),
        "recommended_columns": [
            "root_lot_id", "wafer_id", "step_id", "item_id", "subitem_id", "value",
            "shot_x", "shot_y",
        ],
        "inline_maps": [
            {
                "step_id": str(rule.get("step_id") or ""),
                "item_id": str(rule.get("item_id") or ""),
                "map_name": str(rule.get("matching_table") or ""),
                "vehicle": str(rule.get("vehicle") or ""),
                "available": bool(rule.get("available")),
                "shot_count": int(rule.get("shot_count") or 0),
            }
            for rule in rules[:100]
        ],
    }


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
    assist = _chart_builder_inline_assist(root, product) if root and product and not file else {}
    virtual_schema = {
        name: dtype for name, dtype in _CHART_BUILDER_INLINE_VIRTUAL_SCHEMA.items()
        if name not in schema
    } if assist else {}
    completion_schema = {**schema, **virtual_schema}
    columns = list(completion_schema.keys())
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
        "dtypes": {c: completion_schema.get(c, "") for c in page},
        "virtual_columns": [c for c in page if c in virtual_schema],
        "assist": assist,
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
@_track_filebrowser_sql_execution("rootpq")
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
                      reuse_history_id: str = Query(""),
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
    def teg_layout() -> dict:
        from core import teg_map as _teg_map
        payload = _teg_map.map_payload(product)
        shots = _teg_map.full_shots_for_payload(payload)
        rows = []
        seen = set()
        for shot in shots:
            try:
                x = float(shot.get("x"))
                y = float(shot.get("y"))
                radius = float(shot.get("radius", shot.get("r")))
            except (TypeError, ValueError):
                continue
            key = (round(x, 6), round(y, 6))
            if key in seen or not all(math.isfinite(value) for value in (x, y, radius)):
                continue
            seen.add(key)
            rows.append({"shot_x": key[0], "shot_y": key[1], "radius": round(radius, 8)})
        if not rows:
            raise LookupError(f"TEG 위치조회에 {product} shot geometry가 없습니다")
        rows.sort(key=lambda row: (row["shot_y"], row["shot_x"]))
        return {
            "ok": True,
            "product": str(product),
            "mask": str(payload.get("vehicle") or product),
            "file": "TEG 위치조회",
            "rows": rows,
            "row_count": len(rows),
            "geometry": payload.get("geometry") or {},
            "geometry_source": "teg_map",
        }

    root = _db_root()
    path = next((candidate for candidate in root.iterdir()
                 if candidate.is_file() and candidate.name.casefold() == "chip_radius.csv"), None)
    if path is None:
        try:
            return teg_layout()
        except Exception as exc:
            raise HTTPException(404, f"Chip_Radius.csv 또는 TEG 위치조회 geometry를 찾을 수 없습니다: {exc}") from exc
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
        try:
            return teg_layout()
        except Exception as exc:
            raise HTTPException(404, f"Chip_Radius.csv와 TEG 위치조회에 {product} 제품 geometry가 없습니다") from exc
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
        try:
            return teg_layout()
        except Exception as exc:
            raise HTTPException(404, f"Chip_Radius.csv의 {matched_mask} shot 정보가 비어 있습니다") from exc
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
        "geometry_source": "chip_radius",
    }


def _chart_builder_history_path() -> Path:
    return PATHS.data_root / CHART_BUILDER_HISTORY_FILE


def _chart_builder_pins_path() -> Path:
    # history path를 테스트/운영에서 다른 root로 바꾸면 pin registry도 반드시
    # 같은 root를 따라가야 서로 다른 환경의 고정 상태가 섞이지 않는다.
    return _chart_builder_history_path().with_name(CHART_BUILDER_PINS_FILE)


def _chart_builder_pins() -> dict[str, dict]:
    raw = load_json(_chart_builder_pins_path(), {}) or {}
    values = raw.get("pins") if isinstance(raw, dict) else {}
    if not isinstance(values, dict):
        return {}
    pins: dict[str, dict] = {}
    for raw_id, raw_meta in values.items():
        history_id = _cache_safe_text(raw_id, 120)
        if not history_id or not isinstance(raw_meta, dict):
            continue
        pins[history_id] = {
            "pinned_at": _cache_safe_text(raw_meta.get("pinned_at"), 80),
            "pinned_by": _cache_safe_text(raw_meta.get("pinned_by"), 80),
        }
    return pins


def _save_chart_builder_pins(pins: dict[str, dict]) -> None:
    save_json(_chart_builder_pins_path(), {"version": 1, "pins": pins})


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
    """Return chronological history with unique names and server-owned pin metadata."""
    entries = jsonl_read(
        _chart_builder_history_path(),
        limit=0,
        filter_fn=lambda entry: isinstance(entry, dict) and entry.get("event") == "history",
    )
    pins = _chart_builder_pins()
    used = {
        str(entry.get("history_id") or "").strip().casefold()
        for entry in entries
        if entry.get("history_id")
    }
    normalized = []
    for entry in entries:
        row = dict(entry)
        try:
            row["reuse_count"] = max(0, int(row.get("reuse_count") or 0))
        except (TypeError, ValueError):
            row["reuse_count"] = 0
        name = _chart_builder_unique_name(_chart_builder_name_base(row), used)
        row["name"] = name
        history_id = str(row.get("history_id") or "")
        pin = pins.get(history_id) or {}
        row.update({
            "pinned": bool(pin),
            "pinned_at": str(pin.get("pinned_at") or ""),
            "pinned_by": str(pin.get("pinned_by") or ""),
        })
        used.add(name.casefold())
        normalized.append(row)
    return normalized


def _chart_builder_visible_history_entries(*, recent_limit: int = CHART_BUILDER_VISIBLE_RECENT) -> list[dict]:
    """Pinned rows first, followed by newest unpinned rows.

    Pinned rows do not consume the rolling recent allowance.  This is shared by
    ChartBuilder and Template Report so both screens expose the same catalog.
    """
    entries = _chart_builder_history_entries()
    pinned = [entry for entry in entries if entry.get("pinned")]
    pinned.sort(
        key=lambda entry: str(entry.get("pinned_at") or entry.get("timestamp") or ""),
        reverse=True,
    )
    recent = [entry for entry in entries if not entry.get("pinned")]
    recent = list(reversed(recent[-max(1, min(CHART_BUILDER_VISIBLE_RECENT, int(recent_limit or CHART_BUILDER_VISIBLE_RECENT))):]))
    return [*pinned, *recent]


def _set_chart_builder_pin(
    history_id: str,
    *,
    pinned: bool,
    username: str,
) -> dict:
    chart_id = _cache_safe_text(history_id, 120)
    if not chart_id:
        raise ValueError("Chart ID가 비어 있습니다.")
    if not any(str(entry.get("history_id") or "") == chart_id for entry in _chart_builder_history_entries()):
        raise KeyError(chart_id)
    with _CHART_BUILDER_PIN_LOCK:
        pins = _chart_builder_pins()
        current = pins.get(chart_id) or {}
        if not pinned:
            pins.pop(chart_id, None)
        else:
            pins[chart_id] = {
                "pinned_at": str(current.get("pinned_at") or datetime.datetime.now(datetime.timezone.utc).isoformat()),
                "pinned_by": _cache_safe_text(current.get("pinned_by") or username, 80) or "system",
            }
        _save_chart_builder_pins(pins)
    return next(entry for entry in _chart_builder_history_entries() if str(entry.get("history_id") or "") == chart_id)


def _trim_chart_builder_history() -> None:
    """Retain every pinned chart plus a bounded rolling unpinned history."""
    entries = jsonl_read(
        _chart_builder_history_path(),
        limit=0,
        filter_fn=lambda entry: isinstance(entry, dict) and entry.get("event") == "history",
    )
    if not entries:
        return
    pinned_ids = set(_chart_builder_pins())
    unpinned_indexes = [
        index for index, entry in enumerate(entries)
        if str(entry.get("history_id") or "") not in pinned_ids
    ]
    keep_indexes = set(unpinned_indexes[-CHART_BUILDER_RETAIN_RECENT:])
    keep_indexes.update(
        index for index, entry in enumerate(entries)
        if str(entry.get("history_id") or "") in pinned_ids
    )
    if len(keep_indexes) == len(entries):
        return
    kept = [entry for index, entry in enumerate(entries) if index in keep_indexes]
    atomic_write_text(
        _chart_builder_history_path(),
        "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in kept),
    )


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


def _chart_builder_runtime_pairs(values) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in (values if isinstance(values, (list, tuple)) else []):
        if not isinstance(raw, dict):
            continue
        root = str(raw.get("root_lot_id") or "").strip()[:160]
        wafer = str(raw.get("wafer_id") or "").strip()[:160]
        if not root or not wafer:
            continue
        wafer = re.sub(r"^(?:#|WAFER|WF|W)\s*", "", wafer, flags=re.I)
        key = (root.casefold(), wafer.casefold())
        if wafer and key not in seen:
            seen.add(key)
            out.append({"root_lot_id": root, "wafer_id": wafer})
        if len(out) >= 200:
            break
    return out


def _chart_builder_derived_specs(values) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for raw in (values if isinstance(values, (list, tuple)) else []):
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()[:80]
        key = name.casefold()
        columns = [str(column).strip()[:120] for column in (raw.get("columns") or []) if str(column).strip()]
        separator = str(raw.get("separator") if raw.get("separator") is not None else "_")[:8]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) and columns and len(columns) <= 12 and key not in seen:
            seen.add(key)
            out.append({"name": name, "columns": columns, "separator": separator})
        if len(out) >= 20:
            break
    return out


def _chart_builder_filter_specs(values) -> list[dict]:
    out: list[dict] = []
    allowed = {"in", "not_in", "equals", "not_equals", "contains", "not_contains", "is_blank", "not_blank"}
    aliases = {"=": "equals", "==": "equals", "eq": "equals", "!=": "not_equals", "ne": "not_equals", "notin": "not_in", "blank": "is_blank", "is_not_blank": "not_blank"}
    for raw in (values if isinstance(values, (list, tuple)) else []):
        if not isinstance(raw, dict):
            continue
        column = str(raw.get("column") or "").strip()[:120]
        operator = re.sub(r"[\s-]+", "_", str(raw.get("operator") or "in").strip().casefold())
        operator = aliases.get(operator, operator)
        clean: list[str] = []
        seen: set[str] = set()
        raw_values = raw.get("values") or []
        if isinstance(raw_values, str):
            raw_values = re.split(r"[,\n]+", raw_values)
        for value in raw_values:
            item = str(value or "").strip()[:160]
            key = item.casefold()
            if item and key not in seen:
                seen.add(key)
                clean.append(item)
            if len(clean) >= 200:
                break
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", column) and operator in allowed and (clean or operator in {"is_blank", "not_blank"}):
            out.append({"column": column, "operator": operator, "values": clean})
        if len(out) >= 50:
            break
    return out


def _chart_builder_runtime_required_columns(source: ChartBuilderSourceReq) -> list[str]:
    required: list[str] = []
    derived = _chart_builder_derived_specs(source.derived_columns)
    derived_names = {item["name"].casefold() for item in derived}
    for item in derived:
        for column in item["columns"]:
            if column.casefold() not in derived_names and column.casefold() not in {value.casefold() for value in required}:
                required.append(column)
    for item in _chart_builder_filter_specs(source.runtime_filters):
        column = item["column"]
        if column.casefold() not in derived_names and column.casefold() not in {value.casefold() for value in required}:
            required.append(column)
    return required


def _chart_builder_filter_sql_expression(columns: list[str], source: ChartBuilderSourceReq, requested: str) -> str:
    direct = _chart_builder_runtime_column(columns, requested)
    if direct:
        return f"CAST({duckdb_engine.quote_ident(direct)} AS VARCHAR)"
    available = {str(column).casefold(): str(column) for column in columns}
    derived_expressions: dict[str, str] = {}
    for item in _chart_builder_derived_specs(source.derived_columns):
        expressions: list[str] = []
        valid = True
        for requested_column in item["columns"]:
            raw_column = available.get(requested_column.casefold())
            nested = derived_expressions.get(requested_column.casefold())
            if raw_column:
                expressions.append(f"COALESCE(CAST({duckdb_engine.quote_ident(raw_column)} AS VARCHAR), '')")
            elif nested:
                expressions.append(f"COALESCE(({nested}), '')")
            else:
                valid = False
                break
        if valid:
            separator = duckdb_engine.sql_literal(item["separator"])
            derived_expressions[item["name"].casefold()] = f"CONCAT_WS({separator}, {', '.join(expressions)})"
    return derived_expressions.get(str(requested or "").casefold(), "")


def _chart_builder_generic_filter_where(
    columns: list[str], source: ChartBuilderSourceReq, source_id: str, warnings: list[str],
) -> str:
    clauses: list[str] = []
    for item in _chart_builder_filter_specs(source.runtime_filters):
        expression = _chart_builder_filter_sql_expression(columns, source, item["column"])
        if not expression:
            warnings.append(f"{source_id}: 필터 열({item['column']})이 없어 적용하지 않았습니다.")
            continue
        normalized = f"UPPER(TRIM(COALESCE(({expression}), '')))"
        values = [value.upper() for value in item["values"]]
        operator = item["operator"]
        if operator in {"in", "equals", "not_in", "not_equals"}:
            literals = ", ".join(duckdb_engine.sql_literal(value) for value in values)
            clause = f"{normalized} IN ({literals})"
            if operator in {"not_in", "not_equals"}:
                clause = f"NOT ({clause})"
        elif operator in {"contains", "not_contains"}:
            pieces = [f"STRPOS({normalized}, {duckdb_engine.sql_literal(value)}) > 0" for value in values]
            clause = f"({' OR '.join(pieces)})"
            if operator == "not_contains":
                clause = f"NOT ({clause})"
        elif operator == "is_blank":
            clause = f"{normalized} = ''"
        else:
            clause = f"{normalized} <> ''"
        clauses.append(clause)
    return " AND ".join(f"({clause})" for clause in clauses)


def _chart_builder_runtime_column(columns: list[str], requested: str) -> str:
    folded = str(requested or "").strip().casefold()
    return next((column for column in columns if str(column).casefold() == folded), "")


def _chart_builder_runtime_where(
    columns: list[str], source: ChartBuilderSourceReq, source_id: str, warnings: list[str],
) -> str:
    clauses: list[str] = []
    pairs = _chart_builder_runtime_pairs(source.runtime_lot_wafer_pairs)
    pairs_applied = False
    if pairs:
        root_column = _chart_builder_runtime_column(columns, "root_lot_id")
        wafer_column = _chart_builder_runtime_column(columns, "wafer_id")
        if root_column and wafer_column:
            root_expr = f"UPPER(TRIM(CAST({duckdb_engine.quote_ident(root_column)} AS VARCHAR)))"
            wafer_expr = f"UPPER(TRIM(CAST({duckdb_engine.quote_ident(wafer_column)} AS VARCHAR)))"
            for pattern in ("^#\\s*", "^WAFER\\s*", "^WF\\s*", "^W\\s*"):
                wafer_expr = f"REGEXP_REPLACE({wafer_expr}, '{pattern}', '')"
            pair_clauses = [
                f"({root_expr} = {duckdb_engine.sql_literal(pair['root_lot_id'].upper())} "
                f"AND {wafer_expr} = {duckdb_engine.sql_literal(pair['wafer_id'].upper())})"
                for pair in pairs
            ]
            clauses.append(f"({' OR '.join(pair_clauses)})")
            pairs_applied = True
        if not pairs_applied:
            missing = ", ".join(name for name, column in (("root_lot_id", root_column), ("wafer_id", wafer_column)) if not column)
            warnings.append(f"{source_id}: 연동표 조합 필터 열({missing})이 없어 Root Lot/Wafer 개별 필터로 적용했습니다.")
    if not pairs_applied:
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
    generic = _chart_builder_generic_filter_where(columns, source, source_id, warnings)
    if generic:
        clauses.append(generic)
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
    pairs = _chart_builder_runtime_pairs(source.runtime_lot_wafer_pairs)
    pairs_applied = False
    if pairs:
        root_column = _chart_builder_runtime_column(columns, "root_lot_id")
        wafer_column = _chart_builder_runtime_column(columns, "wafer_id")
        if root_column and wafer_column:
            root_expr = pl.col(root_column).cast(pl.String, strict=False).str.strip_chars().str.to_uppercase()
            wafer_expr = pl.col(wafer_column).cast(pl.String, strict=False).str.strip_chars().str.to_uppercase().str.replace(r"^(?:#|WAFER|WF|W)\s*", "")
            pair_filter = None
            for pair in pairs:
                clause = (root_expr == pair["root_lot_id"].upper()) & (wafer_expr == pair["wafer_id"].upper())
                pair_filter = clause if pair_filter is None else pair_filter | clause
            if pair_filter is not None:
                out = out.filter(pair_filter)
                pairs_applied = True
        else:
            missing = ", ".join(name for name, column in (("root_lot_id", root_column), ("wafer_id", wafer_column)) if not column)
            warnings.append(f"{source_id}: 연동표 조합 필터 열({missing})이 없어 Root Lot/Wafer 개별 필터로 적용했습니다.")
    if not pairs_applied:
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


def _chart_builder_apply_derived_filters(
    frame: pl.DataFrame, source: ChartBuilderSourceReq, source_id: str, warnings: list[str],
) -> pl.DataFrame:
    """파생열을 만든 뒤 같은 규칙으로 필터한다. Reformatter/가상 DB도 이 경로를 공유한다."""
    out = frame
    for item in _chart_builder_derived_specs(source.derived_columns):
        actual: list[str] = []
        for requested in item["columns"]:
            column = _chart_builder_runtime_column(list(out.columns), requested)
            if not column:
                warnings.append(f"{source_id}: 파생열 {item['name']}의 원본 열({requested})이 없어 만들지 않았습니다.")
                actual = []
                break
            actual.append(column)
        if not actual:
            continue
        expression = pl.concat_str(
            [pl.col(column).cast(pl.String, strict=False).fill_null("") for column in actual],
            separator=item["separator"],
        ).alias(item["name"])
        out = out.with_columns(expression)

    for item in _chart_builder_filter_specs(source.runtime_filters):
        column = _chart_builder_runtime_column(list(out.columns), item["column"])
        if not column:
            warnings.append(f"{source_id}: 필터 열({item['column']})이 없어 적용하지 않았습니다.")
            continue
        expression = pl.col(column).cast(pl.String, strict=False).fill_null("").str.strip_chars().str.to_uppercase()
        values = [value.upper() for value in item["values"]]
        operator = item["operator"]
        if operator in {"in", "equals", "not_in", "not_equals"}:
            condition = expression.is_in(values)
            if operator in {"not_in", "not_equals"}:
                condition = ~condition
        elif operator in {"contains", "not_contains"}:
            condition = None
            for value in values:
                piece = expression.str.contains(value, literal=True)
                condition = piece if condition is None else condition | piece
            if condition is None:
                continue
            if operator == "not_contains":
                condition = ~condition
        elif operator == "is_blank":
            condition = expression == ""
        else:
            condition = expression != ""
        out = out.filter(condition)
    return out


def _chart_builder_inline_required_columns(columns: list[str]) -> list[str]:
    """Raw columns required for authoritative TEG Inline-map enrichment."""
    required: list[str] = []
    for aliases in (
        ("step_id", "process_id"),
        ("item_id", "rawitem_id", "item"),
        ("subitem_id", "shot_id"),
    ):
        column = next((_chart_builder_runtime_column(columns, alias) for alias in aliases
                       if _chart_builder_runtime_column(columns, alias)), "")
        if column and column not in required:
            required.append(column)
    return required


def _chart_builder_safe_rename(frame: pl.DataFrame, source: str, wanted: str) -> tuple[pl.DataFrame, str]:
    if source not in frame.columns:
        return frame, ""
    candidate = wanted
    suffix = 2
    while candidate in frame.columns and candidate != source:
        candidate = f"{wanted}_{suffix}"
        suffix += 1
    return (frame if candidate == source else frame.rename({source: candidate})), candidate


def _chart_builder_attach_inline_coordinates(
    frame: pl.DataFrame,
    source: ChartBuilderSourceReq,
    source_id: str,
    warnings: list[str],
) -> tuple[pl.DataFrame, dict]:
    """Attach TEG Inline-map shot coordinates to a raw INLINE query result.

    The rulebook selects the map with product/step/item and the map table owns
    subitem_id -> shot coordinates. Raw INLINE shot columns are kept only as
    audit evidence; the new shot_x/shot_y columns are always the TEG mapping.
    """
    if not _chart_builder_is_inline_root(source.root):
        return frame, {}
    meta = {
        "inline_coordinate_mapping": {
            "configured": False,
            "applied": False,
            "matched_rows": 0,
            "unmatched_rows": int(frame.height),
            "ambiguous_keys": 0,
            "map_names": [],
            "vehicles": [],
        }
    }
    status = meta["inline_coordinate_mapping"]
    try:
        rules = inline_coordinates.load_matching_rules(
            PATHS.base_root,
            products=[source.product] if str(source.product or "").strip() else (),
        )
        mapping = inline_coordinates.load_coordinate_mapping(
            PATHS.base_root,
            products=[source.product] if str(source.product or "").strip() else (),
        )
    except Exception as exc:
        warnings.append(f"{source_id}: TEG Inline map 설정을 읽지 못했습니다: {exc}")
        return frame, meta

    status["configured"] = bool(mapping.get("configured"))
    status["map_names"] = list(mapping.get("configured_tables") or [])
    status["missing_maps"] = list(mapping.get("missing_tables") or [])
    if not status["configured"]:
        warnings.append(
            f"{source_id}: Inline shot 매칭 규칙이 없어 원본 결과만 표시합니다. "
            "inline_shot_matching.csv에서 제품·STEP·ITEM을 TEG Inline map에 연결해 주세요."
        )
        return frame, meta

    columns = list(frame.columns)
    step_col = next((_chart_builder_runtime_column(columns, name) for name in ("step_id", "process_id")
                     if _chart_builder_runtime_column(columns, name)), "")
    item_col = next((_chart_builder_runtime_column(columns, name) for name in ("item_id", "rawitem_id", "item")
                     if _chart_builder_runtime_column(columns, name)), "")
    subitem_col = next((_chart_builder_runtime_column(columns, name) for name in ("subitem_id", "shot_id")
                        if _chart_builder_runtime_column(columns, name)), "")
    missing = [label for label, column in (("step_id/process_id", step_col), ("subitem_id", subitem_col)) if not column]
    if missing:
        status["reason"] = "missing_source_columns"
        warnings.append(f"{source_id}: TEG Inline map 좌표를 붙일 원본 열이 없습니다: {', '.join(missing)}")
        return frame, meta

    rule_items = sorted({str(rule.get("item_id") or "").strip() for rule in rules if rule.get("item_id")}, key=str.casefold)
    constant_item = ""
    if not item_col:
        requested = {
            token.casefold() for token in re.findall(r"[A-Za-z_][A-Za-z0-9_$]*", str(source.sql or ""))
        }
        candidates = [item for item in rule_items if item.casefold() in requested]
        if len(candidates) == 1:
            constant_item = candidates[0]
        elif len(rule_items) == 1:
            constant_item = rule_items[0]
        else:
            status["reason"] = "ambiguous_wide_item"
            warnings.append(
                f"{source_id}: item_id 열이 없고 연결된 Inline ITEM이 여러 개라 좌표 TABLE을 고를 수 없습니다. "
                "조회할 ITEM 열 하나를 SQL SELECT에 포함해 주세요."
            )
            return frame, meta

    vehicle_by_table = {
        str(rule.get("matching_table") or "").strip().casefold(): str(rule.get("vehicle") or "").strip()
        for rule in rules if rule.get("matching_table")
    }
    coordinate_by_key: dict[tuple[str, str, str], dict] = {}
    ambiguous: set[tuple[str, str, str]] = set()
    for row in mapping.get("rows") or []:
        key = tuple(inline_coordinates.normalize_key(row.get(name)) for name in ("step_id", "item_id", "subitem_id"))
        map_name = str(row.get("matching_table") or "").strip()
        candidate = {
            "__flow_inline_step": key[0],
            "__flow_inline_item": key[1],
            "__flow_inline_subitem": key[2],
            "shot_x": float(row["shot_x"]),
            "shot_y": float(row["shot_y"]),
            "inline_map_name": map_name,
            "inline_vehicle": vehicle_by_table.get(map_name.casefold(), ""),
        }
        old = coordinate_by_key.get(key)
        if old and (old["shot_x"], old["shot_y"], old["inline_map_name"].casefold()) != (
            candidate["shot_x"], candidate["shot_y"], candidate["inline_map_name"].casefold(),
        ):
            ambiguous.add(key)
        else:
            coordinate_by_key[key] = candidate
    for key in ambiguous:
        coordinate_by_key.pop(key, None)
    status["ambiguous_keys"] = len(ambiguous)
    if not coordinate_by_key:
        status["reason"] = "no_usable_coordinates"
        detail = ", ".join(status.get("missing_maps") or [])
        warnings.append(
            f"{source_id}: 연결된 TEG Inline map에 사용할 수 있는 subitem 좌표가 없습니다"
            f"{f' ({detail})' if detail else ''}."
        )
        return frame, meta

    # Preserve raw coordinates for audit, then reserve canonical names for the
    # TEG map. Case-insensitive raw column names are handled as well.
    raw_coordinate_columns: dict[str, str] = {}
    for canonical in ("shot_x", "shot_y"):
        raw = _chart_builder_runtime_column(list(frame.columns), canonical)
        if raw:
            frame, renamed = _chart_builder_safe_rename(frame, raw, f"raw_inline_{canonical}")
            raw_coordinate_columns[canonical] = renamed
    status["raw_coordinate_columns"] = raw_coordinate_columns

    map_frame = pl.DataFrame(list(coordinate_by_key.values()))
    working = frame.with_row_index("__flow_inline_row_order").with_columns([
        pl.col(step_col).cast(pl.String, strict=False).str.strip_chars().str.to_lowercase().alias("__flow_inline_step"),
        (pl.col(item_col).cast(pl.String, strict=False).str.strip_chars().str.to_lowercase()
         if item_col else pl.lit(constant_item.casefold())).alias("__flow_inline_item"),
        pl.col(subitem_col).cast(pl.String, strict=False).str.strip_chars().str.to_lowercase().alias("__flow_inline_subitem"),
    ])
    working = (
        working.join(
            map_frame,
            on=["__flow_inline_step", "__flow_inline_item", "__flow_inline_subitem"],
            how="left",
        )
        .sort("__flow_inline_row_order")
        .drop(["__flow_inline_row_order", "__flow_inline_step", "__flow_inline_item", "__flow_inline_subitem"])
    )
    matched = working.filter(pl.col("shot_x").is_not_null() & pl.col("shot_y").is_not_null()).height
    unmatched = int(working.height - matched)
    status.update({
        "applied": True,
        "matched_rows": int(matched),
        "unmatched_rows": unmatched,
        "match_rate": round((matched * 100.0 / working.height), 2) if working.height else 0.0,
        "vehicles": sorted({value for value in working["inline_vehicle"].drop_nulls().to_list() if str(value).strip()}, key=str.casefold),
    })
    if unmatched:
        sample = (
            working.filter(pl.col("shot_x").is_null())
            .select(pl.col(subitem_col).cast(pl.String, strict=False)).drop_nulls().unique().head(8).to_series().to_list()
        )
        status["unmatched_subitems"] = [str(value) for value in sample]
        warnings.append(
            f"{source_id}: TEG Inline map 좌표 매칭 {matched:,}/{working.height:,}행 "
            f"({status['match_rate']:.2f}%), 미매칭 {unmatched:,}행은 shot 차트에서 제외됩니다."
        )
    elif working.height:
        warnings.append(f"{source_id}: TEG Inline map 좌표 {matched:,}행을 모두 매칭했습니다.")
    return working, meta


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
        "runtime_lot_wafer_pairs": _chart_builder_runtime_pairs(source.runtime_lot_wafer_pairs),
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
        "runtime_lot_wafer_pairs": _chart_builder_runtime_pairs(source.runtime_lot_wafer_pairs),
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
            "reuse_count": 0,
        }
        # 고정 차트는 최근 이력 한도와 무관하게 남아야 하므로 일반 tail trim을
        # 쓰지 않는다. 서버가 고정 전체 + 최근 이력만 골라 원자적으로 정리한다.
        jsonl_append(_chart_builder_history_path(), entry, max_lines=None)
        _trim_chart_builder_history()
    return entry


def _increment_chart_builder_history_reuse(*, history_id: str, username: str) -> dict:
    """Update one chart history row in place instead of appending a duplicate."""
    chart_id = _cache_safe_text(history_id, 120)
    if not chart_id:
        raise ValueError("Chart ID가 비어 있습니다.")
    with _CHART_BUILDER_HISTORY_LOCK:
        entries = jsonl_read(_chart_builder_history_path(), limit=0)
        updated = None
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict) or entry.get("event") != "history":
                continue
            if str(entry.get("history_id") or "") != chart_id:
                continue
            row = dict(entry)
            try:
                reuse_count = max(0, int(row.get("reuse_count") or 0))
            except (TypeError, ValueError):
                reuse_count = 0
            row.update({
                "reuse_count": reuse_count + 1,
                "last_reused_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "last_reused_by": _cache_safe_text(username, 80) or "anonymous",
            })
            entries[index] = row
            updated = row
            break
        if updated is None:
            raise KeyError(chart_id)
        atomic_write_text(
            _chart_builder_history_path(),
            "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries),
        )
    return next(
        entry for entry in _chart_builder_history_entries()
        if str(entry.get("history_id") or "") == chart_id
    )


_CHART_ASSISTANT_TYPES = {
    "scatter", "line", "box", "bar", "bar_horizontal", "pie", "donut", "radius", "wafer_map",
}
_CHART_ASSISTANT_FIELDS = {
    "type", "x", "y", "color", "trellis", "width", "height", "highlight", "show_legend",
    "color_rules", "color_else", "y_scale",
}
_CHART_ASSISTANT_JOIN_FIELDS = {"left", "right", "left_on", "right_on", "how"}
_CHART_ASSISTANT_JOIN_HOWS = {"left", "inner", "full", "semi", "anti"}
_CHART_ASSISTANT_TYPE_CONVERSION_CONTRACT = {
    "runtime_filters": "RECENT_DAYS and root_lot_id/wafer_id filters cast and normalize automatically; do not add CAST.",
    "joins": "JOIN keys are converted to strings automatically when source dtypes differ.",
    "color_rules": "Use root_lot_id/wafer_id equality directly and use 'tkout_time WITHIN N DAYS'; the UI normalizes string values.",
    "manual_sql": "Only free-form numeric/temporal WHERE comparisons on string columns need CAST/TRY_CAST; execution normalizes to TRY_CAST.",
}
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
    chart_type = ""
    for labels, value in (
        (("wafer map", "wf map", "웨이퍼 맵", "웨이퍼맵"), "wafer_map"),
        (("radius", "반경"), "radius"),
        (("scatter", "corr", "상관", "산점"), "scatter"),
        (("trend", "line", "트렌드", "추이", "선 그래프"), "line"),
        (("box", "박스", "상자"), "box"),
        (("horizontal bar", "가로 막대"), "bar_horizontal"),
        (("donut", "도넛"), "donut"),
        (("pie", "파이"), "pie"),
        (("bar", "막대"), "bar"),
    ):
        if any(label in folded for label in labels):
            chart_type = value
            break
    if chart_type:
        set_chart("type", chart_type)

    def mentioned_axis(axis: str) -> str:
        for column in columns:
            escaped = re.escape(str(column))
            if re.search(rf"(?:{axis}\s*축?|{axis}\s*=)\s*(?:을|를|은|는|:)?\s*{escaped}(?![A-Za-z0-9_])", prompt, re.I):
                return str(column)
            if re.search(rf"(?<![A-Za-z0-9_]){escaped}\s*(?:을|를|은|는)?\s*{axis}\s*(?:축|로)", prompt, re.I):
                return str(column)
        return ""

    x_axis = mentioned_axis("x")
    y_axis = mentioned_axis("y")
    if x_axis:
        set_chart("x", x_axis)
    if y_axis:
        set_chart("y", y_axis)

    auto_context = any(word in folded for word in ("자동 추천", "자동 설정", "추천해", "알아서", "기본 차트"))
    if auto_context:
        by_fold = {str(column).casefold(): str(column) for column in columns}
        time_col = next((by_fold[name] for name in ("tkout_time", "tkin_time", "time", "date") if name in by_fold), "")
        value_col = next((by_fold[name] for name in ("value", "item_value", "measurement_value", "shot_yield") if name in by_fold), "")
        numeric_guess = value_col or next((str(column) for column in columns if str(column).casefold() not in {
            "root_lot_id", "lot_id", "wafer_id", "item_id", "subitem_id", "step_id", "tkout_time", "tkin_time",
        }), "")
        if "shot_x" in by_fold and "shot_y" in by_fold and numeric_guess:
            set_chart("type", "wafer_map")
            set_chart("x", by_fold["shot_x"])
            set_chart("y", numeric_guess)
        elif time_col and numeric_guess:
            set_chart("type", "line")
            set_chart("x", time_col)
            set_chart("y", numeric_guess)

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

    legend_context = "legend" in folded or "범례" in folded
    if legend_context:
        set_chart("show_legend", not any(word in folded for word in ("없애", "숨겨", "끄기", "빼줘")))
    highlight_context = "highlight" in folded or "하이라이트" in folded or "강조 선택" in folded
    if highlight_context:
        set_chart("highlight", not any(word in folded for word in ("없애", "해제", "끄기", "빼줘")))
    y_scale_context = any(word in folded for word in ("y축", "y axis", "yscale", "y scale", "로그", "log", "linear", "선형"))
    if y_scale_context:
        if any(word in folded for word in ("로그", "log")):
            set_chart("y_scale", "log")
        elif any(word in folded for word in ("linear", "선형")):
            set_chart("y_scale", "linear")

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
- {scope:'chart', field:type|x|y|color|trellis|width|height|highlight|show_legend|color_rules|color_else|y_scale, value:any}
- {scope:'join', index:zero-based integer, field:left|right|left_on|right_on|how, value:any}
If the request is ambiguous, return no operations and ask one short clarification in message.
For a solid color set chart color='custom', color_rules=[], and color_else to the requested CSS color.
For lot/wafer coloring, use direct root_lot_id/wafer_id equality in color_rules without CAST.
For relative time coloring, use `tkout_time WITHIN N DAYS` without CAST.
Runtime filters and JOIN keys already normalize string/numeric types automatically.
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
            "type_conversion_contract": _CHART_ASSISTANT_TYPE_CONVERSION_CONTRACT,
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
            elif field in {"highlight", "show_legend"}:
                value = value if isinstance(value, bool) else str(value or "").strip().lower() in {"1", "true", "yes", "on"}
            elif field == "y_scale":
                value = str(value or "").strip().casefold()
                if value not in {"linear", "log"}:
                    warnings.append("Y축 Scale은 linear 또는 log여야 합니다.")
                    continue
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
