# flow 소유자용 아키텍처 및 업데이트 요청 보고서

작성일: 2026-04-26
대상 독자: 반도체 공정/개발/분석 도메인 담당자, 앱 기획자, IT 전공자가 아닌 앱 소유자

## 1. 한 줄 요약

`flow`는 lot/wafer 중심으로 공정 데이터, 실험 plan, actual, 이슈, 인폼, 회의, 액션아이템을 이어 보는 업무 앱이다.

동시성/번들 분리 이후 웹 구조 변화와 화면별 사용법은 [WEB_CONCURRENCY_BUNDLE_GUIDE.md](WEB_CONCURRENCY_BUNDLE_GUIDE.md)에 따로 정리했다.

이번 정리의 핵심은 아래 네 가지다.

1. 화면은 조회와 판단에 집중한다.
2. 계산, 저장, 변환, 권한, 알림은 백엔드에서 처리한다.
3. 로그인 상태, 알림 상태, 업무 데이터 상태, 설정 상태를 한곳에 섞지 않고 따로 관리한다.
4. 시스템 업데이트와 사용자 데이터(`data_root`)를 분리해, 앱을 교체해도 기존 운영 데이터는 그대로 읽고 필요하면 백업으로 롤백한다.

반도체 업무에 비유하면, 프론트엔드는 control room 화면이고 백엔드는 실제 data handling, rule check, report generation을 수행하는 자동화/분석실이다. 데이터 파일은 lot traveler, recipe table, monitor log처럼 각각의 목적에 맞게 따로 보관한다.

## 2. 왜 구조를 바꿨나

이전 구조는 동작은 하지만 큰 화면 파일과 큰 라우터 파일에 많은 책임이 모여 있었다.

예를 들어 한 화면 파일 안에 아래 항목이 같이 들어가면, 기능을 하나 고칠 때 다른 기능까지 영향을 받을 수 있다.

- 버튼과 표를 그리는 코드
- 서버에 데이터를 요청하는 코드
- 모달을 여닫는 상태
- 로그인/권한 상태
- 알림 상태
- 저장 파일을 바꾸는 규칙

이번 정리는 "한 번에 새로 만들기"가 아니라, 현재 앱을 유지하면서 관리가 쉬운 방향으로 나눈 것이다.

목표는 IT 관점의 예쁜 구조가 아니라, 앞으로 사용자가 이런 식으로 요청했을 때 안전하게 고칠 수 있게 하는 것이다.

- "SplitTable에서 특정 컬럼만 더 보여줘"
- "Inform 메일 본문에 wafer별 표를 조금 다르게 넣어줘"
- "Dashboard 차트 설정을 저장하고 다시 불러오게 해줘"
- "Tracker에서 lot watch 상태를 ET 기준과 FAB 기준으로 따로 보게 해줘"
- "Admin 설정은 사용자 화면과 섞이지 않게 해줘"

## 3. 현재 앱의 큰 그림

`flow`는 크게 5개 영역으로 보면 된다.

```text
사용자 화면
  -> frontend

업무 처리 API
  -> backend/routers

업무 로직과 저장 처리
  -> backend/app_v2/modules

공통 저장/데이터 해석 도구
  -> backend/app_v2/shared, backend/core

실제 데이터와 런타임 상태
  -> data/Fab, data/flow-data
```

각 영역의 역할은 다음과 같다.

| 영역 | 반도체 업무식 설명 | 실제 역할 |
|---|---|---|
| `frontend` | control room 화면 | 표, 차트, 입력 폼, 모달, 네비게이션 |
| `backend/routers` | 창구 담당자 | 화면 요청을 받아 적절한 서비스로 전달 |
| `backend/app_v2/modules` | 담당 엔지니어/분석 담당자 | 기능별 판단, 저장, 변경 이력 처리 |
| `backend/app_v2/shared` | 공용 장비/공용 양식 | JSON 저장, 결과 포맷, source adapter |
| `backend/app_v2/runtime` | 앱 기동/보안/스케줄러 | 로그인 검사, 라우터 로딩, 백그라운드 작업 시작 |
| `backend/core` | 기존 공통 유틸 | 경로, 인증, 메일, 알림, domain helper |
| `data/Fab` | 원천 fab/inline/ET 데이터 | parquet/csv 기반 raw 또는 derived data |
| `data/flow-data` | 앱 운영 기록 | 사용자, 설정, tracker, informs, meetings, calendar |
| `archive` | 폐기/보관 창고 | 현재 실행에 필요 없는 과거 문서와 retired 파일 |

사내 반입 기준 포트는 `flow=8080`, `OmniHarness=8081`이다. `db_root`와 `base_root`는 같은 경로를 가리키며, 별도 Base 루트는 더 이상 운영 기준이 아니다. 자세한 사내 점검 절차는 `SOFT_LANDING_INTERNAL.md`를 기준으로 한다.

## 4. 프론트엔드 관리 구조

프론트엔드는 사용자가 보는 화면이다. 이번 방향은 "프론트는 보여주고, 선택하게 하고, 결과를 확인하게 하는 데 집중한다"이다.

현재 주요 구조는 이렇다.

```text
frontend/src/
├── App.jsx
├── app/
│   ├── pageRegistry.jsx
│   └── useFlowShell.js
├── pages/
├── components/
├── constants/
└── lib/api.js
```

### 4.1 `App.jsx`

`App.jsx`는 앱의 바깥 shell이다.

담당하는 일:

- 상단 메뉴 표시
- 현재 선택된 탭 표시
- 페이지를 바꿔 끼우기
- 전역 모달 일부 표시
- 페이지 에러를 잡아서 앱 전체가 죽지 않게 하기

담당하지 않아야 하는 일:

- 각 페이지의 복잡한 계산
- 데이터 파일 직접 저장
- 특정 업무 기능의 세부 상태
- 차트 계산이나 table 변환

즉, `App.jsx`는 공장 전체의 복잡한 공정 조건을 들고 있는 곳이 아니라, 어느 화면으로 들어갈지 안내하는 메인 패널에 가깝다.

### 4.2 `frontend/src/app/pageRegistry.jsx`

이 파일은 "탭 이름과 실제 페이지 파일을 연결하는 표"다.

예를 들어 `dashboard` 탭을 누르면 `My_Dashboard.jsx`를 보여주는 식이다.

새 페이지를 만들 때는 보통 두 군데를 본다.

- `frontend/src/config.js`: 메뉴에 어떤 탭이 있는지
- `frontend/src/app/pageRegistry.jsx`: 그 탭이 어떤 화면 파일을 열지

### 4.3 `frontend/src/app/useFlowShell.js`

이 파일은 전역 상태를 관리한다.

현재 여기로 분리된 상태:

- 로그인한 사용자
- 현재 탭
- 다크/라이트 모드
- 알림 목록
- 사용자 권한에 따른 보이는 탭
- 세션 만료 처리

이렇게 분리한 이유는 각 업무 페이지가 로그인/알림/테마 같은 공통 상태까지 같이 들고 있으면 화면 파일이 너무 커지고, 한 기능을 고치다가 앱 전체 동작을 건드릴 위험이 생기기 때문이다.

### 4.4 `frontend/src/pages`

각 페이지는 실제 업무 화면이다.

예:

- `My_FileBrowser.jsx`: 데이터 파일 존재, 컬럼, preview 확인
- `My_Dashboard.jsx`: 추세, KPI, chart snapshot 확인
- `My_SplitTable.jsx`: plan vs actual, lot/wafer matrix 작업
- `My_Tracker.jsx`: 이슈와 lot watch 추적
- `My_Inform.jsx`: product/lot/wafer 단위 인폼 기록
- `My_Meeting.jsx`: 회의록, 결정사항, 액션아이템
- `My_Calendar.jsx`: 변경점과 액션 날짜 관리
- `My_Admin.jsx`: 사용자, 권한, 설정, 모니터링

앞으로 페이지 파일이 커지면, 새 기능을 바로 페이지에 넣지 않고 아래처럼 빼는 방향이 좋다.

```text
frontend/src/features/inform/
├── api.js
├── hooks.js
├── components/
└── utils.js
```

쉽게 말하면, 한 화면 안에서도 "메일 모달", "담당자 선택", "SplitTable embed preview" 같은 기능은 각각 작은 부품으로 분리하는 것이 좋다.

## 5. 백엔드 관리 구조

백엔드는 사용자가 누른 버튼 뒤에서 실제 일을 처리한다.

현재 주요 구조는 이렇다.

```text
backend/
├── app.py
├── routers/
├── core/
└── app_v2/
    ├── runtime/
    ├── shared/
    ├── modules/
    └── orchestrator/
```

### 5.1 `backend/app.py`

앱을 켜는 조립 파일이다.

현재 역할:

- FastAPI 앱 생성
- 인증 미들웨어 연결
- 라우터 자동 로딩 호출
- 정적 프론트 빌드 파일 서빙
- version.json 제공

이 파일에 특정 업무 기능을 추가하지 않는 것이 원칙이다. 예를 들어 "SplitTable 저장 규칙"이나 "Inform 메일 본문 생성" 같은 내용은 여기에 넣지 않는다.

### 5.2 `backend/app_v2/runtime`

앱 기동과 운영 wiring을 담당한다.

| 파일 | 역할 |
|---|---|
| `security.py` | `/api/*` 호출에 로그인 토큰 확인, 보안 헤더 적용 |
| `router_loader.py` | `backend/routers/*.py`를 자동 등록 |
| `startup.py` | 백업, tracker scheduler, valve watch, product dedup scheduler 시작 |

반도체 업무로 비유하면 runtime은 공정 조건 자체가 아니라, 장비를 켜고 interlock을 확인하고 background monitor를 시작하는 영역이다.

### 5.3 `backend/routers`

라우터는 화면과 백엔드 사이의 입구다.

좋은 라우터:

- 요청값을 받는다.
- 로그인/권한을 확인한다.
- service에 일을 넘긴다.
- 화면이 기대하는 응답 모양으로 돌려준다.

좋지 않은 라우터:

- 파일을 직접 많이 읽고 쓴다.
- 긴 계산을 직접 한다.
- 여러 기능의 규칙을 한 파일에 섞는다.

현재는 기존 큰 라우터가 아직 남아 있다. 이건 한번에 갈아엎지 않고, 기능 단위로 `app_v2/modules`로 옮겨가는 중이다.

### 5.4 `backend/app_v2/modules`

여기가 앞으로 기능별 업무 로직의 중심이 된다.

목표 구조:

```text
backend/app_v2/modules/<feature>/
├── domain.py
├── repository.py
└── service.py
```

각 파일의 의미:

| 파일 | 쉬운 설명 | 예시 |
|---|---|---|
| `domain.py` | 업무 규칙 | status 값 검증, lot row 정규화 |
| `repository.py` | 저장소 접근 | JSON/CSV/parquet 읽기/쓰기 |
| `service.py` | 실제 유스케이스 | 이슈 생성, 댓글 추가, watch 저장 |

예를 들어 Tracker라면:

- domain: 이슈 데이터의 기본 모양
- repository: `issues.json` 읽고 저장
- service: 이슈 생성/수정/삭제, lot watch 저장
- router: `/api/tracker/issues` 같은 API 입출력

이렇게 나누면 "화면은 그대로 두고 저장 방식만 바꾸기" 또는 "저장 방식은 그대로 두고 UI만 바꾸기"가 쉬워진다.

## 6. 데이터와 상태 관리

`flow`에서 "상태"는 여러 종류가 있다. 이것들을 섞으면 유지보수가 어려워진다.

### 6.1 전역 앱 상태

위치:

- `frontend/src/app/useFlowShell.js`

내용:

- 로그인 사용자
- 현재 탭
- 테마
- 알림
- 세션 만료

### 6.2 화면 내부 상태

위치:

- 각 `frontend/src/pages/My_*.jsx`
- 앞으로는 `frontend/src/features/<feature>/hooks.js`로 분리 예정

내용:

- 선택된 product
- 선택된 lot
- 필터 조건
- 열린 모달
- 현재 보고 있는 table row

### 6.3 업무 데이터 상태

위치:

- `data/flow-data/`

예:

- `tracker/issues.json`
- `informs/`
- `meetings/`
- `calendar/`
- `splittable/`
- `messages/`
- `admin_settings.json`
- `users.csv`

이 데이터는 앱의 운영 기록이다. 코드를 업데이트해도 최대한 보존해야 한다.

### 6.4 원천/분석 데이터

위치:

- `data/Fab/`

예:

- FAB parquet
- INLINE parquet
- ET parquet
- ML_TABLE parquet
- matching csv

이 데이터는 앱이 판단하는 근거다. 화면에서 "데이터가 안 보인다"는 문제는 대개 source path, product naming, column mapping, case sensitivity 문제일 수 있다.

### 6.5 Archive

위치:

- `archive/`

역할:

- 현재 실행에는 필요 없지만 기록으로 남길 문서
- retired page
- 과거 proposal
- 생성 요청 캡처
- Python cache 같은 런타임 산출물

archive는 삭제가 아니라 보관이다. 운영 중인 코드와 헷갈리지 않게 분리한 것이다.

## 7. 업데이트는 어떤 순서로 하는가

업데이트는 보통 아래 순서로 진행하는 것이 안전하다.

```text
1. 사용자 흐름 정의
2. 관련 화면 확인
3. 읽고 쓰는 데이터 확인
4. API 영향 확인
5. 권한/공개 범위 확인
6. 작은 단위로 구현
7. build/smoke/test 검증
8. 문서나 버전 메타 반영
```

### 7.1 사용자 흐름 정의

먼저 "누가, 어느 화면에서, 무엇을 보고, 어떤 판단을 하는지"를 정한다.

나쁜 요청:

```text
Dashboard 개선해줘.
```

좋은 요청:

```text
Dashboard에서 product와 root_lot_id를 선택하면,
해당 lot의 wafer별 ET Rc trend와 FAB latest step을 같이 보고 싶어.
목적은 회의 전에 lot 진행과 전기 특성 이상을 한 화면에서 확인하는 거야.
```

### 7.2 읽고 쓰는 데이터 확인

새 기능은 반드시 어떤 데이터를 읽고 쓰는지 알아야 한다.

예:

- 읽기만 함: parquet preview, chart 조회
- 쓰기도 함: plan 저장, inform 생성, meeting action 추가
- 설정 변경: admin settings, product config
- 운영 기록: tracker issue, calendar event

쓰기 기능은 항상 더 조심해야 한다. 변경 이력, 동시 수정, 권한, 백업을 생각해야 한다.

### 7.3 API 영향 확인

화면이 백엔드와 통신하는 경로가 API다.

예:

- `/api/splittable/view`
- `/api/informs`
- `/api/meetings`
- `/api/tracker/issues`
- `/api/admin/settings`

기존 API 응답 모양을 바꾸면 화면이 깨질 수 있으므로, 가능하면 응답 shape는 유지하고 필요한 필드만 추가하는 방식이 좋다.

### 7.4 권한 확인

아래 질문이 중요하다.

- 일반 사용자도 볼 수 있는가?
- admin만 수정해야 하는가?
- 그룹별 가시성이 필요한가?
- 메일/공지/권한처럼 실수하면 영향이 큰 기능인가?

### 7.5 검증

기본 검증:

```bash
cd frontend && npm run build
python scripts/smoke_test.py
```

환경에 따라 `python` 대신 `python3`가 필요할 수 있다.

백엔드 테스트:

```bash
python -m pytest tests
```

## 8. 업데이트 요청을 어떻게 하면 좋은가

가장 좋은 요청은 "업무 목적"과 "화면에서 기대하는 변화"를 같이 말하는 것이다.

아래 양식을 쓰면 좋다.

```text
[업데이트 요청]

1. 대상 화면:
   예: SplitTable / Dashboard / Inform / Tracker / Meeting / Admin

2. 업무 목적:
   예: 회의 전에 root lot별 진행 상태와 이상 wafer를 빨리 확인하고 싶다.

3. 사용자가 하는 동작:
   예: product 선택 -> root_lot_id 선택 -> wafer별 상태 표 확인

4. 화면에 보여야 하는 것:
   예: wafer_id, fab_lot_id, latest_step, ET Rc, spec out 여부

5. 저장이 필요한가:
   예: 조회만 / 사용자 설정 저장 / plan 저장 / 이슈 생성

6. 권한:
   예: 전체 사용자 조회 가능, admin만 설정 수정

7. 데이터 출처:
   예: ML_TABLE_PRODA, FAB parquet, ET parquet, splittable notes

8. 성공 기준:
   예: product=PRODA, root=A0007에서 wafer 1~25가 보이고, ET 없는 wafer는 빈값으로 표시
```

## 9. 요청 예시

### 9.1 좋은 UI 변경 요청

```text
SplitTable에서 plan 값과 actual 값 차이를 더 빨리 보려고 해.
현재 matrix에서 diff가 있는 cell만 연한 빨강 배경으로 표시하고,
hover하면 plan, actual, updated_by, updated_at을 tooltip으로 보여줘.
저장은 필요 없고 조회 표시만 바꾸면 돼.
```

이 요청은 좋은 이유:

- 대상 화면이 명확하다.
- 조회/표시 변경이라는 범위가 명확하다.
- 저장이 필요 없다고 말해 위험이 낮다.
- 어떤 값이 보이면 성공인지 명확하다.

### 9.2 좋은 백엔드 처리 요청

```text
Tracker의 lot watch에서 FAB 기준 latest_step과 ET 기준 last_measurement를 따로 관리하고 싶어.
사용자는 issue row에서 source를 FAB/ET로 선택하고 target_step을 저장한다.
저장 위치는 tracker issue의 lots[].watch 아래가 좋고,
기존 issue 응답 shape는 깨지지 않아야 해.
```

이 요청은 백엔드 service/repository로 분리하기 좋다.

### 9.3 좋은 데이터 어댑터 요청

```text
새 FAB 데이터는 column명이 ROOT_LOT_ID, WAFERID처럼 기존과 달라.
앱 내부에서는 root_lot_id, wafer_id로 계속 보고 싶어.
FileBrowser와 SplitTable에서 이 차이를 source adapter/profile로 흡수해줘.
특정 라우터에 대문자 컬럼명을 하드코딩하지 않았으면 해.
```

이 요청은 현재 철학인 "실데이터 soft landing"에 잘 맞는다.

### 9.4 피해야 할 요청

```text
Inform, Dashboard, SplitTable 전부 예쁘게 개선하고 백엔드도 빠르게 해줘.
```

이 요청은 범위가 너무 크다. 화면, 저장, 성능, 디자인, API가 섞여 있어 회귀 위험이 크다.

더 좋은 분리:

```text
1차: Inform 메일 미리보기 모달만 분리
2차: Inform product contacts API/hook 분리
3차: SplitTable embed builder를 backend service로 분리
4차: Dashboard chart config 저장 구조 분리
```

## 10. 기능별 업데이트 기준

### 10.1 FileBrowser

목적:

- 데이터가 있는지 확인
- 컬럼과 sample을 확인
- source root 문제를 진단

좋은 요청:

- 특정 source의 컬럼명을 보기 쉽게 alias로 보여달라
- parquet preview 속도를 높여달라
- product folder 자동 인식이 안 되는 경우 진단 메시지를 강화해달라

피해야 할 방향:

- FileBrowser 안에서 복잡한 분석까지 하게 만들기

분석은 Dashboard, SplitTable, ML로 넘기는 것이 좋다.

### 10.2 SplitTable

목적:

- product + lot + wafer 기준으로 plan, actual, diff, notes를 관리

좋은 요청:

- 특정 prefix 컬럼만 보기
- plan 변경 이력 표시
- actual 값의 source와 timestamp 표시
- notes 저장을 별도 service로 분리

주의할 점:

- 저장이 들어가면 version, updated_by, 동시 수정 정책이 필요하다.

### 10.3 Dashboard

목적:

- 추세, KPI, fab progress, chart snapshot 확인

좋은 요청:

- chart 설정 저장
- 특정 KPI card 추가
- product/lot 필터와 snapshot 상태 표시

주의할 점:

- raw data 탐색 기능을 Dashboard에 넣기보다 FileBrowser로 분리한다.

### 10.4 Tracker

목적:

- 개발 이슈와 lot watch를 닫힐 때까지 추적

좋은 요청:

- 이슈 상태 정의 정리
- FAB/ET watch source 분리
- category별 알림 rule 추가
- group visibility 강화

주의할 점:

- Tracker는 운영 기록이므로 변경 이력과 권한이 중요하다.

### 10.5 Inform

목적:

- 제품/lot/wafer 단위 이슈를 담당자에게 전달하고 기록으로 남김

좋은 요청:

- product contacts 분리
- 메일 미리보기/발송 모달 분리
- SplitTable embed builder 분리
- 첨부 실패 시 UI 진단 강화

주의할 점:

- 메일은 외부 영향이 있으므로 dry-run, preview, recipient 확인이 중요하다.

### 10.6 Meeting / Calendar

목적:

- 회의, 결정사항, 액션아이템을 운영 데이터로 남김

좋은 요청:

- 회의 액션을 calendar에 push
- decision/action의 source 표시
- 동시 수정 충돌 메시지 개선

주의할 점:

- Meeting과 Calendar는 서로 연결되므로 한쪽 저장 변경이 다른쪽에 영향을 줄 수 있다.

### 10.7 Admin

목적:

- 사용자, 권한, 그룹, data root, 백업, 설정 관리

좋은 요청:

- 특정 panel을 component로 분리
- 권한 없는 사용자에게 설정이 노출되지 않게 하기
- 백업/다운로드/모니터 panel 정리

주의할 점:

- Admin은 영향 범위가 크므로 작은 단위로 바꾸는 것이 좋다.

## 11. 현재 archive 정리 기준

이번에 archive로 옮긴 항목은 삭제가 아니라 실행 구조에서 제외한 보관이다.

| archive 위치 | 의미 |
|---|---|
| `archive/frontend_retired_2026_04_26` | 독립 페이지에서 빠진 `My_Message`, `My_Monitor` |
| `archive/generated_requests_2026_04_26` | 과거 요청/검증 캡처 JSON |
| `archive/audit_proposals_2026_04_26` | 과거 audit proposal 문서 |
| `archive/runtime_artifacts_2026_04_26` | Python cache 등 소스가 아닌 산출물 |
| `archive/domain_sources_2026_04_26` | 문서에 흡수된 별도 도메인 원문 |

archive에 넣는 기준:

- 현재 앱 실행에 필요 없다.
- 현재 문서 5개에 흡수된 과거 분석이다.
- 라우팅되지 않는 retired 화면이다.
- 재생성 가능한 cache/build 부산물이다.

archive에 넣으면 안 되는 것:

- 현재 라우터가 import하는 파일
- 현재 페이지가 import하는 component
- runtime data 원본
- users, informs, meetings, tracker 같은 운영 기록

## 12. 업데이트 후 확인할 체크리스트

업데이트가 끝났다고 판단하려면 아래를 확인한다.

```text
[기능]
- 요청한 사용자 흐름이 실제로 된다.
- 기존 주요 흐름이 깨지지 않는다.
- 실패 시 원인을 알 수 있는 메시지가 있다.

[데이터]
- 어떤 파일을 읽는지 명확하다.
- 저장이 있다면 updated_at/updated_by/version 정책이 있다.
- 사용자 데이터가 코드 업데이트로 덮이지 않는다.

[권한]
- admin 전용 기능은 admin만 가능하다.
- group visibility가 필요한 기능은 필터가 유지된다.

[검증]
- frontend build 통과
- smoke 또는 관련 테스트 통과
- 테스트를 못 돌렸다면 이유를 기록

[문서]
- 구조나 사용법이 바뀌면 docs에 반영
- 과거 자료는 archive로 이동
```

## 13. 앞으로의 추천 리팩터링 순서

현재 앱은 이미 동작하는 기능이 많으므로, 한 번에 크게 바꾸기보다 업무 리스크가 큰 순서로 잘라서 진행하는 것이 좋다.

추천 순서:

1. Inform
   - product contacts API/hook 분리
   - mail preview/send modal 분리
   - SplitTable embed builder 분리

2. SplitTable
   - notes repository/service
   - rulebook repository/service
   - product scan adapter

3. Dashboard
   - chart config repository
   - snapshot scheduler service
   - fab progress service

4. Admin
   - backup panel
   - monitor panel
   - permission panel
   - group/category manager

5. 공통 상태/저장 계층 강화
   - JSON atomic write 확대
   - 변경 이력 통일
   - data root/source adapter 진단 표준화

## 14. 가장 중요한 운영 원칙

`flow`는 예쁜 landing page가 아니라, 개발/pilot fab에서 lot과 wafer를 중심으로 빠르게 판단하고 기록을 남기는 업무 도구다.

따라서 새 기능을 요청할 때는 항상 이 질문으로 시작하는 것이 좋다.

```text
이 기능은 사용자가 product, lot, wafer 맥락에서 더 빨리 판단하게 하는가?
이 기능은 나중에 tracker, inform, meeting, calendar 기록과 연결되는가?
실패했을 때 원인을 알 수 있는가?
```

이 질문에 답이 있으면 좋은 기능일 가능성이 높다. 답이 없으면 새 화면을 만들기보다 기존 화면의 작은 개선으로 푸는 것이 더 안전하다.
