"""Top-level uvicorn entrypoint shim — v8.6.x.

목적: README/사용자가 단순히 다음 명령으로 실행할 수 있게 한다.

    uvicorn app:app --host 0.0.0.0 --port 8080

backend/ 내부에 실제 app.py 가 있고 routers 가 동적으로 import 되기 때문에,
import 위치(working directory)에 따라 `Could not import module 'app'` 또는
`No module named 'routers'` 에러가 발생할 수 있다. 이 shim 은:

  1. 이 파일이 위치한 디렉토리(=flow/) 와 그 하위 backend/ 를 sys.path 앞에
     추가한다 → 어디서 호출하든 `routers.*` import 가 안전하다.
  2. backend.app 의 `app` 객체를 그대로 re-export 한다.

이렇게 하면:
  - cd flow && uvicorn app:app  ✓
  - python -m uvicorn app:app           ✓ (flow 가 cwd)
  - 과거 명령 `uvicorn app:app --app-dir backend` 도 그대로 동작 (backend/app.py 가 자체적으로 동작).
"""
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE / "backend"

# backend 디렉토리를 sys.path 앞에 두어 `routers.*`, `core.*` 가 import 되게 한다.
for p in (str(_BACKEND), str(_HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

# Working directory 와 무관하게 backend/app.py 를 import.
# 우선 `from backend.app import app` 시도, 실패 시 cwd 변경 후 fallback.
try:
    # backend 가 패키지가 아닐 수 있으므로 (no __init__.py) 직접 모듈 로드.
    import importlib.util
    spec = importlib.util.spec_from_file_location("_flow_backend_app", _BACKEND / "app.py")
    if spec is None or spec.loader is None:
        raise ImportError("Cannot locate backend/app.py spec")
    _mod = importlib.util.module_from_spec(spec)
    sys.modules["_flow_backend_app"] = _mod
    spec.loader.exec_module(_mod)
    app = _mod.app
except Exception as e:
    # 마지막 수단: cwd 를 backend 로 옮긴 뒤 재시도 (uvicorn --app-dir 동등 효과)
    os.chdir(str(_BACKEND))
    sys.path.insert(0, str(_BACKEND))
    from app import app  # type: ignore

__all__ = ["app"]
