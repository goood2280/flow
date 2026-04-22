#!/usr/bin/env python3
"""FabCanvas (flow) v8.8.24 — self-contained installer.

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

VERSION = "8.8.24"
CODENAME = "flow"
VERSION_META = {"version": "8.8.24", "codename": "flow", "changelog": [{"version": "8.8.24", "date": "2026-04-22", "title": "메일 API `mailSendString` top-level form field 정합 (informs/meetings/core 공통)", "tag": "fix", "changes": ["**메일 API multipart 구조 정합 (v8.8.21 이중 래핑 버그 수정)** — 사내 메일 API 규약은 `mailSendString` 을 multipart/form-data 의 **top-level form field** 로 직접 받는다 (값은 payload 를 JSON 직렬화한 문자열). v8.8.21~v8.8.23 구현은 form field 가 `data` 였고 그 값 안에 다시 `{\"mailSendString\":\"<json>\"}` 를 JSON 으로 감싸는 이중 래핑이라 서버가 key 를 최상위에서 못 찾아 502/누락. `backend/core/mail.py` + `backend/routers/informs.py` + `backend/routers/meetings.py` 세 경로 모두 `fields = {\"mailSendString\": json.dumps(data_obj)}` 로 교정.", "**회의록 메일 sender 키 통일** — `_send_minutes_mail` 의 `data_obj` 가 `senderMailaddress` 소문자 변형만 갖던 것을 `senderMailAddress`(camelCase) + legacy 소문자 양쪽 병행으로 보강. core/mail.send_mail · informs.send_mail 과 필드 네이밍 통일. 호출부 시그니처는 그대로."]}, {"version": "8.8.23", "date": "2026-04-22", "title": "SplitTable 오버라이드 컬럼 검색/필터 노출 · S3 신호등 화살표 자체 컬러 · 인폼 ML_TABLE CI 스냅샷 · 메일/이슈 그룹 = Admin 그룹 통합", "tag": "feat", "changes": ["**SplitTable 오버라이드 컬럼 검색/필터 노출** — `/api/splittable/schema` 가 이제 `_scan_product` 를 통해 post-join 스키마를 반환하고 `override_cols_present` 를 함께 내려 FE 가 '오버라이드 제공 컬럼' 을 인식. `My_SplitTable` 은 `_CUSTOM_HIDDEN_BASE` 기본 숨김 집합에서 오버라이드 성공 컬럼을 예외 처리(`_OVERRIDE_EXEMPT`) 하고 `allCols` 도 overrideCols/productSchema union 으로 보강 → `root_lot_id` / `lot_id` / `fab_lot_id` 가 검색 드롭다운·CUSTOM pool 양쪽에서 정상 노출. 기존 '조인 활성' 배지는 유지.", "**S3 신호등 디자인 변경** — `S3StatusLight` 의 원형 배경 제거. 화살표 자체가 신호등 색(초록/빨강/회색)을 갖고, 업=위 화살표 · 다운=아래 화살표. viewBox 22×22 · stroke 3 · round caps · drop-shadow filter 로 시각 비중은 기존 아이콘과 동일. red 상태는 기존 `s3blink` 애니메이션 그대로.", "**인폼 → 제품 ML_TABLE CI 매칭 통일** — `routers/informs._resolve_fab_lot_snapshot` 이 `fab_lot_id` / `root_lot_id` / `lot_id` / `wafer_id` 를 literal 비교하던 것을 CI 로 교체(`_ci_resolve` / `_ci_pick_first` 헬퍼 신설). ML_TABLE 이 대문자 컬럼명(`ROOT_LOT_ID` 등) 으로 찍혀도 저장 시 스냅샷이 정상 해결. v8.8.22 에서 SplitTable 쪽만 CI 적용됐던 누락 메꿈.", "**메일 그룹 · 이슈추적 그룹 = Admin 그룹 통합** — 단일 진실원 = `groups/groups.json`. `mail_groups.json` + `admin_settings.json:recipient_groups` 를 최초 `_load()` 호출 시 일회성 merge(이름 기준, `mail_groups.json` → `.json.migrated` rename). `/api/mail-groups/*` 와 `/api/informs/mail-groups` 는 groups.json 을 투영해 응답 — Admin '그룹' 탭에서 만든 그룹이 인폼 메일 수신 드롭다운 · 이슈추적 그룹 선택 · 회의 mail_group_ids 에 그대로 노출. groups 스키마에 `extra_emails` 필드 추가 · Admin GroupsPanel 에 외부 수신자 관리 UI 신설."]}, {"version": "8.8.22", "date": "2026-04-22", "title": "SplitTable/인폼 case-insensitive 매칭 · 메일 본문 SplitTable HTML 인라인 · 개별유저 picker 도메인 자동합성 · CUSTOM 15줄 확장 · S3 신호등 SVG 화살표 · Admin 권한 탭 재배치", "tag": "feat", "changes": ["**SplitTable CI 매칭 대수술** — (a) 제품 폴더 대소문자 무시: `ML_TABLE_PRODA` → `1.RAWDATA_DB/ProdA` / `proda` / `PRODA` 어느 쪽이든 매칭(`_find_ci_child` / `_find_ci_path`). (b) **join key CI 매칭**: ML_TABLE 이 `ROOT_LOT_ID`/`WAFER_ID`(대문자), hive 원천이 `root_lot_id`/`wafer_id`(소문자) 로 달라도 같은 컬럼으로 인식 → 그동안 나오던 **\"공통 join key 없음\"** 에러 박멸. fab_lf 컬럼을 main 쪽 casing 으로 rename(`_ci_align_fab_to_main`) → `_scan_product` / `_resolve_override_meta` 양쪽 동일 로직. fab_col / ts_col / override_cols 도 `_ci_resolve_in` / `_pick_first_present_ci` 로 전부 CI.", "**인폼 스냅샷/CUSTOM 에도 CI 적용** — 인폼이 `/api/splittable/view` 를 호출할 때 자동으로 혜택. 이제 대문자 ML_TABLE 컬럼명이라도 hive 원천의 `fab_lot_id` / `tkout_time` 등이 정상 조인되어 스냅샷/메일/엑셀 모두 일관된 최신값.", "**인폼 메일 본문에 SplitTable 인라인 HTML 테이블** — `_build_html_body` 에 `embed_table` 파라미터 추가 + `_render_embed_table_html` 신설. `st_view`(parameter×wafer 매트릭스 + wafer_fab_list) 와 legacy 2D 양쪽 모두 스타일링된 HTML `<table>` 로 렌더 → 메일 수신자가 xlsx 열지 않고도 본문에서 바로 값 확인. plan 값은 `→` 오렌지 강조, 최대 60행 렌더(초과 시 잘림 경고). `mail-preview` / `send-mail` 둘 다 `target.embed_table` 을 전달.", "**개별유저 picker '빈 리스트' 해결** — `/api/informs/recipients` 가 users.csv 의 email 필드가 비어있고 username 에 '@' 도 없는 일반 계정까지 도메인 자동합성(`<un>@<admin.mail.domain>`) 으로 `effective_email` 채움 → 사내 유저 이름만 저장된 환경에서도 picker 에 전원 노출. `admin`/`hol`/`test` 토큰/role 필터는 유지. 결과는 username 알파벳순 정렬.", "**CUSTOM 프리뷰 15줄 가시권** — `EmbedTableView` maxHeight 320 → 460 (약 18~22줄 수용) 으로 확장. CUSTOM 컬럼 다수 선택 시 스크롤 없이 한눈에 비교 가능. st_view + legacy 2D 두 경로 모두 일관 적용.", "**S3 신호등 화살표 SVG 전환** — 유니코드 `↓`/`↑` 텍스트 대신 흰색 stroke 2.5px SVG 화살표로 재구현(`S3StatusLight.ArrowSvg`). 18×18 원 안에 선명히 보이고 폰트/zoom/OS 의존성 없음. 다운(아래) / 업(위) 방향이 한눈에 식별.", "**Admin 권한 탭 재배치** — (a) `dashboard_chart` 열 제거 (페이지 위임 탭이 동일 역할). (b) `ALL_TABS` 를 실제 nav 순서로 정렬: filebrowser→dashboard→splittable→tracker→inform→meeting→calendar→tablemap→ml→devguide(맨 뒤). (c) `PAGE_IDS` 매트릭스도 같은 순서 + 향후 페이지(spc/ettime/wafer_map) → 공용(messages/groups) 순."]}, {"version": "8.8.21", "date": "2026-04-22", "title": "SplitTable root:~~ 제거 · 인폼 메일 mailSendString 래핑 + 자동 xlsx 첨부 + 실시간 미리보기 · psutil /proc 폴백 · Admin 페이지 권한 매트릭스 (유저×페이지) · S3 신호등 화살표 확대", "tag": "feat", "changes": ["**SplitTable fab_source `root:~~` 옵션 제거** — 제품 스코프를 넘어 데이터가 섞이던 footgun 제거. `_scan_fab_source` / `_scan_product` / `_resolve_override_meta` 모두 `root:` prefix 저장값을 무시하고 auto-derive 경로로 회귀. 저장 시점에 `_migrate_legacy_root_prefix` 로 기존 레거시 값도 청소. FE SplitTable fab_source 드롭다운에서 `[DB 루트]` 옵션 완전 제거, 첫 항목을 `(자동 매칭)` 로 교체. canonical layout: `/config/work/sharedworkspace/DB/1.RAWDATA_DB/<PROD>/`.", "**인폼 메일 API mailSendString 래핑** — 사내 메일 API 규약 수정. `core/mail.send_mail` + `routers/informs.send_mail` 둘 다 multipart `data` 필드에 `{\"mailSendString\": \"<json string>\"}` 로 한 번 더 감싸 POST. dry-run 응답에 `payload_wrapped` / `preview_data_wrapped` 추가로 FE/Admin 미리보기 검증 가능.", "**인폼 메일 다이얼로그 대수술** — (a) 개별 파일 첨부 UI 완전 제거. (b) 인폼 스냅샷 xlsx 자동 생성 + 첨부(`_build_inform_snapshot_xlsx` — 제품/lot/wafer/splittable_change/body 를 openpyxl 로 렌더). (c) 신규 `GET /api/informs/{id}/mail-preview` 엔드포인트 — 실제 발송될 HTML body + 제품담당자 라인 + 자동 첨부 목록 반환. FE MailDialog 가 body 입력에 debounced 바인딩 → 실시간 미리보기 패널에 최종 HTML + 수신자 + 자동 xlsx 크기 표시. (d) 유저 picker: BE 가 `admin`/`hol`/`test`/비-email 계정을 선제 필터 (`_is_blocked_contact`) → FE 는 `(no email)` 표시 제거하고 username 만 노출.", "**sysmon /proc + statvfs 폴백** — psutil 미설치 사내 서버도 CPU/Mem/Disk 측정되도록 `_read_proc_cpu_percent` (`/proc/stat` 2회 차), `_read_proc_meminfo` (`MemAvailable`/`MemTotal`), `_read_proc_disk` (`os.statvfs`) 폴백 추가. `_collect_stats` 가 psutil 없을 때 자동으로 이 경로로 떨어지며 `source: \"proc_fallback\"` 필드 포함. `/api/system/stats`, `/api/monitor/system` 응답 동일 포맷 유지.", "**Admin 페이지 권한 매트릭스 재설계** — 행=유저 / 열=페이지 매트릭스로 transpose. admin 역할 + `admin`/`hol` username 은 모든 페이지 자동 체크 + disabled 로 '수정 불가' 명시. 체크박스 토글 시 즉시 `/api/admin/page-admins` POST.", "**S3 신호등 화살표 확대** — 원 18px / 화살표 13px Arial bold + textShadow 강화. ↓다운 / ↑업 방향이 원 내부에서 확실히 보이게."]}, {"version": "8.8.19", "date": "2026-04-22", "title": "사내 공유 경로 자동 보존 · 인폼 담당자 admin/hol/test 필터 · SplitTable fab_source 진단 · CUSTOM set 양방향 공유 · 인폼 Lot 드롭다운 · 메일 도메인 자동 합성", "tag": "feat", "changes": ["**사내 공유 경로 자동 감지/보존** — `/config/work/sharedworkspace` 가 존재하면 환경변수 없이도 `holweb-data` / `DB` / `Base` 를 자동으로 기본 루트로 사용 (core/paths.py + core/roots.py). setup.py `_build_setup.py` 의 `_resolve_data_roots` + `_write` L6 가드가 이 경로를 자동 보호 → 재설치 시 사용자/그룹/회의/인폼/대시보드 등 데이터가 절대 덮어쓰이지 않음. 기존에는 `/config/work/holweb-fast-api` 가 함께 있어야만 인식돼 setup.py 재실행마다 로컬 `./data/holweb-data` 로 떨어져 DB 휘발.", "**인폼 제품 담당자 admin/hol/test 완전 제외** — `routers/informs._is_blocked_contact` 신설 (admin role + `admin`/`hol`/`test` 포함 username 전부 차단). 새 엔드포인트 `GET /api/informs/eligible-contacts` 추가. `bulk-add` 가 동일 필터 적용. FE `My_Inform.jsx` 일괄 추가 모달이 `/api/informs/eligible-contacts` 호출로 교체. 그룹 `_is_blocked_member` (admin 허용) 는 그대로 유지 — 담당자 필터만 더 엄격.", "**SplitTable fab_source_off 진단 강화** — `_resolve_override_meta` 가 `db_root` / `base_root` / `searched_db_roots` / `tried_candidates` 를 응답에 포함. 에러 메시지에 product → pro 추론 결과 + 실제 탐색 경로 + 권장 해결법을 상세 기술. FE 배지는 `title` 툴팁 + 클릭 시 `alert` 로 전체 상세 표시(db_root/base_root/DB 최상위 후보/탐색 경로 목록).", "**CUSTOM set 양방향 공유 (SplitTable ↔ 인폼)** — 이미 `/api/splittable/customs` 가 공용이었지만 v8.8.17 에서 인폼 UI 의 Saved CUSTOM 드롭다운이 제거돼 사실상 단방향이었음. 인폼 인라인 CUSTOM 편집기에 공용 set 드롭다운 + 저장(프롬프트 기반) 추가. set 선택 시 컬럼이 `embedCustomCols` 에 즉시 반영, 저장 시 SplitTable 의 `customs` API 에 기록되어 SplitTable 에서도 동일 이름으로 노출.", "**CUSTOM 선택 pool 기본 컬럼 제거** — SplitTable + 인폼 양쪽 모두 `product` / `root_lot_id` / `wafer_id` / `lot_id` / `fab_lot_id` 는 자동 첨부되는 기본 컬럼이라 CUSTOM 선택 UI 에서 숨김. 사용자는 분석 대상 parameter 에만 집중.", "**인폼 Lot 후보 = SplitTable override DB 기반** — `GET /api/splittable/lot-candidates` 에 `source=auto|override|mltable` 인자 추가, 기본값 `auto` 에서 ML_TABLE_ 제품이면 override fab_source (hive `1.RAWDATA_DB/<PROD>/`) 를 먼저 스캔. 인폼 Lot 드롭다운이 'DB 에 실제로 찍혀있는 최신 lot' 을 그대로 보여줌.", "**인폼 Lot 입력 = 스크롤 드롭다운** — 기존 datalist autocomplete 를 `<select size=1>` 로 교체. 제품 선택처럼 드롭다운을 열어 root_lot_id/fab_lot_id 목록 전체를 스크롤해서 선택. `✏ 직접` 토글로 수동 입력 모드 전환 가능.", "**Admin 메일 도메인 자동 합성** — admin_settings.mail 에 `domain` 필드 추가 (예: `company.co.kr`). `core.mail.resolve_usernames_to_emails` / `send_mail` 이 username 에 '@' 가 없으면 자동으로 `<username>@<domain>` 으로 조합해 발송. Admin UI 에 '메일 도메인' 필드 + preview JSON 도 domain 기반 샘플 표시.", "**기타** — `PATHS._ensure_dirs` 가 data_root 자체도 생성 보장(공유 경로 첫 실행 시). `/api/informs/eligible-contacts` 도 role 정보 포함 응답."]}, {"version": "8.8.18", "date": "2026-04-22", "title": "Admin 메일 API UI 간소화 · 메일 파일첨부 범용 업로드 · SplitTable 1.RAWDATA_DB exact match + Save Override feedback · psutil 시스템 모니터 + 유휴 부하 정책", "tag": "feat", "changes": ["**Admin 메일 API 설정 UI 재설계** — 수신자 그룹 관리 제거(수신자는 각 페이지에서 선택). URL / x-dep-ticket / senderMailAddress / statusCode 4필드 + 활성화 토글만 남김. 저장된 설정 기반 **전체 API 틀 JSON 미리보기** 블록(headers/data/files 구조) 추가. BE /api/admin/settings 가 `dep_ticket` 단일 필드 받으면 자동으로 headers[\"x-dep-ticket\"] 에 반영.", "**메일 다이얼로그 파일첨부 범용화** — 기존 인폼 이미지 외에 xlsx/pptx/pdf/doc 등 모든 파일 타입 선택 가능. FE 파일 input → `/api/informs/upload-attachment` 업로드 → URL 을 send-mail attachments 에 push. BE 엔드포인트 신설: 실행파일(.exe/.bat/.ps1 등) 차단, 10MB 개별 한도, mime 자동 추론.", "**SplitTable 1.RAWDATA_DB exact match** — `_RAWDATA_PREFIX` startswith 매칭을 `_RAWDATA_EXACT = \"1.RAWDATA_DB\"` equality 로 교체. `1.RAWDATA_DB_INLINE` / `1.RAWDATA_DB_FAB` 처럼 suffix 붙은 폴더는 자동 매칭에서 제외(별개 소스로 취급). 사용자가 `lot_overrides[product].fab_source` 에 명시 지정하면 여전히 존중.", "**Save Override 즉시 반영 + 피드백** — 저장 후 (1) `/source-config` 재로드로 저장된 값을 FE state 에 동기화, (2) `/ml-table-match` 재계산으로 override 메타 업데이트, (3) `loadView()` 로 테이블 행 갱신, (4) alert 로 성공/실패 명시적 피드백.", "**psutil 기반 시스템 모니터 (core/sysmon.py)** — 크로스플랫폼 CPU/Memory/Disk 5분 주기 수집(resource.jsonl, trim 8640 rows = 1개월). `/api/system/stats` 통합 엔드포인트 + `/api/monitor/system·history·state·heartbeat`. 기존 리눅스 전용 `/proc/stat` 로직 대체. requirements.txt + install_deps 에 psutil 추가.", "**유휴 자원 부하 정책** — 최근 6시간 동안 CPU/Memory 가 85% 이상 찍은 적이 없으면 5~10분(랜덤) 동안 numpy SVD 기반 더미 부하 생성. 사용자 활동(AuthMiddleware 에서 `/api/*` 인증 통과 시 `mark_user_activity()` 호출) 감지 시 `_load_stop` Event set → 부하 즉시 중단 + 30분 대기 창. `/api/monitor` + `/api/system` 자체 호출은 활동 감지에서 제외(위젯 폴링 노이즈 방지).", "**My_Monitor 페이지 개편** — 새 `/api/system/stats` 응답 기반. CPU/Mem/Disk 3개 게이지 + 각 지표의 **24h sparkline**(85% 빨간 선 dashed) + 유휴 부하 배너(진행/대기) + psutil 미설치 경고. 15초 auto-refresh.", "**보존 대상 확장** — setup.py `_PROTECTED_BASENAMES` 에 `farm_status.json / sysmon_state.json` 추가. resource.jsonl 은 v8.8.17 에서 이미 등록됨."]}, {"version": "8.8.17", "date": "2026-04-22", "title": "데이터 보존 재설계 (snapshot+verify+restore) · SplitTable db_root as rawdata · 인폼 CUSTOM only scope · FileBrowser 첫 클릭 head 200 · username=email 메일 · 사유별 메일 템플릿 · 공용 메일 헬퍼 · dep_ticket · 담당자 편집 간소화 · PPT 제거", "tag": "feat", "changes": ["**setup.py 데이터 보존 재설계** — 추출 직전 data_root 전체를 `~/.fabcanvas_backups/v8.8.17-<stamp>/` 로 자동 스냅샷(shutil.copytree). 추출 후 SHA-256 diff 로 검증하고 변조된 파일은 즉시 스냅샷에서 복구. `python setup.py restore [latest|<stamp>]` + `snapshots` + `snapshot` 수동 커맨드 추가. L0 화이트리스트 가드(backend/frontend/docs/scripts/app.py/README/CHANGELOG/VERSION.json/requirements.txt 외 top-level 쓰기 금지) 추가 → 코드만 교체. _PROTECTED_BASENAMES 에 paste_sets/prefix_config/history.jsonl/status.json/resource.jsonl/calendar.json/reformatter.json 추가.", "**SplitTable hive override 확장** — `_list_db_roots` 가 db_root 자체가 `1.RAWDATA_DB*` 일 때(Case1) + db_root 바로 아래에 parquet 제품 폴더만 있을 때(Case3) 를 모두 인식. `_auto_derive_fab_source` 도 db_root 자체가 매칭 루트일 때 제품명만 반환 → `_scan_fab_source` 의 `db_base/fab_source` 해석에서 prefix 중복 방지. 이제 사용자가 DB 루트를 `1.RAWDATA_DB` / `.../1.RAWDATA_DB_FAB` / 그 상위 폴더 어느 쪽으로 지정해도 ML_TABLE_<PROD> → hive 원천 자동 매칭 + 최신 lot_id 오버라이드가 동작.", "**My_Inform SplitTable scope CUSTOM only** — 등록 폼의 ALL/KNOB/MASK/INLINE/VM/FAB prefix chip + Saved CUSTOM 드롭다운 완전 제거. 인라인 CUSTOM 빌더만 노출(SplitTable CUSTOM UX 와 동일: 전체 체크·제거·pill·검색). view fetch 는 항상 prefix=ALL 로 받아 FE 에서 embedCustomCols 필터링, 미선택은 빈 프리뷰.", "**FileBrowser 첫 클릭 head 200** — meta_only 기본 off. `loadBaseFileView/loadHiveView/loadRootPqView` 모두 첫 클릭에서 polars lazy head(200) 으로 즉시 샘플 로드. SQL 적용 / 전체 컬럼 SELECT 만 전체 스캔. JSON/MD 파일은 원래대로 원문 반환.", "**인폼 메일 수신자 해석 — username = email** — `_resolve_users_to_emails` 에서 users.csv.email 비어있어도 username 이 `a@b.c` 포맷이면 그대로 발송 대상. admin/test 등 시스템 계정은 자동 제외. `/recipients` 응답에 `effective_email` 필드 추가 (FE 표시 편의).", "**사유별 메일 제목/본문 템플릿** — informs/config.json 에 `reason_templates: {\"<reason>\": {\"subject\":\"...\", \"body\":\"...\"}}` 스키마 추가. GET/POST `/api/informs/config` 에 reason_templates 필드 반영. My_Inform PageGear 안에 `ReasonTemplatesPanel` 컴포넌트 신설(사유 chip + subject/body textarea + 변수 `{product}{lot}{wafer}{module}{reason}` 참고). 등록 폼에서 사유 선택 시 본문 자동 채움(text 비어있으면 즉시, 아니면 confirm), 메일 발송 다이얼로그 초기 subject/body 도 템플릿 기반으로 변수 치환하여 prefill.", "**공용 메일 헬퍼 backend/core/mail.py** — `send_mail(sender_username, receiver_usernames, title, content, files=None, extra_emails=None, status_code=\"\")` 간단 인터페이스. admin_settings.mail 자동 참조, username→email 해석(users.csv 우선, username 자체 email 포맷 fallback, 나머지 skip), multipart/form-data 인코딩, dry-run 지원, 응답 dict 표준화(ok/status/to/skipped/reason). 인폼·회의 등 어떤 라우터에서도 1줄 호출.", "**메일 API dep_ticket 필드** — admin_settings.mail 에 `dep_ticket` 단일 필드 지원. POST `/api/admin/settings` 가 dep_ticket 을 받으면 자동으로 headers[\"x-dep-ticket\"] 에 반영 (기존 headers dict 직접 편집도 여전히 지원). senderMailAddress / senderMailaddress 두 키 병행 주입(구버전 호환).", "**인폼 제품 담당자 편집 간소화** — 이메일/전화/메모 필드 제거. 아이디(username=사내 email id) + 역할 2필드만 노출. 기존 저장된 email/phone/note 값은 BE 에서 그대로 보존.", "**문서 정리** — `docs/FabCanvas_flow_intro.pptx` · `scripts/make_pptx.js` 삭제 (repo 용량 정리)."]}, {"version": "8.8.16", "date": "2026-04-22", "title": "SplitTable hive override 다중컬럼 · FileBrowser meta_only 지연로딩 · 회원/S3 보존 재강화 · SplitTable/인폼 CUSTOM UX · 인폼 필터 strict · 회의 메일 본문 분리 · 대시보드 scatter fit toggle · 회의 담당자 placeholder", "tag": "feat", "changes": ["SplitTable 다중 컬럼 override + FileBrowser meta_only 지연 로딩 + 회원/S3 보존 재강화 + SplitTable/인폼 CUSTOM 대개편 + 인폼 필터 strict + 회의 메일 본문 분리 + 대시보드 scatter fit + 회의 담당자 placeholder + 담당자 즉시 반영. 상세 이력은 이전 VERSION.json 참조."]}]}


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
        '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'
        'r1hX3z0jAAA='
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
        'H4sIAAAAAAAC/618bXMTV7bu91t1/8MuV91CwrYamyTDpYAaA07CObxdTHKmKqTUbaltK+ht'
        '1DLgYUgZkCkFm8EONohg+4g7TmwYcyKMAHMD91Rl/kk+ulv/4Txr7b37RZY9CXM+JMit1u69'
        '117rWc962X31f/4PIbou2SUnU8h3HRRdBxIHEv0fdPXw9VQhbeetnE1fjGQLl/XlMSs/amcL'
        'o7j+BV0R4qr8Z+ex+Lu0Veax+vf1f9S774Pe/v7Ql+VMOcvfuk/mvaW3YuDsCWHmrEx2yM6n'
        'h8qlTH7UFOVCsTdrX7KzYqRQyomRjJ1NC6++0Fp4ImKZPF10jJxtl3G3Y6QKJVtsvXjZuvUy'
        'Hn6SNcorylwJXZSLcoIl8dW9e0OzyY1ny5miVSqLrZfr3uOG/+BLvNI+4S01vZU54S7XWgv4'
        'Z6Oy9XpTeNUa7ovv3St+mZwX3o1190ZThEbder3oLbzzlia3r9ZbqgQPNWhxvRChhes1sXdv'
        'J1ngKe7jReGtXvfqmEHjkXt73p1eEbGtxrf0iKI1kS1YaeF+/1b8y9CZ03SnW19vPZxvLSwK'
        'd33TW571HjTjCaHW9LX8dz8tuVWr0BghyW81MGuaEuZam9x6URe0YjxLeAtV78GswLO96UVh'
        'Xr3QFV3cha6DF7oOfeUU8kcudF0zQxNafEsr2GrMetObmH1UqPjDhdy8yiJJF0+/aE/wb72X'
        'i97N695iBU/Ft8J9+pPwGv/fW6iID/f1G2614i69Swhz2EpdxCRYNQyaUqI4YYru4ItSYbwM'
        'FTaUMu30tdYx/t6rYNEb72je7tM1d25WmCwfRxwWnVYuaN2J9Hiu6MRIesnC8FdxEgIt/OUs'
        '1CURaCZrYeu7GWy6+3hJa46D8eySaN1YEdBuXFH6ZSbpm2Quk8c8nSQ92mR9MfWDTLlrcoBT'
        '+N5Kp0u24+C2WzNSA4T7YrJVW3BXZ3DvffdODau7ScoY+tWA+lUsBYDIHrMcOw45Ze1RKzUR'
        'Gsm7P+c9fYcB51r3v1F7675objUWEiLYAzlnfBI/vxZa8sHFrRdvRWuh4t7DbysrpAONO2rZ'
        'CdGqbXqvFt1XkwKaBvVzp6veRo00h/6YmcQTE11amF/KD9d6/hFs7f/NsDVUzGbK563hrA1b'
        'WIF6QlFpqpi099O6++9vIcRJ7+Y3BlbSqjSEO0XzpgUP7cfU61iHe++5gCl6N5qtOfwKprjR'
        'lD9ep/u8pc3W3bfi1Mnk+YGjJwfFsRPCu73i3pzybr6m76VqGGQwt6u8+r++gQYOpKEN+k9I'
        'DZDVAQxtq/wr0PD9Fql107CKGcOhIco0hOGkxuycJRWSZl1fJP1NWflksVRIj6fKEhd4zk1R'
        'LDjl3q8KWAtWDcV3V6v0rduotR4CG2oEP2YBG1nKpO1kqpB1MIwNLdLDLKxt/VQTgF+3viI+'
        'HuTH7tm2jPoiPIZazR6GYMjdm14CeJyaSAYCILOCHSWPfTZ0/syp5Kcnjh8fPJ08OjA0iAVt'
        'NtwXBP1rW2+wtNU5yFwB07bnVZ4Hz+On1areQ/wWOvzDesxMnvl88Ny5E8cHk4N/GDx19rwZ'
        'F3qtVjZ7DKvE6u5WhF44XTGU+IZYvmI8D80WEeMTv9z6VpilQqGczOK/TNoUhjBDH0esYf8b'
        'kpPcVoEZu4+fEap/1/z5tVw6NqaQVYauF1lfABqr3U+QOLzHTcj6cQOyhIovYtV7sHMNb3WS'
        'MX6xjk/toBexC3d+ngwCPwc4AWy1Tg3tHypb5XHnZGZ0rCyhzns0C/Si4XEfb+jzRmKbYbHS'
        '+cNjcTGvWQXCGu6bNUjIIMi9+U2ctgQgCIH3CO/B1GG4mNBQZHUsjMPwNHBRwVfwoBn78tHC'
        'FdHf//cH/f10q1MuFS7aYj99hiPJp0XKKjr0V7oEZ+6MWenCZbjWLFyM9ONAtAbk+KYCF0jq'
        'piSJh5G9vKsRMrp3HzIQlmxQoZvXWzcXGfzknaazfzibyV+EZBbqgEbGiKY3VQujY1TsCmZI'
        'QyC71rfVCOK4qyvem2dtTqfdaSZhd4XsJTup1cjJY6FjBd6gZlS7jF318LI1Ypf4DzJh2J5d'
        'srIkEPhJ2EHIN9HclP/caMJuUhk9Cx6I/i5mUheTI5mSg3m0Hqy37rwlDfAqK+A7/hppgiQY'
        '5b/YLt2nUzHz3Jkz55Mn8d+J45jNvedxbVJe406rNklG6NUnveXvadsCZKbxlD0AxbY2Gppb'
        '9QtlLWFQffqOvC4he/26990Td3aWlijpCwH81n9W2/dLMQIF8OwnyAF4r+Yxxq5uQG2fO71G'
        'A3irFQ+6/GgWN5uj2NKiY8h/EsRXzIQkqMnwNaJGFg0M2lGWhIiuHyzZqUwxA+hVd5s+SWtW'
        'sRfEQWNxU/luKa+lt2RxleciZ5dG7RjB4w8VVuOVyZ5Oj2YI48+JXGa0BCcNLSlxwIL9lM6G'
        'ftWr1rIXyvdwUn3hxwrBDZgj7Cb0CAb/VnXdq10n/+MtzbnTL1liUpZ7pDD3iNbNZ5p2rs64'
        '9+pKyrzz0pjUHlEsMF2PoOhOG+ZV6q2bS/St5H4iEADMwSHdCUzYR1o5+8BB0l2mfaVcspI2'
        'DYBFKiKFpxECYny5mE/4l2etPOIJ+hV8EHMqnjFZwlZzEg5JfHZC2cx7kKr+f4JUGUqSKTDN'
        '3kwebt3JlDOXbA1IPv+Bf9iE8Yat6tPzp07STrDX3aRbtxqL7osKuZ06giLAAgHu3QqPsEkO'
        'AqBKPhvqiLuVn+v70Fsh8F8gG2/nbUOffxL1C8reXs1QZAUNwajr5PHe1P472FeAxNAA2qRq'
        'XVlzzIpr3G7dbbp35vmOgNqvN2FuB0FmFOAlz547c3xAmVNf4tzAvx0fOD+QPH7UOAsWMcDg'
        'SXzC4k/qZu9+0709S3DFNKauJgPYHcmAtQNrU2OZrIRw/1LRKo+ZMM3YcBzhKzM5Ct/8pezd'
        'ezAKwxHINcx/G/h48Bx9jPn4HO8RY6QEQC1vY5F/E3YmRuA/Yr4I4uwn3Ol1qAOB9lbjIblW'
        'xcAUpDPlY5mQkcHDLlTBHGvE3u5Q8H2hS+YVhL8M78Etb2nmAraJ7Icou9tYcJ9sJgR7u5EQ'
        'xYMl5kl2pMzATO1HJHZJ12VlM6N5dp/lAgVBefA+3qE2fkzy1c7W5745myNyFXoxPaA1I9aX'
        'kwE3xg/LjvoQ4cxMJsPeM4ln82MC76l5Ne6SQatXrxBYHDuxA5fwnaGhTIkkdLcS+DmdHeHb'
        'eRvbYwViU4rHs9NoLUDpFmaUrSoJtmqLAM2EDiYCNx6olfbnMpNAk4goUG0bOSlfBLlJljM5'
        'm71+yJ1LOuvO1mANoSXqMOzBnDc1qRMCuEIAOrvITnC6vtX4dgdZRVCMkDhMD3wMY0hrTc3Q'
        'Mjbn/eB/eBxWlxwr57LJ4UKaUhPsAHLDdjpZllFLa2aGFv7jJsVnygt0sxZRaJ8M3cvjmBru'
        'QSXLSd6FWNEqQVFBxf7+gO2L7Ld1e9P94RmkgMGk0bEcM045zm5XZQb6j/t6qQQDV3VzEit2'
        'V6+TeHhh5iGewBGpXW59hmCM1D/iR7GxNPkrWecK1tlEBAGSextMnXbVlx+75ca8TCp9y/C9'
        'BKMsZq28UFkxE0ObHJnVZ2gU0H/sbQ9tFVRIfLSvdf8bNQsKEoh0M2VZRohGEe87PDOu6FEv'
        'bEPqqiHTJb0yB+PO1ShUgD5ZoDflRGRPOO6DDU2vtytFJ0e1x30DMPphnUR3e3OPopWRIFvT'
        'G5+HOTKMG3fgkhMp5xIHSkwJFCGgbynOuN/0lqsUXtKthEesQnt+v4eBASAnc3JvEXaLLcys'
        'vrD1hoXW2X3GzEPj+SO/P8QkMcHJnnSB8OyI6fNn0x4ZsVPkzCVJgTyeV7xHkzIEkQlTJQHJ'
        'CokiS6pNGoPoH1sgd5rmqKTEJKZeIT6r2ZGkqvAKY4Us/l+2ORC4VW9dbxilAuxLZi1CESk2'
        't4HtpiuBPBZmyIY2XnhVjnTd+rZdUyjXmq9gm9zXDUUdIGOK6F7N6L0aJCVg0/6cVSZnXfnU'
        'pkBW7O/fx8v/4KN9IuYtvBN9B77u76dBSPe/e+ILT9KRhOYoKgtD3LJa0yRSxSOt6+vu4xXp'
        'qJpY66JblUlajqZodu5tjKTMPMjmwWbJUqMZTgloCr53jdwDVkQcCVuCDdN4v0gxqfdunhgp'
        'rPAeduWXW3PYlKk7UruZvIAyt5YalH9QEXR/4sPilSjlYje0vC7T1LFoWiAxUCoVLg9dGiX2'
        '0Xfg7w/6DhDg6yQ1pASH0FquUnIEoiHtb91t4OnGnwqFnHFmiKwF0TTRQenmEyrsj8mwPw5j'
        '9x5MxbzFCthF40nr/vdREYNMwIzbxbQzRQxxOTNtOWPDBasEDjVmlSiEftBUWQ0Ra/2lRs6O'
        'oG+x4i1XOBahCFZ6fe/BMzhKxbnMgZPsBodUNIYVwEnmLYBBdZGM57HW54OUhLCHITQoPdTQ'
        'nwI+Bz4ZfyCwIGPDJ4k4+KAy4/iUsrLAP4u+5ftzVpG+z9KA9qXRcVCOmLu6JtxvKQCPpTDD'
        'swOfDILh0QxDLiXM0nim0E2IuPUIy9XrjznFlEExaM42pP/B4yRnAlODjsZytuNYoNUqqI3T'
        'WO8RxPT9M5lhIqcHv/5ab1+Q1FWOLVoq0JWkbgWpytVtrBHZ6uYNpAQRnN2PmwQ0L5oIl2nQ'
        'ojNeBrJTHjDFUUDjeRCSBCqjNC/ivWMSaS+M79uX/p1/a3yXPDUQCEb63xHUEGNwCuOllC1p'
        'PCRFbvklpaukxDRuyPgG8wV2EMpSHrjCPMy9A1LWBIpzfq+yTB4DjH0Ew42O5/1soOLRwRMl'
        'x/3V5FrXeXiapoDTH8lcUU6JWUVFRVoqWWuNlwu94FfENSWQMnh/N7P1Gl4myBt59VmmbEmV'
        '0khKDE5yVCOfoupEMr/n1qtYDwE8nsoeeuMZIp0E5bc7SzacfFD0yPzi+FHh/nUNWvBlIPCH'
        'cO4VJTBwoY2/Ac+euU+fcCEopjRSRm5xM5R7SyCsyRfyGZi/yFoTYM+INo1UIT+SGTUuF0oX'
        'DQc4Zqfpo1O0UraBWDMSeB6iKPOIYe7OkLkk28le/kGpVVVkoQMdClCc0WpPZ4a+1TQuqAWr'
        '+qdkUbx3HUt+qtpJHgxXVNGTVIAMcAOuBwxXFj3F2TND5xMiXZroLZHGctqJB1bV2+TlklUs'
        '2mkVmjPdTHJ5z/9C8noa/+NBQ5p9BCMonf/Dknb5uwci01Rx8+4T26Dqbqd8g2SpFFqw05EA'
        'RZmisA5JJ9QeESpMk9rk3VwiH9uthojpgEZuhJ9FTtJvzBASGIjXJOqHQsakRByDAiF2eIWi'
        'nS9OXMmGggrldyggew07+GTwvIjw56uZ9DUjSuu9B8RWWnfXaSUgKDwL6UrdxqJ367E7W5dR'
        'DD+4W03RnW660284AyMjOB/UlbhgWVzf5XIW2y+VWY9nrGxhlLk7D+ctT7n170kb0vZwYTyf'
        'stMU3VBgOP9EEubOXqE1s+ZWGFsoAP2/U3KK3aEEX9TLgCzyz+YQrYI6x9JxEYlCDoqjsoLW'
        'iVQboJS9tqrdUozAAU6lTkJSlUHsbcZJDmcLGAzMppAvW4Bc6bCxdmLcZixfkDEKpYp5IkqV'
        'FKj6lJwCAkXy23TZmXBysDrpCbthfVb50oijfKLSYOUtIS7EuSBffszBXQacpzj7mXHKzhnH'
        'M85F4W0+JpY0W6OIBztGPgJGSU9IporjyaINmKWiY8zkxxr0UFP0A+yF11iL90R+kbNzpGx0'
        'N54wcIkK8hQTGvTn+ULZypptv0hjEnR7AcAkl0M1Qenkpd2Te0sVslkEVUm6RQV/ap3MYCvb'
        'kydEGwPX5N5fo1hwddJ90kAgy76DUIynMGJls9QRcaHLzyfDIFoLazrvDrGXIS/58B6djIdT'
        'KBdK6ktTJ9QVVcUA7urrHUqBv4a5gDxj/6BvalsRtB9WKkvsvHk4+HmEXGKxoLF5p1hw7IRg'
        'bVa82a9uKO0OxYBLMrNzrx6alG/PTRgPfouNoq1MM97skV5HuK+q2AtE00+n2LDk3W5jgZaA'
        'WHRrc1LGaz98w50zLDmehFEEe+3lj9hQdhK/LuySBE17RcQ9fQcQPRmhO/r248JACVgDlMmm'
        'MfeyfaU8JOuRW40F3JmAZd5TJQsDn+cQ74RCHQ6zbzQBZIpTUFw6vRIKqjZmfjvZ7vvfv5ls'
        'K9slzr9Y1zGrJiuYyeNmmHX7oCwljE02CMA0SBHp7UiivNWKO70Wqg04dpkSW1Ig+unBg04W'
        'ytuqPtq/tuVNhEycvDef3k0ARDBWJw0pBz9htAs1U70ZCH2X16nc+qSp0i3ui0lOMHAugVPG'
        'kN1le7hXsiEQk+NH+Z+jlmOreDMMNqpDQpJOnuGNdQRp8MbEyahSQK1VUET+m9gv/Q13DUGP'
        'F+krzQ30BdXm5BN2JkT8Q+Z04EaZMmZy8iNmPTLhFYI8f4KkJTAh6UsZUtglTOsp4i5D1ugM'
        'WZlTlSmDMxXgQdzWwS0LkVCkXqVconvnPwhW7zU0Zizc5nSCpPOUFr/dviVKsCOWU+4FGMgt'
        'Uc0s3nKVhlt4yykxLlq4d98GQqIFwArvf0PNMqCtVAX4aV2YCYPEY0T2LIT69U2BiKC1WAOj'
        '2SmlL0OwHS0ooH7ew80d+wQ6UACVbwavZCTmvFz3Dqk79jkhVJZlCDhZGCd0xbtZ3UbXtnM8'
        'O5sZzcC+e9UMHDNwo8Pj2YuAXNUEoz2VBAeV+iK2Qp1BJ3i4xFfETDk5VtHZdXIU0+tBVWOX'
        'B6tmtlAspcrBEbaUo+RxydQiaj2ockYw0vOmHKkq8OtNUglOokuIN7wHla2N7cm7ToiXLIyM'
        'aNSTDsGvOewQIcve0GE2QUaCYSBB8JdjW6XUGBajbnFksaWUIVWw8ukMwb3uG/BDIM0ydIUN'
        'wImYeZWcjlBhOxsuPpP03cd1lcFV6RLinzdnKZOogLGbqATF3jKJ7m5IqnrzOvd0wiirdd7h'
        'oFfJZFeDrao2WzPXKRN1vQkmwQhhWlmbMnSqJsbte3IoSV9jarGGLwvEvkHvqmg9qgA/jOgU'
        'ZWAQ3yHH3NHxxEJ7+MstXVfze5CB2D9ubi+xpcadciGnyKJMmhFKQbuwcqgMt630/U63rSgg'
        'oDAPwDtkXQLTUZOK5BiWdLaSkIkwFJepOWx6zacP9AxCQY0tfplLJ9L/sumtztF2UNcDz4wX'
        'HnGp3SqNEqOE0ON1/J/snRxNoxb3TZrlFUqLq6JsU1XJjrEMZFsdB0uShmEIr3a9J9zgEynL'
        'wfH40qNMA88TT368pMqD4bv9SoVO0nJRQ/fndQxjdHZfTpwb7nSPoUz4R9JjoYd1+zKNVN3M'
        'cI6rvf0q6LnauSeQayTh+JXiIdl2FsxKdWtHJ//ZCV+BuD0yEfhVGsB9VfEqS5xlgJL4lUa2'
        'ePJxq3PeytwOPolYljQhcTgsBI1M5NSkPmjw8p1ByAywxN4wAnHqRaLgYUrj/VkP9+dc1q/f'
        'yWYx1rEeJQSqNpr0A1Mv2G+5UN6T5ANG5U8vRDBjXJU2O6bF4gyK7tpbrojdXvH+33xiJ6JJ'
        'mr2HMIaLEASAQccayANvIxekBZYt211DXUXgiQ/WvZWZXeQtExKQd1BgCj9fCVplK4loUEmY'
        '06GpQq6Yxd7yasxDjk3xqnAyf7IP9x2JJhR1vpc1iDpjqcQVWWWFK8AwtJAyG4HC6uyKBGX2'
        'KXq6AH5WRh4bLv+Xxbvq/ISp4jGWWLXGui5Xy6Hfoqpl7ZBFU1m3XUm+kk5bBx3nTVjrZKm0'
        'vWcr5tWqByl7mSta+YlEqpC4WKLKFuczZY1V+2RNjRxqI9EdYKow7R8OaG6v9TJjBbcHJkFB'
        'I8zdPKTvPvL7Q7qW67dCPm5gZdQtJ3NhCVVzkEYv9rSLY49eWrdQCTZ5CoTQUY6tDBZ+tNaa'
        'n9H5qPYq+WajdVNHuObZgfOfDiWSdt4ZLyEOyJSUS/MjAtUBzOlymXEkTYfnaAuaNv4mJHsm'
        'wPd7CnehbzQiE1ZKEAGFFEGV/OU9wt8Dvzn8jWgdeSJIfqtR8W7NgLKFok6ZqtWpx4158qiI'
        '6CkSurfYFveGQUjYV7BUkbPKqTFsGnl9ccbHL9tOU1ooVIIiegZLm5J14+kqEedu4qatRejI'
        'q0kEldxO83zqvQPebUumkK2+wDrXlhIKdTNKWq2aGqX3jPlfsydrXA9yO9pjMUhAEz47dxJm'
        'dKU3bRd7y5QPLePPbYdj6BpXoY8VIJ0PfFWXDfC0IQpgiI/fUM7Q72tQy1D6D9CVhJJW2Jqe'
        'VHYSSvHS2a9NUN6l2JhtpSnM4hiPKrmOOrEW0KCjgyKUWdLQo0i7XUzKRZm6N1ifvmk86gQJ'
        '6nlfXOgKS+RC15ds9JI77dC13F5d6KCXQayhu981SyQay0H0Q26Uory1USyW8b/0iJEupGQU'
        'rtJ0sjZBvUbLU5qL6JYHEHz1fSZfHJchRNTYx4tUeem1wBFSYzlO7wbmQreTRpAb8lt+RHCv'
        '7NYtjjtjLPj2kFQGvAcV1siJxBL2FdtIDFtlI1F0+lS/OQe2PaJv36mjfs1lgahkj8hlCL4V'
        'I+PIZ5e4bieL9mM6/fXZc4Mfn/iDSWpcKjuXM+UxVfHjIqB/2+AfBo6dBwe40BUembLD9h/H'
        '4fHLExF3HqE1yROnT544PchuKXL944GjplDO3hkfofqq+4qbAGRfa4iCqjnpwyeUcohBOBAR'
        'HUVTSV7v9drW5jfxEN9kbSeCoBmY84XixV8mwpVh1mLO1wrCAjr1yakwIkegAMtVzpBtp6VR'
        'dIyEEoQC82+Jw/hlCBVbUFdDrC8O/ZOP75W5IJObQ1jfZHSpYUJVm6HChDXShUMm5BIfzveI'
        'WD8Nlcv2MlXt5W3msagwc6OhLNinoDBLmAjrtsxe3d7EGPvjJCcrTV1KMVXs9ZsPKdGOWTyH'
        'GuPWD+KCg2AZA/PhJoMUe2ZNiZB63P2lt0tMeQ3t8zs4D5kglGUdygjqZD9xOb57fsZd/hvh'
        'gyraFEoTsm7zIUIL4f31LZW1COlX52JEk0jG3O6f7RHlUiYnDnz0wT5BrS9Q6D6okPdoPt6x'
        'rqFOUWyz5+6O9Y6fX4+B+WI2P7/mjcLfNoxqGE7O9I9IEZhXp7iaAbSHT46UkGQPL8dGZEYl'
        'GFemZDPCJMpXynhwJo87s9kkUFiBjnLDEvbbabx0w8SGKH0f9sZaJ18Cld+Kj3RFUTZCB5Jl'
        'h3Hgw//FHbfUEtu4w1069esc9vsM8sOv+/ZB/DF3edG9sxLXA+XHc8UJMfT5cb3jZNY/bvpT'
        'YXIWMljynPhpbGC8PHYqk05n7ctWSUfUSup7ZTj2wxJtj27VNHNW6SKz4aRFnYZApOD8SVzl'
        'xeWdfDgliZ0qmmLwEmTLKQNuPlWzknYMe6dkWLfYTyujTWHFarxLRLff9PXBr3vJg53y4Yxn'
        'vCo1iyiKUVdX/UdGvFV5oA6C/qFKlRdqy2nf0VMTyVPyseFiWWOx9Redg6WcaCddVsU4uQ+J'
        'aMVzPwHp1saMGq+buRE+ERnno+j9H4wJB7H6xWwmb+/dGyON4GN0XPUV1D5mp+PbiJ/baLiV'
        'asxbrQBCDClBumtbQVa22CaojbJZlb00JXsE1ju2jVbIEo9KH8jmSLXyUOUAgfT5wWPnB4/z'
        'ec3TA6cGh1ScP2JRnwGTNnkGCBSOsYYv2urokWZRUfzgquC2JBnn2uC+ORe09h5BwO9++xsU'
        'dOFBV7x8HixiuoeiG4/JjEx0YwnQFjvexvlVolJYjihZl/mtA0E1SyV0sOQJ4aQKRZu++xg8'
        '86jsGJTNQjItStxQ9O/bR7fowPGwbA1QFJDGhYEv1onO6PBkapGA/If/5MM6MuGnv5IH9+jQ'
        'pM9T5TlMP8nN2cJo2HP27HnF8t87zvD1Z2fxaht7Nc/H2iiLUAmHnX72wfzaIIaRsvKXLCdJ'
        'MROdUFO603sIupYrHjFUIlmROt0xE3PGyDwQ9xcnyiWbDrupBxJ1GPp0oLf/w49EOjMyIkkX'
        't/qojgkq2z1ucNe0JNoE1grP/AOLqmf+xUsEDQAzPGWMGpf08pXGiC+ylB8r/1nN9ksGOq1e'
        'TuQv08+f/LTirq4FuYyEOLmP69DMNPyedlWii+m3LoyUEGLTB5B6x3BSpUyx7ADEaD7GucGB'
        '46cGjWOfDpz+ZPDkmU+MzwfPDZ04c5qN0tjmJOlwdfDqDO9eg/uhNqvc5agyLFz7465kfgeC'
        'oq2dYEM6WQtISvkbOnlNLXpJVb9THl/CgxGCFSOKHIZuldVfUtCB+MEuqYOIHd13yFw5W+iT'
        'uAjsmXwII1Rm4VTIcDgRwjw4zL3Zh76lFpEYvdmhj0BZ/0SdpZCNz3L1pT+OU1Y9fOiMc7XL'
        'VdVowqPsV3lL3T+uDtUnCc6TsjUy0o/JOaBt89Sn3rhwrCapW6yeTnEkzR1U4dNSES7P78IY'
        'TlIFxgh/wXnAJaX8up8TPv7FS+Vs/WNFkfjB75xksw4LkSOaRCJhbI9qDH5Pii76yFN6oSN1'
        'KpemQg3OU/mpY5kE5tWFDi5FAyFytH5Sl7KfbS8d0LXM5bkO/EHWMcO+QEJ8CPT1uWF2aoJP'
        'bNXEwMmTxr+ePnPUODUw9K+GDOqMz08ZWLAWZ2osU1RJo46lovZ+wW11IPfNjNItWSYJ17nU'
        'LZ/9gY8bydLKQb8Kx102P7+WI//8upjJZn9+LV9qEJdH9cWITTktiipbC8+4+MCzPoyFydp4'
        '4xG9TubjQe3b2wpGfnH1eo+kLpyspkahN9XgKEi7xP+B21SippJqkv2tKrAURkYSMiajBgsa'
        'hGIzgy58CrXw/zgH6zn7R3m6RFue/xSt7IWsVXJE1vrTBD83hucGh9uVd5DpVxmAJsTQ/zmp'
        '6t/UW6VkLCtRQ4MngZFCntGRX6gSBWWsjFPHw67n0SxARBes8cf6pm6A3LUnNcjjSatlGflJ'
        '7MOyaXBbgZoPP4UT4Wr9/qGohOIl+hgUmSRsL9T11RSm9fvhRMpU7WqqghMqmXDaW/FP1U0m'
        'GyL4LRN+QKvbIoOuMeb7FDuEz2yFmn+3HZJqKwpQDkv2SBL/Waptiw22kSwqBj19Yqjz0j7l'
        'UnLTmS/pypQfoomUbAufk4gdiswADtI7jg7Jq0eo1Rl/OuPDX2G2/JYnQOCFrh5xoYsaWIMr'
        '166ZwTl1nw58MnjeoNa2tvSbnwPBBNqfHyQnOc8oAgw7a43anyDI1gd9zHP80/P6l3zaHYP+'
        '1KTdrMwEybiYFJZGLLUc2UpMrXGIOi1cV11Q5lWVNrp2FYB77SoXUK9dzeFa1r52VU6YltvY'
        'lCcFQ8ipwgT5uFBhWu2K1g4+EBejR4fUU6Vi2UJ72CPTCz6agqVVysV7/PKT0sq2ZCudZQT5'
        'iayOFD5g3zIc9F8dI3u+3qgX7ngP1iVGZrPbCjKdKHund29pI/ULUjGZRfcrVz3Y75QNtQ8u'
        'OT2CQ50eWihoYbmHjys5h08X8rgYft2BuiSZV5LeZ3f4QteFrjixIO5lIVypNFSsfHsl0bkW'
        'pzahscnHQvU84IP1CUoCoVjocOV3DWxlTwg5ZMivbpedrrqJtocOlrtrnMl2LmaK2LbOL33b'
        'JEo6/6QnOBoAhvBotkfH7ulMqswYsDIJSh0rXFSM0ygXDBq4aKcNqYtxXS/++bV6uwSjE+Du'
        '/gr3p3/XgFSCRoU+fvFBrVNnQqjuEorHpEn+w/LmjoUGubKECEFBtFKhaGzwRGaa71+eEDGV'
        'gVM3S2Gq9+jJgFKef/UzvjxDbkjsUPVpf7kaH6Ckl7XJF6FRGtJbnoohwAIvI8oD4cKo4r+2'
        '2a49xA119qjT51Sanqez6PD6vlQ1r+LXB7nz8zE/Ilddo1JBMzJXI3ug++WPQy32fh3Gz0Hz'
        'z4wiwkTbyBfKtj5YfdTnStGmgsfNbWq0vqleHAWapEGBo72PreFjMkymV1AmM/lyqZCgKo9J'
        'cb2pg8GcddFO0mV4KiDtjWfE1GMlu1gQ9B6df/9GjR1/jwzMR//Mkb9oaEYQvDKnqFJb1iQg'
        'eZwGbBDZmn+iXwHzaNYY2h/KOMguvGjaxogmaMCFg6SN6lqk80epcui9MtG3priviKdyPiXc'
        'zoowiqJR4GwZwfPoaNYOj+CrJdxqyh4rZKH875lmCQlOikrTSl+C3bvKTCihde8ms+6dRUZJ'
        'SE6XBp1TUcF17yq37p3F1r2ruOiX/vVIpSihWwjJZOvfM2WkOLQiwnkO5Zy2Kzf9g8/4978A'
        'uOd+WOxVAAA='
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
