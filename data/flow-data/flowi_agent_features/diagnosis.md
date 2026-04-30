# diagnosis

반도체 진단/RCA, item 의미, knowledge card, causal graph 요청을 처리한다.

## Flow
- 증상 metric과 source를 추출한다.
- item 의미를 dictionary/RAG로 해석한다.
- 확정 원인이 아니라 후보와 확인 차트를 제안한다.
- RAG/문서 내용은 flow-data 내부 저장소 밖으로 내보내지 않는다.

## Required Slots
- symptom metrics
- optional product, lot, source, test_structure

## Examples
- `GAA short Lg에서 DIBL과 SS 증가 원인 후보 알려줘`
