# Web Concurrency And Bundle Guide

작성일: 2026-04-28

이 문서는 최근 구조 이관, 동시성 처리, 프론트엔드 번들 분리 이후 `flow`가 어떻게 바뀌었는지와 웹 화면의 각 단위 기능을 어떻게 쓰는지 설명한다.

## 한 줄 요약

이제 웹 앱은 로그인, 메뉴, 알림 같은 공통 shell을 먼저 띄우고, 사용자가 실제로 들어간 페이지 코드만 따로 불러온다. 탭 전환은 React transition으로 처리해 화면 shell 반응성을 유지하고, 각 업무 화면은 기존 API와 데이터 저장 구조를 그대로 사용한다.

## 무엇이 바뀌었나

### 1. Shell과 Page가 분리됨

기존에는 `App.jsx`가 모든 페이지 컴포넌트를 한 번에 import하는 구조였다. 지금은 shell과 페이지 로딩 책임이 아래처럼 나뉘었다.

| 영역 | 변경 전 | 변경 후 |
|---|---|---|
| `frontend/src/App.jsx` | 모든 페이지를 직접 렌더링 | 로그인, nav, 공지, 문의, 알림, 에러 경계, lazy page mount 담당 |
| `frontend/src/app/pageRegistry.jsx` | 모든 `My_*` 페이지를 정적 import | `React.lazy(() => import(...))`로 페이지별 bundle 분리 |
| `frontend/src/app/useFlowShell.js` | `setTab()`으로 즉시 탭 변경 | `startTransition()`으로 탭 변경을 낮은 우선순위 작업으로 처리 |

핵심 파일:

- `frontend/src/App.jsx`
- `frontend/src/app/pageRegistry.jsx`
- `frontend/src/app/useFlowShell.js`
- `frontend/src/config.js`

### 2. 번들이 페이지 단위로 나뉨

`pageRegistry.jsx`의 `PAGE_MAP`은 tab key와 실제 페이지를 연결한다.

```jsx
home: lazy(() => import("../pages/My_Home")),
splittable: lazy(() => import("../pages/My_SplitTable")),
inform: lazy(() => import("../pages/My_Inform")),
admin: lazy(() => import("../pages/My_Admin")),
```

이 구조에서는 앱 최초 진입 시 모든 대형 페이지 코드를 한꺼번에 가져오지 않는다. 사용자가 `SplitTable` 탭을 누르면 `My_SplitTable` bundle을, `Inform` 탭을 누르면 `My_Inform` bundle을 그때 가져온다.

사용자 입장에서 달라지는 점:

- 첫 화면은 공통 shell 중심으로 더 가볍게 시작한다.
- 처음 여는 탭은 `페이지 로딩...` fallback이 잠깐 보일 수 있다.
- 이미 로드된 탭은 브라우저 캐시에 남아 다시 진입할 때 더 빠르게 열린다.
- 특정 페이지 로딩 중 에러가 나도 `ErrorBoundary`가 해당 페이지 영역에서 잡아 앱 전체가 죽는 상황을 줄인다.

### 3. 탭 전환은 transition으로 처리됨

`useFlowShell.js`의 `nav()`는 `startTransition()`으로 tab state를 바꾼다.

```jsx
startTransition(() => setTab(tabKey));
```

의미:

- 메뉴 클릭, 알림, 프로필 메뉴 같은 shell UI 반응을 우선 유지한다.
- 무거운 페이지가 mount되는 동안 브라우저가 급하게 모든 렌더링을 막지 않게 한다.
- 업무 데이터 API나 저장 방식은 바뀌지 않는다.

주의할 점:

- 이것은 서버 동시 쓰기 lock이나 데이터 충돌 해결 기능이 아니다.
- 저장 충돌, JSON atomic write, 권한 검사는 백엔드 service/repository에서 별도로 다뤄야 한다.
- 페이지 내부에서 여러 API를 동시에 부르는 작업은 각 페이지의 `Promise.all` 또는 개별 hook 책임이다.

### 4. 기존 API와 데이터 구조는 유지됨

이번 작업은 프론트엔드 로딩 구조를 바꾼 것이다. 아래 항목은 그대로 유지된다.

- `/api/*` endpoint 이름과 응답 shape
- `data/flow-data` runtime/user state
- `data/Fab` 또는 운영 `FLOW_DB_ROOT` 원천 데이터
- 사용자 권한, 탭 가시성, admin gate
- SplitTable snapshot이 Inform에 붙는 흐름

## 현재 웹 구조

```text
frontend/src/
├── App.jsx                  # 로그인 이후 전체 shell
├── config.js                # 탭 목록, 권한, beta/admin 설정
├── app/
│   ├── pageRegistry.jsx     # tab key -> lazy page bundle
│   └── useFlowShell.js      # auth/theme/tab/session/notification shell state
├── pages/
│   ├── My_*.jsx             # 실제 업무 화면 entry
│   └── SplitTable/          # SplitTable에서 먼저 분리된 local pieces
├── components/              # BrandLogo, Modal, Loading, UXKit 등 공통 UI
└── lib/api.js               # 인증 fetch, JSON post/download helper
```

새 탭을 추가할 때 보는 순서:

1. `frontend/src/config.js`에 탭 key, label, icon, 권한을 추가한다.
2. `frontend/src/app/pageRegistry.jsx`에 같은 key로 lazy import를 추가한다.
3. `frontend/src/pages/My_<Name>.jsx`를 만들거나 기존 local folder를 연결한다.
4. 필요한 API는 `frontend/src/lib/api.js` helper를 우선 사용한다.

## 공통 웹 기능 사용법

| 기능 | 위치 | 사용법 |
|---|---|---|
| 로그인 | 첫 화면 | 계정으로 로그인하면 `localStorage.hol_user`에 session token이 저장되고 shell이 열린다. |
| 탭 이동 | 상단 nav | 탭을 누르면 `useFlowShell.nav()`가 권한을 확인하고 해당 page bundle을 lazy load한다. |
| 문의/공지 | 우상단 mail 아이콘 | 사용자는 관리자에게 문의를 보내고, admin은 받은 문의 답장과 공지 작성이 가능하다. |
| 알림 | 우상단 bell | tracker/admin 알림을 확인하고 dismiss한다. 30초 주기로 새로고침된다. |
| 프로필 | 우상단 사용자명 | 다크/라이트 모드 전환, 비밀번호 변경, 로그아웃을 한다. |
| 공지 배너 | nav 아래 | 최신 공지를 3일 TTL로 보여주며 사용자가 닫을 수 있다. |
| 에러 경계 | 페이지 영역 | 한 페이지 JavaScript 에러를 해당 페이지 영역에서 잡고 재시도 버튼을 보여준다. |

## 웹 단위 기능 사용법

### Home

용도: 로그인 후 현재 버전, 최근 변경, 공지, 문의 진입을 빠르게 확인한다.

사용 흐름:

1. 로그인한다.
2. 홈에서 버전과 최근 변경 내용을 본다.
3. 우상단 문의/공지 또는 알림으로 운영 메시지를 확인한다.

주요 파일:

- `frontend/src/pages/My_Home.jsx`
- `backend/routers/home.py`
- `VERSION.json`

### FileBrowser

용도: DB root의 파일, 컬럼, parquet/CSV preview, SQL filter, S3 동기화 상태를 확인한다.

사용 흐름:

1. 파일탐색기 탭을 연다.
2. DB root 또는 scope를 선택한다.
3. 파일 목록에서 parquet/CSV/JSON 파일을 고른다.
4. 컬럼, head preview, SQL filter 결과를 확인한다.
5. S3 설정이 필요한 경우 admin/root 설정과 S3 상태를 함께 확인한다.

주요 파일:

- `frontend/src/pages/My_FileBrowser.jsx`
- `backend/routers/filebrowser.py`
- `backend/core/utils.py`

### Dashboard

용도: SPC성 chart, fab progress, alert watch, snapshot을 한 화면에서 본다.

사용 흐름:

1. 대시보드 탭을 연다.
2. product, chart, time window 또는 progress 조건을 선택한다.
3. 차트와 progress를 확인한다.
4. 필요한 경우 snapshot을 저장하거나 기존 snapshot을 불러온다.
5. admin은 Dashboard section visibility를 조정할 수 있다.

주요 파일:

- `frontend/src/pages/My_Dashboard.jsx`
- `backend/routers/dashboard.py`

### SplitTable

용도: product, root/fab lot, wafer 기준으로 plan, actual, diff, notes, rulebook 매핑을 관리한다.

사용 흐름:

1. 스플릿 테이블 탭을 연다.
2. product를 선택한다.
3. root lot 또는 fab lot 후보를 고른다.
4. wafer matrix에서 plan, actual, diff를 확인한다.
5. 필요한 plan/notes를 저장하고, export가 필요하면 XLSX로 내려받는다.
6. Inform에서 snapshot으로 붙일 때는 SplitTable의 root/fab/wafer 표시와 같은 기준을 사용한다.

주요 파일:

- `frontend/src/pages/My_SplitTable.jsx`
- `frontend/src/pages/SplitTable/PlanPanel.jsx`
- `frontend/src/pages/SplitTable/_helpers.js`
- `backend/routers/splittable.py`

연결 테이블과 룰북 구조:

- 위치: 모두 `db_root` 최상단 CSV가 운영 기준이다. `base_root`는 호환 alias라 같은 위치를 본다.
- 형식: UTF-8 CSV, 첫 줄 header 필수, product 값은 제품 alias까지 고려하되 운영 파일에서는 대문자 표기를 권장한다.
- 키: 같은 product 안에서 연결 키가 중복되면 마지막 수정자가 의도한 행을 알 수 없으므로 중복을 피한다. 공용 행이 필요한 경우 product를 비워둘 수 있다.
- schema override: 사내 CSV 컬럼명이 다르면 `data_root/splittable/rulebook_schema.json`에 역할별 컬럼명을 저장한다. 기본 역할은 `/api/splittable/rulebook/schema`와 Admin/SplitTable 연결 규칙 화면에서 확인한다.

| 파일 | 역할 | 필수 컬럼 | 선택 컬럼/규칙 |
|---|---|---|---|
| `step_matching.csv` | FAB step_id를 기능 공정으로 정규화 | `product`, `step_id`, `func_step` | `module`, `step_class`, `measure_domain`, `main_function_step`, `is_active`, `valid_from`, `valid_to`, `priority`, `note`. 키는 `product + step_id`. |
| `matching_step.csv` | adapter/catalog용 raw step registry | `product`, `raw_step_id`, `canonical_step`, `step_type` | `area`, `function_step`, `step_class`, `measure_domain`, `main_function_step`. `step_type`은 보통 `main` 또는 `meas`. |
| `knob_ppid.csv` | PPID와 기능 공정을 KNOB feature로 연결 | `product`, `ppid`, `feature_name`, `function_step` | split 축 산출에는 `knob_name`, `knob_value`를 같이 둔다. 복합 rule은 `rule_order`, `operator`, `category`, `use`를 쓰고 `use=N`은 제외한다. |
| `inline_matching.csv` | `INLINE_<item_id>` feature를 측정 step에 연결 | `product`, `item_id`, `step_id` | `process_id`, `item_desc`, `function_step`. 한 item이 여러 step에 걸치면 행을 나눠 쓴다. |
| `vm_matching.csv` | `VM_<feature_name>` feature를 예측/측정 step에 연결 | `product`, `feature_name`, `step_id` | `step_desc`, `function_step`. 한 feature가 여러 step에 걸치면 행을 나눠 쓴다. |
| `inline_step_match.csv` | INLINE raw step_id를 canonical 측정 step으로 정규화 | `product`, `raw_step_id`, `canonical_step` | `step_class`, `measure_domain`, `main_function_step`를 둘 수 있다. |
| `inline_item_map.csv` | INLINE item을 canonical item과 좌표 map으로 연결 | `product`, `item_id`, `canonical_item` | 좌표 변환까지 쓰면 `map_id`가 필요하다. step별 item명이 겹치면 `step_id`를 추가한다. |
| `inline_subitem_pos.csv` | INLINE subitem을 ET shot 좌표로 연결 | `map_id`, `subitem_id`, `shot_x`, `shot_y` | `shot_x`, `shot_y`는 ET 좌표계 정수값이다. 기존 `item_id` 기반 파일은 `map_id=item_id` 형태로 맞춰서 사용한다. |
| `mask.csv` | reticle_id를 MASK version/vendor로 연결 | `product`, `reticle_id`, `mask_version` | `mask_vendor`, `photo_step`. |

### Tracker

용도: 개발 이슈, lot watch, category, group visibility, 메일/ET watch를 이슈 단위로 관리한다.

사용 흐름:

1. 이슈 추적 탭을 연다.
2. 새 이슈를 만들고 category, priority, 상태, 공개 그룹을 지정한다.
3. 관련 lot/wafer watch를 FAB 또는 ET source 의미에 맞게 추가한다.
4. 댓글, 첨부, 메일 설정, ET watch 조건을 관리한다.
5. 상태 변경은 Calendar/Meeting 연동 대상이 될 수 있다.

주요 파일:

- `frontend/src/pages/My_Tracker.jsx`
- `backend/routers/tracker.py`
- `backend/app_v2/modules/tracker/`

### Inform

용도: 제품, lot, wafer 단위 이슈를 담당자에게 전달하고 후속 thread로 남긴다.

사용 흐름:

1. 인폼 로그 탭을 연다.
2. product, lot, wafer, module/reason chip을 지정한다.
3. 담당자, 마감, 상태, 이미지나 설명을 입력한다.
4. 필요하면 SplitTable snapshot을 붙인다.
5. 메일 미리보기 후 발송하거나 thread 기록만 저장한다.

주요 파일:

- `frontend/src/pages/My_Inform.jsx`
- `backend/routers/informs.py`
- `backend/routers/informs_extra.py`
- `backend/app_v2/modules/informs/`

### Meeting

용도: 회의 차수, agenda, minutes, 결정사항, 액션아이템을 운영 기록으로 남긴다.

사용 흐름:

1. 회의관리 탭을 연다.
2. 회의 차수와 agenda를 만든다.
3. Tracker 이슈를 가져오거나 직접 agenda를 작성한다.
4. minutes, decision, action item을 저장한다.
5. 필요한 경우 메일 발송 또는 Calendar push 흐름을 사용한다.

주요 파일:

- `frontend/src/pages/My_Meeting.jsx`
- `backend/routers/meetings.py`
- `backend/app_v2/modules/meetings/`

### Calendar

용도: tracker, meeting action, decision, 자체 변경점의 날짜와 상태를 월 grid로 본다.

사용 흐름:

1. 변경점 관리 탭을 연다.
2. 월별 grid에서 pending, in progress, done 상태를 확인한다.
3. 자체 이벤트를 만들거나 linked action의 상태를 바꾼다.
4. 원본 entity와 상태가 동기화되는지 확인한다.

주요 파일:

- `frontend/src/pages/My_Calendar.jsx`
- `backend/routers/calendar.py`

### ET Report

용도: product/lot 검색에서 출발해 ET measurement package, step_seq, reformatter index, PPTX 리포트를 관리한다.

사용 흐름:

1. ET 레포트 탭을 연다.
2. product와 lot을 검색한다.
3. measurement package와 `step_seq(XXpt)` 구성을 확인한다.
4. reformatter index별 Statistical Table, Box Table, WF Map, Trend 등을 본다.
5. 필요하면 PPTX 또는 리포트 bundle을 생성한다.

주요 파일:

- `frontend/src/pages/My_ETTime.jsx`
- `backend/routers/ettime.py`
- `backend/routers/reformatter.py`

### Wafer Layout

용도: wafer, shot, chip, TEG 좌표 기반으로 공간 패턴을 본다.

사용 흐름:

1. 웨이퍼 레이아웃 탭을 연다.
2. product, wafer, shot/chip 범위를 선택한다.
3. Shot Sample 안에서 TEG를 검색하거나 선택한다.
4. Chip View에서 chip이 속한 shot 표를 확인한다.
5. 필요하면 CSV로 내려받는다.

주요 파일:

- `frontend/src/pages/My_WaferLayout.jsx`
- `backend/routers/waferlayout.py`
- `backend/core/wafer_geometry.py`

### TableMap

용도: 작은 lookup/base table, relation hint, product YAML block을 관리한다.

사용 흐름:

1. admin 권한으로 테이블맵 탭을 연다.
2. table node와 relation을 확인한다.
3. 작은 CSV table은 편집/version 관리한다.
4. Product Connection page에서 product YAML block을 추가, 삭제, 숨김, 복원한다.
5. 대용량 분석이나 raw preview는 FileBrowser/Dashboard로 넘긴다.

주요 파일:

- `frontend/src/pages/My_TableMap.jsx`
- `backend/routers/dbmap.py`

### ML

용도: Y에 대한 feature 영향 후보를 뽑고 도메인 방향성과 함께 해석한다.

사용 흐름:

1. ML 분석 탭을 연다.
2. product, target Y, feature source, model 후보를 선택한다.
3. TabICL/XGBoost/LightGBM 실행 결과를 비교한다.
4. SHAP/feature importance를 공정 area와 direction 신뢰도와 함께 본다.
5. 결과는 원인 확정이 아니라 후보로 해석한다.

주요 파일:

- `frontend/src/pages/My_ML.jsx`
- `backend/routers/ml.py`
- `backend/core/ml_heuristics.py`

### Messages

용도: 사용자와 admin 사이의 1:1 문의와 공지를 앱 내부 기록으로 남긴다.

사용 흐름:

1. 우상단 mail 아이콘을 누른다.
2. 사용자는 `내 문의`에서 메시지를 보낸다.
3. admin은 `받은 문의`에서 사용자별 thread에 답장한다.
4. admin은 `공지 작성`에서 공지를 등록하고, 사용자는 `공지`에서 확인한다.

주요 파일:

- `frontend/src/App.jsx`
- `backend/routers/messages.py`

### Admin

용도: 사용자, 권한, 그룹, root, 백업, 메일/API, monitor, Base CSV 같은 운영 설정을 관리한다.

사용 흐름:

1. admin 권한으로 관리자 탭을 연다.
2. 사용자와 탭 권한을 관리한다.
3. data root, backup, monitor, mail/API 설정을 확인한다.
4. Dashboard section visibility, product/root 설정 등 운영 설정을 바꾼다.
5. 변경 전후 진단 메시지와 백업 상태를 확인한다.

주요 파일:

- `frontend/src/pages/My_Admin.jsx`
- `backend/routers/admin.py`
- `backend/core/roots.py`
- `backend/core/backup.py`

### DevGuide

용도: 앱 내부에서 현재 구조와 주요 API를 빠르게 확인한다.

사용 흐름:

1. admin 설정에서 DevGuide 표시를 허용한다.
2. 개발자 가이드 탭을 연다.
3. 현재 구조, API, 수정 기준을 빠르게 확인한다.
4. 긴 운영 문서는 repo의 `docs/` 문서를 기준으로 본다.

주요 파일:

- `frontend/src/pages/My_DevGuide.jsx`
- `docs/DEVELOPMENT.md`
- `docs/ARCHITECTURE.md`

## 기능 수정 시 기준

| 수정 대상 | 먼저 볼 파일 | 원칙 |
|---|---|---|
| 탭 추가/삭제 | `config.js`, `pageRegistry.jsx` | key가 두 파일에서 일치해야 한다. |
| shell 상태 | `useFlowShell.js` | auth/theme/tab/session/notification만 둔다. |
| page bundle | `pageRegistry.jsx` | 새 페이지도 lazy import로 등록한다. |
| 페이지 내부 기능 | `pages/My_*.jsx` 또는 local folder | 커지면 component/hook/helper로 분리한다. |
| 공통 UI | `components/*` | 여러 페이지에서 재사용될 때만 올린다. |
| API 호출 | `lib/api.js` | 인증 fetch helper를 우선 사용한다. |
| 저장/업무 규칙 | `backend/app_v2/modules/<feature>` | domain/repository/service로 나눈다. |

## 검증 기준

문서 또는 프론트 구조 변경 후 최소 확인:

```bash
git diff --check
cd frontend && npm run build
```

서버가 떠 있는 상태에서 핵심 smoke:

```bash
python scripts/smoke_test.py
```

백엔드 저장/업무 규칙을 바꾼 경우:

```bash
python -m pytest tests
```

## 소유자 전달용 요약

- 구조 변화: shell, page registry, page bundle을 분리했고 탭 전환은 React transition으로 처리한다.
- 사용 변화: 사용자는 기존과 같은 탭을 쓰지만, 각 페이지 코드는 해당 탭 진입 시 lazy load된다.
- 영향 범위: API, 데이터 root, 사용자 권한, SplitTable to Inform snapshot 흐름은 그대로 유지된다.
- 개발 기준: 새 탭은 `config.js`와 `pageRegistry.jsx`를 같이 수정하고, 페이지가 커지면 local component/hook/helper로 먼저 나눈다.
