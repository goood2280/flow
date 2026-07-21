# dashboard

차트, trend, 그래프, scatter, 상관, EQP/Chamber별 요청을 처리한다.

## Flow
- product와 metric/item을 먼저 잡는다.
- `Trend`, `추세`, `시계열`이면 INLINE item을 날짜별 median line chart로 만든다.
- `EQP/Chamber별`, `장비별`, `챔버별`이면 INLINE metric을 FAB context와 join해 grouped bar chart로 만든다.
- `INLINE vs ET`, `scatter`, `상관`이면 두 source metric을 wafer/shot key로 join해 scatter/corr를 계산한다.
- metric 후보가 없으면 item 후보를 표로 보여주고 하나를 물어본다.

## Required Slots
- product: PRODA, PRODA0, PRODB 등
- metric/item: CD_GATE, SPACER_CD 등. alias는 실제 item_id로 매칭한다.
- optional lot/wafer/filter/group_by

## Examples
- `PRODA0 SPACER_CD Trend 그려줘`
- `PRODA CD_GATE EQP/Chamber별로 그려줘`
- `PRODA INLINE CD_GATE ET VTH scatter 그려줘`
