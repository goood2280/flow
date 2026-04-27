# flow

> **Fab data analytics + plan vs actual 추적 플랫폼.**
> 반도체 fab (특히 개발/pilot 단계) 공정 데이터를 single-user 데스크톱 환경에서 탐색·분석·공유하기 위한 웹 앱.

현재 버전: **v9.0.4** (codename `flow`)

---

## Features

| 페이지 | 설명 |
|---|---|
| 🏠 **홈** | 버전·changelog·Contact Bell·공지 배너 |
| 📂 **파일 탐색기** | DB/Base parquet·CSV 탐색. SQL 필터. 컬럼 projection 즉시 적용. S3 양방향(↓/↑) 신호등. DB 루트의 단일 CSV 는 Base 로 분류. 톱니(⚙) 좌하단. |
| 📊 **대시보드** | 동적 차트 (scatter/line/bar/pie/histogram/box/pareto/treemap/wafer_map/combo/heatmap/table/cross-table). LEFT JOIN 복수 소스. Admin 설정으로 Charts/FAB Progress/Alert Watch 섹션 공개 범위 제어. |
| 🗂️ **스플릿 테이블** | LOT_WF 축 transposed 뷰. plan vs actual diff. fab_lot_id 그룹 헤더행. 빈 셀 dbl-click edit. XLSX 내보내기 (셀 병합·컬러 팔레트·자연 정렬·plan 주황 테두리). KNOB · INLINE · VM_ prefix 별 서브라벨 (Base matching csv). |
| 📋 **트래커** | 이슈 게시판 + Gantt. 카테고리 필수 지정. PROD→root/fab lot 검색. 이슈 단위 메일 설정/수신그룹/템플릿/미리보기. Analysis ET DB 측정 `step_seq(XXpt)` 표시. |
| 🧪 **ET Report** | 제품/lot 검색 → 측정 package → reformatter index page → scoreboard/PPTX. index당 Statistical Table, Box Table, WF Map, Trend, Radius, Cumulative Plot 제공. |
| 🧭 **WF Layout** | wafer/shot/chip/TEG 배치 확인. TEG는 Shot Sample에서 선택/검색 표시. Chip View는 칩별 포함 shot table + CSV 다운로드. |
| 🔗 **테이블맵** | DB 관계 그래프. 테이블 편집/버전 관리. Product Connection 숨김/복원. 제품별 YAML block은 단일 `product_config/products.yaml`에서 추가/삭제 관리. |
| 🧠 **ML 분석** | TabICL / XGBoost / LightGBM 트리거 + SHAP 결과. 공정 영역 필터 (STI/Well/PC/…). |
| 📝 **인폼 로그** | wafer 단위 인폼 스레드. 모듈/사유 칩 + 이미지 첨부 + flow 상태 + SplitTable snapshot + 데드라인 + 간트. SplitTable과 같은 root/fab/wafer 표시 구조 유지. |
| 🗓 **회의관리** | 회의 차수(1차/2차/…) + 아젠다 + 회의록. 이슈 가져오기는 글/이미지를 함께 포함하고, 메일 발송 시 용량 초과 이미지는 링크 안내로 전환. |
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
- **세션 6h idle + 24h absolute auto logout** (v9.0.0) — 서버측 enforcement + FE 활동 이벤트 sync
- **자동 백업** — `data_root` + `base_root` 주기적 zip (기본 24h). parquet 제외, logs 포함.
- **관측성 알림 허브** (v9.0.0) — 8종 이벤트(plan/회의록/액션아이템/댓글/상태/step 도달 등) + 유저별 구독 룰
- **smoke test** — `python scripts/smoke_test.py` → 27개 라우트 검증 (외부 의존성 0)

---

## Stack

- **Backend**: FastAPI · Polars · pyarrow · openpyxl · xlsxwriter · xlrd · uvicorn · psutil · pyyaml · python-multipart
- **Frontend**: React 18 · Vite · 순수 SVG 차트 (외부 chart lib 제로)
- **데이터**: data/DB (Hive-partitioned parquet) + data/Base (단일 파일 ML_TABLE + 매칭 rulebook — step_matching / knob_ppid / inline_matching / vm_matching)

---

## Getting Started

### 1. 의존성
```bash
# Python — FastAPI + polars + pyarrow + openpyxl/xlsxwriter/xlrd + psutil + pyyaml + python-multipart
pip install -r backend/requirements.txt

# Frontend
npm install
cd frontend && npm install
```
(또는 `python3 setup.py` 한 줄로 자체-추출 번들에서 backend deps + frontend build 를 모두 설치.)

### 2. 프론트 빌드
```bash
cd frontend && npm run build
```
`frontend/dist/` 생성. 백엔드 FastAPI 가 dist 를 정적 서빙.

### 3. 실행
프로젝트 루트(`flow/`)에서:
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

### 5. Smoke test (서버 기동 후)
```bash
python scripts/smoke_test.py    # 27개 항목, 외부 의존성 0, <5초
```

---

## Paths & Config

- `data/Fab/` — 로컬 개발용 DB 루트. 사내에서는 `/config/work/sharedworkspace/DB`
- `data/flow-data/` — 사용자 데이터 루트. 설정, 세션, 캐시, 그룹, 인폼, 백업 상태 저장
- 환경변수 override: `FLOW_DATA_ROOT`, `FLOW_DB_ROOT`, `FLOW_WAFER_MAP_ROOT`
- `base_root` 는 `db_root` 와 동일한 호환 alias 입니다. 별도 Base 루트는 사용하지 않습니다.
- 사내 배포 자동 감지: Linux에서 `/config/work/sharedworkspace`가 있으면 DB는 `/config/work/sharedworkspace/DB`, `flow-data`는 `/config/work/sharedworkspace/flow-data` 존재 시 자동 바인딩

---

## Structure

```
flow/
├── VERSION.json             # 버전 + changelog (홈 표시)
├── app.py                   # uvicorn shim (backend/app.py 로드)
├── setup.py                 # self-contained installer
├── backend/
│   ├── app.py               # FastAPI app assembly + static serving
│   ├── core/                # paths · roots · utils · auth · s3_sync · notify · domain
│   │                        # · parquet_perf · lot_step · long_pivot · sysmon · mail
│   ├── app_v2/
│   │   ├── runtime/         # AuthMiddleware · router loading · startup services
│   │   ├── shared/          # JsonFileStore · Result · source adapter
│   │   └── modules/         # tracker · meetings 등 service/repository/domain
│   └── routers/             # admin · auth · catalog · dashboard · dbmap · ettime
│                            # · filebrowser · informs · meetings · messages · ml
│                            # · mail_groups · monitor · reformatter · s3_ingest
│                            # · session_api · splittable · tracker · llm · groups
│                            # · calendar · informs_extra
├── frontend/
│   ├── index.html
│   ├── public/favicon.svg
│   └── src/
│       ├── App.jsx          # shell composition only
│       ├── app/             # pageRegistry · useFlowShell 상태 훅
│       ├── pages/           # My_Home · My_Login · My_FileBrowser · My_Dashboard
│       │                    # · My_SplitTable · My_TableMap · My_Tracker · My_ETTime
│       │                    # · My_ML · My_Admin · My_DevGuide · My_Inform
│       │                    # · My_Meeting · My_Calendar · My_WaferLayout
│       ├── components/      # BrandLogo · AwsPanel · Loading · Modal · ComingSoon
│       │                    # · UXKit (v9.0.0 — 9 프리미티브)
│       └── lib/api.js
├── scripts/                 # 데이터 시드 · 마이그레이션 · fixtures · smoke_test
├── docs/                    # README · ARCHITECTURE · DEVELOPMENT
│                            # · PRODUCT_PHILOSOPHY · FEATURE_GOALS
└── archive/                 # legacy docs · retired pages · generated request captures
```

---

## Version history

**9.0.4 (2026-04-26)** — Tracker 이슈 단위 메일/수신그룹/템플릿/Analysis ET 측정 표시, ET Report reformatter index page/PPTX, TableMap Product/YAML 관리, WF Layout TEG/Chip View 정리, Dashboard section visibility와 Meeting/Inform 연동 보강.

**9.0.1 (2026-04-23)** — SplitTable root↔fab 연결 정상화(`_scan_product` coalesce fix, view_split 5자 경고화, available_fab_lots 동봉, /lot-candidates root_scope 데이터-driven join) + 인폼/회의록 메일 자동 본문(PEMS 단일화, KNOB/MASK/FAB 첫 50개 폴백, minutes.body 자동 폴백).

**9.0.0 (2026-04-23)** — 메이저 rollup. 이월 큐 5 청산(parquet_perf streaming+prune+meta · stability playbook · SplitTable history/final+drift · dev-verifier smoke 의무) + PRODA 중복 근본(trim+CI dedup + `/products/dedup`) + SplitTable CUSTOM 재설계(기본 식별자 pool 제외 + `custom_cols` ad-hoc) + 인폼 embed `st_view`/`st_scope` 보존 + 트래커 Lot FAB/ET auto-step(`core/lot_step.py` + `/lot-step`·`/lot-watch`·`/lot-check`) + SplitTable FAB/INLINE/VM step sub-label + `/infer-step-mapping` + 알림 허브(`notify.py` v7.0 + `emit_event` + 8 이벤트 + `/admin/notify-rules`) + 세션 6h idle + 24h absolute + 보안 감사 17 Finding high 4 즉시 fix + UXKit.jsx 9 프리미티브.

8.8.32 (2026-04-23) — FileBrowser 교차 선택 + 첫 공식 smoke test 세트(27개).

8.8.31 — FAB/INLINE long-format primary 승격 + SplitTable override long adapter + FileBrowser 소스 배지.

8.4.5 (2026-04-21) — Contact 허브, XLSX 팔레트/병합/자연정렬, TableMap 버전관리, SplitTable 톱니 override, diff 시연 fixture, FB 톱니 분리, 공지배너, favicon

8.4.4 — 랏 포맷 (root 5자 / fab_lot `{root}{L}.{n}`), SplitTable fab_lot_id 그룹 헤더 + diff 클라이언트 필터 + dbl-click edit, Dashboard Base 소스 연동, flow 브랜드 로고

8.4.3 — flow Platform (단위기능 페이지 철학 + OOM lazy scan + ML_TABLE pivot)

8.4.0–2 — flow 리브랜딩 (flow → flow, 로고 타이포 확정)

8.3.x — 이전 flow → flow 통합

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
