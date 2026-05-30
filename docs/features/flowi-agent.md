# Flow-i Agent

Agent 탭은 단위기능 AI 실행 흐름을 확인하고 LLM 연결 상태를 관리하는 화면이다.

## Owns

- `frontend/src/pages/My_Diagnosis.jsx`의 Agent 화면 shell
- `Flow-i`, `Semantic layer`, `단위기능 AI`, `LLM 설정` 탭 구성
- LLM 연결 시 공통 API 에러를 사용자용 설명으로 바꾸는 보조 endpoint:
  - `POST /api/llm/error/explain`
- Agent scoped endpoint:
  - `GET /api/agent/catalog`
  - `POST /api/agent/unit/{key}/run`
  - `GET /api/agent/unit/{key}/graph`
  - `GET /api/agent/unit/{key}/history`
  - `GET /api/agent/home-flowi/runtime/graph`
  - `GET /api/agent/home-flowi/runtime/runs`
  - `GET /api/agent/home-flowi/runtime/runs/{run_id}`
  - `GET /api/agent/unit-ai/catalog`
  - `GET /api/agent/unit-ai/filebrowser_ai_sql/runtime/graph`
  - `POST /api/agent/unit-ai/filebrowser_ai_sql/runtime/run`
  - `GET /api/agent/unit-ai/{unit_key}/runtime/graph`
  - `POST /api/agent/unit-ai/{unit_key}/runtime/run`
  - `GET /api/agent/unit-ai/{unit_key}/runtime/history`
  - `GET /api/agent/unit-ai/change_management/runtime/graph`
  - `POST /api/agent/unit-ai/change_management/runtime/run`
  - `GET /api/agent/unit-ai/change_management/runtime/history`
  - `GET /api/agent/unit-ai/dashboard_agent/runtime/graph`
  - `POST /api/agent/unit-ai/dashboard_agent/runtime/run`
  - `GET /api/agent/unit-ai/{unit_key}/feedback-profile`
  - `POST /api/agent/unit-ai/{unit_key}/feedback`
  - `GET/PUT /api/agent/unit-ai/{unit_key}/runtime/overrides` (호환용 backend API)
  - `GET /api/agent/semantic/lexicon`
  - `PUT/DELETE /api/agent/semantic/alias-groups/{canonical}`
  - `PUT/DELETE /api/agent/semantic/intent-hints/{intent}`
  - `GET /api/agent/semantic/proposals`
  - `POST /api/agent/semantic/proposals/{id}/decision`
  - `POST /api/agent/semantic/draft`
- `filebrowser_ai_sql` unit의 공개 실행 trace와 LangGraph-ready DAG 가시화
- `inform_registration` unit의 short-memory slot 수집, draft review, confirm-only Inform 저장 흐름
- `change_management` unit의 회의/변경점 저장 데이터 기반 plain text recall 답변
- `dashboard_agent` unit의 source-agnostic chart_result draft 생성
- `data/flow-data/semantic` JSON lexicon, intent hint, proposal queue 관리
- `data/flow-data/agent_feedback_penalties.json` 단위기능 AI feedback penalty profile
- Home Flow-i 실행의 공개 runtime graph snapshot 관찰
- Home Flow-i 사용자별 Q/A 메모리(`data/flow-data/home_agent_memory/conversation.jsonl`) 저장과 후속 질문 context 병합

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

## Inform Registration Unit

`inform_registration`은 Agent 단위기능 AI의 두 번째 unit이다. 화면 구조는 FileBrowser AI SQL과 같이 상단 `질문 이력`, 하단 `State` / `LangGraph` / `Test prompt`를 쓴다.

실행 graph는 `context_seed -> semantic_layer -> slot_extract -> validate_missing -> snapshot_preview -> review -> register`다. `semantic_layer`는 공유 lexicon의 alias hit, slot hint, unknown term, warning을 공개 trace에 남긴다. `product`, 단일 `lot_id`, `module`, `note`, 메일 target이 필수 slot이다. 사용자가 set/KNOB/CUSTOM/SplitTable snapshot을 요청한 경우에만 `snapshot_custom_cols` 또는 `attached_sets`를 추가 필수값으로 본다.

slot 병합 우선순위는 기존 short-memory slot, semantic hint, 원문에서 명확히 추출된 값, explicit `slot_overrides` 순서다. 즉 `slot_overrides`와 원문 명시값은 semantic hint보다 우선한다.

`continue` action은 slot을 누적하고 누락값을 질문한다. `confirm` action은 누락값이 없을 때만 `routers.informs.InformCreate`와 `create_inform()`을 호출한다. confirm 전에는 `FLOW_DATA_ROOT/informs/informs.json`을 쓰지 않고, 1시간 TTL의 short memory session JSON만 `FLOW_DATA_ROOT/agent_unit_ai_sessions/inform_registration/` 아래에 저장한다. 메일은 발송하지 않고 `mail_draft`만 Inform에 보존한다.

Inform 화면 안에는 별도 `Flow-i 인폼 질문` 입력창을 두지 않는다. Home Agent는 `/api/home-agent/orchestrate`, `/api/home-agent/orchestrate/stream`, `/api/home-agent/run-tool`에서 `inform_registration` unit을 직접 runtime으로 실행한다. `/run-tool`은 `input` dict에 `prompt`, `session_id`, `action`, `slot_overrides`를 담아 호출하며, `confirm` 저장은 Home Agent request/user context를 그대로 전달해 기존 Inform 권한과 audit 흐름을 탄다.

## Change Management Unit

`change_management`는 Agent 단위기능 AI의 세 번째 unit이다. 화면 구조는 FileBrowser AI SQL, Inform 등록 도우미와 같이 상단 `질문 이력`, 하단 `State` / `LangGraph` / `Test prompt`를 쓴다.

실행 graph는 `context_scope -> meeting_reference -> evidence_pack -> answer_compose`다. `context_scope`는 현재 사용자가 볼 수 있는 Meeting과 Calendar event만 읽고, `meeting_reference`는 prompt의 회의명을 기존 meeting ask resolver로 해석한다. 후보가 여러 개이거나 특정 회의가 없으면 하나를 추측하지 않고 후보와 함께 확인 필요 상태를 반환한다.

`evidence_pack`은 visible meeting의 agenda, minutes, decision, action item과 변경점 관리 calendar event summary를 만든다. `answer_compose`는 `routers.meetings`의 안전한 meeting ask LLM/fallback 경로를 재사용하되, 결과는 `**`, `###`, backtick 같은 markdown 장식을 제거한 plain text로 정리한다. LLM이 없거나 실패하면 저장 데이터 기반 fallback을 쓰고, 근거가 없으면 없다고 답한다.

이 unit은 Meeting/Calendar 데이터를 쓰지 않는다. 실행 이력만 `FLOW_DATA_ROOT/agent_unit_ai_sessions/change_management/history.jsonl`에 append한다.

## Dashboard Agent Unit

`dashboard_agent`는 Agent 단위기능 AI의 네 번째 unit이다. 입력은 source 종류와 무관하게 `{natural_language, columns, sample_rows}`만 본다.

실행 graph는 `semantic_layer -> chart_intent -> chart_type_select -> params_fill -> spec_validate -> render_spec`다. `semantic_layer`는 `backend/core/agent_semantic_service.py`를 공유하고, LLM이 없거나 실패하면 chart type과 x/y/group 파라미터를 deterministic fallback으로 채운다.

출력은 기존 Home/Dashboard가 쓰는 `chart_result` shape를 유지한다. `PlotlyChart.jsx`가 받는 `kind`, `chart_type`, `points`, `config`, `chart_config`, `total` 필드를 깨지 않는다.

Agent 단위기능 AI 탭은 Unit 전체와 LangGraph node별 `좋아요` / `싫어요` feedback을 저장한다. feedback은 `FLOW_DATA_ROOT/agent_feedback_penalties.json`의 runtime penalty profile만 갱신하며 prompt, code, rule, cache를 자동 수정하지 않는다. 각 runtime trace row와 graph node에는 현재 node penalty metadata를 붙인다. v1에서는 penalty가 높아도 node 실행 자체를 skip하지 않고, Home Flow-i의 휴리스틱 점수와 LLM/ReAct planner catalog의 낮은 우선순위/avoid 표시 신호로만 사용한다.

기존 `FLOW_DATA_ROOT/agent_unit_overrides.json`와 `/runtime/overrides` API는 과거 저장값 호환을 위해 backend에 남긴다. Agent UI에서는 persona/prompt/cache 편집 textarea를 노출하지 않는다.

신규 단위기능 AI 실행 surface는 `/api/agent/unit/{key}/graph|run|history`를 우선 사용한다. 기존 `/api/agent/unit-ai/{key}/runtime/*` 경로는 호환용으로 유지한다. 공통 node timing, trace row, exception wrapping, state diff merge는 `backend/app_v2/modules/agent_runtime/executor.py`가 맡고, 각 unit runtime은 노드 정의, persona, 도메인 prompt, owner API 호출만 보존한다.

## Home SQL Join Dashboard Unit

`home_sql_join_dashboard`는 기준 source SQL draft, schema relation 기반 JOIN, output route를 담당한다. `dashboard_draft` 노드는 직접 chart spec을 만들지 않고 `dashboard_agent`를 sub-runtime으로 호출하며, sub-trace를 parent trace의 `dashboard.sub_trace`와 Home ToolCall `sub_trace`에 남긴다.

## Home Flow-i Runtime Tab

Home Flow-i 응답은 기존 `/api/llm/flowi/chat` 결과를 유지하면서 `run_id`와 공개 runtime graph snapshot을 남긴다. Agent의 `Flow-i` 탭은 `data/flow-data/home_agent_runs/*.json`에 저장된 최근 실행을 읽어 `프롬프트 입력 → 용어해석 → 오케스트레이터 → 단위기능 AI MCP 후보 → 결과 정리` 그래프로 보여준다.

Snapshot에는 원본 DB row 전체나 내부 추론 원문을 저장하지 않는다. preview rows는 Home 화면 표시 수준으로 제한하고, node detail은 input/output 요약, warning, action log만 포함한다.

Home Flow-i는 같은 feedback penalty profile을 읽어 자동 Unit AI 후보 점수에 `boost - penalty`를 반영한다. 명시적 alias나 사용자가 `/api/home-agent/run-tool`로 특정 unit을 직접 실행하는 경우에는 차단하지 않고, 실행 trace와 runtime graph에 penalty metadata만 남긴다. LLM/ReAct planner catalog는 down-rated unit을 `low_priority` 또는 `avoid` 후보로 표시한다.

### 반복 ReAct 루프 (선택, flag 기본 off)

`FLOW_LLM_REACT_LOOP=1`이고 LLM planner(`FLOW_LLM_TOOL_CALL` + 연결된 LLM)도 활성이면 Home Flow-i는 단일 패스 plan 대신 **반복 ReAct 루프**로 동작한다. 루프는 `prompt -> semantic_layer(공유 resolver) -> [관찰 -> 다음 도구 1개 결정 -> 실행]* -> 결론` 순서로, 각 턴에서 `llm_adapter.complete_json`으로 "도구 1개 호출" 또는 "finalize"를 strict JSON으로 결정한다. native `tool_calls`는 쓰지 않아 GPT-OSS 120B급 on-prem 서빙과 호환된다. 무한 루프는 `FLOW_LLM_REACT_MAX_ITERS`(기본 6, [1,12] clamp) 상한 + 반복-액션 가드 + 무진전 가드 + `model_final`/`blocked`로 막는다. LLM이 실패해 한 step도 못 돌면 기존 alias/heuristic 단일 패스로 graceful degrade한다.

react 실행의 snapshot은 기존 graph/`node_details` shape를 유지하면서 `iter:{i}:{tool}` 반복 노드 chain(`orchestrator -> iter:0 -> ... -> result_renderer`)과 additive 필드(`iterations`, `stop_reason`, `semantic_frame`)를 더한다. 공개에는 도구/상태/결과 요약/`reason`만 싣고 모델 내부 `thought`는 노출하지 않는다. flag off 기본값에서는 단일 패스 동작과 snapshot 계약이 그대로다. 구현은 `backend/core/home_orchestrator.py`의 `_run_react_loop`, `_decide_next_action`, `_compose_final_reply`다.

Home Flow-i는 응답 생성 후 사용자별 prompt/answer와 공개 tool summary만 `FLOW_DATA_ROOT/home_agent_memory/conversation.jsonl`에 append한다. 다음 `/api/llm/flowi/chat` 요청은 frontend가 보낸 현재 세션 context와 서버 메모리의 최근 Q/A를 병합해 후속 질문 해석에 사용한다. `아까 내가 뭐 물어봤지?`처럼 이전 질문/답변을 묻는 prompt는 LLM 없이 메모리 기반 plain text 답변을 반환한다. 이 메모리에는 raw preview row dump, 내부 reasoning, source DB 원문을 저장하지 않는다.

Home Flow-i는 `Vehicle_matching.csv`, `step_matching.csv`, `matching_step.csv`, `ppid_knob.csv`가 schema catalog 또는 DB root single-file로 등록되어 있으면 read-only evidence로 사용할 수 있다. `step_id -> function_step/step_desc` 직접 조회와 `ppid_knob.csv feature_name -> function_step -> step_id` 확장은 `/api/llm/flowi/chat` 응답의 `tool.source_ids`, `tool.filters`, `tool.table`, `term_resolution`, `trace.api_calls`에 근거 파일과 필터를 남기며 원본 CSV를 수정하지 않는다.

## Semantic Layer Tab

`Semantic layer` 탭은 공유 semantic JSON 사전의 disk override와 effective merge view를 분리해 보여준다. 사용자는 JSON 편집으로 alias group과 intent hint를 저장할 수 있고, meeting/inform/tracker/activity log에서 쌓인 pending proposal을 approve/reject할 수 있다.

자연어 등록은 `/api/agent/semantic/draft`에서 alias/intent JSON 초안만 생성한다. 실제 저장은 사용자가 `초안 저장` 또는 JSON 저장 버튼을 눌렀을 때만 `/api/agent/semantic/alias-groups/*`와 `/api/agent/semantic/intent-hints/*` write API로 이뤄진다. write/decision API는 admin 또는 `agent`/`diagnosis`/`knowledge` page manager만 허용하고, 조회와 draft 생성은 로그인 사용자에게 허용한다.

Semantic layer의 API, data-root, unit별 사용 규칙은 [agent-semantic-layer.md](agent-semantic-layer.md)를 기준으로 본다.

## App Error Explanation

공통 frontend API helper는 `/api/*` 응답이 실패하면 원문 에러를 먼저 만든 뒤 `/api/llm/error/explain`에 발생 화면, API, HTTP status, 원문을 전달한다. LLM이 사용 가능하고 설명 생성에 성공하면 UI에는 `문제`, `발생 위치`, `가능한 원인`, `해결 방법`, `원문 에러` 순서로 표시한다.

LLM이 꺼져 있거나 설명 생성이 실패하면 기존 원문 에러 메시지를 그대로 보여준다. 이 endpoint는 원문을 prompt에 넣기 전 token/password류 문자열을 redaction하고, 내부 reasoning이나 숨은 trace를 노출하지 않는다.

## Code Entrypoints

| Layer | Path |
|---|---|
| Frontend page | `frontend/src/pages/My_Diagnosis.jsx` |
| Common API error formatting | `frontend/src/lib/api.js` |
| LLM settings panel | `frontend/src/components/agent/LlmTab.jsx` |
| Agent router | `backend/routers/agent.py` |
| Shared Agent runtime helpers | `backend/app_v2/modules/agent_runtime/` |
| LLM status/error explain router | `backend/routers/llm.py` |
| Home runtime graph | `backend/core/home_orchestrator.py` |
| Home Q/A memory | `backend/core/home_memory.py` |
| Unit registry | `backend/core/flowi_units/registry.py` |
| Shared semantic resolver | `backend/core/agent_semantic_service.py` |
| FileBrowser AI SQL runtime | `backend/core/flowi_units/filebrowser_ai_sql_runtime.py` |
| Inform registration runtime | `backend/core/flowi_units/inform_registration_runtime.py` |
| Change management runtime | `backend/core/flowi_units/change_management_runtime.py` |
| Dashboard Agent runtime | `backend/core/flowi_units/dashboard_agent_runtime.py` |
| Semantic lexicon store | `backend/app_v2/modules/semantic_lexicon/` |
| Semantic proposal queue | `backend/app_v2/modules/semantic_learning/` |
| FileBrowser owner | `backend/routers/filebrowser.py` |
| Inform owner | `backend/routers/informs.py` |

## Validation

- `python3 -m pytest tests/agent/test_filebrowser_ai_sql_runtime.py`
- `python3 -m pytest tests/agent/test_agent_semantic_service.py tests/agent/test_dashboard_agent_runtime.py`
- `python3 -m pytest tests/agent/test_home_orchestrator_chaining.py tests/agent/test_home_sql_join_dashboard_runtime.py`
- `python3 -m pytest tests/agent/test_home_react_loop.py`
- `python3 -m pytest tests/agent/test_inform_registration_runtime.py`
- `python3 -m pytest tests/agent/test_change_management_runtime.py`
- `python3 -m pytest tests/agent/test_semantic_agent_api.py tests/test_semantic_lexicon.py tests/test_semantic_learning_extractor.py`
- `python3 -m pytest tests/test_home_orchestrator.py`
- `python3 -m pytest tests/test_filebrowser_sql.py`
- `python3 -m pytest tests/test_feature_contracts.py`
- `cd frontend && npm run build`
