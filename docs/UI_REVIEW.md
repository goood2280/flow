# UI Consistency Review

작성: 2026-05-10 (mtime 기준 — 명시 버전 표기는 사용하지 않음)

Flow 앱의 UI 통일성을 코드 레벨에서 점검한다. 핵심 진단은 **"공용 primitive와 design token은 잘 갖춰져 있지만, 11개 페이지가 충실히 쓰지 않아 Inline style/hardcoded color/직접 fetch가 폭증"**이다. 토큰 자체를 새로 만들 필요는 적고, **사용 강제와 정리**가 quick win이다.

## Foundation 현황 (이미 있음)

| 영역 | 위치 | 상태 |
|---|---|---|
| Primitive (Pill/Panel/Button/DataTable/EmptyState/Banner/Modal/PageHeader/TabStrip/PageShell/Toolbar/TwoCol/Field) | `frontend/src/components/UXKit.jsx` | ✅ 잘 정의됨 |
| 모달 (ESC/backdrop) | `frontend/src/components/Modal.jsx` | ✅ 있음 (사용률 낮음) |
| CSS 변수 — bg/text/status/accent/border/radius | `frontend/src/global.css`, `useFlowShell.js:14-48` | ✅ dark/light 자동 전환 |
| `statusPalette` (ok/warn/bad/info/neutral/accent) | `UXKit.jsx:7-78` (`uxColors`) | ✅ 일관 |
| `chartPalette` (series/pastel/heat) | `UXKit.jsx` | ✅ 있음 |
| `formControlStyle` | `UXKit.jsx` | ✅ select/input 표준 |
| Brand / Logo | `components/BrandLogo.jsx` (FlowWordmark, PixelGlyph #f97316) | ✅ 있음 |
| Page settings drawer | `components/PageGear.jsx` (40×40, fixed 우하단) | ✅ 일관 |
| Auth/theme/tab/notification 전역 | `app/useFlowShell.js` | ✅ 있음 |

빠진 토큰:
- **spacing scale** (gap/padding/margin) — 6/8/10/12/16/20 hardcoded 산재
- **typography scale** (fontSize 12/13/14/15/16) — token화 안 됨
- **shadow token** — 일관된 그림자 정의 없음

## Page Score (UI 통일성, 10점 만점)

`(primitive 사용 / token 사용 / API helper / header 일관성 / state 일관성)` 5축 합산.

| 페이지 | inline style | className | sf() | UXKit 사용 | 종합 |
|---|---:|---:|---:|---:|---:|
| **My_Admin** | 744 | 0 | 92 | 24 | **2.5** |
| **My_Home** | 312 | 0 | 0(fetch 13회) | 6 | **2** |
| **My_FileBrowser** | 270 | 4 | 32(fetch 7회) | 3 | **2** |
| **My_Inform** | 579 | 1 | 60 | 30 | **3** |
| **My_Tracker** | 251 | 3 | 35 | 4 | **3** |
| **My_Meeting** | 282 | 1 | 13 | 0 | **3** |
| **My_Knowledge** | 59 | 0 | 9 | 0 | **3** |
| **My_TableMap** | 351 | 12 | 49 | 9 | **3** |
| **My_SplitTable** | 475 | 15 | 40 | 0 | **3.5** |
| **My_Dashboard** | 522 | 13 | 23 | 5 | **4** |
| **My_Calendar** | 68 | 1 | 6 | 9 | **4** |
| **My_Diagnosis** | 144 | 0 | 16 | 58 | **5.5** |

UI 통일성 평균 ≈ **3.1/10**. 토큰/primitive가 잘 있는데도 페이지에 적용되지 않은 게 원인.

## 6 Pattern Audit

### 1. 페이지 헤더
- 현재: `<PageHeader title subtitle right />`이 UXKit에 있는데 거의 안 씀.
- 위반: `My_Dashboard.jsx:741` `const Header = <div style={{...}}>` 인라인 작성, `My_Calendar.jsx` 자체 스타일.
- 권고: 모든 페이지 헤더를 `<PageHeader>`로. 탭은 그 아래 `<TabStrip>`.

### 2. 탭 / 필터 바
- 현재: UXKit `<TabStrip>` 표준 있음. 일부 페이지가 button 배열 직접 렌더.
- 위반: `My_Admin.jsx`, `My_Home.jsx`.
- 권고: `<TabStrip items={[{k,l,badge}]} active onChange />`로 통일.

### 3. 모달 / 다이얼로그 (z-index 카오스)
- 현재: `components/Modal.jsx` 있음 (ESC/backdrop 처리). 많은 페이지가 `position:fixed` 직접.
- 위반:
  - `My_Calendar.jsx:485` — `position:fixed, rgba(0,0,0,0.55), zIndex:9999` 수동
  - `My_SplitTable.jsx:1803/1824/1871` — z-index 2000/3000/9998/9999 혼재
  - `My_Inform.jsx:1401/1451` — 중첩 모달 z-index 9999/10001 수동 증가
- 권고: `<Modal open onClose title>` 단일 사용. 내부에 stack-aware z-index. 모든 직접 fixed overlay 금지.

### 4. 표
- 현재: UXKit `<DataTable>` 있음 (sticky header, render fn). 대형 페이지(SplitTable/Tracker)는 직접 `<table>`.
- 위반: `My_SplitTable.jsx`(custom hover/selected/cell bg), `My_FileBrowser.jsx`(header/body 스타일 불일치).
- 권고: 중규모는 `<DataTable>`, 초대형(SplitTable matrix)은 column 정의 형식만 맞추고 가상화 고려.

### 5. 상태 알림 (toast/banner)
- 현재: `<Banner>` 있는데 미사용. `alert()` 또는 ad-hoc inline `<div>` 다수.
- 위반: `My_Calendar.jsx`, `My_Admin.jsx`의 `alert()` 동기 호출, `My_Dashboard.jsx:1589/1621` 위치/스타일 제각각.
- 권고: 토스트(`<Toast>` 신설) + `<Banner>`. `alert()` 전면 금지.

### 6. Theme / Dark mode (hardcoded color 누수)
- 현재: CSS 변수 + dark/light 자동 전환 있음. 그러나 페이지 코드에 색 hex 박힘.
- 위반:
  - `My_Home.jsx:5` `B="#ea580c"`, `M="#f97316"` 개별 정의 → dark/light 적용 안 됨
  - `My_Dashboard.jsx:15-22` Spotfire embed에 `#0e1116/#fff/#2a2a2a` hardcoded
  - `My_Calendar.jsx:335/429/477` 버튼 `color:"#fff"` hardcoded
  - `My_Diagnosis.jsx:1263-1270` `CALL_NODE_TONE` 안 hex 직접
- 권고: `var(--bg-*, --text-*, --border, --accent)` 또는 `uxColors`로 통일. 특수 차트 팔레트는 `chartPalette` 사용.

## Top 5 Quick Wins

| # | 작업 | 페이지 범위 | 효과 |
|---|---|---|---|
| 1 | `position:fixed` 모달 → `<Modal>` 강제 + z-index 자동화 | Calendar / SplitTable / Inform / Dashboard / FileBrowser | 모달 스택 깨짐/덮임 해결 |
| 2 | hardcoded `#fff`/`#000`/`rgba(0,0,0,*)`/페이지 내 `B="#xxx"` → CSS 변수 또는 `uxColors`로 일괄 치환 | Home / Dashboard / Calendar / Diagnosis | dark/light 양쪽 정상 |
| 3 | tab 직접 렌더 → `<TabStrip>` 통일 | Admin / Home | 시각 일관 + 키보드 탐색 표준화 |
| 4 | `alert()` 전면 제거 → `<Banner>` + toast | 전 페이지 | 비차단 UX |
| 5 | 페이지 헤더 inline → `<PageHeader title subtitle right />` | 10+ 페이지 | 정렬/높이/간격 일관 |

총 추정 코드 변경 7시간 정도. 페이지 분해(Inform 5,141줄 등)와 별개로 진행 가능.

## 추가 권고 (next layer)

- **spacing token** 신설: `--space-1: 4px ~ --space-6: 24px` 같은 scale을 `global.css`에 추가하고 UXKit이 노출. inline `gap:8/10/12` 점진 치환.
- **typography token**: `--fs-xs/sm/md/lg/xl` (12/13/14/16/18) + `--fw-regular/medium/bold`.
- **shadow token**: `--shadow-sm/md/lg` 정의. Panel/Modal/Drawer가 같은 그림자 쓰게.
- **icon system 결정**: 현재 emoji 위주. lucide/heroicons 도입 여부는 별도 결정 — 도입한다면 BrandLogo의 PixelGlyph는 유지해 정체성 보존.
- **fetch 헬퍼 강제**: ESLint 규칙으로 `fetch(` 직접 호출 금지(`src/lib/api.js`의 `sf()` 사용 강제). FileBrowser 7건, Home 13건 우선 정리.

## 본 보고서 사용법

- 이 점수는 코드 정황 기준이며, 실제 시각 인상(브라우저)에서 본 것이 아니다.
- 실제 코드 변경은 Codex CLI 세션이 진행. 본 세션은 평가/명세/하네스 유지가 역할.
- 작업 큐는 [`../CLAUDE.md`](../CLAUDE.md), 페이지별 책임은 [`features/README.md`](features/README.md), 코드 품질 평가는 [`REVIEW.md`](REVIEW.md).
