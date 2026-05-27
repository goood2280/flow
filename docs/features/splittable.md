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
- `TAG_*` 꼬리표 열 생성은 조회 결과 표 맨 아래의 `+ TAG` 행에서 수행한다. CUSTOM 패널은 custom set 구성만 담당하며 관리 행 생성/선택 UI를 노출하지 않는다.
- `TAG_*` 꼬리표 행은 맨 아래에 고정하지 않고 이름 앞 숫자의 natural sort 위치에 표시한다.
- `TAG_*` 꼬리표 열 삭제는 admin 또는 `splittable` page manager만 가능하다. 삭제 시 product별 TAG 정의와 `custom_tags.json`에 저장된 해당 열의 모든 값을 함께 제거한다.
- 기존 `MGMT_*` 관리 행 저장값은 `data/flow-data/splittable/management_rows.json`에 남아도 원본 파일을 수정하지 않는다. 신규 UI에서는 CUSTOM 선택 풀과 저장 set에서 `MGMT_*`를 제외한다.
- KNOB 적용공정정보는 `ppid_knob.csv`를 제품 공용 룰로 읽고, product별 `step_desc -> step_id` 확장은 `Vehicle_matching.csv`를 우선 사용한다. 기존 배포처럼 `Vehicle_matching.csv`가 없으면 `step_matching.csv`를 fallback으로 사용한다.
- `ppid_knob.csv`는 `feature_name`, `rule_order`, `step_desc`, `operator`, `value`, `category` 컬럼을 기본 계약으로 하며, `feature_name`은 KNOB 이름이고 같은 KNOB에 등록된 `step_desc` 종류가 적용 공정 후보로 표시된다.
- `Vehicle_matching.csv`는 `product`, `step_id`, `step_desc` 컬럼을 기본 계약으로 하며, 현재 선택 product alias에 맞는 row만 대소문자 구분 없이 `step_desc`별 step 후보로 노출한다. 같은 KNOB의 중복 `step_desc` rule은 한 그룹으로 합쳐 보여준다.
- 적용공정정보 표시와 하단 적용 요약은 SplitTable 톱니바퀴 기본 설정에서도 켜고 끌 수 있고, 룰북 행/컬럼 매핑은 톱니바퀴 고급 설정의 `ppid_knob.csv` / `Vehicle_matching.csv` 섹션에서 관리한다.
- Shared 설정(source config, rulebook/schema, prefixes, precision, paste sets, custom sets, match cache refresh)은 `splittable` page manager 이상만 쓴다.
- Plan/note 작성자는 request body의 `username`이 아니라 세션 사용자로 기록한다. 내부 테스트/Flow-i 직접 호출만 fallback 값을 허용한다.
- 같은 plan cell에서 값이 바뀌는 경우 KnowledgeEvent payload에 `conflicting_evidence=true`를 남겨 Home Flow-i가 “영향 평가가 갈림”으로 답할 수 있게 한다.
- plan이 actual DB 값과 달라지는 경우 plan 작성자에게 `my_plan_actual_mismatch` 알림을 1회 발행한다. 저장 시 기존 actual과 이미 다르면 즉시 발행하고, 이후 DB 갱신으로 `/view`에서 새 mismatch가 관측돼도 같은 cell/plan/actual 조합은 재발행하지 않는다.

## Verify

```bash
git diff --check
python -m pytest tests/test_splittable_lot_candidates.py
cd frontend && npm run build
```
