import pytest
from fastapi import HTTPException

from routers import groups


def _group(name="PRODA", members=None):
    return {
        "id": "grp-1",
        "name": name,
        "owner": "owner",
        "members": list(members or []),
        "watched_lots": [],
        "modules": [],
    }


def test_group_names_are_unique_without_case_sensitivity(monkeypatch):
    records = [_group("PRODA")]
    monkeypatch.setattr(groups, "current_user", lambda request: {"username": "owner", "role": "admin"})
    monkeypatch.setattr(groups, "_load", lambda: records)

    with pytest.raises(HTTPException) as exc:
        groups.create_group(groups.GroupCreate(name="proda"), object())

    assert exc.value.status_code == 409


def test_bulk_member_ids_extracts_email_local_parts_and_deduplicates():
    assert groups._bulk_member_ids(
        "alpha@mail.api; BETA@MAIL.API,alpha@other.api\ngamma"
    ) == ["alpha", "BETA", "gamma"]


def test_add_members_bulk_resolves_registered_users_and_reports_others(monkeypatch):
    records = [_group(members=["alice"])]
    saved = []
    audits = []
    monkeypatch.setattr(groups, "current_user", lambda request: {"username": "owner", "role": "admin"})
    monkeypatch.setattr(groups, "_load", lambda: records)
    monkeypatch.setattr(groups, "_save", lambda value: saved.append(value))
    monkeypatch.setattr(groups, "_audit", lambda *args: audits.append(args))
    monkeypatch.setattr(groups, "_load_users_by_name", lambda: {
        "alice": {"username": "alice"},
        "Bob": {"username": "Bob"},
        "charlie": {"username": "charlie"},
        "testbot": {"username": "testbot"},
    })

    result = groups.add_members_bulk(
        groups.MembersBulkReq(
            entries="ALICE@mail.api;bob@mail.api;charlie@mail.api;missing@mail.api;testbot@mail.api;BOB@mail.api"
        ),
        object(),
        id="grp-1",
    )

    assert result["added"] == ["Bob", "charlie"]
    assert result["already_members"] == ["alice"]
    assert result["not_found"] == ["missing"]
    assert result["rejected"] == ["testbot"]
    assert records[0]["members"] == ["alice", "Bob", "charlie"]
    assert len(saved) == 1
    assert audits[0][1] == "member_add_bulk"
