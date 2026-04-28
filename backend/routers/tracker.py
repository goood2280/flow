"""routers/tracker.py v4.1.0 — Issue board + inline images in description + lot/wafer table"""
import datetime, uuid, base64, re
from pathlib import Path
import sys

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_APP_ROOT = _BACKEND_ROOT.parent
for _path in (_APP_ROOT, _BACKEND_ROOT):
    _raw = str(_path)
    sys.path[:] = [p for p in sys.path if p != _raw]
    sys.path.insert(0, _raw)

from fastapi import APIRouter, HTTPException, Query, Request, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, List
from core.paths import PATHS
from core.utils import load_json, save_json
from core.auth import current_user, require_admin
from core.tracker_schema import migrate_tracker_issues_file, normalize_lot_row
from app_v2.modules.tracker.repository import TrackerIssueRepository
from app_v2.modules.tracker.service import TrackerService

router = APIRouter(prefix="/api/tracker", tags=["tracker"])
TRACKER_DIR = PATHS.data_root / "tracker"
IMG_DIR = TRACKER_DIR / "images"
for d in (TRACKER_DIR, IMG_DIR):
    d.mkdir(parents=True, exist_ok=True)
ISSUES_FILE = TRACKER_DIR / "issues.json"
CATS_FILE = TRACKER_DIR / "categories.json"
SETTINGS_FILE = PATHS.data_root / "settings.json"
TRACKER_SERVICE = TrackerService(TrackerIssueRepository(ISSUES_FILE))
migrate_tracker_issues_file(reason="router_import", actor="tracker_router")
# v8.1.5: categories can now be mixed list of str or {name, color}
DEFAULT_CATS = [
    {"name": "Analysis", "color": "#3b82f6"},
    {"name": "Monitor", "color": "#a855f7"},
    {"name": "Equipment", "color": "#f97316"},
    {"name": "Process", "color": "#10b981"},
    {"name": "Quality", "color": "#ef4444"},
    {"name": "Other", "color": "#64748b"},
]


def _load():
    return load_json(ISSUES_FILE, [])


def _hash_color(name: str) -> str:
    """Legacy HSL hash fallback for items without color (mirrors frontend catColor)."""
    if not name:
        return "#64748b"
    h = 0
    for ch in name:
        h = ((h << 5) - h + ord(ch)) & 0xFFFFFFFF
        # mimic JS |0 signed 32-bit
        if h >= 0x80000000:
            h -= 0x100000000
    return f"hsl({abs(h) % 360}, 58%, 58%)"


def _normalize_cats(raw):
    """Accept list of str or {name,color[,source]}; return list of {name,color,source}.
    v8.8.33: source 필드 (fab|et|both|auto) — Lot step 추적 모드. 미지정 시 auto.
    """
    out = []
    for item in raw or []:
        if isinstance(item, str):
            nm = item.strip()
            if nm:
                out.append({"name": nm, "color": _hash_color(nm), "source": "auto"})
        elif isinstance(item, dict):
            nm = (item.get("name") or "").strip()
            if nm:
                src = (item.get("source") or "auto").lower().strip()
                if src not in ("fab", "et", "both", "auto"):
                    src = "auto"
                row = {
                    "name": nm,
                    "color": item.get("color") or _hash_color(nm),
                    "source": src,
                }
                if isinstance(item.get("mail_group_ids"), list):
                    row["mail_group_ids"] = [str(x) for x in item.get("mail_group_ids") if str(x).strip()]
                if item.get("auto_close_step_id") is not None:
                    row["auto_close_step_id"] = str(item.get("auto_close_step_id") or "").strip()
                if item.get("max_issues_per_user") is not None:
                    try:
                        row["max_issues_per_user"] = int(item.get("max_issues_per_user") or 0)
                    except Exception:
                        pass
                out.append(row)
    return out


def _load_cats():
    """Returns list of {name, color} dicts (v8.1.5). Legacy str list auto-upgraded on read."""
    raw = load_json(CATS_FILE, DEFAULT_CATS)
    cats = _normalize_cats(raw)
    if cats:
        return cats
    # If categories.json exists but is empty/corrupt-shaped, the create/edit
    # form should still have usable categories instead of rendering a blank
    # required select.
    return _normalize_cats(DEFAULT_CATS)


def _cat_names():
    """Legacy shape (list of str) for code paths that still use it."""
    return [c["name"] for c in _load_cats()]


def _issue_mail_watch(issue: dict) -> dict:
    """Issue-level mail setting with legacy row-level watch.mail fallback."""
    current = issue.get("mail_watch") if isinstance(issue.get("mail_watch"), dict) else None
    if current is not None:
        return {
            "enabled": bool(current.get("enabled")),
            "mail_group_ids": [str(x) for x in (current.get("mail_group_ids") or []) if str(x).strip()],
            "updated_by": current.get("updated_by", ""),
            "updated_at": current.get("updated_at", ""),
        }
    groups = []
    enabled = False
    for lot in issue.get("lots") or []:
        watch = lot.get("watch") if isinstance(lot, dict) else {}
        if not isinstance(watch, dict) or not watch.get("mail"):
            continue
        enabled = True
        groups.extend(watch.get("mail_group_ids") or [])
    return {
        "enabled": enabled,
        "mail_group_ids": [str(x) for x in dict.fromkeys(str(g).strip() for g in groups if str(g).strip())],
        "legacy_row_mail": enabled,
    }


def _comment_count(issue: dict) -> int:
    total = 0
    for c in issue.get("comments") or []:
        if not isinstance(c, dict):
            continue
        total += 1
        total += len([r for r in (c.get("replies") or []) if isinstance(r, dict)])
    return total


def _category_source(category: str, default: str = "auto") -> str:
    """Resolve tracker category to lot tracking source."""
    name = (category or "").strip()
    low = name.lower()
    try:
        from core.lot_step import tracker_role_names_config
        roles = tracker_role_names_config()
    except Exception:
        roles = {"monitor": "Monitor", "analysis": "Analysis"}
    if low == str(roles.get("monitor") or "Monitor").strip().lower():
        return "fab"
    if low == str(roles.get("analysis") or "Analysis").strip().lower():
        return "et"
    if name:
        try:
            for c in _load_cats():
                if isinstance(c, dict) and (c.get("name") or "").strip().lower() == low:
                    src = (c.get("source") or "").lower().strip()
                    if src in ("fab", "et", "both"):
                        return src
                    break
        except Exception:
            pass
    src = (default or "auto").lower().strip()
    return src if src in ("fab", "et", "both", "auto") else "auto"


def _tracker_mltable_lot_candidates(product: str, prefix: str = "", limit: int = 200) -> list[dict]:
    """Use SplitTable's warmed ML_TABLE/FAB match cache for Tracker dropdowns."""
    prod = str(product or "").strip()
    if not prod:
        return []
    try:
        limit = max(1, min(500, int(limit or 200)))
    except Exception:
        limit = 200
    try:
        from routers import splittable
    except Exception:
        return []
    ml_product = prod if prod.upper().startswith("ML_TABLE_") else f"ML_TABLE_{prod}"
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add_rows(values, typ: str, source_root: str) -> None:
        for value in values or []:
            text = str(value or "").strip()
            if not text:
                continue
            key = (typ, text.upper())
            if key in seen:
                continue
            seen.add(key)
            out.append({"value": text, "type": typ, "source_root": source_root or "ML_TABLE"})
            if len(out) >= limit:
                return

    root_limit = max(1, limit // 2)
    try:
        roots = splittable.get_lot_candidates(
            product=ml_product,
            col="root_lot_id",
            prefix=prefix,
            limit=root_limit,
            source="auto",
            root_lot_id="",
        )
        add_rows(roots.get("candidates") or [], "root_lot_id", roots.get("fab_source") or roots.get("source") or "ML_TABLE")
    except Exception:
        pass

    remaining = max(1, limit - len(out))
    try:
        fabs = splittable.get_lot_candidates(
            product=ml_product,
            col="fab_lot_id",
            prefix=prefix,
            limit=remaining,
            source="auto",
            root_lot_id="",
        )
        add_rows(fabs.get("candidates") or [], "fab_lot_id", fabs.get("fab_source") or fabs.get("source") or "ML_TABLE")
    except Exception:
        pass
    return out[:limit]


def _save(issues):
    save_json(ISSUES_FILE, issues, indent=2)


def _save_image(data_uri: str) -> str:
    """Save base64 data URI to file, return filename."""
    match = re.match(r"data:image/(\w+);base64,(.*)", data_uri, re.DOTALL)
    if not match:
        return ""
    ext = match.group(1)
    if ext == "jpeg":
        ext = "jpg"
    raw = base64.b64decode(match.group(2))
    name = f"{uuid.uuid4().hex[:12]}.{ext}"
    (IMG_DIR / name).write_bytes(raw)
    return name


def _process_description(desc: str) -> tuple:
    """Extract base64 images from description HTML, save to disk, replace with markers."""
    if not desc:
        return desc, []
    saved = []
    def replace_img(m):
        src = m.group(1)
        if src.startswith("data:image/"):
            name = _save_image(src)
            if name:
                saved.append(name)
                return f"[IMG:{name}]"
        return m.group(0)
    processed = re.sub(r'<img[^>]+src="([^"]+)"[^>]*/?>', replace_img, desc)
    return processed, saved


def _render_description(desc: str) -> str:
    """Replace [IMG:name] markers with img tags for display."""
    if not desc:
        return desc
    def replace_marker(m):
        name = m.group(1)
        return f'<img src="/api/tracker/image?name={name}" style="max-width:100%;border-radius:6px;margin:4px 0;" />'
    return re.sub(r'\[IMG:([^\]]+)\]', replace_marker, desc)


class IssueCreate(BaseModel):
    title: str
    description: str = ""
    username: str = ""
    status: str = "in_progress"
    priority: str = "normal"
    category: str = ""
    images: List[str] = []
    lots: List[dict] = []
    links: List[str] = []
    # v8.5.0: group 가시성. 빈 배열 = public, 채워지면 해당 그룹 멤버만 조회.
    group_ids: List[str] = []

class IssueUpdate(BaseModel):
    issue_id: str
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    category: Optional[str] = None
    username: str = ""
    group_ids: Optional[List[str]] = None

class CommentReq(BaseModel):
    issue_id: str
    username: str = ""
    text: str = ""
    lot_id: str = ""
    wafer_id: str = ""


class CommentReplyReq(BaseModel):
    issue_id: str
    parent_index: int
    username: str = ""
    text: str = ""


class CommentDeleteReq(BaseModel):
    issue_id: str
    comment_index: int
    reply_index: Optional[int] = None


class LotBulkReq(BaseModel):
    issue_id: str
    username: str = ""
    rows: List[dict] = []


class LotCheckAllReq(BaseModel):
    issue_id: str


class TrackerSchedulerReq(BaseModel):
    enabled: bool = True
    interval_minutes: int = 30
    et_stable_delay_minutes: int = 180


class EtLotCacheRefreshReq(BaseModel):
    product: str = ""
    source_root: str = ""
    force: bool = True


class TrackerDbSourcesReq(BaseModel):
    monitor: str = ""
    analysis: str = ""
    monitor_name: str = "Monitor"
    analysis_name: str = "Analysis"
    monitor_mail_subject: str = ""
    monitor_mail_body: str = ""
    analysis_mail_subject: str = ""
    analysis_mail_body: str = ""


class TrackerMailPreviewReq(BaseModel):
    kind: str = "monitor"
    monitor_name: str = "Monitor"
    analysis_name: str = "Analysis"
    subject: str = ""
    body: str = ""


@router.get("/categories")
def get_categories():
    """v8.1.5: Returns list of {name, color}. Legacy str list auto-upgraded."""
    return {"categories": _load_cats()}


@router.get("/scheduler")
def get_tracker_scheduler(request: Request):
    """Tracker background scan settings + last run status."""
    me = current_user(request)
    from core.tracker_scheduler import scheduler_status
    data = scheduler_status()
    return {**data, "can_edit": me.get("role") == "admin"}


@router.post("/scheduler/save")
def save_tracker_scheduler(req: TrackerSchedulerReq, request: Request, _a=Depends(require_admin)):
    from core.tracker_scheduler import save_scheduler_config, scheduler_status
    save_scheduler_config(
        enabled=req.enabled,
        interval_minutes=req.interval_minutes,
        et_stable_delay_minutes=req.et_stable_delay_minutes,
    )
    return {"ok": True, **scheduler_status(), "can_edit": True}


@router.post("/scheduler/run-now")
def run_tracker_scheduler_now(request: Request, _a=Depends(require_admin)):
    from core.tracker_scheduler import run_once, scheduler_status
    run_result = run_once(force=True)
    return {
        "ok": bool(run_result.get("ok")),
        "run": run_result,
        **scheduler_status(),
        "can_edit": True,
    }


@router.get("/et-lot-cache/status")
def get_et_lot_cache_status(request: Request,
                            product: str = Query(""),
                            source_root: str = Query("")):
    me = current_user(request)
    if me.get("role") != "admin":
        raise HTTPException(403, "admin only")
    from core.lot_step import et_lot_cache_status
    return et_lot_cache_status(product=product, source_root=source_root)


@router.post("/et-lot-cache/refresh")
def refresh_et_lot_cache_now(req: EtLotCacheRefreshReq, request: Request, _a=Depends(require_admin)):
    from core.lot_step import refresh_et_lot_cache
    return refresh_et_lot_cache(
        product=req.product or "",
        source_root=req.source_root or "",
        force=bool(req.force),
    )


def _sync_role_categories(prev_roles: dict, next_roles: dict) -> dict:
    """Rename role categories and existing issues when Monitor/Analysis names change."""
    pairs = [
        (str(prev_roles.get("monitor") or "Monitor").strip() or "Monitor",
         str(next_roles.get("monitor") or "Monitor").strip() or "Monitor",
         "fab", "hsl(210, 58%, 58%)"),
        (str(prev_roles.get("analysis") or "Analysis").strip() or "Analysis",
         str(next_roles.get("analysis") or "Analysis").strip() or "Analysis",
         "et", "hsl(330, 58%, 58%)"),
    ]
    cats = _load_cats()
    changed_cats = False
    for old_name, new_name, source, color in pairs:
        old_idx = next((i for i, c in enumerate(cats) if (c.get("name") or "") == old_name), None)
        new_idx = next((i for i, c in enumerate(cats) if (c.get("name") or "") == new_name), None)
        if new_idx is not None:
            if cats[new_idx].get("source") != source:
                cats[new_idx] = {**cats[new_idx], "source": source}
                changed_cats = True
            if old_idx is not None and old_idx != new_idx:
                cats.pop(old_idx)
                changed_cats = True
            continue
        if old_idx is not None:
            cats[old_idx] = {**cats[old_idx], "name": new_name, "source": source}
            changed_cats = True
        else:
            cats.append({"name": new_name, "color": color, "source": source})
            changed_cats = True
    if changed_cats:
        save_json(CATS_FILE, cats, indent=2)

    issues = _load()
    issue_changes = 0
    for iss in issues:
        if not isinstance(iss, dict):
            continue
        for old_name, new_name, _source, _color in pairs:
            if old_name != new_name and (iss.get("category") or "") == old_name:
                iss["category"] = new_name
                iss["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
                issue_changes += 1
                break
    if issue_changes:
        _save(issues)
    return {"categories_changed": changed_cats, "issues_changed": issue_changes}


@router.get("/db-sources")
def get_tracker_db_sources(request: Request):
    from core.lot_step import list_db_source_roots, tracker_db_sources_config, tracker_role_names_config
    from core.tracker_templates import DEFAULT_MAIL_TEMPLATES, TEMPLATE_VARIABLES, tracker_mail_templates_config
    me = current_user(request)
    cfg = tracker_db_sources_config()
    roles = tracker_role_names_config()
    templates = tracker_mail_templates_config()
    return {
        "roots": list_db_source_roots(),
        "monitor": cfg.get("monitor") or "",
        "analysis": cfg.get("analysis") or "",
        "monitor_name": roles.get("monitor") or "Monitor",
        "analysis_name": roles.get("analysis") or "Analysis",
        "role_names": roles,
        "mail_templates": templates,
        "default_mail_templates": DEFAULT_MAIL_TEMPLATES,
        "template_variables": TEMPLATE_VARIABLES,
        "can_edit": me.get("role") == "admin",
    }


@router.get("/settings")
def get_tracker_settings_compat(request: Request):
    """Compatibility settings payload for PageGear builds expecting one endpoint."""
    return {
        "categories": _load_cats(),
        "db_sources": get_tracker_db_sources(request),
        "scheduler": get_tracker_scheduler(request),
    }


@router.get("")
def tracker_bootstrap(request: Request):
    """Root compatibility payload for clients probing /api/tracker."""
    return {
        "ok": True,
        "categories": _load_cats(),
        "issues": list_issues(request).get("issues", []),
        "db_sources": get_tracker_db_sources(request),
        "scheduler": get_tracker_scheduler(request),
    }


@router.post("/db-sources/save")
def save_tracker_db_sources(req: TrackerDbSourcesReq, request: Request, _a=Depends(require_admin)):
    from core.lot_step import FAB_ROOT, ET_ROOT, list_db_source_roots, tracker_db_sources_config, tracker_role_names_config
    from core.tracker_templates import DEFAULT_MAIL_TEMPLATES, TEMPLATE_VARIABLES, tracker_mail_templates_config
    prev_roles = tracker_role_names_config()
    settings = load_json(SETTINGS_FILE, {})
    if not isinstance(settings, dict):
        settings = {}
    monitor = (req.monitor or "").strip() or FAB_ROOT
    analysis = (req.analysis or "").strip() or ET_ROOT
    monitor_name = (req.monitor_name or "").strip() or "Monitor"
    analysis_name = (req.analysis_name or "").strip() or "Analysis"
    if monitor_name.lower() == analysis_name.lower():
        raise HTTPException(400, "Monitor와 Analysis 이름은 서로 달라야 합니다.")
    db_sources = {"monitor": monitor, "analysis": analysis}
    role_names = {"monitor": monitor_name, "analysis": analysis_name}
    prev_templates = tracker_mail_templates_config()
    mail_templates = {
        "monitor": {
            "subject": str(req.monitor_mail_subject or prev_templates["monitor"].get("subject") or DEFAULT_MAIL_TEMPLATES["monitor"]["subject"]),
            "body": str(req.monitor_mail_body or prev_templates["monitor"].get("body") or DEFAULT_MAIL_TEMPLATES["monitor"]["body"]),
        },
        "analysis": {
            "subject": str(req.analysis_mail_subject or prev_templates["analysis"].get("subject") or DEFAULT_MAIL_TEMPLATES["analysis"]["subject"]),
            "body": str(req.analysis_mail_body or prev_templates["analysis"].get("body") or DEFAULT_MAIL_TEMPLATES["analysis"]["body"]),
        },
    }
    settings["tracker_db_sources"] = dict(db_sources)
    settings["tracker_role_names"] = dict(role_names)
    settings["tracker_mail_templates"] = dict(mail_templates)
    tracker = settings.get("tracker") if isinstance(settings.get("tracker"), dict) else {}
    settings["tracker"] = {
        **tracker,
        "db_sources": dict(db_sources),
        "role_names": dict(role_names),
        "mail_templates": dict(mail_templates),
    }
    save_json(SETTINGS_FILE, settings, indent=2)
    sync = _sync_role_categories(prev_roles, role_names)
    cfg = tracker_db_sources_config()
    roles = tracker_role_names_config()
    templates = tracker_mail_templates_config()
    return {
        "ok": True,
        "roots": list_db_source_roots(),
        "monitor": cfg.get("monitor") or monitor,
        "analysis": cfg.get("analysis") or analysis,
        "monitor_name": roles.get("monitor") or monitor_name,
        "analysis_name": roles.get("analysis") or analysis_name,
        "role_names": roles,
        "mail_templates": templates,
        "default_mail_templates": DEFAULT_MAIL_TEMPLATES,
        "template_variables": TEMPLATE_VARIABLES,
        **sync,
        "can_edit": True,
    }


@router.post("/mail-template-preview")
def preview_tracker_mail_template(req: TrackerMailPreviewReq, request: Request):
    from core.tracker_templates import DEFAULT_MAIL_TEMPLATES, TEMPLATE_VARIABLES, render_tracker_mail, tracker_mail_context
    current_user(request)
    kind = "analysis" if (req.kind or "").lower().strip() in {"analysis", "et"} else "monitor"
    role_names = {
        "monitor": (req.monitor_name or "").strip() or "Monitor",
        "analysis": (req.analysis_name or "").strip() or "Analysis",
    }
    sample_issue = {
        "id": "ISS-260426-A001" if kind == "analysis" else "ISS-260426-M001",
        "title": "ET 측정 확인 요청" if kind == "analysis" else "Lot 진행 모니터링",
        "category": role_names["analysis"] if kind == "analysis" else role_names["monitor"],
        "description": (
            "<p>요청한 조건에 맞는 진행/측정이 확인되었습니다.</p>"
            "<p>본문에 작성한 분석 내용과 붙여넣은 이미지가 2MB 이하이면 이 영역에 같이 표시됩니다.</p>"
        ),
    }
    context = tracker_mail_context(
        kind,
        sample_issue,
        product="PRODUCT_A0",
        lot="A0001",
        root_lot_id="A0001",
        lot_id="FABLOT12345",
        wafer_id="7",
        step_id="ETA100030" if kind == "analysis" else "AA100010000250",
        target_step_id="" if kind == "analysis" else "AA100000",
        recent_et="VIA_DC(ETA100030) seq1(60pt),seq2(20pt),seq3(10pt)  M1_DC(ETA100040) seq1(30pt)",
        et_count=4 if kind == "analysis" else 0,
        recipient_groups="analysis-mail" if kind == "analysis" else "monitor-mail",
        source="et" if kind == "analysis" else "fab",
        source_root="1.RAWDATA_DB_ET" if kind == "analysis" else "1.RAWDATA_DB_FAB",
        checked_at=datetime.datetime.now().isoformat(timespec="seconds"),
    )
    rendered = render_tracker_mail(
        kind,
        context,
        templates_override={
            kind: {
                "subject": req.subject or DEFAULT_MAIL_TEMPLATES[kind]["subject"],
                "body": req.body or DEFAULT_MAIL_TEMPLATES[kind]["body"],
            }
        },
        role_names_override=role_names,
    )
    return {
        "ok": True,
        "kind": kind,
        "subject": rendered["subject"],
        "body": rendered["body"],
        "sample": {k: v for k, v in context.items() if k != "issue_detail_html"},
        "template_variables": TEMPLATE_VARIABLES,
    }


@router.get("/products")
def tracker_products(request: Request,
                     category: str = Query(""), source: str = Query("auto"),
                     prefix: str = Query(""), limit: int = Query(500)):
    from core.lot_step import db_product_candidates, source_root_for_context
    current_user(request)
    resolved_source = _category_source(category, source)
    source_root = source_root_for_context(resolved_source, category)
    products = db_product_candidates(
        source_root=source_root,
        source=resolved_source,
        prefix=prefix,
        limit=max(1, min(1000, int(limit or 500))),
    )
    return {
        "source": resolved_source,
        "source_root": source_root,
        "products": products,
    }


@router.get("/lot-candidates")
def tracker_lot_candidates(request: Request,
                           category: str = Query(""), source: str = Query("auto"),
                           product: str = Query(""), prefix: str = Query(""),
                           limit: int = Query(200)):
    from core.lot_step import lot_id_candidates, source_root_for_context
    current_user(request)
    resolved_source = _category_source(category, source)
    source_root = source_root_for_context(resolved_source, category)
    if not (product or "").strip():
        return {
            "source": resolved_source,
            "source_root": source_root,
            "requires_product": True,
            "candidates": [],
        }
    if resolved_source != "et":
        fast_candidates = _tracker_mltable_lot_candidates(
            product=product,
            prefix=prefix,
            limit=max(1, min(500, int(limit or 200))),
        )
        if fast_candidates:
            return {
                "source": resolved_source,
                "source_root": source_root,
                "candidates": fast_candidates,
                "cache": "mltable",
            }
    candidates = lot_id_candidates(
        product=product,
        source_root=source_root,
        source=resolved_source,
        prefix=prefix,
        limit=max(1, min(500, int(limit or 200))),
    )
    if candidates:
        cache_name = "et_lot" if any(c.get("cache") == "et_lot" for c in candidates) else ""
        return {
            "source": resolved_source,
            "source_root": source_root,
            "candidates": candidates,
            **({"cache": cache_name} if cache_name else {}),
        }
    return {
        "source": resolved_source,
        "source_root": source_root,
        "candidates": candidates,
    }


@router.get("/categories/usage")
def category_usage():
    """Return per-category issue count + orphan (issues whose category is no longer in list)."""
    cats = _cat_names()
    issues = _load()
    counts = {c: 0 for c in cats}
    orphans = {}
    for iss in issues:
        c = iss.get("category") or ""
        if not c:
            continue
        if c in counts:
            counts[c] += 1
        else:
            orphans[c] = orphans.get(c, 0) + 1
    return {"counts": counts, "orphans": orphans, "total": len(issues)}


@router.post("/categories/save")
def save_categories(cats: list, request: Request):
    """v8.1.5: accepts list of str OR list of {name, color}. Always stored as normalized {name, color}.
    v8.8.33 보안: admin 전용."""
    me = current_user(request)
    if me.get("role") != "admin":
        raise HTTPException(403, "admin only")
    normalized = _normalize_cats(cats)
    save_json(CATS_FILE, normalized)
    return {"ok": True, "categories": normalized}


@router.get("/issues")
def list_issues(request: Request, status: str = Query(""), limit: int = Query(200)):
    """v8.5.0: group_ids 가시성 필터. admin 은 전체 열람."""
    from routers.groups import filter_by_visibility
    me = current_user(request)
    issues = _load()
    if status:
        issues = [i for i in issues if i.get("status") == status]
    issues = filter_by_visibility(issues, me["username"], me.get("role", "user"), key="group_ids")
    out = []
    for iss in issues[-limit:]:
        # v8.8.28: 이슈 가져오기 picker 에서 최신 수정 우선 정렬 + 1줄 요약 노출을 위해
        #   updated_at, summary(한 줄) 필드를 응답에 포함한다. summary 는 HTML description 을
        #   태그 제거 + 첫 줄만 trim, 최대 140 char 로 압축.
        raw_html = (iss.get("description_html") or iss.get("description") or "")
        try:
            text = re.sub(r"<[^>]+>", " ", str(raw_html))
            text = re.sub(r"\s+", " ", text).strip()
            summary = text[:140]
        except Exception:
            summary = ""
        out.append({
            "id": iss["id"], "title": iss.get("title", ""),
            "status": iss.get("status", ""), "priority": iss.get("priority", "normal"),
            "category": iss.get("category", ""),
            "username": iss.get("username", ""),
            "created": iss.get("created", iss.get("timestamp", "")),
            "updated_at": iss.get("updated_at") or iss.get("created") or iss.get("timestamp", ""),
            "summary": summary,
            "closed_at": iss.get("closed_at"),
            "lot_count": len(iss.get("lots", [])),
            "comment_count": _comment_count(iss),
            "group_ids": iss.get("group_ids") or [],
        })
    # v8.8.28: updated_at 내림차순 정렬 (최신 수정 위). 기존 reversed(created 기준) 대체.
    out.sort(key=lambda x: (x.get("updated_at") or ""), reverse=True)
    return {"issues": out}


@router.get("/issue")
def get_issue(request: Request, issue_id: str = Query(...)):
    from routers.groups import filter_by_visibility
    me = current_user(request)
    issues = _load()
    issues = filter_by_visibility(issues, me["username"], me.get("role", "user"), key="group_ids")
    iss = next((i for i in issues if i["id"] == issue_id), None)
    if not iss:
        raise HTTPException(404)
    result = dict(iss)
    result["description_html"] = _render_description(result.get("description", ""))
    result["mail_watch"] = _issue_mail_watch(result)
    return {"issue": result}


@router.post("/create")
def create_issue(req: IssueCreate, request: Request):
    # v8.8.33 보안: current_user 필수 + username 은 서버 세션 기준으로 강제 (spoof 방지).
    me = current_user(request)
    req.username = me.get("username") or ""
    if not (req.category or "").strip():
        raise HTTPException(400, "카테고리를 지정해주세요.")
    issues = _load()
    now = datetime.datetime.now()
    iid = f"ISS-{now.strftime('%y%m%d')}-{uuid.uuid4().hex[:4].upper()}"
    desc, desc_images = _process_description(req.description)
    img_names = list(desc_images)
    for img in req.images:
        if img.startswith("data:"):
            name = _save_image(img)
            if name:
                img_names.append(name)
    lots = [normalize_lot_row({**lot, "username": req.username, "added": now.isoformat()}) for lot in req.lots]
    result = TRACKER_SERVICE.create_legacy_issue(
        issue_id=iid,
        title=req.title,
        description=desc,
        username=req.username,
        status=req.status,
        priority=req.priority,
        category=req.category or "",
        links=req.links or [],
        images=img_names,
        lots=lots,
        group_ids=list(req.group_ids or []),
    )
    if not result.ok:
        raise HTTPException(400, result.error)
    return {"ok": True, "id": iid}


@router.post("/update")
def update_issue(req: IssueUpdate, request: Request):
    me = current_user(request)
    req.username = me.get("username") or ""
    issues = _load()
    iss = next((i for i in issues if i["id"] == req.issue_id), None)
    if not iss:
        raise HTTPException(404)
    if req.category is not None and not (req.category or "").strip():
        raise HTTPException(400, "카테고리를 지정해주세요.")
    desc = None
    desc_images = []
    if req.description is not None:
        desc, desc_images = _process_description(req.description)
    old_status = iss.get("status")
    status_changed = False
    if req.status is not None:
        status_changed = (old_status != req.status)
    result = TRACKER_SERVICE.update_legacy_issue(
        issue_id=req.issue_id,
        username=req.username,
        title=req.title,
        description=desc,
        status=req.status,
        priority=req.priority,
        category=req.category,
        group_ids=req.group_ids,
        append_images=desc_images,
    )
    if not result.ok:
        raise HTTPException(404, result.error)
    iss = result.data["issue"]
    # v8.8.33: 이슈 작성자에게 상태 변경 알림.
    if status_changed:
        try:
            from core.notify import emit_event
            target = iss.get("username")
            if target:
                emit_event(
                    "my_tracker_status_changed",
                    actor=req.username or "",
                    target_user=target,
                    title=f"[이슈 상태 변경] {iss.get('title') or iss['id']}",
                    body=f"{req.username or ''} · {old_status or '-'} → {req.status}",
                    payload={"issue_id": iss["id"], "old_status": old_status or "", "new_status": req.status},
                )
        except Exception:
            pass
    return {"ok": True}


@router.post("/comment")
def add_comment(req: CommentReq, request: Request):
    me = current_user(request)
    req.username = me.get("username") or ""
    result = TRACKER_SERVICE.add_legacy_comment(
        issue_id=req.issue_id,
        username=req.username,
        text=req.text,
        lot_id=req.lot_id,
        wafer_id=req.wafer_id,
    )
    if not result.ok:
        raise HTTPException(404, result.error)
    iss = result.data["issue"]
    # v8.8.33: 이슈 작성자와 이전 댓글 작성자들에게 "내 이슈에 댓글" 알림.
    try:
        from core.notify import emit_event
        targets = set()
        if iss.get("username"):
            targets.add(iss["username"])
        for c in (iss.get("comments") or [])[:-1]:
            u = c.get("username")
            if u:
                targets.add(u)
        preview = (req.text or "")[:80]
        for tgt in targets:
            if tgt == req.username:
                continue
            emit_event(
                "my_tracker_comment",
                actor=req.username or "",
                target_user=tgt,
                title=f"[이슈 댓글] {iss.get('title') or iss['id']}",
                body=f"{req.username or ''} · {preview}",
                payload={"issue_id": iss["id"], "text": preview,
                         "lot_id": req.lot_id, "wafer_id": req.wafer_id},
            )
    except Exception:
        pass
    return {"ok": True}


@router.post("/comment/reply")
def add_comment_reply(req: CommentReplyReq, request: Request):
    me = current_user(request)
    username = me.get("username") or ""
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(400, "reply text required")
    issues = _load()
    iss = next((i for i in issues if i.get("id") == req.issue_id), None)
    if not iss:
        raise HTTPException(404)
    comments = iss.get("comments") if isinstance(iss.get("comments"), list) else []
    if req.parent_index < 0 or req.parent_index >= len(comments):
        raise HTTPException(404, "parent comment not found")
    now = datetime.datetime.now().isoformat(timespec="seconds")
    reply = {
        "username": username,
        "text": text,
        "timestamp": now,
    }
    parent = dict(comments[req.parent_index] or {})
    replies = list(parent.get("replies") or [])
    replies.append(reply)
    parent["replies"] = replies
    comments[req.parent_index] = parent
    iss["comments"] = comments
    iss["updated_at"] = now
    iss["updated_by"] = username
    try:
        iss["revision"] = int(iss.get("revision") or 0) + 1
    except Exception:
        iss["revision"] = 1
    _save(issues)
    try:
        from core.notify import emit_event
        targets = set()
        if iss.get("username"):
            targets.add(iss["username"])
        if parent.get("username"):
            targets.add(parent["username"])
        for r in parent.get("replies") or []:
            u = r.get("username") if isinstance(r, dict) else ""
            if u:
                targets.add(u)
        preview = text[:80]
        for tgt in targets:
            if tgt == username:
                continue
            emit_event(
                "my_tracker_comment_reply",
                actor=username,
                target_user=tgt,
                title=f"[이슈 대댓글] {iss.get('title') or iss['id']}",
                body=f"{username} · {preview}",
                payload={"issue_id": iss["id"], "parent_index": req.parent_index, "text": preview},
            )
    except Exception:
        pass
    return {"ok": True, "reply": reply}


@router.post("/comment/delete")
def delete_comment(req: CommentDeleteReq, request: Request):
    me = current_user(request)
    username = me.get("username") or ""
    is_admin = me.get("role") == "admin"
    issues = _load()
    iss = next((i for i in issues if i.get("id") == req.issue_id), None)
    if not iss:
        raise HTTPException(404)
    comments = iss.get("comments") if isinstance(iss.get("comments"), list) else []
    if req.comment_index < 0 or req.comment_index >= len(comments):
        raise HTTPException(404, "comment not found")
    raw_parent = comments[req.comment_index]
    parent = dict(raw_parent) if isinstance(raw_parent, dict) else {"text": str(raw_parent or ""), "username": ""}
    target = parent
    if req.reply_index is not None:
        replies = list(parent.get("replies") or [])
        if req.reply_index < 0 or req.reply_index >= len(replies):
            raise HTTPException(404, "reply not found")
        raw_reply = replies[req.reply_index]
        target = dict(raw_reply) if isinstance(raw_reply, dict) else {"text": str(raw_reply or ""), "username": ""}
        owner = target.get("username") or ""
        if not is_admin and owner != username:
            raise HTTPException(403, "작성자 또는 admin 만 삭제 가능")
        del replies[req.reply_index]
        parent["replies"] = replies
        comments[req.comment_index] = parent
    else:
        owner = target.get("username") or ""
        if not is_admin and owner != username:
            raise HTTPException(403, "작성자 또는 admin 만 삭제 가능")
        del comments[req.comment_index]
    now = datetime.datetime.now().isoformat(timespec="seconds")
    iss["comments"] = comments
    iss["updated_at"] = now
    iss["updated_by"] = username
    try:
        iss["revision"] = int(iss.get("revision") or 0) + 1
    except Exception:
        iss["revision"] = 1
    _save(issues)
    return {"ok": True}


@router.post("/lots/bulk")
def bulk_lots(req: LotBulkReq, request: Request):
    me = current_user(request)
    req.username = me.get("username") or ""
    result = TRACKER_SERVICE.add_legacy_lots(
        issue_id=req.issue_id,
        username=req.username,
        rows=req.rows,
    )
    if not result.ok:
        raise HTTPException(404, result.error)
    return {"ok": True, "added": result.data["added"]}


# v8.8.33: Lot step 추적 엔드포인트.
#   - source=fab  : FAB 공정이력에서 최신 step_id
#   - source=et   : ET 측정 패키지 (step_id/step_seq/flat_zone/tkout_time) 역순
#   - source=both|auto : 둘 다
#   - 카테고리가 주어지면 `_load_cats()` 에서 해당 category.source 로 override
@router.get("/lot-step")
def lot_step(request: Request,
             product: str = Query(""), root_lot_id: str = Query(""),
             lot_id: str = Query(""), wafer_id: str = Query(""),
             monitor_prod: str = Query(""),
             source: str = Query("auto"), category: str = Query("")):
    from core.lot_step import lot_step_snapshot, source_root_for_context, _is_root_lot_id
    me = current_user(request)
    if monitor_prod.strip():
        product = monitor_prod.strip()
    # 카테고리 기반 source 결정. Monitor 는 FAB step 전용, Analysis 는 ET 전용.
    source = _category_source(category, source)
    source_root = source_root_for_context(source, category)
    # root_lot_id 가 5자리 영숫자면 root 기준, 아니면 lot_id 로 폴백.
    if root_lot_id and not _is_root_lot_id(root_lot_id):
        # 5자리가 아닌 입력은 lot_id 로 해석
        if not lot_id:
            lot_id = root_lot_id
        root_lot_id = ""
    snap = lot_step_snapshot(product=product, root_lot_id=root_lot_id,
                             lot_id=lot_id, wafer_id=wafer_id, source=source,
                             source_root=source_root)
    return {
        "source": source,
        "source_root": source_root,
        "product": product,
        "monitor_prod": monitor_prod.strip(),
        "root_lot_id": root_lot_id,
        "lot_id": lot_id,
        "wafer_id": wafer_id,
        "snapshot": snap,
    }


@router.post("/lot-check-all")
def lot_check_all(req: LotCheckAllReq, request: Request):
    from routers.groups import filter_by_visibility
    from core.lot_step import (
        check_et_measured,
        expand_lot_row_for_wafer_selection,
        lot_step_snapshot,
        source_root_for_context,
        snapshot_row_fields,
        _is_root_lot_id,
    )

    me = current_user(request)
    issues = filter_by_visibility(_load(), me["username"], me.get("role", "user"), key="group_ids")
    iss = next((i for i in issues if i.get("id") == req.issue_id), None)
    if not iss:
        raise HTTPException(404, "issue not found")

    original_lots = list(iss.get("lots") or [])
    source = _category_source(iss.get("category") or "", "auto")
    source_root = source_root_for_context(source, iss.get("category") or "")
    lots = []
    for lot in original_lots:
        lot = normalize_lot_row(lot)
        root = (lot.get("root_lot_id") or "").strip()
        lid = (lot.get("lot_id") or "").strip()
        wid = str(lot.get("wafer_id") or "").strip()
        monitor_prod = (lot.get("product") or lot.get("monitor_prod") or "").strip()
        product = monitor_prod or (iss.get("product") or "")
        row_root = root
        row_lot = lid
        if row_root and not _is_root_lot_id(row_root):
            if not row_lot:
                row_lot = row_root
            row_root = ""
        lots.extend(
            normalize_lot_row(row)
            for row in expand_lot_row_for_wafer_selection(
                lot,
                product=product,
                root_lot_id=row_root,
                lot_id=row_lot,
                wafer_id=wid,
                source=source,
                source_root=source_root,
            )
        )
    total = len(lots)
    rows = []
    checked_at = datetime.datetime.now().isoformat(timespec="seconds")
    updated_lots = []
    for idx, lot in enumerate(lots):
        lot = normalize_lot_row(lot)
        root = (lot.get("root_lot_id") or "").strip()
        lid = (lot.get("lot_id") or "").strip()
        wid = str(lot.get("wafer_id") or "").strip()
        monitor_prod = (lot.get("product") or lot.get("monitor_prod") or "").strip()
        product = monitor_prod or (iss.get("product") or "")
        row_root = root
        row_lot = lid
        if row_root and not _is_root_lot_id(row_root):
            if not row_lot:
                row_lot = row_root
            row_root = ""
        snap = lot_step_snapshot(
            product=product,
            root_lot_id=row_root,
            lot_id=row_lot,
            wafer_id=wid,
            source=source,
            source_root=source_root,
        )
        row_fields = snapshot_row_fields(snap)
        fab = (snap.get("fab") or {})
        row_product = monitor_prod or product or str(fab.get("product") or "").strip()
        if source == "fab":
            et_fields = {
                "et_measured": None,
                "et_last_seq": None,
                "et_last_time": "",
                "et_last_step": "",
                "et_last_function_step": "",
                "et_step_summary": [],
                "et_step_seq_summary": "",
                "et_recent_formatted": "",
            }
        else:
            et_fields = check_et_measured(
                root_lot_id=row_root,
                lot_id=row_lot,
                wafer_id=wid,
                product=product,
                source_root=source_root,
            )
        current_step = row_fields.get("current_step") or ""
        current_function_step = row_fields.get("current_function_step") or ""
        current_step_seq = row_fields.get("current_step_seq")
        updated_lot = normalize_lot_row({
            **lot,
            "product": row_product,
            "monitor_prod": row_product,
            "current_step": current_step or None,
            "current_function_step": current_function_step or None,
            "function_step": current_function_step or None,
            "func_step": current_function_step or None,
            "current_step_seq": current_step_seq,
            "step_seq": current_step_seq,
            "et_measured": et_fields.get("et_measured"),
            "et_last_seq": et_fields.get("et_last_seq"),
            "et_last_time": et_fields.get("et_last_time"),
            "et_last_step": et_fields.get("et_last_step"),
            "et_last_function_step": et_fields.get("et_last_function_step"),
            "et_step_summary": et_fields.get("et_step_summary") or row_fields.get("et_step_summary") or [],
            "et_step_seq_summary": et_fields.get("et_step_seq_summary") or row_fields.get("et_step_seq_summary") or "",
            "et_recent_formatted": et_fields.get("et_recent_formatted") or row_fields.get("et_recent_formatted") or "",
            "last_move_at": row_fields.get("last_move_at") or "",
            "last_checked_at": checked_at,
            "last_scan_source": source,
            "last_scan_source_root": source_root,
            "last_scan_status": "ok" if snap else "no_match",
        })
        updated_lots.append(updated_lot)
        rows.append({
            "row_index": idx,
            "product": row_product,
            "monitor_prod": row_product,
            "source": source,
            "source_root": source_root,
            "snapshot": snap,
            "current_step": current_step or None,
            "current_function_step": current_function_step or None,
            "function_step": current_function_step or None,
            "func_step": current_function_step or None,
            "current_step_seq": current_step_seq,
            "step_seq": current_step_seq,
            "last_move_at": row_fields.get("last_move_at") or "",
            "last_checked_at": checked_at,
            "last_scan_source": source,
            "last_scan_source_root": source_root,
            "last_scan_status": "ok" if snap else "no_match",
            **et_fields,
            **row_fields,
        })
    if updated_lots != original_lots:
        next_issue = dict(iss)
        next_issue["lots"] = updated_lots
        TRACKER_SERVICE.apply_lot_check_result(
            issue_id=req.issue_id,
            issue_data=next_issue,
            username=me.get("username") or "system",
        )
    return {
        "ok": True,
        "issue_id": req.issue_id,
        "total": total,
        "done": total,
        "lots": updated_lots,
        "rows": rows,
    }


class LotWatchReq(BaseModel):
    issue_id: str
    row_index: int                  # lots[index]
    target_step_id: str = ""        # empty = 진행 변경만 관측 (FAB 모드 전용)
    target_et_step_id: str = ""     # ET 모드: step_id 또는 func_step 필터
    target_et_seqs: str = ""        # ET 모드: comma/space separated seq filter
    source: str = "fab"             # v9.0.0: fab|et 둘 중 하나. both/auto 제거.
    username: str = ""
    mail: bool = False
    mail_group_ids: List[str] = []


class IssueMailReq(BaseModel):
    issue_id: str
    mail: bool = False
    mail_group_ids: List[str] = []


@router.get("/issue-mail")
def get_issue_mail(request: Request, issue_id: str = Query(...)):
    current_user(request)
    iss = _get(issue_id)
    if not iss:
        raise HTTPException(404)
    return {"ok": True, "issue_id": issue_id, "mail_watch": _issue_mail_watch(iss)}


@router.post("/issue-mail")
def save_issue_mail(req: IssueMailReq, request: Request):
    """Save Tracker mail delivery settings at issue level, not lot/wafer row level."""
    me = current_user(request)
    issues = _load()
    iss = next((i for i in issues if i.get("id") == req.issue_id), None)
    if not iss:
        raise HTTPException(404)
    if me.get("role") != "admin" and iss.get("username") != me.get("username"):
        raise HTTPException(403, "Only issue owner or admin can edit mail settings")
    now = datetime.datetime.now().isoformat(timespec="seconds")
    iss["mail_watch"] = {
        "enabled": bool(req.mail),
        "mail_group_ids": [str(x) for x in (req.mail_group_ids or []) if str(x).strip()],
        "updated_by": me.get("username") or "",
        "updated_at": now,
    }
    iss["updated_at"] = now
    iss["updated_by"] = me.get("username") or ""
    _save(issues)
    return {"ok": True, "issue": iss, "mail_watch": iss["mail_watch"]}


@router.post("/lot-watch")
def save_lot_watch(req: LotWatchReq, request: Request):
    """Lot 행에 watch 설정 저장. 이후 `/lot-check` 주기 호출로 fire 판정.
    Lot 행에 'watch' 서브도큐먼트로 저장."""
    me = current_user(request)
    req.username = me.get("username") or ""
    result = TRACKER_SERVICE.save_legacy_lot_watch(
        issue_id=req.issue_id,
        row_index=req.row_index,
        username=req.username,
        target_step_id=req.target_step_id,
        target_et_step_id=req.target_et_step_id,
        target_et_seqs=req.target_et_seqs,
        source=req.source,
        mail=req.mail,
        mail_group_ids=req.mail_group_ids,
    )
    if not result.ok:
        code = 400 if result.error == "row_index out of range" else 404
        raise HTTPException(code, result.error)
    return {"ok": True, "watch": result.data["watch"]}


@router.post("/lot-check")
def check_lot_watches(request: Request, _a=Depends(require_admin)):
    """전체 active watch 를 1회 폴링 — 신규 진입/측정 감지 시 notify + mail.
    주기 스케줄러나 FE 주기 호출로 실행.
    """
    from core.lot_step import (
        compare_to_watch,
        expand_lot_row_for_wafer_selection,
        lot_step_snapshot,
        source_root_for_context,
        _is_root_lot_id,
    )
    from core.notify import emit_event
    from core.tracker_scheduler import _mark_watch_fired, _recipient_payload, _unique_list, scheduler_config
    from core.tracker_templates import render_tracker_mail, tracker_mail_context
    scheduler_cfg = scheduler_config()
    issues = _load()
    fired = []
    changed_issue_ids = set()
    for iss in issues:
        source_for_issue = _category_source(iss.get("category") or "", "auto")
        source_root_for_issue = source_root_for_context(source_for_issue, iss.get("category") or "")
        expanded_lots = []
        for raw_lot in iss.get("lots") or []:
            lot = normalize_lot_row(raw_lot)
            root = (lot.get("root_lot_id") or "").strip()
            lid = (lot.get("lot_id") or "").strip()
            wid = str(lot.get("wafer_id") or "").strip()
            product = (lot.get("product") or lot.get("monitor_prod") or iss.get("product") or "").strip()
            row_root = root
            row_lot = lid
            if row_root and not _is_root_lot_id(row_root):
                if not row_lot:
                    row_lot = row_root
                row_root = ""
            expanded_lots.extend(
                normalize_lot_row(row)
                for row in expand_lot_row_for_wafer_selection(
                    lot,
                    product=product,
                    root_lot_id=row_root,
                    lot_id=row_lot,
                    wafer_id=wid,
                    source=source_for_issue,
                    source_root=source_root_for_issue,
                )
            )
        if expanded_lots != (iss.get("lots") or []):
            iss["lots"] = expanded_lots
            changed_issue_ids.add(iss["id"])
        for i, lot in enumerate(iss.get("lots") or []):
            watch = lot.get("watch") or {}
            if not watch:
                continue
            # watch 가 설정된 lot 만. Monitor 카테고리는 기존 저장 watch 가 ET 여도 FAB 로 강제한다.
            root = (lot.get("root_lot_id") or "").strip()
            lid = (lot.get("lot_id") or "").strip()
            wid = str(lot.get("wafer_id") or "").strip()
            product = (lot.get("product") or lot.get("monitor_prod") or iss.get("product") or "").strip()
            if root and not _is_root_lot_id(root):
                if not lid:
                    lid = root
                root = ""
            resolved_source = _category_source(iss.get("category") or "", watch.get("source") or "auto")
            if resolved_source in ("both", "auto"):
                watch_source = (watch.get("source") or "fab").lower().strip()
                if watch_source not in ("fab", "et"):
                    watch_source = "fab"
            else:
                watch_source = "et" if resolved_source == "et" else "fab"
            source_root = source_root_for_context(resolved_source, iss.get("category") or "")
            effective_watch = {**watch, "source": watch_source}
            if watch.get("source") != watch_source:
                watch["source"] = watch_source
                changed_issue_ids.add(iss["id"])
            snap = lot_step_snapshot(
                product=product,
                root_lot_id=root, lot_id=lid, wafer_id=wid,
                source=resolved_source,
                source_root=source_root,
            )
            checked_at = datetime.datetime.now().isoformat(timespec="seconds")
            cmp = compare_to_watch(
                snap,
                effective_watch,
                now_iso=checked_at,
                et_stable_delay_minutes=scheduler_cfg.get("et_stable_delay_minutes", 180),
            )
            # last_observed 업데이트 (fire 여부와 무관)
            fab_step = ((snap.get("fab") or {}).get("step_id")) or ""
            et_count = int(cmp.get("et_count") or 0)
            watch_updates = cmp.get("watch_updates") if isinstance(cmp.get("watch_updates"), dict) else {}
            if fab_step and fab_step != watch.get("last_observed_step"):
                watch["last_observed_step"] = fab_step
                changed_issue_ids.add(iss["id"])
            if et_count != int(watch.get("last_observed_et_count") or 0):
                watch["last_observed_et_count"] = et_count
                changed_issue_ids.add(iss["id"])
            if watch_updates:
                next_watch = {**watch, **watch_updates}
                if next_watch != watch:
                    watch = next_watch
                    changed_issue_ids.add(iss["id"])
            iss["lots"][i]["watch"] = watch
            if not cmp.get("fire"):
                continue
            # 알림 대상 = 이슈 작성자 + lot 추가자. 메일 수신 설정은 이슈 단위로만 관리한다.
            base_targets = set()
            if iss.get("username"):
                base_targets.add(iss["username"])
            if lot.get("username"):
                base_targets.add(lot["username"])
            mail_watch = _issue_mail_watch(iss)
            recipient_groups = _unique_list(list(mail_watch.get("mail_group_ids") or []))
            notify_recipients = _recipient_payload(base_targets, [])
            targets = notify_recipients.get("users") or []
            body_text = cmp.get("reason") or "lot progress"
            for tgt in targets:
                emit_event(
                    "tracker_step_reached",
                    actor="system",
                    target_user=tgt,
                    title=f"[Lot 진행] {iss.get('title') or iss['id']}",
                    body=f"{body_text} · lot={root or lid} wf={wid}",
                    payload={
                        "issue_id": iss["id"],
                        "product": product,
                        "lot_id": lid, "root_lot_id": root, "wafer_id": wid,
                        "step_id": cmp.get("new_step_id"),
                        "et_count": et_count,
                        "reason": body_text,
                    },
                )
            if mail_watch.get("enabled"):
                try:
                    from core.mail import send_mail
                    mail_recipients = _recipient_payload(base_targets, recipient_groups)
                    mail_targets = mail_recipients.get("users") or []
                    kind = "analysis" if watch_source == "et" else "monitor"
                    context = tracker_mail_context(
                        kind,
                        iss,
                        product=product,
                        lot=root or lid,
                        root_lot_id=root,
                        lot_id=lid,
                        wafer_id=wid,
                        step_id=cmp.get("new_step_id") or fab_step or "",
                        target_step_id=watch.get("target_step_id") or "",
                        recent_et=cmp.get("et_recent_formatted") or "-",
                        et_count=et_count,
                        recipient_groups=", ".join(recipient_groups) or "User only",
                        source=resolved_source,
                        source_root=source_root,
                        checked_at=checked_at,
                    )
                    rendered = render_tracker_mail(
                        kind,
                        context,
                    )
                    send_mail(
                        sender_username="flow-scheduler",
                        receiver_usernames=mail_targets,
                        extra_emails=mail_recipients.get("extra_emails") or [],
                        title=rendered["subject"],
                        content=rendered["body"],
                    )
                except Exception:
                    pass
            if watch_source == "fab" and watch.get("target_step_id"):
                watch = _mark_watch_fired(watch, str(watch.get("target_step_id") or "").strip().upper(), str(cmp.get("new_step_id") or fab_step or ""))
            fired.append({
                "issue_id": iss["id"], "lot": {"root_lot_id": root, "lot_id": lid, "wafer_id": wid},
                "reason": body_text, "step_id": cmp.get("new_step_id"), "et_count": et_count,
            })
            iss["lots"][i]["watch"] = watch
            iss["updated_at"] = datetime.datetime.now().isoformat()
            changed_issue_ids.add(iss["id"])
    if changed_issue_ids:
        for iss in issues:
            if iss.get("id") not in changed_issue_ids:
                continue
            TRACKER_SERVICE.apply_lot_check_result(
                issue_id=iss["id"],
                issue_data=iss,
                username="system",
            )
    return {"ok": True, "fired": fired, "fire_count": len(fired)}


@router.get("/image")
def get_image(name: str = Query(...)):
    # v8.4.6: traversal 방어 — IMG_DIR 밖 파일 다운로드 차단, 디렉터리 컴포넌트 제거
    from pathlib import Path as _P
    safe_name = _P(name).name  # 슬래시·백슬래시·.. 제거
    fp = (IMG_DIR / safe_name).resolve()
    try:
        fp.relative_to(IMG_DIR.resolve())
    except ValueError:
        raise HTTPException(400, "Invalid path")
    if not fp.is_file():
        raise HTTPException(404)
    # MIME 명시로 sniffing 기반 XSS 방어 (L3)
    ext = fp.suffix.lower()
    media = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
    }.get(ext, "application/octet-stream")
    return FileResponse(str(fp), media_type=media,
                        headers={"X-Content-Type-Options": "nosniff"})


@router.post("/delete")
def delete_issue(request: Request, issue_id: str = Query(...)):
    # v8.8.33 보안: 작성자 본인 또는 admin 만 삭제 허용.
    me = current_user(request)
    all_issues = _load()
    target = next((i for i in all_issues if i["id"] == issue_id), None)
    if not target:
        raise HTTPException(404)
    if me.get("role") != "admin" and (target.get("username") or "") != (me.get("username") or ""):
        raise HTTPException(403, "본인 이슈 또는 admin 만 삭제 가능")
    result = TRACKER_SERVICE.delete_legacy_issue(issue_id)
    if not result.ok:
        raise HTTPException(404, result.error)
    return {"ok": True}
