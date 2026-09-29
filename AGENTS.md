# Flow contributor instructions

## 프로젝트와 문서 읽는 순서

- Flow는 반도체 개발 데이터를 lot/wafer 중심으로 연결하는 FastAPI + React 웹 앱이다. 파일탐색기, SplitTable, ET/LOT 추적, TEG/WF MAP, 업무 게시판, 차트·리포트와 홈 에이전트를 제공한다.
- 먼저 이 파일을 읽고 `README.md`의 **단일 운영서버 전환 상태**, **빠른 설치**, **운영 API 서버 설정**, **Windows 서버 이사**, **서버 용량 자동 조정과 일일 모니터 부하**를 읽는다. 기능별 상세는 README의 해당 절과 실제 구현·테스트를 대조한다.
- README는 설치·운영 절차, `VERSION.json`은 릴리스 이력의 정본이다. 문서와 코드가 다르면 실제 코드를 확인하고 문서를 함께 고친다. 목표 성능을 실측 결과로 표현하지 않는다.
- 루트 `app.py`는 import shim, 실제 앱은 `backend/app.py`, HTTP 경로는 `backend/routers/`, 계산은 `backend/core/`다. SplitTable·FileBrowser 라우터 일부는 `backend/app_v2/modules/*/router_parts/`에서 조립되므로 해당 part를 수정한다.
- 탭 등록은 `frontend/src/app/pageManifest.jsx`, 구현은 `frontend/src/features/`다. `frontend/src/pages/`의 호환 wrapper와 혼동하지 않는다. 사용자용 기능 안내는 `guides/README.md`도 참고한다.
- `doppelganger/`와 과거 볼트 참조 지침은 사용하지 않는다. 현재 사용자 지시, 코드·문서, 직접 검증 결과로 판단한다.

## 코드 에이전트(사내 VM opencode 등) 빠른 안내

**전체 구조·탭별 파일 위치·수정 레시피는 [`docs/CODEMAP.md`](docs/CODEMAP.md)에 있다. 수정 전에 먼저 읽는다.**

```
React SPA (frontend/src) ──/api/*──▶ backend/app.py ──▶ backend/routers/<기능>.py (HTTP·권한)
                                                          └─▶ backend/core/<기능>.py (계산·저장)
                                                                ├─ FLOW_DB_ROOT   원천 DB (읽기 전용)
                                                                └─ FLOW_DATA_ROOT 사용자 기록·설정·로그
```

작업 순서:
1. 요청한 화면의 탭 key를 `docs/CODEMAP.md` 3절 표에서 찾아 화면 파일·라우터·core 모듈을 정한다.
2. 큰 파일은 통째로 읽지 않는다. `grep -n`으로 함수·API 경로를 찾고 그 구간만 읽는다.
3. 필요한 부분만 고친다. 파일 전체를 다시 쓰지 않는다(CRLF/LF 줄바꿈이 파일마다 다르다).
4. 서버 파라미터는 기본값을 두어 기존 호출을 깨지 않는다. 화면과 서버 기본값이 같이 있는 값은 둘 다 고친다.
5. 바꾼 기능의 pytest(`python -m pytest -q tests/test_<관련>.py`)와 `cd frontend && npm run check`로 확인한다.
6. 은퇴 코드(`routers/home_agent.py`, `core/home_orchestrator.py`, `features/diagnosis/` 등, CODEMAP 3절 끝)는 배포되지 않으므로 고치지 않는다.

## 켜기·상태 확인·끄기 (Windows 운영)

명령은 **실제 설치 폴더**에서 실행한다. 개발 PC에서 운영 경로를 그대로 설정해 앱을 띄우지 않는다.
운영은 1개 API 프로세스와 감시기를 사용한다. `uvicorn --reload` 또는 `--workers 8`로 바꾸지 않는다.
여러 API 프로세스는 RAM 캐시·메모리 예산을 복제하므로 코어 활용 방법으로 사용하지 않는다.

수동 실행용 PowerShell(경로는 현장 값으로 변경):

```powershell
Set-Location 'D:\Flow'  # setup.py를 푼 실제 설치 폴더
$env:FLOW_PROD='1'
$env:FLOW_DB_ROOT='D:\DB'
$env:FLOW_DATA_ROOT='D:\flow-data'
$env:FLOW_RESOURCE_PROFILE='auto'
python scripts/flow_server.py
```

기동 명령은 포그라운드다. 상시 운영은 아래 작업 스케줄러를 사용한다.
별도 PowerShell에서도 같은 설치 폴더와 `FLOW_DATA_ROOT`를 지정한 뒤:

```powershell
python scripts/flow_server.py --status
Invoke-RestMethod 'http://127.0.0.1:8080/health'
python scripts/flow_server.py --restart  # 서버 자식만 재시작 요청
python scripts/flow_server.py --stop     # 서버 + 감시기 정상 종료 요청
```

- `--stop`/`--restart`는 요청 파일을 남기고 즉시 반환한다. 완료까지 `--status`의 프로세스·상태와 `/health`를 확인한다. `--status`의 exit 0 자체는 정상 상태 보장이 아니다.
- 수동 기동한 터미널의 Ctrl+C도 정상 종료 경로다. `taskkill /IM python.exe`처럼 다른 Python 작업까지 끄지 않는다.
- 기본 포트는 8080. 바꾸면 실행·상태 명령의 `--port`와 방화벽을 맞춘다. 종료/재시작 요청 파일은 설치 폴더 단위이므로 한 설치 폴더에 여러 인스턴스를 띄우지 않는다.
- 로그: `FLOW_DATA_ROOT/logs/uvicorn.log`, `flow_restarts.log`, `flow_supervisor.json`. 감시기는 종료 시 재기동, `/health` 연속 실패 시 재기동, psutil이 있으면 자식 포함 RSS 감시를 수행한다. 자동 재기동은 무중단 보장이 아니다.

상시 기동은 `scripts/windows/flow_env.bat`에 운영 경로·역할·offload 설정을 반영한 뒤 관리자 PowerShell에서 등록한다. 다른 PowerShell 창의 `$env:` 값은 SYSTEM 예약 작업에 전달되지 않는다. `flow_env.bat`은 기존 환경변수를 보존하므로 이전 머신 설정도 확인한다.

```powershell
python -c "import fastapi, uvicorn, polars, psutil"
powershell -ExecutionPolicy Bypass -File .\scripts\windows\install_autostart.ps1 -StartNow -OpenFirewall -DisableSleep
Get-ScheduledTask -TaskName FlowWebApp
```

기본은 SYSTEM으로 부팅 시 시작한다. Python 실행 경로, DB/data 접근권한, 인증·외부 LLM 설정을 그 계정 기준으로 검증한다. `-RunAsUser`는 로그인 시 시작이므로 무인 부팅과 다르다. 현재 설치 스크립트의 `-Port`는 방화벽용이며 runner 포트는 `FLOW_PORT`로 별도 맞춰야 한다.

점검 중 재부팅 뒤에도 꺼져 있어야 하면 **예약 작업을 먼저 비활성화**하고 정상 종료한다:

```powershell
Disable-ScheduledTask -TaskName FlowWebApp
python scripts/flow_server.py --stop
# 재개
Enable-ScheduledTask -TaskName FlowWebApp
Start-ScheduledTask -TaskName FlowWebApp
```

등록 자체를 제거하려면 정상 종료를 확인한 후 `install_autostart.ps1 -Uninstall`을 실행한다(해당 방화벽 규칙도 제거).
`.flow_stop`만 남기는 것은 영구 비활성화가 아니다. 다음 수동/부팅 기동 때 감시기가 오래된 요청 파일을 지운다.

업데이트는 **정상 종료 확인 → 코드 추출 → 검증 → 재기동** 순서다. 예약 작업으로 운영 중이면 위 점검 중지 절차를 쓴다. 운영 폴더에서 `$env:FLOW_SETUP_STRICT='1'; python setup.py extract` 후 `$LASTEXITCODE`와 `extract_report.json`을 확인한다. 의존성이 바뀐 경우만 현장 `requirements.txt`로 `python setup.py install-deps`를 수행한다. DB/data, 계정·키·설정은 보존한다.

## Windows VM 설치 폴더에서 작업하기 (Miniforge · opencode)

운영 VM은 컨테이너 이미지가 아니라 **바탕화면 `flow` 폴더에 setup.py를 풀어 그 폴더에서 바로 서버를 돌린다.** 같은 폴더를 opencode 같은 코드 에이전트가 고친다. 이 폴더는 운영 그 자체이므로 아래를 지킨다.

- **위치 규약(코드 기본값, 2026-09-29):** Windows에서 `.git`이 없는 설치 폴더는 환경변수가 없어도 `D:\DB`(원천, 읽기 전용)·`D:\flow-data`(사용자 기록·설정·로그)를 쓴다. 드라이브는 `FLOW_STORAGE_ROOT`, 끄려면 `FLOW_STORAGE_DEFAULT=0`. Windows에서는 Linux `/config/work/...` 경로를 절대 쓰지 않는다(현재 드라이브의 `\config\...`로 풀려 엉뚱한 곳에 쓰던 문제). 자동 백업 기본 위치는 `D:\flow-backups`. 구현: `backend/core/root_profile.py`, `backend/core/backup.py`, 테스트 `tests/test_windows_storage_defaults.py`.
- **파이썬:** Miniforge conda env(기본 이름 `flow`, `FLOW_CONDA_ENV`로 변경). 명령은 `Miniforge Prompt`에서 `conda activate flow` 후 실행한다. 예약 작업은 등록할 때 활성 env의 `python.exe` 전체 경로를 고정한다.
- **켜기/끄기:** `scripts\windows\flow_run.bat`(감시기+바깥 재기동 루프, 창을 닫으면 꺼짐) · `scripts\windows\flow_ctl.bat status|stop|restart|log|health`. 현장 전용 값(LLM 키·로그인·포트)은 `scripts\windows\flow_env.local.bat`에 둔다 — setup.py 업데이트가 `flow_env.bat`은 덮어쓰지만 `.local`은 건드리지 않는다.
- **opencode가 코드를 고친 뒤:** 백엔드 변경은 `flow_ctl.bat restart`(서버 자식만 재기동, 감시기 유지)로 반영한다. `frontend/src` 변경은 `cd frontend && npm run build`로 `frontend/dist`를 다시 만든 뒤 브라우저 새로고침(서버 재시작 불필요). 반영 전 해당 pytest와 `npm run structure:check`·`npm run build`를 돌린다. 테스트는 반드시 임시 `FLOW_DATA_ROOT`/`FLOW_DB_ROOT`와 `FLOW_PROD=0`으로 — 운영 `D:\flow-data`에 테스트 기록을 남기지 않는다.
- **opencode에 필요한 파일:** 기본 `python setup.py extract`는 `AGENTS.md`·`docs/`·`tests/`를 풀지 않는다. 코드를 고칠 VM에서는 처음 한 번 `set FLOW_EXTRACT_ALL=1` 후 `python setup.py`(또는 `python setup.py extract --all`)로 전부 받는다. 구조는 `docs/CODEMAP.md`, 운영 절차는 README "Windows 서버 이사 (D: 저장소)" 절.
- **로컬 수정 보존:** 새 setup.py로 업데이트하면 같은 경로의 소스가 덮어써진다. VM에서 고친 내용은 설치 폴더에서 `git init` 후 커밋해 두고(원격 push 금지 — 사내 코드·설정), 업데이트 뒤 `git diff`로 다시 적용할 부분을 확인한다. `D:\DB`·`D:\flow-data`·`flow_env.local.bat`은 추출이 건드리지 않는다.
- **하면 안 되는 것:** 운영 중 `uvicorn --reload`·여러 워커 기동, `D:\DB` 원천 파일 수정·삭제, `taskkill /IM python.exe`(감시기와 ET 계산 자식까지 죽는다), 설치 폴더 안에 두 번째 인스턴스 기동(`.flow_stop`이 폴더 단위라 서로 끈다).

## Model delegation and token budget

- The user authorizes lower-model subagents for simple, bounded Flow work. Keep the main agent responsible for design decisions, S0/history invariants, cache correctness, permissions, concurrency, and final review.
- Delegate independent file inventories, reference checks, small UI/copy edits, and focused verification to `gpt-5.6-luna` at low or medium reasoning when that model is available. Use `gpt-5.6-sol` for a bounded implementation that needs more reasoning. If unavailable, choose an available inexpensive coding model; do not silently change the main task model.
- Give each subagent only its goal, relevant paths, constraints, and acceptance checks. The main agent inspects delegated diffs and runs the relevant checks before completion.
- Read only relevant file sections. Avoid full-repository dumps, repeated unchanged polling, and tests unrelated to the changed behavior.

## Deployment contract

- `python _build_setup.py` rebuilds `frontend/dist` and the self-contained `setup.py`; the installer is the deployment. A source change that is not rebuilt is not deployed.
- The retired Flow-i agent runtime (`FLOWI_EXCLUDE_*` in `_build_setup.py`) is not shipped. The active home agent is `backend/routers/data_chat.py` + `backend/core/data_chat*.py`.
- `config/` and data files are seed-only. Every new setting needs its default in code.
- Never `git add -A` (runtime data and caches live in the tree). The GitHub repository is public: no internal documents, data, or reports.

## Server roles

- Deployment: **one production Windows Xeon 6448Y 2.1GHz host, allocated 8 cores / 128 GB RAM**. The development worker was removed on 2026-09-30 (`worker_dispatch`, `worker_tasks`, `upstream_proxy`, `home_agent_offload` deleted). Do not reintroduce a second server, a file queue to another host, or role checks (`server_role`, `FLOW_SERVER_ROLE`, `FLOW_WORKER_OFFLOAD` no longer exist in code).
- Every heavy job runs locally through `core/heavy_jobs.run_heavy(kind, fn, label=, idle_only=, product=)`: cache kinds (`CACHE_BUILD_KINDS`) take the server-wide `core.scan_gate` slot, other heavy kinds share one local slot, interactive kinds bypass both; memory admission runs before start. Former worker jobs now start in the background owner (`app_v2/runtime/startup.py` owner_starters): FAB matching scanner (`core/fab_matching_alerts.py`, idle lane), Auto report runner (`core/auto_report.py`: job files are the durable queue, one job at a time, a crash re-queues a running job once), Auto report history scheduler.
- ET tracker, Tracker and Dashboard periodic scanners start only when `FLOW_ENABLE_HEAVY_BACKGROUND_JOBS=1` or the profile is `full` (the `large` profile does not enable them by default).
- 8 logical cores / 128GiB with no overrides selects `large`: CPU budget and Polars pool 8, process soft limit 99.8GiB, cache pool 61.44GiB, heavy request lane 4, essential lane 8. Actual usable memory/CPU detected by the OS takes precedence. These are settings, not a benchmark or OOM guarantee.
- Home agent turns run locally and have a memory precheck, but no global turn semaphore. The legacy `FLOW_FLOWI_MAX_CONCURRENCY` gate does not constrain this path. Per-user question quota is not a global concurrent-work limit. Coordinate heavy tool execution across tabs before increasing parallelism.
- DuckDB defaults to the CPU budget per connection; concurrent queries can contend with the shared Polars pool. Never increase every per-tab limit independently. Measure mixed workload p95, queue waits, memory peaks and `/health` responsiveness before accepting performance.
- For this dedicated server, inspect the administrator's daily synthetic-load schedule (default 11:00, target 85%). `FLOW_SYSMON_ENABLE_LOAD=0` only disables idle load and does not disable the daily schedule; use the schedule setting itself when synthetic load is unwanted. Do not silently overwrite an existing operator setting.
- Run the server through `scripts/flow_server.py` (restart on exit, hang via `/health`, memory), not a bare `uvicorn` command.
- Keep cache work in the shared scan gate (via `heavy_jobs`) and preserve interactive request priority.
- Budgets are derived from the detected host (`core/runtime_limits.py`, `core/cache_budget.py`); do not hard-code machine sizes.

## UI and design system

- Colors, radii, and spacing come from `frontend/src/styles/tokens.css`. `components.css`, `layouts.css`, and `utilities.css` must not contain raw colors, `!important`, or style-attribute selectors (`npm run design:check`).
- Work pages share the Carbon-like page layer (`flow-connected-page`): square 2-4 px corners, 1 px neutral borders, no drop shadows, 32 px controls. Use the accent color for the primary action and state, not for panel borders.
- Charts render through `FlowPlotlyChart` and size themselves from `lib/chartLayout.js`; do not pass fixed heights. New Plotly trace types must be registered in `lib/plotlyCustom.js`.
- When a Flow UI asks users to enter or paste row-and-column data, use `components/SpreadsheetPasteGrid.jsx`: direct multi-cell paste from Excel and Google Sheets, about 10 visible rows with an internal scrollbar and sticky headers, per-cell editing, row numbers, and clear validation. Textareas remain for prose, code, SQL, formulas, and one-dimensional input.

## Verification

- Frontend: `cd frontend && npm run check` (design check, feature boundaries, production build).
- Backend: run the `pytest` modules that cover the changed behavior.
- Tests must use isolated `FLOW_DATA_ROOT`, `FLOW_DB_ROOT`, `FLOW_WAFER_MAP_ROOT` and `FLOW_PROD=0`; never append test state to operator data or run load generation on a live server.
- 8-core/128GB capacity calculation should explicitly test `auto`/`large`. Existing `test_runtime_capacity_scaling.py` mainly forces `small`, so passing it alone does not validate the production profile.
- Live acceptance needs representative data and simultaneous tab/agent operations. Use `scripts/check_split_server_latency.py` for nonempty ready SplitTable responses and `?split_perf=1` for browser paint. A passing unit test/build or a fast empty response is not evidence that every tab is fast.
