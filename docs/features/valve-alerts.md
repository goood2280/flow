# 매칭알람 (valve_alerts)

탭 `valve` / 라벨 "매칭알람". Valve 파이프라인이 발행한 알람을 읽어 엔지니어 판정을 받고, 그 판정을 룰북 CSV에 반영한다. Valve · flow · 룰북 CSV가 **순환 구조**로 물려 있다.

## 순환 구조

Valve 쪽 짝은 **Valve 앱**의 `backend/core/alert_store.py`다 (flow 저장소에는 없다 — 별도 앱).

1. **Valve 파이프라인 실행 → 알람 발행** → S3 `valve-alerts/pipeline/{vehicle}.json`
   - `unmatched_step` — vehicle_matching에 없는 step (`um|{vehicle}|{step_id}`)
   - `ro_ppid` — knob 매핑에 없어 raw ppid로 남은 건 (`ro|{vehicle}|{step_id}|{ppid}`)
2. **flow(이 화면)가 알람을 읽어 판정 화면에 표시**
   - `ro_ppid` → 카테고리 기입 → `ppid_knob.csv`에 해당 feature의 **다음 Rule 넘버(`R{n+1}`)**로 추가
   - `unmatched_step` → vehicle/step_id/step_desc 확인 → `Vehicle_matching.csv`에 행 추가
   - 반영 시: 원자적 저장 + 파일 버전 스냅샷(`file_versions`) + matching cache 갱신 + S3 업로드(`flow/artifacts/matching/…`) + 판정 이력(jsonl) 기록 + ack 상태 갱신
3. **Valve `csv_sync`가 내려받아 재실행 → 알람 자연 소멸**

## Owns

- 알람 목록 (ack/판정 이력 병합)
- 판정 이력 조회
- ro_ppid 분류와 unmatched_step 매칭 반영
- ack 상태 — 보류(미확인예정) / 반영불필요 / 해제(active)
- 전송 설정 (S3 bucket/prefix, local_root, 폴링) + 저장소 연결 상태
- 수동 폴링 (신규 알람 벨 알림 체크)

## Does Not Own

- Valve 파이프라인 실행 자체 — Valve 앱 소유
- 룰북 CSV의 전체 편집 UI — 이 화면은 **알람에서 출발한 행 추가**만 한다
- 알람 억제 정책의 최종 판단 — Valve의 `SUPPRESS_STATUSES`가 기준

## Code Entrypoints

| Layer | Path |
|---|---|
| Frontend page | `frontend/src/pages/My_ValveAlerts.jsx` |
| Backend router | `backend/routers/valve_alerts.py` |
| 소비/반영 로직 | `backend/core/valve_alerts.py` |
| 폴링 스케줄러 | `backend/core/valve_watch.py`, `backend/core/valve_alerts.py` (`start_scheduler`) |
| 설정 | `data/flow-data/valve_alerts.json` |
| 알람 소스 | S3 `valve-alerts/pipeline/{vehicle}.json`, ack는 `valve-alerts/pipeline/ack.json` |

## API

| Method | Path | 용도 |
|---|---|---|
| GET | `/api/valve-alerts` | 알람 목록 |
| GET | `/api/valve-alerts/decisions` | 판정 이력 |
| GET·PUT | `/api/valve-alerts/config` | 전송 설정 |
| POST | `/api/valve-alerts/classify-ppid` | ro_ppid → `ppid_knob.csv` 다음 Rule로 추가 |
| POST | `/api/valve-alerts/match-step` | unmatched_step → `Vehicle_matching.csv`에 추가 |
| POST | `/api/valve-alerts/ack` | 보류/반영불필요/해제 |
| POST | `/api/valve-alerts/poll` | 수동 폴링 |

**쓰기 권한: admin 또는 page manager(`valve`).**

## Guardrails

- **"반영완료"는 정보 표시용이다.** Valve가 재실행으로 해소할 때까지 알람이 active로 남아 있는 것이 정상 동작이다 — 이걸 버그로 보고 강제로 닫지 않는다.
- 억제 대상은 Valve의 `SUPPRESS_STATUSES`(미확인예정 / 반영불필요)뿐이다.
- Rule 넘버는 해당 feature의 마지막 + 1로만 붙인다. 중간 번호를 재사용하지 않는다.
- CSV 반영은 원자적 저장 + 버전 스냅샷을 함께 한다. 셋 중 하나만 하면 되돌릴 수 없다.
- 전송 계층은 실환경 boto3 S3 / 로컬 데모는 `local_root`(Valve fake_local 버킷 폴더) 직접 읽기·쓰기다. 로컬 경로를 코드에 하드코딩하지 않는다.
- worker(개발서버) 역할에서는 이 스케줄러가 뜨지 않는다 — 메일/알림 발송은 운영 서버가 소유한다 ([../WORKER_DISPATCH.md](../WORKER_DISPATCH.md)).

## Verify

```bash
git diff --check
```

```bash
cd frontend && npm run build
```
