_PLAN_POST_SAVE_LAST_THREAD: threading.Thread | None = None

_PIVOT_BUILD_LOCK = threading.Lock()
_PIVOT_BUILD_INPROGRESS: set[str] = set()
_PIVOT_BUILD_LAST: dict[str, float] = {}
_PIVOT_BUILD_COOLDOWN_SEC = 300.0
_SEARCH_CACHE_MAINT_LOCK = threading.Lock()
_SEARCH_CACHE_MAINT_THREAD: threading.Thread | None = None
_SEARCH_CACHE_MAINT_WAKE = threading.Event()
_AUTO_PRODUCT_CACHE_STATE_LOCK = threading.RLock()
_AUTO_PRODUCT_CACHE_STATE: dict = {
    "started": False,
    "current_product": "",
    "queued_product": "",
    "queued_task_id": "",
    "next_product": "",
    "next_at": "",
    "next_at_ts": 0.0,
    "last_product": "",
    "last_at": "",
    "last_ok": None,
    "job_id": "",
    "cycle_first_product": "",
    "cycle_completed_products": 0,
    "last_duration_sec": 0.0,
    "delayed": False,
    "delayed_reason": "",
    "loaded": False,
}
_AUTO_PRODUCT_CACHE_RETRY_SEC = 15.0
_AUTO_PRODUCT_CACHE_DEFAULT_INTERVAL_MINUTES = 15


def _auto_product_cache_iso(epoch_seconds: float) -> str:
    return datetime.datetime.fromtimestamp(
        float(epoch_seconds), tz=datetime.timezone.utc
    ).astimezone().isoformat(timespec="seconds")


def _pivot_cache_path(product: str, root_lot_id: str) -> Path:
    # Resolve under the *active* base root (db_root) rather than a value frozen
    # at import time. In production `_base_root()/cache/split_table` is identical
    # to the builder's CACHE_DIR (db_cache_dir == db_root/cache); resolving it
    # live keeps reader/writer consistent if the DB root is re-pointed at runtime
    # (admin_settings takes effect without a restart) and lets tests that patch
    # `_base_root` sandbox the pivot cache instead of reading the global one.
    from app_v2.modules.splittable.cache_builder import canonical_product_dir
    canonical = canonical_product_dir(product) or str(product or "").strip()
    safe_root = str(root_lot_id).replace("/", "_").replace("\\", "_")
    return _base_root() / "cache" / "split_table" / canonical / f"{safe_root}.parquet"


def _pivot_cache_knob_path(product: str, root_lot_id: str) -> Path:
    """KNOB 전용 사이드카 경로 (하위 폴더 — lot-candidates 의 *.parquet 열거와 분리)."""
    from app_v2.modules.splittable.cache_builder import KNOB_SIDECAR_DIR
    main = _pivot_cache_path(product, root_lot_id)
    return main.parent / KNOB_SIDECAR_DIR / main.name


def _knob_only_request(prefix: str, custom_name: str, custom_cols: str) -> bool:
    """이 검색이 KNOB prefix '만' 보는가 (사이드카 사용 조건)."""
    if str(custom_name or "").strip() or str(custom_cols or "").strip():
        return False
    parts = [p.strip().upper() for p in str(prefix or "").split(",") if p.strip()]
    return parts == ["KNOB"]


def _merge_all_columns(frame_cols: list, full_cols: list | None, lot_col: str, wf_col: str) -> list:
    """FE 컬럼 선택기용 전체 컬럼 목록.

    KNOB 사이드카로 읽으면 프레임에는 KNOB 컬럼밖에 없다. 그대로 내보내면
    커스텀 세트 만들 때 INLINE/VM 등이 사라지므로, 전체 pivot 스키마를 기준으로
    복원하고 프레임에만 있는 항목(태그/관리 행 오버레이)을 뒤에 붙인다."""
    if not full_cols:
        return frame_cols
    skip = {c for c in (lot_col, wf_col) if c}
    out = [c for c in full_cols if c not in skip]
    seen = set(out)
    for c in frame_cols or []:
        if c not in seen:
            out.append(c)
            seen.add(c)
    return out


def _knob_sidecar_usable(sidecar: Path, main: Path) -> bool:
    """사이드카를 써도 결과가 같은가.

    ① 본 파일보다 오래되지 않았고 ② 렌더에 필요한 앵커 컬럼(lot/wafer/fab)이
    모두 들어 있어야 한다. 하나라도 어긋나면 전체 파일로 폴백한다 — 사이드카가
    결과를 바꾸는 경로를 만들지 않는다."""
    try:
        if sidecar.stat().st_mtime_ns < main.stat().st_mtime_ns:
            return False
    except OSError:
        return False
    names = _cached_scan_schema(sidecar)[1]
    if not names:
        return False
    lower = {str(n).lower() for n in names}
    if not any(n in lower for n in ("wafer_id", "waferid", "wafer")):
        return False
    return any(n in lower for n in ("root_lot_id", "lot_id"))


def _pivot_cache_build_state(product: str) -> str:
    canonical = _canonical_mltable_product_name(product, allow_bare=True) or str(product or "").strip().upper()
    with _PIVOT_BUILD_LOCK:
        if canonical in _PIVOT_BUILD_INPROGRESS:
            return "building"
        if _PIVOT_BUILD_LAST.get(canonical):
            return "built"
    return ""


def _match_progress(done: int, total: int, *, state: str = "running") -> dict:
    """전체 랏 진행률 집계용 표준 블록 (매칭 캐시). 수집기가 없으면 빈 dict."""
    try:
        from core.cache_event_log import progress_detail
        return progress_detail("match", done, total, state=state)
    except Exception:
        return {}


def _stage(kind: str, phase: str) -> dict:
    """제품별 캐시 이력 집계용 수명주기 표식. 수집기가 없으면 빈 dict."""
    try:
        from core.cache_event_log import stage_detail
        return stage_detail(kind, phase)
    except Exception:
        return {}


def _cache_build_emit(product: str, event: str, *, ok: bool = True, detail: dict | None = None) -> None:
    """백그라운드 캐시 빌드(pivot·WIP latest-lot) 진행을 캐시 이벤트 로그로 내보낸다.

    **`logger.info` 는 화면에 안 보인다** — 캐시관리 화면은 `cache_event_log.record`
    만 읽는다. 수동 캐싱은 pivot 빌드를 큐에 넣고 "큐 등록 완료"만 남겼고, 실제
    빌드/실패/lease 스킵은 서버 터미널에만 찍혀 "캐시를 지웠는데 이벤트 로그에
    아무것도 안 뜬다"가 됐다 (FAB 랏인덱스는 `_fab_idx_emit` 로 이미 해결한 것과
    같은 문제). 같은 category(`cache_op`)라 '전체' 필터에 실시간으로 섞여 보인다.
    """
    try:
        from core.cache_event_log import record as _rec
        _rec("cache_op", event, ok=ok, detail=detail or {}, product=product)
    except Exception:
        pass


def _enqueue_pivot_cache_build(product: str, reason: str = "", *, immediate: bool = False,
                               local_only: bool = False) -> bool:
    """Rebuild the product's pre-pivoted root_lot cache in a daemon thread.
    Single-flight per product with a cooldown so view-triggered rebuilds cannot
    stampede; the daily 03:00 scheduler remains the full sweep.

    local_only=True 면 개발 워커로 오프로드하지 않고 이 서버에서 직접 빌드한다
    (관리자가 이 서버에서 누른 수동 캐싱)."""
    canonical = _canonical_mltable_product_name(product, allow_bare=True) or str(product or "").strip().upper()
    if not canonical:
        return False
    now = time.time()
    with _PIVOT_BUILD_LOCK:
        if canonical in _PIVOT_BUILD_INPROGRESS:
            # 검색이 유발하는 스킵은 빌드 1건당 수십 번 발생하므로 로그에 남기지
            # 않는다. 관리자가 직접 요청한 경우(immediate)만 "왜 안 도는지" 를 남긴다.
            if immediate:
                _cache_build_emit(canonical, "[Pivot캐시] 이미 빌드 중 — 이번 요청은 건너뜁니다",
                            detail={"reason": reason})
            return False
        if (not immediate and
                now - _PIVOT_BUILD_LAST.get(canonical, 0.0) < _PIVOT_BUILD_COOLDOWN_SEC):
            return False
        _PIVOT_BUILD_INPROGRESS.add(canonical)

    try:
        source_fp = _product_path(canonical)
    except HTTPException:
        source_fp = None

    # 중단 신호(scan_gate)는 **스레드 로컬**이다 — 아래 데몬 스레드에서 인자 없이
    # cancel_requested() 를 부르면 항상 False 라, 관리자가 "중단" 을 눌러도 빌드가
    # 끝까지 돌았다. 호출한 스레드(스캔 큐 작업)에서 지금 id 를 붙잡아 빌더까지
    # 명시적으로 넘긴다. 큐 작업 밖에서 시작된 빌드는 id 가 없어 종전대로 무중단.
    try:
        from core import scan_gate as _scan_gate_capture
        cancel_task_id = _scan_gate_capture.current_task_id()
    except Exception:
        cancel_task_id = ""

    def _run():
        ok = False
        started_ts = time.time()
        _cache_build_emit(canonical, f"[Pivot캐시] 빌드 시작 — {reason or '요청'}",
                    detail={"reason": reason, "immediate": immediate})

        def _local_build() -> dict:
            # v9.1.x: 공유 flow-data lease — 개발/운영 서버가 같은 product 를 동시에
            # 빌드하지 않게 한다. lease 실패 = 다른 서버가 빌드 중 → 건너뛰고
            # cooldown 후 재시도 (그 사이 빌드 완료본을 그대로 사용).
            lease_name = f"splittable_pivot_{canonical}"
            lease_held = False
            try:
                from core import shared_lease as _shared_lease
                lease_held = _shared_lease.try_acquire(lease_name, ttl_sec=1800.0)
                if not lease_held:
                    holder = _shared_lease.holder(lease_name)
                    logger.info(f"pivot cache build skipped for {canonical} — 다른 서버가 빌드 중 (holder={holder})")
                    # lease 는 30분 TTL 이라 죽은 홀더가 남으면 그 시간 동안 빌드가
                    # 조용히 아무것도 안 한다 — 반드시 화면에서 보여야 한다.
                    _cache_build_emit(canonical,
                                f"[Pivot캐시] 빌드 건너뜀 — 다른 서버가 빌드 중 (holder={holder or '?'})",
                                ok=False, detail={"reason": reason, "lease": lease_name,
                                                  "stage": _stage("pivot", "fail")})
                    return {"ok": False}
                from app_v2.modules.splittable.cache_builder import build_pivoted_cache_for_product
                # 사용자 검색이 진행 중이면 빌드 시작을 미룬다 — 검색 collect 와 전역
                # polars 풀을 두고 경쟁하지 않도록 (빌더 내부 배치 사이 yield 와 별개로
                # 시작 시점 자체를 한 번 양보).
                from core import request_priority as _rp
                _rp.yield_to_users(max_wait_sec=60.0)

                from core import scan_gate as _shared_cancel

                def _should_cancel() -> bool:
                    if not cancel_task_id:
                        return False
                    try:
                        return bool(_shared_cancel.cancel_requested(cancel_task_id))
                    except Exception:
                        return False

                def _renew_lease() -> None:
                    # try_acquire 의 TTL 은 30 분인데 빌드는 그보다 오래 걸릴 수
                    # 있다. 갱신하지 않으면 lease 가 stale 이 되어 다른 서버가
                    # 같은 제품을 동시에 빌드한다.
                    if lease_held:
                        _shared_lease.renew(lease_name, ttl_sec=1800.0)

                with _HEAVY_BUILD_SEMAPHORE:
                    return {"ok": bool(build_pivoted_cache_for_product(
                        canonical, product_path=source_fp,
                        should_cancel=_should_cancel, on_chunk_done=_renew_lease))}
            except Exception as exc:
                logger.warning(f"pivot cache build failed for {canonical} ({reason}): {exc}")
                _cache_build_emit(canonical, f"[Pivot캐시] 빌드 실패 — {exc}", ok=False,
                            detail={"reason": reason, "error": str(exc),
                                    "stage": _stage("pivot", "fail")})
                return {"ok": False}
            finally:
                if lease_held:
                    try:
                        from core import shared_lease as _shared_lease
                        _shared_lease.release(lease_name)
                    except Exception:
                        pass

        try:
            if local_only:
                # 관리자가 이 서버에서 요청한 수동 캐싱 — 워커로 넘기지 않는다.
                # 오프로드된 빌드는 중단 신호가 워커까지 전파되지 않아 "중단을
                # 눌러도 안 멈춘다" 로 보인다. 로컬 실행은 should_cancel 이
                # 그대로 배선되므로 중단이 즉시 먹는다.
                from core import worker_dispatch as _wd
                res = _wd._run_local_heavy(
                    "splittable_pivot_build", f"pivot:{canonical}",
                    _local_build, product=canonical)
            else:
                # v9.4.x: 개발서버(워커) 생존 시 빌드를 워커로 오프로드 — 결과는 공유
                # cache/split_table 파일로 돌아온다. 워커 다운/타임아웃/원격 실패면
                # 위 로컬 경로로 자동 폴백 (core.worker_dispatch.run_heavy).
                from core import worker_dispatch as _wd
                res = _wd.run_heavy(
                    "splittable_pivot_build",
                    {"product": canonical, "product_path": str(source_fp) if source_fp else ""},
                    _local_build,
                    label=f"pivot:{canonical}",
                    local_idle_only=not immediate,
                    local_fallback=True,
                    durable=not immediate,
                    priority="normal" if immediate else "maintenance",
                    dedupe_key=f"pivot:{canonical}",
                    timeout_sec=6 * 3600.0,
                )
            ok = bool((res or {}).get("ok"))
        except Exception as exc:
            # run_heavy 자체가 터지면 예전에는 스레드만 조용히 죽어 화면에
            # 아무 흔적도 남지 않았다.
            logger.warning(f"pivot cache build dispatch failed for {canonical} ({reason}): {exc}")
            _cache_build_emit(canonical, f"[Pivot캐시] 빌드 실패 — {exc}", ok=False,
                        detail={"reason": reason, "error": str(exc),
                                "stage": _stage("pivot", "fail")})
        finally:
            with _PIVOT_BUILD_LOCK:
                _PIVOT_BUILD_INPROGRESS.discard(canonical)
                _PIVOT_BUILD_LAST[canonical] = time.time()
        elapsed = round(time.time() - started_ts, 1)
        if ok:
            _cache_build_emit(canonical, f"[Pivot캐시] 빌드 완료 — {elapsed}s",
                        detail={"reason": reason, "elapsed_sec": elapsed,
                                "stage": _stage("pivot", "done")})
            # 새 pivot 파일은 view payload cache 의존 시그니처에 잡히지 않으므로
            # 빌드 완료 시점에 명시적으로 비운다.
            _clear_split_view_cache_product(canonical)
        elif cancel_task_id and _scan_cancel_requested_for(cancel_task_id):
            # 관리자 중단은 실패가 아니다 — "생성된 캐시 없음" 으로 뭉뚱그리면
            # 중단을 눌렀는데 빌드가 깨진 것처럼 읽힌다. 이미 쓴 root 는 그대로
            # 서빙되고 남은 root 는 다음 빌드가 이어받는다.
            _cache_build_emit(canonical,
                        f"[Pivot캐시] 빌드 중단됨 — 관리자 요청 ({elapsed}s) · "
                        "이미 만들어진 캐시는 유지되고 남은 랏은 다음 빌드가 이어받습니다",
                        ok=False, detail={"reason": reason, "elapsed_sec": elapsed,
                                          "cancelled": True,
                                          "stage": _stage("pivot", "fail")})
        else:
            # 실패 사유는 위에서 이미 남겼을 수 있지만(lease/예외), 빌더가 조용히
            # False 만 돌려준 경우에도 반드시 한 줄은 남는다.
            _cache_build_emit(canonical, f"[Pivot캐시] 빌드 종료 — 생성된 캐시 없음 ({elapsed}s)",
                        ok=False, detail={"reason": reason, "elapsed_sec": elapsed,
                                          "stage": _stage("pivot", "fail")})

    threading.Thread(target=_run, daemon=True, name=f"splittable-pivot-{canonical}").start()
    logger.info(f"pivot cache build queued for {canonical} ({reason})")
    return True


def _pivot_cache_needs_build(product: str, source_fp: Path) -> bool:
    """Cheap completeness check; the worker performs the expensive fingerprint."""
    try:
        out_dir = _pivot_cache_path(product, "__probe__").parent
        from app_v2.modules.splittable.cache_builder import completed_cache_matches
        if completed_cache_matches(out_dir, source_fp):
            return False
        fingerprint_fp = out_dir / ".root_fingerprints.json"
        if not fingerprint_fp.is_file():
            return True
        if source_fp.stat().st_mtime > fingerprint_fp.stat().st_mtime:
            return True
        meta = json.loads(fingerprint_fp.read_text(encoding="utf-8"))
        if meta.get("complete") is False:
            return True
        expected = {
            str(value or "").strip()
            for value in (
                _ml_table_lookup.read_candidate_index(source_fp).get("root_lot_ids") or []
            )
            if str(value or "").strip()
        }
        if not expected:
            return False
        built = {str(value or "").strip() for value in (meta.get("roots") or {})}
        if not expected.issubset(built):
            return True
        return sum(1 for _path in out_dir.glob("*.parquet")) < len(expected)
    except Exception:
        return True


def _auto_product_cache_role_key() -> str:
    return "dev" if _ml_table_lookup._root_ram_cache_use_dev() else "prod"


def _auto_product_cache_enabled() -> bool:
    """자동 제품 순환 캐싱 사용 여부. **기본은 꺼짐**(2026-08-04).

    예전 기본값은 켜짐이었다 — 반입 직후 아무도 켜지 않았는데 전 제품 순환이
    돌기 시작해, 관리자가 상황을 파악하기도 전에 무거운 빌드가 서버를 물고
    있었다. 자동 캐싱은 관리자가 톱니바퀴에서 명시적으로 켜는 기능으로 둔다.
    """
    raw = os.environ.get("FLOW_SPLITTABLE_AUTO_PRODUCT_CACHE_ENABLED")
    if raw is not None and str(raw).strip() != "":
        return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}
    try:
        from core import cache_settings
        return cache_settings.get_bool_role(
            "auto_product_cache_enabled",
            _ml_table_lookup._root_ram_cache_use_dev(),
            False,
        )
    except Exception:
        return False


def _auto_product_cache_interval_minutes() -> int:
    raw = os.environ.get("FLOW_SPLITTABLE_AUTO_PRODUCT_CACHE_INTERVAL_MINUTES")
    if raw is not None and str(raw).strip() != "":
        try:
            return int(max(1, min(1440, float(raw))))
        except Exception:
            pass
    try:
        from core import cache_settings
        value = cache_settings.get_int_role(
            "auto_product_cache_interval_minutes",
            _ml_table_lookup._root_ram_cache_use_dev(),
            _AUTO_PRODUCT_CACHE_DEFAULT_INTERVAL_MINUTES,
        )
        return int(max(1, min(1440, int(value))))
    except Exception:
        return _AUTO_PRODUCT_CACHE_DEFAULT_INTERVAL_MINUTES


def _auto_product_cache_state_path() -> Path:
    return PLAN_DIR / "auto_product_cache_schedule.json"


def _auto_product_cache_load_cursor() -> None:
    with _AUTO_PRODUCT_CACHE_STATE_LOCK:
        if _AUTO_PRODUCT_CACHE_STATE.get("loaded"):
            return
        _AUTO_PRODUCT_CACHE_STATE["loaded"] = True
    try:
        saved = load_json(_auto_product_cache_state_path(), {})
        role_state = saved.get(_auto_product_cache_role_key()) or {}
    except Exception:
        role_state = {}
    with _AUTO_PRODUCT_CACHE_STATE_LOCK:
        _AUTO_PRODUCT_CACHE_STATE["last_product"] = str(role_state.get("last_product") or "")
        _AUTO_PRODUCT_CACHE_STATE["last_at"] = str(role_state.get("last_at") or "")
        _AUTO_PRODUCT_CACHE_STATE["last_ok"] = role_state.get("last_ok")


def _auto_product_cache_save_cursor() -> None:
    try:
        saved = load_json(_auto_product_cache_state_path(), {})
        if not isinstance(saved, dict):
            saved = {}
        with _AUTO_PRODUCT_CACHE_STATE_LOCK:
            saved[_auto_product_cache_role_key()] = {
                "last_product": _AUTO_PRODUCT_CACHE_STATE.get("last_product") or "",
                "last_at": _AUTO_PRODUCT_CACHE_STATE.get("last_at") or "",
                "last_ok": _AUTO_PRODUCT_CACHE_STATE.get("last_ok"),
            }
        save_json(_auto_product_cache_state_path(), saved)
    except Exception:
        logger.debug("auto product cache cursor save failed", exc_info=True)


def _next_auto_cache_product(products: list[str], last_product: str = "") -> str:
    order = list(dict.fromkeys(str(value or "").strip() for value in products if str(value or "").strip()))
    if not order:
        return ""
    last = str(last_product or "").strip().casefold()
    for index, product in enumerate(order):
        if product.casefold() == last:
            return order[(index + 1) % len(order)]
    return order[0]


def _auto_product_cache_set_next(delay_sec: float, *, delayed: bool = False,
                                 reason: str = "") -> None:
    when = time.time() + max(0.0, float(delay_sec))
    products = _match_cache_products("")
    with _AUTO_PRODUCT_CACHE_STATE_LOCK:
        last = _AUTO_PRODUCT_CACHE_STATE.get("last_product") or ""
        _AUTO_PRODUCT_CACHE_STATE.update({
            "next_product": _next_auto_cache_product(products, last),
            "next_at_ts": when,
            "next_at": _auto_product_cache_iso(when),
            "delayed": bool(delayed),
            "delayed_reason": str(reason or ""),
        })


def _auto_product_cache_on_started(product: str, job_id: str) -> None:
    now = time.time()
    next_product = _next_auto_cache_product(_match_cache_products(""), product)
    with _AUTO_PRODUCT_CACHE_STATE_LOCK:
        # The configured interval is the pause after a complete catalog sweep,
        # not a gap between products.  While a product is active, estimate the
        # next product from the latest observed whole-product duration.
        estimate = max(
            60.0,
            float(_AUTO_PRODUCT_CACHE_STATE.get("last_duration_sec") or 0.0)
            or _auto_product_cache_interval_minutes() * 60.0,
        )
        next_at = now + estimate
        _AUTO_PRODUCT_CACHE_STATE.update({
            "current_product": product,
            "current_started_ts": now,
            "queued_product": "",
            "queued_task_id": "",
            "next_product": next_product,
            # Earliest prediction while a product is running.  The status
            # snapshot keeps moving this floor forward if the current stage
            # runs longer, so the UI never advertises a time already passed.
            "next_at": _auto_product_cache_iso(next_at),
            "next_at_ts": next_at,
            "job_id": job_id,
            "delayed": False,
            "delayed_reason": "",
        })
    _SEARCH_CACHE_MAINT_WAKE.set()


def _auto_product_cache_advance_after_product(product: str, *, ok: bool) -> None:
    """Advance inside one continuous catalog sweep.

    Products inside a sweep start back-to-back.  The configured interval is
    applied only after the cursor wraps to the sweep's first product.
    """
    now = time.time()
    products = _match_cache_products("")
    next_product = _next_auto_cache_product(products, product)
    with _AUTO_PRODUCT_CACHE_STATE_LOCK:
        first = str(_AUTO_PRODUCT_CACHE_STATE.get("cycle_first_product") or product)
        started = float(_AUTO_PRODUCT_CACHE_STATE.get("current_started_ts") or 0.0)
        duration = max(0.0, now - started) if started else 0.0
        completed = int(_AUTO_PRODUCT_CACHE_STATE.get("cycle_completed_products") or 0) + 1
        wrapped = (
            not next_product
            or next_product.casefold() == first.casefold()
            or len(products) <= 1
            or completed >= max(1, len(products))
        )
        delay = _auto_product_cache_interval_minutes() * 60.0 if wrapped else 0.0
        _AUTO_PRODUCT_CACHE_STATE.update({
            "current_product": "",
            "current_started_ts": 0.0,
            "queued_product": "",
            "queued_task_id": "",
            "last_product": product,
            "last_at": _auto_product_cache_iso(now),
            "last_ok": bool(ok),
            "job_id": "",
            "next_product": next_product,
            "next_at_ts": now + delay,
            "next_at": _auto_product_cache_iso(now + delay),
            "cycle_first_product": "" if wrapped else first,
            "cycle_completed_products": 0 if wrapped else completed,
            "last_duration_sec": duration or float(
                _AUTO_PRODUCT_CACHE_STATE.get("last_duration_sec") or 0.0),
            "delayed": False,
            "delayed_reason": "",
        })
    _auto_product_cache_save_cursor()
    _SEARCH_CACHE_MAINT_WAKE.set()


def _auto_product_cache_on_finished(product: str, result: dict) -> None:
    _auto_product_cache_advance_after_product(
        product, ok=bool((result or {}).get("ok")))


def _auto_product_cache_on_cancelled(product: str, task_id: str = "") -> None:
    """Keep the rotation cursor in sync when its pending queue item is removed.

    A cancelled automatic item must not remain as the UI's current/next item and
    must not remain stuck in the UI. Treat it as a visited cursor entry and
    continue the same catalog sweep with the following product; the configured
    interval is used only after the sweep wraps.
    """
    product = str(product or "").strip()
    task_id = str(task_id or "").strip()
    with _AUTO_PRODUCT_CACHE_STATE_LOCK:
        queued_id = str(_AUTO_PRODUCT_CACHE_STATE.get("queued_task_id") or "")
        queued_product = str(_AUTO_PRODUCT_CACHE_STATE.get("queued_product") or "")
        if task_id and queued_id and task_id != queued_id:
            return
        if product and queued_product and product != queued_product:
            return
        target = product or queued_product
        _AUTO_PRODUCT_CACHE_STATE.update({
            "queued_product": "",
            "queued_task_id": "",
        })
    _auto_product_cache_advance_after_product(target, ok=False)


def _auto_product_cache_schedule_snapshot() -> dict:
    _auto_product_cache_load_cursor()
    enabled = _auto_product_cache_enabled()
    interval = _auto_product_cache_interval_minutes()
    with _AUTO_PRODUCT_CACHE_STATE_LOCK:
        state = dict(_AUTO_PRODUCT_CACHE_STATE)
    queued_task_id = str(state.get("queued_task_id") or "")
    if queued_task_id:
        gate = _scan_gate_snapshot()
        gate_ids = {
            str(row.get("id") or "")
            for row in [gate.get("current"), *(gate.get("pending") or [])]
            if isinstance(row, dict)
        }
        # scan_gate가 5분 무진행 pending을 자동 제거했으면 순환 상태도 함께
        # 비운다. 그렇지 않으면 화면과 스케줄러만 영원히 queued로 남는다.
        if queued_task_id not in gate_ids:
            _auto_product_cache_on_cancelled(
                str(state.get("queued_product") or ""), queued_task_id,
            )
            with _AUTO_PRODUCT_CACHE_STATE_LOCK:
                state = dict(_AUTO_PRODUCT_CACHE_STATE)
    active_product = str(state.get("current_product") or state.get("queued_product") or "")
    if enabled and active_product:
        # Products in the same sweep are back-to-back.  Predict the next start
        # from the last whole-product duration and keep moving an expired
        # estimate forward while the current four-stage pipeline is still live.
        now = time.time()
        estimate = max(
            60.0,
            float(state.get("last_duration_sec") or 0.0) or interval * 60.0,
        )
        started = float(state.get("current_started_ts") or 0.0)
        floor_ts = (started + estimate) if started else (now + estimate)
        if floor_ts <= now:
            floor_ts = now + 60.0
        with _AUTO_PRODUCT_CACHE_STATE_LOCK:
            _AUTO_PRODUCT_CACHE_STATE["next_product"] = _next_auto_cache_product(
                _match_cache_products(""), active_product)
            if float(_AUTO_PRODUCT_CACHE_STATE.get("next_at_ts") or 0.0) < floor_ts:
                _AUTO_PRODUCT_CACHE_STATE["next_at_ts"] = floor_ts
                _AUTO_PRODUCT_CACHE_STATE["next_at"] = _auto_product_cache_iso(floor_ts)
            state = dict(_AUTO_PRODUCT_CACHE_STATE)
    if enabled and not state.get("current_product") and not state.get("queued_product") and not state.get("next_product"):
        _auto_product_cache_set_next(0.0)
        with _AUTO_PRODUCT_CACHE_STATE_LOCK:
            state = dict(_AUTO_PRODUCT_CACHE_STATE)
    return {
        "key": "product_rotation",
        "label": "제품별 필수 캐시 순환",
        "started": bool(_SEARCH_CACHE_MAINT_THREAD and _SEARCH_CACHE_MAINT_THREAD.is_alive()),
        "enabled": enabled,
        "interval_minutes": interval,
        "current_product": str(state.get("current_product") or ""),
        "queued_product": str(state.get("queued_product") or ""),
        "queued_task_id": str(state.get("queued_task_id") or ""),
        "next_product": str(state.get("next_product") or ""),
        "next_at": str(state.get("next_at") or ""),
        "last_product": str(state.get("last_product") or ""),
        "last_at": str(state.get("last_at") or ""),
        "last_ok": state.get("last_ok"),
        "job_id": str(state.get("job_id") or ""),
        "cycle_active": bool(state.get("cycle_first_product")),
        "cycle_first_product": str(state.get("cycle_first_product") or ""),
        "cycle_completed_products": int(state.get("cycle_completed_products") or 0),
        "cycle_total_products": len(_match_cache_products("")),
        "next_after_current": bool(active_product),
        "delayed": bool(state.get("delayed")),
        "delayed_reason": str(state.get("delayed_reason") or ""),
        "serial_policy": "one_product_one_cache_kind_per_server",
    }


def _split_search_cache_maintenance_loop() -> None:
    """Run one product's required cache pipeline at a time in rotation order."""
    _auto_product_cache_load_cursor()
    try:
        first_delay = float(os.environ.get("FLOW_SPLITTABLE_SEARCH_CACHE_FIRST_DELAY_SEC", "") or 20.0)
    except Exception:
        first_delay = 20.0
    _auto_product_cache_set_next(max(0.0, min(600.0, first_delay)))
    while True:
        _SEARCH_CACHE_MAINT_WAKE.clear()
        try:
            from core.background_owner import is_owner
            if not is_owner():
                _SEARCH_CACHE_MAINT_WAKE.wait(5.0)
                continue
        except Exception:
            _SEARCH_CACHE_MAINT_WAKE.wait(5.0)
            continue
        if not _auto_product_cache_enabled():
            with _AUTO_PRODUCT_CACHE_STATE_LOCK:
                _AUTO_PRODUCT_CACHE_STATE.update({
                    "next_product": "", "next_at": "", "next_at_ts": 0.0,
                    "delayed": False, "delayed_reason": "",
                })
            _SEARCH_CACHE_MAINT_WAKE.wait(30.0)
            continue
        with _AUTO_PRODUCT_CACHE_STATE_LOCK:
            state = dict(_AUTO_PRODUCT_CACHE_STATE)
        if state.get("current_product") or state.get("queued_product"):
            _SEARCH_CACHE_MAINT_WAKE.wait(5.0)
            continue
        if not state.get("next_product"):
            _auto_product_cache_set_next(0.0)
            with _AUTO_PRODUCT_CACHE_STATE_LOCK:
                state = dict(_AUTO_PRODUCT_CACHE_STATE)
        next_at_ts = float(state.get("next_at_ts") or 0.0)
        now = time.time()
        if next_at_ts > now:
            _SEARCH_CACHE_MAINT_WAKE.wait(min(30.0, next_at_ts - now))
            continue
        product = str(state.get("next_product") or "")
        if not product:
            _auto_product_cache_set_next(60.0, delayed=True, reason="제품 목록 없음")
            _SEARCH_CACHE_MAINT_WAKE.wait(30.0)
            continue
        # 예약 시각이 오면 다른 작업이 실행 중이어도 실제 공용 FIFO 큐에 넣는다.
        # 예전처럼 메모리 상태로만 15초씩 미루면 화면의 "다음 작업"과 실제 큐가
        # 서로 달랐고, 취소할 task id도 없었다.
        with _AUTO_PRODUCT_CACHE_STATE_LOCK:
            following = _next_auto_cache_product(_match_cache_products(""), product)
            now = time.time()
            estimate = max(
                60.0,
                float(_AUTO_PRODUCT_CACHE_STATE.get("last_duration_sec") or 0.0)
                or _auto_product_cache_interval_minutes() * 60.0,
            )
            if not _AUTO_PRODUCT_CACHE_STATE.get("cycle_first_product"):
                _AUTO_PRODUCT_CACHE_STATE["cycle_first_product"] = product
                _AUTO_PRODUCT_CACHE_STATE["cycle_completed_products"] = 0
            _AUTO_PRODUCT_CACHE_STATE.update({
                "queued_product": product,
                "queued_task_id": "",
                "next_product": following,
                "next_at": _auto_product_cache_iso(now + estimate),
                "next_at_ts": now + estimate,
                "delayed": False,
                "delayed_reason": "",
            })
        out = _submit_product_cache_scan(
            product,
            force=False,
            source="scheduler",
            on_started=_auto_product_cache_on_started,
            on_finished=_auto_product_cache_on_finished,
        )
        accepted = bool(out.get("accepted"))
        if accepted:
            # 바로 시작한 경우 on_started 가 먼저 queued_product 를 비울 수 있다.
            # 그때 완료된 callback 상태 위에 오래된 task id를 다시 덮지 않는다.
            with _AUTO_PRODUCT_CACHE_STATE_LOCK:
                if (_AUTO_PRODUCT_CACHE_STATE.get("queued_product") == product
                        and not _AUTO_PRODUCT_CACHE_STATE.get("current_product")):
                    _AUTO_PRODUCT_CACHE_STATE["queued_task_id"] = str(out.get("id") or "")
        if not accepted:
            with _AUTO_PRODUCT_CACHE_STATE_LOCK:
                _AUTO_PRODUCT_CACHE_STATE.update({
                    "queued_product": "", "queued_task_id": "",
                })
            _auto_product_cache_set_next(
                _AUTO_PRODUCT_CACHE_RETRY_SEC,
                delayed=True,
                reason=str(out.get("detail") or "캐시 실행 큐 대기"),
            )
        _SEARCH_CACHE_MAINT_WAKE.wait(5.0)


def start_split_search_cache_maintainer() -> bool:
    """Start the single product-rotation cache scheduler for this server."""
    global _SEARCH_CACHE_MAINT_THREAD
    with _SEARCH_CACHE_MAINT_LOCK:
        if _SEARCH_CACHE_MAINT_THREAD is not None and _SEARCH_CACHE_MAINT_THREAD.is_alive():
            return False
        _SEARCH_CACHE_MAINT_THREAD = threading.Thread(
            target=_split_search_cache_maintenance_loop,
            daemon=True,
            name="splittable-search-cache-maintainer",
        )
        _SEARCH_CACHE_MAINT_THREAD.start()
    with _AUTO_PRODUCT_CACHE_STATE_LOCK:
        _AUTO_PRODUCT_CACHE_STATE["started"] = True
    return True


try:
    _ml_table_lookup.register_build_complete_hook(
        lambda fp: _enqueue_pivot_cache_build(Path(fp).stem, reason="lookup_complete")
    )
except Exception:
    logger.debug("lookup-complete pivot hook registration failed", exc_info=True)


# ── Per-root FAB latest-lot index (additive fast layer for the fab join) ──────
# Profiling (5000-root / 20M-row FAB sandbox) pinned the dominant SplitTable
# cost to a single collect() in the fab override join: picking the latest FAB
# lot per (root_lot_id, wafer_id) scanned the WHOLE FAB source on every search.
# Two things defeat parquet pruning there: the source is not partitioned by root,
# and the root filter is wrapped in _join_key_expr (cast+upper), so predicate
# pushdown cannot use row-group stats. That collect was ~2.5s and grew with FAB
# size — it fired even on pivot-cache hits whenever the lot_progress projection
# cache did not cover the searched root.
#
# This layer precomputes, in the background, the global FAB source re-partitioned
# by a normalized root key. A search reads only the one root's partition (a few
# hundred rows) and the EXISTING align/scope/latest-pick/join logic then runs
# unchanged on that tiny frame. Purely additive: on any miss / staleness / error
# it returns None and the caller falls back to the original full-scan path while
# a (re)build is scheduled. Does not touch the SWR/signature/scan_root_lot_cache
# paths. Measured per-root read: ~13–40ms vs ~2130ms full-scan (~50–160x).
_FAB_IDX_ROOT_COL = "__fab_idx_root"
_FAB_IDX_META_FILE = "_meta.json"
_FAB_IDX_BUILD_LOCK = threading.Lock()


def _heavy_build_concurrency() -> int:
    # 운영 계약: 한 서버에서 캐시 산출은 한 제품의 한 종류만 실행한다. 배포
    # 환경에 과거 튜닝값이 남아 있어도 이 경로만 병렬로 빠져나가지 않게 고정한다.
    return 1


# 무거운 백그라운드 빌드(제품 pivot, fab index)는 전역 직렬화한다 — 스윕/뷰가
# 여러 product 를 동시에 enqueue 해도 빌드 피크(각 수백 MB)가 겹쳐 쌓이지
# 않게 한다. 배치/청크 단위 메모리 가드와 함께 12~20GB 호스트에서 총 상한을
# "빌드 1개 피크" 로 유지하는 장치.
_HEAVY_BUILD_SEMAPHORE = threading.Semaphore(_heavy_build_concurrency())
_FAB_IDX_BUILD_INPROGRESS: set[str] = set()
_FAB_IDX_BUILD_LAST: dict[str, float] = {}
_FAB_IDX_BUILD_COOLDOWN_SEC = 120.0
# central revalidator (startup service) — keeps every built index in line with
# the FAB sources without any staleness work on the search hot path
_FAB_IDX_SWEEP_THREAD_LOCK = threading.Lock()
_FAB_IDX_SWEEP_THREAD: threading.Thread | None = None
_FAB_IDX_SWEEP_WAKE = threading.Event()
_FAB_IDX_SWEEP_FIRST_DELAY_SEC = 10.0


def _fab_lot_index_enabled() -> bool:
    return _env_bool("FLOW_SPLITTABLE_FAB_LOT_INDEX", True)


def _fab_lot_index_dir(product: str) -> Path:
    canonical = _canonical_mltable_product_name(product, allow_bare=True) or str(product or "").strip()
    return _base_root() / "cache" / "fab_lot_index" / canonical


def _is_fab_lot_index_staging_product(product: str) -> bool:
    """True when a `<product>.build` staging path was mistaken for a product."""
    raw = str(product or "").strip().rstrip("/\\")
    return bool(raw) and raw.casefold().endswith(".build")


def _fab_lot_index_meta_path(product: str) -> Path:
    return _fab_lot_index_dir(product) / _FAB_IDX_META_FILE


def _fab_source_signature(fab_source: str, include_all: bool) -> list:
    """(path, mtime, size) for every FAB source file — the index staleness key."""
    sig: list = []
    seen_targets: set[str] = set()
    for source in _global_fab_source_paths(fab_source, include_all=include_all):
        # Keep signature discovery identical to the scanner. Directly joining
        # db_base/source loses case-insensitive matches on Linux and misses a
        # usable alias/source mounted under base_root.
        base, _resolved_source = _resolve_fab_source_target(source)
        if base is None:
            continue
        target_key = _canon_file_key(base)
        if target_key in seen_targets:
            continue
        seen_targets.add(target_key)
        try:
            if base.is_file():
                st = base.stat()
                sig.append([str(base), int(st.st_mtime), int(st.st_size)])
                continue
            for p in sorted(base.rglob("*")):
                if p.is_file() and p.suffix.lower() in (".parquet", ".csv"):
                    st = p.stat()
                    sig.append([str(p), int(st.st_mtime), int(st.st_size)])
        except Exception:
            continue
    return sig


def _fab_lot_index_read_meta(product: str) -> dict:
    try:
        return load_json(_fab_lot_index_meta_path(product), {}) or {}
    except Exception:
        return {}


def _fab_lot_index_partition_dir(product: str, root_lot_id: str) -> Path | None:
    root = str(root_lot_id or "").strip().upper()
    if not root:
        return None
    part = _fab_lot_index_dir(product) / f"{_FAB_IDX_ROOT_COL}={root}"
    return part if part.is_dir() else None


def _fab_lot_index_sweep_interval_sec() -> float:
    try:
        value = float(os.environ.get("FLOW_SPLITTABLE_FAB_LOT_INDEX_SWEEP_SEC", "") or 60.0)
    except Exception:
        value = 60.0
    return max(10.0, min(3600.0, value))


def _fab_lot_index_sweep_once() -> None:
    """Compare every built index against the live FAB source signature and
    enqueue rebuilds on drift. One signature walk is shared by all products
    that resolve to the same (fab_source, include_all) source set."""
    base = _base_root() / "cache" / "fab_lot_index"
    products: set[str] = set()
    try:
        # `<product>.build` 는 중단 시 이어받기용 임시 산출물이다. 제품으로
        # 다시 넣으면 다음 staging이 `.BUILD.BUILD`로 계속 증식한다.
        products.update(
            p.name for p in base.iterdir()
            if p.is_dir() and not _is_fab_lot_index_staging_product(p.name)
        )
    except Exception:
        pass
    # A sweep that only visits existing index directories can never create the
    # first index. Include configured products so a fresh deployment prepares
    # every root before the first operator searches it.
    try:
        cfg = load_json(SOURCE_CFG, {}) or {}
        products.update(
            _canonical_mltable_product_name(value, allow_bare=True)
            or str(value or "").strip().upper()
            for value in (cfg.get("enabled") or [])
            if str(value or "").strip()
        )
    except Exception:
        pass
    products.discard("")
    if not products:
        return
    include_all = _foreground_global_fab_scan_enabled()
    sig_memo: dict[tuple, list] = {}
    for product in sorted(products):
        try:
            ml_product, _ov, fab_source = _current_fab_override(product)
            if not ml_product:
                continue
            key = (fab_source, include_all)
            if key not in sig_memo:
                sig_memo[key] = _fab_source_signature(fab_source, include_all)
            meta = _fab_lot_index_read_meta(product)
            if not meta or meta.get("source_sig") != sig_memo[key]:
                _enqueue_fab_lot_index_build(
                    product, fab_source, include_all=include_all,
                    reason="sweep_missing_meta" if not meta else "sweep_stale",
                )
        except Exception:
            logger.debug("fab_lot_index sweep failed product=%s", product, exc_info=True)


def _fab_lot_index_sweep_loop() -> None:
    _FAB_IDX_SWEEP_WAKE.wait(_FAB_IDX_SWEEP_FIRST_DELAY_SEC)
    while True:
        _FAB_IDX_SWEEP_WAKE.clear()
        try:
            if _fab_lot_index_enabled():
                _fab_lot_index_sweep_once()
        except Exception:
            logger.debug("fab_lot_index sweep tick failed", exc_info=True)
        _FAB_IDX_SWEEP_WAKE.wait(_fab_lot_index_sweep_interval_sec())


def start_fab_lot_index_revalidator() -> bool:
    """Retired: FAB refresh is owned by the product-rotation scheduler.

    Keeping an independent FAB timer would allow a second cache kind/product to
    enter the queue while the scheduled product pipeline is running.  The sweep
    helper remains callable for diagnostics and tests, but startup no longer
    creates a per-cache scheduler thread.
    """
    logger.info("fab_lot_index independent scheduler retired; product rotation owns refresh")
    return False


def notify_fab_sources_changed(reason: str = "") -> None:
    """Move the next product-rotation check forward after FAB source ingest."""
    logger.info("fab sources changed (%s) — product cache rotation waked", reason or "-")
    with _AUTO_PRODUCT_CACHE_STATE_LOCK:
        if not (_AUTO_PRODUCT_CACHE_STATE.get("current_product") or
                _AUTO_PRODUCT_CACHE_STATE.get("queued_product")):
            _AUTO_PRODUCT_CACHE_STATE["next_at_ts"] = time.time()
            _AUTO_PRODUCT_CACHE_STATE["next_at"] = _auto_product_cache_iso(time.time())
    _SEARCH_CACHE_MAINT_WAKE.set()


def _fab_lot_index_scan_root(product: str, root_lot_id: str,
                             fab_source: str = "", include_all: bool = False):
    """Return a LazyFrame of the FAB source rows for one root (schema identical to
    _scan_global_fab_sources), or None to signal fallback to the full scan.
    Serve-immediately: freshness is maintained by the central revalidator sweep,
    so the search hot path does no staleness work at all."""
    if not _fab_lot_index_enabled():
        return None
    root = str(root_lot_id or "").strip()
    if not root:
        return None
    try:
        part = _fab_lot_index_partition_dir(product, root)
        if part is None:
            return None
        files = sorted(part.glob("*.parquet"))
        if not files:
            return None
        lf = _scan_parquet_compat([str(p) for p in files])
        names = lf.collect_schema().names()
        if _FAB_IDX_ROOT_COL in names:
            lf = lf.drop(_FAB_IDX_ROOT_COL)
        return _cast_cats_lazy(lf)
    except Exception:
        logger.debug("fab_lot_index scan failed product=%s root=%s", product, root, exc_info=True)
        return None


def _fab_source_sig_delta(old_sig, new_sig) -> set[str] | None:
    """ADDED file keys between two source signatures, or None when any file was
    removed/rewritten (→ 전체 재빌드 필요)."""
    try:
        old_map = {_canon_file_key(p): (int(m), int(s)) for p, m, s in (old_sig or [])}
        new_map = {_canon_file_key(p): (int(m), int(s)) for p, m, s in (new_sig or [])}
    except Exception:
        return None
    if not old_map:
        return None
    for key, sig in old_map.items():
        if new_map.get(key) != sig:
            return None
    return {k for k in new_map if k not in old_map}


def _build_fab_lot_index(product: str, fab_source: str, include_all: bool) -> bool:
    """Build a per-root FAB lot index: the latest FAB row per (root, wafer),
    partitioned by a normalized root key.

    Storing the *reduced* latest-per-(root,wafer) frame (rather than every raw
    FAB row) keeps the index tiny — a few rows per root instead of thousands —
    which makes the build I/O an order of magnitude cheaper (shorter cold window
    after a data refresh) and per-root reads near-instant. The reduction is by
    (root, wafer) keyed on the FAB timestamp, so it is equivalent to the fab
    join's own latest-pick for any join key ⊆ {root_lot_id, wafer_id} (the
    default identity join): reducing by (root,wafer)-latest and then re-picking
    latest per join key yields the same rows because the timestamp order is
    preserved. The downstream sort+unique in _scan_product still runs and stays
    correct on the tiny frame.

    FAB 원천은 보통 새 date 파티션 파일이 추가되는 append 형이다 — 기존 파일이
    그대로고 파일만 늘었으면 새 파일만 스캔해 기존 인덱스와 (root,wafer)-latest
    로 병합하고 영향받은 root 파티션만 교체한다(수 초). 파일이 지워졌거나
    재기록됐으면 전체 재빌드로 폴백한다."""
    if _is_fab_lot_index_staging_product(product):
        logger.warning("fab_lot_index staging path rejected as product: %s", product)
        return False
    canonical = _canonical_mltable_product_name(product, allow_bare=True) or str(product or "").strip()
    if not canonical:
        return False

    job_id = ""
    try:
        from core.cache_event_log import start_job, stage_started
        job_id = start_job(
            "fab_index_background",
            f"FAB latest 인덱스 백그라운드 갱신 ({canonical})",
            [("fab_index", "FAB latest 인덱스")],
            product=canonical
        )
        stage_started(job_id, "fab_index")
    except Exception:
        pass

    try:
        live_sig = _fab_source_signature(fab_source, include_all)
        old_meta = _fab_lot_index_read_meta(canonical)
        if old_meta.get("source_sig") and _fab_lot_index_dir(canonical).is_dir():
            added = _fab_source_sig_delta(old_meta.get("source_sig"), live_sig)
            if added is not None:
                if not added:
                    # 파일 변화 없음 — 인덱스가 이미 최신. 이것도 알려야 한다.
                    # 아니면 스캔 큐에 뜬 작업이 "아무 것도 안 하고 끝난" 것처럼 보인다.
                    _fab_idx_emit(canonical, "[FAB랏인덱스] 최신 — FAB 원천 변화 없음(건너뜀)",
                                  detail={"stage": _stage("fab_index", "skip")})
                    return True
                if _build_fab_lot_index_incremental(
                        canonical, fab_source, include_all, added, live_sig, old_meta):
                    return True
                logger.info("fab_lot_index incremental 실패 — 전체 재빌드 (product=%s)", canonical)
        return _build_fab_lot_index_full(canonical, fab_source, include_all)
    finally:
        if job_id:
            try:
                from core.cache_event_log import stage_finished, finish_job
                stage_finished(job_id, "fab_index")
                finish_job(job_id)
            except Exception:
                pass

def _fab_latest_reduce_lf(fab_lf):
    """(reduced_lf, root_col) — 정규화 root 키 부여 + (root,wafer)-latest 축소.

    Reduce to the latest row per (root, wafer). Keep every FAB column so the
    read path is a drop-in replacement for _scan_global_fab_sources output.
    이미 _FAB_IDX_ROOT_COL 이 있는 프레임(파티션/병합 재축소)에도 그대로 동작."""
    try:
        names = fab_lf.collect_schema().names()
    except Exception:
        return None, ""
    root_col = _ci_resolve_in("root_lot_id", names) or _pick_first_present_ci(("root_lot_id",), names)
    if not root_col:
        return None, ""
    wf_col = _ci_resolve_in("wafer_id", names) or _pick_first_present_ci(("wafer_id", "wafer"), names)
    ts_col = _pick_ts_col(names)
    lf = (
        fab_lf
        .with_columns(_join_key_expr(root_col).alias(_FAB_IDX_ROOT_COL))
        .filter(pl.col(_FAB_IDX_ROOT_COL).is_not_null() & (pl.col(_FAB_IDX_ROOT_COL) != ""))
    )
    reduce_subset = [_FAB_IDX_ROOT_COL] + ([wf_col] if wf_col else [])
    try:
        if ts_col and ts_col in names:
            lf = lf.sort(ts_col, descending=True, nulls_last=True).unique(
                subset=reduce_subset, keep="first", maintain_order=True)
        else:
            lf = lf.unique(subset=reduce_subset, keep="last")
    except Exception:
        # If reduction is not expressible, fall back to storing raw per-root rows
        # (still correct — the fab join reduces at read time).
        logger.debug("fab_lot_index reduction skipped", exc_info=True)
    return lf, root_col


def _scan_cancel_requested() -> bool:
    """이 스레드가 실행 중인 스캔 큐 작업에 중단 요청이 왔는가.

    긴 루프의 **안전한 지점**(제품 경계·배치 경계 — 이미 메모리 가드나 사용자
    양보를 확인하는 자리)에서만 확인한다. 중간에서 끊으면 parquet 파티션이
    깨지므로 절대 강제 종료하지 않는다.
    """
    try:
        from core import scan_gate
        return bool(scan_gate.cancel_requested())
    except Exception:
        return False


def _scan_cancel_requested_for(task_id: str) -> bool:
    """**다른 스레드**에서 실행 중인 큐 작업의 중단 여부.

    인자 없는 `_scan_cancel_requested()` 는 스레드 로컬이라 데몬 빌드 스레드에서
    부르면 항상 False 다. 작업을 띄운 스레드에서 붙잡아 둔 id 로 물어볼 때 쓴다.
    """
    if not str(task_id or "").strip():
        return False
    try:
        from core import scan_gate
        return bool(scan_gate.cancel_requested(str(task_id)))
    except Exception:
        return False


def _fab_idx_log_gap_sec() -> float:
    return _float_env_clamped("FLOW_SPLITTABLE_FAB_INDEX_LOG_GAP_SEC", 1.5, 0.0, 60.0)


def _fab_idx_emit(product: str, event: str, *, ok: bool = True, detail: dict | None = None) -> None:
    """FAB 랏 인덱스 빌드 진행을 캐시 이벤트 로그로 내보낸다.

    **`logger.info` 는 화면에 안 보인다** — 캐시관리 화면(수동 스캔/스캔 큐)은
    `cache_event_log.record` 만 읽는다. 그래서 이 빌드는 스캔 큐에 이름만 뜨고
    "몇 분째 뭘 하는지" 가 전혀 안 보였다(매칭 캐시는 이미 배치별로 내보내고
    있었다). 같은 category(`cache_op`)라 '전체' 필터에 실시간으로 섞여 보인다.
    """
    try:
        from core.cache_event_log import record as _rec
        _rec("cache_op", event, ok=ok, detail=detail or {}, product=product)
    except Exception:
        pass


def _fab_idx_progress(done: int, total: int, *, state: str = "running") -> dict:
    """Standard progress payload used by the cache dashboard (batch X/Y)."""
    from core.cache_event_log import progress_detail
    return progress_detail("fab_index", done, total, state=state, unit="배치")


def _fab_index_build_batch_rows() -> int:
    # 수 GB FAB도 약 50만 행씩 축소해 총 원천 크기와 무관하게 피크를 낮춘다.
    # 캐시는 느려도 괜찮고 안정성이 우선이므로 기존 100만 행보다 보수적으로 시작.
    try:
        rows = float(os.environ.get("FLOW_SPLITTABLE_FAB_INDEX_BUILD_BATCH_ROWS", "") or 500_000)
    except Exception:
        rows = 500_000.0
    return int(max(100_000.0, min(100_000_000.0, rows)))


def _parquet_row_count(path: str) -> int | None:
    """Row count from the parquet footer only (no data read)."""
    try:
        if not str(path).lower().endswith(".parquet"):
            return None
        return int(pl.scan_parquet(str(path)).select(pl.len()).collect().item(0, 0))
    except Exception:
        return None


def _write_fab_index_partitions(product: str, lf) -> None:
    idx_dir = _fab_lot_index_dir(product)
    tmp_dir = idx_dir.with_name(idx_dir.name + ".tmp")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        sink_target = pl.PartitionBy(
            tmp_dir, key=_FAB_IDX_ROOT_COL, include_key=True,
            approximate_bytes_per_file="auto",
        )
        lf.sink_parquet(sink_target, mkdir=True, maintain_order=False)
    except Exception:
        # Older polars / sink edge cases — fall back to an eager partitioned write.
        df = lf.collect()
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
        tmp_dir.mkdir(parents=True, exist_ok=True)
        if df.height:
            df.write_parquet(tmp_dir, partition_by=_FAB_IDX_ROOT_COL)
    # 디렉터리 전체를 지웠다가 바꾸면 그 짧은 틈에 모든 검색이 full FAB scan으로
    # 떨어진다. 완성된 root 파티션만 하나씩 교체하고 meta는 호출측이 마지막에
    # publish한다. 빌드 중에는 기존 인덱스가 계속 읽힌다.
    idx_dir.mkdir(parents=True, exist_ok=True)
    new_names = {p.name for p in tmp_dir.iterdir() if p.is_dir()}
    for old in list(idx_dir.iterdir()):
        if old.is_dir() and old.name.startswith(f"{_FAB_IDX_ROOT_COL}=") and old.name not in new_names:
            shutil.rmtree(old, ignore_errors=True)
    for child in list(tmp_dir.iterdir()):
        target = idx_dir / child.name
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink()
        child.replace(target)
    shutil.rmtree(tmp_dir, ignore_errors=True)


def _build_fab_lot_index_full(product: str, fab_source: str, include_all: bool) -> bool:
    """전체 재빌드 — FAB 파일을 row 수 기준 배치로 나눠 배치별로 latest
    축소 후 병합한다. (root,wafer)-latest 는 결합법칙이 성립하므로 결과는 단일
    패스 전역 sort 와 동일하고(동률 ts 는 앞선 파일 우선 — stable sort 와 일치),
    피크 메모리가 FAB 전체 크기가 아니라 배치 크기에 비례한다. row 수는
    parquet footer 만 읽어 세므로(데이터 미접근) 배치 산정은 저렴하고, 축소
    결과는 root×wafer 로 상한되는 작은 프레임이라 누적 병합 비용도 미미하다."""
    live_sig = _fab_source_signature(fab_source, include_all)
    batch_rows = _fab_index_build_batch_rows()
    batches: list[list[str] | None] = []
    cur: list[str] = []
    cur_rows = 0
    for path, _mtime, _size in live_sig:
        n = _parquet_row_count(path)
        cur.append(path)
        # row 수를 모르는 파일(CSV 등)은 보수적으로 배치를 끊는다.
        cur_rows += n if n is not None else batch_rows
        if cur_rows >= batch_rows:
            batches.append(cur)
            cur, cur_rows = [], 0
    if cur:
        batches.append(cur)
    if not batches:
        # 시그니처가 파일을 못 찾는 엣지 소스 — 필터 없는 전체 스캔 1배치.
        batches = [None]

    total_batches = len(batches)
    build_sig = hashlib.sha256(json.dumps(
        {"source_sig": live_sig, "batch_rows": batch_rows, "total": total_batches},
        ensure_ascii=False, sort_keys=True, default=str,
    ).encode("utf-8")).hexdigest()
    idx_parent = _fab_lot_index_meta_path(product).parent
    staging = idx_parent.with_name(idx_parent.name + ".build")
    manifest_fp = staging / "_resume.json"
    manifest = load_json(manifest_fp, {}) if manifest_fp.is_file() else {}
    if str(manifest.get("build_sig") or "") != build_sig:
        shutil.rmtree(staging, ignore_errors=True)
        manifest = {}
    staging.mkdir(parents=True, exist_ok=True)
    completed = {
        int(v) for v in (manifest.get("completed") or [])
        if str(v).isdigit() and (staging / f"{int(v):06d}.parquet").is_file()
    }
    used_all: list[str] = [str(v) for v in (manifest.get("used") or [])]
    root_col_meta = str(manifest.get("root_col") or "")
    reduced_rows = int(manifest.get("reduced_rows") or 0)
    batch_failures: list[str] = []
    started = time.time()
    last_log = 0.0
    _fab_idx_emit(
        product,
        f"[FAB랏인덱스] 전체 재빌드 시작 — FAB 파일 {len(live_sig):,}개 · "
        f"배치 {total_batches:,}개(배치당 최대 {batch_rows:,}행) · "
        f"이어받기 {len(completed):,}개 · RSS {_proc_rss_gb()}GB",
        detail={
            "stage": _stage("fab_index", "start"),
            "progress": _fab_idx_progress(len(completed), total_batches),
        },
    )
    for bi, batch in enumerate(batches, 1):
        part_fp = staging / f"{bi:06d}.parquet"
        if bi in completed and part_fp.is_file():
            continue
        # 배치 경계 = 안전한 취소 지점. 완료 배치는 staging에 남겨 다음 실행에서
        # 이어받고, 공개 인덱스는 meta publish 전까지 건드리지 않는다.
        if _scan_cancel_requested():
            _fab_idx_emit(product,
                          f"[FAB랏인덱스] 중단됨 — 배치 {bi - 1:,}/{total_batches:,} 에서 접습니다. "
                          f"완료 배치는 보존해 다음 스캔에서 이어받습니다.",
                          ok=False,
                          detail={"progress": _fab_idx_progress(
                              bi - 1, total_batches, state="cancelled")})
            return False
        only = {_canon_file_key(p) for p in batch} if batch is not None else None
        fab_lf, used = _scan_global_fab_sources(fab_source, include_all=include_all,
                                                only_files=only)
        if fab_lf is None:
            batch_failures.append(f"batch {bi}: source scan returned no frame")
            continue
        reduced, root_col = _fab_latest_reduce_lf(fab_lf)
        if reduced is None:
            try:
                cols = fab_lf.collect_schema().names()
            except Exception:
                cols = []
            batch_failures.append(
                f"batch {bi}: root_lot_id column missing (columns={cols[:20]})"
            )
            continue
        root_col_meta = root_col_meta or root_col
        try:
            batch_df = reduced.collect()
        except Exception as e:
            logger.warning("fab_lot_index 배치 축소 실패 (product=%s) %s: %s",
                           product, type(e).__name__, e)
            _fab_idx_emit(product,
                          f"[FAB랏인덱스] 배치 {bi}/{total_batches} 축소 실패: "
                          f"{type(e).__name__}: {e}", ok=False,
                          detail={"progress": _fab_idx_progress(
                              bi - 1, total_batches, state="failed")})
            return False
        used_all.extend(u for u in used if u not in used_all)
        tmp_part = part_fp.with_suffix(".parquet.tmp")
        batch_df.write_parquet(tmp_part)
        os.replace(tmp_part, part_fp)
        completed.add(bi)
        reduced_rows += batch_df.height
        manifest = {
            "build_sig": build_sig,
            "completed": sorted(completed),
            "used": used_all,
            "root_col": root_col_meta,
            "reduced_rows": reduced_rows,
        }
        save_json(manifest_fp, manifest)
        # 첫·마지막 배치는 항상, 나머지는 스로틀 — 배치가 많아도 로그가 화면을
        # 덮지 않으면서 "멈춘 게 아니라 진행 중"이 계속 보이게.
        now = time.time()
        if bi == 1 or bi == total_batches or (now - last_log) >= _fab_idx_log_gap_sec():
            last_log = now
            eta = ((now - started) / bi) * (total_batches - bi)
            _fab_idx_emit(
                product,
                f"[FAB랏인덱스] 배치 {bi:,}/{total_batches:,}"
                f" ({int(bi * 100 / max(1, total_batches))}%) · 누적 "
                f"{reduced_rows:,}행"
                f" · RSS {_proc_rss_gb()}GB · 남은 ~{_fmt_dur_ko(eta)}",
                detail={
                    "batch": bi,
                    "total_batches": total_batches,
                    "progress": _fab_idx_progress(bi, total_batches),
                },
            )
        batch_df = None
        if len(batches) > 1:
            # 배치 사이에 사용자 요청/메모리 회복에 양보 — 빌드가 길어도
            # interactive 검색과 RSS 안정에 영향을 주지 않게 한다.
            try:
                from core.runtime_limits import process_memory_high as _pmh
                from core import request_priority as _rp
                if _pmh():
                    time.sleep(0.5)
                _rp.yield_to_users(max_wait_sec=20.0)
            except Exception:
                pass
    part_files = [staging / f"{bi:06d}.parquet" for bi in range(1, total_batches + 1)]
    part_files = [p for p in part_files if p.is_file()]
    if not part_files or not root_col_meta:
        sources = _global_fab_source_paths(fab_source, include_all=include_all)
        reason = batch_failures[-1] if batch_failures else "FAB data files not found"
        _fab_idx_emit(
            product,
            "[FAB랏인덱스] 전체 재빌드 중단 — 읽을 수 있는 FAB 행이 없습니다"
            f" · {reason}",
            ok=False,
            detail={
                "fab_source": fab_source,
                "resolved_sources": sources[:20],
                "source_file_count": len(live_sig),
                "batch_failures": batch_failures[-20:],
                "stage": _stage("fab_index", "fail"),
                "progress": _fab_idx_progress(
                    len(completed), total_batches, state="failed"),
            },
        )
        return False
    _fab_idx_emit(product,
                  f"[FAB랏인덱스] root 파티션 기록 중 — 배치 축소 {reduced_rows:,}행")
    combined = pl.concat([pl.scan_parquet(str(p)) for p in part_files], how="diagonal_relaxed")
    final_lf, _ = _fab_latest_reduce_lf(combined)
    if final_lf is None:
        return False
    _write_fab_index_partitions(product, final_lf)
    meta = {
        "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "sources": used_all,
        "root_col": root_col_meta,
        "source_sig": live_sig,
    }
    try:
        save_json(_fab_lot_index_meta_path(product), meta)
    except Exception:
        logger.debug("fab_lot_index meta write failed product=%s", product, exc_info=True)
    shutil.rmtree(staging, ignore_errors=True)
    _fab_idx_emit(product,
                  f"[FAB랏인덱스] 전체 재빌드 완료 — 배치 축소 {reduced_rows:,}행 · "
                  f"총 {_fmt_dur_ko(time.time() - started)} · RSS {_proc_rss_gb()}GB",
                  detail={
                      "stage": _stage("fab_index", "done"),
                      "progress": _fab_idx_progress(
                          total_batches, total_batches, state="done"),
                  })
    return True


def _build_fab_lot_index_incremental(product: str, fab_source: str, include_all: bool,
                                     added_files: set[str], live_sig: list,
                                     old_meta: dict) -> bool:
    """Merge newly added FAB files into the existing index; rewrite only the
    affected root partitions. False → caller falls back to the full rebuild.

    타이 규칙: 같은 (root,wafer) 에 동일 timestamp 행이 기존 인덱스와 새 파일
    양쪽에 있으면 기존 행을 유지한다 — 전체 재빌드의 stable sort 에서 경로
    정렬상 앞서는(=기존) 파일이 이기는 것과 일치한다."""
    started = time.time()
    _fab_idx_emit(product,
                  f"[FAB랏인덱스] 증분 갱신 시작 — 새 FAB 파일 {len(added_files):,}개")
    try:
        idx_dir = _fab_lot_index_dir(product)
        fab_lf, used_sources = _scan_global_fab_sources(
            fab_source, include_all=include_all, only_files=added_files)
        if fab_lf is None:
            # 추가 파일이 이 product 의 소스 범위 밖(다른 소스 폴더)일 수 있다 —
            # 인덱스 내용 불변이므로 시그니처만 갱신한다.
            meta = dict(old_meta)
            meta["built_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            meta["source_sig"] = live_sig
            save_json(_fab_lot_index_meta_path(product), meta)
            return True
        new_lf, root_col = _fab_latest_reduce_lf(fab_lf)
        if new_lf is None:
            return False
        new_df = new_lf.collect()
        roots = sorted({str(v) for v in new_df[_FAB_IDX_ROOT_COL].to_list() if str(v or "").strip()})
        if not roots:
            meta = dict(old_meta)
            meta["built_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            meta["source_sig"] = live_sig
            save_json(_fab_lot_index_meta_path(product), meta)
            return True
        # 리터럴 디렉터리명으로 안전하게 교체 가능한 root 만 증분 처리한다
        # (특수문자는 hive 인코딩과 어긋날 수 있음 → 전체 재빌드).
        if any(not _re.fullmatch(r"[A-Z0-9_\-.]+", r) for r in roots):
            return False
        # 새 파일이 기존 root 대부분을 건드리면 per-root 병합(파티션별 읽기+
        # 교체)이 한 번의 전체 sort 보다 비싸다 — 실측상 30% 를 넘으면 전체
        # 재빌드가 더 빠르므로 폴백한다.
        try:
            existing = sum(1 for p in idx_dir.iterdir() if p.is_dir())
        except Exception:
            existing = 0
        if existing and len(roots) > max(16, int(existing * 0.3)):
            logger.info("fab_lot_index incremental 포기 — 영향 root %d/%d (product=%s)",
                        len(roots), existing, product)
            _fab_idx_emit(product,
                          f"[FAB랏인덱스] 증분 포기 — 새 파일이 root {len(roots):,}/{existing:,}개를 "
                          f"건드려 전체 재빌드가 더 빠릅니다")
            return False

        frames = []
        for root in roots:
            part = idx_dir / f"{_FAB_IDX_ROOT_COL}={root}"
            if part.is_dir():
                files = sorted(part.glob("*.parquet"))
                if files:
                    frames.append(_scan_parquet_compat([str(p) for p in files]))
        frames.append(new_df.lazy())  # 기존 인덱스 행이 앞 — 타이에서 기존 우선
        merged_input = pl.concat(frames, how="diagonal_relaxed") if len(frames) > 1 else frames[0]
        merged, _ = _fab_latest_reduce_lf(merged_input)
        if merged is None:
            return False

        staging = idx_dir.with_name(idx_dir.name + ".delta")
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        try:
            try:
                sink_target = pl.PartitionBy(
                    staging, key=_FAB_IDX_ROOT_COL, include_key=True,
                    approximate_bytes_per_file="auto",
                )
                merged.sink_parquet(sink_target, mkdir=True, maintain_order=False)
            except Exception:
                merged_df = merged.collect()
                shutil.rmtree(staging, ignore_errors=True)
                staging.mkdir(parents=True, exist_ok=True)
                if merged_df.height:
                    merged_df.write_parquet(staging, partition_by=_FAB_IDX_ROOT_COL)
            written = {p.name for p in staging.iterdir() if p.is_dir()}
            expected = {f"{_FAB_IDX_ROOT_COL}={r}" for r in roots}
            if written != expected:
                return False
            for child in sorted(staging.iterdir()):
                if not child.is_dir():
                    continue
                target = idx_dir / child.name
                if target.exists():
                    shutil.rmtree(target, ignore_errors=True)
                child.replace(target)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

        sources = list(dict.fromkeys(list(old_meta.get("sources") or []) + list(used_sources)))
        meta = {
            "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "sources": sources,
            "root_col": old_meta.get("root_col") or root_col,
            "source_sig": live_sig,
        }
        save_json(_fab_lot_index_meta_path(product), meta)
        logger.info("fab_lot_index incremental merge: %d file(s) → %d root(s) (product=%s)",
                    len(added_files), len(roots), product)
        _fab_idx_emit(product,
                      f"[FAB랏인덱스] 증분 갱신 완료 — 새 파일 {len(added_files):,}개 → "
                      f"root {len(roots):,}개 교체 · 총 {_fmt_dur_ko(time.time() - started)}")
        return True
    except Exception:
        logger.debug("fab_lot_index incremental build failed product=%s", product, exc_info=True)
        _fab_idx_emit(product, "[FAB랏인덱스] 증분 갱신 실패 — 전체 재빌드로 넘어갑니다", ok=False)
        return False


def _enqueue_fab_lot_index_build(product: str, fab_source: str = "",
                                 include_all: bool = False, reason: str = "", *,
                                 immediate: bool = False,
                                 local_only: bool = False) -> bool:
    """Single-flight, cooldown-guarded background (re)build of the fab lot index.

    local_only=True 면 개발 워커로 오프로드하지 않고 이 서버에서 직접 빌드한다."""
    if not _fab_lot_index_enabled():
        return False
    if _is_fab_lot_index_staging_product(product):
        logger.warning("fab_lot_index staging product enqueue rejected: %s", product)
        return False
    canonical = _canonical_mltable_product_name(product, allow_bare=True) or str(product or "").strip().upper()
    if not canonical:
        return False
    now = time.time()
    with _FAB_IDX_BUILD_LOCK:
        if canonical in _FAB_IDX_BUILD_INPROGRESS:
            return False
        if (not immediate and
                now - _FAB_IDX_BUILD_LAST.get(canonical, 0.0) < _FAB_IDX_BUILD_COOLDOWN_SEC):
            return False
        _FAB_IDX_BUILD_INPROGRESS.add(canonical)

    def _run():
        ok = False

        def _local_build() -> dict:
            lease_name = f"splittable_fabidx_{canonical}"
            lease_held = False
            try:
                try:
                    from core import shared_lease as _shared_lease
                    lease_held = _shared_lease.try_acquire(lease_name, ttl_sec=1800.0)
                    if not lease_held:
                        logger.info(f"fab_lot_index build skipped for {canonical} — 다른 서버가 빌드 중")
                        return {"ok": False}
                except Exception:
                    lease_held = False  # lease infra optional; proceed without it
                # 사용자 검색이 진행 중이면 빌드 시작을 미룬다 (pivot 빌드와 동일한 이유).
                from core import request_priority as _rp
                _rp.yield_to_users(max_wait_sec=60.0)
                with _HEAVY_BUILD_SEMAPHORE:
                    return {"ok": bool(_build_fab_lot_index(canonical, fab_source, include_all))}
            except Exception as exc:
                logger.warning(f"fab_lot_index build failed for {canonical} ({reason}): {exc}")
                return {"ok": False}
            finally:
                if lease_held:
                    try:
                        from core import shared_lease as _shared_lease
                        _shared_lease.release(lease_name)
                    except Exception:
                        pass

        try:
            if local_only:
                # 이 서버에서 요청한 수동 캐싱 — 워커로 넘기지 않는다 (pivot 과 동일).
                res = _local_build()
            else:
                # v9.4.x: 워커 생존 시 오프로드, 아니면 로컬 폴백 (pivot 빌드와 동일).
                from core import worker_dispatch as _wd
                res = _wd.run_heavy(
                    "splittable_fab_lot_index_build",
                    {"product": canonical, "fab_source": fab_source, "include_all": include_all},
                    _local_build,
                    # 스캔 큐에 그대로 뜨는 문자열이다 — 'fabidx:PRODA' 로는
                    # 무슨 작업인지 알 수 없어 사람이 읽는 이름으로 둔다.
                    label=f"FAB 랏 인덱스 (root별 최신 FAB lot): {canonical}",
                    local_idle_only=not immediate,
                    local_fallback=bool(immediate),
                    durable=not immediate,
                    priority="normal" if immediate else "maintenance",
                    dedupe_key=f"fab_lot_index:{canonical}",
                    timeout_sec=6 * 3600.0,
                )
            ok = bool((res or {}).get("ok"))
        finally:
            with _FAB_IDX_BUILD_LOCK:
                _FAB_IDX_BUILD_INPROGRESS.discard(canonical)
                _FAB_IDX_BUILD_LAST[canonical] = time.time()
        if ok:
            # New fab labels are not captured by the view payload cache signature;
            # clear it so the next search recomputes with fresh joined lot ids.
            _clear_split_view_cache_product(canonical)

    threading.Thread(target=_run, daemon=True, name=f"splittable-fabidx-{canonical}").start()
    logger.info(f"fab_lot_index build queued for {canonical} ({reason})")
    return True


def _split_view_cache_get(key: tuple, hard_sig: tuple, soft_sig: tuple) -> tuple[str, dict | None]:
    """(freshness, payload) 반환. freshness ∈ {"miss","fresh","stale"}.

    - hard_sig 불일치(신규 lot / 사용자 편집) → ("miss", None) + 엔트리 폐기.
    - soft_sig 까지 일치 → ("fresh", payload).
    - soft_sig 만 불일치(백그라운드 파생 캐시 재기록) → ("stale", payload) —
      즉시 서빙하고 호출측이 백그라운드 재검증을 예약한다.
    """
    global _VIEW_CACHE_BYTES
    with _VIEW_CACHE_LOCK:
        cached = _VIEW_CACHE.get(key)
        if cached:
            cached_hard, cached_soft, payload, approx_bytes = cached
            if cached_hard != hard_sig:
                _VIEW_CACHE.pop(key, None)
                _VIEW_CACHE_BYTES = max(0, _VIEW_CACHE_BYTES - approx_bytes)
            else:
                _VIEW_CACHE.move_to_end(key)
                if cached_soft == soft_sig:
                    return "fresh", dict(payload)
                return "stale", dict(payload)
    # RAM LRU에서 밀려도 공유 디스크의 압축 payload를 읽어 cold parquet/FAB 계산을
    # 피한다. 디스크 엔트리도 동일 hard/soft 시그니처 계약을 사용한다.
    freshness, payload = _view_disk_cache_read(key, hard_sig, soft_sig)
    if payload is not None:
        _split_view_cache_put_memory(key, hard_sig, soft_sig, payload)
        return freshness, dict(payload)
    return "miss", None


def _split_view_cache_prepare_payload(payload: dict) -> dict:
    stored = dict(payload)
    stored.pop("related_issues", None)
    stored.pop("runtime_profile", None)
    stored.pop("view_cache", None)
    # 레거시 fat rows(`_cells`, 셀당 9키 dict ≈441B)는 캐시에 담지 않는다. HTTP 응답은
    # rows_compact(≈40B/셀)만 쓰고 fat rows 는 버려지므로, 저장하면 엔트리 하나가
    # 예산의 ~11배를 먹어 다른 사용자의 검색 결과를 계속 밀어냈다(= 여러 명이
    # 서로 다른 lot 을 검색하면 전부 cold 로 떨어지던 원인). 레거시 형태가 필요한
    # 내부 호출자는 _expand_view_rows() 로 그때 복원한다.
    if stored.get("rows_compact") is not None:
        stored.pop("rows", None)
    # v: lookup_cache 는 그대로 저장한다 — HIT 경로에서 _attach 가 매번
    # _ml_table_lookup.cache_status(meta 읽기 + partition dir glob) 를 재계산하던
    # 비용을 없앤다. 저장 시점의 배지값이 약간 stale 할 수 있으나, 캐시가 렌더된
    # 상태에서 빌드 진행 배지는 행동 가치가 없으므로 허용.
    return stored


def _split_view_cache_put_memory(key: tuple, hard_sig: tuple, soft_sig: tuple, stored: dict) -> None:
    global _VIEW_CACHE_BYTES
    approx_bytes = _estimate_view_payload_bytes(stored)
    budget = _view_cache_max_bytes()
    with _VIEW_CACHE_LOCK:
        old = _VIEW_CACHE.pop(key, None)
        if old is not None:
            _VIEW_CACHE_BYTES = max(0, _VIEW_CACHE_BYTES - old[3])
        _VIEW_CACHE[key] = (hard_sig, soft_sig, stored, approx_bytes)
        _VIEW_CACHE_BYTES += approx_bytes
        while _VIEW_CACHE and (
            len(_VIEW_CACHE) > _view_cache_max_entries() or _VIEW_CACHE_BYTES > budget
        ):
            if len(_VIEW_CACHE) == 1:
                break  # 방금 넣은 항목은 예산 초과라도 유지 (miss 반복 방지)
            _, evicted = _VIEW_CACHE.popitem(last=False)
            _VIEW_CACHE_BYTES = max(0, _VIEW_CACHE_BYTES - evicted[3])


def _split_view_cache_put(key: tuple, hard_sig: tuple, soft_sig: tuple, payload: dict) -> None:
    stored = _split_view_cache_prepare_payload(payload)
    _split_view_cache_put_memory(key, hard_sig, soft_sig, stored)
    _view_disk_cache_write(key, hard_sig, soft_sig, stored)


def _view_revalidate_cooldown_sec() -> float:
    try:
        v = float(os.environ.get("FLOW_SPLITTABLE_VIEW_REVALIDATE_COOLDOWN_SEC", "")
                  or _VIEW_REVALIDATE_COOLDOWN_SEC_DEFAULT)
    except Exception:
        v = _VIEW_REVALIDATE_COOLDOWN_SEC_DEFAULT
    return max(0.0, min(86400.0, v))


def _view_revalidate_delay_sec() -> float:
    try:
        v = float(os.environ.get("FLOW_SPLITTABLE_VIEW_REVALIDATE_DELAY_SEC", "")
                  or _VIEW_REVALIDATE_DELAY_SEC_DEFAULT)
    except Exception:
        v = _VIEW_REVALIDATE_DELAY_SEC_DEFAULT
    return max(0.0, min(600.0, v))


def _view_revalidate_execute(view_cache_key: tuple, params: dict) -> dict:
    """재검증 1건을 운영 서버에서 실행해 운영 RAM/disk view 캐시를 갱신한다."""
    from core import request_priority

    # 사용자 HTTP 요청이 진행 중이면 백그라운드 재검증 시작을 미룬다. 실제 사용자
    # cold 조회는 이 대기 경로를 거치지 않고 운영 서버의 전용 cold 레인에서 즉시 돈다.
    request_priority.yield_to_users(max_wait_sec=120.0, quiet_for_sec=3.0)
    _VIEW_REVALIDATE_TLS.force = True
    try:
        view_split_core(request=None, include_related=False, **params)
    finally:
        _VIEW_REVALIDATE_TLS.force = False
    return {"ok": True, "stored": "operating"}


def _view_revalidate_worker_loop() -> None:
    """전역 재검증 워커 — 큐를 한 건씩 처리한다.

    재계산은 워커(개발서버) 생존 시 오프로드되어 이 서버의 polars 풀을 쓰지
    않고, 로컬 폴백 시에도 동시 재계산은 항상 최대 1건: 20명이 몰려도
    백그라운드가 polars 풀에서 점유하는 collect 는 하나뿐이라 사용자 검색의
    CPU 경쟁이 상수로 묶인다."""
    while True:
        _VIEW_REVALIDATE_WAKE.wait(timeout=60.0)
        while True:
            now = time.time()
            key = None
            params: dict = {}
            wait_hint = 0.0
            with _VIEW_REVALIDATE_LOCK:
                if not _VIEW_REVALIDATE_PENDING:
                    _VIEW_REVALIDATE_WAKE.clear()
                    break
                head_key, (enq_ts, head_params) = next(iter(_VIEW_REVALIDATE_PENDING.items()))
                age = now - enq_ts
                burst_quiet = now - _VIEW_REVALIDATE_LAST_ENQUEUE_TS
                # 디바운스: 새 stale 검색이 계속 들어오는 동안(burst)은 처리를 미루고,
                # 잠잠해지면 처리한다. 상시 트래픽에서도 head 가 delay 이상 기다리면
                # 진행을 보장해 워커가 굶지 않는다.
                if burst_quiet >= _VIEW_REVALIDATE_BURST_QUIET_SEC or age >= _view_revalidate_delay_sec():
                    _VIEW_REVALIDATE_PENDING.pop(head_key, None)
                    _VIEW_REVALIDATE_INFLIGHT.add(head_key)
                    key = head_key
                    params = head_params
                else:
                    wait_hint = min(5.0, max(0.5, _VIEW_REVALIDATE_BURST_QUIET_SEC - burst_quiet))
            if key is None:
                time.sleep(wait_hint)
                continue
            try:
                _view_revalidate_execute(key, params)
            except Exception as exc:
                logger.debug("view revalidate 실패 (product=%s): %s", params.get("product"), exc)
            finally:
                with _VIEW_REVALIDATE_LOCK:
                    _VIEW_REVALIDATE_INFLIGHT.discard(key)
                    _VIEW_REVALIDATE_LAST[key] = time.time()
                    if len(_VIEW_REVALIDATE_LAST) > _VIEW_REVALIDATE_LAST_MAX:
                        overflow = len(_VIEW_REVALIDATE_LAST) - _VIEW_REVALIDATE_LAST_MAX
                        for old_key in sorted(_VIEW_REVALIDATE_LAST, key=_VIEW_REVALIDATE_LAST.get)[:overflow]:
                            _VIEW_REVALIDATE_LAST.pop(old_key, None)


def _ensure_view_revalidate_worker_locked() -> None:
    global _VIEW_REVALIDATE_THREAD
    t = _VIEW_REVALIDATE_THREAD
    if t is not None and t.is_alive():
        return
    t = threading.Thread(target=_view_revalidate_worker_loop, daemon=True,
                         name="splittable-view-revalidate")
    _VIEW_REVALIDATE_THREAD = t
    t.start()


def _enqueue_view_revalidate(view_cache_key: tuple, params: dict) -> bool:
    """Stale hit 시 백그라운드에서 view payload 를 재계산해 최신 lot 라벨로 갱신.

    key 단위 병합(중복 제거) + 쿨다운(기본 3h) — lot_progress 스케줄러가 파생
    캐시를 자주 재기록해도 같은 검색을 반복 재계산하지 않는다. 사용자 요청은 이미
    stale 캐시로 즉시 응답했으므로 이 갱신은 다음 조회를 fresh 로 만드는 용도다.
    실행은 전역 단일 워커가 담당한다 (_view_revalidate_worker_loop)."""
    global _VIEW_REVALIDATE_LAST_ENQUEUE_TS
    now = time.time()
    with _VIEW_REVALIDATE_LOCK:
        if view_cache_key in _VIEW_REVALIDATE_INFLIGHT or view_cache_key in _VIEW_REVALIDATE_PENDING:
            return False
        if now - _VIEW_REVALIDATE_LAST.get(view_cache_key, 0.0) < _view_revalidate_cooldown_sec():
            return False
        _VIEW_REVALIDATE_PENDING[view_cache_key] = (now, dict(params))
        _VIEW_REVALIDATE_LAST_ENQUEUE_TS = now
        while len(_VIEW_REVALIDATE_PENDING) > _VIEW_REVALIDATE_PENDING_MAX:
            # 큐 상한 초과 — 가장 오래된 항목을 버린다. 버려진 검색은 다음 stale hit
            # 때 다시 등록되므로 유실이 아니라 지연일 뿐이다.
            _VIEW_REVALIDATE_PENDING.popitem(last=False)
        _ensure_view_revalidate_worker_locked()
    _VIEW_REVALIDATE_WAKE.set()
    return True


def _split_view_request_user(request: Request | None) -> tuple[str, str]:
    if request is None:
        return "", "user"
    try:
        me = current_user(request)
        return me.get("username") or "", me.get("role") or "user"
    except Exception:
        return "", "user"


# 비동기 감사 로그: /view 는 요청마다 감사 로그를 남기는데, jsonl_append 는 공유
# 드라이브 파일 락 + open/write 라 동시 요청을 직렬화한다. 큐에 쌓고 단일 데몬
# 스레드가 비워 요청 지연 경로에서 제거한다(감사 유실 없이).
_AUDIT_QUEUE: deque = deque()
_AUDIT_QUEUE_WAKE = threading.Event()
_AUDIT_QUEUE_MAX = 10000
_AUDIT_WORKER_STARTED = False
_AUDIT_WORKER_LOCK = threading.Lock()


def _audit_worker_loop() -> None:
    while True:
        _AUDIT_QUEUE_WAKE.wait(timeout=5.0)
        _AUDIT_QUEUE_WAKE.clear()
        while _AUDIT_QUEUE:
            try:
                username, action, detail, tab = _AUDIT_QUEUE.popleft()
            except IndexError:
                break
            try:
                _audit_user(username, action, detail=detail, tab=tab)
            except Exception:
                pass


def _ensure_audit_worker() -> None:
    global _AUDIT_WORKER_STARTED
    if _AUDIT_WORKER_STARTED:
        return
    with _AUDIT_WORKER_LOCK:
        if _AUDIT_WORKER_STARTED:
            return
        threading.Thread(target=_audit_worker_loop, daemon=True, name="splittable-audit").start()
        _AUDIT_WORKER_STARTED = True


def _audit_enqueue(username: str, action: str, detail: str = "", tab: str = "") -> None:
    if len(_AUDIT_QUEUE) >= _AUDIT_QUEUE_MAX:
        return  # 과부하 시 드롭 — 요청 지연보다 우선.
    _ensure_audit_worker()
    _AUDIT_QUEUE.append((username, action, detail, tab))
    _AUDIT_QUEUE_WAKE.set()


def _audit_split_view_search(
    request: Request | None,
    product: str,
    root_lot_id: str,
    fab_lot_id: str,
    wafer_ids: str,
    prefix: str,
) -> None:
    if request is None or not (str(root_lot_id or "").strip() or str(fab_lot_id or "").strip()):
        return
    username, _role = _split_view_request_user(request)
    if not username:
        return
    detail = (
        f"product={str(product or '').strip()} "
        f"root_lot_id={str(root_lot_id or '').strip()} "
        f"fab_lot_id={str(fab_lot_id or '').strip()} "
        f"wafer_ids={str(wafer_ids or '').strip()} "
        f"prefix={str(prefix or '').strip()}"
    )
    _audit_enqueue(username, "splittable:view_search", detail=detail, tab="splittable")


def _split_view_lookup_cache_public(status: dict | None, queued: dict | None = None) -> dict:
    return _lookup_cache_public_meta(status, queued)


def _split_view_cache_preparing_payload(
    product: str,
    root_lot_id: str,
    wafer_ids: str,
    prefix: str,
    history_mode: str,
    status: dict | None,
    queued: dict | None,
    *,
    message: str,
    started: float,
    runtime_profile: dict,
    view_cache_key: tuple,
) -> dict:
    payload = {
        "product": product,
        "lot_col": "root_lot_id",
        "wf_col": "wafer_id",
        "headers": [],
        "rows": [],
        "header_groups": [],
        "wafer_fab_list": [],
        "prefixes": _load_prefixes(),
        "root_lot_id": str(root_lot_id or "").strip(),
        "prefix": str(prefix or "").strip(),
        "history_mode": history_mode,
        "mismatch_count": 0,
        "product_cache": _product_ram_cache_response_meta(product),
        "lookup_cache": _split_view_lookup_cache_public(status, queued),
        "msg": message,
    }
    return _split_view_finish_payload(
        payload,
        started=started,
        runtime_profile=runtime_profile,
        payload_cache_hit=False,
        view_cache_key=view_cache_key,
    )


def _split_view_large_root_cache_or_defer(
    product: str,
    root_lot_id: str,
    wafer_ids: str,
    fp: Path,
    *,
    started: float,
    runtime_profile: dict,
    view_cache_key: tuple,
    prefix: str,
    history_mode: str,
    force_defer_raw_fallback: bool = False,
) -> tuple[Any | None, dict | None]:
    root = str(root_lot_id or "").strip()
    if not root or fp.suffix.lower() != ".parquet":
        return None, None
    if _product_ram_cache_entry(product):
        return None, None
    if not force_defer_raw_fallback and not _split_view_should_defer_raw_fallback(fp):
        return None, None
    status = _ml_table_lookup.cache_status(fp)
    runtime_profile["root_cache_status"] = status.get("status") or ""
    if not status.get("has_cache"):
        queued = _ml_table_lookup.enqueue_build(fp)
        runtime_profile["_lookup_cache"] = _split_view_lookup_cache_public(status, queued)
        runtime_profile["root_cache_hit"] = False
        # 수백 MB ML_TABLE을 HTTP 요청에서 직접 collect하면 동시 검색 2~3건만으로
        # 수 GB peak가 겹친다. 큰 소스는 공유 lookup 빌드를 먼저 끝내고 프런트가
        # 짧게 재시도한다. 작은 개발 fixture만 기존 raw fallback을 허용한다.
        # 여기서 바로 반환하므로 호출측 scan_ms lap 을 못 탄다 — 직접 닫는다.
        _lap(runtime_profile, "scan_ms")
        return None, _split_view_cache_preparing_payload(
            product,
            root,
            wafer_ids,
            prefix,
            history_mode,
            status,
            queued,
            message="Root lot 인덱스를 준비 중입니다. 완료되면 자동 재검색됩니다.",
            started=started,
            runtime_profile=runtime_profile,
            view_cache_key=view_cache_key,
        )
    # 소스가 갱신돼 cache 가 stale 여도, 해당 root 의 hive 파티션이 있으면 즉시
    # 서빙하고 백그라운드 재빌드만 예약한다(allow_stale). 데이터 갱신 직후마다
    # 소스 전체를 재스캔(5~10초)하던 것을 파티션 인덱스 읽기로 대체 — 이 stale
    # 구간이 SplitTable 검색이 캐시가 있어도 느리던 주원인이었다.
    lf, status = _ml_table_lookup.scan_root_lot_cache(fp, root, wafer_ids=wafer_ids, allow_stale=True, profile=runtime_profile)
    runtime_profile["root_cache_status"] = status.get("status") or ""
    runtime_profile["root_cache_hit"] = lf is not None
    runtime_profile["_lookup_cache"] = _split_view_lookup_cache_public(status, {})
    if lf is None:
        _lap(runtime_profile, "scan_ms")
        return None, _split_view_cache_preparing_payload(
            product,
            root,
            wafer_ids,
            prefix,
            history_mode,
            status,
            {},
            message="No data",
            started=started,
            runtime_profile=runtime_profile,
            view_cache_key=view_cache_key,
        )
    return _cast_cats_lazy(lf), None


def _attach_split_view_runtime_fields(
    payload: dict,
    request: Request | None,
    *,
    include_related: bool = False,
    started: float | None = None,
    runtime_profile: dict | None = None,
    payload_cache_hit: bool = False,
    view_cache_key: tuple | None = None,
    view_stale: bool = False,
) -> dict:
    out = dict(payload)
    product = out.get("product") or ""
    compact_rows = out.get("rows_compact") or out.get("rows") or []
    selected = [str(row.get("_param") or "") for row in compact_rows if isinstance(row, dict)]
    historical_s0 = _knob_s0_for_root(product, out.get("root_lot_id") or "", selected)
    # 편집용 값은 append-only 이력과 분리한다. Split 추가 시점에는 현재
    # credential/f_step.csv 의 step_id -> recipe_id를 그대로 사용해야 한다.
    out["s0_edit_by_knob"] = _knob_current_s0_for_product(product, selected)
    # First f_step arrival can race the asynchronous snapshot capture. Resolve
    # previously unknown KNOBs immediately while preserving captured history.
    out["s0_by_knob"] = {**out["s0_edit_by_knob"], **historical_s0}
    if "lookup_cache" not in out:
        status = None
        try:
            product = out.get("product") or ""
            root = str(out.get("root_lot_id") or "").strip()
            if product and root:
                fp = _product_path(product)
                if fp.suffix.lower() == ".parquet":
                    status = _ml_table_lookup.cache_status(fp)
        except Exception:
            status = None
        out["lookup_cache"] = _split_view_lookup_cache_public(status, None)
    if include_related:
        username, role = _split_view_request_user(request)
        out["related_issues"] = _related_tracker_issues(
            out.get("product") or "",
            out.get("root_lot_id") or "",
            username,
            role,
        )
    out["product_cache"] = _product_ram_cache_response_meta(out.get("product") or "")
    # 마무리 메타(lookup_cache 재조회/related_issues/product_cache) — 캐시 히트
    # 응답에서도 도는 구간이라 여기가 무거우면 "캐시 히트인데 느리다" 가 된다.
    _lap(runtime_profile, "finish_ms")
    if runtime_profile and runtime_profile.get("fab_index_queued"):
        out["background_cache"] = {
            "queued": True,
            "kind": "fab_root_index",
            "message": "FAB 랏 인덱스를 백그라운드에서 준비 중입니다. 표는 자동으로 갱신됩니다.",
        }
    if started is not None:
        out = _split_view_finish_payload(
            out,
            started=started,
            runtime_profile=runtime_profile,
            payload_cache_hit=payload_cache_hit,
            view_cache_key=view_cache_key,
            view_stale=view_stale,
        )
    return out


@router.get("/related-issues")
def related_issues_for_view(
    product: str = Query(...),
    root_lot_id: str = Query(""),
    request: Request = None,
):
    username, role = _split_view_request_user(request)
    return {
        "product": product,
        "root_lot_id": root_lot_id,
        "related_issues": _related_tracker_issues(product, root_lot_id, username, role),
    }


# ── View ──


try:
    import orjson as _orjson
except ImportError:
    _orjson = None


def _view_orjson_response(payload):
    """/view 전용 직렬화 우회. FastAPI 기본 경로는 dict 반환 시 jsonable_encoder 를
    payload 전체에 재귀 적용하는데, KNOB 처럼 행×웨이퍼 셀이 많은 응답(수만 셀)에서
    이 인코딩만 수 초가 걸린다. Response 객체를 직접 반환하면 그 경로를 건너뛴다.
    orjson 미설치·직렬화 실패 시 dict 를 그대로 돌려 기본 경로로 폴백한다.

    (응답, 직렬화 ms, 본문 바이트) 를 돌려준다 — 이 비용은 핸들러 total_ms 밖이라
    호출측이 타이밍 로그에 따로 실어야 보인다. 폴백(dict 반환) 시 바이트는 0."""
    if _orjson is None or not isinstance(payload, dict):
        return payload, 0.0, 0
    _t0 = time.perf_counter()
    try:
        body = _orjson.dumps(
            payload,
            default=str,
            option=_orjson.OPT_SERIALIZE_NUMPY | _orjson.OPT_NON_STR_KEYS,
        )
    except Exception:
        return payload, (time.perf_counter() - _t0) * 1000.0, 0
    return (
        Response(content=body, media_type="application/json"),
        (time.perf_counter() - _t0) * 1000.0,
        len(body),
    )


def _expand_view_rows(payload: dict) -> dict:
    """슬림 셀 포맷(cells_format v2) → 레거시 `_cells` 행으로 복원.

    v2 행은 actual 배열(a) + sparse plan(p) + sparse mismatch(m) + 행-상수
    플래그만 담는다. 셀마다 행-상수 플래그와 파생 가능한 key 를 반복하던 레거시
    `_cells` 는 KNOB 2000행×25웨이퍼에서 ≈10.9MB(셀당 실측 441B)였고, HTTP 응답은
    이미 v2 만 보낸다. 그래서 서버는 v2 만 만들고 캐시하며, 레거시 형태가 필요한
    내부 호출자(informs embed, 백그라운드 revalidate, knob-allocation, worker
    task)만 이 함수로 복원한다.

    셀 key 조립 규칙 `root_lot_id|wafer_keys[ci]|_param` 은 FE(My_SplitTable
    expandViewRows)와 동일한 계약이다 — 어긋나면 plan 저장 키가 틀어진다."""
    if not isinstance(payload, dict):
        return payload
    compact = payload.get("rows_compact")
    if compact is None:
        return payload
    existing = payload.get("rows")
    if isinstance(existing, list) and existing:
        return payload
    wafer_keys = payload.get("wafer_keys") or []
    n_keys = len(wafer_keys)
    root = str(payload.get("root_lot_id") or "")
    rows = []
    for r in compact:
        vals = r.get("a") or []
        plans_sparse = r.get("p") or {}
        mism = set(r.get("m") or [])
        can_plan = bool(r.get("can_plan"))
        is_tag = bool(r.get("tag"))
        tag_colors = r.get("tc") or {}
        is_mgmt = bool(r.get("mgmt"))
        param = r.get("_param")
        cells = {}
        for ci in range(len(vals)):
            key = str(ci)
            wf = wafer_keys[ci] if ci < n_keys else ci
            cells[key] = {
                "actual": vals[ci],
                "plan": plans_sparse.get(key),
                "key": f"{root}|{wf}|{param}",
                "can_plan": can_plan,
                "mismatch": ci in mism,
                "is_custom_tag": is_tag,
                "can_tag": is_tag,
                "tag_color": tag_colors.get(key) if is_tag else "",
                "is_management_row": is_mgmt,
                "can_management_edit": is_mgmt,
            }
        rows.append({"_param": param, "_display": r.get("_display"), "_cells": cells})
    out = dict(payload)
    out["rows"] = rows
    return out


@router.get("/view")
def view_split_http(product: str = Query(...), root_lot_id: str = Query(""),
                    wafer_ids: str = Query(""), prefix: str = Query("KNOB"),
                    custom_name: str = Query(""), view_mode: str = Query("all"),
                    history_mode: str = Query("all"),
                    fab_lot_id: str = Query(""),
                    custom_cols: str = Query(""),
                    include_related: bool = Query(False),
                    cache_first: bool = Query(False),
                    request: Request = None):
    # HTTP 진입점 — 서버는 슬림 셀 포맷(v2)만 만들고 캐시한다. 레거시 rows(_cells)를
    # 기대하는 내부 호출자(재검증 스레드/informs embed/knob-allocation/테스트)는
    # view_split() 래퍼가 _expand_view_rows() 로 그때 복원해 준다.
    #
    # 타이밍 기록은 직렬화까지 끝난 뒤 flush 한다 — 큰 응답에서는 직렬화 자체가
    # 무시 못 할 비용이고, 핸들러 안에서만 재면 그 시간이 통째로 사라진다.
    pending: list[dict] = []
    _VIEW_TIMING_TLS.pending = pending
    try:
        payload = view_split_core(
            product=product, root_lot_id=root_lot_id, wafer_ids=wafer_ids,
            prefix=prefix, custom_name=custom_name, view_mode=view_mode,
            history_mode=history_mode, fab_lot_id=fab_lot_id,
            custom_cols=custom_cols, include_related=include_related,
            cache_first=cache_first, request=request,
        )
        compact = payload.pop("rows_compact", None)
        if compact is not None:
            payload["rows"] = compact
            payload["cells_format"] = "v2"
        response, serialize_ms, body_bytes = _view_orjson_response(payload)
        for entry in pending:
            entry["serialize_ms"] = round(serialize_ms, 3)
            entry["response_bytes"] = body_bytes
        return response
    finally:
        _VIEW_TIMING_TLS.pending = None
        for entry in pending:
            _search_timing_log.record(entry)


def view_split(**kwargs) -> dict:
    """레거시 계약(rows = `_cells` dict) 을 유지하는 내부 호출자용 래퍼.

    HTTP 경로는 view_split_core 를 직접 쓰고 슬림 포맷 그대로 내보낸다."""
    return _expand_view_rows(view_split_core(**kwargs))


def view_split_core(product: str = Query(...), root_lot_id: str = Query(""),
               wafer_ids: str = Query(""), prefix: str = Query("KNOB"),
               custom_name: str = Query(""), view_mode: str = Query("all"),
               history_mode: str = Query("all"),
               fab_lot_id: str = Query(""),
               custom_cols: str = Query(""),
               include_related: bool = Query(False),
               cache_first: bool = Query(False),
               request: Request = None):
    # v8.8.33: custom_cols (쉼표 구분) 추가 — Save 없이 체크만 한 컬럼을 ad-hoc 으로 전달.
    # v9.0.3: 한 root_lot_id 아래 여러 fab_lot_id 가 정상이다. FAB 공정 진행 중
    #   fab_lot_id 가 바뀔 수 있으므로 앞 5자 일치 여부를 검증/경고 기준으로 쓰지 않는다.
    started = time.perf_counter()
    request_username, request_user_role = _split_view_request_user(request)
    runtime_profile = {
        "username": request_username,
        "user_role": request_user_role,
        "is_user_search": request is not None,
        "root_cache_hit": False,
        "product_cache_hit": False,
        "scan_ms": 0.0,
        "collect_ms": 0.0,
        "matrix_ms": 0.0,
        "overlay_ms": 0.0,
        "root_cache_status": "",
        "root_data_source": "",
        # 미들웨어 레인 대기(있으면). started 는 레인 통과 후에 찍히므로 이 값을
        # 더해야 사용자가 체감한 시간(wall_ms)이 된다.
        "lane_wait_ms": _request_lane_wait_ms(request),
        "cold_lane_wait_ms": 0.0,
    }
    runtime_profile[_LAP_MARK] = started
    _history_mode = (history_mode or "all").strip().lower() or "all"
    if _history_mode not in ("all", "final", "lot_all"):
        raise HTTPException(400, "history_mode must be one of: all, final, lot_all")
    cache_first_enabled = _truthy_value(cache_first)
    # 백그라운드 stale-revalidate 스레드의 재진입이면 캐시 서빙/감사로그를 건너뛰고
    # 순수 재계산 후 fresh 시그니처로 캐시를 덮어쓴다.
    force_recompute = bool(getattr(_VIEW_REVALIDATE_TLS, "force", False))
    if not force_recompute and not cache_first_enabled:
        _audit_split_view_search(request, product, root_lot_id, fab_lot_id, wafer_ids, prefix)
    _lot_warn = ""
    fp = _product_path(product)
    view_cache_key = _split_view_cache_key(
        product, root_lot_id, wafer_ids, prefix, custom_name,
        view_mode, _history_mode, fab_lot_id, custom_cols,
    )
    view_hard_sig, view_soft_sig = _split_view_cache_dep_signature(
        product, custom_name=custom_name, product_fp=fp)
    if not force_recompute:
        freshness, cached_view = _split_view_cache_get(view_cache_key, view_hard_sig, view_soft_sig)
        # 진입~여기 = 인증/감사/캐시키/의존 시그니처(공유드라이브 stat)/캐시 조회.
        # payload 캐시 히트 응답은 사실상 전부 이 구간이다.
        _lap(runtime_profile, "prelude_ms")
        if cached_view is not None:
            # 신규 lot 없음(hard 일치) → fresh/stale 모두 캐시 즉시 서빙. soft 만
            # 달라진 stale 이면 백그라운드에서 최신 lot 라벨로 재검증을 예약한다.
            if freshness == "stale":
                _enqueue_view_revalidate(view_cache_key, {
                    "product": product, "root_lot_id": root_lot_id, "wafer_ids": wafer_ids,
                    "prefix": prefix, "custom_name": custom_name, "view_mode": view_mode,
                    "history_mode": history_mode, "fab_lot_id": fab_lot_id,
                    "custom_cols": custom_cols,
                })
                _lap(runtime_profile, "prelude_ms")
            return _attach_split_view_runtime_fields(
                cached_view,
                request,
                include_related=include_related,
                started=started,
                runtime_profile=runtime_profile,
                payload_cache_hit=True,
                view_cache_key=view_cache_key,
                view_stale=(freshness == "stale"),
            )
    # force_recompute(재검증 워커) 경로는 위 lap 을 타지 않는다 — 여기서 한 번 더
    # 불러 진입 구간을 닫는다. 이미 닫혔으면 ~0 이 더해질 뿐이다.
    _lap(runtime_profile, "prelude_ms")
    if not root_lot_id.strip() and not fab_lot_id.strip():
        return _split_view_finish_payload(
            {"product": product, "lot_col": "root_lot_id", "wf_col": "wafer_id",
             "headers": [], "rows": [], "prefixes": _load_prefixes(),
             "product_cache": _product_ram_cache_response_meta(product),
             "msg": "Enter a Root Lot ID or Fab Lot ID to view"},
            started=started,
            runtime_profile=runtime_profile,
            payload_cache_hit=False,
            view_cache_key=view_cache_key,
        )
    # 같은 cold key가 동시에 들어오면 한 요청만 계산하고 나머지는 결과를 기다린다.
    # 수백 MB 원본/파티션 scan이 중복 실행되는 메모리 스파이크와 CPU 경합을 방지한다.
    compute_owner, compute_event = _view_compute_begin(view_cache_key)
    if not compute_owner:
        wait_started = time.perf_counter()
        completed = compute_event.wait(timeout=_view_compute_wait_seconds())
        runtime_profile["singleflight_wait_ms"] = (time.perf_counter() - wait_started) * 1000.0
        # 대기는 singleflight_wait_ms 에 이미 잡혔다 — 단계 합계에서 빼기 위해 버린다.
        _lap(runtime_profile, None)
        view_hard_sig, view_soft_sig = _split_view_cache_dep_signature(
            product, custom_name=custom_name, product_fp=fp)
        freshness, cached_view = _split_view_cache_get(view_cache_key, view_hard_sig, view_soft_sig)
        _lap(runtime_profile, "prelude_ms")
        if cached_view is not None:
            return _attach_split_view_runtime_fields(
                cached_view,
                request,
                include_related=include_related,
                started=started,
                runtime_profile=runtime_profile,
                payload_cache_hit=True,
                view_cache_key=view_cache_key,
                view_stale=(freshness == "stale"),
            )
        if completed:
            # 이전 owner가 빈 결과/오류로 끝났다면 한 요청만 다음 owner가 된다.
            compute_owner, compute_event = _view_compute_begin(view_cache_key)
        if not compute_owner:
            status = _ml_table_lookup.cache_status(fp) if root_lot_id.strip() and fp.suffix.lower() == ".parquet" else {}
            return _split_view_cache_preparing_payload(
                product,
                root_lot_id,
                wafer_ids,
                prefix,
                _history_mode,
                status,
                {"status": "running", "queued": True},
                message="같은 SplitTable 검색을 처리 중입니다. 완료되면 자동 재검색됩니다.",
                started=started,
                runtime_profile=runtime_profile,
                view_cache_key=view_cache_key,
            )
    # 여기부터가 실제 메모리/CPU 를 쓰는 cold 계산이다. 미들웨어가 아니라 이 지점에서
    # 직렬화하므로, 위에서 이미 반환된 캐시 HIT/단일비행 대기 요청은 슬롯을 기다리지
    # 않는다. 슬롯 확보 실패(레인 포화)는 429 대신 "준비 중" 페이로드로 응답해
    # FE 의 기존 자동 재조회 흐름을 그대로 태운다.
    if not _view_cold_lane_acquire(runtime_profile):
        _lap(runtime_profile, None)  # 레인 대기는 cold_lane_wait_ms 에 기록됨.
        _view_compute_finish(view_cache_key)
        return _split_view_cache_preparing_payload(
            product, root_lot_id, wafer_ids, prefix, _history_mode,
            {}, {"status": "running", "queued": True},
            message="검색 요청이 몰려 대기 중입니다. 잠시 후 자동으로 다시 조회합니다.",
            started=started,
            runtime_profile=runtime_profile,
            view_cache_key=view_cache_key,
        )
    _lap(runtime_profile, None)  # 레인 대기는 cold_lane_wait_ms 에 기록됨.
    pivot_base_lf = None
    knob_sidecar_all_columns = None
    try:
        if root_lot_id.strip() and not cache_first_enabled:
            fast_cache_path = _pivot_cache_path(product, root_lot_id.strip())
            if fast_cache_path.exists():
                try:
                    if fp and fast_cache_path.stat().st_mtime < fp.stat().st_mtime:
                        # 원본 ML_TABLE 이 pivot cache 보다 최신 — 즉시성은 유지하고
                        # 백그라운드 재빌드를 예약해 다음 조회부터 최신 데이터를 쓴다.
                        _enqueue_pivot_cache_build(product, reason="stale_pivot")
                except Exception:
                    pass
                # v9.2: native-orientation per-root cache (wafer rows × param
                # cols). Feed it straight into the normal renderer as base_lf so
                # column projection (prefix/custom) stays index-fast AND the
                # latest-lot join runs (lot_id/fab label). Legacy transposed
                # files (a "parameter" column, no wafer_id) are skipped + rebuilt.
                try:
                    cache_names = pl.scan_parquet(str(fast_cache_path)).collect_schema().names()
                except Exception:
                    cache_names = []
                is_legacy = ("parameter" in cache_names) and not any(
                    c.lower() == "wafer_id" for c in cache_names
                )
                if is_legacy:
                    _enqueue_pivot_cache_build(product, reason="legacy_pivot_format")
                elif cache_names:
                    # KNOB 만 보는 검색이면 KNOB 전용 사이드카(좁은 파일)를 읽는다.
                    # 없거나 앵커 컬럼이 빠져 있으면 그대로 전체 파일 — 결과 동일.
                    read_path = fast_cache_path
                    if _knob_only_request(prefix, custom_name, custom_cols):
                        knob_path = _pivot_cache_knob_path(product, root_lot_id.strip())
                        if _knob_sidecar_usable(knob_path, fast_cache_path):
                            read_path = knob_path
                            runtime_profile["root_data_source_detail"] = "knob_sidecar"
                            # 좁은 파일로 읽으면 프레임 스키마에 KNOB 밖 컬럼이 없다.
                            # all_columns(FE 컬럼 선택기 목록)는 전체 스키마여야 하므로
                            # 여기서 확보해 둔다 — 안 그러면 커스텀 세트에서 INLINE/VM 이 사라진다.
                            knob_sidecar_all_columns = list(cache_names)
                        else:
                            # 구버전 캐시엔 사이드카가 없다 — 다음 빌드에서 backfill.
                            _enqueue_pivot_cache_build(product, reason="knob_sidecar_missing")
                    pivot_base_lf = _cast_cats_lazy(_scan_parquet_compat(str(read_path)))
                    runtime_profile["root_cache_hit"] = True
            else:
                # pivot cache miss — 이번 요청은 아래 일반 경로로 처리하고,
                # 백그라운드에서 제품 전체 pivot cache 를 빌드해 다음 검색을 즉시화한다.
                _enqueue_pivot_cache_build(product, reason="cache_miss")
    except HTTPException:
        _view_compute_finish(view_cache_key)
        raise
    except Exception as e:
        logger.warning(f"Fast path failed: {e}")
    # pivot 캐시 탐색 = 경로 stat + 스키마 읽기 + KNOB 사이드카 판정. 넓은 pivot
    # 파일에서는 스키마 읽기만으로도 무시 못 할 시간이 든다.
    _lap(runtime_profile, "fastpath_ms")

    try:
        if pivot_base_lf is not None:
            base_lf = pivot_base_lf
            runtime_profile["root_data_source"] = "pivot_cache"
        else:
            base_lf, deferred_payload = _split_view_large_root_cache_or_defer(
                product,
                root_lot_id,
                wafer_ids,
                fp,
                started=started,
                runtime_profile=runtime_profile,
                view_cache_key=view_cache_key,
                prefix=prefix,
                history_mode=_history_mode,
                force_defer_raw_fallback=cache_first_enabled,
            )
            _lap(runtime_profile, "scan_ms")
            if deferred_payload is not None:
                return deferred_payload
        lf = _scan_product(
            product,
            root_lot_id=root_lot_id,
            fab_lot_id=fab_lot_id,
            wafer_ids=wafer_ids,
            base_lf=base_lf,
            runtime_profile=runtime_profile,
        )
        # _scan_product 은 base_lf(파티션/RAM) + latest-lot/fab override join 을
        # lazy 로 구성한다(실제 실행은 뒤 collect). 여기서는 그 구성 시간을 scan_ms 에
        # 합산 — DB-first 경로에서 join 준비 비용을 breakdown 에 노출한다.
        _lap(runtime_profile, "scan_ms")
        lot_col, wf_col = _detect_lot_wafer(lf, product)
        # v8.4.4/v8.8.3: fab_lot_col — 매뉴얼 override > 자동 추론 > "fab_lot_id".
        fab_lot_col = "fab_lot_id"
        try:
            schema_names = lf.collect_schema().names()
            _cfg = load_json_cached(SOURCE_CFG, {}) or {}
            _ov = _lot_override_for(_cfg, product)
            _fc = (_ov.get("fab_col") or "").strip()
            if _fc and _fc in schema_names:
                fab_lot_col = _fc
            elif "fab_lot_id" not in schema_names:
                # 자동 보강된 컬럼 이름 중 하나로 대체.
                for c in _FAB_COL_CANDIDATES:
                    if c in schema_names:
                        fab_lot_col = c
                        break
        except Exception:
            pass
        # lot/wafer/fab 컬럼 해석 — 스키마 확보 + 소스 오버라이드 설정 읽기.
        _lap(runtime_profile, "schema_ms")

        fab_scope = {}
        fab_filter_for_join = fab_lot_id
        forced_fab_scope_label = ""
        if fab_lot_id.strip():
            # v9.0.5: fab_lot_id 는 DB FAB 원천에서 정확히 매칭될 때만 유효하다.
            # v9.0.6: 다만 사내/데모 파일이 이미 ML_TABLE 안에 fab/lot 값을 가진 경우도
            # 있으므로 FAB history scope 가 없다고 즉시 종료하지 않고 coalesced /view
            # 데이터에서 한 번 더 필터한다.
            fab_scope = _fab_history_scope(product, root_lot_id=root_lot_id,
                                           fab_lot_id=fab_lot_id, limit=5000)
            src_wafers = fab_scope.get("wafer_ids") or []
            if src_wafers:
                if not root_lot_id.strip() and fab_scope.get("root_ids"):
                    root_lot_id = fab_scope["root_ids"][0]
                wafer_ids = _merge_wafer_scope(wafer_ids, src_wafers)
                fab_filter_for_join = ""
                forced_fab_scope_label = fab_lot_id.strip()
        # FAB 히스토리 스코프 조회 — fab_lot_id 를 넣은 검색에서만 돈다.
        _lap(runtime_profile, "fabscope_ms")

        joined_lf = lf
        lf = _filter_lot_wafer(lf, lot_col, wf_col, root_lot_id, wafer_ids,
                               fab_lot_id=fab_filter_for_join, fab_lot_col=fab_lot_col)

        def _prepare_view_frame(view_lf):
            view_schema = view_lf.collect_schema().names()
            all_data = _view_data_columns(view_schema, lot_col, wf_col, fab_lot_col)
            tag_labels = _custom_tag_label_map(product)
            # purpose is a built-in SplitTable context row, not an opt-in TAG
            # column.  Keep the invariant even if a legacy/custom tag catalog
            # omits it.
            tag_labels[DEFAULT_CUSTOM_TAG_COLUMN] = DEFAULT_CUSTOM_TAG_LABEL
            for tag_col in tag_labels:
                if tag_col not in all_data:
                    all_data.append(tag_col)
            management_labels = _management_row_label_map(product)
            if custom_name or custom_cols:
                for mgmt_col in management_labels:
                    if mgmt_col not in all_data:
                        all_data.append(mgmt_col)
            sel = _select_columns(all_data, custom_name, prefix,
                                  max_fallback=50, custom_cols=custom_cols)
            sel = _with_default_custom_tag(sel)
            if not custom_name and not custom_cols:
                for raw_pref in [p.strip() for p in str(prefix or "").split(",") if p.strip()]:
                    for virt in _virtual_columns_for_prefix(
                            product, raw_pref, existing_columns=sel):
                        if virt not in sel:
                            sel.append(virt)
            rename = _build_col_rename_map(sel, product)
            rename.update({col: f"{CUSTOM_TAG_PREFIX}_{label}" for col, label in tag_labels.items()})
            rename.update({col: label for col, label in management_labels.items()})
            # prefix별 묶음이 아니라 각 parameter의 step_id 공정 순서를 공통 기준으로 정렬.
            step_rank = _split_step_order_context(product).get("param_rank") or {}
            sel = sorted(sel, key=lambda c: _step_order_sort_key(c, rename.get(c, c), step_rank))
            keep_cols = []
            for c in (lot_col, wf_col):
                if c and c in view_schema and c not in keep_cols:
                    keep_cols.append(c)
            keep_fab_col = "fab_lot_id" if "fab_lot_id" in view_schema else None
            if not keep_fab_col:
                keep_fab_col = (
                    _ci_resolve_in(fab_lot_col, view_schema)
                    or _pick_first_present_ci(_FAB_COL_CANDIDATES, view_schema)
                    or None
                )
            if keep_fab_col and keep_fab_col in view_schema and keep_fab_col not in keep_cols:
                keep_cols.append(keep_fab_col)
            for c in sel:
                if c in view_schema and c not in keep_cols:
                    keep_cols.append(c)
            q = view_lf.select(keep_cols) if keep_cols else view_lf
            # 여기까지가 컬럼 선택·rename·공정순 정렬 — 전부 파이썬이고 컬럼 수에
            # 비례한다(멀티 prefix 검색에서 커지는 쪽). collect 와 분리해야 어느
            # 쪽이 문제인지 보인다.
            _lap(runtime_profile, "select_ms")
            df_out = q.head(SPLITTABLE_VIEW_MAX_WAFERS).collect()
            _lap(runtime_profile, "collect_ms")
            return df_out, all_data, sel, rename

        df, all_data_cols, selected, col_rename = _prepare_view_frame(lf)
        if df.height == 0 and root_lot_id.strip() and fab_lot_id.strip():
            # If the UI carries a stale Fab Lot while the operator searches a
            # valid root lot, do not let the stale secondary field hide the
            # renderable SplitTable rows. Root remains the primary scope.
            try:
                root_only_lf = _filter_lot_wafer(
                    joined_lf, lot_col, wf_col, root_lot_id, wafer_ids,
                    fab_lot_col=fab_lot_col,
                )
                root_only_df, all_data_cols, selected, col_rename = _prepare_view_frame(root_only_lf)
                if root_only_df.height > 0:
                    df = root_only_df
                    _lot_warn = "Fab Lot ID와 Root Lot ID 조합이 없어 Root Lot ID 기준으로 조회했습니다."
            except Exception as e:
                logger.warning("view_split root-only fallback 실패 (product=%s root=%s fab=%s) %s: %s",
                               product, root_lot_id, fab_lot_id, type(e).__name__, e)
        if df.height == 0:
            # Operators often paste the FAB lot value they found in File Browser
            # into the Root Lot field. Treat that as a fab_lot_id lookup before
            # declaring the SplitTable empty.
            root_input = root_lot_id.strip()
            if root_input and not fab_lot_id.strip():
                try:
                    pasted_fab_scope = _fab_history_scope(
                        product, fab_lot_id=root_input, limit=5000
                    )
                    pasted_wafers = pasted_fab_scope.get("wafer_ids") or []
                    pasted_roots = pasted_fab_scope.get("root_ids") or []
                    if pasted_wafers and pasted_roots:
                        fallback_root = pasted_roots[0]
                        fallback_wafers = _merge_wafer_scope(wafer_ids, pasted_wafers)
                        fallback_lf = _scan_product(
                            product, root_lot_id=fallback_root,
                            wafer_ids=fallback_wafers,
                            runtime_profile=runtime_profile,
                        )
                    else:
                        fallback_root = ""
                        fallback_lf = _scan_product(product, fab_lot_id=root_input,
                                                    wafer_ids=wafer_ids,
                                                    runtime_profile=runtime_profile)
                    fallback_names = fallback_lf.collect_schema().names()
                    fallback_fab_col = (
                        _ci_resolve_in(fab_lot_col, fallback_names)
                        or _pick_first_present_ci(_FAB_COL_CANDIDATES, fallback_names)
                    )
                    if pasted_wafers and pasted_roots:
                        fallback_df, all_data_cols, selected, col_rename = _prepare_view_frame(fallback_lf)
                        if fallback_df.height > 0:
                            df = fallback_df
                            fab_lot_id = root_input
                            root_lot_id = fallback_root
                            forced_fab_scope_label = root_input
                            _lot_warn = "입력한 Root Lot ID를 fab_lot_id로 해석해 조회했습니다."
                    elif fallback_fab_col:
                        fallback_lf = _filter_lot_wafer(
                            fallback_lf, lot_col, wf_col, "",
                            wafer_ids, fab_lot_id=root_input,
                            fab_lot_col=fallback_fab_col,
                        )
                        fallback_df, all_data_cols, selected, col_rename = _prepare_view_frame(fallback_lf)
                        if fallback_df.height > 0:
                            df = fallback_df
                            fab_lot_id = root_input
                            root_lot_id = ""
                            _lot_warn = "입력한 Root Lot ID를 fab_lot_id로 해석해 조회했습니다."
                except Exception as e:
                    logger.warning("view_split fab_lot fallback 실패 (product=%s input=%s) %s: %s",
                                   product, root_input, type(e).__name__, e)
        # 빈 결과 재해석 구간(위 두 폴백). 안쪽 _prepare_view_frame 은 자기 몫을
        # select/collect 로 이미 떼 갔으므로 여기 남는 건 폴백 자체의 비용이다.
        _lap(runtime_profile, "emptyfallback_ms")
        if df.height == 0:
            return _split_view_finish_payload(
                {"product": product, "lot_col": lot_col, "wf_col": wf_col,
                 "headers": [], "rows": [], "prefixes": _load_prefixes(),
                 "product_cache": _product_ram_cache_response_meta(product),
                 "msg": "No data"},
                started=started,
                runtime_profile=runtime_profile,
                payload_cache_hit=False,
                view_cache_key=view_cache_key,
            )
        if not root_lot_id.strip() and lot_col and lot_col in df.columns:
            roots = []
            for v in df[lot_col].cast(_STR, strict=False).to_list():
                s = str(v or "").strip()
                if s and s not in ("None", "null") and s not in roots:
                    roots.append(s)
            if roots:
                root_lot_id = sorted(roots)[0]

        # Wafer header list + fab_lot_id grouping (v8.4.4)
        fab_col = "fab_lot_id" if "fab_lot_id" in df.columns else None
        if wf_col and wf_col in df.columns:
            # Wafer IDs are physically 1..25. Some upstream DBs contain
            # placeholder values like 1000; do not expose or plan against them.
            wf_raw = [_normalize_wafer_id(v) for v in df[wf_col].to_list()]
            # Per-wafer fab_lot_id (first non-null occurrence per wafer)
            wf2fab: dict = {}
            if fab_col:
                fab_vals = [(None if v is None else str(v)) for v in df[fab_col].to_list()]
                for w, f in zip(wf_raw, fab_vals):
                    if not w: continue
                    if w not in wf2fab and f and f not in ("None", "null"):
                        wf2fab[w] = f
            if forced_fab_scope_label:
                wf2fab = {w: forced_fab_scope_label for w in dict.fromkeys(wf_raw) if w}
            # Sort: (fab_lot_id 그룹, wafer_id 숫자-aware) — fab_lot 미정이면 "~" 로 후순위.
            # v8.8.3: wafer_id 가 문자열일 때 "10" < "2" 오작동 → 숫자 가능하면 int 로 cast 해서 secondary 키.
            wf_uniq = [w for w in dict.fromkeys(wf_raw) if w]
            def _wf_sort_key(w):
                primary = wf2fab.get(w, "~")
                try:
                    n = int(w)
                    return (primary, 0, n)
                except (TypeError, ValueError):
                    s = str(w)
                    # 선행 'W' 제거 후 숫자 시도
                    if s.upper().startswith("W"):
                        try:
                            return (primary, 0, int(s[1:]))
                        except ValueError:
                            pass
                    return (primary, 1, s)
            wf_sorted = sorted(wf_uniq, key=_wf_sort_key)
            headers = [f"#{v}" for v in wf_sorted]
            wf_idx = {v: i for i, v in enumerate(wf_sorted)}
            # Build header_groups: consecutive same-fab_lot segments
            wafer_fab_list = [wf2fab.get(w, "") for w in wf_sorted]
            header_groups = []
            if fab_col:
                cur = None; span = 0
                for f in wafer_fab_list:
                    if f == cur:
                        span += 1
                    else:
                        # fab_lot_id 를 못 찾은 구간도 라벨은 남긴다 — 빈 문자열로
                        # 두면 프런트가 lot_id 헤더 행에 빈 칸을 그려서 "lot id 가
                        # 사라졌다" 로 보인다.
                        if span > 0: header_groups.append({"label": cur or "—", "span": span})
                        cur = f; span = 1
                if span > 0: header_groups.append({"label": cur or "—", "span": span})
        else:
            wf_raw = list(range(df.height))
            wf_sorted = list(range(df.height))
            headers = [f"#{i}" for i in wf_sorted]
            wf_idx = {i: i for i in wf_sorted}
            wafer_fab_list = []
            header_groups = []
        # 웨이퍼 헤더/그룹 조립 — df 컬럼을 파이썬 리스트로 꺼내 정렬·그룹핑한다.
        _lap(runtime_profile, "header_ms")

        # Load plans — 이 root 것만. 아래 조회는 전부 `root|wafer|col` 키라서
        # 제품 전체를 들고 있을 이유가 없었고, 그 전량 복사가 요청당 3.5ms 였다.
        # (편집 경로는 계속 _load_plan_data 로 전체 사본을 받는다.)
        plans = _plan_entries_for_root(product, root_lot_id)
        tag_labels = _custom_tag_label_map(product)
        tag_values = _custom_tag_values_for_root(product, root_lot_id)
        tag_colors = _custom_tag_colors_for_root(product, root_lot_id)
        management_labels = _management_row_label_map(product)
        management_values = _management_row_values_for_root(product, root_lot_id)
        _lap(runtime_profile, "overlay_ms")

        # 슬림 셀 포맷(v2)을 곧바로 만든다. 예전에는 셀마다 9키 dict + f-string key 를
        # 만들고(KNOB 2000행×25웨이퍼 ≈ 5만 dict / 10만 f-string) 그 결과를 다시
        # 전량 순회해 compact 로 변환했다. 이 구간은 순수 파이썬이라 GIL 을 놓지
        # 않아 동시 검색이 여기서 그대로 직렬화됐다. 셀 key 는 plan/mismatch 가
        # 실제로 있는 셀에서만 조립한다(대부분 lot 은 plan 이 없거나 몇 개뿐).
        rows_compact = []
        row_mismatches = []  # rows_compact 와 같은 인덱스 — diff 필터 후 평탄화
        df_cols_set = set(df.columns)
        n_cols = len(wf_sorted)
        has_plans = bool(plans)
        for col_name in selected:
            is_tag_col = col_name in tag_labels
            is_management_row = col_name in management_labels
            vals = [None] * n_cols
            plan_vals = [None] * n_cols
            # v8.8.16: CUSTOM 에 저장된 컬럼이 현재 df 에 없더라도 빈 행으로 표시.
            #   (e.g. plan 전용 가상 컬럼, 다른 제품에서 저장된 컬럼 등). plan 값은 여전히 lookup.
            if is_tag_col:
                for ci, wf_key in enumerate(wf_sorted):
                    vals[ci] = tag_values.get(f"{root_lot_id}|{wf_key}|{col_name}")
            elif is_management_row:
                for ci, wf_key in enumerate(wf_sorted):
                    vals[ci] = management_values.get(f"{root_lot_id}|{wf_key}|{col_name}")
            elif col_name in df_cols_set:
                try:
                    col_data = df[col_name].to_list()
                    for i, val in enumerate(col_data):
                        key = wf_raw[i] if i < len(wf_raw) else None
                        idx = wf_idx.get(key)
                        if idx is not None:
                            vals[idx] = val
                            if has_plans:
                                pv = plans.get(f"{root_lot_id}|{key}|{col_name}", {}).get("value")
                                if pv is not None:
                                    plan_vals[idx] = pv
                except Exception:
                    pass
            elif has_plans:
                # 가상 컬럼 — plan 값만 확인.
                for ci, wf_key in enumerate(wf_sorted):
                    pv = plans.get(f"{root_lot_id}|{wf_key}|{col_name}", {}).get("value")
                    if pv is not None:
                        plan_vals[ci] = pv

            plans_sparse = {}
            mism = []
            cell_mismatches = []
            for ci in range(n_cols):
                actual = vals[ci]
                actual_str = None if actual is None else str(actual)
                if actual_str in ("None", "null"):
                    actual_str = None
                vals[ci] = actual_str
                plan = plan_vals[ci]
                if plan is None:
                    continue
                plans_sparse[str(ci)] = plan
                if plan and actual_str and str(plan) != actual_str:
                    mism.append(ci)
                    ck = f"{root_lot_id}|{wf_sorted[ci]}|{col_name}"
                    plan_info = plans.get(ck, {})
                    cell_mismatches.append({
                        "param": col_name, "key": ck,
                        "plan": plan, "actual": actual_str,
                        "plan_user": plan_info.get("user", ""),
                        "plan_updated": plan_info.get("updated", ""),
                    })

            # v8.8.14: _display — rule_order + step_desc를 포함한 렌더용 이름.
            #   없으면 원본과 동일. FE 는 _display 를 사용하고 prefix strip 후 표시.
            row_c = {"_param": col_name, "_display": col_rename.get(col_name, col_name), "a": vals}
            if plans_sparse:
                row_c["p"] = plans_sparse
            if mism:
                row_c["m"] = mism
            if n_cols:
                # 행-상수 플래그 — 셀이 하나도 없으면(웨이퍼 0개) 레거시와 동일하게 생략.
                col_upper = col_name.upper()
                if (not is_tag_col and not is_management_row) and any(
                        col_upper.startswith(p + "_") for p in PLAN_ALLOWED_PREFIXES):
                    row_c["can_plan"] = True
                if is_tag_col:
                    row_c["tag"] = True
                    cell_colors = {
                        str(ci): color
                        for ci, wf_key in enumerate(wf_sorted)
                        if (color := tag_colors.get(f"{root_lot_id}|{wf_key}|{col_name}"))
                    }
                    if cell_colors:
                        row_c["tc"] = cell_colors
                if is_management_row:
                    row_c["mgmt"] = True
            rows_compact.append(row_c)
            row_mismatches.append(cell_mismatches)

        if view_mode == "diff":
            keep = [i for i, r in enumerate(rows_compact)
                    if len(set(v for v in r["a"] if v is not None)) > 1]
            rows_compact = [rows_compact[i] for i in keep]
            row_mismatches = [row_mismatches[i] for i in keep]

        # Detect mismatches and send notifications to plan owners
        # (diff 모드에서는 레거시와 동일하게 화면에 남은 행만 대상)
        mismatches = [m for per_row in row_mismatches for m in per_row]
        _lap(runtime_profile, "matrix_ms")
        if not force_recompute:
            # 백그라운드 재검증은 동일 데이터를 재계산하는 것이므로 알림을 중복 발송하지 않는다.
            _enqueue_plan_actual_mismatches(product, mismatches, actor="flow")
        _lap(runtime_profile, "mismatch_ms")

        # v8.8.5: view 응답에 오버라이드 resolve 결과 동봉 — FE 상단 배지에 "어디서 읽어왔는지" 바로 표시.
        override_meta = _resolve_override_meta_light(product)
        # v9.0.5: FAB 후보는 DB FAB 원천의 정확한 root 매칭만 노출한다.
        #   DB FAB 에 없는 root 는 ML_TABLE LOT_ID / joined null fallback 을 쓰지 않는다.
        available_fab_lots = sorted(
            {str(v).strip() for v in wafer_fab_list if str(v or "").strip()},
            key=lambda s: s.upper(),
        )
        fab_presence_known = bool(available_fab_lots)
        if not available_fab_lots:
            hist_lots = _fab_history_scope(product, root_lot_id=root_lot_id, limit=1000)
            fab_presence_known = bool(hist_lots.get("query_ok"))
            if hist_lots.get("candidates"):
                available_fab_lots = hist_lots["candidates"]
        fab_present = True if available_fab_lots else (False if fab_presence_known else None)
        _lap(runtime_profile, "overlay_ms")
        # latest-lot 캐시 기준 현재 진행 step 이후(미진행) 행 — FE 회색 셰이딩용.
        # FAB 조회가 정상 완료됐는데 root 매칭이 0건이면 아직 FAB에 없는 lot이므로
        # 모든 표시 공정을 회색으로 보낸다. 조회 실패/소스 미설정은 미확인으로 둔다.
        step_progress = _split_step_progress(
            product, root_lot_id, selected, wf_sorted, fab_present=fab_present)
        _lap(runtime_profile, "progress_ms")
        payload = {
            "product": product, "lot_col": lot_col, "wf_col": wf_col,
            "step_progress": step_progress,
            "headers": headers, "rows_compact": rows_compact,
            "wafer_keys": [f"{k}" for k in wf_sorted],
            "header_groups": header_groups, "wafer_fab_list": wafer_fab_list,
            "row_labels": {"root_lot_id": "root_lot_id", "lot_id": "lot_id", "parameter": "항목"},
            "available_fab_lots": available_fab_lots,
            "prefixes": _load_prefixes(), "precision": load_json_cached(PRECISION_CFG, DEFAULT_PRECISION), "root_lot_id": root_lot_id,
            "all_columns": _merge_all_columns(all_data_cols, knob_sidecar_all_columns, lot_col, wf_col),
            "selected_count": len(selected),
            "prefix": prefix or (custom_name if custom_name else ""),
            "history_mode": _history_mode,
            "plan_allowed_prefixes": PLAN_ALLOWED_PREFIXES,
            "mismatch_count": len(mismatches),
            "override": override_meta,
            "match_cache": _match_cache_response_meta(product),
            "product_cache": _product_ram_cache_response_meta(product),
            "lookup_cache": runtime_profile.get("_lookup_cache") or _split_view_lookup_cache_public(None, None),
            "lot_warn": _lot_warn,
        }
        # 응답 dict 조립 — prefixes/precision 설정 로드, all_columns 병합, 캐시 메타.
        _lap(runtime_profile, "payload_ms")
        # FAB root index가 아직 없을 때의 부분 응답을 payload cache에 고정하지 않는다.
        # 인덱스 완료 후 UI polling 또는 다음 검색이 즉시 완전한 결과를 받게 한다.
        if not runtime_profile.get("cache_incomplete"):
            _split_view_cache_put(view_cache_key, view_hard_sig, view_soft_sig, payload)
        _lap(runtime_profile, "cacheput_ms")
        return _attach_split_view_runtime_fields(
            payload,
            request,
            include_related=include_related,
            started=started,
            runtime_profile=runtime_profile,
            payload_cache_hit=False,
            view_cache_key=view_cache_key,
        )
    except HTTPException:
        _view_compute_finish(view_cache_key)
        raise
    except Exception as e:
        _view_compute_finish(view_cache_key)
        raise HTTPException(400, f"View error: {str(e)}")


# ── Pre-pivoted cache: on-demand refresh ──
