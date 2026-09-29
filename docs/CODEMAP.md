# Flow 코드 지도 (CODEMAP)

사내 VM의 opencode 등 코딩 에이전트가 **어디를 고쳐야 하는지 빠르게 찾도록** 만든 지도다.
규칙·금지사항은 루트 `AGENTS.md`가 정본이고, 이 문서는 위치 안내만 한다.
파일 목록이 바뀌면 이 문서도 같이 고친다(2026-09-29 기준으로 실제 코드에서 추출).

## 1. 한눈에 보는 구조

```
브라우저 (React 18 + Vite SPA, frontend/src)
  │  lib/api.js 의 sf()/postJson() 이 모든 요청에 X-Session-Token 헤더를 붙인다
  ▼
/api/*  ── backend/app.py (FastAPI 앱 조립, 인증 미들웨어, frontend/dist 정적 서빙)
  │        app_v2/runtime/router_loader.py 가 backend/routers/*.py 를 자동 등록
  ▼
backend/routers/<기능>.py   HTTP 입출력 + 권한 검사(core/auth.py)
  │
  ▼
backend/core/<기능>.py      계산·캐시·저장 (polars / duckdb / sqlite / json)
  │
  ├─ FLOW_DB_ROOT    원천 DB(parquet/csv, 제품별 폴더). **읽기 전용**으로 다룬다
  ├─ FLOW_DATA_ROOT  사용자 기록·설정·로그(json/jsonl/sqlite). 앱이 쓰는 곳
  └─ (선택) 사내 LLM  core/llm_adapter.py — 없거나 실패해도 기능이 규칙만으로 동작해야 한다
```

- 경로는 전부 `core/paths.py`의 `PATHS`(`data_root`, `db_root`, `base_root`, `wafer_map_root`)에서 얻는다. 경로 문자열을 직접 만들지 않는다.
- 백그라운드 스케줄러(캐시 예열, ET 추적, 매칭 알람 등)는 `app_v2/runtime/startup.py`가 기동 때 켠다.
- 모든 요청 활동은 `core/audit.py`의 `record()`로 `FLOW_DATA_ROOT/logs/activity.jsonl`에 남고, 관리자 > 활동 현황이 `core/activity_index.py`로 집계한다.

## 2. 폴더 지도

| 경로 | 내용 |
|---|---|
| `app.py`, `core/`, `routers/`, `app_v2/` (루트) | import shim. **여기는 고치지 않는다** |
| `backend/app.py` | 실제 FastAPI 앱. 미들웨어·라우터 등록·SPA 서빙·번들 자가복구 목록 |
| `backend/routers/` | API 엔드포인트 (파일 하나 = prefix 하나가 기본) |
| `backend/core/` | 업무 로직·캐시·저장소 (약 180개 모듈) |
| `backend/app_v2/modules/splittable/router_parts/` | SplitTable 라우터 본문 (`routers/splittable.py`는 11줄 로더) |
| `backend/app_v2/modules/filebrowser/router_parts/` | FileBrowser 라우터 본문 (`routers/filebrowser.py`는 로더) |
| `backend/app_v2/runtime/` | 기동 wiring: 라우터 로더, 보안, 압축, 리소스 가드, startup |
| `frontend/src/app/pageManifest.jsx` | **탭 등록 단일 목록** (key·라벨·그룹·lazy import) |
| `frontend/src/pages/My_*.jsx` | 2줄짜리 호환 wrapper. 로직을 넣지 않는다 |
| `frontend/src/features/<기능>/` | 화면 구현 본체 |
| `frontend/src/components/`, `components/ui/` | 공용 UI (Modal, DataTable, Icon, SpreadsheetPasteGrid, PlotlyChart …) |
| `frontend/src/lib/` | 공용 로직 (api.js, permissions.js, chartLayout.js, chartTheme.js, plotlyCustom.js …) |
| `frontend/src/styles/` | `tokens.css`(색·간격 토큰) + components/layouts/utilities.css |
| `frontend/scripts/` | `check-design-system.mjs`, `check-feature-boundaries.mjs` (npm run check) |
| `tests/` | pytest 약 150개. `tests/conftest.py`가 운영 데이터 격리 fixture 제공 |
| `scripts/` | 운영 스크립트 (`flow_server.py` 감시기, Windows 자동기동 등) |
| `guides/` | 사용자용 탭 사용법 안내·영상 |
| `_build_setup.py` → `setup.py` | 배포 번들 빌더 → 자기추출 설치본 (배포 = 재빌드) |
| `data/` | 로컬 런타임 데이터. **커밋·번들 금지** |

## 3. 탭 ↔ 코드 위치

화면 파일은 `frontend/src/features/` 기준, API는 `backend/routers/` 기준이다.
"주요 core"는 대표 모듈만 적었다 — 나머지는 라우터 파일의 `from core ...` import를 따라간다.

### 데이터 그룹

| 탭 key (라벨) | 화면 | API prefix → 라우터 | 주요 core |
|---|---|---|---|
| `home` (홈) | `home/My_Home.jsx`, `HomeDataChat.jsx`, `HomeAppIcons.jsx` | `/api/home` → `home.py`, `/api/home-agent` → `data_chat.py`·`chat_prompts.py` | `data_chat*.py`, `flowi_turn.py`, `flowi_quota.py`, `home_agent_offload.py` |
| `filebrowser` (파일탐색기) | `filebrowser/My_FileBrowser.jsx` | `/api/filebrowser` → `app_v2/modules/filebrowser/router_parts/*`, `/api/sql-workspace`, `/api/s3ingest` | `filebrowser_cache.py`, `duckdb_engine.py`, `sql_workspace.py`, `db_cache.py` |
| `dashboard` (대시보드) | `dashboard/My_Dashboard.jsx` | `/api/dashboard` → `dashboard.py` | `dashboard_join.py`, `lot_progress_cache.py` |
| `splittable` (스플릿 테이블) | `splittable/My_SplitTable.jsx` | `/api/splittable` → `app_v2/modules/splittable/router_parts/*` | `lot_step.py`, `ml_table_lookup.py`, `lot_list_cache.py`, `scan_gate.py` |
| `lotmanage` (랏 관리) | `lotmanagement/My_LotManagement.jsx` | `/api/lot-management` → `lot_management.py` | `watchlist.py` |
| `productwiki` (제품 위키) | `productwiki/` | `/api/product-wiki` → `product_wiki.py`, `/api/product-semantics` | `product_wiki*.py`, `product_semantics.py` |
| `ramcache` (캐시 관리) | `ramcache/My_RamCache.jsx` | `/api/splittable` (관리 API) | `cache_budget.py`, `cache_settings.py`, `memory_watchdog.py` |
| `matchfill` (매칭 채우기) | `matchfill/My_MatchFill.jsx` | `/api/matching-fill` → `matching_fill.py` | `matching_fill.py`, `matching_store.py` |

### 업무 그룹

| 탭 key (라벨) | 화면 | API prefix → 라우터 | 주요 core |
|---|---|---|---|
| `chartbuilder` (차트생성) | `chartbuilder/My_ChartBuilder.jsx` | `/api/filebrowser` (chart 부분: `70_chart_builder_and_downloads.part.py`) | `chart_builder_definition.py` |
| `templatereport` (Template Report) | `templatereport/My_TemplateReport.jsx` | `/api/template-report` → `template_report.py` | `report_variables.py` |
| `autoreport` (Auto report) | `autoreport/My_AutoReport.jsx` | `/api/auto-report`·`/api/autoreport` → `auto_report.py` | `auto_report.py` (작업 파일 대기열 + 운영 러너 1건씩) |
| `lotrequest` (랏 배정/요청) | `lotrequest/My_LotRequest.jsx` | `/api/lot-requests` → `lot_requests.py` | `rich_text.py` |
| `analysisrequest` (분석의뢰) | `analysisrequest/` | `/api/analysis-requests` → `analysis_requests.py`, `/api/dc-layers` | `analysis_requests.py`, `dc_layer_mapping.py` |
| `lotlocation` (랏 현위치 확인) | `lotlocation/My_LotLocation.jsx` | `/api/lot-location` → `lot_location.py` | `lot_wip.py`, `latest_lot_partitions.py` |
| `inform` (인폼 로그) | `inform/My_Inform.jsx` | `/api/informs` → `informs.py`·`informs_extra.py` | `mail.py`, `app_v2/modules/informs/` |
| `meeting` (회의관리) | `meeting/My_Meeting.jsx` | `/api/meetings` → `meetings.py` | `app_v2/modules/meetings/` |
| `calendar` (변경점 관리) | `calendar/My_Calendar.jsx` | `/api/calendar` → `calendar.py` | — |
| `tracker` (ET 추적) | `tracker/My_Tracker.jsx` | `/api/tracker` → `tracker.py` | `et_tracker.py`, `tracker_scheduler.py` |
| `lottracker` (LOT Tracker) | `tracker/LotTracker.jsx` | `/api/lot-tracker` → `lot_tracker.py` | `lot_tracker.py` |
| `valve` (매칭알람) | `valve/My_ValveAlerts.jsx` | `/api/valve-alerts` → `valve_alerts.py` | `valve_alerts.py`, `valve_step_advisor.py`, `fab_matching_alerts.py` |
| `teg` (TEG 위치 조회) | `teg/My_TegMap.jsx`, `teg/TegCheck.jsx` | `/api/teg-map` → `teg_map.py` | `teg_map.py`, `teg_check.py`, `mapfile_traffic.py` |
| `yieldmap` (WF MAP) | `yieldmap/My_YieldMap.jsx` | `/api/yield-map` → `yield_map.py` | `yield_map.py` |
| `ettime` (ET 측정시간) | `ettime/My_EtTime.jsx` | `/api/et-time` → `et_time.py` | `et_run_service.py` (상주 계산 프로세스) |
| `reformatize` (ET 다운로드) | `reformatize/My_Reformatize.jsx` | `/api/reformatize` → `reformatize.py` | `vehicle_reformatter.py`, `reformatter.py` |
| `dcop` (양산DCOP 검사) | `dcop/My_DcopCheck.jsx` | `/api/dcop` → `dcop.py` | — |

### 관리·내부

| 탭 key | 화면 | API | 비고 |
|---|---|---|---|
| `admin` (관리자) | `admin/My_Admin.jsx` (+ 같은 폴더 패널들) | `/api/admin` → `admin.py`, `/api/groups`, `/api/monitor`, `/api/catalog`, `/api/messages`, `/api/admin/domain-knowledge` | 소탭은 `My_Admin.jsx`의 `ADMIN_SECTIONS`(운영/시스템/에이전트)에 등록해야 보인다 |
| 3D 구조 (관리자 > 에이전트 > 기본지식) | `structure/StructureModelWorkspace.jsx` | `/api/structure-model` → `structure_model.py` | `core/structure_model.py`(장면), `structure_edit_rules.py`(한국어 규칙), `structure_topology.py`(SRAM/latch-up) |
| `tablemap` (내부) | `tablemap/My_TableMap.jsx` | `/api/dbmap` → `dbmap.py` | 네비게이션에 없음 |
| `knowledge` (내부) | `knowledge/My_Knowledge.jsx` | `/api/knowledge` → `knowledge.py` | 네비게이션에 없음 |
| 로그인 | `auth/My_Login.jsx` | `/api/auth` → `auth.py`, `/api/auth/sso/oidc` | 계정 저장소 = `FLOW_DATA_ROOT/users.csv` |

### 배포되지 않는 은퇴 코드 (고쳐도 운영에 반영 안 됨)

`_build_setup.py`의 `FLOWI_EXCLUDE_PREFIXES`/`FLOWI_EXCLUDE_FILES`:
`routers/agent.py`, `routers/home_agent.py`, `routers/flowi_learning.py`, `core/home_orchestrator.py`,
`core/home_memory.py`, `core/flowi_fewshots.py`, `core/flowi_file_docs.py`, `core/flowi_multisource.py`,
`core/flowi_workflow_catalog.py`, `core/flowi_units/`, `app_v2/modules/llm/`, `app_v2/modules/agent_runtime/`,
`app_v2/modules/semantic_learning/`, `frontend/src/features/diagnosis/`.
홈 챗 수정은 `routers/data_chat.py` + `core/data_chat*.py`에서 한다.

## 4. 공통 인프라 (여러 탭이 같이 쓰는 것)

| 필요한 것 | 쓰는 곳 |
|---|---|
| 로그인 사용자 / 관리자 확인 | `core/auth.py`: `current_user(request)`, `Depends(require_admin)`, `has_page_access`, `require_page_manager(page_id)` |
| 사용자 목록·역할 | `routers/auth.py`: `read_users()` (users.csv, 행 dict: username/role/…) |
| 활동 기록 | `core/audit.py`: `record(request, "기능:동작", detail=..., tab=...)` |
| 알림 | `core/notify.py` |
| 메일 | `core/mail.py`, 수신 그룹 `/api/mail-groups` |
| LLM 호출 | `core/llm_adapter.py` (`complete_json` 등). 새 LLM 경로는 어댑터의 허용 목록 등록이 필요 |
| 무거운 계산 | `core/heavy_jobs.run_heavy()` (메모리 확인·캐시 슬롯·사용자 양보), `core/request_priority.py` |
| 캐시 예산·메모리 | `core/cache_budget.py`, `core/runtime_limits.py`, `core/memory_watchdog.py` |
| JSON 파일 저장 | `core/utils.py` (`load_json`, `save_json`, `jsonl_*`), 동시 쓰기는 `core/file_transaction.py` |
| 프런트 요청 | `lib/api.js`: `sf(url)`(GET→JSON), `postJson`, `putJson`, `dl`, `postDownload` |
| 탭 권한 승계 | `lib/permissions.js` `INHERITED_TAB_ACCESS` |
| 아이콘 | `components/ui/Icon.jsx` (UI에 이모지를 새로 쓰지 않는다), 홈 런처 아이콘 `features/home/HomeAppIcons.jsx` |
| 차트 | `components/PlotlyChart.jsx`·`FlowPlotlyChart`, 크기 `lib/chartLayout.js`, 색 `lib/chartTheme.js` |
| 표 붙여넣기 입력 | `components/SpreadsheetPasteGrid.jsx` |

요청 한 건의 흐름 예시 (관리자 > 활동 현황):
`features/admin/My_Admin.jsx` `ActivityDashboardPanel` → `sf("/api/admin/activity/summary?days=30")`
→ `routers/admin.py` `activity_summary()` (`require_admin`) → `core/activity_index.py` `summary()` → activity.jsonl 인덱스(sqlite) 집계.

## 5. 자주 하는 수정 레시피

**기존 화면에 버튼·필터 추가**
1. 3절 표에서 화면 파일을 찾는다. 파일이 크면(수천 줄) `grep -n "function 패널이름\|/api/경로"`로 위치를 찾고 그 구간만 읽는다.
2. 서버 값이 필요하면 해당 라우터 함수에 Query 파라미터를 **기본값과 함께** 추가하고(기존 호출이 깨지지 않게), 계산은 core 함수에 인자로 넘긴다.
3. core 함수에 메모이즈 캐시가 있으면 새 인자를 캐시 키에 넣는다(예: `activity_index._memoized`).
4. 관련 pytest에 경우를 추가한다.

**새 API 엔드포인트**
- 기존 prefix 라우터 파일에 `@router.get/post` 추가. 권한 없는 공개 API를 만들지 않는다(`current_user` 또는 `require_admin`).
- SplitTable/FileBrowser는 `router_parts/NN_*.part.py`에 추가한다. part들은 **하나의 모듈 namespace**로 파일명 순서대로 실행되므로 앞 part의 helper를 import 없이 쓸 수 있고, 같은 이름을 다시 정의하면 덮어쓴다.
- 새 라우터 파일은 `backend/routers/`에 두면 자동 등록된다.

**새 backend 모듈** — `backend/app.py`의 `_REQUIRED_BUNDLED_BACKEND_SOURCES`에 등록(운영 자가복구). 최상단 import는 try/except로 감싼다.

**새 탭**
1. `features/<name>/My_<Name>.jsx` 구현 + `pages/My_<Name>.jsx` 2줄 wrapper
2. `app/pageManifest.jsx`에 항목 추가 (key, label, group, layout, helpId, defaultEnabled, load)
3. group이 data/work면 `backend/core/auth.py` `DELEGABLE_PAGE_IDS`에도 추가 (`npm run design:check`가 대조)
4. 홈 런처 아이콘 `features/home/HomeAppIcons.jsx`, 필요 시 승계 권한 `lib/permissions.js`

**관리자 소탭** — `My_Admin.jsx`의 `ADMIN_SECTIONS` 해당 구역 `tabs`에 `[key, 라벨]` 추가 + 렌더 분기 `{tab==="key"&&...}`.

**새 설정 키** — `config/`와 데이터 파일은 seed-only. 기본값을 반드시 코드에 둔다.

**3D 구조 기본 모양** — 파라미터 범위·설명은 `core/structure_model.py` 상단 `DETAIL_LIMITS`/`PARAMETER_GUIDE`, 기본값은 `build_scene` 안의 `params.get(키, 기본값)`,
관리자 화면 기본 표시값은 `StructureModelWorkspace.jsx`의 `currentFallback`/`DETAIL_PARAMS`. 둘을 같이 바꾼다.

## 6. 검증

```bash
# 백엔드: 바꾼 기능의 테스트만 (저장소 루트 flow/ 에서 실행)
python -m pytest -q tests/test_<관련>.py
# 관련 테스트 찾기
grep -l "core.activity_index\|routers.admin" tests/*.py
# 프런트: 디자인 규칙 + 기능 경계 + 빌드
cd frontend && npm run check
```

- 테스트는 격리된 `FLOW_DATA_ROOT`/`FLOW_DB_ROOT`와 `FLOW_PROD=0`에서 돌린다. 운영 데이터에 쓰지 않는다.
- `npm run check`는 `frontend/dist`를 다시 만든다. 배포하려면 `python _build_setup.py`로 `setup.py`까지 재빌드해야 한다.

## 7. 수정할 때 자주 걸리는 함정

- **줄바꿈 보존:** 일부 파일은 CRLF(`My_Admin.jsx`, `routers/admin.py` 등), 대부분은 LF다. 파일 전체를 다시 쓰지 말고 필요한 부분만 바꾼다.
- **큰 파일:** `informs.py`(약 7천 줄), `My_Inform.jsx`·`My_SplitTable.jsx`·`My_FileBrowser.jsx`·`My_Admin.jsx`(4~5천 줄)는 통째로 읽지 말고 grep으로 구간을 찾는다.
- **디자인 규칙:** 색·간격은 `tokens.css` 변수. `components.css`/`layouts.css`/`utilities.css`에 raw 색상·`!important` 금지 (`npm run design:check`).
- **한국어 정규식:** Python `re`에서 한글도 `\w`라 `\b`가 "GATE는" 같은 곳에서 경계를 못 잡는다 → `(?<![A-Za-z0-9])` 사용.
- **LLM 선택성:** LLM이 없어도 규칙 경로로 동작해야 하는 기능(3D 구조 편집, 운영 점검, 홈 챗 일부)은 테스트가 LLM 호출을 막는다.
- **개발용 추출:** 운영 기본 `python setup.py extract`는 `tests/`, `docs/`, `AGENTS.md`를 풀지 않는다. 코드를 고칠 VM에서는 `python setup.py extract --all`로 받는다.
- **공개 저장소:** GitHub 저장소는 PUBLIC. 사내 데이터·제품 실명·리포트·발표자료를 커밋하지 않는다. `git add -A` 금지.
