# SplitTable 검색 및 메모리 운영 기준

기준 환경은 운영 서버 5코어/28GB, 개발 worker 서버 5코어/10~15GB이다.
FAB 원본은 수 GB, `ML_TABLE_<제품>.parquet`은 수백 MB, 제품별 root lot은
수천 개까지 증가할 수 있다고 가정한다.

## 검색 경로

1. `/api/splittable/view`는 같은 조건의 완성된 view payload cache를 먼저 찾는다.
2. 서로 다른 root lot의 첫 검색은 제품별 pre-pivot cache 또는 root-lot lookup
   partition 하나만 읽는다. 요청 스레드는 해당 root의 전체 wide frame을 RAM에
   올리지 않고, 화면에 필요한 prefix/custom 컬럼만 parquet projection으로 읽는다.
3. 수백 MB ML_TABLE에 lookup cache가 없으면 API 프로세스에서 원본 전체를 읽지
   않는다. worker에 빌드를 예약하고 `queued/running` 응답을 반환한다.
4. UI는 준비 응답을 받으면 `cache_first=true`로 자동 재조회한다. 준비 중 재조회는
   검색 감사 로그를 중복 생성하지 않는다.
5. 동일 키의 동시 첫 검색은 single-flight로 합쳐 같은 root를 여러 번 계산하지 않는다.
6. API startup maintainer가 누락된 lookup/pivot/FAB root index를 선제 탐지한다. 실제
   빌드는 개발 worker에 맡기고, worker가 꺼져 있으면 운영 서버가 사용자 요청이
   10초 이상 조용할 때 하나씩 수행한다.
7. 디스크에서 처음 읽은 root의 전체 RAM 예열 역시 요청 밖의 단일 idle queue에서
   진행한다. 압축 partition이 128MB를 넘으면 transient OOM 방지를 위해 RAM 예열을
   생략하고 projection 디스크 경로를 유지한다.

`cache_status()`는 성공 시 마지막에 원자 기록되는 `_meta.json`으로 완성 여부를
판단한다. 모든 root 디렉터리를 재귀 순회하지 않는다. DB/cache 경로와 의존 파일
signature는 짧은 TTL로 메모한다. 실제 파티션이 사라졌지만 meta가 남아 있으면
candidate index에 존재하는 root에 한해 자동 재빌드를 예약한다.

## 현재 샘플 기준 (2026-07-22)

- lookup 상태 확인: median 약 0.36ms, p95 약 0.48ms
- 서로 다른 5개 root 순차 조회(pivot 준비됨): 56~151ms, 첫 import성 조회 제외 시
  약 56~86ms
- 서로 다른 5개 root 동시 조회: 전체 wall 312ms, 각 요청 308~311ms
- 동일 조건 재조회: 8.7ms
- 5개 cold lookup partition 동시 projection 조회: 전체 wall 433ms, 측정 프로세스
  peak RSS 증가 71.7MB
- 전체 30-root pivot 준비: 988ms, 측정 프로세스 peak RSS 증가 67.0MB

샘플은 약 0.8MB ML_TABLE/30 root 데이터다. 운영의 수백 MB 원본에서는 첫 전체
lookup 빌드 시간은 더 길지만, 요청 프로세스는 그 파일을 직접 전체 scan하지 않고
준비 화면을 반환한다. lookup 완성 뒤 개별 root 조회량은 제품 전체가 아니라 해당
partition 크기에 비례한다.

## 메모리 한도

기본 process soft limit은 물리 메모리의 80%, 전체 cache pool은 물리 메모리의
45%이다. 개별 cache 환경변수를 더 크게 지정해도 전체 pool의 해당 share를 넘지
못한다.

| cache | pool share | 운영 28GB 기준 상한 |
|---|---:|---:|
| SplitTable root RAM | 40% | 약 5.04GB |
| FileBrowser preview | 18% | 약 2.27GB |
| SplitTable product RAM | 14% | 약 1.76GB |
| SplitTable view payload | 12% | 약 1.51GB |
| Reformatize raw | 10% | 약 1.26GB |
| Reformatize wide | 6% | 약 0.76GB |

운영 API의 process soft limit은 약 22.4GB다. worker가 없어서 운영 서버에서
fallback하는 heavy 작업도 semaphore 1개로 직렬화하며, 기본 약 4.2GB의 host
available memory를 남기지 못하면 최대 120초 대기 후 안전하게 실패한다. 자동
유지보수 fallback은 사용자 요청이 계속 있으면 기본 최대 30분 대기 후 다음 주기로
미루며, pivot 파일 생성도 root 1개씩 처리한다.

개발 worker는 `FLOW_WORKER_CONCURRENCY=1`, Polars 2 threads가 기본이다. API용
root/product/view RAM cache 및 일반 scheduler는 worker 역할에서 비활성화된다.
worker task claim은 available memory 2.5~4GB와 process memory 80%를 admission
기준으로 사용한다. lookup cache 초기 생성은 운영 fallback에서 root 4개, 개발
worker에서 root 2개씩만 collect하고, pivot 생성은 서버 역할과 무관하게 1개씩
처리한다.

5코어 운영 API에서 root-scoped SplitTable 조회 lane은 기본 3개다. 5명이 동시에
조회하면 3개가 실행되고 2개는 최대 90초 queue에서 기다리므로, 다섯 full-wide
collect가 동시에 겹치거나 즉시 429가 나는 구조가 아니다.

## 권장 역할 설정

운영 서버:

```text
FLOW_SERVER_ROLE=api
FLOW_CACHE_TOTAL_BUDGET_FRACTION=0.45
FLOW_PROCESS_MEMORY_LIMIT_FRACTION=0.80
```

개발 서버:

```text
FLOW_SERVER_ROLE=worker
FLOW_WORKER_CONCURRENCY=1
FLOW_POLARS_MAX_THREADS=2
```

개별 RAM cache GB 값을 먼저 고정하기보다 전체 pool 비율을 유지한다. 특정 cache를
줄여야 할 때만 해당 `*_MAX_GB` 값을 더 낮게 지정한다.

## 운영 관측

RAM 캐시 관리 화면의 수동 스캔 패널은 다음을 표시한다.

- FAB match → 제품 RAM → root lot RAM 단계별 queued/running/done/failed 상태
- 현재 API RSS, 해당 작업 시작 이후 API peak 증가량, 최소 host available memory
- worker 실행/대기 task, lookup build 실행/대기 제품
- 현재 root RAM idle 예열 항목과 앞으로 대기 중인 root
- worker 현재 effective memory와 worker 프로세스 lifetime peak RSS

API 작업 peak와 worker 프로세스 peak는 서로 다른 프로세스의 값이므로 화면에서도
구분해서 표기한다.

## 검증

```powershell
python -m py_compile backend/core/cache_event_log.py backend/core/ml_table_lookup.py backend/core/worker_dispatch.py backend/core/worker_tasks.py backend/routers/splittable.py
python -m pytest -q tests/test_worker_cache_resilience.py
cd frontend
npm run build
```
