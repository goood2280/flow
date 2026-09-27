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


_CACHE: dict = {"key": None, "rows": []}


def _rows_cached() -> list[dict]:
    """홈 챗·분석의뢰·ET 추적이 매 요청 부르므로 파일이 그대로면 다시 읽지 않는다."""
    path = mapping_path()
    try:
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        key = (str(path), None, None)
    if _CACHE["key"] != key:
        _CACHE["rows"] = load_mapping()["rows"] if key[1] is not None else []
        _CACHE["key"] = key
    return _CACHE["rows"]


def step_to_layer() -> dict[str, str]:
    """step_id → DC layer. 같은 step_id 가 여러 layer 에 있으면 먼저 적힌 layer (auto report dc_dict 와 같은 first-wins)."""
    out: dict[str, str] = {}
    for row in _rows_cached():
        for step_id in row.get("step_ids") or []:
            out.setdefault(str(step_id).upper(), str(row.get("dc_layer") or ""))
    return out


def dc_layer_for_step(step_id: str) -> str:
    return step_to_layer().get(str(step_id or "").strip().upper(), "")


def steps_for_layer(layer: str) -> list[str]:
    wanted = str(layer or "").strip().upper()
    row = next((r for r in _rows_cached() if r.get("dc_layer") == wanted), None)
    return list(row.get("step_ids") or []) if row else []


def parse_mapping_text(text: str) -> list[dict]:
    """auto report 의 dict 를 그대로 붙여넣어도 읽는다.

    - ``self.dc_step_to_ids = {'M1DC': ['NU467300', ...], ...}`` (DC layer → step_id 목록)
    - ``{'NU467300': 'M1DC', ...}`` (dc_dict, step_id → DC layer) — 먼저 나온 layer 순서를 유지해 뒤집는다
    - JSON 같은 모양, 또는 줄마다 ``M1DC: NU1, NU2`` / ``M1DC<TAB>NU1,NU2``
    """
    import ast
    import json
    import re

    raw = str(text or "").strip()
    if not raw:
        return []
    body = re.sub(r"^[\w.\s]*=\s*", "", raw, count=1) if re.match(r"^[\w.\s]*=\s*[{\[]", raw) else raw
    parsed = None
    for loader in (json.loads, ast.literal_eval):
        try:
            parsed = loader(body)
            break
        except Exception:
            continue
    rows: list[dict] = []
    if isinstance(parsed, dict):
        values = list(parsed.values())
        if values and all(isinstance(v, str) for v in values):
            inverted: dict[str, list[str]] = {}
            for step_id, layer in parsed.items():
                inverted.setdefault(str(layer), []).append(str(step_id))
            rows = [{"dc_layer": layer, "step_ids": steps} for layer, steps in inverted.items()]
        else:
            rows = [{"dc_layer": str(layer), "step_ids": value if isinstance(value, (list, tuple, set)) else str(value or "")}
                    for layer, value in parsed.items()]
    elif isinstance(parsed, list):
        rows = [row for row in parsed if isinstance(row, dict)]
    else:
        for line in raw.splitlines():
            parts = re.split(r"\t|:|=", line, maxsplit=1)
            if len(parts) == 2 and parts[0].strip():
                rows.append({"dc_layer": parts[0].strip().strip("'\""),
                             "step_ids": re.sub(r"[\[\]'\"]", "", parts[1])})
    return normalize_rows(rows)


def mentioned_layers(text: str) -> dict[str, list[str]]:
    """질문에 나온 DC layer 이름(M1DC 등) → step_id 목록. 단어 경계로만 맞춘다(M1DC ≠ M10DC)."""
    import re

    found: dict[str, list[str]] = {}
    source = str(text or "")
    for row in _rows_cached():
        layer = str(row.get("dc_layer") or "")
        if layer and re.search(rf"(?<![A-Za-z0-9]){re.escape(layer)}(?![A-Za-z0-9])", source, re.I):
            found[layer] = list(row.get("step_ids") or [])
    return found


def prompt_context(text: str = "", *, max_layers: int = 40) -> dict:
    """홈 챗 계획 LLM 참고자료 — 질문에 나온 layer 는 step_id 까지, 나머지는 이름만."""
    rows = _rows_cached()
    if not any(row.get("step_ids") for row in rows):
        return {}
    mentioned = mentioned_layers(text)
    return {
        "meaning": "DC layer(예: M1DC)는 ET 측정 공정 묶음 이름이다. 질문의 DC layer 는 아래 step_id 들의 ET 측정을 뜻한다.",
        "mentioned": mentioned,
        "layers": [row["dc_layer"] for row in rows if row.get("step_ids")][:max_layers],
    }


def annotate_rows(rows: list, columns: list | None = None) -> tuple[list, list | None]:
    """표에 step_id 열이 있으면 옆에 dc_layer 열을 붙인다(값을 찾은 행이 하나라도 있을 때만)."""
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return rows, columns
    step_key = next((k for k in rows[0] if str(k).casefold() == "step_id"), None)
    if not step_key or any(str(k).casefold() == "dc_layer" for k in rows[0]):
        return rows, columns
    mapping = step_to_layer()
    if not mapping:
        return rows, columns
    layers = [mapping.get(str(row.get(step_key) or "").strip().upper(), "") for row in rows]
    if not any(layers):
        return rows, columns
    out = []
    for row, layer in zip(rows, layers):
        new_row = {}
        for key, value in row.items():
            new_row[key] = value
            if key == step_key:
                new_row["dc_layer"] = layer
        out.append(new_row)
    if isinstance(columns, list) and step_key in columns and "dc_layer" not in columns:
        index = columns.index(step_key) + 1
        columns = columns[:index] + ["dc_layer"] + columns[index:]
    return out, columns

