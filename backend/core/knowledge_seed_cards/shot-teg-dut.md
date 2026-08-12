---
term: shot
kind: concept
aliases: [샷, TEG, DUT, die]
trigger_terms: [chip, 칩]
related: [chip-radius-teg, et-db, et-representative]
status: active
---
shot / chip / TEG / DUT 계층 구조.

- **shot** = 노광 1회에 떠지는 단위. 한 shot 안에 **chip(die)이 여러 개** 들어간다.
- **TEG** 는 shot 안에 **수천 개 단위로 굉장히 많다** — TEG 안에 **DUT**(측정 단위)가 여러 개 들어가기 때문.
- ET 측정값은 shot 단위 위치로 관리된다 — 위치/반경은 Chip_Radius, TEG 좌표는 Teg_location 참조.
