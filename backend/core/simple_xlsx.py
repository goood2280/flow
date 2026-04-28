from __future__ import annotations

import datetime as dt
import io
import re
import zipfile
from typing import Any
from xml.sax.saxutils import escape


_INVALID_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")


def _col_name(index: int) -> str:
    name = ""
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name or "A"


def _cell_ref(row: int, col: int) -> str:
    return f"{_col_name(col)}{row}"


def _sheet_name(value: Any, fallback: str) -> str:
    text = _INVALID_SHEET_CHARS.sub("_", str(value or fallback)).strip("'").strip()
    return (text or fallback)[:31]


def _inline_cell(row: int, col: int, value: Any) -> str:
    if value is None or value == "":
        return ""
    text = escape(str(value))
    return f'<c r="{_cell_ref(row, col)}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _sheet_xml(rows: list[list[Any]], merges: list[tuple[int, int, int, int]] | None = None) -> str:
    row_xml = []
    for r_idx, row in enumerate(rows, start=1):
        cells = "".join(_inline_cell(r_idx, c_idx, value) for c_idx, value in enumerate(row, start=1))
        row_xml.append(f'<row r="{r_idx}">{cells}</row>')

    merge_refs = []
    for r1, c1, r2, c2 in merges or []:
        if r1 <= 0 or c1 <= 0 or r2 < r1 or c2 < c1:
            continue
        if r1 == r2 and c1 == c2:
            continue
        merge_refs.append(f'<mergeCell ref="{_cell_ref(r1, c1)}:{_cell_ref(r2, c2)}"/>')
    merge_xml = ""
    if merge_refs:
        merge_xml = f'<mergeCells count="{len(merge_refs)}">{"".join(merge_refs)}</mergeCells>'

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheetData>{"".join(row_xml)}</sheetData>{merge_xml}</worksheet>'
    )


def build_workbook(sheets: list[dict[str, Any]]) -> bytes:
    """Build a small XLSX workbook without openpyxl/xlsxwriter.

    This intentionally supports only the subset needed for Flow exports:
    multiple sheets, inline strings, and merged ranges. It is a dependency-free
    fallback for environments where pip/openpyxl is broken.
    """

    normalized = []
    for idx, sheet in enumerate(sheets, start=1):
        rows = sheet.get("rows") if isinstance(sheet, dict) else []
        if not isinstance(rows, list):
            rows = []
        normalized.append({
            "name": _sheet_name(sheet.get("title") if isinstance(sheet, dict) else "", f"Sheet{idx}"),
            "rows": rows,
            "merges": sheet.get("merges", []) if isinstance(sheet, dict) else [],
        })
    if not normalized:
        normalized = [{"name": "Sheet1", "rows": [], "merges": []}]

    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    content_types = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>',
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>',
    ]
    for idx in range(1, len(normalized) + 1):
        content_types.append(
            f'<Override PartName="/xl/worksheets/sheet{idx}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )
    content_types.append("</Types>")

    workbook_sheets = "".join(
        f'<sheet name="{escape(sheet["name"])}" sheetId="{idx}" r:id="rId{idx}"/>'
        for idx, sheet in enumerate(normalized, start=1)
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{workbook_sheets}</sheets></workbook>"
    )
    workbook_rels = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
    ]
    for idx in range(1, len(normalized) + 1):
        workbook_rels.append(
            f'<Relationship Id="rId{idx}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{idx}.xml"/>'
        )
    workbook_rels.append("</Relationships>")

    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
        "</Relationships>"
    )
    core_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        "<dc:creator>flow</dc:creator><cp:lastModifiedBy>flow</cp:lastModifiedBy>"
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>'
        "</cp:coreProperties>"
    )
    app_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        "<Application>flow</Application></Properties>"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "".join(content_types))
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", "".join(workbook_rels))
        zf.writestr("docProps/core.xml", core_xml)
        zf.writestr("docProps/app.xml", app_xml)
        for idx, sheet in enumerate(normalized, start=1):
            zf.writestr(f"xl/worksheets/sheet{idx}.xml", _sheet_xml(sheet["rows"], sheet["merges"]))
    return buf.getvalue()
