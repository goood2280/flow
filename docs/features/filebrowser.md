# FileBrowser

FileBrowser는 DB root와 runtime cache 파일을 탐색하고, parquet/CSV schema와 sample preview로 다음 작업 대상을 고르는 화면이다.

## Owns

- DB root, root-level base/rulebook 파일 탐색
- parquet/CSV schema, row preview, column 후보 확인
- read-only SQL/filter/download preview
- S3 동기화 상태와 로컬 cache 파일 접근성 확인
- `data/flow-data/cache/lot_progress/lot_wf_current.parquet`처럼 runtime에서 생성된 parquet preview

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

## Verify

```bash
git diff --check
python -m pytest tests/test_filebrowser_sql.py tests/test_lot_progress_cache.py
```

환경에 DuckDB/pytest가 없으면 frontend build와 smoke를 별도로 확인한다.
