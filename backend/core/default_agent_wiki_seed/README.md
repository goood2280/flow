# Default Agent Wiki Seed

이 폴더는 Agent Wiki seed 설치 장치만 남겨둔다.

현재 Flow-i는 반도체 배경지식 seed를 자동 설치하지 않는다. Home Agent가 쓰는 운영 지식은
runtime `data/flow-data/knowledge/wiki/`와 `schema_relations.json`의 `column_catalog`에 관리자가
넣은 내용이 source of truth다.

동작 규칙:

- `*.md` 중 frontmatter에 `doc_id`가 있는 문서만 seed 대상이다.
- 파일명이 `_`로 시작하는 문서는 template/reference로 보고 설치하지 않는다.
- runtime 문서는 `data/flow-data/knowledge/wiki/`에 같은 `doc_id`가 없을 때만 생성한다.
- 한번 생성된 runtime 문서는 Agent 지식 Wiki에서 편집 가능하며, seed가 덮어쓰지 않는다.
- seed를 다시 추가해야 한다면 배경 설명 문서가 아니라 실행에 필요한 최소 scaffold만 새 `doc_id`로 둔다.
