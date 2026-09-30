import importlib.util
import io
from pathlib import Path

from core import auth, runtime_limits

ROOT = Path(__file__).resolve().parents[1]


def _load_flow_server():
    spec = importlib.util.spec_from_file_location("flow_server_under_test", ROOT / "scripts" / "flow_server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_token_file_is_written_outside_the_request_lock(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "TOKENS_FILE", tmp_path / "tokens.json")
    monkeypatch.setattr(auth, "_cache", {})
    monkeypatch.setattr(auth, "_cache_loaded", True)
    lock_held = []
    real_write = auth._write_tokens_file

    def spy(gen, payload):
        lock_held.append(auth._lock.locked())
        real_write(gen, payload)

    monkeypatch.setattr(auth, "_write_tokens_file", spy)

    token, _ = auth.issue_token("vm-user", "user", auth_method="websocket")
    assert auth.validate_token(token)["username"] == "vm-user"
    assert token in (tmp_path / "tokens.json").read_text("utf-8")
    auth.revoke_token(token)
    auth.issue_token("vm-user", "user")
    assert auth.revoke_user_tokens("vm-user") == 1

    assert lock_held == [False, False, False, False]
    assert "vm-user" not in (tmp_path / "tokens.json").read_text("utf-8")


class _FakeProc:
    pid = 4242
    stdout = io.BytesIO(b"")


class _FakeLog:
    def line(self, text):
        pass

    def write(self, data):
        pass


def _spawn_cmd(monkeypatch, module):
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeProc()

    monkeypatch.setattr(module.subprocess, "Popen", fake_popen)
    args = type("Args", (), {"python": "python", "host": "0.0.0.0", "port": 8080, "uvicorn_args": []})()
    module._spawn(args, _FakeLog())
    return captured["cmd"]


def test_supervisor_disables_uvicorn_access_log_by_default(monkeypatch):
    module = _load_flow_server()
    monkeypatch.delenv("FLOW_UVICORN_ACCESS_LOG", raising=False)
    assert "--no-access-log" in _spawn_cmd(monkeypatch, module)
    monkeypatch.setenv("FLOW_UVICORN_ACCESS_LOG", "1")
    assert "--no-access-log" not in _spawn_cmd(monkeypatch, module)


def test_ad_websocket_message_fields_are_read_by_default(monkeypatch):
    from core import auth_providers as ap

    for name in ("USER", "DEPT", "NAME", "EMAIL"):
        monkeypatch.delenv(f"FLOW_WS_AUTH_{name}_FIELDS", raising=False)
    msg = ap._ws_parse('{"ad": {"department": "Dept A", "company": "C", "mail": "hong@corp.example", '
                       '"title": "T", "description": "D", "name": "Hong Gildong"}}')
    assert ap._ws_pick(msg, ap._ws_env_list("FLOW_WS_AUTH_USER_FIELDS", ap._WS_DEFAULT_USER_FIELDS)) == "hong@corp.example"
    assert ap._ws_pick(msg, ap._ws_env_list("FLOW_WS_AUTH_DEPT_FIELDS", ap._WS_DEFAULT_DEPT_FIELDS)) == "Dept A"
    assert ap._ws_pick(msg, ap._ws_env_list("FLOW_WS_AUTH_NAME_FIELDS", ap._WS_DEFAULT_NAME_FIELDS)) == "Hong Gildong"
    assert ap._ws_pick(msg, ap._ws_env_list("FLOW_WS_AUTH_EMAIL_FIELDS", ap._WS_DEFAULT_EMAIL_FIELDS)) == "hong@corp.example"


def test_host_diagnostics_flags_vm_memory_that_hides_large_profile(monkeypatch):
    monkeypatch.setattr(runtime_limits, "system_memory_snapshot", lambda *a, **k: {"system_memory_total_gb": 16.0})
    monkeypatch.setattr(runtime_limits, "effective_cpu_count", lambda: 8.0)
    monkeypatch.setattr(runtime_limits, "resource_profile", lambda: "small")
    monkeypatch.setenv("FLOW_RESOURCE_PROFILE_SOURCE", "auto")

    info, warnings = runtime_limits.host_diagnostics({"FLOW_DATA_ROOT": r"\\fileserver\flow-data"})

    assert info["profile"] == "small" and info["cores"] == 8.0
    assert any("FLOW_RESOURCE_PROFILE=large" in w for w in warnings)
    assert any("네트워크 드라이브" in w for w in warnings)

    monkeypatch.setenv("FLOW_RESOURCE_PROFILE_SOURCE", "env")
    _, warnings = runtime_limits.host_diagnostics({"FLOW_DATA_ROOT": str(ROOT)})
    assert warnings == []
