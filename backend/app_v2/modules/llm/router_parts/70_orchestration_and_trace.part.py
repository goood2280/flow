def _handle_flowi_query(
    prompt: str,
    product: str = "",
    max_rows: int = 12,
    allowed_keys: set[str] | None = None,
    username: str = "flowi",
    role: str = "user",
    agent_context: dict[str, Any] | None = None,
) -> dict:
    interpretation = _flowi_wiki_prompt_interpretation(prompt)
    generic_actions = {
        "route_flowi_feature",
        "open_dashboard",
        "open_filebrowser",
        "open_splittable",
        "open_inform",
        "open_meeting",
    }
    with flowi_progress.step("기능 라우팅", "질문에 맞는 기능 찾는 중"):
        tool = _handle_flowi_query_core(
            prompt,
            product,
            max_rows=max_rows,
            allowed_keys=allowed_keys,
            username=username,
            role=role,
            agent_context=agent_context,
        )
    _flowi_progress_route_note(tool)
    wiki_splittable = (
        interpretation.get("pre_route")
        and str(tool.get("feature") or "") != "splittable"
        and _flowi_wiki_interpretation_prefers_splittable(prompt, interpretation, allowed_keys)
    )
    if interpretation.get("pre_route") and (wiki_splittable or not tool.get("handled") or str(tool.get("action") or "") in generic_actions):
        route_prompt = str(interpretation.get("augmented_prompt") or prompt)
        routed_tool = _handle_flowi_query_core(
            route_prompt,
            product,
            max_rows=max_rows,
            allowed_keys=allowed_keys,
            username=username,
            role=role,
            agent_context=agent_context,
        )
        if routed_tool.get("handled") and (not tool.get("handled") or str(tool.get("action") or "") in generic_actions or str(routed_tool.get("action") or "") not in generic_actions):
            tool = routed_tool
            _flowi_progress_route_note(tool, label="기능 재라우팅")
    return _flowi_apply_wiki_prompt_interpretation(tool, interpretation)


def _flowi_progress_route_note(tool: dict[str, Any], *, label: str = "기능 선택") -> None:
    """어떤 기능/동작이 골라졌는지만 진행 표시에 남긴다 (근거·데이터는 제외)."""
    if not isinstance(tool, dict):
        return
    feature = str(tool.get("feature") or "")
    action = str(tool.get("action") or tool.get("intent") or "")
    detail = " · ".join(part for part in (feature, action) if part)
    if not detail:
        return
    status = "blocked" if tool.get("blocked") else ("success" if tool.get("handled") else "skipped")
    flowi_progress.note(label, detail, status=status)


def _flowi_impact_context_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    explicit_knowledge = any(t in low or t in text for t in ("지식", "근거", "확인된", "이력", "기록", "wiki", "위키", "impact context"))
    domain = any(t in low or t in text for t in ("mts", "recipe", "레시피", "anchor", "앵커", "split 영향", "스플릿 영향", "영향 평가", "변경점", "변경 이력"))
    lot_knowledge = any(t in low or t in text for t in ("lot 이상", "랏 이상", "lot anomaly", "이상 lot"))
    return bool(domain or (explicit_knowledge and lot_knowledge))


def _flowi_impact_item_token(prompt: str) -> str:
    text = str(prompt or "")
    m = re.search(r"\b((?:INLINE|ET|VM|MASK|KNOB|FAB)_[A-Za-z0-9_.\-]+)", text, re.I)
    if m:
        return m.group(1)
    metric = _flowi_metric_token(prompt)
    return metric if metric and metric.upper() not in {"SORT", "SPLIT", "KNOB", "MTS"} else ""


def _flowi_impact_knob_token(prompt: str, item_id: str = "") -> str:
    text = str(prompt or "")
    m = re.search(r"\b(KNOB_[A-Za-z0-9_.\-]+|MASK_[A-Za-z0-9_.\-]+)", text, re.I)
    if m:
        return m.group(1)
    if str(item_id or "").upper().startswith(("KNOB_", "MASK_")):
        return item_id
    m = re.search(r"\b(?:knob|노브)\s*[:=]?\s*([A-Za-z0-9_.\-]+)", text, re.I)
    return m.group(1) if m else ""


def _flowi_impact_context_args(prompt: str, product: str = "") -> dict[str, Any]:
    classified = _classified_lot_tokens(prompt)
    root_lot_id = next(iter(classified.get("root_lot_ids") or []), "")
    item_id = _flowi_impact_item_token(prompt)
    knob = _flowi_impact_knob_token(prompt, item_id)
    return {
        "product": _product_hint(prompt, product),
        "root_lot_id": root_lot_id,
        "step_id": _flowi_func_step_token(prompt),
        "item_id": item_id,
        "knob": knob,
    }


def _flowi_impact_evidence_label(ctx: dict[str, Any]) -> str:
    wiki_ids = [str(row.get("doc_id") or row.get("id") or "") for row in ctx.get("wiki_refs") or [] if isinstance(row, dict)]
    event_ids = [str(row.get("event_id") or "") for row in ctx.get("event_refs") or [] if isinstance(row, dict)]
    parts = []
    if wiki_ids:
        parts.append("wiki " + ", ".join(wiki_ids[:4]))
    if event_ids:
        parts.append("event " + ", ".join(event_ids[:5]))
    return " / ".join(parts) if parts else "없음"


def _flowi_impact_ref_value(ref: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = ref.get(key)
        if value not in (None, "", [], {}):
            return str(value)
    return ""


def _flowi_impact_ref_label(ref: dict[str, Any]) -> str:
    step = _flowi_impact_ref_value(ref, ("step_id",))
    item = _flowi_impact_ref_value(ref, ("item_id", "knob_name"))
    return " / ".join([x for x in (step, item) if x]) or str(ref.get("title") or ref.get("event_id") or "event")


def _flowi_impact_change_phrase(ref: dict[str, Any]) -> str:
    if not isinstance(ref, dict):
        return ""
    before = _flowi_impact_ref_value(ref, ("previous_value", "old_value", "from_value", "previous_threshold", "old_threshold", "from_threshold"))
    after = _flowi_impact_ref_value(ref, ("new_value", "current_value", "to_value", "new_threshold", "current_threshold", "to_threshold"))
    label = _flowi_impact_ref_label(ref)
    if before or after:
        return f"{label} 기준 변경: {before or '-'} -> {after or '-'}"
    current = _flowi_impact_ref_value(ref, ("split_value", "baseline_value", "baseline", "criteria", "criterion"))
    if current:
        return f"{label} 기준: {current}"
    return ""


def _flowi_anchor_history_phrase(anchor_items: list[Any]) -> str:
    rows = [row for row in anchor_items if isinstance(row, dict)]
    if not rows:
        return ""
    rows.sort(key=lambda row: str(row.get("valid_from") or row.get("changed_at") or ""))
    parts = []
    for row in rows[-4:]:
        if row.get("status") == "verified_wiki" and not row.get("valid_from"):
            continue
        item = str(row.get("item_id") or row.get("title") or "").strip()
        if not item:
            continue
        start = str(row.get("valid_from") or "").strip()[:10]
        end = str(row.get("valid_to") or "").strip()[:10] or "현재"
        repl = str(row.get("replaced_by") or "").strip()
        label = f"{item}({start or '-'}~{end})"
        if repl:
            label += f" -> {repl}"
        parts.append(label)
    return " / ".join(parts)


def _flowi_impact_context_answer(ctx: dict[str, Any]) -> str:
    wiki_refs = ctx.get("wiki_refs") or []
    event_refs = ctx.get("event_refs") or []
    conflicts = ctx.get("conflicts") or []
    anchor_items = ctx.get("anchor_items") or []
    lot_anomalies = ctx.get("lot_anomalies") or []
    split_impacts = ctx.get("split_impacts") or []
    mts_changes = ctx.get("mts_changes") or []
    if wiki_refs:
        first = wiki_refs[0]
        status = f"확인된 운영 지식 {len(wiki_refs)}건"
        lead = f"{status}: {first.get('title') or first.get('doc_id')}"
    elif event_refs:
        lead = f"확인된 지식 없음 / 후보 이벤트만 있음: {len(event_refs)}건"
    else:
        lead = "확인된 지식 없음"
    lines = [lead]
    if conflicts:
        lines.append(f"영향 평가가 갈림: conflict {len(conflicts)}건")
    if lot_anomalies:
        lines.append(f"lot 이상 후보: {len(lot_anomalies)}건")
    if split_impacts:
        phrase = next((_flowi_impact_change_phrase(row) for row in split_impacts if _flowi_impact_change_phrase(row)), "")
        lines.append(f"split 영향 후보: {len(split_impacts)}건" + (f" ({phrase})" if phrase else ""))
    if mts_changes:
        phrase = next((_flowi_impact_change_phrase(row) for row in mts_changes if _flowi_impact_change_phrase(row)), "")
        lines.append(f"MTS 변경 후보: {len(mts_changes)}건" + (f" ({phrase})" if phrase else ""))
    if anchor_items:
        current = next((row for row in anchor_items if isinstance(row, dict) and not row.get("valid_to") and row.get("valid_from")), None)
        if current is None:
            current = next((row for row in anchor_items if isinstance(row, dict) and not row.get("valid_to")), anchor_items[0])
        if isinstance(current, dict):
            lines.append(f"Anchor item 현재: {current.get('step_id') or '-'} / {current.get('item_id') or current.get('title') or '-'}")
        history = _flowi_anchor_history_phrase(anchor_items)
        if history:
            lines.append(f"Anchor item 이력: {history}")
    lines.append("근거: " + _flowi_impact_evidence_label(ctx))
    return "\n".join(lines)


def _handle_knowledge_impact_context(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _flowi_impact_context_intent(prompt):
        return {"handled": False}
    args = _flowi_impact_context_args(prompt, product)
    ctx = knowledge_impact.impact_context(**args, limit=max(20, min(200, max_rows * 20)))
    retrieved = [
        {
            "id": row.get("doc_id") or row.get("id") or "",
            "doc_id": row.get("doc_id") or row.get("id") or "",
            "kind": "agent_wiki",
            "schema_type": row.get("schema_type") or "",
            "title": row.get("title") or row.get("doc_id") or "",
            "summary": row.get("summary") or "",
            "source": "impact_context",
            "score": 1.0,
        }
        for row in ctx.get("wiki_refs") or []
        if isinstance(row, dict) and (row.get("doc_id") or row.get("id"))
    ]
    rows = []
    for ref in (ctx.get("event_refs") or [])[: max(1, min(80, max_rows * 6))]:
        if not isinstance(ref, dict):
            continue
        before = _flowi_impact_ref_value(ref, ("previous_value", "old_value", "from_value", "previous_threshold", "old_threshold", "from_threshold"))
        after = _flowi_impact_ref_value(ref, ("new_value", "current_value", "to_value", "new_threshold", "current_threshold", "to_threshold"))
        rows.append({
            "type": ref.get("event_type") or "",
            "status": ref.get("status") or "",
            "id": ref.get("event_id") or "",
            "source": f"{ref.get('source_type') or ''}:{ref.get('source_id') or ''}".strip(":"),
            "changed_at": ref.get("changed_at") or "",
            "product": ref.get("product") or "",
            "root_lot_id": ref.get("root_lot_id") or "",
            "step_id": ref.get("step_id") or "",
            "item_id": ref.get("item_id") or ref.get("knob_name") or "",
            "split_value": ref.get("split_value") or ref.get("baseline_value") or ref.get("baseline") or "",
            "before": before,
            "after": after,
            "summary": ref.get("summary") or "",
        })
    return {
        "handled": True,
        "intent": "knowledge_impact_context",
        "action": "knowledge.impact_context.lookup",
        "feature": "knowledge",
        "answer": _flowi_impact_context_answer(ctx),
        "impact_context": ctx,
        "event_refs": ctx.get("event_refs") or [],
        "retrieved_knowledge": retrieved,
        "filters": args,
        "table": {
            "kind": "knowledge_impact_events",
            "title": "Knowledge impact event evidence",
            "placement": "below",
            "columns": _table_columns(["type", "status", "id", "source", "changed_at", "product", "root_lot_id", "step_id", "item_id", "split_value", "before", "after", "summary"]),
            "rows": rows,
            "total": len(ctx.get("event_refs") or []),
        } if rows else {},
    }


def _handle_flowi_query_core(
    prompt: str,
    product: str = "",
    max_rows: int = 12,
    allowed_keys: set[str] | None = None,
    username: str = "flowi",
    role: str = "user",
    agent_context: dict[str, Any] | None = None,
) -> dict:
    # NOTE (M6 dead-path note): As of M2 PRs #2~#7 + M6 the Unit AI dispatcher
    # (try_dispatch) in _run_flowi_chat catches filebrowser / meeting / inform /
    # tracker / dashboard / splittable / tablemap / diagnosis
    # prompts BEFORE this function is called. The corresponding if/elif blocks
    # below are kept intact (dead path) as a temporary safety net during the
    # migration. A future PR will remove the migrated branches once each unit
    # AI's handle() has been verified against production traces. calendar
    # still relies on this function (no LLM-specific handler for it).
    context_product = _flowi_context_product_hint(agent_context)
    product = _product_hint(prompt, product) or context_product
    raw_table_out = _handle_flowi_raw_table_followup(prompt, agent_context, max_rows)
    if raw_table_out.get("handled"):
        return raw_table_out
    if allowed_keys is None or "dashboard" in allowed_keys:
        provenance_out = _handle_dashboard_chart_raw_data_provenance_followup(
            prompt,
            agent_context,
            username=username,
            role=role,
        )
        if provenance_out.get("handled"):
            return provenance_out
        raw_data_out = _handle_dashboard_chart_raw_data_followup(
            prompt,
            agent_context,
            max_rows,
            username=username,
            role=role,
        )
        if raw_data_out.get("handled"):
            return raw_data_out
        chart_context_out = _handle_dashboard_chart_context_followup(prompt, product, max_rows, agent_context, username=username)
        if chart_context_out.get("handled"):
            return _augment_dashboard_tool(chart_context_out, prompt, product=product, username=username)
    impact_context_out = _handle_knowledge_impact_context(prompt, product, max_rows)
    if impact_context_out.get("handled"):
        return impact_context_out
    context_view_out = _handle_flowi_splittable_context_followup(prompt, product, max_rows, allowed_keys, agent_context)
    if context_view_out.get("handled"):
        return context_view_out
    if allowed_keys is None or {"filebrowser", "dashboard", "splittable"} & set(allowed_keys):
        step_mapping_out = _handle_step_mapping_lookup(prompt, product, max_rows)
        if step_mapping_out.get("handled"):
            return step_mapping_out
    if _flowi_context_prefers_splittable(agent_context) and _flowi_should_continue_splittable_context(prompt):
        prompt = f"{prompt} Split"
    if any(term in str(prompt or "").lower() or term in str(prompt or "") for term in ("테이블맵", "테이블 맵", "tablemap", "table map")):
        tablemap_allowed = {"tablemap"} if allowed_keys is None or "tablemap" in allowed_keys else set()
        return _unit_feature_guidance(prompt, product, max_rows=max_rows, allowed_keys=tablemap_allowed)
    early_matches = _matched_feature_entrypoints(prompt, limit=1, allowed_keys=allowed_keys)
    if early_matches and (early_matches[0].get("key") or "") == "tablemap":
        return _unit_feature_guidance(prompt, product, max_rows=max_rows, allowed_keys=allowed_keys)
    if allowed_keys is None or "tracker" in allowed_keys:
        tracker_purpose_out = _handle_tracker_lot_purpose_lookup(prompt, product, max_rows)
        if tracker_purpose_out.get("handled"):
            return tracker_purpose_out
    if (
        (allowed_keys is None or "splittable" in allowed_keys)
        and _flowi_knob_table_lookup_intent(prompt)
        and not _flowi_explicit_splittable_view_prompt(prompt)
        and not _knob_rulebook_lookup_intent(prompt)
    ):
        knob_table_out = _handle_knob_query(prompt, product, max_rows)
        if knob_table_out.get("handled"):
            return knob_table_out
    if allowed_keys is None or {"filebrowser", "dashboard", "splittable"} & set(allowed_keys):
        wip_out = _handle_lot_wip_location(prompt, product, max_rows)
        if wip_out.get("handled"):
            return wip_out
        fab_lot_out = _handle_current_fab_lot_lookup(prompt, product, max_rows)
        if fab_lot_out.get("handled"):
            return fab_lot_out
        current_location_out = _handle_fab_current_location_lookup(prompt, product, max_rows)
        if current_location_out.get("handled"):
            return current_location_out
        current_step_out = _handle_current_step_from_progress_cache(prompt, product, max_rows)
        if current_step_out.get("handled"):
            return current_step_out
        knob_value_out = _handle_find_lots_by_knob_value(
            prompt,
            product,
            max_rows,
            infer_unique_product=bool(allowed_keys is not None and set(allowed_keys) == {"splittable"}),
        )
        if knob_value_out.get("handled"):
            return knob_value_out
        metric_step_out = _handle_metric_at_step(prompt, product, max_rows)
        if metric_step_out.get("handled"):
            return metric_step_out
        wafer_split_out = _handle_wafer_split_at_step(prompt, product, max_rows)
        if wafer_split_out.get("handled"):
            return wafer_split_out
        mismatch_out = _handle_splittable_plan_mismatch_query(prompt, product, max_rows)
        if mismatch_out.get("handled"):
            return mismatch_out
        for handler in (_handle_split_fab_lot_basis, _handle_fab_corun_lots, _handle_knob_clean_interference, _handle_lot_anomaly_summary):
            ops_out = handler(prompt, product, max_rows)
            if ops_out.get("handled"):
                return ops_out
        eta_out = _handle_fab_step_eta(prompt, product, max_rows)
        if eta_out.get("handled"):
            return eta_out
        fab_eqp_out = _handle_fab_eqp_lookup(prompt, product, max_rows)
        if fab_eqp_out.get("handled"):
            return fab_eqp_out
        process_out = _handle_product_process_id_lookup(prompt, product, max_rows)
        if process_out.get("handled"):
            return process_out
        inline_item_out = _handle_inline_item_lookup(prompt, product, max_rows, username=username)
        if inline_item_out.get("handled"):
            return inline_item_out
        for handler in (_handle_step_mapping_lookup, _handle_knob_rulebook_lookup, _handle_ppid_knob_lookup, _handle_index_form_lookup):
            meta_out = handler(prompt, product, max_rows)
            if meta_out.get("handled"):
                return meta_out
        fab_progress_out = _handle_fab_progress_query(prompt, product, max_rows)
        if fab_progress_out.get("handled"):
            return fab_progress_out
    if (allowed_keys is None or "splittable" in allowed_keys) and _flowi_knob_table_lookup_intent(prompt) and not _knob_rulebook_lookup_intent(prompt):
        knob_table_out = _handle_knob_query(prompt, product, max_rows)
        if knob_table_out.get("handled"):
            return knob_table_out
    defer_diagnosis_for_source_chart = (
        (allowed_keys is None or "dashboard" in allowed_keys)
        and _contains_chart_intent(prompt)
        and (
            bool(_source_terms(prompt))
            or (_et_trend_should_handle(prompt) and _flowi_explicit_chart_draw_intent(prompt))
            or _is_wafer_map_chart_request(prompt)
        )
    )
    if (allowed_keys is None or "diagnosis" in allowed_keys) and not defer_diagnosis_for_source_chart:
        diag_out = _handle_semiconductor_diagnosis_query(prompt, product, max_rows)
        if diag_out.get("handled"):
            return diag_out
    if allowed_keys is None or {"filebrowser", "dashboard"} & set(allowed_keys):
        source_chart_out = _handle_dashboard_source_chart_runtime(
            prompt,
            product,
            max_rows,
            allowed_keys=allowed_keys,
            username=username,
            agent_context=agent_context,
        )
        if source_chart_out.get("handled"):
            return _augment_dashboard_tool(source_chart_out, prompt, product=product, username=username)
        multisource_out = _handle_flowi_multisource_query(
            prompt,
            product,
            max_rows,
            allowed_keys=allowed_keys,
            username=username,
        )
        if multisource_out.get("handled"):
            return multisource_out
    composite_allowed = allowed_keys is None or {"dashboard", "splittable"}.issubset(set(allowed_keys))
    if composite_allowed:
        composite_out = _handle_home_composite_lot_analysis(prompt, product, max_rows)
        if composite_out.get("handled"):
            return _augment_dashboard_tool(composite_out, prompt, product=product, username=username)
    if allowed_keys is None or "dashboard" in allowed_keys:
        knob_ratio_chart_out = _handle_knob_ratio_chart(prompt, product, max_rows)
        if knob_ratio_chart_out.get("handled"):
            return _augment_dashboard_tool(knob_ratio_chart_out, prompt, product=product, username=username)
        wafer_map_out = _handle_wafer_map_chart(prompt, product, max_rows)
        if wafer_map_out.get("handled"):
            return _augment_dashboard_tool(wafer_map_out, prompt, product=product, username=username)
        box_chart_out = _handle_inline_box_chart(prompt, product, max_rows)
        if box_chart_out.get("handled"):
            return _augment_dashboard_tool(box_chart_out, prompt, product=product, username=username)
        et_trend_chart_out = _handle_et_trend_chart(prompt, product, max_rows)
        if et_trend_chart_out.get("handled"):
            return _augment_dashboard_tool(et_trend_chart_out, prompt, product=product, username=username)
        vm_trend_chart_out = _handle_vm_trend_chart(prompt, product, max_rows)
        if vm_trend_chart_out.get("handled"):
            return _augment_dashboard_tool(vm_trend_chart_out, prompt, product=product, username=username)
        trend_chart_out = _handle_inline_trend_chart(prompt, product, max_rows)
        if trend_chart_out.get("handled"):
            return _augment_dashboard_tool(trend_chart_out, prompt, product=product, username=username)
        grouped_chart_out = _handle_grouped_metric_chart(prompt, product, max_rows)
        if grouped_chart_out.get("handled"):
            return _augment_dashboard_tool(grouped_chart_out, prompt, product=product, username=username)
        generic_chart_out = _handle_dashboard_generic_chart(prompt, product, max_rows)
        if generic_chart_out.get("handled"):
            return _augment_dashboard_tool(generic_chart_out, prompt, product=product, username=username)
        chart_out = _handle_chart_request(prompt, product, max_rows)
        if chart_out.get("handled"):
            return _augment_dashboard_tool(chart_out, prompt, product=product, username=username)
    if (allowed_keys is None or "diagnosis" in allowed_keys) and defer_diagnosis_for_source_chart:
        diag_out = _handle_semiconductor_diagnosis_query(prompt, product, max_rows)
        if diag_out.get("handled"):
            return diag_out
    if allowed_keys is None or "splittable" in allowed_keys:
        fastest_out = _handle_fastest_knob_query(prompt, product, max_rows)
        if fastest_out.get("handled"):
            return fastest_out
    if allowed_keys is None or "filebrowser" in allowed_keys:
        sql_draft_out = _handle_filebrowser_sql_llm_draft(prompt, product, max_rows, username=username)
        if sql_draft_out.get("handled"):
            return sql_draft_out
        preview_out = _handle_filebrowser_data_preview(prompt, product, max_rows)
        if preview_out.get("handled"):
            return preview_out
        schema_out = _handle_filebrowser_schema_search(prompt, product, max_rows)
        if schema_out.get("handled"):
            return schema_out
    if allowed_keys is None or "filebrowser" in allowed_keys or "splittable" in allowed_keys:
        table_out = _handle_value_table_query(prompt, product, max_rows)
        if table_out.get("handled"):
            return table_out
    pre_matches = _matched_feature_entrypoints(prompt, limit=3, allowed_keys=allowed_keys)
    if pre_matches and pre_matches[0].get("key") not in {"splittable"}:
        return _unit_feature_guidance(prompt, product, max_rows=max_rows, allowed_keys=allowed_keys)
    for handler in (_handle_knob_query,):
        if allowed_keys is not None and "splittable" not in allowed_keys:
            continue
        out = handler(prompt, product, max_rows)
        if out.get("handled"):
            return out
    routed = _unit_feature_guidance(prompt, product, max_rows=max_rows, allowed_keys=allowed_keys)
    if routed.get("feature_entrypoints"):
        return routed
    return {
        "handled": False,
        "intent": "general",
        "answer": (
            "Flowi local tools는 현재 파일 탐색, 대시보드, SplitTable, Inform, Meeting, Tracker 흐름을 우선 지원합니다.\n"
            "예: `A1000 knob 어떻게돼`, `lot_id가 A1000인 행 보여줘`"
        ),
    }


def _clean_source_ai(raw: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(raw or "").strip())
    return text.strip("._:-")[:64] or "external"


def _json_excerpt(value: Any, limit: int = 4000) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)[:limit]
    except Exception:
        return str(value or "")[:limit]


def _flowi_plain_answer_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"(?m)^\s*[-*]{3,}\s*$", "", text)
    return text.strip()


FLOWI_ACTION_LOG_DISCLAIMER = "내부 추론 원문이 아니라 검증 가능한 실행 요약입니다."


def _flowi_clean_public_summary_line(value: Any) -> str:
    text = _flowi_plain_answer_text(value)
    text = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", text).strip()
    text = re.sub(r"\s+", " ", text)
    return text[:240]


def _flowi_parse_public_polish_text(raw: Any) -> dict[str, Any]:
    text = _flowi_plain_answer_text(raw)
    if not text:
        return {"summary": [], "final_answer": ""}
    summary_match = re.search(r"\[생각요약\]\s*(.*?)(?=\n\s*\[최종답변\]|\Z)", text, flags=re.S)
    final_match = re.search(r"\[최종답변\]\s*(.*)\Z", text, flags=re.S)
    if not final_match:
        return {"summary": [], "final_answer": ""}
    summary_raw = summary_match.group(1) if summary_match else ""
    final_answer = _flowi_plain_answer_text(final_match.group(1))
    if not final_answer or len(final_answer) > 1000:
        return {"summary": [], "final_answer": ""}
    summary = [
        line
        for line in (_flowi_clean_public_summary_line(line) for line in summary_raw.splitlines())
        if line and not re.search(r"chain[-_ ]?of[-_ ]?thought|내부 추론 원문|hidden reasoning", line, flags=re.I)
    ][:6]
    return {"summary": summary, "final_answer": final_answer}


def _flowi_llm_polish_payload(tool: dict[str, Any]) -> dict[str, Any]:
    slots: dict[str, Any] = {}
    for src_key in ("arguments", "slots", "filters"):
        src = tool.get(src_key) if isinstance(tool.get(src_key), dict) else {}
        for key, value in src.items():
            if value not in (None, "", [], {}) and key not in slots:
                slots[key] = value
    table = tool.get("table") if isinstance(tool.get("table"), dict) else {}
    chart = tool.get("chart_result") if isinstance(tool.get("chart_result"), dict) else (tool.get("chart") if isinstance(tool.get("chart"), dict) else {})
    result_summary = {
        "answer": _flowi_plain_answer_text(tool.get("answer") or "")[:1200],
        "table_kind": table.get("kind") or "",
        "table_rows": table.get("total", len(table.get("rows") or [])) if table else 0,
        "chart_kind": chart.get("kind") or chart.get("status") or "",
        "missing": tool.get("missing") or [],
        "waiting_for": _flowi_waiting_for(tool),
    }
    source = (tool.get("filters") or {}).get("source") if isinstance(tool.get("filters"), dict) else ""
    return {
        "slots": {k: slots[k] for k in list(slots)[:16]},
        "feature": tool.get("feature") or "",
        "source": source or tool.get("intent") or "",
        "result_summary": result_summary,
    }


def _flowi_llm_polish_prompt(prompt: str, tool: dict[str, Any]) -> str:
    payload = _flowi_llm_polish_payload(tool)
    return (
        "아래 Flow 서버의 deterministic 해석/실행 결과를 사용자에게 짧은 한국어 공개 요약으로만 정리하세요.\n"
        "라우팅, 권한, product, lot, wafer, step, plan 값은 새로 추론하지 마세요.\n"
        "입력 JSON에 있는 slots, feature, source, result_summary만 사용하세요.\n"
        "이전 context JSON은 보강 요청 해석에만 사용하고, tool/cache 결과에 없는 값은 만들지 마세요.\n"
        "raw reasoning, chain-of-thought, 숨겨진 사고과정 원문은 절대 쓰지 마세요.\n"
        "출력은 반드시 아래 형식만 사용하세요. markdown 표, JSON, 내부 schema id는 쓰지 마세요.\n"
        "[생각요약]\n"
        "- 선택한 intent/action, 사용 근거, 검증 결과만 3-6줄로 씁니다.\n"
        "[최종답변]\n"
        "사용자에게 보여줄 최종 답변만 씁니다.\n\n"
        f"사용자 질문: {str(prompt or '').strip()[:1000]}\n"
        f"입력 JSON: {json.dumps(payload, ensure_ascii=False, default=str)[:5000]}"
    )


def _flowi_validate_llm_polish_text(raw: Any) -> str:
    text = _flowi_plain_answer_text(raw)
    if not text:
        return ""
    if len(text) > 800:
        return ""
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) > 6:
        return ""
    if re.search(r"```|^\s*[-*]\s|\{|\}|\[|\]", text, flags=re.M):
        return ""
    if re.search(r"\b(intent|action|schema_id|function_call|tool_call|trace|chain[-_ ]?of[-_ ]?thought)\b", text, flags=re.I):
        return ""
    return text


def _flowi_agent_actions(tool: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    entries = tool.get("feature_entrypoints") or []
    if isinstance(entries, list):
        for item in entries[:3]:
            if not isinstance(item, dict) or not item.get("key"):
                continue
            actions.append({
                "type": "open_tab",
                "tab": item.get("key"),
                "title": item.get("title") or item.get("key"),
                "description": item.get("description") or "",
            })
    unit_action = tool.get("action")
    if unit_action:
        contract_action = _flowi_driver_contract_action(
            str(unit_action or ""),
            str(tool.get("intent") or ""),
            str(tool.get("feature") or ""),
        )
        actions.append({
            "type": "flowi_unit_action",
            "action": unit_action,
            "unit_action": contract_action,
            "intent": tool.get("intent") or "",
            "slots": tool.get("slots") or {},
            "filters": tool.get("filters") or {},
        })
        if contract_action and contract_action != unit_action:
            actions.append({
                "type": "agent_driver_action",
                "action": contract_action,
                "handler_action": unit_action,
                "intent": tool.get("intent") or "",
                "slots": tool.get("slots") or {},
                "filters": tool.get("filters") or {},
            })
    return actions


def _flowi_output_summary(tool: dict[str, Any]) -> dict[str, Any]:
    table = tool.get("table") if isinstance(tool.get("table"), dict) else {}
    chart = tool.get("chart_result") if isinstance(tool.get("chart_result"), dict) else (tool.get("chart") if isinstance(tool.get("chart"), dict) else {})
    blocks = tool.get("blocks") if isinstance(tool.get("blocks"), list) else []
    aux_tables = []
    for key, value in tool.items():
        if key == "table" or not key.endswith("_table") or not isinstance(value, dict):
            continue
        aux_tables.append({
            "key": key,
            "kind": value.get("kind") or "",
            "total": value.get("total", len(value.get("rows") or [])),
        })
    return {
        "table": {
            "kind": table.get("kind") or "",
            "total": table.get("total", len(table.get("rows") or [])) if table else 0,
            "title": table.get("title") or "",
        } if table else {},
        "chart": {
            "kind": chart.get("kind") or chart.get("status") or "",
            "status": chart.get("status") or "",
            "title": chart.get("title") or "",
        } if chart else {},
        "aux_tables": aux_tables[:4],
        "blocks": [
            {
                "kind": block.get("kind") or "",
                "title": block.get("title") or "",
            }
            for block in blocks[:8]
            if isinstance(block, dict)
        ],
        "has_rows": bool(tool.get("rows")),
        "has_knobs": bool(tool.get("knobs")),
    }


def _flowi_waiting_for(tool: dict[str, Any]) -> str:
    if tool.get("blocked"):
        return "permission_or_policy"
    if (
        tool.get("missing")
        or (tool.get("validation") or {}).get("missing")
        or tool.get("arguments_choices")
        or tool.get("missing_freetext")
    ):
        return "required_fields"
    if tool.get("requires_confirmation"):
        return "user_confirmation"
    clarification = tool.get("clarification") if isinstance(tool.get("clarification"), dict) else {}
    if clarification.get("choices"):
        return "user_choice"
    if not tool.get("handled"):
        return "more_context"
    return ""


def _flowi_workflow_status(tool: dict[str, Any]) -> str:
    if tool.get("blocked"):
        return "blocked"
    waiting = _flowi_waiting_for(tool)
    if waiting == "required_fields":
        return "awaiting_fields"
    if waiting == "user_confirmation":
        return "awaiting_confirmation"
    if waiting == "user_choice":
        return "awaiting_choice"
    if waiting == "more_context":
        return "needs_more_context"
    return "ready"


def _flowi_next_actions(tool: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    clarification = tool.get("clarification") if isinstance(tool.get("clarification"), dict) else {}
    choices = clarification.get("choices") if isinstance(clarification.get("choices"), list) else []
    for i, choice in enumerate(choices[:3]):
        if not isinstance(choice, dict):
            continue
        actions.append({
            "type": "respond_with_prompt",
            "id": choice.get("id") or f"choice_{i + 1}",
            "label": choice.get("label") or str(i + 1),
            "title": choice.get("title") or choice.get("label") or f"선택 {i + 1}",
            "description": choice.get("description") or "",
            "prompt": choice.get("prompt") or choice.get("title") or "",
            "recommended": bool(choice.get("recommended")),
            "requires_user": True,
        })
    if tool.get("requires_confirmation") and not choices:
        actions.append({
            "type": "confirm_required",
            "id": "confirm",
            "title": "확인 필요",
            "description": "실제 저장/변경 전에 전용 확인 플로우가 필요합니다.",
            "requires_user": True,
        })
    entries = tool.get("feature_entrypoints") if isinstance(tool.get("feature_entrypoints"), list) else []
    for entry in entries[:3]:
        if not isinstance(entry, dict) or not entry.get("key"):
            continue
        actions.append({
            "type": "open_tab",
            "id": f"open_{entry.get('key')}",
            "tab": entry.get("key"),
            "title": f"{entry.get('title') or entry.get('key')} 열기",
            "description": entry.get("description") or "",
            "requires_user": False,
        })
    if isinstance(tool.get("table"), dict) and (tool.get("table") or {}).get("rows"):
        actions.append({
            "type": "inspect_table",
            "id": "inspect_table",
            "title": "표 확인",
            "description": f"{(tool.get('table') or {}).get('kind') or 'result'} 결과를 홈 화면에서 확인합니다.",
            "requires_user": False,
        })
    blocks = tool.get("blocks") if isinstance(tool.get("blocks"), list) else []
    if blocks:
        block_kinds = [str(b.get("kind") or "") for b in blocks if isinstance(b, dict)]
        if any(kind == "lot_table" for kind in block_kinds):
            actions.append({
                "type": "inspect_table",
                "id": "inspect_composite_table",
                "title": "복합 표 확인",
                "description": "복합 분석의 lot/wafer 표 블록을 확인합니다.",
                "requires_user": False,
            })
        if any(kind.startswith("chart_") for kind in block_kinds):
            actions.append({
                "type": "render_chart",
                "id": "render_composite_charts",
                "title": "복합 차트 확인",
                "description": "복합 분석의 산점도/추세 블록을 렌더링합니다.",
                "requires_user": False,
            })
            actions.append({
                "type": "save_as_dashboard_chart",
                "id": "save_dashboard_chart",
                "title": "Dashboard 차트 저장",
                "description": "표시된 차트 설정을 Dashboard 저장 flow로 이어갑니다.",
                "requires_user": True,
            })
    if isinstance(tool.get("samples_table"), dict) and (tool.get("samples_table") or {}).get("rows"):
        actions.append({
            "type": "inspect_aux_table",
            "id": "inspect_samples",
            "title": "근거 sample 확인",
            "description": "ETA/집계 계산에 사용된 sample table을 확인합니다.",
            "requires_user": False,
        })
    if isinstance(tool.get("chart_result"), dict) or isinstance(tool.get("chart"), dict):
        actions.append({
            "type": "render_chart",
            "id": "render_chart",
            "title": "차트 확인",
            "description": "홈 Flow-i 기본 차트 preset으로 렌더링합니다.",
            "requires_user": False,
        })
    if not actions and not tool.get("blocked"):
        actions.append({
            "type": "follow_up_prompt",
            "id": "follow_up",
            "title": "후속 조건 입력",
            "description": "product, lot, wafer, step, item 중 빠진 조건을 추가해 이어서 질문합니다.",
            "requires_user": True,
        })
    return actions[:8]


def _limit_flowi_choices(tool: dict[str, Any], limit: int = 3) -> dict[str, Any]:
    clarification = tool.get("clarification") if isinstance(tool.get("clarification"), dict) else {}
    choices = clarification.get("choices") if isinstance(clarification.get("choices"), list) else []
    if len(choices) <= limit:
        return tool
    trimmed = choices[:max(1, int(limit or 3))]
    clarification = dict(clarification)
    clarification["choices"] = trimmed
    tool["clarification"] = clarification
    return tool


def _flowi_workflow_state(
    tool: dict[str, Any],
    *,
    prompt: str,
    allowed_keys: set[str],
    agent_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    messages = []
    if isinstance(agent_context, dict) and isinstance(agent_context.get("messages"), list):
        messages = agent_context.get("messages") or []
    clarification = tool.get("clarification") if isinstance(tool.get("clarification"), dict) else {}
    choices = clarification.get("choices") if isinstance(clarification.get("choices"), list) else []
    return {
        "version": 1,
        "surface": "home_flowi",
        "status": _flowi_workflow_status(tool),
        "waiting_for": _flowi_waiting_for(tool),
        "intent": tool.get("intent") or "general",
        "action": tool.get("action") or "",
        "feature": tool.get("feature") or "",
        "requires_confirmation": bool(tool.get("requires_confirmation")),
        "blocked": bool(tool.get("blocked")),
        "last_prompt": str(prompt or "")[:500],
        "allowed_features": sorted(allowed_keys),
        "slots": tool.get("slots") if isinstance(tool.get("slots"), dict) else {},
        "filters": tool.get("filters") if isinstance(tool.get("filters"), dict) else {},
        "outputs": _flowi_output_summary(tool),
        "choice_count": len(choices),
        "context_message_count": len(messages),
    }


def _finalize_flowi_tool(
    tool: dict[str, Any],
    *,
    prompt: str,
    allowed_keys: set[str],
    agent_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(tool, dict):
        return tool
    _flowi_set_inline_type(tool)
    _limit_flowi_choices(tool, 3)
    attach_term_knowledge(prompt, tool)
    tool["workflow_state"] = _flowi_workflow_state(tool, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)
    tool["next_actions"] = _flowi_next_actions(tool)
    return tool


def _agent_api_meta(
    *,
    source: str,
    client_run_id: str,
    username: str,
    tool: dict[str, Any],
    agent_context: dict[str, Any],
) -> dict[str, Any]:
    return {
        "received": True,
        "source_ai": source,
        "client_run_id": client_run_id,
        "auth_user": username,
        "read_only": True,
        "actions": _flowi_agent_actions(tool),
        "workflow_state": tool.get("workflow_state") if isinstance(tool.get("workflow_state"), dict) else {},
        "next_actions": tool.get("next_actions") if isinstance(tool.get("next_actions"), list) else [],
        "requires_confirmation": bool(tool.get("requires_confirmation")),
        "clarification": tool.get("clarification") if isinstance(tool.get("clarification"), dict) else {},
        "context_keys": sorted(str(k) for k in agent_context.keys())[:20],
    }


def _event_fields(fields: dict[str, Any], *, source: str = "", client_run_id: str = "") -> dict[str, Any]:
    out = dict(fields)
    if source:
        out["source_ai"] = source
    if client_run_id:
        out["client_run_id"] = client_run_id
    return out


def _flowi_tool_retrieved_ids(tool: dict[str, Any]) -> list[str]:
    ids: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in ids:
            ids.append(text[:160])

    if not isinstance(tool, dict):
        return ids
    diagnosis = tool.get("diagnosis") if isinstance(tool.get("diagnosis"), dict) else {}
    for hyp in diagnosis.get("ranked_hypotheses") or []:
        if isinstance(hyp, dict):
            add(hyp.get("knowledge_card_id"))
    for card in diagnosis.get("knowledge_cards") or []:
        if isinstance(card, dict):
            add(card.get("id") or card.get("knowledge_card_id"))
    for case in diagnosis.get("similar_cases") or []:
        if isinstance(case, dict):
            add(case.get("case_id") or case.get("id"))
    for row in tool.get("retrieved_knowledge") or []:
        if isinstance(row, dict):
            add(row.get("id") or row.get("knowledge_id"))
        else:
            add(row)
    return ids[:30]


def _flowi_tool_retrieval_score(tool: dict[str, Any]) -> float | None:
    if not isinstance(tool, dict):
        return None
    scores: list[float] = []
    diagnosis = tool.get("diagnosis") if isinstance(tool.get("diagnosis"), dict) else {}
    for hyp in diagnosis.get("ranked_hypotheses") or []:
        if not isinstance(hyp, dict):
            continue
        for key in ("score", "confidence"):
            try:
                val = float(hyp.get(key))
            except Exception:
                continue
            scores.append(val)
            break
    for row in tool.get("retrieved_knowledge") or []:
        if not isinstance(row, dict):
            continue
        try:
            scores.append(float(row.get("score")))
        except Exception:
            continue
    return max(scores) if scores else None


def _flowi_result_status(tool: dict[str, Any], llm_info: dict[str, Any] | None = None) -> str:
    if isinstance(tool, dict) and tool.get("blocked"):
        return "error"
    if isinstance(llm_info, dict) and llm_info.get("error"):
        return "error"
    if isinstance(tool, dict) and (
        tool.get("missing")
        or (tool.get("validation") or {}).get("missing")
        or tool.get("arguments_choices")
        or tool.get("missing_freetext")
    ):
        return "missing"
    if isinstance(tool, dict) and ((tool.get("clarification") or {}).get("choices")):
        return "missing"
    return "success"


def _flowi_trace_result_endpoint(result: dict[str, Any]) -> str:
    return "/api/llm/flowi/agent/chat" if isinstance(result.get("agent_api"), dict) else "/api/llm/flowi/chat"


def _flowi_trace_status(tool: dict[str, Any]) -> str:
    status = _flowi_workflow_status(tool)
    if status == "blocked":
        return "blocked"
    if status.startswith("awaiting") or status.startswith("needs"):
        return "waiting"
    return "done"


def _flowi_activation_status(tool: dict[str, Any], result: dict[str, Any] | None = None) -> str:
    if tool.get("blocked"):
        return "blocked"
    llm = (result or {}).get("llm") if isinstance((result or {}).get("llm"), dict) else {}
    if llm.get("error") and not tool.get("handled"):
        return "error"
    waiting = _flowi_waiting_for(tool)
    if waiting == "required_fields":
        return "needs_input"
    if waiting == "user_confirmation":
        return "awaiting_confirmation"
    if waiting in {"user_choice", "more_context"}:
        return "needs_input"
    return "done" if tool.get("handled", True) else "ready"


def _flowi_trace_missing_slots(tool: dict[str, Any]) -> list[str]:
    out: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text[:80])

    for value in tool.get("missing") or []:
        add(value)
    validation = tool.get("validation") if isinstance(tool.get("validation"), dict) else {}
    for value in validation.get("missing") or []:
        add(value)
    for item in tool.get("missing_freetext") or []:
        if isinstance(item, dict):
            add(item.get("field") or item.get("key"))
        else:
            add(item)
    for item in tool.get("arguments_choices") or []:
        if isinstance(item, dict):
            add(item.get("field") or item.get("key"))
    return out[:12]


def _flowi_trace_term_resolution(tool: dict[str, Any], knowledge_terms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw_items = tool.get("term_resolution") if isinstance(tool.get("term_resolution"), list) else []
    refs_by_term: dict[str, list[str]] = {}
    for row in knowledge_terms:
        if not isinstance(row, dict):
            continue
        term = str(row.get("term") or "").strip().upper()
        ref = str(row.get("id") or row.get("title") or "").strip()
        if term and ref:
            refs_by_term.setdefault(term, []).append(ref)
        column = str(row.get("column") or "").strip().upper()
        if column and ref:
            refs_by_term.setdefault(column, []).append(ref)
    out: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        token = str(item.get("token") or item.get("term") or "").strip()
        if not token:
            continue
        refs = [str(ref) for ref in (item.get("wiki_refs") or []) if str(ref or "").strip()]
        token_u = token.upper()
        refs.extend(refs_by_term.get(token_u, []))
        for key, values in refs_by_term.items():
            if key and (key in token_u or token_u in key):
                refs.extend(values)
        out.append({
            "token": token[:120],
            "meaning": str(item.get("meaning") or "")[:240],
            "wiki_refs": list(dict.fromkeys(refs))[:8],
            "query_filter": str(item.get("query_filter") or "")[:500],
            "status": str(item.get("status") or "resolved")[:40],
        })
    return out[:12]


def _flowi_activation_payload_summary(tool: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for src_key in ("arguments", "filters", "slots"):
        src = tool.get(src_key) if isinstance(tool.get(src_key), dict) else {}
        for key in ("product", "root_lot_id", "root_lot_ids", "fab_lot_id", "fab_lot_ids", "wafer_id", "wafer_ids", "module", "step", "source_type", "target"):
            value = src.get(key)
            if value not in (None, "", [], {}):
                payload[key] = value
    return payload


def _flowi_next_action_label(result: dict[str, Any], tool: dict[str, Any]) -> str:
    next_actions = result.get("next_actions") if isinstance(result.get("next_actions"), list) else []
    if next_actions:
        first = next_actions[0]
        if isinstance(first, dict):
            return str(first.get("title") or first.get("id") or "next action")
    waiting = _flowi_waiting_for(tool)
    if waiting:
        return waiting
    return "show_answer"


def _flowi_trace_output_label(tool: dict[str, Any]) -> str:
    output = _flowi_output_summary(tool)
    parts: list[str] = []
    table = output.get("table") if isinstance(output.get("table"), dict) else {}
    chart = output.get("chart") if isinstance(output.get("chart"), dict) else {}
    if table:
        parts.append(f"{table.get('kind') or 'table'} {table.get('total', 0)} rows")
    if chart:
        parts.append(chart.get("title") or chart.get("kind") or "chart")
    if output.get("has_rows"):
        rows = tool.get("rows") if isinstance(tool.get("rows"), list) else []
        parts.append(f"rows {len(rows)}")
    if output.get("has_knobs"):
        knobs = tool.get("knobs") if isinstance(tool.get("knobs"), list) else []
        parts.append(f"knobs {len(knobs)}")
    if not parts and tool.get("answer"):
        parts.append("answer text")
    return ", ".join(parts) or "pending result"


def _flowi_trace_feature_api_calls(tool: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(tool, dict) or tool.get("blocked"):
        return []
    action = str(tool.get("action") or "")
    intent = str(tool.get("intent") or "")
    feature = str(tool.get("feature") or "")
    status = _flowi_trace_status(tool)
    output = _flowi_trace_output_label(tool)
    filters = tool.get("filters") if isinstance(tool.get("filters"), dict) else {}
    slots = tool.get("slots") if isinstance(tool.get("slots"), dict) else {}
    args = tool.get("arguments") if isinstance(tool.get("arguments"), dict) else {}
    calls: list[dict[str, Any]] = []

    def add(
        *,
        name: str,
        method: str = "internal",
        path: str = "",
        callee: str = "",
        purpose: str = "",
        payload: dict[str, Any] | None = None,
        output_label: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        call = {
            "stage": "feature_api",
            "feature": feature or "flowi",
            "action": action or intent,
            "name": name,
            "method": method,
            "path": path,
            "callee": callee,
            "purpose": purpose,
            "payload": payload or {},
            "output": output_label or output,
            "status": status,
        }
        if metadata:
            call["metadata"] = metadata
        calls.append(call)

    if action in {"query_splittable_view", "query_wafer_split_at_step"} or intent in {"splittable_view", "wafer_split_at_step"}:
        split_api = tool.get("split_api") if isinstance(tool.get("split_api"), dict) else {}
        runtime_profile = tool.get("runtime_profile") if isinstance(tool.get("runtime_profile"), dict) else {}
        view_cache = tool.get("view_cache") if isinstance(tool.get("view_cache"), dict) else {}
        elapsed_ms = tool.get("elapsed_ms")
        meta = {
            "elapsed_ms": elapsed_ms,
            "runtime_profile": runtime_profile,
            "view_cache": view_cache,
        }
        meta = {k: v for k, v in meta.items() if v not in (None, "", [], {})}
        add(
            name="SplitTable view",
            method=str(split_api.get("method") or "GET"),
            path=str(split_api.get("path") or "/api/splittable/view"),
            callee=str(split_api.get("callee") or "routers.splittable.view_split"),
            purpose="product/root/wafer/step/prefix 조건으로 split table row를 조회",
            payload={k: args.get(k) or filters.get(k) or slots.get(k) for k in ("product", "root_lot_id", "fab_lot_id", "wafer_id", "step", "prefix") if (args.get(k) or filters.get(k) or slots.get(k))},
            metadata=meta,
        )
    elif action == "knowledge.impact_context.lookup" or intent == "knowledge_impact_context":
        add(
            name="Impact context lookup",
            method="GET",
            path="/api/knowledge/impact-context",
            callee="core.knowledge_impact.impact_context",
            purpose="검증된 Agent Wiki 문서와 raw KnowledgeEvent 후보를 함께 조회",
            payload={k: filters.get(k) for k in ("product", "root_lot_id", "step_id", "item_id", "knob") if filters.get(k)},
        )
    elif action == "filebrowser.sql.llm.draft" or intent == "filebrowser_sql_llm_draft":
        sql_draft = tool.get("sql_draft") if isinstance(tool.get("sql_draft"), dict) else {}
        add(
            name="FileBrowser AI SQL draft",
            method="POST",
            path="/api/filebrowser/sql/llm/draft",
            callee="routers.filebrowser.filebrowser_sql_llm_draft",
            purpose="자연어 조건을 read-only SQL filter와 선택 컬럼으로 정형화하고 preview를 검증",
            payload={
                "source_type": filters.get("source_type") or args.get("source_type"),
                "product": filters.get("product") or args.get("product"),
                "sql": sql_draft.get("sql") or filters.get("sql") or "",
                "selected_columns": sql_draft.get("selected_columns") or filters.get("selected_columns") or [],
            },
        )
    elif action in {"filebrowser.multisource.preview", "dashboard.chart.llm.draft"} or intent in {"filebrowser_multisource_join", "dashboard_multisource_chart"}:
        add(
            name="Confirmed schema multi-source query",
            method="internal",
            path="data/flow-data/schema_relations.json + DB/base files",
            callee="core.flowi_multisource.execute_multisource_request",
            purpose="schema_doc/column_catalog로 용어를 해석하고 confirmed schema relation으로만 filter/join/chart draft 실행",
            payload={
                "source_ids": tool.get("source_ids") or [],
                "relation_ids": tool.get("relation_ids") or [],
                "join_keys": tool.get("join_keys") or [],
                "filters": filters,
                "selected_columns": tool.get("selected_columns") or [],
                "sql_plan": tool.get("sql_plan") or "",
            },
        )
    elif action == "query_lot_current_step_from_progress_cache" or intent == "lot_current_step_lookup":
        add(
            name="Latest progress cache",
            method="internal",
            path="data/Fab/cache/lot_progress_latest_lot_by_root_wafer.parquet",
            callee="core.lot_progress_cache.lot_progress_snapshot",
            purpose="root_lot_id와 wafer_id로 최신 step_id/function_step 조회",
            payload={k: (args.get(k) or slots.get(k)) for k in ("product", "root_lot_ids", "wafer_ids", "lot_wf_ids") if (args.get(k) or slots.get(k))},
        )
    elif action == "query_lot_knobs_from_ml_table" or intent == "lot_knobs":
        add(
            name="ML_TABLE root lot lookup",
            method="POST",
            path="/api/filebrowser/ml-table/lookup",
            callee="core.ml_table_lookup.query_root_lot",
            purpose="root_lot_id 기준 lookup cache에서 선택 컬럼만 조회",
            payload={k: args.get(k) or filters.get(k) or slots.get(k) for k in ("product", "root_lot_ids", "wafer_ids", "step", "group", "cache_status") if (args.get(k) or filters.get(k) or slots.get(k))},
        )
    elif action == "find_lots_by_knob_value" or intent == "knob_value_lot_search":
        add(
            name="ML_TABLE KNOB search",
            method="internal",
            path="data/Fab/ML_TABLE_<product>.parquet",
            callee="_handle_find_lots_by_knob_value",
            purpose="ML_TABLE에서 KNOB 값과 step 조건에 맞는 lot_wf 후보 검색",
            payload={k: args.get(k) or filters.get(k) for k in ("product", "step", "knob_value", "sort") if (args.get(k) or filters.get(k))},
        )
        add(
            name="Progress join",
            method="internal",
            path="data/Fab/cache/lot_progress_latest_lot_by_root_wafer.parquet",
            callee="_flowi_progress_for_lot_rows",
            purpose="검색된 lot_wf 후보를 최신 FAB 진행 step과 연결",
            payload={"join_key": "lot_wf"},
        )
    elif action == "query_step_mapping_lookup" or intent == "step_mapping_lookup":
        add(
            name="Step matching lookup",
            method="internal",
            path="registered matching/rulebook source or data/Fab/Vehicle_matching.csv + step_matching.csv + ppid_knob.csv",
            callee="_handle_step_mapping_lookup",
            purpose="승인 등록된 matching/rulebook catalog 또는 fallback CSV에서 step_id와 function_step/step_desc 연결 근거 조회",
            payload={
                "source_ids": tool.get("source_ids") or [],
                "product": filters.get("product") or "",
                "step_id_terms": filters.get("step_id_terms") or [],
                "query_terms": filters.get("query_terms") or [],
                "function_steps": filters.get("function_steps") or [],
                "row_count": filters.get("row_count") or 0,
            },
        )
    elif action == "query_knob_rulebook_rows" or intent == "knob_rulebook_lookup":
        add(
            name="KNOB rulebook lookup",
            method="internal",
            path="registered rulebook source or data/Fab/ppid_knob.csv + step_matching.csv",
            callee="_handle_knob_rulebook_lookup",
            purpose="승인 등록된 rulebook/matching catalog 또는 fallback CSV에서 KNOB rule 행과 step_id 확장 근거 조회",
            payload={
                "source_ids": tool.get("source_ids") or [],
                "product": filters.get("product") or "",
                "step_terms": filters.get("step_terms") or [],
                "ppid": filters.get("ppid") or [],
                "row_count": filters.get("row_count") or 0,
            },
        )
    elif action == "query_tracker_lot_purpose" or intent == "tracker_lot_purpose_lookup":
        add(
            name="Tracker issue load",
            method="GET",
            path="/api/tracker/issues",
            callee="routers.tracker._load",
            purpose="issue lots에서 lot_id/fab_lot_id 목적 purpose 조회",
            payload={k: (args.get(k) or slots.get(k)) for k in ("root_lot_ids", "fab_lot_ids", "lot_ids") if (args.get(k) or slots.get(k))},
        )
    elif action == "register_inform_log" or intent.startswith("inform_log"):
        add(
            name="Inform draft",
            method="internal",
            path="/api/informs",
            callee="_handle_flowi_register_inform_log",
            purpose="module/content/recipient/lot scope를 검증하고 저장 전 draft 또는 confirmation 생성",
            payload={k: args.get(k) for k in ("product", "root_lot_ids", "fab_lot_ids", "wafer_ids", "module", "split_set", "recipients") if args.get(k)},
        )
    elif action == "build_dashboard_metric_chart" or intent.startswith("dashboard_"):
        add(
            name="Dashboard chart draft",
            method="internal",
            path="dashboard chart draft/session",
            callee="_augment_dashboard_tool",
            purpose="차트 draft/config를 만들고 Home inline preview용 data/session을 구성",
            payload={k: tool.get(k) for k in ("chart_type", "chart_session_id") if tool.get(k)} | {"config": tool.get("chart_config") or tool.get("config") or {}},
        )
    elif action == "query_meeting_calendar_records" or intent == "meeting_recall_summary":
        add(
            name="Meeting ask summary",
            method="POST",
            path="/api/meetings/ask",
            callee="_handle_meeting_recall",
            purpose="회의/차수/아젠다/회의록/결정사항/액션아이템 저장 기록을 read-only 요약",
            payload={k: (slots.get(k) or filters.get(k)) for k in ("meeting_id", "meeting_title", "session_id", "session_idx") if (slots.get(k) or filters.get(k))},
        )
    elif action in {"query_current_fab_lot", "query_current_fab_lot_from_fab_db"} or intent == "current_fab_lot_lookup":
        add(
            name="FAB current lot lookup",
            method="internal",
            path="data/Fab",
            callee="_handle_current_fab_lot_lookup",
            purpose="FAB snapshot에서 현재 fab_lot_id를 조회",
            payload={k: (args.get(k) or slots.get(k)) for k in ("product", "root_lot_ids", "fab_lot_ids", "lot_ids", "wafer_ids") if (args.get(k) or slots.get(k))},
        )
    elif feature:
        add(
            name=f"{feature} handler",
            callee=action or intent,
            purpose="선택된 feature handler가 local result를 생성",
            payload={"intent": intent, "action": action},
        )
    return calls[:4]


def _flowi_trace_api_calls(
    *,
    result: dict[str, Any],
    tool: dict[str, Any],
    prompt: str,
    allowed_keys: set[str],
) -> list[dict[str, Any]]:
    endpoint = _flowi_trace_result_endpoint(result)
    status = _flowi_trace_status(tool)
    calls: list[dict[str, Any]] = [
        {
            "stage": "ingress",
            "feature": "llm",
            "action": "flowi_agent_chat" if endpoint.endswith("/agent/chat") else "flowi_chat",
            "name": "Flow-i FastAPI endpoint",
            "method": "POST",
            "path": endpoint,
            "callee": "flowi_agent_chat" if endpoint.endswith("/agent/chat") else "flowi_chat",
            "purpose": "prompt, product, context를 받아 인증된 Flow-i 실행 요청으로 변환",
            "payload": {"prompt_chars": len(prompt or ""), "allowed_features": len(allowed_keys)},
            "output": "request accepted",
            "status": "done",
        },
        {
            "stage": "orchestrator",
            "feature": "flowi",
            "action": tool.get("action") or tool.get("intent") or "",
            "name": "Flow-i orchestrator",
            "method": "internal",
            "path": "backend/routers/llm.py",
            "callee": "_run_flowi_chat",
            "purpose": "권한, slot, intent, feature handler, LLM polish 여부를 결정",
            "payload": {
                "intent": tool.get("intent") or "",
                "feature": tool.get("feature") or "",
                "missing": tool.get("missing") or [],
            },
            "output": f"workflow={_flowi_workflow_status(tool)}",
            "status": status,
        },
    ]
    retrieved_knowledge = tool.get("retrieved_knowledge") if isinstance(tool.get("retrieved_knowledge"), list) else []
    if retrieved_knowledge:
        terms = []
        ids = []
        for row in retrieved_knowledge[:12]:
            if not isinstance(row, dict):
                continue
            term = str(row.get("term") or "").strip()
            doc_id = str(row.get("id") or row.get("doc_id") or row.get("knowledge_id") or "").strip()
            if term and term not in terms:
                terms.append(term)
            if doc_id and doc_id not in ids:
                ids.append(doc_id)
        calls.append({
            "stage": "knowledge",
            "feature": "knowledge",
            "action": "lookup_term",
            "name": "Agent Wiki / schema lookup",
            "method": "internal",
            "path": "data/flow-data/knowledge + schema_relations.json",
            "callee": "core.knowledge_vault.lookup_term",
            "purpose": "질문 용어를 Agent Wiki schema_doc, column_catalog, promoted knowledge와 대조",
            "payload": {"terms": terms[:8]},
            "output": ", ".join(ids[:5]) or f"{len(retrieved_knowledge)} knowledge hits",
            "status": "done",
        })
    calls.extend(_flowi_trace_feature_api_calls(tool))
    calls.append({
        "stage": "response",
        "feature": "flowi",
        "action": "render_response",
        "name": "Answer composer",
        "method": "internal",
        "path": "Flow-i response payload",
        "callee": "_attach_flowi_trace",
        "purpose": "tool result, clarification, next actions, trace를 화면 응답으로 패키징",
        "payload": {"llm_used": bool((result.get("llm") or {}).get("used")) if isinstance(result.get("llm"), dict) else False},
        "output": _flowi_trace_output_label(tool),
        "status": "done" if status != "blocked" else "blocked",
    })
    return calls[:8]


def _flowi_trace_call_graph(
    *,
    api_calls: list[dict[str, Any]],
    tool: dict[str, Any],
    result: dict[str, Any],
    prompt: str,
) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    def add_node(node_id: str, node_type: str, title: str, detail: str, status: str = "done") -> None:
        if any(n.get("id") == node_id for n in nodes):
            return
        nodes.append({"id": node_id, "type": node_type, "title": title, "detail": detail, "status": status})

    def add_edge(source: str, target: str, label: str = "") -> None:
        if source == target:
            return
        edges.append({"source": source, "target": target, "label": label})

    status = _flowi_trace_status(tool)
    endpoint = next((c for c in api_calls if c.get("stage") == "ingress"), {})
    orchestrator = next((c for c in api_calls if c.get("stage") == "orchestrator"), {})
    knowledge_calls = [c for c in api_calls if c.get("stage") == "knowledge"]
    feature_calls = [c for c in api_calls if c.get("stage") == "feature_api"]
    prompt_text = str(prompt or "").strip()
    activated_feature = str(tool.get("feature") or (feature_calls[0].get("feature") if feature_calls else "") or "general")
    handler_action = str(tool.get("action") or tool.get("intent") or "")
    activated_action = _flowi_driver_contract_action(handler_action, str(tool.get("intent") or ""), activated_feature)
    first_api = feature_calls[0] if feature_calls else {}

    add_node("prompt", "input", "Prompt 전달", prompt_text[:260] or "빈 prompt", "done")
    add_node("fastapi", "fastapi", endpoint.get("path") or _flowi_trace_result_endpoint(result), endpoint.get("purpose") or "FastAPI request", endpoint.get("status") or "done")
    add_node(
        "orchestrator",
        "orchestrator",
        "오케스트레이터 판단",
        f"intent={tool.get('intent') or 'general'}, action={tool.get('action') or '-'}",
        orchestrator.get("status") or status,
    )
    add_node(
        "guardrail",
        "guardrail",
        "Schema / permission check",
        "필수 slot, 권한, 저장 전 확인 상태 검증",
        "blocked" if tool.get("blocked") else ("waiting" if _flowi_waiting_for(tool) else "done"),
    )
    add_edge("prompt", "fastapi", "POST")
    add_edge("fastapi", "orchestrator", "request")
    add_edge("orchestrator", "guardrail", "validate")

    previous = "guardrail"
    if knowledge_calls:
        knowledge = knowledge_calls[0]
        add_node(
            "knowledge",
            "knowledge",
            "Agent Wiki / Schema",
            knowledge.get("output") or knowledge.get("purpose") or "knowledge lookup",
            knowledge.get("status") or "done",
        )
        add_edge(previous, "knowledge", "lookup")
        previous = "knowledge"
    if feature_calls:
        feature_name = str(tool.get("feature") or feature_calls[0].get("feature") or "feature")
        add_node("feature", "feature_subagent", f"{feature_name} subagent", str(tool.get("action") or tool.get("intent") or ""), status)
        add_edge(previous, "feature", "dispatch")
        previous = "feature"
        for idx, call in enumerate(feature_calls, start=1):
            node_id = f"api_{idx}"
            title = call.get("name") or call.get("callee") or f"API {idx}"
            detail = call.get("path") or call.get("callee") or call.get("purpose") or ""
            add_node(node_id, "api_call", title, detail, call.get("status") or status)
            add_edge(previous, node_id, call.get("method") or "call")
            previous = node_id
    elif not tool.get("blocked"):
        add_node("feature", "feature_subagent", str(tool.get("feature") or "general subagent"), str(tool.get("action") or tool.get("intent") or ""), status)
        add_edge(previous, "feature", "dispatch")
        previous = "feature"

    add_node("result", "result", "Tool result", _flowi_trace_output_label(tool), "done" if not tool.get("blocked") else "blocked")
    add_node("answer", "answer", "Answer payload", "answer, table/chart, clarification, next_actions를 반환", "done" if not tool.get("blocked") else "blocked")
    add_edge(previous, "result", "return")
    add_edge("result", "answer", "compose")
    return {
        "nodes": nodes,
        "edges": edges,
        "layout": "linear_dag",
        "activation": {
            "prompt": prompt_text[:1000],
            "endpoint": endpoint.get("path") or _flowi_trace_result_endpoint(result),
            "intent": str(tool.get("intent") or "general"),
            "feature": activated_feature,
            "action": activated_action,
            "handler_action": handler_action,
            "api": first_api.get("path") or first_api.get("callee") or "",
            "handler": first_api.get("callee") or activated_action,
            "status": _flowi_activation_status(tool, result),
            "output": _flowi_trace_output_label(tool),
            "payload_summary": _flowi_activation_payload_summary(tool),
            "next_action": _flowi_next_action_label(result, tool),
            "missing": _flowi_trace_missing_slots(tool),
            "cause": str(tool.get("reject_reason") or tool.get("detail") or ""),
        },
    }


def _flowi_trace_persona_snapshot() -> dict[str, Any]:
    persona = _flowi_persona_config()
    return {
        "label": FLOWI_AGENT_PERSONA.get("label") or "",
        "role": FLOWI_AGENT_PERSONA.get("role") or "",
        "prompt_source": persona.get("source") or "default",
        "system_prompt_chars": len(str(persona.get("active_system_prompt") or "")),
        "must_not_chars": len(str(persona.get("must_not") or "")),
        "principle_count": len(FLOWI_AGENT_PERSONA.get("principles") or []),
        "public_note": "내부 사고과정이 아니라 현재 적용된 persona/system prompt cache의 공개 요약입니다.",
    }


def _flowi_trace_prompt_cache(allowed_keys: set[str]) -> dict[str, Any]:
    feature_docs: list[str] = []
    try:
        if FLOWI_AGENT_FEATURE_GUIDE_DIR.is_dir():
            allowed = set(allowed_keys or set())
            for fp in sorted(FLOWI_AGENT_FEATURE_GUIDE_DIR.glob("*.md")):
                stem = fp.stem
                if not allowed or stem in allowed:
                    feature_docs.append(fp.name)
    except Exception:
        feature_docs = []
    workflow_count = 0
    try:
        workflow_count = len(flowi_workflow_catalog.load_catalog(ensure=True).get("workflows") or [])
    except Exception:
        workflow_count = 0
    return {
        "allowed_features": sorted(allowed_keys),
        "feature_entrypoint_count": len([item for item in FLOWI_FEATURE_ENTRYPOINTS if item.get("key") in allowed_keys]),
        "feature_docs": feature_docs[:12],
        "few_shot_count": len(FLOWI_FUNCTION_FEW_SHOTS),
        "workflow_catalog_count": workflow_count,
        "promoted_knowledge_count": len(_flowi_promoted_knowledge_items(limit=200)),
        "cache_scope": "feature docs, workflow catalog, few-shot examples, promoted knowledge summaries",
    }


def _flowi_trace_subagent_context(tool: dict[str, Any], api_calls: list[dict[str, Any]]) -> dict[str, Any]:
    feature_calls = [call for call in api_calls if call.get("stage") == "feature_api"]
    first_api = feature_calls[0] if feature_calls else {}
    handler_action = str(tool.get("action") or tool.get("intent") or "")
    unit_action = _flowi_driver_contract_action(handler_action, str(tool.get("intent") or ""), str(tool.get("feature") or ""))
    context = {
        "feature_subagent": tool.get("feature") or "general",
        "intent": tool.get("intent") or "general",
        "handler_action": handler_action,
        "unit_action": unit_action,
        "api_or_handler": first_api.get("path") or first_api.get("callee") or "",
        "payload_summary": _flowi_activation_payload_summary(tool),
        "source_profile": tool.get("source_profile") if isinstance(tool.get("source_profile"), dict) else {},
        "deterministic_handler": True,
    }
    children = tool.get("_subagent_children") if isinstance(tool.get("_subagent_children"), list) else []
    if not children and isinstance(tool.get("subagent_children"), list):
        children = tool.get("subagent_children") or []
    if children:
        context["children"] = [
            {
                "name": child.get("name") or "",
                "status": child.get("status") or "",
                "took_ms": child.get("took_ms") or 0,
                "intent": child.get("intent") or "",
                "action": child.get("action") or "",
                "feature": child.get("feature") or "",
                "evidence_count": child.get("evidence_count") or 0,
                "error": child.get("error") or "",
            }
            for child in children[:12]
            if isinstance(child, dict)
        ]
    if isinstance(tool.get("slots"), dict):
        context["slots"] = {k: v for k, v in tool.get("slots", {}).items() if v not in (None, "", [], {})}
    if isinstance(tool.get("filters"), dict):
        context["filters"] = {k: v for k, v in tool.get("filters", {}).items() if v not in (None, "", [], {})}
    return context


def _flowi_trace_clarification_loop(tool: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    clarification = tool.get("clarification") if isinstance(tool.get("clarification"), dict) else {}
    choices = clarification.get("choices") if isinstance(clarification.get("choices"), list) else []
    safe_choices = []
    for choice in choices[:5]:
        if not isinstance(choice, dict):
            continue
        safe_choices.append({
            key: choice.get(key)
            for key in ("id", "label", "title", "description", "prompt", "value", "recommended")
            if key in choice
        })
    missing = _flowi_trace_missing_slots(tool)
    status = _flowi_activation_status(tool, result)
    handler_action = str(tool.get("action") or tool.get("intent") or "")
    return {
        "status": status,
        "needs_input": status == "needs_input",
        "question": clarification.get("question") or ("필수 입력값을 보완해 주세요." if missing else ""),
        "missing": missing,
        "choices": safe_choices,
        "next_unit_action": _flowi_driver_contract_action(handler_action, str(tool.get("intent") or ""), str(tool.get("feature") or "")),
        "pending_prompt": str(tool.get("pending_prompt") or tool.get("last_partial_prompt") or (tool.get("workflow_state") or {}).get("last_prompt") or "")[:1000],
        "user_answer": "",
    }


def _flowi_trace_interpretation(tool: dict[str, Any]) -> dict[str, Any]:
    slots = tool.get("slots") if isinstance(tool.get("slots"), dict) else {}
    filters = tool.get("filters") if isinstance(tool.get("filters"), dict) else {}
    args = tool.get("arguments") if isinstance(tool.get("arguments"), dict) else {}

    def first(*keys: str) -> Any:
        for src in (args, slots, filters):
            for key in keys:
                value = src.get(key)
                if value not in (None, "", [], {}):
                    return value
        return ""

    source_candidates = []
    raw_sources = first("source_types", "source_type")
    if isinstance(raw_sources, list):
        source_candidates = [str(x) for x in raw_sources if str(x or "").strip()]
    elif raw_sources:
        source_candidates = [str(raw_sources)]
    if not source_candidates and isinstance(tool.get("source_ids"), list):
        source_candidates = [str(x) for x in tool.get("source_ids") or [] if str(x or "").strip()]
    missing = _flowi_trace_missing_slots(tool)
    filled = {}
    for key in ("product", "root_lot_id", "root_lot_ids", "fab_lot_ids", "lot_ids", "wafer_id", "wafer_ids", "step", "step_id", "metric", "item_id", "semantic_term", "source_type", "module", "meeting_title", "session_idx"):
        value = first(key)
        if value not in (None, "", [], {}):
            filled[key] = value
    knowledge_terms = []
    seen_terms = set()
    for row in tool.get("retrieved_knowledge") or []:
        if not isinstance(row, dict):
            continue
        term = str(row.get("term") or "").strip()
        doc_id = str(row.get("id") or row.get("doc_id") or row.get("knowledge_id") or "").strip()
        if not term or (term, doc_id) in seen_terms:
            continue
        seen_terms.add((term, doc_id))
        knowledge_terms.append({
            "term": term,
            "id": doc_id,
            "title": row.get("title") or doc_id,
            "kind": row.get("kind") or "",
            "relation_id": row.get("relation_id") or "",
            "column": row.get("column") or "",
        })
    return {
        "input_slots": {
            "product": first("product"),
            "lot": first("root_lot_ids", "root_lot_id", "fab_lot_ids", "fab_lot_id", "lot_ids", "lot_id"),
            "wafer": first("wafer_ids", "wafer_id"),
            "step": first("step", "step_ids", "step_id"),
            "item": first("metric", "metrics", "metrics_or_items", "item", "items", "item_id"),
            "semantic_term": first("semantic_term"),
            "agg": first("agg", "aggregation"),
            "meeting": first("meeting_title", "meeting_id"),
            "session": first("session_idx", "session_id"),
            "source_candidates": source_candidates,
        },
        "missing_slots": missing,
        "filled_slots": filled,
        "knowledge_terms": knowledge_terms[:8],
        "term_resolution": _flowi_trace_term_resolution(tool, knowledge_terms),
    }


def _flowi_trace_evidence(tool: dict[str, Any], api_calls: list[dict[str, Any]]) -> dict[str, Any]:
    feature_calls = [call for call in api_calls if call.get("stage") == "feature_api"]
    first_api = feature_calls[0] if feature_calls else {}
    sql_draft = tool.get("sql_draft") if isinstance(tool.get("sql_draft"), dict) else {}
    chart_cfg = tool.get("chart_config") if isinstance(tool.get("chart_config"), dict) else (tool.get("config") if isinstance(tool.get("config"), dict) else {})
    if not chart_cfg and isinstance(tool.get("blocks"), list):
        chart_cfg = _flowi_block_chart_config(tool.get("blocks") or [])
    table = tool.get("table") if isinstance(tool.get("table"), dict) else {}
    filters = tool.get("filters") if isinstance(tool.get("filters"), dict) else {}
    sources = []
    for row in tool.get("sources") or []:
        if isinstance(row, dict):
            sources.append({k: row.get(k) for k in ("meeting_id", "meeting_title", "session_id", "session_idx", "type", "title") if row.get(k) not in (None, "")})
    knowledge_sources = []
    for row in tool.get("retrieved_knowledge") or []:
        if not isinstance(row, dict):
            continue
        doc_id = str(row.get("id") or row.get("doc_id") or row.get("knowledge_id") or "").strip()
        if not doc_id:
            continue
        knowledge_sources.append({
            "id": doc_id,
            "title": row.get("title") or doc_id,
            "kind": row.get("kind") or "",
            "term": row.get("term") or "",
            "source": row.get("source") or "",
            "relation_id": row.get("relation_id") or "",
            "column": row.get("column") or "",
        })
    return {
        "used_feature_ai": tool.get("feature") or first_api.get("feature") or "flowi",
        "endpoint": first_api.get("path") or first_api.get("callee") or "",
        "payload_summary": _flowi_activation_payload_summary(tool),
        "filters": filters,
        "sql": sql_draft.get("sql") or filters.get("sql") or "",
        "selected_columns": tool.get("selected_columns") or sql_draft.get("selected_columns") or [],
        "source_ids": tool.get("source_ids") if isinstance(tool.get("source_ids"), list) else [],
        "relation_ids": tool.get("relation_ids") if isinstance(tool.get("relation_ids"), list) else [],
        "join_keys": tool.get("join_keys") if isinstance(tool.get("join_keys"), list) else [],
        "join_plan": tool.get("join_plan") if isinstance(tool.get("join_plan"), dict) else {},
        "query_plan": tool.get("query_plan") if isinstance(tool.get("query_plan"), dict) else {},
        "sql_plan": tool.get("sql_plan") or chart_cfg.get("sql_plan") or ((chart_cfg.get("source_evidence") or {}).get("sql_plan") if isinstance(chart_cfg.get("source_evidence"), dict) else ""),
        "impact_context": tool.get("impact_context") if isinstance(tool.get("impact_context"), dict) else {},
        "chart_config": chart_cfg,
        "meeting_sources": sources[:8],
        "knowledge_sources": knowledge_sources[:12],
        "table_total": table.get("total", len(table.get("rows") or [])) if table else 0,
        "api_calls": [
            {
                "stage": call.get("stage"),
                "method": call.get("method"),
                "path": call.get("path"),
                "callee": call.get("callee"),
                "status": call.get("status"),
                "output": call.get("output"),
            }
            for call in api_calls
        ],
    }


def _flowi_trace_validation(tool: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    table = tool.get("table") if isinstance(tool.get("table"), dict) else {}
    chart = tool.get("chart_result") if isinstance(tool.get("chart_result"), dict) else (tool.get("chart") if isinstance(tool.get("chart"), dict) else {})
    blocks = tool.get("blocks") if isinstance(tool.get("blocks"), list) else []
    if blocks and not chart:
        for block in blocks:
            payload = block.get("payload") if isinstance(block, dict) and isinstance(block.get("payload"), dict) else {}
            if str(block.get("kind") or "").startswith("chart_") and payload:
                chart = payload
                break
    block_rows = 0
    for block in blocks:
        payload = block.get("payload") if isinstance(block, dict) and isinstance(block.get("payload"), dict) else {}
        if block.get("kind") == "lot_table":
            block_rows += int(payload.get("total") or len(payload.get("rows") or []) or 0)
    sql_draft = tool.get("sql_draft") if isinstance(tool.get("sql_draft"), dict) else {}
    warnings = [str(w) for w in (tool.get("warnings") or []) if str(w).strip()]
    warnings.extend(str(w) for w in (sql_draft.get("warnings") or []) if str(w).strip())
    llm = result.get("llm") if isinstance(result.get("llm"), dict) else {}
    source_count = 0
    if isinstance(tool.get("source_ids"), list):
        source_count = len(tool.get("source_ids") or [])
    elif isinstance(tool.get("sources"), list):
        source_count = len(tool.get("sources") or [])
    elif isinstance(tool.get("retrieved_knowledge"), list):
        source_count = len(tool.get("retrieved_knowledge") or [])
    return {
        "rows": int(tool.get("row_count") or 0) or block_rows or (table.get("total", len(table.get("rows") or [])) if table else len(tool.get("rows") or [])),
        "chart_readiness": chart.get("status") or ("ready" if chart else ""),
        "source_count": source_count,
        "warnings": list(dict.fromkeys(warnings))[:12],
        "fallback": bool(sql_draft.get("fallback") or tool.get("fallback") or (llm.get("error") and not llm.get("used"))),
        "llm_used": bool(llm.get("used") or (sql_draft.get("llm") or {}).get("used")),
        "llm_error": llm.get("error") or (sql_draft.get("llm") or {}).get("error") or "",
    }


def _flowi_agent_runtime_contract_trace(prompt: str, tool: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    try:
        frame = _agent_runtime_resolve_semantic_frame(prompt, max_terms=32)
        plans, meta = _agent_runtime_build_action_plans(
            goal=prompt,
            semantic=frame.model_dump(),
            username=str(result.get("user") or ""),
        )
        plan_rows = _agent_runtime_compact_plan_rows(plans)
        guardrail = meta.get("guardrail") or _agent_runtime_guardrail_summary_from_plans(plans)
        selected_feature = str(tool.get("feature") or "")
        selected_action = str(tool.get("action") or tool.get("intent") or "")
        selections = []
        for row in plan_rows:
            unit_ai = str(row.get("unit_ai") or "")
            status = "planned"
            if row.get("policy") == "blocked" or tool.get("blocked"):
                status = "blocked"
            elif row.get("approval_required") or tool.get("requires_confirmation"):
                status = "approval_required"
            elif selected_feature and unit_ai == selected_feature:
                status = "delegated"
            selections.append({
                "key": unit_ai,
                "title": f"{unit_ai}.{row.get('action') or ''}",
                "status": status,
                "reason": str(row.get("policy") or "read_only"),
            })
        return {
            "semantic": {
                "intent": frame.intent,
                "coverage": frame.coverage,
                "slots": frame.slots,
                "warnings": frame.warnings,
                "candidate_count": len(frame.candidates),
            },
            "plan": plan_rows,
            "unit_ai_selection": selections,
            "guardrail": {
                **guardrail,
                "tool_blocked": bool(tool.get("blocked")),
                "tool_requires_confirmation": bool(tool.get("requires_confirmation")),
                "selected_feature": selected_feature,
                "selected_action": selected_action,
            },
        }
    except Exception as exc:
        logger.debug("flowi agent runtime contract trace failed: %s", exc)
        return {
            "semantic": {},
            "plan": [],
            "unit_ai_selection": [],
            "guardrail": {"status": "unavailable", "error": str(exc)[:160]},
        }


def _flowi_public_trace(
    *,
    prompt: str,
    allowed_keys: set[str],
    result: dict[str, Any],
    agent_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """User-visible execution trace. This is not model chain-of-thought."""
    tool = result.get("tool") if isinstance(result.get("tool"), dict) else {}
    llm = result.get("llm") if isinstance(result.get("llm"), dict) else {}
    context_messages = []
    if isinstance(agent_context, dict):
        raw_msgs = agent_context.get("messages")
        context_messages = raw_msgs if isinstance(raw_msgs, list) else []
    table = tool.get("table") if isinstance(tool.get("table"), dict) else {}
    chart = tool.get("chart_result") if isinstance(tool.get("chart_result"), dict) else (tool.get("chart") if isinstance(tool.get("chart"), dict) else {})
    data_source = tool.get("data_source") if isinstance(tool.get("data_source"), dict) else {}
    source_profile = tool.get("source_profile") if isinstance(tool.get("source_profile"), dict) else {}
    choices = []
    clarification = tool.get("clarification") if isinstance(tool.get("clarification"), dict) else {}
    if isinstance(clarification.get("choices"), list):
        choices = clarification.get("choices") or []
    retrieved_knowledge = [
        {
            "id": row.get("id") or row.get("doc_id") or row.get("knowledge_id") or "",
            "title": row.get("title") or "",
            "kind": row.get("kind") or "",
            "summary": row.get("summary") or "",
            "term": row.get("term") or "",
            "source": row.get("source") or "",
            "relation_id": row.get("relation_id") or "",
            "column": row.get("column") or "",
            "score": row.get("score"),
        }
        for row in (tool.get("retrieved_knowledge") or [])
        if isinstance(row, dict) and (row.get("id") or row.get("doc_id") or row.get("knowledge_id"))
    ][:12]

    intent = str(tool.get("intent") or "general")
    action = str(tool.get("action") or tool.get("feature") or "")
    output_bits = []
    if table:
        output_bits.append(f"table {table.get('kind') or ''} rows={table.get('total', len(table.get('rows') or []))}")
    if chart:
        output_bits.append(f"chart {chart.get('kind') or chart.get('status') or 'planned'}")
    if tool.get("rows"):
        output_bits.append(f"rows={len(tool.get('rows') or [])}")
    if tool.get("knobs"):
        output_bits.append(f"knobs={len(tool.get('knobs') or [])}")
    if data_source.get("file"):
        output_bits.append(f"source={data_source.get('file')}")
    elif data_source.get("root"):
        output_bits.append(f"source={data_source.get('root')}/{data_source.get('product') or ''}".rstrip("/"))
    if source_profile:
        output_bits.append("profile=" + _flowi_profile_label(source_profile))
    if choices:
        output_bits.append(f"choices={len(choices)}")
    if retrieved_knowledge:
        output_bits.append("knowledge=" + ", ".join(str(row.get("id") or "") for row in retrieved_knowledge[:3]))
    if not output_bits:
        output_bits.append("answer text")

    guard_status = "blocked" if tool.get("blocked") else "done"
    guard_detail = "차단됨" if tool.get("blocked") else "허용된 단위기능 범위에서 진행"
    if tool.get("blocked") and tool.get("missing_permission"):
        guard_detail = f"권한 없음: {tool.get('missing_permission')}"
    elif tool.get("intent") == "admin_file_operation":
        guard_detail = "admin 파일 작업은 FLOWI_FILE_OP 확인 구조로 제한"
    elif tool.get("intent") == "blocked_write_request":
        guard_detail = "일반 user의 DB/File 원본 수정 요청 차단"

    llm_status = "done" if llm.get("used") else ("error" if llm.get("error") else "skipped")
    if llm.get("blocked"):
        llm_status = "blocked"
    llm_detail = "LLM이 로컬 결과를 짧게 정리" if llm.get("used") else "로컬 단위기능 결과를 그대로 사용"
    if llm.get("error"):
        llm_detail = f"LLM 오류: {llm.get('error')}"
    if llm.get("blocked"):
        llm_detail = "권한/보호 정책으로 LLM 보정 없이 종료"

    ts = datetime.now(timezone.utc).isoformat()
    steps = [
        {
            "key": "receive",
            "stage": "receive",
            "title": "요청 접수",
            "label": "요청 접수",
            "status": "done",
            "detail": f"prompt {len(prompt or '')} chars, context {len(context_messages)} messages",
            "ts": ts,
        },
        {
            "key": "auth",
            "stage": "auth",
            "title": "사용자/권한 확인",
            "label": "사용자/권한 확인",
            "status": "done",
            "detail": f"허용 기능 {len(allowed_keys)}개",
            "ts": ts,
        },
        {
            "key": "route",
            "stage": "route",
            "title": "의도/단위기능 선택",
            "label": "의도/단위기능 선택",
            "status": "done",
            "detail": f"intent={intent}" + (f", action={action}" if action else ""),
            "ts": ts,
        },
        {
            "key": "guardrail",
            "stage": "guardrail",
            "title": "권한/쓰기 보호",
            "label": "권한/쓰기 보호",
            "status": guard_status,
            "detail": guard_detail,
            "ts": ts,
        },
        {
            "key": "tool",
            "stage": "tool",
            "title": "DB/cache/tool 실행",
            "label": "DB/cache/tool 실행",
            "status": "skipped" if tool.get("blocked") else "done",
            "detail": ", ".join(output_bits),
            "ts": ts,
        },
        {
            "key": "llm",
            "stage": "llm",
            "title": "LLM 답변 정리",
            "label": "LLM 답변 정리",
            "status": llm_status,
            "detail": llm_detail,
            "ts": ts,
        },
        {
            "key": "render",
            "stage": "render",
            "title": "화면 출력 준비",
            "label": "화면 출력 준비",
            "status": "done",
            "detail": ", ".join(output_bits),
            "ts": ts,
        },
    ]
    if retrieved_knowledge:
        wiki_context = tool.get("wiki_interpretation") if isinstance(tool.get("wiki_interpretation"), dict) else {}
        knowledge_step = {
            "key": "knowledge",
            "stage": "knowledge",
            "title": "Agent Wiki 사전 해석" if wiki_context.get("pre_route") else "Agent Wiki 검색",
            "label": "Agent Wiki 사전 해석" if wiki_context.get("pre_route") else "Agent Wiki 검색",
            "status": "done",
            "detail": ", ".join(
                f"{row.get('id')}{'(' + row.get('term') + ')' if row.get('term') else ''}"
                for row in retrieved_knowledge[:5]
            ),
            "ts": ts,
        }
        steps.insert(2 if wiki_context.get("pre_route") else 3, knowledge_step)
    workflow_matches = []
    try:
        workflow_matches = flowi_workflow_catalog.match_workflows(prompt, limit=5)
    except Exception:
        workflow_matches = []
    api_calls = _flowi_trace_api_calls(
        result=result,
        tool=tool,
        prompt=prompt,
        allowed_keys=allowed_keys,
    )
    call_graph = _flowi_trace_call_graph(api_calls=api_calls, tool=tool, result=result, prompt=prompt)
    runtime_contract = _flowi_agent_runtime_contract_trace(prompt, tool, result)
    return {
        "kind": "public_execution_trace",
        "visible": True,
        "note": "사고과정 원문이 아니라 사용자가 검증할 수 있는 실행 흐름 요약입니다.",
        "activation": call_graph.get("activation") or {},
        "semantic": runtime_contract.get("semantic") or {},
        "plan": runtime_contract.get("plan") or [],
        "unit_ai_selection": runtime_contract.get("unit_ai_selection") or [],
        "guardrail": runtime_contract.get("guardrail") or {},
        "interpretation": _flowi_trace_interpretation(tool),
        "evidence": _flowi_trace_evidence(tool, api_calls),
        "validation": _flowi_trace_validation(tool, result),
        "persona_snapshot": _flowi_trace_persona_snapshot(),
        "prompt_cache": _flowi_trace_prompt_cache(allowed_keys),
        "workflow_matches": workflow_matches,
        "subagent_context": _flowi_trace_subagent_context(tool, api_calls),
        "clarification_loop": _flowi_trace_clarification_loop(tool, result),
        "retrieved_knowledge": retrieved_knowledge,
        "steps": steps,
        "api_calls": api_calls,
        "call_graph": call_graph,
    }


def _flowi_action_log_refs(values: Any, limit: int = 8) -> list[str]:
    refs: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in refs:
            refs.append(text[:160])

    if isinstance(values, dict):
        for key in ("id", "doc_id", "knowledge_id", "relation_id", "source_id", "event_id", "path", "callee"):
            add(values.get(key))
    elif isinstance(values, list):
        for item in values:
            if isinstance(item, dict):
                for key in ("id", "doc_id", "knowledge_id", "relation_id", "source_id", "event_id", "path", "callee"):
                    add(item.get(key))
            else:
                add(item)
    else:
        add(values)
    return refs[:limit]


def _flowi_action_log_api_refs(api_calls: list[dict[str, Any]], stages: set[str], limit: int = 6) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for call in api_calls:
        if not isinstance(call, dict) or (stages and call.get("stage") not in stages):
            continue
        ref = {
            key: call.get(key)
            for key in ("stage", "name", "method", "path", "callee", "status", "output", "metadata")
            if call.get(key) not in (None, "", [], {})
        }
        if ref:
            refs.append(ref)
    return refs[:limit]


def _flowi_action_log_evidence_refs(trace: dict[str, Any]) -> list[str]:
    refs: list[str] = []

    def extend(values: Any) -> None:
        for ref in _flowi_action_log_refs(values):
            if ref not in refs:
                refs.append(ref)

    extend(trace.get("retrieved_knowledge") if isinstance(trace.get("retrieved_knowledge"), list) else [])
    evidence = trace.get("evidence") if isinstance(trace.get("evidence"), dict) else {}
    for key in ("source_ids", "relation_ids", "join_keys"):
        extend(evidence.get(key))
    impact = evidence.get("impact_context") if isinstance(evidence.get("impact_context"), dict) else {}
    extend(impact.get("wiki_refs") if isinstance(impact.get("wiki_refs"), list) else [])
    extend(impact.get("event_refs") if isinstance(impact.get("event_refs"), list) else [])
    return refs[:12]


def _flowi_action_log_summary(result: dict[str, Any], trace: dict[str, Any]) -> list[str]:
    llm = result.get("llm") if isinstance(result.get("llm"), dict) else {}
    public_summary = llm.get("public_summary") if isinstance(llm.get("public_summary"), list) else []
    if public_summary:
        return [line for line in (_flowi_clean_public_summary_line(x) for x in public_summary) if line][:6]

    activation = trace.get("activation") if isinstance(trace.get("activation"), dict) else {}
    evidence = trace.get("evidence") if isinstance(trace.get("evidence"), dict) else {}
    validation = trace.get("validation") if isinstance(trace.get("validation"), dict) else {}
    semantic = trace.get("semantic") if isinstance(trace.get("semantic"), dict) else {}
    plan = trace.get("plan") if isinstance(trace.get("plan"), list) else []
    api_calls = trace.get("api_calls") if isinstance(trace.get("api_calls"), list) else []
    knowledge = trace.get("retrieved_knowledge") if isinstance(trace.get("retrieved_knowledge"), list) else []
    lines: list[str] = []

    intent = activation.get("intent") or semantic.get("intent") or "general"
    feature = evidence.get("used_feature_ai") or activation.get("feature") or "Flow-i"
    action = activation.get("action") or ""
    lines.append(f"선택한 intent/action은 {intent}" + (f" / {action}" if action else "") + f"이며 {feature} 기능으로 처리했습니다.")
    if knowledge:
        ids = _flowi_action_log_refs(knowledge, limit=3)
        lines.append(f"사용 근거는 Wiki/schema {len(knowledge)}건" + (f"({', '.join(ids)})" if ids else "") + "입니다.")
    elif evidence.get("source_ids") or evidence.get("relation_ids"):
        refs = _flowi_action_log_evidence_refs(trace)[:4]
        lines.append("사용 근거는 등록 source/relation " + ", ".join(refs) + "입니다.")
    if plan:
        delegated = [str(row.get("unit_ai") or row.get("action") or "").strip() for row in plan if isinstance(row, dict)]
        delegated = [x for x in delegated if x]
        if delegated:
            lines.append(f"실행 계획은 {', '.join(delegated[:4])} 단위 기능 후보를 확인했습니다.")
    feature_calls = [c for c in api_calls if isinstance(c, dict) and c.get("stage") == "feature_api"]
    if feature_calls:
        names = [str(c.get("name") or c.get("callee") or c.get("path") or "").strip() for c in feature_calls]
        names = [name for name in names if name]
        lines.append(f"실행 경로는 {', '.join(names[:3])}입니다.")
        split_call = next((c for c in feature_calls if str(c.get("path") or "") == "/api/splittable/view"), None)
        split_meta = split_call.get("metadata") if isinstance(split_call, dict) and isinstance(split_call.get("metadata"), dict) else {}
        if split_meta:
            elapsed = split_meta.get("elapsed_ms")
            cache = split_meta.get("view_cache") if isinstance(split_meta.get("view_cache"), dict) else {}
            cache_bits = []
            for key in ("status", "state", "hit", "source"):
                if cache.get(key) not in (None, "", [], {}):
                    cache_bits.append(f"{key}={cache.get(key)}")
            detail = []
            if elapsed not in (None, ""):
                detail.append(f"{elapsed}ms")
            if cache_bits:
                detail.append("cache " + ", ".join(cache_bits[:3]))
            lines.append("/api/splittable/view 실제 호출 완료" + (f" ({' / '.join(detail)})" if detail else "") + ".")
    rows = validation.get("rows")
    warnings = validation.get("warnings") if isinstance(validation.get("warnings"), list) else []
    result_bits = []
    if rows not in (None, ""):
        result_bits.append(f"결과 {rows}건")
    if validation.get("chart_readiness"):
        result_bits.append(f"chart {validation.get('chart_readiness')}")
    if validation.get("fallback"):
        result_bits.append("fallback 사용")
    if warnings:
        result_bits.append(f"warning {len(warnings)}건")
    if result_bits:
        lines.append("검증 결과: " + ", ".join(result_bits) + ".")
    if llm.get("used"):
        lines.append("LLM은 서버 결과를 재판단하지 않고 최종 문장 정리에만 사용했습니다.")
    elif llm.get("error"):
        lines.append("LLM 정리는 실패해 deterministic 결과를 그대로 사용했습니다.")
    return [line for line in lines if line][:6] or ["Flow-i가 허용된 기능 범위에서 요청을 처리했습니다."]


def _flowi_action_log_timeline(trace: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    activation = trace.get("activation") if isinstance(trace.get("activation"), dict) else {}
    semantic = trace.get("semantic") if isinstance(trace.get("semantic"), dict) else {}
    interpretation = trace.get("interpretation") if isinstance(trace.get("interpretation"), dict) else {}
    guardrail = trace.get("guardrail") if isinstance(trace.get("guardrail"), dict) else {}
    validation = trace.get("validation") if isinstance(trace.get("validation"), dict) else {}
    plan = trace.get("plan") if isinstance(trace.get("plan"), list) else []
    api_calls = trace.get("api_calls") if isinstance(trace.get("api_calls"), list) else []
    evidence_refs = _flowi_action_log_evidence_refs(trace)
    status = str(activation.get("status") or "done")
    guard_status = "blocked" if guardrail.get("tool_blocked") or status == "blocked" else ("waiting" if status.startswith("awaiting") or status == "needs_input" else "done")
    rows = validation.get("rows")
    warnings = validation.get("warnings") if isinstance(validation.get("warnings"), list) else []

    slots = semantic.get("slots") if isinstance(semantic.get("slots"), dict) else {}
    filled_slots = interpretation.get("filled_slots") if isinstance(interpretation.get("filled_slots"), dict) else {}
    slot_keys = list((slots or filled_slots or {}).keys())[:8]
    plan_titles = [
        str(row.get("unit_ai") or row.get("action") or "").strip()
        for row in plan
        if isinstance(row, dict) and (row.get("unit_ai") or row.get("action"))
    ][:5]
    feature_calls = [call for call in api_calls if isinstance(call, dict) and call.get("stage") == "feature_api"]
    feature_names = [
        str(call.get("name") or call.get("callee") or call.get("path") or "").strip()
        for call in feature_calls
    ][:4]

    return [
        {
            "stage": "semantic_layer",
            "title": "질문 해석",
            "detail": f"intent={semantic.get('intent') or activation.get('intent') or 'general'}"
            + (f", coverage={semantic.get('coverage')}" if semantic.get("coverage") not in (None, "") else "")
            + (f", slots={', '.join(slot_keys)}" if slot_keys else ""),
            "status": "done",
            "evidence_refs": evidence_refs[:6],
            "api_refs": _flowi_action_log_api_refs(api_calls, {"ingress", "knowledge"}, limit=4),
        },
        {
            "stage": "task_planner",
            "title": "실행 계획",
            "detail": ", ".join(plan_titles) if plan_titles else f"feature={activation.get('feature') or 'flowi'}, action={activation.get('action') or '-'}",
            "status": guard_status,
            "evidence_refs": evidence_refs[:6],
            "api_refs": _flowi_action_log_api_refs(api_calls, {"orchestrator"}, limit=3),
        },
        {
            "stage": "unit_agents",
            "title": "단위 기능 실행",
            "detail": ", ".join(feature_names) if feature_names else ("권한/입력값 검증에서 실행 대기" if guard_status != "done" else "local Flow-i result"),
            "status": "blocked" if guard_status == "blocked" else ("waiting" if guard_status == "waiting" else "done"),
            "evidence_refs": evidence_refs[:8],
            "api_refs": _flowi_action_log_api_refs(api_calls, {"feature_api"}, limit=6),
        },
        {
            "stage": "conclusion",
            "title": "결과 검증과 답변",
            "detail": ", ".join(
                [
                    bit
                    for bit in (
                        f"rows={rows}" if rows not in (None, "") else "",
                        f"warnings={len(warnings)}" if warnings else "",
                        f"answer_chars={len(str(result.get('answer') or ''))}",
                    )
                    if bit
                ]
            ),
            "status": "blocked" if guard_status == "blocked" else "done",
            "evidence_refs": evidence_refs[:8],
            "api_refs": _flowi_action_log_api_refs(api_calls, {"response"}, limit=3),
        },
    ]


def _flowi_action_log(result: dict[str, Any], trace: dict[str, Any]) -> dict[str, Any]:
    trace = trace if isinstance(trace, dict) else {}
    return {
        "summary": _flowi_action_log_summary(result, trace),
        "timeline": _flowi_action_log_timeline(trace, result),
        "final_answer": result.get("answer") or "",
        "disclaimer": FLOWI_ACTION_LOG_DISCLAIMER,
    }


def _attach_flowi_trace(
    result: dict[str, Any],
    *,
    prompt: str,
    allowed_keys: set[str],
    agent_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if isinstance(agent_context, dict):
        input_prompt = str(agent_context.get("_flowi_input_prompt") or "").strip()
        resolved_prompt = str(agent_context.get("_flowi_resolved_prompt") or prompt or "").strip()
        if input_prompt and resolved_prompt and input_prompt != resolved_prompt:
            result["prompt"] = resolved_prompt
            result["input_prompt"] = input_prompt
            result["resolved_prompt"] = resolved_prompt
    tool = result.get("tool") if isinstance(result.get("tool"), dict) else {}
    if tool:
        _finalize_flowi_tool(tool, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)
        result["workflow_state"] = tool.get("workflow_state")
        result["next_actions"] = tool.get("next_actions")
        if tool.get("last_partial_prompt"):
            result["last_partial_prompt"] = tool.get("last_partial_prompt")
        if isinstance(tool.get("missing_freetext"), list):
            result["missing_freetext"] = tool.get("missing_freetext") or []
        if isinstance(result.get("agent_api"), dict):
            result["agent_api"]["workflow_state"] = tool.get("workflow_state") or {}
            result["agent_api"]["next_actions"] = tool.get("next_actions") or []
            result["agent_api"]["requires_confirmation"] = bool(tool.get("requires_confirmation"))
            if isinstance(tool.get("clarification"), dict):
                result["agent_api"]["clarification"] = tool.get("clarification")
    result["trace"] = _flowi_public_trace(
        prompt=prompt,
        allowed_keys=allowed_keys,
        result=result,
        agent_context=agent_context,
    )
    result["action_log"] = _flowi_action_log(result, result["trace"])
    try:
        from core import home_orchestrator as _home_runtime
        runtime = _home_runtime.record_flowi_runtime_run(
            prompt=prompt,
            result=result,
            user={"username": result.get("user") or ""},
            source="llm_flowi_chat",
        )
        result["run_id"] = runtime.get("run_id") or ""
        result["graph"] = runtime.get("graph") or {}
        result["runtime_status"] = runtime.get("status") or ""
    except Exception as exc:
        logger.warning("home flowi runtime snapshot failed: %s", exc)
    try:
        row = home_memory.remember_turn(
            username=str(result.get("user") or ""),
            prompt=prompt,
            answer=str(result.get("answer") or ""),
            tool=tool,
            source="llm_flowi_chat",
            run_id=str(result.get("run_id") or ""),
        )
        result["home_memory"] = {
            "stored": bool(row),
            "memory_id": (row or {}).get("memory_id") or "",
            "context_message_count": len(_flowi_context_messages(agent_context)),
        }
    except Exception as exc:
        logger.debug("home flowi memory append failed: %s", exc)
    clarification_loop = result["trace"].get("clarification_loop") if isinstance(result.get("trace"), dict) else {}
    if isinstance(clarification_loop, dict) and clarification_loop.get("needs_input"):
        result["needs_input"] = True
        result["question"] = clarification_loop.get("question") or ""
        result["missing"] = clarification_loop.get("missing") or []
        result["choices"] = clarification_loop.get("choices") or []
        result["next_unit_action"] = clarification_loop.get("next_unit_action") or ""
        result["pending_prompt"] = clarification_loop.get("pending_prompt") or result.get("pending_prompt") or ""
    return result


_FLOWI_HOME_USER_TOOL_KEYS = {
    "type",
    "answer",
    "intent",
    "inline_summary",
    "action",
    "feature",
    "feature_entrypoints",
    "navigate",
    "clarification",
    "table",
    "rows",
    "knobs",
    "custom_sets",
    "lot_list",
    "split_view",
    "splittable_view",
    "split_api",
    "runtime_profile",
    "view_cache",
    "elapsed_ms",
    "chart",
    "chart_result",
    "chart_type",
    "chart_session_id",
    "chart_config",
    "blocks",
    "config",
    "data",
    "fit",
    "stats_table",
    "samples_table",
    "wafer_table",
    "module_summary",
    "summary",
    "created_record",
    "created_records",
    "missing",
    "arguments",
    "arguments_partial",
    "arguments_choices",
    "missing_freetext",
    "last_partial_prompt",
    "pending_prompt",
    "inform_preview",
    "mail_preview",
    "walkthrough",
    "draft_id",
    "session_id",
    "slots",
    "filters",
    "term_resolution",
    "measurement_term",
    "sql_draft",
    "source_ids",
    "source",
    "source_detail",
    "relation_ids",
    "join_keys",
    "join_plan",
    "selected_columns",
    "sample_rows",
    "row_count",
    "warnings",
    "sources",
    "impact_context",
    "event_refs",
    "highlight",
    "highlights",
    "side_effect",
    "blocked",
    "reject_reason",
    "requires_confirmation",
}
