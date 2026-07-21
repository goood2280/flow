# Agent Orchestration

## 목표
최종적으로 `사내 API + 약한 내부 모델 + deterministic backend service`를 조합해서,
공정 설계/분석/발행이 계속 돌아가는 agentic flow를 만드는 것이 목표다.

중요한 점은 모델 성능 자체보다 `계약`이다.

- 모델은 자유서술보다 JSON task/output을 만든다
- backend service는 검증과 실행을 담당한다
- 메일 발행과 시스템 POST는 같은 action payload를 공유한다

## 최종 흐름

1. `data_adapter`
- 파일, 배치 export, 사내 API를 canonical dataset으로 정규화

2. `source_contract_review`
- source 테이블의 계약과 join key를 먼저 검증

3. `step_auto_classification`
- raw `step_id` 를 `function_step/module` 로 자동 분류
- matching table 우선, heuristic fallback

4. `clean_split_review`
- lot 내부/모듈 내부에서 clean split 후보를 찾음

5. `process_ml_review`
- feature importance + heuristic review
- clean split / repeatability / incoming dominance / sign prior를 반영

6. `et_report_review`
- ET report/time, step_seq bottleneck, idle gap 정리

7. `plan_generation`
- 어떤 step_id에 어떤 knob/parameter를 어떻게 넣을지 제안

8. `publish_mail`
- 메일 본문 / xlsx / 수신자 정보 생성

9. `publish_json`
- 같은 내용을 사내 시스템 API용 JSON payload로 생성

## 설계 원칙

### 1. API 없어도 동작
- 현재는 parquet/csv/file-drop 기반으로 돌아간다
- 나중에 API를 붙여도 domain/analysis layer는 그대로 간다

### 2. 모델은 제안, 시스템은 검증
- 모델이 “이 knob이 좋아 보인다”를 제안
- backend는 sign prior, clean split, module plausibility 같은 규칙으로 검증

### 3. action payload 일원화
- 지금은 메일
- 나중에는 내부 시스템 POST
- 둘 다 같은 JSON payload를 source of truth로 쓴다

## 핵심 JSON 계약

### AgentTaskRequest
- 어떤 에이전트가 어떤 lot/product/target_y를 가지고 무슨 일을 해야 하는지

### AgentTaskResult
- summary
- findings
- artifacts
- next_tasks
- metrics

### ActionProposal
- 실행 가능한 계획
- `step_id / function_step / module / parameter / planned_value`
- confidence
- evidence

## 현재 코드 위치
- schema: [schemas.py](/mnt/d/TEST_Making_Video/semi_all/flow/backend/app_v2/orchestrator/schemas.py:1)
- registry: [registry.py](/mnt/d/TEST_Making_Video/semi_all/flow/backend/app_v2/orchestrator/registry.py:1)
- scaffold service: [service.py](/mnt/d/TEST_Making_Video/semi_all/flow/backend/app_v2/orchestrator/service.py:1)

## 에이전트 문서
- [agents/README.md](agents/README.md)
- [agents/orchestrator.md](agents/orchestrator.md)
- [agents/data_adapter_agent.md](agents/data_adapter_agent.md)
- [agents/source_contract_review_agent.md](agents/source_contract_review_agent.md)
- [agents/step_auto_classification_agent.md](agents/step_auto_classification_agent.md)
- [agents/clean_split_review_agent.md](agents/clean_split_review_agent.md)
- [agents/process_ml_review_agent.md](agents/process_ml_review_agent.md)
- [agents/et_report_review_agent.md](agents/et_report_review_agent.md)
- [agents/plan_generation_agent.md](agents/plan_generation_agent.md)
- [agents/publish_mail_agent.md](agents/publish_mail_agent.md)
- [agents/publish_json_agent.md](agents/publish_json_agent.md)

## 왜 이 구조가 맞는가
- 사내 API 승인이 늦어도 제품은 먼저 쓸 수 있다
- 모델 수준이 아주 높지 않아도 task를 작게 나누면 실무에 쓸 수 있다
- 메일 중심 운영에서 시스템 연결 중심 운영으로 자연스럽게 확장된다
