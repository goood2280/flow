# SplitTable 용어집

SplitTable에서 보이는 내부 용어를 사용자 관점의 말로 풀어쓴 문서입니다.

## 1. root_lot_id

공정의 원래 lot 기준 ID입니다. lot이 split/rework 되면서 뒤가 바뀌어도, 같은 출발 lot인지 볼 때 기준이 됩니다.

## 2. fab_lot_id

현재 FAB 진행 중 실제로 쓰는 lot ID입니다. 운영 추적이나 설비 이력 확인은 보통 이 값을 기준으로 봅니다.

## 3. wafer_id

lot 안의 개별 wafer 번호입니다.

## 4. parameter

한 줄에 표시되는 관리 대상 항목입니다. 예를 들어 KNOB, INLINE, VM, MASK 같은 항목이 여기에 해당합니다.

## 5. 적용 공정 정보

해당 parameter가 실제로 어느 step_id/function_step과 연결되는지 보여주는 보조 정보입니다.

## 6. function_step

엔지니어가 이해하기 쉬운 공정 기능 이름입니다. 제품마다 step_id가 달라도 같은 의미의 공정을 묶어 볼 때 사용합니다.

## 7. step_id

시스템과 설비가 실제로 쓰는 공정 ID입니다. 적용이나 설비 확인은 결국 step_id 기준으로 합니다.

## 8. KNOB

FAB의 recipe/ppid 같은 설정값을 사람이 보기 쉬운 운영 파라미터로 정리한 값입니다.

## 9. INLINE

공정 중간에서 측정된 검사/계측 값입니다. shot 또는 subitem 단위로 관리되는 경우가 많습니다.

## 10. VM

설비에서 나온 예측/가상 측정값입니다. 실제 측정 대신 빠르게 상태를 볼 때 씁니다.

## 11. CUSTOM

사용자가 필요한 컬럼만 골라 만든 개인/팀용 보기입니다.

## 12. History

누가 언제 어떤 값을 계획(plan)으로 넣었는지, 바꾸거나 삭제했는지의 기록입니다.

## 13. 적용 요약

현재 화면에 보이는 parameter들이 어떤 function_step / step_id에 연결되는지 모아 보여주는 표입니다.

## 14. source

값이 어디서 왔는지의 원천입니다. 예: ML_TABLE, FAB DB, INLINE DB 등.

## 15. precision

숫자를 몇 자리까지 반올림해서 보여줄지 정하는 표시 규칙입니다.
