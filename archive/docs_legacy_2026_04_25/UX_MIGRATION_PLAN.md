# flow UX Migration Plan

목적: `docs/ux_standard.md`와 `frontend/src/components/UXKit.jsx`를 실제 페이지 구현 경로에 연결해서, 페이지별 ad-hoc 스타일과 hard-coded hex 사용을 줄이고 UI 회귀를 막는다.

## 현재 진단

- 표준 문서 있음: `docs/ux_standard.md`
- 공용 컴포넌트 있음: `frontend/src/components/UXKit.jsx`
- 실제 페이지 적용은 매우 낮음
- 핵심 페이지에 hex 하드코딩 다수
- 결과:
  - 페이지별 톤 차이 발생
  - 버튼/탭/배너/헤더 패턴 중복
  - 버그 수정 시 스타일 회귀 반복

## 문제 유형

1. 공통 컴포넌트 미사용
- `PageHeader`, `TabStrip`, `Pill`, `Button`, `Banner`, `DataTable`를 직접 구현으로 우회

2. 색상 의미 계층 부재
- 성공/경고/실패/정보 색을 페이지마다 직접 hex로 사용
- 같은 의미인데 다른 색이 나옴

3. 구조와 표현 혼재
- 도메인 로직과 스타일 로직이 같은 파일에서 섞임
- 유지보수와 리뷰가 어려움

## 원칙

1. 새 UI는 먼저 UXKit로 조합한다
2. 색은 `var(--*)`, `uxColors`, `statusPalette` 우선
3. hard-coded hex는 도메인 전용 표현이 아니면 제거한다
4. 한 페이지를 고칠 때는 같은 패턴을 한 번에 묶어서 교체한다
5. 발표/캡처 노출이 잦은 화면은 가독성을 우선한다

## 마이그레이션 우선순위

### 1. Dashboard

목표:
- 발표용 가독성 강화
- 상단 헤더/탭/배지/버튼을 UXKit 기반으로 통일

치환 대상:
- header → `PageHeader`
- section nav → `TabStrip`
- alert badge → `Pill`
- action button → `Button`

추가 기준:
- Trend 차트는 발표형 톤
- Distribution 통계표는 `Median`, `Std Dev` 중심

상태:
- 1차 적용 완료

### 2. Admin

목표:
- 탭/버튼/배너/리스트를 공통 패턴으로 통일
- runtime error 대응 시 UI도 같이 안정화

치환 대상:
- 탭 전환 → `TabStrip`
- 액션 버튼 → `Button`
- 에러/성공 메시지 → `Banner`
- 사용자/설정 목록 → `DataTable`

우선 구현:
- bulk user paste UI
- forgot-password 연계 상태 안내 배너

### 3. Inform

목표:
- 인폼 등록/상세/메일 프리뷰를 같은 톤으로 정리
- 상태/종류/대상 라벨을 의미 색으로 통일

치환 대상:
- module/reason/category badge → `Pill`
- 메일 발송 상태 → `Banner`
- 상단 섹션 헤더 → `PageHeader`
- 일부 summary table → `DataTable`

### 4. SplitTable

목표:
- 현업 색 규칙은 유지하되 공통 팔레트로 정리
- 경고/불일치/plan 상태색을 semantic tone으로 통일

치환 대상:
- 버튼류 → `Button`
- 상태 라벨 → `Pill`
- 성공/경고/에러 tone → `statusPalette`

예외:
- wafer/parameter 상태 셀의 도메인 색은 일부 유지 가능
- 단, 직접 hex 남발은 피하고 팔레트화

## 완료 기준

한 페이지가 완료로 보려면 아래를 만족해야 한다.

1. 헤더/탭/버튼/배너가 UXKit 기반
2. 의미 색이 `statusPalette` 또는 토큰 사용
3. 새 기능 추가 시 ad-hoc hex가 늘지 않음
4. 캡처/발표 관점에서 정보 계층이 분명함

## 점검 체크리스트

- `import ... from "../components/UXKit"` 존재
- 경고/성공/에러 색이 semantic tone 사용
- 탭이 `TabStrip` 사용
- 상단 영역이 `PageHeader` 사용
- 버튼이 `Button` 사용
- empty 상태가 `EmptyState` 사용
- 동일 의미의 badge가 페이지마다 다른 색을 쓰지 않음

## 권장 작업 순서

1. Dashboard 2차 정리
2. Admin 마이그레이션
3. Inform 마이그레이션
4. SplitTable 팔레트 정리
5. 남은 page 전수 점검

## 비고

- `TableMap`, `WF Layout`처럼 도메인 시각화가 강한 화면은 완전 공통화보다 `header / action / badge / empty`만 먼저 통일하는 것이 좋다.
- 도메인 전용 색은 허용하되, 그 의미가 명확하고 반복될 때는 UXKit 팔레트로 끌어올린다.
