---
event_id: evt_demo_proda_a1001_w07_trend_note
source_type: manual
source_id: proda_a1001_w07_trend_note
actor: codex_demo_seed
created_at: 2026-05-15T07:47:50+09:00
product: PRODA
root_lot_id: A1001
wafer_id: W07
tags: ["PRODA", "DIBL", "SS", "trend", "demo"]
---

# PRODA A1001 W07 DIBL/SS trend note

W07 wafer trend 확인을 위한 graph 검증용 raw event입니다.

## Payload

```json
{
  "metrics": [
    "DIBL",
    "SS"
  ],
  "lot_wf": "A1001_W07",
  "purpose": "knowledge graph demo"
}
```
