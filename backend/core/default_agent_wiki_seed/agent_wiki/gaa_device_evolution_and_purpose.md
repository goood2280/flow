---
doc_id: gaa_device_evolution_and_purpose
kind: agent_wiki
title: GAA Device Evolution and Purpose
summary: 2nm/3nm logic node에서 FinFET 한계를 넘어 GAA nanosheet 구조가 필요한 이유와 기본 장치 물리를 정리합니다.
actor: system_seed
tags: ["default_seed", "semiconductor", "GAA", "FinFET", "nanosheet", "DIBL", "SCE", "PPA", "2nm", "3nm"]
schema_type: default_agent_wiki_seed_v1
---

## Purpose

이 문서는 차세대 2nm/3nm logic 반도체에서 Gate-All-Around(GAA) 구조가 왜 필요한지 설명하는 기본 배경지식입니다. Flow Agent는 GAA, FinFET, DIBL, leakage, nanosheet 같은 용어를 해석할 때 이 문서를 배경으로 사용하되, 실제 제품/LOT/측정 판단은 Flow DB와 Wiki trace로 확인해야 합니다.

## Background

- Planar transistor는 미세화가 진행되면서 gate가 channel을 충분히 제어하지 못했고, 이를 보완하기 위해 3면 gate 구조인 FinFET이 도입되었습니다.
- FinFET은 5nm 세대까지 scaling 수명을 늘렸지만, 3nm/2nm 영역에서는 bottom leakage와 short-channel effect 제어가 어려워집니다.
- Gate length가 줄어들면 source/drain depletion region이 channel과 겹치고, gate의 electrostatic control이 약해집니다.
- Short-channel effect는 off current(Ioff)를 증가시키고 DIBL(Drain-Induced Barrier Lowering)을 악화시켜 static power를 키웁니다.
- AI/HPC workload는 높은 performance, 낮은 power, 작은 area를 동시에 요구하므로 PPA 관점에서 더 강한 gate control이 필요합니다.

## GAA Core Idea

- GAA는 channel의 네 면을 gate가 둘러싸는 구조입니다.
- FinFET이 channel의 좌/우/상 3면을 감싸는 것과 달리, GAA는 360도 electrostatic control을 목표로 합니다.
- 초기 GAA 후보인 nanowire는 short-channel control은 강하지만 channel area가 작아 drive current가 부족할 수 있습니다.
- 업계 주류는 얇고 넓은 channel sheet를 수평으로 여러 겹 쌓은 nanosheet GAA입니다.
- Nanosheet는 channel width(Wsheet)를 설계 목적에 따라 조절할 수 있어 저전력 cell과 고성능 cell을 같은 architecture 안에서 구성하기 좋습니다.

## Naming

- Samsung은 nanosheet 기반 GAA를 MBCFET(Multi-Bridge Channel FET)으로 부릅니다.
- Intel은 ribbon 형태 channel을 강조해 RibbonFET이라는 이름을 사용합니다.
- 이름은 달라도 핵심은 수직 적층된 수평 channel과 gate-all-around electrostatic control입니다.

## Flow Agent Interpretation Rules

- `GAA`, `MBCFET`, `RibbonFET`, `nanosheet`는 서로 관련된 advanced logic transistor 용어로 해석합니다.
- `DIBL`, `Ioff`, `SCE`, `leakage`, `electrostatic control`은 gate/channel 제어력과 전력 문제의 지표로 봅니다.
- `PPA`는 performance, power, area tradeoff입니다.
- GAA 관련 이상 해석은 device physics 배경만으로 단정하지 않고, ET/WAT/INLINE/VM/FAB 이력과 함께 확인해야 합니다.
