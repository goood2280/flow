# flow docs

이 디렉터리는 현재 운영/수정에 필요한 문서만 둔다. 오래된 분석 전문, 과거 협업 기록, retired 문서는 `archive/`를 기준으로 찾는다.

## 빠른 읽기 순서

| 순서 | 문서 | 언제 읽나 |
|---:|---|---|
| 1 | [../README.md](../README.md) | 설치, 실행, 전체 구조를 빠르게 확인할 때 |
| 2 | [PRODUCT_PHILOSOPHY.md](PRODUCT_PHILOSOPHY.md) | 앱이 어떤 문제를 풀어야 하는지 정렬할 때 |
| 3 | [FEATURE_GOALS.md](FEATURE_GOALS.md) | 화면별 목표와 기능 추가 기준을 볼 때 |
| 4 | [ARCHITECTURE.md](ARCHITECTURE.md) | backend/frontend/data 구조와 책임 경계를 볼 때 |
| 5 | [DEVELOPMENT.md](DEVELOPMENT.md) | 코드를 수정하기 전 범위, 검증, 문서 반영 기준을 볼 때 |
| 6 | [SOFT_LANDING_INTERNAL.md](SOFT_LANDING_INTERNAL.md) | 사내 서버 반입, 포트, data_root 보존, preflight를 볼 때 |

소유자에게 구조와 요청 방법을 설명해야 하면 [OWNER_ARCHITECTURE_UPDATE_REPORT.md](OWNER_ARCHITECTURE_UPDATE_REPORT.md)를 사용한다.

## 수정 전 체크

- 어떤 사용자 흐름을 바꾸는지 먼저 쓴다.
- 읽고 쓰는 파일/API/권한을 확인한다.
- backend는 `router -> service -> repository/domain` 순서로 분리한다.
- frontend는 `page -> extracted component/hook -> shared` 순서로 분리한다.
- 실패 시 빈 화면 대신 후보와 진단 정보를 보여준다.
- Claude/Codex handoff loop, inbox/outbox, daemon 방식은 사용하지 않는다.

## 실행

```bash
pip install -r backend/requirements.txt
cd frontend && npm install && npm run build
cd .. && uvicorn app:app --host 0.0.0.0 --port 8080
```

Smoke/preflight:

```bash
python scripts/smoke_test.py
python3 scripts/preflight_internal.py --write-probe
```

## 문서 원칙

- 새 문서를 만들기 전에 이 디렉터리의 기존 문서에 흡수 가능한지 확인한다.
- 진입점 문서(`../README.md`, `docs/README.md`)는 링크와 실행 경로 중심으로 유지한다.
- 긴 changelog는 `VERSION.json`에 둔다.
- 긴 분석 전문, 임시 계획, 생성 요청 캡처는 `archive/`로 보낸다.
- `docs/README.md`는 100줄 이내로 유지한다.
