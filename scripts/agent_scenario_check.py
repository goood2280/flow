"""HTTP scenario smoke for the Flow-i Agent tab and adjacent regressions.

Runs against an already running server.

Environment:
  FLOW_BASE=http://localhost:8080
  FLOW_USER=hol
  FLOW_PW=hol12345!
"""
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
from typing import Any

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

BASE = os.environ.get("FLOW_BASE", "http://localhost:8080").rstrip("/")
USER = os.environ.get("FLOW_USER", "hol")
PW = os.environ.get("FLOW_PW", "hol12345!")

TOKEN = ""
PASS = 0
FAIL = 0
CREATED_INFORM_IDS: list[str] = []
CREATED_WIKI_DOC_IDS: list[str] = []


def _req(method: str, path: str, body: Any = None, token: str | None = None, timeout: int = 15) -> tuple[int, Any]:
    url = BASE + path
    data = None
    headers = {"Content-Type": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    if token:
        headers["X-Session-Token"] = token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            content_type = resp.headers.get("content-type", "")
            if "json" in content_type:
                return resp.status, json.loads(raw.decode("utf-8"))
            return resp.status, raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        try:
            parsed = json.loads(raw) if raw.strip().startswith("{") else raw
        except Exception:
            parsed = raw
        return exc.code, parsed
    except Exception as exc:
        return 0, f"{type(exc).__name__}: {exc}"


def _path(obj: Any, dotted: str) -> Any:
    cur = obj
    for part in str(dotted or "").split("."):
        if part == "":
            continue
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except Exception:
                return None
        else:
            return None
    return cur


def _fmt(value: Any, ctx: dict[str, Any]) -> Any:
    if isinstance(value, str):
        try:
            return value.format(**ctx)
        except Exception:
            return value
    if isinstance(value, list):
        return [_fmt(v, ctx) for v in value]
    if isinstance(value, dict):
        return {k: _fmt(v, ctx) for k, v in value.items()}
    return value


def _json_len(value: Any) -> int:
    if isinstance(value, (list, tuple, dict, str)):
        return len(value)
    return 0


def _assertions(body: Any, assertions: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for rule in assertions or []:
        target = _path(body, rule.get("path", ""))
        label = rule.get("path", "<body>")
        if rule.get("type") == "dict" and not isinstance(target, dict):
            errors.append(f"{label} is not dict")
        if rule.get("type") == "list" and not isinstance(target, list):
            errors.append(f"{label} is not list")
        if rule.get("truthy") and not target:
            errors.append(f"{label} is empty")
        if "equals" in rule and target != rule["equals"]:
            errors.append(f"{label}={target!r}, expected {rule['equals']!r}")
        if "min_len" in rule and _json_len(target) < int(rule["min_len"]):
            errors.append(f"{label} length {_json_len(target)} < {rule['min_len']}")
        if "contains_key" in rule and not (isinstance(target, dict) and rule["contains_key"] in target):
            errors.append(f"{label} missing key {rule['contains_key']}")
    return errors


def _pick_first_product(rows: Any) -> str:
    if not isinstance(rows, list):
        return "PRODA"
    for row in rows:
        if isinstance(row, dict):
            name = row.get("name") or row.get("product") or row.get("value")
        else:
            name = row
        if str(name or "").strip():
            return str(name).strip()
    return "PRODA"


def _preload_context() -> dict[str, Any]:
    stamp = "agent_scenario_" + uuid.uuid4().hex[:10]
    ctx: dict[str, Any] = {
        "stamp": stamp,
        "wiki_doc_id": stamp,
        "wiki_source_id": "src_" + stamp,
        "inform_id": "",
        "chart_id": "",
        "split_product": "PRODA",
        "split_product_clean": "PRODA",
        "dashboard_product": "PRODA",
        "file_root": "",
        "inform_module": "GATE",
    }
    status, body = _req("GET", "/api/splittable/products", token=TOKEN)
    if status == 200 and isinstance(body, dict):
        ctx["split_product"] = _pick_first_product(body.get("products"))
        ctx["split_product_clean"] = ctx["split_product"].replace("ML_TABLE_", "")
    status, body = _req("GET", "/api/dashboard/products", token=TOKEN)
    if status == 200 and isinstance(body, dict):
        ctx["dashboard_product"] = _pick_first_product(body.get("products"))
    status, body = _req("GET", "/api/filebrowser/roots", token=TOKEN)
    if status == 200 and isinstance(body, dict) and body.get("roots"):
        ctx["file_root"] = str((body["roots"][0] or {}).get("name") or "")
    status, body = _req("GET", "/api/dashboard/charts", token=TOKEN)
    if status == 200 and isinstance(body, dict):
        charts = body.get("charts") or []
        if charts:
            ctx["chart_id"] = str((charts[0] or {}).get("id") or "")
    status, body = _req("GET", "/api/informs/config", token=TOKEN)
    if status == 200 and isinstance(body, dict) and body.get("modules"):
        ctx["inform_module"] = str(body["modules"][0] or "GATE")
    return ctx


def _save_context(name: str, body: Any, ctx: dict[str, Any]) -> None:
    if name == "inform_id":
        value = _path(body, "inform.id") or _path(body, "id")
        if value:
            ctx["inform_id"] = str(value)
            CREATED_INFORM_IDS.append(str(value))
    elif name == "wiki_doc_id":
        value = _path(body, "doc.doc_id") or _path(body, "page.doc_id") or _path(body, "preview.doc_id")
        if value:
            ctx["wiki_doc_id"] = str(value)
            CREATED_WIKI_DOC_IDS.append(str(value))
    elif name == "wiki_source_id":
        value = _path(body, "source.source_id")
        if value:
            ctx["wiki_source_id"] = str(value)


def scenario_list() -> list[dict[str, Any]]:
    wiki_body = (
        "PRODA SORT 공정에서 KNOB 값은 wafer split 의 기준으로 사용한다.\n"
        "한글 지식 입력 검증용 문서이며, 실제 원본 DB는 수정하지 않는다.\n"
        "태그와 요약은 한국어로 표시되어야 한다."
    )
    return [
        {"name": "auth/me", "method": "GET", "path": "/api/auth/me", "expect_status": 200, "assertions": [{"path": "username", "truthy": True}]},
        {"name": "bad login 401", "method": "POST", "path": "/api/auth/login", "token": "none", "body": {"username": "noexist", "password": "bad"}, "expect_status": 401},
        {"name": "admin endpoint no token", "method": "GET", "path": "/api/admin/users", "token": "none", "expect_status": 401},
        {"name": "admin endpoint invalid token", "method": "GET", "path": "/api/admin/users", "token": "invalid", "expect_status": 401},
        {"name": "admin users", "method": "GET", "path": "/api/admin/users", "expect_status": 200, "assertions": [{"path": "users", "type": "list"}]},
        {"name": "version", "method": "GET", "path": "/version.json", "token": "none", "expect_status": 200},
        {"name": "system stats", "method": "GET", "path": "/api/system/stats", "expect_status": 200, "assertions": [{"path": "current", "type": "dict"}]},

        {"name": "agent persona card", "method": "GET", "path": "/api/llm/flowi/persona-card", "expect_status": 200, "assertions": [{"path": "persona", "type": "dict"}, {"path": "do_list", "type": "list"}]},
        {"name": "agent dry-run fab", "method": "POST", "path": "/api/llm/flowi/orchestrator/preview", "body": {"prompts": ["PRODA A1000 #6 현재 fab lot id가 뭐야?"], "context": {"ask_llm_to_guess_missing": True}}, "expect_status": 200, "assertions": [{"path": "rows", "min_len": 1}]},
        {"name": "agent missing slot dry-run", "method": "POST", "path": "/api/llm/flowi/orchestrator/preview", "body": {"prompts": ["인폼 남겨줘"], "context": {"ask_llm_to_guess_missing": True}}, "expect_status": 200, "timeout": 45, "assertions": [{"path": "rows", "min_len": 1}]},
        {"name": "agent clarification choices", "method": "POST", "path": "/api/llm/flowi/orchestrator/preview", "body": {"prompts": ["A1004 인폼전체 작성해줘"], "context": {"ask_llm_to_guess_missing": True}}, "expect_status": 200, "assertions": [{"path": "rows", "min_len": 1}]},
        {"name": "agent prompt history", "method": "GET", "path": "/api/agent/prompt-history?limit=20", "expect_status": 200, "assertions": [{"path": "rows", "type": "list"}]},
        {"name": "agent prompt review", "method": "POST", "path": "/api/agent/prompt-review", "body": {"prompt": "인폼 남겨줘"}, "expect_status": 200, "timeout": 45, "assertions": [{"path": "review.improved_prompt", "truthy": True}]},
        {"name": "agent prompt review fallback shape", "method": "POST", "path": "/api/agent/prompt-review", "body": {"prompt": "A1000 #6 어디야"}, "expect_status": 200, "timeout": 45, "assertions": [{"path": "llm", "type": "dict"}, {"path": "review.ambiguous_questions", "type": "list"}]},
        {"name": "agent blocked write guard", "method": "POST", "path": "/api/llm/flowi/agent/chat", "body": {"prompt": "raw DB 파일을 직접 삭제해줘", "source_ai": "scenario", "max_rows": 3}, "expect_status": 200, "assertions": [{"path": "trace", "type": "dict"}]},
        {"name": "agent confirmation-required route", "method": "POST", "path": "/api/llm/flowi/agent/chat", "body": {"prompt": "Files root 파일 내용을 바꾸는 초안을 만들어줘", "source_ai": "scenario", "max_rows": 3}, "expect_status": 200, "assertions": [{"path": "trace", "type": "dict"}]},
        {"name": "agent SplitTable route", "method": "POST", "path": "/api/llm/flowi/agent/chat", "body": {"prompt": "{split_product_clean} A1002 24.0 SORT KNOB 구성이 어떻게돼?", "source_ai": "scenario", "max_rows": 3}, "expect_status": 200, "timeout": 45, "assertions": [{"path": "trace.steps", "type": "list"}]},
        {"name": "agent GPT OSS deterministic fallback", "method": "POST", "path": "/api/llm/flowi/agent/chat", "body": {"prompt": "{split_product_clean} A1002 KNOB TABLE 보여줘", "source_ai": "scenario", "max_rows": 3}, "expect_status": 200, "assertions": [{"path": "llm.used", "equals": False}, {"path": "trace.interpretation", "type": "dict"}]},
        {"name": "agent FileBrowser route", "method": "POST", "path": "/api/llm/flowi/agent/chat", "body": {"prompt": "{dashboard_product} FAB 최근 3행 보여줘", "source_ai": "scenario", "max_rows": 3}, "expect_status": 200, "assertions": [{"path": "trace.api_calls", "type": "list"}]},
        {"name": "agent Inform draft", "method": "POST", "path": "/api/llm/flowi/agent/chat", "body": {"prompt": "A1004 인폼전체 작성해줘", "source_ai": "scenario", "max_rows": 3}, "expect_status": 200, "assertions": [{"path": "trace", "type": "dict"}]},
        {"name": "agent trace shape", "method": "POST", "path": "/api/llm/flowi/agent/chat", "body": {"prompt": "PRODA A1000 #6 현재 fab lot id가 뭐야?", "source_ai": "scenario", "max_rows": 3}, "expect_status": 200, "assertions": [{"path": "trace.activation", "type": "dict"}, {"path": "trace.call_graph.nodes", "type": "list"}]},

        {"name": "wiki source register", "method": "POST", "path": "/api/agent/wiki/sources", "body": {"source_id": "{wiki_source_id}", "source_type": "markdown", "title": "[scenario] 한글 source {stamp}", "content": wiki_body, "tags": ["시나리오", "한글"]}, "expect_status": 200, "save": "wiki_source_id", "assertions": [{"path": "source.source_id", "truthy": True}]},
        {"name": "wiki source list", "method": "GET", "path": "/api/agent/wiki/sources?q={stamp}&limit=10", "expect_status": 200, "assertions": [{"path": "sources", "type": "list"}]},
        {"name": "wiki ingest preview", "method": "POST", "path": "/api/agent/wiki/ingest/preview", "body": {"source_ids": ["{wiki_source_id}"], "doc_id": "{wiki_doc_id}", "title": "[scenario] 한글 지식 {stamp}", "tags": ["시나리오", "한글"]}, "expect_status": 200, "assertions": [{"path": "preview.doc_id", "truthy": True}]},
        {"name": "wiki ingest commit", "method": "POST", "path": "/api/agent/wiki/ingest/commit", "body": {"source_ids": ["{wiki_source_id}"], "doc_id": "{wiki_doc_id}", "title": "[scenario] 한글 지식 {stamp}", "summary": "한글 요약 표시 검증", "body": wiki_body, "tags": ["시나리오", "한글"]}, "expect_status": 200, "save": "wiki_doc_id", "assertions": [{"path": "doc.doc_id", "truthy": True}]},
        {"name": "wiki page list", "method": "GET", "path": "/api/agent/wiki/pages?q={stamp}&limit=20", "expect_status": 200, "assertions": [{"path": "pages", "type": "list"}]},
        {"name": "wiki page detail", "method": "GET", "path": "/api/agent/wiki/page?doc_id={wiki_doc_id}", "expect_status": 200, "assertions": [{"path": "page.title", "truthy": True}, {"path": "page.body", "truthy": True}]},
        {"name": "wiki search", "method": "GET", "path": "/api/agent/wiki/search?q={stamp}&limit=20", "expect_status": 200, "assertions": [{"path": "results", "type": "list"}]},
        {"name": "wiki graph", "method": "GET", "path": "/api/knowledge/wiki/graph", "expect_status": 200, "assertions": [{"path": "nodes", "type": "list"}]},
        {"name": "wiki lint permission", "method": "POST", "path": "/api/agent/wiki/lint", "body": {}, "expect_status": 200, "assertions": [{"path": "ok", "equals": True}]},
        {"name": "wiki korean tag summary", "method": "GET", "path": "/api/agent/wiki/page?doc_id={wiki_doc_id}", "expect_status": 200, "assertions": [{"path": "page.tags", "type": "list"}, {"path": "page.summary", "truthy": True}]},
        {"name": "wiki delete", "method": "POST", "path": "/api/agent/wiki/page/delete", "body": {"doc_id": "{wiki_doc_id}"}, "expect_status": 200, "assertions": [{"path": "deleted", "equals": True}]},

        {"name": "dashboard charts", "method": "GET", "path": "/api/dashboard/charts", "expect_status": 200, "assertions": [{"path": "charts", "type": "list"}]},
        {"name": "dashboard summary", "method": "GET", "path": "/api/dashboard/summary?product={dashboard_product}", "expect_status": 200},
        {"name": "dashboard products", "method": "GET", "path": "/api/dashboard/products", "expect_status": 200, "assertions": [{"path": "products", "type": "list"}]},
        {"name": "dashboard items", "method": "GET", "path": "/api/dashboard/items?group=ET&product={dashboard_product}&limit=20", "expect_status": 200, "assertions": [{"path": "items", "type": "list"}]},
        {"name": "dashboard columns empty guard", "method": "GET", "path": "/api/dashboard/columns?source_type=base_file&file=__missing__.csv", "expect_status": 404},
        {"name": "dashboard preview fallback", "method": "GET", "path": "/api/dashboard/preview?source_type=missing&limit=3", "expect_status": 200, "assertions": [{"path": "rows", "type": "list"}]},
        {"name": "dashboard multi-db-chart", "method": "POST", "path": "/api/dashboard/multi-db-chart", "body": {"primary_source": "ET", "x_item": "CD", "y_item": "CD", "chart_type": "scatter", "product": "{dashboard_product}", "root_lot_ids": []}, "expect_status": 200, "assertions": [{"path": "data", "type": "list"}]},
        {"name": "dashboard chart-refine missing", "method": "POST", "path": "/api/dashboard/chart-refine", "body": {"chart_session_id": "missing-session", "action": "font_size", "value": 12}, "expect_status": 404},
        {"name": "dashboard chart defaults", "method": "GET", "path": "/api/dashboard/chart-defaults", "expect_status": 200, "assertions": [{"path": "defaults", "type": "dict"}]},
        {"name": "dashboard fab progress", "method": "GET", "path": "/api/dashboard/fab-progress?product={dashboard_product}&limit=3", "expect_status": 200},
        {"name": "dashboard trend alerts", "method": "GET", "path": "/api/dashboard/trend-alerts?limit=3", "expect_status": 200, "assertions": [{"path": "alerts", "type": "list"}]},
        {"name": "dashboard stuck alerts", "method": "GET", "path": "/api/dashboard/stuck-lots?product={dashboard_product}&limit=3", "expect_status": 200, "assertions": [{"path": "lots", "type": "list"}]},

        {"name": "inform config", "method": "GET", "path": "/api/informs/config", "expect_status": 200, "assertions": [{"path": "modules", "type": "list"}]},
        {"name": "inform recipients", "method": "GET", "path": "/api/informs/recipients", "expect_status": 200, "assertions": [{"path": "recipients", "type": "list"}]},
        {"name": "inform create", "method": "POST", "path": "/api/informs", "body": {"product": "{split_product_clean}", "module": "{inform_module}", "reason": "시나리오", "text": "[scenario] {stamp}", "lot_id": "A1000", "wafer_id": "1"}, "expect_status": 200, "save": "inform_id", "assertions": [{"path": "inform.id", "truthy": True}]},
        {"name": "inform mail preview", "method": "GET", "path": "/api/informs/{inform_id}/mail-preview", "expect_status": 200},
        {"name": "inform dashboard data", "method": "GET", "path": "/api/informs/dashboard-data", "expect_status": 200},
        {"name": "inform delete", "method": "POST", "path": "/api/informs/delete?id={inform_id}", "expect_status": 200},
        {"name": "meeting list", "method": "GET", "path": "/api/meetings/list", "expect_status": 200, "assertions": [{"path": "meetings", "type": "list"}]},
        {"name": "meeting minutes route", "method": "POST", "path": "/api/meetings/minutes/append", "body": {"meeting_id": "missing", "session_id": "missing", "text": "x"}, "expect_status": [400, 404]},
        {"name": "calendar events", "method": "GET", "path": "/api/calendar/events", "expect_status": 200, "assertions": [{"path": "events", "type": "list"}]},
        {"name": "tracker issues", "method": "GET", "path": "/api/tracker/issues?limit=5", "expect_status": 200, "assertions": [{"path": "issues", "type": "list"}]},
        {"name": "splittable products", "method": "GET", "path": "/api/splittable/products", "expect_status": 200, "assertions": [{"path": "products", "type": "list"}]},
        {"name": "splittable view", "method": "GET", "path": "/api/splittable/view?product={split_product}&root_lot_id=A1000&wafer_ids=1&prefix=KNOB&view_mode=all&history_mode=all", "expect_status": 200},
        {"name": "splittable history", "method": "GET", "path": "/api/splittable/history?product={split_product}&limit=5", "expect_status": 200, "assertions": [{"path": "history", "type": "list"}]},
        {"name": "filebrowser roots", "method": "GET", "path": "/api/filebrowser/roots", "expect_status": 200, "assertions": [{"path": "roots", "type": "list"}]},
        {"name": "filebrowser products", "method": "GET", "path": "/api/filebrowser/products?root={file_root}", "expect_status": 200, "assertions": [{"path": "products", "type": "list"}]},
        {"name": "filebrowser sql draft", "method": "POST", "path": "/api/filebrowser/sql/llm/draft", "body": {"natural_language": "lot_id가 A1000인 행", "columns": ["lot_id", "wafer_id", "step_id"], "sample_rows": [{"lot_id": "A1000", "wafer_id": "1", "step_id": "AA200000"}]}, "expect_status": 200, "timeout": 45, "assertions": [{"path": "ok", "equals": True}]},
        {"name": "groups list", "method": "GET", "path": "/api/groups/list", "expect_status": 200, "assertions": [{"path": "groups", "type": "list"}]},
        {"name": "messages notices", "method": "GET", "path": "/api/messages/notices", "expect_status": 200},
        {"name": "runtime roots", "method": "GET", "path": "/runtime-roots.json", "token": "none", "expect_status": 200},
    ]


def _token_for(mode: str | None) -> str | None:
    if mode == "none":
        return None
    if mode == "invalid":
        return "invalid-token-agent-scenario"
    return TOKEN


def run_scenario(sc: dict[str, Any], ctx: dict[str, Any]) -> None:
    global PASS, FAIL
    method = sc["method"]
    path = _fmt(sc["path"], ctx)
    body = _fmt(sc.get("body"), ctx) if "body" in sc else None
    expect_raw = sc.get("expect_status", 200)
    expected = expect_raw if isinstance(expect_raw, list) else [expect_raw]
    status, response = _req(method, path, body=body, token=_token_for(sc.get("token")), timeout=int(sc.get("timeout", 20)))
    errors = []
    if status not in expected:
        errors.append(f"status {status}, expected {expected}; body={str(response)[:220]}")
    if status in expected:
        errors.extend(_assertions(response, sc.get("assertions") or []))
    if not errors and sc.get("save"):
        _save_context(sc["save"], response, ctx)
    if errors:
        FAIL += 1
        print(f"FAIL {sc['name']} {method} {path}")
        for err in errors:
            print(f"  - {err}")
    else:
        PASS += 1
        print(f"PASS {sc['name']} [{status}]")


def cleanup() -> None:
    for inform_id in list(dict.fromkeys(CREATED_INFORM_IDS)):
        _req("POST", f"/api/informs/delete?id={urllib.parse.quote(inform_id)}", token=TOKEN, timeout=10)
    for doc_id in list(dict.fromkeys(CREATED_WIKI_DOC_IDS)):
        _req("POST", "/api/agent/wiki/page/delete", {"doc_id": doc_id}, token=TOKEN, timeout=10)


def main() -> int:
    global TOKEN
    print(f"Flow Agent scenario check: {BASE} user={USER}")
    status, body = _req("POST", "/api/auth/login", {"username": USER, "password": PW}, timeout=12)
    if status != 200 or not isinstance(body, dict) or not body.get("token"):
        print(f"FAIL login [{status}] {str(body)[:240]}")
        return 1
    TOKEN = body["token"]
    ctx = _preload_context()
    start = time.time()
    try:
        for sc in scenario_list():
            run_scenario(sc, ctx)
    finally:
        cleanup()
    total = PASS + FAIL
    elapsed = time.time() - start
    print("=" * 72)
    print(f"AGENT SCENARIO RESULT: {PASS}/{total} PASS, {FAIL} FAIL, elapsed={elapsed:.1f}s")
    if total < 55:
        print(f"FAIL scenario count {total} < 55")
        return 1
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
