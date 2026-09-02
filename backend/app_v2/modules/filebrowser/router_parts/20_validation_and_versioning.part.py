def _require_filebrowser_user(request: Request | None) -> dict:
    if request is None:
        return {}
    from core.auth import current_user
    return current_user(request)


def _require_filebrowser_admin(request: Request | None) -> dict:
    me = _require_filebrowser_user(request)
    if me and not _can_manage_filebrowser(me):
        raise HTTPException(403, "Admin or delegated filebrowser admin only")
    return me


def _require_filebrowser_manager(request: Request) -> dict:
    me = current_user(request)
    if not _can_manage_filebrowser(me):
        raise HTTPException(403, "Admin or delegated filebrowser admin only")
    return me


TEG_REFERENCE_ACCESS_SCOPE = "teg_reference"


def _require_base_file_access(request: Request, file: str, access_scope: str = "",
                              *, manage: bool = False) -> tuple[dict, Path | None]:
    """Apply normal FileBrowser auth or the TEG reference-file allowlist.

    The access_scope flag never grants access by itself.  A TEG-scoped request
    must resolve to one of the three files configured by teg_map and the user
    must have TEG page access.  Within this narrowly allowlisted scope, every
    TEG page user may mutate the reference files; normal FileBrowser mutations
    still require a FileBrowser manager.
    """
    scope = str(access_scope or "").strip().casefold()
    if scope != TEG_REFERENCE_ACCESS_SCOPE:
        me = _require_filebrowser_manager(request) if manage else _require_filebrowser_user(request)
        rel_parts = [str(part or "").strip().rstrip(" .").casefold() for part in Path(str(file or "")).parts]
        credential_path = "credential" in rel_parts
        if not credential_path:
            for root in (_base_root(), _db_root()):
                try:
                    candidate = (root / Path(str(file or ""))).resolve()
                    candidate.relative_to((root / "credential").resolve())
                    credential_path = True
                    break
                except (OSError, ValueError):
                    continue
        if credential_path and str((me or {}).get("role") or "").strip().casefold() != "admin":
            # 숨김은 권한이 아니다. URL을 직접 구성해도 credential 아래 파일은
            # global admin 외에는 읽기/다운로드/편집할 수 없다.
            raise HTTPException(403, "Admin only credential folder")
        return me, None

    from core import teg_map as _teg_map
    from core.auth import canonical_tab_token, is_page_manager

    me = current_user(request)
    if not is_page_manager(me, "teg"):
        raw_tabs = me.get("tabs") or []
        tabs = raw_tabs if isinstance(raw_tabs, list) else str(raw_tabs).split(",")
        if not any(canonical_tab_token(tab) == "teg" for tab in tabs):
            raise HTTPException(403, "TEG page permission required")

    target = _resolve_base_file_for_version(file).resolve()
    try:
        allowed = {
            _teg_map.reference_file_path(kind).resolve()
            for kind in _teg_map.REFERENCE_FILE_KEYS
        }
    except (OSError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    if target not in allowed:
        raise HTTPException(403, "File is not in the TEG reference allowlist")
    return me, target


def _parse_datetime_like(value: str) -> datetime.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    iso = text.replace("Z", "+00:00")
    try:
        return datetime.datetime.fromisoformat(iso)
    except Exception:
        pass
    for fmt in (
        "%Y-%m-%d", "%Y/%m/%d", "%Y%m%d",
        "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M",
        "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.datetime.strptime(text, fmt)
        except Exception:
            continue
    return None


def _csv_rows_to_frame(header: list[str], data_rows: list[list[str]]) -> pl.DataFrame:
    cols = [str(c or "").strip() for c in header]
    data = {
        col: [row[i] if i < len(row) else "" for row in data_rows]
        for i, col in enumerate(cols)
        if col
    }
    if not data:
        return pl.DataFrame()
    return pl.DataFrame(data)


def _csv_validation_error(errors: list[dict], rule: str, message: str, *,
                          row: int | None = None, column: str = "", value=None,
                          max_errors: int = 200) -> None:
    if len(errors) >= max_errors:
        return
    item = {"rule": rule, "message": message}
    if row is not None:
        item["row"] = int(row)
    if column:
        item["column"] = column
    if value is not None:
        item["value"] = "" if value is None else str(value)
    errors.append(item)


def _validate_csv_rule(header: list[str], data_rows: list[list[str]], rule: dict) -> dict:
    header = [str(c or "").strip() for c in header]
    data_rows, _ = _normalize_rows(data_rows, len(header), "")
    errors: list[dict] = []
    seen_header: set[str] = set()
    for col in header:
        if col in seen_header:
            _csv_validation_error(errors, "columns", f"Duplicate column: {col}", column=col)
        seen_header.add(col)
    columns = set(header)

    def _missing(col: str, rule_name: str) -> bool:
        if col in columns:
            return False
        _csv_validation_error(errors, rule_name, f"Missing column: {col}", column=col)
        return True

    for col in rule.get("required_columns") or []:
        _missing(str(col), "required_columns")

    col_idx = {c: i for i, c in enumerate(header)}
    for col in rule.get("not_empty") or []:
        col = str(col)
        if _missing(col, "not_empty"):
            continue
        idx = col_idx[col]
        for row_no, row in enumerate(data_rows, start=1):
            val = row[idx] if idx < len(row) else ""
            if str(val or "").strip() == "":
                _csv_validation_error(errors, "not_empty", f"{col} must not be empty", row=row_no, column=col, value=val)

    for combo in rule.get("unique_keys") or []:
        cols = [str(c) for c in (combo or [])]
        if any(_missing(c, "unique_keys") for c in cols):
            continue
        indexes = [col_idx[c] for c in cols]
        seen: dict[tuple[str, ...], int] = {}
        for row_no, row in enumerate(data_rows, start=1):
            key = tuple(str(row[i] if i < len(row) else "").strip() for i in indexes)
            if all(v == "" for v in key):
                continue
            if key in seen:
                _csv_validation_error(
                    errors,
                    "unique_keys",
                    f"Duplicate key {cols}: first row {seen[key]}, duplicate row {row_no}",
                    row=row_no,
                    column=",".join(cols),
                    value="|".join(key),
                )
            else:
                seen[key] = row_no

    for col, allowed in (rule.get("enums") or {}).items():
        col = str(col)
        if _missing(col, "enums"):
            continue
        idx = col_idx[col]
        allowed_set = {str(v) for v in (allowed or [])}
        for row_no, row in enumerate(data_rows, start=1):
            val = str(row[idx] if idx < len(row) else "").strip()
            if val == "":
                continue
            if val not in allowed_set:
                _csv_validation_error(errors, "enums", f"{col} must be one of {sorted(allowed_set)}", row=row_no, column=col, value=val)

    for col, spec in (rule.get("numeric") or {}).items():
        col = str(col)
        if _missing(col, "numeric"):
            continue
        idx = col_idx[col]
        spec = spec or {}
        for row_no, row in enumerate(data_rows, start=1):
            val = str(row[idx] if idx < len(row) else "").strip()
            if val == "":
                continue
            try:
                num = float(val)
            except Exception:
                _csv_validation_error(errors, "numeric", f"{col} must be numeric", row=row_no, column=col, value=val)
                continue
            if not math.isfinite(num):
                _csv_validation_error(errors, "numeric", f"{col} must be finite", row=row_no, column=col, value=val)
                continue
            if spec.get("integer") and not num.is_integer():
                _csv_validation_error(errors, "numeric", f"{col} must be an integer", row=row_no, column=col, value=val)
            if spec.get("min") is not None and num < float(spec["min"]):
                _csv_validation_error(errors, "numeric", f"{col} must be >= {spec['min']}", row=row_no, column=col, value=val)
            if spec.get("max") is not None and num > float(spec["max"]):
                _csv_validation_error(errors, "numeric", f"{col} must be <= {spec['max']}", row=row_no, column=col, value=val)

    for col in rule.get("date") or []:
        col = str(col)
        if _missing(col, "date"):
            continue
        idx = col_idx[col]
        for row_no, row in enumerate(data_rows, start=1):
            val = str(row[idx] if idx < len(row) else "").strip()
            if val == "":
                continue
            if _parse_datetime_like(val) is None:
                _csv_validation_error(errors, "date", f"{col} must parse as a date/time", row=row_no, column=col, value=val)

    for col, pattern in (rule.get("regex") or {}).items():
        col = str(col)
        if _missing(col, "regex"):
            continue
        idx = col_idx[col]
        compiled = re.compile(str(pattern))
        for row_no, row in enumerate(data_rows, start=1):
            val = str(row[idx] if idx < len(row) else "")
            if val == "":
                continue
            if compiled.fullmatch(val) is None:
                _csv_validation_error(errors, "regex", f"{col} does not match /{pattern}/", row=row_no, column=col, value=val)

    if rule.get("conditions"):
        try:
            df = _csv_rows_to_frame(header, data_rows)
            if df.height:
                df = df.with_row_index("__row_nr", offset=1)
        except Exception as e:
            df = pl.DataFrame()
            _csv_validation_error(errors, "conditions", f"Cannot build condition frame: {e}")
        for condition in rule.get("conditions") or []:
            expr = str((condition or {}).get("expr") or "").strip()
            if not expr or df.is_empty():
                continue
            try:
                checked = df.with_columns(pl.sql_expr(expr).alias("__condition_ok"))
                violated = checked.filter(~pl.col("__condition_ok").fill_null(False)).select("__row_nr").head(200)
                for item in violated.to_dicts():
                    row_no = int(item.get("__row_nr") or 0)
                    _csv_validation_error(
                        errors,
                        "conditions",
                        (condition or {}).get("message") or f"Condition must be true: {expr}",
                        row=row_no,
                    )
            except Exception as e:
                _csv_validation_error(errors, "conditions", f"Condition failed '{expr}': {e}")

    ordered_by = rule.get("ordered_by") or {}
    order_specs = ordered_by.get("keys") or []
    if order_specs:
        group_by = [str(c) for c in (ordered_by.get("group_by") or [])]
        needed_cols = [str(item.get("column") or "") for item in order_specs] + group_by
        missing = [c for c in needed_cols if c and c not in col_idx]
        for col in missing:
            _csv_validation_error(errors, "ordered_by", f"Missing column: {col}", column=col)
        if not missing:
            prev_row = None
            prev_row_no = 0
            prev_group = None
            group_idx = [col_idx[c] for c in group_by]
            for row_no, row in enumerate(data_rows, start=1):
                group_key = tuple(str(row[i] if i < len(row) else "") for i in group_idx) if group_idx else None
                if prev_row is not None and (not group_idx or group_key == prev_group):
                    comp = _compare_rows_by_specs(col_idx, prev_row, row, order_specs)
                    if comp > 0:
                        _csv_validation_error(
                            errors,
                            "ordered_by",
                            f"Rows must be ordered by {', '.join(str(s.get('column') or '') for s in order_specs)}",
                            row=row_no,
                            column=",".join(str(s.get("column") or "") for s in order_specs),
                            value=f"previous row {prev_row_no}",
                        )
                prev_row = row
                prev_row_no = row_no
                prev_group = group_key

    return {
        "ok": not errors,
        "errors": errors,
        "error_count": len(errors),
        "truncated": len(errors) >= 200,
        "rows": len(data_rows),
        "columns": len(header),
    }


def _sort_cast_value(value: str, typ: str):
    text = str(value or "").strip()
    if text == "":
        return None
    if typ == "numeric":
        try:
            num = float(text)
            return num if math.isfinite(num) else None
        except Exception:
            return None
    if typ == "date":
        return _parse_datetime_like(text)
    if typ == "leading_number":
        m = re.match(r"^\s*([+-]?\d+(?:\.\d+)?)", text)
        if not m:
            return None
        try:
            num = float(m.group(1))
            return num if math.isfinite(num) else None
        except Exception:
            return None
    if typ == "rule_order":
        up = text.upper()
        if up == "RO":
            return (1, 0)
        m = re.fullmatch(r"R(\d+)", up)
        if not m:
            return None
        try:
            return (0, int(m.group(1)))
        except Exception:
            return None
    return text


def _compare_values(left, right) -> int:
    if left == right:
        return 0
    try:
        return -1 if left < right else 1
    except Exception:
        ls, rs = str(left), str(right)
        if ls == rs:
            return 0
        return -1 if ls < rs else 1


def _compare_rows_by_specs(col_idx: dict[str, int], left_row: list[str], right_row: list[str], specs: list[dict]) -> int:
    for spec in specs:
        col = str(spec.get("column") or "")
        idx = col_idx[col]
        typ = str(spec.get("type") or "string")
        nulls = str(spec.get("nulls") or "last")
        direction = str(spec.get("direction") or "asc")
        lv = _sort_cast_value(left_row[idx] if idx < len(left_row) else "", typ)
        rv = _sort_cast_value(right_row[idx] if idx < len(right_row) else "", typ)
        lnull, rnull = lv is None, rv is None
        if lnull or rnull:
            if lnull and rnull:
                continue
            comp = -1 if (lnull and nulls == "first") or (rnull and nulls == "last") else 1
        else:
            comp = _compare_values(lv, rv)
        if comp:
            return -comp if direction == "desc" else comp
    return 0


def _apply_csv_sort_rule(header: list[str], data_rows: list[list[str]], rule: dict) -> list[list[str]]:
    sort_rule = rule.get("sort") or []
    if not sort_rule:
        return data_rows
    header = [str(c or "").strip() for c in header]
    data_rows, _ = _normalize_rows(data_rows, len(header), "")
    col_idx = {c: i for i, c in enumerate(header)}
    missing = [str(item.get("column") or "") for item in sort_rule if str(item.get("column") or "") not in col_idx]
    if missing:
        raise HTTPException(400, f"Sort column not found: {', '.join(missing)}")

    def _cmp(left_item, right_item):
        left_i, left_row = left_item
        right_i, right_row = right_item
        comp = _compare_rows_by_specs(col_idx, left_row, right_row, sort_rule)
        if comp:
            return comp
        return left_i - right_i

    return [row for _, row in sorted(enumerate(data_rows), key=functools.cmp_to_key(_cmp))]


def _validate_and_sort_csv_rows(file: str, header: list[str], data_rows: list[list[str]]) -> tuple[list[list[str]], dict]:
    rule = _csv_rule_for_file(file)
    if not rule:
        return data_rows, {
            "ok": True,
            "errors": [],
            "error_count": 0,
            "truncated": False,
            "rows": len(data_rows),
            "columns": len(header),
            "rule_applied": False,
            "rule_summary": None,
            "rule_sections": _csv_rule_sections({}),
            "sorted": False,
            "sort_preview_applied": False,
            "save_sort_applies_on_success": False,
        }
    validation = _validate_csv_rule(header, data_rows, rule)
    validation["rule_applied"] = True
    validation["rule_summary"] = _csv_rule_summary(rule)
    validation["rule_sections"] = _csv_rule_sections(rule)
    validation["save_sort_applies_on_success"] = bool(rule.get("sort"))
    if not validation.get("ok"):
        validation["sorted"] = False
        validation["sort_preview_applied"] = False
        return data_rows, validation
    sorted_rows = _apply_csv_sort_rule(header, data_rows, rule)
    validation["sorted"] = bool(rule.get("sort"))
    validation["sort_preview_applied"] = bool(rule.get("sort"))
    return sorted_rows, validation


def _rows_to_csv_text(header: list[str], data_rows: list[list[str]], delimiter: str, include_header: bool = True) -> str:
    out = io.StringIO()
    writer = csv.writer(out, delimiter="\t" if delimiter == "tab" else ",", lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    if include_header:
        writer.writerow(["" if v is None else str(v) for v in header])
    for row in data_rows:
        writer.writerow(["" if v is None else str(v) for v in row])
    return out.getvalue()


def _base_file_versioned(file: str, target: Path | None = None) -> bool:
    rel = str(file or "").strip().replace("\\", "/").lower()
    name = Path(rel).name.lower()
    parts = Path(rel).parts
    folder = str(parts[0]).casefold() if parts else ""
    if folder == _SINGLE_FILE_STEP_CACHE_DIR:
        return False
    if folder and folder in _versioned_single_file_dir_names():
        if target is None:
            return True
        if not target.is_file():
            return False
        if target.suffix.lower() == ".csv":
            try:
                return target.stat().st_size <= EDM_VERSION_MAX_CSV_BYTES
            except Exception:
                return False
        return target.suffix.lower() in {".parquet", ".json", ".yaml", ".yml", ".md", ".txt"}
    if rel == "product_config/products.yaml":
        return True
    if rel.startswith("reformatter/") and name.endswith(".json"):
        return True
    if target is not None and target.is_file() and target.suffix.lower() == ".csv":
        try:
            return target.stat().st_size <= EDM_VERSION_MAX_CSV_BYTES
        except Exception:
            return False
    if target is None and name in EDM_VERSIONED_SINGLE_FILES:
        # Compatibility fallback for callers that only ask by legacy file name.
        return True
    return False


def _version_file_id(file: str) -> str:
    rel = str(file or "").strip().replace("\\", "/")
    rel = rel.strip("/")
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "__", rel)
    return safe.strip("._-") or "base_file"


def _version_dir(file: str) -> Path:
    return BASE_VERSION_DIR / _version_file_id(file)


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _next_file_version(vdir: Path) -> int:
    try:
        nums = []
        for fp in vdir.glob("v*.meta.json"):
            stem = fp.name.split(".", 1)[0]
            try:
                nums.append(int(stem.lstrip("v")))
            except ValueError:
                pass
        return (max(nums) if nums else 0) + 1
    except Exception:
        return 1


def _int_or_none(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except Exception:
        return None


def _profile_column_count(profile: dict | None) -> int | None:
    if not isinstance(profile, dict):
        return None
    value = profile.get("column_count")
    if value is None:
        value = profile.get("columns")
        if isinstance(value, list):
            return len(value)
    return _int_or_none(value)


def _meta_version_shape(meta: dict | None) -> tuple[int | None, int | None]:
    if not isinstance(meta, dict):
        return None, None
    post_save_profile = meta.get("post_save_profile")
    if isinstance(post_save_profile, dict):
        rows = _int_or_none(post_save_profile.get("rows"))
        columns = _profile_column_count(post_save_profile)
        if rows is not None or columns is not None:
            return rows, columns
    return _int_or_none(meta.get("rows")), _int_or_none(meta.get("columns"))


def _shape_changed(
    rows: int | None,
    columns: int | None,
    prev_rows: int | None,
    prev_columns: int | None,
) -> bool:
    return (
        (rows is not None and prev_rows is not None and rows != prev_rows)
        or (columns is not None and prev_columns is not None and columns != prev_columns)
    )


def _bump_semver(display_version: str, *, rows: int | None = None, columns: int | None = None, prev_rows: int | None = None, prev_columns: int | None = None) -> str:
    m = re.match(r"^v(\d+)\.(\d+)$", str(display_version or ""))
    major = int(m.group(1)) if m else 1
    minor = int(m.group(2)) if m else 0
    if _shape_changed(rows, columns, prev_rows, prev_columns):
        return f"v{major + 1}.0"
    return f"v{major}.{minor + 1}"


def _next_semver(vdir: Path, *, rows: int | None = None, columns: int | None = None) -> str:
    metas = []
    try:
        for fp in vdir.glob("v*.meta.json"):
            try:
                meta = json.loads(fp.read_text(encoding="utf-8"))
                sem = str(meta.get("display_version") or "")
                m = re.match(r"^v(\d+)\.(\d+)$", sem)
                if m:
                    metas.append((int(m.group(1)), int(m.group(2)), meta))
            except Exception:
                continue
    except Exception:
        metas = []
    if not metas:
        return "v1.0"
    major, minor, latest = sorted(metas, key=lambda x: (x[0], x[1]))[-1]
    prev_rows, prev_cols = _meta_version_shape(latest)
    return _bump_semver(f"v{major}.{minor}", rows=rows, columns=columns, prev_rows=prev_rows, prev_columns=prev_cols)


def _latest_base_version_meta(file: str, *, exclude_version: str = "") -> tuple[dict, Path] | None:
    vdir = _version_dir(file)
    if not vdir.is_dir():
        return None
    candidates = []
    for meta_fp in vdir.glob("v*.meta.json"):
        try:
            meta = json.loads(meta_fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        storage_version = str(meta.get("version") or meta_fp.name.split(".", 1)[0])
        if exclude_version and storage_version == exclude_version:
            continue
        candidates.append((_version_number(storage_version), meta_fp.stat().st_mtime, meta, meta_fp))
    if not candidates:
        return None
    _, _, meta, meta_fp = sorted(candidates, key=lambda x: (x[0], x[1]))[-1]
    return meta, meta_fp


def _post_save_profile_matching_current(meta: dict | None, current_profile: dict | None) -> dict | None:
    if not isinstance(meta, dict) or not isinstance(current_profile, dict):
        return None
    post_save_profile = meta.get("post_save_profile")
    if not isinstance(post_save_profile, dict):
        return None
    current_checksum = str(current_profile.get("checksum") or "")
    post_save_checksum = str(post_save_profile.get("checksum") or "")
    if current_checksum and post_save_checksum and current_checksum == post_save_checksum:
        return post_save_profile
    return None


def _latest_post_save_profile_matching_current(file: str, target: Path, meta: dict | None) -> tuple[dict | None, dict | None]:
    latest = _latest_base_version_meta(file)
    if latest is None or not isinstance(meta, dict):
        return None, None
    latest_meta, _ = latest
    latest_storage = str(latest_meta.get("version") or "")
    storage_version = str(meta.get("version") or "")
    if not latest_storage or storage_version != latest_storage:
        return None, None
    current_profile = _file_profile(target)
    post_save_profile = _post_save_profile_matching_current(meta, current_profile)
    if post_save_profile is None:
        return None, current_profile
    return post_save_profile, current_profile


def _version_meta_with_profile(meta: dict, profile: dict, *, state: str = "") -> dict:
    out = {**meta}
    out["size"] = profile.get("size")
    out["rows"] = profile.get("rows")
    out["columns"] = _profile_column_count(profile)
    out["checksum"] = profile.get("checksum") or ""
    if state:
        out["content_file_state"] = state
    return out


def _current_base_file_version_info(file: str, target: Path, profile: dict | None = None) -> dict:
    if not _base_file_versioned(file, target):
        return {}
    profile = profile or _file_profile(target)
    rows = profile.get("rows")
    columns = profile.get("column_count")
    vdir = _version_dir(file)
    current_version = _next_semver(vdir, rows=rows, columns=columns)
    info = {
        "current_version": current_version,
        "current_version_state": "computed_next",
    }
    latest = _latest_base_version_meta(file)
    if latest is None:
        return info
    latest_meta, _ = latest
    latest_display = str(latest_meta.get("display_version") or latest_meta.get("version") or "")
    latest_storage = str(latest_meta.get("version") or "")
    checksum = str(profile.get("checksum") or "")
    post_save_profile = latest_meta.get("post_save_profile")
    post_save_checksum = str(post_save_profile.get("checksum") or "") if isinstance(post_save_profile, dict) else ""
    if checksum and post_save_checksum and checksum == post_save_checksum:
        info["current_version"] = latest_display or latest_storage or current_version
        info["current_version_state"] = "latest_post_save"
        if latest_storage:
            info["current_storage_version"] = latest_storage
    elif checksum and checksum == str(latest_meta.get("checksum") or ""):
        info["current_version"] = latest_display or latest_storage or current_version
        info["current_version_state"] = "latest_snapshot"
        if latest_storage:
            info["current_storage_version"] = latest_storage
    return info


def _cap_file_versions(vdir: Path) -> None:
    try:
        metas = sorted(vdir.glob("v*.meta.json"), key=lambda p: (p.stat().st_mtime, p.name))
        excess = len(metas) - BASE_VERSION_CAP
        if excess <= 0:
            return
        for meta_fp in metas[:excess]:
            try:
                meta = json.loads(meta_fp.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
            content = meta.get("content_file") or meta_fp.name.replace(".meta.json", meta_fp.suffix)
            for fp in (vdir / str(content), meta_fp):
                try:
                    if fp.exists():
                        fp.unlink()
                except Exception:
                    pass
    except Exception:
        pass


def _scan_one_file_raw(fp: Path):
    """Lazy-scan a CSV/parquet without Flow source normalization or wafer filtering."""
    try:
        ext = fp.suffix.lower()
        if ext == ".csv":
            return pl.scan_csv(str(fp), infer_schema_length=5000, try_parse_dates=False)
        if ext == ".parquet":
            from core.parquet_perf import scan_parquet_relaxed
            return scan_parquet_relaxed(str(fp))
    except Exception:
        return None
    return None


def _read_table_for_diff_frame(path: Path, limit: int = 20000) -> pl.DataFrame | None:
    lf = _scan_one_file_raw(path)
    if lf is None:
        fallback = _csv_lenient_lazy_frame(path)
        lf = fallback[0] if fallback else None
    if lf is None:
        return None
    try:
        df = lf.collect()
        if df.height > limit:
            df = df.head(limit)
        cols = [str(c) for c in df.columns]
        if not cols:
            return pl.DataFrame()
        return df.select([pl.col(c).cast(pl.Utf8, strict=False).fill_null("").alias(c) for c in cols])
    except Exception:
        fallback = _csv_lenient_lazy_frame(path)
        if fallback:
            try:
                df = fallback[0].collect()
                if df.height > limit:
                    df = df.head(limit)
                return df.select([pl.col(c).cast(pl.Utf8, strict=False).fill_null("").alias(c) for c in df.columns])
            except Exception:
                return None
        return None


def _file_shape(path: Path) -> tuple[int | None, int | None]:
    try:
        ext = path.suffix.lower()
        if ext in {".csv", ".parquet"}:
            lf = _scan_one_file_raw(path)
            if lf is None:
                return None, None
            cols = list(lf.collect_schema().names())
            rows = int(lf.select(pl.len()).collect().item())
            return rows, len(cols)
    except Exception:
        fallback = _csv_lenient_lazy_frame(path)
        if fallback:
            return fallback[3], len(fallback[1])
        pass
    return None, None


def _file_profile(path: Path) -> dict:
    profile = {
        "size": None,
        "checksum": "",
        "rows": None,
        "columns": [],
        "column_count": None,
    }
    try:
        profile["size"] = path.stat().st_size
        profile["checksum"] = _file_sha256(path)
    except Exception:
        pass
    try:
        if path.suffix.lower() in {".csv", ".parquet"}:
            lf = _scan_one_file_raw(path)
            if lf is not None:
                cols = list(lf.collect_schema().names())
                profile["columns"] = cols
                profile["column_count"] = len(cols)
                profile["rows"] = int(lf.select(pl.len()).collect().item())
    except Exception:
        fallback = _csv_lenient_lazy_frame(path)
        if fallback:
            profile["columns"] = fallback[1]
            profile["column_count"] = len(fallback[1])
            profile["rows"] = fallback[3]
        pass
    return profile


def _profile_diff(current: dict, version: dict) -> dict:
    cur_cols = [str(c) for c in (current.get("columns") or [])]
    ver_cols = [str(c) for c in (version.get("columns") or [])]
    cur_set = set(cur_cols)
    ver_set = set(ver_cols)
    cur_size = current.get("size")
    ver_size = version.get("size")
    cur_rows = current.get("rows")
    ver_rows = version.get("rows")
    return {
        "checksum_equal": bool(current.get("checksum") and current.get("checksum") == version.get("checksum")),
        "size_delta": (cur_size - ver_size) if isinstance(cur_size, int) and isinstance(ver_size, int) else None,
        "rows_delta": (cur_rows - ver_rows) if isinstance(cur_rows, int) and isinstance(ver_rows, int) else None,
        "columns_delta": len(cur_cols) - len(ver_cols) if cur_cols or ver_cols else None,
        "added_columns_in_current": [c for c in cur_cols if c not in ver_set],
        "removed_columns_from_current": [c for c in ver_cols if c not in cur_set],
    }


def _latest_version_content(vdir: Path) -> Path | None:
    metas = []
    try:
        for fp in vdir.glob("v*.meta.json"):
            try:
                meta = json.loads(fp.read_text(encoding="utf-8"))
                m = re.match(r"^v(\d+)$", str(meta.get("version") or fp.name.split(".", 1)[0]))
                idx = int(m.group(1)) if m else 0
                content = vdir / str(meta.get("content_file") or "")
                if content.exists():
                    metas.append((idx, content))
            except Exception:
                continue
    except Exception:
        return None
    return sorted(metas, key=lambda x: x[0])[-1][1] if metas else None


def _snapshot_change_summary(current: Path, previous: Path | None, file: str = "") -> dict:
    if previous is None or not previous.exists():
        return {"label": "초기 버전", "rows_delta": None, "columns_delta": None, "changed_cells": None, "added_rows": 0, "deleted_rows": 0, "modified_rows": 0}
    cur_profile = _file_profile(current)
    prev_profile = _file_profile(previous)
    diff = _profile_diff(cur_profile, prev_profile)
    schema_changed = [str(c) for c in (cur_profile.get("columns") or [])] != [str(c) for c in (prev_profile.get("columns") or [])]
    if schema_changed:
        added_columns = diff.get("added_columns_in_current") or []
        removed_columns = diff.get("removed_columns_from_current") or []
        parts = []
        if added_columns:
            parts.append(f"열 +{len(added_columns)}")
        if removed_columns:
            parts.append(f"열 -{len(removed_columns)}")
        if not parts and diff.get("columns_delta") not in (None, 0):
            parts.append(f"열 {'+' if diff['columns_delta'] > 0 else ''}{diff['columns_delta']}")
        if not parts:
            parts.append("열 순서 변경")
        if diff.get("rows_delta") not in (None, 0):
            parts.append(("행 +" if diff["rows_delta"] > 0 else "행 ") + str(diff["rows_delta"]))
        return {
            "label": " / ".join(parts),
            "schema_changed": True,
            "rows_delta": diff.get("rows_delta"),
            "columns_delta": diff.get("columns_delta"),
            "changed_cells": None,
            "added_rows": 0,
            "deleted_rows": 0,
            "modified_rows": 0,
            "added_columns": added_columns,
            "removed_columns": removed_columns,
            "added_columns_count": len(added_columns),
            "removed_columns_count": len(removed_columns),
            "checksum_equal": diff.get("checksum_equal"),
        }
    table_diff = _diff_table_between(current, previous, file=file)
    counts = table_diff.get("counts") if isinstance(table_diff, dict) else {}
    added_rows = int(counts.get("added") or 0) if isinstance(counts, dict) else 0
    deleted_rows = int(counts.get("deleted") or 0) if isinstance(counts, dict) else 0
    modified_rows = int(counts.get("modified") or 0) if isinstance(counts, dict) else 0
    changed_cells = None
    try:
        if current.suffix.lower() in {".csv", ".parquet"} and previous.suffix.lower() in {".csv", ".parquet"}:
            cur = _read_table_for_diff_frame(current)
            prev = _read_table_for_diff_frame(previous)
            if cur is not None and prev is not None:
                common_cols = [c for c in cur.columns if c in prev.columns]
                h = min(cur.height, prev.height)
                changed = 0
                if common_cols and h:
                    cur_s = cur.select([pl.col(c).cast(pl.Utf8, strict=False).alias(c) for c in common_cols]).head(h)
                    prev_s = prev.select([pl.col(c).cast(pl.Utf8, strict=False).alias(c) for c in common_cols]).head(h)
                    for c in common_cols:
                        changed += int((cur_s[c] != prev_s[c]).sum())
                changed_cells = changed
    except Exception:
        changed_cells = None
    parts = []
    if modified_rows:
        parts.append(f"수정 {modified_rows}행")
    if added_rows:
        parts.append(f"추가 {added_rows}행")
    if deleted_rows:
        parts.append(f"삭제 {deleted_rows}행")
    if diff.get("columns_delta") not in (None, 0):
        parts.append(("+" if diff["columns_delta"] > 0 else "") + f"{diff['columns_delta']}열")
    if diff.get("added_columns_in_current"):
        parts.append("컬럼추가 " + ",".join(diff["added_columns_in_current"][:3]))
    if diff.get("removed_columns_from_current"):
        parts.append("컬럼삭제 " + ",".join(diff["removed_columns_from_current"][:3]))
    return {
        "label": " / ".join(parts) if parts else "내용 수정" if not diff.get("checksum_equal") else "변경 없음",
        "rows_delta": diff.get("rows_delta"),
        "columns_delta": diff.get("columns_delta"),
        "changed_cells": changed_cells,
        "added_rows": added_rows,
        "deleted_rows": deleted_rows,
        "modified_rows": modified_rows,
        "added_columns": diff.get("added_columns_in_current") or [],
        "removed_columns": diff.get("removed_columns_from_current") or [],
        "checksum_equal": diff.get("checksum_equal"),
    }


def _version_number(version: str) -> int:
    m = re.match(r"^v(\d+)$", str(version or ""))
    return int(m.group(1)) if m else 0


def _previous_version_content(file: str, storage_version: str) -> Path | None:
    target_num = _version_number(storage_version)
    if target_num <= 1:
        return None
    vdir = _version_dir(file)
    candidates = []
    for meta_fp in vdir.glob("v*.meta.json"):
        try:
            meta = json.loads(meta_fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        version = str(meta.get("version") or meta_fp.name.split(".", 1)[0])
        num = _version_number(version)
        content = vdir / str(meta.get("content_file") or "")
        if num and num < target_num and content.exists():
            candidates.append((num, content))
    return sorted(candidates, key=lambda x: x[0])[-1][1] if candidates else None


def _table_rows_for_diff(path: Path, limit: int = 20000) -> tuple[list[str], list[dict[str, str]]]:
    df = _read_table_for_diff_frame(path, limit=limit)
    if df is None:
        return [], []
    cols = [str(c) for c in df.columns]
    rows = [{c: str(row.get(c) or "") for c in cols} for row in df.to_dicts()]
    return cols, rows


def _diff_key_candidates(columns: list[str]) -> list[list[str]]:
    by_lower = {c.lower(): c for c in columns}
    candidates = [
        ["id"],
        ["key"],
        ["product", "step_id"],
        ["product", "ppid"],
        ["product", "item_id"],
        ["process_id", "item_id"],
        ["product", "feature_name", "rule_order"],
        ["product", "feature_name"],
        ["root_lot_id", "wafer_id"],
        ["lot_id", "wafer_id"],
        ["step_id"],
        ["item_id"],
    ]
    out: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for keys in candidates:
        if all(k in by_lower for k in keys):
            combo = tuple(by_lower[k] for k in keys)
            if combo not in seen:
                seen.add(combo)
                out.append(list(combo))
    if columns:
        first = (columns[0],)
        if first not in seen:
            out.append([columns[0]])
    return out


def _infer_diff_keys(columns: list[str]) -> list[str]:
    candidates = _diff_key_candidates(columns)
    return candidates[0] if candidates else []


def _diff_file_key_candidates(path: Path, file: str = "") -> list[str]:
    out: list[str] = []

    def add(value) -> None:
        text = str(value or "").replace("\\", "/").strip()
        if text and text not in out:
            out.append(text)

    add(file)
    for root in (_db_root(), _base_root(), PATHS.data_root):
        try:
            add(path.resolve().relative_to(root.resolve()))
        except Exception:
            pass
    parent_name = path.parent.name
    if "__" in parent_name:
        add(parent_name.replace("__", "/"))
    add(path.name)
    return out


def _csv_rule_unique_key_candidates_for_diff(file: str, current: Path, columns: list[str]) -> list[list[str]]:
    if current.suffix.lower() != ".csv":
        return []
    lookup = _column_lookup(columns)
    out: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for candidate in _diff_file_key_candidates(current, file=file):
        try:
            rule = _csv_rule_for_file(candidate)
        except Exception:
            rule = {}
        for combo in rule.get("unique_keys") or []:
            cols = [lookup.get(str(col).casefold()) for col in (combo or [])]
            if not cols or any(not col for col in cols):
                continue
            key = tuple(str(col) for col in cols)
            if key not in seen:
                seen.add(key)
                out.append(list(key))
    return out


def _is_clear_diff_key(rows: list[dict[str, str]], key_cols: list[str]) -> bool:
    if not key_cols:
        return False
    seen: set[tuple[str, ...]] = set()
    for row in rows:
        key = tuple(str(row.get(col, "")) for col in key_cols)
        if all(v == "" for v in key):
            return False
        if key in seen:
            return False
        seen.add(key)
    return True


def _select_diff_key_columns(
    cur_cols: list[str],
    prev_cols: list[str],
    cur_rows: list[dict[str, str]],
    prev_rows: list[dict[str, str]],
    *,
    file: str = "",
    current: Path,
) -> tuple[list[str], str]:
    all_cols = list(dict.fromkeys([*cur_cols, *prev_cols]))
    both_cols = set(cur_cols) & set(prev_cols)
    candidates = [
        *_csv_rule_unique_key_candidates_for_diff(file, current, all_cols),
        *_diff_key_candidates(all_cols),
    ]
    seen: set[tuple[str, ...]] = set()
    for candidate in candidates:
        key_cols = [str(c) for c in candidate if str(c).strip()]
        key = tuple(key_cols)
        if not key or key in seen:
            continue
        seen.add(key)
        if not all(col in both_cols for col in key_cols):
            continue
        if _is_clear_diff_key(cur_rows, key_cols) and _is_clear_diff_key(prev_rows, key_cols):
            return key_cols, "unique_key"
    return ["__row_signature", "__occurrence"], "sequence"


def _diff_table_between(current: Path, previous: Path | None, max_changes: int = 1000, file: str = "") -> dict | None:
    if previous is None or not previous.exists():
        return None
    if current.suffix.lower() not in {".csv", ".parquet"} or previous.suffix.lower() not in {".csv", ".parquet"}:
        return None
    try:
        cur_cols, cur_rows = _table_rows_for_diff(current)
        prev_cols, prev_rows = _table_rows_for_diff(previous)
    except Exception:
        return None
    all_cols = list(dict.fromkeys([*cur_cols, *prev_cols]))
    added_columns = [c for c in cur_cols if c not in set(prev_cols)]
    removed_columns = [c for c in prev_cols if c not in set(cur_cols)]
    if cur_cols != prev_cols:
        return {
            "kind": "version_diff_table",
            "title": "직전 버전 대비 스키마 변경",
            "columns": ["rev", "changed_cols", *all_cols],
            "key_columns": [],
            "match_strategy": "schema_changed",
            "schema_changed": True,
            "columns_delta": len(cur_cols) - len(prev_cols),
            "added_columns": added_columns,
            "removed_columns": removed_columns,
            "added_columns_count": len(added_columns),
            "removed_columns_count": len(removed_columns),
            "rows": [],
            "counts": {"added": 0, "deleted": 0, "modified": 0, "unchanged": 0},
            "truncated": False,
        }
    if not all_cols:
        return None
    key_cols, match_strategy = _select_diff_key_columns(
        cur_cols,
        prev_cols,
        cur_rows,
        prev_rows,
        file=file,
        current=current,
    )
    out_rows = []
    counts = {"added": 0, "deleted": 0, "modified": 0, "unchanged": 0}

    def add_output(row: dict) -> None:
        if len(out_rows) < max_changes:
            out_rows.append(row)

    def record_added(cur: dict[str, str]) -> None:
        counts["added"] += 1
        add_output({"rev": "추가", "changed_cols": "ALL", **{c: cur.get(c, "") for c in all_cols}, "_changed_cols": all_cols})

    def record_deleted(prev: dict[str, str]) -> None:
        counts["deleted"] += 1
        add_output({"rev": "삭제", "changed_cols": "ALL", **{c: prev.get(c, "") for c in all_cols}, "_changed_cols": all_cols})

    def record_pair(cur: dict[str, str], prev: dict[str, str]) -> None:
        changed = [c for c in all_cols if cur.get(c, "") != prev.get(c, "")]
        if not changed:
            counts["unchanged"] += 1
            return
        counts["modified"] += 1
        add_output({"rev": "수정", "changed_cols": ", ".join(changed[:12]), **{c: cur.get(c, "") for c in all_cols}, "_changed_cols": changed})

    if match_strategy == "unique_key":
        def row_key(row: dict[str, str]):
            return tuple(row.get(k, "") for k in key_cols)

        cur_map = {row_key(r): r for r in cur_rows}
        prev_map = {row_key(r): r for r in prev_rows}
        keys = list(dict.fromkeys([*cur_map.keys(), *prev_map.keys()]))
        for key in keys:
            cur = cur_map.get(key)
            prev = prev_map.get(key)
            if cur is not None and prev is None:
                record_added(cur)
            elif cur is None and prev is not None:
                record_deleted(prev)
            elif cur is not None and prev is not None:
                record_pair(cur, prev)
    elif max(len(prev_rows), len(cur_rows)) > 5000:
        key_cols = ["__row_index"]
        match_strategy = "row_index"
        max_len = max(len(prev_rows), len(cur_rows))
        for idx in range(max_len):
            if idx >= len(prev_rows):
                record_added(cur_rows[idx])
            elif idx >= len(cur_rows):
                record_deleted(prev_rows[idx])
            else:
                record_pair(cur_rows[idx], prev_rows[idx])
    else:
        from difflib import SequenceMatcher

        prev_sigs = [tuple(row.get(c, "") for c in all_cols) for row in prev_rows]
        cur_sigs = [tuple(row.get(c, "") for c in all_cols) for row in cur_rows]
        matcher = SequenceMatcher(None, prev_sigs, cur_sigs, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                counts["unchanged"] += i2 - i1
            elif tag == "delete":
                for prev in prev_rows[i1:i2]:
                    record_deleted(prev)
            elif tag == "insert":
                for cur in cur_rows[j1:j2]:
                    record_added(cur)
            elif tag == "replace":
                prev_part = prev_rows[i1:i2]
                cur_part = cur_rows[j1:j2]
                pair_count = min(len(prev_part), len(cur_part))
                for offset in range(pair_count):
                    record_pair(cur_part[offset], prev_part[offset])
                for prev in prev_part[pair_count:]:
                    record_deleted(prev)
                for cur in cur_part[pair_count:]:
                    record_added(cur)
    total_changes = counts["added"] + counts["deleted"] + counts["modified"]
    return {
        "kind": "version_diff_table",
        "title": "직전 버전 대비 변경점",
        "columns": ["rev", "changed_cols", *all_cols],
        "key_columns": key_cols,
        "match_strategy": match_strategy,
        "added_columns": added_columns,
        "removed_columns": removed_columns,
        "added_columns_count": len(added_columns),
        "removed_columns_count": len(removed_columns),
        "rows": out_rows,
        "counts": counts,
        "truncated": total_changes > max_changes,
    }


def _snapshot_base_file_version(
    target: Path,
    file: str,
    *,
    actor: str = "",
    action: str = "edit",
    note: str = "",
    diff_previous: Path | None = None,
) -> dict | None:
    if not target.exists() or not _base_file_versioned(file, target):
        return None
    vdir = _version_dir(file)
    vdir.mkdir(parents=True, exist_ok=True)
    vnum = _next_file_version(vdir)
    version = f"v{vnum}"
    previous_content = _latest_version_content(vdir)
    content_name = f"{version}{target.suffix.lower() or '.bin'}"
    content_fp = vdir / content_name
    shutil.copy2(target, content_fp)
    rows, cols = _file_shape(target)
    previous_for_diff = diff_previous if diff_previous is not None and diff_previous.exists() else previous_content
    if previous_content is None and previous_for_diff is not None and previous_for_diff.exists():
        prev_rows, prev_cols = _file_shape(previous_for_diff)
        display_version = _bump_semver("v1.0", rows=rows, columns=cols, prev_rows=prev_rows, prev_columns=prev_cols)
    else:
        display_version = _next_semver(vdir, rows=rows, columns=cols)
    change_summary = _snapshot_change_summary(target, previous_for_diff, file=file)
    save_diff_table = _diff_table_between(target, previous_for_diff, file=file)
    meta = {
        "version": version,
        "display_version": display_version,
        "file": file,
        "artifact_type": "edm_single_file",
        "actor": actor or "",
        "action": action,
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "size": target.stat().st_size,
        "rows": rows,
        "columns": cols,
        "checksum": _file_sha256(target),
        "source_path": str(target),
        "content_file": content_name,
        "note": note or "",
        "change_summary": change_summary,
        "save_diff_table": save_diff_table,
    }
    (vdir / f"{version}.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    _cap_file_versions(vdir)
    return meta


_FILE_VERSION_ACTIONS = frozenset({
    "edit", "rollback", "pre-rollback", "legacy-backup", "system_import",
})


def _normalize_file_version_action(value: object) -> str:
    """Map legacy or future action labels to the public version contract.

    The original metadata remains unchanged on disk.  Unknown producer labels
    such as the historical ``valve-alert`` action are exposed as
    ``system_import`` so one old record cannot break the entire history list.
    """
    action = str(value or "edit").strip() or "edit"
    return action if action in _FILE_VERSION_ACTIONS else "system_import"


def _list_base_file_versions(file: str) -> list[dict]:
    vdir = _version_dir(file)
    if not vdir.is_dir():
        return []
    rows = []
    current_profile = {}
    latest_storage = ""
    try:
        target = _resolve_base_file_for_version(file)
        current_profile = _file_profile(target)
        latest = _latest_base_version_meta(file)
        if latest is not None:
            latest_meta, _ = latest
            latest_storage = str(latest_meta.get("version") or "")
    except Exception:
        current_profile = {}
        latest_storage = ""
    for meta_fp in sorted(vdir.glob("v*.meta.json"), key=lambda p: (p.stat().st_mtime, p.name), reverse=True):
        try:
            meta = json.loads(meta_fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        storage_version = meta.get("version") or meta_fp.name.split(".", 1)[0]
        profile_override = None
        if latest_storage and str(storage_version) == latest_storage:
            profile_override = _post_save_profile_matching_current(meta, current_profile)
        change_summary = meta.get("change_summary") or {}
        if not any(change_summary.get(k) for k in ("added_rows", "deleted_rows", "modified_rows", "added_columns", "removed_columns", "columns_delta")):
            content_fp = vdir / str(meta.get("content_file") or "")
            try:
                diff_table = _diff_table_between(content_fp, _previous_version_content(file, storage_version), file=file)
                counts = diff_table.get("counts") if isinstance(diff_table, dict) else {}
                if isinstance(counts, dict):
                    added_rows = int(counts.get("added") or 0)
                    deleted_rows = int(counts.get("deleted") or 0)
                    modified_rows = int(counts.get("modified") or 0)
                    added_columns = list(diff_table.get("added_columns") or []) if isinstance(diff_table, dict) else []
                    removed_columns = list(diff_table.get("removed_columns") or []) if isinstance(diff_table, dict) else []
                    parts = []
                    if modified_rows:
                        parts.append(f"수정 {modified_rows}행")
                    if added_rows:
                        parts.append(f"추가 {added_rows}행")
                    if deleted_rows:
                        parts.append(f"삭제 {deleted_rows}행")
                    if added_columns:
                        parts.append(f"열 +{len(added_columns)}")
                    if removed_columns:
                        parts.append(f"열 -{len(removed_columns)}")
                    if parts:
                        change_summary = {
                            **change_summary,
                            "label": " / ".join(parts),
                            "added_rows": added_rows,
                            "deleted_rows": deleted_rows,
                            "modified_rows": modified_rows,
                            "added_columns": added_columns,
                            "removed_columns": removed_columns,
                            "added_columns_count": len(added_columns),
                            "removed_columns_count": len(removed_columns),
                            "columns_delta": diff_table.get("columns_delta", change_summary.get("columns_delta")),
                        }
            except Exception:
                pass
        rows.append(FileVersionMeta(**{
            "version": meta.get("display_version") or meta.get("version") or meta_fp.name.split(".", 1)[0],
            "storage_version": storage_version,
            "file": meta.get("file") or file,
            "artifact_type": meta.get("artifact_type") or "edm_single_file",
            "actor": meta.get("actor") or "",
            "action": _normalize_file_version_action(meta.get("action")),
            "created_at": meta.get("created_at") or "",
            "size": profile_override.get("size") if profile_override else meta.get("size"),
            "rows": profile_override.get("rows") if profile_override else meta.get("rows"),
            "columns": _profile_column_count(profile_override) if profile_override else meta.get("columns"),
            "checksum": (profile_override.get("checksum") if profile_override else meta.get("checksum")) or "",
            "note": meta.get("note") or "",
            "change_summary": change_summary,
        }).dict())
    return rows


def _legacy_history_versions(target: Path, file: str) -> list[dict]:
    hist_dir = target.parent / BASE_EDIT_HISTORY_DIR
    if not hist_dir.is_dir():
        return []
    rows = []
    suffix = "_" + target.name
    for fp in sorted(hist_dir.iterdir(), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
        if not fp.is_file() or not fp.name.endswith(suffix):
            continue
        ts_token = fp.name[: -len(suffix)]
        created = ""
        try:
            created = datetime.datetime.strptime(ts_token, "%Y%m%d-%H%M%S").isoformat(timespec="seconds")
        except Exception:
            created = datetime.datetime.fromtimestamp(fp.stat().st_mtime).isoformat(timespec="seconds")
        rows_count, cols_count = _file_shape(fp)
        try:
            checksum = _file_sha256(fp)
            size = fp.stat().st_size
        except Exception:
            checksum = ""
            size = None
        rows.append(FileVersionMeta(**{
            "version": "legacy_" + fp.name,
            "storage_version": "legacy_" + fp.name,
            "file": file,
            "artifact_type": "legacy_history",
            "actor": "",
            "action": "legacy-backup",
            "created_at": created,
            "size": size,
            "rows": rows_count,
            "columns": cols_count,
            "checksum": checksum,
            "note": "Legacy .history backup",
        }).dict())
    return rows


def _resolve_base_version_content(file: str, version: str, target: Path) -> tuple[Path, dict]:
    clean_version = safe_filename(version)
    if re.match(r"^v\d+\.\d+$", clean_version):
        vdir = _version_dir(file)
        for meta_fp in vdir.glob("v*.meta.json"):
            try:
                meta = json.loads(meta_fp.read_text(encoding="utf-8"))
            except Exception:
                continue
            if str(meta.get("display_version") or "") == clean_version:
                clean_version = str(meta.get("version") or meta_fp.name.split(".", 1)[0])
                break
    if clean_version.startswith("legacy_"):
        legacy_name = clean_version[len("legacy_"):]
        hist_root = (target.parent / BASE_EDIT_HISTORY_DIR).resolve()
        cand = (hist_root / legacy_name).resolve()
        try:
            cand.relative_to(hist_root)
        except ValueError:
            raise HTTPException(400, "Invalid legacy version path")
        if not cand.is_file() or not cand.name.endswith("_" + target.name):
            raise HTTPException(404, f"Legacy version not found: {version}")
        meta = next((v for v in _legacy_history_versions(target, file) if v.get("version") == clean_version), None) or {
            "version": clean_version,
            "file": file,
            "artifact_type": "legacy_history",
            "action": "legacy-backup",
        }
        return cand, meta
    vdir = _version_dir(file)
    meta_fp = vdir / f"{clean_version}.meta.json"
    if not meta_fp.exists():
        raise HTTPException(404, f"Version not found: {version}")
    try:
        meta = json.loads(meta_fp.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(500, "Cannot read version metadata")
    content_fp = vdir / str(meta.get("content_file") or "")
    if not content_fp.exists():
        raise HTTPException(404, "Version content missing")
    post_save_profile, current_profile = _latest_post_save_profile_matching_current(file, target, meta)
    if post_save_profile is not None and current_profile is not None:
        return target, _version_meta_with_profile(meta, current_profile, state="current_post_save_compat")
    return content_fp, meta


def _migrate_legacy_history(target: Path, file: str, *, actor: str = "", note: str = "") -> dict:
    if not _base_file_versioned(file, target):
        raise HTTPException(400, "This file is not configured for EDM version migration")
    hist_dir = target.parent / BASE_EDIT_HISTORY_DIR
    if not hist_dir.is_dir():
        return {"migrated": 0, "skipped": 0}
    existing_checksums = {str(v.get("checksum") or "") for v in _list_base_file_versions(file)}
    migrated = 0
    skipped = 0
    suffix = "_" + target.name
    vdir = _version_dir(file)
    vdir.mkdir(parents=True, exist_ok=True)
    for fp in sorted(hist_dir.iterdir(), key=lambda p: p.stat().st_mtime if p.exists() else 0):
        if not fp.is_file() or not fp.name.endswith(suffix):
            continue
        checksum = _file_sha256(fp)
        if checksum in existing_checksums:
            skipped += 1
            continue
        version = f"v{_next_file_version(vdir)}"
        content_name = f"{version}{target.suffix.lower() or '.bin'}"
        shutil.copy2(fp, vdir / content_name)
        rows, cols = _file_shape(fp)
        display_version = _next_semver(vdir, rows=rows, columns=cols)
        try:
            ts_token = fp.name[: -len(suffix)]
            created_at = datetime.datetime.strptime(ts_token, "%Y%m%d-%H%M%S").isoformat(timespec="seconds")
        except Exception:
            created_at = datetime.datetime.fromtimestamp(fp.stat().st_mtime).isoformat(timespec="seconds")
        meta = {
            "version": version,
            "display_version": display_version,
            "file": file,
            "artifact_type": "edm_single_file",
            "actor": actor or "",
            "action": "system_import",
            "created_at": created_at,
            "size": fp.stat().st_size,
            "rows": rows,
            "columns": cols,
            "checksum": checksum,
            "source_path": str(target),
            "content_file": content_name,
            "note": note or f"Migrated from legacy .history: {fp.name}",
            "change_summary": {"label": "migrated legacy backup"},
        }
        (vdir / f"{version}.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        existing_checksums.add(checksum)
        migrated += 1
    _cap_file_versions(vdir)
    return {"migrated": migrated, "skipped": skipped}


def _replace_with_retry(tmp_name: str, target: Path, attempts: int = 5, delay: float = 0.08):
    """os.replace 재시도 — 읽던 쪽이 손을 놓을 때까지 잠깐 기다린다.

    Windows 는 다른 프로세스/스레드가 대상 파일을 열고 있으면 replace 가
    PermissionError(WinError 5) 로 실패한다. 미리보기 스캔이 파일 핸들을 아직
    들고 있는 순간에 저장을 누르면 "Save failed" 로 떨어졌다. 리눅스 운영에서는
    발생하지 않지만, 개발 PC(Windows)에서는 실제로 저장이 막힌다.
    """
    import gc

    for i in range(max(1, attempts)):
        try:
            os.replace(tmp_name, target)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            gc.collect()          # lazy scan 이 잡고 있던 핸들 해제 유도
            time.sleep(delay)


def _write_text_atomic(target: Path, payload: str):
    payload_bytes = payload.encode("utf-8")
    if len(payload_bytes) > BASE_FILE_EDIT_MAX_BYTES:
        raise HTTPException(400, f"Replace payload too large: {len(payload_bytes):,} bytes (max {BASE_FILE_EDIT_MAX_BYTES:,})")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as fp_out:
            fp_out.write(payload_bytes)
        _replace_with_retry(tmp_name, target)
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except Exception:
            pass


def _write_parquet_atomic(target: Path, df: "pl.DataFrame"):
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        os.close(fd)
        df.write_parquet(tmp_name)
        _replace_with_retry(tmp_name, target)
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except Exception:
            pass


def _ensure_base_file_backup(target: Path) -> str | None:
    try:
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_root = target.parent / BASE_EDIT_HISTORY_DIR
        backup_root.mkdir(exist_ok=True)
        backup = backup_root / f"{ts}_{target.name}"
        shutil.copy2(target, backup)
        return str(backup)
    except Exception as e:
        logger.warning("base-file/save backup skipped file=%s: %s", target, e)
        return None


def _db_root():
    return resolve_existing_root("db", PATHS.db_root)


def _base_edit_backup_root() -> Path:
    """장기 백업 폴더('DB BACKUP'). DB 루트 아래가 기본, 못 쓰면 data_root 로 물러난다.

    사내에서는 DB 루트가 읽기전용 공유일 수 있다 — 그때 저장 자체를 실패시키면
    안 되므로 조용히 data_root 로 떨어진다.
    """
    for base in (Path(_db_root()), Path(PATHS.data_root)):
        try:
            root = base / BASE_EDIT_BACKUP_DIR_NAME
            root.mkdir(parents=True, exist_ok=True)
            return root
        except Exception:
            continue
    return Path(PATHS.data_root) / BASE_EDIT_BACKUP_DIR_NAME


def _base_file_version_number(version_meta: dict | None) -> int:
    """저장 스냅샷의 순번(v1 → 1). 못 읽으면 0."""
    try:
        return int(str((version_meta or {}).get("version") or "").lstrip("vV"))
    except Exception:
        return 0


def _archive_base_file_every_n_edits(target: Path, version_meta: dict | None) -> str | None:
    """BASE_EDIT_BACKUP_EVERY 번째 저장마다 원본 사본을 'DB BACKUP' 에 남긴다.

    버전 이력(file_versions)은 최근 BASE_VERSION_CAP 개만 유지하므로 그보다
    오래된 상태로는 되돌릴 수 없다. 저장 횟수는 버전 번호(v1, v2, …)가 이미
    단조 증가로 세고 있으니 그 번호로만 판단한다 — 별도 카운터를 두면 재시작·
    다중 서버에서 어긋난다.

    백업 실패가 저장 실패로 번지면 안 되므로 모든 오류를 삼키고 None 을 준다.
    """
    number = _base_file_version_number(version_meta)
    if number <= 0 or number % BASE_EDIT_BACKUP_EVERY != 0:
        return None
    try:
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = _base_edit_backup_root() / f"{target.stem}_{ts}{target.suffix.lower()}"
        shutil.copy2(target, dest)
        logger.info("base-file/save periodic backup file=%s -> %s (v%d)", target.name, dest, number)
        return str(dest)
    except Exception as e:
        logger.warning("base-file/save periodic backup skipped file=%s: %s", target, e)
        return None


def _base_root():
    return resolve_existing_root("base", PATHS.base_root)


def _date_key_from_text(text: str) -> str:
    m = _DATE_TOKEN_RE.search(str(text or ""))
    if not m:
        return ""
    return "".join(m.groups())


def _date_label_from_key(key: str) -> str:
    key = str(key or "")
    if len(key) != 8:
        return key
    return f"{key[:4]}-{key[4:6]}-{key[6:]}"


def _date_key_from_path(path: Path) -> str:
    for part in path.parts:
        if part.startswith("date="):
            key = _date_key_from_text(part[len("date="):])
            if key:
                return key
    return _date_key_from_text(path.name)


def _latest_date_label_for_dir(directory: Path) -> str:
    keys: list[str] = []
    try:
        for d in directory.iterdir():
            if d.is_dir() and d.name.startswith("date="):
                key = _date_key_from_path(d)
                if key:
                    keys.append(key)
    except Exception:
        pass
    if not keys:
        try:
            for d in directory.rglob("date=*"):
                if d.is_dir():
                    key = _date_key_from_path(d)
                    if key:
                        keys.append(key)
                    if len(keys) >= 200:
                        break
        except Exception:
            pass
    if not keys:
        for fp in data_files_limited(directory, limit=2000):
            key = _date_key_from_path(fp)
            if key:
                keys.append(key)
    return _date_label_from_key(max(keys)) if keys else ""


def _latest_order_column(columns: list[str]) -> str:
    by_lower = {str(c).lower(): str(c) for c in columns}
    for name in _LATEST_COLUMN_PRIORITY:
        if name in by_lower:
            return by_lower[name]
    for name in columns:
        low = str(name).lower()
        if "tkout" in low or "timestamp" in low:
            return str(name)
    for name in columns:
        low = str(name).lower()
        if low.endswith("time") or low.endswith("date") or "datetime" in low:
            return str(name)
    return ""


def _log_dl(username, product, sql, rows, cols, select_cols="", size_bytes=0):
    jsonl_append(DL_LOG, {
        "username": username, "product": product, "sql": sql or "",
        "rows": rows, "cols": cols, "select_cols": select_cols,
        "size_mb": round(size_bytes / 1e6, 2),
    })


def _list_cache_get(key: tuple):
    cached = _LIST_CACHE.get(key)
    if not cached:
        return None
    ts, payload = cached
    if time.monotonic() - ts > LIST_CACHE_TTL_SEC:
        _LIST_CACHE.pop(key, None)
        return None
    return copy.deepcopy(payload)


def _list_cache_set(key: tuple, payload):
    if len(_LIST_CACHE) > 128:
        _LIST_CACHE.clear()
    _LIST_CACHE[key] = (time.monotonic(), copy.deepcopy(payload))
    return payload


def _path_sig(path: Path) -> tuple:
    try:
        st = path.stat()
        return (str(path.resolve()), st.st_mtime, st.st_size)
    except Exception:
        return (str(path), 0.0, 0)


@router.get("/domain")
def domain_info(request: Request = None):
    """v7.2: Expose canonical domain model to frontend (level hierarchy, granularity, DB registry)."""
    _require_filebrowser_user(request)
    from core.domain import DB_REGISTRY, VISIBLE_CANONICAL, LEVEL_ORDER
    return {
        "dbs": {k: v for k, v in DB_REGISTRY.items() if k in VISIBLE_CANONICAL or k == "ML_TABLE"},
        "level_order": LEVEL_ORDER,
        "visible": sorted(list(VISIBLE_CANONICAL)),
    }
