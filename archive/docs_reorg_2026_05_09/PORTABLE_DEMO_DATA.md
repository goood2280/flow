# Portable Demo Data

Flow 데모 데이터는 사내 이동과 외부 시연을 모두 고려해 세 층으로 유지한다.

## 권장 구성

| 층 | 목적 | 저장 방식 |
|---|---|---|
| Synthetic fixture | 설치 직후 성능/화면 재현 | `scripts/perf_flow_data_paths.py`로 생성. Git에는 원본 대용량 parquet를 넣지 않음 |
| Public semiconductor data | 실제 반도체 문맥 시연 | 다운로드/변환 스크립트와 출처만 보관. 원본은 사용자가 직접 받음 |
| Internal sample | 사내 설득/검증 | 익명화 후 별도 volume 또는 object storage. repo 밖 `FLOW_DB_ROOT`에 배치 |

이 구조를 쓰면 코드는 그대로 옮기고, 데이터만 환경별 root에 꽂아 넣을 수 있다.

## 공개 데이터 후보

| 데이터 | 적합한 Flow 화면 | 장점 | 주의점 |
|---|---|---|---|
| SECOM | 파일탐색기, 스플릿테이블 schema/feature preview | 실제 반도체 제조 feature table. 1,567행, 591 feature, pass/fail label | lot/wafer/process 이력 구조가 약해 Flow 컬럼으로 변환 필요 |
| WM-811K / LSWMD | 웨이퍼맵, 파일탐색기, 결함 패턴 데모 | 실제 wafer map 811,457개, lot/wafer index와 failure type 포함 | 이미지/행렬 중심이라 SplitTable plan-vs-actual과 직접 맞지 않음 |
| PHM 2016 CMP | VM, trace, 장비 time-series 데모 | wafer CMP 장비 time-series와 removal-rate target 포함 | 원천 컬럼명이 익명화되어 있고 다운로드/라이선스 확인 필요 |

참고 링크:

- UCI SECOM: https://archive.ics.uci.edu/dataset/179/secom
- WM-811K 설명/원본 링크: https://github.com/makinarocks/awesome-industrial-machine-datasets/blob/master/data-explanation/WM-811K%28LSWMD%29/README.md
- WM-811K Kaggle mirror: https://www.kaggle.com/datasets/qingyi/wm811k-wafer-map
- PHM 2016 CMP challenge: https://phmsociety.org/conference/annual-conference-of-the-phm-society/annual-conference-of-the-prognostics-and-health-management-society-2016/phm-data-challenge-4/

## 변환 원칙

공개 데이터는 Flow의 운영 컬럼에 억지로 맞추지 말고 adapter parquet를 만든다.

| Flow 컬럼 | 공개 데이터 매핑 예 |
|---|---|
| `product` | dataset 이름 또는 recipe/tool group |
| `root_lot_id` | lot name, wafer group, synthetic lot key |
| `lot_id` / `fab_lot_id` | lot + stage/run 조합 |
| `wafer_id` | wafer index 또는 wafer identifier |
| `tkout_time` | timestamp가 없으면 ingest timestamp 또는 run order |
| `KNOB_*`, `INLINE_*`, `VM_*` | SECOM/CMP numeric feature, removal-rate target, wafer-map summary feature |

데모에서 중요한 것은 원본 그대로의 연구 정확도가 아니라 Flow가 기대하는 탐색/필터/스키마/lot 연결 동작을 안정적으로 재현하는 것이다.

## 성능 fixture

20MB/50MB 단일 parquet와 partitioned DB 성능은 아래 명령으로 재현한다.

```bash
python3 scripts/perf_flow_data_paths.py --sizes-mb 20 50
```

이 스크립트는 임시 `FLOW_DB_ROOT`, `FLOW_DATA_ROOT`를 만들고 종료 시 삭제한다. 원본 데이터를 포함하지 않아 사내/외부 전달 시 라이선스나 보안 부담이 없다.
