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

### 3.1 Mapfile 신호등 상세 판정

검사는 TEG의 중심점 하나가 아니라 **좌하단 앵커 `(x,y)`와 폭·높이로 만든 사각형 전체**를 본다. 좌표는 flat(Horizontal/Vertical R/Vertical L), PCHK/PRBCHK 기준점, 제품별 보정과 모듈별 변환을 적용한 뒤 정답지와 대조한다. 원문 숫자와 정답지 숫자를 그대로 빼면 화면의 ΔX/ΔY와 다를 수 있다.

S/L 목록의 기준은 `Teg_location.csv`다. 이름·top_cell 및 지원하는 이름 변환으로 정답지를 찾은 행은 S/L로 검사한다. 정답지 파일은 읽혔으나 이름을 찾지 못한 행은 MAIN 내부 TEG 쪽으로 분류하며, 무조건 S/L die 침범으로 처리하지 않는다. 이름 변환으로 찾았다는 이유만으로 좌표 검사를 통과시키지도 않는다.

#### S/L 좌표 차이: 얼마부터 빨간불인가

`ΔX = 환산 X − 정답지 X`, `ΔY = 환산 Y − 정답지 Y`다. 단위는 **ebeam raw 좌표 단위**이며, 유클리드 거리나 X·Y 오차의 합으로 판정하지 않는다. 입력 정밀도에 맞게 차이를 정리한 뒤 아래 조건을 적용한다.

| 조건 | 좌표 판정 | 예시 ΔX, ΔY |
|---|---|---|
| 두 축 모두 절댓값이 `0.000001` 미만 | match / 초록 | `0, 0` |
| 위 조건은 아니지만 두 축 모두 절댓값이 `2.0` 이하 | warning / 노랑 | `2, 0`, `-2, 2`, `1.5, 1.5` |
| 어느 한 축이라도 절댓값이 `2.0` 초과 | mismatch / 빨강 | `2.1, 0`, `0, -2.1` |

따라서 **2 이상이 아니라 2 초과**다. 기본 `ebeam_scale=0.001`이면 raw 2는 0.002 mm(2 µm)다. scale이 다른 제품/설정에서는 같은 raw 2의 물리적 길이가 달라진다. `TOL=1e-6`, `WARN_TOL=2.0`은 현재 코드 상수이며 `check.die_tol`을 바꿔도 이 좌표 임계값은 바뀌지 않는다. 같은 이름의 정답지가 여러 행이면 환산 위치와 `|ΔX|+|ΔY|`가 가장 작은 후보를 선택한 후 축별로 판정한다.

#### S/L die 침범·방향 오류

| 빨간불 조건 | 판정 범위와 확인할 내용 |
|---|---|
| 등록 S/L TEG가 die 안으로 허용오차보다 깊게 들어감 | 좌표 차이가 0이어도 die 침범이면 빨강. 정답지 좌표·TEG 크기·die 형상을 함께 확인 |
| 정답지 방향과 해당 행의 Map 방향이 다름 | 예: Horizontal TEG가 Vertical(R) Map에 포함. 방향 오류만으로 빨강 |
| TEG 사각형 일부 또는 전체가 shot 밖 | 아래 shot 경계 규칙 적용. die 침범과 별도 사유 |

Die 침범 허용오차는 `check.die_tol × ebeam_scale` mm다. 기본 die_tol은 raw `3.0`, 기본 scale에서는 **0.003 mm(3 µm)**다. 경계에 정확히 닿거나 허용오차 이하로 살짝 겹치는 것은 침범으로 세지 않는다. 코드는 사각형을 die 밖으로 빼내는 데 필요한 최소 축 이동량이 허용오차를 넘는지 검사한다(수치 오차 1e-9 mm 포함). 단순 겹침 면적 비율 기준은 아니다. 예를 들어 기본 설정에서 0.003 mm 겹침은 허용하고 0.004 mm 침범은 빨강이다.

PCHK/PRBCHK 기준행은 **die 침범 검사만 제외**한다. 좌표·방향·shot 검사를 전부 면제한다는 뜻은 아니다. 현재 die_proximity는 경계 근처라는 이유만으로 새 노랑을 만들지 않는다. 관련 die 형상 정보가 없으면 침범 검사를 완료했다고 해석하면 안 된다.

#### MAIN 내부 TEG: 자기 die에 포함되는가

MAIN 앵커는 Teg_location의 MAIN 위치를 우선 사용하고 필요한 경우 Mapfile MAIN 앵커를 사용한다. MAIN 크기는 `Main_chip_info.csv`의 chipsize_x/chipsize_y에서 가져온다(µm → mm). TEG 이름이 없는 MAIN 블록 자체의 앵커 행은 내부 TEG 판정 대상에서 제외되어 회색일 수 있다.

| 배치 상태 | 색 / 사유 |
|---|---|
| 자기 MAIN 사각형 안에 TEG 전체가 포함되고 다른 MAIN 침범 없음 | 노랑 / `MAIN… die 안`. S/L 정답지의 정밀 좌표 검증을 통과했다는 뜻은 아님 |
| 자기 MAIN에 일부 걸친 채 나머지가 밖으로 나감 | 빨강 / `MAIN… 경계 넘어감` |
| 자기 MAIN과 다른 MAIN에 동시에 걸침 | 빨강 / `여러 MAIN(…)에 걸침` |
| 자기 MAIN 안에 포함되지만 다른 MAIN 영역도 침범 | 빨강 / `여러 MAIN(…)에 걸침` |
| 자기 MAIN에는 없고 다른 MAIN 안에 들어감/일부 침범 | 빨강 / `다른 MAIN(…) 안` 또는 `침범` |
| 자기 MAIN과 다른 MAIN 어디에도 들어가지 않음 | 빨강 / `MAIN… 밖` |
| 자기 MAIN의 위치·크기 정보를 찾지 못함 | 주황 / `MAIN 정보없음`. 정상으로 간주하지 않음 |

전체 포함 비교에도 die_tol을 사용한다. “대부분 자기 MAIN 안에 있다”거나 중심점이 안에 있다는 것만으로 통과하지 않는다. MAIN 이름은 정규화해 비교하므로 `MAIN01`과 `MAIN_M01` 같은 표기를 연결할 수 있다.

#### purpose가 IP이거나 다른 값인 die

`Main_chip_info.csv`의 purpose는 공백 제거·대문자화 및 연속 공백/밑줄/하이픈을 공백으로 정규화한다.

| purpose | MAIN 내부 TEG 배치 |
|---|---|
| 빈칸 또는 `TEG` | purpose 자체로 금지하지 않음. MAIN 경계·shot 검사는 계속 적용 |
| `IP`, `NO TEG`, `NO_TEG`, `NO-TEG`, 그 밖의 비어 있지 않은 값 | 배치 금지 |

MAIN 내부 TEG가 **허용오차를 넘게 실제로 겹친 MAIN 중 하나라도** 배치 금지 purpose이면 `purpose … — TEG 배치 금지`로 빨강이다. 자기 MAIN뿐 아니라 침범한 다른 MAIN의 purpose도 검사한다. purpose가 있는 die가 파일에 존재한다는 사실만으로 모든 행을 빨갛게 만들지는 않는다. S/L TEG는 purpose와 관계없이 일반 die 침범 규칙을 적용한다.

예: MAIN01 purpose가 TEG이고 MAIN02가 IP일 때, MAIN01 소속 TEG가 MAIN02까지 걸치면 빨강이다. MAIN02 안에 완전히 들어가도 빨강이다. 화면 사유는 purpose 금지 사유가 MAIN 걸침 사유를 대체할 수 있으므로 관련 MAIN과 배치도를 함께 확인한다. 제품에 MAIN 정보가 한 행뿐인 경우 이름이 달라도 그 크기/purpose를 사용하는 호환 처리가 있으므로 chip_name도 정확히 관리한다.

#### Shot 경계 이탈

Shot은 중심 기준 `[-W/2,+W/2] × [-H/2,+H/2]`이고 TEG는 `[x,x+w] × [y,y+h]`다. 두 영역은 mm 단위로 비교한다.

- TEG 전체가 shot 안: inside. TEG의 바깥 변이 shot 경계에 정확히 맞는 것은 허용.
- 면적 일부는 안에 있지만 나머지는 밖: partial → **빨강, shot 경계 벗어남**.
- 면적 교집합이 없음: outside → **빨강, shot 완전 이탈**. 바깥에서 경계선에만 닿아도 outside.

예: 폭·높이 20 mm shot에서 `(x,y)=(9.5,0)`, `(w,h)=(1,0.1)`이면 x 끝이 10.5여서 partial이다. x=10에서 시작하면 outside다. shot 검사는 수치 오차 `1e-9 mm`를 제외하면 die_tol로 경계 초과를 허용하지 않는다. Shot 크기가 없으면 이 검사를 수행할 수 없다.

#### 행 색과 파일 신호등의 차이

S/L이나 MAIN에 빨간 행이 하나라도 있으면 해당 영역이 빨강이고, 둘 중 하나가 빨강이면 파일 신호등도 빨강이다. 빨강이 없을 때 주황·노랑 또는 S/L 필수 대상 누락은 영역/파일 노랑으로 모인다. **필수 대상 누락만으로는 빨강이 아니다.** MAIN 내부에 정상적으로 포함된 미등록 TEG도 확인 성격의 노랑이라 파일 노랑의 원인이 될 수 있다.

현재 파일 집계는 빨강 → 노랑 → 초록 → 회색 순이다. 한 영역이 초록이고 다른 영역이 회색/없음이면 전체가 초록일 수 있으므로 전체 색만으로 모든 영역 검증 완료를 단정하지 않는다. 파일 읽기·검사 오류는 회색일 수 있고, 회색/노랑도 앞 절의 알람 대상이다.

빨간불을 확인할 때는 S/L과 MAIN 상세에서 **행 이름, 정답지 이름, 환산 좌표, ΔX/ΔY, 방향, 크기, 침범한 MAIN, purpose**를 함께 확인한다. 좌표·die·shot 문제가 동시에 있으면 하나만 수정해도 다른 사유가 남는다. `die_tol`을 크게 해서 구조 오류를 숨기지 말고 원천 좌표계와 형상을 먼저 맞춘다.

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

## 5. confidential 및 관련 기준 파일의 용도

파일명이 기준 파일이라고 해서 모두 confidential을 자동 탐색하지는 않는다. **소비 기능별 경로가 다르다.** 현재 소스의 기본 경로는 아래와 같으며, 운영에서 별도 파일명/경로를 설정했다면 그 설정을 확인한다. 실제 파일을 임의로 한 폴더에 모아 옮기면 조회가 끊길 수 있다.

| 파일 | 기본 위치/우선순위 | 역할 |
|---|---|---|
| `f_step.csv` | `DB/credential/` 우선 | 제품별 현재 route와 step의 POR recipe, S0 입력 기준 |
| `f_step.parquet` | 위 CSV가 없을 때 `DB/confidential/` | f_step의 이전 형식 호환 입력 |
| `ppid_knob.csv` | `DB/` 루트, SplitTable 설정 파일명 적용 가능 | PPID 값과 KNOB feature·category 연결 |
| `Vehicle_matching.csv` | `DB/` 루트, SplitTable 설정 파일명 적용 가능 | 제품별 step_id와 공통 step_desc 연결 |
| `step_matching.csv` | `DB/` 루트 | function_step 조회 및 Vehicle 매칭의 레거시 호환 |
| `inline_matching.csv` | `DB/` 루트 | 제품·step·item 설명과 Inline 메타데이터 |
| `vm_matching.csv` | `DB/` 루트 | VM item과 공통 step_desc 연결 |
| `mask_info.csv` | 매칭 기능의 DB 기준 경로 | reticle 정보와 제품·공정 연결 보조 |
| `inline_shot_matching.csv`, `inline_map_settings.json` | `DB/confidential/` 우선 | 4절의 Inline 좌표 선택·내용 |
| `teg_map.json` | `DB/teg_location/` | TEG 파일 경로·좌표 변환·검사·제품 설정 |
| `Chip_Radius.csv`, `Teg_location.csv`, `Main_chip_info.csv` | 기본 DB 루트, teg_map 설정에서 상대/절대 경로 변경 가능 | shot/TEG/die 형상과 검사 기준 |

`confidential/ppid_knob.csv`를 복사해 놓는 것만으로 기본 루트 소비자가 그 파일을 읽지는 않는다. SplitTable의 스키마 파일 설정, 실제 DB root, 해당 기능의 파일 resolver를 확인한다. CSV 캐시에 등록된 호환 파일명 목록도 모든 파일을 자동 탐색·통합한다는 의미는 아니다.

### 5.1 f_step: 공정 순서와 POR recipe

`f_step`은 열 이름이 아니라 파일 이름이다. 권장 열은 다음과 같다.

| 열 | 의미/규칙 |
|---|---|
| `product` | 제품별 범위. 여러 제품이 함께 있으면 반드시 명시 권장 |
| `step_id` | 공정 식별자 문자열. 앞자리 0 유지 |
| `recipe_id` | 해당 step의 현재 POR PPID 문자열. KNOB category나 function_step을 넣지 않음 |

```csv
product,step_id,recipe_id
DEMO_PRODUCT,001200,DEMO_POR_A
DEMO_PRODUCT,001300,DEMO_POR_B
```

행 순서가 route 순서다. 임의로 step_id 문자순 정렬해서 원래 공정 순서를 바꾸지 않는다. 같은 제품·step이 중복되면 **처음 등장한 route 위치를 유지하고 마지막 비어 있지 않은 recipe_id를 사용**한다. product 열이 없으면 step_id가 전 제품에서 유일한 공통 키라고 취급한다. product 열은 있는데 값이 빈 행은 건너뛴다. step·recipe 열 자체가 없으면 유효한 f_step으로 읽지 못한다.

`credential/f_step.csv`가 존재하면 `confidential/f_step.parquet`를 함께 병합하거나 우선하지 않는다. CSV가 잘못되었다고 정상 Parquet로 조용히 넘어간다고 기대하지 않는다. 두 입력이 없을 때 레거시 `credential/*_sop.csv` 탐색 경로가 있지만, 편집 시점 S0 입력 경로는 f_step CSV/Parquet만 허용한다.

소비처는 SplitTable의 공정/parameter 순서, 현재 recipe 조회 및 S0 캡처다. **S0 이력으로 이미 확정한 값은 나중에 POR 파일을 바꿨다고 덮어쓰지 않는다.** 현재 편집에서 쓰는 f_step recipe와 과거에 캡처한 S0는 시간 기준이 다를 수 있으므로 구분한다. f_step은 실제 wafer가 실행한 모든 FAB 이력 파일을 대체하지 않는다.

### 5.2 ppid_knob: PPID → KNOB 규칙

SplitTable 관리자 룰북의 현재 표준 열은 다음과 같다.

| 열 | 입력 의미 |
|---|---|
| `feature_name` | KNOB/실험 축 이름. 예: `DEMO_WIDTH` |
| `rule_order` | 규칙 표식/순서. 예: `R1`, `R2`. `RO`는 문자 O이며 R0(숫자 0)와 구분 |
| `step_desc` | 연결할 공통 공정 설명. Vehicle_matching의 step_desc와 맞춤 |
| `operator` | 비교 연산. 신규 호환 예시는 `eq` 사용 |
| `value` | 비교할 실제 PPID 문자열. 예: `DEMO_POR_A` |
| `category` | PPID가 매칭되었을 때의 KNOB/Split 분류값. 예: `BASE` |

```csv
feature_name,rule_order,step_desc,operator,value,category
DEMO_WIDTH,R1,DEMO_PATTERN,eq,DEMO_POR_A,BASE
DEMO_WIDTH,R2,DEMO_PATTERN,eq,DEMO_RECIPE_B,VARIANT_B
```

이 예시는 같은 공정에서 PPID에 따라 BASE/VARIANT_B를 구분한다. 하나의 feature에 여러 공정이 연결될 수도 있다. 공통 룰북의 step_desc를 선택 제품의 Vehicle_matching에서 실제 step_id로 확장하므로 **룰북에 제품명만 추가한다고 제품별 step 매칭을 대신하지 않는다.** 현재 SplitTable은 ppid_knob 규칙을 제품 공통으로 읽고 제품별 공정 확장은 Vehicle_matching에서 한다.

소비 경로별 호환 차이가 있다. `core/fab_reference.py`의 단일 PPID 조회는 `function_step` 열을 사용하며, `value`를 대소문자 무시로 비교하고 operator가 빈칸 또는 `eq`인 행만 처리한다. regex/contains/부등호를 적어도 이 조회에서는 동작하지 않는다. SplitTable 표준 편집 열은 `step_desc`이므로 두 소비자를 모두 쓴다면 function_step과 step_desc의 연결도 확인해야 한다. 레거시 파일의 열 이름을 추측해서 일괄 변경하지 않는다. 퇴역/미배포 AI 화면이 해당 조회를 제공한다고 가정하지 않는다.

규칙 목록 조회는 숫자 R 순서로 정렬하고 RO를 뒤에 둔다. 여러 eq 규칙이 같은 PPID에 맞으면 단일 규칙만 선택한다고 보장하지 않으므로 서로 다른 category로 중복 매칭하지 않게 작성한다. Valve RO 알람 승인 경로는 해당 function_step 그룹의 다음 `R{n+1}` eq 규칙을 기존 RO 앞에 추가한다. 이는 담당자가 확인한 분류 반영 절차이며, 문서나 CSV 복사 자체가 승인/알람 처리를 실행하지 않는다.

### 5.3 Vehicle_matching과 step_matching

```csv
product,step_id,step_desc
DEMO_PRODUCT,001200,DEMO_PATTERN
DEMO_PRODUCT,001300,DEMO_ETCH
```

Vehicle_matching은 현재 제품의 공정 귀속과 공통 공정 설명을 정하는 기준이다. 한 step_desc에 복수 step_id가 연결될 수 있으며, 이 연결로 KNOB와 VM의 공정 범위를 확장한다. 추가 vehicle/module 열을 쓰는 소비자도 있다. step_desc는 자유 텍스트라 쉼표가 있으면 CSV 따옴표로 감싸야 하며, 쉼표를 여러 공정 구분자로 해석한다고 가정하지 않는다.

`step_matching.csv`의 기본 형식은 `product,step_id,function_step`이다. 공정 ID ↔ function_step 양방향 조회와 레거시 매칭에 쓰인다. 현재 Vehicle 파일이 있으면 비어 있어도 과거 step_matching 행을 자동 합쳐 복원하지 않는 경로가 있다. 새 제품은 Vehicle의 product·step_id·step_desc를 먼저 맞춘 뒤 해당 제품의 KNOB 확장 결과를 확인한다.

### 5.4 Inline·VM·Mask 보조 기준

- `inline_matching.csv`: `product,step_id,item_id,item_desc,matching_table`. 필수 의미는 제품·step·item이며 item_desc는 설명이다. `INLINE_<item_id>` 메타데이터와 제품/공정 연결에 쓰인다. matching_table만 적고 좌표 테이블 본문을 만들지 않으면 shot 좌표가 생기지 않는다. 4절의 전용 shot 룰북과 중복·충돌하지 않게 관리한다.
- `vm_matching.csv`: `step_desc,item_id`를 기준으로 `VM_<step_desc>_<item_id>`를 연결하고 실제 제품별 step_id는 Vehicle_matching에서 확장한다. VM에 제품 정보를 임의 추가해 Vehicle의 제품 귀속을 대신하지 않는다.
- `mask_info.csv`: 매칭 채우기는 `reticle_id`를 FAB 원천 reticle_id와 대조하고 같은 제품·step의 Vehicle 정보에서 step_desc를 가져온다. `mask_version,mask_vendor,photo_step` 및 `product,step_id,step_desc`는 해당 메타데이터다. PPID 분류 룰북과는 다른 키를 사용한다.

매칭 채우기의 원천 스캔 결과는 검토할 제안이다. 제품/공정/설명을 확인한 뒤 기존 승인·저장 절차를 이용한다. 실제 데이터 없이 문서 예시만으로 운영 기준을 채우지 않는다.

### 5.5 TEG 검사 설정·정답지

| 파일/설정 | 핵심 값과 소비처 |
|---|---|
| `Teg_location.csv` | vehicle, TEG 이름/top_cell, ebeam 좌표, 방향, TEG 크기 등. S/L 정답지 매칭·좌표 비교·MAIN 앵커 |
| `Main_chip_info.csv` | `vehicle,chip_name,chipsize_x,chipsize_y,purpose`. 크기는 µm, purpose는 3.1절 배치 금지 판정 |
| `Chip_Radius.csv` | 제품의 shot/wafer 배치 형상. mm 기준으로 읽으며 ebeam_scale을 곱하는 표가 아님 |
| `teg_map.json`의 `ebeam_scale` | ebeam raw → mm. 기본 0.001 |
| `check.die_tol` | die 겹침/MAIN 포함 허용오차. raw 단위, 기본 3.0 |
| `check.flat_offsets`, 제품 flat 보정·module 규칙 | 설비 상대좌표 환산. 정답지 PCHK/PRBCHK 기준점이 있으면 그 기준점을 우선 |
| `teg_default_w`, `teg_default_h` | 정답지에 개별 크기가 없을 때 TEG 크기. 저장 mm, UI µm. 기본 3.0 × 0.1 mm |
| `check_targets` | 제품별 필수 TEG 목록. 제품 키가 없으면 기본 H_/V_ 대상, 명시적 빈 배열은 대상 없음 |
| `product_codes` | Mapfile 이름 접두어 선택용 제품 코드 |

좌표 비교 `WARN_TOL=2.0`과 die_tol은 서로 다른 기준이다. 제품 vehicle은 이 파일들과 제품별 설정의 공통 조인 키이므로 한 표만 이름을 바꾸지 않는다. 정답지/형상 변경 후 대표 Mapfile을 강제 재검사해 ΔX/ΔY와 die/shot 결과를 확인한다. 검사 캐시와 알림 중복 방지는 별도이므로 재검사했다고 동일 파일 버전 알림이 다시 발행되는 것은 아니다.

### 5.6 변경 후 확인 순서

1. 소비 화면이 실제 읽는 DB root·파일명·경로를 확인한다. 기본 경로와 운영 지정 경로를 혼동하지 않는다.
2. 원본을 내부에 백업하고 문자열 식별자·필수 열·중복 키·행 순서를 검토한다.
3. f_step은 제품별 step→recipe와 route 순서, ppid_knob은 PPID→category와 공정 확장, Inline은 subitem→shot 좌표를 각각 대표 입력으로 확인한다.
4. S0 과거 이력 보존과 현재 입력 반영을 따로 확인한다. 캐시 파일을 수기로 편집해 원천을 대신하지 않는다.
5. 실제 confidential/credential 파일·제품 값·담당자·원천 데이터는 내부에 보관한다. GitHub에는 이 가이드의 합성 예시만 둔다.

## 6. 유지보수 시 확인할 소스

- `backend/app_v2/modules/splittable/router_parts/25_s0_snapshot.part.py`: f_step 우선순위·S0 원천과 이력
- `backend/app_v2/modules/splittable/router_parts/20_sources_schema_and_rules.part.py`: 룰북 경로·열·제품별 공정 확장
- `backend/core/fab_reference.py`, `backend/core/matching_fill.py`: PPID/공정 조회·매칭 제안
- `backend/core/ai_semantic.py`: `_load`, `_validate_record`, `prompt_context`
- `backend/core/inline_coordinates.py`: `find_matching_rulebook_files`, `load_matching_rules`, `load_coordinate_mapping`
- `backend/core/teg_map.py`: `_clean_inline_table`, `save_inline_map_table`
- `backend/core/mapfile_traffic.py`: `list_mapfiles_for_product`, `file_signature`
- `backend/core/mapfile_alerts.py`: `_abnormal_reasons`, `publish_mapfile_alerts`
- `backend/core/mapfile_traffic_scheduler.py`: `run_mapfile_traffic_once`, `_seconds_until_next_run`

문서는 저장소 루트에 두고 `_build_setup.py`의 INCLUDE_FILES에 추가하지 않는다. `docs/`는 번들 수집 대상이므로 이 문서를 그 아래로 옮기지 않는다. 이 가이드의 발행은 문서만 커밋·push하며 setup.py를 재생성하지 않는다.
