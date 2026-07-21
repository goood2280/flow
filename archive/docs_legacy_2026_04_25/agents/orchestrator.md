# Orchestrator

## 역할
- 어떤 task를 어떤 순서로 실행할지 정한다
- 실패 시 fallback 경로를 택한다
- 메일과 시스템 POST가 같은 action payload를 보도록 유지한다

## 입력
- product
- root_lot_id / fab_lot_id
- target_y
- optional trigger (`manual`, `scheduled`, `event`)

## 출력
- `OrchestrationRun`
- ordered tasks
- optional action proposals

## 핵심 규칙
- `data_adapter`가 항상 먼저
- `plan_generation`은 `clean_split_review`와 `process_ml_review`가 끝난 뒤
- `publish_mail`과 `publish_json`은 같은 payload를 source로 사용
