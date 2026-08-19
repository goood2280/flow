def _step_id_terms_from_prompt(prompt: str, lots: list[str] | None = None, product: str = "") -> list[str]:
    blocked = set(_STOP_TOKENS)
    blocked.update(_upper(v) for v in lots or [])
    blocked.update(_product_aliases(product))
    out: list[str] = []
    seen: set[str] = set()
    for sid in _step_tokens(prompt):
        key = _upper(sid)
        if key and key not in seen and key not in blocked:
            seen.add(key)
            out.append(sid)
    text = str(prompt or "")
    for m in re.finditer(r"\bstep[_\s-]*id\s*(?:=|:|가|이|는|은|가\s*이건데|이\s*이건데)?\s*([A-Za-z0-9_.-]+)", text, flags=re.I):
        raw = (m.group(1) or "").strip(" .,;:()[]{}")
        key = _upper(raw)
        if not key or key in blocked or key.startswith(("PPID", "PROD", "KNOB")):
            continue
        if not _is_step_id_token(key) and key not in _known_func_step_names():
            continue
        if key not in seen:
            seen.add(key)
            out.append(raw)
    return out[:6]


def _ppid_tokens(prompt: str) -> list[str]:
    text = str(prompt or "")
    out: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"\b(PP(?:ID)?[A-Za-z0-9_.-]{1,80})\b", text, flags=re.I):
        raw = (m.group(1) or "").strip(" .,;:()[]{}")
        key = _upper(raw)
        if key and key not in {"PPID", "PP"} and key not in seen:
            seen.add(key)
            out.append(raw)
    toks = _tokens(text)
    for i, tok in enumerate(toks[:-1]):
        if _upper(tok) == "PPID":
            raw = toks[i + 1].strip(" .,;:()[]{}")
            key = _upper(raw)
            if key and key not in _STOP_TOKENS and key not in seen:
                seen.add(key)
                out.append(raw)
    return out[:6]


def _flowi_schema_catalog_payload() -> dict[str, Any]:
    try:
        data = load_json(PATHS.data_root / "schema_relations.json", {"relations": [], "column_catalog": []})
    except Exception:
        data = {"relations": [], "column_catalog": []}
    if not isinstance(data, dict):
        return {"relations": [], "column_catalog": []}
    if not isinstance(data.get("column_catalog"), list):
        data["column_catalog"] = []
    return data


def _flowi_registered_file_candidates(*, purposes: tuple[str, ...] = (), name_hints: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    payload = _flowi_schema_catalog_payload()
    purpose_set = {_upper(p) for p in purposes if p}
    hint_set = {_upper(h).replace(".CSV", "").replace(".PARQUET", "") for h in name_hints if h}
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(path: Path, *, source_id: str = "", purpose: str = "", relation_id: str = "") -> None:
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path
        key = str(resolved)
        if key in seen or not resolved.is_file() or resolved.suffix.lower() not in {".csv", ".parquet"}:
            return
        seen.add(key)
        candidates.append({
            "path": resolved,
            "source_id": source_id or relation_id or resolved.name,
            "purpose": purpose,
            "relation_id": relation_id,
        })

    roots = [PATHS.base_root, PATHS.db_root, PATHS.data_root]
    for row in payload.get("column_catalog") or []:
        if not isinstance(row, dict):
            continue
        relation_id = str(row.get("relation_id") or "").strip()
        source_id = str(row.get("source_id") or "").strip()
        purpose = str(row.get("purpose") or row.get("source_purpose") or "").strip()
        file_name = str(row.get("file_name") or row.get("source_file") or "").strip()
        hay = _upper(" ".join([relation_id, source_id, purpose, file_name]))
        purpose_hit = not purpose_set or _upper(purpose) in purpose_set
        hint_hit = not hint_set or any(hint and hint in hay for hint in hint_set)
        if not (purpose_hit and hint_hit):
            continue
        raw_path = str(row.get("source_path") or row.get("path") or "").strip()
        if raw_path:
            add(Path(raw_path), source_id=source_id, purpose=purpose, relation_id=relation_id)
        if file_name:
            rel = Path(file_name.replace("\\", "/")).name
            for root in roots:
                add(root / rel, source_id=source_id, purpose=purpose, relation_id=relation_id)
    for hint in name_hints:
        rel = Path(str(hint or "").replace("\\", "/")).name
        if not rel:
            continue
        for root in (PATHS.base_root, PATHS.db_root):
            add(root / rel, source_id=rel, purpose=purposes[0] if purposes else "", relation_id=Path(rel).stem)
    return candidates


def _flowi_scan_registered_table(*, purposes: tuple[str, ...], name_hints: tuple[str, ...]) -> dict[str, Any]:
    errors: list[str] = []
    for candidate in _flowi_registered_file_candidates(purposes=purposes, name_hints=name_hints):
        path = candidate.get("path")
        if not isinstance(path, Path):
            continue
        try:
            if path.suffix.lower() == ".csv":
                lf = pl.scan_csv(str(path), infer_schema_length=5000, try_parse_dates=False)
            elif path.suffix.lower() == ".parquet":
                lf = pl.scan_parquet(str(path))
            else:
                continue
            return {**candidate, "lf": lf, "columns": _schema_names(lf)}
        except Exception as e:
            errors.append(f"{path.name}: {e}")
    return {"lf": None, "columns": [], "errors": errors}


_FLOWI_STEP_MATCHING_HINTS = ("Vehicle_matching.csv", "step_matching.csv", "matching_step.csv")


def _knob_rulebook_feature_terms(prompt: str) -> list[str]:
    text = str(prompt or "")
    out: list[str] = []
    seen: set[str] = set()

    def add(raw: Any) -> None:
        value = _text(raw).strip(" .,;:()[]{}")
        key = _upper(value)
        if not key or key in seen:
            return
        seen.add(key)
        out.append(value)

    patterns = (
        r"(?<![A-Za-z0-9_.-])((?:\d+\.)+\d+\s+[A-Za-z][A-Za-z0-9_/.-]*)(?=\s*(?:KNOB|노브)\b)",
        r"(?:KNOB|노브)\s+((?:\d+\.)+\d+\s+[A-Za-z][A-Za-z0-9_/.-]*)",
    )
    for pat in patterns:
        for m in re.finditer(pat, text, flags=re.I):
            add(m.group(1))
    return out[:6]


def _knob_rulebook_lookup_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    up = _upper(text)
    if "KNOB" not in up and "노브" not in text:
        return False
    if not any(t in low or t in text for t in ("rule", "rulebook", "룰", "규칙", "매칭", "matching")):
        return False
    return bool(_ppid_tokens(text) or _step_id_terms_from_prompt(text) or _flowi_func_step_token(text) or _knob_rulebook_feature_terms(text))


def _flowi_product_cell_matches(raw: Any, aliases: set[str]) -> bool:
    if not aliases:
        return True
    parts = [p for p in re.split(r"[,;/|]+", str(raw or "")) if p.strip()]
    values = {_upper(p) for p in (parts or [raw]) if _upper(p)}
    expanded: set[str] = set()
    for value in values:
        expanded.update(_product_aliases(value) or {value})
    return bool(expanded & aliases)


def _flowi_step_mapping_query_terms(prompt: str, product: str = "") -> list[str]:
    blocked = {
        "STEP",
        "STEP_ID",
        "FUNCTION_STEP",
        "FUNC_STEP",
        "PRODUCT",
        "PROD",
        "KNOB",
        "PPID",
    } | set(_STOP_TOKENS)
    blocked.update(_product_aliases(product))
    out: list[str] = []
    seen: set[str] = set()

    def add(raw: Any) -> None:
        value = _text(raw).strip(" .,;:()[]{}")
        key = _upper(value)
        if not key or key in blocked or key in seen:
            return
        seen.add(key)
        out.append(value)

    for term in _step_id_terms_from_prompt(prompt, product=product):
        add(term)
    add(_flowi_func_step_token(prompt))
    for tok in _tokens(prompt):
        key = _upper(tok)
        if key in blocked or key.startswith(("ML_TABLE_", "PRODUCT_")):
            continue
        if _is_step_id_token(key) or "_" in key:
            add(tok)
    return out[:8]


def _flowi_step_matching_maps(product: str = "") -> dict[str, Any]:
    src = _flowi_scan_registered_table(purposes=("matching", "lookup_table"), name_hints=_FLOWI_STEP_MATCHING_HINTS)
    lf = src.get("lf")
    if lf is None:
        return {"rows": [], "by_step": {}, "by_step_rows": {}, "by_function": {}, "source": src}
    cols = src.get("columns") or []
    product_col = _ci_col(cols, "product", "PRODUCT", "process_id", "prod")
    step_col = _ci_col(cols, "step_id", "STEP_ID", "raw_step_id", "step")
    func_col = _ci_col(cols, "function_step", "func_step", "step_desc", "canonical_step", "step_function", "FUNCTION_STEP", "FUNC_STEP")
    if not step_col or not func_col:
        return {"rows": [], "by_step": {}, "by_step_rows": {}, "by_function": {}, "source": src}
    aliases = _product_aliases(product)
    exprs = [
        pl.col(product_col).cast(_STR, strict=False).alias("product") if product_col else pl.lit(product).alias("product"),
        pl.col(step_col).cast(_STR, strict=False).alias("step_id"),
        pl.col(func_col).cast(_STR, strict=False).alias("function_step"),
    ]
    try:
        raw_rows = lf.select(exprs).drop_nulls(subset=["step_id", "function_step"]).collect().to_dicts()
    except Exception:
        raw_rows = []
    rows: list[dict[str, Any]] = []
    for row in raw_rows:
        if aliases and product_col and not _flowi_product_cell_matches(row.get("product"), aliases):
            continue
        step = _text(row.get("step_id"))
        func = _text(row.get("function_step"))
        if not step or not func:
            continue
        rows.append({
            "product": _text(row.get("product") or product),
            "step_id": step,
            "function_step": func,
        })
    by_step: dict[str, dict[str, Any]] = {}
    by_step_rows: dict[str, list[dict[str, Any]]] = {}
    by_function: dict[tuple[str, str], list[str]] = {}
    for row in rows:
        step = _upper(row.get("step_id"))
        func = _upper(row.get("function_step"))
        prod = _upper(row.get("product"))
        if step:
            by_step.setdefault(step, row)
            by_step_rows.setdefault(step, []).append(row)
        if func:
            by_function.setdefault((prod, func), []).append(_text(row.get("step_id")))
            by_function.setdefault(("", func), []).append(_text(row.get("step_id")))
    return {"rows": rows, "by_step": by_step, "by_step_rows": by_step_rows, "by_function": by_function, "source": src}


def _step_mapping_lookup_intent(prompt: str, product: str = "") -> bool:
    text = str(prompt or "")
    low = text.lower()
    has_step_word = any(t in low or t in text for t in ("step", "step_id", "function_step", "func_step", "스텝", "공정"))
    has_lookup_word = any(t in low or t in text for t in ("어떤", "무슨", "뭐", "영향", "매칭", "mapping", "lookup", "관련", "연결"))
    query_terms = _flowi_step_mapping_query_terms(text, product=product)
    if has_step_word and _step_id_terms_from_prompt(text, product=product):
        return True
    return bool(has_step_word and has_lookup_word and query_terms)


def _flowi_matching_source_id(src: dict[str, Any], fallback: str) -> str:
    return _text(src.get("source_id") or Path(str(src.get("path") or fallback)).name)


def _flowi_collect_ppid_rulebook_rows(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    src = _flowi_scan_registered_table(purposes=("rulebook", "lookup_table"), name_hints=("ppid_knob.csv", "knob_ppid.csv"))
    lf = src.get("lf")
    if lf is None:
        return {"rows": [], "source": src, "columns": [], "errors": src.get("errors") or []}
    cols = src.get("columns") or []
    product_col = _ci_col(cols, "product", "PRODUCT", "process_id", "prod")
    feature_col = _ci_col(cols, "feature_name", "FEATURE_NAME", "feature", "FEATURE", "knob", "KNOB")
    func_col = _ci_col(cols, "function_step", "func_step", "step_desc", "canonical_step", "step_function", "FUNCTION_STEP", "FUNC_STEP")
    rule_col = _ci_col(cols, "rule_order", "RULE_ORDER", "order", "ORDER", "priority", "PRIORITY")
    op_col = _ci_col(cols, "operator", "OPERATOR", "op", "OP")
    ppid_col = _ci_col(cols, "ppid", "PPID", "category", "CATEGORY", "rule_category", "RULE_CATEGORY")
    if not (feature_col or func_col):
        return {"rows": [], "source": src, "columns": cols, "errors": ["feature/function column not found"]}
    exprs = [
        pl.col(product_col).cast(_STR, strict=False).alias("product") if product_col else pl.lit(product).alias("product"),
        pl.col(feature_col).cast(_STR, strict=False).alias("feature_name") if feature_col else pl.lit("").alias("feature_name"),
        pl.col(func_col).cast(_STR, strict=False).alias("function_step") if func_col else pl.lit("").alias("function_step"),
        pl.col(rule_col).cast(_STR, strict=False).alias("rule_order") if rule_col else pl.lit("").alias("rule_order"),
        pl.col(op_col).cast(_STR, strict=False).alias("operator") if op_col else pl.lit("").alias("operator"),
        pl.col(ppid_col).cast(_STR, strict=False).alias("ppid") if ppid_col else pl.lit("").alias("ppid"),
    ]
    try:
        raw_rows = lf.select(exprs).limit(max(100, min(5000, max_rows * 200))).collect().to_dicts()
    except Exception as e:
        return {"rows": [], "source": src, "columns": cols, "errors": [str(e)]}

    terms = [_upper(t) for t in _flowi_step_mapping_query_terms(prompt, product=product) if _upper(t)]
    aliases = _product_aliases(product)
    rows: list[dict[str, Any]] = []
    for row in raw_rows:
        if aliases and product_col and not _flowi_product_cell_matches(row.get("product"), aliases):
            continue
        hay_values = [_upper(row.get("feature_name")), _upper(row.get("function_step")), _upper(row.get("ppid"))]
        if terms and not any(term and any(term in hay or hay in term for hay in hay_values if hay) for term in terms):
            continue
        clean = {k: _text(row.get(k)) for k in ("product", "feature_name", "function_step", "rule_order", "operator", "ppid")}
        if clean.get("function_step") or clean.get("feature_name"):
            rows.append(clean)
    return {"rows": rows, "source": src, "columns": cols, "errors": []}


def _flowi_step_rows_for_function(step_maps: dict[str, Any], function_step: str, product: str = "") -> list[dict[str, Any]]:
    func = _upper(function_step)
    aliases = _product_aliases(product)
    out: list[dict[str, Any]] = []
    for row in step_maps.get("rows") or []:
        if _upper(row.get("function_step")) != func:
            continue
        if aliases and not _flowi_product_cell_matches(row.get("product"), aliases):
            continue
        out.append(row)
    return out


def _flowi_group_step_ids_by_product(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for row in rows:
        prod = _text(row.get("product")) or "(common)"
        sid = _text(row.get("step_id"))
        if not sid:
            continue
        grouped.setdefault(prod, [])
        if sid not in grouped[prod]:
            grouped[prod].append(sid)
    return grouped


def _flowi_step_group_text(grouped: dict[str, list[str]]) -> str:
    return "; ".join(f"{prod}: {', '.join(step_ids)}" for prod, step_ids in grouped.items() if step_ids)


def _handle_step_mapping_lookup(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    product_hint = _product_hint(prompt, product)
    if not _step_mapping_lookup_intent(prompt, product_hint):
        return {"handled": False}

    step_maps = _flowi_step_matching_maps(product_hint)
    step_src = step_maps.get("source") or {}
    step_source_id = _flowi_matching_source_id(step_src, "step_matching.csv")
    step_terms = _step_id_terms_from_prompt(prompt, product=product_hint)
    query_terms = _flowi_step_mapping_query_terms(prompt, product=product_hint)
    table_rows: list[dict[str, Any]] = []
    term_resolution: list[dict[str, Any]] = []
    source_ids: list[str] = [step_source_id] if step_source_id else []

    if step_terms:
        direct_rows: list[dict[str, Any]] = []
        for term in step_terms:
            direct_rows.extend(step_maps.get("by_step_rows", {}).get(_upper(term), []) or [])
        seen_direct: set[tuple[str, str, str]] = set()
        for row in direct_rows:
            key = (_text(row.get("product")), _text(row.get("step_id")), _text(row.get("function_step")))
            if key in seen_direct:
                continue
            seen_direct.add(key)
            table_rows.append({
                "product": key[0],
                "step_id": key[1],
                "function_step": key[2],
                "feature_name": "",
                "ppid": "",
                "rule_order": "",
                "mapping_source": step_source_id,
                "rulebook_source": "",
            })
        if table_rows:
            funcs = sorted({_text(row.get("function_step")) for row in table_rows if _text(row.get("function_step"))})
            products = sorted({_text(row.get("product")) for row in table_rows if _text(row.get("product"))})
            if len(funcs) == 1:
                product_text = ", ".join(products) if products else (product_hint or "공통")
                answer = f"{step_terms[0]}은 {product_text} 기준 {funcs[0]} step입니다. 근거: {step_source_id}"
            else:
                answer = (
                    f"{step_terms[0]}은 제품별 function_step mapping이 달라 product 확인이 필요합니다. "
                    f"후보 function_step: {', '.join(funcs)}. 근거: {step_source_id}"
                )
            term_resolution.append({
                "token": step_terms[0],
                "meaning": "step_id를 matching CSV의 function_step/step_desc로 해석",
                "wiki_refs": [step_source_id],
                "query_filter": f"step_id={step_terms[0]} product={product_hint or '(all)'}",
                "status": "resolved",
            })
            cols_out = ["product", "step_id", "function_step", "mapping_source"]
            return {
                "handled": True,
                "intent": "step_mapping_lookup",
                "action": "query_step_mapping_lookup",
                "answer": answer,
                "feature": "knowledge",
                "source_ids": source_ids,
                "table": {"kind": "step_mapping_lookup", "title": "Step matching lookup", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in table_rows], "total": len(table_rows), "source": step_source_id},
                "filters": {"product": product_hint, "step_id_terms": step_terms, "query_terms": query_terms, "matching_file": _path_tail(step_src.get("path")) if isinstance(step_src.get("path"), Path) else "", "row_count": len(table_rows)},
                "term_resolution": term_resolution,
            }

    rulebook = _flowi_collect_ppid_rulebook_rows(prompt, product_hint, max_rows)
    rule_src = rulebook.get("source") or {}
    rule_source_id = _flowi_matching_source_id(rule_src, "ppid_knob.csv")
    if rulebook.get("rows") and rule_source_id and rule_source_id not in source_ids:
        source_ids.insert(0, rule_source_id)
    matched_functions: list[str] = []
    for rule in rulebook.get("rows") or []:
        func = _text(rule.get("function_step"))
        if not func:
            continue
        if func not in matched_functions:
            matched_functions.append(func)
        scoped_product = product_hint or _text(rule.get("product"))
        step_rows = _flowi_step_rows_for_function(step_maps, func, scoped_product)
        grouped = _flowi_group_step_ids_by_product(step_rows)
        if not grouped:
            table_rows.append({
                "product": _text(rule.get("product")),
                "step_id": "",
                "function_step": func,
                "feature_name": _text(rule.get("feature_name")),
                "ppid": _text(rule.get("ppid")),
                "rule_order": _text(rule.get("rule_order")),
                "mapping_source": step_source_id,
                "rulebook_source": rule_source_id,
            })
            continue
        for prod, step_ids in grouped.items():
            table_rows.append({
                "product": prod,
                "step_id": ", ".join(step_ids),
                "function_step": func,
                "feature_name": _text(rule.get("feature_name")),
                "ppid": _text(rule.get("ppid")),
                "rule_order": _text(rule.get("rule_order")),
                "mapping_source": step_source_id,
                "rulebook_source": rule_source_id,
            })

    if not table_rows and not matched_functions:
        function_hits: list[dict[str, Any]] = []
        for term in query_terms:
            function_hits.extend(_flowi_step_rows_for_function(step_maps, term, product_hint))
        grouped = _flowi_group_step_ids_by_product(function_hits)
        for prod, step_ids in grouped.items():
            table_rows.append({
                "product": prod,
                "step_id": ", ".join(step_ids),
                "function_step": query_terms[0] if query_terms else "",
                "feature_name": "",
                "ppid": "",
                "rule_order": "",
                "mapping_source": step_source_id,
                "rulebook_source": "",
            })
        if table_rows and query_terms:
            matched_functions = [query_terms[0]]

    cols_out = ["product", "feature_name", "function_step", "step_id", "ppid", "rule_order", "mapping_source", "rulebook_source"]
    if table_rows:
        grouped = _flowi_group_step_ids_by_product([
            {"product": row.get("product"), "step_id": sid.strip()}
            for row in table_rows
            for sid in str(row.get("step_id") or "").split(",")
            if sid.strip()
        ])
        group_text = _flowi_step_group_text(grouped)
        feature_token = next((_text(row.get("feature_name")) for row in table_rows if _text(row.get("feature_name"))), query_terms[0] if query_terms else "요청 항목")
        if rulebook.get("rows"):
            answer = (
                f"{feature_token}은 {rule_source_id}에서 {', '.join(matched_functions[:4])}로 해석됐고, "
                f"{step_source_id} 기준 step_id는 {group_text or '매칭 없음'} 입니다. 근거: {rule_source_id} -> {step_source_id}"
            )
            term_resolution.append({
                "token": feature_token,
                "meaning": "ppid_knob.csv feature_name/step_desc에서 function_step 후보 확인",
                "wiki_refs": [rule_source_id],
                "query_filter": f"feature/function contains {query_terms}",
                "status": "resolved",
            })
        else:
            answer = f"{matched_functions[0]}에 매핑된 step_id는 {group_text or '매칭 없음'} 입니다. 근거: {step_source_id}"
        term_resolution.append({
            "token": ", ".join(matched_functions[:4]) if matched_functions else (query_terms[0] if query_terms else ""),
            "meaning": "matching CSV에서 function_step/step_desc를 step_id 목록으로 확장",
            "wiki_refs": [step_source_id],
            "query_filter": f"function_step in {matched_functions or query_terms} product={product_hint or '(all)'}",
            "status": "resolved",
        })
        return {
            "handled": True,
            "intent": "step_mapping_lookup",
            "action": "query_step_mapping_lookup",
            "answer": answer,
            "feature": "knowledge",
            "source_ids": source_ids,
            "table": {"kind": "step_mapping_lookup", "title": "Step matching lookup", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in table_rows[:max(1, min(100, max_rows * 8))]], "total": len(table_rows), "source": " -> ".join(source_ids)},
            "filters": {
                "product": product_hint,
                "step_id_terms": step_terms,
                "query_terms": query_terms,
                "function_steps": matched_functions,
                "rulebook_file": _path_tail(rule_src.get("path")) if isinstance(rule_src.get("path"), Path) else "",
                "matching_file": _path_tail(step_src.get("path")) if isinstance(step_src.get("path"), Path) else "",
                "row_count": len(table_rows),
                "search_conditions": {"product": product_hint or "(all)", "feature_or_function_contains": query_terms},
            },
            "term_resolution": term_resolution,
        }

    return {
        "handled": True,
        "intent": "step_mapping_lookup",
        "action": "query_step_mapping_lookup",
        "answer": f"matching/rulebook CSV에서 {', '.join(query_terms) or '요청 항목'}에 해당하는 step mapping을 찾지 못했습니다.",
        "feature": "knowledge",
        "source_ids": source_ids,
        "filters": {"product": product_hint, "step_id_terms": step_terms, "query_terms": query_terms, "matching_errors": step_src.get("errors") or [], "rulebook_errors": rulebook.get("errors") or []},
    }


def _handle_knob_rulebook_lookup(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _knob_rulebook_lookup_intent(prompt):
        return {"handled": False}
    product_hint = _product_hint(prompt, product)
    feature_terms = _knob_rulebook_feature_terms(prompt)
    ppids = _ppid_tokens(prompt)
    step_terms = _step_id_terms_from_prompt(prompt, product=product_hint)
    if not step_terms and _flowi_func_step_token(prompt):
        step_terms = [_flowi_func_step_token(prompt)]
    step_maps = _flowi_step_matching_maps(product_hint)
    expanded_functions: list[str] = []
    expanded_step_ids: list[str] = []
    for term in step_terms:
        step_hit = step_maps.get("by_step", {}).get(_upper(term))
        if step_hit:
            func = _text(step_hit.get("function_step"))
            if func and func not in expanded_functions:
                expanded_functions.append(func)
            sid = _text(step_hit.get("step_id"))
            if sid and sid not in expanded_step_ids:
                expanded_step_ids.append(sid)
    rule_src = _flowi_scan_registered_table(purposes=("rulebook", "lookup_table"), name_hints=("ppid_knob.csv", "knob_ppid.csv"))
    lf = rule_src.get("lf")
    if lf is None:
        return {
            "handled": True,
            "intent": "knob_rulebook_lookup",
            "action": "query_knob_rulebook_rows",
            "answer": "KNOB rulebook CSV를 찾지 못했습니다. Agent Wiki에서 rulebook 단일 파일을 등록하거나 data root의 ppid_knob.csv를 확인해주세요.",
            "feature": "knowledge",
            "filters": {"product": product_hint, "feature_terms": feature_terms, "step_terms": step_terms, "ppid": ppids, "errors": rule_src.get("errors") or []},
        }
    cols = rule_src.get("columns") or []
    product_col = _ci_col(cols, "product", "PRODUCT")
    feature_col = _ci_col(cols, "feature_name", "FEATURE_NAME", "feature", "FEATURE", "knob", "KNOB", "step", "STEP")
    func_col = _ci_col(cols, "function_step", "func_step", "FUNCTION_STEP", "FUNC_STEP")
    rule_col = _ci_col(cols, "rule_order", "RULE_ORDER", "order", "ORDER", "priority", "PRIORITY")
    op_col = _ci_col(cols, "operator", "OPERATOR", "op", "OP")
    category_col = _ci_col(cols, "category", "CATEGORY", "ppid", "PPID", "rule_category", "RULE_CATEGORY")
    ppid_col = _ci_col(cols, "ppid", "PPID") or category_col
    if not (feature_col or func_col or ppid_col):
        return {
            "handled": True,
            "intent": "knob_rulebook_lookup",
            "action": "query_knob_rulebook_rows",
            "answer": "KNOB rulebook에서 feature/function_step/ppid 역할 컬럼을 찾지 못했습니다.",
            "feature": "knowledge",
            "table": {"kind": "knob_rulebook_lookup", "title": "KNOB rulebook columns", "placement": "below", "columns": _table_columns(["columns"]), "rows": [{"columns": ", ".join(cols[:80])}], "total": 1},
        }
    aliases = _product_aliases(product_hint)
    filters = []
    if aliases and product_col:
        filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    exact_feature_filter = bool(feature_terms and feature_col)
    if exact_feature_filter and feature_col:
        filters.append(
            pl.col(feature_col)
            .cast(_STR, strict=False)
            .str.strip_chars()
            .str.to_uppercase()
            .is_in([_upper(v) for v in feature_terms])
        )
    if ppids and ppid_col:
        filters.append(pl.col(ppid_col).cast(_STR, strict=False).str.to_uppercase().is_in([_upper(v) for v in ppids]))
    step_search_terms = list(dict.fromkeys([*step_terms, *expanded_functions, *expanded_step_ids]))
    if step_search_terms and not exact_feature_filter:
        expr = None
        search_cols = [c for c in (feature_col, func_col) if c]
        for col in search_cols:
            for term in step_search_terms:
                term_u = _upper(term)
                if not term_u:
                    continue
                piece = pl.col(col).cast(_STR, strict=False).str.to_uppercase().str.contains(term_u, literal=True)
                expr = piece if expr is None else (expr | piece)
        if expr is not None:
            filters.append(expr)
    if not filters and not (feature_terms or ppids or step_terms):
        return {
            "handled": True,
            "intent": "knob_rulebook_lookup",
            "action": "collect_required_fields",
            "answer": "KNOB rulebook 조회에는 step/function_step 또는 PPID가 필요합니다. 예: `24.0 SORT KNOB 룰 매칭`, `PPID_05_1 어떤 knob rule이야?`",
            "feature": "knowledge",
            "missing": ["step_or_ppid"],
        }
    for expr in filters:
        lf = lf.filter(expr)
    exprs = [
        pl.col(product_col).cast(_STR, strict=False).alias("product") if product_col else pl.lit(product_hint).alias("product"),
        pl.col(feature_col).cast(_STR, strict=False).alias("feature_name") if feature_col else pl.lit("").alias("feature_name"),
        pl.col(func_col).cast(_STR, strict=False).alias("function_step") if func_col else pl.lit("").alias("function_step"),
        pl.col(rule_col).cast(_STR, strict=False).alias("rule_order") if rule_col else pl.lit("").alias("rule_order"),
        pl.col(op_col).cast(_STR, strict=False).alias("operator") if op_col else pl.lit("").alias("operator"),
        pl.col(category_col).cast(_STR, strict=False).alias("category") if category_col else pl.lit("").alias("category"),
        pl.col(ppid_col).cast(_STR, strict=False).alias("ppid") if ppid_col else pl.lit("").alias("ppid"),
    ]
    try:
        rows = lf.select(exprs).limit(max(1, min(500, max_rows * 40))).collect().to_dicts()
    except Exception as e:
        logger.warning("flowi knob rulebook lookup failed: %s", e)
        return {"handled": True, "intent": "knob_rulebook_lookup", "action": "query_knob_rulebook_rows", "answer": f"KNOB rulebook 조회 실패: {e}", "feature": "knowledge"}
    func_map = step_maps.get("by_function") or {}
    for row in rows:
        func = _upper(row.get("function_step"))
        prod = _upper(row.get("product"))
        step_ids = list(dict.fromkeys([*(func_map.get((prod, func), []) or []), *(func_map.get(("", func), []) or [])]))
        row["step_ids"] = ", ".join(step_ids[:12])
        row["step_id_expansion_source"] = _text((step_maps.get("source") or {}).get("source_id") or Path(str((step_maps.get("source") or {}).get("path") or "step_matching.csv")).name)
    cols_out = ["product", "feature_name", "function_step", "step_ids", "rule_order", "operator", "category", "ppid"]
    if rows:
        first = rows[0]
        answer = (
            f"KNOB rulebook에서 {len(rows)}개 행을 찾았습니다. "
            f"대표: {first.get('feature_name') or '-'} / {first.get('function_step') or '-'} / {first.get('ppid') or first.get('category') or '-'}."
        )
    else:
        answer = "조건에 맞는 KNOB rulebook 행을 찾지 못했습니다."
    rule_source_id = _text(rule_src.get("source_id") or Path(str(rule_src.get("path") or "ppid_knob.csv")).name)
    step_source_id = _text((step_maps.get("source") or {}).get("source_id") or Path(str((step_maps.get("source") or {}).get("path") or "step_matching.csv")).name)
    return {
        "handled": True,
        "intent": "knob_rulebook_lookup",
        "action": "query_knob_rulebook_rows",
        "answer": answer,
        "feature": "knowledge",
        "source_ids": [sid for sid in (rule_source_id, step_source_id) if sid],
        "table": {
            "kind": "knob_rulebook_lookup",
            "title": "Matched KNOB rulebook rows",
            "placement": "below",
            "columns": _table_columns(cols_out),
            "rows": [{k: r.get(k, "") for k in cols_out} for r in rows],
            "total": len(rows),
            "source": "ppid_knob.csv",
        },
        "filters": {
            "product": product_hint,
            "feature_terms": feature_terms,
            "step_terms": step_terms,
            "expanded_functions": expanded_functions,
            "expanded_step_ids": expanded_step_ids,
            "ppid": ppids,
            "rulebook_file": _path_tail(rule_src.get("path")) if isinstance(rule_src.get("path"), Path) else "",
            "step_matching_file": _path_tail((step_maps.get("source") or {}).get("path")) if isinstance((step_maps.get("source") or {}).get("path"), Path) else "",
            "row_count": len(rows),
            "search_conditions": {
                "product": product_hint or "(all)",
                "feature_name": feature_terms,
                "step_or_function_step_contains": step_search_terms,
                "ppid": ppids,
            },
        },
        "term_resolution": [
            {"token": ", ".join(feature_terms) or "KNOB rulebook", "meaning": "ppid_knob.csv 등록 source 또는 fallback 파일", "wiki_refs": [rule_source_id], "query_filter": f"feature_name={feature_terms or '(not specified)'} source={rule_source_id}", "status": "resolved"},
            {"token": "function_step", "meaning": "step_matching.csv로 step_id 후보 확장", "wiki_refs": [step_source_id], "query_filter": f"expanded={expanded_functions or expanded_step_ids}", "status": "resolved"},
        ],
    }


def _files_matching_prompt_terms(files: list[Path], prompt: str, lots: list[str], product: str = "") -> list[Path]:
    terms = _flowi_report_terms(prompt, lots, product)
    if not terms:
        return files
    filtered = []
    for fp in files:
        hay = _upper(str(fp))
        if any(term in hay for term in terms):
            filtered.append(fp)
    return filtered or files


def _flowi_lot_root_expr(cols: list[str], lots: list[str]):
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    return _or_contains([c for c in (root_col, lot_col, fab_col) if c], lots)


def _is_fab_step_eta_prompt(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    if not _lot_tokens(prompt):
        return False
    if not any(t in low or t in text for t in ("도착", "언제쯤", "언제", "eta", "arrival", "arrive")):
        return False
    return bool(_step_tokens(prompt) or _step_query_terms(prompt, _lot_tokens(prompt)))


def _target_step_ids_from_fab_rows(prompt: str, rows: list[dict[str, Any]], lots: list[str], product: str) -> tuple[list[str], list[dict[str, Any]]]:
    lot_set = {_upper(v) for v in lots}
    exact = [s for s in _step_tokens(prompt) if _upper(s) not in lot_set]
    if exact:
        seen = set()
        out = []
        for sid in exact:
            key = _upper(sid)
            if key not in seen:
                seen.add(key)
                out.append(sid)
        return out, []
    terms = _step_query_terms(prompt, lots, product)
    if not terms:
        return [], []
    candidates: dict[str, dict[str, Any]] = {}
    for row in rows:
        sid = _text(row.get("step_id"))
        if not sid:
            continue
        func = _function_step_label(row.get("product") or product, sid)
        hay = _upper(" ".join([sid, func]))
        if not any(term in hay for term in terms):
            continue
        candidates.setdefault(sid, {"step_id": sid, "function_step": func, "row_count": 0})
        candidates[sid]["row_count"] += 1
    cand_rows = sorted(candidates.values(), key=lambda r: (-int(r.get("row_count") or 0), r.get("step_id") or ""))
    if len(cand_rows) == 1:
        return [cand_rows[0]["step_id"]], cand_rows
    return [], cand_rows


def _handle_fab_step_eta(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _is_fab_step_eta_prompt(prompt):
        return {"handled": False}
    lots = _lot_tokens(prompt)
    product_hint, candidate_tool = _product_or_candidate_tool(prompt, product, lots, kinds=("FAB",), intent="fab_step_eta")
    if candidate_tool:
        return candidate_tool
    files = _fab_files(product_hint)
    if not files:
        return {
            "handled": True,
            "intent": "fab_step_eta",
            "answer": "FAB parquet을 찾지 못했습니다. product 또는 DB root를 확인해주세요.",
            "table": {"kind": "fab_step_eta", "title": "FAB step ETA", "placement": "below", "columns": _table_columns(["message"]), "rows": [{"message": "FAB not found"}], "total": 0},
        }
    lf = _scan_parquet(files)
    cols = _schema_names(lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    step_col = _ci_col(cols, "step_id", "STEP_ID")
    time_col = _ci_col(cols, "tkout_time", "TKOUT_TIME", "time", "TIME", "timestamp", "TIMESTAMP", "move_time", "MOVE_TIME", "updated_at", "UPDATED_AT")
    if not step_col or not (root_col or lot_col or fab_col):
        return {"handled": True, "intent": "fab_step_eta", "answer": "FAB 데이터에서 root/lot 또는 step_id 컬럼을 찾지 못했습니다."}
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
        pl.col(time_col).cast(_STR, strict=False).alias("time") if time_col else pl.lit("").alias("time"),
    ]
    try:
        df = lf.select(exprs).drop_nulls(subset=["step_id"]).limit(120000).collect()
    except Exception as e:
        return {"handled": True, "intent": "fab_step_eta", "answer": f"FAB ETA 조회 실패: {e}"}
    rows_all = df.to_dicts()
    target_steps, candidates = _target_step_ids_from_fab_rows(prompt, rows_all, lots, product_hint)
    if not target_steps:
        if candidates:
            return {
                "handled": True,
                "intent": "fab_step_eta",
                "action": "clarify_target_step",
                "answer": "도착 ETA를 계산할 target step이 여러 후보로 매칭됐습니다. step_id를 하나 선택해주세요.",
                "clarification": {
                    "question": "어느 step 도착 기준으로 볼까요?",
                    "choices": [
                        {
                            "id": f"step_{i}",
                            "label": str(i + 1),
                            "title": f"{row.get('step_id')} {row.get('function_step') or ''}".strip(),
                            "recommended": i == 0,
                            "description": f"FAB row {row.get('row_count')}건",
                            "prompt": f"{prompt.strip()} {row.get('step_id')}",
                        }
                        for i, row in enumerate(candidates[:4])
                    ],
                },
                "table": {"kind": "fab_step_candidates", "title": "FAB target step candidates", "placement": "below", "columns": _table_columns(["step_id", "function_step", "row_count"]), "rows": candidates[:max(1, max_rows)], "total": len(candidates)},
            }
        return {"handled": True, "intent": "fab_step_eta", "answer": "도착 기준 step_id를 찾지 못했습니다. 예: `A0001 AA230400에 언제쯤 도착해?`"}
    target_step = target_steps[0]
    wafers = _wafer_tokens(prompt)
    lot_expr_values = {_upper(v) for v in lots}
    def lot_hit(row: dict[str, Any]) -> bool:
        hay = _upper(" ".join([row.get("root_lot_id") or "", row.get("lot_id") or "", row.get("fab_lot_id") or ""]))
        return any(tok and tok in hay for tok in lot_expr_values)
    def wafer_hit(row: dict[str, Any]) -> bool:
        if not wafers:
            return True
        vals = set()
        for wf in wafers:
            vals.add(wf)
            try:
                vals.add(str(int(wf)))
                vals.add(f"{int(wf):02d}")
            except Exception:
                pass
        return _text(row.get("wafer_id")) in vals
    lot_rows = [row for row in rows_all if lot_hit(row) and wafer_hit(row)]
    if not lot_rows:
        return {"handled": True, "intent": "fab_step_eta", "answer": f"{', '.join(lots)}에 해당하는 FAB row를 찾지 못했습니다."}
    def row_sort_key(row: dict[str, Any]):
        dt = _parse_flowi_datetime(row.get("time"))
        return (dt or datetime.min, _step_rank_key(row.get("step_id")))
    lot_rows.sort(key=row_sort_key, reverse=True)
    current = lot_rows[0]
    current_step = _text(current.get("step_id"))
    current_time = _parse_flowi_datetime(current.get("time"))
    reached_rows = [row for row in lot_rows if _upper(row.get("step_id")) == _upper(target_step)]
    target_func = _function_step_label(current.get("product") or product_hint, target_step)
    current_func = _function_step_label(current.get("product") or product_hint, current_step)
    if reached_rows:
        reached_rows.sort(key=row_sort_key)
        first_reached = reached_rows[0]
        latest_reached = reached_rows[-1]
        row = {
            "product": current.get("product") or product_hint,
            "root_lot_id": current.get("root_lot_id") or lots[0],
            "current_step_id": current_step,
            "current_function_step": current_func,
            "target_step_id": target_step,
            "target_function_step": target_func,
            "status": "already_reached",
            "current_time": current.get("time") or "",
            "first_target_time": first_reached.get("time") or "",
            "latest_target_time": latest_reached.get("time") or "",
            "eta_median_hours": 0,
            "eta_p80_hours": 0,
            "eta_at_median": latest_reached.get("time") or "",
            "eta_at_p80": latest_reached.get("time") or "",
            "sample_lots": 0,
            "confidence": "actual",
        }
        cols_out = ["product", "root_lot_id", "current_step_id", "current_function_step", "target_step_id", "target_function_step", "status", "current_time", "first_target_time", "latest_target_time", "eta_median_hours", "eta_p80_hours", "eta_at_median", "eta_at_p80", "sample_lots", "confidence"]
        return {
            "handled": True,
            "intent": "fab_step_eta",
            "action": "query_fab_step_eta",
            "answer": f"{row['root_lot_id']}는 이미 {target_step}{('(' + target_func + ')') if target_func else ''}에 도착했습니다. 최초 도착: {row['first_target_time'] or '-'}, 최신 기록: {row['latest_target_time'] or '-'}.",
            "table": {"kind": "fab_step_eta", "title": "FAB step ETA", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: row.get(k, "") for k in cols_out}], "total": 1},
            "filters": {"product": product_hint, "lots": lots, "wafers": wafers, "target_step": target_step},
        }
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows_all:
        root = _text(row.get("root_lot_id"))
        if root:
            grouped.setdefault(root, []).append(row)
    sample_rows: list[dict[str, Any]] = []
    durations: list[float] = []
    current_key_roots = {_text(r.get("root_lot_id")) for r in lot_rows if _text(r.get("root_lot_id"))}
    for root, root_rows in grouped.items():
        if root in current_key_roots:
            continue
        starts = [r for r in root_rows if _upper(r.get("step_id")) == _upper(current_step) and _parse_flowi_datetime(r.get("time"))]
        targets = [r for r in root_rows if _upper(r.get("step_id")) == _upper(target_step) and _parse_flowi_datetime(r.get("time"))]
        if not starts or not targets:
            continue
        starts.sort(key=lambda r: _parse_flowi_datetime(r.get("time")) or datetime.min)
        targets.sort(key=lambda r: _parse_flowi_datetime(r.get("time")) or datetime.min)
        best = None
        for start in reversed(starts):
            start_dt = _parse_flowi_datetime(start.get("time"))
            target_after = next((t for t in targets if (_parse_flowi_datetime(t.get("time")) or datetime.min) >= start_dt), None)
            if target_after:
                best = (start, target_after)
                break
        if not best:
            continue
        hours = _flowi_hours_between(best[0].get("time"), best[1].get("time"))
        if hours is None or hours < 0:
            continue
        durations.append(hours)
        sample_rows.append({
            "root_lot_id": root,
            "from_time": best[0].get("time") or "",
            "target_time": best[1].get("time") or "",
            "duration_hours": hours,
        })
    median_h = _flowi_percentile(durations, 0.5)
    p80_h = _flowi_percentile(durations, 0.8)
    eta_median = current_time + timedelta(hours=median_h) if current_time and median_h is not None else None
    eta_p80 = current_time + timedelta(hours=p80_h) if current_time and p80_h is not None else None
    confidence = "high" if len(durations) >= 5 else ("medium" if len(durations) >= 2 else ("low" if durations else "no_sample"))
    row = {
        "product": current.get("product") or product_hint,
        "root_lot_id": current.get("root_lot_id") or lots[0],
        "current_step_id": current_step,
        "current_function_step": current_func,
        "target_step_id": target_step,
        "target_function_step": target_func,
        "status": "estimated" if durations else "no_historical_sample",
        "current_time": current.get("time") or "",
        "eta_median_hours": median_h if median_h is not None else "",
        "eta_p80_hours": p80_h if p80_h is not None else "",
        "eta_at_median": _fmt_flowi_datetime(eta_median),
        "eta_at_p80": _fmt_flowi_datetime(eta_p80),
        "sample_lots": len(durations),
        "confidence": confidence,
    }
    cols_out = ["product", "root_lot_id", "current_step_id", "current_function_step", "target_step_id", "target_function_step", "status", "current_time", "eta_median_hours", "eta_p80_hours", "eta_at_median", "eta_at_p80", "sample_lots", "confidence"]
    if durations:
        answer = (
            f"{row['root_lot_id']} 현재 위치는 {current_step}{('(' + current_func + ')') if current_func else ''}이고, "
            f"{target_step}{('(' + target_func + ')') if target_func else ''} 도착 예상은 median 기준 {row['eta_at_median'] or '-'} "
            f"(p80 {row['eta_at_p80'] or '-'})입니다. 과거 sample lot {len(durations)}개 기준입니다."
        )
    else:
        answer = (
            f"{row['root_lot_id']} 현재 위치는 {current_step}{('(' + current_func + ')') if current_func else ''}입니다. "
            f"{target_step}{('(' + target_func + ')') if target_func else ''}까지의 과거 duration sample을 찾지 못해 ETA는 계산하지 않았습니다."
        )
    return {
        "handled": True,
        "intent": "fab_step_eta",
        "action": "query_fab_step_eta",
        "answer": answer,
        "table": {"kind": "fab_step_eta", "title": "FAB step ETA", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: row.get(k, "") for k in cols_out}], "total": 1},
        "samples_table": {"kind": "fab_step_eta_samples", "title": "Historical FAB step durations", "placement": "below", "columns": _table_columns(["root_lot_id", "from_time", "target_time", "duration_hours"]), "rows": sample_rows[:max(1, max_rows)], "total": len(sample_rows)},
        "filters": {"product": product_hint, "lots": lots, "wafers": wafers, "target_step": target_step},
    }


def _is_et_report_freshness_prompt(prompt: str) -> bool:
    up = _upper(prompt)
    text = str(prompt or "")
    low = text.lower()
    if "ET" not in up or "REPORT" not in up:
        return False
    return any(t in low or t in text for t in ("최근업데이트", "최근 업데이트", "업데이트", "안올라", "안 올라", "latest", "fresh", "updated"))


def _is_et_report_lookup_prompt(prompt: str) -> bool:
    up = _upper(prompt)
    if "ET" not in up or "REPORT" not in up:
        return False
    return not _is_et_report_freshness_prompt(prompt)


def _et_product_or_candidate(prompt: str, product: str, lots: list[str], intent: str) -> tuple[str, dict[str, Any] | None]:
    product_hint = _product_hint(prompt, product)
    if product_hint:
        return product_hint, None
    if lots:
        product_hint, candidate_tool = _product_or_candidate_tool(prompt, product, lots, kinds=("ET", "FAB"), intent=intent)
        if product_hint or candidate_tool:
            return product_hint, candidate_tool
    return "", None


def _handle_et_report_freshness(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _is_et_report_freshness_prompt(prompt):
        return {"handled": False}
    return {
        "handled": True,
        "intent": "et_report_freshness_lookup",
        "answer": "ET Report 기능은 archive/agent_reset_2026_05_26 으로 이동되어 새로 설계할 예정입니다.",
    }


def _handle_et_report_lookup(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _is_et_report_lookup_prompt(prompt):
        return {"handled": False}
    return {
        "handled": True,
        "intent": "et_report_lookup",
        "answer": "ET Report 기능은 archive/agent_reset_2026_05_26 으로 이동되어 새로 설계할 예정입니다.",
    }


def _is_measurement_duration_prompt(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    return bool(_lot_tokens(prompt)) and any(t in low or t in text for t in ("측정시간", "측정 시간", "얼마나 걸", "duration", "measure time", "measurement time"))


def _handle_measurement_duration_lookup(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _is_measurement_duration_prompt(prompt):
        return {"handled": False}
    lots = _lot_tokens(prompt)
    product_hint, candidate_tool = _et_product_or_candidate(prompt, product, lots, "measurement_duration_lookup")
    if candidate_tool:
        return candidate_tool
    files = _files_matching_prompt_terms(_et_files(product_hint), prompt, lots, product_hint)
    if not files:
        return {"handled": True, "intent": "measurement_duration_lookup", "answer": "측정시간을 계산할 ET parquet을 찾지 못했습니다."}
    lf = _scan_parquet(files)
    cols = _schema_names(lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    step_col = _ci_col(cols, "step_id", "STEP_ID")
    item_col = _ci_col(cols, "item_id", "ITEM_ID")
    start_col = _ci_col(cols, "tkin_time", "TKIN_TIME", "start_time", "START_TIME", "measure_start_time", "MEASURE_START_TIME", "measurement_start_time", "MEASUREMENT_START_TIME")
    end_col = _ci_col(cols, "tkout_time", "TKOUT_TIME", "end_time", "END_TIME", "measure_end_time", "MEASURE_END_TIME", "measurement_end_time", "MEASUREMENT_END_TIME")
    span_col = _ci_col(cols, "time", "TIME", "timestamp", "TIMESTAMP", "measure_time", "MEASURE_TIME")
    aliases = _product_aliases(product_hint)
    filters = []
    if aliases and product_col:
        filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    lot_expr = _flowi_lot_root_expr(cols, lots)
    if lot_expr is not None:
        filters.append(lot_expr)
    wafers = _wafer_tokens(prompt)
    wf_expr = _wafer_match_expr(wafer_col, wafers)
    if wf_expr is not None:
        filters.append(wf_expr)
    terms = _flowi_report_terms(prompt, lots, product_hint)
    item_matches = _match_values(_unique_strings(lf, item_col, limit=500), terms) if item_col else []
    step_matches = _match_values(_unique_strings(lf, step_col, limit=500), terms) if step_col else []
    if item_matches:
        filters.append(pl.col(item_col).cast(_STR, strict=False).is_in(item_matches))
    elif step_matches:
        filters.append(pl.col(step_col).cast(_STR, strict=False).is_in(step_matches))
    for expr in filters:
        lf = lf.filter(expr)
    start_src = start_col or span_col or end_col
    end_src = end_col or span_col or start_col
    if not (start_src and end_src):
        return {"handled": True, "intent": "measurement_duration_lookup", "answer": "측정 시작/종료/time 컬럼을 찾지 못했습니다."}
    exprs = [
        pl.col(product_col).cast(_STR, strict=False).alias("product") if product_col else pl.lit(_core_product_name(product_hint)).alias("product"),
        pl.col(root_col).cast(_STR, strict=False).alias("root_lot_id") if root_col else (
            pl.col(lot_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id") if lot_col else (pl.col(fab_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id") if fab_col else pl.lit("").alias("root_lot_id"))
        ),
        pl.col(step_col).cast(_STR, strict=False).alias("step_id") if step_col else pl.lit("").alias("step_id"),
        pl.col(item_col).cast(_STR, strict=False).alias("item_id") if item_col else pl.lit("").alias("item_id"),
        pl.col(wafer_col).cast(_STR, strict=False).alias("wafer_id") if wafer_col else pl.lit("").alias("wafer_id"),
        pl.col(start_src).cast(_STR, strict=False).alias("start_time"),
        pl.col(end_src).cast(_STR, strict=False).alias("end_time"),
    ]
    try:
        scoped = lf.select(exprs)
        group_cols = ["product", "root_lot_id", "step_id", "item_id", "wafer_id"]
        df = (
            scoped.group_by(group_cols)
            .agg([
                pl.col("start_time").min().alias("start_time"),
                pl.col("end_time").max().alias("end_time"),
                pl.len().alias("row_count"),
            ])
            .sort("end_time", descending=True)
            .limit(max(1, min(200, max_rows * 10)))
            .collect()
        )
    except Exception as e:
        return {"handled": True, "intent": "measurement_duration_lookup", "answer": f"측정시간 계산 실패: {e}"}
    rows = df.to_dicts()
    basis = "start_end_columns" if start_col and end_col else "span_of_time_column"
    for row in rows:
        row["function_step"] = _function_step_label(row.get("product") or product_hint, row.get("step_id"))
        hours = _flowi_hours_between(row.get("start_time"), row.get("end_time"))
        row["duration_min"] = round(hours * 60.0, 2) if hours is not None else ""
        row["duration_basis"] = basis
        row["row_count"] = int(row.get("row_count") or 0)
    cols_out = ["product", "root_lot_id", "step_id", "function_step", "item_id", "wafer_id", "start_time", "end_time", "duration_min", "duration_basis", "row_count"]
    answer = f"측정시간을 {len(rows)}개 그룹으로 계산했습니다."
    if rows:
        answer += f" 대표 duration: {rows[0].get('duration_min') or '-'}분."
    else:
        answer = "조건에 맞는 측정시간 row를 찾지 못했습니다."
    return {
        "handled": True,
        "intent": "measurement_duration_lookup",
        "action": "query_measurement_duration",
        "answer": answer,
        "table": {"kind": "measurement_duration", "title": "Measurement duration", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in rows[:max(1, min(120, max_rows * 8))]], "total": len(rows)},
        "filters": {"product": product_hint, "lots": lots, "wafers": wafers, "terms": terms, "item_matches": item_matches, "step_matches": step_matches},
    }


def _is_inline_item_lookup_prompt(prompt: str) -> bool:
    text = str(prompt or "")
    up = _upper(text)
    if "INLINE" not in up and "인라인" not in text:
        return False
    if "ITEM" not in up and "아이템" not in text and "항목" not in text:
        return False
    discovery_terms = (
        "비슷", "유사", "후보", "모르", "찾아", "검색", "unique", "유니크",
        "최근", "며칠", "어떤 item", "무슨 item", "which item",
    )
    return bool(_step_id_terms_from_prompt(prompt)) or any(term in text.lower() for term in discovery_terms)


def _inline_recent_days(prompt: str, default: int = 5) -> int:
    text = str(prompt or "")
    match = re.search(r"최근\s*(\d{1,2})\s*일|(?:last|recent)\s*(\d{1,2})\s*days?", text, flags=re.I)
    raw = next((part for part in (match.groups() if match else ()) if part), "")
    try:
        return max(1, min(30, int(raw)))
    except Exception:
        return default


def _inline_partition_date(path: Path):
    for part in path.parts:
        match = re.search(r"date=(\d{4}-?\d{2}-?\d{2})", part, flags=re.I)
        if not match:
            continue
        try:
            return datetime.strptime(match.group(1).replace("-", ""), "%Y%m%d").date()
        except ValueError:
            return None
    return None


def _inline_candidate_tokens(prompt: str, product: str, step_terms: list[str]) -> list[str]:
    stop = {
        "INLINE", "ITEM", "ITEMID", "ID", "인라인", "아이템", "항목", "최근", "며칠",
        "비슷한", "비슷", "유사한", "유사", "후보", "검색", "찾아줘", "찾아", "알려줘",
        "UNIQUE", "LAST", "RECENT", "DAYS", "DAY", "모르겠어", "모르겠는데", "어떤", "무슨",
    }
    stop.update(_upper(part) for part in _product_aliases(product))
    stop.update(_upper(part) for part in step_terms)
    out: list[str] = []
    for token in _query_tokens(prompt):
        key = _upper(token).replace("_", "")
        if len(key) < 2 or key in stop or key.isdigit():
            continue
        if key not in out:
            out.append(key)
    return out[:12]


def _inline_candidate_score(item_id: str, tokens: list[str], row_count: int = 0) -> float:
    item = _upper(item_id)
    compact = item.replace("_", "").replace("-", "")
    best = 0.0
    for token in tokens:
        tok = _upper(token).replace("_", "").replace("-", "")
        if not tok:
            continue
        score = SequenceMatcher(None, tok, compact).ratio()
        if tok == compact:
            score = 1.0
        elif tok in compact or compact in tok:
            # A short plant alias (for example CDW) is often a shared prefix.
            # Treat all containing item IDs equally, then use observed frequency
            # as the deterministic tie-break instead of preferring shorter IDs.
            score = max(score, 0.9)
        best = max(best, score)
    frequency_tiebreaker = min(max(int(row_count or 0), 0), 100_000) / 10_000_000
    return round(best + frequency_tiebreaker, 6)


def _inline_learning_term(tokens: list[str], rows: list[dict[str, Any]]) -> str:
    if not tokens:
        return ""
    top_items = [str(row.get("item_id") or "") for row in rows[:5]]
    return max(
        tokens,
        key=lambda token: max((_inline_candidate_score(item, [token]) for item in top_items), default=0.0),
    )


def _handle_inline_item_lookup(
    prompt: str,
    product: str,
    max_rows: int,
    username: str = "flowi",
) -> dict[str, Any]:
    if not _is_inline_item_lookup_prompt(prompt):
        return {"handled": False}
    product_hint = _product_hint(prompt, product)
    step_terms = _step_id_terms_from_prompt(prompt, product=product_hint)
    # Duration phrases such as "last 3 days" can look like fab step tokens to
    # the broad domain parser (for example "3.0 DAYS"). They are time windows,
    # not step_id filters.
    step_terms = [
        term for term in step_terms
        if not re.fullmatch(r"\s*\d+(?:\.0+)?\s*(?:DAYS?|일)\s*", str(term), flags=re.I)
    ]
    discovery = not step_terms or any(
        term in str(prompt or "").lower()
        for term in ("비슷", "유사", "후보", "모르", "unique", "유니크", "최근", "며칠")
    )
    recent_days = _inline_recent_days(prompt)
    files = _inline_files(product_hint)
    window_fallback = False
    latest_partition = ""
    if discovery:
        files = prune_recent_partitions(files, days=recent_days, max_files=48)
        partition_dates = [date for date in (_inline_partition_date(path) for path in files) if date is not None]
        if partition_dates:
            latest_date = max(partition_dates)
            latest_partition = latest_date.isoformat()
            window_fallback = latest_date < (datetime.now().date() - timedelta(days=recent_days))
    if not files:
        return {
            "handled": True,
            "intent": "inline_item_by_step_lookup",
            "answer": "INLINE parquet을 찾지 못했습니다. product 또는 DB root를 확인해주세요.",
            "table": {"kind": "inline_item_by_step", "title": "INLINE items by step", "placement": "below", "columns": _table_columns(["message"]), "rows": [{"message": "INLINE not found"}], "total": 0},
        }
    lf = _scan_parquet(files)
    cols = _schema_names(lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    step_col = _ci_col(cols, "step_id", "STEP_ID")
    item_col = _ci_col(cols, "item_id", "ITEM_ID", "inline_item", "INLINE_ITEM")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    time_col = _ci_col(cols, "time", "TIME", "timestamp", "TIMESTAMP", "measure_time", "MEASURE_TIME", "tkout_time", "TKOUT_TIME", "updated_at", "UPDATED_AT")
    value_col = _ci_col(cols, "value", "VALUE")
    if not step_col or not item_col:
        return {
            "handled": True,
            "intent": "inline_item_by_step_lookup",
            "answer": "INLINE 데이터에서 step_id 또는 item_id 컬럼을 찾지 못했습니다.",
            "table": {"kind": "inline_item_by_step", "title": "INLINE items by step", "placement": "below", "columns": _table_columns(["message", "columns"]), "rows": [{"message": "missing step_id/item_id", "columns": ", ".join(cols[:50])}], "total": 1},
        }
    aliases = _product_aliases(product_hint)
    filters = []
    if aliases and product_col:
        filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    step_matches = _match_values(_unique_strings(lf, step_col, limit=800), step_terms)
    if step_matches:
        filters.append(pl.col(step_col).cast(_STR, strict=False).is_in(step_matches))
    elif step_terms:
        expr = None
        for term in step_terms:
            piece = pl.col(step_col).cast(_STR, strict=False).str.to_uppercase().str.contains(_upper(term), literal=True)
            expr = piece if expr is None else (expr | piece)
        if expr is not None:
            filters.append(expr)
    for expr in filters:
        lf = lf.filter(expr)
    exprs = [
        pl.col(product_col).cast(_STR, strict=False).alias("product") if product_col else pl.lit(_core_product_name(product_hint)).alias("product"),
        pl.col(step_col).cast(_STR, strict=False).alias("step_id"),
        pl.col(item_col).cast(_STR, strict=False).alias("item_id"),
        pl.col(root_col).cast(_STR, strict=False).alias("root_lot_id") if root_col else pl.lit("").alias("root_lot_id"),
        pl.col(wafer_col).cast(_STR, strict=False).alias("wafer_id") if wafer_col else pl.lit("").alias("wafer_id"),
        pl.col(time_col).cast(_STR, strict=False).alias("latest_time") if time_col else pl.lit("").alias("latest_time"),
    ]
    if value_col:
        exprs.append(pl.col(value_col).cast(pl.Float64, strict=False).alias("value"))
    try:
        scoped = lf.select(exprs).drop_nulls(subset=["step_id", "item_id"])
        aggs = [
            pl.len().alias("row_count"),
            pl.col("root_lot_id").n_unique().alias("root_count"),
            pl.col("wafer_id").n_unique().alias("wafer_count"),
            pl.col("latest_time").max().alias("latest_time"),
        ]
        if value_col:
            aggs.append(pl.col("value").median().alias("median"))
        df = (
            scoped.group_by(["product", "step_id", "item_id"])
            .agg(aggs)
            .sort(["row_count", "latest_time"], descending=[True, True])
            .limit(max(1, min(120, max_rows * 8)))
            .collect()
        )
    except Exception as e:
        return {"handled": True, "intent": "inline_item_by_step_lookup", "answer": f"INLINE item 조회 실패: {e}"}
    rows = df.to_dicts()
    for row in rows:
        row["function_step"] = _function_step_label(row.get("product") or product_hint, row.get("step_id"))
        row["median"] = _round4(row.get("median"))
        row["row_count"] = int(row.get("row_count") or 0)
        row["root_count"] = int(row.get("root_count") or 0)
        row["wafer_count"] = int(row.get("wafer_count") or 0)
    candidate_tokens = _inline_candidate_tokens(prompt, product_hint, step_terms)
    learned = semantic_hitl.find_resolution(
        prompt,
        username=username,
        source_type="INLINE",
        product=product_hint,
        step_id=(step_matches[0] if step_matches else (step_terms[0] if step_terms else "")),
    )
    if discovery:
        for row in rows:
            row["candidate_score"] = _inline_candidate_score(
                str(row.get("item_id") or ""), candidate_tokens, int(row.get("row_count") or 0)
            )
        if learned and learned.get("item_id"):
            wanted = _upper(learned.get("item_id"))
            selected = [row for row in rows if _upper(row.get("item_id")) == wanted]
            if selected:
                rows = selected + [row for row in rows if _upper(row.get("item_id")) != wanted]
        else:
            rows.sort(key=lambda row: (float(row.get("candidate_score") or 0.0), int(row.get("row_count") or 0)), reverse=True)
    cols_out = ["product", "step_id", "function_step", "item_id", "row_count", "root_count", "wafer_count", "median", "latest_time"]
    if discovery:
        cols_out.insert(4, "candidate_score")
    if rows:
        answer = f"{', '.join(step_terms)} 기준 INLINE item 후보 {len(rows)}개를 찾았습니다. 대표 item: {rows[0].get('item_id') or '-'}."
    else:
        answer = f"{', '.join(step_terms)} 기준 INLINE item을 찾지 못했습니다."
    result = {
        "handled": True,
        "intent": "inline_item_by_step_lookup",
        "action": "query_inline_items_by_step",
        "answer": answer,
        "table": {"kind": "inline_item_by_step", "title": "INLINE items by step", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in rows], "total": len(rows)},
        "filters": {
            "product": product_hint,
            "step_terms": step_terms,
            "step_matches": step_matches,
            "recent_days": recent_days if discovery else None,
            "files_scanned": len(files),
            "latest_partition": latest_partition or None,
            "window_fallback": window_fallback,
        },
    }
    if window_fallback:
        result.setdefault("warnings", []).append(
            f"요청한 최근 {recent_days}일 파티션이 없어 최신 가용 파티션 {latest_partition}을 fallback 조회했습니다."
        )
    if discovery and rows:
        learning_term = str(learned.get("term") or "") if learned else _inline_learning_term(candidate_tokens, rows)
        if learned:
            selected_id = str(learned.get("item_id") or "")
            result["answer"] = (
                f"이전에 확인한 '{learned.get('term')}' → INLINE item_id '{selected_id}' 매핑을 재사용했습니다. "
                + (
                    f"최근 {recent_days}일 데이터가 없어 최신 가용 파티션 {latest_partition}의 근거를 표시합니다."
                    if window_fallback
                    else f"최근 {recent_days}일 범위의 근거를 함께 표시합니다."
                )
            )
            result["semantic_learning"] = {
                "reused": True,
                "scope": learned.get("scope") or "shared",
                "term": learned.get("term"),
                "item_id": selected_id,
                "step_id": learned.get("step_id") or "",
                "shared_votes": learned.get("shared_votes") or 1,
                "shared_conflict_count": learned.get("shared_conflict_count") or 0,
            }
        elif learning_term:
            choices: list[dict[str, Any]] = []
            seen_items: set[str] = set()
            for row in rows:
                item_id = str(row.get("item_id") or "")
                if not item_id or _upper(item_id) in seen_items:
                    continue
                seen_items.add(_upper(item_id))
                marker = semantic_hitl.encode_choice({
                    "term": learning_term,
                    "source_type": "INLINE",
                    "product": product_hint,
                    "item_id": item_id,
                    "step_id": str(row.get("step_id") or ""),
                    "original_prompt": prompt,
                    "evidence": {
                        "recent_days": recent_days,
                        "row_count": int(row.get("row_count") or 0),
                        "candidate_score": row.get("candidate_score"),
                    },
                })
                choices.append({
                    "id": f"inline_item_{len(choices) + 1}",
                    "label": str(len(choices) + 1),
                    "title": item_id,
                    "value": item_id,
                    "recommended": len(choices) == 0,
                    "description": (
                        f"step={row.get('step_id') or '-'}, 최근 row={row.get('row_count') or 0}, "
                        f"유사도={row.get('candidate_score') or 0}"
                    ),
                    "prompt": marker,
                })
                if len(choices) >= 3:
                    break
            result.update({
                "answer": (
                    f"'{learning_term}'와 비슷한 INLINE item_id 후보를 "
                    + (
                        f"최근 {recent_days}일 데이터가 없어 최신 가용 파티션 {latest_partition}에서 찾았습니다. "
                        if window_fallback
                        else f"최근 {recent_days}일 데이터에서 찾았습니다. "
                    )
                    + "맞는 항목을 선택하면 공유 매핑으로 기억해 다른 사용자 질문에도 적용합니다."
                ),
                "needs_input": True,
                "missing": ["inline_item_id_confirmation"],
                "clarification": {
                    "question": f"'{learning_term}'가 의미하는 INLINE item_id는 어느 것인가요?",
                    "choices": choices,
                },
                "semantic_learning": {
                    "reused": False,
                    "scope": "shared",
                    "term": learning_term,
                    "candidate_count": len(rows),
                },
            })
    return result


def _is_ppid_knob_lookup_prompt(prompt: str) -> bool:
    text = str(prompt or "")
    up = _upper(text)
    return ("KNOB" in up or "노브" in text) and "PPID" in up and bool(_step_id_terms_from_prompt(prompt) or _ppid_tokens(prompt))


def _handle_ppid_knob_lookup(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _is_ppid_knob_lookup_prompt(prompt):
        return {"handled": False}
    product_hint = _product_hint(prompt, product)
    step_terms = _step_id_terms_from_prompt(prompt, product=product_hint)
    ppids = _ppid_tokens(prompt)
    if not ppids:
        return {
            "handled": True,
            "intent": "ppid_knob_lookup",
            "action": "clarify_ppid",
            "answer": "PPID 값이 필요합니다. 예: `PRODA step_id AA230400 ppid PPID_STI 이거 무슨 knob이야?`",
            "clarification": {
                "question": "어떤 PPID 기준으로 KNOB를 볼까요?",
                "choices": [],
            },
        }
    fab_rows: list[dict[str, Any]] = []
    fab_files = _fab_files(product_hint)
    if fab_files:
        try:
            lf = _scan_parquet(fab_files)
            cols = _schema_names(lf)
            product_col = _ci_col(cols, "product", "PRODUCT")
            root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
            lot_col = _ci_col(cols, "lot_id", "LOT_ID")
            fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
            wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
            step_col = _ci_col(cols, "step_id", "STEP_ID")
            ppid_col = _ci_col(cols, "ppid", "PPID")
            time_col = _ci_col(cols, "tkout_time", "TKOUT_TIME", "time", "TIME", "timestamp", "TIMESTAMP")
            filters = []
            aliases = _product_aliases(product_hint)
            if aliases and product_col:
                filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
            if ppid_col:
                filters.append(pl.col(ppid_col).cast(_STR, strict=False).str.to_uppercase().is_in([_upper(v) for v in ppids]))
            if step_terms and step_col:
                step_matches = _match_values(_unique_strings(lf, step_col, limit=800), step_terms)
                if step_matches:
                    filters.append(pl.col(step_col).cast(_STR, strict=False).is_in(step_matches))
                else:
                    expr = None
                    for term in step_terms:
                        piece = pl.col(step_col).cast(_STR, strict=False).str.to_uppercase().str.contains(_upper(term), literal=True)
                        expr = piece if expr is None else (expr | piece)
                    if expr is not None:
                        filters.append(expr)
            for expr in filters:
                lf = lf.filter(expr)
            exprs = [
                pl.col(product_col).cast(_STR, strict=False).alias("product") if product_col else pl.lit(_core_product_name(product_hint)).alias("product"),
                pl.col(root_col).cast(_STR, strict=False).alias("root_lot_id") if root_col else (
                    pl.col(lot_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id") if lot_col else (pl.col(fab_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id") if fab_col else pl.lit("").alias("root_lot_id"))
                ),
                pl.col(wafer_col).cast(_STR, strict=False).alias("wafer_id") if wafer_col else pl.lit("").alias("wafer_id"),
                pl.col(step_col).cast(_STR, strict=False).alias("step_id") if step_col else pl.lit("").alias("step_id"),
                pl.col(ppid_col).cast(_STR, strict=False).alias("ppid") if ppid_col else pl.lit("").alias("ppid"),
                pl.col(time_col).cast(_STR, strict=False).alias("time") if time_col else pl.lit("").alias("time"),
            ]
            fab_rows = lf.select(exprs).limit(5000).collect().to_dicts()
        except Exception as e:
            logger.warning("flowi ppid knob FAB scan failed: %s", e)
            fab_rows = []
    roots = sorted({_text(r.get("root_lot_id")) for r in fab_rows if _text(r.get("root_lot_id"))})
    product_from_fab = next((_text(r.get("product")) for r in fab_rows if _text(r.get("product"))), "")
    ml_product = product_hint or product_from_fab
    ml_files = _ml_files(ml_product)
    if not ml_files:
        return {"handled": True, "intent": "ppid_knob_lookup", "answer": "KNOB를 확인할 ML_TABLE parquet을 찾지 못했습니다.", "filters": {"product": ml_product, "step_terms": step_terms, "ppid": ppids}}
    lf = _scan_parquet(ml_files)
    cols = _schema_names(lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    ppid_col = _ci_col(cols, "ppid", "PPID")
    step_col = _ci_col(cols, "step_id", "STEP_ID")
    knob_cols = [c for c in cols if _upper(c).startswith("KNOB_")]
    filters = []
    aliases = _product_aliases(ml_product)
    if aliases and product_col:
        filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    if roots and root_col:
        filters.append(pl.col(root_col).cast(_STR, strict=False).is_in(roots))
    elif ppid_col:
        filters.append(pl.col(ppid_col).cast(_STR, strict=False).str.to_uppercase().is_in([_upper(v) for v in ppids]))
    if step_terms and step_col:
        step_matches = _match_values(_unique_strings(lf, step_col, limit=800), step_terms)
        if step_matches:
            filters.append(pl.col(step_col).cast(_STR, strict=False).is_in(step_matches))
    for expr in filters:
        lf = lf.filter(expr)
    if not knob_cols:
        return {"handled": True, "intent": "ppid_knob_lookup", "answer": "ML_TABLE에서 KNOB_* 컬럼을 찾지 못했습니다."}
    keep = [c for c in (product_col, root_col, wafer_col, ppid_col, step_col) if c] + knob_cols[:120]
    try:
        df = lf.select([pl.col(c).cast(_STR, strict=False).alias(c) for c in keep]).limit(10000).collect()
    except Exception as e:
        return {"handled": True, "intent": "ppid_knob_lookup", "answer": f"PPID→KNOB 조회 실패: {e}"}
    raw_rows = df.to_dicts()
    rows = []
    for knob in knob_cols:
        values = {}
        lot_set = set()
        wafer_set = set()
        for row in raw_rows:
            val = _text(row.get(knob))
            if not val or val.lower() in {"none", "null", "nan"}:
                continue
            values[val] = values.get(val, 0) + 1
            if root_col and _text(row.get(root_col)):
                lot_set.add(_text(row.get(root_col)))
            if wafer_col and _text(row.get(wafer_col)):
                wafer_set.add(_text(row.get(wafer_col)))
        for val, count in sorted(values.items(), key=lambda kv: (-kv[1], kv[0]))[:6]:
            rows.append({
                "product": ml_product or product_from_fab,
                "step_id": ", ".join(step_terms),
                "ppid": ", ".join(ppids),
                "knob": knob,
                "knob_value": val,
                "row_count": count,
                "root_count": len(lot_set),
                "wafer_count": len(wafer_set),
                "example_lots": ", ".join(sorted(lot_set)[:8]),
            })
    rows.sort(key=lambda r: (-int(r.get("row_count") or 0), r.get("knob") or "", r.get("knob_value") or ""))
    cols_out = ["product", "step_id", "ppid", "knob", "knob_value", "row_count", "root_count", "wafer_count", "example_lots"]
    answer = f"{', '.join(ppids)} 기준 KNOB 후보 {len(rows)}개를 찾았습니다." if rows else f"{', '.join(ppids)} 조건에 맞는 KNOB 값을 찾지 못했습니다."
    if fab_rows:
        answer += f" FAB 매칭 lot {len(roots)}개를 ML_TABLE에 연결했습니다."
    return {
        "handled": True,
        "intent": "ppid_knob_lookup",
        "action": "query_knob_by_step_ppid",
        "answer": answer,
        "table": {"kind": "ppid_knob_lookup", "title": "PPID to KNOB lookup", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in rows[:max(1, min(100, max_rows * 8))]], "total": len(rows)},
        "filters": {"product": ml_product, "step_terms": step_terms, "ppid": ppids, "fab_root_count": len(roots)},
    }


def _is_index_form_prompt(prompt: str) -> bool:
    text = str(prompt or "")
    up = _upper(text)
    return ("INDEX" in up or "ADDP" in up or "인덱스" in text) and any(t in text or t in up for t in ("어떻게", "만들", "FORM", "폼", "식", "계산", "설명"))


def _handle_index_form_lookup(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _is_index_form_prompt(prompt):
        return {"handled": False}
    product_hint = _product_hint(prompt, product)
    terms = _flowi_report_terms(prompt, product=product_hint) or ["INDEX", "ADDP"]
    files = _ml_files(product_hint) + _et_files(product_hint) + _inline_files(product_hint)
    rows: list[dict[str, Any]] = []
    for source, source_files in (("ML_TABLE", _ml_files(product_hint)), ("ET", _et_files(product_hint)), ("INLINE", _inline_files(product_hint))):
        if not source_files:
            continue
        try:
            lf = _scan_parquet(source_files[:80])
            cols = _schema_names(lf)
            matches = _column_matches(cols, terms + ["INDEX", "ADDP"], include_knob_when_named=True)
            for col in matches[:12]:
                rec = {"source": source, "column": col, "non_null": "", "unique_count": "", "sample_values": "", "file_count": len(source_files)}
                try:
                    df = lf.select(pl.col(col).cast(_STR, strict=False).drop_nulls().alias(col)).limit(1000).collect()
                    vals = [_text(v) for v in df[col].to_list() if _text(v)]
                    rec["non_null"] = len(vals)
                    rec["unique_count"] = len(set(vals))
                    rec["sample_values"] = ", ".join(list(dict.fromkeys(vals))[:6])
                except Exception:
                    pass
                rows.append(rec)
        except Exception:
            continue
    templates = [
        {"source": "reformatter_template", "column": "scale_abs", "non_null": "", "unique_count": "", "sample_values": "source_col * scale + offset, optional abs"},
        {"source": "reformatter_template", "column": "python_expr", "non_null": "", "unique_count": "", "sample_values": "expr with named inputs, e.g. max({A}, {B})"},
        {"source": "reformatter_template", "column": "shot_formula", "non_null": "", "unique_count": "", "sample_values": "item_map + group_by shot/wafer keys + expr"},
        {"source": "reformatter_template", "column": "shot_agg", "non_null": "", "unique_count": "", "sample_values": "group_by shot keys + agg"},
        {"source": "reformatter_template", "column": "poly2_window", "non_null": "", "unique_count": "", "sample_values": "x_col/y_col + lsl/usl process window"},
    ]
    rows = rows[:max(1, min(80, max_rows * 6))] + templates
    cols_out = ["source", "column", "non_null", "unique_count", "sample_values", "file_count"]
    answer = (
        "INDEX/ADDP form은 실제 생성식 메타데이터가 있으면 reformatter 설정을 우선 확인해야 합니다. "
        "현재는 DB 컬럼 후보와 Flow reformatter에서 지원하는 form template을 함께 정리했습니다."
    )
    return {
        "handled": True,
        "intent": "index_form_lookup",
        "action": "explain_index_addp_form",
        "answer": answer,
        "table": {"kind": "index_form_lookup", "title": "INDEX/ADDP form lookup", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in rows], "total": len(rows)},
        "filters": {"product": product_hint, "terms": terms, "file_count": len(files)},
    }
