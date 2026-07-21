---
term: Chip_Radius.csv
kind: data-source
aliases: [Chip Radius, Teg_location.csv, TEG 위치]
sources:
  - file: Chip_Radius.csv
    role: wafer_geometry
  - file: Teg_location.csv
    role: wafer_geometry
related: [et-db]
status: active
---
wafer 기하 정보 테이블 2종.

- **Chip_Radius.csv**: wafer 의 ET 샷 센터와 wafer center 사이 거리를 **mm 단위**로 저장한 테이블. 컬럼: Mask(제품별 매칭), **chip_x_adj/chip_y_adj**(horizontal notch 기준으로 변환된 shot 격자 좌표), Chip_Radius.
- **Teg_location.csv**: 제품별 TEG 위치가 적힌 테이블.
- ET 측정값 자체가 아니라 위치/기하 참조용 — radial 분석(중심~엣지 경향) 등에 쓰인다.
