# Agent Semantic Layer

Agent semantic layer는 사용자의 자연어 목표와 Flow의 실제 DB/API/unit runtime 사이에 있는 해석층이다. 이 문서는 구현자가 Agent가 어떤 semantic source를 읽고, 어떤 API로 관리하며, 어떤 경우에 write를 금지해야 하는지 확인하는 기준이다.

## Position

Agent의 기본 흐름은 아래처럼 본다.

```text
goal/prompt -> semantic_layer -> unit runtime -> result/trace
```

- `goal/prompt`: Home Flow-i 또는 Agent unit tab에서 들어온 사용자 요청.
- `semantic_layer`: prompt의 용어, column, slot, meeting reference, rulebook hint를 Flow 내부 canonical 표현으로 정리한다.
- `unit runtime`: FileBrowser AI SQL, Inform registration, Change management 같은 단위기능 AI가 자기 owner API와 data contract를 지키며 실행한다.
- `result/trace`: 공개 가능한 `semantic_frame`, node trace, warning, answer만 화면에 노출한다. 내부 추론 원문과 raw preview row dump는 semantic layer의 책임이 아니다.

Semantic layer는 source data를 직접 수정하는 실행기가 아니다. DB/CSV/Parquet write, Inform 저장, Meeting/Calendar 수정은 각 feature owner API가 명시 action과 권한 확인 뒤 수행한다.

## Runtime Concepts

| 개념 | 의미 | 저장 위치 | write 규칙 |
|---|---|---|---|
| `alias_groups` | 사용자가 쓰는 표현과 canonical key를 묶는 사전. 예: `IOFF`, `LKG` 같은 별칭을 내부 column/slot 의미로 연결한다. | `FLOW_DATA_ROOT/semantic/alias_groups.json` | 명시 save API 또는 승인된 proposal decision만 가능 |
| `intent_hints` | 특정 intent가 요구하는 canonical slot/key 목록. Inform registration 같은 slot-fill unit이 required hint로 참고한다. | `FLOW_DATA_ROOT/semantic/intent_hints.json` | 명시 save API만 가능 |
| `proposal queue` | meeting/inform/tracker/activity log 등에서 나온 새 용어 후보의 pending queue. | `FLOW_DATA_ROOT/semantic/proposals/*.json` | enqueue는 proposal producer, approve/reject는 권한 있는 사용자만 가능 |
| `changes` | alias/intent write audit log. | `FLOW_DATA_ROOT/semantic/changes.jsonl` | semantic lexicon service가 append |
| `semantic_frame` | 실행 1회에서 prompt를 어떻게 해석했는지 담는 공개 trace payload. | runtime response 또는 unit history | 실행 결과로만 생성. shared semantic JSON write와 별개 |

`effective` view는 in-code seed와 disk override를 merge한 결과다. 현재 Agent API는 `backend/app_v2/modules/semantic_lexicon/service.py`에서 disk value를 우선 적용한다.

런타임 resolver의 공유 진입점은 `backend/core/agent_semantic_service.py`의 `resolve(prompt, columns=None, product="", dtypes=None)`다. Unit runtime은 필요에 따라 기존 공개 frame shape만 골라 노출한다.

## Semantic APIs

| Endpoint | 용도 | 권한 | write 여부 |
|---|---|---|---|
| `GET /api/agent/semantic/lexicon` | effective/disk alias, intent, changes, pending proposals 조회 | 로그인 사용자 | 없음 |
| `POST /api/agent/semantic/draft` | 자연어 또는 JSON에서 alias/intent 초안 생성 | 로그인 사용자 | 없음 |
| `PUT /api/agent/semantic/alias-groups/{canonical}` | disk alias group 저장 | admin 또는 `agent`/`diagnosis`/`knowledge` page manager | `alias_groups.json`, `changes.jsonl` |
| `DELETE /api/agent/semantic/alias-groups/{canonical}` | disk alias group 삭제 | admin 또는 `agent`/`diagnosis`/`knowledge` page manager | `alias_groups.json`, `changes.jsonl` |
| `PUT /api/agent/semantic/intent-hints/{intent}` | disk intent hint 저장 | admin 또는 `agent`/`diagnosis`/`knowledge` page manager | `intent_hints.json`, `changes.jsonl` |
| `DELETE /api/agent/semantic/intent-hints/{intent}` | disk intent hint 삭제 | admin 또는 `agent`/`diagnosis`/`knowledge` page manager | `intent_hints.json`, `changes.jsonl` |
| `GET /api/agent/semantic/proposals` | proposal queue 조회 | 로그인 사용자 | 없음 |
| `POST /api/agent/semantic/proposals/{id}/decision` | proposal approve/reject | admin 또는 `agent`/`diagnosis`/`knowledge` page manager | proposal status, 승인 시 alias group |

`draft`는 항상 read-only다. 초안 응답에 `alias_groups`나 `intent_hints`가 있어도 disk에는 저장하지 않는다. 저장은 사용자가 `PUT` API를 호출하거나 pending proposal을 승인할 때만 가능하다.

## Guardrails

- shared semantic write는 기본적으로 approval/save 이후만 허용한다.
- Agent unit runtime은 source DB/CSV/Parquet를 semantic layer에서 직접 수정하지 않는다.
- proposal approve는 alias 추가까지 가능하지만, DB source, Inform, Meeting, Calendar, Tracker data를 바꾸지 않는다.
- Inform registration은 confirm 전에는 `FLOW_DATA_ROOT/informs/informs.json`을 쓰지 않는다.
- FileBrowser AI SQL preview는 read-only다. SQL 적용은 preview endpoint 재호출이며 LLM 재실행이나 원본 저장이 아니다.
- Change management는 Meeting/Calendar visible data를 읽어 답변하고, 회의나 일정 데이터를 쓰지 않는다.
- prompt에서 추출한 값과 `slot_overrides`가 semantic hint보다 강하다. semantic hint는 후보/보조 정보로 취급한다.

## Unit Usage

### FileBrowser AI SQL

위치:

```text
context_sample -> semantic_layer -> filter_draft -> column_draft -> merge -> preview_apply
```

- `context_sample`은 FileBrowser source of truth에서 schema와 compact `sample_profile`을 얻는다.
- `semantic_layer`는 공유 `agent_semantic_service.resolve()` 결과에서 기존 shape인 `resolved_columns`, `unknown_column_terms`, `value_terms`, `synonyms`, `step_mapping`을 `semantic_frame`에 넣는다.
- `filter_draft`와 `column_draft`는 `semantic_frame`을 참고하지만 SQL validation, selected column validation, preview는 FileBrowser helper를 재사용한다.
- root/file/schema 해석의 source of truth는 `backend/routers/filebrowser.py`와 `backend/core/flowi_units/filebrowser_ai_sql_runtime.py`다.
- Agent history에는 preview row 전체를 싣지 않는다. preview table은 사용자가 `적용`을 눌러 FileBrowser preview endpoint를 다시 호출한 결과만 보여준다.

### Inform Registration

위치:

```text
context_seed -> semantic_layer -> slot_extract -> validate_missing -> snapshot_preview -> review -> register
```

- `semantic_layer`는 공유 `agent_semantic_service.resolve()` 결과에서 alias hit, slot hint, unknown term, matched intent를 공개 trace에 남긴다.
- 필수 slot은 `product`, 단일 `lot_id`, `module`, `note`, mail target이다.
- slot merge는 short-memory state와 semantic hint를 먼저 깔고, prompt에서 명확히 추출된 값과 explicit `slot_overrides`가 덮어쓴다.
- `continue`는 `FLOW_DATA_ROOT/agent_unit_ai_sessions/inform_registration/*.json` short memory만 갱신한다.
- `confirm`은 누락값이 없을 때만 `backend/routers/informs.py`의 `create_inform()` 경로로 저장한다.

### Change Management

위치:

```text
context_scope -> meeting_reference -> evidence_pack -> answer_compose
```

- 이 unit의 semantic phase는 shared lexicon write가 아니라 visible Meeting/Calendar 범위 안에서 회의명과 변경점 맥락을 해석하는 단계다.
- `meeting_reference`는 후보가 여러 개이면 추측하지 않고 clarification 상태를 반환한다.
- `evidence_pack`은 agenda, minutes, decision, action item, change-management calendar event summary만 근거로 묶는다.
- `answer_compose`는 plain text 답변을 만든다. 근거가 없으면 없다고 답하고 markdown decoration은 제거한다.
- 실행 이력만 `FLOW_DATA_ROOT/agent_unit_ai_sessions/change_management/history.jsonl`에 append한다.

### Dashboard Agent

위치:

```text
semantic_layer -> chart_intent -> chart_type_select -> params_fill -> spec_validate -> render_spec
```

- `semantic_layer`는 공유 `agent_semantic_service.resolve()`를 호출한다.
- 입력은 `{natural_language, columns, sample_rows}`이며 source root/file에는 의존하지 않는다.
- 출력은 기존 `chart_result` shape를 유지해 `PlotlyChart.jsx`가 그대로 받을 수 있어야 한다.

## DB And File References

### `FLOW_DB_ROOT`

`FLOW_DB_ROOT`는 운영 DB/source root다. `backend/core/roots.py`와 `backend/core/paths.py`가 env, runtime admin setting, shared default, local fallback 순서로 해석한다. Agent는 이 root의 source file을 read-mostly source로 보고, write는 owner 기능의 명시 API에 맡긴다.

| Source | 의미 | 경로 패턴 | 읽는 코드 | owner | write 가능 여부 | Agent가 참고할 때 |
|---|---|---|---|---|---|---|
| FAB raw parquet | lot/wafer의 현재 FAB 진행, step, equipment, time source | `FLOW_DB_ROOT/1.RAWDATA_DB_FAB/<PRODUCT>/**/*.parquet` 또는 설정된 FAB root | `backend/core/lot_progress_cache.py`, `backend/routers/filebrowser.py`, `backend/routers/splittable.py` | DB ops, FileBrowser/SplitTable read path | Agent 직접 write 금지 | lot progress, latest step, SplitTable override, Tracker/Inform 후보 설명 |
| ET raw parquet | ET measurement, trend/RCA용 numeric source | `FLOW_DB_ROOT/1.RAWDATA_DB_ET/<PRODUCT>/**/*.parquet` | `backend/routers/filebrowser.py`, chart/Home/FileBrowser query path | DB ops, FileBrowser | Agent 직접 write 금지 | ET metric alias, trend/filter SQL, measurement evidence |
| INLINE raw parquet | INLINE process measurement source | `FLOW_DB_ROOT/1.RAWDATA_DB_INLINE/<PRODUCT>/**/*.parquet` | `backend/routers/filebrowser.py`, `backend/routers/splittable.py` | DB ops, FileBrowser/SplitTable | Agent 직접 write 금지 | INLINE prefix rows, source matching explanation |
| VM raw parquet | VM process/item measurement source | `FLOW_DB_ROOT/1.RAWDATA_DB_VM/<PRODUCT>/**/*.parquet` | `backend/routers/filebrowser.py`, `backend/routers/splittable.py` | DB ops, FileBrowser/SplitTable | Agent 직접 write 금지 | VM prefix rows, step/item matching explanation |
| ML_TABLE product file | SplitTable의 제품 단위 wafer-level working table | `FLOW_DB_ROOT/ML_TABLE_<PRODUCT>.parquet` | `backend/routers/splittable.py`, `backend/routers/filebrowser.py` | SplitTable/FileBrowser | FileBrowser/SplitTable의 명시 save/admin 경로만 가능 | FileBrowser AI SQL target, SplitTable product/source 설명 |
| LOT progress cache parquet | root lot + wafer 기준 latest lot/step cache | `FLOW_DB_ROOT/cache/lot_progress_latest_lot_by_root_wafer.parquet` | `backend/core/lot_progress_cache.py`, `backend/routers/filebrowser.py`, `backend/routers/splittable.py` | lot progress cache job | cache refresh 경로만 write | hot path에서 latest lot/step을 설명하거나 후보를 좁힐 때 |
| Matching/rulebook CSV | KNOB/INLINE/VM step_desc, step_id, function step, ppid rulebook | `FLOW_DB_ROOT/Vehicle_matching.csv`, `FLOW_DB_ROOT/step_matching.csv`, `FLOW_DB_ROOT/ppid_knob.csv`, related rulebook CSV | `backend/routers/splittable.py`, `backend/core/lot_progress_cache.py`, `backend/routers/filebrowser.py` | SplitTable rulebook/FileBrowser admin | manager/admin rulebook 또는 base-file save 경로만 가능 | step/function alias, KNOB rule, split-check explanation |
| FileBrowser root file | DB root-level single parquet/csv/yaml/json file | `FLOW_DB_ROOT/<file>` | `backend/routers/filebrowser.py` | FileBrowser | FileBrowser base-file edit/version API만 가능 | user selected `rootpq`/`base` target의 schema/profile |

Home Flow-i의 `/api/llm/flowi/chat`은 matching/rulebook CSV를 read-only evidence로 조회할 수 있다. 이 경로는 등록된 schema catalog/single-file source를 먼저 보고, 없으면 DB root의 `Vehicle_matching.csv`, `step_matching.csv`, `matching_step.csv`, `ppid_knob.csv` fallback을 읽어 `step_id`, `function_step`/`step_desc`, `feature_name` 연결만 공개 trace에 남긴다.

### `FLOW_DATA_ROOT`

`FLOW_DATA_ROOT`는 runtime/user state root다. 코드 업데이트나 Agent semantic draft가 이 파일들을 임의로 덮어쓰면 안 된다.

| Source | 의미 | 경로 패턴 | 읽는 코드 | owner | write 가능 여부 | Agent가 참고할 때 |
|---|---|---|---|---|---|---|
| Semantic lexicon | shared alias/intent override와 audit | `FLOW_DATA_ROOT/semantic/alias_groups.json`, `intent_hints.json`, `changes.jsonl` | `backend/app_v2/modules/semantic_lexicon/`, `backend/routers/agent.py` | Agent Semantic layer | semantic writer API만 가능 | prompt 용어를 canonical key/slot hint로 정규화할 때 |
| Semantic proposals | 새 용어 후보 queue | `FLOW_DATA_ROOT/semantic/proposals/*.json` | `backend/app_v2/modules/semantic_learning/`, `backend/routers/agent.py` | Agent Semantic layer | enqueue 또는 approve/reject API만 가능 | unknown term을 바로 저장하지 않고 검토 queue로 보낼 때 |
| Agent unit sessions | unit별 실행 session/history | `FLOW_DATA_ROOT/agent_unit_ai_sessions/inform_registration/*.json`, `change_management/history.jsonl` | `backend/core/flowi_units/*_runtime.py` | 각 unit runtime | unit runtime만 write | short memory, 실행 이력, 재현 가능한 public trace |
| Home Flow-i runs | Home prompt의 공개 runtime graph snapshot | `FLOW_DATA_ROOT/home_agent_runs/*.json` | `backend/core/home_orchestrator.py`, `backend/routers/agent.py` | Home Flow-i | Home orchestrator만 write | Agent `Flow-i` tab에서 Home 실행 흐름을 관찰할 때 |
| Home Flow-i memory | 사용자별 최근 prompt/answer와 공개 tool summary | `FLOW_DATA_ROOT/home_agent_memory/conversation.jsonl` | `backend/core/home_memory.py`, `backend/routers/llm.py`, `backend/core/home_orchestrator.py` | Home Flow-i | Home Flow-i 응답 종료 시 append | 새로고침/외부 호출 후에도 후속 질문 context를 이어받을 때 |
| Inform runtime data | Inform rows, mail draft, audit 흐름 | `FLOW_DATA_ROOT/informs/informs.json` and related Inform files | `backend/routers/informs.py`, `backend/core/flowi_units/inform_registration_runtime.py` | Inform | Inform owner API만 가능 | Inform registration confirm 전/후 상태와 권한 설명 |
| Meeting/Calendar runtime data | 회의 agenda/minutes/decision/action item, calendar event | `FLOW_DATA_ROOT/meetings/*.json`, `FLOW_DATA_ROOT/calendar/events.json` | `backend/routers/meetings.py`, `backend/core/flowi_units/change_management_runtime.py` | Meeting/Calendar | Meeting/Calendar owner API만 가능 | Change management answer evidence |
| Tracker runtime data | issue, category, comment, lot status cache | `FLOW_DATA_ROOT/tracker/*.json` and tracker cache files | `backend/routers/tracker.py`, `backend/core/lot_progress_cache.py` | Tracker | Tracker owner API만 가능 | issue/lot context를 근거로 보여줄 때 |
| FileBrowser AI SQL history/feedback | SQL draft 이력, feedback, replay metadata | `FLOW_DATA_ROOT/filebrowser_ai_sql_history.jsonl`, `filebrowser_ai_sql_feedback.jsonl` | `backend/routers/filebrowser.py`, `backend/routers/agent.py`, `backend/core/home_orchestrator.py` | FileBrowser | FileBrowser/Agent runtime history 경로만 append | Agent unit 질문 이력, answer provenance, debug request |
| FileBrowser settings | preview/cache/csv rule/versioned dir 설정 | `FLOW_DATA_ROOT/filebrowser_settings.json` | `backend/routers/filebrowser.py` | FileBrowser | FileBrowser settings API만 가능 | schema/profile limits, CSV validation, preview cap 설명 |

## Code Entrypoints

| Layer | Path |
|---|---|
| Agent semantic API | `backend/routers/agent.py` |
| Shared semantic resolver | `backend/core/agent_semantic_service.py` |
| Semantic lexicon storage/service | `backend/app_v2/modules/semantic_lexicon/` |
| Semantic proposal queue/classifier | `backend/app_v2/modules/semantic_learning/` |
| Agent runtime semantic seed placeholder | `backend/app_v2/modules/agent_runtime/semantic.py` |
| Unit metadata and `DataSourceRef` | `backend/core/flowi_units/base.py`, `backend/core/flowi_units/*.py` |
| FileBrowser AI SQL runtime | `backend/core/flowi_units/filebrowser_ai_sql_runtime.py` |
| Inform registration runtime | `backend/core/flowi_units/inform_registration_runtime.py` |
| Change management runtime | `backend/core/flowi_units/change_management_runtime.py` |
| Dashboard Agent runtime | `backend/core/flowi_units/dashboard_agent_runtime.py` |
| DB/data root resolution | `backend/core/roots.py`, `backend/core/paths.py` |
| FileBrowser source of truth | `backend/routers/filebrowser.py` |
| SplitTable rulebook/source matching | `backend/routers/splittable.py` |
| LOT progress cache | `backend/core/lot_progress_cache.py` |

## Validation

문서 변경만 했을 때의 최소 검증:

```bash
git diff --check -- docs/AGENT_FLOW_CONTEXT.md docs/features/README.md docs/features/flowi-agent.md docs/features/agent-semantic-layer.md
python3 -m pytest tests/test_feature_contracts.py
python3 -m pytest tests/agent/test_agent_semantic_service.py
```
