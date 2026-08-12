import asyncio

from backend.routers import informs as mod


class _Upload:
    filename = "clipboard"
    content_type = "image/x-png"

    async def read(self):
        return b"flow-image-test"


class _Request:
    async def form(self):
        return {"file": _Upload()}


def test_inform_upload_accepts_windows_clipboard_image_mimes(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "UPLOADS_DIR", tmp_path)
    monkeypatch.setattr(mod, "current_user", lambda _request: {"username": "alice"})

    assert mod._image_upload_ext("clipboard", "image/x-png") == ".png"
    assert mod._image_upload_ext("clipboard", "image/pjpeg") == ".jpg"
    assert mod._image_upload_ext("clipboard", "image/x-ms-bmp") == ".bmp"

    result = asyncio.run(mod.upload_image(_Request()))
    assert result["ok"] is True
    assert result["filename"].endswith(".png")
    assert result["url"].startswith("/api/informs/files/")
    saved = list(tmp_path.rglob("*.png"))
    assert len(saved) == 1
    assert saved[0].read_bytes() == b"flow-image-test"
