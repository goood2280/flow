"""smoke_test.py — flow 핵심 경로 smoke.

v8.8.32: 자동 테스트 0 의 첫 방어선. 실행 중인 uvicorn 서버(기본 localhost:8080) 에
HTTP 로 요청을 보내 로그인·인폼·스플릿테이블·회의·트래커·admin 기본 동작을 PASS/FAIL 로 검증.

실행:
  cd flow && python scripts/smoke_test.py
  또는:
  FLOW_BASE=http://localhost:8080 python scripts/smoke_test.py
  또는 CI:
  FLOW_BASE=... FLOW_USER=hol FLOW_PW=... python scripts/smoke_test.py

Exit code: 0 = 전체 PASS, 1 = 하나라도 FAIL.
외부 의존성 없음 (stdlib urllib 만 사용).
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

# Windows cp949 console 에서 이모지/한글 안전 출력.
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

BASE = os.environ.get("FLOW_BASE", "http://localhost:8080").rstrip("/")
USER = os.environ.get("FLOW_USER", "hol")
PW   = os.environ.get("FLOW_PW",   "hol12345!")

PASS = 0
FAIL = 0
TOKEN = ""


def _req(method: str, path: str, body=None, token=None, timeout=10):
    url = BASE + path
    data = None
    headers = {"Content-Type": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    if token:
        headers["X-Session-Token"] = token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            ct = r.headers.get("content-type", "")
            if "json" in ct:
                return r.status, json.loads(raw.decode("utf-8"))
            return r.status, raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        try:
            body_s = e.read().decode("utf-8", errors="replace")
        except Exception:
            body_s = ""
        try:
            parsed = json.loads(body_s) if body_s.strip().startswith("{") else body_s
        except Exception:
            parsed = body_s
        return e.code, parsed
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def check(name: str, status: int, expect_status: int | list[int], detail: str = ""):
    global PASS, FAIL
    exp = expect_status if isinstance(expect_status, list) else [expect_status]
    ok = status in exp
    marker = "✅" if ok else "❌"
    line = f"{marker} {name}  [{status}]  {detail}".rstrip()
    print(line)
    if ok:
        PASS += 1
    else:
        FAIL += 1
    return ok


def section(title: str):
    print(f"\n── {title} ──")


# ── 1. 서버 기본 ────────────────────────────────────────────────
section("1. 서버 헬스")
status, body = _req("GET", "/version.json")
version = body.get("version", "?") if isinstance(body, dict) else "?"
check("서버 기동 + /version.json", status, 200, f"v{version}")

status, body = _req("POST", "/api/auth/login", {"username": USER, "password": PW})
if status == 200 and isinstance(body, dict) and body.get("token"):
    TOKEN = body["token"]
    check("로그인 성공", status, 200, f"user={body.get('username')} role={body.get('role')}")
else:
    check("로그인 실패", status, 200, str(body)[:120])
    print("\n❌ 로그인 실패 — 이후 테스트 스킵.")
    sys.exit(1)

status, body = _req("GET", "/api/auth/me", token=TOKEN)
check("/api/auth/me (v8.8.27 name 필드)", status, 200,
      f"name={(body or {}).get('name','?')}")

# ── 2. Admin ────────────────────────────────────────────────────
section("2. Admin")
status, body = _req("GET", "/api/admin/users", token=TOKEN)
ucount = len((body or {}).get("users", [])) if isinstance(body, dict) else 0
check("GET /api/admin/users", status, 200, f"users={ucount}")

status, _ = _req("GET", "/api/admin/settings", token=TOKEN)
check("GET /api/admin/settings", status, 200)

status, body = _req("GET", "/api/system/stats", token=TOKEN)
cur = (body or {}).get("current", {}) if isinstance(body, dict) else {}
check("GET /api/system/stats (psutil)", status, 200,
      f"cpu={cur.get('cpu_percent','?')}% mem={cur.get('memory_percent','?')}%")

# ── 3. FileBrowser ──────────────────────────────────────────────
section("3. FileBrowser")
status, body = _req("GET", "/api/filebrowser/roots", token=TOKEN)
roots = (body or {}).get("roots", []) if isinstance(body, dict) else []
check("GET /api/filebrowser/roots", status, 200, f"roots={[r.get('name') for r in roots][:4]}")

if roots:
    first_root = roots[0].get("name", "")
    status, body = _req("GET", f"/api/filebrowser/products?root={urllib.parse.quote(first_root)}", token=TOKEN)
    prods = (body or {}).get("products", []) if isinstance(body, dict) else []
    check(f"GET /products?root={first_root}", status, 200, f"products={[p.get('name') for p in prods]}")

# ── 4. SplitTable ───────────────────────────────────────────────
section("4. SplitTable")
status, body = _req("GET", "/api/splittable/products", token=TOKEN)
raw_prods = (body or {}).get("products", []) if isinstance(body, dict) else []
# 응답이 {name, file, ...} dict 목록일 수도 있고 순수 문자열일 수도 있음 — 둘 다 대응.
sprods = [(p.get("name") if isinstance(p, dict) else p) for p in raw_prods if p]
check("GET /api/splittable/products", status, 200, f"products={sprods[:4]}")

if sprods:
    first = sprods[0]
    status, body = _req("GET", f"/api/splittable/override-debug?product={urllib.parse.quote(first)}", token=TOKEN)
    meta = (body or {}).get("meta", {}) if isinstance(body, dict) else {}
    check(f"GET /override-debug?product={first}", status, 200,
          f"enabled={meta.get('enabled')} row_count={meta.get('row_count')}")

    status, body = _req("GET", f"/api/splittable/long-items?source=fab&product={urllib.parse.quote(first.replace('ML_TABLE_',''))}", token=TOKEN)
    items = (body or {}).get("items", []) if isinstance(body, dict) else []
    check(f"GET /long-items?source=fab (long format primary)", status, 200, f"items={items}")
    status, body = _req("GET", f"/api/splittable/operational-history?product={urllib.parse.quote(first)}&root_lot_id=A0007", token=TOKEN)
    check(f"GET /operational-history?product={first}", status, 200,
          f"items={len((body or {}).get('items', [])) if isinstance(body, dict) else 0}")

# ── 5. 인폼 ────────────────────────────────────────────────────
section("5. 인폼 로그")
status, body = _req("GET", "/api/informs/config", token=TOKEN)
mods = (body or {}).get("modules", []) if isinstance(body, dict) else []
check("GET /api/informs/config", status, 200, f"modules={len(mods)}")

status, body = _req("GET", "/api/informs/recipients", token=TOKEN)
rcount = len((body or {}).get("recipients", [])) if isinstance(body, dict) else 0
check("GET /api/informs/recipients", status, 200, f"recipients={rcount}")

# 인폼 생성 → 프리뷰 → 삭제 round-trip
test_inf = {
    "product": (sprods[0] if sprods else "PRODA"),
    "module": (mods[0] if mods else "GATE"),
    "reason": "재측정",
    "text": f"[smoke] {uuid.uuid4().hex[:6]}",
    "lot_id": "A1000",
    "wafer_id": "1",
}
status, body = _req("POST", "/api/informs", test_inf, token=TOKEN)
created = None
if isinstance(body, dict):
    created = body.get("id") or ((body.get("inform") or {}).get("id") if isinstance(body.get("inform"), dict) else None)
check("POST /api/informs (신규 인폼)", status, 200, f"id={created}")

if created:
    status, body = _req("GET", f"/api/informs/{created}/mail-preview", token=TOKEN)
    sz = (body or {}).get("html_size_kb", 0) if isinstance(body, dict) else 0
    check(f"GET /informs/{created}/mail-preview (v8.8.30 size field)", status, 200,
          f"html_size={sz}KB over_limit={(body or {}).get('html_over_limit')}")
    status, _ = _req("POST", f"/api/informs/delete?id={urllib.parse.quote(created)}", token=TOKEN)
    check("POST /api/informs/delete (cleanup)", status, 200)

# ── 6. 회의 ────────────────────────────────────────────────────
section("6. 회의관리")
status, body = _req("GET", "/api/meetings/list", token=TOKEN)
mcount = len((body or {}).get("meetings", [])) if isinstance(body, dict) else 0
check("GET /api/meetings/list", status, 200, f"meetings={mcount}")

# minutes/append는 v8.8.13 기능 — 실행중 서버가 stale 하면 405 반환(회귀 지표).
status, body = _req("POST", "/api/meetings/minutes/append",
                     {"meeting_id": "xxx", "session_id": "xxx", "text": "x"},
                     token=TOKEN)
# 존재하지 않는 meeting → 404 가 expect (405 면 서버 stale)
check("POST /minutes/append (404 expected = route 등록됨)", status, [404, 400],
      "⚠ 405 반환 = 서버 재시작 필요" if status == 405 else "")

# ── 7. 트래커 ──────────────────────────────────────────────────
section("7. 트래커")
status, body = _req("GET", "/api/tracker/issues?limit=10", token=TOKEN)
icount = len((body or {}).get("issues", [])) if isinstance(body, dict) else 0
check("GET /api/tracker/issues", status, 200, f"issues={icount}")
# v8.8.28: updated_at desc 정렬 + summary 필드 있는지
if status == 200 and isinstance(body, dict) and body.get("issues"):
    first = body["issues"][0]
    has_fields = "updated_at" in first and "summary" in first
    check("tracker issue updated_at+summary (v8.8.28)",
          200 if has_fields else 0, 200, f"fields present={has_fields}")

# ── 8. 달력 + 그룹 + 메시지 ────────────────────────────────────
section("8. 달력 · 그룹 · 메시지")
status, body = _req("GET", "/api/calendar/events", token=TOKEN)
ev = len((body or {}).get("events", [])) if isinstance(body, dict) else 0
check("GET /api/calendar/events", status, 200, f"events={ev}")

status, body = _req("GET", "/api/groups/list", token=TOKEN)
gc = len((body or {}).get("groups", [])) if isinstance(body, dict) else 0
check("GET /api/groups/list", status, 200, f"groups={gc}")

status, body = _req("GET", "/api/messages/notices", token=TOKEN)
check("GET /api/messages/notices", status, 200)

# ── 9. 대시보드 + TableMap ────────────────────────────────────
section("9. 대시보드 · TableMap")
status, _ = _req("GET", "/api/dashboard/charts", token=TOKEN)
check("GET /api/dashboard/charts", status, 200)

status, _ = _req("GET", "/api/dbmap/tables", token=TOKEN)
check("GET /api/dbmap/tables", status, 200)

# ── 10. 인증 실패 경로 ─────────────────────────────────────────
section("10. 인증 방어")
status, _ = _req("POST", "/api/auth/login", {"username": "noexist", "password": "bad"})
check("잘못된 로그인 401", status, 401)

status, _ = _req("GET", "/api/admin/users")  # no token
check("토큰 없이 /admin/users 401", status, 401)

status, _ = _req("GET", "/api/admin/users", token="invalid-token-xxx")
check("잘못된 토큰 /admin/users 401", status, 401)

# ── 요약 ───────────────────────────────────────────────────────
total = PASS + FAIL
print(f"\n{'='*56}")
print(f"SMOKE TEST 결과: {PASS}/{total} PASS  ·  {FAIL} FAIL")
print(f"서버: {BASE}  ·  유저: {USER}")
print('='*56)

sys.exit(0 if FAIL == 0 else 1)
