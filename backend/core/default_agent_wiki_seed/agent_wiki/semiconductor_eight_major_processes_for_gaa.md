---
doc_id: semiconductor_eight_major_processes_for_gaa
kind: agent_wiki
title: Semiconductor 8 Major Processes for GAA Nodes
summary: GAA와 2nm/3nm logic node 관점에서 반도체 8대 공정의 역할과 핵심 통제 포인트를 정리합니다.
actor: system_seed
tags: ["default_seed", "semiconductor", "8_major_processes", "wafer", "oxidation", "photo", "etch", "implant", "deposition", "metallization", "packaging", "GAA"]
schema_type: default_agent_wiki_seed_v1
---

## Purpose

이 문서는 2nm/3nm GAA logic 공정에서 8대 제조 공정이 어떤 의미를 갖는지 설명합니다. Flow Agent는 공정/계측/결함 문맥을 해석할 때 이 내용을 기본 배경으로 사용합니다.

## 1. Wafer Manufacturing

- Wafer는 integrated circuit의 substrate이자 도화지입니다.
- Silicon purification, ingot growth, slicing, polishing, CMP를 거쳐 표면 결함과 평탄도를 관리합니다.
- Advanced node에서는 초기 wafer defect, crystal defect, surface flatness가 이후 모든 공정 수율에 영향을 줍니다.

## 2. Oxidation

- Silicon surface에 SiO2 산화막을 형성합니다.
- Oxide는 protection layer, isolation layer, ion implantation mask 역할을 합니다.
- Thin and uniform oxide, interface state control이 device leakage와 reliability에 중요합니다.

## 3. Photolithography

- Photoresist(PR), mask, exposure, develop로 pattern을 wafer에 전사합니다.
- 5nm 이하에서는 EUV lithography가 핵심이고, metal layer 일부에서도 EUV가 필요할 수 있습니다.
- EPE(Edge Placement Error), overlay margin, mask alignment는 advanced logic 수율의 핵심 위험입니다.
- High-NA EUV는 더 작은 pitch와 tighter overlay control을 위해 도입되는 차세대 lithography 방향입니다.

## 4. Etching

- Photo pattern을 mask로 하여 oxide, metal, silicon, dielectric 등을 제거합니다.
- Wet etch는 chemical 중심이고, dry etch는 plasma 기반입니다.
- Advanced node에서는 anisotropic profile control, high selectivity, ALE(Atomic Layer Etching), isotropic selective etch가 중요합니다.
- GAA에서는 nanosheet channel release와 inner spacer recess가 etch 기술의 핵심 난제입니다.

## 5. Ion Implantation

- P, As, B 같은 dopant를 silicon 내부에 주입해 N/P type 영역과 source/drain 특성을 만듭니다.
- Shallow junction, low-energy implantation, activation anneal, lattice damage recovery가 중요합니다.
- GAA와 advanced node에서는 thermal budget이 제한되므로 activation과 diffusion control의 tradeoff가 큽니다.

## 6. Deposition

- CVD, PVD, ALD 등을 통해 thin film을 형성합니다.
- GAA에서는 high-k dielectric, work-function metal, inner spacer dielectric, conformal fill이 중요합니다.
- ALD는 complex 3D structure의 sidewall/bottom까지 균일하게 코팅해야 하므로 step coverage가 핵심입니다.

## 7. Metallization

- Transistor들을 electrical circuit으로 연결하는 MOL/BEOL 배선 공정입니다.
- 기존 Al은 Cu/W로 대체되었고, advanced node에서는 W contact와 Cu damascene도 resistance 한계에 부딪힙니다.
- Mo, Co, Ru 같은 new conductor가 contact/via 저항과 fill defect를 줄이기 위한 후보입니다.

## 8. Packaging

- Wafer를 die 단위로 dicing한 뒤 substrate/package와 연결하고 보호합니다.
- Advanced packaging은 단순 보호가 아니라 2.5D interposer, 3D TSV, chiplet, HBM integration을 통해 system performance를 좌우합니다.
- GAA logic, HBM, advanced packaging은 AI/HPC system에서 함께 고려해야 합니다.

## Flow Agent Interpretation Rules

- `photo`, `litho`, `overlay`, `EPE`, `EUV`는 pattern placement와 mask alignment 리스크로 해석합니다.
- `etch`, `ALE`, `selectivity`, `profile`, `recess`는 구조 형상과 damage/remaining material 리스크로 해석합니다.
- `deposition`, `ALD`, `step coverage`, `high-k`, `WFM`은 3D 구조 균일 코팅과 gap fill 리스크로 해석합니다.
- `metallization`, `MOL`, `BEOL`, `contact`, `via`는 resistance, EM, fill defect, power/signal routing 리스크로 해석합니다.
