---
term: INLINE 항목 종류
kind: concept
aliases: [OCD, THK, OVL, CD]
trigger_terms: [단위, um, nm]
sources:
  - file: inline_matching.csv
    role: inline_matching
related: [inline-matching]
status: active
---
INLINE item 이름에서 측정 종류를 읽을 수 있다.

- 대표 측정 종류: **CD**(critical dimension, 선폭), **OCD**(optical CD), **THK**(두께), **OVL**(overlay) 등 — item 명에 이 약어가 들어간다. 크게 기본이 되는 것은 **CD / OCD / THK 3종**이고, **edge map 등이 따로 세팅**되어 있다.
- 단위는 대체로 **nm**. 다만 CD 등은 원본이 **um 로 적혀 있는 경우가 많고, 보통 nm 로 변환해서 본다** — 단위 확인 없이 값 크기를 단정하지 말 것.
- INLINE 은 item 별로 매칭 테이블이 조금씩 다르다 — shot 좌표 매핑(INLINE wafer map)은 추후 추가 예정.
