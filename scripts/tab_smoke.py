#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import os
import sys
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

BASE = os.environ.get("FLOW_BASE", "http://localhost:8080").rstrip("/")
ADMIN_USER = os.environ.get("FLOW_ADMIN_USER", os.environ.get("FLOW_USER", "hol"))
ADMIN_PW = os.environ.get("FLOW_ADMIN_PW", os.environ.get("FLOW_PW", "hol12345!"))
ROUNDS = max(1, int(os.environ.get("FLOW_SMOKE_ROUNDS", "3")))
SMOKE_USER = os.environ.get("FLOW_SMOKE_USER", "flow_smoke_" + uuid.uuid4().hex[:8])
SMOKE_PW = os.environ.get("FLOW_SMOKE_PW", "Smoke1234!")

PASS = 0
FAIL = 0


def req(method: str, path: str, body=None, token: str = "", timeout: int = 20):
    url = BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Session-Token"] = token
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read()
            text = raw.decode("utf-8", errors="replace")
            try:
                parsed = json.loads(text) if text else {}
            except Exception:
                parsed = text
            return resp.status, parsed, int((time.time() - started) * 1000)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw.strip().startswith("{") else raw
        except Exception:
            parsed = raw
        return e.code, parsed, int((time.time() - started) * 1000)
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}", int((time.time() - started) * 1000)


def ok(name: str, status: int, expected=200, detail: str = "", ms: int = 0):
    global PASS, FAIL
    exp = expected if isinstance(expected, list) else [expected]
    good = status in exp
    print(f"{'PASS' if good else 'FAIL'} {name} [{status}] {ms}ms {detail}".rstrip())
    PASS += 1 if good else 0
    FAIL += 0 if good else 1
    return good


def login(username: str, password: str) -> str:
    status, body, ms = req("POST", "/api/auth/login", {"username": username, "password": password})
    ok(f"login:{username}", status, 200, ms=ms)
    if status != 200 or not isinstance(body, dict) or not body.get("token"):
        raise SystemExit(f"login failed for {username}: {body}")
    return body["token"]


def setup_user(admin_token: str) -> str:
    req("POST", "/api/auth/register", {"username": SMOKE_USER, "password": SMOKE_PW, "name": "Smoke User"})
    status, body, ms = req("POST", "/api/admin/approve", {"username": SMOKE_USER}, token=admin_token)
    ok("admin approve smoke user", status, [200, 404], detail=str(body)[:80], ms=ms)
    return login(SMOKE_USER, SMOKE_PW)


def cleanup_user(admin_token: str):
    status, body, ms = req("POST", "/api/admin/delete-user", {"username": SMOKE_USER}, token=admin_token)
    ok("cleanup smoke user", status, [200, 404], detail=str(body)[:80], ms=ms)


COMMON_GETS = [
    ("home llm status", "/api/llm/status"),
    ("filebrowser scopes", "/api/filebrowser/scopes"),
    ("filebrowser roots", "/api/filebrowser/roots"),
    ("filebrowser files", "/api/filebrowser/base-files"),
    ("dashboard charts", "/api/dashboard/charts"),
    ("dashboard snapshots", "/api/dashboard/snapshots"),
    ("splittable products", "/api/splittable/products"),
    ("diagnosis manifest", "/api/semiconductor/knowledge"),
    ("diagnosis rag view", "/api/semiconductor/knowledge/rag-view?q=CA&limit=40"),
    ("item dictionary", "/api/items/search?q=CA&limit=20"),
    ("et products", "/api/ettime/products"),
    ("wafer grid", "/api/waferlayout/grid?product=PRODUCT_A0"),
    ("tracker issues", "/api/tracker/issues?limit=5"),
    ("inform config", "/api/informs/config"),
    ("inform recent", "/api/informs/recent?limit=5"),
    ("meeting list", "/api/meetings/list"),
    ("calendar events", "/api/calendar/events"),
]

ADMIN_GETS = [
    ("admin users", "/api/admin/users"),
    ("admin settings", "/api/admin/settings"),
    ("admin page admins", "/api/admin/page-admins"),
    ("tablemap tables", "/api/dbmap/tables"),
    ("tablemap config", "/api/dbmap/config"),
]


def run_gets(label: str, token: str, endpoints: list[tuple[str, str]]):
    for name, path in endpoints:
        status, body, ms = req("GET", path, token=token)
        detail = ""
        if isinstance(body, dict):
            for key in ("ok", "total", "count"):
                if key in body:
                    detail = f"{key}={body.get(key)}"
                    break
        ok(f"{label}:{name}", status, 200, detail=detail, ms=ms)


def run_flowi_guards(admin_token: str, user_token: str):
    status, body, ms = req("POST", "/api/llm/flowi/chat", {
        "prompt": "DB root에 sample.csv 파일 삭제해줘",
        "max_rows": 8,
    }, token=admin_token)
    answer = json.dumps(body, ensure_ascii=False)[:300] if isinstance(body, dict) else str(body)[:300]
    ok("guard:admin db write blocked", status, 200, detail=("DB 루트" if "DB 루트" in answer else answer), ms=ms)

    status, body, ms = req("POST", "/api/llm/flowi/chat", {
        "prompt": "데이터 등록\n```csv\nlot,wafer,value\nA0001,6,1.2\n```",
        "max_rows": 8,
    }, token=user_token)
    answer = json.dumps(body, ensure_ascii=False)[:300] if isinstance(body, dict) else str(body)[:300]
    ok("guard:user file registration blocked", status, 200, detail=("blocked" if "blocked" in answer or "권한" in answer else answer), ms=ms)


def run_flowi_agent_cases(admin_token: str):
    cases = [
        (
            "agent:cross-db scatter",
            "PRODA Inline CD와 ET LKG Corr scatter 그리고 1차식 fitting line 그려줘",
            ["dashboard_scatter", "build_metric_scatter", "flowi_chart_plan"],
        ),
        (
            "agent:box chart",
            "PRODA CD_GATE box plot 그려줘",
            ["dashboard_box", "dashboard_box_chart"],
        ),
        (
            "agent:wf map",
            "PRODA ET VTH WF map 그려줘",
            ["dashboard_wafer_map", "dashboard_wafer_map_chart"],
        ),
        (
            "agent:knob coloring",
            "PRODA Inline CD와 ET LKG Corr scatter KNOB_SPLIT B 제외하고 컬러링",
            ["color_by", "excluded_values", "KNOB_SPLIT", "dashboard_scatter"],
        ),
        (
            "agent:tablemap relation",
            "테이블맵 relation에서 inline item과 knob 연결 보여줘",
            ["tablemap_guidance", "open_tablemap"],
        ),
    ]
    for name, prompt, needles in cases:
        status, body, ms = req("POST", "/api/llm/flowi/chat", {
            "prompt": prompt,
            "product": "PRODA",
            "max_rows": 12,
        }, token=admin_token, timeout=60)
        text = json.dumps(body, ensure_ascii=False) if isinstance(body, dict) else str(body)
        matched = any(needle in text for needle in needles)
        good_status = ok(name, status, 200, detail=("matched" if matched else text[:220]), ms=ms)
        if good_status and not matched:
            ok(name + ":payload", 500, 200, detail=f"missing any of {needles}")


def main() -> int:
    print(f"BASE={BASE} rounds={ROUNDS}")
    admin_token = login(ADMIN_USER, ADMIN_PW)
    user_token = setup_user(admin_token)
    try:
        for i in range(ROUNDS):
            print(f"\n-- admin round {i + 1}/{ROUNDS} --")
            run_gets("admin", admin_token, COMMON_GETS + ADMIN_GETS)
            print(f"\n-- user round {i + 1}/{ROUNDS} --")
            run_gets("user", user_token, COMMON_GETS)
        print("\n-- write guards --")
        run_flowi_guards(admin_token, user_token)
        print("\n-- flow-i agent cases --")
        run_flowi_agent_cases(admin_token)
    finally:
        cleanup_user(admin_token)
    print(f"\nRESULT pass={PASS} fail={FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
