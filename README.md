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
| [guides/](guides/README.md) | 탭별 사용법 영상과 안내 |
| [DB_AI_AND_INPUT_GUIDE.md](DB_AI_AND_INPUT_GUIDE.md) | DB/AI 참고 파일과 입력 데이터 형식(합성 예시) |
| [SECOND_BRAIN.md](SECOND_BRAIN.md) | 외부 second-brain 지식 패키지와 Flow 소비 계약 |
| [VERSION.json](VERSION.json) | 버전과 릴리스 노트(정본) |

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
| ET 다운로드 동시 계산 / 대기열 | 1 / 16 | 2 / 48 |
| SplitTable 자동 제품 캐싱 | 꺼짐 | 켜짐 |

위 표는 **설정값이지 측정 결과가 아닙니다.** 메모리 소프트 기준은 OS 강제 상한이 아니며, 옛 서버에서
저장한 관리자 ⚙ 캐시 설정(`pool_fraction` 등)이 있으면 그 값이 우선합니다.

### 무거운 작업은 어떻게 도는가

예전 개발 worker가 맡던 작업을 포함해 모든 무거운 작업이 운영 서버의 `core/heavy_jobs.py`를 거칩니다.

- **캐시 빌드·스캔은 서버 전체에서 한 번에 1건**(공용 스캔 슬롯): lookup·pivot·FAB 인덱스·WIP latest-lot,
  FAB 매칭 검사, ET 추적 스캔, Auto report 생성·ET history 갱신.
- 자동(예약) 작업은 **사용자 요청이 조용해질 때까지 기다린 뒤** 시작합니다. 조회의 전제가 되는 캐시는 짧은
  유예(기본 5초, `FLOW_REQUIRED_CACHE_IDLE_WAIT_SEC`) 뒤 진행합니다.
- 시작 전 **메모리 확인**: 프로세스 한도 초과나 호스트 여유 메모리 부족이면 최대 2분 기다리고, 그래도 부족하면
  실행하지 않고 다음 기회로 미룹니다.
- 대화형 작업(홈 에이전트, 파일탐색기 SQL, 차트 원본 조회)은 이 줄에 서지 않습니다.
- 관리자 → 시스템 → 모니터의 **무거운 작업** 패널과 캐시관리 화면에서 실행 중인 작업과 미뤄진 횟수를 봅니다.

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

## 설치 (Windows, Miniforge)

요구 사항: Python 3.10 이상(Miniforge conda env), Node.js LTS(npm), `D:\DB`·`D:\flow-data` 읽기·쓰기 권한.

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
   추출 → Python 의존성 → 프런트 빌드를 한 번에 합니다. 같은 폴더의 현장 `requirements.txt`가 있으면 그것을
   씁니다(없을 때만 Flow 최소 의존성). 사내 패키지(`botocore`, `boto`, `awscli`, `bigdataquery`)는 버전
   표기를 빼 두면 충돌이 적습니다. 암호화 연락처와 WebSocket 로그인에 `cryptography`, `websockets`가 필요합니다.
   코드 에이전트(opencode)가 고칠 설치 폴더라면 `set FLOW_EXTRACT_ALL=1` 후 실행해 `AGENTS.md`·`docs/`·`tests/`까지 풉니다.
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
로그는 `D:\flow-data\logs\uvicorn.log`(20MB×5 순환), 재시작 이력 `flow_restarts.log`, 상태 `flow_supervisor.json`입니다.
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
   (STRICT가 없으면 일부 파일 쓰기 실패도 exit 0으로 끝나 반쯤 갱신된 상태를 놓칩니다)
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
설정하면 ID/PW 로그인과 비밀번호 찾기(`/api/auth/forgot-password`, `/api/auth/reset-request`)가 꺼집니다.
비상시 `FLOW_PASSWORD_LOGIN_ENABLED=1`.

관리자 → **사내 로그인·관리자**에서 계정 ID, 이름, 메일, 역할과 위임 페이지를 등록합니다(관리자는 `admin`,
페이지 위임자는 `user`+페이지 ID). 여기 명시한 관리자·위임자만 권한 계정에 추가되고 Flow 비밀번호는 생기지
않습니다. 일반 사내 로그인 사용자는 부서 규칙을 따릅니다. 연락처는 `auth/people.enc`에 암호화 저장되며
(키 `FLOW_DATA_KEY` 또는 `<설치 폴더>\.flow_data.key` — 커밋 금지), 메일 수신 주소에도 쓰입니다. 행을 비워도
계정은 지워지지 않습니다 — 위임 해제·삭제는 업무 권한·위임과 사용자 관리에서 합니다.

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
- 목표는 준비된 데이터 조회부터 표 첫 표시까지 p95 500ms입니다(원본만 있고 캐시가 없는 최초 생성은 제외).

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
관리자 모니터에서 **예약 설정 자체를 끕니다**(`FLOW_SYSMON_ENABLE_LOAD=0`은 유휴 부하만 끄고 이 예약은 끄지 않음).
정기 부하가 공급자의 자원 회수 방지를 보장하지는 않습니다.

## 서버 이사 체크리스트

| 구분 | 위험 | 확인·대응 |
|---|---|---|
| 경로 | 옛 `admin_settings.json`·⚙ 캐시 설정의 절대경로·`pool_fraction`이 새 서버를 옛 경로/작은 예산으로 묶음 | 이전 스크립트가 `data_roots`를 지움. 관리자 → 데이터 루트, 캐시관리 ⚙에서 확인 |
| 경로 | 설정 파일에 남은 `/config/work/...`·`\\옛서버\...` 경로(Valve `local_root`, 백업, 메일/LLM) | `D:\flow-data`에서 검색해 `{db_root}` 토큰이나 D: 경로로 교체 |
| 옛 설정 | `FLOW_SERVER_ROLE`, `FLOW_WORKER_OFFLOAD`, `FLOW_API_SERVER_URL` 등 개발 worker 설정 | 효과 없음. 운영 점검 스캔이 알려 주면 `flow_env*.bat`에서 삭제 |
| 옛 고정값 | `FLOW_CPU_BUDGET_CORES`, `FLOW_PROCESS_MEMORY_LIMIT_GB`, `POLARS_MAX_THREADS`, `FLOW_DUCKDB_THREADS` 등 | 자동 인식이 맞으면 지우고 재시작(Polars 풀은 시작 때 고정). 캐시관리 → 검색 코어는 **자동** 저장 |
| 디스크 | `D:`가 동적 확장 가상디스크·네트워크 드라이브면 느림 | 로컬 고정 디스크(SSD) 권장. 여유: DB + 캐시(DB의 20~50%) + 백업 5개 |
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
