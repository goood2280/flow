"""Operator-managed DC layer to ET step_id mapping.

The CSV intentionally lives at the DB root so it is visible as one ordinary
file in DB FileBrowser and can be shared by API/worker servers.
"""
from __future__ import annotations

import csv
import os
import threading
from pathlib import Path

from core.paths import PATHS


FILE_NAME = "dc_layer_step_mapping.csv"
_LOCK = threading.Lock()


def mapping_path() -> Path:
    return PATHS.db_root / FILE_NAME


def default_rows() -> list[dict]:
    return [
        *[{"dc_layer": f"M{i}DC", "step_ids": []} for i in range(1, 10)],
        {"dc_layer": "AADC", "step_ids": []},
    ]


def _tokens(value) -> list[str]:
    raw = value if isinstance(value, (list, tuple, set)) else str(value or "").split(",")
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item or "").strip().upper()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def normalize_rows(rows) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        layer = str(raw.get("dc_layer") or raw.get("DC_Layer") or "").strip().upper()
        if not layer or layer in seen:
            continue
        seen.add(layer)
        out.append({"dc_layer": layer, "step_ids": _tokens(raw.get("step_ids") or raw.get("step_id"))})
    return out


def load_mapping() -> dict:
    path = mapping_path()
    if not path.is_file():
        return {"exists": False, "path": str(path), "file_name": FILE_NAME, "rows": default_rows()}
    rows: list[dict] = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for raw in csv.DictReader(handle):
                rows.append({
                    "dc_layer": raw.get("dc_layer") or raw.get("DC_Layer") or "",
                    "step_ids": raw.get("step_ids") or raw.get("step_id") or "",
                })
    except Exception:
        rows = []
    return {"exists": True, "path": str(path), "file_name": FILE_NAME, "rows": normalize_rows(rows)}


def save_mapping(rows) -> dict:
    normalized = normalize_rows(rows)
    path = mapping_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with _LOCK:
        try:
            with tmp.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["dc_layer", "step_ids"])
                writer.writeheader()
                for row in normalized:
                    writer.writerow({"dc_layer": row["dc_layer"], "step_ids": ",".join(row["step_ids"])})
            tmp.replace(path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
    return {"ok": True, "exists": True, "path": str(path), "file_name": FILE_NAME, "rows": normalized}


def step_to_layer() -> dict[str, str]:
    out: dict[str, str] = {}
    for row in load_mapping()["rows"]:
        for step_id in row.get("step_ids") or []:
            out.setdefault(str(step_id).upper(), str(row.get("dc_layer") or ""))
    return out


def dc_layer_for_step(step_id: str) -> str:
    return step_to_layer().get(str(step_id or "").strip().upper(), "")

