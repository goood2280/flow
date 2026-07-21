# flow v9 Improvement Plan — 2026-04-24

**작성**: orchestrator (dev-lead + eval-lead 위임 통합)
**기준 버전**: v9.0.2 (종합 7.0 / 10)
**문서 성격**: 엔지니어용 기술 개선안 — endpoint / 파일 / 코드 변경 단위
**자매 문서**: `docs/v9_improvement_summary_ko.md` (사용자용 한국어 요약, mgmt-lead 소유)

> **NOTE (archive)**: 본 문서는 원본입니다. 2026-04-24 에 `docs/collab/` 하위 협업 구조로 분산되었습니다. 참고/원전 보관용입니다.

---

## 0. Executive Summary

직전 수준점검(`docs/FLOW_APP_ASSESSMENT_2026_04_24.md`)에서 **10개 이슈 + 화면별 직관성 평균 3.2/5 + 애매 기능 5건**이 식별되었다. 본 플랜은 이를 4개 릴리즈 궤도(v9.0.3 핫픽스 → v9.1 메이저 → v9.2 플랫폼화 → v9.3+ 장기)로 배치하고, 각 항목을 **현상 · 영향 · 개선안 · 공수 · 검증** 5-요소 스펙으로 정리한다.

**최종 목표 로드맵**:

| 릴리즈 | 목표 점수 | 핵심 키워드 |
|---|---|---|
| v9.0.2 (현재) | 7.0 | 기능 포화, UX 파편 |
| **v9.0.3** (+2주) | 7.2 | 핫픽스 · ML 탭 정합 · Home 가치제안 |
| **v9.1** (+6주) | 7.5 | UXKit 실투입 · pytest 도입 · SplitTable 분할 |
| **v9.2** (+3개월) | 8.0 | CI · 관측성 · SQLite 세션 · SSO 초기 |
| **v9.3+** (+6개월) | 8.5 | SPC · DVC 방향성 · 인과 매트릭스 · 모바일 |

---

## 1. 시점별 개선안

### v9.0.3 — 핫픽스 (1~2일 단위, +2주 릴리즈)

단일 PR 크기, 대형 리팩터링 없음. 즉시 체감 개선 위주.

#### 1.1 ML 탭 상태 정합

- **현상**: `frontend/src/config.js` 에서 `ML: PLANNED` 인데 `pages/My_ML.jsx` 는 실존 + `/api/ml/*` 8개 엔드포인트 정상 응답. "반쯤 살아있는 기능" 상태.
- **영향**: 유저가 사이드바에서 `PLANNED` 배지 보고 안 들어감 → 실제 기능을 못 씀. `assess/inline_et_overview` 가 방금 추가됐는데도 유저가 발견 못 함.
- **개선안**:
  1. `config.js` 에서 ML 을 `ACTIVE` 로 전환 + `featureMap.ML` 에 `{ status: 'beta', badge: 'BETA' }` 추가
  2. `My_ML.jsx` 최상단에 `UXKit.Banner(tone='info')` "ML 기능은 beta 입니다. 현업 질문 → Inline_ET + KNOB 요약을 먼저 시도하세요"
  3. `My_Home` 에 ML 카드에 "beta" 프리픽스 표시
- **공수**: 반일
- **검증**:
  - smoke test: `GET /api/config` 응답에 `ML.status==='beta'` 검증
  - 수동: Home → ML 카드 클릭 → beta 배너 노출 → Inline_ET 탭 진입 확인
- **리스크 (안 고치면)**: 유저가 "ML 기능 없는 줄 알고 외부 도구 사용" → 제품 ROI 손실

#### 1.2 Home 가치 제안 섹션

- **현상**: `pages/My_Home.jsx` 가 기능 카드 나열형. 신규 유저가 "내 lot 문제 어디서 해결?" 판단 불가.
- **영향**: 온보딩 실패율 추정 40%+ (신규 유저 첫 5분에 3페이지 이탈 → FEATURE_GUIDES 미도달).
- **개선안**:
  1. Home 상단에 **"3가지 질문" 섹션** 추가 — `<UXKit.TwoCol>` 로 (질문 → 추천 페이지) 매핑:
     - "내 lot 문제있나?" → Tracker 카테고리=Analysis 생성 버튼
     - "plan 대로 흐르고 있나?" → SplitTable 진입 + 최근 본 제품 memo
     - "이번 주 인폼 요약?" → Inform `_effective_modules` 자동 필터
  2. 각 카드 하단에 `<UXKit.Pill tone="info">` 3분 투어 링크 (v9.1 온보딩 재료)
  3. 기존 기능 카드 그리드는 하단으로 이동
- **공수**: 1일
- **검증**:
  - smoke: `GET /api/home/summary` 에 `suggested_actions` 필드 3건 이상
  - 수동: 신규 계정 로그인 → Home 첫 화면에 "3가지 질문" 가시
- **리스크**: 신규 사용자 retention 개선 부재 → v9.1 대확장 이후에도 유저 기반 안 늘어남

#### 1.3 페이지 애매 기능 정리

- **현상**: `WaferLayout`·`Messages`·`TableMap`·`DevGuide` 4개가 사이드바에 있으나 포지션 불명.
- **영향**: 사이드바 15개 탭 중 4개가 dead weight → 결정 피로 증가.
- **개선안**:
  1. **WaferLayout**: 설명 부재 → `My_WaferLayout.jsx` 상단에 `<UXKit.Banner tone='neutral'>` 용도 한 줄 + 언제 쓰는지 3줄. 현 상태 유지 (v9.1 에서 SplitTable drawer 로 승급 검토).
  2. **Messages**: 포지션 불명 → "내부 알림함" 라벨로 변경 (`config.js` label 수정). Tracker bell + Inform bell 통합 inbox 역할 명시.
  3. **TableMap**: admin 성이지만 일반 탭에 있음 → `config.js` 에서 `requiresRole: 'page_admin'` 플래그 추가 → 일반 유저 사이드바 숨김, Admin 탭 내부로 이동.
  4. **DevGuide**: 일반 유저에 불필요 → `requiresRole: 'devguide_user'` (admin_settings 에 신규 권한). 기본 유저 숨김.
- **공수**: 1일
- **검증**:
  - smoke: 일반 유저 토큰으로 `GET /api/config/sidebar` → TableMap/DevGuide 미포함
  - 수동: 일반 계정 사이드바 11개 탭 (15 - 4), admin 계정 15개 탭
- **리스크**: 사이드바 인지부하 → 핵심 기능(Tracker/Inform/SplitTable) 도달률 하락

#### 1.4 Dashboard 팔레트 통일

- **현상**: `My_Dashboard.jsx` 에 `COLORS`(15색) + `PASTEL`(15색) 두 팔레트 혼재. 차트 시리즈마다 어느 팔레트 쓰는지 비일관.
- **영향**: 대시보드 PDF 캡처 후 회의자료에서 "저 초록이 어떤 시리즈였지" 혼란.
- **개선안**:
  1. `UXKit.jsx` 에 `chartPalette.series` (12색 확정) + `chartPalette.pastel` (보조) export
  2. `My_Dashboard.jsx` 에서 `COLORS`/`PASTEL` 제거 → `UXKit.chartPalette.series` 로 치환
  3. 차트 종류별 할당 규칙: line=series / bar=series / scatter=series / pie=pastel / heatmap=gradient (`UXKit.chartPalette.heat`)
- **공수**: 반일
- **검증**:
  - grep: `My_Dashboard.jsx` 에 `const COLORS`, `const PASTEL` 존재 0건
  - 수동: 3차트 동시 비교 — series 0(pie) 와 series 0(bar) 같은 색
- **리스크**: 팔레트 카오스 지속 → UX 일관성 점수 7→ 7.5 못 올라감

#### 1.5 SplitTable 내부용어 은닉

- **현상**: `My_SplitTable.jsx` 에 `override-debug`, `long-items`, `fab-roots`, `_effective_modules` 같은 엔지니어 용어가 UI 라벨로 노출.
- **영향**: 일반 공정 엔지니어가 "long-items 가 뭔가요?" 질문 → 관리자 피로.
- **개선안**:
  1. PageGear(톱니) 내부로 이동 — `<UXKit.TabStrip>` "고급" 탭에 격리
  2. 일반 UI 라벨: `override-debug → 적용 진단`, `long-items → 긴 형식 항목`, `fab-roots → FAB 원본 루트`
  3. `docs/splittable_terms_ko.md` 신규 — 용어집 10개
- **공수**: 1일
- **검증**:
  - grep: `My_SplitTable.jsx` 기본 뷰(톱니 외)에서 `override-debug` 문자열 0건
  - 수동: 일반 유저 SplitTable 첫 화면에 "fab-roots" 단어 없음
- **리스크**: 학습 곡선 높음 → 신규 유저 이탈

#### 1.6 PRODA 중복 잔존 모니터

- **현상**: v9.0.0 에서 `/products/dedup` one-shot 추가됐으나 신규 유입되는 ML_TABLE 에서 여전히 가끔 중복 발생.
- **영향**: 사이드바 제품 탭 중복 → 저장 시 어느 쪽에 붙는지 혼란.
- **개선안**:
  1. `/api/informs/products/add` POST 에 `_dedup_on_save=True` 플래그 (기본 True)
  2. trim + casefold 통일 후 이미 존재하면 409 + "기존 product 사용" 메시지
  3. cron `core/product_dedup.py` 일 1회 — 새벽 3시 자동 정리
- **공수**: 1일
- **검증**:
  - pytest: `test_product_add_duplicate_returns_409`
  - smoke: 사이드바 제품 목록 중복 0건
- **리스크**: 중복 재발 시 유저 신뢰 손상

---

### v9.1 — 메이저 (1~2주 단위, +6주 릴리즈)

UXKit 실투입 + pytest 도입 + 페이지 분할 + 이슈 가져오기 대확장.

#### 1.7 UXKit 실투입 (핵심 4페이지)

- **현상**: `docs/ux_standard.md` 표준 있고 `UXKit.jsx` 존재하나 `pages/*.jsx` 어디서도 import 0건.
  - SplitTable 89 hex hardcoded
  - Dashboard 82 hex hardcoded
  - Admin 89 hex hardcoded
  - Inform 117 hex hardcoded
  - 총 **377건 hex hardcoded**
- **영향**: UX 일관성 점수 6.5/10 에서 올라가지 않음. 다크모드 전환 시 깨짐.
- **개선안**: 페이지별 migration 브랜치 4개 (각 1주)
  1. **My_Inform** (v9.1-a, 1주) — 제일 활발히 build-up 되는 페이지, UXKit 이득 최대:
     - 117개 hex → `statusPalette.{ok,warn,bad,info,neutral}` 치환
     - `Pill`/`TabStrip`/`TwoCol`/`EmptyState`/`Banner` 5개 primitive 적용
     - `test_inform_ui_kit_smoke.py` 추가
  2. **My_Dashboard** (v9.1-b, 1주) — 팔레트 통일 후속:
     - 82 hex → `chartPalette.*`
     - `PageHeader` + `TabStrip` 적용
     - 차트 ↔ 테이블 전환 시 UXKit.Banner 로 loading 상태 통일
  3. **My_Admin** (v9.1-c, 1주) — 14탭 안정화와 함께:
     - 89 hex → `statusPalette`
     - TabStrip 으로 탭 전환 표준화
     - `TabBoundary` + `UXKit.EmptyState` 결합
  4. **My_SplitTable** (v9.1-d, 3주) — 가장 큰 난이도 (별도 1.8 섹션 참조)
- **공수**: 합계 3주 (b/c 병렬 가능)
- **검증**:
  - grep: 4개 파일 hex hardcoded 총합 377 → 40 이하
  - `ux-reviewer` 에이전트 pass
  - smoke: `test_*_ui_kit_*` 4건 pass
  - 수동: 다크↔라이트 토글 시 4페이지 글자 가독성 유지
- **리스크**: 지속 지연 → `docs/ux_standard.md` 가 사문화, 신규 페이지도 hex 하드코딩

#### 1.8 SplitTable 페이지 분할 (3,480줄 → 4파일)

- **현상**: `My_SplitTable.jsx` 3,480줄, `state` hook 40+ 개, `useEffect` 22개. 변경 시 regression 1순위 페이지.
- **영향**: 개발 속도 저하 (1 buglet 수정에 3~4시간). 코드 리뷰 난이도 최상.
- **개선안**:
  1. `pages/SplitTable/index.jsx` (shell, 400줄)
  2. `pages/SplitTable/LotTable.jsx` (메인 테이블 + cell render, 1,200줄)
  3. `pages/SplitTable/PlanPanel.jsx` (plan vs actual + override, 900줄)
  4. `pages/SplitTable/NotesDrawer.jsx` (lot/global 노트 + 이슈 연결, 600줄)
  5. 공통 utils: `pages/SplitTable/_helpers.js` (380줄)
- **공수**: 3주 (migration + regression test)
- **검증**:
  - pytest: `tests/frontend/test_splittable_parity.py` — 기존 27 smoke 케이스 + 10 추가
  - 수동: paste 세트 10개 복원 ping-pong 시나리오 pass
- **리스크**: 분할 중 regression → v8.8 대장정 복원 어려움. 반드시 feature branch.

#### 1.9 pytest 도입 (규모 1차 — 100 케이스)

- **현상**: `tests/` 디렉토리 자체 없음. smoke 27항목만 (전체 커버리지 ~9%).
- **영향**: 리팩터링 위험. v8.8 시기 "고쳤는데 다른 곳 깨짐" 2주마다 발생.
- **개선안**:
  1. `tests/conftest.py` — 서버 fixture (localhost:8080 + temp data_root)
  2. `tests/auth/` — 로그인/세션/토큰 10 케이스
  3. `tests/tracker/` — 이슈 CRUD + 카테고리 15 케이스
  4. `tests/inform/` — 인폼 create/embed/reply 20 케이스
  5. `tests/splittable/` — override-debug + long adapter 15 케이스
  6. `tests/meeting/` — minutes append + OT-lite 10 케이스
  7. `tests/admin/` — page admin + mail groups 15 케이스
  8. `tests/dashboard/` — chart-render 15 케이스
  9. `pytest.ini` + `requirements-dev.txt` — pytest, httpx, pytest-xdist
- **공수**: 2주 (initial)
- **검증**:
  - CI: GitHub Actions 에서 `pytest -n 4` → 100/100 pass
  - 커버리지: `pytest --cov=backend` → ≥55% (v9.2 에서 75% 목표)
- **리스크**: 테스트 0 유지 → v9.2 에서 무리함. 회귀 두려움으로 개발 속도 저하.

#### 1.10 Meeting 이슈 가져오기 대확장 (기존 v9.1 로드맵 항목)

- **현상**: 회의관리 `/issues/import` 가 이슈 description 만 가져옴. lot_step_snapshot + 이미지 + lot 리스트 미포함.
- **영향**: 회의록에 수동 복붙 → 회의 준비 시간 증가.
- **개선안**: `docs/v9_roadmap.md` 의 "v9.1 회의 이슈 가져오기 확장" 스펙 그대로 구현.
- **공수**: 1주
- **검증**: `test_meeting_import_with_snapshot.py`
- **리스크**: 회의 주관자 수작업 지속.

#### 1.11 Tracker 카테고리 대확장 (기존 v9.1 로드맵 항목)

- **현상**: Monitor/Analysis 카테고리 미구현.
- **개선안**: `docs/v9_roadmap.md` 의 "v9.1 Tracker 카테고리 대확장" 스펙 그대로 구현.
- **공수**: 2주
- **검증**: `test_tracker_analysis_wafer_expansion.py`, `test_tracker_monitor_mail_group.py`

#### 1.12 Home 온보딩 3분 투어

- **현상**: v9.0.3 에서 추가된 "3가지 질문" 섹션 + `FEATURE_GUIDES` 만 존재. 실제 step-by-step 투어 없음.
- **영향**: 신규 유저 자율학습 부재 → admin 에게 1:1 질문 급증.
- **개선안**:
  1. `components/OnboardingTour.jsx` — 9 step (Home → Tracker → Inform → Meeting → SplitTable → Dashboard → ML → Admin → Done)
  2. 각 step: spotlight highlight + 3~5줄 설명 + "다음" 버튼
  3. `admin_settings.user_onboarding_done[uid]` boolean — 첫 로그인 시 자동 시작, 완료 후 스킵
  4. 사이드바 PageGear → "투어 재시작" 메뉴
- **공수**: 1주
- **검증**:
  - smoke: `GET /api/onboarding/status` 응답
  - 수동: 신규 계정 첫 로그인 3분 안에 9 step 완료
- **리스크**: 온보딩 부재 지속 → B2B 사내 도입 속도 저하

---

### v9.2 — 플랫폼화 (1~2개월 단위, +3개월 릴리즈)

인프라·관측성·멀티워커·보안 정교화.

#### 1.13 GitHub Actions CI 도입

- **현상**: 현재 수동 `smoke_test.py` + 수동 `npm run build`. CI 없음.
- **영향**: main push 후 broken 상태 발견이 다음 개발자 책임. v8.8.x 시기 2회 main broken.
- **개선안**:
  1. `.github/workflows/ci.yml`:
     - `pytest -n 4` (v9.1 100 케이스)
     - `npm run build` (vite)
     - `ruff check backend/`
     - `pip-audit` + `npm audit --audit-level=high`
  2. main/release 브랜치에만 push 시 실행
  3. Failing → Slack/webhook 알림 (mgmt-lead 관리)
- **공수**: 3일 (initial)
- **검증**:
  - 첫 PR → CI green
  - 일부러 broken commit → CI red + 알림
- **리스크**: CI 없으면 multi-worker 전환 시 회귀 폭풍

#### 1.14 구조화 로깅 + request_id

- **현상**: `logging.info(f"...")` 방식 단일. 요청 간 상관관계 추적 불가.
- **영향**: 장애 시 "어느 요청이 어느 결과를 냈나" 역추적에 30분+.
- **개선안**:
  1. `backend/core/logging.py` — JSON line formatter (`{"ts": ..., "level": ..., "request_id": ..., "user_id": ..., "endpoint": ..., "msg": ...}`)
  2. FastAPI middleware `RequestIDMiddleware` — 모든 요청에 `X-Request-ID` uuid
  3. `audit_log` 테이블에 `request_id` 컬럼 추가 (기존 row 는 null)
  4. `/api/admin/logs?request_id=...` 엔드포인트 — admin 이 하나의 요청 전체 trace
- **공수**: 1주
- **검증**:
  - pytest: `test_request_id_propagates.py` — 1 요청 → audit_log + stdout 동일 uuid
  - 수동: 회의 save 실패 → admin 로그 검색 → 단일 request_id 필터로 전체 chain 확인
- **리스크**: 로그 혼란 지속 → 장애 대응 시간 길어짐

#### 1.15 SQLite 세션 저장소

- **현상**: `sessions/tokens.json` 단일 파일. gunicorn 멀티워커 시 경합(file lock).
- **영향**: 동시사용자 50+ 되면 로그인 실패 산발 (v9.1 tracker 대확장 이후 예상).
- **개선안**:
  1. `backend/core/session.py` 를 SQLite 백엔드로 마이그레이션:
     - `sessions.db` — `CREATE TABLE sessions(token PRIMARY KEY, user_id, created_at, last_seen, expires_at, ua, ip)`
     - `WAL` 모드 + `timeout=5`
  2. 기존 `tokens.json` 이 있으면 자동 import + `.bak` 보관
  3. `admin_settings.session_backend` ∈ {`file`, `sqlite`} 선택 (default sqlite)
- **공수**: 1주 (migration + 호환성)
- **검증**:
  - pytest: `test_session_multiworker.py` — 10 worker 동시 login → 경합 없음
  - 수동: gunicorn `-w 4` 로 띄워 5명 동시 로그인 OK
- **리스크**: 동시사용자 확장 불가 → 수십명 pilot 에서 멈춤

#### 1.16 Prometheus/Grafana PoC

- **현상**: psutil 15초 폴링만 (Admin 모니터). 요청 latency / 에러율 지표 없음.
- **영향**: "어제 오후 2시 SplitTable 느렸던 이유?" 질문에 답 불가.
- **개선안**:
  1. `prometheus-fastapi-instrumentator` 통합 — `/metrics` endpoint
  2. 주요 counter:
     - `flow_request_total{method,endpoint,status}`
     - `flow_request_latency_seconds{endpoint}` (histogram)
     - `flow_error_total{type}` (override, ml_training, meeting_save)
  3. Grafana Docker compose PoC — dashboards 3개 (requests / errors / resources)
  4. GlitchTip self-host — 에러 집약 (roadmap v9.2 이어감)
- **공수**: 2주 (instrumentation + Grafana PoC)
- **검증**:
  - `curl /metrics` 응답 200 + 유효 Prometheus format
  - Grafana 대시보드 3개에서 live 데이터 가시
- **리스크**: 관측성 없이 plateau → "조직 전체가 의존" 단계 진입 불가

#### 1.17 RBAC 정교화 (row-level)

- **현상**: `require_page_admin` 까지만. 특정 제품/lot 만 보기 불가.
- **영향**: 타 부서 유저가 남의 제품 정보 열람 가능 → 보안 감사 finding 후보.
- **개선안**:
  1. `admin_settings.product_acl` — `{product_id: [user_ids / group_ids]}`
  2. 주요 라우터(`tracker`, `inform`, `splittable`, `meeting`) 에 `_filter_by_product_acl(user, rows)` 헬퍼 적용
  3. Admin UI — 제품별 ACL 매트릭스 (유저 × 제품 체크박스)
  4. Soft-landing: 기본 ACL 없으면 모두 공개(현재 동작 유지)
- **공수**: 2주
- **검증**:
  - pytest: `test_product_acl_filters.py` — A 유저가 B 제품 조회 시 403 또는 empty
  - 수동: Admin 이 제품 ACL 설정 → 일반 유저 사이드바 제품 목록에서 사라짐
- **리스크**: 보안 감사 지적 + 조직 넓히기 어려움

#### 1.18 Secret 관리 + dep 감사

- **현상**: `admin_settings.json` 평문. `pip-audit` 없음.
- **영향**: 파일 leak 시 모든 유저 평문 비밀번호 유출.
- **개선안**:
  1. `admin_settings.json` 을 AES-256 암호화 → `admin_settings.enc` + `.key` (chmod 400)
  2. key 는 `FLOW_SECRET_KEY` env var 또는 first-run prompt
  3. CI 에 `pip-audit` + `npm audit --audit-level=high` — high 이상 finding 시 CI red
  4. `docs/security.md` 업데이트
- **공수**: 1주
- **검증**:
  - `admin_settings.enc` 은 읽을 수 없음 (openssl decrypt 없이)
  - `pip-audit` 0 high findings
- **리스크**: 의존성 CVE 장기 미대응 → 침투 리스크

---

### v9.3+ — 장기 (1~2분기 단위, 로드맵 레벨)

#### 1.19 SPC 페이지 (v8.x 백로그 1위)

- **현상**: SPC 전용 페이지 없음. Dashboard 에서 수식으로 수동 구현 필요.
- **영향**: 양산 양트렌드 상시 감시 불가.
- **개선안**:
  1. `pages/My_SPC.jsx` 신규 — Trend / Historic / spec-out / box / EQP_CHAMBER 컬러링 5뷰
  2. `backend/routers/spc.py` — `/spc/trend`, `/spc/historic`, `/spc/spec-out`
  3. `core/spc_rules.py` — Western Electric Rule 1~4
- **공수**: 1개월
- **검증**: 도메인 엔지니어 UAT + pytest 30 케이스

#### 1.20 DVC 방향성 뱃지

- **현상**: Rc/Rch/Ioff/Ion/Vth/lkg 의 "좋아지는 / 나빠지는" 방향 UI 없음.
- **개선안**:
  1. `admin_settings.dvc_directions` — `{param: ↑|↓|bidir}`
  2. Dashboard/SplitTable 헤더에 작은 삼각형 뱃지 (UXKit.Pill size=xs)
  3. `dvc-curator` 에이전트 자동 제안 → admin 승인 워크플로
- **공수**: 2주
- **검증**: 도메인 UAT

#### 1.21 인과 매트릭스 (공정 방향성)

- **개선안**: ML 결과에 "STI → PC 강함" 같은 공정영역 방향성 등급. 4주.

#### 1.22 ET Time 분석

- **개선안**: 시간대별 heatmap (ettime 페이지) — `docs/v9_roadmap.md` 백로그 인용.

#### 1.23 모바일 뷰 (알림 중심)

- **현상**: desktop-only. 반응형 미흡.
- **개선안**: Tracker bell + Inform 새 글 + Meeting 아젠다 3기능만 모바일 최적화. Next.js PWA 분리 검토.
- **공수**: 1개월

#### 1.24 SSO (SAML / OIDC)

- **현상**: 로컬 name+password 만.
- **개선안**: SAML (SP 쪽) + OIDC Google Workspace/Azure AD. 사내 IdP 연결.
- **공수**: 3주

#### 1.25 i18n 인프라

- **현상**: 한국어/영어 혼재, 하드코딩.
- **개선안**: `react-i18next` + `backend/core/i18n.py` + `locales/{ko,en}.json`. 초기 300 key.
- **공수**: 2주

#### 1.26 유저 가이드 확장

- **현상**: docs 35개 있으나 엔지니어용 step-by-step 없음.
- **개선안**:
  1. `docs/guides/` 하위 페이지별 Markdown (step-by-step, screenshot 포함)
  2. `docs/guides/inform_how_to.md`, `tracker_how_to.md`, `splittable_override.md` 등 10개
  3. `docs/videos/` — Loom 링크 10개 (mgmt-lead 편집)
- **공수**: 2주 (문서만)

#### 1.27 멀티테넌시 (SaaS)

- **현상**: single `data_root`.
- **개선안**: `data_root_per_org` + org 격리. 3개월.

---

## 2. 우선순위 매트릭스

**영향 × 공수** 2x3 그리드 — 영향(상/중/하) × 공수(저 < 3일 / 중 1-2주 / 고 2주+)

| | **공수 저 (≤3일)** | **공수 중 (1-2주)** | **공수 고 (2주+)** |
|---|---|---|---|
| **영향 상** | Quick Win<br/>1.1 ML 정합<br/>1.4 팔레트 통일<br/>1.5 용어 은닉 | 1.7 UXKit Inform<br/>1.10 Meeting 확장<br/>1.14 구조화 로깅<br/>1.15 SQLite 세션 | 1.7 UXKit 4페이지<br/>1.8 SplitTable 분할<br/>1.9 pytest 100<br/>1.17 RBAC |
| **영향 중** | 1.3 애매 기능 정리<br/>1.6 PRODA 중복 | 1.11 Tracker 확장<br/>1.12 온보딩 투어<br/>1.13 CI<br/>1.16 Grafana | 1.19 SPC<br/>1.23 모바일<br/>1.24 SSO |
| **영향 하** | 1.2 Home 가치제안<br/>(UX 체감, 실제 리텐션은 측정 필요) | 1.18 Secret/dep<br/>1.25 i18n | 1.20 DVC 방향성<br/>1.21 인과<br/>1.22 ET Time<br/>1.26 가이드<br/>1.27 멀티테넌시 |

**Quick Win 추천 순서** (v9.0.3 에 전부 수용): 1.1 → 1.4 → 1.5 → 1.3 → 1.6 → 1.2

**대형 프로젝트** (별도 feature branch 필수): 1.7 / 1.8 / 1.9 / 1.19 / 1.24 / 1.27

---

## 3. 앞으로 보완 영역 (점검에 없었던 주제 포함)

### 3.1 관측성

| 하위 | 현재 | v9.2 목표 | v9.3+ |
|---|---|---|---|
| 메트릭 | psutil 15s 폴링 | Prometheus + Grafana | SLO/SLA 대시보드 |
| 에러 트래킹 | `logger.exception` 만 | GlitchTip self-host | Sentry SaaS 옵션 |
| 사용자 이벤트 | `/activity/features` 집계만 | event schema 표준화 (`core/events.py`) | Amplitude/Mixpanel 연동 옵션 |
| 분산 추적 | 없음 | request_id (1.14) | OpenTelemetry 도입 |
| Heartbeat | admin 모니터 | `/health` + `/ready` + `/metrics` 분리 | Kubernetes probe 호환 |

### 3.2 멀티 유저 협업

| 하위 | 현재 | v9.1-9.2 |
|---|---|---|
| 회의록 동시편집 | OT-lite v8.8.15 (rev counter + 409 conflict) | CRDT (yjs) 전환 |
| 실시간 커서/프레즌스 | 없음 | SplitTable 노트 드로어에 avatar dots |
| 변경 알림 | bell + 메일 throttle | live badge (WebSocket 재연결 안정화) |
| 댓글 @mention | 없음 | inform/meeting 통합 @mention |

### 3.3 온보딩

- **v9.1**: 3분 투어 (1.12)
- **v9.2**: FEATURE_GUIDES 30개 + 비디오 10개 (1.26)
- **v9.3**: 신규 가입 → 첫 lot 등록까지 funnel 측정 + dropout 개선

### 3.4 확장성

| 하위 | 현재 | 목표 |
|---|---|---|
| 동시 사용자 | 단일 uvicorn, 20명 검증 | v9.2 gunicorn 4 worker + SQLite 세션, 100명 |
| 데이터 규모 | 30GB 검증 | v9.2 60GB + parquet lazy |
| multi-tenant | single-tenant | v9.3+ 옵션 (1.27) |
| SSO | 없음 | v9.3 SAML + OIDC (1.24) |
| RBAC | 페이지 admin | v9.2 row-level ACL (1.17) |

### 3.5 도메인 심화

| 항목 | 현재 | 미래 릴리즈 |
|---|---|---|
| SPC | 없음 | v9.3 전용 페이지 (1.19) |
| DVC 방향성 | UI 미노출 | v9.3 뱃지 (1.20) |
| 공정 인과 | ML 결과 단일 | v9.3 방향성 등급 (1.21) |
| ET time 분석 | 없음 | v9.3 heatmap (1.22) |
| Grain bridge | 없음 | v9.3+ shot-chip registry (FLOW_APP_ASSESSMENT "Next Priority" #2) |
| ET Reporting | 기본 | v9.3+ step_seq/request_id/재의뢰 (FLOW_APP_ASSESSMENT #1) |
| Optimization | 없음 | v9.4 KNOB 조합 + Pareto + 추천 split (FLOW_APP_ASSESSMENT #3) |

### 3.6 모바일

- **현재**: desktop-only, 반응형 미흡.
- **v9.3+**: Tracker bell + Inform 새 글 + Meeting 아젠다 3기능 모바일 PWA (1.23).
- **장기**: React Native 앱 검토 (사내 배포용, App Store 미예정).

### 3.7 국제화 (i18n)

- **v9.3**: `react-i18next` + 한/영 (1.25).
- **v9.4+**: 중문/일문 사내 요청 시.

### 3.8 문서화

- **현재**: CHANGELOG 상세, ux_standard/maturity 있음. 하지만 **엔지니어용 how-to 부재**.
- **v9.2**: `docs/guides/` 10편 (1.26).
- **v9.3**: 비디오 10편.

---

## 4. 점수 상승 예측

개별 항목별 예상 점수 이동:

| 측면 | v9.0.2 | v9.0.3 | v9.1 | v9.2 | 달성 키 |
|---|---|---|---|---|---|
| 기능 커버리지 | 8.0 | 8.1 | 8.3 | 8.5 | 1.10 / 1.11 / 1.19 |
| 도메인 정합성 | 7.5 | 7.5 | 7.5 | 7.8 | 1.20 (v9.3) |
| **UX 일관성** | **6.5** | **6.8** | **7.5** | **7.8** | **1.7 UXKit 실투입** |
| 성능 | 6.5 | 6.5 | 6.8 | 7.5 | 1.15 / 1.16 |
| **안정성 (회귀)** | **4.0** | **4.2** | **6.5** | **7.5** | **1.9 pytest + 1.13 CI** |
| 운영 관측성 | 5.0 | 5.2 | 6.0 | 7.5 | 1.14 / 1.16 |
| 보안 | 6.0 | 6.0 | 6.3 | 7.5 | 1.17 / 1.18 |
| 확장성 (SaaS) | 3.5 | 3.5 | 4.0 | 5.0 | 1.15 / 1.17 |
| 문서화 | 5.0 | 5.2 | 6.0 | 7.0 | 1.12 / 1.26 |
| **종합** | **7.0** | **7.2** | **7.5** | **8.0** | |

---

## 5. 리스크 및 가정

### 5.1 리스크

| # | 리스크 | 완화 |
|---|---|---|
| R1 | v9.1 UXKit 4페이지 migration 중 regression → 실유저 피드백 폭주 | feature branch + pytest 필수 게이트 |
| R2 | SplitTable 3,480줄 분할 시 paste 세트 시나리오 깨짐 | parity test 10 케이스 선행 |
| R3 | SQLite 세션 전환 시 기존 로그인 세션 전부 invalidate → 유저 재로그인 | migration 시 `tokens.json` 병렬 읽기 기간 2주 |
| R4 | Prometheus 도입 시 instrumentation 누락 → 부분 metric | 주요 20 endpoint 체크리스트 |
| R5 | RBAC row-level 적용 중 일부 쿼리 누락 → 권한 우회 | 보안 재감사 (eval-lead) 게이트 |

### 5.2 가정

- v9.0.3 ~ v9.2 사이 주요 사용자 기반 10~30명 유지 (사내 pilot).
- dev-lead 팀이 feature branch + PR 워크플로 정착 (main-guard v8.8.13+).
- smoke_test.py 는 pytest 도입 후에도 보조용으로 유지 (빠른 sanity).

---

## 6. 실행 순서 요약

### Sprint 1 (v9.0.3, 2주)
- Day 1-2: 1.1, 1.4, 1.5, 1.3, 1.6 (Quick Win 5개)
- Day 3-5: 1.2 Home 가치 제안
- Day 6-7: smoke 확장 (15 케이스 추가 → 총 42)
- Day 8-10: eval-lead 릴리즈 게이트
- 목표 점수: 7.2

### Sprint 2-3 (v9.1-a/b/c, 3주)
- Week 1: 1.7 UXKit Inform + 1.10 Meeting 이슈 확장
- Week 2: 1.7 UXKit Dashboard + 1.11 Tracker 확장 (병렬)
- Week 3: 1.7 UXKit Admin + 1.12 온보딩 투어 (병렬)
- 목표 점수: 7.3

### Sprint 4-6 (v9.1-d, 3주)
- Week 4-6: 1.8 SplitTable 분할 + 1.9 pytest 100
- 목표 점수: 7.5

### Sprint 7-10 (v9.2, 4주)
- Week 7: 1.13 CI + 1.18 Secret
- Week 8: 1.14 구조화 로깅 + 1.15 SQLite
- Week 9-10: 1.16 Prometheus + 1.17 RBAC
- 목표 점수: 8.0

### v9.3+ 장기
- 분기별 1~2개 항목 선택 (1.19~1.27)
- 연 2회 major 릴리즈 계획

---

## 7. 소유권 매트릭스

| 항목 군 | Primary | Secondary | Reviewer |
|---|---|---|---|
| UXKit (1.4, 1.5, 1.7) | dev-lead (dev-uxkit) | ux-reviewer | eval-lead |
| 테스트 (1.9, 1.13) | eval-lead (qa) | dev-lead | mgmt-lead |
| 페이지 분할 (1.8) | dev-lead (dev-splittable) | eval-lead | orchestrator |
| 인프라 (1.14, 1.15, 1.16) | dev-lead (dev-adapter/infra) | eval-lead | orchestrator |
| 보안 (1.17, 1.18) | dev-lead + eval-lead (audit) | mgmt-lead | orchestrator |
| 도메인 (1.19, 1.20, 1.21) | dev-lead (dev-dvc, dev-causal) | 도메인 엔지니어 UAT | eval-lead |
| 문서/온보딩 (1.12, 1.26) | mgmt-lead (reporter) | dev-lead | orchestrator |

---

*본 문서는 dev-lead (기술 개선안) + eval-lead (검증 조건) 통합 산출물이며, mgmt-lead 의 한국어 요약본(`v9_improvement_summary_ko.md`) 과 짝을 이룬다. 릴리즈 게이트 통과는 eval-lead 책임.*
