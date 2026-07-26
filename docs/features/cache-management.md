# Cache Management (캐시 관리)

탭 `ramcache` / 라벨 "캐시 관리". SplitTable이 쓰는 RAM 캐시의 현황, 예산, 수동 스캔, 이벤트 로그를 한 화면에서 운영한다. 성능 설계 배경은 [../SPLITTABLE_SCALING.md](../SPLITTABLE_SCALING.md), 서버 역할은 [../WORKER_DISPATCH.md](../WORKER_DISPATCH.md)를 같이 본다.

## Owns

- 전체 RAM 캐시 사용량과 서버 메모리 종합 현황 (운영/개발 서버 각각)
- 제품별 캐시 현황 분해 — root 수, MB, 우선 lot 적재율
- 제품별 **주요 Lot** 등록표 — purpose/comment는 엔지니어가 입력하고 위치(step_id/step_desc)는 최신 진행 데이터에서 자동으로 채운다. 등록된 lot은 RAM 캐시에 우선 적재된다 (lot_id 앞 5자리 = root_lot_id).
- 제품별 캐시 상한(product budget)
- 캐시 수동 스캔 — 통합 스캔/전체 셋업 실행, 진행 단계·실행 중 작업·대기 큐·피크 메모리 표시
- **캐시 예산 톱니바퀴(⚙)** — 풀 비율, root RAM GB, 제품 원본 RAM 캐시 on/off와 GB, view payload MB. 모든 값이 운영/개발 서버별로 분리된다.
- 캐시 이벤트 로그 — 수동 스캔 / 예열 / 축출 / 워치독 / 캐시 적재 필터. 운영·개발 서버 로그를 공유 JSONL에 `origin` 필드로 통합해 한 곳에서 본다.
- 쿼리 병렬 코어 수 설정

## Does Not Own

- SplitTable 검색 자체의 계산 경로 (`My_SplitTable.jsx`, [splittable.md](splittable.md))
- 워커 역할 지정과 원격 기동 — 관리자 탭 소유
- FileBrowser 미리보기 캐시

## Code Entrypoints

| Layer | Path |
|---|---|
| Frontend page | `frontend/src/pages/My_RamCache.jsx` |
| Backend router | `backend/routers/splittable.py` (`/api/splittable/ram-cache/*`, `/cache-budget/*`, `/cache-event-log`) |
| 예산 설정값 | `backend/core/cache_settings.py` |
| 예산 계산 | `backend/core/cache_budget.py` |
| 이벤트 로그 | `backend/core/cache_event_log.py` |
| 축출/청소 | `backend/core/cache_sweeper.py` |
| 설정 저장 | `{data_root}/splittable/cache_budget_settings.json` |
| 이벤트 로그 파일 | `{data_root}/logs/cache_events.jsonl` |

## API

| Method | Path | 용도 |
|---|---|---|
| GET | `/api/splittable/ram-cache/overview` | 제품별 캐시 현황 요약 (미적재 제품은 0으로 포함) |
| GET | `/api/splittable/ram-cache/contents` | 캐시 적재 내역 상세 |
| GET | `/api/splittable/ram-cache/lot-status` | 우선 lot의 적재/위치 상태 |
| GET·POST | `/api/splittable/ram-cache/priority-lots[/save]` | 주요 Lot 등록표 |
| GET·POST | `/api/splittable/ram-cache/product-budgets[/save]` | 제품별 캐시 상한 |
| POST | `/api/splittable/ram-cache/unified-scan` | 수동 통합 스캔 |
| POST | `/api/splittable/ram-cache/full-setup` | 전체 셋업 |
| GET | `/api/splittable/ram-cache/scan-status` | 스캔 진행 폴링 |
| GET | `/api/splittable/ram-cache/knob-allocation` | KNOB 캐시 배분 |
| GET·POST | `/api/splittable/cache-budget/settings[/save]` | 예산 톱니바퀴 |
| GET | `/api/splittable/cache-event-log` | 이벤트 로그 |
| GET·POST | `/api/splittable/product-cache/status`·`/refresh` | 제품 원본 캐시 |
| GET·POST | `/api/splittable/root-lot-cache/status`·`/refresh`·`/evict` | root lot 캐시 |

## Guardrails

- **예산 우선순위는 항상 `env > cache_budget_settings.json > 적응형 기본값`이다.** 운영자가 env로 고정한 값을 UI 설정이 덮지 않는다.
- 설정 파일은 `data_root`에 있고 운영/개발 서버가 `data_root`를 공유한다. 따라서 저장은 두 서버에 동시 적용되며, **서버별로 달라야 하는 값은 반드시 운영/개발 분리 키로 둔다.**
- 캐시 작업은 사용자 요청에 양보한다(`yield_to_users`). 메모리 압박 시 중단한다.
- 서버 메모리가 부족하면 lot 위치(step) 조회를 건너뛰고 화면에 그 사실을 표시한다 — 조용히 빈 값으로 두지 않는다.
- 로그/상태 표시는 "완료"와 "건너뜀"과 "실패"를 구분한다. 대기 중인 작업도 로그에 남는다.
- 이벤트 로그는 인메모리 링 버퍼(200건) + 공유 JSONL이다. 링 버퍼만 보고 "이력이 없다"고 판단하지 않는다.
- worker(개발서버) 역할은 RAM 캐시를 소유하지 않는다. 운영 API 서버가 캐시와 공유 파일 작업 스케줄을 소유한다.

## Verify

```bash
git diff --check
```

```bash
cd frontend && npm run build
```

화면에서 확인할 것: 제품별 현황이 0 제품까지 나오는지, 수동 스캔이 실시간 진행 로그를 내는지, 톱니바퀴 저장 후 운영/개발 값이 각각 반영되는지, 이벤트 로그에 두 서버 `origin`이 섞여 보이는지.
