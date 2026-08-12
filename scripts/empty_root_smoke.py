#!/usr/bin/env python3
"""Boot Flow against empty runtime/data roots and check core endpoints.

This smoke protects the code-only checkout contract: GitHub does not carry
runtime data, but the app must still start, create the seed admin, and return
empty or explicit no-data responses instead of 500 errors.
"""
from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
ADMIN_USER = os.environ.get("FLOW_USER", "hol")
ADMIN_PW = os.environ.get("FLOW_PW", "hol12345!")
STARTUP_TIMEOUT = int(os.environ.get("FLOW_EMPTY_ROOT_STARTUP_TIMEOUT", "45"))

PASS = 0
FAIL = 0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _req(method: str, base: str, path: str, body=None, token: str = "", timeout: int = 10):
    url = base.rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Session-Token"] = token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw) if raw else {}
            except Exception:
                parsed = raw
            return resp.status, parsed
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw.strip().startswith("{") else raw
        except Exception:
            parsed = raw
        return exc.code, parsed
    except Exception as exc:
        return 0, f"{type(exc).__name__}: {exc}"


def _check(name: str, status: int, expected=200, detail: str = "") -> bool:
    global PASS, FAIL
    exp = expected if isinstance(expected, list) else [expected]
    ok = status in exp
    print(f"{'PASS' if ok else 'FAIL'} {name} [{status}] {detail}".rstrip())
    if ok:
        PASS += 1
    else:
        FAIL += 1
    return ok


def _wait_for_server(base: str, proc: subprocess.Popen) -> None:
    deadline = time.time() + STARTUP_TIMEOUT
    last = ""
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"uvicorn exited early with code {proc.returncode}")
        status, body = _req("GET", base, "/version.json", timeout=2)
        if status == 200:
            return
        last = str(body)[:200]
        time.sleep(0.5)
    raise RuntimeError(f"server did not start within {STARTUP_TIMEOUT}s: {last}")


def _json_summary(value) -> str:
    if isinstance(value, dict):
        keys = ", ".join(sorted(str(k) for k in value.keys())[:5])
        return f"keys={keys}"
    if isinstance(value, list):
        return f"items={len(value)}"
    return str(value)[:100]


def main() -> int:
    port = int(os.environ.get("FLOW_EMPTY_ROOT_PORT") or _free_port())
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="flow-empty-root-") as tmp:
        tmp_root = Path(tmp)
        data_root = tmp_root / "flow-data"
        db_root = tmp_root / "DB"
        data_root.mkdir()
        db_root.mkdir()

        env = os.environ.copy()
        env["FLOW_DATA_ROOT"] = str(data_root)
        env["FLOW_DB_ROOT"] = str(db_root)
        env["FLOW_ADMIN_PW"] = ADMIN_PW
        env.pop("FLOW_PROD", None)

        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            _wait_for_server(base, proc)

            status, body = _req("GET", base, "/runtime-roots.json")
            paths = body.get("paths", {}) if isinstance(body, dict) else {}
            roots_ok = paths.get("data_root") == str(data_root) and paths.get("db_root") == str(db_root)
            detail = f"data_root={paths.get('data_root')} db_root={paths.get('db_root')}"
            _check("/runtime-roots.json", status if roots_ok else 0, 200, detail)

            status, body = _req("POST", base, "/api/auth/login", {"username": ADMIN_USER, "password": ADMIN_PW})
            token = body.get("token") if isinstance(body, dict) else ""
            detail = f"user={body.get('username') if isinstance(body, dict) else ''}"
            _check("login seed admin", status if token else 0, 200, detail)
            if not token:
                raise RuntimeError(f"login failed: {body}")

            probes = [
                ("admin settings", "GET", "/api/admin/settings", 200),
                ("filebrowser roots", "GET", "/api/filebrowser/roots", 200),
                ("filebrowser scopes", "GET", "/api/filebrowser/scopes", 200),
                ("filebrowser base files", "GET", "/api/filebrowser/base-files", 200),
                ("splittable products", "GET", "/api/splittable/products", 200),
                ("tracker issues", "GET", "/api/tracker/issues?limit=5", 200),
                ("tracker products", "GET", "/api/tracker/products?limit=5", 200),
                ("agent prompt history", "GET", "/api/agent/prompt-history?limit=5", 200),
                ("agent knowledge overview", "GET", "/api/agent/knowledge/overview?limit=5", 200),
            ]
            for name, method, path, expected in probes:
                status, body = _req(method, base, path, token=token)
                if status >= 500:
                    _check(name, status, expected, _json_summary(body))
                    continue
                _check(name, status, expected, _json_summary(body))
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
            if FAIL:
                try:
                    output = proc.stdout.read() if proc.stdout else ""
                except Exception:
                    output = ""
                if output:
                    print("\n-- uvicorn output tail --")
                    print(output[-4000:])

    total = PASS + FAIL
    print(f"\nEMPTY ROOT SMOKE: {PASS}/{total} PASS, {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
