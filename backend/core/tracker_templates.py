from __future__ import annotations

import base64
import html
import mimetypes
import re
from pathlib import Path

MAX_INLINE_ISSUE_IMAGE_BYTES = 2 * 1024 * 1024
RAW_TEMPLATE_VARIABLES = {"issue_detail_html"}

DEFAULT_MAIL_TEMPLATES = {
    "monitor": {
        "subject": "[flow] {role_name} 알림 · {issue_title}",
        "body": (
            "<div style=\"font-family:Arial,sans-serif;color:#111827;line-height:1.6\">"
            "<div style=\"font-size:15px;font-weight:700;margin-bottom:8px\">{issue_title}</div>"
            "<div style=\"display:inline-block;padding:3px 8px;border-radius:4px;background:#dbeafe;color:#1d4ed8;font-size:12px;font-weight:700;margin-bottom:12px\">{role_name}</div>"
            "<div style=\"padding:10px 12px;border-left:4px solid #2563eb;background:#eff6ff;margin-bottom:14px\">{reason}</div>"
            "<table style=\"border-collapse:collapse;width:100%;font-size:13px\">"
            "<tr><td style=\"padding:6px 8px;color:#6b7280;width:120px\">Issue</td><td style=\"padding:6px 8px;font-weight:600\">{issue_id}</td></tr>"
            "<tr><td style=\"padding:6px 8px;color:#6b7280\">Product</td><td style=\"padding:6px 8px\">{product}</td></tr>"
            "<tr><td style=\"padding:6px 8px;color:#6b7280\">Lot / Wafer</td><td style=\"padding:6px 8px\">{lot} / {wafer_id}</td></tr>"
            "<tr><td style=\"padding:6px 8px;color:#6b7280\">Step</td><td style=\"padding:6px 8px;font-family:Consolas,monospace\">{step_id}</td></tr>"
            "<tr><td style=\"padding:6px 8px;color:#6b7280\">Checked</td><td style=\"padding:6px 8px\">{checked_at}</td></tr>"
            "<tr><td style=\"padding:6px 8px;color:#6b7280\">Recipients</td><td style=\"padding:6px 8px\">{recipient_groups}</td></tr>"
            "</table>"
            "{issue_detail_html}"
            "</div>"
        ),
    },
    "analysis": {
        "subject": "[flow] {role_name} 알림 · {issue_title}",
        "body": (
            "<div style=\"font-family:Arial,sans-serif;color:#111827;line-height:1.6\">"
            "<div style=\"font-size:15px;font-weight:700;margin-bottom:8px\">{issue_title}</div>"
            "<div style=\"display:inline-block;padding:3px 8px;border-radius:4px;background:#fce7f3;color:#be185d;font-size:12px;font-weight:700;margin-bottom:12px\">{role_name}</div>"
            "<div style=\"padding:10px 12px;border-left:4px solid #db2777;background:#fdf2f8;margin-bottom:14px\">{reason}</div>"
            "<table style=\"border-collapse:collapse;width:100%;font-size:13px;margin-bottom:12px\">"
            "<tr><td style=\"padding:6px 8px;color:#6b7280;width:120px\">Issue</td><td style=\"padding:6px 8px;font-weight:600\">{issue_id}</td></tr>"
            "<tr><td style=\"padding:6px 8px;color:#6b7280\">Product</td><td style=\"padding:6px 8px\">{product}</td></tr>"
            "<tr><td style=\"padding:6px 8px;color:#6b7280\">Lot / Wafer</td><td style=\"padding:6px 8px\">{lot} / {wafer_id}</td></tr>"
            "<tr><td style=\"padding:6px 8px;color:#6b7280\">ET Step</td><td style=\"padding:6px 8px;font-family:Consolas,monospace\">{step_id}</td></tr>"
            "<tr><td style=\"padding:6px 8px;color:#6b7280\">Checked</td><td style=\"padding:6px 8px\">{checked_at}</td></tr>"
            "</table>"
            "<div style=\"font-size:12px;color:#6b7280;margin-bottom:4px\">Recent ET</div>"
            "<div style=\"padding:9px 10px;background:#f9fafb;border:1px solid #e5e7eb;border-radius:4px;font-family:Consolas,monospace;font-size:12px;margin-bottom:10px\">{recent_et}</div>"
            "<div style=\"font-size:12px;color:#6b7280\">Recipients: {recipient_groups}</div>"
            "{issue_detail_html}"
            "</div>"
        ),
    },
}

TEMPLATE_VARIABLES = [
    "role_name",
    "issue_id",
    "issue_title",
    "category",
    "product",
    "lot",
    "root_lot_id",
    "lot_id",
    "wafer_id",
    "step_id",
    "reason",
    "recent_et",
    "et_count",
    "recipient_groups",
    "source",
    "source_root",
    "checked_at",
    "issue_detail_html",
]


def _settings_file() -> Path:
    from core.paths import PATHS
    return PATHS.data_root / "settings.json"


def _read_settings() -> dict:
    try:
        from core.utils import load_json
        data = load_json(_settings_file(), {})
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def tracker_mail_templates_config() -> dict:
    settings = _read_settings()
    tracker = settings.get("tracker") if isinstance(settings.get("tracker"), dict) else {}
    raw = settings.get("tracker_mail_templates") if isinstance(settings.get("tracker_mail_templates"), dict) else {}
    nested = tracker.get("mail_templates") if isinstance(tracker.get("mail_templates"), dict) else {}
    raw = {**nested, **raw}
    out = {}
    for key in ("monitor", "analysis"):
        src = raw.get(key) if isinstance(raw.get(key), dict) else {}
        default = DEFAULT_MAIL_TEMPLATES[key]
        out[key] = {
            "subject": str(src.get("subject") or default["subject"]),
            "body": str(src.get("body") or default["body"]),
        }
    return out


class _SafeFormatDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def _format_template(template: str, context: dict) -> str:
    safe = _SafeFormatDict({
        k: str(v if v is not None else "") if k in RAW_TEMPLATE_VARIABLES else html.escape(str(v if v is not None else ""))
        for k, v in (context or {}).items()
    })
    try:
        return str(template or "").format_map(safe)
    except Exception:
        return str(template or "")


def short_step_id(value: str = "") -> str:
    """Display FAB step as two letters + six digits when longer internal ids are supplied."""
    text = str(value or "").strip().upper()
    m = re.match(r"^([A-Z]{2})(\d{6})", text)
    return f"{m.group(1)}{m.group(2)}" if m else text


def _tracker_image_dir() -> Path:
    from core.paths import PATHS
    return PATHS.data_root / "tracker" / "images"


def render_issue_detail_html(issue: dict | None, *, max_inline_image_bytes: int = MAX_INLINE_ISSUE_IMAGE_BYTES) -> dict:
    """Render issue description with inline images unless saved images exceed max bytes."""
    desc = str((issue or {}).get("description") or "")
    if not desc.strip():
        return {"html": "", "inline_images": 0, "image_bytes": 0, "omitted_images": False}
    img_names = [m.group(1) for m in re.finditer(r"\[IMG:([^\]]+)\]", desc)]
    image_dir = _tracker_image_dir()
    image_paths = []
    total = 0
    for name in img_names:
        safe_name = Path(name).name
        fp = image_dir / safe_name
        if not fp.is_file():
            continue
        try:
            size = fp.stat().st_size
        except Exception:
            continue
        total += size
        image_paths.append((safe_name, fp, size))
    omit = total > int(max_inline_image_bytes or MAX_INLINE_ISSUE_IMAGE_BYTES)
    inline_count = 0

    def replace_marker(match):
        nonlocal inline_count
        safe_name = Path(match.group(1)).name
        fp = image_dir / safe_name
        if omit or not fp.is_file():
            return ""
        try:
            raw = fp.read_bytes()
        except Exception:
            return ""
        mime = mimetypes.guess_type(safe_name)[0] or "image/png"
        inline_count += 1
        data = base64.b64encode(raw).decode("ascii")
        return (
            f'<img src="data:{mime};base64,{data}" '
            f'style="display:block;max-width:640px;width:auto;height:auto;border:1px solid #e5e7eb;border-radius:6px;margin:10px 0" />'
        )

    body = re.sub(r"\[IMG:([^\]]+)\]", replace_marker, desc)
    if omit:
        body += (
            '<div style="margin-top:12px;padding:10px 12px;background:#fffbeb;border:1px solid #f59e0b;'
            'border-radius:6px;color:#92400e;font-size:12px">'
            '첨부 이미지 용량이 2MB를 초과하여 메일에는 글만 포함했습니다. '
            '상세 내용과 이미지는 go/flow에서 확인해주세요.'
            '</div>'
        )
    return {
        "html": (
            '<div style="margin-top:16px;padding-top:12px;border-top:1px solid #e5e7eb">'
            '<div style="font-size:12px;color:#6b7280;margin-bottom:8px">Issue Detail</div>'
            f'<div style="font-size:13px;color:#111827;line-height:1.7">{body}</div>'
            '</div>'
        ),
        "inline_images": inline_count,
        "image_bytes": total,
        "omitted_images": omit,
    }


def tracker_mail_context(kind: str, issue: dict | None, *, product: str = "", lot: str = "",
                         root_lot_id: str = "", lot_id: str = "", wafer_id: str = "",
                         step_id: str = "", target_step_id: str = "", recent_et: str = "",
                         et_count: int = 0, recipient_groups: str = "", source: str = "",
                         source_root: str = "", checked_at: str = "") -> dict:
    role = "analysis" if str(kind or "").lower().strip() in {"analysis", "et"} else "monitor"
    issue = issue or {}
    detail = render_issue_detail_html(issue)
    step_display = short_step_id(target_step_id or step_id) if role == "monitor" else str(step_id or "").strip()
    if role == "analysis":
        reason = "새로운 ET 측정 정보가 확인되었습니다."
    elif target_step_id:
        reason = f"설정해둔 {step_display} step을 통과했습니다."
    else:
        reason = f"{step_display} step으로 진행되었습니다." if step_display else "FAB step 진행이 확인되었습니다."
    return {
        "issue_id": issue.get("id") or "",
        "issue_title": issue.get("title") or issue.get("id") or "",
        "category": issue.get("category") or "",
        "product": product,
        "lot": lot,
        "root_lot_id": root_lot_id,
        "lot_id": lot_id,
        "wafer_id": wafer_id,
        "step_id": step_display,
        "reason": reason,
        "recent_et": recent_et or "-",
        "et_count": et_count,
        "recipient_groups": recipient_groups or "User only",
        "source": source,
        "source_root": source_root,
        "checked_at": checked_at,
        "issue_detail_html": detail.get("html") or "",
    }


def render_tracker_mail(kind: str, context: dict, *, templates_override: dict | None = None,
                        role_names_override: dict | None = None) -> dict:
    """Render Tracker mail subject/body for Monitor or Analysis."""
    role = "analysis" if str(kind or "").lower().strip() in {"analysis", "et"} else "monitor"
    templates = tracker_mail_templates_config()
    if isinstance(templates_override, dict):
        templates = {
            **templates,
            **{k: v for k, v in templates_override.items() if isinstance(v, dict)},
        }
    tpl = templates.get(role) or DEFAULT_MAIL_TEMPLATES[role]
    ctx = dict(context or {})
    roles = role_names_override if isinstance(role_names_override, dict) else None
    if roles is None:
        try:
            from core.lot_step import tracker_role_names_config
            roles = tracker_role_names_config()
        except Exception:
            roles = {}
    ctx.setdefault("role_name", roles.get(role) or ("Analysis" if role == "analysis" else "Monitor"))
    return {
        "subject": _format_template(tpl.get("subject") or DEFAULT_MAIL_TEMPLATES[role]["subject"], ctx),
        "body": _format_template(tpl.get("body") or DEFAULT_MAIL_TEMPLATES[role]["body"], ctx),
        "kind": role,
    }
