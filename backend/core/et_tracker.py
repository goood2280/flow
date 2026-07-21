"""core/et_tracker.py v1.0.0 — ET Tracker 일일 스캔.

목표 (v9.5.13):
  - 진행중(closed 아님)인 ET source 이슈의 lot 행(root_lot_id/wafer_id)을 하루 n번
    (톱니바퀴에서 HH:MM 시각 목록 지정) ET DB 에서 스캔 — 어떤 step_id 에
    PGM(pt) 가 측정되었는지 확인한다.
  - lot 행마다 `et_history` 에 측정 이력을 누적한다. 이전 스캔에서 확인된
    이력은 그대로 남고, 새 스캔에서는 **추가된 것만** append 된다
    (key = step_id|step_seq|pt|flat|time).
  - 신규 측정이 감지되면 bell 알림 + (톱니바퀴에서 메일 ON 시) 메일 발송.
    PGM 필터(예: "H1")가 설정되어 있으면 PGM(pt) 라벨에 해당 문자열이 포함된
    측정만 알림/메일 대상이 된다 (이력 저장은 필터와 무관하게 전부).
  - 메일: 제목 "[ET Tracker]<이슈제목>", 본문 = 신규 측정 요약 + 링크 + 설명 +
    랏리스트(root_lot_id/wafer_id/목적/참고/측정이력/작성자/날짜).
    본문은 2MB 이하로 이미지 축소(Pillow 가용 시)해 맞추고, 축소로도 안 되면
    이미지를 생략하고 "본문 첨부가 너무 커서 링크에서 확인" 안내로 대체한다.
  - 수신 그룹/메일 on-off/스캔 시각/PGM 필터/링크 주소는 모두 톱니바퀴
    (settings.json `tracker_et_scan`) 에서 한 번에 설정 — 이슈 단위 메일 설정 없음.
"""
from __future__ import annotations

import base64
import datetime as dt
import html as _html
import logging
import mimetypes
import os
import re
import threading
import time

logger = logging.getLogger("flow.et_tracker")

_scheduler_thread: threading.Thread | None = None
_scheduler_started = False
_scan_lock = threading.Lock()

MAX_MAIL_BODY_BYTES = 2 * 1024 * 1024          # core.mail CONTENT_MAX 와 동일
_MAIL_BODY_MARGIN = 64 * 1024                  # 인코딩/래핑 여유
_HISTORY_CAP = 500                             # lot 행당 이력 상한
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")


def _now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _tracker_dir():
    from core.paths import PATHS
    return PATHS.data_root / "tracker"


def _settings_file():
    from core.paths import PATHS
    return PATHS.data_root / "settings.json"


def _status_file():
    return _tracker_dir() / "et_scan_status.json"


# ─────────────────────────── config ───────────────────────────

def _normalize_times(values) -> list[str]:
    out: list[str] = []
    for v in values or []:
        text = str(v or "").strip()
        if not text:
            continue
        if len(text) == 4 and text[1] == ":":  # 8:00 → 08:00
            text = "0" + text
        if _TIME_RE.match(text) and text not in out:
            out.append(text)
    return sorted(out)


def _normalize_tokens(values) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for v in values or []:
        text = str(v or "").strip()
        if not text:
            continue
        key = text.upper()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def et_tracker_config() -> dict:
    from core.utils import load_json
    settings = load_json(_settings_file(), {})
    if not isinstance(settings, dict):
        settings = {}
    raw = settings.get("tracker_et_scan")
    if not isinstance(raw, dict):
        tracker = settings.get("tracker") if isinstance(settings.get("tracker"), dict) else {}
        raw = tracker.get("et_scan") if isinstance(tracker.get("et_scan"), dict) else {}
    return {
        "enabled": bool(raw.get("enabled", True)),
        "scan_times": _normalize_times(raw.get("scan_times")),
        "mail_enabled": bool(raw.get("mail_enabled")),
        "mail_group_ids": [str(x) for x in (raw.get("mail_group_ids") or []) if str(x).strip()],
        "pgm_filters": _normalize_tokens(raw.get("pgm_filters")),
        "app_base_url": str(raw.get("app_base_url") or "").strip(),
    }


def save_et_tracker_config(*, enabled: bool, scan_times, mail_enabled: bool,
                           mail_group_ids, pgm_filters, app_base_url: str) -> dict:
    from core.utils import load_json, save_json
    settings = load_json(_settings_file(), {})
    if not isinstance(settings, dict):
        settings = {}
    cfg = {
        "enabled": bool(enabled),
        "scan_times": _normalize_times(scan_times),
        "mail_enabled": bool(mail_enabled),
        "mail_group_ids": [str(x) for x in (mail_group_ids or []) if str(x).strip()],
        "pgm_filters": _normalize_tokens(pgm_filters),
        "app_base_url": str(app_base_url or "").strip().rstrip("/"),
    }
    settings["tracker_et_scan"] = dict(cfg)
    tracker = settings.get("tracker") if isinstance(settings.get("tracker"), dict) else {}
    settings["tracker"] = {**tracker, "et_scan": dict(cfg)}
    save_json(_settings_file(), settings, indent=2)
    return et_tracker_config()


# ─────────────────────────── status ───────────────────────────

def _read_status() -> dict:
    from core.utils import load_json
    status = load_json(_status_file(), {})
    return status if isinstance(status, dict) else {}


def _write_status(payload: dict) -> dict:
    """스캔 결과를 저장하되 스케줄 슬롯 기록(ran_slots)은 보존한다."""
    try:
        from core.utils import save_json
        current = _read_status()
        merged = {**current, **(payload or {})}
        merged["running"] = _scan_lock.locked() if "running" not in (payload or {}) else bool(payload["running"])
        save_json(_status_file(), merged, indent=2)
        return merged
    except Exception as e:
        logger.warning(f"et tracker status save failed: {e}")
        return payload or {}


def et_scan_status() -> dict:
    status = _read_status()
    status.pop("ran_slots", None)
    return {**et_tracker_config(), "status": {**status, "running": _scan_lock.locked()}}


# ─────────────────────────── issue helpers ───────────────────────────

def _issue_source(issue: dict, cats_map: dict, roles: dict) -> str:
    name = str(issue.get("category") or "").strip()
    low = name.lower()
    if low == str(roles.get("monitor") or "Monitor").strip().lower():
        return "fab"
    if low == str(roles.get("analysis") or "Analysis").strip().lower():
        return "et"
    meta = cats_map.get(name) or {}
    src = str(meta.get("source") or "").lower().strip()
    return src if src in ("fab", "et", "both") else "auto"


def _load_cats_map() -> dict:
    from core.utils import load_json
    try:
        raw = load_json(_tracker_dir() / "categories.json", [])
    except Exception:
        raw = []
    out = {}
    for c in raw if isinstance(raw, list) else []:
        if isinstance(c, dict) and c.get("name"):
            out[c["name"]] = c
    return out


def _role_names() -> dict:
    try:
        from core.lot_step import tracker_role_names_config
        return tracker_role_names_config()
    except Exception:
        return {"monitor": "Monitor", "analysis": "Analysis"}


def _entry_from_package(pkg: dict) -> dict:
    """et_packages() 1행 → et_history 엔트리. PGM(pt) 라벨은 auto report 와 동일하게
    step_seq(Npt) 형식 (예: AAAH1(20pt))."""
    step_id = str(pkg.get("step_id") or "").strip()
    seq = pkg.get("step_seq")
    seq_text = "" if seq is None else str(seq).strip()
    pt = int(pkg.get("pt_count") or 0)
    time_text = str(pkg.get("time") or "")
    flat = str(pkg.get("flat") or "")
    func = str(pkg.get("function_step") or pkg.get("func_step") or "")
    pgm = f"{seq_text}({pt}pt)" if seq_text else f"({pt}pt)"
    return {
        "key": f"{step_id}|{seq_text}|{pt}|{flat}|{time_text}",
        "step_id": step_id,
        "step_seq": seq_text,
        "pt_count": pt,
        "flat": flat,
        "time": time_text,
        "pgm": pgm,
        "function_step": func,
    }


def _entry_key(entry: dict) -> str:
    key = str(entry.get("key") or "").strip()
    if key:
        return key
    return "|".join([
        str(entry.get("step_id") or ""),
        str(entry.get("step_seq") or ""),
        str(entry.get("pt_count") or 0),
        str(entry.get("flat") or ""),
        str(entry.get("time") or ""),
    ])


def _pgm_matches(entry: dict, filters: list[str]) -> bool:
    """톱니바퀴 PGM 필터 — PGM(pt) 라벨/step_seq 부분일치 (대소문자 무시). 비어있으면 전부."""
    if not filters:
        return True
    pgm = str(entry.get("pgm") or "").upper()
    seq = str(entry.get("step_seq") or "").upper()
    return any(str(f or "").upper() in pgm or str(f or "").upper() in seq for f in filters)


def _lot_display_root(lot: dict) -> str:
    return str(lot.get("root_lot_id") or lot.get("lot_id") or lot.get("fab_lot_id") or "").strip()


def _scan_issue_lots(iss: dict, *, source_root: str, now_iso: str) -> tuple[list[dict], bool, int]:
    """이슈의 모든 lot 행을 ET DB 에서 스캔해 et_history 를 갱신한다.
    반환: (신규 엔트리 [{lot, entry}], changed, scanned_lot_count)."""
    from core.lot_step import et_packages, _is_root_lot_id
    from core.tracker_schema import normalize_lot_row
    new_items: list[dict] = []
    changed = False
    scanned = 0
    next_lots: list[dict] = []
    for raw_lot in iss.get("lots") or []:
        lot = normalize_lot_row(raw_lot)
        root = str(lot.get("root_lot_id") or "").strip()
        lid = str(lot.get("lot_id") or "").strip()
        if root and not _is_root_lot_id(root):
            if not lid:
                lid = root
            root = ""
        wid = str(lot.get("wafer_id") or "").strip()
        product = str(lot.get("product") or lot.get("monitor_prod") or iss.get("product") or "").strip()
        if not (root or lid):
            next_lots.append(lot)
            continue
        scanned += 1
        try:
            packages = et_packages(
                product=product,
                root_lot_id=root,
                lot_id=lid,
                wafer_id=wid,
                limit=_HISTORY_CAP,
                source_root=source_root,
            )
        except Exception as e:
            logger.warning(f"et_packages failed issue={iss.get('id')} lot={root or lid}: {e}")
            packages = None
        if packages is None:
            lot["last_checked_at"] = now_iso
            lot["last_scan_status"] = "error"
            next_lots.append(lot)
            changed = True
            continue
        history = [h for h in (lot.get("et_history") or []) if isinstance(h, dict)]
        known = {_entry_key(h) for h in history}
        fresh = []
        for pkg in packages:
            entry = _entry_from_package(pkg)
            if not entry["step_id"] or entry["key"] in known:
                continue
            known.add(entry["key"])
            fresh.append({**entry, "detected_at": now_iso})
        if fresh:
            fresh.sort(key=lambda e: str(e.get("time") or ""))
            history = (history + fresh)[-_HISTORY_CAP:]
            new_items.extend({"lot": lot, "entry": e} for e in fresh)
        latest = packages[0] if packages else {}
        lot["et_history"] = history
        lot["last_checked_at"] = now_iso
        lot["last_scan_source"] = "et"
        lot["last_scan_source_root"] = source_root
        lot["last_scan_status"] = "ok" if (packages or history) else "no_match"
        lot["et_measured"] = bool(history or packages)
        if latest:
            lot["et_last_seq"] = latest.get("step_seq")
            lot["et_last_time"] = latest.get("time")
            lot["et_last_step"] = latest.get("step_id")
            lot["et_last_function_step"] = latest.get("function_step") or latest.get("func_step") or ""
        next_lots.append(lot)
        changed = True
    if next_lots != (iss.get("lots") or []):
        iss["lots"] = next_lots
        changed = True
    return new_items, changed, scanned


# ─────────────────────────── mail body ───────────────────────────

def _issue_link(cfg: dict, issue_id: str) -> str:
    base = str(cfg.get("app_base_url") or "").strip().rstrip("/")
    if not base:
        return ""
    return f"{base}/tracker?issue_id={issue_id}"


def _history_cell_html(lot: dict, *, max_steps: int = 8) -> str:
    """측정이력 셀 — step_id 별로 PGM(pt) 를 묶어 한 줄씩."""
    history = [h for h in (lot.get("et_history") or []) if isinstance(h, dict)]
    if not history:
        return '<span style="color:#9ca3af">측정 이력 없음</span>'
    grouped: dict[str, dict] = {}
    for h in history:
        step = str(h.get("step_id") or "-")
        g = grouped.setdefault(step, {"func": "", "pgms": [], "last": ""})
        func = str(h.get("function_step") or "")
        if func and not g["func"]:
            g["func"] = func
        pgm = str(h.get("pgm") or "")
        if pgm and pgm not in g["pgms"]:
            g["pgms"].append(pgm)
        t = str(h.get("time") or "")
        if t > g["last"]:
            g["last"] = t
    rows = sorted(grouped.items(), key=lambda kv: kv[1]["last"], reverse=True)
    lines = []
    for step, g in rows[:max_steps]:
        label = f"{step}({g['func']})" if g["func"] else step
        lines.append(
            f'<div style="margin:1px 0"><b style="font-family:Consolas,monospace">{_html.escape(label)}</b> '
            f'<span style="font-family:Consolas,monospace">{_html.escape(", ".join(g["pgms"]))}</span></div>'
        )
    if len(rows) > max_steps:
        lines.append(f'<div style="color:#6b7280">외 {len(rows) - max_steps}개 step</div>')
    return "".join(lines)


def _lot_list_table_html(iss: dict) -> str:
    """랏리스트 — root_lot_id / wafer_id / 목적 / 참고 / 측정이력 / 작성자 / 날짜."""
    th = ('style="padding:6px 8px;background:#f3f4f6;border:1px solid #e5e7eb;'
          'font-size:12px;color:#374151;text-align:left;white-space:nowrap"')
    td = 'style="padding:6px 8px;border:1px solid #e5e7eb;font-size:12px;vertical-align:top"'
    tdm = ('style="padding:6px 8px;border:1px solid #e5e7eb;font-size:12px;'
           'vertical-align:top;font-family:Consolas,monospace;white-space:nowrap"')
    rows_html = []
    for lot in iss.get("lots") or []:
        if not isinstance(lot, dict):
            continue
        rows_html.append(
            "<tr>"
            f"<td {tdm}>{_html.escape(_lot_display_root(lot) or '-')}</td>"
            f"<td {tdm}>{_html.escape(str(lot.get('wafer_id') or lot.get('wafer_label') or '-'))}</td>"
            f"<td {td}>{_html.escape(str(lot.get('purpose') or ''))}</td>"
            f"<td {td}>{_html.escape(str(lot.get('comment') or ''))}</td>"
            f"<td {td}>{_history_cell_html(lot)}</td>"
            f"<td {td}>{_html.escape(str(lot.get('username') or ''))}</td>"
            f"<td {tdm}>{_html.escape(str(lot.get('added') or '')[:10])}</td>"
            "</tr>"
        )
    if not rows_html:
        rows_html.append(f'<tr><td colspan="7" {td}>등록된 lot 없음</td></tr>')
    return (
        '<table style="border-collapse:collapse;width:100%;margin-top:6px">'
        f"<tr>"
        f"<th {th}>root_lot_id</th><th {th}>wafer_id</th><th {th}>목적</th><th {th}>참고</th>"
        f"<th {th}>측정이력 (step_id · PGM(pt))</th><th {th}>작성자</th><th {th}>날짜</th>"
        f"</tr>"
        + "".join(rows_html)
        + "</table>"
    )


def _new_entries_table_html(new_items: list[dict]) -> str:
    th = ('style="padding:5px 8px;background:#eff6ff;border:1px solid #bfdbfe;'
          'font-size:12px;color:#1e40af;text-align:left;white-space:nowrap"')
    td = ('style="padding:5px 8px;border:1px solid #dbeafe;font-size:12px;'
          'font-family:Consolas,monospace;white-space:nowrap"')
    rows = []
    for item in new_items[:60]:
        lot = item.get("lot") or {}
        e = item.get("entry") or {}
        step = str(e.get("step_id") or "-")
        func = str(e.get("function_step") or "")
        rows.append(
            "<tr>"
            f"<td {td}>{_html.escape(_lot_display_root(lot) or '-')}</td>"
            f"<td {td}>{_html.escape(str(lot.get('wafer_id') or '-'))}</td>"
            f"<td {td}>{_html.escape(f'{step}({func})' if func else step)}</td>"
            f"<td {td}>{_html.escape(str(e.get('pgm') or '-'))}</td>"
            f"<td {td}>{_html.escape(str(e.get('time') or '')[:19])}</td>"
            "</tr>"
        )
    more = ""
    if len(new_items) > 60:
        more = f'<div style="font-size:12px;color:#6b7280;margin-top:4px">외 {len(new_items) - 60}건</div>'
    return (
        '<table style="border-collapse:collapse;width:100%">'
        f"<tr><th {th}>root_lot_id</th><th {th}>wafer_id</th><th {th}>step_id</th>"
        f"<th {th}>PGM(pt)</th><th {th}>측정시간</th></tr>"
        + "".join(rows)
        + "</table>"
        + more
    )


def _compress_image_bytes(raw: bytes, max_width: int, quality: int) -> bytes | None:
    """Pillow 가용 시 JPEG 재인코딩. 불가/실패 시 None."""
    try:
        import io
        from PIL import Image
    except Exception:
        return None
    try:
        img = Image.open(io.BytesIO(raw))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        if img.width > max_width:
            ratio = max_width / float(img.width)
            img = img.resize((max_width, max(1, int(img.height * ratio))))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        return buf.getvalue()
    except Exception:
        return None


def _description_html(issue: dict, budget_bytes: int) -> tuple[str, bool]:
    """설명 HTML — [IMG:name] 마커를 인라인 data URI 로 치환.
    이미지 총량이 예산을 넘으면 축소를 시도하고, 그래도 넘치면 이미지를 생략한다.
    반환: (html, images_omitted)."""
    desc = str(issue.get("description") or "").strip()
    if not desc:
        return "", False
    from core.paths import PATHS
    image_dir = PATHS.data_root / "tracker" / "images"
    names = [m.group(1) for m in re.finditer(r"\[IMG:([^\]]+)\]", desc)]
    files: dict[str, bytes] = {}
    for name in names:
        from pathlib import Path as _P
        safe = _P(name).name
        fp = image_dir / safe
        if fp.is_file() and safe not in files:
            try:
                files[safe] = fp.read_bytes()
            except Exception:
                continue

    def _b64_len(data: dict[str, bytes]) -> int:
        return sum((len(v) * 4 // 3) + 128 for v in data.values())

    omitted = False
    final: dict[str, bytes] = dict(files)
    mimes: dict[str, str] = {n: (mimetypes.guess_type(n)[0] or "image/png") for n in files}
    if files and _b64_len(files) > max(0, budget_bytes):
        # 압축 사다리 — Pillow 없으면 바로 생략 폴백.
        fitted = None
        for max_width, quality in ((900, 75), (700, 60), (520, 45)):
            candidate: dict[str, bytes] = {}
            ok = True
            for n, raw in files.items():
                comp = _compress_image_bytes(raw, max_width, quality)
                if comp is None:
                    ok = False
                    break
                candidate[n] = comp
            if not ok:
                break
            if _b64_len(candidate) <= max(0, budget_bytes):
                fitted = candidate
                break
        if fitted is not None:
            final = fitted
            mimes = {n: "image/jpeg" for n in fitted}
        else:
            # 압축률이 과해 이미지가 제대로 안 보이는 수준 → 생략하고 링크 안내.
            final = {}
            omitted = True

    def replace_marker(m):
        from pathlib import Path as _P
        safe = _P(m.group(1)).name
        raw = final.get(safe)
        if raw is None:
            return ""
        data = base64.b64encode(raw).decode("ascii")
        return (
            f'<img src="data:{mimes.get(safe, "image/png")};base64,{data}" '
            f'style="display:block;max-width:640px;width:auto;height:auto;'
            f'border:1px solid #e5e7eb;border-radius:6px;margin:8px 0" />'
        )

    body = re.sub(r"\[IMG:([^\]]+)\]", replace_marker, desc)
    return body, omitted


def build_et_tracker_mail(iss: dict, new_items: list[dict], cfg: dict) -> dict:
    """제목 [ET Tracker]<제목> + 본문(신규 측정 / 링크 / 설명 / 랏리스트), 2MB 이내."""
    title = str(iss.get("title") or iss.get("id") or "").strip()
    subject = f"[ET Tracker]{title}"
    link = _issue_link(cfg, str(iss.get("id") or ""))
    link_html = (
        f'<div style="margin:10px 0"><a href="{_html.escape(link)}" '
        f'style="color:#2563eb;font-weight:700">▶ flow 에서 이슈 열기</a> '
        f'<span style="color:#6b7280;font-size:12px">({_html.escape(link)})</span></div>'
    ) if link else ""
    header = (
        '<div style="font-family:Arial,sans-serif;color:#111827;line-height:1.6">'
        f'<div style="font-size:16px;font-weight:700;margin-bottom:4px">{_html.escape(title)}</div>'
        '<div style="display:inline-block;padding:3px 8px;border-radius:4px;background:#dbeafe;'
        'color:#1d4ed8;font-size:12px;font-weight:700;margin-bottom:10px">ET Tracker</div>'
        f'<div style="padding:9px 12px;border-left:4px solid #2563eb;background:#eff6ff;margin-bottom:10px">'
        f'신규 ET 측정 {len(new_items)}건이 감지되었습니다.</div>'
        + _new_entries_table_html(new_items)
        + link_html
    )
    lot_section = (
        '<div style="font-size:13px;font-weight:700;margin-top:14px">랏리스트</div>'
        + _lot_list_table_html(iss)
        + "</div>"
    )
    desc_title = '<div style="font-size:13px;font-weight:700;margin-top:14px">설명</div>'
    base_len = len((header + desc_title + lot_section).encode("utf-8"))
    budget = MAX_MAIL_BODY_BYTES - _MAIL_BODY_MARGIN - base_len
    desc_html, images_omitted = _description_html(iss, budget)
    if images_omitted:
        desc_html += (
            '<div style="margin-top:10px;padding:9px 12px;background:#fffbeb;border:1px solid #f59e0b;'
            'border-radius:6px;color:#92400e;font-size:12px">'
            '본문 첨부(이미지)가 너무 커서 메일에 담지 못했습니다. '
            '위 링크로 들어와서 원본 이미지를 확인해주세요.</div>'
        )
    desc_section = (desc_title
                    + f'<div style="font-size:13px;line-height:1.7">{desc_html}</div>') if desc_html else ""
    body = header + desc_section + lot_section
    # 최후 방어 — 그래도 2MB 를 넘으면 설명을 통째로 생략.
    if len(body.encode("utf-8")) > MAX_MAIL_BODY_BYTES - _MAIL_BODY_MARGIN:
        notice = (
            '<div style="margin-top:10px;padding:9px 12px;background:#fffbeb;border:1px solid #f59e0b;'
            'border-radius:6px;color:#92400e;font-size:12px">'
            '본문 첨부가 너무 커서 설명을 생략했습니다. 링크로 들어와서 확인해주세요.</div>'
        )
        body = header + notice + lot_section
        images_omitted = True
    return {"subject": subject, "body": body, "images_omitted": images_omitted}


# ─────────────────────────── scan (phase 분리) ───────────────────────────
# v9.5.14: 양산(api) 서버에서 스케줄이 돌되, 개발 워커가 살아있으면 무거운
#   스캔 phase(ET DB 조회 + 이력 diff)를 worker_dispatch.run_heavy 로 워커에
#   우선 위임한다. 저장·bell·메일(apply phase)은 항상 api 측 — worker 역할은
#   외부 서비스(메일)가 차단되어 있고, issues.json 쓰기 경쟁도 한 곳으로 모은다.

def _slim_lot(lot: dict) -> dict:
    """new_items 직렬화용 최소 lot 정보 (워커 → api 결과 payload)."""
    return {
        "root_lot_id": str((lot or {}).get("root_lot_id") or ""),
        "lot_id": str((lot or {}).get("lot_id") or ""),
        "wafer_id": str((lot or {}).get("wafer_id") or ""),
        "username": str((lot or {}).get("username") or ""),
    }


def scan_phase(only_issue_id: str = "") -> dict:
    """스캔 phase — ET DB 조회 + et_history diff 만 수행. 저장/알림/메일 없음.

    워커·로컬 어디서 실행해도 결과가 같도록 부수효과 없이 JSON 직렬화 가능한
    dict 를 반환한다. 이슈별 revision 을 함께 기록해 apply 시 동시 수정 가드로 쓴다."""
    started_at = _now_iso()
    out = {
        "ok": True,
        "executed_on": "local",
        "started_at": started_at,
        "finished_at": "",
        "only_issue_id": str(only_issue_id or "").strip(),
        "issues": [],
        "issues_scanned": 0,
        "lots_scanned": 0,
        "new_entries": 0,
        "last_error": "",
    }
    try:
        from core.paths import PATHS
        from core.utils import load_json
        from core.lot_step import source_root_for_context
    except Exception as e:
        out.update({"ok": False, "finished_at": _now_iso(), "last_error": f"import failed: {e}"})
        return out
    issues_fp = PATHS.data_root / "tracker" / "issues.json"
    if not issues_fp.is_file():
        out["finished_at"] = _now_iso()
        return out
    issues = load_json(issues_fp, [])
    if not isinstance(issues, list):
        out.update({"ok": False, "finished_at": _now_iso(), "last_error": "issues.json is not a list"})
        return out
    cats_map = _load_cats_map()
    roles = _role_names()
    target = out["only_issue_id"]
    for iss in issues:
        if not isinstance(iss, dict):
            continue
        if target and str(iss.get("id") or "") != target:
            continue
        if not target and iss.get("status") == "closed":
            continue
        source = _issue_source(iss, cats_map, roles)
        if source not in ("et", "both"):
            continue
        if not (iss.get("lots") or []):
            continue
        source_root = source_root_for_context("et", iss.get("category") or "")
        now_iso = _now_iso()
        new_items, issue_changed, scanned = _scan_issue_lots(iss, source_root=source_root, now_iso=now_iso)
        out["issues_scanned"] += 1
        out["lots_scanned"] += scanned
        out["new_entries"] += len(new_items)
        out["issues"].append({
            "issue_id": str(iss.get("id") or ""),
            "revision": int(iss.get("revision") or 0),
            "changed": bool(issue_changed),
            "checked_at": now_iso,
            "lots": iss.get("lots") or [],
            "new_items": [
                {"lot": _slim_lot(x.get("lot")), "entry": dict(x.get("entry") or {})}
                for x in new_items
            ],
        })
    out["finished_at"] = _now_iso()
    return out


def _notify_issue(iss: dict, matched: list[dict], cfg: dict, *, actor: str, summary: dict) -> None:
    """신규 측정 감지 이슈 1건에 대한 bell + (설정 시) 메일 발송 — api 측 전용."""
    base_targets = set()
    if iss.get("username"):
        base_targets.add(iss["username"])
    for x in matched:
        u = (x.get("lot") or {}).get("username")
        if u:
            base_targets.add(u)
    preview = ", ".join(
        f"{((x.get('entry') or {}).get('step_id') or '-')}·{(x.get('entry') or {}).get('pgm') or ''}"
        for x in matched[:4]
    )
    if len(matched) > 4:
        preview += f" 외 {len(matched) - 4}건"
    try:
        from core.notify import emit_event
        from core.tracker_scheduler import _recipient_payload
        notify_targets = _recipient_payload(base_targets, []).get("users") or []
        for tgt in notify_targets:
            if emit_event(
                "et_tracker_new",
                actor=actor,
                target_user=tgt,
                title=f"[ET Tracker] {iss.get('title') or iss['id']}",
                body=f"신규 ET 측정 {len(matched)}건 · {preview}",
                payload={
                    "issue_id": iss.get("id") or "",
                    "count": len(matched),
                    "entries": [
                        {
                            "root_lot_id": _lot_display_root(x.get("lot") or {}),
                            "wafer_id": (x.get("lot") or {}).get("wafer_id") or "",
                            "step_id": (x.get("entry") or {}).get("step_id") or "",
                            "pgm": (x.get("entry") or {}).get("pgm") or "",
                            "time": (x.get("entry") or {}).get("time") or "",
                        }
                        for x in matched[:20]
                    ],
                },
            ):
                summary["notify_count"] += 1
    except Exception as e:
        logger.warning(f"et tracker notify failed: {e}")
    if not cfg["mail_enabled"]:
        return
    try:
        from core.mail import send_mail
        from core.tracker_scheduler import _recipient_payload
        recipients = _recipient_payload(base_targets, cfg["mail_group_ids"])
        mail_targets = recipients.get("users") or []
        extra_emails = recipients.get("extra_emails") or []
        if not mail_targets and not extra_emails:
            return
        rendered = build_et_tracker_mail(iss, matched, cfg)
        res = send_mail(
            sender_username="flow-et-tracker",
            receiver_usernames=mail_targets,
            extra_emails=extra_emails,
            title=rendered["subject"],
            content=rendered["body"],
        )
        if res.get("ok"):
            summary["mail_count"] += len(res.get("to") or [])
        elif res.get("reason"):
            summary["last_error"] = str(res.get("reason") or "")[:300]
    except Exception as e:
        logger.warning(f"et tracker mail failed: {e}")
        summary["last_error"] = f"mail: {e}"


def _apply_scan_result(result: dict, cfg: dict, *, notify: bool, actor: str) -> dict:
    """apply phase — 스캔 결과를 issues.json 에 반영하고 알림/메일 발송 (api 측).

    스캔 중 이슈가 수정된 경우(revision 불일치)는 그 이슈 결과를 버린다 —
    et_history diff 는 멱등이라 다음 스캔이 최신 lots 기준으로 다시 잡는다."""
    result = result if isinstance(result, dict) else {}
    summary = {
        "ok": bool(result.get("ok", False)),
        "executed_on": str(result.get("executed_on") or "local"),
        "started_at": result.get("started_at") or _now_iso(),
        "finished_at": "",
        "actor": actor,
        "issues_scanned": int(result.get("issues_scanned") or 0),
        "lots_scanned": int(result.get("lots_scanned") or 0),
        "new_entries": int(result.get("new_entries") or 0),
        "skipped_stale": 0,
        "notify_count": 0,
        "mail_count": 0,
        "last_error": str(result.get("last_error") or ""),
    }
    rows = [r for r in (result.get("issues") or []) if isinstance(r, dict)]
    target_id = str(result.get("only_issue_id") or "").strip()
    try:
        from core.paths import PATHS
        from core.utils import load_json, save_json
    except Exception as e:
        summary.update({"ok": False, "finished_at": _now_iso(), "last_error": f"import failed: {e}"})
        return summary
    issues_fp = PATHS.data_root / "tracker" / "issues.json"
    issues = load_json(issues_fp, [])
    if not isinstance(issues, list):
        issues = []
    by_id = {str(i.get("id") or ""): i for i in issues if isinstance(i, dict)}
    changed = False
    notify_batches: list[tuple[dict, list[dict]]] = []
    for row in rows:
        iss = by_id.get(str(row.get("issue_id") or ""))
        if iss is None:
            continue
        if int(iss.get("revision") or 0) != int(row.get("revision") or 0):
            summary["skipped_stale"] += 1
            continue
        if row.get("changed"):
            iss["lots"] = row.get("lots") or []
            iss["updated_at"] = row.get("checked_at") or _now_iso()
            iss["updated_by"] = actor
            iss["revision"] = int(iss.get("revision") or 0) + 1
            changed = True
        new_items = [x for x in (row.get("new_items") or []) if isinstance(x, dict)]
        if new_items and notify:
            notify_batches.append((iss, new_items))
    if changed:
        try:
            save_json(issues_fp, issues, indent=2)
        except Exception as e:
            summary.update({"ok": False, "last_error": f"save issues failed: {e}"})
    for iss, new_items in notify_batches:
        matched = [x for x in new_items if _pgm_matches(x.get("entry") or {}, cfg["pgm_filters"])]
        if matched:
            _notify_issue(iss, matched, cfg, actor=actor, summary=summary)
    summary["finished_at"] = _now_iso()
    if target_id:
        summary["issue"] = by_id.get(target_id)
    return summary


def _dispatch_scan(only_issue_id: str, *, timeout_sec: float) -> dict:
    """스캔 phase 실행 위치 결정 — 개발 워커가 켜져 있으면 워커에 우선 위임.

    run_heavy: api 역할 + 워커 생존/여유 → 큐잉 후 결과 대기, 그 외(워커 다운·
    과부하·타임아웃·standalone)는 로컬 scan_phase 폴백 — 기능 동일, 실행 위치만 바뀐다."""
    local = lambda: scan_phase(only_issue_id=only_issue_id)
    try:
        from core import worker_dispatch as _wd
        result = _wd.run_heavy(
            "et_tracker_scan",
            {"only_issue_id": only_issue_id},
            local,
            timeout_sec=timeout_sec,
            label="et_tracker_scan",
        )
    except Exception as e:
        logger.warning(f"et tracker dispatch failed ({e}) — running locally")
        result = local()
    return result if isinstance(result, dict) else {"ok": False, "last_error": "scan returned no result"}


def run_scan(*, force: bool = False, actor: str = "scheduler") -> dict:
    cfg = et_tracker_config()
    if not force and (not cfg["enabled"] or not cfg["scan_times"]):
        return _write_status({
            "ok": True, "started_at": "", "finished_at": _now_iso(),
            "issues_scanned": 0, "lots_scanned": 0, "new_entries": 0,
            "notify_count": 0, "mail_count": 0,
            "last_error": "" if cfg["enabled"] else "disabled",
        })
    if not _scan_lock.acquire(blocking=False):
        return {**_read_status(), "ok": False, "running": True, "last_error": "scan already running"}
    try:
        result = _dispatch_scan("", timeout_sec=1800.0)
        summary = _apply_scan_result(result, cfg, notify=True, actor=actor)
    except Exception as e:
        logger.warning(f"et tracker scan failed: {e}")
        summary = {"ok": False, "started_at": _now_iso(), "finished_at": _now_iso(),
                   "issues_scanned": 0, "lots_scanned": 0, "new_entries": 0,
                   "notify_count": 0, "mail_count": 0, "last_error": str(e)}
    finally:
        _scan_lock.release()
    return _write_status({**summary, "running": False})


def scan_issue(issue_id: str, *, actor: str = "manual") -> dict:
    """단일 이슈 이력 갱신 (알림/메일 없음) — 이슈 상세의 '즉시 스캔' 버튼용."""
    if not _scan_lock.acquire(blocking=False):
        return {"ok": False, "last_error": "scan already running", "issue": None}
    try:
        result = _dispatch_scan(str(issue_id or "").strip(), timeout_sec=420.0)
        return _apply_scan_result(result, et_tracker_config(), notify=False, actor=actor)
    finally:
        _scan_lock.release()


# ─────────────────────────── scheduler ───────────────────────────

def _tick_due_slots(now: dt.datetime, cfg: dict) -> list[str]:
    day = now.strftime("%Y-%m-%d")
    status = _read_status()
    ran_map = status.get("ran_slots") if isinstance(status.get("ran_slots"), dict) else {}
    ran_today = [str(t) for t in (ran_map.get(day) or [])]
    hhmm = now.strftime("%H:%M")
    return [t for t in cfg["scan_times"] if t <= hhmm and t not in ran_today]


def _mark_slots_ran(now: dt.datetime, slots: list[str]) -> None:
    day = now.strftime("%Y-%m-%d")
    status = _read_status()
    ran_map = status.get("ran_slots") if isinstance(status.get("ran_slots"), dict) else {}
    ran_today = sorted(set([str(t) for t in (ran_map.get(day) or [])] + list(slots)))
    _write_status({"ran_slots": {day: ran_today}})


def _scheduler_loop():
    time.sleep(45)
    while True:
        try:
            # v9.5.14: 스케줄 트리거는 양산(api)·standalone 만 — worker 역할은
            #   worker_dispatch 큐로 스캔 phase 만 위임받아 실행한다. 역할은
            #   재시작 없이 바뀔 수 있으므로 tick 마다 확인.
            try:
                from core.worker_dispatch import external_services_enabled
                trigger_allowed = external_services_enabled()
            except Exception:
                trigger_allowed = True
            cfg = et_tracker_config()
            if trigger_allowed and cfg["enabled"] and cfg["scan_times"]:
                now = dt.datetime.now()
                due = _tick_due_slots(now, cfg)
                if due:
                    run_scan(force=False, actor="scheduler")
                    _mark_slots_ran(now, due)
        except Exception as e:
            logger.warning(f"et tracker scheduler tick failed: {e}")
        time.sleep(60)


def start_scheduler() -> bool:
    global _scheduler_thread, _scheduler_started
    if _scheduler_started:
        return False
    if os.environ.get("FLOW_DISABLE_ET_TRACKER_SCHED") == "1":
        logger.info("et tracker scheduler disabled via FLOW_DISABLE_ET_TRACKER_SCHED=1")
        return False
    try:
        from core.worker_dispatch import server_role
        if server_role() == "worker":
            logger.info("et tracker scheduler not started on worker role (scan jobs arrive via worker_dispatch)")
            return False
    except Exception:
        pass
    t = threading.Thread(target=_scheduler_loop, name="et-tracker-scheduler", daemon=True)
    t.start()
    _scheduler_thread = t
    _scheduler_started = True
    logger.info("et tracker scheduler started (daily time slots, scan phase offloads to worker when alive)")
    return True
