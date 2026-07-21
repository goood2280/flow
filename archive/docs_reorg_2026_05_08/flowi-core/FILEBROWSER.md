# FileBrowser

파일탐색기는 DB root의 파일을 확인하고, schema/sample preview로 다음 작업지를 고르는 화면이다.

## 유지 범위

- DB root와 root-level base/rulebook 파일 탐색
- parquet/CSV schema 확인
- row preview, column 후보 확인, 제한된 filter/download
- S3 동기화 상태 확인
- Flowi에서 "이 파일/컬럼/샘플 보여줘" 요청 처리

## 제외 범위

- 분석 판단, chart 생성, plan/actual 비교
- 원본 DB root 파일 생성/수정/삭제
- 대용량 join 결과를 화면에 계속 보관하는 기능

분석은 Dashboard나 SplitTable로 넘긴다. 원본 변경은 admin 확인 workflow 없이는 하지 않는다.

## Code Entrypoints

| Layer | Path |
|---|---|
| Frontend page | `frontend/src/pages/My_FileBrowser.jsx` |
| Backend router | `backend/routers/filebrowser.py` |
| Flowi feature guide | `data/flow-data/flowi_agent_features/filebrowser.md` |
| Shared API helper | `frontend/src/lib/api.js` |

## Flowi Slots

| Slot | Required | Note |
|---|---:|---|
| `source_type` | optional | FAB/ET/INLINE/VM/EDS/QTIME 등 |
| `product` | optional | 파일 후보를 좁힐 때 사용 |
| `file` | optional | 특정 파일 요청 |
| `columns` | optional | schema/search/preview 대상 |
| `limit` | optional | preview row cap |
| `filter` | optional | read-only preview filter |

## Guardrails

- DB root는 기본 read-only다.
- 사용자가 "삭제/수정/이동/등록"을 말하면 먼저 admin 여부와 확인 문구가 필요하다.
- preview는 cap을 걸고, 전체 파일을 UI state에 올리지 않는다.
- FileBrowser에서 발견한 dataset을 SplitTable/Inform으로 넘길 때는 product/lot/wafer key를 명시한다.
