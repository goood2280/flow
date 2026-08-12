from core import upstream_proxy, worker_dispatch


def test_browser_unit_ai_posts_are_owned_by_operating_api(monkeypatch):
    monkeypatch.setenv("FLOW_API_SERVER_URL", "http://operating-api:8080")
    monkeypatch.setattr(worker_dispatch, "server_role", lambda: "worker")

    assert upstream_proxy.requires_operating(
        "/api/filebrowser/sql/llm/draft", "POST", {}
    ) is True
    assert upstream_proxy.should_proxy(
        "/api/filebrowser/chart-builder/assistant", "POST", {}
    ) is True
    assert upstream_proxy.should_proxy(
        "/api/agent/unit-ai/filebrowser_ai_sql/runtime/run", "POST", {}
    ) is True
    assert upstream_proxy.should_proxy(
        "/api/filebrowser/chart-builder/run", "POST", {}
    ) is False


def test_operating_proxy_loop_guard_prevents_a_second_hop(monkeypatch):
    monkeypatch.setenv("FLOW_API_SERVER_URL", "http://operating-api:8080")
    monkeypatch.setattr(worker_dispatch, "server_role", lambda: "worker")
    headers = {upstream_proxy.LOOP_GUARD_HEADER: "1"}

    assert upstream_proxy.requires_operating(
        "/api/filebrowser/sql/llm/draft", "POST", headers
    ) is False
    assert upstream_proxy.should_proxy(
        "/api/filebrowser/sql/llm/draft", "POST", headers
    ) is False
