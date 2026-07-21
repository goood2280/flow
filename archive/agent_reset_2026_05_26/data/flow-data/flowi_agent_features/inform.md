# inform

인폼, 인폼로그, 공지, 공유, 메일 요청을 처리한다.

## Flow
- 신규 인폼이면 product, lot, message를 추출한다.
- product가 없고 lot만 있으면 product를 물어본다.
- message가 없으면 인폼 본문을 물어본다.
- 필요한 값이 충분하면 바로 생성한다.
- 메일 공유는 수신자가 알아야 할 제목, 대상, 본문, go/flow 링크만 남긴다. 내부 Inform ID나 SplitTable source/scope 문자열은 숨긴다.
- "보여줘/조회/현황/상태/요약/목록"은 인폼 로그 조회/요약이다.
- "등록/생성/남겨/기록/올려"가 있을 때만 등록 초안을 만든다.
- 등록 초안은 확인 전에는 실제 저장하지 않는다.

## Required Slots
- product
- root_lot_id 또는 fab_lot_id
- message/reason

## Deterministic Actions
- `inform_lot_module_summary`: lot별 모듈 인폼 현황 조회
- `register_inform_log`: 단일/batch 인폼 등록 초안
- `register_inform_walkthrough`: 모듈별 전체 인폼 작성 walkthrough
- `compose_inform_module_mail`: 모듈 담당자 메일 preview

## Examples
- `Lot: A1003 인폼로그 aaa1 커스텀셋으로 등록해줘`
- `PRODA A1004 인폼으로 spacer split 변경 공유해줘`
- `A1003 인폼로그 현황 보여줘`
