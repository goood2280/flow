---
term: 상관 해석
kind: concept
aliases: [R2, 결정계수]
trigger_terms: [corr, 상관 있, 상관 없]
answered_by: dashboard
related: [et-index-chart, chart-color-split, chart-playbook]
status: active
---
corr 차트를 그린 뒤 보는 판단 관례.

- 상관 세기는 **R² 를 가장 많이 본다** — corr 답변에 R² 를 함께 제시한다.
- 이상 패턴으로 유의해서 보는 것: ① **밑둥이 뜨는 경우**(일부 포인트가 본 추세에서 분리되어 떠 있음), ② **split 으로 컬러링했을 때 특정 cloud 만 거동이 다르게 보이는 경우**.
- 그래서 corr 이상이 보이면 split/knob 컬러 분리를 제안하는 것이 정석이다 (chart-color-split).
