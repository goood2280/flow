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

LLM_ENV_KEYS = (
    "FLOW_LLM_DISABLE_ENV_FALLBACK",
    "FLOW_LLM_ENABLE_ENV_FALLBACK",
    "FLOW_LLM_PROVIDER",
    "FLOW_LLM_MODEL",
    "FLOW_OPENAI_API_KEY",
    "FLOW_OPENAI_API_URL",
    "FLOW_OPENAI_MODEL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_API_BASE",
    "OPENAI_MODEL",
    "FLOW_VERTEX_PROJECT",
    "FLOW_VERTEX_LOCATION",
    "FLOW_VERTEX_MODEL",
    "FLOW_VERTEX_API_URL",
    "VERTEX_PROJECT",
    "VERTEX_LOCATION",
    "VERTEX_MODEL",
    "VERTEX_OPENAI_API_URL",
    "GOOGLE_VERTEX_MODEL",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "GCLOUD_PROJECT",
    "CLOUDSDK_CORE_PROJECT",
    "CLOUDSDK_CONFIG",
    "GOOGLE_APPLICATION_CREDENTIALS",
)


def _clear_llm_env(monkeypatch):
    for key in LLM_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(llm_adapter, "_DOTENV_FILE", ROOT / ".pytest_missing_dotenv", raising=False)
    if hasattr(llm_adapter, "_DOTENV_CACHE"):
        llm_adapter._DOTENV_CACHE.update({"path": "", "mtime": None, "values": {}})


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


def test_vertex_openai_compatible_profile_forces_google_adc(monkeypatch):
    monkeypatch.setattr(
        llm_adapter,
        "load_json",
        lambda *_args, **_kwargs: {
            "llm": {
                "enabled": True,
                "api_url": "https://aiplatform.googleapis.com/v1/projects/p/locations/us-central1/endpoints/openapi/chat/completions",
                "model": "google/gemini-2.5-flash",
                "provider": "openai_compatible",
                "auth_mode": "bearer",
                "admin_token": "stale-token",
                "format": "openai",
            }
        },
    )

    cfg = llm_adapter._raw_config()

    assert cfg["provider"] == "openai_compatible"
    assert cfg["auth_mode"] == "google_adc"
    assert cfg["format"] == "openai"
    assert cfg["admin_token"] == ""


def test_openai_env_fallback_enables_local_dev_without_admin_url(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("FLOW_LLM_ENABLE_ENV_FALLBACK", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(llm_adapter, "_path_exists", lambda _path: False)
    monkeypatch.setattr(
        llm_adapter,
        "load_json",
        lambda *_args, **_kwargs: {"llm": {"enabled": True, "provider": "generic", "api_url": ""}},
    )

    cfg = llm_adapter.get_config(redact=False)

    assert llm_adapter.is_available() is True
    assert cfg["source"] == "env_fallback"
    assert cfg["provider"] == "openai"
    assert cfg["model"] == "gpt-4o-mini"
    assert cfg["api_url"] == "https://api.openai.com/v1"
    assert cfg["admin_token"] == "sk-test"


def test_vertex_env_fallback_uses_google_adc(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("FLOW_LLM_ENABLE_ENV_FALLBACK", "1")
    monkeypatch.setenv("FLOW_LLM_PROVIDER", "vertex")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "flow-dev")
    monkeypatch.setattr(llm_adapter, "_path_exists", lambda _path: False)
    monkeypatch.setattr(llm_adapter, "load_json", lambda *_args, **_kwargs: {})

    cfg = llm_adapter.get_config(redact=False)

    assert llm_adapter.is_available() is True
    assert cfg["source"] == "env_fallback"
    assert cfg["provider"] == "vertex_gemini"
    assert cfg["auth_mode"] == "google_adc"
    assert cfg["model"] == "google/gemini-2.5-flash"
    assert "aiplatform.googleapis.com" in cfg["api_url"]
    assert "/projects/flow-dev/" in cfg["api_url"]


def test_vertex_fallback_reads_gcloud_config_project(monkeypatch, tmp_path):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("FLOW_LLM_ENABLE_ENV_FALLBACK", "1")
    gcloud_root = tmp_path / "gcloud"
    config_dir = gcloud_root / "configurations"
    config_dir.mkdir(parents=True)
    (gcloud_root / "active_config").write_text("default", encoding="utf-8")
    (config_dir / "config_default").write_text("[core]\nproject = flow-gcloud\n", encoding="utf-8")
    monkeypatch.setenv("CLOUDSDK_CONFIG", str(gcloud_root))
    monkeypatch.setattr(llm_adapter, "_path_exists", lambda _path: False)
    monkeypatch.setattr(llm_adapter, "load_json", lambda *_args, **_kwargs: {})

    cfg = llm_adapter.get_config(redact=False)

    assert llm_adapter.is_available() is True
    assert cfg["provider"] == "vertex_gemini"
    assert cfg["source"] == "env_fallback"
    assert "/projects/flow-gcloud/" in cfg["api_url"]


def test_config_work_path_blocks_external_env_fallback(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(llm_adapter, "_path_exists", lambda path: path == "/config/work")
    monkeypatch.setattr(llm_adapter, "load_json", lambda *_args, **_kwargs: {})

    cfg = llm_adapter.get_config(redact=False)

    assert llm_adapter.is_available() is False
    assert cfg["provider"] == "generic"
    assert cfg["external_ai_blocked"] is True
    assert cfg["external_ai_block_reason"] == "/config/work exists"


def test_config_work_path_blocks_configured_external_provider(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setattr(llm_adapter, "_path_exists", lambda path: path == "/config/work")
    monkeypatch.setattr(
        llm_adapter,
        "load_json",
        lambda *_args, **_kwargs: {
            "llm": {
                "enabled": True,
                "api_url": "https://api.openai.com/v1",
                "provider": "openai",
                "model": "gpt-4o-mini",
                "admin_token": "sk-test",
            }
        },
    )

    cfg = llm_adapter.get_config(redact=False)

    assert llm_adapter.is_available() is False
    assert cfg["api_url"] == ""
    assert cfg["blocked_provider"] == "openai"
    assert cfg["external_ai_block_reason"] == "/config/work exists"


def test_playground_profile_blocks_external_env_fallback(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(llm_adapter, "_path_exists", lambda _path: False)
    monkeypatch.setattr(
        llm_adapter,
        "load_json",
        lambda *_args, **_kwargs: {
            "llm_profiles": {
                "playground": {
                    "enabled": True,
                    "provider": "playground",
                    "api_url": "https://playground.internal/v1",
                }
            },
        },
    )

    cfg = llm_adapter.get_config(redact=False)

    assert llm_adapter.is_available() is False
    assert cfg["provider"] == "generic"
    assert cfg["external_ai_blocked"] is True
    assert cfg["external_ai_block_reason"] == "playground profile configured"


def test_active_playground_profile_remains_available_while_external_is_blocked(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(llm_adapter, "_path_exists", lambda _path: False)
    monkeypatch.setattr(
        llm_adapter,
        "load_json",
        lambda *_args, **_kwargs: {
            "llm": {
                "enabled": True,
                "api_url": "https://playground.internal/v1/chat",
                "provider": "playground",
                "admin_token": "dep-ticket",
                "system_name": "playground",
            }
        },
    )

    cfg = llm_adapter.get_config(redact=False)

    assert llm_adapter.is_available() is True
    assert cfg["provider"] == "playground"
    assert cfg["api_url"] == "https://playground.internal/v1/chat"
    assert cfg["external_ai_blocked"] is True
    assert cfg["external_ai_block_reason"] == "playground profile active"


def test_connected_internal_profile_prevents_active_dev_provider(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setattr(llm_adapter, "_path_exists", lambda _path: False)
    monkeypatch.setattr(
        llm_adapter,
        "load_json",
        lambda *_args, **_kwargs: {
            "llm_profiles": {
                "openai_compatible": {
                    "enabled": True,
                    "api_url": "https://llm.internal/v1/chat/completions",
                    "provider": "openai_compatible",
                    "model": "gpt-oss-120b",
                    "format": "openai",
                },
                "vertex_gemini": {
                    "enabled": True,
                    "api_url": "https://aiplatform.googleapis.com/v1/projects/dev/locations/us-central1/endpoints/openapi/chat/completions",
                    "provider": "vertex_gemini",
                    "model": "google/gemini-2.5-flash",
                    "format": "openai",
                },
            },
            "llm": {
                "enabled": True,
                "api_url": "https://aiplatform.googleapis.com/v1/projects/dev/locations/us-central1/endpoints/openapi/chat/completions",
                "provider": "vertex_gemini",
                "model": "google/gemini-2.5-flash",
                "format": "openai",
            },
        },
    )

    cfg = llm_adapter.get_config(redact=False)

    assert llm_adapter.is_available() is True
    assert cfg["provider"] == "openai_compatible"
    assert cfg["api_url"] == "https://llm.internal/v1/chat/completions"
    assert cfg["dev_ai_blocked"] is True
    assert cfg["blocked_provider"] == "vertex_gemini"


def test_connected_internal_profile_prevents_env_dev_fallback(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("FLOW_LLM_ENABLE_ENV_FALLBACK", "1")
    monkeypatch.setenv("FLOW_LLM_PROVIDER", "vertex")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "flow-dev")
    monkeypatch.setattr(llm_adapter, "_path_exists", lambda _path: False)
    monkeypatch.setattr(
        llm_adapter,
        "load_json",
        lambda *_args, **_kwargs: {
            "llm_profiles": {
                "openai_compatible": {
                    "enabled": True,
                    "api_url": "https://llm.internal/v1/chat/completions",
                    "provider": "openai_compatible",
                    "model": "gpt-oss-120b",
                    "format": "openai",
                },
            },
            "llm": {
                "enabled": True,
                "api_url": "",
                "provider": "generic",
            },
        },
    )

    cfg = llm_adapter.get_config(redact=False)

    assert llm_adapter.is_available() is True
    assert cfg["provider"] == "openai_compatible"
    assert cfg["source"] != "env_fallback"
    assert cfg["dev_ai_blocked"] is True


def test_google_adc_ignores_stored_admin_token_and_replaces_auth_header(monkeypatch):
    cfg = {
        "provider": "openai_compatible",
        "auth_mode": "google_adc",
        "admin_token": "stale-token",
        "headers": {"Authorization": "Bearer {token}", "X-Token": "{token}"},
        "format": "openai",
        "extra_body": {},
        "model": "google/gemini-2.5-flash",
    }
    monkeypatch.setattr(llm_adapter, "_google_adc_access_token", lambda *_args, **_kwargs: "adc-token")

    headers = _build_request_headers(cfg, timeout_s=3)

    assert headers["Authorization"] == "Bearer adc-token"
    assert "stale-token" not in str(headers)
    assert "X-Token" not in headers


def test_google_adc_falls_back_to_gcloud_user_token(monkeypatch):
    llm_adapter._clear_google_adc_token_cache()
    calls = []

    def fake_gcloud(args, *, timeout_s, label):
        calls.append((args, timeout_s, label))
        if args == ["auth", "print-access-token"]:
            return "user-token"
        return ""

    monkeypatch.setattr(llm_adapter, "_google_auth_adc_access_token", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(llm_adapter, "_gcloud_access_token", fake_gcloud)

    token = llm_adapter._google_adc_access_token(timeout_s=4)

    assert token == "user-token"
    assert [c[0] for c in calls] == [
        ["auth", "application-default", "print-access-token"],
        ["auth", "print-access-token"],
    ]
    llm_adapter._clear_google_adc_token_cache()


def test_google_adc_access_token_uses_process_cache(monkeypatch):
    llm_adapter._clear_google_adc_token_cache()
    calls = []

    def fake_google_auth(*_args, **_kwargs):
        calls.append("google-auth")
        return "cached-token"

    monkeypatch.setattr(llm_adapter, "_google_auth_adc_access_token", fake_google_auth)
    monkeypatch.setattr(
        llm_adapter,
        "_gcloud_access_token",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("gcloud should not run")),
    )

    first = llm_adapter._google_adc_access_token(timeout_s=3)
    second = llm_adapter._google_adc_access_token(timeout_s=3)

    assert first == "cached-token"
    assert second == "cached-token"
    assert calls == ["google-auth"]
    status = llm_adapter._google_adc_token_cache_status()
    assert status["cached"] is True
    assert status["source"] == "google-auth"
    llm_adapter._clear_google_adc_token_cache()


def test_google_adc_header_timeout_is_applied_to_token_generation(monkeypatch):
    cfg = {
        "provider": "vertex_gemini",
        "auth_mode": "google_adc",
        "admin_token": "",
        "headers": {},
        "format": "openai",
        "extra_body": {},
        "model": "google/gemini-2.5-flash",
    }
    calls = []

    def fake_token(*, timeout_s):
        calls.append(timeout_s)
        return "adc-token"

    monkeypatch.setattr(llm_adapter, "_google_adc_access_token", fake_token)

    headers = _build_request_headers(cfg, timeout_s=2)

    assert headers["Authorization"] == "Bearer adc-token"
    assert calls == [2]


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


def test_list_profiles_empty_admin_settings(monkeypatch):
    monkeypatch.setattr(llm_adapter, "load_json", lambda *_args, **_kwargs: {})
    assert llm_adapter.list_profiles() == []


def test_list_profiles_active_only(monkeypatch):
    monkeypatch.setattr(
        llm_adapter,
        "load_json",
        lambda *_args, **_kwargs: {"llm": {"provider": "openai_compatible", "enabled": True}},
    )
    assert llm_adapter.list_profiles() == ["openai_compatible"]


def test_list_profiles_merges_profiles_and_active(monkeypatch):
    monkeypatch.setattr(
        llm_adapter,
        "load_json",
        lambda *_args, **_kwargs: {
            "llm_profiles": {"openai_compatible": {"provider": "openai_compatible"}, "vertex_gemini": {"provider": "vertex_gemini"}},
            "llm": {"provider": "openai_compatible"},
        },
    )
    result = llm_adapter.list_profiles()
    assert sorted(result) == ["openai_compatible", "vertex_gemini"]
    # active provider does not duplicate when already in llm_profiles
    assert result.count("openai_compatible") == 1


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


# --- env-fallback is opt-in -------------------------------------------------

def test_env_fallback_requires_opt_in(monkeypatch):
    _clear_llm_env(monkeypatch)
    # A Google project in the environment must NOT silently enable an external
    # endpoint when the admin api_url is blank and the opt-in flag is absent.
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "flow-dev")
    monkeypatch.setattr(llm_adapter, "_path_exists", lambda _path: False)
    monkeypatch.setattr(llm_adapter, "load_json", lambda *_args, **_kwargs: {})

    cfg = llm_adapter.get_config(redact=False)

    assert llm_adapter.is_available() is False
    assert cfg["api_url"] == ""
    assert cfg.get("source") != "env_fallback"


def test_env_fallback_reads_local_dotenv_when_process_env_is_empty(monkeypatch, tmp_path):
    _clear_llm_env(monkeypatch)
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "GOOGLE_CLOUD_PROJECT=flow-dotenv\n"
        "GOOGLE_CLOUD_LOCATION=asia-northeast3\n"
        "FLOW_LLM_ENABLE_ENV_FALLBACK=1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(llm_adapter, "_DOTENV_FILE", dotenv, raising=False)
    monkeypatch.setattr(llm_adapter, "_path_exists", lambda _path: False)
    monkeypatch.setattr(
        llm_adapter,
        "load_json",
        lambda *_args, **_kwargs: {
            "llm": {
                "enabled": True,
                "provider": "vertex_gemini",
                "model": "google/gemini-2.5-flash",
                "auth_mode": "google_adc",
            }
        },
    )

    cfg = llm_adapter.get_config(redact=False)

    assert llm_adapter.is_available() is True
    assert cfg["source"] == "env_fallback"
    assert cfg["provider"] == "vertex_gemini"
    assert "/projects/flow-dotenv/" in cfg["api_url"]
    assert "/locations/asia-northeast3/" in cfg["api_url"]


# --- LLM health circuit breaker ---------------------------------------------

_OPENAI_FALLBACK_ADMIN = {
    "llm": {
        "enabled": True,
        "api_url": "https://api.openai.com/v1",
        "provider": "openai",
        "admin_token": "sk-test",
        "model": "gpt-4o-mini",
    }
}


def test_circuit_breaker_marks_and_blocks():
    llm_adapter.reset_llm_health()
    try:
        assert llm_adapter.should_attempt_llm() is True
        llm_adapter._mark_llm_unhealthy("boom", 1200)
        assert llm_adapter.should_attempt_llm() is False
        snap = llm_adapter.health_snapshot()
        assert snap["status"] == "unhealthy"
        assert snap["breaker_open"] is True
        assert snap["last_error"] == "boom"
        assert snap["cooldown_remaining_s"] > 0
        llm_adapter._mark_llm_healthy(50)
        assert llm_adapter.should_attempt_llm() is True
        assert llm_adapter.health_snapshot()["breaker_open"] is False
    finally:
        llm_adapter.reset_llm_health()


def test_complete_short_circuits_when_breaker_open(monkeypatch):
    llm_adapter.reset_llm_health()
    monkeypatch.setattr(llm_adapter, "_path_exists", lambda _p: False)
    monkeypatch.setattr(llm_adapter, "load_json", lambda *_a, **_k: _OPENAI_FALLBACK_ADMIN)
    calls = {"n": 0}

    def fake_urlopen(*_a, **_k):
        calls["n"] += 1
        raise OSError("network down")

    monkeypatch.setattr(llm_adapter.urllib.request, "urlopen", fake_urlopen)
    try:
        llm_adapter._mark_llm_unhealthy("prior failure")
        out = llm_adapter.complete("hi")
        assert out["ok"] is False
        assert "circuit breaker open" in out["error"]
        assert calls["n"] == 0  # breaker prevented the HTTP attempt

        # An explicit probe bypasses the breaker and actually attempts the call.
        out2 = llm_adapter.complete("hi", probe=True)
        assert out2["ok"] is False
        assert calls["n"] == 1
    finally:
        llm_adapter.reset_llm_health()


def test_complete_opens_and_recovers_breaker(monkeypatch):
    llm_adapter.reset_llm_health()
    monkeypatch.setattr(llm_adapter, "_path_exists", lambda _p: False)
    monkeypatch.setattr(llm_adapter, "load_json", lambda *_a, **_k: _OPENAI_FALLBACK_ADMIN)

    # First: a failing call opens the breaker.
    monkeypatch.setattr(
        llm_adapter.urllib.request,
        "urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("boom")),
    )
    try:
        out = llm_adapter.complete("hi")
        assert out["ok"] is False
        assert llm_adapter.should_attempt_llm() is False

        # Then: a successful probe closes it again.
        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

            def read(self, *_a):
                return b'{"choices":[{"message":{"content":"hello"}}]}'

        monkeypatch.setattr(llm_adapter.urllib.request, "urlopen", lambda *_a, **_k: FakeResp())
        ok = llm_adapter.complete("hi", probe=True)
        assert ok["ok"] is True
        assert ok["text"] == "hello"
        assert llm_adapter.should_attempt_llm() is True
    finally:
        llm_adapter.reset_llm_health()
