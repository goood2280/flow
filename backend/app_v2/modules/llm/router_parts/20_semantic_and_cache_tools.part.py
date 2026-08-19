def _fab_context_files(product: str) -> list[Path]:
    files = [p for p in _fab_files(product) if "1.RAWDATA_DB_FAB" in str(p) and "_backups" not in str(p)]
    return files or [p for p in _fab_files(product) if "_backups" not in str(p)] or _fab_files(product)


def _handle_grouped_metric_chart(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    text = str(prompt or "")
    if not (_contains_chart_intent(text) or "별로" in text or "별" in text or "분리" in text):
        return {"handled": False}
    group_keys = _group_chart_group_keys(text)
    if not group_keys:
        return {"handled": False}
    product_hint = _product_hint(text, product)
    if not product_hint:
        return {
            "handled": True,
            "intent": "dashboard_group_metric_needs_context",
            "action": "collect_required_fields",
            "answer": "EQP/Chamber별 차트를 그리려면 product가 필요합니다. 예: `PRODA CD_GATE EQP/Chamber별로 그려줘`",
            "missing": ["product"],
            "feature": "dashboard",
        }
    inline_files = _inline_files(product_hint)
    if not inline_files:
        return {"handled": True, "intent": "dashboard_group_metric", "answer": f"{product_hint} INLINE parquet을 찾지 못했습니다.", "feature": "dashboard"}
    inline_lf = _scan_parquet(inline_files)
    inline_cols = _schema_names(inline_lf)
    product_col = _ci_col(inline_cols, "product", "PRODUCT")
    root_col = _ci_col(inline_cols, "root_lot_id", "ROOT_LOT_ID")
    wafer_col = _ci_col(inline_cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    lot_wf_col = _ci_col(inline_cols, "lot_wf", "LOT_WF")
    item_col = _ci_col(inline_cols, "item_id", "ITEM_ID", "rawitem_id", "RAWITEM_ID", "item", "ITEM")
    value_col = _ci_col(inline_cols, "value", "VALUE", "_value", "val", "VAL")
    if not item_col or not value_col:
        return {
            "handled": True,
            "intent": "dashboard_group_metric",
            "answer": "INLINE 데이터에서 item_id/value 컬럼을 찾지 못했습니다.",
            "table": {"kind": "dashboard_group_metric_error", "title": "Missing INLINE columns", "placement": "below", "columns": _table_columns(["message", "columns"]), "rows": [{"message": "missing item_id/value", "columns": ", ".join(inline_cols[:80])}], "total": 1},
            "feature": "dashboard",
        }
    if not ((root_col and wafer_col) or lot_wf_col):
        return {"handled": True, "intent": "dashboard_group_metric", "answer": "INLINE 데이터에 root_lot_id+wafer_id 또는 lot_wf join key가 필요합니다.", "feature": "dashboard"}
    metric, item_matches, item_candidates = _inline_metric_match_for_prompt(inline_lf, item_col, text)
    if not metric:
        return {
            "handled": True,
            "intent": "dashboard_group_metric_needs_context",
            "action": "collect_required_fields",
            "answer": "차트로 그릴 INLINE item을 찾지 못했습니다. item명을 더 정확히 알려주세요.",
            "missing": ["item_id"],
            "feature": "dashboard",
            "table": {"kind": "inline_item_candidates", "title": "INLINE item candidates", "placement": "below", "columns": _table_columns(["item_id"]), "rows": [{"item_id": x} for x in item_candidates], "total": len(item_candidates)},
        }
    lots = _lot_tokens(text)
    aliases = _product_aliases(product_hint)
    filters = []
    if aliases and product_col:
        filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    if lots:
        lot_cols = [c for c in (root_col, lot_wf_col) if c]
        lot_expr = _or_contains(lot_cols, lots)
        if lot_expr is not None:
            filters.append(lot_expr)
    filters.append(pl.col(item_col).cast(_STR, strict=False).is_in(item_matches or [metric]))
    for expr in filters:
        inline_lf = inline_lf.filter(expr)
    inline_exprs = []
    join_cols = []
    if root_col and wafer_col:
        inline_exprs.append(_root_key_expr(root_col).alias("root_lot_id"))
        inline_exprs.append(_wafer_key_expr(wafer_col).alias("wafer_id"))
        join_cols = ["root_lot_id", "wafer_id"]
    if root_col and wafer_col:
        inline_exprs.append(_lot_wf_expr(root_col, wafer_col).alias("lot_wf"))
    elif lot_wf_col:
        inline_exprs.append(pl.col(lot_wf_col).cast(_STR, strict=False).alias("lot_wf"))
        if not join_cols:
            join_cols = ["lot_wf"]
    inline_exprs.append(pl.col(value_col).cast(pl.Float64, strict=False).alias("metric_value"))
    inline_group_cols = list(dict.fromkeys([*join_cols, "lot_wf"]))
    try:
        metric_lf = (
            inline_lf.select(inline_exprs)
            .drop_nulls(subset=["metric_value"])
            .group_by(inline_group_cols)
            .agg([
                pl.col("metric_value").mean().alias("metric_value"),
                pl.len().alias("metric_n"),
            ])
        )
    except Exception as e:
        return {"handled": True, "intent": "dashboard_group_metric", "answer": f"INLINE metric 집계 실패: {e}", "feature": "dashboard"}

    fab_files = _fab_context_files(product_hint)
    if not fab_files:
        return {"handled": True, "intent": "dashboard_group_metric", "answer": f"{product_hint} FAB parquet을 찾지 못해 EQP/Chamber를 붙일 수 없습니다.", "feature": "dashboard"}
    fab_lf = _scan_parquet(fab_files)
    fab_cols = _schema_names(fab_lf)
    f_product_col = _ci_col(fab_cols, "product", "PRODUCT")
    f_root_col = _ci_col(fab_cols, "root_lot_id", "ROOT_LOT_ID")
    f_wafer_col = _ci_col(fab_cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    f_lot_wf_col = _ci_col(fab_cols, "lot_wf", "LOT_WF")
    eqp_col = _ci_col(fab_cols, "eqp", "EQP", "eqp_id", "EQP_ID", "equipment_id", "EQUIPMENT_ID")
    chamber_col = _ci_col(fab_cols, "chamber", "CHAMBER", "chamber_id", "CHAMBER_ID")
    time_col = _ci_col(fab_cols, "tkout_time", "TKOUT_TIME", "time", "TIME", "timestamp", "TIMESTAMP")
    if ("eqp" in group_keys and not eqp_col) or ("chamber" in group_keys and not chamber_col):
        return {
            "handled": True,
            "intent": "dashboard_group_metric",
            "answer": "FAB 데이터에서 요청한 EQP/Chamber 컬럼을 찾지 못했습니다.",
            "table": {"kind": "dashboard_group_metric_error", "title": "Missing FAB columns", "placement": "below", "columns": _table_columns(["message", "columns"]), "rows": [{"message": "missing eqp/chamber", "columns": ", ".join(fab_cols[:80])}], "total": 1},
            "feature": "dashboard",
        }
    f_filters = []
    if aliases and f_product_col:
        f_filters.append(pl.col(f_product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    if lots:
        f_lot_cols = [c for c in (f_root_col, f_lot_wf_col) if c]
        lot_expr = _or_contains(f_lot_cols, lots)
        if lot_expr is not None:
            f_filters.append(lot_expr)
    for expr in f_filters:
        fab_lf = fab_lf.filter(expr)
    fab_exprs = []
    if f_root_col and f_wafer_col and "root_lot_id" in join_cols:
        fab_exprs.append(_root_key_expr(f_root_col).alias("root_lot_id"))
        fab_exprs.append(_wafer_key_expr(f_wafer_col).alias("wafer_id"))
        fab_join_cols = ["root_lot_id", "wafer_id"]
    elif f_root_col and f_wafer_col:
        fab_exprs.append(_lot_wf_expr(f_root_col, f_wafer_col).alias("lot_wf"))
        fab_join_cols = ["lot_wf"]
    elif f_lot_wf_col:
        fab_exprs.append(pl.col(f_lot_wf_col).cast(_STR, strict=False).alias("lot_wf"))
        fab_join_cols = ["lot_wf"]
    else:
        return {"handled": True, "intent": "dashboard_group_metric", "answer": "FAB 데이터에 metric과 연결할 root_lot_id+wafer_id 또는 lot_wf가 필요합니다.", "feature": "dashboard"}
    if eqp_col:
        fab_exprs.append(pl.col(eqp_col).cast(_STR, strict=False).alias("eqp"))
    else:
        fab_exprs.append(pl.lit("").alias("eqp"))
    if chamber_col:
        fab_exprs.append(pl.col(chamber_col).cast(_STR, strict=False).alias("chamber"))
    else:
        fab_exprs.append(pl.lit("").alias("chamber"))
    fab_exprs.append(pl.col(time_col).cast(_STR, strict=False).alias("latest_time") if time_col else pl.lit("").alias("latest_time"))
    try:
        fab_ctx = (
            fab_lf.select(fab_exprs)
            .drop_nulls(subset=[g for g in group_keys if g in {"eqp", "chamber"}])
            .group_by([*fab_join_cols, "eqp", "chamber"])
            .agg([
                pl.len().alias("fab_context_rows"),
                pl.col("latest_time").max().alias("latest_time"),
            ])
        )
        joined = metric_lf.join(fab_ctx, on=fab_join_cols, how="inner")
        group_exprs = [
            pl.col("metric_value").mean().alias("mean"),
            pl.col("metric_value").median().alias("median"),
            pl.len().alias("joined_rows"),
            pl.col("lot_wf").n_unique().alias("wafer_groups") if "lot_wf" in joined.collect_schema().names() else pl.len().alias("wafer_groups"),
            pl.col("metric_n").sum().alias("metric_n"),
            pl.col("fab_context_rows").sum().alias("fab_context_rows"),
        ]
        grouped = (
            joined.group_by(group_keys)
            .agg(group_exprs)
            .sort("median", descending=True)
            .limit(max(5, min(40, max_rows * 4)))
            .collect()
        )
    except Exception as e:
        logger.warning("flowi grouped metric chart failed: %s", e)
        return {"handled": True, "intent": "dashboard_group_metric", "answer": f"EQP/Chamber별 chart query 실패: {e}", "feature": "dashboard"}
    rows = grouped.to_dicts()
    groups = []
    for row in rows:
        label = " / ".join(_text(row.get(k)) or "-" for k in group_keys)
        groups.append({
            "label": label,
            "value": _round4(row.get("median")),
            "mean": _round4(row.get("mean")),
            "median": _round4(row.get("median")),
            "joined_rows": int(row.get("joined_rows") or 0),
            "wafer_groups": int(row.get("wafer_groups") or 0),
            "metric_n": int(row.get("metric_n") or 0),
            "fab_context_rows": int(row.get("fab_context_rows") or 0),
            **{k: row.get(k) or "" for k in group_keys},
        })
    cols_out = [*group_keys, "median", "mean", "joined_rows", "wafer_groups", "metric_n", "fab_context_rows"]
    answer = (
        f"{product_hint} {metric}을 실제 INLINE 값에 FAB EQP/Chamber context를 붙여 {len(groups)}개 그룹으로 그렸습니다. "
        "집계값은 그룹별 median 기준이며, join은 root_lot_id+wafer_id 우선입니다."
    )
    if not groups:
        answer = f"{product_hint} {metric} 조건으로 EQP/Chamber별 chart row를 찾지 못했습니다."
    return {
        "handled": True,
        "intent": "dashboard_group_metric_chart",
        "action": "query_group_metric_bar_chart",
        "answer": answer,
        "feature": "dashboard",
        "slots": {"product": product_hint, "metric": metric, "group_by": group_keys, "lots": lots},
        "chart_result": {
            "ok": True,
            "kind": "dashboard_group_bar",
            "title": f"{product_hint} {metric} by {'/'.join(group_keys).upper()}",
            "groups": groups,
            "total": len(groups),
            "x_label": " / ".join(group_keys),
            "y_label": f"{metric} median",
            "metric": metric,
            "group_by": group_keys,
            "join_cols": fab_join_cols,
            "sources": {"inline_file_count": len(inline_files), "fab_file_count": len(fab_files), "inline_items": item_matches or [metric]},
        },
        "table": {"kind": "dashboard_group_metric", "title": f"{metric} by {'/'.join(group_keys)}", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in rows[:max(1, min(120, max_rows * 8))]], "total": len(rows)},
    }


def _handle_chart_request(prompt: str, product: str, max_rows: int) -> dict:
    if not _contains_chart_intent(prompt):
        return {"handled": False}
    chart_defaults = _flowi_chart_defaults()
    scatter_defaults = chart_defaults.get("scatter") or FLOWI_CHART_DEFAULTS["scatter"]
    inline_agg = scatter_defaults.get("inline_agg") if scatter_defaults.get("inline_agg") in _CHART_AGG_VALUES else "avg"
    et_agg = scatter_defaults.get("et_agg") if scatter_defaults.get("et_agg") in _CHART_AGG_VALUES else "median"
    _prompt_agg = _flowi_chart_agg_from_prompt(prompt, default="")
    if _prompt_agg in _CHART_AGG_VALUES:
        et_agg = _prompt_agg
    sources = _source_terms(prompt)
    metrics = _metric_alias_hits(prompt)
    operations = _chart_operations(prompt)
    chart_type = _flowi_chart_type_from_prompt(prompt, metrics)
    lots = _lot_tokens(prompt)
    product_hint = _product_hint(prompt, product)
    join_key = _chart_default_join_key(sources)
    requires = []
    general_draft_types = {"pie", "donut", "bar", "area", "heatmap", "table", "cross_table", "treemap", "pareto", "binning"}
    if chart_type in general_draft_types:
        if not product_hint:
            requires.append("product")
        if not sources:
            requires.append("source_type")
        if chart_type not in {"table", "cross_table"} and not metrics:
            requires.append("metric")
        if chart_type in {"cross_table", "heatmap"} and len(metrics) < 2:
            requires.append("x/y metric")
    elif len(sources) < 2 and "correlation" in operations:
        requires.append("x/y source")
    if chart_type not in general_draft_types and len(metrics) < 2 and ("correlation" in operations or "scatter" in operations):
        requires.append("x/y metric")
    if chart_type not in general_draft_types and not product_hint:
        requires.append("product")
    rows = [
        {"field": "unit_action", "value": "dashboard.chart_draft" if chart_type in general_draft_types else "dashboard.metric_scatter"},
        {"field": "sources", "value": ", ".join(sorted(sources)) or "-"},
        {"field": "metrics", "value": ", ".join(m["metric"] for m in metrics) or "-"},
        {"field": "operations", "value": ", ".join(operations)},
        {"field": "join_key_priority", "value": "WF Agg(root_lot_id+wafer_id/lot_wf) 기본; shot/die는 명시 요청 시"},
        {"field": "INLINE aggregation", "value": f"{inline_agg} by wafer by default"},
        {"field": "ET aggregation", "value": f"{et_agg} by wafer by default"},
        {"field": "join_default", "value": "left join; ambiguous direction must be confirmed"},
        {"field": "anti_fabrication", "value": "schema catalog and DB rows only; no invented columns/data"},
    ]
    if product_hint:
        rows.append({"field": "product", "value": product_hint})
    if lots:
        rows.append({"field": "lot_filter", "value": ", ".join(lots)})
    if requires:
        rows.append({"field": "needs_clarification", "value": ", ".join(requires)})

    choices = []
    for choice in FLOWI_JOIN_CHOICES:
        next_prompt = f"{prompt.strip()} / {choice['prompt_suffix']}"
        choices.append({**choice, "prompt": next_prompt})
    if requires:
        choices.insert(0, {
            "id": "open_schema_search",
            "label": "0",
            "title": "schema 후보 먼저 찾기",
            "recommended": True,
            "description": "실제 DB schema catalog에서 INLINE/ET/ML_TABLE 컬럼 후보를 먼저 확인합니다.",
            "prompt": f"{prompt.strip()} / schema 후보 먼저 확인",
        })
        # Keep only one recommended marker in the rendered list.
        for item in choices[1:]:
            item["recommended"] = False

    config = _flowi_dashboard_default_config(prompt, chart_type, metrics, product=product_hint)
    if product_hint:
        config["product"] = product_hint
    if len(sources) == 1:
        config["source_type"] = next(iter(sources))
    metric_names = [m.get("metric") for m in metrics if m.get("metric")]
    if metric_names:
        config.setdefault("metric", metric_names[0])
        config.setdefault("item_id", metric_names[0])
    config["chart_type"] = chart_type
    config.setdefault("title", _dashboard_chart_title(product_hint, chart_type, metrics, config))

    chart = {
        "kind": chart_type,
        "status": "planned",
        "sources": sorted(sources),
        "metrics": metrics,
        "operations": operations,
        "join_key": join_key,
        "aggregations": {"INLINE": inline_agg, "ET": et_agg},
        "render_preset": scatter_defaults,
        "render_target": "dashboard",
        "requires": requires,
    }
    chart_result = None
    if chart_type in general_draft_types:
        choices_for_missing = []
        if "source_type" in requires:
            for src in ("INLINE", "ET", "FAB"):
                choices_for_missing.append({
                    "id": f"source_{src.lower()}",
                    "label": src,
                    "title": f"{src} 기준",
                    "description": f"{src} 데이터 소스로 초안을 계속 편집합니다.",
                    "prompt": f"{prompt.strip()} {src}",
                })
        if "metric" in requires:
            choices_for_missing.append({
                "id": "open_schema_search",
                "label": "schema",
                "title": "schema 후보 먼저 찾기",
                "recommended": not choices_for_missing,
                "description": "실제 DB schema catalog에서 컬럼 후보를 확인합니다.",
                "prompt": f"{prompt.strip()} / schema 후보 먼저 확인",
            })
        return {
            "handled": True,
            "intent": "dashboard_chart_draft_needs_context",
            "action": "collect_required_fields",
            "answer": f"{_dashboard_chart_title(product_hint, chart_type, metrics, config)} 초안을 만들었습니다. 부족한 값은 편집 모달에서 보완할 수 있습니다.",
            "feature": "dashboard",
            "slots": {
                "product": product_hint,
                "lots": lots,
                "sources": sorted(sources),
                "metrics": metric_names,
                "operations": operations,
            },
            "chart_type": chart_type,
            "config": config,
            "chart_config": config,
            "chart": chart,
            "chart_result": {
                "ok": False,
                "kind": f"dashboard_{chart_type}",
                "title": config.get("title") or _dashboard_chart_title(product_hint, chart_type, metrics, config),
                "chart_type": chart_type,
                "chart_config": config,
                "requires": requires,
                "points": [],
                "total": 0,
            },
            "missing": requires,
            "question": f"{_dashboard_chart_label(chart_type)} 생성을 계속하려면 {', '.join(requires) if requires else '세부 설정'} 값을 확인해 주세요.",
            "choices": choices_for_missing[:4],
            "pending_prompt": prompt,
            "clarification": {
                "question": f"{_dashboard_chart_label(chart_type)} 생성을 계속하려면 {', '.join(requires) if requires else '세부 설정'} 값을 확인해 주세요.",
                "choices": choices_for_missing[:4],
            },
            "table": {
                "kind": "flowi_chart_plan",
                "title": "Flowi chart draft",
                "placement": "below",
                "columns": [{"key": "field", "label": "FIELD"}, {"key": "value", "label": "VALUE"}],
                "rows": rows[:max(1, max_rows)],
                "total": len(rows),
            },
        }
    if not requires:
        actual = _try_metric_scatter(prompt, product_hint, metrics, lots, operations)
        if actual.get("ok"):
            chart_result = actual
            chart["status"] = "computed"
        else:
            chart["status"] = "planned"
            chart["execution_error"] = actual.get("error") or "chart execution failed"
    answer = (
        "차트/상관 분석 단위기능으로 처리할 요청입니다. "
        "Flowi는 metric 이름을 지어내지 않고 schema catalog와 실제 DB row로만 차트를 만듭니다.\n"
        f"- 감지 source: {', '.join(sorted(sources)) or '-'}\n"
        f"- 감지 metric 후보: {', '.join(m['metric'] for m in metrics) or '-'}\n"
        f"- 기본 집계: INLINE {inline_agg}, ET {et_agg}\n"
        "- 기본은 WF Agg입니다. shot/die/map을 명시한 경우에만 shot 단위 매칭을 시도합니다."
    )
    if requires:
        answer += "\n아래 선택지에서 먼저 확인할 범위를 골라주세요."
    elif chart_result:
        answer += (
            f"\n실제 DB 기준 scatter를 계산했습니다. n={chart_result.get('total', 0)}, "
            f"corr={chart_result.get('corr') if chart_result.get('corr') is not None else '-'}."
        )
    else:
        answer += "\n조건은 충분하지만 실제 차트 계산에 실패했습니다. 아래 계획과 오류를 확인해주세요."
    return {
        "handled": True,
        "intent": "dashboard_scatter_plan",
        "action": "build_metric_scatter",
        "answer": answer,
        "feature": "dashboard",
        "slots": {
            "product": product_hint,
            "lots": lots,
            "sources": sorted(sources),
            "metrics": [m["metric"] for m in metrics],
            "operations": operations,
        },
        "chart_type": chart_type,
        "config": config,
        "chart_config": config,
        "chart": chart,
        "chart_result": chart_result,
        "clarification": {
            "question": "어떤 기준으로 실제 DB query를 만들까요?",
            "choices": choices[:3],
        },
        "table": {
            "kind": "flowi_chart_plan",
            "title": "Flowi chart/query plan",
            "placement": "below",
            "columns": [{"key": "field", "label": "FIELD"}, {"key": "value", "label": "VALUE"}],
            "rows": rows[:max(1, max_rows)],
            "total": len(rows),
        },
    }


def _matches_any(value: str, needles: set[str]) -> bool:
    val = _upper(value)
    return any(n and (val == n or n in val) for n in needles)


def _filter_files_by_product(files: list[Path], product: str) -> list[Path]:
    aliases = _product_aliases(product)
    if not aliases:
        return files
    out = []
    for fp in files:
        parts = {_upper(fp.stem), _upper(fp.parent.name)}
        parts.update(_upper(p) for p in fp.parts[-6:])
        if any(_matches_any(p, aliases) or _matches_any(p.replace("ML_TABLE_", ""), aliases) for p in parts):
            out.append(fp)
    return out


def _scan_parquet(files: list[Path]) -> pl.LazyFrame:
    if not files:
        raise HTTPException(404, "읽을 parquet 파일이 없습니다")
    paths = [str(p) for p in files]
    try:
        lf = pl.scan_parquet(paths, missing_columns="insert", extra_columns="ignore")
    except TypeError:
        lf = pl.scan_parquet(paths)
    try:
        from core.utils import filter_valid_wafer_ids_lazy
        return filter_valid_wafer_ids_lazy(lf)
    except Exception:
        return lf


def _schema_names(lf: pl.LazyFrame) -> list[str]:
    try:
        return list(lf.collect_schema().names())
    except Exception:
        return list(lf.schema.keys())


def _ci_col(cols: list[str], *candidates: str) -> str:
    by_lower = {c.lower(): c for c in cols}
    for cand in candidates:
        hit = by_lower.get(str(cand).lower())
        if hit:
            return hit
    return ""


def _db_root_candidates(kind: str) -> list[Path]:
    base = PATHS.db_root
    kind_u = kind.upper()
    if not base.exists():
        return []
    roots = []
    if base.is_dir() and kind_u in base.name.upper():
        roots.append(base)
    try:
        for child in sorted(base.iterdir()):
            if child.is_dir() and kind_u in child.name.upper():
                roots.append(child)
    except Exception:
        pass
    return roots


def _et_files(product: str) -> list[Path]:
    files: list[Path] = []
    for root in _db_root_candidates("ET"):
        files.extend(sorted(root.rglob("*.parquet")))
    return _filter_files_by_product(files, product)


def _ml_files(product: str) -> list[Path]:
    roots = []
    for root in (PATHS.base_root, PATHS.db_root):
        try:
            if root.exists() and root not in roots:
                roots.append(root)
        except Exception:
            pass
    files: list[Path] = []
    for root in roots:
        try:
            files.extend(sorted(root.glob("ML_TABLE_*.parquet")))
        except Exception:
            pass
    dedup = []
    seen = set()
    for fp in files:
        key = str(fp.resolve()) if fp.exists() else str(fp)
        if key in seen:
            continue
        seen.add(key)
        dedup.append(fp)
    return _filter_files_by_product(dedup, product)


def _flowi_single_ml_product_hint(files: list[Path]) -> str:
    products: set[str] = set()
    for fp in files or []:
        try:
            stem = Path(fp).stem
        except Exception:
            continue
        if stem.upper().startswith("ML_TABLE_"):
            name = stem[len("ML_TABLE_"):].strip()
            if name:
                products.add(_upper(name))
    return next(iter(products)) if len(products) == 1 else ""


def _unique_strings(lf: pl.LazyFrame, col: str, limit: int = 200) -> list[str]:
    if not col:
        return []
    try:
        vals = (
            lf.select(pl.col(col).cast(_STR, strict=False).drop_nulls().unique().alias(col))
            .limit(limit)
            .collect()[col]
            .to_list()
        )
    except Exception:
        return []
    return [_text(v) for v in vals if _text(v)]


def _match_values(values: list[str], needles: list[str]) -> list[str]:
    clean = [_upper(n) for n in needles if _upper(n) and _upper(n) not in _STOP_TOKENS]
    if not clean:
        return []
    exact = [v for v in values if _upper(v) in clean]
    if exact:
        return sorted(set(exact))
    contains = [v for v in values if any(n in _upper(v) for n in clean)]
    return sorted(set(contains))


def _core_product_name(product: str) -> str:
    raw = _text(product)
    if raw.upper().startswith("ML_TABLE_"):
        return raw[len("ML_TABLE_"):].strip()
    return raw


def _column_matches(cols: list[str], terms: list[str], *, include_knob_when_named: bool = False) -> list[str]:
    clean = []
    seen_terms = set()
    for term in terms:
        key = _upper(term)
        if not key or key in _STOP_TOKENS or key in FLOWI_CHART_METRIC_STOP:
            continue
        if key in seen_terms:
            continue
        seen_terms.add(key)
        clean.append(key)
    out = []
    seen = set()
    for col in cols:
        col_u = _upper(col)
        body = col_u.replace("KNOB_", "", 1)
        if include_knob_when_named and "KNOB" in clean and col_u.startswith("KNOB_"):
            hit = True
        else:
            hit = any(t == col_u or t == body or t in col_u or t in body for t in clean)
        if hit and col not in seen:
            seen.add(col)
            out.append(col)
    return out


def _flowi_value_lookup_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    if ("스플릿테이블" in text or "split table" in low or "splittable" in low) and not any(t in low or t in text for t in ("값", "얼마", "sql", "select", "where", "db", "files", "파일탐색기", "조회", "검색", "찾")):
        return False
    return any(t in low or t in text for t in (
        "값", "얼마", "몇", "찾", "조회", "검색", "sql", "select", "where",
        "파일탐색기", "파일 탐색기", "files", "filebrowser", "db",
    ))


def _table_columns(keys: list[str]) -> list[dict[str, str]]:
    return [{"key": key, "label": key.upper()} for key in keys]


_FLOWI_HIGHLIGHT_TERMS = (
    "잘못", "틀린", "이상", "불일치", "안 맞", "안맞", "mismatch", "wrong", "fail", "ng"
)


def _flowi_wants_highlight(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    return any(term in text or term in low for term in _FLOWI_HIGHLIGHT_TERMS)


def _flowi_field_question(field: str) -> str:
    labels = {
        "product": "어느 제품인가요?",
        "root_lot_ids": "어느 Root Lot인가요?",
        "root_lot_id": "어느 Root Lot인가요?",
        "lot_ids": "어느 Lot인가요?",
        "fab_lot_ids": "어느 Fab Lot인가요?",
        "root_lot_id_or_fab_lot_id": "어느 Lot인가요?",
        "module": "어느 모듈인가요?",
        "recipients": "수신처는 누구인가요?",
        "step": "어느 Step인가요?",
        "metric": "어느 항목인가요?",
        "metrics_or_items": "어느 항목인가요?",
        "knob_value": "어떤 KNOB 값인가요?",
        "source_type": "어느 Source인가요?",
        "split_set": "SplitTable은 어떤 Split으로 진행할까요?",
        "note": "어떤 내용을 남길까요?",
        "entries": "모듈별 Split을 어떻게 넣을까요?",
        "wafer_ids": "어느 Wafer인가요?",
        "plan_assignments": "Wafer별 plan 값을 어떻게 넣을까요?",
        "keyword": "어떤 키워드로 찾을까요?",
    }
    return labels.get(str(field or ""), f"{field} 값을 알려주세요.")


def _flowi_table_headers(table: dict[str, Any]) -> list[str]:
    if not isinstance(table, dict):
        return []
    headers = table.get("headers")
    if isinstance(headers, list) and headers:
        return [str(h.get("label") if isinstance(h, dict) else h) for h in headers]
    columns = table.get("columns") if isinstance(table.get("columns"), list) else []
    out: list[str] = []
    for col in columns:
        if isinstance(col, dict):
            out.append(str(col.get("label") or col.get("key") or ""))
        else:
            out.append(str(col or ""))
    if out:
        return out
    rows = table.get("rows") if isinstance(table.get("rows"), list) else []
    if rows and isinstance(rows[0], dict):
        return [str(k) for k in rows[0].keys() if not str(k).startswith("__")]
    return []


def _flowi_prepare_inline_table(table: dict[str, Any] | None, *, highlight: bool = False) -> dict[str, Any] | None:
    if not isinstance(table, dict):
        return table
    rows = table.get("rows") if isinstance(table.get("rows"), list) else []
    columns = table.get("columns") if isinstance(table.get("columns"), list) else []
    if not columns and rows and isinstance(rows[0], dict):
        keys = [str(k) for k in rows[0].keys() if not str(k).startswith("__")]
        table["columns"] = _table_columns(keys)
    headers = _flowi_table_headers(table)
    if headers and "headers" not in table:
        table["headers"] = headers
    table.setdefault("max_height", 320)
    table.setdefault("overflow_x", True)
    if highlight:
        table["highlight"] = True
        table.setdefault("highlight_reason", "사용자가 잘못된/불일치 셀 강조를 요청했습니다.")
    return table


def _flowi_inline_summary(tool: dict[str, Any]) -> str:
    if not isinstance(tool, dict):
        return ""
    table = tool.get("table") if isinstance(tool.get("table"), dict) else {}
    chart = tool.get("chart_result") if isinstance(tool.get("chart_result"), dict) else (tool.get("chart") if isinstance(tool.get("chart"), dict) else {})
    split_view = tool.get("split_view") if isinstance(tool.get("split_view"), dict) else {}
    lot_list = tool.get("lot_list") if isinstance(tool.get("lot_list"), list) else []
    if split_view:
        total = split_view.get("total", len(split_view.get("rows") or []))
        return f"{split_view.get('title') or 'SplitTable'} {total}개 셀"
    if lot_list:
        return f"Lot list {len(lot_list)}건"
    if table:
        total = table.get("total", len(table.get("rows") or []))
        headers = _flowi_table_headers(table)
        return f"{table.get('title') or table.get('kind') or 'Table'} {total} rows · {len(headers)} columns"
    if chart:
        return f"{chart.get('title') or chart.get('kind') or 'Chart'}"
    return str(tool.get("action") or tool.get("intent") or "Flowi response")


def _flowi_set_inline_type(tool: dict[str, Any], tool_type: str = "", *, prompt: str = "", highlight: bool | None = None) -> dict[str, Any]:
    if not isinstance(tool, dict):
        return tool
    wants_highlight = _flowi_wants_highlight(prompt) if highlight is None else bool(highlight)
    if isinstance(tool.get("table"), dict):
        _flowi_prepare_inline_table(tool["table"], highlight=wants_highlight or bool(tool.get("highlight")))
    if not tool_type:
        if isinstance(tool.get("chart_result"), dict) or isinstance(tool.get("chart"), dict):
            tool_type = "chart"
        elif isinstance(tool.get("split_view"), dict):
            tool_type = "split_view"
        elif isinstance(tool.get("lot_list"), list):
            tool_type = "lot_list"
        elif isinstance(tool.get("table"), dict) or isinstance(tool.get("rows"), list) or isinstance(tool.get("knobs"), list):
            tool_type = "table"
        else:
            tool_type = "message"
    tool["type"] = tool_type
    if wants_highlight:
        tool["highlight"] = True
    tool.setdefault("inline_summary", _flowi_inline_summary(tool))
    return tool


def _flowi_feature_for_function(function_name: str, selected: dict[str, Any] | None = None) -> str:
    if isinstance(selected, dict) and selected.get("feature"):
        return str(selected.get("feature") or "")
    name = str(function_name or "")
    if name in {"query_current_fab_lot_from_fab_db", "query_lot_current_step_from_progress_cache", "preview_filebrowser_data", "search_filebrowser_schema", "filebrowser.sql.llm.draft"}:
        return "filebrowser"
    if name in {"query_splittable_view", "query_wafer_split_at_step", "query_lot_knobs_from_ml_table", "find_lots_by_knob_value", "preview_splittable_plan_update"}:
        return "splittable"
    if name in {"register_inform_log", "register_inform_walkthrough", "compose_inform_module_mail", "summarize_inform_modules"}:
        return "inform"
    if name == "query_tracker_lot_purpose":
        return "tracker"
    if name == "build_dashboard_metric_chart":
        return "dashboard"
    if name == "query_meeting_calendar_records":
        return "meeting"
    if name == "semiconductor_diagnosis":
        return "diagnosis"
    return ""


def _flowi_api_target_for_function(function_name: str, feature: str = "") -> dict[str, str]:
    name = str(function_name or "")
    if name == "filebrowser.sql.llm.draft":
        return {"api": "/api/filebrowser/sql/llm/draft", "handler": "routers.filebrowser.filebrowser_sql_llm_draft"}
    if name == "query_current_fab_lot_from_fab_db":
        return {"api": "data/Fab", "handler": "_handle_current_fab_lot_lookup"}
    if name == "query_lot_current_step_from_progress_cache":
        return {"api": "data/Fab/cache/lot_progress_latest_lot_by_root_wafer.parquet", "handler": "_handle_current_step_from_progress_cache"}
    if name in {"query_splittable_view", "query_wafer_split_at_step"}:
        return {"api": "/api/splittable/view", "handler": "routers.splittable.view_split"}
    if name == "query_lot_knobs_from_ml_table":
        return {"api": "/api/filebrowser/ml-table/lookup (ML_TABLE lookup cache)", "handler": "_handle_knob_query"}
    if name == "find_lots_by_knob_value":
        return {"api": "ML_TABLE + latest progress cache", "handler": "_handle_find_lots_by_knob_value"}
    if name == "register_inform_log":
        return {"api": "/api/informs", "handler": "_handle_flowi_register_inform_log"}
    if name == "query_tracker_lot_purpose":
        return {"api": "/api/tracker/issues", "handler": "_handle_tracker_lot_purpose_lookup"}
    if name == "build_dashboard_metric_chart":
        return {"api": "dashboard chart draft/session", "handler": "_augment_dashboard_tool"}
    if name == "query_meeting_calendar_records":
        return {"api": "/api/meetings/ask", "handler": "_handle_meeting_recall"}
    return {"api": feature or "local handler", "handler": name}


def _flowi_driver_contract_action(action: str = "", intent: str = "", feature: str = "") -> str:
    """Map internal Flow-i handlers to the Agent Driver Contract action keys."""
    name = str(action or "")
    intent = str(intent or "")
    feature = str(feature or "")
    if name in FLOWI_REGISTERED_UNIT_ACTIONS:
        return name
    if name == "filebrowser.sql.llm.draft" or intent == "filebrowser_sql_llm_draft":
        return "filebrowser.sql.llm.draft"
    if name in {"query_current_fab_lot_from_fab_db", "query_lot_current_step_from_progress_cache"}:
        return "filebrowser.lot_progress.latest"
    if name == "preview_filebrowser_data":
        return "filebrowser.preview"
    if name == "search_filebrowser_schema":
        return "filebrowser.preview"
    if name in {"query_splittable_view", "query_wafer_split_at_step"}:
        return "splittable.view"
    if name == "query_lot_knobs_from_ml_table":
        return "splittable.knob.summary"
    if name == "preview_splittable_plan_update":
        return "splittable.plan.compare"
    if name == "register_inform_log":
        return "inform.draft.start"
    if name == "register_inform_walkthrough":
        return "inform.draft.start"
    if name == "compose_inform_module_mail":
        return "inform.draft.resolve"
    if name == "summarize_inform_modules":
        return "inform.thread.list"
    if name == "query_tracker_lot_purpose":
        return "tracker.lot.purpose"
    if name == "build_dashboard_metric_chart":
        return "dashboard.chart.llm.draft"
    if name == "query_meeting_calendar_records" or intent == "meeting_recall_summary":
        return "meeting.ask.llm"
    if name == "run_semiconductor_diagnosis":
        return "diagnosis.rca.read"
    if feature == "tablemap":
        return "tablemap.query"
    if feature in {"filebrowser", "splittable", "inform"} and intent:
        candidate = f"{feature}.{intent}".replace("_", ".")
        return candidate if candidate in FLOWI_REGISTERED_UNIT_ACTIONS else "flowi.feature.guidance"
    if name == "route_flowi_feature" or intent.endswith("_guidance"):
        return "flowi.feature.guidance"
    return "flowi.general"


def _flowi_orchestrator_activation_preview(prompt: str, product: str = "", max_rows: int = 12) -> dict[str, Any]:
    preview = _structure_flowi_function_call(prompt, product=product, max_rows=max_rows)
    selected = preview.get("selected_function") if isinstance(preview.get("selected_function"), dict) else {}
    function = (preview.get("function_call") or {}).get("function") if isinstance(preview.get("function_call"), dict) else {}
    args = function.get("arguments") if isinstance(function, dict) else {}
    args = args if isinstance(args, dict) else {}
    validation = preview.get("validation") if isinstance(preview.get("validation"), dict) else {}
    missing = validation.get("missing") if isinstance(validation.get("missing"), list) else []
    action = str(selected.get("name") or (function.get("name") if isinstance(function, dict) else "") or "")
    feature = _flowi_feature_for_function(action, selected)
    unit_action = _flowi_driver_contract_action(action, str(selected.get("intent") or ""), feature)
    target = _flowi_api_target_for_function(action, feature)
    if selected.get("requires_confirmation"):
        status = "awaiting_confirmation"
    elif missing:
        status = "needs_input"
    else:
        status = "ready"
    candidates = []
    for entry in preview.get("feature_candidates") or []:
        if not isinstance(entry, dict) or not entry.get("key"):
            continue
        candidates.append({
            "key": entry.get("key"),
            "title": entry.get("title") or entry.get("key"),
            "active": entry.get("key") == feature,
        })
    if feature and not any(c.get("key") == feature for c in candidates):
        candidates.insert(0, {"key": feature, "title": feature, "active": True})
    return {
        "prompt": preview.get("prompt") or str(prompt or ""),
        "intent": selected.get("intent") or action or "general",
        "feature": feature or "general",
        "action": unit_action or action or "general",
        "handler_action": action or "",
        "unit_action": unit_action,
        "api": target.get("api") or "",
        "handler": target.get("handler") or "",
        "status": status,
        "missing": missing,
        "requires_confirmation": bool(selected.get("requires_confirmation")),
        "side_effect": selected.get("side_effect") or "none",
        "confidence": selected.get("confidence"),
        "reason": selected.get("reason") or "",
        "arguments": {
            "product": args.get("product") or "",
            "root_lot_ids": args.get("root_lot_ids") or [],
            "fab_lot_ids": args.get("fab_lot_ids") or [],
            "wafer_ids": args.get("wafer_ids") or [],
            "step": args.get("step") or "",
            "module": args.get("module") or "",
            "knob_value": args.get("knob_value") or "",
        },
        "candidates": candidates[:4],
    }


def _flowi_orchestrator_activation_previews(prompts: list[str], product: str = "", max_rows: int = 12) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in prompts:
        prompt = str(raw or "").strip()
        if not prompt or prompt in seen:
            continue
        seen.add(prompt)
        try:
            row = _flowi_orchestrator_activation_preview(prompt, product=product, max_rows=max_rows)
        except Exception as e:
            row = {
                "prompt": prompt,
                "intent": "preview_error",
                "feature": "flowi",
                "action": "orchestrator_preview",
                "unit_action": "flowi.orchestrator.preview",
                "api": "",
                "handler": "",
                "status": "error",
                "missing": [],
                "requires_confirmation": False,
                "side_effect": "none",
                "confidence": None,
                "reason": str(e),
                "arguments": {},
                "candidates": [],
            }
        rows.append(row)
        if len(rows) >= 12:
            break
    return rows


def _flowi_deterministic_missing_guesses(prompt: str, row: dict[str, Any]) -> dict[str, str]:
    text = str(prompt or "")
    args = row.get("arguments") if isinstance(row.get("arguments"), dict) else {}
    guesses: dict[str, str] = {}
    for raw in row.get("missing") or []:
        field = _flowi_missing_key(str(raw or "")) or str(raw or "").strip()
        value: Any = ""
        if field == "product":
            match = re.search(r"\b(PROD[A-Z0-9_]+)\b", text, flags=re.I)
            value = match.group(1).upper() if match else args.get("product")
        elif field in {"root_lot_id", "root_lot_ids", "root_lot_id_or_fab_lot_id", "root_lot_id/lot_id"}:
            roots = args.get("root_lot_ids") or re.findall(r"\b([A-Z][0-9]{4,}[A-Z]?(?:\.\d+)?)\b", text)
            value = ", ".join(str(x) for x in roots[:3]) if isinstance(roots, list) else str(roots or "")
        elif field in {"fab_lot_id", "fab_lot_ids"}:
            lots = args.get("fab_lot_ids") or re.findall(r"\b([A-Z][0-9]{4,}[A-Z]?\.\d+)\b", text)
            value = ", ".join(str(x) for x in lots[:3]) if isinstance(lots, list) else str(lots or "")
        elif field in {"wafer_id", "wafer_ids"}:
            wafers = args.get("wafer_ids") or _wafer_tokens(text)
            value = ", ".join(str(x) for x in wafers[:5]) if isinstance(wafers, list) else str(wafers or "")
        elif field in {"step", "step_id"}:
            value = args.get("step") or (_flowi_func_step_token(text) or "")
        elif field in {"module"}:
            value = args.get("module") or (_flowi_module_token(text) or "")
        elif field in {"knob_value"}:
            value = args.get("knob_value") or ""
        elif field in {"metric", "metrics_or_items", "item_id"}:
            value = args.get("metric") or args.get("item_id") or ""
        if value not in (None, "", [], {}):
            guesses[str(raw)] = str(value)
    return guesses


def _flowi_guess_missing_for_preview(prompt: str, row: dict[str, Any]) -> dict[str, Any]:
    missing = [str(x or "").strip() for x in (row.get("missing") or []) if str(x or "").strip()]
    if not missing:
        return {}
    values = _flowi_deterministic_missing_guesses(prompt, row)
    rationale = "prompt에서 직접 확인 가능한 product/lot/wafer/module token을 우선 채웠습니다."
    if llm_adapter.is_available() and llm_adapter.should_attempt_llm():
        schema = {
            "values": {field: "string value or empty string" for field in missing[:8]},
            "rationale": "short Korean sentence; no hidden reasoning",
        }
        ask = (
            "다음 Flow-i dry-run 결과의 missing slot 값을 추정해 주세요. "
            "응답은 JSON 하나만 반환하고 내부 추론은 쓰지 마세요. "
            "확실하지 않은 값은 빈 문자열로 두세요.\n"
            + json.dumps({
                "prompt": prompt,
                "missing": missing[:8],
                "current_arguments": row.get("arguments") if isinstance(row.get("arguments"), dict) else {},
                "schema": schema,
            }, ensure_ascii=False, default=str)
        )
        out = llm_adapter.complete(
            ask,
            system="Return compact JSON only. Do not include chain-of-thought.",
            timeout=8,
        )
        if out.get("ok"):
            raw = str(out.get("text") or "").strip()
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I | re.S).strip()
            try:
                obj = json.loads(raw)
                if isinstance(obj, dict):
                    llm_values = obj.get("values") if isinstance(obj.get("values"), dict) else {}
                    for key, value in llm_values.items():
                        text = str(value or "").strip()
                        if text:
                            values[str(key)] = text[:200]
                    llm_rationale = str(obj.get("rationale") or "").strip()
                    if llm_rationale:
                        rationale = llm_rationale[:240]
            except Exception:
                pass
    if not values:
        rationale = "자동으로 확정할 값이 없어 직접 입력이 필요합니다."
    return {"values": values, "rationale": rationale}


def _fab_files(product: str = "") -> list[Path]:
    files: list[Path] = []
    for root in _db_root_candidates("FAB"):
        files.extend(sorted(root.rglob("*.parquet")))
    return _filter_files_by_product(files, product)


def _wafer_tokens(prompt: str) -> list[str]:
    text = str(prompt or "")
    out: list[str] = []
    seen: set[str] = set()
    def add(raw: Any) -> None:
        val = _normalize_wafer_id(raw)
        if val and val not in seen:
            seen.add(val)
            out.append(val)
    def add_range(a: Any, b: Any) -> None:
        try:
            start, end = int(a), int(b)
        except Exception:
            return
        if start > end:
            start, end = end, start
        for n in range(max(1, start), min(FLOWI_MAX_WAFER_ID, end) + 1):
            add(n)
    range_patterns = [
        r"#\s*0?(\d{1,2})\s*(?:~|-|–|—|to)\s*#?\s*0?(\d{1,2})",
        r"\b(?:WF|WAFER)\s*0?(\d{1,2})\s*(?:~|-|–|—|to)\s*(?:WF|WAFER)?\s*0?(\d{1,2})\b",
        r"\b(?:SLOT|슬롯)\s*0?(\d{1,2})\s*(?:~|-|–|—|to)\s*(?:SLOT|슬롯)?\s*0?(\d{1,2})\b",
        r"웨이퍼\s*0?(\d{1,2})\s*(?:~|-|–|—|부터)\s*(?:웨이퍼\s*)?0?(\d{1,2})",
        r"0?(\d{1,2})\s*번\s*(?:~|-|–|—|부터)\s*0?(\d{1,2})\s*번",
        r"0?(\d{1,2})\s*장\s*(?:~|-|–|—|부터)\s*0?(\d{1,2})\s*장",
    ]
    for pat in range_patterns:
        for m in re.finditer(pat, text, flags=re.I):
            add_range(m.group(1), m.group(2))
    patterns = [
        r"#\s*(\d{1,2})(?=\D|$)",
        r"\bWF\s*0?(\d{1,2})\b",
        r"\bWAFER\s*0?(\d{1,2})\b",
        r"\bSLOT\s*0?(\d{1,2})\b",
        r"슬롯\s*0?(\d{1,2})",
        r"웨이퍼\s*0?(\d{1,2})",
        r"(\d{1,2})\s*번\s*(?:WF|WAFER|웨이퍼)",
        r"(\d{1,2})\s*번\s*(?:SLOT|슬롯)",
        r"(\d{1,2})\s*번\s*장",
        r"(\d{1,2})\s*번장",
        r"(\d{1,2})\s*장\b",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text, flags=re.I):
            add(m.group(1))
    return out


def _wafer_match_expr(col: str, wafers: list[str]):
    if not col or not wafers:
        return None
    vals: set[str] = set()
    for raw in wafers:
        val = _normalize_wafer_id(raw)
        if val:
            vals.add(val)
    if not vals:
        return pl.lit(False)
    return _wafer_key_expr(col).is_in(sorted(vals))


def _step_meta(product: str, step_id: Any) -> dict[str, Any]:
    try:
        from core.lot_step import lookup_step_meta
        meta = lookup_step_meta(product=product, step_id=step_id)
        return meta if isinstance(meta, dict) else {}
    except Exception:
        return {}


def _function_step_label(product: str, step_id: Any) -> str:
    meta = _step_meta(product, step_id)
    return _text(meta.get("func_step") or meta.get("function_step") or meta.get("step_desc"))


def _source_filter_lots(lf: pl.LazyFrame, cols: list[str], lots: list[str]) -> pl.LazyFrame:
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    lot_wf_col = _ci_col(cols, "lot_wf", "LOT_WF")
    expr = _or_contains([c for c in (root_col, lot_col, fab_col, lot_wf_col) if c], lots)
    return lf.filter(expr) if expr is not None else lf


def _is_current_fab_lot_prompt(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    if not _lot_tokens(text):
        return False
    mentions_fab_lot = any(t in low for t in ("fab_lot", "fab lot", "fab-lot", "fablot"))
    mentions_lot_id_with_fab = "fab" in low and ("lot id" in low or "lot_id" in low)
    mentions_current_lot_id = any(t in low for t in ("lot_id", "lot id", "lotid")) and "fab" not in low
    if not (mentions_fab_lot or mentions_lot_id_with_fab or mentions_current_lot_id):
        return False
    return any(t in low or t in text for t in ("현재", "지금", "current", "now", "뭐야", "무엇", "알려", "찾", "조회", "확인"))


def _flowi_current_lot_id_requested(prompt: str) -> bool:
    low = str(prompt or "").lower()
    return any(t in low for t in ("lot_id", "lot id", "lotid")) and not any(
        t in low for t in ("fab_lot", "fab lot", "fab-lot", "fablot")
    )


def _flowi_exact_lot_scope_expr(cols: list[str], root_lots: list[str], fab_lots: list[str], lots: list[str]):
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    expr = None

    def add(piece):
        nonlocal expr
        if piece is not None:
            expr = piece if expr is None else (expr | piece)

    roots = [_upper(v) for v in (root_lots or []) if _upper(v)]
    fabs = [_upper(v) for v in (fab_lots or []) if _upper(v)]
    other_lots = [_upper(v) for v in (lots or []) if _upper(v)]
    if roots and root_col:
        add(pl.col(root_col).cast(_STR, strict=False).str.to_uppercase().is_in(roots))
    if fabs:
        if fab_col:
            add(pl.col(fab_col).cast(_STR, strict=False).str.to_uppercase().is_in(fabs))
        if lot_col:
            add(pl.col(lot_col).cast(_STR, strict=False).str.to_uppercase().is_in(fabs))
    if other_lots and not (roots or fabs):
        add(_or_contains([c for c in (root_col, lot_col, fab_col) if c], other_lots))
    return expr


def _is_fab_current_location_prompt(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    if not _lot_tokens(text):
        return False
    if _is_current_fab_lot_prompt(text):
        return False
    has_location = any(t in low for t in ("where", "location")) or any(
        t in text for t in ("\uc5b4\ub514", "\uc704\uce58")
    )
    has_current = any(t in low for t in ("current", "now")) or any(
        t in text for t in ("\ud604\uc7ac", "\uc9c0\uae08")
    )
    return bool(has_location and has_current)


def _fab_current_location_interpretation_notes(
    prompt: str,
    *,
    product: str,
    roots: list[str],
    fabs: list[str],
    lots: list[str],
    wafers: list[str],
) -> list[str]:
    notes: list[str] = []
    if product:
        notes.append(f"product={product} FAB source")
    lot = (roots or lots or fabs or [""])[0]
    if lot:
        notes.append(f"{lot} -> root_lot_id={lot}")
    for wafer in wafers[:3]:
        display = f"#{wafer}" if re.search(rf"#\s*0?{re.escape(str(wafer))}(?=\D|$)", str(prompt or "")) else f"wafer {wafer}"
        notes.append(f"{display} -> wafer_id={wafer}")
    notes.append("current location -> latest FAB row ordered by tkout_time desc; return step_id only")
    return notes


def _handle_fab_current_location_lookup(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    preview = _structure_flowi_function_call(prompt, product=product, max_rows=max_rows)
    selected = str((preview.get("selected_function") or {}).get("name") or "")
    if selected != "query_current_location" and not _is_fab_current_location_prompt(prompt):
        return {"handled": False}

    args = ((preview.get("function_call") or {}).get("function") or {}).get("arguments") or {}
    classified = _classified_lot_tokens(prompt)

    def strings(name: str) -> list[str]:
        return [str(x).strip() for x in (args.get(name) or []) if str(x).strip()]

    roots = strings("root_lot_ids") or [str(x).strip() for x in (classified.get("root_lot_ids") or []) if str(x).strip()]
    fabs = strings("fab_lot_ids") or [str(x).strip() for x in (classified.get("fab_lot_ids") or []) if str(x).strip()]
    lots = strings("lot_ids") or _lot_tokens(prompt)
    wafers = strings("wafer_ids") or [str(x).strip() for x in _wafer_tokens(prompt) if str(x).strip()]
    lookup_lots = list(dict.fromkeys([*roots, *fabs, *lots]))
    action = "query_current_location"
    slots = {
        "product": str(args.get("product") or product or "").strip(),
        "root_lot_ids": roots,
        "fab_lot_ids": fabs,
        "lot_ids": lots,
        "wafer_ids": wafers,
    }
    if not lookup_lots:
        return {
            "handled": True,
            "intent": "fab_current_location_lookup",
            "action": action,
            "answer": "FAB current location lookup needs a lot id.",
            "missing": ["lot_ids"],
            "slots": slots,
            "feature": "filebrowser",
        }
    if not wafers:
        return {
            "handled": True,
            "intent": "fab_current_location_lookup",
            "action": action,
            "answer": "FAB current location lookup needs a wafer id.",
            "missing": ["wafer_ids"],
            "slots": slots,
            "feature": "filebrowser",
        }

    product_hint, candidate_tool = _product_or_candidate_tool(
        prompt,
        str(args.get("product") or product or "").strip(),
        lookup_lots,
        kinds=("FAB",),
        intent="fab_current_location_lookup",
    )
    slots["product"] = product_hint
    if candidate_tool:
        candidate_tool.setdefault("action", action)
        candidate_tool.setdefault("slots", slots)
        return candidate_tool

    files = _fab_files(product_hint)
    filters = {
        "product": product_hint,
        "root_lot_ids": roots,
        "fab_lot_ids": fabs,
        "lot_ids": lots,
        "wafer_ids": wafers,
        "source": "FAB",
        "latest_order": "tkout_time desc",
    }
    interpretation_notes = _fab_current_location_interpretation_notes(
        prompt,
        product=product_hint,
        roots=roots,
        fabs=fabs,
        lots=lots,
        wafers=wafers,
    )
    if not files:
        return {
            "handled": True,
            "intent": "fab_current_location_lookup",
            "action": action,
            "answer": "FAB parquet files were not found for the requested lookup.",
            "feature": "filebrowser",
            "slots": slots,
            "filters": filters,
            "interpretation_notes": interpretation_notes,
            "table": {"kind": "fab_current_location_lookup", "title": "Current FAB location", "placement": "below", "columns": _table_columns(["message"]), "rows": [{"message": "FAB not found"}], "total": 0},
        }

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
            lf = lf.filter(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(_product_aliases(product_hint))))
        lot_expr = _flowi_exact_lot_scope_expr(cols, roots, fabs, lots)
        if lot_expr is not None:
            lf = lf.filter(lot_expr)
        else:
            lf = _source_filter_lots(lf, cols, lookup_lots)
        wf_expr = _wafer_match_expr(wafer_col, wafers)
        if wf_expr is not None:
            lf = lf.filter(wf_expr)
        exprs = [
            pl.col(product_col).cast(_STR, strict=False).alias("product") if product_col else pl.lit(_core_product_name(product_hint)).alias("product"),
            pl.col(root_col).cast(_STR, strict=False).alias("root_lot_id") if root_col else (
                pl.col(lot_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id") if lot_col else (
                    pl.col(fab_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id") if fab_col else pl.lit("").alias("root_lot_id")
                )
            ),
            pl.col(lot_col).cast(_STR, strict=False).alias("lot_id") if lot_col else pl.lit("").alias("lot_id"),
            pl.col(fab_col).cast(_STR, strict=False).alias("fab_lot_id") if fab_col else pl.lit("").alias("fab_lot_id"),
            _wafer_key_expr(wafer_col).alias("wafer_id") if wafer_col else pl.lit("").alias("wafer_id"),
            pl.col(step_col).cast(_STR, strict=False).alias("step_id") if step_col else pl.lit("").alias("step_id"),
            pl.col(process_col).cast(_STR, strict=False).alias("process_id") if process_col else pl.lit("").alias("process_id"),
            pl.col(time_col).cast(_STR, strict=False).alias("tkout_time") if time_col else pl.lit("").alias("tkout_time"),
        ]
        rows_all = lf.select(exprs).limit(50000).collect().to_dicts()
    except Exception as e:
        return {
            "handled": True,
            "intent": "fab_current_location_lookup",
            "action": action,
            "answer": f"FAB current location lookup failed: {e}",
            "feature": "filebrowser",
            "slots": slots,
            "filters": filters,
            "interpretation_notes": interpretation_notes,
        }

    if not rows_all:
        return {
            "handled": True,
            "intent": "fab_current_location_lookup",
            "action": action,
            "answer": "No FAB row matched the requested lot and wafer.",
            "feature": "filebrowser",
            "slots": slots,
            "filters": filters,
            "interpretation_notes": interpretation_notes,
            "table": {"kind": "fab_current_location_lookup", "title": "Current FAB location", "placement": "below", "columns": _table_columns(["message"]), "rows": [{"message": "No FAB row matched"}], "total": 0},
        }

    rows_all.sort(
        key=lambda row: (
            _parse_flowi_datetime(row.get("tkout_time")) or datetime.min,
            _step_rank_key(row.get("step_id")),
        ),
        reverse=True,
    )
    current = rows_all[0]
    cols_out = ["product", "root_lot_id", "fab_lot_id", "lot_id", "wafer_id", "step_id", "step_desc", "process_id", "tkout_time"]
    row = {k: current.get(k, "") for k in cols_out}
    step_label, step_desc = _lot_wip_step_annotation(row.get("step_id"), row.get("product") or product_hint)
    row["step_desc"] = step_desc.get("step_desc") or ""
    root_label = row.get("root_lot_id") or lookup_lots[0]
    wafer_label = row.get("wafer_id") or wafers[0]
    answer = f"{root_label} #{wafer_label} \ud604\uc7ac \uc704\uce58\ub294 step_id={row.get('step_id') or '-'} \uc785\ub2c8\ub2e4."
    if row.get("step_desc"):
        answer += f" ({step_label})"
    if row.get("tkout_time"):
        answer += f" \ucd5c\uc2e0 tkout_time: {row.get('tkout_time')}."
    notice = _lot_wip_delay_notice(source="fab", latest_move=row.get("tkout_time") or "")
    if notice:
        answer += f"\n\n{notice}"
    return {
        "handled": True,
        "intent": "fab_current_location_lookup",
        "action": action,
        "answer": answer,
        "feature": "filebrowser",
        "slots": slots,
        "filters": filters,
        "interpretation_notes": interpretation_notes,
        "source_ids": ["FAB", *[str(fp) for fp in files[:6]]],
        "table": {
            "kind": "fab_current_location_lookup",
            "title": "Current FAB location",
            "placement": "below",
            "columns": _table_columns(cols_out),
            "rows": [row],
            "total": 1,
            "matched_total": len(rows_all),
        },
    }


def _handle_current_fab_lot_lookup(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _is_current_fab_lot_prompt(prompt):
        return {"handled": False}
    action = "query_current_fab_lot_from_fab_db"
    lots = _lot_tokens(prompt)
    classified = _classified_lot_tokens(prompt)
    roots = [str(x) for x in classified.get("root_lot_ids") or []]
    fabs = [str(x) for x in classified.get("fab_lot_ids") or []]
    wafers = _wafer_tokens(prompt)
    product_hint, candidate_tool = _product_or_candidate_tool(
        prompt, product, lots, kinds=("FAB",), intent="current_fab_lot_lookup"
    )
    slots = {
        "product": product_hint,
        "root_lot_ids": roots,
        "fab_lot_ids": fabs,
        "lot_ids": lots,
        "wafer_ids": wafers,
    }
    if candidate_tool:
        candidate_tool.setdefault("action", action)
        candidate_tool.setdefault("slots", slots)
        return candidate_tool
    if not product_hint:
        return {
            "handled": True,
            "intent": "current_fab_lot_lookup",
            "action": action,
            "answer": "현재 fab_lot_id를 FAB DB에서 찾으려면 product가 필요합니다. 예: `PRODA A1000 #6 현재 fab lot id가 뭐야?`",
            "missing": ["product"],
            "slots": slots,
            "feature": "filebrowser",
        }
    files = _fab_files(product_hint)
    slots["product"] = product_hint
    if not files:
        return {
            "handled": True,
            "intent": "current_fab_lot_lookup",
            "action": action,
            "answer": f"{product_hint} FAB parquet을 찾지 못했습니다. DB root와 product명을 확인해주세요.",
            "table": {"kind": "current_fab_lot_lookup", "title": "Current FAB lot", "placement": "below", "columns": _table_columns(["message"]), "rows": [{"message": "FAB not found"}], "total": 0},
            "slots": slots,
            "filters": {"product": product_hint, "root_lot_ids": roots, "fab_lot_ids": fabs, "lots": lots, "wafers": wafers, "source": "FAB"},
            "feature": "filebrowser",
        }
    try:
        lf = _scan_parquet(files)
        cols = _schema_names(lf)
        product_col = _ci_col(cols, "product", "PRODUCT")
        root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
        lot_col = _ci_col(cols, "lot_id", "LOT_ID")
        fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID") or lot_col
        wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
        step_col = _ci_col(cols, "step_id", "STEP_ID")
        process_col = _ci_col(cols, "process_id", "PROCESS_ID")
        time_col = _ci_col(cols, "tkout_time", "TKOUT_TIME", "time", "TIME", "timestamp", "TIMESTAMP", "move_time", "MOVE_TIME", "updated_at", "UPDATED_AT")
        if not fab_col or not (root_col or lot_col):
            return {
                "handled": True,
                "intent": "current_fab_lot_lookup",
                "action": action,
                "answer": "FAB 데이터에서 root_lot_id/lot_id/fab_lot_id 컬럼을 찾지 못했습니다.",
                "slots": slots,
                "filters": {"product": product_hint, "root_lot_ids": roots, "fab_lot_ids": fabs, "lots": lots, "wafers": wafers, "source": "FAB"},
                "feature": "filebrowser",
            }
        aliases = _product_aliases(product_hint)
        if aliases and product_col:
            lf = lf.filter(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
        lot_expr = _flowi_exact_lot_scope_expr(cols, roots, fabs, lots)
        if lot_expr is not None:
            lf = lf.filter(lot_expr)
        else:
            lf = _source_filter_lots(lf, cols, lots)
        if wafers and wafer_col:
            wf_expr = _wafer_match_expr(wafer_col, wafers)
            if wf_expr is not None:
                lf = lf.filter(wf_expr)
        exprs = [
            pl.col(product_col).cast(_STR, strict=False).alias("product") if product_col else pl.lit(_core_product_name(product_hint)).alias("product"),
            pl.col(root_col).cast(_STR, strict=False).alias("root_lot_id") if root_col else (
                pl.col(lot_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id") if lot_col else pl.lit("").alias("root_lot_id")
            ),
            pl.col(lot_col).cast(_STR, strict=False).alias("lot_id") if lot_col else pl.lit("").alias("lot_id"),
            pl.col(fab_col).cast(_STR, strict=False).alias("fab_lot_id"),
            _wafer_key_expr(wafer_col).alias("wafer_id") if wafer_col else pl.lit("").alias("wafer_id"),
            pl.col(step_col).cast(_STR, strict=False).alias("step_id") if step_col else pl.lit("").alias("step_id"),
            pl.col(process_col).cast(_STR, strict=False).alias("process_id") if process_col else pl.lit("").alias("process_id"),
            pl.col(time_col).cast(_STR, strict=False).alias("time") if time_col else pl.lit("").alias("time"),
        ]
        df = lf.select(exprs).drop_nulls(subset=["fab_lot_id"]).limit(50000).collect()
    except Exception as e:
        return {
            "handled": True,
            "intent": "current_fab_lot_lookup",
            "action": action,
            "answer": f"FAB DB fab_lot_id 조회 실패: {e}",
            "slots": slots,
            "filters": {"product": product_hint, "root_lot_ids": roots, "fab_lot_ids": fabs, "lots": lots, "wafers": wafers, "source": "FAB"},
            "feature": "filebrowser",
        }
    rows_all = [r for r in df.to_dicts() if _text(r.get("fab_lot_id"))]
    if not rows_all:
        wafer_text = f" wafer #{', #'.join(wafers)}" if wafers else ""
        return {
            "handled": True,
            "intent": "current_fab_lot_lookup",
            "action": action,
            "answer": f"{product_hint} {', '.join(lots)}{wafer_text}에 해당하는 FAB row를 찾지 못했습니다.",
            "table": {"kind": "current_fab_lot_lookup", "title": "Current FAB lot", "placement": "below", "columns": _table_columns(["message"]), "rows": [{"message": "No FAB row matched"}], "total": 0},
            "filters": {"product": product_hint, "lots": lots, "wafers": wafers},
            "slots": slots,
            "feature": "filebrowser",
        }
    def sort_key(row: dict[str, Any]):
        dt = _parse_flowi_datetime(row.get("time"))
        return (dt or datetime.min, _text(row.get("step_id")), _text(row.get("fab_lot_id")))
    rows_all.sort(key=sort_key, reverse=True)
    current = rows_all[0]
    cols_out = ["product", "root_lot_id", "wafer_id", "fab_lot_id", "lot_id", "step_id", "process_id", "time"]
    rows = [{k: r.get(k, "") for k in cols_out} for r in rows_all[:max(1, min(max_rows, 25))]]
    wafer_label = f" wafer #{current.get('wafer_id')}" if current.get("wafer_id") else ""
    wants_lot_id = _flowi_current_lot_id_requested(prompt)
    if wants_lot_id:
        answer = (
            f"{current.get('product') or product_hint} {current.get('root_lot_id') or lots[0]}{wafer_label}의 현재 lot_id는 "
            f"`{current.get('lot_id') or current.get('fab_lot_id')}`입니다."
        )
    else:
        answer = (
            f"{current.get('product') or product_hint} {current.get('root_lot_id') or lots[0]}{wafer_label}의 현재 fab_lot_id는 "
            f"`{current.get('fab_lot_id')}`입니다."
        )
    if current.get("time") or current.get("step_id"):
        answer += f" 기준 row: step_id={current.get('step_id') or '-'}, time={current.get('time') or '-'}."
    if current.get("root_lot_id") or current.get("wafer_id"):
        answer += " 기준 SQL: root_lot_id/wafer_id 조건 후 tkout_time 최신 row."
    return {
        "handled": True,
        "intent": "current_fab_lot_lookup",
        "action": action,
        "answer": answer,
        "table": {"kind": "current_fab_lot_lookup", "title": "Current FAB lot", "placement": "below", "columns": _table_columns(cols_out), "rows": rows, "total": len(rows_all)},
        "filters": {"product": product_hint, "root_lot_ids": roots, "fab_lot_ids": fabs, "lots": lots, "wafers": wafers, "source": "FAB", "latest_order": "tkout_time desc"},
        "slots": slots,
        "feature": "filebrowser",
    }


def _flowi_latest_progress_cache_row(
    *,
    product: str = "",
    root_lot_id: str = "",
    lot_id: str = "",
    wafer_id: str = "",
    lot_wf: str = "",
) -> dict[str, Any]:
    def normalize_row(fab: dict[str, Any], *, cache_source: str, source_root: str = "") -> dict[str, Any]:
        root = _text(fab.get("root_lot_id") or root_lot_id)
        wafer = _normalize_wafer_id(fab.get("wafer_id") or wafer_id) or _text(fab.get("wafer_id") or wafer_id)
        step_id = _text(fab.get("step_id") or fab.get("current_step") or "")
        function_step = _text(fab.get("function_step") or fab.get("func_step") or fab.get("current_function_step") or "")
        return {
            "product": _text(fab.get("product") or product),
            "root_lot_id": root,
            "wafer_id": wafer,
            "lot_wf": _text(fab.get("lot_wf") or lot_wf or _flowi_lot_wf_id(root, wafer)),
            "lot_id": _text(fab.get("lot_id") or lot_id),
            "fab_lot_id": _text(fab.get("fab_lot_id") or fab.get("lot_id") or lot_id),
            "step_id": step_id,
            "function_step": function_step,
            "func_step": function_step,
            "update_time": _text(fab.get("update_time") or fab.get("time") or fab.get("tkout_time") or fab.get("tkin_time")),
            "cache_source": _text(fab.get("cache_source") or cache_source),
            "source_root": _text(source_root or fab.get("source_root")),
            "step_rank": _step_rank_key(step_id),
        }

    try:
        from core.lot_progress_cache import lot_progress_snapshot
        snapshot = lot_progress_snapshot(
            product=product or "",
            root_lot_id=root_lot_id or "",
            lot_id=lot_id or "",
            wafer_id=wafer_id or "",
            lot_wf=lot_wf or "",
            max_age_seconds=365 * 24 * 60 * 60,
        )
    except Exception:
        snapshot = {}
    if isinstance(snapshot, dict) and (snapshot.get("cache") or {}).get("hit"):
        fab = snapshot.get("fab") if isinstance(snapshot.get("fab"), dict) else {}
        if fab:
            return normalize_row(
                fab,
                cache_source=_text((snapshot.get("cache") or {}).get("source") or "filebrowser_latest"),
                source_root=_text((snapshot.get("cache") or {}).get("source_root") or ""),
            )
    try:
        from core import lot_progress_cache as progress_cache
        fp = progress_cache.filebrowser_cache_parquet_file()
        if not fp or not Path(fp).is_file():
            return {}
        lf = pl.scan_parquet(str(fp))
        cols = _schema_names(lf)
        from core.latest_lot_cache_format import FORMAT_COLUMN, FORMAT_VERSION
        if FORMAT_COLUMN not in cols:
            return {}
        lf = lf.filter(
            pl.col(FORMAT_COLUMN).cast(pl.Int64, strict=False) == FORMAT_VERSION
        )
        product_col = _ci_col(cols, "product", "PRODUCT", "process_id")
        root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
        wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
        lot_col = _ci_col(cols, "lot_id", "LOT_ID")
        fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
        lot_wf_col = _ci_col(cols, "lot_wf", "LOT_WF")
        step_col = _ci_col(cols, "step_id", "STEP_ID", "current_step")
        func_col = _ci_col(cols, "function_step", "func_step", "current_function_step")
        time_col = _ci_col(cols, "update_time", "tkout_time", "time", "tkin_time")
        expr = None
        if product and product_col:
            expr = pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(_product_aliases(product)))
        if root_lot_id and root_col:
            piece = pl.col(root_col).cast(_STR, strict=False).str.to_uppercase() == _upper(root_lot_id)
            expr = piece if expr is None else (expr & piece)
        if lot_id and (lot_col or fab_col):
            lot_expr = _or_contains([c for c in (lot_col, fab_col, root_col) if c], [lot_id])
            if lot_expr is not None:
                expr = lot_expr if expr is None else (expr & lot_expr)
        if wafer_id and wafer_col:
            wf_expr = _wafer_match_expr(wafer_col, [wafer_id])
            if wf_expr is not None:
                expr = wf_expr if expr is None else (expr & wf_expr)
        if lot_wf and lot_wf_col:
            piece = pl.col(lot_wf_col).cast(_STR, strict=False).str.to_uppercase() == _upper(lot_wf)
            expr = piece if expr is None else (expr & piece)
        if expr is not None:
            lf = lf.filter(expr)
        exprs = [
            pl.col(product_col).cast(_STR, strict=False).alias("product") if product_col else pl.lit(product or "").alias("product"),
            pl.col(root_col).cast(_STR, strict=False).alias("root_lot_id") if root_col else pl.lit(root_lot_id or "").alias("root_lot_id"),
            _wafer_key_expr(wafer_col).alias("wafer_id") if wafer_col else pl.lit(wafer_id or "").alias("wafer_id"),
            pl.col(lot_wf_col).cast(_STR, strict=False).alias("lot_wf") if lot_wf_col else pl.lit(lot_wf or "").alias("lot_wf"),
            pl.col(lot_col).cast(_STR, strict=False).alias("lot_id") if lot_col else pl.lit(lot_id or "").alias("lot_id"),
            pl.col(fab_col).cast(_STR, strict=False).alias("fab_lot_id") if fab_col else pl.lit("").alias("fab_lot_id"),
            pl.col(step_col).cast(_STR, strict=False).alias("step_id") if step_col else pl.lit("").alias("step_id"),
            pl.col(func_col).cast(_STR, strict=False).alias("function_step") if func_col else pl.lit("").alias("function_step"),
            pl.col(time_col).cast(_STR, strict=False).alias("update_time") if time_col else pl.lit("").alias("update_time"),
        ]
        rows = lf.select(exprs).limit(20).collect().to_dicts()
    except Exception:
        return {}
    if not rows:
        return {}
    rows.sort(key=lambda r: (_parse_flowi_datetime(r.get("update_time")) or datetime.min, _text(r.get("step_id"))), reverse=True)
    return normalize_row(rows[0], cache_source="filebrowser_latest", source_root=str(fp))


def _flowi_progress_for_lot_rows(product: str, rows: list[dict[str, Any]], *, limit: int = 500) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    roots_needing_fallback: list[str] = []
    for row in rows[:max(1, min(limit, 2000))]:
        root = _text(row.get("root_lot_id"))
        wafer = _normalize_wafer_id(row.get("wafer_id")) or _text(row.get("wafer_id"))
        lot_wf = _text(row.get("lot_wf") or _flowi_lot_wf_id(root, wafer))
        hit = _flowi_latest_progress_cache_row(
            product=product or row.get("product") or "",
            root_lot_id=root,
            lot_id=row.get("fab_lot_id") or row.get("lot_id") or "",
            wafer_id=wafer,
            lot_wf=lot_wf,
        )
        if hit:
            out[lot_wf or f"{root}_{wafer}"] = hit
            continue
        if root:
            roots_needing_fallback.append(root)
    missing_roots = [r for r in dict.fromkeys(roots_needing_fallback) if r]
    if missing_roots:
        fallback = _latest_fab_steps_for_roots(product, missing_roots, limit=limit)
        for row in rows:
            root = _text(row.get("root_lot_id"))
            wafer = _normalize_wafer_id(row.get("wafer_id")) or _text(row.get("wafer_id"))
            lot_wf = _text(row.get("lot_wf") or _flowi_lot_wf_id(root, wafer))
            if lot_wf in out or root not in fallback:
                continue
            fab = fallback.get(root) or {}
            function_step = _text(fab.get("func_step") or fab.get("function_step") or "")
            out[lot_wf] = {
                "product": fab.get("product") or product or row.get("product") or "",
                "root_lot_id": root,
                "wafer_id": _normalize_wafer_id(fab.get("wafer_id") or wafer) or _text(fab.get("wafer_id") or wafer),
                "lot_wf": lot_wf,
                "lot_id": fab.get("lot_id") or row.get("lot_id") or "",
                "fab_lot_id": fab.get("fab_lot_id") or row.get("fab_lot_id") or row.get("lot_id") or "",
                "step_id": fab.get("step_id") or "",
                "function_step": function_step,
                "func_step": function_step,
                "update_time": fab.get("time") or "",
                "cache_source": "fab_scan_fallback",
                "step_rank": fab.get("step_rank") or _step_rank_key(fab.get("step_id")),
            }
    return out


def _lot_wip_step_annotation(step_id: str, product: str = "", function_step: str = "") -> tuple[str, dict[str, str]]:
    """`AA100090 (SD_EPI · Gate Poly Etch)` 표기와 Vehicle_matching 부가정보.

    현재위치를 답하는 handler 들이 step_id 만 던지지 않도록 공통으로 쓴다 —
    사용자가 쓰는 이름은 매칭표의 step_desc 인 경우가 많다.
    """
    try:
        from core import lot_wip
        desc = lot_wip.describe_step(step_id, product)
        return lot_wip.step_label(step_id, function_step, desc.get("step_desc") or ""), desc
    except Exception:
        return (str(step_id or "-"), {"step_desc": "", "vehicle": ""})


def _lot_wip_delay_notice(*, source: str = "cache", cache_generated_at: str = "", latest_move: str = "") -> str:
    try:
        from core import lot_wip, lot_progress_cache as _lpc
        generated_at = str(cache_generated_at or "")
        if source != "fab" and not generated_at:
            # 답을 만든 캐시의 기준시각을 모르면 "얼마나 늦은 값인지"를 말할 수 없다.
            generated_at = str(_lpc.read_lot_progress_cache(allow_stale=True).get("generated_at") or "")
        return lot_wip.ingest_delay_notice(
            source=source,
            cache_generated_at=generated_at,
            latest_move=latest_move,
        )
    except Exception:
        return ""


def _handle_lot_wip_location(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    """전 제품 WIP latest cache 로 "지금 어디 있어 / 어느 step 이야" 에 답한다.

    FAB parquet 를 다시 스캔하는 아래 handler 들보다 **먼저** 시도한다. 같은
    질문에 대해 캐시 조회가 훨씬 빠르고, step_desc(Vehicle_matching.csv) 병합과
    적재 지연 고지가 함께 붙는다. 제품명만 준 질문(예: "AAAAA 지금 어디 있어")은
    아래 handler 들이 lot 토큰을 요구해서 아예 받지 못하던 구멍이기도 하다.

    wafer 를 특정한 질문도 우선 latest cache에서 처리한다. 현재위치 검색은 운영
    API의 가벼운 즉답 경로라는 Flow-i 실행 정책을 지키고, 한 단계 더 신선하다는
    이유로 요청 중 FAB parquet 전체를 여는 일을 피한다. 캐시에 대상이 없을 때만
    아래 기존 FAB 조회 경로에 양보한다.

    대상을 캐시에서 못 찾았는데 prompt 에 lot 토큰이 있으면 기존 FAB 스캔 경로에
    양보한다. lot 토큰이 없으면 어느 handler 도 받지 못하므로 지연 고지가 붙은
    안내라도 남긴다.
    """
    try:
        from core import lot_wip
        out = lot_wip.answer_wip(prompt, product=product, max_rows=max_rows)
    except Exception:
        logger.warning("lot WIP location lookup failed", exc_info=True)
        return {"handled": False}
    if not out or not out.get("handled"):
        return {"handled": False}
    if out.get("low_confidence") and _lot_tokens(prompt):
        return {"handled": False}
    table = out.get("table") if isinstance(out.get("table"), dict) else {}
    columns = table.get("columns") if isinstance(table.get("columns"), list) else []
    if columns and all(isinstance(col, str) for col in columns):
        table["columns"] = _table_columns(list(columns))
    return _flowi_set_inline_type(out, "lot_list" if out.get("lot_list") else "", prompt=prompt)


def _handle_current_step_from_progress_cache(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _flowi_current_step_prompt(prompt):
        return {"handled": False}
    preview = _structure_flowi_function_call(prompt, product=product, max_rows=max_rows)
    if ((preview.get("selected_function") or {}).get("name") != "query_lot_current_step_from_progress_cache"):
        return {"handled": False}
    args = ((preview.get("function_call") or {}).get("function") or {}).get("arguments") or {}
    roots = [str(x).strip() for x in (args.get("root_lot_ids") or []) if str(x).strip()]
    fabs = [str(x).strip() for x in (args.get("fab_lot_ids") or []) if str(x).strip()]
    lots = [str(x).strip() for x in (args.get("lot_ids") or []) if str(x).strip()]
    wafers = [str(x).strip() for x in (args.get("wafer_ids") or []) if str(x).strip()]
    product_hint = str(args.get("product") or product or "").strip()
    lookup_lot = fabs[0] if fabs else next((x for x in lots if "." in x), "")
    root = roots[0] if roots else (_flowi_root_from_fab_lot(lookup_lot) if lookup_lot else (lots[0] if lots else ""))
    wafer = wafers[0] if wafers else ""
    row = _flowi_latest_progress_cache_row(
        product=product_hint,
        root_lot_id=root,
        lot_id=lookup_lot,
        wafer_id=wafer,
        lot_wf=_flowi_lot_wf_id(root, wafer),
    )
    cols_out = ["product", "root_lot_id", "wafer_id", "lot_wf", "lot_id", "fab_lot_id", "step_id", "function_step", "update_time", "cache_source"]
    if not row:
        target = lookup_lot or root or (lots[0] if lots else "")
        wf_text = f" #{wafer}" if wafer else ""
        return {
            "handled": True,
            "intent": "lot_current_step_lookup",
            "action": "query_lot_current_step_from_progress_cache",
            "answer": f"{target}{wf_text}는 FileBrowser latest progress cache에서 현재 step을 찾지 못했습니다.",
            "feature": "filebrowser",
            "table": {"kind": "lot_current_step_lookup", "title": "Current lot step", "placement": "below", "columns": _table_columns(cols_out), "rows": [], "total": 0, "source": "filebrowser_latest"},
            "filters": {"product": product_hint, "root_lot_ids": roots, "fab_lot_ids": fabs, "wafer_ids": wafers, "source": "filebrowser_latest"},
        }
    _step_label, step_desc = _lot_wip_step_annotation(
        row.get("step_id"), row.get("product") or product_hint, row.get("function_step") or ""
    )
    row["step_desc"] = step_desc.get("step_desc") or ""
    if row["step_desc"] and "step_desc" not in cols_out:
        cols_out.insert(cols_out.index("function_step") + 1, "step_desc")
    answer = (
        f"{row.get('root_lot_id') or root} #{row.get('wafer_id') or wafer} 현재 step은 "
        f"step_id={row.get('step_id') or '-'}"
        f"{(' / function_step=' + row.get('function_step')) if row.get('function_step') else ''}"
        f"{(' / step_desc=' + row['step_desc']) if row['step_desc'] else ''} 입니다."
    )
    if row.get("update_time"):
        answer += f" 최신 cache 시간: {row.get('update_time')}."
    notice = _lot_wip_delay_notice(
        source="cache",
        cache_generated_at=_text(row.get("cache_generated_at")),
        latest_move=row.get("update_time") or "",
    )
    if notice:
        answer += f"\n\n{notice}"
    return _flowi_set_inline_type({
        "handled": True,
        "intent": "lot_current_step_lookup",
        "action": "query_lot_current_step_from_progress_cache",
        "answer": answer,
        "feature": "filebrowser",
        "lot_list": [{
            "product": row.get("product") or product_hint,
            "root_lot": row.get("root_lot_id") or "",
            "fab_lot": row.get("fab_lot_id") or row.get("lot_id") or "",
            "wafer": row.get("wafer_id") or "",
            "current_step": row.get("step_id") or "",
            "current_function_step": row.get("function_step") or "",
            "tkout_time": row.get("update_time") or "",
        }],
        "table": {"kind": "lot_current_step_lookup", "title": "Current lot step", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: row.get(k, "") for k in cols_out}], "total": 1, "source": "filebrowser_latest"},
        "filters": {"product": product_hint, "root_lot_ids": roots, "fab_lot_ids": fabs, "wafer_ids": wafers, "lot_wf_ids": args.get("lot_wf_ids") or [], "source": "filebrowser_latest"},
    }, "lot_list", prompt=prompt)


def _handle_tracker_lot_purpose_lookup(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _flowi_tracker_lot_purpose_prompt(prompt):
        return {"handled": False}
    preview = _structure_flowi_function_call(prompt, product=product, max_rows=max_rows)
    if ((preview.get("selected_function") or {}).get("name") != "query_tracker_lot_purpose"):
        return {"handled": False}
    args = ((preview.get("function_call") or {}).get("function") or {}).get("arguments") or {}
    lots = [str(x).strip() for x in (args.get("fab_lot_ids") or args.get("lot_ids") or args.get("root_lot_ids") or []) if str(x).strip()]
    target = lots[0] if lots else ""
    target_u = _upper(target)
    root = _flowi_root_from_fab_lot(target) or (target if _is_root_lot_token(target) else "")
    rows: list[dict[str, Any]] = []
    try:
        from routers import tracker as tracker_router
        issues = tracker_router._load()
    except Exception:
        issues = []
    for issue in issues or []:
        if not isinstance(issue, dict):
            continue
        for lot in issue.get("lots") or []:
            if not isinstance(lot, dict):
                continue
            candidates = {
                _upper(lot.get("lot_id")),
                _upper(lot.get("fab_lot_id")),
                _upper(lot.get("root_lot_id")),
            }
            exact = bool(target_u and target_u in candidates)
            root_match = bool(root and not exact and _upper(lot.get("root_lot_id")) == _upper(root) and "." not in target)
            if not (exact or root_match):
                continue
            purpose = _text(lot.get("purpose"))
            if not purpose:
                continue
            rows.append({
                "issue_id": issue.get("id") or "",
                "title": issue.get("title") or "",
                "status": issue.get("status") or "",
                "category": issue.get("category") or "",
                "root_lot_id": lot.get("root_lot_id") or "",
                "lot_id": lot.get("lot_id") or "",
                "fab_lot_id": lot.get("fab_lot_id") or "",
                "wafer_id": lot.get("wafer_id") or "",
                "purpose": purpose,
                "progress_note": lot.get("progress_note") or "",
            })
    limit = max(1, min(80, int(max_rows or 12) * 4))
    cols_out = ["issue_id", "title", "status", "category", "root_lot_id", "lot_id", "fab_lot_id", "wafer_id", "purpose", "progress_note"]
    if not rows:
        answer = f"{target or '해당 lot'}은 이슈추적에서 목적이 보이지 않습니다."
    else:
        title_line = f"{target or rows[0].get('lot_id') or rows[0].get('root_lot_id')} 이슈추적 목적"
        answer_lines = [
            title_line,
            "",
            "요약",
            f"- 이슈추적에서 lot 목적 {len(rows)}건을 찾았습니다.",
        ]
        if len(rows) > 1:
            answer_lines.append("- 여러 목적 후보가 있어 확인이 필요합니다.")
        answer_lines.extend(["", "관련 이슈"])
        for row in rows[:6]:
            lot_label = row.get("fab_lot_id") or row.get("lot_id") or row.get("root_lot_id") or "-"
            answer_lines.append(
                "- "
                + " / ".join([
                    str(row.get("issue_id") or "-"),
                    str(row.get("title") or "-"),
                    str(lot_label),
                    f"WF {row.get('wafer_id') or '-'}",
                    str(row.get("purpose") or "-"),
                    f"상태 {row.get('status') or '-'}",
                ])
            )
        if len(rows) > 6:
            answer_lines.append(f"- 외 {len(rows) - 6}건은 표에서 확인하세요.")
        answer_lines.extend(["", "근거", f"- tracker.issues / lot filter {target or root or '-'}"])
        answer = "\n".join(answer_lines)
    return _flowi_set_inline_type({
        "handled": True,
        "intent": "tracker_lot_purpose_lookup",
        "action": "query_tracker_lot_purpose",
        "answer": answer,
        "feature": "tracker",
        "table": {"kind": "tracker_lot_purpose_lookup", "title": "Tracker lot purpose", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in rows[:limit]], "total": len(rows)},
        "filters": {"lot_ids": lots, "root_lot_id": root, "source": "tracker.issues"},
    }, "table", prompt=prompt)


def _resolve_products_for_lots(lots: list[str], *, kinds: tuple[str, ...] = ("FAB", "ET", "INLINE", "ML_TABLE"), limit: int = 12) -> list[dict[str, Any]]:
    clean_lots = _flowi_lot_scope_terms([x for x in dict.fromkeys(_text(v) for v in lots) if x])
    if not clean_lots:
        return []
    out: dict[str, dict[str, Any]] = {}
    for kind in kinds:
        if kind == "FAB":
            files = _fab_files("")
        elif kind == "ET":
            files = _et_files("")
        elif kind == "INLINE":
            files = _inline_files("")
        elif kind == "ML_TABLE":
            files = _ml_files("")
        else:
            files = []
        if not files:
            continue
        try:
            lf = _scan_parquet(files[:240])
            cols = _schema_names(lf)
            product_col = _ci_col(cols, "product", "PRODUCT")
            root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
            lot_col = _ci_col(cols, "lot_id", "LOT_ID")
            fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
            if not (root_col or lot_col or fab_col):
                continue
            lf = _source_filter_lots(lf, cols, clean_lots)
            exprs = []
            if product_col:
                exprs.append(pl.col(product_col).cast(_STR, strict=False).alias("product"))
            else:
                exprs.append(pl.lit("").alias("product"))
            if root_col:
                exprs.append(pl.col(root_col).cast(_STR, strict=False).alias("root_lot_id"))
            elif lot_col:
                exprs.append(pl.col(lot_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id"))
            else:
                exprs.append(pl.col(fab_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id"))
            df = lf.select(exprs).drop_nulls(subset=["root_lot_id"]).limit(300).collect()
        except Exception:
            continue
        for row in df.to_dicts():
            product = _text(row.get("product"))
            root = _text(row.get("root_lot_id"))
            if not product:
                continue
            key = product.upper()
            cur = out.setdefault(key, {"product": product, "sources": set(), "lots": set(), "row_count": 0})
            cur["sources"].add(kind)
            if root:
                cur["lots"].add(root)
            cur["row_count"] += 1
    rows = []
    for rec in out.values():
        rows.append({
            "product": rec["product"],
            "sources": ",".join(sorted(rec["sources"])),
            "lots": ",".join(sorted(rec["lots"])[:8]),
            "row_count": rec["row_count"],
        })
    rows.sort(key=lambda r: (-int(r.get("row_count") or 0), r.get("product") or ""))
    return rows[:limit]


def _flowi_product_candidate_tool(prompt: str, candidates: list[dict[str, Any]], *, intent: str, answer: str = "") -> dict[str, Any] | None:
    rows = [row for row in (candidates or []) if _is_flowi_product_choice_name(str(row.get("product") or ""))]
    if not rows:
        return None
    choices = [
        {
            "id": f"product_{i}",
            "label": str(i + 1),
            "title": row["product"],
            "recommended": i == 0,
            "description": f"{row['sources']}에서 {row['row_count']} row 후보",
            "prompt": f"{row['product']} {prompt.strip()}",
        }
        for i, row in enumerate(rows[:4])
    ]
    return {
        "handled": True,
        "intent": intent,
        "action": "clarify_product",
        "answer": answer or "같은 lot/root_lot_id가 여러 product에서 발견됐습니다. product를 선택한 뒤 다시 진행해주세요.",
        "missing": ["product"],
        "pending_prompt": prompt.strip(),
        "clarification": {"question": "어느 product 기준으로 볼까요?", "choices": choices},
        "table": {
            "kind": "flowi_product_candidates",
            "title": "Product candidates by lot",
            "placement": "below",
            "columns": _table_columns(["product", "sources", "lots", "row_count"]),
            "rows": rows,
            "total": len(rows),
        },
    }


def _product_or_candidate_tool(prompt: str, product: str, lots: list[str], *, kinds: tuple[str, ...], intent: str, ask_if_any: bool = False) -> tuple[str, dict[str, Any] | None]:
    product_hint = _product_hint(prompt, product)
    if product_hint:
        return product_hint, None
    candidates = _resolve_products_for_lots(lots, kinds=kinds)
    candidates = [row for row in candidates if _is_flowi_product_choice_name(str(row.get("product") or ""))]
    if len(candidates) == 1:
        return candidates[0]["product"], None
    if ask_if_any:
        return "", _flowi_product_candidate_tool(
            prompt,
            candidates,
            intent=intent,
            answer="product가 없는 SplitTable 요청입니다. 어느 product 기준으로 볼지 선택해주세요.",
        )
    if len(candidates) > 1:
        return "", _flowi_product_candidate_tool(prompt, candidates, intent=intent)
    return "", None


def _step_query_terms(prompt: str, lots: list[str], product: str = "") -> list[str]:
    blocked = set(_STOP_TOKENS) | {
        "EQP", "EQUIPMENT", "장비", "설비", "STEP", "STEP_ID", "PROCESS_ID", "PROCESS",
        "FAB", "DB", "PRODUCT", "PROD",
    }
    blocked.update(_upper(v) for v in lots)
    blocked.update(_product_aliases(product))
    out: list[str] = []
    seen: set[str] = set()
    for tok in _query_tokens(prompt):
        key = _upper(tok)
        if not key or key in blocked or key.startswith("PROD"):
            continue
        if re.fullmatch(r"[A-Z]\d{4,}(?:[A-Z])?(?:\.\d+)?", key):
            continue
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out[:8]


def _is_fab_eqp_prompt(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    return any(t in low or t in text for t in _FLOWI_FAB_EQP_TERMS) and any(t in low or t in text for t in _FLOWI_STEP_WORDS)


def _handle_fab_eqp_lookup(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _is_fab_eqp_prompt(prompt):
        return {"handled": False}
    lots = _lot_tokens(prompt)
    if not lots:
        return {"handled": False}
    product_hint, candidate_tool = _product_or_candidate_tool(prompt, product, lots, kinds=("FAB",), intent="fab_eqp_lookup")
    if candidate_tool:
        return candidate_tool
    files = _fab_files(product_hint)
    if not files:
        return {
            "handled": True,
            "intent": "fab_eqp_lookup",
            "answer": "FAB parquet을 찾지 못했습니다. product 또는 DB root를 확인해주세요.",
            "table": {"kind": "fab_eqp_lookup", "title": "FAB EQP lookup", "placement": "below", "columns": _table_columns(["message"]), "rows": [{"message": "FAB not found"}], "total": 0},
        }
    lf = _scan_parquet(files)
    cols = _schema_names(lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    step_col = _ci_col(cols, "step_id", "STEP_ID")
    eqp_col = _ci_col(cols, "eqp_id", "EQP_ID", "equipment_id", "EQUIPMENT_ID")
    chamber_col = _ci_col(cols, "chamber_id", "CHAMBER_ID")
    ppid_col = _ci_col(cols, "ppid", "PPID")
    reticle_col = _ci_col(cols, "reticle_id", "RETICLE_ID")
    tkin_col = _ci_col(cols, "tkin_time", "TKIN_TIME", "start_time", "START_TIME")
    tkout_col = _ci_col(cols, "tkout_time", "TKOUT_TIME", "end_time", "END_TIME", "time", "TIME", "timestamp", "TIMESTAMP")
    if not step_col or not eqp_col:
        return {
            "handled": True,
            "intent": "fab_eqp_lookup",
            "answer": "FAB 데이터에서 step_id 또는 eqp_id 컬럼을 찾지 못했습니다.",
            "table": {"kind": "fab_eqp_lookup", "title": "FAB EQP lookup", "placement": "below", "columns": _table_columns(["message", "columns"]), "rows": [{"message": "missing step_id/eqp_id", "columns": ", ".join(cols[:40])}], "total": 1},
        }
    aliases = _product_aliases(product_hint)
    filters = []
    if aliases and product_col:
        filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    if lots:
        expr = _or_contains([c for c in (root_col, lot_col, fab_col) if c], lots)
        if expr is not None:
            filters.append(expr)
    wafers = _wafer_tokens(prompt)
    wf_expr = _wafer_match_expr(wafer_col, wafers)
    if wf_expr is not None:
        filters.append(wf_expr)
    for expr in filters:
        lf = lf.filter(expr)
    select_exprs = []
    for src, alias in (
        (product_col, "product"), (root_col, "root_lot_id"), (lot_col, "lot_id"), (fab_col, "fab_lot_id"),
        (wafer_col, "wafer_id"), (step_col, "step_id"), (eqp_col, "eqp_id"), (chamber_col, "chamber_id"),
        (ppid_col, "ppid"), (reticle_col, "reticle_id"), (tkin_col, "tkin_time"), (tkout_col, "tkout_time"),
    ):
        select_exprs.append(pl.col(src).cast(_STR, strict=False).alias(alias) if src else pl.lit("").alias(alias))
    try:
        df = lf.select(select_exprs).limit(5000).collect()
    except Exception as e:
        return {"handled": True, "intent": "fab_eqp_lookup", "answer": f"FAB EQP 조회 실패: {e}"}
    rows_all = df.to_dicts()
    terms = _step_query_terms(prompt, lots, product_hint)
    lot_set = {_upper(v) for v in lots}
    step_ids = {s for s in _step_tokens(prompt) if _upper(s) not in lot_set}
    rows = []
    for row in rows_all:
        func = _function_step_label(row.get("product") or product_hint, row.get("step_id"))
        hay = _upper(" ".join([
            row.get("step_id") or "",
            func,
            row.get("eqp_id") or "",
            row.get("chamber_id") or "",
            row.get("ppid") or "",
            row.get("reticle_id") or "",
        ]))
        if step_ids and _upper(row.get("step_id")) not in step_ids:
            continue
        if terms and not any(term in hay for term in terms):
            continue
        if not func and terms:
            func = next((term for term in terms if term in hay), "")
        row["function_step"] = func
        rows.append(row)
    if terms and not rows and rows_all:
        candidates = []
        seen = set()
        for row in rows_all:
            sid = _text(row.get("step_id"))
            if not sid or sid in seen:
                continue
            seen.add(sid)
            candidates.append({"step_id": sid, "function_step": _function_step_label(row.get("product") or product_hint, sid)})
        return {
            "handled": True,
            "intent": "fab_eqp_lookup",
            "action": "clarify_function_step",
            "answer": f"{', '.join(terms)}와 매칭되는 function step을 찾지 못했습니다. 아래 step 후보 중에서 선택해주세요.",
            "table": {
                "kind": "fab_step_candidates",
                "title": "FAB step candidates for lot",
                "placement": "below",
                "columns": _table_columns(["step_id", "function_step"]),
                "rows": candidates[:max(1, max_rows * 2)],
                "total": len(candidates),
            },
        }
    rows = rows or rows_all
    rows.sort(key=lambda r: str(r.get("tkout_time") or r.get("tkin_time") or ""), reverse=True)
    display_cols = ["product", "root_lot_id", "wafer_id", "step_id", "function_step", "eqp_id", "chamber_id", "ppid", "reticle_id", "lot_id", "fab_lot_id", "tkin_time", "tkout_time"]
    shown = [{k: row.get(k, "") for k in display_cols} for row in rows[:max(1, min(80, max_rows * 6))]]
    top = shown[0] if shown else {}
    answer = (
        f"{top.get('product') or product_hint or '-'} {', '.join(lots)} 기준 FAB EQP를 조회했습니다. "
        f"대표 결과: {top.get('step_id') or '-'}{('(' + top.get('function_step') + ')') if top.get('function_step') else ''} "
        f"EQP={top.get('eqp_id') or '-'}."
    )
    return {
        "handled": True,
        "intent": "fab_eqp_lookup",
        "action": "query_fab_eqp_by_function_step",
        "answer": answer,
        "table": {
            "kind": "fab_eqp_lookup",
            "title": "FAB EQP by step/function step",
            "placement": "below",
            "columns": _table_columns(display_cols),
            "rows": shown,
            "total": len(rows),
        },
        "filters": {"product": product_hint, "lots": lots, "wafers": wafers, "step_terms": terms},
    }


def _is_process_id_prompt(prompt: str) -> bool:
    up = _upper(prompt)
    return "PROCESS_ID" in up or "PROCESS ID" in up or "공정ID" in up or "프로세스" in prompt


def _handle_product_process_id_lookup(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _is_process_id_prompt(prompt):
        return {"handled": False}
    product_hint = _product_hint(prompt, product)
    if not product_hint:
        return {"handled": False}
    files = _fab_files(product_hint) or _ml_files(product_hint)
    source = "FAB" if _fab_files(product_hint) else "ML_TABLE"
    if not files:
        return {"handled": True, "intent": "product_process_id_lookup", "answer": f"{product_hint} 관련 FAB/ML_TABLE parquet을 찾지 못했습니다."}
    lf = _scan_parquet(files)
    cols = _schema_names(lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    process_col = _ci_col(cols, "process_id", "PROCESS_ID")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    time_col = _ci_col(cols, "tkout_time", "TKOUT_TIME", "time", "TIME", "timestamp", "TIMESTAMP", "updated_at", "UPDATED_AT")
    if not process_col:
        return {
            "handled": True,
            "intent": "product_process_id_lookup",
            "answer": f"{source} 데이터에서 process_id 컬럼을 찾지 못했습니다.",
            "table": {"kind": "process_id_lookup", "title": "process_id lookup", "placement": "below", "columns": _table_columns(["message", "columns"]), "rows": [{"message": "process_id column not found", "columns": ", ".join(cols[:50])}], "total": 1},
        }
    aliases = _product_aliases(product_hint)
    if aliases and product_col:
        lf = lf.filter(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    exprs = [pl.col(process_col).cast(_STR, strict=False).alias("process_id")]
    exprs.append(pl.col(product_col).cast(_STR, strict=False).alias("product") if product_col else pl.lit(product_hint).alias("product"))
    exprs.append(pl.col(root_col).cast(_STR, strict=False).alias("latest_root_lot_id") if root_col else pl.lit("").alias("latest_root_lot_id"))
    exprs.append(pl.col(time_col).cast(_STR, strict=False).alias("latest_time") if time_col else pl.lit("").alias("latest_time"))
    try:
        scoped = lf.select(exprs).drop_nulls(subset=["process_id"])
        if time_col:
            scoped = scoped.sort("latest_time", descending=True)
        df = (
            scoped.group_by(["product", "process_id"])
            .agg([
                pl.len().alias("row_count"),
                pl.col("latest_time").first().alias("latest_time"),
                pl.col("latest_root_lot_id").first().alias("latest_root_lot_id"),
            ])
            .sort(["latest_time", "row_count"], descending=[True, True])
            .limit(max(1, min(50, max_rows * 4)))
            .collect()
        )
    except Exception as e:
        return {"handled": True, "intent": "product_process_id_lookup", "answer": f"process_id 조회 실패: {e}"}
    rows = df.to_dicts()
    top = rows[0] if rows else {}
    answer = f"{product_hint}의 최신 {source} 기준 process_id는 {top.get('process_id') or '-'} 입니다." if rows else f"{product_hint}에서 process_id row를 찾지 못했습니다."
    for row in rows:
        row["source"] = source
    cols_out = ["product", "process_id", "row_count", "latest_time", "latest_root_lot_id", "source"]
    return {
        "handled": True,
        "intent": "product_process_id_lookup",
        "action": "query_product_process_id",
        "answer": answer,
        "table": {"kind": "process_id_lookup", "title": "Product process_id", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in rows], "total": len(rows)},
    }


def _load_flowi_meetings() -> list[dict[str, Any]]:
    data = load_json(PATHS.data_root / "meetings" / "meetings.json", [])
    return data if isinstance(data, list) else []


def _load_flowi_calendar_events() -> list[dict[str, Any]]:
    data = load_json(PATHS.data_root / "calendar" / "events.json", [])
    return data if isinstance(data, list) else []


def _meeting_visible_to_flowi(meeting: dict[str, Any], me: dict[str, Any]) -> bool:
    username = me.get("username") or "user"
    role = me.get("role") or "user"
    try:
        from routers.meetings import _meeting_visible, _my_meeting_group_ids
        return bool(_meeting_visible(meeting, username, role, _my_meeting_group_ids(username, role)))
    except Exception:
        if role == "admin":
            return True
        gids = meeting.get("group_ids") or []
        return not gids or meeting.get("owner") == username or meeting.get("created_by") == username


def _meeting_search_terms(prompt: str) -> list[str]:
    text = str(prompt or "").strip()
    stop = {
        "회의", "회의록", "결정사항", "결정", "액션", "액션아이템", "날짜별로", "정리",
        "보여줘", "찾아줘", "이전", "지난", "했던", "일", "뭐", "어떤", "에서", "만",
        "날짜", "시간", "일시", "언제", "몇시", "몇", "아젠다", "agenda", "차", "회차",
    }
    text = re.sub(r"\b\d+\s*(?:차|회차|번째)\b", " ", text)
    parts = re.split(r"[\s,./]+", text)
    out: list[str] = []
    for part in parts:
        item = part.strip(" ?!~요은는이가을를과와:")
        if not item or item in stop:
            continue
        item = re.sub(r"(회의에서|회의만|회의|아젠다는|아젠다)$", "", item)
        if item and item not in stop and item not in out:
            out.append(item)
    return out[:8]


def _meeting_session_idx_from_prompt(prompt: str) -> int | None:
    text = str(prompt or "")
    m = re.search(r"(\d{1,3})\s*(?:차|회차|번째)", text)
    if m:
        try:
            return max(1, int(m.group(1)))
        except Exception:
            return None
    aliases = {
        "첫": 1, "첫번": 1, "첫번째": 1,
        "두": 2, "둘": 2, "두번": 2, "두번째": 2,
        "세": 3, "셋": 3, "세번": 3, "세번째": 3,
        "네": 4, "넷": 4, "네번째": 4,
        "다섯": 5, "다섯번째": 5,
    }
    for key, value in aliases.items():
        if key in text and ("차" in text or "번째" in text or "회의" in text):
            return value
    return None


def _meeting_context_from_agent(agent_context: dict[str, Any] | None) -> dict[str, Any]:
    for msg in reversed(_flowi_context_messages(agent_context)):
        intent = str(msg.get("intent") or "")
        feature = str(msg.get("feature") or "")
        slots = msg.get("slots") if isinstance(msg.get("slots"), dict) else {}
        workflow = msg.get("workflow_state") if isinstance(msg.get("workflow_state"), dict) else {}
        workflow_slots = workflow.get("slots") if isinstance(workflow.get("slots"), dict) else {}
        merged_slots = {**workflow_slots, **slots}
        if intent != "meeting_recall_summary" and feature != "meeting" and not merged_slots.get("meeting_id"):
            continue
        out = {
            "meeting_id": str(merged_slots.get("meeting_id") or ""),
            "meeting_title": str(merged_slots.get("meeting_title") or ""),
            "session_idx": merged_slots.get("session_idx"),
        }
        try:
            if out["session_idx"] not in (None, ""):
                out["session_idx"] = int(out["session_idx"])
        except Exception:
            out["session_idx"] = None
        if out.get("meeting_id") or out.get("meeting_title") or out.get("session_idx"):
            return out
    return {}


def _is_meeting_recall_prompt(prompt: str, agent_context: dict[str, Any] | None = None) -> bool:
    text = str(prompt or "")
    meeting_terms = ("결정", "회의록", "아젠다", "했던 일", "정리", "액션", "지난", "날짜", "시간", "일시", "언제", "몇시")
    if ("회의" in text or "회의록" in text) and any(term in text for term in meeting_terms):
        return True
    if _meeting_context_from_agent(agent_context) and any(term in text for term in meeting_terms + ("차", "번째")):
        return True
    return False


def _meeting_issue_line(issue: Any) -> str:
    if not isinstance(issue, dict):
        return ""
    issue_id = str(issue.get("issue_id") or issue.get("id") or "").strip()
    title = str(issue.get("title") or "").strip()
    lots = []
    for lot in issue.get("lots") or []:
        if not isinstance(lot, dict):
            continue
        lot_label = str(lot.get("fab_lot_id") or lot.get("lot_id") or lot.get("root_lot_id") or "").strip()
        wafer = str(lot.get("wafer_id") or "").strip()
        purpose = str(lot.get("purpose") or lot.get("comment") or "").strip()
        parts = [lot_label]
        if wafer:
            parts.append(f"WF {wafer}")
        if purpose:
            parts.append(purpose)
        label = " / ".join([p for p in parts if p])
        if label:
            lots.append(label)
    pieces = [issue_id, title, "; ".join(lots[:4])]
    return " / ".join([p for p in pieces if p])


def _meeting_search_blob(meeting: dict[str, Any]) -> str:
    pieces = [
        meeting.get("title") or "",
        meeting.get("category") or "",
        meeting.get("owner") or "",
        meeting.get("status") or "",
    ]
    for session in meeting.get("sessions") or []:
        if not isinstance(session, dict):
            continue
        pieces.extend([session.get("status") or "", session.get("scheduled_at") or ""])
        for ag in session.get("agendas") or []:
            if not isinstance(ag, dict):
                continue
            pieces.extend([ag.get("title") or "", ag.get("description") or "", ag.get("owner") or ""])
            pieces.append(_meeting_issue_line(ag.get("issue_ref")))
        minutes = session.get("minutes") or {}
        if isinstance(minutes, dict):
            pieces.append(minutes.get("body") or "")
            for dec in minutes.get("decisions") or []:
                if isinstance(dec, dict):
                    pieces.extend([dec.get("text") or "", dec.get("due") or ""])
                else:
                    pieces.append(str(dec or ""))
            for action in minutes.get("action_items") or []:
                if isinstance(action, dict):
                    pieces.extend([
                        action.get("text") or "",
                        action.get("owner") or "",
                        action.get("due") or "",
                        action.get("status") or "",
                    ])
    return _upper(" ".join(str(p or "") for p in pieces))


def _meeting_recall_line(row: dict[str, Any]) -> str:
    date = str(row.get("date") or "-")
    meeting = str(row.get("meeting_title") or "-")
    idx = str(row.get("session_idx") or "").strip()
    session = f"{idx}차" if idx else "-"
    text = str(row.get("text") or "-")
    owner = str(row.get("owner") or "-")
    status = str(row.get("status") or "-")
    return f"{date} / {meeting} / {session} / {text} / 담당 {owner} / 상태 {status}"


def _meeting_recall_answer(scope: str, rows: list[dict[str, Any]]) -> str:
    sections = [
        ("결정사항", [r for r in rows if r.get("type") == "decision"]),
        ("액션아이템", [r for r in rows if r.get("type") == "action"]),
        ("변경점 일정", [r for r in rows if r.get("type") not in {"agenda", "minutes", "decision", "action", "issue", "session"}]),
        ("관련 이슈", [r for r in rows if r.get("type") == "issue"]),
        ("회의록", [r for r in rows if r.get("type") == "minutes"]),
        ("아젠다", [r for r in rows if r.get("type") in {"agenda", "session"}]),
    ]
    lines = [
        f"{scope} 회의/변경점 기록",
        "",
        "요약",
        f"- 회의관리/변경점 관리 저장 기록 {len(rows)}건을 찾았습니다.",
    ]
    for title, items in sections:
        if not items:
            continue
        lines.extend(["", title])
        for row in items[:8]:
            lines.append(f"- {_meeting_recall_line(row)}")
        if len(items) > 8:
            lines.append(f"- 외 {len(items) - 8}건은 표에서 확인하세요.")
    lines.extend(["", "근거", "- 회의관리 sessions, agendas, minutes와 변경점 캘린더 events만 사용했습니다."])
    return "\n".join(lines).strip()


def _handle_meeting_recall(
    prompt: str,
    max_rows: int,
    me: dict[str, Any],
    agent_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not _is_meeting_recall_prompt(prompt, agent_context):
        return {"handled": False}
    meetings = [m for m in _load_flowi_meetings() if isinstance(m, dict) and _meeting_visible_to_flowi(m, me)]
    context = _meeting_context_from_agent(agent_context)
    requested_idx = _meeting_session_idx_from_prompt(prompt)
    context_idx = context.get("session_idx") if isinstance(context.get("session_idx"), int) else None
    session_idx = requested_idx or context_idx
    terms = _meeting_search_terms(prompt)
    if not terms and context.get("meeting_title"):
        terms = [str(context.get("meeting_title"))]
    if context.get("meeting_id"):
        meetings = [m for m in meetings if str(m.get("id") or "") == str(context.get("meeting_id"))] or meetings
    if terms:
        def score(meeting: dict[str, Any]) -> int:
            hay = _meeting_search_blob(meeting)
            return sum(1 for term in terms if _upper(term) in hay)
        scored = [(score(m), m) for m in meetings]
        meetings = [m for s, m in scored if s > 0] or meetings
    want_actions = "액션" in prompt or "했던 일" in prompt or "할 일" in prompt
    want_decisions = "결정" in prompt or "decision" in prompt.lower()
    want_agenda = "아젠다" in prompt
    want_issues = any(term in prompt.lower() or term in prompt for term in ("이슈", "issue", "tracker", "이슈추적", "목적"))
    want_schedule = any(term in prompt for term in ("시간", "일시", "언제", "몇시")) or ("날짜" in prompt and "날짜별" not in prompt)
    want_minutes = "회의록" in prompt or "정리" in prompt
    rows: list[dict[str, Any]] = []
    matched_meetings: list[dict[str, Any]] = []
    matched_sessions: list[dict[str, Any]] = []
    for meeting in meetings:
        title = meeting.get("title") or ""
        for session in meeting.get("sessions") or []:
            try:
                idx_int = int(session.get("idx") or 0)
            except Exception:
                idx_int = 0
            if session_idx and idx_int != session_idx:
                continue
            if not any(m.get("id") == meeting.get("id") for m in matched_meetings):
                matched_meetings.append(meeting)
            matched_sessions.append(session)
            session_date = str(session.get("scheduled_at") or "")[:10]
            session_time = str(session.get("scheduled_at") or "")[11:16]
            scheduled_at = str(session.get("scheduled_at") or "")
            idx = session.get("idx") or ""
            minutes = session.get("minutes") or {}
            if want_schedule:
                agenda_count = len(session.get("agendas") or [])
                decision_count = len(minutes.get("decisions") or [])
                action_count = len(minutes.get("action_items") or [])
                rows.append({
                    "date": session_date,
                    "time": session_time,
                    "meeting_title": title,
                    "session_idx": idx,
                    "type": "session",
                    "text": f"{idx}차 회의 일시: {scheduled_at or '(미정)'} · agenda {agenda_count} · decision {decision_count} · action {action_count}",
                    "owner": meeting.get("owner") or "",
                    "status": session.get("status") or "",
                })
            if want_agenda:
                for ag in session.get("agendas") or []:
                    issue_line = _meeting_issue_line(ag.get("issue_ref"))
                    rows.append({
                        "date": session_date,
                        "time": session_time,
                        "meeting_title": title,
                        "session_idx": idx,
                        "type": "agenda",
                        "text": " - ".join(x for x in [ag.get("title") or "", ag.get("description") or "", issue_line] if x),
                        "owner": ag.get("owner") or "",
                        "status": session.get("status") or "",
                    })
            if want_issues:
                for ag in session.get("agendas") or []:
                    issue_line = _meeting_issue_line(ag.get("issue_ref"))
                    if not issue_line:
                        continue
                    issue = ag.get("issue_ref") if isinstance(ag.get("issue_ref"), dict) else {}
                    rows.append({
                        "date": session_date,
                        "time": session_time,
                        "meeting_title": title,
                        "session_idx": idx,
                        "type": "issue",
                        "text": issue_line,
                        "owner": issue.get("username") or ag.get("owner") or "",
                        "status": issue.get("status") or session.get("status") or "",
                    })
            if want_minutes and _text(minutes.get("body")):
                rows.append({
                    "date": session_date,
                    "time": session_time,
                    "meeting_title": title,
                    "session_idx": idx,
                    "type": "minutes",
                    "text": minutes.get("body") or "",
                    "owner": minutes.get("author") or "",
                    "status": session.get("status") or "",
                })
            if not want_actions or want_decisions or "전체" in prompt or "정리" in prompt:
                for dec in minutes.get("decisions") or []:
                    obj = {"text": dec} if isinstance(dec, str) else (dec if isinstance(dec, dict) else {})
                    if not _text(obj.get("text")):
                        continue
                    rows.append({
                        "date": str(obj.get("due") or session_date)[:10],
                        "time": session_time,
                        "meeting_title": title,
                        "session_idx": idx,
                        "type": "decision",
                        "text": obj.get("text") or "",
                        "owner": "",
                        "status": "calendar_pushed" if obj.get("calendar_pushed") else "",
                    })
            if want_actions or "전체" in prompt or "정리" in prompt:
                for ai in minutes.get("action_items") or []:
                    if not isinstance(ai, dict) or not _text(ai.get("text")):
                        continue
                    rows.append({
                        "date": str(ai.get("due") or session_date)[:10],
                        "time": session_time,
                        "meeting_title": title,
                        "session_idx": idx,
                        "type": "action",
                        "text": ai.get("text") or "",
                        "owner": ai.get("owner") or "",
                        "status": ai.get("status") or "",
                    })
    if "변경점" in prompt or "캘린더" in prompt:
        meeting_ids = {m.get("id") for m in meetings}
        meeting_labels = {str(m.get("title") or "") for m in meetings}
        term_needles = [_upper(t) for t in terms if t]
        for ev in _load_flowi_calendar_events():
            if not isinstance(ev, dict):
                continue
            ref = ev.get("meeting_ref") or {}
            event_blob = _upper(" ".join([
                ev.get("title") or "",
                ev.get("body") or "",
                ev.get("category") or "",
                ref.get("meeting_id") or "",
                ref.get("meeting_title") or "",
            ]))
            linked = bool(ref.get("meeting_id") and ref.get("meeting_id") in meeting_ids)
            manual_match = not ref.get("meeting_id") and (
                any(mid and _upper(mid) in event_blob for mid in meeting_ids)
                or any(label and _upper(label) in event_blob for label in meeting_labels)
                or any(term and term in event_blob for term in term_needles)
                or not term_needles
            )
            if not (linked or manual_match):
                continue
            rows.append({
                "date": ev.get("date") or "",
                "time": "",
                "meeting_title": ref.get("meeting_title") or "",
                "session_idx": "",
                "type": ev.get("source_type") or "calendar",
                "text": " - ".join(x for x in [ev.get("title") or "", ev.get("body") or ""] if x),
                "owner": ev.get("author") or "",
                "status": ev.get("status") or "",
            })
    rows = [r for r in rows if r.get("text")]
    rows.sort(key=lambda r: (str(r.get("date") or ""), str(r.get("time") or ""), str(r.get("meeting_title") or ""), str(r.get("session_idx") or "")), reverse=True)
    cols = ["date", "time", "meeting_title", "session_idx", "type", "text", "owner", "status"]
    if not rows:
        return {
            "handled": True,
            "intent": "meeting_recall_summary",
            "answer": "조건에 맞는 회의 기록을 찾지 못했습니다. 회의명이나 기간을 조금 더 구체적으로 알려주세요.",
            "table": {"kind": "meeting_recall", "title": "Meeting recall", "placement": "below", "columns": _table_columns(["message"]), "rows": [{"message": "no meeting records"}], "total": 0},
        }
    scope = " / ".join(terms) if terms else "전체 회의"
    if session_idx:
        scope += f" · {session_idx}차"
    title = "Meeting minutes" if want_minutes else ("Meeting session details" if (want_schedule or want_agenda) else "Meeting decisions/actions by date")
    answer = _meeting_recall_answer(scope, rows)
    primary_meeting = matched_meetings[0] if matched_meetings else {}
    primary_session = matched_sessions[0] if matched_sessions else {}
    sources = []
    for meeting in matched_meetings[:8]:
        for session in (meeting.get("sessions") or [])[:12]:
            if session_idx:
                try:
                    if int(session.get("idx") or 0) != int(session_idx):
                        continue
                except Exception:
                    continue
            sources.append({
                "meeting_id": meeting.get("id") or "",
                "meeting_title": meeting.get("title") or "",
                "session_id": session.get("id") or "",
                "session_idx": session.get("idx") or "",
                "scheduled_at": session.get("scheduled_at") or "",
                "agenda_count": len(session.get("agendas") or []),
                "decision_count": len((session.get("minutes") or {}).get("decisions") or []),
                "action_count": len((session.get("minutes") or {}).get("action_items") or []),
            })
            if len(sources) >= 12:
                break
        if len(sources) >= 12:
            break
    return {
        "handled": True,
        "intent": "meeting_recall_summary",
        "action": "query_meeting_calendar_records",
        "answer": answer,
        "feature_entrypoints": [item for item in FLOWI_FEATURE_ENTRYPOINTS if item["key"] in {"meeting", "calendar"}],
        "table": {
            "kind": "meeting_recall",
            "title": title,
            "placement": "below",
            "columns": _table_columns(cols),
            "rows": [{k: row.get(k, "") for k in cols} for row in rows[:max(1, min(120, max_rows * 8))]],
            "total": len(rows),
        },
        "filters": {"terms": terms, "session_idx": session_idx or ""},
        "sources": sources,
        "slots": {
            "meeting_id": primary_meeting.get("id") or context.get("meeting_id") or "",
            "meeting_title": primary_meeting.get("title") or context.get("meeting_title") or "",
            "session_id": primary_session.get("id") or "",
            "session_idx": primary_session.get("idx") or session_idx or "",
        },
    }


def _detect_app_write_feature(prompt: str) -> str:
    text = str(prompt or "")
    low = text.lower()
    has_write_intent = (
        any(term in low or term in text for term in _FLOWI_APP_WRITE_TERMS)
        or any(term in low or term in text for term in _FLOWI_APP_CREATE_TERMS)
        or any(term in low or term in text for term in _FLOWI_APP_MODIFY_TERMS)
        or ("변경" in text.replace("변경점", ""))
    )
    if not has_write_intent:
        return ""
    # "안올라왔는데" 같은 freshness 질문은 write가 아니다.
    if any(term in text for term in ("안올라", "안 올라", "최근업데이트", "업데이트 되었")):
        return ""
    for feature, hints in _FLOWI_APP_WRITE_HINTS.items():
        if any(h in low or h in text for h in hints):
            return feature
    return ""


def _flowi_app_write_mode(prompt: str) -> str:
    text = str(prompt or "")
    low = text.lower()
    create = any(term in low or term in text for term in _FLOWI_APP_CREATE_TERMS)
    modify = any(term in low or term in text for term in _FLOWI_APP_MODIFY_TERMS)
    # "변경점 등록"은 calendar create 의미라서 수정 요청으로 보지 않는다.
    change_text = text.replace("변경점", "")
    if "변경" in change_text and not create:
        modify = True
    if modify:
        return "modify"
    if create:
        return "create"
    return ""


def _flowi_prompt_title(prompt: str, feature: str) -> str:
    text = str(prompt or "").strip()
    quoted = re.findall(r"[\"'“”‘’「」『』](.+?)[\"'“”‘’「」『』]", text)
    if quoted:
        text = max(quoted, key=len)
    for pat in (
        r"(?:이름|제목|title)\s*[:=]\s*([^\n,;/]+)",
        r"(?:이름|제목|title)\s*(?:은|는)\s*([^\n,;/]+)",
        r"([A-Za-z0-9_.-]{1,80})\s*(?:이름|제목|title)\s*으로",
    ):
        m = re.search(pat, text, flags=re.I)
        if m:
            title = re.sub(r"\s+", " ", m.group(1)).strip(" \t\r\n-_:,.;")
            if title:
                return title[:120]
    feature_words = {
        "tracker": r"(?:이슈추적|이슈\s*추적|이슈|tracker|issue|트래커)",
        "meeting": r"(?:회의|미팅|meeting)",
        "inform": r"(?:인폼|inform)",
        "calendar": r"(?:일정|캘린더|calendar|변경점)",
    }.get(feature, "")
    if feature_words:
        m = re.search(rf"{feature_words}\s+(.{{1,80}}?)(?:이라고|라고|이라는|라는)", text, flags=re.I)
        if m:
            title = re.sub(r"\s+", " ", m.group(1)).strip(" \t\r\n-_:,.;")
            if title:
                return title[:120]
    if feature == "meeting":
        for pat in (
            r"^\s*(.{1,80}?)(?:이라고|라고|이라는|라는)\s*",
            r"^\s*(.{1,80}?)(?:\s+)?(?:회의|미팅)\s*(?:하나|한\s*개|1개)?\s*(?:등록|만들|생성|추가)",
        ):
            m = re.search(pat, text, flags=re.I)
            if m:
                title = re.sub(r"\s+", " ", m.group(1)).strip(" \t\r\n-_:,.;")
                if title:
                    return title[:120]
    remove_terms = [
        "등록해줘", "등록해주세요", "만들어줘", "만들어주세요", "생성해줘", "생성해주세요",
        "추가해줘", "추가해주세요", "넣어줘", "넣어주세요", "남겨줘", "남겨주세요",
        "기록해줘", "기록해주세요", "올려줘", "올려주세요",
        "등록", "만들어", "생성", "추가", "넣어", "남겨", "기록", "올려",
        "인폼", "inform", "이슈", "issue", "tracker", "트래커", "회의", "meeting",
        "일정", "캘린더", "calendar", "변경점", "아젠다", "회의록", "주세요", "해줘",
    ]
    for term in remove_terms:
        text = re.sub(re.escape(term), " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" \t\r\n-_:,.;")
    if not text:
        text = f"{_feature_title(feature)} 자동 등록"
    return text[:120]


_FLOWI_WEEKDAY_WORDS = (
    ("월요일", 0), ("화요일", 1), ("수요일", 2), ("목요일", 3),
    ("금요일", 4), ("토요일", 5), ("일요일", 6),
)


def _flowi_prompt_weekdays(prompt: str) -> list[int]:
    text = str(prompt or "")
    days = []
    for word, idx in _FLOWI_WEEKDAY_WORDS:
        if word in text and idx not in days:
            days.append(idx)
    return days


def _flowi_prompt_time(prompt: str) -> tuple[int, int] | None:
    text = str(prompt or "")
    m = re.search(r"(오전|오후|am|pm)?\s*(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분?)?", text, flags=re.I)
    if not m:
        m = re.search(r"(오전|오후|am|pm)?\s*(\d{1,2})\s*:\s*(\d{2})", text, flags=re.I)
    if not m:
        return None
    meridiem = (m.group(1) or "").lower()
    hour = int(m.group(2))
    minute = int(m.group(3) or 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    if meridiem in {"오후", "pm"} and hour < 12:
        hour += 12
    if meridiem in {"오전", "am"} and hour == 12:
        hour = 0
    return hour, minute


def _flowi_prompt_meeting_schedule(prompt: str) -> tuple[str, dict[str, Any]]:
    text = str(prompt or "")
    weekdays = _flowi_prompt_weekdays(text)
    time_pair = _flowi_prompt_time(text)
    recurrence = {"type": "none", "count_per_week": 0, "weekday": [], "note": ""}
    if any(term in text.lower() or term in text for term in ("매주", "매 주", "주마다", "weekly")):
        recurrence = {
            "type": "weekly",
            "count_per_week": len(weekdays) or 1,
            "weekday": weekdays,
            "note": text[:200],
        }
    date_s = _flowi_prompt_date(text)
    if not date_s and weekdays:
        today = datetime.now().date()
        target_wd = weekdays[0]
        days_ahead = (target_wd - today.weekday()) % 7
        candidate = today + timedelta(days=days_ahead)
        if time_pair:
            now = datetime.now()
            cand_dt = datetime(candidate.year, candidate.month, candidate.day, time_pair[0], time_pair[1])
            if cand_dt <= now:
                candidate = candidate + timedelta(days=7)
        date_s = candidate.isoformat()
    if date_s and time_pair:
        return f"{date_s}T{time_pair[0]:02d}:{time_pair[1]:02d}:00", recurrence
    if date_s:
        return f"{date_s}T00:00:00", recurrence
    return "", recurrence


def _flowi_prompt_field(prompt: str, names: tuple[str, ...], limit: int = 80) -> str:
    for name in names:
        m = re.search(rf"(?:{re.escape(name)})\s*[:=]\s*([^\n,;/]+)", str(prompt or ""), flags=re.I)
        if m:
            return m.group(1).strip()[:limit]
    return ""


def _flowi_prompt_content(prompt: str, limit: int = 4000) -> str:
    text = str(prompt or "")
    m = re.search(r"(?:내용|본문|description|desc)\s*(?:은|는|:|=)?\s*(.+?)(?:\s*(?:적어줘|작성해줘|등록해줘|넣어줘|남겨줘)\s*)?$", text, flags=re.I | re.S)
    if not m:
        return ""
    content = re.sub(r"\s+", " ", m.group(1)).strip(" \t\r\n-_:,.;")
    return content[:limit]


def _flowi_prompt_inform_text(prompt: str, limit: int = 4000) -> str:
    explicit = _flowi_prompt_content(prompt, limit=limit)
    if explicit:
        return explicit
    text = str(prompt or "")
    m = re.search(r"(?:인폼\s*로그|인폼로그|인폼|inform)\s+(.+?)(?:\s*으로)?\s*(?:등록|생성|추가|남겨|기록|올려)", text, flags=re.I | re.S)
    if not m:
        return ""
    body = re.sub(r"\s+", " ", m.group(1)).strip(" \t\r\n-_:,.;")
    return body[:limit]


def _extract_flowi_splittable_note_payload(prompt: str) -> dict[str, Any] | None:
    text = str(prompt or "")
    idx = text.upper().find(_FLOWI_SPLITTABLE_NOTE_MARKER)
    if idx < 0:
        return None
    tail = text[idx + len(_FLOWI_SPLITTABLE_NOTE_MARKER):].strip()
    if tail.startswith(":"):
        tail = tail[1:].strip()
    if not tail:
        return {}
    try:
        obj, _end = json.JSONDecoder().raw_decode(tail)
    except Exception as e:
        return {"_parse_error": str(e)}
    return obj if isinstance(obj, dict) else {"_parse_error": "JSON object가 필요합니다."}


def _flowi_splittable_note_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    if _FLOWI_SPLITTABLE_NOTE_MARKER in text.upper():
        return True
    has_split = any(t in low or t in text for t in ("split table", "splittable", "스플릿", "스플릿테이블", "스플릿 테이블"))
    has_note = any(t in low or t in text for t in ("꼬리표", "태그", "tag", "메모", "memo", "코멘트", "comment"))
    has_write = any(t in low or t in text for t in _FLOWI_APP_WRITE_TERMS + _FLOWI_APP_CREATE_TERMS)
    has_lot_wafer = bool(_lot_tokens(text) and _wafer_tokens(text))
    issue_like = any(t in low or t in text for t in ("이상있", "이상 있", "문제", "불량", "issue", "fail", "failure"))
    return bool(has_note and has_write and (has_split or (_product_hint(text) and has_lot_wafer) or (has_lot_wafer and not issue_like)))


def _clean_flowi_splittable_note_text(candidate: str, prompt: str) -> str:
    text = re.sub(r"\s+", " ", str(candidate or "")).strip(" \t\r\n-_:,.;'\"“”‘’")
    text = re.sub(r"(?:이라고|라고|이라는|라는)\s*$", "", text).strip(" \t\r\n-_:,.;'\"“”‘’")
    for term in (
        "스플릿 테이블", "스플릿테이블", "split table", "splittable",
        "꼬리표", "태그", "tag", "메모", "memo", "코멘트", "comment",
        "달아줘", "달아주세요", "붙여줘", "붙여주세요", "등록해줘", "등록해주세요",
        "추가해줘", "추가해주세요", "남겨줘", "남겨주세요", "기록해줘", "기록해주세요",
    ):
        text = re.sub(re.escape(term), " ", text, flags=re.I)
    for lot in _lot_tokens(prompt):
        text = re.sub(rf"\b{re.escape(lot)}\b\s*(?:에|에는|으로|로|를|을|은|는)?", " ", text, flags=re.I)
    text = re.sub(r"#\s*\d{1,4}\s*(?:번|번\s*WF|WF|WAFER|웨이퍼|에|에는|으로|로|를|을|은|는)?", " ", text, flags=re.I)
    text = re.sub(r"\b(?:WF|WAFER|W)\s*0?\d{1,4}\b\s*(?:에|에는|으로|로|를|을|은|는)?", " ", text, flags=re.I)
    product = _product_hint(prompt)
    if product:
        text = re.sub(rf"\b{re.escape(product)}\b\s*(?:에|에는|으로|로|를|을|은|는)?", " ", text, flags=re.I)
    text = re.sub(r"^(?:에|에는|으로|로|를|을|은|는)\s+", "", text)
    text = re.sub(r"\s+", " ", text).strip(" \t\r\n-_:,.;'\"“”‘’")
    return text[:2000]


def _flowi_prompt_splittable_note_text(prompt: str) -> str:
    text = str(prompt or "")
    marker = re.search(r"(꼬리표|태그|tag|메모|memo|코멘트|comment)", text, flags=re.I)
    if marker:
        after = text[marker.end():]
        after = re.sub(r"^\s*(?:로|를|을|은|는|:|=|-)?\s*", "", after)
        m = re.search(
            r"(.+?)(?:이라고|라고|이라는|라는)?\s*(?:달아|붙여|등록|추가|남겨|기록|저장|add|save|create)",
            after,
            flags=re.I | re.S,
        )
        body = m.group(1) if m else after
        cleaned = _clean_flowi_splittable_note_text(body, prompt)
        if cleaned:
            return cleaned
    for pat in (
        r"(.{1,200}?)(?:이라는|이라고|라는|라고)\s*(?:꼬리표|태그|tag|메모|memo|코멘트|comment)?\s*(?:달아|붙여|등록|추가|남겨|기록|저장)",
        r"(.{1,200}?)\s*(?:꼬리표|태그|tag|메모|memo|코멘트|comment)(?:를|을)?\s*(?:달아|붙여|등록|추가|남겨|기록|저장)",
    ):
        m = re.search(pat, text, flags=re.I | re.S)
        if m:
            cleaned = _clean_flowi_splittable_note_text(m.group(1), prompt)
            if cleaned:
                return cleaned
    return ""


def _flowi_splittable_product_id(product: str) -> str:
    raw = _upper(product)
    if not raw:
        return ""
    if raw.startswith("ML_TABLE_"):
        raw = raw[len("ML_TABLE_"):]
    if raw in {"PRODUCT_A", "PRODUCT_A0", "PRODUCT_A1", "PRODA0", "PRODA1"}:
        raw = "PRODA"
    elif raw == "PRODUCT_B":
        raw = "PRODB"
    if not raw:
        return ""
    return f"ML_TABLE_{raw}"


def _flowi_splittable_note_confirm_text(product: str, root_lot_id: str, text: str, scope: str = "lot", wafers: list[str] | None = None) -> str:
    basis = re.sub(r"\s+", " ", str(text or "")).strip()[:80]
    wf = ",".join(str(w) for w in (wafers or []) if str(w).strip())
    return f"SPLITTABLE_NOTE_CONFIRM::{product}::{root_lot_id}::{scope}::{wf}::{basis}"


def _flowi_splittable_note_table(rows: list[dict[str, Any]], title: str = "SplitTable lot note") -> dict[str, Any]:
    return {
        "kind": "splittable_lot_note",
        "title": title,
        "placement": "below",
        "columns": _table_columns(["field", "value"]),
        "rows": rows,
        "total": len(rows),
    }


def _flowi_splittable_note_product_choices(prompt: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    choices: list[dict[str, Any]] = []
    for row in candidates:
        product = _flowi_splittable_product_id(row.get("product") or "")
        if not product or product in seen:
            continue
        seen.add(product)
        choices.append({
            "id": f"product_{len(choices) + 1}",
            "label": str(len(choices) + 1),
            "title": product,
            "recommended": len(choices) == 0,
            "description": f"{row.get('sources') or 'data'} 기준 후보",
            "prompt": f"{product} {prompt.strip()}",
        })
    return choices[:4]


def _flowi_splittable_note_payload(prompt: str, me: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    classified = _classified_lot_tokens(prompt)
    root_lots = classified.get("root_lot_ids") or []
    fab_lots = classified.get("fab_lot_ids") or []
    root_lot_id = root_lots[0] if root_lots else ((fab_lots[0][:5] if fab_lots else "") or (_lot_tokens(prompt)[0] if _lot_tokens(prompt) else ""))
    wafer_ids = _wafer_tokens(prompt)
    note_text = _flowi_prompt_splittable_note_text(prompt)
    product = _flowi_splittable_product_id(_product_hint(prompt))
    missing: list[str] = []
    if not root_lot_id:
        missing.append("root_lot_id")
    if not note_text:
        missing.append("꼬리표 내용")
    if not product and root_lot_id:
        candidates = _resolve_products_for_lots([root_lot_id], kinds=("ML_TABLE", "FAB"), limit=8)
        choices = _flowi_splittable_note_product_choices(prompt, candidates)
        if len(choices) == 1:
            product = choices[0]["title"]
        elif len(choices) > 1:
            return None, {
                "handled": True,
                "intent": "splittable_lot_note_needs_product",
                "action": "clarify_product",
                "answer": "같은 lot 후보가 여러 product에서 발견됐습니다. 스플릿 테이블 꼬리표를 등록할 product를 선택해주세요.",
                "feature": "splittable",
                "missing": ["product"],
                "pending_prompt": prompt,
                "clarification": {"question": "어느 스플릿 테이블 product에 꼬리표를 등록할까요?", "choices": choices},
                "table": _flowi_splittable_note_table([
                    {"field": "status", "value": "needs_product"},
                    {"field": "root_lot_id", "value": root_lot_id},
                    {"field": "note", "value": note_text},
                ], title="SplitTable note needs product"),
            }
    if not product:
        missing.append("product")
    if missing:
        return None, _flowi_app_write_missing("splittable", missing, prompt, product, [root_lot_id] if root_lot_id else [], [])
    return {
        "scope": "wafer" if wafer_ids else "lot",
        "product": product,
        "root_lot_id": root_lot_id,
        "wafer_ids": wafer_ids,
        "text": note_text,
        "username": me.get("username") or "user",
    }, None


def _save_flowi_splittable_note(payload: dict[str, Any]) -> dict[str, Any]:
    from routers import splittable as splittable_router
    product = _flowi_splittable_product_id(payload.get("product") or "")
    root_lot_id = _upper(payload.get("root_lot_id") or "")
    scope = str(payload.get("scope") or "lot").strip()
    wafer_ids = [str(w).strip() for w in (payload.get("wafer_ids") or []) if str(w).strip()]
    text = str(payload.get("text") or "").strip()
    username = _safe_username(payload.get("username") or "user")
    if not product:
        raise ValueError("product가 필요합니다.")
    if not root_lot_id:
        raise ValueError("root_lot_id가 필요합니다.")
    if not text:
        raise ValueError("꼬리표 내용이 비어 있습니다.")
    if len(text) > 2000:
        raise ValueError("꼬리표 내용은 2000자 이하로 입력해주세요.")
    if scope == "wafer":
        if not wafer_ids:
            raise ValueError("wafer scope에는 wafer_id가 필요합니다.")
        wafer_id = _normalize_wafer_id(wafer_ids[0])
        if not wafer_id:
            raise ValueError("유효한 wafer_id가 필요합니다.")
        key = splittable_router._notes_key_wafer(product, root_lot_id, wafer_id)
    else:
        scope = "lot"
        wafer_id = ""
        key = splittable_router._notes_key_lot(product, root_lot_id)
    entry = {
        "id": splittable_router._new_note_id(),
        "scope": scope,
        "key": key,
        "text": text,
        "username": username,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    if wafer_id:
        entry["wafer_id"] = wafer_id
    entries = splittable_router._load_notes()
    entries.append(entry)
    splittable_router._save_notes(entries)
    return entry


def _extract_flowi_splittable_plan_payload(prompt: str) -> dict[str, Any] | None:
    text = str(prompt or "")
    idx = text.upper().find(_FLOWI_SPLITTABLE_PLAN_MARKER)
    if idx < 0:
        return None
    tail = text[idx + len(_FLOWI_SPLITTABLE_PLAN_MARKER):].strip()
    if tail.startswith(":"):
        tail = tail[1:].strip()
    if not tail:
        return {}
    try:
        obj, _end = json.JSONDecoder().raw_decode(tail)
    except Exception as e:
        return {"_parse_error": str(e)}
    return obj if isinstance(obj, dict) else {"_parse_error": "JSON object가 필요합니다."}


def _flowi_splittable_plan_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    if _FLOWI_SPLITTABLE_PLAN_MARKER in text.upper():
        return True
    has_plan = any(t in low or t in text for t in ("plan", "플랜", "계획"))
    has_knob_or_split = any(t in low or t in text for t in ("knob", "노브", "스플릿", "splittable", "split table")) or bool(_flowi_func_step_token(text))
    has_write = any(t in low or t in text for t in ("넣어", "입력", "저장", "등록", "plan해", "plan 해", "set", "save"))
    return bool(has_plan and has_knob_or_split and has_write)


def _flowi_splittable_plan_confirm_text(product: str, root_lot_id: str, knob_col: str, plans: dict[str, Any]) -> str:
    basis = f"{product}|{root_lot_id}|{knob_col}|{len(plans or {})}"
    return "SPLITTABLE_PLAN_CONFIRM::" + basis[:160]


def _flowi_plan_table(rows: list[dict[str, Any]], title: str = "SplitTable plan draft") -> dict[str, Any]:
    return {
        "kind": "splittable_plan",
        "title": title,
        "placement": "below",
        "columns": _table_columns(["field", "value"]),
        "rows": rows,
        "total": len(rows),
    }


def _flowi_plan_value_from_tail(tail: str) -> str:
    text = re.sub(r"\s+", " ", str(tail or "")).strip()
    step = _flowi_func_step_token(text)
    if step:
        text = re.sub(re.escape(step), " ", text, flags=re.I)
    text = re.sub(r"\b\d+\.\d+\s+[A-Z][A-Z0-9_/]*\b", " ", text, flags=re.I)
    text = re.sub(r"\b(?:까지|부터|은|는|에|으로|로|값|value)\b", " ", text, flags=re.I)
    m = re.search(r"\b([A-Za-z0-9_.-]{1,60})\s*(?:로|으로)?\s*(?:plan|플랜|계획)", text, flags=re.I)
    if m:
        val = m.group(1).strip(" .,:;")
        if _upper(val) not in {"PLAN", "SAVE", "SET", "KNOB", "WF", "WAFER"}:
            return val[:80]
    # "plan PPID_03_3_S1 넣어줘" — 값이 plan 키워드 뒤에 오는 어순도 지원.
    m = re.search(r"(?:plan|플랜|계획)\s*(?:은|는|을|를|으로|로|:|=)?\s*([A-Za-z0-9_.-]{2,60})", text, flags=re.I)
    if m:
        val = m.group(1).strip(" .,:;")
        if _upper(val) not in {"PLAN", "SAVE", "SET", "KNOB", "WF", "WAFER"}:
            return val[:80]
    text = re.sub(r"^(?:은|는|에|으로|로|:|=|-)\s*", "", text)
    m = re.search(r"([A-Za-z0-9_.-]{1,60})", text)
    if not m:
        return ""
    val = m.group(1).strip(" .,:;")
    if _upper(val) in {"PLAN", "SAVE", "SET", "KNOB", "WF", "WAFER"}:
        return ""
    return val[:80]


def _flowi_wafer_ids_from_fragment(fragment: str) -> list[str]:
    text = str(fragment or "")
    out: list[str] = []
    seen: set[str] = set()
    def add(raw: Any) -> None:
        val = _normalize_wafer_id(raw)
        if val and val not in seen:
            seen.add(val)
            out.append(val)
    m = re.search(r"(\d{1,4})\s*(?:~|-|–|—|to)\s*#?\s*(\d{1,4})", text, flags=re.I)
    if m:
        try:
            start, end = int(m.group(1)), int(m.group(2))
        except Exception:
            start, end = 0, -1
        if start > end:
            start, end = end, start
        for n in range(max(1, start), min(FLOWI_MAX_WAFER_ID, end) + 1):
            add(n)
        return out
    for m in re.finditer(r"(?:#|WF|WAFER|W)?\s*0?(\d{1,4})", text, flags=re.I):
        add(m.group(1))
    return out


def _flowi_parse_splittable_plan_assignments(prompt: str) -> tuple[list[dict[str, Any]], list[str]]:
    text = str(prompt or "")
    assignments: list[dict[str, Any]] = []
    invalid_wafers: list[str] = []
    used: set[str] = set()
    range_pat = re.compile(
        r"(?P<wf>#\s*\d{1,4}\s*(?:~|-|–|—|to)\s*#?\s*\d{1,4}|#\s*\d{1,4}"
        r"|\b(?:WF|WAFER)\s*\d{1,4}\s*(?:~|-|–|—|to)\s*#?\s*\d{1,4}|\b(?:WF|WAFER)\s*\d{1,4}\b"
        r"|웨이퍼\s*\d{1,4}\s*(?:~|-|–|—)\s*\d{1,4}|웨이퍼\s*\d{1,4})"
        r"(?P<tail>.{0,80}?)(?=,|그리고|나머지|$)",
        flags=re.I | re.S,
    )
    for m in range_pat.finditer(text):
        frag = m.group("wf") or ""
        raw_nums = [int(x) for x in re.findall(r"\d{1,4}", frag)]
        for raw in raw_nums:
            if raw < 1 or raw > FLOWI_MAX_WAFER_ID:
                invalid_wafers.append(str(raw))
        wafers = _flowi_wafer_ids_from_fragment(frag)
        if not wafers:
            continue
        value = _flowi_plan_value_from_tail(m.group("tail") or "")
        if not value:
            continue
        for wf in wafers:
            used.add(wf)
        assignments.append({"wafers": wafers, "value": value, "label": frag.strip()})
    rest_pat = re.compile(r"나머지(?:는|은|에)?(?P<tail>.{0,80}?)(?=,|그리고|$)", flags=re.I | re.S)
    for m in rest_pat.finditer(text):
        value = _flowi_plan_value_from_tail(m.group("tail") or "")
        if not value:
            continue
        rest = [wf for wf in _all_valid_wafer_ids() if wf not in used]
        if rest:
            assignments.append({"wafers": rest, "value": value, "label": "나머지"})
            used.update(rest)
    return assignments, sorted(set(invalid_wafers), key=lambda x: int(x))


def _flowi_splittable_plan_product_choices(prompt: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    choices = []
    seen: set[str] = set()
    for row in candidates:
        product = _flowi_splittable_product_id(row.get("product") or "")
        if not product or product in seen:
            continue
        seen.add(product)
        choices.append({
            "id": f"product_{len(choices) + 1}",
            "label": str(len(choices) + 1),
            "title": product,
            "recommended": len(choices) == 0,
            "description": f"{row.get('sources') or 'ML_TABLE'} 기준 후보",
            "prompt": f"{product} {prompt.strip()}",
        })
    return choices[:3]


def _flowi_pick_plan_row_from_view(rows: list[dict[str, Any]], prompt: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    planable = [
        row for row in rows
        if isinstance(row, dict) and any(bool(c.get("can_plan")) for c in (row.get("cells") or []) if isinstance(c, dict))
    ]
    if not planable:
        return None, []
    terms = _flowi_knob_tokens(prompt)
    up = _upper(prompt)
    explicit_terms = set()
    for term in terms:
        explicit_terms.add(_upper(term))
        if not _upper(term).startswith("KNOB_"):
            explicit_terms.add("KNOB_" + _upper(term))
    for m in re.finditer(r"\b([A-Z])\s+KNOB\b|\bKNOB[_\s-]?([A-Z0-9]+)\b", up):
        raw = m.group(1) or m.group(2) or ""
        if raw:
            explicit_terms.add("KNOB_" + raw)
    if explicit_terms:
        exact = []
        for row in planable:
            text = _upper(" ".join([str(row.get("parameter") or ""), str(row.get("display") or "")]))
            if any(term and term in text for term in explicit_terms):
                exact.append(row)
        if exact:
            return exact[0], exact
    return (planable[0], planable) if len(planable) == 1 else (None, planable)


def _flowi_plan_column_choices(prompt: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    choices = []
    for row in rows[:3]:
        param = str(row.get("parameter") or "")
        display = str(row.get("display") or param)
        choices.append({
            "id": f"plan_col_{len(choices) + 1}",
            "label": str(len(choices) + 1),
            "title": display,
            "recommended": len(choices) == 0,
            "description": f"{param} 컬럼에 plan을 넣습니다.",
            "prompt": f"{prompt.strip()} {param}",
        })
    return choices


def _flowi_build_plan_from_splittable_view(
    product: str,
    root_lot_id: str,
    prompt: str,
    assignments: list[dict[str, Any]],
    invalid_wafers: list[str],
    me: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    step = _flowi_func_step_token(prompt)
    args = {"product": product, "root_lot_ids": [root_lot_id], "wafer_ids": [], "step": step}
    view_tool = _flowi_query_splittable_view_tool(args, product, prompt, max_rows=80)
    if not view_tool.get("handled"):
        return None, None
    split_rows = (view_tool.get("split_view") or {}).get("rows") or []
    picked, candidates = _flowi_pick_plan_row_from_view(split_rows, prompt)
    if not picked and candidates:
        return None, _flowi_app_write_missing(
            "splittable",
            ["KNOB/FAB plan 컬럼"],
            prompt,
            product,
            [root_lot_id],
            _wafer_tokens(prompt),
            choices=_flowi_plan_column_choices(prompt, candidates),
        )
    if not picked:
        return None, None
    header_to_idx = {
        str(h).lstrip("#"): i
        for i, h in enumerate((view_tool.get("split_view") or {}).get("headers") or [])
    }
    cells = picked.get("cells") if isinstance(picked.get("cells"), list) else []
    plans: dict[str, str] = {}
    summary_parts = []
    for item in assignments:
        value = str(item.get("value") or "").strip()
        wafers = [wf for wf in (item.get("wafers") or []) if _normalize_wafer_id(wf)]
        if not value or not wafers:
            continue
        saved = 0
        for wf in wafers:
            idx = header_to_idx.get(str(wf))
            cell = cells[idx] if idx is not None and idx < len(cells) and isinstance(cells[idx], dict) else {}
            key = str(cell.get("key") or "").strip()
            if key and cell.get("can_plan"):
                plans[key] = value
                saved += 1
        summary_parts.append(f"{item.get('label')}: {value} ({saved}wf)")
    if not plans:
        return None, _flowi_app_write_missing("splittable", ["SplitTable 화면 기준 plan 가능한 cell"], prompt, product, [root_lot_id], _wafer_tokens(prompt))
    return {
        "product": product,
        "root_lot_id": root_lot_id,
        "knob": picked.get("parameter") or "",
        "plans": plans,
        "assignments": assignments,
        "summary": summary_parts,
        "invalid_wafers": invalid_wafers,
        "username": me.get("username") or "user",
        "knob_candidates": [row.get("parameter") for row in candidates if isinstance(row, dict)][:12],
        "source": "splittable.view",
    }, None


def _flowi_build_splittable_plan_payload(prompt: str, me: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    lots = _lot_tokens(prompt)
    classified = _classified_lot_tokens(prompt)
    root_lot_id = (classified.get("root_lot_ids") or lots or [""])[0]
    if "." in root_lot_id:
        root_lot_id = root_lot_id.split(".", 1)[0]
    product = _flowi_splittable_product_id(_product_hint(prompt))
    if not product and root_lot_id:
        candidates = _resolve_products_for_lots([root_lot_id], kinds=("ML_TABLE",), limit=8)
        choices = _flowi_splittable_plan_product_choices(prompt, candidates)
        if len(choices) == 1:
            product = choices[0]["title"]
        elif len(choices) > 1:
            return None, {
                "handled": True,
                "intent": "splittable_plan_needs_product",
                "action": "clarify_product",
                "answer": "같은 lot 후보가 여러 product에서 발견됐습니다. plan을 넣을 SplitTable product를 선택해주세요.",
                "feature": "splittable",
                "missing": ["product"],
                "pending_prompt": prompt,
                "clarification": {"question": "어느 SplitTable product에 plan을 넣을까요?", "choices": choices},
                "table": _flowi_plan_table([
                    {"field": "status", "value": "needs_product"},
                    {"field": "root_lot_id", "value": root_lot_id},
                ]),
            }
    assignments, invalid_wafers = _flowi_parse_splittable_plan_assignments(prompt)
    missing = []
    if not root_lot_id:
        missing.append("root_lot_id")
    if not product:
        missing.append("product")
    if not assignments:
        missing.append("wafer별 plan 값")
    if missing:
        return None, _flowi_app_write_missing("splittable", missing, prompt, product, [root_lot_id] if root_lot_id else [], _wafer_tokens(prompt))
    view_draft, view_missing = _flowi_build_plan_from_splittable_view(product, root_lot_id, prompt, assignments, invalid_wafers, me)
    if view_missing:
        return None, view_missing
    if view_draft:
        return view_draft, None
    files = _ml_files(product)
    if not files:
        return None, {
            "handled": True,
            "intent": "splittable_plan_failed",
            "action": "prepare_splittable_plan",
            "blocked": True,
            "answer": f"{product} ML_TABLE parquet을 찾지 못해 plan cell을 만들 수 없습니다.",
            "feature": "splittable",
        }
    try:
        lf = _scan_parquet(files)
        cols = _schema_names(lf)
        product_col = _ci_col(cols, "product", "PRODUCT")
        root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
        lot_col = _ci_col(cols, "lot_id", "LOT_ID")
        knob_cols = [c for c in cols if _upper(c).startswith("KNOB_")]
        if product_col:
            aliases = _product_aliases(product)
            if aliases:
                lf = lf.filter(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
        lot_expr = _or_contains([c for c in (root_col, lot_col) if c], [root_lot_id])
        if lot_expr is not None:
            lf = lf.filter(lot_expr)
        knob_col, knob_candidates = _select_knob_column(lf, knob_cols, prompt, [root_lot_id], [])
    except Exception as e:
        return None, {
            "handled": True,
            "intent": "splittable_plan_failed",
            "action": "prepare_splittable_plan",
            "blocked": True,
            "answer": f"SplitTable plan 준비 중 ML_TABLE 조회에 실패했습니다: {e}",
            "feature": "splittable",
        }
    if not knob_col:
        return None, _flowi_app_write_missing("splittable", ["KNOB 컬럼"], prompt, product, [root_lot_id], _wafer_tokens(prompt))
    plans: dict[str, str] = {}
    summary_parts = []
    for item in assignments:
        value = str(item.get("value") or "").strip()
        wafers = [wf for wf in (item.get("wafers") or []) if _normalize_wafer_id(wf)]
        if not value or not wafers:
            continue
        for wf in wafers:
            plans[f"{root_lot_id}|{wf}|{knob_col}"] = value
        summary_parts.append(f"{item.get('label')}: {value} ({len(wafers)}wf)")
    if not plans:
        return None, _flowi_app_write_missing("splittable", ["유효 wafer_id 1~25 plan"], prompt, product, [root_lot_id], _wafer_tokens(prompt))
    return {
        "product": product,
        "root_lot_id": root_lot_id,
        "knob": knob_col,
        "plans": plans,
        "assignments": assignments,
        "summary": summary_parts,
        "invalid_wafers": invalid_wafers,
        "username": me.get("username") or "user",
        "knob_candidates": knob_candidates[:12],
    }, None


def _save_flowi_splittable_plan(payload: dict[str, Any]) -> dict[str, Any]:
    from routers import splittable as splittable_router
    product = _flowi_splittable_product_id(payload.get("product") or "")
    root_lot_id = _upper(payload.get("root_lot_id") or "")
    plans = payload.get("plans") if isinstance(payload.get("plans"), dict) else {}
    username = _safe_username(payload.get("username") or "user")
    clean_plans = {
        str(k): str(v)
        for k, v in plans.items()
        if str(k or "").startswith(f"{root_lot_id}|") and _normalize_wafer_id(str(k).split("|")[1] if "|" in str(k) else "")
    }
    if not product or not root_lot_id or not clean_plans:
        raise ValueError("product/root_lot_id/plans가 필요합니다.")
    req = splittable_router.PlanReq(product=product, plans=clean_plans, username=username, root_lot_id=root_lot_id)
    result = splittable_router.save_plan(req)
    return result if isinstance(result, dict) else {"ok": True, "saved": len(clean_plans)}
