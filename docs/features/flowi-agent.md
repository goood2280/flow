# Flow-i Agent

Flow-i Agent는 사용자의 자연어 요청을 Flow 오케스트레이터가 판단한 뒤 기능별 단위기능으로 연결한다.

현재 미션은 **Agent 탭이 Inform Log / SplitTable / FileBrowser 의 driver로 동작하는 것**이다. Agent 페이지는 prompt → orchestrator → feature subagent → unit_action → API/handler → result 흐름이 한 화면에서 모두 보여야 한다. Diagnosis와는 시각적으로 분리한다.

예시 prompt와 사용자가 실행한 prompt를 `POST /api/llm/flowi/orchestrator/preview` dry-run 결과로 비교하고, 선택한 prompt는 `POST /api/llm/flowi/agent/chat` 실행 결과를 같이 보여준다.
`POST /api/agent/prompt-review`는 수동 프롬프트 점검용이다. LLM은 개선 문장과 모호점 질문만 제안하며, 실행 판단은 기존 deterministic preview와 guardrail 결과를 유지한다. LLM 실패 또는 미설정 시 missing slot 기반 fallback을 반환한다.

- prompt별 오케스트레이터 활성화 표: prompt, feature subagent, unit action, API/data target, missing field
- single prompt dry-run은 `context.ask_llm_to_guess_missing=true`일 때 공개 가능한 `guessed.values` / `guessed.rationale`만 보여준다.
- 입력 prompt와 user
- 오케스트레이터가 판단한 intent, feature subagent, unit action
- 실제 전달 prompt, 활성화된 feature/action, 호출 API를 한 줄 흐름으로 보여주는 activation map
- 서버가 검증한 단위기능 payload
- FastAPI endpoint -> 오케스트레이터 -> 기능 subagent -> 내부 API/cache/table -> answer payload 호출 그래프
- 각 API/handler 호출의 target, 목적, output, status
- 공개 가능한 단계별 trace
- answer, table/chart, next action 요약

여기서 feature subagent는 독립 LLM worker가 아니라 `FileBrowser`, `SplitTable`, `Inform`, `Tracker`, `Dashboard` 등 기능별 deterministic handler/skill을 뜻한다. LLM은 JSON draft와 답변 정리에만 쓰고, 실제 실행은 서버 schema/권한/확인 플로우가 결정한다.

`trace.call_graph`와 `trace.api_calls`는 사용자가 검증할 수 있는 실행 이벤트만 담는다. 내부 chain-of-thought나 모델 추론 원문은 노출하지 않는다.

## Owns

- feature intent routing
- FileBrowser/SplitTable/Inform/Tracker/Dashboard 등 앱 기능 질의
- RAG/knowledge lookup과 답변 근거 구성
- app 내부 action 후보 제안

## Does Not Own

- 일반 사용자 prompt에서 source code 변경
- raw DB 파일 직접 수정
- 관리자 확인 없는 destructive operation
- feature별 업무 규칙의 단독 판단

## Code Entrypoints

| Layer | Path |
|---|---|
| LLM router | `backend/routers/llm.py` |
| Agent router | `backend/routers/agent.py` |
| Knowledge router | `backend/routers/knowledge.py` |
| Agent tab components | `frontend/src/components/agent/` |
| Agent scenario smoke | `scripts/agent_scenario_check.py` |
| Feature prompts | `data/flow-data/flowi_agent_features/` |
| User notes | `data/flow-data/flowi_users/` |
| Entry docs | `data/flow-data/flowi_agent_entrypoints.md` |

## Guardrails

- 불명확한 product, lot, wafer, module은 action 전에 확인한다.
- feature docs의 책임 경계를 우선한다.
- raw DB write, code mutation, admin 설정 변경은 명시적 확인과 권한을 요구한다.
- 답변에는 사용자가 확인할 수 있는 app link나 파일/컬럼 근거를 남긴다.
- 저장성 작업은 draft/confirmation 상태까지만 Agent 페이지에서 보여주고, 확인 없는 저장은 하지 않는다.

## Current Method

- v1은 LangGraph hard dependency 없이 Flow 자체 라우터와 상태 요약을 사용한다.
- 오케스트레이터는 prompt를 `intent`, `feature`, `unit_action`, `slots`로 정리한다.
- 기능별 subagent는 앱 내부 deterministic 단위기능이다.
- 장기 실행, 병렬 step, replay/checkpoint가 필요해지면 그때 Skill Runner 내부에만 durable orchestration을 붙인다.

## LLM Target (사내 API)

Agent가 사용하는 기본 LLM은 **사내 API의 GPT OSS 120B**다. 검증/개발 profile로 OpenAI 소형 모델과 Vertex Gemini ADC profile을 둘 수 있지만, 실제 라우팅·권한·확인은 deterministic handler가 결정한다.

- 어댑터: `backend/core/llm_adapter.py` (`provider="openai"`, `"openai_compatible"`, `"vertex_gemini"` 등).
- 권장 profile:
  - `local_test_openai`: OpenAI small/nano급 연결 확인용.
  - `internal_gpt_oss_120b`: 사내 GPT OSS 120B openai-compatible.
  - `vertex_gemini`: Google ADC/OAuth access token을 요청 직전 refresh해 Bearer로 전송 (`auth_mode="google_adc"`).
- 설정 위치: `data/flow-data/admin_settings.json` 의 `llm` / `llm_profiles` 블록 — `enabled`, `api_url`, `model`, `provider`, `auth_mode`, `headers`, `format`, `timeout_s`.
- 본 모델은 **오픈소스 파인튜닝 수준**이라 추론 안정성이 낮다. caller 규약(이미 어댑터 docstring에 적힘):
  - LLM 응답은 JSON draft / 문장 정리 등 **rephrasing 영역**에서만 사용. 실제 라우팅·권한·확인은 deterministic handler가 결정.
  - 프롬프트는 짧고 단순하게 작성. 긴 chain-of-thought 강요하지 않는다.
  - JSON draft는 schema validation을 거치고 parse 실패 시 1회 repair prompt 후 deterministic fallback을 사용한다.
  - `HTTP 429` 또는 응답 실패/미설정 시 `{"ok": False}`로 처리하고 사용자에게 직접 입력 fallback 또는 local deterministic router 결과를 제공한다.
- LLM 사용 가능 여부는 `llm_adapter.is_available()`로 확인하고, UI 카드(예: 자동 답변, 자동 요약)는 이 값이 `True`일 때만 표시한다.
- 응답 본문에는 사내 endpoint URL이나 token이 노출되지 않게 한다 (admin only redacted view).

## Slot Rules

- 제품명은 product config, ML_TABLE 파일명, FAB product directory에서 확인되는 이름을 우선한다.
- `lot_id`/`fab_lot_id`는 주로 `A1001A.1`, `A1000.1`, `A1000.XX`처럼 영문/숫자 5~6자 이상과 `.` suffix 조합으로 본다.
- `root_lot_id`는 dot lot의 왼쪽 head 5글자다. 예: `A1001A.3` -> `A1001`.
- `wafer_id`는 `#21`, `WF21`, `21번 wafer`를 같은 물리 wafer `21`로 정규화한다.
- `lot_wf`는 `root_lot_id + "_" + wafer_id`다. 예: `A1000 #21` -> `A1000_21`.

## Routing Examples

- `PRODA A1001A.1 KNOB Split Table 보여줘`는 SplitTable view API를 호출하고 `KNOB` prefix row만 보여준다.
- `A1000 #21 현재 step이 어디야`는 FileBrowser latest progress cache에서 `step_id`와 `function_step`을 찾는다.
- `PRODA A1000 KNOB_ALPHA 보여줘`처럼 root lot이 명시된 ML_TABLE feature/knob 조회는 `/api/filebrowser/ml-table/lookup`과 `core.ml_table_lookup.query_root_lot` cache를 우선 사용한다.
- `PRODA 24.0 SORT KNOB PPID_24_1인 WF 중에 가장 빠른게 뭐야`는 ML_TABLE에서 matching `lot_wf`를 찾고 latest progress cache의 step 순서로 정렬한다. 이처럼 root lot 없는 value 역검색은 전체 후보 탐색이 필요하므로 별도 검색 경로를 쓴다.
- `PRODA A1000 test2 커스텀 세트로 인폼남겨줘`는 Inform Log draft로 보내며 module, note, 수신처가 없으면 확인 질문을 먼저 만든다.
- `A1001A.3 이거 무슨랏이야`는 Tracker issue lot 목적(`purpose`)을 조회한다.

## Agent Wiki

Agent Wiki 운영은 다음 단계의 지식 운영 계층이다. 현재 Agent 페이지 메인 흐름에는 노출하지 않는다.

- Raw source는 `data/flow-data/knowledge/raw/sources/` 아래 append-only로 저장하며 원본 DB/Fab 파일은 수정하지 않는다.
- Maintained wiki page는 `data/flow-data/knowledge/wiki/agent_wiki/` 아래 markdown으로 저장한다.
- Wiki page frontmatter의 최소 필드는 `doc_id`, `kind=agent_wiki`, `title`, `summary`, `source_ids`, `updated_at`, `tags`이다.
- Search entrypoint는 `data/flow-data/knowledge/index/wiki_index.json`이며 Agent 탭 검색은 `agent_wiki` kind를 우선한다.
- Chronological 운영 기록은 `data/flow-data/knowledge/index/wiki_log.jsonl`에 append-only로 남긴다.
- Lint는 broken `[[wiki_link]]`, missing source, orphan page, stale summary, contradiction 후보를 점검한다.
- Source 등록, ingest commit, lint는 admin 또는 `diagnosis`/`knowledge` page admin만 수행한다. 읽기와 preview는 로그인 사용자가 수행할 수 있다.

## Agent Tab UX (single-page flow)

Agent 탭은 다음 7개 카드를 한 페이지에서 위에서 아래로 순서대로 본다. 사용자 평가 "보기가 불편" 의 1차 원인은 Diagnosis와 한 페이지를 공유하던 점이며, 이 화면은 진단/지식 카드와 섞이지 않는다.

1. **Persona / Do / Don't** — `GET /api/llm/flowi/persona-card`. Agent의 책임 범위 한 줄 요약.
2. **Prompt 입력 + 예시 drop-down** — `data/flow-data/flowi_agent_features/*.md`의 examples를 자동 수집한 prompt 후보. 사용자는 직접 입력하거나 후보 선택.
3. **Activation Map (5단계)** — 아래 "Activation Map (5 stages)" 참조.
4. **Call Graph** — FastAPI → orchestrator → feature subagent → 내부 API/data → answer 의 노드/엣지.
5. **API Calls 표** — 각 호출의 `stage / method / target / callee / purpose / output / status / payload`.
6. **Trace Steps** — 공개 가능한 단계별 진행 (`trace.steps`). 현재 비어있어 채워야 함.
7. **Answer + next action + 보강 질문** — 답변, 권고 next action, 부족한 정보 질문.

좌측(또는 사이드)에는 Inform walkthrough 진입과 최근 inform draft 세션 목록을 둔다. SplitTable view 호출/FileBrowser preview 호출도 동일 페이지의 응답 카드에 인라인 결과를 보여줄 수 있다.

## Activation Map (5 stages)

`trace.call_graph.activation`은 항상 다음 키를 채운다.

| stage | 카드 표시 | 채울 필드 |
|---|---|---|
| 01 prompt | 에이전트가 받은 prompt | `prompt`, `endpoint` |
| 02 orchestrator | intent 판정 | `intent`, `feature`, `action`, `status` |
| 03 feature subagent | 활성화된 기능 | `feature`, `handler` |
| 04 unit_action | 호출된 단위기능 | `action`, `api`, `payload_summary` |
| 05 result | 호출 결과 | `output`, `status`, `next_action`, `missing` |

`status ∈ {ready, done, needs_input, awaiting_confirmation, blocked, error}`. `needs_input` / `blocked` / `error`인 경우 카드 위에 cause + missing slot을 강조 배너로 표시.

## 용어 해석 (Wiki + Schema Registry)

자연어 용어를 DB 컬럼으로 연결할 때 의미 설명과 구조 메타를 분리한다. Wiki는 `kind=schema_doc` 문서로 DB/테이블/컬럼의 의미, 도메인 설명, 사용 예, 주의사항을 markdown으로 저장하고 frontmatter에 `relation_id`와 `column_refs`(`RELATION.column_name`)를 둔다.

구조 메타는 `data/flow-data/schema_relations.json`의 `column_catalog`에 둔다. 각 항목은 `relation_id`, 정규화된 `column`, `raw_names`, `dtype`, `canonical_alias`, `unit`, `fk`, `sample_values`, `wiki_doc_id`를 가지며, `wiki_doc_id`와 schema_doc의 `column_refs`로 양쪽을 연결한다.

Agent는 `resolve_term_to_columns(term)`에서 `kv.list_docs(kind="schema_doc", q=term)`를 먼저 사용해 wiki hit를 찾고, frontmatter의 `relation_id`/`column_refs`를 `column_catalog`와 매칭해 후보를 만든다. wiki hit가 없을 때만 기존 `_RELATION_ALIASES` fallback을 사용한다. 이 단계는 slot extract/arguments 정형화에서 쓸 lookup이며, cross-DB join과 차트 렌더링 통합은 후속 범위다.

## Backend Trace Contract

`POST /api/llm/flowi/agent/chat` 응답의 `trace`는 다음을 항상 포함한다.

- `trace.activation` — 위 5단계 Activation Map dict (누락 없음)
- `trace.interpretation` — product/lot/wafer/step/item/회의명/차수/source 후보, Wiki/schema로 해석한 knowledge_terms, missing/filled slot 공개 요약
- `trace.evidence` — 사용한 기능 AI, endpoint, payload 요약, SQL/filter, chart config, meeting sources
- `trace.evidence.knowledge_sources` / `trace.retrieved_knowledge` — prompt 용어를 Agent Wiki `schema_doc`와 `column_catalog`에 대조한 공개 근거
- `trace.validation` — rows, chart readiness, source count, warnings, fallback 여부
- `trace.call_graph.nodes` / `trace.call_graph.edges` — 노드/엣지 (빈 배열 아님)
- `trace.call_graph.activation` — `trace.activation`과 동일 내용 동봉 (frontend fallback용)
- `trace.api_calls` — list (이미 채워짐)
- `trace.steps` — list of `{stage, title, detail, status, ts}` (현재 빈 배열, **이번 보강 대상**)

`POST /api/llm/flowi/orchestrator/preview` row schema는 `{prompt, feature, action, api, missing, status}`를 항상 채운다. `action`은 feature md의 Agent Driver Contract unit action이며, 내부 handler 이름이 다르면 `handler_action`에 별도로 남긴다.

## Acceptance Criteria (Codex 인계)

Agent 탭이 다음을 모두 만족하면 본 미션의 완료 조건이다.

- [x] Agent 탭이 Diagnosis와 시각적으로 분리되어 있다 (별도 탭 또는 명확한 section + sticky header).
- [x] Activation Map 5단계 카드가 모두 채워져 표시된다 (빈 칸/`-` 만 있는 카드 없음).
- [x] `trace.steps`가 비어있지 않다 (Backend Trace Contract 충족).
- [x] 예시 prompt 5개를 orchestrator preview에 입력하면 표에 `prompt/feature/action/api/status/missing` 모두 채워진다.
- [x] failure 케이스(`needs_input` / `blocked` / `error`)에서 cause·missing slot이 카드 상단 배너로 보인다.
- [x] Inform Log / SplitTable / FileBrowser 의 unit action(각 feature md의 Agent Driver Contract 참조)이 호출 가능하고, 응답 카드가 Agent 탭 안에서 인라인 결과로 보인다.
- [x] Linux 사내 환경(`/config/work/sharedworkspace/...` 마운트)에서도 hardcoded 경로/URL 의존 없이 동일하게 동작한다 (`scripts/preflight_internal.py --write-probe` pass).

## Verify

```bash
git diff --check
python3 scripts/smoke_test.py
python3 scripts/agent_scenario_check.py
```
