from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core import llm_adapter  # noqa: E402
from core.llm_adapter import (  # noqa: E402
    _build_request_body,
    _build_request_headers,
    _extract_response_text,
    _openai_chat_url,
    _parse_json_object,
)


def test_openai_compatible_blank_model_defaults_to_internal_gpt_oss(monkeypatch):
    monkeypatch.setattr(
        llm_adapter,
        "load_json",
        lambda *_args, **_kwargs: {
            "llm": {
                "enabled": True,
                "api_url": "http://llm.internal/v1",
                "provider": "openai_compatible",
                "format": "openai",
                "model": "",
            }
        },
    )

    cfg = llm_adapter.get_config(redact=False)

    assert cfg["provider"] == "openai_compatible"
    assert cfg["model"] == "gpt-oss-120b"
    assert cfg["format"] == "openai"


def test_openai_format_accepts_v1_base_url():
    assert _openai_chat_url("https://llm.local/v1", "openai") == "https://llm.local/v1/chat/completions"
    assert _openai_chat_url("https://llm.local/v1/chat/completions", "openai") == "https://llm.local/v1/chat/completions"


def test_openai_response_text_variants():
    assert _extract_response_text({"choices": [{"message": {"content": "확인완료"}}]}) == "확인완료"
    assert _extract_response_text({"choices": [{"text": "plain"}]}) == "plain"
    assert _extract_response_text({"output": [{"content": [{"text": "response"}]}]}) == "response"
    assert _extract_response_text({"candidates": [{"content": {"parts": [{"text": "vertex"}]}}]}) == "vertex"


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


def test_openai_provider_does_not_send_internal_mode_parameter():
    cfg = {
        "provider": "openai",
        "auth_mode": "bearer",
        "headers": {},
        "format": "openai",
        "extra_body": {},
        "mode": "fast",
        "model": "gpt-4o-mini",
    }

    body = _build_request_body(cfg, "ping", None)

    assert body["model"] == "gpt-4o-mini"
    assert "mode" not in body
    assert body["messages"] == [{"role": "user", "content": "ping"}]


def test_openai_compatible_provider_does_not_send_internal_mode_parameter():
    cfg = {
        "provider": "openai_compatible",
        "auth_mode": "bearer",
        "headers": {},
        "format": "openai",
        "extra_body": {},
        "mode": "fast",
        "model": "compatible-model",
    }

    body = _build_request_body(cfg, "ping", None)

    assert body["model"] == "compatible-model"
    assert "mode" not in body


def test_local_provider_uses_openai_shape_without_auth_or_internal_mode():
    cfg = {
        "provider": "local",
        "auth_mode": "none",
        "admin_token": "secret",
        "headers": {},
        "format": "openai",
        "extra_body": {},
        "mode": "fast",
        "model": "GPT-OSS-120B",
    }

    headers = _build_request_headers(cfg)
    body = _build_request_body(cfg, "ping", None)

    assert "Authorization" not in headers
    assert "x-dep-ticket" not in headers
    assert body["model"] == "GPT-OSS-120B"
    assert "mode" not in body
    assert body["messages"] == [{"role": "user", "content": "ping"}]


def test_vertex_gemini_profile_uses_google_adc_bearer(monkeypatch):
    cfg = {
        "provider": "vertex_gemini",
        "auth_mode": "google_adc",
        "admin_token": "",
        "headers": {},
        "format": "openai",
        "extra_body": {},
        "mode": "fast",
        "model": "google/gemini-2.5-flash",
    }
    monkeypatch.setattr(llm_adapter, "_google_adc_access_token", lambda *_args, **_kwargs: "adc-token")

    headers = _build_request_headers(cfg)

    assert headers["Authorization"] == "Bearer adc-token"


def test_vertex_gemini_native_body_shape():
    cfg = {
        "provider": "vertex_gemini",
        "auth_mode": "google_adc",
        "headers": {},
        "format": "vertex_gemini",
        "extra_body": {},
        "mode": "fast",
        "model": "gemini",
    }

    body = _build_request_body(cfg, "ping", "system")

    assert body["contents"] == [{"role": "user", "parts": [{"text": "ping"}]}]
    assert body["systemInstruction"] == {"parts": [{"text": "system"}]}
    assert "model" not in body


def test_parse_json_object_repairs_fenced_and_prose_json():
    obj, err = _parse_json_object("```json\n{\"sql\":\"a = 1\", \"extra\": true}\n```", required=["sql"], keys=["sql"])
    assert err == ""
    assert obj == {"sql": "a = 1"}

    obj, err = _parse_json_object("draft: {\"target\":\"lot_progress\"}", required=["target"])
    assert err == ""
    assert obj["target"] == "lot_progress"


def test_complete_json_retries_malformed_json(monkeypatch):
    calls = []

    def fake_complete(prompt, **_kwargs):
        calls.append(prompt)
        if len(calls) == 1:
            return {"ok": True, "text": "{bad json"}
        return {"ok": True, "text": "{\"target\":\"lot_progress\"}"}

    monkeypatch.setattr(llm_adapter, "complete", fake_complete)

    out = llm_adapter.complete_json(
        "classify",
        schema={"keys": ["target"], "required": ["target"], "properties": {"target": {}}},
        max_retries=1,
    )

    assert out["ok"] is True
    assert out["obj"] == {"target": "lot_progress"}
    assert out["repaired"] is True
    assert len(calls) == 2
