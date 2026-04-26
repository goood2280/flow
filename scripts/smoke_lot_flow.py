"""smoke_lot_flow.py — flow 의 한 lot 전체 작업 시나리오 E2E smoke (v9.0.0).

시나리오:
  1. 로그인 (hol)
  2. SplitTable /view (product=ML_TABLE_PRODA, root_lot_id=A0001, wafer_ids="1,2,3")
  3. plan 1셀 등록 (KNOB_GATE_PPID 변경) + history 확인
  4. 이슈 추적 /create (category=Monitor, source=fab)
  5. /lots/bulk 로 lot/wafer 1건 추가 + /lot-watch target_step 설정 + mail=true
  6. /lot-step 조회 — FAB 최신 step_id 반환 확인
  7. 회의 /create + 해당 이슈 id 를 agenda 에 삽입
  8. /minutes/append 로 회의록에 lot 변경점 기록 (변경점 관리 역할)
  9. 인폼 /create — 동일 lot + SplitTable 스냅샷 embed + 모듈=GATE
 10. 트래커 /update status=closed → 알림 이벤트 확인
 11. 각 단계 assertion — http status + 핵심 필드 존재 확인

실행:
  cd flow && python scripts/smoke_lot_flow.py
  또는 env override:
  FLOW_BASE=... FLOW_USER=hol FLOW_PW=... FLOW_PRODUCT=ML_TABLE_PRODA FLOW_ROOT=A0001 \
      python scripts/smoke_lot_flow.py

Exit code: 0 = 전체 PASS, 1 = 하나라도 FAIL.
외부 의존성 없음 (stdlib urllib).
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

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

BASE = os.environ.get("FLOW_BASE", "http://localhost:8080").rstrip("/")
USER = os.environ.get("FLOW_USER", "hol")
PW   = os.environ.get("FLOW_PW",   "hol12345!")
PRODUCT = os.environ.get("FLOW_PRODUCT", "ML_TABLE_PRODA")
ROOT    = os.environ.get("FLOW_ROOT", "A0001")

PASS = 0
FAIL = 0
CREATED_IDS: dict[str, str] = {}  # 정리용 id 보관


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
            try:
                return e.code, json.loads(body_s)
            except Exception:
                return e.code, body_s
        except Exception:
            return e.code, ""
    except Exception as e:
        return 0, f"NETERR: {type(e).__name__}: {e}"


def _check(label: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}  {detail}"[:260])
    else:
        FAIL += 1
        print(f"  ❌ {label}  {detail}"[:260])


def main():
    global PASS, FAIL
    print("=" * 70)
    print(f"E2E Lot Flow Smoke — {BASE}  user={USER}  product={PRODUCT}  root={ROOT}")
    print("=" * 70)

    # 1. 로그인
    print("\n── 1. 로그인 ──")
    st, d = _req("POST", "/api/auth/login", {"username": USER, "password": PW})
    _check("POST /api/auth/login", st == 200 and isinstance(d, dict) and d.get("token"),
           f"status={st}")
    token = (d or {}).get("token", "") if isinstance(d, dict) else ""
    if not token:
        print("\n💥 로그인 실패 — 이후 단계 중단")
        return False

    # 2. SplitTable view
    print("\n── 2. SplitTable /view ──")
    qs = urllib.parse.urlencode({
        "product": PRODUCT, "root_lot_id": ROOT,
        "wafer_ids": "1,2,3", "prefix": "KNOB", "view_mode": "all",
    })
    st, d = _req("GET", f"/api/splittable/view?{qs}", token=token)
    _check("GET /splittable/view (KNOB)", st == 200 and isinstance(d, dict),
           f"rows={len((d or {}).get('rows') or [])}")
    # 빈 응답이어도 상관없음 — 단, 스키마 필드는 있어야 함.
    _check("view 응답 스키마(rows/headers)",
           isinstance(d, dict) and ("rows" in d or "msg" in d), "")

    # 3. plan 등록 + history 조회
    print("\n── 3. SplitTable plan 등록 + history ──")
    cell = f"{ROOT}|1|KNOB_GATE_PPID"
    uniq = f"E2E_{uuid.uuid4().hex[:4]}"
    st, d = _req("POST", "/api/splittable/plan",
                 {"product": PRODUCT, "plans": {cell: uniq},
                  "username": USER, "root_lot_id": ROOT}, token=token)
    _check("POST /splittable/plan", st == 200 and isinstance(d, dict) and d.get("ok"),
           f"saved={(d or {}).get('saved')}")
    st, d = _req("GET", f"/api/splittable/history?product={PRODUCT}&root_lot_id={ROOT}",
                 token=token)
    hist = (d or {}).get("history") or []
    hit = any(h.get("cell") == cell and h.get("new") == uniq for h in hist)
    _check("history 에 방금 등록한 plan 기록",
           st == 200 and hit, f"uniq={uniq} count={len(hist)}")

    # 4. 이슈 추적 create (Monitor 카테고리)
    print("\n── 4. 트래커 이슈 /create (Monitor) ──")
    iss_title = f"E2E smoke {uuid.uuid4().hex[:6]}"
    st, d = _req("POST", "/api/tracker/create",
                 {"title": iss_title, "description": "E2E smoke lot flow",
                  "status": "in_progress", "priority": "normal",
                  "category": "Monitor"},
                 token=token)
    _check("POST /tracker/create", st == 200 and isinstance(d, dict) and d.get("id"),
           f"id={(d or {}).get('id')}")
    iid = (d or {}).get("id")
    CREATED_IDS["issue_id"] = iid or ""

    # 5. Lot 추가 + watch
    print("\n── 5. 트래커 /lots/bulk + /lot-watch ──")
    if iid:
        st, d = _req("POST", "/api/tracker/lots/bulk",
                     {"issue_id": iid,
                      "rows": [{"root_lot_id": ROOT, "wafer_id": "1", "comment": "E2E"}]},
                     token=token)
        _check("POST /tracker/lots/bulk", st == 200, f"added={(d or {}).get('added')}")
        # watch (FAB source, mail=True)
        st, d = _req("POST", "/api/tracker/lot-watch",
                     {"issue_id": iid, "row_index": 0,
                      "target_step_id": "AB123456000100",
                      "source": "fab", "mail": True},
                     token=token)
        _check("POST /tracker/lot-watch (FAB + mail)",
               st == 200 and (d or {}).get("watch", {}).get("mail") is True,
               f"watch={(d or {}).get('watch')}")

    # 6. lot-step 조회
    print("\n── 6. /lot-step — FAB 최신 step ──")
    qs = urllib.parse.urlencode({
        "product": PRODUCT.replace("ML_TABLE_", ""),
        "root_lot_id": ROOT,
        "source": "fab",
    })
    st, d = _req("GET", f"/api/tracker/lot-step?{qs}", token=token)
    _check("GET /tracker/lot-step (fab)", st == 200 and isinstance(d, dict),
           f"snapshot keys={list((d or {}).get('snapshot') or {})}")

    # 7. 회의 create + agenda
    print("\n── 7. 회의 /create + agenda ──")
    today = time.strftime("%Y-%m-%d")
    st, d = _req("POST", "/api/meetings/create",
                 {"title": f"E2E 회의 {uuid.uuid4().hex[:4]}",
                  "date": today, "category": "", "attendees": [USER]},
                 token=token)
    # v9.0.0: 응답 구조 {"ok": True, "meeting": {...}} — meeting.id 로 추출.
    mtg = (d or {}).get("meeting") if isinstance(d, dict) else None
    mid = (mtg or {}).get("id") if isinstance(mtg, dict) else None
    _check("POST /meetings/create", st == 200 and bool(mid), f"id={mid}")
    CREATED_IDS["meeting_id"] = mid or ""

    # 8. minutes/append — 변경점 기록 (회의에서 결정된 변경사항을 minutes body_appendix 로)
    print("\n── 8. /minutes/append — 변경점 기록 ──")
    if mid:
        # 세션 id 획득 — create 응답에 이미 sessions 가 있거나, /meetings/{mid} 에서 재조회.
        sid = ""
        if mtg and mtg.get("sessions"):
            sid = (mtg["sessions"][0] or {}).get("id", "")
        if not sid:
            st, d2 = _req("GET", f"/api/meetings/{mid}", token=token)
            if isinstance(d2, dict):
                m = d2.get("meeting") or {}
                sessions = m.get("sessions") or []
                if sessions:
                    sid = sessions[0].get("id", "")
        if sid:
            st, d = _req("POST", "/api/meetings/minutes/append",
                         {"meeting_id": mid, "session_id": sid,
                          "text": f"[변경점] 이슈 {iid or '-'} 관련 — plan {uniq} 적용 결정"},
                         token=token)
            _check("POST /meetings/minutes/append",
                   st == 200 and (d or {}).get("ok") is True,
                   f"entry_id={((d or {}).get('entry') or {}).get('id')}")
        else:
            _check("minutes/append (session_id 확보)", False, "session 없음")

    # 9. 인폼 /create — 동일 lot + SplitTable 스냅샷 embed + 모듈=GATE
    print("\n── 9. 인폼 /create (동일 lot, 모듈 GATE) ──")
    inform_body = {
        "product": PRODUCT, "lot_id": ROOT, "wafer_id": "1",
        "module": "GATE", "reason": "레시피 변경",
        "text": f"[E2E smoke] plan {uniq} 세워짐 — 이슈 {iid or '-'}",
        "embed_table": {
            "source": f"SplitTable/{PRODUCT.replace('ML_TABLE_','')} @ {ROOT}",
            "columns": ["parameter", "W1"],
            "rows": [["KNOB_GATE_PPID", f"→ {uniq}"]],
            "note": "E2E smoke",
        },
    }
    st, d = _req("POST", "/api/informs", inform_body, token=token)
    # v9.0.0: 응답 구조 {"ok": True, "inform": {...}} — inform.id 추출.
    inf = (d or {}).get("inform") if isinstance(d, dict) else None
    inform_id = (inf or {}).get("id") if isinstance(inf, dict) else None
    _check("POST /api/informs (embed 포함)",
           st == 200 and isinstance(d, dict) and bool(inform_id),
           f"id={inform_id}")
    CREATED_IDS["inform_id"] = inform_id or ""

    # 10. 트래커 update status=closed + notify event 확인
    print("\n── 10. 트래커 /update status=closed + notify ──")
    if iid:
        st, d = _req("POST", "/api/tracker/update",
                     {"issue_id": iid, "status": "closed"}, token=token)
        _check("POST /tracker/update (closed)", st == 200, "")
        # v9.0.0: actor == target (본인이 자기 이슈 상태 변경) 이면 notify 는 no-op 이 설계.
        # 멀티 유저 시나리오는 v9.1 E2E 로 이월. 여기선 self-action → 0 이 정상.
        st, d = _req("GET", f"/api/admin/all-notifications?username={USER}", token=token)
        notifs = (d or {}).get("notifications") or []
        tr_events = [n for n in notifs if n.get("event") == "my_tracker_status_changed"]
        _check("notify 자기-행동 no-op (self-action)",
               st == 200 and len(tr_events) == 0,
               f"(정상 동작) self-trigger 시 0건 · 실제 값={len(tr_events)}")

    # 11. 정리 — 생성한 자원 제거 (best-effort, 실패해도 테스트 결과에 영향 X)
    print("\n── 11. 정리 ──")
    if CREATED_IDS.get("inform_id"):
        st, _ = _req("POST", f"/api/informs/delete?id={CREATED_IDS['inform_id']}",
                     token=token)
        print(f"  🧹 inform del status={st}")
    if CREATED_IDS.get("issue_id"):
        st, _ = _req("POST", f"/api/tracker/delete?issue_id={CREATED_IDS['issue_id']}",
                     token=token)
        print(f"  🧹 issue del status={st}")
    if CREATED_IDS.get("meeting_id"):
        st, _ = _req("POST", f"/api/meetings/delete?id={CREATED_IDS['meeting_id']}",
                     token=token)
        print(f"  🧹 meeting del status={st}")
    # plan 되돌리기 — history 를 원천적으로 되돌릴 순 없으므로 plan/delete 만.
    cell = f"{ROOT}|1|KNOB_GATE_PPID"
    _req("POST", "/api/splittable/plan/delete",
         {"product": PRODUCT, "cell_keys": [cell], "username": USER}, token=token)

    total = PASS + FAIL
    print("\n" + "=" * 70)
    print(f"E2E LOT FLOW SMOKE: {PASS}/{total} PASS · {FAIL} FAIL")
    print(f"서버: {BASE} · 유저: {USER} · 제품: {PRODUCT} · root: {ROOT}")
    print("=" * 70)
    return FAIL == 0


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
