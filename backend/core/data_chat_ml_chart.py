"""ML_TABLE measurement trends executed through the shared ChartBuilder.

The resolver deliberately inspects only the physical ML_TABLE schema before a
user confirms a measurement, time axis, or colour column.  A non-exact match is
never silently bound to a similarly named process column.
"""
from __future__ import annotations

from copy import deepcopy
from difflib import SequenceMatcher
import re
from typing import Any

from fastapi import HTTPException

from core import auth
from core.chart_builder_definition import format_chart_builder_definition


LIMIT = 5000
TREND = re.compile(r"trend|트렌드|추이|그려\s*(?:줘|주세요)?|plot|chart|차트", re.I)
ML_SOURCE = re.compile(r"(?:ml[_\s-]*table|스플릿\s*테이블|splittable|split\s*table)(?:에|에서|의|로|으로)?", re.I)
COLOR = re.compile(r"컬러링|색상|색칠|컬러|color|colour|group|구분", re.I)
TIME_SUFFIX = re.compile(r"(?:^|[_\s])tkout[_\s]*time$", re.I)
NUMERIC_TYPE = re.compile(r"(?:^|\b)(?:u?int\d*|float\d*|double|decimal|numeric)(?:\b|$)", re.I)
NON_MEASURE_PREFIXES = ("KNOB_", "MASK_", "ECU_", "META_")
IDENTIFIERS = {"product", "root_lot_id", "lot_id", "fab_lot_id", "wafer_id"}


def _norm(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", " ", str(value or "").casefold()).strip()


def _compact(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", str(value or "").casefold())


def _bare_column(value: str) -> str:
    text = re.sub(r"^(?:ML|VALUE|MEASURE(?:MENT)?)[_\s-]+", "", str(value), flags=re.I)
    text = re.sub(r"^(?:\d+(?:\.\d+)?)[_\s-]+", "", text)
    return _norm(text)


def _number_tag(value: str) -> str:
    match = re.search(r"(?:^|[^0-9])(\d+(?:\.\d+)?)(?=$|[^0-9])", str(value))
    return match.group(1) if match else ""


def _process_name(value: str) -> str:
    text = re.sub(r"tkout[_\s]*time$", "", str(value), flags=re.I)
    text = re.sub(r"(?:^|[_\s-])\d+(?:\.\d+)?(?:$|[_\s-])", " ", text)
    text = re.sub(r"\b(?:ML|VALUE|MEASURE|FAB|ECU|ALL|KNOB)\b", " ", text, flags=re.I)
    return _norm(text)


def _q(value: str) -> str:
    return "`" + str(value).replace("`", "``") + "`"


def _reply(message: str, context: dict, query: dict | None = None, **tool: Any) -> dict:
    from core.data_chat import reply
    if query is not None:
        tool["query_scope"] = query
    return reply(
        message,
        context=context,
        ok=not bool(tool.get("missing") or tool.get("error")),
        tool={"feature": "chart", "action": "ml_table.trend", **tool},
    )


def _ask(context: dict, query: dict, kind: str, options: list[dict], message: str) -> dict:
    context["pending_ml_chart"] = {"query": query, "kind": kind, "options": options}
    return _reply(
        message,
        context,
        query,
        missing=[kind],
        needs_input=True,
        clarification={
            "kind": kind,
            "title": message,
            "options": options,
            "allow_other": False,
        },
        table={
            "columns": ["번호", "실제 ML_TABLE 열"],
            "rows": [{"번호": index + 1, "실제 ML_TABLE 열": option["label"]} for index, option in enumerate(options)],
            "total": len(options),
        },
    )


def _selected_option(text: str, options: list[dict]) -> dict | None:
    ordinal = re.fullmatch(r"\s*(\d+)\s*번?[.!]?\s*", text)
    if ordinal and 0 < int(ordinal.group(1)) <= len(options):
        return options[int(ordinal.group(1)) - 1]
    folded = text.strip().casefold()
    matches = [option for option in options if folded in {
        str(option.get("value") or "").casefold(), str(option.get("label") or "").casefold()
    }]
    return matches[0] if len(matches) == 1 else None


def _measurement_text(text: str, product: str) -> str:
    clean = re.sub(re.escape(product), " ", text, flags=re.I) if product else text
    clean = ML_SOURCE.sub(" ", clean)
    clean = TREND.sub(" ", clean)
    clean = re.sub(r"\S*tkout[_\s]*time\S*", " ", clean, flags=re.I)
    clean = re.sub(r"제품(?:으로|에서|의)?|측정값?|항목|보여\s*(?:줘|주세요)?|가져와서|있는\s*거|으로", " ", clean, flags=re.I)
    return _norm(clean)


def _measurement_candidates(schema: dict[str, str], term: str) -> tuple[str, list[dict]]:
    candidates = []
    for column, dtype in schema.items():
        upper = column.upper()
        if column.casefold() in IDENTIFIERS or TIME_SUFFIX.search(column) or upper.startswith(NON_MEASURE_PREFIXES):
            continue
        if not NUMERIC_TYPE.search(str(dtype)):
            continue
        bare = _bare_column(column)
        exact = bool(term and term in {_norm(column), bare})
        similarity = max(SequenceMatcher(None, term, candidate).ratio() for candidate in {_norm(column), bare}) if term else 0.0
        contains = bool(term and (_compact(term) in _compact(column) or _compact(column) in _compact(term)))
        if exact or not term or contains or similarity >= 0.42:
            candidates.append({"label": column, "value": column, "exact": exact, "score": max(similarity, 0.8 if contains else 0.0)})
    candidates.sort(key=lambda row: (-int(row["exact"]), -row["score"], _norm(row["value"])))
    exact = [row["value"] for row in candidates if row["exact"]]
    return (exact[0] if len(exact) == 1 else ""), candidates[:30]


def _time_candidates(schema: dict[str, str], measurement: str) -> list[dict]:
    number = _number_tag(measurement)
    process = _process_name(measurement)
    rows = []
    for column in schema:
        if not TIME_SUFFIX.search(column):
            continue
        candidate_process = _process_name(column)
        similarity = SequenceMatcher(None, process, candidate_process).ratio() if process and candidate_process else 0.0
        same_number = bool(number and _number_tag(column) == number)
        rows.append({
            "label": column,
            "value": column,
            "same_numeric_prefix": same_number,
            "process_similarity": round(similarity, 4),
        })
    rows.sort(key=lambda row: (-int(row["same_numeric_prefix"]), -row["process_similarity"], _norm(row["value"])))
    return rows[:30]


def _mentioned_column(text: str, columns: list[str]) -> str:
    matches = [column for column in columns if re.search(r"(?<![0-9A-Za-z가-힣])" + re.escape(column) + r"(?![0-9A-Za-z가-힣])", text, re.I)]
    return matches[0] if len(matches) == 1 else ""


def _color_term(text: str, product: str) -> str:
    clean = re.sub(re.escape(product), " ", text, flags=re.I) if product else text
    clean = COLOR.sub(" ", clean)
    clean = re.sub(r"eqp[_\s-]*id|equipment|장비|chamber[_\s-]*id|eqp[_\s-]*chamber|chamber|챔버|으로|로|와|과|해\s*줘", " ", clean, flags=re.I)
    return _norm(clean)


def _color_mode(text: str) -> str:
    has_equipment = bool(re.search(r"eqp[_\s-]*id|equipment|장비", text, re.I))
    has_chamber = bool(re.search(r"eqp[_\s-]*chamber|chamber[_\s-]*id|chamber|챔버", text, re.I))
    if has_chamber:
        return "equipment_chamber"
    if has_equipment:
        return "equipment"
    return "full"


def _ecu_options(columns: list[str], term: str) -> tuple[str, list[dict]]:
    candidates = [column for column in columns if "ECU" in column.upper()]
    rows = []
    for column in candidates:
        exact = bool(term and term == _norm(column))
        score = SequenceMatcher(None, _compact(term), _compact(column)).ratio() if term else 0.0
        if exact or not term or _compact(term) in _compact(column) or score >= 0.35:
            rows.append({"label": column, "value": column, "exact": exact, "score": score})
    rows.sort(key=lambda row: (-int(row["exact"]), -row["score"], _norm(row["value"])))
    exact = [row["value"] for row in rows if row["exact"]]
    return (exact[0] if len(exact) == 1 else ""), rows[:30]


def _hierarchy_value(value: Any, mode: str) -> Any:
    if value is None or mode == "full":
        return value
    parts = str(value).split("_")
    if mode == "equipment":
        return parts[0] if parts else ""
    return "_".join(parts[:2]).rstrip("_")


def _permissions_ok(request: Any) -> bool:
    user = auth.current_user(request)
    tabs = auth.effective_permissions(user).get("tabs") or []
    return bool(user.get("role") == "admin" or tabs == "*" or "*" in tabs or "chartbuilder" in tabs)


def dispatch(text: str, context: dict, request: Any = None) -> dict | None:
    """Resolve and execute a product ML_TABLE trend, with schema-bound HITL."""
    pending = context.get("pending_ml_chart") or {}
    previous = context.get("ml_chart_query") or {}
    fresh = bool(TREND.search(text) and ML_SOURCE.search(text))
    recolor = bool(previous and COLOR.search(text))
    if not (fresh or recolor or pending):
        return None

    context = deepcopy(context)
    if pending and re.fullmatch(r"취소(?:해|해줘)?[.!　\s]*", text, re.I):
        context.pop("pending_ml_chart", None)
        return _reply("ML_TABLE 차트 조건 확인을 취소했습니다.", context)

    product = str(context.get("confirmed_product") or "").strip()
    if not product:
        from core.data_chat import _product_question, available_split_product_names
        return _product_question(text, context, available_split_product_names())
    if not _permissions_ok(request):
        return _reply("차트생성 권한이 필요합니다.", context, error="permission_denied", blocked=True)

    query = deepcopy((pending.get("query") or previous) if recolor else (pending.get("query") or {}))
    if fresh:
        query = {"product": product, "prompt": text, "aggregation": "raw"}
    if query.get("product") != product:
        context.pop("pending_ml_chart", None)
        return _reply("제품이 변경되었습니다. ML_TABLE Trend를 다시 요청해 주세요.", context, error="ml_chart_product_changed")

    choice = None
    if pending and not fresh and not recolor:
        choice = _selected_option(text, pending.get("options") or [])
        if not choice:
            return _ask(context, query, pending.get("kind", "ml_column"), pending.get("options") or [], "실제 ML_TABLE 열의 번호 또는 이름을 선택해 주세요.")
        query[pending["kind"]] = choice["value"]
        context.pop("pending_ml_chart", None)

    from routers import filebrowser as fb
    try:
        schema, _source_size = fb._schema_for_product_source("ML_TABLE", product)
        if not schema:
            raise ValueError(f"{product}의 실제 ML_TABLE 스키마를 찾지 못했습니다.")
        columns = list(schema)

        measurement = str(query.get("ml_measurement") or "")
        if measurement and measurement not in schema:
            raise ValueError("선택한 측정 열이 변경되었습니다. ML_TABLE Trend를 다시 요청해 주세요.")
        if not measurement:
            term = _measurement_text(query.get("prompt", text), product)
            exact, options = _measurement_candidates(schema, term)
            if exact:
                measurement = exact
                query["ml_measurement"] = exact
                query["measurement_term"] = term
            elif options:
                query["measurement_term"] = term
                return _ask(context, query, "ml_measurement", options, "실제 ML_TABLE 숫자 측정 열을 선택해 주세요.")
            else:
                raise ValueError("요청과 일치하는 실제 ML_TABLE 숫자 측정 열을 찾지 못했습니다.")

        time_column = str(query.get("ml_time") or "")
        times = _time_candidates(schema, measurement)
        if time_column and time_column not in {option["value"] for option in times}:
            raise ValueError("선택한 tkout_time 열이 변경되었습니다. ML_TABLE Trend를 다시 요청해 주세요.")
        if not time_column:
            explicitly_mentioned = _mentioned_column(query.get("prompt", text), [option["value"] for option in times])
            if explicitly_mentioned:
                time_column = explicitly_mentioned
                query["ml_time"] = time_column
            elif not times:
                raise ValueError("ML_TABLE에 실제 *_tkout_time 열이 없습니다.")
            else:
                # Even a single candidate is confirmed: process names alone are not a binding.
                return _ask(context, query, "ml_time", times, "어떤 실제 *_tkout_time 열을 X축으로 사용할까요?")

        color_column = str(query.get("ml_color") or "")
        color_mode = str(query.get("color_mode") or "full")
        if recolor:
            query.pop("ml_color", None)
            color_column = ""
            color_mode = _color_mode(text)
            query["color_mode"] = color_mode
            user = auth.current_user(request)
            if re.search(r"KNOB|노브|스플릿", text, re.I):
                from core import knob_resolution
                knob_columns = [column for column in columns if column.upper().startswith("KNOB_")]
                resolution = knob_resolution.resolve(product, text, knob_columns, user.get("username", ""))
                query["color_alias"] = resolution["alias"]
                if resolution["exact"]:
                    color_column = resolution["exact"]
                else:
                    return _ask(context, query, "ml_color", resolution["options"] or [{"label": c, "value": c} for c in knob_columns[:30]], "실제 ML_TABLE KNOB 열을 선택해 주세요.")
            else:
                term = _color_term(text, product)
                exact, options = _ecu_options(columns, term)
                if not term and previous.get("ml_color") in columns:
                    exact = previous["ml_color"]
                if exact:
                    color_column = exact
                elif options:
                    return _ask(context, query, "ml_color", options, "실제 ML_TABLE ECU 열을 선택해 주세요.")
                else:
                    raise ValueError("요청과 일치하는 실제 ML_TABLE ECU 열을 찾지 못했습니다.")
            query["ml_color"] = color_column
        elif choice and pending.get("kind") == "ml_color":
            color_column = choice["value"]
            query["color_mode"] = color_mode
            if color_column.upper().startswith("KNOB_"):
                from core import knob_resolution
                user = auth.current_user(request)
                knob_columns = [column for column in columns if column.upper().startswith("KNOB_")]
                knob_resolution.remember(user.get("username", ""), product, query.get("color_alias", ""), color_column, knob_columns)
        if color_column and color_column not in schema:
            raise ValueError("선택한 색상 열이 변경되었습니다. 색상 조건을 다시 요청해 주세요.")

        identifiers = [column for column in columns if column.casefold() in IDENTIFIERS]
        projection = list(dict.fromkeys(identifiers + [time_column, measurement] + ([color_column] if color_column else [])))
        derived_columns = []
        chart_color = color_column
        if color_column and color_mode in {"equipment", "equipment_chamber"}:
            chart_color = "ml_equipment_color" if color_mode == "equipment" else "ml_equipment_chamber_color"
            derived_columns.append({
                "name": chart_color,
                "columns": [color_column],
                "separator": "_",
                "operation": "split_prefix",
                "segments": 1 if color_mode == "equipment" else 2,
            })
        source = {
            "id": "q1",
            "root": "ML_TABLE",
            "product": product,
            "sql": "SELECT " + ", ".join(_q(column) for column in projection) + " ORDER BY " + _q(time_column) + " DESC",
            "derived_columns": derived_columns,
        }
        title = f"{product} · {measurement} Trend"
        chart = {
            "type": "scatter", "x": time_column, "y": measurement,
            "title": title, "color": chart_color, "show_legend": True,
            "aggregation": "raw",
        }
        result = fb.chart_builder_run(fb.ChartBuilderRunReq(
            sources=[fb.ChartBuilderSourceReq(**source)], max_rows=LIMIT,
            chart=chart, chart_name=title + (f" · {color_column}" if color_column else ""),
            save_history=True,
        ), request)
        rows = (result.get("joined") or {}).get("rows") or []
        points = []
        for row in rows:
            point = {**row, "x": row.get(time_column), "y": row.get(measurement)}
            if color_column:
                point["color_value"] = row.get(chart_color) if chart_color != color_column else row.get(color_column)
            points.append(point)
        saved = result.get("saved_chart") or {}
        query["history_id"] = saved.get("id", "")
        context.pop("pending_ml_chart", None)
        context.update(ml_chart_query=query, last_action="ml_table.trend")
        warnings = list(result.get("warnings") or [])
        if not saved:
            warnings.append("조회는 완료했지만 차트생성 이력 저장을 확인하지 못했습니다.")
        definition = format_chart_builder_definition(sources=[source], joins=[], max_rows=LIMIT, chart=chart)
        summary = f"실제 ML_TABLE의 {measurement}을 {time_column} 기준 개별 측정값 산점도로 표시했습니다."
        if color_column:
            level = {"equipment": "장비", "equipment_chamber": "장비+챔버", "full": "전체 ECU"}[color_mode]
            summary += f" {color_column}의 {level} 범주로 색상을 구분했습니다."
        return _reply(
            f"{product} ML_TABLE 측정값 {len(rows):,}개를 표시했습니다." + (" 차트생성 이력에 저장했습니다." if saved else ""),
            context,
            query,
            sources=[f"ML_TABLE_{product}"],
            executed_sql="\n\n".join(f"{item['id']}: {item['sql']}" for item in result.get("sources", [])),
            definition_code=definition,
            chart_result={
                **chart, "chart_type": "scatter", "x_type": "date",
                "x_label": time_column, "y_label": measurement,
                "color_by": chart_color, "color_source": color_column,
                "color_mode": color_mode, "points": points,
            },
            table={"columns": (result.get("joined") or {}).get("columns", projection), "rows": rows, "total": len(rows)},
            saved_chart=saved,
            warnings=warnings,
            interpretation={
                "summary": summary,
                "origin": "실제 ML_TABLE 스키마·사용자 확인·ChartBuilder 실행",
                "status": "completed",
                "details": [
                    {"label": "측정 열", "value": measurement},
                    {"label": "X축", "value": time_column},
                    {"label": "표현", "value": "Scatter · 집계 없음"},
                    {"label": "색상", "value": f"{color_column} · {color_mode}" if color_column else "지정하지 않음"},
                    {"label": "저장", "value": f"차트생성 이력 {saved.get('id')} · Template report 재사용 가능" if saved else "이력 저장 미확인"},
                ],
                "unresolved": [],
            },
        )
    except (HTTPException, ValueError) as exc:
        context.pop("pending_ml_chart", None)
        message = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
        return _reply(message, context, query, error="ml_chart_failed")
