# Flow Development Guardrails

작성일: 2026-04-25

이 문서는 `flow`가 기능을 빠르게 붙이는 단계에서 운영 가능한 구조로 넘어가기 위한 개발 기준이다. 목표는 전면 재작성이 아니라, 현재 동작하는 앱을 유지하면서 새 기능과 수정 작업이 더 이상 대형 파일에 섞이지 않게 만드는 것이다.

## 1. 현재 진단

현재 구조는 기능 추가 속도는 빠르지만 변경 범위가 커지기 쉬운 상태다.

- 프론트 페이지 파일이 화면, API 호출, 상태 관리, 모달, 테이블 렌더링을 한 번에 들고 있다.
  - `frontend/src/pages/My_Inform.jsx`: 약 3.6k lines
  - `frontend/src/pages/My_Admin.jsx`: 약 2.1k lines
  - `frontend/src/pages/My_SplitTable.jsx`: 약 1.9k lines
  - `frontend/src/pages/My_Dashboard.jsx`: 약 1.8k lines
- 백엔드 라우터가 HTTP, 파일 저장, 도메인 규칙, Polars 처리, 메일/알림까지 같이 들고 있다.
  - `backend/routers/splittable.py`: 약 4.1k lines
  - `backend/routers/informs.py`: 약 2.8k lines
  - `backend/routers/meetings.py`: 약 1.9k lines
  - `backend/routers/dashboard.py`: 약 1.8k lines
- `backend/app_v2`에는 이미 점진적 이관 구조가 시작되어 있다. 이 방향을 표준으로 삼는다.

## 2. 기본 원칙

1. 새 기능은 큰 페이지/라우터에 바로 붙이지 않는다.
2. 기존 API 계약은 유지하되 내부 구현을 `service`, `repository`, `domain`으로 옮긴다.
3. 저장소 접근은 라우터에서 직접 하지 않는다.
4. 프론트 페이지는 화면 조립만 담당하고, 기능별 API와 상태 로직은 `features/*`로 분리한다.
5. 한 번에 전체를 갈아엎지 않는다. 사용자가 실제로 쓰는 흐름부터 얇게 분리한다.

## 3. 백엔드 규칙

권장 구조:

```text
backend/app_v2/modules/<feature>/
├── domain.py       # 업무 규칙, validation, 계산
├── repository.py   # JSON/CSV/parquet/S3 접근
└── service.py      # 유스케이스 조립
```

라우터 규칙:

- `backend/routers/*.py`는 request parsing, auth, response shape만 담당한다.
- `load_json`, `save_json`, `open`, `pl.scan_*`, `urllib` 같은 I/O는 새 코드에서 라우터에 추가하지 않는다.
- JSON 파일 저장은 `app_v2.shared.json_store.JsonFileStore` 또는 그에 준하는 공통 저장 계층을 거친다.
- 여러 기능에서 쓰는 로직은 `backend/core`에 막 추가하지 않는다. 먼저 특정 feature module에 두고, 실제 공유가 확인되면 `app_v2/shared`로 올린다.

허용되는 예외:

- 기존 라우터의 작은 버그 수정
- API 계약 유지용 glue code
- 긴급 hotfix

## 4. 프론트엔드 규칙

권장 구조:

```text
frontend/src/
├── pages/
│   └── My_<Feature>.jsx          # 화면 조립
├── features/
│   └── <feature>/
│       ├── api.js                # 해당 기능 API 호출
│       ├── hooks.js              # 상태/로드/저장 흐름
│       ├── components/           # 기능 전용 UI
│       └── utils.js              # 순수 함수
└── shared/
    ├── api/
    ├── ui/
    ├── hooks/
    └── utils/
```

프론트 규칙:

- 새 API 호출은 `fetch()`를 직접 쓰지 않고 `src/lib/api.js`의 `sf`, `postJson`, `dl`을 사용한다.
- 페이지 파일이 800 lines를 넘으면 새 기능을 추가하기 전에 컴포넌트나 hook 추출을 먼저 한다.
- 새 모달, drawer, 테이블 편집기는 페이지 안에 길게 넣지 않는다.
- `App.jsx`는 탭 등록, 전역 shell, error boundary 수준만 담당한다.

## 5. 기능 추가 전 체크리스트

기능을 시작하기 전에 아래를 먼저 정한다.

- 목적: 사용자가 어떤 결정을 더 빨리 하게 되는가?
- 소유 데이터: 어떤 JSON/CSV/parquet 파일을 읽고 쓰는가?
- API 계약: endpoint, request, response shape
- 권한: admin 전용인지, 그룹 가시성이 필요한지
- 실패 모드: 데이터 없음, 컬럼 없음, 권한 없음, 대용량 timeout 때 무엇을 보여줄지
- 테스트: smoke에 넣을 최소 성공/실패 케이스

위 항목이 정리되지 않은 상태에서는 대형 페이지에 UI부터 붙이지 않는다.

## 6. 이관 우선순위

1. `Inform`
   - 이유: 가장 큰 프론트 파일이고 메일, 첨부, SplitTable embed, product contacts가 섞여 있다.
   - 첫 분리 대상: product contacts API/hook, mail preview/send modal, SplitTable embed builder

2. `SplitTable`
   - 이유: 백엔드 라우터가 가장 크고 데이터 스캔, rulebook, notes, plan 저장이 섞여 있다.
   - 첫 분리 대상: notes repository/service, rulebook repository/service, product scan adapter

3. `Dashboard`
   - 이유: 차트 계산, 스냅샷 스케줄러, fab progress가 한 라우터에 있다.
   - 첫 분리 대상: chart config repository, snapshot scheduler service, fab progress service

4. `Meeting`
   - 이유: 이미 `app_v2.modules.meetings`가 시작되어 있어 이관 비용이 낮다.
   - 첫 분리 대상: mail rendering/sending, calendar push/unpush

5. `Admin`
   - 이유: 여러 관리 기능의 모음이라 탭 단위로 분리하면 효과가 크다.
   - 첫 분리 대상: backup panel, activity panel, category manager, data roots panel

## 7. 작업 크기 제한

한 번의 변경은 아래 중 하나로 제한한다.

- 한 기능의 API 호출 분리
- 한 기능의 repository/service 도입
- 한 모달 또는 패널 컴포넌트 추출
- 한 저장 파일의 atomic write 전환
- 한 smoke/pytest 케이스 추가

피해야 할 변경:

- 라우팅, 디자인, 저장 구조, 권한, 데이터 스캔을 한 번에 바꾸는 작업
- `App.jsx`와 여러 대형 페이지를 동시에 수정하는 작업
- 기능 추가와 대규모 리팩터링을 같은 변경에 섞는 작업

## 8. 다음 3단계

1. `Inform`에서 product contacts를 `features/inform`과 `app_v2/modules/informs`로 분리한다.
2. `SplitTable` notes 저장을 `JsonFileStore` 기반 repository/service로 옮긴다.
3. `Dashboard`의 직접 `fetch()` 사용을 `src/lib/api.js` 기반 호출로 통일한다.

이 세 작업은 사용자 기능을 크게 바꾸지 않으면서 구조 혼선을 줄이는 시작점이다.
