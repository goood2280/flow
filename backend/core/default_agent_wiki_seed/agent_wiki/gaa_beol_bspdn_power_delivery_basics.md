---
doc_id: gaa_beol_bspdn_power_delivery_basics
kind: agent_wiki
title: GAA BEOL, New Metals, and Backside Power Delivery
summary: 2nm/3nm GAA logic에서 BEOL 저항 병목, Mo/Co/Ru 신금속, BSPDN/PowerVia/BPR 전력망 개념을 정리합니다.
actor: system_seed
tags: ["default_seed", "semiconductor", "GAA", "BEOL", "MOL", "Mo", "Co", "Ru", "BSPDN", "PowerVia", "BPR", "IR_drop", "Vmin"]
schema_type: default_agent_wiki_seed_v1
---

## Purpose

이 문서는 advanced GAA logic node에서 front-end transistor만큼 중요한 BEOL, contact/via resistance, backside power delivery 개념을 정리합니다. Flow Agent는 BEOL, Mo, Co, Ru, BSPDN, PowerVia, IR drop 같은 질문을 해석할 때 이 문서를 배경으로 사용합니다.

## Contact and BEOL Bottleneck

- 2nm/3nm logic에서는 transistor scaling만으로 성능을 확보하기 어렵고 MOL/BEOL resistance가 큰 병목이 됩니다.
- Contact, Metal 0, local via의 resistance 증가는 switching speed와 static power loss에 영향을 줍니다.
- 기존 tungsten contact는 narrow trench fill에서 seam/void risk가 있고 barrier/fluorine 관련 resistance 문제가 생길 수 있습니다.
- Copper BEOL은 전도성이 좋지만 pitch가 줄수록 electromigration, barrier thickness, line resistance 문제가 커집니다.

## New Metal Candidates

- Mo, Co, Ru 같은 금속은 advanced node contact/via/local interconnect 후보입니다.
- Mo는 bottom-up fill과 barrier-less 또는 low-barrier integration 후보로 언급되며 narrow feature resistance를 낮추는 방향으로 봅니다.
- Co/Ru는 electromigration, gap fill, line resistance 관점에서 Cu/W 대체 또는 보완 후보로 이해합니다.
- 실제 적용 여부는 foundry node, layer, integration scheme에 따라 달라지므로 특정 제품에 단정하지 않습니다.

## Backside Power Delivery

- BSPDN(Backside Power Delivery Network)은 power delivery를 wafer backside 쪽으로 옮겨 front-side routing congestion을 줄이는 architecture입니다.
- Intel은 PowerVia라는 이름을 사용하고, buried power rail(BPR)과 결합될 수 있습니다.
- 목적은 front-side signal routing 공간 확보, IR drop 감소, Vmin 안정화, cell utilization 개선입니다.
- AI/HPC workload에서는 안정적인 power delivery와 낮은 IR drop이 performance와 yield에 직접 영향을 줄 수 있습니다.

## Integration Risks

- BSPDN은 wafer thinning, backside alignment, backside via/contact formation, front-back overlay가 매우 어렵습니다.
- Power rail과 signal routing 분리는 장점이지만, thermal, mechanical stress, backside process damage를 함께 관리해야 합니다.
- BEOL/BSPDN 문제는 ET, reliability, voltage droop, timing margin, yield issue로 나타날 수 있습니다.

## Flow Agent Interpretation Rules

- `BEOL`, `MOL`, `contact`, `via`, `M0`는 resistance, fill defect, EM, local interconnect 병목으로 해석합니다.
- `Mo`, `Co`, `Ru`는 advanced interconnect/material 후보로 해석하되 실제 적용은 node/source 확인이 필요합니다.
- `BSPDN`, `PowerVia`, `BPR`, `backside power`는 IR drop, routing congestion, Vmin, cell utilization, backside alignment issue와 연결합니다.
- 전력망 관련 질문은 device 성능뿐 아니라 layout, BEOL, packaging, reliability 관점까지 함께 봅니다.
