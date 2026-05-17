---
doc_id: gaa_nanosheet_process_flow_and_failure_modes
kind: agent_wiki
title: GAA Nanosheet Process Flow and Failure Modes
summary: GAA nanosheet 제조에서 Si/SiGe superlattice, inner spacer, channel release, RMG 공정과 주요 실패 모드를 정리합니다.
actor: system_seed
tags: ["default_seed", "semiconductor", "GAA", "nanosheet", "SiGe", "inner_spacer", "channel_release", "RMG", "BDI", "ALE", "selective_etch"]
schema_type: default_agent_wiki_seed_v1
---

## Purpose

이 문서는 GAA nanosheet transistor의 주요 process flow와 공정 난제를 정리합니다. Flow Agent는 GAA module issue, channel release, SiGe, inner spacer, RMG 같은 질문을 해석할 때 이 문서를 기본 배경으로 사용합니다.

## 1. Si/SiGe Superlattice Epitaxy

- GAA nanosheet 공정은 silicon substrate 위에 Si와 SiGe를 번갈아 epitaxial growth하는 superlattice stack에서 시작합니다.
- Si layer는 최종 channel nanosheet가 되고, SiGe layer는 이후 제거될 sacrificial layer 역할을 합니다.
- Channel thickness(TSi)는 epitaxy로 정의되므로 lithography 한계보다 원자층 수준으로 균일하게 제어할 수 있습니다.
- Si/SiGe lattice mismatch는 intrinsic strain과 defect risk를 만듭니다.
- 여러 thermal cycle 동안 Ge diffusion과 thermal intermixing이 생기면 Si/SiGe 경계가 흐려져 selective etch margin이 무너집니다.
- GAA integration은 thermal budget을 강하게 제한합니다.

## 2. Bottom Dielectric Isolation

- BDI(Bottom Dielectric Isolation)는 최하단 nanosheet 아래쪽의 parasitic capacitance와 bottom leakage를 줄이기 위한 isolation 기술입니다.
- 일반 sacrificial layer보다 Ge 농도가 높은 bottom SiGe layer를 선택적으로 제거한 뒤 dielectric으로 채우는 접근을 사용할 수 있습니다.
- 목적은 substrate와 transistor body를 전기적으로 분리하고 DIBL/Ioff를 줄이는 것입니다.

## 3. Inner Spacer Formation

- GAA에서는 gate metal이 nanosheet 사이 공간을 채우므로 source/drain과 gate 사이의 parasitic capacitance를 막는 inner spacer가 필수입니다.
- 노출된 stack sidewall에서 SiGe sacrificial layer edge만 lateral recess로 파내 작은 cavity를 만듭니다.
- SiBCN, SiOCN, BN, silicon oxynitride 같은 dielectric을 ALD 등으로 채운 뒤 etch-back하여 spacer를 남깁니다.
- Inner spacer는 electrical isolation과 mechanical support를 동시에 담당합니다.
- Spacer thickness/profile이 불균일하면 channel release 중 etchant가 source/drain epi를 공격하거나 gate-S/D leakage가 증가할 수 있습니다.

## 4. Channel Release

- Channel release는 dummy gate 제거 후 SiGe sacrificial layer를 선택적으로 제거해 Si nanosheet를 suspended 상태로 만드는 핵심 공정입니다.
- Wet etch는 capillary force와 stiction 때문에 얇은 sheet collapse 위험이 있습니다.
- RIE는 ion bombardment damage로 channel surface와 mobility를 해칠 수 있습니다.
- Remote plasma dry etching은 radicals 중심의 chemical reaction으로 SiGe를 제거해 damage를 줄이는 방향입니다.
- 다단계 etch와 plasma-free oxidation treatment를 교대로 사용하면 Si surface 보호 산화막을 만들고 SiGe selectivity를 높일 수 있습니다.
- 핵심 control metric은 SiGe:Si selectivity, Si loss, sheet bending, stiction, residual SiGe, surface roughness입니다.

## 5. Replacement Metal Gate

- RMG(Replacement Metal Gate)는 suspended nanosheet의 4면에 interface oxide, high-k dielectric, work-function metal, main metal gate를 채우는 공정입니다.
- ALD conformality와 gap fill이 부족하면 void, pinch-off, non-uniform VT가 발생할 수 있습니다.
- Vertical spacing이 좁아지면 parasitic capacitance는 줄 수 있지만 WFM 증착 공간이 부족해집니다.
- Multi-VT 구현은 기존 FinFET식 WFM thickness modulation보다 더 어려우며, dipole layer 같은 volumeless VT control이 중요해질 수 있습니다.

## Flow Agent Interpretation Rules

- `SiGe`, `sacrificial layer`, `channel release`는 GAA channel 형성과 selective etch 문제로 연결합니다.
- `inner spacer`는 gate-S/D isolation, parasitic capacitance, S/D epi protection, mechanical support로 해석합니다.
- `BDI`는 bottom leakage, DIBL, substrate isolation, parasitic capacitance 저감 지식으로 해석합니다.
- `RMG`, `high-k`, `WFM`, `multi-VT`, `dipole`은 threshold voltage와 gate stack fill 문제로 해석합니다.
- 공정 이슈를 답할 때는 FAB step 이력, INLINE CD/OCD, ET/WAT parameter, VM/FDC sensor 근거를 함께 확인해야 합니다.
