"""Tables pasted into the home chat.

Excel and Google Sheets copy a range as tab-separated text (TSV), one row per
line. The chat must keep such a paste as one request instead of splitting each
row into a separate question, and handlers read it back as rows of cells.
"""
from __future__ import annotations

import csv
import io
import re

MAX_ROWS = 500
MAX_COLUMNS = 20
# A table request may be longer than a typed question (4,000 characters).
MAX_TABLE_PROMPT_CHARS = 20000


def _clean(cell) -> str:
    return re.sub(r"\s+", " ", str(cell or "")).strip()


def parse(text) -> dict | None:
    """Return ``{"intro", "rows", "outro"}`` when ``text`` holds a pasted table.

    The table is the block from the first to the last line that contains a tab.
    Lines before it are the instruction (``intro``), lines after it ``outro``.
    Quoted cells follow Excel's TSV rules. Empty rows and trailing empty
    columns are dropped. ``None`` when there is no tab-separated line.
    """
    source = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = source.split("\n")
    tabbed = [index for index, line in enumerate(lines) if "\t" in line]
    if not tabbed:
        return None
    first, last = tabbed[0], tabbed[-1]
    block = "\n".join(lines[first:last + 1])
    try:
        raw_rows = list(csv.reader(io.StringIO(block), delimiter="\t"))
    except csv.Error:
        raw_rows = [line.split("\t") for line in lines[first:last + 1]]
    rows = []
    for raw in raw_rows:
        cells = [_clean(cell) for cell in raw[:MAX_COLUMNS]]
        if any(cells):
            rows.append(cells)
    if not rows:
        return None
    width = max(len(row) for row in rows)
    while width > 1 and not any(len(row) >= width and row[width - 1] for row in rows):
        width -= 1
    rows = [(row + [""] * width)[:width] for row in rows[:MAX_ROWS]]
    return {
        "intro": "\n".join(lines[:first]).strip(),
        "rows": rows,
        "outro": "\n".join(lines[last + 1:]).strip(),
        "truncated": len(raw_rows) > MAX_ROWS,
    }


def is_table_paste(text) -> bool:
    """True when the text carries a pasted table of two or more rows."""
    table = parse(text)
    return bool(table and len(table["rows"]) >= 2 and len(table["rows"][0]) >= 2)
