# SplitTable 검색 속도 — CPU/메모리 스케일링 분석 & 권장치

작성 2026-07-14. 대상: root_lot_id 검색이 4000행 기준 최대 ~10초 걸리는 콜드 경로.

## 1. 검색 단계별 breakdown (관리자 화면에서 실시간 확인)

`GET /api/splittable/view` 응답의 `runtime_profile` 과 관리자 페이지의
"SplitTable 검색 타이밍" 표에서 아래 값을 ms 단위로 확인한다.

| 단계 | 필드 | 내용 | 병렬화 |
|---|---|---|---|
| 데이터 소스 | `data_source` | payload_cache / pivot_cache / product_ram / **ram**(메모리 HIT) / **ram_load**(첫 적재) / **disk**(첫 검색) / raw | — |
| root scan | `root_scan_ms` | RAM 캐시 조회 or 파티션 parquet 읽기 | polars |
| scan(준비) | `scan_ms` | 위 + latest-lot/fab override join **lazy 구성** | polars |
| collect | `collect_ms` | 피벗 프레임 실제 collect | **polars (코어 스케일)** |
| matrix | `matrix_ms` | 셀 매트릭스 구성(Python 루프) | **serial (병목)** |
| overlay | `overlay_ms` | plan/tag/management 오버레이 | 경미 |

콜드(=`disk`/`ram_load`) 10초의 대부분은 **collect_ms**(파티션 스캔+조인+피벗)와
**matrix_ms**(4000행 × 파라미터 셀 Python 루프)에 몰린다. 메모리 HIT(`ram`)면
root_scan 이 수십 ms 로 떨어지고 collect 도 in-memory 라 전체가 sub-second 로 준다.

## 2. CPU 스케일링

- polars 스레드 = `min(FLOW_CPU_BUDGET_CORES, 코어수-1)` (기본 `코어수-1`). scan/join/
  collect 가 이 스레드로 병렬 실행된다 (`backend/core/runtime_limits.py:_default_polars_threads`).
- **권장: 물리 8코어(→ polars 7스레드).** 4→8코어면 polars 병렬 구간이 대략 1.7~2×
  빨라진다. 8코어 초과는 단일 4000행 검색에선 수확체감 — parquet row-group 수 상한과
  serial 한 `matrix` 루프(Amdahl) 때문. 동시 사용자(설계 30명)를 감안하면 8~12코어가
  현실적 상한.
- 예열(상시 RAM 캐시 채우기)은 이제 **병렬 로드**(`FLOW_SPLITTABLE_ROOT_LOT_RAM_CACHE_LOAD_WORKERS`,
  기본 `min(8, cpu_budget)`)라 코어 수에 비례해 hot set 을 빨리 채운다 →
  콜드 10초 검색 자체의 발생 빈도가 준다.

## 3. 메모리 스케일링

- RAM 캐시 예산 = RSS 한도의 40%, `[3, 8]GB` 클램프. RSS 한도 = 총 메모리의 80%
  (`runtime_limits.auto_process_memory_limit_gb`, `_root_ram_cache_auto_max_gb`).
- 상시 유지 목표 = `target_roots` ≈ 1000 root(knob 수준). 4000행 root 파티션이 수 MB면
  1000 root ≈ 수 GB.
- **권장: 개발 16GB / 양산 24~32GB.**
  - 16GB → RSS 한도 ~12.8GB → 캐시 예산 ~5GB (16×0.8×0.4). 1000-root hot set 이 대체로
    상주 → 대부분 검색이 메모리 HIT.
  - 메모리가 부족하면 eviction 으로 hot root 가 디스크로 밀려 **10초 콜드 읽기가 반복**된다.
  - `FLOW_SPLITTABLE_ROOT_LOT_RAM_CACHE_MAX_GB` 로 캐시 예산을 명시 고정 가능.

간이 산식: 필요 캐시 예산 ≈ (target_roots) × (root당 평균 파티션 MB). 이 값이 host의
40%×80%×총메모리보다 작아야 eviction 없이 상주한다.

## 4. 병렬처리 반영/후속

- **반영됨**: 상시 RAM 캐시 예열의 파티션 로드 병렬화(위 워커). eviction/우선순위
  결정성은 "병렬 로드 → 우선순위 순 순차 삽입"으로 보존.
- **후속 후보**: `matrix` 단계의 Python 셀 루프(`splittable.py` `_prepare_view_frame` 이후
  매트릭스 구성)가 유일한 serial 병목. 대량 행에서 polars 벡터화 또는 파라미터 청크
  병렬화로 추가 개선 여지. (이번 변경 범위 밖.)

## 5. 설정 레버 요약

| env | 기본 | 용도 |
|---|---|---|
| `FLOW_CPU_BUDGET_CORES` | 자동(코어-1) | polars/예열 코어 예산 |
| `FLOW_POLARS_MAX_THREADS` | min(예산, 코어-1) | polars 스레드 직접 지정 |
| `FLOW_PROCESS_MEMORY_LIMIT_GB` | 자동(총×0.8) | RSS 소프트 한도 |
| `FLOW_SPLITTABLE_ROOT_LOT_RAM_CACHE_MAX_GB` | 자동(한도×0.4, 3~8) | RAM 캐시 예산 |
| `FLOW_SPLITTABLE_ROOT_LOT_RAM_CACHE_TARGET_ROOTS` | 1000 | 상시 유지 root 수 |
| `FLOW_SPLITTABLE_ROOT_LOT_RAM_CACHE_LOAD_WORKERS` | min(8, 예산) | 예열 병렬 로드 워커 |
| `FLOW_SPLITTABLE_ROOT_LOT_RAM_CACHE_STEP_IDS` | 설정(톱니바퀴) | 캐싱 대상 step_id |

**요약 권장**: 8코어 / 16GB(개발), 8~12코어 / 24~32GB(양산 동시 30명). 이러면 지정 step
통과 lot ~1000개가 상시 메모리 상주 → 콜드 10초 검색이 대부분 메모리 HIT sub-second 로
수렴하고, 남는 콜드 검색도 polars 코어 스케일로 단축된다.
