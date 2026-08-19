def _parse_tab_or_csv(text: str, delimiter: str) -> tuple[list[list[str]], str]:
    normalized = str(text or "")
    if not normalized:
        return [], delimiter

    def _read(d: str, strict: bool = True) -> list[list[str]]:
        try:
            reader = csv.reader(io.StringIO(normalized), delimiter=d, quotechar='"', doublequote=True)
            rows = [list(r) for r in reader]
        except Exception:
            if strict:
                raise
            return []
        while rows and all(str(v or "").strip() == "" for v in rows[-1]):
            rows.pop()
        return rows

    requested = (delimiter or "auto").lower()
    if requested in {"tab", "\t", "\\t"}:
        return _read("\t"), "tab"
    if requested in {"comma", ",", "csv"}:
        return _read(","), "comma"

    # auto: 우선 탭 파서, 실패/의미없는 분리면 CSV 파서로 폴백.
    try:
        tab_rows = _read("\t")
    except Exception:
        tab_rows = []
    if ("\t" in normalized) or any(len(r) > 1 for r in tab_rows):
        return tab_rows, "tab"
    return _read("," , strict=False), "comma"


def _normalize_rows(rows: list[list[str]], width: int, fill: str = "") -> tuple[list[list[str]], int]:
    norm: list[list[str]] = []
    for r in rows:
        rr = ["" if v is None else str(v) for v in (r or [])]
        if len(rr) < width:
            rr = rr + [fill] * (width - len(rr))
        elif len(rr) > width:
            rr = rr[:width]
        norm.append(rr)
    return norm, width


_GENERATED_EXTRA_COL_RE = re.compile(r"^extra_col_\d+$", re.IGNORECASE)


def _csv_header_names(raw_header: list[str], width: int) -> list[str]:
    names: list[str] = []
    seen: dict[str, int] = {}
    for idx in range(width):
        raw = raw_header[idx] if idx < len(raw_header) else ""
        name = str(raw or "").lstrip("\ufeff").strip() or f"col_{idx + 1}"
        base = name
        count = seen.get(base.casefold(), 0)
        if count:
            name = f"{base}_{count + 1}"
        seen[base.casefold()] = count + 1
        names.append(name)
    return names


def _drop_generated_extra_columns(header: list[str], data_rows: list[list[str]]) -> tuple[list[str], list[list[str]], bool]:
    drop = {idx for idx, col in enumerate(header) if _GENERATED_EXTRA_COL_RE.fullmatch(str(col or "").strip())}
    if not drop:
        return header, data_rows, False
    keep = [idx for idx in range(len(header)) if idx not in drop]
    next_header = [header[idx] for idx in keep]
    next_rows = [[row[idx] if idx < len(row) else "" for idx in keep] for row in data_rows]
    return next_header, next_rows, True


def _read_csv_lenient_rows(fp: Path) -> tuple[list[str], list[list[str]], str, bool] | None:
    try:
        if fp.suffix.lower() != ".csv" or fp.stat().st_size > BASE_FILE_EDIT_MAX_BYTES:
            return None
        text = fp.read_text(encoding="utf-8-sig", errors="replace")
    except Exception:
        return None
    rows, used_delim = _parse_tab_or_csv(text, "auto")
    if not rows:
        return [], [], used_delim, False
    raw_header = ["" if v is None else str(v) for v in (rows[0] or [])]
    data_rows = rows[1:]
    data_width = max((len(r or []) for r in data_rows), default=0)
    width = max(len(raw_header), 1)
    columns = _csv_header_names(raw_header, width)
    normalized, _ = _normalize_rows(data_rows, width, "")
    return columns, normalized, used_delim, data_width != width


def _csv_lenient_lazy_frame(fp: Path) -> tuple[pl.LazyFrame, list[str], dict[str, str], int, dict] | None:
    parsed = _read_csv_lenient_rows(fp)
    if parsed is None:
        return None
    columns, rows, used_delim, added_columns = parsed
    data = {col: [row[idx] if idx < len(row) else "" for row in rows] for idx, col in enumerate(columns)}
    df = pl.DataFrame(data if data else {col: pl.Series([], dtype=pl.Utf8) for col in columns})
    if columns:
        df = df.select([pl.col(col).cast(pl.Utf8, strict=False).alias(col) for col in columns])
    schema = {col: "String" for col in columns}
    meta = {
        "csv_schema_reinitialized": True,
        "csv_ragged_rows_normalized": bool(added_columns),
        "csv_delimiter": used_delim,
    }
    return df.lazy(), columns, schema, len(rows), meta


def _resolve_base_file_for_edit(file: str) -> Path:
    name = (file or "").strip()
    if not name:
        raise HTTPException(400, "file is required")
    rel = Path(name)
    if rel.is_absolute() or any(p in {"", ".", ".."} for p in rel.parts):
        raise HTTPException(400, "Invalid file path")
    settings = _load_filebrowser_settings()
    versioned_dirs = _versioned_single_file_dir_names(settings)
    folder_fp = _resolve_single_file_folder_data_path(file, (_base_root(), _db_root()), versioned_dirs)
    if folder_fp is not None:
        return folder_fp
    if rel.parts and str(rel.parts[0]).casefold() in _single_file_folder_names(settings):
        raise HTTPException(400, f"This folder is read-only in File Browser: {rel.parts[0]}")
    if rel.parts and rel.parts[0] in BASE_EDIT_RESERVED_PREFIXES:
        raise HTTPException(400, f"Editing scope mismatch: {rel.parts[0]}/* is not a single Base/DB file")

    base_root = _base_root()
    db_root = _db_root()
    for candidate_root in (base_root, db_root):
        if not candidate_root.is_dir():
            continue
        cand = (candidate_root / rel).resolve()
        try:
            cand.relative_to(candidate_root.resolve())
        except ValueError:
            continue
        if cand.suffix.lower() not in BASE_EDIT_ALLOWED_EXTENSIONS:
            raise HTTPException(400, f"Unsupported file type: {cand.suffix}")
        if cand.is_file():
            return cand

    raise HTTPException(404, f"Base file not found in Base/DB root: {file}")


def _resolve_base_file_for_version(file: str) -> Path:
    name = (file or "").strip()
    if not name:
        raise HTTPException(400, "file is required")
    rel = Path(name)
    if rel.is_absolute() or any(p in {"", ".", ".."} for p in rel.parts):
        raise HTTPException(400, "Invalid file path")
    settings = _load_filebrowser_settings()
    folder_fp = _resolve_single_file_folder_data_path(file, (_base_root(), _db_root()), _single_file_folder_names(settings))
    if folder_fp is not None:
        return folder_fp
    if rel.parts and rel.parts[0] == "product_config":
        if len(rel.parts) != 2 or rel.parts[1].startswith("."):
            raise HTTPException(400, "Invalid product config path")
        root = (PATHS.data_root / "product_config").resolve()
        cand = (root / rel.parts[1]).resolve()
        try:
            cand.relative_to(root)
        except ValueError:
            raise HTTPException(400, "Invalid product config path")
        if cand.is_file() and cand.suffix.lower() in PRODUCT_CONFIG_EXTENSIONS:
            return cand
        raise HTTPException(404, f"Product config not found: {file}")
    if rel.parts and rel.parts[0] == "reformatter":
        if len(rel.parts) != 2 or rel.parts[1].startswith("."):
            raise HTTPException(400, "Invalid reformatter path")
        root = (PATHS.data_root / "reformatter").resolve()
        requested = rel.parts[1]
        candidates = [root / requested]
        if requested.lower().endswith(".csv"):
            candidates.append(root / (Path(requested).stem + ".json"))
        for cand0 in candidates:
            cand = cand0.resolve()
            try:
                cand.relative_to(root)
            except ValueError:
                continue
            if cand.is_file() and cand.suffix.lower() in {".csv", ".json"}:
                return cand
        raise HTTPException(404, f"Reformatter file not found: {file}")
    return _resolve_base_file_for_edit(file)


def _filebrowser_settings_path() -> Path:
    return PATHS.data_root / FILEBROWSER_SETTINGS_FILE


def _filebrowser_agent_prompts_path() -> Path:
    return PATHS.data_root / FILEBROWSER_AGENT_PROMPTS_FILE


def _read_json_file_safe(path: Path) -> dict:
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        logger.warning("json read failed: %s", path)
    return {}


def _deep_merge_dict(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base) if isinstance(base, dict) else {}
    if not isinstance(override, dict):
        return out
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge_dict(out.get(key) or {}, value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _load_filebrowser_agent_prompts() -> dict:
    default = _read_json_file_safe(FILEBROWSER_AGENT_PROMPTS_DEFAULT_FILE)
    runtime = _read_json_file_safe(_filebrowser_agent_prompts_path())
    return _deep_merge_dict(default, runtime)


def _filebrowser_agent_prompt(key: str, fallback: str) -> str:
    cfg = _load_filebrowser_agent_prompts()
    raw = cfg.get(key)
    if raw is None and "." in key:
        section, field = key.split(".", 1)
        node = cfg.get(section)
        if isinstance(node, dict):
            raw = node.get(field)
    text = str(raw or "").strip()
    return text or fallback


def _clean_rule_file_key(file: str) -> str:
    name = str(file or "").strip().replace("\\", "/")
    if not name:
        raise HTTPException(400, "CSV rule file key is required")
    rel = Path(name)
    if rel.is_absolute() or any(p in {"", ".", ".."} for p in rel.parts):
        raise HTTPException(400, f"Invalid CSV rule file key: {file}")
    return "/".join(rel.parts)


def _clean_string_list(value, *, lower: bool = False) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = re.split(r"[,\n]", value)
    elif isinstance(value, (list, tuple, set)):
        raw = list(value)
    else:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item or "").strip()
        if not text:
            continue
        key = text.lower() if lower else text
        if key in seen:
            continue
        seen.add(key)
        out.append(key if lower else text)
    return out


def _normalize_unique_keys(value) -> list[list[str]]:
    if value is None:
        return []
    raw = value
    if isinstance(raw, str):
        raw = [line for line in raw.splitlines() if line.strip()]
    if not isinstance(raw, list):
        return []
    out: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for item in raw:
        if isinstance(item, str):
            cols = _clean_string_list(item)
        elif isinstance(item, (list, tuple)):
            cols = _clean_string_list(list(item))
        elif isinstance(item, dict):
            cols = _clean_string_list(item.get("columns") or item.get("keys") or [])
        else:
            cols = []
        if not cols:
            continue
        key = tuple(cols)
        if key in seen:
            continue
        seen.add(key)
        out.append(cols)
    return out


def _normalize_enums(value) -> dict[str, list[str]]:
    if not value:
        return {}
    raw: dict = {}
    if isinstance(value, dict):
        raw = value
    elif isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            col = str(item.get("column") or "").strip()
            if col:
                raw[col] = item.get("values") or item.get("allowed") or []
    out: dict[str, list[str]] = {}
    for col, vals in raw.items():
        name = str(col or "").strip()
        if not name:
            continue
        if isinstance(vals, str):
            allowed = [v.strip() for v in re.split(r"[|,\n]", vals) if v.strip()]
        elif isinstance(vals, (list, tuple, set)):
            allowed = [str(v).strip() for v in vals if str(v).strip()]
        else:
            allowed = []
        if allowed:
            out[name] = allowed
    return out


def _normalize_numeric(value) -> dict[str, dict]:
    if not value:
        return {}
    raw: dict = {}
    if isinstance(value, dict):
        raw = value
    elif isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            col = str(item.get("column") or "").strip()
            if col:
                raw[col] = item
    out: dict[str, dict] = {}
    for col, spec in raw.items():
        name = str(col or "").strip()
        if not name:
            continue
        if not isinstance(spec, dict):
            spec = {}
        clean: dict = {}
        for key in ("min", "max"):
            raw_v = spec.get(key)
            if raw_v in (None, ""):
                continue
            try:
                clean[key] = float(raw_v)
            except Exception:
                raise HTTPException(400, f"Invalid numeric.{name}.{key}: {raw_v}")
        if bool(spec.get("integer")):
            clean["integer"] = True
        out[name] = clean
    return out


def _normalize_regex(value) -> dict[str, str]:
    if not value:
        return {}
    raw: dict = {}
    if isinstance(value, dict):
        raw = value
    elif isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            col = str(item.get("column") or "").strip()
            if col:
                raw[col] = item.get("pattern") or item.get("regex") or ""
    out: dict[str, str] = {}
    for col, pattern in raw.items():
        name = str(col or "").strip()
        pat = str(pattern.get("pattern") if isinstance(pattern, dict) else pattern or "").strip()
        if not name or not pat:
            continue
        try:
            re.compile(pat)
        except re.error as e:
            raise HTTPException(400, f"Invalid regex for {name}: {e}")
        out[name] = pat
    return out


def _normalize_date_columns(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, dict):
        return _clean_string_list(list(value.keys()))
    if isinstance(value, list):
        cols = []
        for item in value:
            if isinstance(item, dict):
                cols.append(item.get("column") or item.get("col") or "")
            else:
                cols.append(item)
        return _clean_string_list(cols)
    return _clean_string_list(value)


def _normalize_conditions(value) -> list[dict]:
    if not value:
        return []
    raw = value
    if isinstance(raw, str):
        raw = [line for line in raw.splitlines() if line.strip()]
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if isinstance(item, str):
            expr, msg = item, ""
        elif isinstance(item, dict):
            expr = item.get("expr") or item.get("where") or item.get("selector") or ""
            msg = item.get("message") or item.get("label") or ""
        else:
            continue
        expr = str(expr or "").strip()
        if not expr:
            continue
        if ";" in expr or "__" in expr:
            raise HTTPException(400, f"Unsafe condition expression: {expr}")
        try:
            pl.sql_expr(expr)
        except Exception as e:
            raise HTTPException(400, f"Invalid condition expression '{expr}': {e}")
        out.append({"expr": expr, "message": str(msg or "").strip()})
    return out


_ORDER_SPEC_TYPES = {"string", "text", "numeric", "number", "integer", "date", "datetime", "leading_number", "rule_order"}


def _normalize_order_specs(value, *, label: str) -> list[dict]:
    if not value:
        return []
    raw = value
    if isinstance(raw, str):
        raw = [line for line in raw.splitlines() if line.strip()]
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if isinstance(item, str):
            parts = [p for p in re.split(r"[\s,]+", item.strip()) if p]
            spec = {
                "column": parts[0] if len(parts) >= 1 else "",
                "direction": parts[1] if len(parts) >= 2 else "asc",
                "type": parts[2] if len(parts) >= 3 else "string",
                "nulls": parts[3] if len(parts) >= 4 else "last",
            }
        elif isinstance(item, dict):
            spec = item
        else:
            continue
        col = str(spec.get("column") or spec.get("col") or "").strip()
        if not col:
            continue
        direction = str(spec.get("direction") or spec.get("dir") or "asc").strip().lower()
        typ = str(spec.get("type") or "string").strip().lower()
        nulls = str(spec.get("nulls") or "last").strip().lower()
        if direction not in {"asc", "ascending", "desc", "descending"}:
            raise HTTPException(400, f"Invalid {label} direction for {col}: {direction}")
        if typ not in _ORDER_SPEC_TYPES:
            raise HTTPException(400, f"Invalid {label} type for {col}: {typ}")
        if nulls not in {"first", "last", "nulls_first", "nulls_last"}:
            raise HTTPException(400, f"Invalid {label} nulls for {col}: {nulls}")
        if typ in {"number", "integer"}:
            typ = "numeric"
        elif typ == "datetime":
            typ = "date"
        elif typ == "text":
            typ = "string"
        out.append({
            "column": col,
            "direction": "desc" if direction.startswith("desc") else "asc",
            "type": typ,
            "nulls": "first" if nulls.endswith("first") else "last",
        })
    return out


def _normalize_sort(value) -> list[dict]:
    return _normalize_order_specs(value, label="sort")


def _normalize_ordered_by(value) -> dict:
    if not value:
        return {}
    if isinstance(value, dict):
        specs = _normalize_order_specs(
            value.get("keys") or value.get("sort") or value.get("order") or [],
            label="ordered_by",
        )
        group_by = _clean_string_list(value.get("group_by") or value.get("groups"))
    else:
        specs = _normalize_order_specs(value, label="ordered_by")
        group_by = []
    if not specs:
        return {}
    out = {"keys": specs}
    if group_by:
        out["group_by"] = group_by
    return out


def _normalize_csv_rule(raw) -> dict:
    if not isinstance(raw, dict):
        return {}
    rule: dict = {}
    for key in ("required_columns", "not_empty"):
        vals = _clean_string_list(raw.get(key))
        if vals:
            rule[key] = vals
    unique_keys = _normalize_unique_keys(raw.get("unique_keys"))
    if unique_keys:
        rule["unique_keys"] = unique_keys
    enums = _normalize_enums(raw.get("enums"))
    if enums:
        rule["enums"] = enums
    numeric = _normalize_numeric(raw.get("numeric"))
    if numeric:
        rule["numeric"] = numeric
    date_cols = _normalize_date_columns(raw.get("date"))
    if date_cols:
        rule["date"] = date_cols
    regexes = _normalize_regex(raw.get("regex"))
    if regexes:
        rule["regex"] = regexes
    conditions = _normalize_conditions(raw.get("conditions"))
    if conditions:
        rule["conditions"] = conditions
    sort_rule = _normalize_sort(raw.get("sort"))
    if sort_rule:
        rule["sort"] = sort_rule
    ordered_by = _normalize_ordered_by(raw.get("ordered_by"))
    if ordered_by:
        rule["ordered_by"] = ordered_by
    return rule


def _normalize_csv_rules(raw_rules) -> dict[str, dict]:
    if not isinstance(raw_rules, dict):
        return {}
    out: dict[str, dict] = {}
    for file, rule in raw_rules.items():
        key = _clean_rule_file_key(str(file or ""))
        clean = _normalize_csv_rule(rule)
        if clean:
            out[key] = clean
    return out


def _normalize_filebrowser_settings(raw) -> dict:
    data = copy.deepcopy(DEFAULT_FILEBROWSER_SETTINGS)
    if not isinstance(raw, dict):
        return data
    try:
        max_bytes = int(raw.get("csv_full_read_max_bytes", data["csv_full_read_max_bytes"]))
    except Exception:
        raise HTTPException(400, "csv_full_read_max_bytes must be an integer")
    data["csv_full_read_max_bytes"] = max(0, min(MAX_CSV_FULL_READ_MAX_BYTES, max_bytes))
    try:
        max_rows = int(raw.get("csv_download_max_rows", data["csv_download_max_rows"]))
    except Exception:
        raise HTTPException(400, "csv_download_max_rows must be an integer")
    data["csv_download_max_rows"] = max(1, min(MAX_CSV_DOWNLOAD_MAX_ROWS, max_rows))
    try:
        dl_bytes = int(raw.get("csv_download_max_bytes", data["csv_download_max_bytes"]))
    except Exception:
        raise HTTPException(400, "csv_download_max_bytes must be an integer")
    data["csv_download_max_bytes"] = max(1, min(MAX_CSV_DOWNLOAD_BYTES, dl_bytes))
    try:
        query_bytes = int(raw.get("sql_query_max_source_bytes", data["sql_query_max_source_bytes"]))
    except Exception:
        raise HTTPException(400, "sql_query_max_source_bytes must be an integer")
    data["sql_query_max_source_bytes"] = max(0, min(MAX_SQL_QUERY_MAX_SOURCE_BYTES, query_bytes))
    try:
        preview_cols = int(raw.get("preview_max_columns", data["preview_max_columns"]))
    except Exception:
        raise HTTPException(400, "preview_max_columns must be an integer")
    data["preview_max_columns"] = max(1, min(MAX_PREVIEW_MAX_COLUMNS, preview_cols))
    try:
        preview_rows = int(raw.get("preview_max_rows", data["preview_max_rows"]))
    except Exception:
        raise HTTPException(400, "preview_max_rows must be an integer")
    data["preview_max_rows"] = max(1, min(LATEST_PREVIEW_ROWS, preview_rows))
    try:
        schema_page = int(raw.get("schema_column_page_size", data["schema_column_page_size"]))
    except Exception:
        raise HTTPException(400, "schema_column_page_size must be an integer")
    data["schema_column_page_size"] = max(1, min(MAX_SCHEMA_COLUMN_PAGE_SIZE, schema_page))
    data["csv_rules"] = _normalize_csv_rules(raw.get("csv_rules") or {})
    data["db_name_aliases"] = _normalize_db_name_aliases(raw.get("db_name_aliases") or {})
    data["auto_s3_upload_on_save"] = bool(raw.get("auto_s3_upload_on_save", data.get("auto_s3_upload_on_save", False)))
    data["preview_cache_enabled"] = bool(raw.get("preview_cache_enabled", data.get("preview_cache_enabled", True)))
    hidden = [
        name for name in _clean_string_list(raw.get("hidden_db_dirs"), lower=True)
        if not _is_filebrowser_hidden_dir_name(name)
    ]
    data["hidden_db_dirs"] = hidden if hidden else list(DEFAULT_FILEBROWSER_SETTINGS["hidden_db_dirs"])
    raw_versioned = raw.get("versioned_single_file_dirs", data["versioned_single_file_dirs"])
    versioned = [
        name for name in _clean_string_list(raw_versioned, lower=True)
        if name and name != _SINGLE_FILE_STEP_CACHE_DIR and "/" not in name and "\\" not in name
    ]
    data["versioned_single_file_dirs"] = versioned
    return data


def _load_filebrowser_settings() -> dict:
    path = _filebrowser_settings_path()
    if not path.is_file():
        return copy.deepcopy(DEFAULT_FILEBROWSER_SETTINGS)
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception:
        logger.warning("filebrowser settings read failed: %s", path)
        return copy.deepcopy(DEFAULT_FILEBROWSER_SETTINGS)
    try:
        return _normalize_filebrowser_settings(raw)
    except HTTPException:
        logger.warning("filebrowser settings invalid, using defaults: %s", path)
        return copy.deepcopy(DEFAULT_FILEBROWSER_SETTINGS)


def _save_filebrowser_settings(settings: dict) -> None:
    path = _filebrowser_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(_normalize_filebrowser_settings(settings), ensure_ascii=False, indent=2)
    _write_text_atomic(path, payload + "\n")


def _filebrowser_auto_s3_upload_enabled(settings: dict | None = None) -> bool:
    settings = settings or _load_filebrowser_settings()
    return bool(settings.get("auto_s3_upload_on_save"))


def _filebrowser_s3_sync_for_saved_path(path: Path) -> dict:
    if not _filebrowser_auto_s3_upload_enabled():
        return {
            "ok": True,
            "status": "disabled_by_filebrowser_setting",
            "path": str(path),
        }
    try:
        return _s3.sync_saved_path(PATHS.data_root, PATHS.db_root, path)
    except Exception as exc:
        logger.warning("filebrowser auto S3 sync failed path=%s: %s", path, exc)
        return {"ok": False, "status": "error", "path": str(path), "error": str(exc)}


def _hidden_db_dir_names(settings: dict | None = None) -> set[str]:
    settings = settings or _load_filebrowser_settings()
    return {str(v or "").strip().casefold() for v in (settings.get("hidden_db_dirs") or []) if str(v or "").strip()}


def _csv_rule_for_file(file: str, settings: dict | None = None) -> dict:
    settings = settings or _load_filebrowser_settings()
    rules = settings.get("csv_rules") or {}
    try:
        key = _clean_rule_file_key(file)
    except HTTPException:
        return {}
    return copy.deepcopy(rules.get(key) or rules.get(Path(key).name) or {})


def _csv_rule_summary(rule: dict) -> dict | None:
    if not rule:
        return None
    return {
        "required_columns": len(rule.get("required_columns") or []),
        "not_empty": len(rule.get("not_empty") or []),
        "unique_keys": len(rule.get("unique_keys") or []),
        "enums": len(rule.get("enums") or {}),
        "numeric": len(rule.get("numeric") or {}),
        "date": len(rule.get("date") or []),
        "regex": len(rule.get("regex") or {}),
        "conditions": len(rule.get("conditions") or []),
        "ordered_by": len((rule.get("ordered_by") or {}).get("keys") or []),
        "sort": len(rule.get("sort") or []),
    }


_CSV_VALIDATION_RULE_KEYS = (
    "required_columns", "not_empty", "unique_keys", "enums",
    "numeric", "date", "regex", "conditions", "ordered_by",
)
_CSV_SORT_RULE_KEYS = ("sort",)


def _csv_rule_sections(rule: dict) -> dict:
    if not isinstance(rule, dict):
        rule = {}
    validation_logic = {
        key: copy.deepcopy(rule[key])
        for key in _CSV_VALIDATION_RULE_KEYS
        if rule.get(key)
    }
    sort_logic = {
        key: copy.deepcopy(rule[key])
        for key in _CSV_SORT_RULE_KEYS
        if rule.get(key)
    }
    return {
        "validation_logic": validation_logic,
        "sort_logic": sort_logic,
    }


_CSV_RULE_ALLOWED_KEYS = {
    "required_columns", "not_empty", "unique_keys", "enums", "numeric",
    "date", "regex", "conditions", "ordered_by", "sort",
}
_SQL_EXPR_IGNORE_TOKENS = {
    "and", "or", "not", "is", "in", "null", "true", "false", "none",
    "case", "when", "then", "else", "end", "as", "cast", "between",
    "like", "ilike", "str", "int", "float", "date", "datetime",
    "abs", "round", "ceil", "floor", "min", "max", "sum", "mean", "avg",
    "lower", "upper", "contains", "starts_with", "ends_with", "is_null",
    "is_not_null", "fill_null", "strptime", "len",
}


def _settings_context_columns(columns, sample_rows=None) -> list[str]:
    out = _clean_string_list(columns)
    seen = {c.casefold() for c in out}
    if not out and isinstance(sample_rows, list):
        for row in sample_rows[:5]:
            if not isinstance(row, dict):
                continue
            for key in row.keys():
                text = str(key or "").strip()
                if text and text.casefold() not in seen:
                    seen.add(text.casefold())
                    out.append(text)
    return out[:500]


def _column_lookup(columns: list[str]) -> dict[str, str]:
    return {str(c).casefold(): str(c) for c in columns or [] if str(c or "").strip()}


def _draft_warning(warnings: list[str], message: str) -> None:
    text = str(message or "").strip()
    if text and text not in warnings:
        warnings.append(text)


def _canon_rule_column(column: str, lookup: dict[str, str], warnings: list[str], context: str) -> str:
    text = str(column or "").strip()
    if not text:
        return ""
    if not lookup:
        return text
    hit = lookup.get(text.casefold())
    if hit:
        return hit
    _draft_warning(warnings, f"{context}: unknown column removed: {text}")
    return ""


def _filter_rule_column_list(values, lookup: dict[str, str], warnings: list[str], context: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        col = _canon_rule_column(value, lookup, warnings, context)
        key = col.casefold()
        if col and key not in seen:
            seen.add(key)
            out.append(col)
    return out


def _filter_rule_dict_by_column(value: dict, lookup: dict[str, str], warnings: list[str], context: str) -> dict:
    out: dict = {}
    for col, spec in (value or {}).items():
        clean = _canon_rule_column(col, lookup, warnings, context)
        if clean:
            out[clean] = spec
    return out


def _filter_unique_keys(value: list[list[str]], lookup: dict[str, str], warnings: list[str]) -> list[list[str]]:
    out: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for combo in value or []:
        cols: list[str] = []
        missing = False
        for col in combo or []:
            clean = _canon_rule_column(col, lookup, warnings, "unique_keys")
            if not clean:
                missing = True
            elif clean not in cols:
                cols.append(clean)
        if missing:
            _draft_warning(warnings, f"unique_keys: combo removed because it referenced a missing column: {combo}")
            continue
        key = tuple(cols)
        if cols and key not in seen:
            seen.add(key)
            out.append(cols)
    return out


def _condition_references_missing_columns(expr: str, lookup: dict[str, str]) -> list[str]:
    if not lookup:
        return []
    scrubbed = re.sub(r"'[^']*'|\"[^\"]*\"", " ", str(expr or ""))
    tokens = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", scrubbed)
    missing: list[str] = []
    for token in tokens:
        key = token.casefold()
        if key in lookup or key in _SQL_EXPR_IGNORE_TOKENS:
            continue
        if token not in missing:
            missing.append(token)
    return missing


def _filter_conditions(value: list[dict], lookup: dict[str, str], warnings: list[str]) -> list[dict]:
    out: list[dict] = []
    for item in value or []:
        expr = str((item or {}).get("expr") or "").strip()
        missing = _condition_references_missing_columns(expr, lookup)
        if missing:
            _draft_warning(warnings, f"conditions: expression removed because columns were not found: {', '.join(missing)}")
            continue
        out.append(item)
    return out


def _filter_order_specs(value: list[dict], lookup: dict[str, str], warnings: list[str], context: str) -> list[dict]:
    out: list[dict] = []
    for item in value or []:
        clean = _canon_rule_column((item or {}).get("column") or "", lookup, warnings, context)
        if clean:
            out.append({**item, "column": clean})
    return out


def _normalize_csv_rule_draft(raw, *, columns=None) -> tuple[dict, list[str]]:
    warnings: list[str] = []
    if not isinstance(raw, dict):
        return {}, ["LLM draft did not return a csv_rules object."]
    lookup = _column_lookup(_settings_context_columns(columns))
    unknown = sorted(str(k) for k in raw.keys() if str(k) not in _CSV_RULE_ALLOWED_KEYS)
    for key in unknown:
        _draft_warning(warnings, f"unsupported key removed: {key}")

    rule: dict = {}
    for key in ("required_columns", "not_empty"):
        vals = _filter_rule_column_list(_clean_string_list(raw.get(key)), lookup, warnings, key)
        if vals:
            rule[key] = vals

    try:
        unique_keys = _filter_unique_keys(_normalize_unique_keys(raw.get("unique_keys")), lookup, warnings)
        if unique_keys:
            rule["unique_keys"] = unique_keys
    except HTTPException as exc:
        _draft_warning(warnings, str(exc.detail))

    try:
        enums = _filter_rule_dict_by_column(_normalize_enums(raw.get("enums")), lookup, warnings, "enums")
        if enums:
            rule["enums"] = enums
    except HTTPException as exc:
        _draft_warning(warnings, str(exc.detail))

    try:
        numeric = _filter_rule_dict_by_column(_normalize_numeric(raw.get("numeric")), lookup, warnings, "numeric")
        if numeric:
            rule["numeric"] = numeric
    except HTTPException as exc:
        _draft_warning(warnings, str(exc.detail))

    date_cols = _filter_rule_column_list(_normalize_date_columns(raw.get("date")), lookup, warnings, "date")
    if date_cols:
        rule["date"] = date_cols

    try:
        regexes = _filter_rule_dict_by_column(_normalize_regex(raw.get("regex")), lookup, warnings, "regex")
        if regexes:
            rule["regex"] = regexes
    except HTTPException as exc:
        _draft_warning(warnings, str(exc.detail))

    try:
        conditions = _filter_conditions(_normalize_conditions(raw.get("conditions")), lookup, warnings)
        if conditions:
            rule["conditions"] = conditions
    except HTTPException as exc:
        _draft_warning(warnings, str(exc.detail))

    try:
        sort_rule = _filter_order_specs(_normalize_sort(raw.get("sort")), lookup, warnings, "sort")
        if sort_rule:
            rule["sort"] = sort_rule
    except HTTPException as exc:
        _draft_warning(warnings, str(exc.detail))

    try:
        ordered_by = _normalize_ordered_by(raw.get("ordered_by"))
        if ordered_by:
            keys = _filter_order_specs(ordered_by.get("keys") or [], lookup, warnings, "ordered_by")
            group_by = _filter_rule_column_list(ordered_by.get("group_by") or [], lookup, warnings, "ordered_by.group_by")
            if keys:
                rule["ordered_by"] = {"keys": keys, **({"group_by": group_by} if group_by else {})}
    except HTTPException as exc:
        _draft_warning(warnings, str(exc.detail))

    return rule, warnings


def _safe_sample_rows(rows, *, max_rows: int = 5, max_cols: int = 40, max_value_len: int = 120) -> list[dict]:
    out: list[dict] = []
    if not isinstance(rows, list):
        return out
    for row in rows[:max_rows]:
        if not isinstance(row, dict):
            continue
        clean: dict = {}
        for idx, (key, value) in enumerate(row.items()):
            if idx >= max_cols:
                break
            text = str(value if value is not None else "")
            clean[str(key)[:120]] = text[:max_value_len]
        out.append(clean)
    return out


def _settings_column_profiles(columns: list[str], sample_rows: list[dict]) -> list[dict]:
    profiles: list[dict] = []
    for col in columns[:80]:
        values: list[str] = []
        for row in sample_rows[:10]:
            if not isinstance(row, dict):
                continue
            value = row.get(col)
            if value is None:
                for key, raw in row.items():
                    if str(key).casefold() == col.casefold():
                        value = raw
                        break
            text = str(value if value is not None else "").strip()
            if text:
                values.append(text[:80])
        unique = []
        seen = set()
        for value in values:
            key = value.casefold()
            if key not in seen:
                seen.add(key)
                unique.append(value)
        numeric_count = 0
        integer_count = 0
        for value in values:
            try:
                parsed = float(value)
            except Exception:
                continue
            numeric_count += 1
            if parsed.is_integer():
                integer_count += 1
        inferred = "string"
        if values and numeric_count == len(values):
            inferred = "integer" if integer_count == len(values) else "numeric"
        elif re.search(r"(date|time|_dt$|^dt_|created|updated|start|end)", col, flags=re.I):
            inferred = "date"
        profiles.append({
            "column": col,
            "sample_values": unique[:8],
            "non_empty_sample_count": len(values),
            "sample_unique_count": len(unique),
            "inferred_type": inferred,
        })
    return profiles


def _settings_llm_rule_candidate(plan: dict, file_key: str) -> dict:
    if not isinstance(plan, dict):
        return {}
    csv_rules = plan.get("csv_rules")
    if isinstance(csv_rules, dict):
        for key in (file_key, Path(file_key).name):
            item = csv_rules.get(key)
            if isinstance(item, dict):
                return item
        for item in csv_rules.values():
            if isinstance(item, dict):
                return item
    for key in ("draft", "rule", "csv_rule"):
        item = plan.get(key)
        if isinstance(item, dict):
            return item
    if any(key in plan for key in _CSV_RULE_ALLOWED_KEYS):
        return plan
    return {}


def _settings_prompt_has_duplicate_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    return any(term in low or term in text for term in (
        "unique", "duplicate", "duplicated", "dedupe", "same row", "same combination",
        "중복", "유니크", "같은 행", "똑같은 행", "동일한 행", "같은 조합", "조합이 중복",
    ))


def _prompt_identifier_tokens(prompt: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_]*", str(prompt or "")):
        key = token.casefold()
        if key not in seen:
            seen.add(key)
            out.append(token)
    return out


def _resolve_prompt_rule_columns(prompt: str, columns: list[str]) -> tuple[list[str], list[str]]:
    lookup = _column_lookup(columns)
    aliases = {
        "product": ("product", "product_id", "prod_id", "prod"),
        "prod": ("product", "product_id", "prod_id", "prod"),
        "lot": ("lot_id", "fab_lot_id", "lot", "lotid"),
        "lot_id": ("lot_id", "fab_lot_id", "lot", "lotid"),
        "fab_lot": ("fab_lot_id", "lot_id", "fab_lot", "lot"),
        "fab_lot_id": ("fab_lot_id", "lot_id", "fab_lot", "lot"),
        "wafer": ("wafer_id", "wf_id", "wafer"),
        "wafer_id": ("wafer_id", "wf_id", "wafer"),
        "wf": ("wafer_id", "wf_id", "wafer"),
        "wf_id": ("wafer_id", "wf_id", "wafer"),
        "root_lot": ("root_lot_id", "root_lot", "lot_root_id"),
        "root_lot_id": ("root_lot_id", "root_lot", "lot_root_id"),
    }
    resolved: list[str] = []
    missing: list[str] = []
    for token in _prompt_identifier_tokens(prompt):
        key = token.casefold()
        candidates = (key, key.replace(" ", "_"), *(aliases.get(key) or ()))
        hit = ""
        for cand in candidates:
            if cand in lookup:
                hit = lookup[cand]
                break
        if hit:
            if hit not in resolved:
                resolved.append(hit)
            continue
        if "_" in token or key in aliases:
            missing.append(token)
    return resolved, missing


def _settings_prompt_has_enum_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    english = bool(re.search(r"\b(enum|allowed|allowlist|only)\b", low) or "one of" in low or "must be" in low)
    korean = any(term in text for term in ("허용", "허용값", "중 하나", "중에", "만 있어야", "만 가능", "만 허용", " 또는 "))
    return english or korean


def _prompt_enum_values(prompt: str, target_column: str, columns: list[str]) -> list[str]:
    text = str(prompt or "")
    tail = text
    for needle in (target_column, target_column.replace("_", " ")):
        m = re.search(re.escape(needle), text, flags=re.I)
        if m:
            tail = text[m.end():]
            break
    column_tokens = {c.casefold() for c in columns}
    column_tokens.update(c.casefold().replace("_", " ") for c in columns)
    stop = {
        "or", "and", "only", "must", "be", "one", "of", "in", "value", "values",
        "enum", "allowed", "allowlist", "operator", "column", "col",
    }
    out: list[str] = []
    seen: set[str] = set()
    for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_.-]*", tail):
        clean = token.strip().strip(".,;:()[]{}")
        key = clean.casefold()
        if not clean or key in stop or key in column_tokens:
            continue
        if key not in seen:
            seen.add(key)
            out.append(clean)
    return out[:50]


def _has_not_empty_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    return any(term in low or term in text for term in (
        "not empty", "non-empty", "not blank", "blank", "empty",
        "빈 값", "비어", "비면", "공백", "값이 있", "값은 있",
    ))


def _has_required_column_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    return any(term in low or term in text for term in (
        "required column", "must exist", "must have", "column must", "required",
        "필수 컬럼", "컬럼은 반드시", "컬럼이 반드시", "컬럼 있어야", "컬럼은 있어야",
    ))


def _has_numeric_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    if re.search(r"\b(numeric|number|integer|float|min|max)\b", low) or ">=" in text or "<=" in text:
        return True
    if "정수" in text:
        return True
    if re.search(r"-?\d+(?:\.\d+)?\s*(?:이상|이하|초과|미만)", text):
        return True
    return any(term in text for term in (
        "숫자여야", "숫자 이어야", "숫자이어야", "숫자로", "숫자 값", "숫자값", "숫자 컬럼", "숫자만",
    ))


def _has_date_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    return bool(re.search(r"\b(date|datetime|timestamp|time)\b", low)) or any(term in text for term in (
        "날짜", "일자", "일시", "시간 형식", "시간값", "시간 값",
    ))


def _prompt_term_positions(text: str, terms: tuple[str, ...]) -> list[int]:
    low = str(text or "").casefold()
    positions: list[int] = []
    for term in terms:
        needle = str(term or "").casefold()
        if not needle:
            continue
        start = 0
        while True:
            idx = low.find(needle, start)
            if idx < 0:
                break
            positions.append(idx)
            start = idx + max(1, len(needle))
    return positions


def _column_positions(text: str, column: str) -> list[int]:
    low = str(text or "").casefold()
    variants = [str(column or "").casefold()]
    spaced = str(column or "").replace("_", " ").casefold()
    if spaced not in variants:
        variants.append(spaced)
    out: list[int] = []
    for variant in variants:
        if not variant:
            continue
        start = 0
        while True:
            idx = low.find(variant, start)
            if idx < 0:
                break
            out.append(idx)
            start = idx + max(1, len(variant))
    return out


def _prompt_columns_near_terms(prompt: str, resolved: list[str], terms: tuple[str, ...], *, window: int = 32) -> list[str]:
    term_positions = _prompt_term_positions(prompt, terms)
    if not term_positions:
        return []
    out: list[str] = []
    for col in resolved:
        col_positions = _column_positions(prompt, col)
        if any(abs(cpos - tpos) <= window for cpos in col_positions for tpos in term_positions):
            out.append(col)
    return out


def _numeric_targets_from_prompt(prompt: str, resolved: list[str]) -> list[str]:
    if not resolved or not _has_numeric_intent(prompt):
        return []
    numeric_name_re = re.compile(r"(rank|order|sort|seq|count|cnt|qty|num|number|idx|index|priority|score|value|rate|ratio|pct|percent|min|max|limit)", re.I)
    numeric_named = [col for col in resolved if numeric_name_re.search(col)]
    near = _prompt_columns_near_terms(
        prompt,
        resolved,
        ("numeric", "number", "integer", "float", "min", "max", "숫자", "정수", "이상", "이하", "초과", "미만"),
        window=32,
    )
    if numeric_named:
        return [col for col in near if col in numeric_named] or numeric_named
    if len(resolved) == 1:
        return resolved
    return near[:1]


def _date_targets_from_prompt(prompt: str, resolved: list[str]) -> list[str]:
    if not resolved or not _has_date_intent(prompt):
        return []
    date_named = [col for col in resolved if _looks_date_like_column(col)]
    near = _prompt_columns_near_terms(
        prompt,
        resolved,
        ("date", "datetime", "timestamp", "time", "날짜", "일자", "일시", "시간"),
        window=32,
    )
    if date_named:
        return [col for col in near if col in date_named] or date_named
    if len(resolved) == 1:
        return resolved
    return near


def _numeric_rule_from_prompt(prompt: str, target: str) -> dict:
    text = str(prompt or "")
    low = text.lower()
    spec: dict = {}
    if "integer" in low or "정수" in text:
        spec["integer"] = True
    min_match = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:이상|>=|부터)", text)
    max_match = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:이하|<=|까지)", text)
    if min_match:
        val = float(min_match.group(1))
        spec["min"] = int(val) if val.is_integer() else val
    if max_match:
        val = float(max_match.group(1))
        spec["max"] = int(val) if val.is_integer() else val
    if spec or _has_numeric_intent(prompt):
        return {target: spec}
    return {}


def _has_regex_format_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    return bool(re.search(r"\b(regex|regexp|pattern|format)\b", low)) or any(term in text for term in (
        "정규식", "패턴", "형식", "포맷", "같은", "처럼", "이어야", "있어야",
    ))


def _regex_rule_from_prompt(prompt: str, resolved: list[str]) -> dict:
    text = str(prompt or "")
    low = text.lower()
    out: dict = {}
    for col in resolved:
        key = col.casefold()
        has_rule_pattern = (
            "r숫자" in low
            or "r 숫자" in low
            or (
                _has_regex_format_intent(prompt)
                and bool(re.search(r"\bR\d+\b", text, flags=re.I))
                and bool(re.search(r"\bRO\b", text, flags=re.I))
            )
        )
        if key == "rule_order" and has_rule_pattern:
            out[col] = r"R\d+|RO"
        elif key in {"feature_name", "feature"} and any(term in text for term in ("앞에", "선행", "첫")) and "숫자" in text:
            out[col] = r"\d+(?:\.\d+)?\s+.+"
        elif key == "category" and "ppid" in low and "숫자" in text:
            out[col] = r"^PPID_\d+_\d+$"
        elif key == "function_step" and "대문자" in text and ("underscore" in low or "언더" in text or "_" in text):
            out[col] = r"^[A-Z_]+$"
    return out


def _condition_rules_from_prompt(prompt: str, resolved: list[str]) -> list[dict]:
    text = str(prompt or "")
    if len(resolved) < 2:
        return []
    lookup = _column_lookup(resolved)
    for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*(>=|<=|==|!=|>|<)\s*([A-Za-z_][A-Za-z0-9_]*)\b", text):
        left_key = match.group(1).casefold()
        right_key = match.group(3).casefold()
        left = lookup.get(left_key)
        right = lookup.get(right_key)
        if left and right:
            op = match.group(2)
            return [{"expr": f"{left} {op} {right}", "message": f"{left} must be {op} {right}"}]
    if any(term in text for term in ("빠르면 안", "보다 빠르", "이전이면 안", "작으면 안")):
        left = resolved[0]
        right = resolved[1]
        return [{"expr": f"{left} >= {right}", "message": f"{left} must be >= {right}"}]
    return []


def _order_intents_from_prompt(prompt: str) -> tuple[bool, bool]:
    text = str(prompt or "")
    low = text.lower()
    has_order = bool(re.search(r"\b(sort|ordered|order by)\b", low))
    has_order = has_order or any(term in text for term in (
        "정렬", "순서", "오름차순", "내림차순", "앞에 숫자", "앞 숫자", "선행 숫자", "숫자에 따라서", "숫자 기준",
    ))
    if not has_order:
        return False, False
    validate_order = any(term in low or term in text for term in (
        "validate", "check order", "order check",
        "검증", "검사", "확인", "현재 순서", "현재 행 순서", "순서가 맞", "정렬되어 있는지", "정렬 검증",
    ))
    save_sort = any(term in low or term in text for term in (
        "save", "on save", "when saving",
        "저장", "저장할 때", "저장 시", "저장 정렬", "정렬해줘", "정렬해서 저장", "순서대로 저장",
    ))
    if not validate_order and not save_sort:
        save_sort = True
    return validate_order, save_sort


def _sort_rule_from_prompt(prompt: str, columns: list[str], resolved: list[str]) -> dict:
    validate_order, save_sort = _order_intents_from_prompt(prompt)
    if not (validate_order or save_sort):
        return {}
    specs = _fallback_sort_specs(prompt, resolved or columns, expert=False)
    if not specs:
        return {}
    out: dict = {}
    if validate_order:
        out["ordered_by"] = {"keys": specs}
    if save_sort:
        out["sort"] = specs
    return out


def _settings_prompt_explicit_rule(prompt: str, columns: list[str], current_rule: dict,
                                   warnings: list[str]) -> dict | None:
    rule: dict = {}
    explicit_seen = False
    resolved, missing = _resolve_prompt_rule_columns(prompt, columns)

    if _settings_prompt_has_duplicate_intent(prompt):
        if resolved or missing:
            explicit_seen = True
            if missing:
                _draft_warning(warnings, f"unique_keys prompt referenced missing column(s): {', '.join(missing)}")
            if len(resolved) >= 2:
                rule["unique_keys"] = [resolved]
            else:
                _draft_warning(warnings, "duplicate prompt did not resolve to a usable unique key.")

    if resolved and _has_required_column_intent(prompt):
        explicit_seen = True
        rule["required_columns"] = resolved

    if resolved and _has_not_empty_intent(prompt):
        explicit_seen = True
        rule["not_empty"] = resolved

    numeric_cols: set[str] = set()
    if resolved and _has_numeric_intent(prompt):
        explicit_seen = True
        for target in _numeric_targets_from_prompt(prompt, resolved) or [resolved[0]]:
            numeric = _numeric_rule_from_prompt(prompt, target)
            if numeric:
                rule.setdefault("numeric", {}).update(numeric)
                numeric_cols.add(target)

    date_cols = _date_targets_from_prompt(prompt, resolved)
    if date_cols:
        explicit_seen = True
        rule["date"] = date_cols

    sort_rule = _sort_rule_from_prompt(prompt, columns, resolved)
    regex_rules = {} if sort_rule and not _has_regex_format_intent(prompt) else _regex_rule_from_prompt(prompt, resolved)
    if regex_rules:
        explicit_seen = True
        rule.setdefault("regex", {}).update(regex_rules)

    conditions = _condition_rules_from_prompt(prompt, resolved)
    if conditions:
        explicit_seen = True
        rule["conditions"] = conditions

    if sort_rule:
        explicit_seen = True
        rule.update(sort_rule)

    if _settings_prompt_has_enum_intent(prompt) and resolved:
        target = resolved[0]
        if target not in numeric_cols and target not in regex_rules:
            explicit_seen = True
            values = _prompt_enum_values(prompt, target, columns)
            if values:
                rule.setdefault("enums", {})[target] = values
            else:
                _draft_warning(warnings, f"enums prompt did not include allowed values for {target}.")

    return rule if explicit_seen else None


def _fallback_sort_direction(prompt: str) -> str:
    text = str(prompt or "")
    low = text.lower()
    desc_terms = (
        "desc", "descending", "내림차순", "역순", "큰순", "큰 순",
        "높은순", "높은 순", "많은순", "많은 순",
    )
    if any(term in low or term in text for term in desc_terms):
        return "desc"
    return "asc"


def _fallback_sort_type(prompt: str, column: str) -> str:
    text = str(prompt or "")
    low = text.lower()
    column_l = str(column or "").casefold()
    if column_l in {"rule_order", "ruleorder", "order", "sort_order"}:
        return "rule_order"
    leading_number_terms = (
        "leading number", "prefix number", "prefix numeric",
        "앞에 숫자", "앞 숫자", "선행 숫자", "첫 숫자", "숫자에 따라서", "숫자 기준",
    )
    if column_l in {"feature_name", "feature", "step_name", "function_step"}:
        return "leading_number"
    return "string"


def _settings_prompt_wants_expert(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    return any(term in low or term in text for term in (
        "expert", "comprehensive", "detailed", "strict", "all possible", "as much as possible",
        "전문가", "상세", "자세", "가능한", "가능한거", "가능한 것", "전체", "꼼꼼", "강하게",
        "다 짜", "다 만들어", "최대한",
    ))


def _sample_values_for_column(sample_rows: list[dict] | None, column: str) -> list[str]:
    values: list[str] = []
    for row in (sample_rows or [])[:20]:
        if not isinstance(row, dict):
            continue
        raw = row.get(column)
        if raw is None:
            for key, value in row.items():
                if str(key).casefold() == str(column).casefold():
                    raw = value
                    break
        text = str(raw if raw is not None else "").strip()
        if text:
            values.append(text)
    return values


def _sample_unique_values(values: list[str], limit: int = 20) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _numeric_spec_from_values(values: list[str]) -> dict | None:
    parsed: list[float] = []
    for value in values:
        try:
            parsed.append(float(str(value).strip()))
        except Exception:
            return None
    if not parsed:
        return None
    out: dict = {"integer": all(v.is_integer() for v in parsed)}
    out["min"] = int(min(parsed)) if out["integer"] else min(parsed)
    out["max"] = int(max(parsed)) if out["integer"] else max(parsed)
    return out


def _looks_date_like_column(column: str) -> bool:
    return bool(re.search(r"(date|time|_dt$|^dt_|created|updated|start|end)", str(column or ""), flags=re.I))


def _order_prompt_segment(prompt: str) -> str:
    text = str(prompt or "")
    markers = (
        "sort", "ordered", "order by",
        "정렬", "순서", "오름차순", "내림차순", "현재 순서", "현재 행 순서",
    )
    parts = [part for part in re.split(r"[.;\n]", text) if part.strip()]
    selected = [part for part in parts if any(marker in part.lower() or marker in part for marker in markers)]
    return " ".join(selected) if selected else text


def _is_ppid_knob_settings_file(file_key: str) -> bool:
    return Path(str(file_key or "")).name.casefold() == "ppid_knob.csv"


def _ppid_knob_contract_columns(columns: list[str]) -> list[str]:
    lookup = _column_lookup(columns)
    out: list[str] = []
    for key in ("feature_name", "rule_order", "step_desc", "operator", "value", "category"):
        col = lookup.get(key)
        if not col and key == "step_desc":
            col = lookup.get("function_step") or lookup.get("func_step")
        if not col and key == "value":
            col = lookup.get("ppid")
        if col and col not in out:
            out.append(col)
    return out or list(columns)


def _ppid_knob_not_empty_columns(columns: list[str]) -> list[str]:
    lookup = _column_lookup(columns)
    out = [
        lookup.get("feature_name"),
        lookup.get("step_desc") or lookup.get("function_step") or lookup.get("func_step"),
    ]
    return [col for col in out if col] or _ppid_knob_contract_columns(columns)


def _fallback_sort_specs(prompt: str, columns: list[str], *, expert: bool = False, file_key: str = "") -> list[dict]:
    lookup = _column_lookup(columns)
    order_text = _order_prompt_segment(prompt)
    low = order_text.casefold()
    direction = _fallback_sort_direction(prompt)
    is_ppid_knob = _is_ppid_knob_settings_file(file_key)
    mentioned = [
        col for col in columns
        if col.casefold() in low or col.casefold().replace("_", " ") in low
    ]
    if not mentioned and any(term in str(prompt or "") for term in ("앞에 숫자", "앞 숫자", "선행 숫자")):
        feature_col = lookup.get("feature_name")
        if feature_col:
            mentioned = [feature_col]
    if mentioned:
        candidates = mentioned
    elif is_ppid_knob:
        candidates = [lookup[col] for col in ("feature_name", "rule_order") if col in lookup]
    elif expert:
        candidates = [lookup[col] for col in ("product", "feature_name", "rule_order") if col in lookup]
        if not candidates:
            candidates = [lookup[col] for col in ("rank", "order", "sort_order", "seq", "sequence", "priority") if col in lookup]
    else:
        candidates = [lookup[col] for col in ("product", "feature_name", "rule_order") if col in lookup]
    specs: list[dict] = []
    seen: set[str] = set()
    for col in candidates:
        key = col.casefold()
        if key in seen:
            continue
        seen.add(key)
        specs.append({
            "column": col,
            "direction": direction,
            "type": _fallback_sort_type(prompt, col),
            "nulls": "last",
        })
    return specs


def _fallback_unique_keys(columns: list[str], *, file_key: str = "") -> list[list[str]]:
    lookup = _column_lookup(columns)
    if _is_ppid_knob_settings_file(file_key):
        combos: list[list[str]] = []
        for combo in (
            ("feature_name", "rule_order", "step_desc"),
            ("feature_name", "rule_order", "function_step"),
            ("feature_name", "step_desc"),
            ("feature_name", "function_step"),
        ):
            cols = [lookup[c] for c in combo if c in lookup]
            if len(cols) == len(combo):
                combos.append(cols)
        return combos[:2]
    combos: list[list[str]] = []
    for combo in (
        ("id",),
        ("key",),
        ("product", "feature_name", "rule_order"),
        ("product", "lot_id", "wafer_id"),
        ("root_lot_id", "wafer_id"),
        ("lot_id", "wafer_id"),
        ("product", "feature_name"),
    ):
        cols = [lookup[c] for c in combo if c in lookup]
        if len(cols) == len(combo):
            combos.append(cols)
    return combos[:2]


def _fallback_regex_rules(columns: list[str], sample_rows: list[dict] | None) -> dict[str, str]:
    lookup = _column_lookup(columns)
    regexes: dict[str, str] = {}
    feature_col = lookup.get("feature_name")
    if feature_col:
        values = _sample_values_for_column(sample_rows, feature_col)
        if not values or any(re.match(r"^\d+(?:\.\d+)?\s+\S+", value) for value in values):
            regexes[feature_col] = r"\d+(?:\.\d+)?\s+.+"
    rule_col = lookup.get("rule_order")
    if rule_col:
        values = _sample_values_for_column(sample_rows, rule_col)
        if not values or all(re.match(r"^R\d+$|^RO$", value, flags=re.I) for value in values):
            regexes[rule_col] = r"R\d+|RO"
    return regexes


def _fallback_numeric_rules(columns: list[str], sample_rows: list[dict] | None) -> dict[str, dict]:
    out: dict[str, dict] = {}
    numeric_name_re = re.compile(r"(rank|order|sort|seq|count|cnt|qty|num|number|idx|index|priority|score|value|rate|ratio|pct|percent|min|max|limit)", re.I)
    for col in columns:
        values = _sample_values_for_column(sample_rows, col)
        spec = _numeric_spec_from_values(values)
        if spec and (numeric_name_re.search(col) or values):
            out[col] = spec
    return out


def _fallback_enum_rules(columns: list[str], sample_rows: list[dict] | None) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    enum_name_re = re.compile(r"(status|state|type|category|cat|operator|mode|flag|yn|use|enabled|result|pass|fail)", re.I)
    for col in columns:
        if not enum_name_re.search(col):
            continue
        values = _sample_unique_values(_sample_values_for_column(sample_rows, col), limit=16)
        if values and len(values) <= 12:
            out[col] = values
    return out


def _fallback_condition_rules(columns: list[str]) -> list[dict]:
    lookup = _column_lookup(columns)
    conditions: list[dict] = []
    for start_key, end_key in (
        ("start_time", "end_time"),
        ("start_date", "end_date"),
        ("from_time", "to_time"),
        ("begin_time", "end_time"),
    ):
        if start_key in lookup and end_key in lookup:
            conditions.append({
                "expr": f"{lookup[end_key]} >= {lookup[start_key]}",
                "message": f"{lookup[end_key]} must be >= {lookup[start_key]}",
            })
            break
    return conditions


def _settings_draft_fallback_rule(prompt: str, columns: list[str], current_rule: dict, warnings: list[str],
                                  file_key: str = "",
                                  sample_rows: list[dict] | None = None) -> dict:
    rule = copy.deepcopy(current_rule) if isinstance(current_rule, dict) else {}
    low = str(prompt or "").lower()
    expert = _settings_prompt_wants_expert(prompt)
    is_ppid_knob = _is_ppid_knob_settings_file(file_key)
    if not columns:
        _draft_warning(warnings, "No columns were supplied, so only schema-level cleanup was applied.")
        return rule
    if expert or any(token in low for token in ("required", "필수", "must have")):
        rule["required_columns"] = _ppid_knob_contract_columns(columns) if is_ppid_knob else columns
    if expert or any(token in low for token in ("not empty", "non-empty", "blank", "빈 값", "비어")):
        if is_ppid_knob:
            rule["not_empty"] = _ppid_knob_not_empty_columns(columns)
        elif sample_rows and expert:
            non_empty_cols = [col for col in columns if _sample_values_for_column(sample_rows, col)]
            rule["not_empty"] = non_empty_cols or columns
        else:
            rule["not_empty"] = columns
    if (expert or any(token in low for token in ("unique", "duplicate", "중복", "유니크"))) and not rule.get("unique_keys"):
        unique_keys = _fallback_unique_keys(columns, file_key=file_key)
        if unique_keys:
            rule["unique_keys"] = unique_keys
    if expert:
        enums = _fallback_enum_rules(columns, sample_rows)
        if enums and not rule.get("enums"):
            rule["enums"] = enums
        numeric = _fallback_numeric_rules(columns, sample_rows)
        if numeric and not rule.get("numeric"):
            rule["numeric"] = numeric
        date_cols = [col for col in columns if _looks_date_like_column(col)]
        if date_cols and not rule.get("date"):
            rule["date"] = date_cols
        regexes = _fallback_regex_rules(columns, sample_rows)
        if regexes and not rule.get("regex"):
            rule["regex"] = regexes
        conditions = _fallback_condition_rules(columns)
        if conditions and not rule.get("conditions"):
            rule["conditions"] = conditions
    validate_order, save_sort = _order_intents_from_prompt(prompt)
    has_order_token = validate_order or save_sort or any(token in low or token in str(prompt or "") for token in (
        "sort", "order", "정렬", "순서", "오름차순", "내림차순", "앞에 숫자", "앞 숫자", "선행 숫자",
    ))
    if (expert or has_order_token) and not (is_ppid_knob and expert and not has_order_token) and not rule.get("sort") and not rule.get("ordered_by"):
        specs = _fallback_sort_specs(prompt, columns, expert=expert, file_key=file_key)
        if specs:
            if expert or validate_order:
                rule["ordered_by"] = {"keys": specs}
            if expert or save_sort or not validate_order:
                rule["sort"] = specs
    if not rule:
        _draft_warning(warnings, "LLM unavailable or empty; no deterministic draft could be inferred.")
    else:
        _draft_warning(warnings, "LLM unavailable or empty; deterministic keyword draft was used.")
    return rule


def _can_manage_filebrowser(me: dict) -> bool:
    try:
        from core.auth import is_page_admin, is_page_manager
        if is_page_manager(me, "filebrowser"):
            return True
        return is_page_admin(me.get("username") or "", "filebrowser")
    except Exception:
        return False
