# Flow

Flow는 반도체 개발 데이터를 lot/wafer 중심으로 연결하는 FastAPI + React 웹 앱입니다. 파일탐색기,
SplitTable, ET/LOT 추적, TEG/WF MAP, 업무 게시판, 차트·리포트와 홈 에이전트(Flow-i)를 제공합니다.

- **배포 = `setup.py` 한 파일.** 백엔드·프런트엔드(빌드된 `frontend/dist` 포함)·운영 스크립트를 담은
  자기추출 설치 파일입니다. 소스를 고친 뒤 `python _build_setup.py`로 다시 만들지 않으면 배포되지 않습니다.
- **운영 = Windows 서버 1대.** 개발 worker 서버는 쓰지 않습니다(2026-09-29 폐지). 모든 작업이 운영
  서버 한 프로세스에서 돕니다.
- DB, 계정, 설정, 로그, 캐시 같은 운영 데이터는 `setup.py`에 들어가지 않고 업데이트 때도 덮어쓰지 않습니다.
- **이 저장소는 공개(PUBLIC)입니다.** 사내 데이터·문서·발표자료·실제 사번/IP를 커밋하지 않습니다.

| 문서 | 내용 |
|---|---|
| [AGENTS.md](AGENTS.md) | 코드 에이전트·기여자 작업 규칙(단일 원천). `CLAUDE.md`는 이 파일을 가리킵니다 |
| [docs/CODEMAP.md](docs/CODEMAP.md) | 탭별 화면·라우터·core 모듈 위치와 수정 레시피 |
| [docs/LOG_STORAGE.md](docs/LOG_STORAGE.md) | 로그·대화·업무 이력·캐시의 저장 위치, 내용, 보존 정책, 기록/조회 코드 |
| [docs/LOG_STORAGE_OPERATIONS.md](docs/LOG_STORAGE_OPERATIONS.md) | OpenCode용 용량 진단·로그 순환·S3 보관·백업·복원 작업 절차 |
| [guides/](guides/README.md) | 탭별 사용법 영상과 안내 |
| [DB_AI_AND_INPUT_GUIDE.md](DB_AI_AND_INPUT_GUIDE.md) | DB/AI 참고 파일과 입력 데이터 형식(합성 예시) |
| [SECOND_BRAIN.md](SECOND_BRAIN.md) | 외부 second-brain 지식 패키지와 Flow 소비 계약 |
| [VERSION.json](VERSION.json) | 버전과 릴리스 노트(정본) |

사내 VM의 OpenCode에서 로그인·관리자를 조정하려면 먼저 아래
[관리자 ID·이름·허용 부서 빠른 수정](#관리자-id이름허용-부서-빠른-수정-vmopencode)과
[WebSocket 수신 형식·방식 수정](#websocket-수신-형식방식-수정)을 읽습니다.

## 주요 기능

- 파일 공유, 대용량 parquet/CSV 탐색·미리보기, read-only SQL과 AI SQL 초안
- 제품별 SplitTable 검색, plan/actual 비교와 편집, root lot/wafer 기준 FAB 데이터 결합
- WF MAP, TEG 위치 조회·Mapfile 검사, TEG 위치 기반 full chip Yield Map
- ET 추적(일일 스캔·변경점 이슈·메일)과 ET Index 다운로드
- FAB 매칭알람 검사 — 제품별 신규 `step_id`/`ppid`/`reticle_id`를 찾아 룰북·매칭테이블 CSV에 반영
- 차트생성·Template Report·Auto report(PPT 생성)
- 랏 배정/요청, Inform, Tracker, Meeting, Dashboard, 분석의뢰, 제품 위키
- 홈 에이전트(Flow-i) 데이터 챗
- 관리자 콘솔: 사용자·권한, 모니터, 백업, 메일, LLM, 기본지식, **운영 점검 스캔(매일 자동 + 관리자 알림)**

## 서버 구성과 처리 용량

### 구성

| 항목 | 값 |
|---|---|
| 서버 | Windows, Xeon 6448Y 2.1GHz, 할당 8코어 / 128GB — 1대 |
| 원천 DB (읽기 전용) | `D:\DB` (`FLOW_DB_ROOT`) |
| 사용자 기록·설정·로그·캐시 | `D:\flow-data` (`FLOW_DATA_ROOT`) |
| 자동 백업 | `D:\flow-backups` |
| 프로세스 | API 1개 + 감시기(`scripts/flow_server.py`). `--workers N`·`--reload` 금지 |

호스트가 64GiB·8논리코어 이상이면 자원 프로파일이 자동으로 `large`가 됩니다(`FLOW_RESOURCE_PROFILE=auto`).

| 항목 | small (그 외) | large (8코어/128GB 기준) |
|---|---|---|
| 계산 CPU 예산 · Polars 풀 | 코어-1 | 8 |
| 메모리 소프트 기준 | 총량×0.80 | 총량×0.78 ≈ 99.8GiB (`FLOW_MEMORY_CEILING_GB`) |
| 메모리 워치독 경고/긴급 축출/목표 | 80/95/60% | 72/78/62% (상한 기준) |
| 전역 캐시 풀 | 총량×0.45×0.8 | 총량×0.6×0.8 ≈ 61GiB |
| 요청 스레드 | 120 | 240 (`FLOW_THREADPOOL_TOKENS`) |
| 무거운 요청 동시 실행 | 2~3 | 4 (`FLOW_HEAVY_REQUEST_CONCURRENCY`, 대기 최대 120초) |
| SplitTable·파일 보기 전용 레인 | 3 | 8 (`FLOW_ESSENTIAL_REQUEST_CONCURRENCY`) |
| 차트생성 동시 조회 / 결과 캐시 | 2 / 128MB | 4 / 1GB |
| 파일탐색기 SQL 동시 실행 (사용자당) | 2 (1) | 3 (1) (`FLOW_FILEBROWSER_SQL_CONCURRENCY`, `FLOW_FILEBROWSER_SQL_PER_USER`) |
| ET 다운로드 동시 계산 / 대기열 | 1 / 16 | 2 / 48 |
| SplitTable 자동 제품 캐싱 | 꺼짐 | 켜짐 |

위 표는 **설정값이지 측정 결과가 아닙니다.** 메모리 소프트 기준은 OS 강제 상한이 아니며, 옛 서버에서
저장한 관리자 ⚙ 캐시 설정(`pool_fraction` 등)이 있으면 그 값이 우선합니다.

### 무거운 작업은 어떻게 도는가

예전 개발 worker가 맡던 작업을 포함해 모든 무거운 작업이 운영 서버의 `core/heavy_jobs.py`를 거칩니다.

- **백그라운드 캐시 빌드·스캔은 서버 전체에서 한 번에 1건**(공용 스캔 슬롯): FAB 매칭 검사, ET 추적 스캔,
  예약·수동 스캔, Auto report ET history 갱신.
- **조회에 필요한 캐시**(lookup·pivot·FAB 인덱스·WIP latest-lot)는 `large` 서버에서 별도 **조회 레인**(기본 2칸,
  `FLOW_CACHE_READ_LANE_SLOTS`, 0=끄기)을 씁니다. 긴 백그라운드 스캔 뒤에서 SplitTable 첫 조회가 기다리지 않습니다.
  소형 서버는 예전처럼 공용 슬롯 하나입니다.
- **Auto report 생성**은 `large` 서버에서 자기 레인(1칸)을 씁니다(최대 6시간 걸리는 PPT 생성이 캐시 슬롯을 잡지 않음).
- 자동(예약) 작업은 **사용자 요청이 조용해질 때까지 기다린 뒤** 시작합니다. 조회의 전제가 되는 캐시는 짧은
  유예(기본 5초, `FLOW_REQUIRED_CACHE_IDLE_WAIT_SEC`) 뒤 진행합니다.
- 시작 전 **메모리 확인**: 프로세스 한도 초과나 호스트 여유 메모리 부족이면 최대 2분 기다리고, 그래도 부족하면
  실행하지 않고 다음 기회로 미룹니다.
- 대화형 작업(홈 에이전트, 파일탐색기 SQL, 차트 원본 조회)은 이 줄에 서지 않습니다.
- 관리자 → 시스템 → 모니터의 **무거운 작업** 패널과 캐시관리 화면에서 실행 중인 작업과 미뤄진 횟수를 봅니다.
- **파이썬 GC 정지 방지**: 캐시(특히 SplitTable 응답)가 수십 GB 의 파이썬 객체라 전체 GC 한 번이 수 초 동안 모든
  요청을 멈출 수 있었습니다. 무거운 작업이 끝날 때·1분마다 젊은 세대만 수집하고 `gc.freeze()`로 살아 있는 객체를
  제외합니다. 순환 쓰레기 정리(전체 수집)는 사용자가 10분 이상 없을 때 2시간에 한 번만 합니다
  (`FLOW_GC_FREEZE=0`으로 끄기, `FLOW_GC_MAINTENANCE_QUIET_SEC`·`…_INTERVAL_SEC`). 2세대 수집 정지 시간은
  `/api/splittable/memory/overview`의 `watchdog.gc`(`gen2_pause_max_ms`)에서 봅니다.
- **DuckDB 메모리 상한**: 쿼리 연결마다 `memory_limit`(대형 서버 16GB, 그 외 RAM 25%, 그리고 현재 여유 메모리의
  절반 이하)과 임시 폴더(`FLOW_DATA_ROOT\tmp\duckdb`)를 둡니다. 넘치는 정렬·집계는 디스크로 흘려 씁니다
  (`FLOW_DUCKDB_MEMORY_LIMIT_GB`, 0=DuckDB 기본값, `FLOW_DUCKDB_TEMP_DIR`).
- **Windows 메모리 계측**: 프로세스 메모리는 working set 이 아니라 private bytes(커밋)로 봅니다 — working set 은
  Polars 가 매핑해 읽은 parquet 파일 페이지까지 세어 캐시를 필요 이상 비웠습니다. 기동 로그 `host resources:` 줄에
  `commit_limit_gb`가 나오고, pagefile 이 작아 커밋 한도가 RAM 과 비슷하면 경고합니다. pagefile 을 고정 크기로 둔
  서버만 `FLOW_SYSTEM_COMMIT_GUARD_PERCENT=95`로 커밋 사용률 가드를 켜세요.

예전 `FLOW_SERVER_ROLE`, `FLOW_WORKER_OFFLOAD`, `FLOW_API_SERVER_URL`, `FLOW_*_OFFLOAD` 환경변수는
**아무 효과가 없습니다.** 남아 있으면 운영 점검 스캔이 알려 줍니다 — 지워 두세요.

ET 추적·Tracker·Dashboard 차트의 **주기 스캐너**는 `FLOW_ENABLE_HEAVY_BACKGROUND_JOBS=1`일 때만 켜집니다
(`large` 프로파일에서도 기본은 꺼짐). 운영에서 이 예약 스캔이 필요하면 `flow_env.local.bat`에 켜 두세요.

### 동시 사용자 용량 (추정)

아래는 위 설정값과 요청 구조로 계산한 **추정치**이며, 실제 데이터로 측정한 값이 아닙니다.

| 사용 형태 | 무난한 범위(추정) | 먼저 막히는 곳 |
|---|---|---|
| 화면을 열어 둔 접속자(알림 30초 폴링 등 가벼운 요청) | 약 300명 | 거의 없음 — 요청당 수 ms, 초당 수십 건 |
| 동시에 검색·조회하는 사용자 | 약 30~50명 | 무거운 요청 레인 4건 + SplitTable/파일 보기 레인 8건. 캐시가 준비된 검색(0.1~0.5초)은 빠르게 돌지만, 원본을 처음 읽는 조회(수 초~수십 초)가 몰리면 줄이 생기고 120초를 넘으면 잠시 뒤 재시도 응답 |
| 홈 챗(Flow-i) 동시 질문 | 약 5~10턴 | 사내 LLM(Gemma4) 처리량. 한 턴에 LLM 2~4회(수 초~수십 초). 서버 쪽 제한은 사용자당 분당 25질문뿐이고 전체 동시 턴 제한은 없음 |
| 캐시 빌드·FAB 검사·Auto report | 한 번에 1건 | 설계상 직렬. 사용자 요청에 양보 |

"몇 명까지"는 동시에 무엇을 하느냐에 달려 있습니다. 가입자 200~300명 규모에서 평소 동시 활동 20~30명은
여유 있는 범위로 보고, 첫 주에 아래로 확인하세요.

- `scripts/check_split_server_latency.py --help` — 실제 제품·root lot으로 SplitTable 응답 측정(준비 중·빈 결과는 성공으로 세지 않음)
- 화면 URL에 `?split_perf=1` — 브라우저 첫 표시 시간
- 관리자 → 모니터(메모리 p95·무거운 작업), 캐시관리 → 검색 속도(히트율·대기), 운영 점검 스캔 알림

## 로그·저장소 확인과 보관 작업 (OpenCode)

OpenCode/oh-my-opencode에는 **`AGENTS.md`를 읽고 로그·저장소 작업 진입점을 따라 처리하라**고 요청하면 됩니다.
Gemma4 등으로 작업할 때 종류·경로는 [로그·저장소 목록](docs/LOG_STORAGE.md), 수정·정리 절차는
[작업 안내](docs/LOG_STORAGE_OPERATIONS.md)의 해당 절만 읽고 필요한 함수로 이동합니다.
설치본에 문서가 없으면 현장 수정 보존·서버 정상 종료 후 `python setup.py extract --all`로 문서·테스트까지 받습니다.
이 명령은 설치 폴더의 코드도 덮어쓰므로 [업데이트 절차](#업데이트와-데이터-보존)를 따릅니다.

| 구분 | 기본 위치 | 확인할 점 |
|---|---|---|
| 실행·감사·성능 로그 | `D:\flow-data\logs` (`FLOW_DATA_ROOT`; 감시기는 `FLOW_LOG_DIR` 가능) | 순환/건수 제한 로그와 전체 보존 감사 이력을 구별 |
| 대화·업무 이력·첨부파일 | `D:\flow-data`의 기능별 폴더 | 대화 표·차트와 파일 변경본·휴지통·Auto report 중간 자료도 용량에 포함 |
| 원천·파생 DB 캐시 | `D:\DB`, `D:\DB\cache` (`FLOW_DB_ROOT`) | 원천을 정리 대상으로 취급하지 않음. Auto report의 관리 산출물은 별도 경로 |
| 자동 백업 | `D:\flow-backups` 또는 관리자 설정 | 기본 48시간·최근 3개, 최대 5개. DB 원천·cache/tmp·모든 Parquet 제외 |

운영 설치 폴더에서 `powershell -File scripts\windows\measure_flow_storage.ps1`로 파일 내용 없이
폴더·로그 용량과 C:/D: 여유를 기록할 수 있습니다. 사용자 활동이 적은 시간에 실행하고, 실제 root가 다르면
매개변수로 지정합니다. 이전 JSON과 비교하면 순증가량을 계산합니다(자세한 명령은 작업 안내 참고).
S3 연결만으로 로컬 용량이 줄거나 과거 자료가 자동 복원되지는 않습니다. 기간별 보관은 조회·복원까지 함께 설계합니다.

## 설치 (Windows, Miniforge)

요구 사항: Python 3.10 이상(Miniforge conda env), `D:\DB`·`D:\flow-data` 읽기·쓰기 권한.
검증된 번들 화면을 그대로 쓰는 최초 설치에는 Node.js/npm이 필요하지 않습니다. 화면 소스를 수정해 재빌드할 때는 Node.js LTS(npm)가 필요합니다.

1. **Python 환경** — Miniforge를 가능하면 "All Users"(`C:\ProgramData\miniforge3`)로 설치합니다(부팅 예약 작업이
   SYSTEM 계정으로 같은 파이썬을 쓰기 쉽습니다). `Miniforge Prompt`에서:
   ```bat
   conda create -n flow python=3.10 -y
   conda activate flow
   ```
2. **코드 풀기** — 설치 폴더(예: 바탕화면 `flow`)에 `setup.py`를 넣고:
   ```bat
   python setup.py
   ```
   추출 → Python 의존성 → 프런트 확인을 한 번에 합니다. 같은 폴더의 현장 `requirements.txt`가 있으면 그것을
   먼저 씁니다(없을 때만 Flow 최소 의존성). 묶음 설치가 실패하거나 Flow 패키지가 빠졌으면 필요한 패키지를
   개별 재시도하므로 사내 저장소에 없는 패키지 하나가 다른 필수 패키지 설치까지 막지 않습니다.
   사내 pip/npm 저장소 설정을 그대로 사용하며 외부 저장소로 우회하지 않습니다.
   사내 패키지(`botocore`, `boto`, `awscli`, `bigdataquery`)는 버전
   표기를 빼 두면 충돌이 적습니다. 암호화 연락처와 WebSocket 로그인에 `cryptography`, `websockets`가 필요합니다.
   코드 에이전트(opencode)가 고칠 설치 폴더라면 `set FLOW_EXTRACT_ALL=1` 후 실행해 `AGENTS.md`·`docs/`·`tests/`까지 풉니다.
   **설치 마지막에 라이브러리 점검 표**가 나옵니다(`[check] OK/WARN/FAIL`, 설치 버전·최소 버전·번들을 만든 개발 PC 의
   검증 버전). 사내 저장소에서 버전이 다르거나 빠진 패키지를 여기서 확인하고, 설치 후
   `python setup.py check-deps`로 다시 볼 수 있습니다(결과 `install_check.json`, 필수 FAIL 이면 종료 코드 1).
   **미설치·최소 버전 부족·DLL/import 실패 패키지와 용도를 콘솔에 출력**하고 필수·성능·기능별 요약을 남깁니다.
   필수(FAIL) 항목이 있으면 기본 설치도 종료 코드 1로 끝납니다. 앞 단계가 실패해도 마지막 점검을 수행합니다.
   `install_check.json`에는 최종 사용 가능 상태와 설치 명령 실패·개별 재시도·단계별 종료 코드를 구분해 남깁니다.
   필수 검사 통과 시 성능·선택 기능 누락은 WARN으로 진행할 수 있습니다. `FLOW_SETUP_STRICT=1`이면
   설치 명령 실패도 실패로 끝납니다. 설치 목록과 점검 목록이 같으며 Polars ≥1.0, Pydantic ≥2.0, Websockets ≥11을 확인합니다.
   프런트 소스·lockfile 지문과 모든 dist 파일 해시가 검증된 번들과 같으면 strict에서도 npm을 생략합니다.
   재빌드가 필요한 경우 lockfile이 있으면 `npm ci`, 없으면 `npm install`을 사용합니다. npm 실패나 낡은/깨진 화면은 성공으로 처리하지 않습니다.
   OpenCode·oh-my-opencode는 이 설치 폴더를 작업 폴더로 열면 `AGENTS.md`를 자동으로 읽습니다. `/init`·`/init-deep`으로
   `AGENTS.md`를 다시 만들지 마세요(번들이 덮어쓰는 정본입니다).
3. **기존 데이터 이전**(서버 이사 때) — 옛 서버의 Flow를 멈춘 뒤, 먼저 `-DryRun`으로 확인하고 실행합니다.
   다시 실행하면 바뀐 파일만 복사합니다.
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\windows\migrate_flow_data.ps1 -SourceData "<옛 flow-data>" -SourceDb "<옛 DB>"
   ```
   옛 `admin_settings.json`의 `data_roots` 경로 덮어쓰기는 자동으로 지웁니다(백업 `.pre_migration.bak`).
   캐시 폴더도 수정 시각을 보존해 복사하므로 첫날부터 캐시가 유효합니다(`-SkipCache`면 새로 만듭니다).
4. **현장 설정** — LLM 주소·키, 로그인, 포트 같은 현장 값은 `scripts\windows\flow_env.local.bat`에
   `set "이름=값"`으로 적습니다. `flow_env.bat`은 업데이트 때 덮어써지지만 `.local`은 번들에 없어 남습니다.
5. **초기 관리자** — `FLOW_ADMIN_PW`에 10자 이상의 비기본 비밀번호를 지정한 경우에만 `hol` 관리자를 만듭니다
   (`1111`, `CHANGE_ME` 같은 기본값이면 만들지 않습니다).

경로 기본값: Windows에서 `.git`이 없는 설치 폴더는 환경변수가 없어도 `D:\DB`·`D:\flow-data`를 씁니다
(드라이브는 `FLOW_STORAGE_ROOT`, 끄려면 `FLOW_STORAGE_DEFAULT=0`). Linux 경로 `/config/work/...`는 쓰지 않습니다.
관리자 → 데이터 루트에서 실제 적용 경로를 확인합니다.

## 켜기·상태·끄기

```bat
scripts\windows\flow_run.bat
```

창 하나가 감시기입니다(`scripts/flow_server.py`). uvicorn이 죽거나(OOM 포함) `/health`가 1분 넘게 응답이 없거나
메모리 합이 호스트의 90%를 1분 넘게 넘으면 서버를 다시 띄우고, 감시기가 죽으면 바깥 루프가 5초 뒤 다시 띄웁니다.
창을 닫으면 꺼집니다.

다른 Prompt에서:

```bat
scripts\windows\flow_ctl.bat status
scripts\windows\flow_ctl.bat restart
scripts\windows\flow_ctl.bat stop
scripts\windows\flow_ctl.bat log
scripts\windows\flow_ctl.bat health
```

`restart`는 서버만 다시 띄우고(감시기 유지), `stop`은 서버와 감시기를 함께 끕니다. 둘 다 요청 파일을 남기고
바로 돌아오므로 `status`와 `/health`로 완료를 확인합니다. 같은 동작을 `python scripts/flow_server.py --status | --restart | --stop`으로도 할 수 있습니다.
로그는 `D:\flow-data\logs\uvicorn.log`(20MiB 활성 1개+백업 5개, 명목 약 120MiB), 재시작 이력 `flow_restarts.log`, 상태 `flow_supervisor.json`입니다.
요청별 접근 로그는 기본으로 끕니다(로그 쓰기 지연이 서버 전체를 멈추지 않게). 필요하면 `FLOW_UVICORN_ACCESS_LOG=1` 후 감시기까지 재기동합니다.
기동 직후 `host resources:` 줄에서 프로파일·코어·메모리·Polars 스레드를 확인하고, 동적 메모리로 `small`이 된 경우나 데이터 경로가 네트워크 드라이브인 경우 경고가 함께 남습니다.
같은 줄의 `prod=False`(운영 모드 아님 — SplitTable 계산 1코어), `duckdb=False`·`orjson=False`(파일탐색기 대용량 SQL·큰 응답 직렬화가 느린 경로)도 경고로 남으니 conda env 에 `pip install duckdb orjson` 후 재기동합니다.
Windows 는 다른 요청이 파일을 읽는 순간 교체(`os.replace`)가 거부되므로 캐시·JSON 저장은 최대 `FLOW_FILE_REPLACE_RETRY_SEC`(기본 2초) 재시도합니다.
API 의 dict·list 응답은 FastAPI 기본 인코더(이벤트 루프에서 순회) 대신 요청 스레드에서 바로 JSON 으로 만듭니다(`core/json_fast.py`, 라우터 로더가 기동 때 적용). 기동 로그 `json_fast routes: wrapped=…` 로 확인하고, 문제가 의심되면 `FLOW_JSON_FAST_ROUTES=0` 후 재기동하면 예전 경로로 돌아갑니다.
파일탐색기 SQL 은 여러 사용자의 조회를 동시에 돌리고(위 표), 같은 사용자의 두 번째 탭과 같은 사용자·같은 제품 조회는 예전처럼 차례를 기다립니다. 다른 조회와 같이 도는 DuckDB 조회는 스레드를 나눠 쓰고(8→4→2), 메모리가 빠듯하면 병렬로 시작하지 않고 앞 조회가 끝나기를 기다립니다.
대형 서버는 SplitTable KNOB 예열(표 미리 계산)을 별도 프로세스(`flow-splittable-prewarm`, 낮은 우선순위·코어 절반)에서 돌리고, 결과는 디스크 view 캐시로 운영 프로세스에 넘어옵니다. 작업 관리자에 python 프로세스가 하나 더 보이는 것이 정상이며, 운영 프로세스가 끝나면 함께 끝납니다. 끄려면 `FLOW_SPLITTABLE_PREWARM_PROCESS=0`(예전처럼 스레드), 스레드 수는 `FLOW_SPLITTABLE_PREWARM_THREADS`.
WIP(랏 현위치) 캐시 재빌드는 Polars 로 최신 행을 먼저 고른 뒤 파이썬으로 항목을 만듭니다(결과는 예전과 같음). 문제가 의심되면 `FLOW_LOT_PROGRESS_VECTOR=0` 으로 예전 행 단위 경로를 씁니다.
`scripts\windows\flow_ctl.bat perf`는 현장 환경설정을 읽어 **다음 기동에 적용될** 프로파일·실제 Polars 스레드·DuckDB 스레드·캐시 풀·동시 요청 수와 예약 합성 부하를 표시합니다.
실행 중인 감시기의 환경은 읽지 않으므로 현재 기동 로그와 비교합니다. 스캔·부하 생성·설정 변경은 하지 않습니다.
`large`인데 이전 서버의 CPU/메모리 고정값이 작게 남아 있으면 경고합니다. 의도한 제한이 아니라면 해당 값만 `.local.bat`/기동 환경에서 제거하고 감시기까지 다시 기동합니다.
대형 호스트의 `small` 강제 설정, `FLOW_PROCESS_CPU_GUARD_CORES`, 캐시의 `pool_fraction`·`view_mb`와
`FLOW_PREVIEW_MEMORY_CACHE_GB` 제한도 기동 로그와 `perf`에 표시합니다.
캐시 설정은 환경변수가 우선하므로 경고에 나온 위치에서 해제합니다. 관리자 저장값은 캐시관리 ⚙에서 자동으로 되돌립니다.
`perf`의 `cpu_guard_cores`는 큰 요청을 미루는 실제 CPU 보호 기준입니다.
`detected_cores`는 OS affinity·quota를 반영한 코어 수, `cores`는 `FLOW_SYSTEM_CPU_CORES`까지 적용한 코어 수입니다.
옛 `FLOW_SYSTEM_CPU_CORES=4` 때문에 8코어 VM이 조용히 `small`이 되는 경우도 기동 로그·`perf`·운영 점검에서 경고합니다.
조회 캐시 레인·예열 프로세스·AnyIO 스레드 풀·파일탐색기 SQL 동시성·DuckDB 메모리 상한과
GC·JSON·WIP 벡터·응답 압축 스위치도 표시합니다. 작은 명시 동시성이나 꺼진 최적화는 이유와 함께 경고합니다.
`heavy_background_jobs_enabled`·`cache_change_driven_enabled`의 기본 False는 의도된 정책이며 자동으로 켜지 않습니다.
폐기된 root/product RAM 예열의 옛 설정은 현재 자원 제한으로 진단하지 않습니다.
`taskkill /IM python.exe`처럼 다른 파이썬까지 끄지 마세요.

접속 주소는 `http://<서버주소>:8080`입니다.

### 부팅 자동 시작

관리자 PowerShell에서 flow env를 활성화한 상태로:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\install_autostart.ps1 -StartNow -OpenFirewall -DisableSleep
Get-ScheduledTask -TaskName FlowWebApp
```

활성 conda env의 `python.exe` 전체 경로를 고정합니다(패키지 없는 python이면 등록을 거부). 부팅 시 로그인 없이
SYSTEM 계정으로 감시기를 실행하므로 DB/data 접근 권한과 LLM·인증 설정을 그 계정 기준으로 확인합니다. 수동 창과
동시에 띄우지 않습니다. 재부팅 후에도 꺼 두려면 **먼저** `Disable-ScheduledTask -TaskName FlowWebApp` 후 `stop`
하고, 다시 켤 때 `Enable-ScheduledTask` → `Start-ScheduledTask`. 등록 제거는 정상 종료 확인 뒤 `install_autostart.ps1 -Uninstall`.

### 헬스체크와 배포 진단

`GET /health`는 인증 없이 `{"status":"ok","uptime_sec":…}`만 돌려줍니다(경로·환경변수 같은 내부 정보 없음).

화면에 "앱 파일을 서버에서 받지 못했습니다"가 뜨면 부팅 실패 화면이 `GET /deploy-info.json`을 조회해 판정을 보여 줍니다.

| 판정 | 의미 | 조치 |
|---|---|---|
| 서버에는 파일이 있음 | 중간 프록시·캐시가 `/assets/`를 막거나 낡은 응답 | 프록시 설정/캐시 확인 |
| 서버 dist에 파일이 없음 | extract 부분 실패(쓰기 실패 목록 병기) | 다시 추출 |
| 참조 자체가 다름 | 프록시가 낡은 index.html 캐시 | 프록시 캐시 무효화 |
| 구버전 백엔드 | 새 번들 미배포 | 재배포 확인 |

## 업데이트와 데이터 보존

순서는 **정상 종료 확인 → 코드 추출 → 검증 → 재기동**입니다.

1. 캐시관리에서 도는 스캔·빌드가 있으면 끝나기를 기다리거나 중단합니다.
2. 백업: `python scripts/preflight_internal.py --write-probe --backup-now`
3. 정지: 예약 작업으로 운영 중이면 `Disable-ScheduledTask -TaskName FlowWebApp` 후 `flow_ctl.bat stop`, `status`로 종료 확인
4. 추출: `set FLOW_SETUP_STRICT=1` 후 `python setup.py extract` — 종료 코드와 `extract_report.json` 확인
   (새 설치기는 STRICT 없이도 파일 쓰기 실패·불완전한 화면 파일을 실패로 끝냅니다. 구버전 설치기에는 STRICT가 필요합니다)
   opencode로 고치는 설치 폴더는 `python setup.py extract --all`을 씁니다. 기본 추출은 `AGENTS.md`·`docs/`·`tests/`를
   갱신하지 않아 에이전트가 옛 지침·코드 지도를 읽게 됩니다. VM에서 고친 소스는 추출 전에 로컬 git 커밋으로 남깁니다.
5. 의존성이 바뀐 경우만: `python setup.py install-deps`
6. 기동: `Enable-ScheduledTask` → `Start-ScheduledTask`(또는 `flow_run.bat`), `/health`와 `/version.json` 확인
7. 문제가 있으면: 정지 → `python setup.py restore latest` → 기동

보존 대상: `data/`, `flow-data/`, `DB/`, `Base/`, `Fab/`, `wafer_maps/`, `FLOW_DATA_ROOT`·`FLOW_DB_ROOT`·`FLOW_WAFER_MAP_ROOT`
아래 모든 파일(users, sessions, groups, informs, tracker, meetings, dashboard, cache, 로그), 그리고 `flow_env.local.bat`.
설치 전 소형 설정 파일은 사용자 홈의 `.flow_backups`에 스냅숏됩니다.

```bat
python setup.py extract
python setup.py install-deps
python setup.py build-frontend
python setup.py version
python setup.py sync-version
python setup.py restore latest
```

예전 개발 worker 시절 파일(`backend/core/worker_dispatch.py` 등)이 설치 폴더에 남아 있어도 더 이상 불러오지 않으므로
지워도 되고 그대로 둬도 됩니다. `D:\flow-data\worker\` 폴더(옛 작업 큐)도 지워도 됩니다.

### 기본지식을 포함한 로컬 설치본

관리자 **기본지식**의 최신 내용을 새 설치에도 가져가려면 전용 설치본을 만듭니다(본문·제목·편집 지침만,
계정·수정 이력 제외).

```bash
python _build_setup.py --include-domain-knowledge --output "../deliverables/flow-private/setup.py"
```

다른 DB는 `--include-domain-knowledge "/path/to/knowledge/domain_knowledge.sqlite3"`. 설치하면 설치 폴더의
`data/install-seeds/domain_knowledge.json`에 사본을 두고 `FLOW_DATA_ROOT/knowledge/domain_knowledge.sqlite3`에
첫 버전으로 등록합니다. 기존 문서가 있으면 덮어쓰지 않습니다. **사내 지식을 담으므로 로컬 전달 전용이며**,
출력 경로는 이 공개 저장소 밖만 허용됩니다.

## 로그인과 권한

### 접속 IP 로그인 (버튼 하나)

사내 인증서버를 붙이기 전, 지정한 PC에서 [로그인] 버튼만 누르면 들어오게 할 수 있습니다. `FLOW_IP_LOGIN_MAP`에
접속 IP → 사내 ID를 적으면 로그인 화면에 버튼만 남습니다(ID/PW 입력은 꺼지고, 필요하면
`FLOW_PASSWORD_LOGIN_ENABLED=1`). 사내 ID는 WebSocket 로그인과 같은 규칙(`FLOW_WS_AUTH_USER_MAP`)으로 Flow 계정이 됩니다.

```bat
set "FLOW_IP_LOGIN_MAP={"127.0.0.1":"example.user"}"
set "FLOW_WS_AUTH_USER_MAP={"example.user":"hol"}"
```

실제 사번·IP는 `flow_env.local.bat`에만 적고 저장소에 커밋하지 않습니다. IP는 TCP 접속 주소만 보고
`X-Forwarded-For`는 믿지 않으므로 리버스 프록시 뒤에서는 쓰지 마세요. VM 이사로 사용자 IP/NAT가 바뀌면 매핑을 갱신합니다.

### WebSocket 로그인과 관리자 연락처

`FLOW_WS_AUTH_URL`(브라우저가 접속할 로그인 주소)과 `FLOW_WS_AUTH_VERIFY_URL`(Flow가 토큰을 재확인할 주소)을
설정하면 로그인 화면에는 **[사내 로그인] 버튼만** 남고 ID/PW 로그인·회원가입·비밀번호 찾기
(`/api/auth/register`, `/api/auth/forgot-password`, `/api/auth/reset-request`)가 꺼집니다.
setup.py로 푼 설치본(`.git` 없음)과 운영(`FLOW_PROD=1`, `flow_env.bat` 기본값)은 사내 로그인을 아직 설정하지 않았어도
ID/PW 입력칸을 띄우지 않습니다(개발 체크아웃·`FLOW_PROD=0` 격리 테스트만 기본 켜짐). 비상시 `FLOW_PASSWORD_LOGIN_ENABLED=1`.
버튼은 누를 때 연결합니다(`FLOW_WS_AUTH_AUTO=1`이면 화면을 열 때 한 번 자동 시도).

관리자 → **사내 로그인·관리자**에서 계정 ID, 이름, 메일, 역할과 위임 페이지를 등록합니다(관리자는 `admin`,
페이지 위임자는 `user`+페이지 ID). 여기 명시한 관리자·위임자만 권한 계정에 추가되고 Flow 비밀번호는 생기지
않습니다. 일반 사내 로그인 사용자는 부서 규칙을 따릅니다. 연락처는 `auth/people.enc`에 암호화 저장되며
(키 `FLOW_DATA_KEY` 또는 `<설치 폴더>\.flow_data.key` — 커밋 금지), 메일 수신 주소에도 쓰입니다. 행을 비워도
계정은 지워지지 않습니다 — 위임 해제·삭제는 업무 권한·위임과 사용자 관리에서 합니다.

### 관리자 ID·이름·허용 부서 빠른 수정 (VM/OpenCode)

현재 구현 기준입니다. VM에서는 **setup.py를 푼 실제 Flow 설치 폴더**를 OpenCode 작업 폴더로 엽니다.
처음 문서·테스트까지 받을 때는 서버를 정상 종료하고 `python setup.py extract --all`을 실행합니다.
이미 VM에서 수정한 소스가 있으면 먼저 로컬 사본/커밋을 남깁니다(추출하면 소스는 덮어써집니다).
설정값만 바꾸는 작업은 아래 화면·현장 환경변수를 사용하고, 동작 자체를 바꿀 때 표의 구현을 수정합니다.

| 바꿀 내용 | 현재 조정 방법 | OpenCode가 확인할 구현 |
|---|---|---|
| 관리자 추가, 이름·메일·역할 | 관리자 → 운영 → **사내 로그인·관리자**의 관리자·대리인 표 | `frontend/src/features/admin/DepartmentAccessPanel.jsx` → `backend/routers/admin.py`: `ManagerProfileReq`(행), `ManagerProfilesReq`(요청), `manager_profiles_save()` (`GET/POST /api/admin/manager-profiles`) |
| 기존 계정의 역할·연락처 | 관리자 → 운영 → **사용자** | `frontend/src/features/admin/My_Admin.jsx` → `backend/routers/admin.py`: `set_role()`, `set_name()`, `set_email()` |
| 사내 ID와 Flow 계정 ID 연결 | `scripts/windows/flow_env.local.bat`의 `FLOW_WS_AUTH_USER_MAP` | `backend/core/auth_providers.py`: `_ws_user_map()`, `_identity_for_company_user()` |
| 로그인 허용 부서·탭 | **사내 로그인·관리자**의 부서 규칙 표 | `DepartmentAccessPanel.jsx` → `backend/routers/auth.py`: `get_department_rules()`, `save_department_rules()` → `auth_providers.py`: `write_department_rules()`, `department_access()` |
| 검증 응답의 부서·이름 키 | `FLOW_WS_AUTH_DEPT_FIELDS`, `FLOW_WS_AUTH_NAME_FIELDS` 등 | 아래 WebSocket 절의 설정표와 `WebsocketAuthProvider.authenticate()` |

**ID와 이름은 서로 다릅니다.** 인증서버가 확인한 사내 ID(`ws_user`)를 `FLOW_WS_AUTH_USER_MAP`으로 Flow
계정 ID(`username`)에 연결하고, `name`은 화면에 표시할 이름입니다. 역할은 `role="admin"`으로 판정하므로
이름이나 ID를 `admin`으로 적는 것만으로 관리자 권한이 생기지는 않습니다.

- 이미 관리자 계정이 있으면 표에 계정·이름·유효한 메일·`admin`을 입력해 추가/수정합니다. 페이지 대리인은
  `user`와 위임 페이지 ID가 하나 이상 필요합니다. 저장은 **제출한 ID별 추가/갱신**이며, 빠진 행은 삭제하지 않습니다.
  이름·메일을 직접 저장하면 이후 WebSocket 로그인 응답보다 이 수동 연락처가 우선합니다.
- 사내 ID 표기가 바뀌었지만 기존 Flow 기록을 유지하려면 **새 사내 ID → 기존 Flow 계정 ID** 매핑을 바꿉니다.
  관리자 표의 ID를 바꾸는 것은 기존 계정의 이름 변경이 아니라 다른 계정 추가입니다. 일괄 ID 변경 API는 없습니다.
  기존 ID의 게시글 작성자·그룹·위임·세션까지 옮길 작업은 별도 데이터 이관으로 다룹니다.
- 매핑 JSON의 key는 사내 ID, value는 Flow 계정 ID입니다. 기존 대상 계정이 있으면 그 계정의 역할·탭을 쓰며,
  **대상 계정이 없으면 관리자 세션을 만듭니다.** 따라서 일반 사용자 전체를 여기에 등록하지 않습니다. 기본 매핑은
  빈 `{}`이며 실제 ID는 현장 파일에만 둡니다. 새 서버에 관리자가 없어도 이 매핑으로 첫 관리자를 로그인시켜 표에서 등록할 수 있습니다.
- 관리자를 해제할 때는 기존 계정의 역할/위임뿐 아니라 매핑도 확인합니다. 매핑을 남기고 대상 계정을 삭제하면
  다음 로그인에서 다시 관리자 세션이 만들어집니다. 새 관리자 로그인을 먼저 확인한 뒤 기존 권한을 정리합니다.
- 로컬 비밀번호용 초기 계정 `hol`은 `backend/app_v2/runtime/startup.py`의 `ensure_seed_admin()`에 고정되어 있습니다.
  `FLOW_ADMIN_PW`는 없는 `hol`을 최초 생성하는 비밀번호(10자 이상)이며 ID·표시 이름 설정이나 기존 비밀번호 변경용이 아닙니다.
  사내 관리자 변경은 위 표/매핑으로 처리합니다. 초기 ID 기본값 자체를 바꾸려면 이 함수와
  `tests/test_security_and_background_owner.py`의 seed 검사, 설치 안내의 `hol` 표기도 함께 확인합니다.

**부서 규칙의 현재 적용 범위와 우선순위:** `_identity_for_company_user()`는 매핑 후 기존 `users.csv` 계정이
있으면 그 역할·개인 지정·직접 그룹 권한을 우선하며 로그인 허용 부서 규칙은 검사하지 않습니다. WebSocket
재검증 응답의 부서는 기존 계정의 `department`에 갱신하고, 개인 지정이 없으면 권한 그룹의 `기본 부서`를
적용합니다. 매핑 대상이 없으면 앞서 설명한 관리자 경로입니다. 계정이 없는 일반 WebSocket 사용자는 먼저
로그인 허용 부서 규칙을 통과해야 하며, 일치하는 권한 그룹의 탭이 있으면 그 탭을 세션에 적용합니다.
일치하는 그룹이 없으면 부서 규칙의 탭을 사용합니다. IP 로그인은 같은 identity 함수를 쓰지만 부서 claim을
받지 않습니다. WebSocket의 부서는 브라우저 프레임이 아닌 인증서버 재검증 응답에서만 가져옵니다.

| 부서 규칙 | 현재 결과 |
|---|---|
| 규칙이 비어 있음 | 일반 사용자 모두 로그인 가능. 기본 탭은 `FLOW_WS_AUTH_DEFAULT_TABS`(미지정/`__all_user__`이면 관리자 외 허용 탭 전체) |
| 규칙이 하나 이상 있음 | 허용 규칙에 일치하는 부서만 로그인. 일치 없음·부서 정보 없음은 403 |
| `match=exact` / `prefix` | 앞뒤 공백 제거·대소문자 무시 후 전체 일치 / 접두어 일치 |
| 여러 규칙에 일치 | 거부(`allow_login=false`, 화면 `X`)가 우선. 거부가 없으면 허용 규칙들의 탭을 합침 |
| 허용 규칙의 `tabs`가 빈 값/`__all_user__` | 관리자 외 허용 탭 전체. 빈 값이 접근 금지를 뜻하지 않음 |

예를 들어 `공정기술 / prefix / O / splittable,filebrowser`, `공정기술외주 / prefix / X /`를 저장하면
일반 `공정기술1팀` 사용자는 두 탭을, `공정기술외주1팀` 사용자는 로그인 거부를 받습니다(합성 부서명).
제한 운영에서는 허용 목록을 실제로 저장해 둡니다. 현재 `read_department_rules()`는 파일 누락·잘못된 JSON도
빈 규칙으로 취급하므로 파일을 지우거나 깨뜨리는 방식으로 차단하지 않습니다.
기존 계정까지 부서로 제한하거나 규칙이 없으면 거부하도록 바꾸려면 `_identity_for_company_user()`와
`department_access()`의 분기를 수정하고 관리자 진입 경로 및 관련 테스트를 함께 확인합니다.

저장 위치는 `FLOW_DATA_ROOT/users.csv`(역할·상태·탭), `auth/people.enc`(암호화 연락처),
`auth/department_rules.json`(부서 규칙), `admin_settings.json`의 `page_admins`(위임)입니다.
`people.enc`는 직접 텍스트 편집하지 않습니다. 키는 `FLOW_DATA_KEY`, `FLOW_DATA_KEY_FILE` 또는 설치 폴더의
`.flow_data.key`이므로 데이터 이사 때 키도 보존합니다. 운영 파일·실제 ID·부서·메일·키는 공개 저장소나 번들에 넣지 않습니다.

### WebSocket 수신 형식·방식 수정

현재 로그인 흐름:

```text
My_Login.jsx: GET /api/auth/providers → [사내 로그인] → wsLogin.js startWsLogin()
  new WebSocket(ws_url) → 연결 직후 send 전송 → 받은 프레임을 순서대로(binary는 텍스트로)
  → POST /api/auth/sso/ws/login {"message": 프레임, "step": n, "via": "ws"}
backend/routers/auth.py: websocket_login()
  → auth_providers.py: WebsocketAuthProvider.authenticate()
     _ws_parse() → _ws_credentials()  ID·토큰 있음 → _ws_verify()로 인증서버 재확인
                                        주소만 있음   → URL_ACTION: open/browser → HTTP 202 {action,url}
                                                                    server       → _ws_fetch_url() 응답으로 로그인
                                        아무것도 없음 → HTTP 400(브라우저는 다음 프레임 대기)
  → _ws_reject_fixed_user() → _identity_for_company_user() → start_session() → Flow 세션 token
```

막혔을 때 확인·판단 순서(개발자 도구 Messages, `FLOW_WS_AUTH_DEBUG`, 받은 것별 조치표)는
`AGENTS.md`의 **WebSocket 사내 로그인이 안 될 때** 절에 있습니다.

브라우저는 수신 문자열을 그대로 전달합니다. 서버 `_ws_parse()`는 JSON 문자열을 객체로 풀고 `_ws_pick()`은
필드 목록에서 **첫 번째 비어 있지 않은 문자열/정수**를 선택합니다. key는 대소문자를 무시하고 `data.user.id`처럼
점 경로로 중첩 객체를 읽습니다(배열 인덱스 경로는 지원하지 않음). 부서·이름·메일은 **인증서버 재검증 응답**에서
읽습니다. 브라우저 메시지에만 부서를 붙여도 허용 부서 판정에 쓰지 않습니다.

| 현장 환경변수 | 현재 기본값·의미 |
|---|---|
| `FLOW_WS_AUTH_URL` | 브라우저가 접속할 `ws://`/`wss://` 주소. 비면 WebSocket 로그인 비활성 |
| `FLOW_WS_AUTH_SEND` / `FLOW_WS_AUTH_AUTO` | 연결 직후 보낼 문자열(기본 전송 없음, **사용자 ID를 넣지 않음**) / 화면 진입 시 자동 시도(기본 `0`=버튼을 눌러야 연결, 켜려면 `1`). URL·SEND의 `{nonce}`(시도마다 새 값)·`{origin}`(Flow 주소)은 브라우저가 채움 |
| `FLOW_WS_AUTH_CONTACT` | 로그인 화면·거부 메시지에 붙일 문의처(합성 예: `example.admin` → "문의 example.admin"). 실제 문의처는 현장 `.local.bat`에만 둔다. 비면 "관리자에게 문의" |
| `FLOW_WS_AUTH_USER_FIELDS` | 사내 ID 후보. 기본 `user_id,userId,userid,username,user_name,user,loginId,login_id,sAMAccountName,ad.sAMAccountName,id,empNo,emp_no,sabun,sub,data.user_id,data.userId,data.id,user.id,ad.user_id,ad.userId,ad.id,ad.mail`(AD 로그인 ID `sAMAccountName`이 있으면 그것, 없으면 AD 메일을 ID로 쓰고 사내 도메인은 떼어 기존 계정과 맞춤). AD 형식 `{"ad": {"department","company","mail","title","description","name"}}`의 부서·이름·메일은 기본 후보(`ad.department`, `ad.name`, `ad.mail`)로 읽힘. http(s) 주소 값은 ID로 쓰지 않음 |
| `FLOW_WS_AUTH_TOKEN_FIELDS` | 인증서버 토큰 후보. 기본 `token,access_token,accessToken,ticket,session,sessionId,session_id,data.token,data.ticket` |
| `FLOW_WS_AUTH_DEPT_FIELDS` | 기본 `department,dept,deptName,dept_name,deptNm,orgName,org_name,org,team,data.department,data.dept,user.department,ad.department` |
| `FLOW_WS_AUTH_NAME_FIELDS` | 기본 `name,userName,user_name,displayName,display_name,korName,kor_name,data.name,user.name,ad.name` |
| `FLOW_WS_AUTH_EMAIL_FIELDS` | 기본 `email,mail,emailAddress,email_address,data.email,user.email,ad.mail,ad.email` |
| `FLOW_WS_AUTH_VERIFY_URL` / `FLOW_WS_AUTH_VERIFY` | 서버의 재검증 주소 / `http` 또는 `ws`. mode 미지정 시 verify URL이 HTTP면 `http`, 나머지는 `ws`; URL·mode 둘 다 없으면 기본 거부 |
| `FLOW_WS_AUTH_VERIFY_SEND` | 검증 요청 문자열 템플릿. 기본 `{"token": "{token}"}`. `{token}`·`{user}`를 추출값으로 치환(JSON 템플릿이면 따옴표 등을 JSON 규칙으로 넣음) |
| `FLOW_WS_AUTH_VERIFY_METHOD` / `FLOW_WS_AUTH_VERIFY_HEADERS` | HTTP 재검증 `POST`(기본)·`GET`. VERIFY_URL에도 `{token}`·`{user}` 치환(URL 인코딩) / 헤더 JSON(예 `{"Authorization": "Bearer {token}"}`) |
| `FLOW_WS_AUTH_VERIFY_MAX_FRAMES` | WS 재검증에서 ID나 거부가 든 응답을 기다릴 최대 프레임 수, 기본 3 |
| `FLOW_WS_AUTH_VERIFY_TIMEOUT_SEC` | 서버 재검증·주소 읽기 제한시간, 기본 10초. 브라우저 대기는 `wsLogin.js`에서 60초(인증 창을 연 뒤 180초) |
| `FLOW_WS_AUTH_URL_FIELDS` | ID·토큰 없이 주소만 온 프레임에서 주소를 찾을 후보. 기본 `url,redirect,redirectUrl,redirect_url,redirectUri,redirect_uri,loginUrl,login_url,authUrl,auth_url,href,location,link,data.url,data.redirectUrl,data.loginUrl,data.authUrl`. 문자열 프레임이 `http(s)://`면 그 자체 |
| `FLOW_WS_AUTH_URL_ACTION` | 주소만 온 프레임 처리. `open`(기본: 인증 창을 열고 같은 연결에서 다음 프레임 대기) · `server`(Flow 서버가 주소를 직접 GET, 응답의 ID로 로그인) · `browser`(브라우저가 쿠키·Windows 인증으로 GET한 본문을 다시 넘겨 재검증. 인증서버 CORS 필요) |
| `FLOW_WS_AUTH_FETCH_ALLOW` | `server` 방식에서 읽어도 되는 주소 접두어(쉼표, scheme·host·port·경로 접두어 일치). 비면 `server`는 거부. 리다이렉트는 따라가지 않음 |
| `FLOW_WS_AUTH_DEBUG` | `1`이면 받은 프레임 모양(키 구조·값 종류, 값은 가림)과 판정을 `FLOW_DATA_ROOT/logs/ws_login_probe.jsonl`에 최근 200건 기록. 관리자 `GET /api/auth/sso/ws/probe`로 기록과 실행 중 설정 요약을 봄. 기본 끔 |

설정 문자열(`FLOW_WS_AUTH_URL`·`SEND`·`VERIFY_URL`·`VERIFY_SEND`·`VERIFY_HEADERS`)에 확인된 사용자 ID가 그대로 들어 있으면
로그인을 403으로 막습니다. ID를 넣어 보내면 인증서버는 그 사람 정보를 돌려줄 뿐이라 누가 눌러도 같은 사람이 되기 때문입니다.

`*_FIELDS`는 쉼표로 나열하며 **기본 목록을 대체**합니다. ID 목록은 브라우저 수신과 서버 재검증 응답에 공통으로
쓰므로 두 응답의 경로를 모두 넣습니다. 단순 key 변경은 환경변수로 해결하고, 일반 기본값을 바꿀 때는
`auth_providers.py`의 `_WS_DEFAULT_*_FIELDS`도 수정합니다.

다음은 합성 메시지 예입니다. 브라우저가 `{"data":{"employee":{"id":"example.user"},"ticket":"sample-ticket"}}`를
받고 재검증 응답이 `{"ok":true,"employee":{"id":"example.user","department":"공정기술1팀","name":"예시 사용자","email":"user@example.com"}}`라면,
현장 파일에 아래 경로를 지정합니다(실제 URL·토큰은 이 문서에 넣지 않음).

```bat
rem scripts\windows\flow_env.local.bat (Miniforge Prompt / CMD 문법)
set "FLOW_WS_AUTH_USER_FIELDS=data.employee.id,employee.id"
set "FLOW_WS_AUTH_TOKEN_FIELDS=data.ticket"
set "FLOW_WS_AUTH_DEPT_FIELDS=employee.department"
set "FLOW_WS_AUTH_NAME_FIELDS=employee.name"
set "FLOW_WS_AUTH_EMAIL_FIELDS=employee.email"
```

**수신 방식을 바꿀 때의 수정 지점:**

- 브라우저 연결 옵션·첫 송신·프레임 순차 처리·binary/Blob 변환·202(주소) 처리는 `frontend/src/features/auth/wsLogin.js`의
  `startWsLogin()`. 모든 프레임을 순서대로 POST하며 HTTP 400은 중간 메시지로 보고 계속 기다리고, 202는 인증 창을 열거나
  (`open`) 주소를 읽어 다시 넘깁니다(`browser`). 연결이 닫히거나 다른 오류면 종료합니다.
- JSON wrapper·배열·문자열 프로토콜은 `auth_providers.py`의 `_ws_parse()`, `_ws_credentials()`, `_ws_frame_url()`, `authenticate()`.
  일반 문자열은 `http(s)://`면 주소, 아니면 ID로만 읽어 토큰이 없으므로 정상 재검증 로그인에 쓸 수 없습니다.
- 서버 HTTP 헤더·Bearer 토큰·GET 요청·인증서버 성공 코드·서버 WS의 추가 handshake/중간 프레임은 `_ws_verify()`.
  HTTP는 `VERIFY_METHOD`(POST 기본)·`VERIFY_HEADERS`, WS는 한 번 보내고 ID나 거부가 든 프레임까지 최대 `VERIFY_MAX_FRAMES`개를 읽습니다.
  명시적 `FLOW_WS_AUTH_VERIFY=ws`에서 verify URL이 비면 브라우저 URL을 재사용합니다.
- 현재 성공 판정은 `ok` → `success` → `result` 우선이며 `false/fail/error/0`을 거부하고, 확인 ID가 있어야 통과합니다.
  브라우저 ID도 있으면 재검증 ID와 대소문자 무시 일치해야 합니다. 프로필은 검증 응답에서 추출하는 규칙을 유지합니다.
  요청 템플릿이 JSON(`{`·`[`로 시작)이면 치환값을 JSON 문자열 규칙으로 넣습니다. 그 밖의 템플릿은 단순 문자열 치환입니다.
- 사내 토큰과 Flow 세션 `token`은 별개입니다. `start_session()` → `core/auth.py: issue_token()` 경로를 유지하고
  인증서버 토큰·비밀번호·수신 원문을 로그/`tokens.json`/공개 문서에 남기지 않습니다.
  `FLOW_WS_AUTH_VERIFY=none` + `FLOW_WS_AUTH_TRUST_CLIENT=1`은 재검증을 생략하고 클라이언트 프로필까지 믿는 예외이므로
  로그인 오류 해결책으로 적용하지 않습니다.

### VM 수정 반영·검증

- 화면에서 저장한 관리자·부서 설정은 서버 재시작이 필요 없습니다. 관리자·대리인 표에서 권한을 바꾸거나 사용자 화면에서
  역할을 바꾸면 해당 계정 세션은 무효화됩니다.
  부서 규칙·ID 매핑 변경은 이미 발급된 일반 사용자 세션을 일괄 회수하지 않으므로 로그아웃 후 **새 로그인**으로 확인합니다.
- **`flow_env.local.bat` 변경은 감시기까지 정상 종료 후 다시 기동**합니다. `flow_ctl.bat restart`는 API 자식만
  재기동해 실행 중인 감시기의 옛 환경변수를 그대로 전달합니다. Miniforge Prompt에서 `scripts\windows\flow_ctl.bat stop`
  후 `status`로 감시기 종료를 확인하고 `scripts\windows\flow_run.bat`을 실행합니다. 예약 작업 운영이면 같은 종료 확인 뒤
  PowerShell의 `Start-ScheduledTask -TaskName FlowWebApp`으로 다시 시작합니다.
- Python 소스만 바꿨으면 관련 검증 후 `scripts\windows\flow_ctl.bat restart`. 화면 소스는
  `cd frontend` → `npm run check`(검사+dist 빌드) 후 브라우저 새로고침으로 반영합니다.
- 검증은 임시 `FLOW_DATA_ROOT`·`FLOW_DB_ROOT`·`FLOW_WAFER_MAP_ROOT`·`FLOW_DATA_KEY_FILE`, `FLOW_PROD=0`으로
  운영 데이터와 분리합니다. `tests/test_websocket_auth.py`, `tests/test_manager_profiles.py`,
  `tests/test_group_departments_ip_login.py`를 우선 실행하고, 초기 계정 동작을 바꾸면 seed 테스트도 실행합니다.
  기존 WS 테스트는 주요 경로의 `_ws_verify()`를 mock하므로 통과해도 실제 사내 인증서버 연결이 검증된 것은 아닙니다.
  프로토콜을 바꾸면 송신 payload·중간 프레임·검증 ID 불일치·클라이언트 부서 위조에 대한 검증도 추가합니다.
- 현장에서는 `GET /api/auth/providers`, 브라우저 개발자 도구의 WS 연결/프레임과 `/api/auth/sso/ws/login` 응답,
  새 로그인 뒤 `/api/auth/me`의 ID·역할·탭을 확인합니다. **새 관리자, 허용 부서, 거부 부서, 부서 없음**을 각각 확인합니다.
  로그인 API의 400은 ID/토큰 추출, 401은 토큰·검증 ID, 403은 부서/검증 설정, 503은 재검증 설정,
  502는 VM→인증서버 연결 실패부터 조사합니다. 관리자 프로필 API의 503은 암호화 키/연락처 저장도 확인합니다.
  개발자 도구 캡처는 토큰·개인정보를 가리고 공유합니다.
- VM 수정은 다음 `setup.py extract`에서 소스가 덮어써집니다. 로컬 patch/커밋을 보관하고,
  다른 설치본에 전달할 일반 코드·문서 변경은 개발 체크아웃에서 `python _build_setup.py`로 `setup.py`까지 재생성합니다.
  현장 ID·URL·키·운영 데이터는 번들에 넣지 않습니다.

### 사내 OIDC SSO

설정이 없으면 비활성입니다. 아래 값을 환경변수로 넣고 재시작하면 로그인 화면에 `SSO Login`이 나타납니다.
Client Secret은 `.env`, Git, 프런트 코드에 커밋하지 않습니다.

```env
FLOW_OIDC_ISSUER=https://sso.company.example/oidc
FLOW_OIDC_CLIENT_ID=flow-client-id
FLOW_OIDC_CLIENT_SECRET=secret-from-sso-team
FLOW_OIDC_REDIRECT_URI=https://flow.company.example/api/auth/sso/oidc/callback
FLOW_OIDC_USERNAME_CLAIM=preferred_username
FLOW_OIDC_DEPARTMENT_CLAIM=department
FLOW_OIDC_AUTO_PROVISION=false
```

Discovery 주소가 `ISSUER + /.well-known/openid-configuration`과 다르면 `FLOW_OIDC_DISCOVERY_URL`, IdP가 client
secret을 POST body로만 받으면 `FLOW_OIDC_CLIENT_AUTH_METHOD=client_secret_post`(기본 `client_secret_basic`).
ID token은 RS256이어야 합니다. 검증이 끝나면 `FLOW_PASSWORD_LOGIN_ENABLED=false`로 SSO만 남길 수 있습니다.

SSO 사용자는 `users.csv` 계정과 매칭되고 역할·탭·제품 노드 권한은 Flow가 관리합니다. 로그인 때 OIDC `sub`와
부서 claim을 `sso_id`, `department` 열에 동기화합니다. 권한 그룹의 `기본 부서`에 SSO 부서명을 연결하면, 개인 권한이나
직접 그룹이 없는 사용자는 그 그룹 권한을 상속합니다(개인 지정 → 직접 그룹 → 부서 기본). 기본값은 로컬 계정이
없는 SSO 사용자를 거부하며, `FLOW_OIDC_AUTO_PROVISION=true`면 권한 없는 `pending` 계정으로 만들어 관리자 승인을
기다립니다. 인증팀에 callback URL을 Redirect URI로 정확히 등록해 달라고 요청하고, 서버가 Discovery/Token/JWKS에
HTTPS로 나갈 수 있는지 확인합니다. PKCE·state·nonce·issuer·audience·expiry·RS256 서명을 모두 검사하며 세션 토큰은
callback query string에 넣지 않습니다.

### TEG 제품 노드 권한

TEG 제품은 `상위 노드 / 하위 노드 / 제품명` 계층입니다. 경로는 `TEG_Product_Info.csv`와 TEG 설정의
`product_nodes`에 저장되고, 이름 변경은 `Chip_Radius.csv`, `Teg_location.csv`, `Main_chip_info.csv`,
`TEG_Product_Info.csv`와 제품별 Mapfile/Inline 설정에 함께 반영됩니다. 최상위 노드별 `node_access` 규칙이 없으면
공개, 있으면 허용 사용자·부서만 목록과 API에서 접근합니다(관리자는 항상 전체). 부서는 세션의 `department`,
`departments`, `dept`, `department_name`, `org`, `org_name` 값과 비교합니다.

## 기능별 운영 메모

### SplitTable 캐시와 성능

제품별 필수 캐시는 ① 랏 lookup ② `root_lot_id`별 pivot ③ WIP latest-lot ④ root별 FAB latest 인덱스입니다.
캐시관리의 통합 캐싱은 실제 단계가 끝날 때까지 작업 큐에 남아 진행 상황과 중단 버튼을 제공하고, 중단 시 현재
안전 배치까지만 마칩니다. 제품 전체 RAM·Root lot RAM 예열은 쓰지 않습니다. ET history는 ET 추적에서 따로
관리하며 SplitTable 필수 캐시에 포함하지 않습니다.

- 조회는 제품 전체가 아니라 root partition/pivot 파일 하나만, 필요한 prefix/custom 컬럼만 읽습니다.
- Root Lot 후보·LOT ID 목록·KNOB 입력 후보는 lookup 빌드 때 함께 계산해 둡니다. 캐시가 없으면 원천을 동기
  스캔하지 않고 빌드만 큐에 넣고 즉시 응답합니다(준비 중에도 Root Lot 직접 입력 조회 가능).
- pivot은 root 1개씩 만들고, 실패하면 최대 3번까지 다시 시도합니다.
- `large` 서버는 자동 제품 캐싱이 켜져 모든 제품의 root 인덱스·pivot을 미리 만들고, 자주 찾는 root와 최근 공정이
  진행된 root의 KNOB 화면을 30분마다 미리 계산합니다(사용자 요청에 양보, 메모리 압박이면 중단). 끄기:
  ⚙ 캐시 설정 또는 `FLOW_SPLITTABLE_AUTO_PRODUCT_CACHE_ENABLED=0`, 예열은 `FLOW_SPLITTABLE_KNOB_PREWARM=0`
  (개수 `…_MAX_LOTS`, 기간 `…_RECENT_DAYS`).
- **원천 변경 기반 갱신(기본 꺼짐, `FLOW_CACHE_CHANGE_DRIVEN=1`로 활성화).** 자동 제품 캐싱은 제품마다 입력 지문(FAB 원천 폴더 전체의 파일 목록·수정
  시각·크기, 그 제품 ML_TABLE, 설정·step matching 파일, 앱 버전)을 마지막 성공 때와 비교해, 같고 산출물이 모두
  있으면 작업을 만들지 않고 넘어갑니다. WIP latest-lot·FAB 매칭 캐시도 "30분 지남"이 아니라 원천 지문이 바뀌었을 때
  다시 만들고(바뀌었으면 30분 전이라도 다시 만듦), 대시보드 최신 랏 파일은 바뀐 제품의 행만 다시 씁니다. 지문이
  못 보는 변경에 대비해 6시간마다 제품 파이프라인을 확인하고, WIP·매칭은 실제 생성 시각이 6시간을 넘으면 재스캔합니다
  (`FLOW_CACHE_FULL_RECHECK_HOURS`). `FLOW_CACHE_CHANGE_DRIVEN=0` 또는 미설정이면 예전 나이 기준입니다.
  캐시 파일 삭제·입력 읽기 실패는 재생성 대상이며, 실패한 WIP를 제품 순환 성공으로 기록하지 않습니다.
  캐시관리 화면의 캐싱 진행 카드에 이번·지난 순환의 건너뜀·갱신 수와
  마지막 재생성 사유(첫 실행·원천 변경: fab/ml_table/settings/…·주기 재확인·산출물 없음)가 보입니다.
- WIP latest-lot 의 FAB 스캔은 사용자가 있을 때 **1초 훑을 때마다 최대 0.5초** 양보합니다
  (`FLOW_LOT_PROGRESS_YIELD_EVERY_SEC`·`FLOW_LOT_PROGRESS_YIELD_MAX_WAIT_SEC`). 예전에는 파일마다 최대 3초를 쉬어,
  사용자가 끊이지 않는 서버에서 FAB 파일이 많으면 WIP 갱신이 몇 시간 걸리며 제품 순환을 붙들었습니다.
- 목표는 준비된 데이터 조회부터 표 첫 표시까지 p95 500ms입니다(원본만 있고 캐시가 없는 최초 생성은 제외).
- 조회 결과 RAM 캐시는 최근에 쓴 응답(예산의 15%, `FLOW_SPLITTABLE_VIEW_HOT_FRACTION`)만 파이썬 객체로 두고
  나머지는 orjson bytes 로 접어 둡니다(같은 예산에 약 6배, 다시 쓰이면 풀어서 올림, 큰 응답 하나 푸는 데 수 ms).
  끄기 `FLOW_SPLITTABLE_VIEW_PACK=0`. 캐시관리 메모리 화면의 `packed_entries`가 접힌 항목 수입니다.

샘플 데이터 검증(운영 데이터 아님): 서로 다른 5개 root 동시 조회 약 312ms, 순차 조회 약 56~86ms, 동일 조건
재조회 약 8.7ms, cold partition 5개 동시 조회 약 433ms(peak RSS 증가 약 72MB). 운영 속도는 parquet 폭·root당
행 수·스토리지 성능에 따라 달라집니다.

캐시관리 화면은 제품별 상태(행을 누르면 lookup·pivot·WIP latest-lot·FAB 인덱스의 최근 성공·실패·진행 상세),
스캔 단계별 `queued / running / done / failed`, 실행 중·대기 작업, 무거운 작업 현황, API RSS와 peak 증가량을 보여 줍니다.

### 차트생성·Template Report

차트 데이터 조회는 동시에 제한된 수만 실행하고(`FLOW_CHART_BUILDER_CONCURRENCY`), 같은 Query/JOIN/필터 결과는
짧게 재사용합니다(`FLOW_CHART_BUILDER_CACHE_MB`, `FLOW_CHART_BUILDER_CACHE_TTL_SEC`). Template Report는 같은 데이터
정의의 차트를 한 번만 조회합니다. `root_lot_id`·`wafer_id`·`color` 목록은 스프레드시트형 편집표로 Excel/Google
Sheets 여러 셀을 그대로 붙여 넣을 수 있고, 조합별 색상 규칙은 이후 데이터에도 적용됩니다. 시간 기준 색상
(`tkout_time WITHIN N DAYS`)은 Query 조회 기간(`RECENT_DAYS`)과 독립입니다.

### ET 조회·TEG 위치 조회

- ET 조회(`/api/reformatize/run`)의 첫 계산은 우선순위를 낮춘 상주 계산 프로세스에서 합니다. 캐시 적중·페이지
  넘김은 바로 처리하고, 같은 조건을 여러 명이 동시에 조회하면 한 번만 계산합니다. 조절: `FLOW_REFORMATIZE_RUN_PROCS`,
  `…_THREADS`, `…_TIMEOUT_SEC`, `…_MAX_RSS_MB`, `…_IDLE_SEC`, `FLOW_REFORMATIZE_CHILD_NICE`, 끄기 `FLOW_REFORMATIZE_RUN_ISOLATION=0`.
- ET 측정 이력은 제품별 history parquet를 공유합니다. 첫 생성 후에는 최근 3일만 재집계해 병합합니다.
- TEG 위치 조회는 Chip_Radius·Teg_location·MAIN·Product Info를 파일 지문이 바뀔 때만 다시 읽습니다.
- TEG 조회 권한과 제품 노드 접근 규칙은 그대로 적용됩니다. 조회 가능한 제품 안에서는 일반 사용자도 TEG를
  모두 동시에 선택할 수 있습니다. shot 확대에서 40개를 넘게 선택하면 겹치는 이름 대신 마커에 마우스를 올려
  이름을 확인합니다.

### FAB 매칭알람 검사

파일탐색기 폴더 설정에서 표시명이 정확히 `FAB`인 DB의 제품 폴더를 **운영 서버가 하나씩, 사용자 요청이 조용할 때**
검사하고 결과를 `FLOW_DATA_ROOT`에 저장합니다(무거운 작업 슬롯 1건, 메모리 확인 후 실행). 화면의 `지금 다음 제품 검사`는
요청만 등록하며 HTTP 요청이 Parquet를 직접 읽지 않습니다. 기본 간격은 제품당 2시간(톱니바퀴에서 변경, 페이지 관리자 이상).

- `step_id`가 `Vehicle_matching.csv`에 없으면 신규 step 알람
- 매칭된 step의 function step에 연결된 `ppid_knob.csv` split별 Rule(`eq`/`contains`/`starts_with`/`ends_with`/`regex`)에
  맞지 않아 `RO`로 빠지는 PPID만 PPID 알람
- 신규 step 알람은 제품 범위와 PPID/EQP ID/EQP MODEL 조건으로 예외 처리
- 신규 `reticle_id`는 `mask_info.csv`의 기존 `category`(mask 이름)·`product`(vehicle) 열에 추가, 새 열을 만들지 않음

판정은 파일탐색기 단일 파일 저장 흐름으로 버전 스냅샷과 변경 메모를 남기고, 매칭 캐시 갱신 후 재검사를 요청합니다.
미매칭 step에는 **function step 추천**(같은 PPID를 쓰는 매칭 step 우선, 없으면 가까운 번호의 eqp/area 비교)이 뜨며
LLM 없이도 동작합니다.

### 파일점검과 홈 에이전트 매칭 CSV 편집

`데이터 → 파일점검`은 SplitTable이 읽는 `Vehicle_matching.csv`의 `step_desc`를 기준으로
`ppid_knob.csv`와 `vm_matching.csv`에서 매칭되지 않는 값과 원본 CSV 행 번호를 보여 줍니다.
파일 누락·열 누락·빈 `step_desc`도 알려 주며, 점검은 파일을 변경하지 않습니다.
이 매칭 CSV들은 운영 DB 루트의 단일 파일을 사용합니다.
SplitTable에서 Vehicle·Inline·VM 파일명을 별도로 설정한 경우 점검과 홈 편집은 해당 DB 파일을 사용하며,
미리보기에서 실제 저장 파일명을 보여 줍니다. 기존 파일로 대체해 점검한 경우에는 지정 파일 누락을 경고합니다.

홈 에이전트에서 `ppid_knob.csv`, `Vehicle_matching.csv`, `Inline_matching.csv`,
`vm_matching.csv`, `mask.csv`, `mask_info.csv`를 조회하고 수정할 수 있습니다. 변경 권한이 있는 사용자는
파일명과 `추가`·`수정`·`삭제`를 명시하고 Excel 표를 붙여 넣습니다. 수정·삭제 표에는
먼저 조회한 CSV `row_number` 열을 넣습니다(머리글이 1행, 첫 데이터가 2행).
단일 셀 수정은 `12행 category를 NEW로 수정`처럼 요청할 수도 있습니다.
에이전트가 영향받는 행의 변경 전후 값과 저장 위치를 표로 보고하고 승인을 요청합니다.
그 다음 **별도 메시지에서 승인**해야 저장됩니다. 승인 전 파일이 바뀌면 다시 미리보기를
만들어야 합니다. 기존 파일은 로컬에 백업되며, 매칭 캐시는 저장 후 갱신됩니다.

PPID 규칙을 저장할 때 같은 feature·PPID에 서로 다른 category가 있고 한쪽이
`~~tkout_time` 및 `>=` 계열 조건이면, 정확 일치(`eq`) 규칙이 먼저 오도록 `R#` 또는 숫자 우선순위를
조정합니다. 같은 순서의 행은 AND 조건 묶음이므로 함께 이동합니다. 우선순위 충돌이
모호하면 임의로 바꾸지 않고 알림을 남깁니다.

Valve 연동 알람 파일 위치는 `data/flow-data/valve_alerts.json`의 `local_root`가 정합니다(비면 S3). S3가 없으면
`{"local_root": "{db_root}", "alerts_prefix": "valve-alerts"}`처럼 공유 DB 폴더를 씁니다(`{db_root}`/`{data_root}`/`{app_root}`
토큰 지원). 예시 알람: `python scripts/seed_valve_alert_examples.py --write`.

### Auto report

요청은 작업 파일로 저장되고, 운영 서버의 러너가 **오래된 요청부터 한 번에 한 건씩** 무거운 작업 슬롯에서 PPT를 만듭니다
(원 렌더러는 별도 자식 프로세스). 메모리가 부족하거나 다른 캐시 작업이 돌면 대기하고, 서버가 재시작되면 실행 중이던 작업을
한 번 다시 대기열에 넣습니다(두 번째 중단은 실패 처리). ET history는 6시간마다(`FLOW_AUTO_REPORT_HISTORY_INTERVAL_SEC`)
스캔 슬롯으로 갱신합니다. 실행 파일과 자산은 `<DB>/Auto report`, reformatter CSV는 `<DB>/reformatter`에 둡니다.

### 운영 점검 스캔

관리자 → 에이전트 → 운영 점검 스캔. 파일(DB 루트)·서버 자원·라이브러리·운영 설정을 **읽기만** 해서 점검 결과와
(LLM이 있으면) 추천을 만듭니다. 설정은 아무것도 바꾸지 않습니다. 버튼 외에 **매일 한 번(기본 07시)** 자동으로 돌고,
높음·보통 항목이 있으면 관리자 전원에게 알림(bell)을 남깁니다(알림을 누르면 이 화면이 열림). 시각·AI 사용·알림 조건은
화면 상단에서 바꾸고, `FLOW_DISABLE_OPS_SCAN_SCHEDULE=1`이면 자동 실행이 꺼집니다.

### 사내 LLM (Gemma4)

관리자 → LLM 설정에서 provider `gemma4`를 고르면 호출 제한시간 최소 60초, JSON 계획·추출 호출 temperature 0.1이
적용되고, 기본지식·제품 위키·3D 구조는 질문과 관련된 항목부터 글자 예산 안에서만 보냅니다(`FLOW_LLM_CONTEXT_SCALE`, 기본 1).
LLM을 부르는 새 API 경로는 `backend/core/llm_adapter.py`의 `_DATA_TASK_PATHS`에 등록해야 합니다.

홈 에이전트 계획 프롬프트는 고정 내용(도구·제품 목록)을 앞에, 질문을 맨 뒤에 두어 서버 prefix cache 가
재사용할 수 있게 합니다(`backend/core/data_chat.py` `_feature_plan`).

### 랏 배정/요청과 게시판 본문

`업무 → 랏 배정/요청`은 제품별 랏 배정·Hot grade·PI 처리 요청 보드입니다. 상태 변경·답변·메일은 `lotrequest`에
위임된 사용자만, 본문·답변 수정은 작성자만 가능합니다(관리자도 소유권을 우회하지 않음). 이력은 등록·수정·상태 변경·
답변·메일 발송을 한 줄 로그로 보여 주고, 메일에는 본문·대상 랏·답변·이력이 들어갑니다. 요청 유형과 요청 팀 목록은
톱니바퀴에서 관리합니다. 랏 요청·PI 답변·Inform Note는 게시판형 편집기를 써서 Ctrl+V로 이미지와 Excel 표를 본문에
넣을 수 있고, 저장 시 서버가 허용 태그와 Flow 내부 이미지 경로만 남깁니다. ET 추적과 Inform 등록은 레코드를 먼저
저장해 바로 보여 주고, LOT 진행·매핑·스냅샷 같은 무거운 보강은 응답 뒤에 채웁니다.

### 관리자 모니터의 보도블럭 갈기

기본 예약은 한국 시간 매일 11:00, CPU·RAM 목표 85%, 최대 10분입니다. 90% 안전선에 닿거나 관리자가 해제하면 멈추고
임시 RAM을 반환합니다. 운영(`PATHS.is_prod`)에서만 돌고, 놓친 예약은 당일 한 번만 따라잡습니다. 합성 부하가 필요 없으면
관리자 모니터에서 **예약 설정 자체를 끄거나** `FLOW_SYSMON_ENABLE_LOAD=0`을 지정합니다.
명시적인 0/false/no/off는 유휴·일일 자동 합성 부하를 모두 차단합니다. 미설정 시 기존 일일 예약 정책을 따릅니다.
예약 파일의 활성 여부·시각·최근 실행 기록은 바꾸지 않으며, 차단을 해제하면 원래 예약을 다시 따릅니다.
관리자가 직접 시작하는 수동 부하는 이 자동 차단과 별개입니다. 환경 변경은 감시기까지 stop 후 다시 기동합니다.
정기 부하가 공급자의 자원 회수 방지를 보장하지는 않습니다.

## 서버 이사 체크리스트

| 구분 | 위험 | 확인·대응 |
|---|---|---|
| 경로 | 옛 `admin_settings.json`·⚙ 캐시 설정의 절대경로·`pool_fraction`이 새 서버를 옛 경로/작은 예산으로 묶음 | 이전 스크립트가 `data_roots`를 지움. 관리자 → 데이터 루트, 캐시관리 ⚙에서 확인 |
| 경로 | 설정 파일에 남은 `/config/work/...`·`\\옛서버\...` 경로(Valve `local_root`, 백업, 메일/LLM) | `D:\flow-data`에서 검색해 `{db_root}` 토큰이나 D: 경로로 교체 |
| 옛 설정 | `FLOW_SERVER_ROLE`, `FLOW_WORKER_OFFLOAD`, `FLOW_API_SERVER_URL` 등 개발 worker 설정 | 효과 없음. 운영 점검 스캔이 알려 주면 `flow_env*.bat`에서 삭제 |
| 옛 고정값 | `FLOW_CPU_BUDGET_CORES`, `FLOW_PROCESS_MEMORY_LIMIT_GB`, `POLARS_MAX_THREADS`, `FLOW_DUCKDB_THREADS` 등 | 자동 인식이 맞으면 지우고 재시작(Polars 풀은 시작 때 고정). 캐시관리 → 검색 코어는 **자동** 저장 |
| 디스크 | `D:`가 동적 확장 가상디스크·네트워크 드라이브면 느림 | 로컬 고정 디스크(SSD) 권장. DB + 실측 캐시·업무 자료 + 백업 최근 3개(설정 최대 5개) + 작업 중 임시 공간을 합산. [용량 진단](docs/LOG_STORAGE_OPERATIONS.md) 참고 |
| 백신 | Defender 실시간 검사가 parquet·캐시 파일마다 끼어듦 | 승인 하에 `D:\DB`, `D:\flow-data`, 설치 폴더, conda env 검사 제외 |
| 메모리 | VM 동적 메모리면 총량이 작게 보여 `small`로 뜸 | 메모리 고정 128GB. 모니터에서 `large` 확인(강제 `FLOW_RESOURCE_PROFILE=large`). 페이지 파일은 끄지 않음 |
| 전원 | 절전·자동 업데이트 재부팅 | `-DisableSleep`, Windows Update 재부팅 창 설정, 부팅 자동 시작 |
| 네트워크 | 방화벽 8080, 호스트명 해석 | `-OpenFirewall`, 고정 IP, `http://<IP>:8080` 안내 |
| 로그인 | IP 로그인 매핑이 이사 후 IP/NAT 변경으로 안 맞음 | `flow_env.local.bat` 매핑 갱신 |
| 인코딩 | 한글 경로·cp949 콘솔 | `flow_env.bat`이 `PYTHONUTF8=1` 설정. 설치 경로에 한글·공백 피하기 |
| 외부 연결 | LLM·메일·S3가 새 VM에서 막힘 | LLM 연결 테스트, 메일 테스트 발송, S3 동기화 1회 수동 실행 |
| 동시 사용 | 대화형 요청과 캐시 빌드 경합 | 빌드는 한 번에 1건·사용자 요청 양보. 첫 주는 캐시관리 → 검색 속도와 운영 점검 스캔 알림 확인 |
| 백업 | 백업 위치 | 기본 `D:\flow-backups`. 다른 디스크/NAS는 관리자 → 자동 백업 경로 |

## 개발자 안내

- 문서: 규칙은 `AGENTS.md`, 동작 구조는 `docs/ARCHITECTURE.md`, 파일 위치는 `docs/CODEMAP.md`, 개선 계획은 `docs/PLAN.md`
  (설치 폴더에는 `python setup.py extract --all` 일 때 풀립니다).
- 백엔드 앱은 `backend/app.py`(루트 `app.py`는 import shim), HTTP 경로는 `backend/routers/`, 계산·저장은 `backend/core/`.
  SplitTable·파일탐색기 라우터 일부는 `backend/app_v2/modules/*/router_parts/`에서 조립되므로 해당 part를 고칩니다.
- 탭 등록은 `frontend/src/app/pageManifest.jsx`, 구현은 `frontend/src/features/`. 색·간격은 `frontend/src/styles/tokens.css`.
- 무거운 계산은 `core/heavy_jobs.run_heavy()`를 거칩니다(메모리 확인·캐시 슬롯·사용자 양보). 새 예약 작업은
  `app_v2/runtime/startup.py`의 background owner 목록에 넣습니다.
- 새 backend 모듈은 `backend/app.py`의 `_REQUIRED_BUNDLED_BACKEND_SOURCES`에, 새 최상위 파일은 `_build_setup.py`의
  포함 목록에 넣어야 번들에 실립니다.
- 검증: `cd frontend && npm run check`(디자인·구조 검사+빌드)와 관련 `pytest`. 테스트는 임시 `FLOW_DATA_ROOT`/`FLOW_DB_ROOT`/
  `FLOW_WAFER_MAP_ROOT`와 `FLOW_PROD=0`으로 돌리고 운영 데이터에 기록을 남기지 않습니다. 로컬 데이터·드라이버 버전에 기대는
  테스트 일부는 환경에 따라 실패할 수 있으므로, 변경 전후 결과를 비교해 새로 생긴 실패가 없는지 봅니다.
- 배포: `python _build_setup.py` → `frontend/dist`와 `setup.py` 재생성. `git add -A` 금지(작업 트리에 런타임 데이터가 있음).

## 주의 사항

- 공개 저장소입니다. 운영 DB·사용자 정보·사내 문서·발표자료·실제 사번/IP·키를 커밋하지 않습니다.
- 운영 서버는 API 프로세스 1개로 둡니다. 여러 프로세스는 RAM 캐시와 메모리 예산을 복제합니다.
- 동시 처리 수(`FLOW_HEAVY_REQUEST_CONCURRENCY` 등)를 늘리기 전에 실제 조회의 순간 메모리를 측정합니다.

## License

Private. 사내/개인 검증 목적으로만 사용합니다.
