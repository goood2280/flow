import { useState } from "react";

const mono = "'JetBrains Mono',monospace";

function Code({ children }) {
  return <pre style={{ background:"#111", borderRadius:8, padding:"14px 18px", border:"1px solid var(--border,#333)", overflow:"auto", fontFamily:mono, fontSize:14, lineHeight:1.8, color:"#e5e5e5", whiteSpace:"pre", margin:"12px 0" }}>{children}</pre>;
}

function H2({ children, id }) {
  return <h2 id={id} style={{ fontSize:16, fontWeight:700, color:"var(--accent,#f97316)", marginTop:36, marginBottom:14, paddingBottom:6, borderBottom:"1px solid var(--border,#333)", fontFamily:mono }}><span style={{color:"var(--text-secondary)"}}>{">"} </span>{children}</h2>;
}

function ApiRow({ method, path, desc }) {
  const c = {GET:"#22c55e",POST:"#f97316",DELETE:"#ef4444"};
  return (
    <div style={{ display:"flex", alignItems:"center", gap:10, padding:"8px 0", borderBottom:"1px solid var(--border,#222)", fontSize:14 }}>
      <span style={{ fontFamily:mono, fontSize:14, fontWeight:700, padding:"2px 8px", borderRadius:4, minWidth:44, textAlign:"center", background:(c[method]||"#666")+"22", color:c[method]||"#666" }}>{method}</span>
      <span style={{ fontFamily:mono, color:"var(--text-primary)", minWidth:300 }}>{path}</span>
      <span style={{ color:"var(--text-secondary)" }}>{desc}</span>
    </div>
  );
}

function RouterRow({ file, prefix, desc }) {
  return (
    <div style={{ display:"flex", alignItems:"center", gap:10, padding:"7px 0", borderBottom:"1px solid var(--border,#222)", fontSize:14 }}>
      <span style={{ fontFamily:mono, color:"var(--text-primary)", minWidth:190 }}>{file}</span>
      <span style={{ fontFamily:mono, color:"var(--accent,#f97316)", minWidth:200 }}>{prefix}</span>
      <span style={{ color:"var(--text-secondary)" }}>{desc}</span>
    </div>
  );
}

const NAV = [
  { id:"arch", label:"아키텍처" },
  { id:"files", label:"파일 구조" },
  { id:"tabs", label:"탭 구성" },
  { id:"api", label:"API 레퍼런스" },
  { id:"db", label:"데이터 루트/DB" },
  { id:"schema", label:"표준 스키마" },
  { id:"flowi", label:"Flow-i 규칙" },
  { id:"perf", label:"대용량 운영" },
  { id:"ux", label:"UX 시스템" },
  { id:"add", label:"기능 추가" },
  { id:"update", label:"업데이트/배포" },
  { id:"theme", label:"테마 시스템" },
  { id:"infra", label:"인프라" },
];

export default function My_DevGuide() {
  const [active, setActive] = useState("arch");

  const scrollTo = (id) => { setActive(id); document.getElementById(id)?.scrollIntoView({behavior:"smooth",block:"start"}); };

  return (
    <div style={{ display:"flex", minHeight:"calc(100vh - 52px)", background:"var(--bg-primary,#1a1a1a)", color:"var(--text-primary,#e5e5e5)", fontFamily:"'Pretendard',sans-serif" }}>

      {/* Side Nav */}
      <div style={{ width:190, padding:"20px 10px", borderRight:"1px solid var(--border,#333)", position:"sticky", top:52, height:"calc(100vh - 52px)", overflowY:"auto", flexShrink:0 }}>
        <div style={{ fontSize:14, fontWeight:700, color:"var(--accent,#f97316)", textTransform:"uppercase", letterSpacing:"0.08em", marginBottom:12, paddingLeft:10, fontFamily:mono }}>{">"} 개발자_가이드</div>
        {NAV.map(n => (
          <div key={n.id} onClick={() => scrollTo(n.id)}
            style={{ padding:"6px 10px", borderRadius:5, cursor:"pointer", fontSize:14, marginBottom:1, fontFamily:mono,
              background: active===n.id ? "var(--accent-glow,#f9731622)" : "transparent",
              color: active===n.id ? "var(--accent,#f97316)" : "var(--text-secondary,#a3a3a3)",
              fontWeight: active===n.id ? 600 : 400 }}>{n.label}</div>
        ))}
      </div>

      {/* Content */}
      <div style={{ flex:1, padding:"28px 36px", maxWidth:860, overflow:"auto", lineHeight:1.8, fontSize:14, color:"var(--text-secondary,#a3a3a3)" }}>

        <H2 id="arch">아키텍처</H2>
        <p>flow = <strong style={{color:"var(--text-primary)"}}>FastAPI</strong> (백엔드) + <strong style={{color:"var(--text-primary)"}}>React + Vite</strong> (프론트엔드) + <strong style={{color:"var(--text-primary)"}}>Polars/DuckDB/Parquet</strong> (데이터)</p>
        <Code>{`[Browser] ──HTTP──> [FastAPI :8080]
                        ├── /api/*               → backend/routers/*.py (auto-loaded)
                        ├── /version.json        → 버전 표시 (mtime 기반)
                        ├── /runtime-roots.json  → root 해석 진단 (비인증)
                        └── /*                   → frontend/dist/ (SPA)

FastAPI startup (backend/app.py):
  runtime_limits 적용 (thread/memory cap)
    → AuthMiddleware + ResourceGuardMiddleware
    → include_router_modules()      # routers/ 동적 로드
    → start_background_services()   # scheduler, cache sweeper 등
    → ensure_seed_admin()`}</Code>

        <p><strong style={{color:"var(--text-primary)"}}>진입점:</strong> 루트 <code>app.py</code>는 uvicorn shim이고 실제 앱 조립은 <code>backend/app.py</code>가 담당합니다. 루트의 <code>core/</code>, <code>app_v2/</code>, <code>routers/</code> 폴더는 <code>backend/</code> 하위 실코드로 위임하는 import 호환 shim입니다.</p>
        <p><strong style={{color:"var(--text-primary)"}}>동적 라우터 로딩:</strong> <code>backend/routers/</code>에 .py 파일을 넣으면 <code>app_v2/runtime/router_loader.py</code>가 자동으로 등록합니다.</p>

        <H2 id="files">파일 구조</H2>
        <Code>{`flow/
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
    └── flow-data/           # local 사용자/운영 상태`}</Code>

        <H2 id="tabs">탭 구성</H2>
        <p>탭 메타는 <code>frontend/src/config.js</code>의 <code>TABS</code>, 페이지 연결은 <code>app/pageRegistry.jsx</code>의 <code>PAGE_MAP</code>에서 관리합니다. 소탭 권한은 <code>SUB_TABS</code>와 백엔드 <code>core/auth.py</code>의 <code>TAB_SUBTABS</code>를 동기로 유지합니다.</p>
        <Code>{`main   : home(홈)
data   : filebrowser(파일탐색기)  dashboard(대시보드)
         splittable(스플릿 테이블)
tool   : diagnosis(에이전트)  tracker(이슈 추적)  valve(매칭알람)
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

* tablemap 페이지는 PAGE_MAP에는 등록되어 있으나 네비 탭에는 없음.`}</Code>

        <H2 id="api">API 레퍼런스</H2>
        <p>보안상 Swagger UI는 비활성화되어 있습니다. 모든 <code>/api/*</code>는 AuthMiddleware의 세션 토큰 인증을 통과해야 하며, admin API는 <code>require_admin</code> 경계를 유지합니다. 정확한 shape는 각 라우터와 smoke test를 기준으로 확인하세요.</p>

        <div style={{fontSize:14,fontWeight:700,color:"var(--accent)",marginTop:20,marginBottom:8,fontFamily:mono}}>라우터 카탈로그 (backend/routers/)</div>
        <RouterRow file="auth.py" prefix="/api/auth" desc="로그인/로그아웃/가입/비번 변경·리셋" />
        <RouterRow file="admin.py" prefix="/api/admin" desc="유저관리/알림/백업/관리 설정" />
        <RouterRow file="groups.py" prefix="/api/groups" desc="그룹 가시성" />
        <RouterRow file="mail_groups.py" prefix="/api/mail-groups" desc="메일 그룹" />
        <RouterRow file="messages.py" prefix="/api/messages" desc="유저 간 메시지" />
        <RouterRow file="session_api.py" prefix="/api/session" desc="유저 세션 저장/복원" />
        <RouterRow file="monitor.py" prefix="/api/monitor, /api/system" desc="시스템/자원 모니터, heartbeat" />
        <RouterRow file="filebrowser.py" prefix="/api/filebrowser" desc="Parquet/CSV 탐색 + SQL 필터" />
        <RouterRow file="dashboard.py" prefix="/api/dashboard" desc="대시보드 차트/스냅샷" />
        <RouterRow file="splittable.py" prefix="/api/splittable" desc="SplitTable view/캐시/rulebook/notes" />
        <RouterRow file="informs(_extra).py" prefix="/api/informs" desc="인폼 로그/매트릭스/메일" />
        <RouterRow file="tracker.py" prefix="/api/tracker" desc="이슈 추적, 카테고리 관리" />
        <RouterRow file="meetings.py" prefix="/api/meetings" desc="회의관리, 메일/캘린더 연동" />
        <RouterRow file="calendar.py" prefix="/api/calendar" desc="변경점 관리" />
        <RouterRow file="valve_alerts.py" prefix="/api/valve-alerts" desc="매칭알람" />
        <RouterRow file="teg_map.py" prefix="/api/teg-map" desc="TEG 위치 조회, Mapfile 체크" />
        <RouterRow file="et_time.py" prefix="/api/et-time" desc="ET 측정시간" />
        <RouterRow file="reformatize.py" prefix="/api/reformatize" desc="리포마타이즈 (vehicle reformatter)" />
        <RouterRow file="reformatter.py" prefix="/api/reformatter" desc="리포맷터" />
        <RouterRow file="sql_workspace.py" prefix="/api/sql-workspace" desc="SQL 작업대" />
        <RouterRow file="lot_progress.py" prefix="/api/lot-progress" desc="lot 진행 조회/캐시" />
        <RouterRow file="ml.py" prefix="/api/ml" desc="ML_TABLE 조회" />
        <RouterRow file="dbmap.py" prefix="/api/dbmap" desc="DB 매핑" />
        <RouterRow file="catalog.py" prefix="/api/catalog" desc="스키마/소스 카탈로그" />
        <RouterRow file="knowledge.py" prefix="/api/knowledge" desc="지식/위키/그래프" />
        <RouterRow file="semiconductor.py" prefix="/api/..." desc="반도체 진단(RCA)" />
        <RouterRow file="llm.py" prefix="/api/llm" desc="LLM 설정/어댑터" />
        <RouterRow file="agent.py" prefix="/api/agent" desc="에이전트 실행/시맨틱 레이어" />
        <RouterRow file="home.py, home_agent.py" prefix="/api/home, /api/home-agent" desc="홈 화면, 홈 오케스트레이터" />
        <RouterRow file="flowi_learning.py" prefix="/api/flowi-learning" desc="Flow-i 학습/피드백" />
        <RouterRow file="skills.py" prefix="/api/skills" desc="스킬 카탈로그" />
        <RouterRow file="s3_ingest.py" prefix="/api/s3ingest" desc="S3 인제스트/동기화" />
        <RouterRow file="aipd_bridge.py" prefix="—" desc="aipd 연동 브리지" />

        <div style={{fontSize:14,fontWeight:700,color:"var(--accent)",marginTop:20,marginBottom:8,fontFamily:mono}}>핵심 엔드포인트 예시</div>
        <ApiRow method="POST" path="/api/auth/login" desc="로그인 → 세션 토큰" />
        <ApiRow method="POST" path="/api/auth/register" desc="회원가입 → admin 승인 대기" />
        <ApiRow method="GET" path="/api/auth/me" desc="현재 사용자 확인" />
        <ApiRow method="POST" path="/api/session/save" desc="유저 세션 저장 (탭, 폼 데이터)" />
        <ApiRow method="GET" path="/api/session/load" desc="유저 세션 복원" />
        <ApiRow method="GET" path="/api/monitor/system" desc="CPU/메모리/디스크 사용량" />
        <ApiRow method="POST" path="/api/monitor/heartbeat" desc="서버 유지용 heartbeat (cron)" />

        <H2 id="db">데이터 루트/DB</H2>
        <p>데이터 루트는 <code>backend/core/roots.py</code>와 <code>core/paths.py</code>의 resolver를 통해서만 해석합니다. 특정 DB 경로를 하드코딩하지 않습니다.</p>
        <Code>{`db_root 우선순위:
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
* 현재 해석 결과는 /runtime-roots.json 에서 확인.`}</Code>

        <Code>{`# Hive Partition 규칙
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
    pl.sql_expr("item_id = 'VTH' AND et_value > 0.5"))     # SQL 필터`}</Code>

        <p><strong style={{color:"var(--text-primary)"}}>DuckDB:</strong> parquet/csv 원본 위의 in-memory read-only query engine으로만 사용합니다 (<code>core/duckdb_engine.py</code>). 원본 DB 파일을 수정하거나 DuckDB database 파일로 변환하지 않습니다.</p>

        <H2 id="schema">표준 스키마</H2>
        <p>Flow의 분석 기능과 Flow-i는 아래 contract를 기준으로만 데이터를 연결합니다. 컬럼명은 schema catalog에서 실제 존재 여부를 확인한 뒤 사용하며, 없는 컬럼은 추측하지 않습니다.</p>
        <Code>{`# 공통 식별자
product        # 제품명 또는 ML_TABLE product
root_lot_id    # root lot
fab_lot_id     # 최신 FAB lot, splittable match_cache 에서 우선 조회
wafer_id       # wafer 식별자
lot_wf         # root_lot_id + "_" + wafer_id
shot_id        # optional, 있으면 lot_wf 보다 우선
die_x, die_y   # optional, shot_id 없을 때 shot/die 매칭 후보
measure_time   # 측정 시각 또는 source update time`}</Code>

        <p><strong style={{color:"var(--text-primary)"}}>INLINE:</strong> 원천 DB는 <code>subitem_id</code>로 측정 위치를 구분하고 <code>shot_x/shot_y</code>는 없습니다. 기본 집계는 <code>lot_wf</code> 기준 평균이며, item별 mapping table로 ET shot 좌표를 만든 경우에만 shot 단위로 매칭합니다.</p>
        <Code>{`INLINE required:
- product, root_lot_id, wafer_id, lot_wf
- step or step_id
- item or item_id
- value
- measure_time

default:
group by product, root_lot_id, wafer_id, lot_wf, step, item
value = avg(value)`}</Code>

        <p><strong style={{color:"var(--text-primary)"}}>ET:</strong> 기본 집계는 <code>lot_wf</code> 기준 median입니다.</p>
        <Code>{`ET required:
- product, root_lot_id or lot_id/fab_lot_id
- wafer_id, lot_wf
- step_id
- item_id
- value
- measure_time

default:
group by product, root_lot_id, wafer_id, lot_wf, step_id, item_id
value = median(value)`}</Code>

        <p><strong style={{color:"var(--text-primary)"}}>ML_TABLE_제품:</strong> 4000열 이상 wide table을 전제로 합니다. 전체 collect 금지, schema catalog를 먼저 보여주고 선택된 컬럼만 읽습니다.</p>
        <Code>{`ML_TABLE required:
- product, root_lot_id, wafer_id, lot_wf
- fab_lot_id optional
- KNOB_* columns
- target/metric columns

guard:
- default selected columns <= 100
- preview rows <= configured query budget
- product/date/lot/filter 없이 broad scan 금지`}</Code>

        <p><strong style={{color:"var(--text-primary)"}}>매칭 우선순위:</strong></p>
        <Code>{`1. root_lot_id + wafer_id + shot_id
2. root_lot_id + wafer_id + die_x + die_y
3. root_lot_id + wafer_id + site/field/reticle
4. lot_wf = root_lot_id + "_" + wafer_id

결과에는 항상 join key, left/right row 수, matched row 수,
null/drop 비율을 표시합니다.`}</Code>

        <p><strong style={{color:"var(--text-primary)"}}>반도체 기본 metric dictionary:</strong> DIBL, Rch, DC, Rs, Rc, LKG, Short, Vth/VT, Ion, Ioff, Idsat, Ilin, BV, CD, Overlay, Thickness, Resistance, Contact, Defect. 이 사전은 후보 검색용이며 실제 데이터 확정은 DB schema와 사용자 선택으로만 합니다.</p>

        <H2 id="flowi">Flow-i 규칙</H2>
        <p>Flow-i는 자유 실행 agent가 아니라 등록된 단위기능을 고르는 입구입니다. LLM은 JSON 계획과 설명을 만들 수 있지만 실제 실행은 백엔드 단위기능이 검증합니다. 에이전트 탭은 <strong style={{color:"var(--text-primary)"}}>기능 카탈로그 / 실행 추적 / Workflow 템플릿</strong> 소탭으로 재편되었고, Semantic 레이어와 LLM 설정은 관리자 탭으로 이관되었습니다.</p>
        <Code>{`Flow-i pipeline:
1. prompt 수신
2. feature/action 후보 선택 (기능 카탈로그)
3. role, tab permission, query budget 검사
4. 애매하면 1/2/3 선택지로 질문
5. schema catalog로 실제 컬럼 존재 확인
6. 단위기능 실행 (app_v2/modules/agent_runtime executor)
7. 기존 화면 renderer와 같은 표/차트 결과 반환
8. user memory/activity log 기록 (실행 추적 소탭에서 확인)`}</Code>

        <p><strong style={{color:"var(--text-primary)"}}>권한:</strong> 일반 user는 조회/요약/차트만 가능합니다. Admin 파일 조작은 별도 admin-only 단위기능, diff, 확인, audit log, soft-delete를 거쳐야 합니다.</p>
        <p><strong style={{color:"var(--text-primary)"}}>관련 백엔드:</strong> <code>core/home_orchestrator.py</code>(오케스트레이터), <code>core/llm_adapter.py</code>(LLM 어댑터), <code>core/agent_tool_contract.py</code>·<code>core/tool_registry.py</code>(도구 계약), <code>app_v2/modules/semantic_lexicon</code>·<code>semantic_learning</code>(시맨틱 레이어/학습).</p>

        <H2 id="perf">대용량 운영</H2>
        <p>INLINE/ET 50~100GB, ML_TABLE wide table을 기준으로 설계합니다. 원본 Parquet는 유지하고, DuckDB/Polars lazy scan, cache, index table을 붙여 broad scan을 피합니다.</p>
        <Code>{`source of truth:
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
- atomic cache build: temp file -> replace`}</Code>

        <p><strong style={{color:"var(--text-primary)"}}>stale-while-revalidate:</strong> SplitTable /view는 캐시를 먼저 서빙하고, 전역 <strong style={{color:"var(--text-primary)"}}>단일 revalidate 데몬 워커</strong>가 백그라운드로 재검증합니다. root/product/fab_lot_index rebuild는 single-flight + cooldown 가드로 폭주를 막습니다 (모두 <code>routers/splittable.py</code>, 빌드 실행은 <code>app_v2/modules/splittable/cache_builder.py</code>).</p>
        <p><strong style={{color:"var(--text-primary)"}}>리소스 가드:</strong> <code>app_v2/runtime/resource_guard.py</code> 미들웨어가 메모리 압력 시 heavy 요청을 거절합니다. 프로파일은 <code>FLOW_RESOURCE_PROFILE</code>(small/full), 소형 호스트(16GB)는 <code>FLOW_PROCESS_MEMORY_LIMIT_STRICT=0</code>으로 Polars RSS 잔류 오탐을 피합니다 (start_flow.bat 참고). Polars/DuckDB thread는 <code>core/runtime_limits.py</code>가 기동 시 제한합니다.</p>

        <H2 id="ux">UX 시스템</H2>
        <p>Flow는 작업용 도구이므로 페이지마다 같은 밀도, 글씨, 색, 표 스타일을 사용합니다. 공통 컴포넌트는 <code>frontend/src/components/UXKit.jsx</code>를 우선 사용합니다.</p>
        <Code>{`UX rules:
- no blank page: cached data 또는 skeleton 먼저 표시
- long job: job id/progress/cancel 제공
- stale-while-revalidate: 기존 결과 먼저 표시 후 갱신
- loading/empty/error 상태 문구 통일
- table/filter/chart 색상 팔레트 통일
- card radius <= 8px
- dashboard/splittable/inform/tracker/admin spacing 통일`}</Code>

        <H2 id="add">새 기능 추가</H2>
        <p><strong style={{color:"var(--text-primary)"}}>1단계.</strong> 백엔드 라우터 생성 — 저장만 하면 서버 재시작 시 자동 로드됩니다. 라우터는 HTTP shape와 권한 확인만 담당하고, 업무 로직은 <code>backend/app_v2/modules/&lt;feature&gt;</code>에 domain/repository/service로 둡니다. JSON 저장은 <code>app_v2.shared.json_store.JsonFileStore</code>를 우선 사용합니다.</p>
        <Code>{`# backend/routers/myfeature.py
from fastapi import APIRouter

router = APIRouter(prefix="/api/myfeature", tags=["myfeature"])

@router.get("/items")
def get_items():
    return {"items": [...]}`}</Code>

        <p><strong style={{color:"var(--text-primary)"}}>2단계.</strong> 프론트 페이지 생성. API 호출은 <code>fetch()</code> 직접 호출 대신 <code>src/lib/api.js</code> helper를 사용합니다.</p>
        <Code>{`// frontend/src/pages/My_MyFeature.jsx
export default function My_MyFeature() {
  return <div>MyFeature</div>;
}`}</Code>

        <p><strong style={{color:"var(--text-primary)"}}>3단계.</strong> 탭 등록 — <code>config.js</code>와 <code>pageRegistry.jsx</code> 두 곳을 함께 수정합니다 (App.jsx는 건드리지 않음):</p>
        <Code>{`// frontend/src/config.js — 네비 탭 메타
export const TABS = [
  ...
  {key:"myfeature", label:"내 기능", icon:"🧩", group:"tool"},
];

// frontend/src/app/pageRegistry.jsx — lazy 페이지 매핑
export const PAGE_MAP = {
  ...
  myfeature: lazy(() => import("../pages/My_MyFeature")),
};`}</Code>

        <p><strong style={{color:"var(--text-primary)"}}>4단계.</strong> 빌드 + 검증:</p>
        <Code>{`cd frontend && npm run build
python scripts/smoke_test.py      # 서버 떠 있는 상태
python -m pytest tests            # 백엔드 단위 테스트`}</Code>

        <p style={{marginTop:12}}>TABS에 등록되면 네비게이션은 자동으로 나타나고, PAGE_MAP에 없으면 Coming Soon이 표시됩니다. 소탭 권한이 필요하면 <code>SUB_TABS</code>와 백엔드 <code>core/auth.py TAB_SUBTABS</code>를 함께 수정합니다.</p>

        <H2 id="update">업데이트/배포</H2>
        <p>배포는 저장소 전체를 담은 단일 <code>setup.py</code> 설치 파일로 합니다:</p>
        <Code>{`# 1. 설치 파일 재생성 (main 체크아웃에서, worktree 금지)
python _build_setup.py            # → setup.py 덮어씀

# 2. 버전 확인 후 diff까지 커밋
python setup.py version

# 3. GitHub main 푸시 (docs/GITHUB_MAIN_PUSH.md 절차)
push_flow.bat                     # git push origin main

# 4. 대상 서버에서 설치
python setup.py                   # 소스 추출 + npm install/build
python setup.py restore [latest]  # ~/.flow_backups 스냅샷 복원`}</Code>
        <p><strong style={{color:"var(--text-primary)"}}>안전 규칙:</strong> setup.py는 소스 파일만 포함하며 <code>data/</code>, <code>FLOW_DATA_ROOT</code>, <code>FLOW_DB_ROOT</code>, <code>FLOW_WAFER_MAP_ROOT</code> 아래 runtime 데이터를 절대 덮어쓰지 않습니다. 추출 전 소형 config/state 파일을 <code>~/.flow_backups</code>에 스냅샷합니다. 사내 반입 전후에는 <code>python scripts/preflight_internal.py --write-probe</code>로 root 보존을 확인합니다. 버전 메타는 <code>VERSION.json</code>에서 관리합니다.</p>

        <H2 id="theme">테마 시스템</H2>
        <p>CSS 변수 기반 다크/라이트 테마. 전환 상태는 <code>app/useFlowShell.js</code>가 관리합니다.</p>
        <Code>{`/* 사용 가능한 CSS 변수 */
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
style={{ color: "var(--accent)", background: "var(--bg-card)" }}`}</Code>

        <H2 id="infra">인프라</H2>
        <p><strong style={{color:"var(--text-primary)"}}>경로 관리 (core/paths.py, core/roots.py):</strong></p>
        <Code>{`from core.paths import PATHS

PATHS.db_root      # FLOW_DB_ROOT → profile → shared DB → data/Fab
PATHS.data_root    # FLOW_DATA_ROOT → ... → data/flow-data

# 환경변수로 오버라이드
FLOW_DB_ROOT=/other/path uvicorn app:app
FLOW_DATA_ROOT=/other/state uvicorn app:app

# resolver 우회 경로 하드코딩 금지`}</Code>

        <p><strong style={{color:"var(--text-primary)"}}>알림 시스템 (core/notify.py):</strong></p>
        <Code>{`from core.notify import send_notify, send_to_admins

send_notify("username", "Title", "Body", type="info")   # 특정 유저
send_to_admins("New Alert", "...", type="approval")     # 모든 admin

# type: info | warning | approval | message`}</Code>

        <p><strong style={{color:"var(--text-primary)"}}>검증 명령:</strong></p>
        <Code>{`python scripts/smoke_test.py                       # 핵심 smoke (서버 필요)
python -m pytest tests                             # 백엔드 단위 테스트
python scripts/preflight_internal.py --write-probe # 사내 반입/업데이트 전
python scripts/empty_root_smoke.py                 # 빈 데이터 root 부팅`}</Code>

        <p><strong style={{color:"var(--text-primary)"}}>하트비트 (서버 유지):</strong></p>
        <Code>{`# crontab에 등록 (매 30분)
*/30 * * * * curl -X POST http://localhost:8080/api/monitor/heartbeat`}</Code>

        <p style={{marginTop:24,padding:"12px 16px",borderRadius:8,background:"var(--bg-card,#2a2a2a)",border:"1px solid var(--border,#333)",fontSize:14}}>
          <strong style={{color:"var(--accent)"}}>포트:</strong> 8080 &nbsp;|&nbsp;
          <strong style={{color:"var(--accent)"}}>기본 admin:</strong> hol / hol12345! &nbsp;|&nbsp;
          <strong style={{color:"var(--accent)"}}>서버 실행:</strong> 프로젝트 루트에서 uvicorn app:app --host 0.0.0.0 --port 8080 (또는 start_flow.bat)
        </p>
      </div>
    </div>
  );
}
