"""기본지식에 설명된 단일 파일의 Trend·Corr 차트 (홈 챗).

관리자가 기본지식에 "AA_yld.csv 는 AA 의 yld 파일, tkouttimeA 가 tkout_time,
yld01 이 수율값"처럼 적으면 ``file_knowledge`` 가 실제 헤더와 대조한 카탈로그를
만든다. 여기서는 그 카탈로그만으로 측정 열을 고르고 ChartBuilder(DB_FILE 소스)로
실행한다 — 모델이 열 이름을 지어내는 경로는 없다. Corr 상대는 다른 파일 측정값
또는 ML_TABLE 의 Inline 평균 열(제품 Semantic)이며 Root Lot·Wafer 로 LEFT JOIN 한다.
"""
from __future__ import annotations

from copy import deepcopy
import re

from fastapi import HTTPException

from core import auth, file_knowledge
from core.chart_builder_definition import format_chart_builder_definition
from core.utils import SINGLE_FILE_ROOT

LIMIT = 5000
CHART = re.compile(r"trend|트렌드|추이|시계열|차트|chart|그래프|graph|plot|그려|산점|scatter|corr|상관", re.I)
CORR = re.compile(r"\bcorr(?:elation)?\b|상관|산점도|scatter|\bvs\.?(?![A-Za-z])", re.I)
INLINE = re.compile(r"(?<![A-Za-z0-9_])inline(?![A-Za-z0-9_])|인라인", re.I)
MAP = re.compile(r"맵|(?<![A-Za-z])map(?![A-Za-z])", re.I)
COLOR = re.compile(r"컬러|색상|색칠|색으로|color|colour|(?:lot|랏|wafer|웨이퍼)\s*별", re.I)
CANCEL = re.compile(r"취소(?:해|해줘)?[.!\s]*")
ROOT_KEY = "root_lot_key"


def _q(value):
    return "`" + str(value).replace("`", "``") + "`"


def _lit(value):
    return "'" + str(value).replace("'", "''") + "'"


def _mentioned(alias, text):
    alias = str(alias or "").strip()
    if len(alias) < 2:
        return None
    if re.search(r"[가-힣]", alias):
        index = text.casefold().find(alias.casefold())
        return (index, index + len(alias)) if index >= 0 else None
    match = re.search(r"(?<![A-Za-z0-9_])" + re.escape(alias) + r"(?![A-Za-z0-9_])", text, re.I)
    return match.span() if match else None


def _reply(message, context, query=None, **tool):
    from core.data_chat import reply
    if query:
        tool["query_scope"] = query
    return reply(message, context=context, ok=not bool(tool.get("missing") or tool.get("error")),
                 tool={"feature": "chart", "action": tool.pop("action", "file.chart"), **tool})


def _ask(context, query, kind, options, message):
    context["pending_file_chart"] = {"query": query, "kind": kind, "options": options}
    return _reply(message, context, query, missing=[kind], needs_input=True,
                  clarification={"kind": kind, "title": message, "options": options, "allow_other": False})


def _choice(text, options):
    ordinal = re.fullmatch(r"\s*(\d+)\s*번?[.!]?\s*", text)
    if ordinal and 0 < int(ordinal[1]) <= len(options):
        return options[int(ordinal[1]) - 1]
    folded = text.strip().casefold()
    matches = [o for o in options if folded in {str(o.get("value", "")).casefold(), str(o.get("label", "")).casefold()}]
    return matches[0] if len(matches) == 1 else None


def _key(entry, measure):
    return f"{entry['path']}|{measure['column']}"


def _label(entry, measure):
    product = f"{entry['product']} " if entry.get("product") else ""
    return f"{product}{measure['label']} · {entry['path']} / {measure['column']}"


def _pairs(text, entries):
    """글에 나온 측정 언급 → [(시작 위치, [(entry, measure) ...])]. 같은 자리의 여러 후보는 모호함."""
    spans: dict[tuple, list] = {}
    for entry in entries:
        file_span = _mentioned(entry["file"], text) or _mentioned(entry["file"].rsplit(".", 1)[0], text)
        hit = False
        for measure in entry["measures"]:
            found = [span for alias in measure["aliases"] if (span := _mentioned(alias, text))]
            if found:
                span = min(found)
                spans.setdefault(span, []).append((entry, measure))
                hit = True
        if file_span and not hit:
            spans.setdefault(file_span, []).extend((entry, measure) for measure in entry["measures"])
    # 겹치는 언급(“수율값” 안의 “수율”)은 긴 쪽 하나로 합친다.
    merged: list[list] = []
    for span in sorted(spans, key=lambda s: (s[0], -(s[1] - s[0]))):
        if merged and span[0] < merged[-1][0][1]:
            merged[-1][1].extend(p for p in spans[span] if p not in merged[-1][1])
            continue
        merged.append([span, list(spans[span])])
    return [(span[0], pairs) for span, pairs in merged]


def _product_filter(entries, text, context):
    """다른 제품을 말했거나 확인된 제품과 다른 파일은 뺀다(파일 이름을 직접 쓴 경우는 유지)."""
    from core.data_chat import available_product_names, product_candidates
    named = [e for e in entries if e.get("product") and _mentioned(e["product"], text)]
    if named:
        return [e for e in entries if e in named or _mentioned(e["file"], text)]
    flow_named = product_candidates(text, available_product_names())
    confirmed = str(context.get("confirmed_product") or "")
    scope = flow_named[:1] or ([confirmed] if confirmed else [])
    if not scope:
        return entries
    kept = []
    for entry in entries:
        if not entry.get("product") or _mentioned(entry["file"], text):
            kept.append(entry)
        elif product_candidates(entry["product"], scope) or entry["product"].casefold() == scope[0].casefold():
            kept.append(entry)
    return kept


def _keys(entry):
    """(root lot 열, wafer 열, 파생열) — Root Lot 이 없으면 fab lot 의 '.' 앞부분을 쓴다."""
    roles = entry["roles"]
    wafer = (roles.get("wafer_id") or {}).get("column")
    if not wafer:
        return "", "", []
    if roles.get("root_lot_id"):
        return roles["root_lot_id"]["column"], wafer, []
    if roles.get("lot_id"):
        lot = roles["lot_id"]["column"]
        return ROOT_KEY, wafer, [{"name": ROOT_KEY, "columns": [lot], "separator": ".",
                                  "operation": "split_prefix", "segments": 1}]
    return "", "", []


def _file_source(entry, columns, source_id, *, order="", days=0, where=""):
    sql = "SELECT " + ", ".join(_q(c) for c in dict.fromkeys(columns))
    if where:
        sql += f" WHERE {where}"
    if order:
        sql += f" ORDER BY {_q(order)} DESC"
    source = {"id": source_id, "root": SINGLE_FILE_ROOT, "product": entry["path"], "sql": sql}
    time_col = (entry["roles"].get("time") or {}).get("column")
    if days and time_col:
        source.update(runtime_recent_days=int(days), runtime_date_column=time_col)
    return source


def _product_where(fb, entry, warnings):
    column = (entry["roles"].get("product") or {}).get("column")
    if not column or not entry.get("product"):
        return ""
    from core.utils import resolve_db_single_file
    values = fb.duckdb_engine.distinct_values([resolve_db_single_file(entry["path"])], column, limit=1001)
    match = [v for v in values if str(v).casefold() == entry["product"].casefold()]
    if not match:
        warnings.append(f"{entry['file']}의 {column} 열에 {entry['product']} 값이 없어 제품으로 거르지 않았습니다.")
        return ""
    return f"{_q(column)} = {_lit(match[0])}"


def _run(fb, request, sources, joins, chart, title):
    result = fb.chart_builder_run(fb.ChartBuilderRunReq(
        sources=sources, joins=joins, max_rows=LIMIT, chart=chart, chart_name=title, save_history=True), request)
    joined = result.get("joined") or {}
    return result, joined.get("rows") or [], joined.get("columns") or []


def _actual(column, columns, source_id):
    return column if column in columns else f"{source_id}__{column}" if f"{source_id}__{column}" in columns else column


def _trend(query, context, request, entry, measure):
    from routers import filebrowser as fb
    roles = entry["roles"]
    time_col = (roles.get("time") or {}).get("column")
    if not time_col:
        return _reply(f"기본지식에 {entry['file']}의 시간 열 설명이 없어 Trend를 그릴 수 없습니다. "
                      "예: 'tkouttimeA 가 tkout_time'처럼 적어 주세요.", context, query,
                      action="file.trend", error="time_column_missing")
    warnings = []
    root_lot = (roles.get("root_lot_id") or {}).get("column")
    lot_col = root_lot or (roles.get("lot_id") or {}).get("column")
    wafer_col = (roles.get("wafer_id") or {}).get("column")
    color = ""
    if COLOR.search(query["prompt"]):
        color = lot_col or ""
        if not color:
            warnings.append("Lot 열 설명이 없어 색 구분 없이 그렸습니다.")
    columns = [time_col, measure["column"], *(c for c in (lot_col, (roles.get("lot_id") or {}).get("column"), wafer_col) if c)]
    source = _file_source(entry, columns, "file", order=time_col, days=query.get("days") or 0,
                          where=_product_where(fb, entry, warnings))
    title = f"{entry.get('product') or entry['file']} {measure['label']} Trend"
    chart = {"type": "scatter", "x": time_col, "y": measure["column"], "title": title,
             "aggregation": "raw", "fit": "none", "show_legend": bool(color), "color": color}
    result, rows, joined_columns = _run(fb, request, [source], [], chart, title)
    saved = result.get("saved_chart") or {}
    warnings += list(result.get("warnings") or [])
    if not saved:
        warnings.append("조회는 완료했지만 차트생성 이력 저장을 확인하지 못했습니다.")
    points = [{**row, "x": row.get(time_col), "y": row.get(measure["column"]),
               "color_value": row.get(color) if color else None} for row in rows]
    context.pop("pending_file_chart", None)
    context.update(file_chart_query={**query, "primary": _key(entry, measure), "history_id": saved.get("id", "")},
                   last_action="file.trend")
    period = f"최근 {query['days']}일 " if query.get("days") else ""
    return _reply(f"{entry['file']}의 {measure['label']}({measure['column']}) {period}측정값 {len(rows):,}개를 Trend로 표시했습니다."
                  + (f" 최신 {LIMIT:,}개까지만 조회합니다." if len(rows) >= LIMIT else ""),
                  context, query, action="file.trend", sources=[f"DB 파일 {entry['path']}"],
                  executed_sql=(result.get("sources") or [{}])[0].get("sql", source["sql"]),
                  definition_code=format_chart_builder_definition(sources=[source], joins=[], max_rows=LIMIT, chart=chart),
                  saved_chart=saved, warnings=warnings,
                  chart_result={**chart, "chart_type": "scatter", "x_type": "date",
                                "x_label": f"{time_col} ({(roles.get('time') or {}).get('meaning') or '시간'})",
                                "y_label": f"{measure['label']} ({measure['column']})", "color_by": color, "points": points},
                  table={"columns": joined_columns, "rows": rows, "total": len(rows)},
                  interpretation=_interpretation(
                      f"기본지식 v{query['knowledge_version']}의 파일 설명대로 {entry['path']}의 {measure['column']}을(를) "
                      f"{measure['label']}로, {time_col}을(를) 시간 축으로 써서 집계 없이 산점도로 그렸습니다.",
                      [("파일", entry["path"]), ("측정 열", f"{measure['column']} = {measure['label']}"),
                       ("시간 열", time_col), ("기간", f"최근 {query['days']}일" if query.get("days") else "전체(최신 순)"),
                       ("색상", color or "없음"), ("저장", f"차트생성 이력 {saved['id']}" if saved else "이력 저장 미확인")]))


def _interpretation(summary, details):
    return {"summary": summary, "origin": "관리자 기본지식 파일 설명 · 실제 파일 헤더 · ChartBuilder", "status": "completed",
            "details": [{"label": label, "value": str(value)} for label, value in details], "unresolved": []}


def _fit_reply(query, context, request, *, sources, joins, x, y, x_label, y_label, title, action, source_names, summary, details):
    from routers import filebrowser as fb
    from core.data_chat_et_chart import _complete_point, _linear_fit
    chart = {"type": "scatter", "x": x, "y": y, "title": title, "aggregation": "raw", "fit": "linear", "show_legend": False}
    result, rows, columns = _run(fb, request, sources, joins, chart, title)
    x_col, y_col = _actual(x, columns, sources[-1]["id"]), _actual(y, columns, sources[-1]["id"])
    if x_col != x or y_col != y:
        chart.update(x=x_col, y=y_col)
    fit, pairs = _linear_fit(rows, x_col, y_col)
    points = [point for row in rows if (point := _complete_point(row, x_col, y_col))]
    table_rows = [{**row, "pair_status": "complete" if row.get(x_col) is not None and row.get(y_col) is not None else "unmatched"}
                  for row in rows]
    saved = result.get("saved_chart") or {}
    warnings = list(result.get("warnings") or [])
    if len(pairs) < 2:
        warnings.append("두 값이 모두 있는 행이 2개 미만이라 선형 적합과 R²를 계산하지 못했습니다. Lot·Wafer 표기(예: 01과 1)가 같은지 확인해 주세요.")
    if not saved:
        warnings.append("조회는 완료했지만 차트생성 이력 저장을 확인하지 못했습니다.")
    context.pop("pending_file_chart", None)
    context.update(file_chart_query={**query, "history_id": saved.get("id", "")}, last_action=action)
    r2 = f" R²={fit['r2']:.4f}." if fit else " R²는 계산할 수 없습니다."
    unmatched = len(rows) - len(pairs)
    return _reply(f"완전한 쌍 {len(pairs):,}개로 Corr chart를 만들었습니다.{r2}"
                  + (f" 짝이 없는 행 {unmatched:,}개도 표에 남겼습니다." if unmatched else ""),
                  context, query, action=action, sources=source_names,
                  executed_sql="\n\n".join(f"{row.get('id')}: {row.get('sql')}" for row in result.get("sources", [])),
                  definition_code=format_chart_builder_definition(sources=sources, joins=joins, max_rows=LIMIT, chart=chart),
                  saved_chart=saved, warnings=warnings, joins=result.get("joins", []), fit=fit,
                  evidence={"complete_pair_count": len(pairs), "unmatched_count": len(rows) - len(pairs), "r2": fit.get("r2")},
                  chart_result={**chart, "chart_type": "scatter", "x_label": x_label, "y_label": y_label,
                                "points": points, "fit": fit, "linear_fit": fit},
                  table={"columns": [*columns, "pair_status"], "rows": table_rows, "total": len(table_rows)},
                  interpretation=_interpretation(summary, [*details, ("완전한 쌍", len(pairs)),
                                                          ("선형 적합", fit.get("equation", "계산 불가")),
                                                          ("R²", fit.get("r2", "계산 불가"))]))


def _file_corr(query, context, request, first, second):
    (e1, m1), (e2, m2) = first, second
    days = query.get("days") or 0
    if e1["path"] == e2["path"]:
        source = _file_source(e1, [m1["column"], m2["column"], *(c for c in _keys(e1)[:2] if c and c != ROOT_KEY)], "file", days=days)
        sources, joins = [source], []
        join_text = "같은 파일의 같은 행"
    else:
        k1, k2 = _keys(e1), _keys(e2)
        missing = [e["file"] for e, k in ((e1, k1), (e2, k2)) if not k[0]]
        if missing:
            return _reply("기본지식에 Lot·Wafer 열 설명이 없어 두 파일을 결합할 수 없습니다: " + ", ".join(missing)
                          + ". 예: 'LOTID 는 fab lot, WF 는 wafer 번호'.", context, query, action="file.correlation", error="join_keys_missing")
        lot1 = [(e1["roles"].get("lot_id") or {}).get("column")] if k1[2] else [k1[0]]
        lot2 = [(e2["roles"].get("lot_id") or {}).get("column")] if k2[2] else [k2[0]]
        s1 = _file_source(e1, [*lot1, k1[1], m1["column"]], "f1", days=days)
        s2 = _file_source(e2, [*lot2, k2[1], m2["column"]], "f2", days=days)
        if k1[2]:
            s1["derived_columns"] = k1[2]
        if k2[2]:
            s2["derived_columns"] = k2[2]
        sources = [s1, s2]
        joins = [{"left": "f1", "right": "f2", "left_on": f"{k1[0]},{k1[1]}", "right_on": f"{k2[0]},{k2[1]}", "how": "left"}]
        join_text = f"{e1['file']} LEFT JOIN {e2['file']} · Root Lot + Wafer"
    title = f"{m1['label']} vs {m2['label']} Corr"
    return _fit_reply(query, context, request, sources=sources, joins=joins, x=m1["column"], y=m2["column"],
                      x_label=f"{m1['label']} ({m1['column']})", y_label=f"{m2['label']} ({m2['column']})",
                      title=title, action="file.correlation", source_names=list(dict.fromkeys(f"DB 파일 {e['path']}" for e in (e1, e2))),
                      summary=f"기본지식 파일 설명으로 두 측정값을 찾아 {join_text} 기준으로 짝지었습니다.",
                      details=[("X", f"{e1['path']} / {m1['column']} = {m1['label']}"),
                               ("Y", f"{e2['path']} / {m2['column']} = {m2['label']}"), ("결합", join_text)])


def _inline_corr(query, context, request, entry, measure):
    from core import data_chat_inline
    from core.data_chat import available_product_names, product_candidates
    from routers import filebrowser as fb
    root_col, wafer_col, derived = _keys(entry)
    if not root_col:
        return _reply(f"기본지식에 {entry['file']}의 Lot·Wafer 열 설명이 없어 Inline과 결합할 수 없습니다. "
                      "예: 'LOTID 는 fab lot, WF 는 wafer 번호'.", context, query,
                      action="file.inline_correlation", error="join_keys_missing")
    products = available_product_names()
    product = query.get("ml_product") if query.get("ml_product") in products else ""
    if not product:
        found = product_candidates(entry["product"], products) if entry.get("product") else []
        confirmed = str(context.get("confirmed_product") or "")
        # 파일에 제품이 적혀 있는데 Flow 제품과 연결되지 않으면 추측하지 않고 묻는다.
        product = found[0] if len(found) == 1 else confirmed if not entry.get("product") and confirmed in products else ""
    if not product:
        with_ml = [p for p in products if fb.source_data_files(root="ML_TABLE", product=p)]
        if not with_ml:
            raise ValueError("Inline 평균 열이 있는 ML_TABLE 제품이 없습니다.")
        return _ask(context, query, "ml_product", [{"label": p, "value": p} for p in with_ml[:40]],
                    f"Inline 값을 가져올 Flow 제품(ML_TABLE)을 선택해 주세요. 파일 제품: {entry.get('product') or '미기재'}")
    query["ml_product"] = product
    files = fb.source_data_files(root="ML_TABLE", product=product)
    if not files:
        raise ValueError(f"{product}의 ML_TABLE을 찾지 못했습니다.")
    columns, _ = fb.duckdb_engine.inspect_files(files)
    names = {c.casefold(): c for c in columns}
    if not all(k in names for k in ("root_lot_id", "wafer_id")):
        raise ValueError("ML_TABLE에 Root Lot·Wafer 결합 키가 없습니다.")
    clean = query["prompt"]
    for token in [entry["file"], entry.get("product") or "", *measure["aliases"], *entry.get("topics", [])]:
        if token and len(token) >= 2:
            clean = re.sub(re.escape(token), " ", clean, flags=re.I)
    clean = CORR.sub(" ", INLINE.sub(" ", clean))
    clean = re.sub(r"(?:와|과|랑|하고|및)(?=\s)|\b(?:and|with)\b", " ", clean)
    candidates, _ = data_chat_inline._candidates(product, query["prompt"], data_chat_inline._query_text(clean, product, ""), columns, "INLINE")
    # 여러 step 의 Semantic 이 같은 ML_TABLE 평균 열을 가리키면 고를 필요가 없다.
    candidates = list({row["column"]: row for row in reversed(candidates)}.values())[::-1]
    selected = next((r for r in candidates if data_chat_inline._identity(r) == query.get("inline_measure")), None)
    if not selected:
        if len(candidates) == 1:
            selected = candidates[0]
        elif candidates:
            options = [{"label": f"{r.get('item_desc') or r.get('term') or r.get('item_id')} · {r.get('step_id', '')} / {r.get('item_id', '')} · {r['column']}",
                        "value": data_chat_inline._identity(r)} for r in candidates]
            return _ask(context, query, "inline_measure", options, "Corr에 쓸 Inline 항목을 선택해 주세요.")
        else:
            return _reply("상관 대상을 찾지 못했습니다. Inline 항목 이름(예: 3.0 VTN)이나 기본지식에 설명된 다른 파일 측정값을 함께 적어 주세요.",
                          context, query, action="file.inline_correlation", error="corr_target_unresolved")
    query["inline_measure"] = data_chat_inline._identity(selected)
    inline_col = selected["column"]
    lot_cols = [(entry["roles"].get("lot_id") or {}).get("column")] if derived else [root_col]
    warnings_holder: list[str] = []
    file_source = _file_source(entry, [*lot_cols, wafer_col, measure["column"]], "file", days=query.get("days") or 0,
                               where=_product_where(fb, entry, warnings_holder))
    if derived:
        file_source["derived_columns"] = derived
    inline_source = {"id": "inline", "root": "ML_TABLE", "product": product,
                     "sql": f"SELECT {_q(names['root_lot_id'])}, {_q(names['wafer_id'])}, {_q(inline_col)}"}
    joins = [{"left": "file", "right": "inline", "left_on": f"{root_col},{wafer_col}",
              "right_on": f"{names['root_lot_id']},{names['wafer_id']}", "how": "left"}]
    inline_name = selected.get("item_desc") or selected.get("term") or selected.get("item_id") or inline_col
    root_text = f"{lot_cols[0]}의 '.' 앞부분" if derived else root_col
    reply = _fit_reply(query, context, request, sources=[file_source, inline_source], joins=joins,
                       x=inline_col, y=measure["column"], x_label=f"Inline {inline_name} ({inline_col})",
                       y_label=f"{measure['label']} ({measure['column']})",
                       title=f"{entry.get('product') or product} Inline {inline_name} vs {measure['label']} Corr",
                       action="file.inline_correlation", source_names=[f"DB 파일 {entry['path']}", f"ML_TABLE_{product}"],
                       summary=(f"기본지식 파일 설명으로 {entry['path']}의 {measure['column']}({measure['label']})을 찾고, "
                                f"ML_TABLE_{product}의 Inline 평균 열 {inline_col}을 Root Lot·Wafer로 LEFT JOIN했습니다."),
                       details=[("Y (파일)", f"{entry['path']} / {measure['column']} = {measure['label']}"),
                                ("X (Inline)", f"{inline_col} · Wafer 평균"),
                                ("결합 키", f"Root Lot = {root_text}, Wafer = {wafer_col}")])
    if warnings_holder:
        reply["tool"].setdefault("warnings", []).extend(warnings_holder)
    return reply


def _resolve(query, context, request):
    catalog = file_knowledge.catalog()
    query["knowledge_version"] = catalog["version"]
    entries = [e for e in catalog["entries"] if e["status"] == "ready"]
    by_key = {_key(e, m): (e, m) for e in entries for m in e["measures"]}
    prompt = query["prompt"]
    mentions = _pairs(prompt, _product_filter(entries, prompt, context))
    picks = []
    for index, (_, pairs) in enumerate(mentions):
        chosen = query.get(f"pick{index}")
        if chosen in by_key:
            picks.append(by_key[chosen])
        elif len(pairs) == 1:
            picks.append(pairs[0])
        else:
            options = [{"label": _label(e, m), "value": _key(e, m)} for e, m in pairs]
            return _ask(context, query, f"pick{index}", options, "기본지식에 설명된 파일 중 어느 측정값인지 선택해 주세요.")
    picks = list(dict.fromkeys((e["path"], m["column"]) for e, m in picks))
    picks = [by_key[f"{path}|{column}"] for path, column in picks]
    if not picks:
        return None
    if query["mode"] == "corr":
        if len(picks) >= 2:
            return _file_corr(query, context, request, picks[0], picks[1])
        return _inline_corr(query, context, request, *picks[0])
    return _trend(query, context, request, *picks[0])


def dispatch(text, context, request=None):
    pending = context.get("pending_file_chart") or {}
    state = deepcopy(context)
    if pending:
        if CANCEL.fullmatch(text):
            state.pop("pending_file_chart", None)
            return _reply("파일 차트 조건 확인을 취소했습니다.", state)
        option = _choice(text, pending.get("options") or [])
        if not option:
            # 번호가 아닌 새 요청이면 대기 상태를 버리고 다른 처리기로 넘긴다.
            state.pop("pending_file_chart", None)
            context.pop("pending_file_chart", None)
            return dispatch(text, context, request) if CHART.search(text) else None
        query = deepcopy(pending.get("query") or {})
        query[pending["kind"]] = option["value"]
        state.pop("pending_file_chart", None)
    else:
        if not CHART.search(text) or (MAP.search(text) and not re.search(r"trend|트렌드|추이|corr|상관", text, re.I)):
            return None
        catalog = file_knowledge.catalog()
        entries = [e for e in catalog["entries"] if e["status"] == "ready"]
        if not entries or not _pairs(text, _product_filter(entries, text, context)):
            return None
        day = re.search(r"(\d+)\s*일", text)
        query = {"mode": "corr" if CORR.search(text) else "trend", "prompt": text,
                 "days": max(1, min(3650, int(day[1]))) if day else 0}
    user = auth.current_user(request)
    tabs = auth.effective_permissions(user).get("tabs") or []
    if user.get("role") != "admin" and tabs != "*" and "*" not in tabs and "chartbuilder" not in tabs:
        return _reply("차트생성 권한이 필요합니다.", state, query, action="file.chart.permission",
                      error="permission_denied", blocked=True)
    try:
        return _resolve(query, state, request)
    except (HTTPException, ValueError) as exc:
        state.pop("pending_file_chart", None)
        message = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
        return _reply(f"파일 차트를 만들 수 없습니다: {message}", state, query, action="file.chart.failed", error="file_chart_failed")
