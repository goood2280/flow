# Inform Log

인폼 로그는 제품/lot/wafer 이슈를 모듈 담당자에게 전달하고, 후속 대화와 근거 snapshot을 thread로 남기는 화면이다.

## 유지 범위

- lot/root 단위 inform thread
- module, reason, deadline, status, 담당자 기록
- PEMS reason chip과 사용자 입력 reason
- 이미지/첨부 roundtrip
- SplitTable CUSTOM snapshot embed
- module-wise mail compose/send
- Dashboard inform widget에 필요한 요약 데이터
- Flowi에서 "인폼 등록/메일 작성/공유" 요청 처리

## 제외 범위

- SplitTable plan 자체 편집
- 원본 파일 수정
- 회의록/캘린더 액션의 주 저장소 역할
- 메일 본문에 내부 source/scope/id를 노출하는 것

## Code Entrypoints

| Layer | Path |
|---|---|
| Frontend page | `frontend/src/pages/My_Inform.jsx` |
| Standard screen component | `frontend/src/components/FlowInformStandardScreen.jsx` |
| Main backend router | `backend/routers/informs.py` |
| Extra backend router | `backend/routers/informs_extra.py` |
| Inform module layer | `backend/app_v2/modules/informs/` |
| Flowi feature guide | `data/flow-data/flowi_agent_features/inform.md` |
| Inform data | `data/flow-data/informs/` |

## Flowi Slots

| Slot | Required | Note |
|---|---:|---|
| `product` | yes | lot만 있으면 후보 확인 |
| `root_lot_id` or `fab_lot_id` | yes | thread anchor |
| `module` | recommended | GATE/STI/MOL/BEOL 등 |
| `reason` or `message` | yes | inform 본문 |
| `split_set` | optional | SplitTable snapshot 연결 |
| `recipients` | optional | mail compose 때 필요 |

## Guardrails

- product가 불명확하면 생성 전에 물어본다.
- message/reason이 없으면 빈 inform을 만들지 않는다.
- 메일에는 사용자가 알아야 할 제목, 대상, 본문, Flow link만 남긴다.
- 내부 Inform ID, SplitTable source/scope 문자열은 메일에 노출하지 않는다.
- 첨부와 메일 실패는 UI에서 복구 가능한 상태로 보여준다.
