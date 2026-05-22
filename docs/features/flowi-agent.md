# Flow Agent Runtime

Agent 탭은 FileBrowser AI SQL을 건드리지 않고, 별도 Agent surface에서 질문 처리 설계, 시멘틱/위키 운영, 누적 지식 현황, runtime trace를 다룬다. 현재 visible Agent 표면은 다섯 가지다.

- `질문 설계`: 사용자 질문을 semantic resolve, workflow match, dry-run step으로 분해하고 개인 workflow 초안으로 저장
- `기능 AI/시멘틱`: 단위 기능 AI, semantic alias/intent, workflow template, Agent Wiki, proposal queue 운영
- `누적 지식`: Wiki page/source, semantic proposal/change, prompt trace, knowledge event, knowledge inventory 조회
- `런타임 추적`: semantic layer, unit-agent orchestration, SSE status stream, final conclusion
- `LLM 연결`: 기존 LLM runtime 연결 상태와 admin 설정

권한 모델은 초안+승인 방식이다. 일반 유저는 개인 workflow template을 저장하고, shared workflow/semantic alias/intent/maintained wiki 반영은 admin 또는 diagnosis/agent/knowledge page manager만 수행한다.

## Owns

- 사용자 목표를 semantic frame으로 정규화
- slot/product/lot/wafer/step/column 후보 해석
- 단위 에이전트 계획 생성
- FastAPI SSE 실시간 상태 업데이트
- LangGraph `astream` 기반 orchestration hook
- LangSmith tracing metadata와 traceable node hook
- LLM 연결 상태 표시와 선택적 최종 문장 정리
- Agent Wiki source/page, semantic proposal/change, prompt trace, knowledge event의 read-only overview

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
| Question design UI | `frontend/src/components/agent/QuestionDesignTab.jsx` |
| Unit AI / semantic UI | `frontend/src/components/agent/AgentV2.jsx`, `SemanticLayerTab.jsx`, `WorkflowsTab.jsx` |
| Knowledge overview UI | `frontend/src/components/agent/KnowledgeOverviewTab.jsx` |
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

`GET /api/agent/knowledge/overview?q=&kind=&limit=`

- Returns `counts`, `recent_items`, `pending_semantic_proposals`, `recent_wiki_pages`, `recent_wiki_sources`, `recent_prompt_history`, `recent_knowledge_events`, and `recent_semantic_changes`.
- Reads existing stores only: Knowledge Vault, semantic proposal queue, semantic changes, `flowi_activity.jsonl`, and knowledge inventory.
- `kind` can focus broad sections such as `semantic_proposal`, `wiki_page`, `wiki_source`, `prompt_history`, `knowledge_event`, or a knowledge inventory kind.

`POST /api/agent/workflows/test` and `POST /api/agent/workflows/execute`

- Used by `질문 설계` to show matched workflow and dry-run step results for the current prompt.
- `POST /api/agent/workflows` saves the template shape `{key,title,trigger,steps,shared}`.
- `shared=true` and shared-template updates require admin or diagnosis/agent/knowledge page-manager rights. Personal templates can only be updated by their owner or a manager.

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

## LLM Profile (P1)

Flow는 사내 GPT OSS 120B를 기본 LLM 백본으로 쓴다. dev/외부 환경은 Gemini Flash 또는 OpenAI mini로 fallback한다.

| Preset key | 용도 | provider | model | auth_mode | timeout_s |
|---|---|---|---|---|---|
| `gpt_oss_120b_internal` | 사내 운영 | `openai_compatible` | `gpt-oss-120b` | `bearer` | 60 |
| `dev_gemini_flash` | dev fallback | `vertex_gemini` | `google/gemini-2.5-flash` | `google_adc` | 30 |
| `dev_openai_mini` | dev fallback | `openai` | `gpt-5.4-mini` | `bearer` | 20 |

각 preset은 `GET /api/admin/llm/presets`로 메타데이터만 노출하고 (api_url/admin_token은 NEVER 포함), admin이 LLM 패널에서 preset을 고르면 provider/model/format/auth_mode/timeout이 자동 채워진다. 실제 endpoint URL과 token은 admin이 직접 입력해서 `POST /api/admin/settings/save`로 저장한다 — `admin_settings.json.llm` (active) + `admin_settings.json.llm_profiles[<provider>]` (per-provider 저장본) 양쪽에 반영된다.

저장된 프로필 키 목록은 `llm_adapter.list_profiles()` 또는 admin 패널의 `llm_profiles` dict로 확인한다. token rotation은 같은 preset을 다시 골라 admin_token만 갱신 → 저장.

401 fallback: bearer 토큰이 만료된 vertex Gemini OpenAI-호환 endpoint는 `_raw_config()`가 자동으로 `auth_mode="google_adc"`로 강제하고 admin_token을 무시한다 (`llm_adapter._is_vertex_openai_compatible_config`).

## Guardrails

- Runtime layer is read-only by default.
- LLM never decides permissions or writes.
- Semantic coverage below threshold is shown as a warning, not hidden.
- Missing product/lot/column evidence is returned in `final.missing` / `final.warnings`.
- Existing FileBrowser AI SQL endpoints stay owned by FileBrowser.
- General users create personal drafts/proposals/sources; maintained shared knowledge is applied through existing approval or wiki commit APIs.

## Verify

```bash
python3 -m pytest tests/agent/test_agent_endpoints.py tests/test_agent_runtime.py tests/test_feature_contracts.py -q
python3 -m py_compile backend/routers/agent.py backend/core/flowi_workflow_templates.py backend/app_v2/modules/agent_runtime/*.py
cd frontend && npm run build
```
