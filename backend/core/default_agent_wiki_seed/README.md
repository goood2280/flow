# Default Agent Wiki Seed

이 폴더는 Flow Agent가 기본적으로 알고 있어야 하는 지식 Wiki 원본을 둔다.

동작 규칙:

- `*.md` 중 frontmatter에 `doc_id`가 있는 문서만 seed 대상이다.
- 파일명이 `_`로 시작하는 문서는 template/reference로 보고 설치하지 않는다.
- runtime 문서는 `data/flow-data/knowledge/wiki/`에 같은 `doc_id`가 없을 때만 생성한다.
- 한번 생성된 runtime 문서는 Agent 지식 Wiki에서 편집 가능하며, seed가 덮어쓰지 않는다.
- 기존 seed 문서를 바꾸면 신규 설치에는 반영되지만, 이미 flow-data에 생성된 같은 `doc_id` 문서는 보존된다.
- 기존 사용자에게 새 기본지식을 배포하려면 새 `doc_id` 문서로 추가한다.
