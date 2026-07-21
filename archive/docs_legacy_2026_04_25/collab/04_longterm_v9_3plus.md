# 04. Long-term v9.3+ (L1~L9)

**시점**: +6개월~ / **점수 목표**: 8.0 → 8.5 / **항목 수**: 9 (각 2주~3개월)
**성격**: 분기별 1~2건 선택. 로드맵 레벨. 사람 승인 · 도메인 UAT 필수.
**전제**: v9.2 플랫폼화 완료 (CI · 관측성 · RBAC · Secret · SQLite 세션)

> 본 섹션 항목은 대부분 "대형 신규 기능" 또는 "인프라 이전". claude/codex 가 단독으로 완주하기는 어렵고, 각 항목마다 설계 단계에서 eval-lead + orchestrator + 도메인 엔지니어 협업 필요.

---

## L1. SPC 페이지 (v8.x 백로그 1위)

- **상태**: todo
- **담당 후보**: claude (dev-spc 단독) · human-required (도메인 UAT)
- **변경 파일**:
  - `D:\TEST_Making_Video\semi_all\flow\frontend\src\pages\My_SPC.jsx` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\frontend\src\config.js` (SPC 탭 추가)
  - `D:\TEST_Making_Video\semi_all\flow\backend\routers\spc.py` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\backend\core\spc_rules.py` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\tests\spc\` (신규 전체)
- **변경 내용**:
  - `My_SPC.jsx` — 5뷰:
    1. Trend — 시계열 라인
    2. Historic — 기간 비교 bar
    3. Spec-out — 규격 이탈 alert 리스트
    4. Box — box plot 분포
    5. EQP_CHAMBER — 장비/챔버 색상 매핑
  - `spc.py` 엔드포인트: `/spc/trend`, `/spc/historic`, `/spc/spec-out`, `/spc/box`, `/spc/chambers`
  - `core/spc_rules.py` — Western Electric Rule 1~4 구현 (Rule 1: 3σ, Rule 2: 2σ 연속 2, Rule 3: 1σ 연속 4 중 3, Rule 4: 연속 8 same side)
- **완료 조건 (DoD)**:
  - [ ] 도메인 엔지니어 UAT 통과 (최소 3명 현업 사용자)
  - [ ] pytest 30 케이스 (5뷰 × 6 케이스)
  - [ ] 실제 ML_TABLE 데이터로 Rule 1~4 정확도 검증
  - [ ] smoke: `/spc/trend` 응답 < 2초 (1개 제품 · 6개월)
- **의존성**: v9.2 전체 완료
- **예상 공수**: 1개월
- **리스크**:
  - 안 하면: 양산 상시 감시 불가 → Dashboard 에서 수식 수동 구현 지속
  - 하다가: Rule 구현 오류 → 도메인 엔지니어 신뢰 손상. UAT 선행 필수.

---

## L2. DVC 방향성 뱃지

- **상태**: todo
- **담당 후보**: claude (dev-lead · dvc-curator 자문)
- **변경 파일**:
  - `D:\TEST_Making_Video\semi_all\flow\backend\admin_settings.py` (`dvc_directions` 추가)
  - `D:\TEST_Making_Video\semi_all\flow\backend\routers\admin.py` (DVC CRUD)
  - `D:\TEST_Making_Video\semi_all\flow\frontend\src\UXKit.jsx` (Pill size=xs 지원)
  - `D:\TEST_Making_Video\semi_all\flow\frontend\src\pages\My_Dashboard.jsx` (뱃지 렌더)
  - `D:\TEST_Making_Video\semi_all\flow\frontend\src\pages\My_SplitTable.jsx` (뱃지 렌더)
  - `D:\TEST_Making_Video\semi_all\flow\frontend\src\pages\My_Admin.jsx` (DVC 매트릭스 탭)
- **변경 내용**:
  - `admin_settings.dvc_directions` — `{param: ↑|↓|bidir}` 구조 (예: `{"Rc": "↓", "Ioff": "↓", "Ion": "↑", "Vth": "bidir"}`)
  - Dashboard/SplitTable 헤더 셀에 `<UXKit.Pill size="xs" tone="neutral">` 작은 삼각형 뱃지
  - `dvc-curator` 에이전트 자동 제안 → Admin 승인 워크플로 (`admin_settings.dvc_suggestions` 큐)
  - Admin UI — 파라미터 × 방향 매트릭스 편집기
- **완료 조건 (DoD)**:
  - [ ] 도메인 UAT — 주요 parameter 10+ 방향 합의
  - [ ] Dashboard/SplitTable 헤더 뱃지 가시
  - [ ] dvc-curator 제안 → 승인 flow 동작
  - [ ] pytest 10 케이스
- **의존성**: F1 (UXKit.Pill) 선행
- **예상 공수**: 2주
- **리스크**:
  - 안 하면: 신규 유저가 "Rc 커질수록 좋은 건가?" 질문 급증
  - 하다가: 방향 설정 오류 시 도메인 해석 뒤집힘. dvc-curator 자동 제안 + 사람 승인 이중화 필수.

---

## L3. 인과 매트릭스 (공정 방향성 등급)

- **상태**: todo
- **담당 후보**: claude (dev-ml · causal-analyst 자문) · human-required (도메인)
- **변경 파일**:
  - `D:\TEST_Making_Video\semi_all\flow\backend\routers\ml.py` (causal 엔드포인트 추가)
  - `D:\TEST_Making_Video\semi_all\flow\backend\core\causal.py` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\frontend\src\pages\My_ML.jsx` (인과 매트릭스 뷰 추가)
- **변경 내용**:
  - ML 결과에 "STI → PC 강함", "GATE → Vth 약함" 같은 공정영역 방향성 등급
  - `core/causal.py` — SHAP + partial dependence 기반 방향성 추출 로직
  - `My_ML.jsx` — 공정영역 × 파라미터 matrix heatmap (강/중/약)
- **완료 조건 (DoD)**:
  - [ ] 도메인 UAT — 주요 영역 조합 10+ 일치
  - [ ] 기존 ML 학습 데이터에서 역방향 결과 0건
  - [ ] pytest 15 케이스
- **의존성**: L2 (DVC 방향성) 선행 권장 (방향 해석에 활용)
- **예상 공수**: 4주
- **리스크**:
  - 안 하면: ML 결과 단일 수치만 제공 → 엔지니어 해석 수작업
  - 하다가: causal 추론 오류 → ML 신뢰 손상

---

## L4. ET Time 분석 (시간대별 heatmap)

- **상태**: todo
- **담당 후보**: claude (dev-ettime)
- **변경 파일**:
  - `D:\TEST_Making_Video\semi_all\flow\frontend\src\pages\My_ETTime.jsx` (기존 확장)
  - `D:\TEST_Making_Video\semi_all\flow\backend\routers\ettime.py` (heatmap 엔드포인트 추가)
- **변경 내용**:
  - `docs/v9_roadmap.md` 백로그 스펙 그대로 구현
  - 시간대(시/일/요일) × 파라미터 heatmap
  - 장비 이상 탐지 (hourly spike detection)
- **완료 조건 (DoD)**:
  - [ ] heatmap 렌더 200 응답 < 3초
  - [ ] 장비 spike 1시간 단위 탐지 검증 (실데이터)
  - [ ] pytest 10 케이스
- **의존성**: 없음
- **예상 공수**: 3주
- **리스크**: ET 원 데이터 schema 변경 시 재작업

---

## L5. 모바일 뷰 (알림 중심 PWA)

- **상태**: todo
- **담당 후보**: claude (FE) · human-required (배포 승인)
- **변경 파일**:
  - `D:\TEST_Making_Video\semi_all\flow\frontend\src\mobile\` (신규 디렉토리)
  - `D:\TEST_Making_Video\semi_all\flow\frontend\public\manifest.json` (PWA)
  - `D:\TEST_Making_Video\semi_all\flow\frontend\src\service-worker.js` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\backend\routers\mobile.py` (신규, 모바일 전용 API)
- **변경 내용**:
  - 3 기능만 모바일 최적화:
    1. Tracker bell (알림 목록 + push)
    2. Inform 새 글 (읽기/답글)
    3. Meeting 아젠다 (당일 스케줄)
  - PWA manifest + service worker (offline cache)
  - Next.js PWA 분리 검토 (별도 repo 로 splitoff 가능성)
- **완료 조건 (DoD)**:
  - [ ] iOS Safari + Android Chrome 설치 가능 PWA
  - [ ] 3기능 오프라인 캐시 동작
  - [ ] push 알림 수신 (FCM 연동)
  - [ ] 수동: 3명 현업 사용자 UAT
- **의존성**: v9.2 전체 완료
- **예상 공수**: 1개월
- **리스크**:
  - 안 하면: 현장/이동 대응 불가
  - 하다가: iOS PWA 제약 (push 지원 한계) → 네이티브 대안 검토 필요

---

## L6. SSO (SAML / OIDC)

- **상태**: todo
- **담당 후보**: either (BE) · human-required (사내 IdP 협의)
- **변경 파일**:
  - `D:\TEST_Making_Video\semi_all\flow\backend\core\sso.py` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\backend\routers\auth.py` (SSO callback 추가)
  - `D:\TEST_Making_Video\semi_all\flow\requirements.txt` (python3-saml, authlib 추가)
  - `D:\TEST_Making_Video\semi_all\flow\docs\sso_setup.md` (신규)
- **변경 내용**:
  - SAML 2.0 (SP 측) + OIDC (Google Workspace / Azure AD) 듀얼 지원
  - `admin_settings.sso_config` — 사내 IdP 설정
  - 기존 name+password 병행 유지 (admin 전환 스위치)
- **완료 조건 (DoD)**:
  - [ ] 사내 IdP (AD FS or Azure AD) 연동 검증
  - [ ] `docs/sso_setup.md` — IT 팀용 설정 가이드
  - [ ] pytest 20 케이스 (mock IdP)
  - [ ] 롤백 절차 문서화 (SSO 장애 시 로컬 로그인 복귀)
- **의존성**: P3 (SQLite 세션) + P5 (RBAC) 선행
- **예상 공수**: 3주
- **리스크**:
  - 안 하면: 사내 IdP 정책 준수 불가
  - 하다가: SSO 장애 시 전체 로그인 차단 → 로컬 fallback 필수

---

## L7. i18n 인프라 (한/영 기본)

- **상태**: todo
- **담당 후보**: either (FE + BE)
- **변경 파일**:
  - `D:\TEST_Making_Video\semi_all\flow\frontend\src\locales\ko.json` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\frontend\src\locales\en.json` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\frontend\src\i18n.js` (신규, react-i18next 설정)
  - `D:\TEST_Making_Video\semi_all\flow\backend\core\i18n.py` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\frontend\package.json` (react-i18next 추가)
- **변경 내용**:
  - `react-i18next` 설정 + 초기 300 key
  - 주요 페이지 한국어 하드코딩 → `t('key')` 치환
  - BE `core/i18n.py` — 에러 메시지 locale 대응
  - 유저 설정 `admin_settings.user_locale[uid]` (기본 ko)
- **완료 조건 (DoD)**:
  - [ ] 주요 페이지 (Home, Inform, Tracker, Meeting, SplitTable) 한/영 토글 동작
  - [ ] 300 key 번역 완료 (ko 100% / en 80%+)
  - [ ] pytest 10 케이스 (locale 분기)
- **의존성**: F1 (UXKit) 완료 후 진행 권장 (스트링 중앙화 용이)
- **예상 공수**: 2주
- **리스크**: 번역 품질 이슈 — 도메인 전문 용어 (fab, lot 등) 영문화 시 의미 변질

---

## L8. 유저 가이드 확장 (guides 10편 + 비디오 10편)

- **상태**: todo
- **담당 후보**: either (문서) · human-required (비디오 촬영)
- **변경 파일**:
  - `D:\TEST_Making_Video\semi_all\flow\docs\guides\inform_how_to.md` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\docs\guides\tracker_how_to.md` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\docs\guides\splittable_override.md` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\docs\guides\meeting_minutes.md` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\docs\guides\dashboard_y_multi.md` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\docs\guides\admin_mail_groups.md` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\docs\guides\ml_beta.md` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\docs\guides\filebrowser_hive.md` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\docs\guides\tablemap_lineage.md` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\docs\guides\onboarding_tour.md` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\docs\videos\README.md` (신규, Loom 링크 10개)
- **변경 내용**:
  - 페이지별 step-by-step Markdown + screenshot
  - 10편 가이드 + 10편 비디오 (Loom 링크, mgmt-lead 편집)
- **완료 조건 (DoD)**:
  - [ ] 10편 Markdown 완성 (각 페이지당 screenshot 5+ 장)
  - [ ] 10편 Loom 링크 유효 (3~5분/편)
  - [ ] Home `FEATURE_GUIDES` 에서 직접 링크 연결
  - [ ] 사내 공지 1회 배포
- **의존성**: 없음 (v9.1 이후 병렬 가능)
- **예상 공수**: 2주 (문서만)
- **리스크**: 비디오 제작 지연 → 문서만 선 배포 + 비디오 단계적 추가

---

## L9. 멀티테넌시 (SaaS 옵션)

- **상태**: todo
- **담당 후보**: human-required (설계 단계) · claude (구현)
- **변경 파일**:
  - `D:\TEST_Making_Video\semi_all\flow\backend\core\tenancy.py` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\backend\main.py` (tenant middleware 추가)
  - 거의 모든 router · core 모듈에 tenant 전파 코드 (대공사)
- **변경 내용**:
  - 현재 single `data_root` → `data_root_per_org` 구조
  - org 격리: `tenants.db` + `data/{tenant_id}/` 파일 구조
  - subdomain-based tenant resolution (`acme.flow.sx` → tenant=acme)
  - Admin UI — tenant 생성/삭제/상태 관리
- **완료 조건 (DoD)**:
  - [ ] 2 tenant 동시 운영 검증 (격리 확인)
  - [ ] 기존 single-tenant 모드 유지 (soft-landing)
  - [ ] pytest 30 케이스 (격리 검증 중심)
  - [ ] SSO per-tenant 설정 가능 (L6 확장)
- **의존성**: v9.2 전체 + L6 (SSO) 선행
- **예상 공수**: 3개월 (대공사)
- **리스크**:
  - 안 하면: 외부 판매 옵션 없음
  - 하다가: 격리 누락 시 데이터 유출 대형 사고. 보안 재감사 다회 + pen-test 필수.

---

## v9.3+ 릴리즈 전략

- **분기별 선택**: 9개 항목 중 분기당 1~2건 선택
- **필수 통과**: 도메인 UAT (L1/L2/L3) · 보안 재감사 (L6/L9) · 현업 3명+ 검증 (L5)
- **로드맵 리뷰**: 반기 1회 orchestrator + dev-lead + eval-lead + mgmt-lead 합동 리뷰

**추천 분기별 순서** (orchestrator 제안):
1. **Q1 (v9.3)**: L1 SPC + L2 DVC 방향성 (도메인 정합성 0.3↑)
2. **Q2 (v9.4)**: L8 가이드 확장 + L7 i18n (문서/접근성 1.0↑)
3. **Q3 (v9.5)**: L6 SSO + L5 모바일 (확장성 1.0↑)
4. **Q4 (v9.6+)**: L3 인과 + L4 ET Time (분석 심화)
5. **2027 ~**: L9 멀티테넌시 (SaaS 전략 결정 후)

---

## 참고

- 원본 스펙: [`_archive/v9_improvement_plan.md`](./_archive/v9_improvement_plan.md) §1.19~1.27
- 도메인 관련: `docs/v9_roadmap.md` · `docs/flow_maturity_2026_04_23.md`
- 종합 로드맵: `_archive/v9_improvement_summary_ko.md` (장기 섹션)
- 협업 허브: [`README.md`](./README.md)
