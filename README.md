# Flow

Flow는 반도체 개발·파일 공유·SplitTable 관리·WF MAP 검사·데이터 분석을 lot/wafer 중심으로 연결하는 FastAPI + React 웹 애플리케이션입니다.

이 GitHub 저장소는 사내 반입을 단순화하기 위해 세 파일만 배포합니다.

- `setup.py`: 백엔드, 프런트엔드, 문서와 운영 스크립트를 압축 포함한 단일 설치 파일
- `README.md`: 설치 및 운영 안내
- `VERSION.json`: 버전과 릴리스 노트. `setup.py` 번들 안에도 같은 파일이 들어 있지만, 변경 이력은 잃으면 복구할 수 없어 커밋 diff로 보이도록 따로 추적합니다.

DB, 계정, 설정, 로그, 캐시 등 운영 데이터는 `setup.py`에 포함되지 않으며 업데이트 시에도 덮어쓰지 않습니다.

## 주요 기능

- 파일 공유, 대용량 parquet/CSV 탐색 및 미리보기
- 제품별 SplitTable 검색, plan/actual 비교와 편집
- root lot/wafer 기준 FAB 데이터 결합
- WF MAP, TEG MAP, 공정 데이터 검사
- ET 추적(일일 스캔·변경점 이슈·메일 발송)과 ET Index 다운로드
- 개발 서버 FAB 매칭알람 검사 — 제품별 신규 step_id / ppid 를 찾아 룰북·매칭테이블 CSV에 반영
- 제품별 랏 배정·Hot grade 요청, PI 처리 상태·답변 및 작성자별 수정·삭제 이력 관리
- Inform Note와 랏 요청·답변의 게시판형 본문 — 이미지와 Excel 표를 Ctrl+V로 본문에 직접 삽입
- Inform, Tracker, Meeting, Dashboard 및 Flow-i 지원
- 업무 차트생성에서 여러 DB의 read-only SQL 결과를 JOIN·미리보기·CSV 다운로드하고 동일 데이터로 차트 생성
- 운영 API 서버와 개발 worker 서버를 이용한 무거운 작업 분산
- 필수 SplitTable 캐시 작업 큐, 실행 중 작업과 피크 메모리 관측

수동으로 준비하는 제품별 필수 캐시는 ① 랏 lookup ② `root_lot_id`별 SplitTable pivot
③ WIP latest-lot ④ root별 FAB latest 인덱스의 네 종류입니다. ET history는 ET 추적에서
독립적으로 관리하며 SplitTable 필수 캐시 완료 조건에는 포함하지 않습니다.
통합 캐싱은 실제 단계가 끝날 때까지 작업 큐에 남아 진행 상황과 중단 버튼을 제공하고,
중단 시 현재 안전 배치까지만 마친 뒤 다음 제품·단계는 시작하지 않습니다. 제품 전체 RAM과
Root lot RAM 예열은 사용하지 않습니다. 검색 조건별 완성 응답 캐시는 실제 검색 때만
생기는 read-through 항목이므로 수동 전체 생성 대상에 넣지 않습니다.
제품별 `root_lot_id`·LOT ID 목록과 KNOB별 입력 후보는 lookup 빌드 중 함께 계산해
RAM+공유 디스크에 미리 게시합니다. SplitTable의 Root Lot 후보 요청은 이 목록만 읽고,
캐시가 없거나 오래됐으면 원천·FAB·`lot-ids`를 동기 스캔하지 않고 lookup 빌드만 큐에
넣어 즉시 응답합니다. 준비 중에도 사용자는 Root Lot을 직접 입력해 바로 조회할 수 있습니다.

ET 추적과 Inform 등록은 사용자 입력 레코드를 먼저 내구 저장하고 화면 목록·상세에
즉시 반영합니다. LOT 진행상태/wafer 확장, FAB·ROOT·WAFER 매핑, 다중 LOT별
SplitTable 스냅샷, audit·지식 추출은 응답 이후에 보강한 뒤 화면이 완성 데이터를
재조회합니다. 따라서 무거운 계산이 등록 버튼과 새 항목 표시를 붙잡지 않습니다.

## 랏 배정/요청과 리치 본문

`업무 → 랏 배정/요청`은 제품별 랏 배정, Hot grade, 기타 PI 처리 요청을 업무
이력으로 남기는 보드입니다. 요청은 `등록 → 처리중 → 처리완료`로 진행하거나
`반려`할 수 있으며, 상태 변경자·시각·메모와 PI 답변의 작성자·작성 시각을 함께
보존합니다. 상태 변경은 `lotrequest` 페이지 관리자로 위임된 사용자와 전체
관리자만 가능하지만, 요청 본문은 요청 작성자만, 각 답변은 그 답변 작성자만
수정하거나 삭제할 수 있습니다. 관리자 권한도 다른 사용자의 본문 소유권을
우회하지 않습니다.

랏 요청 본문·PI 답변과 신규 Inform Note는 공통 게시판형 편집기를 사용합니다.
클립보드의 이미지 파일은 Ctrl+V 시 현재 커서 위치에 업로드되어 표시되고,
Excel·Google Sheets에서 복사한 탭/행 형식 텍스트는 HTML 표로 변환됩니다.
저장 시 서버가 허용 태그·스타일과 Flow 내부 이미지 경로만 남기며, Inform 메일
미리보기와 발송 본문에서도 표와 인라인 이미지를 유지합니다. 기존 일반 텍스트
Inform 이력은 별도 변환 없이 계속 표시됩니다.

ET 측정 이력은 제품별 집계 history parquet를 공유합니다. 캐시가 없는 제품만 전체
ET 원본을 한 번 읽어 초기 history를 만들고, 이후 스캔은 최신 원본 날짜 기준 최근
3일만 재집계해 병합합니다. ET Tracker는 이 제품 history에서 LOT/wafer 패키지를
찾아 이슈에 붙이며, 캐시 준비 실패 때만 기존 원본 조회로 폴백합니다.

캐시관리의 제품별 상태는 전체 제품을 한 줄씩 표시합니다. 행을 누르면 lookup,
SplitTable pivot, WIP latest-lot, FAB latest 인덱스의 최근 성공·실패·진행 상세가
펼쳐지고, 목록은 고정 높이 안에서 스크롤되므로 제품 수가 많아도 캐시 이벤트 로그를
아래로 밀어내지 않습니다.
제품 목록은 현재 ML_TABLE 원본 카탈로그를 기준으로 제한합니다. 공유 이벤트 로그에
남은 테스트·진단용 제품명은 상태 행을 만들지 않으므로 빈 실패 행이 누적되지 않습니다.

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

설치 폴더에 `setup.py`를 복사한 뒤 실행합니다. 현장에서 관리하는
`requirements.txt`, `Dockerfile`, `.dockerignore`가 있으면 같은 폴더에 그대로 둡니다.
이 파일들은 setup 번들에 포함되지 않으며 추출할 때 생성하거나 덮어쓰지 않습니다.

```bash
python setup.py
```

이 명령은 다음을 순서대로 수행합니다.

1. 포함된 Flow 소스를 현재 폴더에 추출
2. 같은 폴더의 기존 `requirements.txt`로 백엔드 Python 의존성 설치
   (`requirements.txt`가 없을 때만 Flow 최소 의존성 사용)
3. 프런트엔드 npm 의존성 설치 및 production build

소스만 먼저 풀려면 다음 명령을 사용합니다.

```bash
python setup.py extract
```

`extract`는 pip를 실행하지 않습니다. 의존성만 별도로 설치하려면
`python setup.py install-deps`를 실행합니다. `pip freeze > requirements.txt`로 만든
현장 파일도 그대로 사용할 수 있으며, 충돌 방지를 위해 아래 사내 패키지는 필요에 따라
버전 표기를 제거한 형태로 유지합니다.

```text
botocore
boto
awscli
bigdataquery
```

Docker 빌드는 setup이 새 파일을 만드는 방식이 아니라 설치 폴더에 이미 있는
`Dockerfile`과 `.dockerignore`를 사용합니다.

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

## FAB 매칭알람 검사

매칭알람은 Valve/S3에서 JSON 알람을 받지 않습니다. 개발 worker가 파일탐색기의
폴더 설정에서 표시명이 정확히 `FAB`으로 지정된 DB만 찾아 제품 폴더를 하나씩
순차 검사하고, 결과를 `FLOW_DATA_ROOT`의 공유 상태에 저장합니다. 수~수십 GB
Parquet를 여는 실제 검사는 `FLOW_SERVER_ROLE=worker`인 개발 서버에서만 실행됩니다.
운영 API 서버(`FLOW_SERVER_ROLE=api`)는 저장된 결과만 조회하며, 화면에서 수동
검사를 요청해도 운영 서버가 원천을 읽지 않고 개발 worker에 다음 제품 검사를
요청합니다. 역할이 없거나 판정에 실패한 경우에도 검사는 실행되지 않습니다.

검사와 판정 기준은 다음과 같습니다.

- 제품별 `step_id`가 `Vehicle_matching.csv`에 없으면 신규 step 알람으로 표시
- 매칭된 step의 function step에 연결된 `ppid_knob.csv` split별 명시 Rule을 적용
- `eq`, `contains`, `starts_with`, `ends_with`, `regex` 어느 Rule에도 맞지 않아
  해당 split의 `RO`로 빠지는 PPID unique 값만 PPID 알람으로 표시
- 신규 step 알람은 제품 범위와 PPID/EQP ID/EQP MODEL의 포함·시작·일치 조건으로
  예외 처리 가능하며, 해당 step의 어느 행이라도 조건에 맞으면 step 전체를 제외

알람 화면에서 PPID를 분류하면 해당 split의 RO 앞에 다음 Rule 번호로
`ppid_knob.csv`에 추가하고, 신규 step을 매칭하면 제품·vehicle·step 정보를
`Vehicle_matching.csv`에 추가합니다. 두 작업 모두 파일탐색기의 단일 파일 저장
흐름을 사용해 버전 스냅샷과 변경 메모를 남기고, 매칭 캐시 갱신과 기존 파일
동기화를 수행한 뒤 개발 worker의 재검사를 요청합니다. 알람 전송용 S3 bucket/prefix
설정은 더 이상 사용하지 않습니다.

DCOP 검사는 붙여넣은 테이블에 실제로 적용되는 규칙 번호·등급·조건을 결과 위에
표시합니다. GPT OSS 120B가 연결된 경우 FAIL/WARNING 내용을 요약하고, 연결되지
않았거나 호출이 실패하면 규칙별 건수와 행 번호(5개 이상은 `5행 이상`)를 표시합니다.

매칭알람 화면 좌하단 톱니바퀴에서는 자동 검사 사용 여부와 제품 1개당 검사 간격을
설정할 수 있습니다. `지금 다음 제품 검사`는 현재 커서의 제품을 우선 검사하도록
공유 요청을 등록하며, 버튼을 누른 HTTP 요청 안에서 대용량 Parquet를 직접 읽지는
않습니다. 신규 설치의 기본 간격은 제품당 2시간이며, 설정 변경과 수동 검사 요청은
매칭알람 페이지 관리자 이상만 가능합니다.

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

## 매칭알람 (Valve 연동)

Valve 파이프라인이 발행한 미매칭 step / RO ppid 알람을 읽어 엔지니어 판정을 받고, 그 판정을 DB 루트의 `Vehicle_matching.csv` · `ppid_knob.csv` 에 반영합니다.

알람 파일의 위치는 `data/flow-data/valve_alerts.json` 의 `local_root` 가 정합니다. 비어 있으면 S3, 값이 있으면 그 폴더를 버킷 루트처럼 씁니다. **S3가 없는 환경에서는 Valve가 알람을 공유 DB 폴더에 떨궈 두고 flow가 그걸 읽는 구성이 기본입니다.**

```json
{ "local_root": "{db_root}", "alerts_prefix": "valve-alerts" }
```

```
{db_root}/valve-alerts/pipeline/{vehicle}.json   ← Valve가 씀
{db_root}/valve-alerts/pipeline/ack.json         ← flow가 씀 (양방향, 폴더 sync로 덮지 말 것)
{db_root}/flow/artifacts/matching/*.csv          ← 판정 반영 결과 (Valve가 가져감)
```

`local_root` 에는 `{db_root}` / `{data_root}` / `{app_root}` 토큰을 쓸 수 있습니다. 설치마다 다른 드라이브 경로를 설정에 박지 않기 위한 것으로, 해석은 `FLOW_DB_ROOT` 체인을 그대로 따릅니다.

미매칭 step 은 **function step 추천**이 함께 뜹니다. 같은 앞 영문자 계열에서 번호가 가까운 매칭 step 을 앞뒤로 뽑아 최근 며칠치 `ppid · eqp_id · eqp_model · area` unique 집합을 비교하고, 사내 LLM이 연결돼 있으면 그 근거로 최종 선택을 받습니다. **LLM이 없어도 동작합니다** — `AI 미적용`과 사유를 표시하고 step_id 숫자가 가장 가까운 step 의 function step 을 제시합니다. 반영은 사람이 확인 후 누릅니다.

데모·점검용 예시 알람은 실제 FAB raw 를 읽어 만듭니다.

```bash
python scripts/seed_valve_alert_examples.py --write
```

## SplitTable 성능 및 메모리 정책

- 서로 다른 root lot 조회도 제품 전체가 아닌 root partition/pivot 파일 하나만 읽습니다.
- 요청 중에는 root의 전체 wide frame을 RAM에 올리지 않고 필요한 prefix/custom 컬럼만 parquet projection으로 읽습니다.
- lookup/pivot/FAB root index는 개발 worker가 우선 생성합니다.
- 자동 lookup/pivot/FAB/view 캐시는 운영 API heartbeat가 살아 있을 때 worker가 꾸준히 처리합니다. API가 내려가면 큐에 보존하고, 복구 후 이어서 처리합니다.
- 수동 캐싱은 normal 우선순위로 큐에 들어가며 worker가 없으면 운영 서버가 메모리 가드를 거쳐 한 작업씩 local fallback합니다.
- pivot 생성은 root 1개씩 처리합니다.
- 제품 전체 RAM과 Root lot RAM 예열은 자동·수동 모두 폐기했습니다. 가장 먼저 읽는 view 응답 RAM은 호스트의 15%, 1~6GB 범위이며 30GB 호스트에서는 약 4.5GB입니다.
- 역할 마커는 소스 폴더가 아니라 `{data_root}/worker/roles/<hostname>/`에 저장되어, `.dev_worker` 같은 파일이 Git/설치 번들에 섞여도 운영 서버가 worker로 기동되지 않습니다.
- 5코어 운영 서버의 cold root-scoped 조회는 기본 2개가 실행되고 추가 요청은 짧은 큐에서 기다립니다.

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
- 백엔드 pytest 1,296개 전부 통과 (실패 0 — 실패는 곧 회귀로 봅니다)
- Vite production build
- 로컬 HTTP smoke test 35항목 (인증 방어 포함)
- `setup.py` 추출 파일 목록 및 데이터 제외 정책 확인

## 주의 사항

- `setup.py`는 사내 코드와 프런트엔드를 포함하므로 외부에 공개하지 마십시오.
- 운영 서버와 worker 서버의 시스템 시간과 공유 경로 권한을 맞추십시오.
- DB 및 runtime data를 Git 저장소 안에 직접 커밋하지 마십시오.
- 개발 worker 메모리가 부족할 때 동시 실행 수를 늘리지 마십시오.

## License

Private. 사내/개인 검증 목적으로만 사용합니다.
