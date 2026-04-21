# flow — CHANGELOG

주요 변경점만 간략히. 세부 내역은 `VERSION.json` 의 changelog 배열 참고.

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
