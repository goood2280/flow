# Collaboration Loop

`Claude`와 `Codex`가 번갈아가며 개선 작업을 이어가는 목적의 `near-real-time` 협업 루프 규약이다.

목표는 단순 handoff 저장이 아니라 다음 루프를 안정적으로 돌리는 것이다.

1. 한쪽이 작업 요청 생성
2. 다른 쪽이 요청 claim
3. 구현/검토/테스트 수행
4. 응답 기록
5. 원 요청자가 응답 확인 후 다음 요청 생성

## 상태 전이

- `open`
  - 아직 아무도 잡지 않음
- `claimed`
  - 담당자가 처리 시작
- `responded`
  - 처리 결과가 달림
- `closed`
  - 요청자가 응답 확인 후 종료
- `archived`
  - 기록용 보관

## 파일 구조

- `flow/collab/inbox`
  - 현재 살아있는 요청
- `flow/collab/outbox`
  - 필요 시 별도 응답 보관
- `flow/collab/archive`
  - 종료된 요청

실무적으로는 `inbox/*.json` 하나만 써도 충분하다. 요청과 응답을 같은 파일에 누적한다.

## 권장 필드

```json
{
  "id": "loop_20260424_150000_001",
  "created_at": "2026-04-24T15:00:00+09:00",
  "updated_at": "2026-04-24T15:00:00+09:00",
  "from": "claude",
  "to": "codex",
  "kind": "implementation_request",
  "priority": "p1",
  "status": "open",
  "title": "Fix chip exclusion in WF Layout",
  "summary": "Chip view should exclude chips crossing the 3mm edge band.",
  "request": {
    "acceptance_criteria": [
      "chip rect must be fully inside wafer radius minus 3mm",
      "view should not render clipped chips"
    ],
    "linked_files": [
      "frontend/src/pages/My_WaferLayout.jsx"
    ]
  },
  "claim": {},
  "response": {
    "by": "",
    "summary": "",
    "verification": []
  }
}
```

## 루프 운영 방식

### Claude 쪽

- 전체 점검 결과를 `open`으로 생성
- `responded` 상태를 폴링해서 확인
- 응답 확인 후 다음 개선 요청 생성

### Codex 쪽

- `to=codex and status=open` 요청을 claim
- 수정/테스트 후 `responded`로 변경
- 검증 내용과 수정 파일 기록

## 실시간 vs near-real-time

현재 가장 현실적인 것은 `near-real-time polling`이다.

- 3초 ~ 10초 간격 폴링
- 새 요청/응답 발생 시 로그 출력
- 필요하면 Admin 알림으로도 연결 가능

완전 실시간 소켓 연결보다 이 방식이 더 안정적이다.

## 권장 명령

```bash
python3 flow/scripts/collab_loop.py submit --from claude --to codex --title "WF chip exclusion fix"
python3 flow/scripts/collab_loop.py claim loop_20260424_150000_001 --by codex
python3 flow/scripts/collab_loop.py reply loop_20260424_150000_001 --by codex --summary "3mm edge exclusion applied"
python3 flow/scripts/collab_loop.py watch --agent codex --interval 3
```

## 다음 확장

- watcher가 새 요청을 감지하면 `Admin` 알림 생성
- `tracker` 이슈 자동 생성
- `orchestrator`가 특정 kind를 자동 분기
- `Claude`와 `Codex` 각각의 작업 가이드 템플릿 자동 생성
