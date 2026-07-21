---
event_id: evt_demo_proda_sort_split_review
source_type: manual
source_id: proda_sort_split_review
actor: codex_demo_seed
created_at: 2026-05-15T07:47:50+09:00
product: PRODA
root_lot_id: A1001
tags: ["PRODA", "SORT", "split", "knob", "demo"]
---

# PRODA SORT split review

SORT split과 step_id 필터 확인을 위한 graph 검증용 raw event입니다.

## Payload

```json
{
  "split_groups": [
    "A",
    "B"
  ],
  "check_columns": [
    "step_id",
    "LOT_WF"
  ]
}
```
