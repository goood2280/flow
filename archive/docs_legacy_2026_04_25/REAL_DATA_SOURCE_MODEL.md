# Real Data Source Model

이 문서는 `flow`가 앞으로 기준으로 삼을 실제 반도체 공정 데이터 모델을 정리한다.
목표는 두 가지다.

- 테스트용 합성 데이터가 아니라 사내 실데이터 구조에 맞는 기준을 세운다.
- 환경마다 컬럼명/경로가 조금 달라도, 내부 로직은 같은 canonical model 위에서 동작하게 한다.

## 1. 공통 원칙

원천 DB는 기본적으로 `column-oriented long table`이라고 본다.
핵심 식별 축은 아래다.

- `product`
- `root_lot_id`
- `lot_id`
- `wafer_id`
- `step_id`
- `tkin_time`
- `tkout_time`
- `item_id`
- `value`

실제로는 source 마다 추가 컬럼이 붙는다.
그래도 앱 내부에서는 위 공통축을 기준으로 source-specific 필드를 확장하는 방식으로 가져간다.

## 2. Source 별 canonical schema

### 2.1 FAB

FAB는 장비 이력/recipe/chamber/condition 추적용 원천이다.

기본 컬럼:

- `product`
- `root_lot_id`
- `lot_id`
- `wafer_id`
- `step_id`
- `tkin_time`
- `tkout_time`
- `eqp`
- `item_id`
- `value`

실무에서 자주 붙는 컬럼:

- `ppid`
- `chamber`
- `slot_no`
- `recipe`
- `oper_id`
- `track_in_user`
- `track_out_user`

중요 포인트:

- FAB는 `item_id/value` 구조를 유지하되, `eqp`, `ppid`, `chamber`는 별도 컬럼이 있어도 허용한다.
- 동일 의미가 중복 저장될 수 있다. 예: `ppid` 컬럼이 있으면서 `item_id=PPID` row도 존재.
- 앱에서는 우선 별도 컬럼을 쓰고, 없으면 `item_id/value`에서 복구하는 방식이 안전하다.

### 2.2 INLINE

INLINE은 shot 단위 계측이다.

기본 컬럼:

- `product`
- `root_lot_id`
- `lot_id`
- `wafer_id`
- `step_id`
- `tkin_time`
- `tkout_time`
- `item_id`
- `subitem_id`
- `value`

중요 포인트:

- `subitem_id`는 사실상 shot 번호 역할이다.
- 제품/step/item마다 shot map의 meaning이 다를 수 있다.
- INLINE map은 1종이 아니라 여러 패턴이 있고, 현재 기준으로 약 4종 정도의 map family를 갖는다고 본다.

### 2.3 ET

ET는 die/shot 좌표 기반 electrical test 원천이다.

기본 컬럼:

- `product`
- `root_lot_id`
- `lot_id`
- `wafer_id`
- `step_id`
- `step_seq`
- `tkin_time`
- `tkout_time`
- `item_id`
- `shot_x`
- `shot_y`
- `value`

추가로 자주 필요한 컬럼:

- `request_id`
- `request_no`
- `job_id`
- `measure_group_id`

중요 포인트:

- 같은 `step_id` 안에서도 여러 `step_seq`가 존재할 수 있다.
- 같은 `step_seq`라도 별도 의뢰서로 다시 측정되면 다른 measurement package로 취급해야 한다.
- 따라서 ET time 분석 기준 key는 단순 `step_id + step_seq`가 아니라, 가능하면 아래 계층으로 잡는다.

권장 ET package key:

- 1순위: `request_id`
- 2순위: `measure_group_id`
- 3순위: `(step_id, step_seq, tkout_time)`

## 3. Source 간 연결 규칙

### 3.1 공통 wafer key

source 간 기본 연결키는 아래다.

- `product`
- `root_lot_id`
- `lot_id`
- `wafer_id`

운영상 `lot_id` 형식이 환경마다 다를 수 있으므로, join 기준은 상황별로 나눈다.

- lot 계층 비교: `product + root_lot_id`
- wafer 계층 비교: `product + root_lot_id + wafer_id`
- exact run 계층 비교: `product + lot_id + wafer_id`

### 3.2 INLINE ↔ ET 연결

INLINE은 `subitem_id`, ET는 `(shot_x, shot_y)`이므로 바로 join 하면 안 된다.
반드시 `matching_table`을 사용한다.

matching table 역할:

- 특정 제품/step/item의 INLINE shot 번호와 ET 좌표를 연결
- map family 별 좌표 변환을 정의
- 제품별/공정별 다른 map 규칙을 흡수

권장 matching table key:

- `product`
- `step_id`
- `map_id`
- `inline_subitem_id`
- `et_shot_x`
- `et_shot_y`

추가 권장 컬럼:

- `item_id` 또는 `item_group`
- `valid_from`
- `valid_to`
- `priority`

### 3.3 map family

INLINE map은 item마다 완전히 같다고 가정하면 안 된다.

최소 개념은 아래처럼 둔다.

- `map_id`
- `map_name`
- `map_type`
- `description`

예시:

- `INLINE_MAP_A`
- `INLINE_MAP_B`
- `INLINE_MAP_C`
- `INLINE_MAP_D`

앱은 item별로 어떤 `map_id`를 써야 하는지 lookup할 수 있어야 한다.

## 4. ML_TABLE 과 derived feature 의미

`ML_TABLE_<PRODUCT>`는 원천 DB가 아니라, 여러 원천을 wafer-level feature로 정리한 파생 테이블이다.
즉 SplitTable 이 보는 `KNOB / MASK / FAB / INLINE / VM / QTIME` 컬럼은 모두 같은 종류의 데이터가 아니라,
각기 다른 규칙으로 만들어진 feature layer다.

### 4.1 ML_TABLE 기본 정체성

`ML_TABLE_<PRODUCT>`는 보통 아래 key를 가진다.

- `product`
- `root_lot_id`
- `lot_id` 또는 `fab_lot_id`
- `wafer_id`

그리고 그 오른쪽에 feature column 이 붙는다.

예:

- `KNOB_GATE_01`
- `MASK_M1_STACK`
- `FAB_GATE_EQP`
- `INLINE_CD_GATE_MEAN`
- `VM_VTH_PRED`
- `QTIME_GATE_TO_SPA`

중요한 점은, 이 컬럼들을 같은 방식으로 해석하면 안 된다는 것이다.

### 4.2 KNOB

KNOB은 FAB 원천의 `ppid`를 엔지니어가 보기 좋은 feature 값으로 변환한 derived feature다.

핵심 성격:

- 원천은 FAB
- 원천 의미는 보통 `ppid`
- 사용자 노출은 공정 의미 중심의 feature
- 변환 로직은 rulebook 기반

예:

- 원천: `step_id=GATE`, `ppid=PPID_GATE_007`
- 노출: `KNOB_GATE_RECIPE = FAST`

### 4.3 KNOB은 반드시 step 1:1 이 아니다

실무에서는 보통 `한 step의 ppid -> 한 knob`처럼 보이지만, 이걸 하드코딩하면 안 된다.

가능한 경우:

- 한 step의 ppid 하나로 결정
- 같은 step의 여러 item 조합으로 결정
- 여러 step의 ppid 조합으로 결정
- 특정 step의 존재 여부 + ppid 조합으로 결정

즉 KNOB rule은 단순 lookup도 있지만, 실제로는 `feature derivation rule`에 가깝다.

권장 rulebook 구조:

- `feature_name`
- `rule_type`
- `priority`
- `source_steps`
- `source_items`
- `operator`
- `expected_values`
- `output_value`
- `effective_from`
- `effective_to`

rule type 예시:

- `single_ppid_exact`
- `multi_step_combo`
- `exists_and_value`
- `ordered_first_match`

### 4.4 SplitTable 에서의 해석 원칙

SplitTable 은 `KNOB_*`를 직접 컬럼명 규칙만으로 해석하지 말고, 가능한 한 rulebook registry를 통해 해석해야 한다.

권장 우선순위:

1. 명시적 rulebook 매핑
2. source model의 derived feature registry
3. fallback heuristic

이렇게 해야 `한 KNOB이 여러 step 조합으로 결정되는 경우`를 흡수할 수 있다.

## 5. step_id 와 function_step

제품별로 실제 `step_id`는 다를 수 있다.
그래서 앱 내부 분석 축은 가능하면 raw `step_id`가 아니라 `function_step`을 같이 가져가야 한다.

### 5.1 왜 필요한가

같은 기능을 하는 step이라도 제품마다 이름이 다를 수 있다.

예:

- 제품 A: `GATE01`
- 제품 B: `GT_A1`
- 제품 C: `POLY_GATE`

이 셋은 raw 이름은 다르지만, 실제로는 같은 `function_step=GATE`일 수 있다.

### 5.2 기본 원칙

- 원천 데이터에는 `step_id`를 그대로 보존한다.
- 분석/비교/룰북 해석에는 `function_step`을 별도 축으로 둔다.
- `step_id -> function_step` 매핑은 source별, 제품별 테이블에서 관리한다.

### 5.3 1:N 허용

보통은 `한 제품에서 step_id -> function_step`이 거의 1:1이다.
하지만 예외가 있다.

- manual step
- rework step
- abnormal split step
- 운영자가 수동 개입하려고 별도 step을 딴 경우

이 경우 `한 product + function_step`에 여러 `step_id`가 연결될 수 있다.
즉 cardinality는 아래처럼 봐야 한다.

- 일반 케이스: `product + step_id -> function_step` 1:1
- 예외 케이스: `product + function_step -> step_id` 1:N

### 5.4 step_id 변경 이력

실무에서는 같은 기능인데 `step_id` 자체가 바뀌는 경우가 있다.
그래서 step mapping은 고정 lookup으로 끝내면 안 된다.

권장 컬럼:

- `product`
- `step_id`
- `function_step`
- `step_type`
- `is_manual`
- `is_active`
- `valid_from`
- `valid_to`
- `priority`
- `note`

source별 운영 예:

- FAB: `fab step mapping`
- INLINE: `inline step mapping`
- VM: `vm step mapping`

즉 `matching_step.csv` 하나로 모든 source를 덮어쓰기보다,
최소 논리적으로는 source별 registry를 나누는 편이 안전하다.

권장 해석 원칙:

1. `product + step_id` exact match
2. active + validity window 우선
3. manual step 은 별도 flag 유지
4. 하나의 function_step에 여러 step_id가 잡히는 것은 허용
5. ambiguity가 있으면 후보를 모두 보여주고 priority 로 정렬

### 5.5 main step 과 meas step

step은 모두 같은 성격이 아니다.
실무에서는 크게 아래 두 종류로 나눠서 봐야 한다.

- `main step`: 실제 공정이 진행되는 step
- `meas step`: ET, INLINE 등 계측/평가가 진행되는 step

예시:

- main step: STI, GATE, MOL, BEOL
- meas step: INLINE_CD, ET_VT, OVL_CHECK

중요 포인트:

- 하나의 `function_step`은 `main`일 수도 있고 `meas`일 수도 있다.
- ET/INLINE은 보통 `meas step`에 속한다.
- VM은 실제 측정 장비 결과가 아니라 `main step` 설비/공정 값으로부터 계산되거나 직접 읽어온 값을 측정 feature처럼 쓰는 경우다.

즉 VM은 source 성격상 아래처럼 본다.

- source origin: `main step` FAB/tool signal
- user-facing semantics: `measurement-like derived feature`

그래서 VM은 meas source처럼 보이지만, lineage 상으로는 main step 쪽에서 왔다고 표시해야 한다.

권장 step registry 추가 컬럼:

- `step_class`: `main` | `meas`
- `measure_domain`: `inline` | `et` | `vm` | `none`
- `main_function_step`: meas step 이 어느 main step을 보는지 연결하는 부모 축

예:

- `GATE_01 -> function_step=GATE, step_class=main`
- `GATE_CD_01 -> function_step=GATE_CD, step_class=meas, measure_domain=inline, main_function_step=GATE`
- `GATE_ET_01 -> function_step=GATE_ET, step_class=meas, measure_domain=et, main_function_step=GATE`

### 5.6 앱에서의 사용 방식

- `KNOB` rulebook: raw `step_id` 대신 가능하면 `function_step` 기준으로 rule 작성
- `FAB` 요약: raw `step_id`와 `function_step`을 함께 노출
- `INLINE` 요약: inline 전용 step mapping을 통해 `function_step` 정규화
- `VM` feature: vm 전용 step mapping을 통해 main function에 연결
- `Tracker`: 현재 위치 표시 시 raw step 과 function step 둘 다 보관
- `Dashboard`: 제품 간 비교는 `function_step` 기준, 상세 drill-down은 `step_id` 기준
- `Inform/Meeting`: 사람이 읽는 본문에는 `function_step` 우선, 필요 시 raw `step_id` 병기
- `VM`: source는 FAB/main step 쪽 lineage를 유지하되, 화면에서는 measurement 계열 feature로 다룰 수 있게 표시

## 6. Source lineage 관점의 해석

앞으로 source를 볼 때는 물리 원천과 사용자 해석을 분리해서 보는 게 맞다.

### 6.1 물리 원천 기준

- `FAB`: main step/run event
- `INLINE`: meas step/metrology
- `ET`: meas step/electrical test
- `VM`: main step derived

### 6.2 사용자 해석 기준

- `KNOB`: process setting feature
- `FAB`: process/run feature
- `INLINE`: metrology feature
- `ET`: electrical feature
- `VM`: measurement-like feature
- `QTIME`: elapsed-time feature

### 6.3 왜 분리해야 하나

이걸 분리하지 않으면 다음이 꼬인다.

- VM을 ET/INLINE과 같은 lineage로 오해
- main step 기준 분석과 meas step 기준 분석이 섞임
- function_step 기준 비교에서 parent-child 관계가 사라짐

따라서 source registry에는 최소 아래 정보가 필요하다.

- `step_class`
- `measure_domain`
- `main_function_step`
- `feature_source`
- `feature_lineage`

그리고 각 source는 자기 step mapping registry를 가져야 한다.

- FAB step mapping
- INLINE step mapping
- VM step mapping

## 7. ET Time 분석 기준

ET time 분석은 단순히 `step_id`만 보면 안 된다.
아래 단위를 구별해서 보여줘야 한다.

- 같은 `step_id`, 다른 `step_seq`
- 같은 `step_seq`, 다른 `request_id`
- 같은 `request_id`, 다른 `tkout_time`

권장 표시 축:

- `step_id`
- `step_seq`
- `request_id` 또는 대체 package key
- `tkout_time`
- package point count

권장 집계 단위:

- wafer 기준 ET package 수
- lot 기준 ET package 수
- `step_id + step_seq` 기준 repeat count
- 동일 wafer에 대한 재측정 간격

## 8. 앱 내부 canonical field 권장안

soft-landing 이후 앱 내부에서 공통으로 쓰는 canonical 이름은 아래를 우선 사용한다.

- `product`
- `root_lot_id`
- `lot_id`
- `wafer_id`
- `step_id`
- `step_seq`
- `tkin_time`
- `tkout_time`
- `eqp`
- `ppid`
- `chamber`
- `item_id`
- `subitem_id`
- `shot_x`
- `shot_y`
- `value`
- `request_id`
- `map_id`
- `function_step`
- `step_type`
- `is_manual`
- `step_class`
- `measure_domain`
- `main_function_step`

derived feature 해석용 canonical 이름도 같이 둔다.

- `feature_name`
- `feature_group`
- `feature_source`
- `feature_lineage`
- `rule_type`
- `source_steps`
- `source_function_steps`
- `source_items`

## 9. 운영 구현 원칙

### 6.1 source profile

환경별 차이는 source profile로 관리한다.

- root path 후보
- root alias
- column alias
- source별 required columns
- source별 optional columns
- ET package key 우선순위
- INLINE↔ET matching table 경로

### 6.2 화면/기능별 적용

- `FileBrowser`: 실제 폴더 이름이 달라도 source alias로 접근
- `Dashboard`: source-specific canonical field를 기준으로 컬럼 해석
- `SplitTable`: wide/long 혼합을 지원하되 원천은 long 기준으로 정리하고, `KNOB_*`는 rulebook 기반으로 해석
- `Tracker`: ET watch는 `step_id + step_seq + package key + tkout_time` 기준
- `Inform`: `root_lot_id / lot_id / wafer_id / step_id`를 공통 축으로 사용
- `Step mapping`: raw `step_id`를 `function_step`으로 정규화하는 제품별 registry를 별도로 유지
- `Source-specific step mapping`: FAB/INLINE/VM은 각자 별도 `step_id -> function_step` registry를 유지
- `Step class`: step을 `main` / `meas`로 나눠 VM lineage와 ET/INLINE lineage를 구분

## 10. 지금 바로 반영해야 하는 것

우선순위는 아래 순서가 맞다.

1. source model 설정 파일 추가
2. adapter profile에 canonical column alias 확장
3. step mapping table을 `product + step_id -> function_step + step_class` registry로 확장
4. FAB/INLINE/VM step mapping을 source별 registry로 분리
5. KNOB rulebook을 단순 `step-ppid` 매핑이 아니라 derived feature rule로 확장
6. VM feature에 `main step derived` lineage 부여
7. ET package key helper 추가
8. INLINE↔ET matching table registry 추가
9. Dashboard / Tracker / SplitTable에서 source model 사용

## 11. 한 줄 결론

앞으로 `flow`의 데이터 모델 기준은
`FAB = run/event`,
`INLINE = subitem shot`,
`ET = (shot_x, shot_y) + step_seq + package`
로 잡는다.
INLINE과 ET는 직접 join하지 않고, 반드시 matching table을 통해 연결한다.
그리고 `ML_TABLE`의 `KNOB_*`는 단순 step별 값이 아니라, rulebook으로 계산되는 derived feature로 본다.
또한 제품별로 달라지는 `step_id`는 별도 step mapping table을 통해 `function_step`으로 정규화해 사용한다.
추가로 step은 `main`과 `meas`로 나눠 관리하고, VM은 `main step` 원천에서 나온 measurement-like feature로 취급한다.
