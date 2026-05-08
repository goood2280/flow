# Meeting And Calendar

Meeting은 회의, agenda, minutes, decision, action item을 남기고 Calendar는 날짜와 진행 상태를 한 곳에서 보여준다.

## Owns

- 회의 차수, agenda, minutes, decision, action item
- tracker issue import
- meeting mail compose/send
- calendar 월 grid, pending/in_progress/done 상태
- meeting action과 tracker/calendar 상태 동기화

## Does Not Own

- tracker issue의 주 저장소 역할
- inform thread의 주 저장소 역할
- 실시간 공동 편집 엔진

## Code Entrypoints

| Layer | Path |
|---|---|
| Meeting page | `frontend/src/pages/My_Meeting.jsx` |
| Calendar page | `frontend/src/pages/My_Calendar.jsx` |
| Meeting router | `backend/routers/meetings.py` |
| Calendar router | `backend/routers/calendar.py` |
| Meeting module | `backend/app_v2/modules/meetings/` |
| Data | `data/flow-data/meetings/`, `data/flow-data/calendar/` |

## Guardrails

- meeting write와 calendar push는 분리된 service 경계로 다룬다.
- 이미지가 큰 mail은 텍스트 요약과 링크 중심으로 보낸다.
- calendar 항목은 자체 입력인지 외부 push인지 출처를 유지한다.
- 원본 entity와 동기화되는 상태 변경은 충돌 가능성을 고려한다.

## Verify

```bash
git diff --check
cd frontend && npm run build
```
