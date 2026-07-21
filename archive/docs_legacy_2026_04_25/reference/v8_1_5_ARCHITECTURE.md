# HOL WEB — ARCHITECTURE (v8.1.5 "Options")

**2026-04-17 기준.** 사내 FAB 반도체 데이터 플랫폼. FastAPI + React + Polars 스택.

---

## 1. 프로젝트 루트

```
/config/work/holweb-fastapi/
├── backend/           # FastAPI 서버 (uvicorn app:app)
│   ├── app.py
│   ├── core/          # 공용 유틸 (paths, utils, notify, reformatter, ...)
│   └── routers/       # API 라우터 (파일별 엔드포인트 묶음)
├── frontend/          # React + Vite
│   ├── index.html, vite.config.js
│   └── src/
│       ├── App.jsx, main.jsx, config.js
│       ├── components/  (ComingSoon, Loading, Modal)
│       ├── lib/api.js
│       └── pages/       (My_*.jsx — 탭마다 1개)
├── scripts/           # 데이터 시드
└── version.json       # 버전 + changelog (API 통해 제공)
```

**외부 경로 (sharedworkspace):**
- 앱 데이터: `/config/work/sharedworkspace/holweb-data/`
- DB: `/config/work/sharedworkspace/DB/`

## 2. 실행 & 로그인

```bash
cd /config/work/holweb-fastapi/backend
uvicorn app:app --host 0.0.0.0 --port 8080
```

초기 관리자: `hol / hol12345!`

## 3. 백엔드 라우터 (전부 `/api/<name>/` prefix)

| 파일 | Prefix | 역할 |
|---|---|---|
| `auth.py` | `/api/auth` | 로그인, 회원가입, 사용자 관리 |
| `admin.py` | `/api/admin` | 사용자 승인, 권한, 알림, 다운로드 로그, **전역 settings (v8.1.5)** |
| `filebrowser.py` | `/api/filebrowser` | DB 디렉토리 탐색, parquet/CSV preview, SQL filter |
| `s3_ingest.py` | `/api/filebrowser` (shared) | S3 sync, aws configure 읽기/쓰기 |
| `dashboard.py` | `/api/dashboard` | 차트 config CRUD, compute_chart (+ exclude_null v8.1.5) |
| `splittable.py` | `/api/splittable` | Lot/Wafer 분할 테이블 저장/조회 |
| `tracker.py` | `/api/tracker` | 이슈 트래커 + **카테고리 색상 (v8.1.5)** |
| `tablemap.py` → `dbmap.py` | `/api/tablemap` | 테이블 관계도, 그룹/DB refs |
| `ml.py` | `/api/ml` | Process-Window ML 분석 |
| `ettime.py` | `/api/ettime` | ET 시간 분석 |
| `monitor.py` | `/api/monitor` | 시스템 모니터링 (CPU/메모리 /proc 기반) |
| `catalog.py` | `/api/catalog` | 제품/설비 카탈로그 |
| `reformatter.py` | `/api/reformatter` | 데이터 스키마 변환 규칙 |

**동적 로드:** `app.py` 는 `routers/*.py` 에서 `router` 객체를 자동 찾아서 include.

## 4. 프론트엔드 페이지 (`src/pages/`)

| 파일 | 탭 | 비고 |
|---|---|---|
| `My_Home.jsx` | 홈 | 권한 기반 카드, HOL-i 애니메이션, changelog 표시 |
| `My_Login.jsx` | 로그인 | — |
| `My_FileBrowser.jsx` | File Browser | DB 탐색, root parquets, CSV 지원, S3 sync |
| `My_Dashboard.jsx` | Dashboard | 차트 편집/표시, **exclude_null 옵션 (v8.1.5)** |
| `My_SplitTable.jsx` | Split Table | 1.6x 너비, wrap, 진한 경계선 (v803) |
| `My_Tracker.jsx` | Tracker | 이슈 리스트, Gantt, **카테고리 색상 (v8.1.5)** |
| `My_TableMap.jsx` | Table Map | Graph/Manage/Relations, DB refs |
| `My_Admin.jsx` | Admin | 사용자/권한/카테고리/AWS/Monitor 탭 |
| `My_ML.jsx` | ML Analysis | Process-Window 분석 (대용량 파일) |
| `My_Monitor.jsx` | — | Admin 탭 내부 |
| `My_DevGuide.jsx` | Dev Guide | 개발자 레퍼런스 |
| `My_ETTime.jsx` | — | 예정 |

## 5. 버전 관리 & 배포 워크플로

### 5.1 setup_v8.py (재편성된 v8.1.5 구조)

`setup_v8.py` 는 전체 프로젝트 파일을 **gzip + base64 인코딩**해서 단일 스크립트로 담음. Project Knowledge 에는 이를 **11개 part** 로 분할해 저장.

**Part 구조 (v8.1.5 재편성):**

| Part | 그룹 | 변경 빈도 | 크기 |
|---|---|---|---|
| 01 | meta (VERSION + setup code) | 릴리스마다 | 5KB |
| 02 | backend_stable (core/* + stable routers) | 드묾 | 54KB |
| 03 | backend_ml (routers/ml.py) | 드묾 | 28KB |
| 04 | frontend_infra (App, components, lib) | 중간 | 16KB |
| 05 | frontend_stable_pages | 드묾 | 36KB |
| 06 | frontend_ml (My_ML.jsx) | 드묾 | 29KB |
| 07 | feat_dashboard | **HOT** | 42KB |
| 08 | feat_filebrowser (+ s3_ingest) | **HOT** | 31KB |
| 09 | feat_split_tracker | **HOT** | 38KB |
| 10 | feat_admin_tablemap | 중간 | 44KB |
| 11 | scripts + main dispatch | 드묾 | 4KB |

**핵심 원칙:**
- 각 파일은 **하나의 part 에만** 존재 (cross-part split 없음)
- `FILES.update({...})` 패턴으로 parts 가 독립적
- 피처 단위 그룹핑 → Dashboard 수정 시 `part01` + `part07` 만 갱신
- gzip 으로 전체 setup 1,044KB → 326KB (-68%)

### 5.2 업데이트 배포 (update_vXXX.py)

**monolithic base64 self-contained** 스크립트로 배포.

```python
# update_v815.py 패턴
FILES = {
    'backend/routers/dashboard.py': ('<b64 chunk1>', '<b64 chunk2>', ...),
    ...
    'version.json': (...)
}
def main():
    for rel, payload in FILES.items():
        data = base64.b64decode("".join(payload))
        (ROOT / rel).write_bytes(data)
    subprocess.run(['npm', 'run', 'build'], cwd='frontend')
```

실행:
```bash
cd /config/work/holweb-fastapi
python update_vXXX.py
pkill -f "uvicorn app:app" || true
cd backend && uvicorn app:app --host 0.0.0.0 --port 8080 &
```

### 5.3 파일 업데이트 흐름 (일반적인 v8.1.x 버그픽스)

1. 사용자가 웹 Claude 에 버그 리포트 전달
2. Claude 가 Project Knowledge 에서 변경 대상 파일을 찾아 `project_knowledge_search` 로 해당 part 내용 추출
3. gzip 디코드 후 수정 → 재인코딩
4. `update_vXXX.py` 생성 (수정된 파일만 포함, base64 self-contained)
5. 사내 서버에서 `python update_vXXX.py`
6. 새 파트 파일 (`setup_v8_partXX.txt`) 재생성 → Project Knowledge 동기화

## 6. 전역 설정 (v8.1.5 도입)

`{data_root}/settings.json` — admin 전용 톱니 (우하단) 에서 조절:
```json
{
  "dashboard_refresh_minutes": 10,
  "dashboard_bg_refresh_minutes": 10
}
```

API: `GET /api/admin/settings` (read public), `POST /api/admin/settings/save` (admin only UI gate).

## 7. 데이터 포맷 규칙

### 7.1 DB 디렉토리 구조

- **Hive-partitioned**: `1.RAWDATA~/<product>/date=YYYY-MM-DD/*.parquet`
- **Root parquet**: DB 루트에 직접 `*.parquet` 또는 `*.csv` (수천 컬럼, 단일 파일)
- **Flat directory**: `DB/<name>/*.parquet` (partition 없이)

### 7.2 Polars 호환성 함정

```python
_STR = getattr(pl, "Utf8", None) or getattr(pl, "String", pl.Object)

# Bulk cast 금지 — 컬럼별 cast 필요
df = df.with_columns([pl.col(c).cast(_STR, strict=False) for c in df.columns])

# Multi-file scan
pl.scan_parquet(paths, cast_options=pl.ScanCastOptions(categorical_to_string="allow"))
```

### 7.3 Tracker Categories (v8.1.5)

```json
[
  {"name": "Analysis", "color": "#3b82f6"},
  {"name": "Monitor", "color": "#a855f7"}
]
```

- Legacy `["Analysis", "Monitor"]` 자동 upgrade (hash color fallback)
- 백엔드 `_normalize_cats()` 에서 처리

## 8. 주요 함정 (Gotchas)

| # | 함정 | 해결 |
|---|---|---|
| 1 | Vite 는 lazy import 안 됨 | `App.jsx` 는 static import 만 |
| 2 | `version.json` 을 static 파일로 두면 SPA catch-all 이 가로챔 | 반드시 API 라우트로 제공 |
| 3 | `safeFetch` 래퍼 필수 — 서버가 HTML 에러 페이지 반환 시 JSON parse 실패 | `src/lib/api.js` 사용 |
| 4 | Profile dropdown 클리핑 | `position:fixed` 사용 |
| 5 | `true`/`True` JSON-Python 혼동 | script 생성 시 주의 |
| 6 | JSX ternary else-branch 는 `<>...</>` 필요 | |
| 7 | psutil 금지 → `/proc/*` + cgroup 파일 사용 | `monitor.py` 참조 |
| 8 | SplitTable plan key 포맷 | `{lot_id}\|{col_name}\|{wafer_id}` — wafer_id 는 원본 문자열 유지 |
| 9 | 차트 폴링 heartbeat는 CPU burst 만 (memory burst 는 99% 가서 서버 reclaim) | `monitor.py` |
| 10 | Categorical cast 는 per-column (bulk `df.cast()` 금지) | 위 7.2 참조 |
| 11 | App.jsx 등 shared 파일이 parallel 대화의 충돌 지점 | 여러 대화 동시 진행 시 주의 |
| 12 | **exclude_null default True** (v8.1.5) — 기존 차트도 자동 적용됨 | 옵션 끄려면 Chart Config > Advanced |

## 9. 체크리스트 (새 기능 추가 시)

- [ ] `backend/routers/<new>.py` 에 `router = APIRouter(prefix="/api/<new>")`
- [ ] 프론트 `src/pages/My_<New>.jsx` 추가
- [ ] `src/config.js` 의 `TABS` 에 신규 탭 항목 추가
- [ ] `App.jsx` `PAGE_MAP` 에 컴포넌트 추가 (static import)
- [ ] `FEATURE_VERSIONS` 에 버전 기록
- [ ] setup_v8 파트 배치 고민 (HOT 인지 STABLE 인지)
