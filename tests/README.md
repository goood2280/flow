# tests

```bash
python -m pytest tests
```

전체 실행에 약 7~8분 걸린다. 특정 영역만 볼 때는 파일을 지정한다.

## 기준선 (2026-07-26)

**1,022 통과 / 45 실패.** 45개는 알려진 실패이며 아래에 이유가 있다. **이 숫자가 늘어나면 새 회귀다.**

## 이 디렉터리의 내력

테스트 92개가 2026-07-20 커밋 `bb0737b5`("Reduce repo to README.md and setup.py only")에서 유실됐고, 소스는 복구됐지만 테스트는 복구되지 않아 3개만 남아 있었다. 2026-07-26에 히스토리(`bb0737b5^`)에서 되살렸다.

되살린 테스트는 v9.4.x 시점에 작성된 것이라, 그 뒤 v9.5.x에서 정당하게 바뀐 동작을 아직 옛 기대값으로 확인한다. 그게 아래 45개다.

## 알려진 실패 45개

| 파일 | 개수 | 이유 |
|---|---:|---|
| `test_splittable_lot_candidates.py` | 19 | 캐시 이름 변경(`splittable_roots`·`lookup_cache_roots` → `split_table_cache_fast`), `latest_*()`에 `allowed_roots` kwarg 추가 |
| `test_filebrowser_sql.py` | 9 | ML_TABLE lookup 캐시 경로/스키마 변경 |
| `test_resource_guard.py` | 4 | heavy-request 레인 구조 변경 (`X-Flow-Heavy-Request-Group` 헤더 제거) |
| `test_et_time.py` | 3 | PGM(pt) 라벨 접미사 규칙이 "항상 붙임" → "재측정 있을 때만"으로 바뀜 (의도된 변경 — [../docs/features/et-time.md](../docs/features/et-time.md) 참조) |
| `inform/test_inform_ui_contract.py` | 3 | JSX 원문 문자열 assert — 화면 리팩터로 깨짐 |
| `test_feature_contracts.py` | 2 | 같음 (JSX 원문 문자열 assert) |
| `test_s3_ingest_status.py` | 2 | S3 상태 이력 포맷 변경 |
| `test_pivot_cache_builder.py` | 1 | 청크 크기 축소 정책 변경 |
| `test_latest_lot_partitions.py` | 1 | fab lot index sweep 트리거 조건 변경 |
| `test_splittable_view_hitpath.py` | 1 | **단독 실행하면 통과** — 전체 실행 시 테스트 간 상태 누수 |

### 대표적인 오해 사례

`test_lookup_cache_build_uses_lazy_partition_sink`는 "빌드 중 `.collect()` 금지"를 검사하는데, 현재 구현은 root_lot_id **청크 단위**로 나눠 쓰면서 매 청크마다 `gc.collect()`와 메모리 체크를 한다 — 청크 목록을 만들려면 distinct root를 한 번 collect해야 한다. **지금 구현이 옛 테스트가 지키려던 것보다 메모리 안전하다.** 실패를 회귀로 읽지 않는다.

`test_feature_contracts.py`와 `inform/test_inform_ui_contract.py`는 JSX 소스에 특정 문자열이 있는지를 assert한다. 리팩터에 무조건 깨지는 방식이라, 고칠 때는 문자열 대신 동작을 검사하도록 바꾸는 편이 낫다.

## 정리할 때

45개를 줄이려면 하나씩 **현재 동작이 맞는지 확인한 뒤** 기대값을 고친다. 통째로 지우면 커버리지 의도까지 사라진다. 위 표의 개수를 함께 갱신한다.

## 주의

테스트는 `data/Fab/`를 로컬 fallback DB로 쓴다. 전체 실행 후 `git status`에 `data/Fab/**` 수정이 500건 넘게 뜨는 건 이 때문이며, 커밋 대상이 아니다.
