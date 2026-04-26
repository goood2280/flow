from __future__ import annotations

import io
import sys

from fastapi.testclient import TestClient

sys.path.append("backend")
from app import app  # noqa: E402

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass


PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    print(("✅" if ok else "❌"), name, detail)
    if ok:
        PASS += 1
    else:
        FAIL += 1


def main():
    client = TestClient(app)

    r = client.get("/version.json")
    check("GET /version.json", r.status_code == 200, str(r.status_code))

    r = client.post("/api/auth/login", json={"username": "hol", "password": "hol12345!"})
    check("POST /api/auth/login", r.status_code == 200, str(r.status_code))
    if r.status_code != 200:
        raise SystemExit(1)
    token = r.json()["token"]
    headers = {"X-Session-Token": token}

    r = client.get("/api/splittable/products", headers=headers)
    check("GET /api/splittable/products", r.status_code == 200, str(r.status_code))
    products = [(p.get("name") if isinstance(p, dict) else p) for p in r.json().get("products", [])]
    first = products[0] if products else ""

    if first:
        r = client.get(f"/api/splittable/operational-history?product={first}&root_lot_id=A0007", headers=headers)
        check("GET /api/splittable/operational-history", r.status_code == 200, f"{r.status_code} items={len(r.json().get('items', []))}")

    r = client.get("/api/tracker/issues?limit=5", headers=headers)
    check("GET /api/tracker/issues", r.status_code == 200, str(r.status_code))
    issues = r.json().get("issues", [])
    if issues:
        iid = issues[0]["id"]
        r = client.get(f"/api/tracker/issue?issue_id={iid}", headers=headers)
        check("GET /api/tracker/issue", r.status_code == 200, str(r.status_code))

    r = client.get("/api/ml/sources", headers=headers)
    check("GET /api/ml/sources", r.status_code == 200, str(r.status_code))
    sources = r.json().get("sources", [])
    if sources:
        src = sources[0]
        body = {
            "source_type": src.get("source_type", "flat"),
            "root": src.get("root", ""),
            "product": src.get("product", ""),
            "file": src.get("file", ""),
            "target_et": "",
        }
        r = client.post("/api/ml/inline_et_overview", json=body, headers=headers)
        check("POST /api/ml/inline_et_overview", r.status_code == 200, f"{r.status_code} target={r.json().get('target_et') if r.status_code == 200 else ''}")

    print(f"\nRESULT {PASS} PASS / {FAIL} FAIL")
    raise SystemExit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
