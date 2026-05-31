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
| `alias_groups` | 사용자가 쓰는 표현과 canonical key를 묶는 사전. 예: `IOFF`, `LKG` 같은 별칭을 내부 column/slot 의미로 연결한다. 값은 기존 `["alias"]` 형식과 신규 `{aliases, semantic_class, normalization, value_domain}` 형식을 모두 읽는다. | `FLOW_DATA_ROOT/semantic/alias_groups.json` | 명시 save API 또는 승인된 proposal decision만 가능 |
| `intent_hints` | 특정 intent가 요구하는 canonical slot/key 목록. Inform registration 같은 slot-fill unit이 required hint로 참고한다. | `FLOW_DATA_ROOT/semantic/intent_hints.json` | 명시 save API만 가능 |
| `proposal queue` | meeting/inform/tracker/activity log 등에서 나온 새 용어 후보의 pending queue. | `FLOW_DATA_ROOT/semantic/proposals/*.json` | enqueue는 proposal producer, approve/reject는 권한 있는 사용자만 가능 |
| `changes` | alias/intent write audit log. | `FLOW_DATA_ROOT/semantic/changes.jsonl` | semantic lexicon service가 append |
| `source_catalog` | Agent가 semantic source search와 unknown-term priority에 쓰는 source 목록. 기본 seed는 code에 있고 운영 override/add/delete는 data-root에 저장한다. | `backend/core/semantic_source_catalog.py`, `FLOW_DATA_ROOT/semantic/source_catalog.json`, `docs/semantic/<id>.md` | Semantic layer save API만 가능. source raw data write 금지 |
| `measurement_terms` | `CA BCD`, `PCCB Chain`처럼 사용자가 부르는 측정 이름을 source_type/product/step_id/item_id/spec/evidence로 연결하는 사전. | `FLOW_DATA_ROOT/semantic/measurement_terms.json` | Semantic layer save API만 가능. 변경 근거는 measurement change log와 Change management history에 append |
| `semantic_frame` | 실행 1회에서 prompt를 어떻게 해석했는지 담는 공개 trace payload. | runtime response 또는 unit history | 실행 결과로만 생성. shared semantic JSON write와 별개 |

`effective` view는 in-code seed와 disk override를 merge한 결과다. 현재 Agent API는 `backend/app_v2/modules/semantic_lexicon/service.py`에서 disk value를 우선 적용한다.

런타임 resolver의 공유 진입점은 `backend/core/agent_semantic_service.py`의 `resolve(prompt, columns=None, product="", dtypes=None, sample_profile=None, source_ref=None)`다. Unit runtime은 필요에 따라 기존 공개 frame shape만 골라 노출한다.

`semantic_frame`은 기존 `resolved_columns`, `value_terms`, `synonyms`, `step_mapping`, `alias_hits`, `slot_hints`를 유지하면서 아래 additive 필드를 더한다.

- `alias_group_meta`: canonical별 `semantic_class`, `normalization`, `value_domain`
- `value_catalog_matches`: FileBrowser owner read path로 즉석 조회한 실제 distinct/sample value hit
- `source_catalog_matches`: code catalog가 매칭한 semantic source와 docs link
- `measurement_term_matches`: 측정 용어 catalog가 매칭한 source_type, product, step_id, item_id, spec/evidence 후보
- `unknown_terms`: `{term, search_priority:[{location, table_file, confidence}]}` 구조의 미지어 탐색 우선순위
- `unknown_term_texts`: 기존 list 소비자를 위한 plain term 요약

## Semantic Source Catalog

PR2의 source catalog는 deterministic-first source selection을 위한 공용 메타데이터다. 기본 source는 `backend/core/semantic_source_catalog.py`에서 dict로 관리하고, 운영자가 수정/추가/삭제한 항목은 `FLOW_DATA_ROOT/semantic/source_catalog.json`에 override/tombstone으로 저장한다. 각 source 문서는 `docs/semantic/<id>.md`에 둔다.

| ID | Role | Source docs |
|---|---|---|
| `rulebook` | `ppid_knob.csv` rulebook rows for `feature_name` and PPID knob classification | `docs/semantic/rulebook.md` |
| `step_matching` | `Vehicle_matching.csv` step/function mapping, with `step_matching.csv` fallback | `docs/semantic/step_matching.md` |
| `split_base` | `ML_TABLE_<product>.parquet` SplitTable view and raw export source | `docs/semantic/split_base.md` |
| `fab_db` | FAB raw parquet for latest progress and current location evidence | `docs/semantic/fab_db.md` |
| `inline_db` | Inline measurement source for item/target/spec and trend/value lookup | `docs/semantic/inline_db.md` |
| `et_db` | ET measurement source for electrical-test item/target/spec and trend/value lookup | `docs/semantic/et_db.md` |

The catalog is source metadata only. It does not read source rows and does not decide PR3 deterministic routing by itself. Runtime code uses it to explain which source should be searched first when an unknown prompt term looks like step, knob, SplitTable, or FAB-progress language.

## Semantic APIs

| Endpoint | 용도 | 권한 | write 여부 |
|---|---|---|---|
| `GET /api/agent/semantic/lexicon` | effective/disk alias, intent, changes, pending proposals 조회 | 로그인 사용자 | 없음 |
| `GET /api/agent/semantic/sources` | source catalog, role index, and docs base 조회 | 로그인 사용자 | 없음 |
| `PUT /api/agent/semantic/sources/{id}` | source catalog 항목 저장 또는 추가. Body는 `source` object이며 role/roles/path_patterns/search_terms/docs_path 등을 포함할 수 있다. | admin 또는 `agent`/`diagnosis`/`knowledge` page manager | `source_catalog.json`, source catalog changes |
| `DELETE /api/agent/semantic/sources/{id}` | source catalog 항목 삭제. 기본 seed 삭제는 tombstone으로 보존되어 재병합되지 않는다. | admin 또는 `agent`/`diagnosis`/`knowledge` page manager | `source_catalog.json`, source catalog changes |
| `GET /api/agent/semantic/measurements` | measurement term catalog와 evidence/update metadata 조회 | 로그인 사용자 | 없음 |
| `POST /api/agent/semantic/draft` | 자연어 또는 JSON에서 alias/intent 초안 생성 | 로그인 사용자 | 없음 |
| `PUT /api/agent/semantic/measurements/{id}` | 측정 용어 mapping 저장. Body는 `term` object이며 source_type/product/step_id/item_id/target/spec/evidence를 포함할 수 있다. | admin 또는 `agent`/`diagnosis`/`knowledge` page manager | `measurement_terms.json`, measurement changes, Change management history |
| `DELETE /api/agent/semantic/measurements/{id}` | 측정 용어 mapping 삭제. 기본 seed 삭제는 tombstone으로 보존되어 재병합되지 않는다. | admin 또는 `agent`/`diagnosis`/`knowledge` page manager | `measurement_terms.json`, measurement changes, Change management history |
| `POST /api/agent/semantic/measurements/merge-defaults` | 기본 측정 용어 seed를 누락분만 병합 | admin 또는 `agent`/`diagnosis`/`knowledge` page manager | `measurement_terms.json` |
| `PUT /api/agent/semantic/alias-groups/{canonical}` | disk alias group 저장. Body는 `aliases`와 선택 메타 `semantic_class`, `normalization`, `value_domain`을 받을 수 있다. | admin 또는 `agent`/`diagnosis`/`knowledge` page manager | `alias_groups.json`, `changes.jsonl` |
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
- `semantic_layer`는 공유 `agent_semantic_service.resolve()` 결과에서 기존 shape인 `resolved_columns`, `unknown_column_terms`, `value_terms`, `synonyms`, `step_mapping`과 신규 `value_catalog_matches`, `source_catalog_matches`, 구조화된 `unknown_terms`를 `semantic_frame`에 넣는다.
- `filter_draft`와 `column_draft`는 `semantic_frame`을 참고하지만 SQL validation, selected column validation, preview는 FileBrowser helper를 재사용한다.
- `value_catalog_matches`는 선택된 FileBrowser source의 hot distinct/sample value를 즉석 조회한 read-only 결과다. 캐시는 process memory TTL만 사용하고 원본 DB/CSV/Parquet와 schema profile 파일은 쓰지 않는다.
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
- 입력은 `{natural_language, columns, sample_rows}`를 기본으로 한다. Home Agent가 source/chart 요청으로 Dashboard Agent를 선택하면 내부 source orchestration이 `root/product/file` 힌트를 처리한다.
- 출력은 기존 `chart_result` shape를 유지해 `PlotlyChart.jsx`가 그대로 받을 수 있어야 한다.
- prompt에 x/y축 컬럼이 명시됐는데 값이 비어 있거나 table columns/sample row에서 확인되지 않으면 chart를 만들지 않고 `needs_input` 질문으로 멈춘다.
- 단위기능 AI 화면의 Dashboard 질문 이력은 `FLOW_DATA_ROOT/agent_unit_ai_sessions/dashboard_agent/history.jsonl`에 prompt, columns, 실행 metadata, chart/trace summary만 저장한다. 원본 `sample_rows`, preview rows, chart points는 저장하지 않는다.

### Dashboard Agent Source Orchestration

위치:

```text
semantic_layer -> source_resolve -> filebrowser_sql_draft -> data_need_decision -> join_candidate_select -> join_plan_validate -> data_execute -> output_route -> dashboard_draft
```

- 별도 공개 Unit AI가 아니라 Dashboard Agent의 내부 data access path다. Home Agent가 Dashboard Agent를 뽑아 쓰면 이 graph를 실행하고, 결과 `chart_result`는 Home 화면에 바로 붙는다.
- source 선택은 explicit `root/product/file`을 우선하고, 자동 후보가 모호하면 `source_resolution.needs_input=true`와 후보 목록만 반환한다.
- SQL draft는 FileBrowser AI SQL runtime의 read-only `display_sql`, `where_sql`, `selected_columns`, `sort` 계약을 재사용한다.
- JOIN은 confirmed `schema_relations`가 있을 때만 실행한다. unconfirmed/draft relation은 `join_plan.blocked`로 끝난다.
- chart 생성은 `dashboard_agent` sub-runtime으로 위임하며 `chart_result.config.source_evidence`에 source ids, relation ids, join keys, SQL summary, sub-trace를 보존한다.

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
| Agent unit sessions | unit별 실행 session/history | `FLOW_DATA_ROOT/agent_unit_ai_sessions/inform_registration/*.json`, `change_management/history.jsonl`, `dashboard_agent/history.jsonl` | `backend/core/flowi_units/*_runtime.py` | 각 unit runtime | unit runtime만 write | short memory, 실행 이력, 재현 가능한 public trace |
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
| Semantic source catalog | `backend/core/semantic_source_catalog.py`, `docs/semantic/<id>.md` |
| Semantic lexicon storage/service | `backend/app_v2/modules/semantic_lexicon/` |
| Semantic proposal queue/classifier | `backend/app_v2/modules/semantic_learning/` |
| Agent runtime shared executor/prompt/validation | `backend/app_v2/modules/agent_runtime/` |
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
