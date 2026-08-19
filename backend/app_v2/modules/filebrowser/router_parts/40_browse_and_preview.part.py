@router.get("/base-file-view")
def base_file_view(file: str = Query(...), sql: str = Query(""),
                   rows: int = Query(LATEST_PREVIEW_ROWS), cols: int = Query(10),
                   select_cols: str = Query(""),
                   sort_column: str = Query(""),
                   sort_direction: str = Query("asc"),
                   sort_nulls: str = Query("last"),
                   agg_func: str = Query(""),
                   agg_column: str = Query(""),
                   agg_group_by: str = Query(""),
                   engine: str = Query("auto"),
                   meta_only: bool = Query(True),
                   page: int = Query(0, ge=0),
                   page_size: int = Query(LATEST_PREVIEW_ROWS, ge=1, le=1000),
                   access_scope: str = Query(""),
                   request: Request = None):
    """v4.1: Preview a file under the Base root.

    Parquet/CSV use the same lazy reader path as `/root-parquet-view`; JSON
    files are returned as-is (truncated to first 2KB preview + full size) so
    `_uniques.json` can be inspected.
    """
    _require_base_file_access(request, file, access_scope)
    # 활동 대시보드: 실제 데이터 조회만 기록 (스키마 로드/페이지 넘김 제외).
    if not meta_only and page == 0:
        from core.audit import record as _fb_audit
        _fb_audit(request, "filebrowser:view",
                  detail=f"target=Base/{file} cols={select_cols or 'all'} sql={sql.strip()}",
                  tab="filebrowser")
    rows = rows if isinstance(rows, int) else LATEST_PREVIEW_ROWS
    cols = cols if isinstance(cols, int) else 10
    page, page_size, _offset = _preview_page_args(rows, page_size)
    rows = page_size
    # Guard against path traversal — allow base_root, and also db_root-level
    # single files (CSV/Parquet). v8.7.7: parquet 도 허용 (base-files 에 노출되므로
    # 미리보기도 가능해야 함).
    base_root = _base_root()
    db_root = _db_root()
    fp = None
    rel = Path(file)
    settings = _load_filebrowser_settings()
    sort_spec = _view_sort_query(sort_column, sort_direction, sort_nulls)
    aggregate_spec = _view_aggregate_query(agg_func, agg_column, agg_group_by)
    cols = _preview_cols_limit(cols or _settings_preview_max_columns(settings))
    single_file_folders = _single_file_folder_names(settings)
    if rel.parts and str(rel.parts[0]).casefold() in single_file_folders:
        fp = _resolve_single_file_folder_data_path(file, (base_root, db_root), single_file_folders)
        if fp is None:
            raise HTTPException(404, f"Single-file folder item not found: {file}")
    if fp is None and rel.parts and rel.parts[0] == "product_config":
        if len(rel.parts) != 2 or rel.parts[1].startswith(".") or rel.parts[1] in ("", ".", ".."):
            raise HTTPException(400, "Invalid product config path")
        pc_root = (PATHS.data_root / "product_config").resolve()
        cand = (pc_root / rel.parts[1]).resolve()
        try:
            cand.relative_to(pc_root)
        except ValueError:
            raise HTTPException(400, "Invalid product config path")
        if cand.is_file() and cand.suffix.lower() in PRODUCT_CONFIG_EXTENSIONS:
            fp = cand
        else:
            raise HTTPException(404, f"Product config not found: {file}")
    elif fp is None and rel.parts and rel.parts[0] == "uploads":
        if len(rel.parts) != 2 or rel.parts[1].startswith(".") or rel.parts[1] in ("", ".", ".."):
            raise HTTPException(400, "Invalid uploads path")
        up_root = PATHS.upload_dir.resolve()
        cand = (up_root / rel.parts[1]).resolve()
        try:
            cand.relative_to(up_root)
        except ValueError:
            raise HTTPException(400, "Invalid uploads path")
        if cand.is_file() and cand.suffix.lower() in (".csv", ".json", ".txt"):
            fp = cand
        else:
            raise HTTPException(404, f"Registered file not found: {file}")
    elif fp is None and rel.parts and rel.parts[0] == "reformatter":
        suffix = Path(rel.parts[1]).suffix.lower()
        if len(rel.parts) != 2 or rel.parts[1].startswith(".") or rel.parts[1] in ("", ".", "..") or suffix not in (".csv", ".json"):
            raise HTTPException(400, "Invalid reformatter path")
        rf_root = (PATHS.data_root / "reformatter").resolve()
        product = Path(rel.parts[1]).stem
        csv_cand = (rf_root / f"{product}.csv").resolve()
        json_cand = (rf_root / f"{product}.json").resolve()
        cand = csv_cand if csv_cand.is_file() else json_cand
        try:
            cand.relative_to(rf_root)
        except ValueError:
            raise HTTPException(400, "Invalid reformatter path")
        if cand.is_file():
            try:
                from core.reformatter import REFORMATTER_TABLE_COLUMNS, load_rules, rules_to_reformatter_table
                if cand.suffix.lower() == ".csv":
                    df = pl.read_csv(str(cand), infer_schema_length=5000, try_parse_dates=False)
                    page, page_size, offset = 0, df.height, 0
                    rows_out = serialize_rows(df.to_dicts())
                    columns = list(df.columns)
                    total_rows = df.height
                    dtypes = {c: str(df.schema[c]) for c in columns}
                else:
                    rows_all = rules_to_reformatter_table(load_rules(rf_root, product))
                    page, page_size, offset = 0, len(rows_all), 0
                    rows_out = rows_all
                    columns = REFORMATTER_TABLE_COLUMNS
                    total_rows = len(rows_all)
                    dtypes = {c: "str" for c in columns}
                return {
                    "kind": "table",
                    "file": file,
                    "product": product,
                    "columns": columns,
                    "all_columns": columns,
                    "total_cols": len(columns),
                    "data": rows_out,
                    "showing": len(rows_out),
                    "showing_cols": columns,
                    "total_rows": total_rows,
                    "page": page,
                    "page_size": page_size,
                    "has_more": offset + len(rows_out) < total_rows,
                    "dtypes": dtypes,
                    "source_path": str(cand),
                    "source_modified": cand.stat().st_mtime,
                    "source_format": cand.suffix.lower().lstrip("."),
                }
            except Exception as e:
                raise HTTPException(400, f"Cannot read reformatter: {e}")
        raise HTTPException(404, f"Reformatter not found: {file}")
    for candidate_root in (base_root, db_root):
        if fp is not None:
            break
        if not candidate_root.is_dir():
            continue
        cand = (candidate_root / file).resolve()
        try:
            cand.relative_to(candidate_root.resolve())
        except ValueError:
            continue
        if cand.is_file():
            # v8.7.7: db_root 도 CSV + parquet 모두 Base 단일 파일로 취급.
            if candidate_root == db_root and cand.suffix.lower() not in (".csv", ".parquet"):
                continue
            fp = cand
            break
    if fp is None:
        raise HTTPException(404, f"File not found in Base or DB root: {file}")

    ext = fp.suffix.lower()

    def _serve_static(static_kind: str, build):
        if _fbcache.is_enabled(settings):
            source_stat = _fbcache.stat_for_file(fp)
            if source_stat is not None:
                return _fbcache.get_or_compute(
                    endpoint="base-file-view", source=source_stat,
                    key_payload={"static_kind": static_kind},
                    compute=build,
                )
        return build()

    if ext == ".json":
        def _build_json() -> dict:
            try:
                text = fp.read_text(encoding="utf-8")
            except Exception as e:
                raise HTTPException(400, f"Cannot read JSON: {e}")
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = None
            return {
                "kind": "json",
                "file": file,
                "size": fp.stat().st_size,
                "preview": text,
                "truncated": False,
                "parsed_top_keys": list(parsed.keys()) if isinstance(parsed, dict) else None,
            }
        return _serve_static("json", _build_json)
    if ext in {".md", ".txt"}:
        def _build_md() -> dict:
            try:
                text = fp.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                raise HTTPException(400, f"Cannot read {ext.lstrip('.')}: {e}")
            return {"kind": "md", "file": file, "size": fp.stat().st_size, "text": text,
                    "truncated": False}
        return _serve_static("md", _build_md)
    if ext in PRODUCT_CONFIG_EXTENSIONS:
        def _build_yaml() -> dict:
            try:
                text = fp.read_text(encoding="utf-8")
            except Exception as e:
                raise HTTPException(400, f"Cannot read yaml: {e}")
            parsed_keys = None
            try:
                from core import product_config as _pc
                parsed = _pc.parse_text(text)
                parsed_keys = list(parsed.keys()) if isinstance(parsed, dict) else None
            except Exception:
                parsed_keys = None
            return {"kind": "yaml", "file": file, "size": fp.stat().st_size, "text": text,
                    "truncated": False, "parsed_top_keys": parsed_keys}
        return _serve_static("yaml", _build_yaml)
    if ext not in DATA_EXTENSIONS:
        raise HTTPException(400, f"Unsupported ext for preview: {ext}")
    # v8.4.3 OOM-aware — lazy scan 동일.
    try:
        def _compute() -> dict:
            if meta_only and ext == ".parquet":
                try:
                    from core.parquet_perf import read_meta
                    cached_meta_fast = read_meta(fp)
                except Exception:
                    cached_meta_fast = None
                cached_schema = (cached_meta_fast or {}).get("schema") or {}
                if cached_schema:
                    all_cols_fast = list(cached_schema.keys())
                    schema_fast = {n: str(cached_schema[n]) for n in all_cols_fast}
                    return _finalize_preview_response({
                        "kind": "table", "file": file,
                        "all_columns": all_cols_fast, "total_cols": len(all_cols_fast),
                        "columns": all_cols_fast[:cols], "dtypes": schema_fast,
                        "data": [], "showing": 0, "showing_cols": [],
                        "total_rows": int((cached_meta_fast or {}).get("row_count") or 0),
                        "meta_only": True,
                        "page": page, "page_size": page_size, "has_more": False,
                        "meta_cached": True,
                        "source_path": str(fp),
                        "source_size": fp.stat().st_size,
                        "source_modified": fp.stat().st_mtime,
                        "csv_rule_summary": None,
                        "row_count_unknown": False,
                    }, settings)
            lf = scan_one_file(fp)
            if lf is None:
                fallback = _csv_lenient_lazy_frame(fp)
                if fallback:
                    lf, all_cols_full, schema_full, fallback_rows, csv_reinit_meta = fallback
                else:
                    raise HTTPException(400, f"Cannot read: {file}")
            else:
                csv_reinit_meta = {}
                fallback_rows = None
                try:
                    full_schema_obj = lf.collect_schema()
                    all_cols_full = list(full_schema_obj.names())
                    schema_full = {n: str(full_schema_obj[n]) for n in all_cols_full}
                except Exception as schema_exc:
                    fallback = _csv_lenient_lazy_frame(fp)
                    if not fallback:
                        raise HTTPException(400, f"Cannot read schema: {schema_exc}")
                    lf, all_cols_full, schema_full, fallback_rows, csv_reinit_meta = fallback
                if ext == ".csv" and not meta_only and not csv_reinit_meta:
                    fallback = _csv_lenient_lazy_frame(fp)
                    if fallback and fallback[4].get("csv_ragged_rows_normalized"):
                        lf, all_cols_full, schema_full, fallback_rows, csv_reinit_meta = fallback
            # v8.8.16: meta_only 빠른 경로 — 스키마만 돌려주고 collect 없음.
            if meta_only:
                cached_meta_only = None
                if ext == ".parquet":
                    try:
                        from core.parquet_perf import read_meta
                        cached_meta_only = read_meta(fp)
                    except Exception:
                        cached_meta_only = None
                return _finalize_preview_response({
                    "kind": "table", "file": file,
                    "all_columns": all_cols_full, "total_cols": len(all_cols_full),
                    "columns": all_cols_full[:cols], "dtypes": schema_full,
                    "data": [], "showing": 0, "showing_cols": [],
                    "total_rows": int((cached_meta_only or {}).get("row_count") or fallback_rows or 0),
                    "meta_only": True,
                    "page": page, "page_size": page_size, "has_more": False,
                    "meta_cached": bool(cached_meta_only),
                    "row_count_unknown": not bool(cached_meta_only) and fallback_rows is None,
                    "source_path": str(fp),
                    "source_size": fp.stat().st_size,
                    "source_modified": fp.stat().st_mtime,
                    "csv_rule_summary": _csv_rule_summary(_csv_rule_for_file(file)) if ext == ".csv" else None,
                    **csv_reinit_meta,
                }, settings)
            cached_meta = None
            if ext == ".parquet":
                try:
                    from core.parquet_perf import read_meta
                    cached_meta = read_meta(fp)
                except Exception:
                    cached_meta = None
            ml_table = _is_ml_table_file(fp)
            csv_rule_summary = _csv_rule_summary(_csv_rule_for_file(file, settings)) if ext == ".csv" else None
            csv_full_read = False
            if ext == ".csv":
                try:
                    csv_full_read = fp.stat().st_size <= int(settings.get("csv_full_read_max_bytes") or 0)
                except Exception:
                    csv_full_read = False
            # CSV under the configured byte threshold is safe to read fully for
            # editing only on the initial open. SQL/column selection uses the same
            # capped preview path as DB sources so the page stays responsive.
            full_single_file = (
                csv_full_read
                and not _is_cache_file_ref(file, fp)
                and not _has_view_transform(sql, select_cols, aggregate_spec)
            )
            if full_single_file:
                resp = _run_view_lazy_full(
                    lf, sql, select_cols,
                    preview_cols=cols if ml_table else None,
                    sort_spec=sort_spec,
                    aggregate_spec=aggregate_spec,
                )
                resp["all_columns"] = all_cols_full
                resp["total_cols"] = len(all_cols_full)
                resp["dtypes"] = schema_full
                resp["kind"] = "table"
                resp["file"] = file
                resp["source_path"] = str(fp)
                resp["source_size"] = fp.stat().st_size
                resp["source_modified"] = fp.stat().st_mtime
                resp["csv_full_read_max_bytes"] = settings.get("csv_full_read_max_bytes")
                resp["csv_rule_summary"] = csv_rule_summary
                resp.update(csv_reinit_meta)
                return _finalize_preview_response(resp, settings)
            if not csv_reinit_meta and not aggregate_spec and duckdb_engine.should_use_duckdb([fp], engine=engine, sql=sql, select_cols=select_cols):
                try:
                    resp = _run_view_duckdb(
                        [fp], sql, select_cols, rows,
                        page=page, page_size=page_size, preview_cols=cols,
                        cached_meta=cached_meta,
                        settings=settings,
                        sort_spec=sort_spec,
                    )
                    resp["kind"] = "table"
                    resp["file"] = file
                    resp["source_path"] = str(fp)
                    resp["source_size"] = fp.stat().st_size
                    resp["source_modified"] = fp.stat().st_mtime
                    resp["csv_full_read_max_bytes"] = settings.get("csv_full_read_max_bytes")
                    resp["csv_rule_summary"] = csv_rule_summary
                    resp.update(csv_reinit_meta)
                    return _finalize_preview_response(resp, settings)
                except Exception as e:
                    if str(engine or "").lower() in {"duckdb", "on", "true", "1"}:
                        raise HTTPException(400, f"DuckDB query failed: {e}")
                    logger.warning("duckdb base-file-view fallback file=%s: %s", file, e)
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
            resp["kind"] = "table"
            resp["file"] = file
            resp["source_path"] = str(fp)
            resp["source_size"] = fp.stat().st_size
            resp["source_modified"] = fp.stat().st_mtime
            resp["csv_full_read_max_bytes"] = settings.get("csv_full_read_max_bytes")
            resp["csv_rule_summary"] = csv_rule_summary
            resp.update(csv_reinit_meta)
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
                    endpoint="base-file-view", source=source_stat,
                    key_payload=key_payload, compute=_compute,
                )
        return _compute()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Error: {str(e)}")


@router.get("/products")
def list_products(root: str = Query(...), fast: bool = Query(False)):
    """List products available under a root.

    v8.2.2 — Hive-partitioned layout support:
      If the root's immediate subdirs are NOT `product=<P>/` (e.g. the FAB
      root contains `fab_history/` which then contains `product=<P>/`),
      walk one level deeper so the sidebar shows real product names
      (PRODUCT_A0, PRODUCT_A1, ...) instead of table names (fab_history,
      et_wafer, ...).  For tables in multi-table roots we aggregate the
      parquet count across all tables hosting that product.
    """
    if str(root or "").strip().upper() == YIELD_SHOT_ROOT:
        from core import yield_map as _yield_map
        products = []
        for name, config in sorted((_yield_map.load_config().get("products") or {}).items()):
            shot = config.get("shot_layout") if isinstance(config, dict) else {}
            if not isinstance(shot, dict) or not shot.get("enabled"):
                continue
            products.append({
                "name": str(name), "date_count": 0, "parquet_count": 0,
                "latest_date": "", "structure": "yield-shot",
            })
        return {"products": products, "metadata_deferred": False}
    if str(root or "").strip().upper() == "SPLITTABLE":
        split_root = _db_root() / "cache" / "split_table"
        products = []
        if split_root.is_dir():
            for directory in sorted(split_root.iterdir()):
                if not directory.is_dir() or not directory.name.startswith("ML_TABLE_"):
                    continue
                count = 0 if fast else sum(1 for fp in directory.glob("*.parquet") if fp.is_file())
                products.append({"name": directory.name, "date_count": 0, "parquet_count": count, "latest_date": "", "structure": "splittable"})
        return {"products": products, "metadata_deferred": bool(fast)}
    if _is_filebrowser_hidden_dir_name(root):
        raise HTTPException(404)
    db_root = _db_root()
    rp = resolve_named_child(db_root, root) or (db_root / root)
    if not rp.is_dir():
        raise HTTPException(404)
    cache_key = ("products", str(root), bool(fast), _path_sig(rp))
    cached = _list_cache_get(cache_key)
    if cached is not None:
        return cached

    if fast:
        # First click only needs product names. Avoid count_data_files,
        # latest-date discovery, and partition recursion; previews/SQL perform
        # their own source work after a product is selected.
        try:
            with os.scandir(rp) as scanned:
                immediate = [
                    Path(entry.path)
                    for entry in scanned
                    if entry.is_dir(follow_symlinks=False)
                    and not _is_filebrowser_hidden_dir_name(entry.name)
                ]
        except OSError:
            immediate = []
        direct_hive = [d for d in immediate if d.name.startswith("product=")]
        product_names: set[str] = {
            d.name[len("product="):]
            for d in direct_hive
            if d.name[len("product="):]
        }
        if not product_names:
            # Multi-table roots (e.g. fab_history/product=X) are uncommon and
            # have only a few table folders. Probe a small prefix; legacy
            # root/product/date layouts then avoid scanning every product.
            table_layout = False
            for sub in immediate[:8]:
                try:
                    with os.scandir(sub) as children:
                        has_product_partition = any(
                            child.is_dir(follow_symlinks=False)
                            and child.name.startswith("product=")
                            for child in children
                        )
                    if has_product_partition:
                        table_layout = True
                        break
                except OSError:
                    continue
            if table_layout:
                for sub in immediate:
                    try:
                        with os.scandir(sub) as children:
                            for child in children:
                                if child.is_dir(follow_symlinks=False) and child.name.startswith("product="):
                                    name = child.name[len("product="):]
                                    if name and not _is_filebrowser_hidden_dir_name(name):
                                        product_names.add(name)
                    except OSError:
                        continue
            else:
                product_names.update(d.name for d in immediate)
        payload = {
            "products": [
                {
                    "name": name,
                    "date_count": 0,
                    "parquet_count": 0,
                    "latest_date": "",
                    "structure": "deferred",
                    "metadata_deferred": True,
                }
                for name in sorted(product_names)
            ],
            "metadata_deferred": True,
        }
        return _list_cache_set(cache_key, payload)

    # 1. Collect every `product=<P>` directory at depth 1 or 2.
    direct_hive = [d for d in rp.iterdir()
                   if d.is_dir() and d.name.startswith("product=")
                   and not _is_filebrowser_hidden_dir_name(d.name)]
    nested_hive = []
    for sub in rp.iterdir():
        if (not sub.is_dir() or sub.name.startswith("product=")
                or _is_filebrowser_hidden_dir_name(sub.name)):
            continue
        for inner in sub.iterdir():
            if (inner.is_dir() and inner.name.startswith("product=")
                    and not _is_filebrowser_hidden_dir_name(inner.name)):
                nested_hive.append(inner)
    hive_dirs = direct_hive + nested_hive

    if hive_dirs:
        # Group partitions by the product value (strip `product=` prefix).
        by_name: dict[str, list] = {}
        for d in hive_dirs:
            name = d.name[len("product="):]
            by_name.setdefault(name, []).append(d)
        prods = []
        for name in sorted(by_name):
            parts = by_name[name]
            total_files = 0
            latest_dates = []
            for p in parts:
                total_files += count_data_files(p)
                latest = _latest_date_label_for_dir(p)
                if latest:
                    latest_dates.append(latest)
            prods.append({
                "name": name,
                "date_count": 0,
                "parquet_count": total_files,
                "latest_date": max(latest_dates) if latest_dates else "",
                "structure": "hive",
            })
        return _list_cache_set(cache_key, {"products": prods})

    # 2. Legacy fallback — emit each subdir as a "product" (pre-v8.2.2 behaviour).
    prods = []
    for d in sorted(rp.iterdir()):
        if not d.is_dir() or _is_filebrowser_hidden_dir_name(d.name):
            continue
        data_file_count = count_data_files(d)
        if not data_file_count:
            continue
        has_hive = any(x.is_dir() and x.name.startswith("date=") for x in d.iterdir())
        structure = "hive" if has_hive else "flat"
        dates = sorted([x.name.replace("date=", "")
                        for x in d.iterdir()
                        if x.is_dir() and x.name.startswith("date=")])
        latest_date = _date_label_from_key(_date_key_from_text(dates[-1])) if dates else _latest_date_label_for_dir(d)
        prods.append({
            "name": d.name, "date_count": len(dates), "parquet_count": data_file_count,
            "latest_date": latest_date, "structure": structure,
        })
    return _list_cache_set(cache_key, {"products": prods})


def _page_args(page: int = 0, page_size: int = 200) -> tuple[int, int, int]:
    try:
        page = max(0, int(page or 0))
    except Exception:
        page = 0
    try:
        page_size = max(1, min(1000, int(page_size or 200)))
    except Exception:
        page_size = 200
    return page, page_size, page * page_size


def _preview_page_args(rows: int = LATEST_PREVIEW_ROWS, page_size: int = LATEST_PREVIEW_ROWS,
                       cap: int = LATEST_PREVIEW_ROWS) -> tuple[int, int, int]:
    try:
        capped = min(int(cap or LATEST_PREVIEW_ROWS), max(1, int(page_size or rows or LATEST_PREVIEW_ROWS)))
    except Exception:
        capped = LATEST_PREVIEW_ROWS
    return 0, capped, 0


def _mark_preview_capped(resp: dict) -> dict:
    if not isinstance(resp, dict):
        return resp
    resp["page"] = 0
    resp["has_more"] = False
    resp["preview_row_limit"] = LATEST_PREVIEW_ROWS
    resp["download_max_rows"] = MAX_CSV_DOWNLOAD_MAX_ROWS
    resp["download_max_bytes"] = MAX_CSV_DOWNLOAD_BYTES
    return resp


_PRODUCT_STAT_TTL_SEC = 30.0
_PRODUCT_STAT_CACHE: dict[str, tuple[float, dict | None]] = {}
_PRODUCT_STAT_INFLIGHT: set[str] = set()
_PRODUCT_STAT_LOCK = threading.Lock()
