"""core/worker_tasks.py — 워커(개발서버)가 실행하는 오프로드 작업 핸들러.

core.worker_dispatch.start_services() 가 worker 역할일 때 import 하며, import
부수효과로 핸들러가 등록된다. 새 오프로드 작업을 추가하려면 여기에
@handler("...") 함수를 더하고, 호출부는 worker_dispatch.run_heavy() 를 쓴다.

규칙:
  - 기본은 결과가 shared workspace 파일로 남는 작업 (pivot 캐시, 인덱스,
    파티션 등). 프로세스 RAM 캐시는 운영 API 서버가 소유하므로 오프로드
    대상이 아니다.
  - 예외적으로 응답-페이로드 작업도 허용하되, ①지연 허용(자체 처리 시간이
    큐 왕복 ~1초를 압도)이고 ②부수 상태가 전부 공유 data_root 에 남아 어느
    서버가 실행해도 이후 요청을 서빙할 수 있는 작업만 (예: flowi_chat_turn —
    LLM 대기가 지배, 차트 세션/유저 이벤트는 공유 파일).
  - 핸들러 반환값은 JSON 직렬화 가능한 작은 dict — 큰 데이터는 파일로 남기고
    경로/통계만 돌려준다.
  - cross-server 이중 실행 가드(shared_lease / 빌드 락)는 핸들러 안에서
    실행측이 잡는다 — api 서버가 로컬 폴백할 때도 같은 가드를 지나므로
    최악의 경쟁도 기존과 동일하게 수렴한다.
  - 무거운 모듈은 함수 안에서 lazy import (앱 전반의 관례).
"""
from __future__ import annotations

import logging
from pathlib import Path

from core.worker_dispatch import handler

logger = logging.getLogger("flow.worker_tasks")


@handler("ping")
def _ping(payload: dict) -> dict:
    """헬스/배선 점검용 — 페이로드를 그대로 돌려준다."""
    return {"ok": True, "pong": payload.get("echo", "")}


@handler("splittable_pivot_build")
def _splittable_pivot_build(payload: dict) -> dict:
    """SplitTable product pivot 캐시 재빌드 (결과: db_root/cache/split_table/...)."""
    product = str(payload.get("product") or "").strip()
    if not product:
        return {"ok": False, "error": "missing product"}
    source_raw = str(payload.get("product_path") or "").strip()
    source_fp = Path(source_raw) if source_raw else None
    # 서버마다 shared DB의 mount 경로가 다를 수 있다. api의 절대 경로가 worker에
    # 없으면 product 이름으로 worker 로컬 PATHS에서 다시 해석한다.
    if source_fp is not None and not source_fp.is_file():
        source_fp = None
    from core import shared_lease
    lease_name = f"splittable_pivot_{product}"
    if not shared_lease.try_acquire(lease_name, ttl_sec=1800.0):
        return {"ok": False, "error": f"lease_held:{shared_lease.holder(lease_name)}"}
    try:
        from app_v2.modules.splittable.cache_builder import build_pivoted_cache_for_product
        ok = bool(build_pivoted_cache_for_product(product, product_path=source_fp))
        return {"ok": ok, "product": product}
    finally:
        shared_lease.release(lease_name)


@handler("splittable_fab_lot_index_build")
def _splittable_fab_lot_index_build(payload: dict) -> dict:
    """SplitTable fab lot index (재)빌드 (결과: 공유 인덱스 파일 + meta json)."""
    product = str(payload.get("product") or "").strip()
    if not product:
        return {"ok": False, "error": "missing product"}
    fab_source = str(payload.get("fab_source") or "")
    include_all = bool(payload.get("include_all"))
    from core import shared_lease
    lease_name = f"splittable_fabidx_{product}"
    if not shared_lease.try_acquire(lease_name, ttl_sec=1800.0):
        return {"ok": False, "error": f"lease_held:{shared_lease.holder(lease_name)}"}
    try:
        from routers.splittable import _build_fab_lot_index
        ok = bool(_build_fab_lot_index(product, fab_source, include_all))
        return {"ok": ok, "product": product}
    finally:
        shared_lease.release(lease_name)


@handler("splittable_match_cache_refresh")
def _splittable_match_cache_refresh(payload: dict) -> dict:
    """수 GB FAB source를 읽는 제품별 match cache를 개발 worker에서 생성."""
    product = str(payload.get("product") or "").strip()
    if not product:
        return {"ok": False, "error": "missing product"}
    from routers import splittable

    return splittable._refresh_match_cache_products(
        [product], force=bool(payload.get("force"))
    )


@handler("splittable_view_recompute")
def _splittable_view_recompute(payload: dict) -> dict:
    """SplitTable view payload 재계산 (stale-while-revalidate 백그라운드 갱신).

    응답-페이로드 위임의 예외 케이스 — 재계산(polars collect)이 큐 왕복을
    압도하고, 입력(pivot 캐시·lot_progress 파생·plan/tag)은 전부 공유
    data_root 라 어느 서버가 계산해도 같은 결과다. 산출물인 view payload 는
    api 프로세스의 RAM 캐시 전용이라 파일로 남길 곳이 없으므로 결과 dict 로
    그대로 돌려주고, 저장(의존 시그니처 계산 포함)은 api 측이 한다 —
    soft_sig 에 서버-로컬 product RAM 캐시 서명이 섞여 있어 워커가 계산한
    시그니처는 api 캐시에 무의미하다."""
    product = str(payload.get("product") or "").strip()
    if not product:
        return {"ok": False, "error": "missing product"}
    from routers import splittable as _st

    params = {
        "product": product,
        "root_lot_id": str(payload.get("root_lot_id") or ""),
        "wafer_ids": str(payload.get("wafer_ids") or ""),
        "prefix": str(payload.get("prefix") or ""),
        "custom_name": str(payload.get("custom_name") or ""),
        "view_mode": str(payload.get("view_mode") or "all"),
        "history_mode": str(payload.get("history_mode") or "all"),
        "fab_lot_id": str(payload.get("fab_lot_id") or ""),
        "custom_cols": str(payload.get("custom_cols") or ""),
    }
    key = _st._split_view_cache_key(
        params["product"], params["root_lot_id"], params["wafer_ids"],
        params["prefix"], params["custom_name"], params["view_mode"],
        params["history_mode"], params["fab_lot_id"], params["custom_cols"],
    )
    # request=None + force → 캐시 서빙/감사로그/알림을 건너뛰고 순수 재계산.
    # include_related=False 명시 — 직접 호출에서는 Query(False) 기본값 객체가
    # truthy 라 related_issues 를 계산하고 cache_put 이 도로 버리는 낭비가 있다.
    _st._VIEW_REVALIDATE_TLS.force = True
    try:
        _st.view_split(request=None, include_related=False, **params)
    finally:
        _st._VIEW_REVALIDATE_TLS.force = False
    # view_split(force) 는 이 프로세스의 _VIEW_CACHE 에 저장한다 — 그 저장본
    # (related/runtime 필드가 제거된 캐시 형태)을 돌려준다. 결과 파일은 stdlib
    # json 으로 기록되므로 rows 에 datetime/numpy 등이 섞이면 깨진다 — HTTP
    # 응답(_view_orjson_response)과 같은 옵션으로 여기서 미리 문자열화한다.
    with _st._VIEW_CACHE_LOCK:
        entry = _st._VIEW_CACHE.get(key)
    if not entry:
        return {"ok": False, "error": "recompute stored no view cache entry"}
    try:
        import orjson
        payload_json = orjson.dumps(
            entry[2], default=str,
            option=orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_NON_STR_KEYS,
        ).decode("utf-8")
    except Exception as exc:
        return {"ok": False, "error": f"payload serialize failed: {type(exc).__name__}: {exc}"}
    return {"ok": True, "product": product, "payload_json": payload_json}


@handler("flowi_chat_turn")
def _flowi_chat_turn(payload: dict) -> dict:
    """Flow-i 채팅 턴 실행 (데이터 컨텍스트 빌드 + LLM 호출).

    응답-페이로드 위임의 예외 케이스 — LLM 대기가 지배해 큐 왕복이 체감되지
    않고, 부수 상태(차트 세션·유저 이벤트 md·활동 로그)는 공유 data_root 에
    남는다. HTTPException 은 http_error 봉투로 돌려 api 가 로컬 재실행 없이
    그대로 변환한다 (LLM 이중 호출·이중 이벤트 기록 방지)."""
    from fastapi import HTTPException

    from core import llm_adapter
    from routers.llm import _run_flowi_chat

    # LLM 설정은 공유 admin_settings.json 이 기본이지만 API 키가 운영 env 에만
    # 있는 배포도 있다 — 이 워커에서 LLM 이 불가하면 실행하지 않고 돌려보내
    # api 가 로컬로 돌게 한다 (저품질 "LLM 미설정" 응답이 조용히 나가는 것 방지).
    if not llm_adapter.is_available():
        return {"ok": False, "error": "llm_unavailable_on_worker"}

    agent_context = payload.get("agent_context")
    try:
        result = _run_flowi_chat(
            prompt=str(payload.get("prompt") or ""),
            product=str(payload.get("product") or ""),
            max_rows=int(payload.get("max_rows") or 0),
            me=payload.get("me") or {},
            source_ai=str(payload.get("source_ai") or ""),
            client_run_id=str(payload.get("client_run_id") or ""),
            agent_context=agent_context if isinstance(agent_context, dict) else None,
            allow_rag_update=bool(payload.get("allow_rag_update")),
        )
        return {"ok": True, "result": result}
    except HTTPException as exc:
        return {"ok": True, "http_error": {"status": int(exc.status_code), "detail": exc.detail}}


@handler("lot_progress_cache_refresh")
def _lot_progress_cache_refresh(payload: dict) -> dict:
    """LOT progress(latest lot) 캐시 재스캔 (결과: 공유 lot_progress 캐시 JSON).

    refresh_lot_progress_cache 자체가 cross-process refresh lock + fresh-file
    스킵을 갖고 있어 그대로 위임한다 — api 가 로컬 폴백해도 같은 락을 지난다.
    items 목록은 크므로 요약만 반환 (본문은 공유 캐시 파일로 전달)."""
    from core import lot_progress_cache

    raw_age = payload.get("max_age_seconds")
    try:
        max_age = int(raw_age) if raw_age else None
    except Exception:
        max_age = None
    state = lot_progress_cache.load_lot_progress_cache(max_age_seconds=max_age)
    return {
        "ok": bool(state.get("generated_at")),
        "count": int(state.get("count") or 0),
        "files_scanned": int(state.get("files_scanned") or 0),
        "generated_at": str(state.get("generated_at") or ""),
        "skipped_by_lock": bool(state.get("skipped_by_lock")),
    }


@handler("et_tracker_scan")
def _et_tracker_scan(payload: dict) -> dict:
    """ET Tracker 스캔 phase — root_lot/wafer 기준 ET DB 조회 + et_history diff.

    v9.5.14: 양산(api)에서 스케줄이 돌고, 개발 워커가 켜져 있으면 이 핸들러가
    무거운 DB 스캔을 대신 수행한다. 입력(issues.json·ET DB)은 공유 data_root
    이므로 어느 서버가 계산해도 같은 결과 — 저장·bell·메일(apply phase)은
    api 서버가 결과 dict 를 받아 수행한다 (worker 는 메일 발송이 차단됨)."""
    from core.et_tracker import scan_phase
    result = scan_phase(only_issue_id=str(payload.get("only_issue_id") or ""))
    result["executed_on"] = "worker"
    return result


@handler("ml_lookup_cache_build")
def _ml_lookup_cache_build(payload: dict) -> dict:
    """ML_TABLE root_lot 파티션 lookup 캐시 빌드 (결과: db cache 파티션 트리).

    build_lookup_cache 자체가 cross-server 빌드 락 + fresh 스킵을 갖고 있어
    그대로 위임한다."""
    from core import ml_table_lookup

    # 절대경로는 서버별 mount 차이 때문에 transport 식별자로 쓰지 않는다.
    # logical product/file을 먼저 worker의 PATHS에서 해석하고, 구버전 api가 보낸
    # source_path는 같은 경로가 실제 존재할 때만 호환 폴백으로 사용한다.
    product = str(payload.get("product") or "").strip()
    file_name = str(payload.get("file") or "").strip()
    fp = ml_table_lookup.resolve_ml_table_file(product=product, file=file_name)
    source_raw = str(payload.get("source_path") or "").strip()
    if fp is None and source_raw:
        candidate = Path(source_raw)
        if candidate.is_file():
            fp = candidate
    if fp is None:
        logical = file_name or product or source_raw or "(empty)"
        return {"ok": False, "error": f"source not found on worker: {logical}"}
    res = ml_table_lookup.build_lookup_cache(fp, force=bool(payload.get("force")))
    fresh = ml_table_lookup.cache_status(fp).get("status") == "fresh"
    ok = bool(res.get("ok")) and fresh
    return {
        # build_lock_held 같은 정상 skip도 cache가 stale이면 작업 성공이 아니다.
        # transport 실패로 돌려 api의 로컬 폴백/제한 재시도를 활성화한다.
        "ok": ok,
        "skipped": bool(res.get("skipped")),
        "reason": str(res.get("reason") or ""),
        "error": "" if ok else str(res.get("error") or res.get("reason") or "lookup cache not fresh"),
        "cache_dir": str(res.get("cache_dir") or ""),
        "build_seconds": (res.get("meta") or {}).get("build_seconds"),
    }
