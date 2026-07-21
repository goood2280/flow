# Flow Process Analytics Roadmap

## Goal
하나의 앱에서 아래를 끊김 없이 다루는 것이 목표다.

- 운영: lot 진행, issue, inform, meeting, plan vs actual, watcher
- 분석: FAB, VM, INLINE, ET, EDS를 공정 의미 기준으로 연결
- 의사결정: 어떤 KNOB 조합이 성능/수율 `y`를 가장 좋게 만드는지 설명

## Grain Model
분석 시스템의 핵심은 데이터 grain을 명확히 나누는 것이다.

- `lot`: 운영 추적, split/rework/hold 관리 단위
- `wafer`: ML_TABLE, tracker lot attachment, wafer-level KPI 단위
- `shot`: INLINE, ET 맵/공간패턴, shot-level 공정 이상 단위
- `chip`: EDS 수율/불량 단위

권장 고유 키는 아래와 같다.

- wafer entity key: `root_lot_id + wafer_id`
- shot key: `root_lot_id + wafer_id + shot_id`
- chip key: `root_lot_id + wafer_id + shot_id + chip_id`

## Bridge Tables
원천 DB를 직접 엮는 대신 bridge를 둔다.

1. `step_registry`
- source별 `step_id -> function_step`
- `main_step`, `meas_step`, `is_manual`, `valid_from`, `valid_to`

2. `inline_et_map_registry`
- `product`, `map_id`, `inline_subitem_id`, `et_shot_x`, `et_shot_y`
- map family 4종 이상 대응

3. `shot_chip_layout_registry`
- `product`, `shot_id`, `chip_id`, `chip_x`, `chip_y`
- shot당 chip 6~40개 대응

4. `wafer_geometry_registry`
- `product`, `wf_center_x`, `wf_center_y`
- `ref_shot_x`, `ref_shot_y`, `ref_shot_center_x`, `ref_shot_center_y`
- `shot_pitch_x`, `shot_pitch_y`, `shot_size_x`, `shot_size_y`
- wafer absolute coordinate 기준 고정

5. `teg_layout_registry`
- `product`, `step_id`, `step_seq`, `item_id`, `teg_id`
- `teg_ll_x`, `teg_ll_y`, `coord_mode`
- 현재는 TEG lower-left를 representative point로 사용

6. `knob_rulebook`
- `KNOB <- function_step(step_id들) <- FAB ppid`
- 다중 function_step 조합 허용

7. `chip_spatial_contract`
- `shot index`, `chip layout`, `teg_no`, `teg size`, `edge exclusion`
- ET shot ↔ EDS chip ↔ TEG number 연결의 기준 계약

## Product Surfaces
앞으로 앱에서 보여줄 핵심 화면은 아래다.

1. `SplitTable`
- wafer-level parameter 비교
- KNOB/INLINE/VM에 실제 step_id 병기
- tracker/inform 운영 이력 연결

2. `ET Reporting`
- ET step_seq / request 분리
- same step_id 내 재의뢰, 재측정 구분
- wafer/shot map, top fail signature, drift summary

3. `Inline_ET Analysis`
- ET target 기준 top Inline feature
- KNOB 그룹별 target mean
- KNOB별 earliest FAB step_id / function_step

4. `Lineage Analysis`
- FAB, VM(wafer) -> INLINE, ET(shot) -> EDS(chip)
- lot/wafer/shot/chip drill-down

5. `Spatial Analysis`
- wafer center / ref shot center / TEG representative point 기반 radius 분석
- ET TEG radius ↔ nearby chip yield
- 초기에는 shot agg, 이후 chip proximity로 확장

6. `Chip-Level EDS Bridge`
- shot-level ET와 chip-level EDS를 직접 잇기 위한 bridge
- wafer 평균, shot agg, chip local 3층 비교

7. `Optimization`
- `y = f(KNOB, MASK, FAB, VM, INLINE, ET, EDS)`
- 중요도, 상호작용, Pareto, recommended split

8. `Data Freshness Watch`
- FileBrowser 에서 S3 sync 방향뿐 아니라 target 최신 항목 시각까지 노출
- 최신 local item 이 6시간 이상 갱신되지 않으면 stale alert
- sync 성공이어도 DB query/export 단 failure 가능성을 별도로 감시

## Build Order
가장 안전한 구현 순서는 아래다.

1. 운영 히스토리와 분석 화면 연결
2. step/function/knob rulebook 고도화
3. Inline_ET + KNOB 요약 분석
4. ET reporting system
5. wafer geometry + TEG representative point
6. shot-chip bridge + EDS 연결
7. chip spatial contract + TEG number join
8. 최적화/설명형 ML
9. source freshness anomaly watch 고도화

## Guardrails
- 코드와 운영 데이터는 분리 유지
- 실데이터 차이는 adapter profile로 흡수
- 운영 키는 `step_id`, 사용자 의미 키는 `function_step`
- ML feature는 원천이 아니라 engineered feature로 취급
- 화면 노출은 권한과 그룹 가시성으로 제어
- S3 sync 성공과 데이터 최신성은 별개로 취급
