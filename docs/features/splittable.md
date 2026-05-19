# SplitTable

SplitTable은 `product + lot + wafer` 기준으로 plan, actual, diff, notes, rulebook mapping을 맞추는 작업대다.

## Owns

- root lot/fab lot/wafer 축 matrix
- KNOB/MASK/CUSTOM set plan과 actual 비교
- final value, drift, diff, notes, related issue
- runtime-only `TAG_*` 꼬리표 열: 원본 파일을 수정하지 않고 SplitTable matrix와 CUSTOM 세트에 표시하는 사용자 overlay
- split 영향 후보 이벤트: notes와 plan history 변경을 append-only KnowledgeEvent로 남긴다.
- XLSX export와 Inform Log용 SplitTable snapshot 생성
- `data/Fab/cache/lot_progress_latest_lot_by_root_wafer.parquet`의 root/wafer별 최신 `lot_id`를 사용한 fab lot label 표시

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
- Inform용 fab lot snapshot은 선택된 fab lot의 header/wafer scope를 유지하고, root plan overlay는 해당 scope의 wafer cell에만 적용한다.
- fab lot 연결은 SplitTable 전용 match cache를 만들지 않고 LOT 진행 최신 캐시를 우선 사용한다. 캐시가 없거나 scope가 맞지 않으면 기존 FAB source raw scan으로 fallback한다.
- ML_TABLE lot view는 `root_lot_id`가 있을 때 `backend/core/ml_table_lookup.py`의 root-lot lookup cache를 먼저 사용한다. cache hit 시 원본 `ML_TABLE_*.parquet` 전체 scan 대신 해당 `root_lot_id=<id>` partition에서 필요한 KNOB/MASK/CUSTOM 컬럼을 읽고, cache miss 시 기존 small/local fallback 경로를 유지한다.
- History 탭은 plan history의 전체/최종 log만 표시한다. Lot Operational History 패널과 `/operational-history` 호출은 UI에서 사용하지 않는다.
- cache/parquet 변경은 runtime 산출물과 코드 변경을 분리해서 설명한다.
- `TAG_*` 꼬리표 값은 `data/flow-data/splittable/custom_tags.json`에만 저장하고, 원본 `ML_TABLE_*.parquet` / CSV / FAB source에는 쓰지 않는다.
- Shared 설정(source config, rulebook/schema, prefixes, precision, paste sets, custom sets, match cache refresh)은 `splittable` page manager 이상만 쓴다.
- Plan/note 작성자는 request body의 `username`이 아니라 세션 사용자로 기록한다. 내부 테스트/Flow-i 직접 호출만 fallback 값을 허용한다.
- 같은 plan cell에서 값이 바뀌는 경우 KnowledgeEvent payload에 `conflicting_evidence=true`를 남겨 Home Flow-i가 “영향 평가가 갈림”으로 답할 수 있게 한다.

## Verify

```bash
git diff --check
python -m pytest tests/test_splittable_lot_candidates.py
cd frontend && npm run build
```
