# LLM module

`router_parts/`는 기존 `routers.llm`을 역할별 소스 파일로 나눈 호환 계층이다.
모델/요청 스키마, 도구, orchestration, chat runtime, 공개 API의 실행 순서와 module
namespace는 분리 전과 같다.

part 파일은 독립적으로 import하지 않는다. 새 도구와 업무 로직은 가능한 한
`app_v2.modules`의 명시적 모듈에 구현하고, LLM part는 조합과 HTTP 경계만 담당한다.
전역 cache나 테스트 monkeypatch 대상의 실제 모듈 이동은 호출자 이관 후 진행한다.
