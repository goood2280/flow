# FileBrowser

FileBrowser는 DB root와 runtime cache 파일을 탐색하고, parquet/CSV schema와 sample preview로 다음 작업 대상을 고르는 화면이다.

## Owns

- DB root, root-level base/rulebook 파일 탐색
- parquet/CSV schema, row preview, column 후보 확인. FileBrowser 화면에서 DB 제품/root parquet을 처음 열면 2단계로 로드한다: ① `meta_only=true`로 스키마를 즉시 그리고("샘플 행 불러오는 중…" 표시), ② 100행 샘플을 백그라운드로 이어 받아 교체한다. SQL/SELECT/정렬/집계가 있는 조회와 페이지 이동은 기존처럼 단일 `meta_only=false` 요청이다. 응답 순서 꼬임은 요청 시퀀스 가드로 무시한다.
- read-only SQL/filter/download preview
- 빠른 화면 표시: DB/Parquet/cache preview와 SQL/컬럼 선택 결과는 브라우저에 최대 100행, 기본 컬럼 100개만 표시한다. 5000열 같은 wide schema는 `schema_column_page_size`만 응답에 싣고, 컬럼 검색은 `/api/filebrowser/columns/search`로 서버 schema에서 찾는다.
- CSV 다운로드: 화면 100행 제한과 별개로 톱니바퀴의 `csv_download_max_bytes`를 주 제한으로 사용한다. `csv_download_max_rows`는 legacy 보조 제한으로 유지하며, 서버 허용 한도(최대 500,000행 / 100MB)를 넘지 않는다.
- 연결된 LLM을 통한 자연어 SQL 초안 작성. AI SQL은 read-only SQL filter, SQL문 안 `ORDER BY`, 별도 aggregate, 명시 요청된 선택 컬럼을 초안으로 만들고 화면에서 즉시 preview 조회까지 실행한다.
- Agent 탭의 `filebrowser_ai_sql` unit은 FileBrowser AI SQL을 가져간 새 소유자가 아니라, `context_sample -> semantic_layer -> filter_draft -> column_draft -> merge -> preview_apply` 실행 흐름을 보여주는 wrapper다. SQL validation, source sampling, selected column 검증, preview 적용은 계속 FileBrowser helper와 read-only 계약을 재사용한다.
- S3 동기화 상태와 로컬 cache 파일 접근성 확인
- **LOT 진행 최신 캐시** (`data/Fab/cache/lot_progress_latest_lot_by_root_wafer.parquet`) — FileBrowser, SplitTable, Inform, Tracker, Flow-i current-step 질의가 공유하는 현재 lot/wafer 진행 기준.

## Does Not Own

- 분석 판단, chart 생성, plan/actual 비교
- 원본 DB root 파일 생성/수정/삭제
- 대용량 join 결과를 브라우저 state에 장기 보관하는 기능
- CSV 다운로드를 100행 preview 제한에 묶는 동작

## Code Entrypoints

| Layer | Path |
|---|---|
| Frontend page | `frontend/src/pages/My_FileBrowser.jsx` |
| Backend router | `backend/routers/filebrowser.py` |
| Agent unit wrapper | `backend/core/flowi_units/filebrowser_ai_sql_runtime.py`, `backend/routers/agent.py` |
| pre-pivoted SplitTable cache | `backend/app_v2/modules/splittable/cache_builder.py` |
| Lot progress cache builder | `backend/core/lot_progress_cache.py` |
| API helper | `frontend/src/lib/api.js` |
| Flow-i guide | `data/flow-data/flowi_agent_features/filebrowser.md` |

## S3 Sync Permissions

- 일반 사용자는 파일/제품 목록의 S3 신호등과 freshness 상태만 본다.
- 파일/제품 목록의 신호등은 `GET /api/s3ingest/status-by-target?include_local=0` fast 응답으로 먼저 표시한다. 응답의 `local_freshness_included=false`는 로컬 파일 최신시각 재귀 scan을 생략했다는 뜻이며, 화면은 이후 idle/5분 주기로 `include_local=1`을 호출해 freshness 텍스트만 보강한다.
- FileBrowser page manager는 `GET /api/s3ingest/items`, `GET /api/s3ingest/history`, `POST /api/s3ingest/run`으로 이미 등록된 S3 동기화 항목을 조회하고 수동 실행할 수 있다.
- 실행 중/대기 중인 항목은 `■ 중지`(`POST /api/s3ingest/stop`)로 개별 중지한다. 전송은 `subprocess.Popen`으로 실행되어 핸들을 보관하므로, 중지 요청 시 실행 중이면 프로세스를 terminate하고 대기 중이면 큐에서 제거한다. 항목 설정은 그대로 유지되어 삭제/재등록 없이 다시 실행할 수 있다. 사용자 중지는 `status="cancelled"`로 기록되고 신호등 실패 집계에서 제외된다. 항목 삭제 없이 주기 동기화만 멈추려면 `⏸ 정지`/`재개`(`POST /api/s3ingest/set-enabled`, Admin 전용)로 `enabled` 플래그를 토글한다.
- S3 항목 생성/수정/삭제, 스케줄 저장, AWS credential/profile 조회·저장·삭제는 global Admin 전용이다. 위임받은 FileBrowser manager에게는 `항목`/`이력` 탭만 보이고 `+ 추가`, `수정`, `삭제`, `AWS 설정`은 표시하지 않는다.
- S3 항목 등록 저장 실패와 실행 실패는 `history.jsonl`에 `reason`, 원문 `output_tail`, 선택적 `ai_explanation`을 남긴다. LLM 연결이 활성화되어 있으면 FileBrowser 이력 탭의 `사유` 상세에서 한국어 원인/확인 항목을 함께 보여준다.
- 주기 동기화 전역 on/off: `항목` 탭 상단 토글(Admin 전용, `GET /api/s3ingest/auto-sync`, `POST /api/s3ingest/auto-sync/save`)이 `config.json`의 `auto_download_enabled`/`auto_upload_enabled`를 저장하고, 스케줄러가 방향별로 주기 실행을 건너뛴다. 수동 `▶ 실행`/`push`는 영향받지 않는다. `FLOW_DISABLE_S3_INGEST=1` 환경변수는 해당 서버의 주기 실행 전체를 강제 종료 상태로 만들고(UI에 안내 표시), `FLOW_DISABLE_S3_SYNC=1`은 저장 시 artifact 업로드(`core/s3_sync.py`)를 서버 단위로 끈다(`s3_sync.status="disabled_env"`). 개발/양산 서버가 같은 버킷을 공유할 때 개발 서버에 두 env를 설정한다.

## Data And Cache

- Raw DB root는 `FLOW_DB_ROOT` 또는 `data/Fab/`에서 온다.
- Runtime state와 cache는 `FLOW_DATA_ROOT` 또는 `data/flow-data/`에서 온다.
- 사전 피벗된 SplitTable 캐시는 `FLOW_DB_ROOT/cache/split_table/<product>/<root_lot_id>.parquet`에 행렬 형태로 저장되어 원본 탐색을 생략하고 0.1초 내외의 응답속도를 보장한다.
- `select_cols`가 비어 있으면 identity 컬럼(`root_lot_id`, `lot_id`/`fab_lot_id`, `wafer_id`, `step_id`, `function_step`, time 후보)만 반환한다. `*`/전체 컬럼 요청은 차단하고, 없는 컬럼은 `code=unknown_column` 400으로 반환한다. 결과 row는 최대 25행이다.
- canonical cache 파일은 일반 파일처럼 목록 진입, schema 확인, 100행 샘플 preview가 가능해야 한다. cache 폴더의 CSV/Parquet을 직접 열어도 작다는 이유로 전체 읽기 경로를 타지 않는다.
- `Files`(운영 파일) 목록은 현재 폴더의 **바로 아래** 항목만 보여주고, 폴더를 클릭해 들어가면 그 안이 보인다. 하위 폴더(`cache/ml_table_lookup/...` 등)를 최상위에 평탄하게 나열하지 않는다. 최상위에 표시할 폴더는 톱니바퀴 `폴더 설정`의 "Files에 표시할 폴더"(`filebrowser_settings.json.hidden_db_dirs`, 기본 `cache`,`reformatter`)로 정하며, 목록에 적힌 폴더와 최상위 파일만 노출한다. `cache`는 항상 표시된다. DB 제품 루트/백업 폴더는 Base 목록에서 숨긴다.
- 단일 파일 편집 저장(`POST /api/filebrowser/base-file/save`)은 파일을 원자적으로 쓴 뒤 즉시 응답한다. 파생 캐시 재생성(matching CSV의 DuckDB 캐시)과 S3 artifact sync는 백그라운드 스레드로 처리해 저장 응답 지연을 없앤다(응답 `s3_sync.status="pending_background"`, `cache_rows=null`). 버전 스냅샷은 동기로 남아 저장 직후 버전 목록에 반영된다.
- 같은 canonical cache 파일은 Dashboard `+ 차트 추가` 데이터 소스 목록에서 `Cache/LOT latest`로도 노출된다. FileBrowser가 생성/갱신을 소유하고 Dashboard는 read-only `root_parquet` chart source로만 읽는다.

### LOT 진행 최신 캐시 파이프라인

`data/Fab/cache/lot_progress_latest_lot_by_root_wafer.parquet` 가 어떤 데이터에서 어떤 기준으로 만들어지는지, 어디를 고치면 동작이 바뀌는지 정리한다. 빌더 본체는 `backend/core/lot_progress_cache.py:refresh_lot_progress_cache` (라인 886–1083) 다. FileBrowser 캐시 탭에서 수동 재생성할 수 있는 파일은 이 parquet 하나뿐이다.

#### DB 의 실체와 경로

여기서 말하는 "DB" 는 RDBMS 가 아니라 **parquet 파일 트리** 다. 패턴은 `<db_root>/<source_root>/<product>/**/*.parquet`.

- `db_root` 결정 우선순위 (`backend/core/roots.py`, `backend/core/paths.py:72-78`):
  1. 환경변수 `FLOW_DB_ROOT`
  2. `admin_settings.json` 의 `data_roots.db`
  3. 프로필 기본값 (공유: `/config/work/sharedworkspace/DB`, 로컬: `<repo>/data/Fab`)
- `source_root` 결정 (`backend/core/lot_progress_cache.py:lot_progress_cache_source_root`, `lot_progress_source_root_candidates`):
  - UI "DB root" 입력값이 `settings.json.lot_progress_source_root` 에 저장된다.
  - 값이 비었거나 `auto` 면 `1.RAWDATA_DB → FAB → 1.RAWDATA_DB_FAB` 순서로 **실제 존재하는 첫 폴더** 를 자동 선택한다 (`LOT_PROGRESS_DEFAULT_SOURCE_ROOTS`, 라인 24).
  - 그래도 없고 `db_root` 자체에 product 폴더가 있으면 `db_root` 를 직접 source_root 로 쓴다.
- `product` 는 **parquet 컬럼이 아니라** source_root 바로 아래 **폴더 이름** 이다 (`backend/core/lot_progress_cache.py:968-969`). 같은 product 의 parquet 들이 그 폴더 하위 트리에 흩어져 있어도 모두 같은 product 로 묶인다.

#### 읽는 컬럼 (canonical 12개 + function_step 보조 후보)

코드 고정 canonical 이름과 실제 parquet 컬럼명을 매핑한다. 매핑은 UI "LOT 컬럼 매칭" 폼 또는 `settings.json.lot_progress_column_mapping` 에서 바꾼다. 정의는 `LOT_PROGRESS_CANONICAL_COLUMNS` (라인 31–34), 적용은 `_read_parquet_rows` (라인 660 부근).

| canonical | 기본 매핑 | 용도 |
|---|---|---|
| `root_lot_id` | `root_lot_id` | 그룹 키 1, 필수 |
| `wafer_id` | `wafer_id` | 그룹 키 2 (W01/#01 → 1 정규화), 필수 |
| `step_id` | `step_id` | 필수, function_step 매핑 입력 |
| `lot_id` | `lot_id` | 출력 |
| `process_id` | `process_id` | step matching 보조 키 |
| `tkin_time` | `tkin_time` | 최신 정렬 3순위 |
| `tkout_time` | `tkout_time` | 최신 정렬 2순위, 출력 |
| `time` | `time` | 최신 정렬 4순위 (최후) |
| `update_time` | `update_time` | 최신 정렬 1순위 |
| `eqp_id` | `eqp_id` | 보조 |
| `chamber_id` | `chamber_id` | 보조 |
| `ppid` | `ppid` | 보조 |

`function_step` 은 출력 canonical 컬럼이지만 원본 필수 컬럼은 아니다. 기본은 step matching CSV 로 `step_id` 를 매핑하고, 매핑이 없을 때 원본 parquet 의 `function_step` / `func_step` / `step_desc` 값을 fallback 으로 읽는다.

행 인정 조건: `root_lot_id`, `wafer_id`, `step_id` 가 모두 비어있지 않아야 한다 (`backend/core/lot_progress_cache.py:980-981`). 하나라도 비면 그 행은 skip.

#### "최신" 판정 로직

- **그룹 키**: `(product, LOT_WF)` — `LOT_WF = f"{root_lot_id}_{wafer_id}"` (`backend/core/lot_progress_cache.py:991, 1012`).
- **정렬 우선순위** (`_sort_time`, 라인 370–371): `update_time > tkout_time > tkin_time > time`. 행이 가진 첫 비어있지 않은 값을 **문자열 비교** 한다. 의미 있는 비교를 위해 ISO 8601 (`YYYY-MM-DDTHH:MM:SS`) 형식이라야 한다.
- **유지 규칙** (라인 1013–1015): 같은 그룹 키에서 가장 큰 시간 문자열을 가진 한 행만 남긴다.
  ```python
  if prev is None or _sort_time(item) > _sort_time(prev):
      latest[key] = item
  ```

#### 출력 parquet 스키마 (8개 컬럼)

- 출력 파일: `<db_root>/cache/lot_progress_latest_lot_by_root_wafer.parquet` (현 환경: `data/Fab/cache/lot_progress_latest_lot_by_root_wafer.parquet`).
- 코드 위치: `_write_lot_progress_parquet` (라인 525–539), `_lot_progress_parquet_rows` (라인 503–522), `filebrowser_cache_parquet_file` (라인 67–70).

| 컬럼 | 출처 |
|---|---|
| `product` | parquet 이 들어있던 폴더명 |
| `root_lot_id` | canonical |
| `wafer_id` | canonical (정규화) |
| `lot_id` | canonical |
| `step_id` | canonical |
| `function_step` | `step_id` 를 step matching CSV 로 매핑한 값, 없으면 원본 `function_step` / `func_step` / `step_desc` |
| `tkout_time` | canonical |
| `update_time` | **캐시 빌드 시각** (`state.generated_at`, 라인 519). 원본 행의 `update_time` 이 아님 — 주의. |

step matching CSV 후보 (repo 루트): `Vehicle_matching.csv`, `vehicle_matching.csv`, `step_matching.csv`, `matching_step.csv`, `step_function.csv` (`STEP_MAPPING_FILENAMES`, 라인 36–42). 매핑 키 우선순위: `(product, step_id)` → `(process_id, step_id)` → `step_id` (라인 986–989).

#### 트리거 · 스케줄 · 잠금 · 로그

| 항목 | 위치 |
|---|---|
| 자동 스케줄러 시작 | `start_lot_progress_cache_scheduler()` (라인 1365–1372). `backend/routers/lot_progress.py` 가 앱 기동 시 호출 |
| 자동 주기 | `settings.json.lot_progress_refresh_minutes` (기본 30, 1~1440 분) |
| 수동 갱신 | UI "수동 갱신" → `POST /api/filebrowser/cache/match/refresh` (`backend/routers/filebrowser.py:4529-4539`) → `_refresh_filebrowser_cache_target` → `refresh_lot_progress_cache(force=True)` |
| 빌드 본체 | `refresh_lot_progress_cache` (라인 886–1083) |
| JSON 중간 캐시 | `<flow-data>/cache/lot_progress/lot_wf_current.json` (`cache_file()`, 라인 59–60) |
| 잠금 파일 | `<flow-data>/locks/lot_progress_cache.lock` (`refresh_lock_file()`, 라인 108–111) — 공유 data root 단일 실행 보장 |
| 갱신 로그 | `<flow-data>/logs/lot_progress_cache_refresh.jsonl` (`refresh_log_file()`, 라인 114–117) |
| S3 업로드 | `filebrowser_settings.json.auto_s3_upload_on_save=true` 일 때 갱신 후 `s3_sync.sync_saved_path` 호출. 버킷/리전/프리픽스 등은 `<flow-data>/s3_sync.json` (`backend/core/s3_sync.py`) |
| AWS key/config | FileBrowser AWS 설정 탭의 access key/secret/config는 `<flow-data>/s3_ingest/aws/credentials`와 `<flow-data>/s3_ingest/aws/config`에 저장하고, `aws` CLI 실행과 boto3 업로드가 이 파일을 읽는다. |

#### 엔지니어 수정 포인트 (한 표)

| 바꾸고 싶은 것 | 1차 수단 (설정/UI, 재시작 불필요) | 2차 수단 (코드) |
|---|---|---|
| DB root 경로 | `FLOW_DB_ROOT` env, 또는 admin UI → `admin_settings.json.data_roots.db` | — |
| 어느 source_root 폴더를 쓸지 | UI "DB root" 입력 = `settings.json.lot_progress_source_root` (`auto`/빈 값 → 자동 선택) | 자동 후보 순서: `LOT_PROGRESS_DEFAULT_SOURCE_ROOTS` (라인 24) |
| parquet 의 실 컬럼명 → canonical 매핑 | UI "LOT 컬럼 매칭" 12개 입력 = `settings.json.lot_progress_column_mapping` | canonical 컬럼 자체 추가/삭제: `LOT_PROGRESS_CANONICAL_COLUMNS` (라인 31–34) |
| 자동 갱신 주기 | UI "자동 주기 분" = `settings.json.lot_progress_refresh_minutes` | 범위 한계: `CACHE_REFRESH_MINUTES_MIN/MAX` (라인 27–28) |
| S3 자동 업로드 토글 | UI "저장/캐시 갱신 후 S3 업로드" = `filebrowser_settings.json.auto_s3_upload_on_save` | S3 설정 자체: `<flow-data>/s3_sync.json` |
| AWS key/config 저장소 | UI "AWS 설정" 탭 | `<flow-data>/s3_ingest/aws/credentials`, `<flow-data>/s3_ingest/aws/config` |
| "최신" 정렬 우선순위 (시간 컬럼 순서) | — | `_sort_time` (라인 370–371) |
| 그룹 키 (product 묶음 등) | — | latest key 조립 (라인 991, 1012) |
| 행 인정 필수 필드 | — | (라인 980–981) |
| 출력 parquet 컬럼 셋 | — | `_write_lot_progress_parquet.columns` (라인 528–531), `_lot_progress_parquet_rows` (라인 503–522) |
| `step_id → function_step` 매핑 | repo 루트의 `Vehicle_matching.csv` / `step_matching.csv` / `matching_step.csv` / `step_function.csv` 편집 | 파일명 후보 자체: `STEP_MAPPING_FILENAMES` (라인 36–42) |
| product 추출 규칙 (폴더명) | — | (라인 968–969) |

#### 컨슈머와 100ms 이하 응답 체크리스트

이 캐시는 인폼 / 스플릿테이블 / Tracker(이슈추적) / Flow-i 의 현재 step 응답이 공유하는 단일 진실원이다. 컨슈머가 한 요청 안에서 100ms 안에 응답하려면 아래 두 갈래 read 경로의 차이를 알아야 한다.

**컨슈머 호출 지점**

| 화면/모듈 | 진입 함수 | 읽기 경로 |
|---|---|---|
| Tracker 행 hydrate, lot_step endpoint | `backend/routers/tracker.py` 의 `lot_progress_summary`, `lot_progress_snapshot`, `lot_id_candidates`, `compress_wafer_ids`, `upsert_tracker_lot_status_rows` import (라인 247, 273, 314, 326, 882, 1303, 1335, 1583) | `load_lot_progress_cache` 인메모리 dict |
| Tracker scheduler hydrate batch | `backend/core/tracker_scheduler.py:280, 382, 417` | 동일 |
| Tracker `lot_step` 폴백 | `backend/core/lot_step.py:_latest_fab_step_from_lot_progress_cache` (라인 79) → `tracker.py:1545` | **`<db_root>/cache/lot_progress_latest_lot_by_root_wafer.parquet` 매 호출 polars scan** |
| 인폼 product 옵션 | `backend/routers/informs.py:_lot_progress_cache_products` (라인 179) | **동일 parquet 매 호출 polars scan** |
| 스플릿테이블 매칭 캐시 빌드 후 트리거 | `backend/routers/splittable.py:5051` | `refresh_lot_progress_cache(force=...)` (write) |
| 캐시 상태 / 수동 갱신 endpoint | `backend/routers/filebrowser.py` cache_match_* | 상태 확인은 read-only JSON/memory cache만 읽고, 수동 갱신만 `refresh_lot_progress_cache`를 호출 |

**두 갈래 read 경로의 latency 양상**

- **인메모리 dict 경로** (`load_lot_progress_cache` → `lookup_lot_progress`, `lot_progress_summary` …): hot `_CACHE_STATE` dict 가 살아 있고 `age <= lot_progress_refresh_minutes`, source_root/column_mapping 일치면 즉시 반환. 본문 필터는 `state["items"]` 리스트 선형 스캔 (Python list comprehension, 라인 1155–1160). items 가 1만 행이면 한 번 호출에 수 ms, 한 페이지에서 N 개 lot 을 hydrate 하면 N 번 선형 스캔이 누적된다.
- **parquet 직접 스캔 경로** (`_latest_fab_step_from_lot_progress_cache`, `_lot_progress_cache_products`): 매 호출마다 `pl.scan_parquet` → `collect_schema()` → filter → collect. cold disk / 공유 스토리지 / 큰 parquet 에서는 한 호출만으로 100ms 초과 가능.

**100ms 이하 보장이 깨지는 지점**

1. 인메모리 dict 가 cold (서버 부팅 직후 첫 요청, 또는 30분 stale). disk JSON `lot_wf_current.json` 한 번 read+`json.loads` (수십 ms). 그래도 stale 면 풀 빌드(FAB parquet 전체 스캔, 수 초~수 분).
2. parquet 직접 스캔 경로를 쓰는 컨슈머(인폼 product 옵션 / Tracker `lot_step` 폴백)가 페이지 진입마다 polars open + schema 추출.
3. `lookup_lot_progress` 의 선형 필터를 한 화면에서 N 번 호출 (Tracker hydrate, 인폼 dashboard).
4. 운영 상태 확인 endpoint가 stale 상태를 이유로 source refresh나 cache parquet scan을 수행하면 polling만으로 heavy 작업이 누적된다. 현재 `/api/filebrowser/cache/match/status`는 이를 피하기 위해 JSON/memory cache만 읽는다.

**개선 체크리스트 (우선순위 순)**

| 순위 | 변경 | 위치 | 기대 효과 |
|---|---|---|---|
| 1 | 적용됨: `_CACHE_INDEX` 빌드: `by_product`, `by_lot_id`, `by_root_lot_id`, `by_wafer_id`, `by_lot_wf` dict. `lookup_lot_progress` / `lot_progress_summary` / `lot_id_candidates` 가 이 인덱스를 사용 | `backend/core/lot_progress_cache.py` | N×items 선형 스캔 → N개 dict lookup. 한 페이지 hydrate 합계 latency 가 자릿수 단위로 떨어짐 |
| 2 | 적용됨: parquet 직접 스캔 두 곳을 인메모리 helper 호출로 교체 — `_latest_fab_step_from_lot_progress_cache` 는 `lot_progress_snapshot(..., refresh_if_missing=False)` 으로, `_lot_progress_cache_products` 는 `list_products()` helper 로 읽음 | `backend/core/lot_step.py`, `backend/routers/informs.py`, `backend/core/lot_progress_cache.py` | 매 호출 수십~수백 ms parquet IO → memory/JSON cache lookup |
| 3 | 부분 적용: `backend/routers/lot_progress.py` import 시 `start_lot_progress_cache_scheduler()` 가 daemon thread를 시작하고 첫 loop에서 `load_lot_progress_cache()`를 호출한다. 운영 visibility는 `/api/lot-progress/status`와 latency probe로 확인 | `backend/routers/lot_progress.py`, `backend/core/lot_progress_cache.py`, `scripts/latency_budget_probe.py` | 서버 재시작 직후 첫 요청 latency 안정화와 회귀 감지 |
| 4 | JSON disk fallback 직렬화 포맷을 binary (pickle/parquet) 로 교체. JSON loads 수십 ms → 한자리 ms | `cache_file()`, `refresh_lot_progress_cache`, `load_lot_progress_cache`, `read_lot_progress_cache` | 메모리 cold 시점의 disk fall-through 비용 단축 |
| 5 | hit/miss/latency_ms/items_scanned 를 `lot_progress_cache_refresh.jsonl` 와는 별도 read 메트릭 로그로 남기기. SLA 추적과 회귀 감지 가능 | `lookup_lot_progress`, `lot_progress_summary` 진입/종료 | 100ms SLA 의 실측·회귀 알림 |
| 6 | Tracker dashboard / 인폼 product 옵션처럼 사용자별로 자주 같은 결과를 받는 endpoint 는 짧은 ETag/Cache-Control 부여 | 각 router 응답 헤더 | 같은 요청 반복은 0 ms |

체크리스트 1+2가 적용되어 hot cache 상태의 특정 lot/root/wafer 조회는 선형 scan 대신 index lookup을 탄다. 100ms SLA 위반이 보고되면 `scripts/latency_budget_probe.py`로 light endpoint를 먼저 확인하고, 남은 4·5·6 순서로 회귀 지점을 좁힌다.
- SplitTable 매칭 캐시, Tracker Analysis ET 후보 캐시, ET/INLINE/VM 요약 캐시는 FileBrowser 운영 캐시에서 제외한다. legacy/non-canonical cache parquet/csv/json은 목록에서 숨기고, FileBrowser page manager가 `/api/filebrowser/cache/cleanup-candidates`와 `/api/filebrowser/cache/cleanup`으로 명시 삭제한다.
- `filebrowser_settings.json.auto_s3_upload_on_save=true`이면 base file save/text save/rollback과 LOT 진행 캐시 parquet 갱신 후 S3 artifact sync를 호출한다. 꺼져 있으면 저장은 그대로 수행하고 응답 `s3_sync.status`는 `disabled_by_filebrowser_setting`이다.
- Flow-i의 현재 step 질문은 FAB 원본 재스캔보다 `lot_progress_latest_lot_by_root_wafer.parquet`를 우선 사용해 `step_id`와 `function_step`을 답한다.
- 연결된 LLM을 통한 캐시 생성은 `lot_progress` 요청 분류와 명시된 `source_root` 힌트 해석에만 사용한다. 명시 힌트가 없으면 FileBrowser 캐시 설정의 `settings.json.lot_progress_source_root`를 사용한다. 캐시가 없다고 LLM이 주기 실행을 만들거나 임의 경로를 생성하지 않으며, 실제 dataset 생성/파일 쓰기는 서버 builder가 수행한다.
- FileBrowser LLM prompt 기본값은 `backend/core/filebrowser_agent_prompts.default.json`에 두고, setup 설치 시 `FLOW_DATA_ROOT/filebrowser_agent_prompts.json`이 없을 때만 복사한다. 운영자가 수정한 runtime prompt는 덮어쓰지 않는다.

## Preview, SQL, Download

- DB product / root parquet / base parquet 화면 preview는 최대 100행만 반환한다. UI는 pagination을 숨기고 첫 화면만 보여준다.
- 관리용 단일 CSV/JSON/YAML/MD는 기존처럼 전체 표시 경로를 유지한다. cache 파일, `ML_TABLE_*.parquet`, 일반 대형 parquet은 lazy/DuckDB 경로로 100행과 제한된 열만 반환한다.
- 관리용 단일 CSV가 header보다 긴 row를 가져 `found more fields than defined` 형태로 스캔 실패하면 FileBrowser는 Python CSV fallback으로 header 범위까지만 복구한다. 초과 필드는 `extra_col_N` 같은 임시 컬럼으로 노출하거나 저장하지 않으며, 사용자가 그리드에서 명시적으로 열을 추가/삭제/이름 변경하면 EDM version diff는 `열 +N`, `열 -N`, 행/열 수 변화로 남긴다.
- SQL 실행과 컬럼 선택도 표시 결과는 최대 100행이다. 사용자는 조건 적용 결과가 맞는지 빠르게 확인한 뒤 CSV 다운로드를 실행한다.
- `/api/filebrowser/base-file-view`, `/api/filebrowser/view`, `/api/filebrowser/root-parquet-view`는 `meta_only=true`를 계속 지원한다. FileBrowser 화면의 첫 open은 `meta_only=true` 스키마 응답을 먼저 그리고 100행 샘플을 백그라운드 후속 요청으로 받는다(base 파일은 편집 상태 동기화 때문에 기존 단일 요청 유지). 응답에는 `meta_only`, `meta_cached`, `row_count_unknown`, `source_size`, `preview_capped`, `truncated_cols`, `requires_filter`, `query_block_reason`을 포함한다.
- `/api/filebrowser/download-csv`는 preview row cap을 적용하지 않는다. 대신 `max_bytes <= 100MB`, `max_rows <= 500000`, wide source 컬럼 선택 요구를 따른다. FileBrowser UI는 저장된 `filebrowser_settings.json.csv_download_max_bytes`를 `max_bytes`로 보내고 `csv_download_max_rows`는 보조 제한으로 보낸다.
- Home Flow-i chart raw data 다운로드(`/api/llm/flowi/chart-session/raw-data.csv`)도 같은 `csv_download_max_rows`, `csv_download_max_bytes`, wide-column guard를 사용한다. chart session에 실제 표시된 point/group row만 내보내며 원본 DB 파일을 직접 읽거나 수정하지 않는다.
- `filebrowser_settings.json`은 `csv_download_max_bytes`, `sql_query_max_source_bytes`, `preview_max_columns`, `preview_max_rows`, `schema_column_page_size`를 가진다. 큰 source가 `sql_query_max_source_bytes`를 넘고 SQL filter나 selected columns가 없으면 `filter_required`로 차단한다.
- SQL 입력창은 `SELECT col_a, col_b WHERE ... ORDER BY sort_col ASC` 형태의 표시 SQL을 받는다. 서버는 실행 직전에 `selected_columns`, read-only `where_sql`, 단일 `sort`로 분리해 기존 preview/download 계약에 적용한다. `SELECT` 절은 실제 존재하는 단순 컬럼명만 허용하고, `ORDER BY`는 단일 실제 컬럼명 + `ASC|DESC` + 선택 `NULLS FIRST|LAST`만 허용한다. 함수, alias, from/join/group by, DDL/DML, semicolon, SQL comment는 거부한다.
- 수동 SQL 입력은 `root lot id = A1000`, `wafer id = 3`처럼 공백이 있는 컬럼 별칭과 따옴표 없는 ID 값을 `root_lot_id = 'A1000'`, `wafer_id = 3` 형태로 정규화해 실행한다. 동일 정규화는 일반 preview, DuckDB preview, CSV download에 적용한다.
- `WHERE` 안에서는 문자열로 저장된 숫자/시간 컬럼 비교를 위해 `CAST(column AS DOUBLE|FLOAT|BIGINT|INTEGER|INT|DATE|TIMESTAMP|DATETIME|TIME)` 또는 `TRY_CAST(column AS ...)`를 허용한다. 서버는 실행 시 모두 `TRY_CAST` 의미로 정규화하므로 변환 실패 행은 비교에서 제외된다. 예: `CAST(value AS DOUBLE) >= 10`, `CAST(tkout_time AS TIMESTAMP) >= '2024-04-21'`. `ORDER BY CAST(...)`, 중첩 함수, 산술식, 없는 컬럼, unsupported type은 계속 거부한다.
- `POST /api/filebrowser/sql/llm/draft`는 자연어와 현재 컬럼 목록, dtype, sample values 및 `scope/root/product/file`로 서버가 직접 만든 기본 20행, 필요 시 최대 50행의 경량 `sample_profile`을 받아 read-only `where_sql`, 사용자에게 그대로 보여줄 `display_sql`, 호환용 `sort`, 별도 `aggregate`, 명시적으로 “이 열만/컬럼만” 요청된 `selected_columns`, 피드백 저장용 `draft_id`를 반환한다. 정렬 의도가 있으면 `display_sql`에 `ORDER BY`가 포함되며, 하위호환을 위해 응답의 `sql`은 계속 WHERE-only 값이다. profile은 raw row dump 대신 컬럼명/dtype, 컬럼별 대표값 최대 3개, null/blank/numeric/datetime count를 담고, wide table에서는 질문에 언급된 컬럼·선택 컬럼·식별/시간/값 후보 컬럼을 최대 80개만 우선 profiling한다. 응답은 `sample_profile.rows_sampled`, `sample_profile.columns_profiled`, `sample_profile.sampling_policy`, `resolved_columns`, `unknown_column_terms`, `resolved_values`, `value_terms`, `warnings`, `feedback_context_used`, `feedback_context`를 포함해 prompt의 컬럼/값 해석과 최근 피드백 반영 여부를 보여준다. 지원 범위를 벗어난 `ORDER BY`/세미콜론/없는 컬럼은 거부하고, 존재하지 않는 선택/정렬/집계 컬럼은 warning과 함께 제거한다.
- AI SQL에서 사용자가 `PC_LITHO step`처럼 function step 이름을 말했지만 현재 대상 파일에는 `step_id`만 있을 수 있다. 이때 서버는 Base/DB root의 단일 step matching CSV 후보(`Vehicle_matching.csv`, `step_matching.csv`, `matching_step.csv`, `step_function.csv`)를 read-only로 읽어 `function_step -> step_id`를 역산하고, `step_id = '...'` 또는 `step_id IN (...)` 필터를 초안에 넣는다. `product` 컨텍스트가 있으면 같은 product 매핑을 우선하며, 응답 `step_mapping`에 사용한 source file과 매칭 행을 남긴다.
- 정렬 의도는 SQL 입력창의 `ORDER BY`가 source of truth다. 예: `IOFF value 큰순서`는 `item_id = 'IOFF' ORDER BY value DESC` 표시 SQL로 반영하고, 내부 호환 필드 `sort: {column, direction, nulls}`도 함께 유지한다. SQL문 안 `ORDER BY`가 별도 sort 파라미터보다 우선한다.
- 집계 의도는 SQL 문자열에 `SELECT AVG(...)`를 넣지 않고 `aggregate: {function, column, group_by, alias}`로 반환하고 실행한다. 지원 함수는 `avg`, `sum`, `min`, `max`, `median`, `count`이며 원본 parquet/CSV는 수정하지 않고 read-only preview/download 결과만 가공한다.
- AI SQL 결과 박스에는 항상 optional `좋아요`/`싫어요` 피드백 버튼을 노출한다. 누르지 않아도 preview, 수정, 재실행 흐름은 막히지 않는다. `싫어요`는 선택 사유 입력을 열 수 있지만 사유는 필수가 아니다.
- `POST /api/filebrowser/sql/feedback`은 `draft_id`, `rating`, 선택 `reason`, `natural_language`, `sql`, `sort`, `selected_columns`, `columns`, `scope/root/product/file`을 받아 `FLOW_DATA_ROOT/filebrowser_ai_sql_feedback.jsonl`에 append-only로 저장한다.
- `GET /api/filebrowser/sql/history`는 같은 사용자(관리자는 전체)의 최근 AI SQL 질문 이력을 반환한다. 이력은 `FLOW_DATA_ROOT/filebrowser_ai_sql_history.jsonl`에 append-only로 저장되며 `source=filebrowser`, `source=agent_test_prompt`, `source=home_flowi_unit_ai`, `source=home_flowi_sql_draft`를 같은 목록으로 통합한다. 저장 payload는 prompt/target, answer, display_sql/where_sql, selected_columns, sort, warnings, trace/action_log 요약, preview_summary만 담고 preview row 전체는 저장하지 않는다.
- 다음 AI SQL 초안은 같은 사용자, 비슷한 컬럼셋, 비슷한 표현의 최근 `좋아요` 사례 최대 3개와 `싫어요` 사례 최대 3개를 draft context에 넣는다. 반영은 LLM prompt context와 deterministic fallback 힌트에만 사용하며 원본 DB/파일/설정은 자동 수정하지 않는다.
- 2가지 선택안 UI는 기본으로 표시하지 않는다. 같은 표현에 상반된 피드백이 누적된 낮은 빈도 상황에서만 `A안/B안`을 보여주고, 같은 사용자에게 하루 1회 이하로 제한한다. 선택하지 않아도 계속 사용할 수 있으며, 선택하면 선택 결과만 피드백으로 저장한다.
- 날짜/시간형 컬럼(`tkout_time`, `update_time`, `measure_time` 등)의 자연어 조건은 월·일·시·분·초를 보존해 quoted ISO literal(`'2024-04-20'`, `'2024-04-20T14:05:00'`)로 만든다. LLM이 `tkout_time >= 2024`처럼 연도만 남기면 초안을 거부하고 deterministic fallback으로 다시 만든다.
- `wafer_id`/`wf_id` 조건은 원본 저장 타입이 string이어도 숫자 의미로 실행한다. 예: `wafer_id = 3`, `wafer_id >= 3`, `wafer_id IN ('WF03', 10)`은 실행 전에 numeric cast filter로 정규화된다.
- AI SQL 자연어의 `#3` 같은 `#숫자` 토큰은 lot_id 문자열이 아니라 wafer 번호로 해석한다. 예: `A1000 #3 IOFF만 보고싶어`는 lot/root lot 후보 + `wafer_id = 3` + `item_id = 'IOFF'` 필터로 초안을 만든다.
- AI SQL 초안은 `display_sql`을 SQL 입력창에 반영하고 즉시 preview 조회를 실행한다. 컬럼 탭에서 컬럼명을 누르면 SQL 입력창의 `SELECT` 절이 토글되고 바로 preview를 다시 읽는다. 체크 상태는 내부 highlight/호환 요청값으로만 사용하며, 사용자가 보는 source of truth는 SQL 텍스트다.
- LLM 호출이 실패하거나 이상한 SQL을 반환하면 제한적 deterministic fallback을 사용하되, 응답의 `llm.used=false`, `fallback=true`, `warnings`로 상태를 노출한다.
- LLM JSON draft는 raw text를 그대로 믿지 않고 JSON object parse, required key validation, 1회 repair prompt를 거친다. parse 실패, schema mismatch, `HTTP 429`는 warnings/fallback으로 노출하고 Home Flow-i 응답을 중단하지 않는다.

## File Settings

파일 톱니바퀴의 파일 설정은 `FLOW_DATA_ROOT/filebrowser_settings.json`의 `csv_rules`에 저장된다.

- `POST /api/filebrowser/settings/llm/draft`는 저장하지 않는 `csv_rules` 초안만 만든다. 허용 key는 아래 rule schema로 제한되고, 없는 컬럼이나 unsupported key는 warning과 함께 제거된다.
- 규칙 초안은 연결된 전역 LLM(`core.llm_adapter`)을 우선 사용한다. LLM이 비어 있거나 실패해도 "전문가처럼", "가능한 규칙" 같은 prompt는 컬럼/샘플 기반 deterministic 초안을 생성한다.
- UI는 생성된 초안을 요약과 JSON으로 먼저 보여주며, `초안 적용`을 눌러 form에 반영한 뒤 `저장`을 눌러야 실제 `filebrowser_settings.json`에 저장된다.
- 검증로직은 저장 차단 여부를 결정한다. 허용 key는 `required_columns`, `not_empty`, `unique_keys`, `enums`, `numeric`, `date`, `regex`, `conditions`, `ordered_by`다.
- `conditions`: 한 줄 또는 항목마다 Polars SQL expression을 쓴다. 모든 조건이 각 row에서 참이어야 통과한다.
- `ordered_by`: 현재 CSV row 순서를 검증한다. 순서가 깨져 있으면 저장을 막는다.
- 정렬로직은 검증 통과 후에만 저장할 실제 CSV row 순서를 바꾼다. 허용 key는 `sort` 하나다.
- `/base-file/validate`는 검증 결과와 정렬 preview를 반환하되, 실제 저장은 `_save_base_file`에서 검증 성공 후 `sort`를 적용한다. 검증 실패 시 원본 파일은 바뀌지 않는다.

`ppid_knob.csv`는 product 없는 공용 KNOB 룰북으로 관리한다. legacy `product` 컬럼이 있어도 SplitTable 매칭 필터로 쓰지 않으며, 기본 컬럼 계약은 `feature_name`, `rule_order`, `step_desc`, `operator`, `value`, `category`다.

```json
{
  "csv_rules": {
    "ppid_knob.csv": {
      "required_columns": ["feature_name", "rule_order", "step_desc", "operator", "value", "category"],
      "not_empty": ["feature_name", "step_desc"],
      "regex": {
        "feature_name": "\\d+(?:\\.\\d+)?\\s+.+",
        "rule_order": "R\\d+|RO"
      },
      "conditions": [
        { "expr": "feature_name != ''", "message": "feature_name은 비어 있을 수 없습니다" },
        { "expr": "step_desc != ''", "message": "step_desc는 비어 있을 수 없습니다" }
      ]
    }
  }
}
```

## Agent Driver Contract

Agent 탭(Flow-i)이 FileBrowser를 driver로 호출할 때 사용하는 unit action이다. 모든 action은 `current_user` 검증을 통과해야 하며, raw DB 파일은 read-only로만 접근한다.

| action | 입력 | 출력 | 권한 | 실패 시 missing slot |
|---|---|---|---|---|
| `filebrowser.scopes` | - | scope list (DB / Files; cache files are exposed under Files/cache) | user | - |
| `filebrowser.list` | `scope`, `path?` | 디렉터리 + 파일 목록 | user | `scope` |
| `filebrowser.preview` | `scope`, `path`, `rows?`, `cols?` | schema + sample preview | user | `scope`, `path` |
| `filebrowser.lot_progress.latest` | `root_lot_id`, `wafer_id?` | `step_id`, `function_step`, `lot_id`, source path | user | `root_lot_id` |
| `filebrowser.csv.rules.read` | `csv_name` | `csv_rules` 정의 (filebrowser_settings.json) | user | `csv_name` |
| `filebrowser.csv.rules.draft` | `file`, `prompt`, `columns`, `sample_rows`, `current_rule` | 저장하지 않은 `csv_rules` 초안 + warnings | manager | `file`, `prompt` |
| `filebrowser.sql.llm.draft` | `natural_language`, `columns`, `dtypes?`, `sample_rows?`, `preferred_selected_columns?`, `current_sql?`, `scope?`, `root?`, `product?`, `file?` | 표시 SQL(`SELECT/WHERE/ORDER BY`) + where_sql + 호환 sort + aggregate + 명시 선택 컬럼 + draft_id + 서버 sample profile + 피드백 반영 카운트 + step matching 역변환 근거 + 컬럼/값 후보 + warnings | user | `natural_language`, `columns` |
| `filebrowser.sql.feedback` | `draft_id`, `rating`, `reason?`, `natural_language?`, `sql?`, `sort?`, `aggregate?`, `selected_columns?`, `columns?`, `scope/root/product/file?` | append-only 피드백 저장 결과 | user | `draft_id`, `rating` |
| `filebrowser.sql.history` | `limit?` | 최근 AI SQL 질문 이력과 대상 DB/파일, 표시 SQL | user | session |
| `filebrowser.multisource.preview` | `prompt`, `product?`, `max_rows?` | `schema_doc`/`column_catalog` 용어 해석, 실제 source 존재 확인, confirmed relation 기반 join preview + `source_ids`, `relation_ids`, `join_keys`, `filters`, `selected_columns`, `sample_rows`, `warnings` | user | confirmed relation 또는 실제 source/column |
| `filebrowser.cache.lot_progress.refresh` | `target=lot_progress`, `source_root?` | LOT 진행 최신 캐시 refresh 결과 + `s3_sync` | manager | `target` |
| `filebrowser.cache.lot_progress.status` | `target=lot_progress` | 마지막 성공/시도 시각, freshness, lock state, 제품 수, row 수, `interval_minutes`/`next_refresh_at` | user | `target` |
| `filebrowser.cache.llm.refresh` | `prompt`, `product?`, `source_root?`, `force?` | LLM target draft + LOT 진행 최신 캐시 refresh 결과 | manager | `prompt` |

자연어 예시 → action 매핑:
- `A1000 #21 현재 step이 어디야` → `filebrowser.lot_progress.latest` (`lot_progress_latest_lot_by_root_wafer.parquet` 우선)
- `이 csv 미리 보여줘` → `filebrowser.preview`
- `PRODA FAB wafer 3 조건 SQL 초안 만들어줘` → `filebrowser.sql.llm.draft` (Home에서 SQL/filter, 선택 컬럼, preview table, warnings 표시)
- `DB root에 뭐 있어` → `filebrowser.list` (`scope=db`, root path)
- `ppid_knob.csv 규칙 뭐야` → `filebrowser.csv.rules.read`

신규 라우터는 만들지 않고 기존 `backend/routers/filebrowser.py` 의 endpoint를 Agent unit action 키로 매핑한다. 권한 게이트가 누락된 endpoint(현재 `/scopes`, `/base-file/view` 등 의심)는 Agent 미션 작업 중 함께 일관화한다 (`docs/REVIEW.md` 참조).

## Verify

```bash
git diff --check
python3 -m pytest tests/test_filebrowser_sql.py tests/test_lot_progress_cache.py
python3 scripts/eval_filebrowser_ai_sql.py --live --cases 40
```

환경에 DuckDB/pytest가 없으면 frontend build와 smoke를 별도로 확인한다.
