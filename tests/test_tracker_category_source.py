from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from routers import tracker  # noqa: E402


def test_category_source_uses_saved_category_mapping_case_insensitively(monkeypatch):
    monkeypatch.setattr(
        tracker,
        "_load_cats",
        lambda: [{"name": "Inline Analysis", "color": "#3b82f6", "source": "et"}],
    )

    assert tracker._category_source(" inline analysis ", "fab") == "et"
