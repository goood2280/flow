# FileBrowser

FileBrowser는 DB root와 runtime cache 파일을 탐색하고, parquet/CSV schema와 sample preview로 다음 작업 대상을 고르는 화면이다.

## Owns

- DB root, root-level base/rulebook 파일 탐색
- parquet/CSV schema, row preview, column 후보 확인. FileBrowser 화면에서 파일/DB를 처음 열면 `meta_only=false`로 100행 샘플을 바로 보여준다. API의 `meta_only=true` schema-only 계약은 호환용으로 유지한다.
- read-only SQL/filter/download preview
- ML_TABLE `root_lot_id` lookup cache/API는 backend/Flow-i 호환 기능으로 유지한다. FileBrowser 화면의 기본 UX에서는 lookup 입력/실행 UI를 두지 않고 `ML_TABLE_*.parquet`도 100행 샘플 preview로 연다.
- 빠른 화면 표시: DB/Parquet/cache preview와 SQL/컬럼 선택 결과는 브라우저에 최대 100행, 기본 컬럼 100개만 표시한다. 5000열 같은 wide schema는 `schema_column_page_size`만 응답에 싣고, 컬럼 검색은 `/api/filebrowser/columns/search`로 서버 schema에서 찾는다.
- CSV 다운로드: 화면 100행 제한과 별개로 톱니바퀴의 `csv_download_max_bytes`를 주 제한으로 사용한다. `csv_download_max_rows`는 legacy 보조 제한으로 유지하며, 서버 허용 한도(최대 500,000행 / 100MB)를 넘지 않는다.
- 연결된 LLM을 통한 자연어 SQL 초안 작성. LLM은 SQL 입력창만 채우며 자동 실행하지 않는다.
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
| ML_TABLE lookup cache | `backend/core/ml_table_lookup.py` |
| Lot progress cache builder | `backend/core/lot_progress_cache.py` |
| API helper | `frontend/src/lib/api.js` |
| Flow-i guide | `data/flow-data/flowi_agent_features/filebrowser.md` |

## Data And Cache

- Raw DB root는 `FLOW_DB_ROOT` 또는 `data/Fab/`에서 온다.
- Runtime state와 cache는 `FLOW_DATA_ROOT` 또는 `data/flow-data/`에서 온다.
- ML_TABLE lookup cache는 `FLOW_DB_ROOT/cache/ml_table_lookup/<ML_TABLE_STEM>/root_lot_id=<id>/*.parquet`에 저장한다. meta는 원본 path/mtime/size, row count, total cols, root_lot_id count, schema, build time을 담고 원본 mtime/size가 바뀌면 stale로 본다.
- `POST /api/filebrowser/ml-table/lookup`은 `file` 또는 `product`, `root_lot_id`, `select_cols`, 선택 `wafer_id`를 받는다. cache가 없으면 원본 parquet을 즉시 scan하지 않고 `lookup_cache_hit=false`, `cache_status=queued|missing|running`과 빈 row를 반환하며 background build queue에 등록한다. stale cache가 있으면 `source_stale=true`를 표시하고 기존 cache로 조회하면서 rebuild를 queue한다. 이 endpoint는 호환 기능이며 FileBrowser 화면의 기본 preview 흐름에서는 호출하지 않는다.
- `select_cols`가 비어 있으면 identity 컬럼(`root_lot_id`, `lot_id`/`fab_lot_id`, `wafer_id`, `step_id`, `function_step`, time 후보)만 반환한다. `*`/전체 컬럼 요청은 차단하고, 없는 컬럼은 `code=unknown_column` 400으로 반환한다. 결과 row는 최대 25행이다.
- lot progress cache는 root lot id, wafer id별 최신 lot id를 parquet로 볼 수 있어야 한다.
- canonical cache 파일은 일반 파일처럼 목록 진입, schema 확인, 100행 샘플 preview가 가능해야 한다. cache 폴더의 CSV/Parquet을 직접 열어도 작다는 이유로 전체 읽기 경로를 타지 않는다.
- FileBrowser 캐시 탭에서 `lot_progress_latest_lot_by_root_wafer.parquet`만 수동 재생성할 수 있다.
- LOT 진행 최신 캐시는 앱 기동 시 `lot_progress` router가 scheduler를 시작하고, `settings.json.lot_progress_refresh_minutes` 주기에 맞춰 stale 여부를 확인한다. 수동 갱신도 같은 builder를 호출한다. FileBrowser 톱니바퀴의 캐시 탭에서 `settings.json.lot_progress_source_root`와 `settings.json.lot_progress_column_mapping`을 저장하면 scheduler와 수동 갱신이 해당 DB root와 컬럼 매핑을 사용한다. DB root 값이 비어 있으면 실제 존재하는 `1.RAWDATA_DB`, `FAB`, `1.RAWDATA_DB_FAB` 후보 순서로 자동 선택한다. builder는 `FLOW_DATA_ROOT/locks/lot_progress_cache.lock` 파일락으로 공유 data root 안에서 단일 실행만 허용하고, `FLOW_DATA_ROOT/logs/lot_progress_cache_refresh.jsonl`에 성공/실패/lock skip 이력을 남긴다.
- builder는 선택된 FAB root의 `<product folder>/**/*.parquet`를 스캔한다. 여기서 `product` 값은 parquet/DB 컬럼이 아니라 FAB root 바로 아래의 제품 폴더명이다. 각 row에서 설정된 컬럼 매핑을 canonical 컬럼(`root_lot_id`, `lot_id`, `wafer_id`, `step_id`, `process_id`, `tkin_time`, `tkout_time`, `time`, `update_time`, `eqp_id`, `chamber_id`, `ppid`)으로 정규화한다. 기본 매핑은 모든 canonical 컬럼이 같은 이름의 원본 컬럼을 읽는 형태다. 이후 `(제품 폴더명, root_lot_id, wafer_id)`별로 `update_time`, `tkout_time`, `tkin_time`, `time` 기준 최신 row 하나를 고른다. `step_id`는 가능한 경우 step matching 파일로 `function_step`에 매핑하고, 최종 결과는 `product`, `root_lot_id`, `wafer_id`, `lot_id`, `step_id`, `function_step`, `tkout_time`, `update_time` 컬럼을 가진 `data/Fab/cache/lot_progress_latest_lot_by_root_wafer.parquet`로 저장한다. FileBrowser에 노출되는 캐시는 이 parquet 하나만 canonical이다.
- SplitTable 매칭 캐시, Tracker Analysis ET 후보 캐시, ET/INLINE/VM 요약 캐시는 FileBrowser 운영 캐시에서 제외한다. legacy/non-canonical cache parquet/csv/json은 목록에서 숨기고, FileBrowser page manager가 `/api/filebrowser/cache/cleanup-candidates`와 `/api/filebrowser/cache/cleanup`으로 명시 삭제한다.
- `filebrowser_settings.json.auto_s3_upload_on_save=true`이면 base file save/text save/rollback과 LOT 진행 캐시 parquet 갱신 후 S3 artifact sync를 호출한다. 꺼져 있으면 저장은 그대로 수행하고 응답 `s3_sync.status`는 `disabled_by_filebrowser_setting`이다.
- Flow-i의 현재 step 질문은 FAB 원본 재스캔보다 `lot_progress_latest_lot_by_root_wafer.parquet`를 우선 사용해 `step_id`와 `function_step`을 답한다.
- 연결된 LLM을 통한 캐시 생성은 `lot_progress` 요청 분류와 명시된 `source_root` 힌트 해석에만 사용한다. 명시 힌트가 없으면 FileBrowser 캐시 설정의 `settings.json.lot_progress_source_root`를 사용한다. 캐시가 없다고 LLM이 주기 실행을 만들거나 임의 경로를 생성하지 않으며, 실제 dataset 생성/파일 쓰기는 서버 builder가 수행한다.
- FileBrowser LLM prompt 기본값은 `backend/core/filebrowser_agent_prompts.default.json`에 두고, setup 설치 시 `FLOW_DATA_ROOT/filebrowser_agent_prompts.json`이 없을 때만 복사한다. 운영자가 수정한 runtime prompt는 덮어쓰지 않는다.

## Preview, SQL, Download

- DB product / root parquet / base parquet 화면 preview는 최대 100행만 반환한다. UI는 pagination을 숨기고 첫 화면만 보여준다.
- 관리용 단일 CSV/JSON/YAML/MD는 기존처럼 전체 표시 경로를 유지한다. cache 파일, `ML_TABLE_*.parquet`, 일반 대형 parquet은 lazy/DuckDB 경로로 100행과 제한된 열만 반환한다.
- SQL 실행과 컬럼 선택도 표시 결과는 최대 100행이다. 사용자는 조건 적용 결과가 맞는지 빠르게 확인한 뒤 CSV 다운로드를 실행한다.
- `/api/filebrowser/base-file-view`, `/api/filebrowser/view`, `/api/filebrowser/root-parquet-view`는 `meta_only=true`를 계속 지원한다. FileBrowser 화면의 첫 open은 `meta_only=false`이며 100행 샘플을 바로 요청한다. 응답에는 `meta_only`, `meta_cached`, `row_count_unknown`, `source_size`, `preview_capped`, `truncated_cols`, `requires_filter`, `query_block_reason`을 포함한다.
- `/api/filebrowser/download-csv`는 preview row cap을 적용하지 않는다. 대신 `max_bytes <= 100MB`, `max_rows <= 500000`, wide source 컬럼 선택 요구를 따른다. FileBrowser UI는 저장된 `filebrowser_settings.json.csv_download_max_bytes`를 `max_bytes`로 보내고 `csv_download_max_rows`는 보조 제한으로 보낸다.
- `filebrowser_settings.json`은 `csv_download_max_bytes`, `sql_query_max_source_bytes`, `preview_max_columns`, `preview_max_rows`, `schema_column_page_size`를 가진다. 큰 source가 `sql_query_max_source_bytes`를 넘고 SQL filter나 selected columns가 없으면 `filter_required`로 차단한다.
- SQL/filter는 read-only WHERE expression만 허용한다. `SELECT/FROM`, DDL/DML, semicolon, SQL comment는 `invalid_filter`로 거부한다.
- `POST /api/filebrowser/sql/llm/draft`는 자연어와 현재 컬럼 목록, dtype, sample values 및 `scope/root/product/file`로 서버가 직접 만든 최대 200행 `sample_profile`을 받아 read-only filter expression 초안과 `selected_columns`를 반환한다. 응답은 `resolved_columns`, `unknown_column_terms`, `resolved_values`, `value_terms`, `warnings`를 포함해 prompt의 컬럼/값 해석 상태를 보여준다. `SELECT/FROM/DDL/DML/세미콜론/없는 컬럼`은 거부하고, 존재하지 않는 선택 컬럼은 warning과 함께 제거한다.
- 날짜/시간형 컬럼(`tkout_time`, `update_time`, `measure_time` 등)의 자연어 조건은 월·일·시·분·초를 보존해 quoted ISO literal(`'2024-04-20'`, `'2024-04-20T14:05:00'`)로 만든다. LLM이 `tkout_time >= 2024`처럼 연도만 남기면 초안을 거부하고 deterministic fallback으로 다시 만든다.
- `wafer_id`/`wf_id` 조건은 원본 저장 타입이 string이어도 숫자 의미로 실행한다. 예: `wafer_id = 3`, `wafer_id >= 3`, `wafer_id IN ('WF03', 10)`은 실행 전에 numeric cast filter로 정규화된다.
- AI SQL 초안은 SQL 입력창과 컬럼 체크 상태에 반영되며 같은 값으로 즉시 preview 조회를 실행한다. 실행 후에도 SQL식과 선택 컬럼은 화면에 남아 사용자가 수정할 수 있다.
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

`ppid_knob.csv` 예시는 product 오름차순, 같은 product 안에서 `feature_name` 앞 숫자 오름차순, 같은 feature 안에서 `R1`, `R2`, `R3`, ..., `RO` 순서를 적용한다.

```json
{
  "csv_rules": {
    "ppid_knob.csv": {
      "required_columns": ["product", "feature_name", "function_step", "rule_order", "operator", "category"],
      "not_empty": ["product", "feature_name", "function_step", "rule_order", "operator", "category"],
      "enums": { "operator": ["eq"] },
      "regex": {
        "feature_name": "\\d+(?:\\.\\d+)?\\s+.+",
        "rule_order": "R\\d+|RO"
      },
      "conditions": [
        { "expr": "product != ''", "message": "product는 비어 있을 수 없습니다" },
        { "expr": "feature_name != ''", "message": "feature_name은 비어 있을 수 없습니다" }
      ],
      "ordered_by": {
        "keys": [
          { "column": "product", "direction": "asc", "type": "string", "nulls": "last" },
          { "column": "feature_name", "direction": "asc", "type": "leading_number", "nulls": "last" },
          { "column": "rule_order", "direction": "asc", "type": "rule_order", "nulls": "last" }
        ]
      },
      "sort": [
        { "column": "product", "direction": "asc", "type": "string", "nulls": "last" },
        { "column": "feature_name", "direction": "asc", "type": "leading_number", "nulls": "last" },
        { "column": "rule_order", "direction": "asc", "type": "rule_order", "nulls": "last" }
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
| `filebrowser.ml_table.lookup` | `file?`, `product?`, `root_lot_id`, `select_cols?`, `wafer_id?` | 최대 25행의 선택 컬럼 + `lookup_cache_hit`, `cache_status`, `source_stale` | user | `root_lot_id` |
| `filebrowser.lot_progress.latest` | `root_lot_id`, `wafer_id?` | `step_id`, `function_step`, `lot_id`, source path | user | `root_lot_id` |
| `filebrowser.csv.rules.read` | `csv_name` | `csv_rules` 정의 (filebrowser_settings.json) | user | `csv_name` |
| `filebrowser.csv.rules.draft` | `file`, `prompt`, `columns`, `sample_rows`, `current_rule` | 저장하지 않은 `csv_rules` 초안 + warnings | manager | `file`, `prompt` |
| `filebrowser.sql.llm.draft` | `natural_language`, `columns`, `dtypes?`, `sample_rows?`, `preferred_selected_columns?`, `current_sql?`, `scope?`, `root?`, `product?`, `file?` | SQL filter 초안 + 선택 컬럼 + 서버 sample profile + 컬럼/값 후보 + warnings | user | `natural_language`, `columns` |
| `filebrowser.cache.lot_progress.refresh` | `target=lot_progress`, `source_root?` | LOT 진행 최신 캐시 refresh 결과 + `s3_sync` | manager | `target` |
| `filebrowser.cache.lot_progress.status` | `target=lot_progress` | 마지막 성공/시도 시각, freshness, lock state, 제품 수, row 수, `interval_minutes`/`next_refresh_at` | user | `target` |
| `filebrowser.cache.llm.refresh` | `prompt`, `product?`, `source_root?`, `force?` | LLM target draft + LOT 진행 최신 캐시 refresh 결과 | manager | `prompt` |

자연어 예시 → action 매핑:
- `A1000 #21 현재 step이 어디야` → `filebrowser.lot_progress.latest` (`lot_progress_latest_lot_by_root_wafer.parquet` 우선)
- `이 csv 미리 보여줘` → `filebrowser.preview`
- `PRODA A1000 KNOB_ALPHA 보여줘` → `filebrowser.ml_table.lookup` (`ML_TABLE_PRODA.parquet` lookup cache 우선)
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
