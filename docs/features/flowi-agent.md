# Flow Agent Runtime

Agent 탭은 FileBrowser AI SQL을 건드리지 않고, 별도 Agent runtime surface에서 추상 목표를 실행 가능한 상태 흐름으로 분해한다. 현재 visible Agent 표면은 두 가지다.

- `런타임 설계`: semantic layer, unit-agent orchestration, SSE status stream, final conclusion
- `LLM 연결`: 기존 LLM runtime 연결 상태와 admin 설정

기존 Wiki/schema/unit-AI 운영 endpoint는 호환을 위해 보존하지만, Agent 탭의 기본 작업면에서는 노출하지 않는다.

## Owns

- 사용자 목표를 semantic frame으로 정규화
- slot/product/lot/wafer/step/column 후보 해석
- 단위 에이전트 계획 생성
- FastAPI SSE 실시간 상태 업데이트
- LangGraph `astream` 기반 orchestration hook
- LangSmith tracing metadata와 traceable node hook
- LLM 연결 상태 표시와 선택적 최종 문장 정리

## Does Not Own

- FileBrowser AI SQL endpoint/UI 제거
- raw DB/file 직접 수정
- 관리자 확인 없는 저장성 작업
- Home Flow-i의 기존 deterministic feature routing 교체
- LLM이 라우팅/권한/저장 판단을 단독 결정하는 흐름

## Code Entrypoints

| Layer | Path |
|---|---|
| Runtime schemas | `backend/app_v2/modules/agent_runtime/schemas.py` |
| Semantic layer | `backend/app_v2/modules/agent_runtime/semantic.py` |
| LangGraph/LangSmith graph | `backend/app_v2/modules/agent_runtime/graph.py` |
| FastAPI routes | `backend/routers/agent.py` (`/api/agent/runtime/*`) |
| EventSource auth fallback | `backend/app_v2/runtime/security.py` |
| Agent page | `frontend/src/pages/My_Diagnosis.jsx` |
| Runtime UI | `frontend/src/components/agent/AgentRuntime.jsx` |
| LLM config UI | `frontend/src/components/agent/LlmTab.jsx`, `LlmCfgPanel.jsx` |

## API Contract

`GET /api/agent/runtime/blueprint`

- Returns unit-agent specs, LangGraph availability, LangSmith tracing readiness, LLM redacted status, and endpoint names.

`POST /api/agent/runtime/semantic/resolve`

```json
{
  "goal": "PRODA A1000 #21 현재 step과 KNOB 영향을 확인해줘",
  "max_terms": 32
}
```

Response includes `semantic.intent`, `semantic.slots`, `semantic.candidates`, `semantic.coverage`, `semantic.polars_profile`.

`POST /api/agent/runtime/run`

- Runs the same graph once and returns collected events plus the final conclusion as JSON.

`GET /api/agent/runtime/stream?goal=...&use_llm=false&max_terms=32`

- Server-Sent Events endpoint.
- Browser `EventSource` uses `?t=<session_token>` because custom headers are unavailable.
- Event names: `status`, `final`, `done`.

## Runtime Flow

1. `semantic_layer`
   - Tokenizes the goal.
   - Normalizes Korean/English aliases such as `루트랏`, `wafer`, `KNOB`, `AI SQL`, `LangSmith`.
   - Scores column candidates from `schema_relations.json` column catalog and existing Unit AI `ColumnDoc`.
   - Uses Polars to sort/dedupe/profile candidate evidence.
2. `task_planner`
   - Builds the unit-agent sequence from the semantic intent.
3. `unit_agents`
   - Produces read-only execution artifacts and readiness checks.
   - This is the boilerplate hook where real feature API calls can be attached.
4. `conclusion`
   - Produces missing-slot warnings, next actions, and final answer.
   - LLM is optional and only polishes the final wording when enabled and configured.

## LangGraph And LangSmith

- `backend/requirements.txt` includes `langgraph` and `langsmith`.
- If they are installed, the graph runs through LangGraph `StateGraph.astream(..., stream_mode="updates")`.
- If they are not installed in a local checkout, the same node contract runs through a deterministic async fallback so Flow remains usable.
- LangSmith tracing is activated by environment, for example:

```bash
export LANGSMITH_TRACING=true
export LANGSMITH_PROJECT=flow-agent-runtime
export LANGSMITH_API_KEY=...
```

The UI shows whether LangGraph, LangSmith, SSE, and LLM are ready without exposing secrets.

## Guardrails

- Runtime layer is read-only by default.
- LLM never decides permissions or writes.
- Semantic coverage below threshold is shown as a warning, not hidden.
- Missing product/lot/column evidence is returned in `final.missing` / `final.warnings`.
- Existing FileBrowser AI SQL endpoints stay owned by FileBrowser.

## Verify

```bash
python3 -m pytest tests/test_agent_runtime.py tests/test_feature_contracts.py -q
python3 -m py_compile backend/app_v2/modules/agent_runtime/*.py backend/routers/agent.py backend/app_v2/runtime/security.py
cd frontend && npm run build
```
