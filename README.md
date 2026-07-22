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
uvicorn app:app --host 0.0.0.0 --port 8081
```

개발 worker 기본 정책:

- 무거운 작업 동시 실행 1개
- Polars 최대 2 threads
- API용 product/root/view RAM 캐시 비활성화
- backup, mail, S3 등 운영 scheduler 비활성화
- worker available memory와 process memory admission 통과 후 작업 실행

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
