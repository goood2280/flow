# Inform Log

Inform Log는 제품/lot/wafer 이슈를 모듈 담당자에게 전달하고, 후속 대화와 근거 snapshot을 thread로 남기는 화면이다.

## Owns

- lot/root 단위 inform thread
- module, reason, deadline, status, 담당자 기록
- PEMS reason chip, 사용자 입력 reason, 이미지/첨부 roundtrip
- SplitTable CUSTOM snapshot embed
- multi-selected fab lot별 독립 SplitTable snapshot embed
- 다중 fab lot 등록용 `POST /api/informs/bulk-create` 순서 보존 저장
- module-wise mail compose/send
- 신규 등록 시 선택한 mail users/groups/extra emails를 Inform `mail_draft`로 저장해 등록 후 메일 탭과 발송창에서 이어 쓴다.
- Agent/Home Agent `inform_registration` unit의 최종 저장 계약. Agent는 slot 수집과 review를 담당하고, confirm 시 기존 `InformCreate`/`create_inform()` 경로만 호출한다.
- Dashboard inform widget용 요약 데이터

## Does Not Own

- SplitTable plan 자체 편집
- 원본 파일 수정
- 회의록/캘린더 액션의 주 저장소 역할
- 내부 source/scope/id를 메일 본문에 노출하는 것
- Inform 화면 내부 Flow-i prompt 입력창

## Code Entrypoints

| Layer | Path |
|---|---|
| Frontend page | `frontend/src/pages/My_Inform.jsx` |
| Standard screen | `frontend/src/components/FlowInformStandardScreen.jsx` |
| Main router | `backend/routers/informs.py` |
| Extra router | `backend/routers/informs_extra.py` |
| Module layer | `backend/app_v2/modules/informs/` |
| Inform data | `data/flow-data/informs/` |
| Flow-i guide | `data/flow-data/flowi_agent_features/inform.md` |

## Guardrails

- product가 불명확하면 생성 전에 후보를 확인한다.
- Inform 화면 안에는 `Flow-i 인폼 질문` 입력창을 두지 않는다. 자연어 기반 Inform 등록은 Home Agent `/api/home-agent/orchestrate` 또는 `/api/home-agent/run-tool`의 `inform_registration` 단위 AI에서 실행한다.
- 신규 등록용 `/config.products`, `/products`, sidebar product 후보는 LOT progress cache의 unique `product` 값에서 자동 생성한다. 별도 Inform product catalog를 관리하지 않는다.
- Inform product와 SplitTable product는 `ML_TABLE_` prefix와 대소문자가 달라도 같은 product로 본다.
- message/reason이 없으면 빈 inform을 만들지 않는다.
- Agent `inform_registration` unit은 confirm 전에는 Inform 저장 파일을 쓰지 않는다. short memory session은 `FLOW_DATA_ROOT/agent_unit_ai_sessions/inform_registration/`에 1시간 TTL로만 남긴다.
- 여러 fab lot을 선택해 생성할 때 각 Inform의 `lot_id`와 `fab_lot_id_at_save`는 선택한 target lot과 같아야 한다.
- 다중 fab lot 등록은 frontend 개별 POST 병렬 호출이 아니라 `/api/informs/bulk-create`로 보내며, 응답 `informs` 순서는 요청 순서와 같아야 한다.
- Config/modules, product contacts 변경은 `inform` page manager 이상만 수행한다.
- SplitTable snapshot endpoint는 같은 product/lot/custom_cols 요청이 겹치면 짧은 in-memory cache로 중복 계산을 피하되, 저장되는 embed payload shape는 유지한다.
- SplitTable snapshot은 사용자가 선택한 KNOB/CUSTOM/세트 컬럼만 포함한다. 저장된 plan 값은 선택된 컬럼 row 안에 overlay할 수 있지만, 선택하지 않은 plan-only 컬럼 row를 자동으로 추가하지 않는다.
- 수신자 후보는 `data/flow-data/users.csv`, 그룹 후보는 `data/flow-data/groups/groups.json`을 기준으로 한다.
- Inform 페이지 권한이 있는 사용자는 별도 유저별 모듈 조회 권한 없이 모든 module의 Inform을 조회한다.
- 메일에는 제목, 대상, 본문, Flow link만 남긴다.
- 첨부와 메일 실패는 UI에서 복구 가능한 상태로 보여준다.
- 상세 화면은 원문 `수정` 버튼을 노출하지 않는다. 열람 가능한 사용자는 `재인폼 {count}` 버튼으로 `재인폼 작성` wizard를 열어 기존 인폼의 `parent_id` 아래에 `[RE]` 재인폼을 새로 만든다.
- 재인폼은 목록 표에서 원 인폼 바로 아래에 들여쓰기와 `↳ [RE]` 표시로 항상 펼쳐 보인다. 목록 필터와 카운트는 루트 인폼 기준으로 유지한다.
- 원본 edit endpoint는 내부 유지보수용으로 유지하되, 작성 후 공개 UI에서는 본문 수정이나 첨부 세트 제거 진입을 노출하지 않는다.
- 메일 제목 기본값은 사유별 `reason_templates.<reason>.subject`가 있으면 신규 작성 미리보기, 메일 미리보기, 발송 기본 제목에 동일하게 적용한다. 지원 변수는 `{product}`, `{lot}`, `{module}`, `{reason}`이며, 템플릿이 비어 있으면 기존 `[plan 적용 통보] ...` 제목을 사용한다.

## Verify

```bash
git diff --check
cd frontend && npm run build
```
