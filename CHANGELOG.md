# flow — CHANGELOG

주요 변경점만 간략히. 세부 내역은 `VERSION.json` 의 changelog 배열 참고.

## v8.7.2 — 2026-04-21

- **신규 회의관리 탭** — 회의 생성(주관자·예정 일시), 아젠다 추가(담당자·링크), 회의록(본문·결정사항·액션 아이템) 한 화면에서 관리. 좌측 회의 목록(상태 필터/검색) + 우측 상세. nav '회의관리' 탭 모든 유저 노출.
- **신규 라우터 `meetings.py`** — `/api/meetings/{list, create, update, delete, agenda/add, agenda/update, agenda/delete, minutes/save}`. 권한: 회의 메타·회의록은 주관자/admin, 아젠다는 담당자/주관자/admin. 회의록 저장 시 status auto → completed.
- **`setup.py` 자체-추출 번들로 복원** — v8.7.1 의 단순 runner 패턴 폐기. 전체 소스 트리를 gzip+base64 로 임베드. `python setup.py` 한 줄로 어디서든 풀고 deps + frontend build 까지 진행. data/ 보존 가드 유지.

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
