# splittable

SplitTable, plan/actual, KNOB, MASK, CUSTOM set, wafer별 matrix 요청을 처리한다.

## Flow
- product와 lot/wafer를 추출한다.
- KNOB/MASK/FAB/INLINE/VM parameter 조건이 있으면 해당 prefix나 컬럼을 사용한다.
- 조회/표시는 바로 수행한다.
- plan 변경/저장은 기존 기록 수정에 해당하므로 권한과 변경 전 확인이 필요하다.
- "보여줘/조회/상태"는 read-only 조회다.
- "plan/등록/저장/바꿔"는 저장 전 confirmation payload를 먼저 만든다.
- product가 없으면 product 후보를 묻고 임의 추정하지 않는다.

## Required Slots
- product
- root_lot_id 또는 fab_lot_id
- optional wafer_id, parameter, CUSTOM set

## Deterministic Actions
- `query_lot_knobs_from_ml_table`: lot별 KNOB/MASK 구성 조회
- `query_wafer_split_at_step`: wafer + step 조건 split 조회
- `preview_splittable_plan_update`: plan 변경 확인 초안
- `find_lots_by_knob_value`: 특정 KNOB value를 받은 lot/wafer 역검색

## Examples
- `PRODA A1003 KNOB 상태 보여줘`
- `A1003 custom aaa1 컬럼으로 SplitTable 봐줘`
