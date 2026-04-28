# flow

Fab data analytics + plan vs actual tracking platform.

`flow`는 반도체 개발/pilot 단계에서 공정 데이터, 실험 plan, actual, 이슈, 인폼, 회의, 액션아이템을 lot/wafer 중심으로 이어 보는 FastAPI + React/Vite 웹 앱이다.

- 현재 버전: **v9.0.4**
- 기본 실행 포트: **8080**
- 기본 admin: `hol / hol12345!`
- 상세 변경 이력: [VERSION.json](VERSION.json)

## 먼저 읽을 것

| 목적 | 파일 |
|---|---|
| 지금 바로 실행 | 이 README의 [Quick Start](#quick-start) |
| 문서 전체 입구 | [docs/README.md](docs/README.md) |
| 구조 파악 | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| 반도체 진단/RCA 지식 구조 | [docs/SEMICONDUCTOR_DIAGNOSIS_MVP.md](docs/SEMICONDUCTOR_DIAGNOSIS_MVP.md) |
| 동시성/번들 분리와 웹 기능 사용 | [docs/WEB_CONCURRENCY_BUNDLE_GUIDE.md](docs/WEB_CONCURRENCY_BUNDLE_GUIDE.md) |
| 수정 반영 기준 | [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) |
| 화면별 목표 | [docs/FEATURE_GOALS.md](docs/FEATURE_GOALS.md) |
| 사내 반입/업데이트 | [docs/SOFT_LANDING_INTERNAL.md](docs/SOFT_LANDING_INTERNAL.md) |

## Active Pages

| 페이지 | 현재 역할 |
|---|---|
| 홈 | 버전, changelog, contact bell, 공지 배너, Flow-i 대화형 프롬프트/LLM 연결 상태 |
| 파일탐색기 | DB root 파일 탐색, parquet/CSV preview, SQL filter, S3 동기화 상태, 기본 샘플 로드 |
| 대시보드 | chart, fab progress, alert watch, snapshot, admin section visibility, lazy filter/join projection |
| 스플릿 테이블 | root/fab lot cache, wafer 축 plan vs actual, diff, notes, related issues, XLSX export |
| 진단/RCA | item semantics, Knowledge Card, causal graph, Case DB, Eval, Flow-i RAG Update, reformatter/TEG proposal |
| 트래커 | 이슈, Gantt, category, group visibility, 댓글/대댓글, 이슈 단위 메일/ET watch |
| ET 레포트 | product/lot 검색, measurement package, `step_seq(XXpt)`, reformatter index, PPTX |
| 웨이퍼 레이아웃 | wafer/shot/chip/TEG layout, selected TEG, chip-shot table, CSV export |
| 테이블맵 | DB 관계 그래프, table edit/version, product YAML block 관리 |
| ML 분석 | TabICL/XGBoost/LightGBM trigger, SHAP/feature importance, process area filter |
| 인폼 로그 | lot/root 단위 thread, PEMS reason, image, SplitTable CUSTOM snapshot, timeline |
| 회의관리 | 회의 차수, agenda, minutes, tracker issue import, mail |
| 변경점 관리 | 월 grid, pending/in_progress/done, meeting action/decision sync |
| Messages | 사용자-admin 1:1 문의, admin 공지, bell 동기화 |
| 관리자 | 사용자, 권한, 그룹, 알림, 메일/API/LLM, root, backup, monitor, Base CSV |
| 개발자 가이드 | 앱 내부에서 보는 요약형 개발 문서 |

## Quick Start

### 1. Install

```bash
pip install -r backend/requirements.txt
npm install
cd frontend && npm install
```

자체 추출 번들에서는 아래 명령으로 backend deps와 frontend build를 함께 설치할 수 있다.

```bash
python3 setup.py
```

### 2. Build Frontend

```bash
cd frontend && npm run build
```

### 3. Run

프로젝트 루트에서 실행한다.

```bash
uvicorn app:app --host 0.0.0.0 --port 8080
```

`app.py`는 `backend/app.py`를 로드하는 shim이므로 `--app-dir backend` 없이 동작한다.

접속:

```text
http://localhost:8080
```

### 4. Smoke Test

서버를 띄운 뒤 실행한다.

```bash
python scripts/smoke_test.py
```

사내 반입/업데이트 점검:

```bash
python3 scripts/preflight_internal.py --write-probe
```

## Paths

| 경로/변수 | 의미 |
|---|---|
| `data/Fab/` | 로컬 개발용 DB root. parquet, rulebook CSV, `ML_TABLE_*.parquet` |
| `data/flow-data/` | 로컬 runtime data root. 사용자, 설정, tracker, informs, meetings, calendar |
| `FLOW_DB_ROOT` | 운영 DB root override |
| `FLOW_DATA_ROOT` | 운영 data root override |
| `FLOW_WAFER_MAP_ROOT` | wafer map root override |
| `base_root` | 호환 alias. 현재는 항상 `db_root`와 같은 경로 |

Linux에서 `/config/work/sharedworkspace`가 보이면 사내 기본값을 자동 감지한다.

```text
/config/work/sharedworkspace/DB
/config/work/sharedworkspace/flow-data
```

## Structure

```text
flow/
├── app.py                   # uvicorn shim
├── backend/
│   ├── app.py               # FastAPI app assembly + static serving
│   ├── routers/             # HTTP endpoints, runtime auto-loaded
│   ├── core/                # paths, roots, auth, notify, backup, source helpers
│   └── app_v2/
│       ├── runtime/         # auth middleware, router loading, startup services
│       ├── shared/          # JsonFileStore, Result, source adapter, contracts
│       ├── modules/         # tracker, meetings, informs migration layer
│       └── orchestrator/    # future internal API/agent task contracts
├── frontend/
│   └── src/
│       ├── App.jsx          # shell composition
│       ├── app/             # pageRegistry, useFlowShell
│       ├── pages/           # My_* page entries
│       ├── pages/SplitTable # extracted SplitTable pieces
│       ├── components/      # shared visual components
│       └── lib/api.js       # authenticated API helpers
├── data/                    # local demo/runtime data
├── scripts/                 # smoke, preflight, migration, seed scripts
├── docs/                    # current docs
└── archive/                 # legacy docs and retired files
```

## Validation

일반 수정:

```bash
cd frontend && npm run build
python scripts/smoke_test.py
```

백엔드 로직 수정:

```bash
python -m pytest tests
```

문서만 수정한 경우에는 최소한 아래를 확인한다.

```bash
git diff --check
```

## Latest Changes

**9.0.4 (2026-04-29)**: Flow-i 홈 프롬프트를 대화형으로 정리하고 LLM 연결 확인/모델 표시/컨텍스트 전달을 보강했다. SplitTable/Tracker는 root_lot_id↔fab_lot_id 캐시와 수동 스캔, LOT 노트/related issue, 댓글·대댓글 삭제 권한을 정리했다. Dashboard/FileBrowser는 대용량 parquet 대응을 위해 lazy filter, join projection, preview/download cap을 보강했고, Admin monitor와 공통 Loading UX도 운영 기준으로 맞췄다.

전체 내역은 [VERSION.json](VERSION.json)을 기준으로 한다.

## License

Private. 사내/개인 검증 목적의 저장소다.
