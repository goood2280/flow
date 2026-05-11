# TODO

> `Now` 항목은 `(Claude)` / `(Codex)` 접두로 책임을 분리한다. Claude 세션은 진입점/문서/TODO/평가/Agent ↔ feature 명세를 유지하고, Codex CLI 세션은 frontend/backend 코드 자체 변경을 담당한다 (`AGENTS.md` Role Split 참조).

## Now

- [ ] (Codex) UI Quick Win #1 — 모든 `position:fixed` 모달을 `components/Modal.jsx`로 통일. 위반: `My_Calendar.jsx:485`, `My_SplitTable.jsx:1803/1824/1871`, `My_Inform.jsx:1401/1451`, `My_Dashboard.jsx`, `My_FileBrowser.jsx`. z-index 자동화 (`docs/UI_REVIEW.md` 참조)
- [ ] (Codex) UI Quick Win #2 — hardcoded color hex 일괄 치환 — `My_Home.jsx:5` 개별 변수, `My_Dashboard.jsx:15-22` Spotfire 임베드, `My_Calendar.jsx:335/429/477` `#fff`, `My_Diagnosis.jsx:1263-1270` `CALL_NODE_TONE` → CSS 변수 또는 `uxColors`/`chartPalette` 사용
- [ ] (Codex) UI Quick Win #3 — `My_Admin.jsx`, `My_Home.jsx` 직접 button 탭을 UXKit `<TabStrip items active onChange />`로 교체
- [ ] (Codex) UI Quick Win #4 — `alert()` 전면 제거 → UXKit `<Banner tone>` 또는 toast. `My_Calendar.jsx`, `My_Admin.jsx` 우선
- [ ] (Codex) UI Quick Win #5 — 페이지 헤더 inline `<div>` → `<PageHeader title subtitle right />` 통일. `My_Dashboard.jsx:741` 등 10+ 페이지
- [ ] (Codex) `fetch(` 직접 호출을 `src/lib/api.js`의 `sf()`로 일괄 정리 — `My_FileBrowser.jsx`(7건), `My_Home.jsx`(13건) 우선

## Next

- [ ] (Codex) Inform Log 페이지 분해 — thread/list/draft/mail/snapshot 패널 단위로 panel 또는 hook 추출 (5,141줄 → 사용자 흐름 단위)
- [ ] (Codex) SplitTable 페이지 분해 — matrix/notes/rulebook/embed builder를 독립 컴포넌트로 분리 (2,226줄, useState 74개)
- [ ] (Codex) Inform 라우터의 silent `try/except: pass` 46건 일괄 정리 — 실패 사유 UI/log 노출
- [ ] (Codex) FileBrowser/SplitTable/Inform 라우터의 service/repository 계층 도입 — `app_v2/modules/<feature>/`로 점진 이관
- [ ] (Codex) Admin / Dashboard 페이지 분해 — panel 단위로 분리 (각 ~3,000줄)
- [ ] (Claude) Agent 미션 정착 후 Diagnosis/Knowledge 본연 흐름 재정리 (`docs/features/diagnosis-knowledge.md` 우선순위 메모 해제)
- [ ] Keep feature docs current when page/API ownership changes.
- [ ] Regenerate `setup.py` with `_build_setup.py` after source or bundled doc changes.

## Done

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
