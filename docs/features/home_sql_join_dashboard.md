# Dashboard Agent Source Orchestration

`home_sql_join_dashboard`는 공개 Unit AI가 아니라 `dashboard_agent`가 Home Agent에서 source/chart 요청을 처리할 때 쓰는 내부 read-only orchestration runtime이다. 사용자는 별도 “Home SQL JOIN Dashboard”가 아니라 Dashboard Agent 결과로 차트를 보고, Home Agent가 선택한 경우에도 Home 화면에서 같은 `chart_result`가 바로 렌더링된다.

## Owns

- Runtime graph: `semantic_layer -> source_resolve -> filebrowser_sql_draft -> data_need_decision -> join_candidate_select -> join_plan_validate -> data_execute -> output_route -> dashboard_draft`
- Public Agent surface: `dashboard_agent`
- Runtime implementation: `backend/core/flowi_units/home_sql_join_dashboard_runtime.py`
- Internal metadata: `backend/core/flowi_units/home_sql_join_dashboard.py`
- Home Flow-i routing through `dashboard_agent` for mixed SQL/JOIN/chart/source prompts

## Contracts

- Source resolution prefers explicit `root/product/file`. Without explicit source, it may auto-select only a single high-confidence candidate.
- Ambiguous source, missing product, missing file, or empty axis values return `blocked/needs_input` with candidate or question payload. The runtime does not guess.
- SQL draft delegates to `filebrowser_ai_sql` and uses only FileBrowser read-only filter/projection/sort contracts.
- JOIN runs only through confirmed `schema_relations`; unconfirmed/draft relations block execution.
- Single-source chart requests skip JOIN and pass FileBrowser preview rows/columns to `dashboard_agent`.
- `dashboard_draft` delegates chart creation to `dashboard_agent`.
- `chart_result.config.source_evidence` preserves source ids, relation ids, join keys, SQL summary, and FileBrowser/Dashboard sub-traces.

## Does Not Own

- FileBrowser source schema/profile discovery
- Free-form `FROM` or `JOIN` SQL execution
- DB/CSV/Parquet writes
- Dashboard chart renderer internals
- Semantic lexicon writes

## Validation

```bash
python3 -m pytest tests/agent/test_dashboard_agent_runtime.py tests/agent/test_home_sql_join_dashboard_runtime.py
python3 -m pytest tests/agent/test_home_orchestrator_chaining.py tests/test_filebrowser_sql.py tests/test_sql_workspace.py
cd frontend && npm run build
```
