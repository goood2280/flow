# flow UX Standard — v8.8.33

**기준 페이지:** `My_FileBrowser.jsx` + `My_SplitTable.jsx`
**도구:** `frontend/src/components/UXKit.jsx`

다른 My_* 페이지가 FileBrowser/SplitTable 와 같은 톤·색·스페이싱·상호작용을 갖도록 이 문서와 UXKit 를 따른다. 신규 기능은 **먼저 UXKit 만으로 prototype** → 불가한 경우에만 custom styling.

## 원칙

1. **pill/tab/header 는 절대 ad-hoc 스타일로 쓰지 않는다.**  `UXKit.Pill`, `UXKit.TabStrip`, `UXKit.PageHeader` 만 사용.
2. **색은 `var(--*)` CSS 변수 또는 `statusPalette.{ok,warn,bad,info,neutral,accent}` 에서 선택.**  hard-coded hex 는 `#22c55e/#f97316/#ef4444/#3b82f6` 4색 고정 팔레트 외 금지.
3. **상호작용 cursor: pointer 는 실제 클릭 가능한 요소에만.**  그 외는 cursor default.
4. **빈 상태는 반드시 `UXKit.EmptyState`**.  테이블은 `UXKit.DataTable` 의 `empty` prop.
5. **2MB 경고 배너 같이 자주 나오는 패턴은 `UXKit.Banner` 로.**  tone 은 info/warn/bad 중 선택.
6. **좌우 분할 페이지는 `UXKit.TwoCol`.**  leftWidth 는 기본 260, 사이드바가 매우 빡빡하면 220, 여유가 있으면 300.

## 페이지별 적용 가이드

| 페이지 | 권장 적용 |
|---|---|
| My_Home | `PageHeader` + `TwoCol(left=폴더, right=list)` + `DataTable` + `EmptyState` |
| My_Tracker | `TabStrip(이슈/간트/로그)` + `DataTable` + `Pill` (카테고리/상태) |
| My_Meeting | `TabStrip` + `PageHeader` + `Banner` (참석자 미응답 경고) |
| My_Inform | `TwoCol` + `Pill` (module/reason) + `EmptyState` (등록된 제품 없음) |
| My_Message | `DataTable` + `StatusDot` (읽음/안읽음) |
| My_Admin | `TabStrip` + `DataTable` + `Button` (primary/ghost/danger) |
| My_Calendar | `PageHeader` + category `Pill` |
| My_Dashboard | `TabStrip` + `PageHeader` |
| My_TableMap | `PageHeader` |
| My_Monitor | `StatusDot` + `DataTable` |

## 톤·색 매핑

| 의미 | tone | color var |
|---|---|---|
| 성공 / 정상 / 읽음 | `ok` | `#22c55e` |
| 주의 / 변경 / 미응답 | `warn` | `#f97316` |
| 실패 / 삭제 / 에러 | `bad` | `#ef4444` |
| 정보 / 안내 / plan | `info` | `#3b82f6` |
| 일반 / 보조 | `neutral` | `--text-secondary` |
| 브랜드 강조 | `accent` | `--accent` |

## 레이아웃 치수

| 요소 | 권장값 |
|---|---|
| Header height (compact) | 34 px |
| TwoCol left width | 260 px (default) |
| Pill height | 18~20 px |
| Tab strip height | 34 px |
| Table row height | 28~32 px |
| Section padding | 8~16 px |

## 타이포

- 본문: 12 px / line-height 1.5
- 보조 라벨: 10~11 px / `--text-secondary`
- 제목 (Header): 13 px / 700
- monospace (lot/wafer/column name): `font-family: monospace, "Consolas"` 10 px

## 추가 예시

### 상태 변경 전후 표기
```jsx
<Pill tone="warn">이전 → 이후</Pill>
```

### Drift 경고 셀
```jsx
<Pill tone="bad">⚠ drift</Pill>
```

### 관리자 전용 액션 버튼
```jsx
<Button variant="primary">승인</Button>
<Button variant="danger">삭제</Button>
<Button variant="ghost">취소</Button>
```

## 이월

아래 페이지는 2026-04-23 기준 UXKit 미적용 — v8.9 에서 순차 마이그레이션 예정.  변경 잦은 페이지부터:
1. My_Inform (부분적용 — sidebar normalize 는 v8.8.33)
2. My_Tracker
3. My_Meeting
4. My_Calendar
5. My_Dashboard
6. My_Admin

---

*담당: dev-lead (구현) + ux-reviewer (적용 검증).  UX 일관성 요청 접수 시 본 문서 + UXKit 를 기준으로 회귀 점검.*
