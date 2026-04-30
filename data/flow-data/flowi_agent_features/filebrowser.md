# filebrowser

parquet/csv 파일, schema, 컬럼, row 조회 요청을 처리한다.

## Flow
- file/source/product 조건을 추출한다.
- schema 확인, 컬럼 후보, 샘플 row 조회는 바로 수행한다.
- 파일 생성/수정/삭제/이동은 원본 변경이므로 admin 전용 확인 플로우가 필요하다.

## Required Slots
- source/file 또는 product
- optional filter, columns, row limit
