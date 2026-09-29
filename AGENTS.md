# Flow contributor instructions

## 프로젝트와 문서 읽는 순서

- Flow는 반도체 개발 데이터를 lot/wafer 중심으로 연결하는 FastAPI + React 웹 앱이다. 파일탐색기, SplitTable, ET/LOT 추적, TEG/WF MAP, 업무 게시판, 차트·리포트와 홈 에이전트를 제공한다.
- 먼저 이 파일을 읽고 `README.md`의 **서버 구성과 처리 용량**, **설치 (Windows, Miniforge)**, **켜기·상태·끄기**, **업데이트와 데이터 보존**, **서버 이사 체크리스트**를 읽는다. 로그인 변경은 아래 **사내 로그인·관리자·WebSocket 수정** 절에서 시작한다. 기능별 상세는 README의 해당 절과 실제 구현·테스트를 대조한다.
- README는 설치·운영 절차, `VERSION.json`은 릴리스 이력의 정본이다. 문서와 코드가 다르면 실제 코드를 확인하고 문서를 함께 고친다. 목표 성능을 실측 결과로 표현하지 않는다.
- 로그·저장소·보관·백업 작업은 아래 **로그·저장소 작업 진입점**에서 시작한다. 종류와 경로는 `docs/LOG_STORAGE.md`, 진단·정리·S3 보관·복원 절차는 `docs/LOG_STORAGE_OPERATIONS.md`가 안내한다.
- 루트 `app.py`는 import shim, 실제 앱은 `backend/app.py`, HTTP 경로는 `backend/routers/`, 계산은 `backend/core/`다. SplitTable·FileBrowser 라우터 일부는 `backend/app_v2/modules/*/router_parts/`에서 조립되므로 해당 part를 수정한다.
- 탭 등록은 `frontend/src/app/pageManifest.jsx`, 구현은 `frontend/src/features/`다. `frontend/src/pages/`의 호환 wrapper와 혼동하지 않는다. 사용자용 기능 안내는 `guides/README.md`도 참고한다.
- `doppelganger/`와 과거 볼트 참조 지침은 사용하지 않는다. 현재 사용자 지시, 코드·문서, 직접 검증 결과로 판단한다.

## 지금 어디서 작업하는가 (먼저 판별)

이 파일은 개발 체크아웃과 사내 VM 설치 폴더에 **같은 내용으로** 들어간다. 적용 규칙이 다르므로 먼저 판별한다.

| 판별 | 환경 | 적용 |
|---|---|---|
| `git remote -v`에 GitHub `flow` 원격이 있다 | **개발 체크아웃** | 아래 전부. 배포 = `_build_setup.py` 재빌드, 공개 저장소 규칙 |
| 원격이 없다(`.git` 없음 또는 VM에서 만든 로컬 git), `scripts\windows\flow_env.local.bat`이 있거나 서버가 이 폴더에서 돈다 | **사내 VM 설치 폴더 = 운영 그 자체** | **Windows VM 설치 폴더에서 작업하기** 절이 우선. `_build_setup.py` 실행·원격 push 금지, 테스트는 반드시 격리 명령으로 |

판별이 애매하면 운영 폴더로 간주하고 사용자에게 확인한다.

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
2. 큰 파일은 통째로 읽지 않는다. `rg -n`으로 함수·API 경로를 찾고 그 구간만 읽는다.
3. 필요한 부분만 고친다. 파일 전체를 다시 쓰지 않는다(CRLF/LF 줄바꿈이 파일마다 다르다).
4. 서버 파라미터는 기본값을 두어 기존 호출을 깨지 않는다. 화면과 서버 기본값이 같이 있는 값은 둘 다 고친다.
5. 바꾼 기능의 pytest와 `cd frontend && npm run check`로 확인한다. VM에서는 pytest를 **아래 VM 절의 격리 명령으로만** 돌린다(`tests/conftest.py`는 일부 파일만 격리하고, 환경변수 없이 돌리면 설치본 기본값 `D:\flow-data`·`D:\DB`를 쓴다).
6. 은퇴 코드(`routers/home_agent.py`, `core/home_orchestrator.py`, `features/diagnosis/` 등, CODEMAP 3절 끝)는 배포되지 않으므로 고치지 않는다.
7. 문서는 필요할 때 그 절만 읽는다. 이 파일에 적힌 경로(`docs/…`, README 절)는 자동으로 로드되지 않는다.

## 켜기·상태 확인·끄기 (Windows 운영)

절차·명령 정본은 README **켜기·상태·끄기**, **부팅 자동 시작**, **업데이트와 데이터 보존**이다. 필요할 때 그 절만 읽는다.
코드를 고치는 에이전트가 지킬 것만 여기 둔다.

- 운영은 API 프로세스 1개 + 감시기(`scripts/flow_server.py`, 창은 `scripts\windows\flow_run.bat`)다. `uvicorn --reload`, `--workers N`, 맨 `uvicorn` 기동으로 바꾸지 않는다. 여러 프로세스는 RAM 캐시·메모리 예산을 복제한다.
- 제어는 `scripts\windows\flow_ctl.bat status|restart|stop|log|health`(= `python scripts/flow_server.py --status|--restart|--stop`). `restart`/`stop`은 요청 파일만 남기고 바로 반환하므로 `status`와 `/health`(기본 `http://127.0.0.1:8080/health`)로 완료를 확인한다.
- `restart`는 API 자식만 다시 띄운다. **환경변수·`flow_env.local.bat` 변경은 감시기까지 `stop` → 종료 확인 → `flow_run.bat`(또는 예약 작업 `FlowWebApp`) 재기동**이어야 반영된다.
- `taskkill /IM python.exe` 금지(감시기·ET 계산 자식·다른 Python 작업까지 죽는다). 한 설치 폴더에 인스턴스를 두 개 띄우지 않는다(`.flow_stop`이 폴더 단위).
- 로그: `FLOW_DATA_ROOT/logs/uvicorn.log`, `flow_restarts.log`, `flow_supervisor.json`.
- 업데이트 순서는 **정상 종료 확인 → `FLOW_SETUP_STRICT=1`로 추출 → 종료 코드·`extract_report.json` 확인 → 재기동**. 예약 작업 운영이면 먼저 `Disable-ScheduledTask -TaskName FlowWebApp`. DB/data·계정·키·`flow_env.local.bat`은 보존된다.

## Windows VM 설치 폴더에서 작업하기 (Miniforge · opencode)

운영 VM은 컨테이너 이미지가 아니라 **바탕화면 `flow` 폴더에 setup.py를 풀어 그 폴더에서 바로 서버를 돌린다.** 같은 폴더를 opencode 같은 코드 에이전트가 고친다. 이 폴더는 운영 그 자체이므로 아래를 지킨다.

- **위치 규약(코드 기본값, 2026-09-29):** Windows에서 `.git`이 없는 설치 폴더는 환경변수가 없어도 `D:\DB`(원천, 읽기 전용)·`D:\flow-data`(사용자 기록·설정·로그)를 쓴다. 드라이브는 `FLOW_STORAGE_ROOT`, 끄려면 `FLOW_STORAGE_DEFAULT=0`. Windows에서는 Linux `/config/work/...` 경로를 절대 쓰지 않는다(현재 드라이브의 `\config\...`로 풀려 엉뚱한 곳에 쓰던 문제). 자동 백업 기본 위치는 `D:\flow-backups`. 구현: `backend/core/root_profile.py`, `backend/core/backup.py`, 테스트 `tests/test_windows_storage_defaults.py`.
- **파이썬:** Miniforge conda env(기본 이름 `flow`, `FLOW_CONDA_ENV`로 변경). 명령은 `Miniforge Prompt`에서 `conda activate flow` 후 실행한다. 예약 작업은 등록할 때 활성 env의 `python.exe` 전체 경로를 고정한다.
- **켜기/끄기:** `scripts\windows\flow_run.bat`(감시기+바깥 재기동 루프, 창을 닫으면 꺼짐) · `scripts\windows\flow_ctl.bat status|stop|restart|log|health`. 현장 전용 값(LLM 키·로그인·포트)은 `scripts\windows\flow_env.local.bat`에 둔다 — setup.py 업데이트가 `flow_env.bat`은 덮어쓰지만 `.local`은 건드리지 않는다.
- **opencode가 코드를 고친 뒤:** 백엔드 변경은 `flow_ctl.bat restart`(서버 자식만 재기동, 감시기 유지)로 반영한다. `frontend/src` 변경은 `cd frontend && npm run check`(디자인·구조 검사 + `frontend/dist` 재빌드) 뒤 브라우저 새로고침(서버 재시작 불필요). 반영 전 해당 pytest를 아래 격리 명령으로 돌린다.
- **테스트 격리 명령(VM 필수):** 설치 폴더에서는 환경변수가 없으면 앱·테스트가 운영 `D:\flow-data`·`D:\DB`를 기본으로 잡는다. pytest는 항상 이렇게 돌린다(Miniforge Prompt=cmd 기준, `<관련>`만 바꾼다):
  ```bat
  set FLOW_PROD=0& set FLOW_STORAGE_DEFAULT=0& set FLOW_DATA_ROOT=%TEMP%\flow-test\data& set FLOW_DB_ROOT=%TEMP%\flow-test\db& set FLOW_WAFER_MAP_ROOT=%TEMP%\flow-test\wafer& set FLOW_DATA_KEY_FILE=%TEMP%\flow-test\data.key
  python -m pytest -q tests\test_<관련>.py
  ```
  PowerShell이면 같은 값을 `$env:FLOW_PROD='0'; $env:FLOW_DATA_ROOT="$env:TEMP\flow-test\data"` 식으로 넣는다. 같은 창에서 서버를 띄우지 않는다. 관련 테스트는 `findstr /m "core.<모듈>" tests\*.py`로 찾는다.
- **프런트 빌드 준비물:** `npm run design:check`·`structure:check`는 Node만 있으면 된다. `npm run build`는 `frontend\node_modules`가 필요하다 — 설치 때 dist가 소스와 같으면 npm을 건너뛰므로 없을 수 있다. `frontend\node_modules\.bin\vite.cmd`가 없으면 `npm install`(사내 npm 미러 필요, 번들에 lockfile이 없어 패키지 버전이 개발 PC와 다를 수 있음)을 사용자에게 먼저 확인한다. 설치할 수 없으면 `frontend/src`를 고쳐도 반영되지 않는다고 알리고 멈춘다. 기존 `frontend/dist`를 손으로 고치지 않는다.
- **opencode에 필요한 파일:** 기본 `python setup.py extract`는 `AGENTS.md`·`docs/`·`tests/`를 풀지 않고 **이미 있는 옛 사본도 갱신하지 않는다**. 코드를 고칠 VM에서는 설치·업데이트 때마다 `set FLOW_EXTRACT_ALL=1` 후 `python setup.py`(또는 `python setup.py extract --all`)로 전부 받는다. 그러지 않으면 이 파일·CODEMAP·테스트가 코드보다 옛 버전으로 남는다. 구조는 `docs/CODEMAP.md`, 운영 절차는 README의 **설치 (Windows, Miniforge)**·**서버 이사 체크리스트** 절.
- **OpenCode · oh-my-opencode 사용 규칙:** 작업 폴더(프로젝트 루트)는 setup.py를 푼 설치 폴더로 연다 — OpenCode는 시작 폴더에서 위로 올라가며 `AGENTS.md`를 찾아 매 턴 넣는다(같은 폴더에 `AGENTS.md`가 있으면 `CLAUDE.md`는 쓰지 않는다). `/init`·`/init-deep`으로 이 파일을 다시 만들거나 하위 폴더 `AGENTS.md`를 생성하지 않는다 — 이 파일은 번들이 덮어쓰는 정본이고, 생성된 하위 파일은 업데이트 뒤 낡은 규칙으로 남는다. 현장 전용 지시가 필요하면 OpenCode 전역 설정(`~/.config/opencode/AGENTS.md`)이나 `opencode.json`의 `instructions`로 설치 폴더 밖 파일을 가리킨다. 외부 웹 검색·GitHub 조회용 하위 에이전트는 사내망에서 실패할 수 있으니 저장소 안 문서·코드로 판단한다. 사내 코드·로그·설정 내용을 외부 LLM·웹으로 보내지 않는다.
- **로컬 수정 보존:** 새 setup.py로 업데이트하면 같은 경로의 소스가 덮어써진다. VM에서 고친 내용은 설치 폴더에서 `git init` 후 커밋해 두고(원격 push 금지 — 사내 코드·설정), 업데이트 뒤 `git diff`로 다시 적용할 부분을 확인한다. `git add -A` 대신 고친 파일을 지정해 add 한다(`--all` 추출 때 받은 `.gitignore`가 없으면 `data/`·`node_modules`·캐시가 딸려 들어간다). `git init`을 해도 `D:\flow-data`가 있거나 `flow_env.bat`이 경로를 넣으므로 운영 저장 위치는 바뀌지 않는다. `D:\DB`·`D:\flow-data`·`flow_env.local.bat`은 추출이 건드리지 않는다.
- **VM에서 하지 않는 것(개발 체크아웃 전용):** `python _build_setup.py` 재빌드, `setup.py` 교체, 원격 push. 다른 설치본에 전달할 수정은 사용자가 개발 체크아웃으로 옮긴다.
- **하면 안 되는 것:** 운영 중 `uvicorn --reload`·여러 워커 기동, `D:\DB` 원천 파일 수정·삭제, `taskkill /IM python.exe`(감시기와 ET 계산 자식까지 죽는다), 설치 폴더 안에 두 번째 인스턴스 기동(`.flow_stop`이 폴더 단위라 서로 끈다).

## 로그·저장소 작업 진입점 (OpenCode · oh-my-opencode)

Gemma4 등으로 작업할 때는 이 절 → 해당 문서의 필요한 절 → 표에 적힌 실제 함수 순서로 읽는다.
문서 전체와 모든 로그 본문을 한꺼번에 컨텍스트에 넣지 않는다. 현장 경로는 기본 예시와 다를 수 있다.

| 요청 | 먼저 읽을 곳 | 코드 시작점 |
|---|---|---|
| 어떤 기록이 어디에 쌓이는지, 가장 큰 폴더 찾기 | `docs/LOG_STORAGE.md`의 경로·목록, `docs/LOG_STORAGE_OPERATIONS.md`의 용량 진단 | `scripts/windows/measure_flow_storage.ps1`, `backend/core/paths.py`, `roots.py` |
| 오류·재시작·검색 지연 확인 | 로그 목록의 감시기·자원·검색 행, 작업 절차의 진단 | `scripts/flow_server.py`, `core/sysmon.py`, `search_timing_log.py`, `cache_event_log.py` |
| 로그 순환·오래된 기록 보관·S3 전송 | 작업 절차의 순환·원격 보관·복원 | `core/audit.py`, `utils.py`, `activity_index.py`, `s3_sync.py`, `routers/s3_ingest.py` |
| 자동 백업 범위·누락·복구 변경 | 작업 절차의 백업 범위 | `core/backup.py`, SplitTable `25_s0_snapshot.part.py`, `core/chat_conversations.py` |
| 대화·PPT·첨부파일·휴지통 정리 | 로그 목록의 업무 기록, 작업 절차의 업무 파일 | `core/chat_conversations.py`, `auto_report.py`, FileBrowser `20_validation_and_versioning.part.py`·`80_settings_edit_and_version_routes.part.py` |

- `activity.jsonl`·`downloads.jsonl`는 전체 보존이 현재 계약이다. 일반 JSONL의 건수 제한을 일괄 적용하지 않는다.
- S0 일별/revision Parquet, plan 변경 이력, 대화 SQLite, 첨부파일은 업무 기록이다. 캐시/진단 로그와 구별하고 과거 조회·복원을 유지한다.
- 자동 ZIP은 기본 **48시간·최근 3개(최대 5개)**, `data_root` 대상이다. DB 원천·cache/tmp·모든 Parquet는 제외된다. S0/Parquet 이력 누락과 활성 SQLite snapshot은 별도 검토 대상이다.
- S3 동기화에는 기간별 보관·로컬 정리·과거 화면 복원 기능이 없다. 원격 보관 요청 범위, 파일 무결성, 기존 링크/조회 영향까지 확인한다. 기존 로컬 자료를 자동으로 원격 전송하지 않는다.
- 진단은 내용 대신 크기·건수·증가량부터 확인한다. 토큰·인증 설정·사용자 질문·실제 SQL/파일 경로가 든 로그를 공개 저장소나 외부 LLM 입력에 넣지 않는다.
- 한 번에 한 종류의 기록을 변경하고, 관련 테스트와 합성 임시 root로 검증한다. 자세한 검증 매핑과 작업 입력 예시는 `docs/LOG_STORAGE_OPERATIONS.md`에 있다.

## 사내 로그인·관리자·WebSocket 수정 (VM opencode 우선 안내)

이 작업은 먼저 README의 **관리자 ID·이름·허용 부서 빠른 수정 (VM/OpenCode)**, **WebSocket 수신 형식·방식 수정**,
**VM 수정 반영·검증**을 읽는다. 환경변수 목록·합성 메시지·규칙 예시는 README가 정본이며, 이 절은 수정 파일과 지켜야 할 동작을 정한다.

| 요청 | 수정/확인 시작점 |
|---|---|
| 관리자 추가·표시 이름/메일·페이지 대리인 | 화면 `frontend/src/features/admin/DepartmentAccessPanel.jsx`; `backend/routers/admin.py`: `ManagerProfileReq`(행), `ManagerProfilesReq`(요청), `manager_profiles_save()` (`/api/admin/manager-profiles`) |
| 기존 계정 역할·이름·메일·위임 해제 | `features/admin/My_Admin.jsx`; `routers/admin.py`: `set_role()`, `set_name()`, `set_email()`, `page_admins_set()` |
| 사내 ID → Flow ID | 현장 `scripts/windows/flow_env.local.bat`: `FLOW_WS_AUTH_USER_MAP`; `backend/core/auth_providers.py`: `_ws_user_map()`, `_identity_for_company_user()` |
| 허용 department·탭·일치 방식 | `DepartmentAccessPanel.jsx`; `backend/routers/auth.py`: `get_department_rules()`, `save_department_rules()`; `auth_providers.py`: `write_department_rules()`, `department_access()` |
| WS에서 받을 ID/token/department/name/email key | 현장 `FLOW_WS_AUTH_*_FIELDS`; `auth_providers.py`: `_WS_DEFAULT_*_FIELDS`, `_ws_pick()` (점 경로, 후보 순서). ID 목록은 브라우저/검증 응답에 공통 |
| 브라우저 연결·송신·수신 프레임 처리 | `frontend/src/features/auth/My_Login.jsx`: `wsLogin()`; `WebsocketAuthProvider.describe()`에서 `/api/auth/providers` 설정 제공 |
| 메시지 파싱·서버 재검증 HTTP/WS 방식 | `backend/routers/auth.py`: `WsLoginReq`, `websocket_login()` (`/api/auth/sso/ws/login`); `auth_providers.py`: `_ws_parse()`, `authenticate()`, `_ws_verify()` |
| 로컬 비밀번호 초기 관리자 ID 기본값 | `backend/app_v2/runtime/startup.py`: `ensure_seed_admin()` (`hol` 고정, `FLOW_ADMIN_PW`는 최초 비밀번호). 사내 관리자 지정과 구별 |

수정 원칙:
1. 실제 ID·이름·부서·URL·키 요청이면 먼저 현장 설정/기존 관리자 API로 처리할 수 있는지 확인한다. 공개 소스에
   실제 값을 하드코딩하지 않는다. 신규 환경변수/일반 key 기본값은 코드에도 둔다. 전체 인증 모듈을 재작성하지 않는다.
2. `username`은 Flow 기록의 식별자이고 `name`은 표시 이름이다. manager-profiles의 ID 수정은 새 ID 추가/갱신이며
   기존 ID를 일괄 rename하지 않는다. 사내 ID가 바뀐 경우 기존 Flow ID로 매핑하면 기존 기록을 유지할 수 있다.
   계정 ID 이관은 작성자·그룹·위임·세션을 확인하는 별도 작업으로 다룬다.
3. `_identity_for_company_user()`의 현재 순서: 매핑 → 기존 계정의 역할·탭 → 매핑 대상이 없으면 admin →
   그 외 일반 사용자는 부서 규칙. `FLOW_WS_AUTH_USER_MAP`은 일반 사용자 목록이 아니라 관리자 지정에도 쓰인다.
   기존 `users.csv` 계정은 부서 규칙을 우회한다. 정책 변경 요청 없이는 이 우선순위를 바꾸지 않는다.
4. 부서 규칙은 비어 있으면 일반 로그인 허용, 하나 이상이면 일치하는 허용 부서만 통과, 거부 우선, 허용 탭 합집합이다.
   빈 tabs는 관리자 외 허용 탭 전체다. 파일 누락/파싱 실패도 빈 규칙 처리라는 현재 동작을 고려한다.
   OIDC의 부서 권한 그룹과 혼동하지 않는다. IP 로그인은 같은 identity 함수를 쓰지만 부서 claim이 없다.
5. 프로필 저장은 `users.csv`의 역할/상태, 암호화 `auth/people.enc`의 연락처, `admin_settings.json.page_admins`의
   위임을 함께 조정하고 권한 변경 시 해당 사용자 세션을 회수한다. 빠진 프로필 행은 삭제가 아니다.
   매핑을 남긴 채 대상 계정을 삭제하면 다시 관리자 세션을 얻을 수 있다. 수동 연락처는 재로그인에도 보존한다.
6. 브라우저 수신은 원문을 `{message: event.data}`로 POST하고 400이면 다음 프레임을 기다린다. 서버 WS 재검증은
   첫 프레임 하나만 읽는다. 여러 프레임·binary·배열·추가 handshake는 해당 수신 코드를 바꿔야 한다.
   부서·이름·메일은 재검증 응답에서 읽고, 요청 ID와 검증 ID가 함께 있으면 일치 검사한다.
   `TRUST_CLIENT=1`/`VERIFY=none`으로 인증 실패를 우회하지 않는다. 사내 토큰을 Flow claims·로그에 저장하지 않는다.
7. 설정 파일 변경이면 감시기까지 `flow_ctl.bat stop` → 종료 확인 → runner/예약 작업으로 다시 기동한다.
   `flow_ctl.bat restart`는 자식 API만 재기동하므로 **실행 중인 감시기의 환경변수는 갱신되지 않는다**.
   Python 소스만 바꾸면 자식 restart, 프런트 변경은 `npm run check`로 dist를 다시 만든다.
8. 관련 검증: `tests/test_websocket_auth.py`, `tests/test_manager_profiles.py`, `tests/test_group_departments_ip_login.py`;
   seed 변경은 `test_security_and_background_owner.py`의 seed 검사도 실행한다. 운영 데이터/암호화 키와 분리한
   임시 root·`FLOW_DATA_KEY_FILE`·`FLOW_PROD=0`을 쓴다. WS 테스트는 재검증 mock을 쓰므로 실제 HTTP/WS 연결·
   변경한 payload/프레임 처리를 별도로 확인한다. 새 관리자/허용·거부·부서 없음과 클라이언트 부서 위조를 검증한다.
9. 부서 규칙/매핑은 기존 세션을 일괄 회수하지 않는다. 변경 후 새 로그인으로 `/api/auth/me`의 ID·역할·탭을 확인한다.
   일반 변경을 전달할 때는 README/이 지침과 함께 `_build_setup.py`를 재빌드한다. VM 전용 설정은 로컬에 보존한다.

## Model delegation and token budget

- The user authorizes lower-model subagents for simple, bounded Flow work. Keep the main agent responsible for design decisions, S0/history invariants, cache correctness, permissions, concurrency, and final review.
- Delegate independent file inventories, reference checks, small UI/copy edits, and focused verification to `gpt-5.6-luna` at low or medium reasoning when that model is available. Use `gpt-5.6-sol` for a bounded implementation that needs more reasoning. If unavailable, choose an available inexpensive coding model; do not silently change the main task model.
- The model names above are for Codex on the development checkout. In OpenCode/oh-my-opencode on the internal VM, delegate only to the agents and models already configured there (for example its explore agent for file searches); do not edit OpenCode config files to switch models unless the user asks.
- Give each subagent only its goal, relevant paths, constraints, and acceptance checks. The main agent inspects delegated diffs and runs the relevant checks before completion.
- Read only relevant file sections. Avoid full-repository dumps, repeated unchanged polling, and tests unrelated to the changed behavior.

## Deployment contract

- This section applies to the development checkout. On the internal VM install folder, follow **Windows VM 설치 폴더에서 작업하기** instead (no rebuild, no push).
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
