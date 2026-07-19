---
term: wafer map
kind: concept
aliases: [웨이퍼 맵, 웨이퍼맵, spec out map, flat_zone, chip_x_pos, chip_x_adj]
trigger_terms: [map 그려, 맵 그려, spec out]
sources:
  - file: Chip_Radius.csv
    role: chip_radius
  - file: Teg_location.csv
    role: teg_location
related: [chip-radius-teg, wafer-zone, shot-teg-dut]
status: active
---
wafer map 요청 규약과 좌표 규칙. 제품별 shot 위치 정보가 있어 shot 단위 컬러링이 가능하다.

- **값 컬러링 map**: "○○ 컬러링해서 map 그려줘" → 해당 항목 값으로 shot 에 색을 입혀 그린다.
- **spec out map**: "spec ~~ 기준 spec out map 그려줘" → **spec out 포인트만 빨간색, 나머지는 회색**.
- ET raw 좌표 컬럼은 **chip_x_pos, chip_y_pos, flat_zone 뿐**이다. **flat_zone = notch 의 반시계 회전각 (0=horizontal, 90=vertical(right))** — map 은 항상 **horizontal notch 기준으로 회전 보정**해서 그린다 (TEG vertical 항목 왜곡 방지).
- 변환된 격자 좌표(**chip_x_adj/chip_y_adj**)는 ET raw 가 아니라 **Chip_Radius.csv 에 제품(Mask)별로** 매칭되어 있다.
- ET spec(reformatter spec high/low)은 개발단 기준이라 **자동 참조하지 않는다** — spec out map 은 사용자가 준 spec 값만 쓴다.
- shot 격자→실좌표(mm) 변환 기하는 core/teg_map.py(Chip_Radius fit)와 같다.
