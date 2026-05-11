# flow docs

이 디렉터리는 현재 운영과 수정에 필요한 문서만 둔다. 긴 분석 전문, 과거 협업 기록, retired 문서는 `archive/`에서 찾는다.

## Reading Order

| 순서 | 문서 | 언제 읽나 |
|---:|---|---|
| 1 | [../README.md](../README.md) | 설치, 실행, 기본 구조를 빠르게 확인할 때 |
| 2 | [features/README.md](features/README.md) | 화면/기능별 책임과 코드 진입점을 볼 때 |
| 3 | [ARCHITECTURE.md](ARCHITECTURE.md) | backend/frontend/data 책임 경계를 볼 때 |
| 4 | [DEVELOPMENT.md](DEVELOPMENT.md) | 수정 전 체크, 검증 명령, 리팩터 기준을 볼 때 |
| 5 | [GITHUB_MAIN_PUSH.md](GITHUB_MAIN_PUSH.md) | 로컬 상태를 GitHub `origin/main`에 올릴 때 |
| 6 | [FEATURE_GOALS.md](FEATURE_GOALS.md) | 화면별 추가 기준을 한 번에 비교할 때 |
| 7 | [PRODUCT_PHILOSOPHY.md](PRODUCT_PHILOSOPHY.md) | 제품 방향과 판단 기준을 정렬할 때 |
| 8 | [SEMICONDUCTOR_DIAGNOSIS_RCA.md](SEMICONDUCTOR_DIAGNOSIS_RCA.md) | 진단/RCA 지식 구조를 볼 때 |
| 9 | [RAG/SEMICONDUCTOR_RAG_OPERATIONS.md](RAG/SEMICONDUCTOR_RAG_OPERATIONS.md) | 사내 지식/RAG 입력과 검증 절차를 볼 때 |
| 10 | [FLOW_UI_SYSTEM.md](FLOW_UI_SYSTEM.md) | 공통 UI 규칙과 표준 inform 화면을 볼 때 |
| 11 | [SOFT_LANDING_INTERNAL.md](SOFT_LANDING_INTERNAL.md) | 사내 서버 반입, root 보존, preflight를 볼 때 |

## Current Docs

- 기능 문서는 [features/](features/) 아래를 공식 기준으로 둔다.
- 실행 경로가 바뀌면 루트 [README.md](../README.md)와 이 파일을 같이 수정한다.
- 구조 경계가 바뀌면 [ARCHITECTURE.md](ARCHITECTURE.md)를 수정한다.
- 개발 절차나 검증 명령이 바뀌면 [DEVELOPMENT.md](DEVELOPMENT.md)를 수정한다.
- GitHub `main` 동기화 절차가 바뀌면 [GITHUB_MAIN_PUSH.md](GITHUB_MAIN_PUSH.md)를 수정한다.
- 긴 히스토리, 임시 계획, 생성 요청 캡처는 `archive/`로 보낸다.

## Commands

```bash
cd frontend && npm install && npm run build
cd .. && uvicorn app:app --host 0.0.0.0 --port 8080
```

```bash
git diff --check
python scripts/smoke_test.py
python3 scripts/preflight_internal.py --write-probe
```
