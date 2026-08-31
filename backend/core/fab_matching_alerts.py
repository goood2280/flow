"""Development-worker FAB scanner for matching alerts.

The matching-alert page used to consume alarm JSON produced by Valve.  This
module makes Flow authoritative instead:

* only the development ``worker`` runs the periodic, product-by-product scan;
* the API server reads the shared state written by that worker;
* a FAB step absent from ``Vehicle_matching.csv`` becomes ``unmatched_step``;
* a PPID without an explicit rule for the step's function step becomes
  ``ro_ppid`` and can be added to ``ppid_knob.csv`` from the existing page;
* a FAB ``reticle_id`` absent from ``mask_info.csv`` becomes ``missing_reticle``
  and can be added there with its mask name.  That file has no product column,
  so the alert is keyed by reticle_id alone and merged across products.

The public shape deliberately matches the old valve-alert API so bookmarked
URLs and the page permission key (``valve``) do not need a migration.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import socket
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from core.paths import PATHS
from core.utils import load_json, save_json

logger = logging.getLogger("flow.fab_matching_alerts")

DEFAULT_SCAN_INTERVAL_SECONDS = 2 * 60 * 60
CONFIG_SCHEMA_VERSION = 2
LEGACY_DEFAULT_SCAN_INTERVAL_SECONDS = 5 * 60

CFG_PATH = PATHS.data_root / "fab_matching_alerts.json"
STATE_PATH = PATHS.log_dir / "fab_matching_alerts_state.json"
# 실제로 돌고 있는 검사 스레드의 생존/진행 신호.  큰 state 파일과 분리해 10초마다
# 부담 없이 덮어쓸 수 있게 하고, 두 서버가 같은 workspace 를 볼 때 "요청은
# 등록됐는데 아무도 안 돈다" 와 "지금 몇 시간짜리 제품을 검사 중이다" 를
# 화면에서 구분할 수 있게 한다.
SCANNER_PATH = PATHS.log_dir / "fab_matching_alerts_scanner.json"
SCANNER_BEAT_SECONDS = 10
SCANNER_STALE_SECONDS = 60
# 추천기는 한 번에 처리하는 건수에 상한이 있다. FAB 제품 검사 주기(기본 2시간)에
# 묶어 두면 밀린 추천이 며칠씩 남으므로, 남은 건이 있을 때만 이 간격으로 다음
# 배치를 이어서 실행한다.
RECOMMENDATION_BATCH_INTERVAL_SECONDS = 60
ACK_PATH = PATHS.data_root / "fab_matching_alert_acks.json"
DECISIONS_PATH = PATHS.data_root / "fab_matching_alert_decisions.jsonl"

PPID_KNOB_FILE = "ppid_knob.csv"
VEHICLE_MATCHING_FILE = "Vehicle_matching.csv"
# reticle_id → mask 이름 룰북.  제품 구분이 없는 2열(reticle_id, mask) 파일이라
# 알람 키도 제품이 아니라 reticle_id 하나로 잡는다.
MASK_INFO_FILE = "mask_info.csv"
MASK_INFO_COLUMNS = ["reticle_id", "mask"]

DEFAULT_CFG = {
    "config_schema_version": CONFIG_SCHEMA_VERSION,
    "enabled": True,
    # One product is scanned per interval.  A full cycle therefore takes
    # interval * product count and never fans large FAB reads out in parallel.
    "scan_interval_seconds": DEFAULT_SCAN_INTERVAL_SECONDS,
    "step_exceptions": [],
}

_thread: threading.Thread | None = None
_started = False
_lock = threading.Lock()
_write_lock = threading.Lock()
_scanner_lock = threading.Lock()
_scanner_local: dict = {}


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    save_json(path, payload)


# 예외 규칙이 비교할 수 있는 FAB 열.  화면의 드롭다운 순서와 같다.
EXCEPTION_COLUMNS = ("eqp_model", "eqp_id", "area", "ppid")
_EXCEPTION_COLUMNS = set(EXCEPTION_COLUMNS)
_EXCEPTION_OPERATORS = {"eq", "contains", "starts_with"}
# 근거 열은 알람마다 상한을 둔다 — 제품 하나의 step 이 수백 개 eqp_id 를 갖는
# 경우까지 state 파일에 통째로 실으면 목록 API 가 무거워진다.
_EVIDENCE_VALUE_LIMIT = 20


def _normalize_step_exceptions(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    out: list[dict] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            continue
        column = str(raw.get("column") or "ppid").strip().lower()
        operator = str(raw.get("operator") or "contains").strip().lower()
        expected = str(raw.get("value") or "").strip()
        if column not in _EXCEPTION_COLUMNS or operator not in _EXCEPTION_OPERATORS or not expected:
            continue
        out.append({
            "id": str(raw.get("id") or f"exception-{index + 1}").strip()[:80],
            "enabled": bool(raw.get("enabled", True)),
            "product": str(raw.get("product") or "").strip()[:120],
            "column": column,
            "operator": operator,
            "value": expected[:500],
            "note": str(raw.get("note") or "").strip()[:500],
        })
    return out


def load_cfg() -> dict:
    raw = load_json(CFG_PATH, DEFAULT_CFG)
    raw = raw if isinstance(raw, dict) else {}
    interval = int(
        raw.get("scan_interval_seconds") or raw.get("poll_seconds") or DEFAULT_SCAN_INTERVAL_SECONDS
    )
    # Before the settings gear existed, 300 seconds was written automatically
    # as the application default. Migrate only that unversioned default; keep
    # every other pre-existing/custom interval intact.
    if (int(raw.get("config_schema_version") or 0) < CONFIG_SCHEMA_VERSION
            and interval == LEGACY_DEFAULT_SCAN_INTERVAL_SECONDS):
        interval = DEFAULT_SCAN_INTERVAL_SECONDS
    return {
        "config_schema_version": CONFIG_SCHEMA_VERSION,
        "enabled": bool(raw.get("enabled", True)),
        "scan_interval_seconds": max(30, interval),
        "step_exceptions": _normalize_step_exceptions(raw.get("step_exceptions") or []),
    }


def save_cfg(patch: dict) -> dict:
    cfg = load_cfg()
    if "enabled" in patch:
        cfg["enabled"] = bool(patch["enabled"])
    interval = patch.get("scan_interval_seconds", patch.get("poll_seconds"))
    if interval is not None:
        cfg["scan_interval_seconds"] = max(30, int(interval or DEFAULT_SCAN_INTERVAL_SECONDS))
    if "step_exceptions" in patch:
        cfg["step_exceptions"] = _normalize_step_exceptions(patch.get("step_exceptions"))
    _atomic_json(CFG_PATH, cfg)
    return cfg


def _load_state() -> dict:
    state = load_json(STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}
    state.setdefault("alerts_by_product", {})
    state.setdefault("products", [])
    state.setdefault("cursor", 0)
    return state


def _save_state(state: dict) -> None:
    _atomic_json(STATE_PATH, state)


def _scanner_beat(**patch: Any) -> None:
    """Publish this host's scanner liveness/progress to the shared workspace.

    Only the host actually running the loop writes this file, so a stale
    ``alive_ts`` is exactly the signal the page needs: a scan request that
    nobody will ever consume.
    """
    with _scanner_lock:
        _scanner_local.update(patch)
        _scanner_local.update({
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "alive_ts": time.time(),
        })
        payload = dict(_scanner_local)
    try:
        _atomic_json(SCANNER_PATH, payload)
    except Exception:
        logger.debug("FAB matching scanner heartbeat write failed", exc_info=True)


def _scanner_status() -> dict:
    """Read the scanner heartbeat and judge whether a scanner is really running.

    Wall clocks between the api and worker hosts can drift, so staleness is
    judged on the absolute difference — the same rule ``worker_dispatch`` uses
    for its own heartbeat.
    """
    raw = load_json(SCANNER_PATH, {})
    raw = raw if isinstance(raw, dict) else {}
    alive_ts = float(raw.get("alive_ts") or 0.0)
    alive = bool(alive_ts) and abs(time.time() - alive_ts) < SCANNER_STALE_SECONDS
    started = float(raw.get("product_started_ts") or 0.0)
    scanning = alive and str(raw.get("state") or "") == "scanning"
    return {
        "alive": alive,
        "state": (str(raw.get("state") or "idle") if alive else "down"),
        "host": str(raw.get("host") or ""),
        "pid": int(raw.get("pid") or 0),
        "alive_ts": alive_ts,
        "started_ts": float(raw.get("started_ts") or 0.0),
        "product": str(raw.get("product") or "") if scanning else "",
        "product_started_ts": started if scanning else 0.0,
        "elapsed_seconds": round(max(0.0, time.time() - started), 1) if scanning and started else 0.0,
        "files_done": int(raw.get("files_done") or 0) if scanning else 0,
        "files_total": int(raw.get("files_total") or 0) if scanning else 0,
        "next_scan_ts": float(raw.get("next_scan_ts") or 0.0),
        "last_error": str(raw.get("last_error") or ""),
    }


def _scan_request_hint(requested: bool, requested_ts: float, scanner: dict,
                       worker_enabled: bool) -> str:
    """Explain, in one line, why a pending scan request has not run yet."""
    if not requested:
        return ""
    waited = max(0.0, time.time() - requested_ts) if requested_ts else 0.0
    waited_text = f" ({int(waited // 60)}분 대기)" if waited >= 60 else ""
    if scanner.get("state") == "scanning":
        return (f"개발 worker가 {scanner.get('product')} 검사 중입니다 — "
                f"끝나면 이어서 실행됩니다{waited_text}")
    if scanner.get("alive"):
        return f"개발 worker가 요청을 확인 중입니다{waited_text}"
    if worker_enabled:
        return (f"이 서버는 worker 역할이지만 검사 스레드가 실행 중이 아닙니다{waited_text} — "
                "'지금 다음 제품 검사'를 다시 누르거나 개발 서버를 재시작하세요")
    return (f"실행 중인 개발 worker 검사기가 없습니다{waited_text} — "
            "개발 서버 기동과 worker 역할 마커를 확인하세요")


def _norm(value: Any) -> str:
    text = str(value or "").strip()
    if text.upper().startswith("ML_TABLE_"):
        text = text[len("ML_TABLE_"):]
    return text.upper()


def _read_csv(path: Path) -> tuple[list[str], list[dict]]:
    if not path.is_file():
        return [], []
    with path.open("r", encoding="utf-8-sig", newline="") as fp:
        reader = csv.DictReader(fp)
        return list(reader.fieldnames or []), [dict(row) for row in reader]


def _write_csv_atomic(path: Path, columns: list[str], rows: list[dict]) -> None:
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer, fieldnames=columns, extrasaction="ignore", lineterminator="\n"
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({column: row.get(column) if row.get(column) is not None else ""
                         for column in columns})
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(buffer.getvalue(), encoding="utf-8", newline="")
    temporary.replace(path)


_RAW_DB_DISPLAY_RE = re.compile(r"^1\.RAWDATA_DB(?:_(.+))?$", re.IGNORECASE)


def _default_db_display_name(name: str) -> str:
    clean = str(name or "").strip()
    matched = _RAW_DB_DISPLAY_RE.fullmatch(clean)
    if not matched:
        return clean
    suffix = str(matched.group(1) or "").strip(" _-")
    return suffix or "FAB"


def _folder_aliases() -> dict[str, str]:
    """FileBrowser 폴더설정의 실제 폴더명 → 표시 DB명 매핑."""
    raw = load_json(PATHS.data_root / "filebrowser_settings.json", {})
    configured = raw.get("db_name_aliases") if isinstance(raw, dict) else {}
    if not isinstance(configured, dict):
        return {}
    return {
        str(source).strip().casefold(): str(display).strip()
        for source, display in configured.items()
        if str(source or "").strip() and str(display or "").strip()
    }


def _fab_roots() -> list[Path]:
    """Return only folders whose FileBrowser folder-setting display name is FAB."""
    root = Path(PATHS.db_root)
    if not root.is_dir():
        return []
    aliases = _folder_aliases()
    out: list[Path] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        display = aliases.get(child.name.casefold(), _default_db_display_name(child.name))
        if str(display).strip().casefold() == "fab":
            out.append(child)
    return sorted(out, key=lambda p: p.name.casefold())


def discover_products() -> list[dict]:
    """Return FAB product folders that contain parquet or CSV data."""
    found: dict[str, dict] = {}
    for root in _fab_roots():
        try:
            children = sorted(root.iterdir(), key=lambda p: p.name.upper())
        except OSError:
            continue
        for product_dir in children:
            if not product_dir.is_dir() or product_dir.name.startswith((".", "_")):
                continue
            try:
                has_data = next(product_dir.rglob("*.parquet"), None) is not None
                if not has_data:
                    has_data = next(product_dir.rglob("*.csv"), None) is not None
            except OSError:
                has_data = False
            if not has_data:
                continue
            key = _norm(product_dir.name)
            found.setdefault(key, {
                "product": product_dir.name,
                "path": str(product_dir),
                "root": root.name,
            })
    return [found[key] for key in sorted(found)]


def _column_expr(pl, names: list[str], aliases: tuple[str, ...], alias: str):
    by_lower = {str(name).lower(): name for name in names}
    source = next((by_lower.get(name.lower()) for name in aliases if by_lower.get(name.lower())), None)
    if source:
        return pl.col(source).cast(pl.Utf8, strict=False).fill_null("").str.strip_chars().alias(alias)
    return pl.lit("").cast(pl.Utf8).alias(alias)


def _scan_file(path: Path) -> tuple[list[dict], list[dict]]:
    """Aggregate one FAB file before collecting so source row volume stays off RAM.

    Returns ``(step/ppid groups, reticle groups)``.  The reticle rollup is its
    own aggregation — folding it into the step/ppid key would either duplicate
    ppid alerts or make every reticle inherit the whole group's row count.
    """
    import polars as pl

    if path.suffix.lower() == ".csv":
        lf = pl.scan_csv(str(path), infer_schema_length=1000, ignore_errors=True)
    else:
        lf = pl.scan_parquet(str(path))
    names = lf.collect_schema().names()
    selected = lf.select([
        _column_expr(pl, names, ("step_id", "step"), "step_id"),
        _column_expr(pl, names, ("ppid", "recipe_id", "recipe"), "ppid"),
        _column_expr(pl, names, ("lot_id", "fab_lot_id"), "lot_id"),
        _column_expr(pl, names, ("root_lot_id", "root_lot"), "root_lot_id"),
        _column_expr(pl, names, ("wafer_id", "wafer"), "wafer_id"),
        _column_expr(pl, names, ("eqp_id", "eqp"), "eqp_id"),
        _column_expr(pl, names, ("eqp_model", "equipment_model", "model"), "eqp_model"),
        # area 는 예외 규칙과 function step 추천(valve_step_advisor)이 같이 쓰는
        # 열이다.  사내 DB 에 없으면 _column_expr 가 빈 문자열로 채운다.
        _column_expr(pl, names, ("area", "area_id", "eqp_area", "module"), "area"),
        _column_expr(pl, names, ("reticle_id", "reticle"), "reticle_id"),
        _column_expr(pl, names, ("tkout_time", "time", "timestamp"), "event_time"),
    ]).filter(pl.col("step_id") != "")
    grouped = selected.group_by(["step_id", "ppid"]).agg([
        pl.len().alias("rows"),
        pl.col("lot_id").filter(pl.col("lot_id") != "").n_unique().alias("n_lots"),
        pl.col("lot_id").filter(pl.col("lot_id") != "").first().alias("lot_id"),
        pl.col("root_lot_id").filter(pl.col("root_lot_id") != "").first().alias("root_lot_id"),
        pl.col("wafer_id").filter(pl.col("wafer_id") != "").first().alias("wafer_id"),
        pl.col("eqp_id").filter(pl.col("eqp_id") != "").unique().alias("eqp_ids"),
        pl.col("eqp_model").filter(pl.col("eqp_model") != "").unique().alias("eqp_models"),
        pl.col("area").filter(pl.col("area") != "").unique().alias("areas"),
        pl.col("event_time").filter(pl.col("event_time") != "").max().alias("latest_event_time"),
    ])
    by_reticle = selected.filter(pl.col("reticle_id") != "").group_by("reticle_id").agg([
        pl.len().alias("rows"),
        pl.col("lot_id").filter(pl.col("lot_id") != "").n_unique().alias("n_lots"),
        pl.col("lot_id").filter(pl.col("lot_id") != "").first().alias("lot_id"),
        pl.col("root_lot_id").filter(pl.col("root_lot_id") != "").first().alias("root_lot_id"),
        pl.col("wafer_id").filter(pl.col("wafer_id") != "").first().alias("wafer_id"),
        pl.col("eqp_id").filter(pl.col("eqp_id") != "").first().alias("eqp_id"),
        pl.col("eqp_model").filter(pl.col("eqp_model") != "").first().alias("eqp_model"),
        pl.col("area").filter(pl.col("area") != "").first().alias("area"),
        pl.col("step_id").filter(pl.col("step_id") != "").unique().alias("step_ids"),
        pl.col("ppid").filter(pl.col("ppid") != "").unique().alias("ppids"),
        pl.col("event_time").filter(pl.col("event_time") != "").max().alias("latest_event_time"),
    ])
    # 한 번의 collect 로 두 집계를 같이 계산해 소스 스캔이 두 번 일어나지 않게 한다.
    frames = pl.collect_all([grouped, by_reticle])
    return frames[0].to_dicts(), frames[1].to_dicts()


def scan_fab_product(product: dict,
                     progress: Callable[[int, int], None] | None = None
                     ) -> tuple[list[dict], list[dict]]:
    """Return ``(step/ppid observations, reticle observations)`` for one product.

    ``progress(done, total)`` is called as files are consumed so the page can
    show "전체 몇 개 파일 중 몇 개" instead of an opaque multi-hour silence.
    """
    base = Path(product["path"])
    files = sorted(
        [p for p in base.rglob("*") if p.is_file() and p.suffix.lower() in {".parquet", ".csv"}],
        key=lambda p: str(p).lower(),
    )
    total = len(files)
    if progress:
        progress(0, total)
    merged: dict[tuple[str, str], dict] = {}
    reticles: dict[str, dict] = {}
    for index, path in enumerate(files, 1):
        if progress and (index == total or index % 25 == 0):
            progress(index, total)
        try:
            rows, reticle_rows = _scan_file(path)
        except Exception as exc:
            logger.warning("FAB matching scan file failed (%s): %s", path, exc)
            continue
        for row in reticle_rows:
            reticle_id = str(row.get("reticle_id") or "").strip()
            if not reticle_id:
                continue
            bucket = reticles.setdefault(_norm(reticle_id), {
                "reticle_id": reticle_id, "rows": 0, "n_lots": 0,
                "lot_id": "", "root_lot_id": "", "wafer_id": "", "eqp_id": "", "eqp_model": "",
                "area": "",
                "_step_ids": set(), "_ppids": set(), "latest_event_time": "",
            })
            bucket["rows"] += int(row.get("rows") or 0)
            bucket["n_lots"] += int(row.get("n_lots") or 0)
            for value in (row.get("step_ids") or []):
                if str(value or "").strip():
                    bucket["_step_ids"].add(str(value).strip())
            for value in (row.get("ppids") or []):
                if str(value or "").strip():
                    bucket["_ppids"].add(str(value).strip())
            for field in ("lot_id", "root_lot_id", "wafer_id", "eqp_id", "eqp_model", "area"):
                if not bucket[field] and row.get(field):
                    bucket[field] = str(row[field])
            event_time = str(row.get("latest_event_time") or "")
            if event_time > bucket["latest_event_time"]:
                bucket["latest_event_time"] = event_time
        for row in rows:
            step_id = str(row.get("step_id") or "").strip()
            ppid = str(row.get("ppid") or "").strip()
            key = (step_id, ppid)
            current = merged.setdefault(key, {
                "step_id": step_id, "ppid": ppid, "rows": 0, "n_lots": 0,
                "lot_id": "", "root_lot_id": "", "wafer_id": "", "eqp_id": "", "eqp_model": "",
                "area": "",
                "_eqp_ids": set(), "_eqp_models": set(), "_areas": set(),
                "latest_event_time": "",
            })
            current["rows"] += int(row.get("rows") or 0)
            current["n_lots"] += int(row.get("n_lots") or 0)
            for source, target in (("eqp_ids", "_eqp_ids"), ("eqp_models", "_eqp_models"),
                                   ("areas", "_areas")):
                for value in (row.get(source) or []):
                    if str(value or "").strip():
                        current[target].add(str(value).strip())
            for field in ("lot_id", "root_lot_id", "wafer_id"):
                if not current[field] and row.get(field):
                    current[field] = str(row[field])
            event_time = str(row.get("latest_event_time") or "")
            if event_time > current["latest_event_time"]:
                current["latest_event_time"] = event_time
    out: list[dict] = []
    for current in merged.values():
        current["eqp_ids"] = sorted(current.pop("_eqp_ids"), key=str.casefold)
        current["eqp_models"] = sorted(current.pop("_eqp_models"), key=str.casefold)
        current["areas"] = sorted(current.pop("_areas"), key=str.casefold)
        current["eqp_id"] = current["eqp_ids"][0] if current["eqp_ids"] else ""
        current["eqp_model"] = current["eqp_models"][0] if current["eqp_models"] else ""
        current["area"] = current["areas"][0] if current["areas"] else ""
        out.append(current)
    reticle_out: list[dict] = []
    for bucket in reticles.values():
        bucket["step_ids"] = sorted(bucket.pop("_step_ids"), key=str.casefold)
        bucket["ppids"] = sorted(bucket.pop("_ppids"), key=str.casefold)
        reticle_out.append(bucket)
    return out, reticle_out


def _rule_matches_ppid(ppid: str, operator: str, expected: str) -> bool:
    actual = str(ppid or "").strip()
    wanted = str(expected or "").strip()
    op = re.sub(r"[\s-]+", "_", str(operator or "eq").strip().lower())
    left, right = actual.casefold(), wanted.casefold()
    if op in {"", "eq", "=", "==", "equals"}:
        return left == right
    if op in {"contains", "contain", "includes"}:
        return bool(right) and right in left
    if op in {"starts_with", "startswith", "prefix"}:
        return bool(right) and left.startswith(right)
    if op in {"ends_with", "endswith", "suffix"}:
        return bool(right) and left.endswith(right)
    if op in {"regex", "matches"}:
        try:
            return re.search(wanted, actual, flags=re.IGNORECASE) is not None
        except re.error:
            return False
    return False


def _mapping_context(product: str) -> dict:
    _, vehicle_rows = _read_csv(Path(PATHS.db_root) / VEHICLE_MATCHING_FILE)
    _, knob_rows = _read_csv(Path(PATHS.db_root) / PPID_KNOB_FILE)
    product_key = _norm(product)
    associated = [r for r in vehicle_rows if _norm(r.get("product")) == product_key]
    if not associated:
        associated = [r for r in vehicle_rows if _norm(r.get("vehicle")) == product_key]
    vehicles = sorted({str(r.get("vehicle") or "").strip() for r in associated if r.get("vehicle")})
    vehicle = vehicles[0] if len(vehicles) == 1 else str(product)
    step_map: dict[str, str] = {}
    for row in associated:
        step_id = str(row.get("step_id") or "").strip()
        step_desc = str(row.get("step_desc") or row.get("function_step") or "").strip()
        if step_id:
            step_map.setdefault(step_id, step_desc)
    features_by_step: dict[str, dict[str, dict]] = {}
    for row in knob_rows:
        order = str(row.get("rule_order") or "").strip().upper()
        step_desc = str(row.get("function_step") or row.get("step_desc") or "").strip()
        feature = str(row.get("feature_name") or "").strip()
        if not step_desc or not feature:
            continue
        feature_rule = features_by_step.setdefault(step_desc.casefold(), {}).setdefault(
            feature, {"feature_name": feature, "step_desc": step_desc,
                      "has_ro": False, "rules": []}
        )
        if order == "RO":
            feature_rule["has_ro"] = True
            feature_rule["ro_category"] = str(row.get("category") or "").strip()
            continue
        operator = str(row.get("operator") or "eq").strip().lower()
        value = str(row.get("value") or "").strip()
        if value:
            feature_rule["rules"].append({
                "rule_order": order,
                "operator": operator,
                "value": value,
                "category": str(row.get("category") or "").strip(),
            })
    return {
        "vehicle": vehicle, "step_map": step_map,
        "features_by_step": features_by_step,
    }


def _mask_info_column(columns: list[str], wanted: str) -> str:
    """mask_info.csv 헤더는 대소문자·공백이 제각각이라 이름으로 실제 열을 찾는다."""
    for column in columns:
        if str(column or "").strip().casefold() == wanted:
            return column
    return ""


def _known_reticles() -> set[str]:
    columns, rows = _read_csv(Path(PATHS.db_root) / MASK_INFO_FILE)
    reticle_column = _mask_info_column(columns, "reticle_id")
    if not reticle_column:
        return set()
    return {_norm(row.get(reticle_column)) for row in rows if str(row.get(reticle_column) or "").strip()}


def _exception_matches(product: str, evidence: dict, rules: list[dict]) -> dict | None:
    """Return the first enabled rule matching this step's FAB evidence.

    A rule with an empty ``product`` deliberately applies to every product —
    that is how one site-wide exception ("모든 제품의 dummy PPID 제외") is written
    without repeating it per product.
    """
    values_by_column = {
        "ppid": evidence.get("ppids") or [],
        "eqp_id": evidence.get("eqp_ids") or [],
        "eqp_model": evidence.get("eqp_models") or [],
        "area": evidence.get("areas") or [],
    }
    for rule in rules:
        if not rule.get("enabled", True):
            continue
        scoped_product = str(rule.get("product") or "").strip()
        if scoped_product and _norm(scoped_product) != _norm(product):
            continue
        operator = rule.get("operator") or "contains"
        expected = str(rule.get("value") or "")
        for actual in values_by_column.get(rule.get("column"), []):
            if _rule_matches_ppid(str(actual), str(operator), expected):
                return {**rule, "matched_value": str(actual)}
    return None


def _alerts_for_product(product: dict, observations: list[dict],
                        reticle_observations: list[dict], previous: list[dict]) -> list[dict]:
    now = time.time()
    old = {a.get("id"): a for a in previous}
    context = _mapping_context(product["product"])
    vehicle = context["vehicle"]
    step_map = context["step_map"]
    features_by_step = context["features_by_step"]
    exception_rules = load_cfg().get("step_exceptions") or []
    known_reticles = _known_reticles()
    by_step: dict[str, dict] = {}
    alerts: list[dict] = []

    for row in observations:
        step_id = row["step_id"]
        agg = by_step.setdefault(step_id, {
            "rows": 0, "n_lots": 0, "lot_id": "", "root_lot_id": "", "wafer_id": "",
            "eqp_id": "", "eqp_model": "", "area": "", "latest_event_time": "",
            "ppids": set(), "eqp_ids": set(), "eqp_models": set(), "areas": set(),
        })
        agg["rows"] += int(row.get("rows") or 0)
        agg["n_lots"] += int(row.get("n_lots") or 0)
        if row.get("ppid"):
            agg["ppids"].add(str(row["ppid"]))
        for field in ("eqp_ids", "eqp_models", "areas"):
            agg[field].update(str(v) for v in (row.get(field) or []) if str(v or "").strip())
        for field in ("lot_id", "root_lot_id", "wafer_id", "eqp_id", "eqp_model", "area"):
            if not agg[field] and row.get(field):
                agg[field] = row[field]
        if str(row.get("latest_event_time") or "") > agg["latest_event_time"]:
            agg["latest_event_time"] = str(row.get("latest_event_time") or "")

    def common(alert_id: str, step_id: str, evidence: dict) -> dict:
        prior = old.get(alert_id) or {}
        example = {k: evidence.get(k) for k in ("lot_id", "root_lot_id", "wafer_id") if evidence.get(k)}
        return {
            "id": alert_id,
            "vehicle": vehicle,
            "product": product["product"],
            "step_id": step_id,
            "first_seen_ts": prior.get("first_seen_ts") or now,
            "last_seen_ts": now,
            "rows": int(evidence.get("rows") or 0),
            "n_lots": int(evidence.get("n_lots") or 0),
            "eqp_id": evidence.get("eqp_id") or "",
            "eqp_model": evidence.get("eqp_model") or "",
            "area": evidence.get("area") or "",
            "latest_event_time": evidence.get("latest_event_time") or "",
            "examples": [example] if example else [],
            "source": product["path"],
            "source_root": product["root"],
        }

    def _values(evidence: dict, field: str) -> list[str]:
        return sorted(evidence.get(field) or [], key=str.casefold)[:_EVIDENCE_VALUE_LIMIT]

    def _match_hint(step_id: str, evidence: dict) -> dict:
        """FAB 스캔 프로세스가 가진 동일-area 매칭 step 근거를 웹 추천에 동봉한다."""
        def node(candidate_id: str, candidate: dict) -> dict:
            return {
                "step_id": candidate_id,
                "rows": int(candidate.get("rows") or 0),
                "values": {
                    "ppid": _values(candidate, "ppids"),
                    "eqp_id": _values(candidate, "eqp_ids"),
                    "eqp_model": _values(candidate, "eqp_models"),
                    "area": _values(candidate, "areas"),
                },
            }

        target_areas = set(evidence.get("areas") or [])
        candidates = []
        for candidate_id in sorted(step_map, key=str.casefold):
            if candidate_id == step_id:
                continue
            candidate = by_step.get(candidate_id)
            if not candidate:
                continue
            if not target_areas.intersection(candidate.get("areas") or []):
                continue
            candidates.append(node(candidate_id, candidate))
        return {
            **node(step_id, evidence),
            "cols": ["ppid", "eqp_id", "eqp_model", "area"],
            "neighbors": candidates,
        }

    for step_id, evidence in sorted(by_step.items()):
        if step_id not in step_map:
            matched_exception = _exception_matches(product["product"], evidence, exception_rules)
            if matched_exception:
                continue
            alert_id = f"fab-step|{_norm(product['product'])}|{step_id}"
            # 예외 규칙을 쓰려면 이 step 이 실제로 어떤 값을 갖고 있는지 알아야
            # 한다 — 화면이 후보값을 그대로 보여줄 수 있게 같이 싣는다.
            alerts.append({**common(alert_id, step_id, evidence),
                           "type": "unmatched_step", "step_desc": "",
                           "eqp_ids": _values(evidence, "eqp_ids"),
                           "eqp_models": _values(evidence, "eqp_models"),
                           "areas": _values(evidence, "areas"),
                           "ppids": _values(evidence, "ppids"),
                           "match_hint": _match_hint(step_id, evidence)})

    for evidence in sorted(reticle_observations,
                           key=lambda r: _norm(r.get("reticle_id"))):
        reticle_id = str(evidence.get("reticle_id") or "").strip()
        if not reticle_id or _norm(reticle_id) in known_reticles:
            continue
        step_ids = list(evidence.get("step_ids") or [])
        # mask_info.csv 에는 제품 열이 없다 — 알람 ID 도 reticle_id 로만 만들어
        # 여러 제품에서 같은 reticle 이 빠져 있어도 판정은 한 번만 하게 한다.
        alert_id = f"fab-reticle|{_norm(reticle_id)}"
        alerts.append({**common(alert_id, step_ids[0] if step_ids else "", evidence),
                       "type": "missing_reticle", "reticle_id": reticle_id,
                       "step_ids": step_ids,
                       "ppids": list(evidence.get("ppids") or []), "step_desc": ""})

    for row in sorted(observations, key=lambda r: (r["step_id"], r["ppid"])):
        ppid = row["ppid"]
        if not ppid:
            continue
        step_desc = step_map.get(row["step_id"], "")
        if not step_desc:
            continue
        split_rules = features_by_step.get(step_desc.casefold()) or {}
        for feature, feature_rule in sorted(split_rules.items(), key=lambda item: item[0].casefold()):
            if not feature_rule.get("has_ro"):
                continue
            if any(_rule_matches_ppid(ppid, rule.get("operator"), rule.get("value"))
                   for rule in feature_rule.get("rules") or []):
                continue
            feature_token = re.sub(r"[^A-Za-z0-9._-]+", "_", feature).strip("_") or "split"
            alert_id = (
                f"fab-ppid|{_norm(product['product'])}|{row['step_id']}|"
                f"{feature_token}|{ppid}"
            )
            alerts.append({**common(alert_id, row["step_id"], row),
                           "type": "ro_ppid", "ppid": ppid,
                           "step_desc": step_desc, "feature_name": feature,
                           "split": feature, "ro_category": feature_rule.get("ro_category") or ""})
    return alerts


def scan_product(product: dict) -> dict:
    """Scan one product and atomically publish its current matching gaps."""
    # This function opens every parquet/csv belonging to the product.  Keep the
    # role gate at the heavy-work boundary as well as at scheduler startup so a
    # future API route, test hook, or refactor cannot make the operating server
    # scan multi-GB FAB data accidentally.
    if not _development_worker_enabled():
        return {"ok": False, "skipped": True, "reason": "development_worker_only",
                "product": product.get("product") or ""}
    started = time.time()
    name = str(product.get("product") or "")
    _scanner_beat(state="scanning", product=name, product_started_ts=started,
                  files_done=0, files_total=0, last_error="")
    try:
        observations, reticle_observations = scan_fab_product(
            product,
            progress=lambda done, total: _scanner_beat(files_done=done, files_total=total),
        )
    finally:
        # 실패해도 "검사 중" 으로 굳지 않게 한다 — 실패 사유는 state 의
        # last_error 와 product_status 가 갖는다.
        _scanner_beat(state="idle", product="", product_started_ts=0.0,
                      files_done=0, files_total=0)
    with _lock:
        state = _load_state()
        previous = list((state.get("alerts_by_product") or {}).get(_norm(product["product"])) or [])
        alerts = _alerts_for_product(product, observations, reticle_observations, previous)
        state["alerts_by_product"][_norm(product["product"])] = alerts
        state["last_scan_ts"] = time.time()
        state["last_product"] = product["product"]
        state["last_error"] = ""
        status = state.setdefault("product_status", {})
        status[_norm(product["product"])] = {
            "product": product["product"], "scanned_ts": state["last_scan_ts"],
            "duration_seconds": round(state["last_scan_ts"] - started, 3),
            "observations": len(observations), "alerts": len(alerts), "error": "",
        }
        _save_state(state)
    return {"ok": True, "product": product["product"], "alerts": len(alerts),
            "observations": len(observations), "duration_seconds": round(time.time() - started, 3)}


def scan_next_product(force: bool = False) -> dict:
    if not _development_worker_enabled():
        return {"ok": False, "skipped": True, "reason": "development_worker_only"}
    cfg = load_cfg()
    if not cfg["enabled"] and not force:
        return {"ok": True, "enabled": False}
    products = discover_products()
    with _lock:
        state = _load_state()
        state["products"] = products
        cursor = int(state.get("cursor") or 0)
        # 요청은 여기서 한 번 소비된다 — 제품을 못 찾은 경우에도 반드시 지운다.
        # 예전에는 아래 이른 return 이 플래그를 남겨서 화면이 영원히 "검사 요청
        # 대기 중" 이었고, 루프도 요청이 남은 줄 알고 10초마다 FAB 트리 전체를
        # 다시 훑었다.
        state["scan_requested"] = False
        state["scan_requested_ts"] = 0
        if not products:
            state["last_scan_ts"] = time.time()
            state["last_error"] = "FAB 제품 폴더를 찾지 못했습니다"
            _save_state(state)
            _scanner_beat(last_error=state["last_error"])
            return {"ok": False, "error": state["last_error"], "products": 0}
        product = products[cursor % len(products)]
        state["cursor"] = (cursor + 1) % len(products)
        _save_state(state)
    try:
        result = scan_product(product)
    except Exception as exc:
        logger.exception("FAB matching product scan failed: %s", product.get("product"))
        with _lock:
            state = _load_state()
            state["last_scan_ts"] = time.time()
            state["last_product"] = product.get("product") or ""
            state["last_error"] = f"{type(exc).__name__}: {exc}"
            status = state.setdefault("product_status", {})
            status[_norm(product.get("product"))] = {
                "product": product.get("product"), "scanned_ts": state["last_scan_ts"],
                "duration_seconds": 0, "observations": 0, "alerts": 0,
                "error": state["last_error"],
            }
            _save_state(state)
        _scanner_beat(last_error=f"{type(exc).__name__}: {exc}")
        return {"ok": False, "product": product.get("product"), "error": str(exc)}
    result["products"] = len(products)
    return result


def request_scan() -> dict:
    """Ask the development worker to advance its product scan immediately.

    The request is only ever consumed by the scanner thread, so a host that was
    marked as the worker *after* boot would queue requests nobody reads.  Check
    the thread here as well — that turns "restart the dev server" into "press
    the button again".
    """
    _ensure_scheduler_running()
    with _lock:
        state = _load_state()
        state["scan_requested"] = True
        state["scan_requested_ts"] = time.time()
        _save_state(state)
    scanner = _scanner_status()
    if not scanner["alive"]:
        return {"ok": True, "queued": True, "scanner_alive": False,
                "message": "요청은 등록했지만 실행 중인 개발 worker 검사기가 없습니다 — "
                           "개발 서버 기동과 worker 역할 마커를 확인하세요"}
    if scanner["state"] == "scanning":
        return {"ok": True, "queued": True, "scanner_alive": True,
                "message": f"검사 대기열에 등록했습니다 — 현재 {scanner['product']} 검사 중입니다"}
    return {"ok": True, "queued": True, "scanner_alive": True,
            "message": "개발 서버 검사 대기열에 등록했습니다"}


def _acks() -> dict:
    value = load_json(ACK_PATH, {})
    return value if isinstance(value, dict) else {}


def set_ack(alert_id: str, status: str, note: str = "", by: str = "flow") -> dict:
    with _write_lock:
        ack = _acks()
        if status and status != "active":
            ack[alert_id] = {"status": status, "note": note, "by": by, "ts": time.time()}
        else:
            ack.pop(alert_id, None)
        _atomic_json(ACK_PATH, ack)
    return ack


def _append_decision(rec: dict) -> None:
    rec = {"ts": time.time(), **rec}
    DECISIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with DECISIONS_PATH.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def list_decisions(limit: int = 200) -> list[dict]:
    if not DECISIONS_PATH.is_file():
        return []
    out: list[dict] = []
    for line in DECISIONS_PATH.read_text("utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out[-max(1, int(limit)):][::-1]


def _decided_ids() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in list_decisions(2000):
        alert_id = row.get("alert_id")
        if alert_id and alert_id not in out and row.get("action") in {"classify", "match", "add_mask"}:
            out[alert_id] = row
    return out


def _recommendations() -> dict[str, dict]:
    try:
        from core import valve_step_advisor
        records = valve_step_advisor.load_records()
        # 알고리즘 교체 직후 백그라운드 재검사가 끝날 때까지 과거 추천을 그대로
        # 보여주면 사용자는 이미 폐기된 값을 새 결과로 오해한다. 현재 버전만 붙이고
        # 나머지는 화면에서 "추천 대기"로 둔다.
        return {
            key: record for key, record in records.items()
            if int(record.get("algorithm_version") or 0)
            == valve_step_advisor.ALGORITHM_VERSION
        }
    except Exception:
        return {}


def _run_recommendation_batch() -> dict:
    """Recommend active unmatched steps without letting advisor errors kill the scanner."""
    started_ts = time.time()
    try:
        from core import valve_step_advisor

        # list_alerts() applies current acknowledgements, decisions and exception
        # rules first, so the advisor never spends work on rows already handled.
        current_alerts = list_alerts().get("alerts") or []
        result = valve_step_advisor.recommend_pending(current_alerts)
        if not isinstance(result, dict):
            raise TypeError("추천 결과 형식이 올바르지 않습니다")
        status = {
            "ok": bool(result.get("ok")),
            "enabled": bool(result.get("enabled", True)),
            "last_run_ts": started_ts,
            "finished_ts": time.time(),
            "pending": int(result.get("pending") or 0),
            "checked": int(result.get("checked") or 0),
            "remaining": int(result.get("skipped") or 0),
            "error": str(result.get("error") or ""),
        }
    except Exception as exc:
        logger.exception("FAB matching recommendation batch failed")
        result = {"ok": False, "checked": 0, "skipped": 0, "error": str(exc)}
        status = {
            "ok": False,
            "enabled": True,
            "last_run_ts": started_ts,
            "finished_ts": time.time(),
            "pending": 0,
            "checked": 0,
            "remaining": 0,
            "error": str(exc),
        }
    with _lock:
        state = _load_state()
        state["recommendation_status"] = status
        _save_state(state)
    return result


def list_alerts() -> dict:
    # 개발 worker 에서 페이지를 열기만 해도 죽은/미기동 스캐너가 되살아난다.
    # 운영 API 에서는 역할 게이트에 막혀 아무 일도 하지 않는다.
    _ensure_scheduler_running()
    state = _load_state()
    alerts: list[dict] = []
    merged_by_id: dict[str, dict] = {}
    for product_alerts in (state.get("alerts_by_product") or {}).values():
        for alert in (product_alerts or []):
            alert = dict(alert)
            # reticle 알람은 제품이 아니라 reticle_id 로 식별한다 — 여러 제품 상태에
            # 같은 ID 가 들어 있으면 근거만 합쳐 한 줄로 보여준다.
            if alert.get("type") != "missing_reticle":
                alerts.append(alert)
                continue
            prior = merged_by_id.get(alert["id"])
            if prior is None:
                alert["products"] = [alert.get("product")] if alert.get("product") else []
                # mask_info.csv 는 전 제품 공용이라 reticle 알람 자체는 한 줄로
                # 합치되, 화면에서 제품을 골랐을 때는 그 제품에서 발견한 근거만
                # 보여줄 수 있도록 합치기 전 수치를 함께 보존한다.
                alert["product_evidence"] = [{
                    "product": alert.get("product"),
                    "rows": int(alert.get("rows") or 0),
                    "n_lots": int(alert.get("n_lots") or 0),
                    "examples": list(alert.get("examples") or []),
                    "first_seen_ts": alert.get("first_seen_ts"),
                    "last_seen_ts": alert.get("last_seen_ts"),
                    "latest_event_time": alert.get("latest_event_time"),
                    "step_ids": list(alert.get("step_ids") or []),
                    "ppids": list(alert.get("ppids") or []),
                }]
                merged_by_id[alert["id"]] = alert
                alerts.append(alert)
                continue
            prior["rows"] = int(prior.get("rows") or 0) + int(alert.get("rows") or 0)
            prior["n_lots"] = int(prior.get("n_lots") or 0) + int(alert.get("n_lots") or 0)
            prior["first_seen_ts"] = min(float(prior.get("first_seen_ts") or 0),
                                         float(alert.get("first_seen_ts") or 0))
            prior["last_seen_ts"] = max(float(prior.get("last_seen_ts") or 0),
                                        float(alert.get("last_seen_ts") or 0))
            if alert.get("product") and alert["product"] not in prior["products"]:
                prior["products"].append(alert["product"])
            prior.setdefault("product_evidence", []).append({
                "product": alert.get("product"),
                "rows": int(alert.get("rows") or 0),
                "n_lots": int(alert.get("n_lots") or 0),
                "examples": list(alert.get("examples") or []),
                "first_seen_ts": alert.get("first_seen_ts"),
                "last_seen_ts": alert.get("last_seen_ts"),
                "latest_event_time": alert.get("latest_event_time"),
                "step_ids": list(alert.get("step_ids") or []),
                "ppids": list(alert.get("ppids") or []),
            })
            for field in ("step_ids", "ppids"):
                prior[field] = sorted(set(prior.get(field) or []) | set(alert.get(field) or []),
                                      key=str.casefold)
            if str(alert.get("latest_event_time") or "") > str(prior.get("latest_event_time") or ""):
                prior["latest_event_time"] = alert.get("latest_event_time")
    # 예외 규칙은 저장 즉시 화면에서 걷힌다.  규칙은 스캔 시점에도 적용되지만,
    # 제품 하나씩 도는 검사가 그 제품에 닿기까지 몇 시간이 걸릴 수 있다.
    # 근거값(`_EVIDENCE_VALUE_LIMIT` 개까지)이 알람에 실려 있어 FAB 재검사 없이
    # 여기서 다시 판정할 수 있다 — 상한을 넘는 값에만 걸리는 규칙은 다음 검사에서
    # 반영된다.
    exception_rules = load_cfg().get("step_exceptions") or []
    if exception_rules:
        alerts = [
            alert for alert in alerts
            if alert.get("type") != "unmatched_step"
            or not _exception_matches(alert.get("product") or alert.get("vehicle") or "",
                                     alert, exception_rules)
        ]
    ack = _acks()
    decided = _decided_ids()
    recs = _recommendations()
    mapping_by_product: dict[str, dict] = {}
    for alert in alerts:
        # A step decision is visible immediately, before the worker reaches the
        # product again.  This also unlocks its PPID rows for same-session rule
        # classification without making the operating API rescan FAB.
        if alert.get("type") == "ro_ppid":
            product_key = _norm(alert.get("product") or alert.get("vehicle"))
            context = mapping_by_product.get(product_key)
            if context is None:
                context = _mapping_context(alert.get("product") or alert.get("vehicle") or "")
                mapping_by_product[product_key] = context
            step_desc = str(context["step_map"].get(alert.get("step_id")) or "").strip()
            if step_desc:
                alert["step_desc"] = step_desc
                alert["blocked_by_step"] = False
        info = ack.get(alert.get("id")) or {}
        alert["status"] = info.get("status") or "active"
        alert["ack_note"] = info.get("note") or ""
        alert["decision"] = decided.get(alert.get("id"))
        if alert.get("type") == "unmatched_step":
            alert["recommendation"] = recs.get(
                f"{alert.get('vehicle') or ''}|{alert.get('step_id') or ''}"
            )
    alerts.sort(key=lambda a: (
        1 if a.get("decision") else 0,
        1 if a.get("status") != "active" else 0,
        -float(a.get("first_seen_ts") or 0),
    ))
    active = sum(1 for a in alerts if a["status"] == "active" and not a.get("decision"))
    # The operating API reads only the shared worker state.  Product discovery
    # itself can recurse through a large FAB tree and is worker-owned.
    products = state.get("products") or []
    worker_enabled = _development_worker_enabled()
    scanner_info = _scanner_status()
    requested = bool(state.get("scan_requested"))
    requested_ts = float(state.get("scan_requested_ts") or 0.0)
    return {
        "ok": True,
        "source": "development_fab_scanner",
        "store": "개발 서버 FAB 직접 검사",
        "alerts": alerts,
        "active": active,
        "stalled": 0,
        "vehicles": [],
        "alert_cols": [],
        "scanner": {
            "role": "worker",
            "execution_enabled_here": worker_enabled,
            "execution_policy": "development_worker_only",
            "products": len(products),
            "product_list": [p.get("product") for p in products],
            "source_roots": sorted({str(p.get("root") or "") for p in products if p.get("root")}),
            "last_scan_ts": state.get("last_scan_ts"),
            "last_product": state.get("last_product") or "",
            "last_error": state.get("last_error") or "",
            "next_product": (
                products[int(state.get("cursor") or 0) % len(products)].get("product")
                if products else ""
            ),
            "scan_requested": requested,
            "scan_requested_ts": requested_ts,
            "scan_waiting_seconds": (
                round(max(0.0, time.time() - requested_ts), 1) if requested and requested_ts else 0.0
            ),
            # 아래 6개가 "대기 중" 의 이유를 화면에서 가른다: 검사기가 아예
            # 없는지, 살아 있는데 다른 제품을 오래 검사 중인지.
            "scanner_alive": scanner_info["alive"],
            "scanner_state": scanner_info["state"],
            "scanner_host": scanner_info["host"],
            "scanner_pid": scanner_info["pid"],
            "scanner_last_alive_ts": scanner_info["alive_ts"],
            "scanning": {
                "product": scanner_info["product"],
                "started_ts": scanner_info["product_started_ts"],
                "elapsed_seconds": scanner_info["elapsed_seconds"],
                "files_done": scanner_info["files_done"],
                "files_total": scanner_info["files_total"],
            },
            "next_scan_ts": scanner_info["next_scan_ts"],
            "scan_request_hint": _scan_request_hint(
                requested, requested_ts, scanner_info, worker_enabled),
            "product_status": state.get("product_status") or {},
            "recommendation": state.get("recommendation_status") or {},
        },
    }


def _find_alert(alert_id: str) -> dict | None:
    return next((a for a in list_alerts().get("alerts", []) if a.get("id") == alert_id), None)


def _post_write(path: Path, actor: str, note: str) -> dict:
    """Snapshot, invalidate the matching cache and use normal artifact sync."""
    snapshot_ok = True
    snapshot_meta = None
    try:
        from routers.filebrowser import _snapshot_base_file_version
        snapshot_meta = _snapshot_base_file_version(
            path, path.name, actor=actor or "fab-matching-alerts",
            action="fab-matching-alert", note=note,
        )
    except Exception as exc:
        snapshot_ok = False
        logger.warning("FAB matching version snapshot failed (%s): %s", path.name, exc)
    try:
        from core import matching_cache
        cache_result = matching_cache.refresh_matching_csv(path)
    except Exception as exc:
        cache_result = {"ok": False, "error": str(exc)}
    try:
        from core import s3_sync
        sync_result = s3_sync.sync_saved_path(PATHS.data_root, PATHS.db_root, path) or {}
    except Exception as exc:
        sync_result = {"status": "error", "error": str(exc)}
    return {"version_snapshot": snapshot_ok, "version_meta": snapshot_meta,
            "cache": cache_result,
            "s3_sync": sync_result, "store_push": sync_result.get("store_push")}


def _step_desc_from_rows(rows: list[dict], product: str, step_id: str) -> str:
    product_key = _norm(product)
    associated = [row for row in rows if _norm(row.get("product")) == product_key]
    if not associated:
        associated = [row for row in rows if _norm(row.get("vehicle")) == product_key]
    for row in associated:
        if str(row.get("step_id") or "").strip() == step_id:
            return str(row.get("step_desc") or row.get("function_step") or "").strip()
    return ""


_EXPECTED_CHANGE_KIND = {
    "unmatched_step": "match_step",
    "ro_ppid": "classify_ppid",
    "missing_reticle": "add_mask",
}


def apply_batch(changes: list[dict], note: str = "", username: str = "") -> dict:
    """Apply multiple step/PPID/reticle decisions with one snapshot per changed CSV.

    Step decisions are prepared first, so a PPID decision may depend on a step
    mapping included in the same batch. Every changed CSV shares the batch id.
    """
    if not isinstance(changes, list) or not changes:
        raise ValueError("일괄 반영할 항목이 없습니다")
    if len(changes) > 500:
        raise ValueError("한 번에 최대 500건까지 반영할 수 있습니다")

    alert_rows = list_alerts().get("alerts") or []
    alerts = {str(alert.get("id") or ""): alert for alert in alert_rows}
    normalized: list[tuple[str, dict, dict]] = []
    seen: set[str] = set()
    for raw in changes:
        alert_id = str(raw.get("id") or "").strip()
        if not alert_id or alert_id in seen:
            raise ValueError(f"중복되거나 비어 있는 알람 ID: {alert_id or '(없음)'}")
        seen.add(alert_id)
        alert = alerts.get(alert_id)
        if not alert:
            raise LookupError(f"알람을 찾을 수 없음: {alert_id}")
        kind = str(raw.get("type") or "").strip().lower().replace("-", "_")
        expected = _EXPECTED_CHANGE_KIND.get(alert.get("type"), "classify_ppid")
        if kind not in {expected, alert.get("type")}:
            raise ValueError(f"알람 유형이 일치하지 않음: {alert_id}")
        normalized.append((expected, alert, raw))

    actor = username or "flow"
    batch_id = f"MB-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
    batch_note = str(note or "").strip()
    vehicle_path = Path(PATHS.db_root) / VEHICLE_MATCHING_FILE
    knob_path = Path(PATHS.db_root) / PPID_KNOB_FILE
    mask_path = Path(PATHS.db_root) / MASK_INFO_FILE
    results: list[dict] = []
    decisions: list[dict] = []
    posts: dict[str, dict] = {}

    with _write_lock:
        vehicle_columns, vehicle_rows = _read_csv(vehicle_path)
        knob_columns, knob_rows = _read_csv(knob_path)
        mask_columns, mask_rows = _read_csv(mask_path)
        if not mask_columns:
            mask_columns = list(MASK_INFO_COLUMNS)
        mask_reticle_column = _mask_info_column(mask_columns, "reticle_id")
        mask_value_column = _mask_info_column(mask_columns, "mask")
        if not mask_reticle_column:
            mask_reticle_column = "reticle_id"
            mask_columns.append(mask_reticle_column)
        if not mask_value_column:
            mask_value_column = "mask"
            mask_columns.append(mask_value_column)
        if not vehicle_columns:
            vehicle_columns = ["vehicle", "product", "step_id", "step_desc"]
        if not knob_columns:
            knob_columns = ["feature_name", "function_step", "rule_order", "operator", "value", "category"]
        for column in ("vehicle", "product", "step_id", "step_desc"):
            if column not in vehicle_columns:
                vehicle_columns.append(column)
        fs_col = "function_step" if "function_step" in knob_columns else "step_desc"
        if fs_col not in knob_columns:
            knob_columns.append(fs_col)
        for column in ("feature_name", "rule_order", "operator", "value", "category"):
            if column not in knob_columns:
                knob_columns.append(column)

        step_changes = 0
        ppid_changes = 0
        mask_changes = 0
        for kind, alert, raw in normalized:
            if kind != "match_step":
                continue
            step_desc = str(raw.get("step_desc") or "").strip()
            if not step_desc:
                raise ValueError(f"step_desc 가 비어있습니다: {alert.get('id')}")
            vehicle = str(alert.get("vehicle") or alert.get("product") or "").strip()
            product = str(alert.get("product") or "").strip()
            step_id = str(alert.get("step_id") or "").strip()
            duplicate = next((row for row in vehicle_rows
                              if _norm(row.get("product")) == _norm(product)
                              and str(row.get("step_id") or "").strip() == step_id), None)
            if duplicate:
                raise ValueError(f"이미 등록된 매칭: {product} {step_id} → {duplicate.get('step_desc')}")
            new_row = {column: "" for column in vehicle_columns}
            new_row.update({"vehicle": vehicle, "product": product,
                            "step_id": step_id, "step_desc": step_desc})
            vehicle_rows.append(new_row)
            step_changes += 1
            result = {"alert_id": alert["id"], "type": kind, "file": vehicle_path.name,
                      "vehicle": vehicle, "product": product, "step_id": step_id,
                      "step_desc": step_desc, "batch_id": batch_id}
            results.append(result)
            decisions.append({**result, "type": "unmatched_step", "action": "match", "by": actor,
                              "detail": str(raw.get("note") or "").strip() or f"{step_id} → {step_desc}"})

        for kind, alert, raw in normalized:
            if kind != "classify_ppid":
                continue
            category = str(raw.get("category") or "").strip()
            if not category:
                raise ValueError(f"category(분류값)가 비어있습니다: {alert.get('id')}")
            product = str(alert.get("product") or alert.get("vehicle") or "").strip()
            step_id = str(alert.get("step_id") or "").strip()
            step_desc = (_step_desc_from_rows(vehicle_rows, product, step_id)
                         or str(alert.get("step_desc") or "").strip())
            if not step_desc:
                raise ValueError("이 PPID의 step_id가 아직 Vehicle_matching.csv에 없습니다. 같은 배치에서 step 매칭을 먼저 선택하세요")
            ppid = str(alert.get("ppid") or "").strip()
            feature = str(raw.get("feature_name") or alert.get("feature_name") or "").strip()
            same_step = [row for row in knob_rows if str(row.get(fs_col) or "").strip() == step_desc]
            if not feature:
                feature = str((same_step[0].get("feature_name") if same_step else step_desc) or step_desc).strip()
            same_indexes = [i for i, row in enumerate(knob_rows)
                            if str(row.get("feature_name") or "").strip() == feature]
            duplicate = next((knob_rows[i] for i in same_indexes
                              if str(knob_rows[i].get("rule_order") or "").strip().upper() != "RO"
                              and str(knob_rows[i].get("value") or "").strip() == ppid), None)
            if duplicate:
                raise ValueError(f"이미 등록된 룰: {feature} {duplicate.get('rule_order')} {ppid}")
            max_rule = 0
            ro_position = None
            for index in same_indexes:
                order = str(knob_rows[index].get("rule_order") or "").strip().upper()
                if order == "RO":
                    ro_position = index if ro_position is None else min(ro_position, index)
                elif order.startswith("R") and order[1:].isdigit():
                    max_rule = max(max_rule, int(order[1:]))
            rule_order = f"R{max_rule + 1}"
            new_row = {column: "" for column in knob_columns}
            new_row.update({"feature_name": feature, fs_col: step_desc, "rule_order": rule_order,
                            "operator": "eq", "value": ppid, "category": category})
            if "use" in knob_columns:
                new_row["use"] = "Y"
            if ro_position is not None:
                knob_rows.insert(ro_position, new_row)
            elif same_indexes:
                knob_rows.insert(same_indexes[-1] + 1, new_row)
            else:
                knob_rows.append(new_row)
            ppid_changes += 1
            result = {"alert_id": alert["id"], "type": kind, "file": knob_path.name,
                      "vehicle": alert.get("vehicle"), "product": alert.get("product"),
                      "step_id": step_id, "step_desc": step_desc, "ppid": ppid,
                      "feature_name": feature, "rule_order": rule_order, "category": category,
                      "batch_id": batch_id}
            results.append(result)
            decisions.append({**result, "type": "ro_ppid", "action": "classify", "by": actor,
                              "detail": str(raw.get("note") or "").strip() or f"{ppid} → {category}"})

        for kind, alert, raw in normalized:
            if kind != "add_mask":
                continue
            mask = str(raw.get("mask") or "").strip()
            if not mask:
                raise ValueError(f"mask(마스크 이름)가 비어있습니다: {alert.get('id')}")
            reticle_id = str(alert.get("reticle_id") or "").strip()
            if not reticle_id:
                raise ValueError(f"reticle_id 가 비어있습니다: {alert.get('id')}")
            duplicate = next((row for row in mask_rows
                              if _norm(row.get(mask_reticle_column)) == _norm(reticle_id)), None)
            if duplicate:
                raise ValueError(
                    f"이미 등록된 reticle: {reticle_id} → {duplicate.get(mask_value_column)}")
            new_row = {column: "" for column in mask_columns}
            new_row.update({mask_reticle_column: reticle_id, mask_value_column: mask})
            mask_rows.append(new_row)
            mask_changes += 1
            result = {"alert_id": alert["id"], "type": kind, "file": mask_path.name,
                      "product": alert.get("product"),
                      "products": list(alert.get("products") or []),
                      "reticle_id": reticle_id, "mask": mask, "batch_id": batch_id}
            results.append(result)
            decisions.append({**result, "type": "missing_reticle", "action": "add_mask", "by": actor,
                              "detail": str(raw.get("note") or "").strip() or f"{reticle_id} → {mask}"})

        written: list[tuple[Path, bytes | None]] = []
        try:
            if step_changes:
                written.append((vehicle_path, vehicle_path.read_bytes() if vehicle_path.exists() else None))
                _write_csv_atomic(vehicle_path, vehicle_columns, vehicle_rows)
            if ppid_changes:
                written.append((knob_path, knob_path.read_bytes() if knob_path.exists() else None))
                _write_csv_atomic(knob_path, knob_columns, knob_rows)
            if mask_changes:
                written.append((mask_path, mask_path.read_bytes() if mask_path.exists() else None))
                _write_csv_atomic(mask_path, mask_columns, mask_rows)
        except Exception:
            for path, original in reversed(written):
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    temporary = path.with_suffix(path.suffix + ".rollback.tmp")
                    temporary.write_bytes(original)
                    temporary.replace(path)
            raise

        summary = (f"[FAB 매칭 검사 일괄] {batch_id}: step {step_changes}건, "
                   f"PPID {ppid_changes}건, mask {mask_changes}건")
        if batch_note:
            summary += f" | {batch_note}"
        if step_changes:
            posts[vehicle_path.name] = _post_write(vehicle_path, actor, summary)
        if ppid_changes:
            posts[knob_path.name] = _post_write(knob_path, actor, summary)
        if mask_changes:
            posts[mask_path.name] = _post_write(mask_path, actor, summary)

    for decision in decisions:
        _append_decision(decision)
    request_scan()
    return {"ok": True, "batch_id": batch_id, "count": len(results),
            "step_count": step_changes, "ppid_count": ppid_changes,
            "mask_count": mask_changes,
            "results": results, "files": posts}


def classify_ro_ppid(alert_id: str, category: str, feature_name: str = "",
                     note: str = "", username: str = "") -> dict:
    batch = apply_batch([{"type": "classify_ppid", "id": alert_id, "category": category,
                          "feature_name": feature_name, "note": note}], username=username)
    result = batch["results"][0]
    return {"ok": True, **result, **batch["files"].get(result["file"], {})}


def match_step(alert_id: str, step_desc: str = "", note: str = "", username: str = "") -> dict:
    batch = apply_batch([{"type": "match_step", "id": alert_id, "step_desc": step_desc,
                          "note": note}], username=username)
    result = batch["results"][0]
    return {"ok": True, **result, **batch["files"].get(result["file"], {})}


def add_mask(alert_id: str, mask: str, note: str = "", username: str = "") -> dict:
    batch = apply_batch([{"type": "add_mask", "id": alert_id, "mask": mask, "note": note}],
                        username=username)
    result = batch["results"][0]
    return {"ok": True, **result, **batch["files"].get(result["file"], {})}


def hold_alert(alert_id: str, status: str, note: str = "", username: str = "") -> dict:
    if status not in {"반영불필요", "active", ""}:
        raise ValueError(f"허용되지 않는 상태: {status}")
    alert = _find_alert(alert_id)
    if not alert:
        raise LookupError(f"알람을 찾을 수 없음: {alert_id}")
    actor = username or "flow"
    set_ack(alert_id, status, note=note, by=actor)
    _append_decision({"alert_id": alert_id, "type": "ack",
                      "product": alert.get("product"),
                      "products": list(alert.get("products") or []),
                      "action": status or "active", "by": actor, "detail": note})
    return {"ok": True, "alert_id": alert_id, "status": status or "active"}


def poll_once() -> dict:
    """Queue a manual scan; never read large FAB sources in the HTTP request."""
    return request_scan()


def _loop_once() -> None:
    cfg = load_cfg()
    state = _load_state()
    requested = bool(state.get("scan_requested"))
    if cfg.get("enabled") or requested:
        try:
            from core import worker_dispatch
            worker_dispatch.run_heavy(
                "fab_matching_alert_scan",
                {"force": requested},
                lambda: scan_next_product(force=requested),
                timeout_sec=3600.0,
                label="FAB matching alert scan",
                local_idle_only=True,
                local_fallback=True,
                durable=False,
                priority="maintenance",
                dedupe_key="fab-matching-alert-scan",
            )
        except Exception:
            logger.exception("FAB matching scan dispatch failed; running on owner")
            scan_next_product(force=requested)
    # 새 FAB 스캐너가 추천 기록을 읽기만 하고 만들지는 않던 누락을 보완한다.
    # advisor 자체의 max_alerts_per_run 상한은 그대로 지키고, backlog가 있을 때만
    # idle 구간에서 다음 배치를 이어간다.
    recommendation = _run_recommendation_batch()
    recommendation_remaining = int(recommendation.get("skipped") or 0)
    next_recommendation_ts = time.time() + RECOMMENDATION_BATCH_INTERVAL_SECONDS
    # Wake frequently enough for an API-side manual request, while the
    # normal interval is still measured from the last completed scan.
    deadline = time.time() + int(cfg.get("scan_interval_seconds") or DEFAULT_SCAN_INTERVAL_SECONDS)
    _scanner_beat(state="idle", product="", next_scan_ts=deadline)
    while time.time() < deadline:
        time.sleep(min(SCANNER_BEAT_SECONDS, max(1, deadline - time.time())))
        _scanner_beat(state="idle", next_scan_ts=deadline)
        if _load_state().get("scan_requested"):
            break
        if recommendation_remaining and time.time() >= next_recommendation_ts:
            recommendation = _run_recommendation_batch()
            recommendation_remaining = int(recommendation.get("skipped") or 0)
            next_recommendation_ts = time.time() + RECOMMENDATION_BATCH_INTERVAL_SECONDS


def _loop() -> None:
    logger.info("[fab_matching_alerts] owner-controlled scanner started")
    _scanner_beat(state="idle", started_ts=time.time(), product="",
                  files_done=0, files_total=0, last_error="")
    while True:
        if not _scheduler_owner_enabled():
            time.sleep(5)
            continue
        try:
            _loop_once()
        except Exception:
            # 스레드는 절대 죽이지 않는다 — 예전에는 루프 본문(설정/상태 읽기)에서
            # 예외가 나면 스캐너가 조용히 사라지고, 화면에는 요청만 쌓였다.
            logger.exception("[fab_matching_alerts] scan loop failed")
            _scanner_beat(state="idle", last_error="scan loop failed (로그 확인)")
            time.sleep(30)


def _ensure_scheduler_running() -> bool:
    """Start the scanner thread late only on the elected scheduler owner."""
    if _started and _thread is not None and _thread.is_alive():
        return True
    if not _scheduler_owner_enabled():
        return False
    try:
        start_scheduler()
    except Exception:
        logger.exception("FAB matching scanner late start failed")
        return False
    return bool(_started)


def _development_worker_enabled() -> bool:
    """True only on the designated development worker.

    Role resolution is intentionally fail-closed.  The default/api role and a
    role lookup error must never touch the large FAB parquet sources.
    """
    try:
        from core.worker_dispatch import server_role
        return server_role() == "worker"
    except Exception:
        return False


def _scheduler_owner_enabled() -> bool:
    try:
        from core.worker_dispatch import server_role
        return server_role() == "worker"
    except Exception:
        return False


def start_scheduler() -> None:
    """Start only on the development worker."""
    global _thread, _started
    if not _scheduler_owner_enabled():
        logger.info("FAB matching scanner not started: development worker role required")
        return
    with _lock:
        # 죽은 스레드는 다시 띄운다 — _started 만 보면 한 번 죽은 뒤 영영
        # 살아나지 않는다.
        if _started and _thread is not None and _thread.is_alive():
            return
        if not CFG_PATH.exists():
            _atomic_json(CFG_PATH, DEFAULT_CFG)
        _thread = threading.Thread(target=_loop, name="fab-matching-alerts", daemon=True)
        _thread.start()
        _started = True
