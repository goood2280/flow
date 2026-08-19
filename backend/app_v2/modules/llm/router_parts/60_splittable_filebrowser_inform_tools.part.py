def _flowi_splittable_prefixes_from_args(args: dict[str, Any], prompt: str) -> list[str]:
    prefix_raw = args.get("prefix")
    if prefix_raw:
        prefixes: list[str] = []
        for item in re.split(r"[,/ ]+", str(prefix_raw or "").upper()):
            item = item.strip()
            if item in {"KNOB", "MASK", "FAB", "INLINE", "VM"} and item not in prefixes:
                prefixes.append(item)
        if prefixes:
            return prefixes[:5]
    group = str(args.get("group") or _flowi_group_token(prompt) or "").strip().upper()
    if group in {"KNOB", "MASK", "FAB", "INLINE", "VM"}:
        return [group]
    return ["KNOB"]


def _flowi_custom_col_tokens(value: Any) -> list[str]:
    return [
        token.casefold()
        for token in re.findall(r"[A-Za-z0-9]+", str(value or ""))
        if token.strip()
    ]


def _flowi_splittable_custom_cols_from_prompt(
    product_for_view: str,
    args: dict[str, Any],
    prompt: str,
    prefixes: list[str],
) -> tuple[list[str], str]:
    custom_filter = str(args.get("custom_set_filter") or args.get("knob_name") or args.get("step") or "").strip()
    if not custom_filter:
        custom_filter = _flowi_func_step_token(prompt)
    if not custom_filter:
        match = re.search(
            r"([A-Za-z][A-Za-z0-9_.#-]{0,80})\s*(?:\uc774|\uac00)?\s*(?:\ub4e4\uc5b4\uac04|\ud3ec\ud568(?:\ub41c)?)\s*(?:\uc2a4\ud50c\ub9bf|split)",
            str(prompt or ""),
            flags=re.I,
        )
        if match:
            custom_filter = str(match.group(1) or "").strip()
    query_tokens = _flowi_custom_col_tokens(custom_filter)
    blocked_tokens = {"split", "table", "splittable", "custom", "set", "show", "view"}
    query_tokens = [token for token in query_tokens if token not in blocked_tokens]
    if not query_tokens:
        return [], custom_filter
    wanted_prefixes = tuple(f"{str(p or '').strip().upper()}_" for p in (prefixes or ["KNOB"]) if str(p or "").strip()) or ("KNOB_",)
    try:
        from routers import splittable as splittable_router
        lf = splittable_router._scan_product_base(product_for_view)
        schema = lf.collect_schema()
        cols = schema.names() if hasattr(schema, "names") else list(schema)
    except Exception:
        return [], custom_filter
    matches: list[str] = []
    for col in cols:
        name = str(col or "")
        if not _upper(name).startswith(wanted_prefixes):
            continue
        col_tokens = set(_flowi_custom_col_tokens(name))
        if all(token in col_tokens for token in query_tokens) and name not in matches:
            matches.append(name)
        if len(matches) >= 80:
            break
    return matches, custom_filter


def _flowi_saved_custom_name_from_prompt(prompt: str) -> str:
    """프롬프트가 SplitTable 페이지에 저장된 custom SET 이름을 지목하면 그 이름을 반환.

    오탐 방지: 이름이 프롬프트에 그대로 등장하고, (이름이 4자 이상이거나
    프롬프트에 custom/커스텀 언급이 있을 때)만 매칭. 여러 개면 가장 긴 이름 우선.
    """
    text = str(prompt or "")
    up = _upper(text)
    mentions_custom = "CUSTOM" in up or "커스텀" in text
    try:
        from routers import splittable as splittable_router
        customs = (splittable_router.list_customs() or {}).get("customs") or []
    except Exception:
        return ""
    best = ""
    for c in customs:
        name = str((c or {}).get("name") or "").strip()
        if not name or name.upper() not in up:
            continue
        if len(name) < 2 and not re.search(
            rf"(?<![A-Za-z0-9_.-]){re.escape(name)}(?![A-Za-z0-9_.-])\s+(?:CUSTOM\s*SET|custom\s*set|커스텀)",
            text,
            flags=re.I,
        ):
            continue
        if len(name) < 4 and not mentions_custom:
            continue
        if len(name) > len(best):
            best = name
    return best


def _flowi_splittable_view_to_inline(
    view: dict[str, Any],
    *,
    step: str = "",
    max_rows: int = 12,
    prefixes: list[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    headers = [str(h) for h in (view.get("headers") or [])]
    raw_rows = view.get("rows") if isinstance(view.get("rows"), list) else []
    step_u = _upper(step)
    normalized_prefixes = [str(p or "").strip().upper() for p in (prefixes or ["KNOB"]) if str(p or "").strip()]
    wanted_prefixes = tuple(f"{p}_" for p in normalized_prefixes) or ("KNOB_",)
    row_label = "/".join(normalized_prefixes) if normalized_prefixes else "KNOB"
    wafer_fab_list = view.get("wafer_fab_list") or []
    header_groups = view.get("header_groups") or []
    lot_values = []
    header_group_items = header_groups if isinstance(header_groups, list) else []
    wafer_fab_items = wafer_fab_list if isinstance(wafer_fab_list, list) else []
    wafer_fab_list = wafer_fab_items
    header_groups = header_group_items
    for group in header_group_items:
        label = str((group or {}).get("label") or "").strip() if isinstance(group, dict) else ""
        if label and label not in lot_values:
            lot_values.append(label)
    for value in wafer_fab_items:
        label = str(value or "").strip()
        if label and label not in lot_values:
            lot_values.append(label)
    lot_id_label = ", ".join(lot_values)

    def row_text(row: dict[str, Any]) -> str:
        return _upper(" ".join([
            str(row.get("_param") or row.get("parameter") or ""),
            str(row.get("_display") or row.get("display") or ""),
        ]))

    candidates = [
        row for row in raw_rows
        if isinstance(row, dict) and _upper(row.get("_param") or row.get("parameter") or "").startswith(wanted_prefixes)
    ]
    if step_u:
        matched = [row for row in candidates if step_u in row_text(row)]
        if matched:
            candidates = matched
    limit_rows = max(1, min(80, int(max_rows or 12) * 6))
    split_rows: list[dict[str, Any]] = []
    flat_rows: list[dict[str, Any]] = []
    for row in candidates[:limit_rows]:
        param = str(row.get("_param") or row.get("parameter") or "")
        display = str(row.get("_display") or row.get("display") or param)
        cells_src = row.get("_cells") if isinstance(row.get("_cells"), dict) else {}
        cells: list[dict[str, Any]] = []
        for idx, header in enumerate(headers):
            src = cells_src.get(str(idx)) if isinstance(cells_src.get(str(idx)), dict) else {}
            actual = src.get("actual")
            plan = src.get("plan")
            cells.append({
                "wafer_id": header.lstrip("#"),
                "actual": "" if actual is None else str(actual),
                "plan": "" if plan is None else str(plan),
                "mismatch": bool(src.get("mismatch")),
                "highlight": bool(src.get("mismatch")),
                "key": src.get("key") or "",
                "can_plan": bool(src.get("can_plan")),
            })
            if actual not in (None, "") or plan not in (None, "") or src:
                flat_rows.append({
                    "product": view.get("product") or "",
                    "root_lot_id": view.get("root_lot_id") or "",
                    "lot_id": wafer_fab_list[idx] if idx < len(wafer_fab_list) else "",
                    "fab_lot_id": wafer_fab_list[idx] if idx < len(wafer_fab_list) else "",
                    "wafer_id": header.lstrip("#"),
                    "step": step or "",
                    "parameter": param,
                    "display": display,
                    "actual": "" if actual is None else str(actual),
                    "plan": "" if plan is None else str(plan),
                    "mismatch": bool(src.get("mismatch")),
                    "cell_key": src.get("key") or "",
                })
        split_rows.append({"parameter": param, "display": display, "cells": cells})
    split_view = {
        "kind": "splittable_view",
        "title": "SplitTable view",
        "headers": headers,
        "header_groups": header_groups,
        "wafer_fab_list": wafer_fab_list,
        "root_lot_id": view.get("root_lot_id") or "",
        "lot_id_label": lot_id_label,
        "row_labels": view.get("row_labels") or {"root_lot_id": "root_lot_id", "lot_id": "lot_id", "parameter": "항목"},
        "rows": split_rows,
        "total": len(flat_rows) if flat_rows else sum(len(r.get("cells") or []) for r in split_rows),
        "row_label": row_label,
        "source": "splittable.view",
        "lot_warn": view.get("lot_warn") or "",
        "available_fab_lots": view.get("available_fab_lots") or [],
    }
    cols_out = ["product", "root_lot_id", "lot_id", "fab_lot_id", "wafer_id", "step", "parameter", "display", "actual", "plan", "mismatch", "cell_key"]
    table = {
        "kind": "splittable_view_rows",
        "title": "SplitTable rows",
        "placement": "below",
        "columns": _table_columns(cols_out),
        "rows": [{k: r.get(k, "") for k in cols_out} for r in flat_rows[:max(1, min(160, int(max_rows or 12) * 12))]],
        "total": len(flat_rows),
        "source": "splittable.view",
    }
    return split_view, table


def _flowi_query_splittable_view_tool(args: dict[str, Any], product_hint: str, prompt: str, max_rows: int) -> dict[str, Any]:
    root = next((str(x).strip() for x in (args.get("root_lot_ids") or []) if str(x).strip()), "")
    fab = next((str(x).strip() for x in (args.get("fab_lot_ids") or []) if str(x).strip()), "")
    wafer_ids = ",".join(str(w) for w in (args.get("wafer_ids") or []) if str(w).strip())
    if not root and not fab:
        return {"handled": False}
    product_for_view = _flowi_splittable_product_id(product_hint or args.get("product") or "")
    if not product_for_view:
        return {"handled": False}
    prefixes = _flowi_splittable_prefixes_from_args(args, prompt)
    prefix_filter = ",".join(prefixes)
    custom_cols, custom_filter = _flowi_splittable_custom_cols_from_prompt(product_for_view, args, prompt, prefixes)
    saved_custom_name = _flowi_saved_custom_name_from_prompt(prompt)
    if saved_custom_name:
        # 저장된 custom SET 이 지목되면 ad-hoc 컬럼 추론보다 우선한다.
        custom_cols, custom_filter = [], ""
    custom_cols_param = ",".join(custom_cols)
    started = time.monotonic()
    try:
        from routers import splittable as splittable_router
        view = splittable_router.view_split(
            product=product_for_view,
            root_lot_id=root,
            wafer_ids=wafer_ids,
            prefix=prefix_filter,
            custom_name=saved_custom_name,
            view_mode="all",
            history_mode="all",
            fab_lot_id=fab,
            custom_cols=custom_cols_param,
            request=None,
        )
    except Exception:
        return {"handled": False}
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if not isinstance(view, dict):
        return {"handled": False}
    runtime_profile = view.get("runtime_profile") if isinstance(view.get("runtime_profile"), dict) else {}
    view_cache = view.get("view_cache") if isinstance(view.get("view_cache"), dict) else {}
    split_view, table = _flowi_splittable_view_to_inline(
        view,
        step=str(args.get("step") or ""),
        max_rows=max_rows,
        prefixes=prefixes,
    )
    intent = "splittable_view" if custom_cols else ("wafer_split_at_step" if args.get("step") else "splittable_view")
    action = "query_splittable_view" if custom_cols else ("query_wafer_split_at_step" if args.get("step") else "query_splittable_view")
    answer = (
        f"{product_for_view} {view.get('root_lot_id') or root or fab} SplitTable {prefix_filter} 기준으로 "
        f"{len(split_view.get('rows') or [])}개 row를 조회했습니다."
    )
    if custom_cols:
        answer = (
            f"{product_for_view} {view.get('root_lot_id') or root or fab} "
            f"{custom_filter} ad-hoc CUSTOM SET 기준으로 {len(split_view.get('rows') or [])}개 row를 조회했습니다."
        )
    if saved_custom_name:
        answer = (
            f"{product_for_view} {view.get('root_lot_id') or root or fab} "
            f"저장된 CUSTOM SET '{saved_custom_name}' 기준으로 {len(split_view.get('rows') or [])}개 row를 조회했습니다."
        )
    if not (split_view.get("rows") or []):
        answer = view.get("msg") or "SplitTable 화면 기준으로 표시할 split row를 찾지 못했습니다."
    if view.get("lot_warn"):
        answer += f" {view.get('lot_warn')}"
    return _flowi_set_inline_type({
        "handled": True,
        "intent": intent,
        "action": action,
        "answer": answer,
        "feature": "splittable",
        "split_view": split_view,
        "table": table,
        "filters": {
            "product": product_for_view,
            "root_lot_ids": [root] if root else [],
            "fab_lot_ids": [fab] if fab else [],
            "wafer_ids": args.get("wafer_ids") or [],
            "step": args.get("step") or "",
            "prefix": prefix_filter,
            "custom_set_filter": custom_filter if custom_cols else "",
            "custom_cols": custom_cols,
            "custom_name": saved_custom_name,
            "source": "splittable.view",
        },
        "splittable_view": view,
        "split_api": {
            "path": "/api/splittable/view",
            "callee": "routers.splittable.view_split",
            "method": "GET",
            "elapsed_ms": elapsed_ms,
            "status": "done",
            "custom_cols_count": len(custom_cols),
        },
        "runtime_profile": runtime_profile,
        "view_cache": view_cache,
        "elapsed_ms": elapsed_ms,
    }, "split_view", prompt=prompt)


def _handle_wafer_split_at_step(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    preview = _structure_flowi_function_call(prompt, product=product, max_rows=max_rows)
    if ((preview.get("selected_function") or {}).get("name") not in {"query_wafer_split_at_step", "query_splittable_view"}):
        return {"handled": False}
    args = ((preview.get("function_call") or {}).get("function") or {}).get("arguments") or {}
    missing = list((preview.get("validation") or {}).get("missing") or [])
    product_hint = str(args.get("product") or product or "")
    lots_for_product = _flowi_lot_scope_terms(
        args.get("root_lot_ids") or [],
        args.get("fab_lot_ids") or [],
        args.get("lot_ids") or [],
    )
    if "product" in missing:
        root_only = bool(args.get("root_lot_ids") or args.get("lot_ids")) and not bool(args.get("fab_lot_ids"))
        resolved_product, candidate_tool = _product_or_candidate_tool(
            prompt,
            product,
            lots_for_product,
            kinds=("ML_TABLE", "FAB"),
            intent="wafer_split_at_step",
            ask_if_any=root_only,
        )
        if candidate_tool:
            candidate_tool.setdefault("feature", "splittable")
            candidate_tool.setdefault("arguments", args)
            candidate_tool.setdefault("slots", {
                "product": "",
                "root_lot_ids": args.get("root_lot_ids") or [],
                "fab_lot_ids": args.get("fab_lot_ids") or [],
                "wafer_ids": args.get("wafer_ids") or [],
                "step": args.get("step") or "",
            })
            return candidate_tool
        if resolved_product:
            product_hint = resolved_product
            args["product"] = resolved_product
            missing = [m for m in missing if m != "product"]
    if missing:
        return _flowi_preview_tool(preview, answer="wafer split 조회에 필요한 값을 보완해 주세요.")
    view_tool = _flowi_query_splittable_view_tool(args, product_hint, prompt, max_rows)
    if view_tool.get("handled"):
        return view_tool
    files = _ml_files(product_hint)
    if not files:
        return {"handled": True, "intent": "wafer_split_at_step", "action": "query_wafer_split_at_step", "answer": "ML_TABLE parquet을 찾지 못했습니다.", "feature": "splittable"}
    try:
        lf = _scan_parquet(files)
        cols = _schema_names(lf)
        product_col = _ci_col(cols, "product", "PRODUCT")
        root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
        lot_col = _ci_col(cols, "lot_id", "LOT_ID")
        fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
        wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
        lot_wf_col = _ci_col(cols, "lot_wf", "LOT_WF")
        if product_hint and product_col:
            lf = lf.filter(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(_product_aliases(product_hint))))
        lot_expr = _flowi_lot_filter_expr(cols, args.get("root_lot_ids") or [], args.get("fab_lot_ids") or [])
        if lot_expr is not None:
            lf = lf.filter(lot_expr)
        step_expr = _flowi_step_filter_expr(cols, str(args.get("step") or ""))
        if step_expr is not None:
            lf = lf.filter(step_expr)
        wf_expr = _wafer_match_expr(wafer_col, [str(w) for w in args.get("wafer_ids") or []])
        if wf_expr is not None:
            lf = lf.filter(wf_expr)
        split_cols = [c for c in cols if _upper(c).startswith(("KNOB_", "MASK_"))]
        keep = [c for c in (product_col, root_col, lot_col, fab_col, wafer_col) if c] + split_cols[:40]
        df = lf.select([pl.col(c).cast(_STR, strict=False).alias(c) for c in keep]).limit(500).collect()
    except Exception as e:
        return {"handled": True, "intent": "wafer_split_at_step", "action": "query_wafer_split_at_step", "answer": f"wafer split 조회 실패: {e}", "feature": "splittable"}
    rows_raw = df.to_dicts()
    rows = []
    for row in rows_raw:
        for col in split_cols[:40]:
            val = _text(row.get(col))
            if not val:
                continue
            rows.append({
                "product": row.get(product_col) if product_col else product_hint,
                "root_lot_id": row.get(root_col) if root_col else "",
                "fab_lot_id": row.get(fab_col) if fab_col else "",
                "lot_id": row.get(lot_col) if lot_col else "",
                "wafer_id": row.get(wafer_col) if wafer_col else "",
                "step": args.get("step") or "",
                "parameter": col,
                "value": val,
            })
    cols_out = ["product", "root_lot_id", "fab_lot_id", "lot_id", "wafer_id", "step", "parameter", "value"]
    answer = f"{args.get('step')} 기준 wafer split 조합 {len(rows)}개를 찾았습니다." if rows else "조건에 맞는 wafer split 값을 찾지 못했습니다."
    wafer_headers = [w for w in dict.fromkeys(_normalize_wafer_id(r.get("wafer_id")) or _text(r.get("wafer_id")) for r in rows) if w]
    split_rows = []
    for param in dict.fromkeys(_text(r.get("parameter")) for r in rows if _text(r.get("parameter"))):
        cells = []
        for wf in wafer_headers:
            match = next((r for r in rows if _text(r.get("parameter")) == param and (_normalize_wafer_id(r.get("wafer_id")) or _text(r.get("wafer_id"))) == wf), {})
            cells.append({
                "wafer_id": wf,
                "actual": match.get("value") or "",
                "plan": "",
                "mismatch": False,
                "highlight": False,
            })
        split_rows.append({"parameter": param, "display": param.replace("KNOB_", "").replace("MASK_", ""), "cells": cells})
    return _flowi_set_inline_type({
        "handled": True,
        "intent": "wafer_split_at_step",
        "action": "query_wafer_split_at_step",
        "answer": answer,
        "feature": "splittable",
        "split_view": {"kind": "wafer_split_at_step", "title": "Wafer split at step", "headers": [f"#{w}" for w in wafer_headers], "rows": split_rows, "total": len(rows), "row_label": "KNOB/MASK"},
        "table": {"kind": "wafer_split_at_step", "title": "Wafer split at step", "placement": "below", "columns": _table_columns(cols_out), "rows": rows[:max(1, min(120, max_rows * 8))], "total": len(rows)},
        "filters": {"product": product_hint, "step": args.get("step"), "root_lot_ids": args.get("root_lot_ids"), "fab_lot_ids": args.get("fab_lot_ids"), "wafer_ids": args.get("wafer_ids")},
    }, "split_view", prompt=prompt)


def _handle_find_lots_by_knob_value(prompt: str, product: str, max_rows: int, *, infer_unique_product: bool = False) -> dict[str, Any]:
    preview = _structure_flowi_function_call(prompt, product=product, max_rows=max_rows)
    if ((preview.get("selected_function") or {}).get("name") != "find_lots_by_knob_value"):
        return {"handled": False}
    args = ((preview.get("function_call") or {}).get("function") or {}).get("arguments") or {}
    missing = (preview.get("validation") or {}).get("missing") or []
    if "product" in missing and infer_unique_product:
        inferred_product = _flowi_single_ml_product_hint(_ml_files(product or ""))
        if inferred_product:
            args = dict(args)
            args["product"] = inferred_product
            missing = [m for m in missing if m != "product"]
    if missing:
        answer = "KNOB value 역검색에 필요한 값을 보완해 주세요."
        if "product" in missing:
            answer = (
                "어느 product 기준인지 필요합니다. 제품명을 알려주면 SplitTable/ML_TABLE에서 해당 KNOB value의 LOT_WF를 찾고, "
                "latest progress cache에서 각 LOT_WF의 현재 step_id/function_step을 붙여 가장 앞선 후보를 계산합니다."
            )
        return _flowi_preview_tool(preview, answer=answer)
    product_hint = str(args.get("product") or product or "")
    knob_value = str(args.get("knob_value") or "")
    files = _ml_files(product_hint)
    if not files:
        return {"handled": True, "intent": "knob_value_lot_search", "action": "find_lots_by_knob_value", "answer": "ML_TABLE parquet을 찾지 못했습니다.", "feature": "splittable"}
    try:
        lf = _scan_parquet(files)
        cols = _schema_names(lf)
        product_col = _ci_col(cols, "product", "PRODUCT")
        root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
        lot_col = _ci_col(cols, "lot_id", "LOT_ID")
        fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
        wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
        lot_wf_col = _ci_col(cols, "lot_wf", "LOT_WF")
        if product_hint and product_col:
            lf = lf.filter(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(_product_aliases(product_hint))))
        step_expr = _flowi_step_filter_expr(cols, str(args.get("step") or ""))
        if step_expr is not None:
            lf = lf.filter(step_expr)
        knob_cols = [c for c in cols if _upper(c).startswith(("KNOB_", "MASK_"))]
        expr = None
        for col in knob_cols:
            piece = pl.col(col).cast(_STR, strict=False) == knob_value
            expr = piece if expr is None else (expr | piece)
        if expr is None:
            return {"handled": True, "intent": "knob_value_lot_search", "action": "find_lots_by_knob_value", "answer": "ML_TABLE에서 KNOB/MASK 컬럼을 찾지 못했습니다.", "feature": "splittable"}
        scoped = lf.filter(expr)
        keep = [c for c in (product_col, root_col, lot_col, fab_col, wafer_col, lot_wf_col) if c] + knob_cols
        exprs = [pl.col(c).cast(_STR, strict=False).alias(c) for c in keep]
        if not lot_wf_col and root_col and wafer_col:
            exprs.append(_lot_wf_expr(root_col, wafer_col).alias("lot_wf"))
        df = scoped.select(exprs).limit(10000).collect()
    except Exception as e:
        return {"handled": True, "intent": "knob_value_lot_search", "action": "find_lots_by_knob_value", "answer": f"KNOB value 역검색 실패: {e}", "feature": "splittable"}
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in df.to_dicts():
        for col in knob_cols:
            if _text(row.get(col)) != knob_value:
                continue
            root = _text(row.get(root_col)) if root_col else ""
            wafer = _text(row.get(wafer_col)) if wafer_col else ""
            lot_wf = _text(row.get(lot_wf_col)) if lot_wf_col else _text(row.get("lot_wf") or _flowi_lot_wf_id(root, wafer))
            key = (root, wafer, col)
            grouped[key] = {
                "product": _text(row.get(product_col)) if product_col else product_hint,
                "root_lot_id": root,
                "lot_id": _text(row.get(lot_col)) if lot_col else "",
                "fab_lot_id": _text(row.get(fab_col)) if fab_col else "",
                "wafer_id": wafer,
                "lot_wf": lot_wf,
                "step": args.get("step") or "",
                "knob": col,
                "knob_value": knob_value,
            }
    rows = list(grouped.values())
    progress_by_lot_wf = _flowi_progress_for_lot_rows(product_hint, rows, limit=1000)
    for row in rows:
        lot_wf = row.get("lot_wf") or _flowi_lot_wf_id(row.get("root_lot_id"), row.get("wafer_id"))
        fab = progress_by_lot_wf.get(lot_wf) or {}
        row["lot_wf"] = lot_wf
        row["current_step"] = fab.get("step_id") or ""
        row["current_func_step"] = fab.get("function_step") or fab.get("func_step") or ""
        row["current_lot_id"] = fab.get("lot_id") or row.get("lot_id") or ""
        row["current_fab_lot_id"] = fab.get("fab_lot_id") or row.get("fab_lot_id") or ""
        row["tkout_time"] = fab.get("update_time") or ""
        row["progress_source"] = fab.get("cache_source") or ""
        row["_rank"] = fab.get("step_rank") or _step_rank_key(row.get("current_step"))
    rows.sort(key=lambda r: (tuple(r.get("_rank") or (-1,)), str(r.get("tkout_time") or "")), reverse=True)
    for row in rows:
        row.pop("_rank", None)
    limit = max(1, min(100, int(args.get("limit") or max_rows or 10)))
    cols_out = ["product", "root_lot_id", "wafer_id", "lot_wf", "lot_id", "fab_lot_id", "step", "knob", "knob_value", "current_step", "current_func_step", "current_lot_id", "current_fab_lot_id", "tkout_time", "progress_source"]
    answer = f"{args.get('step')}에서 {knob_value} 값을 받은 lot/wafer {len(rows)}건을 FAB 진행 위치와 연결했습니다." if rows else f"{knob_value} 조건의 lot을 찾지 못했습니다."
    if rows and any(t in str(prompt or "") for t in ("가장 빠", "가장 빨", "제일 빠", "제일 빨", "빠른", "빨리", "앞선")):
        top = rows[0]
        answer = (
            f"{args.get('step')}에서 {knob_value} 값을 받은 WF 중 가장 앞선 후보는 "
            f"{top.get('lot_wf') or '-'} / step_id={top.get('current_step') or '-'}"
            f"{(' / function_step=' + top.get('current_func_step')) if top.get('current_func_step') else ''} 입니다."
        )
    lot_list = [
        {
            "product": r.get("product") or product_hint,
            "root_lot": r.get("root_lot_id") or "",
            "fab_lot": r.get("fab_lot_id") or r.get("lot_id") or "",
            "wafer": r.get("wafer_id") or "",
            "lot_wf": r.get("lot_wf") or "",
            "current_step": r.get("current_step") or r.get("current_func_step") or "",
            "current_function_step": r.get("current_func_step") or "",
            "tkout_time": r.get("tkout_time") or "",
            "knob": r.get("knob") or "",
            "knob_value": r.get("knob_value") or knob_value,
        }
        for r in rows[:limit]
    ]
    return _flowi_set_inline_type({
        "handled": True,
        "intent": "knob_value_lot_search",
        "action": "find_lots_by_knob_value",
        "answer": answer,
        "feature": "splittable",
        "lot_list": lot_list,
        "table": {"kind": "knob_value_lot_search", "title": "Lots by KNOB value", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in rows[:limit]], "total": len(rows)},
        "filters": {"product": product_hint, "step": args.get("step"), "knob_value": knob_value, "sort": args.get("sort") or "earliest_progress"},
        "slots": {"product": product_hint, "step": args.get("step") or "", "knob_value": knob_value, "source": "ML_TABLE+latest_progress_cache"},
        "term_resolution": [
            {"token": product_hint, "meaning": "SplitTable/ML_TABLE product", "wiki_refs": ["schema:product"], "query_filter": f"product={product_hint}", "status": "resolved"},
            {"token": args.get("step") or "", "meaning": "KNOB step/function_step 조건", "wiki_refs": ["schema:step_id", "schema:function_step", "schema:func_step"], "query_filter": f"step/function_step contains {args.get('step') or ''}", "status": "resolved"},
            {"token": knob_value, "meaning": "검색할 KNOB value", "wiki_refs": ["schema:KNOB_*"], "query_filter": f"any KNOB_/MASK_ column == {knob_value}", "status": "resolved"},
            {"token": "가장 빠른", "meaning": "latest progress cache의 step_id 순서가 가장 앞선 LOT_WF", "wiki_refs": ["schema:lot_wf", "schema:step_id", "schema:function_step"], "query_filter": "sort by current step_id rank desc; include function_step label", "status": "resolved"},
        ],
    }, "lot_list", prompt=prompt)


def _handle_metric_at_step(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    preview = _structure_flowi_function_call(prompt, product=product, max_rows=max_rows)
    if ((preview.get("selected_function") or {}).get("name") != "query_metric_at_step"):
        return {"handled": False}
    args = ((preview.get("function_call") or {}).get("function") or {}).get("arguments") or {}
    if (preview.get("validation") or {}).get("missing"):
        return _flowi_preview_tool(preview, answer="측정값 조회에 필요한 값을 보완해 주세요.")
    product_hint = str(args.get("product") or product or "")
    metric = str(args.get("metric") or "")
    agg = str(args.get("agg") or "median").lower()
    rows: list[dict[str, Any]] = []
    for source_type, files in (("ET", _et_files(product_hint)), ("INLINE", _inline_files(product_hint))):
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
            item_col = _ci_col(cols, "item_id", "ITEM_ID", "metric", "METRIC", "subitem_id", "SUBITEM_ID")
            value_col = _ci_col(cols, "value", "VALUE", "result", "RESULT")
            if product_hint and product_col:
                lf = lf.filter(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(_product_aliases(product_hint))))
            lot_expr = _flowi_lot_filter_expr(cols, args.get("root_lot_ids") or [], args.get("fab_lot_ids") or [])
            if lot_expr is not None:
                lf = lf.filter(lot_expr)
            wf_expr = _wafer_match_expr(wafer_col, [str(w) for w in args.get("wafer_ids") or []])
            if wf_expr is not None:
                lf = lf.filter(wf_expr)
            step_expr = _flowi_step_filter_expr(cols, str(args.get("step") or ""))
            if step_expr is not None:
                lf = lf.filter(step_expr)
            metric_cols = _column_matches(cols, [metric], include_knob_when_named=False)
            metric_col = next((c for c in metric_cols if c not in {product_col, root_col, lot_col, fab_col, wafer_col, step_col, item_col}), "")
            if metric_col:
                value_expr = pl.col(metric_col).cast(pl.Float64, strict=False).alias("value")
                item_expr = pl.lit(metric).alias("metric")
            elif item_col and value_col:
                matches = _match_values(_unique_strings(lf, item_col, limit=1000), [metric])
                if matches:
                    lf = lf.filter(pl.col(item_col).cast(_STR, strict=False).is_in(matches))
                value_expr = pl.col(value_col).cast(pl.Float64, strict=False).alias("value")
                item_expr = pl.col(item_col).cast(_STR, strict=False).alias("metric")
            else:
                continue
            exprs = [
                pl.lit(source_type).alias("source_type"),
                pl.col(product_col).cast(_STR, strict=False).alias("product") if product_col else pl.lit(product_hint).alias("product"),
                pl.col(root_col).cast(_STR, strict=False).alias("root_lot_id") if root_col else (
                    pl.col(lot_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id") if lot_col else pl.lit("").alias("root_lot_id")
                ),
                _wafer_key_expr(wafer_col).alias("wafer_id") if wafer_col else pl.lit("").alias("wafer_id"),
                pl.col(step_col).cast(_STR, strict=False).alias("step_id") if step_col else pl.lit(str(args.get("step") or "")).alias("step_id"),
                item_expr,
                value_expr,
            ]
            df = lf.select(exprs).drop_nulls(subset=["value"]).limit(100000).collect()
        except Exception as e:
            logger.warning("flowi metric at step failed source=%s: %s", source_type, e)
            continue
        if df.height == 0:
            continue
        group_cols = ["source_type", "product", "root_lot_id", "wafer_id", "step_id", "metric"]
        # _flowi_metric_agg 가 뽑은 max/p90/p10 등이 median 으로 뭉개지지 않게 공용 헬퍼로.
        agg_expr = _flowi_agg_polars_expr(agg if agg in _CHART_AGG_VALUES else "median", "value").alias("value")
        try:
            got = df.lazy().group_by(group_cols).agg([agg_expr, pl.len().alias("count")]).collect()
            rows.extend(got.to_dicts())
        except Exception:
            pass
    cols_out = ["source_type", "product", "root_lot_id", "wafer_id", "step_id", "metric", "value", "count"]
    answer = f"{args.get('step')} {metric} {agg} 집계 {len(rows)}건입니다." if rows else f"{args.get('step')} {metric} 측정값을 찾지 못했습니다."
    highlight = _flowi_wants_highlight(prompt)
    table_rows = [{k: r.get(k, "") for k in cols_out} for r in rows[:max(1, min(120, max_rows * 8))]]
    if highlight:
        for row in table_rows[:1]:
            row["__highlight"] = True
    return _flowi_set_inline_type({
        "handled": True,
        "intent": "metric_at_step_lookup",
        "action": "query_metric_at_step",
        "answer": answer,
        "feature": "filebrowser",
        "highlight": highlight,
        "table": {"kind": "metric_at_step", "title": "Metric at step", "placement": "below", "columns": _table_columns(cols_out), "rows": table_rows, "total": len(rows)},
        "filters": {"product": product_hint, "step": args.get("step"), "metric": metric, "agg": agg, "root_lot_ids": args.get("root_lot_ids"), "fab_lot_ids": args.get("fab_lot_ids"), "wafer_ids": args.get("wafer_ids")},
    }, "table", prompt=prompt, highlight=highlight)


def _flowi_splittable_plan_mismatch_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    has_plan = any(t in low or t in text for t in ("plan", "계획", "플랜"))
    has_wrong = _flowi_wants_highlight(text) or any(t in low or t in text for t in ("불일치", "mismatch", "다른", "달라"))
    has_view = any(t in low or t in text for t in ("보여", "찾", "조회", "확인", "show", "list"))
    return has_plan and has_wrong and (has_view or bool(_lot_tokens(text)))


def _flowi_plan_value(raw: Any) -> str:
    if isinstance(raw, dict):
        raw = raw.get("value")
    val = "" if raw is None else str(raw)
    return "" if val in {"None", "null"} else val


def _ml_lookup_lazy_for_lots(files: list[Path], lot_terms: list[str], group: str) -> tuple[pl.LazyFrame | None, list[str], dict[str, Any]]:
    if not files or not lot_terms:
        return None, [], {}
    fp = Path(files[0])
    try:
        status = ml_table_lookup.cache_status(fp)
    except Exception:
        return None, [], {}
    if not status.get("has_cache"):
        return None, [], status
    meta = status.get("meta") or {}
    schema = meta.get("schema") or {}
    cols = list(schema.keys())
    if not cols:
        return None, [], status
    group_u = str(group or "KNOB").upper()
    prefixes = (f"{group_u}_",) if group_u in {"KNOB", "MASK", "INLINE", "VM"} else ("KNOB_",)
    value_cols = [c for c in cols if _upper(c).startswith(prefixes)]
    keep = ml_table_lookup.identity_columns(cols) + [c for c in value_cols if c not in ml_table_lookup.identity_columns(cols)]
    if not keep:
        return None, [], status
    rows: list[dict[str, Any]] = []
    seen_roots: set[str] = set()
    for raw in lot_terms[:8]:
        root = str(raw or "").strip().upper()
        if "." in root:
            root = root.split(".", 1)[0]
        if not root or root in seen_roots:
            continue
        seen_roots.add(root)
        try:
            out = ml_table_lookup.query_root_lot(
                fp,
                root,
                selected_cols=keep,
                enqueue_missing=False,
            )
        except Exception:
            continue
        if out.get("lookup_cache_hit"):
            rows.extend(out.get("data") or [])
    if not rows:
        return None, cols, status
    try:
        return pl.DataFrame(rows).lazy(), keep, status
    except Exception:
        return None, cols, status


def _handle_splittable_plan_mismatch_query(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _flowi_splittable_plan_mismatch_intent(prompt):
        return {"handled": False}
    classified = _classified_lot_tokens(prompt)
    product_hint = _flowi_splittable_product_id(_product_hint(prompt, product))
    product_aliases = _product_aliases(product_hint) if product_hint else set()
    raw_lots = [*(classified.get("root_lot_ids") or []), *_lot_tokens(prompt)]
    lot_matches = [
        lot for lot in dict.fromkeys(_upper(x) for x in raw_lots if x)
        if lot not in product_aliases and not lot.startswith("PROD")
    ]
    root_lot_id = _upper((lot_matches or [""])[0])
    if "." in root_lot_id:
        root_lot_id = root_lot_id.split(".", 1)[0]
    product_choices: list[dict[str, Any]] = []
    if not product_hint and root_lot_id:
        candidates = _resolve_products_for_lots([root_lot_id], kinds=("ML_TABLE",), limit=6)
        seen_products = []
        for c in candidates:
            prod = _flowi_splittable_product_id(c.get("product") or "")
            if prod and prod not in seen_products:
                seen_products.append(prod)
        if len(seen_products) == 1:
            product_hint = seen_products[0]
        elif len(seen_products) > 1:
            product_choices = [
                {
                    "id": str(i + 1),
                    "label": str(i + 1),
                    "title": prod,
                    "value": prod,
                    "recommended": i == 0,
                    "description": f"{prod} SplitTable에서 mismatch를 조회",
                    "prompt": f"{prompt} {prod}",
                }
                for i, prod in enumerate(seen_products[:3])
            ]
    missing = []
    if not product_hint:
        missing.append("product")
    if not root_lot_id:
        missing.append("root_lot_id")
    if missing:
        tool = {
            "handled": True,
            "intent": "splittable_plan_mismatch",
            "action": "query_lot_knobs_from_ml_table",
            "answer": "SplitTable plan과 actual이 다른 셀을 보려면 product와 lot이 필요합니다.",
            "feature": "splittable",
            "missing": missing,
            "arguments_choices": _flowi_arguments_choices(missing, prompt, {"product": product_hint, "root_lot_ids": [root_lot_id] if root_lot_id else []}),
        }
        if product_choices:
            tool["clarification"] = {"question": "어느 SplitTable product에서 볼까요?", "choices": product_choices}
        return _flowi_set_inline_type(tool, "message", prompt=prompt, highlight=True)
    files = _ml_files(product_hint)
    if not files:
        return _flowi_set_inline_type({
            "handled": True,
            "intent": "splittable_plan_mismatch",
            "action": "query_lot_knobs_from_ml_table",
            "answer": f"{product_hint} ML_TABLE parquet을 찾지 못했습니다.",
            "feature": "splittable",
            "highlight": True,
        }, "message", prompt=prompt, highlight=True)
    try:
        lf = _scan_parquet(files)
        cols = _schema_names(lf)
        product_col = _ci_col(cols, "product", "PRODUCT")
        root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
        lot_col = _ci_col(cols, "lot_id", "LOT_ID")
        fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
        wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
        if product_hint and product_col:
            lf = lf.filter(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(_product_aliases(product_hint))))
        lot_expr = _or_contains([c for c in (root_col, lot_col, fab_col) if c], [root_lot_id])
        if lot_expr is not None:
            lf = lf.filter(lot_expr)
        split_cols = [c for c in cols if _upper(c).startswith(("KNOB_", "MASK_", "FAB_"))]
        plans = load_json(PATHS.data_root / "splittable" / f"{product_hint}.json", {}).get("plans", {})
        planned_cols = []
        planned_wafers = []
        if isinstance(plans, dict):
            for key, value in plans.items():
                parts = str(key or "").split("|")
                if len(parts) < 3 or _upper(parts[0]) != root_lot_id or not _flowi_plan_value(value):
                    continue
                wf = _normalize_wafer_id(parts[1])
                col = parts[2]
                if wf and col not in planned_cols:
                    planned_cols.append(col)
                if wf and wf not in planned_wafers:
                    planned_wafers.append(wf)
        selected_cols = [c for c in planned_cols if c in cols] or split_cols[:30]
        keep = [c for c in (product_col, root_col, lot_col, fab_col, wafer_col) if c] + [c for c in selected_cols if c in cols]
        if not keep:
            return {"handled": False}
        df = lf.select([pl.col(c).cast(_STR, strict=False).alias(c) for c in keep]).limit(5000).collect()
    except Exception as e:
        return _flowi_set_inline_type({
            "handled": True,
            "intent": "splittable_plan_mismatch",
            "action": "query_lot_knobs_from_ml_table",
            "answer": f"SplitTable mismatch 조회 실패: {e}",
            "feature": "splittable",
            "highlight": True,
        }, "message", prompt=prompt, highlight=True)
    rows: list[dict[str, Any]] = []
    for rec in df.to_dicts():
        root = _upper(_text(rec.get(root_col))) if root_col else root_lot_id
        if not root:
            root = root_lot_id
        wafer = _normalize_wafer_id(rec.get(wafer_col)) if wafer_col else ""
        if not wafer:
            continue
        for col in selected_cols:
            key = f"{root}|{wafer}|{col}"
            plan = _flowi_plan_value((plans or {}).get(key) if isinstance(plans, dict) else "")
            if not plan:
                continue
            actual = _flowi_plan_value(rec.get(col) if col in rec else "")
            status = "plan_only"
            mismatch = False
            if actual:
                mismatch = str(plan) != str(actual)
                status = "mismatch" if mismatch else "match"
            if status != "mismatch":
                continue
            rows.append({
                "product": rec.get(product_col) if product_col else product_hint,
                "root_lot_id": root,
                "fab_lot_id": rec.get(fab_col) if fab_col else "",
                "lot_id": rec.get(lot_col) if lot_col else "",
                "wafer_id": wafer,
                "parameter": col,
                "actual": actual,
                "plan": plan,
                "status": status,
                "__highlight": True,
            })
    rows = _sort_wafer_rows(rows)
    wafer_headers = [w for w in dict.fromkeys(r.get("wafer_id") for r in rows if r.get("wafer_id"))]
    split_rows = []
    for param in dict.fromkeys(r.get("parameter") for r in rows if r.get("parameter")):
        cells = []
        for wf in wafer_headers:
            match = next((r for r in rows if r.get("parameter") == param and r.get("wafer_id") == wf), {})
            cells.append({
                "wafer_id": wf,
                "actual": match.get("actual") or "",
                "plan": match.get("plan") or "",
                "mismatch": bool(match),
                "highlight": bool(match),
            })
        split_rows.append({"parameter": param, "display": str(param or "").replace("KNOB_", "").replace("MASK_", "").replace("FAB_", ""), "cells": cells})
    limit = max(1, min(120, max_rows * 8))
    answer = (
        f"{product_hint} {root_lot_id}에서 plan과 actual이 다른 셀 {len(rows)}개를 찾았습니다."
        if rows else f"{product_hint} {root_lot_id}에서 plan과 actual이 다른 셀을 찾지 못했습니다."
    )
    cols_out = ["product", "root_lot_id", "fab_lot_id", "lot_id", "wafer_id", "parameter", "actual", "plan", "status"]
    return _flowi_set_inline_type({
        "handled": True,
        "intent": "splittable_plan_mismatch",
        "action": "query_lot_knobs_from_ml_table",
        "answer": answer,
        "feature": "splittable",
        "highlight": True,
        "split_view": {
            "kind": "splittable_plan_mismatch",
            "title": "SplitTable plan mismatch",
            "headers": [f"#{w}" for w in wafer_headers],
            "rows": split_rows,
            "total": len(rows),
            "row_label": "KNOB/MASK",
        },
        "table": {"kind": "splittable_plan_mismatch", "title": "SplitTable plan mismatch", "placement": "below", "columns": _table_columns(cols_out), "rows": [{**{k: r.get(k, "") for k in cols_out}, "__highlight": True} for r in rows[:limit]], "total": len(rows), "highlight": True},
        "filters": {"product": product_hint, "root_lot_ids": [root_lot_id], "planned_wafers": planned_wafers},
    }, "split_view", prompt=prompt, highlight=True)


def _handle_filebrowser_data_preview(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    preview = _structure_flowi_function_call(prompt, product=product, max_rows=max_rows)
    if ((preview.get("selected_function") or {}).get("name") != "preview_filebrowser_data"):
        return {"handled": False}
    args = ((preview.get("function_call") or {}).get("function") or {}).get("arguments") or {}
    if (preview.get("validation") or {}).get("missing"):
        return _flowi_preview_tool(preview, answer="DB preview에 필요한 source/product를 보완해 주세요.")
    source_type = str(args.get("source_type") or "")
    product_hint = str(args.get("product") or product or "")
    limit = max(1, min(500, int(args.get("limit") or 100)))
    files = _flowi_source_files(source_type, product_hint)
    if not files:
        return {"handled": True, "intent": "filebrowser_data_preview", "action": "preview_filebrowser_data", "answer": f"{source_type} parquet을 찾지 못했습니다.", "feature": "filebrowser"}
    try:
        lf = _scan_parquet(files[:120])
        cols = _schema_names(lf)
        lot_expr = _flowi_lot_filter_expr(cols, args.get("root_lot_ids") or [], args.get("fab_lot_ids") or [])
        if lot_expr is not None:
            lf = lf.filter(lot_expr)
        wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
        wf_expr = _wafer_match_expr(wafer_col, [str(w) for w in args.get("wafer_ids") or []])
        if wf_expr is not None:
            lf = lf.filter(wf_expr)
        show_cols = cols[: min(18, len(cols))]
        df = lf.select([pl.col(c).cast(_STR, strict=False).alias(c) for c in show_cols]).limit(limit).collect()
    except Exception as e:
        return {"handled": True, "intent": "filebrowser_data_preview", "action": "preview_filebrowser_data", "answer": f"DB preview 실패: {e}", "feature": "filebrowser"}
    rows = df.to_dicts()
    return {
        "handled": True,
        "intent": "filebrowser_data_preview",
        "action": "preview_filebrowser_data",
        "answer": f"{source_type}/{product_hint} row {len(rows)}건을 read-only preview 했습니다.",
        "feature": "filebrowser",
        "table": {"kind": "filebrowser_data_preview", "title": f"{source_type} preview", "placement": "below", "columns": _table_columns(show_cols), "rows": rows, "total": len(rows), "source": source_type},
        "filters": {"source_type": source_type, "product": product_hint, "limit": limit},
    }


def _flowi_filebrowser_sql_prompt(prompt: str, product: str = "") -> bool:
    text = str(prompt or "")
    low = text.lower()
    semantic_condition = bool(
        re.search(r"(?:최근\s*\d+\s*일|(?:last|past|recent)\s*\d+\s*days?)", text, flags=re.I)
        or re.search(r"[A-Za-z_][A-Za-z0-9_.-]*\s*(?:에|에서)\s*[A-Za-z0-9_.#-]+\s*(?:이|가)?\s*(?:들어간|포함)", text, flags=re.I)
        or re.search(r"[A-Za-z0-9_.#-]+\s*(?:이|가)?\s*(?:들어간|포함된?)\s*(?:열|컬럼)", text, flags=re.I)
    )
    if not semantic_condition and not any(term in low or term in text for term in ("sql", "where", "filter", "필터", "조건", "컬럼 선택", "선택 컬럼")):
        return False
    return bool(
        "파일" in text
        or "filebrowser" in low
        or "db" in low
        or _source_terms(text)
        or _product_hint(text, "")
        or str(product or "").strip()
    )


def _handle_filebrowser_sql_llm_draft(prompt: str, product: str, max_rows: int, username: str = "") -> dict[str, Any]:
    if not _flowi_filebrowser_sql_prompt(prompt, product):
        return {"handled": False}
    source_terms = _source_terms(prompt)
    source_type = next((s for s in ("FAB", "INLINE", "ET", "VM", "EDS", "ML_TABLE") if s in source_terms), "")
    product_hint = _product_hint(prompt, product)
    missing = []
    if not source_type:
        missing.append("source_type")
    if source_type and source_type != "ML_TABLE" and not product_hint:
        missing.append("product")
    args = {"source_type": source_type, "product": product_hint}
    if missing:
        choices = _flowi_arguments_choices(missing, prompt, args)
        return {
            "handled": True,
            "intent": "filebrowser_sql_llm_draft",
            "action": "filebrowser.sql.llm.draft",
            "feature": "filebrowser",
            "answer": "FileBrowser SQL 초안에 필요한 source/product 조건을 보완해 주세요.",
            "missing": missing,
            "arguments": args,
            "arguments_choices": choices,
        }
    files = _flowi_source_files(source_type, product_hint)
    if not files:
        return {
            "handled": True,
            "intent": "filebrowser_sql_llm_draft",
            "action": "filebrowser.sql.llm.draft",
            "feature": "filebrowser",
            "answer": f"{source_type}/{product_hint or '-'} source 파일을 찾지 못했습니다.",
            "warnings": ["source files not found"],
            "arguments": args,
        }
    warnings: list[str] = []
    try:
        lf = _scan_parquet(files[:120])
        schema = lf.collect_schema()
        cols = schema.names() if hasattr(schema, "names") else _schema_names(lf)
        sample_df = lf.select([pl.col(c).cast(_STR, strict=False).alias(c) for c in cols[: min(40, len(cols))]]).limit(20).collect()
        sample_rows = sample_df.to_dicts()
        dtypes = {c: str(schema[c]) if c in schema else "unknown" for c in cols}
    except Exception as exc:
        return {
            "handled": True,
            "intent": "filebrowser_sql_llm_draft",
            "action": "filebrowser.sql.llm.draft",
            "feature": "filebrowser",
            "answer": f"FileBrowser SQL 초안 context 생성에 실패했습니다: {exc}",
            "warnings": [str(exc)],
            "arguments": args,
        }
    try:
        from routers import filebrowser as filebrowser_router
        draft = filebrowser_router._draft_filebrowser_ai_sql(
            natural_language=prompt,
            columns=cols,
            dtypes=dtypes,
            sample_rows=sample_rows,
            scope="",
            root=source_type,
            product=product_hint,
            file="",
            preferred_selected_columns=[],
            context_warnings=[],
        )
    except Exception as exc:
        draft = {
            "ok": False,
            "sql": "",
            "selected_columns": [],
            "warnings": [f"draft failed: {exc}"],
            "llm": {"available": llm_adapter.is_available(), "used": False, "error": str(exc)},
            "fallback": True,
        }
    sql = str(draft.get("sql") or "").strip()
    selected_columns = [str(c) for c in (draft.get("selected_columns") or []) if str(c) in cols]
    if not selected_columns:
        selected_columns = cols[: min(12, len(cols))]
    rows: list[dict[str, Any]] = []
    preview_error = ""
    try:
        preview_lf = lf
        if sql:
            from routers import filebrowser as filebrowser_router
            expr = filebrowser_router._lazy_filter_expr(sql, cols)
            if expr is not None:
                preview_lf = preview_lf.filter(expr)
        rows = preview_lf.select([pl.col(c).cast(_STR, strict=False).alias(c) for c in selected_columns]).limit(max(1, min(80, max_rows * 6))).collect().to_dicts()
    except Exception as exc:
        preview_error = str(exc)
        warnings.append(f"preview failed: {preview_error}")
    draft_warnings = [str(w) for w in (draft.get("warnings") or []) if str(w).strip()]
    warnings.extend(draft_warnings)
    answer = (
        f"{source_type}/{product_hint or '-'} 기준 FileBrowser SQL 초안을 만들고 preview {len(rows)}행을 확인했습니다."
        if draft.get("ok", True)
        else f"{source_type}/{product_hint or '-'} 기준 SQL 초안을 완성하지 못해 fallback 상태로 표시합니다."
    )
    sql_draft_payload = {
        "sql": sql,
        "selected_columns": selected_columns,
        "warnings": warnings,
        "fallback": bool(draft.get("fallback")) or not bool(draft.get("llm", {}).get("used")),
        "llm": draft.get("llm") or {},
        "resolved_columns": draft.get("resolved_columns") or [],
        "resolved_values": draft.get("resolved_values") or [],
        "sample_profile": draft.get("sample_profile") or {},
        "source_ids": [str(fp) for fp in files[:120]],
    }
    try:
        from routers import filebrowser as filebrowser_router
        filebrowser_router._record_filebrowser_ai_sql_history(
            username or "",
            source="home_flowi_sql_draft",
            request_payload={
                "natural_language": prompt,
                "scope": "db_product",
                "root": source_type,
                "product": product_hint,
                "file": "",
            },
            result_payload={
                "ok": bool(draft.get("ok", True)),
                "answer": answer,
                "sql": sql,
                "where_sql": draft.get("where_sql") or sql,
                "display_sql": draft.get("display_sql") or sql,
                "sort": draft.get("sort") or {},
                "aggregate": draft.get("aggregate") or {},
                "selected_columns": selected_columns,
                "warnings": warnings,
                "preview": {
                    "columns": selected_columns,
                    "rows": rows,
                    "total_rows": len(rows),
                    "preview_capped": False,
                },
                "tool": {"sql_draft": sql_draft_payload},
            },
        )
    except Exception:
        logger.debug("home Flow-i SQL draft history append failed", exc_info=True)
    return {
        "handled": True,
        "intent": "filebrowser_sql_llm_draft",
        "action": "filebrowser.sql.llm.draft",
        "feature": "filebrowser",
        "answer": answer,
        "arguments": args,
        "sql_draft": sql_draft_payload,
        "source_ids": [source_type, *[str(fp) for fp in files[:120]]],
        "table": {
            "kind": "filebrowser_sql_preview",
            "title": "FileBrowser SQL preview",
            "placement": "below",
            "columns": _table_columns(selected_columns),
            "rows": rows,
            "total": len(rows),
            "source": source_type,
            "source_files": [str(fp) for fp in files[:120]],
        },
        "warnings": warnings,
        "filters": {"source_type": source_type, "product": product_hint, "sql": sql, "selected_columns": selected_columns},
    }


def _handle_filebrowser_schema_search(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    preview = _structure_flowi_function_call(prompt, product=product, max_rows=max_rows)
    if ((preview.get("selected_function") or {}).get("name") != "search_filebrowser_schema"):
        return {"handled": False}
    args = ((preview.get("function_call") or {}).get("function") or {}).get("arguments") or {}
    if (preview.get("validation") or {}).get("missing"):
        return _flowi_preview_tool(preview, answer="schema 검색 keyword를 보완해 주세요.")
    keyword = str(args.get("keyword") or "")
    source_types = [str(args.get("source_type") or "").upper()] if args.get("source_type") else ["FAB", "ET", "INLINE", "VM", "EDS", "ML_TABLE"]
    rows: list[dict[str, Any]] = []
    for st in source_types:
        files = _flowi_source_files(st, product)
        if not files:
            continue
        try:
            cols = _schema_names(_scan_parquet(files[:20]))
        except Exception:
            continue
        for col in cols:
            if _upper(keyword) in _upper(col):
                rows.append({"source_type": st, "column": col, "file_count": len(files)})
    rows.sort(key=lambda r: (r.get("source_type") or "", r.get("column") or ""))
    cols_out = ["source_type", "column", "file_count"]
    answer = f"`{keyword}` schema 컬럼 후보 {len(rows)}개를 찾았습니다." if rows else f"`{keyword}` 컬럼 후보를 찾지 못했습니다."
    return {
        "handled": True,
        "intent": "filebrowser_schema_search",
        "action": "search_filebrowser_schema",
        "answer": answer,
        "feature": "filebrowser",
        "table": {"kind": "filebrowser_schema_search", "title": "Schema column search", "placement": "below", "columns": _table_columns(cols_out), "rows": rows[:max(1, min(120, max_rows * 8))], "total": len(rows)},
        "filters": {"keyword": keyword, "source_types": source_types},
    }


def _flowi_module_recipients(module: str) -> list[dict[str, Any]]:
    try:
        from routers import informs as informs_router
        rows = informs_router._module_recipient_rows(module)
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


def _flowi_build_mail_preview_for_draft(entry: dict[str, Any], username: str = "") -> dict[str, Any]:
    try:
        from routers import informs as informs_router
        recipients = _flowi_module_recipients(str(entry.get("module") or ""))
        subject = informs_router._default_mail_subject(entry)
        body = informs_router._default_mail_prose(entry, sender_username=username)
        return {
            "subject": subject,
            "body_text": body,
            "resolved_recipients": [r.get("email") for r in recipients if isinstance(r, dict) and r.get("email")],
            "auto_module_recipients": recipients,
            "auto_module_used": bool(recipients),
        }
    except Exception:
        return {"subject": "", "body_text": "", "resolved_recipients": [], "auto_module_recipients": [], "auto_module_used": False}


def _handle_compose_inform_module_mail(prompt: str, product: str, max_rows: int, me: dict[str, Any] | None = None) -> dict[str, Any]:
    preview = _structure_flowi_function_call(prompt, product=product, max_rows=max_rows)
    if ((preview.get("selected_function") or {}).get("name") != "compose_inform_module_mail"):
        return {"handled": False}
    args = ((preview.get("function_call") or {}).get("function") or {}).get("arguments") or {}
    missing = list((preview.get("validation") or {}).get("missing") or [])
    if missing:
        return _flowi_preview_tool(preview, answer="메일 미리보기에 필요한 값을 선택해 주세요.")
    username = (me or {}).get("username") or "user"
    lot_id = (args.get("fab_lot_ids") or args.get("root_lot_ids") or args.get("lot_ids") or [""])[0]
    entry = {
        "id": "dry_run",
        "product": args.get("product") or product,
        "module": args.get("module") or "",
        "reason": args.get("reason") or "Flow-i 메일 미리보기",
        "text": args.get("reason") or "",
        "root_lot_id": (args.get("root_lot_ids") or [""])[0],
        "lot_id": lot_id,
        "wafer_id": lot_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fab_lot_id_at_save": ", ".join(args.get("fab_lot_ids") or []),
    }
    mail_preview = _flowi_build_mail_preview_for_draft(entry, username=username)
    rows = [
        {"field": "product", "value": entry["product"]},
        {"field": "module", "value": entry["module"]},
        {"field": "lot", "value": lot_id},
        {"field": "recipients", "value": ", ".join(mail_preview.get("resolved_recipients") or [])},
        {"field": "subject", "value": mail_preview.get("subject") or ""},
        {"field": "policy", "value": "미리보기만 생성하며 발송은 별도 확인 후 진행"},
    ]
    return {
        "handled": True,
        "intent": "inform_module_mail_preview",
        "action": "compose_inform_module_mail",
        "answer": f"{entry['module']} 모듈 메일 미리보기입니다. 실제 발송은 하지 않았습니다.",
        "feature": "inform",
        "requires_confirmation": True,
        "side_effect": "confirm_before_write",
        "arguments": args,
        "mail_preview": mail_preview,
        "table": {"kind": "inform_mail_preview", "title": "Inform mail preview", "placement": "below", "columns": _table_columns(["field", "value"]), "rows": rows, "total": len(rows)},
    }


def _flowi_inform_session_path(session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(session_id or "")).strip("._-")
    if not safe:
        raise HTTPException(400, "session_id required")
    return FLOWI_INFORM_SESSION_DIR / f"{safe}.json"


def _flowi_cleanup_inform_sessions() -> None:
    try:
        FLOWI_INFORM_SESSION_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).timestamp()
        for fp in FLOWI_INFORM_SESSION_DIR.glob("*.json"):
            try:
                data = load_json(fp, {})
                ts = _parse_ts(data.get("last_active_at") or data.get("created_at"))
                if ts and now - ts.timestamp() > FLOWI_INFORM_SESSION_TTL_SECONDS:
                    fp.unlink(missing_ok=True)
            except Exception:
                continue
    except Exception:
        pass


def _flowi_save_inform_state(state: dict[str, Any]) -> dict[str, Any]:
    FLOWI_INFORM_SESSION_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    state = dict(state)
    state.setdefault("created_at", now)
    state["last_active_at"] = now
    save_json(_flowi_inform_session_path(str(state.get("session_id") or state.get("draft_id") or "")), state, indent=2)
    return state


def _flowi_load_inform_state(session_id: str) -> dict[str, Any]:
    _flowi_cleanup_inform_sessions()
    fp = _flowi_inform_session_path(session_id)
    data = load_json(fp, {})
    if not isinstance(data, dict) or not data:
        raise HTTPException(404, "inform session not found")
    return data


def _flowi_draft_id() -> str:
    return "draft_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:8]


def _flowi_inform_entry_preview(args: dict[str, Any], entry_args: dict[str, Any] | None = None) -> dict[str, Any]:
    entry_args = entry_args if isinstance(entry_args, dict) else {}
    lot = ""
    root_lots = [str(x) for x in (entry_args.get("root_lot_ids") or args.get("root_lot_ids") or []) if str(x or "").strip()]
    fab_lots = [str(x) for x in (entry_args.get("fab_lot_ids") or args.get("fab_lot_ids") or []) if str(x or "").strip()]
    entry_root = str(entry_args.get("root_lot_id") or "").strip()
    entry_fab = str(entry_args.get("fab_lot_id") or "").strip()
    if entry_root:
        root_lots = [entry_root]
    if entry_fab:
        fab_lots = [entry_fab]
    if fab_lots:
        lot = fab_lots[0]
    elif root_lots:
        lot = root_lots[0]
    wafer_ids = [str(x) for x in (entry_args.get("wafer_ids") or args.get("wafer_ids") or []) if str(x or "").strip()]
    module = str(entry_args.get("module") or args.get("module") or "").strip()
    split_set = str(entry_args.get("split_set") or args.get("split_set") or "").strip()
    note = str(entry_args.get("note") or args.get("note") or "").strip()
    reason = str(entry_args.get("reason") or args.get("reason") or split_set or "Flow-i 인폼").strip()
    recipients = entry_args.get("recipients") if isinstance(entry_args.get("recipients"), list) else args.get("recipients")
    recipients = [str(x).strip() for x in (recipients or []) if str(x).strip()]
    missing = []
    if not module:
        missing.append("module")
    return {
        "product": args.get("product") or "",
        "root_lot_id": root_lots[0] if root_lots else "",
        "fab_lot_id": fab_lots[0] if fab_lots else "",
        "lot_id": lot,
        "wafer_id": wafer_ids[0] if wafer_ids else lot,
        "module": module,
        "split_set": split_set,
        "reason": reason,
        "note": note,
        "recipients": recipients,
        "missing": missing,
    }


def _flowi_inform_lot_scopes(args: dict[str, Any]) -> list[dict[str, str]]:
    scopes: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    fab_values = [str(x).strip() for x in (args.get("fab_lot_ids") or []) if str(x).strip()]
    fab_roots = {fab.split(".", 1)[0][:5] for fab in fab_values if fab}
    for root in [str(x).strip() for x in (args.get("root_lot_ids") or []) if str(x).strip()]:
        if root in fab_roots:
            continue
        key = (root, "")
        if key not in seen:
            seen.add(key)
            scopes.append({"root_lot_id": root, "fab_lot_id": ""})
    for fab in fab_values:
        root = fab.split(".", 1)[0][:5] if fab else ""
        key = (root, fab)
        if key not in seen:
            seen.add(key)
            scopes.append({"root_lot_id": root, "fab_lot_id": fab})
    if not scopes:
        scopes.append({"root_lot_id": "", "fab_lot_id": ""})
    return scopes


def _flowi_expand_inform_entries(args: dict[str, Any], raw_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    base_entries = raw_entries if raw_entries else [{}]
    scopes = _flowi_inform_lot_scopes(args)
    entries: list[dict[str, Any]] = []
    for scope in scopes:
        for raw in base_entries:
            merged = {**(raw if isinstance(raw, dict) else {}), **scope}
            entries.append(_flowi_inform_entry_preview(args, merged))
    return entries


def _flowi_custom_set_columns(product: str, name: str) -> tuple[list[str], str]:
    """Resolve a saved SplitTable CUSTOM set by its exact display name."""
    wanted = str(name or "").strip().casefold()
    if not wanted:
        return [], ""
    try:
        from core import splittable_sets_cache
        rows = (splittable_sets_cache.list_sets(product) or {}).get("sets") or []
    except Exception:
        return [], ""
    for row in rows:
        if not isinstance(row, dict) or str(row.get("name") or "").strip().casefold() != wanted:
            continue
        columns = [str(value).strip() for value in (row.get("columns") or []) if str(value or "").strip()]
        return columns[:120], str(row.get("name") or name).strip()
    return [], ""


def _flowi_attach_inform_split_snapshots(entries: list[dict[str, Any]], product: str) -> None:
    """Attach the requested saved CUSTOM set as an Inform SplitTable snapshot."""
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("split_set"):
            continue
        columns, resolved_name = _flowi_custom_set_columns(product, str(entry.get("split_set") or ""))
        if not columns:
            entry["snapshot_status"] = "custom_set_not_found"
            entry["snapshot_warning"] = f"CUSTOM set not found: {entry.get('split_set')}"
            continue
        lot_id = str(entry.get("fab_lot_id") or entry.get("lot_id") or entry.get("root_lot_id") or "").strip()
        if not product or not lot_id:
            entry["snapshot_status"] = "missing_product_or_lot"
            continue
        try:
            from routers import informs as informs_router
            req = informs_router.SplitTableSnapshotReq(
                product=product,
                lot_id=lot_id,
                custom_cols=columns,
                is_fab_lot=bool(entry.get("fab_lot_id")),
                display_mode="matrix",
            )
            embed = informs_router._build_splittable_snapshot_embed(req)
            scope = embed.get("st_scope") if isinstance(embed.get("st_scope"), dict) else {}
            scope.update({"custom_name": resolved_name, "custom_columns": columns})
            embed["st_scope"] = scope
            entry["split_set"] = resolved_name
            entry["embed_table"] = embed
            entry["snapshot_status"] = "ready"
            entry["snapshot_source"] = "informs._build_splittable_snapshot_embed"
        except Exception as exc:
            entry["snapshot_status"] = "failed"
            entry["snapshot_warning"] = str(exc)[:500]


def _flowi_inform_snapshot_preview_table(entries: list[dict[str, Any]]) -> dict[str, Any]:
    entry = next((row for row in entries if isinstance(row, dict) and isinstance(row.get("embed_table"), dict)), None)
    if not entry:
        return {}
    embed = entry.get("embed_table") or {}
    raw_columns = embed.get("columns") if isinstance(embed.get("columns"), list) else []
    keys = [str(col.get("key") or col.get("label") or "") if isinstance(col, dict) else str(col) for col in raw_columns]
    keys = [key for key in keys if key]
    raw_rows = embed.get("rows") if isinstance(embed.get("rows"), list) else []
    rows: list[dict[str, Any]] = []
    for raw in raw_rows[:200]:
        if isinstance(raw, dict):
            rows.append({key: raw.get(key, "") for key in keys})
        elif isinstance(raw, list):
            rows.append({key: (raw[idx] if idx < len(raw) else "") for idx, key in enumerate(keys)})
    if not keys or not rows:
        return {}
    return {
        "kind": "inform_split_snapshot",
        "title": f"CUSTOM set {entry.get('split_set') or ''} SplitTable snapshot",
        "placement": "below",
        "columns": _table_columns(keys),
        "rows": rows,
        "total": len(raw_rows),
        "source": entry.get("snapshot_source") or "informs._build_splittable_snapshot_embed",
    }


def _flowi_save_inform_draft(args: dict[str, Any], entries: list[dict[str, Any]], username: str) -> dict[str, Any]:
    draft_id = _flowi_draft_id()
    state = {
        "kind": "inform_draft",
        "draft_id": draft_id,
        "session_id": draft_id,
        "username": username,
        "product": args.get("product") or "",
        "root_lot_ids": args.get("root_lot_ids") or [],
        "fab_lot_ids": args.get("fab_lot_ids") or [],
        "recipients": args.get("recipients") or [],
        "entries": entries,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return _flowi_save_inform_state(state)


def _flowi_create_inform_records_from_entries(state: dict[str, Any], me: dict[str, Any]) -> list[dict[str, Any]]:
    from routers import informs as informs_router
    username = me.get("username") or state.get("username") or "user"
    product = str(state.get("product") or "").strip()
    records = []
    items = informs_router._load_upgraded()
    now = informs_router._now()
    base_roots = [str(x) for x in (state.get("root_lot_ids") or []) if str(x or "").strip()]
    base_fabs = [str(x) for x in (state.get("fab_lot_ids") or []) if str(x or "").strip()]
    for entry in state.get("entries") or []:
        if not isinstance(entry, dict) or entry.get("missing"):
            continue
        lot = str(entry.get("lot_id") or entry.get("fab_lot_id") or entry.get("root_lot_id") or (base_fabs[0] if base_fabs else (base_roots[0] if base_roots else ""))).strip()
        wafer = str(entry.get("wafer_id") or lot).strip()
        root_lot = str(entry.get("root_lot_id") or (base_roots[0] if base_roots else "") or informs_router._root_lot_from_values(lot)).strip()
        fab_snapshot = (
            str(entry.get("fab_lot_id") or (base_fabs[0] if base_fabs else "")).strip()
            or informs_router._resolve_fab_lot_snapshot(product, lot or root_lot, wafer)
        )
        text = str(entry.get("note") or "").strip()
        if not text:
            bits = [str(entry.get("module") or "").strip()]
            if entry.get("split_set"):
                bits.append(f"split={entry.get('split_set')}")
            text = " ".join([b for b in bits if b]).strip() or "Flow-i 인폼"
        rec = {
            "id": informs_router._new_id(),
            "parent_id": None,
            "wafer_id": wafer,
            "lot_id": lot or root_lot,
            "root_lot_id": root_lot or informs_router._root_lot_from_values(lot),
            "product": product or str(entry.get("product") or ""),
            "module": str(entry.get("module") or "").strip(),
            "reason": str(entry.get("reason") or entry.get("split_set") or "Flow-i 인폼").strip(),
            "text": text,
            "author": username,
            "created_at": now,
            "checked": False,
            "checked_by": "",
            "checked_at": "",
            "flow_status": "received",
            "status_history": [{"status": "received", "actor": username, "at": now, "note": "created by Flow-i confirm"}],
            "splittable_change": None,
            "images": [],
            "embed_table": None,
            "auto_generated": False,
            "group_ids": [],
            "fab_lot_id_at_save": fab_snapshot,
        }
        if isinstance(entry.get("embed_table"), dict):
            rec["embed_table"] = deepcopy(entry.get("embed_table"))
        items.append(rec)
        records.append({"id": rec["id"], "module": rec["module"], "lot_id": rec["lot_id"], "root_lot_id": rec["root_lot_id"]})
    if records:
        informs_router._save(items)
    return records


def _flowi_confirm_inform_draft(draft_id: str, confirm: bool, me: dict[str, Any]) -> dict[str, Any]:
    state = _flowi_load_inform_state(draft_id)
    if not confirm:
        return {
            "handled": True,
            "intent": "inform_log_cancelled",
            "action": "cancel_inform_draft",
            "answer": "인폼 등록을 취소했습니다. 저장된 인폼은 없습니다.",
            "feature": "inform",
            "draft_id": draft_id,
        }
    missing_entries = [e for e in (state.get("entries") or []) if isinstance(e, dict) and e.get("missing")]
    if missing_entries:
        return {
            "handled": True,
            "intent": "inform_log_confirm_blocked",
            "action": "confirm_inform_draft",
            "blocked": True,
            "answer": "누락 항목이 있어 등록하지 않았습니다. module/split/note/recipients 선택지를 먼저 보완해 주세요.",
            "feature": "inform",
            "draft_id": draft_id,
            "entries": state.get("entries") or [],
        }
    records = _flowi_create_inform_records_from_entries(state, me)
    cols_out = ["id", "module", "lot_id", "root_lot_id"]
    mail_confirm_payload = {"inform_ids": [r["id"] for r in records], "confirm": True}
    mail_cancel_payload = {"inform_ids": [], "confirm": False}
    return {
        "handled": True,
        "intent": "inform_log_registered",
        "action": "confirm_inform_draft",
        "answer": f"인폼 {len(records)}건을 등록했습니다. 메일 발송은 아래에서 별도 확인한 경우에만 진행합니다.",
        "feature": "inform",
        "created_records": records,
        "clarification": {
            "question": "등록한 인폼을 모듈 담당자에게 메일로도 보낼까요? (발송 전 마지막 확인입니다)",
            "choices": [
                {
                    "id": "skip_inform_mail",
                    "label": "1",
                    "title": "보내지 않음",
                    "recommended": True,
                    "description": "메일 없이 인폼 등록만 유지합니다.",
                    "prompt": f"{_FLOWI_INFORM_MAIL_MARKER} {json.dumps(mail_cancel_payload, ensure_ascii=False)}",
                },
                {
                    "id": "send_inform_mail",
                    "label": "2",
                    "title": "메일 발송",
                    "description": f"{len(records)}건 인폼을 모듈 담당자 수신자에게 발송합니다.",
                    "prompt": f"{_FLOWI_INFORM_MAIL_MARKER} {json.dumps(mail_confirm_payload, ensure_ascii=False)}",
                },
            ],
        } if records else {},
        "table": {"kind": "inform_log_registered", "title": "Registered inform logs", "placement": "below", "columns": _table_columns(cols_out), "rows": records, "total": len(records)},
    }


def _extract_flowi_inform_confirm(prompt: str) -> dict[str, Any] | None:
    text = str(prompt or "").strip()
    if not text.startswith(_FLOWI_INFORM_CONFIRM_MARKER):
        return None
    raw = text[len(_FLOWI_INFORM_CONFIRM_MARKER):].strip()
    try:
        data = json.loads(raw)
    except Exception:
        return {"_parse_error": "invalid JSON"}
    return data if isinstance(data, dict) else {"_parse_error": "invalid JSON"}


def _extract_flowi_inform_mail_confirm(prompt: str) -> dict[str, Any] | None:
    text = str(prompt or "").strip()
    if not text.startswith(_FLOWI_INFORM_MAIL_MARKER + " ") and text != _FLOWI_INFORM_MAIL_MARKER:
        return None
    raw = text[len(_FLOWI_INFORM_MAIL_MARKER):].strip()
    try:
        # 클릭 흐름에서 마커 뒤에 추가 텍스트가 붙을 수 있어 앞쪽 JSON 만 파싱한다.
        data, _ = json.JSONDecoder().raw_decode(raw)
    except Exception:
        return {"_parse_error": "invalid JSON"}
    return data if isinstance(data, dict) else {"_parse_error": "invalid JSON"}


def _flowi_send_inform_mail_confirmed(payload: dict[str, Any], me: dict[str, Any]) -> dict[str, Any]:
    """메일 confirm 토큰을 받은 뒤에만 실행되는 인폼 메일 발송. 토큰 없이 호출되는 경로는 없다."""
    if not bool(payload.get("confirm")):
        return {
            "handled": True,
            "intent": "inform_mail_cancelled",
            "action": "cancel_inform_mail",
            "answer": "메일 발송을 취소했습니다. 보낸 메일은 없습니다.",
            "feature": "inform",
        }
    ids = [str(x).strip() for x in (payload.get("inform_ids") or []) if str(x or "").strip()]
    if not ids:
        return {
            "handled": True,
            "intent": "inform_mail_confirm_blocked",
            "blocked": True,
            "answer": "발송할 인폼 id가 없습니다.",
            "feature": "inform",
        }
    from routers import informs as informs_router
    sent, errors = [], []
    for iid in ids[:10]:
        try:
            res = informs_router._send_inform_mail_core(iid, informs_router.SendMailReq(), me)
            sent.append({
                "id": iid,
                "to": ", ".join(res.get("to") or []),
                "subject": res.get("subject") or "",
                "dry_run": "dry-run" if res.get("dry_run") else "",
            })
        except HTTPException as e:
            errors.append({"id": iid, "error": str(e.detail)})
        except Exception as e:
            errors.append({"id": iid, "error": str(e)})
    parts = []
    if sent:
        parts.append(f"인폼 메일 {len(sent)}건을 발송했습니다" + (" (dry-run 구성 검증)" if all(s.get("dry_run") for s in sent) else "") + ".")
    if errors:
        parts.append(f"{len(errors)}건은 보내지 못했습니다: " + "; ".join(f"{e['id']}: {e['error']}" for e in errors[:3]))
    rows = sent + [{"id": e["id"], "to": "", "subject": e["error"], "dry_run": "error"} for e in errors]
    return {
        "handled": True,
        "intent": "inform_mail_sent" if sent else "inform_mail_failed",
        "action": "send_inform_mail",
        "answer": " ".join(parts) or "발송 결과가 없습니다.",
        "feature": "inform",
        "mail_results": {"sent": sent, "errors": errors},
        "table": {"kind": "inform_mail_result", "title": "Inform mail result", "placement": "below", "columns": _table_columns(["id", "to", "subject", "dry_run"]), "rows": rows, "total": len(rows)},
    }


def _handle_flowi_register_inform_log(prompt: str, product: str, max_rows: int, me: dict[str, Any], allowed_keys: set[str] | None = None) -> dict[str, Any]:
    if _flowi_inform_summary_intent(prompt):
        return {"handled": False}
    payload = _extract_flowi_inform_confirm(prompt)
    if payload is not None:
        if payload.get("_parse_error"):
            return {"handled": True, "intent": "inform_log_confirm_failed", "blocked": True, "answer": "인폼 확인 payload를 읽지 못했습니다.", "feature": "inform"}
        return _flowi_confirm_inform_draft(str(payload.get("draft_id") or ""), bool(payload.get("confirm")), me)
    mail_payload = _extract_flowi_inform_mail_confirm(prompt)
    if mail_payload is not None:
        if mail_payload.get("_parse_error"):
            return {"handled": True, "intent": "inform_mail_confirm_failed", "blocked": True, "answer": "메일 확인 payload를 읽지 못했습니다.", "feature": "inform"}
        return _flowi_send_inform_mail_confirmed(mail_payload, me)
    preview = _structure_flowi_function_call(prompt, product=product, max_rows=max_rows)
    if ((preview.get("selected_function") or {}).get("name") != "register_inform_log"):
        return {"handled": False}
    if allowed_keys is not None and "inform" not in allowed_keys:
        return _flowi_permission_block("inform", me)
    args = ((preview.get("function_call") or {}).get("function") or {}).get("arguments") or {}
    missing = list((preview.get("validation") or {}).get("missing") or [])
    if missing:
        if len(missing) > 1:
            first_missing = missing[:1]
            preview = deepcopy(preview)
            validation = dict(preview.get("validation") or {})
            validation["missing"] = first_missing
            validation["valid"] = False
            preview["validation"] = validation
            preview["arguments_choices"] = _flowi_arguments_choices(first_missing, prompt, args)
            preview["missing_freetext"] = _flowi_missing_freetext(first_missing)
        return _flowi_preview_tool(preview, answer="인폼 등록 초안에 필요한 값을 선택해 주세요.")
    raw_entries = args.get("entries") if isinstance(args.get("entries"), list) else []
    entries = _flowi_expand_inform_entries(args, [e for e in raw_entries if isinstance(e, dict)])
    _flowi_attach_inform_split_snapshots(entries, str(args.get("product") or product or ""))
    draft = _flowi_save_inform_draft(args, entries, me.get("username") or "user")
    first_entry = entries[0] if entries else {}
    mail_preview = _flowi_build_mail_preview_for_draft({
        "id": draft.get("draft_id"),
        "product": args.get("product") or product,
        "module": first_entry.get("module") or "",
        "reason": first_entry.get("reason") or "",
        "text": first_entry.get("note") or "",
        "root_lot_id": first_entry.get("root_lot_id") or "",
        "lot_id": first_entry.get("lot_id") or first_entry.get("root_lot_id") or "",
        "wafer_id": first_entry.get("lot_id") or first_entry.get("root_lot_id") or "",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fab_lot_id_at_save": first_entry.get("fab_lot_id") or "",
    }, username=me.get("username") or "user") if first_entry.get("module") else {}
    cols_out = ["product", "root_lot_id", "fab_lot_id", "lot_id", "module", "split_set", "snapshot_status", "reason", "note", "recipients", "missing"]
    confirm_payload = {"draft_id": draft.get("draft_id"), "confirm": True}
    cancel_payload = {"draft_id": draft.get("draft_id"), "confirm": False}
    missing_entry_count = sum(1 for e in entries if e.get("missing"))
    answer = f"인폼 {len(entries)}건을 등록 전 미리보기로 만들었습니다. 확인 전에는 저장하지 않습니다."
    if missing_entry_count:
        answer += f" 누락 항목 {missing_entry_count}건은 보완이 필요합니다."
    inform_table = {"kind": "inform_log_draft", "title": "Inform log draft", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: (", ".join(v) if isinstance(v, list) else v) for k, v in e.items() if k in cols_out} for e in entries], "total": len(entries)}
    snapshot_table = _flowi_inform_snapshot_preview_table(entries)
    blocks = []
    if snapshot_table:
        blocks.append({"id": "inform-split-snapshot", "kind": "lot_table", "title": snapshot_table["title"], "payload": snapshot_table})
    blocks.append({"id": "inform-draft", "kind": "lot_table", "title": inform_table["title"], "payload": inform_table})
    return {
        "handled": True,
        "intent": "inform_log_batch_draft" if len(entries) > 1 else "inform_log_draft",
        "action": "register_inform_log",
        "answer": answer,
        "feature": "inform",
        "requires_confirmation": True,
        "side_effect": "confirm_before_write",
        "draft_id": draft.get("draft_id"),
        "arguments": args,
        "inform_preview": entries,
        "mail_preview": mail_preview,
        "sources": list(dict.fromkeys(
            [str(e.get("snapshot_source") or "") for e in entries if isinstance(e, dict) and e.get("snapshot_source")]
            + [f"CUSTOM set:{e.get('split_set')}" for e in entries if isinstance(e, dict) and e.get("snapshot_status") == "ready"]
        )),
        "blocks": blocks,
        "arguments_choices": _flowi_arguments_choices(["module"], prompt, args) if missing_entry_count else {},
        "clarification": {
            "question": "이대로 인폼을 등록할까요?",
            "choices": [
                {
                    "id": "confirm_inform",
                    "label": "1",
                    "title": "등록",
                    "recommended": True,
                    "description": f"{len(entries)}건을 실제 인폼 로그로 저장합니다.",
                    "prompt": f"{_FLOWI_INFORM_CONFIRM_MARKER} {json.dumps(confirm_payload, ensure_ascii=False)}",
                },
                {
                    "id": "cancel_inform",
                    "label": "2",
                    "title": "취소",
                    "description": "저장하지 않습니다.",
                    "prompt": f"{_FLOWI_INFORM_CONFIRM_MARKER} {json.dumps(cancel_payload, ensure_ascii=False)}",
                },
            ],
        } if not missing_entry_count else {},
        "table": inform_table,
    }


def _flowi_active_walkthrough_session(agent_context: dict[str, Any] | None) -> str:
    for msg in reversed(_flowi_context_messages(agent_context)):
        slots = msg.get("slots") if isinstance(msg.get("slots"), dict) else {}
        sid = slots.get("session_id") or slots.get("inform_session_id")
        if sid:
            return str(sid)
        workflow = msg.get("workflow_state") if isinstance(msg.get("workflow_state"), dict) else {}
        wslots = workflow.get("slots") if isinstance(workflow.get("slots"), dict) else {}
        sid = wslots.get("session_id") or wslots.get("inform_session_id")
        if sid:
            return str(sid)
    return ""


def _flowi_walkthrough_response(state: dict[str, Any], answer: str = "") -> dict[str, Any]:
    current = str(state.get("current_module") or "")
    entries = state.get("entries") if isinstance(state.get("entries"), list) else []
    remaining = state.get("modules_remaining") if isinstance(state.get("modules_remaining"), list) else []
    choices = _flowi_split_set_choice_values(3)
    split_question = f"{current}의 SplitTable은 어떤 Split으로 진행할까요?" if current else "이대로 등록할까요?"
    tool = {
        "handled": True,
        "intent": "inform_walkthrough",
        "action": "register_inform_walkthrough",
        "answer": answer or (split_question if current else f"현재 {len(entries)}개 entry가 있습니다. 이대로 등록할까요?"),
        "feature": "inform",
        "requires_confirmation": True,
        "side_effect": "confirm_before_write",
        "session_id": state.get("session_id"),
        "walkthrough": {
            "session_id": state.get("session_id"),
            "current_module": current,
            "entries": entries,
            "modules_remaining": remaining,
            "next_question": split_question,
        },
        "slots": {
            "session_id": state.get("session_id"),
            "product": state.get("product") or "",
            "root_lot_ids": state.get("root_lot_ids") or [],
            "fab_lot_ids": state.get("fab_lot_ids") or [],
            "current_module": current,
        },
        "arguments_choices": {
            "message": "또는 직접 입력해 주세요",
            "fields": [{
                "field": "split_set",
                "choices": [
                    _flowi_choice("split_set", i + 1, f"{v}로 진행", v, prompt_prefix="")
                    for i, v in enumerate(choices)
                ] + [{"id": "free", "label": "직접", "title": "직접 입력", "value": "", "free_input": True, "description": "split/note를 자유 입력합니다.", "prompt": ""}],
            }],
        } if current else {},
    }
    if not current and entries:
        payload = {"session_id": state.get("session_id"), "confirm": True}
        tool["clarification"] = {
            "question": f"현재 {len(entries)}개 entry를 등록할까요?",
            "choices": [{
                "id": "confirm_walkthrough",
                "label": "1",
                "title": "등록",
                "recommended": True,
                "description": "현재 entry를 일괄 등록합니다.",
                "prompt": f"{_FLOWI_INFORM_WALKTHROUGH_MARKER} {json.dumps(payload, ensure_ascii=False)}",
            }],
        }
    return tool


def _flowi_start_walkthrough(args: dict[str, Any], me: dict[str, Any]) -> dict[str, Any]:
    modules = _flowi_inform_modules()
    root_lot_ids = [str(x).strip() for x in (args.get("root_lot_ids") or []) if str(x).strip()]
    fab_lot_ids = [str(x).strip() for x in (args.get("fab_lot_ids") or []) if str(x).strip()]
    product = str(args.get("product") or "").strip()
    if not product:
        lots_for_product = root_lot_ids + fab_lot_ids
        candidates = _resolve_products_for_lots(lots_for_product, kinds=("FAB", "ML_TABLE"), limit=4) if lots_for_product else []
        products = []
        seen: set[str] = set()
        for row in candidates:
            prod = str(row.get("product") or "").strip()
            if prod and prod not in seen:
                seen.add(prod)
                products.append(prod)
        if len(products) == 1:
            product = products[0]
    state = {
        "kind": "inform_walkthrough",
        "session_id": "walk_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:8],
        "username": me.get("username") or "user",
        "root_lot_ids": root_lot_ids,
        "fab_lot_ids": fab_lot_ids,
        "product": product,
        "modules_remaining": modules[1:],
        "current_module": modules[0] if modules else "",
        "entries": [],
    }
    _flowi_save_inform_state(state)
    return _flowi_walkthrough_response(state, f"{state['current_module']}의 SplitTable은 어떤 Split으로 진행할까요? (예: test1)")


def _flowi_walkthrough_next_module(state: dict[str, Any]) -> None:
    remaining = list(state.get("modules_remaining") or [])
    state["current_module"] = remaining.pop(0) if remaining else ""
    state["modules_remaining"] = remaining


def _flowi_resolve_walkthrough_state(state: dict[str, Any], prompt: str, me: dict[str, Any]) -> dict[str, Any]:
    text = str(prompt or "").strip()
    low = text.lower()
    if not state.get("current_module") and any(t in text for t in ("응", "등록", "확인", "이대로")):
        tmp_state = dict(state)
        tmp_state["kind"] = "inform_draft"
        tmp_state["draft_id"] = str(state.get("session_id") or "")
        return _flowi_confirm_inform_draft(str(state.get("session_id") or ""), True, me)
    if any(t in low or t in text for t in ("끝", "그만", "이대로 등록", "finalize")):
        state["current_module"] = ""
        state["modules_remaining"] = []
        _flowi_save_inform_state(state)
        return _flowi_walkthrough_response(state, f"현재 {len(state.get('entries') or [])}개 entry입니다. 이대로 등록할까요?")
    jump_module = ""
    for module, alias in _flowi_module_alias_pairs():
        if re.search(rf"{re.escape(alias)}\s*도", text, flags=re.I):
            jump_module = module
            break
    if jump_module:
        state["current_module"] = jump_module
    current = str(state.get("current_module") or "")
    if not current:
        return _flowi_walkthrough_response(state)
    note = _flowi_note_extract(text)
    split_values = []
    split = _flowi_split_set_token(text)
    if split:
        split_values.append(split)
    else:
        clean = re.sub(r"(로|으로)?\s*(해줘|해주세요|할게|진행|선택).*", "", text).strip()
        clean = re.sub(r"(그리고|,|/)", " ", clean)
        for tok in clean.split():
            if tok and tok not in {"이건", "일단", "생략할게", "넘어가", "안", "해"} and not _flowi_module_token(tok):
                split_values.append(tok.strip())
    if any(t in low or t in text for t in ("생략", "skip", "넘어가", "안 해", "안해", "비우고", "빈값")) and not split_values:
        _flowi_walkthrough_next_module(state)
        _flowi_save_inform_state(state)
        return _flowi_walkthrough_response(state)
    lot_scopes = _flowi_inform_lot_scopes(state)
    for split_val in split_values[:4]:
        for scope in lot_scopes:
            root = str(scope.get("root_lot_id") or "").strip()
            fab = str(scope.get("fab_lot_id") or "").strip()
            lot_id = fab or root
            state.setdefault("entries", []).append({
                "product": state.get("product") or "",
                "root_lot_id": root,
                "fab_lot_id": fab,
                "lot_id": lot_id,
                "module": current,
                "split_set": split_val,
                "reason": split_val,
                "note": note,
                "missing": [],
            })
    if split_values:
        _flowi_walkthrough_next_module(state)
    _flowi_save_inform_state(state)
    return _flowi_walkthrough_response(state)


def _extract_flowi_walkthrough_payload(prompt: str) -> dict[str, Any] | None:
    text = str(prompt or "").strip()
    if not text.startswith(_FLOWI_INFORM_WALKTHROUGH_MARKER):
        return None
    try:
        data = json.loads(text[len(_FLOWI_INFORM_WALKTHROUGH_MARKER):].strip())
    except Exception:
        return {"_parse_error": "invalid JSON"}
    return data if isinstance(data, dict) else {"_parse_error": "invalid JSON"}


def _handle_flowi_inform_walkthrough_chat(prompt: str, product: str, max_rows: int, me: dict[str, Any], agent_context: dict[str, Any] | None = None, allowed_keys: set[str] | None = None) -> dict[str, Any]:
    if allowed_keys is not None and "inform" not in allowed_keys:
        preview = _structure_flowi_function_call(prompt, product=product, max_rows=max_rows)
        if ((preview.get("selected_function") or {}).get("name") == "register_inform_walkthrough"):
            return _flowi_permission_block("inform", me)
    payload = _extract_flowi_walkthrough_payload(prompt)
    if payload is not None:
        if payload.get("_parse_error"):
            return {"handled": True, "intent": "inform_walkthrough_failed", "blocked": True, "answer": "walkthrough 확인 payload를 읽지 못했습니다.", "feature": "inform"}
        sid = str(payload.get("session_id") or "")
        if payload.get("confirm"):
            return _flowi_confirm_inform_draft(sid, True, me)
        state = _flowi_load_inform_state(sid)
        return _flowi_resolve_walkthrough_state(state, str(payload.get("value") or ""), me)
    active_sid = _flowi_active_walkthrough_session(agent_context)
    if active_sid:
        state = _flowi_load_inform_state(active_sid)
        if state.get("kind") == "inform_walkthrough":
            return _flowi_resolve_walkthrough_state(state, prompt, me)
    preview = _structure_flowi_function_call(prompt, product=product, max_rows=max_rows)
    if ((preview.get("selected_function") or {}).get("name") != "register_inform_walkthrough"):
        return {"handled": False}
    args = ((preview.get("function_call") or {}).get("function") or {}).get("arguments") or {}
    if (preview.get("validation") or {}).get("missing"):
        return _flowi_preview_tool(preview, answer="인폼 전체 작성에 필요한 root lot을 알려주세요.")
    return _flowi_start_walkthrough(args, me)


def _flowi_context_product_hint(agent_context: dict[str, Any] | None) -> str:
    for msg in reversed(_flowi_context_messages(agent_context)):
        containers: list[dict[str, Any]] = []
        for key in ("filters", "slots", "arguments_partial", "arguments"):
            value = msg.get(key)
            if isinstance(value, dict):
                containers.append(value)
        workflow = msg.get("workflow_state") if isinstance(msg.get("workflow_state"), dict) else {}
        for key in ("slots", "filters", "arguments"):
            value = workflow.get(key)
            if isinstance(value, dict):
                containers.append(value)
        for container in containers:
            products = _flowi_context_values(container.get("product"))
            for product in products:
                if _upper(product).startswith("ML_TABLE_"):
                    return _upper(product)
            if products:
                return _upper(products[0])
    return ""


def _flowi_context_values(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple, set)):
        raw = value
    else:
        text = str(value or "").strip()
        if not text:
            return []
        raw = re.split(r"[,/]+", text) if ("," in text or "/" in text) else [text]
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item or "").strip()
        if not text:
            continue
        key = _upper(text)
        if key and key not in seen:
            seen.add(key)
            out.append(text)
    return out


def _flowi_merge_context_dicts(*items: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            if value in (None, "", [], {}):
                continue
            out.setdefault(str(key), value)
    return out


def _flowi_recent_tool_anchor(agent_context: dict[str, Any] | None) -> dict[str, Any]:
    for msg in reversed(_flowi_context_messages(agent_context)[-10:]):
        workflow = msg.get("workflow_state") if isinstance(msg.get("workflow_state"), dict) else {}
        outputs = msg.get("output_summary") if isinstance(msg.get("output_summary"), dict) else {}
        workflow_outputs = workflow.get("outputs") if isinstance(workflow.get("outputs"), dict) else {}
        table_summary = outputs.get("table") if isinstance(outputs.get("table"), dict) else {}
        if not table_summary:
            table_summary = workflow_outputs.get("table") if isinstance(workflow_outputs.get("table"), dict) else {}
        filters = _flowi_merge_context_dicts(
            workflow.get("filters") if isinstance(workflow.get("filters"), dict) else {},
            msg.get("filters") if isinstance(msg.get("filters"), dict) else {},
        )
        slots = _flowi_merge_context_dicts(
            workflow.get("slots") if isinstance(workflow.get("slots"), dict) else {},
            msg.get("slots") if isinstance(msg.get("slots"), dict) else {},
            msg.get("arguments_partial") if isinstance(msg.get("arguments_partial"), dict) else {},
            msg.get("arguments") if isinstance(msg.get("arguments"), dict) else {},
        )
        anchor = {
            "feature": str(msg.get("feature") or workflow.get("feature") or "").strip(),
            "action": str(msg.get("action") or workflow.get("action") or "").strip(),
            "intent": str(msg.get("intent") or workflow.get("intent") or "").strip(),
            "filters": filters,
            "slots": slots,
            "table_kind": str(msg.get("table_kind") or table_summary.get("kind") or "").strip(),
            "split_view_kind": str(msg.get("split_view_kind") or "").strip(),
            "answer": str(msg.get("answer_excerpt") or msg.get("text") or msg.get("answer") or "").strip(),
            "prompt": str(msg.get("prompt") or "").strip(),
            # 이미 사용자에게 내려간 직전 표의 제한된 스냅샷이다. 후속 질문에서
            # "raw data 줘"처럼 대상을 생략해도 같은 결과를 다시 표로 펼칠 수 있다.
            # 새 DB 조회 권한을 만드는 값이 아니라 클라이언트가 이미 본 행만 echo 한다.
            "result_table": msg.get("result_table") if isinstance(msg.get("result_table"), dict) else {},
            "result_sources": [str(v) for v in (msg.get("result_sources") or []) if str(v or "").strip()][:16],
        }
        if any(anchor.get(k) for k in ("feature", "action", "intent", "table_kind", "split_view_kind", "answer")) or filters or slots:
            return anchor
    return {}


def _flowi_raw_table_followup_intent(prompt: str, anchor: dict[str, Any]) -> bool:
    """직전 비차트 표를 그대로 펼쳐 달라는 짧은 후속 질문인지 판별한다."""
    table = anchor.get("result_table") if isinstance(anchor.get("result_table"), dict) else {}
    if not table or not isinstance(table.get("rows"), list):
        return False
    # Dashboard chart는 session 기반 재조회/CSV 계약이 따로 있으므로 기존 전용
    # handler가 처리한다. 여기서는 lot/WIP/SplitTable/검색 표만 이어받는다.
    if str(anchor.get("feature") or "").strip().lower() == "dashboard":
        return False
    text = re.sub(r"\s+", " ", str(prompt or "")).strip()
    low = text.lower()
    wants_raw = any(term in low or term in text for term in (
        "raw data", "rawdata", "raw", "원본 데이터", "로우데이터", "상세 데이터",
        "전체 데이터", "값 줘", "값 보여", "데이터 줘", "데이터 보여", "표로 보여",
    ))
    if not wants_raw:
        return False
    # 긴 독립 질의가 우연히 raw라는 단어를 포함한 경우 직전 결과로 오인하지 않는다.
    followup_hint = len(text) <= 48 or any(term in text for term in ("그거", "그걸", "아까", "직전", "방금", "같은 조건"))
    return followup_hint


def _handle_flowi_raw_table_followup(
    prompt: str,
    agent_context: dict[str, Any] | None,
    max_rows: int,
) -> dict[str, Any]:
    """직전 표 스냅샷을 출처와 함께 다시 제공한다."""
    anchor = _flowi_recent_tool_anchor(agent_context)
    if not _flowi_raw_table_followup_intent(prompt, anchor):
        return {"handled": False}
    source_table = anchor.get("result_table") if isinstance(anchor.get("result_table"), dict) else {}
    raw_rows = [row for row in (source_table.get("rows") or []) if isinstance(row, dict)]
    limit = max(1, min(int(max_rows or 12), 100))
    rows = raw_rows[:limit]
    raw_columns = source_table.get("columns") if isinstance(source_table.get("columns"), list) else []
    columns = []
    for col in raw_columns[:32]:
        if isinstance(col, dict):
            key = str(col.get("key") or col.get("field") or col.get("name") or "").strip()
            if key:
                columns.append({"key": key, "label": str(col.get("label") or key)})
        elif str(col or "").strip():
            key = str(col).strip()
            columns.append({"key": key, "label": key})
    if not columns and rows:
        columns = _table_columns(list(rows[0].keys())[:32])
    sources = [str(v) for v in (anchor.get("result_sources") or []) if str(v or "").strip()]
    if not sources:
        sources = ["직전 Flow-i 조회 결과"]
    total = int(source_table.get("total") or len(raw_rows))
    return {
        "handled": True,
        "type": "table",
        "intent": "context_raw_table_followup",
        "action": "reuse_previous_table_result",
        "feature": str(anchor.get("feature") or "home"),
        "unit_ai": "conversation_context",
        "answer": f"직전 조회 조건을 이어받아 raw data {len(rows):,}행을 표시합니다. 원 조회 결과는 총 {total:,}행입니다.",
        "context_followup": True,
        "table": {
            "kind": "context_raw_data",
            "title": f"Raw data · {source_table.get('title') or '직전 조회'}",
            "placement": "below",
            "columns": columns,
            "rows": rows,
            "total": total,
            "source": sources[0],
        },
        "source": sources[0],
        "sources": sources,
        "source_detail": {
            "kind": "conversation_result_snapshot",
            "source": sources[0],
            "note": "직전 턴에서 사용자에게 이미 제공된 표 행을 재사용",
        },
        "filters": anchor.get("filters") if isinstance(anchor.get("filters"), dict) else {},
        "slots": anchor.get("slots") if isinstance(anchor.get("slots"), dict) else {},
    }


def _flowi_anchor_supports_splittable(anchor: dict[str, Any]) -> bool:
    if not anchor:
        return False
    feature = str(anchor.get("feature") or "")
    action = str(anchor.get("action") or "")
    intent = str(anchor.get("intent") or "")
    filters = anchor.get("filters") if isinstance(anchor.get("filters"), dict) else {}
    slots = anchor.get("slots") if isinstance(anchor.get("slots"), dict) else {}
    text = _upper(" ".join([
        feature,
        action,
        intent,
        str(anchor.get("table_kind") or ""),
        str(anchor.get("split_view_kind") or ""),
        str(filters.get("source") or ""),
        str(filters.get("source_type") or ""),
        " ".join(_flowi_context_values(filters.get("product"))),
        " ".join(_flowi_context_values(slots.get("product"))),
    ]))
    if feature == "splittable":
        return True
    if action in {"query_wafer_split_at_step", "query_splittable_view", "query_lot_knobs_from_ml_table", "find_lots_by_knob_value"}:
        return True
    return "SPLITTABLE" in text or "SPLITTABLE_VIEW" in text or "ML_TABLE" in text


def _flowi_splittable_followup_requested(prompt: str, anchor: dict[str, Any]) -> bool:
    text = str(prompt or "")
    low = text.lower()
    explicit = any(term in low or term in text for term in (
        "스플릿테이블",
        "스플릿 테이블",
        "split table",
        "splittable",
        "pivot",
        "피벗",
        "wafer table",
        "웨이퍼 테이블",
        "wafer별 표",
        "웨이퍼별 표",
    ))
    if explicit:
        return True
    if not _flowi_anchor_supports_splittable(anchor):
        return False
    same_context = any(term in text for term in ("아까", "이전", "직전", "같은 조건")) or "same condition" in low
    rerun = any(term in text for term in ("다시", "보여", "조회", "확인", "형태", "표로", "테이블로"))
    return same_context and rerun


def _flowi_anchor_product(anchor: dict[str, Any], prompt: str, product: str = "") -> str:
    prompt_product = _product_hint(prompt, product)
    if prompt_product:
        return prompt_product
    filters = anchor.get("filters") if isinstance(anchor.get("filters"), dict) else {}
    slots = anchor.get("slots") if isinstance(anchor.get("slots"), dict) else {}
    candidates: list[str] = []
    for value in (filters.get("product"), slots.get("product"), slots.get("products")):
        candidates.extend(_flowi_context_values(value))
    for value in candidates:
        if _upper(value).startswith("ML_TABLE_"):
            return _upper(value)
    return _upper(candidates[0]) if candidates else ""


def _flowi_add_anchor_lot_values(roots: list[str], fabs: list[str], values: Any) -> None:
    seen_roots = {_upper(v) for v in roots}
    seen_fabs = {_upper(v) for v in fabs}

    def add_root(value: Any) -> None:
        root = _upper(value)
        if root and root not in seen_roots:
            seen_roots.add(root)
            roots.append(root)

    def add_fab(value: Any) -> None:
        fab = _upper(value)
        if fab and fab not in seen_fabs:
            seen_fabs.add(fab)
            fabs.append(fab)
        root = _flowi_root_from_fab_lot(fab)
        if root:
            add_root(root)

    for value in _flowi_context_values(values):
        classified = _classified_lot_tokens(value)
        for root in classified.get("root_lot_ids") or []:
            add_root(root)
        for fab in classified.get("fab_lot_ids") or []:
            add_fab(fab)
        if classified.get("root_lot_ids") or classified.get("fab_lot_ids"):
            continue
        key = _upper(value)
        if _is_fab_lot_token(key) or "." in key:
            add_fab(key)
        elif _is_root_lot_token(key) or re.fullmatch(r"[A-Z]\d{4,}(?:[A-Z])?", key):
            add_root(key)


def _flowi_anchor_splittable_args(anchor: dict[str, Any], prompt: str, product: str = "") -> dict[str, Any]:
    filters = anchor.get("filters") if isinstance(anchor.get("filters"), dict) else {}
    slots = anchor.get("slots") if isinstance(anchor.get("slots"), dict) else {}
    current = _classified_lot_tokens(prompt)
    roots = list(current.get("root_lot_ids") or [])
    fabs = list(current.get("fab_lot_ids") or [])
    if not roots and not fabs:
        for key in ("root_lot_ids", "root_lot_id", "roots"):
            _flowi_add_anchor_lot_values(roots, fabs, filters.get(key) or slots.get(key))
        for key in ("fab_lot_ids", "fab_lot_id", "lot_ids", "lots", "lot", "lot_scope"):
            _flowi_add_anchor_lot_values(roots, fabs, filters.get(key) or slots.get(key))
    wafer_ids = _wafer_tokens(prompt)
    if not wafer_ids:
        for key in ("wafer_ids", "wafers", "wafer_id"):
            wafer_ids.extend(_flowi_context_values(filters.get(key) or slots.get(key)))
        wafer_ids = [_normalize_wafer_id(v) or str(v).strip() for v in wafer_ids]
        wafer_ids = [v for v in dict.fromkeys(wafer_ids) if v]
    step = _flowi_func_step_token(prompt) or str(filters.get("step") or slots.get("step") or slots.get("function_step") or "").strip()
    group = _flowi_group_token(prompt) or str(filters.get("group") or slots.get("group") or "").strip().upper()
    prefix = str(filters.get("prefix") or "").strip().upper()
    args = {
        "product": _flowi_anchor_product(anchor, prompt, product),
        "root_lot_ids": roots,
        "fab_lot_ids": fabs,
        "wafer_ids": wafer_ids,
        "read_only": True,
    }
    if step:
        args["step"] = step
    if group:
        args["group"] = group
    if prefix:
        args["prefix"] = prefix
    return args


def _flowi_splittable_anchor_prompt(args: dict[str, Any], prompt: str) -> str:
    parts: list[str] = []
    product = str(args.get("product") or "").strip()
    if product:
        parts.append(product)
    parts.extend(str(x) for x in (args.get("fab_lot_ids") or args.get("root_lot_ids") or []) if str(x).strip())
    wafers = [str(x).strip() for x in (args.get("wafer_ids") or []) if str(x).strip()]
    if wafers:
        parts.append(",".join(f"#{w}" for w in wafers))
    if args.get("step"):
        parts.append(str(args.get("step")))
    if args.get("group") or args.get("prefix"):
        parts.append(str(args.get("group") or args.get("prefix")))
    parts.append("스플릿테이블 형태로 보여줘")
    suffix = str(prompt or "").strip()
    if suffix and suffix not in " ".join(parts):
        parts.append(f"({suffix})")
    return " ".join(parts).strip()


def _handle_flowi_splittable_context_followup(
    prompt: str,
    product: str,
    max_rows: int,
    allowed_keys: set[str] | None,
    agent_context: dict[str, Any] | None,
) -> dict[str, Any]:
    if allowed_keys is not None and "splittable" not in allowed_keys:
        return {"handled": False}
    anchor = _flowi_recent_tool_anchor(agent_context)
    if not (_flowi_anchor_supports_splittable(anchor) and _flowi_splittable_followup_requested(prompt, anchor)):
        return {"handled": False}
    args = _flowi_anchor_splittable_args(anchor, prompt, product)
    if not (args.get("root_lot_ids") or args.get("fab_lot_ids")):
        return {"handled": False}
    synthetic_prompt = _flowi_splittable_anchor_prompt(args, prompt)
    product_hint = str(args.get("product") or "").strip()
    if not product_hint:
        lots_for_product = _flowi_lot_scope_terms(args.get("root_lot_ids") or [], args.get("fab_lot_ids") or [])
        resolved_product, candidate_tool = _product_or_candidate_tool(
            synthetic_prompt,
            "",
            lots_for_product,
            kinds=("ML_TABLE", "FAB"),
            intent="splittable_context_followup",
            ask_if_any=True,
        )
        if candidate_tool:
            candidate_tool.setdefault("feature", "splittable")
            candidate_tool.setdefault("action", "clarify_product")
            candidate_tool.setdefault("arguments", args)
            candidate_tool.setdefault("slots", {k: v for k, v in args.items() if v not in (None, "", [], {})})
            return candidate_tool
        product_hint = resolved_product
        args["product"] = resolved_product
    if not product_hint:
        return {"handled": False}
    tool = _flowi_query_splittable_view_tool(args, product_hint, synthetic_prompt, max_rows=max_rows)
    if not tool.get("handled"):
        return {"handled": False}
    tool["context_followup"] = True
    tool["slots"] = {k: v for k, v in args.items() if v not in (None, "", [], {})}
    tool["answer"] = "이전 조건을 이어받아 SplitTable 형태로 다시 표시했습니다. " + str(tool.get("answer") or "")
    return tool


def _flowi_context_prefers_splittable(agent_context: dict[str, Any] | None) -> bool:
    score = 0
    for msg in reversed(_flowi_context_messages(agent_context)[-8:]):
        feature = str(msg.get("feature") or "").strip()
        action = str(msg.get("action") or "").strip()
        intent = str(msg.get("intent") or "").strip()
        filters = msg.get("filters") if isinstance(msg.get("filters"), dict) else {}
        if feature == "splittable":
            score += 2
        if action in {"query_wafer_split_at_step", "query_splittable_view", "query_lot_knobs_from_ml_table", "find_lots_by_knob_value"}:
            score += 2
        if intent in {"wafer_split_at_step", "splittable_view", "lot_knobs", "knob_value_lot_search", "splittable_plan_mismatch"}:
            score += 2
        if str(filters.get("source") or "").lower() in {"splittable.view", "ml_table"}:
            score += 1
        if str(filters.get("product") or "").upper().startswith("ML_TABLE_"):
            score += 1
    return score >= 2


def _flowi_should_continue_splittable_context(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    up = _upper(text)
    if any(t in low or t in text for t in ("인폼", "inform", "메일", "현재 lot_id", "current lot_id", "fab_lot", "fab lot", "파일탐색기", "filebrowser")):
        return False
    if any(t in {"FAB", "ET", "INLINE", "VM"} for t in _flowi_source_type_tokens(text)) and not any(t in low or t in text for t in ("split", "스플릿", "knob", "노브", "ml_table")):
        return False
    if any(t in low or t in text for t in ("split", "스플릿", "knob", "노브", "plan", "actual", "mask")):
        return False
    return bool((_lot_tokens(text) or _flowi_func_step_token(text)) and any(t in low or t in text for t in ("어떻게", "뭐", "무엇", "보여", "확인", "조회", "값", "?")))
