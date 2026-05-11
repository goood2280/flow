# FileBrowser

FileBrowser는 DB root와 runtime cache 파일을 탐색하고, parquet/CSV schema와 sample preview로 다음 작업 대상을 고르는 화면이다.

## Owns

- DB root, root-level base/rulebook 파일 탐색
- parquet/CSV schema, row preview, column 후보 확인
- read-only SQL/filter/download preview
- S3 동기화 상태와 로컬 cache 파일 접근성 확인
- `data/flow-data/cache/lot_progress/lot_wf_current.parquet`처럼 runtime에서 생성된 parquet preview
- **🧩 SplitTable 매칭 캐시** (`data/flow-data/splittable/match_cache/ML_TABLE_<PRODUCT>.json`) — 목록/preview/자동 주기 갱신 상태/수동 갱신 진입점. 이전 Admin 패널을 대체.
- **🧪 Tracker Analysis ET 캐시** (`data/flow-data/tracker/...` 후보 목록 parquet/json) — 목록/preview/수동 갱신 진입점. ET 캐시는 현재 자동 스케줄을 켜지 않는다.

## Does Not Own

- 분석 판단, chart 생성, plan/actual 비교
- 원본 DB root 파일 생성/수정/삭제
- 대용량 join 결과를 브라우저 state에 장기 보관하는 기능

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
- SplitTable 매칭 캐시는 기본적으로 backend scheduler가 `settings.json.splittable_match_refresh_minutes` 주기에 맞춰 계속 갱신한다. FileBrowser 캐시 탭에서는 이 주기와 다음 예정 시각을 보여주고, admin은 별도 수동 갱신도 실행할 수 있다.
- Tracker Analysis ET 캐시는 현재 수동 갱신만 지원한다. background scheduler는 opt-in 상태로 유지한다.
- Flow-i의 현재 step 질문은 FAB 원본 재스캔보다 `lot_progress_latest_lot_by_root_wafer.parquet`를 우선 사용해 `step_id`와 `function_step`을 답한다.

## File Settings

파일 톱니바퀴의 파일 설정은 `FLOW_DATA_ROOT/filebrowser_settings.json`의 `csv_rules`에 저장된다.

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
| `filebrowser.cache.match.refresh` | `target ∈ {fab, et}` | refresh job status (FAB은 자동 주기와 별도 수동 job, ET는 수동 scan) | admin | `target` |
| `filebrowser.cache.match.status` | `target ∈ {fab, et}` | 마지막 갱신 시각, 제품 수, 진행 카운트, FAB `interval_minutes`/`next_refresh_at` | user | `target` |

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
```

환경에 DuckDB/pytest가 없으면 frontend build와 smoke를 별도로 확인한다.
