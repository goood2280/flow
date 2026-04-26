from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from uuid import uuid4


@dataclass(slots=True)
class TrackerIssue:
    issue_id: str
    title: str
    description: str
    status: str
    priority: str
    category: str
    created_at: str
    updated_at: str
    created_by: str
    updated_by: str
    revision: int

    @classmethod
    def create(
        cls,
        *,
        title: str,
        description: str,
        username: str,
        category: str = "",
        priority: str = "normal",
        status: str = "in_progress",
    ) -> "TrackerIssue":
        now = datetime.now().isoformat(timespec="seconds")
        return cls(
            issue_id=f"trk_{uuid4().hex[:12]}",
            title=(title or "").strip(),
            description=(description or "").strip(),
            status=(status or "in_progress").strip(),
            priority=(priority or "normal").strip(),
            category=(category or "").strip(),
            created_at=now,
            updated_at=now,
            created_by=(username or "").strip(),
            updated_by=(username or "").strip(),
            revision=1,
        )

    def to_dict(self) -> dict:
        return asdict(self)


def make_legacy_issue(
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
) -> dict:
    now = datetime.now().isoformat(timespec="seconds")
    return {
        "id": issue_id,
        "title": (title or "").strip(),
        "description": description,
        "username": (username or "").strip(),
        "status": (status or "in_progress").strip(),
        "priority": (priority or "normal").strip(),
        "category": (category or "").strip(),
        "links": list(links or []),
        "created": now,
        "updated_at": now,
        "closed_at": None,
        "images": list(images or []),
        "lots": list(lots or []),
        "comments": [],
        "group_ids": list(group_ids or []),
        "updated_by": (username or "").strip(),
        "revision": 1,
    }
