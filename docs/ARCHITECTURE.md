# Architecture

`flow`는 FastAPI 백엔드와 React/Vite 프론트엔드로 구성된 단일 웹 앱이다. 현재 방향은 전면 재작성보다, 동작 중인 기능을 유지하면서 큰 라우터와 큰 페이지를 기능 단위로 점진 분리하는 것이다.

## Top Level

```text
flow/
├── app.py                 # uvicorn shim
├── backend/               # FastAPI, routers, services, shared utilities
├── frontend/              # React/Vite UI
├── data/                  # local demo DB + runtime state
├── scripts/               # smoke, preflight, seed, migration
├── docs/                  # current docs
└── archive/               # legacy docs and retired files
```

## Runtime Flow

```text
Browser
  -> FastAPI :8080
     -> /api/*          backend/routers/*.py
     -> /version.json   VERSION.json
     -> /*              frontend/dist SPA

FastAPI startup
  -> AuthMiddleware
  -> include_router_modules()
  -> start_background_services()
  -> ensure_seed_admin()
```

루트 [app.py](../app.py)는 `backend/app.py`를 로드하는 shim이다. 사용자는 프로젝트 루트에서 `uvicorn app:app --host 0.0.0.0 --port 8080`만 실행하면 된다.

## Backend

현재 책임 경계:

| 영역 | 역할 |
|---|---|
| `backend/app.py` | FastAPI app assembly, runtime hook, static serving |
| `backend/routers/*.py` | HTTP request/response, auth/admin gate, service 호출 |
| `backend/core/*.py` | paths, roots, auth, backup, notify, mail, source/domain helper |
| `backend/app_v2/runtime` | auth middleware, router loading, startup scheduler/seed |
| `backend/app_v2/shared` | `JsonFileStore`, `Result`, source adapter, internal API contract |
| `backend/app_v2/modules` | tracker, meetings, informs 등 점진 이관된 업무 로직 |
| `backend/app_v2/orchestrator` | 향후 내부 API/agent 연결용 JSON task/action 계약 |

목표 구조:

```text
backend/app_v2/
├── runtime/
├── shared/
├── modules/<feature>/
│   ├── domain.py
│   ├── repository.py
│   └── service.py
└── orchestrator/
    ├── schemas.py
    ├── registry.py
    └── service.py
```

Backend 규칙:

- Router는 request/response shape와 권한 확인만 담당한다.
- Service는 유스케이스를 담당한다.
- Repository는 JSON/CSV/parquet/S3 접근을 담당한다.
- Domain은 validation과 업무 규칙을 담당한다.
- 새 JSON 저장 로직은 `app_v2.shared.json_store.JsonFileStore` 또는 동등한 atomic 저장 계층을 우선한다.
- `runtime`에는 feature 로직을 넣지 않는다.
- `orchestrator`는 아직 실행 엔진이 아니라 안정적인 task/result/action 계약을 보관하는 스캐폴딩이다.

현재 `app_v2/modules` 사용 지점:

| module | 현재 역할 |
|---|---|
| `tracker` | issue domain/repository/service, legacy issue shape 호환 |
| `meetings` | meeting/session repository/service |
| `informs` | SplitTable embed payload builder |

## Frontend

현재 진입점:

| 파일/폴더 | 역할 |
|---|---|
| `frontend/src/App.jsx` | shell, tab layout, global modal, error boundary |
| `frontend/src/config.js` | tab metadata, admin/restricted setting |
| `frontend/src/app/pageRegistry.jsx` | tab key -> lazy page component mapping |
| `frontend/src/app/useFlowShell.js` | auth, theme, tab, notification, session state |
| `frontend/src/pages/My_*.jsx` | page-level 업무 화면 |
| `frontend/src/pages/SplitTable/*` | SplitTable에서 분리된 일부 panel/helper |
| `frontend/src/components/*` | BrandLogo, PageGear, UXKit 등 shared UI |
| `frontend/src/lib/api.js` | authenticated fetch/download helper |

목표 구조:

```text
frontend/src/
├── app/
├── pages/
├── pages/<PageName>/        # 기존 page에서 먼저 쪼갠 local pieces
├── features/<feature>/      # 규모가 커진 기능의 api/hooks/components/utils
└── shared/ or components/   # 여러 화면에서 재사용하는 UI/helper
```

Frontend 규칙:

- `App.jsx`는 전역 shell만 담당한다.
- 새 탭은 `config.js`와 `pageRegistry.jsx`를 같이 본다.
- API 호출은 가능한 한 `src/lib/api.js`의 helper를 사용한다.
- 페이지 파일이 커지면 새 기능을 붙이기 전에 component/hook/helper를 먼저 뺀다.
- 공통 UI만 `components` 또는 향후 `shared`로 올린다.

## Data Roots

데이터 루트는 `backend/core/roots.py`와 `backend/core/paths.py`를 통해 해석한다.

| 루트 | 의미 |
|---|---|
| `db_root` | raw/derived parquet, matching CSV, rulebook, `ML_TABLE_*.parquet` |
| `base_root` | 호환 alias. 현재는 `db_root`와 같은 경로 |
| `wafer_map_root` | 기본값은 `<db_root>/wafer_maps` |
| `data_root` | 사용자/운영 상태. users, settings, tracker, informs, meetings, calendar 등 |

우선순위:

```text
FLOW_DB_ROOT
  -> runtime profile/admin_settings data_roots.db
  -> /config/work/sharedworkspace/DB when shared defaults are active
  -> data/Fab
```

`FLOW_DATA_ROOT`는 운영 상태 저장 위치를 직접 지정한다. 코드 업데이트, `setup.py`, frontend build는 이 경로를 삭제하거나 덮어쓰면 안 된다.

## Source Model

FAB canonical 공정 이력:

- `root_lot_id`, `lot_id`, `wafer_id`
- `line_id`, `process_id`, `step_id`
- `tkin_time`, `tkout_time`
- `eqp_id`, `chamber_id`, `reticle_id`, `ppid`

ET canonical shot-level numerical schema:

- `root_lot_id`, `lot_id`, `wafer_id`
- `process_id`, `step_id`, `step_seq`
- `eqp_id`, `probe_card`
- `tkin_time`, `tkout_time`, `flat_zone`
- `item_id`, `shot_x`, `shot_y`, `value`

INLINE canonical shot-level numerical schema:

- `root_lot_id`, `lot_id`, `wafer_id`
- `process_id`, `tkin_time`, `tkout_time`, `eqp_id`
- `subitem_id`, `item_id`, `value`
- `speclow`, `target`, `spechigh`

`INLINE`의 `subitem_id`는 shot 위치 key다. 실제 좌표는 `inline_item_map.csv`와 `inline_subitem_pos.csv`를 통해 item별 mapping으로 해석한다.

ML_TABLE wide schema:

- 파일명: `ML_TABLE_<PRODUCT>.parquet`
- 위치: DB root 최상단
- grain: wafer 단위
- 식별자: `PRODUCT`, `ROOT_LOT_ID`, `WAFER_ID`
- feature prefix: `KNOB_XX`, `INLINE_XX`, `MASK_XX`, `FAB_XX`, `VM_XX`, `QTIME_XX`

## Domain Model

`flow`는 반도체 공정 지식을 코드에 고정하기보다 rulebook, matching CSV, product YAML, adapter/profile로 관리한다.

핵심 용어:

- `step_id`: 제품별 raw 공정 step 식별자
- `function_step`: 제품 간 비교 가능한 기능 공정명
- `ppid`: recipe/process plan code
- `knob`: 실험 split 또는 engineer-defined 분류
- `eqp_chamber`: SPC에서 의미 있는 최소 장비 단위

공정 영역 순서:

```text
STI -> Well/VT -> PC -> Gate -> Spacer -> S/D Epi -> MOL -> BEOL-M1..Mn
```

ML 해석 원칙:

- 앞 공정은 뒤 공정에 영향을 줄 수 있다.
- 뒤 공정이 앞 공정 원인으로 잡히면 의심 플래그가 필요하다.
- 같은 area, 직전 area, 형상 전사 경로는 신뢰도가 높다.
- 먼 BEOL feature가 FEOL target 원인으로 잡히면 통계적 상관으로 우선 의심한다.

## Auth And Operations

- 모든 `/api/*`는 session token 기반 인증을 통과해야 한다.
- Admin API는 `require_admin` 경계를 유지한다.
- 사용자/그룹 가시성은 tracker, inform, meeting, calendar에서 유지한다.
- 백업, 알림, scheduler는 사용자 요청과 같은 파일을 건드릴 수 있으므로 atomic write와 lock을 우선한다.
- `/runtime-roots.json`은 운영자가 현재 checkout/root 해석을 확인하는 비인증 진단 endpoint다.

## Migration Direction

1. `Inform`: contacts, mail, SplitTable embed 분리
2. `SplitTable`: notes, rulebook, scan adapter 분리
3. `Dashboard`: chart config, snapshot scheduler, fab progress 분리
4. `Meeting`: mail/calendar push 분리
5. `Admin`: panel 단위 분리
