# Claude-Codex Collaboration

`flow`에서 `Claude Code`와 `Codex`를 같이 쓰려면, 직접 모델끼리 붙이기보다 같은 워크스페이스 안에서 `handoff protocol`을 공유하는 방식이 가장 안전합니다.

## 목적

- `Claude`
  - 전체 구조 점검
  - UX / 운영 / 보안 / 사용성 리뷰
  - 스모크 테스트 / 사용자 시나리오 점검
  - 개선 후보 정리
- `Codex`
  - 단위 기능 구현
  - 리팩터링
  - 테스트 보강
  - 문서/설정 반영

즉 역할을 나누면 됩니다.

- `Claude = reviewer / explorer / QA`
- `Codex = implementer / integrator / fixer`

## 직접 연결 vs 파일 프로토콜

직접 연결 방식은 장점이 있지만, 보통 다음 문제가 있습니다.

- 도구/세션 권한이 다름
- 한쪽 결과를 다른 쪽이 안정적으로 재현하기 어려움
- 컨텍스트가 모델 내부에만 남고 저장이 안 됨
- 플러그인/확장 의존성이 커짐

그래서 현재 단계에서는 `공유 JSON handoff`가 맞습니다.

## 권장 구조

공유 디렉터리:

- `flow/collab/inbox`
- `flow/collab/outbox`
- `flow/collab/archive`

권장 흐름:

1. `Claude`가 점검 결과를 `inbox/*.json`으로 남긴다.
2. `Codex`가 `open` 상태 요청을 읽고 구현/수정한다.
3. 작업 결과를 같은 파일에 `status`, `resolution`, `linked_files`, `verification`으로 반영하거나 `outbox/*.json`으로 응답한다.
4. 끝난 항목은 `archive/`로 이동한다.

## 메시지 종류

권장 `kind`:

- `review_finding`
- `smoke_failure`
- `ux_issue`
- `data_contract_issue`
- `implementation_request`
- `followup_question`

권장 `priority`:

- `p0`
- `p1`
- `p2`
- `p3`

권장 `status`:

- `open`
- `claimed`
- `in_progress`
- `blocked`
- `done`
- `archived`

## 권장 JSON 포맷

```json
{
  "id": "handoff_20260424_001",
  "created_at": "2026-04-24T15:30:00+09:00",
  "from": "claude",
  "to": "codex",
  "kind": "ux_issue",
  "priority": "p1",
  "status": "open",
  "title": "Dashboard trend cards look stretched and non-presentable",
  "summary": "Trend chart area looks visually noisy compared to knob mix and distribution cards.",
  "context": {
    "page": "dashboard",
    "route": "/dashboard",
    "product": "PRODUCT_A0"
  },
  "acceptance_criteria": [
    "Trend chart should look presentation-ready",
    "Legends and stat area should be compact",
    "No inner card clipping"
  ],
  "linked_files": [
    "frontend/src/pages/My_Dashboard.jsx",
    "docs/ux_standard.md"
  ],
  "verification": [],
  "resolution": ""
}
```

## 운영 규칙

- 한 항목은 한 책임자가 잡는다.
- 파일 경로와 기대 결과를 반드시 적는다.
- “좋아 보이게” 대신 측정 가능한 acceptance criteria를 적는다.
- 구현 후 `verification`에 실행한 검증을 남긴다.
- 모델 내부 추론이 아니라 파일에 남은 사실을 기준으로 이어간다.

## Superpower / 플러그인

현재 가장 현실적인 옵션은 다음 순서입니다.

1. `shared JSON/TXT protocol`
2. 필요하면 `local watcher`
3. 그 다음 `internal API / orchestrator`

즉 지금은 `Superpower` 같은 플러그인보다, 이 프로토콜이 더 안정적입니다.

나중에 확장할 수 있는 방향:

- `flow/backend/app_v2/orchestrator/`와 연동
- handoff 생성 시 자동 notify
- 특정 kind는 tracker/inform 자동 생성
- `valve`나 내부 API와도 같은 action schema 사용

## 권장 작업 분담

### Claude에게 잘 맞는 일

- 전체 UX 리뷰
- 화면 일관성 검토
- 스모크 테스트 시나리오 제안
- 설명/가독성/문서 리뷰
- “사용자 입장에서 이상한 점” 찾기

### Codex에게 잘 맞는 일

- 코드 수정
- 타입/런타임 안정화
- 리팩터링
- 스크립트/테스트 추가
- 스키마/백엔드/프론트 실제 반영

## 다음 확장

이 handoff를 다음과 연결할 수 있습니다.

- `tracker`
- `inform`
- `meeting`
- `mail publish`
- `json action payload`
- `agent orchestration`

즉 지금은 파일 프로토콜로 시작하고, 나중에는 내부 orchestrator로 승격하면 됩니다.
