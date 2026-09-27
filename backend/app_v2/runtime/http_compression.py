"""Response compression for API JSON and built frontend assets.

Flow serves large JSON bodies (process meta, catalogs, chart rows, cache logs)
and a 1.5 MB Plotly bundle over the company network. Neither path compressed
anything, so every screen paid the full transfer size.

Only complete single-message bodies are compressed. Streaming responses
(SSE, CSV/XLSX exports, file downloads) and attachments pass through unchanged,
so progress reporting and download byte counts stay exact.
"""
from __future__ import annotations

import gzip
import os
import re
import threading

import anyio
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import Response

_COMPRESSIBLE_TYPES = (
    "application/json",
    "application/javascript",
    "text/javascript",
    "text/css",
    "text/html",
    "text/plain",
    "image/svg+xml",
)
# Above this size the compression runs in a worker thread so one big payload
# cannot stall the event loop for every other request.
_THREAD_THRESHOLD_BYTES = 256 * 1024


def _enabled() -> bool:
    return str(os.environ.get("FLOW_HTTP_GZIP", "1")).strip().lower() not in {"0", "false", "no", "off"}


def _level() -> int:
    try:
        return max(1, min(9, int(os.environ.get("FLOW_HTTP_GZIP_LEVEL", "5"))))
    except ValueError:
        return 5


def _accepts_gzip(scope) -> bool:
    return "gzip" in Headers(scope=scope).get("accept-encoding", "").lower()


def _compressible(headers: Headers) -> bool:
    if headers.get("content-encoding"):
        return False
    if "attachment" in headers.get("content-disposition", "").lower():
        return False
    content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    return content_type in _COMPRESSIBLE_TYPES


# A compressible body larger than this is sent as-is instead of being held in
# memory; normal API payloads are far smaller.
_MAX_BUFFER_BYTES = 32 * 1024 * 1024


class GzipJsonMiddleware:
    """Pure ASGI gzip for complete, compressible responses.

    The app's BaseHTTPMiddleware layers re-stream every response in chunks
    (``more_body=True``), so the compressible types (JSON, JS, CSS, HTML,
    plain text) are collected and compressed once complete. SSE, CSV/XLSX
    exports and other types are never buffered.
    """

    def __init__(self, app, minimum_size: int = 1400):
        self.app = app
        self.minimum_size = minimum_size

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not _enabled() or not _accepts_gzip(scope):
            await self.app(scope, receive, send)
            return

        start = None
        passthrough = False
        chunks: list[bytes] = []
        buffered = 0

        async def flush_raw(final_message):
            nonlocal passthrough
            passthrough = True
            await send(start)
            if chunks:
                await send({"type": "http.response.body", "body": b"".join(chunks), "more_body": True})
                chunks.clear()
            await send(final_message)

        async def wrapped_send(message):
            nonlocal start, passthrough, buffered
            if passthrough:
                await send(message)
                return
            if message["type"] == "http.response.start":
                if not _compressible(Headers(raw=message.get("headers") or [])):
                    passthrough = True
                    await send(message)
                    return
                start = message
                return
            if message["type"] != "http.response.body" or start is None:
                await send(message)
                return
            body = message.get("body", b"")
            more = message.get("more_body", False)
            if more:
                chunks.append(body)
                buffered += len(body)
                if buffered > _MAX_BUFFER_BYTES:
                    await flush_raw({"type": "http.response.body", "body": b"", "more_body": True})
                return
            chunks.append(body)
            payload = b"".join(chunks)
            chunks.clear()
            if len(payload) < self.minimum_size:
                await send(start)
                await send({"type": "http.response.body", "body": payload, "more_body": False})
                return
            level = _level()
            if len(payload) >= _THREAD_THRESHOLD_BYTES:
                compressed = await anyio.to_thread.run_sync(gzip.compress, payload, level)
            else:
                compressed = gzip.compress(payload, level)
            headers = MutableHeaders(raw=list(start.get("headers") or []))
            headers["Content-Encoding"] = "gzip"
            headers["Content-Length"] = str(len(compressed))
            headers.add_vary_header("Accept-Encoding")
            await send({**start, "headers": headers.raw})
            await send({"type": "http.response.body", "body": compressed, "more_body": False})

        await self.app(scope, receive, wrapped_send)


# Vite emits content-hashed names such as `My_Home-CiEMup7r.js`.
_HASHED_ASSET = re.compile(r"-[A-Za-z0-9_-]{8}\.(?:js|css|svg|woff2?)$")
_STATIC_GZIP_SUFFIXES = (".js", ".css", ".svg", ".json", ".html")
_STATIC_GZIP_MAX_BYTES = 8 * 1024 * 1024
_static_cache: dict[tuple[str, int, int], bytes] = {}
_static_cache_bytes = 0
_static_lock = threading.Lock()
_STATIC_CACHE_LIMIT_BYTES = 32 * 1024 * 1024


def is_hashed_asset(path: str) -> bool:
    return bool(_HASHED_ASSET.search(str(path)))


def _gzip_static(path: str, stat_result) -> bytes:
    global _static_cache_bytes
    key = (path, int(stat_result.st_mtime_ns), int(stat_result.st_size))
    with _static_lock:
        cached = _static_cache.get(key)
    if cached is not None:
        return cached
    with open(path, "rb") as stream:
        compressed = gzip.compress(stream.read(), 6)
    with _static_lock:
        if _static_cache_bytes + len(compressed) > _STATIC_CACHE_LIMIT_BYTES:
            _static_cache.clear()
            _static_cache_bytes = 0
        _static_cache[key] = compressed
        _static_cache_bytes += len(compressed)
    return compressed


def static_gzip_response(full_path, stat_result, scope, cache_control: str) -> Response | None:
    """Return a gzip response for a built text asset, or None to serve it raw."""
    path = str(full_path)
    if (not _enabled() or not _accepts_gzip(scope)
            or not path.lower().endswith(_STATIC_GZIP_SUFFIXES)
            or not 1024 <= stat_result.st_size <= _STATIC_GZIP_MAX_BYTES):
        return None
    try:
        body = _gzip_static(path, stat_result)
    except OSError:
        return None
    media_type = {
        ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml",
        ".json": "application/json", ".html": "text/html",
    }[os.path.splitext(path)[1].lower()]
    return Response(body, media_type=media_type, headers={
        "Content-Encoding": "gzip",
        "Cache-Control": cache_control,
        "Vary": "Accept-Encoding",
    })
