import json

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app_v2.runtime.security import _allow_query_token
from routers import guides as api


@pytest.fixture
def guide_root(tmp_path, monkeypatch):
    root = tmp_path / "_guides"
    folder = root / "filebrowser"
    folder.mkdir(parents=True)
    (folder / "filebrowser_720p.mp4").write_bytes(b"\x00" * 2048)
    (folder / "chapters.ko.vtt").write_text("WEBVTT\n", encoding="utf-8")
    (folder / "guide.json").write_text(json.dumps({
        "title": "파일탐색기 사용법",
        "duration": 40,
        "sources": [
            {"file": "filebrowser_720p.mp4", "label": "720p", "height": 720},
            {"file": "missing_1080p.mp4", "label": "1080p", "height": 1080},
        ],
        "captions": "chapters.ko.vtt",
        "poster": "../../secret.webp",
        "chapters": [{"t": 0, "title": "소개"}, {"t": "x", "title": "깨진 값"}],
        "notes": [{"title": "집계", "code": ["SELECT a, AVG(v) GROUP BY a"]}],
    }), encoding="utf-8")
    # 영상 없는 가이드와 이름 규칙에 어긋난 폴더는 목록에서 빠진다.
    (root / "empty").mkdir()
    (root / "empty" / "guide.json").write_text(json.dumps({"sources": [{"file": "x.mp4"}]}), encoding="utf-8")
    (root / "Bad Name").mkdir()
    monkeypatch.setenv("FLOW_GUIDES_DIR", str(root))
    return root


@pytest.fixture
def client():
    app = FastAPI()

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):
        if request.headers.get("x-test-user"):
            request.state.user = {"username": "alice", "role": "user"}
        return await call_next(request)

    app.include_router(api.router)
    return TestClient(app)


def test_list_only_guides_with_existing_media(guide_root, client):
    data = client.get("/api/guides", headers={"x-test-user": "1"}).json()["guides"]
    assert list(data) == ["filebrowser"]
    guide = data["filebrowser"]
    assert [s["label"] for s in guide["sources"]] == ["720p"]
    assert guide["sources"][0]["url"] == "/api/guides/media/filebrowser/filebrowser_720p.mp4"
    assert guide["captions"].endswith("/chapters.ko.vtt")
    assert guide["poster"] == ""
    assert [c["title"] for c in guide["chapters"]] == ["소개"]
    assert guide["notes"][0]["code"] == ["SELECT a, AVG(v) GROUP BY a"]


def test_media_serves_range_and_blocks_traversal(guide_root, client):
    headers = {"x-test-user": "1"}
    whole = client.get("/api/guides/media/filebrowser/filebrowser_720p.mp4", headers=headers)
    assert whole.status_code == 200 and whole.headers["content-type"] == "video/mp4"
    part = client.get("/api/guides/media/filebrowser/filebrowser_720p.mp4", headers={**headers, "Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100
    (guide_root.parent / "secret.mp4").write_bytes(b"x")
    for url in (
        "/api/guides/media/filebrowser/..%2F..%2Fsecret.mp4",
        "/api/guides/media/filebrowser/guide.json",
        "/api/guides/media/Bad%20Name/a.mp4",
        "/api/guides/media/filebrowser/nope.mp4",
    ):
        assert client.get(url, headers=headers).status_code == 404, url


def test_requires_login_and_media_accepts_query_token(guide_root, client):
    assert client.get("/api/guides").status_code == 401
    assert _allow_query_token("/api/guides/media/filebrowser/filebrowser_720p.mp4")
    assert not _allow_query_token("/api/guides")


def test_no_guide_folder_returns_empty(tmp_path, monkeypatch, client):
    monkeypatch.setenv("FLOW_GUIDES_DIR", str(tmp_path / "none"))
    monkeypatch.setattr(api, "_candidate_roots", lambda: [tmp_path / "none"])
    assert client.get("/api/guides", headers={"x-test-user": "1"}).json() == {"guides": {}}
