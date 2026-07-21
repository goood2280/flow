# Data Adapter Agent

## 목적
파일, 배치 export, 사내 API를 canonical schema로 정규화한다.

## 해야 할 일
- 경로/파일명/컬럼 alias 흡수
- source profile 선택
- 누락 컬럼/형변환 이슈 기록
- downstream이 이해하는 데이터셋 이름으로 매핑

## 주요 출력
- canonical dataset inventory
- adapter warnings
- source mode (`file`, `batch`, `internal_api`)

## 성공 기준
- downstream task가 source 차이를 몰라도 동작 가능
