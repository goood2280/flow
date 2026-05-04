# filebrowser

parquet/csv 파일, schema, 컬럼, row 조회 요청을 처리한다.

## Flow
- file/source/product 조건을 추출한다.
- schema 확인, 컬럼 후보, 샘플 row 조회는 바로 수행한다.
- 파일 생성/수정/삭제/이동은 원본 변경이므로 admin 전용 확인 플로우가 필요하다.
- source_type이 명시되면 FAB/ET/INLINE/VM/EDS 중 하나로 고정한다.
- source_type 없이 "파일탐색기 열어줘"면 실행 대신 FileBrowser 진입/필요 slot을 안내한다.
- DB root 원본은 항상 read-only preview로만 다룬다.

## Required Slots
- source/file 또는 product
- optional filter, columns, row limit

## Deterministic Actions
- `preview_filebrowser_data`: source_type + product 조건으로 최근 row preview
- `search_filebrowser_schema`: keyword/source_type 조건으로 컬럼 검색
- `query_current_fab_lot_from_fab_db`: product/root_lot/wafer에서 최신 fab_lot_id 조회
