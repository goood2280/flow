# FileBrowser module

`router_parts/`는 기존 `routers.filebrowser`의 전역 상태, import 순서, 공개 helper와
FastAPI route 계약을 그대로 보존하면서 소스 충돌 범위를 줄이는 1차 분리 영역이다.

각 `*.part.py`는 독립 모듈이 아니다. `routers/filebrowser.py`가 파일명 순서대로 같은
module namespace에서 실행한다. 따라서 part 사이를 직접 import하거나 실행하면 안 된다.

새 로직은 이 디렉터리 아래의 일반 Python 모듈에 작성하고 part에서는 import해서 쓴다.
기존 helper를 실제 모듈로 옮길 때는 해당 helper의 직접 import와 monkeypatch 사용처를
먼저 제거한 뒤, 별도 변경으로 진행한다.
