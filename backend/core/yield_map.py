"""Product-scoped yield die-map source discovery, mapping, and BIN colors."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
import threading
from typing import Any

import polars as pl

from core.paths import PATHS
from core.utils import load_json, save_json, scan_one_file


CONFIG_PATH = PATHS.data_root / "yield_map.json"
SOURCE_NAME_RE = re.compile(r"(?:^|[^A-Z0-9])(BIN|MSR)(?:[^A-Z0-9]|$)", re.I)
DATA_EXTENSIONS = {".parquet", ".csv"}
MAX_MAP_ROWS = 100_000
MAX_SOURCE_FILES = 20
_LOCK = threading.RLock()

FIELD_ALIASES = {
    "x": ("chip_x_pos", "die_x", "chip_x", "map_x", "x", "shot_x"),
    "y": ("chip_y_pos", "die_y", "chip_y", "map_y", "y", "shot_y"),
    "bin": ("bin", "bin_id", "hard_bin", "soft_bin", "bin_code", "result_bin"),
    "msr": ("msr", "measurement", "measure", "value", "result"),
    "lot": ("root_lot_id", "lot_id", "lotid", "lot"),
    "wafer": ("wafer_id", "wf_id", "waferid", "wafer"),
    "product": ("product", "vehicle", "mask", "device"),
}


def _default_config() -> dict:
    return {"version": 2, "products": {}}


def load_config() -> dict:
    raw = load_json(CONFIG_PATH, _default_config())
    if not isinstance(raw, dict):
        return _default_config()
    products = raw.get("products")
    return {"version": 2, "products": products if isinstance(products, dict) else {}}


def product_config(product: str) -> dict:
    raw = load_config()["products"].get(str(product or "").strip(), {})
    return raw if isinstance(raw, dict) else {}


def _clean_color(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    if re.fullmatch(r"#[0-9A-F]{6}", text):
        return text
    return None


def _bounded_int(value: Any, default: int, minimum: int = 1, maximum: int = 100) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _finite_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed == parsed and abs(parsed) != float("inf") else default


def _clean_shot_layout(value: Any) -> dict:
    raw = value if isinstance(value, dict) else {}
    good_bins = []
    seen = set()
    for item in raw.get("good_bins") or []:
        name = str(item or "").strip()[:120]
        if name and name not in seen:
            good_bins.append(name)
            seen.add(name)
    return {
        "enabled": bool(raw.get("enabled")),
        "cols": _bounded_int(raw.get("cols"), 1),
        "rows": _bounded_int(raw.get("rows"), 1),
        "origin_x": _finite_float(raw.get("origin_x"), 0.0),
        "origin_y": _finite_float(raw.get("origin_y"), 0.0),
        "good_bins": good_bins[:200],
    }


def save_product_config(product: str, payload: dict) -> dict:
    name = str(product or "").strip()
    if not name:
        raise ValueError("제품이 비어 있습니다")
    source = str((payload or {}).get("source") or "").strip().replace("\\", "/")
    if source:
        resolve_source(source)
    fields_in = (payload or {}).get("fields") or {}
    fields = {}
    for key in FIELD_ALIASES:
        value = str(fields_in.get(key) or "").strip()
        if value:
            fields[key] = value[:160]
    colors = {}
    bin_map = []
    table_rows = (payload or {}).get("bin_map")
    if isinstance(table_rows, list) and table_rows:
        for row in table_rows:
            if not isinstance(row, dict) or len(bin_map) >= 200:
                continue
            bin_name = str(row.get("bin") or "").strip()
            color = _clean_color(row.get("bin_color") or row.get("color"))
            if not bin_name or not color:
                continue
            bin_name = bin_name[:120]
            if bin_name in colors:
                raise ValueError(f"BIN MAP에 중복 BIN이 있습니다: {bin_name}")
            colors[bin_name] = color
            bin_map.append({"bin": bin_name, "bin_color": color})
    else:
        # v1 호환: 제품별 색상이 객체로만 저장돼 있던 설정을 표 행으로 승격한다.
        for key, value in ((payload or {}).get("bin_colors") or {}).items():
            bin_name = str(key or "").strip()
            color = _clean_color(value)
            if bin_name and color and len(bin_map) < 200:
                bin_name = bin_name[:120]
                colors[bin_name] = color
                bin_map.append({"bin": bin_name, "bin_color": color})
    clean = {
        "source": source,
        "fields": fields,
        "bin_map": bin_map,
        "bin_colors": colors,
        "shot_layout": _clean_shot_layout((payload or {}).get("shot_layout")),
    }
    with _LOCK:
        cfg = load_config()
        cfg["products"][name] = clean
        save_json(CONFIG_PATH, cfg, indent=2)
    return clean


def _is_data_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in DATA_EXTENSIONS


def _source_matches(name: str) -> bool:
    upper = str(name or "").upper()
    return bool(SOURCE_NAME_RE.search(upper) or "BIN" in upper or "MSR" in upper)


def _direct_products(path: Path) -> list[str]:
    if not path.is_dir():
        return []
    out = []
    try:
        children = [p for p in path.iterdir() if p.is_dir()]
    except OSError:
        return []
    for child in children:
        name = child.name
        if name.lower().startswith("product="):
            name = name.split("=", 1)[1]
        if name and not name.startswith((".", "_", "date=", "lot=")):
            out.append(name)
    return sorted(set(out), key=str.casefold)


def discover_sources() -> list[dict]:
    root = PATHS.db_root
    if not root.is_dir():
        return []
    found: dict[str, dict] = {}
    try:
        top = sorted(root.iterdir(), key=lambda p: p.name.casefold())
    except OSError:
        return []
    for item in top:
        if item.name.startswith((".", "_")):
            continue
        candidates = [item]
        if item.is_dir():
            try:
                candidates.extend(sorted(item.iterdir(), key=lambda p: p.name.casefold()))
            except OSError:
                pass
        for candidate in candidates:
            if not _source_matches(candidate.stem if candidate.is_file() else candidate.name):
                continue
            if not (candidate.is_dir() or _is_data_file(candidate)):
                continue
            rel = candidate.relative_to(root).as_posix()
            found[rel.casefold()] = {
                "id": rel,
                "name": candidate.stem if candidate.is_file() else candidate.name,
                "kind": "file" if candidate.is_file() else "table",
                "products": _direct_products(candidate),
            }
    return sorted(found.values(), key=lambda row: (row["name"].casefold(), row["id"].casefold()))


def resolve_source(source_id: str) -> Path:
    raw = str(source_id or "").strip().replace("\\", "/")
    if not raw or Path(raw).is_absolute():
        raise ValueError("유효한 BIN/MSR TABLE을 선택해 주세요")
    root = PATHS.db_root.resolve()
    path = (root / raw).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("TABLE 경로가 DB root를 벗어납니다") from exc
    if not path.exists() or not _source_matches(path.stem if path.is_file() else path.name):
        raise ValueError("BIN/MSR 이름과 매칭되는 TABLE을 찾지 못했습니다")
    return path


def _product_dirs(table: Path, product: str) -> list[Path]:
    if table.is_file():
        return []
    product_cf = str(product or "").strip().casefold()
    try:
        children = [p for p in table.iterdir() if p.is_dir()]
    except OSError:
        return []
    hits = []
    for child in children:
        token = child.name.split("=", 1)[1] if child.name.lower().startswith("product=") else child.name
        if product_cf and token.casefold() == product_cf:
            hits.append(child)
    return hits


def source_files(source_id: str, product: str, max_files: int = MAX_SOURCE_FILES) -> list[Path]:
    source = resolve_source(source_id)
    if source.is_file():
        return [source]
    bases = _product_dirs(source, product)
    if not bases:
        # 제품 파티션 TABLE인데 선택 제품이 없으면 다른 제품 파일 전체로 폴백하지
        # 않는다. 직접 파일형 TABLE(제품 열로 구분)일 때만 source 자체를 읽는다.
        if _direct_products(source):
            return []
        bases = [source]
    files = sorted(
        (fp for base in bases for fp in base.rglob("*") if _is_data_file(fp)),
        key=lambda fp: (fp.stat().st_mtime_ns if fp.exists() else 0, str(fp)),
    )
    return files[-max_files:] if max_files and len(files) > max_files else files


def _schema_names(lf: pl.LazyFrame) -> list[str]:
    try:
        return list(lf.collect_schema().names())
    except Exception:
        return list(lf.schema.keys())


def detect_fields(columns: list[str]) -> dict[str, str]:
    by_lower = {str(col).casefold(): str(col) for col in columns}
    out = {}
    for key, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            if alias.casefold() in by_lower:
                out[key] = by_lower[alias.casefold()]
                break
    return out


def preview(source_id: str, product: str) -> dict:
    files = source_files(source_id, product, max_files=1)
    if not files:
        raise FileNotFoundError("선택한 제품의 BIN/MSR TABLE 데이터 파일이 없습니다")
    lf = scan_one_file(files[-1])
    if lf is None:
        raise ValueError("TABLE 파일을 읽지 못했습니다")
    columns = _schema_names(lf)
    sample = lf.head(50).collect().to_dicts()
    return {
        "source": source_id,
        "file": files[-1].name,
        "columns": columns,
        "detected_fields": detect_fields(columns),
        "sample": sample,
    }


def _lazy_source(files: list[Path]) -> pl.LazyFrame | None:
    frames = [scan_one_file(fp) for fp in files]
    frames = [frame for frame in frames if frame is not None]
    if not frames:
        return None
    return frames[0] if len(frames) == 1 else pl.concat(frames, how="diagonal_relaxed")


def _source_rows(product: str, cfg: dict, lot_id: str = "", wafer_id: str = "",
                 limit: int = MAX_MAP_ROWS) -> tuple[list[dict], dict]:
    """Read and normalize BIN rows for one product without applying shot geometry."""
    source_id = str(cfg.get("source") or "")
    if not source_id:
        raise ValueError("이 제품에 BIN/MSR TABLE 설정이 없습니다")
    files = source_files(source_id, product)
    if not files:
        raise FileNotFoundError("선택한 제품의 TABLE 데이터 파일이 없습니다")
    lf = _lazy_source(files)
    if lf is None:
        raise ValueError("TABLE 데이터를 읽지 못했습니다")
    columns = _schema_names(lf)
    fields = {**detect_fields(columns), **(cfg.get("fields") or {})}
    for required in ("x", "y", "bin"):
        if not fields.get(required) or fields[required] not in columns:
            raise ValueError(f"{required.upper()} 열 매핑이 필요합니다")
    lot_col, wafer_col = fields.get("lot"), fields.get("wafer")
    product_col = fields.get("product")
    if product and product_col in columns:
        lf = lf.filter(
            pl.col(product_col).cast(pl.String, strict=False).str.to_lowercase()
            == str(product).lower()
        )
    if lot_id and lot_col in columns:
        lf = lf.filter(pl.col(lot_col).cast(pl.String, strict=False) == str(lot_id))
    if wafer_id and wafer_col in columns:
        lf = lf.filter(pl.col(wafer_col).cast(pl.String, strict=False) == str(wafer_id))
    selected = {key: col for key, col in fields.items() if col in columns}
    exprs = [pl.col(col).alias(key) for key, col in selected.items()]
    df = lf.select(exprs).limit(limit + 1).collect(streaming=True)
    overflow = df.height > limit
    if overflow:
        df = df.head(limit)
    rows = []
    for source_row in df.to_dicts():
        try:
            x, y = float(source_row.get("x")), float(source_row.get("y"))
        except (TypeError, ValueError):
            continue
        if not (x == x and y == y):
            continue
        rows.append({
            "x": x, "y": y,
            "bin": str(source_row.get("bin") if source_row.get("bin") is not None else ""),
            "msr": source_row.get("msr"),
            "lot": source_row.get("lot"),
            "wafer": source_row.get("wafer"),
        })
    return rows, {
        "source": source_id,
        "fields": selected,
        "overflow": overflow,
        "file_count": len(files),
    }


def _integer_coordinate(value: float, *, tolerance: float = 1e-6) -> int | None:
    rounded = round(value)
    return int(rounded) if abs(value - rounded) <= tolerance else None


def apply_shot_layout(rows: list[dict], layout_value: Any) -> tuple[list[dict], list[dict], dict]:
    """Attach in-shot coordinates and aggregate yield for complete shots only."""
    layout = _clean_shot_layout(layout_value)
    if not layout["enabled"]:
        return [dict(row) for row in rows], [], layout
    cols, row_count = layout["cols"], layout["rows"]
    origin_x = _integer_coordinate(layout["origin_x"])
    origin_y = _integer_coordinate(layout["origin_y"])
    if origin_x is None or origin_y is None:
        raise ValueError("Full Shot origin X/Y는 정수 die 좌표여야 합니다")
    expected = cols * row_count
    groups: dict[tuple[str, str, int, int], dict] = {}
    mapped_rows = []
    unassigned = 0
    for source_row in rows:
        row = dict(source_row)
        x = _integer_coordinate(float(row["x"]))
        y = _integer_coordinate(float(row["y"]))
        if x is None or y is None:
            row.update({"shot_x": None, "shot_y": None, "die_x_in_shot": None, "die_y_in_shot": None})
            mapped_rows.append(row)
            unassigned += 1
            continue
        rel_x, rel_y = x - origin_x, y - origin_y
        shot_x, shot_y = rel_x // cols, rel_y // row_count
        die_x, die_y = rel_x % cols, rel_y % row_count
        row.update({
            "shot_x": shot_x, "shot_y": shot_y,
            "die_x_in_shot": die_x, "die_y_in_shot": die_y,
        })
        mapped_rows.append(row)
        lot = "" if row.get("lot") is None else str(row.get("lot"))
        wafer = "" if row.get("wafer") is None else str(row.get("wafer"))
        key = (lot, wafer, shot_x, shot_y)
        group = groups.setdefault(key, {
            "slots": {}, "lot": lot, "wafer": wafer,
            "shot_x": shot_x, "shot_y": shot_y,
        })
        group["slots"][(die_x, die_y)] = row["bin"]

    good_bins = set(layout["good_bins"])
    shot_rows = []
    for group in groups.values():
        bins = list(group["slots"].values())
        observed = len(bins)
        good = sum(1 for value in bins if value in good_bins)
        is_full = observed == expected
        shot_rows.append({
            "lot": group["lot"], "wafer": group["wafer"],
            "shot_x": group["shot_x"], "shot_y": group["shot_y"],
            "good_die": good, "total_die": observed, "expected_die": expected,
            "completion_pct": round(observed * 100.0 / expected, 6),
            "is_full_shot": is_full,
            "shot_yield": round(good * 100.0 / expected, 6) if is_full and good_bins else None,
        })
    shot_rows.sort(key=lambda row: (row["lot"], row["wafer"], row["shot_y"], row["shot_x"]))
    return mapped_rows, shot_rows, {**layout, "expected_die": expected, "unassigned_die": unassigned}


def scan_shot_layout(product: str, payload: dict, lot_id: str = "", wafer_id: str = "") -> dict:
    """Validate an unsaved X/Y scan setup against the selected BIN DB."""
    current = product_config(product)
    candidate = {
        **current,
        "source": str((payload or {}).get("source") or current.get("source") or ""),
        "fields": (payload or {}).get("fields") or current.get("fields") or {},
    }
    rows, meta = _source_rows(product, candidate, lot_id=lot_id, wafer_id=wafer_id)
    mapped, shots, layout = apply_shot_layout(rows, (payload or {}).get("shot_layout"))
    xs = sorted({row["x"] for row in mapped})
    ys = sorted({row["y"] for row in mapped})
    full = [row for row in shots if row["is_full_shot"]]
    partial = [row for row in shots if not row["is_full_shot"]]
    return {
        **meta, "product": product, "layout": layout,
        "scan_rows": len(mapped),
        "coordinate_count": len({(row["x"], row["y"]) for row in mapped}),
        "x": {"min": xs[0] if xs else None, "max": xs[-1] if xs else None, "unique": len(xs)},
        "y": {"min": ys[0] if ys else None, "max": ys[-1] if ys else None, "unique": len(ys)},
        "shot_count": len(shots), "full_shot_count": len(full),
        "partial_shot_count": len(partial),
        "sample_shots": (full[:8] + partial[:8])[:12],
    }


def map_data(product: str, lot_id: str = "", wafer_id: str = "") -> dict:
    cfg = product_config(product)
    rows, meta = _source_rows(product, cfg, lot_id=lot_id, wafer_id=wafer_id)
    rows, shot_rows, shot_layout = apply_shot_layout(rows, cfg.get("shot_layout"))
    counts = Counter(row["bin"] for row in rows)
    net_die = len({(row["x"], row["y"]) for row in rows})
    full_shots = sum(1 for row in shot_rows if row["is_full_shot"])
    return {
        "product": product, "source": meta["source"], "fields": meta["fields"],
        "bin_colors": cfg.get("bin_colors") or {}, "rows": rows,
        "bins": [{"bin": key, "count": value} for key, value in sorted(counts.items())],
        "net_die": net_die, "overflow": meta["overflow"], "file_count": meta["file_count"],
        "shot_layout": shot_layout, "shot_rows": shot_rows,
        "full_shot_count": full_shots, "partial_shot_count": len(shot_rows) - full_shots,
    }


def shot_yield_frame(product: str) -> pl.DataFrame:
    """Chart Builder virtual source: one row per complete shot."""
    result = map_data(product)
    rows = []
    for shot in result.get("shot_rows") or []:
        if not shot.get("is_full_shot") or shot.get("shot_yield") is None:
            continue
        lot = str(shot.get("lot") or "")
        wafer = str(shot.get("wafer") or "")
        rows.append({
            "product": str(product), "root_lot_id": lot, "lot_id": lot,
            "wafer_id": wafer, "shot_x": int(shot["shot_x"]), "shot_y": int(shot["shot_y"]),
            "shot_yield": float(shot["shot_yield"]), "good_die": int(shot["good_die"]),
            "total_die": int(shot["total_die"]), "expected_die": int(shot["expected_die"]),
            "is_full_shot": True,
        })
    schema = {
        "product": pl.String, "root_lot_id": pl.String, "lot_id": pl.String,
        "wafer_id": pl.String, "shot_x": pl.Int64, "shot_y": pl.Int64,
        "shot_yield": pl.Float64, "good_die": pl.Int64, "total_die": pl.Int64,
        "expected_die": pl.Int64, "is_full_shot": pl.Boolean,
    }
    return pl.DataFrame(rows, schema=schema) if rows else pl.DataFrame(schema=schema)
