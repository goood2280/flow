@router.get("/roots")
def list_roots(request: Request = None, all: bool = Query(False), fast: bool = Query(False)):
    """v7.1: only canonical whitelisted DBs (FAB/VM/MASK/KNOB/INLINE/ET/YLD/ML_TABLE).

    Pass ?all=1 to bypass the whitelist (admin diagnostics).

    v8.7.6 fix: hive/flat 파티션 구조를 가진 임의 디렉토리도 DB 섹션에 노출.
    판단 규칙 — 디렉토리 자체 또는 하위에 parquet/csv 데이터 파일이 존재하면
    whitelist 바깥이어도 DB 로 간주. 루트의 단일 파일은 (신규 정책) Base 섹션에서만 보여줌.
    """
    _require_filebrowser_user(request)
    from core.utils import detect_structure
    from core.domain import is_visible_root, is_visible_file, canonical_name, DB_REGISTRY
    result = []
    DB_BASE = _db_root()
    if not DB_BASE.exists():
        return {"roots": []}
    settings = _load_filebrowser_settings()
    hidden_db_dirs = _hidden_db_dir_names(settings)
    db_name_aliases = _discovered_db_name_aliases(settings)
    cache_key = (
        "roots", bool(all), bool(fast), _path_sig(DB_BASE),
        tuple(sorted(hidden_db_dirs)), tuple(sorted(db_name_aliases.items())),
    )
    cached = _list_cache_get(cache_key)
    if cached is not None:
        return cached
    for d in sorted(DB_BASE.iterdir()):
        # v8.1.2: explicit file skip — root-level single files go via Base only (v8.7.6).
        if not d.is_dir():
            continue
        if d.name.casefold() in hidden_db_dirs or _is_filebrowser_hidden_dir_name(d.name):
            continue
        whitelisted = is_visible_root(d.name)
        # `1.RAWDATA_DB_*` is the physical DB naming contract used by the
        # folder-display settings (for example `1.RAWDATA_DB_MSR` -> `MSR`).
        # Keep such roots visible even while they are empty, mounted late, or
        # contain a data extension that the bounded parquet/csv probe does not
        # recognise yet.  Previously the settings panel discovered the folder
        # but the non-fast DB inventory dropped it again when file_count == 0.
        named_raw_db = bool(_RAW_DB_DISPLAY_RE.fullmatch(d.name))
        # Fast mode is the first-paint inventory: every allowed top-level
        # directory is shown without walking its products/partitions.
        file_count = 0 if fast else count_data_files(d, limit=2000)
        if not all and not whitelisted and not named_raw_db and file_count == 0:
            if not fast or not _looks_like_db_root_fast(d):
                continue
        canon = canonical_name(d.name) if whitelisted else d.name
        meta = DB_REGISTRY.get(canon, {}) if whitelisted else {}
        structure = "directory"
        if not fast:
            try:
                for sub in d.iterdir():
                    if sub.is_dir():
                        structure = detect_structure(sub)
                        break
            except Exception:
                pass
        # v8.7.6: parquet 이 루트 직속에만 있어도 flat/hive 로 간주 → DB 노드로 노출
        if not fast and structure == "directory" and file_count > 0:
            structure = detect_structure(d) or "flat"
        result.append({
            "name": d.name,
            "display_name": _db_display_name(d.name, settings),
            "canonical": canon,
            "level": meta.get("level", ""),
            "granularity": meta.get("granularity", ""),
            "icon": meta.get("icon", ""),
            "description": meta.get("description", "") if whitelisted else "(auto-detected hive/flat)",
            "path": str(d),
            "structure": structure,
            "dir_count": 0 if fast else sum(1 for x in d.iterdir() if x.is_dir()),
            "parquet_count": file_count,
            "parquet_count_estimated": False,
            "metadata_deferred": bool(fast),
            "whitelisted": whitelisted,
        })
    split_root = DB_BASE / "cache" / "split_table"
    if split_root.is_dir() and any(d.is_dir() and d.name.startswith("ML_TABLE_") for d in split_root.iterdir()):
        result.append({
            "name": "SPLITTABLE",
            "display_name": "SplitTable",
            "canonical": "ML_TABLE",
            "level": "wide",
            "granularity": "wafer",
            "icon": "▦",
            "description": "제품별 SplitTable pivot cache (read-only)",
            "path": str(split_root),
            "structure": "virtual",
            "dir_count": 0,
            "parquet_count": 0,
            "parquet_count_estimated": True,
            "metadata_deferred": bool(fast),
            "whitelisted": True,
        })
    # v8.1.1: root-level single files are now served ONLY by /root-parquets (sidebar "Root Parquets" section).
    # Keeping them here caused duplication with the DB list section.
    # Sort: directories first by level (L0→L3→wide), then rulebooks
    level_order = {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "wide": 4, "rulebook": 5, "": 6}
    result.sort(key=lambda r: (level_order.get(r.get("level", ""), 99), r["name"]))
    return _list_cache_set(cache_key, {"roots": result})


@router.get("/scopes")
def list_scopes(request: Request = None):
    """v4.1: Enumerate top-level data scopes for the sidebar switcher.

    Returns `DB` (Hive-flat source tree) and `Files` (DB root-level files).
    The API key remains "Base" for frontend compatibility.
    """
    _require_filebrowser_user(request)
    scopes = []
    db_root = _db_root()
    scopes.append({
        "key": "DB",
        "label": "DB",
        "description": "Hive-flat source tree — FAB/VM/MASK/KNOB/INLINE/ET/YLD + wafer_maps",
        "path": str(db_root),
        "exists": db_root.is_dir(),
        "icon": "🗄️",
    })
    base_root = _base_root()
    scopes.append({
        "key": "Base",
        "label": "Files",
        "description": "DB root-level single files (rulebooks / ML_TABLE / features)",
        "path": str(base_root),
        "exists": base_root.is_dir(),
        "icon": "📚",
    })
    return {"scopes": scopes}


@router.get("/scopes/roots")
def list_scope_roots(request: Request = None):
    """Backward-compat path for clients calling `/scopes/roots`.

    Some mobile/automation callers still target this legacy route shape. Keep it
    aligned with `/roots` behavior to avoid 404 regressions while preserving the
    newer API surface.
    """
    return list_roots(request=request)


class CacheMatchRefreshReq(BaseModel):
    target: str = "lot_progress"
    product: str = ""
    source_root: str = ""
    force: bool = True


class ChartBuilderSourceReq(BaseModel):
    id: str = ""
    root: str
    product: str
    sql: str = ""
    select_cols: str = ""
    apply_reformatter: bool = False
    reformatter_items: str = ""
    runtime_recent_days: int = 0
    runtime_date_column: str = "tkout_time"
    runtime_root_lot_ids: list[str] = []
    runtime_wafer_ids: list[str] = []
    runtime_lot_wafer_pairs: list[dict[str, str]] = []


class ChartBuilderJoinReq(BaseModel):
    left: str
    right: str
    left_on: str
    right_on: str
    how: str = "left"


class ChartBuilderRunReq(BaseModel):
    sources: list[ChartBuilderSourceReq]
    joins: list[ChartBuilderJoinReq] = []
    max_rows: int = 10000
    chart: dict = {}
    chart_name: str = ""
    save_history: bool = True


class ChartBuilderDefinitionReq(BaseModel):
    code: str


class ChartBuilderAssistantReq(BaseModel):
    instruction: str
    definition_code: str
    columns: list[str] = []


class CacheMatchSettingsReq(BaseModel):
    target: str = "lot_progress"
    interval_minutes: int = 30
    auto_s3_upload_on_save: bool | None = None
    source_root: str | None = None
    column_mapping: dict | None = None


class CacheLlmRefreshReq(BaseModel):
    prompt: str = ""
    product: str = ""
    source_root: str = ""
    force: bool = True


class CacheCleanupReq(BaseModel):
    paths: list[str] = []


class MlTableLookupReq(BaseModel):
    file: str = ""
    product: str = ""
    root_lot_id: str = ""
    select_cols: list[str] | str = []
    wafer_id: str = ""


def _cache_match_target(raw: str) -> str:
    target = str(raw or "").strip().lower()
    if target in {
        "lot_progress", "progress", "latest", "latest_lot",
        "latest_lot_by_root_wafer", "lot_progress_latest_lot_by_root_wafer",
        "current_lot", "current_step", "lot_wf", "lot_wf_current",
    }:
        return "lot_progress"
    raise HTTPException(400, "Only lot_progress cache is supported in FileBrowser.")


def _cache_settings_file() -> Path:
    return PATHS.data_root / "settings.json"


def _lot_progress_source_root_setting() -> str:
    current = load_json(_cache_settings_file(), {})
    if not isinstance(current, dict):
        return ""
    try:
        from core import lot_progress_cache as _lot_progress_cache
        key = getattr(_lot_progress_cache, "SOURCE_ROOT_SETTING_KEY", "lot_progress_source_root")
        return _lot_progress_cache.normalize_lot_progress_source_root(current.get(key, ""))
    except Exception:
        return _cache_safe_text(current.get("lot_progress_source_root", ""), 160)


def _lot_progress_column_mapping_setting() -> dict:
    current = load_json(_cache_settings_file(), {})
    if not isinstance(current, dict):
        current = {}
    try:
        from core import lot_progress_cache as _lot_progress_cache
        key = getattr(_lot_progress_cache, "COLUMN_MAPPING_SETTING_KEY", "lot_progress_column_mapping")
        return _lot_progress_cache.normalize_lot_progress_column_mapping(current.get(key))
    except Exception:
        defaults = {
            "root_lot_id": "root_lot_id",
            "lot_id": "lot_id",
            "wafer_id": "wafer_id",
            "step_id": "step_id",
            "process_id": "process_id",
            "tkin_time": "tkin_time",
            "tkout_time": "tkout_time",
            "time": "time",
            "update_time": "update_time",
            "eqp_id": "eqp_id",
            "chamber_id": "chamber_id",
            "ppid": "ppid",
        }
        raw = current.get("lot_progress_column_mapping")
        if not isinstance(raw, dict):
            raw = {}
        return {key: _cache_safe_text(raw.get(key) or value, 120) for key, value in defaults.items()}


def _lot_progress_metadata() -> dict:
    try:
        from core import lot_progress_cache as _lot_progress_cache
        meta = dict(_lot_progress_cache.metadata())
        column_mapping = _lot_progress_column_mapping_setting()
        meta["column_mapping"] = column_mapping
        meta["lot_id_source_column"] = column_mapping.get("lot_id", "lot_id")
        meta["root_lot_id_source_column"] = column_mapping.get("root_lot_id", "root_lot_id")
        meta["wafer_id_source_column"] = f"{column_mapping.get('wafer_id', 'wafer_id')} (normalized, e.g. W01/#01 -> 1)"
        meta.setdefault("column_mapping_setting", "settings.json.lot_progress_column_mapping")
        meta.setdefault("column_mapping_defaults", column_mapping)
        manual_points = dict(meta.get("manual_change_points") or {})
        manual_points.setdefault("column_mapping", "settings.json.lot_progress_column_mapping")
        meta["manual_change_points"] = manual_points
        return meta
    except Exception:
        default_column_mapping = {
            "root_lot_id": "root_lot_id",
            "lot_id": "lot_id",
            "wafer_id": "wafer_id",
            "step_id": "step_id",
            "process_id": "process_id",
            "tkin_time": "tkin_time",
            "tkout_time": "tkout_time",
            "time": "time",
            "update_time": "update_time",
            "eqp_id": "eqp_id",
            "chamber_id": "chamber_id",
            "ppid": "ppid",
        }
        return {
            "product_binding": {
                "rule": "product는 FAB DB root 바로 아래 제품 폴더명으로 고정합니다.",
                "example_path_shape": "<db_root>/<effective_db_root>/<product>/.../*.parquet",
                "source_column": "product_dir.name",
                "code_location": "backend/core/lot_progress_cache.py product folder rule",
            },
            "latest_key_columns": ["product", "LOT_WF(root_lot_id + wafer_id)"],
            "latest_order_columns": ["update_time", "tkout_time", "tkin_time", "time"],
            "lot_id_source_column": "lot_id",
            "root_lot_id_source_column": "root_lot_id",
            "wafer_id_source_column": "wafer_id (normalized, e.g. W01/#01 -> 1)",
            "column_mapping_setting": "settings.json.lot_progress_column_mapping",
            "column_mapping": default_column_mapping,
            "column_mapping_defaults": default_column_mapping,
            "step_mapping_sources": [
                "Vehicle_matching.csv",
                "vehicle_matching.csv",
                "step_matching.csv",
                "matching_step.csv",
                "step_function.csv",
            ],
            "manual_change_points": {
                "db_root": "settings.json.lot_progress_source_root",
                "column_mapping": "settings.json.lot_progress_column_mapping",
                "product_binding": "backend/core/lot_progress_cache.py product folder rule",
                "latest_rule": "backend/core/lot_progress_cache.py _sort_time and latest key creation",
                "step_mapping": "root-level matching CSV files",
            },
        }


def _canonical_lot_progress_metadata() -> dict:
    meta = dict(_lot_progress_metadata())
    meta["product_binding"] = {
        "rule": "ML_TABLE_<PRODUCT> 파일명을 <PRODUCT>로 정규화하고 SplitTable FAB 매칭 행만 기록합니다.",
        "example_path_shape": "<db_root>/ML_TABLE_<PRODUCT>.parquet",
        "source_column": "normalized ML_TABLE file stem",
        "code_location": "backend/routers/splittable.py export_latest_lot_step_cache",
    }
    manual = dict(meta.get("manual_change_points") or {})
    manual["product_binding"] = "SplitTable ML_TABLE product discovery"
    manual["latest_rule"] = "format v2; one row per product + root_lot_id + wafer_id"
    meta["manual_change_points"] = manual
    return meta


def _clamp_lot_progress_interval(value) -> int:
    try:
        from core import lot_progress_cache as _lot_progress_cache
        lo = int(getattr(_lot_progress_cache, "CACHE_REFRESH_MINUTES_MIN", 1))
        hi = int(getattr(_lot_progress_cache, "CACHE_REFRESH_MINUTES_MAX", 1440))
        default = int(getattr(_lot_progress_cache, "CACHE_REFRESH_MINUTES_DEFAULT", 30))
    except Exception:
        lo, hi, default = 1, 1440, 30
    try:
        minutes = int(value)
    except Exception:
        minutes = default
    return max(lo, min(hi, minutes))


def _cache_safe_text(value, max_len: int = 160) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"[\x00\r\n]+", " ", text)
    return text[:max(1, max_len)].strip()


def _cache_mtime_iso(fp: Path) -> str:
    try:
        if fp.is_file():
            return datetime.datetime.fromtimestamp(fp.stat().st_mtime).isoformat(timespec="seconds")
    except Exception:
        pass
    return ""


def _lot_progress_cache_status() -> dict:
    from core import lot_progress_cache as _lot_progress_cache
    from routers import splittable as _splittable

    json_fp = _lot_progress_cache.cache_file()
    parquet_fp = _lot_progress_cache.filebrowser_cache_parquet_file()
    legacy_parquet_fp = _lot_progress_cache.cache_parquet_file()
    core_status = _lot_progress_cache.cache_status()
    canonical_status = _splittable._latest_lot_step_cache_status("")
    cache_metadata = _canonical_lot_progress_metadata()
    configured_source_root = _lot_progress_source_root_setting() or str(core_status.get("configured_source_root") or "")
    row_count = int(canonical_status.get("row_count") or 0)
    products = [str(v) for v in (canonical_status.get("products") or []) if str(v or "").strip()][:500]
    updated_at = str(canonical_status.get("updated_at") or canonical_status.get("latest_updated_at") or "")
    if not updated_at:
        updated_at = _cache_mtime_iso(parquet_fp) or _cache_mtime_iso(json_fp)
    interval_minutes = int(core_status.get("interval_minutes") or _lot_progress_cache.lot_progress_cache_refresh_minutes())
    next_refresh_at = ""
    if updated_at:
        try:
            next_refresh_at = (
                datetime.datetime.fromisoformat(updated_at)
                + datetime.timedelta(minutes=interval_minutes)
            ).isoformat(timespec="seconds")
        except Exception:
            next_refresh_at = ""
    return {
        "ok": True,
        "target": "lot_progress",
        "mode": "scheduled",
        "unit_action": "filebrowser.cache.lot_progress.status",
        "enabled": True,
        "manual_enabled": True,
        "schedule_enabled": True,
        "scheduler_enabled": bool(core_status.get("scheduler_started")),
        "interval_minutes": interval_minutes,
        "interval_min": interval_minutes,
        "interval_min_minutes": int(getattr(_lot_progress_cache, "CACHE_REFRESH_MINUTES_MIN", 1)),
        "interval_max_minutes": int(getattr(_lot_progress_cache, "CACHE_REFRESH_MINUTES_MAX", 1440)),
        "next_refresh_at": next_refresh_at,
        "cache_path": str(parquet_fp),
        "json_cache_path": str(json_fp),
        "legacy_parquet_path": str(legacy_parquet_fp),
        "cache_exists": parquet_fp.is_file(),
        "format_version": int(canonical_status.get("format_version") or 0),
        "expected_format_version": _CANONICAL_LOT_PROGRESS_CACHE_FORMAT_VERSION,
        "format_current": bool(canonical_status.get("format_current")),
        "cache_source": canonical_status.get("cache_source") or "",
        "canonical_owned_by": "splittable_match_cache",
        "configured_source_root": configured_source_root,
        "source_root": core_status.get("source_root") or "",
        "source_roots": list(core_status.get("source_roots") or []),
        "effective_source_roots": list(core_status.get("effective_source_roots") or core_status.get("source_roots") or []),
        "source_root_candidates": list(core_status.get("source_root_candidates") or []),
        "fab_roots": list(core_status.get("fab_roots") or []),
        "row_count": row_count,
        "total_row_count": row_count,
        "products": products,
        "product_count": len(products),
        "updated_at": updated_at,
        "latest_updated_at": updated_at,
        "last_success_at": canonical_status.get("updated_at") or "",
        "last_attempt_at": core_status.get("last_attempt_at") or "",
        "freshness_state": "ok" if canonical_status.get("format_current") else "legacy_ignored",
        "refresh_log_path": core_status.get("refresh_log_path") or "",
        "lock_state": core_status.get("lock_state") or {},
        "running": bool(core_status.get("running")),
        "skipped_by_lock": bool(core_status.get("skipped_by_lock")),
        "files_scanned": int(core_status.get("files_scanned") or 0),
        "rows_seen": int(core_status.get("rows_seen") or 0),
        "auto_s3_upload_on_save": _filebrowser_auto_s3_upload_enabled(),
        **cache_metadata,
    }


def _refresh_filebrowser_cache_target(target: str, *, product: str = "", source_root: str = "",
                                      force: bool = True, reason: str = "filebrowser") -> dict:
    target = _cache_match_target(target)
    product = _cache_safe_text(product, 120)
    source_root = _cache_safe_text(source_root, 160)
    from core import lot_progress_cache as _lot_progress_cache
    from routers import splittable as _splittable
    match_result = _splittable.refresh_match_cache(product=product, force=bool(force))
    export = dict(match_result.get("latest_cache") or {})
    canonical_status = _splittable._latest_lot_step_cache_status("")
    row_count = int(canonical_status.get("row_count") or export.get("row_count") or 0)
    products = [str(v) for v in (canonical_status.get("products") or export.get("products") or []) if str(v or "").strip()][:500]
    s3_sync = _filebrowser_s3_sync_for_saved_path(_lot_progress_cache.filebrowser_cache_parquet_file())
    return {
        "ok": True,
        "target": "lot_progress",
        "mode": "scheduled",
        "manual_enabled": True,
        "schedule_enabled": True,
        "unit_action": "filebrowser.cache.lot_progress.refresh",
        "row_count": row_count,
        "total_row_count": row_count,
        "products": products,
        "product_count": len(products),
        "updated_at": canonical_status.get("updated_at") or export.get("cache_updated_at") or "",
        "latest_updated_at": canonical_status.get("latest_updated_at") or export.get("cache_updated_at") or "",
        "cache_path": str(_lot_progress_cache.filebrowser_cache_parquet_file()),
        "json_cache_path": str(_lot_progress_cache.cache_file()),
        "legacy_parquet_path": str(_lot_progress_cache.cache_parquet_file()),
        "paths": [str(_lot_progress_cache.filebrowser_cache_parquet_file())],
        "configured_source_root": _lot_progress_source_root_setting(),
        "source_root": "splittable_match_cache",
        "source_roots": [],
        "effective_source_roots": [],
        "source_root_candidates": [],
        "fab_roots": [],
        "files_scanned": 0,
        "rows_seen": row_count,
        "errors": [str(r.get("reason") or "") for r in (export.get("skipped") or []) if r.get("reason")][:20],
        "last_success_at": canonical_status.get("updated_at") or export.get("cache_updated_at") or "",
        "last_attempt_at": canonical_status.get("updated_at") or "",
        "freshness_state": "ok" if canonical_status.get("format_current") else "legacy_ignored",
        "format_version": int(canonical_status.get("format_version") or 0),
        "expected_format_version": _CANONICAL_LOT_PROGRESS_CACHE_FORMAT_VERSION,
        "format_current": bool(canonical_status.get("format_current")),
        "cache_source": canonical_status.get("cache_source") or "",
        "canonical_owned_by": "splittable_match_cache",
        "running": False,
        "skipped_by_lock": False,
        "s3_sync": s3_sync,
        **_canonical_lot_progress_metadata(),
    }


def _cache_llm_json(text: str) -> dict:
    raw = str(text or "").strip()
    if not raw:
        return {}
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I | re.S).strip()
    candidates = [raw]
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        candidates.append(m.group(0))
    for item in candidates:
        try:
            parsed = json.loads(item)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _cache_prompt_target(prompt: str) -> str:
    text = str(prompt or "")
    low = text.lower()
    if any(token in low or token in text for token in (
        "lot_progress", "lot progress", "lot_wf_current", "latest_lot", "latest lot",
        "현재 step", "현재 스텝", "최신 lot", "최신 랏", "진행 캐시",
    )):
        return "lot_progress"
    if "캐시" in text and any(token in low or token in text for token in ("rawdata", "fab", "lot", "랏", "제품")):
        return "lot_progress"
    return ""


def _cache_prompt_source_root(prompt: str) -> str:
    text = str(prompt or "")
    m = re.search(r"1\.RAWDATA_DB(?:_FAB)?", text, flags=re.I)
    return m.group(0) if m else ""


def _normalize_cache_plan_target(raw: str) -> str:
    try:
        return _cache_match_target(raw)
    except HTTPException:
        return ""


def _cache_llm_plan(prompt: str, *, product: str = "", source_root: str = "") -> dict:
    prompt = _cache_safe_text(prompt, 2000)
    product = _cache_safe_text(product, 120)
    source_root = _cache_safe_text(source_root, 160)
    plan: dict = {}
    llm_info = {"available": False, "used": False, "error": ""}
    try:
        from core import llm_adapter
        llm_info["available"] = bool(llm_adapter.is_available())
        if prompt and llm_info["available"]:
            system = _filebrowser_agent_prompt("cache_refresh.system", (
                "You classify a Flow FileBrowser cache refresh request. "
                "Return only JSON. The only allowed target value is: lot_progress. "
                "lot_progress means lot_progress_latest_lot_by_root_wafer. "
                "If no explicit FAB source root is requested, omit source_root so the saved FileBrowser cache setting is used. "
                "Do not invent paths, DB names, or schedules."
            ))
            ask = json.dumps({
                "user_prompt": prompt,
                "product_hint": product,
                "source_root_hint": source_root,
                "schema": {"target": "lot_progress", "product": "optional", "source_root": "optional", "reason": "short"},
            }, ensure_ascii=False)
            out = llm_adapter.complete_json(
                ask,
                system=system,
                timeout=8,
                max_retries=1,
                schema={
                    "keys": ["target", "product", "source_root", "reason"],
                    "required": ["target"],
                    "properties": {"target": {"type": "string"}, "product": {}, "source_root": {}, "reason": {}},
                },
            )
            llm_info["used"] = bool(out.get("ok") and isinstance(out.get("obj"), dict) and out.get("obj"))
            if out.get("error"):
                llm_info["error"] = str(out.get("error") or "")
            if out.get("repaired"):
                llm_info["repaired_json"] = True
            plan = out.get("obj") if isinstance(out.get("obj"), dict) else {}
    except Exception as e:
        llm_info["error"] = f"{type(e).__name__}: {e}"
    target = _normalize_cache_plan_target(str(plan.get("target") or ""))
    fallback_target = _cache_prompt_target(prompt)
    if not target:
        target = fallback_target
    source_root_hint = _cache_prompt_source_root(prompt)
    return {
        "target": target,
        "product": _cache_safe_text(plan.get("product") or product, 120),
        "source_root": _cache_safe_text(plan.get("source_root") or source_root or source_root_hint, 160),
        "reason": _cache_safe_text(plan.get("reason") or ("deterministic fallback" if fallback_target else ""), 240),
        "llm": llm_info,
        "raw_plan": {k: plan.get(k) for k in ("target", "product", "source_root", "reason") if k in plan},
    }


@router.get("/cache/match/status")
def cache_match_status(request: Request, target: str = Query("lot_progress"), product: str = Query(""), source_root: str = Query("")):
    _require_filebrowser_user(request)
    target = _cache_match_target(target)
    return _lot_progress_cache_status()


@router.post("/cache/match/settings")
def cache_match_settings(req: CacheMatchSettingsReq, request: Request):
    me = _require_filebrowser_admin(request)
    target = _cache_match_target(req.target)
    minutes = _clamp_lot_progress_interval(req.interval_minutes)
    settings_path = _cache_settings_file()
    current = load_json(settings_path, {})
    if not isinstance(current, dict):
        current = {}
    current["lot_progress_refresh_minutes"] = minutes
    if req.source_root is not None:
        try:
            from core import lot_progress_cache as _lot_progress_cache
            source_root = _lot_progress_cache.normalize_lot_progress_source_root(req.source_root)
            source_root_key = getattr(_lot_progress_cache, "SOURCE_ROOT_SETTING_KEY", "lot_progress_source_root")
        except Exception:
            source_root = _cache_safe_text(req.source_root, 160)
            source_root_key = "lot_progress_source_root"
        current[source_root_key] = source_root
    if req.column_mapping is not None:
        try:
            from core import lot_progress_cache as _lot_progress_cache
            mapping_key = getattr(_lot_progress_cache, "COLUMN_MAPPING_SETTING_KEY", "lot_progress_column_mapping")
            column_mapping = _lot_progress_cache.normalize_lot_progress_column_mapping(req.column_mapping)
        except Exception:
            mapping_key = "lot_progress_column_mapping"
            defaults = {
                "root_lot_id": "root_lot_id",
                "lot_id": "lot_id",
                "wafer_id": "wafer_id",
                "step_id": "step_id",
                "process_id": "process_id",
                "tkin_time": "tkin_time",
                "tkout_time": "tkout_time",
                "time": "time",
                "update_time": "update_time",
                "eqp_id": "eqp_id",
                "chamber_id": "chamber_id",
                "ppid": "ppid",
            }
            column_mapping = {
                key: _cache_safe_text((req.column_mapping or {}).get(key) or value, 120)
                for key, value in defaults.items()
            }
        current[mapping_key] = column_mapping
    save_json(settings_path, current, indent=2)
    if req.auto_s3_upload_on_save is not None:
        fb_settings = _load_filebrowser_settings()
        fb_settings["auto_s3_upload_on_save"] = bool(req.auto_s3_upload_on_save)
        _save_filebrowser_settings(fb_settings)
    jsonl_append(PATHS.activity_log, {
        "username": me.get("username") or "",
        "action": "filebrowser:cache-settings:save",
        "tab": "filebrowser",
        "detail": f"lot_progress_refresh_minutes={minutes} lot_progress_source_root={current.get('lot_progress_source_root', '')} lot_progress_column_mapping={len(current.get('lot_progress_column_mapping') or {})} auto_s3_upload_on_save={_filebrowser_auto_s3_upload_enabled()}",
    })
    return cache_match_status(request=request, target="lot_progress")


@router.post("/cache/match/refresh")
def cache_match_refresh(req: CacheMatchRefreshReq, request: Request):
    _require_filebrowser_admin(request)
    target = _cache_match_target(req.target)
    return _refresh_filebrowser_cache_target(
        target,
        product=req.product or "",
        source_root=req.source_root or "",
        force=bool(req.force),
        reason="filebrowser",
    )


@router.get("/cache/cleanup-candidates")
def cache_cleanup_candidates(request: Request):
    _require_filebrowser_admin(request)
    return {
        "ok": True,
        "canonical": _CANONICAL_LOT_PROGRESS_CACHE_FILE,
        "candidates": _cache_cleanup_candidates(),
    }


@router.post("/cache/cleanup")
def cache_cleanup(req: CacheCleanupReq, request: Request):
    me = _require_filebrowser_admin(request)
    paths = [str(p or "").strip() for p in (req.paths or []) if str(p or "").strip()]
    if not paths:
        raise HTTPException(400, "paths are required")
    deleted: list[dict] = []
    errors: list[dict] = []
    for raw in paths:
        try:
            target = _resolve_cache_cleanup_path(raw)
            size = target.stat().st_size if target.is_file() else 0
            target.unlink()
            deleted.append({"path": str(target), "size": size})
        except HTTPException:
            raise
        except Exception as exc:
            errors.append({"path": raw, "error": str(exc)})
    try:
        jsonl_append(PATHS.activity_log, {
            "username": me.get("username") or "",
            "action": "filebrowser:cache-cleanup",
            "tab": "filebrowser",
            "detail": f"deleted={len(deleted)} errors={len(errors)}",
        })
    except Exception:
        pass
    return {
        "ok": not errors,
        "deleted": deleted,
        "errors": errors,
        "canonical": _CANONICAL_LOT_PROGRESS_CACHE_FILE,
        "candidates": _cache_cleanup_candidates(),
    }


@router.post("/cache/llm/refresh")
def cache_llm_refresh(req: CacheLlmRefreshReq, request: Request):
    me = _require_filebrowser_admin(request)
    prompt = _cache_safe_text(req.prompt, 2000)
    if not prompt:
        raise HTTPException(400, "prompt is required")
    plan = _cache_llm_plan(prompt, product=req.product or "", source_root=req.source_root or "")
    target = plan.get("target") or ""
    if not target:
        raise HTTPException(400, "LLM/cache prompt must resolve to lot_progress")
    result = _refresh_filebrowser_cache_target(
        target,
        product=plan.get("product") or req.product or "",
        source_root=plan.get("source_root") or req.source_root or "",
        force=bool(req.force),
        reason="filebrowser_llm",
    )
    try:
        jsonl_append(PATHS.activity_log, {
            "username": me.get("username") or "",
            "action": "filebrowser:cache-llm-refresh",
            "tab": "filebrowser",
            "detail": f"target={target} product={plan.get('product') or ''}",
        })
    except Exception:
        pass
    return {
        **result,
        "ok": bool(result.get("ok", True)),
        "unit_action": "filebrowser.cache.llm.refresh",
        "target": target,
        "plan": plan,
        "llm": plan.get("llm") or {},
        "result": result,
    }


def _resolve_ml_table_lookup_file(product: str = "", file: str = "") -> Path:
    fp = _ml_table_lookup.resolve_ml_table_file(product=product, file=file)
    if fp is None:
        target = file or product
        raise HTTPException(404, f"ML_TABLE parquet not found: {target}")
    return fp


@router.get("/ml-table/lookup-status")
def ml_table_lookup_status(
    request: Request,
    product: str = Query(""),
    file: str = Query(""),
):
    _require_filebrowser_user(request)
    fp = _resolve_ml_table_lookup_file(product=product, file=file)
    return _ml_table_lookup.cache_status(fp)


@router.post("/ml-table/lookup")
def ml_table_root_lot_lookup(req: MlTableLookupReq, request: Request):
    """Cache-first ML_TABLE root_lot_id lookup.

    Cold cache calls return readiness/build-queue state instead of scanning the
    wide source parquet. When cache exists, only the requested root partition and
    selected columns are read.
    """
    _require_filebrowser_user(request)
    fp = _resolve_ml_table_lookup_file(product=req.product, file=req.file)
    try:
        return _ml_table_lookup.query_root_lot(
            fp,
            req.root_lot_id,
            selected_cols=req.select_cols,
            wafer_id=req.wafer_id,
            enqueue_missing=True,
        )
    except _ml_table_lookup.MlTableLookupError as exc:
        raise HTTPException(400, exc.to_detail())


@router.get("/ml-table/lookup")
def ml_table_root_lot_lookup_get(
    request: Request,
    product: str = Query(""),
    file: str = Query(""),
    root_lot_id: str = Query(""),
    select_cols: str = Query(""),
    wafer_id: str = Query(""),
):
    _require_filebrowser_user(request)
    fp = _resolve_ml_table_lookup_file(product=product, file=file)
    try:
        return _ml_table_lookup.query_root_lot(
            fp,
            root_lot_id,
            selected_cols=select_cols,
            wafer_id=wafer_id,
            enqueue_missing=True,
        )
    except _ml_table_lookup.MlTableLookupError as exc:
        raise HTTPException(400, exc.to_detail())


@router.get("/base-dir")
def base_dir_children(path: str = Query(""), request: Request = None):
    """single-file 폴더(cache 등) **한 칸**의 바로 아래 항목만 나열한다.

    `/base-files` 는 전체 트리를 한 번에 실어 1000개에서 자르므로, 운영 캐시처럼
    제품 × root 파티션이 수만 개인 트리에서는 깊은 곳이 통째로 빠진다(형제 폴더는
    보이는데 열면 비어 있음). 화면이 폴더를 열 때 이 endpoint 로 그 칸만 읽으면
    깊이 제한 없이 parquet 까지 내려갈 수 있다.
    """
    _require_filebrowser_user(request)
    rel = str(path or "").strip()
    if not rel:
        return {"ok": True, "path": "", "entries": [], "truncated": False}
    base_root = _base_root()
    db_root = _db_root()
    settings = _load_filebrowser_settings()
    folder_names = _single_file_folder_names(settings)
    versioned_dirs = _versioned_single_file_dir_names(settings)
    entries: list[dict] = []
    seen: set[str] = set()
    truncated = False
    for root, source_root in ((base_root, "base_root"), (db_root, "db_root")):
        if not root.is_dir():
            continue
        if source_root == "db_root" and db_root == base_root:
            continue
        found, cut = _single_file_dir_children(
            root, source_root, rel,
            versioned_dirs=versioned_dirs, folder_names=folder_names,
        )
        truncated = truncated or cut
        for item in found:
            item = dict(item)
            if item.get("kind") != "dir":
                item["description"] = _file_description_for(item.get("path") or item.get("name") or "", item.get("description") or "", settings)
            key = str(item.get("path") or "").lower()
            if key and key not in seen:
                seen.add(key)
                entries.append(item)
    entries.sort(key=lambda e: (e["kind"] != "dir", str(e["path"]).lower()))
    return {
        "ok": True,
        "path": rel.strip("/").replace("\\", "/"),
        "entries": entries,
        "truncated": truncated,
        "max_entries": _SINGLE_FILE_DIR_MAX_ENTRIES,
    }


def _base_files_fast_payload(base_root: Path, db_root: Path, folder_names: set[str], settings: dict) -> dict:
    """Return only first-level Files inventory; never recurse into folders."""
    files: list[dict] = []
    dirs: list[dict] = []
    seen_files: set[str] = set()
    seen_dirs: set[str] = set()
    roots = [(base_root, "base_root")]
    if db_root != base_root:
        roots.append((db_root, "db_root"))
    for root, source_root in roots:
        if not root.is_dir():
            continue
        try:
            with os.scandir(root) as scanned:
                entries = sorted(scanned, key=lambda entry: entry.name.casefold())
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
                is_file = entry.is_file(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                folded = name.casefold()
                if folded not in folder_names or folded in seen_dirs:
                    continue
                try:
                    modified = entry.stat().st_mtime
                except OSError:
                    modified = 0
                dirs.append({
                    "name": name,
                    "path": name,
                    "size": 0,
                    "modified": modified,
                    "ext": "dir",
                    "kind": "dir",
                    "source": source_root,
                    "role": "directory",
                    "description": "Folder",
                    "order": 99,
                    "children_deferred": True,
                })
                seen_dirs.add(folded)
                continue
            if not is_file:
                continue
            fp = Path(entry.path)
            if not _visible_single_file(fp) or name.casefold() in seen_files:
                continue
            try:
                stat = entry.stat()
            except OSError:
                continue
            meta = _core_file_meta(name)
            files.append({
                "name": name,
                "path": name,
                "size": stat.st_size,
                "modified": stat.st_mtime,
                "ext": fp.suffix.lower().lstrip("."),
                "kind": "file",
                "source": source_root,
                "role": meta["role"],
                "description": meta["description"],
                "order": meta["order"],
            })
            seen_files.add(name.casefold())
    files.sort(key=lambda item: (item.get("order", 999), item["name"].casefold()))
    for item in files:
        item["description"] = _file_description_for(item.get("path") or item.get("name") or "", item.get("description") or "", settings)
    dirs.sort(key=lambda item: item["name"].casefold())
    return {
        "files": dirs + files,
        "dirs": dirs,
        "path": str(base_root) if base_root.is_dir() else "",
        "exists": base_root.is_dir() or db_root.is_dir() or bool(files),
        "metadata_deferred": True,
    }


@router.get("/base-files")
def base_files(request: Request = None, fast: bool = Query(False)):
    """v4.1: List top-level files under the Base root (single-file layout).

    Returns only the operational files needed by the current ML_TABLE workflow:
    ML_TABLE_*.parquet, the small matching CSVs, and product_config/products.yaml.
    Directories and legacy helper files remain on disk but are not surfaced here.
    """
    _require_filebrowser_user(request)
    base_root = _base_root()
    db_root = _db_root()
    settings = _load_filebrowser_settings()
    single_file_folders = _single_file_folder_names(settings)
    versioned_dirs = _versioned_single_file_dir_names(settings)
    description_sig = tuple(sorted((settings.get("file_descriptions") or {}).items()))
    if fast:
        cache_key = (
            "base_files_fast",
            tuple(sorted(single_file_folders)),
            description_sig,
            _path_sig(base_root),
            _path_sig(db_root),
        )
        cached = _list_cache_get(cache_key)
        if cached is not None:
            return cached
        return _list_cache_set(
            cache_key,
            _base_files_fast_payload(base_root, db_root, single_file_folders, settings),
        )
    _ensure_single_file_cache_dirs(base_root, db_root)
    if hasattr(PATHS, "cache_dir") and hasattr(PATHS, "db_cache_dir"):
        try:
            from core import lot_progress_cache as _lot_progress_cache
            import threading
            threading.Thread(target=_lot_progress_cache.export_lot_progress_parquet, daemon=True).start()
        except Exception as e:
            logger.warning("lot-progress parquet cache export start failed: %s", e)
    _refresh_single_file_step_caches(base_root)
    if db_root != base_root:
        _refresh_single_file_step_caches(db_root)
    cache_key = (
        "base_files",
        tuple(sorted(single_file_folders)),
        tuple(sorted(versioned_dirs)),
        description_sig,
        _path_sig(base_root),
        _path_sig(_db_root()),
        _single_file_folder_sigs(base_root, single_file_folders),
        _single_file_folder_sigs(db_root, single_file_folders),
        _path_sig(PATHS.upload_dir),
    )
    cached = _list_cache_get(cache_key)
    if cached is not None:
        return cached
    files, dirs = [], []
    seen_folder_paths: set[str] = set()
    seen_dir_paths: set[str] = set()

    def _add_single_file_folder_entries(root: Path, source_root: str) -> None:
        if not root.is_dir():
            return
        for folder_name in sorted(single_file_folders):
            entries = _single_file_folder_entries(
                root,
                source_root,
                folder_name,
                versioned_dirs=versioned_dirs,
            )
            if not entries:
                continue
            dir_entry = _single_file_folder_dir_entry(root, source_root, folder_name, entries)
            if dir_entry:
                dir_key = str(dir_entry.get("path") or "").lower()
                if dir_key and dir_key not in seen_dir_paths:
                    dirs.append(dir_entry)
                    seen_dir_paths.add(dir_key)
            for entry in entries:
                entry_key = str(entry.get("path") or "").lower()
                if entry_key in seen_folder_paths:
                    continue
                files.append(entry)
                seen_folder_paths.add(entry_key)

    _add_single_file_folder_entries(base_root, "base_root")
    if base_root.is_dir():
        try:
            with os.scandir(base_root) as it:
                for entry in sorted(it, key=lambda e: (not e.is_file(), e.name.lower())):
                    if entry.is_file():
                        fp = Path(entry.path)
                        if not _visible_single_file(fp):
                            continue
                        ext = fp.suffix.lower()
                        meta = _core_file_meta(entry.name)
                        stat = entry.stat()
                        files.append({
                            "name": entry.name,
                            "path": entry.name,
                            "size": stat.st_size,
                            "modified": stat.st_mtime,
                            "ext": ext.lstrip("."),
                            "kind": "file",
                            "source": "base_root",
                            "role": meta["role"],
                            "description": meta["description"],
                            "order": meta["order"],
                        })
                    elif entry.is_dir():
                        dir_name = entry.name
                        if dir_name.startswith(".") or dir_name.startswith("__"):
                            continue
                        # v9.1.x: Files 에는 설정된 폴더(single_file_folders = cache + hidden_db_dirs)만
                        #   최상위 폴더로 노출한다. DB 제품 루트/백업 폴더 등은 Base 목록에서 숨긴다.
                        if dir_name.casefold() not in single_file_folders:
                            continue
                        dir_key = dir_name.lower()
                        if dir_key not in seen_dir_paths:
                            try:
                                stat = entry.stat()
                                dirs.append({
                                    "name": dir_name,
                                    "path": dir_name,
                                    "size": 0,
                                    "modified": stat.st_mtime,
                                    "ext": "dir",
                                    "kind": "dir",
                                    "source": "base_root",
                                    "role": "directory",
                                    "description": "Folder",
                                    "order": 99,
                                })
                                seen_dir_paths.add(dir_key)
                            except OSError:
                                pass
        except OSError:
            pass
    # v8.7.5: DB 루트에 있는 단일 CSV 는 "Base" 로 분류 (물리적 위치와 무관하게 의미적 Base).
    # v8.7.6: 단일 parquet 도 동일 — 폴더(hive/flat) 구조만 DB 섹션에 노출됨.
    # v8.7.7: 같은 파일명이 base_root 와 db_root 양쪽에 있으면 dedup. UI 에 소스 태그
    # (db) 를 노출하던 것도 제거 — 사용자 입장에서 Base 단일 파일은 "한 번만" 보여야 함.
    if db_root.is_dir() and db_root != base_root:
        _add_single_file_folder_entries(db_root, "db_root")
    seen_names = {f["name"].lower() for f in files if f.get("source") != "cache"}
    if db_root.is_dir() and db_root.resolve() != base_root.resolve():
        for f in sorted(db_root.iterdir()):
            if f.is_dir():
                dir_name = f.name
                if dir_name.startswith(".") or dir_name.startswith("__"):
                    continue
                # v9.1.x: 설정된 Files 노출 폴더만 최상위 폴더로 보여준다 (위 base_root 와 동일 규칙).
                if dir_name.casefold() not in single_file_folders:
                    continue
                dir_key = dir_name.lower()
                if dir_key not in seen_dir_paths:
                    try:
                        stat = f.stat()
                        dirs.append({
                            "name": dir_name,
                            "path": dir_name,
                            "size": 0,
                            "modified": stat.st_mtime,
                            "ext": "dir",
                            "kind": "dir",
                            "source": "db_root",
                            "role": "directory",
                            "description": "Folder",
                            "order": 99,
                        })
                        seen_dir_paths.add(dir_key)
                    except OSError:
                        pass
                continue

            if not f.is_file():
                continue
            if not _visible_single_file(f):
                continue
            ext = f.suffix.lower()
            meta = _core_file_meta(f.name)
            if f.name.lower() in seen_names:
                continue
            try:
                stat = f.stat()
            except OSError:
                continue
            files.append({
                "name": f.name,
                "path": f.name,
                "size": stat.st_size,
                "modified": stat.st_mtime,
                "ext": ext.lstrip("."),
                "kind": "file",
                # v8.7.7: source 는 내부적으로만 유지 (preview 라우팅에 필요), UI 태그는 제거.
                "source": "db_root",
                "role": meta["role"],
                "description": meta["description"],
                "order": meta["order"],
            })
            seen_names.add(f.name.lower())
    for item in files:
        item["description"] = _file_description_for(item.get("path") or item.get("name") or "", item.get("description") or "", settings)
    files.sort(key=lambda x: (x.get("order", 999), x["name"].lower()))
    deduped_dirs = {}
    for d in dirs:
        deduped_dirs.setdefault(str(d.get("name") or "").lower(), d)
    dirs = list(deduped_dirs.values())
    dirs.sort(key=lambda x: (x.get("source", ""), x["name"]))
    return _list_cache_set(cache_key, {"files": dirs + files, "dirs": dirs,
            "path": str(base_root) if base_root.is_dir() else "",
            "exists": base_root.is_dir() or bool(files)})
