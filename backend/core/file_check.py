"""Read-only consistency checks for Flow's matching CSV files.

The report deliberately opens the active DB files directly.  A file check
must never modify either storage root.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.paths import PATHS

VEHICLE_FILE = "Vehicle_matching.csv"
CHECK_FILES = (
    ("ppid_knob", "knob_ppid", "ppid_knob.csv", "knob_ppid.csv", "PPID/KNOB 규칙"),
    ("vm_matching", "vm_matching", "vm_matching.csv", "", "VM 매칭"),
)


def _schema(data_root: Path) -> dict[str, dict]:
    path = data_root / "splittable" / "rulebook_schema.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeError, ValueError):
        return {}


def _named_file(root: Path, filename: str) -> Path:
    """Match SplitTable's exact-name-first, unique-case-variant resolution."""
    target = root / Path(str(filename or "")).name
    if target.exists() or not root.is_dir():
        return target
    matches = sorted(p for p in root.iterdir() if p.is_file() and p.name.casefold() == target.name.casefold())
    return matches[0] if len(matches) == 1 else target


def _rulebook_path(root: Path, schema: dict, kind: str, default: str, legacy: str = "") -> tuple[Path, str]:
    settings = schema.get(kind)
    settings = settings if isinstance(settings, dict) else {}
    configured = Path(str(settings.get("file_name") or default)).name
    if not configured.lower().endswith(".csv"):
        configured += ".csv"
    primary = _named_file(root, configured)
    if configured != default or primary.exists() or not legacy:
        return primary, "SplitTable DB rulebook"
    fallback = _named_file(root, legacy)
    return (fallback, "SplitTable DB legacy rulebook") if fallback.exists() else (primary, "SplitTable DB rulebook")


def _read_step_desc(path: Path, *, filename: str, location: str) -> dict[str, Any]:
    source: dict[str, Any] = {
        "file": filename,
        "location": location,
        "exists": path.is_file(),
        "columns": [],
        "row_count": 0,
        "step_desc_count": 0,
        "blank_step_desc_rows": [],
        "diagnostics": [],
    }
    if not source["exists"]:
        source["diagnostics"].append({
            "level": "error", "code": "missing_file",
            "message": f"{filename} 파일을 찾지 못했습니다.",
        })
        return {"source": source, "values": {}}

    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            raw_columns = list(reader.fieldnames or [])
            columns = [str(name or "").strip() for name in raw_columns]
            source["columns"] = columns
            step_column = next((name for name in raw_columns
                                if str(name or "").strip().casefold() == "step_desc"), "")
            if not step_column:
                source["diagnostics"].append({
                    "level": "error", "code": "missing_column",
                    "message": f"{filename}에 step_desc 열이 없습니다.",
                })
                return {"source": source, "values": {}}

            values: dict[str, list[int]] = {}
            for row_number, raw in enumerate(reader, start=2):
                source["row_count"] += 1
                value = str((raw or {}).get(step_column) or "").strip()
                if value:
                    values.setdefault(value, []).append(row_number)
                else:
                    source["blank_step_desc_rows"].append(row_number)
            source["step_desc_count"] = len(values)
            if source["blank_step_desc_rows"]:
                count = len(source["blank_step_desc_rows"])
                source["diagnostics"].append({
                    "level": "warning", "code": "blank_step_desc",
                    "message": f"step_desc가 빈 행이 {count}개 있습니다.",
                    "rows": source["blank_step_desc_rows"],
                })
            return {"source": source, "values": values}
    except (OSError, UnicodeError, csv.Error) as exc:
        source["diagnostics"].append({
            "level": "error", "code": "read_error",
            "message": f"{filename}을 읽지 못했습니다: {type(exc).__name__}",
        })
        return {"source": source, "values": {}}


def _note_legacy_fallback(source: dict[str, Any], requested: str) -> None:
    if source["location"] != "SplitTable DB legacy rulebook":
        return
    source["diagnostics"].append({
        "level": "warning", "code": "requested_file_missing_legacy_used",
        "message": f"DB 루트에 {requested}가 없어 기존 {source['file']} 파일로 비교했습니다.",
    })


def build_report(*, db_root: Path | None = None, data_root: Path | None = None) -> dict[str, Any]:
    """Compare source step_desc values with Vehicle_matching; never write files."""
    if db_root is not None:
        db = Path(db_root)
    else:
        from app_v2.shared.source_adapter import resolve_existing_root

        db = resolve_existing_root("base", Path(PATHS.base_root))
    data = Path(data_root) if data_root is not None else Path(PATHS.data_root)
    schema = _schema(data)
    vehicle_path, vehicle_location = _rulebook_path(
        db, schema, "step_matching", VEHICLE_FILE, "step_matching.csv"
    )
    vehicle = _read_step_desc(vehicle_path, filename=vehicle_path.name, location=vehicle_location)
    _note_legacy_fallback(vehicle["source"], VEHICLE_FILE)
    flow_data_vehicle = data / "matching" / VEHICLE_FILE
    vehicle["source"]["alternatives"] = []
    if flow_data_vehicle.is_file():
        vehicle["source"]["alternatives"].append({
            "file": VEHICLE_FILE,
            "location": "flow-data 기존 사본 (미사용)",
            "selected": False,
            "note": "Vehicle_matching은 DB 루트의 단일 파일을 사용합니다. 이 기존 사본은 읽지 않습니다.",
        })
    reference_ok = not any(d["level"] == "error" for d in vehicle["source"]["diagnostics"])
    known = set(vehicle["values"])

    checks = []
    for check_id, kind, default, legacy, label in CHECK_FILES:
        # SplitTable's KNOB metadata builder intentionally uses the fixed
        # ppid_knob.csv (then knob_ppid.csv) path. VM follows rulebook schema.
        active_schema = {} if check_id == "ppid_knob" else schema
        source_path, source_location = _rulebook_path(db, active_schema, kind, default, legacy)
        parsed = _read_step_desc(source_path, filename=source_path.name, location=source_location)
        _note_legacy_fallback(parsed["source"], default)
        source_error = any(d["level"] == "error" for d in parsed["source"]["diagnostics"])
        missing = []
        if reference_ok and not source_error:
            missing = [
                {"step_desc": value, "source_rows": rows}
                for value, rows in parsed["values"].items()
                if value not in known
            ]
        source_warning = any(d["level"] == "warning" for d in parsed["source"]["diagnostics"])
        status = "error" if source_error or not reference_ok else ("warning" if missing or source_warning else "ok")
        diagnostics = list(parsed["source"]["diagnostics"])
        if not reference_ok:
            diagnostics.append({
                "level": "error", "code": "reference_unavailable",
                "message": "Vehicle_matching.csv 기준 파일을 읽을 수 없어 비교하지 못했습니다.",
            })
        elif missing:
            diagnostics.append({
                "level": "warning", "code": "missing_from_vehicle_matching",
                "message": f"Vehicle_matching.csv에 없는 step_desc가 {len(missing)}개 있습니다.",
            })
        checks.append({
            "id": check_id, "label": label, "status": status,
            "source": parsed["source"], "missing_count": len(missing), "missing": missing,
            "compared": reference_ok and not source_error, "diagnostics": diagnostics,
        })

    total_missing = sum(item["missing_count"] for item in checks)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "reference": vehicle["source"],
        "checks": checks,
        "summary": {
            "status": "error" if any(c["status"] == "error" for c in checks)
            else ("warning" if any(c["status"] == "warning" for c in checks)
                  or any(d["level"] == "warning" for d in vehicle["source"]["diagnostics"])
                  else "ok"),
            "total_missing": total_missing,
            "checked_files": len(checks),
        },
    }
