# Flow UI System

Flow 화면은 많은 탭과 복잡한 반도체 데이터를 다루므로, 장식보다 가독성과 반복 작업 효율을 우선한다. 브랜드 오렌지는 유지하되 기본 UI는 무채색으로 정리하고, 활성 상태와 중요한 대기/진행 상태에만 오렌지를 쓴다.

## Core Tokens

| Token | Value | Usage |
|---|---:|---|
| Primary | `#f97316` | active tab, primary action, pending/warning badge |
| Danger | `#ef4444` | failure, urgent, destructive warning only |
| Info | `#3b82f6` | neutral information, reference detail |
| Success | `#22c55e` | completed, passed, resolved |
| Border | `#e2e8f0` | table, card, input border |
| Surface | `#ffffff`, `#f8fafc` | page and panel backgrounds |
| Radius | `6px`, `8px` | controls 6px, cards/panels 8px max |
| Spacing | `8px`, `12px`, `16px` | item gap, toolbar gap, panel padding |

## Layout Rules

- GNB keeps `flow.` on the left and the user/admin area on the right.
- GNB menu groups should be dropdowns: `Data`, `Work`, `Knowledge`, `Admin`.
- Page shell uses a stable two-column layout only when the tab needs a sidebar. Sidebar width defaults to `280px`.
- Sidebar is neutral: white/slate background, single right border, no saturated filter colors.
- Page padding is `20px`; card/panel body padding is `16px`; dense toolbars may use `12px`.
- Hidden or irrelevant controls should be removed from the current tab, not left disabled in the sidebar.

## Tabs And Filters

- GNB active state may use light orange background and bold text.
- Content tabs and filters are neutral by default: white or slate surface with slate border.
- Active content tab uses orange background and white text.
- Active filter uses orange border/light orange background/orange text.
- Inputs are neutral until focus. Focus ring is light orange and border is orange.

## Data Rows

Lists such as Inform Log and Issue Tracker should use a three-layer row:

1. Top: compact metadata, product, timestamp, author, status badge.
2. Middle: primary title or issue text in the strongest weight.
3. Bottom: lot, wafer, tags, owners, linked items as small neutral badges. Long tag groups are clipped or folded.

## Status Semantics

| Tone | Color | Meaning |
|---|---|---|
| Danger | Red | severe issue, failure, urgent hold |
| Warning | Orange | pending, checking, in progress |
| Info | Blue | supporting detail, non-blocking notice |
| Success | Green | completed, passed, resolved |
| Neutral | Slate | metadata, category, inactive tags |

Do not use red for `확인중` or simple pending states.

## Tables And Cards

- Tables share one border style, compact header text, and row hover background.
- Avoid nested cards. Use page sections as full-width bands or simple panels.
- Operational cards use `8px` radius or less and visible but quiet borders.
- Data-heavy tables should prefer wrapping, chunking, or split-table blocks over horizontal scroll where possible.

## Reference Implementation

The React/Tailwind reference screen lives in:

`frontend/src/components/FlowInformStandardScreen.jsx`

It intentionally is not wired into production navigation because the current frontend does not install Tailwind CSS. Use it as the standard screen blueprint when Tailwind is introduced or when porting the same layout rules into the existing CSS-variable based `UXKit.jsx`.

The component demonstrates:

- grouped GNB dropdowns,
- neutral sidebar and filters,
- orange-only active states,
- three-layer Inform Log rows,
- semantic status badges,
- shared card and table style,
- DB read-only / Files writable context display.
