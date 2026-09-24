"""ET index trends and ET/Inline wafer correlation charts.

All ET values are produced by the deployed reformatter through ChartBuilder.
The module never evaluates vehicle formulas itself.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
import re

from fastapi import HTTPException

from core import auth
from core.chart_builder_definition import format_chart_builder_definition


LIMIT = 5000
ET = re.compile(r"(?<![A-Za-z0-9_])ET(?![A-Za-z0-9_])", re.I)
SHOW = re.compile(r"보여|추출|가져|조회|그려|차트|chart|plot|scatter|trend|트렌드", re.I)
CORR = re.compile(r"\bcorr(?:elation)?\b|상관", re.I)
INLINE = re.compile(r"(?<![A-Za-z0-9_])inline(?![A-Za-z0-9_])|인라인|(?<![A-Za-z0-9_])L0(?![A-Za-z0-9_])", re.I)
TIME = re.compile(r"ET\s*(?:측정\s*시간|시간|time)", re.I)

AGGREGATIONS = [
    {"label": "평균 (mean)", "value": "avg"},
    {"label": "중앙값 (median)", "value": "median"},
    {"label": "최댓값 (max)", "value": "max"},
    {"label": "최솟값 (min)", "value": "min"},
    {"label": "90 백분위 (p90)", "value": "p90"},
    {"label": "10 백분위 (p10)", "value": "p10"},
]


def _q(value):
    return "`" + str(value).replace("`", "``") + "`"


def _mentioned(value, text):
    return bool(value and re.search(r"(?<![A-Za-z0-9_])" + re.escape(str(value)) + r"(?![A-Za-z0-9_])", text, re.I))


def _reply(message, context, query=None, **tool):
    from core.data_chat import reply
    if query:
        tool["query_scope"] = query
    return reply(message, context=context, ok=not bool(tool.get("missing") or tool.get("error")),
                 tool={"feature": "chart", "action": tool.pop("action", "et.index_trend"), **tool})


def _ask(context, query, kind, options, message, *, allow_other=False):
    context["pending_et_chart"] = {"query": query, "kind": kind, "options": options}
    return _reply(message, context, query, missing=[kind], needs_input=True,
                  clarification={"kind": kind, "title": message, "options": options,
                                 "allow_other": allow_other})


def _choice(text, options):
    ordinal = re.fullmatch(r"\s*(\d+)\s*번?[.!]?\s*", text)
    if ordinal and 0 < int(ordinal[1]) <= len(options):
        return options[int(ordinal[1]) - 1]
    folded = text.strip().casefold()
    matches = [option for option in options if folded in {
        str(option.get("value", "")).casefold(), str(option.get("label", "")).casefold()}]
    return matches[0] if len(matches) == 1 else None


def _allowed(user, tab):
    tabs = auth.effective_permissions(user).get("tabs") or []
    return user.get("role") == "admin" or tabs == "*" or "*" in tabs or tab in tabs


def _item_options(items):
    return [{"label": str(row["alias"]), "value": str(row["alias"])} for row in items if row.get("alias")]


def _linear_fit(rows, x_key, y_key):
    pairs = []
    for row in rows:
        try:
            x, y = float(row.get(x_key)), float(row.get(y_key))
        except (TypeError, ValueError):
            continue
        if math.isfinite(x) and math.isfinite(y):
            pairs.append((x, y))
    if len(pairs) < 2:
        return {}, pairs
    mx = sum(x for x, _ in pairs) / len(pairs)
    my = sum(y for _, y in pairs) / len(pairs)
    denom = sum((x - mx) ** 2 for x, _ in pairs)
    if denom <= 0:
        return {}, pairs
    slope = sum((x - mx) * (y - my) for x, y in pairs) / denom
    intercept = my - slope * mx
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in pairs)
    ss_tot = sum((y - my) ** 2 for _, y in pairs)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return {"slope": round(slope, 8), "intercept": round(intercept, 8),
            "r2": round(r2, 6), "sample_count": len(pairs),
            "equation": f"y = {slope:.6g}*x + {intercept:.6g}"}, pairs


def _complete_point(row, x_key, y_key):
    try:
        x, y = float(row.get(x_key)), float(row.get(y_key))
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    return {**row, "x": x, "y": y}


def _product(text, context, products):
    from core.data_chat import product_candidates
    matches = product_candidates(text, products)
    if len(matches) == 1:
        return matches[0], matches
    current = str(context.get("confirmed_product") or "")
    if current in products:
        return current, matches
    return "", matches


def _trend(query, context, user, request):
    from routers import filebrowser as fb, reformatize

    product = query["product"]
    items = reformatize.list_items(product, user).get("items") or []
    aliases = [str(row.get("alias") or "") for row in items if row.get("alias")]
    item = str(query.get("item") or "")
    if item not in aliases:
        found = [alias for alias in aliases if _mentioned(alias, query.get("prompt", ""))]
        if len(found) != 1:
            return _ask(context, query, "item", _item_options(items),
                        "표시할 ET 항목 하나를 선택해 주세요.")
        item = found[0]
        query["item"] = item
    if not query.get("days"):
        day = re.search(r"(\d+)\s*일", query.get("prompt", ""))
        if day:
            query["days"] = max(1, min(3650, int(day[1])))
    if not query.get("days"):
        return _ask(context, query, "days", [
            {"label": f"최근 {days}일", "value": str(days)} for days in (3, 5, 7, 10, 30)
        ], "조회 기간을 선택하거나 며칠인지 입력해 주세요.", allow_other=True)

    root = fb._chart_builder_resolve_root_name("ET")
    source = {"id": "et", "root": root, "product": product,
              "sql": f"SELECT `root_lot_id`, `wafer_id`, `tkout_time`, {_q(item)} ORDER BY `tkout_time` DESC",
              "apply_reformatter": True, "reformatter_items": item,
              "runtime_recent_days": int(query["days"]), "runtime_date_column": "tkout_time"}
    title = f"{product} {item} 최근 {query['days']}일 ET"
    chart = {"type": "scatter", "x": "tkout_time", "y": item, "title": title,
             "aggregation": "raw", "fit": "none", "show_legend": False}
    result = fb.chart_builder_run(fb.ChartBuilderRunReq(
        sources=[source], max_rows=LIMIT, chart=chart, chart_name=title, save_history=True), request)
    rows = (result.get("joined") or {}).get("rows") or []
    roots = sorted(set(str(row.get("root_lot_id") or "").strip() for row in rows
                       if str(row.get("root_lot_id") or "").strip()), key=str.casefold)
    saved = result.get("saved_chart") or {}
    query.update(root=root, root_lot_ids=roots, history_id=saved.get("id", ""), aggregation="raw")
    context.pop("pending_et_chart", None)
    context.update(et_chart_query=query, last_action="et.index_trend",
                   product=product, confirmed_product=product)
    points = [{**row, "x": row.get("tkout_time"), "y": row.get(item)} for row in rows]
    warnings = list(result.get("warnings") or [])
    if not saved:
        warnings.append("조회는 완료했지만 차트생성 이력 저장을 확인하지 못했습니다.")
    definition = format_chart_builder_definition(sources=[source], joins=[], max_rows=LIMIT, chart=chart)
    return _reply(f"{product} {item}의 최근 {query['days']}일 ET 측정값 {len(rows):,}개를 표시했습니다.",
                  context, query, action="et.index_trend", sources=[f"{root}/{product}", "ET reformatize"],
                  executed_sql=(result.get("sources") or [{}])[0].get("sql", source["sql"]),
                  definition_code=definition, saved_chart=saved, warnings=warnings,
                  chart_result={**chart, "chart_type": "scatter", "x_type": "date",
                                "x_label": "tkout_time", "y_label": item, "points": points},
                  table={"columns": (result.get("joined") or {}).get("columns") or [],
                         "rows": rows, "total": len(rows)},
                  interpretation={"summary": "ET vehicle reformatter의 실제 계산 결과를 집계하지 않고 tkout_time별 산점도로 표시했습니다.",
                                  "origin": "ET reformatize + ChartBuilder 실행 결과", "status": "completed",
                                  "details": [{"label": "ET 항목", "value": item},
                                              {"label": "기간", "value": f"최근 {query['days']}일"},
                                              {"label": "표현", "value": "Scatter · 집계 없음"}],
                                  "unresolved": []})


def _correlation(query, context, user, request):
    from core import data_chat_inline
    from routers import filebrowser as fb

    if not query.get("et_aggregation"):
        return _ask(context, query, "et_aggregation", AGGREGATIONS,
                    "ET 원본에는 Wafer당 여러 값이 있을 수 있습니다. Corr에 사용할 집계 방식을 선택해 주세요.")

    product, item = query["product"], query["item"]
    files = fb.source_data_files(root="ML_TABLE", product=product)
    if not files:
        raise ValueError("제품의 ML_TABLE을 찾지 못했습니다.")
    columns, _ = fb.duckdb_engine.inspect_files(files)
    names = {name.casefold(): name for name in columns}
    if not all(key in names for key in ("root_lot_id", "wafer_id")):
        raise ValueError("ML_TABLE에 Root Lot·Wafer 결합 키가 없습니다.")
    prompt = str(query.get("corr_prompt") or "")
    clean = CORR.sub(" ", INLINE.sub(" ", prompt))
    clean = re.sub(re.escape(product), " ", clean, flags=re.I)
    clean = re.sub(re.escape(item), " ", clean, flags=re.I)
    candidates, source_name = data_chat_inline._candidates(product, prompt, clean.strip(), columns, "INLINE")
    selected = None
    identity = query.get("inline_measure")
    if identity:
        selected = next((row for row in candidates if data_chat_inline._identity(row) == identity), None)
        if not selected:
            raise ValueError("선택한 Inline Semantic 연결이 변경되었습니다. Corr 요청을 다시 알려 주세요.")
    elif len(candidates) == 1:
        selected = candidates[0]
        query["inline_measure"] = data_chat_inline._identity(selected)
    elif candidates:
        options = [{"label": f"{row.get('item_desc') or row.get('term') or row.get('item_id')} · {row.get('step_id', '')} / {row.get('item_id', '')} · {row['column']}",
                    "value": str(index + 1), "identity": data_chat_inline._identity(row)}
                   for index, row in enumerate(candidates)]
        return _ask(context, query, "inline_measure", options,
                    "Corr에 사용할 Inline 평균 열의 Semantic 후보를 선택해 주세요.")
    else:
        raise ValueError("요청한 Inline 항목의 Semantic과 ML_TABLE 평균 열을 연결하지 못했습니다.")

    inline_column = selected["column"]
    roots = [str(value) for value in query.get("root_lot_ids", []) if str(value).strip()]
    if not roots:
        raise ValueError("앞선 ET 조회 결과에 Corr 범위를 정할 Root Lot이 없습니다.")
    et_source = {"id": "et", "root": query.get("root") or fb._chart_builder_resolve_root_name("ET"),
                 "product": product,
                 "sql": f"SELECT `root_lot_id`, `wafer_id`, {_q(item)}",
                 "apply_reformatter": True, "reformatter_items": item,
                 "reformatter_agg": query["et_aggregation"], "reformatter_agg_scope": "wafer",
                 "runtime_root_lot_ids": roots,
                 "runtime_recent_days": int(query["days"]), "runtime_date_column": "tkout_time"}
    inline_source = {"id": "inline", "root": "ML_TABLE", "product": product,
                     "sql": f"SELECT {_q(names['root_lot_id'])}, {_q(names['wafer_id'])}, {_q(inline_column)}",
                     "runtime_root_lot_ids": roots}
    joins = [{"left": "et", "right": "inline", "left_on": "root_lot_id,wafer_id",
              "right_on": f"{names['root_lot_id']},{names['wafer_id']}", "how": "left"}]
    title = f"{product} {inline_column} vs {item} Corr"
    chart = {"type": "scatter", "x": inline_column, "y": item, "title": title,
             "aggregation": "raw", "fit": "linear", "show_legend": False}
    result = fb.chart_builder_run(fb.ChartBuilderRunReq(
        sources=[et_source, inline_source], joins=joins, max_rows=LIMIT,
        chart=chart, chart_name=title, save_history=True), request)
    rows = (result.get("joined") or {}).get("rows") or []
    fit, pairs = _linear_fit(rows, inline_column, item)
    complete_points = [point for row in rows if (point := _complete_point(row, inline_column, item))]
    # Mark every ET row so the evidence table makes unmatched wafers explicit.
    table_rows = [{**row, "pair_status": "complete" if row.get(inline_column) is not None and row.get(item) is not None else "unmatched"}
                  for row in rows]
    unmatched = len(rows) - len(pairs)
    saved = result.get("saved_chart") or {}
    query.update(inline_column=inline_column, inline_semantic_source=source_name,
                 history_id=saved.get("id", ""), mode="corr")
    context.pop("pending_et_chart", None)
    context.update(et_chart_query=query, last_action="et.inline_correlation")
    warnings = list(result.get("warnings") or [])
    if not saved:
        warnings.append("조회는 완료했지만 차트생성 이력 저장을 확인하지 못했습니다.")
    if len(pairs) < 2:
        warnings.append("완전한 ET·Inline 쌍이 2개 미만이라 선형 적합과 R²를 계산하지 못했습니다.")
    definition = format_chart_builder_definition(sources=[et_source, inline_source], joins=joins,
                                                 max_rows=LIMIT, chart=chart)
    r2_text = f" R²={fit['r2']:.6f}." if fit else " R²는 계산할 수 없습니다."
    return _reply(f"완전한 ET·Inline 쌍 {len(pairs):,}개로 Corr chart를 만들었습니다.{r2_text} "
                  f"Inline 미매칭 ET 행 {unmatched:,}개도 표에 유지했습니다.",
                  context, query, action="et.inline_correlation",
                  sources=[f"{et_source['root']}/{product}", f"ML_TABLE_{product}"],
                  executed_sql="\n\n".join(f"{row.get('id')}: {row.get('sql')}" for row in result.get("sources", [])),
                  definition_code=definition, saved_chart=saved, warnings=warnings,
                  joins=result.get("joins", []), fit=fit,
                  evidence={"complete_pair_count": len(pairs), "unmatched_et_count": unmatched,
                            "total_et_rows": len(rows), "r2": fit.get("r2")},
                  chart_result={**chart, "chart_type": "scatter", "x_label": inline_column,
                                "y_label": item, "points": complete_points, "fit": fit, "linear_fit": fit},
                  table={"columns": [*((result.get("joined") or {}).get("columns") or []), "pair_status"],
                         "rows": table_rows, "total": len(table_rows)},
                  interpretation={"summary": "선택한 방식으로 ET를 Wafer 단위 집계한 뒤 Root Lot·Wafer를 기준으로 ML_TABLE Inline 평균값을 LEFT JOIN했습니다. 선형 적합에는 두 값이 모두 있는 행만 사용했습니다.",
                                  "origin": "ET reformatize + 제품 Semantic + ML_TABLE + ChartBuilder", "status": "completed",
                                  "details": [{"label": "ET 집계", "value": query["et_aggregation"]},
                                              {"label": "결합", "value": "ET LEFT JOIN Inline · root_lot_id + wafer_id"},
                                              {"label": "적합 표본", "value": str(len(pairs))},
                                              {"label": "미매칭 ET", "value": str(unmatched)},
                                              {"label": "선형 적합", "value": fit.get("equation", "계산 불가")},
                                              {"label": "R²", "value": str(fit.get("r2", "계산 불가"))}],
                                  "unresolved": []})


def _et_item_correlation(query, context, user, request):
    from routers import filebrowser as fb, reformatize

    product = query["product"]
    items = reformatize.list_items(product, user).get("items") or []
    aliases = [str(row.get("alias") or "") for row in items if row.get("alias")]
    prompt = str(query.get("prompt") or "")
    mentioned = [alias for alias in aliases if _mentioned(alias, prompt)]
    if len(mentioned) != 2:
        return _reply("상관분석할 ET 항목 두 개를 질문에 명시해 주세요. 가능한 항목: " + ", ".join(aliases),
                      context, query, action="et.item_correlation", error="et_items_unresolved")
    x_item, y_item = sorted(mentioned, key=lambda alias: prompt.lower().find(alias.lower()))
    if not query.get("et_aggregation"):
        return _ask(context, query, "et_aggregation", AGGREGATIONS,
                    "Wafer별 ET 값의 집계 방식을 선택해 주세요.")
    root = fb._chart_builder_resolve_root_name("ET")
    source = {"id": "et", "root": root, "product": product,
              "sql": f"SELECT `root_lot_id`, `wafer_id`, {_q(x_item)}, {_q(y_item)}",
              "apply_reformatter": True, "reformatter_items": f"{x_item},{y_item}",
              "reformatter_agg": query["et_aggregation"], "reformatter_agg_scope": "wafer",
              "runtime_recent_days": int(query["days"]), "runtime_date_column": "tkout_time"}
    title = f"{product} ET {x_item} vs {y_item} Corr"
    chart = {"type": "scatter", "x": x_item, "y": y_item, "title": title,
             "aggregation": "raw", "fit": "linear", "show_legend": False}
    result = fb.chart_builder_run(fb.ChartBuilderRunReq(
        sources=[source], max_rows=LIMIT, chart=chart, chart_name=title, save_history=True), request)
    rows = (result.get("joined") or {}).get("rows") or []
    fit, pairs = _linear_fit(rows, x_item, y_item)
    points = [point for row in rows if (point := _complete_point(row, x_item, y_item))]
    saved = result.get("saved_chart") or {}
    query.update(items=[x_item, y_item], root=root, history_id=saved.get("id", ""))
    context.pop("pending_et_chart", None)
    context.update(et_chart_query=query, last_action="et.item_correlation",
                   product=product, confirmed_product=product)
    warnings = list(result.get("warnings") or [])
    if len(pairs) < 2:
        warnings.append("완전한 ET 항목 쌍이 2개 미만이라 선형 적합과 R²를 계산하지 못했습니다.")
    if not saved:
        warnings.append("조회는 완료했지만 차트생성 이력 저장을 확인하지 못했습니다.")
    return _reply(f"Wafer별 ET 항목 쌍 {len(pairs):,}개로 Corr chart를 만들었습니다." +
                  (f" R²={fit['r2']:.6f}." if fit else " R²는 계산할 수 없습니다."),
                  context, query, action="et.item_correlation", sources=[f"{root}/{product}", "ET reformatize"],
                  executed_sql=(result.get("sources") or [{}])[0].get("sql", source["sql"]),
                  definition_code=format_chart_builder_definition(sources=[source], joins=[], max_rows=LIMIT, chart=chart),
                  saved_chart=saved, warnings=warnings, fit=fit,
                  evidence={"complete_pair_count": len(pairs), "unmatched_et_count": len(rows) - len(pairs),
                            "total_et_rows": len(rows), "r2": fit.get("r2")},
                  chart_result={**chart, "chart_type": "scatter", "x_label": x_item, "y_label": y_item,
                                "points": points, "fit": fit, "linear_fit": fit},
                  table={"columns": (result.get("joined") or {}).get("columns") or [],
                         "rows": rows, "total": len(rows)},
                  interpretation={"summary": "ET reformatter의 두 항목을 같은 Root Lot·Wafer 단위로 집계해 상관 차트로 표시했습니다.",
                                  "origin": "ET reformatize + ChartBuilder", "status": "completed",
                                  "details": [{"label": "ET 항목", "value": f"{x_item} / {y_item}"},
                                              {"label": "집계", "value": f"Wafer별 {query['et_aggregation']}"},
                                              {"label": "완전한 쌍", "value": str(len(pairs))}], "unresolved": []})


def dispatch(text, context, request=None):
    pending = context.get("pending_et_chart") or {}
    previous = context.get("et_chart_query") or {}
    correlation = bool(previous and CORR.search(text) and INLINE.search(text))
    et_correlation = bool(ET.search(text) and CORR.search(text) and not INLINE.search(text))
    fresh = bool(ET.search(text) and SHOW.search(text) and not TIME.search(text) and not CORR.search(text)
                 and not re.search(r"tracker|트래커|이슈|인폼|inform|ETA|도착", text, re.I))
    if not (fresh or correlation or et_correlation or pending):
        return None
    state = deepcopy(context)
    if pending and re.fullmatch(r"취소(?:해|해줘)?[.!\s]*", text):
        state.pop("pending_et_chart", None)
        return _reply("ET 차트 조건 확인을 취소했습니다.", state)
    if fresh:
        query = {"mode": "trend", "prompt": text}
        state.pop("pending_et_chart", None)
    elif et_correlation:
        day = re.search(r"(\d+)\s*일", text)
        query = {"mode": "et_corr", "prompt": text,
                 "days": max(1, min(3650, int(day[1]))) if day else 30}
        state.pop("pending_et_chart", None)
    elif correlation:
        query = deepcopy(previous)
        query.update(mode="corr", corr_prompt=text)
        query.pop("et_aggregation", None)
        query.pop("inline_measure", None)
        state.pop("pending_et_chart", None)
    else:
        query = deepcopy(pending.get("query") or {})
        option = _choice(text, pending.get("options") or [])
        kind = pending.get("kind")
        if kind == "days" and not option:
            match = re.search(r"(\d+)\s*일?", text)
            if match:
                option = {"value": str(max(1, min(3650, int(match[1]))))}
        if not option:
            return _ask(state, query, kind, pending.get("options") or [],
                        "표시된 후보의 번호 또는 이름을 선택해 주세요.", allow_other=kind == "days")
        if kind == "inline_measure":
            query[kind] = option.get("identity")
        elif kind == "days":
            query[kind] = int(option["value"])
        else:
            query[kind] = option["value"]
        state.pop("pending_et_chart", None)

    user = auth.current_user(request)
    missing_permissions = [tab for tab in ("reformatize", "chartbuilder") if not _allowed(user, tab)]
    if missing_permissions:
        state.pop("pending_et_chart", None)
        return _reply("ET 재포맷 조회와 차트생성 권한이 모두 필요합니다.", state, query,
                      action="et.chart.permission", error="permission_denied", blocked=True,
                      missing_permissions=missing_permissions)

    from routers import reformatize
    try:
        products = [row["product"] for row in reformatize.products(user).get("products", [])]
        product = str(query.get("product") or "")
        if product not in products:
            product, matches = _product(query.get("prompt", text), state, products)
            if len(matches) > 1 or not product:
                return _ask(state, query, "product", [{"label": value, "value": value} for value in (matches or products)],
                            "조회할 ET 제품을 선택해 주세요.")
            query["product"] = product
        if query.get("mode") == "corr":
            return _correlation(query, state, user, request)
        if query.get("mode") == "et_corr":
            return _et_item_correlation(query, state, user, request)
        return _trend(query, state, user, request)
    except (HTTPException, ValueError) as exc:
        state.pop("pending_et_chart", None)
        message = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
        return _reply(f"ET 차트를 만들 수 없습니다: {message}", state, query,
                      action="et.chart.failed", error="et_chart_failed")
