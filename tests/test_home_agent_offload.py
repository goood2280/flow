"""Home agent turns prefer the development worker but never depend on it."""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException


@pytest.mark.parametrize(("prompt", "expected"), [
    ("PRODA A1001 ET VTH 추이 차트 그려줘", "heavy"),
    ("A1001 인라인 CD 분포 비교", "heavy"),
    ("A1001 스플릿테이블 보여줘", "light"),
    ("승인", "light"),
    ("무엇을 할 수 있어?", "heavy"),
])
def test_execution_class(prompt, expected):
    from core import home_agent_offload

    assert home_agent_offload.execution_class(prompt)[0] == expected


def _stub_turn(monkeypatch, calls):
    from core import flowi_turn

    def execute(prompt, context, request, history=None):
        calls.append({"prompt": prompt, "user": getattr(getattr(request, "state", None), "user", None)})
        return {"ok": True, "reply": "local", "context": context}

    monkeypatch.setattr(flowi_turn, "execute", execute)


def test_worker_offline_runs_locally(monkeypatch):
    from core import home_agent_offload, worker_dispatch

    calls = []
    _stub_turn(monkeypatch, calls)
    monkeypatch.setattr(worker_dispatch, "server_role", lambda: "api")
    monkeypatch.setattr(worker_dispatch, "offload_enabled", lambda: True)
    monkeypatch.setattr(worker_dispatch, "worker_alive", lambda **_: False)

    out = home_agent_offload.run_turn("ET 차트 그려줘", {}, object(), [], user={"username": "u"})
    assert out["reply"] == "local"
    assert out["execution"]["target"] == "production_api_fallback"
    assert len(calls) == 1


def test_remote_result_is_returned_without_local_execution(monkeypatch):
    from core import home_agent_offload, worker_dispatch

    calls, submitted = [], []
    _stub_turn(monkeypatch, calls)
    monkeypatch.setattr(worker_dispatch, "server_role", lambda: "api")

    def run_heavy(task_type, payload, local_fn, **kwargs):
        submitted.append((task_type, payload, kwargs))
        return {"ok": True, "result": {"ok": True, "reply": "remote"}}

    monkeypatch.setattr(worker_dispatch, "run_heavy", run_heavy)
    out = home_agent_offload.run_turn("INLINE 추이 차트", {"a": 1}, object(), [{"role": "user", "content": "x"}],
                                      user={"username": "u", "role": "user", "token": "secret", "last_seen": 1})
    assert out["reply"] == "remote"
    assert out["execution"]["target"] == "development_worker"
    assert calls == []
    task_type, payload, kwargs = submitted[0]
    assert task_type == "home_agent_turn"
    assert kwargs["priority"] == "interactive"
    assert payload["user"] == {"username": "u", "role": "user"}
    assert payload["code_version"] == home_agent_offload.code_version()


def test_remote_http_error_is_raised_not_rerun(monkeypatch):
    from core import home_agent_offload, worker_dispatch

    calls = []
    _stub_turn(monkeypatch, calls)
    monkeypatch.setattr(worker_dispatch, "server_role", lambda: "api")
    monkeypatch.setattr(worker_dispatch, "run_heavy",
                        lambda *a, **k: {"ok": True, "http_error": {"status": 403, "detail": "권한 없음"}})
    with pytest.raises(HTTPException) as err:
        home_agent_offload.run_turn("ET 차트", {}, object(), [], user={"username": "u"})
    assert err.value.status_code == 403
    assert calls == []


def test_light_turn_stays_on_production(monkeypatch):
    from core import home_agent_offload, worker_dispatch

    calls = []
    _stub_turn(monkeypatch, calls)
    monkeypatch.setattr(worker_dispatch, "server_role", lambda: "api")
    monkeypatch.setattr(worker_dispatch, "run_heavy", lambda *a, **k: pytest.fail("light turn offloaded"))
    out = home_agent_offload.run_turn("승인", {}, object(), [], user={"username": "u"})
    assert out["execution"]["target"] == "production_api"


def test_worker_payload_checks_version_and_rebuilds_user(monkeypatch):
    from core import home_agent_offload, llm_adapter

    calls = []
    _stub_turn(monkeypatch, calls)
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)
    assert home_agent_offload.execute_payload({"code_version": "0.0.0", "user": {"username": "u"}})["error"] == "version_mismatch"

    out = home_agent_offload.execute_payload({
        "code_version": home_agent_offload.code_version(), "prompt": "ET 차트",
        "context": {"k": "v"}, "history": [], "user": {"username": "u", "role": "admin"},
    })
    assert out["ok"] is True and out["result"]["reply"] == "local"
    assert calls[0]["user"] == {"username": "u", "role": "admin"}
    json.dumps(out)

    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)
    assert home_agent_offload.execute_payload({
        "code_version": home_agent_offload.code_version(), "user": {"username": "u"},
    })["error"] == "llm_unavailable_on_worker"


def test_synthetic_request_resolves_user_for_auth():
    from core import auth, home_agent_offload

    request = home_agent_offload._synthetic_request({"username": "u", "role": "user"})
    assert auth.current_user(request)["username"] == "u"


def test_interactive_lane_claims_only_interactive(monkeypatch, tmp_path):
    from core import worker_dispatch

    queue = tmp_path / "queue"
    claimed = tmp_path / "claimed"
    queue.mkdir()
    claimed.mkdir()
    monkeypatch.setattr(worker_dispatch, "_queue_dir", lambda: queue)
    monkeypatch.setattr(worker_dispatch, "_claimed_dir", lambda: claimed)
    monkeypatch.setattr(worker_dispatch, "api_alive", lambda: True)
    for name, priority in (("001-build", "normal"), ("002-chat", "interactive")):
        (queue / f"{name}.task.json").write_text(json.dumps({"id": name, "type": "t", "priority": priority}), encoding="utf-8")

    task, _fp = worker_dispatch._claim_next(only_interactive=True)
    assert task["id"] == "002-chat"
    assert worker_dispatch._claim_next(only_interactive=True) is None
    task, _fp = worker_dispatch._claim_next()
    assert task["id"] == "001-build"
