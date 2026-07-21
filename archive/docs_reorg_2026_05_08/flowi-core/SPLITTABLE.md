# SplitTable

스플릿 테이블은 `product + lot + wafer` 기준으로 plan, actual, diff, notes를 한 화면에서 맞추는 작업대다.

## 유지 범위

- root lot/fab lot/wafer 축 matrix
- KNOB/MASK/CUSTOM set plan과 actual 비교
- final value, drift, diff 확인
- lot note, related issue, XLSX export
- Inform Log에 들어갈 SplitTable snapshot 생성
- Flowi에서 "이 lot의 KNOB/plan/actual 보여줘" 요청 처리

## 제외 범위

- 원천 파일 탐색 자체
- 담당자 공지/메일 thread 관리
- chart 중심 분석 화면
- 근거 없는 자동 plan 저장

파일 탐색은 FileBrowser로, 전달/공지 이력은 Inform Log로 넘긴다.

## Code Entrypoints

| Layer | Path |
|---|---|
| Frontend page | `frontend/src/pages/My_SplitTable.jsx` |
| Frontend extracted pieces | `frontend/src/pages/SplitTable/` |
| Backend router | `backend/routers/splittable.py` |
| Sets cache | `backend/core/splittable_sets_cache.py` |
| Flowi feature guide | `data/flow-data/flowi_agent_features/splittable.md` |
| Inform embed bridge | `backend/app_v2/modules/informs/splittable_embed.py` |

## Flowi Slots

| Slot | Required | Note |
|---|---:|---|
| `product` | yes | 제품 설정/ML_TABLE 선택 |
| `root_lot_id` or `fab_lot_id` | yes | 5자 mixed token은 root lot 우선 |
| `wafer_id` | optional | `#6`, `WF6`, `6번장` 등 |
| `parameter` | optional | KNOB/MASK/FAB/INLINE/VM column |
| `split_set` | optional | CUSTOM set 선택 |
| `mode` | optional | plan/actual/diff/final |

## Guardrails

- 저장 전에는 product, lot, wafer 범위를 확정한다.
- plan 변경은 preview와 확인 단계를 거친다.
- Inform snapshot과 root/fab/wafer 표시 규칙이 다르면 SplitTable 기준으로 맞춘다.
- cache/parquet 변경은 runtime 산출물일 수 있으므로 TODO나 PR summary에서 코드 변경과 분리해 설명한다.
