import json

from core import auth


def _permission_files(tmp_path):
    (tmp_path / "groups").mkdir()
    (tmp_path / "admin_settings.json").write_text(json.dumps({
        "page_admins": {"inform": ["u1"], "spc": ["u2"]},
    }), encoding="utf-8")
    (tmp_path / "groups" / "groups.json").write_text(json.dumps([
        {"id": "g1", "owner": "u1", "members": ["u2", "u3"]},
        {"name": "g2", "owner": "u3", "members": ["u1"]},
    ]), encoding="utf-8")


def test_bulk_permissions_match_single_user_helper(monkeypatch, tmp_path):
    _permission_files(tmp_path)
    monkeypatch.setattr(auth.PATHS, "data_root", tmp_path)
    users = [
        {"username": "u1", "role": "user", "tabs": "inform"},
        {"username": "u2", "role": "user", "tabs": "spc"},
        {"username": "admin", "role": "admin", "tabs": ""},
    ]

    assert auth.effective_permissions_bulk(users) == [
        auth.effective_permissions(user) for user in users
    ]


def test_bulk_permissions_reads_each_source_once(monkeypatch, tmp_path):
    _permission_files(tmp_path)
    monkeypatch.setattr(auth.PATHS, "data_root", tmp_path)
    users = [{"username": f"u{i}", "role": "user"} for i in range(250)]
    reads = {"page_admins": 0, "groups": 0}
    original_read_text = type(tmp_path).read_text

    def counted_read_text(path, *args, **kwargs):
        if path.name == "admin_settings.json":
            reads["page_admins"] += 1
        elif path.name == "groups.json":
            reads["groups"] += 1
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(type(tmp_path), "read_text", counted_read_text)
    auth.effective_permissions_bulk(users)

    assert reads == {"page_admins": 1, "groups": 1}


def test_bulk_permissions_sees_file_updates_on_next_call(monkeypatch, tmp_path):
    _permission_files(tmp_path)
    monkeypatch.setattr(auth.PATHS, "data_root", tmp_path)
    user = {"username": "u4", "role": "user"}
    assert auth.effective_permissions_bulk([user])[0]["page_manager"] == []

    (tmp_path / "admin_settings.json").write_text(json.dumps({
        "page_admins": {"inform": ["u4"]},
    }), encoding="utf-8")
    (tmp_path / "groups" / "groups.json").write_text(json.dumps([
        {"id": "new-group", "members": ["u4"]},
    ]), encoding="utf-8")

    result = auth.effective_permissions_bulk([user])[0]
    assert result["page_manager"] == ["inform"]
    assert result["groups"] == {"all": False, "owner": [], "member": ["new-group"]}
