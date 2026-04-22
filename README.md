# flow

> **Fab data analytics + plan vs actual 추적 플랫폼.**
> 반도체 fab (특히 개발/pilot 단계) 공정 데이터를 single-user 데스크톱 환경에서 탐색·분석·공유하기 위한 웹 앱.

현재 버전: **v8.8.23** (codename `flow`)

---

## Features

| 페이지 | 설명 |
|---|---|
| 🏠 **홈** | 버전·changelog·Contact Bell·공지 배너 |
| 📂 **파일 탐색기** | DB/Base parquet·CSV 탐색. SQL 필터. 컬럼 projection 즉시 적용. S3 양방향(↓/↑) 신호등. DB 루트의 단일 CSV 는 Base 로 분류. 톱니(⚙) 좌하단. |
| 📊 **대시보드** | 동적 차트 (scatter/line/bar/pie/histogram/box/pareto/treemap/wafer_map/combo/heatmap/table/cross-table). LEFT JOIN 복수 소스 (joined 컬럼 suffix). **X/Y searchable dropdown + 자유 수식**. Fitting line (deg 1-4 + R²). USL/LSL/Target/Multi spec lines. |
| 🗂️ **스플릿 테이블** | LOT_WF 축 transposed 뷰. plan vs actual diff. fab_lot_id 그룹 헤더행. 빈 셀 dbl-click edit. XLSX 내보내기 (셀 병합·컬러 팔레트·자연 정렬·plan 주황 테두리). KNOB · INLINE · VM_ prefix 별 서브라벨 (Base matching csv). |
| 📋 **트래커** | 이슈 게시판 + Gantt. 카테고리별 색상. Lot/Wafer 태깅. |
| 🔗 **테이블맵** | DB 관계 그래프. 테이블 편집 (Base 루트 CSV 영구 저장). display_name 별도. 버전 관리 (최대 30 + 롤백 + 감사 trail). **Relation 자동 매칭** (case-insensitive 컬럼 교집합) + 매칭 pair chip 개별 제거. DB/Base 파일 임포트. |
| 🧠 **ML 분석** | TabICL / XGBoost / LightGBM 트리거 + SHAP 결과. 공정 영역 필터 (STI/Well/PC/…). |
| 📝 **인폼 로그** | wafer 단위 인폼 스레드. 모듈/사유 칩 + 이미지 첨부 + flow 상태 + SplitTable 연동 + 데드라인 + 간트. |
| 🗓 **회의관리** | 회의 차수(1차/2차/…) + 아젠다 + 회의록. **결정사항/액션아이템 각각 📅 달력 단위 push**. 반복 주기(weekly). 카테고리 공유. |
| 📅 **변경점 달력** | 월 grid + TODAY 강조 (잘림 방지) + 카테고리 색상 + 동시편집 version 락 + 상태(pending/in_progress/done). 회의 액션아이템/결정사항 meeting_ref 자동 동기화. |
| ✉️ **Messages** | 1:1 유저 ↔ admin + admin 공지. 우상단 Bell 동기화. |
| ⚙️ **관리자** | 사용자/알림/권한/그룹/인폼 설정/메일 API/Base CSV/Admin Log/다운로드/모니터/데이터 루트. 탭별 ErrorBoundary (크래시 격리). Base CSV 편집기 — step_matching / knob_ppid / **inline_matching / vm_matching**. |
| 📖 **개발자 가이드** | 아키텍처/사용법 문서. |

### 부가 기능
- **Contact 허브** — 우상단 ✉ 버튼. 유저 ↔ admin 1:1 문의 + admin 공지 작성 + 받은 문의함
- **공지 배너** — nav 아래 `📢 M월 D일 …` 포맷, 3일 TTL
- **PageGear 좌하단 통일** — 전 탭의 설정 톱니(⚙)가 좌하단 fixed 고정
- **flow 브랜드** — Outfit 900 `flow.` 타이포. `#FF5E00` 오렌지 + `#1e293b` 다크 도트
- **favicon** `/favicon.svg` — `f.` 로고
- **다크/라이트 모드**
- **세션 4h idle auto logout** — 서버측 enforcement + FE 활동 이벤트 sync
- **자동 백업** — `data_root` + `base_root` 주기적 zip (기본 24h). parquet 제외, logs 포함.

---

## Stack

- **Backend**: FastAPI · Polars · pyarrow · openpyxl · uvicorn · psutil · pyyaml · python-multipart
- **Frontend**: React 18 · Vite · 순수 SVG 차트 (외부 chart lib 제로)
- **데이터**: data/DB (Hive-partitioned parquet) + data/Base (단일 파일 ML_TABLE + 매칭 rulebook — step_matching / knob_ppid / inline_matching / vm_matching)

---

## Getting Started

### 1. 의존성
```bash
# Python — FastAPI + polars + pyarrow + openpyxl + psutil + pyyaml + python-multipart
pip install -r backend/requirements.txt

# Frontend
cd frontend && npm install
```
(또는 `python setup.py` 한 줄로 자체-추출 번들에서 backend deps + frontend build 를 모두 설치.)

### 2. 프론트 빌드
```bash
cd frontend && npm run build
```
`frontend/dist/` 생성. 백엔드 FastAPI 가 dist 를 정적 서빙.

### 3. 실행
프로젝트 루트(`FabCanvas.ai/`)에서:
```bash
uvicorn app:app --host 0.0.0.0 --port 8080
```
또는 동등하게:
```bash
python -m uvicorn app:app --host 0.0.0.0 --port 8080
```
→ `http://localhost:8080` 접속.

> 루트의 `app.py` 는 backend/app.py 를 자동으로 로드하는 shim 입니다. `--app-dir backend` 플래그 없이도 정상 기동합니다. (구 명령 `uvicorn app:app --app-dir backend` 도 그대로 동작.)

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

**8.8.13 (2026-04-21)** — 흰화면 crash fix(useEffect+Promise 5곳), 인폼 UX 대개편(wafer 중복 제거·접수 pill 제거·4버튼 통일·module/reason 수정·답글 상속·이력 최신↑·Embed legacy 컬러링·root_lot 단독 embed), **유저별 인폼 모듈 권한**(PageGear 매트릭스 UI + `/user-modules` BE + 목록 필터), **회의록 공동 작성**(`minutes.body_appendix` append-only + `/minutes/append` + 작성자 삭제), 회의 보관탭 제거 + 📎 이슈 가져오기, 이슈 카테고리 수정 · 간트 `🔎 제목·담당자·카테고리 필터` + 담당자 회색 표시, **대시보드 멀티 Y + 시리즈별 색상**, SplitTable 테두리 보강(FE CSS + XLSX 전셀) · 태그 drawer global 제거, **TableMap** 그룹 더블클릭→멤버 생성/Delete·우클릭 삭제/드래그 편입(동명 컬럼 흡수), **계보 탭 제거 + relation 컬럼 매칭 편집 표**, DB 참조 모달 가독성.

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
