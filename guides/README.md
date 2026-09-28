# flow 사용법 영상 (가이드)

화면 오른쪽 위 **[? 사용법]** 버튼으로 여는 업무 탭별 사용법 영상입니다.
Blender 모션그래픽으로 만들었고, 화면은 실제 flow 를 가상 데모 데이터로 캡처한 것입니다.

| helpId | 화면 | 길이 |
|---|---|---|
| `home` | 홈 (알람 · 기능 이동 · Flow-i) | 약 54초 |
| `filebrowser` | 파일탐색기 (SQL 조회 · GROUP BY 집계 · AI SQL) | 약 80초 |
| `splittable` | 스플릿 테이블 (표 읽기 · 보기 바꾸기 · plan 입력) | 약 64초 |
| `dashboard` | 대시보드 (split 기준 · 물량/비중) | 약 44초 |
| `teg` | TEG 위치 조회 (TEG 선택 · shot 확대 · radius 표) | 약 41초 |
| `reformatize` | ET 다운로드 (Index 선택 · 필터 · 집계 · 검색이력) | 약 52초 |
| `ettime` | ET 측정시간 (조회 · 표 읽기 · 추이) | 약 39초 |

## 1. 넣는 곳 — DB 폴더의 `_guides`

이 폴더의 **`_guides` 폴더를 통째로 flow DB 루트(`FLOW_DB_ROOT`) 바로 아래에 복사**하면 끝입니다.
서버 재시작도, setup.py 재설치도 필요 없습니다(버튼 목록은 브라우저를 새로고침하면 다시 읽습니다).

```
<FLOW_DB_ROOT>/                  ← 예: 1.RAWDATA_DB_ET, 1.RAWDATA_DB_FAB … 가 있는 그 폴더
  _guides/
    home/
      guide.json                 ← 제목 · 챕터 · 메모 · 파일 목록
      home_1080p.mp4
      poster.webp
      captions.ko.vtt
    filebrowser/
      …
    splittable/ dashboard/ teg/ reformatize/ ettime/
```

- 이름이 `_` 로 시작해서 **파일탐색기 DB 목록에는 나타나지 않습니다.**
- 찾는 순서: 환경변수 `FLOW_GUIDES_DIR` → `<FLOW_DB_ROOT>/_guides` → `<FLOW_DATA_ROOT>/guides`. 처음 있는 폴더 하나만 씁니다.
- 폴더 하나 = 가이드 하나. 폴더 이름은 그 화면의 `helpId`(위 표, `frontend/src/app/pageManifest.jsx`)와 같아야 버튼이 붙습니다.
- 특정 화면의 영상을 내리려면 그 폴더만 지우면 됩니다. 새 화면을 추가할 때도 폴더만 추가합니다.

## 2. 동작 방식

| 구성 | 내용 |
|---|---|
| 버튼 | `frontend/src/components/GuideHelp.jsx` — 상단 바 오른쪽. **지금 탭의 helpId 폴더가 있을 때만** 보입니다. |
| 목록 API | `GET /api/guides` (`backend/routers/guides.py`) — 로그인 필요. 각 폴더의 `guide.json` 을 읽고, 영상 파일이 실제로 있는 가이드만 돌려줍니다. |
| 영상 API | `GET /api/guides/media/<helpId>/<파일>` — 로그인 필요. `<video>` 태그는 헤더를 못 붙여서 이 경로만 `?t=<세션토큰>` 을 받습니다(`app_v2/runtime/security.py` 의 `QUERY_TOKEN_PREFIXES`). Range 요청을 지원해 챕터 이동이 됩니다. |
| 보안 | 폴더 이름 · 파일 이름은 영문 소문자/숫자 규칙과 확장자(mp4 · webm · webp · png · jpg · vtt) 화이트리스트로 거르고, 실제 경로가 `_guides` 밖이면 404 입니다. |
| 부담 | 버튼을 누르기 전에는 영상을 받지 않습니다. 편당 약 2~4 MB, 서버는 파일을 조각내 보내기만 합니다(변환 없음). |

창 안에서는 왼쪽에 영상, 오른쪽에 **챕터 목록(누르면 그 장면으로 이동)** 과 **메모(예: SQL 예시)** 가 나옵니다.
자막(captions.ko.vtt)은 영상 컨트롤의 자막 메뉴에서 켤 수 있습니다.

## 3. guide.json 형식

```json
{
  "title": "파일탐색기 사용법",
  "summary": "창 오른쪽 맨 위에 나오는 한두 문장",
  "duration": 80.1,
  "updated": "2026-09-29",
  "poster": "poster.webp",
  "captions": "captions.ko.vtt",
  "sources": [{ "file": "filebrowser_1080p.mp4", "label": "1080p", "height": 1080 }],
  "chapters": [{ "t": 0, "title": "소개", "desc": "…" }, { "t": 3.7, "title": "DB 고르기", "desc": "…" }],
  "notes": [{ "title": "집계(피벗)는 SQL 로", "lines": ["…"], "code": ["SELECT wafer_id, AVG(value) … GROUP BY wafer_id"] }]
}
```

`sources` 에 적은 파일이 폴더에 없으면 그 가이드는 목록에서 빠집니다(버튼도 안 보임).
`chapters` · `notes` 문구는 영상을 다시 만들지 않고 이 파일만 고쳐도 바로 반영됩니다.

## 4. 파일탐색기 — 집계(피벗)는 SQL 로

파일탐색기의 **피벗/집계 버튼은 없앴습니다.** 집계는 두 가지 방법으로 합니다.

1. **직접 입력** — SQL 칸에 `GROUP BY` 를 씁니다.
   ```sql
   SELECT wafer_id, AVG(value) WHERE item_id = 'VTH' GROUP BY wafer_id
   SELECT root_lot_id, LATEST(tkout_time) GROUP BY root_lot_id
   SELECT step_id, COUNT(*) WHERE root_lot_id = 'A1000' GROUP BY step_id
   SELECT wafer_id, MEDIAN(value) WHERE item_id = 'IDSAT' GROUP BY wafer_id ORDER BY CAST(wafer_id AS BIGINT)
   ```
   - 함수는 **한 번에 하나**: `LATEST · AVG · SUM · MIN · MAX · MEDIAN · COUNT`
   - `SELECT` 에 쓴 일반 열은 `GROUP BY` 에도 넣습니다. `GROUP BY` 없이 `SELECT COUNT(*) WHERE …` 는 전체 한 줄 집계입니다.
   - 실행하면 SQL 칸 아래에 `SQL 집계: avg(value) by wafer_id` 가 표시되고, **GROUP BY 를 지우고 다시 실행하면 풀립니다.**
   - CSV 다운로드도 같은 집계로 받습니다.
2. **AI SQL** — `[AI SQL]` 에 "VTH 항목의 웨이퍼별 평균"처럼 말로 씁니다. 집계가 붙으면 `AI 집계:` 줄의 `[해제]` 로 풉니다.

## 5. 영상 다시 만들기

원본 도구(캡처 스크립트 · Blender 합성 · 패키징)는 이 저장소에 넣지 않고 작성자 PC 의
`deliverables/flow-guide-videos/` 에 있습니다. 화면이 크게 바뀌면 거기서 다시 캡처 → 렌더 → 패키징한 뒤
이 폴더의 `_guides/<helpId>` 를 교체합니다.
