"""Domain defaults; explicit source names take priority over metric suffixes."""
import re

VM = re.compile(r"(?<![A-Za-z0-9])(?:VM|IM)(?![A-Za-z0-9])|가상\s*계측", re.I)
INLINE = re.compile(r"\bINLINE\b|인라인|\bL1\b|L1값|(?<![A-Za-z0-9])(?:TCD|BCD|MCD|OCD|CD|THK|DEPTH|HT)(?![A-Za-z0-9])", re.I)


def infer(text):
    return "VM" if VM.search(text) else "INLINE" if INLINE.search(text) else ""


def strip_source_words(text):
    return re.sub(r"L1값|\b(?:INLINE|VM|IM|L1)\b|인라인|가상\s*계측", " ", text, flags=re.I)
