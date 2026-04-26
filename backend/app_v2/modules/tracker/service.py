from __future__ import annotations

import datetime

from app_v2.modules.tracker.domain import TrackerIssue, make_legacy_issue
from app_v2.modules.tracker.repository import TrackerIssueRepository
from app_v2.shared.result import fail, ok
from core.tracker_schema import normalize_lot_row


class TrackerService:
    def __init__(self, repo: TrackerIssueRepository):
        self.repo = repo

    def create_issue(
        self,
        *,
        title: str,
        description: str,
        username: str,
        category: str = "",
        priority: str = "normal",
        status: str = "in_progress",
    ):
        if not (title or "").strip():
            return fail("title required")
        issue = TrackerIssue.create(
            title=title,
            description=description,
            username=username,
            category=category,
            priority=priority,
            status=status,
        )
        saved = self.repo.create_issue(issue)
        return ok({"issue": saved})

    def rename_issue(self, issue_id: str, title: str, username: str):
        if not (title or "").strip():
            return fail("title required")
        row = self.repo.update_issue(
            issue_id,
            {"title": title.strip()},
            username=username,
        )
        if not row:
            return fail("issue not found")
        return ok({"issue": row})

    def create_legacy_issue(
        self,
        *,
        issue_id: str,
        title: str,
        description: str,
        username: str,
        status: str,
        priority: str,
        category: str,
        links: list,
        images: list,
        lots: list,
        group_ids: list,
    ):
        if not (title or "").strip():
            return fail("title required")
        issue = make_legacy_issue(
            issue_id=issue_id,
            title=title,
            description=description,
            username=username,
            status=status,
            priority=priority,
            category=category,
            links=links,
            images=images,
            lots=lots,
            group_ids=group_ids,
        )
        saved = self.repo.create_issue_dict(issue)
        return ok({"issue": saved})

    def update_legacy_issue(
        self,
        *,
        issue_id: str,
        username: str,
        title=None,
        description=None,
        status=None,
        priority=None,
        category=None,
        group_ids=None,
        append_images=None,
    ):
        patch = {}
        if title is not None:
            patch["title"] = title
        if description is not None:
            patch["description"] = description
        if priority is not None:
            patch["priority"] = priority
        if category is not None:
            patch["category"] = category
        if group_ids is not None:
            patch["group_ids"] = list(group_ids)
        if status is not None:
            patch["status"] = status
            if status == "closed":
                patch["closed_at"] = datetime.datetime.now().isoformat(timespec="seconds")
            else:
                patch["closed_at"] = None
        if append_images:
            patch["images_append"] = list(append_images)
        row = self.repo.update_legacy_issue(issue_id, patch, username=username)
        if not row:
            return fail("issue not found")
        return ok({"issue": row})

    def add_legacy_comment(
        self,
        *,
        issue_id: str,
        username: str,
        text: str,
        lot_id: str = "",
        wafer_id: str = "",
    ):
        now = datetime.datetime.now().isoformat(timespec="seconds")
        comment = {
            "username": username,
            "text": text,
            "lot_id": lot_id,
            "wafer_id": wafer_id,
            "timestamp": now,
        }
        row = self.repo.append_legacy_comment(issue_id, comment, username=username)
        if not row:
            return fail("issue not found")
        return ok({"issue": row, "comment": comment})

    def add_legacy_lots(
        self,
        *,
        issue_id: str,
        username: str,
        rows: list[dict],
    ):
        now = datetime.datetime.now().isoformat(timespec="seconds")
        rows_to_add = [normalize_lot_row({**row, "username": username, "added": now}) for row in rows]
        issue = self.repo.append_legacy_lots(issue_id, rows_to_add, username=username)
        if not issue:
            return fail("issue not found")
        return ok({"issue": issue, "added": len(rows_to_add)})

    def save_legacy_lot_watch(
        self,
        *,
        issue_id: str,
        row_index: int,
        username: str,
        target_step_id: str = "",
        target_et_step_id: str = "",
        target_et_seqs: str = "",
        source: str = "fab",
        mail: bool = False,
        mail_group_ids: list | None = None,
    ):
        issues = self.repo.list_issues()
        issue = next((row for row in issues if row.get("id") == issue_id), None)
        if not issue:
            return fail("issue not found")
        lots = issue.get("lots") or []
        if not (0 <= row_index < len(lots)):
            return fail("row_index out of range")
        src = (source or "fab").lower().strip()
        if src not in ("fab", "et"):
            src = "fab"
        previous = (lots[row_index].get("watch") or {})
        prev_target = str(previous.get("target_step_id") or "")
        next_target = str(target_step_id or "")
        prev_et_step = str(previous.get("target_et_step_id") or "")
        next_et_step = str(target_et_step_id or "")
        prev_et_seqs = str(previous.get("target_et_seqs") or "")
        next_et_seqs = str(target_et_seqs or "")
        fired_targets = previous.get("fired_target_step_ids") or []
        if prev_target != next_target:
            fired_targets = []
        et_filter_changed = prev_et_step != next_et_step or prev_et_seqs != next_et_seqs
        watch = {
            "target_step_id": next_target,
            "target_et_step_id": next_et_step,
            "target_et_seqs": next_et_seqs,
            "source": src,
            "last_observed_step": previous.get("last_observed_step", ""),
            "last_observed_et_count": int(previous.get("last_observed_et_count") or 0),
            "last_observed_et_step_keys": [] if et_filter_changed else list(previous.get("last_observed_et_step_keys") or []),
            "et_step_states": {} if et_filter_changed else dict(previous.get("et_step_states") or {}),
            "notified_new_et_step_keys": [] if et_filter_changed else list(previous.get("notified_new_et_step_keys") or []),
            "et_watch_initialized": False if et_filter_changed else bool(previous.get("et_watch_initialized")),
            "fired_target_step_ids": list(fired_targets),
            "last_fired_at": previous.get("last_fired_at", ""),
            "last_fired_step_id": previous.get("last_fired_step_id", ""),
            "last_fired_et_signature": "" if et_filter_changed else previous.get("last_fired_et_signature", ""),
            "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "updated_by": username or "",
        }
        updated = self.repo.update_legacy_lot_watch(
            issue_id,
            row_index=row_index,
            watch_patch=watch,
            username=username,
        )
        if not updated:
            return fail("issue not found")
        return ok({"issue": updated, "watch": watch})

    def apply_lot_check_result(
        self,
        *,
        issue_id: str,
        issue_data: dict,
        username: str = "system",
    ):
        updated = self.repo.replace_legacy_issue(issue_id, issue_data, username=username)
        if not updated:
            return fail("issue not found")
        return ok({"issue": updated})

    def delete_legacy_issue(self, issue_id: str):
        row = self.repo.delete_legacy_issue(issue_id)
        if not row:
            return fail("issue not found")
        return ok({"issue": row})
