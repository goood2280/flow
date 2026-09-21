"""Read-only Split column -> saved condition -> current leading lot conversation."""
import re

from core import split_lead_tracker as source

LEAD = re.compile(r"선두|선행|가장\s*앞|제일\s*앞|앞선\s*랏", re.I)
SPLIT = re.compile(r"split|스플릿|knob|노브", re.I)
KINDS = re.compile(r"종류|고유값|조건.*(?:뭐|어떤|목록)|값.*목록", re.I)
SEARCH = re.compile(r"들어간|포함|검색|항목.*(?:보여|찾)|찾아", re.I)


def _norm(text):
    return re.sub(r"[\s_]+", " ", str(text)).strip().casefold()


def _literal(value, text):
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(_norm(value)) + r"(?![a-z0-9])", _norm(text)))


def matching_columns(product, text, columns):
    exact = [c for c in columns if _literal(c, text) or _literal(re.sub(r"^(?:KNOB|MASK|SPLIT)[_\s]+", "", c, flags=re.I), text)]
    exact = [c for c in exact if not any(c != other and _norm(c) in _norm(other) for other in exact)]
    if exact:
        return exact
    ignored = {product.casefold(), "split", "splittable", "table", "knob", "mask", "show", "lot", "wip", "custom", "set"}
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9_.-]*", text) if w.casefold() not in ignored]
    return [c for c in columns if words and all(_literal(w, c) or w.casefold() in c.casefold() for w in words)]


def _ask(context, operation, kind, options, message, column=""):
    from core.data_chat import reply
    context["pending_split_choice"] = {"operation": operation, "kind": kind, "options": options, "column": column}
    return reply(message, context=context, ok=False, tool={"feature": "splittable", "missing": [kind],
        "clarification": {"kind": kind, "title": "Split 항목 선택" if kind == "split_column" else "Split 조건 선택",
                          "options": [{"label": x, "value": x} for x in options[:5]], "allow_other": True,
                          "placeholder": "실제 항목명" if kind == "split_column" else "실제 조건값"}})


def dispatch(text, context, request=None):
    from core.data_chat import reply
    from core.data_chat_split import EDIT, ASSIGNMENT
    if EDIT.search(text) or ASSIGNMENT.search(text):
        return None
    pending = context.get("pending_split_choice") or {}
    previous = context.get("split_query") or {}
    is_lead = bool(LEAD.search(text))
    is_kinds = bool(KINDS.search(text) and (SPLIT.search(text) or previous))
    is_search = bool(SPLIT.search(text) and SEARCH.search(text))
    if not (pending or is_lead or is_kinds or is_search):
        return None
    if re.search(r"커스텀|custom|변경\s*이력|수정\s*이력", text, re.I):
        return None
    context = dict(context)
    product = context.get("confirmed_product") or context.get("product") or ""
    if not product:
        return None  # Central product scope asks using the SplitTable inventory.
    if previous.get("product") != product:
        previous = {}
    if re.fullmatch(r"취소(?:해|해줘)?[.!\s]*", text):
        context.pop("pending_split_choice", None)
        return reply("Split 조건 선택을 취소했습니다.", context=context)
    operation = "lead" if is_lead else "values" if is_kinds else pending.get("operation") or "columns"
    columns = source.get_product_split_columns(product)
    if not columns:
        return reply("해당 제품의 실제 Split 항목을 확인하지 못했습니다.", context=context, ok=False,
                     tool={"feature": "splittable", "error": "split_columns_unavailable"})
    selected_column = ""
    selected_value = ""
    if pending and not (is_lead or is_kinds or is_search):
        ordinal = re.fullmatch(r"\s*(\d+)\s*번?[.!]?\s*", text)
        choices = pending.get("options") or []
        candidate = choices[int(ordinal[1])-1] if ordinal and 0 < int(ordinal[1]) <= len(choices) else text.strip()
        matches = [v for v in choices if v.casefold() == candidate.casefold()]
        if len(matches) != 1:
            return _ask(context, operation, pending["kind"], choices, "표시된 후보를 선택하거나 실제 이름을 입력해 주세요.", pending.get("column", ""))
        if pending["kind"] == "split_column":
            selected_column = matches[0]
        else:
            selected_column, selected_value = pending["column"], matches[0]
        context.pop("pending_split_choice", None)
    elif pending:
        context.pop("pending_split_choice", None)
    matches = [selected_column] if selected_column else matching_columns(product, text, columns)
    if operation == "columns":
        context["split_query"] = {"product": product, "columns": matches}
        context["last_action"] = "splittable.columns"
        return reply(f"{product}에서 일치하는 Split 항목 {len(matches)}개입니다. 항목을 선택하면 조건 종류를 확인할 수 있습니다.", context=context, tool={
            "feature": "splittable", "action": "splittable.columns", "sources": ["실제 ML_TABLE Split 열"],
            "table": {"rows": [{"Split 항목": c} for c in matches], "columns": ["Split 항목"], "total": len(matches)},
            "split_candidates": [{"title": "조건 종류 보기", "candidates": [{"label": c, "prompt": f"{product} {c} Split 종류 보여줘"} for c in matches[:5]]}]})
    column = matches[0] if len(matches) == 1 else ""
    previous_column = previous.get("column")
    # An abbreviated follow-up retains the explicitly selected column. A new
    # full column name always wins, even if the previous column shared a keyword.
    if not column and previous_column in columns:
        if not matches or previous_column in matches:
            column = previous_column
    if not column:
        choices = matches or previous.get("columns") or columns
        return _ask(context, operation, "split_column", choices, "조회할 Split 항목을 선택해 주세요.")
    if column not in columns:
        return _ask(context, operation, "split_column", columns, "선택한 항목이 변경되었습니다. 실제 항목을 다시 선택해 주세요.")
    values = source.get_split_value_counts(product, column)
    names = [v["value"] for v in values]
    context["split_query"] = {"product": product, "column": column, "columns": matches or [column]}
    if not values:
        return reply("선택한 Split 항목의 실제 조건값을 확인하지 못했습니다.", context=context, ok=False,
                     tool={"feature": "splittable", "error": "split_values_unavailable"})
    if operation == "values":
        context["last_action"] = "splittable.values"
        return reply(f"{product} · {column}의 실제 조건 종류 {len(values)}개입니다.", context=context, tool={
            "feature": "splittable", "action": "splittable.values", "context": {"product": product, "split_col": column},
            "sources": ["실제 ML_TABLE Split 조건값"],
            "table": {"rows": [{"Split 조건": v["value"], "데이터 행 수": v["count"]} for v in values], "columns": ["Split 조건", "데이터 행 수"], "total": len(values)},
            "split_candidates": [{"title": "조건별 선행랏", "candidates": [{"label": v, "prompt": f"{product} {column} Split {v} 조건 가장 선행랏이 뭐야?"} for v in names[:5]]}]})
    matched_values = [v for v in names if _literal(v, text)]
    matched_values = [v for v in matched_values if not any(v != other and v.casefold() in other.casefold() for other in matched_values)]
    value = selected_value or (matched_values[0] if len(matched_values) == 1 else "")
    if not value:
        return _ask(context, "lead", "split_value", names, "선행랏을 확인할 실제 Split 조건을 선택해 주세요.", column)
    if column not in columns or value not in names:
        return _ask(context, "lead", "split_value", names, "선택한 조건이 변경되었습니다. 현재 조건을 다시 선택해 주세요.", column)
    result = source.find_split_leading_lot(product, column, value)
    context["split_query"].update(value=value)
    context["last_action"] = "splittable.leading_lot"
    return reply(result.get("message") or result.get("error") or "선행랏을 확인하지 못했습니다.", context=context, ok=bool(result.get("ok")), tool={
        "feature": "splittable", "action": "splittable.leading_lot", "table": result.get("table"),
        "context": {"product": product, "split_col": column, "split_val": value}, "lead_lot": result.get("lead_lot"),
        "sources": ["실제 ML_TABLE 조건과 WIP 최신 위치"], "warnings": result.get("warnings") or []})
