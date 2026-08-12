from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer


ROOT = Path(__file__).resolve().parent.parent
DOC_PATH = ROOT / "docs" / "ARCHITECTURE_USER_GUIDE.md"
OUT_PATH = ROOT / "reports" / "flow_architecture_guide.pdf"


def pick_font() -> str:
    candidates = [
        Path("/mnt/c/Windows/Fonts/NotoSansKR-VF.ttf"),
        Path("/mnt/c/Windows/Fonts/malgun.ttf"),
    ]
    for fp in candidates:
        if fp.exists():
            pdfmetrics.registerFont(TTFont("FlowKorean", str(fp)))
            return "FlowKorean"
    return "Helvetica"


def parse_markdown(lines: list[str], styles: dict):
    story = []
    bullet_buffer: list[str] = []

    def flush_bullets():
        nonlocal bullet_buffer
        if not bullet_buffer:
            return
        items = [
            ListItem(Paragraph(item, styles["body"]), leftIndent=8)
            for item in bullet_buffer
        ]
        story.append(
            ListFlowable(
                items,
                bulletType="bullet",
                start="circle",
                bulletFontName=styles["body"].fontName,
                bulletFontSize=9,
                leftIndent=16,
            )
        )
        story.append(Spacer(1, 4))
        bullet_buffer = []

    for raw in lines:
        line = raw.rstrip()
        if not line:
            flush_bullets()
            story.append(Spacer(1, 6))
            continue
        if line == "---":
            flush_bullets()
            story.append(Spacer(1, 4))
            continue
        if line.startswith("# "):
            flush_bullets()
            story.append(Paragraph(line[2:].strip(), styles["title"]))
            story.append(Spacer(1, 10))
            continue
        if line.startswith("## "):
            flush_bullets()
            story.append(Paragraph(line[3:].strip(), styles["h1"]))
            story.append(Spacer(1, 6))
            continue
        if line.startswith("### "):
            flush_bullets()
            story.append(Paragraph(line[4:].strip(), styles["h2"]))
            story.append(Spacer(1, 4))
            continue
        if line.startswith("- "):
            bullet_buffer.append(line[2:].strip())
            continue
        flush_bullets()
        story.append(Paragraph(line, styles["body"]))
        story.append(Spacer(1, 3))

    flush_bullets()
    return story


def build_pdf():
    font_name = pick_font()
    styles = getSampleStyleSheet()
    custom = {
        "title": ParagraphStyle(
            "title",
            parent=styles["Title"],
            fontName=font_name,
            fontSize=20,
            leading=25,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#111827"),
            spaceAfter=8,
        ),
        "h1": ParagraphStyle(
            "h1",
            parent=styles["Heading1"],
            fontName=font_name,
            fontSize=14,
            leading=18,
            textColor=colors.HexColor("#c2410c"),
            spaceBefore=6,
            spaceAfter=4,
        ),
        "h2": ParagraphStyle(
            "h2",
            parent=styles["Heading2"],
            fontName=font_name,
            fontSize=11,
            leading=14,
            textColor=colors.HexColor("#1f2937"),
            spaceBefore=4,
            spaceAfter=2,
        ),
        "body": ParagraphStyle(
            "body",
            parent=styles["BodyText"],
            fontName=font_name,
            fontSize=9.5,
            leading=14,
            textColor=colors.HexColor("#111827"),
        ),
    }
    lines = DOC_PATH.read_text(encoding="utf-8").splitlines()
    story = parse_markdown(lines, custom)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUT_PATH),
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title="flow Architecture Guide",
    )
    doc.build(story)
    print(str(OUT_PATH))


if __name__ == "__main__":
    build_pdf()
