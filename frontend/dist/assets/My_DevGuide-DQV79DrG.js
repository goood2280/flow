import{r as p,j as e}from"./index-CADKb9m1.js";const a="'JetBrains Mono',monospace";function t({children:o}){return e.jsx("pre",{style:{background:"#171717",borderRadius:8,padding:"14px 18px",border:"1px solid #333",overflow:"auto",fontFamily:a,fontSize:14,lineHeight:1.8,color:"#e5e5e5",whiteSpace:"pre",margin:"12px 0"},children:o})}function i({children:o,id:l}){return e.jsxs("h2",{id:l,style:{fontSize:16,fontWeight:700,color:"var(--accent,#f97316)",marginTop:36,marginBottom:14,paddingBottom:6,borderBottom:"1px solid var(--border,#333)",fontFamily:a},children:[e.jsxs("span",{style:{color:"var(--text-secondary)"},children:[">"," "]}),o]})}function d({method:o,path:l,desc:n}){const c={GET:{fg:"var(--ok)",bg:"var(--ok-50)"},POST:{fg:"var(--brand)",bg:"var(--brand-50)"},DELETE:{fg:"var(--danger)",bg:"var(--danger-50)"}}[o]||{fg:"var(--text-secondary)",bg:"var(--bg-tertiary)"};return e.jsxs("div",{style:{display:"flex",alignItems:"center",gap:10,padding:"8px 0",borderBottom:"1px solid var(--border,#222)",fontSize:14},children:[e.jsx("span",{style:{fontFamily:a,fontSize:14,fontWeight:700,padding:"2px 8px",borderRadius:4,minWidth:44,textAlign:"center",background:c.bg,color:c.fg},children:o}),e.jsx("span",{style:{fontFamily:a,color:"var(--text-primary)",minWidth:300},children:l}),e.jsx("span",{style:{color:"var(--text-secondary)"},children:n})]})}function r({file:o,prefix:l,desc:n}){return e.jsxs("div",{style:{display:"flex",alignItems:"center",gap:10,padding:"7px 0",borderBottom:"1px solid var(--border,#222)",fontSize:14},children:[e.jsx("span",{style:{fontFamily:a,color:"var(--text-primary)",minWidth:190},children:o}),e.jsx("span",{style:{fontFamily:a,color:"var(--accent,#f97316)",minWidth:200},children:l}),e.jsx("span",{style:{color:"var(--text-secondary)"},children:n})]})}const x=[{id:"arch",label:"아키텍처"},{id:"files",label:"파일 구조"},{id:"tabs",label:"탭 구성"},{id:"api",label:"API 레퍼런스"},{id:"db",label:"데이터 루트/DB"},{id:"schema",label:"표준 스키마"},{id:"flowi",label:"Flow-i 규칙"},{id:"perf",label:"대용량 운영"},{id:"ux",label:"UX 시스템"},{id:"add",label:"기능 추가"},{id:"update",label:"업데이트/배포"},{id:"theme",label:"테마 시스템"},{id:"infra",label:"인프라"}];function m(){const[o,l]=p.useState("arch"),n=s=>{var c;l(s),(c=document.getElementById(s))==null||c.scrollIntoView({behavior:"smooth",block:"start"})};return e.jsxs("div",{style:{display:"flex",minHeight:"calc(100vh - 52px)",background:"var(--bg-primary,#1a1a1a)",color:"var(--text-primary,#e5e5e5)",fontFamily:"'Pretendard',sans-serif"},children:[e.jsxs("div",{style:{width:190,padding:"20px 10px",borderRight:"1px solid var(--border,#333)",position:"sticky",top:52,height:"calc(100vh - 52px)",overflowY:"auto",flexShrink:0},children:[e.jsxs("div",{style:{fontSize:14,fontWeight:700,color:"var(--accent,#f97316)",textTransform:"uppercase",letterSpacing:"0.08em",marginBottom:12,paddingLeft:10,fontFamily:a},children:[">"," 개발자_가이드"]}),x.map(s=>e.jsx("div",{onClick:()=>n(s.id),style:{padding:"6px 10px",borderRadius:5,cursor:"pointer",fontSize:14,marginBottom:1,fontFamily:a,background:o===s.id?"var(--accent-glow,#f9731622)":"transparent",color:o===s.id?"var(--accent,#f97316)":"var(--text-secondary,#a3a3a3)",fontWeight:o===s.id?600:400},children:s.label},s.id))]}),e.jsxs("div",{style:{flex:1,padding:"28px 36px",maxWidth:860,overflow:"auto",lineHeight:1.8,fontSize:14,color:"var(--text-secondary,#a3a3a3)"},children:[e.jsx(i,{id:"arch",children:"아키텍처"}),e.jsxs("p",{children:["flow = ",e.jsx("strong",{style:{color:"var(--text-primary)"},children:"FastAPI"})," (백엔드) + ",e.jsx("strong",{style:{color:"var(--text-primary)"},children:"React + Vite"})," (프론트엔드) + ",e.jsx("strong",{style:{color:"var(--text-primary)"},children:"Polars/DuckDB/Parquet"})," (데이터)"]}),e.jsx(t,{children:`[Browser] ──HTTP──> [FastAPI :8080]
                        ├── /api/*               → backend/routers/*.py (auto-loaded)
                        ├── /version.json        → 버전 표시 (mtime 기반)
                        ├── /runtime-roots.json  → root 해석 진단 (비인증)
                        └── /*                   → frontend/dist/ (SPA)

FastAPI startup (backend/app.py):
  runtime_limits 적용 (thread/memory cap)
    → AuthMiddleware + ResourceGuardMiddleware
    → include_router_modules()      # routers/ 동적 로드
    → start_background_services()   # scheduler, cache sweeper 등
    → ensure_seed_admin()`}),e.jsxs("p",{children:[e.jsx("strong",{style:{color:"var(--text-primary)"},children:"진입점:"})," 루트 ",e.jsx("code",{children:"app.py"}),"는 uvicorn shim이고 실제 앱 조립은 ",e.jsx("code",{children:"backend/app.py"}),"가 담당합니다. 루트의 ",e.jsx("code",{children:"core/"}),", ",e.jsx("code",{children:"app_v2/"}),", ",e.jsx("code",{children:"routers/"})," 폴더는 ",e.jsx("code",{children:"backend/"})," 하위 실코드로 위임하는 import 호환 shim입니다."]}),e.jsxs("p",{children:[e.jsx("strong",{style:{color:"var(--text-primary)"},children:"동적 라우터 로딩:"})," ",e.jsx("code",{children:"backend/routers/"}),"에 .py 파일을 넣으면 ",e.jsx("code",{children:"app_v2/runtime/router_loader.py"}),"가 자동으로 등록합니다."]}),e.jsx(i,{id:"files",children:"파일 구조"}),e.jsx(t,{children:`flow/
├── app.py                   # uvicorn shim (uvicorn app:app)
├── _build_setup.py          # setup.py 빌더 (main 체크아웃에서 실행)
├── setup.py                 # self-contained installer (생성물)
├── VERSION.json             # 버전 메타 + 릴리스 노트
├── core/ app_v2/ routers/   # backend/ 로 위임하는 import shim
│
├── backend/
│   ├── app.py               # FastAPI app assembly, static serving
│   ├── app_v2/
│   │   ├── runtime/         # router_loader, security(auth middleware),
│   │   │                    # resource_guard, startup
│   │   ├── shared/          # json_store(JsonFileStore), result,
│   │   │                    # source_adapter, contracts
│   │   └── modules/         # 기능별 domain/repository/service
│   │       ├── tracker/           # issue domain/repo/service
│   │       ├── meetings/          # meeting repo/service
│   │       ├── informs/           # SplitTable embed builder
│   │       ├── splittable/        # notes/rulebook repo+service,
│   │       │                      # product_adapter, cache_builder
│   │       ├── agent_runtime/     # 에이전트 actions/executor/prompts
│   │       ├── semantic_learning/ # extractor/inbox/proposer
│   │       └── semantic_lexicon/  # semantic layer store/service
│   ├── core/                # 70+ 파일: paths, roots, auth, session,
│   │                        # notify, backup, mail, duckdb_engine,
│   │                        # llm_adapter, home_orchestrator, matching,
│   │                        # runtime_limits, s3_sync, sysmon ...
│   └── routers/             # 35개 라우터 ← 여기 .py 넣으면 자동 로드
│
├── frontend/
│   ├── vite.config.js       # Vite 설정 (proxy → :8080)
│   ├── src/
│   │   ├── App.jsx          # 전역 shell, tab layout, error boundary
│   │   ├── config.js        # 탭/소탭/권한 메타 (TABS, SUB_TABS)
│   │   ├── app/
│   │   │   ├── pageRegistry.jsx  # tab key → lazy page 매핑
│   │   │   └── useFlowShell.js   # auth/theme/tab/notification 상태
│   │   ├── lib/
│   │   │   ├── api.js       # 인증 fetch/download helper (필수 사용)
│   │   │   └── permissions.js
│   │   ├── components/      # UXKit, Modal, Toast, Loading,
│   │   │   └── agent/       # PlotlyChart, agent 전용 패널 등
│   │   └── pages/           # My_*.jsx 19개 + SplitTable/ 로컬 조각
│   └── dist/                # npm run build 결과 (서버가 서빙)
│
├── scripts/                 # smoke_test, preflight_internal,
│                            # empty_root_smoke, e2e_qa, seed_*, migrate_*
├── tests/                   # pytest (+ tests/inform, tests/agent)
├── docs/                    # ARCHITECTURE, DEVELOPMENT, features/*
└── data/                    # 로컬 fallback root (Git 제외)
    ├── Fab/                 # local Parquet/rulebook/ML_TABLE root
    └── flow-data/           # local 사용자/운영 상태`}),e.jsx(i,{id:"tabs",children:"탭 구성"}),e.jsxs("p",{children:["탭 메타는 ",e.jsx("code",{children:"frontend/src/config.js"}),"의 ",e.jsx("code",{children:"TABS"}),", 페이지 연결은 ",e.jsx("code",{children:"app/pageRegistry.jsx"}),"의 ",e.jsx("code",{children:"PAGE_MAP"}),"에서 관리합니다. 소탭 권한은 ",e.jsx("code",{children:"SUB_TABS"}),"와 백엔드 ",e.jsx("code",{children:"core/auth.py"}),"의 ",e.jsx("code",{children:"TAB_SUBTABS"}),"를 동기로 유지합니다."]}),e.jsx(t,{children:`main   : home(홈)
data   : filebrowser(파일탐색기)  dashboard(대시보드)
         splittable(스플릿 테이블)
tool   : diagnosis(에이전트)  tracker(ET 추적)  valve(매칭알람)
         teg(TEG 위치 조회)  ettime(ET 측정시간)  inform(인폼 로그)
         reformatize(리포마타이즈)  meeting(회의관리)
         calendar(변경점 관리)
system : admin(관리자, adminOnly)
         devguide(개발자 가이드, adminOnly + strictAdmin)

소탭(SUB_TABS):
  filebrowser : db / files
  splittable  : view / history
  inform      : 인폼 / 매트릭스 / 로그
  diagnosis   : 기능 카탈로그 / 실행 추적 / Workflow 템플릿

* tablemap 페이지는 PAGE_MAP에는 등록되어 있으나 네비 탭에는 없음.`}),e.jsx(i,{id:"api",children:"API 레퍼런스"}),e.jsxs("p",{children:["보안상 Swagger UI는 비활성화되어 있습니다. 모든 ",e.jsx("code",{children:"/api/*"}),"는 AuthMiddleware의 세션 토큰 인증을 통과해야 하며, admin API는 ",e.jsx("code",{children:"require_admin"})," 경계를 유지합니다. 정확한 shape는 각 라우터와 smoke test를 기준으로 확인하세요."]}),e.jsx("div",{style:{fontSize:14,fontWeight:700,color:"var(--accent)",marginTop:20,marginBottom:8,fontFamily:a},children:"라우터 카탈로그 (backend/routers/)"}),e.jsx(r,{file:"auth.py",prefix:"/api/auth",desc:"로그인/로그아웃/가입/비번 변경·리셋"}),e.jsx(r,{file:"admin.py",prefix:"/api/admin",desc:"유저관리/알림/백업/관리 설정"}),e.jsx(r,{file:"groups.py",prefix:"/api/groups",desc:"그룹 가시성"}),e.jsx(r,{file:"mail_groups.py",prefix:"/api/mail-groups",desc:"메일 그룹"}),e.jsx(r,{file:"messages.py",prefix:"/api/messages",desc:"유저 간 메시지"}),e.jsx(r,{file:"session_api.py",prefix:"/api/session",desc:"유저 세션 저장/복원"}),e.jsx(r,{file:"monitor.py",prefix:"/api/monitor, /api/system",desc:"시스템/자원 모니터, heartbeat"}),e.jsx(r,{file:"filebrowser.py",prefix:"/api/filebrowser",desc:"Parquet/CSV 탐색 + SQL 필터"}),e.jsx(r,{file:"dashboard.py",prefix:"/api/dashboard",desc:"대시보드 차트/스냅샷"}),e.jsx(r,{file:"splittable.py",prefix:"/api/splittable",desc:"SplitTable view/캐시/rulebook/notes"}),e.jsx(r,{file:"informs(_extra).py",prefix:"/api/informs",desc:"인폼 로그/매트릭스/메일"}),e.jsx(r,{file:"tracker.py",prefix:"/api/tracker",desc:"이슈 추적, 카테고리 관리"}),e.jsx(r,{file:"meetings.py",prefix:"/api/meetings",desc:"회의관리, 메일/캘린더 연동"}),e.jsx(r,{file:"calendar.py",prefix:"/api/calendar",desc:"변경점 관리"}),e.jsx(r,{file:"valve_alerts.py",prefix:"/api/valve-alerts",desc:"매칭알람"}),e.jsx(r,{file:"teg_map.py",prefix:"/api/teg-map",desc:"TEG 위치 조회, Mapfile 체크"}),e.jsx(r,{file:"et_time.py",prefix:"/api/et-time",desc:"ET 측정시간"}),e.jsx(r,{file:"reformatize.py",prefix:"/api/reformatize",desc:"리포마타이즈 (vehicle reformatter)"}),e.jsx(r,{file:"reformatter.py",prefix:"/api/reformatter",desc:"리포맷터"}),e.jsx(r,{file:"sql_workspace.py",prefix:"/api/sql-workspace",desc:"SQL 작업대"}),e.jsx(r,{file:"lot_progress.py",prefix:"/api/lot-progress",desc:"lot 진행 조회/캐시"}),e.jsx(r,{file:"ml.py",prefix:"/api/ml",desc:"ML_TABLE 조회"}),e.jsx(r,{file:"dbmap.py",prefix:"/api/dbmap",desc:"DB 매핑"}),e.jsx(r,{file:"catalog.py",prefix:"/api/catalog",desc:"스키마/소스 카탈로그"}),e.jsx(r,{file:"knowledge.py",prefix:"/api/knowledge",desc:"지식/위키/그래프"}),e.jsx(r,{file:"semiconductor.py",prefix:"/api/...",desc:"반도체 진단(RCA)"}),e.jsx(r,{file:"llm.py",prefix:"/api/llm",desc:"LLM 설정/어댑터"}),e.jsx(r,{file:"agent.py",prefix:"/api/agent",desc:"에이전트 실행/시맨틱 레이어"}),e.jsx(r,{file:"home.py, home_agent.py",prefix:"/api/home, /api/home-agent",desc:"홈 화면, 홈 오케스트레이터"}),e.jsx(r,{file:"flowi_learning.py",prefix:"/api/flowi-learning",desc:"Flow-i 학습/피드백"}),e.jsx(r,{file:"skills.py",prefix:"/api/skills",desc:"스킬 카탈로그"}),e.jsx(r,{file:"s3_ingest.py",prefix:"/api/s3ingest",desc:"S3 인제스트/동기화"}),e.jsx(r,{file:"aipd_bridge.py",prefix:"—",desc:"aipd 연동 브리지"}),e.jsx("div",{style:{fontSize:14,fontWeight:700,color:"var(--accent)",marginTop:20,marginBottom:8,fontFamily:a},children:"핵심 엔드포인트 예시"}),e.jsx(d,{method:"POST",path:"/api/auth/login",desc:"로그인 → 세션 토큰"}),e.jsx(d,{method:"POST",path:"/api/auth/register",desc:"회원가입 → admin 승인 대기"}),e.jsx(d,{method:"GET",path:"/api/auth/me",desc:"현재 사용자 확인"}),e.jsx(d,{method:"POST",path:"/api/session/save",desc:"유저 세션 저장 (탭, 폼 데이터)"}),e.jsx(d,{method:"GET",path:"/api/session/load",desc:"유저 세션 복원"}),e.jsx(d,{method:"GET",path:"/api/monitor/system",desc:"CPU/메모리/디스크 사용량"}),e.jsx(d,{method:"POST",path:"/api/monitor/heartbeat",desc:"서버 유지용 heartbeat (cron)"}),e.jsx(i,{id:"db",children:"데이터 루트/DB"}),e.jsxs("p",{children:["데이터 루트는 ",e.jsx("code",{children:"backend/core/roots.py"}),"와 ",e.jsx("code",{children:"core/paths.py"}),"의 resolver를 통해서만 해석합니다. 특정 DB 경로를 하드코딩하지 않습니다."]}),e.jsx(t,{children:`db_root 우선순위:
  FLOW_DB_ROOT
    → runtime profile / admin_settings 의 data_roots.db
    → /config/work/sharedworkspace/DB   (shared defaults 활성 시)
    → data/Fab                          (로컬 fallback)

data_root  : FLOW_DATA_ROOT → ... → data/flow-data
             (users, settings, tracker, informs, meetings,
              calendar, sessions, logs 등 운영 상태)
base_root  : db_root 의 호환 alias (별도 root 아님)
wafer_map_root : 기본값 <db_root>/wafer_maps

* 코드 업데이트/setup.py/frontend build 가 data_root 를
  삭제하거나 덮어쓰면 안 됨.
* 현재 해석 결과는 /runtime-roots.json 에서 확인.`}),e.jsx(t,{children:`# Hive Partition 규칙
{DB root}/
  {RAWDATA_NAME}/
    {product_name}/
      date=YYYY-MM-DD/
        part-00000.parquet

# Polars 읽기
import polars as pl
df = pl.read_parquet("ProductA/date=*/*.parquet")          # 전체 병합
df = pl.read_parquet("ProductA/date=2024-12-17/*.parquet") # 특정 날짜
df = pl.read_parquet(files).filter(
    pl.sql_expr("item_id = 'VTH' AND et_value > 0.5"))     # SQL 필터`}),e.jsxs("p",{children:[e.jsx("strong",{style:{color:"var(--text-primary)"},children:"DuckDB:"})," parquet/csv 원본 위의 in-memory read-only query engine으로만 사용합니다 (",e.jsx("code",{children:"core/duckdb_engine.py"}),"). 원본 DB 파일을 수정하거나 DuckDB database 파일로 변환하지 않습니다."]}),e.jsx(i,{id:"schema",children:"표준 스키마"}),e.jsx("p",{children:"Flow의 분석 기능과 Flow-i는 아래 contract를 기준으로만 데이터를 연결합니다. 컬럼명은 schema catalog에서 실제 존재 여부를 확인한 뒤 사용하며, 없는 컬럼은 추측하지 않습니다."}),e.jsx(t,{children:`# 공통 식별자
product        # 제품명 또는 ML_TABLE product
root_lot_id    # root lot
fab_lot_id     # 최신 FAB lot, splittable match_cache 에서 우선 조회
wafer_id       # wafer 식별자
lot_wf         # root_lot_id + "_" + wafer_id
shot_id        # optional, 있으면 lot_wf 보다 우선
die_x, die_y   # optional, shot_id 없을 때 shot/die 매칭 후보
measure_time   # 측정 시각 또는 source update time`}),e.jsxs("p",{children:[e.jsx("strong",{style:{color:"var(--text-primary)"},children:"INLINE:"})," 원천 DB는 ",e.jsx("code",{children:"subitem_id"}),"로 측정 위치를 구분하고 ",e.jsx("code",{children:"shot_x/shot_y"}),"는 없습니다. 기본 집계는 ",e.jsx("code",{children:"lot_wf"})," 기준 평균이며, item별 mapping table로 ET shot 좌표를 만든 경우에만 shot 단위로 매칭합니다."]}),e.jsx(t,{children:`INLINE required:
- product, root_lot_id, wafer_id, lot_wf
- step or step_id
- item or item_id
- value
- measure_time

default:
group by product, root_lot_id, wafer_id, lot_wf, step, item
value = avg(value)`}),e.jsxs("p",{children:[e.jsx("strong",{style:{color:"var(--text-primary)"},children:"ET:"})," 기본 집계는 ",e.jsx("code",{children:"lot_wf"})," 기준 median입니다."]}),e.jsx(t,{children:`ET required:
- product, root_lot_id or lot_id/fab_lot_id
- wafer_id, lot_wf
- step_id
- item_id
- value
- measure_time

default:
group by product, root_lot_id, wafer_id, lot_wf, step_id, item_id
value = median(value)`}),e.jsxs("p",{children:[e.jsx("strong",{style:{color:"var(--text-primary)"},children:"ML_TABLE_제품:"})," 4000열 이상 wide table을 전제로 합니다. 전체 collect 금지, schema catalog를 먼저 보여주고 선택된 컬럼만 읽습니다."]}),e.jsx(t,{children:`ML_TABLE required:
- product, root_lot_id, wafer_id, lot_wf
- fab_lot_id optional
- KNOB_* columns
- target/metric columns

guard:
- default selected columns <= 100
- preview rows <= configured query budget
- product/date/lot/filter 없이 broad scan 금지`}),e.jsx("p",{children:e.jsx("strong",{style:{color:"var(--text-primary)"},children:"매칭 우선순위:"})}),e.jsx(t,{children:`1. root_lot_id + wafer_id + shot_id
2. root_lot_id + wafer_id + die_x + die_y
3. root_lot_id + wafer_id + site/field/reticle
4. lot_wf = root_lot_id + "_" + wafer_id

결과에는 항상 join key, left/right row 수, matched row 수,
null/drop 비율을 표시합니다.`}),e.jsxs("p",{children:[e.jsx("strong",{style:{color:"var(--text-primary)"},children:"반도체 기본 metric dictionary:"})," DIBL, Rch, DC, Rs, Rc, LKG, Short, Vth/VT, Ion, Ioff, Idsat, Ilin, BV, CD, Overlay, Thickness, Resistance, Contact, Defect. 이 사전은 후보 검색용이며 실제 데이터 확정은 DB schema와 사용자 선택으로만 합니다."]}),e.jsx(i,{id:"flowi",children:"Flow-i 규칙"}),e.jsxs("p",{children:["Flow-i는 자유 실행 agent가 아니라 등록된 단위기능을 고르는 입구입니다. LLM은 JSON 계획과 설명을 만들 수 있지만 실제 실행은 백엔드 단위기능이 검증합니다. 에이전트 탭은 ",e.jsx("strong",{style:{color:"var(--text-primary)"},children:"기능 카탈로그 / 실행 추적 / Workflow 템플릿"})," 소탭으로 재편되었고, Semantic 레이어와 LLM 설정은 관리자 탭으로 이관되었습니다."]}),e.jsx(t,{children:`Flow-i pipeline:
1. prompt 수신
2. feature/action 후보 선택 (기능 카탈로그)
3. role, tab permission, query budget 검사
4. 애매하면 1/2/3 선택지로 질문
5. schema catalog로 실제 컬럼 존재 확인
6. 단위기능 실행 (app_v2/modules/agent_runtime executor)
7. 기존 화면 renderer와 같은 표/차트 결과 반환
8. user memory/activity log 기록 (실행 추적 소탭에서 확인)`}),e.jsxs("p",{children:[e.jsx("strong",{style:{color:"var(--text-primary)"},children:"권한:"})," 일반 user는 조회/요약/차트만 가능합니다. Admin 파일 조작은 별도 admin-only 단위기능, diff, 확인, audit log, soft-delete를 거쳐야 합니다."]}),e.jsxs("p",{children:[e.jsx("strong",{style:{color:"var(--text-primary)"},children:"관련 백엔드:"})," ",e.jsx("code",{children:"core/home_orchestrator.py"}),"(오케스트레이터), ",e.jsx("code",{children:"core/llm_adapter.py"}),"(LLM 어댑터), ",e.jsx("code",{children:"core/agent_tool_contract.py"}),"·",e.jsx("code",{children:"core/tool_registry.py"}),"(도구 계약), ",e.jsx("code",{children:"app_v2/modules/semantic_lexicon"}),"·",e.jsx("code",{children:"semantic_learning"}),"(시맨틱 레이어/학습)."]}),e.jsx(i,{id:"perf",children:"대용량 운영"}),e.jsx("p",{children:"INLINE/ET 50~100GB, ML_TABLE wide table을 기준으로 설계합니다. 원본 Parquet는 유지하고, DuckDB/Polars lazy scan, cache, index table을 붙여 broad scan을 피합니다."}),e.jsx(t,{children:`source of truth:
- Parquet files

fast path:
- DuckDB view/index for SQL preview/filter
- splittable match_cache for root_lot_id -> latest fab_lot_id
- SplitTable canonical index layer (쓰기 시점 canonical 레이아웃)
- 파생 캐시 증분(델타) 재빌드, fab_lot_index 증분 병합
- /view 셀 슬림 포맷 (cells_format v2)
- schema catalog for wide ML_TABLE columns

query budget:
- product/date/lot/filter 없는 원본 전체 scan 금지
- heavy query concurrency: 1 기본, heavy 빌드 직렬화
- cache builder concurrency: 1
- 뷰캐시 바이트 예산 (메모리 압력 시 청크 축소)
- table result paging, preview row/column cap
- atomic cache build: temp file -> replace`}),e.jsxs("p",{children:[e.jsx("strong",{style:{color:"var(--text-primary)"},children:"stale-while-revalidate:"})," SplitTable /view는 캐시를 먼저 서빙하고, 전역 ",e.jsx("strong",{style:{color:"var(--text-primary)"},children:"단일 revalidate 데몬 워커"}),"가 백그라운드로 재검증합니다. root/product/fab_lot_index rebuild는 single-flight + cooldown 가드로 폭주를 막습니다 (모두 ",e.jsx("code",{children:"routers/splittable.py"}),", 빌드 실행은 ",e.jsx("code",{children:"app_v2/modules/splittable/cache_builder.py"}),")."]}),e.jsxs("p",{children:[e.jsx("strong",{style:{color:"var(--text-primary)"},children:"리소스 가드:"})," ",e.jsx("code",{children:"app_v2/runtime/resource_guard.py"})," 미들웨어가 메모리 압력 시 heavy 요청을 거절합니다. 프로파일은 ",e.jsx("code",{children:"FLOW_RESOURCE_PROFILE"}),"(small/full), 소형 호스트(16GB)는 ",e.jsx("code",{children:"FLOW_PROCESS_MEMORY_LIMIT_STRICT=0"}),"으로 Polars RSS 잔류 오탐을 피합니다 (start_flow.bat 참고). Polars/DuckDB thread는 ",e.jsx("code",{children:"core/runtime_limits.py"}),"가 기동 시 제한합니다."]}),e.jsx(i,{id:"ux",children:"UX 시스템"}),e.jsxs("p",{children:["Flow는 작업용 도구이므로 페이지마다 같은 밀도, 글씨, 색, 표 스타일을 사용합니다. 공통 컴포넌트는 ",e.jsx("code",{children:"frontend/src/components/UXKit.jsx"}),"를 우선 사용합니다."]}),e.jsx(t,{children:`UX rules:
- no blank page: cached data 또는 skeleton 먼저 표시
- long job: job id/progress/cancel 제공
- stale-while-revalidate: 기존 결과 먼저 표시 후 갱신
- loading/empty/error 상태 문구 통일
- table/filter/chart 색상 팔레트 통일
- card radius <= 8px
- dashboard/splittable/inform/tracker/admin spacing 통일`}),e.jsx(i,{id:"add",children:"새 기능 추가"}),e.jsxs("p",{children:[e.jsx("strong",{style:{color:"var(--text-primary)"},children:"1단계."})," 백엔드 라우터 생성 — 저장만 하면 서버 재시작 시 자동 로드됩니다. 라우터는 HTTP shape와 권한 확인만 담당하고, 업무 로직은 ",e.jsx("code",{children:"backend/app_v2/modules/<feature>"}),"에 domain/repository/service로 둡니다. JSON 저장은 ",e.jsx("code",{children:"app_v2.shared.json_store.JsonFileStore"}),"를 우선 사용합니다."]}),e.jsx(t,{children:`# backend/routers/myfeature.py
from fastapi import APIRouter

router = APIRouter(prefix="/api/myfeature", tags=["myfeature"])

@router.get("/items")
def get_items():
    return {"items": [...]}`}),e.jsxs("p",{children:[e.jsx("strong",{style:{color:"var(--text-primary)"},children:"2단계."})," 프론트 페이지 생성. API 호출은 ",e.jsx("code",{children:"fetch()"})," 직접 호출 대신 ",e.jsx("code",{children:"src/lib/api.js"})," helper를 사용합니다."]}),e.jsx(t,{children:`// frontend/src/pages/My_MyFeature.jsx
export default function My_MyFeature() {
  return <div>MyFeature</div>;
}`}),e.jsxs("p",{children:[e.jsx("strong",{style:{color:"var(--text-primary)"},children:"3단계."})," 탭 등록 — ",e.jsx("code",{children:"config.js"}),"와 ",e.jsx("code",{children:"pageRegistry.jsx"})," 두 곳을 함께 수정합니다 (App.jsx는 건드리지 않음):"]}),e.jsx(t,{children:`// frontend/src/config.js — 네비 탭 메타
export const TABS = [
  ...
  {key:"myfeature", label:"내 기능", icon:"🧩", group:"tool"},
];

// frontend/src/app/pageRegistry.jsx — lazy 페이지 매핑
export const PAGE_MAP = {
  ...
  myfeature: lazy(() => import("../pages/My_MyFeature")),
};`}),e.jsxs("p",{children:[e.jsx("strong",{style:{color:"var(--text-primary)"},children:"4단계."})," 빌드 + 검증:"]}),e.jsx(t,{children:`cd frontend && npm run build
python scripts/smoke_test.py      # 서버 떠 있는 상태
python -m pytest tests            # 백엔드 단위 테스트`}),e.jsxs("p",{style:{marginTop:12},children:["TABS에 등록되면 네비게이션은 자동으로 나타나고, PAGE_MAP에 없으면 Coming Soon이 표시됩니다. 소탭 권한이 필요하면 ",e.jsx("code",{children:"SUB_TABS"}),"와 백엔드 ",e.jsx("code",{children:"core/auth.py TAB_SUBTABS"}),"를 함께 수정합니다."]}),e.jsx(i,{id:"update",children:"업데이트/배포"}),e.jsxs("p",{children:["배포는 저장소 전체를 담은 단일 ",e.jsx("code",{children:"setup.py"})," 설치 파일로 합니다:"]}),e.jsx(t,{children:`# 1. 설치 파일 재생성 (main 체크아웃에서, worktree 금지)
python _build_setup.py            # → setup.py 덮어씀

# 2. 버전 확인 후 diff까지 커밋
python setup.py version

# 3. GitHub main 푸시 (docs/GITHUB_MAIN_PUSH.md 절차)
push_flow.bat                     # git push origin main

# 4. 대상 서버에서 설치
python setup.py                   # 소스 추출 + npm install/build
python setup.py restore [latest]  # ~/.flow_backups 스냅샷 복원`}),e.jsxs("p",{children:[e.jsx("strong",{style:{color:"var(--text-primary)"},children:"안전 규칙:"})," setup.py는 소스 파일만 포함하며 ",e.jsx("code",{children:"data/"}),", ",e.jsx("code",{children:"FLOW_DATA_ROOT"}),", ",e.jsx("code",{children:"FLOW_DB_ROOT"}),", ",e.jsx("code",{children:"FLOW_WAFER_MAP_ROOT"})," 아래 runtime 데이터를 절대 덮어쓰지 않습니다. 추출 전 소형 config/state 파일을 ",e.jsx("code",{children:"~/.flow_backups"}),"에 스냅샷합니다. 사내 반입 전후에는 ",e.jsx("code",{children:"python scripts/preflight_internal.py --write-probe"}),"로 root 보존을 확인합니다. 버전 메타는 ",e.jsx("code",{children:"VERSION.json"}),"에서 관리합니다."]}),e.jsx(i,{id:"theme",children:"테마 시스템"}),e.jsxs("p",{children:["CSS 변수 기반 다크/라이트 테마. 전환 상태는 ",e.jsx("code",{children:"app/useFlowShell.js"}),"가 관리합니다."]}),e.jsx(t,{children:`/* 사용 가능한 CSS 변수 */
var(--bg-primary)      /* 메인 배경 */
var(--bg-secondary)    /* 카드/패널 배경 */
var(--bg-card)         /* 카드 배경 */
var(--bg-hover)        /* 호버 상태 */
var(--bg-tertiary)     /* 테이블 헤더 등 */
var(--text-primary)    /* 주요 텍스트 */
var(--text-secondary)  /* 보조 텍스트 */
var(--border)          /* 테두리 */
var(--accent)          /* 강조색 (오렌지) */
var(--accent-dim)      /* 강조색 어두운 */
var(--accent-glow)     /* 강조색 글로우 */

/* JSX에서 사용 예시 */
style={{ color: "var(--accent)", background: "var(--bg-card)" }}`}),e.jsx(i,{id:"infra",children:"인프라"}),e.jsx("p",{children:e.jsx("strong",{style:{color:"var(--text-primary)"},children:"경로 관리 (core/paths.py, core/roots.py):"})}),e.jsx(t,{children:`from core.paths import PATHS

PATHS.db_root      # FLOW_DB_ROOT → profile → shared DB → data/Fab
PATHS.data_root    # FLOW_DATA_ROOT → ... → data/flow-data

# 환경변수로 오버라이드
FLOW_DB_ROOT=/other/path uvicorn app:app
FLOW_DATA_ROOT=/other/state uvicorn app:app

# resolver 우회 경로 하드코딩 금지`}),e.jsx("p",{children:e.jsx("strong",{style:{color:"var(--text-primary)"},children:"알림 시스템 (core/notify.py):"})}),e.jsx(t,{children:`from core.notify import send_notify, send_to_admins

send_notify("username", "Title", "Body", type="info")   # 특정 유저
send_to_admins("New Alert", "...", type="approval")     # 모든 admin

# type: info | warning | approval | message`}),e.jsx("p",{children:e.jsx("strong",{style:{color:"var(--text-primary)"},children:"검증 명령:"})}),e.jsx(t,{children:`python scripts/smoke_test.py                       # 핵심 smoke (서버 필요)
python -m pytest tests                             # 백엔드 단위 테스트
python scripts/preflight_internal.py --write-probe # 사내 반입/업데이트 전
python scripts/empty_root_smoke.py                 # 빈 데이터 root 부팅`}),e.jsx("p",{children:e.jsx("strong",{style:{color:"var(--text-primary)"},children:"하트비트 (서버 유지):"})}),e.jsx(t,{children:`# crontab에 등록 (매 30분)
*/30 * * * * curl -X POST http://localhost:8080/api/monitor/heartbeat`}),e.jsxs("p",{style:{marginTop:24,padding:"12px 16px",borderRadius:8,background:"var(--bg-card,#2a2a2a)",border:"1px solid var(--border,#333)",fontSize:14},children:[e.jsx("strong",{style:{color:"var(--accent)"},children:"포트:"})," 8080  | ",e.jsx("strong",{style:{color:"var(--accent)"},children:"기본 admin:"})," hol / hol12345!  | ",e.jsx("strong",{style:{color:"var(--accent)"},children:"서버 실행:"})," 프로젝트 루트에서 uvicorn app:app --host 0.0.0.0 --port 8080 (또는 start_flow.bat)"]})]})]})}export{m as default};
