def _refresh_product_stat(key: str, prod_dir: Path) -> None:
    try:
        stat = _fbcache.stat_for_db_product(prod_dir)
        with _PRODUCT_STAT_LOCK:
            _PRODUCT_STAT_CACHE[key] = (time.time(), dict(stat) if stat else None)
    except Exception:  # noqa: BLE001
        pass
    finally:
        with _PRODUCT_STAT_LOCK:
            _PRODUCT_STAT_INFLIGHT.discard(key)


def _stat_for_db_product_cached(prod_dir: Path | None) -> dict | None:
    """stat_for_db_product 의 stale-while-revalidate 캐시.

    전체 파티션 rglob 서명 계산은 대형 제품에서 요청당 수 초까지 걸린다.
    TTL 안에서는 마지막 서명을 재사용하고, 만료돼도 이전 서명을 즉시 반환한 뒤
    백그라운드 스레드로 갱신한다. 새로 내려온 파티션의 preview 반영은 최대
    TTL(30초)+재계산 시간만큼 늦을 수 있다 — 신선도보다 클릭 응답을 우선한다.
    """
    if prod_dir is None:
        return None
    key = str(prod_dir)
    now = time.time()
    with _PRODUCT_STAT_LOCK:
        entry = _PRODUCT_STAT_CACHE.get(key)
        if entry is not None:
            ts, stat = entry
            if now - ts >= _PRODUCT_STAT_TTL_SEC and key not in _PRODUCT_STAT_INFLIGHT:
                _PRODUCT_STAT_INFLIGHT.add(key)
                threading.Thread(
                    target=_refresh_product_stat, args=(key, prod_dir), daemon=True,
                ).start()
            return dict(stat) if stat else None
    stat = _fbcache.stat_for_db_product(prod_dir)
    with _PRODUCT_STAT_LOCK:
        _PRODUCT_STAT_CACHE[key] = (time.time(), dict(stat) if stat else None)
    return stat


def _resolve_product_dir_fast(root: str, product: str) -> Path | None:
    """Resolve a logical product folder without recursively listing all files."""
    root_path = (_db_root() / root).resolve()
    if not root_path.is_dir():
        return None
    direct = root_path / product
    if direct.is_dir():
        return direct
    ci = resolve_named_child(root_path, product)
    if ci is not None and ci.is_dir():
        return ci
    target = str(product or "").casefold()
    try:
        for name, path, _structure in iter_source_product_dirs(root_path):
            if str(name or "").casefold() == target:
                return path
    except Exception:
        return None
    return None


def _first_data_file(directory: Path, suffixes: tuple[str, ...]) -> Path | None:
    suffix_set = {s.lower() for s in suffixes}
    try:
        for fp in directory.rglob("*"):
            if fp.is_file() and fp.suffix.lower() in suffix_set:
                return fp
    except Exception:
        return None
    return None


def _fast_product_meta_response(root: str, product: str, cols: int,
                                settings: dict | None = None,
                                page: int = 0, page_size: int = 200) -> dict | None:
    """Return schema-only metadata for huge DB products without scanning every partition."""
    prod_dir = _resolve_product_dir_fast(root, product)
    if prod_dir is None:
        return None
    fp = _first_data_file(prod_dir, (".parquet",)) or _first_data_file(prod_dir, (".csv",))
    if fp is None:
        return None
    cached_meta = None
    schema_full = {}
    if fp.suffix.lower() == ".parquet":
        try:
            from core.parquet_perf import read_meta
            cached_meta = read_meta(fp)
        except Exception:
            cached_meta = None
        cached_schema = (cached_meta or {}).get("schema") or {}
        if cached_schema:
            schema_full = {str(k): str(v) for k, v in cached_schema.items()}
        else:
            lf = scan_one_file(fp)
            if lf is None:
                return None
            schema_obj = lf.collect_schema()
            schema_full = {n: str(schema_obj[n]) for n in schema_obj.names()}
    else:
        lf = scan_one_file(fp)
        if lf is None:
            return None
        schema_obj = lf.collect_schema()
        schema_full = {n: str(schema_obj[n]) for n in schema_obj.names()}
    if "INLINE" in str(root or "").upper():
        schema_full = {
            str(k): str(v)
            for k, v in schema_full.items()
            if str(k).lower() not in {"shot_x", "shot_y"}
        }
    all_cols_full = list(schema_full.keys())
    _, page_size, _ = _page_args(page, page_size)
    try:
        st = fp.stat()
        source_modified = st.st_mtime
        source_size = st.st_size
    except Exception:
        source_modified = None
        source_size = None
    return {
        "kind": "table",
        "root": root,
        "product": product,
        "all_columns": all_cols_full,
        "total_cols": len(all_cols_full),
        "columns": all_cols_full[:_preview_cols_limit(cols or _settings_preview_max_columns(settings))],
        "dtypes": schema_full,
        "data": [],
        "showing": 0,
        "showing_cols": [],
        "total_rows": int((cached_meta or {}).get("row_count") or 0),
        "meta_only": True,
        "page": page,
        "page_size": page_size,
        "has_more": False,
        "meta_cached": bool(cached_meta),
        "meta_sample_file": fp.name,
        "source_path": str(prod_dir),
        "source_size": source_size,
        "source_modified": source_modified,
        "row_count_unknown": not bool(cached_meta),
    }


def _preview_cols_limit(raw: int | None = None) -> int:
    try:
        return max(1, min(200, int(raw or 20)))
    except Exception:
        return 20


def _is_ml_table_file(fp_or_name) -> bool:
    try:
        stem = Path(str(fp_or_name or "")).stem
    except Exception:
        stem = str(fp_or_name or "")
    return stem.upper().startswith("ML_TABLE_")


def _has_view_filter(sql: str, select_cols: str) -> bool:
    return bool(str(sql or "").strip() or str(select_cols or "").strip())


def _has_view_transform(sql: str, select_cols: str, aggregate_spec: dict | None = None) -> bool:
    return bool(_has_view_filter(sql, select_cols) or aggregate_spec)


def _is_cache_file_ref(file: str, fp: Path | None = None) -> bool:
    try:
        rel = Path(str(file or "").strip())
        if rel.parts and str(rel.parts[0]).casefold() == _SINGLE_FILE_STEP_CACHE_DIR:
            return True
    except Exception:
        pass
    try:
        return fp is not None and fp.parent.name.casefold() == _SINGLE_FILE_STEP_CACHE_DIR
    except Exception:
        return False


def _selected_columns(all_columns: list[str], select_cols: str, preview_cols: int | None = None) -> tuple[list[str], bool]:
    if select_cols and select_cols.strip():
        allowed = set(all_columns)
        selected = [c.strip() for c in select_cols.split(",") if c.strip() in allowed]
        return selected, False
    limit = _preview_cols_limit(preview_cols)
    return all_columns[:limit], len(all_columns) > limit


def _lazy_filter_expr(sql: str, columns: list[str], dtypes: dict | None = None):
    s = _normalize_polars_view_sql_filter(sql, columns, dtypes)
    if not s:
        return None
    try:
        return pl.sql_expr(s)
    except Exception as sql_err:
        try:
            ns = {c: pl.col(c) for c in columns}
            return eval(s, {"__builtins__": {}, "pl": pl}, ns)  # noqa: S307
        except Exception as eval_err:
            raise HTTPException(400, f"SQL error: {sql_err} | expr error: {eval_err}")


_AI_SQL_FORBIDDEN_RE = re.compile(
    r";|--|/\*|\*/|\b("
    r"ATTACH|CALL|COPY|CREATE|DELETE|DETACH|DROP|EXPORT|FROM|GROUP\s+BY|"
    r"IMPORT|INSERT|INSTALL|JOIN|LIMIT|LOAD|OFFSET|ORDER\s+BY|PRAGMA|"
    r"SELECT|SET|TRUNCATE|UPDATE|VACUUM|WITH"
    r")\b",
    re.I,
)
_AI_SQL_IGNORE_TOKENS = {
    *_SQL_EXPR_IGNORE_TOKENS,
    "and", "or", "not", "like", "ilike", "between", "in", "is",
    "null", "true", "false", "where", "is_in", "is_null", "is_not_null",
    "str", "contains", "starts_with", "ends_with", "cast", "try_cast", "as",
    "bigint", "int64", "integer", "int", "double", "float",
    "date", "timestamp", "datetime", "time",
}
_AI_SQL_CAST_TYPES = {
    "DOUBLE": "DOUBLE",
    "FLOAT": "FLOAT",
    "BIGINT": "BIGINT",
    "INTEGER": "INTEGER",
    "INT": "INT",
    "DATE": "DATE",
    "TIMESTAMP": "TIMESTAMP",
    "DATETIME": "DATETIME",
    "TIME": "TIME",
}
_AI_SQL_CAST_CALL_RE = re.compile(r"\b(?P<fn>TRY_CAST|CAST)\s*\(", re.I)
_AI_SQL_CAST_BODY_RE = re.compile(
    r'^\s*(?P<col>`(?:``|[^`])+`|"(?:""|[^"])+"|[A-Za-z_][A-Za-z0-9_]*)\s+AS\s+(?P<type>[A-Za-z0-9_]+)\s*$',
    re.I,
)
_AI_SQL_ARITHMETIC_RE = re.compile(r"(?:\+|\*|/(?![/*]))")
_AI_SQL_CAST_GUIDE = {
    "syntax": "CAST(column AS DOUBLE|FLOAT|BIGINT|INTEGER|INT|DATE|TIMESTAMP|DATETIME|TIME)",
    "try_cast": "TRY_CAST(column AS same_types)",
    "execution": "CAST and TRY_CAST are normalized to TRY_CAST; conversion failures are excluded by comparisons.",
    "scope": "WHERE/filter expression only. Do not use CAST in ORDER BY, SELECT, arithmetic, nested functions, joins, or DDL/DML.",
    "examples": [
        "CAST(value AS DOUBLE) >= 10",
        "CAST(tkout_time AS TIMESTAMP) >= '2024-04-21'",
    ],
}


def _quote_sql_filter_identifier(name: str) -> str:
    text = str(name or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text):
        return text
    return "`" + text.replace("`", "``") + "`"


def _unquote_sql_filter_identifier(raw: str) -> str:
    text = str(raw or "").strip()
    if len(text) >= 2 and text[0] == "`" and text[-1] == "`":
        return text[1:-1].replace("``", "`")
    return text


def _sql_filter_identifiers_to_duckdb(expr: str) -> str:
    def repl(match: re.Match) -> str:
        return duckdb_engine.quote_ident(_unquote_sql_filter_identifier(match.group(0)))

    parts = re.split(r"('(?:''|[^'])*'|\"(?:\"\"|[^\"])*\")", str(expr or ""))
    for idx in range(0, len(parts), 2):
        parts[idx] = re.sub(r"`(?:``|[^`])+`", repl, parts[idx])
    return "".join(parts)


def _strip_sql_literals(expr: str) -> str:
    return re.sub(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|`(?:``|[^`])+`", " ", str(expr or ""))


def _canonicalize_sql_columns(expr: str, columns: list[str]) -> str:
    lookup = _column_lookup(columns)
    if not lookup:
        return str(expr or "").strip()
    parts = re.split(r"('(?:''|[^'])*'|\"(?:\"\"|[^\"])*\")", str(expr or ""))
    for idx in range(0, len(parts), 2):
        parts[idx] = re.sub(
            r"`(?:``|[^`])+`",
            lambda m: _quote_sql_filter_identifier(
                lookup.get(_unquote_sql_filter_identifier(m.group(0)).casefold(), _unquote_sql_filter_identifier(m.group(0)))
            ),
            parts[idx],
        )
        parts[idx] = re.sub(
            r"\b[A-Za-z_][A-Za-z0-9_]*\b",
            lambda m: lookup.get(m.group(0).casefold(), m.group(0)),
            parts[idx],
        )
    return "".join(parts).strip()


def _sql_unknown_quoted_columns(expr: str, columns: list[str]) -> list[str]:
    lookup = _column_lookup(columns)
    if not lookup:
        return []
    missing: list[str] = []
    for match in re.finditer(r"`(?:``|[^`])+`", str(expr or "")):
        token = _unquote_sql_filter_identifier(match.group(0))
        if lookup.get(token.casefold()):
            continue
        if token not in missing:
            missing.append(token)
    return missing


def _sql_missing_columns(expr: str, columns: list[str]) -> list[str]:
    lookup = _column_lookup(columns)
    if not lookup:
        return []
    missing: list[str] = []
    for token in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", _strip_sql_literals(expr)):
        key = token.casefold()
        if key in lookup or key in _AI_SQL_IGNORE_TOKENS:
            continue
        if token not in missing:
            missing.append(token)
    return missing


_AI_SQL_COMPARE_RE = re.compile(
    r"\b(?P<col>[A-Za-z_][A-Za-z0-9_]*)\s*"
    r"(?P<op>>=|<=|<>|!=|==|=|>|<)\s*"
    r"(?P<rhs>'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|"
    r"\d{4}[-/.]\d{1,2}(?:[-/.]\d{1,2})?(?:[T\s]\d{1,2}:\d{1,2}(?::\d{1,2})?)?|"
    r"-?\d+(?:\.\d+)?)",
    re.I,
)


def _unquote_ai_sql_literal(raw: str) -> tuple[str, bool]:
    text = str(raw or "").strip()
    if len(text) >= 2 and text[0] == "'" and text[-1] == "'":
        return text[1:-1].replace("''", "'"), True
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return text[1:-1].replace('""', '"'), True
    return text, False


def _validate_ai_sql_date_literals(sql: str, columns: list[str]) -> None:
    lookup = _column_lookup(columns)
    date_cols = {
        lookup.get(str(col).casefold(), str(col)).casefold()
        for col in columns
        if _looks_date_like_column(str(col))
    }
    if not date_cols:
        return
    for match in _AI_SQL_COMPARE_RE.finditer(str(sql or "")):
        col = lookup.get(match.group("col").casefold(), match.group("col"))
        if col.casefold() not in date_cols:
            continue
        value, quoted = _unquote_ai_sql_literal(match.group("rhs"))
        compact_or_partial = bool(re.fullmatch(r"\d{4}(?:[-/.]?\d{1,2})?", value))
        compact_ymd = bool(re.fullmatch(r"\d{8}", value))
        slash_or_dot_date = bool(re.fullmatch(r"\d{4}[/.]\d{1,2}[/.]\d{1,2}(?:[T\s].*)?", value))
        bare_number = (not quoted) and bool(re.fullmatch(r"-?\d+(?:\.\d+)?", value))
        bare_date = (not quoted) and bool(re.fullmatch(r"\d{4}[-/.]\d{1,2}(?:[-/.]\d{1,2})?(?:[T\s].*)?", value))
        if compact_or_partial or compact_ymd or slash_or_dot_date or bare_number or bare_date:
            raise ValueError(
                "AI SQL date/time filters must use complete quoted ISO literals "
                "such as '2024-04-20' or '2024-04-20T13:30:00'"
            )


def _temporal_dtype_kind(dtype) -> str:
    text = str(dtype or "").casefold()
    if not text:
        return ""
    if "datetime" in text or "timestamp" in text:
        return "datetime"
    if re.search(r"\bdate\b", text):
        return "date"
    if re.search(r"\btime\b", text):
        return "time"
    return ""


def _format_temporal_time_sql(hour: int, minute: int, second: str) -> str:
    if "." in second:
        sec, frac = second.split(".", 1)
        return f"{hour:02d}:{minute:02d}:{int(sec):02d}.{frac}"
    return f"{hour:02d}:{minute:02d}:{int(second):02d}"


def _parse_temporal_literal(raw: str) -> tuple[str, str] | None:
    value, _quoted = _unquote_ai_sql_literal(raw)
    text = str(value or "").strip().replace("T", " ")
    text = re.sub(r"\s+", " ", text).rstrip("Z")
    date_match = re.fullmatch(
        r"(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})"
        r"(?: (?P<h>\d{1,2}):(?P<mi>\d{1,2})(?::(?P<s>\d{1,2}(?:\.\d{1,6})?))?)?",
        text,
    )
    if date_match:
        year = int(date_match.group("y"))
        month = int(date_match.group("m"))
        day = int(date_match.group("d"))
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return None
        date_sql = f"{year:04d}-{month:02d}-{day:02d}"
        hour = date_match.group("h")
        minute = date_match.group("mi")
        second = date_match.group("s") or "00"
        if hour is None or minute is None:
            return "date", date_sql
        h_int = int(hour)
        mi_int = int(minute)
        sec_head = second.split(".", 1)[0]
        if not (0 <= h_int <= 23 and 0 <= mi_int <= 59 and 0 <= int(sec_head) <= 59):
            return None
        time_sql = _format_temporal_time_sql(h_int, mi_int, second)
        return "datetime", f"{date_sql} {time_sql}"
    time_match = re.fullmatch(r"(?P<h>\d{1,2}):(?P<mi>\d{1,2})(?::(?P<s>\d{1,2}(?:\.\d{1,6})?))?", text)
    if not time_match:
        return None
    h_int = int(time_match.group("h"))
    mi_int = int(time_match.group("mi"))
    second = time_match.group("s") or "00"
    sec_head = second.split(".", 1)[0]
    if not (0 <= h_int <= 23 and 0 <= mi_int <= 59 and 0 <= int(sec_head) <= 59):
        return None
    time_sql = _format_temporal_time_sql(h_int, mi_int, second)
    return "time", time_sql


def _temporal_runtime_compare(col: str, op: str, raw_literal: str, dtype_kind: str) -> str | None:
    parsed = _parse_temporal_literal(raw_literal)
    if not parsed:
        return None
    literal_kind, literal_sql = parsed
    column_sql = duckdb_engine.quote_ident(col)
    op_sql = "=" if op == "==" else op
    if dtype_kind == "time" or (not dtype_kind and literal_kind == "time"):
        if literal_kind == "date":
            return None
        time_sql = literal_sql[-8:] if literal_kind == "datetime" else literal_sql
        lhs = column_sql if dtype_kind == "time" else f"TRY_CAST({column_sql} AS TIME)"
        return f"{lhs} {op_sql} TIME '{time_sql}'"
    if dtype_kind == "date" and literal_kind == "date":
        return f"{column_sql} {op_sql} DATE '{literal_sql}'"
    timestamp_sql = f"{literal_sql} 00:00:00" if literal_kind == "date" else literal_sql
    lhs = column_sql if dtype_kind == "datetime" else f"TRY_CAST({column_sql} AS TIMESTAMP)"
    return f"{lhs} {op_sql} TIMESTAMP '{timestamp_sql}'"


def _sql_literal_spans(sql: str) -> list[tuple[int, int]]:
    text = str(sql or "")
    spans: list[tuple[int, int]] = []
    idx = 0
    while idx < len(text):
        if text[idx] not in {"'", '"'}:
            idx += 1
            continue
        quote = text[idx]
        start = idx
        idx += 1
        while idx < len(text):
            if text[idx] == quote:
                if idx + 1 < len(text) and text[idx + 1] == quote:
                    idx += 2
                    continue
                idx += 1
                break
            idx += 1
        spans.append((start, idx))
    return spans


def _inside_any_span(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(start <= pos < end for start, end in spans)


def _normalize_temporal_sql_filter(
    sql: str,
    columns: list[str] | tuple[str, ...] | None = None,
    dtypes: dict | None = None,
) -> str:
    text = str(sql or "").strip()
    if not text:
        return text
    all_columns = list(columns or [])
    lookup = _column_lookup(all_columns)
    dtype_lookup = {str(k).casefold(): v for k, v in (dtypes or {}).items()}
    literal_spans = _sql_literal_spans(text)
    replacements: list[tuple[int, int, str]] = []
    for match in _AI_SQL_COMPARE_RE.finditer(text):
        if _inside_any_span(match.start(), literal_spans):
            continue
        col = lookup.get(match.group("col").casefold(), match.group("col"))
        dtype_kind = _temporal_dtype_kind(dtype_lookup.get(col.casefold()))
        if not dtype_kind and not _looks_date_like_column(col):
            continue
        replacement = _temporal_runtime_compare(col, match.group("op"), match.group("rhs"), dtype_kind)
        if replacement:
            replacements.append((match.start(), match.end(), replacement))
    if not replacements:
        return text
    out = text
    for start, end, replacement in sorted(replacements, key=lambda item: item[0], reverse=True):
        out = out[:start] + replacement + out[end:]
    return out.strip()


_AI_SQL_TIME_CAST_COMPARE_RE = re.compile(
    r"(?P<lhs>TRY_CAST\(\s*[A-Za-z_][A-Za-z0-9_]*\s+AS\s+TIME\s*\))"
    r"\s*(?P<op>>=|<=|<>|!=|==|=|>|<)\s*"
    r"(?P<rhs>TIME\s+'(?:''|[^'])*'|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\")",
    re.I,
)
_AI_SQL_TIME_LITERAL_RE = re.compile(r"\bTIME\s+'(?P<value>(?:''|[^'])*)'", re.I)
_AI_SQL_TIME_CAST_SIMPLE_RE = re.compile(
    r"TRY_CAST\(\s*(?P<col>[A-Za-z_][A-Za-z0-9_]*)\s+AS\s+TIME\s*\)",
    re.I,
)


def _time_sql_from_cast_literal(raw: str) -> str | None:
    text = str(raw or "").strip()
    time_match = _AI_SQL_TIME_LITERAL_RE.fullmatch(text)
    if time_match:
        literal = "'" + time_match.group("value") + "'"
    else:
        literal = text
    parsed = _parse_temporal_literal(literal)
    if not parsed:
        return None
    literal_kind, literal_sql = parsed
    if literal_kind == "date":
        return None
    if literal_kind == "datetime":
        return literal_sql.split(" ", 1)[1] if " " in literal_sql else literal_sql[-8:]
    return literal_sql


def _normalize_time_cast_literals(sql: str) -> str:
    text = str(sql or "").strip()
    if not text or "TRY_CAST" not in text.upper() or "TIME" not in text.upper():
        return text

    def repl(match: re.Match) -> str:
        time_sql = _time_sql_from_cast_literal(match.group("rhs"))
        if not time_sql:
            return match.group(0)
        return f"{match.group('lhs')} {match.group('op')} TIME '{time_sql}'"

    return _AI_SQL_TIME_CAST_COMPARE_RE.sub(repl, text)


def _polars_time_cast_filter(sql: str) -> str:
    text = str(sql or "").strip()
    if not text or not _AI_SQL_TIME_CAST_SIMPLE_RE.search(text):
        return text

    def cast_repl(match: re.Match) -> str:
        col = match.group("col")
        return f"TRY_CAST(CONCAT('1970-01-01T', {col}) AS TIMESTAMP)"

    text = _AI_SQL_TIME_CAST_SIMPLE_RE.sub(cast_repl, text)

    def literal_repl(match: re.Match) -> str:
        return f"TIMESTAMP '1970-01-01T{match.group('value')}'"

    return _AI_SQL_TIME_LITERAL_RE.sub(literal_repl, text)


def _extract_llm_sql_text(raw_text: str, plan: dict) -> str:
    if isinstance(plan, dict) and plan:
        for key in ("sql", "filter", "where", "expression", "expr"):
            val = plan.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        return ""
    text = str(raw_text or "").strip()
    text = re.sub(r"^```(?:sql|json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    return text


def _validate_ai_sql_filter(raw_sql: str, columns: list[str]) -> tuple[str, list[str]]:
    warnings: list[str] = []
    sql = str(raw_sql or "").strip()
    sql = re.sub(r"^where\s+", "", sql, flags=re.I).strip()
    if not sql:
        raise ValueError("LLM did not return a SQL filter expression")
    sql = _normalize_where_expression(sql, columns)
    _validate_ai_sql_date_literals(sql, columns)
    # Validate the exact execution form too, while preserving the existing draft
    # contract. FileBrowser applies this normalized form at preview/query time.
    execution_sql = _normalize_common_view_sql_filter(sql, columns)
    duckdb_engine.normalize_filter_expr(_sql_filter_identifiers_to_duckdb(execution_sql))
    _lazy_filter_expr(sql, columns or ["value"])
    return sql, warnings


def _read_sql_token(sql: str, start: int) -> tuple[int, int, str] | None:
    text = str(sql or "")
    idx = start
    while idx < len(text) and text[idx].isspace():
        idx += 1
    if idx >= len(text):
        return None
    if text[idx] in {"'", '"'}:
        quote = text[idx]
        end = idx + 1
        value_chars: list[str] = []
        while end < len(text):
            ch = text[end]
            if ch == quote:
                if end + 1 < len(text) and text[end + 1] == quote:
                    value_chars.append(quote)
                    end += 2
                    continue
                return idx, end + 1, "".join(value_chars)
            value_chars.append(ch)
            end += 1
        return None
    match = re.match(r"[#A-Za-z0-9_.+-]+", text[idx:])
    if not match:
        return None
    return idx, idx + match.end(), match.group(0)


def _mask_sql_literals(sql: str) -> str:
    text = str(sql or "")
    out = list(text)
    idx = 0
    while idx < len(text):
        if text[idx] not in {"'", '"'}:
            idx += 1
            continue
        quote = text[idx]
        idx += 1
        while idx < len(text):
            out[idx] = " "
            if text[idx] == quote:
                if idx + 1 < len(text) and text[idx + 1] == quote:
                    out[idx + 1] = " "
                    idx += 2
                    continue
                idx += 1
                break
            idx += 1
    return "".join(out)


def _wafer_literal_number(raw: str) -> int | None:
    text = str(raw or "").strip().strip("'\"").upper()
    text = re.sub(r"^(?:#|WAFER|WF|W)\s*", "", text)
    if not re.fullmatch(r"\d+", text):
        return None
    value = int(text)
    return value if value >= 1 else None


def _split_sql_list_values(body: str) -> list[str]:
    values: list[str] = []
    idx = 0
    text = str(body or "")
    while idx < len(text):
        while idx < len(text) and text[idx] in {" ", "\t", "\n", "\r", ","}:
            idx += 1
        token = _read_sql_token(text, idx)
        if token is None:
            return []
        _start, end, value = token
        values.append(value)
        idx = end
        while idx < len(text) and text[idx].isspace():
            idx += 1
        if idx < len(text) and text[idx] == ",":
            idx += 1
            continue
        if idx < len(text):
            return []
    return values


def _overlaps(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(not (end <= old_start or start >= old_end) for old_start, old_end in spans)


def _normalize_wafer_sql_filter(sql: str, columns: list[str] | tuple[str, ...] | None) -> str:
    text = str(sql or "").strip()
    wafer_col = _wafer_column(list(columns or []))
    if not text or not wafer_col:
        return text
    col_pat = re.escape(wafer_col)
    cast_col = lambda col: f"CAST({col} AS BIGINT)"
    mask = _mask_sql_literals(text)
    replacements: list[tuple[int, int, str]] = []
    occupied: list[tuple[int, int]] = []

    for match in re.finditer(rf"(?<![A-Za-z0-9_])(?P<col>{col_pat})(?![A-Za-z0-9_])\s+(?P<neg>NOT\s+)?IN\s*\((?P<body>[^)]*)\)", mask, flags=re.I):
        span = match.span()
        if _overlaps(span, occupied):
            continue
        body_start, body_end = match.span("body")
        values = _split_sql_list_values(text[body_start:body_end])
        nums = [_wafer_literal_number(value) for value in values]
        if not nums or any(num is None for num in nums):
            continue
        op = "NOT IN" if match.group("neg") else "IN"
        replacement = f"{cast_col(match.group('col'))} {op} ({', '.join(str(num) for num in nums if num is not None)})"
        replacements.append((span[0], span[1], replacement))
        occupied.append(span)

    for match in re.finditer(rf"(?<![A-Za-z0-9_])(?P<col>{col_pat})(?![A-Za-z0-9_])\s+BETWEEN\s+", mask, flags=re.I):
        start = match.start()
        first = _read_sql_token(text, match.end())
        if not first:
            continue
        and_match = re.match(r"\s+AND\s+", mask[first[1]:], flags=re.I)
        if not and_match:
            continue
        second = _read_sql_token(text, first[1] + and_match.end())
        if not second:
            continue
        nums = [_wafer_literal_number(first[2]), _wafer_literal_number(second[2])]
        if any(num is None for num in nums):
            continue
        span = (start, second[1])
        if _overlaps(span, occupied):
            continue
        replacement = f"{cast_col(match.group('col'))} BETWEEN {nums[0]} AND {nums[1]}"
        replacements.append((span[0], span[1], replacement))
        occupied.append(span)

    for match in re.finditer(rf"(?<![A-Za-z0-9_])(?P<col>{col_pat})(?![A-Za-z0-9_])\s*(?P<op>>=|<=|<>|!=|==|=|>|<)\s*", mask, flags=re.I):
        token = _read_sql_token(text, match.end())
        if not token:
            continue
        num = _wafer_literal_number(token[2])
        if num is None:
            continue
        span = (match.start(), token[1])
        if _overlaps(span, occupied):
            continue
        replacement = f"{cast_col(match.group('col'))} {match.group('op')} {num}"
        replacements.append((span[0], span[1], replacement))
        occupied.append(span)

    if not replacements:
        return text
    out = text
    for start, end, replacement in sorted(replacements, key=lambda item: item[0], reverse=True):
        out = out[:start] + replacement + out[end:]
    return out.strip()


_AI_SQL_COLUMN_ALIASES = {
    "product": ("product", "제품"),
    "lot_id": ("lot_id", "lot id", "랏"),
    "root_lot_id": ("root_lot_id", "root lot id", "root lot", "root_lot", "루트 랏", "루트랏"),
    "wafer_id": ("wafer_id", "wafer id", "wafer", "wf", "웨이퍼"),
    "step_id": ("step_id", "step id", "step", "스텝", "공정"),
    "function_step": ("function_step", "function step", "func step"),
    "ppid": ("ppid",),
    "feature_name": ("feature_name", "feature name", "feature"),
    "knob_name": ("knob_name", "knob name"),
    "knob_value": ("knob_value", "knob value"),
    "category": ("category",),
    "item_id": ("item_id", "item id"),
    "item_desc": ("item_desc", "item desc", "description", "desc"),
    "subitem_id": ("subitem_id", "subitem id", "subitem"),
    "shot_x": ("shot_x", "shot x"),
    "shot_y": ("shot_y", "shot y"),
    "value": ("value", "값"),
    "rank": ("rank", "순위"),
    "lsl": ("lsl",),
    "usl": ("usl",),
    "tkout_time": ("tkout_time", "tkout time"),
    "update_time": ("update_time", "update time"),
    "measure_time": ("measure_time", "measure time", "측정 시간"),
}


def _sql_column_alias_pairs(columns: list[str] | tuple[str, ...] | None) -> list[tuple[str, str]]:
    lookup = _column_lookup(list(columns or []))
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for col in columns or []:
        canonical = lookup.get(str(col).casefold(), str(col))
        aliases = {str(col), str(col).replace("_", " "), canonical, canonical.replace("_", " ")}
        aliases.update(_AI_SQL_COLUMN_ALIASES.get(canonical.casefold(), ()))
        for alias in aliases:
            text = str(alias or "").strip()
            key = (text.casefold(), canonical)
            if (
                not text
                or key in seen
                or text.casefold() == canonical.casefold()
                and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text)
            ):
                continue
            seen.add(key)
            pairs.append((text, canonical))
    return sorted(pairs, key=lambda item: len(item[0]), reverse=True)


def _sql_alias_pattern(alias: str) -> str:
    text = str(alias or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_ ]+", text):
        body = r"\s+".join(re.escape(part) for part in text.split())
        return r"(?<![A-Za-z0-9_])" + body + r"(?![A-Za-z0-9_])"
    return re.escape(text)


def _canonicalize_sql_column_aliases(expr: str, columns: list[str] | tuple[str, ...] | None) -> str:
    text = str(expr or "")
    pairs = _sql_column_alias_pairs(columns)
    if not text or not pairs:
        return _canonicalize_sql_columns(text, list(columns or []))
    parts = re.split(r"('(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|`(?:``|[^`])+`)", text)
    for idx in range(0, len(parts), 2):
        segment = parts[idx]
        for alias, canonical in pairs:
            segment = re.sub(_sql_alias_pattern(alias), _quote_sql_filter_identifier(canonical), segment, flags=re.I)
        parts[idx] = segment
    return _canonicalize_sql_columns("".join(parts), list(columns or []))


def _should_quote_sql_rhs(raw: str, columns: list[str], lhs_col: str = "") -> bool:
    text = str(raw or "").strip()
    if not text or len(text) >= 2 and text[0] in {"'", '"'} and text[-1] == text[0]:
        return False
    lower = text.casefold()
    if lower in {"null", "true", "false"}:
        return False
    lookup = _column_lookup(columns)
    if lookup.get(lower):
        return False
    if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return False
    wafer_col = _wafer_column(columns)
    if wafer_col and lhs_col.casefold() == wafer_col.casefold() and _wafer_literal_number(text) is not None:
        return False
    return bool(re.fullmatch(r"[#A-Za-z0-9_.%+-]+", text))


def _quote_bare_sql_values(expr: str, columns: list[str]) -> str:
    text = str(expr or "")
    if not text or not columns:
        return text
    col_tokens: list[str] = []
    seen_tokens: set[str] = set()
    for col in sorted(columns, key=lambda item: len(str(item)), reverse=True):
        rendered = str(col)
        candidates = [rendered]
        quoted = _quote_sql_filter_identifier(rendered)
        if quoted != rendered:
            candidates.insert(0, quoted)
        for candidate in candidates:
            if candidate in seen_tokens:
                continue
            seen_tokens.add(candidate)
            col_tokens.append(re.escape(candidate))
    col_pat = "|".join(col_tokens)
    if not col_pat:
        return text
    parts = re.split(r"('(?:''|[^'])*'|\"(?:\"\"|[^\"])*\")", text)
    compare_re = re.compile(
        rf"(?<![A-Za-z0-9_])(?P<col>{col_pat})(?![A-Za-z0-9_])"
        rf"\s*(?P<op>NOT\s+LIKE|LIKE|ILIKE|>=|<=|<>|!=|==|=|>|<)\s*"
        rf"(?P<rhs>[#A-Za-z0-9_.%+-]+)",
        re.I,
    )
    in_re = re.compile(
        rf"(?<![A-Za-z0-9_])(?P<col>{col_pat})(?![A-Za-z0-9_])"
        rf"\s+(?P<neg>NOT\s+)?IN\s*\((?P<body>[^)]*)\)",
        re.I,
    )

    def quote_compare(match: re.Match) -> str:
        rhs = match.group("rhs")
        lhs_col = _unquote_sql_filter_identifier(match.group("col"))
        if not _should_quote_sql_rhs(rhs, columns, lhs_col):
            return match.group(0)
        return f"{match.group('col')} {match.group('op')} {_sql_literal_for_filter(rhs, columns)}"

    def quote_in(match: re.Match) -> str:
        body = match.group("body")
        values = _split_sql_list_values(body)
        if not values:
            return match.group(0)
        changed = False
        rendered: list[str] = []
        lhs_col = _unquote_sql_filter_identifier(match.group("col"))
        for value in values:
            if _should_quote_sql_rhs(value, columns, lhs_col):
                rendered.append(_sql_literal_for_filter(value, columns))
                changed = True
            else:
                rendered.append(str(value).strip())
        if not changed:
            return match.group(0)
        op = " NOT IN " if match.group("neg") else " IN "
        return f"{match.group('col')}{op}({', '.join(rendered)})"

    for idx in range(0, len(parts), 2):
        segment = in_re.sub(quote_in, parts[idx])
        parts[idx] = compare_re.sub(quote_compare, segment)
    return "".join(parts)


def _validate_no_sql_arithmetic(expr: str) -> None:
    masked = _strip_sql_literals(expr)
    if _AI_SQL_ARITHMETIC_RE.search(masked):
        raise ValueError("SQL arithmetic expressions are not supported.")


def _matching_paren_index(masked_sql: str, open_index: int) -> int:
    depth = 0
    for idx in range(open_index, len(masked_sql)):
        ch = masked_sql[idx]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return idx
    raise ValueError("CAST must be a complete CAST(column AS TYPE) expression.")


def _normalize_sql_casts(expr: str, columns: list[str]) -> str:
    text = str(expr or "")
    if not text or not _AI_SQL_CAST_CALL_RE.search(_mask_sql_literals(text)):
        return text
    lookup = _column_lookup(columns)
    masked = _mask_sql_literals(text)
    replacements: list[tuple[int, int, str]] = []
    occupied: list[tuple[int, int]] = []
    for match in _AI_SQL_CAST_CALL_RE.finditer(masked):
        start = match.start()
        open_index = match.end() - 1
        end = _matching_paren_index(masked, open_index)
        span = (start, end + 1)
        if _overlaps(span, occupied):
            continue
        body = text[open_index + 1:end].strip()
        body_match = _AI_SQL_CAST_BODY_RE.match(body)
        if not body_match:
            raise ValueError("CAST must target one column: CAST(column AS TYPE).")
        raw_col = _unquote_sql_filter_identifier(body_match.group("col"))
        column = lookup.get(raw_col.casefold()) if lookup else raw_col
        if columns and not column:
            raise ValueError(f"SQL referenced unknown column(s): {raw_col}")
        cast_type = _AI_SQL_CAST_TYPES.get(body_match.group("type").upper())
        if not cast_type:
            raise ValueError(f"Unsupported CAST type: {body_match.group('type')}")
        replacements.append((span[0], span[1], f"TRY_CAST({_quote_sql_filter_identifier(column)} AS {cast_type})"))
        occupied.append(span)
    out = text
    for start, end, replacement in sorted(replacements, key=lambda item: item[0], reverse=True):
        out = out[:start] + replacement + out[end:]
    return out


def _normalize_where_expression(sql: str, columns: list[str] | tuple[str, ...] | None = None) -> str:
    text = str(sql or "").strip()
    if not text:
        return ""
    text = re.sub(r"^where\s+", "", text, flags=re.I).strip()
    if _AI_SQL_FORBIDDEN_RE.search(_strip_sql_literals(text)):
        raise ValueError("SQL must be a single read-only WHERE expression.")
    all_columns = list(columns or [])
    text = _canonicalize_sql_column_aliases(text, all_columns)
    text = _quote_bare_sql_values(text, all_columns)
    _validate_no_sql_arithmetic(text)
    text = _normalize_sql_casts(text, all_columns)
    quoted_missing = _sql_unknown_quoted_columns(text, all_columns)
    if quoted_missing:
        raise ValueError("SQL referenced unknown column(s): " + ", ".join(quoted_missing[:8]))
    missing = _sql_missing_columns(text, all_columns)
    if missing:
        raise ValueError("SQL referenced unknown column(s): " + ", ".join(missing[:8]))
    return text.strip()


def _normalize_common_view_sql_filter(
    sql: str,
    columns: list[str] | tuple[str, ...] | None = None,
    dtypes: dict | None = None,
) -> str:
    text = _normalize_where_expression(sql, columns)
    text = _normalize_wafer_sql_filter(text, columns)
    text = _normalize_temporal_sql_filter(text, columns, dtypes)
    return _normalize_time_cast_literals(text)


def _normalize_view_sql_filter(
    sql: str,
    columns: list[str] | tuple[str, ...] | None = None,
    dtypes: dict | None = None,
) -> str:
    return _sql_filter_identifiers_to_duckdb(_normalize_common_view_sql_filter(sql, columns, dtypes))


def _normalize_polars_view_sql_filter(
    sql: str,
    columns: list[str] | tuple[str, ...] | None = None,
    dtypes: dict | None = None,
) -> str:
    return _polars_time_cast_filter(_normalize_common_view_sql_filter(sql, columns, dtypes))


_AI_SQL_ORDER_BY_SPLIT_RE = re.compile(r"\bORDER\s+BY\b", re.IGNORECASE)
_AI_SQL_DISPLAY_IDENTIFIER_RE = r"(?:`(?:``|[^`])+`|\"(?:\"\"|[^\"])+\"|[A-Za-z_][A-Za-z0-9_]*)"
_AI_SQL_ORDER_BY_RE = re.compile(
    rf"^\s*(?P<col>{_AI_SQL_DISPLAY_IDENTIFIER_RE})"
    r"(?:\s+(?P<direction>ASC|DESC))?"
    r"(?:\s+NULLS\s+(?P<nulls>FIRST|LAST))?\s*$",
    re.IGNORECASE,
)


def _split_ai_sql_order_by(sql: str) -> tuple[str, str]:
    text = str(sql or "").strip()
    if not text:
        return "", ""
    masked = _mask_sql_literals(text)
    matches = list(_AI_SQL_ORDER_BY_SPLIT_RE.finditer(masked))
    if not matches:
        return text, ""
    if len(matches) > 1:
        raise ValueError("SQL must contain a single ORDER BY clause")
    match = matches[0]
    return text[:match.start()].strip(), text[match.end():].strip()


def _parse_ai_sql_order_by(order_sql: str, columns: list[str] | tuple[str, ...] | None = None) -> dict:
    text = str(order_sql or "").strip()
    if not text:
        return {}
    if re.search(r";|--|/\*|\*/", text):
        raise ValueError("ORDER BY must be a single read-only sort clause")
    if re.search(r"\b(SELECT|FROM|WHERE|JOIN|GROUP\s+BY|HAVING|LIMIT|OFFSET|WITH)\b", text, flags=re.I):
        raise ValueError("ORDER BY must contain only one column, direction, and optional NULLS order")
    match = _AI_SQL_ORDER_BY_RE.match(text)
    if not match:
        raise ValueError("ORDER BY must use: column [ASC|DESC] [NULLS FIRST|LAST]")
    column = _unquote_ai_sql_display_identifier(match.group("col"))
    lookup = _column_lookup(list(columns or []))
    hit = lookup.get(column.casefold()) if lookup else column
    if columns and not hit:
        raise ValueError(f"ORDER BY referenced unknown column: {column}")
    return {
        "column": hit,
        "direction": (match.group("direction") or "asc").casefold(),
        "nulls": (match.group("nulls") or "last").casefold(),
    }


def _quote_ai_sql_display_identifier(name: str) -> str:
    text = str(name or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text):
        return text
    return "`" + text.replace("`", "``") + "`"


def _unquote_ai_sql_display_identifier(raw: str) -> str:
    text = str(raw or "").strip()
    if len(text) >= 2 and text[0] == "`" and text[-1] == "`":
        return text[1:-1].replace("``", "`")
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return text[1:-1].replace('""', '"')
    return text


def _split_ai_sql_identifier_list(raw_cols: str) -> list[str] | None:
    text = str(raw_cols or "")
    parts: list[str] = []
    buf: list[str] = []
    quote = ""
    idx = 0
    while idx < len(text):
        ch = text[idx]
        if quote:
            buf.append(ch)
            if ch == quote:
                if idx + 1 < len(text) and text[idx + 1] == quote:
                    buf.append(text[idx + 1])
                    idx += 2
                    continue
                quote = ""
            idx += 1
            continue
        if ch in {"`", '"'}:
            quote = ch
            buf.append(ch)
        elif ch == ",":
            part = "".join(buf).strip()
            if part:
                parts.append(part)
            buf = []
        else:
            buf.append(ch)
        idx += 1
    if quote:
        return None
    part = "".join(buf).strip()
    if part:
        parts.append(part)
    return parts


def _split_ai_sql_select_body(text: str) -> tuple[str, str] | None:
    match = re.match(r"^\s*SELECT\b", str(text or ""), flags=re.I)
    if not match:
        return None
    body = str(text or "")[match.end():].strip()
    quote = ""
    idx = 0
    while idx < len(body):
        ch = body[idx]
        if quote:
            if ch == quote:
                if idx + 1 < len(body) and body[idx + 1] == quote:
                    idx += 2
                    continue
                quote = ""
            idx += 1
            continue
        if ch in {"`", '"', "'"}:
            quote = ch
            idx += 1
            continue
        where_match = re.match(r"\bWHERE\b", body[idx:], flags=re.I)
        if where_match:
            return body[:idx].strip(), body[idx + where_match.end():].strip()
        idx += 1
    return body, ""


def _resolve_ai_sql_display_column(raw: str, columns: list[str] | tuple[str, ...] | None) -> str:
    token = _unquote_ai_sql_display_identifier(raw)
    if not token:
        return ""
    all_columns = list(columns or [])
    lookup = _column_lookup(all_columns)
    if lookup:
        return lookup.get(token.casefold(), "")
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token) or token != str(raw or "").strip():
        return token
    return ""


def _parse_ai_sql_select_prefix(sql: str, columns: list[str] | tuple[str, ...] | None = None) -> tuple[str, list[str]]:
    """Detect and strip a SELECT prefix from a Flow AI SQL string.

    Accepts forms like:
      - "SELECT a, b WHERE x = 1"     -> ("x = 1", ["a", "b"])
      - "SELECT a, b"                  -> ("",      ["a", "b"])
      - "SELECT * WHERE x = 1"         -> ("x = 1", [])
      - "x = 1"                        -> ("x = 1", [])

    Returns the original (sql, []) when no SELECT prefix is present, when the
    projection contains complex expressions (functions, *, aliases), or when a
    referenced column is unknown.
    """
    text = str(sql or "").strip()
    if not text:
        return "", []
    if not re.match(r"^\s*SELECT\b", text, flags=re.I):
        return text, []
    if re.search(r"\bFROM\b", _mask_sql_literals(text), flags=re.I):
        return text, []
    select_body = _split_ai_sql_select_body(text)
    if not select_body:
        return text, []
    raw_cols, rest = select_body
    if not raw_cols or raw_cols == "*":
        return rest, []
    out_cols: list[str] = []
    parts = _split_ai_sql_identifier_list(raw_cols)
    if parts is None:
        return text, []
    for part in parts:
        token = _resolve_ai_sql_display_column(part, columns)
        if not token:
            return text, []
        if token not in out_cols:
            out_cols.append(token)
    return rest, out_cols


def _parse_ai_sql_display_sql(sql: str, columns: list[str] | tuple[str, ...] | None = None) -> tuple[str, list[str], dict]:
    """Split Flow's display SQL into WHERE SQL, selected columns, and ORDER BY."""
    text = str(sql or "").strip()
    if not text:
        return "", [], {}
    body, order_sql = _split_ai_sql_order_by(text)
    sort_spec = _parse_ai_sql_order_by(order_sql, columns) if order_sql else {}
    where_sql, selected = _parse_ai_sql_select_prefix(body, columns)
    return where_sql, selected, sort_spec


def _merge_display_sql_into_args(
    sql: str,
    select_cols: str,
    sort_spec: dict | None,
    columns: list[str] | tuple[str, ...] | None,
) -> tuple[str, str, dict]:
    where_sql, parsed_cols, parsed_sort = _parse_ai_sql_display_sql(sql, columns)
    existing = [token.strip() for token in (select_cols or "").split(",") if token.strip()]
    merged: list[str] = []
    for col in parsed_cols:
        if col not in merged:
            merged.append(col)
    for col in existing:
        if col not in merged:
            merged.append(col)
    return where_sql, ",".join(merged), (parsed_sort or sort_spec or {})


def _merge_select_prefix_into_args(sql: str, select_cols: str, columns: list[str] | tuple[str, ...] | None) -> tuple[str, str]:
    """Extract a `SELECT cols WHERE rest` prefix from sql and fold cols into select_cols.

    Idempotent: if sql has no SELECT prefix, both inputs are returned unchanged.
    SELECT-derived columns are placed first (user-stated projection wins); any
    existing select_cols entries are appended de-duplicated. This lets callers
    pass either form transparently.
    """
    parsed_sql, parsed_cols, _parsed_sort = _merge_display_sql_into_args(sql, select_cols, {}, columns)
    if not parsed_cols and parsed_sql == str(sql or "").strip():
        return sql, select_cols
    return parsed_sql, parsed_cols


def _build_ai_sql_display_sql(
    selected_columns: list[str] | tuple[str, ...] | None,
    where_sql: str,
    sort_spec: dict | None = None,
) -> str:
    selected = [str(c or "").strip() for c in (selected_columns or []) if str(c or "").strip()]
    where = str(where_sql or "").strip()
    sort = _normalize_ai_sql_sort(sort_spec or {}, selected + ([str((sort_spec or {}).get("column") or "")] if sort_spec else []), [], "sort") if sort_spec else {}
    rendered_selected = [_quote_ai_sql_display_identifier(c) for c in selected]
    if selected and where:
        base = f"SELECT {', '.join(rendered_selected)} WHERE {where}"
    elif selected:
        base = f"SELECT {', '.join(rendered_selected)}"
    else:
        base = where
    if not sort:
        return base
    order = f"ORDER BY {_quote_ai_sql_display_identifier(sort['column'])} {str(sort.get('direction') or 'asc').upper()}"
    if str(sort.get("nulls") or "last").casefold() == "first":
        order += " NULLS FIRST"
    return f"{base} {order}".strip()

_AI_SQL_AGG_FUNCTION_ALIASES = {
    "avg": ("avg", "average", "mean", "평균"),
    "sum": ("sum", "total", "합계", "합"),
    "min": ("min", "minimum", "최소", "최솟값"),
    "max": ("max", "maximum", "최대", "최댓값"),
    "median": ("median", "med", "중앙값", "중위값"),
    "count": ("count", "cnt", "개수", "건수", "카운트"),
}


def _all_ai_sql_alias_tokens() -> set[str]:
    out: set[str] = set()
    for aliases in _AI_SQL_COLUMN_ALIASES.values():
        for alias in aliases:
            text = str(alias).casefold()
            out.add(text)
            out.update(part for part in re.split(r"[^a-z0-9_]+", text) if part)
    return out


def _ai_sql_column_term(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _resolve_ai_sql_prompt_columns(prompt: str, columns: list[str]) -> tuple[list[str], list[str]]:
    if not columns:
        return [], []
    lookup = _column_lookup(columns)
    alias_lookup: dict[str, str] = {}
    for col in columns:
        canonical = lookup.get(str(col).casefold(), str(col))
        aliases = {str(col), str(col).replace("_", " ")}
        aliases.update(_AI_SQL_COLUMN_ALIASES.get(str(col).casefold(), ()))
        for alias in aliases:
            alias_text = str(alias or "").casefold()
            if not alias_text:
                continue
            alias_lookup[alias_text] = canonical
            alias_norm = _ai_sql_column_term(alias_text)
            if alias_norm:
                alias_lookup[alias_norm] = canonical
            for part in re.split(r"[^a-z0-9_]+", alias_text):
                if len(part) >= 3:
                    alias_lookup.setdefault(part, canonical)
    col_norms = [(_ai_sql_column_term(col), lookup.get(str(col).casefold(), str(col))) for col in columns]
    resolved: list[str] = []
    unknown: list[str] = []
    for token in _prompt_identifier_tokens(prompt):
        key = token.casefold()
        if key in _AI_SQL_IGNORE_TOKENS:
            continue
        norm = _ai_sql_column_term(token)
        hit = lookup.get(key) or alias_lookup.get(key) or alias_lookup.get(norm)
        if not hit and len(norm) >= 3:
            matches = [col for col_norm, col in col_norms if col_norm and (norm in col_norm or col_norm in norm)]
            unique = []
            for col in matches:
                if col not in unique:
                    unique.append(col)
            if len(unique) == 1:
                hit = unique[0]
        if hit:
            if hit not in resolved:
                resolved.append(hit)
            continue
        if "_" in token and token not in unknown:
            unknown.append(token)
    return resolved, unknown


def _alias_span(prompt: str, alias: str) -> tuple[int, int] | None:
    alias = str(alias or "")
    if not alias:
        return None
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_ ]*", alias):
        pattern = r"(?<![A-Za-z0-9_])" + re.escape(alias) + r"(?![A-Za-z0-9_])"
        match = re.search(pattern, prompt, flags=re.I)
        return match.span() if match else None
    idx = prompt.casefold().find(alias.casefold())
    return (idx, idx + len(alias)) if idx >= 0 else None


def _sql_literal_for_filter(value: str, columns: list[str]) -> str:
    text = str(value or "").strip().strip("'\"")
    if not text:
        return "''"
    if text.casefold() in {c.casefold() for c in columns}:
        return _column_lookup(columns).get(text.casefold(), text)
    if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return text
    return "'" + text.replace("'", "''") + "'"


def _ai_sql_time_from_suffix(text: str) -> tuple[int, int, int] | None:
    suffix = str(text or "")[:64]
    cut = re.search(r"(?:이후|이전|부터|까지|만|행|필터|그리고|또|,|;|\n)", suffix, flags=re.I)
    if cut:
        suffix = suffix[:cut.start()]
    meridiem_match = re.search(r"\b(AM|PM|A\.M\.|P\.M\.)\b|오전|오후", suffix, flags=re.I)
    meridiem = meridiem_match.group(0).casefold().replace(".", "") if meridiem_match else ""
    match = re.search(r"(?<!\d)(\d{1,2})\s*:\s*(\d{1,2})(?:\s*:\s*(\d{1,2}))?(?!\d)", suffix)
    if not match:
        match = re.search(r"(?<!\d)(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분?)?(?:\s*(\d{1,2})\s*초)?", suffix)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    second = int(match.group(3) or 0)
    if meridiem in {"pm", "오후"} and hour < 12:
        hour += 12
    if meridiem in {"am", "오전"} and hour == 12:
        hour = 0
    if hour > 23 or minute > 59 or second > 59:
        return None
    return hour, minute, second


def _ai_sql_datetime_value(year: int, month: int = 1, day: int = 1,
                           time_value: tuple[int, int, int] | None = None) -> str | None:
    try:
        if time_value:
            hour, minute, second = time_value
            return datetime.datetime(year, month, day, hour, minute, second).isoformat(timespec="seconds")
        return datetime.date(year, month, day).isoformat()
    except ValueError:
        return None


def _extract_ai_sql_datetime_values(text: str) -> list[str]:
    src = str(text or "")
    candidates: list[tuple[int, int, int, str]] = []

    def add(start: int, end: int, precision: int, value: str | None) -> None:
        if value:
            candidates.append((start, end, precision, value))

    for match in re.finditer(r"(?<!\d)((?:19|20|21)\d{2})\s*년\s*(?:(\d{1,2})\s*월\s*)?(?:(\d{1,2})\s*일)?", src):
        year = int(match.group(1))
        month = int(match.group(2) or 1)
        day = int(match.group(3) or 1)
        time_value = _ai_sql_time_from_suffix(src[match.end():]) if match.group(3) else None
        precision = 4 if time_value else (3 if match.group(3) else (2 if match.group(2) else 1))
        add(match.start(), match.end(), precision, _ai_sql_datetime_value(year, month, day, time_value))

    for match in re.finditer(r"(?<!\d)((?:19|20|21)\d{2})[-/.](\d{1,2})(?:[-/.](\d{1,2}))?(?!\d)", src):
        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3) or 1)
        time_value = _ai_sql_time_from_suffix(src[match.end():]) if match.group(3) else None
        precision = 4 if time_value else (3 if match.group(3) else 2)
        add(match.start(), match.end(), precision, _ai_sql_datetime_value(year, month, day, time_value))

    for match in re.finditer(r"(?<!\d)((?:19|20|21)\d{2})(\d{2})(\d{2})(?!\d)", src):
        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3))
        time_value = _ai_sql_time_from_suffix(src[match.end():])
        precision = 4 if time_value else 3
        add(match.start(), match.end(), precision, _ai_sql_datetime_value(year, month, day, time_value))

    for match in re.finditer(r"(?<![A-Za-z0-9_])((?:19|20|21)\d{2})(?:\s*년)?(?![A-Za-z0-9_])", src):
        year = int(match.group(1))
        add(match.start(), match.end(), 1, _ai_sql_datetime_value(year))

    selected: list[tuple[int, int, str]] = []
    for start, end, _precision, value in sorted(candidates, key=lambda item: (-item[2], item[0], item[1])):
        if any(not (end <= old_start or start >= old_end) for old_start, old_end, _old_value in selected):
            continue
        selected.append((start, end, value))
    out: list[str] = []
    for _start, _end, value in sorted(selected, key=lambda item: item[0]):
        if value not in out:
            out.append(value)
    return out


def _fallback_values(text: str, columns: list[str]) -> list[str]:
    blocked = {c.casefold() for c in columns}
    blocked.update(_AI_SQL_IGNORE_TOKENS)
    blocked.update(_all_ai_sql_alias_tokens())
    blocked.update({"and", "or", "null", "not", "like", "true", "false"})
    values: list[str] = []
    for raw in re.findall(r"'([^']+)'|\"([^\"]+)\"|(\d{4}-\d{2}-\d{2})|([A-Za-z][A-Za-z0-9_.-]*|-?\d+(?:\.\d+)?)", text):
        val = next((item for item in raw if item), "")
        if not val:
            continue
        if val.casefold() in blocked:
            continue
        if val not in values:
            values.append(val)
    return values[:4]


def _fallback_column_hits(prompt: str, columns: list[str]) -> list[str]:
    lookup = _column_lookup(columns)
    hits: list[str] = []
    for col in columns:
        canonical = lookup.get(str(col).casefold(), str(col))
        aliases = _AI_SQL_COLUMN_ALIASES.get(str(col).casefold(), (str(col),))
        matched = False
        for alias in aliases:
            span = _alias_span(prompt, alias)
            if span is None:
                continue
            if str(col).casefold() == "lot_id":
                prefix = prompt[max(0, span[0] - 8):span[0]].casefold().strip()
                if prefix.endswith("root") or prefix.endswith("루트"):
                    continue
            matched = True
            break
        if matched:
            if canonical not in hits:
                hits.append(canonical)
    return hits


def _prompt_hash_wafer_numbers(prompt: str) -> list[int]:
    numbers: list[int] = []
    for match in re.finditer(r"(?<![A-Za-z0-9_])#\s*(\d{1,2})(?!\d)", str(prompt or "")):
        value = _wafer_literal_number(match.group(1))
        if value is not None and value not in numbers:
            numbers.append(value)
    return numbers


def _hash_wafer_clause(prompt: str, columns: list[str]) -> str:
    wafer_col = _wafer_column(columns)
    numbers = _prompt_hash_wafer_numbers(prompt)
    if not wafer_col or not numbers:
        return ""
    if len(numbers) == 1:
        return f"{wafer_col} = {numbers[0]}"
    return f"{wafer_col} IN ({', '.join(str(n) for n in numbers[:25])})"


def _hash_wafer_misread_suffix_re(numbers: list[int]) -> re.Pattern[str] | None:
    if not numbers:
        return None
    alts = "|".join(rf"[A-Za-z]?\.{n}%?$" for n in numbers[:10])
    return re.compile("(?:" + alts + ")")


def _llm_misread_hash_wafer(prompt: str, sql: str, columns: list[str]) -> bool:
    numbers = _prompt_hash_wafer_numbers(prompt)
    wafer_col = _wafer_column(columns)
    if not numbers or not wafer_col:
        return False
    mask = _mask_sql_literals(sql)
    if not re.search(r"(?<![A-Za-z0-9_])" + re.escape(wafer_col) + r"(?![A-Za-z0-9_])", mask, flags=re.I):
        return True
    suffix_re = _hash_wafer_misread_suffix_re(numbers)
    if suffix_re:
        for literal_match in re.finditer(r"'([^']+)'", sql):
            if suffix_re.search(literal_match.group(1)):
                return True
    return False


def _fallback_lot_clause(prompt: str, columns: list[str]) -> str:
    lookup = _column_lookup(columns)
    root_col = lookup.get("root_lot_id")
    lot_col = lookup.get("lot_id")
    if not root_col and not lot_col:
        return ""
    for token in re.findall(r"(?<![A-Za-z0-9_])([A-Za-z]?\d{3,}[A-Za-z0-9_.-]*)(?![A-Za-z0-9_])", str(prompt or "")):
        value = _cache_safe_text(token, 80)
        if not value:
            continue
        if re.fullmatch(r"(?:19|20|21)\d{2}", value):
            continue
        if root_col and "." not in value:
            return f"{root_col} = {_sql_literal_for_filter(value, columns)}"
        if lot_col:
            safe = value.replace("'", "''")
            op_value = f"{safe}%" if "." not in value else safe
            return f"{lot_col} LIKE '{op_value}'" if "." not in value else f"{lot_col} = '{op_value}'"
    return ""


_AI_SQL_STEP_MAPPING_FILENAMES = (
    "Vehicle_matching.csv",
    "vehicle_matching.csv",
    "step_matching.csv",
    "matching_step.csv",
    "step_function.csv",
)
_AI_SQL_STEP_FUNCTION_COLUMNS = (
    "function_step",
    "func_step",
    "func step",
    "canonical_step",
    "step_function",
    "step_desc",
    "step description",
    "step_description",
)
_AI_SQL_GENERIC_STEP_TERMS = {
    "sort", "order", "filter", "select", "value", "avg", "sum", "count",
}


def _ai_sql_step_mapping_filenames() -> tuple[str, ...]:
    try:
        from core import lot_progress_cache as _lot_progress_cache
        names = tuple(str(v) for v in getattr(_lot_progress_cache, "STEP_MAPPING_FILENAMES", ()) if str(v or "").strip())
        return names or _AI_SQL_STEP_MAPPING_FILENAMES
    except Exception:
        return _AI_SQL_STEP_MAPPING_FILENAMES


def _ai_sql_step_function_columns() -> tuple[str, ...]:
    try:
        from core import lot_progress_cache as _lot_progress_cache
        names = tuple(str(v) for v in getattr(_lot_progress_cache, "FUNCTION_STEP_SOURCE_COLUMNS", ()) if str(v or "").strip())
        return names or _AI_SQL_STEP_FUNCTION_COLUMNS
    except Exception:
        return _AI_SQL_STEP_FUNCTION_COLUMNS


def _ai_sql_path_key(path: Path) -> str:
    try:
        return str(Path(path).resolve()).casefold()
    except Exception:
        return str(path).casefold()


def _ai_sql_step_mapping_paths() -> list[Path]:
    roots: list[Path] = []
    for raw in (PATHS.db_root, PATHS.base_root, PATHS.data_root / "Fab"):
        try:
            root = Path(raw)
        except Exception:
            continue
        if not any(_ai_sql_path_key(root) == _ai_sql_path_key(existing) for existing in roots):
            roots.append(root)
    out: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        for name in _ai_sql_step_mapping_filenames():
            path = root / name
            key = _ai_sql_path_key(path)
            if key not in seen:
                seen.add(key)
                out.append(path)
    return out


def _ai_sql_csv_row_ci(row: dict, *names: str):
    lookup = {str(k or "").strip().casefold(): v for k, v in (row or {}).items()}
    for name in names:
        key = str(name or "").strip().casefold()
        if key in lookup and _cache_safe_text(lookup.get(key), 240):
            return lookup.get(key)
    return ""


def _ai_sql_product_key(value) -> str:
    return _cache_safe_text(value, 120).casefold()


def _ai_sql_step_term(value) -> str:
    return re.sub(r"[^a-z0-9]+", "", _cache_safe_text(value, 240).casefold())


def _ai_sql_step_mapping_rows() -> list[dict]:
    rows: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    for path in _ai_sql_step_mapping_paths():
        if not path.is_file():
            continue
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    product = _cache_safe_text(_ai_sql_csv_row_ci(row, "product", "process_id", "prod"), 120)
                    step_id = _cache_safe_text(_ai_sql_csv_row_ci(row, "step_id", "raw_step_id", "step"), 160)
                    function_step = _cache_safe_text(_ai_sql_csv_row_ci(row, *_ai_sql_step_function_columns()), 160)
                    if not step_id or not function_step:
                        continue
                    key = (product.casefold(), step_id.casefold(), function_step.casefold(), _ai_sql_path_key(path))
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append({
                        "product": product,
                        "step_id": step_id,
                        "function_step": function_step,
                        "source": path.name,
                    })
        except Exception as exc:
            logger.warning("AI SQL step matching load failed: %s (%s)", path, exc)
    return rows


def _ai_sql_prompt_products(prompt: str, rows: list[dict], product: str = "") -> set[str]:
    products = {_ai_sql_product_key(product)} if _ai_sql_product_key(product) else set()
    prompt_norm = _ai_sql_step_term(prompt)
    for row in rows:
        prod = _cache_safe_text(row.get("product"), 120)
        prod_key = _ai_sql_product_key(prod)
        if prod_key and _ai_sql_step_term(prod) in prompt_norm:
            products.add(prod_key)
    return products


def _ai_sql_function_step_matches_prompt(prompt: str, function_step: str) -> bool:
    value_norm = _ai_sql_step_term(function_step)
    if len(value_norm) < 3:
        return False
    prompt_norm = _ai_sql_step_term(prompt)
    if value_norm not in prompt_norm:
        return False
    if value_norm in _AI_SQL_GENERIC_STEP_TERMS:
        return bool(re.search(r"\bstep\b|function[_\s-]*step|스텝|공정|단계", str(prompt or ""), flags=re.I))
    return True


def _ai_sql_step_mapping_matches(prompt: str, columns: list[str], product: str = "", limit: int = 50) -> list[dict]:
    lookup = _column_lookup(columns)
    if not lookup.get("step_id") and not lookup.get("function_step"):
        return []
    rows = _ai_sql_step_mapping_rows()
    if not rows:
        return []
    products = _ai_sql_prompt_products(prompt, rows, product)
    matches: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        prod_key = _ai_sql_product_key(row.get("product"))
        if products and prod_key and prod_key not in products:
            continue
        if not _ai_sql_function_step_matches_prompt(prompt, str(row.get("function_step") or "")):
            continue
        key = (
            _ai_sql_product_key(row.get("product")),
            _cache_safe_text(row.get("step_id"), 160).casefold(),
            _cache_safe_text(row.get("function_step"), 160).casefold(),
        )
        if key in seen:
            continue
        seen.add(key)
        matches.append({
            "product": _cache_safe_text(row.get("product"), 120),
            "step_id": _cache_safe_text(row.get("step_id"), 160),
            "function_step": _cache_safe_text(row.get("function_step"), 160),
            "source": _cache_safe_text(row.get("source"), 120),
        })
        if len(matches) >= limit:
            break
    return matches


def _ai_sql_values_clause(column: str, values: list[str], columns: list[str]) -> str:
    clean: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _cache_safe_text(value, 160)
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            clean.append(text)
    if not column or not clean:
        return ""
    if len(clean) == 1:
        return f"{column} = {_sql_literal_for_filter(clean[0], columns)}"
    return f"{column} IN ({', '.join(_sql_literal_for_filter(value, columns) for value in clean[:50])})"


def _ai_sql_step_mapping_context(prompt: str, columns: list[str], product: str = "") -> dict:
    lookup = _column_lookup(columns)
    step_col = lookup.get("step_id")
    function_col = lookup.get("function_step")
    matches = _ai_sql_step_mapping_matches(prompt, columns, product)
    if not matches:
        return {"used": False, "matches": [], "target_sql": "", "target_column": ""}
    target_col = step_col or function_col or ""
    values = [m.get("step_id") for m in matches] if step_col else [m.get("function_step") for m in matches]
    target_sql = _ai_sql_values_clause(target_col, [str(v or "") for v in values], columns)
    source_files: list[str] = []
    for match in matches:
        source = _cache_safe_text(match.get("source"), 120)
        if source and source not in source_files:
            source_files.append(source)
    return {
        "used": bool(target_sql),
        "target_column": target_col,
        "target_sql": target_sql,
        "matches": matches,
        "source_files": source_files,
        "step_ids": [m.get("step_id") for m in matches if m.get("step_id")],
        "function_steps": [m.get("function_step") for m in matches if m.get("function_step")],
    }


def _ai_sql_step_mapping_clause(prompt: str, columns: list[str], product: str = "", context: dict | None = None) -> str:
    ctx = context if isinstance(context, dict) else _ai_sql_step_mapping_context(prompt, columns, product)
    return _cache_safe_text(ctx.get("target_sql"), 1000) if ctx.get("used") else ""


def _public_ai_sql_step_mapping_context(context: dict | None) -> dict:
    if not isinstance(context, dict) or not context.get("used"):
        return {"used": False, "matches": []}
    return {
        "used": True,
        "target_column": context.get("target_column") or "",
        "target_sql": context.get("target_sql") or "",
        "matches": list(context.get("matches") or [])[:20],
        "source_files": list(context.get("source_files") or [])[:10],
    }


def _ai_sql_filter_has_step_mapping(sql: str, context: dict) -> bool:
    if not isinstance(context, dict) or not context.get("used"):
        return True
    target_col = _cache_safe_text(context.get("target_column"), 120)
    if not target_col:
        return True
    mask = _mask_sql_literals(sql)
    if not re.search(r"(?<![A-Za-z0-9_])" + re.escape(target_col) + r"(?![A-Za-z0-9_])", mask, flags=re.I):
        return False
    haystack = str(sql or "").casefold()
    values = context.get("step_ids") if target_col.casefold() == "step_id" else context.get("function_steps")
    values = values or []
    return any(_cache_safe_text(value, 160).casefold() in haystack for value in values)


def _merge_ai_sql_step_mapping_filter(raw_sql: str, context: dict, warnings: list[str]) -> str:
    clause = _cache_safe_text((context or {}).get("target_sql"), 1000)
    sql = str(raw_sql or "").strip()
    if not sql or not clause:
        return sql
    if _ai_sql_filter_has_step_mapping(sql, context):
        return sql
    mask = _mask_sql_literals(sql)
    target_col = _cache_safe_text((context or {}).get("target_column"), 120)
    blocking_cols = [target_col, "step_id", "function_step"]
    if any(col and re.search(r"(?<![A-Za-z0-9_])" + re.escape(col) + r"(?![A-Za-z0-9_])", mask, flags=re.I)
           for col in blocking_cols):
        raise ValueError("AI SQL step mapping did not resolve function_step to the mapped step_id.")
    _draft_warning(warnings, "step matching file used to add mapped step_id filter")
    return f"{sql} AND {clause}"


def _fallback_window(prompt: str, column: str) -> str:
    aliases = _AI_SQL_COLUMN_ALIASES.get(str(column).casefold(), (str(column),))
    spans = [span for alias in aliases for span in [_alias_span(prompt, alias)] if span is not None]
    if not spans:
        return prompt
    candidates: list[tuple[int, int, str]] = []
    for start, end in spans:
        tail = prompt[end:end + 120]
        cut = re.search(r"(?:이고|이면서|그리고|또|보고|보여|표시|조회|,|;|\n|\bAND\b|\bOR\b)", tail, flags=re.I)
        if cut:
            tail = tail[:cut.start()]
        prefix = prompt[max(0, start - 80):start] if _looks_date_like_column(column) else ""
        window = prefix + prompt[start:end] + tail
        score = 1 if _fallback_values(window, [column]) or (_looks_date_like_column(column) and _extract_ai_sql_datetime_values(window)) else 0
        candidates.append((-score, start, window))
    return min(candidates, key=lambda item: (item[0], item[1]))[2]


def _ai_sql_recent_days_clause(prompt: str, columns: list[str], now: datetime.datetime | None = None) -> str:
    """Build a deterministic rolling-time predicate from Korean/English prompts."""
    text = str(prompt or "")
    match = re.search(
        r"(?:\ucd5c\uadfc\s*|(?:last|past|recent)\s*)(\d{1,4})\s*(?:\uc77c|days?)\s*(?:\uc774\ub0b4|\ub3d9\uc548)?",
        text,
        flags=re.I,
    )
    if not match:
        return ""
    days = max(1, min(3650, int(match.group(1))))
    date_columns = [col for col in columns if _looks_date_like_column(col)]
    if not date_columns:
        return ""
    exact = _exact_column_hits(text, date_columns)
    preferred_names = ("tkout_time", "update_time", "updated_at", "measure_time", "created_at", "timestamp")
    lookup = _column_lookup(date_columns)
    column = exact[0] if exact else next((lookup[name] for name in preferred_names if name in lookup), date_columns[0])
    cutoff = (now or datetime.datetime.now()).replace(microsecond=0) - datetime.timedelta(days=days)
    literal = cutoff.strftime("%Y-%m-%d %H:%M:%S")
    return f"CAST({column} AS TIMESTAMP) >= '{literal}'"


def _ai_sql_contains_clause(prompt: str, columns: list[str]) -> str:
    """Resolve '<column>에 <value>가 들어간' as a substring row filter."""
    if _ai_sql_projection_only_prompt(prompt, columns):
        return ""
    text = str(prompt or "")
    for column in sorted(columns, key=lambda value: len(str(value)), reverse=True):
        pattern = (
            r"(?<![A-Za-z0-9_])" + re.escape(str(column))
            + r"(?![A-Za-z0-9_])\s*(?:\uc5d0|\uc5d0\uc11c|:)?\s*"
              r"([A-Za-z0-9_.#-]{1,120})\s*(?:\uc774|\uac00)?\s*"
              r"(?:\ub4e4\uc5b4\uac04|\ud3ec\ud568(?:\ub41c)?)"
        )
        match = re.search(pattern, text, flags=re.I)
        if not match:
            continue
        value = match.group(1).strip()
        if not value or value.casefold() in {str(c).casefold() for c in columns}:
            continue
        return f"{column} LIKE '%{value.replace(chr(39), chr(39) * 2)}%'"
    return ""


def _fallback_ai_sql(prompt: str, columns: list[str], product: str = "", step_mapping_context: dict | None = None) -> str:
    prompt = str(prompt or "").strip()
    if not prompt or not columns:
        return ""
    if _ai_sql_projection_only_prompt(prompt, columns):
        return ""
    hits = _fallback_column_hits(prompt, columns)
    item_clause = _fallback_item_id_clause(prompt, columns)
    if item_clause:
        hits = [col for col in hits if col.casefold() != "item_id"]
    low = prompt.casefold()
    clauses: list[str] = []
    recent_clause = _ai_sql_recent_days_clause(prompt, columns)
    if recent_clause:
        clauses.append(recent_clause)
        recent_col = next((col for col in hits if re.search(rf"\b{re.escape(str(col))}\b", recent_clause, flags=re.I)), "")
        if recent_col:
            hits = [col for col in hits if col.casefold() != recent_col.casefold()]
    contains_clause = _ai_sql_contains_clause(prompt, columns)
    if contains_clause:
        clauses.append(contains_clause)
        contains_col = contains_clause.split(" LIKE ", 1)[0].strip()
        hits = [col for col in hits if col.casefold() != contains_col.casefold()]
        if item_clause and contains_col.casefold() == "item_id":
            item_clause = ""
    hash_wafer = _hash_wafer_clause(prompt, columns)
    if hash_wafer:
        clauses.append(hash_wafer)
        hits = [col for col in hits if col.casefold() not in {"wafer_id", "wf_id"}]
    if not any(col.casefold() in {"lot_id", "root_lot_id"} for col in hits):
        lot_clause = _fallback_lot_clause(prompt, columns)
        if lot_clause:
            clauses.append(lot_clause)
    step_clause = _ai_sql_step_mapping_clause(prompt, columns, product, step_mapping_context)
    if step_clause:
        clauses.append(step_clause)
        hits = [col for col in hits if col.casefold() not in {"step_id", "function_step"}]
    if item_clause:
        clauses.append(item_clause)
    for col in hits:
        window = _fallback_window(prompt, col)
        wlow = window.casefold()
        date_values = _extract_ai_sql_datetime_values(window) if _looks_date_like_column(col) else []
        values = date_values or _fallback_values(window, columns)
        less_match = re.search(r"(-?\d+(?:\.\d+)?)\s*보다\s*(?:작|낮)", window)
        greater_match = re.search(r"(-?\d+(?:\.\d+)?)\s*보다\s*(?:큰|크|높)", window)
        le_match = re.search(r"(-?\d+(?:\.\d+)?)\s*이하", window)
        ge_match = re.search(r"(-?\d+(?:\.\d+)?)\s*이상", window)
        if less_match and greater_match:
            clauses.append(f"{col} < {_sql_literal_for_filter(less_match.group(1), columns)}")
            clauses.append(f"{col} > {_sql_literal_for_filter(greater_match.group(1), columns)}")
            continue
        if greater_match and le_match:
            clauses.append(f"{col} > {_sql_literal_for_filter(greater_match.group(1), columns)}")
            clauses.append(f"{col} <= {_sql_literal_for_filter(le_match.group(1), columns)}")
            continue
        if ge_match and le_match:
            clauses.append(f"{col} >= {_sql_literal_for_filter(ge_match.group(1), columns)}")
            clauses.append(f"{col} <= {_sql_literal_for_filter(le_match.group(1), columns)}")
            continue
        if "null이 아닌" in wlow or "비어있지" in wlow or "not null" in wlow:
            clauses.append(f"{col} IS NOT NULL")
            continue
        if "null" in wlow and ("아닌" in wlow or "not" in wlow):
            clauses.append(f"{col} IS NOT NULL")
            continue
        if col.casefold() == "value":
            other_cols = [c for c in ("lsl", "usl") if c in {x.casefold() for x in columns} and c in wlow]
            if other_cols and ("보다 작은" in wlow or "작은" in wlow or "less" in wlow):
                clauses.append(f"{col} < {_column_lookup(columns).get(other_cols[0], other_cols[0])}")
                continue
            if other_cols and ("보다 큰" in wlow or "큰" in wlow or "greater" in wlow):
                clauses.append(f"{col} > {_column_lookup(columns).get(other_cols[0], other_cols[0])}")
                continue
        if ("포함" in wlow or "들어가" in wlow or "contains" in wlow) and values:
            safe = values[0].replace("'", "''")
            clauses.append(f"{col} LIKE '%{safe}%'")
            continue
        if ("시작" in wlow or "starts" in wlow) and values:
            safe = values[0].replace("'", "''")
            clauses.append(f"{col} LIKE '{safe}%'")
            continue
        if ("또는" in wlow or " or " in wlow) and len(values) >= 2:
            vals = ", ".join(_sql_literal_for_filter(v, columns) for v in values[:4])
            clauses.append(f"{col} IN ({vals})")
            continue
        if ("이상" in wlow or ">=" in wlow) and values:
            clauses.append(f"{col} >= {_sql_literal_for_filter(values[0], columns)}")
            if ("이하" in wlow or "<=" in wlow) and len(values) >= 2:
                clauses.append(f"{col} <= {_sql_literal_for_filter(values[1], columns)}")
            continue
        if ("이하" in wlow or "<=" in wlow) and values:
            clauses.append(f"{col} <= {_sql_literal_for_filter(values[0], columns)}")
            continue
        if ("보다 큰" in wlow or "초과" in wlow or ">" in wlow or "greater" in wlow) and values:
            clauses.append(f"{col} > {_sql_literal_for_filter(values[0], columns)}")
            continue
        if ("보다 작은" in wlow or "미만" in wlow or "<" in wlow or "less" in wlow) and values:
            clauses.append(f"{col} < {_sql_literal_for_filter(values[0], columns)}")
            continue
        if ("이후" in wlow or "after" in wlow) and values:
            clauses.append(f"{col} >= {_sql_literal_for_filter(values[0], columns)}")
            continue
        if ("이전" in wlow or "before" in wlow) and values:
            clauses.append(f"{col} <= {_sql_literal_for_filter(values[0], columns)}")
            continue
        if ("아닌" in wlow or "!=" in wlow or "not " in wlow) and values:
            clauses.append(f"{col} != {_sql_literal_for_filter(values[0], columns)}")
            continue
        if values:
            clauses.append(f"{col} = {_sql_literal_for_filter(values[0], columns)}")
    unique: list[str] = []
    for clause in clauses:
        if clause not in unique:
            unique.append(clause)
    if not unique:
        return ""
    joiner = " OR " if " 또는 " in low and len(unique) <= 2 else " AND "
    return joiner.join(unique)


def _fallback_item_id_clause(prompt: str, columns: list[str]) -> str:
    lookup = _column_lookup(columns)
    item_col = lookup.get("item_id")
    if not item_col:
        return ""
    text = str(prompt or "")
    patterns = (
        r"(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9_.-]{1,})\s+(?:value|값)(?![A-Za-z0-9_])",
        r"(?:item[_\s-]*id|아이템)\s*(?:가|이|은|는|=|:)?\s*([A-Za-z][A-Za-z0-9_.-]{1,})",
    )
    blocked = {c.casefold() for c in columns}
    blocked.update(_AI_SQL_IGNORE_TOKENS)
    blocked.update(_all_ai_sql_alias_tokens())
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if not match:
            continue
        value = _cache_safe_text(match.group(1), 120)
        if not value or value.casefold() in blocked:
            continue
        if _looks_numeric_like_value(value) or _looks_datetime_like_value(value):
            continue
        return f"{item_col} = {_sql_literal_for_filter(value, columns)}"
    for value in _fallback_values(text, columns):
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{1,}", value):
            continue
        if re.search(r"\d", value):
            continue
        if value.casefold() in blocked:
            continue
        if value.casefold() in {str(alias).casefold() for aliases in _AI_SQL_AGG_FUNCTION_ALIASES.values() for alias in aliases}:
            continue
        return f"{item_col} = {_sql_literal_for_filter(value, columns)}"
    return ""


def _ai_sql_sort_raw_values(raw) -> list:
    if raw is None:
        return []
    if isinstance(raw, list):
        return list(raw)
    if isinstance(raw, tuple):
        return list(raw)
    return [raw]


def _plan_ai_sql_sort(plan: dict):
    if not isinstance(plan, dict):
        return None
    for key in ("sort", "order_by", "ordering", "sort_by"):
        value = plan.get(key)
        if value:
            return value
    return None


def _normalize_ai_sql_sort(value, columns: list[str], warnings: list[str] | None = None,
                           context: str = "sort") -> dict:
    warnings = warnings if warnings is not None else []
    lookup = _column_lookup(columns)
    for raw in _ai_sql_sort_raw_values(value):
        column = ""
        direction = ""
        nulls = ""
        if isinstance(raw, str):
            parts = re.split(r"[\s,]+", raw.strip())
            if parts:
                column = parts[0]
            if len(parts) >= 2:
                direction = parts[1]
            if len(parts) >= 3:
                nulls = parts[2]
        elif isinstance(raw, dict):
            column = str(
                raw.get("column") or raw.get("col") or raw.get("name")
                or raw.get("field") or raw.get("order_by") or ""
            )
            direction = str(raw.get("direction") or raw.get("dir") or raw.get("order") or "")
            nulls = str(raw.get("nulls") or raw.get("null_order") or "")
        if not column:
            continue
        hit = lookup.get(column.casefold())
        if not hit:
            _draft_warning(warnings, f"{context}: unknown sort column removed: {column}")
            continue
        dir_l = direction.casefold().strip()
        if dir_l in {"desc", "descending", "내림차순", "큰순서", "큰", "높은순", "최신순"}:
            direction = "desc"
        elif dir_l in {"asc", "ascending", "오름차순", "작은순서", "작은", "낮은순", "오래된순"}:
            direction = "asc"
        elif dir_l:
            _draft_warning(warnings, f"{context}: unsupported sort direction ignored: {direction}")
            direction = "asc"
        else:
            direction = "asc"
        null_l = nulls.casefold().replace("_", " ").strip()
        if null_l in {"first", "nulls first", "앞", "처음"}:
            nulls = "first"
        else:
            nulls = "last"
        return {"column": hit, "direction": direction, "nulls": nulls}
    return {}


def _ai_sql_aggregate_raw_values(raw) -> list:
    if raw is None:
        return []
    if isinstance(raw, list):
        return list(raw)
    if isinstance(raw, tuple):
        return list(raw)
    return [raw]


def _plan_ai_sql_aggregate(plan: dict):
    if not isinstance(plan, dict):
        return None
    for key in ("aggregate", "aggregation", "agg", "summary"):
        value = plan.get(key)
        if value:
            return value
    return None


def _agg_function_from_text(value: str) -> str:
    text = str(value or "").casefold().strip()
    if not text:
        return ""
    for fn, aliases in _AI_SQL_AGG_FUNCTION_ALIASES.items():
        if text == fn or text in {str(alias).casefold() for alias in aliases}:
            return fn
    return ""


def _aggregate_alias(function: str, column: str = "") -> str:
    fn = _agg_function_from_text(function) or str(function or "").casefold()
    col = re.sub(r"[^A-Za-z0-9_]+", "_", str(column or "").strip()).strip("_")
    if fn == "count" and not col:
        return "count_rows"
    return f"{fn}_{col or 'rows'}"


def _clean_group_by_columns(values, columns: list[str], warnings: list[str] | None = None,
                            context: str = "aggregate.group_by") -> list[str]:
    warnings = warnings if warnings is not None else []
    lookup = _column_lookup(columns)
    if isinstance(values, str):
        raw_values = _clean_string_list(values)
    elif isinstance(values, (list, tuple, set)):
        raw_values = [str(v) for v in values if str(v or "").strip()]
    else:
        raw_values = []
    out: list[str] = []
    for value in raw_values:
        hit = lookup.get(str(value).casefold())
        if not hit:
            _draft_warning(warnings, f"{context}: unknown column removed: {value}")
            continue
        if hit not in out:
            out.append(hit)
    return out[:8]


def _normalize_ai_sql_aggregate(value, columns: list[str], warnings: list[str] | None = None,
                                context: str = "aggregate") -> dict:
    warnings = warnings if warnings is not None else []
    lookup = _column_lookup(columns)
    for raw in _ai_sql_aggregate_raw_values(value):
        function = ""
        column = ""
        group_by = []
        alias = ""
        if isinstance(raw, str):
            parts = re.split(r"[\s,()]+", raw.strip())
            if parts:
                function = _agg_function_from_text(parts[0])
            if len(parts) >= 2:
                column = parts[1]
            by_match = re.search(r"\bby\s+(.+)$", raw, flags=re.I)
            if by_match:
                group_by = _clean_group_by_columns(by_match.group(1), columns, warnings, f"{context}.group_by")
        elif isinstance(raw, dict):
            function = _agg_function_from_text(
                raw.get("function") or raw.get("func") or raw.get("op") or raw.get("type") or raw.get("agg") or ""
            )
            column = str(raw.get("column") or raw.get("col") or raw.get("field") or raw.get("value_column") or "")
            group_by = _clean_group_by_columns(
                raw.get("group_by") or raw.get("groupby") or raw.get("by") or [],
                columns,
                warnings,
                f"{context}.group_by",
            )
            alias = _cache_safe_text(raw.get("alias") or raw.get("name") or "", 80)
        if not function:
            continue
        hit = ""
        if column:
            hit = lookup.get(column.casefold()) or ""
            if not hit:
                _draft_warning(warnings, f"{context}: unknown aggregate column removed: {column}")
                continue
        if function != "count" and not hit:
            _draft_warning(warnings, f"{context}: aggregate column is required for {function}")
            continue
        if not alias:
            alias = _aggregate_alias(function, hit)
        alias = re.sub(r"[^A-Za-z0-9_]+", "_", alias).strip("_") or _aggregate_alias(function, hit)
        return {"function": function, "column": hit, "group_by": group_by, "alias": alias}
    return {}


def _fallback_ai_sql_aggregate(prompt: str, columns: list[str]) -> dict:
    text = str(prompt or "")
    low = text.casefold()
    function = ""
    for fn, aliases in _AI_SQL_AGG_FUNCTION_ALIASES.items():
        if any(str(alias).casefold() in low for alias in aliases):
            function = fn
            break
    if not function:
        return {}
    hits = _fallback_column_hits(text, columns)
    lookup = _column_lookup(columns)
    column = ""
    if function != "count":
        for hit in reversed(hits):
            if hit.casefold() not in {"product", "lot_id", "root_lot_id", "wafer_id", "wf_id", "item_id", "step_id"}:
                column = hit
                break
        if not column:
            for preferred in ("value", "rank", "knob_value"):
                if preferred in lookup:
                    column = lookup[preferred]
                    break
    group_by: list[str] = []
    if re.search(r"(?:별|별로|\bby\b|\bgroup\s+by\b)", low):
        group_by = [
            hit for hit in hits
            if hit != column and hit.casefold() not in {"lot_id", "root_lot_id", "item_id"}
        ][:4]
    if function != "count" and not column:
        return {}
    return {"function": function, "column": column, "group_by": group_by, "alias": _aggregate_alias(function, column)}


def _view_aggregate_query(agg_func: str = "", agg_column: str = "", agg_group_by: str = "") -> dict:
    if not isinstance(agg_func, str):
        agg_func = ""
    if not isinstance(agg_column, str):
        agg_column = ""
    if not isinstance(agg_group_by, str):
        agg_group_by = ""
    if not str(agg_func or "").strip():
        return {}
    return {
        "function": str(agg_func or "").strip(),
        "column": str(agg_column or "").strip(),
        "group_by": _clean_string_list(agg_group_by),
    }


def _aggregate_guard_select_cols(aggregate_spec: dict | None) -> str:
    if not aggregate_spec:
        return ""
    cols = []
    for col in (aggregate_spec.get("group_by") or []):
        if col and col not in cols:
            cols.append(col)
    col = aggregate_spec.get("column") or ""
    if col and col not in cols:
        cols.append(col)
    return ",".join(cols)


def _aggregate_expr(spec: dict):
    function = spec.get("function") or ""
    column = spec.get("column") or ""
    alias = spec.get("alias") or _aggregate_alias(function, column)
    if function == "count":
        expr = pl.col(column).count() if column else pl.len()
    elif function == "avg":
        expr = pl.col(column).cast(pl.Float64, strict=False).mean()
    elif function == "sum":
        expr = pl.col(column).cast(pl.Float64, strict=False).sum()
    elif function == "median":
        expr = pl.col(column).cast(pl.Float64, strict=False).median()
    elif function == "min":
        expr = pl.col(column).min()
    elif function == "max":
        expr = pl.col(column).max()
    else:
        raise ValueError(f"unsupported aggregate function: {function}")
    return expr.alias(alias)


def _apply_aggregate_lazy(lf: pl.LazyFrame, spec: dict) -> pl.LazyFrame:
    group_by = [str(c) for c in (spec.get("group_by") or []) if str(c or "").strip()]
    expr = _aggregate_expr(spec)
    if group_by:
        return lf.group_by(group_by).agg(expr)
    return lf.select(expr)


def _apply_aggregate_df(df: pl.DataFrame, spec: dict) -> pl.DataFrame:
    group_by = [str(c) for c in (spec.get("group_by") or []) if str(c or "").strip()]
    expr = _aggregate_expr(spec)
    if group_by:
        return df.group_by(group_by).agg(expr)
    return df.select(expr)


def _aggregate_sort_alias(sort_spec: dict, aggregate_spec: dict | None, output_columns: list[str]) -> dict:
    if not sort_spec:
        return {}
    spec = dict(sort_spec)
    column = str(spec.get("column") or "")
    if column in output_columns:
        return spec
    if aggregate_spec and column and column.casefold() == str(aggregate_spec.get("column") or "").casefold():
        spec["column"] = aggregate_spec.get("alias") or _aggregate_alias(
            aggregate_spec.get("function") or "",
            aggregate_spec.get("column") or "",
        )
    return spec


def _view_sort_query(sort_column: str = "", sort_direction: str = "", sort_nulls: str = "") -> dict:
    if not isinstance(sort_column, str):
        sort_column = ""
    if not isinstance(sort_direction, str):
        sort_direction = "asc"
    if not isinstance(sort_nulls, str):
        sort_nulls = "last"
    if not str(sort_column or "").strip():
        return {}
    return {
        "column": str(sort_column or "").strip(),
        "direction": str(sort_direction or "asc").strip() or "asc",
        "nulls": str(sort_nulls or "last").strip() or "last",
    }


def _resolve_view_sort_spec(sort_spec: dict | None, all_columns: list[str], *,
                            latest_first: bool = False) -> tuple[dict, str | None]:
    warnings: list[str] = []
    spec = _normalize_ai_sql_sort(sort_spec or {}, all_columns, warnings, "sort")
    if warnings:
        _fb_error(400, "unknown_sort_column", warnings[0])
    if spec:
        return spec, None
    latest_order_col = _latest_order_column(all_columns) if latest_first else ""
    if latest_order_col:
        return {"column": latest_order_col, "direction": "desc", "nulls": "last"}, latest_order_col
    return {}, None


def _sort_descending(spec: dict) -> bool:
    return str((spec or {}).get("direction") or "").casefold() == "desc"


def _sort_nulls_last(spec: dict) -> bool:
    return str((spec or {}).get("nulls") or "last").casefold() != "first"


def _sort_response_payload(spec: dict, latest_order_col: str | None) -> dict:
    if not spec or latest_order_col:
        return {}
    return {
        "column": spec.get("column") or "",
        "direction": spec.get("direction") or "asc",
        "nulls": spec.get("nulls") or "last",
    }


def _sort_expr(spec: dict, latest_order_col: str | None):
    expr = pl.col(spec["column"])
    return expr.cast(_SORT_STR, strict=False) if latest_order_col else expr


def _fallback_ai_sql_sort(prompt: str, columns: list[str]) -> dict:
    text = str(prompt or "")
    low = text.casefold()
    if not any(token in low or token in text for token in (
        "큰순서", "큰 순서", "높은순", "높은 순", "내림차순", "desc", "descending",
        "작은순서", "작은 순서", "낮은순", "낮은 순", "오름차순", "asc", "ascending",
        "최신순", "오래된순", "정렬", "순서", "sort", "order",
    )):
        return {}
    hits = _fallback_column_hits(text, columns)
    lookup = _column_lookup(columns)
    if not hits:
        for preferred in ("value", "rank", "tkout_time", "update_time", "measure_time"):
            if preferred in lookup and re.search(r"(?<![A-Za-z0-9_])" + re.escape(preferred) + r"(?![A-Za-z0-9_])", text, flags=re.I):
                hits.append(lookup[preferred])
                break
    if not hits:
        return {}
    direction = "asc"
    if any(token in low or token in text for token in (
        "큰순서", "큰 순서", "높은순", "높은 순", "내림차순", "desc", "descending", "최신순",
    )):
        direction = "desc"
    return {"column": hits[-1], "direction": direction, "nulls": "last"}


def _filebrowser_ai_sql_feedback_path() -> Path:
    return PATHS.data_root / FILEBROWSER_AI_SQL_FEEDBACK_FILE


def _filebrowser_ai_sql_history_path() -> Path:
    return PATHS.data_root / FILEBROWSER_AI_SQL_HISTORY_FILE


def _normalize_ai_sql_history_scope(scope: str) -> str:
    text = str(scope or "").strip().casefold()
    if text in {"hive", "db", "db_product", "product"}:
        return "db_product"
    if text in {"rootpq", "root_parquet", "parquet"}:
        return "rootpq"
    if text in {"base", "file", "single_file"}:
        return "base"
    return text or "db_product"


def _ai_sql_history_result_contract(result_payload: dict) -> dict:
    result = result_payload if isinstance(result_payload, dict) else {}
    merged = result.get("merged") if isinstance(result.get("merged"), dict) else {}
    preview = result.get("preview") if isinstance(result.get("preview"), dict) else {}
    tool = result.get("tool") if isinstance(result.get("tool"), dict) else {}
    sql_draft = tool.get("sql_draft") if isinstance(tool.get("sql_draft"), dict) else {}
    where_sql = (
        result.get("where_sql")
        or merged.get("where_sql")
        or preview.get("applied_where_sql")
        or sql_draft.get("where_sql")
        or sql_draft.get("sql")
        or result.get("sql")
        or ""
    )
    display_sql = (
        result.get("display_sql")
        or merged.get("display_sql")
        or merged.get("sql")
        or preview.get("display_sql")
        or preview.get("applied_sql")
        or sql_draft.get("display_sql")
        or sql_draft.get("sql")
        or result.get("sql")
        or ""
    )
    selected = (
        result.get("selected_columns")
        or merged.get("selected_columns")
        or preview.get("applied_select_cols")
        or sql_draft.get("selected_columns")
        or []
    )
    sort = (
        result.get("sort")
        or merged.get("sort")
        or preview.get("sort")
        or sql_draft.get("sort")
        or {}
    )
    aggregate = result.get("aggregate") or merged.get("aggregate") or sql_draft.get("aggregate") or {}
    warnings: list[str] = []
    for source in (
        result.get("warnings") if isinstance(result.get("warnings"), list) else [],
        merged.get("warnings") if isinstance(merged.get("warnings"), list) else [],
        preview.get("warnings") if isinstance(preview.get("warnings"), list) else [],
        sql_draft.get("warnings") if isinstance(sql_draft.get("warnings"), list) else [],
    ):
        for item in source or []:
            text = _cache_safe_text(item, 300)
            if text and text not in warnings:
                warnings.append(text)
    return {
        "answer": _cache_safe_text(result.get("answer") or result.get("reply") or result.get("notes") or "", 2000),
        "sql": _cache_safe_text(where_sql, 2000),
        "where_sql": _cache_safe_text(where_sql, 2000),
        "display_sql": _cache_safe_text(display_sql, 2000),
        "selected_columns": [str(c or "").strip() for c in (selected or []) if str(c or "").strip()][:100],
        "sort": sort if isinstance(sort, dict) else {},
        "aggregate": aggregate if isinstance(aggregate, dict) else {},
        "warnings": warnings[:10],
        "preview": preview,
        "trace": result.get("trace") if isinstance(result.get("trace"), list) else [],
        "action_log": result.get("action_log") if isinstance(result.get("action_log"), dict) else {},
    }


def _ai_sql_preview_history_summary(preview: dict) -> dict:
    if not isinstance(preview, dict):
        return {}
    rows = preview.get("rows") if isinstance(preview.get("rows"), list) else []
    columns = preview.get("columns") if isinstance(preview.get("columns"), list) else []
    total_rows = preview.get("total_rows")
    if total_rows is None:
        total_rows = preview.get("total")
    if total_rows is None:
        total_rows = preview.get("row_count")
    try:
        total_rows = int(total_rows) if total_rows is not None else None
    except Exception:
        total_rows = None
    try:
        rows_returned = int(preview.get("rows_returned") if preview.get("rows_returned") is not None else len(rows))
    except Exception:
        rows_returned = len(rows)
    return {
        "columns": [str(c or "").strip() for c in columns if str(c or "").strip()][:100],
        "rows_returned": rows_returned,
        "row_count": total_rows if total_rows is not None else rows_returned,
        "preview_capped": bool(preview.get("preview_capped")),
        "row_count_unknown": bool(preview.get("row_count_unknown")),
        "total_cols": preview.get("total_cols") if isinstance(preview.get("total_cols"), int) else None,
    }


def _ai_sql_trace_history_summary(trace: list) -> list[dict]:
    out: list[dict] = []
    for row in trace[:8] if isinstance(trace, list) else []:
        if not isinstance(row, dict):
            continue
        warnings = row.get("warnings") if isinstance(row.get("warnings"), list) else []
        out.append({
            "node_id": _cache_safe_text(row.get("node_id"), 80),
            "status": _cache_safe_text(row.get("status"), 40),
            "duration_ms": row.get("duration_ms") if isinstance(row.get("duration_ms"), int) else 0,
            "warnings": [_cache_safe_text(item, 180) for item in warnings[:3] if str(item or "").strip()],
        })
    return out


def _ai_sql_action_log_history_summary(action_log: dict) -> list[str]:
    if not isinstance(action_log, dict):
        return []
    summary = action_log.get("summary")
    if not isinstance(summary, list):
        return []
    return [_cache_safe_text(item, 240) for item in summary[:6] if str(item or "").strip()]


def _record_filebrowser_ai_sql_history(
    username: str,
    *,
    source: str,
    request_payload: dict,
    result_payload: dict,
) -> None:
    prompt = _cache_safe_text((request_payload or {}).get("natural_language"), 2000)
    if not prompt:
        return
    scope = _normalize_ai_sql_history_scope((request_payload or {}).get("scope") or "")
    root = _cache_safe_text((request_payload or {}).get("root"), 160)
    product = _cache_safe_text((request_payload or {}).get("product"), 160)
    file = _cache_safe_text((request_payload or {}).get("file"), 240)
    if not file and not (root and product):
        return
    contract = _ai_sql_history_result_contract(result_payload or {})
    entry = {
        "event": "history",
        "history_id": f"fb_sql_hist_{uuid.uuid4().hex[:10]}",
        "source": _cache_safe_text(source, 80),
        "username": _cache_safe_text(username, 80),
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "natural_language": prompt,
        "scope": scope,
        "root": root,
        "product": product,
        "file": file,
        "target": {
            "scope": scope,
            "root": root,
            "product": product,
            "file": file,
        },
        "ok": bool((result_payload or {}).get("ok")),
        "answer": contract["answer"],
        "sql": contract["sql"],
        "where_sql": contract["where_sql"],
        "display_sql": contract["display_sql"],
        "sort": contract["sort"],
        "aggregate": contract["aggregate"],
        "selected_columns": contract["selected_columns"],
        "warnings": contract["warnings"],
        "trace_summary": _ai_sql_trace_history_summary(contract["trace"]),
        "action_log_summary": _ai_sql_action_log_history_summary(contract["action_log"]),
        "preview_summary": _ai_sql_preview_history_summary(contract["preview"]),
    }
    jsonl_append(_filebrowser_ai_sql_history_path(), entry)
    try:
        jsonl_trim(_filebrowser_ai_sql_history_path(), 500)
    except Exception:
        pass


def _new_ai_sql_draft_id() -> str:
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    return f"fb_sql_{stamp}_{uuid.uuid4().hex[:8]}"


def _normalize_ai_sql_rating(value: str) -> str:
    text = str(value or "").strip().casefold()
    if text in {"up", "like", "liked", "good", "positive", "thumbs_up", "좋아요"}:
        return "up"
    if text in {"down", "dislike", "disliked", "bad", "negative", "thumbs_down", "싫어요"}:
        return "down"
    raise HTTPException(400, "rating must be up/down")


def _ai_sql_column_signature(columns: list[str] | tuple[str, ...] | None) -> list[str]:
    out: list[str] = []
    for col in columns or []:
        text = str(col or "").strip().casefold()
        if text and text not in out:
            out.append(text)
    return sorted(out)


def _ai_sql_prompt_tokens(text: str) -> set[str]:
    tokens = set(re.findall(r"[A-Za-z0-9_가-힣]{2,}", str(text or "").casefold()))
    tokens.difference_update({
        "and", "or", "the", "for", "show", "filter", "where", "value",
        "행", "조회", "보여줘", "필터", "정렬", "큰순서", "작은순서",
    })
    return tokens


def _ai_sql_similarity(left, right) -> float:
    a = set(left or [])
    b = set(right or [])
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def _ai_sql_feedback_entry(record: dict) -> dict:
    return {
        "draft_id": _cache_safe_text(record.get("draft_id"), 80),
        "natural_language": _cache_safe_text(record.get("natural_language"), 200),
        "sql": _cache_safe_text(record.get("sql"), 500),
        "where_sql": _cache_safe_text(record.get("where_sql") or record.get("sql"), 500),
        "display_sql": _cache_safe_text(record.get("display_sql") or record.get("sql"), 500),
        "sort": record.get("sort") if isinstance(record.get("sort"), dict) else {},
        "aggregate": record.get("aggregate") if isinstance(record.get("aggregate"), dict) else {},
        "selected_columns": [str(c) for c in (record.get("selected_columns") or []) if str(c or "").strip()][:20],
        "reason": _cache_safe_text(record.get("reason"), 240),
        "timestamp": _cache_safe_text(record.get("timestamp"), 60),
    }


def _ai_sql_feedback_context(username: str, prompt: str, columns: list[str], limit: int = 3) -> dict:
    username = _cache_safe_text(username, 80)
    if not username:
        return {"used": False, "positive": [], "negative": [], "counts": {"positive": 0, "negative": 0}, "conflicting": False}
    target_cols = set(_ai_sql_column_signature(columns))
    target_tokens = _ai_sql_prompt_tokens(prompt)
    positive: list[dict] = []
    negative: list[dict] = []
    for record in reversed(jsonl_read(_filebrowser_ai_sql_feedback_path(), limit=500)):
        if not isinstance(record, dict) or record.get("event") not in {None, "", "feedback"}:
            continue
        if _cache_safe_text(record.get("username"), 80) != username:
            continue
        record_cols = set(record.get("column_signature") or _ai_sql_column_signature(record.get("columns") or []))
        if target_cols and record_cols and _ai_sql_similarity(target_cols, record_cols) < 0.5:
            continue
        record_tokens = _ai_sql_prompt_tokens(record.get("natural_language") or "")
        prompt_score = _ai_sql_similarity(target_tokens, record_tokens)
        if target_tokens and record_tokens and prompt_score < 0.15:
            continue
        rating = str(record.get("rating") or "").casefold()
        entry = _ai_sql_feedback_entry(record)
        if rating == "up" and len(positive) < limit:
            positive.append(entry)
        elif rating == "down" and len(negative) < limit:
            negative.append(entry)
        if len(positive) >= limit and len(negative) >= limit:
            break
    return {
        "used": bool(positive or negative),
        "positive": positive,
        "negative": negative,
        "counts": {"positive": len(positive), "negative": len(negative)},
        "conflicting": bool(positive and negative),
    }


def _ai_sql_feedback_summary(context: dict) -> dict:
    counts = context.get("counts") if isinstance(context, dict) else {}
    return {
        "positive": int((counts or {}).get("positive") or 0),
        "negative": int((counts or {}).get("negative") or 0),
        "conflicting": bool((context or {}).get("conflicting")),
    }


def _ai_sql_feedback_hint(context: dict) -> dict:
    positives = context.get("positive") if isinstance(context, dict) else []
    return positives[0] if positives else {}


def _ai_sql_alternative_offer_allowed(username: str) -> bool:
    today = datetime.date.today().isoformat()
    for record in reversed(jsonl_read(_filebrowser_ai_sql_feedback_path(), limit=200)):
        if not isinstance(record, dict) or record.get("event") != "alternatives_offered":
            continue
        if _cache_safe_text(record.get("username"), 80) == username and str(record.get("date") or "") == today:
            return False
    return True


def _maybe_ai_sql_alternatives(username: str, payload: dict, context: dict) -> list[dict]:
    if not context.get("conflicting") or not _ai_sql_alternative_offer_allowed(username):
        return []
    positive = _ai_sql_feedback_hint(context)
    if not positive:
        return []
    jsonl_append(_filebrowser_ai_sql_feedback_path(), {
        "event": "alternatives_offered",
        "username": username,
        "date": datetime.date.today().isoformat(),
        "draft_id": payload.get("draft_id") or "",
    })
    return [
        {
            "key": "A",
            "label": "현재 초안",
            "sql": payload.get("sql") or "",
            "where_sql": payload.get("where_sql") or payload.get("sql") or "",
            "display_sql": payload.get("display_sql") or _build_ai_sql_display_sql(
                payload.get("selected_columns") or [],
                payload.get("where_sql") or payload.get("sql") or "",
                payload.get("sort") or {},
            ),
            "sort": payload.get("sort") or {},
            "aggregate": payload.get("aggregate") or {},
            "selected_columns": payload.get("selected_columns") or [],
        },
        {
            "key": "B",
            "label": "최근 좋아요 사례",
            "sql": positive.get("sql") or "",
            "where_sql": positive.get("where_sql") or positive.get("sql") or "",
            "display_sql": positive.get("display_sql") or _build_ai_sql_display_sql(
                positive.get("selected_columns") or [],
                positive.get("where_sql") or positive.get("sql") or "",
                positive.get("sort") or {},
            ),
            "sort": positive.get("sort") or {},
            "aggregate": positive.get("aggregate") or {},
            "selected_columns": positive.get("selected_columns") or [],
        },
    ]


def _ai_sql_context_columns(columns: list[str], dtypes: dict | None, sample_rows: list[dict] | None) -> list[dict]:
    dtype_map = {str(k): str(v) for k, v in (dtypes or {}).items()} if isinstance(dtypes, dict) else {}
    samples: dict[str, list[str]] = {c: [] for c in columns}
    for row in (sample_rows or [])[:AI_SQL_DEFAULT_SAMPLE_ROWS]:
        if not isinstance(row, dict):
            continue
        for col in columns:
            if col not in row:
                continue
            text = _cache_safe_text(row.get(col), 80)
            if text and text not in samples[col]:
                samples[col].append(text)
    return [
        {"name": col, "dtype": dtype_map.get(col, ""), "sample_values": samples.get(col, [])[:AI_SQL_PROFILE_VALUE_LIMIT]}
        for col in columns[:200]
    ]


def _plan_value_terms(plan: dict, prompt: str, columns: list[str]) -> tuple[list[str], list[str]]:
    value_terms = _fallback_values(prompt, columns)
    for value in _extract_ai_sql_datetime_values(prompt):
        if value not in value_terms:
            value_terms.append(value)
    resolved: list[str] = []
    if isinstance(plan, dict):
        raw = plan.get("resolved_values") or plan.get("value_terms") or plan.get("values") or []
        if isinstance(raw, str):
            raw = [raw]
        if isinstance(raw, list):
            for item in raw:
                text = _cache_safe_text(item, 120)
                if text and text not in resolved:
                    resolved.append(text)
    return resolved[:20], value_terms[:20]


def _ai_sql_selected_values(raw) -> list[str]:
    if isinstance(raw, str):
        return _clean_string_list(raw)
    if isinstance(raw, (list, tuple, set)):
        return _clean_string_list(raw)
    return []


def _plan_selected_columns(plan: dict) -> list[str]:
    if not isinstance(plan, dict):
        return []
    for key in (
        "selected_columns", "select_columns", "display_columns", "show_columns",
        "visible_columns", "columns_to_show", "select_cols",
    ):
        values = _ai_sql_selected_values(plan.get(key))
        if values:
            return values
    return []


def _exact_column_hits(text: str, columns: list[str]) -> list[str]:
    hits: list[str] = []
    lookup = _column_lookup(columns)
    for col in columns:
        canonical = lookup.get(str(col).casefold(), str(col))
        pattern = r"(?<![A-Za-z0-9_])" + re.escape(str(col)) + r"(?![A-Za-z0-9_])"
        if re.search(pattern, str(text or ""), flags=re.I) and canonical not in hits:
            hits.append(canonical)
    return hits


def _ai_sql_column_name_projection(prompt: str, columns: list[str]) -> list[str]:
    """Select exact columns plus columns whose *name* contains a requested token."""
    text = str(prompt or "")
    if not text or not columns:
        return []
    has_column_cue = bool(re.search(
        r"(?:\uc5f4(?:\ub4e4)?|\uceec\ub7fc(?:\ub4e4)?|columns?)",
        text,
        flags=re.I,
    ))
    if not has_column_cue:
        return []
    out = _exact_column_hits(text, columns)
    patterns = (
        r"([A-Za-z][A-Za-z0-9_.#-]{0,80})\s*(?:\uc774|\uac00)?\s*(?:\ub4e4\uc5b4\uac04|\ud3ec\ud568(?:\ub41c)?)\s*(?:\uc5f4(?:\ub4e4)?|\uceec\ub7fc(?:\ub4e4)?|columns?)",
        r"(?:columns?|\uc5f4(?:\ub4e4)?|\uceec\ub7fc(?:\ub4e4)?)\s*(?:containing|with|\ud3ec\ud568)\s*([A-Za-z][A-Za-z0-9_.#-]{0,80})",
    )
    tokens: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.I):
            token = str(match.group(1) or "").strip()
            if token and token.casefold() not in {"column", "columns"}:
                tokens.append(token)
    if not tokens:
        return []
    for token in tokens:
        folded = token.casefold()
        for column in columns:
            if folded in str(column).casefold() and column not in out:
                out.append(column)
    return out


def _ai_sql_projection_only_prompt(prompt: str, columns: list[str]) -> bool:
    selected = _ai_sql_column_name_projection(prompt, columns)
    if not selected:
        return False
    text = str(prompt or "")
    projection_action = bool(re.search(
        r"(?:\ucc3e\uc544|\ubcf4\uc5ec|\uc120\ud0dd|\ucd9c\ub825|show|find|select)",
        text,
        flags=re.I,
    ))
    filter_signal = bool(re.search(
        r"(?:[<>]=?|!=|\bwhere\b|\uc774\uc0c1|\uc774\ud558|\ucd08\uacfc|\ubbf8\ub9cc|\ucd5c\uadfc\s*\d+\s*\uc77c)",
        text,
        flags=re.I,
    ))
    return projection_action and not filter_signal


def _selection_segment_looks_like_columns(segment: str, hits: list[str]) -> bool:
    if not hits:
        return False
    text = str(segment or "")[-180:]
    if len(hits) >= 2 and re.search(r"[,/&+]|\b(and|및|그리고)\b", text, flags=re.I):
        return True
    if len(hits) == 1:
        col = hits[0]
        match = list(re.finditer(r"(?<![A-Za-z0-9_])" + re.escape(col) + r"(?![A-Za-z0-9_])", text, flags=re.I))
        if not match:
            return False
        tail = text[match[-1].end():].strip()
        if not tail:
            return True
        if re.search(r"[=<>]|(?:가|이|은|는)\s*\S|(?:이후|이전|이상|이하|초과|미만|포함|같)", tail):
            return False
        if re.search(r"[A-Za-z0-9가-힣]", tail):
            return False
        return len(tail) <= 4
    return False


def _fallback_selected_columns_from_prompt(prompt: str, columns: list[str]) -> list[str]:
    text = str(prompt or "")
    if not text or not columns:
        return []
    name_projection = _ai_sql_column_name_projection(text, columns)
    if name_projection:
        return name_projection
    cues = list(re.finditer(
        r"(?:만(?:\s*(?:보|표시|출력|조회|select))?|(?:컬럼|열|columns?)\s*(?:만|보|표시|출력|조회)?)",
        text,
        flags=re.I,
    ))
    for cue in cues:
        segment = text[max(0, cue.start() - 180):cue.start()]
        hits = _exact_column_hits(segment, columns)
        if _selection_segment_looks_like_columns(segment, hits):
            return hits
    return []


def _filter_ai_sql_selected_columns(values, columns: list[str], warnings: list[str], context: str) -> list[str]:
    lookup = _column_lookup(columns)
    out: list[str] = []
    seen: set[str] = set()
    for value in _ai_sql_selected_values(values):
        text = str(value or "").strip()
        if not text:
            continue
        hit = lookup.get(text.casefold())
        if not hit:
            _draft_warning(warnings, f"{context}: unknown column removed: {text}")
            continue
        key = hit.casefold()
        if key not in seen:
            seen.add(key)
            out.append(hit)
    return out


def _normalize_ai_sql_selected_columns(plan: dict, columns: list[str], preferred, prompt: str,
                                       warnings: list[str]) -> list[str]:
    name_projection = _ai_sql_column_name_projection(prompt, columns)
    if name_projection:
        return name_projection
    explicit_fallback = _fallback_selected_columns_from_prompt(prompt, columns)
    if not explicit_fallback:
        return []
    selected = _filter_ai_sql_selected_columns(_plan_selected_columns(plan), columns, warnings, "selected_columns")
    if selected:
        return selected
    return explicit_fallback


def _looks_numeric_like_value(value: str) -> bool:
    return bool(re.fullmatch(r"-?\d+(?:\.\d+)?", str(value or "").strip()))


def _looks_datetime_like_value(value: str) -> bool:
    text = str(value or "").strip()
    return bool(
        re.fullmatch(r"(?:19|20|21)\d{2}[-/.]\d{1,2}(?:[-/.]\d{1,2})?(?:[T\s]\d{1,2}:\d{1,2}(?::\d{1,2})?)?", text)
        or re.fullmatch(r"(?:19|20|21)\d{6}", text)
    )


def _ai_sql_prompt_priority_values(prompt: str, columns: list[str]) -> list[str]:
    values: list[str] = []
    for value in [*_fallback_values(prompt, columns), *_extract_ai_sql_datetime_values(prompt)]:
        text = _cache_safe_text(value, 160).strip()
        if text and text not in values:
            values.append(text)
    return values[:20]


def _ai_sql_profile_row_limit(prompt: str, columns: list[str], priority_values: list[str] | None = None) -> int:
    values = priority_values if priority_values is not None else _ai_sql_prompt_priority_values(prompt, columns)
    return AI_SQL_MAX_SAMPLE_ROWS if values else AI_SQL_DEFAULT_SAMPLE_ROWS


def _ai_sql_dtype_is_numeric(dtype: str) -> bool:
    text = str(dtype or "").casefold()
    return any(term in text for term in ("int", "float", "decimal", "numeric", "double"))


def _ai_sql_add_profile_column(out: list[str], lookup: dict[str, str], value) -> None:
    hit = lookup.get(str(value or "").casefold())
    if hit and hit not in out:
        out.append(hit)


def _ai_sql_profile_column_candidates(columns: list[str], dtypes: dict | None = None, *,
                                      prompt: str = "",
                                      preferred_selected_columns: list[str] | None = None) -> list[str]:
    columns = _settings_context_columns(columns)
    if not columns:
        return []
    lookup = _column_lookup(columns)
    dtype_map = {str(k): str(v) for k, v in (dtypes or {}).items()} if isinstance(dtypes, dict) else {}
    out: list[str] = []

    resolved_columns, _unknown = _resolve_ai_sql_prompt_columns(prompt, columns)
    for col in resolved_columns:
        _ai_sql_add_profile_column(out, lookup, col)
    for col in preferred_selected_columns or []:
        _ai_sql_add_profile_column(out, lookup, col)

    for col in columns:
        name = str(col).casefold()
        if name in _AI_SQL_IDENTITY_COLUMN_HINTS:
            _ai_sql_add_profile_column(out, lookup, col)
    for col in columns:
        name = str(col).casefold()
        if any(term in name for term in _AI_SQL_TIME_COLUMN_TERMS):
            _ai_sql_add_profile_column(out, lookup, col)
    for col in columns:
        name = str(col).casefold()
        if any(term in name for term in _AI_SQL_VALUE_COLUMN_TERMS) or _ai_sql_dtype_is_numeric(dtype_map.get(str(col), "")):
            _ai_sql_add_profile_column(out, lookup, col)

    if len(columns) <= AI_SQL_MAX_PROFILE_COLUMNS:
        for col in columns:
            _ai_sql_add_profile_column(out, lookup, col)
    if not out:
        for col in columns:
            _ai_sql_add_profile_column(out, lookup, col)
    return out[:AI_SQL_MAX_PROFILE_COLUMNS]


def _ai_sql_project_sample_rows(rows: list[dict], profile_columns: list[str]) -> list[dict]:
    if not profile_columns:
        return []
    out: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        lower_keys = {str(key).casefold(): key for key in row.keys()}
        clean: dict = {}
        for col in profile_columns[:AI_SQL_MAX_PROFILE_COLUMNS]:
            key = lower_keys.get(str(col).casefold())
            if key is not None:
                clean[col] = row.get(key)
        out.append(clean)
    return out


def _ai_sql_profile_sample_values(examples: list[str], priority_examples: list[str]) -> list[str]:
    out: list[str] = []
    for value in [*priority_examples, *examples]:
        if value not in out:
            out.append(value)
        if len(out) >= AI_SQL_PROFILE_VALUE_LIMIT:
            break
    return out


def _build_ai_sql_sample_profile(columns: list[str], dtypes: dict | None, sample_rows: list[dict] | None,
                                 *, source: str = "request", source_sampled: bool = False,
                                 warnings: list[str] | None = None, prompt: str = "",
                                 preferred_selected_columns: list[str] | None = None,
                                 max_rows: int | None = None,
                                 profile_columns: list[str] | None = None,
                                 priority_values: list[str] | None = None) -> dict:
    columns = _settings_context_columns(columns, sample_rows)
    dtype_map = {str(k): str(v) for k, v in (dtypes or {}).items()} if isinstance(dtypes, dict) else {}
    priority_values = priority_values if priority_values is not None else _ai_sql_prompt_priority_values(prompt, columns)
    rows_limit = _ai_sql_profile_row_limit(prompt, columns, priority_values)
    if max_rows is not None:
        try:
            rows_limit = max(0, min(AI_SQL_MAX_SAMPLE_ROWS, int(max_rows)))
        except Exception:
            rows_limit = _ai_sql_profile_row_limit(prompt, columns, priority_values)
    rows = [row for row in (sample_rows or [])[:rows_limit] if isinstance(row, dict)]
    profile_columns = profile_columns or _ai_sql_profile_column_candidates(
        columns,
        dtype_map,
        prompt=prompt,
        preferred_selected_columns=preferred_selected_columns,
    )
    profile_columns = _settings_context_columns(profile_columns)[:AI_SQL_MAX_PROFILE_COLUMNS]
    priority_lookup = {str(value).casefold() for value in priority_values}
    profiles: list[dict] = []
    for col in profile_columns:
        examples: list[str] = []
        priority_examples: list[str] = []
        null_count = 0
        blank_count = 0
        numeric_like_count = 0
        datetime_like_count = 0
        non_blank_count = 0
        for row in rows:
            raw = row.get(col)
            if raw is None:
                for key, value in row.items():
                    if str(key).casefold() == str(col).casefold():
                        raw = value
                        break
            if raw is None:
                null_count += 1
                continue
            text = _cache_safe_text(raw, 120).strip()
            if not text:
                blank_count += 1
                continue
            non_blank_count += 1
            if _looks_numeric_like_value(text):
                numeric_like_count += 1
            if _looks_datetime_like_value(text):
                datetime_like_count += 1
            if text.casefold() in priority_lookup and text not in priority_examples:
                priority_examples.append(text)
            elif text not in examples:
                examples.append(text)
        profiles.append({
            "name": col,
            "dtype": dtype_map.get(col, ""),
            "sample_values": _ai_sql_profile_sample_values(examples, priority_examples),
            "null_count": null_count,
            "blank_count": blank_count,
            "non_blank_count": non_blank_count,
            "numeric_like_count": numeric_like_count,
            "datetime_like_count": datetime_like_count,
        })
    return {
        "source": source,
        "source_sampled": bool(source_sampled),
        "max_rows": AI_SQL_MAX_SAMPLE_ROWS,
        "rows_sampled": len(rows),
        "columns_scanned": len(columns),
        "columns_profiled": len(profiles),
        "columns": profiles,
        "sampling_policy": {
            "default_rows": AI_SQL_DEFAULT_SAMPLE_ROWS,
            "max_rows": AI_SQL_MAX_SAMPLE_ROWS,
            "rows_limit": rows_limit,
            "profile_value_limit": AI_SQL_PROFILE_VALUE_LIMIT,
            "max_profile_columns": AI_SQL_MAX_PROFILE_COLUMNS,
            "column_strategy": "prompt_selected_identity_time_value_candidates",
            "row_dump_in_prompt": False,
            "extra_rows_for_value_terms": bool(priority_values and rows_limit > AI_SQL_DEFAULT_SAMPLE_ROWS),
        },
        "warnings": list(warnings or []),
    }


def _collect_ai_sql_lazy_context(lf, *, source: str, prompt: str = "",
                                 preferred_selected_columns: list[str] | None = None) -> tuple[list[str], dict, list[dict], dict]:
    schema_obj = lf.collect_schema()
    columns = list(schema_obj.names())
    dtypes = {name: str(schema_obj[name]) for name in columns}
    priority_values = _ai_sql_prompt_priority_values(prompt, columns)
    rows_limit = _ai_sql_profile_row_limit(prompt, columns, priority_values)
    sample_cols = _ai_sql_profile_column_candidates(
        columns,
        dtypes,
        prompt=prompt,
        preferred_selected_columns=preferred_selected_columns,
    )
    sample_lf = lf.select(sample_cols) if sample_cols else lf
    try:
        from core.parquet_perf import collect_streaming
        sample_df = collect_streaming(sample_lf.head(rows_limit))
    except Exception:
        sample_df = sample_lf.head(rows_limit).collect()
    sample_rows = serialize_rows(sample_df.to_dicts())
    profile = _build_ai_sql_sample_profile(
        columns,
        dtypes,
        sample_rows,
        source=source,
        source_sampled=True,
        prompt=prompt,
        preferred_selected_columns=preferred_selected_columns,
        max_rows=rows_limit,
        profile_columns=sample_cols,
        priority_values=priority_values,
    )
    return columns, dtypes, sample_rows, profile


def _resolve_ai_sql_profile_file(scope: str, file: str) -> Path | None:
    name = str(file or "").strip().replace("\\", "/")
    if not name:
        return None
    rel = Path(name)
    if rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
        return None
    roots = []
    if str(scope or "").casefold() == "rootpq":
        roots = [_db_root()]
    else:
        roots = [_base_root(), _db_root()]
    for root in roots:
        if not root.is_dir():
            continue
        cand = (root / rel).resolve()
        try:
            cand.relative_to(root.resolve())
        except ValueError:
            continue
        if cand.is_file() and cand.suffix.lower() in DATA_EXTENSIONS:
            return cand
    return None


def _ai_sql_context_from_source(*, scope: str, root: str, product: str, file: str,
                                columns: list[str], dtypes: dict | None,
                                sample_rows: list[dict] | None, prompt: str = "",
                                preferred_selected_columns: list[str] | None = None) -> tuple[list[str], dict, list[dict], dict, list[str]]:
    warnings: list[str] = []
    try:
        if root and product:
            lf = lazy_read_source(
                root=root,
                product=product,
                recent_days=30,
                max_files=LATEST_PREVIEW_MAX_FILES,
                latest_only=True,
            )
            if lf is not None:
                cols, dtype_map, rows, profile = _collect_ai_sql_lazy_context(
                    lf,
                    source=f"hive:{_cache_safe_text(root, 80)}/{_cache_safe_text(product, 80)}",
                    prompt=prompt,
                    preferred_selected_columns=preferred_selected_columns,
                )
                return cols, dtype_map, rows, profile, warnings
        fp = _resolve_ai_sql_profile_file(scope, file)
        if fp is not None:
            lf = scan_one_file(fp)
            if lf is not None:
                cols, dtype_map, rows, profile = _collect_ai_sql_lazy_context(
                    lf,
                    source=f"file:{_cache_safe_text(file, 160)}",
                    prompt=prompt,
                    preferred_selected_columns=preferred_selected_columns,
                )
                return cols, dtype_map, rows, profile, warnings
    except Exception as exc:
        _draft_warning(warnings, f"sample profile source scan failed: {type(exc).__name__}: {exc}")
    clean_columns = _settings_context_columns(columns, sample_rows)
    priority_values = _ai_sql_prompt_priority_values(prompt, clean_columns)
    rows_limit = _ai_sql_profile_row_limit(prompt, clean_columns, priority_values)
    profile_columns = _ai_sql_profile_column_candidates(
        clean_columns,
        dtypes or {},
        prompt=prompt,
        preferred_selected_columns=preferred_selected_columns,
    )
    clean_rows = _safe_sample_rows(sample_rows or [], max_rows=rows_limit, max_cols=500, max_value_len=120)
    clean_rows = _ai_sql_project_sample_rows(clean_rows, profile_columns)
    profile = _build_ai_sql_sample_profile(
        clean_columns,
        dtypes or {},
        clean_rows,
        source="request",
        source_sampled=False,
        warnings=warnings,
        prompt=prompt,
        preferred_selected_columns=preferred_selected_columns,
        max_rows=rows_limit,
        profile_columns=profile_columns,
        priority_values=priority_values,
    )
    return clean_columns, dict(dtypes or {}), clean_rows, profile, warnings


def _ai_sql_value_catalog_source(source_ref: dict | None):
    source = source_ref or {}
    scope = _cache_safe_text(source.get("scope"), 80)
    root = _cache_safe_text(source.get("root"), 160)
    product = _cache_safe_text(source.get("product"), 160)
    file = _cache_safe_text(source.get("file"), 240)
    if root and product:
        lf = lazy_read_source(
            root=root,
            product=product,
            recent_days=30,
            max_files=LATEST_PREVIEW_MAX_FILES,
            latest_only=True,
        )
        return lf, f"hive:{root}/{product}"
    if file:
        fp = _resolve_ai_sql_profile_file(scope, file)
        if fp is None:
            fp = _resolve_data_file_for_schema(file, _load_filebrowser_settings())
        if fp is not None:
            return scan_one_file(fp), f"file:{file}"
    return None, ""


def _ai_sql_value_catalog_candidates(columns: list[str], dtypes: dict | None, prompt: str, sample_profile: dict | None) -> list[str]:
    clean_columns = _settings_context_columns(columns)
    dtype_map = {str(k): str(v) for k, v in (dtypes or {}).items()} if isinstance(dtypes, dict) else {}
    candidates = _ai_sql_profile_column_candidates(clean_columns, dtype_map, prompt=prompt)
    prompt_norm = re.sub(r"[^0-9a-z가-힣]+", "", str(prompt or "").casefold())
    for item in (sample_profile or {}).get("columns") or []:
        if not isinstance(item, dict):
            continue
        col = _cache_safe_text(item.get("name"), 160)
        if not col or col not in clean_columns:
            continue
        values = [_cache_safe_text(value, 160) for value in (item.get("sample_values") or [])]
        if any(
            (value_norm := re.sub(r"[^0-9a-z가-힣]+", "", value.casefold())) and value_norm in prompt_norm
            for value in values
            if len(value) >= 2
        ):
            if col not in candidates:
                candidates.insert(0, col)
    return candidates[:20]


def _ai_sql_distinct_values(lf, column: str, limit: int = 30) -> list[str]:
    if lf is None or not column:
        return []
    try:
        q = (
            lf.select(pl.col(column).cast(_SORT_STR, strict=False).alias(column))
            .drop_nulls(subset=[column])
            .unique(subset=[column], maintain_order=True)
            .limit(max(1, min(100, int(limit or 30))))
        )
        try:
            from core.parquet_perf import collect_streaming
            df = collect_streaming(q)
        except Exception:
            df = q.collect()
        values: list[str] = []
        for row in serialize_rows(df.to_dicts()):
            text = _cache_safe_text(row.get(column), 160).strip()
            if text and text not in values:
                values.append(text)
        return values
    except Exception:
        return []


def _ai_sql_value_catalog_matches(*, prompt: str, columns: list[str], dtypes: dict | None,
                                  sample_profile: dict | None, source_ref: dict | None) -> list[dict]:
    """Read-only, on-demand value lookup for Agent semantic resolution.

    This reuses the FileBrowser source resolver and keeps only hot in-memory
    cache entries. It never writes to DB/CSV/Parquet or schema profile files.
    """
    prompt_text = _cache_safe_text(prompt, 2000)
    clean_columns = _settings_context_columns(columns)
    candidates = _ai_sql_value_catalog_candidates(clean_columns, dtypes or {}, prompt_text, sample_profile)
    if not prompt_text or not clean_columns or not candidates:
        return []
    source_lf, source_id = _ai_sql_value_catalog_source(source_ref)
    if source_lf is None:
        return []
    priority_values = _ai_sql_prompt_priority_values(prompt_text, clean_columns)
    prompt_norm = re.sub(r"[^0-9a-z가-힣]+", "", prompt_text.casefold())
    cache_key = (
        source_id,
        tuple(candidates),
        tuple(sorted(str(value).casefold() for value in priority_values[:20])),
    )
    now = time.monotonic()
    cached = _AI_SQL_VALUE_CATALOG_CACHE.get(cache_key)
    if cached and now - cached[0] < 300:
        return copy.deepcopy(cached[1])
    matches: list[dict] = []
    for column in candidates:
        values = _ai_sql_distinct_values(source_lf, column, limit=30)
        for value in values:
            value_norm = re.sub(r"[^0-9a-z가-힣]+", "", value.casefold())
            if len(value_norm) < 2:
                continue
            priority_hit = any(value.casefold() == str(item).casefold() for item in priority_values)
            prompt_hit = value_norm in prompt_norm
            if not priority_hit and not prompt_hit:
                continue
            matches.append({
                "column": column,
                "value": value,
                "source": source_id,
                "confidence": 0.86 if prompt_hit else 0.72,
                "match_reason": "prompt_value" if prompt_hit else "priority_value",
            })
            if len(matches) >= 40:
                break
        if len(matches) >= 40:
            break
    _AI_SQL_VALUE_CATALOG_CACHE[cache_key] = (now, copy.deepcopy(matches))
    if len(_AI_SQL_VALUE_CATALOG_CACHE) > 32:
        oldest = sorted(_AI_SQL_VALUE_CATALOG_CACHE.items(), key=lambda item: item[1][0])[:8]
        for key, _ in oldest:
            _AI_SQL_VALUE_CATALOG_CACHE.pop(key, None)
    return matches


def _draft_filebrowser_ai_sql(*, natural_language: str, columns: list[str],
                              dtypes: dict | None = None,
                              sample_rows: list[dict] | None = None,
                              current_sql: str = "", scope: str = "",
                              root: str = "", product: str = "",
                              file: str = "",
                              preferred_selected_columns: list[str] | None = None,
                              sample_profile: dict | None = None,
                              context_warnings: list[str] | None = None,
                              username: str = "") -> dict:
    prompt = _cache_safe_text(natural_language, 2000)
    if not prompt:
        raise HTTPException(400, "natural_language is required")
    columns = _settings_context_columns(columns)
    current_sql = _cache_safe_text(current_sql, 1000)
    context = {
        "scope": _cache_safe_text(scope, 80),
        "root": _cache_safe_text(root, 160),
        "product": _cache_safe_text(product, 160),
        "file": _cache_safe_text(file, 240),
    }
    profile = sample_profile or _build_ai_sql_sample_profile(
        columns,
        dtypes,
        sample_rows,
        source="request",
        prompt=prompt,
        preferred_selected_columns=preferred_selected_columns,
    )
    profile_columns = [
        item for item in (profile.get("columns") if isinstance(profile, dict) else []) or []
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]
    columns_for_prompt = [str(item.get("name") or "").strip() for item in profile_columns][:AI_SQL_MAX_PROFILE_COLUMNS]
    if not columns_for_prompt:
        columns_for_prompt = columns[:AI_SQL_MAX_PROFILE_COLUMNS]
    column_context = [
        {
            "name": str(item.get("name") or "").strip(),
            "dtype": _cache_safe_text(item.get("dtype"), 80),
            "sample_values": [
                _cache_safe_text(value, 80)
                for value in (item.get("sample_values") or [])[:AI_SQL_PROFILE_VALUE_LIMIT]
            ],
        }
        for item in profile_columns[:AI_SQL_MAX_PROFILE_COLUMNS]
    ] or _ai_sql_context_columns(columns_for_prompt, dtypes, [])
    warnings: list[str] = list(context_warnings or [])
    resolved_columns, unknown_column_terms = _resolve_ai_sql_prompt_columns(prompt, columns)
    if unknown_column_terms:
        warnings.append("Unknown column-like terms: " + ", ".join(unknown_column_terms[:8]))
    step_mapping_context = _ai_sql_step_mapping_context(prompt, columns, context.get("product") or "")
    draft_id = _new_ai_sql_draft_id()
    username = _cache_safe_text(username, 80)
    feedback_context = _ai_sql_feedback_context(username, prompt, columns)

    def _finish(payload: dict) -> dict:
        payload.setdefault("draft_id", draft_id)
        payload.setdefault("sort", {})
        payload.setdefault("aggregate", {})
        where_sql = _cache_safe_text(payload.get("where_sql") if payload.get("where_sql") is not None else payload.get("sql"), 2000)
        selected = [
            str(c or "").strip()
            for c in (payload.get("selected_columns") or [])
            if str(c or "").strip()
        ]
        payload["sql"] = where_sql
        payload["where_sql"] = where_sql
        payload["selected_columns"] = selected
        payload["display_sql"] = _build_ai_sql_display_sql(selected, where_sql, payload.get("sort") or {})
        payload["feedback_context_used"] = bool(feedback_context.get("used"))
        payload["feedback_context"] = _ai_sql_feedback_summary(feedback_context)
        payload["step_mapping"] = _public_ai_sql_step_mapping_context(step_mapping_context)
        payload["alternatives"] = _maybe_ai_sql_alternatives(username, payload, feedback_context)
        return payload

    llm_info = {"available": False, "used": False, "error": ""}
    raw_text = ""
    plan: dict = {}
    try:
        from core import llm_adapter
        llm_info["available"] = bool(llm_adapter.is_available())
        if llm_info["available"]:
            system = _filebrowser_agent_prompt("sql_draft.system", (
                "You write Flow FileBrowser SQL filter expressions. Return only JSON. "
                "The output must be a single read-only WHERE/filter expression in the sql field. "
                "Use only provided columns. Do not return SELECT, FROM, JOIN, ORDER BY, LIMIT, "
                "DDL, DML, comments, semicolons, markdown, or explanation. "
                "If step_mapping_context.used is true, use its target_sql for the step condition; "
                "do not filter a function_step name directly when target_column is step_id. "
                "Prefer SQL syntax: =, !=, >, >=, <, <=, LIKE, NOT LIKE, IN (...), "
                "IS NULL, IS NOT NULL, AND, OR. In WHERE only, you may use "
                "CAST(column AS DOUBLE|FLOAT|BIGINT|INTEGER|INT|DATE|TIMESTAMP|DATETIME|TIME) "
                "for string-stored numeric or temporal columns. When schema dtype or samples show a numeric "
                "or temporal value stored as string, always use CAST/TRY_CAST before range, arithmetic-like, "
                "or date comparison; the server normalizes either spelling to safe TRY_CAST. Do not cast "
                "root_lot_id or wafer_id for plain equality/list matching because Flow normalizes those keys "
                "as strings. Treat contains/들어간 row-value requests as "
                "LIKE '%value%', recent N days as a rolling timestamp predicate, time-named columns as "
                "TIMESTAMP, and wafer_id/wf_id as BIGINT only for explicit numeric range/order semantics. "
                "A request for column names containing a token "
                "is display projection and must return an empty sql filter."
            ))
            ask = json.dumps({
                "natural_language": prompt,
                "current_sql": current_sql,
                "columns": columns_for_prompt,
                "schema": column_context,
                "sample_rows": [],
                "sample_profile": profile,
                "supported_where_casts": _AI_SQL_CAST_GUIDE,
                "step_mapping_context": _public_ai_sql_step_mapping_context(step_mapping_context),
                "feedback_context": {
                    "liked_examples": feedback_context.get("positive") or [],
                    "avoid_examples": feedback_context.get("negative") or [],
                    "counts": feedback_context.get("counts") or {},
                },
                "preferred_selected_columns": _filter_ai_sql_selected_columns(
                    preferred_selected_columns or [], columns, [], "preferred_selected_columns"
                ),
                "context": context,
                "response_schema": {
                    "sql": "column = 'value' AND CAST(value AS DOUBLE) >= 10",
                    "sort": {"column": "value", "direction": "desc", "nulls": "last"},
                    "aggregate": {"function": "avg", "column": "value", "group_by": ["item_id"]},
                    "selected_columns": ["column", "other_col"],
                    "resolved_columns": ["column"],
                    "resolved_values": ["value"],
                    "notes": "optional short note",
                },
            }, ensure_ascii=False)
            out = llm_adapter.complete_json(
                ask,
                system=system,
                timeout=20,
                max_retries=1,
                schema={
                    "keys": ["sql", "sort", "aggregate", "selected_columns", "resolved_columns", "resolved_values", "notes"],
                    "required": [],
                    "properties": {"sql": {}, "sort": {}, "aggregate": {}, "selected_columns": {}, "resolved_columns": {}, "resolved_values": {}, "notes": {}},
                },
            )
            raw_text = str(out.get("text") or "")
            llm_info["used"] = bool(out.get("ok") and isinstance(out.get("obj"), dict))
            if out.get("error"):
                llm_info["error"] = str(out.get("error") or "")
            if out.get("repaired"):
                llm_info["repaired_json"] = True
            plan = out.get("obj") if isinstance(out.get("obj"), dict) else {}
        else:
            warnings.append("LLM is not configured.")
    except Exception as exc:
        llm_info["error"] = f"{type(exc).__name__}: {exc}"
    if llm_info.get("error"):
        warnings.append(f"LLM failed: {llm_info['error']}")
    raw_sql = _extract_llm_sql_text(raw_text, plan)
    resolved_values, value_terms = _plan_value_terms(plan, prompt, columns)
    for value in [*(step_mapping_context.get("function_steps") or []), *(step_mapping_context.get("step_ids") or [])]:
        text = _cache_safe_text(value, 160)
        if text and text not in resolved_values:
            resolved_values.append(text)
    selected_columns = _normalize_ai_sql_selected_columns(
        plan,
        columns,
        preferred_selected_columns or [],
        prompt,
        warnings,
    )
    parsed_sql = raw_sql
    parsed_selected_columns: list[str] = []
    parsed_sort_spec: dict = {}
    try:
        parsed_sql, parsed_selected_columns, parsed_sort_spec = _parse_ai_sql_display_sql(raw_sql, columns)
    except Exception as exc:
        warnings.append(f"display_sql parse warning: {exc}")
    if parsed_selected_columns:
        selected_columns = _filter_ai_sql_selected_columns(
            [*parsed_selected_columns, *selected_columns],
            columns,
            warnings,
            "display_sql_selected_columns",
        )
    sort_spec = parsed_sort_spec or _normalize_ai_sql_sort(_plan_ai_sql_sort(plan), columns, warnings, "sort")
    if not sort_spec:
        sort_spec = _fallback_ai_sql_sort(prompt, columns)
    aggregate_spec = _normalize_ai_sql_aggregate(_plan_ai_sql_aggregate(plan), columns, warnings, "aggregate")
    if not aggregate_spec:
        aggregate_spec = _fallback_ai_sql_aggregate(prompt, columns)
    projection_only = _ai_sql_projection_only_prompt(prompt, columns)
    deterministic_required = bool(
        _ai_sql_recent_days_clause(prompt, columns)
        or _ai_sql_contains_clause(prompt, columns)
    )
    if projection_only:
        raw_sql = ""
        parsed_sql = ""
        _draft_warning(warnings, "column-name search treated as display projection, not a row filter")
    elif deterministic_required:
        deterministic_sql = _fallback_ai_sql(
            prompt,
            columns,
            product=context.get("product") or "",
            step_mapping_context=step_mapping_context,
        )
        if deterministic_sql:
            parsed_sql = deterministic_sql
            _draft_warning(warnings, "deterministic contains/recent-time semantics applied")
    try:
        if projection_only:
            return _finish({
                "ok": True,
                "saved": False,
                "unit_action": "filebrowser.sql.llm.draft",
                "sql": "",
                "sort": sort_spec,
                "aggregate": aggregate_spec,
                "selected_columns": selected_columns,
                "sample_profile": profile,
                "warnings": warnings,
                "columns": columns,
                "resolved_columns": resolved_columns,
                "unknown_column_terms": unknown_column_terms,
                "resolved_values": resolved_values,
                "value_terms": value_terms,
                "llm": llm_info,
                "fallback": not llm_info.get("used"),
            })
        raw_sql = _merge_ai_sql_step_mapping_filter(parsed_sql, step_mapping_context, warnings)
        if _llm_misread_hash_wafer(prompt, raw_sql, columns):
            raise ValueError("Prompt #N token must be interpreted as wafer_id, not lot_id text")
        sql, validate_warnings = _validate_ai_sql_filter(raw_sql, columns)
        warnings.extend(validate_warnings)
    except Exception as exc:
        fallback = _fallback_ai_sql(
            prompt,
            columns,
            product=context.get("product") or "",
            step_mapping_context=step_mapping_context,
        )
        feedback_hint = _ai_sql_feedback_hint(feedback_context)
        if not fallback and feedback_hint.get("sql"):
            fallback = str(feedback_hint.get("sql") or "")
            _draft_warning(warnings, "recent liked feedback used as deterministic fallback hint")
        if not sort_spec and isinstance(feedback_hint.get("sort"), dict):
            sort_spec = _normalize_ai_sql_sort(feedback_hint.get("sort"), columns, warnings, "feedback_sort")
        if not aggregate_spec and isinstance(feedback_hint.get("aggregate"), dict):
            aggregate_spec = _normalize_ai_sql_aggregate(feedback_hint.get("aggregate"), columns, warnings, "feedback_aggregate")
        if fallback:
            try:
                sql, validate_warnings = _validate_ai_sql_filter(fallback, columns)
                return _finish({
                    "ok": True,
                    "saved": False,
                    "unit_action": "filebrowser.sql.llm.draft",
                    "sql": sql,
                    "sort": sort_spec,
                    "aggregate": aggregate_spec,
                    "selected_columns": selected_columns,
                    "sample_profile": profile,
                    "warnings": [*warnings, f"LLM draft was not usable: {exc}", "deterministic fallback used"],
                    "columns": columns,
                    "resolved_columns": resolved_columns,
                    "unknown_column_terms": unknown_column_terms,
                    "resolved_values": resolved_values,
                    "value_terms": value_terms,
                    "llm": llm_info,
                    "fallback": True,
                })
            except Exception as fallback_exc:
                warnings.append(f"deterministic fallback failed: {fallback_exc}")
        if not raw_sql.strip() and (selected_columns or sort_spec or aggregate_spec):
            return _finish({
                "ok": True,
                "saved": False,
                "unit_action": "filebrowser.sql.llm.draft",
                "sql": "",
                "sort": sort_spec,
                "aggregate": aggregate_spec,
                "selected_columns": selected_columns,
                "sample_profile": profile,
                "warnings": warnings,
                "columns": columns,
                "resolved_columns": resolved_columns,
                "unknown_column_terms": unknown_column_terms,
                "resolved_values": resolved_values,
                "value_terms": value_terms,
                "llm": llm_info,
                "fallback": not llm_info.get("used"),
            })
        return _finish({
            "ok": False,
            "saved": False,
            "unit_action": "filebrowser.sql.llm.draft",
            "sql": "",
            "sort": sort_spec,
            "aggregate": aggregate_spec,
            "selected_columns": selected_columns,
            "sample_profile": profile,
            "warnings": [*warnings, str(exc)],
            "columns": columns,
            "resolved_columns": resolved_columns,
            "unknown_column_terms": unknown_column_terms,
            "resolved_values": resolved_values,
            "value_terms": value_terms,
            "llm": llm_info,
        })
    return _finish({
        "ok": True,
        "saved": False,
        "unit_action": "filebrowser.sql.llm.draft",
        "sql": sql,
        "sort": sort_spec,
        "aggregate": aggregate_spec,
        "selected_columns": selected_columns,
        "sample_profile": profile,
        "warnings": warnings,
        "columns": columns,
        "resolved_columns": resolved_columns,
        "unknown_column_terms": unknown_column_terms,
        "resolved_values": resolved_values,
        "value_terms": value_terms,
        "llm": llm_info,
        "fallback": False,
    })


def _run_view(df, sql: str, select_cols: str, rows: int,
              page: int = 0, page_size: int | None = None, preview_cols: int | None = None,
              latest_first: bool = False, latest_preview: bool = False,
              sort_spec: dict | None = None,
              aggregate_spec: dict | None = None):
    """Apply select + sql + head; return standard response dict. Legacy DataFrame path."""
    all_columns = list(df.columns)
    schema = {n: str(d) for n, d in df.schema.items()}
    sql, select_cols, sort_spec = _merge_display_sql_into_args(sql, select_cols, sort_spec, all_columns)
    df, wafer_filtered = _filter_valid_wafers_df(df)
    total = df.height
    page_size = int(page_size or rows or 200)
    page, page_size, offset = _page_args(page, page_size)

    warnings: list[str] = []
    active_aggregate = _normalize_ai_sql_aggregate(aggregate_spec or {}, all_columns, warnings, "aggregate")
    if warnings:
        _fb_error(400, "invalid_aggregate", warnings[0])
    sort_columns = all_columns + ([active_aggregate.get("alias")] if active_aggregate else [])
    sel, truncated_cols = _selected_columns(all_columns, "" if active_aggregate else select_cols, preview_cols)
    normalized_sql = _validate_where_expression(sql, all_columns)
    if sql and sql.strip():
        df = apply_sql_like(df, _normalize_polars_view_sql_filter(normalized_sql, all_columns, schema))
        total = df.height
    if active_aggregate:
        df = _apply_aggregate_df(df, active_aggregate)
        total = df.height
        sel = list(df.columns)
        truncated_cols = False
    active_sort, latest_order_col = _resolve_view_sort_spec(sort_spec, sort_columns, latest_first=latest_first and not active_aggregate)
    if active_aggregate:
        active_sort = _aggregate_sort_alias(active_sort, active_aggregate, list(df.columns))
        if active_sort and active_sort.get("column") not in df.columns:
            _fb_error(400, "unknown_sort_column", f"sort: unknown sort column removed: {active_sort.get('column')}")
    if active_sort and active_sort.get("column") in df.columns:
        df = df.sort(
            _sort_expr(active_sort, latest_order_col),
            descending=_sort_descending(active_sort),
            nulls_last=_sort_nulls_last(active_sort),
        )
    if sel:
        df = df.select(sel)
    show = df.slice(offset, page_size)
    return {
        "total_rows": total, "total_cols": len(all_columns),
        "columns": list(show.columns), "all_columns": all_columns,
        "dtypes": schema, "showing_cols": list(show.columns),
        "selected_cols": None if active_aggregate else select_cols.strip() or None,
        "data": serialize_rows(show.to_dicts()), "showing": len(show),
        "page": page, "page_size": page_size,
        "has_more": offset + len(show) < total,
        "preview_cols": len(show.columns),
        "truncated_cols": truncated_cols,
        "latest_order_col": latest_order_col or None,
        "sort": _sort_response_payload(active_sort, latest_order_col),
        "where_sql": normalized_sql,
        "display_sql": _build_ai_sql_display_sql(
            [c.strip() for c in select_cols.split(",") if c.strip()],
            normalized_sql,
            _sort_response_payload(active_sort, latest_order_col),
        ),
        "aggregate": active_aggregate,
        "latest_preview": bool(latest_preview),
        "wafer_filter": {"max": MAX_WAFER_ID} if wafer_filtered else None,
    }


def _run_view_duckdb(files: list[Path], sql: str, select_cols: str, rows: int,
                     page: int = 0, page_size: int | None = None,
                     preview_cols: int | None = None,
                     latest_first: bool = False, latest_preview: bool = False,
                     cached_meta: dict | None = None,
                     settings: dict | None = None,
                     sort_spec: dict | None = None,
                     query_key: str = ""):
    """Apply the same preview contract through DuckDB for large read-only sources."""
    source_size = duckdb_engine.total_size(files)
    # 연결/등록(=전체 parquet footer 스키마 스캔)은 한 번만 수행하고, 같은
    # 연결로 preview SELECT 까지 실행한다. inspect + query 이중 register 제거.
    con, all_columns, schema = duckdb_engine.open_source(files, query_key=query_key)
    try:
        sql, select_cols, sort_spec = _merge_display_sql_into_args(sql, select_cols, sort_spec, all_columns)
        normalized_sql = _validate_where_expression(sql, all_columns)
        _guard_source_operation(
            all_columns=all_columns,
            sql=normalized_sql,
            select_cols=select_cols,
            source_size=source_size,
            settings=settings,
            operation="preview",
        )
        page_size = int(page_size or rows or 200)
        page, page_size, offset = _page_args(page, page_size)
        sel, truncated_cols = _selected_columns(all_columns, select_cols, preview_cols)
        active_sort, latest_order_col = _resolve_view_sort_spec(sort_spec, all_columns, latest_first=latest_first)
        # open_source() already validates and normalizes the wafer column in
        # its _source view. Repeating the regex/TRY_CAST predicate here forced
        # another expensive per-row pass and impeded predicate pushdown.
        wafer_filtered = bool(_wafer_column(all_columns))
        wafer_where = ""
        user_where = _normalize_view_sql_filter(normalized_sql, all_columns, schema)
        show_plus = duckdb_engine.run_source_query(
            con, all_columns,
            where=_combine_where(user_where, wafer_where),
            select_cols=sel,
            limit=page_size + 1,
            offset=offset,
            order_by=active_sort.get("column") or "",
            descending=_sort_descending(active_sort),
        )
    finally:
        duckdb_engine.release_query(query_key, con)
        try:
            con.close()
        except Exception:  # noqa: BLE001
            pass
    has_more = show_plus.height > page_size
    show = show_plus.head(page_size) if has_more else show_plus
    total = offset + show.height + (1 if has_more else 0)
    return {
        "total_rows": total,
        "total_cols": len(all_columns),
        "columns": list(show.columns),
        "all_columns": all_columns,
        "dtypes": schema,
        "showing_cols": list(show.columns),
        "selected_cols": select_cols.strip() or None,
        "data": serialize_rows(show.to_dicts()),
        "showing": len(show),
        "page": page,
        "page_size": page_size,
        "has_more": has_more,
        "preview_cols": len(show.columns),
        "truncated_cols": truncated_cols,
        "latest_order_col": latest_order_col or None,
        "sort": _sort_response_payload(active_sort, latest_order_col),
        "where_sql": normalized_sql,
        "display_sql": _build_ai_sql_display_sql(
            [c.strip() for c in select_cols.split(",") if c.strip()],
            normalized_sql,
            _sort_response_payload(active_sort, latest_order_col),
        ),
        "aggregate": {},
        "latest_preview": bool(latest_preview),
        "engine": "duckdb",
        "source_file_count": len(files),
        "source_size": source_size,
        "total_rows_exact": False,
        "meta_cached": bool(cached_meta),
        "wafer_filter": {"max": MAX_WAFER_ID} if wafer_filtered else None,
    }


def _run_view_lazy(lf, sql: str, select_cols: str, rows: int, meta_only: bool = False,
                   page: int = 0, page_size: int | None = None, cached_meta: dict | None = None,
                   preview_cols: int | None = None, latest_first: bool = False,
                   latest_preview: bool = False,
                   allow_eager_sql_fallback: bool = False,
                   source_size: int | None = None,
                   settings: dict | None = None,
                   sort_spec: dict | None = None,
                   aggregate_spec: dict | None = None):
    """v8.4.3 OOM-aware: lazy 스캔 + projection pushdown + head + (필요 시) SQL.

    - 컬럼 선택 / head 은 lazy 에서 처리 → parquet reader 에서 필요한 컬럼·행만 읽음
    - SQL 필터도 lazy filter 로 밀어 넣고 첫 페이지 + 1행만 collect
    - 초기 미리보기(SQL/select 없음) 는 page 단위 slice 로 10GB 파일도 필요한 행만 로드
    - v8.8.16: meta_only=True 는 컬럼 스키마만 반환 (collect 없음) → 클릭 즉시 반응.
              실제 행 조회는 SQL 실행 / 컬럼 선택 적용 시점으로 이연.
    """
    schema_obj = lf.collect_schema()
    all_columns = list(schema_obj.names())
    schema = {n: str(schema_obj[n]) for n in all_columns}
    sql, select_cols, sort_spec = _merge_display_sql_into_args(sql, select_cols, sort_spec, all_columns)
    preview_cols = _preview_cols_limit(preview_cols or _settings_preview_max_columns(settings))
    page_size = int(page_size or rows or 200)
    page, page_size, offset = _page_args(page, page_size)
    warnings: list[str] = []
    active_aggregate = _normalize_ai_sql_aggregate(aggregate_spec or {}, all_columns, warnings, "aggregate")
    if warnings:
        _fb_error(400, "invalid_aggregate", warnings[0])
    sort_columns = all_columns + ([active_aggregate.get("alias")] if active_aggregate else [])
    active_sort, latest_order_col = _resolve_view_sort_spec(
        sort_spec,
        sort_columns,
        latest_first=latest_first and not active_aggregate,
    )
    lf, wafer_filtered = _filter_valid_wafers_lazy(lf, all_columns)

    if meta_only:
        # 스키마만 — 어떤 collect() 도 하지 않음. 큰 parquet/CSV 도 수 ms.
        total_rows = int((cached_meta or {}).get("row_count") or 0)
        return {
            "total_rows": total_rows, "total_cols": len(all_columns),
            "columns": all_columns[:preview_cols], "all_columns": all_columns,
            "dtypes": schema, "showing_cols": [],
            "selected_cols": select_cols.strip() or None,
            "data": [], "showing": 0, "meta_only": True,
            "page": page, "page_size": page_size, "has_more": False,
            "meta_cached": bool(cached_meta),
            "total_rows_exact": bool(cached_meta) and not wafer_filtered,
            "preview_cols": min(len(all_columns), preview_cols),
            "truncated_cols": len(all_columns) > preview_cols,
            "latest_order_col": latest_order_col or None,
            "sort": _sort_response_payload(active_sort, latest_order_col),
            "where_sql": "",
            "display_sql": _build_ai_sql_display_sql(
                [c.strip() for c in select_cols.split(",") if c.strip()],
                "",
                _sort_response_payload(active_sort, latest_order_col),
            ),
            "aggregate": {},
            "wafer_filter": {"max": MAX_WAFER_ID} if wafer_filtered else None,
        }

    normalized_sql = _validate_where_expression(sql, all_columns)
    guard_select_cols = _aggregate_guard_select_cols(active_aggregate) if active_aggregate else select_cols
    _guard_source_operation(
        all_columns=all_columns,
        sql=normalized_sql,
        select_cols=guard_select_cols,
        source_size=source_size,
        settings=settings,
        operation="preview",
    )

    # Keep SQL filtering on the full source schema.  Projection is applied only
    # after the filter, so users can filter by a column that is not selected for
    # display/download.
    sel, truncated_cols = _selected_columns(all_columns, "" if active_aggregate else select_cols, preview_cols)

    if active_aggregate:
        work_lf = lf
        if sql and sql.strip():
            work_lf = work_lf.filter(_lazy_filter_expr(normalized_sql, all_columns, schema))
        work_lf = _apply_aggregate_lazy(work_lf, active_aggregate)
        output_columns = list(active_aggregate.get("group_by") or []) + [active_aggregate.get("alias")]
        active_sort = _aggregate_sort_alias(active_sort, active_aggregate, output_columns)
        if active_sort and active_sort.get("column") not in output_columns:
            _fb_error(400, "unknown_sort_column", f"sort: unknown sort column removed: {active_sort.get('column')}")
        if active_sort:
            work_lf = work_lf.sort(
                _sort_expr(active_sort, None),
                descending=_sort_descending(active_sort),
                nulls_last=_sort_nulls_last(active_sort),
            )
        try:
            from core.parquet_perf import collect_streaming
            show_plus = collect_streaming(work_lf.slice(offset, page_size + 1))
        except Exception:
            show_plus = work_lf.slice(offset, page_size + 1).collect()
        has_more = show_plus.height > page_size
        show = show_plus.head(page_size) if has_more else show_plus
        total = offset + show.height + (1 if has_more else 0)
        return {
            "total_rows": total, "total_cols": len(all_columns),
            "columns": list(show.columns), "all_columns": all_columns,
            "dtypes": {**schema, **{c: str(show.schema[c]) for c in show.columns if c in show.schema}},
            "showing_cols": list(show.columns),
            "selected_cols": None,
            "data": serialize_rows(show.to_dicts()), "showing": len(show),
            "page": page, "page_size": page_size, "has_more": has_more,
            "meta_cached": bool(cached_meta),
            "total_rows_exact": False,
            "preview_cols": len(show.columns),
            "truncated_cols": False,
            "latest_order_col": None,
            "sort": _sort_response_payload(active_sort, None),
            "where_sql": normalized_sql,
            "display_sql": _build_ai_sql_display_sql([], normalized_sql, _sort_response_payload(active_sort, None)),
            "aggregate": active_aggregate,
            "latest_preview": bool(latest_preview),
            "wafer_filter": {"max": MAX_WAFER_ID} if wafer_filtered else None,
        }

    if sql and sql.strip():
        # Keep SQL lazy. Exact counts and eager fallback are intentionally
        # avoided on production-size parquet because they double-scan or OOM.
        try:
            from core.parquet_perf import collect_streaming
            filtered = lf.filter(_lazy_filter_expr(normalized_sql, all_columns, schema))
            if active_sort:
                filtered = filtered.sort(
                    _sort_expr(active_sort, latest_order_col),
                    descending=_sort_descending(active_sort),
                    nulls_last=_sort_nulls_last(active_sort),
                )
            show_lf = filtered.select(sel) if sel else filtered
            show_plus = collect_streaming(show_lf.slice(offset, page_size + 1))
            has_more = show_plus.height > page_size
            show = show_plus.head(page_size) if has_more else show_plus
            total = offset + show.height + (1 if has_more else 0)
            total_exact = False
        except Exception:
            if not allow_eager_sql_fallback:
                raise
            try:
                from core.parquet_perf import collect_streaming
                df = collect_streaming(lf)
            except Exception:
                df = lf.collect()
            df = apply_sql_like(df, _normalize_polars_view_sql_filter(normalized_sql, all_columns, schema))
            total = df.height
            if active_sort and active_sort.get("column") in df.columns:
                df = df.sort(
                    _sort_expr(active_sort, latest_order_col),
                    descending=_sort_descending(active_sort),
                    nulls_last=_sort_nulls_last(active_sort),
                )
            if sel:
                df = df.select(sel)
            show = df.slice(offset, page_size)
            has_more = offset + len(show) < total
            total_exact = True
    else:
        # Page path: parquet scan + lazy slice → only fetches the rows we need.
        if active_sort:
            lf = lf.sort(
                _sort_expr(active_sort, latest_order_col),
                descending=_sort_descending(active_sort),
                nulls_last=_sort_nulls_last(active_sort),
            )
        if sel:
            lf = lf.select(sel)
        try:
            from core.parquet_perf import collect_streaming
            show_plus = collect_streaming(lf.slice(offset, page_size + 1 if wafer_filtered else page_size))
        except Exception:
            show_plus = lf.slice(offset, page_size + 1 if wafer_filtered else page_size).collect()
        if wafer_filtered:
            has_more = show_plus.height > page_size
            show = show_plus.head(page_size) if has_more else show_plus
            total = offset + show.height + (1 if has_more else 0)
            total_exact = False
        else:
            show = show_plus
            total = int((cached_meta or {}).get("row_count") or 0) or (offset + show.height)
            has_more = show.height == page_size if not cached_meta else offset + show.height < total
            total_exact = bool(cached_meta)

    return {
        "total_rows": total, "total_cols": len(all_columns),
        "columns": list(show.columns), "all_columns": all_columns,
        "dtypes": schema, "showing_cols": list(show.columns),
        "selected_cols": select_cols.strip() or None,
        "data": serialize_rows(show.to_dicts()), "showing": len(show),
        "page": page, "page_size": page_size, "has_more": has_more,
        "meta_cached": bool(cached_meta),
        "total_rows_exact": total_exact,
        "preview_cols": len(show.columns),
        "truncated_cols": truncated_cols,
        "latest_order_col": latest_order_col or None,
        "sort": _sort_response_payload(active_sort, latest_order_col),
        "where_sql": normalized_sql,
        "display_sql": _build_ai_sql_display_sql(
            [c.strip() for c in select_cols.split(",") if c.strip()],
            normalized_sql,
            _sort_response_payload(active_sort, latest_order_col),
        ),
        "aggregate": active_aggregate,
        "latest_preview": bool(latest_preview),
        "wafer_filter": {"max": MAX_WAFER_ID} if wafer_filtered else None,
    }


def _run_view_lazy_full(lf, sql: str, select_cols: str, preview_cols: int | None = None,
                        latest_first: bool = False,
                        sort_spec: dict | None = None,
                        aggregate_spec: dict | None = None):
    """Collect a single lightweight file fully after optional SQL/projection."""
    schema_obj = lf.collect_schema()
    all_columns = list(schema_obj.names())
    schema = {n: str(schema_obj[n]) for n in all_columns}
    sql, select_cols, sort_spec = _merge_display_sql_into_args(sql, select_cols, sort_spec, all_columns)
    warnings: list[str] = []
    active_aggregate = _normalize_ai_sql_aggregate(aggregate_spec or {}, all_columns, warnings, "aggregate")
    if warnings:
        _fb_error(400, "invalid_aggregate", warnings[0])
    sort_columns = all_columns + ([active_aggregate.get("alias")] if active_aggregate else [])
    active_sort, latest_order_col = _resolve_view_sort_spec(
        sort_spec,
        sort_columns,
        latest_first=latest_first and not active_aggregate,
    )
    lf, wafer_filtered = _filter_valid_wafers_lazy(lf, all_columns)

    normalized_sql = _validate_where_expression(sql, all_columns)
    if sql and sql.strip():
        lf = lf.filter(_lazy_filter_expr(normalized_sql, all_columns, schema))
    if active_aggregate:
        lf = _apply_aggregate_lazy(lf, active_aggregate)
        output_columns = list(active_aggregate.get("group_by") or []) + [active_aggregate.get("alias")]
        active_sort = _aggregate_sort_alias(active_sort, active_aggregate, output_columns)
        if active_sort and active_sort.get("column") not in output_columns:
            _fb_error(400, "unknown_sort_column", f"sort: unknown sort column removed: {active_sort.get('column')}")
    if active_sort:
        lf = lf.sort(
            _sort_expr(active_sort, None if active_aggregate else latest_order_col),
            descending=_sort_descending(active_sort),
            nulls_last=_sort_nulls_last(active_sort),
        )
    if active_aggregate:
        sel, truncated_cols = [], False
    elif preview_cols is None:
        sel, truncated_cols = _selected_columns(all_columns, select_cols, len(all_columns) or 1)
    else:
        sel, truncated_cols = _selected_columns(all_columns, select_cols, preview_cols)
    if sel:
        lf = lf.select(sel)
    try:
        from core.parquet_perf import collect_streaming
        show = collect_streaming(lf)
    except Exception:
        show = lf.collect()
    return {
        "total_rows": show.height, "total_cols": len(all_columns),
        "columns": list(show.columns), "all_columns": all_columns,
        "dtypes": schema, "showing_cols": list(show.columns),
        "selected_cols": select_cols.strip() or None,
        "data": serialize_rows(show.to_dicts()), "showing": show.height,
        "page": 0, "page_size": show.height, "has_more": False,
        "meta_cached": False,
        "total_rows_exact": True,
        "preview_cols": len(show.columns),
        "truncated_cols": truncated_cols,
        "latest_order_col": None if active_aggregate else latest_order_col or None,
        "sort": _sort_response_payload(active_sort, None if active_aggregate else latest_order_col),
        "where_sql": normalized_sql,
        "display_sql": _build_ai_sql_display_sql(
            [] if active_aggregate else [c.strip() for c in select_cols.split(",") if c.strip()],
            normalized_sql,
            _sort_response_payload(active_sort, None if active_aggregate else latest_order_col),
        ),
        "aggregate": active_aggregate,
        "latest_preview": False,
        "single_file_full_read": True,
        "wafer_filter": {"max": MAX_WAFER_ID} if wafer_filtered else None,
    }


def _csv_download_max_rows(raw: int | None = None) -> int:
    try:
        return max(1, min(MAX_CSV_DOWNLOAD_MAX_ROWS, int(raw or DEFAULT_CSV_DOWNLOAD_MAX_ROWS)))
    except Exception:
        return DEFAULT_CSV_DOWNLOAD_MAX_ROWS


def _csv_download_max_bytes(raw: int | None = None, settings: dict | None = None) -> int:
    settings = settings or _load_filebrowser_settings()
    default = int(settings.get("csv_download_max_bytes") or DEFAULT_CSV_DOWNLOAD_MAX_BYTES)
    try:
        return max(1, min(MAX_CSV_DOWNLOAD_BYTES, int(raw or default)))
    except Exception:
        return default


def _sql_query_max_source_bytes(settings: dict | None = None) -> int:
    settings = settings or _load_filebrowser_settings()
    try:
        return max(0, min(MAX_SQL_QUERY_MAX_SOURCE_BYTES, int(settings.get("sql_query_max_source_bytes") or 0)))
    except Exception:
        return DEFAULT_SQL_QUERY_MAX_SOURCE_BYTES


def _settings_preview_max_columns(settings: dict | None = None) -> int:
    settings = settings or {}
    try:
        return max(1, min(MAX_PREVIEW_MAX_COLUMNS, int(settings.get("preview_max_columns") or DEFAULT_PREVIEW_MAX_COLUMNS)))
    except Exception:
        return DEFAULT_PREVIEW_MAX_COLUMNS


def _settings_schema_column_page_size(settings: dict | None = None) -> int:
    settings = settings or {}
    try:
        return max(1, min(MAX_SCHEMA_COLUMN_PAGE_SIZE, int(settings.get("schema_column_page_size") or DEFAULT_SCHEMA_COLUMN_PAGE_SIZE)))
    except Exception:
        return DEFAULT_SCHEMA_COLUMN_PAGE_SIZE


def _fb_error(status_code: int, code: str, message: str, **extra):
    detail = {"code": code, "message": message}
    detail.update({k: v for k, v in extra.items() if v is not None})
    raise HTTPException(status_code, detail)


def _filter_present(sql: str) -> bool:
    return bool(str(sql or "").strip())


def _selected_requested_columns(select_cols: str, all_columns: list[str]) -> list[str]:
    allowed = set(all_columns or [])
    return [c.strip() for c in str(select_cols or "").split(",") if c.strip() in allowed]


def _validate_where_expression(sql: str, columns: list[str] | tuple[str, ...] | None = None) -> str:
    try:
        return _normalize_where_expression(sql, columns)
    except ValueError as exc:
        message = str(exc)
        code = "unknown_column" if "unknown column" in message.casefold() else "invalid_filter"
        _fb_error(400, code, message)


def _guard_source_operation(
    *,
    all_columns: list[str],
    sql: str,
    select_cols: str,
    source_size: int | None,
    settings: dict | None,
    operation: str,
) -> None:
    settings = settings or {}
    selected = _selected_requested_columns(select_cols, all_columns)
    if operation in {"preview", "download"} and selected:
        max_cols = _settings_preview_max_columns(settings)
        if len(selected) > max_cols:
            _fb_error(
                400,
                "too_many_columns_without_projection",
                f"선택 컬럼 {len(selected)}개가 허용 한도 {max_cols}개를 넘습니다.",
                selected_columns=len(selected),
                max_columns=max_cols,
            )
    size = int(source_size or 0)
    max_source = _sql_query_max_source_bytes(settings)
    if size > 0 and max_source > 0 and size > max_source and not _filter_present(sql) and not selected:
        _fb_error(
            400,
            "filter_required",
            "Source is too large to read without a SQL filter or selected columns.",
            source_size=size,
            max_source_bytes=max_source,
            operation=operation,
        )
    if operation == "download" and not selected and len(all_columns or []) > MAX_CSV_DOWNLOAD_AUTO_COLUMNS:
        _fb_error(
            400,
            "too_many_columns_without_projection",
            f"CSV 대상이 {len(all_columns)}열입니다. 컬럼 탭에서 필요한 열을 선택한 뒤 다운로드하세요.",
            total_cols=len(all_columns),
            max_auto_columns=MAX_CSV_DOWNLOAD_AUTO_COLUMNS,
        )


def _csv_bytes_checked(df: pl.DataFrame, max_bytes: int) -> bytes:
    csv_bytes = df.write_csv().encode("utf-8")
    if len(csv_bytes) > max_bytes:
        _fb_error(
            400,
            "download_too_large",
            f"CSV result is {len(csv_bytes):,} bytes, above the {max_bytes:,} byte limit. Select fewer columns or add a SQL filter.",
            result_bytes=len(csv_bytes),
            max_bytes=max_bytes,
        )
    return csv_bytes


def _apply_schema_column_cap(resp: dict, settings: dict | None = None) -> dict:
    if not isinstance(resp, dict):
        return resp
    all_columns = [str(c) for c in (resp.get("all_columns") or [])]
    if not all_columns:
        return resp
    limit = _settings_schema_column_page_size(settings)
    truncated = len(all_columns) > limit
    returned = all_columns[:limit] if truncated else all_columns
    resp["all_columns"] = returned
    resp["all_columns_truncated"] = truncated
    resp["schema_columns_returned"] = len(returned)
    resp["schema_column_limit"] = limit
    if truncated:
        keep = set(returned)
        keep.update(str(c) for c in (resp.get("columns") or []))
        keep.update(str(c) for c in (resp.get("showing_cols") or []))
        dtypes = resp.get("dtypes") or {}
        if isinstance(dtypes, dict):
            resp["dtypes"] = {c: dtypes[c] for c in keep if c in dtypes}
    return resp


def _finalize_preview_response(resp: dict, settings: dict | None = None) -> dict:
    resp = _mark_preview_capped(resp)
    if settings:
        resp["download_max_bytes"] = _csv_download_max_bytes(None, settings)
        resp["download_max_rows"] = int(settings.get("csv_download_max_rows") or MAX_CSV_DOWNLOAD_MAX_ROWS)
        resp["preview_row_limit"] = int(settings.get("preview_max_rows") or LATEST_PREVIEW_ROWS)
    resp.setdefault("meta_only", False)
    resp.setdefault("meta_cached", False)
    resp.setdefault("requires_filter", False)
    resp.setdefault("query_block_reason", "")
    if resp.get("meta_only"):
        resp["row_count_unknown"] = not bool(resp.get("meta_cached")) and not bool(resp.get("total_rows"))
    else:
        resp.setdefault("row_count_unknown", False)
    resp["preview_capped"] = bool(
        (not resp.get("meta_only"))
        and (not resp.get("single_file_full_read"))
        and (
            bool(resp.get("has_more"))
            or bool(resp.get("truncated_cols"))
            or int(resp.get("showing") or 0) >= int(resp.get("preview_row_limit") or LATEST_PREVIEW_ROWS)
        )
    )
    return _apply_schema_column_cap(resp, settings)


def _download_lazy_csv(
    lf: pl.LazyFrame,
    sql: str,
    select_cols: str,
    max_rows: int,
    max_bytes: int | None = None,
    *,
    source_size: int | None = None,
    settings: dict | None = None,
    aggregate_spec: dict | None = None,
    sort_spec: dict | None = None,
) -> tuple[pl.DataFrame, bytes]:
    schema_obj = lf.collect_schema()
    all_columns = list(schema_obj.names())
    schema = {n: str(schema_obj[n]) for n in all_columns}
    sql, select_cols, sort_spec = _merge_display_sql_into_args(sql, select_cols, sort_spec, all_columns)
    normalized_sql = _validate_where_expression(sql, all_columns)
    warnings: list[str] = []
    active_aggregate = _normalize_ai_sql_aggregate(aggregate_spec or {}, all_columns, warnings, "aggregate")
    if warnings:
        _fb_error(400, "invalid_aggregate", warnings[0])
    guard_select_cols = _aggregate_guard_select_cols(active_aggregate) if active_aggregate else select_cols
    _guard_source_operation(
        all_columns=all_columns,
        sql=normalized_sql,
        select_cols=guard_select_cols,
        source_size=source_size,
        settings=settings,
        operation="download",
    )
    lf, _wafer_filtered = _filter_valid_wafers_lazy(lf, all_columns)
    requested = [c.strip() for c in str(select_cols or "").split(",") if c.strip()]
    selected = [c for c in requested if c in set(all_columns)]
    if sql and sql.strip():
        try:
            lf = lf.filter(_lazy_filter_expr(normalized_sql, all_columns, schema))
        except Exception as e:
            raise HTTPException(400, f"CSV download SQL error: {e}")
    if active_aggregate:
        lf = _apply_aggregate_lazy(lf, active_aggregate)
        selected = []
    sort_columns = all_columns + ([active_aggregate.get("alias")] if active_aggregate else [])
    active_sort, latest_order_col = _resolve_view_sort_spec(sort_spec, sort_columns)
    if active_aggregate:
        output_columns = list(active_aggregate.get("group_by") or []) + [active_aggregate.get("alias")]
        active_sort = _aggregate_sort_alias(active_sort, active_aggregate, output_columns)
        if active_sort and active_sort.get("column") not in output_columns:
            _fb_error(400, "unknown_sort_column", f"sort: unknown sort column removed: {active_sort.get('column')}")
    if active_sort:
        lf = lf.sort(
            _sort_expr(active_sort, latest_order_col),
            descending=_sort_descending(active_sort),
            nulls_last=_sort_nulls_last(active_sort),
        )
    if selected:
        lf = lf.select(selected)
    try:
        from core.parquet_perf import collect_streaming
        df = collect_streaming(lf.head(max_rows + 1))
    except Exception:
        df = lf.head(max_rows + 1).collect()
    if df.height > max_rows:
        raise HTTPException(
            400,
            f"CSV 다운로드는 최대 {max_rows:,}행까지 허용됩니다. SQL 필터를 추가하거나 max_rows를 조정하세요.",
        )
    csv_bytes = _csv_bytes_checked(df, _csv_download_max_bytes(max_bytes, settings))
    return df, csv_bytes


def _is_dtype_mismatch_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".casefold()
    return any(token in text for token in (
        "data type mismatch",
        "dtype mismatch",
        "schema mismatch",
        "schemaerror",
        "cannot compare 'date/datetime/time' to a string value",
    ))


def _download_duckdb_csv(
    files: list[Path],
    sql: str,
    select_cols: str,
    max_rows: int,
    max_bytes: int | None = None,
    *,
    settings: dict | None = None,
    sort_spec: dict | None = None,
) -> tuple[pl.DataFrame, bytes]:
    if not files:
        raise ValueError("no source files for DuckDB download")
    all_columns, schema = duckdb_engine.inspect_files(files)
    sql, select_cols, sort_spec = _merge_display_sql_into_args(sql, select_cols, sort_spec, all_columns)
    normalized_sql = _validate_where_expression(sql, all_columns)
    _guard_source_operation(
        all_columns=all_columns,
        sql=normalized_sql,
        select_cols=select_cols,
        source_size=duckdb_engine.total_size(files),
        settings=settings,
        operation="download",
    )
    requested = [c.strip() for c in str(select_cols or "").split(",") if c.strip()]
    selected = [c for c in requested if c in set(all_columns)]
    active_sort, _latest_order_col = _resolve_view_sort_spec(sort_spec, all_columns)
    where = _combine_where(
        _normalize_view_sql_filter(normalized_sql, all_columns, schema),
        _duckdb_valid_wafer_where(all_columns),
    )
    df, _columns, _schema = duckdb_engine.query_files(
        files,
        where=where,
        select_cols=selected,
        limit=max_rows + 1,
        order_by=active_sort.get("column") or "",
        descending=_sort_descending(active_sort),
    )
    if df.height > max_rows:
        raise HTTPException(
            400,
            f"CSV 다운로드는 최대 {max_rows:,}행까지 허용됩니다. SQL 필터를 추가하거나 max_rows를 조정하세요.",
        )
    csv_bytes = _csv_bytes_checked(df, _csv_download_max_bytes(max_bytes, settings))
    return df, csv_bytes


@router.get("/view")
@_track_filebrowser_sql_execution("db_product")
def view_product(root: str = Query(...), product: str = Query(...),
                 sql: str = Query(""), rows: int = Query(LATEST_PREVIEW_ROWS),
                 cols: int = Query(20, ge=1, le=200),
                 select_cols: str = Query(""),
                 sort_column: str = Query(""),
                 sort_direction: str = Query("asc"),
                 sort_nulls: str = Query("last"),
                 agg_func: str = Query(""),
                 agg_column: str = Query(""),
                 agg_group_by: str = Query(""),
                 meta_only: bool = Query(True),
                 all_partitions: bool = Query(False),
                 engine: str = Query("auto"),
                 page: int = Query(0, ge=0),
                 page_size: int = Query(LATEST_PREVIEW_ROWS, ge=1, le=1000),
                 query_session: str = Query(""),
                 query_id: str = Query(""),
                 reuse_history_id: str = Query(""),
                 request: Request = None):
    # v8.4.3 OOM-aware: Hive-flat 도 lazy_read_source 로 scan. Polars 가 projection +
    # head 를 parquet reader 로 pushdown → 메모리 수 GB 제품도 안전.
    # v8.8.16: meta_only=True 는 스키마만 — 사이드바 제품 클릭 즉시 반응.
    # v8.8.33: SQL 에 date 필터가 있거나 all_partitions=True 면 파티션 pruning 생략.
    #          그 외에는 최근 30일 파티션만 스캔 → 30~60GB 대응.
    try:
        from core.utils import lazy_read_source
        from core.parquet_perf import has_date_filter
        # 활동 대시보드: 실제 데이터 조회만 기록 (meta_only 스키마 로드/페이지 넘김 제외).
        if not meta_only and page == 0:
            from core.audit import record as _fb_audit
            _fb_audit(request, "filebrowser:view",
                      detail=f"target={root}/{product} cols={select_cols or 'all'} sql={sql.strip()}",
                      tab="filebrowser")
        settings = _load_filebrowser_settings()
        # Startup prewarm invokes this function directly without a Request.
        # Interactive calls are keyed by user so a newer SQL can interrupt the
        # user's older scan, while background work stays in its own scope.
        me = current_user(request) if request is not None else {"username": "__background__"}
        query_key = "filebrowser:" + str(me.get("username") or "") + ":" + str(root) + ":" + str(product)
        query_session = str(query_session or "").strip()[:120] or str(uuid.uuid4())
        query_id = str(query_id or "").strip()[:120] or str(uuid.uuid4())
        sort_spec = _view_sort_query(sort_column, sort_direction, sort_nulls)
        aggregate_spec = _view_aggregate_query(agg_func, agg_column, agg_group_by)
        cols = _preview_cols_limit(cols or _settings_preview_max_columns(settings))
        full_scan = (
            all_partitions
            or bool(sql and sql.strip())
            or bool(select_cols and select_cols.strip())
            or bool(aggregate_spec)
            or has_date_filter(sql)
        )
        queue_needed = bool(full_scan and not meta_only and request is not None)
        # 최신 date 파티션만 읽는 기본 preview 는 500행까지 허용.
        # SQL/컬럼 선택/집계 조회는 기존 100행 preview 계약을 유지한다.
        preview_cap = LATEST_PREVIEW_ROWS if full_scan else DB_LATEST_PREVIEW_ROWS
        page, page_size, _offset = _preview_page_args(rows, page_size, cap=preview_cap)
        rows = page_size
        source_files: list[Path] = []
        source_size = 0
        source_info_loaded = False

        def _ensure_source_info() -> tuple[list[Path], int]:
            nonlocal source_files, source_size, source_info_loaded
            if not source_info_loaded:
                source_files = source_data_files(root=root, product=product)
                source_size = duckdb_engine.total_size(source_files)
                source_info_loaded = True
            return source_files, source_size

        def _compute_body() -> dict:
            local_rows = rows
            local_page_size = page_size
            if meta_only:
                fast_meta = _fast_product_meta_response(root, product, cols, settings=settings, page=page, page_size=local_page_size)
                if fast_meta is not None:
                    return _finalize_preview_response(fast_meta, settings)
            recent = None if full_scan else 30
            latest_preview = not full_scan and not meta_only
            if full_scan:
                _ensure_source_info()
            if latest_preview:
                local_rows = min(int(local_rows or LATEST_PREVIEW_ROWS), DB_LATEST_PREVIEW_ROWS)
                local_page_size = min(int(local_page_size or LATEST_PREVIEW_ROWS), DB_LATEST_PREVIEW_ROWS)
            if full_scan and not meta_only and not aggregate_spec and duckdb_engine.is_available() and "INLINE" not in str(root or "").upper():
                files = source_files
                if duckdb_engine.should_use_duckdb(files, engine=engine, sql=sql, select_cols=select_cols, size_bytes=source_size):
                    try:
                        return _finalize_preview_response(_run_view_duckdb(
                            files, sql, select_cols, local_rows,
                            page=page, page_size=local_page_size, preview_cols=cols,
                            latest_first=False, latest_preview=False,
                            settings=settings,
                            sort_spec=sort_spec,
                            query_key=query_key,
                        ), settings)
                    except Exception as e:
                        if duckdb_engine.is_interrupted_error(e):
                            raise
                        if str(engine or "").lower() in {"duckdb", "on", "true", "1"}:
                            raise HTTPException(400, f"DuckDB query failed: {e}")
                        logger.warning("duckdb product view fallback root=%s product=%s: %s", root, product, e)
            lf = lazy_read_source(
                root=root, product=product,
                recent_days=recent, max_files=None if full_scan else LATEST_PREVIEW_MAX_FILES,
                latest_only=latest_preview,
            )
            if lf is not None:
                out = _finalize_preview_response(_run_view_lazy(lf, sql, select_cols, local_rows, meta_only=meta_only,
                                                           page=page, page_size=local_page_size, preview_cols=cols,
                                                           latest_first=latest_preview, latest_preview=latest_preview,
                                                           source_size=source_size, settings=settings,
                                                           sort_spec=sort_spec,
                                                           aggregate_spec=aggregate_spec), settings)
                if latest_preview and page == 0 and not out.get("data"):
                    # 최신 date 파티션이 비어 있으면(빈 parquet, wafer 필터로 전부
                    # 탈락 등) 이전 날짜 파일들로 확대 스캔해 500행 샘플을 채운다.
                    # latest_first 정렬이 최근 날짜 행부터 보여준다.
                    fb_lf = lazy_read_source(
                        root=root, product=product,
                        recent_days=None, max_files=DB_PREVIEW_FALLBACK_MAX_FILES,
                        latest_only=False,
                    )
                    if fb_lf is not None:
                        fb_out = _finalize_preview_response(_run_view_lazy(
                            fb_lf, sql, select_cols, local_rows, meta_only=meta_only,
                            page=page, page_size=local_page_size, preview_cols=cols,
                            latest_first=True, latest_preview=True,
                            source_size=source_size, settings=settings,
                            sort_spec=sort_spec,
                            aggregate_spec=aggregate_spec), settings)
                        if fb_out.get("data"):
                            fb_out["latest_partition_empty"] = True
                            out = fb_out
                if latest_preview:
                    # 최신 파티션 preview 는 500행 상한을 응답에 그대로 알린다.
                    limit = max(int(out.get("preview_row_limit") or 0), int(local_page_size))
                    out["preview_row_limit"] = limit
                    out["preview_capped"] = bool(
                        out.get("truncated_cols") or int(out.get("showing") or 0) >= limit
                    )
                return out
            # Fallback — legacy DF 경로
            df = read_source(root=root, product=product)
            if meta_only:
                cols_all = list(df.columns)
                return _finalize_preview_response({
                    "total_rows": 0, "total_cols": len(cols_all),
                    "columns": cols_all[:10], "all_columns": cols_all,
                    "dtypes": {n: str(d) for n, d in df.schema.items()},
                    "showing_cols": [], "selected_cols": None,
                    "data": [], "showing": 0, "meta_only": True,
                    "page": page, "page_size": local_page_size, "has_more": False,
                    "row_count_unknown": True,
                }, settings)
            return _finalize_preview_response(_run_view(df, sql, select_cols, local_rows, page=page, page_size=local_page_size,
                                                  preview_cols=cols, latest_first=latest_preview, latest_preview=latest_preview,
                                                  sort_spec=sort_spec,
                                                  aggregate_spec=aggregate_spec), settings)

        def _preview_cache_context() -> tuple[dict, dict] | None:
            if _fbcache.is_enabled(settings):
                prod_dir = _resolve_product_dir_fast(root, product)
                source_stat = _stat_for_db_product_cached(prod_dir) if prod_dir is not None else None
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
                        "all_partitions": bool(all_partitions),
                        "settings_sig": _fbcache.settings_signature(settings),
                    }
                    return source_stat, key_payload
            return None

        def _cached_or_compute() -> dict:
            cache_context = _preview_cache_context()
            if cache_context is not None:
                source_stat, key_payload = cache_context
                return _fbcache.get_or_compute(
                    endpoint="view", source=source_stat,
                    key_payload=key_payload, compute=_compute_body,
                )
            return _compute_body()

        def _offload_or_local() -> dict:
            # Shared preview cache is checked before dispatch so a warm query never
            # pays the filesystem queue round trip to the development server.
            cache_context = _preview_cache_context()
            if cache_context is not None:
                cached = _fbcache.get_cached(
                    endpoint="view", source=cache_context[0], key_payload=cache_context[1])
                if cached is not None:
                    return cached

            files, total_bytes = _ensure_source_info()
            if not _should_offload_filebrowser_sql(
                source_size=total_bytes,
                all_partitions=bool(all_partitions),
                aggregate=bool(aggregate_spec),
            ):
                return _cached_or_compute()

            from core import worker_dispatch
            payload = {
                "root": str(root), "product": str(product), "sql": str(sql or ""),
                "rows": int(rows), "cols": int(cols), "select_cols": str(select_cols or ""),
                "sort_column": str(sort_column or ""), "sort_direction": str(sort_direction or "asc"),
                "sort_nulls": str(sort_nulls or "last"), "agg_func": str(agg_func or ""),
                "agg_column": str(agg_column or ""), "agg_group_by": str(agg_group_by or ""),
                "all_partitions": bool(all_partitions), "engine": str(engine or "auto"),
                "page": int(page), "page_size": int(page_size), "source_file_count": len(files),
                "source_size": int(total_bytes),
            }
            result = worker_dispatch.run_heavy(
                "filebrowser_sql_query",
                payload,
                _cached_or_compute,
                timeout_sec=_sql_queue.max_runtime_seconds(),
                label=f"FileBrowser SQL {root}/{product}",
                local_fallback=True,
                priority="interactive",
            )
            if isinstance(result, dict) and isinstance(result.get("response"), dict):
                response = result["response"]
                # API/worker mount paths may differ. Publish the returned result
                # again under the operating server's logical source signature so
                # the next identical request is served locally without a queue hop.
                if cache_context is not None:
                    _fbcache.put_cached(
                        endpoint="view", source=cache_context[0],
                        key_payload=cache_context[1], response=response,
                    )
                return response
            return result

        if not queue_needed:
            return _cached_or_compute()
        try:
            # Queue wraps cache lookup as well as cold execution. This keeps
            # every interactive SQL request cancellable while it is waiting.
            with _sql_queue.execute(
                username=str(me.get("username") or ""),
                session_id=query_session,
                query_id=query_id,
                query_key=query_key,
            ):
                return _offload_or_local()
        except _sql_queue.QueryQueueCanceled as exc:
            raise HTTPException(409, f"SQL query canceled: {exc}")
        except _sql_queue.QueryQueueExpired as exc:
            raise HTTPException(429, f"SQL query expired: {exc}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"view {root}/{product}: {e}", exc_info=True)
        raise HTTPException(400, f"Error: {str(e)}")
