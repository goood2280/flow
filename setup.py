#!/usr/bin/env python3
"""FabCanvas (flow) v8.8.23 — self-contained installer.

Usage (fresh machine):

    python setup.py                # extract + install deps + build frontend
    python setup.py extract        # just extract embedded sources
    python setup.py install-deps   # pip install backend deps only
    python setup.py build-frontend # npm install + npm run build only
    python setup.py version        # print VERSION
    python setup.py sync-version   # stamp VERSION onto VERSION.json

Run the server afterwards:

    uvicorn app:app --host 0.0.0.0 --port 8080

Login: hol / hol12345!  (override with FABCANVAS_ADMIN_PW / HOL_ADMIN_PW)

This file embeds 15 source files as gzip+base64 blobs. Data
(data/Base, data/DB, data/holweb-data — users.csv, groups, informs,
admin_settings, tracker, splittable, meetings, calendar, messages,
dbmap, S3 sync config, …) is NEVER bundled and NEVER overwritten —
re-running setup.py on an existing install preserves ALL user data.

보존 whitelist (v8.8.3):
  - data/ 트리 전체 (data/Base, data/DB, data/holweb-data)
  - holweb-data/ 세그먼트가 포함된 모든 경로
  - HOL_DATA_ROOT / FABCANVAS_DATA_ROOT 환경변수 아래의 모든 경로
  - FABCANVAS_DB_ROOT / FABCANVAS_BASE_ROOT / FABCANVAS_WAFER_MAP_ROOT
  - 사용자 데이터 기본 파일명 (users.csv, groups.json, admin_settings.json,
    settings.json, shares.json, informs.json, product_contacts.json,
    notes.json, source_config.json, dashboard_*.json, meetings.json,
    events.json, notices.json, tokens.json, issues.json)
"""
from __future__ import annotations

import base64
import gzip
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# v8.8.19: Windows cp949 기본 stdout 에서 em-dash/non-ASCII print 가 터지는 것을
# 방지 — UTF-8 reconfigure (Python 3.7+). 실패해도 조용히 무시.
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

VERSION = "8.8.23"
CODENAME = "flow"
VERSION_META = {"version": "8.8.23", "codename": "flow", "changelog": [{"version": "8.8.23", "date": "2026-04-22", "title": "SplitTable 오버라이드 컬럼 검색/필터 노출 · S3 신호등 화살표 자체 컬러 · 인폼 ML_TABLE CI 스냅샷 · 메일/이슈 그룹 = Admin 그룹 통합", "tag": "feat", "changes": ["**SplitTable 오버라이드 컬럼 검색/필터 노출** — `/api/splittable/schema` 가 이제 `_scan_product` 를 통해 post-join 스키마를 반환하고 `override_cols_present` 를 함께 내려 FE 가 '오버라이드 제공 컬럼' 을 인식. `My_SplitTable` 은 `_CUSTOM_HIDDEN_BASE` 기본 숨김 집합에서 오버라이드 성공 컬럼을 예외 처리(`_OVERRIDE_EXEMPT`) 하고 `allCols` 도 overrideCols/productSchema union 으로 보강 → `root_lot_id` / `lot_id` / `fab_lot_id` 가 검색 드롭다운·CUSTOM pool 양쪽에서 정상 노출. 기존 '조인 활성' 배지는 유지.", "**S3 신호등 디자인 변경** — `S3StatusLight` 의 원형 배경 제거. 화살표 자체가 신호등 색(초록/빨강/회색)을 갖고, 업=위 화살표 · 다운=아래 화살표. viewBox 22×22 · stroke 3 · round caps · drop-shadow filter 로 시각 비중은 기존 아이콘과 동일. red 상태는 기존 `s3blink` 애니메이션 그대로.", "**인폼 → 제품 ML_TABLE CI 매칭 통일** — `routers/informs._resolve_fab_lot_snapshot` 이 `fab_lot_id` / `root_lot_id` / `lot_id` / `wafer_id` 를 literal 비교하던 것을 CI 로 교체(`_ci_resolve` / `_ci_pick_first` 헬퍼 신설). ML_TABLE 이 대문자 컬럼명(`ROOT_LOT_ID` 등) 으로 찍혀도 저장 시 스냅샷이 정상 해결. v8.8.22 에서 SplitTable 쪽만 CI 적용됐던 누락 메꿈.", "**메일 그룹 · 이슈추적 그룹 = Admin 그룹 통합** — 단일 진실원 = `groups/groups.json`. `mail_groups.json` + `admin_settings.json:recipient_groups` 를 최초 `_load()` 호출 시 일회성 merge(이름 기준, `mail_groups.json` → `.json.migrated` rename). `/api/mail-groups/*` 와 `/api/informs/mail-groups` 는 groups.json 을 투영해 응답 — Admin '그룹' 탭에서 만든 그룹이 인폼 메일 수신 드롭다운 · 이슈추적 그룹 선택 · 회의 mail_group_ids 에 그대로 노출. groups 스키마에 `extra_emails` 필드 추가 · Admin GroupsPanel 에 외부 수신자 관리 UI 신설."]}, {"version": "8.8.22", "date": "2026-04-22", "title": "SplitTable/인폼 case-insensitive 매칭 · 메일 본문 SplitTable HTML 인라인 · 개별유저 picker 도메인 자동합성 · CUSTOM 15줄 확장 · S3 신호등 SVG 화살표 · Admin 권한 탭 재배치", "tag": "feat", "changes": ["**SplitTable CI 매칭 대수술** — (a) 제품 폴더 대소문자 무시: `ML_TABLE_PRODA` → `1.RAWDATA_DB/ProdA` / `proda` / `PRODA` 어느 쪽이든 매칭(`_find_ci_child` / `_find_ci_path`). (b) **join key CI 매칭**: ML_TABLE 이 `ROOT_LOT_ID`/`WAFER_ID`(대문자), hive 원천이 `root_lot_id`/`wafer_id`(소문자) 로 달라도 같은 컬럼으로 인식 → 그동안 나오던 **\"공통 join key 없음\"** 에러 박멸. fab_lf 컬럼을 main 쪽 casing 으로 rename(`_ci_align_fab_to_main`) → `_scan_product` / `_resolve_override_meta` 양쪽 동일 로직. fab_col / ts_col / override_cols 도 `_ci_resolve_in` / `_pick_first_present_ci` 로 전부 CI.", "**인폼 스냅샷/CUSTOM 에도 CI 적용** — 인폼이 `/api/splittable/view` 를 호출할 때 자동으로 혜택. 이제 대문자 ML_TABLE 컬럼명이라도 hive 원천의 `fab_lot_id` / `tkout_time` 등이 정상 조인되어 스냅샷/메일/엑셀 모두 일관된 최신값.", "**인폼 메일 본문에 SplitTable 인라인 HTML 테이블** — `_build_html_body` 에 `embed_table` 파라미터 추가 + `_render_embed_table_html` 신설. `st_view`(parameter×wafer 매트릭스 + wafer_fab_list) 와 legacy 2D 양쪽 모두 스타일링된 HTML `<table>` 로 렌더 → 메일 수신자가 xlsx 열지 않고도 본문에서 바로 값 확인. plan 값은 `→` 오렌지 강조, 최대 60행 렌더(초과 시 잘림 경고). `mail-preview` / `send-mail` 둘 다 `target.embed_table` 을 전달.", "**개별유저 picker '빈 리스트' 해결** — `/api/informs/recipients` 가 users.csv 의 email 필드가 비어있고 username 에 '@' 도 없는 일반 계정까지 도메인 자동합성(`<un>@<admin.mail.domain>`) 으로 `effective_email` 채움 → 사내 유저 이름만 저장된 환경에서도 picker 에 전원 노출. `admin`/`hol`/`test` 토큰/role 필터는 유지. 결과는 username 알파벳순 정렬.", "**CUSTOM 프리뷰 15줄 가시권** — `EmbedTableView` maxHeight 320 → 460 (약 18~22줄 수용) 으로 확장. CUSTOM 컬럼 다수 선택 시 스크롤 없이 한눈에 비교 가능. st_view + legacy 2D 두 경로 모두 일관 적용.", "**S3 신호등 화살표 SVG 전환** — 유니코드 `↓`/`↑` 텍스트 대신 흰색 stroke 2.5px SVG 화살표로 재구현(`S3StatusLight.ArrowSvg`). 18×18 원 안에 선명히 보이고 폰트/zoom/OS 의존성 없음. 다운(아래) / 업(위) 방향이 한눈에 식별.", "**Admin 권한 탭 재배치** — (a) `dashboard_chart` 열 제거 (페이지 위임 탭이 동일 역할). (b) `ALL_TABS` 를 실제 nav 순서로 정렬: filebrowser→dashboard→splittable→tracker→inform→meeting→calendar→tablemap→ml→devguide(맨 뒤). (c) `PAGE_IDS` 매트릭스도 같은 순서 + 향후 페이지(spc/ettime/wafer_map) → 공용(messages/groups) 순."]}, {"version": "8.8.21", "date": "2026-04-22", "title": "SplitTable root:~~ 제거 · 인폼 메일 mailSendString 래핑 + 자동 xlsx 첨부 + 실시간 미리보기 · psutil /proc 폴백 · Admin 페이지 권한 매트릭스 (유저×페이지) · S3 신호등 화살표 확대", "tag": "feat", "changes": ["**SplitTable fab_source `root:~~` 옵션 제거** — 제품 스코프를 넘어 데이터가 섞이던 footgun 제거. `_scan_fab_source` / `_scan_product` / `_resolve_override_meta` 모두 `root:` prefix 저장값을 무시하고 auto-derive 경로로 회귀. 저장 시점에 `_migrate_legacy_root_prefix` 로 기존 레거시 값도 청소. FE SplitTable fab_source 드롭다운에서 `[DB 루트]` 옵션 완전 제거, 첫 항목을 `(자동 매칭)` 로 교체. canonical layout: `/config/work/sharedworkspace/DB/1.RAWDATA_DB/<PROD>/`.", "**인폼 메일 API mailSendString 래핑** — 사내 메일 API 규약 수정. `core/mail.send_mail` + `routers/informs.send_mail` 둘 다 multipart `data` 필드에 `{\"mailSendString\": \"<json string>\"}` 로 한 번 더 감싸 POST. dry-run 응답에 `payload_wrapped` / `preview_data_wrapped` 추가로 FE/Admin 미리보기 검증 가능.", "**인폼 메일 다이얼로그 대수술** — (a) 개별 파일 첨부 UI 완전 제거. (b) 인폼 스냅샷 xlsx 자동 생성 + 첨부(`_build_inform_snapshot_xlsx` — 제품/lot/wafer/splittable_change/body 를 openpyxl 로 렌더). (c) 신규 `GET /api/informs/{id}/mail-preview` 엔드포인트 — 실제 발송될 HTML body + 제품담당자 라인 + 자동 첨부 목록 반환. FE MailDialog 가 body 입력에 debounced 바인딩 → 실시간 미리보기 패널에 최종 HTML + 수신자 + 자동 xlsx 크기 표시. (d) 유저 picker: BE 가 `admin`/`hol`/`test`/비-email 계정을 선제 필터 (`_is_blocked_contact`) → FE 는 `(no email)` 표시 제거하고 username 만 노출.", "**sysmon /proc + statvfs 폴백** — psutil 미설치 사내 서버도 CPU/Mem/Disk 측정되도록 `_read_proc_cpu_percent` (`/proc/stat` 2회 차), `_read_proc_meminfo` (`MemAvailable`/`MemTotal`), `_read_proc_disk` (`os.statvfs`) 폴백 추가. `_collect_stats` 가 psutil 없을 때 자동으로 이 경로로 떨어지며 `source: \"proc_fallback\"` 필드 포함. `/api/system/stats`, `/api/monitor/system` 응답 동일 포맷 유지.", "**Admin 페이지 권한 매트릭스 재설계** — 행=유저 / 열=페이지 매트릭스로 transpose. admin 역할 + `admin`/`hol` username 은 모든 페이지 자동 체크 + disabled 로 '수정 불가' 명시. 체크박스 토글 시 즉시 `/api/admin/page-admins` POST.", "**S3 신호등 화살표 확대** — 원 18px / 화살표 13px Arial bold + textShadow 강화. ↓다운 / ↑업 방향이 원 내부에서 확실히 보이게."]}, {"version": "8.8.19", "date": "2026-04-22", "title": "사내 공유 경로 자동 보존 · 인폼 담당자 admin/hol/test 필터 · SplitTable fab_source 진단 · CUSTOM set 양방향 공유 · 인폼 Lot 드롭다운 · 메일 도메인 자동 합성", "tag": "feat", "changes": ["**사내 공유 경로 자동 감지/보존** — `/config/work/sharedworkspace` 가 존재하면 환경변수 없이도 `holweb-data` / `DB` / `Base` 를 자동으로 기본 루트로 사용 (core/paths.py + core/roots.py). setup.py `_build_setup.py` 의 `_resolve_data_roots` + `_write` L6 가드가 이 경로를 자동 보호 → 재설치 시 사용자/그룹/회의/인폼/대시보드 등 데이터가 절대 덮어쓰이지 않음. 기존에는 `/config/work/holweb-fast-api` 가 함께 있어야만 인식돼 setup.py 재실행마다 로컬 `./data/holweb-data` 로 떨어져 DB 휘발.", "**인폼 제품 담당자 admin/hol/test 완전 제외** — `routers/informs._is_blocked_contact` 신설 (admin role + `admin`/`hol`/`test` 포함 username 전부 차단). 새 엔드포인트 `GET /api/informs/eligible-contacts` 추가. `bulk-add` 가 동일 필터 적용. FE `My_Inform.jsx` 일괄 추가 모달이 `/api/informs/eligible-contacts` 호출로 교체. 그룹 `_is_blocked_member` (admin 허용) 는 그대로 유지 — 담당자 필터만 더 엄격.", "**SplitTable fab_source_off 진단 강화** — `_resolve_override_meta` 가 `db_root` / `base_root` / `searched_db_roots` / `tried_candidates` 를 응답에 포함. 에러 메시지에 product → pro 추론 결과 + 실제 탐색 경로 + 권장 해결법을 상세 기술. FE 배지는 `title` 툴팁 + 클릭 시 `alert` 로 전체 상세 표시(db_root/base_root/DB 최상위 후보/탐색 경로 목록).", "**CUSTOM set 양방향 공유 (SplitTable ↔ 인폼)** — 이미 `/api/splittable/customs` 가 공용이었지만 v8.8.17 에서 인폼 UI 의 Saved CUSTOM 드롭다운이 제거돼 사실상 단방향이었음. 인폼 인라인 CUSTOM 편집기에 공용 set 드롭다운 + 저장(프롬프트 기반) 추가. set 선택 시 컬럼이 `embedCustomCols` 에 즉시 반영, 저장 시 SplitTable 의 `customs` API 에 기록되어 SplitTable 에서도 동일 이름으로 노출.", "**CUSTOM 선택 pool 기본 컬럼 제거** — SplitTable + 인폼 양쪽 모두 `product` / `root_lot_id` / `wafer_id` / `lot_id` / `fab_lot_id` 는 자동 첨부되는 기본 컬럼이라 CUSTOM 선택 UI 에서 숨김. 사용자는 분석 대상 parameter 에만 집중.", "**인폼 Lot 후보 = SplitTable override DB 기반** — `GET /api/splittable/lot-candidates` 에 `source=auto|override|mltable` 인자 추가, 기본값 `auto` 에서 ML_TABLE_ 제품이면 override fab_source (hive `1.RAWDATA_DB/<PROD>/`) 를 먼저 스캔. 인폼 Lot 드롭다운이 'DB 에 실제로 찍혀있는 최신 lot' 을 그대로 보여줌.", "**인폼 Lot 입력 = 스크롤 드롭다운** — 기존 datalist autocomplete 를 `<select size=1>` 로 교체. 제품 선택처럼 드롭다운을 열어 root_lot_id/fab_lot_id 목록 전체를 스크롤해서 선택. `✏ 직접` 토글로 수동 입력 모드 전환 가능.", "**Admin 메일 도메인 자동 합성** — admin_settings.mail 에 `domain` 필드 추가 (예: `company.co.kr`). `core.mail.resolve_usernames_to_emails` / `send_mail` 이 username 에 '@' 가 없으면 자동으로 `<username>@<domain>` 으로 조합해 발송. Admin UI 에 '메일 도메인' 필드 + preview JSON 도 domain 기반 샘플 표시.", "**기타** — `PATHS._ensure_dirs` 가 data_root 자체도 생성 보장(공유 경로 첫 실행 시). `/api/informs/eligible-contacts` 도 role 정보 포함 응답."]}, {"version": "8.8.18", "date": "2026-04-22", "title": "Admin 메일 API UI 간소화 · 메일 파일첨부 범용 업로드 · SplitTable 1.RAWDATA_DB exact match + Save Override feedback · psutil 시스템 모니터 + 유휴 부하 정책", "tag": "feat", "changes": ["**Admin 메일 API 설정 UI 재설계** — 수신자 그룹 관리 제거(수신자는 각 페이지에서 선택). URL / x-dep-ticket / senderMailAddress / statusCode 4필드 + 활성화 토글만 남김. 저장된 설정 기반 **전체 API 틀 JSON 미리보기** 블록(headers/data/files 구조) 추가. BE /api/admin/settings 가 `dep_ticket` 단일 필드 받으면 자동으로 headers[\"x-dep-ticket\"] 에 반영.", "**메일 다이얼로그 파일첨부 범용화** — 기존 인폼 이미지 외에 xlsx/pptx/pdf/doc 등 모든 파일 타입 선택 가능. FE 파일 input → `/api/informs/upload-attachment` 업로드 → URL 을 send-mail attachments 에 push. BE 엔드포인트 신설: 실행파일(.exe/.bat/.ps1 등) 차단, 10MB 개별 한도, mime 자동 추론.", "**SplitTable 1.RAWDATA_DB exact match** — `_RAWDATA_PREFIX` startswith 매칭을 `_RAWDATA_EXACT = \"1.RAWDATA_DB\"` equality 로 교체. `1.RAWDATA_DB_INLINE` / `1.RAWDATA_DB_FAB` 처럼 suffix 붙은 폴더는 자동 매칭에서 제외(별개 소스로 취급). 사용자가 `lot_overrides[product].fab_source` 에 명시 지정하면 여전히 존중.", "**Save Override 즉시 반영 + 피드백** — 저장 후 (1) `/source-config` 재로드로 저장된 값을 FE state 에 동기화, (2) `/ml-table-match` 재계산으로 override 메타 업데이트, (3) `loadView()` 로 테이블 행 갱신, (4) alert 로 성공/실패 명시적 피드백.", "**psutil 기반 시스템 모니터 (core/sysmon.py)** — 크로스플랫폼 CPU/Memory/Disk 5분 주기 수집(resource.jsonl, trim 8640 rows = 1개월). `/api/system/stats` 통합 엔드포인트 + `/api/monitor/system·history·state·heartbeat`. 기존 리눅스 전용 `/proc/stat` 로직 대체. requirements.txt + install_deps 에 psutil 추가.", "**유휴 자원 부하 정책** — 최근 6시간 동안 CPU/Memory 가 85% 이상 찍은 적이 없으면 5~10분(랜덤) 동안 numpy SVD 기반 더미 부하 생성. 사용자 활동(AuthMiddleware 에서 `/api/*` 인증 통과 시 `mark_user_activity()` 호출) 감지 시 `_load_stop` Event set → 부하 즉시 중단 + 30분 대기 창. `/api/monitor` + `/api/system` 자체 호출은 활동 감지에서 제외(위젯 폴링 노이즈 방지).", "**My_Monitor 페이지 개편** — 새 `/api/system/stats` 응답 기반. CPU/Mem/Disk 3개 게이지 + 각 지표의 **24h sparkline**(85% 빨간 선 dashed) + 유휴 부하 배너(진행/대기) + psutil 미설치 경고. 15초 auto-refresh.", "**보존 대상 확장** — setup.py `_PROTECTED_BASENAMES` 에 `farm_status.json / sysmon_state.json` 추가. resource.jsonl 은 v8.8.17 에서 이미 등록됨."]}, {"version": "8.8.17", "date": "2026-04-22", "title": "데이터 보존 재설계 (snapshot+verify+restore) · SplitTable db_root as rawdata · 인폼 CUSTOM only scope · FileBrowser 첫 클릭 head 200 · username=email 메일 · 사유별 메일 템플릿 · 공용 메일 헬퍼 · dep_ticket · 담당자 편집 간소화 · PPT 제거", "tag": "feat", "changes": ["**setup.py 데이터 보존 재설계** — 추출 직전 data_root 전체를 `~/.fabcanvas_backups/v8.8.17-<stamp>/` 로 자동 스냅샷(shutil.copytree). 추출 후 SHA-256 diff 로 검증하고 변조된 파일은 즉시 스냅샷에서 복구. `python setup.py restore [latest|<stamp>]` + `snapshots` + `snapshot` 수동 커맨드 추가. L0 화이트리스트 가드(backend/frontend/docs/scripts/app.py/README/CHANGELOG/VERSION.json/requirements.txt 외 top-level 쓰기 금지) 추가 → 코드만 교체. _PROTECTED_BASENAMES 에 paste_sets/prefix_config/history.jsonl/status.json/resource.jsonl/calendar.json/reformatter.json 추가.", "**SplitTable hive override 확장** — `_list_db_roots` 가 db_root 자체가 `1.RAWDATA_DB*` 일 때(Case1) + db_root 바로 아래에 parquet 제품 폴더만 있을 때(Case3) 를 모두 인식. `_auto_derive_fab_source` 도 db_root 자체가 매칭 루트일 때 제품명만 반환 → `_scan_fab_source` 의 `db_base/fab_source` 해석에서 prefix 중복 방지. 이제 사용자가 DB 루트를 `1.RAWDATA_DB` / `.../1.RAWDATA_DB_FAB` / 그 상위 폴더 어느 쪽으로 지정해도 ML_TABLE_<PROD> → hive 원천 자동 매칭 + 최신 lot_id 오버라이드가 동작.", "**My_Inform SplitTable scope CUSTOM only** — 등록 폼의 ALL/KNOB/MASK/INLINE/VM/FAB prefix chip + Saved CUSTOM 드롭다운 완전 제거. 인라인 CUSTOM 빌더만 노출(SplitTable CUSTOM UX 와 동일: 전체 체크·제거·pill·검색). view fetch 는 항상 prefix=ALL 로 받아 FE 에서 embedCustomCols 필터링, 미선택은 빈 프리뷰.", "**FileBrowser 첫 클릭 head 200** — meta_only 기본 off. `loadBaseFileView/loadHiveView/loadRootPqView` 모두 첫 클릭에서 polars lazy head(200) 으로 즉시 샘플 로드. SQL 적용 / 전체 컬럼 SELECT 만 전체 스캔. JSON/MD 파일은 원래대로 원문 반환.", "**인폼 메일 수신자 해석 — username = email** — `_resolve_users_to_emails` 에서 users.csv.email 비어있어도 username 이 `a@b.c` 포맷이면 그대로 발송 대상. admin/test 등 시스템 계정은 자동 제외. `/recipients` 응답에 `effective_email` 필드 추가 (FE 표시 편의).", "**사유별 메일 제목/본문 템플릿** — informs/config.json 에 `reason_templates: {\"<reason>\": {\"subject\":\"...\", \"body\":\"...\"}}` 스키마 추가. GET/POST `/api/informs/config` 에 reason_templates 필드 반영. My_Inform PageGear 안에 `ReasonTemplatesPanel` 컴포넌트 신설(사유 chip + subject/body textarea + 변수 `{product}{lot}{wafer}{module}{reason}` 참고). 등록 폼에서 사유 선택 시 본문 자동 채움(text 비어있으면 즉시, 아니면 confirm), 메일 발송 다이얼로그 초기 subject/body 도 템플릿 기반으로 변수 치환하여 prefill.", "**공용 메일 헬퍼 backend/core/mail.py** — `send_mail(sender_username, receiver_usernames, title, content, files=None, extra_emails=None, status_code=\"\")` 간단 인터페이스. admin_settings.mail 자동 참조, username→email 해석(users.csv 우선, username 자체 email 포맷 fallback, 나머지 skip), multipart/form-data 인코딩, dry-run 지원, 응답 dict 표준화(ok/status/to/skipped/reason). 인폼·회의 등 어떤 라우터에서도 1줄 호출.", "**메일 API dep_ticket 필드** — admin_settings.mail 에 `dep_ticket` 단일 필드 지원. POST `/api/admin/settings` 가 dep_ticket 을 받으면 자동으로 headers[\"x-dep-ticket\"] 에 반영 (기존 headers dict 직접 편집도 여전히 지원). senderMailAddress / senderMailaddress 두 키 병행 주입(구버전 호환).", "**인폼 제품 담당자 편집 간소화** — 이메일/전화/메모 필드 제거. 아이디(username=사내 email id) + 역할 2필드만 노출. 기존 저장된 email/phone/note 값은 BE 에서 그대로 보존.", "**문서 정리** — `docs/FabCanvas_flow_intro.pptx` · `scripts/make_pptx.js` 삭제 (repo 용량 정리)."]}, {"version": "8.8.16", "date": "2026-04-22", "title": "SplitTable hive override 다중컬럼 · FileBrowser meta_only 지연로딩 · 회원/S3 보존 재강화 · SplitTable/인폼 CUSTOM UX · 인폼 필터 strict · 회의 메일 본문 분리 · 대시보드 scatter fit toggle · 회의 담당자 placeholder", "tag": "feat", "changes": ["SplitTable 다중 컬럼 override + FileBrowser meta_only 지연 로딩 + 회원/S3 보존 재강화 + SplitTable/인폼 CUSTOM 대개편 + 인폼 필터 strict + 회의 메일 본문 분리 + 대시보드 scatter fit + 회의 담당자 placeholder + 담당자 즉시 반영. 상세 이력은 이전 VERSION.json 참조."]}]}


# v8.8.3 — 사용자 데이터 보존 whitelist (덮어쓰기 금지 파일명)
# v8.8.16 — users 관련 변형 / S3 sync 관련 / 기타 런타임 state 파일 추가.
_PROTECTED_BASENAMES = {
    # 회원/인증
    'users.csv', 'users.json', 'users_cache.json',
    'tokens.json', 'sessions.json', 'session_tokens.json',
    # 그룹/설정
    'groups.json', 'admin_settings.json', 'settings.json',
    'shares.json', 'informs.json', 'config.json', 'product_contacts.json',
    'mail_groups.json', 'mail_config.json',
    # SplitTable / Dashboard / 인폼 state
    'notes.json', 'source_config.json', 'dashboard_snapshots.json',
    'dashboard_charts.json', 'rulebook_schema.json',
    'paste_sets.json', 'prefix_config.json',
    # 회의/트래커/공지/이슈
    'meetings.json', 'events.json', 'notices.json', 'issues.json',
    'messages.json', 'inform_user_modules.json', 'page_admins.json',
    # S3 / 로그
    's3_ingest_config.json', 's3_sync.json', 'history.jsonl', 'status.json',
    'activity.jsonl', 'downloads.jsonl', 'resource.jsonl',
    # v8.8.17 — 캘린더/대시보드 명시적 추가 (holweb-data 보존원칙 강화)
    'calendar.json', 'reformatter.json',
    # v8.8.18 — 시스템 모니터 state (resource.jsonl 은 이미 위 등록).
    'farm_status.json', 'sysmon_state.json',
}

# v8.8.3 — 데이터 루트로 간주되는 세그먼트 (경로 어디에 있든 보호)
# v8.8.16 — s3_ingest / reformatter / notifications / cache 추가 보호.
_PROTECTED_SEGMENTS = {
    'holweb-data',    # 사내 운영 데이터 디렉토리
    'informs',        # 인폼 설정/카탈로그/담당자
    'groups',         # 그룹 정의
    'mail_groups',    # 메일 그룹
    'dbmap',          # TableMap 버전/아카이브
    'splittable',     # SplitTable 노트/설정
    'tracker',        # 이슈 트래커
    'calendar',       # 달력 이벤트
    'meetings',       # 회의/아젠다/액션아이템
    'messages',       # 쪽지/공지 스레드
    'sessions',       # 로그인 세션/토큰
    'uploads',        # 업로드 파일
    'logs',           # activity/download/resource/S3 sync 로그
    '_backups',       # 자동 백업
    '.trash',         # Base 파일 휴지통
    'Base',           # rulebook / parquet / 사용자 추가 CSV
    'DB',             # Hive-flat 원천 데이터
    'wafer_maps',     # wafer map JSON 라이브러리
    # v8.8.16 — 재배포 시 초기화되던 항목들.
    's3_ingest',      # 파일탐색기 S3 동기화 config/status/history
    'reformatter',    # 제품별 reformatter 룰
    'notifications',  # 사용자 알림 큐
    'cache',          # 런타임 캐시 (초기화해도 재생성되지만 덮어쓰지 말 것)
    'data',           # 전체 data 트리 — 어떤 경로 아래에 있든 덮어쓰기 금지 (defense-in-depth)
}


_ALLOWED_TOP_LEVEL = {
    'backend', 'frontend', 'docs', 'scripts',
    'app.py', 'README.md', 'CHANGELOG.md', 'VERSION.json', 'requirements.txt',
}


def _write(rel: str, gz_b64: str) -> None:
    # v8.8.3/v8.8.17: 사용자 데이터 보존 가드 — defense in depth.
    #
    # 원칙 (v8.8.17): setup.py 는 **코드만 교체하고 holweb-data/ 안의 어떤 파일도
    # 건드리지 않는다**. FILES dict 는 backend/ frontend/ docs/ app.py 등 소스만 담아야 함.
    # 6개 레이어로 검증 (하나라도 match 하면 쓰기 skip):
    #   L0) top-level 세그먼트가 _ALLOWED_TOP_LEVEL 에 없으면 화이트리스트 위반 → skip
    #   L1) 경로 prefix 가 data/ 또는 holweb-data/ 이면 skip
    #   L2) 경로 세그먼트에 _PROTECTED_SEGMENTS 가 하나라도 있으면 skip
    #   L3) 파일명이 _PROTECTED_BASENAMES 에 있으면 skip
    #   L4) resolve() 한 절대 경로가 ./data 또는 ./data/holweb-data 아래면 skip
    #   L5) HOL_DATA_ROOT / FABCANVAS_{DATA,DB,BASE,WAFER_MAP}_ROOT 아래면 skip
    rel_posix = rel.replace("\\", "/").lstrip("./")
    parts = [p for p in rel_posix.split("/") if p]

    # L0: 화이트리스트 — 허용 루트가 아니면 설치 대상 아님 (보수적 기본값).
    if parts and parts[0] not in _ALLOWED_TOP_LEVEL:
        return

    # L1
    for guard in ("data/", "holweb-data/"):
        if rel_posix.startswith(guard) or rel_posix.rstrip("/") == guard.rstrip("/"):
            return

    # L2
    for seg in parts:
        if seg in _PROTECTED_SEGMENTS:
            return

    # L3
    if parts and parts[-1].lower() in _PROTECTED_BASENAMES:
        return

    data = gzip.decompress(base64.b64decode(gz_b64))
    dst = ROOT / rel

    # L4
    try:
        dst_abs = dst.resolve()
        for data_sub in ("data", "data/holweb-data", "data/Base", "data/DB"):
            try:
                dst_abs.relative_to((ROOT / data_sub).resolve())
                return
            except Exception:
                pass
    except Exception:
        pass

    # L5
    for env_key in ("HOL_DATA_ROOT", "FABCANVAS_DATA_ROOT",
                    "FABCANVAS_DB_ROOT", "FABCANVAS_BASE_ROOT",
                    "FABCANVAS_WAFER_MAP_ROOT"):
        env_val = os.environ.get(env_key)
        if env_val:
            try:
                root_resolved = Path(env_val).resolve()
                if str(dst.resolve()).startswith(str(root_resolved)):
                    return
            except Exception:
                pass

    # L6 (v8.8.19): 사내 공유 경로 `/config/work/sharedworkspace/{holweb-data,DB,Base}`
    #   환경변수 없이도 절대 덮어쓰지 않는다 — setup.py 가 공유 데이터 휘발시키는
    #   사고 방지. 해당 경로가 실제 존재하지 않으면 아무 효과 없음 (개발 PC 무해).
    try:
        dst_abs = dst.resolve()
        for _shared_sub in ("/config/work/sharedworkspace/holweb-data",
                            "/config/work/sharedworkspace/DB",
                            "/config/work/sharedworkspace/Base"):
            if str(dst_abs).startswith(_shared_sub):
                return
    except Exception:
        pass

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)


FILES = {
    'app.py': (
        'H4sIAAAAAAAC/3VVwW7bRhC9C9A/DFggoQqJdnsoDAE5pI6KFkWNIijQQ1BQtLSKiVBcgqQd'
        '66a4airXAiLHdqq4lKsAbm0HQqvYgusA7s/4qF39Q2d3SVmyUwoiubuzs2/mvRlqmvYd9XIO'
        'WSMOrK7ZJeq7QNzQr3nUdkMIVuwqXNV3YW3B+MxYN9KpdIq9PeG9Z3l4WLj/4JvCHN/o8/0T'
        'ftAeDerAto55MxofNPHtkHdbwN7+xHrveXTJ3kTAtw7HrzbHez3gzQ7wg+botAXjvQhtpWfA'
        'KwFheV4e/5DLrdAghHlD/nDoUT+EhfmFebFj2So9IW55DtjGkJ3X+a9tcQjvRWK/4dVAgBIH'
        'nfXAp6sh8QM5xV68xhhiWHZV+mTtzuhiAGyvxfoX6CmbTsUrPGrw9x39KfWf2O5jKNs+KYXU'
        'r2XEeWx3wLqXUFykq04ZXBom/qq0vOoQuItI7haBddrsl910qrhEkxXXqpIy3I1xoQ26Y7/3'
        'Jb5BxH/sTmVKpAh4d6gY4d16XiXsEzU7brV491K8KayYVAS2y3qb4+c99mdfv/eFtbxouWtW'
        'YFj2XAb46zqM/rnA7HdwB1zn8Y9LCGqB4VnhCvC9A4SkeAF+vovIFFtw9fwl8FdDPII3Ihh3'
        'Lvh5hK7YTg+KcTzGx8UkE5KEvSbvNYSNJBvgUyM51RA8824HivhSROsuPx0KIAiQteqCIp/k'
        'yLr0NSUXDJf1flYa6rCTYV64zUGpDNPBwp07t0QFV9GOMvZq4Qp1IVe9bTO50Bj0GZcioNLT'
        'ckb5GJ1djt4NYqlD8baC8Z5D0STxohheNKaCE2I82AY9IWFGum3MxbVUlWkGw9c0baJPGkxe'
        'kbt0quLTKggGHXs54eBbHIqkmV8WHhbgnhzrplmxHWKaGcMnAXXWiJ5B5n1sAGj4+f3FrwtL'
        'D9BW7ZkDLUaoCUcfJeHMCO0D+gG23UatTAsjC0VMERESEVFOFeB0Q6hQHzywXdCD0NcTPJks'
        'yKHAlMnklTrtClrK2nMnx8dL4kqmDNsNiB/q81nwMiqI728WtSwN1h+Ohih2CegGLyJEhdgQ'
        'Dvj+gDdQ9jLp04qOo5Ka5lsRcp6VHbB1LIZCP8DO6qPTf2H8WwMqluOI3egTu2+M/TrHIk24'
        'c7xxyI/qqp4abKudtAchj7+lmHSXgmnarh2aJqLFSj96xnvbqM5jtoNt+U3EdiIjzppCqB6o'
        'FWM1tB21FHikhMzPLhli1hSBKt04tGSFNnV1zaxYyyVZHmaM2MS4tSxMVITqUfnTMhPK5CF2'
        'AEvUJYBki7HhUKtM/GR6ikTfsgMCX0lEBd+nvq5hRQrSJQ5ykyfhLTnMxJZ7OxzVh1VAwloX'
        't3iHkIxaDx79X3g/iNpAo+uUxegNso6JUtt18YydClmoLUIh6RRZLxEvhIJ8YCLBCoBMuGdH'
        'TSSbHW0KlvHLmleaQflNKk98VDt/jS6GwF4eohD6SmigJ03ouvlg62A772C8f4z9KsZDA6O0'
        'gouzBTaVgRtF8yEzqftZvQvwYc0jebAfu1jnsu+YKHDTxPAfaTJ36dR/TAmWQX0IAAA='
    ),
    'backend/app.py': (
        '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'
        'NYk8orplCEvTG5Oec8poBNT/Af8LzhN+HgAA'
    ),
    'backend/requirements.txt': (
        'H4sIAAAAAAAC/x2MSw7CMAxE95V6FKyEn8QiuQhiYVEQkdLYcpxCbo/T5Zt5M2+sipxicOC9'
        'm6e2pSdJuVtaFpTlMZrjbZ6YMkrdyTTuKELfGPwZDIlfhfsvx3ACb21tmgwuMIa942pwhX2n'
        'HyqHtWVNjKLjzw3rDxLQ6COIAAAA'
    ),
    'CHANGELOG.md': (
        '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'
        'k/f29nL7YORy15VWu/UHNq2A/zc7rqT+J9gAAA=='
    ),
    'frontend/index.html': (
        'H4sIAAAAAAAC/01STU/jMBC9r8R/MOaAtJC4e0BC3SSXpb3CAQ4cXXuSzOKPyDNN20X893WS'
        'CvViz7w8v/c8cXX99Pzn9f1lI3r2rrn6UU27cDp0tfyIckZA26bywFqYXicCruXb67Z4lGc0'
        'aA+1HBEOQ0wshYmBIWTWAS33tYURDRRzc48BGbUryGgH9a9yNVswsoOmdfFQqaXOoMPwIRK4'
        'WmJWlIJPQ7ZBrztQNHZ3R++k6BO0tVStHidSmfFZ8CIX9+ChMNHFdBHtZrt92KwW99loEeqZ'
        'B1orZWwo/5IFh2MqA7DqehUTxmC04T2pIUHWsTpZZZFYHWCniDWjufhUGiK53ID45IB6AJ4d'
        '57b5+el16jCsV78HbS2GLle7eCwI/03NLiYLqcjI1y7a02ebwxet9uhO69uXb5vbe9KBCoKE'
        '7VelFu1sopYfNx1tKoujQFvLFGOOUOXUY1ORSTjwebA+2r0DKSiZPNC8Kq9xmsJx4i/UXCxy'
        '6vxc/gPzBSICQAIAAA=='
    ),
    'frontend/package.json': (
        'H4sIAAAAAAAC/22PUQ+CIBSF3938D8znpCzbXE899DvaVG5FQ2CAuOb67wGi2dbGy/3Ouedc'
        'xjRBKON1B9kJZQ/BBmjymxLcACfZJqhSUVsbbzCqh4lZUJoK7pcqvMe7aDUvGYI6QXoGEepW'
        'UWm046OfHSFgvctSEz2ONT1lZKZommZNKrAUhkWdZy+/pxIC0l0MvKWwblJQt8bvXYsKH3Cx'
        'RAYhJ6Jbiz959vI/8uxPeOqtZP2d8vzbULqMcikIn/P4iEtcxOw0ce8DgXueMnQBAAA='
    ),
    'frontend/vite.config.js': (
        'H4sIAAAAAAAC/3WPwQqDMBBE74L/sLco2MZjSS+FfkbpIdioKzEbkigW8d+baC899LbsvBlm'
        'cLTkAqzwUi0adSfTYgcbtI5GYDMGxa55hgfllGzCV7olbfDc6qlDc9qlhKplR2OcnHT4iS3W'
        'PAM4DF7AY/cU5bNKb6/crJyITayj5Z0OxqVFJoD1IVjBuaZG6p58EJf6UrMqAtHikcx58GT+'
        'k3HPlmdbGet9ADnnMtXwAAAA'
    ),
    'README.md': (
        'H4sIAAAAAAAC/5VZa1MbV5r+7ir/h7NFVUpc1M3F2fW4sqkCGxzvCMMa7CSf1C2pER1L3Zru'
        'FjaTpErYwoUNCTAWtnAEkWfwBQ+ulQEHsePUVvFT+Kjurp2fsM97TrckwNnZtY3py+n3vNfn'
        'vZwONpUx75w/d/7c56yra0RNsJTqqEw11Mysoydt1s1yGdVgMzZTk05ezTDvl5JXnWN+acnd'
        '/Ku//EHq6qKP3VrZXS56u/tsCkQi/qNDf3OBNWoVt1aRc3rGdJi7+LqxV+xkjb33XnWNuT/U'
        'vI19v1hzX3xgtm6kM1o0b2sWf/Foy5/b8R+8Y/56ubH7q/d0xStWmH9/xbv/8OjA/aXoFTeO'
        'DohSpeqvlRv1GvMqRX+twryfDpm39k4imfxy0dvcYe5u0asWL0HAmYvSRal/oKuLRZJmSjPU'
        'rMYU0oDSSeuj0Sj96uhgI5rq5C3NptvvmP9jGax6rwrsO+YVt9w38+w7PMdy/kNL/v7zchUb'
        '+OsLIP5dsOXRQXJaNdJaxkwfHVw2DQc6ZENaJiNYBz23VnOLCywg8fgekVha8jY+BLJCME7v'
        'ypA8pNoay6nWH/KaA2oTt4IlEpv49xjz14pQpcS8v+24P39gOcv8Rks6umkw7+VDbxF6qc55'
        'z7axeIB5T1bd2rb/5EXk+MFj+fjBaifzFqt+ue4+fidhK+b+5bX/qO5tlMlmxAzt5j4qMc6D'
        '+7zCYAH3RVliMJG7uBA5frYOGn9eginwhdSU5xHkcZcK2N/d23cfV4RultfJg7wabcIidlJ1'
        'HM2SM7qhyQnVgrdo8rRuO2baUrNywrwrQ2rNMWXH0rSsmpPvqFOaFaerpJlNmPI0jEV3jprI'
        'aHLSMm07yq87JRYbHplk/zZ27TpzofGFMvMeLMG7WOQbE/ulQn3Z+akp/S7Wd3V9JX/NbE21'
        'YDqQYCnLzKXMOwYiwdtcgbsxUPEWN7q6JDaiOw4clxHrLJLS0qwvegELbxztgtTNiZgcw8+k'
        'aqU1Rx7NZxyd2TktydfbTS09vfff9WVsTE6PqHr5X8yfhwvsu/US11dsbDL+5QgCb405lmrY'
        'OdMG4+4BrH0qNlP61JREARhHvMX1FGsc1N2/HDL/6Zb7Q8l/Al9xDxeYN19gqUQmmszoydtM'
        'S+mOxL6KTXzF3Hv7ZKd7+xRPEVrm7q36a9tHB1xNO8xfKrnVBZgNT6CMp4i66ppb3Tk64Jx4'
        'f/ngr28T++7qivtyB0r4/fWxIXZ0wK5dj127PkxXt0bjcE8N6gb1IuKp4taX3I0P7u5rFuEO'
        'llWd5DTpNWnPdLacaZGC41Hd3Sx7f9vimqGgfASU2V2Ch/lLK1D9VdVwII53uA8uGntVcMF3'
        'uf/Quz8HdzAd+UtyHwRPpXE436ReekrUQ727r94HYcca+wWgFtfkZtkvFaWWdYALde/VasC1'
        'CBoeKl55rvF+B7opeJsvIEFKt6Gf2TjHG7ADpJQCiCD64BHafl9BpLCBXgjhPt9ya+9w0ait'
        'ePd2yOx6hjvnDS2jiqjeXEEcMffVlnf4luMZeIjqhq0Ztu7oM1ro2Y33K+ARRuwkwnw5UES3'
        'GFSc4wBN6qlWGu9qUhNmAgzyNov+8g6kaqrpFYHcaIwJBOY6mlQT1y7HmMy+ujpkmraDq5ie'
        'nnauDo0yMtfLHZDG3hNfDI7DVLXG3gcpzAFQlPf0bQBeLDIxeU3+EvAoj1+Wjwsv22y/QfGx'
        'UUe+IfiBMfjWHAg4RlUgg3iNKIKLAmug4Tev3ccLMjTIw/Zwm2IYlvuPOkGvt/va/aWAR4T/'
        'cJA5eASxmcvoziQPffg36bibMhIIwkexB7dKsV0nTx+T6zyDxsrCmJw38YBQDngR6cNvuZ/+'
        'I7mIjzVYv+oubuFaLHWfb5CFoSFoBjz7a29lb+25N1+mtciU8xXsPId/pJB5iL3jVl+E0ufy'
        '9jRBEuXhvfcUiYjhyB1Nu52Z7TwZD0ykzZZy5wml9wqUZ6srAV0RXz+VWNoCjnSzybErg19j'
        '/zXvOUHDZtl9CVPUtqFJLk47fRFspDcgPQJTRMmMZtnkue7Gr/QB13ckpxkpRLqsG3GkrDQy'
        'ri2nTIOQO1TgSRXI7fphWU0jAI4DTpoRsbwOyf31UiDfceWhgNdR0FbTms0l67vUh4Khighl'
        'xw9KTE1ldUJ48VvkZijtWQ1sQsE8Z5+l/GxdUBZWx/5CaXC3Z9u4g/WA5h/kxi/Ii2CcY7Ec'
        'umlxC2LI7naJAm1w/JoIPKCHPMiZiJlpGe7hPduHw8P7ZPLmxQXEidysnALMga7uv6UwHrYs'
        '0xoy80ZKtWZRhM3tEFgi+Td2twUch5sE0EU4f1woMdvRcvEm7MrstmEm4rkcLC9DPt2ghNX+'
        'fibbvCOnCx3pCSmDl3wQH85SoGgLsj4s6N/b8ud/8HbLstCRu4sqcKeODMBJUOHVAWQp4EMG'
        'ztxHL1CSgWRYOflPF5AqQI1YbhkHFiYw9R8BV87YlAxNe8CTTpoX/rLqFQlk3dpP3kYhWOWv'
        'vRabnijQgj0NdYbkgFKZAnH/zEYpQq6QBRHWCgNYuq8OetgAPZmcjAlK4/C6qygoWgUSyqb3'
        'WBJKgiwAA3Jn517RXlWRLlrfIWki9SPMsEoQ5+BFCXSzIjRNFMfyzpTusN/19orqVgJr98kY'
        'YFBiSsfIyKfDvb0K4BdYuURiduNpn9b/u4GEAgCg4hvuTigX7KLO6EnTAHlFDq4leyat8N2U'
        'KdAnVN6ritWCgMzxcp9yIgfiCjUK9Nor1hHR7MI001NAWTXvmAwVspl3QoWgJEByrD9nmjFl'
        'WkktqxkOWBwZRjtQoSAnv9rdItL2rJEMyAbxX3vnPZ0PKCnUzcQt03QUEjEB7w/uBEBSHfpH'
        'pMEIudtenfVfmEaQBEU2ZUVvvd5DzNlkW/iGdLJNmIBf3uaPwMAQrgFnXV2X2IhqO4hpKnfG'
        'zYxq2XSVm1URn3fo0gTu5WbvZug6T+q0DL7Czjt6RqydVbPBlTNtGtEsVY9gzBF7jViIiWCz'
        'GxoFR99FXlzpjka/vYUKFbsTt64262yIQvkOZa3loAJNkHiwWmdgsxBTiCJpTUbtE/kChUSU'
        'dtWp6IDrBaohyOeLOJ5Egi4hqBpGY/HJwaHYcKvesPIZLWGat/8h1vyvSHOqRbuqieIbNrAc'
        'LRXiRx9AYKPsPd9HcJ8/pyhk9Gm8YuNckZyF0DxoboV1upvG6W7Zpju0R3doju6PWCMH90HZ'
        '5ajIEFGLJYQTyJb2h7xucc+1Jeeuw9ljodnOn0um2FRwwz75hBm5bEiFM33+XMQtr1DLpYgt'
        '0ZI4+ZyUm0UoU5e7VaQ2jIrw3f0o9eS/oCnbRd2zFfTKASMspeVIvuZmibyeQfeAnpuicnWF'
        'Y85hWeoMFdiPdFIC9SqP3MMlRG6bHj/Ct5U3BNWAcyVcIKPudWTE2v0NGEMSoVkCvaYBCN1o'
        'EeeH0jr1hYj+w3UpZGcA9gSePHmIdp7YqnjVDc4ZT30RZURNXFaNGdWWVF1WOoX0l9o4DuNL'
        'zeUu4YdFo9NUpfZK/C9ucyYC4mLvxd6A/0DxABP0wzRc2F1qpxfYI5pl/2/Kxw/+xJRpx8ld'
        'kuWMmVQztP4SLYCSqqvegx8kMYppNeAKCHOrE0uhc4lnQmkc9bzKB96V80qBcgUW29N6Fq/n'
        'kUkAyID9aBTfRVN600kVMcUpozJBrfuAQHW5yM2A6o0QcXkdfUP4fYRaGvfNvFs9ZMpZ0c/Q'
        'JlpU8ywVOGtgcnNVeFkAtjwbX4JCzAwCHf/39Q9c+PSflNDyF+AxP5RQrbdGRYwmCfCfiFes'
        '+vc3Os+ahaNSPK0Z8bSq9hukpuYf+LaRZVcHB9l11TDtaRSP0MCvKEgCFz1FBdWoY+WTNAhq'
        'p9PBOCxOoRNjgeK9zR0UVM3P7aSl5xxbzuppS3U03o0jkQHIAjodjLKQ3Napi9IBgLkASUlt'
        'VbpAnjxDlOMlfZUyoT+0uyFvHS3k5V031kTDNeg693nNcIoYukVIF6fBQRzFBZ95cXodfJhA'
        '+kYXhFqN2dQXsXBN4NDteDyuOtM2+4ShWpvS0yIpKkEmkUWlcDqdwPifjY5duRkb/lz+jE9s'
        'PpfRBqSg83/9bPxzmvs40S7p2yDr9CTtme+VFl1KPgHlj2eg+PiNsStdPc3808N4Yzeq5qgG'
        'bhGC793RElF+HdD7uUYV02YxLNRF0SJ7/7lC9XQkDQnShmlpKZ4/xZQSPRQlXROdDpomDdKN'
        'DA5dHrx+a3AifmUofmNsbFLpaX84NDgx/JHHXw6ODN+Ijw6Oi3enS47AJemeG+EEAgJkSpXj'
        'UgH/2K3hGxPXxq5L39iwePufjnD40M2aA0q0C+sLzF+tQL7Odioh6NCze/i49SaAIXaKdgju'
        'SH3WbM7UDefspwAPTWZnPs1xH6KiCHmXX6A6nGbTWianiSLKHohTyUeXhunoU7N0BS9XdaO1'
        'SynYxUJNie/kU7uINiAkjt9JGJ5UQKQAJglTtVL8JpGFq+CCSo2sFm7wm386aO2UDmdDKUGj'
        'bNxmg7aTX/OKLmsaumPyl+hbOSyAy/8bcYiPmge4xG9AGnEUV3M6v6UI5VFEd45FdrPaLdnM'
        'y2ftoRsp7a407WQzZ9/l8omMnmyv+89q2raScrsEra8H4STf2Hfb5aA2ChyGbV0i7zgmt8d1'
        'WDSpDamGIXR3op/9OPkc6VY+oabR2fgXZpYrAZfoo4WxcT0C0wy1TIMnV0Jzn6R+7zcNgG/a'
        'JkTiQRNUgluh+eBueHLylOv8A/KjseBiMHRT4lObuZoHpoRrhFOFd8KhPq6gpJnNAWtRhsrh'
        'PkOWaqSgF5O+H7xjj6uGxj0zZqo0jeFkzZSaEVYCF+kJ0zRO0g8tj04C9YgOG7e7WphfPiLl'
        'mWR+9LG0JyIpTDYtwikz+RGqnPDgjctfXJscvjx588awlOUBfPXmtSt0TRSaoKBRWWbLH0lh'
        't4IRFT99II87f66ri46M+gZYpL+3/5+jvRei/X2dQXfpb9A0yN3eZ0kLfkT8RvK2Njw1pSWd'
        '7nELmkN39Gljb6+zJxxP3vyKoSJq1GgqFhHzS29rlY/s+Az26IAKQWSRnI6GInx2Qcw3grnB'
        '0UEWOTKjoclQCdrpQKK6dnTgLr5v1As0WkMlCUKo56ovmPcekF49frB6dDCcTaCHy2hpNTkr'
        '5sMoOuaODqgYiQdHdO7yPNNoHXhGU83nKXxEHExf+UyViZlWV1ekOdxAq8envW/pdOUm9VaK'
        'TMd5UcGrrbAh3hG+2XafbwQTX75Fc/pJgxne3PPBDGgr8DwAuC0lzNQsUI4PC+8qTFxFTSMz'
        'y7cJlsniucJPaogEDaG8e2+hRGwUjBNRBzX2C/79t4FusfjvPz/+sXmUUCt41bpX3kKN2hM+'
        'PDnc5Mom5xJjYJoElX7kzfSbbbLBvrt4iJ2h//bPhMDEW3MFZ+n+wyDn9pw6KmPudsFfrLGv'
        'SRw8fLnjvVxoHWV0oaxpA6LmkQsXsLYWGRlGiTOBb/nBDnK9N1/oJK7p3AP1fspS78D30hkz'
        'oYZ+RiyEWAYXDw6PqAavl/y5fdgWDYy7vQVnDApm+YqW0RwNsj6riRWBwmWal4vOgiaNm/MR'
        'aqfezIenEv7GcyiS27+xVwTP7IRJrOYxh1gezBGCSTIURuJfGWJerU7zaD4a3SHjwXup1aTI'
        'vShdkD49EbY8aE8OE3uEfprnWrI48JLF6ZY43GqrGkXZJOa9J/XPJ3bN0q+nvXoOcayHjQyF'
        'C+n8lEiIKaMYMvawIMWG3F8QpejmctgdRChQ2afkO3J4zMeUb+np99/Gvpe+Nb5XOk/w9Vtn'
        'gTS/IQ7JaHxM5z2pkzMHJzHdp84HoexmXcRHPsEpqjgh6WEnp5DNYaAQY4CLwZeMw6xU8YiR'
        'UaUoprxt5+vebtVfoyOXsbFRllH/OItMotJkvtna5PQZ0+kMafceFx73t+jTaZ/gorTNIu2F'
        'MaPem9b0BNy1JqKo3tdg6YDmgHRXDCHBECrkL8Zi8S+Hh/jnJ+gBiuEo9A3hbbFOh6fe07c0'
        'SVbaC2+FnBT7nZocwg1nkJTh47ZolQCnjfdvvSf7lCIiKJFkRAQ/H4ATvSrwNuNfooQXl4dj'
        'sfjlsdjYjYmW46IlsTTNkGe1DISUTYvKejmRyWtyLm/lkCwcTc3IOd24LeKgzUmoW5RHByd+'
        'z0+Gm3khAA/a+MRh76Xm8e2f4AELf+Wz/nrN2yqwiEK04n39Um98cOJWXJIkhXNO5xpnjpgv'
        'sZuTI9GLbAi2Bo7TKXsGFYiWiquOQlilUAKhQ1NxF6YpeLN40PJuBR61J0456cW4auErVNUK'
        'TUIDnz9pgBgqTcPmXdS4pc+gTafB4T4jdSLaHpXoIAd8ysjVyH3UAjd2C97LDUphXnUO9vwf'
        '63RWfT0jAAA='
    ),
    'scripts/ensure_diff_fixtures.py': (
        'H4sIAAAAAAAC/31VbU8bRxD+bsn/YXpRJB/yGwYCsepKBtwElWIL3H5B6HSx7+Ck89317gyt'
        'EBJJ3QoFqiKKG1PZqVFJSxFS3dRKQCXqf8lH7/o/dHbvbGxT1V98OzP7zDPPzO7e+yBWduzY'
        'E82IKcYmWF+5G6YxEQwIgrA5E52MTsL73WMoaqoaKZlFBeh+nb5oAf3pdyDftWij3a20oHv8'
        'jj4/o69r0WAgGOi0noJtmq6kmy7QF4dADna71TaUNEMrlUswAZ1WHXAr/fopbdSge3BAGjfk'
        'jyuE6rR2YUtWFRtjKmzv2Bi5+KZ7Uu1Wa53XB0D2z8irq7ExluWINioY9iP5vkJOG/DpopRP'
        'zy5mpNxydn4sasn2F2XFBfLqBshfbdqsRsHSZQM2HZALblnWeVVA/jkEWq2Q/T0kEQzw5BHH'
        '0jWX1Uqe1civl7R2BqFO64Q2dnllQJ61kRyt1OGTpews1nJD3uwy7okIq26PvKyR0zqQN5XO'
        'VUtkHIIB+vKyW6t0q3WswdPpbZ1enySDAYAIMNF8vXqw0qN0HqvJLcxDzDNk8nOPhwxzn897'
        'a1/Tvy/JzzfIkmGCL2RiCl1MBYx5e0V+uYbQTGwm9lAEWr/hLLHGvSZgWr7iCiPcEXSrTbJ/'
        'De+/PfKlujgnP9R9huSyTU9bnT/buP+cNg6iXh2c1kouPZdZHmK6uJB/nB2ypJeWMulFaTkz'
        't5DL9Iwr81Imt+AbvSrI82M+LM0KEyhSNHGOZAOVqjfpb7sQwulC8VFq4B06uOXrM6XNOj25'
        'Ioc1jnTxzp9hMcqnPBhQbbMEkqSW3bKtSBJoJcu0XZANw3RlVzMNJxjwbbZsYH5/iyW7G7r2'
        'pBefw2U/0DJ12cZBc3DmWK9XMpl5SEEinngQn0zEg4HlbDaPBrYphLk1HTOLUVtxTH1TCYls'
        'fBXD9f+Cgdn0SgbD+a4YCEXZlQX2MSs7isAT5FBhaS67uCKlMXBVGJ4gIQzC8Aj1Lb0ZEtaC'
        'gUfL2c9yGVynFzP5PMu47TVhFC6JGdiHZ4rHEwxu0DA1apgW1sKDWLdE+ljcFI9P9Lf6hslR'
        'w4MRrH4JSehhMZMHBYOG8VHDFIfaYRIWFRXWbbNsSappS/zwhLa0YhI0ww17HocvUJYJESIf'
        'se+kx+MeHjP/vLHxuz1q45GZMDyMjE+HYXwmkpgSvXhNBYSGD1MwkwRbwckzID7qGp/u+8Y9'
        'n79K9OhatllQHCekWkk+SmKyj4HDC6oVVb7UHNcJiT0kz19UsQZLx3mTi5J/UyKIT842txz0'
        'F9Woa0pFrcD2ex4UBmwsm4f4ufhNg4RTYEfXEUbgMkhaURBvA/yiNAeWTENJQsE0XM0oK7cR'
        '6wgwqj7qyzogDgAxBgVTZxwGR36Ai5+OBTENGNn/yud1zSjrev8m4c+DqziuyB89Wn2HB/Y+'
        'kOsKrZ/fSWCvYoq1/yuJ/Sx8Z1Iwcq64TrhbvAOK4ckecoqtVtfhPuiKEcJvcc2Lx8bN4wXw'
        'sS2XlBDrRBicwoZSklPYMu9LjG7Zmqvc7a1lM1FVAWAbp8NAhJ0kbLMEDEjcAR+PP4BIw0lt'
        'D+q8w5rqzV5J1oxQb+BYW1SDiR0Shh7idO8hZmd4yDPb94gD3etNNL/wYojJ86EyksTI4vWc'
        'SoEgSSy7JAn+To9LMPAv3oSXvc4IAAA='
    ),
    'scripts/knob_lot_dominant.py': (
        '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'
        'VKuBZlkEwbK0TD0FVC79C0NEoKzuCgAA'
    ),
    'scripts/migrate_lot_format.py': (
        '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'
        'f0iqwqzX/gf3dl7DSBIAAA=='
    ),
    'scripts/seed_knob_meta.py': (
        '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'
    ),
    'scripts/seed_power_dashboard.py': (
        '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'
        '8a6/7390KA4UoRsAAA=='
    ),
    'scripts/seed_v73_matching.py': (
        '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'
        'AA=='
    ),
    'VERSION.json': (
        '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'
    ),
}


# ── v8.8.17: 데이터 보존 — 스냅샷 + 검증 + 복구 ────────────────────────────
import hashlib as _hashlib
import shutil as _shutil
import time as _time
from datetime import datetime as _dt


def _resolve_data_roots() -> list:
    """보호 대상 루트 디렉토리 목록 (존재하는 것만). HOL_DATA_ROOT /
    FABCANVAS_DATA_ROOT 환경변수가 있으면 그쪽을, 없으면 ROOT/data 전체.

    v8.8.19: `/config/work/sharedworkspace` 존재 시 사내 공유 경로를 자동 보호
      (holweb-data + DB + Base). 환경변수 없어도 setup.py 가 사용자 데이터를
      절대 덮어쓰지 않도록 보장.
    """
    roots = []
    for env_key in ("HOL_DATA_ROOT", "FABCANVAS_DATA_ROOT"):
        v = os.environ.get(env_key)
        if v:
            p = Path(v).resolve()
            if p.is_dir() and p not in roots:
                roots.append(p)
    # v8.8.19: 사내 공유 경로 자동 보호.
    _shared = Path("/config/work/sharedworkspace")
    if _shared.is_dir():
        for sub in ("holweb-data", "DB", "Base"):
            p = (_shared / sub).resolve()
            if p.is_dir() and p not in roots:
                roots.append(p)
    for sub in ("data", "data/holweb-data", "data/Base", "data/DB", "data/Fab"):
        p = (ROOT / sub).resolve()
        if p.is_dir() and p not in roots:
            roots.append(p)
    # dedupe — drop any path that is a descendant of another root.
    uniq = []
    for p in sorted(roots, key=lambda x: len(str(x))):
        if not any(str(p).startswith(str(u) + os.sep) for u in uniq):
            uniq.append(p)
    return uniq


def _backups_dir() -> Path:
    """외부 백업 디렉토리 — ~/.fabcanvas_backups/ (repo 외부)."""
    home = Path(os.path.expanduser("~"))
    d = home / ".fabcanvas_backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


# v8.8.19 fix: 스냅샷 대상을 **소형 config/state 파일로 한정**.
#   이전에는 data_root 전체(parquet/CSV 원천 포함 수 GB)를 shutil.copytree 로
#   통째 복사 → 사내 공유 환경에서 setup.py 가 수 분~수 시간 멈춘 것처럼 보임.
#
# ★★★ 핵심 원칙 (v8.8.19, 사용자 지시) ★★★
# 1) DB(`/config/work/sharedworkspace/DB`)와 Base 는 **참조만** 한다.
# 2) DB/Base 는 스냅샷 백업 대상에서 **완전히 제외** — 복사 시도 자체 금지.
# 3) parquet/arrow 등 bulk 원천 확장자는 어떤 경로에서도 절대 복사/업로드 금지.
# 4) 백업 대상은 오직 **경량 설정/상태 파일**: users.csv, groups.json,
#    config.json, informs/**, meetings/**, calendar/** 등.
# 5) _write 가드(L0~L6)가 이미 DB/Base 쓰기를 차단 → 스냅샷은 소형 설정파일만
#    대상으로 해도 안전.
_SNAPSHOT_INCLUDE_EXT = {
    '.json', '.jsonl', '.csv', '.md', '.txt', '.yaml', '.yml', '.toml', '.ini',
}
# parquet/bulk 확장자는 **어떤 경우에도** 복사하지 않는다 (이중 방어).
_SNAPSHOT_FORBIDDEN_EXT = {
    '.parquet', '.pq', '.arrow', '.feather', '.orc', '.avro',
    '.db', '.sqlite', '.sqlite3',
    '.zip', '.gz', '.bz2', '.xz', '.7z', '.tar',
    '.bin', '.pkl', '.pickle', '.npy', '.npz',
    '.mp4', '.mov', '.avi', '.mp3', '.wav',
    '.exe', '.dll', '.so', '.dylib',
}
_SNAPSHOT_MAX_FILE_BYTES = 5 * 1024 * 1024   # 개별 파일 5MB 상한
_SNAPSHOT_MAX_TOTAL_BYTES = 200 * 1024 * 1024  # 루트당 총 200MB 상한 (초과 시 중단)
_SNAPSHOT_MAX_FILES = 20000                   # 루트당 파일 수 상한
_SNAPSHOT_SKIP_DIRNAMES = {
    '__pycache__', '.trash', 'uploads', 'cache', '_backups',
    # v8.8.19: **DB 트리는 통째 배제** — parquet hive 원천은 어떤 파일도 복사 금지.
    'DB', 'wafer_maps', 'parquet', 'Fab',
    # NOTE: 'Base' 는 **제외하지 않음** — Base 안에는 rulebook CSV/JSON/TXT 같은
    #   경량 설정 파일이 있고 이건 백업 대상. 대형 parquet 는 아래 확장자/크기
    #   필터로 차단.
}
# 절대 경로로도 하드-코딩 배제: DB 원천 트리.
# Base 는 path substring 배제 대상에서 제외 — 소형 파일은 백업 필요.
_SNAPSHOT_FORBIDDEN_PATH_SUBSTR = (
    '/config/work/sharedworkspace/DB',
    '/config/work/sharedworkspace/wafer_maps',
)


def _is_forbidden_bulk_path(p: Path) -> bool:
    """DB/wafer_maps/parquet 가 경로 어디에든 세그먼트로 있으면 True.
    DB 원천 데이터는 어떤 방식으로도 외부 반출 금지.
    Base 는 여기서 차단하지 않음 — 대형 parquet 는 확장자/크기 필터가 거르고,
    Base 하위 소형 설정 파일(csv/json/txt)은 정상적으로 백업 대상.
    """
    try:
        s = str(p).replace('\\', '/')
    except Exception:
        return False
    for seg in ('DB', 'wafer_maps', 'parquet', 'Fab'):
        if f"/{seg}/" in s or s.endswith(f"/{seg}"):
            return True
    for sub in _SNAPSHOT_FORBIDDEN_PATH_SUBSTR:
        if s.startswith(sub) or f"{sub}/" in s:
            return True
    return False


def _should_snapshot_file(p: Path) -> bool:
    ext = p.suffix.lower()
    # 이중 방어: forbidden 확장자 (parquet/arrow/pickle/zip 등) 절대 거부.
    if ext in _SNAPSHOT_FORBIDDEN_EXT:
        return False
    if ext not in _SNAPSHOT_INCLUDE_EXT:
        return False
    if _is_forbidden_bulk_path(p):
        return False
    try:
        if p.stat().st_size > _SNAPSHOT_MAX_FILE_BYTES:
            return False
    except Exception:
        return False
    return True


def _file_hashes(root: Path) -> dict:
    """root 아래 스냅샷 대상 파일의 SHA-256 해시 맵. 상대경로 key.
    v8.8.19: bulk data(parquet 등) 는 해싱 대상이 아니므로 skip.
    """
    out = {}
    if not root.is_dir():
        return out
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        # skip segments we also skipped at snapshot time
        if any(part in _SNAPSHOT_SKIP_DIRNAMES for part in p.parts):
            continue
        if not _should_snapshot_file(p):
            continue
        rel = str(p.relative_to(root)).replace(os.sep, "/")
        h = _hashlib.sha256()
        try:
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            out[rel] = h.hexdigest()
        except Exception:
            out[rel] = "__unreadable__"
    return out


def _snapshot_roots() -> list:
    """스냅샷 대상 루트 — _resolve_data_roots() 중 bulk data root 는 완전히 제외.

    v8.8.19: `/config/work/sharedworkspace/DB` 같은 수 GB 원천 parquet 루트는
    스냅샷에서 처음부터 배제 (_write L0~L6 가드가 이미 쓰기를 차단).
    basename 뿐 아니라 절대 경로 substring 도 체크 (defense in depth).
    """
    out = []
    for r in _resolve_data_roots():
        # DB/wafer_maps/Fab/parquet 가 root 이름이면 통째 배제.
        # Base 는 root 가 되어도 허용 — 대형 parquet 는 내부에서 확장자로 거름.
        if r.name in {"DB", "wafer_maps", "Fab", "parquet"}:
            print(f"[snapshot]   skip bulk data root {r}")
            continue
        if _is_forbidden_bulk_path(r):
            print(f"[snapshot]   skip forbidden bulk path {r}")
            continue
        out.append(r)
    return out


def _walk_snapshot(root: Path):
    """os.walk with dir pruning — bulk/skip 디렉토리로는 **들어가지도** 않는다.
    yield (abs_file_path, size) tuples for files matching include filter.
    """
    for dirpath, dirnames, filenames in os.walk(str(root)):
        # prune in-place so os.walk doesn't recurse into skipped dirs
        dirnames[:] = [d for d in dirnames if d not in _SNAPSHOT_SKIP_DIRNAMES]
        # forbidden-path prune (defense in depth against symlink/renamed dirs)
        dp = str(dirpath).replace('\\', '/')
        if any(sub in dp for sub in _SNAPSHOT_FORBIDDEN_PATH_SUBSTR):
            dirnames[:] = []
            continue
        for fn in filenames:
            p = Path(dirpath) / fn
            if not _should_snapshot_file(p):
                continue
            try:
                sz = p.stat().st_size
            except Exception:
                continue
            yield p, sz


def _snapshot_data() -> Path | None:
    """추출 직전 data_root 스냅샷. 반환: 스냅샷 디렉토리 경로 (없으면 None).

    v8.8.19: **소형 config/state 파일만 복사** — parquet/CSV-bulk/대형 binary 는 skip.
    루트별 진행 상황 즉시 출력 (setup.py 가 멈춰 보이지 않도록).
    """
    roots = _snapshot_roots()
    if not roots:
        print("[snapshot] no eligible data roots - skipping")
        return None
    stamp = _dt.now().strftime("%Y%m%d-%H%M%S")
    snap = _backups_dir() / f"v{VERSION}-{stamp}"
    snap.mkdir(parents=True, exist_ok=True)
    manifest = {"version": VERSION, "created_at": stamp, "roots": {}}
    grand_files = 0
    grand_bytes = 0
    t_start = _time.time()
    print(f"[snapshot] scanning {len(roots)} root(s) for config/state files "
          f"(ext={sorted(_SNAPSHOT_INCLUDE_EXT)}, <={_SNAPSHOT_MAX_FILE_BYTES//1024//1024}MB/file, "
          f"<={_SNAPSHOT_MAX_TOTAL_BYTES//1024//1024}MB/root)")
    for root in roots:
        print(f"[snapshot]   scan {root}", flush=True)
        t0 = _time.time()
        tag = root.name or "root"
        dest = snap / tag
        i = 1
        while dest.exists():
            dest = snap / f"{tag}__{i}"
            i += 1
        n_files = 0
        n_bytes = 0
        capped = False
        try:
            for src, sz in _walk_snapshot(root):
                if n_bytes + sz > _SNAPSHOT_MAX_TOTAL_BYTES or n_files >= _SNAPSHOT_MAX_FILES:
                    capped = True
                    print(f"[snapshot]     ! cap reached at {n_files} files / "
                          f"{n_bytes/1024/1024:.1f} MB - skipping remainder of {root}")
                    break
                try:
                    rel = src.relative_to(root)
                except Exception:
                    continue
                dst_f = dest / rel
                try:
                    dst_f.parent.mkdir(parents=True, exist_ok=True)
                    _shutil.copy2(str(src), str(dst_f))
                    n_files += 1
                    n_bytes += sz
                except Exception as e:
                    print(f"[snapshot]     WARN copy {rel}: {e}")
            if n_files > 0:
                manifest["roots"][str(root)] = str(dest.relative_to(snap))
            else:
                try:
                    if dest.is_dir() and not any(dest.rglob("*")):
                        _shutil.rmtree(str(dest), ignore_errors=True)
                except Exception:
                    pass
        except Exception as e:
            print(f"[snapshot] WARN scan failed {root}: {e}")
        dt = _time.time() - t0
        suffix = " (capped)" if capped else ""
        print(f"[snapshot]     {n_files} files, {n_bytes/1024/1024:.1f} MB, "
              f"{dt:.1f}s{suffix}", flush=True)
        grand_files += n_files
        grand_bytes += n_bytes
    (snap / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[snapshot] total {grand_files} files, {grand_bytes/1024/1024:.1f} MB, "
          f"{_time.time()-t_start:.1f}s -> {snap}")
    return snap


def _verify_and_restore(snap: Path | None) -> None:
    """추출 후 data_root 가 스냅샷과 동일한지 확인. 변경된 파일이 있으면
    스냅샷에서 즉시 복구 + loud 경고."""
    if snap is None or not snap.is_dir():
        return
    manifest_path = snap / "manifest.json"
    if not manifest_path.is_file():
        return
    try:
        manifest = json.loads(manifest_path.read_text("utf-8"))
    except Exception:
        return
    bad = []
    for original_root_str, snap_sub in (manifest.get("roots") or {}).items():
        orig = Path(original_root_str)
        snap_root = snap / snap_sub
        if not snap_root.is_dir():
            continue
        # Spot-check: any file that existed in snapshot but is MISSING or DIFFERENT now.
        snap_hashes = _file_hashes(snap_root)
        now_hashes = _file_hashes(orig)
        for rel, h_snap in snap_hashes.items():
            h_now = now_hashes.get(rel)
            if h_now is None:
                bad.append((orig, rel, "MISSING"))
            elif h_now != h_snap:
                bad.append((orig, rel, "MODIFIED"))
    if not bad:
        print(f"[verify] data integrity OK ({len(manifest.get('roots') or {})} roots)")
        return
    # Restore
    print(f"[verify] !!! {len(bad)} protected files changed - restoring from {snap}")
    for orig, rel, reason in bad:
        # locate in snap
        for sub in (manifest.get("roots") or {}).values():
            src = snap / sub / rel
            if src.is_file():
                dst = orig / rel
                try:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    _shutil.copy2(str(src), str(dst))
                    print(f"  [restore] {reason}: {rel}")
                except Exception as e:
                    print(f"  [restore] FAIL {rel}: {e}")
                break
    print(f"[verify] restored {len(bad)} files from snapshot")


def restore(argv: list = None) -> int:
    """수동 복구: `python setup.py restore [latest|<timestamp>]`."""
    argv = argv or []
    want = (argv[0] if argv else "latest").strip()
    bdir = _backups_dir()
    snaps = sorted([p for p in bdir.iterdir() if p.is_dir()], key=lambda p: p.name)
    if not snaps:
        print(f"[restore] no snapshots in {bdir}")
        return 1
    chosen = None
    if want == "latest":
        chosen = snaps[-1]
    else:
        for p in snaps:
            if want in p.name:
                chosen = p
                break
    if chosen is None:
        print(f"[restore] no match for '{want}'. Available:")
        for p in snaps[-10:]:
            print(f"  - {p.name}")
        return 1
    mf_path = chosen / "manifest.json"
    if not mf_path.is_file():
        print(f"[restore] manifest missing in {chosen}")
        return 1
    manifest = json.loads(mf_path.read_text("utf-8"))
    restored = 0
    for original_root_str, snap_sub in (manifest.get("roots") or {}).items():
        orig = Path(original_root_str)
        snap_root = chosen / snap_sub
        if not snap_root.is_dir():
            continue
        orig.mkdir(parents=True, exist_ok=True)
        for src in snap_root.rglob("*"):
            if not src.is_file():
                continue
            rel = src.relative_to(snap_root)
            dst = orig / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            _shutil.copy2(str(src), str(dst))
            restored += 1
    print(f"[restore] {restored} files restored from {chosen}")
    return 0


def list_snapshots(argv: list = None) -> int:
    bdir = _backups_dir()
    snaps = sorted([p for p in bdir.iterdir() if p.is_dir()], key=lambda p: p.name)
    if not snaps:
        print(f"[snapshots] (none) at {bdir}")
        return 0
    print(f"[snapshots] {bdir}:")
    for p in snaps[-20:]:
        sz = sum(f.stat().st_size for f in p.rglob('*') if f.is_file()) / (1024*1024)
        n = sum(1 for f in p.rglob('*') if f.is_file())
        print(f"  {p.name}  ({n} files, {sz:.1f} MB)")
    return 0


def _run(cmd: str, cwd: Path, check: bool = False, timeout: int | None = None) -> int:
    print(f"\n$ ({cwd.name}) {cmd}")
    try:
        r = subprocess.run(cmd, cwd=str(cwd), shell=True, timeout=timeout)
        if check and r.returncode != 0:
            print(f"  -> exit {r.returncode}")
        return r.returncode
    except subprocess.TimeoutExpired:
        print(f"  -> TIMEOUT after {timeout}s - skipping")
        return 124
    except FileNotFoundError as e:
        print(f"  -> not found: {e}")
        return 127


def _has(cmd: str) -> bool:
    from shutil import which
    return which(cmd) is not None


def _ensure_critical_deps() -> None:
    """v8.8.2: extract 시에도 엑셀 관련 핵심 의존성은 자동 설치.
    openpyxl 은 인폼 표 embed / SplitTable 엑셀 export 에서 즉시 사용되므로
    pip install 을 따로 실행하지 않아도 동작해야 한다는 요구에 따른 필수 패키지.
    이미 import 되면 skip."""
    critical = ('openpyxl',)
    missing = []
    for mod in critical:
        try:
            __import__(mod)
        except Exception:
            missing.append(mod)
    if not missing:
        return
    print(f"[deps] ensure critical: {', '.join(missing)}")
    # v8.8.19: 오프라인/프록시 환경에서 pip 가 무한 대기하지 않도록 timeout.
    _run(
        f"{sys.executable} -m pip install --disable-pip-version-check "
        + ' '.join(shlex.quote(p) for p in missing),
        cwd=ROOT,
        timeout=180,
    )


def extract() -> int:
    # v8.8.17: 추출 직전 data_root 스냅샷 (~/.fabcanvas_backups/v<ver>-<stamp>/).
    # 스냅샷 실패/없음이면 snap=None 으로 계속 진행 — 신규 설치는 보호할 게 없음.
    snap = None
    if os.environ.get("FABCANVAS_SKIP_SNAPSHOT") == "1":
        print("[snapshot] skipped (FABCANVAS_SKIP_SNAPSHOT=1)")
    else:
        print(f"[extract] flow v{VERSION} starting - snapshot + extract + deps")
        try:
            snap = _snapshot_data()
        except Exception as e:
            print(f"[snapshot] WARN failed: {e}")

    skipped = 0
    written = 0
    for rel, payload in FILES.items():
        # _write 내부에서 보호된 경로면 조용히 return 하므로,
        # 여기서 쓰기 전 후 파일 존재 여부로 write/skip 집계.
        dst = ROOT / rel
        existed = dst.exists()
        _write(rel, ''.join(payload) if isinstance(payload, (list, tuple)) else payload)
        if dst.exists() and not existed:
            written += 1
        elif existed:
            # 기존 파일이 덮어써졌는지 여부는 파일명으로 판단 불가 — 단순 카운트만.
            written += 1
    (ROOT / 'VERSION.json').write_text(
        json.dumps(VERSION_META, indent=2, ensure_ascii=False), encoding='utf-8'
    )
    for sub in ('data', 'data/Base', 'data/DB', 'reports'):
        (ROOT / sub).mkdir(parents=True, exist_ok=True)
    # v8.8.2: extract 단독 실행에도 openpyxl 같은 필수 dep 는 자동으로 채워넣음.
    _ensure_critical_deps()
    # v8.8.17: 추출 후 data 변조 검증 — 변조된 파일은 즉시 스냅샷에서 복구.
    try:
        _verify_and_restore(snap)
    except Exception as e:
        print(f"[verify] WARN failed: {e}")
    print(f"\n[extract] flow v{VERSION} - {len(FILES)} files processed -> {ROOT}")
    print(f"[extract] user data preservation: snapshot @ ~/.fabcanvas_backups/ + "
          f"5-layer _write guard + post-extract SHA-256 verify/restore.")
    print(f"[extract] manual restore: python setup.py restore [latest|<timestamp>]")
    return 0


def install_deps() -> int:
    pkgs = [
        'fastapi', 'uvicorn[standard]', 'pandas', 'pyarrow', 'polars', 'numpy',
        'python-multipart', 'boto3', 'scikit-learn', 'scipy', 'openpyxl',
        'psutil',   # v8.8.18: 시스템 모니터 (core/sysmon.py)
    ]
    return _run(f"{sys.executable} -m pip install " + ' '.join(shlex.quote(p) for p in pkgs), cwd=ROOT)


def build_frontend() -> int:
    fe = ROOT / 'frontend'
    if not (fe / 'package.json').exists():
        print('frontend/package.json not found - skipping', file=sys.stderr)
        return 1
    if not _has('npm'):
        print('[npm] not found - skip frontend install/build')
        return 0
    rc = _run('npm install', cwd=fe)
    if rc != 0:
        return rc
    return _run('npm run build', cwd=fe)


def print_version() -> int:
    print(f"flow (FabCanvas) v{VERSION} - codename {CODENAME}")
    return 0


def sync_version_json() -> int:
    vj = ROOT / 'VERSION.json'
    vj.write_text(json.dumps(VERSION_META, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f"VERSION.json -> {VERSION}")
    return 0


def all_steps() -> int:
    rc = extract() or install_deps() or build_frontend()
    if rc == 0:
        print(f"\n[done] uvicorn app:app --host 0.0.0.0 --port 8080   (run from {ROOT})")
        print(f"[done] open http://localhost:8080 - login: hol / hol12345!")
    return rc


COMMANDS = {
    'extract':        extract,
    'install-deps':   install_deps,
    'build-frontend': build_frontend,
    'version':        print_version,
    'sync-version':   sync_version_json,
    'all':            all_steps,
    # v8.8.17
    'restore':        restore,
    'snapshots':      list_snapshots,
    'snapshot':       lambda: (_snapshot_data() and 0) or 0,
}


def main(argv):
    if not argv:
        return all_steps()
    cmd = argv[0]
    if cmd in ('-h', '--help', 'help'):
        print(__doc__)
        print('\nCommands: ' + ', '.join(sorted(COMMANDS)))
        return 0
    fn = COMMANDS.get(cmd)
    if not fn:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        return 2
    # restore takes extra args
    if cmd == 'restore':
        return restore(argv[1:])
    return fn()


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
