"""Router-wide JSON fast path (core.json_fast.wrap_router_endpoints).

FastAPI encodes a returned dict with ``jsonable_encoder`` on the event loop even
for sync handlers; a 10k-row list stalled every other request for ~0.5 s. The
wrapped endpoints must produce the same JSON while skipping that path, and the
routes FastAPI has to post-process (response_model, Response param, custom
response classes, streaming) must stay untouched.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import decimal
import enum
import json
import time
from pathlib import PurePosixPath

import pytest
from fastapi import APIRouter, BackgroundTasks, FastAPI, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from core import json_fast


@pytest.fixture(autouse=True)
def fast_routes_enabled(monkeypatch):
    monkeypatch.delenv("FLOW_JSON_FAST_ROUTES", raising=False)


class Color(enum.Enum):
    RED = "red"


class Item(BaseModel):
    item_name: str = Field(alias="itemName")
    qty: int = 1


class Loose:
    def __init__(self):
        self.a = 1
        self.b = "x"


def _payload():
    return {
        "rows": [{"lot": f"A{i:04d}", "v": i * 0.5, "ok": i % 2 == 0, "none": None} for i in range(50)],
        "when": dt.datetime(2026, 10, 1, 9, 30, 15, 123456),
        "day": dt.date(2026, 10, 1),
        "dec_int": decimal.Decimal("5"),
        "dec_frac": decimal.Decimal("1.25"),
        "tags": ("a", "b"),
        "color": Color.RED,
        "path": PurePosixPath("/db/x.parquet"),
        "model": Item(itemName="wafer", qty=3),
        "int_keys": {1: "one", 2: "two"},
        "text": "한글 ✓",
    }


def _build(wrap: bool) -> tuple[FastAPI, APIRouter, dict]:
    router = APIRouter(prefix="/api/t")
    ran: dict = {}

    @router.get("/payload")
    def payload():
        return _payload()

    @router.get("/payload-async")
    async def payload_async():
        return _payload()

    @router.get("/loose")
    def loose():
        return {"obj": Loose()}

    @router.get("/big-int")
    def big_int():
        return {"n": 2 ** 70}

    @router.post("/created", status_code=201)
    def created(item: Item):
        return {"got": item.item_name}

    @router.get("/model", response_model=Item)
    def model():
        return {"itemName": "kept", "qty": 2, "secret": "dropped"}

    @router.get("/header")
    def header(response: Response):
        response.headers["X-Flow"] = "yes"
        return {"ok": True}

    @router.get("/html", response_class=HTMLResponse)
    def html():
        return "<b>hi</b>"

    @router.get("/text", response_class=PlainTextResponse)
    def text():
        return "plain"

    @router.get("/explicit")
    def explicit():
        return JSONResponse({"explicit": True}, status_code=202)

    @router.get("/bg")
    def bg(background: BackgroundTasks):
        background.add_task(lambda: ran.setdefault("bg", True))
        return {"queued": True}

    stats = json_fast.wrap_router_endpoints(router) if wrap else {}
    app = FastAPI()
    app.include_router(router)
    return app, router, {"stats": stats, "ran": ran}


def test_wrapped_routes_match_fastapi_json_and_keep_special_routes():
    fast_app, router, info = _build(wrap=True)
    plain_app, _, _ = _build(wrap=False)
    wrapped = {r.path for r in router.routes if getattr(r.endpoint, json_fast._WRAPPED_ATTR, False)}
    assert wrapped == {
        "/api/t/payload", "/api/t/payload-async", "/api/t/loose", "/api/t/big-int",
        "/api/t/created", "/api/t/explicit", "/api/t/bg",
    }
    assert info["stats"]["wrapped"] == len(wrapped) and info["stats"]["failed"] == 0

    with TestClient(fast_app) as fast, TestClient(plain_app) as plain:
        for method, path, kwargs in (
            ("get", "/api/t/payload", {}),
            ("get", "/api/t/payload-async", {}),
            ("get", "/api/t/loose", {}),
            ("get", "/api/t/big-int", {}),
            ("post", "/api/t/created", {"json": {"itemName": "w1"}}),
            ("get", "/api/t/model", {}),
            ("get", "/api/t/header", {}),
            ("get", "/api/t/html", {}),
            ("get", "/api/t/text", {}),
            ("get", "/api/t/explicit", {}),
        ):
            a = getattr(fast, method)(path, **kwargs)
            b = getattr(plain, method)(path, **kwargs)
            assert a.status_code == b.status_code, path
            assert a.headers["content-type"] == b.headers["content-type"], path
            if b.headers["content-type"].startswith("application/json"):
                assert a.json() == b.json(), path
            else:
                assert a.text == b.text, path
        assert fast.post("/api/t/created", json={"itemName": "w1"}).status_code == 201
        assert fast.get("/api/t/model").json() == {"itemName": "kept", "qty": 2}
        assert fast.get("/api/t/header").headers["x-flow"] == "yes"
        assert fast.get("/api/t/bg").json() == {"queued": True}
    assert info["ran"].get("bg") is True


def test_wrapped_route_skips_fastapi_encoder_on_event_loop(monkeypatch):
    import fastapi.routing as fastapi_routing

    app, _, _ = _build(wrap=True)
    calls = []
    real = fastapi_routing.jsonable_encoder

    def spy(obj, *args, **kwargs):
        calls.append(type(obj).__name__)
        return real(obj, *args, **kwargs)

    monkeypatch.setattr(fastapi_routing, "jsonable_encoder", spy)
    with TestClient(app) as client:
        assert client.get("/api/t/payload").status_code == 200
        assert client.get("/api/t/payload-async").status_code == 200
        assert calls == []
        # Unknown object → FastAPI's own encoder, same as before the wrap.
        assert client.get("/api/t/loose").json() == {"obj": {"a": 1, "b": "x"}}
        assert calls


def test_wrap_is_idempotent_and_can_be_disabled(monkeypatch):
    _, router, info = _build(wrap=True)
    again = json_fast.wrap_router_endpoints(router)
    assert again["wrapped"] == 0
    monkeypatch.setenv("FLOW_JSON_FAST_ROUTES", "0")
    _, router_off, _ = _build(wrap=False)
    assert json_fast.wrap_router_endpoints(router_off)["wrapped"] == 0
    assert not any(getattr(r.endpoint, json_fast._WRAPPED_ATTR, False) for r in router_off.routes)


def test_big_list_response_does_not_stall_event_loop():
    """While a 10k-row response is produced, the event loop keeps turning."""
    router = APIRouter(prefix="/api/t")
    rows = [{f"c{j}": f"v{i}-{j}" if j % 2 else i * 1.5 for j in range(20)} for i in range(10_000)]

    @router.get("/big")
    def big():
        return {"rows": rows}

    json_fast.wrap_router_endpoints(router)
    app = FastAPI()
    app.include_router(router)

    import httpx

    async def exercise() -> tuple[float, int]:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            big_task = asyncio.create_task(client.get("/api/t/big"))
            worst = 0.0
            while not big_task.done():
                # A sleep that returns late means another task held the loop.
                t0 = time.perf_counter()
                await asyncio.sleep(0.001)
                worst = max(worst, time.perf_counter() - t0 - 0.001)
            resp = await big_task
            return worst, len(resp.json()["rows"])

    worst, count = asyncio.run(exercise())
    assert count == 10_000
    # Unwrapped, FastAPI's encoder held the loop ≈0.6 s on this payload; wrapped
    # it serializes in the handler thread (≈50 ms stdlib json, ≈12 ms orjson).
    assert worst < 0.25, worst


@pytest.mark.parametrize("value,expected", [
    (decimal.Decimal("5"), 5),
    (decimal.Decimal("1.5"), 1.5),
    (b"abc", "abc"),
    ({1, 2} - {2}, [1]),
])
def test_default_matches_jsonable_encoder(value, expected):
    assert json.loads(json_fast.dumps_bytes({"v": value})) == {"v": expected}
