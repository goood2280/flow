# -*- coding: utf-8 -*-
"""aipd 브리지 — Flow 쪽 조립부 (router_loader 가 자동 로드).

aipd 공통 패키지(setup_flow.py 가 함께 배포)를 Flow 웹에 연결한다:
  - 검토큐: Valve 제안을 엔지니어가 승인/반려 (권한: approve_review)
  - 분석의뢰: 정형 폼 제출 → 템플릿 추천 → split PPT 생성 → 완료
  - 정기 리포트: db/reports/ 조회 + alerts
  - 차트: Vehicle split 비율 / step 흐름 (S3 wide form 소비)
  - /aipd 미니 콘솔 페이지

주의: 데모 콘솔은 Flow 세션 인증 밖의 /aipd/api/* 경로를 쓴다 (AuthMiddleware
는 /api/* 만 가드). 대신 aipd 자체 역할 권한(AuthManager)으로 가드한다.
aipd 가 없으면 available=false 만 응답하고 Flow 본체는 정상 동작.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Query
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter()

try:
    import aipd  # noqa: F401

    _AIPD = True
except ImportError:
    _AIPD = False

_seeded = False


def _seed():
    """데모용 시드 — 유저 역할 + 기본 템플릿 (로컬 모드 전용, 반복 호출 안전).

    실환경에서는 시드하지 않는다 — 권한은 admin UI 의 grant, 템플릿은
    엔지니어가 직접 관리.
    """
    global _seeded
    if _seeded or not _AIPD:
        return
    from aipd.compat import is_local_mode

    if not is_local_mode():
        _seeded = True
        return
    _seeded = True
    try:
        from aipd.core.auth import AuthManager

        auth = AuthManager()
        if auth.role_of("kim") != "analyst":
            auth.grant("kim", "analyst", granted_by="admin")
    except Exception:
        pass
    try:
        from aipd.core.schemas.split_template import (ChartSpec, SplitTemplate,
                                                      TemplatePage, TemplateStore)

        store = TemplateStore()
        if not any(t.name == "eqp-split-basic" for t in store.list()):
            store.save(SplitTemplate(
                name="eqp-split-basic", split_type="eqp",
                description="설비 split 기본 세트",
                pages=[TemplatePage(title="Overview", layout=[
                    ChartSpec(chart_type="box", x="eqp_id", y="ET_VTH_N",
                              title="VTH_N by EQP"),
                    ChartSpec(chart_type="trend", x="wafer_id", y="ET_VTH_N",
                              agg="median", title="VTH_N trend"),
                ])],
            ), updated_by="admin")
    except Exception:
        pass


def _guard(user: str, capability: str):
    from aipd.core.auth import AuthManager

    AuthManager().require(user, capability)


def _err(e: Exception, code: int = 403):
    return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"},
                        status_code=code)


# ================================================================= 상태
@router.get("/aipd/api/status")
def status(user: str = "admin"):
    if not _AIPD:
        return {"available": False, "reason": "aipd 패키지 없음"}
    _seed()
    from aipd.core import cycle
    from aipd.core.auth import AuthManager

    return {
        "available": True,
        "cycle": _safe(lambda: cycle.check()),
        "ui_flags": _safe(lambda: AuthManager().ui_flags(user)),
        "role": _safe(lambda: AuthManager().role_of(user)),
    }


# ================================================================= 검토큐
@router.get("/aipd/api/review")
def review_list(status_name: str = "pending"):
    from aipd.core.schemas import ReviewQueue

    items = ReviewQueue().list(status_name)
    return {"items": [
        {"review_id": i.review_id, "kind": i.kind, "title": i.title,
         "proposed_by": i.proposed_by, "created_at": i.created_at,
         "ai_summary": i.ai_summary, "payload": i.payload}
        for i in items
    ]}


@router.post("/aipd/api/review/{rid}/approve")
def review_approve(rid: str, user: str = "admin", note: str = ""):
    try:
        _guard(user, "approve_review")
        from aipd.core.schemas import ReviewQueue

        item = ReviewQueue().approve(rid, reviewed_by=user, note=note)
        return {"ok": True, "review_id": item.review_id,
                "hint": "Valve 폴러가 수초 내 자동 반영합니다"}
    except PermissionError as e:
        return _err(e)
    except Exception as e:
        return _err(e, 400)


@router.post("/aipd/api/review/{rid}/reject")
def review_reject(rid: str, user: str = "admin", note: str = ""):
    try:
        _guard(user, "approve_review")
        from aipd.core.schemas import ReviewQueue

        item = ReviewQueue().reject(rid, reviewed_by=user, note=note)
        return {"ok": True, "review_id": item.review_id}
    except PermissionError as e:
        return _err(e)
    except Exception as e:
        return _err(e, 400)


# ================================================================= 분석의뢰
@router.get("/aipd/api/requests")
def requests_list():
    from aipd.core.schemas.analysis_request import RequestStore

    store = RequestStore()
    out = {}
    for st in ("pending", "in_progress", "done"):
        out[st] = [{"request_id": r.request_id, "product": r.product,
                    "lot_ids": r.lot_ids, "purpose": r.purpose,
                    "urgency": r.urgency, "template_name": r.template_name,
                    "result_keys": r.result_keys}
                   for r in store.list(st)]
    return out


@router.post("/aipd/api/requests")
def requests_submit(body: dict = Body(...), user: str = "admin"):
    try:
        _guard(user, "submit_analysis_request")
        from aipd.core.schemas.analysis_request import AnalysisRequest, RequestStore

        req = AnalysisRequest(
            product=body.get("product", ""),
            lot_ids=[s.strip() for s in str(body.get("lot_ids", "")).split(",") if s.strip()],
            purpose=body.get("purpose", ""),
            split_info={"split_type": body.get("split_type", "")},
            urgency=body.get("urgency", "normal"),
            requester=user,
        )
        rid = RequestStore().submit(req)
        # AI 템플릿 추천
        from aipd.ai.request_advisor import recommend_template
        from aipd.core.schemas.split_template import TemplateStore

        rec = recommend_template(req, TemplateStore().list()) or {}
        return {"ok": True, "request_id": rid, "recommended": rec.get("template"),
                "reason": rec.get("reason", "")}
    except PermissionError as e:
        return _err(e)
    except ValueError as e:
        return _err(e, 400)


@router.post("/aipd/api/requests/{rid}/generate")
def requests_generate(rid: str, user: str = "admin"):
    """템플릿으로 split PPT 생성 → S3 업로드 → 의뢰 완료 처리."""
    try:
        _guard(user, "create_split_report")
        from pathlib import Path

        from aipd.compat import get_s3_client
        from aipd.ai.request_advisor import recommend_template
        from aipd.core.s3 import S3Layout
        from aipd.core.schemas.analysis_request import RequestStore
        from aipd.core.schemas.split_template import TemplateStore
        from aipd.report import generate_split_ppt
        from aipd.report.vehicle_flow import fetch_flow_data

        store = RequestStore()
        req, _st = store.get(rid)
        templates = TemplateStore().list()
        tname = req.template_name or (recommend_template(req, templates) or {}).get("template")
        if not tname:
            return _err(ValueError("사용할 템플릿 없음 — 먼저 템플릿을 만드세요"), 400)
        store.start(rid, template_name=tname)

        df = fetch_flow_data(req.product, "2026-07-04", source="ET")

        result = generate_split_ppt(
            TemplateStore().load(tname), product=req.product, lot_ids=req.lot_ids,
            data_fetcher=lambda p, lots: df[df["root_lot_id"].astype(str).isin(lots)]
            if "root_lot_id" in df.columns else df,
            out_dir="./RUN/SplitReport", request_id=rid,
        )
        result_keys = []
        if result.get("ppt_path"):
            s3, lay = get_s3_client(), S3Layout()
            key = f"{lay.main_ns}/requests-results/{rid}.pptx"
            s3.put_object(Bucket=lay.bucket, Key=key,
                          Body=Path(result["ppt_path"]).read_bytes())
            result_keys.append(key)
        store.complete(rid, result_keys=result_keys or ["(charts 없음)"])
        return {"ok": True, "template": tname, "charts": result["charts"],
                "result_keys": result_keys}
    except PermissionError as e:
        return _err(e)
    except Exception as e:
        return _err(e, 400)


# ================================================================= 리포트/차트
@router.get("/aipd/api/reports")
def reports(kind: str = "daily"):
    from aipd.compat import get_s3_client
    from aipd.report.periodic_report import list_reports, load_report

    s3 = get_s3_client()
    periods = list_reports(s3, kind)
    latest = load_report(s3, kind, periods[0]) if periods else None
    return {"kind": kind, "periods": periods, "latest": latest}


@router.get("/aipd/api/charts/split-ratio")
def chart_split_ratio(product: str = "PRODA", date: str = "2026-07-04",
                      split_by: str = "eqp_id"):
    from aipd.report.vehicle_flow import fetch_flow_data, split_ratio

    return split_ratio(fetch_flow_data(product, date), split_by=split_by)


@router.get("/aipd/api/charts/step-flow")
def chart_step_flow(product: str = "PRODA", date: str = "2026-07-04",
                    split_by: str = "eqp_id"):
    from aipd.report.vehicle_flow import fetch_flow_data, step_flow

    return step_flow(fetch_flow_data(product, date), split_by=split_by)


def _safe(fn):
    try:
        return fn()
    except Exception as e:
        return {"error": str(e)}


# ================================================================= 미니 콘솔
@router.get("/aipd", response_class=HTMLResponse)
def console():
    return _PAGE


_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Flow × aipd</title>
<style>
body{font-family:'Segoe UI',sans-serif;background:#16181d;color:#ddd;margin:0;padding:16px}
h1{color:#f90;font-size:20px} h3{color:#fb0;font-size:14px;margin:12px 0 6px}
button{background:#f90;color:#111;border:0;border-radius:5px;padding:6px 12px;margin:2px;
font-weight:700;cursor:pointer;font-size:12px} button:hover{background:#fb3}
button.red{background:#c33;color:#fff} select,input{background:#14161a;color:#ddd;
border:1px solid #444;border-radius:4px;padding:5px 8px;margin:2px;font-size:12px}
pre{background:#14161a;border:1px solid #333;border-radius:6px;padding:8px;font-size:11px;
max-height:220px;overflow:auto;white-space:pre-wrap}
.row{display:flex;gap:14px;flex-wrap:wrap}.col{flex:1;min-width:380px}
.panel{background:#20242c;border:1px solid #333;border-radius:8px;padding:12px;margin-bottom:12px}
table{width:100%;border-collapse:collapse;font-size:12px}
td,th{padding:3px 6px;border-bottom:1px solid #2c3038;text-align:left}
.bar{background:#f90;height:14px;border-radius:3px;display:inline-block}
.dim{color:#888;font-size:12px}
</style></head><body>
<h1>Flow × aipd 콘솔</h1>
<div class="dim">검토큐 승인 → Valve 자동 반영, 분석의뢰 → PPT, 정기 리포트, split 차트.
사용자: <select id="user" onchange="tick()">
<option value="admin">admin (전권)</option>
<option value="kim">kim (analyst)</option>
<option value="lee">lee (viewer)</option>
</select> <span id="role" class="dim"></span> (<a href="/" style="color:#f90">Flow 본체</a>)</div>

<div class="row"><div class="col">
<div class="panel"><h3>검토큐 — Valve 제안 승인/반려</h3>
<table id="rq"></table><pre id="rqOut" style="display:none"></pre></div>

<div class="panel"><h3>분석의뢰 제출</h3>
제품 <input id="rp" value="PRODA" size="8">
lot(콤마) <input id="rl" value="T1234,T5678" size="14">
긴급도 <select id="ru"><option>normal</option><option>high</option><option>urgent</option></select><br>
목적 <input id="rr" value="설비(eqp) split 간 VTH 산포 비교" size="40">
<button onclick="submitReq()">의뢰 제출</button>
<div id="reqList"></div><pre id="reqOut" style="display:none"></pre></div>
</div><div class="col">
<div class="panel"><h3>정기 리포트 <span class="dim">(db/reports/daily)</span></h3>
<div id="rep"></div></div>

<div class="panel"><h3>Vehicle Split 차트 <span class="dim">(② AI namespace 소비)</span></h3>
<button onclick="charts()">차트 로드 (split 비율 + step 흐름)</button>
<div id="ratio"></div><pre id="flowJson" style="display:none"></pre><div id="sankey"></div></div>
</div></div>
<script>
const U=()=>document.getElementById('user').value;
async function j(u,opt){const r=await fetch(u,opt);return r.json()}
function esc(s){return String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;')}
async function tick(){
  const s=await j('/aipd/api/status?user='+U());
  document.getElementById('role').textContent=' role='+(s.role||'?');
  loadReview(); loadReqs(); loadRep();
}
async function loadReview(){
  const d=await j('/aipd/api/review');
  document.getElementById('rq').innerHTML='<tr><th>제안</th><th>kind</th><th></th></tr>'+
   (d.items.length? d.items.map(i=>`<tr><td>${esc(i.title)}<br><span class=dim>${i.review_id}</span></td>
    <td>${i.kind}</td><td><button onclick="act('${i.review_id}','approve')">승인</button>
    <button class=red onclick="act('${i.review_id}','reject')">반려</button></td></tr>`).join('')
    : '<tr><td class=dim>대기 항목 없음 — Valve 콘솔에서 "미매칭 스캔" 실행</td></tr>');
}
async function act(rid,a){
  const r=await j(`/aipd/api/review/${rid}/${a}?user=`+U(),{method:'POST'});
  const o=document.getElementById('rqOut');o.style.display='block';o.textContent=JSON.stringify(r,null,1);
  loadReview();
}
async function submitReq(){
  const body={product:rp.value,lot_ids:rl.value,purpose:rr.value,urgency:ru.value,split_type:'eqp'};
  const r=await j('/aipd/api/requests?user='+U(),{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const o=document.getElementById('reqOut');o.style.display='block';o.textContent=JSON.stringify(r,null,1);
  loadReqs();
}
async function loadReqs(){
  const d=await j('/aipd/api/requests');
  const row=(r,st)=>`<tr><td>${r.request_id}<br><span class=dim>${esc(r.purpose)}</span></td>
   <td>${st}</td><td>${st!=='done'?`<button onclick="gen('${r.request_id}')">PPT 생성</button>`
   :esc((r.result_keys||[]).join(','))}</td></tr>`;
  document.getElementById('reqList').innerHTML='<table>'+
   ['pending','in_progress','done'].map(st=>d[st].map(r=>row(r,st)).join('')).join('')+'</table>';
}
async function gen(rid){
  const r=await j(`/aipd/api/requests/${rid}/generate?user=`+U(),{method:'POST'});
  const o=document.getElementById('reqOut');o.style.display='block';o.textContent=JSON.stringify(r,null,1);
  loadReqs();
}
async function loadRep(){
  const d=await j('/aipd/api/reports?kind=daily');
  if(!d.periods.length){document.getElementById('rep').innerHTML='<span class=dim>아직 없음 — Valve 콘솔에서 "daily 리포트 발행"</span>';return}
  const L=d.latest, al=(L.anomalies||[]);
  document.getElementById('rep').innerHTML=`<b>${d.periods[0]}</b> · 이상 ${al.length}건
   <ul>${al.map(a=>`<li><b style="color:#f66">[${a.severity}] ${a.type}</b> ${a.item}: ${esc(a.detail)}</li>`).join('')}</ul>
   <pre>${esc(JSON.stringify(L.totals))}</pre>`;
}
async function charts(){
  const r=await j('/aipd/api/charts/split-ratio?split_by=eqp_id');
  document.getElementById('ratio').innerHTML='<h4 style="margin:8px 0 4px">split 비율 (wafer '+r.total_wafers+'장)</h4>'+
   r.slices.map(s=>`<div>${esc(s.value)} <span class=bar style="width:${s.pct*2.4}px"></span> ${s.pct}% (${s.wafers})</div>`).join('');
  const f=await j('/aipd/api/charts/step-flow?split_by=eqp_id');
  document.getElementById('sankey').innerHTML='<h4 style="margin:8px 0 4px">step 흐름 (노드/링크)</h4><table>'+
   '<tr><th>node</th><th>wafers</th><th>step내 비율</th></tr>'+
   f.nodes.map(n=>`<tr><td>${esc(n.id)}</td><td>${n.wafers}</td><td>${n.pct_in_step}%</td></tr>`).join('')+'</table>'+
   (f.links.length?'<table><tr><th>전이</th><th>wafers</th></tr>'+
    f.links.map(l=>`<tr><td>${esc(l.source)} → ${esc(l.target)}</td><td>${l.wafers}</td></tr>`).join('')+'</table>':'');
}
tick(); setInterval(loadReview, 5000);
</script></body></html>"""
