# waferlayout

TEG, shot, die, wafer layout, edge shot 요청을 처리한다.

## Flow
- product를 추출한다.
- TEG/shot/die/map 조건을 확인한다.
- layout 좌표, edge shot 후보, 유사 wafer map을 조회한다.
- product가 없으면 product를 물어본다.

## Required Slots
- product
- optional TEG id, shot/die coordinate, wafer context
