def _teg_query_terms(prompt: str, product: str = "") -> list[str]:
    blocked = set(_STOP_TOKENS) | {
        "TEG", "SHOT", "WF", "WAFER", "MAP", "RADIUS", "POSITION", "LOCATION",
        "위치", "반경", "가장", "먼", "풀맵", "기준", "보여줘", "어디야",
    }
    blocked.update(_product_aliases(product))
    out: list[str] = []
    seen: set[str] = set()
    for tok in _query_tokens(prompt):
        key = _upper(tok)
        if not key or key in blocked:
            continue
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out[:8]


def _load_flowi_wafer_layout(product: str) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    raise RuntimeError("WF Layout is archived for rebuild.")


def _matching_tegs(tegs: list[dict[str, Any]], terms: list[str]) -> list[dict[str, Any]]:
    if not terms:
        return tegs
    out = []
    for teg in tegs:
        hay = _upper(" ".join([teg.get("id") or "", teg.get("label") or ""]))
        if any(term in hay for term in terms):
            out.append(teg)
    return out or tegs


def _is_teg_radius_prompt(prompt: str) -> bool:
    text = str(prompt or "")
    up = _upper(text)
    return "TEG" in up and any(t in text or t in up for t in ("RADIUS", "반경", "가장 먼", "먼게", "최외곽", "EDGE", "풀맵"))


def _handle_teg_radius_lookup(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _is_teg_radius_prompt(prompt):
        return {"handled": False}
    return {
        "handled": True,
        "intent": "teg_radius_lookup",
        "action": "archived",
        "answer": "WF Layout 기능은 archive/agent_reset_2026_05_26 으로 이동되어 새로 설계할 예정입니다.",
        "feature": "archived",
    }


def _is_teg_position_prompt(prompt: str) -> bool:
    text = str(prompt or "")
    up = _upper(text)
    return "TEG" in up and any(t in text or t in up for t in ("SHOT", "위치", "POSITION", "LOCATION", "어디"))


def _handle_teg_position_lookup(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _is_teg_position_prompt(prompt):
        return {"handled": False}
    from core import teg_map as _tm

    vehicles = [str(value or "").strip() for value in _tm.vehicles() if str(value or "").strip()]
    normalized_prompt = semantic_hitl.normalize_term(prompt)
    vehicle = next((value for value in vehicles if semantic_hitl.normalize_term(value) in normalized_prompt), "")
    if not vehicle:
        hint = _product_hint(prompt, product)
        hint_key = semantic_hitl.normalize_term(hint)
        vehicle = next((value for value in vehicles if semantic_hitl.normalize_term(value) == hint_key), "")
    if not vehicle:
        choices = [{
            "id": f"teg_vehicle_{idx}", "label": value, "title": value,
            "description": "이 vehicle의 TEG Shot 확대를 조회합니다.",
            "prompt": f"{value} TEG Shot 내 위치를 보여줘",
        } for idx, value in enumerate(vehicles[:12], start=1)]
        return {
            "handled": True, "intent": "teg_shot_position_lookup", "action": "clarify_vehicle",
            "feature": "teg", "answer": "어느 vehicle의 Shot을 볼지 선택해 주세요.",
            "missing": ["vehicle"], "clarification": {"question": "TEG vehicle을 선택해 주세요.", "choices": choices},
            "sources": [{"source": "TEG layout vehicle catalog", "path": "/api/teg-map/vehicles"}],
        }
    try:
        payload = _tm.map_payload(vehicle)
    except (FileNotFoundError, LookupError, ValueError) as exc:
        return {
            "handled": True, "intent": "teg_shot_position_lookup", "action": "map_unavailable",
            "feature": "teg", "answer": str(exc),
            "sources": [{"source": "TEG map", "path": f"/api/teg-map/map?vehicle={vehicle}"}],
        }
    tegs = [row for row in (payload.get("tegs") or []) if isinstance(row, dict)]
    exact = [row for row in tegs if semantic_hitl.normalize_term(row.get("teg")) in normalized_prompt]
    if not exact:
        terms = _teg_query_terms(prompt, vehicle)
        exact = _matching_tegs(tegs, terms) if terms else []
        if len(exact) == len(tegs):
            exact = []
    if not exact:
        choices = []
        for idx, row in enumerate(tegs[:20], start=1):
            teg = str(row.get("teg") or "").strip()
            if teg:
                choices.append({
                    "id": f"teg_{idx}", "label": teg, "title": teg,
                    "description": "이 TEG를 설정된 Shot 격자에서 표시합니다.",
                    "prompt": f"{vehicle} {teg}가 Shot 내 어디 있어?",
                })
        return {
            "handled": True, "intent": "teg_shot_position_lookup", "action": "clarify_teg",
            "feature": "teg", "answer": f"{vehicle}에서 표시할 TEG를 선택해 주세요.",
            "missing": ["teg"], "clarification": {"question": "어느 TEG를 볼까요?", "choices": choices},
            "sources": [{"source": "TEG location + Chip_Radius", "path": f"/api/teg-map/map?vehicle={vehicle}"}],
        }
    selected = exact[:max(1, min(int(max_rows or 12), 30))]
    selected_names = [str(row.get("teg") or "") for row in selected]
    rows = [{
        "teg": row.get("teg"), "ebeam_x_mm": row.get("ebeam_x"), "ebeam_y_mm": row.get("ebeam_y"),
        "width_mm": row.get("teg_w"), "height_mm": row.get("teg_h"), "direction": row.get("flat_zone"),
    } for row in selected]
    return {
        "handled": True, "intent": "teg_shot_position_lookup", "action": "teg_map.shot_zoom",
        "feature": "teg", "unit_ai": "teg", "answer": f"{vehicle}의 {', '.join(selected_names)} 위치를 Shot 설정 격자에 표시했습니다.",
        "teg_shot_view": {"vehicle": vehicle, "selected_tegs": selected_names, "map": payload},
        "table": {"kind": "teg_shot_coordinates", "title": "TEG Shot 좌표", "columns": _table_columns(list(rows[0])) if rows else [], "rows": rows, "total": len(rows)},
        "slots": {"product": vehicle, "item": selected_names},
        "interpretation_notes": [f"{vehicle}를 TEG vehicle, {', '.join(selected_names)}를 Shot 내부 TEG로 이해했습니다."],
        "sources": [{"source": "TEG location + Chip_Radius + vehicle display config", "path": f"/api/teg-map/map?vehicle={vehicle}"}],
    }


def _is_et_download_prompt(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    asks_download = "다운로드" in text or "download" in low or "csv" in low
    et_signal = "et" in low or "index" in low or "인덱스" in text or bool(re.search(r"\d{1,4}\s*일(?:치|간)?", text))
    return asks_download and et_signal


def _et_download_days(prompt: str) -> int:
    match = re.search(r"(\d{1,4})\s*일(?:치|간)?", str(prompt or ""))
    return max(0, min(int(match.group(1)), 3660)) if match else 0


def _handle_et_download_request(prompt: str, product: str, me: dict[str, Any], agent_context: dict[str, Any] | None) -> dict[str, Any]:
    if not _is_et_download_prompt(prompt):
        return {"handled": False}
    from routers import reformatize as _rf

    product_rows = (_rf.products().get("products") or [])
    products = [str(row.get("product") or "").strip() for row in product_rows if str(row.get("product") or "").strip()]
    normalized_prompt = semantic_hitl.normalize_term(prompt)
    selected_product = next((value for value in products if semantic_hitl.normalize_term(value) in normalized_prompt), "")
    if not selected_product:
        hint_key = semantic_hitl.normalize_term(_product_hint(prompt, product))
        selected_product = next((value for value in products if semantic_hitl.normalize_term(value) == hint_key), "")
    if not selected_product:
        choices = [{"id": f"et_product_{idx}", "label": value, "title": value,
                    "description": "이 제품의 ET index를 다운로드합니다.",
                    "prompt": f"{value} ET 최근 {_et_download_days(prompt) or 5}일치 다운로드해줘"}
                   for idx, value in enumerate(products[:12], start=1)]
        return {"handled": True, "intent": "et_download", "action": "clarify_product", "feature": "reformatize",
                "answer": "ET 다운로드 제품을 선택해 주세요.", "missing": ["product"],
                "clarification": {"question": "어느 제품인가요?", "choices": choices},
                "sources": [{"source": "DB ET product catalog", "path": "/api/reformatize/products"}]}
    items_payload = _rf.list_items(selected_product, user=me)
    items = [row for row in (items_payload.get("items") or []) if isinstance(row, dict)]
    aliases = [str(row.get("alias") or "").strip() for row in items if str(row.get("alias") or "").strip()]
    learning = (agent_context or {}).get("semantic_learning") if isinstance(agent_context, dict) else {}
    learned_item = str((learning or {}).get("item_id") or "").strip() if str((learning or {}).get("source_type") or "").upper() == "ET" else ""
    exact = learned_item if learned_item in aliases else next(
        (alias for alias in sorted(aliases, key=len, reverse=True) if semantic_hitl.normalize_term(alias) in normalized_prompt), "")
    candidate_term = ""
    candidates: list[str] = []
    if not exact:
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9_.-]{2,}", str(prompt or ""))
        blocked = {"DOWNLOAD", "CSV", "INDEX", "PRODUCT", "FLOW", "FLOWI"}
        tokens = [token for token in tokens if _upper(token) not in blocked and semantic_hitl.normalize_term(token) not in {semantic_hitl.normalize_term(selected_product), "et"}]
        scored: list[tuple[float, str, str]] = []
        for token in tokens:
            nt = semantic_hitl.normalize_term(token)
            for alias in aliases:
                na = semantic_hitl.normalize_term(alias)
                score = SequenceMatcher(None, nt, na).ratio()
                if nt and na and (nt in na or na in nt):
                    score = max(score, 0.78)
                if score >= 0.46:
                    scored.append((score, alias, token))
        scored.sort(key=lambda row: (-row[0], len(row[1]), row[1]))
        for _, alias, token in scored:
            if alias not in candidates:
                candidates.append(alias)
                candidate_term = candidate_term or token
            if len(candidates) >= 6:
                break
        if candidate_term:
            resolution = semantic_hitl.find_resolution(candidate_term, username=me.get("username") or "flowi", source_type="ET", product=selected_product)
            if resolution and int(resolution.get("confirmation_count") or 0) >= 3 and str(resolution.get("item_id") or "") in aliases:
                exact = str(resolution.get("item_id") or "")
    days = _et_download_days(prompt)
    if not days:
        return {"handled": True, "intent": "et_download", "action": "clarify_period", "feature": "reformatize",
                "answer": "재현 가능한 다운로드를 위해 기간을 알려주세요. 예: 최근 5일치",
                "missing": ["days"], "sources": [{"source": items_payload.get("vehicle_csv") or "reformatter CSV", "path": "/api/reformatize/items"}]}
    if not exact:
        if not candidates:
            candidates = aliases[:8]
            candidate_term = candidate_term or "ET index"
        choices = []
        for idx, alias in enumerate(candidates, start=1):
            marker = semantic_hitl.encode_choice({"term": candidate_term, "source_type": "ET", "product": selected_product,
                                                   "item_id": alias, "original_prompt": prompt,
                                                   "evidence": {"recent_days": days}})
            choices.append({"id": f"et_alias_{idx}", "label": alias, "title": alias,
                            "description": "이 alias로 ET 다운로드를 이어갑니다.", "prompt": marker})
        return {"handled": True, "intent": "et_download", "action": "clarify_alias", "feature": "reformatize",
                "answer": f"'{candidate_term}'와 정확히 같은 ET alias가 없습니다. 어떤 항목을 말씀하신 건가요?",
                "missing": ["et_alias"], "clarification": {"question": "ET alias를 선택해 주세요.", "choices": choices},
                "sources": [{"source": items_payload.get("vehicle_csv") or "reformatter CSV", "path": f"/api/reformatize/items?product={selected_product}"}]}
    req = _rf.DownloadJobReq(product=selected_product, items=[exact], days=days)
    job = _rf.download_start(req, user=me)
    job_id = str(job.get("job_id") or "")
    return {"handled": True, "intent": "et_download", "action": "reformatize.download.start", "feature": "reformatize", "unit_ai": "reformatize",
            "answer": f"{selected_product} {exact} 최근 {days}일 ET 다운로드를 대기열에 등록했습니다.",
            "download_job": {**job, "job_id": job_id, "filename": f"{selected_product}_reformatize.csv",
                             "status_url": f"/api/reformatize/download/status?job_id={job_id}",
                             "file_url": f"/api/reformatize/download/file?job_id={job_id}"},
            "semantic_resolution": {"source_type": "ET", "term": candidate_term or exact, "item_id": exact,
                                    "product": selected_product, "auto_applied": bool(candidate_term and not learned_item)},
            "slots": {"product": selected_product, "item": exact, "days": days},
            "interpretation_notes": [f"{selected_product}를 ET 제품, {exact}를 reformatter alias, {days}일을 tkout_time 기간으로 이해했습니다."],
            "sources": [{"source": items_payload.get("vehicle_csv") or "reformatter CSV", "path": f"/api/reformatize/items?product={selected_product}"},
                        {"source": "DB ET + reformatize download queue", "path": "/api/reformatize/download/start"}]}


def _is_et_time_prompt(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    return ("측정시간" in text or "측정 시간" in text or "et time" in low) and not _is_et_download_prompt(text)


def _et_time_months(prompt: str) -> int:
    match = re.search(r"(\d{1,3})\s*(?:개월|month)", str(prompt or ""), flags=re.I)
    return max(0, min(int(match.group(1)), 120)) if match else 0


def _flowi_internal_request(me: dict[str, Any], path: str) -> Request:
    request = Request({
        "type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
        "path": path, "raw_path": path.encode("utf-8"), "query_string": b"", "headers": [],
        "client": ("flowi", 0), "server": ("flowi", 80),
    })
    request.state.user = dict(me or {})
    return request


def _flowi_dashboard_wip_prompt(prompt: str) -> bool:
    """True when the user asks to see the app's current WIP dashboard.

    Metric/source-specific chart requests must continue through the normal
    chart builders; this fast path is only for requests such as "대시보드
    보여줘" or "show the WIP dashboard".
    """
    text = str(prompt or "").strip()
    low = text.lower()
    has_dashboard = "dashboard" in low or "대시보드" in text
    if not has_dashboard:
        return False
    has_show = any(term in low or term in text for term in (
        "show", "display", "view", "open", "보여", "띄워", "열어", "확인", "차트",
    ))
    if not has_show and low not in {"dashboard", "wip dashboard"} and text != "대시보드":
        return False
    # A named DB/metric means "build a chart from this source", not "show the
    # WIP dashboard". Those requests already have richer dedicated handlers.
    metric_hits = _metric_alias_hits(text)
    specific_metrics = [
        row for row in metric_hits
        if str((row or {}).get("metric") or "").upper() not in {"SHOW", "DISPLAY", "VIEW", "OPEN", "WIP", "DASHBOARD"}
    ]
    if _source_terms(text) or specific_metrics:
        return False
    return not any(term in low for term in (
        "scatter", "boxplot", "box plot", "wafer map", "correlation", "heatmap", "trend",
    ))


def _handle_flowi_dashboard_wip_view(
    prompt: str,
    product: str,
    me: dict[str, Any],
) -> dict[str, Any]:
    if not _flowi_dashboard_wip_prompt(prompt):
        return {"handled": False}

    from routers import dashboard as _dashboard

    product_hint = _product_hint(prompt, product)
    axis = "step_desc" if "step_desc" in str(prompt or "").lower() else "step_id"
    bin_match = re.search(r"(?:bin|구간)(?:\s*(?:간격|size))?\s*[:=]?\s*(\d{1,6})", str(prompt or ""), flags=re.I)
    bin_size = max(1, min(100000, int(bin_match.group(1)))) if bin_match else 30000
    split_match = re.search(r"\b((?:KNOB|MASK|FAB)_[A-Za-z0-9_.-]+)\b", str(prompt or ""), flags=re.I)
    split_col = split_match.group(1) if split_match else ""

    request = _flowi_internal_request(me, "/api/dashboard/wip-split")
    payload = _dashboard.wip_split_summary(
        request=request,
        product=product_hint,
        bin_size=bin_size,
        split_col=split_col,
        axis=axis,
        exclude_root_prefix="Z",
        lot_type="",
    )
    bins = payload.get("bins") if isinstance(payload.get("bins"), list) else []
    split_values = payload.get("split_values") if isinstance(payload.get("split_values"), list) else []
    selected_product = str(payload.get("product") or product_hint or "ALL")
    total = int(payload.get("total_wafers") or 0)
    chart_result = {
        "ok": True,
        "kind": "dashboard_wip_split",
        "chart_type": "wip_stacked",
        "title": f"{selected_product} WIP × Split Dashboard",
        "product": selected_product,
        "bins": bins,
        "split_values": split_values,
        "unassigned_label": payload.get("unassigned_label") or "(unassigned)",
        "total_wafers": total,
        "matched_wafers": int(payload.get("matched_wafers") or 0),
        "axis": payload.get("axis") or axis,
        "bin_size": int(payload.get("bin_size") or bin_size),
        "split_col": payload.get("split_col") or "",
        "generated_at": payload.get("generated_at") or "",
    }
    return _flowi_set_inline_type({
        "handled": True,
        "intent": "dashboard_wip_view",
        "action": "dashboard.wip_split.read",
        "feature": "dashboard",
        "answer": f"{selected_product} WIP 대시보드를 바로 표시합니다. 총 {total:,} wafer입니다.",
        "chart_result": chart_result,
        "slots": {
            "product": selected_product,
            "axis": chart_result["axis"],
            "bin_size": chart_result["bin_size"],
            "split_col": chart_result["split_col"],
        },
        "source_ids": ["/api/dashboard/wip-split"],
        "navigate": {"tab": "dashboard", "search": "", "auto": False, "label": "대시보드에서 크게 보기"},
    }, "chart", prompt=prompt)


def _flowi_duration_text(seconds: float | int | None) -> str:
    if seconds is None:
        return "-"
    sec = max(0, int(round(float(seconds))))
    hours, rem = divmod(sec, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def _handle_et_time_request(prompt: str, product: str, max_rows: int, me: dict[str, Any], agent_context: dict[str, Any] | None = None) -> dict[str, Any]:
    if not _is_et_time_prompt(prompt):
        anchor = _flowi_recent_tool_anchor(agent_context)
        followup_lots = [token for token in _tokens(prompt) if _is_root_lot_token(token)]
        if str(anchor.get("feature") or "") == "ettime" and str(anchor.get("action") or "") == "clarify_root_lot" and followup_lots:
            anchor_product = str((anchor.get("slots") or {}).get("product") or product or "").strip()
            prompt = f"{anchor_product} {followup_lots[0]} ET 측정시간 확인해줘"
        else:
            return {"handled": False}
    from routers import et_time as _et

    request = _flowi_internal_request(me, "/api/et-time")
    products = [str(value or "").strip() for value in (_et.et_time_products(request, prefix="", limit=500).get("products") or []) if str(value or "").strip()]
    normalized_prompt = semantic_hitl.normalize_term(prompt)
    selected_product = next((value for value in products if semantic_hitl.normalize_term(value) in normalized_prompt), "")
    if not selected_product:
        hint_key = semantic_hitl.normalize_term(_product_hint(prompt, product))
        selected_product = next((value for value in products if semantic_hitl.normalize_term(value) == hint_key), "")
    if not selected_product:
        choices = [{
            "id": f"ettime_product_{idx}", "label": value, "title": value,
            "description": "이 제품의 ET 측정시간을 조회합니다.",
            "prompt": f"{value} {(_et_time_months(prompt) or 12)}개월간 ET 측정시간 추이 확인해줘",
        } for idx, value in enumerate(products[:12], start=1)]
        return {
            "handled": True, "intent": "et_time_lookup", "action": "clarify_product", "feature": "ettime",
            "answer": "ET 측정시간을 조회할 제품을 선택해 주세요.", "missing": ["product"],
            "clarification": {"question": "어느 ET 제품인가요?", "choices": choices},
            "sources": [{"source": "DB ET product catalog", "path": "/api/et-time/products"}],
        }

    lots = [token for token in _tokens(prompt) if _is_root_lot_token(token)]
    trend_requested = any(term in str(prompt or "").lower() or term in str(prompt or "") for term in ("추이", "trend", "개월", "장기")) and not lots
    if trend_requested:
        months = _et_time_months(prompt) or 12
        try:
            payload = _et.et_time_trend(request, product=selected_product, months=months)
        except HTTPException as exc:
            return {"handled": True, "intent": "et_time_trend", "action": "et_time.trend.error", "feature": "ettime",
                    "answer": str(exc.detail), "sources": [{"source": "DB ET", "path": f"/api/et-time/trend?product={selected_product}&months={months}"}]}
        trend = payload.get("trend") if isinstance(payload.get("trend"), dict) else {}
        ranked = []
        for step_id, points in trend.items():
            valid = [point for point in (points or []) if isinstance(point, dict)]
            wafers = sum(int(point.get("wafers") or 0) for point in valid)
            weighted = sum(float(point.get("avg_duration_sec") or 0) * int(point.get("wafers") or 0) for point in valid)
            avg = weighted / wafers if wafers else 0.0
            ranked.append((avg, wafers, str(step_id), valid))
        ranked.sort(key=lambda row: (-row[0], -row[1], row[2]))
        chart_series = []
        dcop_rows = []
        from core.lot_step import lookup_step_meta
        for avg, wafers, step_id, points in ranked[:max(4, min(12, int(max_rows or 12)))]:
            meta = lookup_step_meta(product=selected_product, step_id=step_id)
            function_step = str(meta.get("function_step") or meta.get("func_step") or "").strip()
            latest = points[-1] if points else {}
            if len(chart_series) < 4:
                chart_series.append({
                    "name": f"{step_id}{' · ' + function_step if function_step else ''}",
                    "points": [{"x": idx, "x_label": point.get("month"), "bucket": point.get("month"),
                                "y": point.get("avg_duration_sec"), "n": point.get("wafers"),
                                "major": point.get("major")} for idx, point in enumerate(points)],
                })
            dcop_rows.append({
                "step_id": step_id, "function_step": function_step, "months": len(points), "wafers": wafers,
                "period_avg_sec": round(avg, 1), "period_avg_time": _flowi_duration_text(avg),
                "latest_month": latest.get("month") or "", "latest_avg_time": latest.get("avg_duration_text") or "",
                "latest_avg_pgm": latest.get("avg_pgm_count"), "major_request": latest.get("major") or "",
                "major_share_pct": latest.get("major_share"),
            })
        chart = {
            "kind": "et_time_trend", "chart_type": "trend", "title": f"{selected_product} 최근 {months}개월 ET 측정시간 추이",
            "metric": "평균 측정시간(초)", "x_label": "월", "y_label": "초", "series": chart_series,
            "total": sum(len(series.get("points") or []) for series in chart_series),
            "basis_label": "DB ET · wafer별 step PGM 시간 합계의 월 평균",
        }
        table = {"kind": "et_time_major_dcops", "title": "주요 측정 DCOP 시간",
                 "columns": _table_columns(list(dcop_rows[0])) if dcop_rows else [], "rows": dcop_rows, "total": len(dcop_rows)}
        return {
            "handled": True, "intent": "et_time_trend", "action": "et_time.trend", "feature": "ettime", "unit_ai": "ettime",
            "answer": f"{selected_product} 최근 {months}개월 측정시간 추이와 평균 시간이 큰 주요 DCOP를 표시했습니다.",
            "blocks": [{"kind": "chart_trend", "title": chart["title"], "payload": chart},
                       {"kind": "lot_table", "title": table["title"], "payload": table}],
            "chart_result": chart, "table": table, "slots": {"product": selected_product, "months": months},
            "interpretation_notes": [f"{selected_product}의 최근 {months}개월 ET 측정시간 추이와 주요 step/DCOP 시간 요청으로 이해했습니다."],
            "sources": [{"source": f"DB ET parquet ({payload.get('file_count') or 0} files)",
                         "path": f"/api/et-time/trend?product={selected_product}&months={months}"}],
        }

    root_lot_id = lots[0] if lots else ""
    if not root_lot_id:
        return {
            "handled": True, "intent": "et_time_measure", "action": "clarify_root_lot", "feature": "ettime",
            "answer": "개별 측정시간을 조회할 Root Lot ID를 알려주세요.", "missing": ["root_lot_id"],
            "pending_prompt": prompt, "slots": {"product": selected_product},
            "sources": [{"source": "DB ET lot catalog", "path": f"/api/et-time/lots?product={selected_product}"}],
        }
    try:
        payload = _et.et_time_measure(request, product=selected_product, root_lot_id=root_lot_id, lot_id="")
    except HTTPException as exc:
        return {"handled": True, "intent": "et_time_measure", "action": "et_time.measure.error", "feature": "ettime",
                "answer": str(exc.detail), "sources": [{"source": "DB ET", "path": f"/api/et-time/measure?product={selected_product}&root_lot_id={root_lot_id}"}]}
    rows = [row for row in (payload.get("rows") or []) if isinstance(row, dict)]
    table_rows = [{
        "step_id": row.get("step_id"), "function_step": row.get("function_step") or "", "pgm": row.get("pgm"),
        "duration": row.get("duration_text"), "duration_sec": row.get("duration_sec"),
        "tkin_first": row.get("tkin_min"), "tkout_last": row.get("tkout_max"),
        "wafer_count": row.get("wafer_count"), "measurement_points": row.get("pt"),
    } for row in rows[:max(1, min(100, int(max_rows or 12) * 5))]]
    total_sec = sum(float(row.get("duration_sec") or 0) for row in (payload.get("step_totals") or []))
    table = {"kind": "et_time_measure", "title": f"{root_lot_id} ET 측정시간",
             "columns": _table_columns(list(table_rows[0])) if table_rows else [], "rows": table_rows, "total": len(rows)}
    return {
        "handled": True, "intent": "et_time_measure", "action": "et_time.measure", "feature": "ettime", "unit_ai": "ettime",
        "answer": f"{selected_product} {root_lot_id}의 ET 측정시간 합계는 {_flowi_duration_text(total_sec)}입니다. step {payload.get('step_count') or 0}개, PGM {payload.get('pgm_count') or 0}개 기준입니다.",
        "table": table, "slots": {"product": selected_product, "root_lot_id": root_lot_id},
        "interpretation_notes": [f"{selected_product}를 ET 제품, {root_lot_id}를 Root Lot ID로 이해했습니다."],
        "sources": [{"source": "DB ET · tkout_time - tkin_time · PGM(pt) aggregation",
                     "path": f"/api/et-time/measure?product={selected_product}&root_lot_id={root_lot_id}"}],
    }


def _is_wafer_map_similarity_prompt(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    return any(t in low or t in text for t in ("wf map", "wafer map", "웨이퍼맵", "맵", "map")) and any(t in low or t in text for t in ("비슷", "similar", "유사", "닮"))


def _pearson_corr(a: list[float], b: list[float]) -> float | None:
    if len(a) != len(b) or len(a) < 3:
        return None
    ma = sum(a) / len(a)
    mb = sum(b) / len(b)
    da = [x - ma for x in a]
    db = [y - mb for y in b]
    va = sum(x * x for x in da)
    vb = sum(y * y for y in db)
    if va <= 0 or vb <= 0:
        return None
    return sum(x * y for x, y in zip(da, db)) / math.sqrt(va * vb)


def _beol_hint(text: str) -> bool:
    up = _upper(text)
    return any(term in up for term in ("BEOL", "M0", "M1", "M2", "M3", "VIA", "CA", "CT", "METAL", "IMD", "ILD"))


def _handle_wafer_map_similarity(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _is_wafer_map_similarity_prompt(prompt):
        return {"handled": False}
    product_hint = _product_hint(prompt, product)
    terms = _flowi_report_terms(prompt, product=product_hint)
    beol_only = "BEOL" in _upper(prompt) or "beol" in str(prompt or "").lower()
    frames: list[dict[str, Any]] = []
    inline_needs_coord_map = False
    for source, files in (("ET", _et_files(product_hint)), ("INLINE", _inline_files(product_hint))):
        if not files:
            continue
        try:
            lf = _scan_parquet(files)
            cols = _schema_names(lf)
            product_col = _ci_col(cols, "product", "PRODUCT")
            item_col = _ci_col(cols, "item_id", "ITEM_ID", "subitem_id", "SUBITEM_ID")
            value_col = _ci_col(cols, "value", "VALUE")
            shot_x_col = _ci_col(cols, "shot_x", "SHOT_X", "x", "X")
            shot_y_col = _ci_col(cols, "shot_y", "SHOT_Y", "y", "Y")
            step_col = _ci_col(cols, "step_id", "STEP_ID")
            if not (item_col and value_col and shot_x_col and shot_y_col):
                if source == "INLINE" and item_col and value_col and _ci_col(cols, "subitem_id", "SUBITEM_ID"):
                    inline_needs_coord_map = True
                continue
            aliases = _product_aliases(product_hint)
            if aliases and product_col:
                lf = lf.filter(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
            df = lf.select([
                pl.lit(source).alias("source"),
                pl.col(product_col).cast(_STR, strict=False).alias("product") if product_col else pl.lit(_core_product_name(product_hint)).alias("product"),
                pl.col(item_col).cast(_STR, strict=False).alias("item_id"),
                pl.col(step_col).cast(_STR, strict=False).alias("step_id") if step_col else pl.lit("").alias("step_id"),
                pl.col(shot_x_col).cast(_STR, strict=False).alias("shot_x"),
                pl.col(shot_y_col).cast(_STR, strict=False).alias("shot_y"),
                pl.col(value_col).cast(pl.Float64, strict=False).alias("value"),
            ]).drop_nulls(subset=["item_id", "shot_x", "shot_y", "value"]).limit(200000).collect()
            frames.extend(df.to_dicts())
        except Exception as e:
            logger.warning("flowi wafer map scan failed source=%s: %s", source, e)
    if not frames:
        answer = "shot_x/shot_y/value/item_id 형태의 ET wafer map 데이터를 찾지 못했습니다."
        if inline_needs_coord_map:
            answer += " INLINE raw DB는 subitem_id만 있으므로 inline_matching.csv의 matching_table과 TEG 위치조회 Inline map TABLE을 연결한 뒤 shot map similarity를 계산할 수 있습니다."
        return {"handled": True, "intent": "wafer_map_similarity", "answer": answer}
    item_counts: dict[tuple[str, str, str], int] = {}
    for row in frames:
        key = (_text(row.get("source")), _text(row.get("item_id")), _text(row.get("step_id")))
        item_counts[key] = item_counts.get(key, 0) + 1
    candidate_items = [
        {"source": src, "item_id": item, "step_id": step, "row_count": count, "beol_hint": _beol_hint(" ".join([item, step]))}
        for (src, item, step), count in item_counts.items()
    ]
    candidate_items.sort(key=lambda r: (not r["beol_hint"] if beol_only else False, -int(r["row_count"]), r["item_id"]))
    target_terms = [
        term for term in terms
        if term not in {"BEOL", "FEOL", "MOL", "MAP", "WF", "WAFER", "SIMILAR"}
        and not _beol_hint(term)
    ]
    target_matches = []
    for item in candidate_items:
        hay = _upper(" ".join([item.get("item_id") or "", item.get("step_id") or "", item.get("source") or ""]))
        if target_terms and any(term in hay for term in target_terms):
            target_matches.append(item)
    if not target_matches:
        rows = candidate_items[:max(1, min(40, max_rows * 4))]
        return {
            "handled": True,
            "intent": "wafer_map_similarity",
            "action": "clarify_target_map_item",
            "answer": "비교 기준이 될 target item을 특정하지 못했습니다. 아래 후보 중 item을 포함해서 다시 질문해주세요.",
            "clarification": {
                "question": "어떤 item의 WF map과 비교할까요?",
                "choices": [
                    {
                        "id": f"item_{i}",
                        "label": str(i + 1),
                        "title": f"{row['source']} {row['item_id']}",
                        "recommended": i == 0,
                        "description": f"step={row.get('step_id') or '-'}, rows={row.get('row_count')}",
                        "prompt": f"{product_hint} {row['item_id']} wf map이랑 가장 비슷한 map 찾아줘",
                    }
                    for i, row in enumerate(rows[:4])
                ],
            },
            "table": {"kind": "wafer_map_item_candidates", "title": "WF map item candidates", "placement": "below", "columns": _table_columns(["source", "item_id", "step_id", "row_count", "beol_hint"]), "rows": rows, "total": len(candidate_items)},
            "filters": {"product": product_hint, "terms": terms, "beol_only": beol_only},
        }
    target = target_matches[0]
    def map_for(src: str, item_id: str, step_id: str = "") -> dict[tuple[str, str], float]:
        vals: dict[tuple[str, str], list[float]] = {}
        for row in frames:
            if _text(row.get("source")) != src or _text(row.get("item_id")) != item_id:
                continue
            if step_id and _text(row.get("step_id")) != step_id:
                continue
            key = (_text(row.get("shot_x")), _text(row.get("shot_y")))
            vals.setdefault(key, []).append(float(row.get("value")))
        return {k: sum(v) / len(v) for k, v in vals.items() if v}
    target_map = map_for(target["source"], target["item_id"], target.get("step_id") or "")
    rows: list[dict[str, Any]] = []
    for cand in candidate_items:
        if cand["source"] == target["source"] and cand["item_id"] == target["item_id"] and cand.get("step_id") == target.get("step_id"):
            continue
        cand_map = map_for(cand["source"], cand["item_id"], cand.get("step_id") or "")
        common = sorted(set(target_map) & set(cand_map))
        if len(common) < 3:
            continue
        corr = _pearson_corr([target_map[k] for k in common], [cand_map[k] for k in common])
        if corr is None:
            continue
        rows.append({
            "target_source": target["source"],
            "target_item": target["item_id"],
            "candidate_source": cand["source"],
            "candidate_item": cand["item_id"],
            "candidate_step": cand.get("step_id") or "",
            "similarity": round(float(corr), 4),
            "abs_similarity": round(abs(float(corr)), 4),
            "common_shots": len(common),
            "beol_hint": bool(cand.get("beol_hint")),
        })
    rows.sort(key=lambda r: (not r["beol_hint"] if beol_only else False, -float(r.get("abs_similarity") or 0), -int(r.get("common_shots") or 0)))
    shown = rows[:max(1, min(80, max_rows * 6))]
    cols_out = ["target_source", "target_item", "candidate_source", "candidate_item", "candidate_step", "similarity", "abs_similarity", "common_shots", "beol_hint"]
    top = shown[0] if shown else {}
    answer = (
        f"{target['source']} {target['item_id']} WF map과 가장 유사한 후보는 "
        f"{top.get('candidate_source')} {top.get('candidate_item')}입니다. similarity={top.get('similarity')}, common_shots={top.get('common_shots')}."
    ) if shown else f"{target['item_id']}와 비교 가능한 common shot map 후보를 찾지 못했습니다."
    if beol_only:
        answer += " BEOL hint가 있는 후보를 우선 정렬했습니다."
    return {
        "handled": True,
        "intent": "wafer_map_similarity",
        "action": "query_similar_wafer_maps",
        "answer": answer,
        "table": {"kind": "wafer_map_similarity", "title": "Similar WF maps", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in shown], "total": len(rows)},
        "filters": {"product": product_hint, "terms": terms, "target": target, "beol_only": beol_only},
        "feature": "dashboard",
    }


def _is_split_fab_lot_basis_prompt(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    return ("fab_lot_id" in low or "fab lot" in low) and ("스플릿" in text or "split" in low) and any(t in text or t in low for t in ("언제", "업데이트", "기준", "fresh", "update"))


def _handle_split_fab_lot_basis(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _is_split_fab_lot_basis_prompt(prompt):
        return {"handled": False}
    product_hint = _product_hint(prompt, product)
    rows: list[dict[str, Any]] = []
    if product_hint:
        try:
            from routers import splittable as splittable_router
            cache_status = splittable_router._latest_lot_step_cache_status(product_hint)
            interval = int(cache_status.get("interval_minutes") or 30)
            if cache_status.get("cache_exists"):
                rows.append({
                    "product": product_hint,
                    "basis": "LOT progress latest cache",
                    "built_at": cache_status.get("updated_at") or cache_status.get("latest_updated_at") or "",
                    "interval_minutes": interval,
                    "fab_source": "lot_progress_latest_lot_by_root_wafer",
                    "fab_col": "lot_id",
                    "ts_col": "tkout_time",
                    "join_keys": "root_lot_id, wafer_id",
                    "row_count": int(cache_status.get("product_row_count") or cache_status.get("row_count") or 0),
                    "path": cache_status.get("cache_path") or "",
                    "status": "cache_current",
                })
            else:
                meta = splittable_router._resolve_override_meta_light(product_hint)
                rows.append({
                    "product": product_hint,
                    "basis": "LOT progress latest cache",
                    "built_at": "",
                    "interval_minutes": interval,
                    "fab_source": meta.get("fab_source") or "",
                    "fab_col": meta.get("fab_col") or "fab_lot_id",
                    "ts_col": meta.get("ts_col") or "",
                    "join_keys": ", ".join(meta.get("join_keys") or []),
                    "row_count": "",
                    "path": "",
                    "status": meta.get("error") or "cache_missing_or_stale",
                })
        except Exception as e:
            rows.append({"product": product_hint, "basis": "SplitTable", "built_at": "", "interval_minutes": "", "fab_source": "", "fab_col": "fab_lot_id", "ts_col": "", "join_keys": "", "row_count": "", "path": "", "status": f"lookup_failed: {e}"})
    else:
        rows.append({
            "product": "",
            "basis": "LOT progress latest cache",
            "built_at": "",
            "interval_minutes": "",
            "fab_source": "product 필요",
            "fab_col": "fab_lot_id",
            "ts_col": "tkout_time/time 계열이 있으면 최신도 기준",
            "join_keys": "root_lot_id, wafer_id 등 product 설정",
            "row_count": "",
            "path": "",
            "status": "product_required_for_exact_cache_status",
        })
    cols_out = ["product", "basis", "built_at", "interval_minutes", "fab_source", "fab_col", "ts_col", "join_keys", "row_count", "path", "status"]
    row = rows[0]
    answer = (
        f"SplitTable fab_lot_id는 LOT 진행 최신 캐시의 root_lot_id/wafer_id별 최신 lot_id 기준입니다. "
        f"{row.get('product') or 'product 미지정'} cache built_at={row.get('built_at') or '-'}, "
        f"fab_col={row.get('fab_col') or 'lot_id'}, ts_col={row.get('ts_col') or 'tkout_time'}."
    )
    return {
        "handled": True,
        "intent": "splittable_fab_lot_basis",
        "action": "explain_splittable_fab_lot_basis",
        "answer": answer,
        "table": {"kind": "splittable_fab_lot_basis", "title": "SplitTable fab_lot_id basis", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in rows[:max(1, max_rows)]], "total": len(rows)},
        "feature": "splittable",
    }


def _is_fab_corun_prompt(prompt: str) -> bool:
    text = str(prompt or "")
    return bool(_lot_tokens(prompt)) and any(t in text for t in ("같이 진행", "같이진행", "동시", "같은 시기", "함께")) and any(t in text for t in ("기준", "step", "공정", "MOL", "FEOL", "BEOL"))


def _handle_fab_corun_lots(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _is_fab_corun_prompt(prompt):
        return {"handled": False}
    lots = _lot_tokens(prompt)
    product_hint, candidate_tool = _product_or_candidate_tool(prompt, product, lots, kinds=("FAB",), intent="fab_corun_lots")
    if candidate_tool:
        return candidate_tool
    files = _fab_files(product_hint)
    if not files:
        return {"handled": True, "intent": "fab_corun_lots", "answer": "FAB parquet을 찾지 못했습니다."}
    lf = _scan_parquet(files)
    cols = _schema_names(lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    step_col = _ci_col(cols, "step_id", "STEP_ID")
    time_col = _ci_col(cols, "tkout_time", "TKOUT_TIME", "time", "TIME", "timestamp", "TIMESTAMP")
    if not step_col or not time_col or not (root_col or lot_col or fab_col):
        return {"handled": True, "intent": "fab_corun_lots", "answer": "FAB 데이터에서 step/time/lot 컬럼을 찾지 못했습니다."}
    aliases = _product_aliases(product_hint)
    if aliases and product_col:
        lf = lf.filter(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    exprs = [
        pl.col(product_col).cast(_STR, strict=False).alias("product") if product_col else pl.lit(_core_product_name(product_hint)).alias("product"),
        pl.col(root_col).cast(_STR, strict=False).alias("root_lot_id") if root_col else (
            pl.col(lot_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id") if lot_col else pl.col(fab_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id")
        ),
        pl.col(lot_col).cast(_STR, strict=False).alias("lot_id") if lot_col else pl.lit("").alias("lot_id"),
        pl.col(fab_col).cast(_STR, strict=False).alias("fab_lot_id") if fab_col else pl.lit("").alias("fab_lot_id"),
        pl.col(wafer_col).cast(_STR, strict=False).alias("wafer_id") if wafer_col else pl.lit("").alias("wafer_id"),
        pl.col(step_col).cast(_STR, strict=False).alias("step_id"),
        pl.col(time_col).cast(_STR, strict=False).alias("time"),
    ]
    try:
        all_rows = lf.select(exprs).drop_nulls(subset=["step_id", "time"]).limit(150000).collect().to_dicts()
    except Exception as e:
        return {"handled": True, "intent": "fab_corun_lots", "answer": f"FAB 같이 진행 lot 조회 실패: {e}"}
    lot_set = {_upper(v) for v in lots}
    target_rows = [
        r for r in all_rows
        if any(tok in _upper(" ".join([r.get("root_lot_id") or "", r.get("lot_id") or "", r.get("fab_lot_id") or ""])) for tok in lot_set)
    ]
    terms = _step_query_terms(prompt, lots, product_hint)
    if terms:
        filtered = []
        for row in target_rows:
            func = _function_step_label(row.get("product") or product_hint, row.get("step_id"))
            hay = _upper(" ".join([row.get("step_id") or "", func]))
            if any(term in hay for term in terms):
                filtered.append(row)
        target_rows = filtered or target_rows
    if not target_rows:
        return {"handled": True, "intent": "fab_corun_lots", "answer": f"{', '.join(lots)} 기준 FAB step row를 찾지 못했습니다."}
    target_rows.sort(key=lambda r: _parse_flowi_datetime(r.get("time")) or datetime.min, reverse=True)
    target_steps = {_text(r.get("step_id")) for r in target_rows[:20] if _text(r.get("step_id"))}
    target_times = [(r, _parse_flowi_datetime(r.get("time"))) for r in target_rows if _text(r.get("step_id")) in target_steps and _parse_flowi_datetime(r.get("time"))]
    rows: list[dict[str, Any]] = []
    target_roots = {_text(r.get("root_lot_id")) for r in target_rows}
    for row in all_rows:
        root = _text(row.get("root_lot_id"))
        if not root or root in target_roots or _text(row.get("step_id")) not in target_steps:
            continue
        dt = _parse_flowi_datetime(row.get("time"))
        if not dt:
            continue
        best = None
        for target_row, target_dt in target_times:
            if _text(target_row.get("step_id")) != _text(row.get("step_id")) or not target_dt:
                continue
            delta = abs((dt - target_dt).total_seconds()) / 3600.0
            if best is None or delta < best[0]:
                best = (delta, target_row, target_dt)
        if best is None or best[0] > 72:
            continue
        rows.append({
            "product": row.get("product") or product_hint,
            "target_lot": ", ".join(lots),
            "peer_root_lot_id": root,
            "peer_lot_id": row.get("lot_id") or "",
            "peer_fab_lot_id": row.get("fab_lot_id") or "",
            "step_id": row.get("step_id") or "",
            "function_step": _function_step_label(row.get("product") or product_hint, row.get("step_id")),
            "target_time": best[1].get("time") or "",
            "peer_time": row.get("time") or "",
            "delta_hours": round(best[0], 3),
        })
    rows.sort(key=lambda r: (float(r.get("delta_hours") or 9999), r.get("peer_root_lot_id") or ""))
    cols_out = ["product", "target_lot", "peer_root_lot_id", "peer_lot_id", "peer_fab_lot_id", "step_id", "function_step", "target_time", "peer_time", "delta_hours"]
    answer = f"{', '.join(lots)}와 같은 step/function 기준 72시간 내 같이 진행한 후보 lot {len(rows)}개를 찾았습니다." if rows else "같은 step에서 72시간 내 같이 진행한 후보 lot을 찾지 못했습니다."
    return {
        "handled": True,
        "intent": "fab_corun_lots",
        "action": "query_fab_corun_lots",
        "answer": answer,
        "table": {"kind": "fab_corun_lots", "title": "FAB co-run lots", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in rows[:max(1, min(120, max_rows * 8))]], "total": len(rows)},
        "filters": {"product": product_hint, "lots": lots, "terms": terms, "target_steps": sorted(target_steps)},
    }


def _is_knob_clean_or_interference_prompt(prompt: str) -> bool:
    up = _upper(prompt)
    text = str(prompt or "")
    if "KNOB" not in up and "노브" not in text:
        return False
    return any(t in text for t in ("클린", "clean", "다른", "신경", "적용", "간섭", "같이"))


def _handle_knob_clean_interference(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _is_knob_clean_or_interference_prompt(prompt):
        return {"handled": False}
    product_hint = _product_hint(prompt, product)
    files = _ml_files(product_hint)
    if not files:
        return {"handled": True, "intent": "knob_clean_interference", "answer": "ML_TABLE parquet을 찾지 못했습니다."}
    lf = _scan_parquet(files)
    cols = _schema_names(lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    knob_cols = [c for c in cols if _upper(c).startswith("KNOB_")]
    if not root_col or not knob_cols:
        return {"handled": True, "intent": "knob_clean_interference", "answer": "ML_TABLE에서 root_lot_id 또는 KNOB_* 컬럼을 찾지 못했습니다."}
    aliases = _product_aliases(product_hint)
    if aliases and product_col:
        lf = lf.filter(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    selected_knob, knob_candidates = _select_knob_column(lf, knob_cols, prompt, _lot_tokens(prompt), [])
    if not selected_knob:
        return {"handled": True, "intent": "knob_clean_interference", "answer": "요청과 맞는 KNOB 컬럼을 찾지 못했습니다."}
    keep = [c for c in (product_col, root_col, wafer_col) if c] + knob_cols[:160]
    try:
        df = lf.select([pl.col(c).cast(_STR, strict=False).alias(c) for c in keep]).limit(20000).collect()
    except Exception as e:
        return {"handled": True, "intent": "knob_clean_interference", "answer": f"KNOB clean/interference 조회 실패: {e}"}
    want_clean = "클린" in prompt or "clean" in str(prompt or "").lower()
    grouped: dict[str, dict[str, Any]] = {}
    for row in df.to_dicts():
        selected_value = _text(row.get(selected_knob))
        if not selected_value or selected_value.lower() in {"none", "null", "nan"}:
            continue
        root = _text(row.get(root_col))
        if not root:
            continue
        rec = grouped.setdefault(root, {
            "product": _text(row.get(product_col)) or product_hint,
            "root_lot_id": root,
            "selected_knob": selected_knob,
            "selected_values": set(),
            "wafer_count": 0,
            "wafers": set(),
            "other_knobs": {},
        })
        rec["selected_values"].add(selected_value)
        rec["wafer_count"] += 1
        wafer = _text(row.get(wafer_col))
        if wafer:
            rec["wafers"].add(wafer)
        for knob in knob_cols:
            if knob == selected_knob:
                continue
            val = _text(row.get(knob))
            if val and val.lower() not in {"none", "null", "nan"}:
                rec["other_knobs"][f"{knob}={val}"] = rec["other_knobs"].get(f"{knob}={val}", 0) + 1
    rows = []
    for rec in grouped.values():
        other = sorted(rec["other_knobs"].items(), key=lambda kv: (-kv[1], kv[0]))
        is_clean = len(other) == 0
        if want_clean and not is_clean:
            continue
        if not want_clean and is_clean:
            continue
        rows.append({
            "product": rec["product"],
            "root_lot_id": rec["root_lot_id"],
            "selected_knob": rec["selected_knob"],
            "selected_values": ", ".join(sorted(rec["selected_values"])),
            "wafer_count": rec["wafer_count"],
            "wafers": ", ".join(sorted(rec["wafers"], key=lambda x: (len(x), x))[:12]),
            "clean_split": is_clean,
            "other_knob_count": len(other),
            "other_knobs": ", ".join(f"{k}({v})" for k, v in other[:8]),
        })
    rows.sort(key=lambda r: (int(r.get("other_knob_count") or 0), -int(r.get("wafer_count") or 0), r.get("root_lot_id") or "") if want_clean else (-int(r.get("other_knob_count") or 0), -int(r.get("wafer_count") or 0), r.get("root_lot_id") or ""))
    cols_out = ["product", "root_lot_id", "selected_knob", "selected_values", "wafer_count", "wafers", "clean_split", "other_knob_count", "other_knobs"]
    if want_clean:
        answer = f"{selected_knob} 기준 다른 KNOB가 같이 잡히지 않은 clean split lot {len(rows)}개를 찾았습니다."
        intent = "knob_clean_split"
        action = "query_knob_clean_split_lots"
    else:
        answer = f"{selected_knob} 분석 시 같이 적용된 다른 KNOB 후보가 있는 lot {len(rows)}개를 찾았습니다."
        intent = "knob_interference_lookup"
        action = "query_knob_interference"
    return {
        "handled": True,
        "intent": intent,
        "action": action,
        "answer": answer,
        "table": {"kind": intent, "title": "KNOB clean/interference", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in rows[:max(1, min(120, max_rows * 8))]], "total": len(rows)},
        "filters": {"product": product_hint, "selected_knob": selected_knob, "knob_candidates": knob_candidates[:12]},
        "feature": "splittable",
    }


def _is_lot_anomaly_prompt(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    if any(t in low or t in text for t in ("그려", "차트", "그래프", "plot", "chart", "graph", "scatter")):
        return False
    return bool(_lot_tokens(prompt)) and any(t in low or t in text for t in ("특이사항", "outlier", "아웃라이어", "trend", "상하향", "상향", "하향", "이상"))


def _mean_std(values: list[float]) -> tuple[float | None, float | None]:
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not clean:
        return None, None
    mean = sum(clean) / len(clean)
    if len(clean) < 2:
        return mean, None
    var = sum((v - mean) ** 2 for v in clean) / (len(clean) - 1)
    return mean, math.sqrt(max(0.0, var))


def _handle_lot_anomaly_summary(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _is_lot_anomaly_prompt(prompt):
        return {"handled": False}
    lots = _lot_tokens(prompt)
    product_hint, candidate_tool = _et_product_or_candidate(prompt, product, lots, "lot_anomaly_summary")
    if candidate_tool:
        return candidate_tool
    rows_raw: list[dict[str, Any]] = []
    for source, files in (("ET", _et_files(product_hint)), ("INLINE", _inline_files(product_hint))):
        if not files:
            continue
        try:
            lf = _scan_parquet(files)
            cols = _schema_names(lf)
            product_col = _ci_col(cols, "product", "PRODUCT")
            root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
            lot_col = _ci_col(cols, "lot_id", "LOT_ID")
            fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
            wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
            step_col = _ci_col(cols, "step_id", "STEP_ID")
            item_col = _ci_col(cols, "item_id", "ITEM_ID")
            value_col = _ci_col(cols, "value", "VALUE")
            time_col = _ci_col(cols, "tkout_time", "TKOUT_TIME", "time", "TIME", "timestamp", "TIMESTAMP", "measure_time", "MEASURE_TIME")
            if not (item_col and value_col and (root_col or lot_col or fab_col)):
                continue
            aliases = _product_aliases(product_hint)
            if aliases and product_col:
                lf = lf.filter(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
            df = lf.select([
                pl.lit(source).alias("source"),
                pl.col(product_col).cast(_STR, strict=False).alias("product") if product_col else pl.lit(_core_product_name(product_hint)).alias("product"),
                pl.col(root_col).cast(_STR, strict=False).alias("root_lot_id") if root_col else (
                    pl.col(lot_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id") if lot_col else pl.col(fab_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id")
                ),
                pl.col(wafer_col).cast(_STR, strict=False).alias("wafer_id") if wafer_col else pl.lit("").alias("wafer_id"),
                pl.col(step_col).cast(_STR, strict=False).alias("step_id") if step_col else pl.lit("").alias("step_id"),
                pl.col(item_col).cast(_STR, strict=False).alias("item_id"),
                pl.col(value_col).cast(pl.Float64, strict=False).alias("value"),
                pl.col(time_col).cast(_STR, strict=False).alias("time") if time_col else pl.lit("").alias("time"),
            ]).drop_nulls(subset=["root_lot_id", "item_id", "value"]).limit(250000).collect()
            rows_raw.extend(df.to_dicts())
        except Exception as e:
            logger.warning("flowi lot anomaly scan failed source=%s: %s", source, e)
    if not rows_raw:
        return {"handled": True, "intent": "lot_anomaly_summary", "answer": "ET/INLINE에서 lot anomaly를 계산할 item/value 데이터를 찾지 못했습니다."}
    lot_set = {_upper(v) for v in lots}
    target_rows = [r for r in rows_raw if _upper(r.get("root_lot_id")) in lot_set or any(tok in _upper(r.get("root_lot_id")) for tok in lot_set)]
    if not target_rows:
        return {"handled": True, "intent": "lot_anomaly_summary", "answer": f"{', '.join(lots)}에 해당하는 ET/INLINE row를 찾지 못했습니다."}
    target_groups: dict[tuple[str, str, str], list[float]] = {}
    baseline_groups: dict[tuple[str, str, str], list[float]] = {}
    latest_time: dict[tuple[str, str, str], str] = {}
    for row in rows_raw:
        key = (_text(row.get("source")), _text(row.get("step_id")), _text(row.get("item_id")))
        val = row.get("value")
        if val is None:
            continue
        is_target = _upper(row.get("root_lot_id")) in lot_set or any(tok in _upper(row.get("root_lot_id")) for tok in lot_set)
        if is_target:
            target_groups.setdefault(key, []).append(float(val))
            latest_time[key] = max(latest_time.get(key, ""), _text(row.get("time")))
        else:
            baseline_groups.setdefault(key, []).append(float(val))
    rows: list[dict[str, Any]] = []
    for key, vals in target_groups.items():
        src, step, item = key
        target_mean, _target_std = _mean_std(vals)
        base_mean, base_std = _mean_std(baseline_groups.get(key) or [])
        if target_mean is None:
            continue
        z = None
        if base_mean is not None and base_std and base_std > 0:
            z = (target_mean - base_mean) / base_std
        direction = "up" if base_mean is not None and target_mean > base_mean else ("down" if base_mean is not None and target_mean < base_mean else "")
        severity = "outlier" if z is not None and abs(z) >= 3 else ("shift" if z is not None and abs(z) >= 2 else ("watch" if z is not None and abs(z) >= 1 else "normal"))
        rows.append({
            "product": product_hint or _text(target_rows[0].get("product")),
            "root_lot_id": ", ".join(lots),
            "source": src,
            "step_id": step,
            "function_step": _function_step_label(product_hint or _text(target_rows[0].get("product")), step),
            "item_id": item,
            "target_mean": _round4(target_mean),
            "baseline_mean": _round4(base_mean),
            "baseline_std": _round4(base_std),
            "z_score": _round4(z),
            "direction": direction,
            "severity": severity,
            "target_n": len(vals),
            "baseline_n": len(baseline_groups.get(key) or []),
            "latest_time": latest_time.get(key, ""),
        })
    rows.sort(key=lambda r: ({"outlier": 0, "shift": 1, "watch": 2, "normal": 3}.get(r.get("severity"), 9), -abs(float(r.get("z_score") or 0)), r.get("item_id") or ""))
    cols_out = ["product", "root_lot_id", "source", "step_id", "function_step", "item_id", "target_mean", "baseline_mean", "baseline_std", "z_score", "direction", "severity", "target_n", "baseline_n", "latest_time"]
    top = rows[0] if rows else {}
    answer = (
        f"{', '.join(lots)} ET/INLINE trend 대비 특이 후보 {len(rows)}개를 계산했습니다. "
        f"Top: {top.get('source') or '-'} {top.get('item_id') or '-'} {top.get('direction') or ''} z={top.get('z_score') or '-'} ({top.get('severity') or '-'})."
    ) if rows else "baseline과 비교 가능한 특이 후보를 찾지 못했습니다."
    return {
        "handled": True,
        "intent": "lot_anomaly_summary",
        "action": "query_lot_anomaly_summary",
        "answer": answer,
        "table": {"kind": "lot_anomaly_summary", "title": "Lot anomaly summary", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in rows[:max(1, min(120, max_rows * 8))]], "total": len(rows)},
        "filters": {"product": product_hint, "lots": lots},
        "feature": "dashboard",
    }


def _handle_et_query(prompt: str, product: str, max_rows: int) -> dict:
    if "ET" not in _upper(prompt):
        return {"handled": False}
    files = _et_files(product)
    if not files:
        return {
            "handled": True,
            "intent": "et_wafer_median",
            "answer": "ET 원천 parquet을 찾지 못했습니다. DB root 아래 `*ET*` 폴더를 확인해주세요.",
            "rows": [],
        }
    lf = _scan_parquet(files)
    cols = _schema_names(lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    step_col = _ci_col(cols, "step_id", "STEP_ID")
    item_col = _ci_col(cols, "item_id", "ITEM_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID")
    value_col = _ci_col(cols, "value", "VALUE")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID", "fab_lot_id", "FAB_LOT_ID")
    if not (step_col and item_col and wafer_col and value_col):
        return {
            "handled": True,
            "intent": "et_wafer_median",
            "answer": "ET 데이터 컬럼(step_id/item_id/wafer_id/value)을 찾지 못했습니다.",
            "rows": [],
        }

    step_vals = _unique_strings(lf, step_col)
    item_vals = _unique_strings(lf, item_col)
    step_matches = _match_values(step_vals, _step_tokens(prompt))
    item_matches = _match_values(item_vals, _query_tokens(prompt))
    lot_matches = _lot_tokens(prompt)
    aliases = _product_aliases(product)

    filters = []
    if aliases and product_col:
        filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    if step_matches:
        filters.append(pl.col(step_col).cast(_STR, strict=False).is_in(step_matches))
    if item_matches:
        filters.append(pl.col(item_col).cast(_STR, strict=False).is_in(item_matches))
    if lot_matches:
        lot_cols = [c for c in (root_col, lot_col, _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")) if c]
        lot_expr = _or_contains(lot_cols, lot_matches)
        if lot_expr is not None:
            filters.append(lot_expr)
    for expr in filters:
        lf = lf.filter(expr)

    group_cols = [c for c in (product_col, step_col, item_col, wafer_col) if c]
    try:
        out = (
            lf.group_by(group_cols)
            .agg([
                pl.col(value_col).cast(pl.Float64, strict=False).median().alias("median"),
                pl.col(value_col).cast(pl.Float64, strict=False).mean().alias("mean"),
                pl.col(value_col).cast(pl.Float64, strict=False).count().alias("count"),
            ])
            .sort(group_cols)
            .limit(max(1, min(120, max_rows * 6)))
            .collect()
        )
    except Exception as e:
        logger.warning("flowi ET query failed: %s", e)
        raise HTTPException(400, f"ET 집계 실패: {e}")

    rows = out.rename({
        product_col: "product",
        step_col: "step_id",
        item_col: "item_id",
        wafer_col: "wafer_id",
    }).to_dicts() if out.height else []
    rows = _sort_wafer_rows(rows)
    for row in rows:
        row["median"] = _round4(row.get("median"))
        row["mean"] = _round4(row.get("mean"))
        row["count"] = int(row.get("count") or 0)

    if not rows:
        hints = []
        if _step_tokens(prompt) and not step_matches:
            hints.append(f"step 후보: {', '.join(step_vals[:8])}")
        if _query_tokens(prompt) and not item_matches:
            hints.append(f"item 후보: {', '.join(item_vals[:8])}")
        hint_txt = " / ".join(hints) if hints else "필터 조건에 맞는 ET row가 없습니다."
        return {
            "handled": True,
            "intent": "et_wafer_median",
            "answer": hint_txt,
            "rows": [],
            "filters": {"step": step_matches, "item": item_matches, "lot": lot_matches, "product": sorted(aliases)},
        }

    preview = rows[:max_rows]
    lines = [
        f"- WF {r.get('wafer_id')}: median {r.get('median')} (mean {r.get('mean')}, n={r.get('count')})"
        for r in preview
    ]
    scope = []
    if step_matches:
        scope.append("step=" + ",".join(step_matches))
    if item_matches:
        scope.append("item=" + ",".join(item_matches))
    if lot_matches:
        scope.append("lot~" + ",".join(lot_matches))
    if aliases:
        scope.append("product=" + ",".join(sorted(aliases)[:4]))
    answer = "ET value wafer별 median입니다"
    if scope:
        answer += " (" + " / ".join(scope) + ")"
    answer += f". 총 {len(rows)}개 그룹 중 상위 {len(preview)}개를 표시합니다.\n" + "\n".join(lines)
    table_cols = ["product", "step_id", "item_id", "wafer_id", "median", "mean", "count"]
    return {
        "handled": True,
        "intent": "et_wafer_median",
        "answer": answer,
        "rows": rows,
        "table": {
            "kind": "et_wafer_median",
            "title": "ET wafer median",
            "placement": "below",
            "columns": _table_columns(table_cols),
            "rows": [{k: r.get(k, "") for k in table_cols} for r in rows[: max(1, min(120, max_rows * 8))]],
            "total": len(rows),
        },
        "filters": {"step": step_matches, "item": item_matches, "lot": lot_matches, "product": sorted(aliases)},
    }


def _flowi_knob_term_resolution(
    *,
    prompt: str,
    lot_matches: list[str],
    lot_scope_matches: list[str],
    step: str,
    group: str,
    selected_knobs: list[str],
    table_requested: bool,
    row_count: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def add(token: str, meaning: str, wiki_refs: list[str], query_filter: str, status: str = "resolved") -> None:
        if not str(token or "").strip():
            return
        out.append({
            "token": str(token).strip(),
            "meaning": meaning,
            "wiki_refs": list(dict.fromkeys(str(ref) for ref in wiki_refs if str(ref or "").strip()))[:8],
            "query_filter": query_filter,
            "status": status,
        })

    if lot_matches or lot_scope_matches:
        display_lots = list(dict.fromkeys([*lot_matches, *lot_scope_matches]))
        add(
            ", ".join(display_lots[:6]),
            "root_lot_id / lot_id / fab_lot_id lot scope",
            ["schema:root_lot_id", "schema:lot_id", "schema:fab_lot_id"],
            "root_lot_id, lot_id, fab_lot_id contains " + ", ".join(display_lots[:6]),
        )
    if step:
        step_parts = " and ".join(part for part in re.split(r"\s+", step) if part) or step
        add(
            step,
            "step_id / function_step 조건",
            ["schema:step_id", "schema:function_step", "schema:func_step"],
            f"step_id/function_step contains {step_parts}",
        )
    if group:
        cols = [col for col in selected_knobs[:6]]
        suffix = f"; selected {', '.join(cols)}" if cols else ""
        add(
            group,
            f"{group}_* 컬럼군",
            [f"schema:{group}_*"],
            f"columns startswith {group}_{suffix}",
        )
    if table_requested:
        table_token = "TABLE"
        if "테이블" in prompt:
            table_token = "테이블"
        elif "표" in prompt:
            table_token = "표"
        add(
            table_token,
            "표 출력 요청",
            ["ui:table"],
            f"inline table rows={row_count}",
        )
    return out


def _handle_knob_query(prompt: str, product: str, max_rows: int) -> dict:
    up = _upper(prompt)
    if "KNOB" not in up and "노브" not in prompt:
        return {"handled": False}
    lot_matches = _lot_tokens(prompt)
    lot_scope_matches = _flowi_lot_scope_terms(lot_matches)
    classified = _classified_lot_tokens(prompt)
    files = _ml_files(product)
    if not files:
        return {
            "handled": True,
            "intent": "lot_knobs",
            "answer": "ML_TABLE parquet을 찾지 못했습니다. DB root의 `ML_TABLE_*.parquet` 파일을 확인해주세요.",
            "knobs": [],
        }
    group = _flowi_group_token(prompt) or "KNOB"
    lf, cols, lookup_status = _ml_lookup_lazy_for_lots(files, lot_scope_matches, group)
    lookup_cache_used = lf is not None
    if lf is None:
        lf = _scan_parquet(files)
        cols = _schema_names(lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID")
    prefixes = (f"{group}_",) if group in {"KNOB", "MASK", "INLINE", "VM"} else ("KNOB_",)
    knob_cols = [c for c in cols if _upper(c).startswith(prefixes)]
    if not knob_cols:
        return {"handled": True, "intent": "lot_knobs", "answer": f"ML_TABLE에서 {group}_* 컬럼을 찾지 못했습니다.", "knobs": []}

    aliases = _product_aliases(product)
    filters = []
    if aliases and product_col:
        filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    if lot_scope_matches:
        lot_cols = [c for c in (root_col, lot_col, fab_col) if c]
        lot_expr = _or_contains(lot_cols, lot_scope_matches)
        if lot_expr is not None:
            filters.append(lot_expr)
    step = _flowi_func_step_token(prompt)
    step_expr = _flowi_step_filter_expr(cols, step)
    if step_expr is not None:
        filters.append(step_expr)
    for expr in filters:
        lf = lf.filter(expr)

    if not lot_matches:
        try:
            sample_cols = [c for c in (product_col, root_col, lot_col) if c]
            sample = lf.select(sample_cols).unique().limit(8).collect().to_dicts()
        except Exception:
            sample = []
        lots = ", ".join(sorted({_text(r.get(root_col) or r.get(lot_col)) for r in sample if _text(r.get(root_col) or r.get(lot_col))})[:8])
        suffix = f" 예: {lots}" if lots else ""
        return {
            "handled": True,
            "intent": "lot_knobs",
            "answer": "KNOB 조회는 lot/root lot 조건이 필요합니다." + suffix,
            "knobs": [],
            "lot_candidates": sample,
        }

    keep = [c for c in (product_col, root_col, lot_col, wafer_col) if c] + knob_cols
    try:
        df = lf.select(keep).collect()
    except Exception as e:
        logger.warning("flowi knob query failed: %s", e)
        raise HTTPException(400, f"KNOB 조회 실패: {e}")
    if wafer_col and wafer_col in df.columns:
        df = (
            df.with_columns(
                pl.col(wafer_col)
                .map_elements(lambda v: _normalize_wafer_id(v), return_dtype=_STR)
                .alias(wafer_col)
            )
            .filter(pl.col(wafer_col).is_not_null() & (pl.col(wafer_col) != ""))
        )
    if df.height == 0:
        return {
            "handled": True,
            "intent": "lot_knobs",
            "answer": f"{', '.join(lot_matches)} 조건에 맞는 ML_TABLE row가 없습니다.",
            "knobs": [],
        }

    q_tokens = set(_query_tokens(prompt)) - set(lot_matches) - set(lot_scope_matches)
    selected_knobs = []
    for col in knob_cols:
        body = _upper(col.replace("KNOB_", ""))
        if not q_tokens or any(tok in body for tok in q_tokens):
            selected_knobs.append(col)
    if not selected_knobs:
        selected_knobs = knob_cols

    table = None
    highlight = _flowi_wants_highlight(prompt)
    table_lookup_requested = _flowi_knob_table_lookup_intent(prompt)
    detail_requested = highlight or table_lookup_requested or bool(q_tokens) or any(w in prompt for w in ("다", "전체", "테이블", "표", "보여"))
    if detail_requested and selected_knobs:
        table_knobs = selected_knobs[:8]
        table_cols = [c for c in (product_col, root_col, lot_col, fab_col, wafer_col) if c] + table_knobs
        rename = {}
        if product_col:
            rename[product_col] = "product"
        if root_col:
            rename[root_col] = "root_lot_id"
        if lot_col:
            rename[lot_col] = "lot_id"
        if fab_col:
            rename[fab_col] = "fab_lot_id"
        if wafer_col:
            rename[wafer_col] = "wafer_id"
        for col in table_knobs:
            rename[col] = col.replace("KNOB_", "", 1)
        try:
            tdf = df.select(table_cols).rename(rename)
            table_rows = _sort_wafer_rows(tdf.to_dicts())[:80]
        except Exception:
            table_rows = []
        if highlight:
            for row in table_rows:
                row["__highlight"] = True
        table_columns = []
        for key, label in [
            ("product", "PRODUCT"),
            ("root_lot_id", "ROOT_LOT_ID"),
            ("lot_id", "LOT_ID"),
            ("fab_lot_id", "FAB_LOT_ID"),
            ("wafer_id", "WAFER_ID"),
        ]:
            if key in rename.values():
                table_columns.append({"key": key, "label": label})
        table_columns.extend({"key": col.replace("KNOB_", "", 1), "label": col.replace("KNOB_", "", 1)} for col in table_knobs)
        table = {
            "kind": "splittable_preview",
            "title": f"{', '.join(lot_matches)} KNOB table",
            "placement": "below",
            "columns": table_columns,
            "rows": table_rows,
            "total": int(df.height),
        }

    summaries = []
    for col in selected_knobs[: max(1, min(40, max_rows * 3))]:
        vc = (
            df.select(pl.col(col).cast(_STR, strict=False).alias("value"))
            .drop_nulls()
            .group_by("value")
            .len()
            .sort("len", descending=True)
        )
        values = vc.to_dicts()
        wafer_by_value = {}
        for rec in values[:5]:
            val = rec.get("value")
            try:
                wafers = (
                    df.filter(pl.col(col).cast(_STR, strict=False) == val)
                    .select(pl.col(wafer_col).cast(_STR, strict=False).alias("wafer_id"))
                    .unique()
                    .sort("wafer_id")
                    .limit(30)
                    .to_series()
                    .to_list()
                ) if wafer_col else []
            except Exception:
                wafers = []
            wafer_by_value[_text(val)] = wafers
        summaries.append({
            "knob": col,
            "display_name": col.replace("KNOB_", "", 1),
            "split": len(values) > 1,
            "values": [{"value": r.get("value"), "count": int(r.get("len") or 0), "wafers": wafer_by_value.get(_text(r.get("value")), [])} for r in values[:5]],
        })

    custom_set_rows: list[dict[str, Any]] = []
    custom_set_columns: list[dict[str, str]] = []
    table_knobs_for_sets = selected_knobs[: max(1, min(8, max_rows))]
    if wafer_col and table_knobs_for_sets:
        display_names = [col.replace("KNOB_", "", 1) for col in table_knobs_for_sets]
        grouped: dict[tuple[str, ...], list[str]] = {}
        for row in df.select([wafer_col, *table_knobs_for_sets]).to_dicts():
            wafer = _normalize_wafer_id(row.get(wafer_col)) or _text(row.get(wafer_col))
            if not wafer:
                continue
            signature = tuple(_text(row.get(col)) for col in table_knobs_for_sets)
            grouped.setdefault(signature, []).append(wafer)
        def first_wafer(items: list[str]) -> int:
            nums = [int(w) for w in items if str(w).isdigit()]
            return min(nums) if nums else 999
        ordered_groups = sorted(grouped.items(), key=lambda kv: (first_wafer(kv[1]), kv[0]))
        for idx, (signature, wafers_for_set) in enumerate(ordered_groups, start=1):
            wafers_sorted = sorted(set(wafers_for_set), key=lambda w: int(w) if str(w).isdigit() else 999)
            row = {
                "custom_set": f"custom_set_{idx}",
                "wafer_count": len(wafers_sorted),
                "wafer_ids": ", ".join(f"#{w}" for w in wafers_sorted),
                "knob_signature": ", ".join(
                    f"{name}={value or '(empty)'}"
                    for name, value in zip(display_names, signature)
                ),
            }
            for name, value in zip(display_names, signature):
                row[name] = value or "(empty)"
            custom_set_rows.append(row)
        custom_set_columns = [
            {"key": "custom_set", "label": "CUSTOM_SET"},
            {"key": "wafer_count", "label": "WF_COUNT"},
            {"key": "wafer_ids", "label": "WAFERS"},
            *({"key": name, "label": name} for name in display_names),
            {"key": "knob_signature", "label": "KNOB_SIGNATURE"},
        ]
    custom_set_table = {
        "kind": "custom_set_preview",
        "title": f"{', '.join(lot_matches)} KNOB custom sets",
        "placement": "below",
        "columns": custom_set_columns,
        "rows": custom_set_rows,
        "total": len(custom_set_rows),
    } if custom_set_rows else None

    lot_label = ", ".join(lot_matches)
    preview = summaries[:max_rows]
    lines = []
    for item in preview:
        val_txt = "; ".join(
            f"{v.get('value')}({v.get('count')}wf" + (f": {','.join(v.get('wafers')[:8])}" if item.get("split") else "") + ")"
            for v in item.get("values", [])[:3]
        )
        lines.append(f"- {item.get('display_name')}: {val_txt}")
    if table_lookup_requested:
        conditions = [lot_label]
        if step:
            conditions.append(step)
        conditions.append(group)
        answer = f"{' / '.join([c for c in conditions if c])} 조건으로 ML_TABLE을 조회했습니다. 결과 {df.height}건입니다."
    else:
        answer = f"{lot_label} KNOB 요약입니다. {df.height} wafer row 기준, {len(summaries)}개 KNOB 중 {len(preview)}개를 표시합니다.\n" + "\n".join(lines)
    prefer_custom_set = bool(custom_set_table) and not highlight and any(t in prompt for t in ("구성", "커스텀", "세트", "custom", "set", "어떻게"))
    if prefer_custom_set:
        set_lines = [
            f"- {row.get('custom_set')}: {row.get('knob_signature')} / {row.get('wafer_ids')}"
            for row in custom_set_rows[:max(1, min(6, max_rows))]
        ]
        answer = (
            f"{lot_label} KNOB 구성은 custom set 기준으로 보는 것이 가장 좋습니다. "
            f"{df.height} wafer row를 {len(custom_set_rows)}개 custom set으로 묶었습니다.\n"
            + "\n".join(set_lines)
        )
    primary_table = custom_set_table if prefer_custom_set else table
    return _flowi_set_inline_type({
        "handled": True,
        "intent": "lot_knobs",
        "action": "query_lot_knobs_from_ml_table",
        "answer": answer,
        "feature": "splittable",
        "knobs": summaries,
        "custom_sets": custom_set_rows,
        "highlight": highlight,
        "table": primary_table,
        "wafer_table": table if primary_table is custom_set_table else None,
        "filters": {
            "lot": lot_matches,
            "lot_scope": lot_scope_matches,
            "root_lot_ids": classified.get("root_lot_ids") or [],
            "fab_lot_ids": classified.get("fab_lot_ids") or [],
            "product": sorted(aliases),
            "step": step,
            "group": group,
            "source": "ml_table_lookup_cache" if lookup_cache_used else "ML_TABLE",
            "lookup_cache_hit": lookup_cache_used,
            "cache_status": (lookup_status or {}).get("status") or "",
        },
        "term_resolution": _flowi_knob_term_resolution(
            prompt=prompt,
            lot_matches=lot_matches,
            lot_scope_matches=lot_scope_matches,
            step=step,
            group=group,
            selected_knobs=selected_knobs,
            table_requested=table_lookup_requested,
            row_count=int(df.height),
        ),
    }, "table", prompt=prompt, highlight=highlight)


def _is_rag_update_prompt(prompt: str) -> bool:
    return semi_knowledge.has_rag_update_marker(prompt)


def _handle_flowi_rag_update(prompt: str, me: dict[str, Any]) -> dict[str, Any]:
    username = me.get("username") or "user"
    role = me.get("role") or "user"
    try:
        knowledge_defaults = _flowi_engineer_knowledge_defaults()
        out = semi_knowledge.structure_rag_update_from_prompt(
            prompt,
            username=username,
            role=role,
            require_marker=(role != "admin") or bool(knowledge_defaults.get("rag_update_requires_marker", True)),
        )
    except ValueError as e:
        return {
            "handled": True,
            "intent": "semiconductor_rag_update",
            "action": "append_custom_knowledge",
            "blocked": True,
            "answer": f"RAG Update 본문이 비어 있습니다. [flow-i update] 또는 [flow-i RAG Update] 뒤에 구조화할 item/TEG/alias/판단 지식을 적어주세요. ({e})",
        }
    saved = out.get("saved") or {}
    structured = out.get("structured") or {}
    storage = out.get("storage") or {}
    rows = [
        {"field": "id", "value": saved.get("id") or ""},
        {"field": "kind", "value": saved.get("kind") or ""},
        {"field": "visibility", "value": saved.get("visibility") or ""},
        {"field": "schema_type", "value": structured.get("schema_type") or ""},
        {"field": "items", "value": ", ".join(structured.get("known_canonical_candidates") or [])},
        {"field": "raw_item_tokens", "value": ", ".join(structured.get("raw_item_tokens") or [])},
        {"field": "discriminators", "value": ", ".join(structured.get("discriminators") or [])},
        {"field": "storage", "value": storage.get("custom_knowledge") or ""},
    ]
    answer = (
        "Flow-i RAG Update를 append-only 지식으로 저장했습니다.\n"
        f"- 저장 위치: {storage.get('custom_knowledge') or '-'}\n"
        f"- visibility: {saved.get('visibility') or '-'}\n"
        f"- 구조 타입: {structured.get('schema_type') or '-'}\n"
        "기본 seed 코드는 프롬프트로 직접 수정하지 않고, 운영 지식은 flow-data에 누적합니다."
    )
    return {
        "handled": True,
        "intent": "semiconductor_rag_update",
        "action": "append_custom_knowledge",
        "answer": answer,
        "rag_update": out,
        "table": {"kind": "flowi_rag_update", "columns": ["field", "value"], "rows": rows},
        "feature": "diagnosis",
    }


def _is_reformatter_proposal_prompt(prompt: str) -> bool:
    low = str(prompt or "").lower()
    return (
        ("reformatter" in low or "alias" in low or "alias화" in low or "별칭" in low)
        and any(t in low for t in ["item", "teg", "chain", "pc-", "cb-", "m1", "raw"])
    )


def _is_teg_layout_prompt(prompt: str) -> bool:
    low = str(prompt or "").lower()
    return ("teg" in low or "좌표" in low or "coordinate" in low) and ("yaml" in low or "layout" in low or "정리" in low or "넣어" in low)


def _flowi_dataset_source_from_prompt(prompt: str, product: str, preferred_source: str = "") -> dict[str, Any]:
    files = _flowi_file_tokens(prompt)
    source: dict[str, Any] = {"product": product or ""}
    if files:
        source.update({"source_type": "base_file", "file": files[0]})
    explicit_source = re.search(r"(?:source_type|source|소스)\s*[:=]\s*(FAB|INLINE|ET|VM|QTIME|EDS)\b", prompt or "", re.I)
    if explicit_source:
        source["source_type_filter"] = explicit_source.group(1).upper()
        source["flowi_source_confirmed"] = True
    if preferred_source:
        source["source_type_filter"] = preferred_source.upper()
        source["flowi_source_confirmed"] = True
    return {k: v for k, v in source.items() if v}


def _compact_flowi_dataset_profile(profile: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(profile, dict):
        return {}
    if not profile.get("ok"):
        return {
            "ok": False,
            "reason": str(profile.get("reason") or "profile_failed")[:240],
            "warnings": [str(x)[:240] for x in (profile.get("warnings") or [])[:4]],
        }
    return {
        "ok": True,
        "source": profile.get("source") or {},
        "suggested_source_type": profile.get("suggested_source_type") or "",
        "metric_shape": profile.get("metric_shape") or "",
        "grain": profile.get("grain") or "",
        "join_keys": [str(x) for x in (profile.get("join_keys") or [])[:10]],
        "unique_items": [str(x) for x in (profile.get("unique_items") or [])[:12]],
        "metric_columns": [str(x) for x in (profile.get("metric_columns") or [])[:12]],
        "default_aggregation": profile.get("default_aggregation") or "",
        "warnings": [str(x)[:240] for x in (profile.get("warnings") or [])[:4]],
    }


def _flowi_dataset_profile_for_source(source: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(source, dict) or not source:
        return {}
    # Explicit file/root sources are cheap and explainable.  Product-only
    # discovery can scan many roots, so keep that out of the Home prompt path.
    if not (source.get("file") or (source.get("root") and source.get("product"))):
        return {}
    try:
        return _compact_flowi_dataset_profile(semi_knowledge.dataset_profile(source, limit=250))
    except Exception as e:
        return {"ok": False, "reason": str(e)[:240], "warnings": [str(e)[:240]]}


def _flowi_profile_label(profile: dict[str, Any]) -> str:
    if not profile:
        return "-"
    if not profile.get("ok"):
        return "profile_failed: " + str(profile.get("reason") or "-")
    bits = [
        str(profile.get("suggested_source_type") or "AUTO"),
        str(profile.get("metric_shape") or "?"),
        str(profile.get("grain") or "?"),
    ]
    keys = profile.get("join_keys") or []
    if keys:
        bits.append("join=" + ",".join(str(x) for x in keys[:4]))
    return " / ".join(bits)


def _flowi_source_profile_needs_clarification(source: dict[str, Any], profile: dict[str, Any]) -> bool:
    if not isinstance(source, dict) or not (source.get("file") or source.get("root")):
        return False
    if source.get("flowi_source_confirmed") or source.get("source_type_filter"):
        return False
    if not profile:
        return False
    if not profile.get("ok"):
        return True
    suggested = str(profile.get("suggested_source_type") or "").upper()
    shape = str(profile.get("metric_shape") or "").lower()
    grain = str(profile.get("grain") or "").lower()
    join_keys = profile.get("join_keys") or []
    if suggested in {"", "AUTO"}:
        return True
    if shape not in {"long", "wide"}:
        return True
    if grain in {"", "row"}:
        return True
    if not join_keys:
        return True
    severe = ("no clear item", "source type could not", "lot_wf cannot", "no readable")
    return any(any(term in str(w).lower() for term in severe) for w in (profile.get("warnings") or []))


def _flowi_source_type_choices(prompt: str, profile: dict[str, Any]) -> list[dict[str, Any]]:
    suggested = str((profile or {}).get("suggested_source_type") or "").upper()
    order = [suggested] if suggested in {"ET", "INLINE", "EDS", "VM", "QTIME", "FAB"} else []
    for item in ("ET", "INLINE", "EDS", "VM", "QTIME", "FAB"):
        if item not in order:
            order.append(item)
    meta = {
        "ET": ("ET/WAT parametric", "lot_wf 기준 median. DIBL/SS/Vth/Ion/Ioff/Rsd 같은 전기 특성에 적합합니다.", "grain=lot_wf aggregation=median"),
        "INLINE": ("INLINE metrology", "lot_wf 기준 avg, shot/position key가 있으면 shot 매칭을 우선합니다.", "grain=lot_wf aggregation=avg"),
        "EDS": ("EDS wafer sort", "die/bin 좌표와 fail/yield-rate를 보존합니다.", "grain=die aggregation=yield_rate"),
        "VM": ("VM/SRAM margin", "macro/condition/bin split을 유지하고 median 또는 fail-rate로 봅니다.", "grain=macro aggregation=median"),
        "QTIME": ("QTIME route window", "from_step/to_step 시간 구간을 route segment별 median/p95로 봅니다.", "grain=route_segment aggregation=p95"),
        "FAB": ("FAB route/progress", "step/time 최신 이력과 route sequence를 기준으로 봅니다.", "grain=lot_wf_step aggregation=latest"),
    }
    choices: list[dict[str, Any]] = []
    for idx, st in enumerate(order[:3]):
        title, desc, suffix = meta[st]
        choices.append({
            "id": f"source_{st.lower()}",
            "label": str(idx + 1),
            "title": title,
            "recommended": idx == 0,
            "description": desc,
            "prompt": f"{prompt.strip()} / source_type={st} {suffix} 으로 진행",
        })
    return choices


def _flowi_source_profile_clarification(
    prompt: str,
    product: str,
    source: dict[str, Any],
    profile: dict[str, Any],
    max_rows: int,
) -> dict[str, Any]:
    rows = [
        {"field": "source", "value": source.get("file") or (str(source.get("root") or "") + "/" + str(source.get("product") or product or "")).rstrip("/")},
        {"field": "profile", "value": _flowi_profile_label(profile)},
        {"field": "reason", "value": "source type/grain/join key가 확실하지 않아 실행 전 확인 필요"},
    ]
    if profile.get("ok"):
        rows.extend([
            {"field": "suggested_source_type", "value": profile.get("suggested_source_type") or "-"},
            {"field": "metric_shape", "value": profile.get("metric_shape") or "-"},
            {"field": "grain", "value": profile.get("grain") or "-"},
            {"field": "join_keys", "value": ", ".join(profile.get("join_keys") or []) or "-"},
            {"field": "unique_items", "value": ", ".join((profile.get("unique_items") or profile.get("metric_columns") or [])[:8]) or "-"},
        ])
    else:
        rows.append({"field": "profile_error", "value": profile.get("reason") or "profile_failed"})
    for i, warning in enumerate((profile.get("warnings") or [])[:3], start=1):
        rows.append({"field": f"warning_{i}", "value": warning})
    choices = _flowi_source_type_choices(prompt, profile)
    answer = (
        "파일/DB source의 schema가 애매해서 진단을 바로 실행하지 않았습니다.\n"
        "아래 1/2/3 중 어떤 데이터 성격으로 볼지 선택해주세요. "
        "선택 후에는 같은 파일을 whitelisted query로만 읽고, DB에 없는 값은 만들지 않습니다."
    )
    return {
        "handled": True,
        "intent": "semiconductor_source_clarification",
        "action": "confirm_semiconductor_source_profile",
        "answer": answer,
        "data_source": source,
        "source_profile": profile,
        "clarification": {
            "question": "이 source를 어떤 반도체 데이터 타입과 집계 기준으로 해석할까요?",
            "choices": choices,
        },
        "table": {
            "kind": "semiconductor_source_profile_review",
            "title": "Flow-i source profile review",
            "placement": "below",
            "columns": [{"key": "field", "label": "FIELD"}, {"key": "value", "label": "VALUE"}],
            "rows": rows[:max(1, max_rows)],
            "total": len(rows),
        },
        "feature": "diagnosis",
        "slots": {"product": product, "source": source},
    }


def _handle_flowi_admin_semiconductor_file_prep(prompt: str, product: str, me: dict[str, Any]) -> dict[str, Any]:
    if (me.get("role") or "user") != "admin":
        return {"handled": False}
    if _is_teg_layout_prompt(prompt):
        source = _flowi_dataset_source_from_prompt(prompt, product)
        source_profile = _flowi_dataset_profile_for_source(source)
        proposal = (
            semi_knowledge.teg_layout_proposal_from_dataset(product, source=source, prompt=prompt)
            if source.get("file") else
            semi_knowledge.teg_layout_proposal_from_rows(product, rows=[], prompt=prompt)
        )
        rows = [
            {"field": "target", "value": "product_config/products.yaml wafer_layout.teg_definitions"},
            {"field": "product", "value": product or "(필요)"},
            {"field": "source", "value": source.get("file") or "prompt/table rows"},
            {"field": "profile", "value": _flowi_profile_label(source_profile)},
            {"field": "detected_tegs", "value": str(len(proposal.get("teg_definitions") or []))},
            {"field": "required_columns", "value": ", ".join(proposal.get("required_columns") or [])},
        ]
        answer = (
            "TEG 좌표/YAML 반영은 admin 단위기능으로 처리해야 합니다.\n"
            "현재 프롬프트에서 추출된 TEG가 부족하면 `label/name/id`, `dx_mm/x`, `dy_mm/y` 컬럼을 가진 표를 먼저 넣어주세요. "
            "검토 후 `/api/semiconductor/teg/apply`가 product YAML에 반영합니다."
        )
        return {
            "handled": True,
            "intent": "semiconductor_teg_layout_proposal",
            "action": "propose_teg_yaml_update",
            "answer": answer,
            "proposal": proposal,
            "data_source": source,
            "source_profile": source_profile,
            "table": {"kind": "semiconductor_teg_yaml_proposal", "columns": ["field", "value"], "rows": rows},
            "feature": "diagnosis",
        }
    if _is_reformatter_proposal_prompt(prompt):
        source = _flowi_dataset_source_from_prompt(prompt, product)
        source_profile = _flowi_dataset_profile_for_source(source)
        proposal = (
            semi_knowledge.reformatter_alias_proposal_from_dataset(product, source=source, prompt=prompt)
            if source.get("file") else
            semi_knowledge.reformatter_alias_proposal_from_prompt(prompt, product=product)
        )
        rows = [
            {"field": "target", "value": "data/flow-data/reformatter/<product>.json"},
            {"field": "product", "value": product or "(필요)"},
            {"field": "source", "value": source.get("file") or "prompt text"},
            {"field": "profile", "value": _flowi_profile_label(source_profile)},
            {"field": "proposed_rules", "value": str(len(proposal.get("rules") or []))},
            {"field": "discriminators", "value": ", ".join(proposal.get("discriminators") or [])},
            {"field": "status", "value": "proposal_only; admin apply required"},
        ]
        answer = (
            "real item alias/reformatter 후보를 만들었습니다.\n"
            "PC-CB-M1처럼 비슷한 item은 14x14/13x13/12x12, pitch, cell height, coordinate 같은 discriminator를 유지한 뒤 admin apply 해야 합니다. "
            "반영은 `/api/semiconductor/reformatter/apply`에서 기존 rule과 중복/validation을 확인하고 저장합니다."
        )
        return {
            "handled": True,
            "intent": "semiconductor_reformatter_proposal",
            "action": "propose_reformatter_alias_rules",
            "answer": answer,
            "proposal": proposal,
            "data_source": source,
            "source_profile": source_profile,
            "table": {"kind": "semiconductor_reformatter_proposal", "columns": ["field", "value"], "rows": rows},
            "feature": "diagnosis",
        }
    return {"handled": False}


def _is_semiconductor_diagnosis_prompt(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    phrase_terms = [
        "rca", "root cause", "원인", "원인 후보", "진단", "mechanism", "causal", "knowledge card",
        "dibl", "vth", "rolloff", "roll-off", "ioff", "rsd", "igate",
        "gate leakage", "sram", "vmin", "ca_rs", "ca rc", "ca_cd", "short lg", "gaa",
    ]
    if any(t in low for t in phrase_terms):
        return True
    return bool(re.search(r"(?<![a-z0-9])(ss|ion)(?![a-z0-9])", low))


def _handle_semiconductor_diagnosis_query(prompt: str, product: str, max_rows: int = 12) -> dict[str, Any]:
    if _is_rag_update_prompt(prompt):
        return {"handled": False}
    if not _is_semiconductor_diagnosis_prompt(prompt):
        return {"handled": False}
    try:
        source_filter = _flowi_dataset_source_from_prompt(prompt, product)
        source_profile = _flowi_dataset_profile_for_source(source_filter)
        if _flowi_source_profile_needs_clarification(source_filter, source_profile):
            return _flowi_source_profile_clarification(prompt, product, source_filter, source_profile, max_rows)
        report = semi_knowledge.run_diagnosis(
            prompt,
            product=product,
            filters={"source": source_filter or "flowi", **source_filter, "max_rows": max_rows},
            save=True,
        )
    except Exception as e:
        return {
            "handled": True,
            "intent": "semiconductor_diagnosis",
            "action": "run_semiconductor_diagnosis",
            "answer": f"반도체 진단 실행 중 오류가 발생했습니다: {e}",
            "blocked": False,
        }
    hyps = report.get("ranked_hypotheses") or []
    rows = [
        {
            "rank": h.get("rank"),
            "hypothesis": h.get("hypothesis"),
            "confidence": h.get("confidence"),
            "mechanism": h.get("electrical_mechanism"),
            "card": h.get("knowledge_card_id"),
        }
        for h in hyps[:max_rows]
    ]
    item_rows = [
        {
            "raw": r.get("raw_item"),
            "status": r.get("status"),
            "canonical": r.get("canonical_item_id") or ", ".join(c.get("canonical_item_id", "") for c in r.get("candidates") or []),
            "meaning": (r.get("item") or {}).get("meaning") or r.get("ambiguity") or "",
        }
        for r in (report.get("interpreted_items") or {}).get("resolved", [])
    ]
    top = hyps[:3]
    source_line = ""
    if source_filter.get("file"):
        source_line = f"\n데이터 source: {source_filter.get('file')}"
    elif source_filter.get("root"):
        source_line = f"\n데이터 source: {source_filter.get('root')}/{source_filter.get('product') or product}"
    if source_profile:
        source_line += f" ({_flowi_profile_label(source_profile)})"
    if top:
        lines = [f"{h.get('rank')}. {h.get('hypothesis')} (confidence {h.get('confidence')})" for h in top]
        answer = (
            "반도체 진단/RCA 단위기능으로 처리했습니다.\n"
            + "\n".join(lines)
            + source_line
            + "\n확정 원인이 아니라 item 의미, Knowledge Card, causal graph, 유사 case 기반 후보입니다."
        )
    else:
        answer = "반도체 진단/RCA 단위기능으로 보았지만 인식된 지표가 부족합니다." + source_line + "\nitem명과 unit/test_structure를 더 알려주세요."
    return {
        "handled": True,
        "intent": "semiconductor_diagnosis",
        "action": "run_semiconductor_diagnosis",
        "answer": answer,
        "diagnosis": report,
        "data_source": source_filter,
        "source_profile": source_profile,
        "table": {"kind": "semiconductor_rca_hypotheses", "columns": ["rank", "hypothesis", "confidence", "mechanism", "card"], "rows": rows},
        "items_table": {"kind": "semiconductor_item_resolution", "columns": ["raw", "status", "canonical", "meaning"], "rows": item_rows},
        "feature": "diagnosis",
        "slots": {
            "product": product,
            "source": source_filter,
            "items": report.get("feature_extractor", {}).get("items") or [],
            "modules": report.get("feature_extractor", {}).get("modules") or [],
        },
    }


def _flowi_source_files(source_type: str, product: str = "") -> list[Path]:
    st = _upper(source_type)
    if st == "FAB":
        return _fab_files(product)
    if st == "ET":
        return _et_files(product)
    if st == "INLINE":
        return _inline_files(product)
    if st == "ML_TABLE":
        return _ml_files(product)
    roots = _db_root_candidates(st)
    files: list[Path] = []
    for root in roots:
        files.extend(sorted(root.rglob("*.parquet")))
    return _filter_files_by_product(files, product)


def _flowi_step_filter_expr(cols: list[str], step: str):
    target = _upper(step)
    if not target:
        return None
    candidates = [
        _ci_col(cols, "func_step", "function_step", "FUNCTION_STEP", "step_name", "STEP_NAME"),
        _ci_col(cols, "step_id", "STEP_ID"),
        _ci_col(cols, "process_id", "PROCESS_ID"),
        _ci_col(cols, "ppid", "PPID"),
    ]
    expr = None
    for col in [c for c in candidates if c]:
        piece = pl.col(col).cast(_STR, strict=False).str.to_uppercase().str.contains(target, literal=True)
        expr = piece if expr is None else (expr | piece)
    return expr


def _flowi_lot_filter_expr(cols: list[str], root_lots: list[str], fab_lots: list[str]):
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    lot_wf_col = _ci_col(cols, "lot_wf", "LOT_WF")
    return _or_contains([c for c in (root_col, lot_col, fab_col, lot_wf_col) if c], [*(root_lots or []), *(fab_lots or [])])


def _handle_fab_progress_query(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    preview = _structure_flowi_function_call(prompt, product=product, max_rows=max_rows)
    selected = (preview.get("selected_function") or {}).get("name")
    if selected != "query_fab_progress":
        return {"handled": False}
    args = ((preview.get("function_call") or {}).get("function") or {}).get("arguments") or {}
    missing = (preview.get("validation") or {}).get("missing") or []
    if missing:
        return _flowi_preview_tool(preview, answer="FAB 진행 조회에 필요한 lot 조건을 보완해 주세요.")
    product_hint = str(args.get("product") or product or "")
    roots = [str(x) for x in args.get("root_lot_ids") or []]
    fabs = [str(x) for x in args.get("fab_lot_ids") or []]
    lots = roots + fabs + [str(x) for x in args.get("lot_ids") or []]
    files = _fab_files(product_hint)
    if not files:
        return {"handled": True, "intent": "fab_progress_lookup", "action": "query_fab_progress", "answer": "FAB parquet을 찾지 못했습니다.", "feature": "filebrowser"}
    try:
        lf = _scan_parquet(files)
        cols = _schema_names(lf)
        product_col = _ci_col(cols, "product", "PRODUCT")
        root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
        lot_col = _ci_col(cols, "lot_id", "LOT_ID")
        fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
        wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
        step_col = _ci_col(cols, "step_id", "STEP_ID")
        process_col = _ci_col(cols, "process_id", "PROCESS_ID")
        time_col = _ci_col(cols, "tkout_time", "TKOUT_TIME", "time", "TIME", "timestamp", "TIMESTAMP", "move_time", "MOVE_TIME", "updated_at", "UPDATED_AT")
        if product_hint and product_col:
            aliases = _product_aliases(product_hint)
            lf = lf.filter(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
        lot_expr = _flowi_lot_filter_expr(cols, roots, fabs or lots)
        if lot_expr is not None:
            lf = lf.filter(lot_expr)
        wf_expr = _wafer_match_expr(wafer_col, [str(w) for w in args.get("wafer_ids") or []])
        if wf_expr is not None:
            lf = lf.filter(wf_expr)
        exprs = [
            pl.col(product_col).cast(_STR, strict=False).alias("product") if product_col else pl.lit(product_hint).alias("product"),
            pl.col(root_col).cast(_STR, strict=False).alias("root_lot_id") if root_col else (
                pl.col(lot_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id") if lot_col else (pl.col(fab_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id") if fab_col else pl.lit("").alias("root_lot_id"))
            ),
            pl.col(lot_col).cast(_STR, strict=False).alias("lot_id") if lot_col else pl.lit("").alias("lot_id"),
            pl.col(fab_col).cast(_STR, strict=False).alias("fab_lot_id") if fab_col else pl.lit("").alias("fab_lot_id"),
            _wafer_key_expr(wafer_col).alias("wafer_id") if wafer_col else pl.lit("").alias("wafer_id"),
            pl.col(step_col).cast(_STR, strict=False).alias("step_id") if step_col else pl.lit("").alias("step_id"),
            pl.col(process_col).cast(_STR, strict=False).alias("process_id") if process_col else pl.lit("").alias("process_id"),
            pl.col(time_col).cast(_STR, strict=False).alias("tkout_time") if time_col else pl.lit("").alias("tkout_time"),
        ]
        df = lf.select(exprs).limit(50000).collect()
    except Exception as e:
        return {"handled": True, "intent": "fab_progress_lookup", "action": "query_fab_progress", "answer": f"FAB 진행 조회 실패: {e}", "feature": "filebrowser"}
    rows = df.to_dicts()
    if not rows:
        return {"handled": True, "intent": "fab_progress_lookup", "action": "query_fab_progress", "answer": "조건에 맞는 FAB 진행 row를 찾지 못했습니다.", "feature": "filebrowser"}
    rows.sort(key=lambda r: (_parse_flowi_datetime(r.get("tkout_time")) or datetime.min, _step_rank_key(r.get("step_id"))), reverse=True)
    cols_out = ["product", "root_lot_id", "fab_lot_id", "lot_id", "wafer_id", "step_id", "process_id", "tkout_time"]
    top = rows[0]
    answer = f"{top.get('fab_lot_id') or top.get('root_lot_id') or (lots[0] if lots else '')} 현재 위치는 step_id={top.get('step_id') or '-'} 입니다."
    if top.get("tkout_time"):
        answer += f" 최신 시간: {top.get('tkout_time')}."
    lot_list = [
        {
            "product": r.get("product") or product_hint,
            "root_lot": r.get("root_lot_id") or "",
            "fab_lot": r.get("fab_lot_id") or r.get("lot_id") or "",
            "wafer": r.get("wafer_id") or "",
            "current_step": r.get("step_id") or "",
            "tkout_time": r.get("tkout_time") or "",
        }
        for r in rows[:max(1, min(40, max_rows * 3))]
    ]
    return _flowi_set_inline_type({
        "handled": True,
        "intent": "fab_progress_lookup",
        "action": "query_fab_progress",
        "answer": answer,
        "feature": "filebrowser",
        "lot_list": lot_list,
        "filters": {"product": product_hint, "root_lot_ids": roots, "fab_lot_ids": fabs},
        "table": {"kind": "fab_progress_lookup", "title": "FAB progress", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in rows[:max(1, min(80, max_rows * 4))]], "total": len(rows)},
    }, "lot_list", prompt=prompt)
