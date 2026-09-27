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
"""
from __future__ import annotations

import datetime as _dt
import decimal
import enum
import json
from pathlib import PurePath
from typing import Any

from fastapi.responses import Response

try:  # 선택 의존성 — 오프라인 설치본에는 없을 수 있다
    import orjson as _orjson  # type: ignore
except Exception:  # pragma: no cover - 환경 의존
    _orjson = None


def _default(obj: Any) -> Any:
    if type(obj).__module__ == "numpy" and hasattr(obj, "item"):
        return obj.item()
    if isinstance(obj, (_dt.datetime, _dt.date, _dt.time)):
        return obj.isoformat()
    if isinstance(obj, (set, frozenset, tuple)):
        return list(obj)
    if isinstance(obj, PurePath):
        return str(obj)
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    if isinstance(obj, enum.Enum):
        return obj.value
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
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
