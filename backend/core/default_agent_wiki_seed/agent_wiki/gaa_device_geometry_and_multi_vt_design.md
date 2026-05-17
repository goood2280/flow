---
doc_id: gaa_device_geometry_and_multi_vt_design
kind: agent_wiki
title: GAA Geometry and Multi-VT Design Knobs
summary: GAA nanosheet의 Wsheet, TSi, vertical spacing, dipole layer, mobility tradeoff 같은 설계 노브를 정리합니다.
actor: system_seed
tags: ["default_seed", "semiconductor", "GAA", "nanosheet", "Wsheet", "TSi", "multi_VT", "dipole", "mobility", "phonon_scattering", "PPA"]
schema_type: default_agent_wiki_seed_v1
---

## Purpose

이 문서는 GAA nanosheet device geometry와 multi-VT 설계 노브를 설명합니다. Flow Agent는 Wsheet, TSi, VT, mobility, low power, high performance 같은 질문을 해석할 때 이 문서를 기본 배경으로 사용합니다.

## Key Geometry Knobs

- `Wsheet`는 nanosheet channel width입니다.
- `TSi`는 silicon nanosheet thickness입니다.
- `Tsus` 또는 vertical spacing은 suspended nanosheet 사이 간격입니다.
- Nanosheet stack count, sheet width, sheet thickness, gate spacing은 drive current, capacitance, leakage, manufacturability를 동시에 바꿉니다.

## Wsheet Tradeoff

- Wsheet가 넓으면 effective channel width가 증가해 drive current를 높일 수 있습니다.
- AI/HPC/server용 high-performance cell은 더 넓은 sheet를 선호할 수 있습니다.
- Mobile/wearable low-power cell은 parasitic capacitance와 power를 줄이기 위해 더 좁은 sheet를 쓸 수 있습니다.
- 같은 cell footprint 안에서 sheet width를 조절할 수 있는 것이 nanosheet GAA의 중요한 장점입니다.

## TSi and Mobility

- TSi를 너무 얇게 만들면 electrostatic control은 좋아질 수 있지만 quantum confinement와 scattering이 커질 수 있습니다.
- 특히 hole mobility는 phonon scattering, surface roughness, crystallographic plane 영향에 민감합니다.
- Narrow sheet effect는 sheet가 너무 좁거나 얇을 때 mobility와 performance가 기대보다 낮아지는 현상으로 이해합니다.
- Sheet width를 넓히면 고성능 crystallographic plane 기여가 커져 일부 mobility 손실을 보완할 수 있습니다.

## Multi-VT Engineering

- Multi-VT는 같은 chip 안에서 low-VT/high-speed device와 high-VT/low-leakage device를 함께 구현하는 설계입니다.
- FinFET에서는 WFM thickness modulation을 쓰는 경우가 많았지만, GAA nanosheet에서는 sheet 사이 공간이 좁아 WFM volume control이 어렵습니다.
- Dipole layer는 부피를 거의 쓰지 않고 VT를 조절하는 volumeless VT tuning 후보입니다.
- 5 angstrom 미만의 thin dipole layer 같은 접근은 GAA 전용 VT tuning에서 중요한 개념으로 봅니다.

## Flow Agent Interpretation Rules

- `Wsheet 증가`는 drive current 증가와 capacitance 증가 가능성을 함께 봅니다.
- `TSi 감소`는 electrostatic control 개선과 mobility degradation 가능성을 함께 봅니다.
- `vertical spacing 감소`는 AC speed/capacitance 이점과 RMG/WFM fill difficulty를 함께 봅니다.
- `multi-VT`, `dipole`, `WFM`은 device target, leakage, speed, gate stack process와 연결합니다.
- 설계 노브 설명은 배경지식이고, 실제 제품 조건은 PDK, measurement, ET/WAT, simulation 근거로 확인해야 합니다.
