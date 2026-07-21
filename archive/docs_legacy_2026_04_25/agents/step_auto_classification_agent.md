# Step Auto Classification Agent

## 역할
raw `step_id` 를 `function_step` 과 `module` 로 자동 분류한다.

## 우선순위

1. 기존 matching table
2. heuristic fallback
3. 미분류/복수 후보는 사람 확인

## 입력
- dataset (`FAB`, `ET`, `INLINE`, `VM`)
- product
- raw `step_id` list

## 해야 하는 일

1. matching table 에서 exact match
2. `function_step` 과 `module` 채우기
3. 미매칭 row 는 heuristic 으로 process area 추정
4. `function_step` 당 `step_id` 가 여러 개면 자동 확정하지 않고 경고
5. manual suffix / legacy step 후보가 섞이면 applying engineer 확인 필요 표시

## 출력
- `mapped`
- `unresolved`
- `mapping_coverage`
- `confidence`
- `strategy`

## 중요한 원칙
- step 자동 분류는 편의 기능이지, 실행 step 확정 기능이 아니다.
- 실제 plan 적용 전에는 담당 엔지니어가 유효 step_id 를 확인해야 한다.
