---
term: ET 대표값 규칙
kind: concept
aliases: [wafer 대표값, ET 대표값]
trigger_terms: [대표값, site]
answered_by: dashboard
sources:
  - file: ET DB
    role: et_db
  - file: reformatter/{vehicle}_reformatter.csv
    role: et_reformat
related: [et-db, reformatter, chart-agg-defaults]
status: active
---
ET 의 wafer 대표값은 저장된 값이 아니라 **집계로 만드는 값**이다.

- ET raw DB 는 **shot 단위**다 — wafer 대표값 컬럼은 따로 없다. wafer 대표값은 항상 샷 값에서 집계해 만든다.
- reformatter 산출값(샷 max, 2차 피팅 등)도 모두 **샷 단위**다 — 그 자체가 wafer 대표값인 항목은 없다.
- 집계 규칙: **기본 median**. 항목에 따라 **P90/P10, max/min** 도 본다 (chart-agg-defaults 의 옵션 제시와 일치).
- spec: reformatter 안에 auto report 발행 항목은 **spec high / spec low** 열이 있으나 **개발단 spec 이라 정확하지 않다**. 이상 여부는 spec 보다 **trend 의 튄값(flier/excursion)이나 corr 차트의 이탈**을 더 유의해서 본다.
