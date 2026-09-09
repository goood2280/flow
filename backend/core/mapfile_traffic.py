"""Mapfile traffic light verification engine for TEG 위치조회.

Discovers mapfiles matching product_code from roots.get_db_root() / 'mapfile',
performs verification with core.teg_check.inspect, and caches results by file signature
(mtime + size) so verification only runs when files change.
"""
from __future__ import annotations

import datetime as dt
import logging
import hashlib
from pathlib import Path
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
        return any(
            entry.is_file() and not entry.name.startswith((".", "~", "$"))
            for entry in path.iterdir()
        )
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
    """List mapfiles matching the vehicle's product_code.

    Returns (product_code, matching_paths).
    """
    code = get_product_code_for_vehicle(vehicle)
    dir_path = get_mapfile_dir()
    if not dir_path.is_dir():
        return code, []

    matched: list[Path] = []
    prefix = code.strip().casefold() if code.strip() else ""
    veh_prefix = vehicle.strip().casefold()

    try:
        for entry in sorted(dir_path.iterdir(), key=lambda p: p.name.casefold()):
            if not entry.is_file() or entry.name.startswith((".", "~", "$")):
                continue
            ename = entry.name.casefold()
            # Match by product_code if configured, else fallback to vehicle name
            if prefix and ename.startswith(prefix):
                matched.append(entry)
            elif not prefix and veh_prefix and ename.startswith(veh_prefix):
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


def verify_single_mapfile(vehicle: str, file_path: Path) -> dict[str, Any]:
    """Inspect a single mapfile using core.teg_check.inspect."""
    filename = file_path.name
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
            cache_key = f"{vehicle}:{path.name}"
            sig = file_signature(path)
            cached_entry = cache.get(cache_key)

            if not force and cached_entry and cached_entry.get("signature") == sig and "sl" in cached_entry and "main" in cached_entry:
                entry = dict(cached_entry)
                entry["is_cached"] = True
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

    # Calculate product summary
    green_cnt = sum(1 for r in results if r.get("traffic_light") == "green")
    yellow_cnt = sum(1 for r in results if r.get("traffic_light") == "yellow")
    red_cnt = sum(1 for r in results if r.get("traffic_light") == "red")
    gray_cnt = sum(1 for r in results if r.get("traffic_light") == "gray")

    sl_green_cnt = sum(1 for r in results if (r.get("sl") or {}).get("light") == "green")
    sl_yellow_cnt = sum(1 for r in results if (r.get("sl") or {}).get("light") == "yellow")
    sl_red_cnt = sum(1 for r in results if (r.get("sl") or {}).get("light") == "red")

    main_green_cnt = sum(1 for r in results if (r.get("main") or {}).get("light") == "green")
    main_yellow_cnt = sum(1 for r in results if (r.get("main") or {}).get("light") == "yellow")
    main_red_cnt = sum(1 for r in results if (r.get("main") or {}).get("light") == "red")

    overall_light = "gray"
    if red_cnt > 0:
        overall_light = "red"
    elif yellow_cnt > 0:
        overall_light = "yellow"
    elif gray_cnt == 0 and green_cnt > 0:
        overall_light = "green"

    return {
        "ok": True,
        "vehicle": vehicle,
        "product_code": code,
        "mapfile_dir": str(mapfile_dir),
        "mapfile_dir_exists": mapfile_dir.is_dir(),
        "files": results,
        "overall_light": overall_light,
        "summary": {
            "total_files": len(results),
            "green_files": green_cnt,
            "yellow_files": yellow_cnt,
            "red_files": red_cnt,
            "gray_files": gray_cnt,
            "sl_green_files": sl_green_cnt,
            "sl_yellow_files": sl_yellow_cnt,
            "sl_red_files": sl_red_cnt,
            "main_green_files": main_green_cnt,
            "main_yellow_files": main_yellow_cnt,
            "main_red_files": main_red_cnt,
        },
    }


def read_mapfile_text(filename: str) -> str:
    """Safely read content of a mapfile from the mapfile directory."""
    clean_name = Path(filename).name
    target = get_mapfile_dir() / clean_name
    if not target.is_file():
        raise FileNotFoundError(f"Mapfile '{clean_name}'이 존재하지 않습니다.")
    try:
        return target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return target.read_text(encoding="cp949", errors="replace")
