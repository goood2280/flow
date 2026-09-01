class UnifiedScanReq(BaseModel):
    product: str = ""
    force: bool = True


_UNIFIED_SCAN_LOCK = threading.Lock()
_UNIFIED_SCAN_BUSY = False
_UNIFIED_SCAN_JOB_ID = ""
_UNIFIED_SCAN_THREAD: threading.Thread | None = None

# 대기 중 진행 로그 간격 — 대기도 '진행'으로 보이게 해 정지와 구분한다.
_SCAN_WAIT_LOG_GAP_SEC = 15.0


def _scan_stage_timeout_sec() -> float:
    return max(60.0, min(6 * 3600.0, _env_float("FLOW_UNIFIED_SCAN_STAGE_TIMEOUT_SEC", 3600.0)))


def _submit_scan(kind: str, label: str, run, *, product: str = "",
                 source: str = "manual", dedupe_key: str = "") -> dict:
    """스캔 작업을 서버 단위 게이트(core.scan_gate)에 넘긴다.

    한 서버에서 스캔은 항상 하나만 돈다 — 이미 다른 스캔이 진행 중이면 거절하지
    않고 대기열에 넣고, 앞 작업이 성공하든 실패하든 끝나면 이어서 실행한다.
    게이트를 못 쓰는 예외 상황(모듈 로드 실패)에서는 예전처럼 전용 스레드로
    떨어뜨려, 스캔 자체가 불가능해지지는 않게 한다."""
    try:
        from core import scan_gate
    except Exception:
        logger.warning("scan gate unavailable — running %s without serialization", kind, exc_info=True)
        threading.Thread(target=run, name=f"splittable-{kind}", daemon=True).start()
        return {"ok": True, "queued": True, "ahead": 0, "detail": "스캔 시작됨(직렬화 게이트 없음)"}
    return scan_gate.submit(kind, label, run, product=product, source=source,
                            dedupe_key=dedupe_key)


def _reap_dead_unified_scan() -> bool:
    """스캔 스레드가 죽었는데 busy 플래그만 남은 상태를 정리한다.

    이 방어가 없으면 스레드가 예기치 않게 사라졌을 때 이후 모든 스캔 요청이
    '이미 실행 중'으로 거부되고 화면은 영원히 '스캔 중...'에 머문다.
    반환: 정리했으면 True."""
    global _UNIFIED_SCAN_BUSY, _UNIFIED_SCAN_JOB_ID, _UNIFIED_SCAN_THREAD
    with _UNIFIED_SCAN_LOCK:
        if not _UNIFIED_SCAN_BUSY:
            return False
        thread = _UNIFIED_SCAN_THREAD
        if thread is not None and thread.is_alive():
            return False
        job_id = _UNIFIED_SCAN_JOB_ID
        _UNIFIED_SCAN_BUSY = False
        _UNIFIED_SCAN_THREAD = None
    logger.warning("unified scan thread vanished — busy flag cleared (job=%s)", job_id)
    try:
        from core.cache_event_log import finish_job, record
        if job_id:
            finish_job(job_id, ok=False, detail={"error": "scan_thread_vanished"})
        record("scan", "[작업 실패] 스캔 스레드가 종료되어 작업을 중단 처리했습니다 "
                       "(서버 재시작/강제 종료 등). 다시 실행할 수 있습니다.", ok=False)
    except Exception:
        logger.debug("unified scan reap logging failed", exc_info=True)
    return True


def _wait_for_cache_job(status_fn, *, stage: str, stage_label: str = "", job_id: str = "") -> dict:
    """이미 실행 중인 캐시 stage가 끝날 때까지 통합 스캔 스레드에서 대기.

    대기 동안 주기적으로 진행 로그를 남긴다 — 예전엔 최대 1시간을 아무 로그 없이
    대기해 화면이 멈춘 것처럼 보였다."""
    from core.cache_event_log import record as _rec, heartbeat as _beat
    timeout = _scan_stage_timeout_sec()
    label = stage_label or stage
    started = time.monotonic()
    deadline = started + timeout
    next_log = started + _SCAN_WAIT_LOG_GAP_SEC
    _rec("scan", f"[대기] {label}: 다른 요청이 이미 실행 중 — 끝날 때까지 기다립니다"
                 f" (최대 {_fmt_dur_ko(timeout)})",
         detail={"job_id": job_id, "stage": stage, "phase": "waiting"} if job_id else {"stage": stage})
    while True:
        now = time.monotonic()
        status = status_fn()
        if not status.get("running"):
            return {
                "ok": bool(status.get("ok_count")),
                "queued": False,
                "products": status.get("products") or [],
                "job": status,
                "joined_existing": True,
            }
        if now >= deadline:
            break
        if now >= next_log:
            next_log = now + _SCAN_WAIT_LOG_GAP_SEC
            _beat(job_id, f"{label} 대기 중")
            done = int(status.get("done") or 0)
            total = int(status.get("total") or 0)
            progress = f" · {done}/{total} 진행" if total else ""
            _rec("scan",
                 f"[대기] {label}: 실행 중인 작업 대기 {_fmt_dur_ko(now - started)} 경과{progress}"
                 f" (현재: {status.get('current_product') or '-'})",
                 detail={"job_id": job_id, "stage": stage, "phase": "waiting"} if job_id else {"stage": stage})
        time.sleep(1.0)
    _rec("scan", f"[실패] {label}: 대기 시간 초과({_fmt_dur_ko(timeout)}) — 앞선 작업이 끝나지 않았습니다."
                 f" env FLOW_UNIFIED_SCAN_STAGE_TIMEOUT_SEC 로 대기 한도를 조절할 수 있습니다.",
         ok=False, detail={"job_id": job_id, "stage": stage, "phase": "failed"} if job_id else {"stage": stage})
    return {"ok": False, "error": f"{stage}_timeout", "joined_existing": True,
            "detail": f"대기 시간 초과({int(timeout)}초)"}


def _scan_lookup_targets(product: str) -> list[Path]:
    """수동 스캔이 랏(lookup) 캐시를 보장해야 할 ML_TABLE 파일 목록."""
    if str(product or "").strip():
        fp = _ml_table_lookup.resolve_ml_table_file(product=product)
        return [fp] if fp else []
    try:
        return list(_ml_table_lookup.discover_ml_table_files() or [])
    except Exception:
        logger.debug("scan lookup target discovery failed", exc_info=True)
        return []


def _wait_for_lookup_builds(targets: dict[str, Path], *, job_id: str = "",
                            note: str = "랏캐시 빌드") -> set[str]:
    """targets(파일명 → 경로)의 lookup 캐시가 fresh 가 될 때까지 기다린다.

    대기 중에도 진행 로그와 heartbeat 를 남긴다 — 조용한 대기는 화면에서 정지와
    구분되지 않고, heartbeat 가 끊기면 job tracker 가 정지로 보고 실패 처리한다.
    반환: 제한 시간 안에 끝나지 못한 파일명 집합(비어 있으면 전부 완료).

    **기다리는 동안 캐시 슬롯을 반납한다** (`scan_gate.lend`). 이 함수는 스캔
    게이트 작업 안에서 불리고, 기다리는 대상인 빌드 스레드는 이제 같은 슬롯을
    잡아야 한다 — 붙들고 기다리면 서로를 기다리는 교착이 된다. 대기 중인 이
    스레드는 자원을 쓰지 않으므로 반납이 안전하고, 빌드가 끝나면 다시 잡는다."""
    from core.cache_event_log import record as _rec, heartbeat as _beat
    pending = set(targets)
    if not pending:
        return set()
    total = len(pending)
    timeout = _scan_stage_timeout_sec()
    started = time.monotonic()
    deadline = started + timeout
    next_log = started + _SCAN_WAIT_LOG_GAP_SEC
    _detail = {"job_id": job_id, "stage": "root_lot_ram", "phase": "waiting"}

    def _wait_loop() -> set[str]:
        nonlocal next_log
        while pending:
            now = time.monotonic()
            for name in sorted(pending):
                fp = targets.get(name)
                try:
                    if fp is not None and _ml_table_lookup.cache_status(fp).get("status") == "fresh":
                        pending.discard(name)
                except Exception:
                    logger.debug("lookup cache status check failed file=%s", name, exc_info=True)
            if not pending or now >= deadline:
                break
            if now >= next_log:
                next_log = now + _SCAN_WAIT_LOG_GAP_SEC
                _beat(job_id, f"{note} 대기 중")
                queue = _ml_table_lookup.build_queue_snapshot()
                current = Path(str(queue.get("current") or "")).name or "-"
                last_error = str(queue.get("last_error") or "")
                _rec("scan",
                     f"[대기] {note} {total - len(pending)}/{total} 완료 · "
                     f"{_fmt_dur_ko(now - started)} 경과 · 빌드 중: {current}"
                     + (f" · 최근 오류: {last_error}" if last_error else "")
                     + " · 남은 제품: " + ", ".join(sorted(pending)[:3])
                     + ("…" if len(pending) > 3 else ""),
                     detail=_detail)
            time.sleep(1.0)
        return pending

    try:
        from core import scan_gate as _gate
        _lend = _gate.lend()
    except Exception:
        logger.debug("scan gate lend unavailable during lookup build wait", exc_info=True)
        import contextlib as _ctx
        _lend = _ctx.nullcontext()
    with _lend:
        return _wait_loop()


def _ensure_scan_lookup_caches(product: str = "", *, job_id: str = "") -> dict:
    """랏(lookup) 디스크 캐시를 대상 제품에 대해 빌드하고 완료까지 기다린다.

    랏캐시는 프로세스 로컬 RAM 이 아니라 운영/개발 두 서버가 함께 읽는 공유
    파티션 산출물이다. 그래서 root RAM 캐시가 꺼진 서버(개발/워커 역할)에서도
    이 단계는 돌아야 한다 — 예전엔 RAM 이 비활성이면 3/3 단계 전체를 건너뛰어,
    개발서버에서 수동 스캔을 아무리 돌려도 랏캐시가 만들어지지 않았다.

    fresh 한 캐시는 건드리지 않는다. 화면의 수동 스캔은 항상 force=True 로
    오지만, 랏캐시에서 force 는 예전에도 '무조건 재빌드'가 아니라 자동 빌드
    크기 상한을 넘긴다는 뜻이었다 — 매 스캔마다 전 제품을 다시 빌드하면
    몇 시간짜리 작업이 된다."""
    from core.cache_event_log import record as _rec
    _detail = {"job_id": job_id, "stage": "root_lot_ram", "phase": "waiting"}
    files = _scan_lookup_targets(product)
    if not files:
        return {"ok": False, "total": 0, "built": 0,
                "detail": "랏캐시 대상 ML_TABLE_*.parquet 파일을 찾지 못했습니다"}
    targets: dict[str, Path] = {}
    for fp in files:
        try:
            fresh = _ml_table_lookup.cache_status(fp).get("status") == "fresh"
        except Exception:
            fresh = False
        if not fresh:
            targets[Path(fp).name] = Path(fp)
    if not targets:
        return {"ok": True, "total": len(files), "built": 0, "skipped": True,
                "reason": "fresh", "detail": "랏캐시가 이미 최신입니다"}
    for fp in targets.values():
        # immediate=True: 관리자가 직접 요청한 빌드는 idle 창을 기다리지 않는다.
        # 진행 화면이 2.5초마다 폴링하는 동안 서버는 절대 idle 이 되지 않아,
        # idle 대기가 곧 무한 정지였다.
        _ml_table_lookup.enqueue_build(fp, immediate=True)
    _rec("scan",
         f"[대기] 랏캐시 빌드 — {len(targets)}/{len(files)}개 제품 빌드 큐 등록"
         f" (최대 {_fmt_dur_ko(_scan_stage_timeout_sec())})",
         detail=_detail)
    pending = _wait_for_lookup_builds(targets, job_id=job_id)
    built = len(targets) - len(pending)
    if pending:
        _rec("scan",
             f"[실패] 랏캐시 빌드 대기 시간 초과 — 미완료 {len(pending)}/{len(targets)}개: "
             + ", ".join(sorted(pending)[:5]),
             ok=False,
             detail={"job_id": job_id, "stage": "root_lot_ram", "phase": "failed"})
        return {"ok": False, "total": len(files), "built": built,
                "pending_files": sorted(pending),
                "detail": f"랏캐시 빌드 대기 시간 초과 — 미완료 {len(pending)}개"}
    _rec("scan", f"[대기] 랏캐시 빌드 {built}/{len(targets)} 완료", detail=_detail)
    return {"ok": True, "total": len(files), "built": built}


def _wait_for_root_lookup_caches(result: dict, *, product: str, job_id: str = "") -> dict:
    """root RAM 예열이 요청한 lookup 파티션 빌드를 기다린 뒤 한 번 재시도.

    빌드는 수 분~수십 분 걸릴 수 있으므로 대기 진행을 주기적으로 로그에 남긴다."""
    from core.cache_event_log import record as _rec, heartbeat as _beat
    pending = {
        str(row.get("file") or "")
        for row in (result.get("products") or [])
        if row.get("build_pending") and row.get("file")
    }
    if not pending:
        return result
    total = len(pending)
    timeout = _scan_stage_timeout_sec()
    started = time.monotonic()
    deadline = started + timeout
    next_log = started + _SCAN_WAIT_LOG_GAP_SEC
    _detail = {"job_id": job_id, "stage": "root_lot_ram", "phase": "waiting"}
    _rec("scan", f"[대기] 랏캐시(원본 lookup) 빌드 대기 — {total}개 제품 "
                 f"(최대 {_fmt_dur_ko(timeout)}). 빌드 진행은 [랏캐시빌드] 로그에 표시됩니다.",
         detail=_detail)
    while pending:
        now = time.monotonic()
        ready: set[str] = set()
        for file_name in pending:
            fp = _ml_table_lookup.resolve_ml_table_file(file=file_name)
            if fp is not None and _ml_table_lookup.cache_status(fp).get("status") == "fresh":
                ready.add(file_name)
        pending.difference_update(ready)
        if not pending:
            break
        if now >= deadline:
            break
        if now >= next_log:
            next_log = now + _SCAN_WAIT_LOG_GAP_SEC
            _beat(job_id, "랏캐시 빌드 대기 중")
            _rec("scan",
                 f"[대기] 랏캐시 빌드 {total - len(pending)}/{total} 완료 · "
                 f"{_fmt_dur_ko(now - started)} 경과 · 남은 제품: "
                 + ", ".join(sorted(pending)[:3]) + ("…" if len(pending) > 3 else ""),
                 detail=_detail)
        time.sleep(1.0)
    if pending:
        _rec("scan",
             f"[실패] 랏캐시 빌드 대기 시간 초과({_fmt_dur_ko(timeout)}) — "
             f"미완료 {len(pending)}/{total}개: " + ", ".join(sorted(pending)[:5]),
             ok=False, detail={"job_id": job_id, "stage": "root_lot_ram", "phase": "failed"})
        out = dict(result)
        out.update(ok=False, error="root_lookup_build_timeout", pending_files=sorted(pending),
                   detail=f"랏캐시 빌드 대기 시간 초과 — 미완료 {len(pending)}개")
        return out
    _rec("scan", f"[대기] 랏캐시 빌드 {total}/{total} 완료 — RAM 적재를 이어서 진행합니다",
         detail=_detail)
    # lookup build 완료 직후 운영 프로세스 RAM에 실제 root frame을 올린다.
    return _ml_table_lookup.refresh_root_lot_ram_cache(product=product, force=False, load_now=True)


_MANUAL_LATEST_REFRESH_LOCK = threading.Lock()
_MANUAL_LATEST_REFRESH_RUNNING = False


def _enqueue_manual_lot_progress_refresh(products: list[str]) -> bool:
    """Queue one normal-priority refresh of the shared WIP/latest-lot cache."""
    global _MANUAL_LATEST_REFRESH_RUNNING
    with _MANUAL_LATEST_REFRESH_LOCK:
        if _MANUAL_LATEST_REFRESH_RUNNING:
            return False
        _MANUAL_LATEST_REFRESH_RUNNING = True

    def _run() -> None:
        global _MANUAL_LATEST_REFRESH_RUNNING
        label = ", ".join(products or []) or "전체 제품"
        started_ts = time.time()
        # pivot 빌드와 같은 이유로 실행 자체를 이벤트 로그에 남긴다 — 예전에는
        # "큐 등록 완료"만 남고 실제 갱신은 서버 터미널에만 찍혔다.
        for _prod in (products or [""]):
            _cache_build_emit(_prod, f"[WIP latest-lot] 갱신 시작 — {label}",
                              detail={"products": list(products or []),
                                      "stage": _stage("latest_lot", "start")})
        ok = False
        detail: dict = {}
        try:
            from core import lot_progress_cache as _lpc
            from core import worker_dispatch as _wd

            def _local_refresh() -> dict:
                state = _lpc.refresh_lot_progress_cache(
                    force=True, required_products=list(products or []))
                return {"ok": bool((state or {}).get("generated_at")),
                        "count": int((state or {}).get("count") or 0)}

            res = _wd.run_heavy(
                "splittable_lot_progress_cache_refresh",
                {"force": True, "required_products": list(products or [])},
                _local_refresh,
                label="WIP latest lot cache",
                local_idle_only=False,
                local_fallback=True,
                durable=False,
                priority="normal",
                dedupe_key="lot_progress_refresh",
            )
            ok = bool((res or {}).get("ok"))
            detail = {"count": int((res or {}).get("count") or 0)}
        except Exception as exc:
            logger.warning("manual WIP/latest-lot cache refresh failed", exc_info=True)
            detail = {"error": str(exc)}
        finally:
            elapsed = round(time.time() - started_ts, 1)
            for _prod in (products or [""]):
                _cache_build_emit(
                    _prod,
                    (f"[WIP latest-lot] 갱신 완료 — {detail.get('count', 0)}건 · {elapsed}s"
                     if ok else f"[WIP latest-lot] 갱신 실패 — {detail.get('error', '결과 없음')} ({elapsed}s)"),
                    ok=ok, detail={**detail, "elapsed_sec": elapsed,
                                   "stage": _stage("latest_lot", "done" if ok else "fail")},
                )
            with _MANUAL_LATEST_REFRESH_LOCK:
                _MANUAL_LATEST_REFRESH_RUNNING = False

    threading.Thread(target=_run, daemon=True, name="manual-lot-progress-refresh").start()
    return True


def _refresh_dashboard_latest_v4(products: list[str], *, force: bool,
                                 job_id: str = "") -> dict:
    """Build and atomically publish the dashboard's canonical format-v4 cache.

    The generic LOT-progress scanner owns ``lot_wf_current.parquet`` only.  The
    dashboard canonical file needs the SplitTable match projection (including
    its embedded format/source columns), so rebuilding only the scanner cache
    can never upgrade an old canonical file to v4.  Build all requested match
    inputs first and publish only after every requested product succeeded; a
    failed refresh therefore leaves the previous canonical generation intact.
    """
    targets = [str(value or "").strip() for value in products if str(value or "").strip()]
    if not targets:
        return {"ok": False, "error": "no_products", "latest_cache": {}}
    if job_id:
        from core.cache_event_log import heartbeat
        heartbeat(job_id, "대시보드 format v4 입력 캐시 빌드 중")
    match = _refresh_match_cache_products(targets, force=force)
    rows = list(match.get("products") or [])
    failed = [row for row in rows if not row.get("ok")]
    if failed or len(rows) < len(targets):
        return {
            "ok": False,
            "error": "match_cache_incomplete",
            "failed": failed,
            "match_cache": match,
            "latest_cache": {},
        }

    # Export from the complete catalog so a product-specific refresh does not
    # replace the shared dashboard file with a one-product subset.  The writer
    # uses .tmp + replace, preserving the old v4 file throughout the build.
    export_products = _match_cache_products("") or targets
    export = export_latest_lot_step_cache(
        products=export_products,
        update_state=_match_cache_products_cover_all(targets),
    )
    exported = _canonical_product_set(list(export.get("products") or []))
    expected = _canonical_product_set(targets)
    ok = bool(export.get("ok") and expected and expected.issubset(exported))
    if job_id:
        from core.cache_event_log import heartbeat
        heartbeat(
            job_id,
            (f"대시보드 format v{LATEST_LOT_STEP_CACHE_FORMAT_VERSION} 완료 — "
             f"{int(export.get('row_count') or 0):,}행") if ok
            else f"대시보드 format v{LATEST_LOT_STEP_CACHE_FORMAT_VERSION} 생성 실패",
        )
    return {
        "ok": ok,
        "error": "" if ok else "canonical_export_incomplete",
        "match_cache": match,
        "latest_cache": export,
    }


def _enqueue_required_split_caches(product: str, force: bool, job_id: str = "", *,
                                   owns_job: bool = True,
                                   local_only: bool = False) -> dict:
    """Build the four required SplitTable caches and keep the scan task cancellable.

    local_only=True 면 네 단계 모두 이 서버에서 직접 빌드한다 (개발 워커 오프로드
    금지). 관리자가 "수동 캐싱" 을 누른 서버가 결과와 진행률을 보는 서버이므로,
    그 서버에서 바로 돌아야 진행 표시와 중단이 모두 같은 곳에서 동작한다.

    The previous implementation returned as soon as it had spawned independent
    daemon queues.  The UI therefore lost its cancellable scan task while the
    expensive work was still running.  Keep this orchestration task alive until
    each real artifact finishes; a cancel request stops before the next product
    or stage while the current safe build batch is allowed to finish.
    """
    global _UNIFIED_SCAN_BUSY
    from core.cache_event_log import heartbeat, record, stage_started, stage_finished

    requested = str(product or "").strip()
    products = _match_cache_products(requested)
    if requested and not products:
        products = [requested]
    results: dict = {
        "ok": True, "product": requested, "mode": "product_pipeline",
        "response_cache": {"queued": False, "reason": "query_specific_read_through"},
        "products": [],
    }
    cancelled = False
    try:
        stage_timeout = max(60.0, float(os.environ.get(
            "FLOW_CACHE_PIPELINE_STAGE_TIMEOUT_SEC", "21600") or 21600.0))
    except Exception:
        stage_timeout = 21600.0

    class _PipelineCancelled(RuntimeError):
        pass

    def _cancel_point() -> None:
        if _scan_cancel_requested():
            raise _PipelineCancelled("관리자 중단 요청")

    def _wait_for(label: str, ready, *, timeout: float | None = None) -> bool:
        from core import scan_gate as _scan_gate
        started = time.monotonic()
        last_beat = 0.0
        limit = stage_timeout if timeout is None else max(1.0, float(timeout))
        # The actual lookup/pivot/FAB builder runs on another thread and uses
        # the same single-cache slot.  Lend that slot while merely waiting or
        # the orchestrator and its child builder would deadlock each other.
        with _scan_gate.lend():
            while time.monotonic() - started < limit:
                _cancel_point()
                if ready():
                    return True
                now = time.monotonic()
                if job_id and now - last_beat >= 2.0:
                    heartbeat(job_id, label)
                    last_beat = now
                time.sleep(0.25)
        return False

    def _release_stage_memory(stage_id: str) -> dict:
        """단계 사이에서 작업 메모리를 OS 로 돌려준다.

        lookup/pivot/latest/fab 네 단계는 각각 수 GB 를 쓰고 끝난다. gc 만으로는
        allocator arena 에 남아 RSS 가 단계마다 누적됐고, 마지막에는 워치독이
        비울 캐시조차 없는 상태로 임계값에 붙어 있었다(`freed 0.0MB [nothing]`).
        단계 경계는 참조가 확실히 끊긴 지점이라 여기서 반환하는 게 가장 안전하다.
        """
        try:
            from core import memory_trim

            return memory_trim.trim(reason=f"cache_stage:{stage_id}")
        except Exception:
            try:
                gc.collect()
            except Exception:
                pass
            return {}

    def _stage_run(stage_id: str, label: str, fn) -> bool:
        _cancel_point()
        if job_id:
            stage_started(job_id, stage_id)
        try:
            ok = bool(fn())
            released = int((_release_stage_memory(stage_id) or {}).get("released_bytes") or 0)
            if released > 0:
                logger.info("[cache pipeline] %s 단계 후 %.0fMB OS 반환",
                            stage_id, released / (1024 * 1024))
            if job_id:
                stage_finished(job_id, stage_id, ok=ok,
                               detail={"completed": ok, "released_mb": round(released / (1024 * 1024), 1)})
            record("scan", f"[제품별 캐싱] {label} " + ("완료" if ok else "일부 실패"),
                   ok=ok, product=requested,
                   detail={"job_id": job_id, "stage": stage_id,
                           "phase": "finished" if ok else "failed"})
            if not ok:
                results["ok"] = False
            return ok
        except _PipelineCancelled:
            # 중단도 단계가 끝난 것이다 — 여기서 안 돌려주면 취소한 빌드의 작업
            # 메모리가 그대로 남는다.
            _release_stage_memory(stage_id)
            if job_id:
                stage_finished(job_id, stage_id, ok=False, detail={"cancelled": True})
            raise
        except Exception as exc:
            results["ok"] = False
            _release_stage_memory(stage_id)
            if job_id:
                stage_finished(job_id, stage_id, ok=False, detail={"error": str(exc)})
            record("scan", f"[수동 캐싱] {label} 큐 등록 실패: {exc}", ok=False,
                   product=requested,
                   detail={"job_id": job_id, "stage": stage_id, "phase": "failed"})
            return False

    try:
        rows = []
        for prod in products:
            try:
                rows.append({"product": prod, "source": str(_product_path(prod))})
            except Exception as exc:
                rows.append({"product": prod, "error": str(exc)})
                results["ok"] = False

        def _build_lookup():
            ok = True
            for row in rows:
                _cancel_point()
                if row.get("source"):
                    source = Path(row["source"]).resolve()
                    target = str(source)
                    row["lookup"] = _ml_table_lookup.enqueue_build(
                        source, immediate=True, local_only=local_only)

                    def _lookup_finished() -> bool:
                        # Wait for the request itself, not just the published
                        # generation.  The previous generation deliberately
                        # remains ready while rebuilding, so readiness alone
                        # can otherwise advance this product to the next kind
                        # while its lookup worker is still active.
                        snapshot = _ml_table_lookup.build_queue_snapshot()
                        current = str(snapshot.get("current") or "")
                        queued = {str(value) for value in (snapshot.get("queued") or [])}
                        retrying = {str(value) for value in (snapshot.get("retrying") or [])}
                        active = bool(snapshot.get("running") and current == target)
                        # A definitive failed attempt with no queued/running/
                        # delayed retry must end this stage now.  Previously the
                        # manual scan sat until the six-hour stage timeout even
                        # though the lookup coordinator had already exhausted
                        # its retry budget and exposed the exact error.
                        failed = bool(
                            not active
                            and target not in queued
                            and target not in retrying
                            and str(snapshot.get("last_source") or "") == target
                            and str(snapshot.get("last_error") or "")
                        )
                        if failed:
                            raise RuntimeError(str(snapshot.get("last_error") or "lookup build failed"))
                        return (
                            not active
                            and target not in queued
                            and target not in retrying
                            and _ml_table_lookup.cache_status(source).get("status") == "fresh"
                        )
                    finished = _wait_for(
                        f"랏 lookup · {row['product']}",
                        _lookup_finished,
                    )
                    row["lookup_ready"] = bool(finished)
                    ok = ok and bool(finished)
            return ok

        _stage_run("lookup_build", "랏 lookup", _build_lookup)

        def _build_pivot():
            ok = True
            for row in rows:
                _cancel_point()
                if row.get("source"):
                    row["pivot_queued"] = _enqueue_pivot_cache_build(
                        row["product"], reason="manual_queue", immediate=True,
                        local_only=local_only)
                    finished = _wait_for(
                        f"SplitTable pivot · {row['product']}",
                        lambda prod=row["product"]: _pivot_cache_build_state(prod) != "building",
                    )
                    try:
                        ready = finished and not _pivot_cache_needs_build(
                            row["product"], Path(row["source"]))
                    except Exception:
                        ready = False
                    row["pivot_ready"] = bool(ready)
                    ok = ok and bool(ready)
            return ok

        _stage_run("pivot_build", "SplitTable pivot", _build_pivot)

        def _build_latest():
            results["latest_queued"] = bool(_enqueue_manual_lot_progress_refresh(products))
            scanner_done = _wait_for(
                "WIP latest-lot", lambda: not _MANUAL_LATEST_REFRESH_RUNNING)
            if not scanner_done:
                return False
            canonical = _refresh_dashboard_latest_v4(
                products, force=force, job_id=job_id)
            results["dashboard_latest_v4"] = canonical
            return bool(canonical.get("ok"))

        _stage_run("latest_lot", "WIP latest-lot", _build_latest)

        def _build_fab():
            include_all = _foreground_global_fab_scan_enabled()
            ok = True
            for row in rows:
                _cancel_point()
                if not row.get("source"):
                    continue
                _ml_product, _ov, fab_source = _current_fab_override(row["product"])
                row["fab_index_queued"] = _enqueue_fab_lot_index_build(
                    row["product"], fab_source, include_all=include_all,
                    reason="manual_queue", immediate=True, local_only=local_only)
                finished = _wait_for(
                    f"FAB latest · {row['product']}",
                    lambda prod=row["product"]: _pivotless_fab_build_done(prod),
                )
                meta = _fab_lot_index_read_meta(row["product"])
                ready = bool(finished and meta.get("built_at") and meta.get("root_col"))
                row["fab_index_ready"] = ready
                ok = ok and ready
            return ok

        def _pivotless_fab_build_done(prod: str) -> bool:
            canonical = (_canonical_mltable_product_name(prod, allow_bare=True)
                         or str(prod or "").strip().upper())
            with _FAB_IDX_BUILD_LOCK:
                return canonical not in _FAB_IDX_BUILD_INPROGRESS

        _stage_run("fab_index", "FAB latest 인덱스", _build_fab)
        results["products"] = rows
        results["queued_products"] = sum(1 for row in rows if row.get("source"))
        results["detail"] = (
            f"제품별 캐시 파이프라인을 처리했습니다: {results['queued_products']}개 제품 · "
            "lookup → SplitTable → WIP latest → FAB latest."
        )
        record("scan", f"[제품별 캐싱] 처리 완료 — {results['detail']}",
               ok=bool(results["ok"]), product=requested,
               detail={"job_id": job_id, "mode": "product_pipeline"})
        return results
    except _PipelineCancelled as exc:
        cancelled = True
        results["ok"] = False
        results["cancelled"] = True
        results["detail"] = str(exc)
        record("scan", f"[제품별 캐싱] 중단 — {exc}. 현재 안전 배치 이후 다음 단계는 시작하지 않습니다.",
               ok=False, product=requested,
               detail={"job_id": job_id, "cancelled": True})
        return results
    finally:
        if owns_job and job_id:
            try:
                from core.cache_event_log import finish_job
                finish_job(job_id, ok=bool(results.get("ok")),
                           detail={"product": requested, "mode": "product_pipeline",
                                   "cancelled": cancelled})
            except Exception:
                logger.debug("manual cache enqueue job finish failed", exc_info=True)
        if owns_job:
            with _UNIFIED_SCAN_LOCK:
                _UNIFIED_SCAN_BUSY = False


def _run_unified_scan(product: str, force: bool, job_id: str = "", *,
                      owns_job: bool = True, local_only: bool = False) -> dict:
    """FAB 매칭 캐시 → 제품 원본 RAM 캐시 → Root lot RAM 캐시를 순서대로 갱신.

    owns_job=False 면 job 종료/busy 해제를 호출자가 맡는다(전체 셋업이 이 함수를
    Phase B 로 감싸 쓰므로, 감싼 쪽이 끝나기 전에 작업이 '완료'로 닫히면 안 된다)."""
    # 구형 match/product-RAM/root-RAM 직렬 스캔 대신 필수 공유 디스크
    # 산출물만 일반 큐에 넣는다. 아래 본문은 과거 작업 이력 호환용으로 보존한다.
    return _enqueue_required_split_caches(product, force, job_id, owns_job=owns_job,
                                          local_only=local_only)

    global _UNIFIED_SCAN_BUSY
    results: dict = {"ok": True, "product": product}
    _label = product or "전체 제품"
    try:
        from core.cache_event_log import record as _log_event
    except Exception:
        _log_event = None

    def _log(cat, msg, *, ok=True, detail=None, stage="", phase=""):
        """통합 스캔 이벤트를 작업/단계와 함께 남긴다.

        캐시 이벤트 로그는 일반 캐시 이벤트도 함께 보관하므로, 수동 스캔의
        각 단계가 어느 작업에서 나온 것인지 구분할 수 있는 식별자를 넣는다.
        프런트는 이 값을 사용해 FAB / 제품 원본 / Root lot 이력을 별도 열에
        표시한다.
        """
        if _log_event:
            try:
                event_detail = dict(detail or {})
                if job_id:
                    event_detail["job_id"] = job_id
                if stage:
                    event_detail["stage"] = stage
                if phase:
                    event_detail["phase"] = phase
                _log_event(cat, msg, ok=ok, product=product, detail=event_detail or None)
            except Exception:
                pass

    # 단계별 결과 요약 — 마지막에 '무엇이 되고 무엇이 실패했는지'를 한 줄로 남긴다.
    stage_outcomes: dict[str, str] = {}

    def _err_text(payload: dict) -> str:
        """실패 사유를 사람이 읽을 수 있는 한 줄로.

        주의: 최상위 `reason` 은 트리거 사유("unified_scan")라 실패 원인이 아니다 —
        여기서 쓰면 화면에 'unified_scan' 이 실패 사유처럼 뜬다. 실제 원인은
        top-level error/detail 또는 제품별 결과의 reason 에 있다."""
        payload = payload or {}
        for key in ("detail", "error", "last_error"):
            value = str(payload.get(key) or "").strip()
            if value:
                return value
        rows = payload.get("products") or []
        bad = [row for row in rows if isinstance(row, dict) and not row.get("ok")]
        if bad:
            parts = [f"{row.get('product') or row.get('file') or '?'}: "
                     f"{row.get('reason') or row.get('error') or '실패'}"
                     for row in bad[:3]]
            more = f" 외 {len(bad) - 3}개" if len(bad) > 3 else ""
            return "; ".join(parts) + more
        if not rows:
            return ("처리 대상 제품이 없습니다 — 제품명이 올바른지, ML_TABLE 원본 파일이 "
                    "존재하는지 확인하세요")
        return "원인 미상(서버 터미널 로그 확인 필요)"

    try:
        # 이 함수 자체가 이미 background thread다. 각 stage가 다시 별도 thread를
        # 띄우면 FAB scan + 전체 product collect + root warmup이 동시에 실행되어
        # 개발서버 OOM의 직접 원인이 된다. 여기서는 stage를 실제 완료 순서로 직렬화.
        # 1. FAB match cache
        if job_id:
            from core.cache_event_log import stage_started
            stage_started(job_id, "match_cache")
        _log("scan", f"[수동 스캔] 1/3 FAB 매칭 캐시 갱신 시작 ({_label})",
             stage="match_cache", phase="started")
        try:
            match_products = _match_cache_products(product)
            started, _status = _begin_match_cache_job(
                match_products, force=force, reason="unified_scan"
            )
            if started:
                # followups=False — 파생 캐시(통합 latest-lot export 등)는 3/3 직전에
                # 돈다. 여기서 돌리면 제품이 100% 끝나도 이 단계가 안 끝나 화면이
                # 'FAB 100% 인데 다음 단계로 안 넘어감' 상태로 보인다.
                results["match_cache"] = _run_started_match_cache_job(
                    match_products,
                    force,
                    reason="unified_scan",
                    refresh_plan_risk=False,
                    job_id=job_id,
                    followups=False,
                )
            else:
                results["match_cache"] = _wait_for_cache_job(
                    _match_cache_job_status, stage="match_cache",
                    stage_label="FAB 매칭 캐시", job_id=job_id,
                )
            mc = results["match_cache"]
            mc_ok = bool(mc.get("ok"))
            stage_outcomes["1/3 FAB 매칭"] = "완료" if mc_ok else "실패"
            if job_id:
                from core.cache_event_log import stage_finished
                stage_finished(job_id, "match_cache", ok=mc_ok,
                               detail={"products": len(mc.get("products") or []),
                                       "error": "" if mc_ok else _err_text(mc)})
            _log("scan",
                 f"[수동 스캔] 1/3 FAB 매칭 캐시 "
                 + (f"완료 — {len(mc.get('products') or [])}개 제품" if mc_ok
                    else f"실패: {_err_text(mc)}")
                 + f" ({_label})",
                 ok=mc_ok,
                 detail={"products": len(mc.get("products") or [])},
                 stage="match_cache", phase="finished" if mc_ok else "failed")
        except Exception as e:
            results["match_cache"] = {"ok": False, "error": str(e)}
            stage_outcomes["1/3 FAB 매칭"] = "실패"
            if job_id:
                from core.cache_event_log import stage_finished
                stage_finished(job_id, "match_cache", ok=False, detail={"error": str(e)})
            _log("scan", f"[수동 스캔] 1/3 FAB 매칭 캐시 실패 ({_label}): {e}", ok=False,
                 stage="match_cache", phase="failed")

        # 2. Product RAM cache
        #    비활성(설정/서버 역할에서 꺼짐)이면 job 을 시작하지도 않고 즉시 다음
        #    단계로 넘어간다. 예전엔 꺼져 있어도 전 제품을 한 바퀴 돌며 "disabled"
        #    행만 쌓았고, 다른 제품 RAM 작업이 돌고 있으면 _wait_for_cache_job 이
        #    최대 1시간을 기다렸다 — 개발서버처럼 항상 비활성인 곳에서 3/3 랏캐시가
        #    시작조차 못 하던 원인이다.
        if job_id:
            from core.cache_event_log import stage_started
            stage_started(job_id, "product_ram")
        pc_disabled = not _product_ram_cache_available()
        if pc_disabled:
            results["product_cache"] = {"ok": True, "skipped": True, "reason": "disabled",
                                        "products": []}
            stage_outcomes["2/3 제품 원본"] = "건너뜀"
            if job_id:
                from core.cache_event_log import stage_skipped
                stage_skipped(job_id, "product_ram", {"disabled": True, "reason": "설정에서 꺼짐"})
            _log("scan",
                 "[수동 스캔] 2/3 제품 원본 RAM 캐시 건너뜀(비활성 — 설정에서 꺼짐. "
                 f"랏캐시와는 무관하며 랏캐시는 계속 갱신됩니다) ({_label})",
                 detail={"disabled": True}, stage="product_ram", phase="finished")
        else:
            _log("scan", f"[수동 스캔] 2/3 제품 원본 RAM 캐시 갱신 시작 ({_label})",
                 stage="product_ram", phase="started")
            try:
                product_ram_products = _product_ram_cache_products(product)
                started, _status = _begin_product_ram_cache_job(
                    product_ram_products, force=force, reason="unified_scan"
                )
                if started:
                    results["product_cache"] = _run_started_product_ram_cache_job(
                        product_ram_products, force, reason="unified_scan"
                    )
                else:
                    results["product_cache"] = _wait_for_cache_job(
                        _product_ram_cache_job_status, stage="product_ram_cache",
                        stage_label="제품 원본 RAM 캐시", job_id=job_id,
                    )
                pc = results["product_cache"]
                pc_ok = bool(pc.get("ok"))
                stage_outcomes["2/3 제품 원본"] = "완료" if pc_ok else "실패"
                if job_id:
                    from core.cache_event_log import stage_finished
                    stage_finished(job_id, "product_ram", ok=pc_ok,
                                   detail={"products": len(pc.get("products") or []),
                                           "error": "" if pc_ok else _err_text(pc)})
                _log("scan",
                     f"[수동 스캔] 2/3 제품 원본 RAM 캐시 "
                     + (f"완료 — {len(pc.get('products') or [])}개 제품" if pc_ok else
                        f"실패: {_err_text(pc)}")
                     + f" ({_label})",
                     ok=pc_ok,
                     detail={"products": len(pc.get("products") or [])},
                     stage="product_ram", phase="finished" if pc_ok else "failed")
            except Exception as e:
                results["product_cache"] = {"ok": False, "error": str(e)}
                stage_outcomes["2/3 제품 원본"] = "실패"
                if job_id:
                    from core.cache_event_log import stage_finished
                    stage_finished(job_id, "product_ram", ok=False, detail={"error": str(e)})
                _log("scan", f"[수동 스캔] 2/3 제품 원본 RAM 캐시 실패 ({_label}): {e}", ok=False,
                     stage="product_ram", phase="failed")

        # 3. 랏(lookup) 디스크 캐시 → Root lot RAM 적재
        if job_id:
            from core.cache_event_log import stage_started
            stage_started(job_id, "root_lot_ram")
        _log("scan", f"[수동 스캔] 3/3 랏(lookup) 캐시 빌드 + Root lot RAM 적재 시작 ({_label})",
             stage="root_lot_ram", phase="started")
        try:
            # FAB 파생 캐시 — Root lot 예열이 통합 latest-lot 캐시에서 예열 후보
            # root 를 고르므로 여기서(실제로 필요한 지점에서) 먼저 갱신한다.
            # 1/3 안에 있던 것을 옮긴 것이며, 진행 로그가 함께 나간다.
            if not _MATCH_CACHE_STOP.is_set():
                results["match_followups"] = _match_cache_followups(
                    force, job_id=job_id, built=_match_cache_products(product))
            # 랏캐시(디스크 파티션)는 운영/개발이 함께 읽는 공유 산출물이라 서버
            # 역할과 무관하게 항상 만든다. RAM 적재만 조회를 서빙하는 운영 서버
            # 전용이다 — 예전엔 root RAM 이 비활성이면 이 단계 전체를 건너뛰어,
            # 개발(worker) 서버에서 수동 스캔을 돌려도 랏캐시가 만들어지지 않았다.
            lookup = _ensure_scan_lookup_caches(product, job_id=job_id)
            results["lookup_cache"] = lookup
            lookup_ok = bool(lookup.get("ok"))
            # 통합 스캔의 기본 목적은 공유 디스크 lookup 갱신이다. pivot이 먼저
            # 읽히는 현재 검색 경로에서 root RAM 자동 예열은 중복이므로 명시적으로
            # scheduler를 opt-in한 배포에서만 함께 적재한다. 개별 수동 적재 API는
            # 그대로 유지해 캐시관리/진단 계약을 깨지 않는다.
            try:
                from core.runtime_limits import splittable_root_lot_ram_cache_scheduler_enabled
                ram_auto_enabled = bool(splittable_root_lot_ram_cache_scheduler_enabled())
            except Exception:
                ram_auto_enabled = False
            ram_disabled = (
                not _ml_table_lookup.root_ram_cache_available() or not ram_auto_enabled
            )
            if ram_disabled:
                results["root_lot_cache"] = {
                    "ok": lookup_ok, "skipped": True, "reason": "ram_disabled",
                    "products": [], "warmed_products": 0, "build_pending": 0,
                    "lookup": lookup,
                }
            else:
                results["root_lot_cache"] = _ml_table_lookup.refresh_root_lot_ram_cache(
                    product=product, force=force, load_now=True)
                results["root_lot_cache"] = _wait_for_root_lookup_caches(
                    results["root_lot_cache"], product=product, job_id=job_id)
            rc = results["root_lot_cache"]
            pending = int(rc.get("build_pending") or 0)
            warmed = int(rc.get("warmed_products") or 0)
            # 원본 lookup 캐시가 아직 빌드 중이면 예열은 '대기'다(실패 아님) — 빌드 완료
            # 후 자동 적재된다. 대기 개수를 표시해 조용한 미적재로 오인하지 않게 한다.
            note = (f" · 빌드 대기 {pending}개(원본 lookup 캐시 빌드 완료 후 자동 적재)"
                    if pending else "")
            lookup_note = ("랏캐시 최신(빌드 불필요)" if lookup.get("skipped")
                           else f"랏캐시 {lookup.get('built') or 0}/{lookup.get('total') or 0} 빌드")
            rc_ok = lookup_ok and (ram_disabled or bool(rc.get("ok")))
            if not lookup_ok:
                phase_label = f"실패: {lookup.get('detail') or _err_text(lookup)}"
            elif ram_disabled:
                phase_label = (f"{lookup_note} 완료 · RAM 적재는 건너뜀"
                               "(이 서버 역할/설정에서 꺼짐 — 조회를 서빙하는 운영 서버 전용)")
            elif rc_ok:
                phase_label = f"{lookup_note} 완료 · {warmed}개 제품 RAM 예열{note}"
            else:
                phase_label = f"RAM 예열 실패: {_err_text(rc)}"
            stage_outcomes["3/3 랏캐시"] = "완료" if rc_ok else "실패"
            if job_id:
                from core.cache_event_log import stage_finished
                stage_finished(job_id, "root_lot_ram", ok=rc_ok,
                               detail={"products": len(rc.get("products") or []),
                                       "max_gb": rc.get("max_gb"),
                                       "ram_disabled": ram_disabled,
                                       "lookup_built": lookup.get("built") or 0,
                                       "lookup_total": lookup.get("total") or 0,
                                       "build_pending": pending, "warmed": warmed,
                                       "error": "" if rc_ok else
                                       (lookup.get("detail") or _err_text(rc))})
            _log("scan", f"[수동 스캔] 3/3 랏(lookup) 캐시 {phase_label} ({_label})",
                 ok=rc_ok,
                 detail={"products": len(rc.get("products") or []),
                         "max_gb": rc.get("max_gb"), "ram_disabled": ram_disabled,
                         "lookup_built": lookup.get("built") or 0,
                         "build_pending": pending, "warmed": warmed},
                 stage="root_lot_ram", phase="finished" if rc_ok else "failed")
        except Exception as e:
            results["root_lot_cache"] = {"ok": False, "error": str(e)}
            stage_outcomes["3/3 랏캐시"] = "실패"
            if job_id:
                from core.cache_event_log import stage_finished
                stage_finished(job_id, "root_lot_ram", ok=False, detail={"error": str(e)})
            _log("scan", f"[수동 스캔] 3/3 랏(lookup) 캐시 실패 ({_label}): {e}", ok=False,
                 stage="root_lot_ram", phase="failed")

        # 전체 성공 판정 — 예전엔 any() 라 3개 중 2개가 실패해도 '완료(ok=True)'로
        # 보였다. 하나라도 실패하면 실패로 본다(건너뜀은 실패가 아니다).
        failed = [name for name, outcome in stage_outcomes.items() if outcome == "실패"]
        results["ok"] = not failed
        results["stages"] = dict(stage_outcomes)
        summary = " · ".join(f"{name} {outcome}" for name, outcome in stage_outcomes.items())
        _log("scan",
             (f"[수동 스캔] 전체 완료 ({_label}) — {summary}" if not failed
              else f"[수동 스캔] 실패 ({_label}) — {summary} · 실패 단계: {', '.join(failed)}"),
             ok=results["ok"], detail={"stages": dict(stage_outcomes)})
    finally:
        if owns_job:
            if job_id:
                try:
                    from core.cache_event_log import finish_job
                    finish_job(job_id, ok=bool(results.get("ok")),
                               detail={"product": product, "stages": results.get("stages") or {}})
                except Exception:
                    logger.debug("unified scan job tracker finish failed", exc_info=True)
            with _UNIFIED_SCAN_LOCK:
                _UNIFIED_SCAN_BUSY = False
    return results


_PRODUCT_CACHE_PIPELINE_STAGES = [
    ("lookup_build", "root별 ML_TABLE lookup"),
    ("pivot_build", "root별 SplitTable pivot"),
    ("latest_lot", "WIP latest-lot"),
    ("fab_index", "root별 FAB latest 인덱스"),
]


def _submit_product_cache_scan(product: str, *, force: bool, source: str,
                               on_started=None, on_finished=None,
                               local_only: bool = False) -> dict:
    """Queue one product's four cache stages as a single serial pipeline.

    Manual and scheduled work share this entry point so a server never starts
    independent per-cache schedules.  The scan gate serializes pipelines and
    the pipeline itself waits for each cache kind before advancing to the next.

    local_only=True 는 관리자가 누른 수동 캐싱 전용이다 — 네 단계를 이 서버에서
    직접 돌린다. 자동(스케줄러) 캐싱은 종전대로 개발 워커로 오프로드할 수 있다.
    """
    product = str(product or "").strip()
    _reap_dead_unified_scan()
    scheduled = str(source or "").strip().lower() == "scheduler"
    label = (
        f"자동 제품 캐싱 ({product})"
        if scheduled else f"SplitTable 통합 캐시 스캔 ({product or '전체 제품'})"
    )

    def _start() -> dict:
        global _UNIFIED_SCAN_BUSY, _UNIFIED_SCAN_JOB_ID, _UNIFIED_SCAN_THREAD
        from core.cache_event_log import start_job
        job_id = start_job(
            "scheduled_product_cache" if scheduled else "unified_scan",
            label,
            list(_PRODUCT_CACHE_PIPELINE_STAGES),
            product=product,
        )
        with _UNIFIED_SCAN_LOCK:
            _UNIFIED_SCAN_BUSY = True
        _UNIFIED_SCAN_JOB_ID = job_id
        _UNIFIED_SCAN_THREAD = threading.current_thread()
        if on_started:
            try:
                on_started(product, job_id)
            except Exception:
                logger.debug("product cache on_started callback failed", exc_info=True)
        result: dict = {"ok": False, "error": "pipeline_not_started"}
        try:
            result = _run_unified_scan(product, force, job_id, local_only=local_only)
            return result
        finally:
            # _run_unified_scan 도 finally 에서 풀지만, 여기서 한 번 더 확실히 푼다 —
            # busy 가 남으면 scan-status 가 영원히 running 이라 화면이 안 끝난다.
            with _UNIFIED_SCAN_LOCK:
                _UNIFIED_SCAN_BUSY = False
            if on_finished:
                try:
                    on_finished(product, result)
                except Exception:
                    logger.debug("product cache on_finished callback failed", exc_info=True)

    out = _submit_scan(
        "scheduled_product_cache" if scheduled else "unified_scan",
        label,
        _start,
        product=product,
        source=source or "manual",
        dedupe_key=f"product_cache_pipeline:{product}",
    )
    return {**out, "product": product, "label": label}


@router.post("/ram-cache/unified-scan")
def unified_scan(req: UnifiedScanReq, _perm=Depends(require_page_manager("splittable"))):
    """관리자: 선택 제품의 필수 공유 캐시 작업을 1회 일반 큐에 등록.

    스캔은 서버당 하나만 돈다. 다른 스캔(수동/전체 셋업/예약)이 진행 중이면
    거절하지 않고 대기열에 넣고, 앞 작업이 끝나면(실패해도) 이어서 실행한다.

    작업(job) 은 **큐에서 꺼내 실제로 시작하는 순간** 만든다 — 대기 중에 미리
    만들면 heartbeat 이 없어 작업 추적기가 정지로 보고 실패 처리한다."""
    product = str(req.product or "").strip()
    force = bool(req.force)
    # 수동 캐싱은 누른 서버에서 바로 돈다 — 개발 워커로 넘기지 않는다. 진행률과
    # 중단 버튼이 모두 이 서버의 scan gate 를 보고 있으므로, 실작업만 다른 서버로
    # 가면 화면은 "등록됨" 인데 아무 진행도 안 보이고 중단도 먹지 않는다.
    out = _submit_product_cache_scan(product, force=force, source="manual",
                                     local_only=True)
    return {
        **out,
        "product": product,
        "detail": (f"수동 캐싱 — 이 서버에서 제품별 4단계 캐시를 순서대로 처리합니다. "
                   f"({product or '전체 제품'}) " + str(out.get("detail") or "")).strip(),
    }


# ── 전체 셋업 (초기 1회, 운영 로컬·고자원 빠른 캐싱) ───────────────────────
#   개발 워커로 오프로드하지 않고 운영 서버에서 직접, 큰 메모리로 전 제품
#   캐시(랏 lookup → 매칭 → 제품RAM → 예열)를 한 번에 빌드한다. 관리자 전용.
#
#   workers 기본값은 1 이다 (2026-07-28). "한 서버에서 캐싱 작업은 한 번에 한 제품
#   한 가지"라는 운영 규칙에 이 버튼만 예외로 남겨 두면, 전체 셋업 중에는 여전히
#   여러 제품의 랏캐시가 동시에 돌아 RSS 피크가 겹친다. 초기 반입처럼 속도가
#   우선일 때만 FLOW_FULL_SETUP_WORKERS 로 올린다(예: 5).
#
#   **청크·배치 크기도 기본으로 건드리지 않는다 (2026-07-31).** 예전에는 속도를
#   위해 랏캐시 청크를 4→32, 매칭 배치를 300→2000 으로 키워서 넘겼다. 그런데 이
#   두 값이 곧 peak RAM 이다 — 랏캐시 peak ≈ "청크 개수만큼의 root wide 데이터",
#   매칭 peak ≈ "배치 1개 크기". 8배·6.7배로 키우면 peak 도 그만큼 커진다.
#   메모리 가드는 **청크/배치 사이에서만** 확인하므로 한 단위 안에서 벌어지는
#   초과는 못 막는다 — 그래서 28GB 호스트에서 상한을 넘겨 캐시가 축출됐다.
#   전체 셋업은 "제품별로 순차 전체 캐싱"이면 충분하다는 게 요구사항이므로,
#   평소와 같은 메모리 프로파일로 돌리고 속도가 필요할 때만 env 로 올린다.

def _full_setup_config() -> dict:
    """전체 셋업 실행 파라미터. 0 = '평소 설정을 그대로 쓴다(오버라이드 없음)'."""
    return {
        # 한 서버에서는 제품/캐시 파이프라인을 반드시 하나씩 실행한다. 과거
        # FLOW_FULL_SETUP_WORKERS 값이 남아 있어도 전체 셋업만 병렬로 새는 예외를
        # 만들지 않는다.
        "workers": 1,
        "memory_gb": _float_env_clamped("FLOW_FULL_SETUP_MEMORY_GB", 20.0, 2.0, 512.0),
        "lookup_chunk": int(_float_env_clamped("FLOW_FULL_SETUP_LOOKUP_CHUNK", 0.0, 0.0, 500.0)),
        "match_batch": int(_float_env_clamped("FLOW_FULL_SETUP_MATCH_BATCH_ROOTS", 0.0, 0.0, 100000.0)),
    }


def _full_setup_env_apply(cfg: dict) -> dict:
    """전체 셋업 동안만 적용할 env 오버라이드. 원래 값 dict 반환(복원용)."""
    overrides = {
        "FLOW_WORKER_OFFLOAD": "0",                                  # 개발 오프로드 금지 → 운영 로컬 처리
        "FLOW_PROCESS_MEMORY_LIMIT_GB": str(cfg["memory_gb"]),       # 메모리 가드 상한 고정
    }
    # 청크/배치는 명시 지정(>0)일 때만 덮어쓴다. env 가 캐시관리 ⚙ 설정보다
    # 우선순위가 높으므로, 무조건 넣으면 관리자가 ⚙ 에서 낮춰둔 값이 전체 셋업
    # 동안 조용히 무시된다 — 메모리를 지키려고 낮춘 설정이 정작 가장 무거운
    # 작업에서만 안 먹는 셈이었다.
    if int(cfg.get("lookup_chunk") or 0) > 0:
        overrides["FLOW_ML_TABLE_LOOKUP_CACHE_BUILD_CHUNK_SIZE"] = str(cfg["lookup_chunk"])
    if int(cfg.get("match_batch") or 0) > 0:
        overrides["FLOW_MATCH_CACHE_STREAM_BATCH_ROOTS"] = str(cfg["match_batch"])
    saved = {k: os.environ.get(k) for k in overrides}
    os.environ.update({k: str(v) for k, v in overrides.items()})
    return saved


def _full_setup_env_restore(saved: dict) -> None:
    for k, old in (saved or {}).items():
        if old is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = old


def _proc_rss_gb() -> float:
    try:
        from core.runtime_limits import process_memory_snapshot
        snap = process_memory_snapshot()
        return round(float(snap.get("process_rss_gb")
                           or snap.get("process_memory_effective_gb") or 0.0), 2)
    except Exception:
        return 0.0


def _fmt_dur_ko(sec: float) -> str:
    sec = int(max(0, sec))
    if sec < 60:
        return f"{sec}초"
    if sec < 3600:
        return f"{sec // 60}분 {sec % 60}초"
    return f"{sec // 3600}시간 {(sec % 3600) // 60}분"


def _full_setup_build_lookups_parallel(cfg: dict, job_id: str = "") -> dict:
    """전 제품의 랏(lookup) 캐시를 운영 로컬에서 N개 병렬로 직접 빌드.

    큐/오프로드/로컬-heavy-게이트(Semaphore 1)를 우회하고 build_lookup_cache 를
    직접 호출한다(파일별 빌드락으로 동일 파일 중복만 방지, 다른 제품끼리는 병렬).
    각 빌드는 청크 스트리밍이라 제품당 메모리가 제한된다."""
    import concurrent.futures as _cf
    from core.cache_event_log import record as _rec, heartbeat as _beat
    files = _ml_table_lookup._discover_ml_table_files() or []
    total = len(files)
    ok_count = 0
    failed_products: list[str] = []
    if not total:
        _rec("cache_op", "[전체셋업] 랏캐시 빌드 대상 제품이 없습니다 (ML_TABLE_*.parquet 미검출)",
             ok=False, product="", detail={"job_id": job_id} if job_id else None)
        return {"ok": False, "total": 0, "built": 0, "detail": "빌드 대상 제품 없음"}
    _rec("cache_op",
         f"[전체셋업] 랏캐시 빌드 시작 — {total:,}개 제품 · "
         f"{'순차(제품 1개씩)' if cfg['workers'] <= 1 else str(cfg['workers']) + '병렬'}(운영 로컬)"
         f" · 청크 {_ml_table_lookup._lookup_cache_build_chunk_size()}"
         f" · 메모리 상한 {cfg['memory_gb']}GB"
         f" · 이미 빌드된 제품은 건너뜀(재실행 시 이어서 진행)",
         product="")
    done = [0]
    lock = threading.Lock()
    started = time.time()

    canceled = [False]

    def _one(fp) -> bool:
        prod = Path(fp).stem
        # 제품 경계가 안전한 취소 지점이다 — 지금까지 만든 제품 캐시는 그대로
        # 남고, 남은 제품은 손대지 않은 채 다음 큐 작업으로 넘어간다.
        if _scan_cancel_requested():
            if not canceled[0]:
                canceled[0] = True
                _rec("cache_op",
                     f"[전체셋업] 중단 요청 — 남은 제품({total - done[0]:,}개)을 건너뜁니다. "
                     f"이미 빌드된 제품은 그대로 남습니다(다시 실행하면 이어서 진행).",
                     ok=False, product="", detail={"job_id": job_id} if job_id else None)
            return False
        okp = True
        err = ""
        try:
            # force=False: 이미 빌드된(fresh) 캐시는 건너뛴다 → 전체 셋업이 중간에
            # 끊겨 재실행해도 처음부터 다시 하지 않고 남은 것만 이어서 빌드(수렴 보장).
            _ml_table_lookup.build_lookup_cache(Path(fp), force=False)
        except Exception as exc:
            okp = False
            err = f"{type(exc).__name__}: {exc}"
            logger.warning("full setup lookup build failed source=%s: %s", fp, exc)
        with lock:
            done[0] += 1
            n = done[0]
            if not okp:
                failed_products.append(prod)
        elapsed = time.time() - started
        eta = (elapsed / n) * (total - n) if n else 0.0
        try:
            _beat(job_id, f"랏캐시 빌드 {n}/{total}")
            _rec("cache_op",
                 f"[전체셋업] 랏캐시 {n:,}/{total:,} — {prod}"
                 + ("" if okp else f" · 빌드 실패: {err}")
                 + f" · RSS {_proc_rss_gb()}GB · 남은 ~{_fmt_dur_ko(eta)}",
                 ok=okp, product=prod, detail={"job_id": job_id} if job_id else None)
        except Exception:
            pass
        return okp

    with _cf.ThreadPoolExecutor(max_workers=int(cfg["workers"]), thread_name_prefix="full-setup") as ex:
        for res in ex.map(_one, files):
            if res:
                ok_count += 1
    all_ok = ok_count == total
    _rec("cache_op",
         (f"[전체셋업] 랏캐시 완료 — {ok_count:,}/{total:,} 성공 · 총 {_fmt_dur_ko(time.time() - started)}"
          if all_ok else
          f"[전체셋업] 랏캐시 일부 실패 — {ok_count:,}/{total:,} 성공, {len(failed_products)}개 실패: "
          + ", ".join(sorted(failed_products)[:5]) + ("…" if len(failed_products) > 5 else "")),
         ok=all_ok, product="", detail={"job_id": job_id} if job_id else None)
    return {"ok": ok_count > 0, "all_ok": all_ok, "total": total, "built": ok_count,
            "failed": sorted(failed_products),
            "detail": "" if all_ok else f"{len(failed_products)}개 제품 랏캐시 빌드 실패"}


def _run_full_setup_scan(job_id: str = "") -> dict:
    global _UNIFIED_SCAN_BUSY
    cfg = _full_setup_config()
    saved = _full_setup_env_apply(cfg)
    try:
        from core import cache_budget
        cache_budget.invalidate()
    except Exception:
        pass
    from core.cache_event_log import record as _rec
    started = time.time()
    ok = True
    try:
        _rec("cache_op",
             f"[전체셋업] 시작 — 운영 로컬 · "
             f"{'순차(제품 1개씩)' if cfg['workers'] <= 1 else str(cfg['workers']) + '병렬'}"
             f" · 메모리 {cfg['memory_gb']}GB"
             f" · 랏캐시 청크 {_ml_table_lookup._lookup_cache_build_chunk_size()}"
             f" · 매칭 배치 {_match_cache_stream_batch_roots()} root"
             f" (랏캐시 → 매칭 → 제품RAM → 예열)",
             product="", detail={"job_id": job_id} if job_id else None)
        # Phase A: 랏(lookup) 캐시 전 제품 로컬 빌드 (가장 무겁다).
        #   기본은 제품 1개씩 순차 — FLOW_FULL_SETUP_WORKERS 로 병렬도를 올린다.
        if job_id:
            from core.cache_event_log import stage_started, stage_finished
            stage_started(job_id, "lookup_build")
        phase_a = _full_setup_build_lookups_parallel(cfg, job_id)
        if job_id:
            from core.cache_event_log import stage_finished
            stage_finished(job_id, "lookup_build", ok=bool(phase_a.get("all_ok")),
                           detail={"built": phase_a.get("built"), "total": phase_a.get("total"),
                                   "error": phase_a.get("detail") or ""})
        # Phase B: 매칭 캐시 → 제품 원본 RAM → root 예열 (기존 통합 스캔 재사용).
        #   랏캐시가 이미 빌드돼 있어 예열이 skip 없이 즉시 적재된다.
        #   _run_unified_scan 이 finally 에서 job 종료 + busy 해제까지 처리한다.
        #   force=False: 이미 최신인 매칭/제품RAM/예열은 건너뛰어 재실행 시 이어서 진행.
        phase_b = _run_unified_scan("", False, job_id, owns_job=False)
        ok = bool(phase_b.get("ok")) and bool(phase_a.get("all_ok"))
        _rec("cache_op",
             (f"[전체셋업] 전체 완료 · 총 {_fmt_dur_ko(time.time() - started)} · RSS {_proc_rss_gb()}GB"
              if ok else
              f"[전체셋업] 완료(일부 실패) · 랏캐시 {phase_a.get('built')}/{phase_a.get('total')}"
              f" · 후속 단계: {phase_b.get('stages') or {}}"
              f" · 총 {_fmt_dur_ko(time.time() - started)}"),
             ok=ok, product="")
    except Exception as exc:
        ok = False
        logger.warning("full setup scan failed: %s", exc, exc_info=True)
        try:
            _rec("cache_op", f"[전체셋업] 실패: {exc}", ok=False, product="")
            if job_id:
                from core.cache_event_log import finish_job
                finish_job(job_id, ok=False, detail={"error": str(exc)})
        except Exception:
            pass
    finally:
        _full_setup_env_restore(saved)
        try:
            from core import cache_budget
            cache_budget.invalidate()
        except Exception:
            pass
        if job_id:
            try:
                from core.cache_event_log import finish_job
                finish_job(job_id, ok=ok, detail={"kind": "full_setup"})
            except Exception:
                logger.debug("full setup job tracker finish failed", exc_info=True)
        with _UNIFIED_SCAN_LOCK:
            _UNIFIED_SCAN_BUSY = False
    return {"ok": ok}


@router.post("/ram-cache/full-setup")
def full_setup_scan(_admin=Depends(require_admin)):
    """호환용 버튼: 전 제품 필수 공유 캐시를 일반 큐에 한 번 등록한다."""
    _reap_dead_unified_scan()
    cfg = _full_setup_config()
    label = "전체 셋업 (운영 로컬 · 빠른 캐싱)"

    def _start() -> dict:
        global _UNIFIED_SCAN_BUSY, _UNIFIED_SCAN_JOB_ID, _UNIFIED_SCAN_THREAD
        from core.cache_event_log import start_job
        job_id = start_job(
            "full_setup",
            label,
            [
                ("lookup_build", "root별 ML_TABLE lookup"),
                ("pivot_build", "root별 SplitTable pivot"),
                ("latest_lot", "WIP latest-lot"),
                ("fab_index", "root별 FAB latest 인덱스"),
            ],
            product="",
        )
        with _UNIFIED_SCAN_LOCK:
            _UNIFIED_SCAN_BUSY = True
        _UNIFIED_SCAN_JOB_ID = job_id
        _UNIFIED_SCAN_THREAD = threading.current_thread()
        try:
            return _enqueue_required_split_caches("", True, job_id)
        finally:
            with _UNIFIED_SCAN_LOCK:
                _UNIFIED_SCAN_BUSY = False

    out = _submit_scan("full_setup", label, _start, dedupe_key="full_setup")
    return {
        **out,
        "config": cfg,
        "effective": {
            "lookup_chunk": _ml_table_lookup._lookup_cache_build_chunk_size(),
            "match_batch_roots": _match_cache_stream_batch_roots(),
        },
        "detail": ("전체 제품 필수 디스크 캐시를 일반 작업 큐에 1회 등록합니다. "
                   "제품/Root RAM 예열과 구형 FAB 매칭 캐시는 실행하지 않습니다. "
                   "개발 워커가 없으면 운영 서버가 같은 작업을 이어받습니다. "
                   + str(out.get("detail") or "")).strip(),
    }


def _scan_gate_snapshot() -> dict:
    """서버 스캔 게이트 현황(실행 1건 + 대기열). 게이트가 없으면 빈 스냅샷."""
    try:
        from core import scan_gate
        return scan_gate.snapshot()
    except Exception:
        logger.debug("scan gate snapshot failed", exc_info=True)
        return {"running": False, "busy": False, "current": None, "pending": [],
                "depth": 0, "last": None, "worker_alive": False}


def _cache_queue_snapshot() -> dict:
    """관리 화면에 노출할 캐시 관련 실행/대기 큐의 작은 스냅샷."""
    try:
        from core import worker_dispatch
        worker = worker_dispatch.queue_snapshot(limit=50)
    except Exception:
        worker = {"depth": 0, "queued": [], "running": []}
    try:
        lookup_raw = _ml_table_lookup.build_queue_snapshot()
        lookup = {
            "running": bool(lookup_raw.get("running")),
            "current": Path(str(lookup_raw.get("current") or "")).name,
            "queued": [Path(str(value)).name for value in (lookup_raw.get("queued") or [])],
            "last_error": str(lookup_raw.get("last_error") or ""),
        }
    except Exception:
        lookup = {"running": False, "current": "", "queued": [], "last_error": ""}
    try:
        root_prefetch = _ml_table_lookup.root_ram_prefetch_snapshot(limit=50)
    except Exception:
        root_prefetch = {"running": False, "depth": 0, "queued": []}
    match = _match_cache_job_status()
    product = _product_ram_cache_job_status()
    try:
        root_status = _ml_table_lookup.root_ram_cache_status(include_detail=False)
    except Exception:
        root_status = {}
    auto_schedule = _auto_product_cache_schedule_snapshot()
    return {
        "worker": worker,
        "lookup_build": lookup,
        "root_prefetch": root_prefetch,
        "match_cache": {
            "running": bool(match.get("running")),
            "current": str(match.get("current_product") or ""),
            "order": list(match.get("order") or []),
            "done": int(match.get("done") or 0),
            "total": int(match.get("total") or 0),
            # 관리자 중단 버튼 노출/표시용 — 요청이 걸린 제품과 남은 중단 건수.
            "cancel_product": str((match.get("cancel") or {}).get("product") or ""),
            "cancelled_count": int(match.get("cancelled_count") or 0),
            "remaining": max(0, int(match.get("total") or 0) - int(match.get("done") or 0)),
            "queued": max(
                0,
                int(match.get("total") or 0)
                - int(match.get("done") or 0)
                - (1 if match.get("running") and match.get("current_product") else 0),
            ),
        },
        "product_ram": {
            "running": bool(product.get("running")),
            "current": str(product.get("current_product") or ""),
            "order": list(product.get("order") or []),
            "done": int(product.get("done") or 0),
            "total": int(product.get("total") or 0),
            "remaining": max(0, int(product.get("total") or 0) - int(product.get("done") or 0)),
            "queued": max(
                0,
                int(product.get("total") or 0)
                - int(product.get("done") or 0)
                - (1 if product.get("running") and product.get("current_product") else 0),
            ),
        },
        "root_lot_ram": {
            "running": bool(root_status.get("running")),
            "current": str(root_status.get("current_product") or ""),
            "order": list(root_status.get("order") or []),
            "done": int(root_status.get("done") or 0),
            "total": len(root_status.get("order") or []),
        },
        # One schedule owns all four required cache kinds.  Each tick processes
        # one product serially, then advances the product cursor.
        "schedules": [auto_schedule],
        "auto_product_cache": auto_schedule,
    }


_PRODUCT_CACHE_STATUS_LOCK = threading.Lock()
_PRODUCT_CACHE_STATUS_SNAPSHOT: dict = {"at": 0.0, "value": None, "refreshing": False}


def _product_cache_status_snapshot(*, nonblocking: bool = False) -> dict:
    """Recent lifecycle plus real four-artifact readiness for every product.

    Directory fingerprints are more expensive than the event reducer, so the
    admin page's frequent poll reuses a short snapshot.  Ten seconds is still
    immediate for operations while avoiding repeated directory walks.
    """
    from core.cache_event_log import product_status
    now = time.monotonic()
    with _PRODUCT_CACHE_STATUS_LOCK:
        cached = _PRODUCT_CACHE_STATUS_SNAPSHOT.get("value")
        if cached is not None and now - float(_PRODUCT_CACHE_STATUS_SNAPSHOT.get("at") or 0.0) < 10.0:
            return cached
        refreshing = bool(_PRODUCT_CACHE_STATUS_SNAPSHOT.get("refreshing"))
        if nonblocking and not refreshing:
            _PRODUCT_CACHE_STATUS_SNAPSHOT["refreshing"] = True

    if nonblocking:
        if not refreshing:
            def _refresh_status() -> None:
                try:
                    _product_cache_status_snapshot(nonblocking=False)
                finally:
                    with _PRODUCT_CACHE_STATUS_LOCK:
                        _PRODUCT_CACHE_STATUS_SNAPSHOT["refreshing"] = False

            threading.Thread(
                target=_refresh_status,
                name="cache-product-status-refresh",
                daemon=True,
            ).start()
        if cached is not None:
            return cached
        # Cold page load must not wait for every product's lookup/pivot/FAB
        # directory walk.  Return the cheap lifecycle projection immediately;
        # the background refresh publishes artifact-accurate readiness for the
        # next poll.
        status = product_status(products=_match_cache_products(""))
        status["artifact_status_pending"] = True
        return status
    status = product_status(products=_match_cache_products(""))
    for row in status.get("products") or []:
        try:
            actual = _required_split_cache_status(row.get("product") or "")
        except Exception:
            continue
        by_kind = {item.get("kind"): item for item in (actual.get("kinds") or [])}
        for item in row.get("kinds") or []:
            artifact = by_kind.get(item.get("kind")) or {}
            lifecycle_state = str(item.get("state") or "")
            item["ready"] = bool(artifact.get("ready"))
            item["refreshing"] = bool(item["ready"] and lifecycle_state == "running")
            item["artifact_state"] = artifact.get("state") or "missing"
            item["artifact_done"] = int(artifact.get("done") or 0)
            item["built_ts"] = float(artifact.get("built_ts") or 0.0)
            # 산출물이 실제로 준비돼 있으면 그 캐시는 성공이다. 이벤트 로그는
            # 7일 창이고, fresh 스킵·워커 오프로드로 완료 줄 자체가 안 남는
            # 경로가 있어서 "4개 다 성공인데 3/4 준비"처럼 요약과 상세가
            # 어긋났다. 준비 여부는 산출물, 시각/소요는 로그 → 산출물 순.
            if item["ready"]:
                # A rebuild is a new unpublished generation.  Keep serving and
                # reporting the previous published artifact until the builder
                # atomically promotes its replacement; progress is exposed via
                # `refreshing`/the progress panel instead of downgrading 4/4.
                if item.get("state") != "ok":
                    item["state"] = "ok"
                built_ts = item["built_ts"]
                if built_ts and built_ts > float(item.get("success_ts") or 0.0):
                    item["success_ts"] = built_ts
                    item["last_ts"] = max(float(item.get("last_ts") or 0.0), built_ts)
                    build_seconds = float(artifact.get("build_seconds") or 0.0)
                    started_ts = float(item.get("started_ts") or 0.0)
                    if build_seconds > 0:
                        item["duration_sec"] = round(build_seconds, 1)
                    elif started_ts and built_ts >= started_ts:
                        item["duration_sec"] = round(built_ts - started_ts, 1)
                artifact_message = str(artifact.get("message") or "")
                if artifact_message and (
                    not item.get("message")
                    or built_ts >= float(item.get("success_ts") or 0.0)
                ):
                    item["message"] = artifact_message
                elif not item.get("message"):
                    item["message"] = "실제 캐시 산출물 준비됨"
            elif not item["ready"] and item.get("state") == "ok":
                # 반대 방향의 어긋남: 빌드 로그는 성공인데 산출물이 없다.
                item["state"] = "stale"
                item["message"] = "빌드 기록은 성공이지만 산출물이 준비되지 않았습니다"
        row["ready_count"] = int(actual.get("ready_count") or 0)
        row["total"] = int(actual.get("total") or 4)
        # 개별 kind 상태를 산출물 기준으로 고쳤으니 제품 요약도 다시 만든다.
        kind_rows = row.get("kinds") or []
        successes = [float(k.get("success_ts") or 0.0) for k in kind_rows if k.get("success_ts")]
        row["last_success_ts"] = max(successes) if successes else 0.0
        row["last_failure_ts"] = max(
            [float(k.get("failed_ts") or 0.0) for k in kind_rows if k.get("state") == "failed"] or [0.0])
        row["failed_kinds"] = [k.get("label") for k in kind_rows if k.get("state") == "failed"]
        row["running_kinds"] = [k.get("label") for k in kind_rows if k.get("state") == "running"]
        states = {item.get("state") for item in kind_rows}
        if "running" in states:
            row["state"] = "running"
        elif "failed" in states:
            row["state"] = "failed"
        elif row["ready_count"] == row["total"]:
            row["state"] = "ok"
        else:
            row["state"] = "partial"
        # '전체 성공 시각' = 마지막으로 완성된 캐시의 시각. 4종이 다 준비됐을
        # 때만 의미가 있다.
        row["all_success_ts"] = (
            max(successes) if successes and row["state"] == "ok"
            and len(successes) == len(kind_rows) else 0.0
        )
    status["ok_count"] = sum(1 for row in status.get("products") or [] if row.get("state") == "ok")
    status["failed_count"] = sum(1 for row in status.get("products") or [] if row.get("state") == "failed")
    status["running_count"] = sum(1 for row in status.get("products") or [] if row.get("state") == "running")
    with _PRODUCT_CACHE_STATUS_LOCK:
        # TTL starts when the expensive all-product artifact walk finishes, not
        # when it starts.  If the walk itself exceeds ten seconds, storing the
        # old start timestamp makes the fresh result immediately stale and the
        # next admin-page poll launches another full walk forever.
        _PRODUCT_CACHE_STATUS_SNAPSHOT.update({"at": time.monotonic(), "value": status})
    return status


@router.get("/ram-cache/scan-status")
def unified_scan_status():
    """통합 수동 스캔 진행 여부 — 프런트가 진행 로그를 폴링할 동안 종료 감지용.

    반환 전 죽은 스레드/정지 작업을 정리한다 — 화면이 끝나지 않는 '스캔 중'에
    갇히지 않도록, running=False 는 반드시 언젠가 온다."""
    global _UNIFIED_SCAN_BUSY
    reaped = _reap_dead_unified_scan()
    from core.cache_event_log import get_jobs
    jobs = get_jobs(recent=1)          # 내부에서 정지 작업(stale)을 실패로 확정
    # 작업 추적기가 정지로 판정했으면 busy 플래그도 함께 푼다.
    if not reaped and any(job.get("id") == _UNIFIED_SCAN_JOB_ID and job.get("status") != "running"
                          for job in jobs):
        with _UNIFIED_SCAN_LOCK:
            if _UNIFIED_SCAN_BUSY and not (_UNIFIED_SCAN_THREAD and _UNIFIED_SCAN_THREAD.is_alive()):
                _UNIFIED_SCAN_BUSY = False
    with _UNIFIED_SCAN_LOCK:
        unified_running = bool(_UNIFIED_SCAN_BUSY)
    # 게이트에 대기 중인 작업까지 running 으로 본다 — 대기 중에 running=False 를
    # 주면 프런트가 폴링을 멈추고 "완료"로 표시해 버린다(실제로는 곧 시작한다).
    gate = _scan_gate_snapshot()
    last = next((job for job in jobs if job.get("id") == _UNIFIED_SCAN_JOB_ID), None)
    return {
        "ok": True,
        "running": unified_running or bool(gate.get("busy")),
        "scan_queue": gate,
        "job_id": _UNIFIED_SCAN_JOB_ID,
        # 마지막 작업의 최종 상태 — 프런트가 '완료/실패'를 명확히 표시하는 데 쓴다.
        "last_status": str((last or {}).get("status") or ""),
        "last_stages": [
            {"id": s.get("id"), "label": s.get("label"), "status": s.get("status"),
             "error": str((s.get("detail") or {}).get("error") or "")}
            for s in ((last or {}).get("stages") or [])
        ],
        "jobs": jobs,
        "queues": _cache_queue_snapshot(),
    }


# ── 캐시 예산 조절 (톱니바퀴) ─────────────────────────────────────────────
def _cache_budget_settings_payload() -> dict:
    """저장된 예산 설정 + 실제 적용(effective) 값 + 호스트 정보."""
    from core import cache_settings, cache_budget
    saved = cache_settings.read()

    def _gb(n):
        return round(float(n or 0) / (1024 ** 3), 2)

    host_gb = 0.0
    try:
        from core.runtime_limits import system_memory_snapshot
        host_gb = round(float(system_memory_snapshot().get("system_memory_total_gb") or 0.0), 1)
    except Exception:
        pass
    env_pins = {
        "pool_fraction": "FLOW_CACHE_TOTAL_BUDGET_FRACTION" in os.environ,
        "dev_factor": "FLOW_DEV_CACHE_BUDGET_FACTOR" in os.environ,
        "root_ram_gb": "FLOW_SPLITTABLE_ROOT_LOT_RAM_CACHE_MAX_GB" in os.environ,
        "view_mb": "FLOW_SPLITTABLE_VIEW_CACHE_MAX_MB" in os.environ,
        "product_ram_gb": "FLOW_SPLITTABLE_PRODUCT_RAM_CACHE_MAX_GB" in os.environ,
        "product_ram_enabled": "FLOW_DISABLE_SPLITTABLE_PRODUCT_RAM_CACHE" in os.environ,
        "match_cache_batch_roots": "FLOW_MATCH_CACHE_STREAM_BATCH_ROOTS" in os.environ,
        "view_cold_concurrency": "FLOW_SPLITTABLE_VIEW_COLD_CONCURRENCY" in os.environ,
        "view_cold_concurrency_dev": True,
        "lookup_build_chunk_roots": "FLOW_ML_TABLE_LOOKUP_CACHE_BUILD_CHUNK_SIZE" in os.environ,
        "pivot_build_chunk_roots": "FLOW_PIVOT_CACHE_CHUNK_SIZE" in os.environ,
        "cache_speed_level": (
            "FLOW_ML_TABLE_LOOKUP_CACHE_BUILD_CHUNK_SIZE" in os.environ
            or "FLOW_PIVOT_CACHE_CHUNK_SIZE" in os.environ
        ),
        "auto_product_cache_enabled": "FLOW_SPLITTABLE_AUTO_PRODUCT_CACHE_ENABLED" in os.environ,
        "auto_product_cache_interval_minutes": "FLOW_SPLITTABLE_AUTO_PRODUCT_CACHE_INTERVAL_MINUTES" in os.environ,
    }
    return {
        "ok": True,
        "saved": saved,
        "is_dev": _ml_table_lookup._root_ram_cache_use_dev(),
        "host_total_gb": host_gb,
        "env_pins": env_pins,   # env 로 고정된 항목은 UI 편집 무시됨
        "effective": {
            "pool_fraction": round(cache_budget._pool_fraction(), 3),
            "dev_factor": round(cache_budget.worker_budget_factor(), 3),
            "pool_gb": _gb(cache_budget.pool_bytes()),
            "root_ram_gb": _gb(_ml_table_lookup._root_ram_cache_max_bytes()),
            "view_mb": round(_view_cache_max_bytes() / (1024 ** 2), 1),
            "product_ram_enabled": _product_ram_cache_available(),
            "product_ram_gb": _gb(_product_ram_cache_max_bytes()),
            "match_cache_batch_roots": _match_cache_stream_batch_roots(),
            "view_cold_concurrency": _view_cold_lane_concurrency(),
            "lookup_build_chunk_roots": _ml_table_lookup._lookup_cache_build_chunk_size(),
            "pivot_build_chunk_roots": _pivot_cache_builder.pivot_build_chunk_roots(),
            "cache_speed_level": cache_settings.cache_speed_level(
                _ml_table_lookup._root_ram_cache_use_dev()),
            "auto_product_cache_enabled": _auto_product_cache_enabled(),
            "auto_product_cache_interval_minutes": _auto_product_cache_interval_minutes(),
        },
        "defaults": {
            "pool_fraction": 0.45,
            "dev_factor": 0.35,
            "product_ram_enabled": False,
            "view_mb": None,
            "match_cache_batch_roots": MATCH_CACHE_STREAM_BATCH_ROOTS_DEFAULT,
            "view_cold_concurrency": 3,
            "view_cold_concurrency_dev": 1,
            "lookup_build_chunk_roots": _ml_table_lookup.lookup_cache_build_chunk_default(),
            "pivot_build_chunk_roots": _pivot_cache_builder.pivot_build_chunk_default(),
            "cache_speed_level": 1,
            "auto_product_cache_enabled": False,
            "auto_product_cache_interval_minutes": _AUTO_PRODUCT_CACHE_DEFAULT_INTERVAL_MINUTES,
        },
        "auto_schedule": _auto_product_cache_schedule_snapshot(),
        # 검색 동시성 레인 현황 — 톱니바퀴에서 현재 몇 개가 돌고 있는지 함께 보여준다.
        "cold_lane": _view_cold_lane_stats(),
    }


@router.get("/cache-budget/settings")
def get_cache_budget_settings(request: Request):
    if not is_page_manager(current_user(request), "splittable"):
        raise HTTPException(403, "관리자 전용")
    return _cache_budget_settings_payload()


class CacheBudgetSaveReq(BaseModel):
    pool_fraction: float | None = None
    pool_fraction_dev: float | None = None
    dev_factor: float | None = None
    root_ram_gb: float | None = None
    root_ram_gb_dev: float | None = None
    view_mb: float | None = None
    view_mb_dev: float | None = None
    product_ram_enabled: bool | None = None
    product_ram_enabled_dev: bool | None = None
    product_ram_gb: float | None = None
    product_ram_gb_dev: float | None = None
    match_cache_batch_roots: int | None = None
    match_cache_batch_roots_dev: int | None = None
    view_cold_concurrency: int | None = None
    view_cold_concurrency_dev: int | None = None
    lookup_build_chunk_roots: int | None = None
    lookup_build_chunk_roots_dev: int | None = None
    pivot_build_chunk_roots: int | None = None
    pivot_build_chunk_roots_dev: int | None = None
    cache_speed_level: int | None = None
    cache_speed_level_dev: int | None = None
    auto_product_cache_enabled: bool | None = None
    auto_product_cache_enabled_dev: bool | None = None
    auto_product_cache_interval_minutes: int | None = None
    auto_product_cache_interval_minutes_dev: int | None = None


@router.post("/cache-budget/settings/save")
def save_cache_budget_settings(req: CacheBudgetSaveReq, _perm=Depends(require_page_manager("splittable"))):
    """캐시 예산 조절값 저장. 빈 값(None)은 미변경, 0/음수 GB 는 '자동'(키 삭제).
    운영/개발 분리: <key> = 운영, <key>_dev = 개발서버 전용(미설정 시 운영값 폴백)."""
    from core import cache_settings
    partial: dict = {}
    fields = req.model_dump(exclude_unset=True)

    def _put_gb(key):
        if key in fields:
            v = fields[key]
            partial[key] = None if (v is None or float(v) <= 0) else max(0.1, min(64.0, float(v)))

    if "pool_fraction" in fields:
        partial["pool_fraction"] = None if fields["pool_fraction"] is None else max(0.1, min(0.8, float(fields["pool_fraction"])))
    if "pool_fraction_dev" in fields:
        v = fields["pool_fraction_dev"]
        partial["pool_fraction_dev"] = None if (v is None or v == "") else max(0.1, min(0.8, float(v)))
    if "dev_factor" in fields:
        partial["dev_factor"] = None if fields["dev_factor"] is None else max(0.05, min(1.0, float(fields["dev_factor"])))
    _put_gb("root_ram_gb"); _put_gb("root_ram_gb_dev")
    _put_gb("product_ram_gb"); _put_gb("product_ram_gb_dev")
    if "view_mb" in fields:
        v = fields["view_mb"]
        partial["view_mb"] = None if (v is None or float(v) <= 0) else max(64.0, min(8192.0, float(v)))
    if "view_mb_dev" in fields:
        v = fields["view_mb_dev"]
        partial["view_mb_dev"] = None if (v is None or float(v) <= 0) else max(64.0, min(8192.0, float(v)))

    def _put_int(key, lo, hi):
        if key in fields:
            v = fields[key]
            partial[key] = None if (v is None or v == "" or float(v) <= 0) else int(max(lo, min(hi, float(v))))
    _put_int("match_cache_batch_roots", 1, 100000)
    _put_int("match_cache_batch_roots_dev", 1, 100000)
    # 검색 cold 레인 슬롯 — 0/빈값이면 키 삭제(코어수 기반 자동값 복귀).
    _put_int("view_cold_concurrency", _VIEW_COLD_CONCURRENCY_MIN, _VIEW_COLD_CONCURRENCY_MAX)
    # 개발 서버 슬롯은 정책상 1로 고정한다. 예전 저장값도 다음 저장 때 정리한다.
    partial["view_cold_concurrency_dev"] = 1
    # 랏캐시 빌드 청크 — 0/빈값이면 키 삭제(역할별 기본값 복귀).
    _put_int("lookup_build_chunk_roots",
             _ml_table_lookup.LOOKUP_CACHE_BUILD_CHUNK_MIN, _ml_table_lookup.LOOKUP_CACHE_BUILD_CHUNK_MAX)
    _put_int("lookup_build_chunk_roots_dev",
             _ml_table_lookup.LOOKUP_CACHE_BUILD_CHUNK_MIN, _ml_table_lookup.LOOKUP_CACHE_BUILD_CHUNK_MAX)
    # pivot 빌드 청크 — 0/빈값이면 키 삭제(역할별 기본값 운영 3 / 개발 1 복귀).
    _put_int("pivot_build_chunk_roots",
             _pivot_cache_builder.PIVOT_BUILD_CHUNK_MIN, _pivot_cache_builder.PIVOT_BUILD_CHUNK_MAX)
    _put_int("pivot_build_chunk_roots_dev",
             _pivot_cache_builder.PIVOT_BUILD_CHUNK_MIN, _pivot_cache_builder.PIVOT_BUILD_CHUNK_MAX)
    _put_int("cache_speed_level", cache_settings.CACHE_SPEED_MIN, cache_settings.CACHE_SPEED_MAX)
    _put_int("cache_speed_level_dev", cache_settings.CACHE_SPEED_MIN, cache_settings.CACHE_SPEED_MAX)
    _put_int("auto_product_cache_interval_minutes", 1, 1440)
    _put_int("auto_product_cache_interval_minutes_dev", 1, 1440)
    if "product_ram_enabled" in fields:
        partial["product_ram_enabled"] = None if fields["product_ram_enabled"] is None else bool(fields["product_ram_enabled"])
    if "product_ram_enabled_dev" in fields:
        partial["product_ram_enabled_dev"] = None if fields["product_ram_enabled_dev"] is None else bool(fields["product_ram_enabled_dev"])
    if "auto_product_cache_enabled" in fields:
        partial["auto_product_cache_enabled"] = None if fields["auto_product_cache_enabled"] is None else bool(fields["auto_product_cache_enabled"])
    if "auto_product_cache_enabled_dev" in fields:
        partial["auto_product_cache_enabled_dev"] = None if fields["auto_product_cache_enabled_dev"] is None else bool(fields["auto_product_cache_enabled_dev"])
    cache_settings.save(partial)
    try:
        from core import cache_budget
        cache_budget.invalidate()   # 풀 메모 무효화 → 즉시 반영
    except Exception:
        pass
    if any(key.startswith("auto_product_cache_") for key in partial):
        # Recompute next product/time immediately instead of waiting for the old
        # interval to expire after an operator saves the schedule.
        with _AUTO_PRODUCT_CACHE_STATE_LOCK:
            _AUTO_PRODUCT_CACHE_STATE.update({
                "next_product": "", "next_at": "", "next_at_ts": 0.0,
                "delayed": False, "delayed_reason": "",
            })
        _SEARCH_CACHE_MAINT_WAKE.set()
    return _cache_budget_settings_payload()


# ── RAM Cache 우선 Lot 등록 / 전체 목록 / 제품별 예산 ──────────────────
_RAM_CACHE_PRIORITY_FILE = PLAN_DIR / "ram_cache_priority_lots.json"


def _load_priority_lots_file() -> dict:
    """우선 lot 등록 JSON 전체를 읽는다. 파일 없으면 빈 dict."""
    try:
        return load_json(_RAM_CACHE_PRIORITY_FILE, {})
    except Exception:
        return {}


def _save_priority_lots_file(data: dict) -> None:
    save_json(_RAM_CACHE_PRIORITY_FILE, data)


@router.get("/ram-cache/priority-lots")
def get_ram_cache_priority_lots(product: str = Query("")):
    """우선 lot 등록 목록 조회."""
    product = str(product or "").strip()
    if not product:
        raise HTTPException(400, "product is required")
    data = _load_priority_lots_file()
    # 대소문자 무시 매칭
    product_upper = product.upper()
    lots = []
    for key, val in data.items():
        if str(key).strip().upper() == product_upper and isinstance(val, list):
            lots = val
            break
    # legacy note → comment 하위호환 (comment 빈 항목만)
    for entry in lots:
        if isinstance(entry, dict) and not entry.get("comment") and entry.get("note"):
            entry["comment"] = entry["note"]
    return {"ok": True, "product": product, "lots": lots}


class RamCachePriorityLotItem(BaseModel):
    lot_id: str
    purpose: str = ""
    note: str = ""      # legacy — comment 로 대체, 하위호환 위해 유지
    comment: str = ""   # 엔지니어 코멘트 (랏 운영 관리용)
    cache_enabled: bool = True  # False = 목록에 유지하되 캐싱에서 제외


class RamCachePriorityLotsSaveReq(BaseModel):
    product: str
    lots: List[RamCachePriorityLotItem] = []


@router.post("/ram-cache/priority-lots/save")
def save_ram_cache_priority_lots(
    req: RamCachePriorityLotsSaveReq,
    request: Request,
    _perm=Depends(require_page_manager("splittable")),
):
    """우선 lot 등록 목록 저장 (product 전체 교체)."""
    product = str(req.product or "").strip()
    if not product:
        raise HTTPException(400, "product is required")
    me = current_user(request)
    user_id = str(me.get("user_id") or me.get("name") or "unknown") if isinstance(me, dict) else "unknown"
    now_iso = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
    entries: list[dict] = []
    for item in req.lots:
        lot_id = str(item.lot_id or "").strip().upper()
        if not lot_id:
            continue
        root_lot_id = lot_id[:5].upper()
        entries.append({
            "lot_id": lot_id,
            "root_lot_id": root_lot_id,
            "purpose": str(item.purpose or "").strip(),
            "note": str(item.note or "").strip(),
            "comment": str(item.comment or "").strip(),
            "cache_enabled": bool(item.cache_enabled),
            "created_at": now_iso,
            "created_by": user_id,
        })
    data = _load_priority_lots_file()
    data[product] = entries
    _save_priority_lots_file(data)
    return {"ok": True, "product": product, "lots": entries}


@router.get("/ram-cache/contents")
def get_ram_cache_contents(product: str = Query("")):
    """현재 RAM 캐시에 올라간 root lot 전체 목록 (전체 목록 서브탭용)."""
    product = str(product or "").strip()
    if not product:
        raise HTTPException(400, "product is required")
    # 소스 파일 해석
    source_fp = None
    try:
        source_fp = _product_path(product)
    except Exception:
        pass
    source_path = str(Path(source_fp).resolve()) if source_fp else ""
    # 우선 lot 등록 root 목록 로드 (is_priority 판정용)
    priority_data = _load_priority_lots_file()
    priority_roots: set[str] = set()
    product_upper = product.upper()
    for key, val in priority_data.items():
        if str(key).strip().upper() == product_upper and isinstance(val, list):
            for entry in val:
                if isinstance(entry, dict):
                    root = str(entry.get("root_lot_id") or "").strip().upper()
                    if root:
                        priority_roots.add(root)
            break
    # RAM 캐시 OrderedDict 에서 항목 수집
    with _ml_table_lookup._ROOT_RAM_CACHE_LOCK:
        entries = []
        product_bytes = 0
        total_bytes = 0
        for key, entry in _ml_table_lookup._ROOT_RAM_CACHE.items():
            eb = _ml_table_lookup.root_ram_entry_bytes(entry)
            total_bytes += eb
            if source_path and key[0] != source_path:
                continue
            product_bytes += eb
            root_lot_id = str(entry.get("root_lot_id") or key[1] or "").strip().upper()
            entries.append({
                "root_lot_id": root_lot_id,
                "row_count": int(entry.get("row_count") or 0),
                "estimated_mb": round(float(eb) / (1024 * 1024), 3),
                "loaded_at": entry.get("loaded_at") or "",
                "access_count": int(entry.get("access_count") or 0),
                "cache_group": str(entry.get("cache_group") or "other"),
                "cache_sources": list(entry.get("cache_sources") or []),
                "is_priority": root_lot_id in priority_roots,
                # 개별 축출(evict) 호출에 필요 — 미스 속도 측정용 "축출→재검색" 흐름.
                "source_path": str(key[0] or ""),
            })
    max_bytes = _ml_table_lookup._root_ram_cache_max_bytes()
    return {
        "ok": True, "product": product, "entries": entries,
        "product_mb": round(product_bytes / (1024 * 1024), 1),
        "total_mb": round(total_bytes / (1024 * 1024), 1),
        "max_mb": round(max_bytes / (1024 * 1024), 1) if max_bytes else 0,
        "total_roots": len(entries),
    }


@router.get("/ram-cache/overview")
def get_ram_cache_overview(request: Request):
    """제품별 RAM 캐시 현황 요약 — 캐시 관리 페이지 상단 제품별 분해용.

    RAM 캐시 키(소스 경로)를 제품명으로 되돌려 제품 단위로 집계한다.
    캐시에 아직 안 올라간 제품도 /products 목록 기준으로 0 값으로 포함한다."""
    # 제품 목록 (source 파일 경로 → 제품명 매핑 준비)
    known: dict[str, str] = {}  # resolved source path → product name
    file_names: dict[str, str] = {}  # product name → source file name (예열 결과 매칭용)
    product_rows: dict[str, dict] = {}
    try:
        for p in list_products().get("products", []):
            name = str(p.get("name") or "")
            if not name:
                continue
            product_rows.setdefault(name, {
                "product": name, "roots": 0, "mb": 0.0,
                "priority_total": 0, "priority_cached": 0,
            })
            try:
                fp = _product_path(name)
                if fp:
                    known[str(Path(fp).resolve())] = name
                    file_names[name] = Path(fp).name
            except Exception:
                pass
    except Exception:
        pass
    # 우선 lot 등록 현황 (제품별 등록 수)
    priority_data = _load_priority_lots_file()
    priority_roots_by_product: dict[str, set[str]] = {}
    for key, val in priority_data.items():
        if not isinstance(val, list):
            continue
        prod_name = str(key).strip()
        roots = {
            str(e.get("root_lot_id") or "").strip().upper()
            for e in val if isinstance(e, dict) and e.get("cache_enabled", True)
        }
        roots.discard("")
        priority_roots_by_product[prod_name] = roots
        row = product_rows.setdefault(prod_name, {
            "product": prod_name, "roots": 0, "mb": 0.0,
            "priority_total": 0, "priority_cached": 0,
        })
        row["priority_total"] = len(roots)
    # RAM 캐시 집계
    total_bytes = 0
    with _ml_table_lookup._ROOT_RAM_CACHE_LOCK:
        for key, entry in _ml_table_lookup._ROOT_RAM_CACHE.items():
            eb = _ml_table_lookup.root_ram_entry_bytes(entry)
            total_bytes += eb
            source_path = str(key[0] or "")
            prod_name = known.get(source_path)
            if not prod_name:
                # 미등록 소스 — 파일 stem 으로 제품명 유추
                stem = Path(source_path).stem if source_path else ""
                prod_name = _canonical_mltable_product_name(stem, allow_bare=True) or (stem or "(unknown)")
            row = product_rows.setdefault(prod_name, {
                "product": prod_name, "roots": 0, "mb": 0.0,
                "priority_total": 0, "priority_cached": 0,
            })
            row["roots"] += 1
            row["mb"] += eb / (1024 * 1024)
            root_lot_id = str(entry.get("root_lot_id") or key[1] or "").strip().upper()
            if root_lot_id in priority_roots_by_product.get(prod_name, set()):
                row["priority_cached"] += 1
    # 제품별 예산 병합 — 운영/개발 구분. 실제 캐싱 로직(_root_ram_cache_product_budget)과
    # 동일하게: 개발서버에서 max_roots_dev 가 없으면 운영 max_roots 가 아니라 개발
    # 기본 target 을 적용한다(표시값이 실제 적재량과 일치하도록).
    cfg = load_json(SOURCE_CFG, {})
    budgets = cfg.get("ram_cache_product_budgets") or {}
    _use_dev = _ml_table_lookup._root_ram_cache_use_dev()
    # 기본 target — 서버 역할 반영.
    default_max_roots = int(_ml_table_lookup._root_ram_cache_default_target_roots())
    for prod_name, row in product_rows.items():
        b = budgets.get(prod_name) if isinstance(budgets, dict) else None
        custom_val = None  # 명시 설정된 값 (없으면 기본 target)
        if isinstance(b, dict):
            try:
                if _use_dev:
                    if b.get("max_roots_dev") is not None:
                        custom_val = int(b.get("max_roots_dev"))
                    # dev 예산 미설정 → 기본 target 폴백 (custom 아님)
                else:
                    if b.get("max_roots") is not None:
                        custom_val = int(b.get("max_roots"))
            except Exception:
                custom_val = None
        elif b is not None and not _use_dev:
            # 스칼라 예산 = 운영 전용. 개발서버는 기본 target.
            try:
                custom_val = int(b)
            except Exception:
                custom_val = None
        row["max_roots"] = custom_val if custom_val else default_max_roots
        row["max_roots_custom"] = bool(custom_val)
        row["mb"] = round(row["mb"], 1)
    # 마지막 예열(warmup) 사이클 결과를 제품 행에 붙인다 — "왜 이 제품은 0인가"
    # (lookup 빌드 대기 / 자원 가드 / 예산 가득 / 사용자 요청 중 중단)를 화면이
    # 그대로 읽어 줄 수 있게. 이게 없어서 0 을 보고 원인을 알 수 없었다.
    warm = _ml_table_lookup.root_ram_warmup_overview()
    warm_products = warm.get("products") or {}
    for prod_name, row in product_rows.items():
        wrow = warm_products.get(file_names.get(prod_name) or f"{prod_name}.parquet") or {}
        row["warm"] = {
            "cached_roots": int(wrow.get("cached_roots") or 0),
            "target_roots": int(wrow.get("target_roots") or 0),
            "missing_roots": int(wrow.get("missing_roots") or 0),
            "resource_skipped_roots": int(wrow.get("resource_skipped_roots") or 0),
            "budget_skipped_roots": int(wrow.get("budget_skipped_roots") or 0),
            "skip_reason": str(wrow.get("last_skip_reason") or ""),
            "cache_status": str(wrow.get("cache_status") or ""),
            # 랏캐시에는 root 가 있는데 예열 후보가 0이던 상황을 화면에서 가른다.
            "available_roots": int(wrow.get("available_roots") or 0),
            "index_target_roots": int(wrow.get("index_target_roots") or 0),
            # 우선적재 미등록 제품의 적재 순서 기준 (0 = 미적용 = 우선 lot 등록됨).
            "step_threshold": int(wrow.get("step_threshold") or 0),
            "step_threshold_roots": int(wrow.get("step_threshold_roots") or 0),
            "build_pending": bool(wrow.get("build_pending")),
            "incomplete": bool(wrow.get("incomplete")),
            "seen": bool(wrow),
        } if wrow else None
    max_bytes = _ml_table_lookup._root_ram_cache_max_bytes()
    products = sorted(product_rows.values(), key=lambda r: (-r["mb"], r["product"]))
    return {
        "ok": True,
        "products": products,
        "total_mb": round(total_bytes / (1024 * 1024), 1),
        "max_mb": round(max_bytes / (1024 * 1024), 1) if max_bytes else 0,
        "default_max_roots": default_max_roots,
        "is_dev": _use_dev,
        # 예열 상태 요약 — 이 서버(운영/개발)에서 예열이 실제로 돌고 있는지,
        # 설정한 예산이 그대로 적용됐는지. 제품별 현황 위에 한 줄로 표시한다.
        "warmup": {
            "server_role": warm.get("server_role") or "",
            "scheduler_started": bool(warm.get("scheduler_started")),
            "disabled_reason": warm.get("disabled_reason") or "",
            "last_refresh_at": warm.get("last_refresh_at") or "",
            "last_resource_guard_reason": warm.get("last_resource_guard_reason") or "",
            "last_cycle_incomplete": bool(warm.get("last_cycle_incomplete")),
            "interval_minutes": warm.get("interval_minutes"),
            "retry_minutes": warm.get("retry_minutes"),
            "budget_gb": warm.get("budget_gb"),
            "budget_setting_gb": warm.get("budget_setting_gb"),
            "budget_capped": bool(warm.get("budget_capped")),
            "load_workers": warm.get("load_workers"),
        },
        # 캐시 관리 화면의 관리자 블록 노출 기준 — 이 페이지의 관리 기능은 모두
        # splittable page manager 권한으로 보호돼 있어 그 판정을 그대로 내려준다.
        # (일반 유저에겐 주요 Lot / 전체 캐시만 보인다)
        "can_manage": is_page_manager(current_user(request), "splittable"),
    }


@router.get("/memory/overview")
def get_memory_overview():
    """프로세스 내 전 캐시 메모리 종합 현황 — 캐시 관리 페이지용.

    root RAM 캐시(관리 페이지에 원래 보이던 것)만이 아니라 filebrowser
    preview 메모리 캐시, reformatize 캐시, view payload 캐시까지 합산해
    "표시 2GB vs 실제 RSS 8.5GB" 괴리를 없앤다. 메타데이터만 반환.
    """
    def _mb(n: int | float) -> float:
        return round(float(n or 0) / (1024 * 1024), 1)

    caches: list[dict] = []

    # 1) SplitTable root RAM 캐시 (ml_table_lookup)
    with _ml_table_lookup._ROOT_RAM_CACHE_LOCK:
        root_bytes = sum(
            _ml_table_lookup.root_ram_entry_bytes(e)
            for e in _ml_table_lookup._ROOT_RAM_CACHE.values()
        )
        root_entries = len(_ml_table_lookup._ROOT_RAM_CACHE)
    caches.append({
        "key": "splittable_root_ram",
        "label": "SplitTable root RAM 캐시",
        "entries": root_entries,
        "mb": _mb(root_bytes),
        "budget_mb": _mb(_ml_table_lookup._root_ram_cache_max_bytes()),
    })

    # 1-b) SplitTable 제품 전체 RAM 캐시 (product cache) — root 캐시와 별개로
    #      제품 단위 DataFrame 을 통째로 상주시키는 층. overview 미집계 항목이었다.
    with _PRODUCT_RAM_CACHE_LOCK:
        product_bytes = sum(
            (_product_ram_cache_estimated_bytes(e.get("df"))
             if e.get("df") is not None else int(e.get("estimated_bytes") or 0))
            for e in _PRODUCT_RAM_CACHE.values()
        )
        product_entries = len(_PRODUCT_RAM_CACHE)
    caches.append({
        "key": "splittable_product_ram",
        "label": "SplitTable 제품 RAM 캐시",
        "entries": product_entries,
        "mb": _mb(product_bytes),
        "budget_mb": _mb(_product_ram_cache_max_bytes()),
    })

    # 2) SplitTable view payload 캐시
    with _VIEW_CACHE_LOCK:
        view_bytes = int(_VIEW_CACHE_BYTES)
        view_entries = len(_VIEW_CACHE)
    caches.append({
        "key": "splittable_view_payload",
        "label": "SplitTable 조회결과(payload) 캐시",
        "entries": view_entries,
        "mb": _mb(view_bytes),
        "budget_mb": _mb(_view_cache_max_bytes()),
    })

    # 3) 파일탐색기 preview 메모리 캐시
    try:
        from core import filebrowser_cache as _fb_cache
        fb = _fb_cache.memory_cache_stats()
        caches.append({
            "key": "filebrowser_preview",
            "label": "파일탐색기 preview 캐시",
            "entries": int(fb.get("entries") or 0),
            "mb": _mb(fb.get("bytes")),
            "budget_mb": _mb(fb.get("budget_bytes")),
        })
    except Exception as e:
        logger.debug("memory overview: filebrowser stats unavailable: %s", e)

    # 4) reformatize (ET Index) 캐시 — wide 결과 + raw ET
    try:
        from routers import reformatize as _reformatize
        rf = _reformatize.cache_stats()
        for sub_key, label in (("wide", "ET Index 결과(wide) 캐시"), ("raw", "ET raw 데이터 캐시")):
            sub = rf.get(sub_key) or {}
            caches.append({
                "key": f"reformatize_{sub_key}",
                "label": label,
                "entries": int(sub.get("entries") or 0),
                "mb": _mb(sub.get("bytes")),
                "budget_mb": _mb(sub.get("budget_bytes")),
            })
    except Exception as e:
        logger.debug("memory overview: reformatize stats unavailable: %s", e)

    # 5) 검색 경로 보조 캐시 + lookup candidate index memo — 어느 tier 도 안 보던
    #    구간이라 "캐시 합계는 작은데 RSS 는 높다" 의 상당 부분이 여기였다.
    try:
        scratch = scratch_cache_sizes()
        scratch_bytes = sum(int((row or {}).get("bytes") or 0) for row in scratch.values())
        caches.append({
            "key": "splittable_scratch",
            "label": "SplitTable 보조 캐시(CSV 행·인덱스·스키마)",
            "entries": sum(int((row or {}).get("entries") or 0) for row in scratch.values()),
            "mb": _mb(scratch_bytes),
            "budget_mb": 0,
            "detail": scratch,
        })
    except Exception as e:
        logger.debug("memory overview: scratch cache stats unavailable: %s", e)
    try:
        memo = _ml_table_lookup.candidate_index_memo_stats()
        caches.append({
            "key": "lookup_candidate_index",
            "label": "lookup candidate index 파싱 사본",
            "entries": int(memo.get("entries") or 0),
            "mb": float(memo.get("used_mb") or 0.0),
            "budget_mb": float(memo.get("budget_mb") or 0.0),
        })
    except Exception as e:
        logger.debug("memory overview: candidate index memo stats unavailable: %s", e)

    caches_total_mb = round(sum(float(c.get("mb") or 0) for c in caches), 1)
    budget_total_mb = round(sum(float(c.get("budget_mb") or 0) for c in caches), 1)

    # 프로세스/호스트 메모리 — 캐시 합계와 실제 RSS 의 괴리를 함께 보여준다.
    process = {}
    try:
        from core.runtime_limits import process_memory_snapshot
        snap = process_memory_snapshot()
        process = {
            "rss_gb": snap.get("process_rss_gb"),
            "effective_gb": snap.get("process_memory_effective_gb"),
            "effective_kind": snap.get("process_memory_effective_kind"),
            "container_working_set_gb": snap.get("container_memory_working_set_gb"),
            "container_current_gb": snap.get("container_memory_current_gb"),
            "limit_gb": snap.get("process_memory_limit_gb"),
            "system_total_gb": snap.get("system_memory_total_gb"),
            "system_available_gb": snap.get("system_memory_available_gb"),
        }
    except Exception as e:
        logger.debug("memory overview: process snapshot unavailable: %s", e)

    # 메모리 워치독 + 캐시 풀 상한 — OOM 방어 상태를 같은 화면에서 확인.
    watchdog = {}
    try:
        from core import memory_watchdog as _memory_watchdog
        watchdog = _memory_watchdog.status()
    except Exception as e:
        logger.debug("memory overview: watchdog status unavailable: %s", e)
    budget_pool = {}
    try:
        from core import cache_budget as _cache_budget
        budget_pool = _cache_budget.overview()
    except Exception as e:
        logger.debug("memory overview: cache budget overview unavailable: %s", e)
    proxy = {}
    try:
        from core import upstream_proxy as _upstream_proxy
        proxy = _upstream_proxy.status()
    except Exception as e:
        logger.debug("memory overview: upstream proxy status unavailable: %s", e)

    return {
        "ok": True,
        "caches": caches,
        "caches_total_mb": caches_total_mb,
        "caches_budget_total_mb": budget_total_mb,
        "process": process,
        "watchdog": watchdog,
        "cache_budget_pool": budget_pool,
        "upstream_proxy": proxy,
    }


@router.get("/cache-event-log")
def get_cache_event_log(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    category: str = Query(""),
):
    """관리자 전용 — 캐시 성공/실패 이벤트 로그 + peak RAM."""
    user = current_user(request)
    if not is_page_manager(user, "splittable"):
        raise HTTPException(403, "관리자 전용")
    from core.cache_event_log import (get_events, get_jobs, milestones, peak_ram_info,
                                      progress_snapshot)
    return {
        "ok": True,
        "events": get_events(limit=limit, category=category),
        "peak_ram": peak_ram_info(),
        "jobs": get_jobs(recent=3),
        # 지금 돌고 있는 빌드의 "몇 랏 중 몇 랏". 끝난 작업은 빠진다.
        # category 필터와 무관하게 항상 전체 기준으로 준다.
        "progress": progress_snapshot(),
        # 제품 × 캐싱 작업별 최근 성공/실패 — 진행률보다 이쪽이 평소 보는 화면이다.
        "product_status": _product_cache_status_snapshot(nonblocking=True),
        # 제품 단위 시작/완료/실패만 뽑은 로그 — 청크 진행에 묻히지 않게.
        "milestones": milestones(),
        "queues": _cache_queue_snapshot(),
        # 서버 스캔 게이트(실행 1건 + 대기열) — 수동 스캔 중이 아닐 때도 화면에서
        # "예약 스캔이 대기 중"을 볼 수 있어야 한다.
        "scan_queue": _scan_gate_snapshot(),
    }


def _artifact_built_ts(value) -> float:
    """ISO 문자열 → epoch 초. tz 가 없으면 로컬 시각으로 본다. 실패하면 0."""
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    try:
        return datetime.datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _path_mtime(path: Path) -> float:
    try:
        return float(path.stat().st_mtime)
    except Exception:
        return 0.0


def _required_split_cache_status(product: str) -> dict:
    """Return the four persistent SplitTable caches managed as one pipeline.

    This is deliberately an artifact check, not a reconstruction from recent
    event-log messages.  A seven-day-old successful event does not prove that
    its files still exist, and an existing cache must not be reported missing
    merely because its build event aged out of the log window.
    """
    canonical = (_canonical_mltable_product_name(product, allow_bare=True)
                 or str(product or "").strip().upper())
    if not canonical:
        return {"product": "", "kinds": [], "ready_count": 0, "total": 4,
                "all_ready": False, "missing_labels": []}

    lookup = _lookup_cache_public_meta_for(canonical)
    lookup_roots = int(lookup.get("root_lot_id_count") or 0)
    lookup_building = str(lookup.get("job_status") or lookup.get("status") or "") in {
        "queued", "running", "building",
    }
    lookup_ready = bool(
        lookup.get("has_cache")
        and (lookup.get("status") == "fresh" or lookup_building)
    )

    lookup_built_ts = _artifact_built_ts(lookup.get("built_at"))
    lookup_build_seconds = float(lookup.get("build_seconds") or 0.0)

    pivot_dir = _pivot_cache_path(canonical, "_probe").parent
    pivot_files = 0
    try:
        pivot_files = sum(1 for path in pivot_dir.glob("*.parquet") if path.is_file())
    except Exception:
        pivot_files = 0
    pivot_running = _pivot_cache_build_state(canonical) == "building"
    try:
        pivot_complete = not _pivot_cache_needs_build(canonical, _product_path(canonical))
    except Exception:
        pivot_complete = False
    # The builder's root fingerprint is the authoritative membership check.
    # File count alone can pass when one stale root remains while a current root
    # is missing, so it is only retained as a compact progress number.
    pivot_ready = bool(pivot_files > 0 and (pivot_complete or pivot_running))

    latest = _latest_lot_step_cache_status(canonical)
    latest_ready = bool(
        latest.get("cache_exists")
        and latest.get("format_current")
        and int(latest.get("product_row_count") or 0) > 0
    )

    fab_meta = _fab_lot_index_read_meta(canonical)
    fab_dir = _fab_lot_index_dir(canonical)
    fab_roots = 0
    try:
        fab_roots = sum(
            1 for path in fab_dir.iterdir()
            if path.is_dir() and path.name.startswith(f"{_FAB_IDX_ROOT_COL}=")
        )
    except Exception:
        fab_roots = 0
    with _FAB_IDX_BUILD_LOCK:
        fab_running = canonical in _FAB_IDX_BUILD_INPROGRESS
    fab_ready = bool(fab_meta.get("built_at") and fab_meta.get("root_col") and fab_roots > 0)

    # 각 산출물이 스스로 들고 있는 완료 시각. 이벤트 로그(7일 창)가 아니라
    # 파일 쪽이 근거라, 로그가 잘렸거나 워커 오프로드·fresh 스킵으로 완료 줄이
    # 안 남은 캐시도 "언제 끝났는지"를 화면에 그대로 줄 수 있다.
    kinds = [
        {"kind": "lookup", "label": "랏 lookup", "ready": lookup_ready,
         "state": "building" if lookup_building else ("ready" if lookup_ready else (lookup.get("status") or "missing")),
         "done": lookup_roots, "total": lookup_roots,
         "built_ts": lookup_built_ts, "build_seconds": lookup_build_seconds,
         "message": (f"lookup 빌드 완료 — {lookup_roots:,} roots"
                     + (f" · {lookup_build_seconds:.1f}s" if lookup_build_seconds > 0 else ""))},
        {"kind": "pivot", "label": "SplitTable pivot", "ready": pivot_ready,
         "state": "building" if pivot_running else ("ready" if pivot_ready else "missing"),
         "done": pivot_files, "total": lookup_roots,
         "built_ts": _path_mtime(pivot_dir / ".root_fingerprints.json"), "build_seconds": 0.0,
         "message": f"Pivot 빌드 완료 — {pivot_files:,} roots"},
        {"kind": "latest_lot", "label": "WIP latest-lot", "ready": latest_ready,
         "state": "ready" if latest_ready else ("building" if _MANUAL_LATEST_REFRESH_RUNNING else "missing"),
         "done": int(latest.get("product_row_count") or 0), "total": 0,
         "built_ts": _artifact_built_ts(latest.get("updated_at")), "build_seconds": 0.0,
         "message": f"WIP latest-lot 빌드 완료 — {int(latest.get('product_row_count') or 0):,} rows"},
        {"kind": "fab_index", "label": "FAB latest 인덱스", "ready": fab_ready,
         "state": "building" if fab_running else ("ready" if fab_ready else "missing"),
         "done": fab_roots, "total": 0,
         "built_ts": _artifact_built_ts(fab_meta.get("built_at")), "build_seconds": 0.0,
         "message": f"FAB latest 인덱스 빌드 완료 — {fab_roots:,} roots"},
    ]
    ready_count = sum(1 for row in kinds if row["ready"])
    return {
        "product": canonical,
        "kinds": kinds,
        "ready_count": ready_count,
        "total": len(kinds),
        "all_ready": ready_count == len(kinds),
        "missing_labels": [row["label"] for row in kinds if not row["ready"]],
    }


@router.get("/cache/required-status")
def required_split_cache_status(request: Request, product: str = Query(...)):
    """Selected product's four-cache readiness, without internal column names."""
    user = current_user(request)
    if not is_page_manager(user, "splittable"):
        raise HTTPException(403, "관리자 전용")
    return {"ok": True, **_required_split_cache_status(product)}


# ── /ram-cache/lot-status 메모리 안전장치 ────────────────────────────────
# 주요 lot 위치 조회는 per-root 파티션 미스 시 monolithic latest-lot 캐시
# 풀스캔으로 떨어진다. 예전에는 root 마다 풀스캔을 반복하고 latest_main 을
# 위해 전체 정렬까지 해서, 캐시 관리 페이지 진입만으로 RSS 가 수 GB 튀어
# 프로세스가 죽었다(OOM). 지금은 ①폴백 스캔을 제품당 1회로 배치, ②streaming
# collect 로 상한 고정, ③메모리 압박 시 스캔 스킵, ④결과 TTL 캐시 + 스캔
# 직렬화로 재진입/연속 클릭 시 재스캔을 막는다.
_RAM_CACHE_LOT_STATUS_TTL_SEC = 180.0
_RAM_CACHE_LOT_STATUS_LOCK = threading.Lock()
_RAM_CACHE_LOT_STATUS_COMPUTE_LOCK = threading.Lock()
_RAM_CACHE_LOT_STATUS_CACHE: OrderedDict = OrderedDict()
_RAM_CACHE_LOT_STATUS_COLS = ("root_lot_id", "lot_id", "step_id", "function_step", "tkout_time")


def _ram_cache_lot_status_compute(product: str, roots: list[str]) -> dict:
    product_upper = product.upper()
    # Vehicle_matching step_id → step_desc 맵 (main step 목록이기도 하다)
    step_desc_map: dict[str, str] = {}
    # 근사 매칭용: 영문 프리픽스별 (숫자, step_id, desc) 목록 — 정확 매칭이 없으면
    # 같은 프리픽스에서 step_id 보다 작은 것 중 가장 가까운 main step 의 desc 를 쓴다.
    prefix_steps: dict[str, list[tuple[int, str, str]]] = {}
    _STEP_SPLIT_RE = _re.compile(r"^([A-Z]+)(\d+)")
    try:
        from core import fab_reference
        vm_rows = fab_reference._read_rows(fab_reference.VEHICLE_MATCHING_FILE)
        bare = product_upper[len("ML_TABLE_"):] if product_upper.startswith("ML_TABLE_") else product_upper
        scoped = [r for r in vm_rows if str(r.get("product") or "").upper() in (product_upper, bare)] or vm_rows
        for r in scoped:
            sid = str(r.get("step_id") or "").strip().upper()
            desc = str(r.get("step_desc") or "").strip()
            if sid and desc and sid not in step_desc_map:
                step_desc_map[sid] = desc
                m = _STEP_SPLIT_RE.match(sid)
                if m:
                    prefix_steps.setdefault(m.group(1), []).append((int(m.group(2)), sid, desc))
        for steps in prefix_steps.values():
            steps.sort()
    except Exception:
        pass

    def _resolve_step_desc(step_id: str) -> tuple[str, bool]:
        """(desc, approx). 정확 매칭 우선, 없으면 같은 영문 프리픽스에서
        step_id 보다 작은 main step 중 가장 가까운 것의 desc (approx=True)."""
        sid = str(step_id or "").strip().upper()
        if not sid:
            return "", False
        exact = step_desc_map.get(sid)
        if exact:
            return exact, False
        m = _STEP_SPLIT_RE.match(sid)
        if not m:
            return "", False
        prefix, num = m.group(1), int(m.group(2))
        best = None
        for cand_num, _cand_sid, cand_desc in prefix_steps.get(prefix, []):
            if cand_num <= num:
                best = cand_desc
            else:
                break
        return (best or ""), bool(best)

    def _empty_status() -> dict:
        return {"step_id": "", "step_desc": "", "step_desc_approx": False,
                "fab_lot_id": "", "tkout_time": "", "wafer_count": 0}

    def _status_from_df(df) -> dict:
        status = _empty_status()
        if df is None or not df.height:
            return status
        status["wafer_count"] = int(df.height)
        if "tkout_time" in df.columns:
            df = df.sort("tkout_time", descending=True, nulls_last=True)
        top = df.row(0, named=True)
        step_id = str(top.get("step_id") or "").strip()
        status["step_id"] = step_id
        status["fab_lot_id"] = str(top.get("lot_id") or "").strip()
        tk = top.get("tkout_time")
        status["tkout_time"] = str(tk) if tk is not None else ""
        # step_desc 는 vehicle_matching 기준: 정확 매칭 → 같은 영문
        # 프리픽스의 이전 main step 근사 → (둘 다 없으면) function_step.
        desc, approx = _resolve_step_desc(step_id)
        if not desc:
            desc = str(top.get("function_step") or "").strip()
            approx = False
        status["step_desc"] = desc
        status["step_desc_approx"] = approx
        return status

    statuses: dict[str, dict] = {root: _empty_status() for root in roots}
    pending: list[str] = []  # per-root 파티션이 없어 monolithic 폴백이 필요한 root
    for root in roots:
        try:
            part_lf = _latest_lot_index_partition_lf(product, root)
            if part_lf is None:
                pending.append(root)
                continue
            names = part_lf.collect_schema().names()
            want = [c for c in _RAM_CACHE_LOT_STATUS_COLS if c in names]
            if "root_lot_id" in names:
                part_lf = part_lf.filter(
                    pl.col("root_lot_id").cast(_STR, strict=False).str.to_uppercase() == root)
            statuses[root] = _status_from_df(part_lf.select(want).collect())
        except Exception as e:
            logger.warning("ram-cache lot-status failed for %s/%s: %s", product, root, e)

    # 메모리 압박이면 monolithic 풀스캔(폴백·latest_main)을 건너뛴다 — 이
    # 페이지가 프로세스를 죽이면 안 된다. 파티션에서 얻은 위치만 노출.
    skipped_reason = ""
    try:
        from core.runtime_limits import process_memory_high
        if process_memory_high():
            skipped_reason = "process_memory_high"
    except Exception:
        pass

    if pending and not skipped_reason:
        # 폴백 root 들은 monolithic 캐시를 '한 번만' 스캔해 일괄 조회한다.
        try:
            lf = _latest_lot_step_cache_lf(product)
            if lf is not None:
                names = lf.collect_schema().names()
                if "root_lot_id" in names:
                    want = [c for c in _RAM_CACHE_LOT_STATUS_COLS if c in names]
                    from core.parquet_perf import collect_streaming
                    batch = collect_streaming(
                        lf.filter(
                            pl.col("root_lot_id").cast(_STR, strict=False)
                            .str.to_uppercase().is_in(sorted(pending))
                        ).select(want)
                    )
                    for root in pending:
                        sub = batch.filter(
                            pl.col("root_lot_id").cast(_STR, strict=False).str.to_uppercase() == root)
                        statuses[root] = _status_from_df(sub)
        except Exception as e:
            logger.warning("ram-cache lot-status batch scan failed for %s: %s", product, e)

    # 참고 헤더용: 이 제품에서 가장 최근 진행된 main step (vehicle_matching 에
    # 등재된 step 중 tkout_time 최신). 정확 매칭이 없으면 최신 행의 근사 desc.
    latest_main: dict = {}
    if not skipped_reason:
        try:
            lf = _latest_lot_step_cache_lf(product)
            if lf is not None:
                names = lf.collect_schema().names()
                want = [c for c in _RAM_CACHE_LOT_STATUS_COLS if c in names]
                lf2 = lf.select(want)
                if "tkout_time" in want:
                    lf2 = lf2.sort("tkout_time", descending=True, nulls_last=True)
                from core.parquet_perf import collect_streaming
                df = collect_streaming(lf2.head(2000))
                fallback = None
                for row in df.iter_rows(named=True):
                    sid = str(row.get("step_id") or "").strip().upper()
                    if not sid:
                        continue
                    entry = {
                        "step_id": sid,
                        "root_lot_id": str(row.get("root_lot_id") or "").strip(),
                        "fab_lot_id": str(row.get("lot_id") or "").strip(),
                        "tkout_time": str(row.get("tkout_time") or ""),
                    }
                    if sid in step_desc_map:
                        latest_main = {**entry, "step_desc": step_desc_map[sid], "approx": False}
                        break
                    if fallback is None:
                        desc, approx = _resolve_step_desc(sid)
                        if desc:
                            fallback = {**entry, "step_desc": desc, "approx": approx}
                if not latest_main and fallback:
                    latest_main = fallback
        except Exception as e:
            logger.warning("ram-cache lot-status latest_main failed for %s: %s", product, e)
    return {"ok": True, "product": product, "statuses": statuses,
            "latest_main_step": latest_main, "skipped_reason": skipped_reason}


@router.get("/ram-cache/lot-status")
def get_ram_cache_lot_status(product: str = Query("")):
    """주요 lot 의 현재 위치(최신 step) — 캐시 관리 페이지 주요 Lot 표의
    step_id/step_desc 자동 컬럼용. 등록된 lot 의 root 별로 latest-lot 캐시에서
    tkout_time 최신 행을 뽑는다."""
    product = str(product or "").strip()
    if not product:
        raise HTTPException(400, "product is required")
    data = _load_priority_lots_file()
    product_upper = product.upper()
    roots: list[str] = []
    for key, val in data.items():
        if str(key).strip().upper() == product_upper and isinstance(val, list):
            for entry in val:
                if isinstance(entry, dict):
                    root = str(entry.get("root_lot_id") or "").strip().upper()
                    if root and root not in roots:
                        roots.append(root)
            break
    try:
        source_sig = repr(_latest_lot_index_source_sig())
    except Exception:
        source_sig = ""
    cache_key = "|".join([product_upper, ",".join(sorted(roots)), source_sig])
    with _RAM_CACHE_LOT_STATUS_LOCK:
        hit = _RAM_CACHE_LOT_STATUS_CACHE.get(cache_key)
        if hit and time.monotonic() - hit[0] < _RAM_CACHE_LOT_STATUS_TTL_SEC:
            return hit[1]
    # 무거운 스캔은 한 번에 하나만 — 페이지 재진입/제품 연속 클릭이 몰려도
    # monolithic 스캔이 겹쳐 메모리가 배로 튀지 않게 직렬화한다.
    with _RAM_CACHE_LOT_STATUS_COMPUTE_LOCK:
        with _RAM_CACHE_LOT_STATUS_LOCK:
            hit = _RAM_CACHE_LOT_STATUS_CACHE.get(cache_key)
            if hit and time.monotonic() - hit[0] < _RAM_CACHE_LOT_STATUS_TTL_SEC:
                return hit[1]
        out = _ram_cache_lot_status_compute(product, roots)
        # 메모리 압박으로 스킵한 응답은 캐시하지 않는다 — 압박이 풀리면 재시도.
        if not out.get("skipped_reason"):
            with _RAM_CACHE_LOT_STATUS_LOCK:
                _RAM_CACHE_LOT_STATUS_CACHE[cache_key] = (time.monotonic(), out)
                while len(_RAM_CACHE_LOT_STATUS_CACHE) > 16:
                    _RAM_CACHE_LOT_STATUS_CACHE.popitem(last=False)
        return out


@router.get("/ram-cache/knob-allocation")
def get_ram_cache_knob_allocation(product: str = Query(""), lot_id: str = Query("")):
    """lot 의 KNOB 할당 비율 — 각 KNOB 항목에서 split 값별 wafer 수/%.
    SplitTable /view (payload 캐시 활용) 를 내부 호출해 계산한다."""
    product = str(product or "").strip()
    root = str(lot_id or "").strip().upper()[:5]
    if not product or not root:
        raise HTTPException(400, "product and lot_id are required")
    try:
        view = view_split(
            product=product, root_lot_id=root, wafer_ids="", prefix="KNOB",
            custom_name="", view_mode="all", history_mode="all", fab_lot_id="",
            custom_cols="", include_related=False, cache_first=True, request=None,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"view load failed: {e}")
    wafer_keys = view.get("wafer_keys") or []
    total_wafers = len(wafer_keys)
    out_rows = []
    for r in view.get("rows") or []:
        cells = r.get("_cells") or {}
        first = next(iter(cells.values()), {})
        if first.get("is_custom_tag") or first.get("is_management_row"):
            continue
        tally: dict[str, int] = {}
        for cell in cells.values():
            v = cell.get("actual")
            if v is None or str(v).strip() == "":
                continue
            sv = str(v).strip()
            tally[sv] = tally.get(sv, 0) + 1
        if not tally:
            continue
        assigned = sum(tally.values())
        values = [
            {"value": val, "count": cnt, "pct": round(cnt / assigned * 100, 1)}
            for val, cnt in sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))
        ][:15]
        out_rows.append({
            "param": r.get("_param") or "",
            "display": r.get("_display") or r.get("_param") or "",
            "assigned": assigned,
            "unassigned": max(0, total_wafers - assigned),
            "split_count": len(tally),
            "values": values,
        })
        if len(out_rows) >= 500:
            break
    # prefix(KNOB 등)가 최우선, 그 다음 split(값 2개 이상) 행 우선, 그 안에서 이름 순
    out_rows.sort(key=lambda row: (
        _natural_param_key(row["param"])[0],
        0 if row["split_count"] >= 2 else 1,
        _natural_param_key(row["param"])[1:]
    ))
    return {
        "ok": True, "product": product, "root_lot_id": root,
        "total_wafers": total_wafers, "rows": out_rows,
        "split_rows": sum(1 for row in out_rows if row["split_count"] >= 2),
    }


@router.get("/ram-cache/product-budgets")
def get_ram_cache_product_budgets():
    """제품별 RAM 캐시 예산(max_roots) + 적재순서 step 임계값 조회 — 운영/개발 구분."""
    cfg = load_json(SOURCE_CFG, {})
    budgets = cfg.get("ram_cache_product_budgets") or {}
    products: dict[str, dict] = {}
    if isinstance(budgets, dict):
        for prod_key, prod_val in budgets.items():
            if isinstance(prod_val, dict):
                products[prod_key] = {
                    "max_roots": int(prod_val.get("max_roots") or 1000),
                    "max_roots_dev": int(prod_val["max_roots_dev"]) if prod_val.get("max_roots_dev") is not None else None,
                    "step_threshold": int(prod_val["step_threshold"]) if prod_val.get("step_threshold") is not None else None,
                }
            else:
                try:
                    products[prod_key] = {"max_roots": int(prod_val), "max_roots_dev": None, "step_threshold": None}
                except Exception:
                    products[prod_key] = {"max_roots": 1000, "max_roots_dev": None, "step_threshold": None}
    default_max_roots = int(
        _ml_table_lookup._root_ram_cache_target_roots()
    )
    _is_dev = _ml_table_lookup._root_ram_cache_use_dev()
    return {
        "ok": True,
        "products": products,
        "default_max_roots": default_max_roots,
        "default_max_roots_dev": int(_ml_table_lookup._root_ram_cache_target_roots_dev()),
        "is_dev": _is_dev,
        # 우선적재(priority) 미등록 제품의 랏캐시 적재 순서 기준 — step_id 숫자 임계값.
        "default_step_threshold": int(_ml_table_lookup._root_ram_cache_step_threshold("")),
    }


class RamCacheProductBudgetSaveReq(BaseModel):
    product: str
    max_roots: int = 1000
    max_roots_dev: int | None = None
    step_threshold: int | None = None


@router.post("/ram-cache/product-budgets/save")
def save_ram_cache_product_budget(
    req: RamCacheProductBudgetSaveReq,
    _perm=Depends(require_page_manager("splittable")),
):
    """제품별 RAM 캐시 예산(max_roots) + 적재순서 step 임계값 저장 — 운영/개발 구분.
    source_config.json 의 ram_cache_product_budgets 아래에 기록한다."""
    product = str(req.product or "").strip()
    if not product:
        raise HTTPException(400, "product is required")
    max_roots = max(0, min(ROOT_LOT_CACHE_LIMIT_MAX, int(req.max_roots or 1000)))
    entry: dict = {"max_roots": max_roots}
    if req.max_roots_dev is not None:
        entry["max_roots_dev"] = max(0, min(ROOT_LOT_CACHE_LIMIT_MAX, int(req.max_roots_dev)))
    if req.step_threshold is not None:
        entry["step_threshold"] = max(0, int(req.step_threshold))
    cfg = load_json(SOURCE_CFG, {})
    budgets = cfg.setdefault("ram_cache_product_budgets", {})
    if not isinstance(budgets, dict):
        budgets = {}
        cfg["ram_cache_product_budgets"] = budgets
    # 기존 값 보존 — 화면이 일부 필드만 보내도 나머지 설정이 지워지지 않게.
    existing = budgets.get(product)
    if isinstance(existing, dict):
        if req.max_roots_dev is None and existing.get("max_roots_dev") is not None:
            entry["max_roots_dev"] = existing["max_roots_dev"]
        if req.step_threshold is None and existing.get("step_threshold") is not None:
            entry["step_threshold"] = existing["step_threshold"]
    budgets[product] = entry
    save_json(SOURCE_CFG, cfg)
    return {"ok": True, "product": product, "max_roots": max_roots,
            "max_roots_dev": entry.get("max_roots_dev"),
            "step_threshold": entry.get("step_threshold")}


def _resolve_override_meta(product: str, include_diagnostics: bool = True) -> dict:
    """v8.8.5: view / ml-table-match 양쪽에서 공용. 현재 product 에 대해 적용된 오버라이드 설정 요약.

    Returns (모든 필드 optional, 에러 시 error 로 이유 표기):
      {
        "enabled": bool,              # 조인 실제 수행 여부
        "manual_override": bool,      # SOURCE_CFG 에 명시된 fab_source 사용 여부
        "fab_source": str,            # 사용된 fab_source 경로 (e.g. "1.RAWDATA_DB_FAB/PRODA")
        "fab_col": str,               # 실제 join 하는 fab 컬럼 이름
        "ts_col": str,                # 최신도 판정에 쓰는 ts 컬럼 (빈 문자열이면 레거시 keep=last)
        "join_keys": [str],
        "scanned_files": [str],       # fab_source 아래 발견된 parquet 들 (최대 20)
        "scanned_count": int,         # 실제 파일 개수
        "row_count": int,             # fab_source LazyFrame 전체 row 수 (scanned)
        "sample_fab_values": [str],   # head(5) 의 fab_col 값 — "어디서 읽어옴?" 답변용
        "error": str | None,
      }
    """
    meta = {
        "enabled": False, "manual_override": False,
        "fab_source": "", "fab_col": "", "ts_col": "",
        "join_keys": [], "scanned_files": [], "scanned_count": 0,
        "row_count": 0, "sample_fab_values": [], "error": None,
        "raw_columns": [], "runtime_columns": [], "column_aliases": {}, "schema_mode": "unknown",
        # v8.8.16: hive 원천에서 끌어오기로 한 override 컬럼 목록 + 실제 스키마에 존재하는 것만.
        "override_cols": [], "override_cols_present": [], "override_cols_missing": [],
    }
    try:
        product = _canonical_mltable_product_name(product, allow_bare=True) or str(product or "").strip()
        cfg = load_json(SOURCE_CFG, {}) if SOURCE_CFG.exists() else {}
        ov = _lot_override_for(cfg, product)
        manual = (ov.get("fab_source") or "").strip()
        # v8.8.21: root:~~ 는 deprecated — 저장된 값이 남아있어도 무시하고 auto-derive 로 재매칭.
        if manual.startswith("root:"):
            manual = ""
        fab_source = manual or _auto_derive_fab_source(product)
        meta["manual_override"] = bool(manual)
        meta["fab_source"] = fab_source
        # v8.8.19: 진단 정보 — 어떤 data_root/DB 에서 어떤 후보를 탐색했는지 노출.
        db_base = _db_base()
        base_root = _base_root()
        meta["db_root"] = str(db_base)
        meta["base_root"] = str(base_root)
        meta["db_root_exists"] = bool(db_base.exists())
        meta["searched_db_roots"] = [p.name for p in _list_db_roots()]

        if not fab_source:
            if product.casefold().startswith("ml_table_"):
                pro = product[len("ML_TABLE_"):].strip()
                # 실제로 탐색한 후보 경로를 모두 리스트업
                tried = []
                for root_dir in _list_db_roots():
                    tried.append(f"{root_dir.name}/{pro}")
                if not _list_db_roots():
                    tried.append(f"(db_root 비어있거나 '1.RAWDATA_DB' 하위 제품 폴더 없음: {db_base})")
                meta["error"] = (
                    f"자동 매칭 실패: product='{product}' → pro='{pro}'. "
                    f"db_root='{db_base}'. "
                    f"후보 탐색: {tried if tried else '(없음)'}. "
                    f"권장 해결: data_root/DB 아래 '1.RAWDATA_DB/{pro}/' 가 존재하거나, "
                    f"수동으로 lot_overrides.{product}.fab_source 를 지정."
                )
                meta["tried_candidates"] = tried
            else:
                meta["error"] = "ML_TABLE_ prefix 아님 — 오버라이드 off."
            return meta

        # locate fab_source folder/file to list scanned files.  The resolver also
        # treats 1.RAWDATA_DB_FAB/<PROD> and 1.RAWDATA_DB/<PROD> as equivalent
        # FAB-history roots for production soft landing.
        fp, resolved_fab_source = _resolve_fab_source_target(fab_source)
        tried = []
        for root in (db_base, base_root):
            if not root or not root.exists():
                tried.append(f"{root} (not exist)" if root else "(None)")
                continue
            for rel in dict.fromkeys([fab_source, resolved_fab_source]):
                if rel:
                    tried.append(str(root / rel) + ("" if fp is not None else "  (not found)"))
        if fp is None:
            meta["tried_candidates"] = tried
            meta["error"] = (
                f"fab_source 경로를 찾을 수 없음: '{fab_source}'. "
                f"탐색 경로: {tried}. db_root='{db_base}' base_root='{base_root}'. "
                f"fab_source 는 데모/운영 모두 db_root 기준 상대경로만 사용하세요 "
                f"(예: '1.RAWDATA_DB_FAB/PRODA', not 'DB/1.RAWDATA_DB_FAB/PRODA')."
            )
            return meta
        if resolved_fab_source:
            meta["fab_source"] = resolved_fab_source
            fab_source = resolved_fab_source
        if fp.is_dir():
            parquets = _rglob_files_ci(fp, (".parquet",))
            base_for_rel = fp.parent if fp.parent.exists() else fp
            rels = []
            for p in parquets:
                try:
                    rels.append(str(p.relative_to(_db_base())))
                except Exception:
                    try:
                        rels.append(str(p.relative_to(_base_root())))
                    except Exception:
                        rels.append(str(p))
            meta["scanned_count"] = len(parquets)
            meta["scanned_files"] = [r.replace("\\", "/") for r in rels[:20]]
        else:
            meta["scanned_count"] = 1
            try:
                meta["scanned_files"] = [str(fp.relative_to(_db_base())).replace("\\", "/")]
            except Exception:
                meta["scanned_files"] = [str(fp)]

        raw_lf = _scan_fab_source_raw(fab_source)
        fab_lf = _scan_fab_source(fab_source)
        if fab_lf is None:
            meta["error"] = f"스캔 실패 (parquet 없음 또는 읽기 불가): {fab_source}"
            return meta
        # v8.8.22: CI 정렬 — ML_TABLE 대문자 vs hive 소문자 컬럼 이름 차이를 흡수.
        try:
            main_fp = _product_path(product)
            if main_fp.suffix.lower() == ".csv":
                main_names_list = pl.scan_csv(str(main_fp), infer_schema_length=5000).collect_schema().names()
            else:
                main_names_list = _scan_parquet_compat(str(main_fp)).collect_schema().names()
        except Exception:
            main_names_list = []
        fab_lf, fab_schema_names = _ci_align_fab_to_main(fab_lf, main_names_list)
        fab_names = fab_schema_names  # list after rename
        main_names = main_names_list
        try:
            raw_names = raw_lf.collect_schema().names() if raw_lf is not None else []
        except Exception:
            raw_names = []
        meta["raw_columns"] = raw_names
        meta["runtime_columns"] = list(fab_names)
        meta["column_aliases"] = _detect_source_column_aliases(raw_names, fab_names)
        meta["schema_mode"] = "adapted" if meta["column_aliases"] else "raw"

        # join keys
        join_keys = ov.get("join_keys") or []
        if isinstance(join_keys, str):
            join_keys = [k.strip() for k in join_keys.split(",") if k.strip()]
        # 유저가 지정한 키도 CI 로 실제 컬럼명에 매핑.
        if join_keys:
            mapped = []
            for k in join_keys:
                actual = _ci_resolve_in(k, main_names) or _resolve_source_col_name(k, fab_names)
                if actual:
                    mapped.append(actual)
            join_keys = mapped
        if not join_keys:
            join_keys = _default_override_join_keys(main_names, fab_names)
        join_keys = [k for k in join_keys if k in fab_names]
        meta["join_keys"] = join_keys

        # fab_col / ts_col 추론 (v8.8.22: CI 매칭 — fab_lf 는 이미 main casing 으로 align 됨).
        fc_raw = (ov.get("fab_col") or "").strip()
        meta["fab_col"] = (_resolve_source_col_name(fc_raw, fab_names) if fc_raw else "") \
                         or _pick_first_present_ci(_FAB_COL_CANDIDATES, fab_names) \
                         or "fab_lot_id"
        tc_raw = (ov.get("ts_col") or "").strip()
        meta["ts_col"] = (_resolve_source_col_name(tc_raw, fab_names) if tc_raw else "") \
                         or _pick_ts_col(fab_names) \
                         or ""

        # v8.8.16: override_cols — 기본 (_DEFAULT_OVERRIDE_COLS) + manual ov.override_cols + 레거시 fab_col 병합.
        raw_oc = ov.get("override_cols")
        if isinstance(raw_oc, str):
            raw_oc = [c.strip() for c in raw_oc.split(",") if c.strip()]
        if not raw_oc:
            raw_oc = list(_DEFAULT_OVERRIDE_COLS)
        # 레거시 fab_col 도 합류 (중복 제거).
        if meta["fab_col"] and meta["fab_col"] not in raw_oc:
            raw_oc = list(raw_oc) + [meta["fab_col"]]
        # v8.8.22: CI 매칭 — 사용자가 소문자로 적었어도 실제 스키마의 casing 으로 맵핑.
        resolved_oc = []
        for c in raw_oc:
            actual = _resolve_source_col_name(c, fab_names)
            resolved_oc.append(actual or c)
        meta["override_cols"] = list(resolved_oc)
        meta["override_cols_present"] = [c for c in resolved_oc if c in fab_names]
        meta["override_cols_missing"] = [c for c in resolved_oc if c not in fab_names]

        if meta["fab_col"] not in fab_names:
            meta["error"] = f"fab_col '{meta['fab_col']}' 이 소스 스키마에 없음. 소스 컬럼: {fab_names[:20]}"
            return meta
        if not join_keys:
            meta["error"] = f"공통 join key 없음. 소스 컬럼: {fab_names[:20]}"
            return meta

        # row count + sample
        if include_diagnostics:
            try:
                rc = fab_lf.select(pl.len()).collect()
                meta["row_count"] = int(rc.item()) if rc.height > 0 else 0
            except Exception as e:
                meta["row_count"] = -1
        try:
            sample_cols = [c for c in (join_keys + [meta["fab_col"]] + ([meta["ts_col"]] if meta["ts_col"] else [])) if c in fab_names]
            sample = fab_lf.select(sample_cols)
            if include_diagnostics and meta["ts_col"] and meta["ts_col"] in fab_names:
                sample = sample.sort(meta["ts_col"], descending=True, nulls_last=True)
            vals = sample.head(5).collect()
            if meta["fab_col"] in vals.columns:
                meta["sample_fab_values"] = [
                    ("" if v is None else str(v)) for v in vals[meta["fab_col"]].to_list()
                ]
        except Exception as e:
            pass
        meta["enabled"] = True
    except Exception as e:
        meta["error"] = f"resolve 중 예외: {type(e).__name__}: {e}"
    return meta


def _resolve_override_meta_light(product: str) -> dict:
    """Cheap view badge metadata; avoid rescanning FAB source after /view already did."""
    meta = {
        "enabled": False, "manual_override": False,
        "fab_source": "", "fab_col": "fab_lot_id", "ts_col": "",
        "root_col": "", "wf_col": "", "join_keys": [], "override_cols": [],
        "override_cols_present": [],
        "scanned_count": 0, "row_count": 0, "sample_fab_values": [],
        "raw_columns": [], "runtime_columns": [], "column_aliases": {},
        "error": None,
    }
    try:
        product = _canonical_mltable_product_name(product, allow_bare=True) or str(product or "").strip()
        cfg = load_json(SOURCE_CFG, {}) if SOURCE_CFG.exists() else {}
        ov = _lot_override_for(cfg, product)
        manual = _normalize_fab_source_path((ov.get("fab_source") or "").strip())
        if manual.startswith("root:"):
            manual = ""
        fab_source = manual or _auto_derive_fab_source(product)
        meta["manual_override"] = bool(manual)
        meta["fab_source"] = fab_source
        meta["enabled"] = bool(fab_source)
        meta["root_col"] = (ov.get("root_col") or "").strip()
        meta["wf_col"] = (ov.get("wf_col") or ov.get("wafer_col") or "").strip()
        meta["fab_col"] = (ov.get("fab_col") or "fab_lot_id").strip() or "fab_lot_id"
        meta["ts_col"] = (ov.get("ts_col") or "").strip()
        join_keys = ov.get("join_keys") or []
        if isinstance(join_keys, str):
            join_keys = [k.strip() for k in join_keys.split(",") if k.strip()]
        meta["join_keys"] = list(join_keys)
        raw_oc = ov.get("override_cols")
        if isinstance(raw_oc, str):
            raw_oc = [c.strip() for c in raw_oc.split(",") if c.strip()]
        meta["override_cols"] = list(raw_oc or _DEFAULT_OVERRIDE_COLS)
        meta["override_cols_present"] = list(meta["override_cols"])
        if not fab_source and product.casefold().startswith("ml_table_"):
            meta["error"] = "FAB source not matched"
    except Exception as e:
        meta["error"] = f"{type(e).__name__}: {e}"
    return meta

def _pick_first_present(candidates, available_names):
    av = set(available_names)
    for c in candidates:
        if c in av:
            return c
    return ""


def _pick_first_present_ci(candidates, available_names):
    """v8.8.22: case-insensitive 버전. 실제 스키마의 정확한 casing 을 반환."""
    ci = {n.casefold(): n for n in available_names}
    for c in candidates:
        actual = ci.get(c.casefold())
        if actual:
            return actual
    return ""


def _pick_ts_col(available_names):
    """Pick the most-likely time column from a FAB source."""
    primary = _pick_first_present_ci(_TS_COL_CANDIDATES, available_names)
    if primary:
        return primary
    for name in (available_names or []):
        low = str(name).casefold()
        if "time" in low or "timestamp" in low or low.endswith("_ts") or low.startswith("ts_"):
            return name
    return _pick_first_present_ci(("date",), available_names)


def _resolve_source_col_name(name: str, available_names):
    """Resolve user-facing raw/runtime column names against runtime source schema."""
    actual = _ci_resolve_in(name, available_names)
    if actual:
        return actual
    folded = str(name or "").strip().casefold()
    if not folded:
        return ""
    ci = {str(n).casefold(): n for n in (available_names or [])}
    for raw_name, runtime_name in _RAW_TO_RUNTIME_ALIAS_CANDIDATES.items():
        if folded == raw_name.casefold():
            actual = ci.get(runtime_name.casefold())
            if actual:
                return actual
        if folded == runtime_name.casefold():
            actual = ci.get(raw_name.casefold())
            if actual:
                return actual
    return ""


def _detect_source_column_aliases(raw_names, runtime_names):
    """Return raw->runtime aliases introduced by source adaptation."""
    raw_ci = {str(n).casefold(): n for n in (raw_names or [])}
    runtime_ci = {str(n).casefold(): n for n in (runtime_names or [])}
    out = {}
    for raw_name, runtime_name in _RAW_TO_RUNTIME_ALIAS_CANDIDATES.items():
        raw_actual = raw_ci.get(raw_name.casefold())
        runtime_actual = runtime_ci.get(runtime_name.casefold())
        if raw_actual and runtime_actual and runtime_name.casefold() not in raw_ci:
            out[raw_actual] = runtime_actual
    return out


def _prefer_raw_schema_name(name: str, raw_names, runtime_names):
    """Map runtime alias names back to physical raw schema names for UI display."""
    actual_raw = _ci_resolve_in(name, raw_names)
    if actual_raw:
        return actual_raw
    aliases = _detect_source_column_aliases(raw_names, runtime_names)
    runtime_to_raw = {str(v).casefold(): k for k, v in aliases.items()}
    return runtime_to_raw.get(str(name or "").strip().casefold(), name)


def _fab_source_context(product: str) -> dict:
    """Return the active FAB history source and resolved key columns for a product."""
    p = (product or "").strip()
    if not p:
        return {}
    ml_product = _canonical_mltable_product_name(p, allow_bare=True)
    try:
        cfg = load_json(SOURCE_CFG, {}) if SOURCE_CFG.exists() else {}
        ov = _lot_override_for(cfg, ml_product)
        fab_source = (ov.get("fab_source") or "").strip()
        if fab_source.startswith("root:"):
            fab_source = ""
        if not fab_source:
            fab_source = _auto_derive_fab_source(ml_product)
        include_all = _foreground_global_fab_scan_enabled()
        if not fab_source and not _global_fab_source_paths("", include_all=include_all):
            return {}
        _, resolved_fab_source = _resolve_fab_source_target(fab_source) if fab_source else (None, "")
        if resolved_fab_source:
            fab_source = resolved_fab_source
        fab_lf, fab_sources = _scan_global_fab_sources(fab_source, include_all=include_all)
        if fab_lf is None:
            return {}
        try:
            main_fp = _product_path(ml_product)
            if main_fp.suffix.lower() == ".csv":
                main_names = pl.scan_csv(str(main_fp), infer_schema_length=5000).collect_schema().names()
            else:
                main_names = _scan_parquet_compat(str(main_fp)).collect_schema().names()
        except Exception:
            main_names = []
        fab_lf, fab_names = _ci_align_fab_to_main(fab_lf, main_names)
        try:
            fab_names = fab_lf.collect_schema().names()
        except Exception:
            pass
        root_col = _resolve_source_col_name((ov.get("root_col") or "").strip(), fab_names) \
                   or _pick_first_present_ci(("root_lot_id",), fab_names)
        wafer_col = _resolve_source_col_name((ov.get("wf_col") or ov.get("wafer_col") or "").strip(), fab_names) \
                    or _pick_first_present_ci(("wafer_id", "wafer"), fab_names)
        fab_col = _resolve_source_col_name((ov.get("fab_col") or "").strip(), fab_names) \
                  or _pick_first_present_ci(_FAB_COL_CANDIDATES, fab_names)
        ts_col = _resolve_source_col_name((ov.get("ts_col") or "").strip(), fab_names) \
                 or _pick_ts_col(fab_names)
        if not root_col or not fab_col:
            return {}
        return {
            "lf": fab_lf,
            "source": fab_source,
            "sources": fab_sources,
            "root_col": root_col,
            "wafer_col": wafer_col,
            "fab_col": fab_col,
            "ts_col": ts_col,
            "columns": fab_names,
        }
    except Exception as e:
        logger.warning("_fab_source_context 실패 (product=%s) %s: %s",
                       product, type(e).__name__, e)
        return {}


def _clean_str(v) -> str:
    s = "" if v is None else str(v).strip()
    return "" if s in ("", "None", "null") else s


def _wafer_sort_key(v: str):
    s = str(v or "").strip()
    try:
        return (0, int(s.upper().lstrip("W")))
    except Exception:
        return (1, s.upper())


def _merge_wafer_scope(user_wafer_ids: str, source_wafers: list[str]) -> str:
    """Intersect user wafer filter with FAB-source wafer scope when both exist."""
    source = [_clean_str(w) for w in (source_wafers or [])]
    source = [w for w in source if w]
    if not source:
        return user_wafer_ids or ""
    user = [w.strip() for w in str(user_wafer_ids or "").split(",") if w.strip()]
    if not user:
        return ",".join(sorted(dict.fromkeys(source), key=_wafer_sort_key))

    def norm(w):
        s = str(w or "").strip().upper()
        try:
            return str(int(s.lstrip("W")))
        except Exception:
            return s

    user_norm = {norm(w) for w in user}
    kept = [w for w in source if norm(w) in user_norm]
    if not kept:
        return "__NO_WAFER_MATCH__"
    return ",".join(sorted(dict.fromkeys(kept), key=_wafer_sort_key))


def _fab_history_scope(product: str, root_lot_id: str = "", fab_lot_id: str = "",
                       prefix: str = "", limit: int = 500,
                       prefer_raw_latest: bool = False) -> dict:
    """Query FAB history as current SplitTable lot identity.

    Candidate LOT_ID values must follow the same contract as ML_TABLE/SplitTable:
    pick the latest FAB row per root_lot_id + wafer_id first, then expose the
    resulting fab_lot_id/lot_id set.
    """
    root_lot_id = root_lot_id if isinstance(root_lot_id, str) else ""
    fab_lot_id = fab_lot_id if isinstance(fab_lot_id, str) else ""
    prefix = prefix if isinstance(prefix, str) else ""
    try:
        limit = int(limit)
    except Exception:
        limit = 500
    cache_key = (
        "fab_history_scope",
        _lot_lookup_cache_sig(product),
        str(product or "").strip(),
        root_lot_id.strip(),
        fab_lot_id.strip(),
        prefix.strip(),
        limit,
        bool(prefer_raw_latest),
    )
    cached = _lot_lookup_cache_get(cache_key)
    if cached is not None:
        return cached

    def finish(payload: dict) -> dict:
        return _lot_lookup_cache_set(cache_key, payload)

    if not prefer_raw_latest:
        cached_scope = _fab_history_scope_from_cache(
            product, root_lot_id=root_lot_id, fab_lot_id=fab_lot_id,
            prefix=prefix, limit=limit,
        )
        if cached_scope is not None:
            has_explicit_scope = bool(root_lot_id.strip() or fab_lot_id.strip())
            if cached_scope.get("candidates") or not has_explicit_scope:
                return finish(cached_scope)

    ctx = _fab_source_context(product)
    if not ctx:
        return finish({"candidates": [], "root_ids": [], "wafer_ids": [], "source": "", "query_ok": False})
    root_col = ctx["root_col"]
    fab_col = ctx["fab_col"]
    wafer_col = ctx.get("wafer_col") or ""
    ts_col = ctx.get("ts_col") or ""
    select_exprs = [
        pl.col(root_col).cast(_STR, strict=False).alias("root"),
        pl.col(fab_col).cast(_STR, strict=False).alias("fab"),
    ]
    if wafer_col:
        select_exprs.append(pl.col(wafer_col).cast(_STR, strict=False).alias("wafer"))
    if ts_col:
        select_exprs.append(pl.col(ts_col).cast(_STR, strict=False).alias("ts"))
    q = ctx["lf"].select(select_exprs)
    q = q.filter(pl.col("root").is_not_null() & pl.col("fab").is_not_null())
    root_scope = (root_lot_id or "").strip()
    fab_scope = (fab_lot_id or "").strip()
    if root_scope:
        q = q.filter(_join_key_expr("root") == root_scope.upper())
    if fab_scope:
        q = q.filter(_join_key_expr("fab") == fab_scope.upper())
    latest_subset = ["root"] + (["wafer"] if wafer_col else [])
    if ts_col:
        q = q.sort("ts", descending=True, nulls_last=True)
        q = q.unique(subset=latest_subset, keep="first", maintain_order=True)
    else:
        q = q.unique(subset=latest_subset, keep="last", maintain_order=True)
    if not fab_scope and prefix.strip():
        q = q.filter(_contains_literal_ci_expr("fab", prefix))
    try:
        fabs = _limited_unique_values(
            q, "fab", prefix="", limit=limit,
            preview_only=not bool(root_scope or fab_scope or prefix.strip()),
        )
        roots: list[str] = [root_scope] if root_scope else []
        wafers: list[str] = []
        # Exact fab lookup is used by /view to infer the root and wafer scope.
        # Keep that metadata precise, but avoid collecting it for broad previews.
        if fab_scope and fabs:
            meta_cols = [pl.col("root")]
            if wafer_col:
                meta_cols.append(pl.col("wafer"))
            meta_df = q.select(meta_cols).unique().collect()
            roots = sorted({s for s in (_clean_str(v) for v in meta_df["root"].to_list()) if s})
            if "wafer" in meta_df.columns:
                wafers = sorted({s for s in (_clean_str(v) for v in meta_df["wafer"].to_list()) if s}, key=_wafer_sort_key)
    except Exception as e:
        logger.warning("_fab_history_scope 조회 실패 (product=%s) %s: %s",
                       product, type(e).__name__, e)
        return finish({"candidates": [], "root_ids": [], "wafer_ids": [],
                       "source": ctx.get("source", ""), "query_ok": False})
    if not fabs:
        return finish({"candidates": [], "root_ids": [], "wafer_ids": [],
                       "source": ctx.get("source", ""), "query_ok": True})
    return finish({
        "candidates": fabs,
        "root_ids": roots,
        "wafer_ids": wafers,
        "source": ctx.get("source", ""),
        "query_ok": True,
    })


def _fab_history_root_candidates(product: str, prefix: str = "", limit: int = 500) -> dict:
    """Return root_lot_id candidates from the configured FAB DB source.

    SplitTable's editable source is ML_TABLE_*, but operators choose lots from
    the live FAB history.  Use the configured fab_source first so the dropdown
    follows the same DB path that /view and fab_lot_id matching use.
    """
    try:
        limit = max(1, int(limit or 500))
    except Exception:
        limit = 500
    cache_key = (
        "fab_history_root_candidates",
        _lot_lookup_cache_sig(product),
        str(product or "").strip(),
        str(prefix or "").strip(),
        limit,
    )
    cached = _lot_lookup_cache_get(cache_key)
    if cached is not None:
        return cached

    def finish(payload: dict) -> dict:
        return _lot_lookup_cache_set(cache_key, payload)

    cached_roots = _fab_history_root_candidates_from_cache(product, prefix=prefix, limit=limit)
    if cached_roots is not None:
        return finish(cached_roots)

    ctx = _fab_source_context(product)
    if not ctx:
        return finish({"candidates": [], "source": ""})
    root_col = ctx.get("root_col") or ""
    if not root_col:
        return finish({"candidates": [], "source": ctx.get("source", "")})
    try:
        values = _limited_unique_values(ctx["lf"], root_col, prefix=prefix, limit=limit)
    except Exception as e:
        logger.warning("_fab_history_root_candidates 실패 (product=%s) %s: %s",
                       product, type(e).__name__, e)
        return finish({"candidates": [], "source": ctx.get("source", "")})
    return finish({"candidates": values, "source": ctx.get("source", "")})


def _merge_candidate_values(*groups, limit: int = 500) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    try:
        limit = max(1, int(limit or 500))
    except Exception:
        limit = 500
    for group in groups:
        for value in group or []:
            text = _clean_str(value)
            if not text:
                continue
            key = text.upper()
            if key in seen:
                continue
            seen.add(key)
            out.append(text)
            if len(out) >= limit:
                return out
    return out


def _plan_product_name(product: str) -> str:
    raw = str(product or "").strip()
    canonical = _canonical_mltable_product_name(raw, allow_bare=True)
    return canonical or safe_id(raw or "product")


def _plan_history_path(product: str) -> Path:
    return PLAN_DIR / f"{_plan_product_name(product)}.json"


def _plan_alias_paths(product: str) -> list[Path]:
    """Plan store aliases kept for older callers that used bare product names."""
    canonical = _plan_history_path(product)
    out = [canonical]
    raw = str(product or "").strip()
    if raw:
        legacy = PLAN_DIR / f"{safe_id(raw)}.json"
        if legacy != canonical:
            out.insert(0, legacy)
    return out


def _load_plan_data(product: str) -> dict:
    merged = {"plans": {}, "history": [], "mismatch_alerts": {}}
    seen_history: set[str] = set()
    for fp in _plan_alias_paths(product):
        # cached — merged 로 복사만 하고 원본은 건드리지 않는다. cold 검색마다 plan
        # 전체를 재파싱하던 비용(GIL 점유 + 공유드라이브 read)을 없앤다.
        data = load_json_cached(fp, {}) if fp.exists() else {}
        if not isinstance(data, dict):
            continue
        plans = data.get("plans")
        if isinstance(plans, dict):
            merged["plans"].update(plans)
        mismatch_alerts = data.get("mismatch_alerts")
        if isinstance(mismatch_alerts, dict):
            merged["mismatch_alerts"].update(mismatch_alerts)
        hist = data.get("history")
        if isinstance(hist, list):
            for row in hist:
                if not isinstance(row, dict):
                    continue
                try:
                    key = json.dumps(row, sort_keys=True, ensure_ascii=False, default=str)
                except Exception:
                    key = str(row)
                if key in seen_history:
                    continue
                seen_history.add(key)
                merged["history"].append(row)
    return merged


# ── plan 변경 이력 아카이브 ────────────────────────────────────────────────────
# 제품 JSON 안의 history 는 저장할 때마다 최근 1000건으로 잘린다(`[-1000:]`).
# "누가 언제 무엇을 어떻게 바꿨는지"를 나중에 되짚으려면 잘리면 안 되므로, 같은
# 엔트리를 append-only JSONL 로 한 벌 더 남긴다. JSON 쪽 창은 기존 소비자(plan
# risk payload 등) 호환을 위해 그대로 둔다.
