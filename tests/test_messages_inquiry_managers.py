from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException

from routers import messages
from routers import admin as admin_router


def _request(username):
    return SimpleNamespace(state=SimpleNamespace(user={"username": username, "role": "user"}))


def test_delegated_managers_receive_inquiries_and_can_reply_independently(tmp_path, monkeypatch):
    threads_dir = tmp_path / "threads"
    threads_dir.mkdir()
    monkeypatch.setattr(messages, "THREADS_DIR", threads_dir)
    monkeypatch.setattr(messages, "get_page_admins", lambda: {"tracker": ["manager_a"], "calendar": ["manager_b"]})
    monkeypatch.setattr(messages, "read_users", lambda: [
        {"username": "admin", "role": "admin", "status": "approved"},
        {"username": "manager_a", "role": "user", "status": "approved"},
        {"username": "manager_b", "role": "user", "status": "approved"},
        {"username": "inactive", "role": "admin", "status": "pending"},
        {"username": "sender", "role": "user", "status": "approved"},
    ])
    monkeypatch.setattr(messages, "current_user", lambda request: request.state.user)

    def verify_owner(request, username):
        if request.state.user["username"] != username:
            raise HTTPException(403)

    monkeypatch.setattr(messages, "verify_owner", verify_owner)
    notifications = []
    monkeypatch.setattr(messages, "send_notify", lambda *args: notifications.append(args))

    messages.user_send(messages.SendReq(username="sender", text="Help"), _request("sender"))
    assert {item[0] for item in notifications} == {"admin", "manager_a", "manager_b"}
    assert messages.admin_unread(_request("manager_a"), "manager_a")["total"] == 1
    assert messages.admin_unread(_request("manager_b"), "manager_b")["total"] == 1
    assert messages.admin_get_thread(_request("manager_a"), "manager_a", "sender")["messages"][0]["text"] == "Help"

    messages.admin_mark_read(messages.AdminThreadReq(admin="manager_a", to_user="sender"), _request("manager_a"))
    assert messages.admin_unread(_request("manager_a"), "manager_a")["total"] == 0
    assert messages.admin_unread(_request("manager_b"), "manager_b")["total"] == 1

    messages.admin_reply(messages.AdminReplyReq(admin="manager_b", to_user="sender", text="Done"), _request("manager_b"))
    assert messages.admin_unread(_request("manager_b"), "manager_b")["total"] == 0
    assert messages.get_thread(_request("sender"), "sender")["messages"][-1]["from"] == "manager_b"
    assert notifications[-1][0] == "sender"

    # The older admin-page inquiry form must use the same visible thread.
    monkeypatch.setattr(admin_router, "verify_owner", verify_owner)
    admin_router.send_inquiry(admin_router.InquiryReq(username="sender", message="Follow up"), _request("sender"))
    assert messages.get_thread(_request("sender"), "sender")["messages"][-1]["text"] == "Follow up"


def test_inquiry_manager_endpoints_reject_non_managers_and_spoofed_identity(monkeypatch):
    monkeypatch.setattr(messages, "get_page_admins", lambda: {"tracker": ["manager"]})
    monkeypatch.setattr(messages, "read_users", lambda: [
        {"username": "manager", "role": "user", "status": "approved"},
        {"username": "outsider", "role": "user", "status": "approved"},
    ])
    monkeypatch.setattr(messages, "current_user", lambda request: request.state.user)
    with pytest.raises(HTTPException) as outsider:
        messages.admin_get_thread(_request("outsider"), "outsider", "sender")
    assert outsider.value.status_code == 403
    with pytest.raises(HTTPException) as spoof:
        messages.admin_reply(messages.AdminReplyReq(admin="manager", to_user="sender", text="spoof"), _request("outsider"))
    assert spoof.value.status_code == 403


def test_simultaneous_manager_replies_are_not_lost(tmp_path, monkeypatch):
    threads_dir = tmp_path / "threads"
    threads_dir.mkdir()
    monkeypatch.setattr(messages, "THREADS_DIR", threads_dir)
    monkeypatch.setattr(messages, "get_page_admins", lambda: {"tracker": ["manager"]})
    monkeypatch.setattr(messages, "read_users", lambda: [
        {"username": "manager", "role": "user", "status": "approved"},
    ])
    monkeypatch.setattr(messages, "current_user", lambda request: request.state.user)
    monkeypatch.setattr(messages, "send_notify", lambda *args: None)

    def reply(index):
        messages.admin_reply(messages.AdminReplyReq(admin="manager", to_user="sender", text=str(index)), _request("manager"))

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(reply, range(12)))

    assert {m["text"] for m in messages._load_thread("sender")["messages"]} == {str(i) for i in range(12)}
