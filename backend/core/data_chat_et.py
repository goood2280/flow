"""Bounded ET operations for the deployed Home chat (no retired agent imports)."""
from copy import deepcopy
import re

from fastapi import HTTPException
from core import auth

TIME = re.compile(r"ET\s*(?:측정\s*시간|시간|time)", re.I)
DOWNLOAD = re.compile(r"\bET\s*(?:DATA|데이터)\b|ET데이터", re.I)
VERBS = re.compile(r"다운로드|download|뽑|추출|내려", re.I)
OTHER = re.compile(r"스플릿|split|대시보드|dashboard|위치|어디|인폼|inform|inline|인라인|트래커|tracker|도착|수율|yield|랏\s*관리|lot\s*(?:management|request)|watchlist", re.I)


def _mentioned(value, text):
    return bool(re.search(r"(?<![\w])" + re.escape(value) + r"(?![\w])", text, re.I))


def _reply(message, context, **tool):
    from core.data_chat import reply
    return reply(message, context=context, ok=not bool(tool.get("missing") or tool.get("error")), tool=tool)


def _ask(context, query, field, options, title):
    context["pending_et"] = {"query": query, "field": field, "options": options}
    return _reply(title, context, feature="ettime" if query["mode"] == "time" else "reformatize",
                  action="et.ask", missing=[field], needs_input=True,
                  clarification={"kind": "et_" + field, "title": title, "allow_other": True,
                                 "options": [{"label": str(v), "value": str(v)} for v in options]})


def dispatch(text, context, request=None):
    explicit = "time" if TIME.search(text) else "download" if DOWNLOAD.search(text) and VERBS.search(text) else ""
    pending = context.get("pending_et") or {}
    if not explicit and not pending:
        return None
    if not explicit and OTHER.search(text):
        context.pop("pending_et", None)
        context.pop("et_query", None)
        return None
    state = deepcopy(context)
    if pending and re.fullmatch(r"취소(?:해|해줘)?[.!\s]*", text):
        state.pop("pending_et", None)
        return _reply("ET 조회 조건 확인을 취소했습니다.", state)
    query = {"mode": explicit, "prompt": text} if explicit else dict(pending.get("query") or {})
    selection = text.strip()
    if not explicit and pending:
        ordinal = re.fullmatch(r"(\d+)\s*번?", selection)
        choices = pending.get("options") or []
        if ordinal and 0 < int(ordinal[1]) <= len(choices):
            selection = choices[int(ordinal[1]) - 1]
        if pending.get("field") in {"item", "lot"} and selection.casefold() in {str(v).casefold() for v in choices}:
            for candidate in choices:
                query["prompt"] = re.sub(re.escape(str(candidate)), " ", query.get("prompt", ""), flags=re.I)
    mode = query.get("mode")
    if mode not in {"time", "download"}:
        return None
    user = auth.current_user(request)
    permissions = auth.effective_permissions(user)
    tabs = permissions.get("tabs") or []
    required = "ettime" if mode == "time" else "reformatize"
    if user.get("role") != "admin" and tabs != "*" and "*" not in tabs and required not in tabs:
        state.pop("pending_et", None)
        return _reply("현재 계정에는 ET 기능 권한이 없습니다.", state, feature=required, error="permission_denied", blocked=True)
    from routers import et_time, reformatize
    try:
        products = (et_time.et_time_products(request, prefix="", limit=500).get("products") or []) if mode == "time" else [
            row["product"] for row in reformatize.products(user).get("products", [])]
        from core.data_chat import extract_lot_tokens, product_candidates, resolve_lot_scope
        matches = product_candidates(selection if not explicit else text, products)
        if len(matches) > 1:
            query.pop("product", None)
            return _ask(state, query, "product", matches, "조회할 ET 제품을 선택해 주세요.")
        if matches:
            if query.get("product") and query["product"] != matches[0]:
                query = {"mode": mode, "prompt": text}
            query["product"] = matches[0]
        named = re.search(r"(?:제품(?:명)?\s*[:=]?\s*|\bproduct\s*[:=]\s*)([A-Za-z][A-Za-z0-9_.-]*)", text, re.I)
        unknown = re.search(r"\bprod[a-z0-9_-]+\b", text, re.I)
        if (named and not product_candidates(named[1], products)) or (unknown and not product_candidates(unknown[0], products)):
            query.pop("product", None)
            return _ask(state, query, "product", products, "실제 ET 데이터에 등록된 제품을 선택해 주세요.")
        if not query.get("product") and not explicit and pending.get("field") == "product":
            selected = text.strip()
            options = pending.get("options") or []
            if re.fullmatch(r"\d+번?", selected):
                index = int(selected.rstrip("번")) - 1
                selected = options[index] if 0 <= index < len(options) else ""
            query["product"] = next((p for p in products if p.casefold() == selected.casefold()), "")
        product = query.get("product")
        if product not in products:
            query.pop("product", None)
            return _ask(state, query, "product", products, "실제 ET 데이터에 등록된 제품을 선택해 주세요.")
        full = text if explicit else query.get("prompt", "") + " " + selection
        # Remove known identifiers before extracting a lot; aliases may resemble lot IDs.
        aliases = []
        if mode == "download":
            aliases = [str(r.get("alias")) for r in reformatize.list_items(product, user).get("items", []) if r.get("alias")]
            item_text = selection if not explicit and pending.get("field") == "item" else full
            selected_items = [a for a in aliases if _mentioned(a, item_text)]
            if len(selected_items) == 1:
                query["item"] = selected_items[0]
            elif len(selected_items) > 1:
                query.pop("item", None)
                return _ask(state, query, "item", selected_items, "다운로드할 ET 항목 하나를 선택해 주세요.")
        lot_text = selection if not explicit and pending.get("field") == "lot" else full
        for value in sorted([*products, *aliases], key=len, reverse=True):
            lot_text = re.sub(re.escape(value), " ", lot_text, flags=re.I)
        lots = [v for v in extract_lot_tokens(lot_text)
                if (any(c.isdigit() for c in v) or len(v) == 5)
                and v.upper() not in {"DATA", "TIME", "TREND", "MONTH", "ALIAS", "ITEMS"}]
        if len(set(lots)) > 1:
            return _ask(state, query, "lot", list(dict.fromkeys(lots)), "조회할 Lot 하나를 선택해 주세요.")
        if lots:
            query["lot"] = lots[0].upper()
        day = re.search(r"(\d+)\s*일", selection if not explicit and pending.get("field") == "scope" else full)
        if day:
            query["days"] = max(1, min(3660, int(day[1])))
        if mode == "download":
            if not query.get("days") and not query.get("lot"):
                return _ask(state, query, "scope", ["최근 3일", "최근 5일", "최근 7일", "최근 30일"], "기간(예: 최근 5일) 또는 Lot ID를 알려 주세요.")
            if query.get("item") not in aliases:
                return _ask(state, query, "item", aliases, "다운로드할 ET 항목을 선택해 주세요.")
            job = reformatize.download_start(reformatize.DownloadJobReq(
                product=product, items=[query["item"]], days=query.get("days", 0), lot_filter=query.get("lot", "")), user)
            job_id = str(job.get("job_id") or "")
            if not job_id:
                raise ValueError("다운로드 작업 ID가 없습니다.")
            tool = {"feature": "reformatize", "action": "reformatize.download.start", "slots": query,
                    "sources": ["/api/reformatize/download/start"],
                    "table": {"columns": ["product", "item", "job_id", "status"], "rows": [
                        {"product": product, "item": query["item"], "job_id": job_id, "status": job.get("status", "queued")}], "total": 1},
                    "download_job": {"job_id": job_id, "filename": f"{product}_reformatize.csv",
                        "status_url": f"/api/reformatize/download/status?job_id={job_id}",
                        "file_url": f"/api/reformatize/download/file?job_id={job_id}"}}
            message = f"{product} {query['item']} ET 다운로드 작업을 등록했습니다."
        else:
            trend = bool(re.search(r"추이|trend|개월", full, re.I))
            if trend:
                months = re.search(r"(\d+)\s*개월", full)
                months = max(1, min(120, int(months[1]))) if months else 12
                payload = et_time.et_time_trend(request, product=product, months=months)
                rows = [{"step_id": step, **p} for step, points in (payload.get("trend") or {}).items() for p in points]
                tool = {"feature": "ettime", "action": "et_time.trend", "sources": ["/api/et-time/trend"],
                        "slots": {"product": product, "months": months},
                        "chart_result": {"chart_type": "scatter", "title": f"{product} ET 측정시간 추이", "x_label": "월", "x_type": "category", "y_label": "초", "color_by": "step_id",
                            "points": [{**r, "x": r["month"], "y": r["avg_duration_sec"], "color_value": r["step_id"]} for r in rows]}}
                latest = max((str(r.get("month") or "") for r in rows), default="")
                message = f"{product} 데이터의 최신 월({latest}) 기준 최근 {months}개월 ET 측정시간 추이입니다."
            else:
                if not query.get("lot"):
                    return _ask(state, query, "lot", [], "측정시간을 조회할 Lot ID를 알려 주세요.")
                lot, root = resolve_lot_scope(query["lot"])
                payload = et_time.et_time_measure(request, product=product, root_lot_id=root, lot_id=lot if "." in lot else "")
                rows = payload.get("rows") or []
                tool = {"feature": "ettime", "action": "et_time.measure", "sources": ["/api/et-time/measure"],
                        "slots": {"product": product, "root_lot_id": root, "lot_id": lot}}
                message = f"{product} {lot} ET 측정시간입니다."
            preferred = ["step_id", "pgm", "duration_text", "wafer_count", "month", "avg_duration_text"]
            columns = list(dict.fromkeys(k for r in rows for k in r))
            tool["table"] = {"columns": [k for k in preferred if k in columns] + [k for k in columns if k not in preferred], "rows": rows, "total": len(rows)}
            if not rows:
                message = "해당 조건의 ET 측정 데이터가 없습니다."
        state.pop("pending_et", None)
        state["et_query"] = query
        # Product is scoped to this ET request; clear artifacts from another product.
        if state.get("product") != product:
            state = {"et_query": query}
        else:
            for key in list(state):
                if key.startswith("pending_"):
                    state.pop(key, None)
        state.update(product=product, confirmed_product=product)
        return _reply(message, state, **tool)
    except (HTTPException, ValueError) as exc:
        state.pop("pending_et", None)
        message = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
        return _reply(f"ET 요청을 처리할 수 없습니다: {message}", state, feature=required, error="et_request_failed")
