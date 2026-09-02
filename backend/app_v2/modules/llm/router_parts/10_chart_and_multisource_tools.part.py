def _flowi_apply_wiki_prompt_interpretation(tool: dict[str, Any], interpretation: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(tool, dict) or not isinstance(interpretation, dict) or not interpretation.get("pre_route"):
        return tool
    additions = interpretation.get("retrieved_knowledge") if isinstance(interpretation.get("retrieved_knowledge"), list) else []
    if additions:
        tool["retrieved_knowledge"] = _merge_retrieved_knowledge(tool.get("retrieved_knowledge"), additions)
    existing_terms = tool.get("term_resolution") if isinstance(tool.get("term_resolution"), list) else []
    merged_terms: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in [*(interpretation.get("term_resolution") or []), *existing_terms]:
        if not isinstance(item, dict):
            continue
        token = str(item.get("token") or item.get("term") or "").strip()
        query_filter = str(item.get("query_filter") or "").strip()
        key = (token.casefold(), query_filter.casefold())
        if not token or key in seen:
            continue
        seen.add(key)
        merged_terms.append(item)
    if merged_terms:
        tool["term_resolution"] = merged_terms[:20]
    tool["wiki_interpretation"] = {
        "pre_route": True,
        "terms": interpretation.get("terms") or [],
        "prompt_hints": interpretation.get("prompt_hints") or [],
        "source": "agent_wiki_schema",
    }
    return tool


def _flowi_wiki_interpretation_prefers_splittable(prompt: str, interpretation: dict[str, Any], allowed_keys: set[str] | None = None) -> bool:
    if allowed_keys is not None and "splittable" not in allowed_keys:
        return False
    if not _lot_tokens(prompt):
        return False
    for item in interpretation.get("retrieved_knowledge") or []:
        if not isinstance(item, dict):
            continue
        relation = _upper(item.get("relation_id") or item.get("title") or "")
        column = _upper(item.get("column") or item.get("title") or "")
        if relation.startswith("ML_TABLE") and column.startswith(("KNOB_", "MASK_", "FAB_", "INLINE_", "VM_")):
            return True
        if column.startswith(("KNOB_", "MASK_")):
            return True
    return False


def _invoke_subagent(
    name: str,
    handler: Any,
    prompt: str,
    product: str,
    max_rows: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    started = datetime.now(timezone.utc)
    try:
        out = handler(prompt, product, max_rows)
        if not isinstance(out, dict):
            out = {"handled": False, "error": "handler returned non-dict"}
        status = "done" if out.get("handled") and not out.get("blocked") else ("blocked" if out.get("blocked") else "skipped")
    except Exception as exc:
        logger.warning("flowi subagent %s failed: %s", name, exc)
        out = {"handled": False, "error": str(exc)}
        status = "error"
    took_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    child = {
        "name": name,
        "status": status,
        "took_ms": max(0, took_ms),
        "intent": out.get("intent") or "",
        "action": out.get("action") or "",
        "feature": out.get("feature") or "",
        "evidence_count": (
            len((out.get("table") or {}).get("rows") or [])
            if isinstance(out.get("table"), dict)
            else len(out.get("points") or out.get("rows") or [])
        ),
        "error": out.get("error") or "",
    }
    return out, child


def _flowi_block_chart_config(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    for block in blocks:
        if not isinstance(block, dict):
            continue
        payload = block.get("payload") if isinstance(block.get("payload"), dict) else {}
        for key in ("chart_config", "config", "config_overrides"):
            cfg = payload.get(key)
            if isinstance(cfg, dict) and cfg:
                return cfg
    return {}


def _dashboard_chart_label(chart_type: str) -> str:
    labels = {
        "scatter": "산점도",
        "line": "라인 차트",
        "trend": "라인 차트",
        "bar": "막대 차트",
        "stacked_bar": "막대 차트",
        "area": "면적 차트",
        "combo": "복합 차트",
        "pie": "파이차트",
        "donut": "도넛 차트",
        "binning": "히스토그램",
        "pareto": "파레토",
        "box": "박스플롯",
        "boxplot": "박스플롯",
        "treemap": "트리맵",
        "heatmap": "히트맵",
        "correlation_matrix": "상관 히트맵",
        "wafer_map": "웨이퍼 맵",
        "classification": "분류 차트",
        "table": "테이블",
        "cross_table": "교차 테이블",
    }
    return labels.get(str(chart_type or "").strip(), "차트")


def _dashboard_chart_title(product: str, chart_type: str, metrics: list[dict[str, Any]] | None = None, config: dict[str, Any] | None = None) -> str:
    cfg = config if isinstance(config, dict) else {}
    metric_names = [str(m.get("metric") or "").strip() for m in (metrics or []) if isinstance(m, dict) and m.get("metric")]
    metric = str(cfg.get("metric") or cfg.get("item_id") or cfg.get("y_expr") or cfg.get("x_col") or (metric_names[0] if metric_names else "")).strip()
    source = str(cfg.get("source_type") or cfg.get("source") or "").strip()
    group = str(cfg.get("group_by") or cfg.get("groupby") or cfg.get("x_groupby") or cfg.get("color_col") or "").strip()
    subject = metric or source or group
    pieces = [str(product or "").strip(), subject, _dashboard_chart_label(chart_type)]
    return " ".join(piece for piece in pieces if piece).strip() or _dashboard_chart_label(chart_type)


def _flowi_compact_json(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def _flowi_dashboard_sql_from_config(cfg: dict[str, Any]) -> str:
    source_type = _upper(cfg.get("source_type") or cfg.get("source") or "")
    metric = _text(cfg.get("item_id") or cfg.get("metric") or cfg.get("y_col") or cfg.get("y_expr"))
    product = _text(cfg.get("product"))
    lots = [_text(x) for x in (cfg.get("lots") or []) if _text(x)] if isinstance(cfg.get("lots"), list) else []
    raw_time_col = _text(cfg.get("x_col") or cfg.get("time_col") or "tkout_time")
    time_col = raw_time_col if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", raw_time_col) else "tkout_time"
    if _text(cfg.get("chart_type")) == "wafer_map" and source_type in ("INLINE", "ET", "VM"):
        raw_x = _text(cfg.get("coord_x") or cfg.get("x_col") or ("chip_x_pos" if source_type == "ET" else "shot_x"))
        raw_y = _text(cfg.get("coord_y") or cfg.get("y_col") or ("chip_y_pos" if source_type == "ET" else "shot_y"))
        coord_x = raw_x if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", raw_x) else "shot_x"
        coord_y = raw_y if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", raw_y) else "shot_y"
        where = ["item_id = :item_id"] if metric else []
        if product:
            where.append("product = :product")
        if lots:
            where.append("root_lot_id IN (:lots)")
        if source_type == "INLINE":
            where.append("UPPER(REGEXP_REPLACE(TRIM(CAST(subitem_id AS VARCHAR)), '[\\s_.-]+', '', 'g')) NOT IN ('AVG','AVERAGE','MEAN','MED','MEDIAN','STD','STDEV','STDDEV','MIN','MINIMUM','MAX','MAXIMUM','Q1','Q3','QUARTILE1','QUARTILE3')")
        where_sql = f" WHERE {' AND '.join(where)}" if where else ""
        shot_id = ", subitem_id" if source_type == "INLINE" else ""
        return (
            f"SELECT root_lot_id, wafer_id{shot_id}, {coord_x}, {coord_y}, value "
            f"FROM {source_type}{where_sql}"
        )
    if source_type == "INLINE":
        where = ["item_id = :item_id"] if metric else []
        if product:
            where.append("product = :product")
        if lots:
            where.append("root_lot_id IN (:lots)")
        where.append("UPPER(REGEXP_REPLACE(TRIM(CAST(subitem_id AS VARCHAR)), '[\\s_.-]+', '', 'g')) NOT IN ('AVG','AVERAGE','MEAN','MED','MEDIAN','STD','STDEV','STDDEV','MIN','MINIMUM','MAX','MAXIMUM','Q1','Q3','QUARTILE1','QUARTILE3')")
        where_sql = f"WHERE {' AND '.join(where)} " if where else ""
        agg = _text(cfg.get("aggregation")) or "avg"
        y_sql = {
            "avg": "AVG(value)", "median": "MEDIAN(value)", "max": "MAX(value)",
            "p90": "QUANTILE_CONT(value, 0.9)", "p10": "QUANTILE_CONT(value, 0.1)",
        }.get(agg)
        if agg == "shot" or y_sql is None:
            return (
                f"SELECT root_lot_id, wafer_id, subitem_id, {time_col}, value AS y "
                "FROM INLINE " + where_sql + f"ORDER BY {time_col}"
            )
        return (
            f"SELECT root_lot_id, wafer_id, {time_col}, {y_sql} AS y "
            "FROM INLINE " + where_sql
            + f"GROUP BY root_lot_id, wafer_id, {time_col}"
        )
    if source_type in ("ET", "VM"):
        where = ["item_id = :item_id"] if metric else []
        if product:
            where.append("product = :product")
        if cfg.get("step_id"):
            where.append("step_id = :step_id")
        if lots:
            where.append("root_lot_id IN (:lots)")
        where_sql = f"WHERE {' AND '.join(where)} " if where else ""
        agg = _text(cfg.get("aggregation")) or ("avg" if source_type == "VM" else "median")
        y_sql = {
            "median": "MEDIAN(value)", "avg": "AVG(value)", "max": "MAX(value)",
            "p90": "QUANTILE_CONT(value, 0.9)", "p10": "QUANTILE_CONT(value, 0.1)",
        }.get(agg)
        if agg == "shot" or y_sql is None:
            # shot = 집계 없이 전체 측정 point
            return (
                f"SELECT product, root_lot_id, wafer_id{', chip_x_pos, chip_y_pos' if source_type == 'ET' else ''}, {time_col}, value AS y "
                f"FROM {source_type} " + where_sql + f"ORDER BY {time_col}"
            )
        return (
            f"SELECT product, root_lot_id, wafer_id, {time_col}, {y_sql} AS y "
            f"FROM {source_type} " + where_sql
            + f"GROUP BY product, root_lot_id, wafer_id, {time_col}"
        )
    return ""


def _flowi_dashboard_base_data_query(
    prompt: str,
    product: str,
    tool: dict[str, Any],
    chart_result: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    sources = chart_result.get("sources") if isinstance(chart_result.get("sources"), dict) else {}
    slots = tool.get("slots") if isinstance(tool.get("slots"), dict) else {}
    source_type = _text(
        sources.get("source_type")
        or chart_result.get("source_type")
        or config.get("source_type")
        or config.get("source")
        or slots.get("source_type")
    )
    files = sources.get("files") or sources.get("source_files") or tool.get("files") or tool.get("source_files") or []
    if isinstance(files, (str, Path)):
        files = [str(files)]
    elif not isinstance(files, list):
        files = []
    query = {
        "prompt": prompt,
        "product": _text(config.get("product") or product or slots.get("product")),
        "tool_intent": tool.get("intent") or "",
        "source_type": source_type,
        "db": sources.get("db") or sources.get("database") or (f"1.RAWDATA_DB_{source_type}" if source_type in {"INLINE", "ET", "FAB", "VM"} else ""),
        "files": [str(x) for x in files[:40]],
        "file_count": sources.get("file_count")
        or sources.get("inline_file_count")
        or sources.get("et_file_count")
        or sources.get("fab_file_count")
        or sources.get("vm_file_count")
        or len(files),
        "sql": sources.get("sql") or sources.get("sql_equivalent") or tool.get("sql") or _flowi_dashboard_sql_from_config(config),
        "filters": chart_result.get("filters") or sources.get("filters") or {
            k: v for k, v in {
                "product": config.get("product") or product or slots.get("product"),
                "item_id": config.get("item_id") or config.get("metric"),
                "step_id": config.get("step_id"),
                "lots": config.get("lots"),
            }.items() if v not in (None, "", [])
        },
        "aggregation": chart_result.get("aggregations") or sources.get("aggregation") or ({source_type: config.get("aggregation")} if source_type and config.get("aggregation") else {}),
        "join_keys": chart_result.get("join_cols") or config.get("join_cols") or ["root_lot_id", "wafer_id"],
    }
    return {k: v for k, v in query.items() if v not in (None, "", [], {})}


def _augment_dashboard_tool(tool: dict[str, Any], prompt: str, product: str = "", username: str = "flowi") -> dict[str, Any]:
    if not isinstance(tool, dict):
        return tool
    chart_result = tool.get("chart_result") if isinstance(tool.get("chart_result"), dict) else {}
    chart = tool.get("chart") if isinstance(tool.get("chart"), dict) else {}
    if tool.get("feature") != "dashboard" and not (chart_result or chart):
        return tool
    metrics = []
    if isinstance(tool.get("slots"), dict) and isinstance(tool["slots"].get("metrics"), list):
        metrics = [{"metric": m} for m in tool["slots"].get("metrics") if m]
    if not metrics and isinstance(tool.get("slots"), dict) and tool["slots"].get("metric"):
        metrics = [{"metric": tool["slots"].get("metric")}]
    if not metrics:
        metrics = _metric_alias_hits(prompt)
    inferred_chart_type = _flowi_chart_type_from_prompt(prompt, metrics) or "scatter"
    chart_kind = str(chart_result.get("chart_type") or chart_result.get("kind") or chart.get("kind") or "")
    chart_kind_norm = chart_kind.replace("dashboard_", "")
    chart_type = str(tool.get("chart_type") or "")
    if not chart_type:
        if inferred_chart_type and (not chart_kind_norm or (chart_kind_norm == "scatter" and inferred_chart_type != "scatter")):
            chart_type = inferred_chart_type
        else:
            chart_type = chart_kind or inferred_chart_type or "scatter"
    chart_type = chart_type.replace("dashboard_", "")
    if chart_type in {"box", "dashboard_box"}:
        chart_type = "boxplot"
    if chart_type in {"line", "dashboard_line"}:
        chart_type = "trend"
    if chart_type in {"group_bar", "dashboard_group_bar", "stacked_bar"}:
        chart_type = "bar"
    if chart_result.get("kind") == "dashboard_wafer_map":
        chart_type = "wafer_map"
    config = _flowi_dashboard_default_config(prompt, chart_type, metrics, product=product)
    for extra_config in (tool.get("config"), tool.get("chart_config"), chart_result.get("chart_config"), chart_result.get("config"), chart_result.get("config_overrides")):
        if isinstance(extra_config, dict):
            config.update({k: v for k, v in extra_config.items() if v is not None})
    slots = tool.get("slots") if isinstance(tool.get("slots"), dict) else {}
    product_hint = str(config.get("product") or slots.get("product") or product or "").strip()
    metric_hint = next((str(m.get("metric") or "").strip() for m in metrics if isinstance(m, dict) and m.get("metric")), "")
    if product_hint:
        config.setdefault("product", product_hint)
    if metric_hint:
        config.setdefault("metric", metric_hint)
        config.setdefault("item_id", metric_hint)
    config["chart_type"] = chart_type
    config.setdefault("title", _dashboard_chart_title(product_hint, chart_type, metrics, config))
    missing = []
    for raw_missing in (tool.get("missing"), (tool.get("validation") or {}).get("missing") if isinstance(tool.get("validation"), dict) else None):
        if isinstance(raw_missing, list):
            missing.extend(str(x) for x in raw_missing if str(x or "").strip())
    missing = list(dict.fromkeys(missing))
    if missing:
        if not (tool.get("intent") and str(tool.get("action") or "") == "collect_required_fields"):
            tool["intent"] = "dashboard_chart_draft_needs_context"
        tool["action"] = "collect_required_fields"
        tool["missing"] = missing
        tool.setdefault("question", f"{_dashboard_chart_label(chart_type)} 생성을 계속하려면 {', '.join(missing)} 값을 보완해 주세요.")
        tool.setdefault("pending_prompt", prompt)
    retrieved_knowledge = _dashboard_agent_wiki_knowledge(prompt, product=product, chart_type=chart_type, metrics=metrics)
    if retrieved_knowledge:
        merged = _merge_retrieved_knowledge(tool.get("retrieved_knowledge"), retrieved_knowledge)
        tool["retrieved_knowledge"] = merged
        config["retrieved_knowledge_ids"] = [row.get("id") for row in merged if isinstance(row, dict) and row.get("id")]
    data = _dashboard_chart_data_for_stats(chart_result)
    stats_cols = config.get("stats_columns")
    stats_table = None if stats_cols is None else dashboard_charting.stats_table_from_points(data, stats_cols if isinstance(stats_cols, list) else None)
    fit = _fit_with_equation(chart_result.get("fit") or chart_result.get("fit_params") or tool.get("fit") or {})
    base_data_query = _flowi_dashboard_base_data_query(prompt, product, tool, chart_result, config)
    table = tool.get("table") if isinstance(tool.get("table"), dict) else {}
    table_rows = [row for row in (table.get("rows") or []) if isinstance(row, dict)]
    try:
        data_total = int(table.get("total") if table.get("total") is not None else len(data))
    except (TypeError, ValueError):
        data_total = len(data)
    data_threshold = 500
    sql_text = _text(base_data_query.get("sql") or _flowi_dashboard_sql_from_config(config))
    chart_data_contract = {
        "mode": "rows" if data_total <= data_threshold and bool(table_rows or data) else "sql",
        "row_count": data_total,
        "inline_row_limit": data_threshold,
        "columns": [str((col.get("key") if isinstance(col, dict) else col) or "") for col in (table.get("columns") or [])],
        "rows": table_rows if table_rows else (data[:data_threshold] if isinstance(data, list) else []),
        "sql": sql_text,
        "db": base_data_query.get("db") or "",
        "files": base_data_query.get("files") or [],
    }
    session_id = tool.get("chart_session_id") or chart_result.get("chart_session_id")
    if not session_id:
        try:
            session_id = dashboard_charting.save_chart_session({
                "username": username,
                "chart_type": chart_type,
                "config": config,
                "base_data_query": base_data_query,
                "data": data,
            })
        except Exception:
            session_id = ""
    tool.update({
        "chart_type": chart_type,
        "config": config,
        "chart_config": config,
        "data": data,
        "fit": fit,
        "stats_table": stats_table,
        "chart_session_id": session_id,
        "base_data_query": base_data_query,
        "chart_data_contract": chart_data_contract,
    })
    if isinstance(chart_result, dict):
        chart_result.update({
            "chart_type": chart_type,
            "title": chart_result.get("title") or config.get("title") or _dashboard_chart_title(product_hint, chart_type, metrics, config),
            "config": config,
            "chart_config": config,
            "retrieved_knowledge": tool.get("retrieved_knowledge") or [],
            "fit_params": fit,
            "stats_table": stats_table,
            "chart_session_id": session_id,
            "base_data_query": base_data_query,
            "chart_data_contract": chart_data_contract,
        })
        tool["chart_result"] = chart_result
    return _flowi_set_inline_type(tool, "chart", prompt=prompt)


def _active_chart_session_id(agent_context: dict[str, Any] | None) -> str:
    ctx = agent_context if isinstance(agent_context, dict) else {}
    direct = str(ctx.get("chart_session_id") or "").strip()
    if direct:
        return direct
    messages = ctx.get("messages") if isinstance(ctx.get("messages"), list) else []
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        sid = str(msg.get("chart_session_id") or "").strip()
        if sid:
            return sid
    return ""


def _flowi_chart_raw_data_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    wants_download = any(t in low or t in text for t in ("download", "export", "csv", "다운", "내려받", "내보내"))
    wants_data = any(t in low or t in text for t in ("raw data", "raw", "data", "데이터", "원본", "로우데이터", "표"))
    explicit_raw = any(t in low or t in text for t in ("raw data", "raw", "원본 데이터", "로우데이터"))
    asks_for_data = any(t in low or t in text for t in ("show", "give", "줘", "달라", "보여", "확인"))
    return bool((wants_download and wants_data) or (explicit_raw and asks_for_data))


def _flowi_chart_session_allowed(session: dict[str, Any], username: str, role: str = "user") -> bool:
    owner = str(session.get("username") or "").strip()
    user = str(username or "").strip()
    if role == "admin":
        return True
    return not owner or owner in {"flowi", "user", user}


def _flowi_chart_session_rows(session: dict[str, Any]) -> list[dict[str, Any]]:
    data = session.get("data") if isinstance(session, dict) else []
    rows: list[dict[str, Any]] = []
    if isinstance(data, list):
        rows = [row for row in data if isinstance(row, dict)]
    elif isinstance(data, dict):
        for key in ("points", "rows", "groups", "boxes", "stats_table", "data"):
            value = data.get(key)
            if isinstance(value, list):
                rows = [row for row in value if isinstance(row, dict)]
                if rows:
                    break
    return rows


def _flowi_chart_raw_columns(rows: list[dict[str, Any]]) -> list[str]:
    cols: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row.keys():
            text = str(key or "").strip()
            if not text or text.startswith("__") or text in seen:
                continue
            seen.add(text)
            cols.append(text)
    return cols


def _flowi_csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def _flowi_chart_raw_filename(session: dict[str, Any], session_id: str) -> str:
    cfg = session.get("config") if isinstance(session.get("config"), dict) else {}
    title = str(cfg.get("title") or session.get("chart_type") or "chart_raw").strip()
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", title).strip("._") or "chart_raw"
    return f"flowi_{safe}_{str(session_id or '')[:8]}.csv"


def _flowi_chart_raw_data_provenance_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    has_chart_data = any(t in low or t in text for t in ("chart", "차트", "raw data", "raw", "data", "데이터"))
    asks_origin = any(t in low or t in text for t in ("how", "explain", "sql", "db", "file", "files", "source", "query", "어떻게", "뽑", "추출", "쿼리", "근거", "출처", "어느 DB"))
    return bool(has_chart_data and asks_origin)


def _flowi_chart_session_provenance(session: dict[str, Any]) -> dict[str, Any]:
    cfg = session.get("config") if isinstance(session.get("config"), dict) else {}
    query = session.get("base_data_query") if isinstance(session.get("base_data_query"), dict) else {}
    source_type = _text(query.get("source_type") or cfg.get("source_type") or cfg.get("source"))
    files = query.get("files") or query.get("source_files") or []
    if isinstance(files, (str, Path)):
        files = [str(files)]
    elif not isinstance(files, list):
        files = []
    rows = _flowi_chart_session_rows(session)
    return {
        "source_type": source_type,
        "db": _text(query.get("db") or query.get("database") or (f"1.RAWDATA_DB_{source_type}" if source_type else "")),
        "files": [str(x) for x in files],
        "file_count": query.get("file_count") or len(files),
        "sql": _text(query.get("sql") or query.get("sql_equivalent") or _flowi_dashboard_sql_from_config(cfg)),
        "filters": query.get("filters") or {},
        "aggregation": query.get("aggregation") or cfg.get("aggregation") or {},
        "join_keys": query.get("join_keys") or ["root_lot_id", "wafer_id"],
        "knob_join": query.get("knob_join") or {},
        "row_count": len(rows),
    }


def _handle_dashboard_chart_raw_data_provenance_followup(
    prompt: str,
    agent_context: dict[str, Any] | None,
    *,
    username: str = "flowi",
    role: str = "user",
) -> dict[str, Any]:
    if not _flowi_chart_raw_data_provenance_intent(prompt):
        return {"handled": False}
    sid = _active_chart_session_id(agent_context)
    if not sid:
        return {
            "handled": True,
            "intent": "dashboard_chart_raw_data_provenance",
            "action": "collect_required_fields",
            "feature": "dashboard",
            "missing": ["chart_session_id"],
            "answer": "직전 chart session을 찾지 못했습니다. 먼저 Home에서 차트를 만든 뒤 raw data 추출 근거를 물어봐 주세요.",
        }
    try:
        session = dashboard_charting.load_chart_session(sid)
    except FileNotFoundError:
        return {
            "handled": True,
            "intent": "dashboard_chart_raw_data_provenance",
            "action": "explain_chart_raw_data_query",
            "feature": "dashboard",
            "blocked": True,
            "answer": "chart session을 찾지 못했습니다. 차트를 다시 만든 뒤 물어봐 주세요.",
        }
    if not _flowi_chart_session_allowed(session, username, role):
        return {
            "handled": True,
            "intent": "dashboard_chart_raw_data_provenance",
            "action": "explain_chart_raw_data_query",
            "feature": "dashboard",
            "blocked": True,
            "answer": "다른 사용자의 chart session raw data 추출 근거는 볼 수 없습니다.",
        }
    prov = _flowi_chart_session_provenance(session)
    file_label = ", ".join(prov["files"][:8]) if prov["files"] else f"{prov.get('file_count') or 0} files"
    sql = prov.get("sql") or "저장된 SQL equivalent가 없습니다. chart config/filter 기준으로만 추적 가능합니다."
    answer = (
        f"직전 chart session({sid[:8]}) raw data는 {prov.get('source_type') or 'source'} "
        f"DB={prov.get('db') or '-'}, Files={file_label} 기준으로 뽑았습니다. "
        f"SQL/filter: {sql}"
    )
    rows = [
        {"field": "chart_session_id", "value": sid},
        {"field": "source_type", "value": prov.get("source_type") or ""},
        {"field": "db", "value": prov.get("db") or ""},
        {"field": "files", "value": file_label},
        {"field": "sql", "value": sql},
        {"field": "filters", "value": _flowi_compact_json(prov.get("filters"))},
        {"field": "aggregation", "value": _flowi_compact_json(prov.get("aggregation"))},
        {"field": "join_keys", "value": _flowi_compact_json(prov.get("join_keys"))},
        {"field": "knob_join", "value": _flowi_compact_json(prov.get("knob_join"))},
        {"field": "row_count", "value": str(prov.get("row_count") or 0)},
    ]
    return {
        "handled": True,
        "intent": "dashboard_chart_raw_data_provenance",
        "action": "explain_chart_raw_data_query",
        "feature": "dashboard",
        "answer": answer,
        "chart_session_id": sid,
        "provenance": prov,
        "table": {
            "kind": "dashboard_chart_raw_data_provenance",
            "title": "Chart raw data provenance",
            "placement": "below",
            "columns": _table_columns(["field", "value"]),
            "rows": rows,
            "total": len(rows),
        },
    }


def _flowi_chart_raw_download_payload(
    chart_session_id: str,
    *,
    username: str = "",
    role: str = "user",
) -> tuple[dict[str, Any], bytes]:
    sid = str(chart_session_id or "").strip()
    if not sid:
        raise HTTPException(400, {"code": "missing_chart_session_id", "message": "chart_session_id is required"})
    try:
        session = dashboard_charting.load_chart_session(sid)
    except FileNotFoundError:
        raise HTTPException(404, {"code": "chart_session_not_found", "message": "Chart session not found"})
    if not _flowi_chart_session_allowed(session, username, role):
        raise HTTPException(403, {"code": "chart_session_forbidden", "message": "Chart session belongs to another user"})
    rows = _flowi_chart_session_rows(session)
    columns = _flowi_chart_raw_columns(rows)
    if not rows or not columns:
        raise HTTPException(404, {"code": "chart_raw_data_empty", "message": "Chart session has no raw data rows"})
    from routers import filebrowser as filebrowser_router

    settings = filebrowser_router._load_filebrowser_settings()
    max_rows = filebrowser_router._csv_download_max_rows(settings.get("csv_download_max_rows"))
    max_bytes = filebrowser_router._csv_download_max_bytes(None, settings)
    filebrowser_router._guard_source_operation(
        all_columns=columns,
        sql="",
        select_cols="",
        source_size=0,
        settings=settings,
        operation="download",
    )
    if len(rows) > max_rows:
        raise HTTPException(
            400,
            {
                "code": "download_too_large",
                "message": f"Chart raw data is {len(rows):,} rows, above the {max_rows:,} row limit.",
                "result_rows": len(rows),
                "max_rows": max_rows,
            },
        )
    normalized = [{col: _flowi_csv_cell(row.get(col)) for col in columns} for row in rows]
    df = pl.DataFrame(normalized)
    csv_bytes = filebrowser_router._csv_bytes_checked(df, max_bytes)
    meta = {
        "chart_session_id": sid,
        "filename": _flowi_chart_raw_filename(session, sid),
        "row_count": len(rows),
        "column_count": len(columns),
        "columns": columns,
        "max_rows": max_rows,
        "max_bytes": max_bytes,
        "chart_type": session.get("chart_type") or (session.get("config") or {}).get("chart_type") or "",
    }
    return meta, csv_bytes


def _handle_dashboard_chart_raw_data_followup(
    prompt: str,
    agent_context: dict[str, Any] | None,
    max_rows: int,
    *,
    username: str = "flowi",
    role: str = "user",
) -> dict[str, Any]:
    if not _flowi_chart_raw_data_intent(prompt):
        return {"handled": False}
    sid = _active_chart_session_id(agent_context)
    if not sid:
        return {
            "handled": True,
            "intent": "dashboard_chart_raw_data",
            "action": "collect_required_fields",
            "feature": "dashboard",
            "missing": ["chart_session_id"],
            "answer": "직전 chart session을 찾지 못했습니다. 먼저 Home에서 차트를 만든 뒤 raw data를 요청해 주세요.",
        }
    try:
        meta, _csv_bytes = _flowi_chart_raw_download_payload(sid, username=username, role=role)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        return {
            "handled": True,
            "intent": "dashboard_chart_raw_data",
            "action": "export_chart_raw_data",
            "feature": "dashboard",
            "blocked": True,
            "answer": detail.get("message") or "chart raw data CSV 다운로드 제한을 통과하지 못했습니다.",
            "validation": {
                "status": "blocked",
                "reason": detail.get("code") or exc.status_code,
                **{k: v for k, v in detail.items() if k not in {"message"}},
            },
        }
    session = dashboard_charting.load_chart_session(sid)
    rows = _flowi_chart_session_rows(session)
    columns = meta.get("columns") or _flowi_chart_raw_columns(rows)
    preview_limit = max(1, min(int(max_rows or 12), 24))
    return {
        "handled": True,
        "intent": "dashboard_chart_raw_data",
        "action": "export_chart_raw_data",
        "feature": "dashboard",
        "answer": (
            f"직전 chart session({sid[:8]})의 raw data를 CSV로 내려받을 수 있습니다. "
            f"FileBrowser 제한 기준: {meta['row_count']:,}/{meta['max_rows']:,}행, "
            f"최대 {meta['max_bytes']:,} bytes."
        ),
        "chart_session_id": sid,
        "raw_data_download": {
            "url": f"/api/llm/flowi/chart-session/raw-data.csv?chart_session_id={sid}",
            **{k: v for k, v in meta.items() if k != "columns"},
        },
        "table": {
            "kind": "dashboard_chart_raw_data_preview",
            "title": "Chart raw data preview",
            "placement": "below",
            "columns": _table_columns(columns),
            "rows": [{col: row.get(col, "") for col in columns} for row in rows[:preview_limit]],
            "total": len(rows),
        },
        "validation": {
            "rows": len(rows),
            "columns": len(columns),
            "download_max_rows": meta["max_rows"],
            "download_max_bytes": meta["max_bytes"],
        },
    }


def _chart_refine_action(prompt: str) -> tuple[str, Any] | None:
    text = str(prompt or "")
    low = text.lower()
    if any(t in text for t in ("글씨 키워", "글자 키워")):
        return ("font_size_delta", 2)
    if any(t in text for t in ("글씨 줄여", "글자 줄여")):
        return ("font_size_delta", -2)
    if "축 라벨 키워" in text:
        return ("axis_label_size_delta", 2)
    if "범례 빼" in text or "legend 숨겨" in text or "legend hide" in low:
        return ("legend", False)
    if "라이트" in text or "light" in low:
        return ("theme", "light")
    if "다크" in text or "dark" in low:
        return ("theme", "dark")
    if "y축 log" in text or "y log" in low:
        return ("y_scale", "log")
    m = re.search(r"타이틀\s*['\"]([^'\"]{1,80})['\"]\s*로\s*바꿔", text)
    if m:
        return ("title", m.group(1))
    return None


def _handle_dashboard_chart_refine(prompt: str, me: dict[str, Any], agent_context: dict[str, Any] | None) -> dict[str, Any]:
    action = _chart_refine_action(prompt)
    if not action:
        return {"handled": False}
    sid = _active_chart_session_id(agent_context)
    if not sid:
        return {"handled": False}
    try:
        refined = dashboard_charting.refine_chart_session(sid, action[0], action[1], username=me.get("username") or "user")
    except FileNotFoundError:
        return {"handled": True, "intent": "dashboard_chart_refine", "feature": "dashboard", "answer": "수정할 차트 세션을 찾지 못했습니다.", "missing": ["chart_session_id"]}
    return {
        "handled": True,
        "intent": "dashboard_chart_refine",
        "action": "refine_chart_session",
        "feature": "dashboard",
        "answer": "차트 설정을 수정했습니다.",
        "chart_config": refined.get("config") if isinstance(refined, dict) else {},
        **refined,
    }


def _handle_dashboard_chart_session_fit(session: dict[str, Any], sid: str, cfg: dict[str, Any]) -> dict[str, Any]:
    rows = _flowi_chart_session_rows(session)
    fit = _chart_fit_from_rows(rows)
    if not fit:
        return {
            "handled": True,
            "intent": "dashboard_chart_fit",
            "feature": "dashboard",
            "answer": "직전 chart session에서 1차식 fitting에 필요한 numeric x/y point를 찾지 못했습니다.",
            "missing": ["numeric_x_y"],
        }
    refined = dashboard_charting.refine_chart_session(sid, "fit", "linear", username="flowi")
    refined_cfg = refined.get("config") if isinstance(refined.get("config"), dict) else cfg
    chart_type = str(refined.get("chart_type") or refined_cfg.get("chart_type") or session.get("chart_type") or "scatter").replace("dashboard_", "")
    chart_result = {
        "ok": True,
        "kind": f"dashboard_{chart_type}",
        "chart_type": chart_type,
        "title": refined_cfg.get("title") or cfg.get("title") or "Flow-i chart",
        "points": rows,
        "total": len(rows),
        "config": refined_cfg,
        "chart_config": refined_cfg,
        "fit": fit,
        "fit_params": fit,
        "chart_session_id": sid,
    }
    return {
        "handled": True,
        "intent": "dashboard_chart_fit",
        "action": "refine_chart_session",
        "feature": "dashboard",
        "answer": f"직전 chart session({sid[:8]})에 1차식 fitting line과 R²={fit.get('r2')}를 추가했습니다.",
        "chart_type": chart_type,
        "config": refined_cfg,
        "chart_config": refined_cfg,
        "chart_result": chart_result,
        "fit": fit,
        "chart_session_id": sid,
    }


def _flowi_chart_session_lot_hints(rows: list[dict[str, Any]], cfg: dict[str, Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        text = _upper(value)
        if text and text not in seen:
            seen.add(text)
            out.append(text)

    if isinstance(cfg.get("lots"), list):
        for value in cfg.get("lots") or []:
            add(value)
    for row in rows:
        add(row.get("root_lot_id"))
    for row in rows:
        add(row.get("lot_wf") or row.get("label"))
    return out[:500]


def _flowi_chart_row_lookup_keys(row: dict[str, Any]) -> list[str]:
    keys: list[str] = []

    def add(value: Any) -> None:
        text = _upper(value)
        if text and text not in keys:
            keys.append(text)

    root = row.get("root_lot_id")
    wafer = row.get("wafer_id")
    add(row.get("lot_wf"))
    add(row.get("label"))
    add(_flowi_lot_wf_id(root, wafer))
    add(root)
    return keys


def _flowi_collect_knob_lookup(knob: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    lf = knob.get("lf")
    if lf is None:
        return {}, []
    df = lf.limit(50000).collect()
    lookup: dict[str, dict[str, Any]] = {}
    rows = df.to_dicts()
    for row in rows:
        value = _text(row.get("color_value"))
        if not value:
            continue
        payload = {"color_value": value, "color_n": row.get("color_n") or ""}
        root = row.get("root_lot_id")
        wafer = row.get("wafer_id")
        candidates = [
            row.get("lot_wf"),
            _flowi_lot_wf_id(root, wafer),
            root,
        ]
        for key in candidates:
            norm = _upper(key)
            if norm and norm not in lookup:
                lookup[norm] = payload
    return lookup, rows


def _handle_dashboard_chart_session_knob_coloring(
    prompt: str,
    product: str,
    max_rows: int,
    session: dict[str, Any],
    sid: str,
    cfg: dict[str, Any],
    *,
    fit_requested: bool = False,
    username: str = "flowi",
) -> dict[str, Any]:
    rows = [dict(row) for row in _flowi_chart_session_rows(session)]
    if not rows:
        return {
            "handled": True,
            "intent": "dashboard_chart_knob_coloring",
            "action": "refine_chart_session_knob_coloring",
            "feature": "dashboard",
            "blocked": True,
            "answer": "직전 chart session에 재사용할 raw data가 없습니다. 차트를 다시 만든 뒤 knob coloring을 요청해 주세요.",
        }
    product_hint = _text(cfg.get("product") or product)
    if not product_hint:
        return {
            "handled": True,
            "intent": "dashboard_chart_knob_coloring",
            "action": "collect_required_fields",
            "feature": "dashboard",
            "missing": ["product"],
            "answer": "Knob coloring을 하려면 제품명이 필요합니다. 제품명을 알려주세요.",
        }
    metric = _text(cfg.get("metric") or cfg.get("item_id") or cfg.get("y_label") or cfg.get("title"))
    lots = _flowi_chart_session_lot_hints(rows, cfg)
    try:
        knob = _flowi_knob_lf(product_hint, lots, prompt, [metric] if metric else [])
    except Exception as exc:
        logger.warning("flowi chart session knob lookup failed: %s", exc)
        knob = {"ok": False, "error": str(exc)}
    if not knob.get("ok"):
        return {
            "handled": True,
            "intent": "dashboard_chart_knob_coloring",
            "action": "refine_chart_session_knob_coloring",
            "feature": "dashboard",
            "blocked": True,
            "answer": knob.get("error") or "ML_TABLE에서 coloring 기준 KNOB을 찾지 못했습니다.",
            "validation": {"status": "blocked", "reason": "knob_lookup_failed", **{k: v for k, v in knob.items() if k != "lf"}},
        }
    try:
        lookup, knob_rows = _flowi_collect_knob_lookup(knob)
    except Exception as exc:
        logger.warning("flowi chart session knob collect failed: %s", exc)
        return {
            "handled": True,
            "intent": "dashboard_chart_knob_coloring",
            "action": "refine_chart_session_knob_coloring",
            "feature": "dashboard",
            "blocked": True,
            "answer": f"KNOB join 데이터를 읽는 중 실패했습니다: {exc}",
        }
    color_by = _text(knob.get("display_name") or knob.get("knob_col") or "KNOB")
    excluded_values = {_text(x) for x in (knob.get("excluded_values") or []) if _text(x)}
    colored_rows: list[dict[str, Any]] = []
    color_counts: dict[str, int] = {}
    missing_color_count = 0
    for row in rows:
        matched = next((lookup.get(key) for key in _flowi_chart_row_lookup_keys(row) if lookup.get(key)), None)
        color_value = _text((matched or {}).get("color_value"))
        if excluded_values and color_value in excluded_values:
            continue
        out_row = dict(row)
        out_row["color_by"] = color_by
        out_row["color_value"] = color_value
        out_row["color_n"] = (matched or {}).get("color_n") or ""
        if color_value:
            color_counts[color_value] = color_counts.get(color_value, 0) + 1
        else:
            missing_color_count += 1
        colored_rows.append(out_row)
    chart_type = str(session.get("chart_type") or cfg.get("chart_type") or "scatter").replace("dashboard_", "") or "scatter"
    refined_cfg = dict(cfg)
    refined_cfg.update({
        "chart_type": chart_type,
        "product": product_hint,
        "color_by": color_by,
        "color_missing": "gray",
        "knob_column": knob.get("knob_col") or "",
        "knob_join_source": f"ML_TABLE_{product_hint}",
    })
    if fit_requested:
        refined_cfg["fit"] = "linear"
    fit = _chart_fit_from_rows(colored_rows) if fit_requested else {}
    base_query = dict(session.get("base_data_query") or {})
    base_query["knob_join"] = {
        "reuse_base_chart_raw_data": True,
        "source": f"ML_TABLE_{product_hint}",
        "knob_column": knob.get("knob_col") or "",
        "display_name": color_by,
        "join_keys": ["lot_wf", "root_lot_id", "wafer_id"],
        "base_chart_session_id": sid,
        "matched_rows": len([row for row in colored_rows if _text(row.get("color_value"))]),
        "knob_rows": len(knob_rows),
        "sql": (
            f"SELECT base.*, knob.{knob.get('knob_col') or 'KNOB'} AS color_value "
            f"FROM chart_session_raw_data base LEFT JOIN ML_TABLE_{product_hint} knob "
            "ON base.root_lot_id = knob.root_lot_id AND base.wafer_id = knob.wafer_id"
        ),
    }
    history = list(session.get("history") or [])
    history.append({
        "action": "knob_coloring",
        "value": color_by,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "username": username,
    })
    dashboard_charting.save_chart_session({
        "session_id": sid,
        "username": session.get("username") or username,
        "chart_type": chart_type,
        "config": refined_cfg,
        "base_data_query": base_query,
        "data": colored_rows,
        "created_at": session.get("created_at"),
        "history": history,
    })
    color_values = [{"value": k, "count": v} for k, v in sorted(color_counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    if missing_color_count:
        color_values.append({"value": "missing", "count": missing_color_count, "color": "gray"})
    columns = _flowi_chart_raw_columns(colored_rows)
    chart_result = {
        "ok": True,
        "kind": f"dashboard_{chart_type}",
        "chart_type": chart_type,
        "title": refined_cfg.get("title") or cfg.get("title") or "Flow-i chart",
        "points": colored_rows,
        "total": len(colored_rows),
        "config": refined_cfg,
        "chart_config": refined_cfg,
        "fit": fit,
        "fit_params": fit,
        "color_by": color_by,
        "color_values": color_values,
        "chart_session_id": sid,
        "sources": {
            "base_chart_session_id": sid,
            "reuse_base_chart_raw_data": True,
            "knob_source": f"ML_TABLE_{product_hint}",
            "knob_column": knob.get("knob_col") or "",
            "join_keys": ["lot_wf", "root_lot_id", "wafer_id"],
        },
    }
    answer = (
        f"직전 chart session({sid[:8]}) raw data {len(rows):,}건은 그대로 두고 "
        f"ML_TABLE_{product_hint}에서 {color_by} KNOB 값만 root_lot_id/wafer_id 기준으로 join해서 다시 그렸습니다."
    )
    if fit:
        answer += f" 1차식 fitting line과 R²={fit.get('r2')}도 함께 반영했습니다."
    return {
        "handled": True,
        "intent": "dashboard_chart_knob_coloring",
        "action": "refine_chart_session_knob_coloring",
        "feature": "dashboard",
        "answer": answer,
        "chart_type": chart_type,
        "config": refined_cfg,
        "chart_config": refined_cfg,
        "chart_result": chart_result,
        "fit": fit,
        "chart_session_id": sid,
        "raw_data_download": {
            "url": f"/api/llm/flowi/chart-session/raw-data.csv?chart_session_id={sid}",
            "chart_session_id": sid,
            "filename": _flowi_chart_raw_filename({"config": refined_cfg, "chart_type": chart_type}, sid),
            "row_count": len(colored_rows),
            "column_count": len(columns),
        },
        "table": {
            "kind": "dashboard_chart_knob_coloring_preview",
            "title": "Chart raw data with KNOB color",
            "placement": "below",
            "columns": _table_columns(columns),
            "rows": [{col: row.get(col, "") for col in columns} for row in colored_rows[:max(1, min(120, max_rows * 8))]],
            "total": len(colored_rows),
        },
        "validation": {
            "base_rows_reused": len(rows),
            "colored_rows": len(colored_rows),
            "matched_rows": len([row for row in colored_rows if _text(row.get("color_value"))]),
            "missing_color_rows": missing_color_count,
        },
    }


def _handle_dashboard_chart_context_followup(
    prompt: str,
    product: str,
    max_rows: int,
    agent_context: dict[str, Any] | None,
    *,
    username: str = "flowi",
) -> dict[str, Any]:
    color_requested = _chart_context_color_intent(prompt)
    fit_requested = _chart_fit_intent(prompt)
    if not (color_requested or fit_requested):
        return {"handled": False}
    sid = _active_chart_session_id(agent_context)
    if not sid:
        return {"handled": False}
    try:
        session = dashboard_charting.load_chart_session(sid)
    except FileNotFoundError:
        return {"handled": False}
    cfg = session.get("config") if isinstance(session.get("config"), dict) else {}
    if color_requested:
        return _handle_dashboard_chart_session_knob_coloring(
            prompt,
            product,
            max_rows,
            session,
            sid,
            cfg,
            fit_requested=fit_requested,
            username=username,
        )
    source_type = _upper(cfg.get("source_type") or cfg.get("source") or "")
    metric = str(cfg.get("metric") or cfg.get("item_id") or "").strip()
    if source_type not in {"ET", "INLINE"} or not metric:
        if fit_requested and not color_requested:
            return _handle_dashboard_chart_session_fit(session, sid, cfg)
        return {"handled": False}
    if str(cfg.get("x_col") or "").lower() != "tkout_time" and str(cfg.get("x") or "").lower() != "tkout_time":
        if fit_requested and not color_requested:
            return _handle_dashboard_chart_session_fit(session, sid, cfg)
        return {"handled": False}
    product_hint = str(cfg.get("product") or product or "").strip()
    lots = [str(x).strip() for x in (cfg.get("lots") or []) if str(x).strip()] if isinstance(cfg.get("lots"), list) else []
    step_id = str(cfg.get("step_id") or "").strip()
    color_hint = str(cfg.get("color_by") or "").strip()
    parts = [product_hint, *lots, step_id, source_type, metric, "Trend", prompt]
    if color_hint and "KNOB" not in _upper(prompt) and "노브" not in str(prompt):
        parts.append(f"{color_hint} KNOB")
    if fit_requested or str(cfg.get("fit") or "").lower() == "linear":
        parts.append("1차식 fitting line R2")
    if source_type == "INLINE" and cfg.get("grain"):
        parts.append(f"grain: {cfg.get('grain')}")
    routed_prompt = " ".join(str(p).strip() for p in parts if str(p or "").strip())
    if source_type == "ET":
        out = _handle_et_trend_chart(routed_prompt, product_hint, max_rows)
    elif source_type == "VM":
        out = _handle_vm_trend_chart(routed_prompt, product_hint, max_rows)
    else:
        out = _handle_inline_trend_chart(routed_prompt, product_hint, max_rows)
    if not out.get("handled"):
        return {"handled": False}
    out["context_chart_session_id"] = sid
    out.setdefault("slots", {})
    if isinstance(out["slots"], dict):
        out["slots"]["chart_session_id"] = sid
        out["slots"]["source_type"] = source_type
    if out.get("answer"):
        out["answer"] = f"직전 chart session({sid[:8]}) 조건을 이어받았습니다. " + str(out.get("answer"))
    return out


def _handle_dashboard_generic_chart(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _contains_chart_intent(prompt):
        return {"handled": False}
    metrics = _metric_alias_hits(prompt)
    chart_type = _flowi_chart_type_from_prompt(prompt, metrics)
    if chart_type not in {"correlation_matrix", "classification"}:
        return {"handled": False}
    product_hint = _product_hint(prompt, product)
    config = _flowi_dashboard_default_config(prompt, chart_type, metrics, product=product_hint)
    metric_names = [m.get("metric") for m in metrics if m.get("metric")]
    if chart_type == "correlation_matrix":
        data = [{"x": a, "y": b, "corr": 1.0 if a == b else None} for a in metric_names for b in metric_names]
        answer = f"{', '.join(metric_names) or '선택 항목'} 기준 correlation matrix 설정을 만들었습니다."
    else:
        data = []
        answer = "step별 classification 차트 설정을 만들었습니다."
    return {
        "handled": True,
        "intent": f"dashboard_{chart_type}",
        "action": "build_dashboard_metric_chart",
        "feature": "dashboard",
        "answer": answer,
        "slots": {"product": product_hint, "metrics": metric_names},
        "chart_type": chart_type,
        "config": config,
        "data": data,
        "fit": {},
        "stats_table": data if chart_type == "correlation_matrix" else [],
        "chart_result": {
            "ok": True,
            "kind": f"dashboard_{chart_type}",
            "title": f"{product_hint} {chart_type}".strip(),
            "points": data,
            "total": len(data),
        },
    }


def _flowi_multisource_explicit_prompt(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    explicit_terms = (
        "schema relation",
        "confirmed relation",
        "multi-source",
        "multi source",
        "join",
        "조인",
        "합쳐",
        "연결성",
        "확인된 relation",
        "confirmed",
        "여러 db",
        "여러 source",
        "여러 소스",
        "db/file",
        "db 파일",
        "단일파일",
        "단일 파일",
    )
    return any(term in low or term in text for term in explicit_terms)


def _handle_flowi_multisource_query(
    prompt: str,
    product: str,
    max_rows: int,
    *,
    allowed_keys: set[str] | None = None,
    username: str = "flowi",
) -> dict[str, Any]:
    if not _flowi_multisource_explicit_prompt(prompt):
        return {"handled": False}
    allowed = set(allowed_keys or set())
    if allowed_keys is not None and not ({"filebrowser", "dashboard"} & allowed):
        return {"handled": False}
    try:
        out = flowi_multisource.execute_multisource_request(prompt, product=product, max_rows=max_rows)
    except Exception as exc:
        logger.warning("flowi multisource execution failed: %s", exc)
        return {"handled": False}
    if not out.get("handled"):
        return {"handled": False}
    has_chart = isinstance(out.get("chart_config"), dict) or isinstance(out.get("chart_result"), dict)
    if has_chart and allowed_keys is not None and "dashboard" not in allowed:
        return {"handled": False}
    feature = "dashboard" if has_chart else "filebrowser"
    action = "dashboard.chart.llm.draft" if has_chart else "filebrowser.multisource.preview"
    row_count = int(out.get("row_count") or 0)
    source_ids = [str(x) for x in (out.get("source_ids") or []) if str(x or "").strip()]
    relation_ids = [str(x) for x in (out.get("relation_ids") or []) if str(x or "").strip()]
    join_keys = [str(x) for x in (out.get("join_keys") or []) if str(x or "").strip()]
    sample_rows = out.get("sample_rows") if isinstance(out.get("sample_rows"), list) else []
    selected_columns = [str(x) for x in (out.get("selected_columns") or []) if str(x or "").strip()]
    columns = [{"key": col, "label": col} for col in selected_columns[:48]]
    if out.get("blocked"):
        answer = (
            "확인된 schema relation 근거가 부족해 multi-source 실행을 차단했습니다.\n"
            f"- source: {', '.join(source_ids) or '-'}\n"
            f"- relation: {', '.join(relation_ids) or 'confirmed relation 없음'}"
        )
    else:
        answer = (
            "confirmed schema relation 기준으로 실제 source를 읽어 결과를 만들었습니다.\n"
            f"- source: {len(source_ids)}개\n"
            f"- relation: {len(relation_ids)}개\n"
            f"- join key: {', '.join(join_keys) or '-'}\n"
            f"- 결과: {row_count}행"
        )
    tool: dict[str, Any] = {
        "handled": True,
        "intent": "dashboard_multisource_chart" if has_chart else "filebrowser_multisource_join",
        "action": action,
        "feature": feature,
        "answer": answer,
        "type": "chart" if has_chart else "table",
        "inline_summary": ("Multi-source chart draft" if has_chart else "Multi-source preview") + f" {row_count} rows",
        "filters": out.get("filters") if isinstance(out.get("filters"), dict) else {},
        "source_ids": source_ids,
        "relation_ids": relation_ids,
        "join_keys": join_keys,
        "join_plan": out.get("join_plan") if isinstance(out.get("join_plan"), dict) else {},
        "query_plan": out.get("query_plan") if isinstance(out.get("query_plan"), dict) else {},
        "sql_plan": str(out.get("sql_plan") or ""),
        "selected_columns": selected_columns,
        "sample_rows": sample_rows,
        "row_count": row_count,
        "warnings": out.get("warnings") if isinstance(out.get("warnings"), list) else [],
        "retrieved_knowledge": out.get("retrieved_knowledge") if isinstance(out.get("retrieved_knowledge"), list) else [],
        "sources": [
            {"type": "schema_source", "source_id": sid, "title": sid}
            for sid in source_ids
        ],
        "table": {
            "kind": "flowi_multisource_join",
            "title": "Multi-source confirmed relation result",
            "columns": columns,
            "rows": sample_rows,
            "total": row_count,
        },
        "validation": {
            "rows": row_count,
            "source_count": len(source_ids),
            "warnings": out.get("warnings") if isinstance(out.get("warnings"), list) else [],
            "missing": ["confirmed relation"] if out.get("blocked") else [],
        },
    }
    if out.get("blocked"):
        tool["blocked"] = True
        tool["reject_reason"] = out.get("reason") or "missing_evidence"
    if has_chart:
        chart_config = out.get("chart_config") if isinstance(out.get("chart_config"), dict) else {}
        chart_result = out.get("chart_result") if isinstance(out.get("chart_result"), dict) else {}
        tool.update({
            "chart_type": chart_config.get("chart_type") or chart_result.get("chart_type") or "scatter",
            "chart_config": chart_config,
            "config": chart_config,
            "chart_result": chart_result,
        })
        tool = _augment_dashboard_tool(tool, prompt, product=product, username=username)
    return tool


def _flowi_dashboard_source_runtime_prompt(prompt: str) -> bool:
    # 모든 차트 요청은 먼저 read-only SQL source orchestration을 통과한다.
    # source가 명시되지 않은 경우 runtime이 DB 후보를 해석하거나 사용자에게 선택을 요청한다.
    return _contains_chart_intent(prompt)


def _flowi_dashboard_source_runtime_payload(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    sources = sorted(_source_terms(prompt))
    root = sources[0] if len(sources) == 1 and sources[0] != "ML_TABLE" else ""
    file_match = re.search(r"\b([A-Za-z0-9_.\-/]+\.(?:parquet|csv))\b", str(prompt or ""), flags=re.I)
    file_name = file_match.group(1) if file_match else ""
    preferred_columns: list[str] = []
    if _is_trend_chart_request(prompt):
        preferred_columns = ["root_lot_id", "wafer_id", "tkout_time", "value"]
        if root == "INLINE":
            preferred_columns.append("subitem_id")
        elif root == "ET":
            preferred_columns.extend(["chip_x_pos", "chip_y_pos"])
    query_rules = (
        "Flow-I SQL rules: Trend x-axis=tkout_time when present; select root_lot_id, wafer_id, time, value. "
        "For shot rows select INLINE.subitem_id or ET.chip_x_pos/chip_y_pos. "
        "Default aggregation is INLINE AVG and ET MEDIAN unless the user requested another aggregation. "
        "For INLINE value aggregation exclude summary subitem_id values AVG, AVERAGE, MEAN, MED, MEDIAN, STD, STDEV, STDDEV, MIN, MINIMUM, MAX, MAXIMUM, Q1, Q3, QUARTILE1, QUARTILE3."
    )
    return {
        "natural_language": str(prompt or ""),
        "query_rules": query_rules,
        "root": root if not file_name else "",
        "product": _product_hint(prompt, product),
        "file": file_name,
        "preferred_selected_columns": preferred_columns,
        "max_rows": max(1, min(int(max_rows or 12), 100)),
    }


def _flowi_dashboard_source_runtime_tool(result: dict[str, Any], prompt: str, product: str) -> dict[str, Any]:
    dashboard = result.get("dashboard") if isinstance(result.get("dashboard"), dict) else {}
    chart_result = result.get("chart_result") if isinstance(result.get("chart_result"), dict) else {}
    if not chart_result and isinstance(dashboard.get("chart_result"), dict):
        chart_result = dashboard.get("chart_result") or {}
    config = result.get("config") if isinstance(result.get("config"), dict) else {}
    if not config:
        config = dashboard.get("config") if isinstance(dashboard.get("config"), dict) else {}
    chart_config = chart_result.get("chart_config") if isinstance(chart_result.get("chart_config"), dict) else config
    evidence = {}
    for candidate in (chart_result.get("config"), chart_result.get("chart_config"), config):
        if isinstance(candidate, dict) and isinstance(candidate.get("source_evidence"), dict):
            evidence = candidate.get("source_evidence") or {}
            break
    source_resolution = result.get("source_resolution") if isinstance(result.get("source_resolution"), dict) else {}
    selected_source = source_resolution.get("selected") if isinstance(source_resolution.get("selected"), dict) else {}
    ai_sql = result.get("ai_sql") if isinstance(result.get("ai_sql"), dict) else {}
    join_plan = result.get("join_plan") if isinstance(result.get("join_plan"), dict) else {}
    joined = result.get("joined") if isinstance(result.get("joined"), dict) else {}
    selected_columns = [str(x) for x in (evidence.get("selected_columns") or ai_sql.get("selected_columns") or joined.get("columns") or []) if str(x or "").strip()]
    source_ids = [str(x) for x in (evidence.get("source_ids") or []) if str(x or "").strip()]
    if not source_ids and selected_source.get("source_id"):
        source_ids = [str(selected_source.get("source_id"))]
    if _is_trend_chart_request(prompt) and selected_columns:
        inspected_columns = list(dict.fromkeys([*selected_columns, *[str(x) for x in (ai_sql.get("preview_columns") or []) if str(x or "").strip()]]))
        source_name = _upper(selected_source.get("root") or (source_ids[0] if source_ids else "DB"))
        selected_time_col, time_question = _flowi_trend_time_column(prompt, inspected_columns, source_name)
        if time_question:
            time_question["source_ids"] = source_ids
            time_question["sql_plan"] = ai_sql.get("display_sql") or ""
            return time_question
        if selected_time_col:
            config = {**config, "x_col": selected_time_col, "time_col": selected_time_col, "x_label": selected_time_col}
            chart_config = {**chart_config, "x_col": selected_time_col, "time_col": selected_time_col, "x_label": selected_time_col}
    relation_ids = [str(x) for x in (evidence.get("relation_ids") or join_plan.get("relation_ids") or []) if str(x or "").strip()]
    join_keys = [str(x) for x in (evidence.get("join_keys") or join_plan.get("join_keys") or []) if str(x or "").strip()]
    rows = joined.get("sample_rows") if isinstance(joined.get("sample_rows"), list) else []
    row_count = int(joined.get("row_count") or chart_result.get("total") or len(rows) or 0)
    blocked = bool(result.get("blocked") or result.get("needs_input") or source_resolution.get("needs_input") or dashboard.get("needs_input"))
    question = _text(result.get("question") or source_resolution.get("question") or dashboard.get("question"))
    if blocked:
        answer = question or "Dashboard 차트를 만들기 전에 DB/File source를 먼저 확인해야 합니다."
    else:
        answer = (
            "Dashboard Agent source orchestration으로 차트를 생성했습니다.\n"
            f"- source: {', '.join(source_ids) or '-'}\n"
            f"- selected columns: {', '.join(selected_columns[:12]) or '-'}\n"
            f"- join: {', '.join(relation_ids) if relation_ids else 'single source'}\n"
            f"- rows: {row_count}"
        )
    tool: dict[str, Any] = {
        "handled": True,
        "intent": "dashboard_source_chart_runtime",
        "action": "dashboard.source_chart_runtime",
        "feature": "dashboard",
        "type": "chart" if chart_result else "message",
        "source_orchestration": True,
        "answer": answer,
        "inline_summary": chart_result.get("title") or dashboard.get("title") or "Dashboard source chart",
        "chart_type": result.get("chart_type") or dashboard.get("chart_type") or chart_result.get("chart_type") or "",
        "config": config,
        "chart_config": chart_config,
        "chart_result": chart_result,
        "selected_columns": selected_columns,
        "source_ids": source_ids,
        "relation_ids": relation_ids,
        "join_keys": join_keys,
        "join_plan": join_plan,
        "sql_plan": evidence.get("sql_plan") or ai_sql.get("display_sql") or "",
        "filters": joined.get("filters") if isinstance(joined.get("filters"), dict) else {},
        "warnings": result.get("warnings") if isinstance(result.get("warnings"), list) else [],
        "source_runtime": {
            "run_id": result.get("run_id") or "",
            "status": result.get("status") or "",
            "unit_ai": result.get("unit_ai") or "home_sql_join_dashboard",
            "output_route": result.get("output_route") if isinstance(result.get("output_route"), dict) else {},
            "ai_sql": {
                "display_sql": ai_sql.get("display_sql") or "",
                "selected_columns": selected_columns,
                "ok": bool(ai_sql.get("ok")),
            },
        },
        "table": {
            "kind": "dashboard_source_chart_rows",
            "title": "Dashboard source rows",
            "placement": "below",
            "columns": _table_columns(selected_columns[:48]),
            "rows": [{k: row.get(k, "") for k in selected_columns[:48]} for row in rows[:max(1, min(80, len(rows) or 1))]],
            "total": row_count,
        } if rows and selected_columns else {},
        "validation": {
            "rows": row_count,
            "source_count": len(source_ids),
            "join_count": len(relation_ids),
            "warnings": result.get("warnings") if isinstance(result.get("warnings"), list) else [],
        },
    }
    if blocked:
        tool["blocked"] = True
        if question:
            tool["missing_freetext"] = [{"key": "dashboard_source", "label": question}]
    return tool


def _handle_dashboard_source_chart_runtime(
    prompt: str,
    product: str,
    max_rows: int,
    *,
    allowed_keys: set[str] | None = None,
    username: str = "flowi",
    agent_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if allowed_keys is not None and "dashboard" not in allowed_keys:
        return {"handled": False}
    if not _flowi_dashboard_source_runtime_prompt(prompt):
        return {"handled": False}
    payload = _flowi_dashboard_source_runtime_payload(prompt, product, max_rows)
    try:
        from core.flowi_units.home_sql_join_dashboard_runtime import run_home_sql_join_dashboard_runtime

        result = run_home_sql_join_dashboard_runtime(
            payload,
            username=username,
            agent_context=agent_context if isinstance(agent_context, dict) else None,
        )
    except Exception as exc:
        logger.warning("home dashboard source runtime failed: %s", exc)
        return {"handled": False}
    chart_result = result.get("chart_result") if isinstance(result.get("chart_result"), dict) else {}
    dashboard = result.get("dashboard") if isinstance(result.get("dashboard"), dict) else {}
    if not chart_result and isinstance(dashboard.get("chart_result"), dict):
        chart_result = dashboard.get("chart_result") or {}
    if not chart_result and not (result.get("blocked") or result.get("needs_input")):
        return {"handled": False}
    return _flowi_dashboard_source_runtime_tool(result, prompt, product)


def _chart_default_join_key(sources: set[str]) -> str:
    return "lot_wf"


def _inline_files(product: str) -> list[Path]:
    files: list[Path] = []
    for root in _db_root_candidates("INLINE"):
        files.extend(sorted(root.rglob("*.parquet")))
    return _filter_files_by_product(files, product)


def _metric_terms(metric: str) -> list[str]:
    key = _upper(metric)
    terms = [key]
    terms.extend(FLOWI_DOMAIN_DICTIONARY.get(key, []))
    out = []
    seen = set()
    for term in terms:
        t = _upper(term)
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _first_metric_in_text(text: str) -> str:
    up = _upper(text)
    for metric, aliases in FLOWI_DOMAIN_DICTIONARY.items():
        if any(_upper(alias) and _upper(alias) in up for alias in aliases):
            return metric
    for tok in _query_tokens(text):
        key = _upper(tok)
        if key and key not in FLOWI_CHART_METRIC_STOP:
            return key
    return ""


def _root_key_expr(root_col: str):
    return (
        pl.col(root_col)
        .cast(_STR, strict=False)
        .str.strip_chars()
        .str.to_uppercase()
    )


def _wafer_key_expr(wafer_col: str):
    raw = pl.col(wafer_col).cast(_STR, strict=False).str.strip_chars()
    core = raw.str.replace(r"(?i)^(?:WAFER|WF|W)", "")
    numeric = core.cast(pl.Int64, strict=False)
    return (
        pl.when((numeric >= 1) & (numeric <= FLOWI_MAX_WAFER_ID))
        .then(numeric.cast(_STR, strict=False))
        .otherwise(None)
    )


def _lot_wf_expr(root_col: str, wafer_col: str):
    return (
        _root_key_expr(root_col)
        + pl.lit("_")
        + _wafer_key_expr(wafer_col)
    )


def _explicit_shot_grain(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    return any(term in low or term in text for term in (
        "shot", "die", "map", "좌표", "샷", "다이", "맵", "raw point", "raw-point",
    ))


def _explicit_lot_wf_grain(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    return any(term in low or term in text for term in (
        "lot_wf", "lot wf", "wafer avg", "wf avg", "wafer 평균", "wf 평균",
        "lot_wf avg", "lot-wf", "웨이퍼 평균", "와퍼 평균",
    ))


def _flowi_metric_lf(
    kind: str,
    product: str,
    lots: list[str],
    metric: str,
    value_alias: str,
    *,
    include_shot: bool = False,
    agg_name: str | None = None,
) -> dict[str, Any]:
    kind_u = _upper(kind)
    files = _flowi_source_files(kind_u, product)  # ET/INLINE/VM 등 소스별 파일 (VM→ET 오독 방지)
    if not files:
        return {"ok": False, "error": f"{kind_u} parquet 파일을 찾지 못했습니다.", "files": []}
    lf = _scan_parquet(files)
    cols = _schema_names(lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    lot_wf_col = _ci_col(cols, "lot_wf", "LOT_WF")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    step_col = _ci_col(cols, "step_id", "STEP_ID", "process_id", "PROCESS_ID")
    item_col = _ci_col(cols, "item_id", "ITEM_ID", "rawitem_id", "RAWITEM_ID", "item", "ITEM")
    value_col = _ci_col(cols, "value", "VALUE", "_value", "val", "VAL")
    shot_id_col = _ci_col(cols, "shot_id", "SHOT_ID")
    if kind_u == "INLINE":
        shot_id_col = _ci_col(cols, "subitem_id", "SUBITEM_ID") or shot_id_col
    shot_x_col = _ci_col(cols, "shot_x", "SHOT_X", "die_x", "DIE_X")
    shot_y_col = _ci_col(cols, "shot_y", "SHOT_Y", "die_y", "DIE_Y")
    if not value_col:
        return {"ok": False, "error": f"{kind_u} value 컬럼을 찾지 못했습니다.", "columns": cols[:80]}
    if not lot_wf_col and not (root_col and wafer_col):
        return {"ok": False, "error": f"{kind_u} lot_wf 또는 root_lot_id/wafer_id 컬럼이 필요합니다.", "columns": cols[:80]}
    if not item_col:
        return {"ok": False, "error": f"{kind_u} item_id 컬럼을 찾지 못했습니다.", "columns": cols[:80]}

    aliases = _product_aliases(product)
    filters = []
    if aliases and product_col:
        filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    if lots:
        lot_cols = [c for c in (root_col, lot_col, fab_col, lot_wf_col) if c]
        lot_expr = _or_contains(lot_cols, lots)
        if lot_expr is not None:
            filters.append(lot_expr)

    item_vals = _unique_strings(lf, item_col, limit=600)
    item_matches = _match_values(item_vals, _metric_terms(metric))
    if not item_matches:
        return {
            "ok": False,
            "error": f"{kind_u}에서 metric `{metric}`에 맞는 item 후보를 찾지 못했습니다.",
            "item_candidates": item_vals[:24],
            "metric": metric,
        }
    filters.append(pl.col(item_col).cast(_STR, strict=False).is_in(item_matches))
    if kind_u == "INLINE" and shot_id_col:
        normalized_subitem = (
            pl.col(shot_id_col).cast(_STR, strict=False).str.strip_chars().str.to_lowercase()
            .str.replace_all(r"[\s_.-]+", "")
        )
        filters.append(~normalized_subitem.is_in(list(inline_coordinates.NORMALIZED_SUMMARY_SUBITEM_IDS)))
    for expr in filters:
        lf = lf.filter(expr)

    coordinate_rows = []
    coordinate_mapping = {"configured": False, "rows": []}
    if kind_u == "INLINE" and include_shot and not (step_col and shot_id_col):
        return {
            "ok": False,
            "error": "INLINE shot 좌표 매칭에는 raw step_id/process_id와 subitem_id 열이 필요합니다.",
        }
    if kind_u == "INLINE" and include_shot:
        coordinate_mapping = inline_coordinates.load_coordinate_mapping(
            PATHS.base_root,
            products=_product_aliases(product),
            item_ids=item_matches,
        )
        coordinate_rows = coordinate_mapping["rows"]
        if not coordinate_mapping["configured"]:
            return {
                "ok": False,
                "error": "INLINE shot 좌표 매칭 규칙이 없습니다. inline_matching.csv의 matching_table을 TEG 위치조회 Inline map TABLE 이름으로 지정해 주세요.",
                "matching_tables": [],
            }
        if coordinate_mapping["configured"] and not coordinate_rows:
            missing = ", ".join(coordinate_mapping.get("missing_tables") or [])
            detail = f" ({missing})" if missing else ""
            return {
                "ok": False,
                "error": f"INLINE matching_table에 사용할 수 있는 shot 위치가 없습니다{detail}.",
                "matching_tables": coordinate_mapping.get("configured_tables") or [],
            }

    exprs = []
    group_cols = []
    if root_col:
        exprs.append(_root_key_expr(root_col).alias("root_lot_id"))
        group_cols.append("root_lot_id")
    if wafer_col:
        exprs.append(_wafer_key_expr(wafer_col).alias("wafer_id"))
        group_cols.append("wafer_id")
    if root_col and wafer_col:
        exprs.append(_lot_wf_expr(root_col, wafer_col).alias("lot_wf"))
    elif lot_wf_col:
        exprs.append(pl.col(lot_wf_col).cast(_STR, strict=False).alias("lot_wf"))
    if "lot_wf" not in group_cols:
        group_cols.append("lot_wf")
    if coordinate_rows:
        exprs.extend([
            pl.col(step_col).cast(_STR, strict=False).str.strip_chars().str.to_lowercase().alias("_inline_step"),
            pl.col(item_col).cast(_STR, strict=False).str.strip_chars().str.to_lowercase().alias("_inline_item"),
            pl.col(shot_id_col).cast(_STR, strict=False).str.strip_chars().str.to_lowercase().alias("_inline_subitem"),
        ])
        group_cols.extend(["shot_x", "shot_y"])
    elif include_shot and shot_id_col:
        exprs.append(pl.col(shot_id_col).cast(_STR, strict=False).alias("shot_id"))
        group_cols.append("shot_id")
    elif include_shot and shot_x_col and shot_y_col:
        exprs.append(pl.col(shot_x_col).cast(pl.Float64, strict=False).alias("shot_x"))
        exprs.append(pl.col(shot_y_col).cast(pl.Float64, strict=False).alias("shot_y"))
        group_cols.extend(["shot_x", "shot_y"])
    exprs.append(pl.col(value_col).cast(pl.Float64, strict=False).alias("_metric_value"))
    scoped = lf.select(exprs).drop_nulls(subset=["_metric_value"])
    if coordinate_rows:
        coordinate_lf = pl.DataFrame({
            "_inline_step": [row["step_id"] for row in coordinate_rows],
            "_inline_item": [row["item_id"] for row in coordinate_rows],
            "_inline_subitem": [row["subitem_id"] for row in coordinate_rows],
            "shot_x": [row["shot_x"] for row in coordinate_rows],
            "shot_y": [row["shot_y"] for row in coordinate_rows],
        }).lazy()
        scoped = scoped.join(
            coordinate_lf,
            on=["_inline_step", "_inline_item", "_inline_subitem"],
            how="inner",
        )
    agg_name = agg_name if agg_name in _CHART_AGG_VALUES else _flowi_source_default_agg(kind_u)
    agg = _flowi_agg_polars_expr(agg_name, "_metric_value").alias(value_alias)
    grouped = scoped.group_by(group_cols).agg([
        agg,
        pl.len().alias(f"{value_alias}_n"),
    ])
    return {
        "ok": True,
        "lf": grouped,
        "group_cols": group_cols,
        "metric": metric,
        "item_matches": item_matches,
        "files": [str(p) for p in files[:12]],
        "file_count": len(files),
        "coordinate_mapping": "inline_matching.matching_table" if coordinate_rows else "",
    }


def _flowi_join_cols(left_cols: list[str], right_cols: list[str]) -> list[str]:
    left = set(left_cols)
    right = set(right_cols)
    if {"root_lot_id", "wafer_id", "shot_id"}.issubset(left) and {"root_lot_id", "wafer_id", "shot_id"}.issubset(right):
        return ["root_lot_id", "wafer_id", "shot_id"]
    if {"root_lot_id", "wafer_id", "shot_x", "shot_y"}.issubset(left) and {"root_lot_id", "wafer_id", "shot_x", "shot_y"}.issubset(right):
        return ["root_lot_id", "wafer_id", "shot_x", "shot_y"]
    return ["lot_wf"]


def _explicit_knob_terms(prompt: str) -> list[str]:
    text = str(prompt or "")
    out: list[str] = []
    seen: set[str] = set()
    for pat in (
        r"\b([A-Za-z0-9_.-]{1,40})\s*(?:KNOB|노브)\b",
        r"\b(?:KNOB|노브)\s*([A-Za-z0-9_.-]{1,40})\b",
    ):
        for m in re.finditer(pat, text, flags=re.I):
            key = _upper(m.group(1))
            if not key or key in {"KNOB", "노브", "PLAN"} or key in seen:
                continue
            seen.add(key)
            out.append(key)
    return out[:6]


def _flowi_knob_query_terms(prompt: str, lots: list[str], xy_metrics: list[str]) -> list[str]:
    blocked = set(FLOWI_CHART_METRIC_STOP) | set(_STOP_TOKENS)
    blocked.update(_upper(v) for v in lots)
    metric_terms = set()
    for metric in xy_metrics:
        metric_terms.update(_metric_terms(metric))
    out = []
    seen = set()
    for key in _explicit_knob_terms(prompt):
        if key not in seen:
            seen.add(key)
            out.append(key)
    for tok in _query_tokens(prompt):
        key = _upper(tok)
        if len(key) < 2 or key in blocked or key in metric_terms:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out[:8]


def _pick_knob_by_values(lf: pl.LazyFrame, candidates: list[str]) -> str:
    limited = [c for c in candidates if c][:80]
    if not limited:
        return ""
    try:
        df = (
            lf.select([pl.col(c).cast(_STR, strict=False).alias(c) for c in limited])
            .limit(1000)
            .collect()
        )
    except Exception:
        return limited[0]
    fallback = ""
    for col in limited:
        try:
            vals = [_text(v) for v in df[col].drop_nulls().to_list() if _text(v)]
        except Exception:
            vals = []
        if vals and not fallback:
            fallback = col
        n_unique = len(set(vals))
        if 1 < n_unique <= 24:
            return col
    return fallback or limited[0]


def _select_knob_column(lf: pl.LazyFrame, knob_cols: list[str], prompt: str, lots: list[str], xy_metrics: list[str]) -> tuple[str, list[str]]:
    terms = _flowi_knob_query_terms(prompt, lots, xy_metrics)
    exact: list[str] = []
    contains: list[str] = []
    for col in knob_cols:
        body = _upper(col.replace("KNOB_", "", 1))
        col_u = _upper(col)
        for term in terms:
            if col_u == f"KNOB_{term}" or body == term:
                exact.append(col)
                break
            if term in body or term in col_u:
                contains.append(col)
                break
    candidates = exact or contains
    if candidates:
        return _pick_knob_by_values(lf, candidates), candidates
    return _pick_knob_by_values(lf, knob_cols), knob_cols[:80]


def _knob_ratio_chart_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    up = _upper(text)
    if "KNOB" not in up and "노브" not in text:
        return False
    ratio_terms = (
        "비율", "분포", "점유", "퍼센트", "%", "ratio", "percent", "percentage",
        "proportion", "share", "distribution", "count", "별 비중", "별로",
    )
    visual_terms = (
        "차트", "그래프", "그려", "파이", "원형", "막대", "pie", "bar", "chart", "plot", "graph",
    )
    return any(t in low or t in text for t in ratio_terms) and any(t in low or t in text for t in visual_terms)


def _knob_ratio_chart_type(prompt: str) -> str:
    text = str(prompt or "")
    low = text.lower()
    if any(t in low or t in text for t in ("bar", "막대")):
        return "bar"
    if any(t in low or t in text for t in ("pie", "파이", "원형")):
        return "pie"
    return "pie"


def _knob_ratio_meaningful_terms(prompt: str, knob_cols: list[str]) -> list[str]:
    terms = _flowi_knob_query_terms(prompt, [], [])
    cols_u = [_upper(col) for col in knob_cols]
    out: list[str] = []
    for term in terms:
        term_u = _upper(term)
        if term_u and any(term_u in col_u for col_u in cols_u):
            out.append(term_u)
    return out


def _handle_knob_ratio_chart(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _knob_ratio_chart_intent(prompt):
        return {"handled": False}
    product_hint = _product_hint(prompt, product)
    if not product_hint:
        return _flowi_set_inline_type({
            "handled": True,
            "intent": "dashboard_knob_ratio_needs_product",
            "action": "collect_required_fields",
            "answer": "KNOB 비율 차트는 product 기준 ML_TABLE 전체 wafer에서 계산합니다. 제품명을 알려주세요.",
            "feature": "dashboard",
            "missing": ["product"],
            "pending_prompt": prompt,
            "slots": {"source": "ML_TABLE", "chart_type": _knob_ratio_chart_type(prompt)},
        }, "message", prompt=prompt)

    files = _ml_files(product_hint)
    if not files:
        return {
            "handled": True,
            "intent": "dashboard_knob_ratio_chart",
            "action": "query_knob_ratio_chart",
            "answer": f"{product_hint} ML_TABLE parquet을 찾지 못했습니다.",
            "feature": "dashboard",
            "filters": {"product": product_hint, "source": "ML_TABLE"},
        }
    lf = _scan_parquet(files)
    cols = _schema_names(lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    lot_wf_col = _ci_col(cols, "lot_wf", "LOT_WF")
    knob_cols = [c for c in cols if _upper(c).startswith("KNOB_")]
    if not knob_cols:
        return {
            "handled": True,
            "intent": "dashboard_knob_ratio_chart",
            "action": "query_knob_ratio_chart",
            "answer": "ML_TABLE에서 KNOB_* 컬럼을 찾지 못했습니다.",
            "feature": "dashboard",
            "filters": {"product": product_hint, "source": "ML_TABLE"},
        }

    aliases = _product_aliases(product_hint)
    if aliases and product_col:
        lf = lf.filter(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    meaningful_terms = _knob_ratio_meaningful_terms(prompt, knob_cols)
    if len(knob_cols) > 1 and not meaningful_terms and not _flowi_func_step_token(prompt):
        choices = [
            {
                "id": f"knob_{i}",
                "label": str(i + 1),
                "title": col.replace("KNOB_", "", 1),
                "recommended": i == 0,
                "description": f"{col} 값별 wafer 비율을 계산합니다.",
                "prompt": f"{prompt.strip()} {col}",
            }
            for i, col in enumerate(knob_cols[:4])
        ]
        return {
            "handled": True,
            "intent": "dashboard_knob_ratio_needs_knob",
            "action": "collect_required_fields",
            "answer": "제품 전체 기준으로 볼 KNOB 컬럼을 하나 선택해야 합니다.",
            "feature": "dashboard",
            "missing": ["knob_column"],
            "clarification": {"question": "어느 KNOB 컬럼 비율을 볼까요?", "choices": choices},
            "table": {
                "kind": "knob_column_candidates",
                "title": f"{product_hint} KNOB column candidates",
                "placement": "below",
                "columns": _table_columns(["knob_column"]),
                "rows": [{"knob_column": c} for c in knob_cols[: max(1, min(40, max_rows * 4))]],
                "total": len(knob_cols),
            },
            "filters": {"product": product_hint, "source": "ML_TABLE", "candidate_count": len(knob_cols)},
        }

    knob_col, knob_candidates = _select_knob_column(lf, knob_cols, prompt, [], [])
    if not knob_col:
        return {"handled": True, "intent": "dashboard_knob_ratio_chart", "action": "query_knob_ratio_chart", "answer": "요청과 맞는 KNOB 컬럼을 정하지 못했습니다.", "feature": "dashboard"}

    exprs = [pl.col(knob_col).cast(_STR, strict=False).alias("knob_value")]
    if root_col and wafer_col:
        exprs.append(_lot_wf_expr(root_col, wafer_col).alias("_wafer_key"))
    elif lot_wf_col:
        exprs.append(pl.col(lot_wf_col).cast(_STR, strict=False).alias("_wafer_key"))
    try:
        scoped = (
            lf.select(exprs)
            .filter(
                pl.col("knob_value").is_not_null()
                & (pl.col("knob_value").str.strip_chars() != "")
                & (~pl.col("knob_value").str.to_lowercase().is_in(["none", "null", "nan"]))
            )
        )
        if "_wafer_key" in scoped.collect_schema().names():
            scoped = (
                scoped
                .filter(pl.col("_wafer_key").is_not_null() & (pl.col("_wafer_key").str.strip_chars() != ""))
                .group_by("_wafer_key")
                .agg(pl.col("knob_value").first().alias("knob_value"))
            )
        df = (
            scoped
            .group_by("knob_value")
            .agg(pl.len().alias("count"))
            .sort(["count", "knob_value"], descending=[True, False])
            .collect()
        )
    except Exception as e:
        logger.warning("flowi knob ratio chart failed: %s", e)
        return {"handled": True, "intent": "dashboard_knob_ratio_chart", "action": "query_knob_ratio_chart", "answer": f"KNOB 비율 차트 집계 실패: {e}", "feature": "dashboard"}

    rows = df.to_dicts()
    total = sum(int(r.get("count") or 0) for r in rows)
    for row in rows:
        count = int(row.get("count") or 0)
        row["count"] = count
        row["percent"] = round(count * 100.0 / total, 2) if total else 0.0
        row["knob_column"] = knob_col
    chart_type = _knob_ratio_chart_type(prompt)
    display_name = knob_col.replace("KNOB_", "", 1)
    groups = [
        {
            "label": _text(row.get("knob_value")) or "(empty)",
            "value": row.get("count") or 0,
            "count": row.get("count") or 0,
            "percent": row.get("percent") or 0,
        }
        for row in rows
    ]
    chart_config = {
        "chart_type": chart_type,
        "title": f"{product_hint} {display_name} KNOB 비율",
        "product": product_hint,
        "source_type": "ML_TABLE",
        "x_col": "knob_value",
        "y_expr": "count",
        "value_col": "count",
        "label_col": "knob_value",
        "percent_col": "percent",
    }
    answer = f"{product_hint} ML_TABLE 전체 wafer 기준으로 {display_name} 값별 비율을 계산했습니다. 총 {total}개 wafer 기준입니다."
    if rows:
        top = rows[0]
        answer += f" 가장 큰 값은 {top.get('knob_value') or '(empty)'} {top.get('count')}건({top.get('percent')}%)입니다."
    cols_out = ["knob_column", "knob_value", "count", "percent"]
    return {
        "handled": True,
        "intent": "dashboard_knob_ratio_chart",
        "action": "query_knob_ratio_chart",
        "answer": answer,
        "feature": "dashboard",
        "slots": {"product": product_hint, "knob_column": knob_col, "chart_type": chart_type},
        "source_ids": [f"ML_TABLE_{product_hint}"],
        "chart_result": {
            "ok": True,
            "kind": "knob_ratio_chart",
            "chart_type": chart_type,
            "title": chart_config["title"],
            "groups": groups,
            "total": total,
            "x_label": f"{display_name} value",
            "y_label": "wafer count",
            "knob_column": knob_col,
            "source": "ML_TABLE",
            "sources": {"ml_table_file_count": len(files), "knob_column": knob_col, "scope": "product_all_wafers"},
            "chart_config": chart_config,
        },
        "chart_config": chart_config,
        "table": {
            "kind": "knob_ratio_summary",
            "title": f"{product_hint} {display_name} ratio",
            "placement": "below",
            "columns": _table_columns(cols_out),
            "rows": [{k: r.get(k, "") for k in cols_out} for r in rows[: max(1, min(120, max_rows * 8))]],
            "total": len(rows),
            "source": "ML_TABLE",
        },
        "filters": {
            "product": product_hint,
            "source": "ML_TABLE",
            "scope": "product_all_wafers",
            "knob_column": knob_col,
            "knob_candidates": knob_candidates[:12],
            "wafer_total": total,
        },
        "term_resolution": [
            {"token": product_hint, "meaning": "product 기준", "wiki_refs": [f"schema:ML_TABLE_{product_hint}"], "query_filter": f"product in {sorted(aliases) if aliases else [product_hint]}", "status": "resolved"},
            {"token": display_name, "meaning": "KNOB_* 컬럼", "wiki_refs": ["schema:KNOB_*"], "query_filter": f"column == {knob_col}", "status": "resolved"},
            {"token": chart_type, "meaning": "Home inline Plotly chart", "wiki_refs": ["ui:chart_result"], "query_filter": f"chart_type={chart_type}", "status": "resolved"},
        ],
    }


def _knob_filter_values(prompt: str, values: list[str]) -> list[str]:
    text = str(prompt or "")
    low = text.lower()
    if not any(term in low or term in text for term in ("filter", "exclude", "except", "without", "제외", "빼", "빼고", "빼줘", "제거")):
        return []
    up = _upper(text)
    toks = set(_tokens(text))
    out = []
    for value in values:
        raw = _text(value)
        val = _upper(raw)
        if not val:
            continue
        if len(val) <= 2:
            hit = val in toks
        else:
            hit = val in up
        if hit and raw not in out:
            out.append(raw)
    return out[:12]


def _knob_exclusion_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    return any(term in low or term in text for term in ("filter", "exclude", "except", "without", "제외", "빼", "빼고", "빼줘", "제거"))


def _chart_context_color_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    up = _upper(text)
    return (
        "KNOB" in up
        or "노브" in text
        or any(term in low or term in text for term in ("color", "colour", "coloring", "색", "색칠", "컬러", "컬러링"))
        or _knob_exclusion_intent(text)
    )


def _flowi_knob_lf(product: str, lots: list[str], prompt: str, xy_metrics: list[str]) -> dict[str, Any]:
    files = _ml_files(product)
    if not files:
        return {"ok": False, "error": "ML_TABLE parquet 파일을 찾지 못했습니다.", "files": []}
    lf = _scan_parquet(files)
    cols = _schema_names(lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    lot_wf_col = _ci_col(cols, "lot_wf", "LOT_WF")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    knob_cols = [c for c in cols if _upper(c).startswith("KNOB_")]
    if not knob_cols:
        return {"ok": False, "error": "ML_TABLE에서 KNOB_* 컬럼을 찾지 못했습니다.", "columns": cols[:80]}
    aliases = _product_aliases(product)
    filters = []
    if aliases and product_col:
        filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    if lots:
        lot_cols = [c for c in (root_col, lot_col, fab_col, lot_wf_col) if c]
        lot_expr = _or_contains(lot_cols, lots)
        if lot_expr is not None:
            filters.append(lot_expr)
    for expr in filters:
        lf = lf.filter(expr)

    knob_col, candidates = _select_knob_column(lf, knob_cols, prompt, lots, xy_metrics)
    if not knob_col:
        return {"ok": False, "error": "ML_TABLE에서 color/filter 기준 KNOB 컬럼을 정하지 못했습니다.", "knob_candidates": knob_cols[:24]}

    values = _unique_strings(lf, knob_col, limit=80)
    excluded_values = _knob_filter_values(prompt, values)
    exprs = []
    group_cols = []
    if root_col:
        exprs.append(_root_key_expr(root_col).alias("root_lot_id"))
        group_cols.append("root_lot_id")
    if wafer_col:
        exprs.append(_wafer_key_expr(wafer_col).alias("wafer_id"))
        group_cols.append("wafer_id")
    if root_col and wafer_col:
        exprs.append(_lot_wf_expr(root_col, wafer_col).alias("lot_wf"))
    elif lot_wf_col:
        exprs.append(pl.col(lot_wf_col).cast(_STR, strict=False).alias("lot_wf"))
    if "lot_wf" not in group_cols:
        group_cols.append("lot_wf")
    if not group_cols:
        return {"ok": False, "error": "ML_TABLE에 lot_wf 또는 root_lot_id/wafer_id 컬럼이 필요합니다.", "columns": cols[:80]}
    exprs.append(pl.col(knob_col).cast(_STR, strict=False).alias("color_value"))
    grouped = (
        lf.select(exprs)
        .drop_nulls(subset=["color_value"])
        .group_by(group_cols)
        .agg([
            pl.col("color_value").first().alias("color_value"),
            pl.len().alias("color_n"),
        ])
    )
    return {
        "ok": True,
        "lf": grouped,
        "group_cols": group_cols,
        "knob_col": knob_col,
        "display_name": knob_col.replace("KNOB_", "", 1),
        "candidate_count": len(candidates),
        "values": values[:24],
        "excluded_values": excluded_values,
        "file_count": len(files),
    }


def _flowi_knob_join_cols(scatter_cols: list[str], knob_cols: list[str]) -> list[str]:
    left = set(scatter_cols)
    right = set(knob_cols)
    if {"root_lot_id", "wafer_id"}.issubset(left) and {"root_lot_id", "wafer_id"}.issubset(right):
        return ["root_lot_id", "wafer_id"]
    if "lot_wf" in left and "lot_wf" in right:
        return ["lot_wf"]
    return []


def _duck_col(alias: str, col: str) -> str:
    return f"{alias}.{duckdb_engine.quote_ident(col)}"


def _duck_cast_str(alias: str, col: str) -> str:
    return f"CAST({_duck_col(alias, col)} AS VARCHAR)"


def _duck_root_key_expr(alias: str, col: str) -> str:
    return f"UPPER(TRIM({_duck_cast_str(alias, col)}))"


def _duck_wafer_key_expr(alias: str, col: str) -> str:
    raw = f"TRIM({_duck_cast_str(alias, col)})"
    core = f"REGEXP_REPLACE(UPPER({raw}), '^(WAFER|WF|W)', '')"
    numeric = f"TRY_CAST({core} AS BIGINT)"
    return f"CASE WHEN {numeric} BETWEEN 1 AND {FLOWI_MAX_WAFER_ID} THEN CAST({numeric} AS VARCHAR) ELSE NULL END"


def _duck_in(values: list[str] | set[str]) -> str:
    return ", ".join(duckdb_engine.sql_literal(v) for v in values if _text(v))


def _duck_alias_filter(alias: str, col: str, aliases: set[str]) -> str:
    vals = _duck_in(sorted(aliases))
    return f"UPPER({_duck_cast_str(alias, col)}) IN ({vals})" if col and vals else ""


def _duck_lot_filter(alias: str, cols: list[str], lots: list[str]) -> str:
    terms = [_upper(v) for v in lots if _upper(v)]
    if not terms:
        return ""
    parts: list[str] = []
    for col in cols:
        if not col:
            continue
        casted = f"UPPER({_duck_cast_str(alias, col)})"
        for term in terms:
            safe = term.replace("'", "''").replace("%", "").replace("_", "")
            if safe:
                parts.append(f"{casted} LIKE '%{safe}%'")
    return "(" + " OR ".join(parts) + ")" if parts else ""


def _duck_lot_wf_expr(alias: str, lot_wf_col: str, root_col: str, wafer_col: str) -> str:
    if root_col and wafer_col:
        return f"{_duck_root_key_expr(alias, root_col)} || '_' || {_duck_wafer_key_expr(alias, wafer_col)}"
    return _duck_cast_str(alias, lot_wf_col)


def _duck_metric_subquery(
    *,
    view: str,
    files: list[Path],
    kind: str,
    product: str,
    lots: list[str],
    metric: str,
    value_alias: str,
    include_shot: bool,
    agg_name: str,
) -> dict[str, Any]:
    kind_u = _upper(kind)
    if not files:
        return {"ok": False, "error": f"{kind_u} parquet 파일을 찾지 못했습니다.", "files": []}
    cols, _schema = duckdb_engine.inspect_files(files)
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    lot_wf_col = _ci_col(cols, "lot_wf", "LOT_WF")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    step_col = _ci_col(cols, "step_id", "STEP_ID", "process_id", "PROCESS_ID")
    item_col = _ci_col(cols, "item_id", "ITEM_ID", "rawitem_id", "RAWITEM_ID", "item", "ITEM")
    value_col = _ci_col(cols, "value", "VALUE", "_value", "val", "VAL")
    shot_id_col = _ci_col(cols, "shot_id", "SHOT_ID")
    if kind_u == "INLINE":
        shot_id_col = _ci_col(cols, "subitem_id", "SUBITEM_ID") or shot_id_col
    shot_x_col = _ci_col(cols, "shot_x", "SHOT_X", "die_x", "DIE_X")
    shot_y_col = _ci_col(cols, "shot_y", "SHOT_Y", "die_y", "DIE_Y")
    if not value_col:
        return {"ok": False, "error": f"{kind_u} value 컬럼을 찾지 못했습니다.", "columns": cols[:80]}
    if not lot_wf_col and not (root_col and wafer_col):
        return {"ok": False, "error": f"{kind_u} lot_wf 또는 root_lot_id/wafer_id 컬럼이 필요합니다.", "columns": cols[:80]}
    if not item_col:
        return {"ok": False, "error": f"{kind_u} item_id 컬럼을 찾지 못했습니다.", "columns": cols[:80]}

    item_vals = duckdb_engine.distinct_values(files, item_col, limit=1200)
    item_matches = _match_values(item_vals, _metric_terms(metric))
    if not item_matches:
        return {
            "ok": False,
            "error": f"{kind_u}에서 metric `{metric}`에 맞는 item 후보를 찾지 못했습니다.",
            "item_candidates": item_vals[:24],
            "metric": metric,
        }

    if kind_u == "INLINE" and include_shot:
        if not (step_col and shot_id_col):
            return {
                "ok": False,
                "error": "INLINE shot coordinate matching requires step_id/process_id and subitem_id",
                "fallback": True,
            }
        coordinate_mapping = inline_coordinates.load_coordinate_mapping(
            PATHS.base_root,
            products=_product_aliases(product),
            item_ids=item_matches,
        )
        # The Polars path performs the rulebook/table join (and emits the
        # actionable missing-rule error). Never treat raw subitem_id as an ET
        # shot identifier in the DuckDB fast path.
        return {
            "ok": False,
            "error": "INLINE shot coordinate matching requires mapped join",
            "fallback": True,
        }

    alias = "src"
    filters: list[str] = []
    product_filter = _duck_alias_filter(alias, product_col, _product_aliases(product))
    if product_filter:
        filters.append(product_filter)
    lot_filter = _duck_lot_filter(alias, [c for c in (root_col, lot_col, fab_col, lot_wf_col) if c], lots)
    if lot_filter:
        filters.append(lot_filter)
    filters.append(f"{_duck_cast_str(alias, item_col)} IN ({_duck_in(item_matches)})")
    if kind_u == "INLINE" and shot_id_col:
        summary_values = _duck_in(inline_coordinates.summary_subitem_sql_values())
        normalized_subitem = (
            f"UPPER(REGEXP_REPLACE(TRIM({_duck_cast_str(alias, shot_id_col)}), "
            "'[\\s_.-]+', '', 'g'))"
        )
        filters.append(f"{normalized_subitem} NOT IN ({summary_values})")
    where_sql = " AND ".join(filters) if filters else "TRUE"

    select_exprs: list[str] = []
    group_cols: list[str] = []
    if root_col:
        select_exprs.append(f"{_duck_root_key_expr(alias, root_col)} AS root_lot_id")
        group_cols.append("root_lot_id")
    if wafer_col:
        select_exprs.append(f"{_duck_wafer_key_expr(alias, wafer_col)} AS wafer_id")
        group_cols.append("wafer_id")
    select_exprs.append(f"{_duck_lot_wf_expr(alias, lot_wf_col, root_col, wafer_col)} AS lot_wf")
    if "lot_wf" not in group_cols:
        group_cols.append("lot_wf")
    if include_shot and shot_id_col:
        select_exprs.append(f"{_duck_cast_str(alias, shot_id_col)} AS shot_id")
        group_cols.append("shot_id")
    elif include_shot and shot_x_col and shot_y_col:
        select_exprs.append(f"{_duck_cast_str(alias, shot_x_col)} AS shot_x")
        select_exprs.append(f"{_duck_cast_str(alias, shot_y_col)} AS shot_y")
        group_cols.extend(["shot_x", "shot_y"])
    select_exprs.append(f"TRY_CAST({_duck_col(alias, value_col)} AS DOUBLE) AS _metric_value")
    agg_name = agg_name if agg_name in _CHART_AGG_VALUES else _flowi_source_default_agg(kind)
    agg_sql = _flowi_agg_duck_sql(agg_name, "_metric_value")
    group_sql = ", ".join(group_cols)
    sql = f"""
        SELECT {group_sql},
               {agg_sql} AS {value_alias},
               COUNT(*) AS {value_alias}_n
        FROM (
            SELECT {", ".join(select_exprs)}
            FROM {duckdb_engine.quote_ident(view)} {alias}
            WHERE {where_sql}
        ) scoped
        WHERE _metric_value IS NOT NULL
        GROUP BY {group_sql}
    """
    return {
        "ok": True,
        "sql": sql,
        "group_cols": group_cols,
        "metric": metric,
        "item_matches": item_matches,
        "files": [str(p) for p in files[:12]],
        "file_count": len(files),
    }


def _duck_select_knob_column(files: list[Path], knob_cols: list[str], prompt: str, lots: list[str], xy_metrics: list[str]) -> tuple[str, list[str], list[str]]:
    terms = _flowi_knob_query_terms(prompt, lots, xy_metrics)
    exact: list[str] = []
    contains: list[str] = []
    for col in knob_cols:
        body = _upper(col.replace("KNOB_", "", 1))
        col_u = _upper(col)
        for term in terms:
            if col_u == f"KNOB_{term}" or body == term:
                exact.append(col)
                break
            if term in body or term in col_u:
                contains.append(col)
                break
    candidates = exact or contains or knob_cols[:80]
    knob_col = candidates[0] if candidates else ""
    values = duckdb_engine.distinct_values(files, knob_col, limit=80) if knob_col else []
    return knob_col, candidates, values


def _duck_knob_subquery(product: str, lots: list[str], prompt: str, xy_metrics: list[str]) -> dict[str, Any]:
    files = _ml_files(product)
    if not files:
        return {"ok": False, "error": "ML_TABLE parquet 파일을 찾지 못했습니다.", "files": []}
    cols, _schema = duckdb_engine.inspect_files(files)
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    lot_wf_col = _ci_col(cols, "lot_wf", "LOT_WF")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    knob_cols = [c for c in cols if _upper(c).startswith("KNOB_")]
    if not knob_cols:
        return {"ok": False, "error": "ML_TABLE에서 KNOB_* 컬럼을 찾지 못했습니다.", "columns": cols[:80]}
    if not lot_wf_col and not (root_col and wafer_col):
        return {"ok": False, "error": "ML_TABLE에 lot_wf 또는 root_lot_id/wafer_id 컬럼이 필요합니다.", "columns": cols[:80]}
    knob_col, candidates, values = _duck_select_knob_column(files, knob_cols, prompt, lots, xy_metrics)
    if not knob_col:
        return {"ok": False, "error": "ML_TABLE에서 color/filter 기준 KNOB 컬럼을 정하지 못했습니다.", "knob_candidates": knob_cols[:24]}

    alias = "src"
    filters: list[str] = []
    product_filter = _duck_alias_filter(alias, product_col, _product_aliases(product))
    if product_filter:
        filters.append(product_filter)
    lot_filter = _duck_lot_filter(alias, [c for c in (root_col, lot_col, fab_col, lot_wf_col) if c], lots)
    if lot_filter:
        filters.append(lot_filter)
    where_sql = " AND ".join(filters) if filters else "TRUE"
    exprs: list[str] = []
    group_cols: list[str] = []
    if root_col:
        exprs.append(f"{_duck_root_key_expr(alias, root_col)} AS root_lot_id")
        group_cols.append("root_lot_id")
    if wafer_col:
        exprs.append(f"{_duck_wafer_key_expr(alias, wafer_col)} AS wafer_id")
        group_cols.append("wafer_id")
    exprs.append(f"{_duck_lot_wf_expr(alias, lot_wf_col, root_col, wafer_col)} AS lot_wf")
    if "lot_wf" not in group_cols:
        group_cols.append("lot_wf")
    exprs.append(f"{_duck_cast_str(alias, knob_col)} AS color_value")
    group_sql = ", ".join(group_cols)
    sql = f"""
        SELECT {group_sql},
               MIN(color_value) AS color_value,
               COUNT(*) AS color_n
        FROM (
            SELECT {", ".join(exprs)}
            FROM {duckdb_engine.quote_ident("ml_src")} {alias}
            WHERE {where_sql}
        ) scoped
        WHERE color_value IS NOT NULL
        GROUP BY {group_sql}
    """
    return {
        "ok": True,
        "sql": sql,
        "group_cols": group_cols,
        "knob_col": knob_col,
        "display_name": knob_col.replace("KNOB_", "", 1),
        "candidate_count": len(candidates),
        "values": values[:24],
        "excluded_values": _knob_filter_values(prompt, values),
        "file_count": len(files),
        "files": files,
    }


def _try_metric_scatter_duckdb(prompt: str, product: str, metrics: list[dict[str, Any]], lots: list[str], operations: list[str]) -> dict[str, Any]:
    if not duckdb_engine.is_available():
        return {"ok": False, "error": "duckdb unavailable", "fallback": True}
    pair = _flowi_scatter_source_pair(prompt)
    if pair is None:
        return {"ok": False, "error": "scatter/corr 는 INLINE/ET/VM 중 2개 소스 조합이 필요합니다.", "fallback": True}
    name_x, name_y = pair
    inline_metric, et_metric = _flowi_source_metric_pair(prompt, metrics, name_x, name_y)
    if not inline_metric or not et_metric:
        return {"ok": False, "error": f"{name_x}/{name_y} metric 2개가 필요합니다.", "fallback": True}
    chart_defaults = _flowi_chart_defaults()
    scatter_defaults = chart_defaults.get("scatter") or FLOWI_CHART_DEFAULTS["scatter"]
    inline_agg = _flowi_scatter_slot_agg(name_x, scatter_defaults)
    et_agg = _flowi_scatter_slot_agg(name_y, scatter_defaults)
    # 프롬프트가 집계를 명시하면 y 슬롯(주로 ET/VM)에 적용. x 슬롯은 소스 기본 유지.
    _prompt_agg = _flowi_chart_agg_from_prompt(prompt, default="")
    if _prompt_agg in _CHART_AGG_VALUES:
        et_agg = _prompt_agg
    try:
        point_limit = max(50, min(5000, int(scatter_defaults.get("max_points") or FLOWI_CHART_POINT_LIMIT)))
    except Exception:
        point_limit = FLOWI_CHART_POINT_LIMIT
    include_shot = _explicit_shot_grain(prompt)
    inline_files = _flowi_source_files(name_x, product)
    et_files = _flowi_source_files(name_y, product)
    inline = _duck_metric_subquery(
        view="inline_src", files=inline_files, kind=name_x, product=product, lots=lots,
        metric=inline_metric, value_alias="inline_value", include_shot=include_shot, agg_name=inline_agg,
    )
    if not inline.get("ok"):
        return inline
    et = _duck_metric_subquery(
        view="et_src", files=et_files, kind=name_y, product=product, lots=lots,
        metric=et_metric, value_alias="et_value", include_shot=include_shot, agg_name=et_agg,
    )
    if not et.get("ok"):
        return et
    join_cols = _flowi_join_cols(inline.get("group_cols") or [], et.get("group_cols") or [])
    join_how = "INNER" if "inner join" in str(prompt).lower() or "inner" in str(prompt).lower() else "LEFT"
    needs_knob = (
        "color_by_column" in operations
        or "filter" in operations
        or "KNOB" in _upper(prompt)
        or "노브" in str(prompt or "")
    )
    source_files = {"inline_src": inline_files, "et_src": et_files}
    ctes = [f"inline_metric AS ({inline['sql']})", f"et_metric AS ({et['sql']})"]
    select_cols = [
        *(f"j.{c}" for c in join_cols),
        "j.lot_wf",
        "j.root_lot_id",
        "j.wafer_id",
        "j.inline_value",
        "j.et_value",
        "j.inline_value_n",
        "j.et_value_n",
    ]
    knob = None
    knob_join_cols: list[str] = []
    exclusion_sql = ""
    if needs_knob:
        knob = _duck_knob_subquery(product, lots, prompt, [inline_metric, et_metric])
        if not knob.get("ok"):
            return knob
        source_files["ml_src"] = knob["files"]
        ctes.append(f"knob_metric AS ({knob['sql']})")
        knob_join_cols = _flowi_knob_join_cols([*join_cols, "lot_wf", "root_lot_id", "wafer_id"], knob.get("group_cols") or [])
        if not knob_join_cols:
            return {"ok": False, "error": "INLINE/ET 결과와 ML_TABLE KNOB를 연결할 lot_wf/root_lot_id+wafer_id 키가 없습니다."}
        select_cols.extend(["j.color_value", "j.color_n"])
        excluded = knob.get("excluded_values") or []
        if excluded:
            exclusion_sql = f"AND (j.color_value IS NULL OR j.color_value NOT IN ({_duck_in(excluded)}))"

    using_cols = ", ".join(join_cols)
    joined_sql = (
        f"SELECT * FROM inline_metric i {join_how} JOIN et_metric e USING ({using_cols})"
    )
    if knob:
        knob_using = ", ".join(knob_join_cols)
        joined_sql = f"SELECT j.*, k.color_value, k.color_n FROM ({joined_sql}) j LEFT JOIN knob_metric k USING ({knob_using})"
    ctes.append(f"joined AS ({joined_sql})")
    final_sql = f"""
        WITH {", ".join(ctes)}
        SELECT {", ".join(dict.fromkeys(select_cols))}
        FROM joined j
        WHERE j.inline_value IS NOT NULL AND j.et_value IS NOT NULL
        {exclusion_sql}
        LIMIT {point_limit}
    """
    try:
        df = duckdb_engine.query_views(source_files, final_sql)
    except Exception as e:
        logger.warning("flowi duckdb metric scatter failed: %s", e)
        return {"ok": False, "error": f"DuckDB metric scatter query 실패: {e}", "fallback": True}

    rows = df.to_dicts()
    points = []
    xs: list[float] = []
    ys: list[float] = []
    for row in rows:
        try:
            x = float(row.get("inline_value"))
            y = float(row.get("et_value"))
        except Exception:
            continue
        xs.append(x)
        ys.append(y)
        label = row.get("lot_wf") or "_".join(str(row.get(c) or "") for c in join_cols)
        point = {
            "x": round(x, 6),
            "y": round(y, 6),
            "label": label,
            "root_lot_id": row.get("root_lot_id") or "",
            "wafer_id": row.get("wafer_id") or "",
            "join_key": "|".join(str(row.get(c) or "") for c in join_cols),
            "inline_n": int(row.get("inline_value_n") or 0),
            "et_n": int(row.get("et_value_n") or 0),
        }
        if knob and _text(row.get("color_value")):
            point["color_by"] = knob.get("display_name") or knob.get("knob_col") or "KNOB"
            point["color_value"] = _text(row.get("color_value"))
            point["color_n"] = int(row.get("color_n") or 0)
        points.append(point)
    corr = _pearson(xs, ys)
    fit = _linear_fit(xs, ys) if "linear_fit" in operations else {}
    color_counts: dict[str, int] = {}
    for point in points:
        cv = _text(point.get("color_value"))
        if cv:
            color_counts[cv] = color_counts.get(cv, 0) + 1
    source_meta = {
        "engine": "duckdb",
        "inline_items": inline.get("item_matches") or [],
        "et_items": et.get("item_matches") or [],
        "inline_file_count": inline.get("file_count") or 0,
        "et_file_count": et.get("file_count") or 0,
    }
    if knob:
        source_meta.update({
            "ml_table_file_count": knob.get("file_count") or 0,
            "knob_column": knob.get("knob_col") or "",
            "knob_join_cols": knob_join_cols,
        })
    return {
        "ok": True,
        "kind": "dashboard_scatter",
        "title": f"{name_x} {inline_metric} vs {name_y} {et_metric}",
        "points": points,
        "total": len(points),
        "x_label": f"{name_x} {inline_metric} {_flowi_agg_label(inline_agg)}",
        "y_label": f"{name_y} {et_metric} {_flowi_agg_label(et_agg)}",
        "join_cols": join_cols,
        "join_how": join_how.lower(),
        "corr": round(corr, 6) if corr is not None else None,
        "fit": fit,
        "color_by": (knob.get("display_name") if knob else "") or "",
        "color_values": [{"value": k, "count": v} for k, v in sorted(color_counts.items(), key=lambda kv: (-kv[1], kv[0]))],
        "filters": {"excluded_values": knob.get("excluded_values") or []} if knob else {},
        "sources": {**source_meta, "x_source": name_x, "y_source": name_y},
        "aggregations": {name_x: inline_agg, name_y: et_agg},
        "render_preset": {**scatter_defaults, "grain": "shot" if include_shot else "wafer_agg"},
    }


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = min(len(xs), len(ys))
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sx = sum((x - mx) ** 2 for x in xs)
    sy = sum((y - my) ** 2 for y in ys)
    if sx <= 0 or sy <= 0:
        return None
    cov = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    return cov / math.sqrt(sx * sy)


def _linear_fit(xs: list[float], ys: list[float]) -> dict[str, Any]:
    n = min(len(xs), len(ys))
    if n < 2:
        return {}
    mx = sum(xs) / n
    my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom <= 0:
        return {}
    slope = sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / denom
    intercept = my - slope * mx
    preds = [slope * x + intercept for x in xs]
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((ys[i] - preds[i]) ** 2 for i in range(n))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0
    return {"slope": round(slope, 8), "intercept": round(intercept, 8), "r2": round(r2, 6)}


def _try_metric_scatter(prompt: str, product: str, metrics: list[dict[str, Any]], lots: list[str], operations: list[str]) -> dict[str, Any]:
    pair = _flowi_scatter_source_pair(prompt)
    if pair is None:
        return {"ok": False, "error": "scatter/corr 는 INLINE/ET/VM 중 2개 소스 조합이 필요합니다."}
    name_x, name_y = pair
    inline_metric, et_metric = _flowi_source_metric_pair(prompt, metrics, name_x, name_y)
    if not inline_metric or not et_metric:
        return {"ok": False, "error": f"{name_x}/{name_y} metric 2개가 필요합니다."}
    duckdb_result = _try_metric_scatter_duckdb(prompt, product, metrics, lots, operations)
    if duckdb_result.get("ok"):
        return duckdb_result
    if duckdb_result.get("fallback") and duckdb_result.get("error") != "duckdb unavailable":
        logger.warning("flowi duckdb scatter fallback: %s", duckdb_result.get("error"))
    chart_defaults = _flowi_chart_defaults()
    scatter_defaults = chart_defaults.get("scatter") or FLOWI_CHART_DEFAULTS["scatter"]
    inline_agg = _flowi_scatter_slot_agg(name_x, scatter_defaults)
    et_agg = _flowi_scatter_slot_agg(name_y, scatter_defaults)
    _prompt_agg = _flowi_chart_agg_from_prompt(prompt, default="")
    if _prompt_agg in _CHART_AGG_VALUES:
        et_agg = _prompt_agg
    try:
        point_limit = max(50, min(5000, int(scatter_defaults.get("max_points") or FLOWI_CHART_POINT_LIMIT)))
    except Exception:
        point_limit = FLOWI_CHART_POINT_LIMIT
    include_shot = _explicit_shot_grain(prompt)
    inline = _flowi_metric_lf(name_x, product, lots, inline_metric, "inline_value", include_shot=include_shot, agg_name=inline_agg)
    if not inline.get("ok"):
        return inline
    et = _flowi_metric_lf(name_y, product, lots, et_metric, "et_value", include_shot=include_shot, agg_name=et_agg)
    if not et.get("ok"):
        return et
    join_cols = _flowi_join_cols(inline.get("group_cols") or [], et.get("group_cols") or [])
    join_how = "inner" if "inner join" in str(prompt).lower() or "inner" in str(prompt).lower() else "left"
    needs_knob = (
        "color_by_column" in operations
        or "filter" in operations
        or "KNOB" in _upper(prompt)
        or "노브" in str(prompt or "")
    )
    knob = None
    knob_join_cols: list[str] = []
    if needs_knob:
        knob = _flowi_knob_lf(product, lots, prompt, [inline_metric, et_metric])
        if not knob.get("ok"):
            return knob
    try:
        joined = inline["lf"].join(et["lf"], on=join_cols, how=join_how)
        if knob:
            knob_join_cols = _flowi_knob_join_cols(joined.collect_schema().names(), knob.get("group_cols") or [])
            if not knob_join_cols:
                return {"ok": False, "error": "INLINE/ET 결과와 ML_TABLE KNOB를 연결할 lot_wf/root_lot_id+wafer_id 키가 없습니다."}
            joined = joined.join(knob["lf"], on=knob_join_cols, how="left")
            excluded = knob.get("excluded_values") or []
            if excluded:
                joined = joined.filter(
                    pl.col("color_value").is_null()
                    | (~pl.col("color_value").cast(_STR, strict=False).is_in(excluded))
                )
        keep = list(dict.fromkeys([
            *join_cols,
            "lot_wf",
            "root_lot_id",
            "wafer_id",
            "inline_value",
            "et_value",
            "inline_value_n",
            "et_value_n",
            "color_value",
            "color_n",
        ]))
        keep = [c for c in keep if c in joined.collect_schema().names()]
        df = (
            joined.select(keep)
            .drop_nulls(subset=["inline_value", "et_value"])
            .limit(point_limit)
            .collect()
        )
    except Exception as e:
        logger.warning("flowi metric scatter failed: %s", e)
        return {"ok": False, "error": f"metric scatter query 실패: {e}"}
    rows = df.to_dicts()
    points = []
    xs: list[float] = []
    ys: list[float] = []
    for row in rows:
        try:
            x = float(row.get("inline_value"))
            y = float(row.get("et_value"))
        except Exception:
            continue
        xs.append(x)
        ys.append(y)
        label = row.get("lot_wf") or "_".join(str(row.get(c) or "") for c in join_cols)
        point = {
            "x": round(x, 6),
            "y": round(y, 6),
            "label": label,
            "root_lot_id": row.get("root_lot_id") or "",
            "wafer_id": row.get("wafer_id") or "",
            "join_key": "|".join(str(row.get(c) or "") for c in join_cols),
            "inline_n": int(row.get("inline_value_n") or 0),
            "et_n": int(row.get("et_value_n") or 0),
        }
        if knob and _text(row.get("color_value")):
            point["color_by"] = knob.get("display_name") or knob.get("knob_col") or "KNOB"
            point["color_value"] = _text(row.get("color_value"))
            point["color_n"] = int(row.get("color_n") or 0)
        points.append(point)
    corr = _pearson(xs, ys)
    fit = _linear_fit(xs, ys) if "linear_fit" in operations else {}
    color_counts: dict[str, int] = {}
    for point in points:
        cv = _text(point.get("color_value"))
        if cv:
            color_counts[cv] = color_counts.get(cv, 0) + 1
    source_meta = {
        "inline_items": inline.get("item_matches") or [],
        "et_items": et.get("item_matches") or [],
        "inline_file_count": inline.get("file_count") or 0,
        "et_file_count": et.get("file_count") or 0,
    }
    if knob:
        source_meta.update({
            "ml_table_file_count": knob.get("file_count") or 0,
            "knob_column": knob.get("knob_col") or "",
            "knob_join_cols": knob_join_cols,
        })
    return {
        "ok": True,
        "kind": "dashboard_scatter",
        "title": f"{name_x} {inline_metric} vs {name_y} {et_metric}",
        "points": points,
        "total": len(points),
        "x_label": f"{name_x} {inline_metric} {_flowi_agg_label(inline_agg)}",
        "y_label": f"{name_y} {et_metric} {_flowi_agg_label(et_agg)}",
        "join_cols": join_cols,
        "join_how": join_how,
        "corr": round(corr, 6) if corr is not None else None,
        "fit": fit,
        "color_by": (knob.get("display_name") if knob else "") or "",
        "color_values": [{"value": k, "count": v} for k, v in sorted(color_counts.items(), key=lambda kv: (-kv[1], kv[0]))],
        "filters": {"excluded_values": knob.get("excluded_values") or []} if knob else {},
        "sources": {**source_meta, "x_source": name_x, "y_source": name_y},
        "aggregations": {name_x: inline_agg, name_y: et_agg},
        "render_preset": {**scatter_defaults, "grain": "shot" if include_shot else "wafer_agg"},
    }


def _flowi_composite_enabled() -> bool:
    return str(os.getenv("FLOWI_COMPOSITE", "1")).strip().lower() not in {"0", "false", "off", "no"}


def _home_composite_lot_analysis_intent(prompt: str) -> bool:
    if not _flowi_composite_enabled():
        return False
    text = str(prompt or "")
    low = text.lower()
    up = _upper(text)
    has_knob = "KNOB" in up or "노브" in text
    has_step = "STEP" in up or "step_id" in low or "빠른" in text or "가장 앞" in text
    has_corr = any(t in low or t in text for t in ("corr", "correlation", "상관"))
    has_trend = any(t in low or t in text for t in ("trend", "추세", "시계열", "라인"))
    has_split_scope = "LOT_WF" in up or "lot_wf" in low or "split table" in low or "splittable" in low or "스플릿" in text
    return has_knob and has_step and has_split_scope and has_corr and has_trend


def _flowi_composite_metric_pair(prompt: str) -> tuple[str, str]:
    text = str(prompt or "")
    patterns = [
        r"([A-Za-z][A-Za-z0-9_.-]*)\s*(?:[·,/&+]|and|와|과|및)\s*([A-Za-z][A-Za-z0-9_.-]*)\s*(?:corr|correlation|상관)",
        r"(?:corr|correlation|상관)\s*(?:분석)?\s*([A-Za-z][A-Za-z0-9_.-]*)\s*(?:[·,/&+]|and|와|과|및)\s*([A-Za-z][A-Za-z0-9_.-]*)",
    ]
    blocked = {"KNOB", "STEP", "STEP_ID", "LOT", "LOT_WF", "SPLIT", "TABLE", "TREND", "CORR", "COLOR"}
    for pat in patterns:
        m = re.search(pat, text, flags=re.I)
        if not m:
            continue
        a = _upper(m.group(1)).strip("._-")
        b = _upper(m.group(2)).strip("._-")
        if a and b and a not in blocked and b not in blocked:
            return a, b
    hits = [str(row.get("metric") or "").strip() for row in _metric_alias_hits(text) if row.get("metric")]
    hits = [h for h in hits if _upper(h) not in blocked]
    if len(hits) >= 2:
        return hits[0], hits[1]
    return "", ""


def _flowi_composite_trend_metric(prompt: str, corr_pair: tuple[str, str]) -> str:
    text = str(prompt or "")
    for pat in (
        r"([A-Za-z][A-Za-z0-9_.-]*)\s*(?:trend|추세|시계열|라인)",
        r"(?:trend|추세|시계열|라인)\s*(?:는|은|로|:)?\s*([A-Za-z][A-Za-z0-9_.-]*)",
    ):
        m = re.search(pat, text, flags=re.I)
        if not m:
            continue
        metric = _upper(m.group(1)).strip("._-")
        if metric and metric not in {"KNOB", "COLOR", "STEP", "LOT_WF", "TREND"}:
            return metric
    for metric in _metric_alias_hits(text):
        value = str(metric.get("metric") or "").strip()
        if value and value not in corr_pair:
            return value
    return ""


def _flowi_lot_keys_from_table(table: dict[str, Any]) -> dict[str, set[str]]:
    keys = {"lot_wf": set(), "root_lot_id": set(), "wafer_id": set()}
    rows = table.get("rows") if isinstance(table.get("rows"), list) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in keys:
            value = _text(row.get(key))
            if value:
                keys[key].add(_upper(value))
        root = _text(row.get("root_lot_id"))
        wafer = _text(row.get("wafer_id") or row.get("current_wafer_id"))
        lot_wf = _flowi_lot_wf_id(root, wafer)
        if lot_wf:
            keys["lot_wf"].add(_upper(lot_wf))
    return keys


def _flowi_filter_chart_result_by_lots(chart_result: dict[str, Any], lot_keys: dict[str, set[str]]) -> dict[str, Any]:
    if not any(lot_keys.values()):
        return chart_result
    out = deepcopy(chart_result)

    def keep(point: dict[str, Any]) -> bool:
        lot_wf = _upper(point.get("lot_wf") or point.get("label") or "")
        root = _upper(point.get("root_lot_id") or "")
        wafer = _upper(point.get("wafer_id") or "")
        derived = _upper(_flowi_lot_wf_id(root, wafer))
        return (
            (lot_wf and lot_wf in lot_keys["lot_wf"])
            or (derived and derived in lot_keys["lot_wf"])
            or (root and root in lot_keys["root_lot_id"])
        )

    if isinstance(out.get("points"), list):
        filtered = [p for p in out.get("points") or [] if isinstance(p, dict) and keep(p)]
        if filtered:
            out["points"] = filtered
            out["total"] = len(filtered)
    if isinstance(out.get("series"), list):
        series = []
        for item in out.get("series") or []:
            if not isinstance(item, dict):
                continue
            points = [p for p in item.get("points") or [] if isinstance(p, dict) and keep(p)]
            if points:
                series.append({**item, "points": points})
        if series:
            out["series"] = series
            out["total"] = sum(len(s.get("points") or []) for s in series)
    return out


def _flowi_knob_values_for_points(prompt: str, product: str, lots: list[str], metric: str) -> tuple[dict[str, str], str]:
    try:
        knob = _flowi_knob_lf(product, lots, prompt, [metric])
    except Exception as exc:
        logger.debug("flowi composite trend knob lookup failed: %s", exc)
        return {}, ""
    if not knob.get("ok"):
        return {}, ""
    try:
        df = knob["lf"].limit(5000).collect()
    except Exception as exc:
        logger.debug("flowi composite trend knob collect failed: %s", exc)
        return {}, str(knob.get("display_name") or "")
    values: dict[str, str] = {}
    for row in df.to_dicts():
        value = _text(row.get("color_value"))
        if not value:
            continue
        lot_wf = _upper(row.get("lot_wf") or "")
        root = _upper(row.get("root_lot_id") or "")
        wafer = _upper(row.get("wafer_id") or "")
        derived = _upper(_flowi_lot_wf_id(root, wafer))
        for key in (lot_wf, derived, root):
            if key and key not in values:
                values[key] = value
    return values, str(knob.get("display_name") or knob.get("knob_col") or "")


def _flowi_trend_payload_from_tool(
    trend_tool: dict[str, Any],
    *,
    prompt: str,
    product: str,
    lots: list[str],
    metric: str,
    lot_keys: dict[str, set[str]],
) -> dict[str, Any]:
    chart_result = trend_tool.get("chart_result") if isinstance(trend_tool.get("chart_result"), dict) else {}
    chart_result = _flowi_filter_chart_result_by_lots(chart_result, lot_keys)
    if isinstance(chart_result.get("series"), list) and chart_result.get("series"):
        return chart_result
    points = [p for p in (chart_result.get("points") or []) if isinstance(p, dict)]
    knob_values, color_by = _flowi_knob_values_for_points(prompt, product, lots, metric)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for point in points:
        lot_wf = _upper(point.get("lot_wf") or point.get("label") or "")
        root = _upper(point.get("root_lot_id") or "")
        wafer = _upper(point.get("wafer_id") or "")
        color_value = knob_values.get(lot_wf) or knob_values.get(_upper(_flowi_lot_wf_id(root, wafer))) or knob_values.get(root) or ""
        if color_value:
            point = {**point, "color_value": color_value}
        grouped.setdefault(color_value or "trend", []).append(point)
    series = [
        {"name": name, "points": sorted(rows, key=lambda r: (str(r.get("tkout_time") or r.get("x_label") or ""), float(r.get("x") or 0)))}
        for name, rows in sorted(grouped.items(), key=lambda kv: kv[0])
    ]
    return {
        **chart_result,
        "kind": "dashboard_trend",
        "title": chart_result.get("title") or f"{metric} Trend",
        "series": series,
        "points": points,
        "total": len(points),
        "metric": chart_result.get("metric") or metric,
        "color_by": color_by,
    }


def _flowi_composite_scatter_tool(prompt: str, product: str, max_rows: int, corr_pair: tuple[str, str], lot_keys: dict[str, set[str]]) -> dict[str, Any]:
    x_metric, y_metric = corr_pair
    if not x_metric or not y_metric:
        return {"handled": False, "error": "corr metrics not resolved"}
    lots = sorted({_text(x) for x in _lot_tokens(prompt) if _text(x)})
    product_hint = _product_hint(prompt, product)
    scatter_prompt = " ".join(x for x in [product_hint, "INLINE ET", x_metric, y_metric, "corr scatter"] if x)
    metrics = [{"metric": x_metric}, {"metric": y_metric}]
    actual = _try_metric_scatter(scatter_prompt, product_hint, metrics, lots, ["correlation", "scatter"])
    if actual.get("ok"):
        payload = _flowi_filter_chart_result_by_lots(actual, lot_keys)
        return {
            "handled": True,
            "intent": "home_composite_corr_scatter",
            "action": "compute_corr_scatter_block",
            "feature": "dashboard",
            "answer": f"{x_metric} vs {y_metric} corr scatter를 계산했습니다.",
            "chart_result": payload,
            "chart_config": payload.get("config_overrides") or payload.get("chart_config") or {},
            "slots": {"product": product_hint, "metrics": [x_metric, y_metric], "lots": lots},
        }
    draft = _handle_chart_request(scatter_prompt, product_hint, max_rows)
    if draft.get("handled"):
        return draft
    return {"handled": False, "error": actual.get("error") or "scatter block failed"}


def _handle_home_composite_lot_analysis(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _home_composite_lot_analysis_intent(prompt):
        return {"handled": False}
    product_hint = _product_hint(prompt, product)
    corr_pair = _flowi_composite_metric_pair(prompt)
    trend_metric = _flowi_composite_trend_metric(prompt, corr_pair)
    children: list[dict[str, Any]] = []
    warnings: list[str] = []
    blocks: list[dict[str, Any]] = []

    fastest_tool, child = _invoke_subagent("fastest_knob", _handle_fastest_knob_query, prompt, product_hint, max_rows)
    children.append(child)
    table = deepcopy(fastest_tool.get("table")) if isinstance(fastest_tool.get("table"), dict) else {}
    if table:
        rows = [dict(row) for row in (table.get("rows") or []) if isinstance(row, dict)]
        if rows:
            rows[0]["__highlight"] = True
            table["rows"] = rows
        blocks.append({
            "kind": "lot_table",
            "title": table.get("title") or "Fastest LOT_WF",
            "payload": table,
            "highlight": {
                "row_keys": [str(rows[0].get("lot_wf") or rows[0].get("root_lot_id") or "")] if rows else [],
                "reason": "fastest step",
            },
        })
    elif fastest_tool.get("error") or fastest_tool.get("answer"):
        warnings.append(str(fastest_tool.get("error") or fastest_tool.get("answer")))
    lot_keys = _flowi_lot_keys_from_table(table)

    scatter_tool, child = _invoke_subagent(
        "corr_scatter",
        lambda p, prod, rows: _flowi_composite_scatter_tool(p, prod, rows, corr_pair, lot_keys),
        prompt,
        product_hint,
        max_rows,
    )
    children.append(child)
    scatter_payload = scatter_tool.get("chart_result") if isinstance(scatter_tool.get("chart_result"), dict) else {}
    if scatter_payload:
        blocks.append({
            "kind": "chart_scatter",
            "title": scatter_payload.get("title") or f"{corr_pair[0]} vs {corr_pair[1]}",
            "payload": scatter_payload,
        })
    elif scatter_tool.get("error") or scatter_tool.get("answer"):
        warnings.append(str(scatter_tool.get("error") or scatter_tool.get("answer")))

    trend_prompt = " ".join(x for x in [product_hint, "INLINE", trend_metric, "trend"] if x)
    trend_tool, child = _invoke_subagent("trend_by_knob", _handle_inline_trend_chart, trend_prompt, product_hint, max_rows)
    children.append(child)
    if trend_tool.get("handled") and trend_metric:
        trend_payload = _flowi_trend_payload_from_tool(
            trend_tool,
            prompt=prompt,
            product=product_hint,
            lots=_lot_tokens(prompt),
            metric=trend_metric,
            lot_keys=lot_keys,
        )
        blocks.append({
            "kind": "chart_trend",
            "title": trend_payload.get("title") or f"{trend_metric} Trend",
            "payload": trend_payload,
        })
    elif trend_tool.get("error") or trend_tool.get("answer"):
        warnings.append(str(trend_tool.get("error") or trend_tool.get("answer")))

    if not blocks:
        return {
            "handled": True,
            "intent": "home_composite_lot_analysis",
            "action": "collect_required_fields",
            "answer": "복합 분석 요청은 인식했지만 표시할 표/차트 블록을 만들지 못했습니다. product, corr metric 2개, trend metric을 더 구체적으로 알려주세요.",
            "feature": "dashboard",
            "missing": [x for x, ok in (("product", bool(product_hint)), ("corr_metrics", all(corr_pair)), ("trend_metric", bool(trend_metric))) if not ok],
            "warnings": warnings,
            "_subagent_children": children,
        }

    primary_table = next((b.get("payload") for b in blocks if b.get("kind") == "lot_table" and isinstance(b.get("payload"), dict)), None)
    primary_chart = next((b.get("payload") for b in blocks if str(b.get("kind") or "").startswith("chart_") and isinstance(b.get("payload"), dict)), None)
    answer_bits = [f"{len(blocks)}개 블록으로 복합 분석을 구성했습니다."]
    if primary_table:
        answer_bits.append(f"표 {len(primary_table.get('rows') or [])}행")
    if primary_chart:
        answer_bits.append(f"차트 point {primary_chart.get('total', len(primary_chart.get('points') or []))}")
    if warnings:
        answer_bits.append("일부 블록은 부분 실패로 trace에 남겼습니다.")
    return {
        "handled": True,
        "intent": "home_composite_lot_analysis",
        "action": "compose_lot_table_corr_trend_blocks",
        "answer": " ".join(answer_bits),
        "feature": "dashboard",
        "blocks": blocks,
        "table": primary_table,
        "chart_result": primary_chart,
        "chart_config": _flowi_block_chart_config(blocks),
        "slots": {
            "product": product_hint,
            "corr_metrics": [m for m in corr_pair if m],
            "trend_metric": trend_metric,
            "lots": _lot_tokens(prompt),
        },
        "filters": {
            "lot_wf": sorted(lot_keys.get("lot_wf") or []),
            "root_lot_id": sorted(lot_keys.get("root_lot_id") or []),
            "source": "home_composite",
        },
        "warnings": warnings,
        "_subagent_children": children,
    }


def _group_chart_group_keys(prompt: str) -> list[str]:
    text = str(prompt or "")
    low = text.lower()
    has_eqp = any(t in low or t in text for t in ("eqp", "equipment", "장비", "설비"))
    has_chamber = any(t in low or t in text for t in ("chamber", "챔버"))
    if has_eqp and has_chamber:
        return ["eqp", "chamber"]
    if has_chamber:
        return ["chamber"]
    if has_eqp:
        return ["eqp"]
    return []


def _inline_metric_match_for_prompt(lf: pl.LazyFrame, item_col: str, prompt: str) -> tuple[str, list[str], list[str]]:
    item_vals = _unique_strings(lf, item_col, limit=1200)
    blocked = {"EQP", "EQUIPMENT", "CHAMBER", "장비", "설비", "챔버"}
    terms = []
    seen = set()
    for hit in _metric_alias_hits(prompt):
        key = _upper(hit.get("metric"))
        if key and key not in blocked and key not in seen:
            seen.add(key)
            terms.append(key)
        for alias in hit.get("aliases") or []:
            alias_key = _upper(alias)
            if alias_key and alias_key not in blocked and alias_key not in seen:
                seen.add(alias_key)
                terms.append(alias_key)
    for tok in _query_tokens(prompt):
        key = _upper(tok)
        if key and key not in blocked and key not in seen:
            seen.add(key)
            terms.append(key)
    exact = []
    for term in terms:
        exact.extend([v for v in item_vals if _upper(v) == term])
    if exact:
        matches = sorted(set(exact), key=lambda x: (-len(str(x)), str(x)))
        return matches[0], matches, item_vals[:24]
    matches = _match_values(item_vals, terms)
    if matches:
        matches = sorted(set(matches), key=lambda x: (-len(str(x)), str(x)))
        return matches[0], matches, item_vals[:24]
    term_sets = {term: set(t for t in re.split(r"[_\W]+", _upper(term)) if t) for term in terms}
    reordered = []
    for value in item_vals:
        val_set = set(t for t in re.split(r"[_\W]+", _upper(value)) if t)
        if val_set and any(val_set == parts for parts in term_sets.values()):
            reordered.append(value)
    if reordered:
        reordered = sorted(set(reordered), key=lambda x: (-len(str(x)), str(x)))
        return reordered[0], reordered, item_vals[:24]
    return "", [], item_vals[:24]


def _is_trend_chart_request(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    if any(re.search(rf"(?<![a-z0-9_]){term}(?![a-z0-9_])", low) for term in ("scatter", "corr", "correlation")):
        return False
    if any(t in text for t in ("추세", "시계열", "라인")):
        return True
    return any(re.search(rf"(?<![a-z0-9_]){term}(?![a-z0-9_])", low) for term in ("trend", "line"))


def _flowi_trend_time_column(prompt: str, columns: list[str], source: str) -> tuple[str, dict[str, Any] | None]:
    """Choose tkout_time, or require an explicit choice among other time columns."""
    tkout = _ci_col(columns, "tkout_time", "TKOUT_TIME")
    if tkout:
        return tkout, None
    candidates = [
        col for col in columns
        if "time" in str(col).casefold() and not str(col).startswith("__")
    ]
    text = str(prompt or "")
    for col in candidates:
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(str(col))}(?![A-Za-z0-9_])", text, flags=re.I):
            return col, None
    if not candidates:
        return "", {
            "handled": True,
            "intent": f"dashboard_{_upper(source).lower()}_trend_needs_time_column",
            "action": "collect_required_fields",
            "feature": "dashboard",
            "missing": ["time_column"],
            "answer": f"{_upper(source)} DB에 tkout_time 열과 이름에 time이 들어간 대체 열이 없어 Trend x축을 정할 수 없습니다.",
        }
    choices = [
        {
            "id": f"time_{idx}",
            "label": str(idx),
            "title": col,
            "value": f"time_col: {col}",
            "recommended": idx == 1,
            "description": f"{col}을 Trend x축으로 사용합니다.",
            "prompt": f"{text} time_col: {col}",
        }
        for idx, col in enumerate(candidates[:3], start=1)
    ]
    return "", {
        "handled": True,
        "intent": f"dashboard_{_upper(source).lower()}_trend_needs_time_column",
        "action": "collect_required_fields",
        "feature": "dashboard",
        "missing": ["time_column"],
        "pending_prompt": text,
        "answer": f"{_upper(source)} DB에 tkout_time이 없습니다. Trend x축으로 사용할 time 계열 열을 선택해 주세요: {', '.join(candidates)}.",
        "clarification": {
            "question": "Trend의 시간축으로 어떤 열을 사용할까요?",
            "choices": choices,
        },
        "table": {
            "kind": "trend_time_column_candidates",
            "title": f"{_upper(source)} time columns",
            "placement": "below",
            "columns": _table_columns(["time_column"]),
            "rows": [{"time_column": col} for col in candidates],
            "total": len(candidates),
        },
    }


def _flowi_chart_lot_tokens(prompt: str) -> list[str]:
    lots = list(_lot_tokens(prompt))
    seen = {_upper(v) for v in lots}
    if not _product_hint(prompt):
        return lots
    text = str(prompt or "")
    step = _flowi_func_step_token(text)
    step_pos = _upper(text).find(_upper(step)) if step else -1
    for m in re.finditer(r"(?<![A-Za-z0-9_.-])([A-Z]{2,5}\d{4,})(?![A-Za-z0-9_.-])", text, flags=re.I):
        tok = _upper(m.group(1))
        if tok in seen or _is_product_token(tok) or _is_step_id_token(tok):
            continue
        if step_pos >= 0 and m.start() > step_pos:
            continue
        seen.add(tok)
        lots.append(tok)
    return lots


def _et_trend_should_handle(prompt: str) -> bool:
    text = str(prompt or "")
    if not (_contains_chart_intent(text) and _is_trend_chart_request(text)):
        return False
    sources = _source_terms(text)
    if "INLINE" in sources and "ET" not in sources:
        return False
    if "ET" in sources:
        return True
    if _flowi_func_step_token(text):
        return False
    step_ids = [s for s in _step_tokens(text) if _is_step_id_token(s)]
    if step_ids:
        return True
    metric_terms: set[str] = set()
    for hit in _metric_alias_hits(text):
        metric_terms.add(_upper(hit.get("metric")))
        for alias in hit.get("aliases") or []:
            metric_terms.add(_upper(alias))
    return bool(metric_terms & FLOWI_ET_TREND_DEFAULT_METRICS)


def _flowi_explicit_chart_draw_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    return any(term in low or term in text for term in (
        "chart", "graph", "plot", "scatter", "line",
        "차트", "그래프", "그려", "그려줘", "산점도", "라인",
    ))


# ── 차트 집계(agg) 확장 — median 기본 + P90/P10/max/avg + shot(전체 미집계) ──────
# ET 는 median 기본, "shot 으로" 요청 시 집계 없이 전체 측정 point. INLINE 은 avg 기본.
_ET_AGG_CHOICES = ("median", "avg", "p90", "p10", "max", "shot")
# scatter/trend 의 wafer 집계 검증 집합 (shot 은 grain 분기라 여기 미포함).
_CHART_AGG_VALUES = {"avg", "median", "p90", "p10", "max"}


def _flowi_agg_duck_sql(agg_name: str, col: str = "_metric_value") -> str:
    """agg 이름 → DuckDB 집계 SQL 조각 (scatter duckdb 경로용)."""
    return {
        "avg": f"AVG({col})",
        "median": f"MEDIAN({col})",
        "max": f"MAX({col})",
        "p90": f"QUANTILE_CONT({col}, 0.9)",
        "p10": f"QUANTILE_CONT({col}, 0.1)",
    }.get(agg_name, f"MEDIAN({col})")


def _flowi_chart_agg_from_prompt(prompt: str, *, default: str = "median") -> str:
    """차트 y 집계 방법을 프롬프트에서 뽑는다. 반환: median/avg/p90/p10/max/shot."""
    text = str(prompt or "")
    low = text.lower()
    if re.search(r"(?<![a-z0-9_])shots?(?![a-z0-9_])", low) or "샷" in text or any(
        t in text for t in ("전체 측정", "모든 point", "모든 포인트", "다 찍어", "전부 찍어")
    ):
        return "shot"
    if re.search(r"(?<![a-z0-9_])p\s*90(?![0-9])", low) or "90분위" in text or "상위10" in text or "상위 10" in text:
        return "p90"
    if re.search(r"(?<![a-z0-9_])p\s*10(?![0-9])", low) or "10분위" in text or "하위10" in text or "하위 10" in text:
        return "p10"
    if re.search(r"(?<![a-z])max(?![a-z])", low) or "최대" in text or "최댓값" in text:
        return "max"
    if any(t in low or t in text for t in ("avg", "average", "mean", "평균")):
        return "avg"
    if any(t in low or t in text for t in ("median", "중앙값")):
        return "median"
    return default  # 명시 없으면 호출자 기본값 그대로 (scatter 는 "" 로 미명시 감지)


def _flowi_agg_polars_expr(agg_name: str, col: str = "metric_value") -> "pl.Expr":
    """agg 이름 → polars 집계 expr (group_by.agg 컨텍스트용). shot 은 별도 분기라 여기 없음."""
    c = pl.col(col).cast(pl.Float64, strict=False)
    if agg_name == "avg":
        return c.mean()
    if agg_name == "max":
        return c.max()
    if agg_name == "p90":
        return c.quantile(0.9, interpolation="linear")
    if agg_name == "p10":
        return c.quantile(0.1, interpolation="linear")
    return c.median()


def _flowi_agg_label(agg_name: str) -> str:
    return {"median": "median", "avg": "avg", "max": "max",
            "p90": "P90", "p10": "P10", "shot": "shot(all)"}.get(agg_name, "median")


# ── 멀티소스 scatter/corr — ET/INLINE/VM 임의 2소스 쌍 ────────────────────────
# VM 은 wafer 단위(shot 없음). 여러 값이면 avg 기본. INLINE 도 avg, ET 는 median.
_SCATTER_SOURCES = ("INLINE", "ET", "VM")  # x/y 슬롯 배정 우선순위(낮은 인덱스가 x)


def _flowi_source_default_agg(source: str) -> str:
    return "median" if _upper(source) == "ET" else "avg"  # VM/INLINE=avg, ET=median


def _flowi_scatter_slot_agg(source: str, scatter_defaults: dict[str, Any]) -> str:
    """슬롯 소스의 기본 집계 — 관리자 scatter 설정(INLINE/ET 키)이 있으면 그것, 없으면 소스 기본."""
    if source == "INLINE" and scatter_defaults.get("inline_agg") in _CHART_AGG_VALUES:
        return scatter_defaults["inline_agg"]
    if source == "ET" and scatter_defaults.get("et_agg") in _CHART_AGG_VALUES:
        return scatter_defaults["et_agg"]
    return _flowi_source_default_agg(source)


def _flowi_scatter_source_pair(prompt: str) -> tuple[str, str] | None:
    """프롬프트의 소스 중 metric-scatter 가능한 2개를 우선순위 순서(x,y)로. 2개 미만이면 None."""
    present = _source_terms(prompt) & set(_SCATTER_SOURCES)
    ordered = [s for s in _SCATTER_SOURCES if s in present]
    if len(ordered) < 2:
        return None
    return ordered[0], ordered[1]


def _flowi_source_token_in_text(text: str, source: str) -> int:
    """소스 이름 토큰의 프롬프트 내 위치(없으면 -1). metric pair slicing 용."""
    up = _upper(text)
    if source == "INLINE":
        pos = up.find("INLINE")
        return pos if pos >= 0 else (text.find("인라인"))
    m = re.search(r"\b" + re.escape(source) + r"\b", up)
    return m.start() if m else -1


def _flowi_source_metric_pair(prompt: str, metrics: list[dict[str, Any]], name_x: str, name_y: str) -> tuple[str, str]:
    """소스-무관 metric 쌍 추출 — 각 소스 토큰 뒤 구간에서 첫 metric. fallback=ordered metrics."""
    text = str(prompt or "")
    px = _flowi_source_token_in_text(text, name_x)
    py = _flowi_source_token_in_text(text, name_y)
    metric_x = metric_y = ""
    if px >= 0:
        end = py if py > px else len(text)
        metric_x = _first_metric_in_text(text[px:end])
    if py >= 0:
        end = px if px > py else len(text)
        metric_y = _first_metric_in_text(text[py:end])
    ordered = [str(m.get("metric") or "").strip() for m in metrics if str(m.get("metric") or "").strip()]
    if not metric_x and ordered:
        metric_x = ordered[0]
    if not metric_y:
        for item in ordered:
            if item != metric_x:
                metric_y = item
                break
    return metric_x, metric_y


def _handle_et_trend_chart(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _et_trend_should_handle(str(prompt or "")):
        return {"handled": False}
    return _flowi_source_trend_chart(prompt, product, max_rows, source="ET", default_agg="median")


def _handle_vm_trend_chart(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    text = str(prompt or "")
    if not (_contains_chart_intent(text) and _is_trend_chart_request(text) and "VM" in _source_terms(text)):
        return {"handled": False}
    return _flowi_source_trend_chart(prompt, product, max_rows, source="VM", default_agg="avg")


def _flowi_source_trend_chart(prompt: str, product: str, max_rows: int, *, source: str = "ET", default_agg: str = "median") -> dict[str, Any]:
    """소스(ET/VM 등)별 tkout_time x축 trend scatter — agg 확장·knob 색상 공용 코어."""
    text = str(prompt or "")
    src = _upper(source)
    src_low = src.lower()
    product_hint = _product_hint(text, product)
    files = _flowi_source_files(src, product_hint)
    if not files:
        label = f"{product_hint} " if product_hint else ""
        return {"handled": True, "intent": f"dashboard_{src_low}_trend", "answer": f"{label}{src} parquet을 찾지 못했습니다.", "feature": "dashboard"}
    et_lf = _scan_parquet(files)
    cols = _schema_names(et_lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    lot_wf_col = _ci_col(cols, "lot_wf", "LOT_WF")
    step_col = _ci_col(cols, "step_id", "STEP_ID", "operation", "OPERATION")
    item_col = _ci_col(cols, "item_id", "ITEM_ID", "rawitem_id", "RAWITEM_ID", "item", "ITEM")
    value_col = _ci_col(cols, "value", "VALUE", "_value", "val", "VAL")
    chip_x_col = _ci_col(cols, "chip_x_pos", "CHIP_X_POS")
    chip_y_col = _ci_col(cols, "chip_y_pos", "CHIP_Y_POS")
    time_col, time_question = _flowi_trend_time_column(text, cols, src)
    if time_question:
        return time_question
    if not (item_col and value_col):
        return {
            "handled": True,
            "intent": f"dashboard_{src_low}_trend",
            "answer": f"{src} Trend에는 item_id/value/tkout_time 컬럼이 필요합니다.",
            "feature": "dashboard",
            "table": {
                "kind": f"dashboard_{src_low}_trend_error",
                "title": f"Missing {src} columns",
                "placement": "below",
                "columns": _table_columns(["message", "columns"]),
                "rows": [{"message": "missing item_id/value/tkout_time", "columns": ", ".join(cols[:80])}],
                "total": 1,
            },
        }
    if not lot_wf_col and not (root_col and wafer_col):
        return {
            "handled": True,
            "intent": f"dashboard_{src_low}_trend",
            "answer": f"{src} Trend scatter에는 lot_wf 또는 root_lot_id/wafer_id 컬럼이 필요합니다.",
            "feature": "dashboard",
            "table": {
                "kind": f"dashboard_{src_low}_trend_error",
                "title": f"Missing {src} grain columns",
                "placement": "below",
                "columns": _table_columns(["message", "columns"]),
                "rows": [{"message": "missing lot_wf or root_lot_id/wafer_id", "columns": ", ".join(cols[:80])}],
                "total": 1,
            },
        }
    metric, item_matches, item_candidates = _inline_metric_match_for_prompt(et_lf, item_col, text)
    if not metric:
        return {
            "handled": True,
            "intent": f"dashboard_{src_low}_trend_needs_context",
            "action": "collect_required_fields",
            "answer": f"Trend로 그릴 {src} item을 찾지 못했습니다. item명을 더 정확히 알려주세요.",
            "missing": ["item_id"],
            "feature": "dashboard",
            "table": {"kind": f"{src_low}_item_candidates", "title": f"{src} item candidates", "placement": "below", "columns": _table_columns(["item_id"]), "rows": [{"item_id": x} for x in item_candidates], "total": len(item_candidates)},
        }

    step_ids = [s for s in _step_tokens(text) if _is_step_id_token(s)]
    lots = _flowi_chart_lot_tokens(text)
    aliases = _product_aliases(product_hint)
    filters = []
    if aliases and product_col:
        filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    if step_ids:
        if not step_col:
            return {
                "handled": True,
                "intent": f"dashboard_{src_low}_trend_needs_context",
                "action": "collect_required_fields",
                "answer": f"{src} Trend에서 step_id 조건을 적용하려면 step_id 컬럼이 필요합니다.",
                "missing": ["step_id_column"],
                "feature": "dashboard",
                "table": {"kind": f"dashboard_{src_low}_trend_error", "title": f"Missing {src} step column", "placement": "below", "columns": _table_columns(["message", "columns"]), "rows": [{"message": "missing step_id", "columns": ", ".join(cols[:80])}], "total": 1},
            }
        filters.append(pl.col(step_col).cast(_STR, strict=False).str.to_uppercase().is_in([_upper(s) for s in step_ids]))
    if lots:
        lot_expr = _or_contains([c for c in (root_col, lot_col, fab_col, lot_wf_col) if c], lots)
        if lot_expr is not None:
            filters.append(lot_expr)
    filters.append(pl.col(item_col).cast(_STR, strict=False).is_in(item_matches or [metric]))
    scoped_lf = et_lf
    for expr in filters:
        scoped_lf = scoped_lf.filter(expr)

    if not product_hint and product_col:
        try:
            product_rows = (
                scoped_lf.group_by(product_col)
                .agg([
                    pl.len().alias("rows"),
                    pl.col(root_col).n_unique().alias("root_lot_count") if root_col else pl.lit(0).alias("root_lot_count"),
                    pl.col(wafer_col).n_unique().alias("wafer_count") if wafer_col else pl.lit(0).alias("wafer_count"),
                ])
                .sort("rows", descending=True)
                .limit(12)
                .collect()
                .to_dicts()
            )
        except Exception:
            product_rows = []
        products = [_text(r.get(product_col)) for r in product_rows if _text(r.get(product_col))]
        unique_products = list(dict.fromkeys(products))
        if len(unique_products) > 1:
            return {
                "handled": True,
                "intent": f"dashboard_{src_low}_trend_needs_context",
                "action": "collect_required_fields",
                "answer": f"{metric} {src} Trend 후보 product가 {len(unique_products)}개입니다. product를 하나 지정해 주세요.",
                "missing": ["product"],
                "feature": "dashboard",
                "pending_prompt": text,
                "table": {
                    "kind": f"{src_low}_product_candidates",
                    "title": f"{src} Trend product candidates",
                    "placement": "below",
                    "columns": _table_columns(["product", "rows", "root_lot_count", "wafer_count"]),
                    "rows": [{"product": r.get(product_col) or "", "rows": r.get("rows") or 0, "root_lot_count": r.get("root_lot_count") or 0, "wafer_count": r.get("wafer_count") or 0} for r in product_rows],
                    "total": len(product_rows),
                },
            }
        if len(unique_products) == 1:
            product_hint = unique_products[0]

    agg_name = _flowi_chart_agg_from_prompt(text, default=default_agg)
    shot_mode = agg_name == "shot"
    if shot_mode and src == "ET" and not (chip_x_col and chip_y_col):
        return {
            "handled": True,
            "intent": "dashboard_et_trend_needs_shot_columns",
            "action": "collect_required_fields",
            "feature": "dashboard",
            "missing": ["chip_x_pos", "chip_y_pos"],
            "answer": "ET shot Trend에는 chip_x_pos와 chip_y_pos 열이 필요합니다.",
        }
    exprs = [
        pl.col(time_col).cast(_STR, strict=False).alias("tkout_time"),
        pl.col(value_col).cast(pl.Float64, strict=False).alias("metric_value"),
        pl.col(item_col).cast(_STR, strict=False).alias("item_id"),
    ]
    if product_col:
        exprs.append(pl.col(product_col).cast(_STR, strict=False).alias("product"))
    else:
        exprs.append(pl.lit(product_hint).alias("product"))
    if root_col:
        exprs.append(_root_key_expr(root_col).alias("root_lot_id"))
    else:
        exprs.append(pl.lit("").alias("root_lot_id"))
    if lot_col:
        exprs.append(pl.col(lot_col).cast(_STR, strict=False).alias("lot_id"))
    else:
        exprs.append(pl.lit("").alias("lot_id"))
    if wafer_col:
        exprs.append(_wafer_key_expr(wafer_col).alias("wafer_id"))
    else:
        exprs.append(pl.lit("").alias("wafer_id"))
    if root_col and wafer_col:
        exprs.append(_lot_wf_expr(root_col, wafer_col).alias("lot_wf"))
    elif lot_wf_col:
        exprs.append(pl.col(lot_wf_col).cast(_STR, strict=False).alias("lot_wf"))
    else:
        exprs.append(pl.lit("").alias("lot_wf"))
    if step_col:
        exprs.append(pl.col(step_col).cast(_STR, strict=False).alias("step_id"))
    else:
        exprs.append(pl.lit("").alias("step_id"))
    if shot_mode and src == "ET":
        exprs.extend([
            pl.col(chip_x_col).cast(pl.Float64, strict=False).alias("chip_x_pos"),
            pl.col(chip_y_col).cast(pl.Float64, strict=False).alias("chip_y_pos"),
        ])

    try:
        scatter_cfg = (_flowi_chart_defaults().get("scatter") or FLOWI_CHART_DEFAULTS["scatter"])
        point_limit = max(20, min(5000, int(scatter_cfg.get("max_points") or FLOWI_CHART_POINT_LIMIT)))
    except Exception:
        scatter_cfg = FLOWI_CHART_DEFAULTS["scatter"]
        point_limit = FLOWI_CHART_POINT_LIMIT
    group_cols = ["product", "tkout_time", "lot_wf", "root_lot_id", "lot_id", "wafer_id", "step_id"]
    try:
        selected = (
            scoped_lf.select(exprs)
            .drop_nulls(subset=["tkout_time", "metric_value", "lot_wf"])
        )
        if shot_mode:
            # 집계 없이 전체 측정 shot 을 point 로 — median/mean/n 은 point 자체값으로 채워 표 호환.
            grouped = selected.with_columns([
                pl.col("metric_value").alias("y_value"),
                pl.col("metric_value").alias("median"),
                pl.col("metric_value").alias("mean"),
                pl.lit(1).alias("n"),
            ])
        else:
            grouped = selected.group_by(group_cols).agg([
                pl.col("metric_value").median().alias("median"),
                pl.col("metric_value").mean().alias("mean"),
                pl.len().alias("n"),
                _flowi_agg_polars_expr(agg_name, "metric_value").alias("y_value"),
            ])
        knob = None
        knob_join_cols: list[str] = []
        needs_knob = _chart_context_color_intent(text)
        if needs_knob and product_hint:
            knob = _flowi_knob_lf(product_hint, lots, text, [metric])
            if knob.get("ok"):
                if _knob_exclusion_intent(text) and not (knob.get("excluded_values") or []) and (knob.get("values") or []):
                    choices = []
                    for idx, value in enumerate((knob.get("values") or [])[:6], start=1):
                        choices.append({
                            "id": f"exclude_{idx}",
                            "label": str(idx),
                            "title": str(value),
                            "value": str(value),
                            "description": f"{knob.get('display_name') or 'KNOB'}={value} point를 제외합니다.",
                            "prompt": f"{text} {value} 제외",
                        })
                    return {
                        "handled": True,
                        "intent": f"dashboard_{src_low}_trend_color_needs_value",
                        "action": "collect_required_fields",
                        "answer": "제외할 KNOB 값을 하나 지정해 주세요.",
                        "missing": ["knob_value"],
                        "feature": "dashboard",
                        "pending_prompt": text,
                        "clarification": {"question": "어떤 KNOB 값을 제외할까요?", "choices": choices},
                    }
                knob_join_cols = _flowi_knob_join_cols(grouped.collect_schema().names(), knob.get("group_cols") or [])
                if knob_join_cols:
                    grouped = grouped.join(knob["lf"], on=knob_join_cols, how="left")
                    excluded = knob.get("excluded_values") or []
                    if excluded:
                        grouped = grouped.filter(
                            pl.col("color_value").is_null()
                            | (~pl.col("color_value").cast(_STR, strict=False).is_in(excluded))
                        )
        df = grouped.sort("tkout_time").limit(point_limit).collect()
    except Exception as e:
        logger.warning("flowi %s trend failed: %s", src, e)
        return {"handled": True, "intent": f"dashboard_{src_low}_trend", "answer": f"{src} trend query 실패: {e}", "feature": "dashboard"}

    rows = df.to_dicts()
    for row in rows:
        row[time_col] = row.get("tkout_time") or ""
    points = []
    color_counts: dict[str, int] = {}
    missing_color_count = 0
    knob_color_ready = bool(knob and knob.get("ok") and knob_join_cols)
    for idx, row in enumerate(rows):
        y = _round4(row.get("y_value"))
        if y is None:
            continue
        color_value = _text(row.get("color_value"))
        if knob_color_ready:
            if color_value:
                color_counts[color_value] = color_counts.get(color_value, 0) + 1
            else:
                missing_color_count += 1
        lot_wf = row.get("lot_wf") or _flowi_lot_wf_id(row.get("root_lot_id"), row.get("wafer_id"))
        points.append({
            "x": idx,
            "x_label": _text(row.get("tkout_time")),
            "tkout_time": _text(row.get("tkout_time")),
            "time_col": time_col,
            "time_value": _text(row.get("tkout_time")),
            "y": y,
            "median": _round4(row.get("median")),
            "mean": _round4(row.get("mean")),
            "n": int(row.get("n") or 0),
            "product": row.get("product") or product_hint,
            "lot_wf": lot_wf,
            "root_lot_id": row.get("root_lot_id") or "",
            "lot_id": row.get("lot_id") or "",
            "wafer_id": row.get("wafer_id") or "",
            "step_id": row.get("step_id") or "",
            "chip_x_pos": row.get("chip_x_pos") if shot_mode and src == "ET" else "",
            "chip_y_pos": row.get("chip_y_pos") if shot_mode and src == "ET" else "",
            "color_by": (knob.get("display_name") if knob_color_ready else "") or "",
            "color_value": color_value,
            "label": lot_wf or row.get("lot_id") or "",
        })
    fit_requested = _chart_fit_intent(text)
    fit = _chart_fit_from_rows(points) if fit_requested else {}
    color_values = [{"value": k, "count": v} for k, v in sorted(color_counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    if knob_color_ready and missing_color_count:
        color_values.append({"value": "missing", "count": missing_color_count, "color": "gray"})
    agg_label = _flowi_agg_label(agg_name)
    y_label = f"{src} {metric} {agg_label}"
    step_label = f" step_id={', '.join(step_ids)}" if step_ids else ""
    if shot_mode:
        basis = f"{src}는 측정 shot 전체(집계 없음) 기준입니다."
    else:
        basis = f"{src}는 lot_wf별 {agg_label}(value) 기준입니다."
    other_aggs = [a for a in ("median", "avg", "p90", "p10", "max") if a != agg_name][:3]
    answer = f"시간축: {time_col}. " + (
        f"{product_hint or src} {metric} {src} Trend를 {time_col} x축 scatter로 그렸습니다. "
        f"{basis} 표시 point={len(points)}, item match={', '.join(item_matches or [metric])}{step_label}. "
        f"다른 집계로 보려면: {', '.join(_flowi_agg_label(a) for a in other_aggs)}, shot(전체)."
    )
    if knob_color_ready:
        answer += f" {knob.get('display_name') or 'KNOB'} 기준으로 색상을 입혔고 KNOB가 없는 point는 회색으로 표시합니다."
    if fit:
        answer += f" 1차식 fitting line과 R²={fit.get('r2')}를 포함했습니다."
    if not points:
        answer = f"{product_hint or src} {metric} 조건으로 Trend chart row를 찾지 못했습니다."
    cols_out = [time_col, "product", "step_id", "lot_wf", "root_lot_id", "lot_id", "wafer_id"] + (["chip_x_pos", "chip_y_pos"] if shot_mode and src == "ET" else []) + ["median", "mean", "n", "color_value"]
    config_overrides = {
        "chart_type": "scatter",
        "source_type": src,
        "product": product_hint,
        "x_col": time_col,
        "time_col": time_col,
        "y_col": "value",
        "y_expr": "value (shot, no agg)" if shot_mode else f"{agg_name}(value)",
        "item_id": (item_matches or [metric])[0],
        "metric": metric,
        "step_id": step_ids[0] if step_ids else "",
        "lots": lots,
        "grain": "shot" if shot_mode else "lot_wf",
        "aggregation": agg_name,
        "group_by": "shot" if shot_mode else "lot_wf",
        "x_label": time_col,
        "y_label": y_label,
        "color_by": (knob.get("display_name") if knob_color_ready else "") or "",
        "color_missing": "gray" if knob_color_ready else "",
        "fit": "linear" if fit_requested else "none",
        "render_preset": {**scatter_cfg, "engine": "plotly", "grain": "lot_wf", "x_axis": "time"},
    }
    chart_result = {
        "ok": True,
        "kind": "dashboard_scatter",
        "chart_type": "scatter",
        "title": f"{product_hint} {src} {metric} Trend".strip(),
        "points": points,
        "total": len(points),
        "x_label": time_col,
        "y_label": y_label,
        "metric": metric,
        "source_type": src,
        "x_col": time_col,
        "time_col": time_col,
        "y_col": "value",
        "item_id": (item_matches or [metric])[0],
        "step_id": step_ids[0] if step_ids else "",
        "grain": "shot" if shot_mode else "lot_wf",
        "aggregation": agg_name,
        "aggregations": {src: agg_name},
        "lot_wf_rule": "root_lot_id + '_' + wafer_id" if root_col and wafer_col else "lot_wf",
        "join_cols": knob_join_cols if knob_color_ready else [],
        "color_by": (knob.get("display_name") if knob_color_ready else "") or "",
        "color_missing": "gray" if knob_color_ready else "",
        "missing_color_count": missing_color_count,
        "color_values": color_values,
        "filters": {"step_ids": step_ids, "lots": lots, "excluded_values": knob.get("excluded_values") or [] if knob else []},
        "fit": fit,
        "config_overrides": config_overrides,
        "render_preset": config_overrides["render_preset"],
        "sources": {
            "db": f"1.RAWDATA_DB_{src}",
            "files": [str(p) for p in files[:24]],
            "sql": _flowi_dashboard_sql_from_config(config_overrides),
            f"{src_low}_file_count": len(files),
            f"{src_low}_items": item_matches or [metric],
            "lot_wf": config_overrides["group_by"],
            "knob_column": knob.get("knob_col") if knob_color_ready else "",
        },
    }
    return {
        "handled": True,
        "intent": f"dashboard_{src_low}_trend_chart",
        "action": f"query_{src_low}_trend_scatter_chart",
        "answer": answer,
        "feature": "dashboard",
        "chart_type": "scatter",
        "config": config_overrides,
        "chart_config": config_overrides,
        "slots": {"product": product_hint, "metric": metric, "lots": lots, "source_type": src, "grain": "shot" if shot_mode else "lot_wf", "aggregation": agg_name, "step_id": step_ids[0] if step_ids else "", "time_col": time_col},
        "chart_result": chart_result,
        "table": {"kind": f"dashboard_{src_low}_trend", "title": f"{metric} {src} Trend", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in rows[:max(1, min(120, max_rows * 8))]], "total": len(rows)},
        "term_resolution": [
            {"token": metric, "meaning": f"{src} item", "wiki_refs": [f"schema:{src}.item_id"], "query_filter": f"item_id={metric}", "status": "resolved"},
            {"token": "Trend", "meaning": f"{time_col} x축 scatter", "wiki_refs": [f"schema:{src}.{time_col}"], "query_filter": f"x={time_col}; y={'value(shot)' if shot_mode else agg_name + '(value)'}", "status": "resolved"},
        ],
    }


def _is_box_chart_request(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    return _contains_chart_intent(text) and (
        any(t in text for t in ("박스", "분포", "분산"))
        or any(re.search(rf"(?<![a-z0-9_]){term}(?![a-z0-9_])", low) for term in ("box", "boxplot", "distribution"))
    )


def _percentile_sorted(vals: list[float], q: float) -> float | None:
    clean = sorted(float(v) for v in vals if v is not None and math.isfinite(float(v)))
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    pos = (len(clean) - 1) * max(0.0, min(1.0, float(q)))
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return clean[lo]
    frac = pos - lo
    return clean[lo] * (1 - frac) + clean[hi] * frac


def _is_wafer_map_chart_request(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    if any(t in low or t in text for t in ("tablemap", "table map", "테이블맵", "테이블 맵", "relation", "관계")):
        return False
    if any(t in low for t in ("heatmap", "heat map", "히트맵", "treemap", "트리맵", "roadmap", "매핑", "mapping")):
        return False
    has_map = any(t in low or t in text for t in ("wf map", "wafer map", "웨이퍼맵", "맵", "map"))
    if not has_map or any(t in low or t in text for t in ("비슷", "similar", "유사", "닮")):
        return False
    return _contains_chart_intent(text) or any(t in low or t in text for t in ("보여", "표시", "view"))


def _parse_spec_bounds(prompt: str) -> dict[str, Any] | None:
    """spec out map 요청의 spec 경계 파싱.

    반환: {"low": float|None, "high": float|None, "label": str} — spec 언급이 없으면 None.
    spec out 요청인데 숫자를 못 찾으면 low/high 모두 None 인 dict 를 반환한다(되물음용).
    """
    text = str(prompt or "")
    low_txt = text.lower()
    wants_spec_out = any(t in low_txt for t in ("spec out", "specout", "스펙 아웃", "스펙아웃"))
    if not wants_spec_out and "spec" not in low_txt and "스펙" not in text:
        return None
    num = r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
    low_v: float | None = None
    high_v: float | None = None
    m = re.search(rf"(?:usl|spec\s*high|상한)\s*[:=]?\s*({num})", low_txt)
    if m:
        high_v = float(m.group(1))
    m = re.search(rf"(?:lsl|spec\s*low|하한)\s*[:=]?\s*({num})", low_txt)
    if m:
        low_v = float(m.group(1))
    if low_v is None and high_v is None:
        m = re.search(rf"(?:spec|스펙)\s*[:=]?\s*({num})\s*~\s*({num})", low_txt)
        if m:
            a, b = float(m.group(1)), float(m.group(2))
            low_v, high_v = min(a, b), max(a, b)
    if low_v is None and high_v is None:
        m = re.search(rf"(?:spec|스펙)\s*[:=]?\s*({num})\s*(이상|이하|초과|미만)?", text, re.IGNORECASE)
        if m:
            v = float(m.group(1))
            direction = str(m.group(2) or "")
            if direction in ("이상", "초과"):
                low_v = v
            elif direction in ("이하", "미만"):
                high_v = v
            else:
                high_v = v  # 방향이 없으면 상한(USL)으로 가정
    if low_v is None and high_v is None:
        return {"low": None, "high": None, "label": ""} if wants_spec_out else None
    parts = []
    if low_v is not None:
        parts.append(f"low {low_v:g}")
    if high_v is not None:
        parts.append(f"high {high_v:g}")
    return {"low": low_v, "high": high_v, "label": " / ".join(parts)}


def _metric_map_source_order(prompt: str, product: str = "") -> list[tuple[str, list[Path]]]:
    up = _upper(prompt)
    product_hint = _product_hint(prompt, product)
    explicit_inline = "INLINE" in up or "인라인" in str(prompt or "")
    explicit_et = "ET" in up
    if explicit_inline and not explicit_et:
        return [("INLINE", _inline_files(product_hint))]
    if explicit_et and not explicit_inline:
        return [("ET", _et_files(product_hint))]
    order: list[tuple[str, list[Path]]] = []
    if explicit_et:
        order.append(("ET", _et_files(product_hint)))
    if explicit_inline:
        order.append(("INLINE", _inline_files(product_hint)))
    for source, getter in (("ET", _et_files), ("INLINE", _inline_files)):
        if source not in {s for s, _ in order}:
            order.append((source, getter(product_hint)))
    return order


def _handle_inline_box_chart(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    text = str(prompt or "")
    if not _is_box_chart_request(text):
        return {"handled": False}
    product_hint = _product_hint(text, product)
    if not product_hint:
        return {
            "handled": True,
            "intent": "dashboard_box_needs_context",
            "action": "collect_required_fields",
            "answer": "Box plot을 그리려면 product가 필요합니다. 예: `PRODA CD_GATE box plot 그려줘`",
            "missing": ["product"],
            "feature": "dashboard",
        }
    inline_files = _inline_files(product_hint)
    if not inline_files:
        return {"handled": True, "intent": "dashboard_box", "answer": f"{product_hint} INLINE parquet을 찾지 못했습니다.", "feature": "dashboard"}
    inline_lf = _scan_parquet(inline_files)
    cols = _schema_names(inline_lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    item_col = _ci_col(cols, "item_id", "ITEM_ID", "rawitem_id", "RAWITEM_ID", "item", "ITEM")
    value_col = _ci_col(cols, "value", "VALUE", "_value", "val", "VAL")
    if not item_col or not value_col:
        return {"handled": True, "intent": "dashboard_box", "answer": "INLINE 데이터에서 item_id/value 컬럼을 찾지 못했습니다.", "feature": "dashboard"}
    metric, item_matches, item_candidates = _inline_metric_match_for_prompt(inline_lf, item_col, text)
    if not metric:
        return {
            "handled": True,
            "intent": "dashboard_box_needs_context",
            "action": "collect_required_fields",
            "answer": "Box plot으로 그릴 INLINE item을 찾지 못했습니다. item명을 더 정확히 알려주세요.",
            "missing": ["item_id"],
            "feature": "dashboard",
            "table": {"kind": "inline_item_candidates", "title": "INLINE item candidates", "placement": "below", "columns": _table_columns(["item_id"]), "rows": [{"item_id": x} for x in item_candidates], "total": len(item_candidates)},
        }
    aliases = _product_aliases(product_hint)
    lots = _lot_tokens(text)
    filters = []
    if aliases and product_col:
        filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    if lots:
        lot_expr = _or_contains([c for c in (root_col, lot_col, fab_col) if c], lots)
        if lot_expr is not None:
            filters.append(lot_expr)
    filters.append(pl.col(item_col).cast(_STR, strict=False).is_in(item_matches or [metric]))
    for expr in filters:
        inline_lf = inline_lf.filter(expr)
    group_expr = pl.col(root_col).cast(_STR, strict=False).alias("group") if root_col else pl.lit(product_hint).alias("group")
    try:
        df = (
            inline_lf.select([
                group_expr,
                pl.col(value_col).cast(pl.Float64, strict=False).alias("value"),
                pl.col(wafer_col).cast(_STR, strict=False).alias("wafer_id") if wafer_col else pl.lit("").alias("wafer_id"),
            ])
            .drop_nulls(subset=["group", "value"])
            .limit(200000)
            .collect()
        )
    except Exception as e:
        logger.warning("flowi inline box failed: %s", e)
        return {"handled": True, "intent": "dashboard_box", "answer": f"Box plot query 실패: {e}", "feature": "dashboard"}
    buckets: dict[str, list[float]] = {}
    wafer_counts: dict[str, set[str]] = {}
    for row in df.to_dicts():
        label = _text(row.get("group")) or product_hint
        try:
            val = float(row.get("value"))
        except Exception:
            continue
        if not math.isfinite(val):
            continue
        buckets.setdefault(label, []).append(val)
        wf = _text(row.get("wafer_id"))
        if wf:
            wafer_counts.setdefault(label, set()).add(wf)
    min_n = max(1, int((_flowi_chart_defaults().get("box") or FLOWI_CHART_DEFAULTS["box"]).get("min_n") or 3))
    max_groups = max(1, min(40, int((_flowi_chart_defaults().get("box") or FLOWI_CHART_DEFAULTS["box"]).get("max_groups") or 12)))
    boxes = []
    for label, vals in buckets.items():
        if len(vals) < min_n:
            continue
        vals_s = sorted(vals)
        boxes.append({
            "label": label,
            "min": _round4(vals_s[0]),
            "q1": _round4(_percentile_sorted(vals_s, 0.25)),
            "median": _round4(_percentile_sorted(vals_s, 0.5)),
            "q3": _round4(_percentile_sorted(vals_s, 0.75)),
            "max": _round4(vals_s[-1]),
            "mean": _round4(sum(vals_s) / len(vals_s)),
            "n": len(vals_s),
            "wafer_count": len(wafer_counts.get(label, set())),
        })
    boxes.sort(key=lambda r: (-int(r.get("n") or 0), str(r.get("label") or "")))
    boxes = boxes[:max_groups]
    rows = boxes
    answer = (
        f"{product_hint} {metric} INLINE 분포를 root_lot_id별 box plot으로 그렸습니다. "
        f"group={len(boxes)}, item match={', '.join(item_matches or [metric])}."
    ) if boxes else f"{product_hint} {metric} 조건으로 box plot을 만들 row가 부족합니다."
    cols_out = ["label", "min", "q1", "median", "q3", "max", "mean", "n", "wafer_count"]
    return {
        "handled": True,
        "intent": "dashboard_box_chart",
        "action": "query_inline_box_chart",
        "answer": answer,
        "feature": "dashboard",
        "slots": {"product": product_hint, "metric": metric, "lots": lots},
        "chart_result": {
            "ok": True,
            "kind": "dashboard_box",
            "title": f"{product_hint} {metric} Box Plot",
            "boxes": boxes,
            "total": len(boxes),
            "x_label": "root_lot_id",
            "y_label": metric,
            "metric": metric,
            "sources": {"inline_file_count": len(inline_files), "inline_items": item_matches or [metric]},
        },
        "table": {"kind": "dashboard_box", "title": f"{metric} box plot", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in rows], "total": len(rows)},
    }


def _handle_wafer_map_chart(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    text = str(prompt or "")
    if not _is_wafer_map_chart_request(text):
        return {"handled": False}
    product_hint = _product_hint(text, product)
    if not product_hint:
        return {
            "handled": True,
            "intent": "dashboard_wafer_map_needs_context",
            "action": "collect_required_fields",
            "answer": "WF map을 그리려면 product가 필요합니다. 예: `PRODA CD_GATE WF map 그려줘`",
            "missing": ["product"],
            "feature": "dashboard",
        }
    spec = _parse_spec_bounds(text)
    if spec and spec.get("low") is None and spec.get("high") is None:
        return {
            "handled": True,
            "intent": "dashboard_wafer_map_needs_context",
            "action": "collect_required_fields",
            "answer": "spec out map을 그리려면 spec 값이 필요합니다. 예: `PRODA IOFF spec 0.5 이하 spec out map 그려줘` (USL/LSL, `spec 0.2~0.5` 범위도 지원)",
            "missing": ["spec"],
            "feature": "dashboard",
        }
    lots = _lot_tokens(text)
    aliases = _product_aliases(product_hint)
    item_candidates: list[str] = []
    inline_needs_coord_map = False
    for source, files in _metric_map_source_order(text, product_hint):
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
            item_col = _ci_col(cols, "item_id", "ITEM_ID", "rawitem_id", "RAWITEM_ID", "item", "ITEM")
            value_col = _ci_col(cols, "value", "VALUE", "_value", "val", "VAL")
            x_adj_col = _ci_col(cols, "chip_x_adj", "CHIP_X_ADJ")
            y_adj_col = _ci_col(cols, "chip_y_adj", "CHIP_Y_ADJ")
            x_pos_col = _ci_col(cols, "chip_x_pos", "CHIP_X_POS")
            y_pos_col = _ci_col(cols, "chip_y_pos", "CHIP_Y_POS")
            shot_x_col = _ci_col(cols, "shot_x", "SHOT_X", "x", "X")
            shot_y_col = _ci_col(cols, "shot_y", "SHOT_Y", "y", "Y")
            flat_col = _ci_col(cols, "flat_zone", "FLAT_ZONE")
            coord_note = ""
            if x_adj_col and y_adj_col:
                cx_col, cy_col = x_adj_col, y_adj_col
                coord_basis = "chip_x_adj/chip_y_adj"
            elif x_pos_col and y_pos_col:
                cx_col, cy_col = x_pos_col, y_pos_col
                coord_basis = "chip_x_pos/chip_y_pos"
                if flat_col:
                    coord_note = " flat_zone(notch 반시계 회전각, 0=horizontal/90=vertical) 회전 보정으로 horizontal notch 기준으로 변환해 그렸습니다."
                else:
                    coord_note = " flat_zone 컬럼이 없어 회전 보정 없이 pos 좌표 그대로 그렸습니다 — TEG vertical 항목은 왜곡될 수 있습니다."
            else:
                cx_col, cy_col = shot_x_col, shot_y_col
                coord_basis = "shot_x/shot_y"
            if not (item_col and value_col and cx_col and cy_col):
                if source == "INLINE" and item_col and value_col and _ci_col(cols, "subitem_id", "SUBITEM_ID"):
                    inline_needs_coord_map = True
                    if not item_candidates:
                        item_candidates = _unique_strings(lf, item_col, limit=80)
                continue
            metric, item_matches, item_candidates = _inline_metric_match_for_prompt(lf, item_col, text)
            if not metric:
                continue
            filters = []
            if aliases and product_col:
                filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
            if lots:
                lot_expr = _or_contains([c for c in (root_col, lot_col, fab_col) if c], lots)
                if lot_expr is not None:
                    filters.append(lot_expr)
            filters.append(pl.col(item_col).cast(_STR, strict=False).is_in(item_matches or [metric]))
            for expr in filters:
                lf = lf.filter(expr)
            x_expr = pl.col(cx_col).cast(pl.Float64, strict=False)
            y_expr = pl.col(cy_col).cast(pl.Float64, strict=False)
            if coord_basis == "chip_x_pos/chip_y_pos" and flat_col:
                # flat_zone = notch 반시계 회전각 → -각도 회전으로 horizontal notch(=Chip_Radius.csv의 adj) 기준 정규화.
                # 90° 배수만 처리. 회전 부호는 사내 실데이터(Chip_Radius 매칭)로 1회 검증 필요.
                rot = (((pl.col(flat_col).cast(pl.Float64, strict=False).fill_null(0.0) / 90.0).round(0).cast(pl.Int64) % 4) + 4) % 4
                x_expr, y_expr = (
                    pl.when(rot == 1).then(y_expr).when(rot == 2).then(-x_expr).when(rot == 3).then(-y_expr).otherwise(x_expr),
                    pl.when(rot == 1).then(-x_expr).when(rot == 2).then(-y_expr).when(rot == 3).then(x_expr).otherwise(y_expr),
                )
            df = (
                lf.select([
                    x_expr.alias("shot_x"),
                    y_expr.alias("shot_y"),
                    pl.col(value_col).cast(pl.Float64, strict=False).alias("value"),
                    pl.col(root_col).cast(_STR, strict=False).alias("root_lot_id") if root_col else pl.lit("").alias("root_lot_id"),
                    pl.col(wafer_col).cast(_STR, strict=False).alias("wafer_id") if wafer_col else pl.lit("").alias("wafer_id"),
                ])
                .drop_nulls(subset=["shot_x", "shot_y", "value"])
                .group_by(["shot_x", "shot_y"])
                .agg([
                    pl.col("value").median().alias("value"),
                    pl.col("value").mean().alias("mean"),
                    pl.len().alias("n"),
                    pl.col("root_lot_id").n_unique().alias("lot_count"),
                    pl.col("wafer_id").n_unique().alias("wafer_count"),
                ])
                .sort(["shot_y", "shot_x"])
                .limit(800)
                .collect()
            )
        except Exception as e:
            logger.warning("flowi wafer map chart failed source=%s: %s", source, e)
            continue
        rows = df.to_dicts()
        s_low = spec.get("low") if spec else None
        s_high = spec.get("high") if spec else None
        out_n = 0
        points = []
        for row in rows:
            val = _round4(row.get("value"))
            point = {
                "x": _round4(row.get("shot_x")),
                "y": _round4(row.get("shot_y")),
                "value": val,
                "mean": _round4(row.get("mean")),
                "n": int(row.get("n") or 0),
                "lot_count": int(row.get("lot_count") or 0),
                "wafer_count": int(row.get("wafer_count") or 0),
                "label": f"shot({row.get('shot_x')},{row.get('shot_y')})",
            }
            if spec:
                is_out = bool(val is not None and (
                    (s_high is not None and val > s_high) or (s_low is not None and val < s_low)
                ))
                point["out"] = is_out
                row["out"] = "OUT" if is_out else ""
                out_n += 1 if is_out else 0
            points.append(point)
        if not points:
            continue
        x_label, _, y_label = coord_basis.partition("/")
        map_config = {
            "chart_type": "wafer_map",
            "source_type": source,
            "product": product_hint,
            "item_id": (item_matches or [metric])[0],
            "metric": metric,
            "lots": lots,
            "coord_x": cx_col,
            "coord_y": cy_col,
            "x_col": cx_col,
            "y_col": cy_col,
            "value_col": "value",
            "grain": "shot",
            "aggregation": "median",
            "wafer_mode": "spec_out" if spec else "value",
            "wafer_spec_low": spec.get("low") if spec else None,
            "wafer_spec_high": spec.get("high") if spec else None,
        }
        if spec:
            answer = (
                f"{product_hint} {source} {metric} spec({spec['label']}) 기준 spec out map을 그렸습니다. "
                f"shot median 기준 out {out_n}/{len(points)} — 빨간색=spec out, 회색=in spec. 좌표={coord_basis}.{coord_note}"
            )
        else:
            answer = (
                f"{product_hint} {source} {metric}을 {coord_basis} 기준 median으로 집계해 WF map을 그렸습니다. "
                f"points={len(points)}, item match={', '.join(item_matches or [metric])}.{coord_note}"
            )
        cols_out = ["shot_x", "shot_y", "value", "mean", "n", "lot_count", "wafer_count"] + (["out"] if spec else [])
        return {
            "handled": True,
            "intent": "dashboard_wafer_map_chart",
            "action": "query_metric_wafer_map",
            "answer": answer,
            "feature": "dashboard",
            "chart_type": "wafer_map",
            "config": map_config,
            "chart_config": map_config,
            "slots": {"product": product_hint, "metric": metric, "source": source, "lots": lots, **({"spec": spec} if spec else {})},
            "chart_result": {
                "ok": True,
                "kind": "dashboard_wafer_map",
                "title": f"{product_hint} {source} {metric} " + ("Spec Out Map" if spec else "WF Map"),
                "points": points,
                "total": len(points),
                "mode": "spec_out" if spec else "value",
                **({"spec": spec, "out_n": out_n} if spec else {}),
                "x_label": x_label or "shot_x",
                "y_label": y_label or "shot_y",
                "coord_basis": coord_basis,
                "value_label": f"{metric} median",
                "metric": metric,
                "product": product_hint,
                "source": source,
                "config": map_config,
                "chart_config": map_config,
                "sources": {"db": f"1.RAWDATA_DB_{source}", "file_count": len(files), "files": [str(p) for p in files[:24]], "items": item_matches or [metric], "sql": _flowi_dashboard_sql_from_config(map_config)},
            },
            "table": {"kind": "dashboard_wafer_map", "title": f"{metric} WF map", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in rows[:max(1, min(120, max_rows * 8))]], "total": len(rows)},
        }
    if inline_needs_coord_map:
        return {
            "handled": True,
            "intent": "dashboard_wafer_map_needs_inline_mapping",
            "action": "collect_inline_coordinate_mapping",
            "answer": "INLINE raw DB에는 shot_x/shot_y가 없고 subitem_id만 있습니다. inline_matching.csv의 matching_table과 TEG 위치조회 Inline map TABLE이 연결되면 매핑된 subitem만 ET shot 좌표로 변환해 그릴 수 있습니다.",
            "missing": ["inline_matching.matching_table", "inline_map_settings"],
            "feature": "dashboard",
            "table": {"kind": "wafer_map_item_candidates", "title": "INLINE item candidates", "placement": "below", "columns": _table_columns(["item_id"]), "rows": [{"item_id": x} for x in item_candidates[:40]], "total": len(item_candidates)},
        }
    return {
        "handled": True,
        "intent": "dashboard_wafer_map_needs_context",
        "action": "collect_required_fields",
        "answer": "WF map으로 그릴 item 또는 ET shot_x/shot_y/value 형태의 데이터를 찾지 못했습니다. INLINE raw는 subitem_id 기반이라 좌표 매핑이 먼저 필요합니다.",
        "missing": ["item_id"],
        "feature": "dashboard",
        "table": {"kind": "wafer_map_item_candidates", "title": "WF map item candidates", "placement": "below", "columns": _table_columns(["item_id"]), "rows": [{"item_id": x} for x in item_candidates[:40]], "total": len(item_candidates)},
    }


def _handle_inline_trend_chart(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    text = str(prompt or "")
    if not (_contains_chart_intent(text) and _is_trend_chart_request(text)):
        return {"handled": False}
    # 명시적으로 다른 소스(VM/ET/FAB)만 지정한 trend 는 INLINE 데이터로 처리하지 않는다
    # (INLINE parquet 을 읽어 헛도는 오처리 방지). ET 는 전용 _handle_et_trend_chart 가,
    # VM 은 아직 전용 trend 핸들러가 없어 소스 매칭 실패 안내로 흐르게 둔다.
    _src = _source_terms(text)
    if "INLINE" not in _src and _src & {"VM", "ET", "FAB"}:
        return {"handled": False}
    product_hint = _product_hint(text, product)
    if not product_hint:
        return {
            "handled": True,
            "intent": "dashboard_inline_trend_needs_context",
            "action": "collect_required_fields",
            "answer": "Trend 차트를 그리려면 product가 필요합니다. 예: `PRODA0 SPACER_CD Trend 그려줘`",
            "missing": ["product"],
            "feature": "dashboard",
        }
    inline_files = _inline_files(product_hint)
    if not inline_files:
        return {"handled": True, "intent": "dashboard_inline_trend", "answer": f"{product_hint} INLINE parquet을 찾지 못했습니다.", "feature": "dashboard"}
    inline_lf = _scan_parquet(inline_files)
    cols = _schema_names(inline_lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    lot_wf_col = _ci_col(cols, "lot_wf", "LOT_WF")
    item_col = _ci_col(cols, "item_id", "ITEM_ID", "rawitem_id", "RAWITEM_ID", "item", "ITEM")
    value_col = _ci_col(cols, "value", "VALUE", "_value", "val", "VAL")
    time_col, time_question = _flowi_trend_time_column(text, cols, "INLINE")
    if time_question:
        return time_question
    shot_id_col = _ci_col(cols, "subitem_id", "SUBITEM_ID", "shot_id", "SHOT_ID")
    shot_x_col = _ci_col(cols, "shot_x", "SHOT_X", "die_x", "DIE_X")
    shot_y_col = _ci_col(cols, "shot_y", "SHOT_Y", "die_y", "DIE_Y")
    spec_high_col = _ci_col(cols, "spec_high", "spec_hi", "usl", "spec_max", "spec_upper", "upper_spec")
    spec_low_col = _ci_col(cols, "spec_low", "spec_lo", "lsl", "spec_min", "spec_lower", "lower_spec")
    if not item_col or not value_col or not time_col:
        return {
            "handled": True,
            "intent": "dashboard_inline_trend",
            "answer": "INLINE 데이터에서 item_id/value/time 컬럼을 찾지 못했습니다.",
            "table": {"kind": "dashboard_inline_trend_error", "title": "Missing INLINE columns", "placement": "below", "columns": _table_columns(["message", "columns"]), "rows": [{"message": "missing item_id/value/time", "columns": ", ".join(cols[:80])}], "total": 1},
            "feature": "dashboard",
        }
    if not lot_wf_col and not (root_col and wafer_col):
        return {
            "handled": True,
            "intent": "dashboard_inline_trend",
            "answer": "INLINE Trend scatter에는 lot_wf 또는 root_lot_id/wafer_id 컬럼이 필요합니다.",
            "table": {"kind": "dashboard_inline_trend_error", "title": "Missing INLINE grain columns", "placement": "below", "columns": _table_columns(["message", "columns"]), "rows": [{"message": "missing lot_wf or root_lot_id/wafer_id", "columns": ", ".join(cols[:80])}], "total": 1},
            "feature": "dashboard",
        }
    metric, item_matches, item_candidates = _inline_metric_match_for_prompt(inline_lf, item_col, text)
    if not metric:
        return {
            "handled": True,
            "intent": "dashboard_inline_trend_needs_context",
            "action": "collect_required_fields",
            "answer": "Trend로 그릴 INLINE item을 찾지 못했습니다. item명을 더 정확히 알려주세요.",
            "missing": ["item_id"],
            "feature": "dashboard",
            "table": {"kind": "inline_item_candidates", "title": "INLINE item candidates", "placement": "below", "columns": _table_columns(["item_id"]), "rows": [{"item_id": x} for x in item_candidates], "total": len(item_candidates)},
        }
    has_shot_grain = bool(shot_id_col or (shot_x_col and shot_y_col))
    include_shot = _explicit_shot_grain(text)
    explicit_lot_wf = _explicit_lot_wf_grain(text)
    if has_shot_grain and not include_shot and not explicit_lot_wf:
        return {
            "handled": True,
            "intent": "dashboard_inline_trend_needs_grain",
            "action": "collect_required_fields",
            "answer": (
                f"{product_hint} {metric}은 INLINE item으로 해석했고 Trend x축은 tkout_time 기준입니다. "
                "INLINE grain을 선택해야 합니다."
            ),
            "missing": ["chart_grain"],
            "feature": "dashboard",
            "pending_prompt": text,
            "last_partial_prompt": text,
            "slots": {
                "product": product_hint,
                "metric": metric,
                "source_type": "INLINE",
                "x_col": time_col,
                "time_col": time_col,
                "color_by": "KNOB" if ("KNOB" in _upper(text) or "노브" in text) else "",
            },
            "clarification": {
                "question": "INLINE Trend를 어떤 grain으로 그릴까요?",
                "choices": [
                    {
                        "id": "lot_wf",
                        "label": "1",
                        "title": "lot_wf avg",
                        "value": "lot_wf",
                        "recommended": True,
                        "description": "root_lot_id+wafer_id별 value 평균을 tkout_time x축에 표시합니다.",
                        "prompt": f"{text} grain: lot_wf",
                    },
                    {
                        "id": "shot",
                        "label": "2",
                        "title": "shot 전체",
                        "value": "shot",
                        "recommended": False,
                        "description": "subitem_id/shot 좌표 단위 point를 모두 tkout_time x축에 표시합니다.",
                        "prompt": f"{text} grain: shot",
                    },
                ],
            },
            "term_resolution": [
                {"token": metric, "meaning": "INLINE item", "wiki_refs": ["schema:INLINE.item_id"], "query_filter": f"item_id={metric}", "status": "resolved"},
                {"token": "Trend", "meaning": "tkout_time x축 scatter", "wiki_refs": ["schema:INLINE.tkout_time"], "query_filter": "x=tkout_time", "status": "resolved"},
                {"token": "grain", "meaning": "lot_wf avg 또는 shot 전체 중 선택 필요", "wiki_refs": ["schema:INLINE.grain"], "query_filter": "await chart_grain", "status": "needs_input"},
            ],
        }
    aliases = _product_aliases(product_hint)
    lots = _flowi_chart_lot_tokens(text)
    filters = []
    if aliases and product_col:
        filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    if lots:
        lot_expr = _or_contains([c for c in (root_col, lot_col, fab_col, lot_wf_col) if c], lots)
        if lot_expr is not None:
            filters.append(lot_expr)
    filters.append(pl.col(item_col).cast(_STR, strict=False).is_in(item_matches or [metric]))
    if shot_id_col:
        normalized_subitem = (
            pl.col(shot_id_col).cast(_STR, strict=False).str.strip_chars().str.to_lowercase()
            .str.replace_all(r"[\s_.-]+", "")
        )
        filters.append(~normalized_subitem.is_in(list(inline_coordinates.NORMALIZED_SUMMARY_SUBITEM_IDS)))
    for expr in filters:
        inline_lf = inline_lf.filter(expr)

    exprs = [
        pl.col(time_col).cast(_STR, strict=False).alias("tkout_time"),
        pl.col(value_col).cast(pl.Float64, strict=False).alias("metric_value"),
    ]
    if root_col:
        exprs.append(_root_key_expr(root_col).alias("root_lot_id"))
    else:
        exprs.append(pl.lit("").alias("root_lot_id"))
    if wafer_col:
        exprs.append(_wafer_key_expr(wafer_col).alias("wafer_id"))
    else:
        exprs.append(pl.lit("").alias("wafer_id"))
    if root_col and wafer_col:
        exprs.append(_lot_wf_expr(root_col, wafer_col).alias("lot_wf"))
    elif lot_wf_col:
        exprs.append(pl.col(lot_wf_col).cast(_STR, strict=False).alias("lot_wf"))
    else:
        exprs.append(pl.lit("").alias("lot_wf"))
    grain_cols = ["tkout_time", "lot_wf", "root_lot_id", "wafer_id"]
    if include_shot:
        if shot_id_col:
            exprs.append(pl.col(shot_id_col).cast(_STR, strict=False).alias("shot_id"))
            grain_cols.append("shot_id")
        elif shot_x_col and shot_y_col:
            exprs.extend([
                pl.col(shot_x_col).cast(_STR, strict=False).alias("shot_x"),
                pl.col(shot_y_col).cast(_STR, strict=False).alias("shot_y"),
            ])
            grain_cols.extend(["shot_x", "shot_y"])
    if spec_high_col:
        exprs.append(pl.col(spec_high_col).cast(pl.Float64, strict=False).alias("spec_high"))
    if spec_low_col:
        exprs.append(pl.col(spec_low_col).cast(pl.Float64, strict=False).alias("spec_low"))
    lot_wf_rule = "derived_from_root_lot_id_wafer_id" if root_col and wafer_col else ("source_lot_wf" if lot_wf_col else "unavailable")
    # INLINE 은 avg 기본. shot 은 grain(include_shot)으로 처리하므로 집계는 단일값 avg 로.
    agg_name = _flowi_chart_agg_from_prompt(text, default="avg")
    y_agg = "avg" if agg_name == "shot" else agg_name
    try:
        scatter_cfg = (_flowi_chart_defaults().get("scatter") or FLOWI_CHART_DEFAULTS["scatter"])
        point_limit = max(20, min(5000, int(scatter_cfg.get("max_points") or FLOWI_CHART_POINT_LIMIT)))
    except Exception:
        scatter_cfg = FLOWI_CHART_DEFAULTS["scatter"]
        point_limit = FLOWI_CHART_POINT_LIMIT
    try:
        grouped = (
            inline_lf.select(exprs)
            .drop_nulls(subset=["tkout_time", "metric_value", "lot_wf"])
            .group_by(grain_cols)
            .agg([
                pl.col("metric_value").mean().alias("avg"),
                pl.col("metric_value").median().alias("median"),
                pl.len().alias("n"),
                _flowi_agg_polars_expr(y_agg, "metric_value").alias("y_value"),
                *([pl.col("spec_high").drop_nulls().first().alias("spec_high")] if spec_high_col else []),
                *([pl.col("spec_low").drop_nulls().first().alias("spec_low")] if spec_low_col else []),
            ])
        )
        knob = None
        knob_join_cols: list[str] = []
        if "KNOB" in _upper(text) or "노브" in text:
            knob = _flowi_knob_lf(product_hint, lots, text, [metric])
            if knob.get("ok"):
                knob_join_cols = _flowi_knob_join_cols(grouped.collect_schema().names(), knob.get("group_cols") or [])
                if knob_join_cols:
                    grouped = grouped.join(knob["lf"], on=knob_join_cols, how="left")
        df = grouped.sort("tkout_time").limit(point_limit).collect()
    except Exception as e:
        logger.warning("flowi inline trend failed: %s", e)
        return {"handled": True, "intent": "dashboard_inline_trend", "answer": f"INLINE trend query 실패: {e}", "feature": "dashboard"}
    rows = df.to_dicts()
    for row in rows:
        row[time_col] = row.get("tkout_time") or ""
    points = []
    color_counts: dict[str, int] = {}
    knob_color_ready = bool(knob and knob.get("ok") and knob_join_cols)
    for idx, row in enumerate(rows):
        y = _round4(row.get("y_value"))
        if y is None:
            continue
        color_value = _text(row.get("color_value"))
        if color_value:
            color_counts[color_value] = color_counts.get(color_value, 0) + 1
        points.append({
            "x": idx,
            "x_label": _text(row.get("tkout_time")),
            "tkout_time": _text(row.get("tkout_time")),
            "time_col": time_col,
            "time_value": _text(row.get("tkout_time")),
            "y": y,
            "avg": _round4(row.get("avg")),
            "median": _round4(row.get("median")),
            "n": int(row.get("n") or 0),
            "lot_wf": row.get("lot_wf") or "",
            "root_lot_id": row.get("root_lot_id") or "",
            "wafer_id": row.get("wafer_id") or "",
            "shot_id": row.get("shot_id") or "",
            "shot_x": row.get("shot_x") or "",
            "shot_y": row.get("shot_y") or "",
            "color_by": (knob.get("display_name") if knob_color_ready else "") or "",
            "color_value": color_value,
            "label": row.get("shot_id") or row.get("lot_wf") or "",
            **({"spec_high": _round4(row.get("spec_high"))} if spec_high_col else {}),
            **({"spec_low": _round4(row.get("spec_low"))} if spec_low_col else {}),
        })
    fit_requested = _chart_fit_intent(text)
    fit = _chart_fit_from_rows(points) if fit_requested else {}
    agg_meta = "shot" if include_shot else y_agg
    agg_disp = "shot(all)" if include_shot else _flowi_agg_label(y_agg)
    other_aggs = [a for a in ("avg", "median", "p90", "max") if a != y_agg][:3]
    answer = f"시간축: {time_col}. " + (
        f"{product_hint} {metric} INLINE Trend를 {time_col} x축 scatter로 그렸습니다. "
        + ("INLINE은 shot 단위 value를 시간별로 표시했습니다. " if include_shot
           else f"INLINE은 lot_wf별 {agg_disp}(value)를 시간별로 집계했습니다. ")
        + f"표시 point={len(points)}, item match={', '.join(item_matches or [metric])}. "
        + f"다른 집계로 보려면: {', '.join(_flowi_agg_label(a) for a in other_aggs)}, shot(전체)."
    )
    if knob_color_ready:
        answer += " KNOB가 없는 point는 회색으로 표시합니다."
    if fit:
        answer += f" 1차식 fitting line과 R²={fit.get('r2')}를 포함했습니다."
    spec_overlay = bool(
        (spec_high_col or spec_low_col)
        and any(p.get("spec_high") is not None or p.get("spec_low") is not None for p in points)
    )
    if spec_overlay:
        answer += " INLINE spec(high/low)을 빨간 계단식 라인으로 함께 표시했습니다 — spec은 wafer마다 달라질 수 있습니다."
    if not points:
        answer = f"{product_hint} {metric} 조건으로 Trend chart row를 찾지 못했습니다."
    cols_out = [time_col, "lot_wf", "root_lot_id", "wafer_id", "shot_id", "shot_x", "shot_y", "avg", "median", "n", "color_value"]
    config_overrides = {
        "chart_type": "scatter",
        "source_type": "INLINE",
        "x_col": time_col,
        "time_col": time_col,
        "y_col": "value",
        "y_expr": "value (shot, no agg)" if include_shot else f"{y_agg}(value)",
        "item_id": (item_matches or [metric])[0],
        "metric": metric,
        "lots": lots,
        "grain": "shot" if include_shot else "lot_wf",
        "aggregation": agg_meta,
        "group_by": "shot" if include_shot else "lot_wf",
        "x_label": time_col,
        "y_label": f"{metric} {agg_disp}",
        "color_missing": "gray" if knob_color_ready else "",
        "color_by": (knob.get("display_name") if knob_color_ready else "") or "",
        "fit": "linear" if fit_requested else "none",
        "render_preset": {**scatter_cfg, "engine": "plotly", "grain": "shot" if include_shot else "lot_wf", "x_axis": "time"},
    }
    return {
        "handled": True,
        "intent": "dashboard_inline_trend_chart",
        "action": "query_inline_trend_scatter_chart",
        "answer": answer,
        "feature": "dashboard",
        "chart_type": "scatter",
        "config": config_overrides,
        "chart_config": config_overrides,
        "slots": {"product": product_hint, "metric": metric, "lots": lots, "source_type": "INLINE", "grain": "shot" if include_shot else "lot_wf", "aggregation": agg_meta, "time_col": time_col},
        "chart_result": {
            "ok": True,
            "kind": "dashboard_scatter",
            "title": f"{product_hint} {metric} Trend",
            "points": points,
            "total": len(points),
            "spec_overlay": spec_overlay,
            "x_label": time_col,
            "y_label": f"{metric} {agg_disp}",
            "metric": metric,
            "source_type": "INLINE",
            "x_col": time_col,
            "time_col": time_col,
            "item_id": (item_matches or [metric])[0],
            "grain": "shot" if include_shot else "lot_wf",
            "aggregation": agg_meta,
            "aggregations": {"INLINE": agg_meta},
            "lot_wf_rule": lot_wf_rule,
            "join_cols": knob_join_cols if knob_color_ready else [],
            "color_by": (knob.get("display_name") if knob_color_ready else "") or "",
            "color_missing": "gray" if knob_color_ready else "",
            "color_values": [{"value": k, "count": v} for k, v in sorted(color_counts.items(), key=lambda kv: (-kv[1], kv[0]))],
            "fit": fit,
            "config_overrides": config_overrides,
            "render_preset": config_overrides["render_preset"],
            "sources": {
                "db": "1.RAWDATA_DB_INLINE",
                "files": [str(p) for p in inline_files[:24]],
                "sql": _flowi_dashboard_sql_from_config(config_overrides),
                "inline_file_count": len(inline_files),
                "inline_items": item_matches or [metric],
                "lot_wf": "root_lot_id + '_' + wafer_id" if root_col and wafer_col else "lot_wf",
                "knob_column": knob.get("knob_col") if knob_color_ready else "",
            },
        },
        "table": {"kind": "dashboard_inline_trend", "title": f"{metric} Trend", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in rows[:max(1, min(120, max_rows * 8))]], "total": len(rows)},
    }
