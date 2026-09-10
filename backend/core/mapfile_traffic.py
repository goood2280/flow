"""Mapfile traffic light verification engine for TEG 위치조회.

Discovers mapfiles matching product_code from roots.get_db_root() / 'mapfile',
performs verification with core.teg_check.inspect, and caches results by file signature
(mtime + size) so verification only runs when files change.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from pathlib import Path
import re
from typing import Any

from core.file_transaction import file_transaction
from core.paths import PATHS
import core.roots as roots
import core.teg_check as _tc
import core.teg_map as _tm
from core.utils import load_json, save_json

logger = logging.getLogger("flow.mapfile_traffic")

MAPFILE_DIR_NAME = "mapfile"
CACHE_FILE_NAME = "mapfile_traffic_cache.json"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _directory_has_mapfiles(path: Path) -> bool:
    """Treat every non-temporary regular file as text mapfile input."""
    try:
        if not path.is_dir():
            return False
        for entry in path.iterdir():
            if entry.is_file() and not entry.name.startswith((".", "~", "$")):
                return True
            if entry.is_dir() and entry.name.lower() in ("dev", "prod"):
                try:
                    if any(
                        sub.is_file() and not sub.name.startswith((".", "~", "$"))
                        for sub in entry.iterdir()
                    ):
                        return True
                except OSError:
                    pass
        return False
    except OSError:
        return False


def get_mapfile_dir() -> Path:
    """Return the mapfile directory inside DB root, with fallback to B/mapfile candidates."""
    db_path = roots.get_db_root() / MAPFILE_DIR_NAME
    if _directory_has_mapfiles(db_path):
        return db_path
    for candidate in [
        db_path,
        _PROJECT_ROOT / "B" / MAPFILE_DIR_NAME,
        _PROJECT_ROOT.parent / "B" / MAPFILE_DIR_NAME,
    ]:
        if _directory_has_mapfiles(candidate):
            return candidate
    return db_path


def get_traffic_cache_path() -> Path:
    """Return path to cached verification results."""
    return PATHS.data_root / "teg_map" / CACHE_FILE_NAME


def load_traffic_cache() -> dict[str, Any]:
    path = get_traffic_cache_path()
    with file_transaction(path):
        if not path.is_file():
            return {}
        try:
            data = load_json(path, {})
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            logger.warning("Failed to load mapfile traffic cache: %s", exc)
            return {}


def save_traffic_cache(cache: dict[str, Any]) -> None:
    path = get_traffic_cache_path()
    with file_transaction(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            save_json(path, cache, indent=2)
        except Exception as exc:
            logger.warning("Failed to save mapfile traffic cache: %s", exc)


def get_product_code_for_vehicle(vehicle: str) -> str:
    """Get configured product_code for a vehicle."""
    v_clean = str(vehicle or "").strip()
    if not v_clean:
        return ""
    cfg = _tm.load_cfg()
    product_codes = cfg.get("product_codes") or {}
    for k, v in product_codes.items():
        if str(k).strip().casefold() == v_clean.casefold():
            return str(v or "").strip()
    # Check catalog fallback
    for row in _tm.product_catalog():
        if row.get("vehicle", "").strip().casefold() == v_clean.casefold():
            return str(row.get("product_code") or "").strip()
    return ""


def list_mapfiles_for_product(vehicle: str) -> tuple[str, list[Path]]:
    """List mapfiles matching the vehicle's product_code from dev, prod, and root directories.

    Returns (product_code, matching_paths).
    """
    code = get_product_code_for_vehicle(vehicle)
    dir_path = get_mapfile_dir()
    if not dir_path.is_dir():
        return code, []

    matched: list[Path] = []
    prefix = code.strip().casefold() if code.strip() else ""
    veh_prefix = vehicle.strip().casefold()

    def _matches(name: str) -> bool:
        ename = name.casefold()
        if prefix and ename.startswith(prefix):
            return True
        if not prefix and veh_prefix and ename.startswith(veh_prefix):
            return True
        return False

    # 1. Subdirectories dev and prod (개발 DC, 양산DC)
    has_dc_subdirs = False
    for sub in ("dev", "prod"):
        sub_path = dir_path / sub
        if sub_path.is_dir():
            has_dc_subdirs = True
            try:
                for entry in sorted(sub_path.iterdir(), key=lambda p: p.name.casefold()):
                    if not entry.is_file() or entry.name.startswith((".", "~", "$")):
                        continue
                    if _matches(entry.name):
                        matched.append(entry)
            except OSError as exc:
                logger.warning("Error reading mapfile subdirectory %s: %s", sub_path, exc)

    # 2. Root directory (only legacy fallback when neither dev nor prod exists)
    if not has_dc_subdirs:
        try:
            for entry in sorted(dir_path.iterdir(), key=lambda p: p.name.casefold()):
                if not entry.is_file() or entry.name.startswith((".", "~", "$")):
                    continue
                if _matches(entry.name):
                    matched.append(entry)
        except OSError as exc:
            logger.warning("Error reading mapfile directory %s: %s", dir_path, exc)

    return code, matched


def file_signature(path: Path) -> str:
    """Content version survives GitHub downloads that only change timestamps."""
    try:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return f"{path.name}:sha256:{digest.hexdigest()}"
    except OSError:
        return f"{path.name}:0:0"


def determine_traffic_light(summary: dict[str, int], targets: dict[str, Any], has_error: bool) -> str:
    """Determine overall traffic light color: red | yellow | green | gray."""
    if has_error:
        return "gray"

    red = int(summary.get("red") or summary.get("mismatch") or 0)
    orange = int(summary.get("orange") or 0)
    yellow = int(summary.get("yellow") or summary.get("warning") or 0)
    green = int(summary.get("green") or summary.get("match") or 0)

    # Missing target verification
    missing_targets = int(targets.get("missing") or 0)

    if red > 0:
        return "red"
    if orange > 0 or yellow > 0 or missing_targets > 0:
        return "yellow"
    if green > 0:
        return "green"
    return "gray"


def verify_single_mapfile(vehicle: str, file_path: Path, dc_type: str = "", dc_label: str = "") -> dict[str, Any]:
    """Inspect a single mapfile using core.teg_check.inspect."""
    filename = file_path.name
    mapfile_dir = get_mapfile_dir()
    try:
        rel_path = file_path.relative_to(mapfile_dir).as_posix()
    except Exception:
        rel_path = filename

    if not dc_type:
        pname = file_path.parent.name.lower()
        if pname == "dev":
            dc_type = "dev"
            dc_label = "개발 DC"
        elif pname == "prod":
            dc_type = "prod"
            dc_label = "양산DC"
        else:
            dc_type = "root"
            dc_label = "기타"

    try:
        st = file_path.stat()
        mtime_str = dt.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        file_size = st.st_size
    except OSError:
        mtime_str = ""
        file_size = 0

    sig = file_signature(file_path)

    try:
        raw = file_path.read_bytes()
        sig = f"{filename}:sha256:{hashlib.sha256(raw).hexdigest()}"
        try:
            content = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            content = raw.decode("cp949", errors="replace")

        inspect_res = _tc.inspect(vehicle, content, flat=None)
        flat_info = inspect_res.get("flat") or {}
        teg_info = inspect_res.get("teg") or {}
        summary_raw = teg_info.get("summary") or {}
        targets_raw = teg_info.get("targets") or {}
        rows_raw = teg_info.get("rows") or []
        if not isinstance(rows_raw, list):
            rows_raw = []
        main_rows_raw = teg_info.get("main_rows") or []
        if not isinstance(main_rows_raw, list):
            main_rows_raw = []
        main_groups_in = teg_info.get("main_groups") or []
        if isinstance(main_groups_in, dict):
            main_groups_raw = list(main_groups_in.values())
        elif isinstance(main_groups_in, list):
            main_groups_raw = main_groups_in
        else:
            main_groups_raw = []
        main_purpose_warnings_raw = teg_info.get("main_purpose_warnings") or []
        if not isinstance(main_purpose_warnings_raw, list):
            main_purpose_warnings_raw = []

        # 1. S/L (Scribe Lane) Traffic Light & Issues
        sl_red = sum(1 for r in rows_raw if r.get("light") == "red")
        sl_orange = sum(1 for r in rows_raw if r.get("light") == "orange")
        sl_yellow = sum(1 for r in rows_raw if r.get("light") == "yellow")
        sl_green = sum(1 for r in rows_raw if r.get("light") == "green")
        sl_gray = sum(1 for r in rows_raw if r.get("light") in ("gray", "dim"))
        sl_missing_targets = int(targets_raw.get("missing") or 0)

        sl_issues = []
        for r in rows_raw:
            light = r.get("light")
            if light in ("red", "orange", "yellow"):
                sl_issues.append({
                    "category": "S/L",
                    "light": light,
                    "teg_name": r.get("name") or "",
                    "ref_teg": r.get("ref_teg") or "",
                    "status": r.get("status") or "",
                    "reason": r.get("reason") or r.get("light_reason") or "",
                    "delta": r.get("delta"),
                })
        for item in targets_raw.get("items") or []:
            if not item.get("matched"):
                sl_issues.append({
                    "category": "S/L 필수대상",
                    "light": "yellow",
                    "teg_name": item.get("target") or "",
                    "ref_teg": "-",
                    "status": "누락",
                    "reason": "Mapfile에 등록되지 않은 필수 대상 TEG",
                    "delta": None,
                })

        if sl_red > 0:
            sl_light = "red"
        elif sl_orange > 0 or sl_yellow > 0 or sl_missing_targets > 0:
            sl_light = "yellow"
        elif sl_green > 0:
            sl_light = "green"
        elif len(rows_raw) == 0 and sl_missing_targets == 0:
            sl_light = "none"
        else:
            sl_light = "gray"

        # 2. Main (Die / Block) Traffic Light & Issues
        main_red = sum(1 for r in main_rows_raw if r.get("light") == "red") + sum(int(g.get("red") or 0) for g in main_groups_raw)
        main_orange = sum(1 for r in main_rows_raw if r.get("light") == "orange") + sum(int(g.get("orange") or 0) for g in main_groups_raw)
        main_yellow = sum(1 for r in main_rows_raw if r.get("light") == "yellow") + sum(int(g.get("yellow") or 0) for g in main_groups_raw) + len(main_purpose_warnings_raw)
        main_green = sum(1 for r in main_rows_raw if r.get("light") == "green")
        main_gray = sum(1 for r in main_rows_raw if r.get("light") in ("gray", "dim"))

        main_issues = []
        for r in main_rows_raw:
            light = r.get("light")
            if light in ("red", "orange", "yellow"):
                main_issues.append({
                    "category": "Main",
                    "light": light,
                    "teg_name": r.get("name") or "",
                    "ref_teg": r.get("main_group") or "MAIN",
                    "status": r.get("status") or "",
                    "reason": r.get("reason") or r.get("light_reason") or "",
                    "delta": r.get("delta"),
                })
        for g in main_groups_raw:
            for t in g.get("tegs") or []:
                if t.get("light") in ("red", "orange", "yellow"):
                    main_issues.append({
                        "category": f"Main ({g.get('group')})",
                        "light": t.get("light"),
                        "teg_name": t.get("teg") or t.get("name") or "",
                        "ref_teg": g.get("group") or "MAIN",
                        "status": "die/purpose",
                        "reason": ("purpose 배치 금지 위반" if t.get("purpose_forbidden") else "die 경계 밖 또는 침범"),
                        "delta": None,
                    })
        for w in main_purpose_warnings_raw:
            main_issues.append({
                "category": "Main Purpose",
                "light": "yellow",
                "teg_name": f"{w.get('group')} ({w.get('teg_count', 0)}개)",
                "ref_teg": w.get("group") or "MAIN",
                "status": "purpose 주의",
                "reason": f"purpose={w.get('purpose')}: {w.get('reason')}",
                "delta": None,
            })

        if main_red > 0:
            main_light = "red"
        elif main_orange > 0 or main_yellow > 0:
            main_light = "yellow"
        elif main_green > 0 or (len(main_groups_raw) > 0 and main_red == 0 and main_yellow == 0 and main_orange == 0):
            main_light = "green"
        elif len(main_rows_raw) == 0 and len(main_groups_raw) == 0:
            main_light = "none"
        else:
            main_light = "gray"

        # 3. Overall Traffic Light
        if sl_light == "red" or main_light == "red":
            traffic_light = "red"
        elif sl_light == "yellow" or main_light == "yellow":
            traffic_light = "yellow"
        elif sl_light == "green" or main_light == "green":
            traffic_light = "green"
        else:
            traffic_light = "gray"

        sl_info = {
            "light": sl_light,
            "green": sl_green,
            "orange": sl_orange,
            "yellow": sl_yellow,
            "red": sl_red,
            "gray": sl_gray,
            "total": len(rows_raw),
            "missing_targets": sl_missing_targets,
            "matched_targets": int(targets_raw.get("matched") or 0),
            "total_targets": int(targets_raw.get("total") or 0),
            "issues": sl_issues,
        }

        main_info = {
            "light": main_light,
            "green": main_green,
            "orange": main_orange,
            "yellow": main_yellow,
            "red": main_red,
            "gray": main_gray,
            "total": len(main_rows_raw) + sum(len(g.get("tegs") or []) for g in main_groups_raw),
            "group_count": len(main_groups_raw),
            "groups": main_groups_raw,
            "purpose_warnings": main_purpose_warnings_raw,
            "issues": main_issues,
        }

        summary = {
            "red": sl_red + main_red,
            "orange": sl_orange + main_orange,
            "yellow": sl_yellow + main_yellow,
            "green": sl_green + main_green,
            "gray": sl_gray + main_gray,
            "total": len(rows_raw) + len(main_rows_raw),
            "mismatch": summary_raw.get("mismatch", 0),
            "warning": summary_raw.get("warning", 0),
            "match": summary_raw.get("match", 0),
        }

        targets = {
            "matched": targets_raw.get("matched", 0),
            "missing": targets_raw.get("missing", 0),
            "total": targets_raw.get("total", 0),
            "items": targets_raw.get("items", []),
        }

        all_issues = sl_issues + main_issues

        return {
            "signature": sig,
            "filename": filename,
            "rel_path": rel_path,
            "dc_type": dc_type,
            "dc_label": dc_label,
            "size": file_size,
            "mtime": mtime_str,
            "verified_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "ok",
            "traffic_light": traffic_light,
            "overall_light": traffic_light,
            "flat_detected": flat_info.get("detected") or "auto",
            "sl": sl_info,
            "main": main_info,
            "summary": summary,
            "targets": targets,
            "issues": all_issues,
            "error": "",
        }
    except Exception as exc:
        logger.exception("Failed to inspect mapfile %s: %s", filename, exc)
        return {
            "signature": sig,
            "filename": filename,
            "rel_path": rel_path,
            "dc_type": dc_type,
            "dc_label": dc_label,
            "size": file_size,
            "mtime": mtime_str,
            "verified_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "error",
            "traffic_light": "gray",
            "overall_light": "gray",
            "flat_detected": "-",
            "sl": {"light": "gray", "green": 0, "yellow": 0, "red": 0, "gray": 0, "total": 0, "missing_targets": 0, "issues": []},
            "main": {"light": "gray", "green": 0, "yellow": 0, "red": 0, "gray": 0, "total": 0, "group_count": 0, "issues": []},
            "summary": {"red": 0, "orange": 0, "yellow": 0, "green": 0, "gray": 0, "total": 0},
            "targets": {"matched": 0, "missing": 0, "total": 0, "items": []},
            "issues": [],
            "error": str(exc),
        }


def get_github_download_status(dir_path: Path | None = None, now: dt.datetime | None = None) -> dict[str, Any]:
    """Inspect DB/mapfile download log to determine if GitHub periodic download ran within 1 day.

    Supports:
    - download.log (text or JSON)
    - download_log.json
    - sync.log
    - download.json

    Traffic light rules:
    - Status in_progress / updating: light="blue", status_label="업데이트중", is_updating=True
    - Status failed / error: light="red", status_label="다운로드 실패", is_updating=False
    - Elapsed <= 24 hours: light="green", status_label="최신 (N시간 전)" or "최신 (N분 전)", within_one_day=True
    - Elapsed > 24 hours: light="yellow", status_label="업데이트 지연 (N일 경과)", within_one_day=False
    - No log found: light="gray", status_label="로그 없음", within_one_day=False, has_log=False
    """
    map_dir = dir_path or get_mapfile_dir()
    cur_now = now or dt.datetime.now()

    if not map_dir or not map_dir.is_dir():
        return {
            "has_log": False,
            "log_file": "",
            "log_path": "",
            "status": "no_log",
            "status_label": "로그 없음",
            "light": "gray",
            "timestamp": "",
            "elapsed_hours": None,
            "within_one_day": False,
            "is_updating": False,
            "message": "DB mapfile 폴더를 찾을 수 없습니다.",
            "raw_text": "",
        }

    log_candidates = [
        "download.log",
        "download_log.json",
        "sync.log",
        "download.json",
    ]
    found_log: Path | None = None
    for cand in log_candidates:
        cand_path = map_dir / cand
        if cand_path.is_file():
            found_log = cand_path
            break

    if not found_log:
        try:
            for entry in map_dir.iterdir():
                if entry.is_file() and entry.name.lower() in log_candidates:
                    found_log = entry
                    break
        except OSError:
            pass

    if not found_log:
        return {
            "has_log": False,
            "log_file": "",
            "log_path": "",
            "status": "no_log",
            "status_label": "로그 없음",
            "light": "gray",
            "timestamp": "",
            "elapsed_hours": None,
            "within_one_day": False,
            "is_updating": False,
            "message": "DB/mapfile에 download log 파일이 없습니다.",
            "raw_text": "",
        }

    raw_text = ""
    for enc in ("utf-8-sig", "cp949", "utf-8"):
        try:
            raw_text = found_log.read_text(encoding=enc)
            break
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            logger.warning("Failed to read log file %s: %s", found_log, exc)
            break

    try:
        file_mtime = dt.datetime.fromtimestamp(found_log.stat().st_mtime)
    except OSError:
        file_mtime = cur_now

    parsed_dt: dt.datetime | None = None
    status_str: str = ""
    message_str: str = ""

    # Try JSON parsing
    if found_log.suffix.lower() == ".json" or raw_text.strip().startswith("{"):
        try:
            data = json.loads(raw_text)
            if isinstance(data, dict):
                t_val = data.get("timestamp") or data.get("time") or data.get("datetime")
                if t_val:
                    try:
                        parsed_dt = dt.datetime.fromisoformat(str(t_val))
                    except Exception:
                        pass
                status_str = str(data.get("status") or "").strip().lower()
                message_str = str(data.get("message") or data.get("msg") or "").strip()
        except Exception:
            pass

    # If not parsed as JSON, parse lines
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    last_line = lines[-1] if lines else ""

    if parsed_dt is None:
        dt_pattern = re.compile(r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)")
        for line in reversed(lines):
            m = dt_pattern.search(line)
            if m:
                dt_str = m.group(1).replace(" ", "T")
                try:
                    parsed_dt = dt.datetime.fromisoformat(dt_str)
                    break
                except Exception:
                    pass

    if not status_str:
        text_to_check = "\n".join(lines[-5:]) if lines else raw_text
        if re.search(r"(?i)\b(in_progress|updating|running|syncing|downloading)\b", text_to_check):
            status_str = "in_progress"
        elif re.search(r"(?i)\b(failed|fail|error|fatal)\b", text_to_check):
            status_str = "failed"
        elif re.search(r"(?i)\b(success|ok|done|finished|complete|completed)\b", text_to_check):
            status_str = "success"
        else:
            status_str = "success"

    if not message_str and last_line:
        message_str = last_line

    if parsed_dt is None:
        parsed_dt = file_mtime

    dt_for_diff = parsed_dt
    if dt_for_diff.tzinfo is not None and cur_now.tzinfo is None:
        cur_now_cmp = cur_now.astimezone()
    elif dt_for_diff.tzinfo is None and cur_now.tzinfo is not None:
        dt_for_diff = dt_for_diff.astimezone(cur_now.tzinfo)
        cur_now_cmp = cur_now
    else:
        cur_now_cmp = cur_now

    elapsed_seconds = (cur_now_cmp - dt_for_diff).total_seconds()
    elapsed_hours = max(0.0, elapsed_seconds / 3600.0)
    within_one_day = elapsed_seconds <= 86400.0

    if status_str in ("in_progress", "updating", "running", "syncing"):
        status = "in_progress"
        light = "blue"
        is_updating = True
        status_label = "업데이트중"
    elif status_str in ("failed", "fail", "error"):
        status = "failed"
        light = "red"
        is_updating = False
        status_label = "다운로드 실패"
    elif within_one_day:
        status = "success"
        light = "green"
        is_updating = False
        if elapsed_hours < 1.0:
            mins = max(1, int(elapsed_seconds / 60))
            status_label = f"최신 ({mins}분 전)"
        else:
            hours = int(elapsed_hours)
            status_label = f"최신 ({hours}시간 전)"
    else:
        status = "stale"
        light = "yellow"
        is_updating = False
        days = int(elapsed_hours // 24)
        rem_hours = int(elapsed_hours % 24)
        if days >= 1 and rem_hours > 0:
            status_label = f"업데이트 지연 ({days}일 {rem_hours}시간 경과)"
        elif days >= 1:
            status_label = f"업데이트 지연 ({days}일 경과)"
        else:
            status_label = f"업데이트 지연 ({int(elapsed_hours)}시간 경과)"

    return {
        "has_log": True,
        "log_file": found_log.name,
        "log_path": str(found_log),
        "status": status,
        "status_label": status_label,
        "light": light,
        "timestamp": parsed_dt.isoformat(timespec="seconds"),
        "elapsed_hours": round(elapsed_hours, 1),
        "within_one_day": within_one_day,
        "is_updating": is_updating,
        "message": message_str[:200],
        "raw_text": raw_text[:500],
    }


def inspect_mapfiles_for_product(vehicle: str, force: bool = False) -> dict[str, Any]:
    """Inspect all mapfiles for a product, checking file signatures against cache."""
    code, file_paths = list_mapfiles_for_product(vehicle)
    mapfile_dir = get_mapfile_dir()
    cache_path = get_traffic_cache_path()

    # The full read/check/verify/write sequence is one transaction. This gives
    # concurrent requests and processes single-flight behavior for unchanged
    # signatures and prevents writers from dropping each other's cache keys.
    with file_transaction(cache_path):
        cache = load_traffic_cache()
        cache_dirty = False
        results: list[dict[str, Any]] = []

        for path in file_paths:
            try:
                rel = path.relative_to(mapfile_dir).as_posix()
            except Exception:
                rel = path.name
            cache_key = f"{vehicle}:{rel}"
            sig = file_signature(path)
            cached_entry = cache.get(cache_key) or cache.get(f"{vehicle}:{path.name}")

            if not force and cached_entry and cached_entry.get("signature") == sig and "sl" in cached_entry and "main" in cached_entry:
                entry = dict(cached_entry)
                entry["is_cached"] = True
                # Ensure dc fields exist in cached entry
                if "rel_path" not in entry:
                    entry["rel_path"] = rel
                if "dc_type" not in entry:
                    pname = path.parent.name.lower()
                    entry["dc_type"] = "dev" if pname == "dev" else "prod" if pname == "prod" else "root"
                    entry["dc_label"] = "개발 DC" if entry["dc_type"] == "dev" else "양산DC" if entry["dc_type"] == "prod" else "기타"
                results.append(entry)
            else:
                entry = verify_single_mapfile(vehicle, path)
                cache[cache_key] = entry
                cache_dirty = True
                entry_copy = dict(entry)
                entry_copy["is_cached"] = False
                results.append(entry_copy)

        if cache_dirty:
            save_traffic_cache(cache)

    def _calc_group_summary(group_files: list[dict[str, Any]]) -> dict[str, int]:
        return {
            "total_files": len(group_files),
            "green_files": sum(1 for r in group_files if r.get("traffic_light") == "green"),
            "yellow_files": sum(1 for r in group_files if r.get("traffic_light") == "yellow"),
            "red_files": sum(1 for r in group_files if r.get("traffic_light") == "red"),
            "gray_files": sum(1 for r in group_files if r.get("traffic_light") == "gray"),
            "sl_green_files": sum(1 for r in group_files if (r.get("sl") or {}).get("light") == "green"),
            "sl_yellow_files": sum(1 for r in group_files if (r.get("sl") or {}).get("light") == "yellow"),
            "sl_red_files": sum(1 for r in group_files if (r.get("sl") or {}).get("light") == "red"),
            "main_green_files": sum(1 for r in group_files if (r.get("main") or {}).get("light") == "green"),
            "main_yellow_files": sum(1 for r in group_files if (r.get("main") or {}).get("light") == "yellow"),
            "main_red_files": sum(1 for r in group_files if (r.get("main") or {}).get("light") == "red"),
        }

    def _calc_light(sum_dict: dict[str, int]) -> str:
        if sum_dict.get("red_files", 0) > 0:
            return "red"
        if sum_dict.get("yellow_files", 0) > 0:
            return "yellow"
        if sum_dict.get("gray_files", 0) == 0 and sum_dict.get("green_files", 0) > 0:
            return "green"
        return "gray"

    # Groups: dev (개발 DC), prod (양산DC) - only dev and prod, root is excluded
    dev_results = [r for r in results if r.get("dc_type") == "dev"]
    prod_results = [r for r in results if r.get("dc_type") == "prod"]

    dev_summary = _calc_group_summary(dev_results)
    dev_light = _calc_light(dev_summary)

    prod_summary = _calc_group_summary(prod_results)
    prod_light = _calc_light(prod_summary)

    groups = [
        {
            "key": "dev",
            "label": "개발 DC",
            "folder": "dev",
            "files": dev_results,
            "overall_light": dev_light,
            "summary": dev_summary,
        },
        {
            "key": "prod",
            "label": "양산DC",
            "folder": "prod",
            "files": prod_results,
            "overall_light": prod_light,
            "summary": prod_summary,
        },
    ]

    # Overall summary across dev and prod files (or all matching files)
    active_results = dev_results + prod_results if (dev_results or prod_results) else results
    overall_summary = _calc_group_summary(active_results)
    overall_light = _calc_light(overall_summary)

    github_sync = get_github_download_status(mapfile_dir)

    return {
        "ok": True,
        "vehicle": vehicle,
        "product_code": code,
        "mapfile_dir": str(mapfile_dir),
        "mapfile_dir_exists": mapfile_dir.is_dir(),
        "files": active_results,
        "groups": groups,
        "overall_light": overall_light,
        "summary": overall_summary,
        "github_sync": github_sync,
    }


def read_mapfile_text(filename: str) -> str:
    """Safely read content of a mapfile from the mapfile directory or dev/prod subfolders."""
    clean = Path(str(filename or "").strip().replace("\\", "/"))
    mapfile_dir = get_mapfile_dir()

    candidates: list[Path] = []
    # If relative path like dev/xxx or prod/xxx
    if len(clean.parts) > 1:
        candidates.append(mapfile_dir / clean)
    else:
        candidates.append(mapfile_dir / "dev" / clean.name)
        candidates.append(mapfile_dir / "prod" / clean.name)
        candidates.append(mapfile_dir / clean.name)

    target: Path | None = None
    for c in candidates:
        if c.is_file():
            target = c
            break

    # Fallback: case-insensitive search across mapfile_dir
    if not target and mapfile_dir.is_dir():
        target_name = clean.name.casefold()
        for root_dir in [mapfile_dir / "dev", mapfile_dir / "prod", mapfile_dir]:
            if root_dir.is_dir():
                try:
                    for entry in root_dir.iterdir():
                        if entry.is_file() and entry.name.casefold() == target_name:
                            target = entry
                            break
                except OSError:
                    pass
                if target:
                    break

    if not target or not target.is_file():
        raise FileNotFoundError(f"Mapfile '{clean.name}'이 존재하지 않습니다.")

    raw = target.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp949", errors="replace")

