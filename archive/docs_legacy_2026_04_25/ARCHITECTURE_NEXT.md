# flow Next Architecture

이 문서는 현재 `flow`를 "혼자 빠르게 만든 앱"에서 "실제 운영을 버틸 수 있는 업무 플랫폼"으로 올리기 위한 목표 구조를 정리한다.

실제 기능 추가/리팩터링 시 지켜야 할 작업 단위와 금지선은 [`FLOW_DEVELOPMENT_GUARDRAILS.md`](FLOW_DEVELOPMENT_GUARDRAILS.md)를 기준으로 한다.

## 1. 현재 구조의 장점과 한계

현재 구조는 다음 강점이 있다.

- FastAPI + React 로 기능 추가 속도가 빠르다.
- 라우터 단위 분리가 되어 있어 화면별 기능 파악이 쉽다.
- 파일 기반 저장소 덕분에 초기 배포와 복구가 단순하다.

반면 운영 단계에서는 아래 문제가 커진다.

- 라우터가 HTTP, 도메인 로직, 저장 로직을 동시에 들고 있어 변경 영향 범위가 넓다.
- JSON/CSV 파일 직접 수정이 많아 동시 편집, 감사 추적, 장애 복구가 취약하다.
- 백그라운드 스케줄러, 알림, 파일 쓰기, 권한 로직이 여러 곳에 흩어져 있다.
- 프론트가 페이지 중심이라 재사용성과 테스트성이 낮아지기 쉽다.

## 2. 목표 원칙

- `Router` 는 요청/응답 변환만 담당한다.
- `Service` 는 유스케이스를 담당한다.
- `Repository` 는 JSON/CSV/parquet/S3 저장소 접근을 담당한다.
- `Domain` 은 공정/회의/이슈/인폼 같은 업무 규칙을 담당한다.
- 파일 기반 저장소를 유지하더라도, 저장은 공통 저장소 계층을 반드시 거친다.
- 장기적으로는 운영 데이터와 분석 데이터를 분리한다.

## 3. 권장 백엔드 구조

```text
backend/
├── app.py                       # 기존 진입점 유지
├── core/                        # 기존 공용 유틸, 점진적 축소 대상
├── routers/                     # 기존 HTTP 엔드포인트, 얇게 유지
└── app_v2/
    ├── shared/
    │   ├── json_store.py        # atomic write / lock / metadata
    │   └── result.py            # 서비스 반환 표준화
    ├── modules/
    │   ├── tracker/
    │   │   ├── domain.py
    │   │   ├── repository.py
    │   │   └── service.py
    │   ├── meetings/
    │   ├── informs/
    │   ├── calendar/
    │   └── dashboard/
    └── README.md
```

## 4. 권장 프론트 구조

```text
frontend/src/
├── app/                         # App, 라우팅, 전역 providers
├── pages/                       # 화면 단위 조립
├── features/                    # tracker, meeting, dashboard 등 기능 단위
├── entities/                    # issue, meeting, lot, wafer 등 공통 모델
├── shared/
│   ├── api/
│   ├── ui/
│   ├── hooks/
│   └── utils/
└── constants/
```

지금 당장은 라우터 라이브러리를 추가로 바꾸기보다, 페이지 안의 API 호출과 UI 상태를 `features/*` 로 빼는 것이 가장 현실적이다.

## 5. 운영 중 실제로 터지는 문제와 방지책

### 5.1 파일 기반 저장소 동시성

문제:

- 두 사용자가 같은 JSON 파일을 거의 동시에 저장하면 마지막 저장이 앞선 저장을 덮어쓴다.
- 스케줄러와 사용자 요청이 같은 파일을 동시에 건드릴 수 있다.

방지:

- 저장은 반드시 atomic write 로 한다.
- 파일별 락을 둔다.
- revision / updated_at / updated_by 메타데이터를 공통으로 기록한다.
- 중요한 엔터티는 append-only audit log 를 남긴다.

### 5.2 데이터 루트 불일치

문제:

- 로컬/운영/테스트 환경에서 `db_root`, `base_root` 해석이 달라져 차트가 비거나 잘못된 데이터를 읽는다.

방지:

- 부팅 시 해석된 루트를 한 번 더 검증한다.
- `/api/system/config` 같은 진단 엔드포인트를 둔다.
- 차트 스케줄러는 시작 전에 데이터 루트 유효성을 확인한다.

### 5.3 백그라운드 작업의 무제어 실행

문제:

- 차트 precompute, 백업, tracker poller 가 한 프로세스 안에서 같이 돌며 I/O 경합을 만든다.

방지:

- 작업별 잠금과 실행 간격을 분리한다.
- 실패 횟수, 마지막 실행 시각, 마지막 에러를 메트릭으로 남긴다.
- 장기적으로는 worker 프로세스로 분리한다.

### 5.4 프론트 대형 페이지 비대화

문제:

- `App.jsx` 와 각 `My_*.jsx` 파일이 계속 커지면 버그 수정 속도가 급격히 느려진다.

방지:

- 페이지는 화면 조립만 하고, 기능별 API/상태/뷰는 `features/*` 로 이동한다.
- 공통 테이블, 필터, drawer, modal 은 shared 컴포넌트로 올린다.

## 6. 단계별 리팩터링 순서

### Phase 1. 운영 안정화

- 공통 저장 계층 도입
- 감사 로그와 revision 메타데이터 통일
- 부팅 시 설정/데이터 루트 진단 강화
- 백그라운드 작업 상태 노출

### Phase 2. 백엔드 모듈화

- `tracker`, `meetings`, `informs`, `calendar` 순으로
  `router -> service -> repository` 분리
- 기존 라우터는 API 계약만 유지하고 내부 호출만 새 서비스로 이동

### Phase 3. 프론트 모듈화

- `tracker`, `meeting`, `inform` 을 먼저 `features/*` 로 추출
- `api.js` 단일 파일을 기능별 API 모듈로 나눔

### Phase 4. 저장소 고도화

- 협업성 높은 엔터티는 SQLite/Postgres 로 전환 검토
- 분석성 대용량 데이터는 parquet/폴라스 유지

## 7. 저장소 전략

권장 방향은 이원화다.

- 운영 데이터:
  이슈, 회의, 액션아이템, 메시지, 권한, 알림, 캘린더
  -> SQLite 또는 Postgres 가 적합

- 분석 데이터:
  parquet, CSV, S3, Base 룰북
  -> 현재처럼 파일 기반 유지 가능

즉 `운영성 데이터는 DB`, `분석성 데이터는 파일/레이크`가 가장 안정적이다.

## 8. 지금 코드베이스에 맞는 현실적인 결론

이 프로젝트는 처음부터 전면 재작성하는 것보다, 현재 동작 중인 라우터를 유지하면서 아래처럼 옮기는 것이 맞다.

1. 기존 라우터는 그대로 둔다.
2. 새 기능부터 `app_v2/modules/*` 로 만든다.
3. 기존 기능은 우선 `tracker -> meetings -> informs` 순으로 이관한다.
4. 파일 기반 저장은 당장 유지하되, 공통 저장 계층을 강제한다.
5. 운영 데이터는 이후 SQLite/Postgres 로 올릴 준비를 한다.

이 방식이면 지금 배워가면서 진행하기에도 부담이 적고, 나중에 구조를 설명할 때도 훨씬 명확해진다.

## 9. 운영 배포 원칙

현행 운영 원칙은 아래처럼 잡는다.

- 코드 배포 경로:
  `/config/work/holfast-api`
- 공유 상태/설정/로그 경로:
  `/config/work/sharedworkspace`
- 공유 운영 데이터 예:
  `/config/work/sharedworkspace/holweb-data`
- 분석 데이터 예:
  `/config/work/sharedworkspace/DB`
  `/config/work/sharedworkspace/Base`

핵심 원칙은 `코드`와 `상태`를 분리하는 것이다.

- `setup.py` 는 코드만 교체한다.
- 사용자 데이터, 설정값, 로그, 회의 기록, 이슈 기록은 공유 경로에 남는다.
- 재배포해도 운영 데이터는 지워지지 않아야 한다.

이 원칙은 실제 현업에서 매우 중요하다. 앱 코드 업데이트는 자주 일어나도, 운영 데이터는 한 번 날아가면 복구 비용이 매우 크기 때문이다.

## 10. Soft-Landing 원칙

테스트 데이터와 실데이터는 경로, 파일명, 컬럼명, 포맷이 어긋날 수 있다.

그래서 아키텍처는 아래를 기본으로 가져야 한다.

- 내부 로직은 canonical 이름을 사용한다.
- 외부 데이터 차이는 adapter/profile 계층에서 흡수한다.
- 경로와 컬럼 alias 는 운영 중에도 수정 가능해야 한다.
- 실패 시 즉시 죽는 대신 후보 목록과 진단 정보를 준다.

즉, `엄격한 스키마 강제`보다 `조정 가능한 정규화 계층`이 먼저다.
