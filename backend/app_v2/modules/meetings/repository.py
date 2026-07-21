from __future__ import annotations

from pathlib import Path

from app_v2.shared.json_store import JsonFileStore


class MeetingRepository:
    def __init__(self, path: Path):
        self.store = JsonFileStore(path, default=list)

    def list_meetings(self) -> list[dict]:
        data = self.store.load()
        return data if isinstance(data, list) else []

    def create_meeting(self, meeting: dict) -> dict:
        def updater(current):
            rows = current if isinstance(current, list) else []
            rows.append(meeting)
            return rows

        self.store.load_and_update(updater)
        return meeting

    def replace_meeting(self, meeting_id: str, replacement: dict) -> dict | None:
        saved = {"row": None}

        def updater(current):
            rows = current if isinstance(current, list) else []
            out = []
            for row in rows:
                if row.get("id") != meeting_id:
                    out.append(row)
                    continue
                saved["row"] = replacement
                out.append(replacement)
            return out

        self.store.load_and_update(updater)
        return saved["row"]

    def delete_meeting(self, meeting_id: str) -> dict | None:
        saved = {"row": None}

        def updater(current):
            rows = current if isinstance(current, list) else []
            out = []
            for row in rows:
                if row.get("id") == meeting_id:
                    saved["row"] = row
                    continue
                out.append(row)
            return out

        self.store.load_and_update(updater)
        return saved["row"]
