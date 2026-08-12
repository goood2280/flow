"""Auto report durable queue integration and development-worker runner.

The production API only creates a shared worker task.  A server configured as
the Flow development worker prepares ET/INLINE/FAB data, launches the legacy
``_TRIGGER_<vehicle>_<lot>_<step>`` entry point, and publishes the resulting
PPTX back into the shared Flow data root.

Runtime code and presentation assets are operator-owned and are read from
``<DB root>/Auto report``.  Vehicle reformatter CSV files remain in the normal
``<DB root>/reformatter`` (with ``data_root/reformatter`` as compatibility
fallback), so the two sources are deliberately not coupled.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from core.paths import PATHS
from core.utils import jsonl_append

JOB_ROOT_NAME = "auto_report"
ASSET_DIR_NAME = "Auto report"
HISTORY_DIR_NAME = "ET_HISTORY"
TASK_TYPE = "auto_report_generate"
JOB_TIMEOUT_SEC = 7 * 24 * 3600
RUN_TIMEOUT_SEC = 6 * 3600
REQUIRED_CODE = ("Main.py", "My_Function.py", "My_config.py", "anomaly_engine.py")
REQUIRED_ASSETS = (
    "INLINE_1_reformatter.xlsx",
    "SF3_Data_Extractor_Input_File_v0.xlsx",
    "HOL_Auto_Report_Description.pptx",
)
_SAFE_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_JOB_LOCK = threading.RLock()
_RUN_LOCK = threading.Lock()


def _job_root() -> Path:
    return PATHS.data_root / JOB_ROOT_NAME


def _jobs_dir() -> Path:
    return _job_root() / "jobs"


def _runtime_dir(job_id: str) -> Path:
    return _job_root() / "runtime" / job_id


def _output_dir(job_id: str) -> Path:
    return _job_root() / "output" / job_id


def _job_path(job_id: str) -> Path:
    return _jobs_dir() / f"{job_id}.json"


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}.{threading.get_ident()}")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), "utf-8")
    os.replace(tmp, path)


def read_job(job_id: str) -> dict:
    if not _SAFE_PART.fullmatch(str(job_id or "")):
        return {}
    try:
        data = json.loads(_job_path(job_id).read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def update_job(job_id: str, **fields: Any) -> dict:
    with _JOB_LOCK:
        row = read_job(job_id)
        if not row:
            row = {"id": job_id, "created_at": dt.datetime.now().isoformat(timespec="seconds")}
        row.update(fields)
        row["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
        _atomic_json(_job_path(job_id), row)
        return row


def list_jobs(username: str, is_admin: bool = False, limit: int = 100) -> list[dict]:
    rows: list[dict] = []
    try:
        paths = sorted(_jobs_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        paths = []
    for path in paths:
        try:
            row = json.loads(path.read_text("utf-8"))
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        if not is_admin and str(row.get("username") or "") != str(username or ""):
            continue
        row = refresh_job(row)
        rows.append(public_job(row))
        if len(rows) >= max(1, min(int(limit or 100), 500)):
            break
    return rows


def public_job(row: dict) -> dict:
    out = {k: v for k, v in row.items() if k not in {"output_path", "runtime_dir"}}
    output = Path(str(row.get("output_path") or ""))
    out["download_ready"] = bool(output.is_file())
    if output.is_file():
        out["size_mb"] = round(output.stat().st_size / 1024 / 1024, 2)
    return out


def parse_key(raw_key: str) -> dict:
    raw = str(raw_key or "").strip()
    for prefix in ("_TRIGGER_", "TRIGGER_"):
        if raw.upper().startswith(prefix):
            raw = raw[len(prefix):]
            break
    parts = raw.rsplit("_", 2)
    if len(parts) != 3:
        raise ValueError("key 형식은 <제품>_<LOT ID>_<STEP ID> 이어야 합니다")
    product, lot_id, step_id = (part.strip() for part in parts)
    for label, value in (("제품", product), ("LOT ID", lot_id), ("STEP ID", step_id)):
        if not value or not _SAFE_PART.fullmatch(value):
            raise ValueError(f"{label} 형식이 올바르지 않습니다: {value!r}")
    return {
        "key": f"{product}_{lot_id}_{step_id}",
        "product": product,
        "lot_id": lot_id,
        "step_id": step_id,
        "trigger": f"_TRIGGER_{product}_{lot_id}_{step_id}",
    }


def asset_dir() -> Path:
    root = PATHS.db_root
    exact = root / ASSET_DIR_NAME
    if exact.is_dir():
        return exact
    try:
        return next((p for p in root.iterdir() if p.is_dir() and p.name.casefold() == ASSET_DIR_NAME.casefold()), exact)
    except OSError:
        return exact


def managed_run_root() -> Path:
    """Operator-visible Auto report outputs under the shared DB folder."""
    return asset_dir() / "RUN"


def _history_root() -> Path:
    return managed_run_root() / HISTORY_DIR_NAME


def history_file(vehicle: str) -> Path:
    return _history_root() / str(vehicle) / "history.parquet"


def _history_state_path() -> Path:
    return _history_root() / "state.json"


def history_status() -> dict:
    try:
        value = json.loads(_history_state_path().read_text("utf-8"))
        state = value if isinstance(value, dict) else {}
    except Exception:
        state = {}
    state.setdefault("state", "never")
    state["root"] = str(_history_root())
    state["products"] = state.get("products") if isinstance(state.get("products"), dict) else {}
    return state


def _config_path(base: Path | None = None) -> Path:
    base = base or asset_dir()
    for path in (base / "config.yaml", base / "reformatter" / "config.yaml"):
        if path.is_file():
            return path
    return base / "config.yaml"


def _config_data(base: Path | None = None) -> dict:
    path = _config_path(base)
    try:
        import yaml

        data = yaml.safe_load(path.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _reformatter_dirs() -> list[Path]:
    return [PATHS.db_root / "reformatter", PATHS.data_root / "reformatter"]


def _find_reformatter(product: str) -> Path | None:
    from core.vehicle_reformatter import find_vehicle_csv

    for directory in _reformatter_dirs():
        found = find_vehicle_csv(directory, product)
        if found is not None:
            return found
    return None


def product_keys() -> list[str]:
    # Main.py loads the trigger's first segment as a config.yaml key. Showing
    # unrelated reformatter names here would offer keys that can never run.
    configured = {str(k).strip() for k in _config_data().keys() if str(k).strip()}
    if configured:
        return sorted(configured, key=str.casefold)
    keys: set[str] = set()
    for directory in _reformatter_dirs():
        try:
            for path in directory.glob("*_reformatter.csv"):
                keys.add(path.name[: -len("_reformatter.csv")])
        except OSError:
            pass
    return sorted(keys, key=str.casefold)


def preflight(product: str = "") -> dict:
    base = asset_dir()
    missing = [name for name in (*REQUIRED_CODE, *REQUIRED_ASSETS) if not (base / name).is_file()]
    config_path = _config_path(base)
    config = _config_data(base)
    if not config_path.is_file():
        missing.append("config.yaml")
    settings = config.get(product) if product else None
    if product and product not in config:
        missing.append(f"config.yaml:{product}")
    vehicle = str(settings.get("vehicle") or product).strip() if isinstance(settings, dict) else product
    ref = (_find_reformatter(product) or _find_reformatter(vehicle)) if product else None
    if product and ref is None:
        missing.append(f"reformatter/{product}_reformatter.csv")
    return {
        "ok": base.is_dir() and not missing,
        "asset_dir": str(base),
        "reformatter": str(ref or ""),
        "missing": missing,
        "products": product_keys(),
    }


def enqueue(raw_key: str, username: str) -> dict:
    from core import worker_dispatch

    parsed = parse_key(raw_key)
    check = preflight(parsed["product"])
    if not check["ok"]:
        raise FileNotFoundError("Auto report 실행 파일 확인 필요: " + ", ".join(check["missing"]))
    job_id = f"ar-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    row = update_job(
        job_id,
        **parsed,
        username=str(username or "anonymous"),
        state="queued",
        phase="개발 서버 대기열에 전달 중",
        download_count=0,
    )
    submitted = worker_dispatch.submit_async(
        TASK_TYPE,
        {"job_id": job_id},
        timeout_sec=JOB_TIMEOUT_SEC,
        priority="normal",
    )
    if not submitted.get("ok"):
        update_job(job_id, state="failed", phase="큐 전달 실패", error=submitted.get("error") or "queue error")
        raise RuntimeError(str(submitted.get("error") or "queue submission failed"))
    row = update_job(
        job_id,
        task_id=submitted["task_id"],
        phase="개발 서버 실행 대기",
        worker_alive_at_submit=bool(submitted.get("worker_alive")),
    )
    return public_job(row)


def refresh_job(row: dict) -> dict:
    if row.get("state") not in {"queued", "running"} or not row.get("task_id"):
        return row
    try:
        from core import worker_dispatch

        snap = worker_dispatch.async_status(str(row["task_id"]))
    except Exception:
        return row
    state = snap.get("state")
    if state == "running" and row.get("state") == "queued":
        return update_job(str(row["id"]), state="running", phase="개발 서버에서 실행 중")
    if state == "failed":
        result = snap.get("result") or {}
        return update_job(str(row["id"]), state="failed", phase="개발 서버 실행 실패", error=result.get("error") or "worker failed")
    return row


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _product_dir(kind: str, product: str) -> Path | None:
    root = PATHS.db_root
    candidates = [root / f"1.RAWDATA_DB_{kind.upper()}", root / kind.upper()]
    dirs: list[Path] = []
    for parent in candidates:
        if parent.is_dir():
            dirs.extend(p for p in parent.iterdir() if p.is_dir())
    wanted = _norm(product)
    for path in dirs:
        if _norm(path.name) == wanted:
            return path
    fuzzy = [p for p in dirs if _norm(p.name) in wanted or wanted in _norm(p.name)]
    return sorted(fuzzy, key=lambda p: len(p.name))[0] if fuzzy else None


def _parquet_files(kind: str, product: str) -> list[Path]:
    directory = _product_dir(kind, product)
    return sorted(directory.rglob("*.parquet")) if directory else []


def _sql_files(paths: list[Path]) -> str:
    return "[" + ",".join("'" + str(p).replace("\\", "/").replace("'", "''") + "'" for p in paths) + "]"


def _sql_path(path: Path) -> str:
    return str(path).replace("\\", "/").replace("'", "''")


def _columns(con, files: list[Path]) -> dict[str, str]:
    rows = con.execute(f"DESCRIBE SELECT * FROM read_parquet({_sql_files(files)}, union_by_name=true)").fetchall()
    return {str(row[0]).casefold(): str(row[0]) for row in rows}


def _q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _pick(cols: dict[str, str], *names: str, default: str = "NULL") -> str:
    for name in names:
        actual = cols.get(name.casefold())
        if actual:
            return _q(actual)
    return default


def _copy_asset_tree(base: Path, runtime: Path) -> None:
    runtime.mkdir(parents=True, exist_ok=True)
    for source in base.iterdir():
        if source.name.casefold() in {"reformatter", "run", "__pycache__", ".git", ".env"}:
            continue
        if source.is_dir():
            shutil.copytree(source, runtime / source.name, dirs_exist_ok=True)
        elif source.is_file() and not source.name.endswith(".bak"):
            shutil.copy2(source, runtime / source.name)
    ref_dir = runtime / "reformatter"
    ref_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_config_path(base), ref_dir / "config.yaml")
    seen: set[str] = set()
    for directory in _reformatter_dirs():
        if not directory.is_dir():
            continue
        for source in directory.glob("*_reformatter.csv"):
            if source.name.casefold() in seen:
                continue
            seen.add(source.name.casefold())
            shutil.copy2(source, ref_dir / source.name)


def _stage_et(
    con,
    runtime: Path,
    product: str,
    lot_id: str,
    days: int = 120,
    source_files: list[Path] | None = None,
) -> Path:
    files = list(source_files) if source_files is not None else _parquet_files("ET", product)
    if not files:
        raise FileNotFoundError(f"ET parquet를 찾을 수 없습니다: {product}")
    cols = _columns(con, files)
    lot = _pick(cols, "fab_lot_id", "lot_id", "root_lot_id", default="''")
    root_lot = _pick(cols, "root_lot_id", "lot_id", default=lot)
    tkout = _pick(cols, "tkout_time", default="NULL")
    value = _pick(cols, "et_value", "value", "fab_value", default="NULL")
    shot_x = _pick(cols, "chip_x_pos", "shot_x", default="0")
    shot_y = _pick(cols, "chip_y_pos", "shot_y", default="0")
    out_dir = runtime / "RUN" / "DB" / f"{product}_daily" / f"date={dt.date.today().isoformat()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / "data.parquet"
    select = {
        "fab_lot_id": lot,
        "lot_id": _pick(cols, "lot_id", default=lot),
        "root_lot_id": root_lot,
        "wafer_id": _pick(cols, "wafer_id", default="''"),
        "process_id": _pick(cols, "process_id", default="''"),
        "part_id": _pick(cols, "part_id", default="''"),
        "step_id": _pick(cols, "step_id", default="''"),
        "step_seq": _pick(cols, "step_seq", "step_id", default="''"),
        "tkin_time": _pick(cols, "tkin_time", default=tkout),
        "tkout_time": tkout,
        "flat_zone": _pick(cols, "flat_zone", "flat", default="0"),
        "eqp_id": _pick(cols, "eqp_id", default="''"),
        "probe_card_id": _pick(cols, "probe_card_id", default="''"),
        "chip_x_pos": shot_x,
        "chip_y_pos": shot_y,
        "subitem_id": _pick(cols, "subitem_id", default="''"),
        "temperature": _pick(cols, "temperature", default="25"),
        "total_site_cnt": _pick(cols, "total_site_cnt", default="1"),
        "item_id": _pick(cols, "item_id", default="''"),
        "et_value": value,
    }
    select_sql = ",\n".join(f"{expr} AS {_q(alias)}" for alias, expr in select.items())
    safe_lot = lot_id.replace("'", "''")
    days = max(1, min(int(days or 120), 3650))
    recent = f"try_cast({tkout} AS TIMESTAMP) >= current_timestamp - INTERVAL '{days} days'"
    where = f"({recent} OR cast({lot} AS VARCHAR) = '{safe_lot}' OR cast({root_lot} AS VARCHAR) = '{safe_lot}')"
    con.execute(
        f"COPY (SELECT {select_sql} FROM read_parquet({_sql_files(files)}, union_by_name=true) WHERE {where}) "
        f"TO '{_sql_path(output)}' (FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    count = con.execute(f"SELECT count(*) FROM read_parquet('{_sql_path(output)}')").fetchone()[0]
    if not count:
        raise ValueError(f"ET 데이터가 없습니다: {product}/{lot_id}")
    return output


def _stage_logs(con, runtime: Path, product: str, et_file: Path) -> None:
    log_dir = runtime / "RUN" / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    rows = con.execute(
        f"""SELECT fab_lot_id, step_id, cast(max(tkout_time) AS VARCHAR),
                   list(distinct try_cast(wafer_id AS INTEGER)),
                   list(distinct step_seq), list(distinct try_cast(total_site_cnt AS INTEGER))
              FROM read_parquet('{_sql_path(et_file)}')
             GROUP BY fab_lot_id, step_id"""
    ).fetchall()
    with open(log_dir / f"{product}_et_log.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["prime_key", "wafer_id", "step_seq", "total_site_cnt", "tkout_time"])
        for lot, step, tkout, wafers, steps, totals in rows:
            writer.writerow([f"{product}_{lot}_{step}", repr([x for x in wafers if x is not None]), repr([x for x in steps if x is not None]), repr([x for x in totals if x is not None]), tkout])


def _publish_atomic(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + f".tmp.{os.getpid()}.{threading.get_ident()}")
    shutil.copy2(source, temp)
    os.replace(temp, target)


def refresh_history_product(vehicle: str, days: int = 120) -> dict:
    """Build a rolling ET history snapshot directly from Flow ET."""
    import duckdb

    clean_vehicle = str(vehicle or "").strip()
    if not clean_vehicle:
        raise ValueError("history vehicle이 비어 있습니다")
    base = PATHS.data_root
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="auto-report-history-", dir=str(base)) as raw:
        runtime = Path(raw)
        con = duckdb.connect()
        try:
            staged = _stage_et(con, runtime, clean_vehicle, "__FLOW_HISTORY__", days)
            _stage_logs(con, runtime, clean_vehicle, staged)
            row_count = int(con.execute(
                f"SELECT count(*) FROM read_parquet('{_sql_path(staged)}')"
            ).fetchone()[0])
        finally:
            con.close()
        target = history_file(clean_vehicle)
        _publish_atomic(staged, target)
        log_source = runtime / "RUN" / "log" / f"{clean_vehicle}_et_log.csv"
        if log_source.is_file():
            _publish_atomic(log_source, target.parent / "et_log.csv")
    return {
        "ok": True,
        "vehicle": clean_vehicle,
        "days": int(days),
        "rows": row_count,
        "path": str(target),
        "size_mb": round(target.stat().st_size / 1024 / 1024, 2),
        "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
    }


def refresh_all_histories() -> dict:
    """Refresh configured main/comparison vehicles serially on the dev worker."""
    config = _config_data()
    default_days = max(1, min(int(os.environ.get("FLOW_AUTO_REPORT_HISTORY_DAYS") or 120), 3650))
    plan: dict[str, int] = {}
    for settings in config.values():
        if not isinstance(settings, dict):
            continue
        days = max(default_days, int(settings.get("viewing_period") or default_days))
        vehicle = str(settings.get("vehicle") or "").strip()
        if vehicle:
            plan[vehicle] = max(plan.get(vehicle, 0), days)
        comparisons = settings.get("with_vehicle") or []
        if isinstance(comparisons, str):
            comparisons = [comparisons]
        for comparison in comparisons:
            value = str(comparison or "").strip()
            if value:
                plan[value] = max(plan.get(value, 0), days)
    if not plan:
        state = {
            "state": "failed", "ok": False, "error": "config.yaml에 history 제품이 없습니다",
            "finished_at": dt.datetime.now().isoformat(timespec="seconds"), "finished_ts": time.time(),
            "products": {},
        }
        _atomic_json(_history_state_path(), state)
        return state

    state = {
        "state": "running", "ok": True,
        "started_at": dt.datetime.now().isoformat(timespec="seconds"),
        "started_ts": time.time(), "current": "", "products": {},
    }
    _atomic_json(_history_state_path(), state)
    for vehicle, days in plan.items():
        state["current"] = vehicle
        _atomic_json(_history_state_path(), state)
        try:
            state["products"][vehicle] = refresh_history_product(vehicle, days)
        except Exception as exc:
            state["ok"] = False
            state["products"][vehicle] = {
                "ok": False,
                "vehicle": vehicle,
                "error": f"{type(exc).__name__}: {exc}",
                "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
            }
    state.update({
        "state": "completed" if state["ok"] else "failed",
        "current": "",
        "finished_at": dt.datetime.now().isoformat(timespec="seconds"),
        "finished_ts": time.time(),
    })
    _atomic_json(_history_state_path(), state)
    return state


def _stage_wip(con, runtime: Path, product: str, et_file: Path) -> None:
    db_dir = runtime / "RUN" / "DB"
    db_dir.mkdir(parents=True, exist_ok=True)
    output = db_dir / f"{product}_wip_current.csv"
    fab_files = _parquet_files("FAB", product)
    if fab_files:
        cols = _columns(con, fab_files)
        lot = _pick(cols, "lot_id", "root_lot_id", default="''")
        step = _pick(cols, "step_id", default="''")
        updated = _pick(cols, "tkout_time", "last_update_date", default="current_timestamp")
        source = f"read_parquet({_sql_files(fab_files)}, union_by_name=true)"
    else:
        lot, step, updated = "lot_id", "step_id", "tkout_time"
        source = f"read_parquet('{_sql_path(et_file)}')"
    relevant_lots = (
        f"SELECT cast(fab_lot_id AS VARCHAR) FROM read_parquet('{_sql_path(et_file)}') "
        f"UNION SELECT cast(lot_id AS VARCHAR) FROM read_parquet('{_sql_path(et_file)}') "
        f"UNION SELECT cast(root_lot_id AS VARCHAR) FROM read_parquet('{_sql_path(et_file)}')"
    )
    con.execute(
        f"COPY (SELECT cast({lot} AS VARCHAR) lot_id, cast({step} AS VARCHAR) step_id, "
        f"cast({updated} AS VARCHAR) last_update_date FROM {source} "
        f"WHERE cast({lot} AS VARCHAR) IN ({relevant_lots})) "
        f"TO '{_sql_path(output)}' (FORMAT CSV, HEADER, DELIMITER ',')"
    )


def _stage_inline(con, runtime: Path, product: str, lot_ids: list[str]) -> Path:
    output = runtime / "RUN" / "DB" / f"{product}_inline_source.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    files = _parquet_files("INLINE", product)
    if not files:
        output.write_text("root_lot_id,wafer_id,STEP_DESC,item_id,fab_value,tkout_time,spc_ctrl_spec_high,spc_ctrl_spec_limit,spc_ctrl_spec_low\n", "utf-8")
        return output
    cols = _columns(con, files)
    root = _pick(cols, "root_lot_id", "lot_id", default="''")
    lot_expr = _pick(cols, "lot_id", default=root)
    values = [str(value).strip() for value in lot_ids if str(value).strip()]
    safe_values = ",".join("'" + value.replace("'", "''") + "'" for value in dict.fromkeys(values))
    if not safe_values:
        safe_values = "''"
    select = {
        "root_lot_id": root,
        "wafer_id": _pick(cols, "wafer_id", default="''"),
        "STEP_DESC": _pick(cols, "step_seq", "step_id", default="''"),
        "item_id": _pick(cols, "item_id", default="''"),
        "fab_value": _pick(cols, "fab_value", "value", default="NULL"),
        "tkout_time": _pick(cols, "tkout_time", default="NULL"),
        "spc_ctrl_spec_high": _pick(cols, "spc_ctrl_spec_high", default="NULL"),
        "spc_ctrl_spec_limit": _pick(cols, "spc_ctrl_spec_limit", default="NULL"),
        "spc_ctrl_spec_low": _pick(cols, "spc_ctrl_spec_low", default="NULL"),
    }
    select_sql = ",".join(f"{expr} AS {_q(alias)}" for alias, expr in select.items())
    con.execute(
        f"COPY (SELECT {select_sql} FROM read_parquet({_sql_files(files)}, union_by_name=true) "
        f"WHERE cast({root} AS VARCHAR) IN ({safe_values}) OR cast({lot_expr} AS VARCHAR) IN ({safe_values})) "
        f"TO '{_sql_path(output)}' (FORMAT CSV, HEADER, DELIMITER ',')"
    )
    return output


def _prepare_runtime(job: dict) -> tuple[Path, Path]:
    import duckdb

    runtime = _runtime_dir(str(job["id"]))
    if runtime.exists():
        shutil.rmtree(runtime)
    _copy_asset_tree(asset_dir(), runtime)
    settings = _config_data().get(str(job["product"])) or {}
    if not isinstance(settings, dict):
        raise ValueError(f"config.yaml 설정이 객체가 아닙니다: {job['product']}")
    vehicle = str(settings.get("vehicle") or job["product"]).strip()
    viewing_days = int(settings.get("viewing_period") or 120)
    matched_reformatter = _find_reformatter(str(job["product"])) or _find_reformatter(vehicle)
    if matched_reformatter is None:
        raise FileNotFoundError(f"reformatter CSV를 찾을 수 없습니다: {job['product']}")
    shutil.copy2(matched_reformatter, runtime / "reformatter" / f"{vehicle}_reformatter.csv")
    con = duckdb.connect()
    try:
        lot_id = str(job["lot_id"])
        cached_history = history_file(vehicle)
        if cached_history.is_file():
            try:
                et_file = _stage_et(
                    con, runtime, vehicle, lot_id, viewing_days,
                    source_files=[cached_history],
                )
            except (FileNotFoundError, ValueError):
                # A requested historical lot may sit outside the rolling
                # snapshot. Fall back to Flow ET for that one report only.
                et_file = _stage_et(con, runtime, vehicle, lot_id, viewing_days)
        else:
            et_file = _stage_et(con, runtime, vehicle, lot_id, viewing_days)
        safe_lot = lot_id.replace("'", "''")
        roots = [str(job["lot_id"])]
        roots.extend(
            str(row[0]) for row in con.execute(
                f"SELECT DISTINCT root_lot_id FROM read_parquet('{_sql_path(et_file)}') "
                "WHERE root_lot_id IS NOT NULL AND ("
                f"cast(fab_lot_id AS VARCHAR) = '{safe_lot}' OR "
                f"cast(lot_id AS VARCHAR) = '{safe_lot}' OR "
                f"cast(root_lot_id AS VARCHAR) = '{safe_lot}')"
            ).fetchall()
            if row and row[0] is not None
        )
        with_vehicles = settings.get("with_vehicle") or []
        if isinstance(with_vehicles, str):
            with_vehicles = [with_vehicles]
        for comparison in dict.fromkeys(str(value).strip() for value in with_vehicles if str(value).strip()):
            if comparison == vehicle:
                continue
            comparison_ref = _find_reformatter(comparison)
            if comparison_ref is None:
                raise FileNotFoundError(f"비교 제품 reformatter CSV를 찾을 수 없습니다: {comparison}")
            shutil.copy2(comparison_ref, runtime / "reformatter" / f"{comparison}_reformatter.csv")
            comparison_history = history_file(comparison)
            if comparison_history.is_file():
                try:
                    _stage_et(
                        con, runtime, comparison, lot_id, viewing_days,
                        source_files=[comparison_history],
                    )
                    continue
                except (FileNotFoundError, ValueError):
                    pass
            _stage_et(con, runtime, comparison, lot_id, viewing_days)
        _stage_logs(con, runtime, vehicle, et_file)
        _stage_wip(con, runtime, vehicle, et_file)
        inline_file = _stage_inline(con, runtime, vehicle, roots)
    finally:
        con.close()
    return runtime, inline_file


def generate_job(job_id: str) -> dict:
    """Worker handler: prepare local inputs, run the legacy trigger, publish PPTX."""
    # Defence in depth: the API process is only a durable queue producer.  Even
    # an accidental direct call must not start the legacy renderer in
    # production; only the development worker may consume and execute it.
    from core import worker_dispatch

    if worker_dispatch.server_role() != "worker":
        return {
            "ok": False,
            "error": "auto_report_generation_requires_development_worker",
        }
    with _RUN_LOCK:
        job = read_job(job_id)
        if not job:
            return {"ok": False, "error": f"job not found: {job_id}"}
        update_job(job_id, state="running", phase="ET · INLINE · FAB 입력 준비 중", started_at=dt.datetime.now().isoformat(timespec="seconds"))
        try:
            runtime, inline_file = _prepare_runtime(job)
            update_job(job_id, phase=f"{job['trigger']} 실행 중", runtime_dir=str(runtime))
            log_path = runtime / "auto_report_worker.log"
            backend_root = Path(__file__).resolve().parent.parent
            env = dict(os.environ)
            env["PYTHONPATH"] = str(backend_root) + os.pathsep + env.get("PYTHONPATH", "")
            env["AUTO_REPORT_DISABLE_EXTERNAL"] = "1"
            cmd = [
                sys.executable,
                "-m",
                "core.auto_report_child",
                "--runtime",
                str(runtime),
                "--trigger",
                str(job["trigger"]),
                "--inline",
                str(inline_file),
            ]
            with open(log_path, "w", encoding="utf-8", errors="replace") as log:
                proc = subprocess.run(cmd, cwd=str(runtime), env=env, stdout=log, stderr=subprocess.STDOUT, timeout=RUN_TIMEOUT_SEC, check=False)
            if proc.returncode != 0:
                tail = log_path.read_text("utf-8", errors="replace")[-6000:]
                raise RuntimeError(f"TRIGGER process exited {proc.returncode}\n{tail}")
            report_root = runtime / "RUN" / "Report"
            candidates = sorted(report_root.rglob("*.pptx"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not candidates:
                raise FileNotFoundError("TRIGGER 실행은 끝났지만 생성된 PPTX가 없습니다")
            source = candidates[0]
            publish_settings = _config_data().get(str(job["product"])) or {}
            vehicle = str(publish_settings.get("vehicle") or job["product"]) if isinstance(publish_settings, dict) else str(job["product"])
            ppt_dir = managed_run_root() / "PPTX" / vehicle / job_id
            html_dir = managed_run_root() / "HTML" / vehicle / job_id
            ppt_dir.mkdir(parents=True, exist_ok=True)
            output = ppt_dir / source.name
            _publish_atomic(source, output)
            html_files = sorted(report_root.rglob("*.html"), key=lambda p: p.stat().st_mtime)
            for html_source in html_files:
                _publish_atomic(html_source, html_dir / html_source.name)
            row = update_job(
                job_id,
                state="completed",
                phase="PPT 생성 완료",
                completed_at=dt.datetime.now().isoformat(timespec="seconds"),
                output_path=str(output),
                filename=output.name,
                html_count=len(html_files),
                publish_root=str(managed_run_root()),
                error="",
                log_path=str(log_path),
            )
            return {"ok": True, "job": public_job(row)}
        except Exception as exc:
            update_job(
                job_id,
                state="failed",
                phase="Auto report 생성 실패",
                completed_at=dt.datetime.now().isoformat(timespec="seconds"),
                error=f"{type(exc).__name__}: {exc}"[-8000:],
            )
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def output_for(job_id: str, username: str, is_admin: bool = False) -> tuple[dict, Path]:
    row = refresh_job(read_job(job_id))
    if not row:
        raise FileNotFoundError("작업을 찾을 수 없습니다")
    if not is_admin and str(row.get("username") or "") != str(username or ""):
        raise PermissionError("다른 사용자의 리포트입니다")
    path = Path(str(row.get("output_path") or ""))
    if row.get("state") != "completed" or not path.is_file():
        raise FileNotFoundError("다운로드할 PPT가 아직 준비되지 않았습니다")
    return row, path


def record_download(job: dict, path: Path, username: str) -> None:
    size_mb = round(path.stat().st_size / 1024 / 1024, 2)
    jsonl_append(PATHS.download_log, {
        "source": "auto_report",
        "username": str(username or "anonymous"),
        "product": str(job.get("product") or ""),
        "key": str(job.get("key") or ""),
        "filename": path.name,
        "size_mb": size_mb,
        "rows": "-",
        "sql": str(job.get("key") or ""),
        "select_cols": "PPTX",
    })
    update_job(str(job["id"]), download_count=int(job.get("download_count") or 0) + 1, last_downloaded_by=username, last_downloaded_at=dt.datetime.now().isoformat(timespec="seconds"))
