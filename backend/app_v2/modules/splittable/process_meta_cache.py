"""Bounded RAM + atomic shared-disk snapshots for applied-process metadata."""

import json
import logging
import os
import threading
from collections import OrderedDict
from pathlib import Path

logger = logging.getLogger("flow.splittable")


class ProcessMetaCache:
    def __init__(self, max_bytes=16 * 1024 * 1024, max_entries=32):
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self._entries = OrderedDict()
        self._lock = threading.Lock()
        # Bounded single-flight locks; unrelated products can build concurrently.
        self._build_locks = [threading.Lock() for _ in range(16)]

    def clear(self):
        with self._lock:
            self._entries.clear()

    def _remember(self, key, signature, data, size):
        # JSON size understates dict/list/string overhead; budget conservatively.
        cost = size * 4
        with self._lock:
            self._entries.pop(key, None)
            if cost > self.max_bytes:
                return
            self._entries[key] = (signature, data, cost)
            while (len(self._entries) > self.max_entries or
                   sum(entry[2] for entry in self._entries.values()) > self.max_bytes):
                self._entries.popitem(last=False)

    def get(self, path: Path, signature: str, build, current_signature):
        """Return immutable-by-contract metadata; never persist a failed/mixed build."""
        key = str(path)
        with self._build_locks[hash(key) % len(self._build_locks)]:
            with self._lock:
                hit = self._entries.get(key)
                if hit and hit[0] == signature:
                    self._entries.move_to_end(key)
                    return hit[1]
            try:
                raw = path.read_bytes()
                saved = json.loads(raw)
                if saved.get("signature") == signature and isinstance(saved.get("items"), dict):
                    self._remember(key, signature, saved["items"], len(raw))
                    return saved["items"]
            except (OSError, ValueError, TypeError, AttributeError):
                pass

            data = build()  # Exceptions intentionally leave the previous snapshot intact.
            if current_signature() != signature:
                raise RuntimeError("Applied-process inputs changed during refresh; retry the request")
            raw = json.dumps({"signature": signature, "items": data}, ensure_ascii=False).encode("utf-8")
            tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_bytes(raw)
                os.replace(tmp, path)
            except OSError:
                # Read-only/shared-drive outages must not make the screen unusable.
                logger.warning("Applied-process snapshot write failed: %s", path, exc_info=True)
            finally:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
            self._remember(key, signature, data, len(raw))
            return data
