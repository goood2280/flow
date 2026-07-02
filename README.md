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
| 앱 운영/성능 리포트 | [docs/APP_MAINTENANCE_REPORT.md](docs/APP_MAINTENANCE_REPORT.md) |
| 사내 반입/업데이트 | [docs/SOFT_LANDING_INTERNAL.md](docs/SOFT_LANDING_INTERNAL.md) |

## Current Shape

```text
flow/
├── app.py                 # uvicorn shim -> backend/app.py
├── backend/               # FastAPI app, routers, core helpers, app_v2 modules
├── frontend/              # React/Vite shell and page tabs
├── scripts/               # smoke, preflight, migration, fixture helpers
├── tests/                 # pytest contract/unit coverage
└── docs/                  # active operation/development docs
```

GitHub에는 앱 코드와 문서만 둔다. `data/`, `flow-data/`, `Fab/`, `DB/`, `Base/`, `wafer_maps/`는 로컬/사내 운영 데이터 루트이며 checkout 뒤 필요할 때 생성되거나 `FLOW_DB_ROOT` / `FLOW_DATA_ROOT`로 외부 경로를 연결한다.

현재 우선 흐름은 Agent 탭이 FileBrowser AI SQL은 그대로 두고, `goal -> semantic_layer -> task_planner -> unit_agents -> conclusion` runtime trace를 FastAPI SSE로 보여주는 것이다.

현재 운영 상태:

- Flow-i Home은 자연어 요청을 기능별 unit action으로 라우팅하고, Agent 탭은 LangGraph/LangSmith-ready runtime 설계와 시멘틱 해석을 보여준다.
- Agent 단위기능 AI 탭은 각 unit 실행 결과/이력과 LangGraph node detail에 feedback을 붙이고, LangGraph State I/O와 공유 state 설계를 실행 전후 trace 결과와 비교한다.
- FileBrowser는 DB 제품/root parquet 첫 클릭에 스키마를 즉시 그리고 100행 샘플을 백그라운드로 이어 받는 2단계 로드를 쓴다. AI SQL draft는 `필터 + 정렬 + 필요 시 선택 컬럼` 계약을 사용한다.
- LOT progress cache는 hot read path에서 product, lot, root lot, wafer, lot_wf 인메모리 인덱스를 사용한다.
- Inform product 후보와 Tracker/Flow-i 최신 step 조회는 cache parquet 직접 scan보다 memory/JSON cache helper를 우선 사용한다.
- Split Table은 root_lot_id별 사전 피벗 `split_table` 파케이 캐시 Fast Path를 쓰고, 캐시 미스/stale이면 백그라운드 single-flight 재빌드를 큐잉한다. plan/tag 편집은 view 시점 overlay라 저장 직후 반영된다.
- CPU/메모리 한도는 호스트를 읽어 자동 산출(코어-1, 총메모리 65%)하고, 백그라운드 작업(캐시 빌드, S3 주기 동기화)은 사용자 요청에 양보한다(`core/request_priority`).
- S3 주기 업로드/다운로드는 서버별로 켜고 끌 수 있다 — env `FLOW_DISABLE_S3_INGEST`/`FLOW_DISABLE_S3_SYNC` 또는 FileBrowser S3 항목 탭의 방향별 토글(개발/양산 2서버가 같은 버킷을 쓸 때 개발 서버는 끔).
- 회의관리/인폼 화면은 FileBrowser와 같은 UXKit 공통 컴포넌트로 통일돼 있다.
- plan/actual 불일치 알람은 계획 작성자와 지정 팀(`source-config.mismatch_alert_recipients`)에게 간다.
- 세부 운영 상태, 가능한 작업, 100ms light endpoint 기준은 [docs/APP_MAINTENANCE_REPORT.md](docs/APP_MAINTENANCE_REPORT.md)에 둔다.

## Flow-i 에이전틱 오케스트레이션 & 공유 스킬

사내 GPT OSS 120B(OpenAI 호환 endpoint)로 Home 첫 화면의 수작업(파일 내부 항목 조회, SplitTable 확인 등)을 에이전틱하게 처리하는 흐름. 세부 계약은 [docs/features/flowi-agent.md](docs/features/flowi-agent.md).

**1) LLM 연결 (admin)** — Agent 탭 LLM 설정에서 프리셋 "GPT OSS 120B (사내)"(`openai_compatible`) 선택 후 사내 endpoint URL과 토큰만 입력하고 연결 테스트. 내부 프로필이 연결되면 외부 dev AI(vertex/openai)는 자동 차단된다.

**2) 에이전틱 모드 (admin)** — 같은 화면의 "에이전틱 오케스트레이션" 체크박스로 `LLM 도구 선택(tool call)`과 `반복 실행 루프(ReAct)`를 켠다. env `FLOW_LLM_TOOL_CALL`/`FLOW_LLM_REACT_LOOP`가 설정된 서버에서는 env가 우선. 켜면 Home Flow-i가 LLM으로 도구를 골라 결과를 관찰하며 다단계 실행한다 (native tool_calls 미사용 — on-prem 서빙 호환).

**3) 공유 스킬 (모든 유저)** — SQL 작업대에서 여러 SQL 셀을 스킬로 저장할 때 공유를 켜면 전 유저가 사용할 수 있다. Home 채팅에서:
- `쓸 수 있는 스킬 알려줘` → 공유 스킬 카탈로그
- `<스킬 제목> 스킬 실행해줘` → read-only 즉시 실행, 결과 행 미리보기
비공유 스킬은 작성자/admin만 보이며, `POST /api/skills/{key}/share`로 전환한다. 자주 반복되는 작업 패턴은 Skill Miner가 후보로 발굴하고 admin 승인으로 공유 스킬이 된다.

**4) step 조회 + human-in-the-loop 학습 (모든 유저)** — Home 채팅에서:
- `AA100100는 무슨 step이야` → step_matching/Vehicle_matching 기반 양방향 조회. 정확 일치가 없으면 suffix 변형(AB100000EC ↔ AB100000) 기준 유사 후보 제시.
- `SD_EPI step_id 관련 파일 어디에 있어` → 룰북/매칭테이블(Files 단일 파일)을 횡단 검색해 어느 파일 어느 열에 쓰이는지 답한다. 수정은 Files 편집 화면으로.
- 못 찾은 매핑은 `기억해: <용어>는 <답>`으로 가르치면 전 유저 공유 학습 데이터(`flowi_fewshots.json`)에 저장되고 다음부터 즉시 답한다. `잊어줘: <용어>`로 삭제.
- 답이 틀렸으면 싫어요 + 코멘트(`X -> Y` 또는 `정답은 Y`)로 교정하면 같은 저장소에 반영된다.

## Recent Changes (2026-07)

사내 이식 대비 안정화 + 에이전틱 확장 배치. 상세 계약은 각 feature 문서에 있다.

| 영역 | 변경 |
|---|---|
| FileBrowser | 첫 클릭 2단계 로드(스키마 즉시 → 100행 샘플 백그라운드), 요청 시퀀스 가드 |
| SplitTable | view fast-path 컨트롤 플로우 회귀 수정(캐시 미스 시 null 반환), pivot 캐시 canonical `ML_TABLE_*` 디렉터리 통일, 미스/stale 백그라운드 single-flight 재빌드 + `POST /api/splittable/cache/pivot/refresh` |
| 리소스 | CPU/RAM 한도 호스트 자동 산출(코어-1, 총메모리 65%, cgroup 인식), 백그라운드 작업의 사용자 요청 양보(`core/request_priority`) |
| 메모리 | lazy-eviction dict 캐시 주기 정리(`core/cache_sweeper`, 5분) — 장기 uptime 메모리 증가 완화 |
| S3 | 주기 동기화 전역/방향별 on/off (env `FLOW_DISABLE_S3_INGEST`/`FLOW_DISABLE_S3_SYNC` + UI 토글, `/api/s3ingest/auto-sync`) |
| 알람 | plan/actual 불일치 알람 지정 팀 수신(`mismatch_alert_recipients`, SplitTable 설정 패널) |
| UI | 회의관리/인폼 페이지 UXKit 통일 (로컬 인라인 스타일 제거, 로직 불변) |
| 에이전틱 | 에이전틱 오케스트레이션 admin 토글(`flowi_defaults.agentic`), GPT OSS 120B는 기존 `openai_compatible` 프리셋으로 연결 |
| 스킬 | 공유/비공유 권한, share/delete API, Home 채팅에서 스킬 카탈로그/즉시 실행 |
| 학습 | step_lookup 유사 후보 + 매칭 파일 횡단 관련 파일 검색, `기억해:`/`잊어줘:` human-in-the-loop few-shot, 싫어요+코멘트 교정 반영 |

## Current Version

현재 표시 버전은 `VERSION.json`의 release metadata와 파일 mtime 기반 bundle label을 함께 본다. 최신 checkout에서 아래 명령으로 확인한다.

```bash
python3 setup.py version
```

실행 중인 앱에서는 `/version.json`을 확인한다. `setup.py`는 source/doc 변경 후 반드시 `_build_setup.py`로 재생성한다.

## Validation Structure

`flow`의 검증은 앱 안의 smoke/preflight/test 스크립트를 기준으로 한다.

| 항목 | 역할 |
|---|---|
| `scripts/smoke_test.py` | 실행 중인 `localhost:8080` 앱에 로그인해 FileBrowser, SplitTable, Inform, Meeting, Tracker, Admin 기본 API를 확인 |
| `scripts/tab_smoke.py` | admin/smoke user로 주요 탭 endpoint를 반복 확인 |
| `scripts/smoke_lot_flow.py` | lot 중심 E2E 업무 시나리오 smoke |
| `scripts/preflight_internal.py` | 사내 반입 전 포트, root, data_root 보존, backup/restore 기준 확인 |
| `scripts/latency_budget_probe.py` | warm server 기준 light API가 100ms 예산을 넘는지 확인 |
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

warm server light API latency:

```bash
FLOW_BASE=http://127.0.0.1:8080 python3 scripts/latency_budget_probe.py
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
| `data/Fab/` | env가 없을 때 쓰는 로컬 DB root fallback. Git 추적 대상 아님 |
| `data/flow-data/` | env가 없을 때 쓰는 로컬 runtime/user state fallback. Git 추적 대상 아님 |

공유 서버에서는 `/config/work/sharedworkspace/DB`와 `/config/work/sharedworkspace/flow-data`를 자동 감지한다. 코드 업데이트, `setup.py`, frontend build는 runtime/user data를 Git에 넣거나 덮어쓰면 안 된다. 데이터가 없는 fresh checkout도 빈 로컬 root 또는 명시된 외부 root로 기동해야 한다.

## License

Private. 사내/개인 검증 목적의 저장소다.
