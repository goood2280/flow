# flow

Fab data analytics and plan-vs-actual tracking platform.

`flow`는 반도체 개발/pilot 단계에서 공정 데이터, 실험 plan, actual, issue, inform, meeting, action item을 lot/wafer 중심으로 이어 보는 FastAPI + React/Vite 웹 앱이다.

- 기본 포트: **8080**
- 기본 admin: `hol / hol12345!`
- 버전/번들 메타: [VERSION.json](VERSION.json)
- 공식 에이전트 진입점: [AGENTS.md](AGENTS.md), [TODO.md](TODO.md)
- Flow 작업 컨텍스트: [docs/AGENT_FLOW_CONTEXT.md](docs/AGENT_FLOW_CONTEXT.md)

## Start Here

| 목적 | 문서 |
|---|---|
| 문서 전체 지도 | [docs/README.md](docs/README.md) |
| 화면/기능별 책임 | [docs/features/README.md](docs/features/README.md) |
| 코드 구조 | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| 수정 기준과 검증 | [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) |
| GitHub `main` 푸시 절차 | [docs/GITHUB_MAIN_PUSH.md](docs/GITHUB_MAIN_PUSH.md) |
| 사내 반입/업데이트 | [docs/SOFT_LANDING_INTERNAL.md](docs/SOFT_LANDING_INTERNAL.md) |

## Current Shape

```text
flow/
├── app.py                 # uvicorn shim -> backend/app.py
├── backend/               # FastAPI app, routers, core helpers, app_v2 modules
├── frontend/              # React/Vite shell and page tabs
├── data/Fab/              # local DB seed
├── data/flow-data/        # local runtime/user state
├── scripts/               # smoke, preflight, migration, fixture helpers
├── tests/                 # pytest contract/unit coverage
└── docs/                  # active operation/development docs
```

현재 우선 흐름은 Flow-i Agent 탭이 Inform Log, SplitTable, FileBrowser를 app-action driver로 호출하고, `prompt -> orchestrator -> feature unit_action -> API/handler -> result` trace를 한 화면에서 보여주는 것이다.

## Validation Structure

`flow`의 검증은 앱 안의 smoke/preflight/test 스크립트를 기준으로 한다.

| 항목 | 역할 |
|---|---|
| `scripts/smoke_test.py` | 실행 중인 `localhost:8080` 앱에 로그인해 FileBrowser, SplitTable, Inform, Meeting, Tracker, Admin 기본 API를 확인 |
| `scripts/tab_smoke.py` | admin/smoke user로 주요 탭 endpoint를 반복 확인 |
| `scripts/smoke_lot_flow.py` | lot 중심 E2E 업무 시나리오 smoke |
| `scripts/preflight_internal.py` | 사내 반입 전 포트, root, data_root 보존, backup/restore 기준 확인 |
| `tests/` | backend/router/service 계약과 회귀 단위 테스트 |

앱은 8080을 사용한다. 실행 중인 앱의 root 해석은 `/runtime-roots.json`에서 확인한다.

## Quick Start

```bash
pip install -r backend/requirements.txt
cd frontend && npm install && npm run build
cd ..
uvicorn app:app --host 0.0.0.0 --port 8080
```

자체 추출 번들은 루트에서 backend deps와 frontend build를 함께 준비할 수 있다.

```bash
python3 setup.py
```

접속:

```text
http://localhost:8080
```

## Validation

문서만 수정:

```bash
git diff --check
```

일반 코드 수정:

```bash
git diff --check
cd frontend && npm run build
python3 scripts/smoke_test.py
```

백엔드 단위 테스트:

```bash
python3 -m pytest tests
```

사내 반입/업데이트:

```bash
python3 scripts/preflight_internal.py --write-probe
```

`setup.py` 또는 번들 산출물을 갱신했을 때:

```bash
python3 _build_setup.py
python3 setup.py version
```

## GitHub Main Push

일반 절차는 [docs/GITHUB_MAIN_PUSH.md](docs/GITHUB_MAIN_PUSH.md)를 따른다. 이 작업환경에서는 WSL Git에 GitHub credential이 없을 수 있으므로, `git push origin main`이 인증 실패하면 Windows Git credential을 쓰는 `git.exe push origin main`을 사용한다.

```bash
git status --short --branch
git fetch origin
python3 _build_setup.py
python3 setup.py version
git add -A
git commit -m "..."
git push origin main
# WSL HTTPS 인증 실패 시
git.exe push origin main
```

푸시 후에는 `git rev-parse HEAD`와 `git rev-parse origin/main`이 같은지 확인한다.

## Key Paths

| 항목 | 의미 |
|---|---|
| `FLOW_DB_ROOT` | 운영 DB root override |
| `FLOW_DATA_ROOT` | 운영 data root override |
| `FLOW_WAFER_MAP_ROOT` | wafer map root override |
| `data/Fab/` | 로컬 개발용 DB root seed |
| `data/flow-data/` | 로컬 runtime/user state |

공유 서버에서는 `/config/work/sharedworkspace/DB`와 `/config/work/sharedworkspace/flow-data`를 자동 감지한다. 코드 업데이트, `setup.py`, frontend build는 runtime/user data를 덮어쓰면 안 된다.

## License

Private. 사내/개인 검증 목적의 저장소다.
