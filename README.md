# flow

> **Fab data analytics + plan vs actual 추적 플랫폼.**
> 반도체 fab (특히 개발/pilot 단계) 공정 데이터를 single-user 데스크톱 환경에서 탐색·분석·공유하기 위한 웹 앱.

현재 버전: **v8.4.5** (codename `flow`)

---

## Features

| 페이지 | 설명 |
|---|---|
| 📂 **파일 탐색기** | DB/Base parquet·CSV 탐색. SQL 필터. 컬럼 projection 즉시 적용. S3 양방향 sync (admin). |
| 📊 **대시보드** | 동적 차트 (scatter/line/bar/pie/histogram/box/pareto/treemap/wafer_map/combo/heatmap/table/cross-table). LEFT JOIN 복수 소스. Fitting line (deg 1-4 + R²). USL/LSL/Target/Multi spec lines. |
| 🗂️ **스플릿 테이블** | LOT_WF (root_lot_id + wafer_id) 축의 transposed 뷰. plan vs actual diff. **fab_lot_id 그룹 헤더행** (wafer 자동 인접 정렬). diff 클라이언트 즉시 필터. 빈 셀 dbl-click edit + 전체셋 suggestion. XLSX 내보내기 (셀 병합·컬러 팔레트·자연 정렬·plan 주황 테두리). |
| 📋 **트래커** | 이슈 게시판 + Gantt. 카테고리별 색상. Lot/Wafer 태깅. |
| 🔗 **테이블맵** | DB 관계 그래프 노드. 테이블 편집 (Base 루트 CSV 영구 저장). display_name 별도 (UI 라벨 vs 물리 파일명). 버전 관리 (최대 30개 + 롤백 + 감사 trail). DB/Base 파일 임포트. |
| 🧠 **ML 분석** | TabICL / XGBoost / LightGBM 트리거 + SHAP 결과. 공정 영역 필터 (STI/Well/PC/…). |
| ⚙️ **관리자** | 사용자/알림/권한/로그/다운로드/모니터/데이터 루트. |
| 📖 **개발자 가이드** | 아키텍처/사용법 문서. |

### 부가 기능
- **Contact 허브** — 우상단 ✉ 버튼. 유저 ↔ admin 1:1 문의 + admin 공지 작성 + 받은 문의함
- **공지 배너** — nav 아래 `📢 M월 D일 …` 포맷, 3일 TTL
- **flow 브랜드** — Outfit 900 `flow.` 타이포. `#FF5E00` 오렌지 + `#1e293b` 다크 도트
- **favicon** `/favicon.svg` — `f.` 로고
- **다크/라이트 모드**

---

## Stack

- **Backend**: FastAPI · Polars · openpyxl · uvicorn
- **Frontend**: React 18 · Vite · 순수 SVG 차트 (외부 chart lib 제로)
- **데이터**: data/DB (Hive-partitioned parquet) + data/Base (단일 파일 ML_TABLE + 매칭 rulebook)

---

## Getting Started

### 1. 의존성
```bash
# Python
pip install -r backend/requirements.txt   # 또는 FastAPI + polars + openpyxl + pyarrow

# Frontend
cd frontend && npm install
```

### 2. 프론트 빌드
```bash
cd frontend && npm run build
```
`frontend/dist/` 생성. 백엔드 FastAPI 가 dist 를 정적 서빙.

### 3. 실행
```bash
python -m uvicorn app:app --host 127.0.0.1 --port 8080 --app-dir backend
```
→ `http://localhost:8080` 접속.

기본 admin: `hol / hol12345!`

### 4. 더미 데이터 시드 (선택)
```bash
python data/_gen_gaa2n.py          # 2nm GAA Nanosheet 픽처 생성
python data/_restructure.py         # Hive-flat 으로 재편
python scripts/migrate_lot_format.py    # root/fab_lot_id 포맷 마이그레이션
python scripts/knob_lot_dominant.py     # ML_TABLE KNOB lot-dominant 보정
python scripts/ensure_diff_fixtures.py  # diff 시연용 split fixtures
```

---

## Paths & Config

- `data/DB/` — Hive-partitioned: `<MODULE>/<table>/product=<P>/part-*.{parquet,csv}`
- `data/Base/` — 단일 파일 ML_TABLE_PROD*, rulebook, TableMap CSV
- `data/holweb-data/` — 런타임 설정/세션/캐시 (gitignored)
- 환경변수 override: `FABCANVAS_DB_ROOT`, `FABCANVAS_BASE_ROOT`, `FABCANVAS_WAFER_MAP_ROOT`

---

## Structure

```
FabCanvas.ai/
├── VERSION.json             # 버전 + changelog (홈 표시)
├── backend/
│   ├── app.py               # FastAPI entrypoint
│   ├── core/                # paths · utils · auth helpers · s3_sync · notify · domain
│   └── routers/             # admin · auth · catalog · dashboard · dbmap · ettime
│                            # · filebrowser · messages · ml · monitor · reformatter
│                            # · s3_ingest · session_api · splittable · tracker
├── frontend/
│   ├── index.html
│   ├── public/favicon.svg
│   └── src/
│       ├── App.jsx          # nav · Contact button · NoticeBanner · ErrorBoundary
│       ├── pages/           # My_Home · My_Login · My_FileBrowser · My_Dashboard
│       │                    # · My_SplitTable · My_TableMap · My_Tracker · My_ETTime
│       │                    # · My_ML · My_Admin · My_DevGuide · My_Message · My_Monitor
│       ├── components/      # BrandLogo · AwsPanel · Loading · Modal · ComingSoon
│       └── lib/api.js
├── scripts/                 # 데이터 시드 · 마이그레이션 · fixtures
├── docs/                    # ARCHITECTURE.md · GUIDE.md
└── reports/
```

---

## Version history

8.4.5 (2026-04-21) — Contact 허브, XLSX 팔레트/병합/자연정렬, TableMap 버전관리, SplitTable 톱니 override, diff 시연 fixture, FB 톱니 분리, 공지배너, favicon

8.4.4 — 랏 포맷 (root 5자 / fab_lot `{root}{L}.{n}`), SplitTable fab_lot_id 그룹 헤더 + diff 클라이언트 필터 + dbl-click edit, Dashboard Base 소스 연동, flow 브랜드 로고

8.4.3 — flow Platform (단위기능 페이지 철학 + OOM lazy scan + ML_TABLE pivot)

8.4.0–2 — flow 리브랜딩 (FabCanvas.ai → flow, 로고 타이포 확정)

8.3.x — 이전 HOL_WEB → FabCanvas.ai 통합

상세 내역은 `VERSION.json` 참고.

---

## Conventions

- 한국어 UX (nav/탭/메시지)
- 7-색 CELL_COLORS 팔레트 (green/yellow/orange/blue/purple/teal/pink) — SplitTable KNOB/MASK 셀 컬러링 + XLSX
- 자연 정렬: prefix 뒤 숫자 기준 (`KNOB_12.0_ASV_...`)
- CSV/XLSX 내보내기: UTF-8 BOM + `downloaded_at` · `username` · `root_lot_id` · `fab_lot_id` (병합) · `Parameter` 순 헤더

---

## License

Private. 이 레포는 사내/개인용 검증 목적.
