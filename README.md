# flow

Fab data analytics and plan-vs-actual tracking platform.

`flow`는 반도체 개발/pilot 단계에서 공정 데이터, 실험 plan, actual, issue, inform, meeting, action item을 lot/wafer 중심으로 이어 보는 FastAPI + React/Vite 웹 앱이다.

- 현재 버전: **v9.0.4**
- 기본 포트: **8080**
- 기본 admin: `hol / hol12345!`
- 버전 메타: [VERSION.json](VERSION.json)

## Start Here

| 목적 | 문서 |
|---|---|
| 문서 전체 지도 | [docs/README.md](docs/README.md) |
| 화면/기능별 책임 | [docs/features/README.md](docs/features/README.md) |
| 코드 구조 | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| 수정 기준과 검증 | [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) |
| 사내 반입/업데이트 | [docs/SOFT_LANDING_INTERNAL.md](docs/SOFT_LANDING_INTERNAL.md) |

## Quick Start

```bash
pip install -r backend/requirements.txt
npm install
cd frontend && npm install && npm run build
```

자체 추출 번들은 루트에서 아래 명령으로 backend deps와 frontend build를 함께 준비할 수 있다.

```bash
python3 setup.py
```

서버 실행:

```bash
uvicorn app:app --host 0.0.0.0 --port 8080
```

접속:

```text
http://localhost:8080
```

## Validation

일반 코드 수정:

```bash
git diff --check
cd frontend && npm run build
python scripts/smoke_test.py
```

백엔드 단위 테스트:

```bash
python -m pytest tests
```

사내 반입/업데이트:

```bash
python3 scripts/preflight_internal.py --write-probe
```

문서만 수정한 경우:

```bash
git diff --check
```

## Key Paths

| 항목 | 의미 |
|---|---|
| `FLOW_DB_ROOT` | 운영 DB root override |
| `FLOW_DATA_ROOT` | 운영 data root override |
| `FLOW_WAFER_MAP_ROOT` | wafer map root override |
| `data/Fab/` | 로컬 개발용 DB root seed |
| `data/flow-data/` | 로컬 runtime/user state |

공유 서버에서는 `/config/work/sharedworkspace/DB`와 `/config/work/sharedworkspace/flow-data`를 자동 감지한다.

코드 구조는 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), 기능별 진입점은 [docs/features/README.md](docs/features/README.md)를 기준으로 본다.

## License

Private. 사내/개인 검증 목적의 저장소다.
