# Architecture

`flow`는 FastAPI 백엔드와 React/Vite 프론트엔드로 구성된 단일 웹 앱이다. 현재 목표는 전면 재작성이 아니라, 동작 중인 앱을 유지하면서 큰 라우터와 큰 페이지를 점진적으로 분리하는 것이다.

## Top Level

```text
flow/
├── app.py                 # uvicorn shim
├── backend/
├── frontend/
├── data/                  # demo/runtime data
├── docs/                  # current essential docs only
├── scripts/               # smoke, migration, seed scripts
└── archive/               # legacy docs and retired collaboration files
```

## Backend

현재 진입점:

- `backend/app.py`: FastAPI app assembly, runtime hook 호출, static serving
- `backend/routers/*.py`: existing HTTP endpoints
- `backend/core/*.py`: shared legacy utilities
- `backend/app_v2/*`: 점진적 이관 대상

목표 구조:

```text
backend/app_v2/
├── runtime/
│   ├── security.py
│   ├── router_loader.py
│   └── startup.py
├── shared/
│   ├── json_store.py
│   ├── result.py
│   └── source_adapter.py
└── modules/<feature>/
    ├── domain.py
    ├── repository.py
    └── service.py
```

규칙:

- Router는 request/response와 auth만 담당한다.
- Service는 유스케이스를 담당한다.
- Repository는 JSON/CSV/parquet/S3 접근을 담당한다.
- Domain은 validation과 업무 규칙을 담당한다.
- 새 저장 로직은 `JsonFileStore` 같은 공통 저장 계층을 거친다.
- App runtime은 인증, 라우터 로딩, 스케줄러/seed 초기화만 담당하고 feature 로직을 갖지 않는다.

## Frontend

현재 진입점:

- `frontend/src/App.jsx`: shell, tabs, global modal, error boundary
- `frontend/src/app/pageRegistry.jsx`: tab key to page component mapping
- `frontend/src/app/useFlowShell.js`: auth/theme/tab/notification shell state
- `frontend/src/pages/My_*.jsx`: existing pages
- `frontend/src/lib/api.js`: shared authenticated fetch
- `frontend/src/components/UXKit.jsx`: shared UI primitives

목표 구조:

```text
frontend/src/
├── app/
│   ├── pageRegistry.jsx
│   └── useFlowShell.js
├── pages/
├── features/<feature>/
│   ├── api.js
│   ├── hooks.js
│   ├── components/
│   └── utils.js
└── shared/
    ├── api/
    ├── ui/
    ├── hooks/
    └── utils/
```

규칙:

- 페이지는 조립만 담당한다.
- API 호출은 `sf`, `postJson`, `dl`을 사용한다.
- 기능별 상태와 모달은 `features/<feature>`로 뺀다.
- `App.jsx`는 라우팅과 전역 shell만 담당한다.
- shell 상태(auth/theme/tab/notification)는 `app/useFlowShell.js`에 둔다.

## Data Model

FAB canonical 공정이력:

- `root_lot_id`
- `lot_id`
- `wafer_id`
- `line_id`
- `process_id`
- `step_id`
- `tkin_time` / `tkout_time`
- `eqp_id`
- `chamber_id`
- `reticle_id`
- `ppid`

FAB는 wafer 단위 데이터다. 한 row는 특정 wafer가 특정 step에서 어떤 line/process, eqp/chamber, reticle, ppid로 들어가고 나온 이력을 표현한다. 구 샘플의 `eqp`, `chamber`, `time` alias는 런타임에서 `eqp_id`, `chamber_id`, `tkout_time/time`으로 정규화한다.

계측 원천 canonical 축:

ET canonical shot-level numerical schema:

- `root_lot_id`
- `lot_id`
- `wafer_id`
- `process_id`
- `step_id`
- `step_seq`
- `eqp_id`
- `probe_card`
- `tkin_time` / `tkout_time`
- `flat_zone`
- `item_id`
- `shot_x` / `shot_y`
- `value`

INLINE canonical shot-level numerical schema:

- `root_lot_id`
- `lot_id`
- `wafer_id`
- `process_id`
- `tkin_time` / `tkout_time`
- `eqp_id`
- `subitem_id`
- `item_id`
- `value`
- `speclow` / `target` / `spechigh`

INLINE은 `subitem_id`가 shot 위치 key다. 실제 `shot_x/shot_y`는 `inline_item_map.csv`에서 item별 `map_id`를 찾고, `inline_subitem_pos.csv`에서 `(map_id, subitem_id)`로 변환한다. 따라서 같은 `subitem_id`라도 item별 matching table이 다를 수 있다.

계측 호환 alias:

- `product`
- `step_id`
- `step_seq`
- `tkin_time` / `tkout_time` / `time`
- `item_id` / `subitem_id`
- `value`

원천별 의미:

- FAB: wafer 단위 공정 이력
- INLINE: shot/subitem 기반 계측
- ET: shot 좌표, step_seq, request/package 기반 전기 측정
- ML_TABLE: 원천이 아니라 wafer-level derived feature layer

ML_TABLE wide schema:

- 파일명: `ML_TABLE_<PRODUCT>.parquet`
- 위치: 파일탐색기에서 보이는 현재 DB 루트 최상단
- grain: wafer 단위, 한 row가 한 wafer
- 식별자: `PRODUCT`, `ROOT_LOT_ID`, `WAFER_ID` (`LOT_ID`는 테스트/호환용으로 포함 가능)
- feature prefix: `KNOB_XX`, `INLINE_XX`, `MASK_XX`, `FAB_XX`, `VM_XX`, `QTIME_XX`
- `XX`: function step 이름. 예: `1.0 STI`, `2.0 WELL`

운영 원천은 prefix별 수천 개 컬럼이 될 수 있다. 테스트 데이터는 prefix별 약 20~30개 컬럼 규모로 생성한다.

## Domain Model

`flow`는 반도체 공정 지식을 코드에 하드코딩하기보다 rulebook과 registry로 관리한다.

핵심 용어:

- `step_id`: 제품별 raw 공정 step 식별자
- `function_step`: 여러 제품에서 비교 가능한 기능 공정명
- `ppid`: 공정 recipe/process plan code
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

DVC 방향성:

- `Rc`, `Ioff`, `lkg`: lower is better
- `Ion`: higher is better
- `Rch`, `Vth`: target centered
- `ACint`: lower is better
- `AChw`: context dependent

이 정보는 Dashboard, ML, SplitTable, matching table UI에서 해석 보조 정보로 쓰인다.

## Roots

데이터 루트는 환경마다 다를 수 있으므로 resolver를 통해 해석한다.

- DB root: raw parquet/csv
- Base root: matching/rulebook/derived table
- Runtime data: users, settings, tracker, informs, meetings, calendar

실데이터 차이는 adapter/profile에서 흡수한다. 코드 안에서 특정 파일명이나 컬럼 대소문자를 강하게 가정하지 않는다.

## Auth And Operations

- 모든 `/api/*`는 session token 기반 인증을 통과해야 한다.
- Admin API는 `require_admin`이 필요하다.
- 사용자/그룹 가시성은 tracker, inform, meeting, calendar에서 유지해야 한다.
- 백업, 알림, scheduler는 사용자 요청과 같은 파일을 건드릴 수 있으므로 저장 계층의 lock/atomic write가 중요하다.

## Migration Direction

1. `Inform`: contacts, mail, SplitTable embed 분리
2. `SplitTable`: notes, rulebook, scan adapter 분리
3. `Dashboard`: chart config, snapshot scheduler, fab progress 분리
4. `Meeting`: mail/calendar push 분리
5. `Admin`: panel 단위 분리
