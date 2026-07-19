---
term: reformatter
kind: rulebook
aliases: [리포매터, reformatter.csv]
trigger_terms: [ET 변환, ET 계산]
sources:
  - file: reformatter/{vehicle}_reformatter.csv
    role: et_reformat
related: [vehicle-matching]
status: active
---
reformatter 폴더의 **{vehicle}_reformatter.csv** 는 ET DB raw data 를 엔지니어가 보는 형태로 **값을 조정·계산해 주는 테이블**이다.

- vehicle(=product, 같은 개념) 별로 파일이 1개씩 있다. 모든 vehicle 이 **같은 꼴(스키마)** 의 reformatter 를 쓰고, **내부 테이블(항목 구성/계수)은 제품마다 다를 수 있다**.
- ET 값을 화면/다운로드로 보여줄 때 raw 그대로가 아니라 이 테이블의 조정·계산을 거친 값이 엔지니어 기준값이다.
- 조정·계산의 종류: 단순 **scale factor 곱**부터, 여러 데이터를 묶어 **각 샷의 max**, **2차식 피팅**, 항목 간 **곱/나눗셈 파생** 등 다양하다.
- raw ET 를 직접 볼 일은 드물다 — 값에 이상이 있을 때 확인하는 용도.
- auto report 발행 항목에는 **spec high / spec low** 열도 있다 — 개발단 spec 이라 정확하지 않으니 참고용 (이상 판단은 trend/corr 우선, et-representative 참조).
