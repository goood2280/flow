# Flowi Core TODO

이 파일이 단일 TODO 목록이다. 기능 문서나 임시 spec 파일에 별도 TODO를 만들지 않는다.

상태 표기:

- `[ ]` 대기
- `[~]` 진행 중
- `[x]` 완료
- `[!]` 막힘

## Now

- [x] Flowi core work folder 생성
- [x] 동작 진입점 문서 작성
- [x] FileBrowser/SplitTable/Inform Log 기능별 기준 문서 작성
- [x] Claude 이어받기 진입 파일 작성
- [x] `.codex_task_*` 임시 작업 스펙 archive 이동
- [x] `CHANGELOG.md` archive 이동 및 setup 번들 제외
- [x] `_build_setup.py` 기준으로 `setup.py` 재생성
- [x] 앱 갱신 직후 페이지 로딩 stuck 방지(cache-control + lazy reload fallback)
- [x] Home Flow-i persona help card 숨김
- [x] FileBrowser/SplitTable/Inform Log core 라우팅 우선순위 보강
- [x] Home Flow-i core 기능 missing-field follow-up 보강
- [x] Inform Log 목록 상태/작성자 겹침 및 대기 상태 표시 정리
- [ ] 기존 `data/flow-data/flowi_agent_entrypoints.md`를 core 3기능 중심으로 슬림화할지 결정
- [ ] Flowi feature guides에서 사용하지 않는 기능 문서를 archive 후보로 분류

## P0

- [x] Home Flowi가 FileBrowser/SplitTable/Inform Log 요청을 우선 라우팅하는지 확인
- [ ] 사용자가 파일/DB 수정을 요청할 때 read-only/admin-confirm guard가 동작하는지 확인
- [ ] Inform mail 본문에 내부 id/source/scope가 노출되지 않는지 확인

## P1

- [ ] SplitTable snapshot과 Inform Log lot/wafer 표시 규칙 통일
- [ ] FileBrowser preview cap과 download cap이 큰 parquet에서 UI를 막지 않는지 확인
- [ ] Dashboard inform widget이 Inform Log 요약만 참조하고 중복 상태를 만들지 않는지 확인

## P2

- [ ] FileBrowser/SplitTable/Inform Log 밖의 페이지를 유지/숨김/archive 후보로 분류
- [x] 오래된 `.codex_task_*_spec.txt` 파일을 archive 대상으로 정리
- [ ] 사용자 가이드 문서와 이 core 작업 문서의 역할을 분리

## Update Rule

- 새 작업은 우선순위가 확정된 뒤 `P0/P1/P2` 중 하나에 넣는다.
- 진행을 시작하면 해당 줄을 `[~]`로 바꾸고 담당 컨텍스트를 짧게 적는다.
- 완료 시 `[x]`로 바꾸고, 코드 변경이 있으면 검증 명령을 final/commit message에 남긴다.
- 막히면 `[!]`로 바꾸고 필요한 질문을 한 줄로 적는다.
