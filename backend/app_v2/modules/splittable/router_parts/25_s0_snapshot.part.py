"""Daily product SOP -> immutable SplitTable KNOB S0 assignments.

The source CSV is intentionally kept under ``<db_root>/credential``.  A daily
run reads current POR PPIDs, discovers KNOB columns from ML_TABLE schemas, and
only appends assignments for KNOBs that have never been seen before.  Existing
assignments are immutable so later SOP changes cannot rewrite historical S0.
"""

_S0_STATE_VERSION = 1
_S0_STATE_FILE = PLAN_DIR / "knob_s0_registry.json"
_S0_DAILY_DIR = PLAN_DIR / "knob_s0_daily"
_S0_REFRESH_LOCK = threading.Lock()
_S0_ENSURE_LOCK = threading.Lock()
_S0_LAST_ENSURE_MONOTONIC = 0.0
_S0_ENSURE_INTERVAL_SEC = 300.0
_S0_SCHEDULER_STARTED = False
_S0_SCHEDULER_STOP = threading.Event()
_S0_HEADER_CLEAN_RE = _re.compile(r"[^0-9a-z가-힣]+", _re.I)

_S0_STEP_HEADER_ALIASES = (
    "stepid", "step", "operationid", "operation", "oper", "operno",
    "공정id", "공정", "스텝id", "스텝",
)
_S0_PPID_HEADER_ALIASES = (
    "currentporppid", "porppid", "standardppid", "currentppid", "공정ppid",
    "표준ppid", "ppid", "recipeid", "recipe", "currentpor", "por",
)
_S0_STATUS_HEADER_ALIASES = (
    "ispor", "por여부", "standard여부", "status", "state", "type", "condition", "구분",
)


def _s0_header_key(value: object) -> str:
    return _S0_HEADER_CLEAN_RE.sub("", str(value or "").strip().casefold())


def _s0_find_column(fieldnames: list[str], aliases: tuple[str, ...]) -> str:
    keyed = {_s0_header_key(name): name for name in fieldnames or [] if str(name or "").strip()}
    for alias in aliases:
        hit = keyed.get(_s0_header_key(alias))
        if hit:
            return hit
    return ""


def _s0_is_por_marker(value: object) -> bool:
    marker = str(value or "").strip().casefold()
    return marker in {"1", "y", "yes", "true", "por", "standard", "std", "current", "현재", "기준"}


def _s0_is_non_por_marker(value: object) -> bool:
    marker = str(value or "").strip().casefold()
    return marker in {"0", "n", "no", "false", "split", "experiment", "exp", "비기준"}


def _s0_read_sop_file(path: Path) -> dict[str, dict]:
    """Return case-insensitive step_id -> current POR metadata.

    Both one-row-per-step SOPs and long SOPs with an is_por/status column are
    accepted.  In a long file, an explicitly marked POR row wins.
    """
    if not path.is_file():
        return {}
    out: dict[str, tuple[int, int, dict]] = {}
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            reader = csv_mod.DictReader(handle)
            fields = [str(name or "").lstrip("\ufeff").strip() for name in (reader.fieldnames or [])]
            step_col = _s0_find_column(fields, _S0_STEP_HEADER_ALIASES)
            ppid_col = _s0_find_column(fields, _S0_PPID_HEADER_ALIASES)
            status_col = _s0_find_column(fields, _S0_STATUS_HEADER_ALIASES)
            if not step_col or not ppid_col:
                logger.warning("S0 SOP columns not found: %s (step=%s, ppid=%s)", path.name, step_col, ppid_col)
                return {}
            if status_col == ppid_col:
                status_col = ""
            for index, raw in enumerate(reader):
                row = {str(key or "").lstrip("\ufeff").strip(): value for key, value in (raw or {}).items() if key is not None}
                step_id = str(row.get(step_col) or "").strip()
                ppid = str(row.get(ppid_col) or "").strip()
                if not step_id or not ppid:
                    continue
                status = row.get(status_col) if status_col else ""
                if status_col and _s0_is_non_por_marker(status):
                    priority = 0
                elif status_col and _s0_is_por_marker(status):
                    priority = 2
                else:
                    priority = 1
                key = step_id.casefold()
                previous = out.get(key)
                item = {"step_id": step_id, "ppid": ppid}
                if previous is None or priority > previous[0]:
                    out[key] = (priority, index, item)
        return {key: item[2] for key, item in out.items() if item[0] > 0}
    except Exception as exc:
        logger.warning("S0 SOP read failed (%s): %s", path.name, exc)
        return {}


def _s0_sop_catalog() -> dict[str, dict]:
    """Discover ``credential/<product>_sop.csv`` under configured data roots."""
    catalog: dict[str, dict] = {}
    seen: set[str] = set()
    for root in (_base_root(), _db_base()):
        try:
            resolved = root.resolve()
        except Exception:
            resolved = root
        root_key = str(resolved).casefold()
        if root_key in seen:
            continue
        seen.add(root_key)
        credential = resolved / "credential"
        if not credential.is_dir():
            continue
        try:
            files = sorted(
                (path for path in credential.iterdir() if path.is_file() and path.name.casefold().endswith("_sop.csv")),
                key=lambda path: path.name.casefold(),
            )
        except OSError:
            files = []
        for path in files:
            product_raw = path.name[:-len("_sop.csv")].strip()
            product = _canonical_product_name(product_raw)
            if not product:
                continue
            rows = _s0_read_sop_file(path)
            try:
                stat = path.stat()
                modified = datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
            except OSError:
                modified = ""
            catalog[product.casefold()] = {
                "product": product,
                "file": path.name,
                "path": path,
                "modified_at": modified,
                "rows": rows,
            }
    return catalog


def _s0_archive_daily_parquets(catalog: dict[str, dict], run_date: str,
                               moment: datetime.datetime) -> int:
    """Archive each product SOP as a dated Parquet under credential/<product>/.

    Every file is a complete daily snapshot and carries previous_por_ppid plus a
    change_type column, so it is both independently readable and useful as a
    day-over-day change log.
    """
    archived = 0
    for source in (catalog or {}).values():
        source_path = source.get("path")
        if not isinstance(source_path, Path):
            continue
        product = str(source.get("product") or "").strip()
        if not product:
            continue
        product_dir = source_path.parent / product
        product_dir.mkdir(parents=True, exist_ok=True)
        target = product_dir / f"{run_date}.parquet"
        previous: dict[str, str] = {}
        try:
            prior_files = sorted(
                (path for path in product_dir.glob("*.parquet") if path.name != target.name),
                key=lambda path: path.name,
            )
            if prior_files:
                prior_df = pl.read_parquet(prior_files[-1], columns=["step_id", "por_ppid"])
                previous = {
                    str(step or "").strip().casefold(): str(ppid or "").strip()
                    for step, ppid in prior_df.iter_rows()
                    if str(step or "").strip()
                }
        except Exception:
            previous = {}
        records = []
        current_keys: set[str] = set()
        for item in (source.get("rows") or {}).values():
            step_id = str(item.get("step_id") or "").strip()
            por_ppid = str(item.get("ppid") or "").strip()
            if not step_id or not por_ppid:
                continue
            key = step_id.casefold()
            current_keys.add(key)
            before = previous.get(key, "")
            records.append({
                "snapshot_date": run_date,
                "snapshot_at": moment.isoformat(timespec="seconds"),
                "product": product,
                "step_id": step_id,
                "por_ppid": por_ppid,
                "previous_por_ppid": before,
                "change_type": "added" if not before else ("changed" if before != por_ppid else "unchanged"),
                "source_file": str(source.get("file") or ""),
                "source_modified_at": str(source.get("modified_at") or ""),
            })
        # Removed steps are retained in the daily history even though they are
        # no longer candidates for assigning a new KNOB.
        for key, before in previous.items():
            if key in current_keys:
                continue
            records.append({
                "snapshot_date": run_date,
                "snapshot_at": moment.isoformat(timespec="seconds"),
                "product": product,
                "step_id": key,
                "por_ppid": "",
                "previous_por_ppid": before,
                "change_type": "removed",
                "source_file": str(source.get("file") or ""),
                "source_modified_at": str(source.get("modified_at") or ""),
            })
        columns = [
            "snapshot_date", "snapshot_at", "product", "step_id", "por_ppid",
            "previous_por_ppid", "change_type", "source_file", "source_modified_at",
        ]
        frame = pl.DataFrame(records, schema={column: pl.Utf8 for column in columns}) if records else pl.DataFrame(
            {column: pl.Series([], dtype=pl.Utf8) for column in columns}
        )
        temp = target.with_suffix(f".tmp.{os.getpid()}.parquet")
        try:
            frame.write_parquet(temp)
            os.replace(temp, target)
            archived += 1
        finally:
            with contextlib.suppress(OSError):
                temp.unlink()
    return archived


def _s0_ml_table_products() -> list[str]:
    products: list[str] = []
    seen: set[str] = set()
    for root in (_base_root(), _db_base()):
        try:
            files = root.glob("ML_TABLE_*.parquet")
            for path in files:
                if not path.is_file():
                    continue
                product = _canonical_product_name(path.stem[len("ML_TABLE_"):])
                key = product.casefold()
                if product and key not in seen:
                    seen.add(key)
                    products.append(product)
        except OSError:
            continue
    return sorted(products, key=str.casefold)


def _s0_source_signature(catalog: dict[str, dict]) -> str:
    sop_parts = []
    for key, source in sorted((catalog or {}).items()):
        rows = source.get("rows") or {}
        row_sig = tuple(sorted(
            (str(step), str(item.get("ppid") or ""))
            for step, item in rows.items() if isinstance(item, dict)
        ))
        sop_parts.append((key, str(source.get("file") or ""), str(source.get("modified_at") or ""), row_sig))
    ml_parts = []
    seen: set[str] = set()
    for root in (_base_root(), _db_base()):
        try:
            for path in root.glob("ML_TABLE_*.parquet"):
                resolved = str(path.resolve()).casefold()
                if resolved in seen or not path.is_file():
                    continue
                seen.add(resolved)
                stat = path.stat()
                ml_parts.append((path.name.casefold(), stat.st_mtime_ns, stat.st_size))
        except OSError:
            continue
    raw = repr((sop_parts, sorted(ml_parts))).encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()[:20]


def _s0_step_candidates(product: str, knob: str) -> list[str]:
    """Resolve a KNOB to process step IDs, preferring the same representative
    step used by SplitTable's process-order contract.
    """
    ctx = _split_step_order_context(product)
    candidates: list[str] = []
    key = str(knob or "").strip().upper()
    representative = str((ctx.get("param_step") or {}).get(key) or "").strip()
    if representative:
        candidates.append(representative)

    def add_from_meta(meta: dict | None) -> None:
        for group in (meta or {}).get("groups") or []:
            for step_id in group.get("step_ids") or []:
                candidates.append(str(step_id or "").strip())
            if group.get("step_id"):
                candidates.append(str(group.get("step_id") or "").strip())

    explicit = _build_knob_meta(product) or {}
    add_from_meta(explicit.get(knob) or explicit.get(str(knob).removeprefix("KNOB_")))
    inferred = _inferred_stage_meta(product, "KNOB") or {}
    add_from_meta(inferred.get(knob) or inferred.get(str(knob).removeprefix("KNOB_")))

    ordered: list[str] = []
    seen: set[str] = set()
    for value in candidates:
        clean = str(value or "").strip()
        folded = clean.casefold()
        if clean and folded not in seen:
            seen.add(folded)
            ordered.append(clean)
    return ordered


def _s0_current_candidate(product: str, knob: str, sop_rows: dict[str, dict]) -> dict:
    for step_id in _s0_step_candidates(product, knob):
        hit = (sop_rows or {}).get(step_id.casefold())
        if hit and str(hit.get("ppid") or "").strip():
            return {"step_id": str(hit.get("step_id") or step_id), "ppid": str(hit.get("ppid") or "").strip()}
    return {}


def _s0_empty_state() -> dict:
    return {"schema_version": _S0_STATE_VERSION, "last_run_date": "", "last_run_at": "", "products": {}, "last_stats": {}}


def _s0_load_state() -> dict:
    raw = load_json(_S0_STATE_FILE, {})
    if not isinstance(raw, dict):
        return _s0_empty_state()
    state = _s0_empty_state()
    state.update(raw)
    if not isinstance(state.get("products"), dict):
        state["products"] = {}
    return state


def refresh_knob_s0_snapshots(*, force: bool = False, now: datetime.datetime | None = None) -> dict:
    """Run the daily append-only S0 assignment pass."""
    moment = now or datetime.datetime.now()
    run_date = moment.date().isoformat()
    with _S0_REFRESH_LOCK:
        state = _s0_load_state()
        daily_path = _S0_DAILY_DIR / f"{run_date}.json"
        catalog = _s0_sop_catalog()
        source_signature = _s0_source_signature(catalog)
        if (not force and state.get("last_run_date") == run_date
                and state.get("last_source_signature") == source_signature
                and daily_path.is_file()):
            return {"ok": True, "skipped": True, "run_date": run_date, **(state.get("last_stats") or {})}

        lease = False
        try:
            from core import shared_lease as _shared_lease
            lease = _shared_lease.try_acquire("splittable-knob-s0-daily", 600.0)
            if not lease:
                return {"ok": True, "skipped": True, "busy": True, "run_date": run_date}

            # Re-read after acquiring the cross-process lease; another process may
            # have completed today's pass while this one was waiting.
            state = _s0_load_state()
            if (not force and state.get("last_run_date") == run_date
                    and state.get("last_source_signature") == source_signature
                    and daily_path.is_file()):
                return {"ok": True, "skipped": True, "run_date": run_date, **(state.get("last_stats") or {})}

            archived_products = _s0_archive_daily_parquets(catalog, run_date, moment)
            products_state = state.setdefault("products", {})
            daily_products: dict[str, dict] = {}
            captured = 0
            discovered = 0
            unresolved = 0
            for product in _s0_ml_table_products():
                sop = catalog.get(product.casefold())
                if not sop:
                    continue
                knobs = _mltable_schema_columns(product, "KNOB")
                product_key = _canonical_product_name(product).upper()
                assigned = products_state.setdefault(product_key, {})
                daily_knobs: dict[str, dict] = {}
                for knob in knobs:
                    discovered += 1
                    candidate = _s0_current_candidate(product, knob, sop.get("rows") or {})
                    existing = assigned.get(knob)
                    if not isinstance(existing, dict) and candidate:
                        existing = {
                            "ppid": candidate["ppid"],
                            "step_id": candidate["step_id"],
                            "captured_on": run_date,
                            "captured_at": moment.isoformat(timespec="seconds"),
                            "sop_file": sop.get("file") or "",
                            "sop_modified_at": sop.get("modified_at") or "",
                        }
                        assigned[knob] = existing
                        captured += 1
                    if not isinstance(existing, dict):
                        unresolved += 1
                        existing = {}
                    daily_knobs[knob] = {
                        "current_sop_ppid": candidate.get("ppid", ""),
                        "current_step_id": candidate.get("step_id", ""),
                        "assigned_s0_ppid": existing.get("ppid", ""),
                        "assigned_step_id": existing.get("step_id", ""),
                        "captured_on": existing.get("captured_on", ""),
                        "newly_captured": bool(existing and existing.get("captured_on") == run_date and candidate and existing.get("ppid") == candidate.get("ppid")),
                    }
                daily_products[product_key] = {
                    "sop_file": sop.get("file") or "",
                    "sop_modified_at": sop.get("modified_at") or "",
                    "knobs": daily_knobs,
                }

            stats = {
                "sop_products": len(catalog),
                "sop_parquets_archived": archived_products,
                "ml_products": len(daily_products),
                "knobs_discovered": discovered,
                "knobs_captured": captured,
                "knobs_unresolved": unresolved,
            }
            state.update({
                "schema_version": _S0_STATE_VERSION,
                "last_run_date": run_date,
                "last_run_at": moment.isoformat(timespec="seconds"),
                "last_source_signature": source_signature,
                "last_stats": stats,
            })
            save_json(_S0_STATE_FILE, state, indent=2)
            save_json(daily_path, {
                "schema_version": _S0_STATE_VERSION,
                "run_date": run_date,
                "run_at": moment.isoformat(timespec="seconds"),
                "stats": stats,
                "products": daily_products,
            }, indent=2)
            logger.info("SplitTable KNOB S0 daily snapshot: %s", stats)
            return {"ok": True, "skipped": False, "run_date": run_date, **stats}
        finally:
            if lease:
                try:
                    _shared_lease.release("splittable-knob-s0-daily")
                except Exception:
                    pass


def _ensure_knob_s0_snapshots_today() -> None:
    global _S0_LAST_ENSURE_MONOTONIC
    current = time.monotonic()
    if current - _S0_LAST_ENSURE_MONOTONIC < _S0_ENSURE_INTERVAL_SEC:
        return
    with _S0_ENSURE_LOCK:
        current = time.monotonic()
        if current - _S0_LAST_ENSURE_MONOTONIC < _S0_ENSURE_INTERVAL_SEC:
            return
        _S0_LAST_ENSURE_MONOTONIC = current
        try:
            refresh_knob_s0_snapshots()
        except Exception:
            logger.warning("SplitTable KNOB S0 daily ensure failed", exc_info=True)


def _knob_s0_for_product(product: str, columns: list[str] | None = None) -> dict[str, dict]:
    _ensure_knob_s0_snapshots_today()
    state = _s0_load_state()
    product_key = _canonical_product_name(product).upper()
    assigned = (state.get("products") or {}).get(product_key) or {}
    wanted = {str(column) for column in (columns or []) if str(column).upper().startswith("KNOB_")}
    out: dict[str, dict] = {}
    for knob, raw in assigned.items():
        if wanted and knob not in wanted:
            continue
        if not isinstance(raw, dict) or not str(raw.get("ppid") or "").strip():
            continue
        out[str(knob)] = {
            "ppid": str(raw.get("ppid") or ""),
            "step_id": str(raw.get("step_id") or ""),
            "captured_on": str(raw.get("captured_on") or ""),
        }
    return out


def _knob_s0_scheduler_loop() -> None:
    # Startup bootstrap handles all existing KNOBs.  The short wake interval is
    # cheap because the refresh itself is date-gated; it also tolerates laptops
    # sleeping across the nominal daily boundary.
    while not _S0_SCHEDULER_STOP.wait(900.0):
        try:
            refresh_knob_s0_snapshots()
        except Exception:
            logger.warning("SplitTable KNOB S0 scheduler tick failed", exc_info=True)


def start_knob_s0_snapshot_scheduler() -> bool:
    global _S0_SCHEDULER_STARTED
    if _S0_SCHEDULER_STARTED:
        return False
    _S0_SCHEDULER_STARTED = True
    try:
        refresh_knob_s0_snapshots()
    except Exception:
        logger.warning("SplitTable KNOB S0 startup bootstrap failed", exc_info=True)
    thread = threading.Thread(target=_knob_s0_scheduler_loop, name="splittable-knob-s0-daily", daemon=True)
    thread.start()
    logger.info("SplitTable KNOB S0 daily scheduler started")
    return True
