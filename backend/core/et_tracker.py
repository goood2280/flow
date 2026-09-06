"""core/et_tracker.py v1.0.0 — ET Tracker 일일 스캔.

목표 (v9.5.13):
  - 진행중(closed 아님)인 ET source 이슈의 lot 행(root_lot_id/wafer_id)을 하루 n번
    (톱니바퀴에서 HH:MM 시각 목록 지정) 이미 생성된 제품 ET history에서 조회 —
    어떤 step_id 에 PGM(pt) 가 측정되었는지 확인한다. Tracker 자체는 원본 ET DB를
    다시 스캔하지 않는다.
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
  - 메일 on-off/스캔 시각/PGM 필터/링크 주소는 톱니바퀴(settings.json
    `tracker_et_scan`) 에서 설정한다.
  - 수신 그룹은 **이슈마다** 지정할 수 있다 (v9.5.84). 이슈 상세의 '메일 수신
    그룹' 에서 고른 그룹(issue.mail_watch.mail_group_ids)이 있으면 그 이슈는
    그 그룹으로만 발송하고, 비어 있으면 톱니바퀴의 기본 수신 그룹을 쓴다.
    작성자·lot 추가자에게는 어느 경우든 항상 간다.
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
# 증분 조회에서 워터마크 뒤로 되짚어 다시 읽는 일수. ET history 갱신 시 당일분은
# 계속 바뀔 수 있으므로, 마지막 하루는 이미 봤더라도 캐시에서 다시 확인한다.
_SCAN_OVERLAP_DAYS = 1


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
    if src in ("fab", "et", "both"):
        return src
    # v9.5.87: routers.tracker._category_source 와 같은 규칙 — Monitor 역할이
    # 아니면 ET. categories.json 에 없는 legacy 카테고리 이슈가 스캔에서
    # 통째로 빠지던 것을 막는다.
    return "et"


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


def _entry_from_package(pkg: dict, dc_layers: dict[str, str] | None = None) -> dict:
    """et_packages() 1행 → et_history 엔트리. PGM(pt) 라벨은 auto report 와 동일하게
    step_seq(Npt) 형식 (예: AAAH1(20pt))."""
    step_id = str(pkg.get("step_id") or "").strip()
    seq = pkg.get("step_seq")
    seq_text = "" if seq is None else str(seq).strip()
    pt = int(pkg.get("pt_count") or 0)
    time_text = str(pkg.get("time") or "")
    flat = str(pkg.get("flat") or "")
    func = str(pkg.get("function_step") or pkg.get("func_step") or "")
    wafer = str(pkg.get("wafer_id") or "").strip()
    pgm = f"{seq_text}({pt}pt)" if seq_text else f"({pt}pt)"
    return {
        "key": f"{step_id}|{seq_text}|{pt}|{flat}|{time_text}",
        "step_id": step_id,
        "step_seq": seq_text,
        "pt_count": pt,
        "flat": flat,
        "time": time_text,
        "pgm": pgm,
        # wafer_id 는 all/범위 행을 wafer 별로 펼칠 때 이력을 나눠 가지는 기준이다.
        # 없으면 펼침 때마다 이력이 사라져 매번 "신규" 로 다시 잡혔다.
        "wafer_id": wafer,
        "function_step": func,
        "dc_layer": (dc_layers or {}).get(step_id.upper(), ""),
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


def issue_mail_group_ids(iss: dict, cfg: dict) -> list[str]:
    """이 이슈에 적용할 수신 그룹 — 이슈 설정이 있으면 그것만, 없으면 톱니바퀴 기본값.

    v9.5.84: 이슈마다 수신처가 다른 경우가 많아 이슈 단위 설정을 우선한다.
    저장 위치는 기존 `issue.mail_watch.mail_group_ids` 를 그대로 재사용한다."""
    ids: list[str] = []
    try:
        from core.tracker_scheduler import _issue_mail_watch
        watch = _issue_mail_watch(iss if isinstance(iss, dict) else {})
        ids = [str(x).strip() for x in (watch.get("mail_group_ids") or []) if str(x).strip()]
    except Exception as e:
        logger.warning(f"et tracker issue mail groups failed: {e}")
    if ids:
        return ids
    return [str(x).strip() for x in (cfg.get("mail_group_ids") or []) if str(x).strip()]


def _lot_display_root(lot: dict) -> str:
    return str(lot.get("root_lot_id") or lot.get("lot_id") or lot.get("fab_lot_id") or "").strip()


def _scan_scope(product: str, root: str, lot_id: str, wafer_id: str) -> str:
    """워터마크가 유효한 조회 범위. 이 값이 달라지면(제품·랏·wafer 를 고쳤다)
    지난 스캔이 덮은 구간을 더는 신뢰할 수 없으므로 전체를 다시 읽는다."""
    return "|".join(str(v or "").strip().upper() for v in (product, root, lot_id, wafer_id))


def _since_from_watermark(lot: dict, scope: str) -> str:
    """이 lot 행을 이번에 어느 날짜부터 읽을지. 워터마크가 없거나 조회 범위가
    바뀌었으면 '' (=전체 스캔)."""
    if str(lot.get("et_scan_scope") or "") != scope:
        return ""
    try:
        mark = dt.date.fromisoformat(str(lot.get("et_scan_watermark") or "")[:10])
    except Exception:
        return ""
    return (mark - dt.timedelta(days=_SCAN_OVERLAP_DAYS)).isoformat()


def _scan_issue_lots(iss: dict, *, source_root: str, now_iso: str,
                     full: bool = False) -> tuple[list[dict], bool, int]:
    """이슈의 모든 lot 행을 제품 ET history에서 읽어 et_history 를 갱신한다.
    반환: (신규 엔트리 [{lot, entry}], changed, scanned_lot_count).

    기본은 증분이다 — lot 행마다 마지막으로 읽은 ET history 날짜를
    ``et_scan_watermark`` 에 남겨 두고, 다음 조회는 그 하루 전부터만 읽는다.
    ``full=True`` 면 워터마크를 무시하고 캐시 전 구간을 다시 읽는다. 이 함수는
    캐시가 없더라도 원본 ET DB로 폴백하지 않는다."""
    from core.lot_step import (
        et_history_packages,
        et_history_packages_multi,
        parse_wafer_selection,
    )
    from core.tracker_schema import normalize_lot_row
    try:
        from core.dc_layer_mapping import step_to_layer
        dc_layers = step_to_layer()
    except Exception:
        dc_layers = {}
    new_items: list[dict] = []
    changed = False
    scanned = 0
    next_lots: list[dict] = []

    # ── lot 행 전처리 + 제품 ET history 배치 조회 ────────────────────────
    # 같은 (제품, 조회 시작일)을 쓰는 root lot들을 묶어 공유 캐시를 한 번만 읽는다.
    # 원본 ET DB 파일 목록 조회/갱신/폴백은 이 경로에서 절대 수행하지 않는다.
    prepared: list[dict] = []
    for raw_lot in iss.get("lots") or []:
        lot = normalize_lot_row(raw_lot)
        root = str(lot.get("root_lot_id") or "").strip()
        lid = str(lot.get("lot_id") or "").strip()
        wid = str(lot.get("wafer_id") or "").strip()
        product = str(lot.get("product") or lot.get("monitor_prod") or iss.get("product") or "").strip()
        wafer_values = parse_wafer_selection(wid)
        multi_wafer = len(wafer_values) > 1 or wid.lower() in ("all", "전체", "*")
        scope = _scan_scope(product, root, lid, wid)
        prepared.append({
            "lot": lot, "root": root, "lid": lid, "wid": wid, "product": product,
            "multi_wafer": multi_wafer,
            "limit": _HISTORY_CAP * 25 if multi_wafer else _HISTORY_CAP,
            "since_date": "" if full else _since_from_watermark(lot, scope),
            "packages": None, "diag": None,
        })
    batches: dict[tuple, list[dict]] = {}
    for item in prepared:
        if not (item["root"] or item["lid"]):
            continue
        batches.setdefault((item["product"], item["since_date"]), []).append(item)
    for (product, since_date), items in batches.items():
        if len(items) < 2:
            continue          # 단건은 배치 이득이 없다 — 기존 경로 그대로.
        batch_diag: dict = {}
        try:
            specs = [{"root_lot_id": x["root"], "lot_id": x["lid"], "wafer_id": x["wid"]} for x in items]
            results = et_history_packages_multi(
                product,
                specs,
                limit=max(x["limit"] for x in items),
                source_root=source_root,
                since_date=since_date,
                diag=batch_diag,
            )
            if results is None:
                batch_diag.setdefault("error", "ET history scan 결과가 준비되지 않았습니다")
        except Exception as e:
            logger.warning(f"et_history_packages_multi failed issue={iss.get('id')} product={product}: {e}")
            batch_diag["error"] = str(e)
            results = None
        if batch_diag.get("incremental_skip") or batch_diag.get("error"):
            # 새 파일 없음 / 조회 실패는 lot 별로 동일한 결론이다.
            for x in items:
                x["packages"] = [] if batch_diag.get("incremental_skip") else None
                x["diag"] = dict(batch_diag)
            continue
        if len(results) != len(items):
            continue          # 배치 결과 이상 — 아래에서 cache를 lot별로 다시 조회.
        for x, packages in zip(items, results):
            x["packages"] = packages
            x["diag"] = dict(batch_diag)

    for item in prepared:
        lot = item["lot"]
        root, lid, wid = item["root"], item["lid"], item["wid"]
        product = item["product"]
        if not (root or lid):
            next_lots.append(lot)
            continue
        wafer_values = parse_wafer_selection(wid)
        multi_wafer = item["multi_wafer"]
        query_limit = item["limit"]
        since_date = item["since_date"]
        # ET 측정시간 화면과 같은 호환 규칙(root 칸의 값이 fab lot 이거나 history의
        # root_lot_id가 비어 있는 경우)은 cache 조회 함수 안에서 한 번에 처리한다.
        if item["diag"] is not None:
            scan_diag = item["diag"]
            packages = item["packages"]
        else:
            scan_diag = {}
            try:
                packages = et_history_packages(
                    product=product,
                    root_lot_id=root,
                    lot_id=lid,
                    wafer_id=wid,
                    limit=query_limit,
                    source_root=source_root,
                    diag=scan_diag,
                    since_date=since_date,
                )
                if packages is None:
                    scan_diag.setdefault("error", "ET history scan 결과가 준비되지 않았습니다")
            except Exception as e:
                logger.warning(f"et_history_packages failed issue={iss.get('id')} lot={root or lid}: {e}")
                scan_diag["error"] = str(e)
                packages = None
        scan_error = str(scan_diag.get("error") or "")
        if packages is None or scan_error:
            # 조회 자체가 실패한 경우 — "측정 없음" 과 구분해 이유를 남긴다.
            scanned += 1
            lot["last_checked_at"] = now_iso
            lot["last_scan_status"] = "error"
            lot["last_scan_error"] = scan_error or "ET history scan 결과 조회 실패"
            next_lots.append(lot)
            changed = True
            continue

        packages_by_wafer: dict[str, list[dict]] = {}
        for pkg in packages:
            pkg_wafer = str(pkg.get("wafer_id") or "").strip()
            if pkg_wafer:
                packages_by_wafer.setdefault(pkg_wafer, []).append(pkg)
        target_wafers = wafer_values or sorted(
            packages_by_wafer,
            key=lambda value: (0, int(value)) if str(value).isdigit() else (1, str(value)),
        )
        if multi_wafer and target_wafers and (packages_by_wafer or wafer_values):
            scan_rows = []
            for wafer in target_wafers:
                wafer_lot = normalize_lot_row({**lot, "wafer_id": wafer})
                # A range/all row has no meaningful shared history. Each wafer
                # owns its own entries after expansion.
                wafer_lot["et_history"] = [
                    h for h in (lot.get("et_history") or [])
                    if str(h.get("wafer_id") or "").strip() == wafer
                ]
                scan_rows.append((wafer_lot, packages_by_wafer.get(wafer, [])))
        else:
            scan_rows = [(lot, packages)]

        scanned += len(scan_rows)
        for scan_lot, wafer_packages in scan_rows:
            history = [h for h in (scan_lot.get("et_history") or []) if isinstance(h, dict)]
            known = {_entry_key(h) for h in history}
            fresh = []
            for pkg in wafer_packages:
                entry = _entry_from_package(pkg, dc_layers)
                if not entry["step_id"] or entry["key"] in known:
                    continue
                known.add(entry["key"])
                fresh.append({**entry, "detected_at": now_iso})
            if fresh:
                fresh.sort(key=lambda e: str(e.get("time") or ""))
                history = (history + fresh)[-_HISTORY_CAP:]
                new_items.extend({"lot": scan_lot, "entry": e} for e in fresh)
            latest = wafer_packages[0] if wafer_packages else {}
            scan_lot["et_history"] = history
            scan_lot["last_checked_at"] = now_iso
            scan_lot["last_scan_source"] = "et"
            scan_lot["last_scan_cache"] = str(scan_diag.get("cache") or "")
            scan_lot["last_scan_history_built_at"] = str(scan_diag.get("history_built_at") or "")
            # 실제로 읽은 루트를 남긴다 — 설정 폴더가 비어 폴백한 경우를 화면에서 본다.
            scan_lot["last_scan_source_root"] = str(scan_diag.get("source_root") or source_root)
            scan_lot["last_scan_error"] = ""
            # 다음 스캔의 하한. 조회가 성공한 경우에만 옮긴다 — 실패 경로는 위에서
            # 이미 continue 로 빠져 워터마크를 그대로 두고 다음 스캔이 다시 읽는다.
            scan_lot["et_scan_watermark"] = str(
                scan_diag.get("max_file_date") or lot.get("et_scan_watermark") or ""
            )
            scan_lot["et_scan_scope"] = _scan_scope(
                product,
                str(scan_lot.get("root_lot_id") or ""),
                str(scan_lot.get("lot_id") or ""),
                str(scan_lot.get("wafer_id") or ""),
            )
            scan_lot["et_scan_from"] = since_date
            if scan_diag.get("incremental_skip"):
                # 새 파일이 없어 parquet 을 열지 않았다. 직전 스캔의 진단값을
                # 덮어써서 "0 파일 · 측정 없음" 으로 보이게 하지 않는다.
                scan_lot["last_scan_files"] = int(lot.get("last_scan_files") or 0)
                scan_lot["last_scan_status"] = (
                    str(lot.get("last_scan_status") or "")
                    or ("ok" if history else "no_match")
                )
            else:
                scan_lot["last_scan_files"] = int(scan_diag.get("files") or 0)
                scan_lot["last_scan_status"] = "ok" if (wafer_packages or history) else "no_match"
            scan_lot["et_measured"] = bool(history or wafer_packages)
            if latest:
                scan_lot["et_last_seq"] = latest.get("step_seq")
                scan_lot["et_last_time"] = latest.get("time")
                scan_lot["et_last_step"] = latest.get("step_id")
                scan_lot["et_last_function_step"] = latest.get("function_step") or latest.get("func_step") or ""
            next_lots.append(scan_lot)
            changed = True
    if next_lots != (iss.get("lots") or []):
        iss["lots"] = next_lots
        changed = True
    return new_items, changed, scanned


# ─────────────────────────── mail body ───────────────────────────

def _issue_link(cfg: dict, issue_id: str) -> str:
    base = str(cfg.get("app_base_url") or "").strip().rstrip("/")
    if not base:
        try:
            from core.mail import get_share_base_url
            base = get_share_base_url().rstrip("/")
        except Exception:
            base = ""
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
# 제품 ET history 캐시는 별도 ET history scan에서 만든다. Tracker scan phase는
# 공유 캐시 조회 + 이력 diff만 하고, 저장·bell·메일(apply phase)은 api 측에서 한다.

def _slim_lot(lot: dict) -> dict:
    """new_items 직렬화용 최소 lot 정보 (워커 → api 결과 payload)."""
    return {
        "root_lot_id": str((lot or {}).get("root_lot_id") or ""),
        "lot_id": str((lot or {}).get("lot_id") or ""),
        "wafer_id": str((lot or {}).get("wafer_id") or ""),
        "username": str((lot or {}).get("username") or ""),
    }


def scan_phase(only_issue_id: str = "", full: bool = False) -> dict:
    """스캔 phase — 제품 ET history 조회 + et_history diff. 저장/알림/메일 없음.

    별도 ET history scan이 준비한 공유 cache를 root_lot_id 기준으로 바로 읽는다.
    원본 ET DB 갱신이나 raw parquet 폴백은 하지 않는다. issues.json은 쓰지 않으며,
    이슈별 revision 을 함께 기록해 apply 시 동시 수정 가드로 쓴다.

    ``full=True`` 는 lot 행의 워터마크를 무시하고 캐시 전 구간을 다시 읽는다."""
    started_at = _now_iso()
    out = {
        "ok": True,
        "executed_on": "local",
        "started_at": started_at,
        "finished_at": "",
        "full": bool(full),
        "only_issue_id": str(only_issue_id or "").strip(),
        "issues": [],
        "skipped": [],
        "issues_scanned": 0,
        "lots_scanned": 0,
        "new_entries": 0,
        "history_caches": [],
        "last_error": "",
    }
    try:
        from core.paths import PATHS
        from core.utils import load_json
        from core.lot_step import et_history_cache_status, source_root_for_context
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
    reported_history_caches: set[tuple[str, str]] = set()
    for iss in issues:
        if not isinstance(iss, dict):
            continue
        if target and str(iss.get("id") or "") != target:
            continue
        if not target and iss.get("status") == "closed":
            continue
        source = _issue_source(iss, cats_map, roles)
        # 스캔에서 빠진 이유를 남긴다 — 예전에는 조용히 건너뛰어 '스캔했는데
        # 아무 결과도 없다' 로만 보였다 (카테고리 source 미설정이 대표 사례).
        if source not in ("et", "both"):
            out["skipped"].append({
                "issue_id": str(iss.get("id") or ""),
                "reason": (f"카테고리 '{iss.get('category') or '-'}' 는 DB source 가 "
                           f"'{source}' 라 ET 스캔 대상이 아닙니다 "
                           f"(FAB 진행 추적용 — 톱니바퀴 → 카테고리에서 ET/both 로 바꾸면 스캔합니다)"),
            })
            continue
        if not (iss.get("lots") or []):
            out["skipped"].append({
                "issue_id": str(iss.get("id") or ""),
                "reason": "랏리스트가 비어 있어 스캔할 lot 이 없습니다",
            })
            continue
        source_root = source_root_for_context("et", iss.get("category") or "")
        products = list(dict.fromkeys(
            str(lot.get("product") or lot.get("monitor_prod") or iss.get("product") or "").strip()
            for lot in (iss.get("lots") or [])
            if isinstance(lot, dict)
            and str(lot.get("product") or lot.get("monitor_prod") or iss.get("product") or "").strip()
        ))
        for product in products:
            cache_key = (product.upper(), str(source_root or "").upper())
            if cache_key in reported_history_caches:
                continue
            reported_history_caches.add(cache_key)
            cache_result = et_history_cache_status(product, source_root=source_root)
            ready = bool(cache_result.get("ready"))
            out["history_caches"].append({
                "product": product,
                "ok": ready,
                "ready": ready,
                "mode": cache_result.get("mode", ""),
                "skipped": True,
                "row_count": int(cache_result.get("row_count") or 0),
                "scanned_file_count": 0,
                "max_file_date": cache_result.get("max_file_date", ""),
                "built_at": cache_result.get("built_at", ""),
                "error": "" if ready else "ET history scan 결과가 준비되지 않았습니다",
            })
        now_iso = _now_iso()
        new_items, issue_changed, scanned = _scan_issue_lots(
            iss, source_root=source_root, now_iso=now_iso, full=bool(full),
        )
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
    try:
        from core.worker_dispatch import server_role
        if server_role() == "worker":
            logger.info("skip ET Tracker notification/mail on worker role")
            return
    except Exception:
        pass
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
        recipients = _recipient_payload(base_targets, issue_mail_group_ids(iss, cfg))
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
        "skipped": [s for s in (result.get("skipped") or []) if isinstance(s, dict)],
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


def _dispatch_scan(only_issue_id: str, *, timeout_sec: float, full: bool = False) -> dict:
    """공유 ET history 캐시를 API 서버에서 바로 읽는다.

    이 단계는 더 이상 원본 DB 스캔을 하지 않으므로 개발 워커 큐를 왕복하지 않는다.
    ``timeout_sec`` 인자는 기존 호출 계약 호환을 위해 유지한다."""
    _ = timeout_sec
    result = scan_phase(only_issue_id=only_issue_id, full=full)
    result["executed_on"] = "local"
    return result


def run_scan(*, force: bool = False, actor: str = "scheduler", full: bool = False) -> dict:
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
        result = _dispatch_scan("", timeout_sec=1800.0, full=full)
        summary = _apply_scan_result(result, cfg, notify=True, actor=actor)
    except Exception as e:
        logger.warning(f"et tracker scan failed: {e}")
        summary = {"ok": False, "started_at": _now_iso(), "finished_at": _now_iso(),
                   "issues_scanned": 0, "lots_scanned": 0, "new_entries": 0,
                   "notify_count": 0, "mail_count": 0, "last_error": str(e)}
    finally:
        _scan_lock.release()
    return _write_status({**summary, "running": False})


def scan_issue(issue_id: str, *, actor: str = "manual", full: bool = False) -> dict:
    """단일 이슈 이력 갱신 (알림/메일 없음) — 이슈 상세의 '즉시 스캔' 버튼용."""
    if not _scan_lock.acquire(blocking=False):
        return {"ok": False, "last_error": "scan already running", "issue": None}
    try:
        result = _dispatch_scan(str(issue_id or "").strip(), timeout_sec=420.0, full=full)
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
            from core.background_owner import is_owner
            if not is_owner():
                time.sleep(5)
                continue
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
