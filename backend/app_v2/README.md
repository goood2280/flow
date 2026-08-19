# app_v2

`app_v2`는 기존 `backend/routers`와 `backend/core`를 한 번에 갈아엎지 않고, 운영형 구조로 점진 이관하기 위한 레이어다.

## 책임 경계

| 영역 | 담당 |
|---|---|
| `runtime/` | 앱 기동 wiring. auth middleware, router loading, startup services |
| `shared/` | 여러 모듈이 같이 쓰는 저장/결과/source adapter/계약 |
| `modules/<feature>/domain.py` | validation, 업무 규칙 |
| `modules/<feature>/repository.py` | JSON/CSV/parquet/S3 접근 |
| `modules/<feature>/service.py` | 유스케이스 |
| `orchestrator/` | 향후 내부 API/agent task/action JSON 계약 |

라우터는 HTTP 입출력과 권한만 담당하고, 저장/업무 판단은 service/repository/domain으로 옮긴다.

## 현재 모듈

| module | 현재 역할 |
|---|---|
| `modules/tracker` | issue 생성/수정, legacy shape 호환, lot row normalize |
| `modules/meetings` | meeting/session repository/service |
| `modules/informs` | SplitTable embed payload builder |
| `modules/filebrowser/router_parts` | FileBrowser legacy namespace를 보존한 역할별 소스 분리 |
| `modules/splittable/router_parts` | SplitTable legacy namespace를 보존한 역할별 소스 분리 |
| `modules/llm/router_parts` | LLM 도구/orchestration/chat/API 역할별 소스 분리 |

기존 라우터와 병행 운영 중이므로 새 코드는 기존 응답 shape를 깨지 않는 방향으로 붙인다.

## Legacy router source parts

거대 라우터 3개는 `runtime/module_parts.py`를 통해 feature별 `router_parts/*.part.py`를
파일명 순서대로 기존 public router namespace에서 실행한다. 이 단계에서는 endpoint,
전역 cache, import 순서, 직접 helper import와 monkeypatch target을 바꾸지 않는다.

part는 물리적인 책임 경계이자 충돌 완화 장치다. 독립 모듈로 import하지 않으며, 새 코드는
일반 app_v2 module에 구현한다. 기존 코드를 실제 service/repository로 옮기는 작업은 호출자와
테스트의 의존성까지 함께 바꾸는 후속 단계로 취급한다.

## Runtime

- `runtime/security.py`: `/api/*` session token 인증과 보안 헤더
- `runtime/router_loader.py`: `backend/routers/*.py` 동적 로딩
- `runtime/startup.py`: backup, tracker, valve watch, product dedup scheduler, seed admin 초기화

`runtime`에는 feature별 계산/저장 규칙을 넣지 않는다.

## Orchestrator

`orchestrator/`는 아직 외부 agent 실행기가 아니라 명시적인 계약 스캐폴딩이다.

- `schemas.py`: task request/result, action proposal, orchestration run/plan
- `registry.py`: agent role metadata
- `service.py`: lot/product 기준 deterministic plan builder

나중에 사내 API나 내부 모델을 연결하더라도 이 JSON 계약을 유지하는 것이 목표다.
