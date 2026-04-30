# meeting

회의 생성, 회의록, 아젠다, 반복 회의 요청을 처리한다.

## Flow
- 신규 회의 생성이면 제목과 일정 조건을 추출한다.
- `매주 수요일 오후2시` 같은 반복 규칙은 recurrence로 저장한다.
- 참여자가 없으면 기본적으로 빈 참여자 또는 요청자 기준으로 생성하고, 필요 시 추가 참여자를 물어본다.
- 기존 회의 수정/삭제는 권한과 대상 확인 전 실행하지 않는다.

## Required Slots
- title/topic
- date/time 또는 recurrence rule
- optional participants, agenda

## Examples
- `네모의 꿈이라고 매주 수요일 오후2시에 진행하는 회의하나 만들어주세요`
