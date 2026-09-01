@router.post("/settings/llm/draft")
def filebrowser_settings_llm_draft(req: FileBrowserSettingsLlmDraftReq, request: Request):
    _require_filebrowser_manager(request)
    file_key = _clean_rule_file_key(req.file)
    prompt = _cache_safe_text(req.prompt, 2000)
    if not prompt:
        raise HTTPException(400, "prompt is required")
    sample_rows = _safe_sample_rows(req.sample_rows)
    columns = _settings_context_columns(req.columns, sample_rows)
    current_rule, current_warnings = _normalize_csv_rule_draft(req.current_rule or {}, columns=columns)
    warnings: list[str] = list(current_warnings)
    llm_info = {"available": False, "used": False, "error": ""}
    plan: dict = {}
    try:
        from core import llm_adapter
        llm_info["available"] = bool(llm_adapter.is_available())
        if llm_info["available"]:
            system = _filebrowser_agent_prompt("settings_draft.system", (
                "You are an expert Flow FileBrowser CSV rule designer. Return only JSON. "
                "검증로직(validation_logic)은 실패 시 저장을 막는 규칙이며 required_columns, not_empty, "
                "unique_keys, enums, numeric, date, regex, conditions, ordered_by만 사용할 수 있다. "
                "정렬로직(sort_logic)은 검증 통과 후 저장 시 물리 CSV row 순서를 바꾸는 규칙이며 sort만 사용할 수 있다. "
                "Use only supplied columns and only csv_rules keys: required_columns, not_empty, "
                "unique_keys, enums, numeric, date, regex, conditions, ordered_by, sort. "
                "Draft the most detailed safe rule set the prompt supports. "
                "ordered_by validates existing row order and blocks save when the current order is wrong; sort physically reorders rows on save only after validation passes. "
                "If the user says 현재 순서 검증 or 순서가 맞는지 검사, use ordered_by. "
                "If the user says 저장할 때 정렬 or 저장 순서대로 정렬, use sort. "
                "Order spec type must be one of string, numeric, date, leading_number, rule_order. "
                "For ppid_knob.csv, product is legacy/display-only; do not require or sort by product unless the user explicitly asks. "
                "The ppid_knob.csv column contract is feature_name, rule_order, step_desc, operator, value, category. "
                "conditions must be simple Polars SQL boolean expressions over supplied columns. "
                "Do not write files, source code, paths, shell commands, or unsupported keys."
            ))
            ask = json.dumps({
                "file": file_key,
                "user_prompt": prompt,
                "expert_mode": _settings_prompt_wants_expert(prompt),
                "columns": columns[:200],
                "column_profiles": _settings_column_profiles(columns, sample_rows),
                "sample_rows": sample_rows,
                "current_rule": current_rule,
                "rule_engine_capabilities": {
                    "required_columns": "listed columns must exist",
                    "not_empty": "listed columns cannot be blank",
                    "unique_keys": "each listed column combo must be unique",
                    "enums": "column value must be one of the listed strings",
                    "numeric": "min/max/integer checks",
                    "date": "date/time parse check",
                    "regex": "Python regex full-row value pattern check",
                    "conditions": "AND-style row pass conditions; every expression must be true",
                    "ordered_by": "validate current CSV row order; keys may include group_by",
                    "sort": "reorder rows during save using the same key shape",
                },
                "response_schema": {
                    "csv_rules": {
                        file_key: {
                            "required_columns": ["column"],
                            "not_empty": ["column"],
                            "unique_keys": [["column_a", "column_b"]],
                            "enums": {"column": ["allowed"]},
                            "numeric": {"column": {"min": 0, "max": 1, "integer": False}},
                            "date": ["column"],
                            "regex": {"column": "pattern"},
                            "conditions": [{"expr": "column != ''", "message": "message"}],
                            "ordered_by": {"keys": [{"column": "column", "direction": "asc", "type": "string", "nulls": "last"}]},
                            "sort": [{"column": "column", "direction": "asc", "type": "string", "nulls": "last"}],
                        }
                    },
                    "warnings": ["optional warning"],
                },
            }, ensure_ascii=False)
            out = llm_adapter.complete_json(
                ask,
                system=system,
                timeout=30,
                max_retries=1,
                schema={
                    "keys": ["csv_rules", "warnings"],
                    "required": [],
                    "properties": {"csv_rules": {}, "warnings": {}},
                },
            )
            llm_info["used"] = bool(out.get("ok") and isinstance(out.get("obj"), dict))
            if out.get("error"):
                llm_info["error"] = str(out.get("error") or "")
            if out.get("repaired"):
                llm_info["repaired_json"] = True
            plan = out.get("obj") if isinstance(out.get("obj"), dict) else {}
    except Exception as exc:
        llm_info["error"] = f"{type(exc).__name__}: {exc}"
    if llm_info.get("available") and not llm_info.get("used") and llm_info.get("error"):
        _draft_warning(warnings, f"LLM failed: {llm_info['error']}")
    for item in (plan.get("warnings") if isinstance(plan, dict) else []) or []:
        _draft_warning(warnings, str(item))
    explicit_rule = _settings_prompt_explicit_rule(prompt, columns, current_rule, warnings)
    if explicit_rule is not None:
        raw_rule = explicit_rule
    else:
        raw_rule = _settings_llm_rule_candidate(plan, file_key)
        if not raw_rule:
            raw_rule = _settings_draft_fallback_rule(prompt, columns, current_rule, warnings, file_key, sample_rows)
    draft, draft_warnings = _normalize_csv_rule_draft(raw_rule, columns=columns)
    for item in draft_warnings:
        _draft_warning(warnings, item)
    return {
        "ok": True,
        "saved": False,
        "file": file_key,
        "unit_action": "filebrowser.settings.llm.draft",
        "draft": draft,
        "draft_sections": _csv_rule_sections(draft),
        "csv_rules": {file_key: draft} if draft else {},
        "warnings": warnings,
        "columns": columns,
        "llm": llm_info,
        "raw_plan": {k: plan.get(k) for k in ("csv_rules", "draft", "rule", "warnings") if isinstance(plan, dict) and k in plan},
    }


@router.post("/sql/llm/draft")
def filebrowser_sql_llm_draft(req: FileBrowserSqlLlmDraftReq, request: Request):
    me = _require_filebrowser_user(request)
    columns, dtypes, sample_rows, sample_profile, context_warnings = _ai_sql_context_from_source(
        scope=req.scope,
        root=req.root,
        product=req.product,
        file=req.file,
        columns=req.columns or [],
        dtypes=req.dtypes or {},
        sample_rows=req.sample_rows or [],
        prompt=req.natural_language,
        preferred_selected_columns=req.preferred_selected_columns or [],
    )
    result = _draft_filebrowser_ai_sql(
        natural_language=req.natural_language,
        columns=columns,
        dtypes=dtypes,
        sample_rows=sample_rows,
        current_sql=req.current_sql,
        scope=req.scope,
        root=req.root,
        product=req.product,
        file=req.file,
        preferred_selected_columns=req.preferred_selected_columns or [],
        sample_profile=sample_profile,
        context_warnings=context_warnings,
        username=me.get("username") or "",
    )
    try:
        _record_filebrowser_ai_sql_history(
            me.get("username") or "",
            source="filebrowser",
            request_payload=req.model_dump() if hasattr(req, "model_dump") else req.dict(),
            result_payload=result,
        )
    except Exception as exc:
        logger.warning("filebrowser ai sql history append failed: %s", exc)
    return result


@router.get("/sql/history")
def filebrowser_sql_history(request: Request, limit: int = Query(50, ge=1, le=200)):
    me = _require_filebrowser_user(request)
    username = str((me or {}).get("username") or "")
    role = str((me or {}).get("role") or "")
    try:
        limit = max(1, min(200, int(limit)))
    except Exception:
        limit = 50

    def _visible(entry: dict) -> bool:
        if not isinstance(entry, dict):
            return False
        if entry.get("event") != "history":
            return False
        if role == "admin":
            return True
        return str(entry.get("username") or "") == username

    entries = jsonl_read(_filebrowser_ai_sql_history_path(), limit=limit, filter_fn=_visible)
    return {
        "ok": True,
        "history": list(reversed(entries)),
        "limit": limit,
    }


@router.get("/sql/execution-history")
def filebrowser_sql_execution_history(
    request: Request,
    scope: str = Query(""),
    root: str = Query(""),
    product: str = Query(""),
    file: str = Query(""),
    history_id: str = Query(""),
    limit: int = Query(100, ge=1, le=200),
    access_scope: str = Query(""),
):
    """Return newest actual SQL executions for one selected DB product/file."""
    _require_filebrowser_user(request)
    normalized_scope = _normalize_ai_sql_history_scope(scope)
    root = _cache_safe_text(root, 160)
    product = _cache_safe_text(product, 160)
    file = _cache_safe_text(file, 300)
    history_id = _cache_safe_text(history_id, 80)
    if history_id and not re.fullmatch(r"fb_sql_exec_[0-9a-f]{12}", history_id, flags=re.I):
        raise HTTPException(400, "Invalid SQL history key")
    if normalized_scope == "base" and file and access_scope:
        _require_base_file_access(request, file, access_scope)
    if normalized_scope == "db_product" and not (root and product):
        return {"ok": True, "history": [], "limit": limit}
    if normalized_scope in {"rootpq", "base"} and not file:
        return {"ok": True, "history": [], "limit": limit}

    def _visible(entry: dict) -> bool:
        if not isinstance(entry, dict) or entry.get("event") != "execution":
            return False
        if history_id and str(entry.get("history_id") or "").casefold() != history_id.casefold():
            return False
        if str(entry.get("scope") or "") != normalized_scope:
            return False
        if normalized_scope == "db_product":
            return str(entry.get("root") or "") == root and str(entry.get("product") or "") == product
        return str(entry.get("file") or "") == file

    entries = jsonl_read(_filebrowser_sql_execution_history_path(), limit=limit, filter_fn=_visible)
    return {"ok": True, "history": list(reversed(entries)), "limit": limit}


@router.post("/sql/feedback")
def filebrowser_sql_feedback(req: FileBrowserSqlFeedbackReq, request: Request):
    me = _require_filebrowser_user(request)
    rating = _normalize_ai_sql_rating(req.rating)
    columns = _settings_context_columns(req.columns or [])
    warnings: list[str] = []
    sort_spec = _normalize_ai_sql_sort(req.sort or {}, columns, warnings, "feedback_sort") if columns else (req.sort or {})
    aggregate_spec = _normalize_ai_sql_aggregate(
        req.aggregate or {},
        columns,
        warnings,
        "feedback_aggregate",
    ) if columns else (req.aggregate or {})
    raw_sql = _cache_safe_text(req.sql, 2000)
    selected_input = ",".join(str(c or "").strip() for c in (req.selected_columns or []) if str(c or "").strip())
    where_sql, selected_cols_text, sort_spec = _merge_display_sql_into_args(
        raw_sql,
        selected_input,
        sort_spec if isinstance(sort_spec, dict) else {},
        columns if columns else None,
    )
    selected_values = [c.strip() for c in str(selected_cols_text or "").split(",") if c.strip()]
    selected_columns = _filter_ai_sql_selected_columns(
        selected_values,
        columns,
        warnings,
        "feedback_selected_columns",
    ) if columns else selected_values
    if columns and str(where_sql or "").strip():
        try:
            where_sql, validate_warnings = _validate_ai_sql_filter(where_sql, columns)
            warnings.extend(validate_warnings)
        except Exception as exc:
            warnings.append(f"feedback_sql rejected: {exc}")
            where_sql = ""
    display_sql = _build_ai_sql_display_sql(selected_columns, where_sql, sort_spec if isinstance(sort_spec, dict) else {})
    entry = {
        "event": "feedback",
        "feedback_id": f"fb_sql_fb_{uuid.uuid4().hex[:10]}",
        "draft_id": _cache_safe_text(req.draft_id, 100),
        "username": _cache_safe_text((me or {}).get("username") or "", 80),
        "rating": rating,
        "reason": _cache_safe_text(req.reason, 500),
        "natural_language": _cache_safe_text(req.natural_language, 2000),
        "sql": _cache_safe_text(where_sql, 2000),
        "where_sql": _cache_safe_text(where_sql, 2000),
        "display_sql": _cache_safe_text(display_sql, 2000),
        "sort": sort_spec if isinstance(sort_spec, dict) else {},
        "aggregate": aggregate_spec if isinstance(aggregate_spec, dict) else {},
        "selected_columns": selected_columns[:100],
        "columns": columns[:300],
        "column_signature": _ai_sql_column_signature(columns),
        "scope": _cache_safe_text(req.scope, 80),
        "root": _cache_safe_text(req.root, 160),
        "product": _cache_safe_text(req.product, 160),
        "file": _cache_safe_text(req.file, 240),
        "choice": _cache_safe_text(req.choice, 20),
        "warnings": warnings[:10],
    }
    jsonl_append(_filebrowser_ai_sql_feedback_path(), entry)
    return {
        "ok": True,
        "saved": True,
        "feedback_id": entry["feedback_id"],
        "path": str(_filebrowser_ai_sql_feedback_path()),
        "sql": entry["sql"],
        "where_sql": entry["where_sql"],
        "display_sql": entry["display_sql"],
        "selected_columns": entry["selected_columns"],
        "warnings": warnings,
    }


@router.post("/settings")
def save_filebrowser_settings(req: FileBrowserSettingsReq, request: Request):
    me = _require_filebrowser_manager(request)
    dump = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    settings = _normalize_filebrowser_settings(dump)
    _save_filebrowser_settings(settings)
    jsonl_append(PATHS.activity_log, {
        "username": me.get("username") or "",
        "action": "filebrowser:settings:save",
        "tab": "filebrowser",
        "detail": f"csv_rules={len(settings.get('csv_rules') or {})} file_descriptions={len(settings.get('file_descriptions') or {})} hidden_db_dirs={len(settings.get('hidden_db_dirs') or [])} versioned_dirs={len(settings.get('versioned_single_file_dirs') or [])} csv_full_read_max_bytes={settings.get('csv_full_read_max_bytes')} csv_download_max_rows={settings.get('csv_download_max_rows')} csv_download_max_bytes={settings.get('csv_download_max_bytes')}",
    })
    return {
        **settings,
        "db_name_aliases": _discovered_db_name_aliases(settings),
        "ok": True,
        "can_manage": True,
    }


@router.post("/base-file/validate")
def validate_base_file_csv(req: BaseFileValidateReq, request: Request):
    _require_filebrowser_manager(request)
    fp = _resolve_base_file_for_edit(req.file)
    if fp.suffix.lower() != ".csv":
        raise HTTPException(400, "CSV validation is available for .csv files only")
    text = req.csv_text or ""
    if not text:
        try:
            if fp.stat().st_size > BASE_FILE_EDIT_MAX_BYTES:
                raise HTTPException(413, f"CSV too large for validation (max {BASE_FILE_EDIT_MAX_BYTES:,} bytes)")
            text = fp.read_text(encoding="utf-8")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, f"Cannot read CSV: {e}")
    rows, used_delim = _parse_tab_or_csv(text, req.delimiter)
    if req.include_header and rows:
        header = [str(x).strip() for x in rows[0]]
        data_rows = rows[1:]
    else:
        try:
            lf = scan_one_file(fp)
            header = list(lf.collect_schema().names()) if lf is not None else []
        except Exception:
            fallback = _csv_lenient_lazy_frame(fp)
            header = list(fallback[1]) if fallback else []
        data_rows = rows
    if not header:
        header = [f"col_{i + 1}" for i in range(max((len(r) for r in data_rows), default=1))]
    header, data_rows, dropped_generated_extra_columns = _drop_generated_extra_columns(header, data_rows)
    if not header:
        raise HTTPException(400, "CSV header has no editable columns")
    data_rows, _ = _normalize_rows(data_rows, len(header), "")
    sorted_rows, result = _validate_and_sort_csv_rows(req.file, header, data_rows)
    result.update({
        "file": req.file,
        "delimiter": used_delim,
        "columns_list": header,
        "dropped_generated_extra_columns": dropped_generated_extra_columns,
        "preview_rows": [dict(zip(header, row)) for row in sorted_rows[:20]],
        "sorted_csv_text": _rows_to_csv_text(header, sorted_rows, used_delim, include_header=req.include_header) if result.get("ok") else "",
        "save_policy": "validation_blocks_save; sort_applies_only_after_validation_passes",
        "save_policy_label": "검증 통과 시 저장 정렬 적용",
    })
    return result


def _missing_ppid_knob_step_desc(
    header: list[str],
    data_rows: list[list[str]],
    vehicle_matching_rows: list[dict],
) -> list[dict]:
    """Return non-empty ppid_knob step_desc values absent from Vehicle_matching.

    Row numbers use CSV line numbers (header is line 1) so the warning can point
    the editor directly to every affected row.
    """
    if "step_desc" not in header:
        return []
    step_desc_idx = header.index("step_desc")
    known = {
        str(row.get("step_desc") or "").strip()
        for row in vehicle_matching_rows
        if str(row.get("step_desc") or "").strip()
    }
    missing_by_value: dict[str, list[int]] = {}
    for csv_row_number, row in enumerate(data_rows, start=2):
        value = str(row[step_desc_idx] if step_desc_idx < len(row) else "").strip()
        if value and value not in known:
            missing_by_value.setdefault(value, []).append(csv_row_number)
    return [
        {"value": value, "rows": row_numbers}
        for value, row_numbers in missing_by_value.items()
    ]


def _save_base_file(req: BaseFileSaveReq, request: Request):
    me, _ = _require_base_file_access(request, req.file, req.access_scope, manage=True)

    if (req.mode or "").strip().lower() != "replace":
        raise HTTPException(400, "Only mode='replace' is supported")
    text = (req.csv_text or "").strip()
    if not text and req.include_header is False:
        raise HTTPException(400, "csv_text is required")

    if len((req.csv_text or "").encode("utf-8")) > BASE_FILE_EDIT_MAX_BYTES:
        raise HTTPException(
            413,
            f"CSV payload too large: {len((req.csv_text or '').encode('utf-8')):,} bytes (max {BASE_FILE_EDIT_MAX_BYTES:,})",
        )

    fp = _resolve_base_file_for_edit(req.file)
    ext = fp.suffix.lower()
    if ext not in BASE_EDIT_ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported file type: {ext}")

    rows, used_delim = _parse_tab_or_csv(req.csv_text or "", req.delimiter)
    if req.include_header and rows:
        header = [str(x).strip() for x in rows[0]]
        data_rows = rows[1:]
    else:
        header = []
        data_rows = rows

    schema_rows = []
    try:
        lf = scan_one_file(fp)
        if lf is not None:
            schema_rows = list(lf.collect_schema().names())
    except Exception:
        fallback = _csv_lenient_lazy_frame(fp)
        schema_rows = list(fallback[1]) if fallback else []

    if not header and schema_rows:
        header = list(schema_rows)
    if not header:
        header = [f"col_{i + 1}" for i in range(max((len(r) for r in data_rows), default=1))]

    header, data_rows, dropped_generated_extra_columns = _drop_generated_extra_columns(header, data_rows)
    if not header:
        raise HTTPException(400, "CSV header has no editable columns")
    data_rows, _ = _normalize_rows(data_rows, len(header), "")

    if Path(req.file).name.casefold() == "ppid_knob.csv" and "step_desc" in header:
        vm_rows = []
        try:
            import re
            from core import fab_reference
            vm_rows = fab_reference._read_rows(fab_reference.VEHICLE_MATCHING_FILE)
            vm_map = {str(r.get("step_id") or "").strip(): str(r.get("step_desc") or "").strip() for r in vm_rows if r.get("step_id") and r.get("step_desc")}

            step_desc_idx = header.index("step_desc")
            pattern = re.compile(r'^[A-Za-z]{2}\d{6}')

            for r_idx, r in enumerate(data_rows):
                if step_desc_idx < len(r):
                    val = str(r[step_desc_idx]).strip()
                    if pattern.search(val) and val in vm_map:
                        data_rows[r_idx][step_desc_idx] = vm_map[val]
        except Exception as e:
            logger.warning("Failed to auto-correct ppid_knob step_desc: %s", e)

        missing_step_desc = _missing_ppid_knob_step_desc(header, data_rows, vm_rows)
        if missing_step_desc and not req.confirm_missing_step_desc:
            raise HTTPException(409, {
                "error_code": "ppid_knob_step_desc_not_found",
                "message": "Vehicle_matching.csv에 없는 step_desc가 있습니다.",
                "file": req.file,
                "reference_file": "Vehicle_matching.csv",
                "missing_step_desc": missing_step_desc,
                "missing_count": len(missing_step_desc),
                "missing_row_count": sum(len(item["rows"]) for item in missing_step_desc),
            })

    if len(data_rows) > BASE_FILE_EDIT_MAX_ROWS:
        raise HTTPException(413, f"Row count too large: {len(data_rows):,} rows (max {BASE_FILE_EDIT_MAX_ROWS:,})")
    csv_validation = {
        "ok": True,
        "rule_applied": False,
        "rule_summary": None,
        "sorted": False,
        "errors": [],
        "error_count": 0,
    }
    if ext == ".csv":
        data_rows, csv_validation = _validate_and_sort_csv_rows(req.file, header, data_rows)
        if not csv_validation.get("ok"):
            raise HTTPException(400, {
                "message": "CSV validation failed",
                "file": req.file,
                "errors": csv_validation.get("errors") or [],
                "error_count": csv_validation.get("error_count") or 0,
                "truncated": bool(csv_validation.get("truncated")),
                "rule_summary": csv_validation.get("rule_summary"),
            })

    backup = None
    version_meta = None
    try:
        backup = _ensure_base_file_backup(fp)
    except Exception:
        backup = None

    try:
        if ext == ".csv":
            _write_text_atomic(fp, _rows_to_csv_text(header, data_rows, used_delim, include_header=req.include_header))
        else:
            data_map = {col: [r[i] if i < len(r) else "" for r in data_rows] for i, col in enumerate(header)}
            df = pl.DataFrame(data_map if data_map else {col: pl.Series([]) for col in header})
            if header:
                for col in header:
                    df = df.with_columns(pl.col(col).cast(pl.Utf8, strict=False))
            _write_parquet_atomic(fp, df)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Save failed: {e}")

    try:
        version_meta = _snapshot_base_file_version(
            fp,
            req.file,
            actor=me.get("username") or "",
            action="edit",
            note=req.note or "FileBrowser single-file edit",
            diff_previous=Path(backup) if backup else None,
        )
    except Exception as e:
        logger.warning("base-file/save version snapshot skipped file=%s: %s", fp, e)
    # 10번째 저장마다 'DB BACKUP' 에 장기 사본 (버전 이력은 최근 BASE_VERSION_CAP 개만 남는다).
    archived = _archive_base_file_every_n_edits(fp, version_meta)

    try:
        # v9.1.x: 저장 응답을 빠르게 — 파생 캐시 재생성(matching DuckDB, ~1s)과 S3 sync 는
        #   파일 자체를 이미 원자적으로 썼으므로 백그라운드로 돌리고 즉시 응답한다.
        import threading
        actor = me.get("username") or ""

        def _post_save_side_effects():
            try:
                if _matching_cache.is_matching_file(fp):
                    cache_result = _matching_cache.refresh_matching_csv(fp)
                    if not cache_result.get("ok", False):
                        logger.warning("filebrowser base-file/save cache refresh failed: %s", cache_result)
            except Exception as e:
                logger.warning("filebrowser base-file/save cache refresh error file=%s: %s", fp, e)
            try:
                _filebrowser_s3_sync_for_saved_path(fp)
            except Exception as e:
                logger.warning("filebrowser base-file/save s3 sync error file=%s: %s", fp, e)

        threading.Thread(target=_post_save_side_effects, daemon=True,
                         name="fb-save-postprocess").start()
        jsonl_append(PATHS.activity_log, {
            "username": actor,
            "action": "filebrowser:base-file:save",
            "tab": "filebrowser",
            "detail": f"file={req.file} rows={len(data_rows)} cols={len(header)} version={(version_meta or {}).get('version', '')}",
        })
        return {
            "ok": True,
            "file": req.file,
            "backup": backup,
            "archived_backup": archived,
            "source_path": str(fp),
            "source_modified": fp.stat().st_mtime,
            "delimiter": used_delim,
            "rows": len(data_rows),
            "cols": len(header),
            "version": version_meta,
            "cache_rows": None,
            "step_cache_rows": None,
            "s3_sync": {"status": "pending_background"},
            "csv_validation": csv_validation,
            "dropped_generated_extra_columns": dropped_generated_extra_columns,
        }
    except Exception as e:
        raise HTTPException(500, f"Failed to read result after save: {e}")


@router.post("/base-file/save")
@router.post("/base-file/save/")
@router.post("/base-file-save")
def save_base_file(req: BaseFileSaveReq, request: Request):
    """Replace a Base-scope single CSV/Parquet file with pasted text."""
    out = _save_base_file(req, request)
    # 활동 대시보드: 어떤 Base 파일을 수정(교체)했는지.
    from core.audit import record as _fb_audit
    _fb_audit(request, "filebrowser:base-file:save", detail=f"file={req.file}", tab="filebrowser")
    return out


@router.post("/base-file/text-save")
def save_base_text_file(req: BaseTextFileSaveReq, request: Request):
    me, _ = _require_base_file_access(request, req.file, req.access_scope, manage=True)
    target = _resolve_base_file_for_version(req.file)
    if not _base_file_versioned(req.file, target):
        raise HTTPException(400, "This file is not configured for EDM text editing")
    if target.suffix.lower() not in {".json", ".yaml", ".yml", ".md", ".txt", ".csv"}:
        raise HTTPException(400, f"Unsupported text file type: {target.suffix}")
    if len((req.text or "").encode("utf-8")) > BASE_FILE_EDIT_MAX_BYTES:
        raise HTTPException(413, f"Text payload too large (max {BASE_FILE_EDIT_MAX_BYTES:,} bytes)")
    if target.suffix.lower() == ".json":
        try:
            json.loads(req.text or "")
        except Exception as e:
            raise HTTPException(400, f"Invalid JSON: {e}")
    if target.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
            yaml.safe_load(req.text or "")
        except ImportError:
            pass
        except Exception as e:
            raise HTTPException(400, f"Invalid YAML: {e}")
    backup = _ensure_base_file_backup(target)
    version_meta = None
    try:
        _write_text_atomic(target, req.text or "")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Text save failed: {e}")
    try:
        version_meta = _snapshot_base_file_version(
            target,
            req.file,
            actor=req.username or me.get("username") or "",
            action="edit",
            note=req.note or "FileBrowser raw text edit",
            diff_previous=Path(backup) if backup else None,
        )
    except Exception as e:
        logger.warning("base-file/text-save version snapshot skipped file=%s: %s", target, e)
    # 10번째 저장마다 'DB BACKUP' 에 장기 사본 (버전 이력은 최근 BASE_VERSION_CAP 개만 남는다).
    archived = _archive_base_file_every_n_edits(target, version_meta)
    # 파일은 이미 원자적으로 썼으므로 S3 sync 는 백그라운드로 돌리고 즉시 응답한다
    # (base-file/save 와 동일 패턴 — S3 지연/장애가 저장 응답을 막지 않도록).
    def _text_save_s3_sync():
        try:
            _filebrowser_s3_sync_for_saved_path(target)
        except Exception as e:
            logger.warning("filebrowser base-file/text-save s3 sync error file=%s: %s", target, e)

    threading.Thread(target=_text_save_s3_sync, daemon=True,
                     name="fb-textsave-s3sync").start()
    jsonl_append(PATHS.activity_log, {
        "username": req.username or me.get("username") or "",
        "action": "filebrowser:base-file:text-save",
        "tab": "filebrowser",
        "detail": f"file={req.file} version={(version_meta or {}).get('version', '')}",
    })
    return {
        "ok": True,
        "file": req.file,
        "source_path": str(target),
        "source_modified": target.stat().st_mtime,
        "backup": backup,
        "archived_backup": archived,
        "version": version_meta,
        "size": target.stat().st_size,
        "s3_sync": {"status": "pending_background"},
    }


@router.get("/base-file/versions")
def base_file_versions(request: Request, file: str = Query(...), access_scope: str = Query("")):
    _require_base_file_access(request, file, access_scope)
    fp = _resolve_base_file_for_version(file)
    versioned = _base_file_versioned(file, fp)
    versions = _list_base_file_versions(file) if versioned else []
    versions.sort(key=lambda v: str(v.get("created_at") or ""), reverse=True)
    profile = _file_profile(fp)
    try:
        modified_at = datetime.datetime.fromtimestamp(fp.stat().st_mtime).isoformat(timespec="seconds")
    except Exception:
        modified_at = ""
    current_version_info = _current_base_file_version_info(file, fp, profile)
    return {
        "ok": True,
        "file": file,
        "versioned": versioned,
        "cap": BASE_VERSION_CAP,
        "versions": versions[:BASE_VERSION_CAP],
        "current_storage_version": current_version_info.get("current_storage_version"),
        "current_profile": {
            "rows": profile.get("rows"),
            "columns": profile.get("column_count"),
            "size": profile.get("size"),
            "modified_at": modified_at,
            "checksum": profile.get("checksum") or "",
            **current_version_info,
        },
    }


@router.get("/base-file/version-content")
def base_file_version_content(request: Request, file: str = Query(...), version: str = Query(...),
                              access_scope: str = Query("")):
    _require_base_file_access(request, file, access_scope)
    target = _resolve_base_file_for_version(file)
    clean_version = safe_filename(version)
    content_fp, meta = _resolve_base_version_content(file, clean_version, target)
    storage_version = str(meta.get("version") or clean_version)
    previous_fp = _previous_version_content(file, storage_version)
    ext = content_fp.suffix.lower()
    out = {"ok": True, "file": file, "version": clean_version, "meta": meta, "kind": ext.lstrip(".")}
    out["current_profile"] = _file_profile(target)
    out["version_profile"] = _file_profile(content_fp)
    out["diff"] = _profile_diff(out["current_profile"], out["version_profile"])
    out["diff_table"] = meta.get("save_diff_table") or _diff_table_between(content_fp, previous_fp, file=file)
    if ext in {".csv", ".txt", ".json", ".yaml", ".yml", ".md"}:
        raw = content_fp.read_text(encoding="utf-8", errors="replace")
        out["text"] = raw[:100_000]
        out["truncated"] = len(raw) > 100_000
    elif ext == ".parquet":
        lf = scan_one_file(content_fp)
        cols = list(lf.collect_schema().names()) if lf is not None else []
        sample = lf.head(50).collect().to_dicts() if lf is not None else []
        out["columns"] = cols
        out["rows"] = serialize_rows(sample)
    return out


@router.post("/base-file/rollback")
def rollback_base_file(req: BaseFileRollbackReq, request: Request):
    me, _ = _require_base_file_access(request, req.file, req.access_scope, manage=True)
    target = _resolve_base_file_for_version(req.file)
    if not _base_file_versioned(req.file, target):
        raise HTTPException(400, "This file is not configured for EDM version rollback")
    clean_version = safe_filename(req.version)
    content_fp, meta = _resolve_base_version_content(req.file, clean_version, target)
    if content_fp.suffix.lower() != target.suffix.lower():
        raise HTTPException(400, "Version file type does not match current file")
    pre = _snapshot_base_file_version(
        target,
        req.file,
        actor=req.username or me.get("username") or "",
        action="pre-rollback",
        note=f"Before rollback to {clean_version}",
    )
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.rollback.", suffix=".tmp", dir=str(target.parent))
    try:
        os.close(fd)
        shutil.copy2(content_fp, tmp_name)
        os.replace(tmp_name, target)
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except Exception:
            pass
    applied = _snapshot_base_file_version(
        target,
        req.file,
        actor=req.username or me.get("username") or "",
        action="rollback",
        note=req.note or f"Rolled back to {clean_version}",
    )
    # 파일 교체는 이미 원자적으로 끝났으므로 S3 sync 는 백그라운드로 (text-save 와 동일).
    def _rollback_s3_sync():
        try:
            _filebrowser_s3_sync_for_saved_path(target)
        except Exception as e:
            logger.warning("filebrowser base-file/rollback s3 sync error file=%s: %s", target, e)

    threading.Thread(target=_rollback_s3_sync, daemon=True,
                     name="fb-rollback-s3sync").start()
    jsonl_append(PATHS.activity_log, {
        "username": req.username or me.get("username") or "",
        "action": "filebrowser:base-file:rollback",
        "tab": "filebrowser",
        "detail": f"file={req.file} version={clean_version}",
    })
    jsonl_append(PATHS.activity_log, {
        "username": req.username or me.get("username") or "",
        "action": "filebrowser:base-file:rollback",
        "tab": "filebrowser",
        "detail": f"file={req.file} version={clean_version}",
    })
    return {"ok": True, "file": req.file, "rolled_back_to": clean_version, "pre_rollback": pre, "version": applied, "s3_sync": {"status": "pending_background"}}


@router.post("/base-file/migrate-history")
def migrate_base_file_history(req: BaseHistoryMigrateReq, request: Request):
    me = _require_filebrowser_manager(request)
    target = _resolve_base_file_for_version(req.file)
    result = _migrate_legacy_history(
        target,
        req.file,
        actor=req.username or me.get("username") or "",
        note=req.note or "",
    )
    jsonl_append(PATHS.activity_log, {
        "username": req.username or me.get("username") or "",
        "action": "filebrowser:base-file:migrate-history",
        "tab": "filebrowser",
        "detail": f"file={req.file} migrated={result.get('migrated')} skipped={result.get('skipped')}",
    })
    return {"ok": True, "file": req.file, **result}


def _schema_source_id(req: SchemaSnapshotReq) -> str:
    parts = [
        str(req.source_type or "").strip() or "source",
        str(req.root or "").strip(),
        str(req.product or "").strip(),
        str(req.file or "").strip(),
    ]
    raw = "::".join(p for p in parts if p)
    return re.sub(r"[^A-Za-z0-9_.-]+", "__", raw).strip("._-")[:180] or "source"


def _schema_diff(current_cols: list[str], previous_cols: list[str]) -> dict:
    cur = [str(c) for c in current_cols if str(c).strip()]
    prev = [str(c) for c in previous_cols if str(c).strip()]
    prev_set = set(prev)
    cur_set = set(cur)
    return {
        "added_columns": [c for c in cur if c not in prev_set],
        "removed_columns": [c for c in prev if c not in cur_set],
        "column_count_delta": len(cur) - len(prev),
        "unchanged": cur == prev,
    }


def _schema_snapshot_diff(current: dict | None, previous: dict | None) -> dict:
    current = current or {}
    previous = previous or {}
    diff = _schema_diff(current.get("columns", []) or [], previous.get("columns", []) or [])
    cur_dtypes = current.get("dtypes") if isinstance(current.get("dtypes"), dict) else {}
    prev_dtypes = previous.get("dtypes") if isinstance(previous.get("dtypes"), dict) else {}
    common = [c for c in (current.get("columns") or []) if c in prev_dtypes]
    diff["dtype_changes"] = [
        {"column": c, "before": prev_dtypes.get(c), "after": cur_dtypes.get(c)}
        for c in common
        if cur_dtypes.get(c) is not None and prev_dtypes.get(c) is not None and cur_dtypes.get(c) != prev_dtypes.get(c)
    ]
    cur_keys = [str(x) for x in (current.get("join_keys") or [])]
    prev_keys = [str(x) for x in (previous.get("join_keys") or [])]
    diff["added_join_keys"] = [k for k in cur_keys if k not in set(prev_keys)]
    diff["removed_join_keys"] = [k for k in prev_keys if k not in set(cur_keys)]
    diff["grain_changed"] = bool(previous and str(current.get("grain") or "") != str(previous.get("grain") or ""))
    diff["unchanged"] = bool(
        diff.get("unchanged")
        and not diff["dtype_changes"]
        and not diff["added_join_keys"]
        and not diff["removed_join_keys"]
        and not diff["grain_changed"]
    )
    return diff


@router.post("/schema/snapshot")
def save_schema_snapshot(req: SchemaSnapshotReq, request: Request):
    from core.auth import current_user
    me = current_user(request)
    cols = []
    seen = set()
    for col in req.columns or []:
        name = str(col or "").strip()
        if name and name not in seen:
            seen.add(name)
            cols.append(name)
    if not cols:
        raise HTTPException(400, "columns are required")
    sid = _schema_source_id(req)
    SCHEMA_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    fp = SCHEMA_PROFILE_DIR / f"{sid}.json"
    try:
        payload = json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else {"source_id": sid, "snapshots": []}
    except Exception:
        payload = {"source_id": sid, "snapshots": []}
    snapshots = payload.get("snapshots") if isinstance(payload.get("snapshots"), list) else []
    previous = snapshots[0] if snapshots else None
    snap = {
        "schema_version": f"s{len(snapshots) + 1}",
        "source_id": sid,
        "source_type": req.source_type,
        "root": req.root,
        "product": req.product,
        "file": req.file,
        "columns": cols,
        "dtypes": {str(k): str(v) for k, v in (req.dtypes or {}).items() if str(k).strip()},
        "grain": req.grain,
        "join_keys": [str(k) for k in (req.join_keys or []) if str(k).strip()],
        "column_count": len(cols),
        "total_rows": req.total_rows,
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "actor": req.username or me.get("username") or "",
        "note": req.note or "",
        "checksum": "sha256:" + hashlib.sha256("\n".join(cols).encode("utf-8")).hexdigest(),
    }
    diff = _schema_snapshot_diff(snap, previous if isinstance(previous, dict) else None)
    payload["source_id"] = sid
    payload["snapshots"] = [snap] + snapshots[: SCHEMA_PROFILE_CAP - 1]
    _write_text_atomic(fp, json.dumps(payload, ensure_ascii=False, indent=2))
    jsonl_append(PATHS.activity_log, {
        "username": req.username or me.get("username") or "",
        "action": "filebrowser:schema:snapshot",
        "tab": "filebrowser",
        "detail": f"source={sid} columns={len(cols)} added={len(diff['added_columns'])} removed={len(diff['removed_columns'])}",
    })
    return {"ok": True, "source_id": sid, "snapshot": snap, "previous": previous, "diff": diff, "count": len(payload["snapshots"])}


@router.get("/schema/snapshots")
def schema_snapshots(
    request: Request,
    source_type: str = Query(""),
    root: str = Query(""),
    product: str = Query(""),
    file: str = Query(""),
):
    from core.auth import current_user
    current_user(request)
    req = SchemaSnapshotReq(source_type=source_type, root=root, product=product, file=file)
    sid = _schema_source_id(req)
    fp = SCHEMA_PROFILE_DIR / f"{sid}.json"
    try:
        payload = json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else {"source_id": sid, "snapshots": []}
    except Exception:
        payload = {"source_id": sid, "snapshots": []}
    snapshots = payload.get("snapshots") if isinstance(payload.get("snapshots"), list) else []
    latest = snapshots[0] if snapshots else None
    previous = snapshots[1] if len(snapshots) > 1 else None
    diff = _schema_snapshot_diff(latest if isinstance(latest, dict) else None, previous if isinstance(previous, dict) else None)
    return {"ok": True, "source_id": sid, "snapshots": snapshots, "latest": latest, "previous": previous, "diff": diff}


@router.post("/base-file/delete")
def delete_base_file(req: BaseDeleteReq, request: Request):
    """Delete only Files/upload single files. DB root is read-only for everyone."""
    me = _require_filebrowser_manager(request)
    name = (req.file or "").strip()
    if not name or "/" in name or "\\" in name or ".." in name or name.startswith("."):
        raise HTTPException(400, "Invalid filename")

    allowed_ext = {".csv", ".json", ".txt"}
    host_root = PATHS.upload_dir
    fp = (host_root / name).resolve()
    try:
        fp.relative_to(host_root.resolve())
    except ValueError:
        raise HTTPException(400, "Invalid filename")
    if not fp.is_file():
        raise HTTPException(404, f"Not found in Files uploads: {name}")
    if fp.suffix.lower() not in allowed_ext:
        raise HTTPException(400, f"Unsupported file type: {fp.suffix}")

    try:
        trash = host_root / ".trash"
        trash.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        archived = trash / f"{ts}_{name}"
        fp.rename(archived)
        logger.info(f"base-file/delete uploads: {name} → {archived} (by {me.get('username')})")
        jsonl_append(PATHS.activity_log, {
            "username": me.get("username") or "",
            "action": "filebrowser:base-file:delete",
            "tab": "filebrowser",
            "detail": f"file={name}",
        })
        return {"ok": True, "file": name, "archived": str(archived), "host": host_root.name}
    except Exception as e:
        raise HTTPException(500, f"Delete failed: {e}")


@router.get("/sql-guide")
def sql_guide():
    return {"examples": [
        {"desc": "표시 열 + 조건", "sql": "SELECT lot_id, wafer_id WHERE root_lot_id = 'A1000'"},
        {"desc": "표시 열 + 조건 + 정렬", "sql": "SELECT lot_id, wafer_id WHERE item_id = 'IOFF' ORDER BY value DESC"},
        {"desc": "Equal", "sql": "root_lot_id = 'A1000'"},
        {"desc": "LIKE", "sql": "lot_id LIKE '%A1000%'"},
        {"desc": "NOT LIKE", "sql": "step_id NOT LIKE '%TEST%'"},
        {"desc": "IN", "sql": "item_id IN ('IOFF', 'ION')"},
        {"desc": "AND", "sql": "wafer_id = 3 AND item_id = 'IOFF'"},
        {"desc": "BETWEEN", "sql": "value BETWEEN 0.1 AND 0.9"},
        {"desc": "CAST 숫자 비교", "sql": "CAST(value AS DOUBLE) >= 10"},
        {"desc": "CAST 시간 비교", "sql": "CAST(tkout_time AS TIMESTAMP) >= '2024-04-21'"},
        {"desc": "IS NOT NULL", "sql": "tkout_time IS NOT NULL"},
    ]}
