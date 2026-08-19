_LATEST_IDX_ROOT_COL = _latest_lot_partitions.ROOT_KEY_COL
_LATEST_IDX_DIR_NAME = _latest_lot_partitions.PARTITION_DIR_NAME
_LATEST_IDX_META_FILE = _latest_lot_partitions.META_FILE
_LATEST_IDX_BUILD_LOCK = threading.Lock()
_LATEST_IDX_BUILD_STATE: dict = {"inprogress": False, "last": 0.0}
_LATEST_IDX_BUILD_COOLDOWN_SEC = 60.0
_LATEST_IDX_FRESH_TTL_SEC = 2.0
_LATEST_IDX_FRESH_LOCK = threading.Lock()
_LATEST_IDX_FRESH_CACHE: dict[str, tuple[float, bool]] = {}


def _latest_lot_index_enabled() -> bool:
    return _env_bool("FLOW_SPLITTABLE_LATEST_LOT_INDEX", True)


def _latest_lot_index_dir() -> Path:
    return _latest_lot_partitions.partitions_dir(_latest_lot_step_cache_path())


def _latest_lot_index_meta_path() -> Path:
    return _latest_lot_partitions.meta_path(_latest_lot_step_cache_path())


def _latest_lot_index_source_sig() -> list:
    """(path, mtime, size) of the monolithic file — the partition staleness key."""
    return _latest_lot_partitions.source_signature(_latest_lot_step_cache_path())


def _latest_lot_index_fresh() -> bool:
    """True → 파티션 세트가 현재 monolithic 파일과 정확히 일치.

    stale/miss 면 백그라운드 재빌드를 예약하고 False 를 반환한다 (호출측은
    monolithic 폴백 — 오늘과 동일한 경로라 정확성 저하 없음). 판정은 짧은 TTL
    로 캐시해 요청마다 meta 재읽기/재-stat 을 피한다. TTL 캐시는 monolithic
    경로를 키로 쓴다 — DB 루트가 런타임에 재지정되면(관리자 설정/테스트 sandbox)
    이전 루트의 fresh 판정이 새 루트로 새어 빈 파티션 응답을 내면 안 된다."""
    if not _latest_lot_index_enabled():
        return False
    mono_fp = _latest_lot_step_cache_path()
    cache_key = str(mono_fp)
    now = time.monotonic()
    with _LATEST_IDX_FRESH_LOCK:
        cached = _LATEST_IDX_FRESH_CACHE.get(cache_key)
        if cached is not None and now - cached[0] < _LATEST_IDX_FRESH_TTL_SEC:
            return cached[1]
    fresh = False
    try:
        meta = load_json(_latest_lot_index_meta_path(), {}) or {}
        fresh = bool(meta) and meta.get("source_sig") == _latest_lot_index_source_sig()
    except Exception:
        fresh = False
    if not fresh and mono_fp.is_file():
        _enqueue_latest_lot_index_build(reason="stale")
    with _LATEST_IDX_FRESH_LOCK:
        _LATEST_IDX_FRESH_CACHE[cache_key] = (now, fresh)
        while len(_LATEST_IDX_FRESH_CACHE) > 8:
            _LATEST_IDX_FRESH_CACHE.pop(next(iter(_LATEST_IDX_FRESH_CACHE)))
    return fresh


def _latest_lot_index_partition_lf(product: str, root_lot_id: str):
    """Return the one-root LazyFrame from the partitioned latest-lot cache, or
    None to signal fallback to the monolithic scan."""
    root = str(root_lot_id or "").strip().upper()
    if not root or not _latest_lot_index_fresh():
        return None
    try:
        part = _latest_lot_index_dir() / f"{_LATEST_IDX_ROOT_COL}={root}"
        if not part.is_dir():
            # 파티션 세트가 fresh 인데 이 root 파티션이 없다 → monolithic 에도
            # 이 root 는 없다. 풀스캔 폴백은 같은 결과를 느리게 낼 뿐이므로
            # 빈 프레임으로 즉시 응답한다. 단, 특수문자 root 는 파티션 디렉터리명
            # 인코딩이 다를 수 있으므로 단정하지 않고 monolithic 폴백.
            if _re.fullmatch(r"[A-Z0-9_\-.]+", root):
                return _empty_latest_lot_step_frame().lazy()
            return None
        files = sorted(part.glob("*.parquet"))
        if not files:
            return None
        lf = _scan_parquet_compat([str(p) for p in files])
        names = lf.collect_schema().names()
        if LATEST_LOT_STEP_CACHE_FORMAT_COLUMN not in names:
            return None
        lf = lf.filter(
            pl.col(LATEST_LOT_STEP_CACHE_FORMAT_COLUMN).cast(pl.Int64, strict=False)
            == LATEST_LOT_STEP_CACHE_FORMAT_VERSION
        )
        if _LATEST_IDX_ROOT_COL in names:
            lf = lf.drop(_LATEST_IDX_ROOT_COL)
        lf = _cast_cats_lazy(lf)
        if product and "product" in names:
            values = _latest_cache_product_values(product)
            if values:
                lf = lf.filter(pl.col("product").cast(_STR, strict=False).str.to_uppercase().is_in(sorted(values)))
        return lf
    except Exception:
        logger.debug("latest_lot_index scan failed root=%s", root, exc_info=True)
        return None


def _build_latest_lot_index(reason: str = "reader_self_heal") -> bool:
    """Re-partition the monolithic latest-lot cache by normalized root key."""
    ok = _latest_lot_partitions.sync_partitions(
        _latest_lot_step_cache_path(), reason=reason)
    if ok:
        with _LATEST_IDX_FRESH_LOCK:
            _LATEST_IDX_FRESH_CACHE.clear()
    return ok


def _enqueue_latest_lot_index_build(reason: str = "") -> bool:
    """Single-flight, cooldown-guarded background rebuild of the root partitions.

    Self-heal only — the exporters write the partitions synchronously, so this
    fires just for crash-truncated layouts or files written by older code."""
    if not _latest_lot_index_enabled():
        return False
    now = time.time()
    with _LATEST_IDX_BUILD_LOCK:
        if _LATEST_IDX_BUILD_STATE.get("inprogress"):
            return False
        if now - float(_LATEST_IDX_BUILD_STATE.get("last") or 0.0) < _LATEST_IDX_BUILD_COOLDOWN_SEC:
            return False
        _LATEST_IDX_BUILD_STATE["inprogress"] = True

    def _run():
        try:
            _build_latest_lot_index(reason=reason or "reader_self_heal")
        except Exception as exc:
            logger.warning("latest_lot_index build failed (%s): %s", reason, exc)
        finally:
            with _LATEST_IDX_BUILD_LOCK:
                _LATEST_IDX_BUILD_STATE["inprogress"] = False
                _LATEST_IDX_BUILD_STATE["last"] = time.time()

    threading.Thread(target=_run, daemon=True, name="splittable-latestidx").start()
    logger.info("latest_lot_index build queued (%s)", reason)
    return True


def _filter_latest_lot_step_cache(lf, *, root_lot_id: str = "", fab_lot_id: str = "",
                                  wafer_ids: str = ""):
    try:
        names = lf.collect_schema().names()
    except Exception:
        names = []
    root_scope = str(root_lot_id or "").strip()
    fab_scope = str(fab_lot_id or "").strip()
    wafer_scope = str(wafer_ids or "").strip()
    if root_scope and "root_lot_id" in names:
        lf = lf.filter(_join_key_expr("root_lot_id") == root_scope.upper())
    if fab_scope:
        fab_filters = []
        if "lot_id" in names:
            fab_filters.append(_join_key_expr("lot_id") == fab_scope.upper())
        if fab_filters:
            expr = fab_filters[0]
            for item in fab_filters[1:]:
                expr = expr | item
            lf = lf.filter(expr)
    if wafer_scope and "wafer_id" in names:
        wf_list = [w.strip() for w in wafer_scope.split(",") if w.strip()]
        try:
            wf_ints = [int(w) for w in wf_list]
            wf_strs = set()
            for n in wf_ints:
                wf_strs.update([str(n), f"{n:02d}", f"W{n}", f"W{n:02d}"])
            lf = lf.filter(
                pl.col("wafer_id").cast(_STR, strict=False).is_in(list(wf_strs))
                | pl.col("wafer_id").cast(pl.Int64, strict=False).is_in(wf_ints)
            )
        except ValueError:
            lf = lf.filter(pl.col("wafer_id").cast(_STR, strict=False).is_in(wf_list))
    return lf


def _latest_lot_step_cache_source(product: str, current: dict | None = None) -> str:
    return "lot_progress_latest_cache"


def _fab_history_scope_from_latest_cache(product: str, root_lot_id: str = "", fab_lot_id: str = "",
                                         prefix: str = "", limit: int = 500) -> dict | None:
    cache_lf = _latest_lot_step_cache_lf(product, root_lot_id=root_lot_id)
    if cache_lf is None:
        return None
    source = _latest_lot_step_cache_source(product)
    try:
        names = cache_lf.collect_schema().names()
    except Exception:
        return None
    if not {"root_lot_id", "lot_id"}.issubset(set(names)):
        return None
    q = _filter_latest_lot_step_cache(
        cache_lf,
        root_lot_id=root_lot_id,
        fab_lot_id=fab_lot_id,
    ).select([
        pl.col("root_lot_id").cast(_STR, strict=False).alias("root"),
        pl.col("lot_id").cast(_STR, strict=False).alias("fab"),
        *([pl.col("wafer_id").cast(_STR, strict=False).alias("wafer")] if "wafer_id" in names else []),
    ]).filter(pl.col("root").is_not_null() & pl.col("fab").is_not_null())
    root_scope = str(root_lot_id or "").strip()
    fab_scope = str(fab_lot_id or "").strip()
    if fab_scope:
        q = q.filter(_join_key_expr("fab") == fab_scope.upper())
    elif str(prefix or "").strip():
        q = q.filter(_contains_literal_ci_expr("fab", prefix))
    try:
        fabs = _limited_unique_values(
            q,
            "fab",
            prefix="",
            limit=limit,
            preview_only=not bool(root_scope or fab_scope or str(prefix or "").strip()),
        )
        roots: list[str] = [root_scope] if root_scope else []
        wafers: list[str] = []
        if fab_scope and fabs:
            meta_cols = [pl.col("root")]
            if "wafer" in q.collect_schema().names():
                meta_cols.append(pl.col("wafer"))
            meta_df = q.select(meta_cols).unique().collect()
            roots = sorted({s for s in (_clean_str(v) for v in meta_df["root"].to_list()) if s})
            if "wafer" in meta_df.columns:
                wafers = sorted({s for s in (_clean_str(v) for v in meta_df["wafer"].to_list()) if s}, key=_wafer_sort_key)
    except Exception as e:
        logger.warning("_fab_history_scope_from_latest_cache 실패 (product=%s) %s: %s",
                       product, type(e).__name__, e)
        return None
    return {
        "candidates": fabs,
        "root_ids": roots,
        "wafer_ids": wafers,
        "source": source,
        "cache": True,
        "query_ok": True,
    }


def _fab_history_root_candidates_from_latest_cache(product: str, prefix: str = "", limit: int = 500) -> dict | None:
    cache_lf = _latest_lot_step_cache_lf(product)
    if cache_lf is None:
        return None
    source = _latest_lot_step_cache_source(product)
    try:
        names = cache_lf.collect_schema().names()
    except Exception:
        return None
    if "root_lot_id" not in names:
        return None
    try:
        values = _limited_unique_values(cache_lf, "root_lot_id", prefix=prefix, limit=limit)
    except Exception as e:
        logger.warning("_fab_history_root_candidates_from_latest_cache 실패 (product=%s) %s: %s",
                       product, type(e).__name__, e)
        return None
    return {"candidates": values, "source": source, "cache": True}


def _fab_lot_snapshot_from_latest_cache(product: str, root_lot_id: str, wafer_id: str = "") -> str:
    root = str(root_lot_id or "").strip()
    if not root:
        return ""
    cache_lf = _latest_lot_step_cache_lf(product, root_lot_id=root)
    if cache_lf is None:
        return ""
    try:
        names = cache_lf.collect_schema().names()
    except Exception:
        return ""
    if "lot_id" not in names:
        return ""
    q = (
        _filter_latest_lot_step_cache(cache_lf, root_lot_id=root, wafer_ids=str(wafer_id or ""))
        .select([
            pl.col("lot_id").cast(_STR, strict=False).alias("fab"),
            *([pl.col("tkout_time").cast(_STR, strict=False).alias("ts")] if "tkout_time" in names else []),
        ])
        .filter(pl.col("fab").is_not_null() & (pl.col("fab") != ""))
    )
    if "ts" in q.collect_schema().names():
        q = q.sort("ts", descending=True, nulls_last=True)
    else:
        q = q.sort("fab")
    try:
        df = q.head(1).collect()
    except Exception as e:
        logger.warning("_fab_lot_snapshot_from_latest_cache 실패 (product=%s root=%s wafer=%s) %s: %s",
                       product, root_lot_id, wafer_id, type(e).__name__, e)
        return ""
    if df.is_empty():
        return ""
    return _clean_str(df.item(0, 0))


def _latest_cache_src_product_expr(names: list[str], fallback_product: str):
    """latest cache 의 `src_product` 열 — **그 행이 온 FAB 제품 폴더**.

    `product`(ML_TABLE 제품)와 **의미가 다르고 둘 다 필요하다**:

    - `product` — "이 ML_TABLE 제품 화면에서 도달 가능한 랏". 매칭은 일부러 FAB
      전체를 훑으므로, 자기 FAB 폴더가 없는 제품도 다른 폴더에서 lot lineage 를
      찾아온다. SplitTable 의 fab lot 라벨이 이 의미에 의존한다.
    - `src_product` — "이 랏이 실제로 속한 FAB 제품 폴더". 물량을 세는 대시보드
      WIP 은 이걸 봐야 한다. 예전에는 이 구분이 없어 모든 제품이 같은 랏을 자기
      것으로 세었고, 제품마다 똑같은 wafer 수가 나왔다(2026-07-31).

    출처 열이 없는 옛 캐시는 `product` 와 같은 값으로 채운다 — 읽는 쪽이 항상
    `src_product` 를 쓸 수 있게 하되 동작은 예전과 같아진다.
    """
    fallback = _normalized_fab_product_expr(pl.lit(fallback_product))
    if MATCH_CACHE_SRC_PRODUCT_COL not in names:
        return fallback.alias("src_product")
    src = _normalized_fab_product_expr(pl.col(MATCH_CACHE_SRC_PRODUCT_COL))
    return (
        pl.when(src == "").then(fallback).otherwise(src).alias("src_product")
    )


def export_latest_lot_step_cache(products: list[str] | None = None, *, update_state: bool = False) -> dict:
    """Export product match caches into the canonical latest lot/step parquet."""
    raw_products = [p for p in (products or _match_cache_products("")) if p]
    cache_updated_at = datetime.datetime.now().isoformat(timespec="seconds")
    frames = []
    exported_products: list[str] = []
    skipped: list[dict] = []
    for raw_product in raw_products:
        current = _match_cache_current(raw_product)
        if not current:
            skipped.append({"product": raw_product, "reason": "match_cache_missing"})
            continue
        lf = current.get("lf")
        product = _normalize_latest_cache_product(current.get("product") or raw_product)
        try:
            names = lf.collect_schema().names()
        except Exception as e:
            skipped.append({"product": product, "reason": f"schema_failed: {type(e).__name__}"})
            continue
        if MATCH_CACHE_ROOT_COL not in names or MATCH_CACHE_FAB_COL not in names:
            skipped.append({"product": product, "reason": "required_columns_missing"})
            continue
        lot_type_col = _ci_resolve_in("lot_type", names)
        exprs = [
            pl.lit(LATEST_LOT_STEP_CACHE_FORMAT_VERSION).cast(pl.Int16).alias(LATEST_LOT_STEP_CACHE_FORMAT_COLUMN),
            pl.lit(LATEST_LOT_STEP_CACHE_SOURCE).alias(LATEST_LOT_STEP_CACHE_SOURCE_COLUMN),
            # product 는 예전 그대로 ML_TABLE 제품 — SplitTable 의 교차 폴더
            # lineage 조회가 이 의미에 의존한다. 실제 소속은 src_product 로 따로.
            pl.lit(product).alias("product"),
            _latest_cache_src_product_expr(names, product),
            pl.col(MATCH_CACHE_ROOT_COL).cast(_STR, strict=False).alias("root_lot_id"),
            (
                pl.col(MATCH_CACHE_WAFER_COL).cast(_STR, strict=False).alias("wafer_id")
                if MATCH_CACHE_WAFER_COL in names else pl.lit("").alias("wafer_id")
            ),
            pl.col(MATCH_CACHE_FAB_COL).cast(_STR, strict=False).alias("lot_id"),
            (
                pl.col("step_id").cast(_STR, strict=False).alias("step_id")
                if "step_id" in names else pl.lit("").alias("step_id")
            ),
            pl.lit("").alias("function_step"),
            # 매칭 캐시는 (root, wafer) 별 FAB 최신 행을 들고 있고 아래에서
            # tkout_time 내림차순 unique 로 한 번 더 최신만 남기므로, 여기서
            # 그대로 실으면 "가장 최신 FAB 행의 lot_type" 이 된다.
            (
                # 앞뒤 공백은 여기서 없앤다 — 대시보드 lot_type 필터가 원본 문자열을
                # 그대로 구분해서, "BUILD" 와 "BUILD " 가 눈에 똑같은 항목 두 개로
                # 나오고 한쪽을 고르면 나머지 wafer 가 사라졌다.
                pl.col(lot_type_col).cast(_STR, strict=False).fill_null("")
                  .str.strip_chars().alias("lot_type")
                if lot_type_col else pl.lit("").alias("lot_type")
            ),
            (
                pl.col(MATCH_CACHE_TS_COL).cast(_STR, strict=False).alias("tkout_time")
                if MATCH_CACHE_TS_COL in names else pl.lit("").alias("tkout_time")
            ),
            pl.lit(cache_updated_at).alias("update_time"),
        ]
        frames.append(lf.select(exprs))
        exported_products.append(product)
    fp = _latest_lot_step_cache_path()
    fp.parent.mkdir(parents=True, exist_ok=True)
    _cleanup_legacy_latest_lot_step_cache()
    if frames:
        q = pl.concat(frames)
        q = q.filter(
            pl.col("product").is_not_null()
            & (pl.col("product") != "")
            & pl.col("root_lot_id").is_not_null()
            & (pl.col("root_lot_id") != "")
            & pl.col("lot_id").is_not_null()
            & (pl.col("lot_id") != "")
        )
        q = (
            q.sort("tkout_time", descending=True, nulls_last=True)
             .unique(subset=["product", "root_lot_id", "wafer_id"], keep="first", maintain_order=True)
             .sort(["product", "root_lot_id", "wafer_id"])
        )
        try:
            from core.parquet_perf import collect_streaming
            df = collect_streaming(q)
        except Exception:
            df = q.collect()
        function_steps = []
        step_meta_cache: dict[tuple[str, str], str] = {}
        try:
            from core.lot_step import lookup_step_meta
        except Exception:
            lookup_step_meta = None
        for product_value, step_value in df.select(["product", "step_id"]).iter_rows():
            product_text = str(product_value or "")
            step_text = str(step_value or "").strip()
            key = (product_text, step_text)
            if key not in step_meta_cache:
                meta = lookup_step_meta(product=product_text, step_id=step_text) if lookup_step_meta and step_text else {}
                step_meta_cache[key] = str((meta or {}).get("function_step") or (meta or {}).get("func_step") or "")
            function_steps.append(step_meta_cache[key])
        df = df.with_columns(pl.Series("function_step", function_steps)).select(LATEST_LOT_STEP_CACHE_COLUMNS)
    else:
        df = _empty_latest_lot_step_frame()
    tmp = fp.with_suffix(fp.suffix + ".tmp")
    try:
        tmp.unlink(missing_ok=True)
    except Exception:
        pass
    df.write_parquet(tmp)
    tmp.replace(fp)
    # per-root 파티션을 같은 쓰기 시점에 동기화 — df 가 손에 있으므로 read-back
    # 없이 즉시 파티션이 fresh 가 된다. 실패해도 reader 의 monolithic 폴백 +
    # self-heal 재빌드가 있으므로 export 는 성공으로 처리한다.
    try:
        _latest_lot_partitions.sync_partitions(fp, df=df, reason="match_cache_export")
    except Exception as e:
        logger.warning("latest-lot per-root partition sync failed %s: %s",
                       type(e).__name__, e)
    with _LATEST_IDX_FRESH_LOCK:
        _LATEST_IDX_FRESH_CACHE.clear()
    result = {
        "ok": True,
        "path": str(fp),
        "row_count": int(df.height),
        "products": exported_products,
        "skipped": skipped,
        "cache_updated_at": cache_updated_at,
    }
    if update_state:
        _mark_match_cache_refreshed(result)
    return result


# status 의 parquet 파생 수치(전체/제품별 row 수, product 목록, max update_time)는
# monolithic 파일 시그니처가 같으면 불변이다. /view 가 캐시 미스마다 이 함수를
# 호출해 monolithic 파일을 4회 full-collect 하던 것이 검색 지연의 고정비용이었다
# — (sig, product) 키로 메모이즈해 파일이 재기록될 때만 재계산한다.
_LATEST_STATUS_STATS_LOCK = threading.Lock()
_LATEST_STATUS_STATS_CACHE: dict[str, tuple[tuple, dict]] = {}
_LATEST_STATUS_STATS_MAX = 64


def _latest_lot_step_cache_parquet_stats(product: str, fp: Path) -> dict:
    key = str(product or "").strip().upper()
    sig = _path_cache_sig(fp)
    with _LATEST_STATUS_STATS_LOCK:
        cached = _LATEST_STATUS_STATS_CACHE.get(key)
        if cached is not None and cached[0] == sig:
            return dict(cached[1])
    lf = _latest_lot_step_cache_lf("")
    if lf is None:
        raise RuntimeError("latest cache is not readable")
    names = lf.collect_schema().names()
    format_version = 0
    cache_source = ""
    if LATEST_LOT_STEP_CACHE_FORMAT_COLUMN in names:
        try:
            format_version = int(
                lf.select(
                    pl.col(LATEST_LOT_STEP_CACHE_FORMAT_COLUMN)
                    .cast(pl.Int64, strict=False)
                    .max()
                ).collect().item(0, 0) or 0
            )
        except Exception:
            format_version = 0
    if LATEST_LOT_STEP_CACHE_SOURCE_COLUMN in names:
        try:
            cache_source = str(
                lf.select(pl.col(LATEST_LOT_STEP_CACHE_SOURCE_COLUMN).drop_nulls().first())
                .collect().item(0, 0) or ""
            )
        except Exception:
            cache_source = ""
    total_df = lf.select(pl.len().alias("row_count")).collect()
    row_count = int(total_df.item(0, 0) or 0)
    products: list[str] = []
    if "product" in names:
        prod_df = (
            lf.select(pl.col("product").cast(_STR, strict=False).alias("product"))
            .filter(pl.col("product").is_not_null() & (pl.col("product") != ""))
            .unique()
            .sort("product")
            .head(500)
            .collect()
        )
        products = [str(v) for v in prod_df["product"].to_list() if str(v or "").strip()]
    product_row_count = row_count
    if str(product or "").strip():
        product_row_count = 0
        if "product" in names:
            product_lf = _latest_lot_step_cache_lf(product)
            if product_lf is not None:
                product_row_count = int(product_lf.select(pl.len().alias("row_count")).collect().item(0, 0) or 0)
    updated_at = ""
    if "update_time" in names:
        try:
            value = lf.select(pl.col("update_time").cast(_STR, strict=False).max().alias("updated_at")).collect().item(0, 0)
            if value:
                updated_at = str(value)
        except Exception:
            pass
    stats = {
        "format_version": format_version,
        "expected_format_version": LATEST_LOT_STEP_CACHE_FORMAT_VERSION,
        "format_current": format_version == LATEST_LOT_STEP_CACHE_FORMAT_VERSION,
        "cache_source": cache_source,
        "row_count": row_count,
        "product_row_count": product_row_count,
        "products": products,
        "updated_at": updated_at,
    }
    with _LATEST_STATUS_STATS_LOCK:
        _LATEST_STATUS_STATS_CACHE[key] = (sig, dict(stats))
        while len(_LATEST_STATUS_STATS_CACHE) > _LATEST_STATUS_STATS_MAX:
            _LATEST_STATUS_STATS_CACHE.pop(next(iter(_LATEST_STATUS_STATS_CACHE)))
    return stats


def _latest_lot_step_cache_status(product: str = "") -> dict:
    """Return a non-throwing status summary for the canonical FAB match cache."""
    fp = _latest_lot_step_cache_path()
    freshness = _match_cache_global_fresh()
    state = _match_cache_state()
    base = {
        "ok": True,
        "cache_path": str(fp),
        "cache_exists": fp.is_file(),
        "row_count": 0,
        "product_row_count": 0,
        "products": [],
        "updated_at": state.get("updated_at") or state.get("last_refresh_at") or "",
        "latest_updated_at": state.get("updated_at") or "",
        "last_refresh_at": state.get("last_refresh_at") or "",
        "interval_minutes": _match_cache_refresh_minutes(),
        "latest_cache": freshness,
        "format_version": 0,
        "expected_format_version": LATEST_LOT_STEP_CACHE_FORMAT_VERSION,
        "format_current": False,
        "cache_source": "",
    }
    if not fp.is_file():
        return base
    try:
        stats = _latest_lot_step_cache_parquet_stats(product, fp)
        updated_at = stats.get("updated_at") or base["updated_at"]
        if not updated_at:
            try:
                updated_at = datetime.datetime.fromtimestamp(fp.stat().st_mtime).isoformat(timespec="seconds")
            except Exception:
                updated_at = ""
        return {
            **base,
            "format_version": int(stats.get("format_version") or 0),
            "expected_format_version": LATEST_LOT_STEP_CACHE_FORMAT_VERSION,
            "format_current": bool(stats.get("format_current")),
            "cache_source": str(stats.get("cache_source") or ""),
            "row_count": int(stats.get("row_count") or 0),
            "product_row_count": int(stats.get("product_row_count") or 0),
            "products": list(stats.get("products") or []),
            "updated_at": updated_at,
            "latest_updated_at": updated_at,
        }
    except Exception as e:
        logger.warning("SplitTable latest lot-step cache status failed (%s) %s: %s", fp, type(e).__name__, e)
        return {**base, "ok": False, "error": f"{type(e).__name__}: {e}"}


def _resolve_match_cache_columns(ov: dict, main_names_list: list[str], fab_schema_names: list[str]) -> dict:
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

    root_col = _resolve_source_col_name((ov.get("root_col") or "").strip(), fab_schema_names) \
               or _pick_first_present_ci(("root_lot_id",), fab_schema_names)
    wafer_col = _resolve_source_col_name((ov.get("wf_col") or ov.get("wafer_col") or "").strip(), fab_schema_names) \
                or _pick_first_present_ci(("wafer_id", "wafer"), fab_schema_names)
    fc_raw = (ov.get("fab_col") or "").strip()
    fab_col = (_resolve_source_col_name(fc_raw, fab_schema_names) if fc_raw else "") \
              or _pick_first_present_ci(_FAB_COL_CANDIDATES, fab_schema_names) \
              or "fab_lot_id"
    tc_raw = (ov.get("ts_col") or "").strip()
    ts_col = (_resolve_source_col_name(tc_raw, fab_schema_names) if tc_raw else "") \
             or _pick_ts_col(fab_schema_names)

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
    return {
        "join_keys": join_keys,
        "root_col": root_col,
        "wafer_col": wafer_col,
        "fab_col": fab_col,
        "ts_col": ts_col,
        "override_cols": override_cols,
    }


def _join_fab_projection_into_main(lf, main_names: set[str], fab_proj, join_keys: list[str],
                                   override_cols: list[str], *, fab_has_join_tmp: bool = False):
    join_aliases = [(k, f"__join_key_{i}") for i, k in enumerate(join_keys)]
    join_tmp_keys = [tmp for _, tmp in join_aliases]
    if not fab_has_join_tmp:
        fab_proj = fab_proj.with_columns([_join_key_expr(k).alias(tmp) for k, tmp in join_aliases])
    lf = lf.with_columns([_join_key_expr(k).alias(tmp) for k, tmp in join_aliases])
    backup_cols: list = []
    for c in override_cols:
        if c in main_names:
            bk = f"__main_bk_{c}"
            lf = lf.with_columns(pl.col(c).alias(bk))
            backup_cols.append((c, bk))
            lf = lf.drop(c)
    lf = lf.join(fab_proj, on=join_tmp_keys, how="left").drop(join_tmp_keys)
    for c, bk in backup_cols:
        if c.casefold() == "fab_lot_id":
            # FAB lot ids should come from the FAB DB connection table.
            lf = lf.drop(bk)
        else:
            lf = lf.with_columns(pl.coalesce([pl.col(c), pl.col(bk)]).alias(c)).drop(bk)
    joined_lot_col = next((c for c in override_cols if str(c).casefold() == "lot_id"), "")
    joined_fab_col = next((c for c in override_cols if str(c).casefold() == "fab_lot_id"), "")
    if joined_lot_col and not joined_fab_col:
        # Raw FAB now uses lot_id as the fab-lot key. Keep raw schema clean, but
        # expose the legacy view label so SplitTable grouping/export still works.
        lf = lf.with_columns(pl.col(joined_lot_col).cast(_STR, strict=False).alias("fab_lot_id"))
    return lf


def _latest_lot_progress_projection(product: str, main_names_list: list[str],
                                     root_lot_id: str = "", fab_lot_id: str = "",
                                     wafer_ids: str = "") -> dict | None:
    """Use the canonical LOT progress cache as SplitTable's lot identity source."""
    cache_lf = _latest_lot_step_cache_lf(product, root_lot_id=root_lot_id)
    if cache_lf is None:
        return None
    main_names = set(main_names_list)
    # New products do not always use the literal root_lot_id/wafer_id names.
    # Resolve configured aliases first, then use the same schema detection as the
    # SplitTable renderer.  Previously LOT_ID (or a configured root column) made
    # this projection return None, so the FAB cache existed but every wafer header
    # was rendered with an unassigned "-" lot label.
    configured_root = ""
    configured_wafer = ""
    try:
        cfg = load_json_cached(SOURCE_CFG, {}) or {}
        ov = _lot_override_for(cfg, product)
        configured_root = _ci_resolve_in(str(ov.get("root_col") or ""), main_names_list)
        configured_wafer = _ci_resolve_in(
            str(ov.get("wf_col") or ov.get("wafer_col") or ""), main_names_list)
    except Exception:
        pass
    detected_root, detected_wafer = find_lot_wafer_cols(main_names_list)
    root_key = (
        configured_root
        or _ci_resolve_in("root_lot_id", main_names_list)
        or detected_root
    )
    wafer_key = (
        configured_wafer
        or _ci_resolve_in("wafer_id", main_names_list)
        or _pick_first_present_ci(("wafer_id", "wf_id", "wafer"), main_names_list)
        or detected_wafer
    )
    if not root_key or root_key not in main_names:
        return None
    join_keys = [root_key]
    if wafer_key and wafer_key in main_names:
        join_keys.append(wafer_key)
    try:
        names = cache_lf.collect_schema().names()
    except Exception:
        return None
    if not {"root_lot_id", "lot_id"}.issubset(set(names)):
        return None
    q = _filter_latest_lot_step_cache(
        cache_lf,
        root_lot_id=root_lot_id,
        fab_lot_id=fab_lot_id,
        wafer_ids=wafer_ids,
    )
    join_aliases = [(k, f"__join_key_{i}") for i, k in enumerate(join_keys)]
    exprs = []
    for source_col, (_main_key, tmp) in zip(["root_lot_id", "wafer_id"], join_aliases):
        if source_col not in names:
            return None
        exprs.append(_join_key_expr(source_col).alias(tmp))
    exprs.extend([
        pl.col("lot_id").cast(_STR, strict=False).alias("lot_id"),
        pl.col("lot_id").cast(_STR, strict=False).alias("fab_lot_id"),
        (
            pl.col("tkout_time").cast(_STR, strict=False).alias(MATCH_CACHE_TS_COL)
            if "tkout_time" in names else pl.lit("").alias(MATCH_CACHE_TS_COL)
        ),
    ])
    try:
        proj = (
            q.select(exprs)
            .filter(pl.col("lot_id").is_not_null() & (pl.col("lot_id") != ""))
            .sort(MATCH_CACHE_TS_COL, descending=True, nulls_last=True)
            .unique(subset=[tmp for _k, tmp in join_aliases], keep="first", maintain_order=True)
            .select([tmp for _k, tmp in join_aliases] + ["lot_id", "fab_lot_id"])
        )
    except Exception as e:
        logger.warning("latest LOT progress projection failed (product=%s) %s: %s",
                       product, type(e).__name__, e)
        return None
    return {
        "lf": proj,
        "join_keys": join_keys,
        "override_cols": ["lot_id", "fab_lot_id"],
        "meta": {
            "source": "lot_progress_latest_cache",
            "path": str(_latest_lot_step_cache_path()),
        },
    }


def _filter_match_cache_scope(cache_lf, root_lot_id: str = "", fab_lot_id: str = "",
                              wafer_ids: str = ""):
    try:
        names = cache_lf.collect_schema().names()
    except Exception:
        names = []
    root_scope = str(root_lot_id or "").strip()
    fab_scope = str(fab_lot_id or "").strip()
    wafer_scope = str(wafer_ids or "").strip()
    if root_scope and MATCH_CACHE_ROOT_COL in names:
        cache_lf = cache_lf.filter(_join_key_expr(MATCH_CACHE_ROOT_COL) == root_scope.upper())
    if fab_scope and MATCH_CACHE_FAB_COL in names:
        cache_lf = cache_lf.filter(_join_key_expr(MATCH_CACHE_FAB_COL) == fab_scope.upper())
    if wafer_scope and MATCH_CACHE_WAFER_COL in names:
        wf_list = [w.strip() for w in wafer_scope.split(",") if w.strip()]
        try:
            wf_ints = [int(w) for w in wf_list]
            wf_strs = set()
            for n in wf_ints:
                wf_strs.update([str(n), f"{n:02d}", f"W{n}", f"W{n:02d}"])
            cache_lf = cache_lf.filter(
                pl.col(MATCH_CACHE_WAFER_COL).cast(_STR, strict=False).is_in(list(wf_strs))
                | pl.col(MATCH_CACHE_WAFER_COL).cast(pl.Int64, strict=False).is_in(wf_ints)
            )
        except ValueError:
            cache_lf = cache_lf.filter(pl.col(MATCH_CACHE_WAFER_COL).cast(_STR, strict=False).is_in(wf_list))
    return cache_lf


def _cached_fab_projection(product: str, ov: dict, fab_source: str, main_names_list: list[str],
                           root_lot_id: str = "", fab_lot_id: str = "", wafer_ids: str = "") -> dict | None:
    current = _match_cache_current(product)
    if not current:
        return None
    meta = current["meta"]
    if meta.get("fab_source") != _normalize_fab_source_path(fab_source):
        return None
    join_keys = [k for k in (meta.get("join_keys") or []) if k in main_names_list]
    join_tmp_keys = list(meta.get("join_tmp_keys") or [])
    if not join_keys or len(join_keys) != len(join_tmp_keys):
        return None
    cache_lf = _filter_match_cache_scope(current["lf"], root_lot_id=root_lot_id,
                                         fab_lot_id=fab_lot_id, wafer_ids=wafer_ids)
    try:
        cache_names = cache_lf.collect_schema().names()
    except Exception:
        return None
    override_cols = [c for c in (meta.get("override_cols") or []) if c in cache_names]
    if not override_cols:
        return None
    keep = list(dict.fromkeys(join_tmp_keys + override_cols + ([MATCH_CACHE_TS_COL] if MATCH_CACHE_TS_COL in cache_names else [])))
    fab_proj = cache_lf.select(keep)
    if MATCH_CACHE_TS_COL in keep:
        fab_proj = fab_proj.sort(MATCH_CACHE_TS_COL, descending=True, nulls_last=True)
        fab_proj = fab_proj.unique(subset=join_tmp_keys, keep="first", maintain_order=True)
    else:
        fab_proj = fab_proj.unique(subset=join_tmp_keys, keep="last")
    return {
        "lf": fab_proj.select(list(dict.fromkeys(join_tmp_keys + override_cols))),
        "join_keys": join_keys,
        "join_tmp_keys": join_tmp_keys,
        "override_cols": override_cols,
        "meta": meta,
    }


def _fab_history_scope_from_cache(product: str, root_lot_id: str = "", fab_lot_id: str = "",
                                  prefix: str = "", limit: int = 500) -> dict | None:
    latest = _fab_history_scope_from_latest_cache(
        product,
        root_lot_id=root_lot_id,
        fab_lot_id=fab_lot_id,
        prefix=prefix,
        limit=limit,
    )
    if latest is not None:
        return latest
    current = _match_cache_current(product)
    if not current:
        return None
    cache_lf = current["lf"]
    try:
        names = cache_lf.collect_schema().names()
    except Exception:
        return None
    if MATCH_CACHE_ROOT_COL not in names or MATCH_CACHE_FAB_COL not in names:
        return None
    root_scope = str(root_lot_id or "").strip()
    fab_scope = str(fab_lot_id or "").strip()
    q = cache_lf.select([
        pl.col(MATCH_CACHE_ROOT_COL).cast(_STR, strict=False).alias("root"),
        pl.col(MATCH_CACHE_FAB_COL).cast(_STR, strict=False).alias("fab"),
        *([pl.col(MATCH_CACHE_WAFER_COL).cast(_STR, strict=False).alias("wafer")] if MATCH_CACHE_WAFER_COL in names else []),
    ]).filter(pl.col("root").is_not_null() & pl.col("fab").is_not_null())
    if root_scope:
        q = q.filter(_join_key_expr("root") == root_scope.upper())
    if fab_scope:
        q = q.filter(_join_key_expr("fab") == fab_scope.upper())
    elif str(prefix or "").strip():
        q = q.filter(_contains_literal_ci_expr("fab", prefix))
    try:
        fabs = _limited_unique_values(q, "fab", prefix="", limit=limit,
                                      preview_only=not bool(root_scope or fab_scope or str(prefix or "").strip()))
        roots: list[str] = [root_scope] if root_scope else []
        wafers: list[str] = []
        if fab_scope and fabs:
            meta_cols = [pl.col("root")]
            if "wafer" in q.collect_schema().names():
                meta_cols.append(pl.col("wafer"))
            meta_df = q.select(meta_cols).unique().collect()
            roots = sorted({s for s in (_clean_str(v) for v in meta_df["root"].to_list()) if s})
            if "wafer" in meta_df.columns:
                wafers = sorted({s for s in (_clean_str(v) for v in meta_df["wafer"].to_list()) if s}, key=_wafer_sort_key)
    except Exception as e:
        logger.warning("_fab_history_scope_from_cache 실패 (product=%s) %s: %s",
                       product, type(e).__name__, e)
        return None
    return {
        "candidates": fabs,
        "root_ids": roots,
        "wafer_ids": wafers,
        "source": current.get("fab_source", ""),
        "cache": True,
        "query_ok": True,
    }


def _fab_history_root_candidates_from_cache(product: str, prefix: str = "", limit: int = 500) -> dict | None:
    latest = _fab_history_root_candidates_from_latest_cache(product, prefix=prefix, limit=limit)
    if latest is not None:
        return latest
    current = _match_cache_current(product)
    if not current:
        return None
    cache_lf = current["lf"]
    try:
        names = cache_lf.collect_schema().names()
    except Exception:
        return None
    if MATCH_CACHE_ROOT_COL not in names:
        return None
    try:
        values = _limited_unique_values(cache_lf, MATCH_CACHE_ROOT_COL, prefix=prefix, limit=limit)
    except Exception as e:
        logger.warning("_fab_history_root_candidates_from_cache 실패 (product=%s) %s: %s",
                       product, type(e).__name__, e)
        return None
    return {"candidates": values, "source": current.get("fab_source", ""), "cache": True}


def _fab_lot_snapshot_from_cache(product: str, root_lot_id: str, wafer_id: str = "") -> str:
    latest = _fab_lot_snapshot_from_latest_cache(product, root_lot_id, wafer_id)
    if latest:
        return latest
    current = _match_cache_current(product)
    root = str(root_lot_id or "").strip()
    if not current or not root:
        return ""
    cache_lf = _filter_match_cache_scope(current["lf"], root_lot_id=root, wafer_ids=str(wafer_id or ""))
    try:
        names = cache_lf.collect_schema().names()
    except Exception:
        return ""
    if MATCH_CACHE_FAB_COL not in names:
        return ""
    q = (
        cache_lf
        .select([
            pl.col(MATCH_CACHE_FAB_COL).cast(_STR, strict=False).alias("fab"),
            *([pl.col(MATCH_CACHE_TS_COL).cast(_STR, strict=False).alias("ts")] if MATCH_CACHE_TS_COL in names else []),
        ])
        .filter(pl.col("fab").is_not_null() & (pl.col("fab") != ""))
    )
    if "ts" in q.collect_schema().names():
        q = q.sort("ts", descending=True, nulls_last=True)
    else:
        q = q.sort("fab")
    try:
        df = q.head(1).collect()
    except Exception as e:
        logger.warning("_fab_lot_snapshot_from_cache 실패 (product=%s root=%s wafer=%s) %s: %s",
                       product, root_lot_id, wafer_id, type(e).__name__, e)
        return ""
    if df.is_empty():
        return ""
    return _clean_str(df.item(0, 0))


def _build_match_cache_streamed(
    q_base,
    tmp: Path,
    *,
    unique_subset: list[str],
    ts_present: bool,
    batch_col: str,
    roots_per_batch: int,
    product: str = "",
) -> int:
    """root_lot_id(=dedup subset 컬럼) 단위로 FAB 매칭캐시를 나눠 빌드한다.

    글로벌 ``q.sort(ts).unique()`` 는 파이프라인 브레이커라 전체 FAB 를 메모리에
    버퍼링해 peak RAM 이 폭발한다(수십 GB). 여기서는 dedup subset 에 속한 한 컬럼
    (``batch_col``, 정규화된 join key = root_lot_id)의 값으로 배치를 나눈다.
    dedup subset 의 한 컬럼으로 나누면 서로 다른 배치는 dedup 그룹이 절대 겹치지
    않으므로, 각 배치를 개별적으로 sort+unique 한 뒤 **마지막에 sort/unique 없이
    스트리밍 병합만** 하면 글로벌 sort+unique 와 결과가 동일하다.

    peak RAM 은 한 배치(roots_per_batch 개의 root) 크기로 제한된다. 대신 FAB 원천을
    배치 수만큼 재스캔하므로 느리다(속도↔메모리 트레이드오프, 메모리 우선).
    배치 사이에서 프레임을 해제(del+gc)하고 메모리 가드를 확인한다.
    """
    def _dedup(part_q):
        if ts_present:
            part_q = part_q.sort(MATCH_CACHE_TS_COL, descending=True, nulls_last=True)
            return part_q.unique(subset=unique_subset, keep="first", maintain_order=True)
        return part_q.unique(subset=unique_subset, keep="last")

    # 캐시 이벤트 로그(사용자가 보는 화면)로 진행 상황을 내보내는 헬퍼.
    # warmup/cache_op 와 같은 category 라 수동스캔 '전체' 필터에 실시간 표시된다.
    def _emit(event: str, *, ok: bool = True, detail: dict | None = None):
        try:
            from core.cache_event_log import record as _rec
            _rec("cache_op", event, ok=ok, detail=detail or {}, product=product)
        except Exception:
            pass

    def _rss_gb() -> float:
        try:
            from core.runtime_limits import process_memory_snapshot
            snap = process_memory_snapshot()
            return round(float(snap.get("process_rss_gb")
                               or snap.get("process_memory_effective_gb") or 0.0), 2)
        except Exception:
            return 0.0

    def _fmt_dur(sec: float) -> str:
        sec = int(max(0, sec))
        if sec < 60:
            return f"{sec}초"
        if sec < 3600:
            return f"{sec // 60}분 {sec % 60}초"
        return f"{sec // 3600}시간 {(sec % 3600) // 60}분"

    partdir = tmp.parent / (tmp.name + ".parts")
    try:
        if partdir.exists():
            for f in partdir.glob("*.parquet"):
                try:
                    f.unlink(missing_ok=True)
                except Exception:
                    pass
            try:
                partdir.rmdir()
            except Exception:
                pass
    except Exception:
        pass

    # 글로벌 FAB(q_base = _scan_global_fab_sources 전체 concat)를 배치마다 재스캔하면
    # 배치 수만큼 전체 FAB 를 다시 읽어 매우 느리고, Windows 워킹셋이 반복 mmap 으로
    # 계속 부풀어 RSS 가 꾸준히 오른다. 따라서 필요한 컬럼만 투영된 q_base 를 **딱 한 번**
    # 임시 parquet 로 흘려쓴다(정렬 없음 → 완전 스트리밍, 저메모리). 이후 배치/중복제거는
    # 이 작은 로컬 파일만 읽으므로 글로벌 재스캔이 사라진다(속도·RSS 동시 개선).
    base_tmp = tmp.parent / (tmp.name + ".base.parquet")
    try:
        base_tmp.unlink(missing_ok=True)
    except Exception:
        pass
    _emit(f"[매칭] {product}: 원본 FAB 1회 스캔·투영 중… (배치 재스캔 제거)",
          detail={"phase": "base_scan", "rss_gb": _rss_gb()})
    _write_match_cache_lazyframe(q_base, base_tmp)
    base_lf = pl.scan_parquet(str(base_tmp))

    # 배치 키 값 목록 — 작은 투영본에서 한 컬럼만 collect (글로벌 재스캔 아님).
    try:
        vals_df = base_lf.select(pl.col(batch_col)).unique().collect(streaming=True)
    except TypeError:
        vals_df = base_lf.select(pl.col(batch_col)).unique().collect()
    vals = sorted({str(v) for v in vals_df.get_column(batch_col).drop_nulls().to_list()})

    if not vals:
        # 빈 결과: 스키마만 있는 빈 parquet 생성 (legacy 경로와 동일 산출).
        try:
            return _write_match_cache_lazyframe(_dedup(base_lf), tmp)
        finally:
            try:
                base_tmp.unlink(missing_ok=True)
            except Exception:
                pass

    partdir.mkdir(parents=True, exist_ok=True)
    part_paths: list[Path] = []
    total_roots = len(vals)
    total_batches = (total_roots + roots_per_batch - 1) // roots_per_batch
    started = time.time()
    rows_done = 0
    last_emit = 0.0
    emit_min_gap = _match_cache_stream_log_gap_seconds()
    _emit(
        f"[매칭] {product}: 스트리밍 빌드 시작 — 총 {total_roots:,} 랏 → {total_batches:,} 배치"
        f" (배치당 {roots_per_batch:,} 랏)",
        detail={"phase": "start", "roots": total_roots, "batches": total_batches,
                "roots_per_batch": roots_per_batch, "rss_gb": _rss_gb(),
                "progress": _match_progress(0, total_roots),
                "stage": _stage("match", "start")},
    )
    try:
        for idx in range(0, total_roots, roots_per_batch):
            if _MATCH_CACHE_STOP.is_set():
                raise RuntimeError("match cache build stopped")
            if _match_cache_cancelled(product):
                # 배치 경계에서만 끊는다 — 쓰는 중인 part 파일을 남기지 않는다.
                raise MatchCacheCancelled(product)
            _wait_for_match_cache_memory()
            batch_no = idx // roots_per_batch + 1
            b_start = time.time()
            chunk = vals[idx:idx + roots_per_batch]
            part_q = _dedup(base_lf.filter(pl.col(batch_col).is_in(chunk)))
            part_path = partdir / f"part_{batch_no:05d}.parquet"
            part_rows = _write_match_cache_lazyframe(part_q, part_path)
            part_paths.append(part_path)
            rows_done += int(part_rows or 0)
            b_dur = time.time() - b_start
            elapsed = time.time() - started
            roots_done = min(idx + len(chunk), total_roots)
            pct = roots_done * 100 // total_roots if total_roots else 100
            # ETA: 지금까지 배치당 평균 소요 × 남은 배치.
            avg = elapsed / batch_no if batch_no else 0.0
            eta = avg * (total_batches - batch_no)
            rss = _rss_gb()
            try:
                mem_lots = _ml_table_lookup.root_ram_cache_lot_count()
            except Exception:
                mem_lots = 0
            try:
                _match_cache_job_update(
                    stream_product=product, stream_batch=batch_no,
                    stream_batch_total=total_batches, stream_roots=total_roots,
                    stream_roots_done=roots_done, stream_rows=rows_done,
                    stream_rss_gb=rss, stream_eta_sec=round(eta, 1),
                    stream_mem_lots=mem_lots,
                )
            except Exception:
                pass
            # 첫·마지막 배치는 항상, 그 외엔 최소 간격 스로틀 — 작은 배치의 로그 폭주 방지.
            now = time.time()
            if batch_no == 1 or batch_no == total_batches or (now - last_emit) >= emit_min_gap:
                last_emit = now
                _emit(
                    f"[매칭] {product}: 랏 {roots_done:,}/{total_roots:,} ({pct}%)"
                    f" · 배치 {batch_no:,}/{total_batches:,} · {b_dur:.1f}s"
                    f" · 누적 {rows_done:,}행 · 메모리 {mem_lots}랏 상주 · RSS {rss}GB"
                    f" · 남은 ~{_fmt_dur(eta)}",
                    detail={"phase": "batch", "batch": batch_no, "batches": total_batches,
                            "roots_done": roots_done, "roots_total": total_roots,
                            "batch_sec": round(b_dur, 2), "elapsed_sec": round(elapsed, 1),
                            "eta_sec": round(eta, 1), "rows": rows_done,
                            "mem_lots": mem_lots, "rss_gb": rss,
                            "progress": _match_progress(roots_done, total_roots)},
                )
            try:
                del part_q
                gc.collect()
            except Exception:
                pass

        # 배치끼리 batch_col 값이 배타적 → sort/unique 없이 스트리밍 병합만 하면 된다.
        merged = pl.scan_parquet([str(p) for p in part_paths])
        row_count = _write_match_cache_lazyframe(merged, tmp)
        _emit(
            f"[매칭] {product}: 완료 — {total_roots:,}랏 · {row_count:,}행 · {total_batches:,}배치"
            f" · 총 {_fmt_dur(time.time() - started)} · RSS {_rss_gb()}GB",
            detail={"phase": "done", "roots": total_roots, "rows": int(row_count),
                    "batches": total_batches, "total_sec": round(time.time() - started, 1),
                    "rss_gb": _rss_gb(),
                    "progress": _match_progress(total_roots, total_roots, state="done"),
                    "stage": _stage("match", "done")},
        )
    finally:
        for p in part_paths:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
        try:
            partdir.rmdir()
        except Exception:
            pass
        try:
            base_tmp.unlink(missing_ok=True)
        except Exception:
            pass
    return row_count


def _refresh_match_cache_products(products: list[str], force: bool = False) -> dict:
    """Build persisted FAB root/fab/wafer connection tables for known products."""
    products = [p for p in products if p]
    results: list[dict] = []
    with _MATCH_CACHE_BUILD_LOCK:
        MATCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        canceled_scan = False
        for raw_product in products:
            # 제품 경계 = 안전한 취소 지점. 이미 만든 제품 캐시는 그대로 두고
            # 남은 제품을 건너뛴 뒤 다음 큐 작업으로 넘어간다.
            if _scan_cancel_requested():
                if not canceled_scan:
                    canceled_scan = True
                    logger.info("match cache: 스캔 큐 중단 요청 — 남은 제품 건너뜀")
                results.append({"product": raw_product, "ok": False, "skipped": True,
                                "row_count": 0, "reason": "scan_canceled"})
                continue
            ml_product, ov, fab_source = _current_fab_override(raw_product)
            result = {"product": ml_product or raw_product, "ok": False, "skipped": False, "row_count": 0, "fab_source": fab_source}
            try:
                if not ml_product:
                    result["reason"] = "FAB source not matched"
                    results.append(result)
                    continue
                if not fab_source and not _global_fab_source_paths(""):
                    result["reason"] = "FAB source not matched"
                    results.append(result)
                    continue
                config_key = _match_cache_config_key(ml_product, ov, fab_source)
                fp = _match_cache_path(ml_product)
                meta_fp = _match_cache_meta_path(ml_product)
                old_meta = load_json(meta_fp, {}) if meta_fp.is_file() else {}
                if not force and fp.is_file() and isinstance(old_meta, dict) and old_meta.get("config_key") == config_key:
                    age_s = time.time() - float(old_meta.get("built_epoch") or 0)
                    if age_s < _match_cache_refresh_minutes() * 60:
                        result.update({"ok": True, "skipped": True, "row_count": int(old_meta.get("row_count") or 0)})
                        results.append(result)
                        continue

                main_lf = _scan_product_base(ml_product)
                main_names_list = main_lf.collect_schema().names()
                fab_lf, fab_sources = _scan_global_fab_sources(fab_source, tag_source_product=True)
                if fab_lf is None:
                    result["reason"] = "FAB source scan failed"
                    result["fab_sources"] = fab_sources
                    results.append(result)
                    continue
                fab_lf, fab_schema_names = _ci_align_fab_to_main(fab_lf, main_names_list)
                try:
                    fab_schema_names = fab_lf.collect_schema().names()
                except Exception:
                    pass
                result["fab_sources"] = fab_sources
                cols = _resolve_match_cache_columns(ov, main_names_list, fab_schema_names)
                join_keys = cols["join_keys"]
                override_cols = cols["override_cols"]
                if not join_keys or not override_cols:
                    result["reason"] = "join keys or override columns missing"
                    result["join_keys"] = join_keys
                    result["override_cols"] = override_cols
                    results.append(result)
                    continue

                wanted = list(dict.fromkeys(
                    join_keys
                    + override_cols
                    + ([cols["ts_col"]] if cols["ts_col"] else [])
                    + ([cols["root_col"]] if cols["root_col"] else [])
                    + ([cols["wafer_col"]] if cols["wafer_col"] else [])
                    + ([cols["fab_col"]] if cols["fab_col"] else [])
                ))
                # 출처 제품 열은 override_cols 해석 대상이 아니므로(FAB 원본 열이
                # 아니다) 여기서 명시적으로 살려 둔다.
                if MATCH_CACHE_SRC_PRODUCT_COL in fab_schema_names:
                    wanted = list(wanted) + [MATCH_CACHE_SRC_PRODUCT_COL]
                wanted = [c for c in wanted if c in fab_schema_names]
                q = fab_lf.select(wanted)
                join_tmp_keys = [f"__join_key_{i}" for i, _ in enumerate(join_keys)]
                exprs = [_join_key_expr(k).alias(tmp) for k, tmp in zip(join_keys, join_tmp_keys)]
                if cols["root_col"] and cols["root_col"] in fab_schema_names:
                    exprs.append(pl.col(cols["root_col"]).cast(_STR, strict=False).alias(MATCH_CACHE_ROOT_COL))
                if cols["wafer_col"] and cols["wafer_col"] in fab_schema_names:
                    exprs.append(pl.col(cols["wafer_col"]).cast(_STR, strict=False).alias(MATCH_CACHE_WAFER_COL))
                if cols["fab_col"] and cols["fab_col"] in fab_schema_names:
                    exprs.append(pl.col(cols["fab_col"]).cast(_STR, strict=False).alias(MATCH_CACHE_FAB_COL))
                if cols["ts_col"] and cols["ts_col"] in fab_schema_names:
                    exprs.append(pl.col(cols["ts_col"]).cast(_STR, strict=False).alias(MATCH_CACHE_TS_COL))
                q = q.with_columns(exprs)
                # 전 FAB 폴더를 읽되 target ML_TABLE의 랏/wafer key에 해당하는 행만
                # 남긴다. 이 제한이 없으면 제품마다 같은 전 FAB cache가 만들어진다.
                q = _scope_match_cache_to_main_keys(main_lf, q, join_keys, join_tmp_keys)
                keep = list(dict.fromkeys(
                    join_tmp_keys
                    + [MATCH_CACHE_ROOT_COL, MATCH_CACHE_WAFER_COL, MATCH_CACHE_FAB_COL,
                       MATCH_CACHE_TS_COL, MATCH_CACHE_SRC_PRODUCT_COL]
                    + override_cols
                ))
                q_names = q.collect_schema().names()
                keep = [c for c in keep if c in q_names]
                q = q.select(keep)
                for k in join_tmp_keys:
                    q = q.filter(pl.col(k).is_not_null() & (pl.col(k) != ""))
                # The persisted cache is the authoritative SplitTable
                # root/wafer -> FAB lot mapping.  Keep exactly one FAB row per
                # root_lot_id + wafer_id join key, chosen by latest tkout/time,
                # so SplitTable and Inform snapshots read the same lot_id basis.
                unique_subset = [c for c in join_tmp_keys if c in keep]
                if not unique_subset:
                    unique_subset = [c for c in (MATCH_CACHE_ROOT_COL, MATCH_CACHE_WAFER_COL) if c in keep]
                if not unique_subset:
                    unique_subset = [c for c in (MATCH_CACHE_FAB_COL,) if c in keep]
                ts_present = MATCH_CACHE_TS_COL in keep
                tmp = fp.with_suffix(fp.suffix + ".tmp")

                # root_lot_id 배치 스트리밍: dedup subset 에 속한 정규화 join key 한 컬럼
                # (가능하면 root_lot_id) 으로 배치를 나눠 peak RAM 을 한 배치 크기로 제한.
                # batch_col 은 반드시 join_tmp_keys(=null 필터 완료) 중 하나여야 행 손실이 없다.
                subset_join_keys = [c for c in unique_subset if c in join_tmp_keys]
                batch_col = ""
                if subset_join_keys:
                    root_src = cols.get("root_col") or ""
                    if root_src:
                        for jk, tmpk in zip(join_keys, join_tmp_keys):
                            if tmpk in subset_join_keys and str(jk).casefold() == str(root_src).casefold():
                                batch_col = tmpk
                                break
                    if not batch_col:
                        batch_col = subset_join_keys[0]

                if _match_cache_stream_enabled() and batch_col:
                    row_count = _build_match_cache_streamed(
                        q, tmp,
                        unique_subset=unique_subset,
                        ts_present=ts_present,
                        batch_col=batch_col,
                        roots_per_batch=_match_cache_stream_batch_roots(),
                        product=ml_product,
                    )
                else:
                    # legacy: 글로벌 sort+unique (streaming off 또는 배치 키 없음).
                    if ts_present:
                        q = q.sort(MATCH_CACHE_TS_COL, descending=True, nulls_last=True)
                        q = q.unique(subset=unique_subset, keep="first", maintain_order=True)
                    else:
                        q = q.unique(subset=unique_subset, keep="last")
                    row_count = _write_match_cache_lazyframe(q, tmp)
                tmp.replace(fp)
                meta = {
                    "version": MATCH_CACHE_VERSION,
                    "product": ml_product,
                    "fab_source": _normalize_fab_source_path(fab_source),
                    "fab_sources": fab_sources,
                    "config_key": config_key,
                    "built_at": datetime.datetime.now().isoformat(timespec="seconds"),
                    "built_epoch": time.time(),
                    "row_count": int(row_count),
                    "join_keys": join_keys,
                    "join_tmp_keys": join_tmp_keys,
                    "dedup_keys": unique_subset,
                    "override_cols": override_cols,
                    "root_col": cols["root_col"],
                    "wafer_col": cols["wafer_col"],
                    "fab_col": cols["fab_col"],
                    "ts_col": cols["ts_col"],
                }
                save_json(meta_fp, meta)
                _LOT_LOOKUP_CACHE.clear()
                # root 후보 풀은 ML_TABLE lookup candidate index가 authoritative다.
                # FAB match/pivot 캐시 갱신은 root 집합을 바꾸지 않으므로 이미 준비된
                # 신규 제품 후보 풀을 지우지 않는다.
                result.update({"ok": True, "row_count": int(row_count), "join_keys": join_keys,
                               "override_cols": override_cols, "fab_sources": fab_sources})
            except MatchCacheCancelled:
                # 중단: 기존 캐시 파일과 meta 는 건드리지 않고 임시 산출만 지운다.
                # meta 를 안 쓰므로 다음 스캔은 이 제품을 처음부터 다시 빌드한다.
                try:
                    _match_cache_path(ml_product).with_suffix(
                        _match_cache_path(ml_product).suffix + ".tmp").unlink(missing_ok=True)
                except Exception:
                    pass
                cancel = _match_cache_cancel_target()
                result.update({
                    "ok": False,
                    "skipped": False,
                    "cancelled": True,
                    "reason": f"관리자 중단 ({cancel.get('by') or '-'}) — 다음 스캔에서 처음부터 다시 빌드합니다",
                })
                logger.warning("SplitTable match cache build cancelled by admin (product=%s)", ml_product)
            except Exception as e:
                logger.warning("SplitTable match cache build failed (product=%s) %s: %s",
                               raw_product, type(e).__name__, e, exc_info=True)
                result["reason"] = f"{type(e).__name__}: {e}"
            results.append(result)
            try:
                gc.collect()
            except Exception:
                pass
    return {"ok": any(r.get("ok") for r in results), "products": results, "interval_minutes": _match_cache_refresh_minutes()}


def _canonical_product_set(products: list[str]) -> set[str]:
    out = set()
    for p in products or []:
        text = _canonical_mltable_product_name(p, allow_bare=True) or str(p or "").strip()
        if text:
            out.add(text.upper())
    return out


def _match_cache_products_cover_all(products: list[str]) -> bool:
    expected = _canonical_product_set(_match_cache_products(""))
    got = _canonical_product_set(products)
    return bool(expected) and expected.issubset(got)


def refresh_match_cache(product: str = "", force: bool = False, max_products: int | None = None) -> dict:
    """Build persisted FAB root/fab/wafer connection tables for SplitTable.

    Callers that warm the whole cache can pass max_products to pace large
    sweeps. Product-specific calls keep the historical synchronous behavior.
    """
    products = _match_cache_products(product)
    if max_products is not None:
        try:
            n = max(1, int(max_products))
            products = products[:n]
        except Exception:
            pass
    result = _refresh_match_cache_products(products, force=force)
    try:
        export_products = _match_cache_products("") or products
        export = export_latest_lot_step_cache(products=export_products, update_state=_match_cache_products_cover_all(products))
        result["latest_cache"] = export
    except Exception as e:
        logger.warning("SplitTable unified latest cache export failed: %s", e, exc_info=True)
        result["latest_cache"] = {"ok": False, "reason": f"{type(e).__name__}: {e}"}
    return result


def _match_cache_progress(job_id: str, message: str, *, ok: bool = True) -> None:
    """FAB 매칭 캐시 진행 신호 — 이벤트 로그 한 줄 + job heartbeat.

    로그가 곧 진행 신호다: cache_event_log.record 가 detail.job_id 로 작업의
    updated_ts 를 갱신하고, reap_stale_jobs() 는 그 값으로 정지를 판정한다.
    조용히 오래 도는 구간이 있으면 그 작업은 기본 5분 뒤 '응답 없음'으로 실패
    처리되고 뒤 단계는 시작조차 못 한다.

    stage 는 일부러 붙이지 않는다 — 단계 시작/종료 이벤트와 섞이면 화면의
    단계별 이력이 진행 로그로 오염된다.
    """
    try:
        from core.cache_event_log import record as _rec, heartbeat as _beat
    except Exception:
        return
    try:
        _rec("scan", message, ok=ok, detail={"job_id": job_id} if job_id else None)
        if job_id:
            _beat(job_id, message)
    except Exception:
        logger.debug("match cache progress log failed", exc_info=True)


def _match_cache_followups(force: bool, *, job_id: str = "", built: list[str] | None = None,
                           refresh_plan_risk: bool = False) -> dict:
    """FAB 매칭 캐시 뒤에 이어지는 파생 캐시 갱신.

    제품 루프가 끝난 뒤에도 남는 무거운 작업이다 — 통합 latest-lot 캐시 export 는
    전 제품 매칭 캐시를 concat/sort/unique 후 전량 collect 하고 행마다 step 메타를
    붙인다. 예전에는 이 구간이 로그도 heartbeat 도 없이 돌아서, 화면상 'FAB 100%'
    인 채로 멈춘 것처럼 보이고 5분 뒤엔 작업 전체가 '응답 없음'으로 실패한다.
    각 단계를 진행 신호로 남긴다.

    built: 이번에 실제로 빌드한 제품 목록. export 는 항상 전 제품을 대상으로 하되,
    '전역 매칭 캐시가 갱신됐다'는 상태 표시는 전 제품을 덮었을 때만 남긴다 —
    제품 하나만 수동 갱신한 것을 전체 갱신으로 기록하면 다음 주기가 건너뛰어진다.
    """
    out: dict[str, Any] = {}
    if refresh_plan_risk and not _MATCH_CACHE_STOP.is_set():
        _match_cache_progress(job_id, "[FAB 후처리] plan risk 캐시 갱신 중…")
        try:
            refresh_plan_risk_cache(force=False)
            out["plan_risk"] = {"ok": True}
        except Exception as e:
            logger.warning("SplitTable plan risk cache refresh after match cache failed: %s", e)
            out["plan_risk"] = {"ok": False, "reason": f"{type(e).__name__}: {e}"}
            _match_cache_progress(job_id, f"[FAB 후처리] plan risk 캐시 실패: {e}", ok=False)
    if not _MATCH_CACHE_STOP.is_set():
        _match_cache_progress(job_id, "[FAB 후처리] 통합 latest-lot 캐시 export 중… "
                                      "(전 제품 매칭 캐시 병합 — 데이터가 크면 수 분 걸립니다)")
        try:
            products = _match_cache_products("")
            export = export_latest_lot_step_cache(
                products=products or list(built or []),
                update_state=_match_cache_products_cover_all(list(built or products)))
            out["latest_cache"] = export
            _match_cache_progress(
                job_id, f"[FAB 후처리] 통합 latest-lot 캐시 완료 — "
                        f"{int(export.get('row_count') or 0):,}행 / "
                        f"{len(export.get('products') or [])}개 제품")
        except Exception as e:
            logger.warning("SplitTable unified latest cache export after match cache failed: %s",
                           e, exc_info=True)
            out["latest_cache"] = {"ok": False, "reason": f"{type(e).__name__}: {e}"}
            _match_cache_progress(job_id, f"[FAB 후처리] 통합 latest-lot 캐시 실패: {e}", ok=False)
    if not _MATCH_CACHE_STOP.is_set():
        _match_cache_progress(job_id, "[FAB 후처리] LOT 진행 캐시 갱신 중…")
        try:
            from core.lot_progress_cache import refresh_lot_progress_cache
            # 진행 문구를 그대로 이벤트 로그 + heartbeat 로 흘린다 — 이 구간이 수십 분
            # 걸리는데 예전에는 '갱신 중…' 한 줄만 남아 멈춘 것과 구분이 안 됐다.
            state = refresh_lot_progress_cache(
                force=force,
                required_products=list(built or []),
                progress=lambda msg: _match_cache_progress(
                    job_id, f"[FAB 후처리] LOT 진행 캐시 — {msg}"))
            out["lot_progress"] = {"ok": True}
            _match_cache_progress(
                job_id, "[FAB 후처리] LOT 진행 캐시 완료 — "
                        f"{int((state or {}).get('count') or 0):,}건")
        except Exception as e:
            logger.warning("LOT progress cache refresh after SplitTable match cache failed: %s", e)
            out["lot_progress"] = {"ok": False, "reason": f"{type(e).__name__}: {e}"}
            _match_cache_progress(job_id, f"[FAB 후처리] LOT 진행 캐시 실패: {e}", ok=False)
    return out


def _run_started_match_cache_job(products: list[str], force: bool, reason: str = "manual",
                                 refresh_plan_risk: bool = False, job_id: str = "",
                                 followups: bool = True) -> dict:
    """제품별 FAB 매칭 캐시 빌드. followups=False 면 파생 캐시 갱신은 호출자가 맡는다.

    통합 스캔은 followups=False 로 부른다 — 파생 캐시(통합 latest-lot export 등)를
    이 단계 안에서 돌리면 제품이 100% 끝난 뒤에도 stage 가 안 끝나, 화면에서는
    'FAB 100% 인데 다음 단계로 안 넘어감' 으로 보인다. 제품 원본 캐시는 파생 캐시에
    의존하지 않으므로 스캔은 바로 다음 단계로 넘어가고, 파생 캐시는 Root lot 단계
    직전(실제로 필요한 지점)에 별도 진행 표시와 함께 돈다.
    """
    pause_s = _match_cache_product_pause_seconds()
    total = len(products)
    try:
        if not products:
            return {"ok": True, "queued": False, "products": [], "job": _match_cache_job_status()}
        for idx, raw_product in enumerate(products):
            if _MATCH_CACHE_STOP.is_set():
                break
            if not _wait_for_match_cache_memory():
                break
            _match_cache_job_update(current_product=raw_product, paused=False)
            try:
                from core import worker_dispatch as _wd
                automatic = reason not in {"manual", "unified_scan"}

                result = _wd.run_heavy(
                    "splittable_match_cache_refresh",
                    {"product": raw_product, "force": bool(force)},
                    lambda: _refresh_match_cache_products([raw_product], force=force),
                    label=f"match_cache:{raw_product}",
                    local_idle_only=(reason != "unified_scan"),
                    local_fallback=not automatic,
                    durable=automatic,
                    priority="maintenance" if automatic else "normal",
                    dedupe_key=f"match_cache:{raw_product}",
                    timeout_sec=6 * 3600.0 if automatic else None,
                ) or {"ok": False, "products": []}
            except Exception as e:
                logger.warning("SplitTable match cache queued build failed (product=%s) %s: %s",
                               raw_product, type(e).__name__, e, exc_info=True)
                result = {
                    "ok": False,
                    "products": [{
                        "product": raw_product,
                        "ok": False,
                        "skipped": False,
                        "row_count": 0,
                        "reason": f"{type(e).__name__}: {e}",
                    }],
                    "interval_minutes": _match_cache_refresh_minutes(),
                }
            _match_cache_job_append_products(result.get("products") or [])
            # 이 제품에 걸린 중단 신호는 여기서 푼다 — 안 풀면 다음 제품까지
            # 연쇄로 끊기거나, 다음 스캔이 시작하자마자 다시 중단된다.
            cancelled = any(r.get("cancelled") for r in (result.get("products") or []))
            if cancelled or _match_cache_cancelled(raw_product):
                clear_match_cache_cancel(raw_product)
            # 제품 하나가 수 분 걸릴 수 있다 — 완료마다 진행 신호를 남겨야 작업이
            # '응답 없음'으로 오판되지 않는다.
            _match_cache_progress(
                job_id,
                f"[FAB 매칭] {idx + 1}/{total} {raw_product} "
                + ("관리자 중단 — 다음 제품으로 넘어갑니다 (이 제품은 다음 스캔에서 처음부터)"
                   if cancelled else "처리 완료"),
                ok=not cancelled,
            )
            if idx < len(products) - 1 and pause_s > 0:
                _MATCH_CACHE_STOP.wait(pause_s)
        if followups:
            _match_cache_followups(force, job_id=job_id, built=products,
                                   refresh_plan_risk=refresh_plan_risk)
    finally:
        _match_cache_job_update(
            running=False,
            queued=False,
            current_product="",
            paused=False,
            finished_at=datetime.datetime.now().isoformat(timespec="seconds"),
        )
    status = _match_cache_job_status()
    return {
        "ok": bool(status.get("ok_count")),
        "queued": False,
        "products": status.get("products") or [],
        "interval_minutes": _match_cache_refresh_minutes(),
        "job": status,
        "reason": reason,
    }


def enqueue_match_cache_refresh(product: str = "", force: bool = True, reason: str = "manual",
                                refresh_plan_risk: bool = False) -> dict:
    """Queue a paced match-cache refresh and return immediately.

    서버 스캔 게이트를 통과하므로 다른 스캔(통합 스캔·전체 셋업·예약)과 절대
    겹치지 않는다. job state(_begin_match_cache_job)는 큐에서 꺼내 **실제로
    시작할 때** 잡는다 — 대기 중에 running 으로 잡아두면 다른 스캔이 '이미
    실행 중'으로 오판하고, 화면에는 진행 없는 running 이 오래 떠 있게 된다."""
    products = _match_cache_products(product)
    label = f"FAB 매칭 캐시 갱신 ({product or '전체 제품'})"

    def _start() -> dict:
        started, status = _begin_match_cache_job(products, force=force, reason=reason)
        if not started:
            return {
                "ok": True,
                "skipped": True,
                "job": status,
                "detail": "SplitTable match cache refresh is already running.",
            }
        return _run_started_match_cache_job(products, force, reason,
                                            refresh_plan_risk=refresh_plan_risk)

    out = _submit_scan("match_cache", label, _start, product=product,
                       source="scheduler" if reason == "scheduler" else "manual",
                       dedupe_key=f"match_cache:{reason}:{product}")
    return {
        **out,
        "products": [{"product": p, "queued": True} for p in products],
        "interval_minutes": _match_cache_refresh_minutes(),
        "job": _match_cache_job_status(),
    }


def _seconds_until_next_match_cache_tick() -> float:
    return max(60.0, _match_cache_refresh_minutes() * 60.0)


def _match_cache_loop() -> None:
    global _MATCH_CACHE_NEXT_TICK_AT
    while not _MATCH_CACHE_STOP.is_set():
        try:
            from core.background_owner import is_owner
            if not is_owner():
                _MATCH_CACHE_STOP.wait(5.0)
                continue
            freshness = _match_cache_global_fresh()
            if freshness.get("fresh"):
                logger.info("SplitTable match cache scheduler skipped; latest cache fresh until %s",
                            freshness.get("next_refresh_at") or "")
            else:
                # 예약 갱신도 수동 스캔과 같은 게이트를 통과한다 — 예전엔 통합
                # 스캔이 2/3 단계에 들어간 사이 이 tick 이 FAB 전체 스캔을 새로
                # 시작해 한 서버에서 두 스캔이 겹쳤다.
                enqueue_match_cache_refresh(product="", force=False, reason="scheduler",
                                            refresh_plan_risk=True)
        except Exception as e:
            logger.warning("SplitTable match cache scheduler tick failed: %s", e)
        wait_s = _seconds_until_next_match_cache_tick()
        _MATCH_CACHE_NEXT_TICK_AT = datetime.datetime.fromtimestamp(
            time.time() + wait_s).isoformat(timespec="seconds")
        while wait_s > 0 and not _MATCH_CACHE_STOP.is_set():
            step = min(wait_s, 60.0)
            _MATCH_CACHE_STOP.wait(step)
            wait_s -= step


def start_match_cache_scheduler() -> bool:
    global _MATCH_CACHE_THREAD, _MATCH_CACHE_STARTED
    if _MATCH_CACHE_STARTED:
        return False
    if _auto_product_cache_enabled():
        logger.info("SplitTable match-cache timer retired; product rotation owns refresh")
        return False
    try:
        from core.runtime_limits import splittable_match_cache_enabled
        if not splittable_match_cache_enabled():
            logger.info("SplitTable match cache scheduler disabled")
            return False
    except Exception:
        pass
    _MATCH_CACHE_STOP.clear()
    _MATCH_CACHE_THREAD = threading.Thread(target=_match_cache_loop, name="splittable-match-cache", daemon=True)
    _MATCH_CACHE_THREAD.start()
    _MATCH_CACHE_STARTED = True
    logger.info("SplitTable match cache scheduler started (interval=%sm)", _match_cache_refresh_minutes())
    return True


class MatchCacheRefreshReq(BaseModel):
    product: str = ""
    force: bool = True


class MatchCacheStopReq(BaseModel):
    # 비우면 지금 돌고 있는 제품을 중단한다.
    product: str = ""


class ProductRamCacheRefreshReq(BaseModel):
    product: str = ""
    force: bool = True


class RootLotRamCacheRefreshReq(BaseModel):
    product: str = ""
    force: bool = True


@router.get("/match-cache/status")
def match_cache_status(request: Request, product: str = Query("")):
    me = current_user(request)
    if me.get("role") != "admin":
        raise HTTPException(403, "admin only")
    try:
        from core.runtime_limits import splittable_match_cache_enabled
        enabled = splittable_match_cache_enabled()
    except Exception:
        enabled = True
    products = [product] if str(product or "").strip() else [p.get("name") for p in list_products().get("products", [])]
    rows = []
    for prod in [p for p in products if p]:
        current = _match_cache_current(prod)
        if not current:
            continue
        meta = current.get("meta") or {}
        rows.append({
            "product": current.get("product") or prod,
            "fab_source": current.get("fab_source") or "",
            "path": str(current.get("path") or ""),
            "built_at": meta.get("built_at", ""),
            "row_count": int(meta.get("row_count") or 0),
            "join_keys": meta.get("join_keys") or [],
        })
    return {
        "ok": True,
        "enabled": enabled,
        "interval_minutes": _match_cache_refresh_minutes(),
        "products": rows,
        "latest_cache": _match_cache_global_fresh(),
        "job": _match_cache_job_status(),
    }


@router.post("/match-cache/refresh")
def refresh_match_cache_now(req: MatchCacheRefreshReq, request: Request, _a=Depends(require_page_manager("splittable"))):
    return enqueue_match_cache_refresh(product=req.product or "", force=bool(req.force), reason="manual")


@router.post("/match-cache/stop")
def stop_match_cache_product(req: MatchCacheStopReq, request: Request,
                             _a=Depends(require_page_manager("splittable"))):
    """진행 중인 제품 하나의 캐싱을 중단하고 다음 제품으로 넘긴다.

    중단한 제품은 부분 산출을 버리므로 **다음 스캔에서 처음부터** 다시 빌드된다
    (이어받기 없음). 기존에 완성돼 있던 캐시 파일은 그대로 남는다."""
    me = current_user(request)
    job = _match_cache_job_status()
    target = str(req.product or "").strip() or str(job.get("current_product") or "")
    if not target:
        raise HTTPException(409, "중단할 진행 중인 캐싱 작업이 없습니다.")
    if not job.get("running"):
        raise HTTPException(409, "실행 중인 FAB 매칭 캐시 작업이 없습니다.")
    cancel = request_match_cache_cancel(target, by=me.get("username") or "admin")
    try:
        from core.cache_event_log import record as _record
        _record("scan", f"[FAB 매칭] {target} 캐싱 중단 요청 — {me.get('username') or 'admin'}", ok=False)
    except Exception:
        pass
    return {"ok": True, "cancel": cancel, "job": _match_cache_job_status()}


@router.get("/product-cache/status")
def product_ram_cache_status(request: Request, product: str = Query("")):
    me = current_user(request)
    include_detail = is_page_manager(me, "splittable")
    products = _product_ram_cache_products(product)
    rows = [_product_ram_cache_public_meta(prod, include_detail=include_detail) for prod in products]
    return {
        "ok": True,
        "enabled": _product_ram_cache_available(),
        "scheduler_enabled": _product_ram_cache_scheduler_enabled(),
        "interval_minutes": _product_ram_cache_refresh_minutes(),
        "max_gb": round(_product_ram_cache_max_bytes() / (1024 ** 3), 3) if _product_ram_cache_max_bytes() else 0,
        "products": rows,
        "job": _product_ram_cache_job_status() if include_detail else {
            "running": _product_ram_cache_job_status().get("running", False),
            "queued": _product_ram_cache_job_status().get("queued", False),
        },
    }


@router.post("/product-cache/refresh")
def refresh_product_ram_cache_now(req: ProductRamCacheRefreshReq, request: Request):
    me = current_user(request)
    if not is_page_manager(me, "splittable"):
        raise HTTPException(403, "Admin or page manager (splittable) only")
    return {
        "ok": False,
        "disabled": True,
        "product": str(req.product or "").strip(),
        "detail": "제품 전체 RAM 예열은 폐기되어 수동 적재도 실행하지 않습니다.",
    }


@router.get("/root-lot-cache/status")
def root_lot_ram_cache_status(request: Request, product: str = Query("")):
    me = current_user(request)
    include_detail = is_page_manager(me, "splittable")
    source_fp = None
    if str(product or "").strip():
        try:
            source_fp = _product_path(product)
        except Exception:
            source_fp = None
    out = {
        "ok": True,
        "settings": _ml_table_lookup.root_ram_cache_settings(),
        "cache": _ml_table_lookup.root_ram_cache_status(source_fp, include_detail=include_detail),
    }
    # 관리자에게만 최근 검색 타이밍 breakdown 을 노출한다.
    if include_detail:
        out["recent_searches"] = recent_search_timings(limit=30)
    return out


@router.get("/search-timings")
def get_search_timings(request: Request, hours: float = Query(24.0), limit: int = Query(200),
                       origin: str = Query("")):
    """검색 타이밍 기간 조회 + 집계 (관리자 전용).

    공유 JSONL 에 누적된 기록을 읽어 wait(줄서기) / compute(계산) 분포를 낸다.
    slow_wait_pct 가 지속적으로 높으면 cold 레인 슬롯을 늘릴 근거가 되고,
    wait 은 낮은데 compute 가 크면 레인이 아니라 캐시/계산 쪽 문제다.

    origin 은 서버 라벨(예: "운영", "개발(worker)") — 로그가 두 서버 공유라
    기본값(빈 값)은 합산이다. 이 서버만 보려면 origin 을 지정한다."""
    if not is_page_manager(current_user(request), "splittable"):
        raise HTTPException(403, "관리자 전용")
    # "__self__" = 이 서버 — 프런트가 라벨을 미리 알 필요 없이 자기 서버만 볼 수 있게.
    if str(origin or "").strip() == "__self__":
        origin = _search_timing_log.this_origin()
    out = _search_timing_log.query(hours=hours, limit=limit, origin=origin)
    out["ok"] = True
    out["cold_lane"] = _view_cold_lane_stats()
    return out


@router.post("/root-lot-cache/refresh")
def refresh_root_lot_ram_cache_now(req: RootLotRamCacheRefreshReq, _perm=Depends(require_page_manager("splittable"))):
    """관리자: root lot RAM 캐시 적재를 스캔 게이트에 넣는다(비동기).

    예전엔 요청 스레드에서 그대로 적재해 응답이 수십 분 걸릴 수 있었고, 진행
    중인 다른 스캔과도 겹쳤다. 결과는 캐시 이벤트 로그와 `/root-lot-cache/status`
    로 확인한다."""
    return {
        "ok": False,
        "disabled": True,
        "product": str(req.product or "").strip(),
        "detail": "Root lot RAM 예열은 폐기되어 수동 적재도 실행하지 않습니다.",
    }


class ScanQueueCancelReq(BaseModel):
    task_id: str = ""


@router.post("/scan-queue/cancel")
def cancel_scan_queue_task(req: ScanQueueCancelReq, request: Request,
                           _a=Depends(require_page_manager("splittable"))):
    """진행 중이거나 대기 중인 캐시 작업을 중단한다.

    대기 중이면 큐에서 바로 빼고, 진행 중이면 중단을 요청한다 — **즉시 멈추지
    않는다.** 현재 제품/배치가 끝나는 안전한 지점에서 접고 다음 큐 작업으로
    넘어간다(스레드를 강제 종료하면 parquet 파티션이 깨진다).

    이미 완성된 제품 캐시는 그대로 남고 즉시 계속 사용한다. FAB 랏 인덱스는
    완료 배치를 staging에 보존해 다음 실행에서 이어받는다.
    """
    from core import scan_gate

    me = current_user(request)
    out = scan_gate.cancel(str(req.task_id or ""), by=me.get("username") or "admin")
    if not out.get("ok"):
        raise HTTPException(409, out.get("detail") or "중단할 작업을 찾을 수 없습니다.")
    # 자동 순환 작업이 아직 대기 중일 때는 scan_gate 에서 제거하는 것만으로는
    # 스케줄러의 queued_product 가 남는다. 그러면 다음 poll 에서 취소한 작업이
    # 다시 현재/다음으로 보이거나 재등록된다. 같은 취소 응답으로 커서도 정리한다.
    if out.get("state") == "removed" and str(out.get("source") or "") == "scheduler":
        _auto_product_cache_on_cancelled(
            str(out.get("product") or ""), str(out.get("id") or "")
        )
    # FAB 매칭 단계가 돌고 있으면 제품 단위 중단 신호도 함께 세운다 — 그 단계는
    # 자체 취소 경로(_MATCH_CACHE_CANCEL)로 현재 배치를 접는 게 가장 빠르다.
    try:
        job = _match_cache_job_status()
        if job.get("running") and str(job.get("current_product") or "").strip():
            request_match_cache_cancel(str(job.get("current_product")),
                                       by=me.get("username") or "admin")
    except Exception:
        logger.debug("scan queue cancel: match cache cancel passthrough failed", exc_info=True)
    return {**out, "queue": _scan_gate_snapshot()}


class LotListCacheRefreshReq(BaseModel):
    product: str = ""


@router.get("/lot-pool/status")
def lot_list_cache_status(request: Request):
    """제품별 root_lot_id 풀 캐시 상태 (관리자 전용).

    드롭다운이 느리다는 신고가 오면 여기부터 본다 — `entries` 에 제품이 없거나
    `hit_ram`/`hit_disk` 대비 `miss` 가 크면 소스 시그니처가 계속 흔들리고
    있다는 뜻이다(스케줄러가 캐시를 자주 다시 쓰는 경우)."""
    if not is_page_manager(current_user(request), "splittable"):
        raise HTTPException(403, "관리자 전용")
    return {**_lot_list_cache.stats(), "prewarm": dict(_CANDIDATE_PREWARM_LAST)}


@router.post("/lot-pool/refresh")
def refresh_lot_list_cache(req: LotListCacheRefreshReq,
                           _perm=Depends(require_page_manager("splittable"))):
    """관리자: root_lot_id 풀 캐시를 비우고 즉시 다시 만든다.

    product 를 비우면 전체를 버리기만 한다(다음 조회에서 각자 다시 빌드).
    """
    product = str(req.product or "").strip()
    dropped = _invalidate_root_lot_pool(product)
    rebuilt = 0
    fab_rebuilt = 0
    if product:
        try:
            pool = _root_lot_pool(product)
            rebuilt = len(pool.get("values") or [])
            fab_pool = _fab_lot_pool(product)
            fab_rebuilt = len(fab_pool.get("values") or [])
        except HTTPException:
            raise
        except Exception as e:
            logger.warning("/lot-pool/refresh 재빌드 실패 (product=%s) %s: %s",
                           product, type(e).__name__, e)
    return {"ok": True, "product": product, "dropped": dropped,
            "root_lot_id_count": rebuilt, "lot_id_count": fab_rebuilt}


class RootLotRamCacheEvictReq(BaseModel):
    source_path: str = ""
    root_lot_id: str = ""


@router.post("/root-lot-cache/evict")
def evict_root_lot_ram_cache_entry(req: RootLotRamCacheEvictReq, _perm=Depends(require_page_manager("splittable"))):
    """관리자: 개별 root lot 캐시 항목 제거."""
    return _ml_table_lookup.evict_root_ram_cache_entry(source_path=req.source_path, root_lot_id=req.root_lot_id)


# ── 통합 수동 스캔 (FAB + product + root lot 일괄) ──────────────────────
