# flow 사내 반입 소프트랜딩

이 문서는 flow를 사내 서버로 옮길 때 지켜야 할 실행 포트, 데이터 루트, 백업/롤백 기준을 정리한다.

## 포트 정책

| 서비스 | 포트 | 용도 |
|---|---:|---|
| flow | 8080 | 사용자가 접속하는 실제 앱 |
| OmniHarness | 8081 | 하네스/점검/활동 뷰어 |

flow 실행 예:

```bash
cd /config/work/flow-fast-api
uvicorn app:app --host 0.0.0.0 --port 8080
```

실행 후 브라우저에서 `/runtime-roots.json`을 열면 현재 프로세스가 어떤 checkout, `data_root`, `db_root`, `base_root`를 보고 있는지 바로 확인할 수 있다.

OmniHarness 실행 예:

```bash
cd /config/work/OmniHarness/backend
uvicorn app:app --host 0.0.0.0 --port 8081
```

## 루트 정책

사내 표준 경로:

```text
/config/work/flow-fast-api/              # flow 코드
/config/work/sharedworkspace/flow-data/  # 사용자 데이터 루트
/config/work/sharedworkspace/DB/         # DB 루트
```

환경변수:

```bash
export FLOW_APP_ROOT=/config/work/flow-fast-api
export FLOW_DATA_ROOT=/config/work/sharedworkspace/flow-data
export FLOW_DB_ROOT=/config/work/sharedworkspace/DB
export FLOW_ADMIN_PW='<초기 admin 비밀번호>'
```

`base_root` 는 이제 별도 경로가 아니라 `db_root` 와 같은 경로를 반환하는 호환 alias다. 단일 rulebook, `ML_TABLE_*.parquet`, `features_*.parquet`, matching CSV도 DB 루트 최상단에서 읽는다.

## data_root 보존 원칙

`FLOW_DATA_ROOT` 아래는 사용자가 만든 운영 데이터다. 시스템 업데이트, setup.py 재실행, 프론트엔드 빌드가 이 디렉터리를 삭제하거나 덮어쓰면 안 된다.

대표 보존 대상:

- `users.csv`, `shares.json`
- `groups/`, `informs/`, `meetings/`, `messages/`, `calendar/`
- `tracker/`, `splittable/`, `dbmap/`, `product_config/`, `reformatter/`
- `uploads/`, `logs/`, `sessions/`, `s3_ingest/`
- `admin_settings.json`, `settings.json`

앱은 시작 시 현재 `FLOW_DATA_ROOT`를 바로 읽는다. 이미 데이터가 있으면 새 seed를 만들지 않고 기존 파일을 사용한다. `admin_settings.json`의 `data_roots.db` 변경은 resolver가 요청 시점에 다시 읽어 새 요청부터 반영된다.

## 업데이트 절차

1. 업데이트 전 백업을 만든다.

```bash
cd /config/work/flow-fast-api
python3 scripts/preflight_internal.py --write-probe --backup-now
```

2. 코드를 교체한다. `FLOW_DATA_ROOT`는 코드 디렉터리 밖 `/config/work/sharedworkspace/flow-data`로 둔다.

3. setup.py를 쓸 경우에도 data_root는 보호 대상이다.

```bash
python3 setup.py extract
python3 setup.py install-deps
cd frontend && npm run build
```

4. 서버를 재기동하고 preflight를 다시 실행한다.

```bash
python3 scripts/preflight_internal.py --write-probe
```

5. 사용자가 접속하는 서버가 최신 코드인지 확인한다.

```bash
curl http://localhost:8080/version.json
curl http://localhost:8080/runtime-roots.json
```

## 백업과 롤백

기본 백업 위치는 `data_root/_backups`다. 예를 들어 사내 기본값은:

```text
/config/work/sharedworkspace/flow-data/_backups/
```

Admin UI의 `Admin > 백업` 또는 `Admin > 데이터 루트 > 자동 백업`에서 즉시 백업과 롤백을 실행할 수 있다.

롤백은 선택한 zip을 `data_root`로 복원한다. 복원 직전 현재 상태는 자동으로 `pre-restore` 백업으로 한 번 더 저장된다. DB 루트 최상단 파일은 공유 원천 데이터에 영향을 줄 수 있으므로 API 옵션을 명시하지 않으면 복원하지 않는다.

API 예:

```bash
curl -X POST http://localhost:8080/api/admin/backup/restore \
  -H 'X-Session-Token: <admin-token>' \
  -H 'Content-Type: application/json' \
  -d '{"filename":"flow_data_20260426_120000_manual.zip"}'
```

## Preflight 기준

preflight는 다음을 확인한다.

- flow 포트 기준이 8080인지
- OmniHarness 포트 기준이 8081인지
- active project가 `flow`인지
- `db_root == base_root`인지
- 파일탐색기에서 보이는 DB 루트가 resolver의 `db_root`와 같은지
- `data/DB`, `data/Base` 같은 병렬 로컬 DB 루트가 남아 있지 않은지
- `1.RAWDATA_DB_FAB_LONG`, `1.RAWDATA_DB_INLINE_LONG` 같은 구 side root가 남아 있지 않은지
- `data_root`가 존재하고 기존 상태 파일을 읽을 수 있는지
- backup 목록 조회와 restore 함수가 가능한지
- `_build_setup.py` 보호 가드가 `flow-data`를 기준으로 되어 있는지

권장 명령:

```bash
python3 scripts/preflight_internal.py --write-probe
python3 scripts/preflight_internal.py --write-probe --backup-now
```

앱 서버가 떠 있는 경우에는 일반 smoke도 같이 확인한다.

```bash
python scripts/smoke_test.py
```
