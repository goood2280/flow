# Page Review

작성: 2026-05-10 (mtime 기준 — 명시 버전 표기는 사용하지 않음)

`flow` 11개 사용자 페이지(+로그인/devguide 제외)를 코드 레벨에서 평가한다. 평가는 (1) 라인수/구조, (2) 상태관리, (3) API 추상화, (4) 권한 게이트, (5) 사내 환경 호환성 다섯 축의 합산이다. 코드 정황만으로 판단한 점수이며, 사용자 시나리오 별 동작 적합성은 별도로 본다.

## Score Table

| 페이지 | 라인 | 코드품질 | 권한 | API추상화 | 사내호환 | 종합 |
|---|---:|---:|---:|---:|---:|---:|
| **Inform Log** | 5,141 | 3 | 5 | 4 | 8 | **5** |
| Admin | 3,023 | 5 | 7 | 5 | 8 | 6 |
| Dashboard | 3,009 | 5 | 6 | 5 | 8 | 6 |
| **SplitTable** | 2,226 | 4 | 6 | 4 | 8 | **5** |
| Tracker | 2,079 | 6 | 7 | 6 | 8 | 7 |
| Meeting | 1,824 | 6 | 6 | 6 | 8 | 7 |
| Diagnosis (Agent 통합) | ~1,800 | 5 | 6 | 6 | 8 | 6 |
| **FileBrowser** | 1,756 | 6 | 5 | 6 | 8 | 6 |
| Calendar | <1,800 | 7 | 7 | 7 | 8 | 7 |
| ETTime | <1,800 | 7 | 7 | 7 | 8 | 7 |
| WaferLayout | <1,800 | 7 | 7 | 7 | 8 | 7 |
| TableMap | <1,800 | 7 | 7 | 7 | 8 | 7 |
| Knowledge | <1,800 | 7 | 7 | 7 | 8 | 7 |
| Home | <1,800 | 8 | 8 | 8 | 9 | 8 |

전체 평균 약 6.5/10. 사내 환경 호환성은 8/10으로 견고(`PATHS`가 env-driven, hardcoded 드라이브 경로 없음).

## Critical Findings

### Inform Log — 5/10
- `frontend/src/pages/My_Inform.jsx` 5,141줄, useState 133개. 단일 페이지에서 thread/list/draft/mail/snapshot/attachments를 모두 들고 있어 변경 cost가 가파르게 상승.
- `backend/routers/informs.py`에서 `try/except: pass` silent fail 46건 — 사용자가 "왜 안 됐는지" 알 수 없음.
- `load_json/save_json` 직접 호출, 동시 write 보호 미흡(audit_log.json 등).
- 권한 정책 endpoint별로 일관성 부족.

### SplitTable — 5/10
- `frontend/src/pages/My_SplitTable.jsx` 2,226줄, useState 74개. 컴포넌트 분리 거의 없음.
- `My_SplitTable.jsx:615-617` raw `fetch()` 3개 — `src/lib/api.js` helper 미사용.
- 백엔드에서 polars 직접 호출 다수, service 계층 부재, 캐싱 전략 분산.

### FileBrowser — 6/10
- `frontend/src/pages/My_FileBrowser.jsx` 1,756줄, useState 15+ — 수용 범위.
- `backend/routers/filebrowser.py`에서 권한 체크 누락 의심 endpoint: `/scopes`, `/base-file/view` 등 — 라우터 초입에 `current_user(request)` 없는 케이스 다수.
- `pl.scan_csv` 등 raw I/O 직접 호출. `try/except: pass` silent fail 다수.
- `PATHS` 사용으로 hardcoded 경로 없음 — 사내 호환은 양호.

### Diagnosis (Agent 통합) — 6/10
- `frontend/src/pages/My_Diagnosis.jsx`에 Flowi Agent 카드(`FlowiCallGraph`/`FlowiActivationMap`/`FlowiOrchestratorPreview`, 약 1280-1628줄)가 진단/지식과 같은 페이지에 묶여 있음 — 사용자 평가 "보기가 불편".
- `/api/llm/flowi/agent/chat` 응답에서 `trace.steps`가 빈 배열, `trace.call_graph.activation`이 optional이라 frontend의 fallback에 의존.

### Admin / Dashboard — 6/10
- 각각 3,023 / 3,009줄. 패널이 한 페이지에 누적 — `docs/DEVELOPMENT.md` "Next Refactor Targets"의 분리 대상.

### 그 외 — 7/10
- Tracker / Meeting / Calendar / ETTime / WaferLayout / TableMap / Knowledge: 라인수 1,800 미만, 책임 비교적 명확. 권한/API 추상화도 큰 결함 없음.

## Cross-cutting Issues

| 항목 | 상태 | 영향 |
|---|---|---|
| 거대 페이지(>2,000줄) | Inform / SplitTable / Admin / Dashboard 4건 | 신규 기능 추가 시 회귀 위험 |
| silent `try/except: pass` | Inform 라우터 46건 | 운영 진단 어려움 |
| Router 직접 raw I/O | filebrowser/splittable/inform 공통 | service/repository 계층 부재 |
| 권한 게이트 일관성 | filebrowser 일부 endpoint 의심 | 보안 표면 |
| Vite chunk size | Inform/Admin/Dashboard | build warning 가능 |

## Portability (사내 Linux 공유 서버 이전)

목표 환경: `/config/work/sharedworkspace/DB`, `/config/work/sharedworkspace/flow-data` 마운트의 Linux 공유 서버.

- ✅ `backend/core/paths.py`는 env(`FLOW_APP_ROOT`/`FLOW_DATA_ROOT`/`FLOW_DB_ROOT`/`FLOW_WAFER_MAP_ROOT`)와 default fallback으로 모든 경로 해석. 하드코딩 드라이브 경로 없음.
- ✅ `runtime-roots.json` 응답으로 현 환경 root 해석을 외부에서 검증 가능.
- ⚠️ `scripts/preflight_internal.py --write-probe`로 사내 반입 전후 root 보존 검증 필요. 본 보고는 로컬 기준만 본 것.
- ⚠️ `_build_setup.py` → `setup.py` 추출 시 `data/flow-data/` 경로 보존 가정. 사내에서 첫 배포 후 실제 보존 여부는 preflight로 재확인 권장.
- ⚠️ atomic write — `app_v2.shared.json_store.JsonFileStore` 사용처는 안전. 비-사용처(audit_log.json 등 직접 `save_json`)는 동시 write race 가능성.

## Recommendations (우선순위)

### Now (현재 미션)
1. **Agent 탭 시각적 분리** — Diagnosis와 섞이지 않게. 단일 페이지 흐름(Persona → 예시 prompt → Activation Map 5단계 → Call Graph → API Calls → Trace Steps → Answer)으로 정리.
2. **Backend trace 충실화** — `/flowi/agent/chat` 응답이 항상 `trace.steps`, `trace.call_graph.activation`을 포함하게.
3. **Agent ↔ Inform/SplitTable/FileBrowser unit action contract 명시** — 각 feature md에 입력/출력/권한/실패 케이스 schema.

### Next (정착 후)
4. Inform / SplitTable 페이지 분해 (사용자 흐름 단위 panel/hook 추출).
5. silent `try/except: pass` 일괄 제거 — 실패 사유 UI 노출.
6. Filebrowser/Inform/Splittable 라우터의 service/repository 계층 도입.
7. 권한 게이트 일관화 audit.

### Later
8. Admin / Dashboard 페이지 분해.
9. Vite chunk 분할.
10. 동시 write race 보호 (lock or single-writer queue).

## 본 보고서 사용법

- 이 점수는 코드 정황 기준이며, 사용자 시나리오 만족도(예: Inform Log가 실제 인폼 thread를 잘 만드는가)는 **별도로 평가**한다. 이 보고서는 "다음에 어디를 손대야 위험·이득이 큰가"를 보는 용도다.
- 실제 코드 변경은 Codex CLI 세션이 진행. 본 세션은 평가/명세/하네스 유지가 역할.
- 최신 작업 큐와 책임 분담은 [`../CLAUDE.md`](../CLAUDE.md), Agent ↔ feature 계약은 [`features/flowi-agent.md`](features/flowi-agent.md), 그리고 각 [`features/inform.md`](features/inform.md), [`features/splittable.md`](features/splittable.md), [`features/filebrowser.md`](features/filebrowser.md)를 본다.
