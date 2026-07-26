# 운영 API / 개발 Worker 분산 구조

운영 서버와 개발 서버는 같은 `FLOW_DATA_ROOT`와 `FLOW_DB_ROOT`를 본다. 별도 broker
없이 `{data_root}/worker` 아래의 원자적 JSON task/result 파일과 heartbeat를 사용한다.

```text
운영 API (FLOW_SERVER_ROLE=api)
  요청 처리 → heavy task 제출 → 결과 대기 → shared cache 즉시 사용
                    │
                    ▼
개발 Worker (FLOW_SERVER_ROLE=worker)
  heartbeat → task claim → 빌드 1개 실행 → result 기록
```

## 역할별 책임

운영 API:

- 사용자 요청과 SplitTable 조회를 처리한다.
- root/product/view RAM cache를 소유한다.
- worker가 없거나 task가 실패한 경우에만 local fallback을 수행한다.
- local heavy fallback은 한 번에 하나만 실행하고 memory admission을 통과해야 한다.

개발 worker:

- SplitTable pivot, ML lookup partition, FAB lot index, FAB match cache 등 공유 파일로
  결과가 남는 작업만 수행한다.
- `FLOW_WORKER_CONCURRENCY=1`, Polars 2 threads가 기본이다.
- API용 RAM cache, backup/mail/S3 scheduler, tracker/filebrowser prewarm은 실행하지
  않는다. 빌드 후 생긴 worker RAM은 task 종료와 함께 회수 대상이다.

수동 캐시 스캔의 FAB match 단계도 제품 단위 worker task로 위임한다. multi-GB FAB
원본을 운영 API와 개발 worker가 동시에 scan하지 않도록 전체 pipeline과 worker
concurrency를 직렬화한다.

## 생존 및 과부하 판단

- heartbeat 주기 기본 10초, stale 기본 45초
- worker queue 기본 최대 32개
- worker available memory 기본 하한: 전체 RAM의 25%, 2.5~4GB 범위
- worker process memory admission: process soft limit의 80%
- task 결과 대기 기본 1800초

heartbeat가 없거나 stale이면 API가 task를 제출하지 않고 local fallback으로 전환한다.
worker가 task를 받은 뒤 실패한 경우 결과의 실패 상태를 API가 확인하고 fallback한다.
죽은 worker가 남긴 build lock은 heartbeat owner 불일치를 확인해 회수한다.

## Local fallback 안전장치

worker를 사용할 수 없을 때도 heavy 작업은 `_LOCAL_HEAVY_GATE` 하나로 직렬화한다.
자동 lookup/pivot/FAB index/match 재생성은 사용자 요청이 10초 이상 조용해질 때까지
기본 최대 30분 기다린다. 수동 스캔처럼 사용자가 명시적으로 시작한 pipeline만 즉시
실행한다. 기본 host memory reserve는 전체 RAM의 15%(2.5~6GB 범위)다. process soft limit 또는
host reserve를 침범하면 최대 120초 기다린 뒤 `local_heavy_memory_guard`로 실패한다.
동시에 여러 parquet 전체 scan을 시작해 OOM으로 프로세스가 종료되는 것보다 명시적
실패와 재시도를 우선한다.

## 주요 환경변수

| 환경변수 | 기본 | 의미 |
|---|---:|---|
| `FLOW_SERVER_ROLE` | 자동 | `api`, `worker`, `standalone` |
| `FLOW_WORKER_OFFLOAD` | 1 | API의 worker 위임 사용 |
| `FLOW_WORKER_CONCURRENCY` | 1 | worker 동시 heavy task 수 |
| `FLOW_WORKER_HEARTBEAT_SEC` | 10 | heartbeat 주기 |
| `FLOW_WORKER_STALE_SEC` | 45 | worker offline 판단 시간 |
| `FLOW_WORKER_TASK_TIMEOUT_SEC` | 1800 | 원격 결과 대기 한도 |
| `FLOW_WORKER_MAX_QUEUE` | 32 | 원격 queue 깊이 상한 |
| `FLOW_WORKER_OFFLOAD_MIN_AVAIL_GB` | 자동 | worker task claim 전 host 여유 RAM |
| `FLOW_WORKER_OFFLOAD_MAX_MEM_PCT` | 80 | worker process memory 비율 상한 |
| `FLOW_LOCAL_HEAVY_MIN_AVAILABLE_GB` | 자동 | local fallback 전 host 여유 RAM |
| `FLOW_LOCAL_HEAVY_IDLE_QUIET_SEC` | 10 | 자동 local fallback 시작 전 무요청 시간 |
| `FLOW_LOCAL_HEAVY_IDLE_WAIT_SEC` | 1800 | 사용자 요청이 계속될 때 다음 주기로 미루기 전 대기 한도 |
| `FLOW_ENABLE_WORKER_RAM_CACHE` | 0 | 진단 목적 worker RAM cache 허용 |

## 관측 API와 화면

- `GET /api/monitor/worker`: 역할, heartbeat, 현재 load, queue와 실행 task
- `GET /api/splittable/ram-cache/scan-status`: 수동 pipeline 단계와 관련 queue
- `GET /api/splittable/cache-event-log`: 최근 cache job, peak memory, queue
- RAM 캐시 관리 화면: 현재/최근 job, future queue, API/worker memory 표시

`worker_dispatch.queue_snapshot()`은 task ID/type/product만 노출하고 payload 전체나
공유 파일 절대경로는 관리 화면에 내보내지 않는다.

## 배포 예시

운영 서버:

```text
FLOW_SERVER_ROLE=api
```

개발 서버:

```text
FLOW_SERVER_ROLE=worker
FLOW_WORKER_CONCURRENCY=1
FLOW_POLARS_MAX_THREADS=2
```

개발 서버에는 `scripts/worker_watchdog.py --port 8081`을 OS 서비스나 작업 스케줄러로
등록한다. watchdog은 start request를 받아 worker uvicorn을 다시 올릴 수 있지만,
OOM 원인을 숨기는 무한 병렬 재시작 용도로 사용하지 않는다.

## 코드 위치

| 파일 | 역할 |
|---|---|
| `backend/core/worker_dispatch.py` | heartbeat, queue, admission, fallback |
| `backend/core/worker_tasks.py` | worker task handler 등록 |
| `backend/core/runtime_limits.py` | 역할별 CPU/process memory 기본값 |
| `backend/routers/splittable.py` | SplitTable 빌드 제출과 cache pipeline |
| `backend/core/ml_table_lookup.py` | root lookup build/partition 조회 |
| `scripts/worker_watchdog.py` | 개발 서버 상주 감시 프로세스 |
