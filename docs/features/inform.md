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
- Dashboard inform widget용 요약 데이터

## Does Not Own

- SplitTable plan 자체 편집
- 원본 파일 수정
- 회의록/캘린더 액션의 주 저장소 역할
- 내부 source/scope/id를 메일 본문에 노출하는 것

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
- 신규 등록용 `/config.products`는 `1.RAWDATA_DB_FAB/<product folder>`와 LOT progress 최신 캐시의 `product` 값을 합친다. `/products`와 sidebar product 후보는 FAB DB 기준을 유지한다. 기존 Inform record의 `product` 값은 보존하지만 이 두 소스에 없는 product는 신규 선택/필터 후보에 넣지 않는다.
- message/reason이 없으면 빈 inform을 만들지 않는다.
- 여러 fab lot을 선택해 생성할 때 각 Inform의 `lot_id`와 `fab_lot_id_at_save`는 선택한 target lot과 같아야 한다.
- 다중 fab lot 등록은 frontend 개별 POST 병렬 호출이 아니라 `/api/informs/bulk-create`로 보내며, 응답 `informs` 순서는 요청 순서와 같아야 한다.
- Config/modules, product catalog, product contacts 변경은 `inform` page manager 이상만 수행한다.
- SplitTable snapshot endpoint는 같은 product/lot/custom_cols 요청이 겹치면 짧은 in-memory cache로 중복 계산을 피하되, 저장되는 embed payload shape는 유지한다.
- 수신자 후보는 `data/flow-data/users.csv`, 그룹 후보는 `data/flow-data/groups/groups.json`을 기준으로 한다.
- `admin_settings.json` 읽기 실패 시 user-modules 조회/저장은 빈 설정으로 진행하지 않고 HTTP detail과 warning log를 남긴다.
- 메일에는 제목, 대상, 본문, Flow link만 남긴다.
- 첨부와 메일 실패는 UI에서 복구 가능한 상태로 보여준다.

## Verify

```bash
git diff --check
cd frontend && npm run build
```
