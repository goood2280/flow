# HOL WEB Guide (v8.1.5 "Options")

**빠른 참조 문서.** 자세한 아키텍처는 `ARCHITECTURE-v8.1.5.md` 참조.

---

## 1. 현재 버전

**v8.1.5 "Options"** — 2026-04-17

### 최근 변경 히스토리

| 버전 | 코드명 | 핵심 |
|---|---|---|
| v8.1.5 | Options | Dashboard exclude_null, admin 톱니 refresh, Tracker 카테고리 색상 |
| v8.1.4 | Crash-Hunt | TableMap +Table crash 수정, Monitor race condition, endpoint_url fallback |
| v8.1.3 | Polish Pack | SplitTable gap, TableMap GUIDE, Tracker category chip, ErrorBoundary |
| v8.1.2 | Column Safety | Dashboard 컬럼 드롭다운 스크롤, FileBrowser 파일 skip 방어 |
| v8.1.1 | Flow-Connect | Admin AWS Config UI, FileBrowser root-level 단일파일 `/roots` 미노출 |
| v8.1.0 | Flow-Fix patches | CSV 지원, S3 endpoint, gear/신호등, SplitTable 넓이 |
| v8.0.x | Flow-Fix | domain whitelist, dashboard preview, paths autodetect |

## 2. 빠른 명령어

```bash
# 서버 기동
cd /config/work/holweb-fastapi/backend
uvicorn app:app --host 0.0.0.0 --port 8080

# 업데이트 배포
cd /config/work/holweb-fastapi
python update_vXXX.py
pkill -f "uvicorn app:app" || true
cd backend && uvicorn app:app --host 0.0.0.0 --port 8080 &
# 브라우저 Ctrl+Shift+R

# 전체 재설치
python setup_v8.py

# setup_v8 파트 합치기
python merge_setup_v8.py
```

**로그인:** `hol / hol12345!`

## 3. 기능 현황

### 운영 중

- **File Browser** (v8.1.1) — Hive/Root parquet/CSV 탐색, SQL 필터, S3 sync, 컬럼 선택 전체
- **Dashboard** (v8.1.5) — 14종 차트 타입, exclude_null, custom refresh interval, LEFT JOIN, spec lines, SPC
- **Split Table** (v8.0.3) — Lot/Wafer 분할 plan, wrap 셀, 1.6x 너비
- **Tracker** (v8.1.5) — 이슈 관리, 카테고리 색상 picker, Gantt 차트, 이미지 인라인
- **Table Map** (v8.1.4) — Spotfire 스타일 관계 그래프, DB refs, 그룹/버전
- **Admin** (v8.1.5) — 사용자/권한/카테고리/AWS/Monitor + 우하단 톱니 전역 설정
- **Monitor** (v8.1.4) — CPU/메모리/디스크 (/proc + cgroup 기반)
- **ML Analysis** (v7.0) — Process-Window ML
- **Dev Guide** — 개발자 레퍼런스

### 예정

- **ET Time** — ET 시간 분석
- **메시지 시스템** (v8.1.6) — User Message 탭 + 홈 팝업 + Admin 답장
- **Monitor 확장** (v8.1.7) — 5분 로그 + 차트 + dummy worker + HOL-i 농사 애니메이션
- **Table Map S3 diff** (v8.1.8) — S3 연동 + edit history diff

## 4. 코드 규칙

### Python (백엔드)

```python
# Polars _STR 호환성 가드
from polars import __version__ as pl_ver
_STR = getattr(pl, "Utf8", None) or getattr(pl, "String", pl.Object)

# per-column cast (bulk 금지)
df = df.with_columns([pl.col(c).cast(_STR, strict=False) for c in str_cols])

# JSON 저장
from core.utils import save_json, load_json
save_json(path, data, indent=2)
```

### JSX (프론트엔드)

```jsx
// static import 만 (lazy import Vite 안 맞음)
import My_NewPage from "./pages/My_NewPage";

// safeFetch 사용
import { sf, postJson } from "./lib/api";
sf("/api/...").then(...).catch(e => setError(e.message));

// ternary 는 fragment 로
{flag ? <Foo /> : <><Bar /><Baz /></>}
```

### CSS 테마 (오렌지 CLI)

- accent: `#f97316` (orange-500) / `#ea580c` (orange-600)
- bg-primary dark: `#1a1a1a` / light: `#fafafa`
- 서체: 시스템 + monospace (JetBrains Mono 헤더)

## 5. setup_v8 파트 구조 (v8.1.5 재편성)

11 개 part, **피처 단위 그룹핑 + gzip+base64**.

| Part | 그룹 | 변경 빈도 |
|---|---|---|
| 01 | meta (VERSION + setup) | 릴리스마다 |
| 02 | backend_stable (core/* + stable routers) | 드묾 |
| 03 | backend_ml (ml.py) | 드묾 |
| 04 | frontend_infra (App, components, lib) | 중간 |
| 05 | frontend_stable_pages (Login/DevGuide/ETTime/Monitor/Home) | 드묾 |
| 06 | frontend_ml (My_ML.jsx) | 드묾 |
| 07 | **feat_dashboard** | HOT |
| 08 | **feat_filebrowser** (+ s3_ingest) | HOT |
| 09 | **feat_split_tracker** | HOT |
| 10 | **feat_admin_tablemap** | 중간 |
| 11 | scripts + main | 드묾 |

**일반적 v8.1.x 업데이트:** `part01` + 수정된 feature part 1~2개 = **총 2~3개 파일만 교체**.

## 6. 전역 설정 (v8.1.5)

화면 우하단 오렌지 톱니 (admin 전용) 클릭:

- **Dashboard auto-refresh** (1~240분, 기본 10분)
- **Dashboard BG recompute** (1~240분, 기본 10분)

저장: `{data_root}/settings.json` (`PATHS.data_root`)

## 7. 버그 리포트 템플릿 (새 대화)

```
HOL WEB v8.1.5 수정 요청.

[재현]
1. ...
2. ...
[기대 동작] ...
[실제 동작] ...
[스크린샷/로그] ...
```

Claude 는 Project Knowledge 에서 자동으로 최신 state (v8.1.5 반영된 parts) 를 로드.

## 8. 대화 운영 규칙

- 답변 최상단에 **`컨텍스트 잔량: ~NN%`** 표시 (예외 없음)
- 모바일에서 50KB 프롬프트 한도 — 요구사항은 한 메시지에 몰아서 전달
- setup_v8 전체 재생성 금지 (너무 큼) — 변경된 part 만 업데이트
- Claude 가 "현재 파일 필요" 하면 Project Knowledge 의 part 에서 추출 후 수정

## 9. 디렉토리 참조

```
Project: /config/work/holweb-fastapi/
App data: /config/work/sharedworkspace/holweb-data/
DB: /config/work/sharedworkspace/DB/
```
