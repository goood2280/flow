from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core.llm_adapter import (  # noqa: E402
    _build_request_body,
    _build_request_headers,
    _extract_response_text,
    _openai_chat_url,
)


def test_openai_format_accepts_v1_base_url():
    assert _openai_chat_url("https://llm.local/v1", "openai") == "https://llm.local/v1/chat/completions"
    assert _openai_chat_url("https://llm.local/v1/chat/completions", "openai") == "https://llm.local/v1/chat/completions"


def test_openai_response_text_variants():
    assert _extract_response_text({"choices": [{"message": {"content": "확인완료"}}]}) == "확인완료"
    assert _extract_response_text({"choices": [{"text": "plain"}]}) == "plain"
    assert _extract_response_text({"output": [{"content": [{"text": "response"}]}]}) == "response"


def test_playground_profile_builds_internal_headers_and_body():
    cfg = {
        "provider": "playground",
        "auth_mode": "dep_ticket",
        "admin_token": "secret",
        "system_name": "playground",
        "user_id": "knox-id",
        "user_type": "admin",
        "headers": {},
        "format": "openai",
        "extra_body": {},
        "mode": "fast",
        "model": "internal-model",
    }
    headers = _build_request_headers(cfg, prompt_msg_id="prompt-id", completion_msg_id="completion-id")
    assert headers["x-dep-ticket"] == "secret"
    assert "Authorization" not in headers
    assert headers["Send-System-Name"] == "playground"
    assert headers["User-Id"] == "knox-id"
    assert headers["User-Type"] == "admin"
    assert headers["Prompt-Msg-Id"] == "prompt-id"
    assert headers["Completion-Msg-Id"] == "completion-id"

    body = _build_request_body(cfg, "How are you?", "You are a helpful assistant.")
    assert body["model"] == "internal-model"
    assert body["temperature"] == 0.5
    assert body["stream"] is False
    assert "mode" not in body
    assert body["messages"] == [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "How are you?"},
    ]
