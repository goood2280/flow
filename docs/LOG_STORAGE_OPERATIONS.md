# 로그·저장소 작업 절차 (OpenCode / oh-my-opencode)

규칙의 진입점은 설치 폴더의 `AGENTS.md`다. 종류·경로·writer/reader는
[LOG_STORAGE.md](LOG_STORAGE.md), 일반 코드 구조는 [CODEMAP.md](CODEMAP.md)를 참고한다.
이 문서는 실제로 진단하거나 보관 코드를 바꿀 때 필요한 순서만 정한다.

## 1. 작업을 작게 나누기

Gemma4 등으로 작업할 때 한 요청에서 한 종류의 기록을 처리한다. 다음 순서로 필요한 절만 읽는다.

1. `AGENTS.md`의 **로그·저장소 작업 진입점**에서 대상 행을 고른다.
2. `LOG_STORAGE.md`의 해당 경로와 보존 정책을 읽는다.
3. writer뿐 아니라 reader·백업·기존 파일 migration도 찾는다. `rg -n '함수이름|파일이름' backend scripts tests`를 사용한다.
4. 작은 수정안을 구현하고 합성 임시 root에서 검증한다. 여러 에이전트를 쓴다면 수정 파일을 서로 겹치지 않게 배정한다.
5. 변경한 파일, 유지한 이력, 검증 결과, 배포/복원 방법을 짧게 남긴다. 운영 설정과 사용자 자료를 코드에 포함하지 않는다.

전체 저장소·전체 로그·모든 대화를 한 번에 읽지 않는다. 보존 계약을 확인하지 않고 모든 JSONL에 동일한 제한을 적용하지 않는다.
로그 본문에 사용자·SQL·질문·파일 경로가 들어갈 수 있으므로 용량 조사에서는 내용 대신 파일 크기·건수·증가량부터 확인한다.

설치본에 문서/테스트가 없다면 **현장 수정 보존 → 서버 정상 종료 확인 → 설치 폴더에서
`python setup.py extract --all` → 추출 검증 → 재기동** 순서로 받는다. 이 명령은 문서만 추출하는 명령이 아니라
현재 설치 폴더의 코드도 덮어쓴다. `.local.bat`, DB/data 및 현장 소스 수정 보존은 AGENTS와 README의 업데이트 절을 따른다.

## 2. 용량 진단: 읽기부터

먼저 관리자 → 데이터 루트/자동 백업에서 실제 root를 확인한다. 기본 D: 경로, 개발 체크아웃,
감시기의 `FLOW_LOG_DIR`, SYSTEM 계정의 환경/임시폴더는 서로 다를 수 있다. 설정 파일 전체나 인증 키를 출력하지 않는다.

운영 설치 폴더에서 사용자 활동이 적은 시간에 실행한다. 다음은 코드 기본 경로를 사용하는 예시다.

```powershell
powershell -NoProfile -File scripts\windows\measure_flow_storage.ps1 `
  -DbRoot 'D:\DB' -DataRoot 'D:\flow-data' -BackupRoot 'D:\flow-backups' `
  -OutputDirectory 'D:\flow-storage-metrics'
```

`measure_flow_storage.ps1`는 파일 내용 없이 root별 크기/파일 수, 세부 폴더, 로그 파일명·크기·mtime,
C:/D: 여유를 JSON/CSV로 기록한다. 기본 결과 위치는 OS temp의 `flow-storage-snapshots`다.
장기 비교에는 위처럼 코드 폴더 밖의 명시적 결과 폴더를 쓴다. 결과 자료는 현장 로컬에 보관하며 공개 저장소에 추가하지 않는다.
symlink/junction은 따라가지 않고 접근 오류를 표시한다. 메타데이터 조회도 대량 파일에서는 디스크 I/O를 만든다.

일주일 이상 뒤 이전 JSON을 지정해 다시 실행한다.

```powershell
powershell -NoProfile -File scripts\windows\measure_flow_storage.ps1 `
  -OutputDirectory 'D:\flow-storage-metrics' `
  -PreviousSnapshot 'D:\flow-storage-metrics\flow-storage-이전시각.json'
```

root를 바꿨다면 두 번째 실행에도 같은 root 매개변수를 지정한다. 경로가 같고 양쪽 root가 존재하며
접근 오류가 없을 때만 일평균 순증가를 계산한다. `.` 합계에는 하위 폴더가 포함되므로 합계를 다시 더하지 않는다.
실행 중 쓰기/순환 때문에 결과는 원자적 스냅샷이 아니며, 감소는 삭제·교체·정리를 반영한 순변화다.

보고할 항목:

- 실제 root와 전체 디스크 여유, 접근하지 못한 범위.
- DB 원천, 재생성 가능한 캐시, 사용자 기록, 리포트 중간 자료, 백업을 각각 얼마나 쓰는지.
- 가장 큰 로그와 무제한 이력의 증가량. 발생량과 순환 후 남은 크기를 구별한다.
- 백업 한 개의 압축 크기와 실제 보관 개수. 새 ZIP을 먼저 만든 뒤 오래된 ZIP을 지우므로 작업 중 한 개 여유도 필요하다.

기간 추정은 `(관리 기준 용량 - 현재 전체 사용량) / 월 순증가량`이다. 월 순증가가 0 이하이거나
짧은 측정에서 큰 일회성 작업이 있었다면 고갈 시점을 단정하지 않는다. 코드 운영 점검은 디스크 80%에 주의, 90%에 높은 위험을 표시한다.

## 3. 진단 로그 순환을 바꾸기

| 요청 | 구현 시작점 | 유지할 동작 |
|---|---|---|
| uvicorn 보관량 | `scripts/flow_server.py`: `LOG_MAX_BYTES`, `LOG_BACKUPS`, `RotatingLog` | 활성 파일 포함 총량 계산. Windows 열린 파일 처리·동시 쓰기·재시작 확인 |
| 검색 타이밍/캐시 이벤트 | `search_timing_log.py`, `cache_event_log.py`: append/trim 함수 | 제한은 줄 수, trim은 주기적. 최근 화면·메모리 tail·기간 집계 유지 |
| 자원 표본 | `sysmon.py`: `collect_once()`, `_record_paver_sample()`, `history()` | 정기 표본과 부하 표본 writer를 모두 확인. 화면 폴링이 매번 로그를 늘리지 않도록 유지 |
| 프리뷰 접근 로그 | `filebrowser_cache.py`: `_log_event()` | hit/miss 집계와 기존 파일을 읽는 도구 확인. 새 한도는 코드 기본값에도 둠 |

일반 `jsonl_append()` 기본 제한이 있어도 `activity.jsonl`·`downloads.jsonl`는 명시적으로 제외한다.
감사 이력 기간 보관을 요청받았다면 다음 절의 원격/과거 조회 설계로 처리한다. 공통 함수 수정으로 이력을 몰래 자르지 않는다.
활성 파일을 단순 rename/복사/삭제하면 쓰기 중 행을 잃거나 열린 핸들을 놓칠 수 있다. writer의 lock 또는 정상 정지 구간을 사용한다.
진단 부하를 운영 서버에서 실행해 검증하지 않는다.

## 4. 기간별 원격 보관을 구현하기

현재 `s3_sync.py`는 정해진 산출물만 업로드하고, `s3_ingest.py`는 DB root 상대 경로의 cp/sync를 실행한다.
감사 이력·대화·업로드 전체를 기간별로 보관하고 로컬을 비우는 기능은 현재 없다. Google Drive 전용 연동도 없다.
NAS/Drive 경로와 S3는 완료된 보관본의 목적지로 사용할 수 있으나, 활성 SQLite/캐시 경로를 바로 원격으로 바꾸는 것으로 대체하지 않는다.

구현은 다음 단계로 나눈다. 원격 전송·로컬 정리는 사용자가 요청한 자료와 범위 안에서 수행한다.

1. 대상 종류와 기준 시각을 정한다. 파일 mtime과 내부 이벤트 시각을 혼동하지 않는다. 최근/전체 화면 조회와 보존 기간을 정의한다.
2. 쓰기가 끝난 일별/월별 파일을 만든다. 시작/끝 시각, 행 수, 바이트 수, hash, 원본 상대 경로, 형식 버전을 manifest에 남긴다.
3. 보관용 prefix/폴더에 업로드한다. 진행 중/완료 상태를 구별하고 전송 실패 시 원본을 유지한다.
4. 원격 크기·hash 확인과 표본 다운로드/복원을 통과한 보관본만 완료로 표시한다. multipart ETag를 항상 파일 MD5라고 가정하지 않는다.
5. 로컬 정리를 구현할 경우 재실행 안전성, 쓰기 중 행, 실패 복구를 확인한다. dry-run으로 대상/용량을 계산하고 과거 UI·다운로드 경로를 유지한다.
6. 다운로드 동기화가 정리한 자료를 다시 내려받지 않도록 경로/필터를 분리한다. 보관용 경로에는 mirror의 `sync --delete`를 적용하지 않는다.

감사 JSONL을 줄이면 `activity_index.py`의 파생 SQLite는 원본 교체/축소를 감지해 재구축한다.
그 결과 로컬에서 제거한 과거 이벤트는 관리자 집계에서 빠진다. 전체 보존 계약을 유지하려면 보관 인덱스·원격 조회 또는 복원 안내가 필요하다.
원격 저장 계층의 복원 시간·최소 보관 기간은 공급자 기능을 확인한다. 사내 S3 호환 서버에 AWS Glacier/Lifecycle이 있다고 가정하지 않는다.

## 5. 백업 범위와 업무 파일

현재 `core/backup.py`의 `_collect_sources()`는 `data_root`만 선택한다.
`_iter_files()`는 cache/tmp 등의 디렉터리와 모든 `*.parquet`를 제외하고 logs/uploads를 포함한다.
설정 기본은 48시간·최신 3개, 상한 5개이며 관리자 설정이 우선한다. 기동/수동 백업도 최신 횟수에 들어간다.
이 규칙은 설치 업데이트의 경량 snapshot(`setup.py`, 사용자 홈 `.flow_backups`)과 별개다.

| 대상 | 정리/백업 작업에서 확인할 점 |
|---|---|
| S0 일별·동일 날짜 revision Parquet | `25_s0_snapshot.part.py`의 immutable 이력. 현재 자동 ZIP에서 실제 Parquet는 제외. JSON registry만으로 완전 복구된다고 말하지 않음 |
| plan 변경 이력 | 변경 전후와 시점의 근거. 파일을 줄일 경우 과거 조회·기존 행 형식 유지 |
| 홈 대화 SQLite | `chat_conversations.py`의 owner별 대화 파일. 삭제/이동은 목록·최근 결과·후속 질문에 영향. `sqlite3.Connection.backup()` 또는 정상 정지 snapshot 등 일관성 방법을 선택하고 복원 검증 |
| 파일 변경본 | `file_versions`는 파일당 20개지만 Parquet 변경본도 ZIP 제외. 매 10회 편집 `DB BACKUP`은 별도 누적 |
| 업로드 휴지통 | `uploads/.trash` 이동도 디스크·백업 용량에 남음. 실제 삭제 여부와 복구 요구를 먼저 구분 |
| Auto report | data_root의 jobs/runtime/output과 DB root의 `Auto report/RUN/PPTX`, `HTML`, `ET_HISTORY`를 모두 확인. 목록 반환 500건은 디스크 제한이 아님. 성공 결과와 필요한 중간 파일을 구별 |
| ET_HISTORY·lookup/pivot/FAB 캐시 | ET_HISTORY의 과거 조회 역할과 재생성 캐시를 구별. DB 원천을 지우지 않고 현재 reader·재생성 경로 확인 |

자동 ZIP은 활성 파일을 그대로 ZIP에 쓰며 일관된 SQLite snapshot API를 사용하지 않는다.
백업 누락을 고칠 때 `*.parquet` 제외를 전체 해제하면 대형 파생 캐시까지 포함될 수 있으므로 업무 이력을 명시적으로 선택한다.
복원은 임시 root에서 JSON 파싱·SQLite `PRAGMA integrity_check`·Parquet 읽기·과거 S0 조회·첨부/결과 파일 연결까지 검증한다.

## 6. 변경별 검증과 전달

| 변경 | 관련 기존 검사 |
|---|---|
| 감사 저장/인덱스 | `tests/test_activity_index.py`, `test_admin_activity_dashboard.py`, `test_history_preservation.py` |
| 대화 저장/이관 | `tests/test_chat_conversations.py` |
| S0 이력/백업 포함 | `tests/test_splittable_s0_snapshot.py`, `test_split_s0_history.py`; ZIP 실제 멤버와 복원도 별도로 검사 |
| 파일 변경본 | `tests/test_filebrowser_save_version_diff.py` |
| S3 대상/전송 | `tests/test_filebrowser_s3_regressions.py`; 원격 실계정 대신 mock 사용 |
| 자원 로그/부하 | `tests/test_sysmon_paver.py`, `test_http_compression_and_jsonl_tail.py` |

이 목록이 모든 요구를 검증하는 것은 아니다. 변경한 보존/복원 규칙이 기존 테스트에 없으면 그 동작의 합성 검사를 추가한다.
운영 root를 쓰지 않고 임시 `FLOW_DATA_ROOT`·`FLOW_DB_ROOT`·`FLOW_PROD=0`을 사용한다.
기존 기록 읽기, 쓰기/보관 경계, 전송 실패, 재실행, 날짜 경계, 실제 복원을 확인한다.
PowerShell 측정 도구는 임시 폴더로 용량·누락 경로·junction 제외·이전 snapshot 비교를 검사한다.

문서 변경은 링크·경로·함수·상수와 번들 포함을 확인하면 된다. 프런트 변경이 있을 때만 해당 npm 검사 범위를 추가한다.
다른 설치본에 전달할 변경은 개발 체크아웃에서 `python _build_setup.py`로 번들을 재생성한다.
VM의 현장 수정은 AGENTS의 적용 절차를 따른다. 공개 원격에 현장 로그·측정 JSON·백업·연락처·키를 올리지 않는다.

## 7. OpenCode에 줄 작업 입력 예시

**읽기 전용 진단**

> AGENTS.md의 로그·저장소 작업 진입점을 따라 실제 root와 로그 종류를 확인해줘.
> LOG_STORAGE_OPERATIONS.md 2절 방식으로 가장 큰 폴더/로그, 보존 상한, 순증가를 조사하고,
> 파일 본문·토큰을 출력하거나 데이터를 삭제/업로드하지 말고 근거 파일과 진단 결과를 정리해줘.

**특정 로그 순환 변경**

> AGENTS.md부터 읽고 [대상 로그]의 writer/reader/기존 보존 계약을 확인한 뒤 [요청한 보관 규칙]을 구현해줘.
> 기존 파일 형식·최근 화면 조회를 유지하고, 감사/다운로드 전체 이력은 건드리지 말아줘.
> 합성 임시 root로 경계와 재실행을 검증하고 반영 방법을 설명해줘.

**원격 보관 기능 구현**

> AGENTS.md부터 읽고 [대상 기록]을 [기간] 기준으로 [사용자가 지정한 목적지]에 보관하는 기능을 만들어줘.
> LOG_STORAGE_OPERATIONS.md 4·5절의 manifest/무결성/실패 복구/과거 조회를 반영해줘.
> 먼저 mock 목적지와 임시 root로 검증하고 실제 전송은 요청된 자료 범위로 한정해줘.
