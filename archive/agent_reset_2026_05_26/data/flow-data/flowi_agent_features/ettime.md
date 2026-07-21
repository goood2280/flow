# ettime

ET 레포트, elapsed time, step/item/wf별 median 요청을 처리한다.

## Flow
- product, lot, step_id, item_id를 추출한다.
- step_id는 영문 2자+숫자 6자리 또는 등록된 func_step 이름만 인정한다.
- wafer별 median/mean/count 표를 반환한다.
- step이나 item이 부족하면 후보를 보여주고 물어본다.

## Required Slots
- product
- root_lot_id 또는 fab_lot_id
- step_id 또는 func_step
- item_id

## Examples
- `PRODA A1004 AA100270 CD_GATE wf별 median 보여줘`
