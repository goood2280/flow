# Flow-i Agent

Agent 탭은 단위기능 AI 실행 흐름을 확인하고 LLM 연결 상태를 관리하는 화면이다.

## Owns

- `frontend/src/pages/My_Diagnosis.jsx`의 Agent 화면 shell
- `단위기능 AI` 탭과 `LLM 설정` 탭 구성
- Agent scoped endpoint:
  - `GET /api/agent/unit-ai/catalog`
  - `GET /api/agent/unit-ai/filebrowser_ai_sql/runtime/graph`
  - `POST /api/agent/unit-ai/filebrowser_ai_sql/runtime/run`
- `filebrowser_ai_sql` unit의 공개 실행 trace와 LangGraph-ready DAG 가시화

## Does Not Own

- FileBrowser AI SQL 자체의 source of truth
- FileBrowser 화면의 SQL 입력 상태 직접 수정
- 원본 DB/CSV/Parquet 파일 write
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

## Code Entrypoints

| Layer | Path |
|---|---|
| Frontend page | `frontend/src/pages/My_Diagnosis.jsx` |
| LLM settings panel | `frontend/src/components/agent/LlmTab.jsx` |
| Agent router | `backend/routers/agent.py` |
| Unit registry | `backend/core/flowi_units/registry.py` |
| FileBrowser AI SQL runtime | `backend/core/flowi_units/filebrowser_ai_sql_runtime.py` |
| FileBrowser owner | `backend/routers/filebrowser.py` |

## Validation

- `python3 -m pytest tests/agent/test_filebrowser_ai_sql_runtime.py`
- `python3 -m pytest tests/test_filebrowser_sql.py`
- `python3 -m pytest tests/test_feature_contracts.py`
- `cd frontend && npm run build`
