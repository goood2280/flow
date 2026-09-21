"""Bounded multi-question execution through the existing single-task harness."""
from copy import deepcopy
import logging
import re
import time

from core import data_chat, data_chat_features, flowi_routing, llm_usage

logger = logging.getLogger(__name__)
_DECISION = re.compile(r"승인|반영|삭제|\b(?:approve|apply|delete|commit)\b", re.I)


def _is_decision(text):
    from core import data_chat_split, data_chat_report
    return bool(_DECISION.search(text) or any(pattern.fullmatch(text) for pattern in
        (data_chat_split.APPROVE, data_chat_split.CANCEL, data_chat_report.APPROVE, data_chat_report.CANCEL)))


def split_questions(prompt):
    text = str(prompt).strip()
    # SQL, chart definitions and pasted plans have meaningful line boundaries.
    if re.search(r"```|\bSELECT\b|^Q\d+\s*$|^SQL\s*=|^CHART\s*$", text, re.I | re.M):
        return [text]
    pieces = re.split(r"(?<=[?？])(?![?？])\s*|\n+|\s+(?:그리고|또한|그다음|그\s*다음|and\s+then)\s+", text, flags=re.I)
    return [re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", part).strip() for part in pieces if part.strip()]


def _approved_plan(resolved, text, context):
    route = (resolved.get("rule") or {}).get("route", "auto")
    if route == "auto":
        return None
    schema = data_chat_features.ACTIONS[route]["parameters"]
    properties = schema["properties"]
    params = {}
    products = data_chat.available_product_names()
    matches = data_chat.product_candidates(text, products)
    product = matches[0] if len(matches) == 1 else context.get("confirmed_product", "")
    if "product" in properties and product:
        params["product"] = product
    lots = data_chat.extract_lot_tokens(text, products)
    # The typed Lot annotation wins over generic token heuristics.
    annotated_lots = [s["value"] for s in resolved.get("segments", []) if s["kind"] == "lot"]
    lot = (annotated_lots or lots or [""])[-1]
    same_product = not matches or product == context.get("confirmed_product")
    if not lot and same_product:
        lot = context.get("lot_id") or context.get("root_lot_id") or ""
    if lot:
        raw, root = data_chat.resolve_lot_scope(lot)
        if "lot_id" in properties:
            params["lot_id"] = raw
        if "root_lot_id" in properties:
            params["root_lot_id"] = root
    for key, pattern, minimum, maximum in (("days", r"(\d+)\s*일", 1, 365), ("hours", r"(\d+)\s*시간", 0, 8760)):
        match = re.search(pattern, text)
        if key in properties and match:
            params[key] = min(maximum, max(minimum, int(match[1])))
    if "issue_id" in properties:
        match = re.search(r"\bISS-[A-Za-z0-9-]+\b", text, re.I)
        if match:
            params["issue_id"] = match[0]
    return route, params


def _status(result):
    tool = result.get("tool") or {}
    state = result.get("context") or {}
    if tool.get("missing") or (tool.get("approval") or {}).get("status") == "pending" or any(
        state.get(k) for k in ("pending_product_prompt", "pending_semantic_selection", "pending_teg_selection", "pending_split_id", "pending_report_id", "pending_split_query", "pending_custom_selection", "pending_split_choice", "pending_eta")
    ):
        return "needs_input"
    if result.get("ok") is False or tool.get("ok") is False or tool.get("error") or tool.get("blocked"):
        return "failed"
    return "completed"


def _one(question, context, request, history):
    start = time.monotonic()
    resolved = flowi_routing.resolve(question)
    normalized = resolved.get("normalized_question") or question
    rule = resolved.get("rule") or {}
    products = data_chat.available_product_names() if rule else []
    normalized_products = data_chat.product_candidates(normalized, products) if rule else []
    original_products = data_chat.product_candidates(question, products) if rule else []
    declared_products = [name for s in resolved.get("segments", []) if s["kind"] == "product"
                         for name in data_chat.product_candidates(s["value"], products)]
    current_products = set(original_products + declared_products + [context.get("confirmed_product", "")])
    unknown_binding = bool(resolved.get("bindings", {}).get("product") and not normalized_products)
    with flowi_routing.trace_scope() as events:
        if resolved.get("ambiguous"):
            result = data_chat.reply("일치하는 관리자 해석이 여러 개입니다. 관리자에서 중복 규칙을 확인해 주세요.",
                                     context=context, ok=False, tool={"missing": ["routing_rule"], "candidates": resolved["candidates"]})
        elif rule and _is_decision(normalized):
            result = data_chat.reply("조회 질문을 변경 승인으로 바꾸는 규칙은 적용할 수 없습니다. 관리자 해석을 수정해 주세요.",
                                     context=context, ok=False, tool={"missing": ["routing_rule"]})
        elif rule and (unknown_binding or any(p not in current_products for p in normalized_products)):
            result = data_chat.reply("교정 질문의 제품을 현재 질문에서 확인할 수 없습니다. 실제 제품명 또는 관리자 제품 별칭을 확인해 주세요.",
                                     context=context, ok=False, tool={"missing": ["product"]})
        else:
            plan = _approved_plan(resolved, normalized, context) if rule else None
            # Existing tests/integrations keep the single-operation signature.
            kwargs = {"approved_plan": plan} if plan else {}
            result = data_chat.execute(normalized, context, request, history=history, **kwargs)
    tool = result.get("tool") or {}
    result["routing_trace"] = {
        "question": question, "normalized_question": normalized, "rule_id": rule.get("id", ""),
        "rule_title": rule.get("title", ""), "rule_updated_at": rule.get("updated_at"),
        "bindings": resolved.get("bindings", {}), "segments": resolved.get("segments", []),
        "route": rule.get("route", "auto"), "action": tool.get("action") or tool.get("feature") or "",
        "status": _status(result), "sources": tool.get("sources") or [], "missing": tool.get("missing") or [],
        "events": events, "elapsed_ms": round((time.monotonic() - start) * 1000),
    }
    return result


def execute(prompt, context, request, history=None):
    questions = split_questions(prompt)
    if not questions:
        return data_chat.reply("질문을 입력해 주세요.", context=context, ok=False)
    if len(questions) > flowi_routing.MAX_QUESTIONS:
        return data_chat.reply(f"한 번에 최대 {flowi_routing.MAX_QUESTIONS}개 질문을 처리합니다. {len(questions)}개 질문을 나누어 보내 주세요. 아직 실행하지 않았습니다.",
                               context=context, ok=False, tool={"missing": ["question_limit"]})
    results, active, paused = [], deepcopy(context), False
    with llm_usage.turn_budget(flowi_routing.MAX_LLM_CALLS) as usage:
        for question in questions:
            if paused:
                results.append({"question": question, "status": "deferred", "reason": "앞 질문의 확인·실패를 해결한 뒤 다시 요청해 주세요."})
                continue
            before = usage["llm_calls_used"]
            try:
                if len(questions) > 1 and (_is_decision(question) or active.get("pending_split_id") or active.get("pending_report_id")):
                    result = data_chat.reply("변경 승인·반영·삭제는 내용을 확인한 뒤 별도 메시지로 요청해 주세요.", context=active,
                                             ok=False, tool={"missing": ["separate_approval"]})
                else:
                    result = _one(question, active, request, history or [])
            except Exception:
                if len(questions) == 1:
                    raise
                logger.exception("Home question execution failed")
                result = data_chat.reply("이 질문 처리에 실패했습니다. 관리자에서 질문과 처리 경로를 확인해 주세요.",
                                         context=active, ok=False, tool={"error": "question_execution_failed"})
                result["routing_trace"] = {"question": question, "status": "failed", "action": "", "events": []}
            result["llm_calls_used"] = usage["llm_calls_used"] - before
            state = _status(result)
            active = result.get("context", active)
            results.append({"question": question, "status": state, "response": {k: v for k, v in result.items() if k != "context"}})
            paused = state != "completed"
        if len(questions) == 1:
            output = {**results[0]["response"], "context": active}
        else:
            last = next((r["response"] for r in reversed(results) if r.get("response")), {})
            completed = sum(r["status"] == "completed" for r in results)
            output = data_chat.reply(f"질문 {len(questions)}개 중 {completed}개 처리 완료. 질문별 결과를 확인하세요.",
                                     context=active, tool=last.get("tool"), ok=completed == len(questions))
            output.update(questions=results, batch={"total": len(questions), "completed": completed, "max_questions": flowi_routing.MAX_QUESTIONS})
        output["usage"] = {**usage, "llm_calls_remaining": usage["llm_call_limit"] - usage["llm_calls_used"], **llm_usage.snapshot()}
        return output
