# Flow DB/AI 및 입력 데이터 운영 가이드

이 문서는 GitHub에서 읽는 별도 기술 문서다. setup.py에 포함하지 않는다. 실제 DB, 제품 정보, 담당자 명단, 내부 지식 원문은 커밋하지 않는다. 아래 값은 모두 합성 예시이며 실제 운영값으로 교체해야 한다.

2026-09-11 작업 소스 기준이다. 특히 DB/AI 소비 코드와 Inline·Mapfile 동작에 아직 배포되지 않은 작업 폴더 변경이 포함되어 있으므로, 문서를 올리는 것만으로 운영 설치본에 기능이 추가되지는 않는다. 운영 코드에 아래 참조 함수가 있는지 먼저 확인한다.

## 1. 파일을 둘 위치

여기서 `DB`는 고정 상대 폴더가 아니라 Flow에 설정된 `PATHS.db_root`다. `PATHS.base_root`도 같은 위치다.

| 위치 | 목적 | 만드는 방법 |
|---|---|---|
| `DB/AI/manifest.json` | 승인한 지식 릴리스 선택 및 해시 검증 | 아래 생성 예시 사용 |
| `DB/AI/releases/<version>/*.json` | AI가 참고할 구조화된 사실 | 출처 확인 후 JSON 배열 작성 |
| `DB/confidential/inline_shot_matching.csv` | 제품·step·item별 좌표 테이블 선택 | Excel에서 문자열 열로 작성 후 UTF-8 CSV 저장 |
| `DB/confidential/inline_map_settings.json` | 테이블별 subitem과 ET shot 좌표 연결 | TEG Inline map 화면에서 저장 권장 |
| `DB/mapfile/dev/`, `DB/mapfile/prod/` | 개발·양산 설비 Mapfile 검사 입력 | 제품 코드 접두어가 있는 파일 배치 |

AI 지식, Inline 좌표 설정, Mapfile 검사 데이터는 소비 경로가 다르다. 아래 CSV나 알람 설정을 `DB/AI`에 넣어도 기능 설정으로 적용되지 않는다.

## 2. DB/AI 패키지 작성

Flow는 파일을 읽는 소비자다. 별도 지식 작성 도구는 필수 조건이 아니며, 다음 형식의 패키지를 직접 만들어도 된다. 지식 생성 도구를 쓰더라도 최종 파일과 해시 계약은 같아야 한다.

### 2.1 JSON 레코드 형식 (schema_version 2 권장)

각 JSON 파일의 최상위는 **배열**이다. 모든 파일을 통틀어 `id`가 유일해야 한다.

| 공통 필드 | 값 규칙 |
|---|---|
| `id` | 비어 있지 않은 문자열, 릴리스 전체에서 유일 |
| `kind` | `product`, `measurement_binding`, `term`, `rule` 중 하나 |
| `created_at`, `updated_at` | 시간대가 있는 ISO 8601 문자열. 예: `2026-09-11T09:00:00+09:00` |
| `status` | 정확히 `active`. 미해결·철회 데이터는 발행 배열에서 제외 |
| `source_ids` | 비어 있지 않은 출처 ID 문자열 배열. 실제 출처 원장은 내부에 보관 |

| kind | 추가 필수 필드 | 작성 원칙 |
|---|---|---|
| `product` | `canonical_name`: 문자열, `aliases`: 문자열 배열 | canonical_name은 조회 대상 DB 제품명과 맞춘다. 별칭은 명확한 것만 등록 |
| `measurement_binding` | `product_id`, `concept`, `step_id`, `item`: 비어 있지 않은 문자열 | product_id는 같은 릴리스에 있는 product의 id를 참조. 제품별 측정 의미를 분리 |
| `term` | 현재 로더에는 별도 필수 필드 없음 | 운영 규약으로 `name`, `definition`을 작성 |
| `rule` | 현재 로더에는 별도 필수 필드 없음 | 운영 규약으로 `name`, `description`을 작성. 실행 코드·권한 부여가 아닌 참고 지식 |

예: `releases/demo-v1/knowledge.json`

```json
[
  {
    "id": "product:demo", "kind": "product",
    "canonical_name": "DEMO_PRODUCT", "aliases": ["데모 제품"],
    "created_at": "2026-09-11T09:00:00+09:00",
    "updated_at": "2026-09-11T09:00:00+09:00",
    "status": "active", "source_ids": ["source:demo-spec"]
  },
  {
    "id": "binding:demo:width", "kind": "measurement_binding",
    "product_id": "product:demo", "concept": "선폭 측정",
    "step_id": "001200", "item": "DEMO_CD",
    "created_at": "2026-09-11T09:00:00+09:00",
    "updated_at": "2026-09-11T09:00:00+09:00",
    "status": "active", "source_ids": ["source:demo-spec"]
  }
]
```

### 2.2 manifest 만들기

최상위 키는 `schema_version`, `release`만 허용한다. release의 키는 `version`, `approved`, `documents`만 허용한다. 각 documents 항목은 `path`, `sha256`만 허용한다.

```json
{
  "schema_version": 2,
  "release": {
    "version": "demo-v1",
    "approved": true,
    "documents": [
      {"path": "releases/demo-v1/knowledge.json", "sha256": "실제 파일 바이트의 SHA256으로 교체"}
    ]
  }
}
```

위 manifest의 해시 자리표시는 그대로 사용할 수 없다. 다음 스크립트를 **운영 DB가 아닌 패키지 작성 폴더**에서 실행하면 `AI/releases/demo-v1/*.json`의 실제 해시를 계산한다. 문서를 먼저 작성하고 내용을 검토한 뒤 실행한다.

```python
from pathlib import Path
import hashlib
import json

root = Path("AI")
version = "demo-v1"
files = sorted((root / "releases" / version).glob("*.json"))
assert 1 <= len(files) <= 32, "JSON 파일 1~32개가 필요합니다"
manifest = {
    "schema_version": 2,
    "release": {
        "version": version,
        "approved": True,
        "documents": [
            {"path": p.relative_to(root).as_posix(),
             "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in files
        ],
    },
}
(root / "manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
)
```

이 스크립트는 해시 생성용이며 레코드 검증기를 대신하지 않는다. JSON은 UTF-8(BOM 없이)로 저장한다. Excel CSV의 BOM 허용과 혼동하지 않는다.

### 2.3 제한·반입·확인

- version: 영문·숫자로 시작하고 이후 영문·숫자·`.`·`_`·`-`만 허용, 1~64자.
- manifest 최대 65,536바이트, 문서 1~32개, 문서당 최대 131,072바이트, 문서 전체 최대 524,288바이트.
- JSON 파일당 최대 1,000레코드. 레코드당 `json.dumps(record, ensure_ascii=False)` 결과 최대 4,000자.
- 경로는 `releases/<version>/` 아래 상대 경로와 `.json` 확장자 사용. 역슬래시, `..`, 콜론, 절대 경로, 심볼릭 링크는 금지.
- `approved`는 JSON boolean `true`여야 한다. 문자열 `"true"`는 사용할 수 없다.
- 호환 schema_version 1은 같은 manifest 구조에 `.md` 파일을 사용한다. 신규 데이터는 제품·측정 관계를 검증할 수 있는 v2로 작성한다.

1. 검토한 파일을 새 버전 디렉터리에 완성한다. 이미 발행한 버전 파일은 덮어쓰지 않는다.
2. 새 디렉터리를 `DB/AI/releases/`로 복사하고 바이트·해시를 확인한다.
3. 기존 manifest를 별도 보관한 뒤 새 manifest를 같은 파일시스템의 임시 파일에서 원자적으로 교체한다. 파일 복사 도중 먼저 manifest를 바꾸지 않는다.
4. Flow 홈의 시맨틱 DB/AI 상태에서 `ready`, 예상 버전, 문서 수를 확인한다. `missing`은 manifest 없음, `invalid`는 형식·승인·경로·해시·레코드 관계 등을 확인한다. 하나라도 실패하면 전체 지식을 읽지 않는다.
5. 별칭 질문과 제품별 측정 질문으로 의도한 제품·step·item이 선택되는지 확인한다. 실패하면 이전 manifest로 되돌린다(이전 릴리스 파일도 보관해야 한다).

문서 수는 레코드 수가 아니라 manifest 파일 수다. 참고 문맥은 상위 최대 8개 항목·총 12,000자 범위로 선택된다. 모든 지식이 매 질문마다 모델에 들어가거나 자동 학습되는 구조는 아니다. source_ids의 실재 여부·내용 정확성은 운영자가 검토해야 한다.

## 3. teg_check 그룹과 .map 이상 알람

### 설정 순서

1. Flow 그룹 관리에서 `TEG_CHECK` 그룹을 만들고 실제 수신자의 username을 멤버로 추가한다. 그룹명 비교는 앞뒤 공백 제거·대소문자 무시이므로 `teg_check`도 인식한다. 같은 이름의 중복 그룹은 만들지 않는다.
2. TEG 제품 설정에서 vehicle과 product_code를 등록하고 검사 정답지·형상·필수 대상 설정을 준비한다. 제품 코드는 파일 선택 접두어다.
3. 예를 들어 product_code가 `DEMO01`이면 `DB/mapfile/dev/DEMO01_example.map`에 배치한다. dev/prod 폴더 중 하나라도 있으면 루트 파일은 탐색하지 않으므로 혼용하지 않는다. 폴더가 둘 다 없을 때만 `DB/mapfile/` 루트가 호환 경로다.
4. TEG Mapfile 화면에서 파일 발견·검사 결과를 확인하고, 운영 스케줄러 실행 후 수신자의 앱 알림을 확인한다.

스케줄러는 **서버 로컬 시각 06:00·18:00**에 background owner에서 실행한다. 시작 즉시 실행하거나 파일을 놓는 즉시 알람하는 구조는 아니다. 화면의 검사/강제 재검사는 결과 조회 경로이며 알림 발행은 `run_mapfile_traffic_once()` 경로에서 수행한다.

### 알람 판정 및 재시도

| 결과 | 처리 |
|---|---|
| 정상 green, 오류·누락·경고 없음 | 조용히 유지 |
| red/yellow/orange, 검사 오류·비정상 상태, 필수 대상 누락 | 그룹 멤버에게 앱 이상 알림 |
| gray 등 판정 불가·알 수 없는 신호등 | 확인이 필요한 결과로 알림 |
| 그룹 없음/멤버 없음 | 경고 기록, 멤버 설정 후 다음 검사에서 재시도 |
| 발행 예외 | 실패 기록, 다음 검사에서 재시도 |
| 개인 알림 설정으로 억제됨 | suppressed로 기록. 동일 버전은 설정을 켜도 자동 재전송되지 않음 |

알림에는 제품·파일명·신호등·누락/오류 근거와 첫 문제 요약, 해당 TEG 검사 화면 링크가 들어간다. 이메일/SMS 전송 설정이 아니다.

중복 방지는 `filename + signature + recipient` 기준이다. 현재 작업 소스의 signature는 파일명과 내용 SHA256이므로 내용이 같은 재다운로드는 새 버전이 아니다. 그룹에 신규 멤버를 넣으면 다음 검사에서 그 멤버는 별도 수신 대상이 된다. 같은 파일 버전에 대한 정답지/설정 변경만으로 재알림을 보장하지는 않는다.

상태는 `PATHS.data_root/teg_map/mapfile_alert_state.json`, 검사 캐시는 같은 폴더의 `mapfile_traffic_cache.json`에 저장된다. 일반 운영에서 중복 방지 파일을 지우지 않는다. 알림이 없으면 그룹·멤버·개인 설정, 제품 코드와 파일 접두어, dev/prod 배치, 스케줄러 로그의 warnings/failed/suppressed/duplicates를 확인한다. 파일이 발견되지 않아 결과가 없는 경우는 개별 이상 파일 알람과 다르며, 파일 부재 알람을 보장하지 않는다.

## 4. inline_shotmatching 열 설계와 값

### 4.1 테이블 선택 CSV

권장 파일명은 `DB/confidential/inline_shot_matching.csv`. 한 행은 **제품 + step_id + item_id에 적용할 테이블 하나**다. 측정값이나 shot 좌표를 이 CSV에 넣지 않는다.

| 권장 열 | 형식 | 입력값 |
|---|---|---|
| `product` | 문자열 | 실제 원천 제품 키. 명시적으로 입력 권장 |
| `step_id` | 문자열 | 실제 공정 step ID. `001200` 같은 앞자리 0 보존 |
| `item_id` | 문자열 | 실제 INLINE item 식별자. 표시용 번역명이 아님 |
| `map` | 문자열 | 아래 JSON의 `table_name`과 같은 이름 |

```csv
product,step_id,item_id,map
DEMO_PRODUCT,001200,DEMO_CD,DEMO_PRODUCT_NORMAL
DEMO_PRODUCT,001300,DEMO_THK,DEMO_PRODUCT_NORMAL
```

Excel에서 모든 열을 텍스트로 설정한 뒤 입력하고 CSV UTF-8로 저장한다(BOM 허용). 필수 값이 비면 규칙이 건너뛰어지며 와일드카드 규칙으로 처리되지 않는다. 키는 앞뒤 공백 제거·대소문자 무시로 비교한다.

호환 열 이름: product는 `prod/vehicle/product_id/device`, step_id는 `step/process_id/stepid/step_no`, item_id는 `item/rawitem_id/itemid/param_name/parameter`, map은 `map_name/matching_table/table_name/mapname/map_file`도 읽는다. 신규 파일은 위 권장 열을 사용한다.

제품별 `DEMO_PRODUCT_inline_shotmatching.csv`도 지원한다. product가 없으면 파일명에서 추정하지만 공용 파일에는 반드시 product를 넣는다. 탐색은 confidential CSV → credential의 matching/shot CSV → DB 루트 기본 파일(없으면 inline_matching.csv) → 루트 제품별 파일 순이다. 같은 규칙을 여러 파일에 중복 작성하지 않는다. 규칙 목록은 먼저 읽은 키를 사용하지만 좌표 로더는 충돌 테이블을 함께 읽을 수 있어 서로 다른 좌표가 섞일 수 있다.

### 4.2 좌표 테이블

TEG Inline map 화면에서 제품을 선택하고 실제 shot을 선택한 다음 각 위치에 원천 `subitem_id`를 입력한다. 테이블명과 저장 comment를 입력해 저장하면 `DB/confidential/inline_map_settings.json`에 반영된다. confidential 파일이 없으면 기존 `DB/credential/inline_map_settings.json`을 읽는 호환 경로가 있다.

```json
{
  "version": 1,
  "tables": [
    {
      "table_name": "DEMO_PRODUCT_NORMAL",
      "vehicle": "DEMO_PRODUCT",
      "comment": "합성 예시: 실제 map 좌표로 교체 필요",
      "shots": [
        {"subitem_id": "SITE_A", "shot_x": 0, "shot_y": 0},
        {"subitem_id": "SITE_B", "shot_x": 1, "shot_y": 0}
      ]
    }
  ]
}
```

| 값 | 설계 규칙 |
|---|---|
| `table_name` | 제품을 포함해 전역에서 구별되는 이름 권장. 저장 시 대소문자를 무시한 같은 이름을 교체 |
| `vehicle` | 해당 좌표 테이블의 TEG 제품 조인 키 |
| `subitem_id` | 실제 측정 위치의 원천 식별자. 화면 표시 이름으로 임의 번역하지 않음 |
| `shot_x`, `shot_y` | 제품 map의 ET shot 좌표계 숫자. mm·픽셀·측정값이 아님. 화면 좌표를 사용하고 임의 부호 반전하지 않음 |
| `comment` | 변경 이유. UI 저장 시 필수 |

좌표는 유한한 수여야 하며 NaN/Infinity/빈칸은 사용할 수 없다. UI 저장은 소수점 6자리로 정리하고 같은 좌표의 중복을 제거하며 제품 map에 없는 좌표를 거부한다. 위 `(0,0)`, `(1,0)`은 실제 제품에서 유효하다는 뜻이 아니다.

`name`은 과거 subitem_id 호환 필드다. 신규 입력은 subitem_id를 사용한다. `avg/mean/median/std/min/max/q1/q3` 등 사전 집계 subitem은 매칭에서 제외된다. 한 subitem을 여러 좌표에 연결하면 여러 매칭 행이 생길 수 있으므로 의도한 관계인지 확인한다.

map 이름은 파일명·확장자를 제거한 별칭으로도 조회하지만 실제 이미지를 읽는 기능은 아니다. 혼동을 피하려면 CSV map과 JSON table_name을 정확히 맞춘다. 정의되지 않은 테이블은 missing_tables에 남고, 테이블에 없는 subitem이나 잘못된 좌표는 결과에서 빠진다.

### 4.3 반영 확인

1. 원천 product/step_id/item_id/subitem_id의 실제 문자열을 확인한다.
2. UI에서 제품 map과 테이블의 shot 위치를 대조한다.
3. 선택 CSV를 저장하고 매칭 규칙 목록의 available, shot_count를 확인한다.
4. 실제 INLINE 원천 한 행이 기대한 shot_x/shot_y로 연결되는지 확인한다. 평균 등 통계 행은 제외되어야 한다.
5. 테이블명 오타, 누락 subitem, 중복 규칙을 점검한다. CSV·설정 파일 변경 시각/크기는 캐시 서명에 사용되므로 값을 바꾼 뒤 이전 결과가 남지 않는지 확인한다.

## 5. 유지보수 시 확인할 소스

- `backend/core/ai_semantic.py`: `_load`, `_validate_record`, `prompt_context`
- `backend/core/inline_coordinates.py`: `find_matching_rulebook_files`, `load_matching_rules`, `load_coordinate_mapping`
- `backend/core/teg_map.py`: `_clean_inline_table`, `save_inline_map_table`
- `backend/core/mapfile_traffic.py`: `list_mapfiles_for_product`, `file_signature`
- `backend/core/mapfile_alerts.py`: `_abnormal_reasons`, `publish_mapfile_alerts`
- `backend/core/mapfile_traffic_scheduler.py`: `run_mapfile_traffic_once`, `_seconds_until_next_run`

문서는 저장소 루트에 두고 `_build_setup.py`의 INCLUDE_FILES에 추가하지 않는다. `docs/`는 번들 수집 대상이므로 이 문서를 그 아래로 옮기지 않는다. 이 가이드의 발행은 문서만 커밋·push하며 setup.py를 재생성하지 않는다.
