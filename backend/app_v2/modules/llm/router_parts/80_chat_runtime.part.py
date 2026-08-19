def _flowi_home_response_for_role(result: dict[str, Any], me: dict[str, Any]) -> dict[str, Any]:
    if (me.get("role") or "user") == "admin":
        return result
    if not isinstance(result, dict):
        return result
    out: dict[str, Any] = {
        "ok": bool(result.get("ok", True)),
        "active": bool(result.get("active", True)),
        "answer": result.get("answer") or "",
    }
    if result.get("run_id"):
        out["run_id"] = result.get("run_id")
    if isinstance(result.get("execution"), dict):
        out["execution"] = deepcopy(result.get("execution") or {})
    for key in ("prompt", "input_prompt", "resolved_prompt"):
        if result.get(key):
            out[key] = result.get(key)
    if isinstance(result.get("graph"), dict):
        out["graph"] = deepcopy(result.get("graph") or {})
    if result.get("runtime_status"):
        out["runtime_status"] = result.get("runtime_status")
    if result.get("error"):
        out["error"] = result.get("error")
    if result.get("blocked"):
        out["blocked"] = True
    if result.get("reject_reason"):
        out["reject_reason"] = result.get("reject_reason")
    if result.get("last_partial_prompt"):
        out["last_partial_prompt"] = result.get("last_partial_prompt")
    if isinstance(result.get("missing_freetext"), list):
        out["missing_freetext"] = result.get("missing_freetext") or []
    tool = result.get("tool") if isinstance(result.get("tool"), dict) else {}
    public_tool = {key: deepcopy(tool[key]) for key in _FLOWI_HOME_USER_TOOL_KEYS if key in tool}
    clarification = public_tool.get("clarification") if isinstance(public_tool.get("clarification"), dict) else {}
    choices = clarification.get("choices") if isinstance(clarification.get("choices"), list) else []
    if choices:
        safe_choices = []
        for choice in choices[:3]:
            if not isinstance(choice, dict):
                continue
            safe = {
                key: choice.get(key)
                for key in ("id", "label", "title", "description", "prompt", "tab", "feature", "value", "recommended")
                if key in choice
            }
            safe_choices.append(safe)
        public_tool["clarification"] = {
            "question": clarification.get("question") or "확인이 필요합니다.",
            "choices": safe_choices,
        }
    if public_tool:
        out["tool"] = public_tool
    if isinstance(result.get("action_log"), dict):
        out["action_log"] = deepcopy(result.get("action_log") or {})
    trace = result.get("trace") if isinstance(result.get("trace"), dict) else {}
    if trace:
        out["trace"] = {
            key: deepcopy(trace[key])
            for key in (
                "kind",
                "visible",
                "note",
                "activation",
                "semantic",
                "plan",
                "unit_ai_selection",
                "guardrail",
                "interpretation",
                "evidence",
                "validation",
                "subagent_context",
                "clarification_loop",
                "retrieved_knowledge",
                "workflow_matches",
                "steps",
                "api_calls",
            )
            if key in trace
        }
    return out


def _handle_fab_reference(prompt: str, product: str = "") -> dict[str, Any] | None:
    """결정적 단일 파일 FAB 레퍼런스 답변 (step_id <-> step, ppid -> knob).

    LLM 유무와 무관하게 동작하는 read-only 조회. 매칭/의도가 없으면 None 을 돌려
    기존 라우팅(LLM function-call / 휴리스틱 fallback)으로 자연스럽게 넘어간다.
    답변은 단순 텍스트와 작은 근거 표를 함께 담는다.
    """
    try:
        from core import fab_reference
    except Exception:
        return None
    step = fab_reference.lookup_step_in_text(prompt, product)
    if step:
        cols = ["product", "step_id", "function_step"]
        rows = [{c: match.get(c, "") for c in cols} for match in (step.get("matches") or [])]
        return {
            "handled": True,
            "type": "answer",
            "intent": "step_lookup",
            "action": step.get("direction") or "lookup_step",
            "feature": "step_lookup",
            "unit_ai": "step_lookup",
            "answer": step.get("answer") or "",
            "table": {"kind": "step_matching", "title": "Step 매칭", "columns": _table_columns(cols), "rows": rows, "total": len(rows)} if rows else {},
            "source_ids": ["step_matching.csv"],
        }
    ppid = fab_reference.classify_ppid_in_text(prompt, product)
    if ppid:
        cols = ["value", "category", "feature_name", "function_step", "rule_order"]
        rows = [{c: match.get(c, "") for c in cols} for match in (ppid.get("matches") or [])]
        return {
            "handled": True,
            "type": "answer",
            "intent": "ppid_knob",
            "action": "classify_ppid_knob",
            "feature": "ppid_knob",
            "unit_ai": "ppid_knob",
            "answer": ppid.get("answer") or "",
            "table": {"kind": "ppid_knob", "title": "PPID Knob 분류", "columns": _table_columns(cols), "rows": rows, "total": len(rows)} if rows else {},
            "source_ids": ["ppid_knob.csv"],
        }
    return None


def _handle_semantic_measurement(prompt: str, product: str = "", max_rows: int = 25) -> dict[str, Any] | None:
    try:
        out = semantic_measure_catalog.query_measurement(prompt, product=product, max_rows=max_rows)
    except Exception as exc:
        logger.debug("semantic measurement query failed", exc_info=True)
        return {
            "handled": True,
            "intent": "semantic_measurement_lookup",
            "action": "query_semantic_measurement",
            "feature": "filebrowser_ai_sql",
            "answer": f"측정 용어 semantic 조회 중 오류가 발생했습니다: {str(exc)[:160]}",
            "warnings": [str(exc)[:200]],
        }
    return out if isinstance(out, dict) and out.get("handled") else None


def _flowi_chat_deadline_s() -> float:
    """Per-turn wall-clock budget, kept just under the 10 minute client abort."""
    raw = str(os.environ.get("FLOW_FLOWI_CHAT_DEADLINE_S", "") or "").strip()
    try:
        value = float(raw) if raw else 570.0
    except (TypeError, ValueError):
        value = 570.0
    return max(15.0, min(590.0, value))


def _handle_explicit_splittable_view_fast_path(
    prompt: str,
    product: str,
    max_rows: int,
    allowed_keys: set[str] | None,
    agent_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Run obvious SplitTable view prompts before the generic Home router.

    This path is read-only and avoids repeated generic router/LLM structure
    passes for prompts such as "A1001 스플릿테이블 보여줘".
    """
    if allowed_keys is not None and "splittable" not in allowed_keys:
        return None
    if not _flowi_explicit_splittable_view_prompt(prompt):
        return None
    if _flowi_write_target_detected(prompt) or _flowi_splittable_note_intent(prompt):
        return None
    # "그 값을 스플릿테이블로 보여줘" should reuse the preceding result's
    # product/lot slots and immediately render the matrix in this same chat.
    context_tool = _handle_flowi_splittable_context_followup(
        prompt,
        product,
        max_rows,
        allowed_keys,
        agent_context,
    )
    if isinstance(context_tool, dict) and context_tool.get("handled"):
        return context_tool
    product_hint = _flowi_explicit_splittable_product_hint(prompt, product)
    if not product_hint:
        classified = _classified_lot_tokens(prompt)
        if classified.get("root_lot_ids") or classified.get("fab_lot_ids"):
            tool = _handle_wafer_split_at_step(prompt, product, max_rows)
            if isinstance(tool, dict) and tool.get("handled"):
                return tool
        if not (classified.get("root_lot_ids") or classified.get("fab_lot_ids")):
            root_hints = _flowi_explicit_splittable_root_hints(prompt)
            if root_hints:
                classified = {**classified, "root_lot_ids": root_hints}
        wafers = [int(w) for w in _wafer_tokens(prompt)]
        args = {
            "product": "",
            "root_lot_ids": classified.get("root_lot_ids") or [],
            "fab_lot_ids": classified.get("fab_lot_ids") or [],
            "wafer_ids": wafers,
            "lot_wf_ids": _flowi_lot_wf_ids(classified.get("root_lot_ids") or [], classified.get("fab_lot_ids") or [], wafers),
            "max_rows": max(1, min(int(max_rows or 12), 200)),
            "read_only": True,
            "side_effect": "none",
        }
        step = _flowi_func_step_token(prompt)
        if step:
            args["step"] = step
        choices_meta = _flowi_arguments_choices(["product"], prompt, args)
        choices: list[dict[str, Any]] = []
        fields = choices_meta.get("fields") if isinstance(choices_meta, dict) else []
        if isinstance(fields, list):
            for field in fields:
                if isinstance(field, dict) and field.get("field") == "product":
                    choices = [c for c in (field.get("choices") or []) if isinstance(c, dict) and not c.get("free_input")]
                    break
        return _flowi_set_inline_type({
            "handled": True,
            "intent": "splittable_view",
            "action": "clarify_product",
            "feature": "splittable",
            "answer": "product가 없는 SplitTable 요청입니다. 어느 product 기준으로 볼지 알려주세요.",
            "needs_input": True,
            "missing": ["product"],
            "pending_prompt": prompt.strip(),
            "arguments": args,
            "arguments_partial": args,
            "arguments_choices": choices_meta,
            "validation": {
                "valid": False,
                "missing": ["product"],
                "requires_confirmation": False,
                "raw_db_policy": "read_only",
            },
            "slots": {
                "product": "",
                "root_lot_ids": args.get("root_lot_ids") or [],
                "fab_lot_ids": args.get("fab_lot_ids") or [],
                "wafer_ids": args.get("wafer_ids") or [],
                "step": args.get("step") or "",
            },
            "clarification": {
                "question": "어느 product 기준으로 SplitTable을 볼까요?",
                "choices": choices[:3],
            },
        }, "message", prompt=prompt)
    tool = _handle_wafer_split_at_step(prompt, product_hint, max_rows)
    return tool if isinstance(tool, dict) and tool.get("handled") else None


def _flowi_split_nav_product_summary(product: str, max_rows: int) -> dict[str, Any] | None:
    """split_nav 인라인 데이터 — product 만 알 때 ML_TABLE root lot 요약 표.

    root lot 이 특정되면 split view 전체를 보여주지만, product 단독 요청은
    조회 대상 root lot 목록(wafer 수 포함)을 즉시 보여줘 링크만 주지 않게 한다."""
    files = _ml_files(product)
    if not files:
        return None
    try:
        lf = _scan_parquet(files)
        cols = _schema_names(lf)
        root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
        wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
        if not root_col:
            return None
        agg = [pl.len().alias("rows")]
        if wafer_col:
            agg.append(pl.col(wafer_col).n_unique().alias("wafers"))
        df = (lf.group_by(root_col).agg(agg)
                .sort(root_col)
                .limit(max(1, min(int(max_rows or 12), 50)))
                .collect())
    except Exception:
        logger.info("split_nav product summary failed", exc_info=True)
        return None
    rows = []
    for r in df.to_dicts():
        rows.append({
            "root_lot_id": str(r.get(root_col) or ""),
            "wafers": str(r.get("wafers") or ""),
            "rows": str(r.get("rows") or ""),
        })
    if not rows:
        return None
    columns = [
        {"key": "root_lot_id", "label": "ROOT LOT"},
        {"key": "wafers", "label": "WAFERS"},
        {"key": "rows", "label": "ROWS"},
    ]
    return {
        "handled": True,
        "intent": "split_nav",
        "feature": "splittable",
        "action": "query_splittable_view",
        "answer": f"{product} ML_TABLE에서 root lot {len(rows)}건을 조회했습니다. root lot을 지정하면 스플릿 매트릭스를 바로 보여드립니다.",
        "table": {
            "kind": "split_nav_product_summary",
            "title": f"{product} root lots",
            "placement": "below",
            "columns": columns,
            "rows": rows,
            "total": len(rows),
        },
    }


_TEACH_PREFIX_RE = re.compile(r"^\s*(기억해줘?|가르쳐줄게|외워줘?)\s*[:,]?\s*", re.IGNORECASE)
_FORGET_PREFIX_RE = re.compile(r"^\s*(잊어줘?|삭제해줘?)\s*[:,]?\s*", re.IGNORECASE)
_TEACH_SEP_RE = re.compile(r"\s*(?:->|→|=|은\s|는\s|:)\s*")


def _handle_flowi_teach(prompt: str, *, username: str) -> dict[str, Any] | None:
    """Human-in-the-loop 티칭 — "기억해: <용어>는 <답>" / "잊어줘: <용어>".

    결정적 조회가 못 찾은 매핑을 사용자가 직접 가르치면 flowi_fewshots 에
    저장하고, 이후 같은 용어 질문은 학습된 답으로 즉시 응답한다.
    """
    text = str(prompt or "").strip()
    if not text:
        return None
    forget_m = _FORGET_PREFIX_RE.match(text)
    if forget_m:
        term = text[forget_m.end():].strip().strip("'\"")
        # teach 는 공백 포함 term("겔징 테이블")을 허용하므로 forget 도 허용해야
        # 가르친 것을 지울 수 있다 (비대칭 방지). 문장형 오입력은 길이로 거른다.
        if not term or len(term) > 120:
            return None
        try:
            from core import flowi_fewshots
            removed = flowi_fewshots.forget(term)
        except Exception:
            removed = False
        return {
            "handled": True, "intent": "fewshot_forget", "feature": "fewshot",
            "action": "fewshot_forget",
            "answer": (f"'{term.upper()}' 학습 데이터를 삭제했습니다." if removed
                       else f"'{term.upper()}' 로 저장된 학습 데이터가 없습니다."),
        }
    teach_m = _TEACH_PREFIX_RE.match(text)
    if not teach_m:
        return None
    body = text[teach_m.end():].strip()
    parts = _TEACH_SEP_RE.split(body, maxsplit=1)
    if len(parts) != 2:
        return {
            "handled": True, "intent": "fewshot_teach_help", "feature": "fewshot",
            "action": "fewshot_teach_help",
            "answer": "형식: \"기억해: <용어>는 <답>\" 또는 \"기억해: <용어> -> <답>\". 예) 기억해: AB100000EC는 VIA1_FORMATION_EC",
        }
    term = parts[0].strip().strip("'\"")
    answer = parts[1].strip()
    if not term or not answer or len(term) > 120:
        return None
    try:
        from core import flowi_fewshots
        entry = flowi_fewshots.teach(term, answer, by=username, source="teach")
    except Exception:
        entry = None
    if not entry:
        return {
            "handled": True, "intent": "fewshot_teach_error", "feature": "fewshot",
            "action": "fewshot_teach_error",
            "answer": "학습 데이터 저장에 실패했습니다. 용어/답 형식을 확인해주세요.",
        }
    return {
        "handled": True, "intent": "fewshot_teach", "feature": "fewshot",
        "action": "fewshot_teach", "fewshot": {"term": entry.get("term")},
        "answer": f"기억했습니다: {entry.get('term')} → {entry.get('answer')}\n다음부터 이 용어 질문에 바로 답합니다. 수정은 같은 형식으로 다시, 삭제는 \"잊어줘: {entry.get('term')}\".",
    }


_FILE_DOC_PREFIX_RE = re.compile(r"^\s*파일\s*설명(?:\s*등록)?\s*[:,]?\s*", re.IGNORECASE)
_FILE_DOC_SEP_RE = re.compile(r"\s*(?:->|→|=|은\s|는\s|:)\s*")
_SEARCH_INTENT_RE = re.compile(r"(찾아|어디|검색|무슨|뭐(?:야|지|인지)|알려|의미|설명해)", re.IGNORECASE)
_TERM_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Z][A-Z0-9_\-.]{2,40}(?![A-Za-z0-9_])")


def _handle_file_doc_teach(prompt: str, *, username: str) -> dict[str, Any] | None:
    """파일 설명 등록 — "파일 설명: <파일명>은 <설명>". 전 유저 공유 카탈로그."""
    text = str(prompt or "").strip()
    m = _FILE_DOC_PREFIX_RE.match(text)
    if not m:
        return None
    body = text[m.end():].strip()
    parts = _FILE_DOC_SEP_RE.split(body, maxsplit=1)
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        return {
            "handled": True, "intent": "file_doc_help", "feature": "fewshot",
            "action": "file_doc_help",
            "answer": "형식: \"파일 설명: <파일명>은 <설명>\". 예) 파일 설명: step_matching.csv는 step_id와 function_step 매핑표",
        }
    try:
        from core import flowi_file_docs
        entry = flowi_file_docs.set_doc(parts[0].strip().strip("'\""), parts[1].strip(), by=username)
    except Exception:
        entry = None
    if not entry:
        return {
            "handled": True, "intent": "file_doc_error", "feature": "fewshot",
            "action": "file_doc_error",
            "answer": "파일 설명 저장에 실패했습니다. 파일명/설명 형식을 확인해주세요.",
        }
    return {
        "handled": True, "intent": "file_doc_teach", "feature": "fewshot",
        "action": "file_doc_teach", "file_doc": {"file": entry.get("file")},
        "answer": f"파일 설명을 저장했습니다: {entry.get('file')} — {entry.get('description')}\n이제 이 설명과 관련된 질문에서 이 파일을 검색 대상으로 씁니다.",
    }


_KCARD_FILL_RE = re.compile(r"^\s*지식\s*(?:카드\s*)?채움(?:\s*수행)?\s*$", re.IGNORECASE)
_KCARD_STATUS_RE = re.compile(r"^\s*지식\s*(?:카드\s*)?(?:현황|상태)\s*$", re.IGNORECASE)
_KCARD_SHOW_RE = re.compile(r"^\s*지식\s*보기\s*[:：]\s*(.+)$", re.IGNORECASE)
_KCARD_APPROVE_RE = re.compile(r"^\s*지식\s*승인\s*[:：]\s*(.+)$", re.IGNORECASE)
_KCARD_REJECT_RE = re.compile(r"^\s*지식\s*반려\s*[:：]\s*(.+)$", re.IGNORECASE)
_KCARD_QUESTIONS_RE = re.compile(r"^\s*지식\s*질문\s*$", re.IGNORECASE)
_KCARD_ANSWER_RE = re.compile(r"^\s*지식\s*답변\s*[:：]\s*(.+)$", re.IGNORECASE | re.DOTALL)


def _kcard_questions_lines(questions: dict[str, list[str]]) -> list[str]:
    lines: list[str] = []
    for term, qs in questions.items():
        for q in qs[:3]:
            lines.append(f"- [{term}] {q}")
    if lines:
        lines.append('답변: "지식 답변: <term> <답변 내용>" 형식으로 알려주시면 카드에 병합됩니다.')
    return lines


def _handle_knowledge_card_admin(prompt: str, *, me: dict[str, Any]) -> dict[str, Any] | None:
    """지식 카드 관리 명령 — 관리자가 flow-i 채팅에서 지식 레이어를 운영한다.

    "지식 채움 수행": todo 카드를 연결 LLM(GPT OSS 등)으로 초안(draft) 작성.
    "지식 현황" / "지식 보기: <term>" / "지식 승인: <term>" / "지식 반려: <term>".
    draft 는 승인 전까지 조회/프롬프트에 노출되지 않는다 (HITL).
    """
    text = str(prompt or "").strip()
    if not text:
        return None
    fill_m = _KCARD_FILL_RE.match(text)
    status_m = _KCARD_STATUS_RE.match(text)
    show_m = _KCARD_SHOW_RE.match(text)
    approve_m = _KCARD_APPROVE_RE.match(text)
    reject_m = _KCARD_REJECT_RE.match(text)
    questions_m = _KCARD_QUESTIONS_RE.match(text)
    answer_m = _KCARD_ANSWER_RE.match(text)
    if not (fill_m or status_m or show_m or approve_m or reject_m or questions_m or answer_m):
        return None
    base = {"handled": True, "feature": "knowledge_cards", "unit_ai": "knowledge_cards"}
    if (me.get("role") or "user") != "admin":
        return {**base, "intent": "knowledge_card_blocked", "action": "blocked", "blocked": True,
                "answer": "지식 카드 관리는 관리자 전용입니다."}
    from core import knowledge_cards

    if status_m:
        summary = knowledge_cards.status_summary()
        pending_qs = knowledge_cards.pending_fill_questions()
        n_qs = sum(len(v) for v in pending_qs.values())
        lines = [
            f"지식 카드 현황 — 상태별: {summary['counts']}, 출처별: {summary['origins']}",
            f"채움 대기(todo): {', '.join(summary['todo']) or '(없음)'}",
            f"승인 대기(draft): {', '.join(summary['draft']) or '(없음)'}",
        ]
        if n_qs:
            lines.append(f'답변 대기 질문 {n_qs}건 — "지식 질문" 으로 확인하세요.')
        if summary["todo"]:
            lines.append('"지식 채움 수행" 으로 연결된 AI가 환경을 조사해 todo 카드 초안을 작성합니다.')
        if summary["draft"]:
            lines.append('"지식 보기: <term>" 으로 초안 확인, "지식 승인: <term>" / "지식 반려: <term>" 으로 처리하세요.')
        return {**base, "intent": "knowledge_card_status", "action": "knowledge_card_status",
                "answer": "\n".join(lines), "knowledge_status": summary}
    if questions_m:
        pending_qs = knowledge_cards.pending_fill_questions()
        q_lines = _kcard_questions_lines(pending_qs)
        return {**base, "intent": "knowledge_card_questions", "action": "knowledge_card_questions",
                "answer": ("채움에 필요한 질문입니다.\n" + "\n".join(q_lines)) if q_lines
                          else "답변 대기 중인 질문이 없습니다.",
                "knowledge_questions": pending_qs}
    if answer_m:
        result = knowledge_cards.answer_fill_question(
            answer_m.group(1).strip(), by=str(me.get("username") or "admin"))
        if not result:
            pending_qs = knowledge_cards.pending_fill_questions()
            terms = ", ".join(sorted(pending_qs) or [c["term"] for c in knowledge_cards.cards_by_status("draft")])
            return {**base, "intent": "knowledge_card_answer", "action": "knowledge_card_answer",
                    "answer": ("형식: \"지식 답변: <term> <답변 내용>\". "
                               + (f"대상 카드: {terms}" if terms else "답변 대기 카드가 없습니다."))}
        rest = result.get("remaining_questions") or []
        lines = [f"'{result['term']}' 카드에 답변을 병합했습니다 (상태: {result.get('status')})."]
        if rest:
            lines.append(f"이 카드의 남은 질문 {len(rest)}건: {rest[0]}")
        elif result.get("status") == "draft":
            lines.append(f'질문이 모두 해소되면 "지식 승인: {result["term"]}" 으로 활성화하세요.')
        return {**base, "intent": "knowledge_card_answer", "action": "knowledge_card_answer",
                "answer": "\n".join(lines)}
    if show_m:
        card = knowledge_cards.find_card(show_m.group(1).strip())
        if not card:
            return {**base, "intent": "knowledge_card_show", "action": "knowledge_card_show",
                    "answer": f"'{show_m.group(1).strip()}' 카드를 찾지 못했습니다."}
        header = (f"[{card.get('status')}] {card['term']} ({card.get('kind') or 'concept'}, "
                  f"origin={card.get('origin')})")
        return {**base, "intent": "knowledge_card_show", "action": "knowledge_card_show",
                "answer": header + "\n" + str(card.get("body") or "(본문 없음)")}
    if approve_m:
        term = approve_m.group(1).strip()
        saved = knowledge_cards.set_status(term, "active", by=str(me.get("username") or "admin"))
        return {**base, "intent": "knowledge_card_approve", "action": "knowledge_card_approve",
                "answer": (f"'{term}' 카드를 활성화했습니다 — 이제 flow-i 조회/프롬프트에 사용됩니다."
                           if saved else f"'{term}' 카드를 찾지 못했습니다.")}
    if reject_m:
        term = reject_m.group(1).strip()
        removed = knowledge_cards.forget_card(term)
        return {**base, "intent": "knowledge_card_reject", "action": "knowledge_card_reject",
                "answer": (f"'{term}' 초안을 삭제했습니다. (시드 todo 틀은 유지되어 다시 채울 수 있습니다)"
                           if removed else f"'{term}' 로컬 카드가 없습니다.")}
    # 채움 수행 — todo 카드를 연결 LLM 으로 초안 작성 (draft 저장, 승인 전 미노출).
    result = knowledge_cards.fill_todo_cards()
    if not result.get("ok"):
        return {**base, "intent": "knowledge_card_fill", "action": "knowledge_card_fill",
                "answer": "LLM 이 연결되어 있지 않아 지식 채움을 수행하지 못했습니다. 관리 > 진단에서 LLM 연결을 확인해 주세요."}
    filled = result.get("filled") or []
    failed = result.get("failed") or []
    questions = result.get("questions") or {}
    lines = [f"지식 채움 완료 — 환경(파일/컬럼)을 조사해 초안 {len(filled)}건 작성"
             + (f", 실패 {len(failed)}건({', '.join(failed)})" if failed else "")
             + f", 남은 todo {result.get('remaining_todo', 0)}건."]
    for term in filled:
        n_q = len(questions.get(term) or [])
        lines.append(f"- {term} → draft 저장" + (f" (추가 질문 {n_q}건)" if n_q else ""))
    q_lines = _kcard_questions_lines(questions)
    if q_lines:
        lines.append("")
        lines.append("데이터로 확인하지 못해 여쭤봅니다:")
        lines.extend(q_lines)
    if filled:
        lines.append('"지식 보기: <term>" 으로 초안 확인, "지식 승인: <term>" 으로 활성화. (승인 전에는 미사용)')
    return {**base, "intent": "knowledge_card_fill", "action": "knowledge_card_fill",
            "answer": "\n".join(lines), "knowledge_fill": result}


def _handle_file_doc_search(prompt: str, *, allowed_keys: set[str] | list[str],
                            username: str) -> dict[str, Any] | None:
    """파일 설명문 기반 최후 검색 — 다른 라우팅이 처리하지 못한 질문에서
    설명 카탈로그로 대상 파일을 고르고 그 안에서 용어를 찾아 답한다.
    못 찾으면 human-in-the-loop 안내(few-shot 티칭 또는 파일 설명 등록)를 준다."""
    text = str(prompt or "").strip()
    if not text or "filebrowser" not in {str(k).strip().lower() for k in allowed_keys}:
        return None
    if not _SEARCH_INTENT_RE.search(text):
        return None
    from core import fab_reference
    tokens = fab_reference.extract_step_tokens(text)
    if not tokens:
        tokens = [t.group(0) for t in _TERM_TOKEN_RE.finditer(text.upper())]
    if not tokens:
        return None
    token = tokens[0]
    try:
        from core import flowi_file_docs
        docs = flowi_file_docs.match_files(text)
    except Exception:
        docs = []
    hits: list[dict[str, Any]] = []
    if docs:
        try:
            hits = fab_reference.search_in_files(token, [d.get("file") for d in docs])
        except Exception:
            hits = []
    if hits:
        lines = [f"'{token}' 검색 결과 (파일 설명 카탈로그 기반):"]
        for h in hits[:4]:
            lines.append(f"\n[{h['file']}] {h['hit_rows']}행 ({', '.join(h['columns'][:4])} 열)")
            for s in h.get("samples") or []:
                pairs = [f"{k}={v}" for k, v in list(s.items())[:5] if v]
                lines.append("- " + ", ".join(pairs))
        lines.append("\n수정이 필요하면 기타 메뉴의 Files에서 해당 파일을 열어 편집하세요.")
        return {
            "handled": True, "intent": "file_doc_search", "feature": "fewshot",
            "action": "file_doc_search", "search_token": token,
            "file_hits": [{k: h[k] for k in ("file", "hit_rows", "columns")} for h in hits],
            "answer": "\n".join(lines),
        }
    # 검색 대상/결과 없음 — human-in-the-loop 안내.
    guide = [
        f"'{token}'에 대한 답을 아직 찾지 못했습니다.",
    ]
    if docs:
        guide.append("설명이 등록된 파일(" + ", ".join(str(d.get("file")) for d in docs[:3]) + ")에서는 등장하지 않았습니다.")
    else:
        guide.append("관련 파일 설명이 아직 등록돼 있지 않습니다.")
    guide.append(
        "도와주실 수 있다면:\n"
        f"- 답을 아시면 → \"기억해: {token}는 <답>\"\n"
        "- 어느 파일에 있는지 아시면 → \"파일 설명: <파일명>은 <설명>\" 으로 등록해주세요. 다음부터 그 파일을 검색해 답합니다."
    )
    return {
        "handled": True, "intent": "file_doc_search_miss", "feature": "fewshot",
        "action": "file_doc_search_miss", "search_token": token,
        "answer": "\n".join(guide),
    }


def _shared_skill_match(prompt: str) -> tuple[dict[str, Any] | None, float, list[dict[str, Any]]]:
    """공유 스킬 카탈로그에서 prompt 와 가장 잘 맞는 스킬을 찾는다."""
    lowered = str(prompt or "").strip().lower()
    try:
        from core import skills_repo
        skills = skills_repo.shared_skills()
    except Exception:
        return None, 0.0, []
    if not skills or not lowered:
        return None, 0.0, skills
    prompt_tokens = {t for t in re.split(r"[\s,/()\[\]{}:;'\"]+", lowered) if len(t) >= 2}
    best: dict[str, Any] | None = None
    best_score = 0.0
    for skill in skills:
        title = str(skill.get("title") or "").strip().lower()
        key = str(skill.get("key") or "").strip().lower()
        name_tokens = {t for t in re.split(r"[\s_\-,/]+", f"{title} {key}") if len(t) >= 2}
        if not name_tokens:
            continue
        if title and title in lowered:
            score = 1.0
        else:
            score = len(name_tokens & prompt_tokens) / max(1, len(name_tokens))
        if score > best_score:
            best, best_score = skill, score
    return best, best_score, skills


def _handle_shared_skill_request(prompt: str, *, username: str, max_rows: int,
                                 allowed_keys: set[str] | list[str]) -> dict[str, Any] | None:
    """공유 스킬 실행/안내 핸들러.

    - "스킬" 언급 + 매칭 약함 → 공유 스킬 카탈로그 안내.
    - 매칭 강함(제목 포함 또는 스킬 언급 + 토큰 과반) → sql_workspace 스킬은
      read-only 로 즉시 실행해 결과 요약을 답하고, chain 스킬은 단계 안내.
    - 매칭 없으면 None → 기존 라우팅 계속 (회귀 없음).
    """
    text = str(prompt or "").strip()
    if not text:
        return None
    explicit = ("스킬" in text) or ("skill" in text.lower())
    best, score, skills = _shared_skill_match(text)
    # 카탈로그 우선 — "스킬 목록/알려줘"류 질문은 실행 동사가 없으면 목록을
    # 보여준다 (이전 대화 문맥이 프롬프트를 실행으로 넓혀 해석하는 것 방지).
    wants_catalog = (
        explicit
        and any(w in text for w in ("목록", "리스트", "카탈로그", "어떤", "뭐", "알려", "보여"))
        and not any(w in text.lower() for w in ("실행", "돌려", "run"))
    )
    threshold = 0.5 if explicit else 1.0
    if wants_catalog or not best or score < threshold:
        if explicit and skills:
            allowed_lower = {str(k).strip().lower() for k in allowed_keys}
            names = []
            for s in skills[:12]:
                req = {str(f).strip().lower() for f in (s.get("required_features") or []) if str(f).strip()}
                if s.get("kind") == "sql_workspace":
                    req.add("filebrowser")
                lacking = sorted(req - allowed_lower)
                names.append(
                    f"- {s.get('title') or s.get('key')} ({'SQL' if s.get('kind') == 'sql_workspace' else '체인'}"
                    + (f", 실행 {int(s.get('run_count') or 0)}회" if s.get("run_count") else "")
                    + (f", 권한 필요: {', '.join(lacking)}" if lacking else "") + ")"
                )
            return {
                "handled": True,
                "intent": "skill_catalog",
                "feature": "skills",
                "action": "skill_catalog",
                "answer": "사용 가능한 공유 스킬입니다. \"<스킬 제목> 스킬 실행\" 형태로 입력하면 바로 실행합니다.\n" + "\n".join(names),
            }
        return None
    kind = str(best.get("kind") or "").strip()
    title = best.get("title") or best.get("key")
    # 권한 게이트 — 스킬이 요구하는 기능 권한이 사용자에게 전부 있어야 실행.
    # 권한이 다른 시스템(기능)에 스킬을 통해 우회 접근하는 것을 막는다.
    required = {str(f).strip().lower() for f in (best.get("required_features") or []) if str(f).strip()}
    if kind == "sql_workspace":
        required.add("filebrowser")
    missing = sorted(required - {str(k).strip().lower() for k in allowed_keys})
    if missing:
        return {
            "handled": True, "intent": "skill_run_blocked", "feature": "skills",
            "action": "skill_run_blocked", "skill_key": best.get("key"),
            "missing_features": missing,
            "answer": f"'{title}' 스킬 실행에는 {', '.join(missing)} 권한이 필요합니다. 페이지 관리자에게 권한을 요청하세요.",
        }
    if kind == "sql_workspace":
        placeholders = best.get("placeholders") or {}
        if placeholders:
            names = ", ".join(sorted(str(k) for k in placeholders.keys()))
            return {
                "handled": True, "intent": "skill_needs_input", "feature": "skills",
                "action": "skill_needs_input", "skill_key": best.get("key"),
                "answer": f"'{title}' 스킬은 입력값({names})이 필요합니다. 기타 메뉴의 SQL 작업대에서 값을 채워 실행해주세요.",
            }
        try:
            from core import skills_repo
            from core import sql_workspace as _sw_engine
            cells = [dict(c) for c in (best.get("cells") or []) if isinstance(c, dict)]
            if not cells:
                return None
            out = _sw_engine.run_workspace(cells, row_limit=max(20, min(200, int(max_rows or 12) * 10)))
            result = out.get("result") or {}
            columns = [str(c) for c in (result.get("columns") or [])]
            rows = result.get("rows") or []
            rowcount = int(result.get("rowcount") or len(rows))
            preview_lines = []
            if columns:
                preview_lines.append(" | ".join(columns[:8]))
            for row in rows[:10]:
                if isinstance(row, dict):
                    preview_lines.append(" | ".join(str(row.get(c, "")) for c in columns[:8]))
                elif isinstance(row, (list, tuple)):
                    preview_lines.append(" | ".join(str(v) for v in row[:8]))
            skills_repo.increment_run_count(best.get("key") or "")
            answer = (
                f"공유 스킬 '{title}' 실행 결과 — {rowcount}행"
                + (f" (미리보기 {min(10, len(rows))}행)" if rows else "")
                + ("\n" + "\n".join(preview_lines) if preview_lines else "\n(결과 없음)")
            )
            return {
                "handled": True, "intent": "skill_run", "feature": "skills",
                "action": "skill_run", "skill_key": best.get("key"),
                "skill_result": {"columns": columns, "rows": rows[:50], "rowcount": rowcount,
                                 "elapsed_ms": out.get("elapsed_ms")},
                "answer": answer,
            }
        except ValueError as e:
            return {
                "handled": True, "intent": "skill_run_error", "feature": "skills",
                "action": "skill_run_error", "skill_key": best.get("key"),
                "answer": f"'{title}' 스킬 실행이 거부되었습니다: {e}",
            }
        except Exception as e:
            logger.warning("shared skill run failed key=%s: %s", best.get("key"), e)
            return {
                "handled": True, "intent": "skill_run_error", "feature": "skills",
                "action": "skill_run_error", "skill_key": best.get("key"),
                "answer": f"'{title}' 스킬 실행 중 오류가 발생했습니다: {str(e)[:200]}",
            }
    steps = best.get("steps") or []
    step_lines = []
    for i, step in enumerate(steps[:12], start=1):
        if isinstance(step, dict):
            step_lines.append(f"{i}. {step.get('action') or step.get('tool') or step}")
        else:
            step_lines.append(f"{i}. {step}")
    return {
        "handled": True, "intent": "skill_guide", "feature": "skills",
        "action": "skill_guide", "skill_key": best.get("key"),
        "answer": f"공유 스킬 '{title}'의 단계 안내입니다.\n" + ("\n".join(step_lines) if step_lines else "(등록된 단계 없음)"),
    }


def _try_flowi_react_orchestration(
    prompt: str,
    *,
    me: dict[str, Any],
    allowed_keys: set[str],
) -> tuple[dict[str, Any] | None, str]:
    """LLM(GPT OSS/adapter) ReAct 오케스트레이션 — 홈 챗 경로.

    home_orchestrator 의 반복 루프가 도구 카탈로그에서 필요한 기능을 골라
    연쇄 실행한다 (유저 최대 8턴/admin 연장, `_react_max_iters`). 모델이 ask_user 를 선택하면
    clarification(질문 + 선택지)으로 반환해 사용자가 답하고 이어갈 수 있다
    (human-in-the-loop). 비활성/LLM 실패 시 (None, 사유) → 기존 단일 패스
    엔진으로 graceful degrade. 사유는 폴백 응답의 warnings 로 표면화된다.
    """
    try:
        from core import home_orchestrator as _ho

        if not _ho.react_available():
            return None, "react_disabled"
        out = _ho.orchestrate(prompt, user=me)
    except Exception:
        logger.info("flowi react orchestration failed", exc_info=True)
        return None, "react_error"
    if not isinstance(out, dict) or (out.get("meta") or {}).get("planner") != "react":
        planner = str(((out.get("meta") or {}).get("planner")) if isinstance(out, dict) else "") or "unknown"
        return None, f"llm_degraded:{planner}"
    meta = out.get("meta") or {}
    trace = out.get("trace") or []
    react_info = {
        "steps": len(trace),
        "max_steps": 8,
        "stop_reason": meta.get("stop_reason") or "",
        "tools": [str(r.get("tool") or "") for r in trace if isinstance(r, dict)][:8],
    }

    # Human-in-the-loop: 모델이 사용자 확인을 요청 — 선택지는 self-contained
    # 프롬프트로 만들어 클릭 시 원 질문 + 답변이 함께 재요청되게 한다.
    ask = out.get("ask_user") or {}
    if isinstance(ask, dict) and str(ask.get("question") or "").strip():
        question = str(ask.get("question")).strip()
        choices = [
            {
                "label": str(i + 1),
                "title": str(c),
                "submit_prompt": f"{prompt}\n(사용자 답변: {c})",
            }
            for i, c in enumerate(ask.get("choices") or [])
            if str(c or "").strip()
        ][:3]
        return {
            "handled": True,
            "type": "answer",
            "intent": "react_ask_user",
            "feature": "home",
            "unit_ai": "react_orchestrator",
            "action": "ask_user",
            "blocked": True,
            "answer": question,
            "clarification": {"question": question, "choices": choices},
            "react": react_info,
        }, ""

    reply = str(out.get("reply") or "").strip()
    # 마지막 성공 도구 결과의 표시용 payload(표/차트/네비게이션)를 인라인으로 전달.
    merged: dict[str, Any] = {}
    for call in reversed(out.get("tool_calls") or []):
        if not isinstance(call, dict) or call.get("status") != "success":
            continue
        output = call.get("output") if isinstance(call.get("output"), dict) else {}
        preview = output.get("preview") if isinstance(output.get("preview"), dict) else {}
        if "table" not in output and preview.get("rows"):
            # FileBrowser AI SQL 같은 unit runtime 결과 — preview rows 를 챗 인라인
            # 표로 변환해 링크/요약만 남지 않게 한다.
            prev_rows = [r for r in preview.get("rows") if isinstance(r, dict)][:50]
            prev_cols = [str(c) for c in (preview.get("columns") or [])] or (
                list(prev_rows[0].keys()) if prev_rows else [])
            if prev_rows and prev_cols:
                output = {**output, "table": {
                    "kind": "react_runtime_preview",
                    "title": "조회 결과 미리보기",
                    "placement": "below",
                    "columns": [{"key": c, "label": c.upper()} for c in prev_cols[:24]],
                    "rows": prev_rows,
                    "total": preview.get("total_rows") or len(prev_rows),
                }}
        if any(k in output for k in ("table", "split_view", "splittable_view",
                                     "chart_result", "chart", "rows", "lot_list", "navigate")):
            merged = output
            break
    if not reply and not merged:
        return None, "react_empty"
    tool: dict[str, Any] = {
        "handled": True,
        "type": "answer",
        "intent": "react_orchestration",
        "feature": str(merged.get("feature") or "home"),
        "unit_ai": "react_orchestrator",
        "action": "react_loop",
        "answer": reply or str(merged.get("answer") or ""),
        "react": react_info,
    }
    for key in ("table", "split_view", "splittable_view", "chart_result", "chart",
                "rows", "lot_list", "navigate", "chart_session_id"):
        if key in merged:
            tool[key] = merged[key]
    # 표시용 payload 만 옮기면 카드의 "근거 / 출처 / 재현 SQL" 이 비어버린다 — ReAct 로
    # 왔다는 이유만으로 같은 답의 근거가 사라지면 안 되므로 증거 필드도 함께 옮긴다.
    for key in ("filters", "source", "source_ids", "source_detail", "sql_draft",
                "delay_notice", "interpretation", "step_groups", "cache_generated_at"):
        if key in merged:
            tool[key] = merged[key]
    return tool, ""


def _run_flowi_chat_maybe_offloaded(
    *,
    prompt: str,
    product: str,
    max_rows: int,
    me: dict[str, Any],
    source_ai: str = "",
    client_run_id: str = "",
    agent_context: dict[str, Any] | None = None,
    allow_rag_update: bool = False,
) -> dict[str, Any]:
    """Flow-i 턴을 워커(개발서버)에 여유가 있으면 위임, 아니면 로컬 실행.

    Flow-i 는 LLM 대기(수초~수십초)가 지배해 큐 왕복(~1초)이 체감되지 않고,
    부수 상태(차트 세션·유저 이벤트 md·활동 로그)는 전부 공유 data_root 에
    남아 어느 서버가 실행해도 이후 요청(chart-session raw-data 등)을 그대로
    서빙한다. 운영서버는 스플릿테이블/plan 상호작용 보장이 최우선이므로
    flowi 의 데이터 스캔/컨텍스트 빌드 CPU·메모리를 워커로 넘긴다. 워커
    다운/과부하/큐 포화면 run_heavy 가 로컬 실행 — 기능 동일, 위치만 바뀐다.

    워커에서 난 HTTPException 은 {"http_error"} 봉투로 돌아와 재실행 없이
    그대로 변환한다 — LLM 이중 호출(이중 과금·이중 이벤트 기록)을 막는다.
    FLOW_FLOWI_OFFLOAD=0 으로 끄고, 타임아웃은 FLOW_FLOWI_OFFLOAD_TIMEOUT_SEC
    (기본 570초)."""

    def _local() -> dict[str, Any]:
        return {"ok": True, "result": _run_flowi_chat(
            prompt=prompt, product=product, max_rows=max_rows, me=me,
            source_ai=source_ai, client_run_id=client_run_id,
            agent_context=agent_context, allow_rag_update=allow_rag_update,
        )}

    execution_class, execution_reason = _flowi_turn_execution_class(prompt, agent_context)
    disabled = str(os.environ.get("FLOW_FLOWI_OFFLOAD", "1")).strip().lower() in {"0", "false", "no", "off"}
    local_called = False

    def _tracked_local() -> dict[str, Any]:
        nonlocal local_called
        local_called = True
        return _local()

    # 검색/캐시 조회/SplitTable view/직전 표 재표시는 운영 API의 warm RAM과
    # 세션을 바로 쓰는 편이 빠르다. 무거운 분석 턴만 개발 worker로 위임한다.
    if disabled or execution_class == "light":
        env = _tracked_local() if execution_class == "heavy" else _local()
    else:
        from core import worker_dispatch as _wd
        try:
            timeout = float(os.environ.get("FLOW_FLOWI_OFFLOAD_TIMEOUT_SEC", "") or 570.0)
        except Exception:
            timeout = 570.0
        env = _wd.run_heavy(
            "flowi_chat_turn",
            {
                "prompt": prompt,
                "product": product,
                "max_rows": int(max_rows or 0),
                "me": dict(me or {}),
                "source_ai": str(source_ai or ""),
                "client_run_id": str(client_run_id or ""),
                "agent_context": agent_context if isinstance(agent_context, dict) else None,
                "allow_rag_update": bool(allow_rag_update),
            },
            _tracked_local,
            timeout_sec=max(30.0, min(3600.0, timeout)),
            label="flowi_chat",
        ) or {}
    http_error = env.get("http_error") if isinstance(env, dict) else None
    if isinstance(http_error, dict) and http_error.get("status"):
        raise HTTPException(int(http_error["status"]), http_error.get("detail"))
    result = env.get("result") if isinstance(env, dict) else None
    if isinstance(result, dict):
        if execution_class == "light":
            target = "production_api" if _wd_server_role() != "worker" else "development_worker"
        elif local_called:
            target = "production_api_fallback" if _wd_server_role() != "worker" else "development_worker"
        else:
            target = "development_worker"
        result["execution"] = {
            "class": execution_class,
            "target": target,
            "policy": "light_on_api_heavy_on_worker",
            "reason": execution_reason,
        }
        return result
    # 봉투 형태가 아니면(형 불일치 등 예상 밖 응답) 로컬 실행이 최후 폴백.
    return _run_flowi_chat(
        prompt=prompt, product=product, max_rows=max_rows, me=me,
        source_ai=source_ai, client_run_id=client_run_id,
        agent_context=agent_context, allow_rag_update=allow_rag_update,
    )


def _wd_server_role() -> str:
    try:
        from core import worker_dispatch as _wd
        return str(_wd.server_role() or "api")
    except Exception:
        return "api"


def _flowi_turn_execution_class(
    prompt: str,
    agent_context: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Flow-i 턴을 운영 즉답(light)과 개발 worker 우선(heavy)으로 나눈다."""
    text = re.sub(r"\s+", " ", str(prompt or "")).strip()
    low = text.lower()
    anchor = _flowi_recent_tool_anchor(agent_context)
    if _flowi_raw_table_followup_intent(text, anchor):
        return "light", "직전 결과 재표시"
    if _is_teg_position_prompt(text):
        return "light", "TEG 탭 Shot 설정 조회"
    if _is_et_download_prompt(text):
        return "light", "ET 다운로드 대기열 등록"
    if _is_et_time_prompt(text):
        return "heavy", "DB ET 측정시간 집계"
    if _flowi_explicit_splittable_view_prompt(text) or any(term in low or term in text for term in (
        "스플릿테이블", "스플릿 테이블", "split table", "splittable",
    )):
        return "light", "SplitTable 조회/보기"
    try:
        from core import lot_wip
        if lot_wip.is_wip_prompt(text):
            return "light", "latest lot cache 현재위치 조회"
    except Exception:
        pass
    heavy_terms = (
        "분석", "원인", "진단", "rca", "상관", "correlation", "추이", "trend",
        "차트", "chart", "plot", "scatter", "dashboard", "대시보드", "sql",
        "parquet", "csv 다운로드", "전체 스캔", "raw db", "원본 db", "대용량",
        "비교해", "예측", "시뮬레이션",
    )
    if any(term in low or term in text for term in heavy_terms):
        return "heavy", "분석/원본 데이터 처리"
    light_actions = ("어디", "찾아", "검색", "조회", "보여", "알려", "목록", "step", "스텝", "schema", "스키마")
    if any(term in low or term in text for term in light_actions):
        return "light", "결정적 검색/조회"
    return "heavy", "오케스트레이션/LLM 처리"


def _run_flowi_chat(
    *,
    prompt: str,
    product: str,
    max_rows: int,
    me: dict[str, Any],
    source_ai: str = "",
    client_run_id: str = "",
    agent_context: dict[str, Any] | None = None,
    allow_rag_update: bool = False,
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc)
    username = me.get("username") or "user"
    prompt = (prompt or "").strip()
    if not prompt:
        raise HTTPException(400, "질문을 입력해주세요")

    source = _clean_source_ai(source_ai) if source_ai else ""
    client_run_id = str(client_run_id or "").strip()[:120]
    # 이 턴을 진행 표시 채널에 묶는다. 워커(개발서버)로 오프로드돼도 공유 data_root
    # 의 같은 파일에 쓰므로 API 서버의 폴링이 그대로 읽는다. run id 가 없으면 no-op.
    flowi_progress.bind(client_run_id)
    agent_context = home_memory.merge_agent_context(
        agent_context if isinstance(agent_context, dict) else {},
        username=username,
    )

    allowed_keys = _allowed_flowi_feature_keys(me)
    input_prompt = prompt
    semantic_choice = semantic_hitl.consume_choice(prompt, username=username)
    if semantic_choice:
        learned = semantic_choice.get("learned") or {}
        original = str(semantic_choice.get("original_prompt") or "").strip()
        prompt = original or f"{learned.get('source_type') or 'INLINE'} item_id {learned.get('item_id') or ''} 선택 결과를 확인해줘"
        prompt += (
            f"\n[Flow-i 사용자 확인: '{learned.get('term') or ''}'는 "
            f"{learned.get('source_type') or 'INLINE'} item_id={learned.get('item_id') or ''}, step_id={learned.get('step_id') or ''}]"
        )
        agent_context = {
            **agent_context,
            "semantic_learning": {
                "confirmed": True,
                "scope": learned.get("scope") or "shared",
                "source_type": learned.get("source_type") or "",
                "product": learned.get("product") or "",
                "term": learned.get("term") or "",
                "item_id": learned.get("item_id") or "",
                "step_id": learned.get("step_id") or "",
            },
        }
    prompt = _flowi_resolve_pending_core_prompt(prompt, agent_context, allowed_keys)
    if input_prompt and input_prompt != prompt:
        agent_context = {
            **agent_context,
            "_flowi_input_prompt": input_prompt,
            "_flowi_resolved_prompt": prompt,
        }
    if home_memory.is_memory_recall_prompt(prompt):
        tool = home_memory.recall_answer(prompt=prompt, username=username, agent_context=agent_context)
        answer = tool.get("answer") or ""
        _append_user_event(username, "home_memory_recall", _event_fields(
            {"prompt": prompt, "answer": answer, "turn_count": (tool.get("memory") or {}).get("turn_count")},
            source=source,
            client_run_id=client_run_id,
        ))
        result = {
            "ok": True,
            "active": True,
            "user": username,
            "answer": answer,
            "tool": tool,
            "llm": {"available": llm_adapter.is_available(), "used": False},
            "allowed_features": sorted(allowed_keys),
        }
        if source:
            result["agent_api"] = _agent_api_meta(
                source=source,
                client_run_id=client_run_id,
                username=username,
                tool=tool,
                agent_context=agent_context,
            )
        return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    # "raw data 줘"처럼 직전 표를 가리키는 후속 요청은 일반 라우터나 ReAct가
    # 새 질의로 해석하기 전에 처리한다. 직전 응답에서 이미 공개된 제한 행만
    # 재사용하므로 운영 API에서 즉시 끝나며 새 DB 스캔도 발생하지 않는다.
    raw_table_tool = _handle_flowi_raw_table_followup(prompt, agent_context, max_rows)
    if raw_table_tool.get("handled"):
        _finalize_flowi_tool(raw_table_tool, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)
        answer = str(raw_table_tool.get("answer") or "직전 raw data를 표시합니다.")
        result = {
            "ok": True,
            "active": True,
            "user": username,
            "answer": answer,
            "tool": raw_table_tool,
            "llm": {"available": llm_adapter.is_available(), "used": False, "skipped": "conversation_result_reuse"},
            "allowed_features": sorted(allowed_keys),
        }
        if source:
            result["agent_api"] = _agent_api_meta(
                source=source,
                client_run_id=client_run_id,
                username=username,
                tool=raw_table_tool,
                agent_context=agent_context,
            )
        return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    # "대시보드 보여줘" means the current WIP dashboard, so return the same
    # chart data used by My_Dashboard directly in Flow-i instead of a page-link
    # guidance response.
    if "dashboard" in allowed_keys:
        dashboard_wip_tool = _handle_flowi_dashboard_wip_view(prompt, product, me)
        if dashboard_wip_tool.get("handled"):
            _finalize_flowi_tool(
                dashboard_wip_tool,
                prompt=prompt,
                allowed_keys=allowed_keys,
                agent_context=agent_context,
            )
            answer = str(dashboard_wip_tool.get("answer") or "WIP 대시보드를 표시합니다.")
            result = {
                "ok": True,
                "active": True,
                "user": username,
                "answer": answer,
                "tool": dashboard_wip_tool,
                "llm": {"available": llm_adapter.is_available(), "used": False, "skipped": "deterministic_dashboard_view"},
                "allowed_features": sorted(allowed_keys),
            }
            return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    # Existing tab features exposed as inline Flow-i artifacts.  These are
    # deterministic and reuse the same backend functions as the TEG/ET tabs.
    inline_tab_tools: list[dict[str, Any]] = []
    if "teg" in allowed_keys:
        inline_tab_tools.append(_handle_teg_position_lookup(prompt, product, max_rows))
    if "reformatize" in allowed_keys:
        inline_tab_tools.append(_handle_et_download_request(prompt, product, me, agent_context))
    if "ettime" in allowed_keys:
        inline_tab_tools.append(_handle_et_time_request(prompt, product, max_rows, me, agent_context))
    tab_tool = next((item for item in inline_tab_tools if isinstance(item, dict) and item.get("handled")), None)
    if tab_tool:
        _finalize_flowi_tool(tab_tool, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)
        answer = str(tab_tool.get("answer") or "요청을 처리했습니다.")
        _append_user_event(username, tab_tool.get("intent") or "inline_tab_action", _event_fields(
            {"prompt": prompt, "intent": tab_tool.get("intent") or "", "feature": tab_tool.get("feature") or "", "answer": answer},
            source=source, client_run_id=client_run_id,
        ))
        result = {
            "ok": True, "active": True, "user": username, "answer": answer, "tool": tab_tool,
            "llm": {"available": llm_adapter.is_available(), "used": False, "skipped": "deterministic_tab_feature"},
            "allowed_features": sorted(allowed_keys),
        }
        if source:
            result["agent_api"] = _agent_api_meta(
                source=source, client_run_id=client_run_id, username=username,
                tool=tab_tool, agent_context=agent_context,
            )
        return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    # INLINE item ambiguity is a deterministic DB+HITL workflow. Run it before
    # generic Unit-AI/ReAct routing so an unknown item cannot become an LLM guess.
    if ("filebrowser" in allowed_keys or "dashboard" in allowed_keys) and _is_inline_item_lookup_prompt(prompt):
        inline_hitl_tool = _handle_inline_item_lookup(prompt, product, max_rows, username=username)
        if inline_hitl_tool.get("handled"):
            _finalize_flowi_tool(
                inline_hitl_tool,
                prompt=prompt,
                allowed_keys=allowed_keys,
                agent_context=agent_context,
            )
            answer = str(inline_hitl_tool.get("answer") or "INLINE item 후보를 조회했습니다.")
            _append_user_event(username, "inline_item_hitl", _event_fields(
                {
                    "prompt": prompt,
                    "intent": inline_hitl_tool.get("intent") or "inline_item_by_step_lookup",
                    "answer": answer,
                    "semantic_learning": inline_hitl_tool.get("semantic_learning") or {},
                },
                source=source,
                client_run_id=client_run_id,
            ))
            result = {
                "ok": True,
                "active": True,
                "user": username,
                "answer": answer,
                "tool": inline_hitl_tool,
                "llm": {"available": llm_adapter.is_available(), "used": False, "skipped": "deterministic_inline_hitl"},
                "allowed_features": sorted(allowed_keys),
            }
            if source:
                result["agent_api"] = _agent_api_meta(
                    source=source,
                    client_run_id=client_run_id,
                    username=username,
                    tool=inline_hitl_tool,
                    agent_context=agent_context,
                )
            return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    # "X 스플릿테이블 보여줘" — 결정적 네비게이션(split_nav)이 splittable fast-path 보다
    # 우선. ML_TABLE 에서 product 를 자동 확인해 SplitTable 페이지를 딥링크로 연다.
    # 단, confirm 마커 프롬프트(plan/note/inform 확인 토큰)는 전용 핸들러가 처리해야
    # 하므로 fast-path 가 가로채면 안 된다.
    _confirm_marker_prompt = str(prompt or "").lstrip().startswith((
        _FLOWI_SPLITTABLE_PLAN_MARKER, _FLOWI_SPLITTABLE_NOTE_MARKER,
        _FLOWI_INFORM_CONFIRM_MARKER, _FLOWI_INFORM_MAIL_MARKER,
        _FLOWI_INFORM_WALKTHROUGH_MARKER, _FLOWI_DATA_REGISTER_MARKER,
    ))
    # Explicit "show SplitTable" requests belong to the inline data fast path
    # below. The navigation Unit AI is only a fallback for open/move requests;
    # letting it run first can reduce the response to a tab button when data is
    # temporarily cold or the product still needs clarification.
    if (
        "splittable" in allowed_keys
        and not _confirm_marker_prompt
        and not _flowi_explicit_splittable_view_prompt(prompt)
    ):
        from core.flowi_units import try_dispatch as _nav_dispatch
        nav_tool = _nav_dispatch(
            prompt, product=product, max_rows=max_rows, allowed_keys=allowed_keys,
            agent_context=agent_context, me=me, only=("split_nav",))
        if nav_tool is not None:
            # 네비게이션 링크만 주지 않고 실제 스플릿 데이터를 즉시 조회해 인라인으로
            # 함께 반환한다 (탭 이동은 선택 버튼으로 유지, auto 이동 해제).
            nav_product = str(nav_tool.get("product") or product or "")
            nav_root = str(nav_tool.get("root_lot_id") or "")
            if nav_product:
                data_tool = None
                try:
                    if nav_root:
                        data_tool = _flowi_query_splittable_view_tool(
                            {"product": nav_product, "root_lot_ids": [nav_root],
                             "fab_lot_ids": [], "wafer_ids": [], "max_rows": max_rows},
                            nav_product, prompt, max_rows)
                    if not (isinstance(data_tool, dict) and data_tool.get("handled")):
                        data_tool = _flowi_split_nav_product_summary(nav_product, max_rows)
                except Exception:
                    logger.info("split_nav inline data fetch failed", exc_info=True)
                    data_tool = None
                has_data = isinstance(data_tool, dict) and data_tool.get("handled") and any(
                    data_tool.get(k) for k in ("table", "split_view", "splittable_view", "rows"))
                if has_data:
                    navigate = dict(nav_tool.get("navigate") or {})
                    navigate["auto"] = False
                    data_tool["navigate"] = navigate
                    data_tool["intent"] = "split_nav"
                    data_tool["unit_ai"] = "split_nav"
                    base_answer = str(data_tool.get("answer") or "").strip()
                    data_tool["answer"] = (base_answer + "\n" if base_answer else "") + \
                        "아래는 조회된 스플릿 데이터입니다. 전체 화면은 SplitTable 열기 버튼을 사용하세요."
                    nav_tool = data_tool
            _finalize_flowi_tool(nav_tool, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)
            answer = nav_tool.get("answer") or "SplitTable 을 엽니다."
            _append_user_event(username, "split_nav", _event_fields(
                {"prompt": prompt, "intent": "split_nav", "feature": "splittable", "answer": answer},
                source=source,
                client_run_id=client_run_id,
            ))
            result = {
                "ok": True,
                "active": True,
                "user": username,
                "answer": answer,
                "tool": nav_tool,
                "llm": {"available": llm_adapter.is_available(), "used": False, "skipped": "deterministic_tool_result"},
                "allowed_features": sorted(allowed_keys),
            }
            if source:
                result["agent_api"] = _agent_api_meta(
                    source=source,
                    client_run_id=client_run_id,
                    username=username,
                    tool=nav_tool,
                    agent_context=agent_context,
                )
            return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    fast_split_tool = _handle_explicit_splittable_view_fast_path(
        prompt,
        product,
        max_rows,
        allowed_keys,
        agent_context,
    )
    if fast_split_tool:
        _finalize_flowi_tool(fast_split_tool, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)
        answer = fast_split_tool.get("answer") or "SplitTable 조회 요청을 처리했습니다."
        _append_user_event(username, fast_split_tool.get("intent") or "splittable_view", _event_fields(
            {
                "prompt": prompt,
                "intent": fast_split_tool.get("intent") or "",
                "feature": fast_split_tool.get("feature") or "splittable",
                "answer": answer,
            },
            source=source,
            client_run_id=client_run_id,
        ))
        result = {
            "ok": True,
            "active": True,
            "user": username,
            "answer": answer,
            "tool": fast_split_tool,
            "llm": {"available": llm_adapter.is_available(), "used": False, "skipped": "deterministic_tool_result"},
            "allowed_features": sorted(allowed_keys),
        }
        if source:
            result["agent_api"] = _agent_api_meta(
                source=source,
                client_run_id=client_run_id,
                username=username,
                tool=fast_split_tool,
                agent_context=agent_context,
            )
        return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    # Human-in-the-loop 티칭 — "기억해: X는 Y" / "잊어줘: X" / "파일 설명: ..." / 지식 카드
    # 관리 명령("지식 채움 수행" 등, 관리자 전용)은 최우선 결정 신호.
    teach_tool = (
        _handle_flowi_teach(prompt, username=username)
        or _handle_file_doc_teach(prompt, username=username)
        or _handle_knowledge_card_admin(prompt, me=me)
    )
    if teach_tool:
        _finalize_flowi_tool(teach_tool, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)
        answer = teach_tool.get("answer") or "학습 요청을 처리했습니다."
        _append_user_event(username, teach_tool.get("intent") or "fewshot_teach", _event_fields(
            {"prompt": prompt, "intent": teach_tool.get("intent") or "", "feature": "fewshot", "answer": answer},
            source=source,
            client_run_id=client_run_id,
        ))
        result = {
            "ok": True,
            "active": True,
            "user": username,
            "answer": answer,
            "tool": teach_tool,
            "llm": {"available": llm_adapter.is_available(), "used": False, "skipped": "deterministic_tool_result"},
            "allowed_features": sorted(allowed_keys),
        }
        if source:
            result["agent_api"] = _agent_api_meta(
                source=source, client_run_id=client_run_id, username=username,
                tool=teach_tool, agent_context=agent_context,
            )
        return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    # 공유 스킬 — "스킬" 언급 + 강한 매칭 또는 스킬 제목이 프롬프트에 그대로
    # 들어 있으면 다른 라우팅보다 먼저 즉시 실행/안내한다 (결정적 신호).
    skill_tool = _handle_shared_skill_request(prompt, username=username, max_rows=max_rows, allowed_keys=allowed_keys)
    if skill_tool:
        _finalize_flowi_tool(skill_tool, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)
        answer = skill_tool.get("answer") or "공유 스킬 요청을 처리했습니다."
        _append_user_event(username, skill_tool.get("intent") or "skill_run", _event_fields(
            {
                "prompt": prompt,
                "intent": skill_tool.get("intent") or "",
                "feature": "skills",
                "skill_key": skill_tool.get("skill_key") or "",
                "answer": answer,
            },
            source=source,
            client_run_id=client_run_id,
        ))
        result = {
            "ok": True,
            "active": True,
            "user": username,
            "answer": answer,
            "tool": skill_tool,
            "llm": {"available": llm_adapter.is_available(), "used": False, "skipped": "deterministic_tool_result"},
            "allowed_features": sorted(allowed_keys),
        }
        if source:
            result["agent_api"] = _agent_api_meta(
                source=source,
                client_run_id=client_run_id,
                username=username,
                tool=skill_tool,
                agent_context=agent_context,
            )
        return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    can_measurement_lookup = bool({"filebrowser", "dashboard"} & set(allowed_keys))
    measurement_tool = _handle_semantic_measurement(prompt, product, max_rows=max_rows) if can_measurement_lookup else None
    if measurement_tool:
        answer = measurement_tool.get("answer") or ""
        _append_user_event(username, measurement_tool.get("intent") or "semantic_measurement_lookup", _event_fields(
            {"prompt": prompt, "intent": measurement_tool.get("intent") or "", "answer": answer},
            source=source,
            client_run_id=client_run_id,
        ))
        result = {
            "ok": True,
            "active": True,
            "user": username,
            "answer": answer,
            "tool": measurement_tool,
            "llm": {"available": llm_adapter.is_available(), "used": False},
            "allowed_features": sorted(allowed_keys),
        }
        if source:
            result["agent_api"] = _agent_api_meta(
                source=source,
                client_run_id=client_run_id,
                username=username,
                tool=measurement_tool,
                agent_context=agent_context,
            )
        return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    admin_block = _flowi_home_admin_function_block(prompt, me)
    if admin_block.get("handled"):
        answer = admin_block["answer"]
        result = {
            "ok": True,
            "active": True,
            "user": username,
            "answer": answer,
            "blocked": True,
            "reject_reason": answer,
            "tool": admin_block,
            "llm": {"available": llm_adapter.is_available(), "used": False, "blocked": True},
            "allowed_features": sorted(allowed_keys),
        }
        return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)
    all_entries = _matched_feature_entrypoints(prompt)
    if all_entries and all_entries[0].get("key") not in allowed_keys:
        tool = _flowi_permission_block(all_entries[0].get("key") or "", me)
        answer = tool["answer"]
        _append_user_event(username, "blocked_permission_request", _event_fields(
            {"prompt": prompt, "feature": tool.get("feature"), "answer": answer},
            source=source,
            client_run_id=client_run_id,
        ))
        result = {
            "ok": True,
            "active": True,
            "user": username,
            "answer": answer,
            "tool": tool,
            "llm": {"available": llm_adapter.is_available(), "used": False, "blocked": True},
            "allowed_features": sorted(allowed_keys),
        }
        if source:
            result["agent_api"] = _agent_api_meta(
                source=source,
                client_run_id=client_run_id,
                username=username,
                tool=tool,
                agent_context=agent_context,
            )
        return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    if _is_rag_update_prompt(prompt):
        if "diagnosis" not in allowed_keys:
            tool = _flowi_permission_block("diagnosis", me)
            answer = tool["answer"]
        elif not allow_rag_update:
            answer = (
                "[flow-i update] 지식 등록은 홈 Flow-i 채팅에서 처리하지 않습니다.\n"
                "에이전트 페이지의 `RAG 반영` 화면에서 문서 타입 지식 등록, 빠른 RAG Update, 표 지식 반영 중 하나로 저장해주세요.\n"
                "홈에서는 일반 질의와 답변 피드백만 받습니다."
            )
            tool = {
                "handled": True,
                "intent": "semiconductor_rag_update",
                "action": "blocked_home_rag_update",
                "blocked": True,
                "answer": answer,
                "feature": "diagnosis",
                "feature_entrypoints": [
                    {"key": "diagnosis", "title": "에이전트", "description": "RAG 반영 화면에서 지식 등록"}
                ],
            }
        else:
            tool = _handle_flowi_rag_update(prompt, me)
            answer = tool.get("answer") or "Flow-i RAG Update를 처리했습니다."
        _append_user_event(username, "semiconductor_rag_update", _event_fields(
            {
                "prompt": prompt,
                "intent": tool.get("intent") or "",
                "action": tool.get("action") or "",
                "answer": answer,
            },
            source=source,
            client_run_id=client_run_id,
        ))
        result = {
            "ok": True,
            "active": True,
            "user": username,
            "answer": answer,
            "tool": tool,
            "llm": {"available": llm_adapter.is_available(), "used": False, "blocked": bool(tool.get("blocked"))},
            "allowed_features": sorted(allowed_keys),
        }
        if source:
            result["agent_api"] = _agent_api_meta(
                source=source,
                client_run_id=client_run_id,
                username=username,
                tool=tool,
                agent_context=agent_context,
            )
        return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    if "dashboard" in allowed_keys:
        refine_tool = _handle_dashboard_chart_refine(prompt, me, agent_context)
        if refine_tool.get("handled"):
            answer = refine_tool.get("answer") or "차트 설정을 수정했습니다."
            _append_user_event(username, "dashboard_chart_refine", _event_fields(
                {"prompt": prompt, "intent": refine_tool.get("intent") or "", "answer": answer},
                source=source,
                client_run_id=client_run_id,
            ))
            result = {
                "ok": True,
                "active": True,
                "user": username,
                "answer": answer,
                "tool": refine_tool,
                "llm": {"available": llm_adapter.is_available(), "used": False},
                "allowed_features": sorted(allowed_keys),
            }
            if source:
                result["agent_api"] = _agent_api_meta(
                    source=source,
                    client_run_id=client_run_id,
                    username=username,
                    tool=refine_tool,
                    agent_context=agent_context,
                )
            return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    if "diagnosis" in allowed_keys:
        prep_tool = _handle_flowi_admin_semiconductor_file_prep(prompt, product, me)
        if prep_tool.get("handled"):
            answer = prep_tool.get("answer") or "반도체 지식/reformatter/YAML 준비 작업을 처리했습니다."
            _append_user_event(username, "semiconductor_admin_file_prep", _event_fields(
                {
                    "prompt": prompt,
                    "intent": prep_tool.get("intent") or "",
                    "action": prep_tool.get("action") or "",
                    "answer": answer,
                },
                source=source,
                client_run_id=client_run_id,
            ))
            result = {
                "ok": True,
                "active": True,
                "user": username,
                "answer": answer,
                "tool": prep_tool,
                "llm": {"available": llm_adapter.is_available(), "used": False, "blocked": False},
                "allowed_features": sorted(allowed_keys),
            }
            if source:
                result["agent_api"] = _agent_api_meta(
                    source=source,
                    client_run_id=client_run_id,
                    username=username,
                    tool=prep_tool,
                    agent_context=agent_context,
            )
            return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    # 인폼 메일 confirm 마커는 LLM function 라우팅(compose/walkthrough)보다 먼저
    # 결정적으로 처리한다 — 마커 프롬프트가 LLM 에 넘어가면 오라우팅된다.
    _inform_mail_payload = _extract_flowi_inform_mail_confirm(prompt)
    if _inform_mail_payload is not None and "inform" in allowed_keys:
        if _inform_mail_payload.get("_parse_error"):
            inform_mail_tool = {"handled": True, "intent": "inform_mail_confirm_failed", "blocked": True, "answer": "메일 확인 payload를 읽지 못했습니다.", "feature": "inform"}
        else:
            inform_mail_tool = _flowi_send_inform_mail_confirmed(_inform_mail_payload, me)
        answer = inform_mail_tool.get("answer") or "인폼 메일 확인을 처리했습니다."
        _append_user_event(username, "inform_mail_confirm", _event_fields(
            {"prompt": prompt, "intent": inform_mail_tool.get("intent") or "", "answer": answer},
            source=source,
            client_run_id=client_run_id,
        ))
        result = {
            "ok": True,
            "active": True,
            "user": username,
            "answer": answer,
            "tool": inform_mail_tool,
            "llm": {"available": llm_adapter.is_available(), "used": False, "blocked": bool(inform_mail_tool.get("blocked"))},
            "allowed_features": sorted(allowed_keys),
        }
        if source:
            result["agent_api"] = _agent_api_meta(
                source=source,
                client_run_id=client_run_id,
                username=username,
                tool=inform_mail_tool,
                agent_context=agent_context,
            )
        return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    walkthrough_tool = _handle_flowi_inform_walkthrough_chat(prompt, product, max_rows, me, agent_context=agent_context, allowed_keys=allowed_keys)
    if walkthrough_tool.get("handled"):
        answer = walkthrough_tool.get("answer") or "인폼 전체 작성 흐름을 진행합니다."
        _append_user_event(username, "inform_walkthrough", _event_fields(
            {"prompt": prompt, "intent": walkthrough_tool.get("intent") or "", "answer": answer},
            source=source,
            client_run_id=client_run_id,
        ))
        result = {
            "ok": True,
            "active": True,
            "user": username,
            "answer": answer,
            "tool": walkthrough_tool,
            "llm": {"available": llm_adapter.is_available(), "used": False, "blocked": bool(walkthrough_tool.get("blocked"))},
            "allowed_features": sorted(allowed_keys),
        }
        if source:
            result["agent_api"] = _agent_api_meta(
                source=source,
                client_run_id=client_run_id,
                username=username,
                tool=walkthrough_tool,
                agent_context=agent_context,
            )
        return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    mail_tool = _handle_compose_inform_module_mail(prompt, product, max_rows, me=me)
    if mail_tool.get("handled"):
        if "inform" not in allowed_keys:
            mail_tool = _flowi_permission_block("inform", me)
        answer = mail_tool.get("answer") or "모듈 인폼 메일 미리보기를 만들었습니다."
        _append_user_event(username, "inform_mail_preview", _event_fields(
            {"prompt": prompt, "intent": mail_tool.get("intent") or "", "answer": answer},
            source=source,
            client_run_id=client_run_id,
        ))
        result = {
            "ok": True,
            "active": True,
            "user": username,
            "answer": answer,
            "tool": mail_tool,
            "llm": {"available": llm_adapter.is_available(), "used": False, "blocked": bool(mail_tool.get("blocked"))},
            "allowed_features": sorted(allowed_keys),
        }
        if source:
            result["agent_api"] = _agent_api_meta(
                source=source,
                client_run_id=client_run_id,
                username=username,
                tool=mail_tool,
                agent_context=agent_context,
            )
        return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    inform_summary_tool = _handle_flowi_inform_summary(prompt, me, max_rows=max_rows, allowed_keys=allowed_keys) if "inform" in allowed_keys else {"handled": False}
    if inform_summary_tool.get("handled"):
        answer = inform_summary_tool.get("answer") or "인폼로그 현황을 정리했습니다."
        _append_user_event(username, "inform_summary", _event_fields(
            {"prompt": prompt, "intent": inform_summary_tool.get("intent") or "", "answer": answer},
            source=source,
            client_run_id=client_run_id,
        ))
        result = {
            "ok": True,
            "active": True,
            "user": username,
            "answer": answer,
            "tool": inform_summary_tool,
            "llm": {"available": llm_adapter.is_available(), "used": False, "blocked": bool(inform_summary_tool.get("blocked"))},
            "allowed_features": sorted(allowed_keys),
        }
        if source:
            result["agent_api"] = _agent_api_meta(
                source=source,
                client_run_id=client_run_id,
                username=username,
                tool=inform_summary_tool,
                agent_context=agent_context,
            )
        return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    inform_draft_tool = _handle_flowi_register_inform_log(prompt, product, max_rows, me, allowed_keys=allowed_keys)
    if inform_draft_tool.get("handled"):
        answer = inform_draft_tool.get("answer") or "인폼 등록 초안을 만들었습니다."
        _append_user_event(username, "inform_log_draft", _event_fields(
            {"prompt": prompt, "intent": inform_draft_tool.get("intent") or "", "answer": answer},
            source=source,
            client_run_id=client_run_id,
        ))
        result = {
            "ok": True,
            "active": True,
            "user": username,
            "answer": answer,
            "tool": inform_draft_tool,
            "llm": {"available": llm_adapter.is_available(), "used": False, "blocked": bool(inform_draft_tool.get("blocked"))},
            "allowed_features": sorted(allowed_keys),
        }
        if source:
            result["agent_api"] = _agent_api_meta(
                source=source,
                client_run_id=client_run_id,
                username=username,
                tool=inform_draft_tool,
                agent_context=agent_context,
            )
        return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    missing_followup_tool = _handle_app_write_missing_followup(prompt, me, agent_context, allowed_keys=allowed_keys)
    if missing_followup_tool.get("handled"):
        answer = missing_followup_tool.get("answer") or "부족한 값을 반영해 등록 요청을 처리했습니다."
        _append_user_event(username, "app_write_missing_followup", _event_fields(
            {
                "prompt": prompt,
                "intent": missing_followup_tool.get("intent") or "",
                "feature": missing_followup_tool.get("feature") or "",
                "answer": answer,
            },
            source=source,
            client_run_id=client_run_id,
        ))
        result = {
            "ok": True,
            "active": True,
            "user": username,
            "answer": answer,
            "tool": missing_followup_tool,
            "llm": {"available": llm_adapter.is_available(), "used": False, "blocked": bool(missing_followup_tool.get("blocked"))},
            "allowed_features": sorted(allowed_keys),
        }
        if source:
            result["agent_api"] = _agent_api_meta(
                source=source,
                client_run_id=client_run_id,
                username=username,
                tool=missing_followup_tool,
                agent_context=agent_context,
            )
        return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    status_tool = _handle_app_write_status_followup(prompt, me, agent_context, allowed_keys=allowed_keys)
    if status_tool.get("handled"):
        answer = status_tool.get("answer") or "직전 등록 상태를 확인했습니다."
        _append_user_event(username, "app_write_status_followup", _event_fields(
            {
                "prompt": prompt,
                "intent": status_tool.get("intent") or "",
                "feature": status_tool.get("feature") or "",
                "answer": answer,
            },
            source=source,
            client_run_id=client_run_id,
        ))
        result = {
            "ok": True,
            "active": True,
            "user": username,
            "answer": answer,
            "tool": status_tool,
            "llm": {"available": llm_adapter.is_available(), "used": False, "blocked": bool(status_tool.get("blocked"))},
            "allowed_features": sorted(allowed_keys),
        }
        if source:
            result["agent_api"] = _agent_api_meta(
                source=source,
                client_run_id=client_run_id,
                username=username,
                tool=status_tool,
                agent_context=agent_context,
            )
        return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    splittable_plan_tool = _handle_splittable_plan_request(prompt, me, allowed_keys=allowed_keys)
    if splittable_plan_tool.get("handled"):
        answer = splittable_plan_tool.get("answer") or "스플릿 테이블 plan 요청을 처리했습니다."
        _append_user_event(username, "splittable_plan", _event_fields(
            {
                "prompt": prompt,
                "intent": splittable_plan_tool.get("intent") or "",
                "feature": splittable_plan_tool.get("feature") or "",
                "answer": answer,
            },
            source=source,
            client_run_id=client_run_id,
        ))
        result = {
            "ok": True,
            "active": True,
            "user": username,
            "answer": answer,
            "tool": splittable_plan_tool,
            "llm": {"available": llm_adapter.is_available(), "used": False, "blocked": bool(splittable_plan_tool.get("blocked"))},
            "allowed_features": sorted(allowed_keys),
        }
        if source:
            result["agent_api"] = _agent_api_meta(
                source=source,
                client_run_id=client_run_id,
                username=username,
                tool=splittable_plan_tool,
                agent_context=agent_context,
            )
        return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    splittable_note_tool = _handle_splittable_note_request(prompt, me, allowed_keys=allowed_keys)
    if splittable_note_tool.get("handled"):
        answer = splittable_note_tool.get("answer") or "스플릿 테이블 꼬리표 요청을 처리했습니다."
        _append_user_event(username, "splittable_lot_note", _event_fields(
            {
                "prompt": prompt,
                "intent": splittable_note_tool.get("intent") or "",
                "feature": splittable_note_tool.get("feature") or "",
                "answer": answer,
            },
            source=source,
            client_run_id=client_run_id,
        ))
        result = {
            "ok": True,
            "active": True,
            "user": username,
            "answer": answer,
            "tool": splittable_note_tool,
            "llm": {"available": llm_adapter.is_available(), "used": False, "blocked": bool(splittable_note_tool.get("blocked"))},
            "allowed_features": sorted(allowed_keys),
        }
        if source:
            result["agent_api"] = _agent_api_meta(
                source=source,
                client_run_id=client_run_id,
                username=username,
                tool=splittable_note_tool,
                agent_context=agent_context,
            )
        return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    draft_tool = _handle_app_write_draft(prompt, me, allowed_keys=allowed_keys)
    if draft_tool.get("handled"):
        answer = draft_tool.get("answer") or "앱 내부 쓰기 작업은 초안 확인이 필요합니다."
        _append_user_event(username, "app_write_draft", _event_fields(
            {
                "prompt": prompt,
                "intent": draft_tool.get("intent") or "",
                "feature": draft_tool.get("feature") or "",
                "answer": answer,
            },
            source=source,
            client_run_id=client_run_id,
        ))
        result = {
            "ok": True,
            "active": True,
            "user": username,
            "answer": answer,
            "tool": draft_tool,
            "llm": {"available": llm_adapter.is_available(), "used": False, "blocked": False},
            "allowed_features": sorted(allowed_keys),
        }
        if source:
            result["agent_api"] = _agent_api_meta(
                source=source,
                client_run_id=client_run_id,
                username=username,
                tool=draft_tool,
                agent_context=agent_context,
            )
        return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    data_tool = _handle_flowi_data_registration(prompt, me)
    if data_tool.get("handled"):
        answer = data_tool.get("answer") or "데이터 등록 요청을 처리했습니다."
        _append_user_event(username, "flowi_data_register", _event_fields(
            {
                "prompt": prompt[:1000],
                "action": data_tool.get("action") or "",
                "requires_confirmation": data_tool.get("requires_confirmation") or False,
                "blocked": data_tool.get("blocked") or False,
                "answer": answer,
            },
            source=source,
            client_run_id=client_run_id,
        ))
        result = {
            "ok": True,
            "active": True,
            "user": username,
            "answer": answer,
            "tool": data_tool,
            "llm": {"available": llm_adapter.is_available(), "used": False, "blocked": bool(data_tool.get("blocked"))},
            "allowed_features": sorted(allowed_keys),
        }
        if source:
            result["agent_api"] = _agent_api_meta(
                source=source,
                client_run_id=client_run_id,
                username=username,
                tool=data_tool,
                agent_context=agent_context,
            )
        return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    if _flowi_write_target_detected(prompt):
        db_blocked = (
            "DB 루트 원본은 admin도 Flow-i에서 수정할 수 없습니다. "
            "수정/등록은 파일탐색기 수정 권한이 있는 사용자만 Files 영역 단일파일에 대해 확인 후 실행됩니다."
        )
        if "db" in str(prompt or "").lower() or "DB" in str(prompt or "") or "원본" in str(prompt or "") or "raw data" in str(prompt or "").lower():
            blocked_msg = db_blocked
            _append_user_event(username, "blocked_write_request", _event_fields(
                {"prompt": prompt, "answer": blocked_msg},
                source=source,
                client_run_id=client_run_id,
            ))
            tool = {
                "handled": True,
                "intent": "blocked_write_request",
                "blocked": True,
                "answer": blocked_msg,
                "policy": FLOWI_READ_ONLY_POLICY,
            }
            result = {
                "ok": True,
                "active": True,
                "user": username,
                "answer": blocked_msg,
                "tool": tool,
                "llm": {"available": llm_adapter.is_available(), "used": False, "blocked": True},
                "allowed_features": sorted(allowed_keys),
            }
            if source:
                result["agent_api"] = _agent_api_meta(
                    source=source,
                    client_run_id=client_run_id,
                    username=username,
                    tool=tool,
                    agent_context=agent_context,
                )
            return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

        if _can_flowi_file_write(me):
            tool = _handle_admin_file_operation(prompt)
            answer = tool.get("answer") or "파일탐색기 관리 작업 요청을 처리했습니다."
            _append_user_event(username, "admin_file_operation", _event_fields(
                {
                    "prompt": prompt,
                    "action": tool.get("action") or "",
                    "requires_confirmation": tool.get("requires_confirmation") or False,
                    "blocked": tool.get("blocked") or False,
                    "answer": answer,
                },
                source=source,
                client_run_id=client_run_id,
            ))
            result = {
                "ok": True,
                "active": True,
                "user": username,
                "answer": answer,
                "tool": tool,
                "llm": {"available": llm_adapter.is_available(), "used": False, "blocked": bool(tool.get("blocked"))},
                "allowed_features": sorted(allowed_keys),
            }
            if source:
                result["agent_api"] = _agent_api_meta(
                    source=source,
                    client_run_id=client_run_id,
                    username=username,
                    tool=tool,
                    agent_context=agent_context,
                )
            return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

        blocked_msg = _flowi_write_block_message(prompt)
        _append_user_event(username, "blocked_write_request", _event_fields(
            {"prompt": prompt, "answer": blocked_msg},
            source=source,
            client_run_id=client_run_id,
        ))
        tool = {
            "handled": True,
            "intent": "blocked_write_request",
            "blocked": True,
            "answer": blocked_msg,
            "policy": FLOWI_READ_ONLY_POLICY,
        }
        result = {
            "ok": True,
            "active": True,
            "user": username,
            "answer": blocked_msg,
            "tool": tool,
            "llm": {"available": llm_adapter.is_available(), "used": False, "blocked": True},
            "allowed_features": sorted(allowed_keys),
        }
        if source:
            result["agent_api"] = _agent_api_meta(
                source=source,
                client_run_id=client_run_id,
                username=username,
                tool=tool,
                agent_context=agent_context,
            )
        return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    max_rows = max(4, min(24, int(max_rows or 12)))
    # M2 strangler-fig: try registered Unit AIs first. Caller is
    # responsible for permission gating via `only=` (e.g. calendar→meeting).
    # Unmigrated units (handle() returns None) fall through to the legacy
    # _handle_flowi_query path below.
    from core.flowi_units import try_dispatch as _try_unit_ai_dispatch
    meeting_allowed = ("meeting" in allowed_keys or "calendar" in allowed_keys)
    unit_only: list[str] = []
    # split_nav 는 "X 스플릿테이블 보여줘" 네비게이션 — step/ppid 조회보다 먼저 본다.
    if "splittable" in allowed_keys:
        unit_only.append("split_nav")
    if "inform" in allowed_keys:
        unit_only.append("inform")
    if meeting_allowed:
        unit_only.append("meeting")
    if "tracker" in allowed_keys:
        unit_only.append("tracker")
    if "dashboard" in allowed_keys:
        unit_only.append("dashboard")
    if "splittable" in allowed_keys:
        unit_only.append("splittable")
    if {"filebrowser", "splittable", "dashboard"} & set(allowed_keys):
        unit_only.append("step_lookup")
    if {"filebrowser", "splittable"} & set(allowed_keys):
        unit_only.append("ppid_knob")
    if "tablemap" in allowed_keys:
        unit_only.append("tablemap")
    if "diagnosis" in allowed_keys:
        unit_only.append("diagnosis")
    # v9.4.x: filebrowser 도 권한 게이트 — 탭 권한 없는 유저의 파일 preview 우회 차단.
    if "filebrowser" in allowed_keys:
        unit_only.append("filebrowser")
    # 지식 기반 기능 선택 — 지식 카드의 answered_by 힌트가 지목한 유닛을 앞으로.
    # 카드 매칭이 없으면 순서는 그대로다 (안정 정렬).
    try:
        from core import knowledge_cards as _knowledge_cards
        unit_only = _knowledge_cards.reorder_units(prompt, unit_only)
    except Exception:
        pass
    unit_tool = _try_unit_ai_dispatch(
        prompt,
        product=product,
        max_rows=max_rows,
        allowed_keys=allowed_keys,
        agent_context=agent_context,
        me=me,
        only=tuple(unit_only),
    )
    if unit_tool is not None and unit_tool.get("low_confidence"):
        # 결정적 유닛의 miss 응답 — 오케스트레이터가 있으면 다른 도구로 답할 기회를 준다.
        try:
            from core import home_orchestrator as _ho
            if _ho.react_available():
                unit_tool = None
        except Exception:
            pass
    react_skip_reason = ""
    if unit_tool is not None:
        tool = unit_tool
    else:
        # LLM(GPT OSS/adapter) ReAct 오케스트레이션 — 도구를 골라 연쇄 실행(유저 최대
        # 8턴, admin 은 연장). 필요 시 ask_user 로 human-in-the-loop 질문. 비활성/실패 시 기존 단일 패스로.
        tool, react_skip_reason = _try_flowi_react_orchestration(prompt, me=me, allowed_keys=allowed_keys)
        if tool is None:
            tool = _handle_flowi_query(
                prompt,
                product,
                max_rows=max_rows,
                allowed_keys=allowed_keys,
                username=username,
                role=str(me.get("role") or "user"),
                agent_context=agent_context,
            )
            # 가이드/링크성 폴백으로 답할 때는 왜 LLM 실행이 아닌지 표면화한다 —
            # 사용자가 "왜 결과 대신 안내가 왔는지" 판단할 수 있게.
            if react_skip_reason and (
                str(tool.get("intent") or "").endswith("_guidance")
                or str(tool.get("action") or "").startswith("open_")
            ):
                notes = {
                    "react_disabled": "LLM 오케스트레이션(ReAct)이 꺼져 있어 기능 안내로 응답했습니다.",
                    "react_error": "LLM 오케스트레이션 실행 중 오류가 발생해 기능 안내로 응답했습니다.",
                    "react_empty": "LLM 오케스트레이션이 결과를 만들지 못해 기능 안내로 응답했습니다.",
                }
                note = notes.get(
                    react_skip_reason,
                    "LLM 호출이 실패해(planner 폴백) 기능 안내로 응답했습니다."
                    if react_skip_reason.startswith("llm_degraded") else "",
                )
                if note:
                    tool.setdefault("warnings", [])
                    if isinstance(tool["warnings"], list):
                        tool["warnings"].append(f"{note} (사유: {react_skip_reason})")
    _schema_search_empty = (
        tool.get("intent") == "filebrowser_schema_search"
        and not ((tool.get("table") or {}).get("rows") or [])
    )
    if not tool.get("handled") or _schema_search_empty:
        # 파일 설명문 기반 최후 검색 + human-in-the-loop 안내 — 다른 라우팅이
        # 처리하지 못했거나 schema 컬럼 검색이 빈손일 때(값 검색 질문) 받는다.
        file_doc_tool = _handle_file_doc_search(prompt, allowed_keys=allowed_keys, username=username)
        if file_doc_tool:
            tool = file_doc_tool
    entries = _matched_feature_entrypoints(prompt, allowed_keys=allowed_keys)
    if entries:
        tool["feature_entrypoints"] = entries
    if not tool.get("handled") and entries:
        tool["answer"] = (
            "질문과 가장 가까운 기능 진입점입니다.\n"
            + "\n".join(f"- {e['title']}: {e['description']}" for e in entries[:3])
        )
    _finalize_flowi_tool(tool, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)
    answer = tool.get("answer") or ""
    llm_info: dict[str, Any] = {"available": llm_adapter.is_available(), "used": False}
    user_ctx = _profile_context(username)
    feature_ctx = _feature_context(prompt, allowed_keys=allowed_keys)
    agent_ctx = _json_excerpt(agent_context) if agent_context else ""
    # 단일 지식 레이어 — LLM(GPT OSS 등) 폴백 프롬프트에 넣을 컴팩트 지식 블록.
    try:
        from core import knowledge_cards as _knowledge_cards
        knowledge_line = _knowledge_cards.prompt_block(prompt)
        knowledge_line = (knowledge_line + "\n\n") if knowledge_line else ""
    except Exception:
        knowledge_line = ""

    skip_llm_polish = _flowi_should_skip_llm_polish(tool)
    if skip_llm_polish:
        llm_info["skipped"] = "deterministic_tool_result"
    chat_over_budget = (datetime.now(timezone.utc) - started_at).total_seconds() > _flowi_chat_deadline_s()
    # The home agent uses the LLM to polish/route, but it must never hang on an
    # endpoint that cannot answer in time: skip enhancement when the breaker is
    # open or the per-turn budget is spent, and return the deterministic result.
    llm_ready = llm_adapter.is_available() and llm_adapter.should_attempt_llm() and not chat_over_budget
    if (not skip_llm_polish) and (not tool.get("blocked")) and (not llm_ready):
        if chat_over_budget:
            llm_info["skipped"] = "deadline_exceeded"
        elif not llm_adapter.is_available():
            llm_info["skipped"] = "llm_unavailable"
        else:
            llm_info["skipped"] = "llm_circuit_open"
            _health_snap = llm_adapter.health_snapshot()
            if _health_snap.get("last_error"):
                llm_info["error"] = _health_snap.get("last_error")
    if llm_ready and not tool.get("blocked") and not skip_llm_polish:
        source_line = f"외부 AI source: {source}\nclient_run_id: {client_run_id}\n" if source else ""
        context_line = f"외부 AI 입력 context JSON: {agent_ctx}\n\n" if agent_ctx else ""
        if tool.get("handled"):
            polish_prompt = _flowi_llm_polish_prompt(prompt, tool)
            if source_line or context_line:
                polish_prompt += "\n\n" + source_line + context_line
            polish_system = (
                "Flow-i 응답 문장 정리기입니다. 서버 결과를 다시 판단하지 말고 plain text만 출력합니다. "
                "이전 context는 후속 보강 해석용이며 tool/cache 결과에 없는 값을 만들지 않습니다."
            )
        else:
            polish_prompt = (
                "당신은 반도체 fab 데이터 Flowi assistant입니다. "
                "사용자 정보와 단위기능 진입점 설명을 바탕으로 가장 좋은 화면/다음 행동을 먼저 추천하세요. "
                "Roo Code/OpenCode 계열 오픈소스 모델처럼 추론 성능이 제한적일 수 있으므로, "
                "복잡한 계획보다 필요한 조건과 다음 화면을 짧게 답하세요. "
                "지원 범위가 불확실하면 필요한 lot/step/item 조건을 3개 이하 선택지로 물어보세요. "
                f"{FLOWI_PLAIN_TEXT_OUTPUT_RULE}\n\n"
                f"{source_line}"
                f"{context_line}"
                f"{knowledge_line}"
                f"사용자 정보 Markdown:\n{user_ctx or '(없음)'}\n\n"
                f"단위기능 진입점:\n{feature_ctx}\n\n"
                f"사용자: {prompt}"
            )
            polish_system = _flowi_system_prompt()
        out = llm_adapter.complete(
            polish_prompt,
            system=polish_system,
            timeout=12,
        )
        if out.get("ok") and out.get("text"):
            parsed_public = _flowi_parse_public_polish_text(out.get("text")) if tool.get("handled") else {}
            polished = parsed_public.get("final_answer") if parsed_public else ""
            if polished:
                public_summary = parsed_public.get("summary") if isinstance(parsed_public.get("summary"), list) else []
                if public_summary:
                    llm_info["public_summary"] = public_summary[:6]
            else:
                polished = (
                    _flowi_validate_llm_polish_text(out.get("text"))
                    if tool.get("handled")
                    else _flowi_plain_answer_text(out.get("text"))
                )
            if polished:
                answer = polished
                llm_info["used"] = True
            else:
                llm_info.update({
                    "used": False,
                    "error": "polish_format_violation",
                    "fallback": "deterministic_answer",
                })
        elif out.get("error"):
            llm_info["error"] = out.get("error")

    if not str(answer or "").strip() and not llm_info.get("used"):
        # No deterministic answer and the LLM could not produce one — tell the
        # user plainly instead of returning an empty bubble or hanging.
        if not llm_adapter.is_available():
            answer = "LLM이 연결되어 있지 않습니다. 관리 > 진단에서 LLM 연결을 설정한 뒤 다시 시도해 주세요."
        elif not llm_adapter.should_attempt_llm():
            _reason = (llm_adapter.health_snapshot().get("last_error") or "최근 LLM 호출이 실패했습니다")
            answer = f"LLM에 연결하지 못해 답변을 생성하지 못했습니다. ({_reason}) 연결 상태를 확인한 뒤 다시 시도해 주세요."
        else:
            answer = "지금은 답변을 생성하지 못했습니다. 잠시 후 다시 시도해 주세요."
        tool["answer"] = answer
        llm_info.setdefault("skipped", "llm_unavailable")

    retrieved_ids = _flowi_tool_retrieved_ids(tool)
    system_knowledge_ids = [item.get("id") for item in _flowi_promoted_knowledge_items() if item.get("id")]
    elapsed_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
    _append_user_event(username, "chat", _event_fields(
        {
            "prompt": prompt,
            "intent": tool.get("intent") or "",
            "selected_function": tool.get("action") or tool.get("intent") or "",
            "retrieved_ids": retrieved_ids,
            "system_knowledge_ids": system_knowledge_ids,
            "retrieval_score": _flowi_tool_retrieval_score(tool),
            "result_status": _flowi_result_status(tool, llm_info),
            "elapsed_ms": elapsed_ms,
            "llm_used": llm_info.get("used"),
            "answer": answer,
        },
        source=source,
        client_run_id=client_run_id,
    ))
    result = {
        "ok": True,
        "active": True,
        "user": username,
        "answer": answer,
        "tool": tool,
        "llm": llm_info,
        "allowed_features": sorted(allowed_keys),
    }
    if source:
        result["agent_api"] = _agent_api_meta(
            source=source,
            client_run_id=client_run_id,
            username=username,
            tool=tool,
            agent_context=agent_context,
        )
    return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)


def _flowi_should_skip_llm_polish(tool: dict[str, Any]) -> bool:
    """Keep local chart/tablemap results fast and avoid delaying visible payloads."""
    intent = str(tool.get("intent") or "")
    action = str(tool.get("action") or "")
    table = tool.get("table") if isinstance(tool.get("table"), dict) else {}
    if action.startswith("clarify_") or tool.get("needs_input") or tool.get("pending_prompt"):
        return True
    if isinstance(tool.get("clarification"), dict) and tool.get("clarification"):
        return True
    if tool.get("missing") or tool.get("missing_freetext") or tool.get("arguments_choices"):
        return True
    if intent.endswith("_guidance") or action == "flowi.feature.guidance" or table.get("kind") == "flowi_action_plan":
        return True
    if intent in {"current_fab_lot_lookup", "fab_current_location_lookup"}:
        return True
    if intent.startswith("dashboard_") or intent == "tablemap_guidance":
        return True
    if intent == "meeting_recall_summary":
        return True
    if intent == "knowledge_impact_context":
        return True
    if intent == "inform_lot_module_summary":
        return True
    if intent.startswith("inform_"):
        return True
    if intent in {"lot_knobs", "splittable_context_followup", "splittable_view", "splittable_plan_mismatch", "wafer_split_at_step", "knob_value_lot_search", "metric_at_step_lookup", "fab_progress_lookup", "lot_current_step_lookup", "step_mapping_lookup", "knob_rulebook_lookup", "tracker_lot_purpose_lookup", "filebrowser_data_preview", "filebrowser_schema_search", "filebrowser_multisource_join", "dashboard_multisource_chart"}:
        return True
    if isinstance(tool.get("chart_result"), dict):
        return True
    return False


ERROR_EXPLAIN_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "where": {"type": "string"},
        "cause": {"type": "string"},
        "how_to_fix": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "where", "cause", "how_to_fix"],
}
