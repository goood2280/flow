from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core.llm_adapter import _extract_response_text, _openai_chat_url  # noqa: E402


def test_openai_format_accepts_v1_base_url():
    assert _openai_chat_url("https://llm.local/v1", "openai") == "https://llm.local/v1/chat/completions"
    assert _openai_chat_url("https://llm.local/v1/chat/completions", "openai") == "https://llm.local/v1/chat/completions"


def test_openai_response_text_variants():
    assert _extract_response_text({"choices": [{"message": {"content": "확인완료"}}]}) == "확인완료"
    assert _extract_response_text({"choices": [{"text": "plain"}]}) == "plain"
    assert _extract_response_text({"output": [{"content": [{"text": "response"}]}]}) == "response"
