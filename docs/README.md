# flow docs

`flow` 문서는 여기 있는 6개 파일만 먼저 읽는다. 과거 세부 문서와 Claude/Codex 협업 문서는 `archive/docs_legacy_2026_04_25/`로 옮겼다.

## 읽는 순서

1. `README.md`
   - 문서 진입점. 어디를 읽어야 하는지만 알려준다.

2. `PRODUCT_PHILOSOPHY.md`
   - 앱 전체가 무엇을 하려는지, 페이지들이 어떤 철학으로 묶이는지 설명한다.

3. `FEATURE_GOALS.md`
   - 각 페이지의 목표, 사용 흐름, 기능 추가 기준을 정리한다.

4. `ARCHITECTURE.md`
   - 백엔드, 프론트엔드, 데이터, 저장 구조를 짧게 설명한다.

5. `DEVELOPMENT.md`
   - AI 또는 사람이 수정할 때 지켜야 할 규칙, 작업 단위, 검증 방법을 정리한다.

6. `SOFT_LANDING_INTERNAL.md`
   - 사내 반입 포트, 루트, data_root 보존, 백업/롤백, preflight 기준을 정리한다.

## 수정 전 기준

- 새 기능은 큰 파일에 바로 넣지 않는다.
- 백엔드는 `router -> service -> repository/domain` 순서로 분리한다.
- 프론트는 `page -> features/<feature> -> shared` 순서로 분리한다.
- 실패 시 바로 죽지 말고 후보와 진단 정보를 보여준다.
- Claude/Codex handoff, daemon, inbox/outbox 방식은 더 이상 사용하지 않는다.

## 실행

```bash
pip install -r backend/requirements.txt
cd frontend && npm install && npm run build
cd .. && uvicorn app:app --host 0.0.0.0 --port 8080
```

Smoke test:

```bash
python scripts/smoke_test.py
python3 scripts/preflight_internal.py --write-probe
```

## 문서 정리 원칙

- 새 문서는 먼저 이 5개 문서 중 하나에 들어갈 수 있는지 확인한다.
- 임시 계획, 과거 협업 기록, 긴 분석 전문은 `archive/`로 보낸다.
- `docs/README.md`는 100줄 이내로 유지한다.

## 소유자용 해설

- `OWNER_ARCHITECTURE_UPDATE_REPORT.md`
  - IT 전공자가 아닌 앱 소유자 관점에서 현재 구조, 관리 방식, 업데이트 요청법을 설명한다.
