# tablemap

테이블 맵, relation, join path, 컬럼 관계 요청을 처리한다.

## Flow
- source table/column과 target table/column을 추출한다.
- relation은 선 안의 노드로 보고, 노드를 선택하면 매칭 컬럼 테이블을 보여준다.
- DB나 table 색상 설정은 UI 설정 변경이며 권한과 저장 확인이 필요하다.

## Required Slots
- source table/column
- optional target table/column
