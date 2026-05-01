#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTERS_DIR = ROOT / "backend" / "routers"
FRONTEND_DIR = ROOT / "frontend" / "src"
DOC_PATH = ROOT / "docs" / "permission_matrix.md"

ROUTER_FILES = [
    "admin.py",
    "agent.py",
    "calendar.py",
    "dbmap.py",
    "filebrowser.py",
    "groups.py",
    "informs.py",
    "llm.py",
    "messages.py",
    "monitor.py",
    "s3_ingest.py",
    "semiconductor.py",
    "tracker.py",
    "waferlayout.py",
    "splittable.py",
    "dashboard.py",
    "catalog.py",
    "ettime.py",
    "home.py",
    "ml.py",
    "reformatter.py",
    "session_api.py",
    "auth.py",
    "meetings.py",
    "mail_groups.py",
    "informs_extra.py",
]

HTTP_METHODS = {"get", "post", "put", "delete", "patch"}
ADMIN_PREFIX_PATTERNS = (
    "/api/admin/",
    "/api/agent/admin-tools/",
    "/api/llm/flowi/admin/",
)
USER_SELF_ADMIN_PREFIX = {
    "/api/admin/user-tabs",
    "/api/admin/send-inquiry",
    "/api/admin/my-notifications",
    "/api/admin/all-notifications",
    "/api/admin/mark-read",
    "/api/admin/dismiss",
    "/api/admin/dismiss-batch",
    "/api/admin/notify-rules",
    "/api/admin/mark-read-batch",
    "/api/admin/log",
    "/api/admin/logs",
    "/api/admin/settings",
    "/api/admin/my-page-admin",
}


def _name(node: ast.AST | None) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Call):
        return _name(node.func)
    return ""


def _literal(node: ast.AST | None) -> str:
    return str(node.value) if isinstance(node, ast.Constant) and node.value is not None else ""


def _call_text(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _prefixes(tree: ast.AST) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Call) or not _name(node.value.func).endswith("APIRouter"):
            continue
        prefix = ""
        for kw in node.value.keywords:
            if kw.arg == "prefix":
                prefix = _literal(kw.value)
        for target in node.targets:
            if isinstance(target, ast.Name):
                out[target.id] = prefix
    return out


def _gate_from_text(text: str, endpoint: str = "") -> str:
    gates: list[str] = []
    if "require_admin" in text:
        gates.append("require_admin")
    if "require_page_admin" in text:
        m = re.search(r"require_page_admin\([\"']([^\"']+)[\"']\)", text)
        gates.append(f"require_page_admin:{m.group(1)}" if m else "require_page_admin")
    if "is_page_admin" in text:
        gates.append("is_page_admin")
    if "_require_tablemap_admin" in text:
        gates.append("require_page_admin:tablemap")
    if "_require_agent_admin" in text:
        gates.append("require_admin")
    if "_require_dashboard_section" in text:
        gates.append("dashboard_section")
    if "_require_admin" in text:
        gates.append("require_admin")
    if "verify_owner" in text:
        gates.append("owner_or_admin")
    if "current_user" in text:
        gates.append("current_user")
    if not gates:
        gates.append("session_middleware")
    unique = []
    for gate in gates:
        if gate not in unique:
            unique.append(gate)
    if endpoint.startswith("/api/admin/") and endpoint in USER_SELF_ADMIN_PREFIX and "require_admin" not in unique:
        unique.append("legacy_self_service_exception")
    return ", ".join(unique)


def backend_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for name in ROUTER_FILES:
        path = ROUTERS_DIR / name
        if not path.exists():
            continue
        src = path.read_text("utf-8")
        tree = ast.parse(src)
        prefixes = _prefixes(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body_text = ast.get_source_segment(src, node) or ""
            sig_text = body_text.split(":", 1)[0]
            for deco in node.decorator_list:
                if not isinstance(deco, ast.Call):
                    continue
                func = _name(deco.func)
                parts = func.split(".")
                if len(parts) < 2 or parts[-1] not in HTTP_METHODS:
                    continue
                router_name = parts[-2]
                route_path = _literal(deco.args[0]) if deco.args else ""
                endpoint = (prefixes.get(router_name, "") + route_path) or route_path
                decorator_text = _call_text(deco)
                gate = _gate_from_text("\n".join([decorator_text, sig_text, body_text]), endpoint)
                rows.append(
                    {
                        "file": name,
                        "line": str(node.lineno),
                        "method": parts[-1].upper(),
                        "endpoint": endpoint,
                        "handler": node.name,
                        "backend_gate": gate,
                    }
                )
    return sorted(rows, key=lambda r: (r["endpoint"], r["method"], r["file"], int(r["line"])))


def _endpoint_regex(endpoint: str) -> re.Pattern[str]:
    escaped = re.escape(endpoint)
    escaped = re.sub(r"\\\{[^}]+\\\}", r"[^/?\"'`]+", escaped)
    return re.compile(escaped)


def frontend_callers(rows: list[dict[str, str]]) -> dict[str, list[str]]:
    patterns = [(row["endpoint"], _endpoint_regex(row["endpoint"])) for row in rows]
    callers: dict[str, list[str]] = defaultdict(list)
    if not FRONTEND_DIR.exists():
        return callers
    for fp in FRONTEND_DIR.rglob("*"):
        if fp.suffix not in {".js", ".jsx", ".ts", ".tsx"}:
            continue
        try:
            lines = fp.read_text("utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        rel = fp.relative_to(ROOT).as_posix()
        for idx, line in enumerate(lines, 1):
            if "/api/" not in line:
                continue
            for endpoint, pattern in patterns:
                if pattern.search(line):
                    callers[endpoint].append(f"{rel}:{idx}")
    return callers


def _risk(row: dict[str, str], callers: list[str]) -> str:
    endpoint = row["endpoint"]
    method = row["method"]
    gate = row["backend_gate"]
    write = method in {"POST", "PUT", "PATCH", "DELETE"}
    if endpoint.startswith(ADMIN_PREFIX_PATTERNS) and endpoint not in USER_SELF_ADMIN_PREFIX and "require_admin" not in gate:
        return "inconsistent"
    if write and (
        endpoint.endswith("/chart-defaults")
        or "/admin-tools/" in endpoint
        or endpoint in {"/api/llm/flowi/admin/update", "/api/llm/flowi/feedback/promote", "/api/llm/flowi/persona"}
    ) and "require_admin" not in gate:
        return "leak_be_open"
    if write and (
        endpoint.startswith("/api/dbmap/")
        or endpoint.startswith("/api/informs/modules/")
        or endpoint in {"/api/informs/config", "/api/informs/settings"}
        or endpoint.startswith("/api/splittable/rulebook")
        or endpoint in {"/api/splittable/source-config/save", "/api/splittable/prefixes/save", "/api/splittable/precision/save"}
    ) and not any(token in gate for token in ("require_admin", "require_page_admin", "is_page_admin")):
        return "leak_be_open"
    return "ok"


def render() -> str:
    rows = backend_rows()
    callers = frontend_callers(rows)
    lines = [
        "# Permission Matrix",
        "",
        "Generated from `backend/routers/*.py` and `frontend/src` API call sites.",
        "`admin_settings.json` is intentionally not embedded in this report.",
        "",
        "| endpoint | method | backend gate | FE caller(file:line) | FE gate | risk |",
        "|---|---:|---|---|---|---|",
    ]
    counts: Counter[str] = Counter()
    for row in rows:
        fe = ", ".join(callers.get(row["endpoint"], [])[:6]) or "-"
        if len(callers.get(row["endpoint"], [])) > 6:
            fe += f", +{len(callers[row['endpoint']]) - 6} more"
        fe_gate = "admin/page helper or inline role guard where rendered" if fe != "-" else "-"
        risk = _risk(row, callers.get(row["endpoint"], []))
        counts[risk] += 1
        lines.append(
            f"| `{row['endpoint']}` | `{row['method']}` | `{row['backend_gate']}` | {fe} | {fe_gate} | `{risk}` |"
        )
    lines.extend(
        [
            "",
            "## Risk Counts",
            "",
            f"- ok: {counts.get('ok', 0)}",
            f"- leak_be_open: {counts.get('leak_be_open', 0)}",
            f"- leak_fe_open: {counts.get('leak_fe_open', 0)}",
            f"- inconsistent: {counts.get('inconsistent', 0)}",
            "",
            "## Change Notes",
            "",
            "- `/api/dashboard/chart-defaults`, dashboard refresh and saved-chart mutations are backend admin-gated.",
            "- Informs module/config writes and SplitTable rule/config writes accept global admin or page-admin delegation.",
            "- `/api/informs/{id}/send-mail` now requires the inform author or global admin.",
            "- Home Flowi blocks regular users from admin-function prompts with `blocked=true` and `reject_reason`.",
            "- Legacy `/api/admin/*` self-service notification/settings routes remain owner/self guarded to avoid breaking normal user flows; admin management routes remain `require_admin`.",
            "",
            "## 갱신 절차",
            "",
            "1. 새 backend endpoint를 추가하면 이 표에 `endpoint`, `method`, `backend gate`, FE caller를 추가한다.",
            "2. admin 전용 write는 `require_admin`, 페이지 위임 write는 `require_page_admin(\"page_key\")` 또는 동일한 `is_page_admin` 검사를 붙인다.",
            "3. FE에서 admin/page-admin UI를 추가하면 `frontend/src/lib/permissions.js` 헬퍼를 우선 사용한다.",
            "4. CI 또는 로컬에서 `python3 scripts/check_permission_matrix.py`를 실행해 라우터와 표 누락을 확인한다.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="rewrite docs/permission_matrix.md")
    args = parser.parse_args()

    content = render()
    if args.write:
        DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
        DOC_PATH.write_text(content, encoding="utf-8")
        return 0

    if not DOC_PATH.exists():
        print("docs/permission_matrix.md is missing")
        return 1
    current = DOC_PATH.read_text("utf-8")
    missing = []
    for row in backend_rows():
        key = f"| `{row['endpoint']}` | `{row['method']}` |"
        if key not in current:
            missing.append(f"{row['method']} {row['endpoint']}")
    if missing:
        print("permission matrix is missing endpoints:")
        for item in missing[:50]:
            print(f"- {item}")
        if len(missing) > 50:
            print(f"- ... and {len(missing) - 50} more")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
