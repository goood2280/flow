# flow v9.x Roadmap — 2026-04-23 기준

v9.0.0 에서 minor 이월로 보류된 대형 기능 묶음. 우선순위 순.

## v9.1 — 회의 이슈 가져오기 확장

사용자 요구 (2026-04-23):
- 회의관리에서 "이슈 가져오기" 를 눌렀을 때 이슈 안의 캡처 이미지 + 붙은 LOT_WF 리스트 + 체크된 watch 에 따른 FAB/ET 최신 정보도 함께 가져와 회의록 본문에 삽입.
- meetings `/issues/import` 엔드포인트에서 `iss.description` + `iss.images` + `iss.lots` + 각 lot 의 watch snapshot 을 통합해 HTML 렌더.
- 각 lot 행별로 `lot_step_snapshot` 호출 → FAB 최신 step_id / ET 최근 5 패키지(step_id/step_seq(Npt)) 를 회의 본문에 inline.

## v9.1 — Tracker 카테고리 대확장 (Monitor/Analysis/기타)

사용자 요구 사항 (2026-04-23 제시):

### 카테고리 스키마 확장
- 기본 카테고리 2종 자동 등록:
  - **Monitor** — source=`fab` · lot 단위 관리 · 특정 step_id 알람 + 그룹 메일
  - **Analysis** — source=`et` · wafer 단위 관리 · ET 측정 패키지 신규 시 알람 + 메일
- 카테고리 필드 확장: `name`, `color`, `source`, `max_issues_per_user` (기본 15), `mail_group_ids` (step 도달 시 메일 그룹), `auto_close_step_id` (모든 wafer 가 이 step 넘기면 자동 완료)
- 기존 tracker `_normalize_cats` 에 이미 `source` 필드 있음. 나머지 필드 추가.

### Analysis 카테고리 동작
- Lot 등록 시 `wafer_id` 칸에 `"all"` 또는 `"1,2,3"` 또는 `"5-10"` 입력 가능 → BE 가 파싱해서 **행 단위로 분리** 저장 (`all` = 1~25).
- 원본 row 의 `comment` 는 모든 분리된 row 에 복사.
- 각 (root_lot_id, wafer_id) 마다 ET 측정 이력 (`step_id`, `step_seq`, `pt_count`) 을 watch 에 저장.
- 하루 2회 스케줄러 `/api/tracker/lot-check-all` (admin / cron) → 전체 wafer 순회해 ET 신규 감지 → 작성자 + 그룹에 bell + 메일.
- **메일 2MB 한계**: HTML body 초과 시 제목 + 설명글 + 요약 테이블만 (detail 없이).
- **유저별 이슈 상한**: 카테고리 `max_issues_per_user` 기본 15. `/api/tracker/create` 가 카테고리 active(open) 이슈 수 확인 → 초과 시 400.
- **자동 완료처리**: 모든 wafer 의 `last_observed_step` 가 카테고리 `auto_close_step_id` 이상이면 이슈 status 자동 closed + 작성자에게 bell (메일 X).

### Monitor 카테고리 동작
- Lot 단위. wafer 는 additional comment 역할 (wafer_id 비워두거나 참고용).
- FAB 최신 step 폴링 → 특정 step_id 도달 시 작성자 bell + 카테고리 `mail_group_ids` 그룹 메일.

### 톱니바퀴 편집 UI
- My_Tracker PageGear 에 카테고리 편집 모달.
- 필드: name, color, source(fab/et/both/auto), max_issues_per_user, mail_group_ids, auto_close_step_id.
- soft-landing: 기존 카테고리의 이름 변경 시 active 이슈 재매핑 prompt.

### SplitTable 노트에 이슈 연결
- `/api/splittable/notes` 응답에 해당 (product, root_lot_id, wafer_id) 에 연관된 tracker 이슈 목록 attach.
- 노트 드로어에 "관련 이슈: ISS-... | 제목 | 상태 | 카테고리" 섹션 추가.

---

## v9.2 — 안정성/확장성 playbook 2차

- GitHub Actions CI — `smoke_test.py` + `vite build` + `ruff` 자동 실행.
- 구조화 로깅 (`backend/core/logging.py`) — JSON line + request_id.
- GlitchTip self-host PoC — 에러 집약.
- SQLite 세션 저장소 마이그레이션 — gunicorn 멀티 워커 준비.
- S3 read-through cache (`core/s3_cache.py`).

---

## v9.3 — UXKit 마이그레이션

- My_Home, My_Tracker, My_Meeting, My_Calendar, My_Dashboard, My_Admin 을 UXKit 기반으로 재작성.
- `docs/ux_standard.md` 기준 적용 검증 — ux-reviewer 에이전트가 각 페이지 pass/fail 판정.

---

## v9.0.x 핫픽스 후보

- 인폼 로그 리스트에서 st_view 가 빈 embed 도 렌더되는지 실제 재현 테스트.
- SplitTable custom_cols ad-hoc 모드에서 plan 저장·불러오기 end-to-end 검증.
- `/api/informs/products/dedup` 호출 후 사이드바 즉시 반영 검증.
- Admin 탭 전반 `n is not a function` 류 런타임 오류 재현/차단.
  - 배열/객체 shape guard 공통화
  - 탭별 loader 실패 시 전체 Admin 마비 방지
  - 문제 payload 샘플 수집 후 offending endpoint 좁히기

---

*담당: dev-lead (구현 roadmap) · eval-lead (릴리즈 게이트) · mgmt-lead (사용자 요약).*
