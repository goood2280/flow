"""Read-only SplitTable history and explicit custom-set selection for home chat."""
import re

HISTORY = re.compile(r"변경\s*이력|수정\s*이력|히스토리|history|변경\s*내역|수정\s*내역", re.I)
CUSTOM = re.compile(r"커스텀|custom|세트", re.I)


def custom_question(text, context, customs, message=None):
    from core.data_chat import reply
    names = list(dict.fromkeys(str(c.get("name") or "").strip() for c in customs if c.get("name")))
    state = {**context, "pending_custom_selection": {"prompt": text, "names": names}}
    return reply(message or "조회할 커스텀 세트를 선택해 주세요.", context=state, ok=False,
                 tool={"missing": ["custom_set"], "clarification": {"kind": "custom_set",
                       "title": "커스텀 세트 선택", "options": [{"label": n, "value": n} for n in names[:5]],
                       "allow_other": True, "placeholder": "등록된 커스텀 세트 이름"}})


def resolve_custom(text, context, customs):
    """An exact saved set wins; ambiguous or unknown sets need a human choice."""
    names = [str(c.get("name") or "") for c in customs if c.get("name")]
    if context.pop("custom_selection_confirmed", False):
        name = context.get("custom_name")
        if name in names:
            return name, None
        return "", custom_question(text, context, customs, "선택한 커스텀 세트가 변경되었습니다. 다시 선택해 주세요.")
    exact = [name for name in names if re.search(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])", text, re.I)]
    exact = [n for n in exact if not any(n.casefold() != other.casefold() and n.casefold() in other.casefold() for other in exact)]
    if len(exact) == 1:
        return exact[0], None
    if len(exact) > 1:
        return "", custom_question(text, context, [c for c in customs if c.get("name") in exact], "여러 커스텀 세트가 일치합니다. 하나를 선택해 주세요.")
    if CUSTOM.search(text):
        return "", custom_question(text, context, customs)
    if re.search(r"전체|모든|기본|KNOB|노브", text, re.I):
        return "", None
    return str(context.get("custom_name") or "") if context.get("custom_name") in names else "", None


def history_result(product, source, root, request, context):
    from core.data_chat import reply
    from core.auth import current_user
    from routers import splittable
    current_user(request)
    data = splittable.get_history(product=source, root_lot_id=root, limit=200, offset=0,
                                 user="", action="", column="", wafer_id="", q="", since="", until="", has_reason=False)
    rows = []
    for item in reversed(data.get("history") or []):
        parts = str(item.get("cell") or "").split("|")
        rows.append({"시간": item.get("time"), "작성자": item.get("user"),
                     "Root Lot": item.get("root_lot_id") or (parts[0] if parts else ""),
                     "Wafer": item.get("wafer_id") or (parts[1] if len(parts) > 1 else ""),
                     "항목": item.get("column") or "|".join(parts[2:]), "작업": item.get("action"),
                     "변경 전": item.get("old"), "변경 후": item.get("new"), "변경 사유": item.get("reason") or ""})
    total = int(data.get("total") or 0)
    message = f"{product} · Root Lot {root}의 스플릿 변경 이력 {total}건입니다."
    if data.get("has_more"):
        message += f" 최신 {len(rows)}건을 표시했습니다."
    message += " 이력은 Root Lot 기준으로 저장됩니다."
    return reply(message, context={**context, "last_action": "splittable.history"}, tool={
        "feature": "splittable.history", "action": "splittable.history",
        "table": {"rows": rows, "columns": ["시간", "작성자", "Root Lot", "Wafer", "항목", "작업", "변경 전", "변경 후", "변경 사유"], "total": total},
        "context": {"product": product, "root_lot_id": root},
        "sources": ["SplitTable History · 저장된 변경 이력"], "warnings": [],
    })
