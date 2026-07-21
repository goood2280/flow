---
term: 설비/챔버 분해
kind: concept
aliases: [chamber, eqp_id, chamber_id, 챔버]
trigger_terms: [설비별, 설비 차이]
related: [commonality, chart-color-split, fab-db]
status: active
---
유의차 원인 분석에서 설비를 나눠 보는 규칙.

- 기본 분해는 **eqp_id → chamber_id 까지** — 같은 설비라도 chamber 별 산포 차이가 흔한 유의차 원인이다. 필요하면 chamber 내 unit 단위까지도 나눠 본다.
- 차트에서는 fab_step_eqp / fab_step_eqp_chamber 컬러 분리로 확인한다 (chart-color-split).
- **PM/파츠 교체 등 설비 이벤트 이력은 아직 DB 에 없다** — trend shift 의 원인 후보로 언급만 하고 데이터로 단정하지 않는다. (추후 BF/DF 등과 함께 추가 예정.)
