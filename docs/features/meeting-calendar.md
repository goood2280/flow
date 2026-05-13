# Meeting And Calendar

Meeting은 회의, agenda, minutes, decision, action item을 남기고 Calendar는 날짜와 진행 상태를 한 곳에서 보여준다.

## Owns

- 회의 차수, agenda, minutes, decision, action item
- tracker issue import
- meeting mail compose/send
- meeting mail preview and selectable content sections
- calendar 하단 meeting ask assistant (`/api/meetings/ask`) — visible meeting의 agenda/minutes/decision/action item read-only 질의
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
- tracker issue import는 현재 Tracker issue와 lot progress cache 기준으로 `issue_ref.lots`를 다시 hydrate한다.
- Monitor issue lot은 Meeting agenda에서도 입력된 `lot_id` 1행과 compact Qty/wafer/step 요약을 유지한다.
- imported tracker issue의 lot table은 agenda card와 신규 agenda draft preview에서 바로 보여야 한다.
- meeting mail preview는 실제 send와 같은 HTML builder를 사용하고, agenda/minutes/decisions/action items 포함 여부는 모두 기본 on이다.
- meeting ask assistant는 `_meeting_visible` 권한을 재사용하고 회의 데이터를 쓰지 않는다. LLM 미설정/실패 시 저장 데이터 기반 fallback 답변을 반환한다.
- Flow-i unit action은 `meeting.ask.llm`로 노출하며 Home에서는 회의/차수/아젠다/회의록/결정사항/액션아이템 요약과 sources를 인라인 표시한다.
- calendar 항목은 자체 입력인지 외부 push인지 출처를 유지한다.
- 원본 entity와 동기화되는 상태 변경은 충돌 가능성을 고려한다.

## Verify

```bash
git diff --check
cd frontend && npm run build
```
