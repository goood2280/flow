def _custom_tags_path() -> Path:
    return PLAN_DIR / "custom_tags.json"


def _load_custom_tags_data() -> dict:
    # cached — _clean_overlay_store_data 는 입력을 수정하지 않고 새 컨테이너를 만든다.
    data = load_json_cached(_custom_tags_path(), {"columns": [], "values": {}, "colors": {}})
    cleaned, changed = _clean_overlay_store_data(data, allow_management=True)
    if changed:
        _save_custom_tags_data(cleaned)
    return cleaned


def _save_custom_tags_data(data: dict) -> None:
    save_json(_custom_tags_path(), {
        "columns": list(data.get("columns") or []),
        "values": dict(data.get("values") or {}),
        "colors": dict(data.get("colors") or {}),
    }, indent=2)


def _tag_column_id(name: str) -> str:
    raw = str(name or "").strip()
    if raw.upper().startswith(f"{CUSTOM_TAG_PREFIX}_"):
        raw = raw[len(CUSTOM_TAG_PREFIX) + 1:].strip()
    token = "".join(c for c in raw if c.isalnum() or c in "_-. ")[:72].strip().replace(" ", "_")
    token = "_".join(part for part in token.split("_") if part)
    if not token:
        raise HTTPException(400, "tag name required")
    return f"{CUSTOM_TAG_PREFIX}_{token}"


def _tag_value_key(product: str, root_lot_id: str, wafer_id: str, column: str) -> str:
    return "|".join([str(product or ""), str(root_lot_id or ""), str(wafer_id or ""), str(column or "")])


# TAG 행의 module 은 엔지니어가 직접 적는 자유 텍스트다. Vehicle_matching 의 module
# 열과 달리 원천이 없으므로 빈 값이 정상이며, 빈 값이면 화면에서도 그냥 비워 둔다.
def _clean_tag_module(value: Any) -> str:
    return str(value or "").strip()[:48]


def _ensure_custom_tag_column(
    data: dict, *, product: str, column: str, label: str, actor: str, now: str, module: Any = None
) -> dict:
    cols = data.setdefault("columns", [])
    product_key = str(product or "").strip()
    column_key = str(column or "").strip()
    existing = next((c for c in cols if c.get("product") == product_key and c.get("column") == column_key), None)
    if existing:
        existing["label"] = str(label or existing.get("label") or column_key).strip() or column_key
        existing["username"] = actor or existing.get("username", "")
        existing["updated"] = now
        # module 은 명시적으로 넘어왔을 때만 건드린다 — 값 저장 경로에서 지워지면 안 된다.
        if module is not None:
            existing["module"] = _clean_tag_module(module)
        return existing
    entry = {
        "product": product_key,
        "column": column_key,
        "label": str(label or column_key).strip() or column_key,
        "module": _clean_tag_module(module),
        "username": actor,
        "created": now,
        "updated": now,
    }
    cols.append(entry)
    return entry


def _custom_tag_columns_for_product(product: str) -> list[dict]:
    product_key = str(product or "").strip()
    data = _load_custom_tags_data()
    out = [{
        "product": product_key,
        "column": DEFAULT_CUSTOM_TAG_COLUMN,
        "label": DEFAULT_CUSTOM_TAG_LABEL,
        "module": "",
        "builtin": True,
    }]
    seen = {DEFAULT_CUSTOM_TAG_COLUMN.upper()}
    for raw in data.get("columns") or []:
        if not isinstance(raw, dict):
            continue
        if raw.get("product") != product_key:
            continue
        column = str(raw.get("column") or "").strip()
        column_key = column.upper()
        if not column or column_key in seen:
            # 저장된 기본 purpose 항목은 생성일/작성자/module 메타만 가상 기본행에 합친다.
            if column_key == DEFAULT_CUSTOM_TAG_COLUMN.upper():
                out[0] = {
                    **raw,
                    "product": product_key,
                    "column": DEFAULT_CUSTOM_TAG_COLUMN,
                    "label": DEFAULT_CUSTOM_TAG_LABEL,
                    "module": _clean_tag_module(raw.get("module")),
                    "builtin": True,
                }
            continue
        seen.add(column_key)
        label = str(raw.get("label") or column).strip() or column
        out.append({**raw, "column": column, "label": label, "module": _clean_tag_module(raw.get("module"))})
    return out


def _custom_tag_label_map(product: str) -> dict[str, str]:
    return {c["column"]: c.get("label") or c["column"] for c in _custom_tag_columns_for_product(product)}


def _custom_tag_values_for_root(product: str, root_lot_id: str) -> dict[str, str]:
    data = _load_custom_tags_data()
    prefix = f"{product}|{root_lot_id}|"
    out: dict[str, str] = {}
    for key, raw in (data.get("values") or {}).items():
        if not str(key).startswith(prefix):
            continue
        parts = str(key).split("|", 3)
        if len(parts) != 4:
            continue
        value = raw.get("value") if isinstance(raw, dict) else raw
        if value is None:
            continue
        out["|".join(parts[1:])] = str(value)
    return out


def _custom_tag_colors_for_root(product: str, root_lot_id: str) -> dict[str, str]:
    data = _load_custom_tags_data()
    prefix = f"{product}|{root_lot_id}|"
    out: dict[str, str] = {}
    for key, raw_color in (data.get("colors") or {}).items():
        if not str(key).startswith(prefix):
            continue
        parts = str(key).split("|", 3)
        color = str(raw_color or "").strip().lower()
        if len(parts) == 4 and color in CUSTOM_TAG_COLOR_PALETTE:
            out["|".join(parts[1:])] = color
    return out


def _with_default_custom_tag(columns: list[str]) -> list[str]:
    """purpose TAG를 어떤 prefix/custom 보기에서도 항상 첫 TAG 후보로 유지한다."""
    return [DEFAULT_CUSTOM_TAG_COLUMN, *[
        c for c in (columns or []) if str(c or "").strip().upper() != DEFAULT_CUSTOM_TAG_COLUMN.upper()
    ]]


def _custom_tag_column_values(product: str, column: str, limit: int = 200) -> list[str]:
    data = _load_custom_tags_data()
    out: list[str] = []
    seen: set[str] = set()
    suffix = f"|{column}"
    prefix = f"{product}|"
    for key, raw in (data.get("values") or {}).items():
        if not str(key).startswith(prefix) or not str(key).endswith(suffix):
            continue
        value = raw.get("value") if isinstance(raw, dict) else raw
        if value is None:
            continue
        s = str(value).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= limit:
            break
    return out


# ── Management rows: runtime-only SplitTable row overlay ──────────────
def _management_rows_path() -> Path:
    return PLAN_DIR / "management_rows.json"


def _load_management_rows_data() -> dict:
    # cached — 위 _load_custom_tags_data 와 동일한 이유.
    data = load_json_cached(_management_rows_path(), {"columns": [], "values": {}})
    cleaned, changed = _clean_overlay_store_data(data, allow_management=True)
    if changed:
        _save_management_rows_data(cleaned)
    return cleaned


def _save_management_rows_data(data: dict) -> None:
    save_json(_management_rows_path(), {
        "columns": list(data.get("columns") or []),
        "values": dict(data.get("values") or {}),
    }, indent=2)


def _management_row_id(name: str) -> str:
    raw = str(name or "").strip()
    if raw.upper().startswith(f"{MANAGEMENT_ROW_PREFIX}_"):
        raw = raw[len(MANAGEMENT_ROW_PREFIX) + 1:].strip()
    token = safe_id(raw, max_len=72).strip().replace(" ", "_")
    token = "_".join(part for part in token.split("_") if part)
    if not token:
        raise HTTPException(400, "management row name required")
    return f"{MANAGEMENT_ROW_PREFIX}_{token}"


def _management_row_value_key(product: str, root_lot_id: str, wafer_id: str, column: str) -> str:
    return "|".join([str(product or ""), str(root_lot_id or ""), str(wafer_id or ""), str(column or "")])


def _ensure_management_row_column(data: dict, *, product: str, column: str, label: str, actor: str, now: str) -> dict:
    cols = data.setdefault("columns", [])
    product_key = str(product or "").strip()
    column_key = str(column or "").strip()
    existing = next((c for c in cols if c.get("product") == product_key and c.get("column") == column_key), None)
    if existing:
        existing["label"] = str(label or existing.get("label") or column_key).strip() or column_key
        existing["username"] = actor or existing.get("username", "")
        existing["updated"] = now
        return existing
    entry = {
        "product": product_key,
        "column": column_key,
        "label": str(label or column_key).strip() or column_key,
        "username": actor,
        "created": now,
        "updated": now,
    }
    cols.append(entry)
    return entry


def _management_row_columns_for_product(product: str) -> list[dict]:
    product_key = str(product or "").strip()
    data = _load_management_rows_data()
    out = []
    seen = set()
    for raw in data.get("columns") or []:
        if not isinstance(raw, dict):
            continue
        if raw.get("product") != product_key:
            continue
        column = str(raw.get("column") or "").strip()
        if not column or column in seen:
            continue
        seen.add(column)
        label = str(raw.get("label") or column).strip() or column
        out.append({**raw, "column": column, "label": label})
    return out


def _management_row_label_map(product: str) -> dict[str, str]:
    return {c["column"]: c.get("label") or c["column"] for c in _management_row_columns_for_product(product)}


def _management_row_values_for_root(product: str, root_lot_id: str) -> dict[str, str]:
    data = _load_management_rows_data()
    prefix = f"{product}|{root_lot_id}|"
    out: dict[str, str] = {}
    for key, raw in (data.get("values") or {}).items():
        if not str(key).startswith(prefix):
            continue
        parts = str(key).split("|", 3)
        if len(parts) != 4:
            continue
        value = raw.get("value") if isinstance(raw, dict) else raw
        if value is None:
            continue
        out["|".join(parts[1:])] = str(value)
    return out


def _management_row_column_values(product: str, column: str, limit: int = 200) -> list[str]:
    data = _load_management_rows_data()
    out: list[str] = []
    seen: set[str] = set()
    suffix = f"|{column}"
    prefix = f"{product}|"
    for key, raw in (data.get("values") or {}).items():
        if not str(key).startswith(prefix) or not str(key).endswith(suffix):
            continue
        value = raw.get("value") if isinstance(raw, dict) else raw
        if value is None:
            continue
        s = str(value).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= limit:
            break
    return out


# ── Notes (v8.4.9-b): 검색된 wafer 태그 + 파라미터 메모 ───────────────
# 스키마: {data_root}/splittable/notes.json
#   { "entries": [
#       { "id": "n_xxxxxx",
#         "scope": "wafer" | "param",
#         "key":  "{product}__{root_lot_id}__W{wafer_id}"
#               | "{product}__{root_lot_id}__W{wafer_id}__{param_name}",
#         "text": "...",
#         "username": "hol",
#         "created_at": "2026-04-21T10:00:00" }
#     ] }
# 작성자 또는 admin 만 삭제 가능. 수정은 지원하지 않음 (메모 히스토리 유지).
def _load_notes() -> list:
    data = load_json(NOTES_FILE, {"entries": []})
    if isinstance(data, dict):
        return data.get("entries", [])
    return data if isinstance(data, list) else []


def _save_notes(entries: list) -> None:
    save_json(NOTES_FILE, {"entries": entries})


def _new_note_id() -> str:
    import secrets as _secrets
    return "n_" + _secrets.token_hex(5)


def _notes_key_wafer(product: str, root_lot_id: str, wafer_id) -> str:
    return f"{product}__{root_lot_id}__W{wafer_id}"


def _notes_key_param(product: str, root_lot_id: str, wafer_id, param: str) -> str:
    return f"{product}__{root_lot_id}__W{wafer_id}__{param}"


def _notes_key_lot(product: str, root_lot_id: str) -> str:
    """v8.7.8: LOT 단위 노트 (해당 root_lot_id 전역). param 태그와 달리 lot 에 묶임."""
    return f"{product}__LOT__{root_lot_id}"


def _notes_key_param_global(product: str, param: str) -> str:
    """v8.7.8: parameter 전역 태그 — product 내 모든 LOT 에서 동일 parameter 에 노출."""
    return f"{product}__PARAM__{param}"


def _notes_lot_prefix(product: str, root_lot_id: str) -> str:
    return f"{product}__{root_lot_id}__"


def _notes_product_param_prefix(product: str) -> str:
    return f"{product}__PARAM__"


def _notes_product_lot_prefix(product: str) -> str:
    return f"{product}__LOT__"


class NoteSaveReq(BaseModel):
    scope: str                 # "wafer" | "param" | "lot" | "param_global"
    product: str = ""
    root_lot_id: str = ""
    wafer_id: str = ""
    param: str = ""            # scope == "param" / "param_global" 일 때
    text: str
    username: str = ""
    images: list[dict] = Field(default_factory=list)


class NoteDeleteReq(BaseModel):
    id: str
    username: str = ""


class NoteCommentReq(BaseModel):
    note_id: str
    text: str = ""
    username: str = ""
    images: list[dict] = Field(default_factory=list)


def _clean_note_text(text: str) -> str:
    return (text or "").replace("\u200b", "").strip()


def _normalize_note_image(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    url = (
        raw.get("url")
        or raw.get("downloadUrl")
        or raw.get("fileUrl")
        or ((raw.get("attachment") or {}).get("downloadUrl") if isinstance(raw.get("attachment"), dict) else "")
        or ((raw.get("file") or {}).get("fileUrl") if isinstance(raw.get("file"), dict) else "")
    )
    url = str(url or "").strip().split("?", 1)[0]
    if url.startswith("api/informs/files/"):
        url = "/" + url
    elif url.startswith("files/"):
        url = "/api/informs/" + url
    if not url.startswith("/api/informs/files/"):
        return None
    filename = (
        raw.get("filename")
        or raw.get("name")
        or raw.get("displayName")
        or Path(url).name
        or "image"
    )
    try:
        size = int(raw.get("size") or raw.get("bytes") or 0)
    except Exception:
        size = 0
    return {"filename": Path(str(filename)).name or "image", "url": url, "size": max(0, size)}


def _normalize_note_images(images) -> list[dict]:
    out = []
    seen = set()
    for raw in images or []:
        item = _normalize_note_image(raw)
        if not item:
            continue
        key = item["url"]
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out[:12]


def _normalize_note_entry(entry: dict) -> dict:
    e = dict(entry or {})
    e["text"] = _clean_note_text(str(e.get("text") or ""))
    e["images"] = _normalize_note_images(e.get("images") or [])
    comments = []
    for raw in e.get("comments") or []:
        if not isinstance(raw, dict):
            continue
        c = dict(raw)
        c["text"] = _clean_note_text(str(c.get("text") or ""))
        c["images"] = _normalize_note_images(c.get("images") or [])
        comments.append(c)
    e["comments"] = comments
    return e


def _note_scope_parts(entry: dict) -> tuple[str, str, str]:
    key = str(entry.get("key") or "")
    scope = entry.get("scope")
    parts = key.split("__")
    if scope == "lot" and len(parts) >= 3:
        return parts[0], parts[2], ""
    if scope in ("wafer", "param") and len(parts) >= 3:
        return parts[0], parts[1], str(parts[2]).replace("W", "", 1)
    return "", "", ""


def _append_splittable_note_knowledge(entry: dict, *, actor: str, text: str) -> None:
    try:
        from core import knowledge_impact
        product, root_lot_id, wafer_id = _note_scope_parts(entry)
        param = ""
        key = str(entry.get("key") or "")
        parts = key.split("__")
        if entry.get("scope") in {"param", "param_global"}:
            param = parts[-1] if parts else ""
        knowledge_impact.append_candidates_from_text(
            text,
            source_type="split_note",
            source_id=entry.get("id") or "",
            actor=actor,
            context={
                "product": product,
                "root_lot_id": root_lot_id,
                "wafer_id": wafer_id,
                "item_id": param,
                "knob_name": param if str(param).upper().startswith(("KNOB_", "MASK_")) else "",
                "source_refs": [{"type": "split_note", "id": entry.get("id") or "", "label": param or root_lot_id}],
            },
            allowed_event_types={"split_impact"},
            status="candidate",
            title_prefix="SplitTable",
        )
    except Exception:
        return


def _append_splittable_plan_knowledge(*, product: str, cell_key: str, old: Any, new: Any, actor: str, changed_at: str, conflicting: bool = False) -> None:
    try:
        from core import knowledge_impact
        parts = str(cell_key or "").split("|")
        root = parts[0] if len(parts) > 0 else ""
        wafer = parts[1] if len(parts) > 1 else ""
        col = parts[2] if len(parts) > 2 else ""
        if not col:
            return
        knowledge_impact.safe_append_domain_event(
            event_type="split_impact",
            source_type="split_note",
            source_id=f"{product}:{cell_key}",
            title="SplitTable plan impact candidate",
            summary=f"SplitTable plan changed {product} {cell_key}: {old} -> {new}",
            actor=actor,
            payload={
                "product": product,
                "root_lot_id": root,
                "wafer_id": wafer,
                "item_id": col,
                "knob_name": col if str(col).upper().startswith(("KNOB_", "MASK_")) else "",
                "split_value": "" if new is None else str(new),
                "previous_split_value": "" if old is None else str(old),
                "effect_direction": "unknown",
                "effect_confidence": "candidate",
                "status": "candidate",
                "changed_at": changed_at,
                "conflicting_evidence": bool(conflicting),
                "source_refs": [{"type": "split_plan", "id": cell_key, "label": f"{old} -> {new}"}],
            },
        )
    except Exception:
        return


def _split_plan_cell_key(cell_key: str) -> tuple[str, str, str]:
    parts = str(cell_key or "").split("|", 2)
    root = parts[0] if len(parts) > 0 else ""
    wafer = parts[1] if len(parts) > 1 else ""
    column = parts[2] if len(parts) > 2 else ""
    return root, wafer, column


def _plan_actual_mismatch(plan: Any, actual: Any) -> bool:
    plan_text = _clean_str(plan)
    actual_text = _clean_str(actual)
    return bool(plan_text and actual_text and plan_text != actual_text)


def _plan_mismatch_alert_key(cell_key: str, plan: Any, actual: Any) -> str:
    raw = json.dumps(
        {"cell": str(cell_key or ""), "plan": _clean_str(plan), "actual": _clean_str(actual)},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def _actual_value_for_plan_cell(product: str, cell_key: str) -> str:
    root, wafer, column = _split_plan_cell_key(cell_key)
    if not root or not wafer or not column:
        return ""
    try:
        lf = _scan_product(product, root_lot_id=root, wafer_ids=wafer)
        lot_col, wf_col = _detect_lot_wafer(lf, product)
        lf = _filter_lot_wafer(lf, lot_col, wf_col, root, wafer)
        names = lf.collect_schema().names()
        actual_col = column if column in names else (_ci_resolve_in(column, names) or "")
        if not actual_col:
            return ""
        df = (
            lf.select(pl.col(actual_col).cast(_STR, strict=False).alias("actual"))
            .drop_nulls()
            .head(1)
            .collect()
        )
        if df.height == 0:
            return ""
        return _clean_str(df.item(0, 0))
    except Exception:
        return ""


def _product_mismatch_group_members(product: str) -> list[str]:
    """제품명과 이름이 같은(대소문자 무시) 관리자 그룹의 멤버 목록.

    SplitTable 제품은 ML_TABLE_<PROD> 형태라 prefix 를 뗀 이름도 같이 매칭한다.
    """
    raw = str(product or "").strip()
    if not raw:
        return []
    names = {raw.lower()}
    if raw.upper().startswith("ML_TABLE_"):
        tail = raw[len("ML_TABLE_"):].strip()
        if tail:
            names.add(tail.lower())
    try:
        from routers.groups import _load as _load_groups
        members: list[str] = []
        for g in _load_groups():
            if not isinstance(g, dict):
                continue
            if str(g.get("name") or "").strip().lower() not in names:
                continue
            for m in g.get("members") or []:
                username = str(m or "").strip()
                if username and username not in members:
                    members.append(username)
        return members
    except Exception:
        return []


def _send_plan_mismatch_mail(product: str, items: list[dict], targets: list[str], actor: str = "flow") -> None:
    """plan/actual 불일치 확인 요청 메일 — 비밀번호 찾기 메일과 같은 방식(from_addr 발신, HTML 본문)."""
    if not items or not targets:
        return
    try:
        import html as _html
        from core.mail import load_mail_cfg, send_mail
        try:
            sender = (load_mail_cfg().get("from_addr") or "").strip()
        except Exception:
            sender = ""
        rows = []
        for it in items[:50]:
            where = _html.escape(str(it.get("root") or ""))
            wafer = str(it.get("wafer") or "").strip()
            if wafer:
                where += f" WF{_html.escape(wafer)}"
            when = str(it.get("updated") or "").strip()[:16].replace("T", " ")
            rows.append(
                "<li style='margin:2px 0'>"
                f"<b>{where}</b> · {_html.escape(str(it.get('column') or ''))} — "
                f"<b>{_html.escape(str(it.get('owner') or ''))}</b> 님이 plan "
                f"<b>{_html.escape(str(it.get('plan') or ''))}</b> 적용"
                + (f" ({_html.escape(when)})" if when else "")
                + f" → 실제로는 <b>{_html.escape(str(it.get('actual') or ''))}</b> 로 진행됨"
                "</li>"
            )
        content = (
            "<div style='font-family:Arial,sans-serif;font-size:14px;line-height:1.6'>"
            f"<p><b>{_html.escape(str(product))}</b> SplitTable 에서 계획(plan)과 실제 진행이 다른 항목이 확인되었습니다.</p>"
            f"<ul style='padding-left:18px'>{''.join(rows)}</ul>"
            "<p>실제 진행 내용이 맞는지 확인해 주세요.</p>"
            "<p style='color:#666;font-size:12px'>본 메일은 동일 불일치 건에 대해 1회만 발송됩니다.</p>"
            "</div>"
        )
        send_mail(
            sender_username=sender or (actor or "flow"),
            receiver_usernames=list(targets),
            extra_emails=[],
            title=f"[flow] plan/actual 불일치 확인 요청 - {product}",
            content=content,
        )
    except Exception:
        logger.debug("mismatch mail send failed product=%s", product, exc_info=True)


def _notify_plan_actual_mismatches_once(product: str, mismatches: list[dict], actor: str = "flow") -> int:
    if not mismatches:
        return 0
    try:
        from core.notify import emit_event
        data = _load_plan_data(product)
        plans = data.get("plans") if isinstance(data.get("plans"), dict) else {}
        alerts = data.get("mismatch_alerts") if isinstance(data.get("mismatch_alerts"), dict) else {}
        mail_enabled = False
        try:
            _cfg = load_json(SOURCE_CFG, {}) or {}
            mail_enabled = bool(_cfg.get("mismatch_mail_enabled"))
        except Exception:
            mail_enabled = False
        # 수신 대상 = 계획 작성자 + 제품명과 동일한 이름의 그룹 멤버.
        group_recipients = _product_mismatch_group_members(product)
        sent = 0
        ledger_changed = False
        mail_items: list[dict] = []
        mail_targets: list[str] = []
        for mm in mismatches[:100]:
            cell_key = str(mm.get("key") or mm.get("cell") or "")
            if not cell_key:
                continue
            plan = mm.get("plan")
            actual = mm.get("actual")
            if not _plan_actual_mismatch(plan, actual):
                continue
            plan_info = plans.get(cell_key) if isinstance(plans.get(cell_key), dict) else {}
            owner = str(mm.get("plan_user") or plan_info.get("user") or "").strip()
            targets: list[str] = []
            if owner:
                targets.append(owner)
            for name in group_recipients:
                if name not in targets:
                    targets.append(name)
            alert_key = _plan_mismatch_alert_key(cell_key, plan, actual)
            root, wafer, column = _split_plan_cell_key(cell_key)
            payload = {
                "product": product,
                "cell": cell_key,
                "root_lot_id": root,
                "wafer_id": wafer,
                "column": column,
                "plan": _clean_str(plan),
                "actual": _clean_str(actual),
                "plan_updated": plan_info.get("updated") or mm.get("plan_updated") or "",
            }
            # 알림 수신자가 없거나 emit_event가 실패해도 매칭알람의
            # 'SplitTable plan 이상항목들'에는 남아야 한다. 수신자별 알림 dedupe와
            # 분리된 canonical 장부를 두고, 같은 cell의 더 오래된 actual 기록은
            # 새 불일치로 교체한다.
            ledger_key = f"{alert_key}|plan_knob_ledger"
            for old_key, old_value in list(alerts.items()):
                if (str(old_key).endswith("|plan_knob_ledger")
                        and isinstance(old_value, dict)
                        and str(old_value.get("cell") or "") == cell_key
                        and old_key != ledger_key):
                    alerts.pop(old_key, None)
                    ledger_changed = True
            prior_ledger = alerts.get(ledger_key) if isinstance(alerts.get(ledger_key), dict) else {}
            ledger_value = {
                "time": prior_ledger.get("time") or datetime.datetime.now().isoformat(),
                "target_user": "",
                "ledger": "plan_knob",
                **payload,
            }
            if alerts.get(ledger_key) != ledger_value:
                alerts[ledger_key] = ledger_value
                ledger_changed = True
            if not targets:
                continue
            alert_body = (
                f"! {product}/{root}"
                + (f" WF{wafer}" if wafer else "")
                + f" {column}: [plan] {payload['plan']} → [actual] {payload['actual']}"
            )
            new_targets: list[str] = []
            for target in targets:
                # 작성자는 기존 key 형식 유지(중복 재알람 방지), 팀 수신자는 사용자별 key.
                target_alert_key = alert_key if target == owner else f"{alert_key}|u:{target}"
                if target_alert_key in alerts:
                    continue
                ok = emit_event(
                    "my_plan_actual_mismatch",
                    actor=actor or "flow",
                    target_user=target,
                    title="[plan/actual 불일치]",
                    body=alert_body,
                    payload=payload,
                )
                if not ok:
                    continue
                alerts[target_alert_key] = {
                    "time": datetime.datetime.now().isoformat(),
                    "target_user": target,
                    **payload,
                }
                sent += 1
                new_targets.append(target)
            if new_targets:
                mail_items.append({
                    "owner": owner or "(작성자 미상)",
                    "root": root,
                    "wafer": wafer,
                    "column": column,
                    "plan": payload["plan"],
                    "actual": payload["actual"],
                    "updated": payload["plan_updated"],
                })
                for t in new_targets:
                    if t not in mail_targets:
                        mail_targets.append(t)
        if sent or ledger_changed:
            if len(alerts) > 2000:
                for old_key in list(alerts.keys())[: len(alerts) - 2000]:
                    alerts.pop(old_key, None)
            data["mismatch_alerts"] = alerts
            save_json(_plan_history_path(product), data)
            # 톱니바퀴 설정에서 켠 경우에만 메일 발송 — 장부 저장 뒤라 동일 건은 1회만 나간다.
            if mail_enabled:
                _send_plan_mismatch_mail(product, mail_items, mail_targets, actor=actor)
        return sent
    except Exception:
        return 0


def _mismatch_notify_pending_key(product: str, mismatch: dict) -> tuple:
    return (
        str(product or "").strip(),
        str(mismatch.get("key") or mismatch.get("cell") or ""),
        _clean_str(mismatch.get("plan")),
        _clean_str(mismatch.get("actual")),
    )


def _mismatch_notify_worker() -> None:
    while True:
        _MISMATCH_NOTIFY_WAKE.wait(_MISMATCH_NOTIFY_DEBOUNCE_SEC)
        _MISMATCH_NOTIFY_WAKE.clear()
        with _MISMATCH_NOTIFY_LOCK:
            items = list(_MISMATCH_NOTIFY_PENDING.values())
            _MISMATCH_NOTIFY_PENDING.clear()
        if not items:
            with _MISMATCH_NOTIFY_LOCK:
                if not _MISMATCH_NOTIFY_PENDING:
                    return
            continue
        grouped: dict[tuple[str, str], list[dict]] = {}
        for item in items:
            grouped.setdefault((item["product"], item["actor"]), []).append(dict(item["mismatch"]))
        for (product, actor), batch in grouped.items():
            try:
                _notify_plan_actual_mismatches_once(product, batch, actor=actor)
            except Exception:
                logger.debug("background mismatch notification failed product=%s", product, exc_info=True)


def _enqueue_plan_actual_mismatches(product: str, mismatches: list[dict], actor: str = "flow") -> None:
    if not mismatches:
        return
    global _MISMATCH_NOTIFY_THREAD
    with _MISMATCH_NOTIFY_LOCK:
        for mm in mismatches[:100]:
            key = _mismatch_notify_pending_key(product, mm)
            if not key[1]:
                continue
            _MISMATCH_NOTIFY_PENDING[key] = {
                "product": str(product or "").strip(),
                "actor": actor or "flow",
                "mismatch": dict(mm),
            }
            _MISMATCH_NOTIFY_PENDING.move_to_end(key)
            while len(_MISMATCH_NOTIFY_PENDING) > _MISMATCH_NOTIFY_PENDING_MAX:
                _MISMATCH_NOTIFY_PENDING.popitem(last=False)
        if _MISMATCH_NOTIFY_THREAD is None or not _MISMATCH_NOTIFY_THREAD.is_alive():
            _MISMATCH_NOTIFY_THREAD = threading.Thread(
                target=_mismatch_notify_worker,
                name="splittable-mismatch-notify",
                daemon=True,
            )
            _MISMATCH_NOTIFY_THREAD.start()
    _MISMATCH_NOTIFY_WAKE.set()


def _drain_plan_actual_mismatch_notifications_for_tests(timeout: float = 2.0) -> None:
    deadline = time.monotonic() + max(0.1, float(timeout or 0.0))
    while time.monotonic() < deadline:
        with _MISMATCH_NOTIFY_LOCK:
            thread = _MISMATCH_NOTIFY_THREAD
            pending = bool(_MISMATCH_NOTIFY_PENDING)
        if not pending and (thread is None or not thread.is_alive()):
            return
        if thread is not None:
            thread.join(timeout=0.05)
        else:
            time.sleep(0.05)


def _notify_tracker_owner_for_note(entry: dict, actor: str) -> None:
    try:
        from core.notify import emit_event
        from core.mail import send_mail
        product, root_lot_id, wafer_id = _note_scope_parts(entry)
        if not product or not root_lot_id:
            return
        tracker_items = load_json(TRACKER_ISSUES_FILE, [])
        for issue in tracker_items or []:
            base_target = str(issue.get("username") or "").strip()
            if not base_target or base_target == actor:
                continue
            matched = False
            for row in issue.get("lots") or []:
                row_product = str(row.get("product") or issue.get("product") or "")
                row_root = str(row.get("root_lot_id") or "")
                row_wafer = _normalize_wafer_id(row.get("wafer_id"))
                if row_product and row_product not in (product, product.replace("ML_TABLE_", "")):
                    continue
                if not _root_lot_matches(row_root, root_lot_id):
                    continue
                if wafer_id and row_wafer and row_wafer != _normalize_wafer_id(wafer_id):
                    continue
                matched = True
                break
            if not matched:
                continue
            title = f"FLOW 알림 - {issue.get('title') or issue.get('id') or 'SplitTable note'}"
            body = f"{actor} 님이 SplitTable 노트를 추가했습니다. lot={root_lot_id}" + (f" wf={wafer_id}" if wafer_id else "")
            emit_event(
                "my_tracker_lot_note",
                actor=actor,
                target_user=base_target,
                title=title,
                body=body,
                payload={"issue_id": issue.get("id"), "product": product, "root_lot_id": root_lot_id, "wafer_id": wafer_id, "note_id": entry.get("id")},
            )
            mail_watch = issue.get("mail_watch") if isinstance(issue.get("mail_watch"), dict) else {}
            if mail_watch.get("enabled"):
                send_mail(
                    sender_username=actor or "flow",
                    receiver_usernames=[base_target],
                    extra_emails=[],
                    title=title,
                    content=body,
                )
    except Exception:
        pass


@router.get("/notes")
def list_notes(product: str = Query(""), root_lot_id: str = Query(""), username: str = Query("")):
    """필터:
      - product+root_lot_id → (wafer + param + lot) for that lot
        PLUS param_global for the product (전역 태그는 모든 LOT 에서 공통 노출)
      - product only → product 전역 (param_global + lot 전체)
      - 없으면 전체
    """
    entries = _load_notes()
    if product and root_lot_id:
        lot_pfx = _notes_lot_prefix(product, root_lot_id)
        lot_key = _notes_key_lot(product, root_lot_id)
        pg_pfx = _notes_product_param_prefix(product)
        def _match(e):
            k = str(e.get("key", ""))
            sc = e.get("scope")
            if sc == "wafer" and k.startswith(lot_pfx):
                return True
            if sc == "param" and k.startswith(lot_pfx):
                return True
            if sc == "lot" and k == lot_key:
                return True
            if sc == "param_global" and k.startswith(pg_pfx):
                return True
            return False
        entries = [e for e in entries if _match(e)]
    elif product:
        pg_pfx = _notes_product_param_prefix(product)
        lot_pfx = _notes_product_lot_prefix(product)
        entries = [e for e in entries
                   if str(e.get("key", "")).startswith(pg_pfx) or str(e.get("key", "")).startswith(lot_pfx)]
    entries = [_normalize_note_entry(e) for e in entries]
    entries.sort(key=lambda e: e.get("created_at", ""), reverse=True)
    return {"notes": entries, "total": len(entries)}


@router.post("/notes/save")
def save_note(req: NoteSaveReq, request: Request):
    from core.auth import current_user as _cu
    me = _cu(request)
    username = me.get("username") or req.username or "anonymous"
    scope = (req.scope or "").strip()
    if scope not in ("wafer", "param", "lot", "param_global"):
        raise HTTPException(400, "scope must be 'wafer'|'param'|'lot'|'param_global'")
    images = _normalize_note_images(req.images)
    text = _clean_note_text(req.text)
    if not text and not images:
        raise HTTPException(400, "empty text")
    if len(text) > 2000:
        raise HTTPException(400, "text too long (max 2000 chars)")
    if not req.product:
        raise HTTPException(400, "product required")
    if scope == "wafer":
        if not req.root_lot_id or not str(req.wafer_id or "").strip():
            raise HTTPException(400, "root_lot_id/wafer_id required for wafer scope")
        key = _notes_key_wafer(req.product, req.root_lot_id, req.wafer_id)
    elif scope == "param":
        if not req.root_lot_id or not str(req.wafer_id or "").strip() or not req.param:
            raise HTTPException(400, "root_lot_id/wafer_id/param required for param scope")
        key = _notes_key_param(req.product, req.root_lot_id, req.wafer_id, req.param)
    elif scope == "lot":
        if not req.root_lot_id:
            raise HTTPException(400, "root_lot_id required for lot scope")
        key = _notes_key_lot(req.product, req.root_lot_id)
    else:  # param_global
        if not req.param:
            raise HTTPException(400, "param required for param_global scope")
        key = _notes_key_param_global(req.product, req.param)
    entry = {
        "id": _new_note_id(),
        "scope": scope,
        "key": key,
        "text": text,
        "images": images,
        "comments": [],
        "username": username,
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    entries = _load_notes()
    entries.append(entry)
    _save_notes(entries)
    _append_splittable_note_knowledge(entry, actor=username, text=text)
    _notify_tracker_owner_for_note(entry, username)
    return {"ok": True, "entry": entry}


@router.post("/notes/comment")
def add_note_comment(req: NoteCommentReq, request: Request):
    from core.auth import current_user as _cu
    me = _cu(request)
    username = me.get("username") or req.username or "anonymous"
    text = _clean_note_text(req.text)
    images = _normalize_note_images(req.images)
    if not text and not images:
        raise HTTPException(400, "empty text")
    entries = _load_notes()
    target = next((e for e in entries if e.get("id") == req.note_id), None)
    if not target:
        raise HTTPException(404, "note not found")
    comment = {
        "id": "c_" + datetime.datetime.now().strftime("%y%m%d%H%M%S%f"),
        "text": text,
        "images": images,
        "username": username,
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    target.setdefault("comments", []).append(comment)
    _save_notes(entries)
    _append_splittable_note_knowledge(target, actor=username, text=text)
    return {"ok": True, "comment": comment}


@router.post("/notes/delete")
def delete_note(req: NoteDeleteReq, request: Request):
    from core.auth import current_user as _cu
    me = _cu(request)
    username = me.get("username") or ""
    role = me.get("role") or ""
    entries = _load_notes()
    target = next((e for e in entries if e.get("id") == req.id), None)
    if not target:
        raise HTTPException(404, "note not found")
    if role != "admin" and target.get("username") != username:
        raise HTTPException(403, "only author or admin can delete")
    entries = [e for e in entries if e.get("id") != req.id]
    _save_notes(entries)
    return {"ok": True}


def _normalize_wafer_id(raw, *, max_wafer: int = SPLITTABLE_MAX_WAFER_ID) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    core = _re.sub(r"^(?:#|WAFER|WF|W)\s*", "", text, flags=_re.I).strip()
    if not _re.fullmatch(r"\d+", core):
        return ""
    try:
        n = int(core)
    except Exception:
        return ""
    return str(n) if 1 <= n <= max_wafer else ""


def _wafer_filter_set(raw: str) -> set[str]:
    out = set()
    for part in (raw or "").split(","):
        s = str(part).strip()
        if not s:
            continue
        norm = _normalize_wafer_id(s)
        if norm:
            out.add(norm)
    return {v for v in out if v not in ("", "None", "null")}


def _wafer_matches(wafer_value, wafer_set: set[str]) -> bool:
    if not wafer_set:
        return True
    s = _normalize_wafer_id(wafer_value)
    return bool(s and s in wafer_set)


def _scope_label(has_wafer: bool) -> str:
    return "wafer" if has_wafer else "lot"


def _root_lot_matches(candidate, root_lot_id: str) -> bool:
    cand = str(candidate or "").strip()
    root = str(root_lot_id or "").strip()
    if not cand or not root:
        return False
    if cand == root:
        return True
    # Legacy tracker/inform entries sometimes stored only the old 5-char root.
    if len(cand) <= 5 and root.startswith(cand):
        return True
    if len(root) <= 5 and cand.startswith(root):
        return True
    return False


def _lot_or_fab_matches_root(value, root_lot_id: str) -> bool:
    text = str(value or "").strip()
    root = str(root_lot_id or "").strip()
    if not text or not root:
        return False
    return text == root or text.startswith(root)


def _load_operational_history(product: str, root_lot_id: str, wafer_ids: str,
                              username: str, role: str) -> list[dict]:
    if not root_lot_id:
        return []
    wafer_set = _wafer_filter_set(wafer_ids)
    out: list[dict] = []
    try:
        from routers.groups import filter_by_visibility
    except Exception:
        def filter_by_visibility(items, username, role, key="group_ids"):
            return []

    tracker_items = filter_by_visibility(load_json(TRACKER_ISSUES_FILE, []), username, role, key="group_ids")
    for issue in tracker_items or []:
        matched_rows = []
        for row in (issue.get("lots") or []):
            rid = str(row.get("root_lot_id") or "").strip()
            lot_value = str(row.get("lot_id") or "").strip()
            if not (_root_lot_matches(rid, root_lot_id) or _lot_or_fab_matches_root(lot_value, root_lot_id)):
                continue
            wafer_val = str(row.get("wafer_id") or "").strip()
            if wafer_val and not _wafer_matches(wafer_val, wafer_set):
                continue
            if not wafer_val and wafer_set:
                continue
            matched_rows.append(row)
        if not matched_rows:
            continue
        for row in matched_rows:
            out.append({
                "source": "tracker",
                "scope": _scope_label(bool(str(row.get("wafer_id") or "").strip())),
                "time": issue.get("updated_at") or issue.get("created") or issue.get("timestamp") or "",
                "author": issue.get("username") or "",
                "title": issue.get("title") or "(untitled issue)",
                "detail": row.get("comment") or "",
                "status": issue.get("status") or "",
                "category": issue.get("category") or "",
                "root_lot_id": root_lot_id,
                "wafer_id": str(row.get("wafer_id") or ""),
                "lot_id": row.get("lot_id") or "",
                "ref_id": issue.get("id") or "",
            })
        for cm in (issue.get("comments") or []):
            for row in matched_rows:
                out.append({
                    "source": "tracker_comment",
                    "scope": _scope_label(bool(str(row.get("wafer_id") or "").strip())),
                    "time": cm.get("created_at") or "",
                    "author": cm.get("username") or "",
                    "title": issue.get("title") or "(issue comment)",
                    "detail": cm.get("text") or "",
                    "status": issue.get("status") or "",
                    "category": issue.get("category") or "",
                    "root_lot_id": root_lot_id,
                    "wafer_id": str(row.get("wafer_id") or ""),
                    "lot_id": row.get("lot_id") or "",
                    "ref_id": issue.get("id") or "",
                })

    inform_items = filter_by_visibility(load_json(INFORMS_FILE, []), username, role, key="group_ids")
    for inf in inform_items or []:
        inf_root = str(inf.get("root_lot_id") or "").strip()
        inf_lot = str(inf.get("lot_id") or "").strip()
        inf_fab = str(inf.get("fab_lot_id_at_save") or inf.get("lot_id") or "").strip()
        fab_parts = [p.strip() for p in inf_fab.split(",") if p.strip()]
        if not (
            _root_lot_matches(inf_root, root_lot_id)
            or _lot_or_fab_matches_root(inf_lot, root_lot_id)
            or any(_lot_or_fab_matches_root(part, root_lot_id) for part in fab_parts)
        ):
            continue
        inf_wafer = str(inf.get("wafer_id") or "").strip()
        if inf_wafer and not _wafer_matches(inf_wafer, wafer_set):
            continue
        if not inf_wafer and wafer_set:
            continue
        out.append({
            "source": "inform",
            "scope": _scope_label(bool(inf_wafer)),
            "time": inf.get("created_at") or "",
            "author": inf.get("author") or "",
            "title": f"{inf.get('module') or 'INFO'} · {inf.get('reason') or ''}".strip(" ·"),
            "detail": inf.get("text") or "",
            "status": inf.get("flow_status") or ("completed" if inf.get("checked") else "received"),
            "category": "inform",
            "root_lot_id": root_lot_id,
            "wafer_id": inf_wafer,
            "lot_id": inf.get("lot_id") or "",
            "ref_id": inf.get("id") or "",
        })
    out.sort(key=lambda x: x.get("time") or "", reverse=True)
    return out[:300]


def _issue_comment_count(issue: dict) -> int:
    total = 0
    for cm in issue.get("comments") or []:
        if not isinstance(cm, dict):
            continue
        total += 1
        total += len([r for r in (cm.get("replies") or []) if isinstance(r, dict)])
    return total


def _product_matches_issue(product: str, issue: dict, row: dict) -> bool:
    aliases = _product_aliases(product)
    if not aliases:
        return True
    values = [
        issue.get("product"),
        row.get("product"),
        row.get("monitor_prod"),
        row.get("prod"),
    ]
    candidates = {str(v or "").strip().upper() for v in values if str(v or "").strip()}
    if not candidates:
        return True
    return bool(candidates & aliases)


def _related_tracker_issues(product: str, root_lot_id: str,
                            username: str = "", role: str = "user",
                            limit: int = 8) -> list[dict]:
    root = str(root_lot_id or "").strip()
    if not root:
        return []
    try:
        from routers.groups import filter_by_visibility
    except Exception:
        def filter_by_visibility(items, username, role, key="group_ids"):
            return []
    try:
        tracker_items = filter_by_visibility(load_json(TRACKER_ISSUES_FILE, []), username, role, key="group_ids")
    except Exception:
        tracker_items = []
    out: list[dict] = []
    for issue in tracker_items or []:
        matched_lots = []
        matched_wafers = []
        for row in (issue.get("lots") or []):
            rid = str(row.get("root_lot_id") or "").strip()
            lot_value = str(row.get("lot_id") or "").strip()
            if not (_root_lot_matches(rid, root) or _lot_or_fab_matches_root(lot_value, root)):
                continue
            if not _product_matches_issue(product, issue, row):
                continue
            matched_lots.append(lot_value or rid or root)
            wafer = str(row.get("wafer_id") or "").strip()
            if wafer:
                matched_wafers.append(wafer)
        if not matched_lots:
            continue
        out.append({
            "id": issue.get("id") or "",
            "title": issue.get("title") or "(untitled issue)",
            "status": issue.get("status") or "",
            "category": issue.get("category") or "",
            "priority": issue.get("priority") or "",
            "username": issue.get("username") or "",
            "updated_at": issue.get("updated_at") or issue.get("created") or issue.get("timestamp") or "",
            "matched_lots": sorted({v for v in matched_lots if v}),
            "matched_wafers": sorted({v for v in matched_wafers if v}, key=_natural_param_key),
            "comment_count": _issue_comment_count(issue),
        })
    out.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    return out[:max(1, min(20, int(limit or 8)))]


# ── Products / schema ──
# v8.8.3: SplitTable 의 "제품" = 오직 ML_TABLE_* 파일로 한정.
#   - 기존에는 DB hive 테이블(FAB/INLINE/ET/EDS)과 레거시 루트 파일도 노출되어
#     실제 검색 가능한 테이블셋이 혼탁했다.
#   - 신규 요청: "검색되는 테이블셋 = ML_TABLE_~~" prefix 로 시작하는 단일 파일만.
#   - DB 하위 제품 폴더는 /fab-roots / /ml-table-match 가 따로 노출 → 오버라이드용 소스.
