"""Transfer-size and tail-read contracts shared by every Flow screen."""
from __future__ import annotations

import gzip
import json

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.testclient import TestClient


def _app():
    from app_v2.runtime.http_compression import GzipJsonMiddleware

    app = FastAPI()

    @app.get("/big")
    def big():
        return {"rows": [{"lot": f"A{i:04d}", "value": i} for i in range(2000)]}

    @app.get("/small")
    def small():
        return {"ok": True}

    @app.get("/download")
    def download():
        body = json.dumps({"rows": list(range(5000))}).encode()
        return JSONResponse(json.loads(body), headers={"Content-Disposition": "attachment; filename=x.json"})

    @app.get("/stream")
    def stream():
        return StreamingResponse(iter([b"data: a\n\n" * 400, b"data: b\n\n" * 400]), media_type="text/event-stream")

    @app.get("/chunked-json")
    def chunked_json():
        # BaseHTTPMiddleware re-streams JSON in several body messages.
        body = json.dumps({"rows": list(range(3000))}).encode()
        return StreamingResponse(iter([body[:5000], body[5000:]]), media_type="application/json")

    app.add_middleware(GzipJsonMiddleware)
    return app


def test_large_json_is_gzipped_and_round_trips():
    client = TestClient(_app())
    raw = client.get("/big", headers={"Accept-Encoding": "gzip"})
    assert raw.headers["content-encoding"] == "gzip"
    assert "accept-encoding" in raw.headers["vary"].lower()
    assert len(raw.json()["rows"]) == 2000  # client transparently decodes


def test_small_streaming_and_attachment_responses_pass_through():
    client = TestClient(_app())
    for path in ("/small", "/stream", "/download"):
        response = client.get(path, headers={"Accept-Encoding": "gzip"})
        assert "content-encoding" not in response.headers, path
    assert client.get("/stream").text.startswith("data: a")


def test_chunked_json_is_collected_and_gzipped():
    client = TestClient(_app())
    response = client.get("/chunked-json", headers={"Accept-Encoding": "gzip"})
    assert response.headers["content-encoding"] == "gzip"
    assert response.json()["rows"][-1] == 2999


def test_no_gzip_without_accept_encoding(monkeypatch):
    client = TestClient(_app())
    response = client.get("/big", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in response.headers
    monkeypatch.setenv("FLOW_HTTP_GZIP", "0")
    assert "content-encoding" not in client.get("/big", headers={"Accept-Encoding": "gzip"}).headers


def test_static_hashed_assets_are_immutable_and_gzipped(tmp_path):
    import os
    from app_v2.runtime import http_compression

    asset = tmp_path / "My_Home-CiEMup7r.js"
    asset.write_text("console.log('flow');\n" * 200, encoding="utf-8")
    assert http_compression.is_hashed_asset(asset)
    assert not http_compression.is_hashed_asset(tmp_path / "favicon.svg")
    scope = {"type": "http", "headers": [(b"accept-encoding", b"gzip, deflate")]}
    response = http_compression.static_gzip_response(asset, os.stat(asset), scope, "public, max-age=31536000, immutable")
    assert response is not None
    assert response.headers["content-encoding"] == "gzip"
    assert gzip.decompress(response.body) == asset.read_bytes()


def test_jsonl_read_tail_matches_forward_scan(tmp_path):
    from core.utils import jsonl_iter, jsonl_read

    path = tmp_path / "log.jsonl"
    lines = [json.dumps({"i": i, "even": i % 2 == 0}) for i in range(5000)]
    lines.insert(1234, "{broken")
    path.write_text("\n".join(lines) + "\n" + '{"i": "partial', encoding="utf-8")

    forward = [e for e in jsonl_iter(path)]
    assert jsonl_read(path, 50) == forward[-50:]
    evens = [e for e in forward if e["even"]]
    assert jsonl_read(path, 30, lambda e: e["even"]) == evens[-30:]
    assert jsonl_read(path, 0) == forward
    assert jsonl_read(path, 10_000) == forward
    assert jsonl_read(tmp_path / "missing.jsonl", 10) == []
