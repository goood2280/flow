# FileBrowser

FileBrowser는 DB root와 runtime cache 파일을 탐색하고, parquet/CSV schema와 sample preview로 다음 작업 대상을 고르는 화면이다.

## Owns

- DB root, root-level base/rulebook 파일 탐색
- parquet/CSV schema, row preview, column 후보 확인
- read-only SQL/filter/download preview
- 빠른 화면 표시: DB/Parquet preview와 SQL/컬럼 선택 결과는 브라우저에 최대 200행만 표시한다.
- CSV 다운로드: 화면 200행 제한과 별개로 톱니바퀴의 `csv_download_max_rows` 설정을 `max_rows`로 전달하되, 서버 허용 한도(최대 500,000행 / 100MB)를 넘지 않는다.
- 연결된 LLM을 통한 자연어 SQL 초안 작성. LLM은 SQL 입력창만 채우며 자동 실행하지 않는다.
- S3 동기화 상태와 로컬 cache 파일 접근성 확인
- **LOT 진행 최신 캐시** (`data/Fab/cache/lot_progress_latest_lot_by_root_wafer.parquet`) — FileBrowser, SplitTable, Inform, Tracker, Flow-i current-step 질의가 공유하는 현재 lot/wafer 진행 기준.

## Does Not Own

- 분석 판단, chart 생성, plan/actual 비교
- 원본 DB root 파일 생성/수정/삭제
- 대용량 join 결과를 브라우저 state에 장기 보관하는 기능
- CSV 다운로드를 200행 preview 제한에 묶는 동작

## Code Entrypoints

| Layer | Path |
|---|---|
| Frontend page | `frontend/src/pages/My_FileBrowser.jsx` |
| Backend router | `backend/routers/filebrowser.py` |
| Lot progress cache builder | `backend/core/lot_progress_cache.py` |
| API helper | `frontend/src/lib/api.js` |
| Flow-i guide | `data/flow-data/flowi_agent_features/filebrowser.md` |

## Data And Cache

- Raw DB root는 `FLOW_DB_ROOT` 또는 `data/Fab/`에서 온다.
- Runtime state와 cache는 `FLOW_DATA_ROOT` 또는 `data/flow-data/`에서 온다.
- lot progress cache는 root lot id, wafer id별 최신 lot id를 parquet로 볼 수 있어야 한다.
- cache 파일도 일반 파일처럼 목록 진입, schema 확인, preview가 가능해야 한다.
- FileBrowser 캐시 탭에서 `lot_progress_latest_lot_by_root_wafer.parquet`만 수동 재생성할 수 있다.
- LOT 진행 최신 캐시는 앱 기동 시 `lot_progress` router가 scheduler를 시작하고, `settings.json.lot_progress_refresh_minutes` 주기에 맞춰 stale 여부를 확인한다. 수동 갱신도 같은 builder를 호출한다. builder는 `FLOW_DATA_ROOT/locks/lot_progress_cache.lock` 파일락으로 공유 data root 안에서 단일 실행만 허용하고, `FLOW_DATA_ROOT/logs/lot_progress_cache_refresh.jsonl`에 성공/실패/lock skip 이력을 남긴다.
- builder는 `1.RAWDATA_DB_FAB/<product folder>/**/*.parquet`를 스캔한다. 여기서 `product` 값은 parquet/DB 컬럼이 아니라 `1.RAWDATA_DB_FAB` 바로 아래의 제품 폴더명이다. 각 row에서 `root_lot_id`, `lot_id`, `wafer_id`, `step_id`, `tkin_time`, `tkout_time`을 읽고, 없는 보조 컬럼(`process_id`, `eqp_id`, `chamber_id`, `ppid`)은 빈 값으로 둔다. 이후 `(제품 폴더명, root_lot_id, wafer_id)`별로 `tkout_time` 또는 `tkin_time`이 가장 최신인 row 하나를 고른다. `step_id`는 가능한 경우 step matching 파일로 `function_step`에 매핑하고, 최종 결과는 `product`, `root_lot_id`, `wafer_id`, `lot_id`, `step_id`, `function_step`, `tkout_time`, `update_time` 컬럼을 가진 `data/Fab/cache/lot_progress_latest_lot_by_root_wafer.parquet`로 저장한다. FileBrowser에 노출되는 캐시는 이 parquet 하나만 canonical이다.
- SplitTable 매칭 캐시, Tracker Analysis ET 후보 캐시, ET/INLINE/VM 요약 캐시는 FileBrowser 운영 캐시에서 제외한다. legacy/non-canonical cache parquet/csv/json은 목록에서 숨기고, admin이 `/api/filebrowser/cache/cleanup-candidates`와 `/api/filebrowser/cache/cleanup`으로 명시 삭제한다.
- `filebrowser_settings.json.auto_s3_upload_on_save=true`이면 base file save/text save/rollback과 LOT 진행 캐시 parquet 갱신 후 S3 artifact sync를 호출한다. 꺼져 있으면 저장은 그대로 수행하고 응답 `s3_sync.status`는 `disabled_by_filebrowser_setting`이다.
- Flow-i의 현재 step 질문은 FAB 원본 재스캔보다 `lot_progress_latest_lot_by_root_wafer.parquet`를 우선 사용해 `step_id`와 `function_step`을 답한다.
- 연결된 LLM을 통한 캐시 생성은 `lot_progress` 요청 분류에만 사용한다. 실제 파일 쓰기는 서버 handler가 수행하며 임의 경로 생성은 허용하지 않는다.
- FileBrowser LLM prompt 기본값은 `backend/core/filebrowser_agent_prompts.default.json`에 두고, setup 설치 시 `FLOW_DATA_ROOT/filebrowser_agent_prompts.json`이 없을 때만 복사한다. 운영자가 수정한 runtime prompt는 덮어쓰지 않는다.

## Preview, SQL, Download

- DB product / root parquet / base parquet 화면 preview는 최대 200행만 반환한다. UI는 pagination을 숨기고 첫 화면만 보여준다.
- SQL 실행과 컬럼 선택도 표시 결과는 최대 200행이다. 사용자는 결과가 맞는지 빠르게 확인한 뒤 CSV 다운로드를 실행한다.
- `/api/filebrowser/download-csv`는 preview row cap을 적용하지 않는다. 대신 기존 안전 한도인 `max_rows <= 500000`, `MAX_CSV_DOWNLOAD_BYTES=100MB`, wide source 컬럼 선택 요구를 따른다. FileBrowser UI는 저장된 `filebrowser_settings.json.csv_download_max_rows`를 이 `max_rows`로 보낸다.
- `POST /api/filebrowser/sql/llm/draft`는 자연어와 현재 컬럼 목록, dtype, sample values를 받아 read-only filter expression 초안만 반환한다. 응답은 `resolved_columns`, `unknown_column_terms`, `resolved_values`, `value_terms`, `warnings`를 포함해 prompt의 컬럼/값 해석 상태를 보여준다. `SELECT/FROM/DDL/DML/세미콜론/없는 컬럼`은 거부한다.
- 날짜/시간형 컬럼(`tkout_time`, `update_time`, `measure_time` 등)의 자연어 조건은 월·일·시·분·초를 보존해 quoted ISO literal(`'2024-04-20'`, `'2024-04-20T14:05:00'`)로 만든다. LLM이 `tkout_time >= 2024`처럼 연도만 남기면 초안을 거부하고 deterministic fallback으로 다시 만든다.
- `wafer_id`/`wf_id` 조건은 원본 저장 타입이 string이어도 숫자 의미로 실행한다. 예: `wafer_id = 3`, `wafer_id >= 3`, `wafer_id IN ('WF03', 10)`은 실행 전에 numeric cast filter로 정규화된다.
- AI SQL 초안은 SQL 입력창에만 반영된다. 실제 조회와 다운로드는 사용자가 별도로 실행한다.
- LLM 호출이 실패하거나 이상한 SQL을 반환하면 제한적 deterministic fallback을 사용하되, 응답의 `llm.used=false`, `fallback=true`, `warnings`로 상태를 노출한다.

## File Settings

파일 톱니바퀴의 파일 설정은 `FLOW_DATA_ROOT/filebrowser_settings.json`의 `csv_rules`에 저장된다.

- `POST /api/filebrowser/settings/llm/draft`는 저장하지 않는 `csv_rules` 초안만 만든다. 허용 key는 아래 rule schema로 제한되고, 없는 컬럼이나 unsupported key는 warning과 함께 제거된다.
- 규칙 초안은 연결된 전역 LLM(`core.llm_adapter`)을 우선 사용한다. LLM이 비어 있거나 실패해도 "전문가처럼", "가능한 규칙" 같은 prompt는 컬럼/샘플 기반 deterministic 초안을 생성한다.
- UI는 생성된 초안을 요약과 JSON으로 먼저 보여주며, `초안 적용`을 눌러 form에 반영한 뒤 `저장`을 눌러야 실제 `filebrowser_settings.json`에 저장된다.
- `conditions`: 한 줄 또는 항목마다 Polars SQL expression을 쓴다. 모든 조건이 각 row에서 참이어야 통과한다.
- `ordered_by`: 현재 CSV row 순서를 검증한다. 순서가 깨져 있으면 저장을 막는다.
- `sort`: 저장 시 같은 기준으로 실제 CSV row를 재정렬한다.

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
| `filebrowser.lot_progress.latest` | `root_lot_id`, `wafer_id?` | `step_id`, `function_step`, `lot_id`, source path | user | `root_lot_id` |
| `filebrowser.csv.rules.read` | `csv_name` | `csv_rules` 정의 (filebrowser_settings.json) | user | `csv_name` |
| `filebrowser.csv.rules.draft` | `file`, `prompt`, `columns`, `sample_rows`, `current_rule` | 저장하지 않은 `csv_rules` 초안 + warnings | manager | `file`, `prompt` |
| `filebrowser.sql.llm.draft` | `natural_language`, `columns`, `dtypes?`, `sample_rows?`, `current_sql?`, `scope?`, `root?`, `product?`, `file?` | 저장/실행하지 않은 SQL filter 초안 + 컬럼/값 후보 + warnings | user | `natural_language`, `columns` |
| `filebrowser.cache.lot_progress.refresh` | `target=lot_progress` | LOT 진행 최신 캐시 refresh 결과 + `s3_sync` | admin | `target` |
| `filebrowser.cache.lot_progress.status` | `target=lot_progress` | 마지막 성공/시도 시각, freshness, lock state, 제품 수, row 수, `interval_minutes`/`next_refresh_at` | user | `target` |
| `filebrowser.cache.llm.refresh` | `prompt`, `product?`, `source_root?`, `force?` | LLM target draft + LOT 진행 최신 캐시 refresh 결과 | admin | `prompt` |

자연어 예시 → action 매핑:
- `A1000 #21 현재 step이 어디야` → `filebrowser.lot_progress.latest` (`lot_progress_latest_lot_by_root_wafer.parquet` 우선)
- `이 csv 미리 보여줘` → `filebrowser.preview`
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
