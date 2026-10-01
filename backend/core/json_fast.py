# -*- coding: utf-8 -*-
"""core/json_fast.py — 캐시된 응답을 요청마다 다시 인코딩하지 않기 위한 JSON 직렬화.

FastAPI 는 dict 를 돌려주면 `jsonable_encoder` 로 전체를 재귀 순회한 뒤 다시
`json.dumps` 한다. 캐시 적중으로 핸들러가 수 ms 에 끝나도 이 인코딩이 매번
붙는다(실측: SplitTable 공정 메타 290KB 46ms, TEG 위치 조회 지도 460개 16ms —
순수 파이썬이라 그동안 GIL 을 잡아 다른 사용자의 검색까지 멈춘다).

`dumps_bytes()` 는 FastAPI `JSONResponse` 와 같은 JSON 을 만든다(ensure_ascii=False,
공백 없는 구분자). orjson 이 있으면 쓰고, 없으면 표준 json 이다. 알 수 없는 타입은
`TypeError` — 호출측은 예전 경로(dict 반환)로 폴백한다. `str()` 로 뭉개서 숫자가
문자열로 바뀌는 일이 없게 하기 위함이다.

`wrap_router_endpoints()` 는 이 직렬화를 **모든** 라우터에 적용한다(라우터 로더가
include 직전에 부른다). FastAPI 는 dict 반환을 핸들러가 sync 여도 이벤트 루프에서
`jsonable_encoder` 로 순회하므로, 1만 행×20열 목록(≈0.5초) 동안 /health 를 포함한
모든 요청이 멈췄다. 감싼 endpoint 는 결과를 핸들러 스레드에서 바로 bytes 로 만들어
Response 로 돌려준다. response_model·전용 응답 클래스·스트리밍·`Response` 인자를
쓰는 경로는 FastAPI 기본 처리를 그대로 탄다.
"""
from __future__ import annotations

import collections
import datetime as _dt
import decimal
import enum
import functools
import inspect
import json
import types
from pathlib import PurePath
from typing import Any, Callable

from fastapi.responses import Response

try:  # 선택 의존성 — 오프라인 설치본에는 없을 수 있다
    import orjson as _orjson  # type: ignore
except Exception:  # pragma: no cover - 환경 의존
    _orjson = None


def _default(obj: Any) -> Any:
    # FastAPI jsonable_encoder 와 같은 변환(없는 타입은 TypeError → 호출측 폴백).
    if type(obj).__module__ == "numpy" and hasattr(obj, "item"):
        return obj.item()
    if isinstance(obj, (_dt.datetime, _dt.date, _dt.time)):
        return obj.isoformat()
    if isinstance(obj, (set, frozenset, tuple, collections.deque, types.GeneratorType)):
        return list(obj)
    if isinstance(obj, PurePath):
        return str(obj)
    if isinstance(obj, decimal.Decimal):
        return int(obj) if obj.as_tuple().exponent >= 0 else float(obj)
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, (bytes, bytearray)):
        return bytes(obj).decode()
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json", by_alias=True)
        except TypeError:
            return dump()
    raise TypeError(f"JSON 직렬화 불가 타입: {type(obj).__name__}")


def dumps_bytes(payload: Any) -> bytes:
    """payload → UTF-8 JSON bytes. 직렬화할 수 없으면 TypeError/ValueError."""
    if _orjson is not None:
        return _orjson.dumps(
            payload, default=_default,
            option=_orjson.OPT_SERIALIZE_NUMPY | _orjson.OPT_NON_STR_KEYS,
        )
    return json.dumps(payload, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":"), default=_default).encode("utf-8")


def response(body: bytes, status_code: int = 200) -> Response:
    return Response(content=body, status_code=status_code, media_type="application/json")


# ── 라우터 전체 적용 ──

_WRAPPED_ATTR = "__flow_json_fast__"
_NO_BODY_STATUS = {204, 205, 304}


def encode_result(result: Any, status_code: int | None = None) -> Any:
    """endpoint 반환값 → JSON Response. 이미 Response 거나 직렬화할 수 없으면 그대로
    돌려준다(FastAPI 기본 경로가 예전처럼 처리하거나 같은 오류를 낸다)."""
    if isinstance(result, Response):
        return result
    try:
        body = dumps_bytes(result)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return result
    return response(body, status_code or 200)


def _uses_response_param(dependant: Any) -> bool:
    # `response: Response` 로 헤더·쿠키·상태를 바꾸는 endpoint/의존성은 FastAPI 가
    # 직접 만든 응답에만 그 값을 옮긴다 → 감싸지 않는다.
    if dependant is None:
        return False
    if getattr(dependant, "response_param_name", None):
        return True
    return any(_uses_response_param(sub) for sub in getattr(dependant, "dependencies", None) or [])


def _is_default_json_class(response_class: Any) -> bool:
    from fastapi.datastructures import DefaultPlaceholder
    from fastapi.responses import JSONResponse

    if isinstance(response_class, DefaultPlaceholder):
        response_class = response_class.value
    return response_class is JSONResponse


def _eligible(route: Any) -> bool:
    from fastapi.routing import APIRoute

    if not isinstance(route, APIRoute):
        return False
    endpoint = route.endpoint
    if getattr(endpoint, _WRAPPED_ATTR, False) or not callable(endpoint):
        return False
    if route.response_model is not None or not _is_default_json_class(route.response_class):
        return False
    status = route.status_code
    if status is not None and (int(status) < 200 or int(status) in _NO_BODY_STATUS):
        return False
    target = inspect.unwrap(endpoint)
    if not (inspect.isfunction(target) or inspect.ismethod(target)):
        return False
    if inspect.isgeneratorfunction(target) or inspect.isasyncgenfunction(target):
        return False
    return not _uses_response_param(getattr(route, "dependant", None))


def _wrap_endpoint(endpoint: Callable[..., Any], status_code: int | None) -> Callable[..., Any]:
    # 주석이 문자열(from __future__ import annotations)이면 FastAPI 버전에 따라 감싼
    # 함수의 전역에서 풀려다 실패한다 → 원래 모듈 기준으로 푼 시그니처를 붙인다.
    signature = inspect.signature(endpoint, eval_str=True)
    signature = signature.replace(return_annotation=inspect.Signature.empty)
    if inspect.iscoroutinefunction(inspect.unwrap(endpoint)):
        @functools.wraps(endpoint)
        async def fast_endpoint(*args, **kwargs):
            return encode_result(await endpoint(*args, **kwargs), status_code)
    else:
        @functools.wraps(endpoint)
        def fast_endpoint(*args, **kwargs):
            return encode_result(endpoint(*args, **kwargs), status_code)
    fast_endpoint.__signature__ = signature  # type: ignore[attr-defined]
    fast_endpoint.__annotations__ = {
        name: param.annotation
        for name, param in signature.parameters.items()
        if param.annotation is not inspect.Parameter.empty
    }
    setattr(fast_endpoint, _WRAPPED_ATTR, True)
    return fast_endpoint


def wrap_router_endpoints(router: Any) -> dict:
    """include 전에 router 의 JSON endpoint 를 감싼다. 반환: 감쌈/건너뜀 수.

    FastAPI 는 include 할 때 route.endpoint 로 앱 쪽 route 를 새로 만든다(0.13x 는
    즉시, 0.141+ 는 첫 요청 때) — 그래서 include **전에** endpoint 를 바꿔야 한다.
    `FLOW_JSON_FAST_ROUTES=0` 이면 아무것도 하지 않는다(기존 FastAPI 경로)."""
    import os

    stats = {"wrapped": 0, "skipped": 0, "failed": 0}
    if str(os.environ.get("FLOW_JSON_FAST_ROUTES", "1")).strip().lower() in {"0", "false", "no", "off"}:
        return stats
    for route in list(getattr(router, "routes", None) or []):
        nested = getattr(route, "original_router", None)  # FastAPI 0.141+ 의 _IncludedRouter
        if nested is not None:
            sub = wrap_router_endpoints(nested)
            for key in stats:
                stats[key] += sub[key]
            continue
        if not _eligible(route):
            stats["skipped"] += 1
            continue
        try:
            route.endpoint = _wrap_endpoint(route.endpoint, route.status_code)
        except Exception:
            stats["failed"] += 1
            continue
        stats["wrapped"] += 1
    return stats
