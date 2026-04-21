# flow — CHANGELOG

주요 변경점만 간략히. 세부 내역은 `VERSION.json` 의 changelog 배열 참고.

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
