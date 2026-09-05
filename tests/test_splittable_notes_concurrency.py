import concurrent.futures
import json
import time

from routers import splittable


def test_parallel_note_and_comment_saves_preserve_all_writers(tmp_path, monkeypatch):
    from core import auth

    path = tmp_path / "notes.json"
    monkeypatch.setattr(splittable, "NOTES_FILE", path)
    monkeypatch.setattr(auth, "current_user", lambda request: {"username": request, "role": "user"})
    monkeypatch.setattr(splittable, "_append_splittable_note_knowledge", lambda *a, **k: None)
    monkeypatch.setattr(splittable, "_notify_tracker_owner_for_note", lambda *a, **k: None)
    original_load = splittable._load_notes

    def slow_load():
        entries = original_load()
        time.sleep(0.01)  # Make the original lost-update window deterministic.
        return entries

    monkeypatch.setattr(splittable, "_load_notes", slow_load)

    def save(i):
        return splittable.save_note(splittable.NoteSaveReq(
            scope="lot", product="P", root_lot_id="L", text=f"note {i}"
        ), request=f"user{i}")["entry"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        entries = list(pool.map(save, range(12)))
    assert len(json.loads(path.read_text("utf-8"))["entries"]) == 12

    def comment(i):
        splittable.add_note_comment(splittable.NoteCommentReq(
            note_id=entries[0]["id"], text=f"comment {i}"
        ), request=f"user{i}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(comment, range(12)))
    saved = next(e for e in original_load() if e["id"] == entries[0]["id"])
    assert {c["text"] for c in saved["comments"]} == {f"comment {i}" for i in range(12)}


def test_note_transaction_releases_lock_after_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(splittable, "NOTES_FILE", tmp_path / "notes.json")
    try:
        with splittable._notes_transaction():
            raise RuntimeError("failed write")
    except RuntimeError:
        pass
    with splittable._notes_transaction():
        splittable._save_notes([{"id": "still-writable"}])
    assert splittable._load_notes() == [{"id": "still-writable"}]
