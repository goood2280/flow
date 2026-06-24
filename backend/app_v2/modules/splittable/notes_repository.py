from core.utils import load_json, save_json
from core.paths import PATHS
from pathlib import Path

PLAN_DIR = PATHS.data_root / "splittable"
NOTES_FILE = PLAN_DIR / "notes.json"

class NotesRepository:
    def load_notes(self) -> list:
        data = load_json(NOTES_FILE, {"entries": []})
        if isinstance(data, dict):
            return data.get("entries", [])
        return data if isinstance(data, list) else []

    def save_notes(self, entries: list) -> None:
        save_json(NOTES_FILE, {"entries": entries})
