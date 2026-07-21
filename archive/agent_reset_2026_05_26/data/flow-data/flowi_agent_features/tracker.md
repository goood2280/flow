# tracker

이슈 등록, 모니터링, Analysis, tracker 요청을 처리한다.

## Flow
- 신규 이슈 생성이면 title, category, product, lot/wafer, content를 추출한다.
- category는 `Analysis/분석`이면 Analysis, `Monitor/모니터링`이면 Monitor로 둔다.
- category가 비어 있으면 Monitor/Analysis를 물어본다.
- 필요한 값이 충분하면 바로 생성한다.
- 기존 이슈 수정/삭제/status 변경은 권한과 대상 확인 전 실행하지 않는다.

## Required Slots
- title: `TEST3 이름으로`, `네모의 꿈이라고` 같은 표현에서 추출
- category: Monitor 또는 Analysis
- product
- root_lot_id/fab_lot_id/wafer_id 중 하나 이상
- content/body

## Examples
- `이슈 PRODA A1004 Analysis 하는거 TEST3 이름으로 등록해줘 내용은 ㅁㅁㅁ 적어줘`
- `이슈추적 네모의 꿈이라고 만들고 랏 PRODB B1025B.1 등록해주세요 모니터링용이야`
