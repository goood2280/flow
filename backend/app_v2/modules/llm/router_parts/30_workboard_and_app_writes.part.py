def _handle_splittable_plan_request(prompt: str, me: dict[str, Any], allowed_keys: set[str] | None = None) -> dict[str, Any]:
    payload = _extract_flowi_splittable_plan_payload(prompt)
    if payload is None and not _flowi_splittable_plan_intent(prompt):
        return {"handled": False}
    if allowed_keys is not None and "splittable" not in allowed_keys:
        return _flowi_permission_block("splittable", me)
    if payload is not None:
        if payload.get("_parse_error"):
            raise HTTPException(400, payload.get("_parse_error"))
        product = _flowi_splittable_product_id(payload.get("product") or "")
        root_lot_id = _upper(payload.get("root_lot_id") or "")
        plans = payload.get("plans") if isinstance(payload.get("plans"), dict) else {}
        expected = _flowi_splittable_plan_confirm_text(product, root_lot_id, str(payload.get("knob") or ""), plans)
        if str(payload.get("confirm") or "").strip() != expected:
            payload = {**payload, "product": product, "root_lot_id": root_lot_id, "confirm": expected, "username": me.get("username") or "user"}
            return _flowi_splittable_plan_confirmation(payload, "SplitTable plan 저장 전 확인이 필요합니다.")
        try:
            saved = _save_flowi_splittable_plan({**payload, "product": product, "root_lot_id": root_lot_id, "username": me.get("username") or payload.get("username") or "user"})
        except Exception as e:
            return {
                "handled": True,
                "intent": "splittable_plan_failed",
                "action": "save_splittable_plan",
                "blocked": True,
                "answer": f"SplitTable plan 저장 중 오류가 발생했습니다: {e}",
                "feature": "splittable",
            }
        rows = [
            {"field": "status", "value": "saved"},
            {"field": "product", "value": product},
            {"field": "root_lot_id", "value": root_lot_id},
            {"field": "knob", "value": str(payload.get("knob") or "")},
            {"field": "saved_cells", "value": str(saved.get("saved") or len(plans))},
            {"field": "wafer_policy", "value": f"wafer_id 1~{FLOWI_MAX_WAFER_ID}만 저장"},
        ]
        return {
            "handled": True,
            "intent": "splittable_plan_saved",
            "action": "save_splittable_plan",
            "answer": f"SplitTable plan을 저장했습니다.\n- product: {product}\n- lot: {root_lot_id}\n- KNOB: {payload.get('knob')}\n- 저장 cell: {saved.get('saved') or len(plans)}",
            "feature": "splittable",
            "created_record": {"id": f"{product}:{root_lot_id}:{payload.get('knob')}", "feature": "splittable", "title": "plan saved", "target": root_lot_id},
            "table": _flowi_plan_table(rows, title="SplitTable plan saved"),
        }
    draft, missing_tool = _flowi_build_splittable_plan_payload(prompt, me)
    if missing_tool:
        return missing_tool
    if not draft:
        return {"handled": False}
    expected = _flowi_splittable_plan_confirm_text(draft["product"], draft["root_lot_id"], draft["knob"], draft["plans"])
    return _flowi_splittable_plan_confirmation({**draft, "confirm": expected}, "SplitTable plan 저장 준비가 됐습니다. 확인 선택을 누르면 실제 plan 저장소에 반영합니다.")


def _flowi_splittable_plan_confirmation(payload: dict[str, Any], answer: str) -> dict[str, Any]:
    product = _flowi_splittable_product_id(payload.get("product") or "")
    root_lot_id = _upper(payload.get("root_lot_id") or "")
    plans = payload.get("plans") if isinstance(payload.get("plans"), dict) else {}
    rows = [
        {"field": "status", "value": "confirmation_required"},
        {"field": "product", "value": product},
        {"field": "root_lot_id", "value": root_lot_id},
        {"field": "knob", "value": str(payload.get("knob") or "")},
        {"field": "plan_cells", "value": str(len(plans))},
        {"field": "assignments", "value": "; ".join(payload.get("summary") or [])},
        {"field": "wafer_policy", "value": f"wafer_id 1~{FLOWI_MAX_WAFER_ID}만 반영"},
    ]
    if payload.get("invalid_wafers"):
        rows.append({"field": "ignored_wafers", "value": ", ".join(payload.get("invalid_wafers") or [])})
    return {
        "handled": True,
        "intent": "splittable_plan_confirm",
        "action": "confirm_splittable_plan",
        "requires_confirmation": True,
        "answer": answer,
        "feature": "splittable",
        "slots": {"product": product, "lots": [root_lot_id], "wafers": sorted({str(k).split("|")[1] for k in plans if "|" in str(k)}, key=lambda x: int(x))},
        "clarification": {
            "question": "이 SplitTable plan을 저장할까요?",
            "choices": [{
                "id": "confirm_splittable_plan",
                "label": "1",
                "title": "plan 저장",
                "recommended": True,
                "description": f"{product} / {root_lot_id} / {payload.get('knob')} {len(plans)} cells",
                "prompt": f"{_FLOWI_SPLITTABLE_PLAN_MARKER} {json.dumps(payload, ensure_ascii=False)}",
            }, {
                "id": "open_splittable",
                "label": "2",
                "title": "SplitTable에서 확인",
                "tab": "splittable",
                "description": "화면에서 lot과 KNOB row를 직접 확인합니다.",
                "prompt": "스플릿 테이블 열기",
            }, {
                "id": "cancel_splittable_plan",
                "label": "3",
                "title": "취소",
                "description": "plan을 저장하지 않습니다.",
                "prompt": "SplitTable plan 저장 취소",
            }],
        },
        "table": _flowi_plan_table(rows),
    }


def _handle_splittable_note_request(prompt: str, me: dict[str, Any], allowed_keys: set[str] | None = None) -> dict[str, Any]:
    payload = _extract_flowi_splittable_note_payload(prompt)
    if payload is None and not _flowi_splittable_note_intent(prompt):
        return {"handled": False}
    if allowed_keys is not None and "splittable" not in allowed_keys:
        return _flowi_permission_block("splittable", me)
    if payload is not None:
        if payload.get("_parse_error"):
            raise HTTPException(400, payload.get("_parse_error"))
        product = _flowi_splittable_product_id(payload.get("product") or "")
        root_lot_id = _upper(payload.get("root_lot_id") or "")
        note_text = str(payload.get("text") or "").strip()
        scope = str(payload.get("scope") or "lot").strip() or "lot"
        wafer_ids = [str(w).strip() for w in (payload.get("wafer_ids") or []) if str(w).strip()]
        expected = _flowi_splittable_note_confirm_text(product, root_lot_id, note_text, scope, wafer_ids)
        if str(payload.get("confirm") or "").strip() != expected:
            return {
                "handled": True,
                "intent": "splittable_lot_note_confirm",
                "action": "confirm_splittable_lot_note",
                "requires_confirmation": True,
                "answer": "스플릿 테이블 꼬리표 등록 전 확인이 필요합니다.",
                "feature": "splittable",
                "clarification": {
                    "question": "이 꼬리표를 스플릿 테이블 lot에 등록할까요?",
                    "choices": [{
                        "id": "confirm_splittable_note",
                        "label": "1",
                        "title": "꼬리표 등록",
                        "recommended": True,
                        "description": f"{product} / {root_lot_id}{' #' + ',#'.join(wafer_ids) if wafer_ids else ''}에 `{note_text[:80]}` 등록",
                        "prompt": f"{_FLOWI_SPLITTABLE_NOTE_MARKER} {json.dumps({**payload, 'product': product, 'root_lot_id': root_lot_id, 'confirm': expected}, ensure_ascii=False)}",
                    }, {
                        "id": "cancel_splittable_note",
                        "label": "2",
                        "title": "취소",
                        "description": "꼬리표를 등록하지 않습니다.",
                        "prompt": "스플릿 테이블 꼬리표 등록 취소",
                    }],
                },
                "table": _flowi_splittable_note_table([
                    {"field": "status", "value": "confirmation_required"},
                    {"field": "product", "value": product},
                    {"field": "root_lot_id", "value": root_lot_id},
                    {"field": "scope", "value": scope},
                    {"field": "wafer_ids", "value": ", ".join(wafer_ids)},
                    {"field": "note", "value": note_text},
                ]),
            }
        try:
            entry = _save_flowi_splittable_note({**payload, "product": product, "root_lot_id": root_lot_id, "scope": scope, "wafer_ids": wafer_ids})
        except Exception as e:
            return {
                "handled": True,
                "intent": "splittable_lot_note_failed",
                "action": "create_splittable_lot_note",
                "blocked": True,
                "answer": f"스플릿 테이블 꼬리표 등록 중 오류가 발생했습니다: {e}",
                "feature": "splittable",
            }
        return {
            "handled": True,
            "intent": "splittable_lot_note_create",
            "action": "create_splittable_lot_note",
            "answer": f"스플릿 테이블 꼬리표를 등록했습니다.\n- product: {product}\n- lot: {root_lot_id}\n- 내용: {entry.get('text')}",
            "feature": "splittable",
            "created_record": {"id": entry.get("id") or "", "feature": "splittable", "title": entry.get("text") or "", "target": root_lot_id},
            "table": _flowi_splittable_note_table([
                {"field": "status", "value": "created"},
                {"field": "id", "value": entry.get("id") or ""},
                {"field": "product", "value": product},
                {"field": "root_lot_id", "value": root_lot_id},
                {"field": "scope", "value": entry.get("scope") or ""},
                {"field": "wafer_id", "value": entry.get("wafer_id") or ""},
                {"field": "note", "value": entry.get("text") or ""},
            ]),
        }

    draft, missing_tool = _flowi_splittable_note_payload(prompt, me)
    if missing_tool:
        return missing_tool
    if not draft:
        return {"handled": False}
    expected = _flowi_splittable_note_confirm_text(draft["product"], draft["root_lot_id"], draft["text"], draft.get("scope") or "lot", draft.get("wafer_ids") or [])
    confirm_payload = {**draft, "confirm": expected}
    return {
        "handled": True,
        "intent": "splittable_lot_note_create_draft",
        "action": "confirm_splittable_lot_note",
        "requires_confirmation": True,
        "answer": "스플릿 테이블 lot 꼬리표 등록 준비가 됐습니다. 확인 선택을 누르면 실제로 등록합니다.",
        "feature": "splittable",
        "slots": {"product": draft["product"], "lots": [draft["root_lot_id"]], "wafers": draft.get("wafer_ids") or []},
        "clarification": {
            "question": "이 꼬리표를 스플릿 테이블 lot에 등록할까요?",
            "choices": [{
                "id": "confirm_splittable_note",
                "label": "1",
                "title": "꼬리표 등록",
                "recommended": True,
                "description": f"{draft['product']} / {draft['root_lot_id']}{' #' + ',#'.join(draft.get('wafer_ids') or []) if draft.get('wafer_ids') else ''}에 `{draft['text'][:80]}` 등록",
                "prompt": f"{_FLOWI_SPLITTABLE_NOTE_MARKER} {json.dumps(confirm_payload, ensure_ascii=False)}",
            }, {
                "id": "cancel_splittable_note",
                "label": "2",
                "title": "취소",
                "description": "꼬리표를 등록하지 않습니다.",
                "prompt": "스플릿 테이블 꼬리표 등록 취소",
            }],
        },
        "table": _flowi_splittable_note_table([
            {"field": "status", "value": "draft_ready"},
            {"field": "product", "value": draft["product"]},
            {"field": "root_lot_id", "value": draft["root_lot_id"]},
            {"field": "scope", "value": draft.get("scope") or "lot"},
            {"field": "wafer_ids", "value": ", ".join(draft.get("wafer_ids") or [])},
            {"field": "note", "value": draft["text"]},
            {"field": "policy", "value": "스플릿 테이블 권한이 있는 사용자는 lot 꼬리표를 확인 후 등록할 수 있습니다. DB/Files 원본은 수정하지 않습니다."},
        ]),
    }


def _flowi_prompt_tracker_category_match(prompt: str, cat_names: list[str]) -> tuple[str, bool]:
    names = [str(c or "").strip() for c in (cat_names or []) if str(c or "").strip()]
    if not names:
        return "General", False
    text = str(prompt or "")
    low = text.lower()
    for name in names:
        if name.lower() in low:
            return name, True

    def by_role(role: str) -> str:
        for name in names:
            if name.lower() == role:
                return name
        for name in names:
            if role in name.lower():
                return name
        return ""

    if any(term in low or term in text for term in ("analysis", "분석", "해석")):
        matched = by_role("analysis")
        if matched:
            return matched, True
    if any(term in low or term in text for term in ("monitor", "monitoring", "모니터", "모니터링", "감시")):
        matched = by_role("monitor")
        if matched:
            return matched, True
    return names[0], False


def _flowi_prompt_tracker_category(prompt: str, cat_names: list[str]) -> str:
    return _flowi_prompt_tracker_category_match(prompt, cat_names)[0]


def _flowi_prompt_date(prompt: str) -> str:
    text = str(prompt or "")
    today = datetime.now().date()
    m = re.search(r"\b(20\d{2})[-./](\d{1,2})[-./](\d{1,2})\b", text)
    if m:
        y, mo, d = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        try:
            return datetime(y, mo, d).date().isoformat()
        except Exception:
            return ""
    m = re.search(r"(?<!\d)(\d{1,2})[-./](\d{1,2})(?!\d)", text)
    if m:
        try:
            return datetime(today.year, int(m.group(1)), int(m.group(2))).date().isoformat()
        except Exception:
            return ""
    if "모레" in text:
        return (today + timedelta(days=2)).isoformat()
    if "내일" in text:
        return (today + timedelta(days=1)).isoformat()
    if "오늘" in text:
        return today.isoformat()
    return ""


def _flowi_app_write_missing(
    feature: str,
    missing: list[str],
    prompt: str,
    product: str,
    lots: list[str],
    wafers: list[str],
    *,
    choices: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    title = _feature_title(feature)
    choice_rows = choices if choices else []
    tool = {
        "handled": True,
        "intent": f"{feature}_create_needs_context",
        "action": "collect_required_fields",
        "answer": f"{title} 등록에 필요한 조건이 부족합니다. 추가로 필요한 값: {', '.join(missing)}",
        "feature": feature,
        "missing": missing,
        "missing_freetext": _flowi_missing_freetext(missing),
        "arguments_choices": _flowi_arguments_choices(missing, prompt, {"product": product, "lot_ids": lots, "wafer_ids": wafers}),
        "last_partial_prompt": prompt,
        "pending_prompt": prompt,
        "slots": {"product": product, "lots": lots, "wafers": wafers},
        "table": {
            "kind": "flowi_app_write_missing",
            "title": "Registration needs more context",
            "placement": "below",
            "columns": _table_columns(["field", "value"]),
            "rows": [
                {"field": "requested_feature", "value": feature},
                {"field": "missing", "value": ", ".join(missing)},
                {"field": "prompt", "value": prompt[:500]},
            ],
            "total": 3,
        },
    }
    if choice_rows:
        tool["clarification"] = {
            "question": f"{title} 등록을 계속하려면 {', '.join(missing)} 값을 알려주세요.",
            "choices": choice_rows[:3],
        }
    return tool


def _flowi_app_create_missing(feature: str, prompt: str, product: str, lots: list[str], wafers: list[str]) -> tuple[list[str], list[dict[str, Any]]]:
    missing: list[str] = []
    choices: list[dict[str, Any]] = []
    if feature == "tracker":
        from routers import tracker as tracker_router
        cat_names = tracker_router._cat_names()
        _, category_explicit = _flowi_prompt_tracker_category_match(prompt, cat_names)
        if not category_explicit:
            missing.append("category")
            for i, name in enumerate(cat_names[:3], start=1):
                choices.append({
                    "id": f"category_{name}",
                    "label": str(i),
                    "title": name,
                    "recommended": i == 1,
                    "description": f"이슈 카테고리를 {name}(으)로 선택하고 등록을 이어갑니다.",
                    "prompt": f"category: {name}",
                })
        if not product:
            missing.append("product")
        if not lots and not wafers:
            missing.append("lot_id 또는 wafer_id")
    elif feature == "inform":
        if not lots and not wafers:
            missing.append("lot_id 또는 wafer_id")
        if not _flowi_prompt_inform_text(prompt) and not _flowi_prompt_content(prompt):
            missing.append("인폼 내용")
    elif feature == "meeting":
        title = _flowi_prompt_title(prompt, feature)
        if not title or title == f"{_feature_title(feature)} 자동 등록":
            missing.append("회의 제목")
        scheduled_at, recurrence = _flowi_prompt_meeting_schedule(prompt)
        if not scheduled_at and (recurrence or {}).get("type") == "none":
            missing.append("회의 일시 또는 반복 조건")
    elif feature == "calendar":
        if not _flowi_prompt_date(prompt):
            missing.append("date")
        title = _flowi_prompt_title(prompt, feature)
        if not title or title == f"{_feature_title(feature)} 자동 등록":
            missing.append("일정 제목")
    return missing, choices


def _flowi_create_app_record(feature: str, prompt: str, me: dict[str, Any], product: str, lots: list[str], wafers: list[str]) -> dict[str, Any]:
    username = me.get("username") or "user"
    title = _flowi_prompt_title(prompt, feature)
    now_s = datetime.now(timezone.utc).isoformat()
    if feature == "inform":
        if not (lots or wafers):
            return _flowi_app_write_missing(feature, ["lot_id 또는 wafer_id"], prompt, product, lots, wafers)
        from routers import informs as informs_router
        lot = lots[0] if lots else ""
        wafer = wafers[0] if wafers else lot
        module = _flowi_prompt_field(prompt, ("module", "모듈")) or ""
        inform_text = _flowi_prompt_inform_text(prompt) or str(prompt or "").strip()
        reason = _flowi_prompt_field(prompt, ("reason", "사유")) or inform_text[:80] or "Flow-i 등록"
        now = informs_router._now()
        root_lot = informs_router._root_lot_from_values(lot)
        fab_snapshot = informs_router._resolve_fab_lot_snapshot(product, lot, wafer)
        entry = {
            "id": informs_router._new_id(),
            "parent_id": None,
            "wafer_id": wafer,
            "lot_id": lot,
            "root_lot_id": root_lot,
            "product": product,
            "module": module,
            "reason": reason,
            "text": inform_text,
            "author": username,
            "created_at": now,
            "checked": False,
            "checked_by": "",
            "checked_at": "",
            "flow_status": "received",
            "status_history": [{"status": "received", "actor": username, "at": now, "note": "created by Flow-i"}],
            "splittable_change": None,
            "images": [],
            "embed_table": None,
            "auto_generated": False,
            "group_ids": [],
            "fab_lot_id_at_save": fab_snapshot,
        }
        items = informs_router._load_upgraded()
        items.append(entry)
        informs_router._save(items)
        record = {"id": entry["id"], "title": reason, "feature": "inform", "target": lot or wafer}
        answer = f"인폼을 바로 등록했습니다.\n- id: {entry['id']}\n- lot/wafer: {lot or '-'} / {wafer or '-'}\n- 내용: {inform_text[:80] or '-'}"
    elif feature == "tracker":
        from routers import tracker as tracker_router
        from core.tracker_schema import normalize_lot_row
        cat_names = tracker_router._cat_names()
        category = _flowi_prompt_tracker_category(prompt, cat_names)
        issue_id = f"ISS-{datetime.now().strftime('%y%m%d')}-{uuid.uuid4().hex[:4].upper()}"
        lot_rows = []
        for lot in lots[:20]:
            root_lot_id = lot if len(lot) == 5 and _is_mixed_alnum_token(lot) else ""
            lot_rows.append(normalize_lot_row({
                "product": product,
                "lot_id": "" if root_lot_id else lot,
                "root_lot_id": root_lot_id,
                "wafer_id": wafers[0] if wafers else "",
                "username": username,
                "added": now_s,
            }))
        result = tracker_router.TRACKER_SERVICE.create_legacy_issue(
            issue_id=issue_id,
            title=title,
            description=_flowi_prompt_content(prompt) or str(prompt or "").strip(),
            username=username,
            status="in_progress",
            priority="normal",
            category=category,
            links=[],
            images=[],
            lots=lot_rows,
            group_ids=[],
        )
        if not result.ok:
            raise RuntimeError(result.error)
        record = {"id": issue_id, "title": title, "feature": "tracker", "target": category}
        answer = f"이슈를 바로 등록했습니다.\n- id: {issue_id}\n- category: {category}\n- title: {title}"
    elif feature == "meeting":
        from routers import meetings as meetings_router
        now = meetings_router._now()
        scheduled_at, recurrence = _flowi_prompt_meeting_schedule(prompt)
        first_session = {
            "id": meetings_router._new_sid(),
            "idx": 1,
            "scheduled_at": scheduled_at,
            "status": "scheduled",
            "agendas": [],
            "minutes": None,
            "created_at": now,
            "updated_at": now,
        }
        items = meetings_router._load()
        used_colors = {m.get("color") for m in items if isinstance(m, dict) and m.get("color")}
        palette = getattr(meetings_router, "MEETING_PALETTE", ["#3b82f6"])
        color = ""
        for i in range(len(palette)):
            cand = palette[(len(items) + i) % len(palette)]
            if cand not in used_colors:
                color = cand
                break
        if not color:
            color = palette[len(items) % len(palette)]
        entry = {
            "id": meetings_router._new_mid(),
            "title": title,
            "owner": username,
            "recurrence": meetings_router._normalize_recurrence(recurrence),
            "status": "active",
            "color": color,
            "sessions": [first_session],
            "created_by": username,
            "created_at": now,
            "updated_at": now,
            "group_ids": [],
        }
        result = meetings_router.MEETING_SERVICE.create_meeting(entry)
        if not result.ok:
            raise RuntimeError(result.error)
        rec_summary = entry["recurrence"].get("type") or "none"
        if entry["recurrence"].get("weekday"):
            rec_summary += f" / weekday={','.join(str(x) for x in entry['recurrence']['weekday'])}"
        record = {
            "id": entry["id"],
            "title": title,
            "feature": "meeting",
            "target": username,
            "scheduled_at": scheduled_at,
            "recurrence": rec_summary,
        }
        answer = f"회의를 바로 등록했습니다.\n- id: {entry['id']}\n- title: {title}"
        if scheduled_at:
            answer += f"\n- 1차 일시: {scheduled_at}"
        if rec_summary != "none":
            answer += f"\n- 반복: {rec_summary}"
    elif feature == "calendar":
        date_s = _flowi_prompt_date(prompt)
        if not date_s:
            return _flowi_app_write_missing(feature, ["date"], prompt, product, lots, wafers)
        from routers import calendar as calendar_router
        now = calendar_router._now_iso()
        entry = {
            "id": calendar_router._new_id(),
            "version": 1,
            "date": date_s,
            "end_date": "",
            "title": title,
            "body": str(prompt or "").strip(),
            "category": "",
            "author": username,
            "source_type": "manual",
            "meeting_ref": None,
            "status": "pending",
            "created_at": now,
            "updated_at": now,
            "history": [],
            "group_ids": [],
        }
        items = calendar_router._load_events()
        items.append(entry)
        calendar_router._save_events(items)
        record = {"id": entry["id"], "title": title, "feature": "calendar", "target": date_s}
        answer = f"일정을 바로 등록했습니다.\n- id: {entry['id']}\n- date: {date_s}\n- title: {title}"
    else:
        return {"handled": False}
    table_rows = [
        {"field": "status", "value": "created"},
        {"field": "feature", "value": feature},
        {"field": "id", "value": record.get("id") or ""},
        {"field": "title", "value": record.get("title") or ""},
        {"field": "target", "value": record.get("target") or ""},
    ]
    for key in ("scheduled_at", "recurrence"):
        if record.get(key):
            table_rows.append({"field": key, "value": record.get(key) or ""})
    return {
        "handled": True,
        "intent": f"{feature}_create",
        "action": "create_app_record",
        "answer": answer,
        "feature": feature,
        "created_record": record,
        "feature_entrypoints": [item for item in FLOWI_FEATURE_ENTRYPOINTS if item["key"] == feature],
        "slots": {"product": product, "lots": lots, "wafers": wafers},
        "table": {
            "kind": "flowi_app_write_created",
            "title": "Created app record",
            "placement": "below",
            "columns": _table_columns(["field", "value"]),
            "rows": table_rows,
            "total": len(table_rows),
        },
    }


def _handle_app_write_draft(prompt: str, me: dict[str, Any], allowed_keys: set[str] | None = None) -> dict[str, Any]:
    feature = _detect_app_write_feature(prompt)
    if not feature:
        return {"handled": False}
    target_feature = "tracker" if feature == "annotation" else feature
    if allowed_keys is not None and target_feature not in allowed_keys and feature != "annotation":
        return _flowi_permission_block(target_feature, me)
    lots = _lot_tokens(prompt)
    wafers = _wafer_tokens(prompt)
    product = _product_hint(prompt)
    mode = _flowi_app_write_mode(prompt)
    if mode == "create" and feature != "annotation" and target_feature in {"inform", "tracker", "meeting", "calendar"}:
        try:
            missing, choices = _flowi_app_create_missing(target_feature, prompt, product, lots, wafers)
            if missing:
                return _flowi_app_write_missing(target_feature, missing, prompt, product, lots, wafers, choices=choices)
            return _flowi_create_app_record(target_feature, prompt, me, product, lots, wafers)
        except Exception as e:
            logger.warning("flowi app create failed: %s", e)
            return {
                "handled": True,
                "intent": f"{target_feature}_create_failed",
                "action": "create_app_record",
                "blocked": True,
                "answer": f"{_feature_title(target_feature)} 등록 중 오류가 발생했습니다. 관련 화면에서 직접 확인해주세요: {e}",
                "feature": target_feature,
                "slots": {"product": product, "lots": lots, "wafers": wafers},
            }
    action_by_feature = {
        "inform": "inform_create_draft",
        "tracker": "tracker_issue_create_draft",
        "meeting": "meeting_write_draft",
        "calendar": "calendar_event_create_draft",
        "splittable": "splittable_plan_update_draft",
        "annotation": "lot_wafer_annotation_draft",
    }
    rows = [
        {"field": "status", "value": "draft_confirmation_required"},
        {"field": "requested_feature", "value": feature},
        {"field": "detected_product", "value": product or ""},
        {"field": "detected_lot", "value": ", ".join(lots)},
        {"field": "detected_wafer", "value": ", ".join(wafers)},
        {"field": "prompt", "value": prompt[:500]},
        {"field": "policy", "value": "신규 등록은 확실하면 바로 실행합니다. 수정/삭제/상태 변경은 권한 확인과 사전 확인 후 실행해야 합니다."},
    ]
    answer = (
        "이 요청은 기존 기록의 수정/변경 또는 권한 확인이 필요한 작업입니다. "
        "변경 전에는 반드시 대상 화면에서 권한과 내용을 확인해야 합니다. "
        "원본 DB/Files는 수정하지 않습니다."
    )
    return {
        "handled": True,
        "intent": action_by_feature.get(feature, "app_write_draft"),
        "action": "draft_confirm_required",
        "requires_confirmation": True,
        "answer": answer,
        "feature": target_feature,
        "slots": {"product": product, "lots": lots, "wafers": wafers},
        "clarification": {
            "question": "이 작업은 실제 저장 전에 전용 초안 화면/확인 명령이 필요합니다.",
            "choices": [
                {
                    "id": "open_feature",
                    "label": "1",
                    "title": f"{_feature_title(target_feature)} 열기",
                    "tab": target_feature,
                    "recommended": True,
                    "description": "관련 화면에서 현재 조건을 확인한 뒤 수동 저장합니다.",
                    "prompt": f"{_feature_title(target_feature)}에서 이 요청을 처리할 화면을 열어줘",
                },
                {
                    "id": "cancel",
                    "label": "2",
                    "title": "취소",
                    "recommended": False,
                    "description": "저장 작업을 진행하지 않습니다.",
                    "prompt": "취소",
                },
            ],
        },
        "table": {
            "kind": "flowi_app_write_draft",
            "title": "Draft-confirm action required",
            "placement": "below",
            "columns": _table_columns(["field", "value"]),
            "rows": rows,
            "total": len(rows),
        },
    }


def _flowi_context_messages(agent_context: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(agent_context, dict):
        return []
    raw = agent_context.get("messages")
    return [m for m in (raw or []) if isinstance(m, dict)] if isinstance(raw, list) else []


def _flowi_pending_create_from_context(agent_context: dict[str, Any] | None) -> dict[str, Any]:
    messages = _flowi_context_messages(agent_context)
    for idx in range(len(messages) - 1, -1, -1):
        msg = messages[idx]
        feature = str(msg.get("feature") or "").strip()
        intent = str(msg.get("intent") or "")
        action = str(msg.get("action") or "")
        missing = msg.get("missing") if isinstance(msg.get("missing"), list) else []
        pending_prompt = str(msg.get("pending_prompt") or "").strip()
        if not feature or (not missing and not intent.endswith("_create_needs_context") and action != "collect_required_fields"):
            continue
        if not pending_prompt:
            for prev in range(idx - 1, -1, -1):
                if str(messages[prev].get("role") or "") == "user":
                    pending_prompt = str(messages[prev].get("prompt") or messages[prev].get("text") or "").strip()
                    break
        if pending_prompt:
            return {
                "feature": "tracker" if feature == "annotation" else feature,
                "pending_prompt": pending_prompt,
                "missing": missing,
            }
    return {}


def _flowi_pending_core_skill_from_context(agent_context: dict[str, Any] | None) -> dict[str, Any]:
    messages = _flowi_context_messages(agent_context)
    for msg in reversed(messages):
        feature = str(msg.get("feature") or "").strip()
        if feature not in FLOWI_CORE_AGENT_FEATURES:
            continue
        missing = msg.get("missing") if isinstance(msg.get("missing"), list) else []
        if not missing:
            validation = msg.get("validation") if isinstance(msg.get("validation"), dict) else {}
            missing = validation.get("missing") if isinstance(validation.get("missing"), list) else []
        pending_prompt = str(msg.get("pending_prompt") or msg.get("last_partial_prompt") or "").strip()
        if not pending_prompt:
            workflow = msg.get("workflow_state") if isinstance(msg.get("workflow_state"), dict) else {}
            pending_prompt = str(workflow.get("last_prompt") or "").strip()
        if not pending_prompt or not missing:
            continue
        return {
            "feature": feature,
            "action": str(msg.get("action") or "").strip(),
            "missing": missing,
            "pending_prompt": pending_prompt,
        }
    return {}


def _flowi_looks_like_core_missing_followup(prompt: str, pending: dict[str, Any]) -> bool:
    text = str(prompt or "").strip()
    if not text:
        return False
    if text.startswith(_FLOWI_INFORM_CONFIRM_MARKER) or text.startswith(_FLOWI_INFORM_WALKTHROUGH_MARKER):
        return False
    missing = {_flowi_missing_key(x) for x in (pending.get("missing") or [])}
    field_match = re.match(r"\s*([A-Za-z가-힣_][A-Za-z가-힣0-9_ -]{0,40})\s*[:=]", text)
    if field_match:
        field_key = _flowi_missing_key(field_match.group(1))
        field_key = {"제품": "product", "프로덕트": "product"}.get(field_key, field_key)
        if field_key in missing:
            return True
    if len(text) > 160 and not (":" in text or missing & {"note", "reason", "entries"}):
        return False
    entries = _matched_feature_entrypoints(text, limit=1)
    source_value_followup = "source_type" in missing and bool(_flowi_source_type_tokens(text))
    if (
        entries
        and entries[0].get("key") != pending.get("feature")
        and not (missing & {"note", "reason", "entries", "comment"})
        and not source_value_followup
    ):
        return False
    return True


def _flowi_is_splittable_source_followup(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    return any(t in low or t in text for t in ("스플릿테이블", "스플릿 테이블", "split table", "splittable", "ml_table", "ml table"))


def _flowi_format_core_missing_followup(prompt: str, pending: dict[str, Any]) -> str:
    text = str(prompt or "").strip()
    if not text or re.search(r"[:=]", text):
        return text
    missing = [_flowi_missing_key(x) for x in (pending.get("missing") or [])]
    first = missing[0] if missing else ""
    feature = str(pending.get("feature") or "")
    if first == "product" and feature == "splittable" and _flowi_is_splittable_source_followup(text):
        return "ML_TABLE"
    if first == "module":
        return f"module: {text}"
    if first == "split_set":
        return f"split_set: {text}"
    if first in {"note", "reason"}:
        return f"내용: {text}" if first == "note" else f"reason: {text}"
    if first == "product":
        return f"product: {text}"
    if first in {"root_lot_ids", "root_lot_id", "lot_ids", "fab_lot_ids", "root_lot_id_or_fab_lot_id"}:
        return f"lot: {text}"
    if first == "source_type":
        return f"source_type: {text}"
    if first == "chart_grain":
        if _explicit_shot_grain(text):
            return "grain: shot"
        if _explicit_lot_wf_grain(text) or any(t in text for t in ("wafer", "웨이퍼", "와퍼", "평균", "avg")):
            return "grain: lot_wf"
        return f"grain: {text}"
    return text


def _flowi_resolve_pending_core_prompt(
    prompt: str,
    agent_context: dict[str, Any] | None,
    allowed_keys: set[str] | None,
) -> str:
    pending = _flowi_pending_core_skill_from_context(agent_context)
    if not pending:
        return prompt
    feature = str(pending.get("feature") or "")
    if allowed_keys is not None and feature not in allowed_keys:
        return prompt
    if not _flowi_looks_like_core_missing_followup(prompt, pending):
        return prompt
    followup = _flowi_format_core_missing_followup(prompt, pending)
    combined = (str(pending.get("pending_prompt") or "").strip() + "\n" + followup).strip()
    if not combined:
        return prompt
    if "chart_grain" in {_flowi_missing_key(x) for x in (pending.get("missing") or [])}:
        return combined
    preview = _structure_flowi_function_call(combined, product="", max_rows=12)
    selected = preview.get("selected_function") if isinstance(preview.get("selected_function"), dict) else {}
    if str(selected.get("feature") or "") != feature:
        return prompt
    pending_action = str(pending.get("action") or "").strip()
    selected_action = str(selected.get("name") or "").strip()
    if pending_action.startswith("clarify_"):
        return combined
    loose_actions = {"route_flowi_feature", "open_filebrowser", "open_splittable", "open_inform", "collect_required_fields"}
    if pending_action and selected_action and pending_action != selected_action and pending_action not in loose_actions:
        return prompt
    return combined


def _handle_app_write_missing_followup(prompt: str, me: dict[str, Any], agent_context: dict[str, Any] | None, allowed_keys: set[str] | None = None) -> dict[str, Any]:
    if _is_app_write_status_followup(prompt):
        return {"handled": False}
    if _detect_app_write_feature(prompt) and _flowi_app_write_mode(prompt) == "create":
        return {"handled": False}
    pending = _flowi_pending_create_from_context(agent_context)
    feature = str(pending.get("feature") or "").strip()
    base = str(pending.get("pending_prompt") or "").strip()
    if not feature or not base:
        return {"handled": False}
    if allowed_keys is not None and feature not in allowed_keys:
        return _flowi_permission_block(feature, me)
    combined = (base + "\n" + str(prompt or "").strip()).strip()
    product = _product_hint(combined)
    lots = _lot_tokens(combined)
    wafers = _wafer_tokens(combined)
    missing, choices = _flowi_app_create_missing(feature, combined, product, lots, wafers)
    if missing:
        return _flowi_app_write_missing(feature, missing, combined, product, lots, wafers, choices=choices)
    try:
        created = _flowi_create_app_record(feature, combined, me, product, lots, wafers)
        created["intent"] = f"{feature}_create_from_missing_context"
        created["answer"] = "부족한 값을 반영해서 등록했습니다.\n" + str(created.get("answer") or "")
        return created
    except Exception as e:
        logger.warning("flowi app missing followup create failed: %s", e)
        return {
            "handled": True,
            "intent": f"{feature}_create_failed",
            "action": "create_app_record",
            "blocked": True,
            "answer": f"{_feature_title(feature)} 등록 중 오류가 발생했습니다. 관련 화면에서 직접 확인해주세요: {e}",
            "feature": feature,
            "slots": {"product": product, "lots": lots, "wafers": wafers},
        }


def _is_app_write_status_followup(prompt: str) -> bool:
    text = str(prompt or "")
    if not text.strip():
        return False
    status_terms = (
        "등록했", "등록됐", "등록 되었", "등록되어", "생성했", "생성됐", "생성 되었",
        "만들었", "만들어졌", "저장했", "저장됐", "저장 되었", "추가했", "추가됐",
        "되어있", "되어 있", "안되어", "안 되어", "안됐", "안 됐", "됐어", "되었어",
    )
    if not any(term in text for term in status_terms):
        return False
    return bool("?" in text or text.rstrip().endswith(("어", "니", "나", "요")))


def _flowi_feature_from_context(agent_context: dict[str, Any] | None) -> str:
    for msg in reversed(_flowi_context_messages(agent_context)):
        feature = str(msg.get("feature") or "").strip()
        if feature:
            return "tracker" if feature == "annotation" else feature
        intent = str(msg.get("intent") or "")
        action = str(msg.get("action") or "")
        text = " ".join([intent, action, str(msg.get("prompt") or ""), str(msg.get("text") or "")])
        if "tracker" in text or "이슈" in text:
            return "tracker"
        if "meeting" in text or "회의" in text:
            return "meeting"
        if "inform" in text or "인폼" in text:
            return "inform"
        if "calendar" in text or "일정" in text or "변경점" in text:
            return "calendar"
    return ""


def _flowi_last_create_prompt(agent_context: dict[str, Any] | None, feature: str) -> str:
    for msg in reversed(_flowi_context_messages(agent_context)):
        if str(msg.get("role") or "") != "user":
            continue
        text = str(msg.get("prompt") or msg.get("text") or "").strip()
        if not text or _is_app_write_status_followup(text):
            continue
        f = _detect_app_write_feature(text)
        f = "tracker" if f == "annotation" else f
        if f == feature and _flowi_app_write_mode(text) == "create":
            return text
    return ""


def _flowi_created_record_from_context(agent_context: dict[str, Any] | None, feature: str) -> dict[str, Any]:
    for msg in reversed(_flowi_context_messages(agent_context)):
        rec = msg.get("created_record")
        if isinstance(rec, dict):
            rec_feature = str(rec.get("feature") or msg.get("feature") or "").strip()
            if not rec_feature or rec_feature == feature:
                return rec
        text = str(msg.get("text") or "")
        if feature == "tracker":
            m = re.search(r"\bISS-\d{6}-[A-Z0-9]{4}\b", text)
            if m:
                return {"id": m.group(0), "feature": feature}
        if feature == "meeting":
            m = re.search(r"\bmt_\d{6}_[a-f0-9]{6}\b", text, flags=re.I)
            if m:
                return {"id": m.group(0), "feature": feature}
    return {}


def _flowi_find_app_record(feature: str, *, username: str, record_id: str = "", title: str = "", lots: list[str] | None = None) -> dict[str, Any]:
    lots_u = {_upper(x) for x in (lots or []) if str(x or "").strip()}
    title_s = str(title or "").strip()
    rid = str(record_id or "").strip()
    try:
        if feature == "tracker":
            from routers import tracker as tracker_router
            rows = tracker_router._load()
            def score(issue: dict[str, Any]) -> int:
                if rid and issue.get("id") == rid:
                    return 100
                s = 0
                if title_s:
                    if str(issue.get("title") or "").strip() == title_s:
                        s += 20
                    else:
                        return 0
                if username and str(issue.get("username") or issue.get("created_by") or "") == username:
                    s += 4
                issue_lots = set()
                for lot in issue.get("lots") or []:
                    if isinstance(lot, dict):
                        issue_lots.update(_upper(lot.get(k)) for k in ("lot_id", "root_lot_id", "wafer_id") if lot.get(k))
                if lots_u and issue_lots & lots_u:
                    s += 12
                elif lots_u and not title_s:
                    return 0
                return s
            best = max((r for r in rows if isinstance(r, dict)), key=score, default=None)
            if best and score(best) >= (100 if rid else 12):
                return {"id": best.get("id") or "", "title": best.get("title") or "", "feature": feature, "target": best.get("category") or "", "found": True}
        if feature == "meeting":
            from routers import meetings as meetings_router
            rows = meetings_router._load()
            def score(meeting: dict[str, Any]) -> int:
                if rid and meeting.get("id") == rid:
                    return 100
                s = 0
                if title_s and str(meeting.get("title") or "").strip() == title_s:
                    s += 20
                if username and username in {str(meeting.get("owner") or ""), str(meeting.get("created_by") or "")}:
                    s += 4
                return s
            best = max((r for r in rows if isinstance(r, dict)), key=score, default=None)
            if best and score(best) >= (100 if rid else 16):
                scheduled = ""
                sessions = best.get("sessions") or []
                if sessions and isinstance(sessions[0], dict):
                    scheduled = sessions[0].get("scheduled_at") or ""
                return {"id": best.get("id") or "", "title": best.get("title") or "", "feature": feature, "target": best.get("owner") or "", "scheduled_at": scheduled, "found": True}
        if feature == "inform":
            from routers import informs as informs_router
            rows = informs_router._load_upgraded()
            for row in reversed([r for r in rows if isinstance(r, dict)]):
                if rid and row.get("id") != rid:
                    continue
                if lots_u and not ({_upper(row.get("lot_id")), _upper(row.get("wafer_id")), _upper(row.get("root_lot_id"))} & lots_u):
                    continue
                if username and row.get("author") not in {"", username}:
                    continue
                return {"id": row.get("id") or "", "title": title_s or row.get("reason") or "인폼", "feature": feature, "target": row.get("lot_id") or row.get("wafer_id") or "", "found": True}
        if feature == "calendar":
            from routers import calendar as calendar_router
            rows = calendar_router._load_events()
            for row in reversed([r for r in rows if isinstance(r, dict)]):
                if rid and row.get("id") != rid:
                    continue
                if title_s and str(row.get("title") or "").strip() != title_s:
                    continue
                if username and row.get("author") not in {"", username}:
                    continue
                return {"id": row.get("id") or "", "title": row.get("title") or "", "feature": feature, "target": row.get("date") or "", "found": True}
    except Exception as e:
        logger.warning("flowi app record lookup failed: %s", e)
    return {}


def _handle_app_write_status_followup(prompt: str, me: dict[str, Any], agent_context: dict[str, Any] | None, allowed_keys: set[str] | None = None) -> dict[str, Any]:
    if not _is_app_write_status_followup(prompt):
        return {"handled": False}
    feature = _detect_app_write_feature(prompt) or _flowi_feature_from_context(agent_context)
    feature = "tracker" if feature == "annotation" else feature
    if not feature or feature not in {"tracker", "meeting", "inform", "calendar"}:
        return {"handled": False}
    if allowed_keys is not None and feature not in allowed_keys:
        return _flowi_permission_block(feature, me)
    username = me.get("username") or "user"
    rec_ctx = _flowi_created_record_from_context(agent_context, feature)
    prev_prompt = _flowi_last_create_prompt(agent_context, feature)
    basis_prompt = prev_prompt or prompt
    title = _flowi_prompt_title(basis_prompt, feature)
    lots = _lot_tokens(basis_prompt)
    found = _flowi_find_app_record(
        feature,
        username=username,
        record_id=str(rec_ctx.get("id") or ""),
        title=title,
        lots=lots,
    )
    if found:
        answer = f"네, 직전 {_feature_title(feature)} 등록 기록을 확인했습니다.\n- id: {found.get('id') or '-'}\n- title: {found.get('title') or '-'}"
        if found.get("target"):
            answer += f"\n- target: {found.get('target')}"
        if found.get("scheduled_at"):
            answer += f"\n- 1차 일시: {found.get('scheduled_at')}"
        rows = [{"field": k, "value": v} for k, v in found.items() if k not in {"found"} and v]
        return {
            "handled": True,
            "intent": f"{feature}_registration_status",
            "action": "check_app_record",
            "answer": answer,
            "feature": feature,
            "created_record": found,
            "feature_entrypoints": [item for item in FLOWI_FEATURE_ENTRYPOINTS if item["key"] == feature],
            "table": {"kind": "flowi_app_record_status", "title": "Registration status", "placement": "below", "columns": _table_columns(["field", "value"]), "rows": rows, "total": len(rows)},
        }
    if rec_ctx.get("id"):
        rid = str(rec_ctx.get("id") or "")
        return {
            "handled": True,
            "intent": f"{feature}_registration_status_missing",
            "action": "check_app_record",
            "blocked": True,
            "answer": (
                f"직전 응답에는 {_feature_title(feature)} 생성 id `{rid}`가 있었지만, 현재 저장소에서 같은 id를 확인하지 못했습니다. "
                "중복 등록을 피하려고 자동 재등록은 하지 않았습니다. 다시 등록하려면 원래 등록 요청을 그대로 보내주세요."
            ),
            "feature": feature,
            "created_record": rec_ctx,
            "feature_entrypoints": [item for item in FLOWI_FEATURE_ENTRYPOINTS if item["key"] == feature],
        }
    if prev_prompt:
        created = _flowi_create_app_record(feature, prev_prompt, me, _product_hint(prev_prompt), _lot_tokens(prev_prompt), _wafer_tokens(prev_prompt))
        created["intent"] = f"{feature}_registration_followup_create"
        created["answer"] = "직전 등록 요청이 저장 기록으로 확인되지 않아, 같은 요청을 지금 이어서 등록했습니다.\n" + str(created.get("answer") or "")
        return created
    return {
        "handled": True,
        "intent": f"{feature}_registration_status_unknown",
        "action": "check_app_record",
        "blocked": True,
        "answer": f"현재 대화에서 확인할 직전 {_feature_title(feature)} 등록 요청이나 생성 id를 찾지 못했습니다. 제목, lot, 회의명 중 하나를 같이 알려주면 실제 저장 기록을 확인하겠습니다.",
        "feature": feature,
        "feature_entrypoints": [item for item in FLOWI_FEATURE_ENTRYPOINTS if item["key"] == feature],
    }


def _flowi_inform_summary_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    if not any(term in low or term in text for term in ("inform", "인폼", "공지", "공유")):
        return False
    has_write_term = (
        (_detect_app_write_feature(text) and _flowi_app_write_mode(text))
        or any(term in low or term in text for term in _FLOWI_APP_WRITE_TERMS + _FLOWI_APP_CREATE_TERMS)
    )
    has_explicit_read_term = any(term in low or term in text for term in (
        "현황", "상태", "요약", "누락", "미등록", "미완료", "조회", "검색",
        "목록", "리스트", "로그", "status", "summary", "missing", "list", "show",
    ))
    has_request_verb = any(term in low or term in text for term in ("해줘", "해주세요", "할게", "진행"))
    if (has_write_term or has_request_verb) and not has_explicit_read_term:
        return False
    has_read_term = any(term in low or term in text for term in (
        "현황", "상태", "요약", "누락", "미등록", "미완료", "전체", "모듈", "관리",
        "보여", "조회", "검색", "확인", "목록", "리스트", "로그", "status", "summary", "missing", "module", "list", "show",
    ))
    if not has_read_term:
        return False
    return True


def _handle_flowi_inform_summary(prompt: str, me: dict[str, Any], max_rows: int, allowed_keys: set[str] | None = None) -> dict[str, Any]:
    if not _flowi_inform_summary_intent(prompt):
        return {"handled": False}
    if allowed_keys is not None and "inform" not in allowed_keys:
        return _flowi_permission_block("inform", me)
    from routers import informs as informs_router
    username = me.get("username") or "user"
    role = me.get("role") or "user"
    my_mods = informs_router._effective_modules(username, role)
    lots = _lot_tokens(prompt)
    module = _flowi_module_token(prompt)
    if not lots and module:
        product_hint = _product_hint(prompt)
        items = informs_router._load_upgraded()
        hits = [
            x for x in items
            if not x.get("parent_id")
            and str(x.get("module") or "").strip().lower() == str(module).strip().lower()
            and (not product_hint or str(x.get("product") or "").strip().upper() == str(product_hint).strip().upper())
        ]
        hits = [x for x in hits if informs_router._visible_to(x, username, role, my_mods)]
        hits.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        limit = max(1, min(80, int(max_rows or 12) * 4))
        rows = []
        status_counts = Counter()
        for item in hits[:limit]:
            status = informs_router._canonical_flow_status(item.get("flow_status"), item)
            status_counts[status] += 1
            rows.append({
                "created_at": item.get("created_at") or "",
                "id": item.get("id") or "",
                "product": item.get("product") or "",
                "root_lot_id": item.get("root_lot_id") or "",
                "lot_id": item.get("lot_id") or "",
                "fab_lot_id_at_save": item.get("fab_lot_id_at_save") or "",
                "module": item.get("module") or "",
                "flow_status": status,
                "reason": item.get("reason") or "",
                "text": _text(item.get("text"))[:240],
            })
        title = f"{module} 모듈 인폼로그 최근 상태"
        answer_lines = [
            title,
            "",
            "요약",
            f"- 조건에 맞는 인폼 {len(hits)}건을 찾았습니다.",
            f"- apply_confirmed {status_counts.get('apply_confirmed', 0)}건 / mail_completed {status_counts.get('mail_completed', 0)}건 / registered {status_counts.get('registered', 0)}건",
        ]
        if rows:
            answer_lines.extend(["", "인폼 상태"])
            for row in rows[:8]:
                answer_lines.append(
                    "- "
                    + " / ".join([
                        str(row.get("created_at") or "-")[:16],
                        str(row.get("product") or "-"),
                        str(row.get("fab_lot_id_at_save") or row.get("lot_id") or row.get("root_lot_id") or "-"),
                        str(row.get("flow_status") or "-"),
                        str(row.get("reason") or "-"),
                    ])
                )
            if len(rows) > 8:
                answer_lines.append(f"- 외 {len(rows) - 8}건은 표에서 확인하세요.")
        answer_lines.extend(["", "근거", "- /api/informs/recent와 같은 Inform 저장소의 visible root inform만 사용했습니다."])
        cols_out = ["created_at", "id", "product", "root_lot_id", "lot_id", "fab_lot_id_at_save", "module", "flow_status", "reason", "text"]
        return {
            "handled": True,
            "intent": "inform_module_recent_summary",
            "action": "summarize_inform_modules",
            "answer": "\n".join(answer_lines),
            "feature": "inform",
            "slots": {"module": module, "product": product_hint},
            "summary": {"module": module, "total": len(hits), "status_counts": dict(status_counts)},
            "feature_entrypoints": [item for item in FLOWI_FEATURE_ENTRYPOINTS if item["key"] == "inform"],
            "table": {
                "kind": "inform_module_recent_summary",
                "title": f"Inform recent summary: {module}",
                "placement": "below",
                "columns": _table_columns(cols_out),
                "rows": rows,
                "total": len(hits),
            },
        }
    if not lots:
        return {
            "handled": True,
            "intent": "inform_lot_module_summary_needs_context",
            "action": "collect_required_fields",
            "answer": "Lot별 인폼 모듈 현황을 보려면 lot_id 또는 root_lot_id가 필요합니다.",
            "feature": "inform",
            "missing": ["lot_id 또는 root_lot_id"],
            "clarification": {
                "question": "어떤 Lot의 인폼 현황을 볼까요?",
                "choices": [{
                    "id": "provide_lot",
                    "label": "1",
                    "title": "Lot 입력",
                    "recommended": True,
                    "description": "root_lot_id 또는 fab_lot_id를 이어서 입력합니다.",
                    "prompt": "lot_id: ",
                }],
            },
            "feature_entrypoints": [item for item in FLOWI_FEATURE_ENTRYPOINTS if item["key"] == "inform"],
        }
    query = lots[0]
    root = informs_router._root_lot_from_values(query)
    root_prefix = root if len(root) <= 5 else ""
    items = informs_router._load_upgraded()
    hits = [x for x in items if (
        (x.get("root_lot_id") or informs_router._root_lot_from_values(x.get("lot_id") or "")) == root
        or (root_prefix and (x.get("root_lot_id") or "").startswith(root_prefix))
        or (query and (x.get("lot_id") or "") == query)
        or (query and (x.get("fab_lot_id_at_save") or "") == query)
    )]
    hits = [x for x in hits if informs_router._visible_to(x, username, role, my_mods)]
    hits.sort(key=lambda x: x.get("created_at", ""))
    summary = informs_router._module_progress_summary(hits)
    rows = []
    for row in summary.get("modules") or []:
        rows.append({
            "module": row.get("module") or "",
            "status": row.get("status") or "",
            "count": row.get("count") or 0,
            "mail_count": row.get("mail_count") or 0,
            "last_at": row.get("last_at") or "",
            "completed_at": row.get("completed_at") or "",
        })
    missing = summary.get("missing_modules") or []
    pending = summary.get("pending_modules") or []
    answer = (
        f"{root or query} 인폼 모듈 현황입니다.\n"
        f"- 등록 모듈: {summary.get('active_modules', 0)}/{summary.get('total_modules', 0)}\n"
        f"- 완료 모듈: {summary.get('completed_modules', 0)}\n"
        f"- 미완료 모듈: {len(pending)}\n"
        f"- 미등록 모듈: {len(missing)}"
    )
    if pending:
        answer += "\n- 미완료: " + ", ".join(pending[:8]) + ("..." if len(pending) > 8 else "")
    if missing:
        answer += "\n- 미등록: " + ", ".join(missing[:8]) + ("..." if len(missing) > 8 else "")
    return {
        "handled": True,
        "intent": "inform_lot_module_summary",
        "action": "summarize_inform_modules",
        "answer": answer,
        "feature": "inform",
        "slots": {"lots": [query], "root_lot_id": root, "product": _product_hint(prompt)},
        "summary": summary,
        "feature_entrypoints": [item for item in FLOWI_FEATURE_ENTRYPOINTS if item["key"] == "inform"],
        "table": {
            "kind": "inform_lot_module_summary",
            "title": f"Inform module summary: {root or query}",
            "placement": "below",
            "columns": _table_columns(["module", "status", "count", "mail_count", "last_at", "completed_at"]),
            "rows": rows,
            "total": len(rows),
        },
    }


def _handle_value_table_query(prompt: str, product: str, max_rows: int) -> dict:
    if not _flowi_value_lookup_intent(prompt):
        return {"handled": False}
    lots = _lot_tokens(prompt)
    terms = _query_tokens(prompt)
    # ET/INLINE requests are handled by their dedicated unit functions. This
    # generic table path focuses on ML_TABLE/Base data, matching FileBrowser's
    # read-only preview behavior.
    if ("ET" in _upper(prompt) or "INLINE" in _upper(prompt)) and not ("ML_TABLE" in _upper(prompt) or "KNOB" in _upper(prompt)):
        return {"handled": False}
    files = _ml_files(product)
    if not files:
        return {"handled": False}
    lf = _scan_parquet(files)
    cols = _schema_names(lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    lot_wf_col = _ci_col(cols, "lot_wf", "LOT_WF")
    id_cols = [c for c in (product_col, root_col, lot_col, fab_col, wafer_col, lot_wf_col) if c]
    matched_cols = [c for c in _column_matches(cols, terms, include_knob_when_named=True) if c not in id_cols]
    if not matched_cols and "KNOB" in _upper(prompt):
        matched_cols = [c for c in cols if _upper(c).startswith("KNOB_")][:8]
    if not lots and not matched_cols:
        return {"handled": False}

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

    display_cols = list(dict.fromkeys([*id_cols, *matched_cols[:16]]))
    if not display_cols:
        display_cols = cols[: min(12, len(cols))]
    try:
        df = lf.select([pl.col(c).cast(_STR, strict=False).alias(c) for c in display_cols]).limit(max(1, min(120, max_rows * 8))).collect()
    except Exception as e:
        logger.warning("flowi table lookup failed: %s", e)
        return {
            "handled": True,
            "intent": "db_table_lookup",
            "answer": f"DB table 조회에 실패했습니다: {e}",
            "table": {
                "kind": "flowi_db_table",
                "title": "DB table lookup error",
                "placement": "below",
                "columns": _table_columns(["error"]),
                "rows": [{"error": str(e)}],
                "total": 1,
            },
        }
    rows = df.to_dicts()
    if not rows:
        return {
            "handled": True,
            "intent": "db_table_lookup",
            "answer": "실제 ML_TABLE parquet에서 조건에 맞는 row를 찾지 못했습니다. product/lot/컬럼명을 다시 확인해주세요.",
            "table": {
                "kind": "flowi_db_table",
                "title": "ML_TABLE lookup",
                "placement": "below",
                "columns": _table_columns(["message"]),
                "rows": [{"message": "no rows"}],
                "total": 0,
            },
            "filters": {"lot": lots, "product": sorted(aliases), "columns": matched_cols},
        }
    title_bits = []
    if product:
        title_bits.append(_core_product_name(product))
    if lots:
        title_bits.append(",".join(lots))
    if matched_cols:
        title_bits.append(",".join(matched_cols[:4]))
    title = " / ".join(title_bits) or "ML_TABLE"
    answer = (
        "실제 ML_TABLE parquet에서 조건을 적용해 표로 조회했습니다. "
        f"{len(rows)}개 row를 표시합니다."
    )
    if matched_cols:
        answer += f" 조회 컬럼: {', '.join(matched_cols[:8])}."
    return {
        "handled": True,
        "intent": "db_table_lookup",
        "action": "query_filebrowser_table",
        "answer": answer,
        "table": {
            "kind": "flowi_db_table",
            "title": title,
            "placement": "below",
            "columns": _table_columns(display_cols),
            "rows": rows,
            "total": len(rows),
            "source": "ML_TABLE",
        },
        "filters": {"lot": lots, "product": sorted(aliases), "columns": matched_cols},
    }


def _fastest_knob_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    has_knob = "KNOB" in _upper(text) or "노브" in text
    has_rank = any(t in low or t in text for t in (
        "가장 빠", "제일 빠", "어디", "앞선", "진행", "current", "latest", "fastest", "advanced",
    ))
    return has_knob and has_rank


def _mentioned_values(prompt: str, values: list[str]) -> list[str]:
    up = _upper(prompt)
    toks = set(_tokens(prompt))
    out = []
    for value in values:
        raw = _text(value)
        val = _upper(raw)
        if not val:
            continue
        hit = val in toks if len(val) <= 2 else val in up
        if hit and raw not in out:
            out.append(raw)
    return out


def _step_rank_key(step_id: Any) -> tuple[int, ...]:
    nums = [int(x) for x in re.findall(r"\d+", str(step_id or ""))]
    if not nums:
        return (-1,)
    return tuple(nums[-4:])


def _latest_fab_steps_for_roots(product: str, roots: list[str], limit: int = 200) -> dict[str, dict[str, Any]]:
    clean_roots = [r for r in dict.fromkeys(_text(r) for r in roots) if r]
    if not clean_roots:
        return {}
    files: list[Path] = []
    for root in _db_root_candidates("FAB"):
        files.extend(sorted(root.rglob("*.parquet")))
    files = _filter_files_by_product(files, product)
    if not files:
        return {}
    lf = _scan_parquet(files)
    cols = _schema_names(lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    step_col = _ci_col(cols, "step_id", "STEP_ID")
    time_col = _ci_col(cols, "tkout_time", "TKOUT_TIME", "time", "TIME", "timestamp", "TIMESTAMP", "move_time", "MOVE_TIME")
    if not step_col or not (root_col or lot_col or fab_col):
        return {}
    aliases = _product_aliases(product)
    filters = []
    if aliases and product_col:
        filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    if root_col:
        filters.append(pl.col(root_col).cast(_STR, strict=False).is_in(clean_roots))
    else:
        lot_expr = _or_contains([c for c in (lot_col, fab_col) if c], clean_roots)
        if lot_expr is not None:
            filters.append(lot_expr)
    for expr in filters:
        lf = lf.filter(expr)
    exprs = []
    if product_col:
        exprs.append(pl.col(product_col).cast(_STR, strict=False).alias("product"))
    else:
        exprs.append(pl.lit(_core_product_name(product)).alias("product"))
    if root_col:
        exprs.append(pl.col(root_col).cast(_STR, strict=False).alias("root_lot_id"))
    elif lot_col:
        exprs.append(pl.col(lot_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id"))
    else:
        exprs.append(pl.col(fab_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id"))
    for src, alias in ((lot_col, "lot_id"), (fab_col, "fab_lot_id"), (wafer_col, "wafer_id")):
        if src:
            exprs.append(pl.col(src).cast(_STR, strict=False).alias(alias))
        else:
            exprs.append(pl.lit("").alias(alias))
    exprs.append(pl.col(step_col).cast(_STR, strict=False).alias("step_id"))
    if time_col:
        exprs.append(pl.col(time_col).cast(_STR, strict=False).alias("time"))
    else:
        exprs.append(pl.lit("").alias("time"))
    try:
        scoped = lf.select(exprs).drop_nulls(subset=["step_id"])
        if time_col:
            scoped = scoped.sort("time", descending=True)
        df = (
            scoped.group_by("root_lot_id")
            .agg([
                pl.col("product").first(),
                pl.col("lot_id").first(),
                pl.col("fab_lot_id").first(),
                pl.col("wafer_id").first(),
                pl.col("step_id").first(),
                pl.col("time").first(),
            ])
            .limit(max(1, min(1000, limit)))
            .collect()
        )
    except Exception as e:
        logger.warning("flowi latest fab step scan failed: %s", e)
        return {}
    try:
        from core.lot_step import lookup_step_meta
    except Exception:
        lookup_step_meta = None
    out = {}
    for row in df.to_dicts():
        root = _text(row.get("root_lot_id"))
        if not root:
            continue
        meta = lookup_step_meta(product=row.get("product") or product, step_id=row.get("step_id")) if lookup_step_meta else {}
        out[root] = {
            **row,
            "func_step": meta.get("func_step") or meta.get("function_step") or meta.get("step_desc") or "",
            "step_rank": _step_rank_key(row.get("step_id")),
        }
    return out


def _handle_fastest_knob_query(prompt: str, product: str, max_rows: int) -> dict:
    if not _fastest_knob_intent(prompt):
        return {"handled": False}
    product_hint = _flowi_splittable_product_id(_product_hint(prompt, product))
    if not product_hint:
        return _flowi_set_inline_type({
            "handled": True,
            "intent": "knob_fastest_lot_needs_product",
            "action": "collect_required_fields",
            "answer": "KNOB 조건으로 가장 앞선 LOT_WF를 찾으려면 product가 필요합니다. 제품명을 알려주면 SplitTable/ML_TABLE에서 KNOB 값을 찾고 latest progress cache로 현재 step_id/function_step을 붙입니다.",
            "feature": "splittable",
            "missing": ["product"],
            "pending_prompt": prompt,
            "slots": {"source": "ML_TABLE+latest_progress_cache"},
        }, "message", prompt=prompt)
    files = _ml_files(product_hint)
    if not files:
        return {
            "handled": True,
            "intent": "knob_fastest_lot",
            "answer": "ML_TABLE parquet을 찾지 못했습니다. product 또는 DB root를 확인해주세요.",
            "table": {
                "kind": "knob_fastest_lot",
                "title": "KNOB fastest lot",
                "placement": "below",
                "columns": _table_columns(["message"]),
                "rows": [{"message": "ML_TABLE not found"}],
                "total": 0,
            },
        }
    lf = _scan_parquet(files)
    cols = _schema_names(lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    lot_wf_col = _ci_col(cols, "lot_wf", "LOT_WF")
    if not root_col:
        return {"handled": True, "intent": "knob_fastest_lot", "answer": "ML_TABLE에 root_lot_id 컬럼이 없어 FAB 진행 위치를 연결할 수 없습니다."}
    knob_cols = [c for c in cols if _upper(c).startswith("KNOB_")]
    if not knob_cols:
        return {"handled": True, "intent": "knob_fastest_lot", "answer": "ML_TABLE에서 KNOB_* 컬럼을 찾지 못했습니다.", "knobs": []}

    lots = _lot_tokens(prompt)
    aliases = _product_aliases(product_hint)
    filters = []
    if aliases and product_col:
        filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    if lots:
        lot_expr = _or_contains([c for c in (root_col, lot_col, fab_col) if c], lots)
        if lot_expr is not None:
            filters.append(lot_expr)
    for expr in filters:
        lf = lf.filter(expr)
    step = _flowi_func_step_token(prompt)
    step_expr = _flowi_step_filter_expr(cols, step)
    if step_expr is not None:
        lf = lf.filter(step_expr)

    terms = _flowi_knob_query_terms(prompt, lots, [])
    if not terms:
        candidates = knob_cols[:12]
        return {
            "handled": True,
            "intent": "knob_fastest_lot",
            "answer": "어떤 KNOB 기준으로 가장 앞선 lot을 찾을지 선택이 필요합니다. 아래 후보 중 하나를 골라주세요.",
            "clarification": {
                "question": "가장 빠른 lot을 찾을 KNOB 컬럼을 선택하세요.",
                "choices": [
                    {
                        "id": f"knob_{i}",
                        "label": str(i + 1),
                        "title": col.replace("KNOB_", "", 1),
                        "recommended": i == 0,
                        "description": f"{col} 값을 가진 lot 중 FAB 최신 step이 가장 앞선 lot을 찾습니다.",
                        "prompt": f"{prompt.strip()} {col}",
                    }
                    for i, col in enumerate(candidates[:4])
                ],
            },
            "table": {
                "kind": "knob_candidates",
                "title": "KNOB column candidates",
                "placement": "below",
                "columns": _table_columns(["knob"]),
                "rows": [{"knob": c} for c in candidates],
                "total": len(candidates),
            },
        }

    knob_col, knob_candidates = _select_knob_column(lf, knob_cols, prompt, lots, [])
    if not knob_col:
        return {"handled": True, "intent": "knob_fastest_lot", "answer": "요청과 맞는 KNOB 컬럼을 찾지 못했습니다.", "knobs": []}
    values = _unique_strings(lf, knob_col, limit=100)
    selected_values = _mentioned_values(prompt, values)
    value_filter = selected_values or []
    scoped = lf
    if value_filter:
        scoped = scoped.filter(pl.col(knob_col).cast(_STR, strict=False).is_in(value_filter))
    else:
        scoped = scoped.filter(
            pl.col(knob_col).is_not_null()
            & (pl.col(knob_col).cast(_STR, strict=False).str.strip_chars() != "")
            & (~pl.col(knob_col).cast(_STR, strict=False).is_in(["None", "null"]))
        )
    keep = [c for c in (product_col, root_col, lot_col, fab_col, wafer_col, lot_wf_col, knob_col) if c]
    try:
        exprs = [pl.col(c).cast(_STR, strict=False).alias(c) for c in keep]
        if not lot_wf_col and root_col and wafer_col:
            exprs.append(_lot_wf_expr(root_col, wafer_col).alias("lot_wf"))
        df = scoped.select(exprs).limit(5000).collect()
    except Exception as e:
        logger.warning("flowi fastest knob ML scan failed: %s", e)
        return {"handled": True, "intent": "knob_fastest_lot", "answer": f"ML_TABLE KNOB 조회 실패: {e}"}
    if df.height == 0:
        return {
            "handled": True,
            "intent": "knob_fastest_lot",
            "answer": f"{knob_col} 조건에 맞는 ML_TABLE row가 없습니다.",
            "table": {
                "kind": "knob_fastest_lot",
                "title": f"{knob_col} fastest lot",
                "placement": "below",
                "columns": _table_columns(["message"]),
                "rows": [{"message": "no ML_TABLE rows"}],
                "total": 0,
            },
        }
    grouped: dict[str, dict[str, Any]] = {}
    for row in df.to_dicts():
        root = _text(row.get(root_col))
        if not root:
            continue
        wafer = _normalize_wafer_id(row.get(wafer_col)) if wafer_col else ""
        lot_wf = _text(row.get(lot_wf_col)) if lot_wf_col else _text(row.get("lot_wf") or _flowi_lot_wf_id(root, wafer))
        key = lot_wf or f"{root}_{wafer}"
        rec = grouped.setdefault(key, {
            "product": _text(row.get(product_col)) or _core_product_name(product_hint),
            "root_lot_id": root,
            "wafer_id": wafer,
            "lot_wf": lot_wf,
            "lot_id": _text(row.get(lot_col)),
            "fab_lot_id": _text(row.get(fab_col)),
            "knob": knob_col,
            "knob_value": _text(row.get(knob_col)),
            "wafer_count": 0,
            "wafers": set(),
        })
        wafer = _text(row.get(wafer_col))
        if wafer:
            rec["wafers"].add(wafer)
        rec["wafer_count"] += 1
        if not rec.get("lot_id") and _text(row.get(lot_col)):
            rec["lot_id"] = _text(row.get(lot_col))
        if not rec.get("fab_lot_id") and _text(row.get(fab_col)):
            rec["fab_lot_id"] = _text(row.get(fab_col))
    progress_rows = list(grouped.values())
    progress_product = product_hint or (next(iter(grouped.values())).get("product") or "")
    progress_by_lot_wf = _flowi_progress_for_lot_rows(progress_product, progress_rows, limit=300)
    rows = []
    for lot_wf, rec in grouped.items():
        fab = progress_by_lot_wf.get(lot_wf) or {}
        wafers = sorted(rec.pop("wafers"), key=lambda x: (len(x), x))
        row = {
            **rec,
            "wafer_ids": ",".join(wafers[:12]),
            "current_step_id": fab.get("step_id") or "",
            "func_step": fab.get("function_step") or fab.get("func_step") or "",
            "fab_lot_current": fab.get("fab_lot_id") or rec.get("fab_lot_id") or "",
            "current_lot_id": fab.get("lot_id") or rec.get("lot_id") or "",
            "current_wafer_id": fab.get("wafer_id") or "",
            "tkout_time": fab.get("update_time") or "",
            "progress_source": fab.get("cache_source") or "",
            "_rank": fab.get("step_rank") or (-1,),
        }
        rows.append(row)
    rows.sort(key=lambda r: (tuple(r.get("_rank") or (-1,)), str(r.get("tkout_time") or "")), reverse=True)
    for row in rows:
        row.pop("_rank", None)
    shown = rows[:max(1, min(40, max_rows))]
    cols_out = [
        "product", "root_lot_id", "wafer_id", "lot_wf", "knob", "knob_value", "wafer_count",
        "current_step_id", "func_step", "fab_lot_current", "current_lot_id", "tkout_time", "progress_source",
    ]
    top = shown[0] if shown else {}
    answer = (
        f"{knob_col} 값을 가진 lot 중 FAB 최신 step 기준으로 가장 앞선 후보를 계산했습니다. "
        f"Top: {top.get('root_lot_id') or '-'} / {top.get('current_step_id') or '-'}"
        f"{' (' + top.get('func_step') + ')' if top.get('func_step') else ''}."
    )
    if value_filter:
        answer += f" 값 필터: {', '.join(value_filter)}."
    return {
        "handled": True,
        "intent": "knob_fastest_lot",
        "action": "query_knob_fastest_fab_step",
        "answer": answer,
        "feature": "splittable",
        "table": {
            "kind": "knob_fastest_lot",
            "title": f"{knob_col} fastest FAB step",
            "placement": "below",
            "columns": _table_columns(cols_out),
            "rows": [{k: row.get(k, "") for k in cols_out} for row in shown],
            "total": len(rows),
        },
        "filters": {"product": sorted(aliases), "lot": lots, "step": step, "knob": knob_col, "values": value_filter, "knob_candidates": knob_candidates[:12], "source": "ML_TABLE+filebrowser_latest_progress"},
    }


def _sort_wafer_rows(rows: list[dict]) -> list[dict]:
    def key(row):
        raw = _text(row.get("wafer_id") or row.get("WAFER_ID"))
        m = re.search(r"\d+", raw)
        return (int(m.group(0)) if m else 9999, raw)
    return sorted(rows, key=key)


def _round4(value: Any) -> Any:
    if value is None:
        return None
    try:
        return round(float(value), 4)
    except Exception:
        return value


def _or_contains(cols: list[str], needles: list[str]) -> Any:
    expr = None
    for col in cols:
        for tok in needles:
            piece = pl.col(col).cast(_STR, strict=False).str.contains(tok, literal=True)
            expr = piece if expr is None else (expr | piece)
    return expr


def _parse_flowi_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            try:
                return value.astimezone(timezone.utc).replace(tzinfo=None)
            except Exception:
                return value.replace(tzinfo=None)
        return value
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "nan"}:
        return None
    text = text.replace("Z", "+00:00")
    if " " in text and "T" not in text:
        text = text.replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y%m%d%H%M%S", "%Y%m%d%H%M", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(str(value).strip(), fmt)
        except Exception:
            continue
    return None


def _fmt_flowi_datetime(value: datetime | None) -> str:
    return value.isoformat(timespec="seconds") if value else ""


def _flowi_hours_between(start: Any, end: Any) -> float | None:
    start_dt = _parse_flowi_datetime(start)
    end_dt = _parse_flowi_datetime(end)
    if not start_dt or not end_dt:
        return None
    return round((end_dt - start_dt).total_seconds() / 3600.0, 3)


def _flowi_percentile(values: list[float], q: float) -> float | None:
    clean = sorted(float(v) for v in values if v is not None)
    if not clean:
        return None
    if len(clean) == 1:
        return round(clean[0], 3)
    pos = (len(clean) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(clean) - 1)
    frac = pos - lo
    return round(clean[lo] * (1 - frac) + clean[hi] * frac, 3)


def _path_tail(fp: Path, depth: int = 4) -> str:
    parts = fp.parts[-depth:]
    return "/".join(parts)


def _flowi_report_terms(prompt: str, lots: list[str] | None = None, product: str = "") -> list[str]:
    blocked = set(_STOP_TOKENS) | {
        "ET", "REPORT", "REPORTED", "업데이트", "최근업데이트", "최근", "안올라왔는데",
        "안올라", "올라왔", "보여줘", "측정시간", "MEASURE", "MEASUREMENT", "DURATION",
        "얼마나", "걸렸어", "걸려", "언제", "도착", "ETA",
    }
    for lot in lots or []:
        blocked.add(_upper(lot))
    blocked.update(_product_aliases(product))
    out: list[str] = []
    seen: set[str] = set()
    for tok in _query_tokens(prompt):
        key = _upper(tok)
        if not key or key in blocked:
            continue
        if re.fullmatch(r"[A-Z]\d{4,}(?:[A-Z])?(?:\.\d+)?", key):
            continue
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out[:10]
