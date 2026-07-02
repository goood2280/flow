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
  - `GET /api/llm/flowi/workflows`
  - `POST /api/llm/flowi/workflows/draft`
  - `POST /api/llm/flowi/workflows`
  - `POST /api/llm/flowi/workflows/delete`
  - `GET /api/agent/unit-ai/change_management/runtime/graph`
  - `POST /api/agent/unit-ai/change_management/runtime/run`
  - `GET /api/agent/unit-ai/change_management/runtime/history`
  - `GET /api/agent/unit-ai/dashboard_agent/runtime/graph`
  - `POST /api/agent/unit-ai/dashboard_agent/runtime/run`
  - `GET /api/agent/unit-ai/dashboard_agent/runtime/history`
  - `GET /api/agent/unit-ai/{unit_key}/feedback-profile`
  - `POST /api/agent/unit-ai/{unit_key}/feedback`
  - `GET/PUT /api/agent/unit-ai/{unit_key}/runtime/overrides` (호환용 backend API)
  - `GET /api/agent/semantic/lexicon`
  - `GET /api/agent/semantic/sources`
  - `GET /api/agent/semantic/measurements`
  - `PUT /api/agent/semantic/measurements/{id}`
  - `POST /api/agent/semantic/measurements/merge-defaults`
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

prompt가 `x축`, `y축`, `x/y축`, `x axis`, `y axis`처럼 data table 축 값을 명시하면 `params_fill/spec_validate`가 해당 축 컬럼이 실제 columns에 있고 비어 있지 않은지 확인한다. 축 컬럼명이 비어 있거나 table에 없거나 sample row에서 전부 빈 값이면 chart를 추측 생성하지 않고 `status=blocked`, `needs_input=true`, `question`으로 사용자에게 다시 채울 값을 묻는다.

Dashboard Agent 질문 이력은 `FLOW_DATA_ROOT/agent_unit_ai_sessions/dashboard_agent/history.jsonl`에 저장한다. 이력에는 prompt, columns, 실행 metadata, chart summary, warning, trace summary만 남기며 `sample_rows`, chart points, preview row payload는 저장하지 않는다.

Agent 단위기능 AI 탭은 각 unit의 실행 결과/질문 이력 답변과 LangGraph node detail에 `좋아요` / `싫어요` feedback을 붙여 저장한다. feedback은 `FLOW_DATA_ROOT/agent_feedback_penalties.json`의 runtime penalty profile만 갱신하며 prompt, code, rule, cache를 자동 수정하지 않는다. 각 runtime trace row와 graph node에는 현재 node penalty metadata를 붙인다. v1에서는 penalty가 높아도 node 실행 자체를 skip하지 않고, Home Flow-i의 휴리스틱 점수와 LLM/ReAct planner catalog의 낮은 우선순위/avoid 표시 신호로만 사용한다.

기존 `FLOW_DATA_ROOT/agent_unit_overrides.json`와 `/runtime/overrides` API는 과거 저장값 호환을 위해 backend에 남긴다. Agent UI에서는 persona/prompt/cache 편집 textarea를 노출하지 않는다.

신규 단위기능 AI 실행 surface는 `/api/agent/unit/{key}/graph|run|history`를 우선 사용한다. 기존 `/api/agent/unit-ai/{key}/runtime/*` 경로는 호환용으로 유지한다. 공통 node timing, trace row, exception wrapping, state diff merge는 `backend/app_v2/modules/agent_runtime/executor.py`가 맡고, 각 unit runtime은 노드 정의, persona, 도메인 prompt, owner API 호출만 보존한다.

## Dashboard Agent Source Orchestration

SQL/JOIN 차트 요청은 별도 “Home SQL JOIN Dashboard” 단위기능이 아니라 `dashboard_agent`의 내부 data access path로 실행된다. Dashboard 탭에서 Dashboard Agent가 만든 결과는 Dashboard 화면에서 차트로 보이고, Home Agent가 Dashboard Agent를 선택한 경우에는 같은 `chart_result`를 Home 응답 payload에 붙여 Home 화면에서 바로 렌더링한다.

실행 graph는 `semantic_layer -> source_resolve -> filebrowser_sql_draft -> data_need_decision -> join_candidate_select -> join_plan_validate -> data_execute -> output_route -> dashboard_draft`다.

- `source_resolve`는 explicit `root/product/file`을 우선 사용하고, 없으면 schema relation column catalog와 FileBrowser 후보를 점수화한다. 후보가 여러 개이거나 product/file 값이 비어 있으면 추측하지 않고 `needs_input` 후보 목록을 반환한다.
- `filebrowser_sql_draft`는 직접 자유 SQL을 만들지 않고 `filebrowser_ai_sql` sub-runtime의 `display_sql`, `where_sql`, `selected_columns`, `sort` 계약을 재사용한다.
- `data_need_decision`과 `join_plan_validate`는 단일 source로 충분한 요청은 JOIN 없이 FileBrowser preview rows를 사용하고, multi-source 요청은 confirmed `schema_relations`가 있을 때만 JOIN을 실행한다.
- `dashboard_draft`는 직접 chart spec을 만들지 않고 `dashboard_agent`를 sub-runtime으로 호출한다. 결과 `chart_result.config.source_evidence`에는 source ids, relation ids, join keys, SQL summary, FileBrowser/Dashboard sub-trace가 남는다.
- Dashboard Agent가 축 컬럼 누락/빈 값으로 `needs_input`을 반환하면 parent unit도 `blocked`로 끝나며 사용자에게 축 값을 다시 묻는다.

## Home Flow-i Runtime Tab

Home Flow-i 응답은 기존 `/api/llm/flowi/chat` 결과를 유지하면서 `run_id`와 공개 runtime graph snapshot을 남긴다. Agent의 `Flow-i` 탭은 `data/flow-data/home_agent_runs/*.json`에 저장된 최근 실행을 읽어 `프롬프트 입력 → 용어해석 → 오케스트레이터 → 단위기능 AI MCP 후보 → 결과 정리` 그래프로 보여준다.

`Flow-i` 탭 내부는 Semantic layer 탭과 같이 하위 탭으로 나눈다. `Workflow 템플릿`은 `/api/llm/flowi/workflows`의 workflow catalog를 관리하는 화면이며, admin은 템플릿을 추가/수정하고 비활성화할 수 있다. 연결된 LLM은 사용자가 입력한 자연어와 현재 초안을 workflow schema 형식으로 맞추는 데만 사용하며, LLM이 없거나 실패하면 로컬 formatter로 fallback한다. 비활성화된 템플릿은 Home Flow-i few-shot/matching 후보에서 제외한다. `Runtime trace`는 기존 최근 실행 목록, runtime graph, node detail을 보여준다.

Snapshot에는 원본 DB row 전체나 내부 추론 원문을 저장하지 않는다. preview rows는 Home 화면 표시 수준으로 제한하고, node detail은 input/output 요약, warning, action log만 포함한다.

Home Flow-i는 같은 feedback penalty profile을 읽어 자동 Unit AI 후보 점수에 `boost - penalty`를 반영한다. Home 답변의 `좋아요` / `개선 필요` feedback도 관련 feature를 unit key로 정규화해 home/unit penalty profile에 반영한다. 명시적 alias나 사용자가 `/api/home-agent/run-tool`로 특정 unit을 직접 실행하는 경우에는 차단하지 않고, 실행 trace와 runtime graph에 penalty metadata만 남긴다. LLM/ReAct planner catalog는 down-rated unit을 `low_priority` 또는 `avoid` 후보로 표시한다.

### 반복 ReAct 루프 (선택, flag 기본 off)

에이전틱 모드는 env 외에 admin 설정으로도 켤 수 있다: LLM 설정 화면의 "에이전틱 오케스트레이션" 토글이 `admin_settings.json flowi_defaults.agentic.{tool_call_enabled,react_loop_enabled}`를 저장하고, `FLOW_LLM_TOOL_CALL`/`FLOW_LLM_REACT_LOOP` env가 명시된 서버에서는 env가 우선한다(`home_orchestrator._flag_enabled`).

`FLOW_LLM_REACT_LOOP=1`(또는 admin 토글)이고 LLM planner(`FLOW_LLM_TOOL_CALL` 또는 admin 토글 + 연결된 LLM)도 활성이면 Home Flow-i는 단일 패스 plan 대신 **반복 ReAct 루프**로 동작한다. 루프는 `prompt -> semantic_layer(공유 resolver) -> [관찰 -> 다음 도구 1개 결정 -> 실행]* -> 결론` 순서로, 각 턴에서 `llm_adapter.complete_json`으로 "도구 1개 호출" 또는 "finalize"를 strict JSON으로 결정한다. native `tool_calls`는 쓰지 않아 GPT-OSS 120B급 on-prem 서빙과 호환된다. 무한 루프는 `FLOW_LLM_REACT_MAX_ITERS`(기본 6, [1,12] clamp) 상한 + 반복-액션 가드 + 무진전 가드 + `model_final`/`blocked`로 막는다. LLM이 실패해 한 step도 못 돌면 기존 alias/heuristic 단일 패스로 graceful degrade한다.

react 실행의 snapshot은 기존 graph/`node_details` shape를 유지하면서 `iter:{i}:{tool}` 반복 노드 chain(`orchestrator -> iter:0 -> ... -> result_renderer`)과 additive 필드(`iterations`, `stop_reason`, `semantic_frame`)를 더한다. 공개에는 도구/상태/결과 요약/`reason`만 싣고 모델 내부 `thought`는 노출하지 않는다. flag off 기본값에서는 단일 패스 동작과 snapshot 계약이 그대로다. 구현은 `backend/core/home_orchestrator.py`의 `_run_react_loop`, `_decide_next_action`, `_compose_final_reply`다.

Home Flow-i는 응답 생성 후 사용자별 prompt/answer와 공개 tool summary만 `FLOW_DATA_ROOT/home_agent_memory/conversation.jsonl`에 append한다. 다음 `/api/llm/flowi/chat` 요청은 frontend가 보낸 현재 세션 context와 서버 메모리의 최근 Q/A를 병합해 후속 질문 해석에 사용한다. `아까 내가 뭐 물어봤지?`처럼 이전 질문/답변을 묻는 prompt는 LLM 없이 메모리 기반 plain text 답변을 반환한다. 이 메모리에는 raw preview row dump, 내부 reasoning, source DB 원문을 저장하지 않는다.

Home Flow-i는 `Vehicle_matching.csv`, `step_matching.csv`, `matching_step.csv`, `ppid_knob.csv`가 schema catalog 또는 DB root single-file로 등록되어 있으면 read-only evidence로 사용할 수 있다. `step_id -> function_step/step_desc` 직접 조회와 `ppid_knob.csv feature_name -> function_step -> step_id` 확장은 `/api/llm/flowi/chat` 응답의 `tool.source_ids`, `tool.filters`, `tool.table`, `term_resolution`, `trace.api_calls`에 근거 파일과 필터를 남기며 원본 CSV를 수정하지 않는다.

### Step lookup 확장 — 유사 후보, 관련 파일, human-in-the-loop 학습

`step_lookup` unit은 정확 일치 외에 다음을 제공한다:
- **유사 후보**: step 의도 질문에서 정확 일치가 없으면 step_id 모양 토큰(AA100000, A00000, AB100000EC 등)을 추출해 suffix 정규화(base) 일치 > 접두 일치 순으로 후보를 제시한다 (`fab_reference.suggest_similar_steps`). step 토큰만 있고 의도 키워드가 없으면 기존처럼 개입하지 않는다 (root lot 오염 방지).
- **관련 파일 횡단 검색**: 조회된 step_id/function_step이 `matching_cache.SUPPORTED_MATCHING_FILES`(vehicle_matching, ppid_knob, inline_* 등) 중 db_root에 존재하는 파일 어디에 쓰이는지 `fab_reference.search_related_files`로 찾아 파일/열/행수를 답에 붙인다. 수정은 Files 단일 파일 편집 경로를 안내한다 (채팅이 원본을 직접 수정하지 않음).
- **Human-in-the-loop 학습**: 조회 실패 답변에 티칭 형식을 안내한다. 사용자가 `기억해: <용어>는 <답>`이라고 하면 `core/flowi_fewshots.py`(`data/flow-data/flowi_fewshots.json`, 전 유저 공유)에 저장되고, 이후 같은 용어 질문은 학습된 답으로 즉시 응답한다(가르친 사람/사용 횟수 표기). `잊어줘: <용어>`로 삭제. Home 답변 피드백에서 **싫어요 + 교정 코멘트**(`X -> Y` 또는 `정답은 Y`)를 남기면 같은 저장소에 교정으로 반영된다 (`home_agent.post_feedback`).

### 파일 설명문 기반 검색 (file docs)

`core/flowi_file_docs.py`(`data/flow-data/flowi_file_docs.json`, 전 유저 공유)는 Files 단일 파일별 설명문 카탈로그다. 사용자는 채팅에서 `파일 설명: <파일명>은 <설명>` 으로 등록한다. 검색성 질문(검색 의도 + 용어 토큰)이 다른 라우팅에서 처리되지 못했거나 schema 컬럼 검색이 빈손이면, Flow-i는 질문↔설명 토큰 매칭으로 대상 파일을 고르고 그 CSV 내용에서 용어를 찾아 파일/열/행 요약을 답한다 (`routers/llm.py _handle_file_doc_search`, filebrowser 권한 필요, 수정은 Files 편집 안내). 대상/결과가 없으면 human-in-the-loop 안내(`기억해:` 티칭 또는 `파일 설명:` 등록 요청)를 반환한다.

두 학습 저장소(few-shot, 파일 설명)는 Admin 페이지의 **Flow-i 학습** 탭에서 조회/수정/삭제한다 (`/api/flowi-learning/fewshots*`, `/api/flowi-learning/file-docs*`, admin 전용).

### 공유 스킬 라우팅

SQL 작업대에서 저장한 스킬(`data_root/skills/*.json`)은 `shared=true`면 모든 로그인 사용자가 조회/실행할 수 있고, `shared=false`(private)는 owner와 admin만 `/api/skills/list`에 보인다. 공유/비공유 전환은 `POST /api/skills/{key}/share`, 삭제는 `POST /api/skills/{key}/delete` (owner 또는 admin).

스킬 실행은 **기능 권한을 통과해야 한다**: 스킬의 `required_features`(sql_workspace 스킬은 `filebrowser` 자동 포함)가 사용자 allowed feature 의 부분집합이 아니면 실행이 차단되고 필요한 권한을 안내한다. 스킬 카탈로그도 사용자가 부족한 권한을 `권한 필요:` 로 표시한다 — 권한이 다른 시스템에 스킬을 통해 우회 접근할 수 없다.

Home Flow-i 채팅은 결정적 라우팅 초입에서 공유 스킬을 매칭한다 (`routers/llm.py _handle_shared_skill_request`):
- "스킬" 언급 + 목록성 질문(목록/알려줘 등, 실행 동사 없음) → 공유 스킬 카탈로그 안내.
- 스킬 제목이 프롬프트에 그대로 있거나 "스킬" 언급 + 토큰 과반 매칭 → `sql_workspace` 스킬은 read-only로 즉시 실행해 행 미리보기를 답하고 `run_count`를 올린다 (filebrowser 권한 필요, placeholder가 있으면 SQL 작업대 안내). `chain` 스킬은 단계 안내를 반환.
- 매칭이 없으면 기존 라우팅을 그대로 탄다 (회귀 없음).

## Semantic Layer Tab

`Semantic layer` 탭은 공유 semantic JSON 사전의 disk override와 effective merge view를 분리해 보여준다. 내부 관리는 `Lexicon 관리`, `Sources 관리`, `Measurements 관리`, `검토 이력` 하위 탭으로 나누어 한 번에 한 영역의 JSON 편집기, 자연어 등록, 카드 목록, proposal/change queue만 노출한다. 사용자는 JSON 편집으로 alias group, intent hint, Source Catalog, Measurement terms를 저장/추가/삭제할 수 있고, Source Catalog와 Measurement terms는 자연어 입력으로 초안을 만든 뒤 즉시 저장할 수 있다. 등록된 Source docs와 Measurement term 카드는 개별 수정/삭제 액션을 제공하며, meeting/inform/tracker/activity log에서 쌓인 pending proposal을 approve/reject할 수 있다.

자연어 등록은 `/api/agent/semantic/draft`에서 alias/intent/source_catalog/measurement_terms JSON 초안을 생성한다. draft endpoint 자체는 read-only이며, 실제 저장은 사용자가 `초안 저장`, Source/Measurement 자연어 저장, 또는 JSON 저장 버튼을 눌렀을 때만 semantic write API로 이뤄진다. write/decision API는 admin 또는 `agent`/`diagnosis`/`knowledge` page manager만 허용하고, 조회와 draft 생성은 로그인 사용자에게 허용한다.

Semantic layer의 API, data-root, unit별 사용 규칙은 [agent-semantic-layer.md](agent-semantic-layer.md)를 기준으로 본다.

## App Error Explanation

공통 frontend API helper는 `/api/*` 응답이 실패하면 원문 에러를 먼저 만든 뒤 `/api/llm/error/explain`에 발생 화면, API, HTTP status, 원문을 전달한다. LLM이 사용 가능하고 설명 생성에 성공하면 UI에는 `문제`, `발생 위치`, `가능한 원인`, `해결 방법`, `원문 에러` 순서로 표시한다.

LLM이 꺼져 있거나 설명 생성이 실패하면 기존 원문 에러 메시지를 그대로 보여준다. 이 endpoint는 원문을 prompt에 넣기 전 token/password류 문자열을 redaction하고, 내부 reasoning이나 숨은 trace를 노출하지 않는다.

## LLM Routing Policy

- If a saved internal GPT OSS-compatible profile (`openai_compatible`, `local`, or internal `generic`) is enabled and has an internal endpoint, runtime LLM selection must use that internal profile before any dev AI profile.
- In that state, active dev providers (`vertex_gemini`, external `openai`) and `FLOW_LLM_ENABLE_ENV_FALLBACK` env fallback are blocked from becoming the runtime config. `/api/llm/status.config` exposes `dev_ai_blocked`, `dev_ai_block_reason`, and `blocked_provider` when this policy redirects the runtime back to the internal profile.
- Saved `playground` profiles keep the existing behavior: when inactive they block external env fallback but are not auto-activated; when active they remain available.

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
