import os
import sys
from pathlib import Path

import pytest

# 운영 admin_settings.json 의 agentic 플래그(react/tool_call)가 켜져 있어도
# 테스트는 결정적이어야 한다 — env 가 settings 보다 우선하므로 여기서 고정.
# (react 관련 테스트는 monkeypatch 로 개별 케이스를 켠다.)
os.environ["FLOW_LLM_TOOL_CALL"] = "0"
os.environ["FLOW_LLM_REACT_LOOP"] = "0"

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture(autouse=True)
def _reset_llm_health_breaker():
    """전역 LLM 헬스 브레이커의 테스트 간 누수 차단.

    mock 없이 도는 테스트에서 실 LLM 시도가 실패하면 breaker(cooldown)가 열리고,
    이후 runtime 테스트들의 mock LLM 호출이 should_attempt_llm() 에서 차단되어
    순서 의존 실패가 난다. 매 테스트 시작 시 초기화한다.
    """
    try:
        from core import llm_adapter
        llm_adapter.reset_llm_health()
    except Exception:
        pass
    yield
