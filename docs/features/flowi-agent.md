# Flow-i Agent

Agent 탭은 단위기능 AI 실행 흐름을 확인하고 LLM 연결 상태를 관리하는 화면이다.

## Owns

- `frontend/src/pages/My_Diagnosis.jsx`의 Agent 화면 shell
- `Flow-i`, `단위기능 AI`, `LLM 설정` 탭 구성
- Agent scoped endpoint:
  - `GET /api/agent/home-flowi/runtime/graph`
  - `GET /api/agent/home-flowi/runtime/runs`
  - `GET /api/agent/home-flowi/runtime/runs/{run_id}`
  - `GET /api/agent/unit-ai/catalog`
  - `GET /api/agent/unit-ai/filebrowser_ai_sql/runtime/graph`
  - `POST /api/agent/unit-ai/filebrowser_ai_sql/runtime/run`
- `filebrowser_ai_sql` unit의 공개 실행 trace와 LangGraph-ready DAG 가시화
- Home Flow-i 실행의 공개 runtime graph snapshot 관찰

## Does Not Own

- FileBrowser AI SQL 자체의 source of truth
- FileBrowser 화면의 SQL 입력 상태 직접 수정
- 원본 DB/CSV/Parquet 파일 write
- Home Flow-i 내부 reasoning 원문 노출
- archived Agent 전체 API 복구

## FileBrowser AI SQL Unit

`filebrowser_ai_sql`은 Agent v1의 첫 단위기능 AI다. 사용자가 Agent 탭에서 FileBrowser 대상을 고르고 자연어를 입력하면 아래 노드가 순서대로 실행된다.

1. `context_sample`
2. `semantic_layer`
3. `filter_draft`
4. `column_draft`
5. `merge`
6. `preview_apply`

`filter_draft`와 `column_draft`는 별도 LLM 호출로 분리한다. LLM이 없거나 실패하면 각 노드는 공개 가능한 warning과 fallback 상태를 trace에 남긴다. 숨은 reasoning은 API 응답에 포함하지 않는다.

`merge` 결과는 FileBrowser와 같은 표시 SQL인 `display_sql`/`sql`과 내부 실행용 `where_sql`, `selected_columns`, 호환 `sort`를 함께 노출한다. 정렬 의도는 `display_sql`의 `ORDER BY`에 들어간다. 화면의 결과 SQL 박스는 편집 가능하며 `적용`은 LLM을 다시 호출하지 않고 FileBrowser의 read-only preview endpoint를 재사용해 같은 대상에 SQL만 다시 적용한다.

Agent 화면의 단위기능 AI 탭은 상단 전체 폭에 FileBrowser AI SQL 질문 이력을 두고, 각 이력에는 작성자와 실행 시각을 함께 표시한다. 하단은 `State` / `LangGraph + Node IO` / `Test prompt` 3칸으로 나눈다. 이력을 클릭하면 answer, SQL, warning, trace/action log 요약을 먼저 보여주고, `재현` 버튼을 눌렀을 때만 prompt와 대상 DB/product 또는 단일 파일을 채운다. `debug request`에서 실제 `/runtime/run` payload를 확인할 수 있다.

단위기능 AI 탭은 실행 전에도 runtime graph endpoint의 `state_design`과 노드별 `persona`/`state_io`/공유 state/cache prompt 설계를 보여준다. 실행 후에는 같은 Node IO 패널에서 정적 설계와 실제 trace input/output, warning, duration을 함께 비교한다.

`preview_apply` 노드는 read-only preview를 검증하지만 runtime trace와 질문 이력에는 preview row 전체를 싣지 않는다. Agent 화면의 preview table은 SQL `적용`을 사용자가 누른 뒤 FileBrowser preview endpoint를 다시 호출한 결과에서만 표시한다.

## Home Flow-i Runtime Tab

Home Flow-i 응답은 기존 `/api/llm/flowi/chat` 결과를 유지하면서 `run_id`와 공개 runtime graph snapshot을 남긴다. Agent의 `Flow-i` 탭은 `data/flow-data/home_agent_runs/*.json`에 저장된 최근 실행을 읽어 `프롬프트 입력 → 용어해석 → 오케스트레이터 → 단위기능 AI MCP 후보 → 결과 정리` 그래프로 보여준다.

Snapshot에는 원본 DB row 전체나 내부 추론 원문을 저장하지 않는다. preview rows는 Home 화면 표시 수준으로 제한하고, node detail은 input/output 요약, warning, action log만 포함한다.

## Code Entrypoints

| Layer | Path |
|---|---|
| Frontend page | `frontend/src/pages/My_Diagnosis.jsx` |
| LLM settings panel | `frontend/src/components/agent/LlmTab.jsx` |
| Agent router | `backend/routers/agent.py` |
| Home runtime graph | `backend/core/home_orchestrator.py` |
| Unit registry | `backend/core/flowi_units/registry.py` |
| FileBrowser AI SQL runtime | `backend/core/flowi_units/filebrowser_ai_sql_runtime.py` |
| FileBrowser owner | `backend/routers/filebrowser.py` |

## Validation

- `python3 -m pytest tests/agent/test_filebrowser_ai_sql_runtime.py`
- `python3 -m pytest tests/test_home_orchestrator.py`
- `python3 -m pytest tests/test_filebrowser_sql.py`
- `python3 -m pytest tests/test_feature_contracts.py`
- `cd frontend && npm run build`
