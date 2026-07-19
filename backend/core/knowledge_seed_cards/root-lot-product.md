---
term: root lot
kind: concept
aliases: [ROOT_LOT_ID, 루트랏, root lot id, FAB_LOT_ID, fab lot id, LOT_ID]
trigger_terms: [root, 루트]
sources:
  - file: ML_TABLE_{PRODUCT}.parquet
    role: split_base
related: [ml-table-knob, splittable-page]
status: active
---
lot id 3계층 체계와 root lot 의 성질.

- **root lot id**: 5자리 영문+숫자 조합, 보통 첫 자리는 A (나머지는 랜덤). 분기 전 모(母) lot.
- **fab lot id**: root lot id 뒤에 영문 또는 숫자 접미가 붙은 형태 — **root lot id 는 fab lot id 의 앞 5자리**다.
- **lot id**: 보통 "." 이 붙어서 나온다. 예) root 가 AA111 이면 AA111A.1, AA111.1 등이 fab/lot id 가 될 수 있다.
- 토큰 해석 규칙: 5자리면 root, 5자리+접미면 fab, "." 포함이면 lot id 로 보고, 앞 5자리를 잘라 root 를 얻을 수 있다.
- lot 의 wafer 는 **최대 25매**. 분기(lot 이 나뉘어 진행)로 매수가 줄 수 있고, **빠진 wafer 번호는 scrap 으로 없어진 것**일 수 있다 — 번호 연속성을 가정하지 말 것.
- root lot 은 **여러 PRODUCT 의 ML_TABLE 에 동시에 존재할 수 있다** — product 를 단정하지 말고 ML_TABLE_{PRODUCT}.parquet 들을 스캔해 확인, 2개 이상이면 사용자에게 선택지를 제시한다.
