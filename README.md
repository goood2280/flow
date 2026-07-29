# Flow

Flow는 반도체 개발·파일 공유·SplitTable 관리·WF MAP 검사·데이터 분석을 lot/wafer 중심으로 연결하는 FastAPI + React 웹 애플리케이션입니다.

이 GitHub 저장소는 사내 반입을 단순화하기 위해 두 파일만 배포합니다.

- `setup.py`: 백엔드, 프런트엔드, 문서와 운영 스크립트를 압축 포함한 단일 설치 파일
- `README.md`: 설치 및 운영 안내

DB, 계정, 설정, 로그, 캐시 등 운영 데이터는 `setup.py`에 포함되지 않으며 업데이트 시에도 덮어쓰지 않습니다.

## 주요 기능

- 파일 공유, 대용량 parquet/CSV 탐색 및 미리보기
- 제품별 SplitTable 검색, plan/actual 비교와 편집
- root lot/wafer 기준 FAB 데이터 결합
- WF MAP, TEG MAP, 공정 데이터 검사
- Inform, Tracker, Meeting, Dashboard 및 Flow-i 지원
- 운영 API 서버와 개발 worker 서버를 이용한 무거운 작업 분산
- 캐시 수동 스캔의 현재 단계, 실행 중 작업, 향후 큐와 피크 메모리 관측

## 권장 서버 구성

기준 구성은 다음과 같습니다.

| 역할 | 권장 자원 | 책임 |
|---|---:|---|
| 운영 API | 5코어 / 28GB RAM | 사용자 요청, SplitTable 조회, API-local RAM 캐시 |
| 개발 worker | 5코어 / 10~15GB RAM | lookup/pivot/FAB index 등 무거운 공유 캐시 생성 |

두 서버는 동일한 `FLOW_DB_ROOT`와 `FLOW_DATA_ROOT`를 봐야 합니다. 개발 worker가 살아 있으면 무거운 작업을 worker로 위임하고, 꺼져 있으면 운영 서버가 사용자 요청이 없는 시간에 한 작업씩 천천히 수행합니다.

## 빠른 설치

### 1. 요구 사항

- Python 3.10 이상
- Node.js와 npm
- DB/data 공유 경로에 대한 읽기·쓰기 권한

### 2. 설치

빈 폴더에 `setup.py`를 복사한 뒤 실행합니다.

```bash
python setup.py
```

이 명령은 다음을 순서대로 수행합니다.

1. 포함된 Flow 소스를 현재 폴더에 추출
2. 백엔드 Python 의존성 설치
3. 프런트엔드 npm 의존성 설치 및 production build

소스만 먼저 풀려면 다음 명령을 사용합니다.

```bash
python setup.py extract
```

설치 후 서버를 실행합니다.

```bash
uvicorn app:app --host 0.0.0.0 --port 8080
```

접속 주소는 `http://<서버주소>:8080`입니다.

초기 관리자 계정은 `hol / hol12345!`이며, 사내 반입 전에 반드시 비밀번호를 변경하거나 `FLOW_ADMIN_PW`를 지정하십시오.

## 운영 API 서버 설정

PowerShell 예시:

```powershell
$env:FLOW_SERVER_ROLE="api"
$env:FLOW_DB_ROOT="\\shared-server\flow\DB"
$env:FLOW_DATA_ROOT="\\shared-server\flow\flow-data"
$env:FLOW_CACHE_TOTAL_BUDGET_FRACTION="0.45"
$env:FLOW_PROCESS_MEMORY_LIMIT_FRACTION="0.80"
uvicorn app:app --host 0.0.0.0 --port 8080
```

Linux 예시:

```bash
export FLOW_SERVER_ROLE=api
export FLOW_DB_ROOT=/config/work/sharedworkspace/DB
export FLOW_DATA_ROOT=/config/work/sharedworkspace/flow-data
export FLOW_CACHE_TOTAL_BUDGET_FRACTION=0.45
export FLOW_PROCESS_MEMORY_LIMIT_FRACTION=0.80
uvicorn app:app --host 0.0.0.0 --port 8080
```

## 개발 worker 서버 설정

운영 서버와 동일한 DB/data 공유 경로를 지정합니다.

```powershell
$env:FLOW_SERVER_ROLE="worker"
$env:FLOW_DB_ROOT="\\shared-server\flow\DB"
$env:FLOW_DATA_ROOT="\\shared-server\flow\flow-data"
$env:FLOW_WORKER_CONCURRENCY="1"
$env:FLOW_POLARS_MAX_THREADS="2"
uvicorn app:app --host 0.0.0.0 --port 8080
```

포트는 운영서버와 다른 머신이므로 8080을 그대로 씁니다. `scripts/worker_watchdog.py`로 원격 기동도 함께 쓴다면 `--port`를 같은 값으로 맞춥니다(워치독 기본값은 8081).

개발 worker 기본 정책:

- 무거운 작업 동시 실행 1개
- Polars 최대 2 threads
- API용 product/root/view RAM 캐시 비활성화
- backup, mail, S3 등 운영 scheduler 비활성화
- worker available memory와 process memory admission 통과 후 작업 실행

## 자동 재시작 (프로세스 상시 기동)

프로세스가 죽었을 때 되살리는 것은 앱이 아니라 **바깥의 관리자**가 합니다. 죽은 프로세스는 스스로를 되살릴 수 없기 때문입니다.

| 서버 | 방법 |
|---|---|
| 운영서버 | Docker 재시작 정책 (`--restart unless-stopped`) — 이미 적용됨 |
| 개발서버 | systemd 유닛 (`scripts/flow.service`) |

수동 `uvicorn` 실행은 OOM이나 예기치 못한 종료 뒤 아무도 되살리지 않습니다. 개발서버는 무거운 작업을 넘겨받는 쪽이라 죽을 여지가 가장 크고, 죽어 있으면 운영서버의 위임이 조용히 로컬 폴백으로 떨어져 느려집니다.

### 프로토콜 A — 자동 재시작 켜기 (개발서버, 최초 1회)

모두 **개발서버 셸에서** 실행합니다. 각 단계의 "확인" 이 통과해야 다음으로 넘어갑니다.

**A-1. 사전 확인** — 유닛에 적을 값을 먼저 알아냅니다.

```bash
whoami && pwd && which python3 && python3 -c "import fastapi, uvicorn; print('deps ok')"
```

확인: flow를 실행할 계정, `app.py`가 있는 backend 경로, python3 절대경로가 나오고 `deps ok`가 찍힐 것. `deps ok`가 안 나오면 먼저 `python setup.py install-deps`를 합니다.

**A-2. 유닛 파일 값 수정** — 템플릿을 그대로 쓰면 반드시 실패합니다(`/opt/flow/backend`, 사용자 `flow`는 예시값).

```bash
sudo nano /etc/systemd/system/flow.service
```

`scripts/flow.service` 내용을 붙여넣고 아래를 A-1에서 확인한 값으로 고칩니다.

| 항목 | 설명 |
|---|---|
| `User` / `Group` | flow를 실행할 계정 |
| `WorkingDirectory` | `app.py`가 있는 backend 디렉터리 |
| `ExecStart` | python3 절대경로 + 포트(개발서버 8080) |
| `FLOW_DATA_ROOT` / `FLOW_DB_ROOT` | **운영서버와 같은 공유 경로** (다르면 위임이 동작하지 않음) |
| `FLOW_ADMIN_PW` | 실제 비밀번호 (미설정 시 공개된 기본값이 시드됨) |

**A-3. 등록·기동**

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now flow
```

`daemon-reload`는 유닛 파일 변경을 systemd에 읽히고, `enable`은 부팅 시 자동 시작 등록, `--now`는 지금 바로 기동입니다.

**A-4. 검증** — 셋 다 통과해야 완료입니다.

```bash
systemctl status flow
```

확인: `Active: active (running)`, 그리고 `Loaded:` 줄에 `enabled`.

```bash
curl -s localhost:8080/health
```

확인: `{"status":"ok",...}`.

```bash
sudo systemctl kill -s SIGKILL flow && sleep 8 && systemctl status flow --no-pager | head -3
```

확인: 강제로 죽였는데도 다시 `active (running)`이면 자동 재시작이 실제로 동작하는 것입니다. **이 테스트를 꼭 한 번 해보세요** — 유닛이 등록만 되고 재시작이 안 되는 경우를 여기서 걸러냅니다.

**A-5. 실패했다면**

```bash
journalctl -u flow -n 50 --no-pager
```

대부분 A-2의 경로·계정·python 경로 오타입니다. 로그에 이유가 그대로 찍힙니다. 고친 뒤에는 항상 `sudo systemctl daemon-reload && sudo systemctl restart flow`.

### 프로토콜 B — 업데이트 (setup.py 재배포)

자동 재시작이 켜진 뒤에는 **프로세스를 그냥 죽이면 안 됩니다.** `Restart=always`는 예기치 못한 종료에만 반응하므로 `systemctl stop`은 되살아나지 않지만, `kill`/`pkill`로 죽이면 즉시 재기동돼 구버전과 신버전 파일이 섞인 채로 앱이 뜹니다.

**B-1. 진행 중 작업 확인** — 정지는 30초 후 강제 종료라 무거운 작업이 끊깁니다. 캐시 관리 화면에서 스캔·빌드가 도는지 봅니다.

**B-2. 백업**

```bash
python3 scripts/preflight_internal.py --write-probe --backup-now
```

**B-3. 정지** — 여기서 `kill`을 쓰지 않습니다.

```bash
sudo systemctl stop flow
```

확인: `systemctl status flow`가 `inactive (dead)`. 이 상태는 유지됩니다(되살아나지 않음).

**B-4. 교체**

```bash
FLOW_SETUP_STRICT=1 python3 setup.py extract && python3 setup.py install-deps
```

`FLOW_SETUP_STRICT=1`이 중요합니다. 기본값은 일부 파일 쓰기가 실패해도 경고만 내고 성공(exit 0)으로 끝나서, 일부만 갱신된 상태를 정상으로 오인합니다.

확인: 명령이 exit 0으로 끝날 것. 실패하면 B-6.

**B-5. 기동**

```bash
sudo systemctl start flow
```

**B-6. 검증**

```bash
systemctl status flow && curl -s localhost:8080/health && curl -s localhost:8080/version.json
```

확인: `active (running)` + `{"status":"ok"}` + 버전이 새 배포 시각인지.

**B-7. 문제가 생겼다면 롤백**

```bash
sudo systemctl stop flow && python3 setup.py restore latest && sudo systemctl start flow
```

> Docker 운영서버는 이미지 교체 → 컨테이너 재생성이므로 B 프로토콜이 필요 없습니다.

### 프로토콜 C — 일상 조작

| 목적 | 명령 |
|---|---|
| 상태 확인 | `systemctl status flow` |
| 실시간 로그 | `journalctl -u flow -f` |
| 최근 로그 50줄 | `journalctl -u flow -n 50 --no-pager` |
| 수동 재시작 | `sudo systemctl restart flow` |
| 임시 정지 (되살아나지 않음) | `sudo systemctl stop flow` |
| 다시 시작 | `sudo systemctl start flow` |
| 부팅 자동시작 해제 | `sudo systemctl disable flow` |
| 유닛 수정 반영 | `sudo systemctl daemon-reload && sudo systemctl restart flow` |

### 헬스체크

`GET /health`는 **인증 없이** 접근됩니다. 감시 도구가 세션 토큰을 들고 다닐 수 없기 때문이며, 대신 경로·환경변수 같은 내부 정보는 넣지 않습니다.

```bash
curl -s localhost:8080/health
```

```json
{"status":"ok","uptime_sec":195,"started_at":"2026-07-29T00:36:16"}
```

systemd는 프로세스가 *죽는* 것은 감지하지만 *살아서 멎은* 것은 모릅니다. 외부 모니터링이 이 엔드포인트를 주기적으로 확인하고, 응답이 없거나 200이 아니면 재시작 대상으로 판단하면 됩니다. Docker 운영서버에서는 `HEALTHCHECK`에 같은 경로를 쓸 수 있습니다.

> **Windows 개발서버라면** systemd가 없으므로 [NSSM](https://nssm.cc)으로 서비스 등록하거나, 작업 스케줄러에서 "시스템 시작 시 실행" + "작업이 실패하면 다시 시작"을 설정합니다. 이때도 B 프로토콜의 순서(정지 → 교체 → 기동)는 같습니다.

### 배포 원격 진단 — "앱 파일을 서버에서 받지 못했습니다"가 뜰 때 (v9.5.80+)

서버 셸에 접근할 수 없어도(Docker 운영서버) 원인을 특정할 수 있습니다. 부팅 실패 화면이 자동으로 `GET /deploy-info.json`(무인증)을 조회해 **판정을 화면에 띄웁니다**:

| 판정 | 의미 | 조치 |
|---|---|---|
| 서버에는 파일이 있음 | 중간 프록시·캐시가 `/assets/`를 막거나 낡은 응답을 줌 | 프록시 설정/캐시 확인 |
| 서버 dist에 파일이 없음 | 서버에서 extract 부분 실패 (쓰기 실패 목록 병기) | 이미지 캐시 없이 재빌드 |
| 참조 자체가 다름 | 프록시가 낡은 index.html을 캐시 중 | 프록시 캐시 무효화 |
| 구버전 백엔드 | 새 번들이 아직 배포되지 않음 | 재배포 확인 |

`setup.py extract`는 끝날 때 dist 정합을 검사해 `extract_report.json`을 남기고, 누락이 있으면 `[extract] FAIL` 로그를 냅니다 (`FLOW_SETUP_STRICT=1`이면 exit 1). 실패 화면의 캡처 한 장이면 원인 보고가 끝납니다.

## SplitTable 성능 및 메모리 정책

- 서로 다른 root lot 조회도 제품 전체가 아닌 root partition/pivot 파일 하나만 읽습니다.
- 요청 중에는 root의 전체 wide frame을 RAM에 올리지 않고 필요한 prefix/custom 컬럼만 parquet projection으로 읽습니다.
- lookup/pivot/FAB root index는 개발 worker가 우선 생성합니다.
- worker가 없으면 운영 서버의 사용자 요청이 조용할 때 local fallback을 1개씩 실행합니다.
- lookup 초기 생성은 운영 fallback에서 root 4개, 개발 worker에서 root 2개 단위로 처리합니다.
- pivot 생성은 root 1개씩 처리합니다.
- 압축 크기 128MB를 넘는 단일 root partition은 순간 OOM 방지를 위해 RAM 예열을 생략합니다.
- 전체 프로세스 캐시 풀 기본값은 물리 메모리의 45%, process soft limit은 80%입니다.
- 5코어 운영 서버의 root-scoped 조회는 기본 3개가 실행되고 추가 요청은 큐에서 기다립니다.

샘플 데이터 검증 결과:

- 서로 다른 5개 root 동시 조회: 전체 약 312ms
- 서로 다른 root 순차 조회: 첫 초기화 이후 약 56~86ms
- 동일 조건 재조회: 약 8.7ms
- cold partition 5개 동시 조회: 약 433ms, 측정 process peak RSS 증가 약 71.7MB

운영 데이터의 실제 속도는 parquet 폭, root당 wafer/row 수, 공유 스토리지와 네트워크 성능에 따라 달라집니다.

## 캐시 관리 화면

관리자용 데이터 캐시 화면에서 다음을 확인할 수 있습니다.

- 수동 스캔 단계별 `queued / running / done / failed`
- 현재 실행 중인 제품과 앞으로 대기 중인 작업
- root RAM 유휴 예열의 현재 root와 향후 큐
- API RSS, 작업 시작 이후 peak 증가량, 최소 host available memory
- worker 현재/대기 task와 worker lifetime peak RSS

## 업데이트와 데이터 보존

새 `setup.py`를 기존 설치 폴더에서 다시 실행해도 운영 데이터는 보존됩니다.

보호 대상에는 다음이 포함됩니다.

- `data/`, `flow-data/`, `Fab/`, `DB/`, `Base/`, `wafer_maps/`
- `FLOW_DATA_ROOT`, `FLOW_DB_ROOT`, `FLOW_WAFER_MAP_ROOT` 아래의 모든 파일
- users, sessions, groups, informs, tracker, meetings, dashboard, cache와 로그

설치 전 소형 설정/state 파일은 사용자 홈의 `.flow_backups`에 snapshot됩니다. 수동 복구가 필요하면 다음 명령을 사용합니다.

```bash
python setup.py restore latest
```

버전과 번들 생성 시각 확인:

```bash
python setup.py version
```

### 자동 재시작이 켜진 서버를 업데이트할 때

`systemctl stop` → `extract` → `systemctl start` 순서를 지켜야 합니다. 프로세스를 `kill`로 죽이면 systemd가 즉시 되살려 구버전과 신버전이 섞입니다. 단계별 절차와 검증 기준은 위의 **[프로토콜 B — 업데이트](#프로토콜-b--업데이트-setuppy-재배포)** 를 그대로 따릅니다.

## 개별 설치 명령

```bash
python setup.py extract
python setup.py install-deps
python setup.py build-frontend
python setup.py version
python setup.py sync-version
python setup.py restore latest
```

## 검증

현재 배포본은 다음 검증을 통과한 상태로 생성합니다.

- Python 구문 검사
- 백엔드 pytest 60개
- Vite production build
- 로컬 HTML/JS HTTP 200 smoke test
- `setup.py` 추출 파일 목록 및 데이터 제외 정책 확인

## 주의 사항

- `setup.py`는 사내 코드와 프런트엔드를 포함하므로 외부에 공개하지 마십시오.
- 운영 서버와 worker 서버의 시스템 시간과 공유 경로 권한을 맞추십시오.
- DB 및 runtime data를 Git 저장소 안에 직접 커밋하지 마십시오.
- 개발 worker 메모리가 부족할 때 동시 실행 수를 늘리지 마십시오.

## License

Private. 사내/개인 검증 목적으로만 사용합니다.
