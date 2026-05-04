# Flowi Core Work Folder

이 폴더는 Flow 앱을 줄이고 이어서 작업하기 위한 기준 문서다. 오래된 요청 파일, archive, 생성된 spec보다 이 폴더를 우선한다.

## 읽는 순서

1. [ENTRYPOINTS.md](ENTRYPOINTS.md) - 앱 실행/라우팅/Flowi 동작 진입점
2. [FILEBROWSER.md](FILEBROWSER.md) - 파일탐색기 유지 범위
3. [SPLITTABLE.md](SPLITTABLE.md) - 스플릿 테이블 유지 범위
4. [INFORM_LOG.md](INFORM_LOG.md) - 인폼 로그 유지 범위
5. [TODO.md](TODO.md) - 단일 작업 목록

## 운영 원칙

- Flow의 핵심 축은 `FileBrowser -> SplitTable -> Inform Log`다.
- 새 기능을 넣기 전에 세 기능 중 어디에 속하는지 먼저 정한다.
- 속하지 않으면 Home/Flowi에서 연결만 하거나 보류한다.
- TODO는 [TODO.md](TODO.md) 하나만 갱신한다. 기능 문서 안에 별도 TODO를 만들지 않는다.
- runtime 데이터(`data/flow-data/*.json`, cache, activity log)는 명시 요청이 없으면 소스 변경으로 취급하지 않는다.

## Claude/Codex 이어받기

- Claude는 `flow/CLAUDE.md`를 먼저 읽고 이 폴더로 들어온다.
- 현재 작업 상태는 [TODO.md](TODO.md)의 상태표를 기준으로 판단한다.
- 기존 dirty worktree가 있으면 먼저 `git status --short`로 사용자 변경을 확인하고, 관련 없는 변경은 건드리지 않는다.
- 기능 판단이 애매하면 코드를 바꾸기 전에 이 폴더 문서에 "확정된 기준"만 보강한다.
