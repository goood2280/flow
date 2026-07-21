from __future__ import annotations

import io
import os
import sys

sys.path.append("backend")
from app import app  # noqa: E402
from routers.auth import read_users  # noqa: E402

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass


PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    print(("✅" if ok else "❌"), name, detail, flush=True)
    if ok:
        PASS += 1
    else:
        FAIL += 1


def main() -> int:
    route_paths = {getattr(route, "path", "") for route in app.routes}
    required = {
        "/version.json",
        "/api/auth/login",
        "/api/splittable/products",
        "/api/splittable/operational-history",
        "/api/tracker/issues",
        "/api/llm/flowi/chat",
        "/api/diagnosis/run",
    }
    for path in sorted(required):
        check(f"route {path}", path in route_paths)

    users = read_users()
    admin = next((u for u in users if u.get("username") == "hol"), None)
    check("seed admin present", bool(admin), "hol")
    if admin:
        check("seed admin approved", admin.get("status") == "approved", str(admin.get("status")))
        check("seed admin role", admin.get("role") == "admin", str(admin.get("role")))

    print(f"\nRESULT {PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
