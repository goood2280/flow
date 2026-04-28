from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core import utils  # noqa: E402


class _FakePaths:
    def __init__(self, root: Path):
        self.db_root = root
        self.base_root = root


def test_find_all_sources_uses_nested_product_partition_names(monkeypatch, tmp_path):
    db = tmp_path / "DB"
    part = db / "1.RAWDATA_DB_FAB" / "fab_history" / "product=PRODA" / "date=20260427" / "part.parquet"
    part.parent.mkdir(parents=True)
    part.write_bytes(b"placeholder")
    monkeypatch.setattr(utils, "PATHS", _FakePaths(db))

    sources = utils.find_all_sources(apply_whitelist=False)

    assert {
        "source_type": "hive",
        "root": "1.RAWDATA_DB_FAB",
        "product": "PRODA",
        "file": "",
        "canonical": "1.RAWDATA_DB_FAB",
        "level": "",
        "label": "1.RAWDATA_DB_FAB/PRODA",
    } in sources
    assert all(s.get("product") != "fab_history" for s in sources)
