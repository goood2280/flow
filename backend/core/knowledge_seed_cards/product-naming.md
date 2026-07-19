---
term: 제품 네이밍 체계
kind: concept
aliases: [EVT0, EVT1, 제품 코드]
sources:
  - file: ML_TABLE_{PRODUCT}.parquet
    role: split_base
related: [root-lot-product, vehicle-matching]
status: active
---
제품 이름의 일반 구조 (실명/상세 코드는 사내 local 카드에서 관리).

- 제품명은 다양하지만 보통 "제품코드 + EVT 단계" 형태가 많다. 예) PRODA EVT0, PRODA EVT1.
- **EVT0 = 개발단 제품, EVT1 = 양산단** 으로 본다 — 같은 제품코드라도 EVT 단계가 다르면 다른 PRODUCT 로 취급될 수 있다.
- EVT 단계는 제품명 **접미 숫자**로 들어가는 경우가 많다: 예) PROD_A0 = EVT0, PROD_A1 = EVT1.
- 제품이 아예 달라져도(차세대) **앞자리 영문/숫자로 세대를 읽을 수 있다** — 세대 문자는 T > U > V 순으로 진행한다.
- 실제 제품 코드 체계/별칭 목록은 보안상 시드에 넣지 않는다 — 사내에서 "지식 채움 수행" 또는 local 카드로 채운다.
