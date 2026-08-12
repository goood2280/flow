---
term: 공정 기본 용어
kind: glossary
aliases: [etch, depo, litho, photo, CMP, implant, anneal, CVD, ALD, 에치, 식각, 증착, 리소, 포토, 세정]
trigger_terms: [무슨 공정, 공정 용어, step_desc 해석]
related: [fab-terms, step-matching, vehicle-matching]
status: active
---
step_desc/step 명칭 해석에 쓰는 일반 반도체 공정 용어 (일반 지식 — 사내 고유 은어는 fab-terms 카드에).

- **litho / photo (노광)**: PR(감광액) coat → expose(노광) → develop(현상)으로 패턴을 그리는 공정. step_desc 의 LITHO/PHOTO/PH 류. rework 은 보통 이 단계에서 발생(rework-ec-step 카드 참조).
- **etch (식각)**: 패턴대로 막을 깎는 공정. dry(플라즈마)/wet(용액) 구분. ETCH/DRY/WET 류.
- **depo (증착)**: 막을 쌓는 공정 — CVD(화학기상)/PVD(물리기상, 주로 metal)/ALD(원자층, 얇고 균일). DEP/DEPO/CVD/PVD/ALD 류.
- **EPI (에피)**: 결정성 실리콘을 성장시키는 증착 — S/D Epi 등. EPI 류.
- **oxidation (산화)**: 열산화로 SiO2 막 형성. OX/OXID 류.
- **implant (이온주입)**: dopant 이온을 주입 — well/VT 형성. IMP/IMPL/IIP 류.
- **anneal (열처리)**: dopant 활성화·결함 회복 — RTA/RTP(급속 열처리) 포함. ANN/ANNEAL/RTA/RTP 류.
- **CMP (연마)**: 화학적·기계적 평탄화. CMP/POL 류.
- **CLN / ASH / strip (세정·제거)**: 세정 / PR 애싱 제거 / 막 제거. CLN/CLEAN/ASH/STRIP 류.
- **diffusion / furnace (확산·로)**: furnace 기반 열공정 계열. DIFF/FUR 류.
- **계측(metrology) step**: CD(선폭), OVL(overlay 정렬), THK(막두께) 등 측정 전용 step — 막을 바꾸지 않는 meas step 으로, 공정 구간 경계 판정에서 제외하고 본다.
