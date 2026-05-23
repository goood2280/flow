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
GET  /api/ai-hub/board                      운영 보드: semantic 제안 + skill 후보 + workflow + 비활성 도구
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
# 34 passed
```

엔드 투 엔드 시나리오 (Plan 의 Verification 절 참조):
1. AI 허브 → 도구 27개 표시 + on-off + 30일 호출수
2. SQL 작업대 → 2 view + final JOIN → 정확한 결과 + 차트 + 스킬로 저장
3. AI 허브 홈 에이전트 패널 → "PROD_A root_lot 별 ET 평균" prompt → trace 표
4. 동일 시퀀스를 2명이 3회 반복 → "💡 스킬" 후보 자동 등장 → admin 승인 → 정식 스킬
5. `/api/llm/flowi/chat` 직접 호출 시 v9.0.4 와 동일 응답 (회귀 0)
