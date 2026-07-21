from __future__ import annotations

import copy
from pathlib import Path

from app_v2.modules.tracker.domain import TrackerIssue
from app_v2.shared.json_store import JsonFileStore, next_revision


class TrackerIssueRepository:
    """Example repository for tracker issues in the v2 architecture."""

    def __init__(self, path: Path):
        self.store = JsonFileStore(path, default=list)

    def list_issues(self) -> list[dict]:
        data = self.store.load()
        return data if isinstance(data, list) else []

    def create_issue(self, issue: TrackerIssue) -> dict:
        def updater(current):
            rows = current if isinstance(current, list) else []
            rows.append(issue.to_dict())
            return rows

        self.store.load_and_update(updater)
        return issue.to_dict()

    def create_issue_dict(self, issue: dict) -> dict:
        def updater(current):
            rows = current if isinstance(current, list) else []
            rows.append(issue)
            return rows

        self.store.load_and_update(updater)
        return issue

    def update_issue(self, issue_id: str, patch: dict, username: str) -> dict | None:
        saved = {"row": None}

        def updater(current):
            rows = current if isinstance(current, list) else []
            out = []
            for row in rows:
                if row.get("issue_id") != issue_id:
                    out.append(row)
                    continue
                meta = next_revision(row, username)
                row = {
                    **row,
                    **patch,
                    "updated_at": meta.updated_at,
                    "updated_by": meta.updated_by,
                    "revision": meta.revision,
                }
                saved["row"] = row
                out.append(row)
            return out

        self.store.load_and_update(updater)
        return saved["row"]

    def update_legacy_issue(self, issue_id: str, patch: dict, username: str) -> dict | None:
        saved = {"row": None}

        def updater(current):
            rows = current if isinstance(current, list) else []
            out = []
            for row in rows:
                if row.get("id") != issue_id:
                    out.append(row)
                    continue
                meta = next_revision(row, username)
                next_row = dict(row)
                append_images = patch.get("images_append") or []
                for key, value in patch.items():
                    if key == "images_append":
                        continue
                    next_row[key] = value
                if append_images:
                    next_row.setdefault("images", [])
                    next_row["images"] = list(next_row["images"]) + list(append_images)
                next_row["updated_at"] = meta.updated_at
                next_row["updated_by"] = meta.updated_by
                next_row["revision"] = meta.revision
                saved["row"] = next_row
                out.append(next_row)
            return out

        self.store.load_and_update(updater)
        return saved["row"]

    def append_legacy_comment(self, issue_id: str, comment: dict, username: str) -> dict | None:
        saved = {"row": None}

        def updater(current):
            rows = current if isinstance(current, list) else []
            out = []
            for row in rows:
                if row.get("id") != issue_id:
                    out.append(row)
                    continue
                meta = next_revision(row, username)
                next_row = dict(row)
                next_row.setdefault("comments", [])
                next_row["comments"] = list(next_row["comments"]) + [comment]
                next_row["updated_at"] = meta.updated_at
                next_row["updated_by"] = meta.updated_by
                next_row["revision"] = meta.revision
                saved["row"] = next_row
                out.append(next_row)
            return out

        self.store.load_and_update(updater)
        return saved["row"]

    def append_legacy_lots(self, issue_id: str, rows_to_add: list[dict], username: str) -> dict | None:
        saved = {"row": None}

        def updater(current):
            rows = current if isinstance(current, list) else []
            out = []
            for row in rows:
                if row.get("id") != issue_id:
                    out.append(row)
                    continue
                meta = next_revision(row, username)
                next_row = dict(row)
                next_row.setdefault("lots", [])
                next_row["lots"] = list(next_row["lots"]) + list(rows_to_add)
                next_row["updated_at"] = meta.updated_at
                next_row["updated_by"] = meta.updated_by
                next_row["revision"] = meta.revision
                saved["row"] = next_row
                out.append(next_row)
            return out

        self.store.load_and_update(updater)
        return saved["row"]

    def update_legacy_lot_watch(
        self,
        issue_id: str,
        row_index: int,
        watch_patch: dict,
        username: str,
    ) -> dict | None:
        saved = {"row": None}

        def updater(current):
            rows = current if isinstance(current, list) else []
            out = []
            for row in rows:
                if row.get("id") != issue_id:
                    out.append(row)
                    continue
                lots = list(row.get("lots") or [])
                if not (0 <= row_index < len(lots)):
                    out.append(row)
                    continue
                meta = next_revision(row, username)
                next_row = dict(row)
                next_lots = copy.deepcopy(lots)
                next_lots[row_index]["watch"] = watch_patch
                next_row["lots"] = next_lots
                next_row["updated_at"] = meta.updated_at
                next_row["updated_by"] = meta.updated_by
                next_row["revision"] = meta.revision
                saved["row"] = next_row
                out.append(next_row)
            return out

        self.store.load_and_update(updater)
        return saved["row"]

    def replace_legacy_issue(self, issue_id: str, replacement: dict, username: str) -> dict | None:
        saved = {"row": None}

        def updater(current):
            rows = current if isinstance(current, list) else []
            out = []
            for row in rows:
                if row.get("id") != issue_id:
                    out.append(row)
                    continue
                meta = next_revision(row, username)
                next_row = dict(replacement)
                next_row["updated_at"] = meta.updated_at
                next_row["updated_by"] = meta.updated_by
                next_row["revision"] = meta.revision
                saved["row"] = next_row
                out.append(next_row)
            return out

        self.store.load_and_update(updater)
        return saved["row"]

    def delete_legacy_issue(self, issue_id: str) -> dict | None:
        saved = {"row": None}

        def updater(current):
            rows = current if isinstance(current, list) else []
            out = []
            for row in rows:
                if row.get("id") == issue_id:
                    saved["row"] = row
                    continue
                out.append(row)
            return out

        self.store.load_and_update(updater)
        return saved["row"]
