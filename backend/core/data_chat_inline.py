"""Product-scoped Inline values and time trends from projected ML_TABLE columns."""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher

import polars as pl

from core import product_semantics as semantic, semantic_measure_catalog, measurement_family
from core.ml_table_lookup import resolve_ml_table_file

LIMIT = 5000
INTENT = re.compile(r"\binline\b|인라인|\btrend\b|트렌드|추이|\bVM\b|\bIM\b|가상\s*계측|\bL1\b|L1값", re.I)
OTHER = re.compile(r"스플릿|split|knob|노브|위키|wiki|의미|뜻|위치|어디|\bteg\b|수율|yield|대시보드|dashboard|도착|언제쯤|\beta\b|완료\s*시각|[xy]\s*축|폰트|범례|높이|너비", re.I)
LOT = re.compile(r"(?<![\w.])([A-Za-z][A-Za-z0-9]{3,}\.[A-Za-z0-9]+)(?![\w.])")


def _norm(value):
    return re.sub(r"[\s_-]+", " ", str(value or "")).strip().casefold()


def _columns(row, columns, family="INLINE"):
    names = {str(row.get(k) or "").strip() for k in ("item_id", "item_desc", "term")}
    names.discard("")
    expected = {_norm(f"{family}_{name}{suffix}") for name in names for suffix in ("", "_avg", "_mean")}
    return [c for c in columns if _norm(c) in expected]


def _query_text(text, product, lot):
    for token in (product, lot):
        if token:
            text = re.sub(r"(?<![\w])" + re.escape(token) + r"(?![\w])", " ", text, flags=re.I)
    text = INTENT.sub(" ", text)
    text = measurement_family.strip_source_words(text)
    text = re.sub(r"보여\s*줘|보여주세요|보여|알려\s*줘|조회|검색|찾아\s*줘|그려\s*줘|산점도|scatter(?:\s+plot)?|\bshow\b|\bplot\b", " ", text, flags=re.I)
    return _norm(text.strip(" ?.!"))


def _candidates(product, text, query, columns, family="INLINE"):
    # Confirmed aliases win over observed vocabulary and fuzzy matching.
    catalog = [r for r in semantic_measure_catalog.match_terms(text, product=product)
               if str(r.get("source_type") or "").upper() == family
               and _norm(r.get("product")) == _norm(product)]
    matches = [r for r in semantic.resolve_terms(product, text)
               if r.get("kind") == "measurements" and str(r.get("source_type") or "INLINE").upper() == family]
    confirmed = [r for r in matches if not str(r.get("reference_id") or "").startswith("item:")]
    matches = confirmed or catalog or matches
    rows = []
    for row in matches:
        rows.extend({**row, "column": column} for column in _columns(row, columns, family))
    if rows:
        return _unique(rows), "semantic"
    fallback_rows = list(semantic.load_inline_matching_rows(product)) if family == "INLINE" else []
    for column in columns:
        if column.upper().startswith(family + "_"):
            name = re.sub(r"_(?:avg|mean)$", "", column[len(family) + 1:], flags=re.I)
            fallback_rows.append({"step_id": "", "item_id": name, "item_desc": name, "source_type": family})
    for row in fallback_rows:
        names = [_norm(row.get(k)) for k in ("item_desc", "item_id", "step_desc") if row.get(k)]
        score = max((SequenceMatcher(None, query, name).ratio() for name in names), default=0)
        overlap = max((len(set(query.split()) & set(name.split())) / max(1, len(query.split())) for name in names), default=0)
        if query and (score >= .55 or overlap >= .5):
            rows.extend({**row, "column": c, "score": max(score, overlap)} for c in _columns(row, columns, family))
    rows.sort(key=lambda r: (-r["score"], r["column"]))
    return _unique(rows)[:20], "Inline_matching.csv / ML_TABLE" if family == "INLINE" else "ML_TABLE VM columns"


def _unique(rows):
    seen, output = set(), []
    for row in rows:
        key = (row.get("step_id"), row.get("item_id"), row["column"])
        if key not in seen:
            seen.add(key)
            output.append(row)
    return output


def _answer(message, context, **tool):
    from core.data_chat import reply
    return reply(message, context=context, ok=not bool(tool.get("missing") or tool.get("error")),
                 tool={"feature": "inline", "action": "inline.values", **tool})


def _ask(context, query, kind, options, title):
    context["pending_inline"] = {"query": query, "kind": kind, "options": options}
    return _answer(title, context, missing=[kind], clarification={
        "kind": kind, "title": title, "options": options, "allow_other": True,
        "placeholder": "후보 번호 또는 실제 이름을 입력하세요"},
        table={"columns": ["번호", "후보"], "rows": [{"번호": i + 1, "후보": o["label"]} for i, o in enumerate(options)]})


def _scope(frame, columns, lot):
    by_name = {c.casefold(): c for c in columns}
    if not lot:
        return frame, ""
    exact = next((by_name[k] for k in ("fab_lot_id", "lot_id") if k in by_name), "")
    if "." in lot:
        if not exact:
            return None, "ML_TABLE에 FAB LOT ID 열이 없어 해당 하위 LOT만 구분할 수 없습니다. Root LOT 전체 조회로 진행할까요?"
        return frame.filter(pl.col(exact).cast(pl.String).str.to_uppercase() == lot.upper()), ""
    root = by_name.get("root_lot_id")
    if not root:
        return None, "ML_TABLE에 root_lot_id 열이 없습니다."
    return frame.filter(pl.col(root).cast(pl.String).str.to_uppercase() == lot.upper()), ""


def dispatch(text, context, request=None):
    from core.data_chat import _product_question, available_product_names
    pending = context.get("pending_inline") or {}
    options = pending.get("options") or []
    is_choice = bool(re.fullmatch(r"(\d+)\s*번?[.!]?", text.strip()) or any(
        text.strip().casefold() in {str(o["value"]).casefold(), str(o["label"]).casefold()} for o in options))
    explicit = bool(INTENT.search(text))
    if OTHER.search(text) and not is_choice:
        context.pop("pending_inline", None)
        return None
    fresh = explicit or bool(re.search(r"보여|조회|알려|찾아|\bshow\b", text, re.I))
    if pending and fresh and not is_choice:
        context.pop("pending_inline", None)
        pending = {}
    context = dict(context)
    if pending and re.fullmatch(r"취소(?:해|해줘)?[.!\s]*", text):
        context.pop("pending_inline", None)
        return _answer("Inline 조회 조건 확인을 취소했습니다.", context)
    product = context.get("confirmed_product") or ""
    query = dict(pending.get("query") or {})
    choice = None
    if pending and (is_choice or not explicit):
        options = pending.get("options") or []
        ordinal = re.fullmatch(r"(\d+)\s*번?[.!]?", text.strip())
        selected = options[int(ordinal[1])-1] if ordinal and 0 < int(ordinal[1]) <= len(options) else None
        choices = [o for o in options if text.strip().casefold() in {str(o["value"]).casefold(), str(o["label"]).casefold()}]
        choice = selected or (choices[0] if len(choices) == 1 else None)
        if not choice:
            return _ask(context, query, pending["kind"], options, "표시된 후보의 번호 또는 이름을 선택해 주세요.")
        query[pending["kind"]] = choice.get("identity", choice["value"])
        context.pop("pending_inline", None)
    else:
        lot_match = LOT.search(text)
        lot = lot_match[1] if lot_match else ""
        if not lot:
            roots = [t for t in re.findall(r"(?<![\w.])[A-Z][A-Z0-9]{4}(?![\w.])", text)
                     if t.casefold() != product.casefold() and t not in {"TREND", "QUERY", "INLINE"} and not measurement_family.infer(t)]
            lot = roots[0] if len(roots) == 1 else ""
        clean = _query_text(text, product, lot)
        # Bare measurement requests (ABC CD 보여줘) also use this resolver.
        if not explicit and not re.search(r"보여|조회|알려|찾아|\bshow\b", text, re.I):
            return None
        if not product:
            if explicit or measurement_family.infer(text):
                return _product_question(text, context, available_product_names())
            return None
        query = {"product": product, "text": text, "term": clean, "lot": lot,
                 "trend": bool(re.search(r"trend|트렌드|추이", text, re.I)), "family": measurement_family.infer(text) or "INLINE"}
        previous = context.get("inline_query") or {}
        if explicit and not clean and previous.get("product") == product:
            query.update(text=previous["text"], term=previous["term"], lot=lot or previous.get("lot", ""))
            query["family"] = measurement_family.infer(text) or previous.get("family", "INLINE")
            if previous.get("inline_measure"):
                query["inline_measure"] = previous["inline_measure"]
    if query.get("product") != product:
        context.pop("pending_inline", None)
        return None
    path = resolve_ml_table_file(product=product)
    if not path:
        return _answer("해당 제품의 ML_TABLE 파일을 찾지 못했습니다.", context, error="ml_table_missing") if explicit or pending else None
    columns = list(pl.read_parquet_schema(path))
    family = query.get("family", "INLINE")
    candidates, source = _candidates(product, query["text"], query["term"], columns, family)
    if not candidates:
        return _answer(f"제품에 맞는 {family} 항목과 실제 ML_TABLE 열을 찾지 못했습니다. 항목명 또는 item_id를 알려 주세요.", context, error="inline_not_found") if explicit or pending or source == "semantic" or measurement_family.infer(text) else None
    options = [{"label": f"{r.get('item_desc') or r.get('term') or r['item_id']} · {r.get('step_id', '')} / {r.get('item_id', '')} · {r['column']}",
                "value": str(i + 1), "column": r["column"]} for i, r in enumerate(candidates)]
    if query.get("inline_measure"):
        # Bind selection to the observed identity, never to an untrusted column.
        selected = next((r for r in candidates if _identity(r) == query["inline_measure"]), None)
        if not selected:
            return _answer(f"선택한 {family} 연결이 변경되었습니다. 원래 요청을 다시 알려 주세요.", context, error="inline_changed")
    elif len(candidates) == 1 and source == "semantic":
        selected = candidates[0]
        query["inline_measure"] = _identity(selected)
    else:
        for option, row in zip(options, candidates):
            option["value"] = option["label"]
            option["identity"] = _identity(row)
        return _ask(context, query, "inline_measure", options, f"제품과 ML_TABLE에서 확인한 {family} 후보를 선택해 주세요.")
    column = selected["column"]
    times = [c for c in columns if re.search(r"(?:^|_)tkout_time(?:_|$)", c, re.I)]
    time_column = query.get("inline_time", "")
    if query["trend"]:
        if not times:
            return _answer("이 ML_TABLE에는 tkout_time 열이 없습니다.", context, error="inline_time_missing")
        if not time_column:
            mentioned = [c for c in times if c.casefold() in query["text"].casefold()]
            time_column = mentioned[0] if len(mentioned) == 1 else times[0] if len(times) == 1 else ""
        if time_column not in times:
            return _ask(context, query, "inline_time", [{"label": c, "value": c} for c in times], "어떤 Step의 tkout_time으로 Trend를 그릴까요?")
    lot = query.get("inline_lot_scope") or query["lot"]
    frame, scope_error = _scope(pl.scan_parquet(path), columns, lot)
    if frame is None:
        if "." in lot and any(c.casefold() == "root_lot_id" for c in columns):
            return _ask(context, query, "inline_lot_scope", [{"label": lot.split(".")[0][:5] + " Root LOT 전체", "value": lot.split(".")[0][:5]}], scope_error)
        return _answer(scope_error, context, error="inline_lot_missing")
    identifiers = [c for c in columns if c.casefold() in {"product", "root_lot_id", "lot_id", "fab_lot_id", "wafer_id"}]
    projection = list(dict.fromkeys(identifiers + [column] + ([time_column] if time_column else [])))
    frame = frame.select(projection).with_columns(pl.col(column).cast(pl.Float64, strict=False))
    frame = frame.filter(pl.col(column).is_not_null() & pl.col(column).is_finite())
    if time_column:
        value = pl.col(time_column).cast(pl.String).str.replace("T", " ").str.strip_chars()
        parsed = pl.coalesce([value.str.strptime(pl.Datetime, fmt, strict=False) for fmt in
                              ("%Y-%m-%d %H:%M:%S%.f", "%Y-%m-%d", "%Y%m%d%H%M%S")])
        frame = frame.with_columns(parsed.alias(time_column))
        frame = frame.filter(pl.col(time_column).is_not_null()).sort(time_column, descending=True)
    # Projection/predicate pushdown; never materialize the wide table.
    result = frame.limit(LIMIT + 1).collect()
    capped = result.height > LIMIT
    rows = json.loads(result.head(LIMIT).write_json())
    context.pop("pending_inline", None)
    context.update(inline_query=query, last_action=family.lower() + (".trend" if time_column else ".values"))
    tool = {"sources": [path.name, source], "table": {"columns": projection, "rows": rows, "total": len(rows), "truncated": capped},
            "query_scope": {"product": product, "lot": lot, "column": column, "tkout_time": time_column, "family": family, "aggregation": "avg" if family == "INLINE" else "stored"},
            "action": context["last_action"]}
    if time_column:
        tool.update(feature="chart", chart_result={"chart_type": "scatter", "type": "scatter", "x": time_column, "y": column,
                    "x_label": time_column, "y_label": column, "x_type": "date", "title": f"{product} · {column} Trend",
                    "points": [{**row, "x": row[time_column], "y": row[column]} for row in rows]})
    message = f"{product} · {lot or '전체 LOT'} · {column}: {len(rows):,}개 " + ("Inline 평균값" if family == "INLINE" else "VM 저장값") + (f"을 {time_column} 기준 산점도로 표시했습니다." if time_column else "입니다.")
    if not rows:
        message = "선택한 조건에 유효한 측정값" + ("과 tkout_time 쌍" if time_column else "") + "이 없습니다."
    if capped:
        message += f" 최대 {LIMIT:,}개" + ("(선택 시간축 최신순)" if time_column else "") + "만 표시합니다."
    return _answer(message, context, **tool)


def _identity(row):
    return json.dumps([row.get("step_id", ""), row.get("item_id", ""), row["column"]], ensure_ascii=False)
