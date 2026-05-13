# TODO

> `Now` 항목은 `(Claude)` / `(Codex)` 접두로 책임을 분리한다. Claude 세션은 진입점/문서/TODO/평가/Agent ↔ feature 명세를 유지하고, Codex CLI 세션은 frontend/backend 코드 자체 변경을 담당한다 (`AGENTS.md` Role Split 참조).

## Restart Plan

- 한 번에 한 항목만 `Now`로 올리고, 완료하면 검증 결과와 함께 `Done`으로 이동한다.
- 기능/API 변경은 관련 `docs/features/<feature>.md`와 endpoint shape를 같이 확인한다.
- UI-only 변경은 `git diff --check`와 `cd frontend && npm run build`를 기본 검증으로 둔다.
- backend/API/data 변경은 영향 범위에 맞춰 `python3 -m pytest tests` 또는 `python3 scripts/smoke_test.py`까지 실행한다.
- 현재 dirty runtime/cache 파일은 기존 세션 산출물로 보고, 해당 작업이 아니면 건드리지 않는다.

## Now

## Next

- [ ] (Codex) P10 Flow-i backend 구조 분리 — `backend/routers/llm.py`에서 unit action handler 한 묶음을 feature별 module로 추출하고 trace 계약을 유지한다.
- [ ] (Codex) P11 Admin / Dashboard 페이지 분해 — panel 하나씩 분리하고 기존 API 호출/권한을 유지한다.
- [ ] (Claude) P12 Agent 미션 정착 후 Diagnosis/Knowledge 본연 흐름 재정리 (`docs/features/diagnosis-knowledge.md` 우선순위 메모 해제).
- [ ] Keep feature docs current when page/API ownership changes.
- [ ] Regenerate `setup.py` with `_build_setup.py` after source or bundled doc changes.

## Done

- [x] (Codex) FileBrowser LOT 진행 캐시 source DB 톱니바퀴 설정화 — `settings.json.lot_progress_source_root`를 저장하고 scheduler/수동 refresh가 설정 DB root를 사용하도록 수정. `python3 -m pytest tests/test_lot_progress_cache.py tests/test_filebrowser_sql.py -q`, `python3 -m pytest tests -q`, `python3 -m py_compile _build_setup.py setup.py backend/core/lot_progress_cache.py backend/routers/filebrowser.py`, `git diff --check`, `cd frontend && npm run build`, `python3 _build_setup.py`, `python3 setup.py version` 통과.
- [x] (Codex) Version 표시 mtime 전환 및 SplitTable LOT 최신 캐시 표시 회귀 수정 — `/version.json`과 `setup.py version` 표시를 mtime 기준 시간 라벨로 분리하고, SplitTable 설정 패널의 자동/현재 적용 표시가 LOT 최신 캐시 hit를 우선 보여주도록 수정. `python3 -m py_compile setup.py backend/app.py backend/routers/splittable.py _build_setup.py`, `python3 setup.py version`, `python3 -m pytest tests/test_splittable_lot_candidates.py -q`, `git diff --check`, `cd frontend && npm run build` 통과.
- [x] (Codex) Tracker empty state ReferenceError 수정 — `My_Tracker.jsx`의 `EmptyState` import 누락을 복구. `git diff --check`, `cd frontend && npm run build` 통과.
- [x] (Codex) FileBrowser LOT 진행 캐시 사내 root 보정 — builder가 `1.RAWDATA_DB`를 우선 스캔하고 `1.RAWDATA_DB_FAB`를 fallback으로 유지하도록 수정, LLM cache refresh는 허용된 source_root 힌트만 전달하고 dataset 생성은 서버 builder가 수행하도록 정리. `python3 -m py_compile backend/core/lot_progress_cache.py backend/routers/filebrowser.py`, `python3 -m pytest tests/test_lot_progress_cache.py tests/test_filebrowser_sql.py -q`, `git diff --check`, `cd frontend && npm run build` 통과. `python3 scripts/smoke_test.py`는 로컬 8080 서버 미기동으로 로그인 전 단계에서 중단.
- [x] (Codex) Dashboard chart-only simplification — FAB 진행/알림 감시 UI 호출 제거, `+ 차트 추가` 단일 모달(일반 chart/Inform preset/AI draft) 통합, Flow-i chart `chart_config` 응답 alias와 GitHub main `setup.py` 재생성 절차 문서 반영. `git diff --check`, `cd frontend && npm run build`, `python3 -m py_compile backend/routers/llm.py _build_setup.py setup.py`, `python3 setup.py version` 통과. `python3 scripts/smoke_test.py`는 로컬 8080 서버 미기동으로 로그인 전 단계에서 중단.
- [x] (Codex) Dashboard 화면 정리 — 홈 Agent 연계 없이 LLM 상태와 group별 visible dashboard 요약을 추가하고, 상단 밀도 선택/그룹 숨김/카드 크기 조절 UI 및 기본 Analysis Workspace 노출을 제거. `git diff --check`, `cd frontend && npm run build` 통과. `python3 scripts/smoke_test.py`는 이 실행 샌드박스의 분리된 network namespace 때문에 8080 연결 거부로 중단.
- [x] (Codex) GitHub main 배포 인계 준비 — owner 요청에 따라 `setup.py`를 `_build_setup.py`로 재생성하고 `python3 setup.py version`, `python3 -m py_compile _build_setup.py setup.py`, `git diff --check`, 로컬 `main...origin/main` 0/0 확인 완료. commit/push는 Discord deploy agent에 남김.
- [x] (Codex) Meeting SSE `/api/meetings/stream` 404 회귀 수정 — static `/stream` 라우트를 `/{mid}`보다 먼저 등록하고 visibility helper 호출 시그니처를 보정. `setup.py` 재생성. `python3 -m pytest tests/test_meeting_mail_preview.py`, `python3 -m py_compile backend/routers/meetings.py`, 대상 경로 `git diff --check`, `cd frontend && npm run build`, `python3 -m pytest tests`, `python3 setup.py version` 통과. 전체 `git diff --check`는 기존 runtime `data/flow-data/flowi_users/hol.md` trailing whitespace로 실패.
- [x] (Codex) Flow UI 통합 디자인 시스템 적용 — `#E25822` 브랜드/시맨틱 토큰과 UXKit/global 공통 컴포넌트를 보강하고 Inform/Meeting/Calendar/Diagnosis/Tracker/App의 하드코드 색·표·pill/card 패턴을 정리. 대상 UI 파일 `git diff --check`, `cd frontend && npm run build`, `python3 -m pytest tests` 통과. 전체 `git diff --check`는 기존 runtime `data/flow-data/flowi_users/hol.md` trailing whitespace로 실패. `python3 scripts/smoke_test.py`는 로컬 서버 미기동으로 로그인 단계에서 중단.
- [x] (Codex) 최근 변경 코드 트림 — Agent legacy 중복 default/LLM panel export 제거, Inform 직접 `fetch()` 3건을 `sf()` helper로 전환, PageGearButton 사용부 inline handler 정리, `s3_sync` silent `pass`를 warning/debug log로 전환. 대상 경로 `git diff --check`, `cd frontend && npm run build`, `python3 -m py_compile backend/core/s3_sync.py backend/core/knowledge_vault.py backend/routers/agent.py backend/routers/filebrowser.py backend/routers/informs.py backend/routers/s3_ingest.py backend/routers/knowledge.py`, `python3 -m pytest tests/agent/test_agent_endpoints.py tests/test_filebrowser_sql.py tests/test_lot_progress_cache.py tests/test_s3_ingest_status.py tests/inform`, `python3 scripts/smoke_test.py` 통과. 전체 `git diff --check`는 기존 runtime `data/flow-data/flowi_users/hol.md` trailing whitespace로 실패.
- [x] (Codex) Knowledge Wiki + DB Schema 연결 1차 — `schema_doc` kind, `column_catalog`, 용어→컬럼 lookup/admin ingest endpoint, seed 문서/fixture를 추가. `python3 -m py_compile backend/core/knowledge_vault.py backend/routers/agent.py backend/routers/knowledge.py backend/app_v2/shared/contracts.py`, backend import, schema_doc lookup/draft shape, `python3 scripts/smoke_test.py`, frontend build 통과. 전체 `git diff --check`는 기존 `data/flow-data/flowi_users/hol.md` trailing whitespace로 실패했고, 변경 대상 경로 diff check는 통과.
- [x] (Codex) Flow 에이전트 탭 정돈 — Diagnosis Agent 탭을 Loop/Wiki/Schema/AI 연결 컴포넌트로 분리하고 dry-run thinking, schema scan 승인 UX, LLM 상태/설정, wiki graph를 구현. `npm run build`, `python3 -m py_compile backend/routers/llm.py backend/routers/agent.py backend/routers/knowledge.py`, `python3 scripts/smoke_test.py`, `python3 _build_setup.py && python3 setup.py version` 통과. 전체 `git diff --check`는 runtime hol.md trailing whitespace 때문에 실패, 변경 대상 경로 diff check는 통과.
- [x] (Codex) P9 FileBrowser 운영 개선 1차 — LOT 진행 캐시 lock/log/status, S3 자동 업로드 토글, version/컬럼/AI SQL UX, 캐시 cleanup API, S3 방향별 상태, CSV 다운로드 row 설정, Inform bulk-create/snapshot cache/UI를 정리. `python3 -m pytest tests/test_s3_ingest_status.py tests/test_filebrowser_sql.py tests/test_lot_progress_cache.py tests/inform`, `git diff --check`, frontend build 통과.
- [x] (Codex) P8 Inform 라우터 silent failure 정리 1차 — `admin_settings.json` 읽기 실패 시 Inform user-modules 조회/저장/clear 경로가 warning 로그와 HTTP 500 detail을 내고, 감사 로그 `try/except: pass`는 warning 로그로 전환. 깨진 설정 파일이 저장으로 덮어써지지 않게 검증. `python3 -m pytest tests/inform` 통과.
- [x] (Codex) P7 SplitTable 페이지 분해 — matrix plan 입력 모달을 `SplitTableCellEditor`로 분리해 matrix 본문에서 edit UI를 떼어냄. `git diff --check`, frontend build 통과.
- [x] (Codex) P6 Inform Log 페이지 분해 — `MailDialog`의 메일 미리보기/본문 크기/인라인 이미지/자동 SplitTable xlsx 첨부 표시 흐름을 `MailDialogPreviewPanel`로 추출. `git diff --check`, frontend build 통과.
- [x] (Codex) P5 PageHeader/TabStrip 표준화 — `My_Home.jsx`의 사용 방법 header를 `PageHeader`로 교체하고 `My_Admin.jsx` 리소스/품질 기간 선택 및 `My_Dashboard.jsx` 밀도 선택을 `TabStrip`으로 정리. `git diff --check`, frontend build 통과.
- [x] (Codex) P4 hardcoded color/token 정리 1차 — Home 픽셀 로고 색은 보존하고 Flow-i/Home UI 상태색, Dashboard Spotfire 팔레트, FileBrowser S3/cache 상태색, TableMap graph 색, SplitTable grid 색을 `statusPalette`/`chartPalette`/지역 토큰으로 1차 이관. `git diff --check`, frontend build 통과.
- [x] (Codex) P3 남은 `position: fixed` 정리 — Dashboard/TableMap 직접 overlay를 `Modal`로 전환하고 SplitTable/FileBrowser 수동 gear button을 공용 `PageGearButton`으로 이동. 대상 4파일 직접 `position: fixed` 0건, `git diff --check`, frontend build 통과.
- [x] (Codex) P2 `alert()` 제거 1차 — `My_TableMap.jsx`, `My_Admin.jsx`, `My_Home.jsx`, `My_Dashboard.jsx`의 직접 `alert()` 호출을 toast로 전환. 대상 4파일 `rg "alert\\("` 0건, `git diff --check`, frontend build 통과.
- [x] (Codex) P1 직접 `fetch(` 제거 1차 — `My_FileBrowser.jsx`와 `My_Home.jsx`의 직접 `fetch(`를 `sf()`, `postJson()`, `dl()`로 이관. `rg "fetch\\("` 대상 2파일 0건, `git diff --check`, frontend build 통과.
- [x] (Codex) P0 Owner Discord request 점검 — FileBrowser sync/cache/CSV/AI SQL/version 편집, Inform snapshot/register/embed UI 흐름 관련 targeted pytest 103개와 frontend build 통과. 즉시 막는 재현 실패가 없어 코드 수정 없음.
- [x] (Codex) c794c42 GitHub main 배포 준비 — HEAD가 `c794c42`임을 확인하고 setup.py 재생성/검증 후 Discord deploy agent에 commit/push 인계.
- [x] (Codex) GitHub main 배포 준비 — 로컬 변경 검토, setup.py 재생성, diff/build/pytest/smoke/preflight 검증 완료. 커밋/푸시는 Discord deploy agent가 이어서 수행.
- [x] (Codex) FileBrowser LOT 진행 최신 캐시 생성 기준 수정 — product는 FAB 제품 폴더명에서 가져오고 FileBrowser cache 폴더는 canonical 최신 cache만 남기며 생성 방식을 자연어로 문서화.
- [x] (Codex) FileBrowser 다운로드 시 `data type mismatch for column` 오류 수정 — Polars dtype mismatch 시 DuckDB CSV fallback 추가.
- [x] (Codex) setup builder/generated setup.py의 과거 버전 라벨 설명 정리 — 현재 설치 안내에는 실제 VERSION 값만 남기고 `v8.8.x`식 히스토리 주석 제거.
- [x] (Codex) FileBrowser 캐시를 LOT 진행 최신 캐시 중심으로 정리하고, ET/INLINE/VM allowlist DB 파생 캐시 생성/검증 시나리오를 추가.
- [x] (Codex) 변경점 관리 하단에 회의별 LLM 질의 패널 추가 — Meeting read-only ask API + Calendar UI.
- [x] (Codex) Meeting 이슈 가져오기 후 agenda 카드와 draft preview에 Tracker LOT table 표시.
- [x] (Codex) Inform 신규 등록 mail group/recipient 연결 및 flow-data 기반 수신자 검색 복구.
- [x] (Codex) Flow lot scope, Tracker Monitor lot summary, Meeting issue import/mail preview fix.
- [x] (Codex) `AGENTS.md`를 Karpathy-style 전역 코딩 규율 + Flow 프로젝트 규칙 구조로 정리.
- [x] (Codex) README를 현재 구조 기준으로 갱신하고 GitHub main push 재현 절차/하네스 구조를 문서화.
- [x] (Codex) Tracker 새 이슈 lot 입력 표에 product/purpose/comment 복구, SplitTable식 lot_id 후보 목록 적용, fab_lot_id 후보가 root 5자리로 잘리지 않게 수정.
- [x] (Codex) FileBrowser SQL/AI SQL의 `wafer_id` 조건을 string 저장 타입에서도 숫자 의미로 실행되도록 정규화.
- [x] (Codex) FileBrowser AI SQL 날짜/시간 자연어 필터가 월·일·시·분·초를 quoted ISO literal로 보존하도록 보강하고, LLM의 연도-only 오역을 deterministic fallback으로 교정.
- [x] (Codex) FileBrowser fast preview / full CSV download / AI SQL draft evaluation 구현 — preview 200행 고정, CSV 다운로드는 500k/100MB 한도까지, `/sql/llm/draft` + live eval/fallback 검증.
- [x] (Codex) FileBrowser 파일설정 LLM 규칙 초안을 전문가형 prompt/fallback으로 보강하고, 생성 초안 요약/JSON 미리보기와 초안 적용 흐름을 추가.
- [x] (Codex) Agent LLM runtime 설정을 Google Cloud Vertex AI Flash 계열로 전환 — `openai_compatible` / `google/gemini-2.5-flash` / `us-central1` endpoint 사용.
- [x] (Codex) Agent LLM runtime 설정을 OpenAI API + GPT OSS 120B급 모델로 채움 — 기존 admin token을 보존하고 active provider/model/api_url을 `openai` / `gpt-5.4-mini` / `https://api.openai.com/v1`로 정렬.
- [x] (Codex) FileBrowser file-settings LLM draft endpoint/UI, FileBrowser prompt seed, Tracker scheduler lot_progress cache 우선 조회를 추가.
- [x] (Codex) FileBrowser 캐시 탭에 `lot_progress_latest_lot_by_root_wafer` 수동 생성과 LLM allowlist 기반 캐시 생성 prompt 추가.
- [x] (Codex) SplitTable FAB lot 표시가 manual 컬럼오버라이드로 보이는 회귀 수정 — `/view`에서 match cache를 우선 생성/사용하고 UI 배지는 cache hit를 우선 표시.
- [x] (Codex) FileBrowser 캐시 탭에서 SplitTable 매칭 캐시 자동 주기/수동 갱신을 분리하고 status HTTP 500을 복구. Tracker Analysis ET 캐시는 자동 scheduler 없이 수동 갱신만 허용.
- [x] (Codex) Agent prompt history를 `data/flow-data/flowi_activity.jsonl`에서 다시 불러오고, Schema 관계 전체 스캔/엔지니어 편집 저장, Wiki/Vault 공통 지식 목록 노출, `A1001 24.SORT KNOB` SplitTable product clarification 라우팅을 추가.
- [x] (Codex) Agent 탭을 `실행 루프 / Wiki Graph / Schema 관계 / AI 연결` 소탭으로 재구성하고, trace 공개 요약(persona/prompt cache/subagent/clarification loop), prompt-only 실행 루프(product override 제거), schema relation preview/admin save graph/delete를 추가.
- [x] (Codex) 6개 기능 페이지 UI quick-win 적용 — ToastHost 추가, 대상 페이지 `alert()` 제거, Calendar/Meeting/FileBrowser/Inform/SplitTable 중앙 모달을 `Modal.jsx`로 통일, PageGear 좌하단 위치 반영, Vite dist stale chunk 정리.
- [x] (Codex) Flow 앱 visible chrome 통일 — 공통 header/sidebar accent bar, title/meta 라인, `PageHeader` 표면 톤을 6개 기능 페이지에 적용.
- [x] (Codex) SplitTable 노트 이미지 회귀 복구 — 이미지 업로드/붙여넣기, 이미지-only 노트 저장, 썸네일/확대 링크, legacy image shape normalization.
- [x] (Codex) Agent 탭을 Diagnosis와 시각적으로 분리하고, Activation Map 5단계 카드와 prompt drop-down, Trace Steps 표시를 단일 페이지 흐름으로 정리 (`docs/features/flowi-agent.md` Agent Tab UX, Acceptance Criteria 충족)
- [x] (Codex) `/flowi/agent/chat` 응답에 `trace.steps`와 `trace.activation`(=`trace.call_graph.activation`)을 항상 채우고, orchestrator preview row를 contract unit action schema로 정렬.
- [x] (Codex) Inform / SplitTable / FileBrowser Agent unit action handler 메타데이터를 각 feature md의 Agent Driver Contract 표에 맞춰 노출.
- [x] (Codex) FileBrowser `/domain`, `/roots`, `/scopes`, `/base-files`, `/base-file-view` 권한 게이트를 `current_user`로 일관화.
- [x] (Codex) `scripts/preflight_internal.py --write-probe`를 사내 Linux 공유 서버 시뮬레이션 root로 실행해 root 보존 / `data/flow-data/` 미덮어쓰기 기준을 확인.
- [x] (Codex) Admin의 SplitTable 매칭 캐시 / Tracker Analysis ET 캐시 패널을 제거하고 운영 진입점을 FileBrowser 캐시 탭으로 이전. `filebrowser.cache.match.refresh/status` unit action 추가.
- [x] (Codex) Agent LLM 설정 기본값을 사내 GPT OSS 120B openai-compatible 경로로 정렬하고 `llm_adapter` provider/format 및 외부 LLM URL 잔여 경로를 확인.
- [x] (Claude) `docs/REVIEW.md` 신규 — 11개 페이지 코드 기반 평가 보고서 작성 (mtime 기준).
- [x] (Claude) `AGENTS.md`에 Mission, Claude/Codex Role Split, Version Note 추가.
- [x] (Claude) `docs/features/flowi-agent.md`에 Agent Tab UX (7카드), Activation Map 5 stages, Backend Trace Contract, Acceptance Criteria 추가.
- [x] (Claude) `docs/features/inform.md`, `splittable.md`, `filebrowser.md`에 Agent Driver Contract 표 추가.
- [x] (Claude) `docs/features/diagnosis-knowledge.md` 헤더에 "현재 우선순위 아님" 메모 추가.
- [x] (Claude) `docs/features/admin.md` Does Not Own에 두 캐시 패널 이전 명시. `docs/features/filebrowser.md` Owns에 두 캐시 추가 + Agent Driver Contract에 `filebrowser.cache.match.refresh/status` 추가.
- [x] (Claude) `docs/features/flowi-agent.md`에 LLM Target 섹션 추가 — 사내 API GPT OSS 120B, openai-compatible 어댑터, JSON draft/문장 정리 영역만 사용, 미설정 시 fallback.
- [x] (Claude) `docs/UI_REVIEW.md` 신규 — 14개 페이지 UI 통일성 점수표(평균 3.2/10), 6 패턴 audit, Quick Win Top 5, 토큰 보강 권고. UXKit/CSS 변수/statusPalette 등 foundation은 잘 갖춰짐.
- [x] Enabled `*` and `%` wildcard search for SplitTable custom-set columns.
- [x] Added prompt-by-prompt orchestrator activation preview on the Agent page.
- [x] Changed Flow-i KNOB configuration answers to prefer custom set tables.
- [x] Fixed current FAB lot prompts to activate FileBrowser/FAB lookup in the Agent call graph.
- [x] Made Agent page show the delivered prompt and activated feature/action as the primary visualization.
- [x] Added Agent page API call graph visualization and regenerated setup bundle.
- [x] Validated Flow-i slot routing changes and regenerated setup bundle.
- [x] Routed Flow-i SplitTable view, current step, KNOB-value fastest WF, Inform draft clarification, and Tracker lot-purpose prompts through deterministic feature handlers.
- [x] Regenerated `setup.py` after Agent page/doc changes.
- [x] Simplified Agent page to show Flow-i prompt -> orchestrator -> feature unit action -> trace -> result flow.
- [x] Fixed Tracker LOT table purpose/progress note columns, latest progress cache wafer summary, and unclipped lot_id dropdown.
- [x] Improved Tracker new-issue lot_id entry, lot-level save, and step summary display.
- [x] Fixed SplitTable note image save/render path so in-flight uploads are included consistently across lots.
- [x] Hardened SplitTable image-only notes with a hidden compatibility placeholder for legacy text-required note APIs.
- [x] Fixed SplitTable image-only note/comment save path to wait for uploaded images and work consistently across all lots.
- [x] Fixed SplitTable notes to use resolved root lots so non-A1000 lots attach and show image-only notes consistently.
- [x] Fixed SplitTable note image upload for clipboard/file blobs without filename extensions.
- [x] Implemented FileBrowser file-setting AND pass conditions and ppid_knob.csv product/feature/rule-order validation example.
- [x] Removed Claude compatibility entrypoint and root Node package files.
- [x] Moved ET PPTX Node dependency management to `scripts/package.json`.
- [x] Expanded `VERSION.json` with repo entrypoints, package roots, bundle metadata, validation commands, and cleanup policy.
- [x] Regenerated `setup.py` from `_build_setup.py`.
- [x] Fixed SplitTable PRODA root lot cache regression and regenerated latest lot cache.
- [x] Audited root and docs references.
- [x] Confirmed `VERSION.json` is required and root package files can move under `scripts/`.
- [x] Chose `AGENTS.md` as the official entrypoint and `TODO.md` as the working checklist.
- [x] Added `AGENTS.md` as the official agent entrypoint.
- [x] Added root `TODO.md` with `Now`, `Next`, `Done`, and `Notes` sections.
- [x] Moved non-essential docs into `archive/docs_reorg_2026_05_09/`.
- [x] Removed the old RAG docs folder after archiving its content.
- [x] Updated current doc links and setup bundle sources.
- [x] Regenerated `setup.py` from `_build_setup.py`.

## Notes

- Start each work session by reading `AGENTS.md`, then this file.
- End each work session by checking off completed items or adding follow-ups.
- `Now` 항목 앞의 `(Claude)`/`(Codex)` 접두를 유지해 책임을 명확히 한다.
- 새 docs/메모에 `v9.0.x` 같은 명시 버전을 적지 않는다 (mtime 기준, `AGENTS.md` Version Note).
- Archived docs는 historical references이며 active 구현 가이드가 아니다.
- `archive/`는 git-ignored. 의도적 force-add 없이는 로컬에만 남는다.
