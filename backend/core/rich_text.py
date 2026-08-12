"""Small dependency-free rich-text sanitizer for user-authored board content."""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser


ALLOWED_TAGS = {
    "a", "b", "blockquote", "br", "caption", "code", "col", "colgroup",
    "div", "em", "figure", "figcaption", "h1", "h2", "h3", "h4",
    "hr", "i", "img", "li", "mark", "ol", "p", "pre", "s", "small",
    "span", "strike", "strong", "sub", "sup", "table", "tbody", "td",
    "tfoot", "th", "thead", "tr", "u", "ul",
}
DANGEROUS_TAGS = {
    "script", "style", "iframe", "object", "embed", "svg", "math", "form",
    "input", "button", "select", "textarea", "video", "audio", "canvas",
}
VOID_TAGS = {"br", "hr", "img", "col"}
ALLOWED_ATTRS = {
    "align", "alt", "border", "cellpadding", "cellspacing", "colspan", "height",
    "href", "rel", "rowspan", "span", "src", "style", "target", "title",
    "valign", "width",
}
SAFE_STYLE_PROPS = {
    "background", "background-color", "border", "border-bottom", "border-collapse",
    "border-left", "border-right", "border-spacing", "border-top", "color", "display",
    "font-size", "font-style", "font-weight", "height", "line-height", "margin",
    "margin-bottom", "margin-left", "margin-right", "margin-top", "max-height",
    "max-width", "min-width", "overflow", "padding", "padding-bottom", "padding-left",
    "padding-right", "padding-top", "table-layout", "text-align", "text-decoration",
    "vertical-align", "white-space", "width", "word-break",
}
_BAD_CSS = re.compile(r"expression|javascript:|vbscript:|@import|behavior|-moz-binding|url\s*\(", re.I)
_SAFE_LINK = re.compile(r"^(?:https?://|mailto:|tel:|#|/)", re.I)
_SAFE_IMAGE = re.compile(r"^/api/(?:lot-requests|informs)/files/[A-Za-z0-9]+/[^/?#]+(?:\?.*)?$", re.I)


def _style(value: str) -> str:
    out: list[str] = []
    for raw in str(value or "").split(";"):
        prop, sep, val = raw.partition(":")
        prop = prop.strip().lower()
        val = val.strip()
        if not sep or prop not in SAFE_STYLE_PROPS or not val or _BAD_CSS.search(val):
            continue
        out.append(f"{prop}: {val[:240]}")
    return "; ".join(out)


class _RichSanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs):
        tag = tag.lower()
        if tag in DANGEROUS_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth or tag not in ALLOWED_TAGS:
            return
        clean = []
        for raw_name, raw_value in attrs:
            name = str(raw_name or "").lower()
            value = str(raw_value or "")
            if name.startswith("on") or name not in ALLOWED_ATTRS:
                continue
            if name == "style":
                value = _style(value)
                if not value:
                    continue
            elif name == "src":
                if tag != "img" or not _SAFE_IMAGE.match(value):
                    continue
                value = value.split("&t=", 1)[0].split("?t=", 1)[0]
            elif name == "href":
                if not _SAFE_LINK.match(value):
                    continue
            elif name == "target":
                value = "_blank" if value == "_blank" else ""
                if not value:
                    continue
            clean.append(f' {name}="{html.escape(value, quote=True)}"')
        if tag == "a" and any(name == "target" and value == "_blank" for name, value in attrs):
            clean.append(' rel="noopener noreferrer"')
        self.out.append(f"<{tag}{''.join(clean)}>")

    def handle_startendtag(self, tag: str, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag in DANGEROUS_TAGS:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if not self.skip_depth and tag in ALLOWED_TAGS and tag not in VOID_TAGS:
            self.out.append(f"</{tag}>")

    def handle_data(self, data: str):
        if not self.skip_depth:
            self.out.append(html.escape(data, quote=False))


def sanitize_rich_html(value: object, *, max_len: int = 200_000) -> str:
    raw = str(value or "").strip()[:max_len]
    if not raw:
        return ""
    parser = _RichSanitizer()
    try:
        parser.feed(raw)
        parser.close()
        return "".join(parser.out).strip()
    except Exception:
        return html.escape(raw)


def rich_text_has_content(value: object) -> bool:
    raw = str(value or "")
    if re.search(r"<(?:img|table)\b", raw, re.I):
        return True
    text = re.sub(r"<[^>]+>", " ", raw)
    return bool(html.unescape(text).strip())
