# flow v9 개선 협업 계획표

> Claude Code + Codex 협업 작업 공간
> 기준일: 2026-04-24 / 현재 버전: v9.0.2 (종합 7.0/10)

---

## 빠른 시작

- **처음이라면**: [`00_context.md`](./00_context.md) 부터 (배경·현재 상태·타겟 점수)
- **작업을 잡으려면**: [`ASSIGNMENT.md`](./ASSIGNMENT.md) 에서 `todo` 상태 항목 하나 고르기
- **전체 진행 현황**: [`CHECKLIST.md`](./CHECKLIST.md) (원페이지 체크박스)
- **원본 문서 참조**: [`_archive/`](./_archive/) (통합 엔지니어/한국어 요약 원전)

---

## 현재 상태

- **현재 버전**: v9.0.2
- **목표**: v9.0.3 (7.2) → v9.1 (7.5) → v9.2 (8.0) → v9.3+ (8.5)
- **전체 항목**: 24개 (H6 + F3 + P6 + L9)
- **완료**: 2 / 24
- **진행 중**: 1 / 24 (`F1`)

### 최근 반영

- `H2` Dashboard 팔레트 통일 완료
- `H3` SplitTable 내부용어 은닉 완료
- `F1` UXKit 4페이지 실투입 1차 진행 중

---

## 파일 맵

| 파일 | 역할 | 대상 독자 | 항목 수 |
|---|---|---|---|
| [`README.md`](./README.md) | 인덱스 허브 (이 문서) | 모두 | — |
| [`00_context.md`](./00_context.md) | 배경·이슈 원인·타겟 점수 | 신규 참여자 | — |
| [`01_hotfix_v9_0_3.md`](./01_hotfix_v9_0_3.md) | 핫픽스 (반일~1일짜리) | codex/claude | H1~H6 (6) |
| [`02_feature_v9_1.md`](./02_feature_v9_1.md) | v9.1 대형 (1~2주) | dev-lead · eval-lead | F1~F3 (3) |
| [`03_platform_v9_2.md`](./03_platform_v9_2.md) | 플랫폼화 (1~2개월) | infra · eval-lead | P1~P6 (6) |
| [`04_longterm_v9_3plus.md`](./04_longterm_v9_3plus.md) | 장기 (분기 단위) | 로드맵 · 도메인 | L1~L9 (9) |
| [`ASSIGNMENT.md`](./ASSIGNMENT.md) | 담당자 현황 + claude↔codex 대화 | claude/codex | — |
| [`CHECKLIST.md`](./CHECKLIST.md) | 원페이지 체크박스 진행률 | 전체 | — |
| [`_archive/`](./_archive/) | 원본 2건 (분산 이전) | 참고용 | — |

---

## 협업 룰

1. **작업 시작 전**: `ASSIGNMENT.md` 에서 본인 행을 찾아 `owner` 열에 `claude` 또는 `codex` 표기
2. **작업 중**: 해당 항목 파일(`01~04_*.md`) 에서 상태를 `in_progress (claude)` 또는 `in_progress (codex)` 로 변경
3. **완료 시**:
   - 항목 파일 상태를 `done` 으로 변경
   - `CHECKLIST.md` 해당 체크박스 `[x]` 로 체크
   - `ASSIGNMENT.md` 의 "완료 로그" 섹션에 한 줄 기입 (날짜·ID·커밋·PR)
4. **충돌/상의 필요 시**: `ASSIGNMENT.md` 의 "대화 섹션" 에 메시지 남기기 (markdown, timestamped)
5. **DoD 미달 시**: 체크박스 채우지 말 것. `status` 를 `blocked` 로 바꾸고 이유 기입

---

## 항목 ID 체계

| 접두어 | 의미 | 범위 |
|---|---|---|
| `H` | Hotfix (v9.0.3) | H1~H6 |
| `F` | Feature (v9.1) | F1~F3 |
| `P` | Platform (v9.2) | P1~P6 |
| `L` | Long-term (v9.3+) | L1~L9 |

각 항목은 다음 형식으로 상세 스펙 제공:

```
## ID. 제목

- 상태: todo / in_progress (owner) / done / blocked
- 담당 후보: claude / codex / either / human-required
- 변경 파일: (절대 경로 리스트)
- 변경 내용: (diff 수준 기술)
- 완료 조건(DoD): (체크박스 3~5개)
- 의존성: (선행 항목)
- 예상 공수: 반일 / 1일 / 3일 / 1주
- 리스크: (안 하면 / 하다가 깨질 수 있는 것)
```

---

## 릴리즈 로드맵 요약

| 릴리즈 | 시점 | 점수 | 핵심 |
|---|---|---|---|
| v9.0.2 (지금) | — | 7.0 | 기능 포화, UX 파편 |
| **v9.0.3** | +2주 | 7.2 | 핫픽스 6건 (H1~H6) |
| **v9.1** | +6주 | 7.5 | UXKit/SplitTable 분할/pytest (F1~F3) |
| **v9.2** | +3개월 | 8.0 | CI·관측성·SQLite·RBAC (P1~P6) |
| **v9.3+** | +6개월 | 8.5 | SPC·모바일·SSO·i18n (L1~L9) |

---

## 연락 · 에스컬레이션

- **Claude Code (본 CLI)**: dev-lead 롤로 기술 분배 가능
- **Codex**: 짝꿍 개발자 — 본 폴더 내 항목 자유 집기 가능
- **사람 승인 필요** (`human-required`): 운영 상 의사결정, 실데이터 접근, 배포 승인

---

*문서 위치*: `D:\TEST_Making_Video\semi_all\flow\docs\collab\`
*생성일*: 2026-04-24
*원본 통합 스펙*: `_archive/v9_improvement_plan.md` · `_archive/v9_improvement_summary_ko.md`
