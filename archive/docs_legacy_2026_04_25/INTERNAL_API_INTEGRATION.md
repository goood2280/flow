# Internal API Integration

## 목표
사내 API가 붙으면 단순 조회가 아니라 아래를 자동으로 하도록 만든다.

1. 들어온 테이블이 어떤 원천인지 식별
2. 컬럼 계약이 맞는지 검증
3. `step_id -> function_step -> module` 자동 분류
4. join key / time column / lot key 이상 여부 경고
5. 미분류 step / 위험 step 은 사람 확인 큐로 넘김

즉 내부 모델이 아주 강하지 않아도, 계약과 검증이 먼저 버티도록 한다.

## 권장 흐름

1. `data_adapter`
- API 응답 JSON / CSV / parquet 를 canonical dataframe 으로 변환

2. `source_contract_review`
- 테이블별 required/optional column 검사
- join key 존재 여부 확인
- time column parse 가능 여부 확인
- 누락/alias 후보 보고

3. `step_auto_classification`
- 기존 matching table 우선
- 없으면 heuristic fallback
- 그래도 불명확하면 사람 검토 필요 상태로 남김

4. `domain modules`
- tracker / informs / ET / ML / dashboard 가 공통 표준 모델 사용

## 왜 필요한가

- 사내 API 는 스펙이 자주 흔들릴 수 있다.
- 제품별로 `step_id` 가 바뀐다.
- 같은 `function_step` 에 여러 `step_id` 가 붙을 수 있다.
- 예전 step 과 manual step 이 섞이면 잘못된 plan 이 만들어질 수 있다.

그래서 `API 직접 연결`보다 `계약 검증 + 안전 경고`가 먼저다.

## 자동 분류 원칙

1. matching table 이 있으면 그것이 우선
2. matching table 이 없으면 process-area heuristic 으로 fallback
3. `function_step` 당 `step_id` 가 여러 개면 자동 확정하지 않는다
4. manual suffix 나 legacy step 후보가 섞이면 적용 엔지니어 확인을 강제한다

## 최소 계약

### FAB
- required: `root_lot_id`, `wafer_id`, `step_id`, `tkin_time`, `tkout_time`, `eqp_id`

### ET
- required: `root_lot_id`, `wafer_id`, `step_id`, `step_seq`, `tkout_time`, `item_id`, `value`

### INLINE
- required: `root_lot_id`, `wafer_id`, `step_id`, `subitem_id`, `item_id`, `value`

### VM
- required: `root_lot_id`, `wafer_id`, `step_id`, `item_id`, `value`

### EDS
- required: `root_lot_id`, `wafer_id`, `shot_id`, `chip_id`, `value`

## 사람이 확인해야 하는 경우

- 한 `function_step` 에 `step_id` 가 여러 개
- step_id 뒤에 manual suffix 후보가 있음
- 같은 제품에서 예전 step 과 현재 step 이 같이 남아 있음
- time column 이 비정상
- join key 가 빠졌음

## 코드 위치
- 계약/검증 스캐폴딩: [internal_api_contract.py](/mnt/d/TEST_Making_Video/semi_all/flow/backend/app_v2/shared/internal_api_contract.py:1)
- orchestrator schema: [schemas.py](/mnt/d/TEST_Making_Video/semi_all/flow/backend/app_v2/orchestrator/schemas.py:1)
- orchestrator registry: [registry.py](/mnt/d/TEST_Making_Video/semi_all/flow/backend/app_v2/orchestrator/registry.py:1)
- orchestrator plan: [service.py](/mnt/d/TEST_Making_Video/semi_all/flow/backend/app_v2/orchestrator/service.py:1)
