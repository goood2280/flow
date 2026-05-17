---
doc_id: default_agent_wiki_seed_framework
kind: agent_wiki
title: Default Agent Wiki Seed Framework
summary: Flow Agent 기본지식은 core seed 원본에서 시작하고, runtime flow-data Wiki에서 편집 가능한 운영 지식으로 자랍니다.
actor: system_seed
tags: ["default_seed", "agent_wiki", "flowi", "knowledge", "semiconductor", "workflow"]
schema_type: default_agent_wiki_seed_v1
---

## Purpose

이 문서는 Flow Agent 기본지식의 운영 틀입니다. core seed는 에이전트가 처음부터 가져야 할 배경지식의 원본이고, 실제 운영 지식은 flow-data의 Agent 지식 Wiki에서 수정하고 확장합니다.

## Seed Categories

- 제품, LOT, root_lot_id, lot_id, wafer, split, KNOB 기본 해석
- FAB step, module team, unit process team, main step, metrology step 해석
- FAB, INLINE, ET, QTIME, VM, EDS, OVL, ML_TABLE, rulebook, matching table, cache 파일 의미
- AMHS, FOUP, OHT, MCS, MES, dispatching, cycle time 같은 FAB 운영 배경
- Inline metrology, inspection, ET/WAT, TEG, APC, OCAP, hold, rework, scrap 같은 품질/제어 배경
- Flow-i가 실제 답변할 때 확인해야 할 DB/API/Wiki trace와 근거 규칙

## Operating Rules

- 대소문자만 다른 컬럼명과 제품명은 우선 같은 후보로 본 뒤 실제 schema와 source로 확인합니다.
- 기본지식은 해석 배경입니다. 실제 LOT, 제품, step, 측정값, chart, query 결과는 Flow DB/API/Wiki trace로 확인해야 합니다.
- 기본지식 seed는 flow-data에 같은 `doc_id`가 없을 때만 설치합니다.
- 설치된 문서는 Agent 지식 Wiki에서 직접 수정할 수 있고, 이후 setup이나 서버 기동이 덮어쓰지 않습니다.
- 새 기본지식은 이 core seed 폴더에 새 markdown 문서와 새 `doc_id`로 추가합니다.

## Expansion Slots

- `semiconductor_product_lot_knob_basics`
- `semiconductor_lot_process_organization_basics`
- `flow_data_sources_and_ml_table_basics`
- `fab_amhs_mcs_mes_basics`
- `fab_inline_et_apc_quality_control`
- `lot_genealogy_split_merge_basics`
- `fab_ocap_hold_rework_scrap_basics`
- `fab_dispatch_hotlot_cycle_time_basics`
- `fab_traceability_security_future_basics`
- `gaa_device_evolution_and_purpose`
- `semiconductor_eight_major_processes_for_gaa`
- `gaa_nanosheet_process_flow_and_failure_modes`
- `gaa_device_geometry_and_multi_vt_design`
- `gaa_beol_bspdn_power_delivery_basics`
