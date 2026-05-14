# Diagnosis, Knowledge, RAG

Diagnosis와 Knowledge는 반도체 이슈를 item semantics, causal graph, case DB, RAG 근거로 연결한다.

## Owns

- 진단/RCA 지식 구조
- Knowledge Card, causal graph, case DB
- Flow-i RAG Update와 사내 지식 입력
- reformatter/TEG proposal의 지식 연결
- ML 결과의 도메인 해석 보조

## Does Not Own

- 원인 단정
- raw data 원본 수정
- tracker/inform의 운영 상태 원본 저장

## Code Entrypoints

| Layer | Path |
|---|---|
| Diagnosis page | `frontend/src/pages/My_Diagnosis.jsx` |
| Knowledge page | `frontend/src/pages/My_Knowledge.jsx` |
| Semiconductor router | `backend/routers/semiconductor.py` |
| Knowledge router | `backend/routers/knowledge.py` |
| ML router | `backend/routers/ml.py` |
| RAG docs | `docs/RAG/SEMICONDUCTOR_RAG_OPERATIONS.md` |
| Knowledge data | `data/flow-data/knowledge/` |

## Guardrails

- 통계 결과는 원인으로 단정하지 않는다.
- source, step, area, direction, confidence를 함께 보여준다.
- 사내 지식 반영 후에는 검색/답변 근거가 바뀌었는지 확인한다.
- 운영 action은 Tracker나 Inform으로 넘긴다.
- Wiki/schema/ontology 같은 shared knowledge write는 `diagnosis` page manager 이상만 수행한다. Prompt preview, chat, search, feedback 조회 흐름은 일반 로그인 사용자에게 열려 있다.

## Verify

```bash
git diff --check
python scripts/smoke_test.py
```
