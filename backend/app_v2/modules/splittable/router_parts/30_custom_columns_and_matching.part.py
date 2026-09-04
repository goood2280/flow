PRECISION_CFG = PLAN_DIR / "precision_config.json"
DEFAULT_PRECISION = {"INLINE": 2, "VM": 2}
DISPLAY_SETTINGS_CFG = PLAN_DIR / "display_settings.json"
DEFAULT_SPLITTABLE_COLUMN_WIDTHS = {
    "module": 86,
    "step_id": 168,
    "step_desc": 180,
    "item": 288,
    "value": 140,
    "split": 80,
    "wafer": 115,
}


def _normalize_splittable_column_widths(raw) -> dict:
    source = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULT_SPLITTABLE_COLUMN_WIDTHS)
    for key, default in DEFAULT_SPLITTABLE_COLUMN_WIDTHS.items():
        try:
            value = int(source.get(key, default))
        except (TypeError, ValueError):
            value = default
        out[key] = max(48, min(640, value))
    return out


@router.get("/display-settings")
def get_display_settings():
    saved = load_json(DISPLAY_SETTINGS_CFG, {})
    return {"column_widths": _normalize_splittable_column_widths(saved)}


class DisplaySettingsReq(BaseModel):
    column_widths: dict = {}


@router.post("/display-settings/save")
def save_display_settings(req: DisplaySettingsReq, _perm=Depends(require_page_manager("splittable"))):
    widths = _normalize_splittable_column_widths(req.column_widths)
    save_json(DISPLAY_SETTINGS_CFG, widths)
    return {"ok": True, "column_widths": widths}


@router.get("/precision")
def get_precision():
    return {"precision": load_json(PRECISION_CFG, DEFAULT_PRECISION)}


class PrecisionReq(BaseModel):
    precision: dict   # {"INLINE": 2, "VM": 3, ...}


@router.post("/precision/save")
def save_precision(req: PrecisionReq, _perm=Depends(require_page_manager("splittable"))):
    # Sanitize: ensure int 0..10 per prefix
    out = {}
    for k, v in (req.precision or {}).items():
        if not isinstance(k, str) or not k.strip():
            continue
        try:
            n = int(v)
        except Exception:
            continue
        n = max(0, min(10, n))
        out[k.strip().upper()] = n
    save_json(PRECISION_CFG, out)
    return {"ok": True, "precision": out}


# ── v8.8.6: Paste sets (팀 공용 — 인폼·SplitTable paste 공유) ──────────────
# Schema: [{id, name, product, columns:[...], rows:[[...]], username, created, updated}]
#   - CUSTOM 탭에서 paste 세트를 직접 columns 로 취급 → as-is 뷰 (SplitTable custom 과 별개 보관).
#   - FE 는 로컬스토리지 대신 이 엔드포인트에서 읽고 씀. 로컬 폴백은 FE 가 알아서.
def _load_paste_sets() -> list:
    data = load_json(PASTE_SETS_FILE, [])
    return data if isinstance(data, list) else []

def _save_paste_sets(items: list) -> None:
    save_json(PASTE_SETS_FILE, items, indent=2)


class PasteSetSaveReq(BaseModel):
    name: str
    product: str = ""
    columns: List[str]
    rows: List[List] = []
    username: str = ""


@router.get("/paste-sets")
def list_paste_sets(product: str = Query("")):
    """팀 공용 paste 세트 목록. product 가 주어지면 해당 product 또는 빈 product(공용) 만 반환."""
    items = _load_paste_sets()
    if product:
        items = [s for s in items if not s.get("product") or s.get("product") == product]
    # recent first
    items = sorted(items, key=lambda s: s.get("updated", s.get("created", "")), reverse=True)
    return {"sets": items}


@router.post("/paste-sets/save")
def save_paste_set(
    req: PasteSetSaveReq,
    request: Request = None,
    _perm=Depends(require_page_manager("splittable")),
):
    actor = req.username or ""
    if request is not None:
        me = current_user(request)
        actor = me.get("username") or actor
    import secrets as _secrets
    nm = (req.name or "").strip()
    if not nm:
        raise HTTPException(400, "name required")
    cols = [str(c) for c in (req.columns or []) if c]
    if not cols:
        raise HTTPException(400, "columns required")
    now = datetime.datetime.now().isoformat(timespec="seconds")
    items = _load_paste_sets()
    # upsert by (name, product) — 같은 이름·제품이면 덮어쓰기.
    existing = next((s for s in items if s.get("name") == nm and s.get("product", "") == (req.product or "")), None)
    if existing:
        existing.update({
            "columns": cols, "rows": req.rows or [], "username": actor or existing.get("username", ""),
            "updated": now,
        })
    else:
        items.append({
            "id": "ps_" + _secrets.token_hex(5),
            "name": nm, "product": req.product or "",
            "columns": cols, "rows": req.rows or [],
            "username": actor,
            "created": now, "updated": now,
        })
    _save_paste_sets(items)
    invalidate_splittable_sets_cache(req.product or "")
    return {"ok": True, "count": len(items)}


class PasteSetDeleteReq(BaseModel):
    id: str = ""
    name: str = ""
    product: str = ""
    username: str = ""


@router.post("/paste-sets/delete")
def delete_paste_set(
    req: PasteSetDeleteReq,
    request: Request = None,
    _perm=Depends(require_page_manager("splittable")),
):
    items = _load_paste_sets()
    before = len(items)
    if req.id:
        items = [s for s in items if s.get("id") != req.id]
    elif req.name:
        items = [s for s in items if not (s.get("name") == req.name and s.get("product", "") == (req.product or ""))]
    else:
        raise HTTPException(400, "id or name required")
    if len(items) == before:
        raise HTTPException(404, "paste set not found")
    _save_paste_sets(items)
    invalidate_splittable_sets_cache(req.product or "")
    return {"ok": True, "removed": before - len(items)}


@router.post("/paste-sets/to-custom")
def paste_set_to_custom(
    req: PasteSetDeleteReq,
    request: Request = None,
    _perm=Depends(require_page_manager("splittable")),
):
    """paste 세트의 columns 를 CUSTOM 커스텀 뷰로 승격.
    CUSTOM 탭에서 바로 선택 가능하게 `custom_<safe_name>.json` 생성."""
    items = _load_paste_sets()
    src = None
    if req.id:
        src = next((s for s in items if s.get("id") == req.id), None)
    elif req.name:
        src = next((s for s in items if s.get("name") == req.name and s.get("product", "") == (req.product or "")), None)
    if not src:
        raise HTTPException(404, "paste set not found")
    actor = req.username or src.get("username", "")
    if request is not None:
        me = current_user(request)
        actor = me.get("username") or actor
    fp, name = _custom_file_path_for_name(src.get("name") or "paste_custom")
    columns = _clean_custom_columns(src.get("columns") or [])
    if not columns:
        raise HTTPException(400, "custom columns required")
    now = datetime.datetime.now().isoformat(timespec="seconds")
    existing = load_json(fp, None) if fp.exists() else None
    save_json(fp, {
        "name": name, "username": actor,
        "columns": columns,
        "created": (existing or {}).get("created", now),
        "updated": now,
        "version": int((existing or {}).get("version", 0)) + 1,
        "source": "paste-set", "paste_id": src.get("id", ""),
    })
    invalidate_splittable_sets_cache(req.product or "")
    return {"ok": True, "custom_name": name}


# ── Customs ──
@router.get("/customs")
def list_customs():
    customs = []
    for f in sorted(PLAN_DIR.glob("custom_*.json")):
        c = _sanitize_custom_record(load_json(f, None), f, persist=True)
        if c:
            c["_file"] = f.name
            customs.append(c)
    return {"customs": customs}


class CustomSaveReq(BaseModel):
    name: str
    username: str
    columns: List[Any]
    # v8.6.1: 낙관적 잠금 — 동일 name 의 기존 커스텀이 있으면 expected_version 일치 시에만 덮어쓴다.
    # 신규(처음 저장)면 0 또는 None.
    expected_version: int | None = None


@router.post("/customs/save")
def save_custom(
    req: CustomSaveReq,
    request: Request = None,
    _perm=Depends(require_page_manager("splittable")),
):
    actor = req.username
    if request is not None:
        me = current_user(request)
        actor = me.get("username") or actor
    fp, name = _custom_file_path_for_name(req.name)
    columns = _clean_custom_columns(req.columns)
    if not columns:
        raise HTTPException(400, "custom columns required")
    now = datetime.datetime.now().isoformat()
    existing = load_json(fp, None) if fp.exists() else None
    if existing:
        cur_v = int(existing.get("version", 1))
        # 클라가 보낸 expected_version 이 None 이면 강제 덮어쓰기 (legacy).
        # 정수면 일치해야 함. 불일치 → conflict 응답.
        if req.expected_version is not None and int(req.expected_version) != cur_v:
            return {
                "ok": False, "conflict": True,
                "server_version": cur_v, "current": existing,
                "detail": "Version conflict — another user has saved this custom view.",
            }
        new_v = cur_v + 1
        created = existing.get("created", now)
    else:
        new_v = 1
        created = now
    save_json(fp, {
        "name": name, "username": actor, "columns": columns,
        "created": created, "updated": now, "version": new_v,
    })
    invalidate_splittable_sets_cache()
    return {"ok": True, "version": new_v}


class CustomDeleteReq(BaseModel):
    name: str
    username: str


@router.post("/customs/delete")
def delete_custom(
    req: CustomDeleteReq,
    request: Request = None,
    _perm=Depends(require_page_manager("splittable")),
):
    fp = PLAN_DIR / f"custom_{safe_id(req.name)}.json"
    if not fp.exists():
        raise HTTPException(404)
    fp.unlink(missing_ok=True)
    invalidate_splittable_sets_cache()
    return {"ok": True}


class CustomTagColumnReq(BaseModel):
    product: str
    name: str
    username: str = ""
    # None = 건드리지 않음, "" = 비우기. 생성 시에는 빈 module 로 시작한다.
    module: Optional[str] = None


class CustomTagModuleReq(BaseModel):
    product: str
    column: str
    module: str = ""
    username: str = ""


class CustomTagColumnDeleteReq(BaseModel):
    product: str
    column: str = ""
    name: str = ""
    username: str = ""


class CustomTagValuesReq(BaseModel):
    product: str
    values: dict = Field(default_factory=dict)
    colors: dict = Field(default_factory=dict)
    username: str = ""
    root_lot_id: str = ""


class ManagementRowColumnReq(BaseModel):
    product: str
    name: str
    username: str = ""


class ManagementRowValuesReq(BaseModel):
    product: str
    values: dict
    username: str = ""
    root_lot_id: str = ""


@router.get("/custom-tags")
def list_custom_tags(product: str = Query("")):
    columns = _custom_tag_columns_for_product(product) if product else []
    return {"columns": columns, "count": len(columns)}


@router.post("/custom-tags/columns/save")
def save_custom_tag_column(req: CustomTagColumnReq, request: Request = None):
    actor = req.username or ""
    if request is not None:
        me = current_user(request)
        actor = me.get("username") or actor
    product = str(req.product or "").strip()
    if not product:
        raise HTTPException(400, "product required")
    column = _tag_column_id(req.name)
    label = str(req.name or "").strip()
    if label.upper().startswith(f"{CUSTOM_TAG_PREFIX}_"):
        label = label[len(CUSTOM_TAG_PREFIX) + 1:].strip()
    now = datetime.datetime.now().isoformat(timespec="seconds")
    data = _load_custom_tags_data()
    entry = _ensure_custom_tag_column(
        data,
        product=product,
        column=column,
        label=label or column,
        actor=actor,
        now=now,
        module=req.module,
    )
    _save_custom_tags_data(data)
    return {
        "ok": True,
        "column": entry["column"],
        "label": entry["label"],
        "module": _clean_tag_module(entry.get("module")),
        "columns": _custom_tag_columns_for_product(product),
    }


# TAG 행의 module 은 이미 있는 열에만 붙인다 — 오타로 새 열이 생기면 안 된다.
@router.post("/custom-tags/columns/module")
def save_custom_tag_module(req: CustomTagModuleReq, request: Request = None):
    actor = req.username or ""
    if request is not None:
        me = current_user(request)
        actor = me.get("username") or actor
    product = str(req.product or "").strip()
    if not product:
        raise HTTPException(400, "product required")
    column = _tag_column_id(req.column)
    module = _clean_tag_module(req.module)
    data = _load_custom_tags_data()
    entry = next(
        (
            c
            for c in (data.get("columns") or [])
            if isinstance(c, dict)
            and str(c.get("product") or "").strip() == product
            and str(c.get("column") or "").strip().upper() == column.upper()
        ),
        None,
    )
    if entry is None and column.upper() == DEFAULT_CUSTOM_TAG_COLUMN.upper():
        entry = _ensure_custom_tag_column(
            data,
            product=product,
            column=DEFAULT_CUSTOM_TAG_COLUMN,
            label=DEFAULT_CUSTOM_TAG_LABEL,
            actor=actor,
            now=datetime.datetime.now().isoformat(timespec="seconds"),
            module=module,
        )
    if entry is None:
        raise HTTPException(404, f"tag column not found: {column}")
    entry["module"] = module
    entry["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    if actor:
        entry["username"] = actor
    _save_custom_tags_data(data)
    return {"ok": True, "column": column, "module": module, "columns": _custom_tag_columns_for_product(product)}


@router.post("/custom-tags/delete")
@router.post("/custom-tags/columns/delete")
def delete_custom_tag_column(
    req: CustomTagColumnDeleteReq,
    request: Request = None,
    _perm=Depends(require_page_manager("splittable")),
):
    product = str(req.product or "").strip()
    if not product:
        raise HTTPException(400, "product required")
    raw_column = str(req.column or req.name or "").strip()
    if not raw_column:
        raise HTTPException(400, "tag column required")
    column = _tag_column_id(raw_column)
    if column.upper() == DEFAULT_CUSTOM_TAG_COLUMN.upper():
        raise HTTPException(400, "purpose tag is built-in and cannot be deleted")
    data = _load_custom_tags_data()

    columns = data.get("columns") if isinstance(data.get("columns"), list) else []
    kept_columns = []
    deleted_columns = 0
    for entry in columns:
        if (
            isinstance(entry, dict)
            and str(entry.get("product") or "").strip() == product
            and str(entry.get("column") or "").strip() == column
        ):
            deleted_columns += 1
            continue
        kept_columns.append(entry)

    values = data.get("values") if isinstance(data.get("values"), dict) else {}
    kept_values = {}
    deleted_values = 0
    for key, value in values.items():
        parts = str(key).split("|", 3)
        if len(parts) == 4 and parts[0] == product and parts[3] == column:
            deleted_values += 1
            continue
        kept_values[key] = value

    data["columns"] = kept_columns
    data["values"] = kept_values
    colors = data.get("colors") if isinstance(data.get("colors"), dict) else {}
    kept_colors = {
        key: value for key, value in colors.items()
        if not (
            len((parts := str(key).split("|", 3))) == 4
            and parts[0] == product and parts[3] == column
        )
    }
    deleted_colors = len(colors) - len(kept_colors)
    data["colors"] = kept_colors
    _save_custom_tags_data(data)
    actor = req.username or ""
    if not actor and isinstance(_perm, dict):
        actor = _perm.get("username") or ""
    _audit_user(actor, "splittable:custom_tag_delete", detail=f"product={product} column={column}")
    return {
        "ok": True,
        "column": column,
        "deleted_columns": deleted_columns,
        "deleted_values": deleted_values,
        "deleted_colors": deleted_colors,
        "columns": _custom_tag_columns_for_product(product),
    }


@router.post("/custom-tags/values")
def save_custom_tag_values(req: CustomTagValuesReq, request: Request = None):
    actor = req.username or ""
    if request is not None:
        me = current_user(request)
        actor = me.get("username") or actor
    product = str(req.product or "").strip()
    if not product:
        raise HTTPException(400, "product required")
    now = datetime.datetime.now().isoformat(timespec="seconds")
    data = _load_custom_tags_data()
    values = data.setdefault("values", {})
    colors = data.setdefault("colors", {})
    saved = 0
    deleted = 0
    rejected: list[str] = []
    for cell_key, raw_value in (req.values or {}).items():
        parts = str(cell_key or "").split("|", 2)
        if len(parts) != 3:
            rejected.append(str(cell_key))
            continue
        root_lot_id, wafer_id, column = [p.strip() for p in parts]
        if not root_lot_id or not wafer_id or not column.upper().startswith(f"{CUSTOM_TAG_PREFIX}_"):
            rejected.append(str(cell_key))
            continue
        _ensure_custom_tag_column(
            data,
            product=product,
            column=column,
            label=column[len(CUSTOM_TAG_PREFIX) + 1:] or column,
            actor=actor,
            now=now,
        )
        store_key = _tag_value_key(product, root_lot_id, wafer_id, column)
        value = "" if raw_value is None else str(raw_value).strip()
        if value:
            values[store_key] = {"value": value, "username": actor, "updated": now}
            saved += 1
        elif store_key in values:
            values.pop(store_key, None)
            deleted += 1
    colors_saved = 0
    colors_deleted = 0
    rejected_colors: list[str] = []
    for cell_key, raw_color in (req.colors or {}).items():
        parts = str(cell_key or "").split("|", 2)
        if len(parts) != 3:
            rejected_colors.append(str(cell_key))
            continue
        root_lot_id, wafer_id, column = [p.strip() for p in parts]
        color = str(raw_color or "").strip().lower()
        if (not root_lot_id or not wafer_id
                or not column.upper().startswith(f"{CUSTOM_TAG_PREFIX}_")
                or color not in CUSTOM_TAG_COLOR_PALETTE):
            rejected_colors.append(str(cell_key))
            continue
        _ensure_custom_tag_column(
            data,
            product=product,
            column=column,
            label=(DEFAULT_CUSTOM_TAG_LABEL if column.upper() == DEFAULT_CUSTOM_TAG_COLUMN.upper()
                   else column[len(CUSTOM_TAG_PREFIX) + 1:] or column),
            actor=actor,
            now=now,
        )
        store_key = _tag_value_key(product, root_lot_id, wafer_id, column)
        # 흰색도 Lot 관리와 동일한 명시적 색 선택이므로 저장한다.
        if colors.get(store_key) != color:
            colors[store_key] = color
            colors_saved += 1
    _save_custom_tags_data(data)
    return {
        "ok": True,
        "saved": saved,
        "deleted": deleted,
        "rejected": rejected,
        "colors_saved": colors_saved,
        "colors_deleted": colors_deleted,
        "rejected_colors": rejected_colors,
    }


@router.get("/management-rows")
def list_management_rows(product: str = Query("")):
    columns = _management_row_columns_for_product(product) if product else []
    return {"columns": columns, "count": len(columns)}


@router.post("/management-rows/columns/save")
def save_management_row_column(req: ManagementRowColumnReq, request: Request = None):
    actor = req.username or ""
    if request is not None:
        me = current_user(request)
        actor = me.get("username") or actor
    product = str(req.product or "").strip()
    if not product:
        raise HTTPException(400, "product required")
    column = _management_row_id(req.name)
    label = str(req.name or "").strip()
    if label.upper().startswith(f"{MANAGEMENT_ROW_PREFIX}_"):
        label = label[len(MANAGEMENT_ROW_PREFIX) + 1:].strip()
    now = datetime.datetime.now().isoformat(timespec="seconds")
    data = _load_management_rows_data()
    entry = _ensure_management_row_column(
        data,
        product=product,
        column=column,
        label=label or column,
        actor=actor,
        now=now,
    )
    _save_management_rows_data(data)
    return {"ok": True, "column": entry["column"], "label": entry["label"], "columns": _management_row_columns_for_product(product)}


@router.post("/management-rows/values")
def save_management_row_values(req: ManagementRowValuesReq, request: Request = None):
    actor = req.username or ""
    if request is not None:
        me = current_user(request)
        actor = me.get("username") or actor
    product = str(req.product or "").strip()
    if not product:
        raise HTTPException(400, "product required")
    now = datetime.datetime.now().isoformat(timespec="seconds")
    data = _load_management_rows_data()
    values = data.setdefault("values", {})
    saved = 0
    deleted = 0
    rejected: list[str] = []
    for cell_key, raw_value in (req.values or {}).items():
        parts = str(cell_key or "").split("|", 2)
        if len(parts) != 3:
            rejected.append(str(cell_key))
            continue
        root_lot_id, wafer_id, column = [p.strip() for p in parts]
        if not root_lot_id or not wafer_id or not column.upper().startswith(f"{MANAGEMENT_ROW_PREFIX}_"):
            rejected.append(str(cell_key))
            continue
        _ensure_management_row_column(
            data,
            product=product,
            column=column,
            label=column[len(MANAGEMENT_ROW_PREFIX) + 1:] or column,
            actor=actor,
            now=now,
        )
        store_key = _management_row_value_key(product, root_lot_id, wafer_id, column)
        value = "" if raw_value is None else str(raw_value).strip()
        if value:
            values[store_key] = {"value": value, "username": actor, "updated": now}
            saved += 1
        elif store_key in values:
            values.pop(store_key, None)
            deleted += 1
    _save_management_rows_data(data)
    return {"ok": True, "saved": saved, "deleted": deleted, "rejected": rejected}


def _resolve_fab_source_target(fab_source: str):
    """Resolve a db-relative fab_source to an existing file or directory."""
    fab_source = _normalize_fab_source_path(fab_source)
    if not fab_source:
        return None, fab_source
    if fab_source.startswith("root:"):
        return None, fab_source
    aliases = [fab_source]
    parts = [p for p in fab_source.split("/") if p]
    if parts:
        head = parts[0].casefold()
        tail = "/".join(parts[1:])
        if head == _RAWDATA_FAB.casefold():
            aliases.append(_RAWDATA_EXACT + (f"/{tail}" if tail else ""))
        elif head == _RAWDATA_EXACT.casefold():
            aliases.append(_RAWDATA_FAB + (f"/{tail}" if tail else ""))
    db_base = _db_base()
    base_root = _base_root()
    fp = None
    matched = fab_source
    for root in (db_base, base_root):
        if not root or not root.exists():
            continue
        for rel in aliases:
            # v8.8.22: CI 경로 매칭 — fab_source 내 제품 폴더 대소문자 무시.
            # v9.0.6: 1.RAWDATA_DB_FAB/<PROD> 와 1.RAWDATA_DB/<PROD> 는 둘 다 FAB
            # history 로 취급한다. 운영 환경은 exact 이름만 쓰는 경우가 있다.
            cand = _find_ci_path(root, rel)
            if cand is not None and cand.exists():
                fp = cand
                matched = rel
                break
            for ext in (".parquet", ".csv"):
                cand2 = _find_ci_path(root, f"{rel}{ext}")
                if cand2 is not None and cand2.exists():
                    fp = cand2
                    matched = rel
                    break
            if fp:
                break
        if fp:
            break
    return fp, matched


def _rglob_files_ci(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    suffix_set = {s.casefold() for s in suffixes}
    try:
        cache_key = (str(root.resolve()), tuple(sorted(suffix_set)))
    except Exception:
        cache_key = (str(root), tuple(sorted(suffix_set)))
    now = time.monotonic()
    cached = _RGLOB_CACHE.get(cache_key)
    if cached and now - cached[0] < _DISCOVERY_CACHE_TTL_SEC:
        return list(cached[1])
    try:
        out = sorted(
            [p for p in root.rglob("*") if p.is_file() and p.suffix.casefold() in suffix_set],
            key=lambda p: str(p).casefold(),
        )
        _RGLOB_CACHE[cache_key] = (now, out)
        return list(out)
    except Exception:
        return []


def _first_data_file_ci(root: Path, suffixes: tuple[str, ...]) -> Path | None:
    suffix_set = {s.casefold() for s in suffixes}
    try:
        cache_key = (str(root.resolve()), tuple(sorted(suffix_set)))
    except Exception:
        cache_key = (str(root), tuple(sorted(suffix_set)))
    now = time.monotonic()
    cached = _FIRST_DATA_FILE_CACHE.get(cache_key)
    if cached and now - cached[0] < _DISCOVERY_CACHE_TTL_SEC:
        return cached[1]
    try:
        for p in root.rglob("*"):
            if p.is_file() and p.suffix.casefold() in suffix_set:
                _FIRST_DATA_FILE_CACHE[cache_key] = (now, p)
                return p
    except Exception:
        pass
    _FIRST_DATA_FILE_CACHE[cache_key] = (now, None)
    return None


def _canon_file_key(path) -> str:
    """Normalized path string for cross-source file identity (sig ↔ scan)."""
    try:
        return str(Path(path).resolve()).casefold()
    except Exception:
        return str(path).casefold()


def _scan_fab_source_raw(fab_source: str, only_files: set[str] | None = None):
    """Scan a fab_source without applying the long-format compatibility adapter.

    only_files: 증분 fab_lot_index 빌드용 — `_canon_file_key` 로 정규화한 경로
    집합에 든 파일만 스캔한다 (None = 전체)."""
    fp, fab_source = _resolve_fab_source_target(fab_source)
    if not fp:
        return None
    try:
        if fp.is_dir():
            parquets = _rglob_files_ci(fp, (".parquet",))
            csvs = _rglob_files_ci(fp, (".csv",))
            if only_files is not None:
                parquets = [p for p in parquets if _canon_file_key(p) in only_files]
                csvs = [p for p in csvs if _canon_file_key(p) in only_files]
            if not parquets and not csvs:
                return None
            frames = []
            # v8.8.5: 사내 `PRODA/date=YYYYMMDD/part_*.parquet` hive 레이아웃 대응.
            # hive_partitioning 을 켜서 경로의 `date=...` 를 컬럼으로 노출 → ts_col 자동 추론 시
            # `date` 후보가 적중해 "가장 최신 date 의 fab_col" join 이 자동으로 동작.
            if parquets:
                try:
                    frames.append(_cast_cats_lazy(_scan_parquet_compat(
                        [str(p) for p in parquets], hive_partitioning=True)))
                except TypeError:
                    # polars 구버전 — 파라미터 미지원 시 폴백 (경로 기반 파티션 컬럼 없음).
                    frames.append(_cast_cats_lazy(_scan_parquet_compat([str(p) for p in parquets])))
            # source discovery/signature는 CSV를 FAB 데이터 파일로 인정했지만 디렉터리
            # scan은 parquet만 읽었다. 운영 FAB가 CSV 파티션이면 파일 수는 잡히는데
            # 모든 배치가 None이 되어 "읽을 수 있는 FAB 행 없음"으로 끝났다.
            frames.extend(_cast_cats_lazy(pl.scan_csv(str(p), infer_schema_length=5000)) for p in csvs)
            if len(frames) == 1:
                return frames[0]
            return _cast_cats_lazy(pl.concat(frames, how="diagonal_relaxed"))
        if only_files is not None and _canon_file_key(fp) not in only_files:
            return None
        if fp.suffix.lower() == ".csv":
            return _cast_cats_lazy(pl.scan_csv(str(fp), infer_schema_length=5000))
        return _cast_cats_lazy(_scan_parquet_compat(str(fp)))
    except Exception as e:
        logger.warning("FAB source scan failed (source=%s target=%s) %s: %s",
                       fab_source, fp, type(e).__name__, e)
        return None


def _scan_fab_source(fab_source: str, only_files: set[str] | None = None):
    """v8.8.0: fab_source 가 가리키는 DB 경로를 LazyFrame 으로 스캔.
    - "FAB/PRODA" / "1.RAWDATA_DB/PRODA" 같은 디렉토리면 그 아래 모든 *.parquet 을 union 으로 스캔.
    - 단일 .parquet/.csv 파일이면 그 파일을 스캔.
    v8.8.21: "root:<name>" legacy prefix 는 제품 scope 를 넘어서므로 더 이상 지원하지 않음.
      저장된 값이 있어도 무시 → 호출측이 _auto_derive_fab_source 로 자동 매칭하도록 None 반환.
    실패 시 None 반환 (조용히 폴백).
    """
    lf_raw = _scan_fab_source_raw(fab_source, only_files=only_files)
    if lf_raw is None:
        return None
    # FAB canonical adapter:
    #   - 정식 FAB 는 wafer 단위 공정이력(root_lot_id/lot_id/wafer_id/step_id/tkin_time/tkout_time/eqp_id...).
    #   - 구 demo alias(eqp/chamber/time)가 섞여 있으면 runtime schema 에서만 정규화한다.
    #   - 아주 오래된 item/value FAB demo data 만 기존 최신 row adapter 로 축약한다.
    try:
        raw_names = lf_raw.collect_schema().names()
        from core.long_pivot import normalize_fab_history
        lf_raw = normalize_fab_history(lf_raw)
        names = lf_raw.collect_schema().names()
    except Exception:
        return lf_raw
    process_markers = {"eqp_id", "chamber_id", "ppid", "reticle_id", "tkout_time", "tkin_time"}
    legacy_process_aliases = {"eqp", "chamber", "ppid", "reticle_id", "tkout_time", "tkin_time"}
    raw_has_process_history = bool((process_markers | legacy_process_aliases) & set(raw_names))
    if "item_id" in names and "value" in names and "lot_id" in names and not raw_has_process_history:
        logger.info("_scan_fab_source: long-format 감지 → fab_lot_id adapter 적용 (source=%s)", fab_source)
        keep = [c for c in ("product", "root_lot_id", "lot_id", "wafer_id", "time") if c in names]
        lf_adapt = lf_raw.select(keep)
        if "time" in keep:
            lf_adapt = lf_adapt.sort("time", descending=True, nulls_last=True)
        renames = {}
        if "lot_id" in keep:
            renames["lot_id"] = "fab_lot_id"
        if "time" in keep:
            renames["time"] = "tkout_time"
        if renames:
            lf_adapt = lf_adapt.rename(renames)
        key_cols = [c for c in ("root_lot_id", "wafer_id") if c in keep]
        if key_cols:
            lf_adapt = lf_adapt.unique(subset=key_cols, keep="first", maintain_order=True)
        return lf_adapt
    return lf_raw


def _foreground_global_fab_scan_enabled() -> bool:
    return str(os.environ.get("FLOW_SPLITTABLE_FOREGROUND_GLOBAL_FAB_SCAN", "")).strip().lower() in {
        "1", "true", "yes", "on", "enabled"
    }


def _global_fab_source_paths(preferred_source: str = "", include_all: bool = True) -> list[str]:
    """Return db-relative FAB product folders to use for lot-id matching.

    SplitTable renders one ML_TABLE product, but fab_lot_id lineage can be
    present in a different FAB product folder.  Build the matching table from
    the whole FAB DB root, keeping the product-derived source first when it
    exists so current behavior remains the common fast path.
    """
    out: list[str] = []
    seen: set[str] = set()

    def add(rel: str):
        rel = _normalize_fab_source_path(rel)
        if not rel:
            return
        key = rel.casefold()
        if key in seen:
            return
        seen.add(key)
        out.append(rel)

    add(preferred_source)
    if not include_all:
        return out
    db_base = _db_base()
    try:
        db_base_resolved = db_base.resolve()
    except Exception:
        db_base_resolved = None

    for root_dir in _list_db_roots():
        up = root_dir.name.upper()
        is_fab_root = (
            up == _RAWDATA_FAB.upper()
            or up == _RAWDATA_EXACT.upper()
            or "FAB" in up
        )
        try:
            root_is_db_base = db_base_resolved is not None and root_dir.resolve() == db_base_resolved
        except Exception:
            root_is_db_base = False
        if not is_fab_root and not root_is_db_base:
            continue
        try:
            children = sorted(
                [p for p in root_dir.iterdir() if p.is_dir() and not p.name.startswith((".", "_", "__"))],
                key=lambda p: p.name.lower(),
            )
        except Exception:
            continue
        for child in children:
            if _first_data_file_ci(child, (".parquet", ".csv")) is None:
                continue
            if root_is_db_base:
                add(child.name)
            else:
                add(f"{root_dir.name}/{child.name}")
    return out


def _fab_source_product(source: str) -> str:
    """FAB source 경로에서 제품명을 뽑는다 (`1.RAWDATA_DB_FAB/PRODA` → `PRODA`).

    FAB 는 `<DB루트>/<제품>/date=.../*.parquet` 구조이고, 이 앱의 제품 결속
    규칙도 "FAB DB root 바로 아래 제품 폴더명"이다(`lot_progress_cache.metadata`).
    루트 자체가 source 인 형태(제품명만 오는 경우)도 마지막 세그먼트가 제품이다.
    """
    rel = _normalize_fab_source_path(source)
    if not rel:
        return ""
    return str(rel).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].strip()


def _normalized_fab_product_expr(expr):
    """Canonical, case-insensitive FAB product value used by cache/dashboard."""
    return (
        expr.cast(_STR, strict=False)
        .fill_null("")
        .str.strip_chars()
        .str.replace(r"(?i)^ML_TABLE_", "")
        .str.to_uppercase()
    )


def _scan_global_fab_sources(preferred_source: str = "", include_all: bool = True,
                             only_files: set[str] | None = None,
                             tag_source_product: bool = False):
    """Scan all FAB DB product folders as one LazyFrame for matching.

    only_files: 증분 fab_lot_index 빌드용 파일 부분집합 (None = 전체).

    tag_source_product: 각 행에 **그 행이 온 FAB 제품 폴더**를 실어 준다
    (`__cache_src_product`). 매칭은 일부러 FAB 전체를 훑기 때문에(랏 lineage 가
    다른 제품 폴더에 있을 수 있다) 결과만 보면 어느 제품 물량인지 알 수 없다 —
    대시보드 WIP 이 제품마다 똑같은 수를 내던 원인이다. 기본은 끔: 다른 호출자
    (fab_lot_index·view join)의 스키마를 바꾸지 않는다.
    """
    frames = []
    used_sources: list[str] = []
    for source in _global_fab_source_paths(preferred_source, include_all=include_all):
        lf = _scan_fab_source(source, only_files=only_files)
        if lf is None:
            continue
        if tag_source_product:
            lf = lf.with_columns(
                _normalized_fab_product_expr(pl.lit(_fab_source_product(source)))
                .alias(MATCH_CACHE_SRC_PRODUCT_COL)
            )
        frames.append(lf)
        used_sources.append(source)
    if not frames:
        return None, used_sources
    if len(frames) == 1:
        return frames[0], used_sources
    try:
        return _cast_cats_lazy(pl.concat(frames, how="diagonal_relaxed")), used_sources
    except Exception as e:
        logger.warning("_scan_global_fab_sources concat 실패 %s: %s", type(e).__name__, e)
        return frames[0], used_sources[:1]


# v8.8.3/v8.8.5: ML_TABLE_<PROD> → DB 상위폴더 자동 매칭.
#   `_list_db_roots()` 에 위임 — 사내 `1.RAWDATA_DB*` 접두 폴더도 인식 (FAB 힌트 우선).
# v8.8.17: root_dir 이 db_base 자체일 때(Case 1/3) 는 제품명만 반환 —
#   `_scan_fab_source` 에서 `db_base / fab_source` 로 해석하므로 prefix 중복 방지.
def _auto_derive_fab_source(product: str) -> str:
    """Return a fab_source path like "1.RAWDATA_DB_FAB/PRODA" (or legacy "FAB/PRODA") if auto-matchable, else "".
    ML_TABLE_ prefix 가 아니면 "" 반환 (오버라이드 off)."""
    p = _canonical_mltable_product_name(product)
    if not p:
        return ""
    pro = p[len("ML_TABLE_"):].strip()
    if not pro:
        return ""
    db_base = _db_base()
    roots = _list_db_roots()
    roots.sort(key=lambda r: _rank_db_root_name(r.name))
    for root_dir in roots:
        up = root_dir.name.upper()
        if up not in (_RAWDATA_EXACT.upper(), _RAWDATA_FAB.upper()) and not up.startswith(_RAWDATA_EXACT.upper() + "_"):
            continue
        # v8.8.22: CI 매칭 — 폴더가 ProdA/proda/PRODA 중 무엇이든 인식.
        cand = _find_ci_child(root_dir, pro)
        if cand is not None:
            actual = cand.name
            try:
                if root_dir.resolve() == db_base.resolve():
                    return actual
            except Exception:
                pass
            return f"{root_dir.name}/{actual}"
    return ""


# v8.8.3/v8.8.5/v9.0.4: ts_col / fab_col 자동 추론.
#   - 사용자가 기대하는 실사용 우선순위: tkout_time > time 계열 > date.
#   - date 는 hive 파티션 키(`date=YYYYMMDD`) 전용 마지막 fallback.
_TS_COL_CANDIDATES = ("tkout_time", "time", "out_ts", "ts", "timestamp", "created_at", "log_ts", "event_ts", "update_ts")
_FAB_COL_CANDIDATES = ("fab_lot_id", "lot_id", "fab_lotid", "fab_lot")
_RAW_TO_RUNTIME_ALIAS_CANDIDATES = {
    "lot_id": "fab_lot_id",
    "time": "tkout_time",
    "eqp": "eqp_id",
    "chamber": "chamber_id",
}


# v8.8.22: case-insensitive 컬럼 정렬.
#   ML_TABLE 은 대문자(ROOT_LOT_ID/WAFER_ID), hive 원천은 소문자(root_lot_id/wafer_id) 로
#   다르게 찍히는 경우가 있음. casefold 같으면 같은 컬럼으로 취급해야 join/override 가 동작.
#   → fab_lf 의 컬럼을 main_lf 쪽 casing 으로 rename 하여 이후 로직이 그대로 exact 매칭되게.
# v8.8.26: 충돌 가드 단순화 + rename 후 실제 스키마 재확인 (rename 이 lazy 상 silently 실패하는 사례 방지).
def _ci_align_fab_to_main(fab_lf, main_names):
    """Rename fab_lf columns to match main_names casing when casefold is equal.

    규칙:
      - fab 의 컬럼 fn (casefold=key) 이 main 의 target 과 casefold 일치하고 casing 만 다르면
        rename[fn] = target.
      - target 이 이미 fab 에 (별도의 distinct 컬럼으로) 존재하면 rename 을 skip (clobber 방지).
      - target 이 이번 rename 맵의 다른 항목에 의해 이미 소비됐으면 skip.
      - rename 후 실제 schema 를 재조회해 실패 여부 확인 — 실패 시 경고 로깅.

    Returns (aligned_lf, new_fab_names_list).
    """
    if fab_lf is None:
        return fab_lf, []
    try:
        fab_names = fab_lf.collect_schema().names()
    except Exception as e:
        logger.warning("_ci_align_fab_to_main: fab schema 조회 실패 %s: %s", type(e).__name__, e)
        return fab_lf, []
    main_ci = {n.casefold(): n for n in main_names}
    fab_set = set(fab_names)
    rename: dict = {}
    used_targets: set = set()
    for fn in fab_names:
        key = fn.casefold()
        target = main_ci.get(key)
        if not target or target == fn:
            continue
        # 단순화된 충돌 가드: target 이 fab 에 별개 컬럼으로 존재하면 rename 불가 (clobber).
        if target in fab_set:
            continue
        if target in used_targets:
            continue
        rename[fn] = target
        used_targets.add(target)
    if rename:
        try:
            fab_lf = fab_lf.rename(rename)
        except Exception as e:
            logger.warning("_ci_align_fab_to_main: rename 실패 %s: %s (rename=%s)",
                           type(e).__name__, e, rename)
            # rename 실패 시 원본 이름 유지
            return fab_lf, list(fab_names)
        # rename 이 적용됐는지 실제 스키마로 재확인.
        try:
            post = fab_lf.collect_schema().names()
            missing = [t for t in rename.values() if t not in post]
            if missing:
                logger.warning("_ci_align_fab_to_main: rename 후 target 누락 %s (post=%s...)",
                               missing, post[:20])
            return fab_lf, post
        except Exception as e:
            logger.warning("_ci_align_fab_to_main: post-schema 조회 실패 %s: %s",
                           type(e).__name__, e)
    new_names = [rename.get(n, n) for n in fab_names]
    return fab_lf, new_names


def _ci_resolve_in(name: str, pool):
    """Return the actual column name from pool matching `name` case-insensitively (exact first)."""
    if not name:
        return ""
    resolved = resolve_column(list(pool), name)
    return resolved.matched if resolved else ""


def _default_override_join_keys(main_names, fab_names):
    """Prefer root_lot_id + wafer_id by default; fall back only when necessary."""
    main_ci = {str(n).casefold(): n for n in (main_names or [])}
    fab_ci = {str(n).casefold(): n for n in (fab_names or [])}
    preferred = []
    for cand in ("root_lot_id", "wafer_id"):
        key = cand.casefold()
        if key in main_ci and key in fab_ci:
            preferred.append(main_ci[key])
    if preferred:
        return preferred
    fallback = []
    for cand in ("lot_id", "wafer_id"):
        key = cand.casefold()
        if key in main_ci and key in fab_ci:
            fallback.append(main_ci[key])
    return fallback


def _join_key_expr(col_name: str):
    """Normalize join key values so main/fab joins are case-insensitive and trim-safe."""
    return (
        pl.col(col_name)
        .cast(_STR, strict=False)
        .str.strip_chars()
        .str.to_uppercase()
    )


def _scope_match_cache_to_main_keys(main_lf, fab_lf, join_keys: list[str],
                                    join_tmp_keys: list[str]):
    """Keep FAB history belonging to the target ML_TABLE product.

    FAB는 제품 폴더 전체를 훑어야 한다. 랏이 공정 중 다른 제품 폴더로 이동할 수
    있기 때문이다. 하지만 그 결과를 target ML_TABLE의 root/wafer key로 제한하지
    않으면 모든 제품 cache가 전 FAB 행을 똑같이 갖게 된다. normalized key semi-join은
    폴더 이동 이력은 보존하면서 다른 제품의 랏만 제거한다.
    """
    if not join_keys or len(join_keys) != len(join_tmp_keys):
        return fab_lf
    main_names = set(main_lf.collect_schema().names())
    if any(key not in main_names for key in join_keys):
        return fab_lf
    main_keys = main_lf.select([
        _join_key_expr(key).alias(tmp) for key, tmp in zip(join_keys, join_tmp_keys)
    ])
    for tmp in join_tmp_keys:
        main_keys = main_keys.filter(pl.col(tmp).is_not_null() & (pl.col(tmp) != ""))
    main_keys = main_keys.unique(subset=join_tmp_keys)
    return fab_lf.join(main_keys, on=join_tmp_keys, how="semi")


def _contains_literal_ci_expr(col_name: str, needle: str):
    """Case-insensitive literal contains for LazyFrame autocomplete filters."""
    return (
        pl.col(col_name)
        .cast(_STR, strict=False)
        .str.to_uppercase()
        .str.contains(str(needle or "").strip().upper(), literal=True)
    )


def _apply_fab_scope_filters(fab_lf, fab_names, ov: dict, root_lot_id: str = "",
                             fab_lot_id: str = "", wafer_ids: str = "",
                             fab_col: str = ""):
    """Limit FAB source rows before latest-row picking and join."""
    root_scope = str(root_lot_id or "").strip()
    fab_scope = str(fab_lot_id or "").strip()
    wafer_scope = str(wafer_ids or "").strip()
    if root_scope:
        root_col = _resolve_source_col_name((ov.get("root_col") or "").strip(), fab_names) \
                   or _ci_resolve_in("root_lot_id", fab_names)
        if root_col:
            fab_lf = fab_lf.filter(_join_key_expr(root_col) == root_scope.upper())
    if fab_scope:
        target_fab_col = fab_col if fab_col in fab_names else _pick_first_present_ci(_FAB_COL_CANDIDATES, fab_names)
        if target_fab_col:
            fab_lf = fab_lf.filter(_join_key_expr(target_fab_col) == fab_scope.upper())
    if wafer_scope:
        wf_col = _resolve_source_col_name((ov.get("wf_col") or ov.get("wafer_col") or "").strip(), fab_names) \
                 or _pick_first_present_ci(("wafer_id", "wafer"), fab_names)
        if wf_col:
            wf_list = [w.strip() for w in wafer_scope.split(",") if w.strip()]
            try:
                wf_ints = [int(w) for w in wf_list]
                wf_strs = set()
                for n in wf_ints:
                    wf_strs.update([str(n), f"{n:02d}", f"W{n}", f"W{n:02d}"])
                fab_lf = fab_lf.filter(
                    pl.col(wf_col).cast(_STR, strict=False).is_in(list(wf_strs))
                    | pl.col(wf_col).cast(pl.Int64, strict=False).is_in(wf_ints)
                )
            except ValueError:
                fab_lf = fab_lf.filter(pl.col(wf_col).cast(_STR, strict=False).is_in(wf_list))
    return fab_lf


# v8.8.16: hive 원천에서 끌어와 ML_TABLE 값을 덮어쓸 기본 컬럼 집합.
#   사내 `1.RAWDATA_DB*/<PROD>/date=*/*.parquet` 레이아웃에서 이 이름이 있으면 소스값으로 교체.
#   fab_col(보통 fab_lot_id) 는 레거시 단일 필드와 병합되어 override_cols 에 합류.
_DEFAULT_OVERRIDE_COLS = (
    "root_lot_id", "lot_id", "wafer_id", "line_id", "process_id", "step_id",
    "tkin_time", "tkout_time", "eqp_id", "chamber_id", "reticle_id", "ppid",
    # lot_type: FAB 가 주는 랏 구분(양산/엔지니어링/모니터 등). 대시보드에서 이
    # 축으로 물량을 나눠 보기 위해 매칭 캐시에 함께 싣는다. 이 목록은 FAB 스키마에
    # 실제로 있는 열만 통과하므로(`c in fab_names`), 없는 환경에서는 조용히 빠진다.
    "lot_type",
)


def _match_cache_refresh_minutes() -> int:
    data = load_json(PATHS.data_root / "settings.json", {})
    raw = data.get("splittable_match_refresh_minutes", MATCH_CACHE_REFRESH_MINUTES_DEFAULT) if isinstance(data, dict) else MATCH_CACHE_REFRESH_MINUTES_DEFAULT
    try:
        value = int(raw)
    except Exception:
        value = MATCH_CACHE_REFRESH_MINUTES_DEFAULT
    return max(MATCH_CACHE_REFRESH_MINUTES_MIN, min(MATCH_CACHE_REFRESH_MINUTES_MAX, value))


def _latest_lot_step_cache_path() -> Path:
    return _db_base() / "cache" / LATEST_LOT_STEP_CACHE_FILE


def _legacy_latest_lot_step_cache_path() -> Path:
    return _db_base() / "cache" / LEGACY_LATEST_LOT_STEP_CACHE_FILE


def _cleanup_legacy_latest_lot_step_cache() -> None:
    try:
        _legacy_latest_lot_step_cache_path().unlink(missing_ok=True)
    except Exception:
        pass


def _empty_latest_lot_step_frame() -> pl.DataFrame:
    return pl.DataFrame({col: [] for col in LATEST_LOT_STEP_CACHE_COLUMNS})


def _match_cache_state() -> dict:
    data = load_json(MATCH_CACHE_STATE_FILE, {}) if MATCH_CACHE_STATE_FILE.is_file() else {}
    return data if isinstance(data, dict) else {}


def _match_cache_global_fresh(now: float | None = None) -> dict:
    now = time.time() if now is None else float(now)
    state = _match_cache_state()
    last = 0.0
    try:
        last = float(state.get("last_refresh_epoch") or 0)
    except Exception:
        last = 0.0
    interval_s = _match_cache_refresh_minutes() * 60
    cache_fp = _latest_lot_step_cache_path()
    fresh = bool(last and cache_fp.is_file() and (now - last) < interval_s)
    return {
        "fresh": fresh,
        "last_refresh_epoch": last or None,
        "last_refresh_at": state.get("last_refresh_at") or "",
        "age_seconds": max(0, int(now - last)) if last else None,
        "next_refresh_at": datetime.datetime.fromtimestamp(last + interval_s).isoformat(timespec="seconds") if last else "",
        "cache_path": str(cache_fp),
        "cache_exists": cache_fp.is_file(),
        "interval_minutes": _match_cache_refresh_minutes(),
    }


def _mark_match_cache_refreshed(export_result: dict) -> None:
    now = time.time()
    state = {
        "last_refresh_epoch": now,
        "last_refresh_at": datetime.datetime.fromtimestamp(now).isoformat(timespec="seconds"),
        "cache_path": export_result.get("path") or str(_latest_lot_step_cache_path()),
        "row_count": int(export_result.get("row_count") or 0),
        "products": export_result.get("products") or [],
        "updated_at": export_result.get("cache_updated_at") or "",
    }
    save_json(MATCH_CACHE_STATE_FILE, state)


def _float_env_clamped(name: str, default: float, lo: float, hi: float) -> float:
    try:
        value = float(os.environ.get(name, "") or default)
    except Exception:
        value = default
    return max(lo, min(hi, value))


def _match_cache_product_pause_seconds() -> float:
    return _float_env_clamped("FLOW_SPLITTABLE_MATCH_CACHE_PRODUCT_PAUSE_SEC", 5.0, 0.0, 300.0)


def _match_cache_memory_wait_seconds() -> float:
    return _float_env_clamped("FLOW_SPLITTABLE_MATCH_CACHE_MEMORY_WAIT_SEC", 60.0, 5.0, 600.0)


def _match_cache_memory_max_wait_seconds() -> float:
    """매칭캐시 빌드 전 메모리 가드 대기의 최대 한도(초). 초과하면 무한 대기 대신
    경고 후 진행한다(빌드는 스트리밍이라 메모리 제한적, run_heavy 에 2차 가드도 있음).
    0 = 무제한(구동작). 기본 600초 — 개발서버에서 통합 스캔이 영원히 멈추는 것 방지."""
    return _float_env_clamped("FLOW_SPLITTABLE_MATCH_CACHE_MEMORY_MAX_WAIT_SEC", 600.0, 0.0, 21600.0)


def _match_cache_stream_enabled() -> bool:
    """FAB 매칭캐시 빌드를 root_lot_id 배치 스트리밍으로 처리할지 여부(기본 ON).
    글로벌 sort+unique 의 peak RAM 폭발을 막는다. 0/false 로 끄면 legacy 경로."""
    raw = str(os.environ.get("FLOW_MATCH_CACHE_STREAM", "1") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


MATCH_CACHE_STREAM_BATCH_ROOTS_DEFAULT = 300


def _match_cache_stream_batch_roots() -> int:
    """root_lot_id 배치 스트리밍에서 한 번에 처리할 root(정규화 join key) 값 개수.
    작을수록 peak RAM 이 낮고 대신 FAB 원천을 여러 번 재스캔해 느려진다.
    개발서버(메모리 작음) OOM 방지를 위해 기본값은 보수적으로 잡는다.

    우선순위: env(FLOW_MATCH_CACHE_STREAM_BATCH_ROOTS) > 캐시관리 예산설정 톱니바퀴
    (match_cache_batch_roots[_dev], 운영/개발 분리) > 기본값 300."""
    env_raw = os.environ.get("FLOW_MATCH_CACHE_STREAM_BATCH_ROOTS", "")
    if str(env_raw).strip():
        try:
            return max(1, min(100000, int(env_raw)))
        except Exception:
            pass
    try:
        from core import cache_settings
        is_dev = bool(_ml_table_lookup._root_ram_cache_use_dev())
        v = cache_settings.get_int_role("match_cache_batch_roots", is_dev, None)
        if v is not None:
            return max(1, min(100000, int(v)))
    except Exception:
        pass
    return MATCH_CACHE_STREAM_BATCH_ROOTS_DEFAULT


def _match_cache_stream_log_gap_seconds() -> float:
    """배치 진행 로그 최소 간격(초). 배치가 빠를 때 이벤트 로그 폭주를 막는다."""
    return _float_env_clamped("FLOW_MATCH_CACHE_STREAM_LOG_GAP_SEC", 1.5, 0.0, 30.0)


def _match_cache_products(product: str = "") -> list[str]:
    raw = str(product or "").strip()
    if raw:
        return [raw]
    try:
        products = [p.get("name") for p in list_products().get("products", [])]
    except Exception:
        products = []
    return [p for p in products if p]


class MatchCacheCancelled(RuntimeError):
    """관리자가 이 제품의 캐싱을 중단했다.

    부분 산출은 버리고(promote/meta 기록 안 함) 다음 제품으로 넘어간다. 중단한
    제품은 재개 지점이 남지 않으므로 다음 스캔에서 처음부터 다시 빌드된다."""


def _match_cache_product_key(product: str) -> str:
    name = str(product or "").strip().upper()
    return name[len("ML_TABLE_"):] if name.startswith("ML_TABLE_") else name


def _match_cache_cancel_file() -> Path:
    """중단 신호 파일. 빌드가 개발 워커(별도 프로세스·서버)에서 돌 수도 있어
    프로세스 메모리만으로는 신호가 닿지 않는다 — 공유 data_root 를 거친다."""
    return MATCH_CACHE_DIR / "cancel.json"


def request_match_cache_cancel(product: str, by: str = "") -> dict:
    """이 제품의 진행 중 캐싱을 중단 요청한다."""
    key = _match_cache_product_key(product)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    with _MATCH_CACHE_CANCEL_LOCK:
        _MATCH_CACHE_CANCEL.update({"product": key, "by": str(by or ""), "at": now})
    try:
        MATCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        save_json(_match_cache_cancel_file(), {"product": key, "by": str(by or ""), "at": now})
    except Exception as e:
        logger.warning("match cache cancel signal write failed: %s", e)
    return {"product": key, "by": str(by or ""), "at": now}


def clear_match_cache_cancel(product: str = "") -> None:
    """중단 신호 해제. product 를 주면 그 제품 신호일 때만 지운다 — 다음 제품을
    시작하면서 남의 중단 요청까지 지우지 않도록."""
    key = _match_cache_product_key(product)
    with _MATCH_CACHE_CANCEL_LOCK:
        if not key or _MATCH_CACHE_CANCEL.get("product") == key:
            _MATCH_CACHE_CANCEL.update({"product": "", "by": "", "at": ""})
    try:
        fp = _match_cache_cancel_file()
        if fp.is_file():
            current = load_json(fp, {}) or {}
            if not key or _match_cache_product_key(current.get("product") or "") == key:
                fp.unlink(missing_ok=True)
    except Exception:
        pass


def _match_cache_cancel_target() -> dict:
    """지금 중단 대상으로 지정된 제품. 프로세스 상태가 우선, 없으면 신호 파일."""
    with _MATCH_CACHE_CANCEL_LOCK:
        if _MATCH_CACHE_CANCEL.get("product"):
            return dict(_MATCH_CACHE_CANCEL)
    try:
        fp = _match_cache_cancel_file()
        if fp.is_file():
            raw = load_json(fp, {}) or {}
            key = _match_cache_product_key(raw.get("product") or "")
            if key:
                return {"product": key, "by": str(raw.get("by") or ""), "at": str(raw.get("at") or "")}
    except Exception:
        pass
    return {"product": "", "by": "", "at": ""}


def _match_cache_cancelled(product: str) -> bool:
    key = _match_cache_product_key(product)
    return bool(key) and _match_cache_cancel_target().get("product") == key


def _match_cache_job_status() -> dict:
    with _MATCH_CACHE_JOB_LOCK:
        out = dict(_MATCH_CACHE_JOB_STATE)
        out["products"] = [dict(r) for r in (_MATCH_CACHE_JOB_STATE.get("products") or [])]
        out["order"] = list(_MATCH_CACHE_JOB_STATE.get("order") or [])
    out["cancel"] = _match_cache_cancel_target()
    return out


def _match_cache_job_update(**updates) -> None:
    with _MATCH_CACHE_JOB_LOCK:
        _MATCH_CACHE_JOB_STATE.update(updates)


def _match_cache_job_append_products(rows: list[dict]) -> None:
    if not rows:
        return
    with _MATCH_CACHE_JOB_LOCK:
        current = [dict(r) for r in (_MATCH_CACHE_JOB_STATE.get("products") or [])]
        current.extend(dict(r) for r in rows)
        _MATCH_CACHE_JOB_STATE["products"] = current[-500:]
        _MATCH_CACHE_JOB_STATE["done"] = int(_MATCH_CACHE_JOB_STATE.get("done") or 0) + len(rows)
        _MATCH_CACHE_JOB_STATE["ok_count"] = int(_MATCH_CACHE_JOB_STATE.get("ok_count") or 0) + len([r for r in rows if r.get("ok")])
        # 관리자 중단은 실패가 아니다 — 별도로 센다.
        _MATCH_CACHE_JOB_STATE["failed_count"] = int(_MATCH_CACHE_JOB_STATE.get("failed_count") or 0) + len([r for r in rows if not r.get("ok") and not r.get("skipped") and not r.get("cancelled")])
        _MATCH_CACHE_JOB_STATE["cancelled_count"] = int(_MATCH_CACHE_JOB_STATE.get("cancelled_count") or 0) + len([r for r in rows if r.get("cancelled")])
        _MATCH_CACHE_JOB_STATE["skipped_count"] = int(_MATCH_CACHE_JOB_STATE.get("skipped_count") or 0) + len([r for r in rows if r.get("skipped")])
        for row in reversed(rows):
            if row.get("reason"):
                _MATCH_CACHE_JOB_STATE["last_error"] = str(row.get("reason") or "")
                break


def _begin_match_cache_job(products: list[str], force: bool, reason: str) -> tuple[bool, dict]:
    now = datetime.datetime.now().isoformat(timespec="seconds")
    with _MATCH_CACHE_JOB_LOCK:
        if _MATCH_CACHE_JOB_STATE.get("running"):
            status = dict(_MATCH_CACHE_JOB_STATE)
            status["products"] = [dict(r) for r in (_MATCH_CACHE_JOB_STATE.get("products") or [])]
            status["order"] = list(_MATCH_CACHE_JOB_STATE.get("order") or [])
            return False, status
        _MATCH_CACHE_JOB_STATE.clear()
        _MATCH_CACHE_JOB_STATE.update({
            "running": True,
            "queued": True,
            "force": bool(force),
            "reason": reason or "manual",
            "started_at": now,
            "finished_at": "",
            "current_product": "",
            "total": len(products),
            "done": 0,
            "ok_count": 0,
            "failed_count": 0,
            "skipped_count": 0,
            "cancelled_count": 0,
            "paused": False,
            "last_error": "",
            "products": [],
            "order": list(products),
        })
        status = dict(_MATCH_CACHE_JOB_STATE)
        status["products"] = []
        status["order"] = list(products)
        return True, status


def _wait_for_match_cache_memory() -> bool:
    try:
        from core.runtime_limits import process_memory_high, process_memory_snapshot
    except Exception:
        return True
    wait_s = _match_cache_memory_wait_seconds()
    max_wait = _match_cache_memory_max_wait_seconds()
    waited = 0.0
    while not _MATCH_CACHE_STOP.is_set():
        try:
            high = process_memory_high()
            snap = process_memory_snapshot()
        except Exception:
            return True
        if not high:
            _match_cache_job_update(paused=False, memory=snap)
            return True
        # 무한 대기 방지 — 메모리 가드가 max_wait 넘게 계속 high 면 경고 후 진행한다.
        # (스트리밍 빌드라 메모리 제한적이고, run_heavy 로컬 실행에 2차 메모리 admission
        #  가드가 또 있다.) 개발서버에서 통합 스캔이 stage 1 에서 영원히 멈추던 문제 해결.
        if max_wait > 0 and waited >= max_wait:
            logger.warning(
                "match cache memory guard high for %.0fs — 대기 한도 초과, 진행함 "
                "(snap=%s)", waited, snap,
            )
            _match_cache_job_update(paused=False, memory=snap)
            return True
        _match_cache_job_update(paused=True, memory=snap)
        _MATCH_CACHE_STOP.wait(wait_s)
        waited += wait_s
    return False


def _write_match_cache_lazyframe(q, tmp: Path) -> int:
    """Write cache output with the lowest available peak-memory path."""
    try:
        tmp.unlink(missing_ok=True)
    except Exception:
        pass
    try:
        q.sink_parquet(str(tmp))
        try:
            return int(pl.scan_parquet(str(tmp)).select(pl.len().alias("row_count")).collect().item(0, 0))
        except Exception:
            try:
                return int(pl.read_parquet(str(tmp)).height)
            except Exception:
                return 0
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
    df = None
    try:
        try:
            from core.parquet_perf import collect_streaming
            df = collect_streaming(q)
        except Exception:
            df = q.collect()
        df.write_parquet(tmp)
        return int(df.height)
    finally:
        try:
            del df
        except Exception:
            pass
        try:
            gc.collect()
        except Exception:
            pass


def _current_fab_override(product: str) -> tuple[str, dict, str]:
    ml_product = _canonical_mltable_product_name(product, allow_bare=True) or str(product or "").strip()
    cfg = load_json(SOURCE_CFG, {}) if SOURCE_CFG.exists() else {}
    ov = _lot_override_for(cfg, ml_product)
    fab_source = _normalize_fab_source_path((ov.get("fab_source") or "").strip())
    if fab_source.startswith("root:"):
        fab_source = ""
    if not fab_source:
        fab_source = _auto_derive_fab_source(ml_product)
    return ml_product, ov, fab_source


def _match_cache_path(product: str) -> Path:
    name = safe_id(_canonical_mltable_product_name(product, allow_bare=True) or str(product or "").strip() or "product")
    return MATCH_CACHE_DIR / f"{name}.parquet"


def _match_cache_meta_path(product: str) -> Path:
    name = safe_id(_canonical_mltable_product_name(product, allow_bare=True) or str(product or "").strip() or "product")
    return MATCH_CACHE_DIR / f"{name}.json"


def _match_cache_config_key(product: str, ov: dict, fab_source: str) -> str:
    keys = ("root_col", "wf_col", "wafer_col", "fab_col", "ts_col", "join_keys", "override_cols")
    clean_ov = {k: ov.get(k) for k in keys if k in ov}
    payload = {
        "version": MATCH_CACHE_VERSION,
        "product": _canonical_mltable_product_name(product, allow_bare=True) or str(product or "").strip(),
        "fab_source": _normalize_fab_source_path(fab_source),
        "fab_sources": _global_fab_source_paths(fab_source),
        "db_root": str(_db_base()),
        "base_root": str(_base_root()),
        "override": clean_ov,
    }
    try:
        return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        return str(payload)


def _match_cache_current(product: str) -> dict | None:
    ml_product, ov, fab_source = _current_fab_override(product)
    if not ml_product:
        return None
    if not fab_source and not _global_fab_source_paths(""):
        return None
    fp = _match_cache_path(ml_product)
    meta_fp = _match_cache_meta_path(ml_product)
    if not fp.is_file() or not meta_fp.is_file():
        return None
    meta = load_json(meta_fp, {})
    if not isinstance(meta, dict):
        return None
    if meta.get("version") != MATCH_CACHE_VERSION:
        return None
    if meta.get("config_key") != _match_cache_config_key(ml_product, ov, fab_source):
        return None
    try:
        lf = _cast_cats_lazy(_scan_parquet_compat(str(fp)))
    except Exception as e:
        logger.warning("SplitTable match cache scan failed (product=%s) %s: %s",
                       ml_product, type(e).__name__, e)
        return None
    return {"product": ml_product, "ov": ov, "fab_source": fab_source, "path": fp, "meta": meta, "lf": lf}


def _match_cache_response_meta(product: str) -> dict:
    """Small response payload for UI badges and Agent trace tables."""
    status = _latest_lot_step_cache_status(product)
    if status.get("cache_exists") and int(status.get("product_row_count") or status.get("row_count") or 0) > 0:
        return {
            "hit": True,
            "source": "lot_progress_latest_cache",
            "product": _canonical_mltable_product_name(product, allow_bare=True) or str(product or "").strip(),
            "fab_source": "lot_progress_latest_lot_by_root_wafer",
            "path": status.get("cache_path") or str(_latest_lot_step_cache_path()),
            "built_at": status.get("updated_at") or status.get("latest_updated_at") or "",
            "row_count": int(status.get("product_row_count") or status.get("row_count") or 0),
            "join_keys": ["root_lot_id", "wafer_id"],
            "override_cols": ["lot_id", "fab_lot_id"],
            "fab_col": "lot_id",
            "ts_col": "tkout_time",
            "dedup_keys": ["product", "root_lot_id", "wafer_id"],
        }
    return {"hit": False, "source": "lot_progress_latest_cache"}


def _ensure_match_cache_current(product: str, *, force: bool = False) -> dict | None:
    """Ensure the product FAB projection is persisted before falling back to raw scan."""
    current = _match_cache_current(product)
    if current:
        return current
    ml_product, ov, fab_source = _current_fab_override(product)
    if not ml_product:
        return None
    if not fab_source and not _global_fab_source_paths(""):
        return None
    config_key = _match_cache_config_key(ml_product, ov, fab_source)
    missed = _MATCH_CACHE_AUTO_BUILD_MISS.get(ml_product)
    now = time.time()
    if (
        not force
        and missed
        and missed[1] == config_key
        and now - missed[0] < _MATCH_CACHE_AUTO_BUILD_MISS_TTL_SEC
    ):
        return None
    try:
        result = _refresh_match_cache_products([ml_product], force=force)
        if not result.get("ok"):
            _MATCH_CACHE_AUTO_BUILD_MISS[ml_product] = (now, config_key)
            return None
        _MATCH_CACHE_AUTO_BUILD_MISS.pop(ml_product, None)
        return _match_cache_current(ml_product)
    except Exception as e:
        logger.warning("SplitTable match cache auto-build failed (product=%s) %s: %s",
                       ml_product, type(e).__name__, e, exc_info=True)
        _MATCH_CACHE_AUTO_BUILD_MISS[ml_product] = (now, config_key)
        return None


def _latest_cache_product_values(product: str) -> set[str]:
    raw = str(product or "").strip()
    if not raw:
        return set()
    canonical = _canonical_mltable_product_name(raw, allow_bare=True) or raw
    values = {raw.upper(), canonical.upper()}
    if canonical.upper().startswith("ML_TABLE_"):
        bare = canonical[len("ML_TABLE_"):].strip()
        if bare:
            values.add(bare.upper())
    else:
        values.add(f"ML_TABLE_{canonical}".upper())
    return values


def _latest_lot_step_cache_lf(product: str = "", root_lot_id: str = ""):
    # Per-root fast path (SplitTable pivot-cache 방식): root 검색이면 해당 root
    # 파티션만 읽는다. 파티션이 stale/miss 면 monolithic 풀스캔으로 폴백.
    if str(root_lot_id or "").strip():
        part_lf = _latest_lot_index_partition_lf(product, root_lot_id)
        if part_lf is not None:
            return part_lf
    fp = _latest_lot_step_cache_path()
    if not fp.is_file():
        return None
    try:
        lf = _cast_cats_lazy(_scan_parquet_compat(str(fp)))
        names = lf.collect_schema().names()
    except Exception as e:
        logger.warning("SplitTable latest lot-step cache scan failed (%s) %s: %s",
                       fp, type(e).__name__, e)
        return None
    if LATEST_LOT_STEP_CACHE_FORMAT_COLUMN not in names:
        logger.info("SplitTable ignores legacy latest cache without format version: %s", fp)
        return None
    lf = lf.filter(
        pl.col(LATEST_LOT_STEP_CACHE_FORMAT_COLUMN).cast(pl.Int64, strict=False)
        == LATEST_LOT_STEP_CACHE_FORMAT_VERSION
    )
    if product and "product" in names:
        values = _latest_cache_product_values(product)
        if values:
            lf = lf.filter(pl.col("product").cast(_STR, strict=False).str.to_uppercase().is_in(sorted(values)))
    return lf


# ── Per-root latest-lot cache partitions ─────────────────────────────────────
# The canonical latest-lot cache (lot_progress_latest_lot_by_root_wafer.parquet)
# is a single monolithic file, so every root-scoped lookup (the fab identity
# join in _scan_product, fab-lot snapshots, history scope) re-scanned the whole
# file with a cast+upper filter that defeats parquet predicate pushdown. The
# per-root partition layout is owned by core.latest_lot_partitions and is
# written by BOTH monolithic exporters at write time, so a root search normally
# reads a fresh partition directly. The freshness check + enqueue below remain
# as self-heal only (crash mid-write, files produced by older code): on any
# miss/stale/error the caller falls back to the monolithic scan while a
# rebuild is scheduled.
