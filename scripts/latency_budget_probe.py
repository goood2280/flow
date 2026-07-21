"""Probe Flow light API latency against the interactive budget.

The goal is to keep normal page hydration endpoints under 100 ms once the app
is warm. Large SQL/data scans and multi-agent work are intentionally excluded;
those paths should expose loading/status details instead of blocking the UI.

Usage:
  FLOW_BASE=http://127.0.0.1:8080 python3 scripts/latency_budget_probe.py
  FLOW_LATENCY_BUDGET_MS=100 FLOW_USER=hol FLOW_PW=... python3 scripts/latency_budget_probe.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request


BASE = os.environ.get("FLOW_BASE", "http://localhost:8080").rstrip("/")
USER = os.environ.get("FLOW_USER", "hol")
PW = os.environ.get("FLOW_PW", "hol12345!")
BUDGET_MS = int(os.environ.get("FLOW_LATENCY_BUDGET_MS", "100") or "100")
TIMEOUT_SEC = float(os.environ.get("FLOW_LATENCY_TIMEOUT_SEC", "10") or "10")

LIGHT_CHECKS = [
    ("home.release_notes", "GET", "/api/home/release-notes", None, (200,)),
    ("filebrowser.roots", "GET", "/api/filebrowser/roots", None, (200,)),
    ("dashboard.charts", "GET", "/api/dashboard/charts", None, (200,)),
    ("splittable.products", "GET", "/api/splittable/products", None, (200,)),
    ("tracker.issues", "GET", "/api/tracker/issues?limit=5", None, (200,)),
    ("inform.config", "GET", "/api/informs/config", None, (200,)),
    ("meeting.list", "GET", "/api/meetings/list", None, (200,)),
    ("calendar.events", "GET", "/api/calendar/events", None, (200,)),
    ("knowledge.wiki", "GET", "/api/knowledge/wiki?limit=10", None, (200,)),
    ("groups.list", "GET", "/api/groups/list", None, (200,)),
    ("messages.notices", "GET", "/api/messages/notices", None, (200,)),
    ("admin.settings", "GET", "/api/admin/settings", None, (200, 401, 403)),
]


def request(method: str, path: str, body=None, token: str = "") -> tuple[int, object, float]:
    url = BASE + path
    headers = {"Content-Type": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    if token:
        headers["X-Session-Token"] = token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as response:
            raw = response.read()
            elapsed_ms = (time.perf_counter() - started) * 1000
            ctype = response.headers.get("content-type", "")
            if "json" in ctype:
                return response.status, json.loads(raw.decode("utf-8")), elapsed_ms
            return response.status, raw.decode("utf-8", errors="replace"), elapsed_ms
    except urllib.error.HTTPError as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw.strip().startswith("{") else raw
        except Exception:
            parsed = raw
        return exc.code, parsed, elapsed_ms
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        return 0, f"{type(exc).__name__}: {exc}", elapsed_ms


def main() -> int:
    status, version_body, version_ms = request("GET", "/version.json")
    rows = [{
        "name": "server.version",
        "status": status,
        "latency_ms": round(version_ms, 1),
        "ok": status == 200 and version_ms <= BUDGET_MS,
    }]
    status, login_body, login_ms = request("POST", "/api/auth/login", {"username": USER, "password": PW})
    token = login_body.get("token", "") if isinstance(login_body, dict) else ""
    rows.append({
        "name": "auth.login",
        "status": status,
        "latency_ms": round(login_ms, 1),
        "ok": status == 200 and bool(token),
        "budget_note": "login is measured but not budget-gated because password hashing is intentionally heavier",
    })
    if not token:
        print(json.dumps({"ok": False, "base": BASE, "checks": rows}, ensure_ascii=False, indent=2))
        return 1

    status, _body, auth_ms = request("GET", "/api/auth/me", token=token)
    rows.append({
        "name": "auth.me",
        "status": status,
        "latency_ms": round(auth_ms, 1),
        "ok": status == 200 and auth_ms <= BUDGET_MS,
    })
    for name, method, path, body, expected in LIGHT_CHECKS:
        status, payload, elapsed_ms = request(method, path, body, token=token)
        rows.append({
            "name": name,
            "status": status,
            "latency_ms": round(elapsed_ms, 1),
            "ok": status in expected and elapsed_ms <= BUDGET_MS,
            "detail": str(payload)[:160] if status not in expected else "",
        })

    failed = [row for row in rows if not row.get("ok")]
    result = {
        "ok": not failed,
        "base": BASE,
        "budget_ms": BUDGET_MS,
        "version": version_body.get("version") if isinstance(version_body, dict) else "",
        "checks": rows,
        "excluded": [
            "large FileBrowser preview/download/SQL scans",
            "Flow-i LLM/multi-agent orchestration",
            "manual cache refresh/build jobs",
        ],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
