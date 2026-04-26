# app_v2

`app_v2` 는 기존 `routers/` 와 `core/` 를 한 번에 갈아엎지 않고, 점진적으로 운영형 구조로 옮기기 위한 새 레이어다.

원칙은 단순하다.

- `routers/*` 는 HTTP 입출력만 담당
- `runtime/*` 은 앱 기동 wiring 담당
- `modules/*/service.py` 는 유스케이스 담당
- `modules/*/repository.py` 는 파일/DB 접근 담당
- `modules/*/domain.py` 는 업무 규칙 담당
- `shared/*` 는 여러 모듈에서 같이 쓰는 인프라 담당

초기에는 기존 코드와 병행 운영한다.

## Runtime

`app_v2/runtime/` 은 feature 로직이 아니라 FastAPI 앱 조립을 보조한다.

- `security.py`: `/api/*` 세션 인증 미들웨어와 보안 헤더
- `router_loader.py`: `backend/routers/*.py` 동적 로딩
- `startup.py`: 백업, tracker, valve watch, product dedup scheduler와 seed admin 초기화

## Orchestrator

`app_v2/orchestrator/` 는 향후 내부 API/에이전트 연결용 스캐폴딩이다.

- `schemas.py`
  - task request/result
  - action proposal
  - orchestration run/plan
- `registry.py`
  - agent 목록과 역할 메타
- `service.py`
  - lot/product 기준 기본 계획 생성기

현재는 로컬 deterministic service와 문서화된 task 계약을 맞추는 단계이고,
나중에 사내 API나 내부 모델을 붙일 때도 같은 JSON 계약을 유지하는 것이 목표다.
