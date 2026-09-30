# 로그·업무 기록·캐시의 종류와 저장 위치

`AGENTS.md`의 **로그·저장소 작업 진입점**에서 시작한다. 이 문서는 파일을 찾는 목록이고,
진단·순환 변경·원격 보관·복원 절차는 [LOG_STORAGE_OPERATIONS.md](LOG_STORAGE_OPERATIONS.md)에 있다.
필요한 행의 writer와 reader만 확인한다. 보존 정책은 코드/관리자 설정을 다시 확인한다.

## 1. 경로를 먼저 구별하기

| 표기 | 의미·Windows 설치 기본값 | 구현 |
|---|---|---|
| `<APP_ROOT>` | setup.py를 푼 실제 설치 폴더 | `backend/core/paths.py`, 감시기 `scripts/flow_server.py` |
| `<DATA_ROOT>` | 사용자 기록·설정·로그·캐시. 기본 `D:\flow-data` | `FLOW_DATA_ROOT`, `core/paths.py` |
| `<DB_ROOT>` | 원천 DB. 기본 `D:\DB`. 원천은 읽기 전용으로 다룸 | `FLOW_DB_ROOT`, `core/roots.py` |
| `<API_LOGS>` | 항상 `<DATA_ROOT>/logs` | `PATHS.log_dir` |
| `<SUPERVISOR_LOGS>` | 감시기 기본 `<DATA_ROOT>/logs`. `FLOW_LOG_DIR` 또는 `--log-dir`로 별도 지정 가능 | `flow_server.py:_default_log_dir()` |
| `<TEMP_ROOT>` | 실행 계정의 OS 임시폴더. SYSTEM과 로그인 사용자의 경로가 다를 수 있음 | Python `tempfile.gettempdir()` |

**FLOW_LOG_DIR는 감시기 로그만 바꾼다.** 감사·자원·검색 로그를 포함한 API의 파일은 `<API_LOGS>`에 남는다.
Windows 설치 기본 드라이브는 `FLOW_STORAGE_ROOT`로 바꾸고, `FLOW_STORAGE_DEFAULT=0`으로 기본 경로 선택을 끈다.
명시한 root 환경변수와 관리자 데이터 루트 설정, 설치/개발 프로파일을 확인한다.
개발 체크아웃의 `data/flow-data`·`data/Fab`를 운영 서버 경로로 단정하지 않는다.

아래 `core/...`는 `backend/core/...`다.
`FB parts`는 `backend/app_v2/modules/filebrowser/router_parts/`,
`Split parts`는 `backend/app_v2/modules/splittable/router_parts/`다.

## 2. 감시기·API 운영 로그

| 위치 | 저장 내용 | writer → reader / 현재 보존 |
|---|---|---|
| `<SUPERVISOR_LOGS>/uvicorn.log`, `.1`~`.5` | 앱 stdout/stderr·경고·예외, 감시기 메시지. HTTP 접근 출력은 `FLOW_UVICORN_ACCESS_LOG=1`일 때 | `flow_server.py:RotatingLog` → control/log 도구. 20MiB 활성 1개+백업 5개, 명목 약 120MiB. 쓰기 단위로 조금 초과 가능 |
| `<SUPERVISOR_LOGS>/flow_restarts.log` | 기동·종료·재시작 시각/이유·코드 | `flow_server.py:_append_history()`. append, 별도 상한 없음 |
| `<SUPERVISOR_LOGS>/flow_supervisor.json` | 현재 PID·상태·재시작/헬스 정보 | `flow_server.py:_write_state()` → `--status`. 같은 상태 파일 교체 |
| `<API_LOGS>/activity.jsonl` | 사용자·행동·탭·상세 메모·시각 | `core/audit.py:record()/append_activity()` → 관리자 활동/통계 `activity_index.py`. **전체 보존, 줄/바이트 제한 없음** |
| `<API_LOGS>/downloads.jsonl` | 다운로드 대상·사용자·크기 등의 메타데이터 | 기능별 라우터 → 관리자 다운로드 이력. **전체 보존**. 다운로드 파일 본문 자체가 아님 |
| `<API_LOGS>/filebrowser_preview_access.jsonl` | 캐시 hit/miss·endpoint·key·path·시각 | `filebrowser_cache.py:_log_event()`. 정상 hit와 선두 계산 miss 기록, 일부 경합 fallback은 기록 없음. 별도 상한 없음 |
| `<API_LOGS>/search_timings.jsonl` | 검색 범위·대기/계산/총 시간·단계별 성능·호스트 표기 | `search_timing_log.py:record()` → 성능 집계. 최근 50,000줄, 500번 append마다 정리. 최근 화면은 200건 RAM ring도 사용 |
| `<API_LOGS>/cache_events.jsonl` | 캐시 생성·스캔·축출 등 이벤트 | `cache_event_log.py` → 캐시관리. 최근 4,000줄, 200번 append마다 정리 |
| `<API_LOGS>/ram_peak_samples_<server-key>.jsonl` | 프로세스 메모리 표본·최근 피크 근거 | `cache_event_log.py:_ram_peak_log_path()` → 피크 집계. 호스트 표기 hash별 최근 30,000줄, 500회마다 정리 |
| `<API_LOGS>/resource.jsonl` | 호스트/프로세스 CPU·RAM·디스크 상태 | `sysmon.py:collect_once()` → `history()`. 정상 5분 간격, 8,640줄 ≈30일. 부하 표본도 쓰므로 정리 전 초과 가능 |
| `<API_LOGS>/sysmon_state.json` | 일일 인공 부하 설정·최근 실행 결과 | `sysmon.py:get_schedule()/save_schedule()`. 현재 상태 갱신, 이력 append 아님 |
| `<API_LOGS>/boot_history.jsonl` | 기동·이전 비정상 종료/OOM 추정 | `sysmon.py:start_crash_forensics()` → 관리자 장애 진단. 최근 500줄 |
| `<API_LOGS>/run_state_api.json` | 현재 진행 요청·최근 느린 요청/서버 오류·메모리 상태 | `sysmon.py:_run_state_snapshot()`. 현재 heartbeat 교체. 요청 method/path/status/시간이며 본문 아님 |
| `<API_LOGS>/faulthandler_api.log` | 네이티브 오류 traceback | `sysmon.py:start_crash_forensics()`. append, 별도 순환 없음. 관리자 표시만 끝 6KiB로 제한 |
| `<API_LOGS>/product_dedup_scheduler.log` | 제품 목록 정규화 실행 시각·전후 건수 | `backend/scheduler.py:_append_log()`. 일일 실행 append, 별도 상한 없음 |

HTTP 모든 요청과 업무 감사 이벤트는 다르다. Uvicorn 접근 출력은 기본으로 끄며, 활성화하면 `uvicorn.log`에 들어간다.
감사 로그는 로그인·업무 변경·홈 챗 등 명시적 `audit.record()` 호출에서 기록한다.
`backend/app.py`의 inflight 추적은 현재 요청과 느린 요청/서버 오류 요약을 장애 진단에 남긴다.

`activity_index.py`는 `<TEMP_ROOT>/flow-activity-*/<hash>.sqlite3`에 감사 JSONL의 파생 인덱스를 만든다.
원본 교체/축소 시 재구축하며, 원본에서 빠진 과거 이벤트가 인덱스에 계속 보존되는 구조는 아니다.
직접 JSONL을 읽는 호출도 있으므로 큰 이력은 조회·집계·임시 디스크 비용을 늘릴 수 있다.

**줄 수 제한은 바이트 상한·기간 보장이 아니다.** 주기적으로 자르므로 잠시 초과할 수 있고,
1건 크기와 기록 빈도에 따라 실제 용량/보관 기간이 달라진다. 표시 목록 제한도 파일 보관 제한과 구별한다.

## 3. 홈 챗·LLM·SQL·차트 기록

| 위치 | 내용·writer/reader | 보존/주의 |
|---|---|---|
| `<DATA_ROOT>/home_conversations/<owner-hash>/<UUID>.sqlite3` | `routers/data_chat.py` + `core/chat_conversations.py`: 질문·답변·대화 상태·응답 tool 결과. 표 행·차트 데이터·리포트/다운로드 정보 포함 | 사용자/대화별 SQLite. 자동 기간 정리 없음. 목록·최근 결과·후속 질문이 읽는 **업무 데이터** |
| `<DATA_ROOT>/llm/minute_usage.json` 및 lock | `core/llm_usage.py`: 최근 provider 호출 시각·분당 사용량 | sliding window 상태를 갱신. 질문/답변 장기 로그 아님 |
| `<DATA_ROOT>/flowi_progress/<run-id>.jsonl` | `core/flowi_progress.py`: 공개 단계·상태·시간·짧은 설명, 진행 화면 polling | run당 최대 200개. 새 턴 시작 시 약 30분 지난 파일 정리. 질문·reasoning·SQL·표 행은 공개 필드에서 제외 |
| `<DATA_ROOT>/filebrowser_sql_execution_history.jsonl` | FB parts `00_bootstrap_and_single_file_cache.part.py`: 사용자·대상·SQL·행 수·실행 시간·오류 → SQL 이력 화면 | 최근 5,000줄. SQL 본문이 있으므로 취급 주의 |
| `<DATA_ROOT>/filebrowser_ai_sql_history.jsonl` | FB parts `50_query_language.part.py`: 질문/SQL·trace/action/preview 요약 → AI SQL 이력 화면 | 최근 500줄. 전체 LLM 원문 로그와는 다름 |
| `<DATA_ROOT>/filebrowser_ai_sql_feedback.jsonl` | FB parts `50_query_language.part.py`: 사용자 평가·정정·학습 참고 | 일반 JSONL 기본 최근 200,000줄 적용. 힌트 reader의 500/200건은 표시/참고 범위이며 파일 보관 한도가 아님 |
| `<DATA_ROOT>/chart_builder_history.jsonl` | FB parts `60_view_and_metadata_routes.part.py`: 차트 정의·source/join·집계·경고 → 차트 재사용 | 고정 차트 전체 + 미고정 최근 1,000개. 고정 수는 증가 가능 |

활성 홈 경로는 `routers/data_chat.py`다. 은퇴한 `routers/home_agent.py`나 번들 제외 LLM 모듈에서 고치지 않는다.
`core/llm_adapter.py`의 호출 요약은 model/provider·글자 수·지연·오류 중심이며,
별도 전체 prompt/provider 응답 디스크 로그를 찾지 못했다. 질문/답변과 반환 결과는 위 대화 파일에 영구 저장된다.

## 4. S3·운영 점검·기능별 이력

| 위치 | 내용·구현 | 현재 보존 |
|---|---|---|
| `<DATA_ROOT>/s3_sync_status.jsonl` | `core/s3_sync.py:_append_status()`: 지정 산출물 업로드 상태·key·크기·hash·오류 | append, 별도 trim 없음. 최근 표시만으로 파일이 제한되는 것은 아님 |
| `<DATA_ROOT>/s3_ingest/history.jsonl` | `routers/s3_ingest.py`: cp/sync 대상·방향·명령·결과·출력 tail | 최근 500줄 |
| `<DATA_ROOT>/s3_ingest/status.json` | 항목별 현재 전송 상태·최근 결과 | 같은 JSON 갱신. `config.json`/aws 폴더는 설정·자격증명으로 별도 취급 |
| `<DATA_ROOT>/ops_scan/latest.json`, `history.json` | `core/ops_scan.py`: 최근 운영 점검/진단 결과 | latest 갱신, history 최근 30개. schedule 파일은 상태/설정 |
| `<API_LOGS>/fab_matching_alerts_state.json`, `fab_matching_alerts_scanner.json` | `core/fab_matching_alerts.py`: 검사/스캐너 상태 | 업무 상태 갱신. 결정 이력은 별도 `<DATA_ROOT>/fab_matching_alert_decisions.jsonl` |

그 밖의 분석의뢰·그룹·위키·plan 변경 이력은 기능 데이터 옆에 있다. 확장자가 JSONL이라고
같은 보존 규칙을 적용하지 않는다. `core/utils.py:jsonl_append()` 일반 기본은 200,000줄이지만
감사/다운로드 예외, 호출별 max_lines, 독자 writer를 함께 확인해야 한다.

## 5. 용량이 커질 수 있는 업무 파일·파생 캐시

| 위치 | 내용·구현 | 정리/백업 판단 |
|---|---|---|
| `<DATA_ROOT>/uploads/`, `uploads/.trash/` | 사용자 업로드·이미지, 삭제 시 이동된 사본 | 업무 파일. 휴지통 이동만으로 용량이 줄지 않음 |
| `<DATA_ROOT>/file_versions/` | FB parts `20_validation_and_versioning.part.py`의 변경 전 원본·meta | 파일당 최근 20개. 매 10회 편집의 `DB BACKUP` 완전 복사본은 별도 누적 |
| `<DATA_ROOT>/splittable/knob_s0_daily/`, `<DATA_ROOT>/splittable/knob_s0_registry.json` | Split parts `25_s0_snapshot.part.py`: 일별 공정 경로·POR·당일 revision·S0 배정 근거 | immutable 업무 이력, 자동 기간 삭제 없음. Parquet가 자동 ZIP에서 제외됨 |
| `<DATA_ROOT>/auto_report/{jobs,runtime,output}/` | `core/auto_report.py`: 작업 JSON·실행 자산·ET/FAB/INLINE 중간 자료·PPT/HTML·실행 로그 | 자동 만료/정리를 찾지 못함. 같은 job 재준비는 runtime 교체. 반환 목록 500건은 디스크 제한 아님 |
| `<DB_ROOT>/Auto report/RUN/PPTX/<vehicle>/<job-id>/`, `HTML/...` | `auto_report.py:managed_run_root()`: 게시한 완성 PPT·HTML | DB 아래 관리 산출물이며 data_root 자동 ZIP 범위 밖. 원천 파일과 구별 |
| `<DB_ROOT>/Auto report/RUN/ET_HISTORY/<vehicle>/history.parquet` | `auto_report.py:history_file()/refresh_history_product()` | 기본 120일 범위 rolling snapshot. 설정별 기간·원천 가용 범위 확인 |
| `<DATA_ROOT>/cache/filebrowser/` | `filebrowser_cache.py:cache_dir()`: 프리뷰 응답 JSON/파생 결과 | 재생성 캐시. 메모리 캐시와 별개이고 응답 데이터 포함 가능 |
| `<DB_ROOT>/cache/` 및 source별 파생 캐시 | lookup·pivot·FAB·latest-lot 등. `ml_table_lookup.py`, Split parts, `lot_progress_cache.py` | reader와 재생성 계약을 확인. RAM 예산과 디스크 보관 정책은 별개 |
| `<TEMP_ROOT>/flow_downloads/` 또는 `FLOW_DOWNLOAD_JOB_DIR` | `core/download_queue.py:tmp_dir()`: 완료 다운로드 파일 | 기본 TTL 30분 및 다운로드 후 유예 정리, 기동 시 오래된 파일 sweep. 순간 공간 필요 |
| 사용자 홈 `.flow_backups/` | 설치 업데이트 `setup.py`의 경량 코드/설정 snapshot | 자동 데이터 ZIP과 별개. 시스템 드라이브 용량에도 포함 |

`DB BACKUP`은 코드가 DB root 쪽을 우선하고 쓰기 불가 시 data_root로 fallback하므로
실제 위치를 함수에서 확인한다. source 원천을 정리 대상으로 삼지 않는다.
현재 필요 없는 재생성 캐시와 과거 배정 근거·사용자 기록을 구별한다.

## 6. 자동 백업 범위와 취급

`core/backup.py` 기본은 48시간·최근 3개(설정 최대 5개)이며, 기동 후 유휴 시점에도 생성한다.
기준은 최근 횟수라 일정 날짜 범위를 보장하지 않는다. 대상은 `<DATA_ROOT>`이고 logs/uploads가 포함된다.
DB 원천·cache/tmp 등의 디렉터리·**모든 *.parquet**는 제외된다.
S0/Parquet 버전 이력이 포함됐다고 가정하지 않는다. 활성 SQLite는 단순 파일 ZIP 복사이므로
일관된 snapshot/복원 검증이 필요하다. 절차는 [작업 안내 5절](LOG_STORAGE_OPERATIONS.md#5-백업-범위와-업무-파일)을 따른다.

기록/대화/SQL/리포트에는 사용자 입력·파일 경로·식별자·데이터가 들어간다.
공개 저장소·외부 LLM 입력에 넣지 않고 현장 로컬에서 조사한다. 로그 종류별 근거 함수와
확인한 보관 설정만 작업 결과에 남긴다. 원격 전송은 사용자가 지정한 자료·목적지·범위에서 수행한다.
