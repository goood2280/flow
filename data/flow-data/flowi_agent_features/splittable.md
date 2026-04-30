# splittable

SplitTable, plan/actual, KNOB, MASK, CUSTOM set, wafer별 matrix 요청을 처리한다.

## Flow
- product와 lot/wafer를 추출한다.
- KNOB/MASK/FAB/INLINE/VM parameter 조건이 있으면 해당 prefix나 컬럼을 사용한다.
- 조회/표시는 바로 수행한다.
- plan 변경/저장은 기존 기록 수정에 해당하므로 권한과 변경 전 확인이 필요하다.

## Required Slots
- product
- root_lot_id 또는 fab_lot_id
- optional wafer_id, parameter, CUSTOM set

## Examples
- `PRODA A1003 KNOB 상태 보여줘`
- `A1003 custom aaa1 컬럼으로 SplitTable 봐줘`
