from __future__ import annotations

import sys
import zipfile
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core.simple_xlsx import build_workbook  # noqa: E402


def test_simple_xlsx_builds_openable_package_with_merges():
    data = build_workbook([{
        "title": "SplitTable Snapshot",
        "rows": [
            ["root_lot_id", "A1000", ""],
            ["fab_lot_id", "A1000A.1", ""],
            ["Parameter", "#1", "#2"],
        ],
        "merges": [(1, 2, 1, 3), (2, 2, 2, 3)],
    }])

    with zipfile.ZipFile(BytesIO(data)) as zf:
        names = set(zf.namelist())
        assert "[Content_Types].xml" in names
        assert "xl/workbook.xml" in names
        sheet = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
        assert 'mergeCell ref="B1:C1"' in sheet
        assert 'mergeCell ref="B2:C2"' in sheet
        assert "A1000A.1" in sheet
