# ET, Wafer Layout, TableMap

ET, Wafer Layout, TableMap은 측정 package, wafer 공간 구조, 작은 lookup/base table 관계를 다룬다.

## Owns

- ET product/lot 검색, measurement package, `step_seq(XXpt)` 표시
- reformatter index 기반 Statistical Table, Box Table, WF Map, Trend, Radius Plot, Cumulative Plot
- wafer/shot/chip/TEG layout과 chip-shot table
- DB 관계 그래프, table edit/version, product YAML block 관리

## Does Not Own

- issue lifecycle 관리
- inform/mail thread 관리
- 대용량 분석 결과의 장기 UI state 보관

## Code Entrypoints

| Layer | Path |
|---|---|
| ET page | `frontend/src/pages/My_ETTime.jsx` |
| Wafer page | `frontend/src/pages/My_WaferLayout.jsx` |
| TableMap page | `frontend/src/pages/My_TableMap.jsx` |
| ET router | `backend/routers/ettime.py` |
| Wafer router | `backend/routers/waferlayout.py` |
| TableMap router | `backend/routers/dbmap.py` |
| Reformatter router | `backend/routers/reformatter.py` |
| Data | `data/flow-data/et_reports/`, `data/flow-data/dbmap/`, `data/flow-data/reformatter/` |

## Guardrails

- ET는 단순 step id가 아니라 request/package와 step sequence 기준으로 해석한다.
- wafer coordinate mapping은 명시적으로 유지한다.
- TableMap은 작은 lookup/base table과 relation hint에 집중한다.
- product YAML은 block 단위 추가/삭제로 관리한다.

## Verify

```bash
git diff --check
cd frontend && npm run build
```
