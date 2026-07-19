---
term: 차트 집계 기본값
kind: concept
aliases: [agg 기본, median 기본, avg 기본, shot으로, 집계 방법]
trigger_terms: [shot으로, median으로, 평균으로, P90, P10, max로]
answered_by: dashboard
related: [chart-playbook, et-index-chart, chart-color-split]
status: active
---
소스별 wafer 집계 기본값과 선택 옵션.

- **ET (ET Index)**: 기본 **median**. "shot 으로 그려줘" = 전체 측정 point 를 집계 없이 다 표시. 그 외 옵션으로 **P90 / P10 / max** 등을 제시/선택.
- **INLINE**: 기본 **avg**. (raw INLINE 은 shot_x/shot_y 가 없고 subitem_id 가 shot 구분자.)
- **FAB**: 최신 route/progress 이력(latest).
- 사용자가 집계 방법을 명시하면 그것을 우선한다. 명시 안 하면 위 기본값을 쓰고, 답변에 "ET median 기준" 처럼 집계 방식을 명시한다.
- 통계표 컬럼(count/median/avg/min/max/std/q1/q3)도 함께 낼 수 있다.
