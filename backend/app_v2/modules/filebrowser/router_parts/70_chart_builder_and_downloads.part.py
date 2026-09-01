@router.post("/chart-builder/parse")
def chart_builder_parse(req: ChartBuilderDefinitionReq, request: Request):
    """Parse shared ChartBuilder code without running a data query."""
    _require_filebrowser_user(request)
    try:
        parsed = parse_chart_builder_definition(req.code)
    except ChartBuilderDefinitionError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, **parsed}


@router.get("/chart-builder/history")
def chart_builder_history(request: Request, limit: int = Query(100, ge=1, le=200), q: str = Query("")):
    """Return shared ChartBuilder code history for all authenticated engineers."""
    _require_filebrowser_user(request)
    entries = _chart_builder_history_entries()
    query = _cache_safe_text(q, 200).casefold()
    if query:
        entries = [entry for entry in entries if query in json.dumps(entry, ensure_ascii=False).casefold()]
    entries = entries[-max(1, min(200, int(limit or 100))):]
    return {"ok": True, "history": list(reversed(entries)), "limit": limit}


@router.get("/chart-builder/radius-layout")
def chart_builder_radius_layout(request: Request, product: str = Query(...)):
    _require_filebrowser_user(request)
    return _chart_builder_radius_layout(product)


def _chart_builder_run_data(req: ChartBuilderRunReq, request: Request, me: dict):
    """Run the bounded data phase. The public route adds cache, admission and history."""
    sources = list(req.sources or [])
    if not sources or len(sources) > 10:
        raise HTTPException(400, "차트생성은 1~10개의 DB query를 지원합니다.")
    max_rows = max(1, min(10000, int(req.max_rows or 10000)))
    frames: dict[str, pl.DataFrame] = {}
    source_payloads: list[dict] = []
    source_order: list[str] = []
    warnings: list[str] = []
    for idx, source in enumerate(sources, start=1):
        source_id = re.sub(r"[^A-Za-z0-9_-]+", "_", str(source.id or f"q{idx}").strip())[:40] or f"q{idx}"
        if source_id in frames:
            raise HTTPException(400, f"중복 query id: {source_id}")
        files: list[Path] = []
        source_meta: dict = {}
        source_root = str(source.root or "").strip()
        if source_root.upper() == YIELD_SHOT_ROOT:
            try:
                df, display_sql, yield_warnings, source_meta = _chart_builder_yield_shot_frame(
                    source, max_rows=max_rows,
                )
                warnings.extend(f"{source_id}: {message}" for message in yield_warnings)
            except HTTPException:
                raise
            except (FileNotFoundError, ValueError) as exc:
                raise HTTPException(400, f"{source_id} Full Shot 수율 조회 실패: {exc}") from exc
        elif source.apply_reformatter:
            try:
                df, display_sql, reformatter_warnings, source_meta = _chart_builder_reformatter_frame(
                    source,
                    max_rows=max_rows,
                    user=me,
                )
                warnings.extend(f"{source_id}: {message}" for message in reformatter_warnings)
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(400, f"{source_id} ET reformatter 실행 실패: {exc}") from exc
        else:
            files = source_data_files(root=source_root, product=source.product)
            if not files:
                resolved_root = _chart_builder_resolve_root_name(source_root)
                if resolved_root != source_root:
                    source_root = resolved_root
                    files = source_data_files(root=source_root, product=source.product)
            if not files:
                raise HTTPException(404, f"{source_id}: {source.root}/{source.product} source를 찾지 못했습니다.")
            try:
                all_columns, schema = duckdb_engine.inspect_files(files)
                where_sql, selected_text, sort_spec = _merge_display_sql_into_args(
                    source.sql, source.select_cols, {}, all_columns
                )
                normalized = _validate_where_expression(where_sql, all_columns)
                runtime_days = max(0, min(3650, int(source.runtime_recent_days or 0)))
                if runtime_days:
                    requested_date_column = str(source.runtime_date_column or "tkout_time").strip()
                    date_column = next((column for column in all_columns if column.casefold() == requested_date_column.casefold()), "")
                    if date_column:
                        cutoff = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=runtime_days)).isoformat()
                        # tkout_time is commonly persisted as VARCHAR.  TRY_CAST keeps
                        # malformed/blank rows from aborting the whole query; those rows
                        # simply become NULL and do not pass the recent-days predicate.
                        # Use the filter-language quoting contract here; its normalizer
                        # converts backticks to the target engine's identifier quoting.
                        quoted_date_column = _quote_sql_filter_identifier(date_column)
                        normalized = _combine_where(normalized, f"TRY_CAST({quoted_date_column} AS TIMESTAMP) >= '{cutoff}'")
                    else:
                        warnings.append(f"{source_id}: 최근 {runtime_days}일 필터 열({requested_date_column})이 없어 원래 조건으로 조회했습니다.")
                runtime_where = _chart_builder_runtime_where(all_columns, source, source_id, warnings)
                requested_columns = [c.strip() for c in str(selected_text or "").split(",") if c.strip()]
                selected = [c for c in requested_columns if c in set(all_columns)]
                if not selected:
                    selected = list(all_columns[:120])
                    if len(all_columns) > 120:
                        warnings.append(f"{source_id}: 전체 {len(all_columns)}열 중 앞 120열만 조회했습니다. SELECT 열을 지정해 주세요.")
                visible_selected = list(selected)
                hidden_inline_columns: list[str] = []
                if _chart_builder_is_inline_root(source_root):
                    for column in _chart_builder_inline_required_columns(all_columns):
                        if column not in selected:
                            selected.append(column)
                            hidden_inline_columns.append(column)
                active_sort, _ = _resolve_view_sort_spec(sort_spec, all_columns)
                where = _combine_where(
                    _combine_where(_normalize_view_sql_filter(normalized, all_columns, schema), runtime_where),
                    _duckdb_valid_wafer_where(all_columns),
                )
                df, _, _ = duckdb_engine.query_files(
                    files,
                    where=where,
                    select_cols=selected,
                    limit=max_rows + 1,
                    order_by=active_sort.get("column") or "",
                    descending=_sort_descending(active_sort),
                )
                display_sql = _build_ai_sql_display_sql(visible_selected, normalized, active_sort)
                if _chart_builder_is_inline_root(source_root):
                    df, inline_meta = _chart_builder_attach_inline_coordinates(
                        df, source, source_id, warnings,
                    )
                    source_meta.update(inline_meta)
                    removable = [column for column in hidden_inline_columns if column in df.columns]
                    if removable:
                        df = df.drop(removable)
                    mapping_status = source_meta.get("inline_coordinate_mapping") or {}
                    if mapping_status.get("applied"):
                        display_columns = list(visible_selected)
                        for column in ("shot_x", "shot_y", "inline_map_name", "inline_vehicle"):
                            if column in df.columns and column not in display_columns:
                                display_columns.append(column)
                        display_sql = _build_ai_sql_display_sql(display_columns, normalized, active_sort)
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(400, f"{source_id} SQL 실행 실패: {exc}") from exc
        truncated = df.height > max_rows
        if truncated:
            df = df.head(max_rows)
            warnings.append(f"{source_id}: JOIN 안전 한도 {max_rows:,}행에서 잘렸습니다.")
        frames[source_id] = df
        source_order.append(source_id)
        source_payloads.append({
            "id": source_id,
            "root": source_root,
            "product": source.product,
            "sql": display_sql,
            "columns": list(df.columns),
            "rows": serialize_rows(df.head(100).to_dicts()),
            "row_count": df.height,
            "truncated": truncated,
            "file_count": len(files),
            "apply_reformatter": bool(source.apply_reformatter),
            "runtime_recent_days": max(0, int(source.runtime_recent_days or 0)),
            "runtime_date_column": str(source.runtime_date_column or ""),
            "runtime_root_lot_ids": _chart_builder_runtime_values(source.runtime_root_lot_ids),
            "runtime_wafer_ids": _chart_builder_runtime_values(source.runtime_wafer_ids),
            "runtime_lot_wafer_pairs": _chart_builder_runtime_pairs(source.runtime_lot_wafer_pairs),
            **source_meta,
        })

    joined = frames[source_order[0]]
    joined_ids = {source_order[0]}
    join_evidence: list[dict] = []
    for join in req.joins or []:
        left_id = str(join.left or "").strip()
        right_id = str(join.right or "").strip()
        if right_id not in frames or left_id not in frames:
            raise HTTPException(400, f"JOIN source id를 찾지 못했습니다: {left_id} → {right_id}")
        if left_id not in joined_ids:
            if len(joined_ids) == 1:
                joined = frames[left_id]
                joined_ids = {left_id}
            else:
                raise HTTPException(400, f"JOIN 순서가 연결되지 않았습니다: {left_id}")
        left_keys = [key.strip() for key in str(join.left_on or "").split(",") if key.strip()]
        right_keys = [key.strip() for key in str(join.right_on or "").split(",") if key.strip()]
        if not left_keys or len(left_keys) != len(right_keys):
            raise HTTPException(400, "JOIN 양쪽 키 개수가 같아야 합니다. 예: root_lot_id, wafer_id")
        right = frames[right_id]
        missing_left = [key for key in left_keys if key not in joined.columns]
        missing_right = [key for key in right_keys if key not in right.columns]
        if missing_left:
            raise HTTPException(400, f"{left_id} 결과에 JOIN key가 없습니다: {', '.join(missing_left)}")
        if missing_right:
            raise HTTPException(400, f"{right_id} 결과에 JOIN key가 없습니다: {', '.join(missing_right)}")
        # DB마다 wafer_id가 Utf8/Int64로 다를 수 있다. 사용자에게는 같은
        # wafer key이므로 JOIN 직전에 양쪽 key를 문자열로 정규화한다.
        joined = joined.with_columns([pl.col(key).cast(pl.String, strict=False).alias(key) for key in left_keys])
        right = right.with_columns([pl.col(key).cast(pl.String, strict=False).alias(key) for key in right_keys])
        rename = {
            col: f"{right_id}__{col}"
            for col in right.columns
            if col not in set(right_keys) and col in joined.columns
        }
        if rename:
            right = right.rename(rename)
        # 기본은 left — 기준(왼쪽) 행을 잃지 않아야 "붙지 않은 행"이 화면에 보인다.
        how = str(join.how or "left").lower()
        if how not in {"inner", "left", "full", "semi", "anti"}:
            raise HTTPException(400, f"지원하지 않는 JOIN 방식: {how}")
        try:
            joined = joined.join(right, left_on=left_keys, right_on=right_keys, how=how)
        except Exception as exc:
            raise HTTPException(400, f"JOIN 실패({left_id}.{','.join(left_keys)}={right_id}.{','.join(right_keys)}): {exc}") from exc
        if joined.height > max_rows:
            joined = joined.head(max_rows)
            warnings.append(f"JOIN 결과가 안전 한도 {max_rows:,}행에서 잘렸습니다.")
        joined_ids.add(right_id)
        join_evidence.append({"left": left_id, "right": right_id, "left_on": left_keys, "right_on": right_keys, "how": how})

    unjoined = [source_id for source_id in source_order if source_id not in joined_ids]
    if unjoined:
        warnings.append(f"최종 결과에 연결되지 않은 Query: {', '.join(unjoined)}. JOIN 설정을 추가하면 차트·다운로드에 포함됩니다.")

    result = {
        "ok": True,
        "sources": source_payloads,
        "joins": join_evidence,
        "joined": {
            "columns": list(joined.columns),
            "rows": serialize_rows(joined.to_dicts()),
            "row_count": joined.height,
            "source_ids": sorted(joined_ids),
        },
        "max_rows": max_rows,
        "warnings": warnings,
    }
    return result


def _chart_builder_cache_limits() -> tuple[int, int, float]:
    try:
        budget_mb = max(0, min(1024, int(os.environ.get("FLOW_CHART_BUILDER_CACHE_MB", "128") or 128)))
    except (TypeError, ValueError):
        budget_mb = 128
    try:
        ttl = max(0.0, min(3600.0, float(os.environ.get("FLOW_CHART_BUILDER_CACHE_TTL_SEC", "180") or 180)))
    except (TypeError, ValueError):
        ttl = 180.0
    if os.environ.get("PYTEST_CURRENT_TEST"):
        ttl = 0.0
    budget = budget_mb * 1024 * 1024
    return budget, min(32 * 1024 * 1024, max(0, budget // 2)), ttl


def _chart_builder_cache_key(req: ChartBuilderRunReq) -> str:
    inline_mapping_signature = []
    if any(_chart_builder_is_inline_root(source.root) for source in (req.sources or [])):
        for path in (
            PATHS.base_root / inline_coordinates.DEFAULT_RULEBOOK_NAME,
            PATHS.base_root / inline_coordinates.LEGACY_RULEBOOK_NAME,
            PATHS.base_root / "credential" / "inline_map_settings.json",
        ):
            try:
                stat = path.stat()
                inline_mapping_signature.append((path.name, int(stat.st_mtime_ns), int(stat.st_size)))
            except OSError:
                inline_mapping_signature.append((path.name, 0, 0))
    payload = {
        "sources": [_chart_builder_model_dict(source) for source in (req.sources or [])],
        "joins": [_chart_builder_model_dict(join) for join in (req.joins or [])],
        "max_rows": max(1, min(10000, int(req.max_rows or 10000))),
        "inline_mapping_signature": inline_mapping_signature,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _chart_builder_cache_get(key: str) -> dict | None:
    _budget, _max_entry, ttl = _chart_builder_cache_limits()
    if ttl <= 0:
        return None
    now = time.monotonic()
    with _CHART_BUILDER_CACHE_LOCK:
        stale = [cache_key for cache_key, (created, _payload) in _CHART_BUILDER_RESULT_CACHE.items() if now - created > ttl]
        for cache_key in stale:
            _CHART_BUILDER_RESULT_CACHE.pop(cache_key, None)
        hit = _CHART_BUILDER_RESULT_CACHE.pop(key, None)
        if hit:
            _CHART_BUILDER_RESULT_CACHE[key] = hit
    if not hit:
        return None
    try:
        return json.loads(hit[1].decode("utf-8"))
    except Exception:
        with _CHART_BUILDER_CACHE_LOCK:
            _CHART_BUILDER_RESULT_CACHE.pop(key, None)
        return None


def _chart_builder_cache_put(key: str, result: dict) -> None:
    budget, max_entry, ttl = _chart_builder_cache_limits()
    if budget <= 0 or max_entry <= 0 or ttl <= 0:
        return
    try:
        payload = json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    except Exception:
        return
    if len(payload) > max_entry:
        return
    with _CHART_BUILDER_CACHE_LOCK:
        _CHART_BUILDER_RESULT_CACHE.pop(key, None)
        total = sum(len(item[1]) for item in _CHART_BUILDER_RESULT_CACHE.values())
        while _CHART_BUILDER_RESULT_CACHE and total + len(payload) > budget:
            _old_key, (_created, old_payload) = _CHART_BUILDER_RESULT_CACHE.popitem(last=False)
            total -= len(old_payload)
        _CHART_BUILDER_RESULT_CACHE[key] = (time.monotonic(), payload)


@router.post("/chart-builder/run")
def chart_builder_run(req: ChartBuilderRunReq, request: Request):
    """Run ChartBuilder with a small shared cache and 5-core/10GB admission control."""
    started = time.monotonic()
    me = current_user(request)
    cache_key = _chart_builder_cache_key(req)
    result = _chart_builder_cache_get(cache_key)
    cache_hit = result is not None
    wait_ms = 0
    if result is None:
        wait_started = time.monotonic()
        if not _CHART_BUILDER_QUERY_GATE.acquire(timeout=120):
            raise HTTPException(503, "차트 조회 작업이 많습니다. 잠시 후 다시 실행해 주세요.")
        wait_ms = round((time.monotonic() - wait_started) * 1000)
        try:
            result = _chart_builder_cache_get(cache_key)
            cache_hit = result is not None
            if result is None:
                result = _chart_builder_run_data(req, request, me)
                _chart_builder_cache_put(cache_key, result)
        finally:
            _CHART_BUILDER_QUERY_GATE.release()

    from core.audit import record as _fb_audit
    joined = result.get("joined") if isinstance(result.get("joined"), dict) else {}
    _fb_audit(
        request,
        "chartbuilder:run",
        detail=(f"user={me.get('username') or ''} sources={len(req.sources or [])} joins={len(req.joins or [])} "
                f"rows={int(joined.get('row_count') or 0)} cache={'hit' if cache_hit else 'miss'}"),
        tab="chartbuilder",
    )
    result["performance"] = {
        "profile": "5-core / 10GB",
        "cache_hit": bool(cache_hit),
        "wait_ms": wait_ms,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
        "concurrency": _CHART_BUILDER_CONCURRENCY,
    }
    if req.save_history:
        try:
            saved = _record_chart_builder_history(username=me.get("username") or "", req=req, result=result)
            result["saved_chart"] = {"id": saved["history_id"], "name": saved["name"]}
        except Exception as exc:
            logger.warning("chart builder history append failed: %s", exc)
    return result


@router.get("/download-csv")
def download_csv(request: Request, root: str = Query(""), product: str = Query(""),
                 file: str = Query(""), sql: str = Query(""),
                 select_cols: str = Query(""), username: str = Query(""),
                 agg_func: str = Query(""),
                 agg_column: str = Query(""),
                 agg_group_by: str = Query(""),
                 apply_reformatter: bool = Query(True),
                 max_rows: int = Query(DEFAULT_CSV_DOWNLOAD_MAX_ROWS, ge=1, le=MAX_CSV_DOWNLOAD_MAX_ROWS),
                 max_bytes: int = Query(0, ge=0, le=MAX_CSV_DOWNLOAD_BYTES),
                 access_scope: str = Query("")):
    """v7.2: If apply_reformatter=True and a per-product rules file exists,
    derived indices (VTH_IDX, CD_RANGE, poly2 window width, etc.) are appended
    to the download — matching what engineers actually need, not raw VALUE.
    v8.8.33 보안: 세션 토큰 필수 + username 서버 세션 기준 강제 (spoof 방지)."""
    from core.auth import current_user
    me = current_user(request)
    if file:
        _require_base_file_access(request, file, access_scope)
    username = me.get("username") or "anonymous"
    try:
        settings = _load_filebrowser_settings()
        aggregate_spec = _view_aggregate_query(agg_func, agg_column, agg_group_by)
        max_rows = _csv_download_max_rows(max_rows)
        max_bytes = _csv_download_max_bytes(max_bytes, settings)
        lazy_lf = None
        source_files: list[Path] = []
        if file:
            rel = Path(file)
            folder_key = str(rel.parts[0]).casefold() if rel.parts else ""
            single_file_fp = None
            single_file_folders = _single_file_folder_names(settings)
            if folder_key in single_file_folders:
                single_file_fp = _resolve_single_file_folder_data_path(
                    file,
                    (_base_root(), _db_root()),
                    single_file_folders,
                )
            if single_file_fp is not None:
                if single_file_fp.suffix.lower() not in DATA_EXTENSIONS:
                    raise HTTPException(400, f"Unsupported file type for CSV download: {single_file_fp.suffix}")
                source_files = [single_file_fp]
                lazy_lf = scan_one_file(single_file_fp)
                if lazy_lf is None:
                    raise HTTPException(400, f"Cannot read: {file}")
                label = file
            elif folder_key == "reformatter":
                suffix = Path(rel.parts[1]).suffix.lower() if len(rel.parts) == 2 else ""
                if len(rel.parts) != 2 or rel.parts[1].startswith(".") or suffix not in (".csv", ".json"):
                    raise HTTPException(400, "Invalid reformatter path")
                product_name = Path(rel.parts[1]).stem
                rf_root = (PATHS.data_root / "reformatter").resolve()
                csv_fp = (rf_root / f"{product_name}.csv").resolve()
                json_fp = (rf_root / f"{product_name}.json").resolve()
                try:
                    (csv_fp if csv_fp.is_file() else json_fp).relative_to(rf_root)
                except ValueError:
                    raise HTTPException(400, "Invalid reformatter path")
                if csv_fp.is_file():
                    df = read_one_file(csv_fp)
                    if df is None:
                        raise HTTPException(400, f"Cannot read: {file}")
                elif json_fp.is_file():
                    from core.reformatter import REFORMATTER_TABLE_COLUMNS, load_rules, rules_to_reformatter_table
                    rows = rules_to_reformatter_table(load_rules(rf_root, product_name))
                    df = pl.DataFrame(rows) if rows else pl.DataFrame({c: [] for c in REFORMATTER_TABLE_COLUMNS})
                    for c in REFORMATTER_TABLE_COLUMNS:
                        if c not in df.columns:
                            df = df.with_columns(pl.lit("").alias(c))
                    df = df.select(REFORMATTER_TABLE_COLUMNS)
                else:
                    raise HTTPException(404, f"Reformatter not found: {file}")
                label = f"reformatter/{product_name}.csv"
            else:
                # v8.4.6: traversal 방어. Base Files can originate from base_root
                # or db_root, so resolve against both but never outside either root.
                fp = None
                for candidate_root in (_base_root(), _db_root()):
                    if not candidate_root.is_dir():
                        continue
                    cand = (candidate_root / file).resolve()
                    try:
                        cand.relative_to(candidate_root.resolve())
                    except ValueError:
                        continue
                    if cand.is_file() and cand.suffix.lower() in DATA_EXTENSIONS:
                        fp = cand
                        break
                if fp is None:
                    raise HTTPException(404)
                source_files = [fp]
                lazy_lf = scan_one_file(fp)
                if lazy_lf is None:
                    raise HTTPException(400, f"Cannot read: {file}")
                label = file
        elif root and product:
            label = f"{root}/{product}"
            reformatter_rules = []
            if apply_reformatter and product:
                try:
                    from core.reformatter import load_rules
                    reformatter_rules = load_rules(PATHS.data_root / "reformatter", product)
                except Exception:
                    reformatter_rules = []
            if reformatter_rules:
                df = read_source(root=root, product=product, max_files=None if sql.strip() else 40)
            else:
                candidate_files = source_data_files(
                    root=root,
                    product=product,
                    max_files=None if sql.strip() else 40,
                )
                parquet_files = [fp for fp in candidate_files if fp.suffix.lower() == ".parquet"]
                csv_files = [fp for fp in candidate_files if fp.suffix.lower() == ".csv"]
                source_files = parquet_files or csv_files
                lazy_lf = lazy_read_source(
                    root=root,
                    product=product,
                    max_files=None if sql.strip() else 40,
                    recent_days=None if sql.strip() else 30,
                )
                if lazy_lf is None:
                    df = read_source(root=root, product=product, max_files=None if sql.strip() else 40)
        else:
            raise HTTPException(400, "Specify file or root+product")

        if lazy_lf is not None:
            try:
                df, csv_bytes = _download_lazy_csv(
                    lazy_lf,
                    sql,
                    select_cols,
                    max_rows,
                    max_bytes,
                    source_size=duckdb_engine.total_size(source_files),
                    settings=settings,
                    aggregate_spec=aggregate_spec,
                )
            except HTTPException:
                raise
            except Exception as e:
                if aggregate_spec or not _is_dtype_mismatch_error(e) or not source_files or not duckdb_engine.is_available():
                    raise
                logger.warning("polars download fallback to duckdb label=%s: %s", label, e)
                df, csv_bytes = _download_duckdb_csv(source_files, sql, select_cols, max_rows, max_bytes, settings=settings)
            _log_dl(username, label, sql, df.height, df.width,
                    select_cols=select_cols, size_bytes=len(csv_bytes))
            # 활동 대시보드: 무엇을 어떤 조건으로 CSV 다운로드했는지 (DL_LOG 와 별도).
            from core.audit import record_user as _fb_audit_user
            _fb_audit_user(username, "filebrowser:download",
                           detail=f"target={label} rows={df.height} cols={df.width} "
                                  f"size_mb={round(len(csv_bytes) / 1e6, 2)} "
                                  f"select_cols={select_cols or 'all'} sql={sql.strip()}",
                           tab="filebrowser")
            return csv_response(csv_bytes, label)

        # v7.2: Apply reformatter rules BEFORE select/sql so derived cols can be selected/filtered.
        # This dataframe path is retained for reformatter-derived columns and small config files.
        rf_applied = []
        if apply_reformatter and product:
            try:
                from core.reformatter import load_rules, apply_rules
                BASE = PATHS.data_root / "reformatter"
                rules = load_rules(BASE, product)
                if rules:
                    orig = set(df.columns)
                    df = apply_rules(df, rules, enabled_only=True)
                    rf_applied = [c for c in df.columns if c not in orig]
                    logger.info(f"Reformatter applied {len(rules)} rules → {len(rf_applied)} derived cols")
            except Exception as e:
                logger.warning(f"Reformatter skipped: {e}")

        df, _wafer_filtered = _filter_valid_wafers_df(df)
        df_schema = {n: str(d) for n, d in df.schema.items()}
        sql, select_cols, sort_spec = _merge_display_sql_into_args(sql, select_cols, {}, list(df.columns))
        normalized_sql = _validate_where_expression(sql, list(df.columns))
        guard_select_cols = select_cols
        if aggregate_spec:
            guard_select_cols = _aggregate_guard_select_cols(
                _normalize_ai_sql_aggregate(aggregate_spec, list(df.columns), [], "aggregate")
            )
        _guard_source_operation(
            all_columns=list(df.columns),
            sql=normalized_sql,
            select_cols=guard_select_cols,
            source_size=0,
            settings=settings,
            operation="download",
        )
        if sql.strip():
            df = apply_sql_like(df, _normalize_polars_view_sql_filter(normalized_sql, list(df.columns), df_schema))
        if aggregate_spec:
            warnings: list[str] = []
            active_aggregate = _normalize_ai_sql_aggregate(aggregate_spec, list(df.columns), warnings, "aggregate")
            if warnings:
                _fb_error(400, "invalid_aggregate", warnings[0])
            df = _apply_aggregate_df(df, active_aggregate)
            select_cols = ""
        sort_columns = list(df.columns)
        active_sort, _latest_order_col = _resolve_view_sort_spec(sort_spec, sort_columns)
        if active_sort and active_sort.get("column") in df.columns:
            df = df.sort(
                _sort_expr(active_sort, None),
                descending=_sort_descending(active_sort),
                nulls_last=_sort_nulls_last(active_sort),
            )
        if select_cols.strip():
            sel = [c.strip() for c in select_cols.split(",") if c.strip() in set(df.columns)]
            if sel:
                df = df.select(sel)
        if df.height > max_rows:
            raise HTTPException(
                400,
                f"CSV 다운로드는 최대 {max_rows:,}행까지 허용됩니다. SQL 필터를 추가하거나 max_rows를 조정하세요.",
            )
        csv_bytes = _csv_bytes_checked(df, max_bytes)
        _log_dl(username, label, sql, df.height, df.width,
                select_cols=select_cols, size_bytes=len(csv_bytes))
        # 활동 대시보드: 무엇을 어떤 조건으로 CSV 다운로드했는지 (DL_LOG 와 별도).
        from core.audit import record_user as _fb_audit_user
        _fb_audit_user(username, "filebrowser:download",
                       detail=f"target={label} rows={df.height} cols={df.width} "
                              f"size_mb={round(len(csv_bytes) / 1e6, 2)} "
                              f"select_cols={select_cols or 'all'} sql={sql.strip()}",
                       tab="filebrowser")
        return csv_response(csv_bytes, label)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Download failed: {str(e)}")


@router.get("/download-history")
def download_history(request: Request, username: str = Query(""), limit: int = Query(100)):
    """v8.8.33 보안: admin 이면 전체, 일반 유저는 본인만."""
    from core.auth import current_user
    me = current_user(request)
    if me.get("role") != "admin":
        username = me.get("username") or ""
    f = (lambda e: e.get("username") == username) if username else None
    return {"logs": jsonl_read(DL_LOG, limit, f)}


class BaseDeleteReq(BaseModel):
    file: str
    username: str = ""


class BaseFileSaveReq(BaseModel):
    file: str
    mode: str = "replace"
    csv_text: str = ""
    delimiter: str = "auto"
    include_header: bool = True
    note: str = ""
    access_scope: str = ""
    confirm_missing_step_desc: bool = False


class FileBrowserSettingsReq(BaseModel):
    csv_full_read_max_bytes: int = DEFAULT_CSV_FULL_READ_MAX_BYTES
    csv_download_max_rows: int = DEFAULT_FILEBROWSER_CSV_DOWNLOAD_ROWS
    csv_download_max_bytes: int = DEFAULT_CSV_DOWNLOAD_MAX_BYTES
    sql_query_max_source_bytes: int = DEFAULT_SQL_QUERY_MAX_SOURCE_BYTES
    preview_max_columns: int = DEFAULT_PREVIEW_MAX_COLUMNS
    preview_max_rows: int = LATEST_PREVIEW_ROWS
    schema_column_page_size: int = DEFAULT_SCHEMA_COLUMN_PAGE_SIZE
    csv_rules: dict = {}
    file_descriptions: dict[str, str] = {}
    hidden_db_dirs: list[str] = DEFAULT_FILEBROWSER_SETTINGS["hidden_db_dirs"]
    db_name_aliases: dict[str, str] = {}
    versioned_single_file_dirs: list[str] = DEFAULT_FILEBROWSER_SETTINGS["versioned_single_file_dirs"]
    auto_s3_upload_on_save: bool = False


class FileBrowserSettingsLlmDraftReq(BaseModel):
    file: str = ""
    prompt: str = ""
    columns: list[str] = []
    sample_rows: list[dict] = []
    current_rule: dict = {}


class FileBrowserSqlLlmDraftReq(BaseModel):
    natural_language: str = ""
    columns: list[str] = []
    dtypes: dict[str, str] = {}
    sample_rows: list[dict] = []
    preferred_selected_columns: list[str] = []
    current_sql: str = ""
    scope: str = ""
    root: str = ""
    product: str = ""
    file: str = ""


class FileBrowserSqlFeedbackReq(BaseModel):
    draft_id: str = ""
    rating: str = ""
    reason: str = ""
    natural_language: str = ""
    sql: str = ""
    sort: dict = {}
    aggregate: dict = {}
    selected_columns: list[str] = []
    columns: list[str] = []
    scope: str = ""
    root: str = ""
    product: str = ""
    file: str = ""
    choice: str = ""


class BaseFileValidateReq(BaseModel):
    file: str
    csv_text: str = ""
    delimiter: str = "auto"
    include_header: bool = True


class BaseTextFileSaveReq(BaseModel):
    file: str
    text: str = ""
    username: str = ""
    note: str = ""
    access_scope: str = ""


class BaseHistoryMigrateReq(BaseModel):
    file: str
    username: str = ""
    note: str = ""


class SchemaSnapshotReq(BaseModel):
    source_type: str = ""
    root: str = ""
    product: str = ""
    file: str = ""
    columns: list[str] = []
    dtypes: dict[str, str] = {}
    grain: str = ""
    join_keys: list[str] = []
    total_rows: int | None = None
    username: str = ""
    note: str = ""


class BaseFileRollbackReq(BaseModel):
    file: str
    version: str
    username: str = ""
    note: str = ""
    access_scope: str = ""


@router.get("/settings")
def filebrowser_settings(request: Request):
    from core.auth import current_user
    me = current_user(request)
    settings = _load_filebrowser_settings()
    return {
        **settings,
        "db_name_aliases": _discovered_db_name_aliases(settings),
        "can_manage": _can_manage_filebrowser(me),
        "max_csv_full_read_max_bytes": MAX_CSV_FULL_READ_MAX_BYTES,
        "max_csv_download_max_rows": MAX_CSV_DOWNLOAD_MAX_ROWS,
        "max_csv_download_max_bytes": MAX_CSV_DOWNLOAD_BYTES,
        "max_sql_query_max_source_bytes": MAX_SQL_QUERY_MAX_SOURCE_BYTES,
        "max_preview_max_columns": MAX_PREVIEW_MAX_COLUMNS,
        "max_schema_column_page_size": MAX_SCHEMA_COLUMN_PAGE_SIZE,
    }
