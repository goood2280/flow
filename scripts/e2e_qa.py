#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_FILE = ROOT / "data" / "flow-data" / "qa_report.json"
USERS_FILE = ROOT / "data" / "flow-data" / "users.json"
BASE = os.environ.get("FLOW_BASE", "http://localhost:8080").rstrip("/")

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ensure_qa_users() -> dict:
    sys.path.insert(0, str(ROOT / "backend"))
    from core.auth import hash_password

    users = load_json(USERS_FILE, [])
    if not isinstance(users, list):
        users = []
    qa_accounts = {
        "qa_admin": {"password": "QaAdmin123!", "role": "admin", "tabs": "filebrowser,dashboard,splittable,ettime,waferlayout,tracker,inform,meeting,calendar,tablemap,ml,devguide"},
        "qa_user": {"password": "QaUser123!", "role": "user", "tabs": "filebrowser,dashboard,splittable,ettime,waferlayout,tracker,inform,meeting,calendar"},
    }
    changed = False
    by_name = {str(u.get("username") or ""): u for u in users if isinstance(u, dict)}
    for username, meta in qa_accounts.items():
        row = by_name.get(username)
        if not row:
            row = {"username": username}
            users.append(row)
            changed = True
        next_row = {
            **row,
            "username": username,
            "password_hash": hash_password(meta["password"]),
            "role": meta["role"],
            "status": "approved",
            "tabs": meta["tabs"],
            "name": username.replace("_", " ").title(),
            "email": f"{username}@local.test",
            "created": row.get("created") or now_iso(),
        }
        if next_row != row:
            changed = True
            row.clear()
            row.update(next_row)
    if changed:
        save_json(USERS_FILE, users)
    return {k: v["password"] for k, v in qa_accounts.items()}


def req(method: str, path: str, body=None, token: str = "", timeout: int = 12):
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Session-Token"] = token
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            latency_ms = int((time.perf_counter() - started) * 1000)
            content_type = response.headers.get("content-type", "")
            parsed = json.loads(raw.decode("utf-8")) if "json" in content_type else raw.decode("utf-8", errors="replace")
            return response.status, parsed, latency_ms
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        latency_ms = int((time.perf_counter() - started) * 1000)
        try:
            parsed = json.loads(raw) if raw.strip().startswith("{") else raw
        except Exception:
            parsed = raw
        return exc.code, parsed, latency_ms
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return 0, f"{type(exc).__name__}: {exc}", latency_ms


def check(rows: list[dict], name: str, status: int, expect, detail: str = "", latency_ms: int = 0) -> bool:
    exp = expect if isinstance(expect, list) else [expect]
    ok = status in exp
    rows.append({"name": name, "status": status, "expect": exp, "ok": ok, "detail": detail, "latency_ms": latency_ms})
    return ok


def login(username: str, password: str) -> tuple[str, dict]:
    status, body, latency = req("POST", "/api/auth/login", {"username": username, "password": password})
    result = {"status": status, "latency_ms": latency, "body": body}
    token = body.get("token") if status == 200 and isinstance(body, dict) else ""
    return token or "", result


def run_persona(name: str, username: str, password: str, role: str) -> dict:
    rows: list[dict] = []
    token, login_meta = login(username, password)
    check(rows, f"{name}: login", login_meta["status"], 200, f"user={username}", login_meta["latency_ms"])
    if not token:
        return {"role": role, "username": username, "pass": 0, "fail": len(rows), "latency_ms": 0, "checks": rows}

    status, body, latency = req("GET", "/api/auth/me", token=token)
    check(rows, f"{name}: /api/auth/me", status, 200, f"role={(body or {}).get('role')}", latency)
    status, body, latency = req("GET", "/version.json", token=token)
    check(rows, f"{name}: /version.json", status, 200, "", latency)
    status, body, latency = req("GET", "/api/dashboard/charts", token=token)
    charts = (body or {}).get("charts", []) if isinstance(body, dict) else []
    check(rows, f"{name}: /api/dashboard/charts", status, 200, f"charts={len(charts)}", latency)
    status, body, latency = req("GET", "/api/dashboard/snapshots", token=token)
    snaps = (body or {}).get("snapshots", {}) if isinstance(body, dict) else {}
    check(rows, f"{name}: /api/dashboard/snapshots", status, 200, f"snapshots={len(snaps)}", latency)
    status, body, latency = req("GET", "/api/tracker/issues?limit=5", token=token)
    check(rows, f"{name}: /api/tracker/issues", status, 200, f"issues={len((body or {}).get('issues', [])) if isinstance(body, dict) else 0}", latency)
    status, body, latency = req("GET", "/api/home/release-notes", token=token)
    check(rows, f"{name}: /api/home/release-notes", status, 200, f"recent={len((body or {}).get('recent', [])) if isinstance(body, dict) else 0}", latency)
    status, body, latency = req("GET", "/api/meetings/list", token=token)
    check(rows, f"{name}: /api/meetings/list", status, 200, f"meetings={len((body or {}).get('meetings', [])) if isinstance(body, dict) else 0}", latency)
    status, body, latency = req("GET", "/api/calendar/events", token=token)
    check(rows, f"{name}: /api/calendar/events", status, 200, f"events={len((body or {}).get('events', [])) if isinstance(body, dict) else 0}", latency)
    status, body, latency = req("GET", "/api/groups/list", token=token)
    check(rows, f"{name}: /api/groups/list", status, 200, f"groups={len((body or {}).get('groups', [])) if isinstance(body, dict) else 0}", latency)
    status, body, latency = req("GET", "/api/informs/config", token=token)
    check(rows, f"{name}: /api/informs/config", status, 200, f"modules={len((body or {}).get('modules', [])) if isinstance(body, dict) else 0}", latency)
    status, body, latency = req("GET", "/api/filebrowser/roots", token=token)
    check(rows, f"{name}: /api/filebrowser/roots", status, 200, f"roots={len((body or {}).get('roots', [])) if isinstance(body, dict) else 0}", latency)
    status, body, latency = req("GET", "/api/splittable/products", token=token)
    products = (body or {}).get("products", []) if isinstance(body, dict) else []
    check(rows, f"{name}: /api/splittable/products", status, 200, f"products={len(products)}", latency)
    status, body, latency = req("GET", "/api/waferlayout/grid?product=PRODUCT_A0", token=token)
    check(rows, f"{name}: /api/waferlayout/grid", status, 200, f"has_chip_w={bool((body or {}).get('wafer_layout', {}).get('chip_w_mm'))}", latency)
    status, body, latency = req("GET", "/api/admin/users", token=token)
    check(rows, f"{name}: /api/admin/users", status, 200 if role == "admin" else [401, 403], "", latency)
    status, body, latency = req("GET", "/api/admin/qa/report", token=token)
    check(rows, f"{name}: /api/admin/qa/report", status, 200 if role == "admin" else [401, 403], "", latency)

    passed = sum(1 for row in rows if row["ok"])
    failed = len(rows) - passed
    avg_latency = int(sum(row["latency_ms"] for row in rows) / max(1, len(rows)))
    return {"role": role, "username": username, "pass": passed, "fail": failed, "latency_ms": avg_latency, "checks": rows}


def run_edge_cases(admin_token: str, user_token: str) -> list[dict]:
    rows: list[dict] = []
    scenarios = [
        ("missing product", "GET", "/api/waferlayout/grid?product=", None, admin_token, [400]),
        ("empty splittable product", "GET", "/api/splittable/view?product=", None, admin_token, [400, 404]),
        ("tracker pagination high", "GET", "/api/tracker/issues?limit=5&offset=99999", None, admin_token, [200]),
        ("unauthorized admin users", "GET", "/api/admin/users", None, user_token, [401, 403]),
        ("anonymous admin users", "GET", "/api/admin/users", None, "", [401, 403]),
        ("bad json login-like", "POST", "/api/auth/login", "not-json", "", [400]),
        ("special chars notify", "POST", "/api/admin/send-message", {"to_user": "qa_user", "message": "한글 <> ' \" ; -- QA"}, admin_token, [200, 404]),
        ("long inquiry", "POST", "/api/admin/send-inquiry", {"username": "qa_user", "message": "x" * 10000}, user_token, [200, 400]),
        ("edge shots empty ids", "GET", "/api/waferlayout/edge-shots?product=PRODUCT_A0&teg_ids=", None, admin_token, [200]),
        ("calendar wide range", "GET", "/api/calendar/events?start=1900-01-01&end=2100-12-31", None, admin_token, [200]),
    ]
    for name, method, path, body, token, expect in scenarios:
        status, payload, latency = req(method, path, body=body, token=token) if body != "not-json" else raw_bad_json(path)
        check(rows, f"edge: {name}", status, expect, str(payload)[:120], latency)
    return rows


def raw_bad_json(path: str):
    headers = {"Content-Type": "application/json"}
    request = urllib.request.Request(BASE + path, data=b"{bad", headers=headers, method="POST")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return response.status, raw, int((time.perf_counter() - started) * 1000)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return exc.code, raw, int((time.perf_counter() - started) * 1000)
    except Exception as exc:
        return 0, str(exc), int((time.perf_counter() - started) * 1000)


def run_chart_checks(admin_token: str) -> list[dict]:
    rows: list[dict] = []
    status, body, latency = req("GET", "/api/dashboard/charts", token=admin_token)
    charts = (body or {}).get("charts", []) if isinstance(body, dict) else []
    check(rows, "charts: list", status, 200, f"charts={len(charts)}", latency)
    for chart in charts[:8]:
        cid = chart.get("id")
        if not cid:
            continue
        status, payload, latency = req("GET", f"/api/dashboard/data?chart_id={cid}", token=admin_token)
        ok = status == 200 and isinstance(payload, dict)
        detail = ""
        if ok:
            labels = payload.get("labels")
            series = payload.get("series")
            if not isinstance(labels, list) or not isinstance(series, list):
                ok = False
                detail = "labels/series missing"
            elif series:
                sample = series[0] if isinstance(series[0], dict) else {}
                if "name" not in sample or "data" not in sample:
                    ok = False
                    detail = "series item missing name/data"
            if not detail:
                detail = f"labels={len(labels or [])} series={len(series or [])}"
        rows.append({"name": f"charts: {cid}", "status": status, "expect": [200], "ok": ok, "detail": detail, "latency_ms": latency})
    return rows


def build_ux_scores() -> dict:
    files = {
        "home": ROOT / "frontend" / "src" / "pages" / "My_Home.jsx",
        "admin": ROOT / "frontend" / "src" / "pages" / "My_Admin.jsx",
        "waferlayout": ROOT / "frontend" / "src" / "pages" / "My_WaferLayout.jsx",
        "tracker": ROOT / "frontend" / "src" / "pages" / "My_Tracker.jsx",
    }
    pages = []
    for page, path in files.items():
        text = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
        score = 5
        notes = []
        if "PageHeader" not in text:
            score -= 1
            notes.append("header pattern missing")
        if text.count("fontSize") < 5:
            score -= 1
            notes.append("few typography cues")
        if "loading" not in text.lower():
            score -= 1
            notes.append("loading state weak")
        pages.append({"page": page, "score": max(1, score), "notes": notes[:3]})
    return {"pages": pages}


def create_qa_issue(run: dict) -> str | None:
    return None


def collect_failures(run: dict) -> list[dict]:
    issues = []
    for persona, detail in (run.get("personas") or {}).items():
        for row in detail.get("checks", []):
            if not row.get("ok"):
                issues.append({"severity": "high" if "admin" in row["name"] else "medium", "area": persona, "desc": row["name"], "repro": row["detail"]})
    for section in ("edge_cases", "charts"):
        for row in run.get(section, []):
            if not row.get("ok"):
                issues.append({"severity": "medium", "area": section, "desc": row["name"], "repro": row["detail"]})
    grouped = []
    seen = set()
    for item in issues:
        key = (item["area"], item["desc"])
        if key in seen:
            continue
        seen.add(key)
        grouped.append(item)
    return grouped


def main() -> int:
    started = time.perf_counter()
    creds = ensure_qa_users()
    admin = run_persona("admin", "qa_admin", creds["qa_admin"], "admin")
    user = run_persona("user", "qa_user", creds["qa_user"], "user")
    admin_token, _ = login("qa_admin", creds["qa_admin"])
    user_token, _ = login("qa_user", creds["qa_user"])
    edge_cases = run_edge_cases(admin_token, user_token)
    charts = run_chart_checks(admin_token)
    ux_scores = build_ux_scores()
    run = {
        "run_at": now_iso(),
        "base": BASE,
        "duration_sec": round(time.perf_counter() - started, 2),
        "personas": {"admin": admin, "user": user},
        "edge_cases": edge_cases,
        "charts": charts,
        "ux_scores": ux_scores,
    }
    run["issues"] = collect_failures(run)
    qa_issue_file = create_qa_issue(run)
    if qa_issue_file:
        run["qa_issue_file"] = qa_issue_file
    report = load_json(REPORT_FILE, {"runs": []})
    runs = report.get("runs") if isinstance(report, dict) else []
    if not isinstance(runs, list):
        runs = []
    runs.insert(0, run)
    report = {"generated_at": now_iso(), "runs": runs[:10]}
    save_json(REPORT_FILE, report)
    print(json.dumps({"ok": True, "report_file": str(REPORT_FILE), "issues": len(run["issues"]), "qa_issue_file": qa_issue_file or ""}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
