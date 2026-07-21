# 02. Feature v9.1 (F1~F3)

**시점**: +6주 / **점수 목표**: 7.2 → 7.5 / **항목 수**: 3 (1~3주 단위)
**성격**: feature branch + PR 필수. 대형 리팩터링.
**추천 진행 순서**: F3 (pytest) 선행 → F1-a/b/c 병렬 → F1-d + F2 병렬

> **v9.1 기존 로드맵 상속 3건** (Meeting 이슈 확장 · Tracker 카테고리 확장 · 온보딩 투어) 은 본 파일 말미 "v9.1 상속 항목" 섹션 참조. 핵심 대형 3건 (F1~F3) 과 별개 sprint 로 동시 진행.

---

## F1. UXKit 실투입 — 4페이지 Migration

- **상태**: todo
- **담당 후보**: claude (dev-lead 위임 → dev-inform/dev-dashboard/dev-admin/dev-splittable 분배)
- **변경 파일**:
  - `D:\TEST_Making_Video\semi_all\flow\frontend\src\UXKit.jsx` (기존, primitives 보강)
  - `D:\TEST_Making_Video\semi_all\flow\frontend\src\pages\My_Inform.jsx` (v9.1-a)
  - `D:\TEST_Making_Video\semi_all\flow\frontend\src\pages\My_Dashboard.jsx` (v9.1-b)
  - `D:\TEST_Making_Video\semi_all\flow\frontend\src\pages\My_Admin.jsx` (v9.1-c)
  - `D:\TEST_Making_Video\semi_all\flow\frontend\src\pages\My_SplitTable.jsx` (v9.1-d, F2 분할과 병합)
  - `D:\TEST_Making_Video\semi_all\flow\tests\frontend\test_inform_ui_kit_smoke.py` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\tests\frontend\test_dashboard_ui_kit_smoke.py` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\tests\frontend\test_admin_ui_kit_smoke.py` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\tests\frontend\test_splittable_ui_kit_smoke.py` (신규)

- **변경 내용**:

  **v9.1-a My_Inform (1주)** — 117 hex → UXKit primitives:
  - `statusPalette.{ok, warn, bad, info, neutral}` 로 상태색 치환
  - `Pill` / `TabStrip` / `TwoCol` / `EmptyState` / `Banner` 5 primitive 적용
  - 기존 inline `style={{ color: '#...' }}` 패턴 전면 제거

  **v9.1-b My_Dashboard (1주, v9.1-a 병렬)** — H2 팔레트 후속:
  - 82 hex → `chartPalette.*`
  - `PageHeader` + `TabStrip` 적용
  - 차트 ↔ 테이블 전환 loading 상태 UXKit.Banner 통일

  **v9.1-c My_Admin (1주, v9.1-a/b 병렬)** — 14탭 안정화 병행:
  - 89 hex → `statusPalette`
  - 14개 탭 TabStrip 으로 전환 표준화
  - `TabBoundary` + `UXKit.EmptyState` 결합 → 탭별 빈 상태 통일

  **v9.1-d My_SplitTable (3주, F2 분할과 통합)** — 89 hex + 내부용어 완전 은닉:
  - F2 SplitTable 분할 시점에 각 파일 단위 UXKit 적용
  - H3 고급탭 은닉 완료 상태 전제

- **완료 조건 (DoD)**:
  - [ ] grep: 4개 파일 hex hardcoded 총합 377 → 40 이하 (`rg -oP '#[0-9a-fA-F]{6}' frontend/src/pages/My_{Inform,Dashboard,Admin,SplitTable}.jsx | wc -l`)
  - [ ] `ux-reviewer` 에이전트 pass (`docs/ux_standard.md` 룰 준수)
  - [ ] smoke: `test_*_ui_kit_smoke.py` 4건 모두 pass
  - [ ] 수동: 다크↔라이트 토글 시 4페이지 글자 가독성 유지
  - [ ] `npm run build` + bundle size 15% 이내 증가 (UXKit 추가분)

- **의존성**:
  - H2 (팔레트) → v9.1-b 선행 권장
  - H3 (SplitTable 고급 탭) → v9.1-d 선행 권장
  - F3 (pytest) → `test_*_ui_kit_smoke.py` 프레임워크 필요

- **예상 공수**: 합계 3주 (a/b/c 병렬 1주 + d 3주, 실제 d 를 F2 와 겸할 경우 F2 3주에 포함)

- **리스크**:
  - 안 하면: `docs/ux_standard.md` 사문화, 신규 페이지도 hex 하드코딩 지속
  - 하다가: 4페이지 동시 작업 시 UXKit 변경이 연쇄 회귀 → primitive 변경 금지 정책 (add-only)

---

## F2. SplitTable 페이지 분할 (3,480줄 → 4파일)

- **상태**: todo
- **담당 후보**: claude (dev-splittable 단독 풀스택)
- **변경 파일**:
  - `D:\TEST_Making_Video\semi_all\flow\frontend\src\pages\My_SplitTable.jsx` (기존, 삭제 대체)
  - `D:\TEST_Making_Video\semi_all\flow\frontend\src\pages\SplitTable\index.jsx` (신규, shell)
  - `D:\TEST_Making_Video\semi_all\flow\frontend\src\pages\SplitTable\LotTable.jsx` (신규, 메인 테이블)
  - `D:\TEST_Making_Video\semi_all\flow\frontend\src\pages\SplitTable\PlanPanel.jsx` (신규, plan vs actual)
  - `D:\TEST_Making_Video\semi_all\flow\frontend\src\pages\SplitTable\NotesDrawer.jsx` (신규, lot/global 노트)
  - `D:\TEST_Making_Video\semi_all\flow\frontend\src\pages\SplitTable\_helpers.js` (신규, 공통 utils)
  - `D:\TEST_Making_Video\semi_all\flow\tests\frontend\test_splittable_parity.py` (신규)

- **변경 내용**:
  - `pages/SplitTable/index.jsx` (400줄 목표) — shell, 라우팅, 최상위 상태 context
  - `pages/SplitTable/LotTable.jsx` (1,200줄 목표) — 메인 테이블 + cell render + XLSX export
  - `pages/SplitTable/PlanPanel.jsx` (900줄 목표) — plan vs actual + override + fab_source 매칭
  - `pages/SplitTable/NotesDrawer.jsx` (600줄 목표) — lot/global 노트 + 이슈 연결 + paste 세트
  - `pages/SplitTable/_helpers.js` (380줄 목표) — 공통 utils (ci_align_fab, infer_step_mapping 프론트 래퍼 등)
  - 기존 `My_SplitTable.jsx` 는 `pages/SplitTable/index.jsx` re-export 로 대체 (import 경로 호환)

- **완료 조건 (DoD)**:
  - [ ] pytest: `test_splittable_parity.py` — 기존 smoke 27 케이스 중 SplitTable 관련 + 10 추가 케이스 모두 pass (신규 케이스 예: paste 세트 복원 ping-pong, long adapter override, root_scope data-driven join, ci_align rename)
  - [ ] 수동: paste 세트 10개 복원 ping-pong 시나리오 100% 동일
  - [ ] 수동: v9.0.x 의 `/override-debug`, `/long-items`, `/fab-roots`, `/lot-candidates` 모두 동일 동작
  - [ ] `npm run build` 성공 + bundle 비정상 증가 없음
  - [ ] 각 파일 1,500줄 미만

- **의존성**:
  - F3 (pytest) 필수 선행 — parity test 프레임워크 없이는 회귀 확인 불가
  - F1-d (UXKit SplitTable) 와 동시 진행 (같은 3주 스프린트에 통합)
  - H3 (고급 탭 은닉) 먼저 적용된 상태에서 분할 시작

- **예상 공수**: 3주 (migration + regression test)

- **리스크**:
  - 안 하면: SplitTable 버그 수정 3~4시간 지속, 리뷰 난이도 최상
  - 하다가: 분할 중 regression → paste 세트 시나리오 깨짐. **완화**: feature branch 필수 + parity test 10 케이스 선행 작성

---

## F3. pytest 도입 (규모 1차 — 100 케이스)

- **상태**: todo
- **담당 후보**: claude (eval-lead + dev-lead 공동) · codex 보조 가능
- **변경 파일**:
  - `D:\TEST_Making_Video\semi_all\flow\tests\` (디렉토리 신규 전체)
  - `D:\TEST_Making_Video\semi_all\flow\tests\conftest.py` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\tests\auth\test_login.py` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\tests\auth\test_session.py` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\tests\tracker\test_issue_crud.py` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\tests\tracker\test_categories.py` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\tests\inform\test_crud.py` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\tests\inform\test_embed.py` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\tests\inform\test_reply.py` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\tests\splittable\test_override_debug.py` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\tests\splittable\test_long_adapter.py` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\tests\meeting\test_minutes_append.py` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\tests\meeting\test_ot_lite.py` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\tests\admin\test_page_admin.py` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\tests\admin\test_mail_groups.py` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\tests\dashboard\test_chart_render.py` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\pytest.ini` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\requirements-dev.txt` (신규)

- **변경 내용**:
  - `tests/conftest.py` — FastAPI TestClient fixture, localhost:8080 서버 fixture (spawn subprocess + teardown), `tmp_data_root` fixture (temp dir 격리)
  - 영역별 케이스 분배 (100 총):
    - `auth/` — 로그인 · 세션 · 토큰 · rate limit: 10 케이스
    - `tracker/` — 이슈 CRUD + 카테고리 + 댓글: 15 케이스
    - `inform/` — create/embed/reply/confirm/cancel: 20 케이스
    - `splittable/` — override-debug · long adapter · fab_source · root_scope: 15 케이스
    - `meeting/` — minutes append · OT-lite rev · issues import: 10 케이스
    - `admin/` — page admin · mail groups · product acl (P5 선행 재료): 15 케이스
    - `dashboard/` — chart render · Y-axis multi · loading state: 15 케이스
  - `pytest.ini` — `addopts = -q --tb=short`, `markers = slow, integration`
  - `requirements-dev.txt` — `pytest>=7.4`, `httpx`, `pytest-xdist`, `pytest-cov`

- **완료 조건 (DoD)**:
  - [ ] `pytest -n 4` → 100/100 pass (로컬)
  - [ ] `pytest --cov=backend` → coverage ≥ 55%
  - [ ] CI (P1 에서 자동화, 여기서는 로컬 스크립트 `scripts/run_tests.sh`) 실행 가능
  - [ ] 각 영역별 README `tests/{area}/README.md` — 케이스 목적 한 줄 요약
  - [ ] 실행 시간 5분 이내 (pytest-xdist 4 worker)

- **의존성**: 없음 (다른 F/P 항목의 선행 재료)

- **예상 공수**: 2주 (initial)

- **리스크**:
  - 안 하면: 리팩터링 공포 지속, F1/F2 migration 시 회귀 폭주
  - 하다가: 초기 fixture 복잡도 과대 → test 작성 지연. **완화**: 단순 HTTP 클라이언트 (httpx) 우선, DB fixture 는 tmp JSON 파일로 시작

---

## v9.1 상속 항목 (기존 로드맵 — `docs/v9_roadmap.md`)

아래 3건은 본 문서의 F1~F3 와 **동일 sprint 내** 별도 진행. 추가 ID 없이 원 로드맵 번호 그대로 유지.

### 1.10 Meeting 이슈 가져오기 대확장

- **요약**: `/issues/import` 가 description 만 가져옴 → lot_step_snapshot + 이미지 + lot 리스트 + FAB/ET 최신 snapshot 포함 확장
- **변경 파일**: `backend/routers/meeting.py`, `frontend/src/pages/My_Meeting.jsx`
- **검증**: `tests/meeting/test_issues_import_with_snapshot.py`
- **공수**: 1주
- **스펙 원전**: `docs/v9_roadmap.md` "v9.1 회의 이슈 가져오기 확장" 섹션

### 1.11 Tracker 카테고리 대확장

- **요약**: Monitor (FAB 특정 step 알람) + Analysis (ET wafer 단위) 2 카테고리 자동 등록. wafer_id `"1-10"`/`"all"` 파싱, 하루 2회 자동 체크, 자동 완료.
- **변경 파일**: `backend/routers/tracker.py`, `backend/core/tracker_auto.py` (신규), `frontend/src/pages/My_Tracker.jsx`
- **검증**: `tests/tracker/test_analysis_wafer_expansion.py`, `tests/tracker/test_monitor_mail_group.py`
- **공수**: 2주
- **스펙 원전**: `docs/v9_roadmap.md` "v9.1 Tracker 카테고리 대확장" 섹션

### 1.12 Home 온보딩 3분 투어

- **요약**: 신규 유저 첫 로그인 → 9 step spotlight tour (Home → Tracker → Inform → Meeting → SplitTable → Dashboard → ML → Admin → Done). `admin_settings.user_onboarding_done[uid]` 로 1회 제한.
- **변경 파일**: `frontend/src/components/OnboardingTour.jsx` (신규), `frontend/src/App.jsx` (첫 로그인 훅), `backend/routers/admin.py` (`/api/onboarding/status`, `/api/onboarding/done`)
- **검증**: `tests/admin/test_onboarding_status.py`, 수동: 신규 계정 3분 안에 9 step 완료
- **공수**: 1주
- **의존성**: H6 (Home 가치 제안) 선행 — 투어 첫 step 이 "3가지 질문" 섹션을 가리킴

---

## v9.1 릴리즈 게이트 (eval-lead 검증)

F1~F3 + 상속 3건 완료 후:

- [ ] 3개 대형 항목 DoD 충족
- [ ] 상속 3건 (Meeting/Tracker 확장 + 온보딩) 완료
- [ ] pytest 100 케이스 + smoke 42 케이스 모두 pass
- [ ] bundle size 증가 15% 이내 (UXKit 투입 분)
- [ ] `docs/ux_standard.md` ux-reviewer 통과
- [ ] SplitTable 분할 후 paste 세트 ping-pong 수동 검증 pass
- [ ] CHANGELOG_v9.1.md 작성

목표 점수: **7.2 → 7.5** (UX 일관성 0.7↑, 안정성 2.3↑, 기능 0.2↑)

---

## 참고

- 원본 스펙: [`_archive/v9_improvement_plan.md`](./_archive/v9_improvement_plan.md) §1.7~1.12
- 스프린트 상세: `_archive/v9_improvement_plan.md` §6.2 (Sprint 2-6)
- 다음 단계: [`03_platform_v9_2.md`](./03_platform_v9_2.md) (P1~P6 플랫폼화)
