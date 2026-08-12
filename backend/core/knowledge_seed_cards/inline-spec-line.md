---
term: INLINE spec 표시
kind: concept
aliases: [spec line, 스펙 라인]
trigger_terms: [spec 같이, 스펙 표시]
related: [inline-matching, inline-item-naming, chart-playbook]
status: active
---
INLINE 차트에 spec 을 함께 그리는 관례.

- INLINE 은 **spec 을 차트에 같이 그려주면 좋다**. spec 은 wafer 마다 거의 같지만 **군데군데 달라지는 경우가 있다**.
- 그래서 spec 라인은 **계단식(step line)** 으로 그리거나, **wafer 별로 위아래에 짧은 빨간 직선**을 넣어 그리기도 한다 — 단일 수평선으로 단정하지 말 것.
- spec 값은 **INLINE raw DB 에 spec 열로 같이 들어 있다** — flow-i INLINE trend 는 spec_high/spec_low(USL/LSL 등 변형 포함) 컬럼을 감지하면 자동으로 빨간 계단식 점선을 함께 그린다.
- 반면 **ET spec(reformatter spec high/low)은 개발단 기준이라 자동으로 그리지 않는다** — 사용자가 spec 값을 주면 그때만 쓴다.
