"""사내 LLM User-Id 치환과 실행 권한 정책."""
from core import llm_adapter


CFG = {"provider": "gemma4", "system_name": "flow", "user_id": "saved.knox", "user_type": "fixed"}


def _user_id(user):
    with llm_adapter.request_execution_scope(user, path="/api/home-agent/orchestrate"):
        return llm_adapter.llm_user_id(CFG)


def test_builtin_hol_admin_uses_saved_user_id():
    assert _user_id({"username": "hol", "role": "admin"}) == "saved.knox"


def test_other_admins_and_users_use_their_flow_id():
    assert _user_id({"username": "kim.admin", "role": "admin"}) == "kim.admin"
    assert _user_id({"username": "lee", "role": "user"}) == "lee"


def test_hol_without_saved_id_falls_back_to_login_name():
    with llm_adapter.request_execution_scope({"username": "hol", "role": "admin"}):
        assert llm_adapter.llm_user_id({"user_id": ""}) == "hol"


def test_header_template_token_uses_resolved_user_id():
    with llm_adapter.request_execution_scope({"username": "lee", "role": "user"}):
        text = llm_adapter._replace_header_tokens("id={user_id}", token="", prompt_msg_id="",
                                                  completion_msg_id="", cfg=CFG)
    assert text == "id=lee"


def test_regular_user_allowed_but_anonymous_denied():
    with llm_adapter.request_execution_scope({"username": "lee", "role": "user"},
                                             path="/api/home-agent/orchestrate"):
        assert llm_adapter._execution_denial() == ""
    with llm_adapter.request_execution_scope(None, path="/api/home-agent/orchestrate"):
        assert llm_adapter._execution_denial()


def test_has_page_access(monkeypatch):
    from core import auth
    monkeypatch.setattr(auth, "get_page_admins", lambda: {})
    assert auth.has_page_access({"username": "a", "role": "admin"}, "dcop")
    assert auth.has_page_access({"username": "u", "role": "user", "tabs": "dcop,filebrowser"}, "dcop")
    assert auth.has_page_access({"username": "u", "role": "user", "tabs": "__all__"}, "chartbuilder")
    assert not auth.has_page_access({"username": "u", "role": "user", "tabs": "filebrowser"}, "dcop")
