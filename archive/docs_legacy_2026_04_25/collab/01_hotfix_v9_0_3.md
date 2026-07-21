# 01. Hotfix v9.0.3 (H1~H6)

**시점**: +2주 / **점수 목표**: 7.0 → 7.2 / **항목 수**: 6 (모두 반일~1일)
**성격**: 단일 PR 크기, 대형 리팩터 없음. 체감 개선 위주.
**추천 진행 순서**: H1 → H2 → H3 → H4 → H5 → H6 (영향 × 공수 Quick Win 순)

---

## H1. ML 탭 상태 정합 (PLANNED → BETA)

- **상태**: todo
- **담당 후보**: either (claude/codex 모두 가능, FE 위주 편집)
- **변경 파일**:
  - `D:\TEST_Making_Video\semi_all\flow\frontend\src\config.js`
  - `D:\TEST_Making_Video\semi_all\flow\frontend\src\pages\My_ML.jsx`
  - `D:\TEST_Making_Video\semi_all\flow\frontend\src\pages\My_Home.jsx`
- **변경 내용**:
  - `config.js`: `featureMap.ML` 의 `status: 'PLANNED'` → `'ACTIVE'`. 추가 필드 `{ status: 'beta', badge: 'BETA' }`.
  - `My_ML.jsx` 최상단에 `<UXKit.Banner tone='info'>` "ML 기능은 beta 입니다. 현업 질문 → Inline_ET + KNOB 요약을 먼저 시도하세요" 삽입.
  - `My_Home.jsx` 의 ML 카드에 `"BETA"` prefix 뱃지 추가.
- **완료 조건 (DoD)**:
  - [ ] smoke: `GET /api/config` 응답에 `ML.status === 'beta'` 포함
  - [ ] 수동: Home → ML 카드에 BETA 뱃지 가시
  - [ ] 수동: My_ML 상단 배너 노출 및 Inline_ET 탭 진입 동작
  - [ ] `npm run build` 성공
- **의존성**: 없음 (독립)
- **예상 공수**: 반일
- **리스크**:
  - 안 하면: 유저가 ML 기능 외부 도구로 대체 사용 → 제품 ROI 손실
  - 하다가: UXKit.Banner 가 Inform/Tracker 외에서 미검증 → 스타일 깨짐 가능 (페이지 로컬 컨테이너로 감싸 방어)

---

## H2. Dashboard 팔레트 통일 (COLORS/PASTEL → UXKit.chartPalette)

- **상태**: todo
- **담당 후보**: either
- **변경 파일**:
  - `D:\TEST_Making_Video\semi_all\flow\frontend\src\UXKit.jsx`
  - `D:\TEST_Making_Video\semi_all\flow\frontend\src\pages\My_Dashboard.jsx`
- **변경 내용**:
  - `UXKit.jsx` 에 `export const chartPalette = { series: [...12색], pastel: [...12색], heat: ['#...', '#...'] }` 추가.
  - `My_Dashboard.jsx` 에서 `const COLORS = [...]`, `const PASTEL = [...]` 두 블록 삭제 → `UXKit.chartPalette.series` 참조로 일괄 치환.
  - 차트 종류별 할당 규칙 고정: `line/bar/scatter → series`, `pie → pastel`, `heatmap → heat`.
- **완료 조건 (DoD)**:
  - [ ] grep: `My_Dashboard.jsx` 내 `const COLORS`, `const PASTEL` 존재 0건
  - [ ] 수동: 3차트 동시 비교 시 series 0 (pie) 와 series 0 (bar) 색상 동일
  - [ ] `npm run build` 성공
  - [ ] 기존 대시보드 PDF 캡처와 색상 톤 대조 시 대폭 이탈 없음 (회의자료 호환)
- **의존성**: 없음 (F1 UXKit 실투입의 선행 준비)
- **예상 공수**: 반일
- **리스크**:
  - 안 하면: 팔레트 카오스 지속, UX 일관성 점수 정체
  - 하다가: 기존 회의자료 색상 링크가 깨짐 (pastel 블록은 그대로 유지하여 호환)

---

## H3. SplitTable 내부용어 은닉 (고급 탭 분리)

- **상태**: todo
- **담당 후보**: either
- **변경 파일**:
  - `D:\TEST_Making_Video\semi_all\flow\frontend\src\pages\My_SplitTable.jsx`
  - `D:\TEST_Making_Video\semi_all\flow\docs\splittable_terms_ko.md` (신규)
- **변경 내용**:
  - `My_SplitTable.jsx` 에서 PageGear(톱니) 모달 내부에 `<UXKit.TabStrip>` 로 "기본" / "고급" 탭 구조 추가.
  - 기본 탭 라벨: 한국어 (`override-debug → 적용 진단`, `long-items → 긴 형식 항목`, `fab-roots → FAB 원본 루트`).
  - 고급 탭에만 엔지니어 용어 유지.
  - 신규 `splittable_terms_ko.md` 에 용어 매핑 10개 (override-debug, long-items, fab-roots, _effective_modules, override-debug-fallback, root_scope, match_mode, use_override, scan_long_*, pivot_*) 정의.
- **완료 조건 (DoD)**:
  - [ ] grep: `My_SplitTable.jsx` 기본 뷰 (톱니 외) 에서 문자열 `override-debug` 0건
  - [ ] 수동: 일반 유저 첫 화면에 `fab-roots`, `long-items` 단어 없음
  - [ ] 수동: 톱니 → 고급 탭 에서 기존 진단 기능 접근 가능
  - [ ] 용어집 `.md` 10개 엔트리 이상
- **의존성**: 없음 (단, F1-d (UXKit SplitTable) 완전 치환 시 동일 영역 건드림 — F1-d 는 3주 후이므로 충돌 없음)
- **예상 공수**: 1일
- **리스크**:
  - 안 하면: 신규 유저 학습곡선 높음 → 이탈
  - 하다가: 일반 탭 → 고급 탭 라우팅 버그 시 진단 기능 접근 불가. 탭 switch 시 기존 상태 유지 테스트 필수.

---

## H4. 사이드바 애매 탭 4개 정리 (WaferLayout/Messages/TableMap/DevGuide)

- **상태**: todo
- **담당 후보**: either
- **변경 파일**:
  - `D:\TEST_Making_Video\semi_all\flow\frontend\src\config.js`
  - `D:\TEST_Making_Video\semi_all\flow\frontend\src\pages\My_WaferLayout.jsx`
  - `D:\TEST_Making_Video\semi_all\flow\frontend\src\pages\My_Messages.jsx`
  - `D:\TEST_Making_Video\semi_all\flow\frontend\src\pages\My_Admin.jsx` (TableMap embed 추가)
  - `D:\TEST_Making_Video\semi_all\flow\backend\routers\admin.py` (devguide_user 권한 + sidebar response)
- **변경 내용**:
  1. **WaferLayout**: `My_WaferLayout.jsx` 최상단에 `<UXKit.Banner tone='neutral'>` "라인별 wafer 레이아웃 참고용. SplitTable 에서 특정 step 데이터 보기 전 사전조사" (3줄). 현 상태 유지.
  2. **Messages**: `config.js` 의 `pages.Messages.label` 을 "알림함" → "내부 알림함" 변경. 용도 한 줄 툴팁 추가.
  3. **TableMap**: `config.js` 에 `pages.TableMap.requiresRole: 'page_admin'` 추가 → 일반 유저 사이드바 숨김. Admin 페이지 내부에 `<TableMapInline />` 탭 추가 또는 iframe.
  4. **DevGuide**: `admin_settings` 신규 플래그 `devguide_user: [uid,...]`. `config.js` 에 `pages.DevGuide.requiresRole: 'devguide_user'` 추가. 기본 유저 숨김.
- **완료 조건 (DoD)**:
  - [ ] smoke: 일반 유저 토큰으로 `GET /api/config/sidebar` → `TableMap`, `DevGuide` 미포함
  - [ ] 수동: 일반 계정 사이드바 11개 탭, admin 계정 15개 탭
  - [ ] 수동: WaferLayout 진입 시 3줄 배너 노출
  - [ ] 수동: Admin 페이지 내 TableMap 서브탭 접근 가능
  - [ ] `admin_settings.devguide_user` 리스트 Admin UI 에서 편집 가능
- **의존성**: 없음
- **예상 공수**: 1일
- **리스크**:
  - 안 하면: 사이드바 인지부하 지속, 핵심 기능 도달률 하락
  - 하다가: 기존에 TableMap 즐겨찾던 일반 유저가 당황. PageGear 안내 배너로 "Admin 탭 → TableMap" 경로 가이드 필요.

---

## H5. PRODA 중복 근본 차단 (저장 시점 dedup + 새벽 cron)

- **상태**: todo
- **담당 후보**: either (BE 위주)
- **변경 파일**:
  - `D:\TEST_Making_Video\semi_all\flow\backend\routers\inform.py` (or wherever `/api/informs/products/add` 정의)
  - `D:\TEST_Making_Video\semi_all\flow\backend\core\product_dedup.py` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\backend\scheduler.py` (기존 스케줄러에 cron 등록)
  - `D:\TEST_Making_Video\semi_all\flow\tests\inform\test_product_add_duplicate.py` (신규)
- **변경 내용**:
  - `/api/informs/products/add` POST 파라미터에 `_dedup_on_save: bool = True` 추가. 저장 전 `name.strip().casefold()` 기준 기존 조회 → 존재 시 `409 Conflict` + `{"existing_id": ..., "message": "기존 product 사용"}`.
  - `core/product_dedup.py` — 전체 `product` 테이블 스캔 → `strip+casefold` 키로 그룹화, 최근 갱신본만 유지, 나머지 archive.
  - `scheduler.py` 에 매일 03:00 `product_dedup.run()` 실행 등록.
  - pytest: 동일 이름 (대소문자 혼용, 공백 포함) 2회 POST → 두 번째 409 반환 검증.
- **완료 조건 (DoD)**:
  - [ ] pytest: `test_product_add_duplicate_returns_409` pass
  - [ ] smoke: 사이드바 제품 목록 중복 0건
  - [ ] 새벽 3시 cron 1회 실행 로그 확인
  - [ ] 409 시 FE 에서 기존 product 로 자동 유도 (Toast "기존 제품으로 이동")
- **의존성**: 없음 (F3 pytest 프레임워크 선행 불필요 — 최소 스모크로 대체 가능)
- **예상 공수**: 1일
- **리스크**:
  - 안 하면: 제품 중복 재발 시 유저 신뢰 손상
  - 하다가: casefold 적용 시 기존 대문자 제품명 (예: `ABC_123` vs `abc_123`) 병합 오류 발생 가능. archive 형태로 백업 후 admin 수동 복원 가능하게 설계.

---

## H6. Home 가치 제안 섹션 ("3가지 질문")

- **상태**: todo
- **담당 후보**: either
- **변경 파일**:
  - `D:\TEST_Making_Video\semi_all\flow\frontend\src\pages\My_Home.jsx`
  - `D:\TEST_Making_Video\semi_all\flow\backend\routers\home.py` (신규 또는 기존)
- **변경 내용**:
  - `My_Home.jsx` 최상단에 `<UXKit.TwoCol>` 기반 "3가지 질문" 섹션 신규:
    | 질문 | 추천 페이지 | 추천 액션 |
    |---|---|---|
    | "내 lot 문제있나?" | Tracker | 카테고리 = Analysis 이슈 생성 버튼 |
    | "plan 대로 흐르고 있나?" | SplitTable | 최근 본 제품 자동 진입 + memo |
    | "이번 주 인폼 요약?" | Inform | `_effective_modules` 자동 필터 적용 |
  - 각 카드 하단에 `<UXKit.Pill tone="info">` "3분 투어" 링크 (F1+ 온보딩 투어 재료로 연계).
  - 기존 기능 카드 그리드는 하단으로 이동 (삭제 금지).
  - BE `GET /api/home/summary` 엔드포인트가 `suggested_actions: [{question, route, hint}, ...]` 최소 3건 응답 (없으면 기본 3건 하드코딩).
- **완료 조건 (DoD)**:
  - [ ] smoke: `GET /api/home/summary` 응답에 `suggested_actions` 필드 3건 이상
  - [ ] 수동: 신규 계정 로그인 → Home 첫 화면 "3가지 질문" 상단 가시
  - [ ] 수동: 3개 카드 클릭 시 각 추천 페이지로 정상 이동 + 추천 액션 반영
  - [ ] 기존 기능 카드 그리드 하단에 여전히 존재
  - [ ] `npm run build` 성공
- **의존성**: H1 (ML 탭 BETA) 이 먼저면 Home 카드에 BETA 뱃지 노출 연계 가능. 단, 독립 병렬 가능.
- **예상 공수**: 1일
- **리스크**:
  - 안 하면: 신규 유저 온보딩 실패율 40%+ 지속
  - 하다가: `/api/home/summary` 가 DB 쿼리 과다 시 Home 로딩 지연. MVP 는 하드코딩 3건 + lazy로 대응.

---

## v9.0.3 릴리즈 게이트 (eval-lead 검증)

핫픽스 6건 완료 후 eval-lead 가 체크:

- [ ] 6개 항목 모두 DoD 충족
- [ ] smoke_test.py 확장 (27 → 42 케이스, +15 신규: H1~H6 각 smoke + 누락 shore)
- [ ] 사내 실DB 환경 (1.RAWDATA_DB_*) 수동 회귀 pass
- [ ] CHANGELOG_v9.0.3.md 작성
- [ ] setup.py 재빌드 + size 비정상 증가 없음

목표 점수: **7.0 → 7.2** (UX 체감 0.3, 안정성 0.2, 문서 0.2 상승)

---

## 참고

- 원본 스펙: [`_archive/v9_improvement_plan.md`](./_archive/v9_improvement_plan.md) §1.1~1.6
- 관련 문서: `docs/FLOW_APP_ASSESSMENT_2026_04_24.md`
- 다음 단계: [`02_feature_v9_1.md`](./02_feature_v9_1.md) (F1~F3 대형 feature)
