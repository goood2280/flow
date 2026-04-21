## v8.8.14 — 2026-04-22

ML_TABLE 컬럼 리네임(rule_order+func_step) · 자연정렬 강화 · 인폼 목록 모듈 pill 재배치 · 페이지 admin 위임 · 예약 백업 · 활동 대시보드 · HR 감사 긴급 fix.

- **ML_TABLE 컬럼 display-rename** — KNOB/INLINE/VM 에서 `knob_ppid.csv`/`inline_matching.csv`/`vm_matching.csv` 매칭 시 `{PREFIX}_{rule_order}_{func_step_label}_{feature_tail}` 형식으로 display 이름 재구성. 원본 컬럼명은 `_param` 유지 → plan/notes/meta lookup 안전. /view + CSV/XLSX 헤더 동일 적용.
- **자연정렬 강화** — `_natural_param_key` 가 display 이름 기준으로 정렬 + 내부 tail 도 숫자/비숫자 토큰 분리 자연 정렬.
- **인폼 CompactRow 재설계** — 좌측 96px 고정폭 컬럼에 색상 pill + [reason] 꼬리표. 모듈 미정 행도 회색 placeholder. 제품 × 모듈이 한눈에.
- **페이지 admin 위임** — `require_page_admin(page_id)` dependency + `admin_settings.page_admins` 저장소. `/api/admin/page-admins` GET/POST, `/api/admin/my-page-admin` 조회. Admin UI에 페이지×유저 체크박스 매트릭스.
- **예약 백업** — `admin_settings.backup.scheduled_at` + 60초 폴링 스케줄러. `/api/admin/backup/schedule` POST. Admin '백업' 탭에서 주기/보관/즉시/예약 통합 UI.
- **활동 대시보드** — `/api/admin/activity/summary` + `/api/admin/activity/features` 집계 엔드포인트. Admin '활동 대시보드' 탭에 by_user/by_action/by_day bar + 기능별 활성 카드.
- **HR 감사 긴급 fix (2026-04-22)** — `eval-lead.md` 팀 로스터 정정(feature-auditor/industry-researcher 제거 → security-auditor/domain-researcher). `orchestrator.md` 브랜딩 flow. `dev-lead.md` `be-*/fe-*` 제거 → 풀스택 dev-* 구조로.

## v8.8.12 — 2026-04-21

인폼 공동편집 + 담당자 패널 전제품 삭제 + 모듈별 요약 접기 + 답글 [RE] + 회의 주관자 기본값.

- **BE 인폼 공동편집** — `/delete` 권한 확장 (작성자/admin/모듈담당자), 신규 `/edit` endpoint + edit_history.
- **담당자 패널 🗑 항상 노출** — catalog 없어도 삭제 가능. 담당자 전원 삭제 + products/delete 연쇄.
- **Lot 모듈별 요약 접기** — LocalStorage 유지. 접힌 상태에 요약 stat 표시.
- **답글 [RE] prefix** — ThreadNode reply text 앞 자동 prefix.
- **회의 주관자 기본값** — 새 회의 모달 owner 인풋 초기값 = 로그인 유저.

## v8.8.11 — 2026-04-21

인폼 SplitTable 자동첨부 — prefix/CUSTOM 선택 + 컬러링 동일 + plan 핀.

- **scope 선택 UI** — ALL/KNOB/MASK/INLINE/VM/FAB 칩 + CUSTOM 드롭다운 (splittable/customs). 선택 시 자동 재fetch.
- **컬러링 동일** — EmbedTableView 가 `embed.st_view` 있으면 SplitTable CELL_COLORS 팔레트로 KNOB_/MASK_ 값 컬러링.
- **plan 📌 핀** — plan 있는 셀 앞 📌 + mismatch 시 `actual →plan` 주황 + 좌측 빨강 테두리.
- **fab_lot_id 행** — embed 상단 두 번째 헤더에 per-wafer fab_lot_id 표시.
- **st_view 스키마** — {headers, rows:[{_param,_cells}], wafer_fab_list, header_groups} 보존. legacy 2D 병행.

## v8.8.10 — 2026-04-21

Rulebook 컬럼 매핑 soft-landing + 인폼 Lot autocomplete + 스냅샷 로딩 버그 fix.

- **Rulebook schema 저장소** — `/api/splittable/rulebook/schema` GET/save (admin). 각 kind 역할→실제 CSV 컬럼명. 사내 CSV 헤더가 달라도 여기만 수정하면 연결 유지.
- **FE 컬럼 매핑 modal** — 섹션별 🔧 버튼 → 역할 리스트 + 기본값 복원/취소/저장.
- **인폼 Lot autocomplete** — SplitTable `/lot-candidates` 의 root_lot_id+fab_lot_id 병합 → datalist. 사내에서 `/product-lots` 실패해도 정상.
- **스냅샷 로딩 영구 bug fix** — early return 에서 `setEmbedFetching(false)` 누락 해소.

## v8.8.9 — 2026-04-21

톱니 Rulebook 섹션 KNOB/INLINE/VM 분리 + 연결 방식 설명 + inline/vm rulebook CRUD.

- **Rulebook 3분할** — prefix 별 박스, 테두리 색으로 구분. 각 섹션이 어떤 CSV 와 어떻게 매칭되는지 한 줄 도식 설명.
- **KNOB** — knob_ppid + step_matching 2단 조회. feature → func_step 조합 → step_id 확장.
- **INLINE** — inline_matching 중심, INLINE_<item_id> 컬럼 매칭 설명.
- **VM** — vm_matching feature_name → step_id 매핑. vmMeta 피처별 pill.
- **rulebook CRUD BE** — inline_matching, vm_matching 추가 (`/api/splittable/rulebook?kind=...`).

## v8.8.8 — 2026-04-21

인폼 새 폼 간소화 + fab_lot_id 자동 SplitTable 스냅샷 + 최근 루트 인폼 꼬리표 + .trash 숨김.

- **인폼 UI 간소화** — SplitTable 변경요청 체크박스 / 표 붙여넣기 / 가져오기 버튼 / 모듈 필터 칩 제거. fab_lot_id 만 넣으면 끝.
- **fab_lot_id 자동 SplitTable 스냅샷** — product+lot 설정 시 debounce 400ms 후 `/splittable/view` 자동 호출 → param×wafer actual/plan 이 embed_table 로 자동 첨부. 입력 해제 시 자동 탈착.
- **최근 루트 인폼 꼬리표 배지** — 각 CompactRow 가 해당 lot 의 splittable notes 를 집계해 `🏷 N` pill 표시. tooltip 에 샘플 3개.
- **파일탐색기 `.trash` · 시스템 폴더 숨김** — `/roots` 가 `.`/`_` prefix 디렉터리 제외.

## v8.8.7 — 2026-04-21

hive DB 대시보드 차트 에러 fix · 인폼 제품 3-way unified · fab_source 현재값 표시 · VM meta product 필터 · Rulebook CRUD BE · ET root flat.

- **dashboard/columns PATHS UnboundLocalError** — 함수 진입부로 `PATHS` import 올림, hive `1.RAWDATA_DB_*` 차트 동작.
- **인폼 제품 카탈로그 3-way unified** — 드롭다운 옵션 `constants ∪ products ∪ productContacts keys`. 담당자 패널 등록 제품도 즉시 노출.
- **담당자 추가 → 카탈로그 자동 등록** — saveContact / bulkAdd 이후 `/products/add` 자동. 싱크 보장.
- **SplitTable fab_source 현재값 표시** — placeholder 가 override resolve 결과 반영. `현재 적용: <경로> (자동/매뉴얼)`. orphan mlOnly state 제거.
- **VM meta product 필터** — `_build_vm_meta(product)` 가 `feature_name, product` 컬럼 지원. product 필터 + feature_name 매핑.
- **SplitTable Rulebook CRUD BE** — `/api/splittable/rulebook` GET/save (admin). product 스코프 부분 교체.
- **ET root flat 레이아웃** — `1.RAWDATA_DB_ET/PRODA/PRODA_YYYY-MM-DD.parquet`. `_build_fab_root.py` layout="flat" 지원.
- **_build_fab_root.py self-sufficient** — Base 없어도 Fab 만으로 재생성. product 컬럼 통합.

## v8.8.6 — 2026-04-21

이월 3건 shipping + 사내 DB 실환경 맞춤.

- **SplitTable paste 세트 팀 공유 BE** — `/api/splittable/paste-sets` (GET/save/delete/to-custom). LocalStorage-only → 팀 공용 JSON. `to-custom` 으로 CUSTOM 탭 승격. FE 인폼 paste 모달이 BE 우선 + LocalStorage 폴백.
- **SplitTable 태그 드로어 lot / param_global 확장** — scope 칩 (전체/wafer/param/lot/global) + LOT 노트/전역 태그 추가 버튼. 저장 draft 에 param 이름 인풋 분기.
- **회의록 SSE 동시편집 MVP** — `/api/meetings/stream?meeting_id=` SSE (in-memory pub/sub). save_minutes 발행 → FE EventSource 구독 → 편집 중이면 배너 + 외부 내용 불러오기/무시 버튼, 아니면 auto-reload. `?t=` fallback 허용.
- **step_matching.csv 제품 컬럼** — `_build_fab_root.py` 에 product col 추가 로직 통합. PRODA/PRODB 별 행 + AB100010/AB100020 은 PRODA 전용. `_build_knob_meta` 제품 필터로 해당 제품 rule 만 노출.
- **SplitTable Rulebook 뷰어** — 톱니 패널 하단에 `/knob-meta` 결과 카드 리스트 (rule_order/func_step/operator/ppid/step_ids/category).
- **DB 3-root 풍부화** — `1.RAWDATA_DB_FAB/INLINE/ET` × PRODA/PRODB × 2 date 파티션. prefix 별 컬럼 투영 (FAB_*/INLINE_*/ET_*).
- **레거시 `data/DB`·`data/Base` 삭제** — `admin_settings.data_roots.db=base=data/Fab` 고정.

## v8.8.5 — 2026-04-21

사내 실 DB 구조 대응 + 오버라이드 resolve 상태 UI + Dashboard 중복 제거 + Tracker UX + 개별 버전 배지 제거 + 그룹 admin 허용 + 담당모듈 UI 제거.

- **DB 상위폴더 `1.RAWDATA_DB*` prefix 인식** — `_list_db_roots()` 공용 헬퍼 (FAB 힌트 우선). `/fab-roots`, `/ml-table-match`, `_auto_derive_fab_source` 공통.
- **hive 파티션 (date=YYYYMMDD) 로딩** — `hive_partitioning=True` 로 파티션 컬럼 노출. 구버전 폴백.
- **ts_col 후보에 `date` 최우선** — 파일 내 timestamp 없어도 파티션 키로 최신도 판정.
- **오버라이드 resolve meta 풀세트** — `_resolve_override_meta()` 가 {fab_source, fab_col, ts_col, join_keys, scanned_files, row_count, sample, error} 반환. `/ml-table-match` + `/view` 응답 동봉. FE 톱니 카드 + 상단 배지로 상세 노출.
- **SplitTable 상단 fab_source 배지** — `🔗 1.RAWDATA_DB_FAB/PRODA · fab_lot_id@date (자동)` 한눈에 확인. 에러 시 빨간 `⚠ fab_source off`.
- **fab_source 드롭다운 재구성** — ML_TABLE(모 테이블) 제거, DB 경로 + TableMap 만. ML_TABLE만 보기 체크박스 제거. 빈 선택 시 자동매칭 경로 힌트.
- **테스트 데이터 `data/Fab/`** — `_build_fab_root.py` 로 ML_TABLE_*.parquet + `1.RAWDATA_DB_FAB/<PROD>/date=.../part_0.parquet` (V1/V2). `admin_settings.data_roots.db=.base=.../data/Fab`. 최신만 join 검증.
- **Dashboard 데이터소스 중복 제거** — `base_root == db_root` 상황에서 `base_file` 로 일원화. `1.RAWDATA_DB*` 하위 whitelist 우회. file-only dedup 로 ML_TABLE 이 base/root 두 번 뜨던 현상 제거.
- **Tracker LotTable 빈행 + 버튼** — 외부 `+ 행 추가` 제거. 표 맨 아래 빈행 클릭 또는 + 셀로 추가. 항상 표 형태 유지.
- **홈화면 개별 버전 배지 제거** — `FEATURE_VERSIONS={}` 빈 객체. 통합 v8.8.5 만 상단.
- **그룹 admin 멤버 허용 + 담당모듈 UI 제거** — `_is_blocked_member` 가 admin 통과 (사내 admin 은 이메일 보유). test substring 만 block. My_Admin 그룹 편집의 담당 모듈 섹션 제거.
- **파일탐색기 단일파일 삭제 admin 이중체크** — session token `role==admin` + body.username `_is_admin()` 동시 요구.

## v8.8.4 — 2026-04-21

S3 신호등 화살표 원 안 각인 · Tracker 이미지 붙여넣기 토큰 복구 · 인폼 제품 카탈로그 CRUD 통일 · 공용 메일그룹 인폼 노출+관리 z-index · 변경점 액션 담당자 제목 명시 · SplitTable 오버라이드 근본 재정리(ML_TABLE 한정+자동 매칭+ts 최신 join) + wafer 숫자정렬.

- **S3 신호등 화살표를 원 안에** — 14px 원 + 내부 흰색 ↓(다운)/↑(업) + 우측 작은 라벨. 한눈에 방향 판별.
- **Tracker 이미지 붙여넣기 깨짐 수정** — BE `_QUERY_TOKEN_PREFIXES` 에 `/api/tracker/image` 추가 + FE `withTrackerImageAuth()` 가 description_html 의 tracker image URL 에 `&t=` 자동 부착. 인폼 패턴과 동일.
- **인폼 제품 카탈로그 CRUD + 소스 통일** — 새 인폼 폼과 제품별 담당자 패널이 동일 unified 리스트 사용. 담당자 패널에 🗑 제거 + `+제품` 이 `/products/add` 로 카탈로그 즉시 등록. `+` 버튼 admin 제한 해제, `−` 제거 버튼 추가.
- **인폼 메일 그룹 드롭다운 + 관리 z-index** — `/api/informs/mail-groups` + `/api/mail-groups/list` 병합 표시 (`[공용]` prefix). MailDialog 내부 `관리` 버튼 + 서브모달 z-index 10001. Meeting MailGroupsEditor z-index 도 10001 로 통일.
- **변경점 액션 담당자 제목 명시** — calendar push_action_item title 앞에 `[담당:홍길동] ` prefix (owner 있을 때).
- **SplitTable 오버라이드 근본 재정리** —
  - `/products` 가 Base 의 `ML_TABLE_*.parquet` 만 반환 (DB hive/legacy 제거).
  - `_scan_product` 가 매뉴얼 override 없을 때 `ML_TABLE_<PROD>` → DB 상위폴더(FAB 우선) `<PROD>/` 자동 매칭.
  - ts_col / fab_col 자동 추론 — `out_ts/ts/timestamp/...`, `fab_lot_id/lot_id/...`.
  - 핵심: join keys 별로 ts_col desc 정렬 후 `unique(..., keep="first")` → 최신 레코드만 left-join.
  - `/ml-table-match` 가 auto_path / effective_fab_source / manual_override 동봉 → FE 톱니 패널에 자동 매칭 상태 카드 표시.
  - wafer_id 정렬 숫자-aware (view/CSV/XLSX) — `"10" < "2"` 문자열 오정렬 제거.
- **이월 (v8.8.5)** — SplitTable paste 세트 BE 공유 + CUSTOM 탭 / SplitTable 태그 FE 드로어 / 회의록 WebSocket 동시편집 (세 건 모두 신규 엔드포인트/프로토콜/대형 UI — 별도 배치).

## v8.8.3 — 2026-04-21

자동백업 최대 5개 · PageGear 전 탭 40px 우하단 통일 · FileBrowser Base 단일파일 admin 삭제 · 인폼 댓글/이력 엔드포인트 · 회의 공개범위 FE picker(patcher) · SplitTable/회의 동시편집 이월.

- **자동백업 최대 5개 유지** — `core/backup.py` `_DEFAULT_KEEP=14→5`, `_MAX_KEEP=5` 상한. `_cleanup_backups()` 공용 훅 (기동 시 1회 + 매 백업마다 + list 조회 시).
- **PageGear 전 탭 통일** — 40px · ⚙️ · 우하단. `position` prop 정규화로 Tracker/Meeting/Calendar/Dashboard 의 `bottom-left` 호출도 자동 수렴.
- **FileBrowser Base 단일 파일 admin 삭제** — 🗑 버튼 + `/base-file/delete` 가 base_root + db_root fallback, `.trash/` archive, 화이트리스트 방어.
- **인폼 댓글/이력 엔드포인트** — `routers/informs_extra.py` 신규. `/api/informs/{id}/comments` (CRUD) + `/api/informs/{id}/history` (status_history + edit_history 병합 타임라인).
- **회의 공개범위 FE picker (patcher)** — `_patch_meeting_v883.py` 에 create/edit 모달 + state + payload 주입.
- **이월 (v8.8.4)** — SplitTable 오버라이드/정렬/paste-세트/태그 드로어 + 회의록 WebSocket 동시편집.

# flow — CHANGELOG

주요 변경점만 간략히. 세부 내역은 `VERSION.json` 의 changelog 배열 참고.

## v8.8.2 — 2026-04-21

**S3 신호등 방향/제품·상위상속 · 대시보드 X/Y·Left Joins 버그 · 인폼 확인취소 backfill · 제품담당자 유저·그룹 일괄 · 달력/회의 공개범위 · TableMap 500·AWS제거·우클릭제거 · openpyxl 자동설치 · RootHeader 컴팩트**

- S3 신호등 방향(↓ 다운 / ↑ 업) pill 라벨 + 가독성 강화. 파일탐색기 사이드바 중복 신호등 제거.
- 파일탐색기 제품별 S3 신호등 추가: 제품 고유 설정 없으면 상위 DB 경로의 sync 상태를 자동 상속(점 표시 + tooltip).
- TableMap 의 S3StatusLight / AWS S3 동기화 명령 입력창 제거 → S3 관리 창구는 FileBrowser ⚙️ 로 단일화.
- 대시보드 Left Joins 선택 유지 + X/Y 드롭다운 안정화: jLabel lookup 수정, base_file join 지원, 공통 colUrl() 빌더, 컬럼 로딩/에러 배너.
- 인폼 확인취소(completed→received) 타임라인 backfill: `_upgrade` 에서 legacy status_history 의 prev 를 직전 상태로 채움 + 자동 '확인 취소' note. TimelineLog 인식 로직도 walking 기반으로 강화.
- 인폼 제품 담당자 — 유저/그룹 혼합 일괄 추가: `/api/informs/product-contacts/bulk-add`. 이단 picker 모달 (유저 체크 리스트 + 그룹 체크 리스트, 일괄 role 지정). admin/test 필터 + 중복 dedup.
- 달력 일반 이벤트 공개범위: EventCreate/Update 가 group_ids 수용, `/events` 가 가시성 필터 적용. 편집 폼에 그룹 칩 picker.
- 회의 공개범위 그룹 (백엔드): MeetingCreate/Update 에 group_ids + `_meeting_visible()` (admin/owner/creator 상시 가시). FE UI 는 차후.
- TableMap 저장/import HTTP 500 박멸: `save_json` default serializer (datetime/Decimal/UUID/Path/bytes/set), `save_table` try/except + traceback, polars rows 를 `_json_safe_rows` 로 사전 평탄화.
- TableMap 노드 우클릭 → 맵에서만 제거 (`/api/dbmap/nodes/unlink`). 원본 테이블 JSON/CSV 보존, 그래프 참조만 정리.
- openpyxl 자동 설치: setup.py extract 단계에서 import 실패 시 자동 pip install. 'python setup.py extract' 만으로 엑셀 기능 즉시 동작.
- 인폼 RootHeader 컴팩트: 완료 pill + 확인완료/해제 버튼 + 메일 pill + 이력 링크 모두 한 줄(30px) 로 수렴.

## v8.8.1 — 2026-04-21

**인폼 확인취소 이력 · 그룹 admin/test 필터 · 제품 카탈로그/Lot DB 드롭다운 · PageGear 통일 · 메일 본문 간소화**

- 인폼 확인취소(completed→received) 를 이력 타임라인에 기록 (prev 필드 + 빨간 '확인취소' kind).
- 그룹 정책 개편: admin 계정과 username 에 'test' 포함 계정은 members 풀에서 자동 제외. 일반 유저도 GroupsPanel 사용 가능 (My_Admin 에 groups 탭). 생성자 자동가입 제거. 신규 `/api/groups/eligible-users`.
- 인폼 제품 카탈로그 + RAWDATA_DB Lot 드롭다운. config.json 에 `products`, `raw_db_root`. 신규 `/api/informs/products/add|delete` + `/api/informs/product-lots?product=` (`{root}/1.RAWDATA_DB/{product}/` 서브폴더 스캔). 작성 폼에서 제품·Lot 모두 select.
- PageGear 아이콘 통일: ⚙️ + 40px + 우하단 default. 인폼 PageGear 우하단으로 이동.
- 메일 본문 간소화: 발송 요청자(hol) 블럭 제거. 제품 담당자는 `제품 담당자 : 이름 <email>, ...` 한 줄로 본문 상단 삽입. MailDialog 의 statusCode 필드 숨김.
- InformConfigPanel 확장: 제품 카탈로그 CRUD + RAWDATA_DB 루트 경로 저장 섹션.

## v8.8.0 — 2026-04-21

**인폼 메일 대개편 (시스템 발송 + 발송자 명시 + 제품 담당자 자동 첨부) · 대시보드 공개범위 그룹·X/Y UX · S3 sync 양방향 · SplitTable fab_source join · setup.py 데이터 보존 강화**

- **인폼 메일 — 시스템(Admin) 계정 발송 + 본문에 발송 요청자(현재 유저) ID 자동 명시.** `_build_html_body(sender_username=...)` 추가. MailDialog 상단 안내 배너로 "발송계정: 시스템 / 발송 요청자: {나}" 표기.
- **인폼 메일 본문 — 제품별 담당자 자동 첨부.** `/api/informs/product-contacts` (CRUD). target.product 의 담당자 표가 본문 끝에 자동.
- **인폼 사이드바 — 제품별 담당자 폴더블 패널.** 좌측 하단 "👥 제품별 담당자". 모든 로그인 유저가 +추가/수정/삭제 (감사 기록).
- **인폼 Lot 뷰 — SplitTable 노트 카드 (root_lot_id 키).** wafer/param/lot/param_global 노트가 인폼 Lot 페이지 상단에 자동 노출.
- **인폼 신규 — Ctrl+V 이미지 본문 inline 삽입 + 별도 첨부 버튼 제거.** markdown `![](url)` 로 즉시 본문에 들어감. Mail 첨부 후보로도 유지.
- **인폼 — SplitTable 가져오기 빈 응답 시 paste 모드 폴백.** 📋 "표 붙여넣기" 모달. 첫 줄 = 컬럼. 세트명 지정 시 LocalStorage 에 저장 → 재사용 가능 (Inform/SplitTable 양쪽 후속 공유 예정).
- **인폼 Lot drill-down UI 개선.** 모듈별 진행 요약 테이블 컬럼 "인폼" → "등록", 3개 데이터 열 균등 너비 (colgroup).
- **간트 → 이력 타임라인 명칭 통일 + Lot 검색 바.** 사이드바 모드 버튼명, TimelineLog 헤더에 🔎 Lot 검색 입력.
- **대시보드 X/Y 컬럼 선택 UX.** 컬럼 미로드 상태에서도 입력이 항상 노출 + 노란 배너 가이드 + 컬럼 개수 표기.
- **대시보드 공개범위 — 모두 / 관리자 / 특정 그룹.** visible_to=='groups' + 그룹 칩 다중 선택. BE 가 admin 차트 + group_ids 필터.
- **S3 동기화 방향 선택 (업로드/다운로드).** SaveReq.direction 추가. _build_cmd 가 upload 면 src/dst 스왑. 항목 테이블에 ⬇/⬆ 컬럼.
- **SplitTable column override — fab_source left-join 실제 적용.** `_scan_product` 가 `lot_overrides[product].fab_source` 의 DB 폴더(예: FAB/PRODA hive flat) 를 union 스캔, join_keys 로 left-join 해 fab_lot_id 보강. ML_TABLE_<PROD> 단독 스캔으로 fab_lot_id 가 비어 보이던 문제 해소.
- **setup.py 데이터 보존 가드 강화.** `_write` 가 `data/`, `holweb-data/` 외에도 경로 어디든 holweb-data 세그먼트 차단, HOL_DATA_ROOT/FABCANVAS_DATA_ROOT 절대 경로 안쪽 차단. users.csv·groups.json·mail_groups·admin_settings·product_contacts·meetings·splittable/notes 등이 재배포로 덮어써지는 사고를 다층 방지.

**이월 (v8.8.1+)**

1. RootHeader 컴팩트 — 확인완료/메일 배지를 카드 우측 한 줄에 묶기.
2. 회의록 WebSocket/SSE 동시편집 (본문·아젠다).
3. 회의·달력 공개범위 그룹 (mail_groups 재활용).
4. SplitTable 태그 FE 드로어 — lot + param_global scope (BE 완료, FE 만 남음).
5. 인폼 댓글/수정 이력 전용 엔드포인트.
6. SplitTable 측 paste 세트 공유 UI (현재 LocalStorage → 백엔드 공유 저장으로).
7. 회의록 동시편집 / 댓글 / 수정 이력.

## v8.7.9 — 2026-04-21

**달력 auto-sync bugfix · 회의별 고유색 · S3 타겟 다단계/프로파일 · 인폼 대개편(2단계 플로우 · 타임라인 · Ctrl+V · root_lot)**

- **[bugfix] 회의→달력 auto-sync 안 보이던 문제** — `except: pass` 가 sync 실패를 silent 하게 삼키고 있었음. 이제 실패 시 stderr 로그 + 응답 `calendar_sync.ok/error` 노출, FE 에서 alert. action_item id 가 매 저장마다 UUID 로 갈아치워지던 버그(id/text 기반 보존)도 수정.
- **결정사항 제목 = "N차 회의 결정사항: …"** — session.idx 포함. 액션아이템은 range bar 제거, 마감일 하루에만 📍 pin.
- **회의별 고유 색 순차 할당** — 15색 팔레트. 신규 회의만 새 색을 받고 기존 회의 색은 불변. legacy 는 첫 로드 시 created_at 순서로 백필. `meeting_ref.color` 로 달력 이벤트에 실려 FE 렌더.
- **달력 ↻ 새로고침 + 회의 필터 색 프리뷰**.
- **S3 타겟 다단계 경로** — `DB/1.RAWDATA/제품명` 같은 경로 허용 (Unicode + `/`). 전형적인 `..`/backslash 는 차단. FE datalist input.
- **S3 AWS 프로필(키) 선택** — item.profile = `aws … --profile <name>`. FE 드롭다운이 aws-config 프로필 목록을 로드.
- **인폼 플로우 2단계** — 접수(received) → 완료(completed). RootHeader 의 큰 FLOW 카드 대신 **✓ 확인 완료** 큰 버튼 하나.
- **인폼 등록 = product + lot_id 2필드만** — wafer_id 입력 제거. lot_id 는 root/fab 어느 쪽이든. `root_lot_id = lot_id[:5]` 자동 파생.
- **인폼 by-lot prefix-5 매칭** — `ABCDE` 검색 → `ABCDE01`, `ABCDE02` 등 앞 5자 일치 전부.
- **인폼 데드라인 필드 완전 폐기**.
- **인폼 본문 Ctrl+V 이미지 붙여넣기** — textarea onPaste → 자동 업로드.
- **인폼 간트차트 → 이력 타임라인** — 한 줄씩 시간순 (등록/확인/메일/댓글). Lot prefix-5 검색과 연동.
- **인폼 Lot drill-down 가독성** — 폰트 11 → 13, `루트/답글` 복잡 컬럼 대신 메일(최근 날짜) · 담당자 확인(완료 날짜) · 건수. CompactRow `[제품명] Lot` 포맷.

**이월 (v8.8+)**

1. 회의록 WebSocket/SSE 동시편집 (본문·아젠다).
2. SplitTable 태그 FE 드로어 (param_global/lot 편집 UI + wafer_id 그룹 필터).
3. 회의·달력 공개범위 그룹 (mail_groups 재활용) — 회의/결정/액션 가시성 제한.
4. 인폼 댓글·수정 전용 이력 엔드포인트 (현재는 status_history + mail_history + reply 조합으로 FE 측 합성).

## v8.7.8 — 2026-04-21

**달력 auto-sync + 액션 범위바 + 인폼 모듈 그룹핑 + SplitTable 태그 확장 + 카테고리 PageGear**

- **변경점 달력 회의 auto-sync** — 회의록 저장 시 모든 결정사항+액션아이템이 자동으로 달력 이벤트로 동기화 (수동 📅 push 불필요). 결정사항 = filled 단일 dot (회의 일자) · 액션아이템 = outline(dashed) range bar (회의일~마감). 달력에서 회의 auto-sync 이벤트 수정/삭제는 회의관리에서만.
- **달력 회의별 필터** — 헤더 드롭다운에 회의 목록 (GET /api/calendar/meetings). 전체 / 일반 이벤트 / 🗓 회의별 세 가지 뷰.
- **달력 이벤트 스키마 확장** — end_date (범위 이벤트) · source_type (manual/meeting_decision/meeting_action). 수동 이벤트도 종료일 지정 가능.
- **액션아이템 date-picker** — 회의록 편집에서 due 가 HTML date input.
- **인폼 Lot 모듈별 요약 테이블 + 그룹핑** — Lot drill-down 상단에 모듈별 인폼/메일 체크 테이블. 본문은 모듈 단위로 그룹핑되어 한 카드씩 정리.
- **인폼 PageGear 모듈 순서 편집** — Admin 이 톱니에서 모듈 추가/삭제/순서 조정. Lot 뷰 그룹핑이 이 순서 따름.
- **회의관리 PageGear 카테고리 편집** — Admin 이 톱니에서 카테고리 이름/색/순서/추가/삭제 통합 관리. 달력 팔레트와 동일 저장소 재사용.
- **인폼 Wafer 검색 모드 제거** — 제품/Lot drill-down 으로 흐름 단순화.
- **SplitTable 태그 스코프 확장** — 기존 wafer/param 에 lot + param_global (전역) 추가. param_global 은 product 내 모든 LOT 에서 공통 노출.
- **SplitTable ML_TABLE auto-match** — /ml-table-match 가 ML_TABLE_PRODA 에서 PRODA 추출 + DB root 의 하위 매칭 폴더 반환. fab_source override UI 가 이를 자동 제안.
- **SplitTable fab_source 상위폴더 옵션** — /fab-roots 가 DB 최상위 폴더(FAB/INLINE/ET) 목록. fab_source 드롭다운에 "상위폴더" 엔트리 추가.
- **SplitTable Product 중복 제거 강화** — Base/DB 양쪽에 있는 동명 parquet 이 Base 우선 dedup.

**이월 (v8.7.9+)**

1. 인폼 Lot 간트차트 (Lot 내 타임라인).
2. 인폼 job 표시 `[제품] Lot명` + fab_lot_id override + root_lot_id = fab[:5].
3. 인폼 Lot 발행 시 root_lot_id 또는 fab_lot_id 로 가능.
4. 회의록 동시편집 (WebSocket/SSE — 본문과 아젠다 동시 작성).
5. SplitTable 태그 FE 드로어 확장 — param_global / lot 편집 UI + wafer_id 그룹 필터.

## v8.7.7 — 2026-04-21

**공용 메일 그룹 · 차수 재발송 · LLM 어댑터 인프라 + 긴급 버그 패치 3건**

- **공용 메일 그룹 신설** — `routers/mail_groups.py` (data_root/mail_groups.json). 모든 로그인 유저가 생성/편집/삭제 가능한 공용 그룹. 한 유저가 여러 그룹에 속할 수 있고 (N:N), `extra_emails` 로 외부(vendor/partner) 이메일도 함께 관리. 회의 메일 발송 시 체크 한 번에 전부 확산.
- **차수 독립 메일 재발송** — `POST /api/meetings/session/send-mail` 신규. 이미 저장된 회의록을 건드리지 않고 동일 HTML 을 다시 보낼 수 있음. My_Meeting 회의록 헤더에 `📧 메일 발송` 버튼.
- **회의록 메일 옵션에 mail_group_ids 추가** — 작성/수정 폼과 재발송 다이얼로그 모두 공용 메일 그룹 chip 선택 + 관리 모달 진입 버튼 포함. `MinutesSave` 가 `mail_group_ids[]` 수용.
- **회의 주관자 정책 강화** — `/meetings/update` 에서 owner 변경은 **생성자(created_by) 또는 admin** 만 가능. 일반 owner 가 이양 후 되찾지 못하던 엣지 케이스 방지. 생성자는 항상 주관자 변경권 유지.
- **아젠다 created_at/updated_at 표시** — 아젠다 카드에 🕐 등록 / ✎ 수정 시각 인라인 표시. 스키마는 기존 필드 재사용이라 마이그레이션 불필요.
- **회의관리 간트 뷰 제거** — My_Meeting 탭의 `리스트/간트` 버튼 제거. 결정사항/액션아이템은 변경점 달력에 이미 나오므로 중복 뷰 정리. (ActionItemsGantt 코드는 유지 — 차기 달력 통합 시 재사용.)
- **사내 LLM 어댑터 infra (옵션)** — `core/llm_adapter.py` + `routers/llm.py` 신설. admin_settings.llm 에 api_url/headers/model/format/extra_body 저장. `is_available()` / `complete(prompt, system=...)` 두 함수가 전부. 100% 옵션이라 설정 미비 시 UI 가 is_available false 로 자동 숨김. 프롬프트는 단순하게, 수동 fallback 필수.
- **[bugfix] Admin "r is not a function"** — `App.jsx canAccess` 의 `userTabs.split(',')` 이 legacy localStorage 에 배열 저장된 경우 터지던 문제. `Array.isArray` / `typeof` 방어.
- **[bugfix] FileBrowser Base 단일 파일 중복** — `/base-files` 가 base_root 와 db_root 의 동명 파일을 중복 반환하던 문제. 파일명 기준 dedup + UI 의 `db` 소스 태그 제거 → Base 단일 파일은 이름당 한 번만 표시.
- **[bugfix] ML_TABLE parquet 미리보기** — `/base-file-view` 가 db_root 의 parquet 을 404 로 거부하던 규칙 제거. 이제 base-files 에 노출된 모든 CSV/Parquet 이 preview 가능.

**이월 (v8.7.8+)**

1. **변경점 달력 회의별 필터 + 액션 outline** — 달력에 특정 회의 드롭다운 필터. 결정사항은 filled, 액션아이템은 outline/dashed 로 시각 구분. (v8.7.4 의 status mirror 는 이미 있음.)
2. **액션아이템 due date-picker** — 현재 자유문자열 → HTML date input.
3. **SplitTable WF 메모** — (root_lot_id, wafer_id) 불변 키 기반 라벨/메모 저장소. fab_lot_id 는 rename 될 수 있으므로 키에서 제외.
4. **SplitTable 태그 시스템** — wafer별 태그(LOT 특정) + parameter별 태그(전역). wafer_id 그룹 필터 + LOT 노트. FB/SPC 태그 오버레이.
5. **SplitTable ML_TABLE → 제품 폴더 auto-match** — 테이블명에서 제품명(PRODA) 추출 → DB root 의 `*/PRODA/*` 경로 상위 폴더를 fab_source 로 자동 제안.

## v8.7.6 — 2026-04-21

**v8.7.5 이월 TODO 일괄 처리 + 긴급 bugfix 라운드**

- **회의 액션아이템 그룹 담당자 + 메일 발송** — `ActionItem.group_ids[]` 추가. `/api/meetings/minutes/save` 에 `send_mail` / `mail_to_users` / `mail_groups` / `mail_to` / `mail_subject` 수용. 저장과 동시에 아젠다 + 회의록 본문 + 결정사항 + 액션아이템을 HTML 한 장으로 조립해 사내 메일 API(multipart, 199명 한도, 본문 2MB) 로 발송. `_send_minutes_mail()` 이 그룹 멤버의 email 을 recursive resolve.
- **액션아이템 간트 차트** — My_Meeting 에 `리스트 / 간트` 탭. SVG 가로 바 차트로 모든 회의·차수의 due-set 액션아이템 타임라인. 오늘 세로선 + overdue 빨강 + status 색(pending/in_progress/done) + 월 tick. 바 클릭 → 해당 회의 상세.
- **인폼 제품 → Lot → Wafer drill-down + 그룹 권한** — `내 모듈` 모드 제거. `제품` 모드에 lot 카드 + wafer 칩 UI (lot 당 상위 5 루트 + 연결 wafer 배지). 백엔드 Inform 스키마에 `group_ids[]` + `_group_visible()` 필터. 그룹 비지정 = public.
- **TableMap ↔ Base CSV 동명 auto-link** — `/api/dbmap/tables` 응답에 `base_csv_match: {path, name, size, root}` 필드. TableMap display_name/name/id 와 같은 Base/DB root 파일(csv/parquet) 을 탐지. `/api/dbmap/tables/{id}/auto-load` 가 실제 rows 를 미리보기로 반환 (적용은 FE 가 별도 버튼으로 분리).
- **fab_source 드롭다운 확장** — SplitTable 톱니 override 의 fab_source 가 Base + DB 제품 디렉토리 + TableMap 테이블을 모두 합친 목록으로 변경. `[Base]` / `[DB/<root>]` / `[TableMap]` 태그로 구분 + dedup.
- **setup.py holweb-data 보존** — `_write()` 가드에 `holweb-data/` 경로 추가. backend/frontend 지우고 setup.py 재실행해도 DB 상위 폴더인 holweb-data 의 기존 데이터(DB/Base/informs/meetings/messages/users.csv 전부) 는 절대 덮어쓰지 않음.
- **[bugfix] 홈 버전 표시 8.7.3 고정** — Linux case-sensitive FS 환경에서 `/version.json` 핸들러가 `version.json`(소문자) 만 찾아서 실제 파일 `VERSION.json`(대문자) 을 못 읽고 하드코딩 fallback `{"version":"8.7.3"}` 을 반환하던 문제. 두 케이스 모두 시도하도록 수정.
- **[bugfix] FileBrowser DB hive/flat 인식** — whitelist 바깥 디렉토리여도 parquet/csv 가 존재하면 DB 섹션에 auto-detect 로 노출. DB 루트의 단일 parquet/csv 는 Base 섹션으로 통합 (기존 "root parquet" 사이드바 섹션은 비도록 하위호환 유지).
- **[bugfix] FileBrowser 톱니 스타일** — `⚙` → `⚙️`, 36×36 → 40×40, fontSize 16 → 18. SplitTable 톱니와 정확히 동일.
- **보안** — `_build_setup.py` INCLUDE_FILES 에서 `FabCanvas_domain.txt` 제거. `.gitignore` 에 `*domain*.txt` / `*도메인*.txt` 패턴 + 명시적 파일명 등재. 내부 도메인 지식이 public repo 로 재유출되는 것 차단.

**이월 (v8.7.7+)**

- **SplitTable 태그 시스템** — wafer별 꼬리표(LOT 특정) + parameter별 꼬리표(전역, 해당 파라미터에 영구 부착). wafer_id 그룹 필터, LOT/fab_lot_id 노트. FE/백엔드 신규 라우터 필요.

## v8.7.5 — 2026-04-21

**버그 수정 / UX 방어 라운드**

- **Admin 탭 전환 "r is not a function" 에러 수정** — `u.tabs` 가 legacy 데이터에서 array 로 저장된 기록이 있어 `.split(",")` 호출이 실패하던 문제. 정규화 헬퍼 `_tabsToArray()` 도입 + 탭 전체를 `TabBoundary` 에러 바운더리로 감싸 개별 서브패널 크래시가 전체 페이지를 막지 않도록. `InformConfigPanel` 내부에 정의돼 있던 inline `Section` 컴포넌트는 매 렌더마다 reference 가 바뀌어 focus loss 를 유발하므로 `renderSection` 함수형 렌더로 교체.
- **변경점 달력 TODAY 배지 잘림 최종 수정** — `absolute` 배지를 포기하고 day 숫자와 한 줄로 flex 배치. `whiteSpace:nowrap` + 작은 `TODAY` 칩 (fontSize 9). 절대 절대로 안 잘림.
- **FileBrowser CSV 분류 수정** — DB 루트에 있는 단일 CSV 는 Base 로 분류 (물리적 위치와 무관하게 의미적 Base). `base-files` 엔드포인트가 db_root 의 CSV 도 포함 + `source: db_root` 태그. 루트 Parquet 섹션은 CSV 제외 (parquet 전용).
- **Base 단일 파일 S3 신호등 + 양방향 분리** — Base 파일 리스트에 traffic-light dot 추가. `S3StatusLight` 컴포넌트가 다운로드(↓)/업로드(↑) 각각 별도 도트로 분리 표시. 백엔드 `/api/s3ingest/health` 에 `download_light`/`upload_light` 필드 추가 — history 의 `direction` (pull/push) 기반.
- **SplitTable Product / fab_source 중복 제거** — `find_all_sources()` (core/utils.py) 와 `/api/splittable/products` 둘 다 최종 return 직전 dedup. 같은 (source_type, root, product, file, label) 조합은 1회만 노출.
- **FileBrowser 톱니 좌하단 통일** — 기존 `bottom-right` → `bottom-left`. PageGear 와 동일한 톤/크기/그림자.

**신규 기능**

- **TableMap relation 자동 매칭** — 노드 간 화살표 연결 시 양쪽 테이블의 컬럼명을 case-insensitive 비교해 자동 매칭. 관계 편집 모달에 `🔍 자동 매칭` 버튼 + 매칭된 pair 를 chip 으로 보여주고 X 로 개별 제거 가능. (from_col, to_col 이 union 으로 병합됨.)
- **결정사항 단위 달력 push** — 회의 minutes.decisions 를 `[{id,text,due,calendar_*}]` 객체로 재설계. 각 결정사항 옆 `📅 달력 등록` 버튼 개별 push. `POST /api/meetings/decision/{push,unpush}` 엔드포인트 신규. Legacy 문자열 decisions 는 자동 객체화.
- **Dashboard X/Y 수식 입력 가이드 강화** — 기존 ColInput 자체가 searchable dropdown + 자유 수식 입력을 지원 (컬럼 선택 또는 `pl.col("a")/pl.col("b")*100` 수식 직접 타이핑). 가이드 텍스트 확장 — 멀티 Y / joined suffix / 수식 패턴 예시.
- **Base CSV 2종 추가** — `inline_matching.csv (step_id, item_id, item_desc)` + `vm_matching.csv (step_desc, step_id)`. SplitTable KNOB 메타와 동일한 방식으로 `_build_inline_meta()` / `_build_vm_meta()` 헬퍼 + `/api/splittable/inline-meta` + `/api/splittable/vm-meta` 엔드포인트. Admin > Base CSV 편집기에서 관리.

**이월 (v8.7.6+)**

- 액션아이템 그룹 담당자 지정 + 회의 본문·아젠다·액션아이템을 사내 메일 API 로 발송 (체크박스).
- 액션아이템 간트 뷰 (각 회의별 action_item 타임라인을 SVG 바 차트).
- 인폼 "내 모듈" 제거 + 제품 → LOT → wafer drill-down 재설계 + Admin 이 그룹별 가시성 권한 설정.
- Base CSV 파일명이 TableMap 테이블 이름과 일치 시 자동 데이터 linking.

## v8.7.4 — 2026-04-21

- **회의관리 차수(Session)** — 한 회의 아래 `1차 / 2차 / 3차 …` 를 쌓아가며 각 차수별로 독립된 scheduled_at / status / 아젠다 / 회의록. 기존 v8.7.2 데이터는 자동 마이그레이션 → 1차 세션으로 래핑. 엔드포인트 `POST /api/meetings/session/{add,update,delete}`.
- **반복 주기(Recurrence)** — 회의 메타에 `recurrence = {type: none|weekly, count_per_week, weekday, note}` 추가. 좌측 회의 카드에 `매주 1회/주 (월)` 요약.
- **액션아이템 → 변경점 달력 Selective Push** — 회의록 저장만으로는 달력에 안 뜨고, 각 action_item 옆 `📅 달력 등록` 버튼으로 명시 push. 등록된 달력 이벤트에 회의 카테고리 색상 + 등록 유저/시각. `POST /api/meetings/action/{push,unpush}` 신규. 회의·차수 삭제 시 연동 이벤트 cascade 제거.
- **달력 이벤트 meeting_ref + status 추적** — events.json 에 `status: pending|in_progress|done` + `meeting_ref: {meeting_id, session_id, action_item_id}`. `/api/calendar/event/status` 로 상태 변경 시 회의록 action_item 상태에 자동 mirror.
- **백업 범위 재정의** — `data_root` + `base_root` 두 소스. 대신 `*.parquet` 전역 제외 (대용량 DB parquet 제외). `logs/` 는 포함.
- **PageGear 좌하단 통일** — 전 탭 설정 톱니 기본 위치 `bottom-left` fixed. Dashboard/Tracker inline → 좌하단 floating. My_Meeting / My_Calendar 에 PageGear 배치.
- **달력 TODAY 배지 잘림 수정** — 셀 `overflow: visible` + 안쪽 좌표 (top:3,right:3) + pointerEvents:none.
- **시드 데이터 정리** — `meetings.json` 의 hol 샘플 회의 제거.
- **smoke 통과** — 회의 생성 → 차수 → 회의록 → action push → 달력 이벤트 → unpush/repush → 회의 삭제 → cascade 제거 전체 200 OK.

다음 릴리스(v8.7.5+): 결정사항 단위 push, 액션아이템 그룹 담당자 + 메일 발송 체크, 액션아이템 간트, 인폼 제품→LOT→wafer drill-down + 그룹 권한, FileBrowser S3 신호등 양방향.

## v8.7.3 — 2026-04-21

- **[critical hotfix] admin 라우터 import-time NameError** — v8.7.2 에서 추가된 `MailCfgReq.extra_data: Optional[Dict[str, Any]]` 가 `Any` 를 import 하지 않아 `backend/routers/admin.py` 가 import 시점에 실패. 결과적으로 Admin 탭 (사용자/설정/Activity Log/메일 API/Base CSV/백업) 전체가 동작 불가였음. `from typing import List, Optional, Dict, Any` 로 수정.
- **v8.7.2 단위기능 전수 점검** — 유저 10영역 (로그인/4h idle, Dashboard, Tracker, SplitTable 낙관적잠금·plan·컬럼CRUD, Inform 글·사진·댓글·데드라인·간트·모듈컬러·메일·작성자삭제, 달력 CRUD·오늘핀·동시편집, 회의관리 생성·아젠다·회의록, TableMap 유령컬럼·Tab이동·셀복사·검증·계보, S3 신호등, PageGear) + 관리자 7영역 (사용자 CRUD + password_hash 응답 스크럽, Activity Log 필터, 데이터 루트, 모듈/사유, 메일 API, Base CSV 편집기, 백업) 코드 경로 정합성 재검증. 상기 admin.py 이외 결함 없음.
- 보안 재확인 — `/api/admin/users` 응답 password_hash 제거(`_scrub_user`), admin 엔드포인트 `Depends(require_admin)` 적용, 세션 4h idle 서버측 만료, 이미지 `/api/informs/files/` 는 token fallback 허용하되 path traversal `resolve+relative_to` 유지, `data_roots` 는 admin 에게만 노출.

## v8.7.2 — 2026-04-21

- **TableMap UX 대폭 개선** — (1) 신규 테이블 생성 시 "이름없음" 유령 컬럼 제거 (초기 `columns=[]` + 저장 시 blank-name 필터). (2) 컬럼 정의 에디터에서 Tab/Shift+Tab 으로 필드 간 이동. (3) 셀 클릭 → 단일 선택, Shift+클릭·드래그로 범위 선택, 행번호 클릭으로 행 전체 선택, `Ctrl+C` → TSV 복사 (토스트 알림). (4) 테이블 바로 아래 `＋ 행 추가` 인라인 버튼 + 마지막 행에서 Tab/Enter 누르면 자동 새 행.
- **TableMap 테이블별 검증/정렬** — 컬럼별 필수·enum·정규식 제약 + 컬럼 선택 + 오름/내림/자연정렬. 저장 시 서버 `/api/dbmap/tables/save` 가 검증 후 실패 시 400 (`VALIDATION_FAILED` + 오류 리스트) — 프론트가 에러를 에디터 내부에 노출. 통과 시 정렬 적용 후 persist.
- **인폼 메일 보내기** — RootHeader 의 `✉ 메일 보내기` 버튼 → 사내 메일 API 로 HTML 본문 전송. 수신자 = (admin 설정의 모듈 그룹) + (개별 유저 email) + (추가 이메일). 최대 199명, 본문 2MB / 첨부 10MB 한도. `multipart/form-data` POST — `data` 필드 = `{content, receiverList:[{email,recipientType,seq}], senderMailaddress, statusCode, title, ...extra_data}` + `files` (인폼 이미지 첨부 선택). URL 이 `dry-run` 이면 실제 전송 없이 payload preview 반환.
- **Admin > 메일 API 탭 신규** — api_url / 헤더(JSON) / senderMailaddress / statusCode / extra_data(JSON) / 모듈 수신자 그룹 (그룹명 → 이메일 리스트) 관리. 저장은 `/api/admin/settings/save` 의 `mail` 블록.
- **신규 회의관리 탭** — 회의·아젠다·회의록 한 화면 관리. `/api/meetings/*`. 권한: 메타/회의록은 주관자/admin, 아젠다는 담당자/주관자/admin.
- **유저 email 필드** — `users.csv` 에 `email` 추가. Admin > 사용자 탭에서 인라인 편집. `/api/admin/set-email`.
- **`setup.py` 자체-추출 번들로 복원** — 전체 소스 트리를 gzip+base64 로 임베드한 단일 `setup.py`. `python setup.py` 한 줄로 어디서든 풀고 backend deps 설치 + frontend build. `data/` 하위는 절대 덮어쓰지 않음(이중 가드). 빌더는 `_build_setup.py` 로 일원화 — per-version `setup_v*.py` / `_build_setup_v*.py` 는 삭제.

## v8.7.1 — 2026-04-21

- **인폼 이미지 깨짐 수정** — `<img>` 가 세션 토큰 헤더를 못 실어 이미지가 401 로 깨지던 문제. 이미지 서빙 엔드포인트에 한해 `?t=<token>` 쿼리 fallback 허용 + FE `authSrc()` 헬퍼.
- **댓글/답글 타임스탬프 가시성** — 인폼 ThreadNode, Tracker 댓글에 🕐 아이콘 + 모노스페이스 pill 로 시간 prominently 표시.
- **Admin Activity Log 확장** — 로그인/로그아웃, 인폼 CRUD, 캘린더 CRUD, SplitTable plan 변경, admin 설정/유저 관리 등 서버측 주요 액션 자동 기록. `/api/admin/logs` 에 username/action/tab 필터 추가 + `/api/admin/logs/users` 신규. Admin 탭은 표 형태 + 유저 드롭다운 + action/tab 필터 바.
- **변경점 달력 오늘 핀** — 오늘 날짜 셀을 굵은 accent 테두리 + 글로우 + `📍 TODAY` 핀 배지로 강조.
- **인폼 데드라인 + 간트 차트** — 인폼 루트에 `deadline` (YYYY-MM-DD) 필드. 작성 폼/루트 헤더에서 설정/변경/해제. `POST /api/informs/deadline`. 신규 "간트" 뷰 모드 — 루트 인폼을 created → deadline 바 + 오늘 기준선 + overdue/임박 색상. CompactRow 에 데드라인 배지 노출.
- **모듈별 구분색 + 사유 태그** — 인폼 루트카드 왼쪽 5px 세로 바 + 모듈 칩 색상(모듈별 고정 팔레트, 14색). 본문 앞에 `[사유]` 컬러 프리픽스.
- **단일 `setup.py`** — 버전별 `setup_vXXX.py` 대신 루트 `setup.py` 하나에 `VERSION = "8.7.1"` 상수. install-deps / build-frontend / version / sync-version 서브커맨드.

## v8.6.4 — 2026-04-21

- S3 신호등 + TableMap 데이터 계보 + 낙관적 잠금 + 변경점 달력 (v8.6.0~v8.6.4 통합).

## v8.5.2 — 2026-04-21

- PageGear 공용 톱니 + Admin Base CSV 편집기.

## v8.5.1 — 2026-04-21

- Inform Log — wafer별 인폼 스레드 (댓글·재인폼).

## v8.5.0 — 2026-04-21

- User 그룹 필터 + 관심 LOT 워치리스트.

## v8.4.6 — 2026-04-21

- 세션 토큰 + 인증 미들웨어 + PBKDF2 비번 + RCE/traversal 차단.

---

이전 버전은 `VERSION.json` 참고.
