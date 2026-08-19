"""Template Report — reusable ChartBuilder report layouts and PPTX export.

한 장짜리 차트 모음이 아니라 "매번 같은 형식으로 다시 뽑는 보고서"가 목표다.
그래서 두 가지를 템플릿이 갖는다.

1. **변수** — 저장된 ChartBuilder 코드 안의 ``{{LOT}}`` 같은 토큰(core/report_variables).
   랏이 바뀌어도 차트를 새로 만들지 않는다.
2. **반복** — 변수 하나(기본 ``LOT``)에 값을 여러 개 주면 페이지 묶음이 랏마다 반복된다.

기본 실행은 ``RECENT_DAYS = 7``처럼 저장 차트가 가진 조건을 그대로 쓴다. 다만 보고서
실행 컨텍스트를 켜면 root lot/wafer 목록과 시간 열·최근 일수를 모든 차트에 한 번만
덧씌울 수 있다. 컨텍스트는 Template 자체를 변경하지 않는 실행 전용 값이다.

A/B 비교는 템플릿이 슬롯을 복제하는 방식이 아니라 **ChartBuilder 코드 쪽에서** 만든다
(조건 열을 COLOR/X 로 잡거나 조건별 차트를 따로 저장). ``stats`` 블록은 그렇게 만들어진
차트 하나를 가리켜 그룹별 통계를 표로 깔고, 그룹이 정확히 둘이면 Δ 와 Δ% 를 덧붙인다.

슬라이드 모양은 HOL Auto Report(`auto report/My_Function.py`)와 같은 규약을 쓴다 —
상단 네이비 바 + 흰 제목, 표지 슬라이드, 하단 푸터. 색은 RGB(31,73,125) 한 값이다.
"""
from __future__ import annotations

import base64
import datetime as dt
import io
import math
import re
import threading
import uuid
import zipfile
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from core.auth import current_user
from core.chart_builder_definition import ChartBuilderDefinitionError, parse_chart_builder_definition
from core.paths import PATHS
from core.report_variables import (
    ReportVariableError,
    extract_variables,
    normalize_name,
    split_list,
    substitute,
    validate_bindings,
)
from core.utils import load_json, save_json, safe_filename


router = APIRouter(prefix="/api/template-report", tags=["template-report"])
STORE_FILE = PATHS.data_root / "template_reports.json"
_STORE_LOCK = threading.Lock()
MAX_PAGES = 30
MAX_CHARTS_PER_PAGE = 20
MAX_REPEAT_VALUES = 20
MAX_RENDERED_PAGES = 200
MAX_IMAGES = MAX_RENDERED_PAGES * MAX_CHARTS_PER_PAGE
LEGACY_SLOT_LAYOUTS = {
    1: {"x": 3.4, "y": 13.6, "width": 45.2, "height": 37.1},
    2: {"x": 51.5, "y": 13.6, "width": 45.2, "height": 37.1},
    3: {"x": 3.4, "y": 54.4, "width": 45.2, "height": 37.1},
    4: {"x": 51.5, "y": 54.4, "width": 45.2, "height": 37.1},
}
DEFAULT_SLOT_LAYOUT = {"x": 5.0, "y": 16.0}
SLIDE_DESIGN_WIDTH = 1920
SLIDE_DESIGN_HEIGHT = 1080
DEFAULT_CHART_WIDTH = 1200
DEFAULT_CHART_HEIGHT = 650
# 차트가 아닌 블록(표·글)은 ChartBuilder 크기가 없어 슬라이드 비율로 직접 잡는다.
DEFAULT_BLOCK_WIDTH_PCT = 46.0
DEFAULT_BLOCK_HEIGHT_PCT = 26.0
SLOT_KINDS = ("chart", "split", "text", "stats")
# 예전 템플릿의 A/B 전용 통계표 — 지금은 그룹 통계표 하나로 합쳤다.
LEGACY_SLOT_KINDS = {"ab_stats": "stats"}

# ─ 슬라이드 디자인 상수 — HOL Auto Report 와 같은 값 ──────────────────────────
SLIDE_WIDTH_IN = 13.333333
SLIDE_HEIGHT_IN = 7.5
TITLE_BAR_HEIGHT_IN = 0.62
COVER_BAR_HEIGHT_IN = 0.45
REPORT_NAVY = (31, 73, 125)
REPORT_FONT = "Malgun Gothic"
MAX_TABLE_ROWS = 26
MAX_TABLE_COLUMNS = 24


class TemplateSlotReq(BaseModel):
    position: int
    chart_id: str = ""
    kind: str = "chart"
    x: float | None = None
    y: float | None = None
    width: float | None = None
    height: float | None = None
    title: str = ""
    # kind=text
    text: str = ""
    # kind=split
    product: str = ""
    lot: str = ""
    columns: str = ""
    display_mode: str = "matrix"
    # kind=stats
    source_position: int = 0
    stats: str = ""


class TemplatePageReq(BaseModel):
    id: str = ""
    title: str = ""
    subtitle: str = ""
    slots: list[TemplateSlotReq] = []


class TemplateVariableReq(BaseModel):
    name: str
    label: str = ""
    default: str = ""


class TemplateOptionsReq(BaseModel):
    cover: bool = True
    footer: bool = True
    subtitle: str = ""
    repeat_variable: str = "LOT"


class TemplateSaveReq(BaseModel):
    id: str = ""
    name: str
    pages: list[TemplatePageReq]
    variables: list[TemplateVariableReq] = []
    options: TemplateOptionsReq | None = None


class TemplateRunContextReq(BaseModel):
    root_lot_ids: list[str] = []
    wafer_ids: list[str] = []
    override_recent_days: bool = False
    recent_days: int = 0
    date_column: str = "tkout_time"
    color_rules: list[str] = []
    color_else: str = "gray"


class TemplateRunReq(BaseModel):
    template_id: str
    bindings: dict[str, str] = {}
    repeat_values: list[str] = []
    context: TemplateRunContextReq | None = None


class SplitBlockReq(BaseModel):
    product: str
    lot_id: str
    columns: str = ""
    display_mode: str = "matrix"
    max_rows: int = MAX_TABLE_ROWS


class ExportImageReq(BaseModel):
    page_index: int = 0
    position: int = 0
    key: str = ""
    chart_id: str = ""
    data_url: str


class ExportTableReq(BaseModel):
    key: str
    title: str = ""
    columns: list[str] = []
    rows: list[list[str]] = []
    note: str = ""


class ExportReq(BaseModel):
    template_id: str
    bindings: dict[str, str] = {}
    repeat_values: list[str] = []
    context: TemplateRunContextReq | None = None
    images: list[ExportImageReq] = []
    tables: list[ExportTableReq] = []


# ── 저장소 ────────────────────────────────────────────────────────────────────
def _load_templates() -> list[dict]:
    raw = load_json(STORE_FILE, {}) or {}
    rows = raw.get("templates") if isinstance(raw, dict) else []
    return _normalize_template_names([row for row in (rows or []) if isinstance(row, dict)])


def _unique_template_name(base: str, used: set[str]) -> str:
    clean = _clean_text(base, 120) or "Template Report"
    candidate = clean
    number = 2
    while candidate.casefold() in used:
        suffix = f" ({number})"
        candidate = f"{clean[:max(1, 120 - len(suffix))]}{suffix}"
        number += 1
    return candidate


def _normalize_template_names(rows: list[dict]) -> list[dict]:
    used = {
        str(row.get("id") or "").strip().casefold()
        for row in rows
        if row.get("id")
    }
    normalized = []
    for raw in rows:
        row = dict(raw)
        name = _unique_template_name(str(row.get("name") or "Template Report"), used)
        row["name"] = name
        used.add(name.casefold())
        normalized.append(row)
    return normalized


def _save_templates(rows: list[dict]) -> None:
    save_json(STORE_FILE, {"version": 2, "templates": rows})


def _chart_history() -> dict[str, dict]:
    from routers import filebrowser

    entries = filebrowser._chart_builder_history_entries()
    return {str(entry.get("history_id") or ""): entry for entry in entries if entry.get("history_id")}


def _chart_history_by_name(history: dict[str, dict]) -> dict[str, dict]:
    return {
        str(entry.get("name") or "").strip().casefold(): entry
        for entry in history.values()
        if str(entry.get("name") or "").strip()
    }


def _template_or_404(template_id: str) -> dict:
    template_ref = str(template_id or "").strip()
    rows = _load_templates()
    row = next((item for item in rows if str(item.get("id")) == template_ref), None)
    if not row:
        folded = template_ref.casefold()
        row = next((item for item in rows if str(item.get("name") or "").casefold() == folded), None)
    if not row:
        raise HTTPException(404, "Template Report를 찾지 못했습니다.")
    return row


def _template_options(row: dict) -> dict:
    raw = row.get("options") if isinstance(row.get("options"), dict) else {}
    repeat_variable = normalize_name(raw.get("repeat_variable", "LOT"))
    return {
        "cover": bool(raw.get("cover", True)),
        "footer": bool(raw.get("footer", True)),
        "subtitle": _clean_text(raw.get("subtitle", ""), 180),
        "repeat_variable": repeat_variable,
    }


def _template_variables(row: dict) -> list[dict]:
    declared = row.get("variables") if isinstance(row.get("variables"), list) else []
    out: list[dict] = []
    seen: set[str] = set()
    for item in declared:
        if not isinstance(item, dict):
            continue
        name = normalize_name(item.get("name"))
        if not name or name in seen:
            continue
        seen.add(name)
        out.append({
            "name": name,
            "label": _clean_text(item.get("label"), 80) or name,
            "default": _clean_text(item.get("default"), 200),
        })
    for name in _used_variable_names(row):
        if name not in seen:
            seen.add(name)
            out.append({"name": name, "label": name, "default": ""})
    return out


def _used_variable_names(row: dict) -> list[str]:
    """템플릿 본문(차트 코드·제목·표 대상)에 실제로 등장하는 변수."""
    texts: list[str] = [str(_template_options(row).get("subtitle") or "")]
    for page in row.get("pages") or []:
        if not isinstance(page, dict):
            continue
        texts.extend([str(page.get("title") or ""), str(page.get("subtitle") or "")])
        for slot in page.get("slots") or []:
            if not isinstance(slot, dict):
                continue
            texts.extend([
                str(slot.get("definition_code") or ""),
                str(slot.get("text") or ""),
                str(slot.get("product") or ""),
                str(slot.get("lot") or ""),
                str(slot.get("columns") or ""),
                str(slot.get("title") or ""),
            ])
    return extract_variables(*texts)


def _public_template(row: dict) -> dict:
    pages = []
    for raw_page in row.get("pages") if isinstance(row.get("pages"), list) else []:
        page = dict(raw_page)
        page["title"] = str(raw_page.get("title") or "")
        page["subtitle"] = str(raw_page.get("subtitle") or "")
        page["slots"] = [
            {**slot, "kind": _slot_kind(slot), **_slot_layout(slot), **_code_time_window(slot.get("definition_code"))}
            for slot in (raw_page.get("slots") or [])
            if isinstance(slot, dict)
        ]
        pages.append(page)
    return {
        "id": str(row.get("id") or ""),
        "name": str(row.get("name") or ""),
        "pages": pages,
        "variables": _template_variables(row),
        "options": _template_options(row),
        "created_by": str(row.get("created_by") or ""),
        "created_at": str(row.get("created_at") or ""),
        "updated_by": str(row.get("updated_by") or ""),
        "updated_at": str(row.get("updated_at") or ""),
        "slide_size": "LAYOUT_WIDE",
    }


RECENT_DAYS_HINT_RE = re.compile(r"(?<![A-Za-z0-9_])recent[_\s-]*days?\s*[:=]\s*(\d+)", re.IGNORECASE)
DATE_COLUMN_HINT_RE = re.compile(r"(?<![A-Za-z0-9_])(?:date|time)[_\s-]*col(?:umn)?\s*[:=]\s*([A-Za-z_]\w*)", re.IGNORECASE)


def _code_time_window(code) -> dict:
    """차트 코드가 스스로 지닌 시간 창 — 화면에 "최근 7일"을 그대로 보여주기 위한 요약.

    실행에 쓰이는 값이 아니라(그건 파서가 낸다) 라벨이라, 코드가 깨져 있어도
    목록이 통째로 죽지 않도록 가벼운 정규식으로만 읽는다.
    """
    text = str(code or "")
    days = [int(match.group(1)) for match in RECENT_DAYS_HINT_RE.finditer(text)]
    if not days:
        return {"recent_days": 0, "date_column": ""}
    columns = [match.group(1) for match in DATE_COLUMN_HINT_RE.finditer(text)]
    return {"recent_days": max(days), "date_column": columns[0] if columns else "tkout_time"}


def _clean_text(value, limit: int) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]+", " ", str(value or "")).strip()[:limit]


def _clean_multiline(value, limit: int) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]+", " ", str(value or "")).strip()
    return text[:limit]


def _slot_kind(slot) -> str:
    raw = slot.get("kind") if isinstance(slot, dict) else getattr(slot, "kind", "")
    kind = str(raw or "chart").strip().casefold()
    kind = LEGACY_SLOT_KINDS.get(kind, kind)
    return kind if kind in SLOT_KINDS else "chart"


def _chart_dimensions(slot: dict | TemplateSlotReq) -> tuple[int, int]:
    if isinstance(slot, dict):
        width = int(slot.get("chart_width") or 0)
        height = int(slot.get("chart_height") or 0)
        definition = str(slot.get("definition_code") or "")
        if (not width or not height) and definition:
            try:
                chart = parse_chart_builder_definition(definition).get("chart") or {}
                width = width or int(chart.get("width") or 0)
                height = height or int(chart.get("height") or 0)
            except (ChartBuilderDefinitionError, TypeError, ValueError):
                pass
    else:
        width = height = 0
    width = max(320, min(2400, width or DEFAULT_CHART_WIDTH))
    height = max(240, min(1600, height or DEFAULT_CHART_HEIGHT))
    scale = min(1.0, SLIDE_DESIGN_WIDTH / width, SLIDE_DESIGN_HEIGHT / height)
    return round(width * scale), round(height * scale)


def _slot_value(slot, key):
    return slot.get(key) if isinstance(slot, dict) else getattr(slot, key, None)


def _slot_layout(slot: dict | TemplateSlotReq) -> dict[str, float | int]:
    """좌상단 좌표 + 블록 크기.

    chart 블록의 크기는 ChartBuilder 원본 크기로 고정이고(사용자는 위치만 옮긴다),
    표·글 블록은 크기 자체가 배치 정보라 입력값을 쓴다.
    """
    kind = _slot_kind(slot)
    position = int(_slot_value(slot, "position") or 1)
    fallback = LEGACY_SLOT_LAYOUTS.get(position, DEFAULT_SLOT_LAYOUT) if kind == "chart" else DEFAULT_SLOT_LAYOUT
    values: dict[str, float | int] = {}
    explicit: dict[str, bool] = {}
    for key in ("x", "y"):
        raw = _slot_value(slot, key)
        explicit[key] = raw is not None
        values[key] = round(float(fallback[key] if raw is None else raw), 3)

    if kind == "chart":
        chart_width, chart_height = _chart_dimensions(slot)
        width_pct = chart_width / SLIDE_DESIGN_WIDTH * 100
        height_pct = chart_height / SLIDE_DESIGN_HEIGHT * 100
    else:
        raw_width = _slot_value(slot, "width")
        raw_height = _slot_value(slot, "height")
        width_pct = float(DEFAULT_BLOCK_WIDTH_PCT if raw_width in (None, 0) else raw_width)
        height_pct = float(DEFAULT_BLOCK_HEIGHT_PCT if raw_height in (None, 0) else raw_height)
        width_pct = min(100.0, max(8.0, width_pct))
        height_pct = min(100.0, max(6.0, height_pct))
        chart_width = round(width_pct / 100 * SLIDE_DESIGN_WIDTH)
        chart_height = round(height_pct / 100 * SLIDE_DESIGN_HEIGHT)

    values.update({
        "chart_width": chart_width,
        "chart_height": chart_height,
        "width": round(width_pct, 3),
        "height": round(height_pct, 3),
    })
    if not all(math.isfinite(float(values[key])) for key in ("x", "y")):
        raise HTTPException(400, "차트 좌표가 올바르지 않습니다.")
    if values["x"] < 0 or values["y"] < 0:
        raise HTTPException(400, "차트 좌표가 올바르지 않습니다.")
    if not explicit["x"]:
        values["x"] = round(min(float(values["x"]), 100 - float(values["width"])), 3)
    if not explicit["y"]:
        values["y"] = round(min(float(values["y"]), 100 - float(values["height"])), 3)
    if values["x"] + values["width"] > 100.001 or values["y"] + values["height"] > 100.001:
        raise HTTPException(400, "차트가 슬라이드 영역을 벗어납니다.")
    return values


# ── 조회 ──────────────────────────────────────────────────────────────────────
@router.get("/templates")
def list_templates(_user=Depends(current_user)):
    rows = sorted(_load_templates(), key=lambda row: str(row.get("updated_at") or ""), reverse=True)
    return {"ok": True, "templates": [_public_template(row) for row in rows]}


@router.get("/charts")
def list_charts(_user=Depends(current_user)):
    rows = list(_chart_history().values())
    rows.sort(key=lambda row: str(row.get("timestamp") or ""), reverse=True)
    charts = []
    for row in rows[:500]:
        chart = row.get("chart") if isinstance(row.get("chart"), dict) else {}
        source_text = " · ".join(
            f"{source.get('id')}:{source.get('root')}/{source.get('product')}"
            for source in (row.get("sources") or []) if isinstance(source, dict)
        )
        label = " × ".join(value for value in (str(chart.get("x") or ""), str(chart.get("y") or "")) if value)
        charts.append({
            "id": row.get("history_id"),
            "name": row.get("name"),
            "timestamp": row.get("timestamp"),
            "username": row.get("username"),
            "label": label or source_text or str(row.get("name") or row.get("history_id")),
            "source_text": source_text,
            "chart": chart,
            "variables": extract_variables(str(row.get("definition_code") or "")),
            "row_count": int(row.get("row_count") or 0),
            **_code_time_window(row.get("definition_code")),
        })
    return {"ok": True, "charts": charts}


# ── 저장 ──────────────────────────────────────────────────────────────────────
def _save_slot(slot: TemplateSlotReq, page_index: int, history: dict, history_by_name: dict, old_slots: dict) -> dict:
    kind = _slot_kind(slot)
    position = int(slot.position)
    common = {
        "position": position,
        "kind": kind,
        "title": _clean_text(slot.title, 180),
    }
    if kind == "chart":
        chart_ref = _clean_text(slot.chart_id, 120)
        if not chart_ref:
            raise HTTPException(400, f"{page_index + 1}페이지 {position}번 차트를 선택해 주세요.")
        history_row = history.get(chart_ref) or history_by_name.get(chart_ref.casefold())
        chart_id = str((history_row or {}).get("history_id") or chart_ref)
        old_slot = old_slots.get(chart_id) or old_slots.get(chart_ref) or {}
        definition = str((history_row or {}).get("definition_code") or old_slot.get("definition_code") or "")
        if not definition:
            raise HTTPException(400, f"Chart ID 또는 Name을 찾지 못했습니다: {chart_ref}")
        chart_name = str((history_row or {}).get("name") or old_slot.get("chart_name") or chart_id)
        layout = _slot_layout({"position": position, "kind": kind, "x": slot.x, "y": slot.y, "definition_code": definition})
        return {
            **common,
            **{key: layout[key] for key in ("x", "y", "chart_width", "chart_height")},
            "chart_id": chart_id,
            "chart_name": chart_name,
            "chart_label": chart_name,
            "definition_code": definition[:100_000],
        }

    layout = _slot_layout({
        "position": position, "kind": kind,
        "x": slot.x, "y": slot.y, "width": slot.width, "height": slot.height,
    })
    block = {**common, **{key: layout[key] for key in ("x", "y", "width", "height")}}
    if kind == "text":
        text = _clean_multiline(slot.text, 4000)
        if not text:
            raise HTTPException(400, f"{page_index + 1}페이지 {position}번 글 블록의 내용을 입력해 주세요.")
        block["text"] = text
    elif kind == "split":
        product = _clean_text(slot.product, 120)
        lot = _clean_text(slot.lot, 120)
        if not product or not lot:
            raise HTTPException(400, f"{page_index + 1}페이지 {position}번 Split 블록에는 제품과 랏이 필요합니다.")
        block.update({
            "product": product,
            "lot": lot,
            "columns": _clean_text(slot.columns, 2000),
            "display_mode": "split_check" if str(slot.display_mode or "").strip() == "split_check" else "matrix",
        })
    elif kind == "stats":
        source = int(slot.source_position or 0)
        if source < 1:
            raise HTTPException(400, f"{page_index + 1}페이지 {position}번 통계표는 대상 차트 번호가 필요합니다.")
        block.update({"source_position": source, "stats": _clean_text(slot.stats, 200) or "n,mean,median,std"})
    return block


@router.post("/templates")
def save_template(req: TemplateSaveReq, user=Depends(current_user)):
    requested_name = _clean_text(req.name, 120)
    if not requested_name:
        raise HTTPException(400, "Template 이름을 입력해 주세요.")
    if not req.pages or len(req.pages) > MAX_PAGES:
        raise HTTPException(400, f"페이지는 1~{MAX_PAGES}개까지 저장할 수 있습니다.")

    history = _chart_history()
    history_by_name = _chart_history_by_name(history)
    with _STORE_LOCK:
        rows = _load_templates()
        existing = next((row for row in rows if str(row.get("id")) == str(req.id)), None)
        if existing and user.get("role") != "admin" and existing.get("created_by") != user.get("username"):
            raise HTTPException(403, "작성자 또는 관리자만 이 Template을 수정할 수 있습니다.")
        old_slots = {
            str(slot.get("chart_id")): slot
            for page in ((existing or {}).get("pages") or [])
            for slot in (page.get("slots") or [])
            if isinstance(slot, dict) and slot.get("chart_id")
        }
        used_names = {
            str(value).casefold()
            for row in rows
            if not existing or str(row.get("id")) != str(existing.get("id"))
            for value in (row.get("id"), row.get("name"))
            if value
        }
        name = _unique_template_name(requested_name, used_names)
        pages = []
        for page_index, page in enumerate(req.pages):
            if len(page.slots) > MAX_CHARTS_PER_PAGE:
                raise HTTPException(400, f"페이지당 블록은 {MAX_CHARTS_PER_PAGE}개까지 배치할 수 있습니다.")
            positions: set[int] = set()
            slots = []
            for slot in page.slots:
                position = int(slot.position)
                if position < 1 or position > 1000 or position in positions:
                    raise HTTPException(400, f"{page_index + 1}페이지의 차트 위치가 올바르지 않습니다.")
                positions.add(position)
                slots.append(_save_slot(slot, page_index, history, history_by_name, old_slots))
            for slot in slots:
                if slot["kind"] == "stats" and int(slot["source_position"]) not in positions:
                    raise HTTPException(400, f"{page_index + 1}페이지 통계표가 가리키는 차트 번호가 없습니다.")
            pages.append({
                "id": _clean_text(page.id, 80) or f"page_{uuid.uuid4().hex[:10]}",
                "title": _clean_text(page.title, 180) or f"Page {page_index + 1}",
                "subtitle": _clean_text(page.subtitle, 180),
                "slots": sorted(slots, key=lambda item: item["position"]),
            })
        options = (req.options or TemplateOptionsReq()).model_dump()
        variables = [
            {
                "name": normalize_name(item.name),
                "label": _clean_text(item.label, 80) or normalize_name(item.name),
                "default": _clean_text(item.default, 200),
            }
            for item in (req.variables or [])
            if normalize_name(item.name)
        ]
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        if existing:
            template_id = str(existing.get("id") or "")
        else:
            used_ids = {str(row.get("id") or "") for row in rows if row.get("id")}
            template_id = ""
            while not template_id or template_id in used_ids:
                template_id = f"report_tpl_{uuid.uuid4().hex[:12]}"
        saved = {
            "id": template_id,
            "name": name,
            "pages": pages,
            "variables": variables,
            "options": options,
            "created_by": str((existing or {}).get("created_by") or user.get("username") or ""),
            "created_at": str((existing or {}).get("created_at") or now),
            "updated_by": str(user.get("username") or ""),
            "updated_at": now,
        }
        rows = [saved if str(row.get("id")) == template_id else row for row in rows]
        if not existing:
            rows.append(saved)
        _save_templates(rows)
    return {"ok": True, "template": _public_template(saved)}


@router.delete("/templates/{template_id}")
def delete_template(template_id: str, user=Depends(current_user)):
    with _STORE_LOCK:
        rows = _load_templates()
        target = next((row for row in rows if str(row.get("id")) == str(template_id)), None)
        if not target:
            raise HTTPException(404, "Template Report를 찾지 못했습니다.")
        if user.get("role") != "admin" and target.get("created_by") != user.get("username"):
            raise HTTPException(403, "작성자 또는 관리자만 삭제할 수 있습니다.")
        _save_templates([row for row in rows if str(row.get("id")) != str(template_id)])
    return {"ok": True, "id": template_id}


# ── 실행(전개) ────────────────────────────────────────────────────────────────
def _run_params(template: dict, req) -> dict:
    options = _template_options(template)
    try:
        bindings = validate_bindings(getattr(req, "bindings", None) or {})
    except ReportVariableError as exc:
        raise HTTPException(400, str(exc)) from exc
    for item in _template_variables(template):
        bindings.setdefault(item["name"], item["default"])

    repeat_variable = options["repeat_variable"]
    repeat_values = [
        value
        for raw in (getattr(req, "repeat_values", None) or [])
        for value in split_list(raw)
    ]
    if not repeat_values and repeat_variable and bindings.get(repeat_variable):
        repeat_values = split_list(bindings[repeat_variable])
    if len(repeat_values) > MAX_REPEAT_VALUES:
        raise HTTPException(400, f"한 번에 실행할 값은 {MAX_REPEAT_VALUES}개까지입니다.")

    raw_context = getattr(req, "context", None)
    if hasattr(raw_context, "model_dump"):
        raw_context = raw_context.model_dump()
    elif hasattr(raw_context, "dict"):
        raw_context = raw_context.dict()
    raw_context = raw_context if isinstance(raw_context, dict) else {}

    def _context_values(name: str) -> list[str]:
        values: list[str] = []
        seen: set[str] = set()
        for raw in raw_context.get(name) or []:
            value = _clean_text(raw, 160)
            key = value.casefold()
            if value and key not in seen:
                seen.add(key)
                values.append(value)
            if len(values) >= 200:
                break
        return values

    recent_days = max(0, min(3650, int(raw_context.get("recent_days") or 0)))
    color_rules = [_clean_text(rule, 500) for rule in (raw_context.get("color_rules") or []) if _clean_text(rule, 500)][:300]
    context = {
        "root_lot_ids": _context_values("root_lot_ids"),
        "wafer_ids": _context_values("wafer_ids"),
        "override_recent_days": bool(raw_context.get("override_recent_days")),
        "recent_days": recent_days,
        "date_column": _clean_text(raw_context.get("date_column") or "tkout_time", 120) or "tkout_time",
        "color_rules": color_rules,
        "color_else": _clean_text(raw_context.get("color_else") or "gray", 80) or "gray",
    }
    return {
        "bindings": bindings,
        "repeat_variable": repeat_variable,
        "repeat_values": repeat_values or [""],
        "options": options,
        "context": context,
    }


def _resolve(text: str, bindings: dict, where: str) -> str:
    try:
        return substitute(text, bindings)
    except ReportVariableError as exc:
        raise HTTPException(400, f"{where}: {exc}") from exc


def _chart_request(definition: str, where: str, context: dict | None = None) -> dict:
    """저장 코드를 실행 요청으로 옮기고 선택된 공통 실행 컨텍스트만 덧씌운다."""
    try:
        parsed = parse_chart_builder_definition(definition)
    except ChartBuilderDefinitionError as exc:
        raise HTTPException(400, f"{where} Chart 코드 오류: {exc}") from exc
    context = context or {}
    sources = [dict(source) for source in (parsed.get("sources") or [])]
    for source in sources:
        if context.get("root_lot_ids"):
            source["runtime_root_lot_ids"] = list(context["root_lot_ids"])
        if context.get("wafer_ids"):
            source["runtime_wafer_ids"] = list(context["wafer_ids"])
        if context.get("override_recent_days"):
            days = int(context.get("recent_days") or 0)
            source["runtime_recent_days"] = days
            source["runtime_date_column"] = str(context.get("date_column") or "tkout_time") if days else ""
    chart = dict(parsed.get("chart") or {})
    if context.get("color_rules"):
        chart.update({
            "color": "custom",
            "color_rules": list(context["color_rules"]),
            "color_else": str(context.get("color_else") or "gray"),
        })
    return {
        "sources": sources,
        "joins": parsed.get("joins") or [],
        "max_rows": parsed.get("max_rows") or 10000,
        "chart": chart,
        "save_history": False,
    }


def _expand_deck(template: dict, params: dict) -> dict:
    """템플릿 + 실행 인자 → 실제로 그릴 페이지 목록. 인자가 같으면 결과도 같다.

    미리보기와 PPTX 내보내기가 같은 함수를 다시 부르기 때문에, 화면에서 본 자리와
    파일의 자리가 어긋나지 않는다.
    """
    options = params["options"]
    pages_out: list[dict] = []
    charts: list[dict] = []
    for repeat_value in params["repeat_values"]:
        bindings = dict(params["bindings"])
        if params["repeat_variable"] and repeat_value:
            bindings[params["repeat_variable"]] = repeat_value
        for page in template.get("pages") or []:
            page_index = len(pages_out)
            if page_index >= MAX_RENDERED_PAGES:
                raise HTTPException(400, f"생성할 페이지가 {MAX_RENDERED_PAGES}장을 넘습니다. 값 개수를 줄여 주세요.")
            where = f"{page_index + 1}페이지"
            blocks: list[dict] = []
            for slot in page.get("slots") or []:
                position = int(slot.get("position") or 1)
                kind = _slot_kind(slot)
                layout = _slot_layout(slot)
                block = {
                    "key": f"{page_index}:{position}",
                    "page_index": page_index,
                    "position": position,
                    "kind": kind,
                    "title": _resolve(str(slot.get("title") or ""), bindings, where),
                    "repeat_value": repeat_value,
                    **{name: layout[name] for name in ("x", "y", "width", "height", "chart_width", "chart_height")},
                }
                if kind == "chart":
                    definition = _resolve(str(slot.get("definition_code") or ""), bindings, where)
                    block.update({
                        "chart_id": str(slot.get("chart_id") or ""),
                        "chart_label": str(slot.get("chart_label") or slot.get("chart_name") or ""),
                        "request": _chart_request(definition, where, params.get("context")),
                    })
                    charts.append(block)
                elif kind == "text":
                    block["text"] = _resolve(str(slot.get("text") or ""), bindings, where)
                elif kind == "split":
                    block.update({
                        "product": _resolve(str(slot.get("product") or ""), bindings, where),
                        "lot": _resolve(str(slot.get("lot") or ""), bindings, where),
                        "columns": _resolve(str(slot.get("columns") or ""), bindings, where),
                        "display_mode": str(slot.get("display_mode") or "matrix"),
                    })
                elif kind == "stats":
                    source = int(slot.get("source_position") or 0)
                    block.update({
                        "source_position": source,
                        "stats": str(slot.get("stats") or "n,mean,median,std"),
                        "source_key": f"{page_index}:{source}",
                    })
                blocks.append(block)
            pages_out.append({
                "index": page_index,
                "title": _resolve(str(page.get("title") or f"Page {page_index + 1}"), bindings, where),
                "subtitle": _resolve(str(page.get("subtitle") or ""), bindings, where),
                "repeat_value": repeat_value,
                "blocks": blocks,
            })
    cover_bindings = dict(params["bindings"])
    if params["repeat_variable"] and params["repeat_values"][0]:
        cover_bindings[params["repeat_variable"]] = params["repeat_values"][0]
    subtitle = _resolve(options["subtitle"], cover_bindings, "표지") if options["subtitle"] else ""
    if not subtitle:
        parts = [value for value in params["repeat_values"] if value]
        subtitle = " · ".join(parts[:4]) + ("…" if len(parts) > 4 else "")
    return {
        "title": str(template.get("name") or "Template Report"),
        "subtitle": subtitle,
        "cover": options["cover"],
        "footer": options["footer"],
        "pages": pages_out,
        "charts": charts,
    }


@router.post("/run")
def prepare_run(req: TemplateRunReq, _user=Depends(current_user)):
    template = _template_or_404(req.template_id)
    params = _run_params(template, req)
    deck = _expand_deck(template, params)
    return {
        "ok": True,
        "template": _public_template(template),
        "bindings": params["bindings"],
        "repeat_variable": params["repeat_variable"],
        "repeat_values": [value for value in params["repeat_values"] if value],
        "context": params["context"],
        "deck": deck,
        "charts": deck["charts"],
    }


@router.post("/split-table")
def split_table_block(req: SplitBlockReq, _user=Depends(current_user)):
    """Split 블록 내용 — Inform 스냅샷과 같은 SplitTable 화면 규약을 그대로 쓴다."""
    from app_v2.modules.informs.splittable_embed import build_splittable_embed

    columns = [item.strip() for item in str(req.columns or "").split(",") if item.strip()]
    embed = build_splittable_embed(product=req.product, lot_id=req.lot_id, custom_cols=columns)
    header = ["parameter", *(embed.get("st_view", {}).get("headers") or [])]
    rows = [[str(cell) for cell in row] for row in (embed.get("rows") or [])]
    max_rows = max(1, min(MAX_TABLE_ROWS * 4, int(req.max_rows or MAX_TABLE_ROWS)))
    truncated = len(rows) > max_rows
    return {
        "ok": True,
        "columns": header,
        "rows": rows[:max_rows],
        "row_total": len(rows),
        "truncated": truncated,
        "note": str(embed.get("note") or ""),
        "source": str(embed.get("source") or ""),
    }


# ── 내보내기 ──────────────────────────────────────────────────────────────────
def _image_key(image: ExportImageReq) -> str:
    key = str(image.key or "").strip()
    return key or f"{int(image.page_index)}:{int(image.position)}"


def _decode_images(images: list[ExportImageReq]) -> dict[str, bytes]:
    if len(images) > MAX_IMAGES:
        raise HTTPException(400, "차트 이미지가 허용 개수를 넘었습니다.")
    decoded: dict[str, bytes] = {}
    for image in images:
        match = re.fullmatch(r"data:image/(png|jpeg);base64,([A-Za-z0-9+/=\s]+)", str(image.data_url or ""), re.IGNORECASE)
        if not match:
            raise HTTPException(400, "차트 이미지는 PNG 또는 JPEG data URL이어야 합니다.")
        try:
            payload = base64.b64decode(match.group(2), validate=False)
        except Exception as exc:
            raise HTTPException(400, "차트 이미지 base64를 해석하지 못했습니다.") from exc
        if not payload or len(payload) > 12 * 1024 * 1024:
            raise HTTPException(400, "차트 이미지 한 장은 12MB 이하여야 합니다.")
        decoded[_image_key(image)] = payload
    return decoded


def _decode_tables(tables: list[ExportTableReq]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for table in tables or []:
        key = str(table.key or "").strip()
        if not key:
            continue
        columns = [_clean_text(value, 60) for value in (table.columns or [])][:MAX_TABLE_COLUMNS]
        rows = [
            [_clean_text(cell, 60) for cell in (row or [])][:MAX_TABLE_COLUMNS]
            for row in (table.rows or [])[:MAX_TABLE_ROWS]
        ]
        out[key] = {
            "title": _clean_text(table.title, 180),
            "columns": columns,
            "rows": rows,
            "note": _clean_text(table.note, 200),
        }
    return out


def _download_header(filename: str) -> str:
    """Return an ASCII-safe Content-Disposition with a UTF-8 filename fallback."""
    cleaned = safe_filename(filename)
    ascii_name = cleaned.encode("ascii", "ignore").decode("ascii").strip("._") or "download"
    suffix = Path(cleaned).suffix
    if suffix and not ascii_name.lower().endswith(suffix.lower()):
        ascii_name += suffix
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(cleaned)}"


def _navy():
    from pptx.dml.color import RGBColor

    return RGBColor(*REPORT_NAVY)


def _add_bar(slide, top_in: float, height_in: float, width_emu):
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Inches

    bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(top_in), width_emu, Inches(height_in))
    bar.fill.solid()
    bar.fill.fore_color.rgb = _navy()
    bar.line.fill.background()
    bar.shadow.inherit = False
    return bar


def _write(text_frame, text: str, *, size: int, bold: bool, color, align, font: str = REPORT_FONT):
    from pptx.util import Pt

    text_frame.clear()
    lines = str(text or "").split("\n")
    for index, line in enumerate(lines):
        paragraph = text_frame.paragraphs[0] if index == 0 else text_frame.add_paragraph()
        paragraph.text = line
        paragraph.alignment = align
        paragraph.font.size = Pt(size)
        paragraph.font.bold = bold
        paragraph.font.name = font
        paragraph.font.color.rgb = color


def _add_title_bar(slide, deck_width_emu, title: str, subtitle: str):
    """상단 네이비 바 + 흰 제목 — HOL Auto Report 슬라이드 헤더와 같은 규약."""
    from pptx.dml.color import RGBColor
    from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
    from pptx.util import Inches

    _add_bar(slide, 0.0, TITLE_BAR_HEIGHT_IN, deck_width_emu)
    title_box = slide.shapes.add_textbox(Inches(0.28), Inches(0.05), Inches(9.4), Inches(TITLE_BAR_HEIGHT_IN - 0.1))
    title_box.text_frame.word_wrap = False
    title_box.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    _write(title_box.text_frame, title, size=22, bold=True, color=RGBColor(255, 255, 255), align=PP_ALIGN.LEFT)
    if subtitle:
        sub_box = slide.shapes.add_textbox(Inches(9.75), Inches(0.05), Inches(3.3), Inches(TITLE_BAR_HEIGHT_IN - 0.1))
        sub_box.text_frame.word_wrap = False
        sub_box.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        _write(sub_box.text_frame, subtitle, size=11, bold=False, color=RGBColor(0xD6, 0xE3, 0xF3), align=PP_ALIGN.RIGHT)


def _add_footer(slide, left_text: str, right_text: str):
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Inches

    grey = RGBColor(0x70, 0x7A, 0x8A)
    left_box = slide.shapes.add_textbox(Inches(0.3), Inches(SLIDE_HEIGHT_IN - 0.34), Inches(8.5), Inches(0.26))
    left_box.text_frame.word_wrap = False
    _write(left_box.text_frame, left_text, size=9, bold=False, color=grey, align=PP_ALIGN.LEFT)
    right_box = slide.shapes.add_textbox(Inches(9.5), Inches(SLIDE_HEIGHT_IN - 0.34), Inches(3.5), Inches(0.26))
    right_box.text_frame.word_wrap = False
    _write(right_box.text_frame, right_text, size=9, bold=False, color=grey, align=PP_ALIGN.RIGHT)


def _add_cover(deck, title: str, subtitle: str, meta: str):
    """표지 — auto report `make_title_page` 와 같은 구성(상하 바·대제목·구분선)."""
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
    from pptx.util import Inches, Pt

    slide = deck.slides.add_slide(deck.slide_layouts[6])
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = RGBColor(255, 255, 255)
    grey = RGBColor(0x60, 0x6A, 0x7A)
    _add_bar(slide, 0.0, COVER_BAR_HEIGHT_IN, deck.slide_width)
    _add_bar(slide, SLIDE_HEIGHT_IN - COVER_BAR_HEIGHT_IN, COVER_BAR_HEIGHT_IN, deck.slide_width)

    date_box = slide.shapes.add_textbox(Inches(SLIDE_WIDTH_IN - 3.9), Inches(0.7), Inches(3.6), Inches(0.4))
    _write(date_box.text_frame, dt.datetime.now().strftime("%Y-%m-%d"), size=13, bold=False, color=grey, align=PP_ALIGN.RIGHT)

    title_box = slide.shapes.add_textbox(Inches(0.6), Inches(2.55), Inches(SLIDE_WIDTH_IN - 1.2), Inches(1.5))
    title_box.text_frame.word_wrap = True
    title_box.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    _write(title_box.text_frame, title, size=44, bold=True, color=_navy(), align=PP_ALIGN.CENTER)

    line = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(SLIDE_WIDTH_IN / 2 - 2.2), Inches(4.18), Inches(4.4), Pt(2.5))
    line.fill.solid()
    line.fill.fore_color.rgb = _navy()
    line.line.fill.background()
    line.shadow.inherit = False

    if subtitle:
        sub_box = slide.shapes.add_textbox(Inches(0.6), Inches(4.35), Inches(SLIDE_WIDTH_IN - 1.2), Inches(0.8))
        sub_box.text_frame.word_wrap = True
        _write(sub_box.text_frame, subtitle, size=22, bold=False, color=grey, align=PP_ALIGN.CENTER)
    if meta:
        meta_box = slide.shapes.add_textbox(Inches(0.6), Inches(5.25), Inches(SLIDE_WIDTH_IN - 1.2), Inches(0.5))
        meta_box.text_frame.word_wrap = True
        _write(meta_box.text_frame, meta, size=13, bold=False, color=grey, align=PP_ALIGN.CENTER)
    return slide


def _set_cell_border(cell):
    """표 칸의 4변 선 — python-pptx 기본 API에 없어 XML로 직접 넣는다."""
    from pptx.oxml.xmlchemy import OxmlElement

    tcPr = cell._tc.get_or_add_tcPr()
    ns = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    for tag in ("lnL", "lnR", "lnT", "lnB"):
        for element in tcPr.findall(ns + tag):
            tcPr.remove(element)
    index = 0
    for tag in ("a:lnL", "a:lnR", "a:lnT", "a:lnB"):
        line = OxmlElement(tag)
        line.set("w", "9525")
        line.set("cmpd", "sng")
        fill = OxmlElement("a:solidFill")
        color = OxmlElement("a:srgbClr")
        color.set("val", "9AA7B8")
        fill.append(color)
        line.append(fill)
        tcPr.insert(index, line)
        index += 1


def _add_table(slide, rect: dict, table: dict):
    from pptx.dml.color import RGBColor
    from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
    from pptx.util import Inches, Pt

    columns = list(table.get("columns") or [])
    rows = [list(row) for row in (table.get("rows") or [])]
    if not columns and rows:
        columns = [f"C{index + 1}" for index in range(len(rows[0]))]
    if not columns:
        return
    columns = columns[:MAX_TABLE_COLUMNS]
    rows = [row[:len(columns)] + [""] * max(0, len(columns) - len(row)) for row in rows[:MAX_TABLE_ROWS]]

    x = SLIDE_WIDTH_IN * float(rect["x"]) / 100
    y = SLIDE_HEIGHT_IN * float(rect["y"]) / 100
    width = SLIDE_WIDTH_IN * float(rect["width"]) / 100
    height = SLIDE_HEIGHT_IN * float(rect["height"]) / 100
    bottom = y + height
    title = str(table.get("title") or "")
    if title:
        label = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(width), Inches(0.26))
        label.text_frame.word_wrap = False
        _write(label.text_frame, title, size=12, bold=True, color=_navy(), align=PP_ALIGN.LEFT)
        y += 0.3
        height = max(0.4, height - 0.3)

    note = str(table.get("note") or "")
    if note:
        height = max(0.4, height - 0.24)
    count = len(rows) + 1
    row_height = max(0.15, min(0.3, height / count))
    shape = slide.shapes.add_table(count, len(columns), Inches(x), Inches(y), Inches(width), Inches(row_height * count))
    grid = shape.table
    # 첫 열은 항목 이름이라 넓게, 나머지는 균등 — SplitTable 화면과 같은 감각.
    first = min(width * 0.34, 2.6) if len(columns) > 2 else width / len(columns)
    rest = (width - first) / max(1, len(columns) - 1) if len(columns) > 1 else width
    grid.columns[0].width = Inches(first)
    for index in range(1, len(columns)):
        grid.columns[index].width = Inches(rest)
    for index in range(count):
        grid.rows[index].height = Inches(row_height)

    font_size = 9 if len(columns) <= 8 else (8 if len(columns) <= 14 else 7)
    for index, name in enumerate(columns):
        cell = grid.cell(0, index)
        cell.text = str(name)
        cell.fill.solid()
        cell.fill.fore_color.rgb = _navy()
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        cell.margin_left = cell.margin_right = Inches(0.02)
        cell.margin_top = cell.margin_bottom = Inches(0.0)
        _set_cell_border(cell)
        for paragraph in cell.text_frame.paragraphs:
            paragraph.font.size = Pt(font_size)
            paragraph.font.bold = True
            paragraph.font.name = REPORT_FONT
            paragraph.font.color.rgb = RGBColor(255, 255, 255)
            paragraph.alignment = PP_ALIGN.CENTER
    for row_index, row in enumerate(rows, start=1):
        stripe = RGBColor(0xF2, 0xF5, 0xFA) if row_index % 2 == 0 else RGBColor(255, 255, 255)
        for column_index, value in enumerate(row):
            cell = grid.cell(row_index, column_index)
            cell.text = str(value)
            cell.fill.solid()
            cell.fill.fore_color.rgb = stripe
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            cell.margin_left = cell.margin_right = Inches(0.02)
            cell.margin_top = cell.margin_bottom = Inches(0.0)
            cell.text_frame.word_wrap = False
            _set_cell_border(cell)
            for paragraph in cell.text_frame.paragraphs:
                paragraph.font.size = Pt(font_size)
                paragraph.font.bold = column_index == 0
                paragraph.font.name = REPORT_FONT
                paragraph.font.color.rgb = RGBColor(0x1A, 0x1A, 0x1A)
                paragraph.alignment = PP_ALIGN.LEFT if column_index == 0 else PP_ALIGN.CENTER
    if note:
        # PowerPoint 는 글자가 길면 행을 스스로 늘린다. 머리글이 두 줄이 되는 경우까지
        # 감안해 한 줄치 여유를 두되, 블록 바깥으로는 내려가지 않게 잡는다.
        note_top = min(y + row_height * (count + 0.8) + 0.02, max(y, bottom - 0.24))
        note_box = slide.shapes.add_textbox(Inches(x), Inches(note_top), Inches(width), Inches(0.22))
        note_box.text_frame.word_wrap = False
        _write(note_box.text_frame, note, size=9, bold=False, color=RGBColor(0x70, 0x7A, 0x8A), align=PP_ALIGN.LEFT)


def _add_text_block(slide, rect: dict, title: str, text: str):
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Inches

    x = SLIDE_WIDTH_IN * float(rect["x"]) / 100
    y = SLIDE_HEIGHT_IN * float(rect["y"]) / 100
    width = SLIDE_WIDTH_IN * float(rect["width"]) / 100
    height = SLIDE_HEIGHT_IN * float(rect["height"]) / 100
    if title:
        label = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(width), Inches(0.26))
        label.text_frame.word_wrap = False
        _write(label.text_frame, title, size=12, bold=True, color=_navy(), align=PP_ALIGN.LEFT)
        y += 0.3
        height = max(0.3, height - 0.3)
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(width), Inches(height))
    box.text_frame.word_wrap = True
    _write(box.text_frame, text, size=12, bold=False, color=RGBColor(0x1F, 0x29, 0x37), align=PP_ALIGN.LEFT)


def _block_label(block: dict) -> str:
    return str(block.get("title") or "").strip()


def _pptx_bytes(template: dict, images: dict[str, bytes], *,
                deck_spec: dict | None = None, tables: dict[str, dict] | None = None) -> bytes:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.util import Inches

    tables = tables or {}
    if deck_spec is None:
        params = _run_params(template, TemplateRunReq(template_id=str(template.get("id") or "")))
        deck_spec = _expand_deck(template, params)

    deck = Presentation()
    deck.slide_width = Inches(SLIDE_WIDTH_IN)
    deck.slide_height = Inches(SLIDE_HEIGHT_IN)
    deck.core_properties.title = str(template.get("name") or "Template Report")
    deck.core_properties.subject = "Flow Template Report"
    deck.core_properties.author = str(template.get("updated_by") or template.get("created_by") or "Flow")
    blank = deck.slide_layouts[6]

    if deck_spec.get("cover"):
        _add_cover(
            deck,
            str(deck_spec.get("title") or template.get("name") or "Template Report"),
            str(deck_spec.get("subtitle") or ""),
            "Flow Template Report",
        )

    pages = deck_spec.get("pages") or []
    total = len(pages)
    for page in pages:
        slide = deck.slides.add_slide(blank)
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = RGBColor(255, 255, 255)
        for block in page.get("blocks") or []:
            key = str(block.get("key") or "")
            kind = str(block.get("kind") or "chart")
            x = SLIDE_WIDTH_IN * float(block["x"]) / 100
            y = SLIDE_HEIGHT_IN * float(block["y"]) / 100
            width = SLIDE_WIDTH_IN * float(block["width"]) / 100
            height = SLIDE_HEIGHT_IN * float(block["height"]) / 100
            if kind == "chart":
                payload = images.get(key)
                if not payload:
                    continue
                slide.shapes.add_picture(io.BytesIO(payload), Inches(x), Inches(y), width=Inches(width), height=Inches(height))
            elif kind == "text":
                _add_text_block(slide, block, _block_label(block), str(block.get("text") or ""))
            else:
                table = tables.get(key)
                if not table:
                    continue
                _add_table(slide, block, {**table, "title": table.get("title") or _block_label(block)})
        # TITLE stays above freely placed charts, matching the browser editor's z-order.
        _add_title_bar(slide, deck.slide_width, str(page.get("title") or ""), str(page.get("subtitle") or ""))
        if deck_spec.get("footer"):
            _add_footer(
                slide,
                str(template.get("name") or ""),
                f"{page.get('index', 0) + 1} / {total}",
            )
    out = io.BytesIO()
    deck.save(out)
    return out.getvalue()


@router.post("/export/pptx")
def export_pptx(req: ExportReq, _user=Depends(current_user)):
    template = _template_or_404(req.template_id)
    params = _run_params(template, req)
    deck_spec = _expand_deck(template, params)
    payload = _pptx_bytes(
        template,
        _decode_images(req.images),
        deck_spec=deck_spec,
        tables=_decode_tables(req.tables),
    )
    stamp = dt.datetime.now().strftime("%Y%m%d")
    filename = safe_filename(f"{template.get('name') or 'template_report'}_{stamp}.pptx")
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": _download_header(filename)},
    )


@router.post("/export/images")
def export_images(req: ExportReq, _user=Depends(current_user)):
    template = _template_or_404(req.template_id)
    decoded = _decode_images(req.images)
    labels = {_image_key(image): safe_filename(str(image.chart_id or ""))[:80] for image in req.images}
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for key in sorted(decoded, key=_parse_key):
            page_index, position = _parse_key(key)
            token = labels.get(key) or f"chart_{position}"
            archive.writestr(f"slide_{page_index + 1:02d}_chart_{position}_{token}.png", decoded[key])
    filename = safe_filename(f"{template.get('name') or 'template_report'}_chart_images.zip")
    return Response(
        content=out.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": _download_header(filename)},
    )


def _parse_key(key: str) -> tuple[int, int]:
    page_text, _, position_text = str(key or "").partition(":")
    try:
        return int(page_text), int(position_text)
    except ValueError:
        return 0, 0
