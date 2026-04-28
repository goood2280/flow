import { useState, useEffect } from "react";

const mono = "'JetBrains Mono',monospace";

function Code({ children }) {
  return <pre style={{ background:"#111", borderRadius:8, padding:"14px 18px", border:"1px solid var(--border,#333)", overflow:"auto", fontFamily:mono, fontSize:11, lineHeight:1.8, color:"#e5e5e5", whiteSpace:"pre", margin:"12px 0" }}>{children}</pre>;
}

function H2({ children, id }) {
  return <h2 id={id} style={{ fontSize:16, fontWeight:700, color:"var(--accent,#f97316)", marginTop:36, marginBottom:14, paddingBottom:6, borderBottom:"1px solid var(--border,#333)", fontFamily:mono }}><span style={{color:"var(--text-secondary)"}}>{">"} </span>{children}</h2>;
}

function ApiRow({ method, path, desc }) {
  const c = {GET:"#22c55e",POST:"#f97316",DELETE:"#ef4444"};
  return (
    <div style={{ display:"flex", alignItems:"center", gap:10, padding:"8px 0", borderBottom:"1px solid var(--border,#222)", fontSize:12 }}>
      <span style={{ fontFamily:mono, fontSize:10, fontWeight:700, padding:"2px 8px", borderRadius:4, minWidth:44, textAlign:"center", background:(c[method]||"#666")+"22", color:c[method]||"#666" }}>{method}</span>
      <span style={{ fontFamily:mono, color:"var(--text-primary)", minWidth:300 }}>{path}</span>
      <span style={{ color:"var(--text-secondary)" }}>{desc}</span>
    </div>
  );
}

const NAV = [
  { id:"arch", label:"아키텍처" },
  { id:"files", label:"파일 구조" },
  { id:"api", label:"API 레퍼런스" },
  { id:"db", label:"DB 구조" },
  { id:"schema", label:"표준 스키마" },
  { id:"flowi", label:"Flow-i 규칙" },
  { id:"perf", label:"대용량 운영" },
  { id:"ux", label:"UX 시스템" },
  { id:"add", label:"기능 추가" },
  { id:"update", label:"업데이트 시스템" },
  { id:"theme", label:"테마 시스템" },
  { id:"infra", label:"인프라" },
];

export default function My_DevGuide() {
  const [version, setVersion] = useState(null);
  const [active, setActive] = useState("arch");

  useEffect(() => { fetch("/version.json").then(r=>r.json()).then(setVersion).catch(()=>{}); }, []);

  const scrollTo = (id) => { setActive(id); document.getElementById(id)?.scrollIntoView({behavior:"smooth",block:"start"}); };

  return (
    <div style={{ display:"flex", minHeight:"calc(100vh - 48px)", background:"var(--bg-primary,#1a1a1a)", color:"var(--text-primary,#e5e5e5)", fontFamily:"'Pretendard',sans-serif" }}>

      {/* Side Nav */}
      <div style={{ width:190, padding:"20px 10px", borderRight:"1px solid var(--border,#333)", position:"sticky", top:48, height:"calc(100vh - 48px)", overflowY:"auto", flexShrink:0 }}>
        <div style={{ fontSize:10, fontWeight:700, color:"var(--accent,#f97316)", textTransform:"uppercase", letterSpacing:"0.08em", marginBottom:12, paddingLeft:10, fontFamily:mono }}>{">"} 개발자_가이드</div>
        {NAV.map(n => (
          <div key={n.id} onClick={() => scrollTo(n.id)}
            style={{ padding:"6px 10px", borderRadius:5, cursor:"pointer", fontSize:12, marginBottom:1, fontFamily:mono,
              background: active===n.id ? "var(--accent-glow,#f9731622)" : "transparent",
              color: active===n.id ? "var(--accent,#f97316)" : "var(--text-secondary,#a3a3a3)",
              fontWeight: active===n.id ? 600 : 400 }}>{n.label}</div>
        ))}
        {version && <div style={{ marginTop:16, padding:"8px 10px", borderRadius:5, background:"var(--bg-card,#2a2a2a)", fontSize:10, color:"var(--text-secondary)", fontFamily:mono, lineHeight:1.6 }}>
          v{version.version}<br/>"{version.codename}"<br/>{version.updated}
        </div>}
      </div>

      {/* Content */}
      <div style={{ flex:1, padding:"28px 36px", maxWidth:860, overflow:"auto", lineHeight:1.8, fontSize:13, color:"var(--text-secondary,#a3a3a3)" }}>

        <H2 id="arch">아키텍처</H2>
        <p>flow = <strong style={{color:"var(--text-primary)"}}>FastAPI</strong> (백엔드) + <strong style={{color:"var(--text-primary)"}}>React + Vite</strong> (프론트엔드) + <strong style={{color:"var(--text-primary)"}}>Polars/Parquet</strong> (데이터)</p>
        <Code>{`[Browser] ──HTTP──> [FastAPI :8080]
                        ├── /api/*          → routers/*.py (auto-loaded)
                        ├── /version.json   → version.json
                        └── /*              → frontend/dist/ (SPA)

[Data]
  local checkout:
    data/Fab/             → Parquet / rulebook / ML_TABLE
    data/flow-data/       → Users, logs, sessions
  prod or FLOW_PROD=1:
    /config/work/sharedworkspace/DB/        → Parquet (read-only)
    /config/work/sharedworkspace/flow-data/ → Users, logs, sessions`}</Code>

        <p><strong style={{color:"var(--text-primary)"}}>동적 라우터 로딩:</strong> backend/routers/ 에 .py 파일을 넣으면 runtime loader가 자동으로 등록합니다. app.py는 앱 조립과 정적 서빙만 담당합니다.</p>
        <Code>{`# backend/app.py (핵심 로직)
app.add_middleware(AuthMiddleware)
loaded, failed = include_router_modules(app, ROUTERS_DIR, logger)
start_background_services(logger)`}</Code>

        <H2 id="files">파일 구조</H2>
        <Code>{`flow/
├── app.py                   # uvicorn shim
├── setup.py                 # self-contained installer
├── VERSION.json             # 버전 + changelog
│
├── backend/
│   ├── app.py               # FastAPI app assembly
│   ├── app_v2/
│   │   ├── runtime/         # auth middleware, router loading, startup
│   │   ├── shared/          # JSON store, result, source adapter
│   │   └── modules/         # feature service/repository/domain
│   ├── core/
│   │   ├── paths.py         # 경로 중앙관리 (환경변수 오버라이드)
│   │   ├── session.py       # 유저별 세션 저장/복원
│   │   └── notify.py        # 알림 시스템
│   └── routers/             # ← 여기 .py 넣으면 자동 로드
│       ├── auth.py          # 로그인/회원가입/비번리셋
│       ├── admin.py         # 유저관리/로그/알림/메시지
│       ├── filebrowser.py   # Parquet browse + SQL filter
│       ├── monitor.py       # DB 상태 신호등 + CPU/메모리
│       └── session_api.py   # 세션 저장/복원 API
│
├── frontend/
│   ├── vite.config.js       # Vite 설정 (proxy → :8080)
│   ├── src/
│   │   ├── App.jsx          # shell composition
│   │   ├── app/
│   │   │   ├── pageRegistry.jsx
│   │   │   └── useFlowShell.js
│   │   ├── config.js        # 탭 등록 (여기 추가하면 네비에 표시)
│   │   ├── components/
│   │   │   ├── Loading.jsx  # 스피너/스켈레톤
│   │   │   └── ComingSoon.jsx # 미구현 페이지 placeholder
│   │   └── pages/           # pageRegistry에 등록하면 연결
│   │       ├── My_Home.jsx
│   │       ├── My_FileBrowser.jsx
│   │       ├── My_Admin.jsx
│   │       ├── My_DevGuide.jsx
│   │       └── My_Login.jsx
│   └── dist/                # npm run build 결과 (서버가 서빙)
│
└── data/
    ├── Fab/                # local Parquet/rulebook/ML_TABLE root
    └── flow-data/          # local app state

prod:
└── /config/work/sharedworkspace/
    ├── DB/                  # Parquet 데이터 (읽기전용)
    │   ├── 1.RAWDATA_*/
    │   │   └── product/date=YYYY-MM-DD/part-*.parquet
    │   └── ...
    └── flow-data/         # 앱 데이터 (읽기쓰기)
        ├── users.csv
        ├── logs/activity.jsonl
        ├── sessions/{user}.json
        ├── notifications/{user}.jsonl
        ├── tracker/
        ├── splittable/
        └── matching/`}</Code>

        <H2 id="api">API 레퍼런스</H2>
        <p>보안상 Swagger UI는 비활성화되어 있습니다. API shape는 각 router와 smoke test를 기준으로 확인합니다.</p>

        <div style={{fontSize:13,fontWeight:700,color:"var(--accent)",marginTop:20,marginBottom:8,fontFamily:mono}}>인증 (Auth)</div>
        <ApiRow method="POST" path="/api/auth/login" desc="로그인 → {ok, username, role}" />
        <ApiRow method="POST" path="/api/auth/register" desc="회원가입 → admin 승인 대기" />
        <ApiRow method="POST" path="/api/auth/reset-request" desc="비밀번호 리셋 요청 → admin에 알림" />

        <div style={{fontSize:13,fontWeight:700,color:"var(--accent)",marginTop:20,marginBottom:8,fontFamily:mono}}>관리자 (Admin)</div>
        <ApiRow method="GET" path="/api/admin/users" desc="전체 유저 목록" />
        <ApiRow method="POST" path="/api/admin/approve" desc="유저 승인" />
        <ApiRow method="POST" path="/api/admin/reject" desc="유저 삭제" />
        <ApiRow method="POST" path="/api/admin/reset-password" desc="비밀번호 초기화 (hol12345!)" />
        <ApiRow method="POST" path="/api/admin/send-message" desc="특정 유저에게 메시지" />
        <ApiRow method="POST" path="/api/admin/broadcast" desc="전체 공지" />
        <ApiRow method="GET" path="/api/admin/my-notifications?username=" desc="내 알림 (미읽음)" />
        <ApiRow method="GET" path="/api/admin/all-notifications?username=" desc="내 알림 (전체)" />
        <ApiRow method="POST" path="/api/admin/mark-read" desc="알림 읽음 처리" />
        <ApiRow method="POST" path="/api/admin/log" desc="활동 로그 기록" />
        <ApiRow method="GET" path="/api/admin/logs?limit=&username=" desc="활동 로그 조회" />

        <div style={{fontSize:13,fontWeight:700,color:"var(--accent)",marginTop:20,marginBottom:8,fontFamily:mono}}>파일탐색기 (File Browser)</div>
        <ApiRow method="GET" path="/api/filebrowser/roots" desc="DB 루트 목록 (자동 탐색)" />
        <ApiRow method="GET" path="/api/filebrowser/tree?root=&depth=" desc="폴더 트리" />
        <ApiRow method="GET" path="/api/filebrowser/files?root=&path=&page=" desc="파일 목록 + 페이징" />
        <ApiRow method="GET" path="/api/filebrowser/preview?root=&path=&rows=" desc="Parquet/CSV head + 컬럼 정보" />
        <ApiRow method="GET" path="/api/filebrowser/merge-preview?root=&path=" desc="Hive 파티션 병합 미리보기" />
        <ApiRow method="GET" path="/api/filebrowser/download?root=&path=" desc="파일 다운로드" />

        <div style={{fontSize:13,fontWeight:700,color:"var(--accent)",marginTop:20,marginBottom:8,fontFamily:mono}}>ML 분석 (v7)</div>
        <ApiRow method="GET" path="/api/ml/sources" desc="ML_TABLE 소스 목록 (와이드 테이블 자동 탐지)" />
        <ApiRow method="GET" path="/api/ml/columns?root=&product=" desc="컬럼을 prefix별로 그룹화 (KNOB/MASK/INLINE/VM/FAB/QTIME/ET)" />
        <ApiRow method="POST" path="/api/ml/train" desc="correlation / TabPFN / TabICL 학습 → importance, scatter, metrics" />
        <ApiRow method="POST" path="/api/ml/process_window" desc="v7: 공정 window-aware 분석 — 상류만 인과 허용, exp(-d/5) 감쇠, KNOB split μ±σ" />

        <div style={{fontSize:13,fontWeight:700,color:"var(--accent)",marginTop:20,marginBottom:8,fontFamily:mono}}>이슈 추적 (카테고리 v7)</div>
        <ApiRow method="GET" path="/api/tracker/categories" desc="카테고리 목록 (admin configurable)" />
        <ApiRow method="GET" path="/api/tracker/categories/usage" desc="v7: 카테고리별 사용 이슈 수 + orphan 감지" />
        <ApiRow method="POST" path="/api/tracker/categories/save" desc="카테고리 리스트 덮어쓰기 (순서 포함)" />

        <div style={{fontSize:13,fontWeight:700,color:"var(--accent)",marginTop:20,marginBottom:8,fontFamily:mono}}>모니터 (Monitor)</div>
        <ApiRow method="GET" path="/api/monitor/health" desc="DB별 상태 신호등 (green/yellow/red)" />
        <ApiRow method="GET" path="/api/monitor/system" desc="CPU/메모리/디스크 사용량" />
        <ApiRow method="POST" path="/api/monitor/heartbeat" desc="서버 유지용 heartbeat (cron)" />

        <div style={{fontSize:13,fontWeight:700,color:"var(--accent)",marginTop:20,marginBottom:8,fontFamily:mono}}>세션 (Session)</div>
        <ApiRow method="POST" path="/api/session/save" desc="유저 세션 저장 (탭, 폼 데이터)" />
        <ApiRow method="GET" path="/api/session/load?username=" desc="유저 세션 복원" />

        <H2 id="db">DB 구조</H2>
        <Code>{`# Hive Partition 규칙
/config/work/sharedworkspace/DB/
  {RAWDATA_NAME}/
    {product_name}/
      date=YYYY-MM-DD/
        part-00000.parquet
        part-00001.parquet

# Polars 읽기
import polars as pl

# 단일 파일
df = pl.read_parquet("path/to/part-00000.parquet")

# 날짜별 파티션 전체 병합
df = pl.read_parquet("ProductA/date=*/*.parquet")

# 특정 날짜만
df = pl.read_parquet("ProductA/date=2024-12-17/*.parquet")

# SQL 필터 적용
df = pl.read_parquet(files).filter(pl.sql_expr("item_id = 'VTH' AND et_value > 0.5"))`}</Code>

        <p><strong style={{color:"var(--text-primary)"}}>모니터 신호등 기준:</strong></p>
        <div style={{display:"flex",gap:16,margin:"8px 0 16px",fontSize:12}}>
          <span><span style={{display:"inline-block",width:10,height:10,borderRadius:"50%",background:"#22c55e",marginRight:6}} />초록: 24시간 이내 업데이트</span>
          <span><span style={{display:"inline-block",width:10,height:10,borderRadius:"50%",background:"#fbbf24",marginRight:6}} />노랑: 24~72시간</span>
          <span><span style={{display:"inline-block",width:10,height:10,borderRadius:"50%",background:"#ef4444",marginRight:6}} />빨강: 72시간 이상 또는 없음</span>
        </div>

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

        <p><strong style={{color:"var(--text-primary)"}}>INLINE:</strong> 기본 집계는 <code>lot_wf</code> 기준 평균입니다. 단, INLINE과 상대 데이터 양쪽에 shot/die key가 있으면 shot 단위로 먼저 매칭합니다.</p>
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

결과에는 항상 join key, left/right row 수, matched row 수, null/drop 비율을 표시합니다.`}</Code>

        <p><strong style={{color:"var(--text-primary)"}}>반도체 기본 metric dictionary:</strong> DIBL, Rch, DC, Rs, Rc, LKG, Short, Vth/VT, Ion, Ioff, Idsat, Ilin, BV, CD, Overlay, Thickness, Resistance, Contact, Defect. 이 사전은 후보 검색용이며 실제 데이터 확정은 DB schema와 사용자 선택으로만 합니다.</p>

        <H2 id="flowi">Flow-i 규칙</H2>
        <p>Flow-i는 자유 실행 agent가 아니라 등록된 단위기능을 고르는 입구입니다. LLM은 JSON 계획과 설명을 만들 수 있지만 실제 실행은 백엔드 단위기능이 검증합니다.</p>
        <Code>{`Flow-i pipeline:
1. prompt 수신
2. feature/action 후보 선택
3. role, tab permission, query budget 검사
4. 애매하면 1/2/3 선택지로 질문
5. schema catalog로 실제 컬럼 존재 확인
6. 단위기능 실행
7. 기존 화면 renderer와 같은 표/차트 결과 반환
8. user memory/activity log 기록`}</Code>

        <p><strong style={{color:"var(--text-primary)"}}>권한:</strong> 일반 user는 조회/요약/차트만 가능합니다. Admin 파일 조작은 별도 admin-only 단위기능, diff, 확인, audit log, soft-delete를 거쳐야 합니다.</p>
        <Code>{`read-only user tools:
- schema_search
- split_table_view
- inform_log_search
- issue_tracker_search
- raw_data_preview
- dashboard_metric_scatter
- ml_table_column_search

admin-only mutation tools:
- file_patch_admin_only
- file_move_admin_only
- file_delete_to_trash_admin_only
- cache_refresh_admin_only
- settings_edit_admin_only`}</Code>

        <p><strong style={{color:"var(--text-primary)"}}>차트 요청:</strong> 예를 들어 "INLINE CD와 ET LKG Corr scatter 그리고 1차식 fitting"은 아래 순서로 처리합니다.</p>
        <Code>{`1. INLINE CD 후보를 schema catalog에서 검색
2. ET LKG 후보를 schema catalog에서 검색
3. 후보가 여러 개면 1/2/3 선택지를 표시
4. INLINE value = avg by lot_wf
5. ET value = median by lot_wf
6. shot/die key가 있으면 shot-level left join 우선
7. scatter + Corr + optional y=ax+b fitting
8. ML_TABLE KNOB coloring/filter는 lot_wf 또는 shot key로 추가 left join`}</Code>

        <H2 id="perf">대용량 운영</H2>
        <p>INLINE/ET 50~100GB, ML_TABLE wide table을 기준으로 설계합니다. 원본 Parquet는 유지하고, DuckDB/Polars lazy scan, cache, index table을 붙여 broad scan을 피합니다.</p>
        <Code>{`source of truth:
- Parquet files

fast path:
- DuckDB view/index for SQL preview/filter
- splittable match_cache for root_lot_id -> latest fab_lot_id
- schema catalog for wide ML_TABLE columns
- activity/user memory index for recommendations

query budget:
- product/date/lot/filter 없는 원본 전체 scan 금지
- heavy query concurrency: 1 기본
- cache builder concurrency: 1
- table result paging
- preview row/column cap
- atomic cache build: temp file -> replace`}</Code>

        <p><strong style={{color:"var(--text-primary)"}}>4 core / 15GB 기준:</strong> Polars/DuckDB thread는 3 수준, BLAS thread는 1, memory danger threshold는 85%로 둡니다. Admin 모니터의 수동 부하는 최대 시간과 메모리 cap을 둡니다.</p>

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
        <p><strong style={{color:"var(--text-primary)"}}>1단계.</strong> 백엔드 라우터 생성:</p>
        <Code>{`# backend/routers/dashboard.py
from fastapi import APIRouter

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

@router.get("/charts")
def get_charts():
    return {"charts": [...]}

# 저장만 하면 서버 재시작 시 자동 로드됨!`}</Code>

        <p><strong style={{color:"var(--text-primary)"}}>2단계.</strong> 프론트 페이지 생성:</p>
        <Code>{`// frontend/src/pages/My_Dashboard.jsx
export default function My_Dashboard() {
  return <div>Dashboard</div>;
}`}</Code>

        <p><strong style={{color:"var(--text-primary)"}}>3단계.</strong> App.jsx 에 등록 (2줄):</p>
        <Code>{`// frontend/src/App.jsx
import My_Dashboard from "./pages/My_Dashboard";  // 추가

const PAGE_MAP = {
  ...
  dashboard: My_Dashboard,  // 추가
};`}</Code>

        <p><strong style={{color:"var(--text-primary)"}}>4단계.</strong> 빌드 + 재시작:</p>
        <Code>{`cd frontend && npm run build
# 서버 재시작`}</Code>

        <p style={{marginTop:12}}>config.js 에 이미 탭이 등록되어 있으므로 네비게이션은 자동으로 나타납니다. PAGE_MAP 에 없으면 Coming Soon 이 표시됩니다.</p>

        <H2 id="update">업데이트 시스템</H2>
        <p>기능 업데이트는 단일 Python 파일로 배포됩니다:</p>
        <Code>{`# update_v101.py 실행하면:
# 1. 새 파일 생성 (base64 디코딩)
# 2. 기존 파일 수정 (App.jsx에 import 추가 등)
# 3. version.json 업데이트
# 4. npm build 자동 실행

python update_v101.py`}</Code>

        <p><strong style={{color:"var(--text-primary)"}}>전체 리셋이 필요할 때:</strong></p>
        <Code>{`python setup.py     # 전체 재생성 (base64 인코딩, 깨짐 없음)
                    # npm install + build 자동`}</Code>

        <H2 id="theme">테마 시스템</H2>
        <p>CSS 변수 기반 다크/라이트 테마. App.jsx 에서 전환합니다.</p>
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
        <p><strong style={{color:"var(--text-primary)"}}>경로 관리 (core/paths.py):</strong></p>
        <Code>{`from core.paths import PATHS

PATHS.db_root      # local: data/Fab, prod: /config/work/sharedworkspace/DB
PATHS.data_root    # local: data/flow-data, prod: /config/work/sharedworkspace/flow-data
PATHS.users_csv    # flow-data/users.csv
PATHS.activity_log # flow-data/logs/activity.jsonl
PATHS.log_dir      # flow-data/logs/

# 환경변수로 오버라이드 가능
FLOW_PROD=1 uvicorn app:app
FLOW_DB_ROOT=/other/path uvicorn app:app`}</Code>

        <p><strong style={{color:"var(--text-primary)"}}>알림 시스템 (core/notify.py):</strong></p>
        <Code>{`from core.notify import send_notify, send_to_admins

# 특정 유저에게
send_notify("username", "Title", "Body", type="info")

# 모든 admin에게
send_to_admins("New Alert", "Something happened", type="approval")

# type: info | warning | approval | message`}</Code>

        <p><strong style={{color:"var(--text-primary)"}}>세션 저장 (core/session.py):</strong></p>
        <Code>{`from core.session import save_session, load_session

save_session("username", {"last_tab": "filebrowser", "filter": "..."})
data = load_session("username")  # → dict`}</Code>

        <p><strong style={{color:"var(--text-primary)"}}>하트비트 (서버 유지):</strong></p>
        <Code>{`# crontab에 등록 (매 30분)
*/30 * * * * curl -X POST http://localhost:8080/api/monitor/heartbeat`}</Code>

        <p style={{marginTop:24,padding:"12px 16px",borderRadius:8,background:"var(--bg-card,#2a2a2a)",border:"1px solid var(--border,#333)",fontSize:12}}>
          <strong style={{color:"var(--accent)"}}>포트:</strong> 8080 &nbsp;|&nbsp;
          <strong style={{color:"var(--accent)"}}>로그인:</strong> hol / hol12345! &nbsp;|&nbsp;
          <strong style={{color:"var(--accent)"}}>서버 실행:</strong> cd backend && uvicorn app:app --host 0.0.0.0 --port 8080
        </p>
      </div>
    </div>
  );
}
