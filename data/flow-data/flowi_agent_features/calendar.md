# calendar

일정, 캘린더, 변경점 관리 요청을 처리한다.

## Flow
- 신규 일정이면 title과 date/range를 추출한다.
- 날짜가 상대 표현이면 현재 날짜 기준으로 절대 날짜를 계산한다.
- 변경점/상태가 있으면 category/status로 저장한다.
- 기존 일정 수정/삭제는 권한과 대상 확인 전 실행하지 않는다.

## Required Slots
- title
- date 또는 date range
- optional status/category/linked issue
