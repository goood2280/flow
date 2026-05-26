# Flow Agent Runtime

Agent 탭은 FileBrowser AI SQL을 건드리지 않고, 별도 Agent surface에서 질문 처리 품질을 운영한다. 전역 기타 메뉴에는 legacy `AI 허브`/`SQL 작업대`를 별도 탭으로 노출하지 않고, 현재 visible Agent 표면은 세 가지 상단 흐름으로 묶는다.

- `운영 보드`: 질문 선택/입력 -> 처리 흐름 확인 -> 개선할 지식/워크플로우 제안 흐름으로 운영한다.
- `Agent 관리`: 운영 가이드, 질문/워크플로우, 용어/기능 AI, Wiki 근거, 검토 큐, 변경 이력을 좌측 섹션으로 묶어 관리한다.
- `설정`: 기존 LLM runtime 연결 상태와 admin 설정을 확인한다.

기본 운영 보드는 공개 가능한 `질문 -> 단어 해석 -> 계획 -> 도구 실행 -> 결과`를 상단에 유지하고, 기존 `/api/ai-hub/workflow-map`의 nodes/edges로 만든 연결 지도를 바로 보여준다. 선택 노드 상세는 입력/출력 edge, Wiki/schema 근거, 다음 개선 액션을 함께 노출한다. `semantic`, SSE/LangSmith 관련 상태, raw `plan/results/events`, 보조 Wiki 관계 표는 `기술 상세 및 원본` 접힘 영역에서 확인한다.

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
| Runtime action registry | `backend/app_v2/modules/agent_runtime/actions.py` |
| Semantic layer | `backend/app_v2/modules/agent_runtime/semantic.py` |
| LangGraph/LangSmith graph | `backend/app_v2/modules/agent_runtime/graph.py` |
| FastAPI routes | `backend/routers/agent.py` (`/api/agent/runtime/*`) |
| EventSource auth fallback | `backend/app_v2/runtime/security.py` |
| Agent page | `frontend/src/pages/My_Diagnosis.jsx` |
| Question design UI | `frontend/src/components/agent/QuestionDesignTab.jsx` |
| Design/knowledge merged UI | `frontend/src/components/agent/AgentManageTab.jsx` |
| Unit AI / semantic UI | `frontend/src/components/agent/AgentV2.jsx`, `SemanticLayerTab.jsx`, `WorkflowsTab.jsx` |
| Knowledge overview UI | `frontend/src/components/agent/KnowledgeOverviewTab.jsx` |
| Wiki graph UI | `frontend/src/components/agent/WikiTab.jsx` |
| Operating board UI | `frontend/src/components/agent/AgentStudioTab.jsx` |
| Folded runtime detail UI | `frontend/src/components/agent/AgentRuntime.jsx` |
| LLM config UI | `frontend/src/components/agent/LlmTab.jsx`, `LlmCfgPanel.jsx` |

## API Contract

`GET /api/agent/runtime/blueprint`

- Returns unit-agent specs, registered unit actions, action policies, workflow integration status, LangGraph availability, LangSmith tracing readiness, LLM redacted status, and endpoint names.

`POST /api/agent/runtime/semantic/resolve`

```json
{
  "goal": "PRODA A1000 #21 현재 step과 KNOB 영향을 확인해줘",
  "max_terms": 32
}
```

Response includes `semantic.intent`, `semantic.slots`, `semantic.candidates`, `semantic.coverage`, `semantic.polars_profile`.

`POST /api/agent/runtime/run`

- Runs the same graph once and returns collected events plus `semantic`, `plan`, `results`, and final conclusion as JSON.
- `plan[]` includes `unit_ai`, `action`, `policy`, `approval_required`, `endpoint`, `missing_slots`, and `evidence_refs`.
- `results[]` includes `handled`, `guardrail`, `tool`, `table`, `chart_result`, and `warnings`.

`GET /api/agent/runtime/stream?goal=...&use_llm=false&max_terms=32`

- Server-Sent Events endpoint.
- Browser `EventSource` uses `?t=<session_token>` because custom headers are unavailable.
- Event names: `status`, `final`, `done`.

`GET /api/agent/knowledge/overview?q=&kind=&limit=`

- Returns `counts`, `recent_items`, `pending_semantic_proposals`, `recent_wiki_pages`, `recent_wiki_sources`, `recent_prompt_history`, `recent_knowledge_events`, and `recent_semantic_changes`.
- Reads existing stores only: Knowledge Vault, semantic proposal queue, semantic changes, `flowi_activity.jsonl`, and knowledge inventory.
- `kind` can focus broad sections such as `semantic_proposal`, `wiki_page`, `wiki_source`, `prompt_history`, `knowledge_event`, or a knowledge inventory kind.

`GET /api/agent/prompt-history?limit=100&scope=mine|all&user=`

- Agent 운영 보드의 질문 큐가 읽는 질문 처리 이력이다.
- 일반 유저는 항상 본인 질문만 본다.
- admin은 `scope=all`로 user/admin 전체 질문 큐를 볼 수 있고, `user=<username>`으로 특정 사용자만 볼 수 있다.
- row는 `user`, `actor_role`, `actor_type`, `prompt`, `feature`, `intent`, `action`, `status`, `missing`, `answer_excerpt`, `elapsed_ms`, `source_ai`, `client_run_id`를 포함한다.

`GET /api/ai-hub/wiki-health?limit=12`

- AI Hub의 `Agent Wiki 상태` 패널이 읽는 운영 요약이다.
- 기존 Knowledge Vault/Agent Wiki store만 읽어 page/source count, graph count, Wiki lint count, recent pages/sources/log를 반환한다.
- Agent Wiki page/source 저장, lint 실행 권한, ingest commit 권한은 기존 `/api/agent/wiki/*` 계약을 그대로 따른다.

`GET /api/knowledge/wiki/graph?view=curated|full`

- Agent Wiki graph가 읽는 Knowledge Vault graph다.
- 기본 `curated` view는 Wiki 문서와 승인된 schema/doc 근거 edge만 반환한다.
- `full` view는 ontology, event, product/lot/wafer 자동 edge까지 포함한 raw graph를 관리자 디버깅용으로 유지한다.
- Agent 관리 > Wiki 근거의 기본 `Vault` 화면은 Obsidian식으로 문서 목록, markdown 본문, backlinks, source refs, metadata를 같은 화면에서 보여준다.
- `scripts/cleanup_runtime_wiki.py --apply`는 실행 전 `data/flow-data/backups/wiki_cleanup_*`에 백업한 뒤 runtime Wiki markdown 전체를 비운다. `--mode selected`는 legacy/demo/internal 문서만 정리하는 보수 모드다.

`GET /api/ai-hub/ops-snapshot?days=30&limit=8`

- AI Hub 첫 화면의 `운영 스냅샷` 패널이 읽는 일일 운영 요약이다.
- 기존 readiness, workflow runbook, workflow map, deep-eval report, Agent Wiki health, 운영 timeline을 집계해 summary card, 상위 개선 항목, Runbook 조치 큐, workflow map 경고, 최근 이벤트, Obsidian/n8n export 링크를 반환한다.
- 운영 보드 항목 제목은 관련 관리 위치로 focus한다. 비활성 도구는 도구 카탈로그, workflow template/run은 Runbook row, skill 후보는 스킬 패널, semantic 제안은 운영 타임라인 semantic filter로 이동한다.
- 화면에서는 summary card를 해당 운영 패널 열기로 연결하고, 상위 개선 항목은 `운영 준비도` 백로그 focus로, Runbook 조치 큐는 `Workflow Runbook` issue filter로, workflow map 경고는 `워크플로우 지도` 패널로, 최근 이벤트는 `운영 타임라인` category filter로 연결한다.
- 운영 타임라인 이벤트 제목은 category에 맞는 관리 패널로 focus한다. workflow는 Runbook, tool은 도구 카탈로그, wiki는 Agent Wiki 상태, validation은 Agent 검증 리포트, skill은 스킬 패널로 이동한다.
- workflow map 경고는 `action`과 `route`를 포함해 Wiki/source 연결, workflow step 수정, deep-eval 재검증 같은 다음 조치를 화면의 스냅샷/지도 경고 큐에 함께 표시하고, route에 맞는 관리 패널 focus로 이어진다.
- 워크플로우 지도는 node 검색과 type filter로 id/label/detail/type/tag 또는 workflow/step/tool/wiki/graph/schema/deep-eval 영역을 찾아 결과를 바로 선택하고 stage column을 matching 노드로 좁혀 보여준다.
- 워크플로우 지도 노드 detail의 입력/출력 엣지는 연결된 workflow step, tool, Wiki/schema 노드로 바로 이동한다.
- 워크플로우 지도에서 tool 노드를 선택하면 `도구 상세` 버튼으로 AI Hub 도구 카탈로그 카드와 최근 호출 이력을 바로 확인한다.
- AI Hub 도구 카탈로그 상세의 Wiki/Graph refs를 선택하면 같은 워크플로우 지도 evidence 노드로 이동해 Obsidian식 관계를 이어서 탐색한다.
- 워크플로우 지도에서 wiki/schema evidence 노드를 선택하면 `Wiki 상태` 버튼으로 Agent Wiki 운영 패널을 열어 graph/lint/source 상태를 이어서 확인할 수 있다.
- 워크플로우 지도에서 workflow 노드를 선택하면 `Runbook` 버튼으로 같은 workflow row를 `workflow_key` filter와 상세 펼침 상태로 바로 열 수 있다.
- 운영 export는 같은 workflow map 경고를 Obsidian `operations/workflow-map-warnings.md` note와 n8n `ops:workflow_warnings` sticky note에 포함한다.
- 새 runtime state를 만들지 않고 각 원천 API의 읽기 전용 builder만 호출한다.

`GET /api/ai-hub/workflow-runbook?days=30&limit=40&focus_tag=&status=&issue=&workflow_key=`

- AI Hub의 `Workflow Runbook` 패널이 읽는 workflow별 관리 표다.
- 기존 workflow map에서 workflow/template node, step, tool, evidence edge, 최근 dry-run/execute 이력을 정규화한다.
- row는 `ready`, `attention`, `blocked` 상태, issue 목록, issue별 `next_actions[]`를 포함한다. `Dry-run` action은 기존 `/api/agent/workflows/execute`를 호출한다.
- 화면의 row `상세`는 step, bind/fixed slot, evidence node, missing/disabled tool, next action route를 펼쳐 보여주고, row `지도`는 같은 workflow의 `workflow:<key>` 노드를 워크플로우 지도에서 바로 focus한다.
- Runbook 상세에서 step 텍스트는 `workflow_step:<key>:<n>` 지도 노드로, evidence tag는 해당 evidence 지도 노드로, `도구`/missing/disabled tool 버튼은 AI Hub 도구 카탈로그 상세 또는 검색으로 이어진다.
- Runbook next action은 자동 실행하지 않고 route/key에 따라 도구 카탈로그, 워크플로우 지도, Wiki 상태, DeepEval, 또는 해당 Runbook row focus로 이동한다.
- `next_action_queue[]`는 현재 필터 결과에서 같은 조치를 요구하는 workflow를 묶어 AI Hub 화면과 Obsidian/n8n export에 운영 큐로 노출한다.
- `status`, `issue`, `workflow_key` filter로 운영자가 blocked workflow, `missing_tools`/`not_checked`/`no_evidence` 개선 대상, 또는 지도에서 선택한 특정 workflow만 좁혀 볼 수 있다.
- workflow template이 0건이면 admin용 `시작 템플릿 생성` action을 내려 기존 `/api/ai-hub/readiness/bootstrap-workflows`로 starter workflow를 만들 수 있게 한다.

`POST /api/agent/workflows/test` and `POST /api/agent/workflows/execute`

- Used by `질문 설계` to show matched workflow and dry-run step results for the current prompt.
- Returns the same runtime `semantic`, `runtime_plan`, and `guardrail` shape used by `/api/agent/runtime/run`.
- `POST /api/agent/workflows` saves the template shape `{key,title,trigger,steps,shared}`.
- `shared=true` and shared-template updates require admin or diagnosis/agent/knowledge page-manager rights. Personal templates can only be updated by their owner or a manager.

## Runtime Flow

1. `semantic_layer`
   - Tokenizes the goal.
   - Normalizes Korean/English aliases such as `루트랏`, `wafer`, `KNOB`, `AI SQL`, `LangSmith`.
   - Scores column candidates from `schema_relations.json` column catalog and existing Unit AI `ColumnDoc`.
   - Uses Polars to sort/dedupe/profile candidate evidence.
2. `task_planner`
   - Builds the unit action plan from semantic intent plus any matching workflow template.
   - Applies `read_only`, `write_requires_approval`, or `blocked` policy from the action registry.
3. `unit_agents`
   - Executes `read_only` plans through existing `core.flowi_units.dispatcher.try_dispatch`.
   - Returns `approval_required` proposals for write-like app actions without running them.
   - Blocks raw DB/file direct write plans.
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
- Semantic proposal decisions and lexicon edits are written to `activity.jsonl` as `semantic:*` AI Hub events so the management timeline can show who changed the Agent vocabulary.

## Verify

```bash
python3 -m pytest tests/agent/test_agent_endpoints.py tests/test_agent_runtime.py tests/test_feature_contracts.py -q
python3 -m py_compile backend/routers/agent.py backend/core/flowi_workflow_templates.py backend/app_v2/modules/agent_runtime/*.py
python3 scripts/flowi_agent_deep_eval.py
cd frontend && npm run build
```

`scripts/flowi_agent_deep_eval.py`는 서버 없이 직접 실행한다. `step_id -> function_step`, `KNOB/PPID -> lot_wf`, `lot_wf`, `raw DB SQL join` 같은 한국어/영어 표현을 semantic resolver로 확인하고, `agent_deep_eval_semiconductor_terms` Agent Wiki 문서를 upsert/search한 뒤, in-memory SQL Workspace의 `fab_db + step_map_db + split_db + et_db + tracker_db + inform_db` 조인 정답을 검증한다. 운영 점검 결과를 보존해야 하면 `--report-json` 을 붙여 AI Hub가 읽는 `data_root/reports/flowi_agent_deep_eval_latest.json` 에 semantic/knowledge/sql/meta 그룹별 통과 수와 전체 assertion 목록을 JSON으로 남긴다.
