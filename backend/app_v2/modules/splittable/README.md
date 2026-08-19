# SplitTable module

`router_parts/`는 기존 `routers.splittable`의 route, 전역 cache, 공개 helper 계약을
유지하는 1차 소스 분리다. 파일명 순서대로 하나의 legacy module namespace에서 실행되며
각 part는 독립 모듈이 아니다.

새 업무 규칙과 저장소 접근은 이미 분리된 `rulebook_service.py`,
`rulebook_repository.py`, `cache_builder.py`, `product_adapter.py` 같은 명시적 모듈에
추가한다. part에는 HTTP 입출력과 기존 호환 조합만 남기는 방향으로 이관한다.
