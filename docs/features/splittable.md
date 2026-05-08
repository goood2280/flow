# SplitTable

SplitTable은 `product + lot + wafer` 기준으로 plan, actual, diff, notes, rulebook mapping을 맞추는 작업대다.

## Owns

- root lot/fab lot/wafer 축 matrix
- KNOB/MASK/CUSTOM set plan과 actual 비교
- final value, drift, diff, notes, related issue
- XLSX export와 Inform Log용 SplitTable snapshot 생성
- `data/flow-data/splittable/match_cache/`의 match cache 사용

## Does Not Own

- 원천 파일 탐색 자체
- 담당자 공지/메일 thread 관리
- chart 중심 분석 화면
- 근거 없는 자동 plan 저장

## Code Entrypoints

| Layer | Path |
|---|---|
| Frontend page | `frontend/src/pages/My_SplitTable.jsx` |
| Frontend pieces | `frontend/src/pages/SplitTable/` |
| Backend router | `backend/routers/splittable.py` |
| Sets cache | `backend/core/splittable_sets_cache.py` |
| Inform embed bridge | `backend/app_v2/modules/informs/splittable_embed.py` |
| Flow-i guide | `data/flow-data/flowi_agent_features/splittable.md` |

## Guardrails

- 저장 전 product, lot, wafer 범위를 확정한다.
- plan 변경은 preview와 확인 단계를 거친다.
- Inform snapshot과 SplitTable의 root/fab/wafer 표시 규칙을 맞춘다.
- cache/parquet 변경은 runtime 산출물과 코드 변경을 분리해서 설명한다.

## Verify

```bash
git diff --check
python -m pytest tests/test_splittable_lot_candidates.py
```
