from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _get_lock(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[key] = lock
        return lock


@dataclass(slots=True)
class RevisionMeta:
    updated_at: str
    updated_by: str
    revision: int


class JsonFileStore:
    """Shared JSON store with per-file process lock and atomic replace."""

    def __init__(self, path: Path, default):
        self.path = path
        self.default = default
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self):
        if not self.path.exists():
            return self.default() if callable(self.default) else self.default
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return self.default() if callable(self.default) else self.default

    def save(self, data) -> None:
        lock = _get_lock(self.path)
        with lock:
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self.path)

    def load_and_update(self, updater):
        lock = _get_lock(self.path)
        with lock:
            current = self.load()
            updated = updater(current)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(
                json.dumps(updated, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self.path)
            return updated


def next_revision(existing: dict | None, updated_by: str) -> RevisionMeta:
    meta = existing or {}
    return RevisionMeta(
        updated_at=datetime.now().isoformat(timespec="seconds"),
        updated_by=(updated_by or "").strip(),
        revision=int(meta.get("revision") or 0) + 1,
    )
