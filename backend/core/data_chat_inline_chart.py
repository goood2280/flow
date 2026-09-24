"""Raw Inline shot trends, executed and persisted by the shared ChartBuilder."""
from copy import deepcopy
import re

from fastapi import HTTPException
from core import auth, product_semantics, semantic_measure_catalog
from core.chart_builder_definition import format_chart_builder_definition

SHOT = re.compile(r"샷|\bshots?\b|\bsite\b", re.I)
TREND = re.compile(r"trend|트렌드|추이|scatter|산점도", re.I)
COLOR = re.compile(r"컬러|색상|색칠|분류|구분|그룹|color|colour|group", re.I)
LIMIT = 5000


def _q(value):
    return "`" + str(value).replace("`", "``") + "`"


def _lit(value):
    return "'" + str(value).replace("'", "''") + "'"


def _reply(message, context, query=None, **tool):
    from core.data_chat import reply
    if query:
        tool["query_scope"] = query
    return reply(message, context=context, ok=not bool(tool.get("missing") or tool.get("error")),
                 tool={"feature": "chart", "action": "inline.shot_trend", **tool})


def _ask(context, query, kind, options, message):
    context["pending_inline_chart"] = {"query": query, "kind": kind, "options": options}
    return _reply(message, context, query, missing=[kind], needs_input=True,
                  clarification={"kind": kind, "title": message, "options": options, "allow_other": False})


def _measures(product, text):
    rows = semantic_measure_catalog.match_terms(text, product=product)
    rows += product_semantics.resolve_terms(product, text)
    out = {}
    for row in rows:
        if str(row.get("source_type") or "INLINE").upper() != "INLINE":
            continue
        if row.get("product") and str(row["product"]).casefold() != product.casefold():
            continue
        if not row.get("step_id") or not row.get("item_id"):
            continue
        out.setdefault((str(row["step_id"]), str(row["item_id"])), dict(row))
    return list(out.values())


def _measure_options(rows):
    return [{"label": f"{r.get('term') or r.get('item_desc') or r['item_id']} · {r['step_id']} / {r['item_id']}",
             "value": str(i + 1), "measure": r} for i, r in enumerate(rows)]


def _site_options(fb, files, columns, where):
    # Discover the actual row-level discriminator; never guess that all rows are shots.
    where = fb._validate_where_expression(where, columns)
    names = {c.casefold(): c for c in columns}
    options = []
    for key in ("data_type", "row_type", "measure_type", "measurement_type", "subitem_type", "stat_type", "value_type", "subitem_id"):
        col = names.get(key)
        if not col:
            continue
        values = fb.duckdb_engine.distinct_values(files, col, where=where, limit=1001)
        if len(values) > 1000:
            continue
        sites = [v for v in values if re.fullmatch(r"(?:SITE|SHOT)(?:[ _-]?\d+)*", str(v).strip(), re.I)]
        if sites:
            options.append({"label": f"{col}: {', '.join(sites[:6])}" + (f" 외 {len(sites)-6}개" if len(sites) > 6 else ""),
                            "value": col, "column": col, "values": sites})
            # An explicit type column is more precise than a per-shot identifier.
            if key != "subitem_id":
                break
    return options


def dispatch(text, context, request=None):
    pending = context.get("pending_inline_chart") or {}
    previous = context.get("inline_chart_query") or {}
    fresh = bool(SHOT.search(text) and TREND.search(text))
    recolor = bool(COLOR.search(text) and previous)
    if not (fresh or recolor or pending):
        return None
    from core.measurement_family import infer
    if pending and not fresh and not recolor and infer(text) and re.search(r"보여|조회|알려|trend|추이", text, re.I):
        context.pop("pending_inline_chart", None)
        return None
    if fresh and infer(text) == "VM":
        return _reply("VM·IM·가상계측은 INLINE 원본과 구분합니다. VM 항목의 저장값 조회 또는 Trend로 요청해 주세요.", context,
                      error="vm_shot_source_unresolved")
    if pending and re.fullmatch(r"취소(?:해|해줘)?[.!\s]*", text):
        context = deepcopy(context)
        context.pop("pending_inline_chart", None)
        return _reply("차트 조건 확인을 취소했습니다.", context)
    if pending and not fresh and not recolor and re.search(r"대시보드|dashboard|ET\s|위치|어디|랏\s*관리|lot\s*management|스플릿테이블", text, re.I):
        context.pop("pending_inline_chart", None)
        return None
    context = deepcopy(context)
    product = context.get("confirmed_product")
    if not product:
        from core.data_chat import _product_question, available_product_names
        return _product_question(text, context, available_product_names())
    user = auth.current_user(request)
    tabs = auth.effective_permissions(user).get("tabs") or []
    if user.get("role") != "admin" and tabs != "*" and "*" not in tabs and "chartbuilder" not in tabs:
        return _reply("차트생성 권한이 필요합니다.", context, error="permission_denied", blocked=True)
    query = {"product": product, "prompt": text, "aggregation": "raw", "x": "tkout_time"} if fresh else deepcopy(pending.get("query") or previous)
    if query.get("product") != product:
        return _reply("제품이 변경되었습니다. 측정 항목과 샷별 Trend를 다시 요청해 주세요.", context, error="chart_product_changed")
    choice = None
    if pending and not fresh and not recolor:
        options = pending.get("options") or []
        ordinal = re.fullmatch(r"\s*(\d+)\s*번?[.!]?\s*", text)
        if ordinal and 0 < int(ordinal[1]) <= len(options):
            choice = options[int(ordinal[1]) - 1]
        else:
            choice = next((o for o in options if text.strip().casefold() in {str(o['value']).casefold(), o['label'].casefold()}), None)
        if choice is None:
            return _ask(context, query, pending["kind"], options, "실제 후보의 번호 또는 이름을 선택해 주세요.")
        query[pending["kind"]] = choice
        context.pop("pending_inline_chart", None)
    from routers import filebrowser as fb
    try:
        root = fb._chart_builder_resolve_root_name("INLINE")
        files = fb.source_data_files(root=root, product=product)
        if not files:
            raise ValueError("해당 제품의 원본 INLINE DB가 없습니다.")
        columns, _ = fb.duckdb_engine.inspect_files(files)
        names = {c.casefold(): c for c in columns}
        required = ["step_id", "item_id", "tkout_time", "root_lot_id", "wafer_id"]
        if any(c not in names for c in required):
            raise ValueError("원본 INLINE 필수 열이 없습니다: " + ", ".join(c for c in required if c not in names))
        measures = _measures(product, query["prompt"])
        if not measures:
            # An explicit DB step/item pair is a verified raw-data selection, even
            # when an operator-facing Semantic name has not been registered.
            step_values = fb.duckdb_engine.distinct_values(files, names["step_id"], limit=1001)
            item_values = fb.duckdb_engine.distinct_values(files, names["item_id"], limit=1001)
            step_matches = [v for v in step_values if re.search(r"(?<![A-Za-z0-9_])" + re.escape(str(v)) + r"(?![A-Za-z0-9_])", query["prompt"], re.I)]
            item_matches = [v for v in item_values if re.search(r"(?<![A-Za-z0-9_])" + re.escape(str(v)) + r"(?![A-Za-z0-9_])", query["prompt"], re.I)]
            if len(step_matches) == len(item_matches) == 1:
                raw_where = f"{_q(names['step_id'])} = {_lit(step_matches[0])} AND {_q(names['item_id'])} = {_lit(item_matches[0])}"
                raw_where = fb._validate_where_expression(raw_where, columns)
                sample, _, _ = fb.duckdb_engine.query_files(files, where=raw_where,
                                                               select_cols=[names["step_id"]], limit=1)
                if sample.height:
                    measures = [{"product": product, "source_type": "INLINE", "term": str(item_matches[0]),
                                 "step_id": str(step_matches[0]), "item_id": str(item_matches[0]),
                                 "value_column": "value", "selection_source": "verified_raw_db"}]
        measure = (query.get("measurement") or {}).get("measure")
        if measure and not any((r["step_id"], r["item_id"]) == (measure["step_id"], measure["item_id"]) for r in measures):
            raise ValueError("Semantic 연결이 변경되었습니다. 측정 항목을 다시 요청해 주세요.")
        if not measure:
            if len(measures) != 1:
                if not measures:
                    return _reply("제품 Semantic에서 해당 측정 항목을 확정하지 못했습니다. PC CD와 연결할 step_id·item_id를 Semantic에 등록한 뒤 다시 요청해 주세요.",
                                  context, query, error="measurement_unresolved")
                return _ask(context, query, "measurement", _measure_options(measures), "측정 항목의 Semantic 후보를 선택해 주세요.")
            measure = measures[0]
            query["measurement"] = _measure_options(measures)[0]
        y = next((names[c.casefold()] for c in [str(measure.get("value_column") or ""), "value", "fab_value"] if c.casefold() in names), "")
        if not y:
            raise ValueError("원본 측정값 열을 확인하지 못했습니다.")
        query.update(root=root, step_id=measure["step_id"], item_id=measure["item_id"], x=names["tkout_time"], y=y)
        where = f"{_q(names['step_id'])} = {_lit(measure['step_id'])} AND {_q(names['item_id'])} = {_lit(measure['item_id'])}"
        site_options = _site_options(fb, files, columns, where)
        site = query.get("site_filter")
        if site and not any(s["column"] == site["column"] and set(s["values"]) == set(site["values"]) for s in site_options):
            site = None
        if not site:
            if not site_options:
                return _reply("SITE/SHOT 행을 구분하는 열과 값을 확인하지 못했습니다. 요약값을 샷 측정값으로 섞어 그리지 않았습니다.", context, query, error="site_filter_unresolved")
            if len(site_options) > 1:
                return _ask(context, query, "site_filter", site_options, "샷 측정 행을 구분할 실제 열을 선택해 주세요.")
            site = site_options[0]
        query["site_filter"] = site
        where += f" AND {_q(site['column'])} IN ({', '.join(_lit(v) for v in site['values'])})"
        projection = list(dict.fromkeys([names[c] for c in required] + [y, site["column"]] + [names[c] for c in ("lot_id", "shot_x", "shot_y", "subitem_id") if c in names]))
        source = {"id": "q1", "root": root, "product": product,
                  "sql": f"SELECT {', '.join(_q(c) for c in projection)} WHERE {where} ORDER BY {_q(query['x'])} DESC"}
        sources, joins = [source], []
        split = (query.get("split_column") or {}).get("value", "")
        if recolor or split:
            from core import knob_resolution
            split_files = fb.source_data_files(root="ML_TABLE", product=product)
            if not split_files:
                raise ValueError("제품의 ML_TABLE을 찾지 못했습니다.")
            split_columns, _ = fb.duckdb_engine.inspect_files(split_files)
            candidates = [c for c in split_columns if c.upper().startswith("KNOB_")]
            if recolor:
                query.pop("split_column", None)
                resolution = knob_resolution.resolve(product, text, candidates, user.get("username", ""))
                query["knob_alias"] = resolution["alias"]
                if resolution["exact"]:
                    split = resolution["exact"]
                else:
                    return _ask(context, query, "split_column", resolution["options"] or [{"label": c, "value": c} for c in candidates[:30]],
                                "요청한 Split이 명확하지 않습니다. ML_TABLE의 실제 Split 열을 선택해 주세요.")
                query["split_column"] = {"label": split, "value": split}
            if split not in candidates:
                raise ValueError("선택한 Split 열이 변경되었습니다. 다시 선택해 주세요.")
            if choice and pending.get("kind") == "split_column":
                knob_resolution.remember(user.get("username", ""), product, query.get("knob_alias", ""), split, candidates)
            split_names = {c.casefold(): c for c in split_columns}
            if not all(k in split_names for k in ("root_lot_id", "wafer_id")):
                raise ValueError("ML_TABLE에 Root Lot·Wafer 결합 키가 없습니다.")
            sources.append({"id": "q2", "root": "ML_TABLE", "product": product,
                            "sql": f"SELECT {_q(split_names['root_lot_id'])}, {_q(split_names['wafer_id'])}, {_q(split)}"})
            joins = [{"left": "q1", "right": "q2", "left_on": "root_lot_id,wafer_id",
                      "right_on": f"{split_names['root_lot_id']},{split_names['wafer_id']}", "how": "left"}]
        title = f"{product} {measure.get('term') or measure.get('item_desc') or measure['item_id']} 샷별 Trend"
        chart = {"type": "scatter", "x": query["x"], "y": y, "title": title, "color": split, "show_legend": True, "aggregation": "raw"}
        result = fb.chart_builder_run(fb.ChartBuilderRunReq(sources=sources, joins=joins, max_rows=LIMIT,
                                                          chart=chart, chart_name=title + (f" · {split}" if split else ""), save_history=True), request)
        rows = (result.get("joined") or {}).get("rows") or []
        saved = result.get("saved_chart") or {}
        query["history_id"] = saved.get("id", "")
        context.pop("pending_inline_chart", None)
        context.update(inline_chart_query=query, last_action="inline.shot_trend")
        points = [{**r, "x": r.get(query["x"]), "y": r.get(y), "color_value": r.get(split) if split else None} for r in rows]
        explanation = f"{product}의 {measure.get('term') or measure['item_id']}를 원본 INLINE의 {measure['step_id']} / {measure['item_id']}로 찾고, {site['column']}의 SITE/SHOT 행만 사용해 {query['x']}에 따른 개별 측정값을 산점도로 보는 요청으로 이해했습니다."
        if split:
            explanation += f" ML_TABLE의 {split}을 root_lot_id·wafer_id로 LEFT JOIN해 색상을 구분하며, 미매칭 측정점도 유지합니다."
        warnings = list(result.get("warnings") or [])
        if not saved:
            warnings.append("조회는 완료했지만 차트생성 이력 저장을 확인하지 못했습니다.")
        tool = {"action": "inline.shot_trend", "sources": [f"{root}/{product}"] + ([f"ML_TABLE_{product}"] if split else []),
                "executed_sql": "\n\n".join(f"{s['id']}: {s['sql']}" for s in result.get("sources", [])),
                "definition_code": format_chart_builder_definition(sources=sources, joins=joins, max_rows=LIMIT, chart=chart),
                "chart_result": {**chart, "chart_type": "scatter", "x_type": "date", "x_label": query["x"], "y_label": y, "color_by": split, "points": points},
                "table": {"columns": result["joined"]["columns"], "rows": rows, "total": len(rows)},
                "saved_chart": saved, "warnings": warnings, "joins": result.get("joins", []),
                "interpretation": {"summary": explanation, "origin": "확정된 Semantic·실제 스키마·실행 계획", "status": "completed",
                    "details": [{"label": "측정 항목", "value": f"{measure['step_id']} / {measure['item_id']}"},
                                {"label": "행 필터", "value": site['label'] + " · SUM/RANGE 등 요약 행 제외"},
                                {"label": "표현", "value": f"Scatter · X: {query['x']} · Y: {y} · 집계 없음"},
                                {"label": "색상 결합", "value": f"{split} · LEFT JOIN · root_lot_id + wafer_id" if split else "아직 지정하지 않음"},
                                {"label": "저장", "value": f"차트생성 이력 {saved['id']} · Template report 재사용 가능" if saved else "이력 저장 미확인"}], "unresolved": []}}
        return _reply(f"원본 샷 측정값 {len(rows):,}개를 표시했습니다." + (" 차트생성 이력에 저장했습니다." if saved else " 이력 저장을 확인하지 못했습니다.") +
                      (f" 최대 {LIMIT:,}개 최신 측정점을 조회합니다." if len(rows) >= LIMIT else ""), context, query, **tool)
    except (HTTPException, ValueError) as exc:
        context.pop("pending_inline_chart", None)
        return _reply(str(exc.detail) if isinstance(exc, HTTPException) else str(exc), context, query, error="inline_chart_failed")
