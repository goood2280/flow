"""Parser and formatter for the shareable ChartBuilder definition language.

The format is intentionally small and line-oriented so engineers can paste it
in chat or a ticket without needing JSON.  SQL remains the same read-only
FileBrowser SQL fragment that ChartBuilder already accepts.

시간 창(``RECENT_DAYS`` / ``DATE_COLUMN``)도 Query 블록이 가진다.  WHERE 조각은
날짜 연산을 허용하지 않아 ``tkout_time >= 오늘 - 7일`` 을 SQL 로 쓸 수 없고,
무엇보다 **저장된 차트는 자기 시간 창을 지니고 다녀야** Template Report 가 그저
"저장된 대로 다시 실행"만 하면 된다.
"""
from __future__ import annotations

import re
from typing import Any


QUERY_HEADER_RE = re.compile(
    r"^\s*(?:\[([A-Za-z][\w-]*)\]|(q\d+))\s*:?(.*)$",
    re.IGNORECASE,
)
FIELD_RE = re.compile(
    r"^\s*(table|db|root|product|sql|query|select_cols?|columns?|reformatter|apply_reformatter|reformatter_items?|items|reformatter_agg|agg"
    r"|recent_days?|recent|days|date_column|date_col|time_column|time_col"
    r"|root_lots?|root_lot_ids?|wafers?|wafer_ids?"
    r"|key|reformatize_key|rh_key|et_key|expression"
    r"|derive|derived|derived_column|combine|filter|filters?)\s*[:=]\s*(.*)$",
    re.IGNORECASE,
)
CHART_HEADER_RE = re.compile(r"^\s*chart\s*:?(.*)$", re.IGNORECASE)
CHART_FIELD_RE = re.compile(
    r"^\s*(type|chart_type|x|x_col|y|y_col|color|color_col|trellis|trellis_col|color_rule|color_else"
    r"|highlight|show_legend|legend|width|height|size|title|x_label|y_label|trend_grain|aggregation"
    r"|map_y|map_scope|map_target|pie_basis|fit|point_size|marker_opacity|line_width|x_min|x_max|y_min|y_max"
    r"|y_scale|show_grid|legend_position|spec_low|spec_high|box_points|wafer_palette|wafer_mode"
    r"|wafer_spec_low|wafer_spec_high|wafer_low|wafer_center|wafer_high)\s*[:=]\s*(.*)$",
    re.IGNORECASE,
)
MAX_ROWS_RE = re.compile(r"^\s*max[_\s-]*rows\s*[:=]\s*(\d+)\s*$", re.IGNORECASE)
JOIN_RE = re.compile(
    r"^\s*(?:join\s+)?([A-Za-z][\w-]*)\s+"
    r"(left|inner|full|semi|anti)\s+(?:join\s+)?([A-Za-z][\w-]*)\s+on\s+(.+?)\s*$",
    re.IGNORECASE,
)

FIELD_ALIASES = {
    "table": "root",
    "db": "root",
    "root": "root",
    "product": "product",
    "sql": "sql",
    "query": "sql",
    "select": "select_cols",
    "select_col": "select_cols",
    "select_cols": "select_cols",
    "column": "select_cols",
    "columns": "select_cols",
    "reformatter": "apply_reformatter",
    "apply_reformatter": "apply_reformatter",
    "item": "reformatter_items",
    "items": "reformatter_items",
    "reformatter_item": "reformatter_items",
    "reformatter_items": "reformatter_items",
    "reformatter_agg": "reformatter_agg",
    "agg": "reformatter_agg",
    "key": "reformatize_key",
    "reformatize_key": "reformatize_key",
    "rh_key": "reformatize_key",
    "et_key": "reformatize_key",
    "expression": "reformatize_key",
    # 시간 창은 저장 차트의 기본값이다. Template Report의 명시적 실행 컨텍스트만
    # 원본 Template을 바꾸지 않고 이번 실행에 한해 이를 덮어쓸 수 있다.
    "recent": "runtime_recent_days",
    "recent_day": "runtime_recent_days",
    "recent_days": "runtime_recent_days",
    "days": "runtime_recent_days",
    "date_col": "runtime_date_column",
    "date_column": "runtime_date_column",
    "time_col": "runtime_date_column",
    "time_column": "runtime_date_column",
    "root_lot": "runtime_root_lot_ids",
    "root_lots": "runtime_root_lot_ids",
    "root_lot_id": "runtime_root_lot_ids",
    "root_lot_ids": "runtime_root_lot_ids",
    "wafer": "runtime_wafer_ids",
    "wafers": "runtime_wafer_ids",
    "wafer_id": "runtime_wafer_ids",
    "wafer_ids": "runtime_wafer_ids",
    "derive": "derived_columns",
    "derived": "derived_columns",
    "derived_column": "derived_columns",
    "combine": "derived_columns",
    "filter": "runtime_filters",
    "filters": "runtime_filters",
}
DEFAULT_DATE_COLUMN = "tkout_time"
MAX_RECENT_DAYS = 3650
RECENT_DAYS_RE = re.compile(r"^(\d+)\s*(?:일|days?)?$", re.IGNORECASE)
COLOR_RULE_IDENTIFIER = r"(?:`[^`]+`|[A-Za-z_][A-Za-z0-9_]*(?:__[A-Za-z0-9_]+)*)"
COLOR_RULE_WITHIN_RE = re.compile(
    rf"^{COLOR_RULE_IDENTIFIER}\s+WITHIN\s+(\d+)\s+DAYS?$", re.IGNORECASE
)
COLOR_RULE_EQUAL_RE = re.compile(
    rf"^{COLOR_RULE_IDENTIFIER}\s*(?:=|==)\s*(?:'[^']*'|\"[^\"]*\"|.+)$"
)
LINKED_COLOR_CONDITION_RE = re.compile(
    r"^`?(root_lot_id|wafer_id)`?\s*(?:=|==)\s*['\"]([^'\"]+)['\"]$", re.IGNORECASE
)
JOIN_HOWS = {"left", "inner", "full", "semi", "anti"}
CHART_TYPES = {"scatter", "line", "box", "bar", "bar_horizontal", "pie", "donut", "radius", "wafer_map"}
MAX_DERIVED_COLUMNS = 20
MAX_RUNTIME_FILTERS = 50
FILTER_OPERATORS = {"in", "not_in", "equals", "not_equals", "contains", "not_contains", "is_blank", "not_blank"}


class ChartBuilderDefinitionError(ValueError):
    """Raised when a definition cannot be converted into a safe run request."""


def _normalize_reformatter_agg(value: Any) -> str:
    normalized = str(value or "").strip().casefold()
    return "" if normalized in {"", "raw", "shot raw", "shot_raw"} else normalized


def _clean_query_id(value: Any, fallback: str = "") -> str:
    text = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "").strip())[:40]
    return (text or fallback).casefold()


def _field_name(value: str) -> str:
    return FIELD_ALIASES.get(str(value or "").strip().casefold().replace("-", "_"), "")


def _pipe_options(value: str) -> tuple[str, dict[str, str]]:
    parts = [part.strip() for part in str(value or "").split("|")]
    head = parts[0] if parts else ""
    options: dict[str, str] = {}
    for part in parts[1:]:
        key, separator, item = part.partition("=")
        if separator:
            options[key.strip().casefold().replace("-", "_")] = item.strip()
    return head, options


def _parse_derived_column(value: str) -> dict[str, Any]:
    cleaned = str(value or "").strip()
    head, options = _pipe_options(cleaned)
    # 복사해 쓰기 쉬운 수식 단축형도 허용한다:
    #   DERIVE = lot_wf = root_lot_id + "_" + wafer_id
    if not options and "=" in head:
        name, expression = [part.strip() for part in head.split("=", 1)]
        tokens = [part.strip() for part in expression.split("+") if part.strip()]
        columns: list[str] = []
        separator = "_"
        for token in tokens:
            quoted = re.fullmatch(r"(['\"])(.*)\1", token)
            if quoted:
                separator = quoted.group(2)
            else:
                columns.append(token.strip("` "))
    else:
        name = options.get("name") or head
        columns = [part.strip().strip("`") for part in (options.get("columns") or options.get("cols") or "").split(",") if part.strip()]
        separator = options.get("separator", options.get("sep", "_"))
    name = str(name or "").strip()[:80]
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ChartBuilderDefinitionError("DERIVE 이름은 영문/숫자/밑줄 열 이름이어야 합니다.")
    if not columns or len(columns) > 12 or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", column) for column in columns):
        raise ChartBuilderDefinitionError("DERIVE columns는 쉼표로 구분한 1~12개 열 이름이어야 합니다.")
    separator = str(separator if separator is not None else "_").replace("\\t", "\t")[:8]
    return {"name": name, "columns": columns, "separator": separator}


def _filter_operator(value: str) -> str:
    normalized = re.sub(r"[\s-]+", "_", str(value or "in").strip().casefold())
    aliases = {"=": "equals", "==": "equals", "eq": "equals", "notin": "not_in", "!=": "not_equals", "ne": "not_equals", "blank": "is_blank", "is_not_blank": "not_blank"}
    return aliases.get(normalized, normalized)


def _parse_runtime_filter(value: str) -> dict[str, Any]:
    head, options = _pipe_options(value)
    column = str(options.get("column") or options.get("col") or head or "").strip().strip("`")[:120]
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", column):
        raise ChartBuilderDefinitionError("FILTER column은 올바른 열 이름이어야 합니다.")
    operator = _filter_operator(options.get("operator") or options.get("op") or "in")
    if operator not in FILTER_OPERATORS:
        raise ChartBuilderDefinitionError(f"지원하지 않는 FILTER operator입니다: {operator}")
    raw_values = options.get("values", options.get("value", ""))
    values: list[str] = []
    seen: set[str] = set()
    for raw in re.split(r"[,\n]+", raw_values):
        item = raw.strip()[:160]
        key = item.casefold()
        if item and key not in seen:
            seen.add(key)
            values.append(item)
        if len(values) >= 200:
            break
    if operator not in {"is_blank", "not_blank"} and not values:
        raise ChartBuilderDefinitionError("FILTER values를 하나 이상 입력해 주세요.")
    return {"column": column, "operator": operator, "values": values}


def _assign_field(source: dict[str, Any], name: str, value: str, *, append_sql: bool = False) -> None:
    field = _field_name(name)
    if not field:
        raise ChartBuilderDefinitionError(f"지원하지 않는 Query 항목입니다: {name}")
    cleaned = str(value or "").strip()
    if field == "derived_columns":
        rows = source.setdefault(field, [])
        if len(rows) >= MAX_DERIVED_COLUMNS:
            raise ChartBuilderDefinitionError(f"DERIVE는 Query마다 최대 {MAX_DERIVED_COLUMNS}개까지 사용할 수 있습니다.")
        rows.append(_parse_derived_column(cleaned))
        return
    if field == "runtime_filters":
        rows = source.setdefault(field, [])
        if len(rows) >= MAX_RUNTIME_FILTERS:
            raise ChartBuilderDefinitionError(f"FILTER는 Query마다 최대 {MAX_RUNTIME_FILTERS}개까지 사용할 수 있습니다.")
        rows.append(_parse_runtime_filter(cleaned))
        return
    if field == "apply_reformatter":
        source[field] = cleaned.casefold() in {"1", "true", "yes", "y", "on", "사용", "적용"}
        return
    if field == "reformatter_agg":
        normalized = _normalize_reformatter_agg(cleaned)
        if not normalized:
            source[field] = ""
            return
        if normalized not in {"max", "min", "median", "avg", "std", "p90", "p10"}:
            raise ChartBuilderDefinitionError(f"지원하지 않는 ET 집계 방식입니다: {cleaned}")
        source[field] = normalized
        return
    if field == "runtime_recent_days":
        match = RECENT_DAYS_RE.match(cleaned)
        if not match:
            raise ChartBuilderDefinitionError("RECENT_DAYS는 날짜 수여야 합니다. 예: RECENT_DAYS = 7")
        days = int(match.group(1))
        if days > MAX_RECENT_DAYS:
            raise ChartBuilderDefinitionError(f"RECENT_DAYS는 1~{MAX_RECENT_DAYS}일 사이여야 합니다.")
        source[field] = days
        return
    if field in {"runtime_root_lot_ids", "runtime_wafer_ids"}:
        values = []
        seen = set()
        for item in re.split(r"[,\n]+", cleaned):
            item = item.strip()
            key = item.casefold()
            if item and key not in seen:
                seen.add(key)
                values.append(item[:160])
        if len(values) > 200:
            raise ChartBuilderDefinitionError("ROOT_LOTS/WAFERS는 각각 최대 200개까지 지정할 수 있습니다.")
        source[field] = values
        return
    if field == "reformatize_key":
        _apply_reformatize_key_or_expr_to_source(source, cleaned)
        return
    if field == "sql" and append_sql and source.get("sql"):
        source["sql"] = f"{source['sql']}\n{cleaned}".strip()
    else:
        source[field] = cleaned


def _parse_inline_fields(rest: str, source: dict[str, Any], line_number: int) -> None:
    chunks = [chunk.strip() for chunk in str(rest or "").split("|") if chunk.strip()]
    if not chunks:
        return
    for chunk in chunks:
        match = FIELD_RE.match(chunk)
        if not match:
            raise ChartBuilderDefinitionError(
                f"{line_number}행 Query 한 줄 형식은 'Q1 | TABLE=... | PRODUCT=... | SQL=...'처럼 작성해 주세요."
            )
        _assign_field(source, match.group(1), match.group(2))


def _strip_source_prefix(value: str, source_id: str) -> str:
    token = str(value or "").strip()
    prefix = f"{source_id}."
    return token[len(prefix):].strip() if token.casefold().startswith(prefix.casefold()) else token


def _parse_join_keys(raw: str, left: str, right: str) -> tuple[str, str]:
    expression = str(raw or "").strip()
    if not expression:
        raise ChartBuilderDefinitionError("JOIN ON 뒤에 연결 열을 입력해 주세요.")
    if "=" in expression:
        left_raw, right_raw = expression.split("=", 1)
    else:
        left_raw = right_raw = expression
    left_keys = [_strip_source_prefix(item, left) for item in left_raw.split(",") if item.strip()]
    right_keys = [_strip_source_prefix(item, right) for item in right_raw.split(",") if item.strip()]
    if not left_keys or len(left_keys) != len(right_keys):
        raise ChartBuilderDefinitionError("JOIN 양쪽 열 개수가 같아야 합니다.")
    return ", ".join(left_keys), ", ".join(right_keys)


def _lookup_reformatize_history(history_id: str) -> dict | None:
    try:
        from routers.reformatize import HISTORY_FILE
        from core.utils import jsonl_read
    except Exception:
        return None

    clean_id = str(history_id or "").strip()
    if not clean_id:
        return None
    m = re.search(r"(RH-[0-9A-Za-z_-]{1,32})", clean_id, re.I)
    target_key = m.group(1).upper() if m else clean_id.upper()
    seq_match = re.match(r"^#?(\d+)$", clean_id)
    target_seq = int(seq_match.group(1)) if seq_match else None

    entries = jsonl_read(
        HISTORY_FILE,
        limit=1000,
        filter_fn=lambda e: (
            isinstance(e, dict)
            and e.get("event") == "history"
            and (
                (target_key and str(e.get("history_id") or "").upper() == target_key)
                or (target_seq is not None and e.get("seq") == target_seq)
            )
        ),
    )
    return entries[-1] if entries else None


def _parse_reformatize_expression_dict(raw: str) -> dict[str, Any]:
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    product = ""
    items: list[str] = []
    filters: dict[str, Any] = {}
    agg = ""
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("Q1") or stripped.startswith("TABLE") or stripped.startswith("REFORMATTER"):
            continue
        if stripped.startswith("#"):
            stripped = stripped[1:].strip()
        m = re.match(r"^([a-zA-Z0-9_\uAC00-\uD7A3\s]+?)\s*[:=]\s*(.+)$", stripped)
        if not m:
            continue
        k = m.group(1).strip().lower()
        v = m.group(2).strip().strip("'\"")
        if k in ("product", "제품", "prod"):
            product = v
        elif k in ("items", "item", "항목", "index"):
            if v.upper() not in ("ALL", "전체", "NONE", "미선택", ""):
                items = [s.strip().strip("'\"") for s in v.split(",") if s.strip()]
        elif k in ("agg", "집계", "aggregation"):
            agg = v
        elif k in ("days", "recent_days", "day", "최근", "일수", "최근일수"):
            if v.isdigit() and int(v) > 0:
                filters["days"] = int(v)
        elif k in ("date_from", "from", "start_date", "시작일"):
            filters["date_from"] = v
        elif k in ("date_to", "to", "end_date", "종료일"):
            filters["date_to"] = v
        elif k in ("root_lots", "root_lot_id", "lot_filter", "lots", "lot", "랏"):
            filters["lot_filter"] = v
        elif k in ("wafers", "wafer_id", "wafer_filter", "wafer", "웨이퍼"):
            filters["wafer_filter"] = v
        elif k in ("step_filter", "step_id", "steps", "step", "스텝"):
            filters["step_filter"] = v
        elif k in ("step_seq_filter", "step_seq", "seq"):
            filters["step_seq_filter"] = v
        elif k in ("site_cnt_filter", "site_cnt", "total_site_cnt", "site", "sites"):
            filters["site_cnt_filter"] = v
        elif k in ("point_cnt_filter", "point_cnt", "shot_count", "point", "points"):
            filters["point_cnt_filter"] = v
    return {"product": product, "items": items, "filters": filters, "agg": agg}


def _apply_reformatize_entry_to_source(source: dict[str, Any], entry: dict[str, Any]) -> None:
    source["root"] = "et"
    if entry.get("product"):
        source["product"] = str(entry["product"]).strip()
    source["apply_reformatter"] = True
    source["reformatter_agg"] = _normalize_reformatter_agg(entry.get("agg"))
    items = entry.get("items") or []
    if items:
        source["reformatter_items"] = ", ".join(str(it) for it in items if str(it).strip())
    filters = entry.get("filters") or {}
    days = int(filters.get("days") or 0)
    if days:
        source["runtime_recent_days"] = days
        source["runtime_date_column"] = "tkout_time"
    if filters.get("lot_filter"):
        source["runtime_root_lot_ids"] = [s.strip() for s in str(filters["lot_filter"]).split(",") if s.strip()]
    if filters.get("wafer_filter"):
        source["runtime_wafer_ids"] = [s.strip() for s in str(filters["wafer_filter"]).split(",") if s.strip()]

    rf = source.setdefault("runtime_filters", [])
    for col_key, filter_col in [
        ("step_filter", "step_id"),
        ("step_seq_filter", "step_seq"),
        ("site_cnt_filter", "total_site_cnt"),
        ("point_cnt_filter", "shot_count"),
    ]:
        val = str(filters.get(col_key) or "").strip()
        if val:
            rf.append({"column": filter_col, "operator": "in", "values": [s.strip() for s in val.split(",") if s.strip()]})

    selected_items = [str(item).strip() for item in items if str(item).strip()]
    if selected_items:
        fixed = (["root_lot_id", "wafer_id", "step_id", "pgm", "shot_count"]
                 if source["reformatter_agg"] else ["root_lot_id", "wafer_id", "tkout_time"])
        sql_parts = [f"SELECT {', '.join([*fixed, *selected_items])}"]
    else:
        sql_parts = ["SELECT *"]
    wheres = []
    if filters.get("date_from"):
        wheres.append(f"tkout_time >= '{filters['date_from']}'")
    if filters.get("date_to"):
        wheres.append(f"tkout_time <= '{filters['date_to']}'")
    if wheres:
        sql_parts.append(f"WHERE {' AND '.join(wheres)}")
    source["sql"] = " ".join(sql_parts)


def _apply_reformatize_key_or_expr_to_source(source: dict[str, Any], key_or_expr: str) -> None:
    cleaned = str(key_or_expr or "").strip()
    if not cleaned:
        return
    key_match = re.search(r"(RH-[0-9A-Za-z_-]{1,32})", cleaned, re.I)
    entry = None
    if key_match or re.match(r"^#?\d+$", cleaned):
        entry = _lookup_reformatize_history(cleaned)
    if entry:
        _apply_reformatize_entry_to_source(source, entry)
    else:
        parsed = _parse_reformatize_expression_dict(cleaned)
        if parsed.get("product") or parsed.get("items"):
            _apply_reformatize_entry_to_source(source, parsed)


def reformatize_key_to_chart_builder_code(history_id: str) -> str | None:
    entry = _lookup_reformatize_history(history_id)
    if not entry:
        return None
    # Structured history fields are authoritative. Regenerating also upgrades
    # entries saved before the ChartBuilder projection/AGG contract was fixed.
    from routers.reformatize import _format_reformatize_expression
    expr = _format_reformatize_expression(
        entry.get("product") or "",
        entry.get("items") or [],
        entry.get("filters") or {},
        entry.get("agg") or "",
    )
    if "CHART" not in expr.upper():
        items = entry.get("items") or []
        first_item = items[0] if items else ""
        days = int((entry.get("filters") or {}).get("days") or 0)
        chart_lines = [
            "",
            "CHART",
            "TYPE = scatter",
            f"X = {'pgm' if _normalize_reformatter_agg(entry.get('agg')) else 'tkout_time'}",
            "COLOR = custom",
        ]
        if first_item:
            chart_lines.insert(4, f"Y = {first_item}")
        if days > 0:
            color_days = min(7, days)
            chart_lines.append(f"COLOR_RULE = tkout_time WITHIN {color_days} DAYS THEN #2563eb")
            chart_lines.append("COLOR_ELSE = #cbd5e1")
        else:
            chart_lines.append("COLOR_ELSE = #2563eb")
        chart_lines.extend([
            "WIDTH = 1200",
            "HEIGHT = 650",
            "",
            "MAX_ROWS = 10000",
        ])
        expr = expr.strip() + "\n" + "\n".join(chart_lines)
    return expr


def is_reformatize_expression(raw: str) -> bool:
    stripped = raw.strip()
    if re.search(r"^\s*(?:Q\d+|\[[^\]\n]+\]|CHART\b)", stripped, re.I):
        return False
    if re.search(r"^\s*TABLE\s*[:=]", stripped, re.M | re.I):
        return False
    has_prod = bool(re.search(r"^\s*(?:PRODUCT|제품)\s*[:=]", stripped, re.M | re.I))
    has_other = bool(re.search(r"^\s*(?:ITEMS?|항목|INDEX|days?|date_from|root_lot_id|step_id|AGG)\s*[:=]", stripped, re.M | re.I))
    return has_prod and has_other


def reformatize_expression_to_chart_builder_code(raw: str) -> str:
    parsed = _parse_reformatize_expression_dict(raw)
    from routers.reformatize import _format_reformatize_expression
    expr = _format_reformatize_expression(
        parsed["product"],
        parsed["items"],
        parsed["filters"],
        parsed["agg"],
    )
    items = parsed.get("items") or []
    first_item = items[0] if items else ""
    days = int(parsed.get("filters", {}).get("days") or 0)
    chart_lines = [
        "",
        "CHART",
        "TYPE = scatter",
        f"X = {'pgm' if _normalize_reformatter_agg(parsed.get('agg')) else 'tkout_time'}",
        "COLOR = custom",
    ]
    if first_item:
        chart_lines.insert(4, f"Y = {first_item}")
    if days > 0:
        color_days = min(7, days)
        chart_lines.append(f"COLOR_RULE = tkout_time WITHIN {color_days} DAYS THEN #2563eb")
        chart_lines.append("COLOR_ELSE = #cbd5e1")
    else:
        chart_lines.append("COLOR_ELSE = #2563eb")
    chart_lines.extend([
        "WIDTH = 1200",
        "HEIGHT = 650",
        "",
        "MAX_ROWS = 10000",
    ])
    return expr.strip() + "\n" + "\n".join(chart_lines)


def parse_chart_builder_definition(code: str) -> dict[str, Any]:
    """Parse engineer-friendly text into ChartBuilder sources and joins."""
    raw = str(code or "").replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        raise ChartBuilderDefinitionError("전체 코드를 입력해 주세요.")

    key_match = re.fullmatch(r"#?\s*(RH-[0-9A-Za-z_-]{1,32})\s*", raw, flags=re.IGNORECASE)
    if key_match:
        rh_key = key_match.group(1).upper()
        expanded_code = reformatize_key_to_chart_builder_code(rh_key)
        if not expanded_code:
            raise ChartBuilderDefinitionError(f"ET 다운로드 고유키를 찾을 수 없습니다: {rh_key}")
        raw = expanded_code
    elif is_reformatize_expression(raw):
        raw = reformatize_expression_to_chart_builder_code(raw)

    sources: list[dict[str, Any]] = []
    joins: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    chart: dict[str, Any] = {}
    in_chart = False
    active_field = ""
    max_rows = 10000

    for line_number, raw_line in enumerate(raw.split("\n"), start=1):
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            if current is not None and active_field == "sql" and current.get("sql"):
                current["sql"] += "\n"
            continue
        if stripped.startswith("#"):
            # ET 다운로드가 예전에 생성한 유일한 의미 있는 주석 형태다.
            # 일반 주석은 계속 무시하되 구형 코드를 붙여넣으면 집계를 복원한다.
            legacy_agg = re.fullmatch(r"#\s*AGG\s*[:=]\s*(.+)", stripped, re.IGNORECASE)
            if legacy_agg and current is not None:
                _assign_field(current, "agg", legacy_agg.group(1))
            continue

        max_match = MAX_ROWS_RE.match(stripped)
        if max_match:
            max_rows = int(max_match.group(1))
            active_field = ""
            continue

        join_match = JOIN_RE.match(stripped)
        if join_match:
            left = _clean_query_id(join_match.group(1))
            how = join_match.group(2).casefold()
            right = _clean_query_id(join_match.group(3))
            left_on, right_on = _parse_join_keys(join_match.group(4), left, right)
            joins.append({"left": left, "right": right, "left_on": left_on, "right_on": right_on, "how": how})
            current = None
            in_chart = False
            active_field = ""
            continue

        chart_match = CHART_HEADER_RE.match(stripped)
        if chart_match:
            current = None
            in_chart = True
            active_field = ""
            rest = chart_match.group(1).strip()
            if rest:
                if rest.startswith("|"):
                    rest = rest[1:].strip()
                for chunk in [part.strip() for part in rest.split("|") if part.strip()]:
                    field_match = CHART_FIELD_RE.match(chunk)
                    if not field_match:
                        raise ChartBuilderDefinitionError(
                            f"{line_number}행 CHART 한 줄 형식은 'CHART | TYPE=scatter | X=... | Y=...'처럼 작성해 주세요."
                        )
                    _assign_chart_field(chart, field_match.group(1), field_match.group(2))
            continue

        query_match = QUERY_HEADER_RE.match(stripped)
        if query_match:
            query_id = _clean_query_id(query_match.group(1) or query_match.group(2), f"q{len(sources) + 1}")
            current = {
                "id": query_id, "root": "", "product": "", "sql": "", "select_cols": "",
                "apply_reformatter": False, "reformatter_items": "", "reformatter_agg": "",
                "runtime_recent_days": 0, "runtime_date_column": "",
                "runtime_root_lot_ids": [], "runtime_wafer_ids": [], "runtime_lot_wafer_pairs": [],
                "derived_columns": [], "runtime_filters": [],
            }
            sources.append(current)
            in_chart = False
            active_field = ""
            rest = query_match.group(3).strip()
            if rest:
                if rest.startswith("|"):
                    rest = rest[1:].strip()
                _parse_inline_fields(rest, current, line_number)
            continue

        field_match = FIELD_RE.match(stripped)
        if field_match:
            if current is None:
                raise ChartBuilderDefinitionError(f"{line_number}행 항목보다 먼저 Q1 같은 Query 이름이 필요합니다.")
            active_field = _field_name(field_match.group(1))
            _assign_field(current, field_match.group(1), field_match.group(2))
            continue

        chart_field_match = CHART_FIELD_RE.match(stripped)
        if chart_field_match and in_chart:
            _assign_chart_field(chart, chart_field_match.group(1), chart_field_match.group(2))
            continue

        if current is not None and active_field == "sql":
            current["sql"] = f"{current.get('sql', '')}\n{stripped}".strip()
            continue

        raise ChartBuilderDefinitionError(f"{line_number}행을 해석하지 못했습니다: {stripped[:120]}")

    if not sources:
        raise ChartBuilderDefinitionError("Q1 이상의 Query 블록이 필요합니다.")
    if len(sources) > 10:
        raise ChartBuilderDefinitionError("Query는 최대 10개까지 사용할 수 있습니다.")
    ids = [source["id"] for source in sources]
    if len(set(ids)) != len(ids):
        raise ChartBuilderDefinitionError("Query 이름이 중복되었습니다.")
    for source in sources:
        missing = [label for key, label in (("root", "TABLE"), ("product", "PRODUCT")) if not source.get(key)]
        if missing:
            raise ChartBuilderDefinitionError(f"{source['id']}: {', '.join(missing)} 값이 필요합니다.")
        source["sql"] = source.get("sql", "").strip()
        if source.get("apply_reformatter") and "ET" not in str(source.get("root") or "").upper():
            raise ChartBuilderDefinitionError(f"{source['id']}: REFORMATTER는 ET DB Query에서만 사용할 수 있습니다.")
        # DATE_COLUMN 만 있으면 아무 조건도 걸리지 않으므로 조용히 흘려보내지 않고 잡는다.
        recent_days = int(source.get("runtime_recent_days") or 0)
        date_column = str(source.get("runtime_date_column") or "").strip()
        if date_column and not recent_days:
            raise ChartBuilderDefinitionError(
                f"{source['id']}: DATE_COLUMN은 RECENT_DAYS와 함께 써야 시간 조건이 걸립니다."
            )
        source["runtime_recent_days"] = recent_days
        source["runtime_date_column"] = (date_column or DEFAULT_DATE_COLUMN) if recent_days else ""
    known = set(ids)
    for join in joins:
        if join["left"] not in known or join["right"] not in known:
            raise ChartBuilderDefinitionError(f"JOIN Query 이름을 찾지 못했습니다: {join['left']} → {join['right']}")
        if join["how"] not in JOIN_HOWS:
            raise ChartBuilderDefinitionError(f"지원하지 않는 JOIN 방식입니다: {join['how']}")
    if len(sources) > 1 and not joins:
        raise ChartBuilderDefinitionError("Query가 여러 개이면 JOIN 문장을 하나 이상 작성해 주세요.")
    if max_rows < 1 or max_rows > 10000:
        raise ChartBuilderDefinitionError("MAX_ROWS는 1~10000 사이여야 합니다.")

    _validate_chart(chart)
    linked_pairs = linked_chart_color_pairs(chart)
    if linked_pairs:
        linked_roots = list(dict.fromkeys(pair["root_lot_id"] for pair in linked_pairs))
        linked_wafers = list(dict.fromkeys(pair["wafer_id"] for pair in linked_pairs))
        for source in sources:
            source["runtime_lot_wafer_pairs"] = [dict(pair) for pair in linked_pairs]
            source["runtime_root_lot_ids"] = linked_roots
            source["runtime_wafer_ids"] = linked_wafers
    payload = {"sources": sources, "joins": joins, "max_rows": max_rows, "chart": chart}
    payload["canonical_code"] = format_chart_builder_definition(**payload)
    return payload


def _source_dict(source: Any) -> dict[str, Any]:
    if isinstance(source, dict):
        return source
    if hasattr(source, "model_dump"):
        return source.model_dump()
    if hasattr(source, "dict"):
        return source.dict()
    return {
        key: getattr(source, key, "")
        for key in (
            "id", "root", "product", "sql", "select_cols", "apply_reformatter", "reformatter_items", "reformatter_agg",
            "runtime_recent_days", "runtime_date_column",
            "runtime_root_lot_ids", "runtime_wafer_ids", "runtime_lot_wafer_pairs",
            "derived_columns", "runtime_filters",
        )
    }


def _assign_chart_field(chart: dict[str, Any], name: str, value: str) -> None:
    key = str(name or "").strip().casefold()
    aliases = {
        "chart_type": "type", "x_col": "x", "y_col": "y",
        "color_col": "color", "trellis_col": "trellis", "legend": "show_legend",
    }
    key = aliases.get(key, key)
    cleaned = str(value or "").strip()
    if key == "color_rule":
        chart.setdefault("color_rules", []).append(cleaned)
    elif key == "size":
        match = re.fullmatch(r"(\d+)\s*[x×,]\s*(\d+)", cleaned, re.IGNORECASE)
        if not match:
            raise ChartBuilderDefinitionError("CHART SIZE는 WIDTHxHEIGHT 형식이어야 합니다. 예: 1200x650")
        chart["width"] = int(match.group(1))
        chart["height"] = int(match.group(2))
    elif key in {"width", "height", "point_size"}:
        if not cleaned.isdigit():
            raise ChartBuilderDefinitionError(f"CHART {key.upper()}는 픽셀 숫자여야 합니다.")
        chart[key] = int(cleaned)
    elif key in {"marker_opacity", "line_width", "x_min", "x_max", "y_min", "y_max", "wafer_spec_low", "wafer_spec_high", "wafer_low", "wafer_center", "wafer_high"}:
        try:
            chart[key] = float(cleaned)
        except ValueError as exc:
            raise ChartBuilderDefinitionError(f"CHART {key.upper()}는 숫자여야 합니다.") from exc
    elif key in {"highlight", "show_legend", "show_grid"}:
        chart[key] = cleaned.casefold() not in {"0", "false", "no", "n", "off", "사용안함"}
    else:
        chart[key] = cleaned


def _validate_chart(chart: dict[str, Any]) -> None:
    if not chart:
        return
    chart_type = str(chart.get("type") or "").casefold()
    if chart_type and chart_type not in CHART_TYPES:
        raise ChartBuilderDefinitionError(f"지원하지 않는 CHART TYPE입니다: {chart_type}")
    color = str(chart.get("color") or "").casefold()
    rules = chart.get("color_rules") if isinstance(chart.get("color_rules"), list) else []
    if rules and color not in {"custom", "__custom__"}:
        chart["color"] = "custom"
    for rule in rules:
        raw_rule = str(rule or "").strip()
        split = re.match(r"^(.*?)\s+then\s+(.+)$", raw_rule, re.IGNORECASE)
        if not split:
            raise ChartBuilderDefinitionError(f"COLOR_RULE에는 THEN 색상이 필요합니다: {rule}")
        for condition in re.split(r"\s+AND\s+", split.group(1), flags=re.IGNORECASE):
            within = COLOR_RULE_WITHIN_RE.fullmatch(condition.strip())
            if within:
                days = int(within.group(1))
                if 1 <= days <= MAX_RECENT_DAYS:
                    continue
                raise ChartBuilderDefinitionError(
                    f"COLOR_RULE WITHIN 날짜는 1~{MAX_RECENT_DAYS}일 사이여야 합니다: {condition.strip()}"
                )
            if COLOR_RULE_EQUAL_RE.fullmatch(condition.strip()):
                continue
            raise ChartBuilderDefinitionError(
                "COLOR_RULE 조건은 열 = '값' 또는 시간열 WITHIN 7 DAYS 형식이어야 합니다: "
                f"{condition.strip()}"
            )
    width = int(chart.get("width") or 0)
    height = int(chart.get("height") or 0)
    if width and not 320 <= width <= 2400:
        raise ChartBuilderDefinitionError("CHART WIDTH는 320~2400px 사이여야 합니다.")
    if height and not 240 <= height <= 1600:
        raise ChartBuilderDefinitionError("CHART HEIGHT는 240~1600px 사이여야 합니다.")
    point_size = int(chart.get("point_size") or 0)
    if point_size and not 2 <= point_size <= 30:
        raise ChartBuilderDefinitionError("CHART POINT_SIZE는 2~30 사이여야 합니다.")
    marker_opacity = float(chart.get("marker_opacity") or 0)
    if marker_opacity and not 0.05 <= marker_opacity <= 1:
        raise ChartBuilderDefinitionError("CHART MARKER_OPACITY는 0.05~1 사이여야 합니다.")
    line_width = float(chart.get("line_width") or 0)
    if line_width and not 0.5 <= line_width <= 8:
        raise ChartBuilderDefinitionError("CHART LINE_WIDTH는 0.5~8 사이여야 합니다.")
    for axis in ("x", "y"):
        lower = chart.get(f"{axis}_min")
        upper = chart.get(f"{axis}_max")
        if lower is not None and upper is not None and float(lower) >= float(upper):
            raise ChartBuilderDefinitionError(
                f"CHART {axis.upper()}_MIN은 {axis.upper()}_MAX보다 작아야 합니다."
            )
    wafer_spec_low = chart.get("wafer_spec_low")
    wafer_spec_high = chart.get("wafer_spec_high")
    if wafer_spec_low is not None and wafer_spec_high is not None and float(wafer_spec_low) >= float(wafer_spec_high):
        raise ChartBuilderDefinitionError("CHART WAFER_SPEC_LOW는 WAFER_SPEC_HIGH보다 작아야 합니다.")
    if str(chart.get("wafer_mode") or "").casefold() == "spec_out" and wafer_spec_low is None and wafer_spec_high is None:
        raise ChartBuilderDefinitionError("CHART Spec Out WF MAP에는 WAFER_SPEC_LOW 또는 WAFER_SPEC_HIGH가 필요합니다.")
    enums = {
        "trend_grain": {"shot", "wafer", "daily", "weekly"},
        "aggregation": {"raw", "avg", "median", "p10", "p90", "min", "max", "count", "sum"},
        "map_scope": {"root_wafer", "root_lot", "trellis_wafer", "trellis_root_wafer"},
        "pie_basis": {"count", "sum"},
        "fit": {"none", "linear", "cubic"},
        "y_scale": {"linear", "log"},
        "legend_position": {"bottom", "top", "right", "inside"},
        "box_points": {"outliers", "all", "none"},
        "wafer_palette": {"blue_gray_red", "red_gray_blue", "viridis", "gray"},
        "wafer_mode": {"value", "spec_out"},
    }
    for key, allowed in enums.items():
        value = str(chart.get(key) or "").casefold()
        if value and value not in allowed:
            raise ChartBuilderDefinitionError(f"지원하지 않는 CHART {key.upper()}입니다: {value}")
    if str(chart.get("y_scale") or "").casefold() == "log":
        for key in ("y_min", "y_max"):
            if chart.get(key) is not None and float(chart[key]) <= 0:
                raise ChartBuilderDefinitionError(f"CHART {key.upper()}은 LOG scale에서 0보다 커야 합니다.")


def linked_chart_color_pairs(chart: dict[str, Any] | None) -> list[dict[str, str]]:
    """Return exact root-lot/wafer pairs encoded by the three-column color table."""
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    rules = chart.get("color_rules") if isinstance(chart, dict) and isinstance(chart.get("color_rules"), list) else []
    for rule in rules:
        split = re.match(r"^(.*?)\s+then\s+(.+)$", str(rule or "").strip(), re.IGNORECASE)
        if not split:
            continue
        conditions = re.split(r"\s+AND\s+", split.group(1), flags=re.IGNORECASE)
        if len(conditions) != 2:
            continue
        values: dict[str, str] = {}
        for condition in conditions:
            match = LINKED_COLOR_CONDITION_RE.fullmatch(condition.strip())
            if not match or match.group(1).casefold() in values:
                values = {}
                break
            values[match.group(1).casefold()] = match.group(2).strip()
        root = values.get("root_lot_id", "")
        wafer = values.get("wafer_id", "")
        key = (root.casefold(), wafer.casefold())
        if root and wafer and key not in seen:
            seen.add(key)
            rows.append({"root_lot_id": root[:160], "wafer_id": wafer[:160]})
        if len(rows) >= 200:
            break
    return rows


def format_chart_builder_definition(
    sources: list[Any], joins: list[Any] | None = None, max_rows: int = 10000,
    chart: dict[str, Any] | None = None, **_: Any
) -> str:
    """Return the canonical, copy-friendly representation of a run request."""
    lines: list[str] = []
    for index, raw_source in enumerate(sources or [], start=1):
        source = _source_dict(raw_source)
        source_id = _clean_query_id(source.get("id"), f"q{index}")
        header = source_id.upper() if re.fullmatch(r"q\d+", source_id, re.IGNORECASE) else f"[{source_id}]"
        lines.extend([
            header,
            f"TABLE = {str(source.get('root') or '').strip()}",
            f"PRODUCT = {str(source.get('product') or '').strip()}",
        ])
        sql_lines = str(source.get("sql") or "").strip().splitlines() or [""]
        lines.append(f"SQL = {sql_lines[0]}")
        lines.extend(f"  {line}" for line in sql_lines[1:])
        select_cols = str(source.get("select_cols") or "").strip()
        if select_cols:
            lines.append(f"SELECT_COLS = {select_cols}")
        recent_days = max(0, min(MAX_RECENT_DAYS, int(source.get("runtime_recent_days") or 0)))
        if recent_days:
            date_column = str(source.get("runtime_date_column") or "").strip() or DEFAULT_DATE_COLUMN
            lines.append(f"RECENT_DAYS = {recent_days}")
            lines.append(f"DATE_COLUMN = {date_column}")
        root_lot_ids = [str(value).strip() for value in (source.get("runtime_root_lot_ids") or []) if str(value).strip()]
        wafer_ids = [str(value).strip() for value in (source.get("runtime_wafer_ids") or []) if str(value).strip()]
        linked_pairs = [pair for pair in (source.get("runtime_lot_wafer_pairs") or []) if isinstance(pair, dict) and str(pair.get("root_lot_id") or "").strip() and str(pair.get("wafer_id") or "").strip()]
        if root_lot_ids and not linked_pairs:
            lines.append(f"ROOT_LOTS = {', '.join(root_lot_ids[:200])}")
        if wafer_ids and not linked_pairs:
            lines.append(f"WAFERS = {', '.join(wafer_ids[:200])}")
        if bool(source.get("apply_reformatter")):
            lines.append("REFORMATTER = true")
            reformatter_items = str(source.get("reformatter_items") or "").strip()
            if reformatter_items:
                lines.append(f"ITEMS = {reformatter_items}")
            reformatter_agg = str(source.get("reformatter_agg") or "").strip().casefold()
            if reformatter_agg:
                lines.append(f"AGG = {reformatter_agg.upper()}")
        for derived in source.get("derived_columns") or []:
            if not isinstance(derived, dict):
                continue
            name = str(derived.get("name") or "").strip()
            columns = [str(column).strip() for column in (derived.get("columns") or []) if str(column).strip()]
            if name and columns:
                separator = str(derived.get("separator") if derived.get("separator") is not None else "_").replace("\t", "\\t")
                lines.append(f"DERIVE = {name} | columns={','.join(columns)} | separator={separator}")
        for item in source.get("runtime_filters") or []:
            if not isinstance(item, dict):
                continue
            column = str(item.get("column") or "").strip()
            operator = _filter_operator(item.get("operator") or "in")
            values = [str(value).strip() for value in (item.get("values") or []) if str(value).strip()]
            if column and (values or operator in {"is_blank", "not_blank"}):
                lines.append(f"FILTER = {column} | operator={operator} | values={','.join(values[:200])}")
        lines.append("")

    for raw_join in joins or []:
        join = _source_dict(raw_join) if not isinstance(raw_join, dict) else raw_join
        left = _clean_query_id(join.get("left"))
        right = _clean_query_id(join.get("right"))
        how = str(join.get("how") or "left").strip().upper()
        left_on = ", ".join(item.strip() for item in str(join.get("left_on") or "").split(",") if item.strip())
        right_on = ", ".join(item.strip() for item in str(join.get("right_on") or "").split(",") if item.strip())
        on_clause = left_on if left_on == right_on else f"{left_on} = {right_on}"
        lines.append(f"JOIN {left} {how} {right} ON {on_clause}")
    if joins:
        lines.append("")
    chart = chart if isinstance(chart, dict) else {}
    if chart:
        lines.append("CHART")
        for key, label in (
            ("type", "TYPE"), ("title", "TITLE"), ("x", "X"), ("y", "Y"), ("x_label", "X_LABEL"),
            ("y_label", "Y_LABEL"), ("color", "COLOR"), ("trellis", "TRELLIS"), ("trend_grain", "TREND_GRAIN"),
            ("aggregation", "AGGREGATION"), ("map_y", "MAP_Y"), ("map_scope", "MAP_SCOPE"),
            ("map_target", "MAP_TARGET"), ("pie_basis", "PIE_BASIS"), ("fit", "FIT"),
            ("point_size", "POINT_SIZE"), ("marker_opacity", "MARKER_OPACITY"), ("line_width", "LINE_WIDTH"),
            ("x_min", "X_MIN"), ("x_max", "X_MAX"), ("y_min", "Y_MIN"), ("y_max", "Y_MAX"),
            ("y_scale", "Y_SCALE"),
            ("legend_position", "LEGEND_POSITION"), ("spec_low", "SPEC_LOW"), ("spec_high", "SPEC_HIGH"),
            ("box_points", "BOX_POINTS"), ("wafer_palette", "WAFER_PALETTE"), ("wafer_mode", "WAFER_MODE"),
            ("wafer_spec_low", "WAFER_SPEC_LOW"), ("wafer_spec_high", "WAFER_SPEC_HIGH"), ("wafer_low", "WAFER_LOW"),
            ("wafer_center", "WAFER_CENTER"), ("wafer_high", "WAFER_HIGH"), ("width", "WIDTH"), ("height", "HEIGHT"),
        ):
            raw_value = chart.get(key)
            value = "" if raw_value is None else str(raw_value).strip()
            if value:
                lines.append(f"{label} = {value}")
        for rule in chart.get("color_rules") or []:
            if str(rule or "").strip():
                lines.append(f"COLOR_RULE = {str(rule).strip()}")
        if str(chart.get("color_else") or "").strip():
            lines.append(f"COLOR_ELSE = {str(chart.get('color_else')).strip()}")
        if chart.get("highlight") is not None:
            lines.append(f"HIGHLIGHT = {'true' if bool(chart.get('highlight')) else 'false'}")
        if chart.get("show_legend") is not None:
            lines.append(f"SHOW_LEGEND = {'true' if bool(chart.get('show_legend')) else 'false'}")
        if chart.get("show_grid") is not None:
            lines.append(f"SHOW_GRID = {'true' if bool(chart.get('show_grid')) else 'false'}")
        lines.append("")
    lines.append(f"MAX_ROWS = {max(1, min(10000, int(max_rows or 10000)))}")
    return "\n".join(lines).strip() + "\n"
