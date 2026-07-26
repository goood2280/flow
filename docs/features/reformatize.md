# ET 다운로드 (reformatize)

탭 `reformatize` / 라벨 "ET 다운로드". auto report의 reformatize 흐름을 flow 화면으로 제공한다. 제품(DB ET 폴더)을 고르면 vehicle reformatter 규칙으로 **shot 단위 index 값**을 계산해 페이지 단위로 보여주고 CSV로 내려준다.

## Owns

- DB ET 제품 목록과 각 제품에 매칭된 vehicle CSV 표시
- vehicle CSV의 REAL / ADDP 항목 목록 (index 선택 UI)
- index 계산 실행과 offset/limit 페이지 반환
- 전체 결과 CSV 다운로드 + 다운로드 이력 기록
- 페이지 행 수 등 설정 (톱니바퀴)
- (admin) ADDP 수식 도움말과 새 수식 테스트 미리보기

## 규칙 소스

`{data_root}/reformatter/<vehicle>_reformatter.csv`

- **REAL** — raw `ITEMID`, abs 여부, scale factor
- **ADDP** — ADDP Form과 참조 컬럼

`/items`는 데이터를 읽지 않고 규칙 CSV만 파싱하므로 가볍다. index 선택 UI는 이걸 쓴다.

## 제품 탐색

제품 목록은 ET 측정시간 탭과 **동일한 탐색**(`core.lot_step.db_product_candidates`)을 쓴다. 제품 폴더, hive 파티션(`product=`), 플랫 파일명, parquet의 `product` 컬럼 스캔까지 흡수한다. 이 경로가 실패할 때만 폴더 나열로 폴백한다.

> 사내 DB에서 제품이 검출되지 않던 문제는 단순 폴더 나열을 `lot_step` 루트 해석으로 교체해 해결했다 (2026-07-21). 폴더 구조만 가정하는 코드로 되돌리지 않는다.

## Does Not Own

- reformatter **규칙 편집** — `backend/routers/reformatter.py` (관리자가 제품별 JSON 규칙 등록, FileBrowser 다운로드·대시보드 차트·ML 학습이 같은 로직을 공유)
- ET 측정 소요시간 — [et-time.md](et-time.md)
- ET 진행 추적 — [tracker.md](tracker.md)

## Code Entrypoints

| Layer | Path |
|---|---|
| Frontend page | `frontend/src/pages/My_Reformatize.jsx` |
| Backend router | `backend/routers/reformatize.py` |
| 규칙 편집 router | `backend/routers/reformatter.py` |
| 규칙 로직 | `backend/core/reformatter.py` |
| 규칙 CSV | `{data_root}/reformatter/<vehicle>_reformatter.csv` |
| 다운로드 이력 | `downloads.jsonl` (`source` 필드로 출처 구분) |

## API

| Method | Path | 권한 | 용도 |
|---|---|---|---|
| GET | `/api/reformatize/products` | user | 제품 목록 + 매칭 vehicle CSV |
| GET | `/api/reformatize/items` | user | REAL/ADDP 항목 목록 |
| GET·POST | `/api/reformatize/settings` | user | 페이지 행 수 등 설정 |
| POST | `/api/reformatize/run` | user | index 계산 → 페이지 반환 |
| GET | `/api/reformatize/download` | user | 전체 결과 CSV (이력 기록) |
| GET | `/api/reformatize/formula-help` | **admin** | 수식 함수/참조 컬럼 도움말 |
| POST | `/api/reformatize/test` | **admin** | 새 ADDP 수식 테스트 미리보기 |
| POST | `/api/reformatize/test/download` | **admin** | 테스트 결과 CSV (이력 기록) |

## Guardrails

- 매칭되는 vehicle CSV가 없으면 400으로 명확히 알린다. 빈 결과로 조용히 넘기지 않는다.
- 수식 테스트 계열 엔드포인트는 `require_admin` 게이트를 유지한다 — 임의 수식 평가 경로다.
- CSV 다운로드는 항상 `downloads.jsonl`에 `source`와 함께 기록한다.
- 계산 결과는 auto report의 reformatize와 일치해야 한다. 한쪽만 고치지 않는다.

## Verify

```bash
git diff --check
```

```bash
python -m pytest tests/test_vehicle_reformatter_output.py
```

```bash
cd frontend && npm run build
```
