# Worker Dispatch — 개발서버 워커 분산 (v9.4.x)

운영/개발 2개 서버가 같은 shared workspace(flow-data)를 공유하는 배포에서,
무거운 파일 산출 작업을 개발서버(워커)로 넘겨 운영서버 부하를 줄이는 구조.

```text
운영서버 (FLOW_SERVER_ROLE=api)          개발서버 (FLOW_SERVER_ROLE=worker)
  유저 접속 · API · 프론트엔드              큐 소비 · 무거운 빌드 실행
  빌드 필요 → 큐에 task 파일 작성   ──→    {data_root}/worker/queue/ 폴링
  results/ 폴링으로 결과 대기       ←──    빌드 후 results/<id>.json 작성
  워커 heartbeat stale → 로컬 실행         heartbeat.json 10초 주기 갱신
```

- 브로커 없음 — shared workspace 파일만 사용 (`{data_root}/worker/`).
- 개발서버는 언제든 꺼질 수 있다: heartbeat 가 45초(기본) 이상 낡으면 운영서버가
  로컬 실행으로 자동 전환하고, heartbeat 가 돌아오면 다음 디스패치부터 자동으로
  오프로드가 재개된다 (상태 저장 없음 — 매번 heartbeat 신선도만 본다).
- 오프로드 대상은 **결과가 공유 파일로 남는 작업만**: SplitTable pivot 캐시,
  fab lot index, ML_TABLE lookup 파티션. 프로세스 RAM 캐시 워밍은 서버별
  자원이므로 각 서버가 각자 한다.
- 이중 실행 가드는 기존 그대로 shared_lease / 빌드 락이 담당 — 폴백 경쟁의
  최악 케이스도 중복 빌드 1회로 기존과 동일.

## 코드 위치

| 파일 | 역할 |
|---|---|
| `backend/core/worker_dispatch.py` | 역할 판정, heartbeat, 파일 큐, `run_heavy()` 디스패치+폴백 |
| `backend/core/worker_tasks.py` | 워커가 실행하는 핸들러 등록 (`@handler("...")`) |
| `backend/routers/splittable.py` | pivot / fab lot index 빌드가 `run_heavy()` 경유 |
| `backend/core/ml_table_lookup.py` | lookup 파티션 빌드가 `run_heavy()` 경유 |
| `backend/app_v2/runtime/startup.py` | `worker_dispatch.start_services()` 기동 |
| `backend/routers/monitor.py` | `GET /api/monitor/worker` 상태 스냅샷 |
| `backend/routers/admin.py` | 역할 조회/변경 · 원격 기동 admin API |
| `frontend/src/pages/My_Admin.jsx` | `WorkerPanel` — 신호등 · 역할 설정 · 켜기 버튼 |
| `scripts/worker_watchdog.py` | 개발서버 상주 워치독 (start_request 소비 → uvicorn spawn) |

## 역할 고정 — 서버별로 한 번 정하면 부팅 시 항상 자동 적용

역할 우선순위: **env `FLOW_SERVER_ROLE` > `server_role.json` > 자동**.
`server_role.json` 은 서버별 로컬 파일(공유 workspace 아님)이고 영구적이다 —
한 번 기록되면 이후 모든 부팅에서 그 역할로 뜬다. 매번 설정할 필요가 없다.
저장 위치는 `{app_root}/server_role.json` 이 기본이고, app_root 가 읽기전용인
배포에서는 `{data_root}/worker/roles/<hostname>.json` 폴백에 저장된다 — 해석
시 두 파일 중 `updated_at` 이 최신인 쪽을 택한다:

- **운영서버**: prod 자동감지(`PATHS.is_prod`)로 별도 설정 없이 항상 `api`.
  명시하고 싶으면 env 나 관리자 탭에서 한 번 고정.
- **개발서버**: `scripts/worker_watchdog.py` 가 시작 시 자기 머신의
  `server_role.json` 을 `worker` 로 자동 고정한다. 워치독을 부팅 등록해 두면
  그 머신에서 flow 를 어떻게 켜든 (원격 기동·수동 uvicorn) 항상 워커로 뜬다.
  워치독이 spawn 하는 프로세스는 env `FLOW_SERVER_ROLE=worker` 로도 이중 보장.
- **관리자 탭**(모니터 → 워커 서버 패널)의 역할 설정은 상태 확인과 예외적
  전환용이지 매번 바꾸는 용도가 아니다. 저장하면 역시 영구 고정된다.

변경은 재시작 없이 즉시 반영된다 — heartbeat/큐 소비/디스패치 루프가 매 반복
역할을 다시 읽는다. (단, startup 에서 한 번만 켜는 heavy 백그라운드 스케줄러
세트는 재시작 후 완전 적용.) env 로 고정된 배포에서는 UI 편집이 거부된다.

## 관리자 탭 — 워커 서버 패널

관리자 콘솔 → 모니터 최상단. 8초 폴링으로 자동 갱신.

- **신호등**: 초록(pulse) = 개발서버 온라인 (heartbeat 신선), 노랑 = 기동
  요청됨/부팅 대기, 빨강 = 오프라인. heartbeat 나이·워커 식별자·실행 중
  작업 수를 함께 표시. 오프라인일 때는 사유를 구분해 안내한다 —
  `stale`(heartbeat 는 있으나 낡음 = 워커 다운) / `no_heartbeat`(기록 자체가
  없음 = 개발서버 FLOW_DATA_ROOT 불일치 의심). 서버 간 벽시계가 stale
  한도(45s) 이상 어긋난 배포에서는 heartbeat '값의 변화'로 생존을 판정하고
  "시계 차이 감지"를 함께 표시한다 (v9.4.5 — 이전에는 시계가 어긋나면 살아있는
  워커가 항상 오프라인으로 보였다).
- **이 서버 역할**: api / worker / standalone 선택 + 저장 (`server_role.json`).
- **개발서버 켜기**: 오프라인일 때 표시. shared workspace 에 start_request 를
  남기고 개발서버의 상주 워치독이 소비해 uvicorn 워커를 띄운다. 워치독이
  응답하지 않으면 버튼 대신 "워치독 미응답" 안내가 뜬다.
- 오프로드/원격성공/로컬폴백 카운터와 큐 깊이를 함께 노출.

Admin API: `GET /api/admin/worker`, `POST /api/admin/worker/role`,
`POST /api/admin/worker/start` (모두 require_admin, 감사 로그 기록).

## 원격 기동 워치독 (개발서버 상주)

flow 앱이 죽어 있어도 원격 기동이 되려면 개발서버 머신에 워치독을 상시
띄워 둔다 (stdlib only, systemd / 작업 스케줄러 등록 권장):

```bash
FLOW_DATA_ROOT=/config/work/sharedworkspace/flow-data \
    python scripts/worker_watchdog.py --port 8081
```

- 시작 시 이 머신의 `server_role.json` 을 `worker` 로 자동 고정 (env 지정 시 생략).
- `control/watchdog.json` 에 5초 주기 heartbeat → 관리자 탭 "켜기" 활성 조건.
- `control/start_request.json` 감지 시: 워커가 이미 살아 있으면 요청만 소비,
  아니면 `FLOW_SERVER_ROLE=worker` 로 uvicorn spawn (로그:
  `control/worker_uvicorn.log`). 자동 재시작은 하지 않는다 — 기동은 항상
  관리자의 명시 요청으로만.

## 배포

운영서버 (기존 그대로 — prod 자동감지 시 역할 명시 불필요):

```bash
FLOW_SERVER_ROLE=api          # 또는 관리자 탭에서 "운영 (API)" 저장
```

개발서버 (5코어 15GB 기준):

```bash
FLOW_SERVER_ROLE=worker       # 또는 관리자 탭에서 "개발 (워커)" 저장
# 선택 조정:
FLOW_WORKER_CONCURRENCY=2     # 동시 빌드 슬롯 (polars 스레드 예산과 곱해짐)
uvicorn app:app --host 0.0.0.0 --port 8081
python scripts/worker_watchdog.py --port 8081   # 별도 상주 (원격 기동용)
```

worker 역할이면 `FLOW_ENABLE_HEAVY_BACKGROUND_JOBS` 미지정이어도 tracker 등
무거운 백그라운드 스케줄러가 기본 켜진다 (명시 env 가 항상 우선).

## 역할별 기능 분리 (v9.4.5)

worker(개발서버) 역할은 **로드 분산 전용**이다 — SplitTable 조회, 데이터 처리,
큐 소비만 수행하고 외부 서비스로 나가는 부수효과는 전부 운영(api)·standalone
서버만 수행한다. 판정은 `worker_dispatch.external_services_enabled()`
(= `server_role() != "worker"`) 한 곳으로 모은다.

| 기능 | worker 에서 | 가드 위치 |
|---|---|---|
| S3 artifact 업로드 (`core/s3_sync.py`) | 차단 (`status=disabled_role`, store fallback 포함) | `sync_one()` |
| S3 자동 동기화 스케줄 (`routers/s3_ingest.py`) | 차단 (매 30s 루프에서 역할 재확인 — 재시작 불필요) | `_scheduler_loop()` |
| 메일 발송 (`core/mail.py`) | 차단 — 인폼로그/회의/valve 알람 등 모든 발송 경로 공통 | `send_mail()` |
| backup / valve watch / valve alerts / dedup 스케줄러 | 시작 안 함 (startup 1회 판정 — 역할 변경 시 재시작 후 적용) | `app_v2/runtime/startup.py` |
| SplitTable 캐시·revalidator·tracker 스캔·큐 소비 | 그대로 실행 (워커의 본업) | — |

수동 API 호출(관리자가 개발서버 UI 에서 직접 실행)도 위 가드를 그대로 탄다 —
개발서버는 어떤 경로로든 S3/메일에 닿지 않는다.

## 환경변수

| 변수 | 기본 | 의미 |
|---|---|---|
| `FLOW_SERVER_ROLE` | prod→api, 그 외 standalone | api / worker / standalone |
| `FLOW_WORKER_OFFLOAD` | 1 | api 역할의 오프로드 스위치 |
| `FLOW_WORKER_HEARTBEAT_SEC` | 10 | 워커 heartbeat 주기 |
| `FLOW_WORKER_STALE_SEC` | 45 | 이보다 낡은 heartbeat = 워커 다운 판정 |
| `FLOW_WORKER_CONCURRENCY` | 2 | 워커 동시 실행 슬롯 |
| `FLOW_WORKER_TASK_TIMEOUT_SEC` | 1800 | 오프로드 결과 대기 한도 (초과 시 로컬 폴백) |
| `FLOW_WORKER_MAX_QUEUE` | 32 | 큐 깊이 초과 시 로컬 실행 |

## 관측

- `GET /api/monitor/worker` — 역할, 워커 생존, heartbeat 나이, 큐 깊이,
  오프로드/폴백 카운터.
- 운영서버 로그: `worker ONLINE/OFFLINE` 전이가 INFO/WARNING 으로 남는다.
- 워커 로그: `worker executing …` / `worker finished … ok=…`.

## 새 오프로드 작업 추가하는 법

1. `core/worker_tasks.py` 에 `@handler("작업이름")` 함수 추가 — 페이로드는
   JSON 직렬화 가능한 dict, 반환도 작은 dict (큰 산출물은 공유 파일로).
2. 호출부에서 기존 로컬 실행을 `_local_build()` 클로저로 감싸고
   `worker_dispatch.run_heavy("작업이름", payload, _local_build)` 호출.
3. cross-server 이중 실행 가드(shared_lease 등)는 핸들러/로컬 양쪽 실행부
   안에 둔다 — 디스패치 계층은 가드를 제공하지 않는다.
