# AI 허브 — 단위 AI 카탈로그 + 홈 에이전트 + 스킬 (v9.1.0)

flow 본진에 흩어져 있던 두 단계 추상화를 한 화면에서 보고·관리하기 위한 layer.
기존 dispatch/라우팅은 손대지 않고 **읽기 전용 카탈로그**만 얹는다.

## 두 단계 추상화

| 단계 | 개수 | 어디 | 의미 |
|---|---|---|---|
| **Unit AI** | 11 | `core/flowi_units/` | Feature 단위 (filebrowser / meeting / inform / tracker / dashboard / splittable / tablemap / diagnosis / calendar / ettime / waferlayout) |
| **Function-call** | 16 | `routers/llm.py` 의 `FLOWI_FUNCTION_FEW_SHOTS` + `_flowi_function_schema` | LLM function-calling 단위 (query_*, register_*, preview_*, build_*, ...) |

`core/tool_registry.py` 가 두 추상화를 lazy import 로 합쳐 단일 카탈로그를 반환한다. 즉:

- 신규 Unit AI 추가 → `flowi_units/<key>.py` 하나 만들면 자동 카드 등장
- 신규 Function 추가 → `llm.py` 의 schema/few-shot 둘 다 등록하면 자동 카드 등장
- 각 도구 상세는 `management_flow` 와 `knowledge_refs` 를 같이 반환해 Prompt → Policy Gate → Execute → Wiki/Schema → Improve 흐름과 Wiki/Graph/schema 참조를 한 화면에서 확인한다.

상태 (enabled toggle, by, ts) 는 `data_root/tool_registry_state.json` 에 저장 — **admin_settings.json 미사용**.

## 라우터

```
GET  /api/ai-hub/tools                      통합 카탈로그 + 30일 호출수
GET  /api/ai-hub/tools/{name}               단일 도구 상세
GET  /api/ai-hub/tools/{name}/history       최근 호출 이력
GET  /api/ai-hub/tags                       태그 목록 (필터용)
GET  /api/ai-hub/board                      운영 보드: semantic 제안 + skill 후보 + workflow + 비활성 도구 + 승인/거부/활성화 action metadata
GET  /api/ai-hub/readiness                  운영 준비도 점수 + 개선 백로그
GET  /api/ai-hub/deep-eval-report           Agent semantic/wiki/sql deep-eval 최신 리포트
POST /api/ai-hub/deep-eval-report/run       최신 deep-eval 리포트 재생성 (admin)
GET  /api/ai-hub/workflow-map               n8n/Obsidian식 Prompt→Policy→Tool→Wiki/Schema→Improve 운영 지도
GET  /api/ai-hub/workflow-map/export        format=n8n|obsidian → 운영 지도 export JSON
POST /api/ai-hub/readiness/bootstrap-workflows  시작 shared workflow 템플릿 생성 (admin)
POST /api/ai-hub/tools/{name}/toggle        enabled on/off (admin)
POST /api/home-agent/orchestrate            { prompt } → trace + reply
POST /api/sql-workspace/run                 cells 실행 → result + 셀 trace
POST /api/sql-workspace/save-skill          cells → Skill 저장
GET  /api/skills/list                       정식 Skill 목록
GET  /api/skills/candidates                 Skill 후보 목록
POST /api/skills/candidates/{key}/approve   후보 → 정식 (admin)
POST /api/skills/candidates/{key}/reject    후보 거부 (admin)
POST /api/skills/mine                       즉시 마이닝 (admin)
```

## 홈 에이전트 오케스트레이터

`core/home_orchestrator.py` 는 자연어 prompt 를 받아 다음 흐름으로 도구를 선택·실행한다.

1. 한국어/영어 키워드 → 태그 가중치 (예: "차트" → chart+2.5, "lot" → lot+2.0)
2. ToolRegistry 의 모든 enabled 도구에 점수 부여
3. 상위 `top_k` (기본 2) 만 실행
4. trace 각 row: `{tool, kind, title, score, confidence, ok, ms, result_preview}`

LLM function-calling 미지원 모델(GPT-OSS-120B 등) 환경에서도 휴리스틱만으로 동작.
`unit_ai` 는 기존 `flowi_units/dispatcher.try_dispatch(only=[name])` 위임 — 회귀 0.
`function-call` 단위는 trace stub 만 (실행은 기존 `/api/llm/flowi/chat` 경로 사용).

## 워크플로우 지도

`core/ai_hub_workflow_map.py` 는 기존 도구 카탈로그와 저장된 workflow template을 읽어서 관리 지도를 만든다. 새 저장소를 만들지 않고 `tool_registry.list_tools()`의 `management_flow`, `knowledge_refs`, 호출 통계와 `flowi_workflow_templates`의 own/shared template을 합쳐 다음 노드를 노출한다.

- `stage:*`: Prompt / Policy / Unit-Function / Wiki-Schema / Improve 단계
- `workflow:<key>`: 저장된 Agent workflow template. trigger 조건, step 목록, 최근 dry-run/execute count/status를 보여주고 각 step의 `unit_ai`를 도구 노드에 연결한다. 노드 detail에서는 `/api/agent/workflows/execute` dry-run action으로 실제 실행 전 guardrail/step 상태를 확인할 수 있다.
- `workflow_step:<key>:<n>`: workflow template의 개별 실행 step. `unit_ai`, `action`, bind/fixed slot을 독립 노드로 보여주고 실제 도구 노드로 이어진다.
- `tool:<name>`: Unit AI 또는 function-call 도구, enabled 상태와 최근 호출수
- `deep_eval:latest`: 최신 Agent deep-eval 리포트. semantic/wiki/sql 검증 통과 수와 실패 assertion 상태를 Improve 단계의 evidence로 연결하고, 노드 detail에서 admin 재검증 action을 실행할 수 있다.
- `wiki:*`, `relation:*`, `column:*`, `arg:*`, `feature:*`: Agent Wiki, schema relation, column catalog, function 입력 스키마, 기능 문서 근거

AI Hub 화면의 `워크플로우 지도` 패널은 태그별 focus filter와 노드 detail을 제공한다. 운영자는 n8n처럼 반복 prompt template → policy gate → unit/function step 흐름을 보고, Obsidian처럼 도구가 어떤 지식/스키마에 연결되는지 확인한다.

지도는 저장성 export 없이 즉석 산출물로도 꺼낼 수 있다.

- `format=n8n`: n8n sticky-note workflow JSON. Flow 내부 실행을 외부 자동화로 우회하지 않고, 운영 리뷰/설계용 노드와 connection만 내보낸다.
- `format=obsidian`: Obsidian vault에 넣을 수 있는 Markdown note 묶음 JSON. index note와 `nodes/*.md` note가 wiki-link로 서로 연결된다.

## 운영 준비도

`core/ai_hub_readiness.py` 는 운영자가 다음 개선 대상을 놓치지 않도록 기존 보드, 워크플로우 지도, Agent deep-eval 최신 리포트를 합쳐 점수와 backlog를 만든다. 새 저장소를 만들지 않고 `tool_registry_state`, semantic proposal queue, skill candidates, workflow templates, workflow-map warnings, `data_root/reports/flowi_agent_deep_eval_latest.json`만 읽는다. `core/ai_hub_board.py` 는 workflow dry-run/execute 감사 로그(`ai_hub_run:workflow:<key>`)도 읽어서 최근 검증 이력을 운영 보드에 표시한다.

- 점수 축: 도구 활성도, Wiki/schema grounding, semantic/skill 승인 큐, workflow/skill 자산, workflow dry-run/execute 검증 coverage, Agent deep-eval 통과/최신성
- backlog: 비활성 도구, 지식 근거가 빈 도구, semantic 승인 대기, skill 후보, workflow/skill 부재, 비어 있거나 step 정의가 불완전한 workflow template, 최근 검증이 없거나 warning이 있는 workflow, deep-eval 리포트 누락/손상/실패/오래됨
- 처리 액션: admin은 readiness backlog에서 비활성 도구 활성화, semantic 제안 승인/거부, skill 후보 승인/거부, workflow Dry-run 재검증, deep-eval 리포트 재생성을 바로 실행할 수 있다. 실제 권한은 각 기존 endpoint가 다시 검증한다.
- workflow 자산이 비어 있으면 admin은 `시작 템플릿 생성`으로 공유 starter workflow 3개 (`LOT 현재 step`, `KNOB lot_wf 영향`, `Inform 초안 전 검토`)를 idempotent하게 생성할 수 있다.
- AI Hub 화면의 `운영 준비도` 패널은 score, check별 점수, 상위 개선 항목을 한눈에 보여준다.
- AI Hub 화면의 `Agent 검증 리포트` 패널은 `data_root/reports/flowi_agent_deep_eval_latest.json` 을 읽어서 semantic/knowledge/sql/meta 그룹별 deep-eval 통과 수와 실패 assertion을 보여준다. admin은 같은 패널의 `검증 실행` 또는 readiness backlog action으로 최신 리포트를 재생성할 수 있다.

## SQL 작업대 — 멀티 셀 조인

```
┌ Cell view 1: lot_meta ───────────────┐
│ SELECT lot_id, root_lot_id, ...      │
└──────────────────────────────────────┘
┌ Cell view 2: et_results ─────────────┐
│ SELECT lot_id, value FROM ...        │
└──────────────────────────────────────┘
┌ Cell FINAL (name 없음) ──────────────┐
│ SELECT m.root_lot_id, AVG(e.value)   │
│ FROM lot_meta m JOIN et_results e... │
│ GROUP BY 1                           │
└──────────────────────────────────────┘
```

- 각 view 셀은 `CREATE OR REPLACE TEMP VIEW <name> AS <sql>` 으로 등록
- 마지막 셀만 결과 fetch → 표 + 간단 SVG 차트 (막대/선) + 값 찾기 필터
- 위험 키워드 14종 차단 (DROP/INSERT/ATTACH/COPY/PRAGMA/...)
- `read_parquet/read_csv_auto` 경로는 `db_root`/`base_root`/`data_root`/`cache_dir` 하위만 허용
- Row 상한 default 5000, max 50000
- `▶ 스킬로 저장` 으로 cells 시퀀스를 `data_root/skills/<key>.json` 에 저장

## 스킬 마이너

`core/skill_miner.py`:
- `activity.jsonl` 의 도구 호출 prefix (`tool:`, `unit_ai:`, `home_agent:`, `filebrowser_sql:`, `ai_hub_run:`, `flowi_function:`) 를 인식
- workflow 실행 이력은 `ai_hub_run:workflow:<key>` 형태로 template key까지 보존해서 workflow별 반복 검증/실행 패턴을 별도 skill 후보로 모은다.
- 같은 user 의 연속 이벤트를 시간 window (기본 5분) 로 묶음
- 도구 호출 시퀀스 시그너처 = 정규화된 action 의 tuple
- freq ≥ 3 + users ≥ 2 만족 → 후보 등록

후보는 AI 허브의 💡 스킬 패널에서 admin 이 승인/거부.
승인된 스킬은 `data_root/skills/<key>.json` 으로 이동되며 `source: "skill_miner"` 표식이 붙는다.

## 검증

```
cd flow
python -m pytest tests/test_tool_registry.py tests/test_sql_workspace.py \
                  tests/test_home_orchestrator.py tests/test_skill_miner.py -v
python3 -m pytest tests/test_ai_hub_readiness.py tests/test_ai_hub_workflow_map.py tests/test_ai_hub_board.py -q
python3 scripts/flowi_agent_deep_eval.py --report-json
python3 -m py_compile backend/core/ai_hub_readiness.py backend/core/ai_hub_workflow_map.py backend/routers/ai_hub.py
```

`flowi_agent_deep_eval.py`는 Agent 단어 인식, Agent Wiki upsert/search, SQL Workspace multi-view join 정답을 함께 검증한다. `--report-json` 을 경로 없이 실행하면 AI Hub가 읽는 `data_root/reports/flowi_agent_deep_eval_latest.json` 을 갱신하고, 결과에는 semantic/knowledge/sql/meta 그룹별 통과 수와 전체 assertion detail이 담긴다.

엔드 투 엔드 시나리오 (Plan 의 Verification 절 참조):
1. AI 허브 → 도구 27개 표시 + on-off + 30일 호출수
2. SQL 작업대 → 2 view + final JOIN → 정확한 결과 + 차트 + 스킬로 저장
3. AI 허브 홈 에이전트 패널 → "PROD_A root_lot 별 ET 평균" prompt → trace 표
4. 동일 시퀀스를 2명이 3회 반복 → "💡 스킬" 후보 자동 등장 → admin 승인 → 정식 스킬
5. `/api/llm/flowi/chat` 직접 호출 시 v9.0.4 와 동일 응답 (회귀 0)
