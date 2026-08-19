PLAN_HISTORY_LOG_SUBDIR = "plan_history_log"
_PLAN_HISTORY_LOG_LOCK = threading.Lock()
_PLAN_HISTORY_FACET_COLUMN_CAP = 500


def _plan_history_log_path(product: str) -> Path:
    # PLAN_DIR 은 테스트가 monkeypatch 하는 지점이라 **호출 시점에** 읽는다.
    # 모듈 상수로 굳히면 PLAN_DIR 만 갈아끼운 테스트가 실제 데이터 루트에 쓴다.
    return PLAN_DIR / PLAN_HISTORY_LOG_SUBDIR / f"{_plan_product_name(product)}.jsonl"


def _plan_history_entry_key(entry: dict) -> str:
    return "".join(
        str(entry.get(k) if entry.get(k) is not None else "")
        for k in ("time", "user", "action", "cell", "old", "new")
    )


PLAN_REASON_MAX_LEN = 500


def _clean_plan_reason(reason: Any) -> str:
    """변경 사유. 선택 입력이라 비어 있는 게 정상이고, 길이만 잘라 둔다
    (이력 파일이 자유 텍스트로 무한정 커지지 않게)."""
    text = str(reason or "").strip()
    return text[:PLAN_REASON_MAX_LEN]


def _new_history_batch_id() -> str:
    """한 번의 저장/삭제 요청을 묶는 식별자 — 화면에서 '이 사람이 이때 한 작업'
    단위로 접어 볼 수 있게 한다."""
    return os.urandom(6).hex()


def _archive_plan_history(product: str, entries: list[dict],
                          prior_history: list[dict] | None = None) -> None:
    """새 이력을 아카이브에 덧붙인다. 아카이브가 아직 없으면 기존 JSON 이력으로 seed
    해서, 아카이브 도입 이전 기록도 한 화면에서 이어 보이게 한다."""
    if not entries:
        return
    try:
        from core.utils import jsonl_append
        path = _plan_history_log_path(product)
        with _PLAN_HISTORY_LOG_LOCK:
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                for old_row in (prior_history or []):
                    if isinstance(old_row, dict):
                        jsonl_append(path, old_row, add_timestamp=False, max_lines=None)
            for row in entries:
                jsonl_append(path, row, add_timestamp=False, max_lines=None)
    except Exception as exc:
        logger.warning(f"plan history archive append failed for {product}: {exc}")


def _plan_history_entries(product: str) -> list[dict]:
    """JSON 창 + 아카이브를 합친 전체 이력 (시간 오름차순)."""
    inline = [h for h in (_load_plan_data(product).get("history") or [])
              if isinstance(h, dict)]
    try:
        from core.utils import jsonl_read
        archived = [h for h in jsonl_read(_plan_history_log_path(product), limit=0)
                    if isinstance(h, dict)]
    except Exception:
        archived = []
    if not archived:
        return inline
    seen = {_plan_history_entry_key(h) for h in archived}
    merged = archived + [h for h in inline if _plan_history_entry_key(h) not in seen]
    merged.sort(key=lambda h: str(h.get("time") or ""))
    return merged


def _history_cell_parts(entry: dict) -> tuple[str, str, str]:
    """엔트리에서 (root, wafer, column). 새 엔트리는 필드로 갖고 있고, 예전
    엔트리는 cell key 를 쪼개서 얻는다."""
    root, wafer, column = _split_plan_cell_key(entry.get("cell") or "")
    return (str(entry.get("root_lot_id") or root or ""),
            str(entry.get("wafer_id") or wafer or ""),
            str(entry.get("column") or column or ""))


def _history_matches_root(entry: dict, root_lot_id: str) -> bool:
    if not root_lot_id:
        return True
    return (entry.get("root_lot_id") == root_lot_id
            or str(entry.get("cell") or "").startswith(root_lot_id + "|"))


def _history_matches_filters(entry: dict, *, user: str = "", action: str = "",
                             column: str = "", wafer_id: str = "", q: str = "",
                             since: str = "", until: str = "",
                             has_reason: bool = False) -> bool:
    _root, wafer, col = _history_cell_parts(entry)
    if has_reason and not str(entry.get("reason") or "").strip():
        return False
    if user and str(entry.get("user") or "") != user:
        return False
    if action and str(entry.get("action") or "") != action:
        return False
    if column and column.lower() not in col.lower():
        return False
    if wafer_id and wafer_id.lower() not in wafer.lower():
        return False
    when = str(entry.get("time") or "")
    # ISO 문자열은 사전순 비교가 곧 시간순 비교다. until 은 그 날 전체를 포함한다.
    if since and when[:len(since)] < since:
        return False
    if until and when[:10] > until[:10]:
        return False
    if q:
        needle = q.lower()
        haystack = " ".join(str(entry.get(k) or "") for k in
                            ("user", "cell", "old", "new", "action", "reason"))
        if needle not in haystack.lower():
            return False
    return True


def _plan_history_facets(entries: list[dict]) -> dict:
    """필터 드롭다운 소스 — 필터 적용 전 집합에서 뽑아야 선택지가 사라지지 않는다."""
    users: set[str] = set()
    actions: set[str] = set()
    columns: set[str] = set()
    for entry in entries:
        if entry.get("user"):
            users.add(str(entry["user"]))
        if entry.get("action"):
            actions.add(str(entry["action"]))
        _root, _wafer, col = _history_cell_parts(entry)
        if col:
            columns.add(col)
    return {
        "users": sorted(users),
        "actions": sorted(actions),
        "columns": sorted(columns)[:_PLAN_HISTORY_FACET_COLUMN_CAP],
    }


# ── root 별 plan 인덱스 ────────────────────────────────────────────────────────
# 저장 파일은 그대로 두고(포맷 변경 없음, 마이그레이션 없음) **읽는 방법만** 바꾼다.
#
# 문제였던 것: JSON 파싱은 이미 load_json_cached 로 캐시돼 있었는데(0.04ms),
# _load_plan_data 가 요청마다 제품의 plan 전체를 새 dict 로 복사하고 history 를
# json.dumps 로 dedup 했다(3.5ms, 90배). 그런데 /view 가 실제로 쓰는 건 "이 root
# 의 plan" 뿐이고 history 는 아예 안 본다.
#
# 그래서 파일 시그니처가 그대로면 root 별로 쪼갠 인덱스를 재사용한다. 인덱스를
# 만드는 비용(전체 1회 순회)은 파일이 바뀔 때만 든다.
_PLAN_ROOT_INDEX_CACHE: dict[str, tuple[tuple, dict[str, dict]]] = {}
_PLAN_ROOT_INDEX_LOCK = threading.Lock()
_PLAN_ROOT_INDEX_MAX = 64

# 반환값을 수정하면 다음 요청이 오염된다 — 읽기 전용 계약이다(load_json_cached 와
# 같은 규칙). 편집 경로는 계속 _load_plan_data 로 자기 사본을 받는다.
_EMPTY_READONLY: dict = {}


def _split_key_root(key: str) -> str:
    """`root|wafer|column` 에서 root 만. 형식이 아니면 빈 문자열."""
    text = str(key or "")
    idx = text.find("|")
    return text[:idx] if idx > 0 else ""


def _build_root_index(values: dict) -> dict[str, dict]:
    """`root|...` 키 dict → {root: {원본키: 값}}."""
    index: dict[str, dict] = {}
    for key, value in values.items():
        root = _split_key_root(key)
        if not root:
            continue
        index.setdefault(root, {})[key] = value
    return index


def _plan_entries_for_root(product: str, root_lot_id: str) -> dict:
    """이 root 의 plan 엔트리만 (읽기 전용).

    반환 dict 의 키는 원본과 동일한 `root|wafer|column` 이라, 호출측은 기존
    `plans.get(f"{root}|{wafer}|{col}")` 코드를 그대로 쓴다.
    """
    root = str(root_lot_id or "").strip()
    if not root:
        return _EMPTY_READONLY
    paths = _plan_alias_paths(product)
    sig = _plan_risk_cache_sig(paths)
    cache_key = str(product or "")
    with _PLAN_ROOT_INDEX_LOCK:
        hit = _PLAN_ROOT_INDEX_CACHE.get(cache_key)
        if hit is not None and hit[0] == sig:
            return hit[1].get(root) or _EMPTY_READONLY

    # alias 병합은 _load_plan_data 와 같은 순서(뒤가 이김)를 유지한다.
    merged_plans: dict = {}
    for fp in paths:
        data = load_json_cached(fp, {}) if fp.exists() else {}
        if not isinstance(data, dict):
            continue
        plans = data.get("plans")
        if isinstance(plans, dict):
            merged_plans.update(plans)
    index = _build_root_index(merged_plans)

    with _PLAN_ROOT_INDEX_LOCK:
        if len(_PLAN_ROOT_INDEX_CACHE) >= _PLAN_ROOT_INDEX_MAX:
            _PLAN_ROOT_INDEX_CACHE.clear()
        _PLAN_ROOT_INDEX_CACHE[cache_key] = (sig, index)
    return index.get(root) or _EMPTY_READONLY


def _plan_risk_cache_key(product: str, include_deleted: bool) -> tuple[str, bool]:
    fp = _plan_history_path(product)
    try:
        return (str(fp.resolve()), bool(include_deleted))
    except Exception:
        return (str(fp), bool(include_deleted))


def _plan_risk_cache_sig(fp: Path | list[Path]) -> tuple:
    if isinstance(fp, list):
        return tuple(_plan_risk_cache_sig(p) for p in fp)
    try:
        st = fp.stat()
        return (str(fp.resolve()), st.st_mtime, st.st_size)
    except Exception:
        return (str(fp), 0.0, 0)


def _empty_plan_risk_payload(cache: bool = False) -> dict:
    return {"final": [], "drift": [], "drift_count": 0, "total_cells": 0, "cache": cache}


def _copy_plan_risk_payload(payload: dict, root_lot_id: str = "") -> dict:
    root = str(root_lot_id or "").strip()
    if root:
        by_root = payload.get("_by_root") if isinstance(payload.get("_by_root"), dict) else {}
        scoped = by_root.get(root) or {"final": [], "drift": []}
        final_rows = [dict(r) for r in (scoped.get("final") or [])]
        drift_rows = [dict(r) for r in (scoped.get("drift") or [])]
    else:
        final_rows = [dict(r) for r in (payload.get("final") or [])]
        drift_rows = [dict(r) for r in (payload.get("drift") or [])]
    return {
        "final": final_rows,
        "drift": drift_rows,
        "drift_count": len(drift_rows),
        "total_cells": len(final_rows),
        "cache": bool(payload.get("cache")),
        "cache_built_at": payload.get("cache_built_at", ""),
    }


def _build_plan_risk_payload(hist: list, include_deleted: bool = False) -> dict:
    per_cell: dict[str, list] = {}
    for h in hist or []:
        if not isinstance(h, dict):
            continue
        ck = h.get("cell")
        if not ck:
            continue
        per_cell.setdefault(str(ck), []).append(h)

    final_rows = []
    drift_rows = []
    for ck, entries in per_cell.items():
        entries.sort(key=lambda x: x.get("time", ""))
        last = entries[-1]
        action = last.get("action") or "set"
        if action == "delete" and not include_deleted:
            continue
        sets = [e for e in entries if (e.get("action") or "set") == "set"]
        distinct_values = list({e.get("new") for e in sets if e.get("new") is not None})
        distinct_users = list({e.get("user") for e in sets if e.get("user")})
        set_count = len(sets)
        delete_count = sum(1 for e in entries if e.get("action") == "delete")
        drift_flags = []
        if set_count >= 2 and len(distinct_values) >= 2:
            drift_flags.append("multi_change")
        if len(distinct_users) >= 2:
            drift_flags.append("multi_user")
        if delete_count >= 1 and set_count >= 1:
            drift_flags.append("reinstated")
        parts = (ck or "").split("|")
        lot = parts[0] if len(parts) > 0 else ""
        wf = parts[1] if len(parts) > 1 else ""
        col = parts[2] if len(parts) > 2 else ""
        row = {
            "cell": ck,
            "root_lot_id": lot,
            "wafer_id": wf,
            "column": col,
            "final_value": last.get("new"),
            "final_action": action,
            "final_user": last.get("user"),
            "final_time": last.get("time"),
            "set_count": set_count,
            "delete_count": delete_count,
            "distinct_values": distinct_values,
            "distinct_users": distinct_users,
            "drift": drift_flags,
            # 지금 값이 왜 이렇게 됐는지 = 마지막 변경의 사유. 그 변경에 사유가
            # 없으면 이 셀에 마지막으로 남은 사유로 폴백해 최소한의 맥락은 남긴다.
            "final_reason": _clean_plan_reason(last.get("reason")),
            "last_reason": next((_clean_plan_reason(e.get("reason"))
                                 for e in reversed(entries)
                                 if str(e.get("reason") or "").strip()), ""),
            "reason_count": sum(1 for e in entries if str(e.get("reason") or "").strip()),
        }
        final_rows.append(row)
        if drift_flags:
            drift_rows.append(row)

    final_rows.sort(key=lambda r: r.get("final_time") or "", reverse=True)
    drift_rows.sort(key=lambda r: r.get("final_time") or "", reverse=True)
    by_root: dict[str, dict[str, list]] = {}
    for row in final_rows:
        root = str(row.get("root_lot_id") or "").strip()
        if not root:
            continue
        bucket = by_root.setdefault(root, {"final": [], "drift": []})
        bucket["final"].append(row)
        if row.get("drift"):
            bucket["drift"].append(row)

    return {
        "final": final_rows,
        "drift": drift_rows,
        "drift_count": len(drift_rows),
        "total_cells": len(final_rows),
        "_by_root": by_root,
    }


def _get_plan_risk_payload(product: str, include_deleted: bool = False, force: bool = False) -> dict:
    paths = _plan_alias_paths(product)
    if not any(fp.exists() for fp in paths):
        return _empty_plan_risk_payload(cache=True)
    # 아카이브도 sig 에 넣는다 — 최종 Log 의 변경 횟수/사용자 수를 1000건 창이
    # 아니라 전체 이력에서 세기 때문에, 아카이브만 갱신돼도 무효화돼야 한다.
    sig = _plan_risk_cache_sig(paths + [_plan_history_log_path(product)])
    key = _plan_risk_cache_key(product, include_deleted)
    with _PLAN_RISK_CACHE_LOCK:
        cached = _PLAN_RISK_CACHE.get(key)
        if cached and not force and cached.get("_sig") == sig:
            return cached
    hist = _plan_history_entries(product)
    payload = _build_plan_risk_payload(hist if isinstance(hist, list) else [], include_deleted=include_deleted)
    payload.update({
        "_sig": sig,
        "cache": True,
        "cache_built_at": datetime.datetime.now().isoformat(timespec="seconds"),
    })
    with _PLAN_RISK_CACHE_LOCK:
        if len(_PLAN_RISK_CACHE) >= _PLAN_RISK_CACHE_MAX:
            try:
                _PLAN_RISK_CACHE.pop(next(iter(_PLAN_RISK_CACHE)))
            except Exception:
                _PLAN_RISK_CACHE.clear()
        _PLAN_RISK_CACHE[key] = payload
    return payload


def _invalidate_plan_risk_cache(product: str) -> None:
    if not product:
        return
    keys = {
        _plan_risk_cache_key(product, False),
        _plan_risk_cache_key(product, True),
    }
    with _PLAN_RISK_CACHE_LOCK:
        for key in keys:
            _PLAN_RISK_CACHE.pop(key, None)


def refresh_plan_risk_cache(product: str = "", force: bool = False) -> dict:
    products = [product] if str(product or "").strip() else []
    if not products:
        try:
            products = [p.get("name") for p in list_products().get("products", []) if p.get("name")]
        except Exception:
            products = []
    results = []
    for raw_product in products:
        fp = _plan_history_path(raw_product)
        if not fp.exists():
            results.append({"product": raw_product, "ok": True, "skipped": True, "reason": "no plan history"})
            continue
        try:
            payload = _get_plan_risk_payload(raw_product, include_deleted=False, force=force)
            results.append({
                "product": raw_product,
                "ok": True,
                "skipped": False,
                "total_cells": int(payload.get("total_cells") or 0),
                "drift_count": int(payload.get("drift_count") or 0),
            })
        except Exception as e:
            logger.warning("plan risk cache refresh failed (product=%s) %s: %s",
                           raw_product, type(e).__name__, e)
            results.append({"product": raw_product, "ok": False, "reason": f"{type(e).__name__}: {e}"})
    return {"ok": all(r.get("ok") for r in results) if results else True, "products": results}


def _candidate_values_from_frame(rows, value_col: str = "v", limit: int = 500) -> list[str]:
    """Return clean string autocomplete values from a collected Polars frame."""
    values: list[str] = []
    seen: set[str] = set()
    try:
        limit = max(1, int(limit or 500))
    except Exception:
        limit = 500
    if rows is None or value_col not in rows.columns:
        return values
    for value in rows[value_col].to_list():
        text = _clean_str(value)
        if not text:
            continue
        key = text.upper()
        if key in seen:
            continue
        seen.add(key)
        values.append(text)
        if len(values) >= limit:
            break
    return values


def _limited_unique_values(lf, col: str, prefix: str = "", limit: int = 500,
                           preview_only: bool = True) -> list[str]:
    """Return bounded autocomplete values without scanning broad empty-prefix lists.

    Empty dropdowns only need a preview.  Once a user types, prefix filtering must
    search the full source so values outside the preview are still discoverable.
    """
    try:
        limit = max(1, int(limit or 500))
    except Exception:
        limit = 500
    prefix = prefix if isinstance(prefix, str) else ""
    q = (
        lf.select(pl.col(col).cast(_STR, strict=False).alias("v"))
        .filter(pl.col("v").is_not_null())
    )
    if prefix.strip():
        q = q.filter(_contains_literal_ci_expr("v", prefix))
        rows = q.unique().sort("v").head(limit).collect()
    elif not preview_only:
        rows = q.unique().sort("v").head(limit).collect()
    else:
        sample_limit = max(limit, min(limit * 20, 10000))
        rows = q.head(sample_limit).unique(maintain_order=True).head(limit).collect()
    values = _candidate_values_from_frame(rows, "v", limit)
    return sorted(values, key=lambda s: str(s).upper())


def _split_table_cache_dir(product: str) -> Path:
    """Pre-pivoted split_table 캐시 디렉터리 (root_lot_id 당 parquet 1개).

    캐시 디렉터리는 canonical ML_TABLE_* 대문자 이름으로 저장된다 — raw product
    문자열로 찾으면 대소문자 구분 FS(운영 Linux)에서 조용히 빗나간다.
    """
    from app_v2.modules.splittable.cache_builder import canonical_product_dir

    base = PATHS.db_cache_dir if hasattr(PATHS, "db_cache_dir") else Path("data/cache")
    return base / "split_table" / (canonical_product_dir(product) or str(product or ""))


def _main_table_candidates(product: str, col: str = "root_lot_id", prefix: str = "",
                           limit: int = 500, root_lot_id: str = "") -> dict:
    """Return candidates from the actual SplitTable render source.

    FAB history can contain operational roots that are not present in the
    current ML_TABLE. Those roots are useful for lineage, but they produce an
    empty SplitTable view. Autocomplete should therefore prefer values that can
    actually render in /view.
    """
    try:
        limit = max(1, int(limit or 500))
    except Exception:
        limit = 500

    cache_key = (
        "main_table_candidates",
        _lot_lookup_cache_sig(product),
        str(product or "").strip(),
        str(col or "").strip(),
        str(prefix or "").strip(),
        str(root_lot_id or "").strip(),
        limit,
    )
    cached = _lot_lookup_cache_get(cache_key)
    if cached is not None:
        return cached

    def finish(payload: dict) -> dict:
        return _lot_lookup_cache_set(cache_key, payload)

    try:
        lookup_meta = {}

        split_table_cache_dir = _split_table_cache_dir(product)
        catalog_path = split_table_cache_dir / "_lot_catalog.json"
        if catalog_path.exists():
            try:
                import json
                with open(catalog_path, "r", encoding="utf-8") as f:
                    catalog = json.load(f)

                target_col = next((k for k in catalog.keys() if k.casefold() == str(col or "").casefold()), None)
                if not target_col and str(col or "").casefold() in {c.casefold() for c in _FAB_COL_CANDIDATES}:
                    target_col = next((k for k in catalog.keys() if k.casefold() == "fab_lot_id"), None)
                    if not target_col:
                        target_col = next((k for k in catalog.keys() if k.casefold() == "lot_id"), None)

                if target_col and target_col in catalog:
                    cands = catalog[target_col]
                    if prefix.strip():
                        cands = [c for c in cands if prefix.strip().upper() in str(c).upper()]
                    if cands:
                        return finish({
                            "col": col,
                            "candidates": cands[:limit],
                            "prefix": prefix,
                            "root_scope": "",
                            "match_mode": "pivot_catalog_fast",
                            "source": "pivot_catalog",
                            "fab_source": "",
                            "lookup_cache": lookup_meta,
                            "strict": False,
                        })
            except Exception as e:
                logger.warning(f"Failed to read _lot_catalog.json for {product}: {e}")

        if str(col or "").casefold() == "root_lot_id":
            lookup = _root_lot_lookup_cache_candidates(product, prefix=prefix, limit=limit)
            if lookup is not None:
                lookup_meta = _lookup_cache_public_meta(lookup)
                candidates = lookup.get("candidates") or []
                if candidates:
                    return finish({
                        "candidates": candidates,
                        "source_col": "root_lot_id",
                        "root_ids": candidates,
                        "match_mode": "lookup_cache_roots",
                        "lookup_cache": lookup_meta,
                    })
                if lookup.get("has_cache") and not lookup.get("source_stale") and prefix.strip():
                    # prefix 검색에서 fresh 캐시가 빈 결과면 그대로 신뢰한다
                    # (여기서 raw 폴백하면 키 입력마다 원천 전체 스캔이 된다).
                    return finish({
                        "candidates": [],
                        "source_col": "root_lot_id",
                        "root_ids": [],
                        "match_mode": "lookup_cache_roots",
                        "lookup_cache": lookup_meta,
                    })
                # 빈 prefix(초기 목록)인데 fresh 캐시가 비어 있으면 캐시가 잘못
                # 빌드된 회귀일 수 있으므로 아래 bounded raw preview 로 재확인한다.
                source_fp = lookup.get("source_fp")
                if source_fp and _split_view_should_defer_raw_fallback(source_fp) and (
                    not lookup.get("has_cache") or lookup.get("source_stale")
                ):
                    queued = _ml_table_lookup.enqueue_build(source_fp)
                    lookup_meta = _lookup_cache_public_meta(lookup, queued)
            # lookup 캐시로 못 채웠을 때만 split_table 캐시 디렉터리를 본다.
            # 이건 **이미 캐시된 root 만** 열거하므로 완전한 목록이 아니다 —
            # 예전엔 이 분기가 맨 앞에 있어서 캐시된 4개만 나오고 나머지 26개
            # root 는 제품 선택 후 드롭다운에서 아예 안 보였다.
            split_table_cache_dir = _split_table_cache_dir(product)
            if split_table_cache_dir.exists():
                cands = sorted(fp.stem for fp in split_table_cache_dir.glob("*.parquet"))
                if prefix.strip():
                    cands = [c for c in cands if prefix.strip().upper() in c.upper()]
                if cands:
                    return finish({
                        "col": "root_lot_id",
                        "candidates": cands[:limit],
                        "prefix": prefix,
                        "root_scope": "",
                        "match_mode": "split_table_cache_fast",
                        "source": "split_table_cache",
                        "fab_source": "",
                        "lookup_cache": lookup_meta,
                        "strict": False,
                    })
        lf = _scan_product_base(product)
        schema_names = lf.collect_schema().names()
        lot_col, _ = _detect_lot_wafer(lf, product)
        target = ""
        if str(col or "").casefold() == "root_lot_id":
            target = lot_col or _ci_resolve_in("root_lot_id", schema_names)
        elif str(col or "").casefold() in {c.casefold() for c in _FAB_COL_CANDIDATES}:
            target = (
                _ci_resolve_in("fab_lot_id", schema_names)
                or _ci_resolve_in("lot_id", schema_names)
                or _pick_first_present_ci(_FAB_COL_CANDIDATES, schema_names)
            )
        else:
            target = _ci_resolve_in(col, schema_names)
        if not target or target not in schema_names:
            return finish({"candidates": [], "source_col": target or col, "root_ids": []})

        root_scope = _clean_str(root_lot_id)
        if root_scope:
            root_col = lot_col or _ci_resolve_in("root_lot_id", schema_names)
            if root_col and root_col in schema_names:
                lf = lf.filter(_join_key_expr(root_col) == root_scope.upper())

        # 빈 prefix 드롭다운(초기 root_lot_id 목록)은 미리보기 앞부분 N개면 충분하다.
        # 전체 컬럼을 unique + sort 하면 큰 원천에서 수 초가 걸려 즉시 뜨지 않으므로,
        # 앞부분만 샘플링해 즉시 응답한다. 사용자가 입력하면 _limited_unique_values 가
        # prefix 로 원천 전체를 서버에서 필터링하므로 미리보기 밖 값도 검색된다.
        # (root_scope 가 지정된 fab_lot_id 조회는 이미 좁혀진 집합이라 전체 스캔해도 빠르다.)
        preview_only = not bool(root_scope)
        values = _limited_unique_values(lf, target, prefix=prefix, limit=limit,
                                        preview_only=preview_only)
        payload = {"candidates": values, "source_col": target, "root_ids": values if str(col or "").casefold() == "root_lot_id" else []}
        if str(col or "").casefold() == "root_lot_id":
            payload["match_mode"] = "splittable_roots"
            if lookup_meta:
                payload["lookup_cache"] = lookup_meta
        return finish(payload)
    except Exception as e:
        logger.warning("_main_table_candidates 실패 (product=%s col=%s) %s: %s",
                       product, col, type(e).__name__, e)
        return finish({"candidates": [], "source_col": col, "root_ids": []})


def _scan_product(
    product: str,
    root_lot_id: str = "",
    fab_lot_id: str = "",
    wafer_ids: str = "",
    base_lf=None,
    runtime_profile: dict | None = None,
):
    """Scan ML_TABLE_<PROD>.parquet + hive override join.

    v8.8.26: 실패 경로마다 logger.warning 로 가시화 (이전 blanket except 제거).
      - CI align 이후 fab schema 를 **재조회** 해서 rename 이 실제로 적용됐는지 확인.
      - override_cols 가 join_keys 만 남으면 경고 후 raw lf 반환.
    """
    product = _canonical_mltable_product_name(product, allow_bare=True) or str(product or "").strip()
    lf = base_lf
    if lf is not None and runtime_profile is not None:
        runtime_profile["root_cache_hit"] = True
    if lf is None:
        lf = _scan_product_base_lookup_cache(
            product,
            root_lot_id=root_lot_id,
            wafer_ids=wafer_ids,
            runtime_profile=runtime_profile,
        )
    if lf is None:
        lf = _scan_product_base(product)

    # v8.8.3: 오버라이드 로직 근본 재정리.
    #   1) 매뉴얼 config(lot_overrides[product].fab_source) 가 있으면 그 값을 사용.
    #   2) 없으면 ML_TABLE_<PROD> → DB/<root>/<PROD> 자동 매칭 시도.
    #   3) ts_col / fab_col 도 매뉴얼 > 자동 추론 순.
    #   4) 조인은 항상 "ts_col 기준 최신 레코드만" join keys 별로 picking 후 left-join.
    try:
        product, ov, fab_source = _current_fab_override(product)
        include_all = _foreground_global_fab_scan_enabled()

        try:
            main_names_list = lf.collect_schema().names()
        except Exception as e:
            logger.warning("_scan_product: main schema 조회 실패 (product=%s) %s: %s",
                           product, type(e).__name__, e)
            return lf
        if root_lot_id or wafer_ids:
            try:
                main_lot_col, main_wf_col = _detect_lot_wafer(lf, product)
                lf = _filter_lot_wafer(
                    lf, main_lot_col, main_wf_col,
                    root_lot_id=root_lot_id,
                    wafer_ids=wafer_ids,
                )
            except Exception as e:
                logger.warning("_scan_product: main scope filter 실패 (product=%s root=%s wafer=%s) %s: %s",
                               product, root_lot_id, wafer_ids, type(e).__name__, e)

        cached = _latest_lot_progress_projection(
            product, main_names_list,
            root_lot_id=root_lot_id,
            fab_lot_id=fab_lot_id,
            wafer_ids=wafer_ids,
        )
        if cached:
            return _join_fab_projection_into_main(
                lf, set(main_names_list), cached["lf"],
                cached["join_keys"], cached["override_cols"],
                fab_has_join_tmp=True,
            )

        if not fab_source and not _global_fab_source_paths("", include_all=include_all):
            return _strip_non_authoritative_fab_fields(lf, product)

        # Fast layer: read only the searched root's FAB partition from the
        # precomputed per-root index, bounding the latest-lot pick to O(one root)
        # instead of scanning the whole FAB source. Additive — a miss returns None
        # and we fall back to the full scan below while a build is scheduled.
        fab_lf = None
        fab_sources: list = []
        if str(root_lot_id or "").strip():
            fab_lf = _fab_lot_index_scan_root(
                product, root_lot_id, fab_source=fab_source, include_all=include_all)
            if fab_lf is not None:
                fab_sources = ["<fab_lot_index>"]
        if fab_lf is None:
            if str(root_lot_id or "").strip():
                enqueued = _enqueue_fab_lot_index_build(
                    product, fab_source, include_all=include_all, reason="scan_miss")
                canonical_product = (
                    _canonical_mltable_product_name(product, allow_bare=True)
                    or str(product or "").strip().upper()
                )
                with _FAB_IDX_BUILD_LOCK:
                    fab_index_running = canonical_product in _FAB_IDX_BUILD_INPROGRESS
                runtime_profile = runtime_profile if runtime_profile is not None else {}
                runtime_profile["fab_index_queued"] = bool(
                    enqueued or fab_index_running
                )
                runtime_profile["cache_incomplete"] = True
                # Never scan a multi-GB FAB tree in an interactive root request.
                # The main ML table is still immediately useful; the worker-built
                # per-root FAB index will refresh labels on the next UI poll.
                if not _env_bool("FLOW_SPLITTABLE_INTERACTIVE_FAB_RAW_FALLBACK", False):
                    return _strip_non_authoritative_fab_fields(lf, product)
            fab_lf, fab_sources = _scan_global_fab_sources(fab_source, include_all=include_all)
        if fab_lf is None:
            logger.warning("_scan_product: FAB source scan 실패 (product=%s fab_source=%s sources=%s)",
                           product, fab_source, fab_sources)
            return _strip_non_authoritative_fab_fields(lf, product)

        # v8.8.22: CI 정렬 — fab_lf 컬럼명을 main 쪽 casing 으로 rename.
        #   ex) ML_TABLE 의 ROOT_LOT_ID ↔ hive root_lot_id → join 성공.
        fab_lf, _ = _ci_align_fab_to_main(fab_lf, main_names_list)
        # v8.8.26: rename 이 silently 실패할 수 있으므로 schema 를 재조회 — 신뢰 가능한 true state.
        try:
            fab_schema_names = fab_lf.collect_schema().names()
        except Exception as e:
            logger.warning("_scan_product: fab post-align schema 조회 실패 (product=%s) %s: %s",
                           product, type(e).__name__, e)
            return lf
        main_names = set(main_names_list)
        fab_names = set(fab_schema_names)

        join_keys = ov.get("join_keys") or []
        if isinstance(join_keys, str):
            join_keys = [k.strip() for k in join_keys.split(",") if k.strip()]
        if join_keys:
            mapped = []
            for k in join_keys:
                actual = _ci_resolve_in(k, main_names_list) or _resolve_source_col_name(k, fab_schema_names)
                if actual:
                    mapped.append(actual)
            join_keys = mapped
        if not join_keys:
            join_keys = _default_override_join_keys(main_names_list, fab_schema_names)
        join_keys = [k for k in join_keys if k in main_names and k in fab_names]
        if not join_keys:
            logger.warning(
                "_scan_product: 공통 join key 없음 (product=%s fab_source=%s main=%s fab=%s)",
                product, fab_source, main_names_list[:20], fab_schema_names[:20],
            )
            return lf

        fc_raw = (ov.get("fab_col") or "").strip()
        fab_col = (_resolve_source_col_name(fc_raw, fab_schema_names) if fc_raw else "") \
                  or _pick_first_present_ci(_FAB_COL_CANDIDATES, fab_schema_names)
        if not fab_col:
            fab_col = "fab_lot_id"
        tc_raw = (ov.get("ts_col") or "").strip()
        ts_col = (_resolve_source_col_name(tc_raw, fab_schema_names) if tc_raw else "") \
                 or _pick_ts_col(fab_schema_names)
        fab_lf = _apply_fab_scope_filters(
            fab_lf, fab_schema_names, ov,
            root_lot_id=root_lot_id,
            fab_lot_id=fab_lot_id,
            wafer_ids=wafer_ids,
            fab_col=fab_col,
        )

        raw_oc = ov.get("override_cols")
        if isinstance(raw_oc, str):
            raw_oc = [c.strip() for c in raw_oc.split(",") if c.strip()]
        if not raw_oc:
            raw_oc = list(_DEFAULT_OVERRIDE_COLS)
        if fab_col and fab_col not in raw_oc:
            raw_oc = list(raw_oc) + [fab_col]
        resolved_oc = []
        for c in raw_oc:
            actual = _resolve_source_col_name(c, fab_schema_names)
            resolved_oc.append(actual or c)
        override_cols = [c for c in dict.fromkeys(resolved_oc)
                         if c in fab_names and c not in join_keys]
        wanted = list(dict.fromkeys(join_keys + override_cols + ([ts_col] if ts_col else [])))
        wanted = [c for c in wanted if c in fab_names]
        if not override_cols:
            logger.warning(
                "_scan_product: override_cols 가 비어있음 — join 없이 raw lf 반환 "
                "(product=%s fab_source=%s raw_oc=%s fab_names=%s)",
                product, fab_source, raw_oc, fab_schema_names[:20],
            )
            return lf

        fab_proj = fab_lf.select(wanted)
        join_aliases = [(k, f"__join_key_{i}") for i, k in enumerate(join_keys)]
        fab_proj = fab_proj.with_columns([_join_key_expr(k).alias(tmp) for k, tmp in join_aliases])
        lf = lf.with_columns([_join_key_expr(k).alias(tmp) for k, tmp in join_aliases])
        join_tmp_keys = [tmp for _, tmp in join_aliases]
        fab_proj = fab_proj.select(list(dict.fromkeys(join_tmp_keys + override_cols + ([ts_col] if ts_col else []))))
        if ts_col and ts_col in fab_names:
            fab_proj = fab_proj.sort(ts_col, descending=True, nulls_last=True)
            fab_proj = fab_proj.unique(subset=join_tmp_keys, keep="first", maintain_order=True)
        else:
            fab_proj = fab_proj.unique(subset=join_tmp_keys, keep="last")
        return _join_fab_projection_into_main(
            lf, main_names, fab_proj, join_keys, override_cols,
            fab_has_join_tmp=True,
        )
    except Exception as e:
        # v8.8.26: blanket except 유지하되 반드시 로그를 남겨 진단 가능하게.
        logger.warning("_scan_product: 예상치 못한 예외 (product=%s) %s: %s",
                       product, type(e).__name__, e, exc_info=True)
        return _strip_non_authoritative_fab_fields(lf, product)

# ── 제품별 root_lot_id 풀 (드롭다운 즉시 표시) ────────────────────────────
# 프런트(My_SplitTable)는 제품을 고를 때 root_lot_id 전체 목록을 한 번 받아
# lotPoolRef 에 담고 이후 키 입력은 로컬 필터로 처리한다. 그 "한 번"이 느렸다:
# split_table 캐시 디렉터리 glob + FAB latest 캐시 unique 스캔을 매번 다시 했고,
# 그 위의 _LOT_LOOKUP_CACHE 는 TTL 60초 + 256개 FIFO 라 1분마다 콜드였다.
#
# 여기서는 소스 시그니처가 바뀔 때까지 유효한 풀을 만들어 core.lot_list_cache
# (RAM LRU + 디스크 JSON) 에 맡긴다. 목록 조립 자체는 아래 빌더가 그대로
# 기존 경로를 쓰므로 결과가 달라지지 않는다 — 다시 계산하는 횟수만 줄어든다.
_ROOT_LOT_POOL_MAX = 50000   # 프런트 ROOT_LOT_CACHE_LIMIT_MAX 와 동일
_FAB_LOT_POOL_MAX = 50000

def _root_lot_pool_sig(product: str) -> str:
    """Fingerprint only the authoritative ML_TABLE lookup candidate source.

    FAB latest/match/pivot caches do not change which roots the ML_TABLE can
    render.  Including their mtimes made the pool miss repeatedly while a cache
    job was steadily writing partitions — exactly when a newly added product
    needed fast candidates.  The ML_TABLE file and lookup meta already change
    atomically when the complete candidate index is rebuilt.
    """
    try:
        source_fp = _product_path(product)
        parts = (
            _path_cache_sig(source_fp),
            _path_cache_sig(_ml_table_lookup.meta_path_for(source_fp)),
            _path_cache_sig(_ml_table_lookup.candidate_index_path_for(source_fp)),
        )
    except Exception:
        parts = ((str(product or ""), 0.0, 0),)
    return hashlib.sha1(repr(parts).encode("utf-8", "ignore")).hexdigest()


def _fab_lot_pool_sig(product: str) -> str:
    """제품 전체 LOT_ID 후보에 실제로 영향을 주는 파생 캐시 지문."""
    try:
        source_fp = _product_path(product)
        parts = (
            _path_cache_sig(source_fp),
            _path_cache_sig(_ml_table_lookup.meta_path_for(source_fp)),
            _path_cache_sig(_ml_table_lookup.candidate_index_path_for(source_fp)),
            _path_cache_sig(_latest_lot_step_cache_path()),
            _path_cache_sig(_match_cache_path(product)),
        )
    except Exception:
        parts = ((str(product or ""), 0.0, 0),)
    return hashlib.sha1(repr(parts).encode("utf-8", "ignore")).hexdigest()


def _invalidate_root_lot_pool(product: str = "") -> int:
    """root_lot 풀 캐시를 버린다 (버린 항목 수 반환).

    소스가 바뀌면 시그니처가 달라져 어차피 miss 지만, 캐시 재빌드 직후처럼
    "지금 바로 비워졌음"을 보장해야 하는 지점에서 명시적으로 부른다.
    """
    key = _canonical_mltable_product_name(product, allow_bare=True) or str(product or "").strip()
    _root_lot_provisional_drop(key)
    return _lot_list_cache.invalidate(key)


def _build_root_lot_pool(product: str) -> tuple[list[str], dict, bool]:
    """완성된 lookup 후보 인덱스만 읽어 제품 root 목록을 게시한다.

    이 함수는 사용자 요청 경로에서도 호출된다. 따라서 lookup 미스 때 ML_TABLE
    원천이나 FAB history를 동기 스캔하지 않고 빌드만 큐에 넣은 뒤 즉시 준비 중
    응답을 돌려준다. lookup 빌드 완료 콜백/사전 예열기가 같은 함수를 다시 불러
    완전한 목록을 RAM+디스크 캐시에 게시한다.
    """
    lookup = _root_lot_lookup_cache_candidates(
        product, prefix="", limit=_ROOT_LOT_POOL_MAX)
    if lookup is None:
        return [], {
            "match_mode": "lookup_cache_unavailable",
            "source": "mltable_lookup",
            "lookup_cache": {},
        }, False

    candidates = _merge_candidate_values(
        lookup.get("candidates") or [], limit=_ROOT_LOT_POOL_MAX)
    fresh = bool(lookup.get("has_cache") and not lookup.get("source_stale"))
    index_ready = bool(fresh and lookup.get("candidate_index"))
    total_count = int(lookup.get("root_lot_id_count") or len(candidates))
    truncated = bool(total_count > len(candidates))
    # ``complete``는 이 API가 약속한 최대 50,000개 풀이 준비됐다는 뜻이다.
    # 종전에는 len == 50,000이면 인덱스가 완성돼도 영구 미완성으로 판정해 같은
    # lookup 빌드를 계속 큐에 넣었다. 원천 전체 포함 여부는 truncated로 분리한다.
    complete = index_ready
    queued = {}
    if not complete:
        source_fp = lookup.get("source_fp")
        if source_fp:
            queued = _ml_table_lookup.enqueue_build(source_fp)
    lookup_meta = _lookup_cache_public_meta(lookup, queued)
    # candidate index 가 아직 없어도 lookup 파티션 디렉터리(root_lot_id=…)는 이미
    # 그 시점의 root 를 전부 들고 있다. 종전에는 그걸 읽어 놓고 `complete` 가
    # 아니라는 이유로 버려서, 인덱스가 나올 때까지 드롭다운이 비어 있었다 —
    # 목록이 늦게 뜬다는 체감의 직접 원인. 잠정 목록으로 바로 내려주고
    # (디스크/RAM 에 굳히지는 않는다) 완성본이 나오면 프런트가 교체한다.
    provisional = bool(not complete and candidates)
    if complete:
        match_mode = "lookup_cache_roots"
    elif provisional:
        match_mode = "lookup_cache_partial"
    else:
        match_mode = "lookup_cache_preparing"
    meta = {
        "match_mode": match_mode,
        "source": "mltable_lookup",
        "fab_source": "",
        "provisional": provisional,
        "truncated": truncated,
        "total_count": total_count,
        "lookup_cache": lookup_meta,
    }
    return candidates, meta, complete


# 잠정(빌드 중) root 목록 — TTL 짧게, 제품 수만큼만. 완성본은 `_lot_list_cache`
# 로 가고 여기는 절대 디스크에 안 남는다.
_ROOT_LOT_PROVISIONAL_TTL_SEC = 10.0
_ROOT_LOT_PROVISIONAL_MAX = 16
_ROOT_LOT_PROVISIONAL_LOCK = threading.Lock()
_ROOT_LOT_PROVISIONAL: OrderedDict[str, tuple[float, dict]] = OrderedDict()


def _root_lot_provisional_get(product: str) -> dict | None:
    now = time.monotonic()
    with _ROOT_LOT_PROVISIONAL_LOCK:
        hit = _ROOT_LOT_PROVISIONAL.get(product)
        if hit is None:
            return None
        if now - hit[0] > _ROOT_LOT_PROVISIONAL_TTL_SEC:
            _ROOT_LOT_PROVISIONAL.pop(product, None)
            return None
        _ROOT_LOT_PROVISIONAL.move_to_end(product)
        return dict(hit[1])


def _root_lot_provisional_put(product: str, payload: dict) -> None:
    with _ROOT_LOT_PROVISIONAL_LOCK:
        _ROOT_LOT_PROVISIONAL[product] = (time.monotonic(), dict(payload))
        _ROOT_LOT_PROVISIONAL.move_to_end(product)
        while len(_ROOT_LOT_PROVISIONAL) > _ROOT_LOT_PROVISIONAL_MAX:
            _ROOT_LOT_PROVISIONAL.popitem(last=False)


def _root_lot_provisional_drop(product: str = "") -> None:
    with _ROOT_LOT_PROVISIONAL_LOCK:
        if product:
            _ROOT_LOT_PROVISIONAL.pop(product, None)
        else:
            _ROOT_LOT_PROVISIONAL.clear()


def _root_lot_pool(product: str) -> dict:
    """캐시된 root_lot_id 풀. 미스면 빌드해서 채운다.

    반환: `{"values", "meta", "complete", "cached"}`. `complete` 가 False 면
    (lookup 캐시 빌드 중이거나 raw 미리보기 폴백) **캐시하지 않는다** — 부분
    목록을 굳혀두면 빌드가 끝나도 잘린 드롭다운이 남고, 프런트의 재폴링
    프로토콜(`lookup_cache.queued`)도 깨진다. 호출자는 기존 경로로 넘어간다.

    시그니처를 **빌드보다 먼저** 읽는 순서가 중요하다. split_table 캐시
    디렉터리는 백그라운드로 root 파일이 하나씩 늘어나므로, 목록을 먼저 읽고
    나중에 stat 하면 "더 많이 본 목록"을 "더 새로운 지문"으로 굳혀 다음
    조회가 계속 hit 한다. 지금 순서라면 빌드 중 스냅샷을 담더라도 그 뒤에
    추가된 파일이 디렉터리 mtime 을 밀어 다음 조회에서 miss → 재빌드다.
    """
    canonical = _canonical_mltable_product_name(product, allow_bare=True) or str(product or "").strip().upper()
    sig = _root_lot_pool_sig(canonical)
    cached = _lot_list_cache.get(canonical, sig)
    if cached is not None and cached.get("values") and cached.get("complete"):
        return cached
    provisional = _root_lot_provisional_get(canonical)
    if provisional is not None:
        return provisional
    values, meta, complete = _build_root_lot_pool(canonical)
    if not values or not complete:
        out = {"values": values, "meta": meta, "complete": False, "cached": ""}
        if values:
            # 빌드가 끝날 때까지 프런트가 2초마다 재확인한다. 그 사이 매번
            # 파티션 디렉터리를 다시 훑지 않도록 짧게만 들고 있는다.
            _root_lot_provisional_put(canonical, out)
        return out
    _root_lot_provisional_drop(canonical)
    return _lot_list_cache.put(canonical, sig, values, meta=meta, complete=True)


def _build_fab_lot_pool(product: str) -> tuple[list[str], dict, bool]:
    """ML_TABLE + canonical latest cache의 제품 전체 LOT_ID 목록을 사전 계산한다.

    원천 FAB tree는 여기서 절대 스캔하지 않는다. lookup 빌드가 수집한 ML_TABLE
    식별자와 narrow latest/match cache만 읽으므로 백그라운드 예열에 안전하고,
    둘 중 하나라도 준비되지 않았으면 부분 결과를 디스크에 굳히지 않는다.
    """
    main_values: list[str] = []
    main_ready = False
    main_complete = False
    main_source_col = ""
    main_stale = False
    needs_candidate_rebuild = False
    try:
        fp = _product_path(product)
        status = _ml_table_lookup.cache_status(fp)
        main_stale = bool(status.get("has_cache") and status.get("status") != "fresh")
        meta_schema = (status.get("meta") or {}).get("schema") or {}
        schema_names = list(meta_schema) if isinstance(meta_schema, dict) else []
        fab_col = next((name for name in schema_names if str(name).casefold() == "fab_lot_id"), "")
        lot_col = next((name for name in schema_names if str(name).casefold() == "lot_id"), "")
        target = fab_col or lot_col
        if status.get("status") == "fresh" and not target:
            # fresh schema에 두 컬럼이 없다는 것 자체가 완전한 빈 ML 후보다.
            main_ready = True
            main_complete = True
        elif target:
            indexed = _ml_table_lookup.candidate_values_from_lookup_cache(
                fp, target, limit=_FAB_LOT_POOL_MAX + 1, allow_stale=True)
            main_values = list(indexed.get("values") or [])
            main_ready = bool(indexed.get("available"))
            main_complete = bool(indexed.get("available") and indexed.get("complete"))
            main_source_col = str(indexed.get("source_column") or target)
            needs_candidate_rebuild = not main_ready
        if main_stale or needs_candidate_rebuild:
            _ml_table_lookup.enqueue_build(fp)
    except Exception as exc:
        logger.debug("fab lot ML candidate index read failed (%s): %s", product, exc)

    history_values: list[str] = []
    history_ready = False
    history_complete = False
    history_source = ""
    try:
        history_lf = _latest_lot_step_cache_lf(product)
        history_col = "lot_id"
        if history_lf is None:
            current = _match_cache_current(product)
            history_lf = current.get("lf") if current else None
            history_col = MATCH_CACHE_FAB_COL
            history_source = str((current or {}).get("fab_source") or "match_cache")
        else:
            history_source = _latest_lot_step_cache_source(product)
        if history_lf is not None:
            names = history_lf.collect_schema().names()
            if history_col in names:
                rows = (
                    history_lf.select(pl.col(history_col).cast(_STR, strict=False).alias("v"))
                    .filter(pl.col("v").is_not_null() & (pl.col("v") != ""))
                    .unique().sort("v").head(_FAB_LOT_POOL_MAX + 1).collect()
                )
                history_values = _candidate_values_from_frame(
                    rows, "v", _FAB_LOT_POOL_MAX + 1)
                history_complete = len(history_values) <= _FAB_LOT_POOL_MAX
                history_values = history_values[:_FAB_LOT_POOL_MAX]
                history_ready = True
            else:
                history_ready = True
                history_complete = True
    except Exception as exc:
        logger.debug("fab lot latest candidate cache read failed (%s): %s", product, exc)

    merged_probe = _merge_candidate_values(
        main_values, history_values, limit=_FAB_LOT_POOL_MAX + 1)
    merged_truncated = len(merged_probe) > _FAB_LOT_POOL_MAX
    merged = merged_probe[:_FAB_LOT_POOL_MAX]
    exhaustive = bool(main_complete and history_complete and not merged_truncated)
    # 두 사전계산 소스를 정상적으로 읽었다면 50,000개 bounded pool은 캐시할 수
    # 있다. 원천 후보가 상한을 넘었다는 이유로 매 요청마다 다시 조립하지 않는다.
    ready = bool(main_ready and history_ready)
    return merged, {
        "match_mode": "cached_fab_lot_pool",
        "source": "mltable+latest_cache",
        "source_col": main_source_col,
        "fab_source": history_source,
        "truncated": not exhaustive,
        "exhaustive": exhaustive,
        "source_stale": main_stale,
    }, ready


def _fab_lot_pool(product: str) -> dict:
    canonical = _canonical_mltable_product_name(product, allow_bare=True) or str(product or "").strip().upper()
    sig = _fab_lot_pool_sig(canonical)
    cached = _lot_list_cache.get(canonical, sig, kind="fab")
    if cached is not None and cached.get("values") and cached.get("complete"):
        return cached
    values, meta, complete = _build_fab_lot_pool(canonical)
    if not values or not complete:
        return {"values": values, "meta": meta, "complete": False, "cached": ""}
    return _lot_list_cache.put(
        canonical, sig, values, kind="fab", meta=meta, complete=True)


def _refresh_root_lot_pool_after_lookup_build(fp: Path) -> None:
    """Publish complete candidate pools before the first user opens them."""
    product = Path(fp).stem
    try:
        _invalidate_root_lot_pool(product)
        pool = _root_lot_pool(product)
        fab_pool = _fab_lot_pool(product)
        logger.info("lot candidate pools warmed product=%s roots=%s lots=%s",
                    product, len(pool.get("values") or []),
                    len(fab_pool.get("values") or []))
    except Exception as exc:
        # Lookup cache itself remains valid; a request can retry pool creation.
        logger.warning("root lot candidate pool warm failed product=%s %s: %s",
                       product, type(exc).__name__, exc)


# 제품 선택 전에 root/LOT 후보 디스크 사본을 만들어 두는 경량 스케줄러. lookup
# 자체의 무거운 계산은 ml_table_lookup worker가 담당하고, 이 루프는 완성된 인덱스와
# narrow latest cache를 합쳐 게시만 한다.
_CANDIDATE_PREWARM_STOP = threading.Event()
_CANDIDATE_PREWARM_THREAD: threading.Thread | None = None
_CANDIDATE_PREWARM_STARTED = False
_CANDIDATE_PREWARM_LAST: dict = {"at": "", "products": 0, "roots": 0, "lots": 0, "failed": 0}


def _candidate_list_prewarm_once() -> dict:
    products = []
    try:
        products = [
            row.get("name") for row in (list_products().get("products") or [])
            if isinstance(row, dict) and row.get("name")
        ]
    except Exception:
        products = []
    roots = lots = failed = 0
    for product in products:
        if _CANDIDATE_PREWARM_STOP.is_set():
            break
        try:
            root_pool = _root_lot_pool(product)
            fab_pool = _fab_lot_pool(product)
            roots += len(root_pool.get("values") or [])
            lots += len(fab_pool.get("values") or [])
        except Exception as exc:
            failed += 1
            logger.debug("candidate list prewarm failed (%s): %s", product, exc)
    result = {
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
        "products": len(products), "roots": roots, "lots": lots, "failed": failed,
    }
    _CANDIDATE_PREWARM_LAST.update(result)
    return result


def _candidate_list_prewarm_loop() -> None:
    start_delay = max(1.0, _env_float("FLOW_SPLITTABLE_LIST_PREWARM_START_DELAY_SEC", 30.0))
    interval = max(60.0, _env_float("FLOW_SPLITTABLE_LIST_PREWARM_INTERVAL_SEC", 600.0))
    if _CANDIDATE_PREWARM_STOP.wait(start_delay):
        return
    while not _CANDIDATE_PREWARM_STOP.is_set():
        try:
            _candidate_list_prewarm_once()
        except Exception as exc:
            logger.warning("candidate list prewarm loop 오류: %s", exc)
        if _CANDIDATE_PREWARM_STOP.wait(interval):
            return


def start_candidate_list_prewarmer() -> bool:
    global _CANDIDATE_PREWARM_THREAD, _CANDIDATE_PREWARM_STARTED
    if _CANDIDATE_PREWARM_STARTED:
        return False
    if str(os.environ.get("FLOW_SPLITTABLE_LIST_PREWARM", "1")).strip().lower() in {"0", "false", "no", "off"}:
        return False
    _CANDIDATE_PREWARM_STOP.clear()
    _CANDIDATE_PREWARM_THREAD = threading.Thread(
        target=_candidate_list_prewarm_loop,
        name="splittable-list-prewarm",
        daemon=True,
    )
    _CANDIDATE_PREWARM_THREAD.start()
    _CANDIDATE_PREWARM_STARTED = True
    logger.info("SplitTable candidate list prewarmer started")
    return True


@router.get("/lot-ids")
def get_lot_ids(product: str = Query(...), limit: int = Query(200)):
    # 여기는 의도적으로 root_lot 풀을 쓰지 않는다. /lot-ids 는 "렌더 가능한 root
    # 의 authoritative 폴백" 이고, 풀의 가장 빠른 소스인 split_table 캐시
    # 디렉터리는 **이미 pre-pivot 된 root 만** 담는 부분집합이라 여기에 쓰면
    # 드롭다운이 조용히 잘린다. 프런트도 /lot-candidates 가 빈손일 때만 이걸
    # 부르므로 — 즉 풀이 없을 때만 — 캐시로 얻을 게 없다.
    catalog_path = _split_table_cache_dir(product) / "_lot_catalog.json"
    if catalog_path.exists():
        try:
            import json
            with open(catalog_path, "r", encoding="utf-8") as f:
                catalog = json.load(f)
            target_col = next((k for k in catalog.keys() if k.casefold() == "root_lot_id"), None)
            if target_col and target_col in catalog:
                return {
                    "lot_col": "root_lot_id",
                    "lot_ids": catalog[target_col][:limit],
                    "fallback": "",
                    "fab_source": "pivot_catalog",
                    "lookup_cache": {},
                }
        except Exception as e:
            logger.warning(f"Failed to read _lot_catalog.json in /lot-ids for {product}: {e}")

    lookup = _root_lot_lookup_cache_candidates(product, prefix="", limit=limit)
    lookup_meta = _lookup_cache_public_meta(lookup) if lookup is not None else {}
    if lookup is not None and lookup.get("candidates"):
        lots_list = _merge_candidate_values(lookup.get("candidates") or [], limit=limit)
        fab_source = ""
        try:
            hist = _fab_history_root_candidates(product, limit=limit)
            fab_source = hist.get("source") or ""
            fab_roots = hist.get("candidates") or []
            if fab_roots:
                main_keys = {str(v).upper() for v in lots_list}
                lots_list = _merge_candidate_values(
                    lots_list,
                    [v for v in fab_roots if str(v).upper() in main_keys],
                    limit=limit,
                )
        except Exception as e:
            logger.warning("/lot-ids: FAB root 후보 조회 실패 (product=%s) %s: %s",
                           product, type(e).__name__, e)
        return {
            "lot_col": "root_lot_id",
            "lot_ids": lots_list,
            "fallback": "",
            "fab_source": fab_source,
            "lookup_cache": lookup_meta,
        }
    if lookup is not None:
        source_fp = lookup.get("source_fp")
        if source_fp and _split_view_should_defer_raw_fallback(source_fp) and (
            not lookup.get("has_cache") or lookup.get("source_stale")
        ):
            queued = _ml_table_lookup.enqueue_build(source_fp)
            lookup_meta = _lookup_cache_public_meta(lookup, queued)
    lf = _scan_product(product)
    lot_col, _ = _detect_lot_wafer(lf)
    lots_list: list = []
    fallback_used = False
    try:
        # /lot-ids 는 렌더 가능한 root 의 authoritative 폴백 목록이라 완전성이 계약이다
        # (검색은 /lot-candidates?prefix= 가 담당). 초기 목록 즉시성은 주 경로인
        # _main_table_candidates(Tier A split_table 캐시)와 FE 재폴링이 담당하므로
        # 여기서는 전체 스캔을 유지한다. 이 경로는 lookup 캐시 미스에서만 도달한다.
        lots_list = _limited_unique_values(lf, lot_col, limit=limit, preview_only=False)
    except Exception as e:
        logger.warning("/lot-ids: main lf 조회 실패 (product=%s) %s: %s",
                       product, type(e).__name__, e)
        lots_list = []
    fab_roots: list[str] = []
    fab_source = ""
    try:
        hist = _fab_history_root_candidates(product, limit=limit)
        fab_roots = hist.get("candidates") or []
        fab_source = hist.get("source") or ""
    except Exception as e:
        logger.warning("/lot-ids: FAB root 후보 조회 실패 (product=%s) %s: %s",
                       product, type(e).__name__, e)
    if fab_roots:
        # Keep the dropdown aligned with what /view can render.  If ML_TABLE has
        # roots, only append FAB roots that are also present there; otherwise a
        # user can pick a valid FAB history root and still get an empty table.
        if lots_list:
            main_keys = {str(v).upper() for v in lots_list}
            fab_roots = [v for v in fab_roots if str(v).upper() in main_keys]
            lots_list = _merge_candidate_values(lots_list, fab_roots, limit=limit)
        else:
            lots_list = _merge_candidate_values(fab_roots, limit=limit)
            fallback_used = True
    # v8.8.26: main 이 all-null 이거나 비어있으면 override fab_source 로 폴백.
    if not lots_list:
        try:
            meta = _resolve_override_meta(product, include_diagnostics=False)
            fab_source = (meta.get("fab_source") or "").strip()
            if fab_source and not meta.get("error"):
                fab_lf = _scan_fab_source(fab_source)
                if fab_lf is not None:
                    fab_names = fab_lf.collect_schema().names()
                    # CI 매칭으로 root_lot_id 를 찾는다.
                    target = next((n for n in fab_names
                                   if n.casefold() == "root_lot_id"), None)
                    if target:
                        lots_list = _limited_unique_values(fab_lf, target, limit=limit)
                        if lots_list:
                            fallback_used = True
                            lot_col = target
        except Exception as e:
            logger.warning("/lot-ids: override 폴백 실패 (product=%s) %s: %s",
                           product, type(e).__name__, e)
    return {"lot_col": lot_col, "lot_ids": lots_list,
            "fallback": "fab_source" if fallback_used else "",
            "fab_source": fab_source,
            "lookup_cache": lookup_meta}


@router.get("/lot-candidates")
def get_lot_candidates(
    product: str = Query(...),
    col: str = Query("root_lot_id"),
    prefix: str = Query(""),
    limit: int = Query(30),
    source: str = Query("auto"),   # v8.8.19: auto|override|mltable
    root_lot_id: str = Query(""),  # v9.0.0 (Q1): fab_lot_id 드롭다운을 특정 root 로 제한
):
    """Autocomplete 후보 반환. col 은 'root_lot_id' 또는 'fab_lot_id'. prefix 가
    비어있으면 최신/정렬 상위 N개, 아니면 prefix 포함 매칭을 정렬 순 top N.

    v8.8.19: `source` 인자 추가.
    v9.0.0: `root_lot_id` 파라미터 추가 — fab_lot_id 후보를 해당 root (앞 5자) 로 제한.
      (예: root_lot_id=A0001 → A0001 로 시작하는 fab_lot_id 만 반환)
    """
    # v9.0.5: fab_lot_id 후보는 DB FAB 원천 이력의 정확한 root/fab 매칭만 허용.
    #   DB FAB 에 없으면 ML_TABLE LOT_ID, starts_with, 전체 후보 fallback 으로 회피하지 않는다.
    root_scope = (root_lot_id or "").strip()
    if col.casefold() == "root_lot_id":
        # root 후보는 lookup candidate index → 제품별 RAM/디스크 풀만 사용한다.
        # 캐시 미스에서 원천/FAB/lot-ids를 동기 스캔하면 드롭다운 하나가 수 초~수
        # 분을 점유하므로, 빌드만 큐에 넣고 즉시 준비 중 응답을 반환한다.
        pool = _root_lot_pool(product)
        # 잠정 목록(빌드 중 파티션 스냅샷)도 그대로 내려준다. 종전에는 완성본만
        # 내보내 인덱스가 나올 때까지 드롭다운이 비어 있었다. `complete=False`
        # 를 함께 알려 프런트가 완성본으로 교체할 때까지 재확인을 이어간다.
        if pool.get("values"):
            values = pool["values"]
            needle = str(prefix or "").strip().upper()
            if needle:
                values = [v for v in values if needle in str(v).upper()]
            return {
                "col": "root_lot_id",
                "candidates": values[:max(1, int(limit or 30))],
                "prefix": prefix,
                "root_scope": root_scope,
                "strict": False,
                "complete": bool(pool.get("complete")),
                "pool_cache": pool.get("cached") or "",
                **(pool.get("meta") or {}),
            }
        meta = pool.get("meta") or {}
        return {
            "col": "root_lot_id",
            "candidates": [],
            "prefix": prefix,
            "root_scope": root_scope,
            "complete": False,
            "provisional": False,
            "match_mode": meta.get("match_mode") or "lookup_cache_preparing",
            "source": meta.get("source") or "mltable_lookup",
            "fab_source": "",
            "lookup_cache": meta.get("lookup_cache") or {},
            "strict": False,
        }
    if col.casefold() in {c.casefold() for c in _FAB_COL_CANDIDATES}:
        # 신규 Inform과 root 미선택 SplitTable은 제품 전체 LOT_ID 풀을 쓴다.
        # RAM/디스크에 이미 게시돼 있으면 prefix 입력도 서버 스캔 없이 필터한다.
        if not root_scope:
            pool = _fab_lot_pool(product)
            if pool.get("complete") and pool.get("values"):
                values = pool["values"]
                needle = str(prefix or "").strip().upper()
                if needle:
                    values = [value for value in values if needle in str(value).upper()]
                return {
                    "col": col,
                    "candidates": values[:max(1, int(limit or 30))],
                    "prefix": prefix,
                    "root_scope": "",
                    "strict": False,
                    "pool_complete": True,
                    "pool_cache": pool.get("cached") or "",
                    **(pool.get("meta") or {}),
                }
        main = _main_table_candidates(product, col, prefix=prefix, limit=limit, root_lot_id=root_scope)
        hist = _fab_history_scope(
            product,
            root_lot_id=root_scope,
            prefix=prefix,
            limit=limit,
            prefer_raw_latest=bool(root_scope or str(prefix or "").strip()),
        )
        main_candidates = main.get("candidates") or []
        hist_candidates = hist.get("candidates") or []
        if root_scope and hist_candidates:
            # A root can legitimately span multiple operational fab_lot_id values.
            # Keep the FAB history set authoritative for scoped lookups; intersecting
            # with ML_TABLE lot_id/fab_lot_id collapses cases like A1003A.2/A1003A.3.
            merged = _merge_candidate_values(hist_candidates, limit=limit)
        else:
            # Unscoped Inform LOT_ID search must also surface operational FAB
            # history lots that are not present in the current ML_TABLE render.
            # Intersecting here made searches such as "A1003" show only
            # A1003A.1 while hiding related A1003A.2/A1003A.3 entries.
            merged = _merge_candidate_values(main_candidates, hist_candidates, limit=limit)
        if merged:
            return {
                "col": col,
                "candidates": merged,
                "prefix": prefix,
                "root_scope": root_scope,
                "match_mode": "splittable_fab_lots" if root_scope else "splittable_fab_lots_all",
                "source": "mltable",
                "fab_source": hist.get("source", ""),
                "strict": False,
            }
        return {
            "col": col,
            "candidates": hist.get("candidates") or [],
            "prefix": prefix,
            "root_scope": root_scope,
            "match_mode": "fab_history_root" if root_scope else "fab_history",
            "source": "fab_source_history",
            "fab_source": hist.get("source", ""),
            "strict": True,
        }
    use_override = False
    lf = None
    if source == "override" and product.casefold().startswith("ml_table_"):
        try:
            meta = _resolve_override_meta(product, include_diagnostics=False)
            fab_source = (meta.get("fab_source") or "").strip()
            if fab_source and not meta.get("error"):
                fab_lf = _scan_fab_source(fab_source)
                if fab_lf is not None:
                    lf = fab_lf
                    use_override = True
        except Exception:
            lf = None
        if lf is None:
            return {"col": col, "candidates": [], "source": "override",
                    "note": "override 비활성 또는 fab_source 없음"}
    if lf is None:
        lf = _scan_product(
            product,
            root_lot_id=root_scope if col.casefold() != "root_lot_id" else "",
        )

    schema_names = lf.collect_schema().names()
    # v8.8.26: CI 매칭 — FE 가 "ROOT_LOT_ID"(ML_TABLE casing) 로 요청해도 raw 소스의
    # "root_lot_id" 로 정확히 매핑 (이전에는 exact match 만 되어 override 경로에서 누락).
    if col not in schema_names:
        col_ci = next((n for n in schema_names if n.casefold() == col.casefold()), None)
        if col_ci:
            col = col_ci
        else:
            # fallback — root 이면 auto-detect lot col, fab 는 그대로
            if col.casefold() == "root_lot_id":
                lot_col, _ = _detect_lot_wafer(lf)
                col = lot_col or col
            if col not in schema_names:
                return {"col": col, "candidates": [], "available_cols": schema_names[:20],
                        "source": "override" if use_override else "mltable"}

    match_mode = "all"
    fallback_used = False

    # v9.0.1: root_scope + fab_lot_id 조회 시 데이터-중심 매칭.
    #   데이터에서 root_lot_id 와 fab_lot_id 의 앞 5자가 자연 일치하지 않는 케이스 (예:
    #   ML_TABLE root=A0015 → fab_lot=A0005B.1) 에서 단순 starts_with 가 0건을 반환하던 문제.
    #   1) main lf 에서 root_lot_id 컬럼을 CI 매칭으로 찾고, 같은 row 의 fab_lot_id 를 unique 추출.
    #   2) (1) 결과가 비면 → 기존 starts_with 폴백.
    #   3) (2) 도 비면 → root_scope 무시하고 전체 후보 반환 (sentinel: fallback_used=True).
    if root_scope and col.casefold() != "root_lot_id":
        root_col = next((n for n in schema_names if n.casefold() == "root_lot_id"), None)
        if root_col:
            try:
                q_join = (lf.filter(_join_key_expr(root_col) == root_scope.strip().upper())
                            .select(pl.col(col).cast(_STR, strict=False).alias("v"))
                            .drop_nulls().unique())
                if prefix.strip():
                    q_join = q_join.filter(_contains_literal_ci_expr("v", prefix))
                rows_join = q_join.sort("v").head(limit).collect()
                cand_join = [v for v in rows_join["v"].to_list()
                             if v and str(v).strip() not in ("", "None", "null")]
                if cand_join:
                    return {"col": col, "candidates": cand_join, "prefix": prefix,
                            "root_scope": root_scope, "match_mode": "root_join",
                            "source": "override" if use_override else "mltable"}
            except Exception as e:
                logger.warning("/lot-candidates: root_join 실패 (product=%s) %s: %s",
                               product, type(e).__name__, e)

    q = lf.select(pl.col(col).cast(_STR, strict=False).alias("v")).drop_nulls().unique()
    if prefix.strip():
        q = q.filter(_contains_literal_ci_expr("v", prefix))
    if root_scope and col.casefold() != "root_lot_id":
        # 폴백 1: starts_with 5자 prefix
        try:
            q_sw = q.filter(pl.col("v").str.starts_with(root_scope[:5]))
            rows_sw = q_sw.sort("v").head(limit).collect()
            if rows_sw.height > 0:
                match_mode = "starts_with"
                return {"col": col, "candidates": rows_sw["v"].to_list(), "prefix": prefix,
                        "root_scope": root_scope, "match_mode": match_mode,
                        "source": "override" if use_override else "mltable"}
        except Exception:
            pass
        fallback_used = True
        match_mode = "all_fallback"
    rows = q.sort("v").head(limit).collect()
    return {"col": col, "candidates": rows["v"].to_list(), "prefix": prefix,
            "root_scope": root_scope, "match_mode": match_mode,
            "root_scope_fallback": fallback_used,
            "source": "override" if use_override else "mltable"}


@router.get("/column-values")
def get_column_values(product: str = Query(...), col: str = Query(...), limit: int = Query(200)):
    """빈셀 dbl-click edit suggestion — col 값의 unique 리스트 (전체 데이터셋 범위) +
    해당 product 의 plan 에 등록된 값 union. null/빈값 제외.
    """
    out: list[str] = []
    seen: set[str] = set()
    indexed = None
    try:
        fp = _product_path(product)
        indexed = _ml_table_lookup.candidate_values_from_lookup_cache(
            fp, col, limit=limit)
        if indexed.get("available"):
            values = indexed.get("values") or []
        else:
            lf = _scan_product(product)
            schema_names = lf.collect_schema().names()
            values = []
            if col in schema_names:
                rows = (lf.select(pl.col(col).cast(_STR, strict=False).alias("v"))
                        .drop_nulls().unique().sort("v").head(limit).collect())
                values = rows["v"].to_list()
        for v in values:
            if v is None: continue
            s = str(v).strip()
            if not s or s in ("None", "null"): continue
            if s in seen: continue
            seen.add(s); out.append(s)
    except Exception:
        pass
    try:
        for v in _custom_tag_column_values(product, col, limit=limit):
            if v in seen:
                continue
            seen.add(v)
            out.append(v)
            if len(out) >= limit:
                break
    except Exception:
        pass
    try:
        for v in _management_row_column_values(product, col, limit=limit):
            if v in seen:
                continue
            seen.add(v)
            out.append(v)
            if len(out) >= limit:
                break
    except Exception:
        pass
    # Union with plan values stored under this column
    try:
        plans = _load_plan_data(product).get("plans", {})
        for ck, pv in plans.items():
            # ck format: root_lot_id|wafer_id|col_name
            parts = str(ck).split("|")
            if len(parts) >= 3 and parts[2] == col:
                v = pv.get("value") if isinstance(pv, dict) else pv
                if v is None: continue
                s = str(v).strip()
                if not s or s in ("None", "null"): continue
                if s in seen: continue
                seen.add(s); out.append(s)
    except Exception:
        pass
    return {
        "col": col, "values": out, "count": len(out),
        "candidate_cache": "lookup_index" if indexed and indexed.get("available") else "",
        "candidate_cache_complete": bool(indexed and indexed.get("complete")),
    }


def _filter_lot_wafer(lf, lot_col, wf_col, root_lot_id: str, wafer_ids: str,
                      fab_lot_id: str = "", fab_lot_col: str = "fab_lot_id"):
    """Apply lot + (optional) wafer filter to LazyFrame. v8.4.3 — fab_lot_id
    경로 추가. root_lot_id / fab_lot_id 중 하나로 조회 가능.
    """
    root_scope = root_lot_id.strip()
    fab_scope = fab_lot_id.strip()
    schema_names = lf.collect_schema().names()
    if root_scope and lot_col and lot_col in schema_names:
        lf = lf.filter(_join_key_expr(lot_col) == root_scope.upper())
    if fab_scope and fab_lot_col in schema_names:
        lf = lf.filter(_join_key_expr(fab_lot_col) == fab_lot_id.strip().upper())
    if wafer_ids.strip() and wf_col:
        wf_list = [w.strip() for w in wafer_ids.split(",") if w.strip()]
        try:
            wf_ints = [int(w) for w in wf_list]
            # Build all possible formats: 1 → ["1", "01", "W01", "W1"]
            wf_strs = set()
            for n in wf_ints:
                wf_strs.update([str(n), f"{n:02d}", f"W{n}", f"W{n:02d}"])
            lf = lf.filter(
                pl.col(wf_col).cast(_STR, strict=False).is_in(list(wf_strs))
                | pl.col(wf_col).cast(pl.Int64, strict=False).is_in(wf_ints)
            )
        except ValueError:
            lf = lf.filter(pl.col(wf_col).cast(_STR, strict=False).is_in(wf_list))
    return lf


def _ml_product_name(product: str) -> str:
    p = str(product or "").strip()
    if not p:
        return ""
    return _canonical_mltable_product_name(p, allow_bare=True)


def resolve_fab_lot_snapshot(product: str, root_lot_id: str, wafer_id: str = "") -> str:
    """Return the fab_lot_id from the same coalesced SplitTable data users see."""
    ml_product = _ml_product_name(product)
    root = str(root_lot_id or "").strip()
    if not ml_product or not root:
        return ""
    try:
        cached = _fab_lot_snapshot_from_cache(ml_product, root, wafer_id)
        if cached:
            return cached
        lf = _scan_product(ml_product, root_lot_id=root, wafer_ids=str(wafer_id or ""))
        lot_col, wf_col = _detect_lot_wafer(lf, ml_product)
        if not lot_col:
            return ""
        names = lf.collect_schema().names()
        fab_col = "fab_lot_id" if "fab_lot_id" in names else ""
        if not fab_col:
            fab_col = _pick_first_present_ci(_FAB_COL_CANDIDATES, names) or ""
        if not fab_col:
            return ""
        lf = _filter_lot_wafer(lf, lot_col, wf_col, root, str(wafer_id or ""),
                               fab_lot_col=fab_col)
        df = (
            lf.select(pl.col(fab_col).cast(_STR, strict=False).alias("fab_lot_id"))
            .drop_nulls()
            .unique()
            .sort("fab_lot_id")
            .head(1)
            .collect()
        )
        if df.height == 0:
            return ""
        return str(df.item(0, 0) or "").strip()
    except Exception as e:
        logger.warning("resolve_fab_lot_snapshot 실패 (product=%s root=%s wafer=%s) %s: %s",
                       product, root_lot_id, wafer_id, type(e).__name__, e)
        return ""


def _resolve_fab_lot_for_cell(product: str, cell_key: str, root_lot_id: str = "") -> str:
    parts = str(cell_key or "").split("|")
    root = str(root_lot_id or (parts[0] if len(parts) >= 1 else "") or "").strip()
    wafer = str(parts[1] if len(parts) >= 2 else "").strip()
    return resolve_fab_lot_snapshot(product, root, wafer)


def _split_view_cache_key(product: str, root_lot_id: str, wafer_ids: str, prefix: str,
                          custom_name: str, view_mode: str, history_mode: str,
                          fab_lot_id: str, custom_cols: str) -> tuple:
    canonical_product = _canonical_mltable_product_name(product, allow_bare=True) or str(product or "").strip()
    cleaned_custom_cols = ",".join(_clean_custom_columns(str(custom_cols or "").split(","))) if custom_cols else ""
    return (
        canonical_product,
        str(root_lot_id or "").strip(),
        str(wafer_ids or "").strip(),
        str(prefix or "").strip().upper(),
        str(custom_name or "").strip(),
        str(view_mode or "all").strip().lower() or "all",
        str(history_mode or "all").strip().lower() or "all",
        str(fab_lot_id or "").strip(),
        cleaned_custom_cols,
    )


def _split_view_cache_stats(hit: bool, key: tuple | None = None, *, stale: bool = False) -> dict:
    key_hash = ""
    if key is not None:
        try:
            raw = json.dumps(key, sort_keys=True, ensure_ascii=False, default=str)
            key_hash = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
        except Exception:
            key_hash = ""
    with _VIEW_CACHE_LOCK:
        size = len(_VIEW_CACHE)
    return {
        "hit": bool(hit),
        "payload_cache_hit": bool(hit),
        # stale=True → 캐시로 즉시 응답했고 백그라운드 재검증이 예약됨(SWR).
        "stale": bool(stale),
        "entries": size,
        "max_entries": _view_cache_max_entries(),
        "key": key_hash,
    }


def _split_view_data_source_label(src: dict, *, payload_cache_hit: bool) -> str:
    """검색이 실제로 어느 계층에서 데이터를 얻었는지 한 단어로 분류.

    payload_cache: 응답 전체 캐시 히트(가장 빠름) · pivot_cache: per-root pivot 캐시 ·
    product_ram/ram: 메모리 캐시 히트 · ram_load: 첫 검색으로 파티션을 메모리 적재 ·
    disk: 파티션 parquet 디스크 스캔(첫 검색) · raw/fallback: 캐시 없이 원본 경로.
    """
    if payload_cache_hit:
        return "payload_cache"
    ds = str(src.get("root_data_source") or "").strip()
    if ds:
        return ds
    if src.get("product_cache_hit"):
        return "product_ram"
    if src.get("root_cache_hit"):
        return "root_cache"
    return "raw"


def _request_lane_wait_ms(request: Request | None) -> float:
    """ResourceGuardMiddleware 가 남긴 레인 대기 시간(ms). 없으면 0."""
    try:
        return max(0.0, float(getattr(request.state, "lane_wait_ms", 0.0) or 0.0))
    except Exception:
        return 0.0


# ── 단계별 계측 ────────────────────────────────────────────────────────────────
# scan/collect/matrix/overlay 4개만 재던 시절엔 느린 검색의 절반 이상이 어느
# 단계인지 알 수 없었다(실측 17,862건에서 미계측 57%). 아래 _PHASE_KEYS 는 그
# 빈칸을 메우는 구간들이고, unaccounted_ms 가 남은 오차를 그대로 드러낸다 —
# 이 값이 크면 "아직 못 재고 있는 구간이 있다"는 뜻이지 최적화 대상이 없다는
# 뜻이 아니다.
#
# 주의: collect_ms 의 의미가 좁아졌다. 예전에는 컬럼 선택(파이썬)+polars collect
# 를 합친 값이었고 지금은 collect 만이다. 그 앞 파이썬 구간은 select_ms 다.
_PHASE_KEYS: tuple[str, ...] = (
    "prelude_ms",      # 진입~payload 캐시 판정 (인증/감사/캐시키/의존 시그니처)
    "fastpath_ms",     # pivot 캐시 탐색 + 스키마 확인 + KNOB 사이드카 판정
    "scan_ms",         # 파티션/RAM 소스 결정 + lazy join 구성
    "root_scan_ms",    #   그중 root RAM/디스크 파티션 확보
    "schema_ms",       # lot/wafer/fab 컬럼 해석 (스키마 + 오버라이드 설정)
    "fabscope_ms",     # fab_lot_id 히스토리 스코프 조회
    "select_ms",       # 컬럼 선택·rename·공정순 정렬 (순수 파이썬)
    "collect_ms",      # polars collect
    "emptyfallback_ms",  # 빈 결과일 때의 재해석(root-only / 붙여넣은 fab_lot) 시도
    "header_ms",       # 웨이퍼 헤더/그룹 조립 (df→파이썬 리스트)
    "overlay_ms",      # plan/tag/management 로드 + override/fab 후보
    "matrix_ms",       # 셀 매트릭스 조립
    "mismatch_ms",     # plan↔actual 불일치 알림 적재
    "progress_ms",     # step_progress (미진행 행 회색 처리)
    "payload_ms",      # 응답 dict 조립 (prefixes/precision/all_columns/meta)
    "cacheput_ms",     # payload 캐시 저장
    "finish_ms",       # 마무리 메타 (lookup_cache/related_issues/product_cache)
    "serialize_ms",    # orjson 직렬화 (HTTP 경로 전용)
)


_LAP_MARK = "_lap_mark"


def _lap(profile: dict | None, key: str | None) -> None:
    """구간 계측 — 직전 lap 이후 흐른 시간을 key 에 누적하고 마커를 옮긴다.

    구간마다 따로 t0 를 잡지 않고 마커 하나를 이어 달리는 이유는 **빈틈이 생기지
    않게** 하기 위해서다. 개별 타이머 방식은 타이머와 타이머 사이의 코드가 아무
    키에도 안 잡혀서 조용히 사라진다 — 기존 계측이 절반 이상을 놓친 원인이 정확히
    이거였다. 이 방식에서는 lap 을 부르지 않은 시간만 unaccounted_ms 로 남는다.

    key=None 은 "이 구간은 다른 데서 이미 셌으니 버린다"(레인/단일비행 대기).
    같은 key 를 여러 번 lap 해도 합산된다(폴백 경로 재실행).

    마커를 profile 안에 두므로 핸들러 밖의 헬퍼(_attach_split_view_runtime_fields)
    도 같은 흐름을 이어서 잴 수 있다.
    """
    if profile is None:
        return
    now = time.perf_counter()
    mark = profile.get(_LAP_MARK)
    if mark is not None and key is not None:
        profile[key] = float(profile.get(key) or 0.0) + (now - float(mark)) * 1000.0
    profile[_LAP_MARK] = now


def _split_view_runtime_profile(started: float, runtime_profile: dict | None, *, payload_cache_hit: bool) -> dict:
    src = runtime_profile or {}
    data_source = _split_view_data_source_label(src, payload_cache_hit=payload_cache_hit)
    total_ms = (time.perf_counter() - started) * 1000.0
    lane_wait_ms = float(src.get("lane_wait_ms") or 0.0)
    cold_lane_wait_ms = float(src.get("cold_lane_wait_ms") or 0.0)
    singleflight_wait_ms = round(float(src.get("singleflight_wait_ms") or 0.0), 3)
    phases = {key: round(float(src.get(key) or 0.0), 3) for key in _PHASE_KEYS}
    # root_scan_ms 는 scan_ms 안쪽 구간이라 합계에서 뺀다(이중 계산 방지).
    # serialize_ms 는 total_ms 밖에서 일어나므로 역시 제외한다.
    accounted = sum(
        v for k, v in phases.items() if k not in ("root_scan_ms", "serialize_ms")
    ) + cold_lane_wait_ms + singleflight_wait_ms
    unaccounted = max(0.0, total_ms - accounted)
    return {
        # 검색 상세 이력에서 실제 요청자를 보여 주기 위한 세션 정보. 응답에도 포함되지만
        # 비밀번호/토큰 같은 인증 정보는 넣지 않고 식별자와 역할만 보관한다.
        "username": str(src.get("username") or ""),
        "user_role": str(src.get("user_role") or ""),
        "is_user_search": bool(src.get("is_user_search")),
        # total_ms 는 핸들러 전체 = cold 레인 대기(핸들러 '안'에서 줄 선 시간)를
        # 포함한다. 순수 계산만 보려면 compute_ms 를 쓸 것 — total 만 보면 줄서기가
        # 계산 시간으로 오인된다.
        "total_ms": round(total_ms, 3),
        "compute_ms": round(max(0.0, total_ms - cold_lane_wait_ms), 3),
        # 핸들러 밖에서 줄 선 시간까지 포함한 체감 시간. total_ms 만 보면 "서버는
        # 빠른데 사용자는 느린" 상태의 원인이 안 보인다.
        "wall_ms": round(total_ms + lane_wait_ms, 3),
        # 줄 선 시간 합계 = 미들웨어 레인 + cold 계산 레인.
        "wait_ms": round(lane_wait_ms + cold_lane_wait_ms, 3),
        "lane_wait_ms": round(lane_wait_ms, 3),
        "cold_lane_wait_ms": round(cold_lane_wait_ms, 3),
        "root_cache_hit": bool(src.get("root_cache_hit")),
        "product_cache_hit": bool(src.get("product_cache_hit")),
        "payload_cache_hit": bool(payload_cache_hit),
        "data_source": data_source,
        # KNOB 전용 사이드카(좁은 pivot 파일)로 읽었는지 — 운영에서 이 경로가
        # 실제로 도는지 확인용. False 면 전체 pivot 파일로 폴백한 것.
        "knob_sidecar": bool(src.get("root_data_source_detail") == "knob_sidecar"),
        "singleflight_wait_ms": singleflight_wait_ms,
        # 단계별 breakdown — 전 구간 계측. serialize_ms 만 total_ms 밖이다
        # (직렬화는 이 프로필이 만들어진 뒤 HTTP 계층에서 일어난다).
        **phases,
        # 아직 어느 단계로도 설명되지 않은 시간. 0 에 가까워야 정상이고, 크면
        # 계측이 빠진 구간이 남아 있다는 신호다.
        "unaccounted_ms": round(unaccounted, 3),
        "root_cache_status": str(src.get("root_cache_status") or ""),
        "root_prefetch_queued": bool(src.get("root_prefetch_queued")),
        "fab_index_queued": bool(src.get("fab_index_queued")),
        "cache_incomplete": bool(src.get("cache_incomplete")),
    }


def _split_view_finish_payload(
    payload: dict,
    *,
    started: float,
    runtime_profile: dict | None,
    payload_cache_hit: bool,
    view_cache_key: tuple | None,
    view_stale: bool = False,
) -> dict:
    out = dict(payload)
    rp = _split_view_runtime_profile(started, runtime_profile, payload_cache_hit=payload_cache_hit)
    out["runtime_profile"] = rp
    out["view_cache"] = _split_view_cache_stats(payload_cache_hit, view_cache_key, stale=view_stale)
    _record_search_timing(out, rp)
    _view_compute_finish(view_cache_key)
    return out


# ── SplitTable 검색 타이밍 로그 (관리자 breakdown 용) ──────────────────────────
# 단계별 소요시간을 기록해 관리자 화면에서 "캐시 히트일 때 속도 / 첫 검색(DB 조회)
# 단계별 breakdown / 동시 요청 때문에 줄 선 시간" 을 보여준다.
# 보관은 core/search_timing_log.py 가 담당한다 — 인메모리 링버퍼(최근 조회용) +
# 공유 JSONL(며칠 치 관찰용, 운영/개발 서버 통합).
def _record_search_timing(payload: dict, rp: dict) -> None:
    try:
        # request=None 인 캐시 재검증/캐시 빌드/Inform·Flow-i 내부 조회는 제외한다.
        # 실제 브라우저의 /view HTTP 요청만 runtime_profile 에 이 표식을 갖는다.
        if not bool(rp.get("is_user_search")):
            return
        root = str(payload.get("root_lot_id") or "").strip()
        if not root:
            return
        rows = payload.get("rows")
        if not isinstance(rows, list):
            rows = payload.get("rows_compact")
        row_count = len(rows) if isinstance(rows, list) else 0
        entry = {
            "actor_type": "user_search",
            "at": datetime.datetime.now().isoformat(timespec="seconds"),
            "product": str(payload.get("product") or ""),
            "root_lot_id": root,
            "data_source": rp.get("data_source") or "",
            "total_ms": rp.get("total_ms") or 0.0,
            "compute_ms": rp.get("compute_ms") or 0.0,
            "wall_ms": rp.get("wall_ms") or 0.0,
            "wait_ms": rp.get("wait_ms") or 0.0,
            "lane_wait_ms": rp.get("lane_wait_ms") or 0.0,
            "cold_lane_wait_ms": rp.get("cold_lane_wait_ms") or 0.0,
            "singleflight_wait_ms": rp.get("singleflight_wait_ms") or 0.0,
            # 단계별 breakdown 전량 + 미설명 잔여.
            **{key: rp.get(key) or 0.0 for key in _PHASE_KEYS},
            "unaccounted_ms": rp.get("unaccounted_ms") or 0.0,
            "root_cache_hit": bool(rp.get("root_cache_hit")),
            "payload_cache_hit": bool(rp.get("payload_cache_hit")),
            "row_count": row_count,
            "col_count": int(payload.get("selected_count") or 0),
            "wafer_count": len(payload.get("headers") or []),
            "cache_status": rp.get("root_cache_status") or "",
            "username": str(rp.get("username") or ""),
            "user_role": str(rp.get("user_role") or ""),
        }
        # HTTP 경로면 직렬화까지 재고 나서 한 줄로 쓴다. 직렬화는 이 함수가
        # 끝난 뒤(view_split_http)에 일어나므로, 여기서 바로 쓰면 KNOB 처럼 큰
        # 응답의 직렬화 비용이 영영 기록되지 않는다.
        pending = getattr(_VIEW_TIMING_TLS, "pending", None)
        if isinstance(pending, list):
            pending.append(entry)
            return
        _search_timing_log.record(entry)
    except Exception:
        pass


def recent_search_timings(limit: int = 50) -> list[dict]:
    return _search_timing_log.recent(limit)


def _view_global_stat_sig() -> tuple:
    """product-독립 전역 파일들의 stat 시그니처를 (global_hard, global_soft) 로 반환.

    짧은 TTL 로 캐시 — 동시 다수 사용자가 매 요청 같은 전역 파일(config/rulebook/
    lot_progress 파생)을 재-stat 하던 공유드라이브 부하를 없앤다. 전역 hard(config/
    rulebook) 변경은 admin 행위라 ≤TTL 지연 허용, soft(lot_progress 파생) 변경은
    어차피 SWR 이 흡수하므로 지연 무해.
    """
    now = time.monotonic()
    with _VIEW_GLOBAL_SIG_LOCK:
        cached = _VIEW_GLOBAL_SIG_CACHE.get("v")
        if cached is not None and (now - cached[0]) < _VIEW_GLOBAL_SIG_TTL:
            return cached[1]
    hard_paths: list[Path] = [SOURCE_CFG, PREFIX_CFG, PRECISION_CFG, RULEBOOK_SCHEMA_FILE]
    for kind in _RULEBOOK_FILES:
        try:
            hard_paths.append(_rulebook_path(kind))
        except Exception:
            pass
    global_hard = tuple(_path_cache_sig(path) for path in hard_paths)
    soft_paths: list[Path] = [
        MATCH_CACHE_STATE_FILE,
        _latest_lot_step_cache_path(),
        PATHS.cache_dir / "lot_progress" / "lot_wf_current.json",
        PATHS.cache_dir / "lot_progress" / "lot_wf_current.parquet",
    ]
    global_soft = tuple(_path_cache_sig(path) for path in soft_paths)
    val = (global_hard, global_soft)
    with _VIEW_GLOBAL_SIG_LOCK:
        _VIEW_GLOBAL_SIG_CACHE["v"] = (now, val)
    return val


def _split_view_cache_dep_signature(product: str, custom_name: str = "", product_fp: Path | None = None) -> tuple:
    """View payload 캐시의 2-tier 의존 시그니처 (hard_sig, soft_sig) 반환.

    hard_sig — 즉시 무효화 대상. 소스 ML_TABLE(신규 lot 신호) + 사용자가 직접
      편집하는 입력(prefix/precision/rulebook/custom tag/management/plan/custom).
      per-product 편집 파일은 항상 fresh stat 하므로 편집·신규 lot 이 지연 없이 반영.
    soft_sig — 백그라운드 스케줄러가 주기적으로 재기록하는 파생 캐시(lot_progress
      최신 lot, match cache, product RAM cache). soft 만 달라졌을 때는 stale-while-
      revalidate 로 캐시를 즉시 서빙하고 백그라운드에서 갱신. (이전에는 이것들이
      hard 와 묶여 lot_progress 재기록마다 모든 검색이 캐시 miss → 풀 재계산.)

    HIT 경로 stat 비용 절감: product-독립 전역 파일은 _view_global_stat_sig 로 짧은
    TTL 캐시해 동시 요청 폭주 시 재-stat 를 제거한다.
    """
    # per-product/사용자편집 파일 — 항상 fresh stat (즉시 무효화 보장).
    fresh_paths: list[Path] = [
        product_fp or _product_path(product),
        _view_product_invalidation_path(product),
        _custom_tags_path(),
        _management_rows_path(),
    ]
    fresh_paths.extend(_plan_alias_paths(product))
    if str(custom_name or "").strip():
        try:
            custom_fp, _clean_name = _custom_file_path_for_name(custom_name)
            fresh_paths.append(custom_fp)
        except HTTPException:
            pass
    per_product_hard = _view_product_signature(fresh_paths)
    global_hard, global_soft = _view_global_stat_sig()
    hard_sig = (per_product_hard, global_hard)
    soft_sig = global_soft + (_product_ram_cache_view_signature(product),)
    return (hard_sig, soft_sig)


def _view_product_invalidation_path(product: str) -> Path:
    canonical = _canonical_mltable_product_name(product, allow_bare=True) or str(product or "").strip().upper()
    digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:16]
    return _base_root() / "cache" / "split_table_view_payload" / "invalidate" / f"{digest}.token"


def _touch_view_product_invalidation(product: str) -> None:
    fp = _view_product_invalidation_path(product)
    try:
        fp.parent.mkdir(parents=True, exist_ok=True)
        tmp = fp.with_name(f"{fp.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(str(time.time_ns()), encoding="utf-8")
        os.replace(tmp, fp)
    except Exception:
        logger.debug("SplitTable product invalidation token write failed: %s", product, exc_info=True)


def _clear_split_view_cache_product(product: str) -> None:
    """한 제품의 RAM/disk view만 무효화하고 다른 사용자·제품의 HIT는 보존한다."""
    canonical = _canonical_mltable_product_name(product, allow_bare=True) or str(product or "").strip().upper()
    if not canonical:
        return
    _touch_view_product_invalidation(canonical)
    global _VIEW_CACHE_BYTES
    with _VIEW_CACHE_LOCK:
        doomed = [key for key in _VIEW_CACHE if str(key[0] if key else "").upper() == canonical.upper()]
        for key in doomed:
            entry = _VIEW_CACHE.pop(key, None)
            if entry is not None:
                _VIEW_CACHE_BYTES = max(0, _VIEW_CACHE_BYTES - entry[3])
    with _VIEW_PRODUCT_SIG_LOCK:
        _VIEW_PRODUCT_SIG_CACHE.clear()
    with _VIEW_REVALIDATE_LOCK:
        for key in list(_VIEW_REVALIDATE_PENDING):
            if str(key[0] if key else "").upper() == canonical.upper():
                _VIEW_REVALIDATE_PENDING.pop(key, None)


def _clear_split_view_cache() -> None:
    global _VIEW_CACHE_BYTES
    with _VIEW_CACHE_LOCK:
        _VIEW_CACHE.clear()
        _VIEW_CACHE_BYTES = 0
    # 전역 시그니처 TTL 캐시도 함께 비운다 — 캐시 재빌드/명시적 무효화가 TTL(≤1s)
    # 지연 없이 즉시 반영되도록.
    with _VIEW_GLOBAL_SIG_LOCK:
        _VIEW_GLOBAL_SIG_CACHE.clear()
    with _VIEW_PRODUCT_SIG_LOCK:
        _VIEW_PRODUCT_SIG_CACHE.clear()
    # 제품 파일 탐색 결과도 함께 — 명시적 무효화는 "배치가 바뀌었을 수 있다"는
    # 신호이므로 TTL 을 기다리지 않는다.
    _clear_product_path_cache()
    # 대기 중이던 재검증도 함께 버린다 — 대상 엔트리가 방금 전부 비워졌으므로
    # 재계산해도 다음 stale hit 때 다시 등록될 뿐인 낭비다.
    with _VIEW_REVALIDATE_LOCK:
        _VIEW_REVALIDATE_PENDING.clear()


# lookup 캐시(hive 파티션) 재빌드가 끝나면 view payload 캐시를 비운다 — stale
# 파티션으로 렌더해 캐시된 payload 를 fresh 데이터로 재계산시키기 위함.
# root_lot 풀도 같이 버린다: 빌드 직후 root 집합이 달라져 있고, 시그니처만
# 믿으면 mtime 해상도 안에서 옛 목록이 잠깐 더 살아남을 수 있다.
try:
    _ml_table_lookup.register_build_complete_hook(
        lambda fp: _clear_split_view_cache_product(Path(fp).stem)
    )
    _ml_table_lookup.register_build_complete_hook(_refresh_root_lot_pool_after_lookup_build)
except Exception:
    logger.debug("ml_table_lookup build-complete hook 등록 실패", exc_info=True)


# ── Pre-pivoted root_lot cache: background build (single-flight per product) ──
# v9.1.x: plan 저장 후 백그라운드 작업 스레드 핸들 — 테스트가 join 으로 완료를 기다린다.
