"""Product-scoped yield die-map source discovery, mapping, and BIN colors."""
from __future__ import annotations

from collections import Counter
import datetime as dt
import math
from pathlib import Path
import re
import threading
from typing import Any

import polars as pl

from core import inline_coordinates
from core.paths import PATHS
from core.utils import load_json, save_json, scan_one_file


CONFIG_PATH = PATHS.data_root / "yield_map.json"
SOURCE_NAME_RE = re.compile(r"(?:^|[^A-Z0-9])BIN(?:[^A-Z0-9]|$)", re.I)
DATA_EXTENSIONS = {".parquet", ".csv"}
MAX_MAP_ROWS = 100_000
MAX_SOURCE_FILES = 20
RELATIONSHIPS_PATH = PATHS.data_root / "yield_map_relationships.json"
SHOT_FIELD_CONFIG_PATH = PATHS.data_root / "yield_map_shot_fields.json"
_LOCK = threading.RLock()

FIELD_ALIASES = {
    "x": ("chip_x_pos", "die_x", "chip_x", "map_x", "x", "shot_x"),
    "y": ("chip_y_pos", "die_y", "chip_y", "map_y", "y", "shot_y"),
    "bin": ("bin", "bin_no", "bin_id", "hard_bin", "soft_bin", "bin_code", "result_bin"),
    "lot": ("root_lot_id", "lot_id", "lotid", "lot"),
    "wafer": ("wafer_id", "wf_id", "waferid", "wafer"),
    "product": ("product", "vehicle", "mask", "device"),
}

SHOT_SOURCE_DIRS = {
    "et": "1.RAWDATA_DB_ET",
    "inline": "1.RAWDATA_DB_INLINE",
}
SHOT_FIELD_ALIASES = {
    "lot": ("root_lot_id", "lot_id", "lotid", "lot"),
    "wafer": ("wafer_id", "wf_id", "waferid", "wafer"),
    "shot_x": ("shot_x", "chip_x_pos", "map_x", "x"),
    "shot_y": ("shot_y", "chip_y_pos", "map_y", "y"),
    "value": ("value", "msr", "measurement", "measure", "result"),
    "item": ("item_id", "itemid", "index", "parameter", "metric"),
    "subitem": ("subitem_id", "subitemid", "site_id", "position_id"),
    "step": ("step_id", "step", "process_step"),
    "step_seq": ("step_seq", "step_sequence", "dcop_step_seq", "measurement_bundle"),
    "split": ("split", "split_id", "split_name", "split_group", "group"),
    "tkout": ("tkout_time", "track_out_time", "out_time", "end_time"),
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
        "vehicle": str((payload or {}).get("vehicle") or "").strip()[:200],
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
    return bool(SOURCE_NAME_RE.search(upper) or "BIN" in upper)


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


def discover_shot_sources() -> dict[str, list[str]]:
    """ET/INLINE DB의 직접 제품 파티션 목록을 WF MAP용으로 반환한다."""
    result: dict[str, list[str]] = {}
    for kind, dirname in SHOT_SOURCE_DIRS.items():
        root = PATHS.db_root / dirname
        names: dict[str, str] = {}
        if root.is_dir():
            try:
                children = [path for path in root.iterdir() if path.is_dir()]
            except OSError:
                children = []
            for child in children:
                name = child.name.split("=", 1)[1] if child.name.lower().startswith("product=") else child.name
                name = str(name or "").strip()
                if name and not name.startswith((".", "_", "date=")):
                    names.setdefault(name.casefold(), name)
        result[kind] = sorted(names.values(), key=str.casefold)
    return result


def discover_split_sources() -> list[dict]:
    """DB root의 ET_TABLE_* split source를 찾는다."""
    root = PATHS.db_root
    if not root.is_dir():
        return []
    found = []
    try:
        children = sorted(root.iterdir(), key=lambda path: path.name.casefold())
    except OSError:
        return []
    for path in children:
        if not path.name.upper().startswith("ET_TABLE_"):
            continue
        if not (path.is_dir() or _is_data_file(path)):
            continue
        found.append({"id": path.relative_to(root).as_posix(), "name": path.stem})
    return found


def _split_rows(source_id: str, root_lot_id: str, wafer_id: str = "") -> list[dict]:
    source = resolve_generic_source(source_id, prefix="ET_TABLE_")
    files = [source] if source.is_file() else sorted(
        (path for path in source.rglob("*") if _is_data_file(path)), key=lambda path: str(path).casefold(),
    )
    lf = _lazy_source(files[:5000])
    if lf is None:
        return []
    columns = _schema_names(lf)
    fields = detect_shot_fields(columns)
    if not fields.get("lot") or not fields.get("wafer") or not fields.get("split"):
        raise ValueError("ET_TABLE split source에는 root_lot_id, wafer_id, split 열이 필요합니다")
    lf = _string_filter(lf, fields["lot"], root_lot_id)
    lf = _string_filter(lf, fields["wafer"], wafer_id)
    exprs = [
        pl.col(fields["wafer"]).cast(pl.String, strict=False).alias("wafer"),
        pl.col(fields["split"]).cast(pl.String, strict=False).alias("split"),
    ]
    has_shot = bool(fields.get("shot_x") and fields.get("shot_y"))
    if has_shot:
        exprs.extend([
            pl.col(fields["shot_x"]).cast(pl.Float64, strict=False).alias("shot_x"),
            pl.col(fields["shot_y"]).cast(pl.Float64, strict=False).alias("shot_y"),
        ])
    frame = lf.select(exprs).drop_nulls().unique().limit(MAX_MAP_ROWS).collect(streaming=True)
    return [{**row, "shot_level": has_shot} for row in frame.to_dicts()]


def resolve_generic_source(source_id: str, prefix: str = "") -> Path:
    raw = str(source_id or "").strip().replace("\\", "/")
    if not raw or Path(raw).is_absolute():
        raise ValueError("유효한 TABLE을 선택해 주세요")
    root = PATHS.db_root.resolve()
    path = (root / raw).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("TABLE 경로가 DB root를 벗어납니다") from exc
    if not path.exists() or (prefix and not path.name.upper().startswith(prefix.upper())):
        raise ValueError(f"{prefix or '선택한'} TABLE을 찾지 못했습니다")
    return path


def _shot_product_dir(kind: str, product: str) -> Path:
    source_kind = str(kind or "").strip().lower()
    if source_kind not in SHOT_SOURCE_DIRS:
        raise ValueError("데이터 유형은 ET 또는 INLINE이어야 합니다")
    base = PATHS.db_root / SHOT_SOURCE_DIRS[source_kind]
    product_cf = str(product or "").strip().casefold()
    if not product_cf:
        raise ValueError("DB 제품이 비어 있습니다")
    if not base.is_dir():
        raise FileNotFoundError(f"{SHOT_SOURCE_DIRS[source_kind]} 폴더가 없습니다")
    try:
        children = [path for path in base.iterdir() if path.is_dir()]
    except OSError as exc:
        raise FileNotFoundError(f"{SHOT_SOURCE_DIRS[source_kind]} 폴더를 읽지 못했습니다") from exc
    for child in children:
        token = child.name.split("=", 1)[1] if child.name.lower().startswith("product=") else child.name
        if token.casefold() == product_cf:
            return child
    raise FileNotFoundError(f"{source_kind.upper()} DB에서 제품을 찾지 못했습니다: {product}")


def shot_source_files(kind: str, product: str, max_files: int = 5000) -> list[Path]:
    base = _shot_product_dir(kind, product)
    files = sorted(
        (path for path in base.rglob("*") if _is_data_file(path)),
        key=lambda path: str(path).casefold(),
    )
    if len(files) > max_files:
        raise ValueError(f"{kind.upper()} 파일이 {max_files:,}개를 넘어 조회 범위를 줄여야 합니다")
    return files


def shot_database_schema_files(kind: str, max_products: int = 500) -> list[Path]:
    """Return one representative data file per product for a DB-wide union schema."""
    source_kind = str(kind or "").strip().lower()
    if source_kind not in SHOT_SOURCE_DIRS:
        raise ValueError("데이터 유형은 ET 또는 INLINE이어야 합니다")
    root = PATHS.db_root / SHOT_SOURCE_DIRS[source_kind]
    if not root.is_dir():
        raise FileNotFoundError(f"{SHOT_SOURCE_DIRS[source_kind]} 폴더가 없습니다")
    try:
        products = sorted((path for path in root.iterdir() if path.is_dir()), key=lambda path: path.name.casefold())
    except OSError as exc:
        raise FileNotFoundError(f"{SHOT_SOURCE_DIRS[source_kind]} 폴더를 읽지 못했습니다") from exc
    files = []
    for product_dir in products[:max_products]:
        candidates = sorted(
            (path for path in product_dir.rglob("*") if _is_data_file(path)),
            key=lambda path: str(path).casefold(),
        )
        if candidates:
            files.append(candidates[-1])
    return files


def _shot_field_config_doc() -> dict:
    raw = load_json(SHOT_FIELD_CONFIG_PATH, {}) if SHOT_FIELD_CONFIG_PATH.is_file() else {}
    if not isinstance(raw, dict):
        raw = {}
    return {
        "version": 2,
        "databases": raw.get("databases") if isinstance(raw.get("databases"), dict) else {},
        "products": raw.get("products") if isinstance(raw.get("products"), dict) else {},
    }


def load_shot_field_configs() -> dict:
    """Legacy-compatible product override view."""
    return _shot_field_config_doc()["products"]


def _saved_mapping_fields(raw: object) -> dict[str, str]:
    fields = raw.get("fields") if isinstance(raw, dict) and isinstance(raw.get("fields"), dict) else raw
    return {
        str(key): str(value) for key, value in fields.items()
        if key in SHOT_FIELD_ALIASES and str(value).strip()
    } if isinstance(fields, dict) else {}


def saved_database_shot_fields(kind: str) -> dict[str, str]:
    raw = _shot_field_config_doc()["databases"].get(str(kind or "").strip().lower(), {})
    return _saved_mapping_fields(raw)


def saved_product_shot_fields(product: str, kind: str) -> dict[str, str]:
    product_name = str(product or "").strip()
    configs = load_shot_field_configs()
    product_rows = next((row for name, row in configs.items() if str(name).casefold() == product_name.casefold()), {})
    raw = product_rows.get(str(kind or "").strip().lower(), {}) if isinstance(product_rows, dict) else {}
    return _saved_mapping_fields(raw)


def saved_shot_fields(product: str, kind: str) -> dict[str, str]:
    return {**saved_database_shot_fields(kind), **saved_product_shot_fields(product, kind)}


def _saved_mapping_values(raw: object) -> list[str]:
    values = raw.get("value_columns") if isinstance(raw, dict) else []
    return [str(value).strip() for value in values if str(value).strip()] if isinstance(values, list) else []


def saved_database_shot_value_columns(kind: str) -> list[str]:
    raw = _shot_field_config_doc()["databases"].get(str(kind or "").strip().lower(), {})
    return _saved_mapping_values(raw)


def saved_product_shot_value_columns(product: str, kind: str) -> list[str]:
    product_name = str(product or "").strip()
    configs = load_shot_field_configs()
    product_rows = next((row for name, row in configs.items() if str(name).casefold() == product_name.casefold()), {})
    raw = product_rows.get(str(kind or "").strip().lower(), {}) if isinstance(product_rows, dict) else {}
    return _saved_mapping_values(raw)


def saved_shot_value_columns(product: str, kind: str) -> list[str]:
    return saved_product_shot_value_columns(product, kind) or saved_database_shot_value_columns(kind)


def detect_shot_fields(columns: list[str], product: str = "", kind: str = "") -> dict[str, str]:
    by_lower = {str(col).casefold(): str(col) for col in columns}
    out = {}
    for key, aliases in SHOT_FIELD_ALIASES.items():
        for alias in aliases:
            if alias.casefold() in by_lower:
                out[key] = by_lower[alias.casefold()]
                break
    for key, requested in saved_shot_fields(product, kind).items():
        actual = by_lower.get(str(requested).casefold())
        if actual:
            out[key] = actual
    return out


def _shot_value_candidates(lf: pl.LazyFrame, fields: dict[str, str]) -> list[str]:
    try:
        schema = lf.collect_schema()
        numeric = [str(name) for name, dtype in schema.items() if dtype.is_numeric()]
    except Exception:
        return []
    identifier_columns = {
        value.casefold() for key, value in fields.items()
        if key not in {"value", "item"} and value
    }
    return [name for name in numeric if name.casefold() not in identifier_columns]


def shot_field_options(product: str, kind: str, scope: str = "database") -> dict:
    source_kind = str(kind or "").strip().lower()
    product_name = str(product or "").strip()
    if source_kind not in SHOT_SOURCE_DIRS:
        raise ValueError("데이터 유형은 ET 또는 INLINE이어야 합니다")
    config_scope = str(scope or "database").strip().lower()
    if config_scope not in {"database", "product"}:
        raise ValueError("열 매칭 범위는 database 또는 product여야 합니다")
    files = shot_database_schema_files(source_kind) if config_scope == "database" else shot_source_files(source_kind, product_name)
    lf = _lazy_source(files)
    if lf is None:
        raise ValueError(f"{source_kind.upper()} DB 데이터를 읽지 못했습니다")
    columns = _schema_names(lf)
    automatic = detect_shot_fields(columns)
    saved = saved_database_shot_fields(source_kind) if config_scope == "database" else saved_product_shot_fields(product_name, source_kind)
    effective_saved = saved if config_scope == "database" else saved_shot_fields(product_name, source_kind)
    by_lower = {str(column).casefold(): str(column) for column in columns}
    fields = dict(automatic)
    for key, requested in effective_saved.items():
        actual = by_lower.get(str(requested).casefold())
        if actual:
            fields[key] = actual
    candidates = _shot_value_candidates(lf, fields)
    scope_values = saved_database_shot_value_columns(source_kind) if config_scope == "database" else saved_product_shot_value_columns(product_name, source_kind)
    configured_values = scope_values if config_scope == "database" else (scope_values or saved_database_shot_value_columns(source_kind))
    saved_values = [
        next((column for column in columns if column.casefold() == value.casefold()), "")
        for value in configured_values
    ]
    saved_values = [value for value in saved_values if value]
    auto_values = [] if automatic.get("value") and automatic.get("item") else candidates
    return {
        "product": product_name, "kind": source_kind, "scope": config_scope,
        "database": SHOT_SOURCE_DIRS[source_kind], "columns": columns,
        "schema_product_count": len(files) if config_scope == "database" else 1,
        "auto_fields": automatic, "saved_fields": saved,
        "saved_value_columns": scope_values,
        "fields": fields,
        "value_candidates": candidates, "auto_value_columns": auto_values,
        "value_columns": saved_values if saved_values else auto_values,
    }


def save_shot_fields(product: str, kind: str, fields: dict, value_columns: list | None = None,
                     scope: str = "database") -> dict:
    product_name = str(product or "").strip()
    source_kind = str(kind or "").strip().lower()
    config_scope = str(scope or "database").strip().lower()
    options = shot_field_options(product_name, source_kind, config_scope)
    by_lower = {str(column).casefold(): str(column) for column in options["columns"]}
    cleaned = {}
    for key, raw_value in (fields or {}).items():
        if key not in SHOT_FIELD_ALIASES or not str(raw_value or "").strip():
            continue
        actual = by_lower.get(str(raw_value).strip().casefold())
        if not actual:
            raise ValueError(f"{source_kind.upper()} DB에 없는 열입니다: {raw_value}")
        cleaned[key] = actual
    clean_values = []
    for raw_value in value_columns or []:
        actual = by_lower.get(str(raw_value or "").strip().casefold())
        if not actual:
            raise ValueError(f"{source_kind.upper()} DB에 없는 지표 열입니다: {raw_value}")
        if actual not in clean_values:
            clean_values.append(actual)
    with _LOCK:
        config_doc = _shot_field_config_doc()
        saved_mapping = {"fields": cleaned, "value_columns": clean_values}
        if config_scope == "database":
            config_doc["databases"][source_kind] = saved_mapping
        else:
            products = config_doc["products"]
            saved_name = next((name for name in products if str(name).casefold() == product_name.casefold()), product_name)
            product_rows = dict(products.get(saved_name) or {})
            product_rows[source_kind] = saved_mapping
            products[saved_name] = product_rows
        save_json(SHOT_FIELD_CONFIG_PATH, config_doc, indent=2)
    return shot_field_options(product_name, source_kind, config_scope)


def available_products(sources: list[dict] | None = None, configs: dict | None = None) -> list[str]:
    """Return only products backed by a discovered BIN source.

    Partitioned tables contribute their product directory names directly.  A
    configured product is also kept for an unpartitioned BIN source, where
    the product name cannot be inferred safely from the directory tree.
    """
    source_rows = discover_sources() if sources is None else sources
    source_by_id = {
        str(row.get("id") or "").replace("\\", "/"): row
        for row in source_rows
        if isinstance(row, dict) and row.get("id")
    }
    names: dict[str, str] = {}
    for row in source_by_id.values():
        for value in row.get("products") or []:
            name = str(value or "").strip()
            if name:
                names.setdefault(name.casefold(), name)

    config_rows = load_config().get("products") or {} if configs is None else configs
    if isinstance(config_rows, dict):
        for value, config in config_rows.items():
            name = str(value or "").strip()
            if not name or not isinstance(config, dict):
                continue
            source_id = str(config.get("source") or "").strip().replace("\\", "/")
            source = source_by_id.get(source_id)
            if not source:
                continue
            partitions = [str(item or "").strip() for item in source.get("products") or []]
            if not partitions or any(item.casefold() == name.casefold() for item in partitions):
                names.setdefault(name.casefold(), name)
    return sorted(names.values(), key=str.casefold)


def resolve_source(source_id: str) -> Path:
    raw = str(source_id or "").strip().replace("\\", "/")
    if not raw or Path(raw).is_absolute():
        raise ValueError("유효한 BIN TABLE을 선택해 주세요")
    root = PATHS.db_root.resolve()
    path = (root / raw).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("TABLE 경로가 DB root를 벗어납니다") from exc
    if not path.exists() or not _source_matches(path.stem if path.is_file() else path.name):
        raise ValueError("BIN 이름과 매칭되는 TABLE을 찾지 못했습니다")
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
        raise FileNotFoundError("선택한 제품의 BIN TABLE 데이터 파일이 없습니다")
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
        raise ValueError("이 제품에 BIN TABLE 설정이 없습니다")
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


def _axis_origin_candidates(values: list[float], geometry_values: list[float], size: int,
                            limit: int = 8) -> list[int]:
    """Rank grid origins by overlap with the geometry's shot coordinates."""
    coordinates = [_integer_coordinate(float(value)) for value in values]
    coordinates = [value for value in coordinates if value is not None]
    geometry_axis = {_integer_coordinate(float(value)) for value in geometry_values}
    geometry_axis.discard(None)
    if not coordinates or not geometry_axis:
        return []
    ranked: dict[int, int] = {}
    for residue in range(size):
        quotients = Counter((value - residue) // size for value in coordinates)
        shifts = {quotient - shot for quotient in quotients for shot in geometry_axis}
        for shift in shifts:
            score = sum(count for quotient, count in quotients.items()
                        if quotient - shift in geometry_axis)
            origin = residue + shift * size
            ranked[origin] = max(ranked.get(origin, 0), score)
    return [origin for origin, _score in sorted(
        ranked.items(), key=lambda item: (-item[1], abs(item[0]), item[0]),
    )[:limit]]


def _auto_shot_layout(rows: list[dict], geometry: dict, good_bins: list[str]) \
        -> tuple[list[dict], list[dict], dict, int]:
    display = geometry.get("display") or {}
    cols = _bounded_int(display.get("cols"), 0, minimum=0)
    row_count = _bounded_int(display.get("rows"), 0, minimum=0)
    if not cols or not row_count:
        raise ValueError("선택한 제품의 Full Shot chip 배열(cols/rows)이 없습니다")
    geometry_shots = {
        (_integer_coordinate(float(shot.get("x"))), _integer_coordinate(float(shot.get("y"))))
        for shot in geometry.get("shots") or []
        if shot.get("x") is not None and shot.get("y") is not None
    }
    geometry_shots = {shot for shot in geometry_shots if None not in shot}
    if not geometry_shots:
        raise ValueError("선택한 제품의 Full Shot 좌표가 없습니다")
    x_origins = _axis_origin_candidates(
        [row["x"] for row in rows], [shot[0] for shot in geometry_shots], cols,
    )
    y_origins = _axis_origin_candidates(
        [row["y"] for row in rows], [shot[1] for shot in geometry_shots], row_count,
    )
    if not x_origins or not y_origins:
        raise ValueError("BIN chip 좌표를 Full Shot geometry와 매칭할 수 없습니다")

    best = None
    expected_die = cols * row_count
    for origin_x in x_origins:
        for origin_y in y_origins:
            groups: dict[tuple[str, str, int, int], set[tuple[int, int]]] = {}
            matched_rows = 0
            for row in rows:
                x = _integer_coordinate(float(row["x"]))
                y = _integer_coordinate(float(row["y"]))
                if x is None or y is None:
                    continue
                rel_x, rel_y = x - origin_x, y - origin_y
                shot_x, shot_y = rel_x // cols, rel_y // row_count
                if (shot_x, shot_y) not in geometry_shots:
                    continue
                matched_rows += 1
                key = (
                    str(row.get("lot") or ""), str(row.get("wafer") or ""),
                    shot_x, shot_y,
                )
                groups.setdefault(key, set()).add((rel_x % cols, rel_y % row_count))
            full_shots = sum(1 for slots in groups.values() if len(slots) == expected_die)
            score = (full_shots, matched_rows, len(groups), -abs(origin_x), -abs(origin_y))
            if best is None or score > best[0]:
                best = (score, origin_x, origin_y, matched_rows)
    if best is None or best[3] <= 0:
        raise ValueError("BIN chip 좌표와 Full Shot geometry에 겹치는 좌표가 없습니다")
    layout_value = {
        "enabled": True, "cols": cols, "rows": row_count,
        "origin_x": best[1], "origin_y": best[2], "good_bins": good_bins,
    }
    mapped, shots, layout = apply_shot_layout(rows, layout_value)
    return mapped, shots, layout, best[3]


def scan_shot_layout(product: str, payload: dict, lot_id: str = "", wafer_id: str = "",
                     root_lot_id: str = "") -> dict:
    """Discover BINs and automatically match chip coordinates to product geometry."""
    current = product_config(product)
    candidate = {
        **current,
        "source": str((payload or {}).get("source") or current.get("source") or ""),
        "fields": (payload or {}).get("fields") or current.get("fields") or {},
    }
    rows, meta = _source_rows(
        product, candidate, lot_id=root_lot_id or lot_id, wafer_id=wafer_id,
    )
    if not rows:
        raise ValueError("Scan할 BIN 데이터가 없습니다")
    vehicle = str((payload or {}).get("vehicle") or current.get("vehicle") or "").strip()
    geometry = _wf_geometry(vehicle)
    if not geometry:
        raise ValueError("선택한 제품과 같은 이름의 WF geometry가 없습니다")
    counts = Counter(row["bin"] for row in rows)
    previous_layout = _clean_shot_layout(
        (payload or {}).get("shot_layout") or current.get("shot_layout"),
    )
    good_bins = previous_layout["good_bins"]
    if not good_bins and "1" in counts:
        good_bins = ["1"]
    mapped, shots, layout, matched_rows = _auto_shot_layout(rows, geometry, good_bins)
    xs = sorted({row["x"] for row in mapped})
    ys = sorted({row["y"] for row in mapped})
    geometry_shots = {
        (_integer_coordinate(float(shot["x"])), _integer_coordinate(float(shot["y"])))
        for shot in geometry.get("shots") or []
    }
    matched_shot_rows = [
        row for row in shots if (row["shot_x"], row["shot_y"]) in geometry_shots
    ]
    full = [row for row in matched_shot_rows if row["is_full_shot"]]
    partial = [row for row in matched_shot_rows if not row["is_full_shot"]]
    wafer_ids = sorted({str(row.get("wafer") or "") for row in mapped}, key=_natural_text_key)
    preview_wafer = wafer_ids[0] if wafer_ids else ""
    preview_rows = [
        row for row in mapped
        if str(row.get("wafer") or "") == preview_wafer
        and (row.get("shot_x"), row.get("shot_y")) in geometry_shots
    ][:25_000]
    preview_shots = [
        row for row in matched_shot_rows if str(row.get("wafer") or "") == preview_wafer
    ]
    return {
        **meta, "product": product, "vehicle": vehicle, "geometry": geometry, "layout": layout,
        "scan_rows": len(mapped),
        "matched_rows": matched_rows,
        "coordinate_count": len({(row["x"], row["y"]) for row in mapped}),
        "x": {"min": xs[0] if xs else None, "max": xs[-1] if xs else None, "unique": len(xs)},
        "y": {"min": ys[0] if ys else None, "max": ys[-1] if ys else None, "unique": len(ys)},
        "bins": [
            {"bin": key, "count": value}
            for key, value in sorted(counts.items(), key=lambda item: _natural_text_key(item[0]))
        ],
        "shot_count": len(matched_shot_rows), "full_shot_count": len(full),
        "partial_shot_count": len(partial),
        "sample_shots": (full[:8] + partial[:8])[:12],
        "preview_wafer_id": preview_wafer,
        "preview_rows": preview_rows,
        "preview_shots": preview_shots,
    }


def _natural_text_key(value: Any) -> list[tuple[int, Any]]:
    return [
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", str(value or ""))
    ]


def _wf_geometry(vehicle: str) -> dict | None:
    name = str(vehicle or "").strip()
    if not name:
        return None
    from core import teg_map
    try:
        return teg_map.map_payload(name)
    except (FileNotFoundError, LookupError):
        return None


def _string_filter(lf: pl.LazyFrame, column: str, value: str) -> pl.LazyFrame:
    if not value:
        return lf
    return lf.filter(pl.col(column).cast(pl.String, strict=False) == str(value))


def _tkout_filter(lf: pl.LazyFrame, column: str | None,
                  tkout_from: str = "", tkout_to: str = "") -> pl.LazyFrame:
    start, end = str(tkout_from or "").strip(), str(tkout_to or "").strip()
    if not start and not end:
        return lf
    if start and end and start > end:
        raise ValueError("tkout_time 시작이 종료보다 늦습니다")
    if not column:
        raise ValueError("선택한 ET/Inline DB에 tkout_time 열이 없어 기간 필터를 적용할 수 없습니다")
    text = pl.col(column).cast(pl.String, strict=False)
    if start:
        lf = lf.filter(text >= start)
    if end:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", end):
            try:
                exclusive_end = (dt.date.fromisoformat(end) + dt.timedelta(days=1)).isoformat()
                lf = lf.filter(text < exclusive_end)
            except ValueError as exc:
                raise ValueError("tkout_time 종료일 형식이 올바르지 않습니다") from exc
        else:
            lf = lf.filter(text <= end)
    return lf


def _candidate_values(lf: pl.LazyFrame, column: str | None, limit: int = 300) -> list[str]:
    if not column:
        return []
    try:
        values = (
            lf.select(pl.col(column).cast(pl.String, strict=False).alias("value"))
            .drop_nulls().unique().limit(limit).collect(streaming=True)["value"].to_list()
        )
    except Exception:
        return []
    return sorted({str(value) for value in values if str(value).strip()}, key=_natural_text_key)


def _inline_table(vehicle: str, table_name: str = "") -> dict:
    from core import teg_map
    settings = teg_map.load_inline_map_settings()
    requested = str(table_name or "").strip().casefold()
    vehicle_cf = str(vehicle or "").strip().casefold()
    tables = [
        table for table in settings.get("tables") or []
        if str(table.get("vehicle") or "").casefold() == vehicle_cf
    ]
    if requested:
        table = next((row for row in tables if str(row.get("table_name") or "").casefold() == requested), None)
        if table is None:
            raise ValueError(f"Inline map setting을 찾지 못했습니다: {table_name}")
        return table
    if not tables:
        raise ValueError(f"{vehicle} 제품에 저장된 Inline map setting이 없습니다")
    return tables[0]


def inline_matching_rules(product: str = "") -> list[dict]:
    """Return ITEM-specific inline_matching.csv links with TEG table status."""
    from core import teg_map
    return inline_coordinates.load_matching_rules(
        PATHS.base_root,
        products=[product] if str(product or "").strip() else (),
        settings_path=teg_map.inline_map_settings_path(),
    )


def shot_map_data(kind: str, product: str, vehicle: str, root_lot_id: str,
                  wafer_id: str = "", item_id: str = "", step_id: str = "",
                  step_seq: str = "", inline_table: str = "", split_source: str = "",
                  tkout_from: str = "", tkout_to: str = "") -> dict:
    """ET/INLINE shot 값을 TEG 위치조회 WF geometry 좌표에 맞춰 정규화한다.

    ET는 원천 shot_x/shot_y를 사용한다. INLINE은 원천 좌표가 있더라도
    Inline map setting의 subitem_id 이름표를 authoritative 좌표로 사용한다.
    """
    source_kind = str(kind or "").strip().lower()
    if source_kind not in SHOT_SOURCE_DIRS:
        raise ValueError("데이터 유형은 ET 또는 INLINE이어야 합니다")
    root_lot = str(root_lot_id or "").strip()
    if not root_lot:
        raise ValueError("ROOT LOT ID가 필요합니다")
    geometry = _wf_geometry(vehicle)
    if geometry is None:
        raise ValueError(f"TEG 위치조회에 저장된 WF geometry가 없습니다: {vehicle}")

    files = shot_source_files(source_kind, product)
    if not files:
        raise FileNotFoundError(f"{source_kind.upper()} DB 데이터 파일이 없습니다: {product}")
    lf = _lazy_source(files)
    if lf is None:
        raise ValueError(f"{source_kind.upper()} DB 데이터를 읽지 못했습니다")
    columns = _schema_names(lf)
    fields = detect_shot_fields(columns, product, source_kind)
    configured_value_columns = [
        next((column for column in columns if column.casefold() == value.casefold()), "")
        for value in saved_shot_value_columns(product, source_kind)
    ]
    value_columns = [value for value in configured_value_columns if value]
    if not value_columns and not (fields.get("value") and fields.get("item")):
        value_columns = _shot_value_candidates(lf, fields)
    wide_values = bool(value_columns)
    required = ["lot", "wafer"]
    if not wide_values:
        required.extend(["value", "item"])
    if source_kind == "et":
        required.extend(["shot_x", "shot_y"])
    else:
        required.append("subitem")
    missing = [key for key in required if not fields.get(key)]
    if missing:
        raise ValueError(f"{source_kind.upper()} DB 열 매핑이 필요합니다: {', '.join(missing)}")

    lf = _string_filter(lf, fields["lot"], root_lot)
    lf = _string_filter(lf, fields["wafer"], str(wafer_id or "").strip())
    lf = _tkout_filter(lf, fields.get("tkout"), tkout_from, tkout_to)
    steps = _candidate_values(lf, fields.get("step"))
    selected_step = str(step_id or "").strip()
    if source_kind == "et" and not selected_step and steps:
        selected_step = steps[0]
    table = None
    inline_rules: list[dict] = []
    if source_kind == "inline":
        if not fields.get("step"):
            raise ValueError("INLINE shot 매칭에는 STEP ID 열이 필요합니다")
        all_items = value_columns if wide_values else _candidate_values(lf, fields.get("item"))
        inline_rules = inline_matching_rules(product)
        if not inline_rules:
            raise ValueError(
                f"{product}에 Inline Matching table 규칙이 없습니다. "
                "inline_matching.csv에 제품·STEP·ITEM·matching_table을 설정해 주세요"
            )
        configured_items = sorted({
            str(rule.get("item_id") or "") for rule in inline_rules if rule.get("item_id")
        }, key=_natural_text_key)
        selected_item = str(item_id or "").strip() or next(
            (value for value in configured_items if value.casefold() in {item.casefold() for item in all_items}),
            configured_items[0] if configured_items else "",
        )
        item_rules = [
            rule for rule in inline_rules
            if str(rule.get("item_id") or "").casefold() == selected_item.casefold()
        ]
        if not item_rules:
            raise ValueError(f"{product} · {selected_item} ITEM에 Inline Matching table 규칙이 없습니다")
        configured_steps = sorted({
            str(rule.get("step_id") or "") for rule in item_rules if rule.get("step_id")
        }, key=_natural_text_key)
        if not selected_step:
            selected_step = next(
                (value for value in configured_steps if value.casefold() in {step.casefold() for step in steps}),
                configured_steps[0] if configured_steps else "",
            )
        step_rules = [
            rule for rule in item_rules
            if str(rule.get("step_id") or "").casefold() == selected_step.casefold()
        ]
        if not step_rules:
            raise ValueError(f"{product} · {selected_step} · {selected_item}에 Inline Matching table 규칙이 없습니다")
        requested_table = str(inline_table or "").strip()
        if requested_table:
            step_rules = [
                rule for rule in step_rules
                if str(rule.get("matching_table") or "").casefold() == requested_table.casefold()
            ]
            if not step_rules:
                raise ValueError(
                    f"{product} · {selected_step} · {selected_item}에 연결되지 않은 Inline Mapsetting입니다: {requested_table}"
                )
        available_rules = [
            rule for rule in step_rules
            if rule.get("available")
            and str(rule.get("vehicle") or "").casefold() == str(vehicle or "").casefold()
        ]
        if not available_rules:
            names = ", ".join(str(rule.get("matching_table") or "") for rule in step_rules)
            raise ValueError(
                f"Inline Matching table에 연결된 TEG Inline Mapsetting을 사용할 수 없습니다: {names or '(미지정)'}"
            )
        if not requested_table and len(available_rules) > 1:
            raise ValueError("이 Inline ITEM에 Mapsetting이 여러 개입니다. 사용할 Inline Mapsetting을 선택해 주세요")
        table = _inline_table(vehicle, str(available_rules[0]["matching_table"]))

    lf = _string_filter(lf, fields.get("step", ""), selected_step) if fields.get("step") else lf
    step_seqs = _candidate_values(lf, fields.get("step_seq"))
    selected_step_seq = str(step_seq or "").strip()
    if source_kind == "et" and not selected_step_seq and step_seqs:
        selected_step_seq = step_seqs[0]
    lf = _string_filter(lf, fields.get("step_seq", ""), selected_step_seq) if fields.get("step_seq") else lf
    items = value_columns if wide_values else _candidate_values(lf, fields.get("item"))
    if source_kind != "inline":
        selected_item = str(item_id or "").strip() or (items[0] if items else "")
    if selected_item:
        if wide_values:
            selected_item = next((value for value in value_columns if value.casefold() == selected_item.casefold()), "")
            if not selected_item:
                raise ValueError(f"{source_kind.upper()} 조회 지표 열이 아닙니다: {item_id}")
        else:
            lf = _string_filter(lf, fields["item"], selected_item)
    wafer_ids = _candidate_values(lf, fields.get("wafer"))
    value_field = selected_item if wide_values else fields["value"]

    exprs = [
        pl.col(fields["wafer"]).cast(pl.String, strict=False).alias("wafer"),
        pl.col(value_field).cast(pl.Float64, strict=False).alias("value"),
    ]
    if source_kind == "et":
        exprs.extend([
            pl.col(fields["shot_x"]).cast(pl.Float64, strict=False).alias("shot_x"),
            pl.col(fields["shot_y"]).cast(pl.Float64, strict=False).alias("shot_y"),
        ])
    else:
        exprs.append(pl.col(fields["subitem"]).cast(pl.String, strict=False).alias("subitem_id"))
    if source_kind == "et":
        grouped = (
            lf.select(exprs).drop_nulls()
            .group_by(["wafer", "shot_x", "shot_y"])
            .agg(pl.col("value").mean().alias("value"), pl.len().alias("sample_count"))
            .sort(["wafer", "shot_y", "shot_x"])
            .limit(MAX_MAP_ROWS + 1).collect(streaming=True)
        )
        overflow = grouped.height > MAX_MAP_ROWS
        if overflow:
            grouped = grouped.head(MAX_MAP_ROWS)
    else:
        # INLINE은 raw 행을 잘라 평균을 왜곡하지 않고 먼저 wafer/subitem별로 집계한다.
        frame = (
            lf.select(exprs).drop_nulls()
            .group_by(["wafer", "subitem_id"])
            .agg(pl.col("value").mean().alias("value"), pl.len().alias("sample_count"))
            .limit(MAX_MAP_ROWS + 1).collect(streaming=True)
        )
        overflow = frame.height > MAX_MAP_ROWS
        if overflow:
            frame = frame.head(MAX_MAP_ROWS)
        position_by_subitem = {}
        for shot in table.get("shots") or []:
            token = str(shot.get("subitem_id") or shot.get("name") or "").strip().casefold()
            if token:
                position_by_subitem[token] = (float(shot["shot_x"]), float(shot["shot_y"]))
        normalized = []
        for row in frame.to_dicts():
            position = position_by_subitem.get(str(row.get("subitem_id") or "").strip().casefold())
            if position is None:
                continue
            normalized.append({**row, "shot_x": position[0], "shot_y": position[1]})
        mapped = pl.DataFrame(normalized) if normalized else pl.DataFrame(
            schema={"wafer": pl.String, "value": pl.Float64, "subitem_id": pl.String,
                    "shot_x": pl.Float64, "shot_y": pl.Float64, "sample_count": pl.UInt32}
        )
        if mapped.height:
            grouped = (
                mapped.with_columns((pl.col("value") * pl.col("sample_count")).alias("weighted_value"))
                .group_by(["wafer", "shot_x", "shot_y"])
                .agg(
                    (pl.col("weighted_value").sum() / pl.col("sample_count").sum()).alias("value"),
                    pl.col("sample_count").sum().alias("sample_count"),
                ).sort(["wafer", "shot_y", "shot_x"])
            )
        else:
            grouped = mapped

    if grouped.height:
        rows = grouped.to_dicts()
        numeric = [float(value) for value in grouped["value"].to_list() if value is not None]
    else:
        rows, numeric = [], []
    if split_source and rows:
        split_rows = _split_rows(split_source, root_lot, str(wafer_id or "").strip())
        wafer_split = {}
        shot_split = {}
        for split_row in split_rows:
            wafer = str(split_row.get("wafer") or "")
            if split_row.get("shot_level"):
                shot_split[(wafer, float(split_row["shot_x"]), float(split_row["shot_y"]))] = str(split_row.get("split") or "")
            else:
                wafer_split[wafer] = str(split_row.get("split") or "")
        for row in rows:
            wafer = str(row.get("wafer") or "")
            row["split"] = shot_split.get((wafer, float(row["shot_x"]), float(row["shot_y"])), wafer_split.get(wafer, ""))
    return {
        "kind": source_kind,
        "product": product,
        "vehicle": vehicle,
        "root_lot_id": root_lot,
        "selected_item": selected_item,
        "selected_step": selected_step,
        "selected_step_seq": selected_step_seq,
        "items": items,
        "steps": steps,
        "step_seqs": step_seqs,
        "wafer_ids": wafer_ids,
        "rows": rows,
        "value_min": min(numeric) if numeric else None,
        "value_max": max(numeric) if numeric else None,
        "geometry": geometry,
        "inline_table": table.get("table_name") if table else "",
        "split_source": str(split_source or ""),
        "tkout_from": str(tkout_from or ""), "tkout_to": str(tkout_to or ""),
        "overflow": overflow,
        "file_count": len(files),
        "fields": fields,
        "value_columns": value_columns,
    }


def et_index_map_data(product: str, vehicle: str, root_lot_id: str,
                      frame: pl.DataFrame, item_alias: str, *, wafer_id: str = "",
                      step_id: str = "", step_seq: str = "", items: list[str] | None = None,
                      item_specs: dict | None = None, item_source: str = "reformatter",
                      item_formula: str = "", rule_errors: list[str] | None = None,
                      notice: str = "", vehicle_csv: str = "",
                      split_source: str = "") -> dict:
    """Normalize an ET Download REAL/ADDP wide frame into WF MAP shot rows.

    ET Download owns raw-item dependency resolution, REAL scale/absolute handling,
    and recursive ADDP evaluation.  This helper only applies exact map filters and
    converts the resulting shot-grain value column to the same payload used by
    :func:`shot_map_data`.
    """
    root_lot = str(root_lot_id or "").strip()
    alias = str(item_alias or "").strip()
    if not root_lot:
        raise ValueError("ROOT LOT ID가 필요합니다")
    if not alias:
        raise ValueError("ET Download ITEM alias가 필요합니다")
    geometry = _wf_geometry(vehicle)
    if geometry is None:
        raise ValueError(f"TEG 위치조회에 저장된 WF geometry가 없습니다: {vehicle}")
    if alias not in frame.columns:
        details = "; ".join(str(value) for value in (rule_errors or []) if str(value).strip())
        suffix = f" ({details})" if details else ""
        raise ValueError(f"ET Download 계산 결과에 '{alias}' 값이 없습니다{suffix}")

    lot_col = next((name for name in ("root_lot_id", "lot_id") if name in frame.columns), None)
    wafer_col = "wafer_id" if "wafer_id" in frame.columns else None
    step_col = "step_id" if "step_id" in frame.columns else None
    step_seq_col = "step_seq" if "step_seq" in frame.columns else None
    shot_x_col = next((name for name in ("shot_x", "chip_x_pos") if name in frame.columns), None)
    shot_y_col = next((name for name in ("shot_y", "chip_y_pos") if name in frame.columns), None)
    missing = [name for name, value in (
        ("root_lot_id", lot_col), ("wafer_id", wafer_col),
        ("shot_x", shot_x_col), ("shot_y", shot_y_col),
    ) if not value]
    if missing:
        raise ValueError(f"ET Download 결과에 WF MAP 좌표 열이 없습니다: {', '.join(missing)}")

    work = frame.filter(pl.col(lot_col).cast(pl.String, strict=False) == root_lot)
    selected_wafer = str(wafer_id or "").strip()
    if selected_wafer:
        work = work.filter(pl.col(wafer_col).cast(pl.String, strict=False) == selected_wafer)
    wafer_ids = sorted({
        str(value) for value in work[wafer_col].cast(pl.String, strict=False).drop_nulls().to_list()
        if str(value).strip()
    }, key=_natural_text_key)
    steps = sorted({
        str(value) for value in (work[step_col].cast(pl.String, strict=False).drop_nulls().to_list() if step_col else [])
        if str(value).strip()
    }, key=_natural_text_key)
    selected_step = str(step_id or "").strip() or (steps[0] if steps else "")
    if selected_step and step_col:
        work = work.filter(pl.col(step_col).cast(pl.String, strict=False) == selected_step)
    step_seqs = sorted({
        str(value) for value in (work[step_seq_col].cast(pl.String, strict=False).drop_nulls().to_list() if step_seq_col else [])
        if str(value).strip()
    }, key=_natural_text_key)
    selected_step_seq = str(step_seq or "").strip() or (step_seqs[0] if step_seqs else "")
    if selected_step_seq and step_seq_col:
        work = work.filter(pl.col(step_seq_col).cast(pl.String, strict=False) == selected_step_seq)

    grouped = (
        work.select(
            pl.col(wafer_col).cast(pl.String, strict=False).alias("wafer"),
            pl.col(shot_x_col).cast(pl.Float64, strict=False).alias("shot_x"),
            pl.col(shot_y_col).cast(pl.Float64, strict=False).alias("shot_y"),
            pl.col(alias).cast(pl.Float64, strict=False).alias("value"),
        ).drop_nulls()
        .group_by(["wafer", "shot_x", "shot_y"])
        .agg(pl.col("value").mean().alias("value"), pl.len().alias("sample_count"))
        .sort(["wafer", "shot_y", "shot_x"])
        .limit(MAX_MAP_ROWS + 1)
    )
    overflow = grouped.height > MAX_MAP_ROWS
    if overflow:
        grouped = grouped.head(MAX_MAP_ROWS)
    rows = grouped.to_dicts()
    if split_source and rows:
        split_rows = _split_rows(split_source, root_lot, selected_wafer)
        wafer_split = {}
        shot_split = {}
        for split_row in split_rows:
            wafer = str(split_row.get("wafer") or "")
            if split_row.get("shot_level"):
                shot_split[(wafer, float(split_row["shot_x"]), float(split_row["shot_y"]))] = str(split_row.get("split") or "")
            else:
                wafer_split[wafer] = str(split_row.get("split") or "")
        for row in rows:
            wafer = str(row.get("wafer") or "")
            row["split"] = shot_split.get(
                (wafer, float(row["shot_x"]), float(row["shot_y"])), wafer_split.get(wafer, ""),
            )
    numeric = [float(value) for value in grouped["value"].to_list() if value is not None]
    return {
        "kind": "et", "product": product, "vehicle": vehicle,
        "root_lot_id": root_lot, "selected_item": alias,
        "selected_step": selected_step, "selected_step_seq": selected_step_seq,
        "items": list(items or []), "steps": steps, "step_seqs": step_seqs,
        "wafer_ids": wafer_ids, "rows": rows,
        "value_min": min(numeric) if numeric else None,
        "value_max": max(numeric) if numeric else None,
        "geometry": geometry, "inline_table": "", "split_source": str(split_source or ""),
        "overflow": overflow, "file_count": 0,
        "fields": {"lot": lot_col, "wafer": wafer_col, "shot_x": shot_x_col,
                   "shot_y": shot_y_col, "step": step_col,
                   "step_seq": step_seq_col, "value": alias},
        "item_source": str(item_source or "reformatter"),
        "item_formula": str(item_formula or ""),
        "item_spec": (item_specs or {}).get(alias, {}),
        "item_specs": item_specs or {}, "rule_errors": list(rule_errors or []),
        "notice": str(notice or ""), "vehicle_csv": str(vehicle_csv or ""),
    }


def _shot_bin_metrics(rows: list[dict], shot_rows: list[dict], selected_bin: str = "") -> list[dict]:
    token = str(selected_bin or "").strip()
    yield_by_key = {
        (str(row.get("lot") or ""), str(row.get("wafer") or ""), float(row["shot_x"]), float(row["shot_y"])): row
        for row in shot_rows
    }
    groups: dict[tuple[str, str, float, float], dict] = {}
    for row in rows:
        if row.get("shot_x") is None or row.get("shot_y") is None:
            continue
        key = (str(row.get("lot") or ""), str(row.get("wafer") or ""), float(row["shot_x"]), float(row["shot_y"]))
        group = groups.setdefault(key, {"total": 0, "match": 0})
        group["total"] += 1
        if token and str(row.get("bin") or "") == token:
            group["match"] += 1
    out = []
    for (lot, wafer, shot_x, shot_y), group in groups.items():
        shot = yield_by_key.get((lot, wafer, shot_x, shot_y), {})
        ratio = group["match"] * 100.0 / group["total"] if token and group["total"] else None
        shot_yield = shot.get("shot_yield")
        value = shot_yield if token.casefold() in {"yield", "shot_yield"} else ratio
        out.append({
            "lot": lot, "wafer": wafer, "shot_x": shot_x, "shot_y": shot_y,
            "selected_bin": token, "selected_bin_count": group["match"],
            "total_die": group["total"], "selected_bin_ratio": ratio,
            "shot_yield": shot_yield, "value": value,
        })
    return sorted(out, key=lambda row: (_natural_text_key(row["wafer"]), row["shot_y"], row["shot_x"]))


def map_data(product: str, lot_id: str = "", wafer_id: str = "", root_lot_id: str = "",
             selected_bin: str = "") -> dict:
    cfg = product_config(product)
    selected_root_lot = str(root_lot_id or lot_id or "").strip()
    rows, meta = _source_rows(product, cfg, lot_id=selected_root_lot, wafer_id=wafer_id)
    rows, shot_rows, shot_layout = apply_shot_layout(rows, cfg.get("shot_layout"))
    counts = Counter(row["bin"] for row in rows)
    net_die = len({(row["x"], row["y"]) for row in rows})
    full_shots = sum(1 for row in shot_rows if row["is_full_shot"])
    wafer_ids = sorted(
        {str(row.get("wafer") or "") for row in rows}, key=_natural_text_key,
    )
    geometry = _wf_geometry(cfg.get("vehicle") or "")
    shot_metrics = _shot_bin_metrics(rows, shot_rows, selected_bin)
    return {
        "product": product, "root_lot_id": selected_root_lot,
        "vehicle": str(cfg.get("vehicle") or ""), "geometry": geometry,
        "source": meta["source"], "fields": meta["fields"],
        "bin_colors": cfg.get("bin_colors") or {}, "rows": rows,
        "bins": [
            {"bin": key, "count": value}
            for key, value in sorted(counts.items(), key=lambda item: _natural_text_key(item[0]))
        ],
        "net_die": net_die, "overflow": meta["overflow"], "file_count": meta["file_count"],
        "wafer_ids": wafer_ids, "wafer_count": len(wafer_ids),
        "shot_layout": shot_layout, "shot_rows": shot_rows,
        "selected_bin": str(selected_bin or ""), "shot_metrics": shot_metrics,
        "full_shot_count": full_shots, "partial_shot_count": len(shot_rows) - full_shots,
    }


def compare_shot_metrics(yield_product: str, et_product: str, vehicle: str,
                         root_lot_id: str, bin_name: str, item_id: str,
                         wafer_id: str = "", step_id: str = "", step_seq: str = "",
                         split_source: str = "",
                         et_map_data: dict | None = None) -> dict:
    """선택 BIN의 shot 평균(비율)과 ET shot 값을 공통 좌표로 JOIN한다."""
    yield_map = map_data(
        yield_product, root_lot_id=root_lot_id, wafer_id=wafer_id, selected_bin=bin_name,
    )
    et_map = et_map_data or shot_map_data(
        "et", et_product, vehicle, root_lot_id, wafer_id=wafer_id,
        item_id=item_id, step_id=step_id, step_seq=step_seq, split_source=split_source,
    )
    yield_by_key = {
        (str(row.get("wafer") or ""), float(row["shot_x"]), float(row["shot_y"])): row
        for row in yield_map.get("shot_metrics") or [] if row.get("value") is not None
    }
    points = []
    for et_row in et_map.get("rows") or []:
        key = (str(et_row.get("wafer") or ""), float(et_row["shot_x"]), float(et_row["shot_y"]))
        yield_row = yield_by_key.get(key)
        if not yield_row:
            continue
        points.append({
            "wafer": key[0], "shot_x": key[1], "shot_y": key[2],
            "yield_value": yield_row.get("value"),
            "shot_yield": yield_row.get("shot_yield"),
            "selected_bin_ratio": yield_row.get("selected_bin_ratio"),
            "et_value": et_row.get("value"), "split": et_row.get("split") or "(미지정)",
            "et_sample_count": et_row.get("sample_count") or 0,
        })
    return {
        "yield_product": yield_product, "et_product": et_product, "vehicle": vehicle,
        "root_lot_id": root_lot_id, "selected_bin": bin_name,
        "selected_item": et_map.get("selected_item") or item_id,
        "points": points, "point_count": len(points),
        "items": et_map.get("items") or [], "steps": et_map.get("steps") or [],
        "step_seqs": et_map.get("step_seqs") or [],
        "selected_step": et_map.get("selected_step") or step_id,
        "selected_step_seq": et_map.get("selected_step_seq") or step_seq,
        "split_source": split_source,
    }


def _pearson_similarity(points: list[dict], left_key: str, right_key: str) -> dict:
    """Return scale-independent map-pattern similarity for finite paired values."""
    pairs = []
    for row in points:
        try:
            left, right = float(row.get(left_key)), float(row.get(right_key))
        except (TypeError, ValueError):
            continue
        if math.isfinite(left) and math.isfinite(right):
            pairs.append((left, right))
    if len(pairs) < 2:
        return {"pearson_r": None, "sample_count": len(pairs)}
    left_mean = sum(left for left, _right in pairs) / len(pairs)
    right_mean = sum(right for _left, right in pairs) / len(pairs)
    numerator = sum((left - left_mean) * (right - right_mean) for left, right in pairs)
    left_ss = sum((left - left_mean) ** 2 for left, _right in pairs)
    right_ss = sum((right - right_mean) ** 2 for _left, right in pairs)
    if left_ss <= 0 or right_ss <= 0:
        value = None
    else:
        value = max(-1.0, min(1.0, numerator / math.sqrt(left_ss * right_ss)))
    return {"pearson_r": value, "sample_count": len(pairs)}


def compare_shot_sources(yield_product: str, et_product: str, inline_product: str,
                         vehicle: str, root_lot_id: str, bin_name: str,
                         et_item_id: str, inline_item_id: str, *, wafer_id: str = "",
                         step_id: str = "", step_seq: str = "", inline_step_id: str = "",
                         inline_table: str = "",
                         split_source: str = "", et_map_data: dict | None = None) -> dict:
    """Join shot averages from Yield, ET, and Inline on Yield-owned coordinates.

    ET and Inline are aggregated to wafer/shot grain by their map loaders.  The
    output is the union of Yield↔ET and Yield↔Inline matches, while each
    similarity metric uses only its own finite pair (ET↔Inline uses triple
    matches). This avoids silently discarding a valid pair when the third source
    was not measured at that shot.
    """
    yield_map = map_data(
        yield_product, root_lot_id=root_lot_id, wafer_id=wafer_id, selected_bin=bin_name,
    )
    et_map = et_map_data or shot_map_data(
        "et", et_product, vehicle, root_lot_id, wafer_id=wafer_id,
        item_id=et_item_id, step_id=step_id, step_seq=step_seq,
        split_source=split_source,
    )
    inline_map = shot_map_data(
        "inline", inline_product, vehicle, root_lot_id, wafer_id=wafer_id,
        item_id=inline_item_id, step_id=inline_step_id, inline_table=inline_table,
    )

    def key(row: dict) -> tuple[str, float, float]:
        return (str(row.get("wafer") or ""), float(row["shot_x"]), float(row["shot_y"]))

    et_by_key = {key(row): row for row in et_map.get("rows") or [] if row.get("value") is not None}
    inline_by_key = {
        key(row): row for row in inline_map.get("rows") or [] if row.get("value") is not None
    }
    points = []
    for yield_row in yield_map.get("shot_metrics") or []:
        if yield_row.get("value") is None:
            continue
        shot_key = key(yield_row)
        et_row, inline_row = et_by_key.get(shot_key), inline_by_key.get(shot_key)
        if et_row is None and inline_row is None:
            continue
        points.append({
            "root_lot_id": root_lot_id, "wafer_id": shot_key[0], "wafer": shot_key[0],
            "shot_x": shot_key[1], "shot_y": shot_key[2],
            "yield_value": yield_row.get("value"),
            "shot_yield": yield_row.get("shot_yield"),
            "selected_bin_ratio": yield_row.get("selected_bin_ratio"),
            "et_value": et_row.get("value") if et_row else None,
            "inline_value": inline_row.get("value") if inline_row else None,
            "split": (et_row or {}).get("split") or "(미지정)",
            "et_sample_count": (et_row or {}).get("sample_count") or 0,
            "inline_sample_count": (inline_row or {}).get("sample_count") or 0,
        })
    points.sort(key=lambda row: (_natural_text_key(row["wafer"]), row["shot_y"], row["shot_x"]))
    yield_et = _pearson_similarity(points, "yield_value", "et_value")
    yield_inline = _pearson_similarity(points, "yield_value", "inline_value")
    et_inline = _pearson_similarity(points, "et_value", "inline_value")
    triple_count = sum(
        1 for row in points if row.get("et_value") is not None and row.get("inline_value") is not None
    )
    return {
        "yield_product": yield_product, "et_product": et_product,
        "inline_product": inline_product, "vehicle": vehicle,
        "root_lot_id": root_lot_id, "selected_bin": bin_name,
        "selected_item": et_map.get("selected_item") or et_item_id,
        "selected_inline_item": inline_map.get("selected_item") or inline_item_id,
        "selected_inline_step": inline_map.get("selected_step") or inline_step_id,
        "selected_inline_table": inline_map.get("inline_table") or inline_table,
        "points": points, "point_count": len(points), "triple_count": triple_count,
        "yield_et_count": yield_et["sample_count"],
        "yield_inline_count": yield_inline["sample_count"],
        "similarity": {
            "yield_et": yield_et, "yield_inline": yield_inline, "et_inline": et_inline,
        },
        "items": et_map.get("items") or [], "steps": et_map.get("steps") or [],
        "step_seqs": et_map.get("step_seqs") or [],
        "inline_items": inline_map.get("items") or [],
        "selected_step": et_map.get("selected_step") or step_id,
        "selected_step_seq": et_map.get("selected_step_seq") or step_seq,
        "split_source": split_source,
    }


def _linear_fit(points: list[dict], x_key: str, y_key: str) -> dict:
    pairs = []
    for row in points:
        try:
            x, y = float(row.get(x_key)), float(row.get(y_key))
        except (TypeError, ValueError):
            continue
        if math.isfinite(x) and math.isfinite(y):
            pairs.append((x, y))
    if len(pairs) < 2:
        return {"slope": None, "intercept": None, "r2": None, "sample_count": len(pairs)}
    x_mean = sum(x for x, _ in pairs) / len(pairs)
    y_mean = sum(y for _, y in pairs) / len(pairs)
    denominator = sum((x - x_mean) ** 2 for x, _ in pairs)
    if denominator <= 0:
        return {"slope": None, "intercept": None, "r2": None, "sample_count": len(pairs)}
    slope = sum((x - x_mean) * (y - y_mean) for x, y in pairs) / denominator
    intercept = y_mean - slope * x_mean
    similarity = _pearson_similarity(points, x_key, y_key)
    corr = similarity["pearson_r"]
    return {
        "slope": slope, "intercept": intercept,
        "r2": corr * corr if corr is not None else None,
        "sample_count": len(pairs),
    }


def _threshold_fit(points: list[dict], x_key: str, y_key: str) -> dict | None:
    """Find a two-line X threshold that materially improves on one linear fit."""
    pairs = []
    for row in points:
        try:
            x, y = float(row.get(x_key)), float(row.get(y_key))
        except (TypeError, ValueError):
            continue
        if math.isfinite(x) and math.isfinite(y):
            pairs.append({"x": x, "y": y})
    pairs.sort(key=lambda row: (row["x"], row["y"]))
    if len(pairs) < 8:
        return None
    overall = _linear_fit(pairs, "x", "y")
    if overall.get("slope") is None:
        return None

    def sse(rows: list[dict], fit: dict) -> float:
        return sum((row["y"] - (fit["slope"] * row["x"] + fit["intercept"])) ** 2 for row in rows)

    overall_sse = sse(pairs, overall)
    if overall_sse <= 1e-18:
        return None
    min_side = max(3, min(10, len(pairs) // 4))
    best = None
    for index in range(min_side, len(pairs) - min_side + 1):
        if pairs[index - 1]["x"] == pairs[index]["x"]:
            continue
        left_rows, right_rows = pairs[:index], pairs[index:]
        left_fit = _linear_fit(left_rows, "x", "y")
        right_fit = _linear_fit(right_rows, "x", "y")
        if left_fit.get("slope") is None or right_fit.get("slope") is None:
            continue
        piece_sse = sse(left_rows, left_fit) + sse(right_rows, right_fit)
        if best is None or piece_sse < best["piece_sse"]:
            best = {
                "threshold": (pairs[index - 1]["x"] + pairs[index]["x"]) / 2,
                "improvement": max(0.0, 1 - piece_sse / overall_sse),
                "piece_sse": piece_sse,
                "left_fit": left_fit, "right_fit": right_fit,
                "left_min": left_rows[0]["x"], "left_max": left_rows[-1]["x"],
                "right_min": right_rows[0]["x"], "right_max": right_rows[-1]["x"],
                "sample_count": len(pairs),
            }
    if best is None:
        return None
    best.pop("piece_sse", None)
    best["is_candidate"] = best["improvement"] >= 0.25
    return best


def _ml_table_path(product: str) -> Path | None:
    requested = f"ML_TABLE_{str(product or '').strip()}".casefold()
    try:
        files = [path for path in PATHS.base_root.iterdir() if path.is_file() and path.stem.casefold() == requested]
    except OSError:
        return None
    return sorted(files, key=lambda path: (path.suffix.lower() != ".parquet", path.name.casefold()))[0] if files else None


def fab_color_fields(product: str) -> list[str]:
    path = _ml_table_path(product)
    if path is None:
        return []
    lf = scan_one_file(path)
    if lf is None:
        return []
    return sorted(
        [name for name in _schema_names(lf) if name.upper().startswith(("FAB_", "SPLIT"))],
        key=_natural_text_key,
    )


def _fab_color_by_wafer(product: str, root_lot_id: str, color_field: str) -> dict[str, str]:
    path = _ml_table_path(product)
    if path is None or not color_field:
        return {}
    lf = scan_one_file(path)
    if lf is None:
        return {}
    columns = _schema_names(lf)
    by_lower = {name.casefold(): name for name in columns}
    root_col = by_lower.get("root_lot_id")
    wafer_col = by_lower.get("wafer_id")
    selected = by_lower.get(str(color_field).casefold())
    if not root_col or not wafer_col or not selected:
        return {}
    frame = (
        lf.filter(pl.col(root_col).cast(pl.String, strict=False) == str(root_lot_id))
        .select(
            pl.col(wafer_col).cast(pl.String, strict=False).alias("wafer"),
            pl.col(selected).cast(pl.String, strict=False).alias("color"),
        ).drop_nulls().group_by("wafer").agg(pl.col("color").first()).collect(streaming=True)
    )
    return {str(row["wafer"]): str(row["color"]) for row in frame.to_dicts()}


def _relation_key(product: str, left: str, right: str) -> str:
    pair = sorted([str(left), str(right)], key=str.casefold)
    return f"{str(product).casefold()}|{pair[0].casefold()}|{pair[1].casefold()}"


def load_relationships() -> list[dict]:
    raw = load_json(RELATIONSHIPS_PATH, {}) if RELATIONSHIPS_PATH.is_file() else {}
    rows = (raw.get("relationships") or []) if isinstance(raw, dict) else []
    return [dict(row) for row in rows if isinstance(row, dict)]


def save_relationship(payload: dict, username: str) -> dict:
    import datetime
    product = str(payload.get("product") or "").strip()
    left = str(payload.get("left_metric") or "").strip()
    right = str(payload.get("right_metric") or "").strip()
    status = str(payload.get("status") or "").strip().lower()
    if not product or not left or not right or status not in {"significant", "not_significant", "review"}:
        raise ValueError("제품·두 지표·유의차 분류가 필요합니다")
    record = {
        **payload,
        "product": product, "left_metric": left, "right_metric": right, "status": status,
        "key": _relation_key(product, left, right),
        "updated_by": str(username or ""),
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    }
    with _LOCK:
        rows = [row for row in load_relationships() if row.get("key") != record["key"]]
        rows.append(record)
        rows.sort(key=lambda row: str(row.get("key") or ""))
        save_json(RELATIONSHIPS_PATH, {"version": 1, "relationships": rows}, indent=2)
    return record


def compare_metric_relations(product: str, vehicle: str, root_lot_id: str,
                             metrics: list[dict], *, wafer_id: str = "",
                             color_source: str = "none", color_field: str = "",
                             split_source: str = "", target_metric_id: str = "",
                             tkout_from: str = "", tkout_to: str = "") -> dict:
    """Compare any 2+ Yield/ET/Inline shot metrics and rank every pair."""
    if len(metrics) < 2:
        raise ValueError("비교 지표를 2개 이상 선택해 주세요")
    maps: list[tuple[dict, dict]] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(metrics[:30]):
        metric = dict(raw or {})
        kind = str(metric.get("kind") or "").strip().lower()
        metric_id = str(metric.get("id") or f"metric_{index + 1}").strip()
        if kind not in {"yield", "et", "inline"} or not metric_id or metric_id in seen_ids:
            raise ValueError("지표 ID는 중복 없이 Yield/ET/Inline 유형으로 지정해 주세요")
        seen_ids.add(metric_id)
        if kind == "yield":
            selected_bin = str(metric.get("bin_name") or "yield")
            result = map_data(product, root_lot_id=root_lot_id, wafer_id=wafer_id, selected_bin=selected_bin)
            rows = result.get("shot_metrics") or []
            label = str(metric.get("label") or f"Yield · {selected_bin}")
            result = {**result, "rows": rows, "selected_item": selected_bin}
        else:
            result = shot_map_data(
                kind, product, vehicle, root_lot_id, wafer_id=wafer_id,
                item_id=str(metric.get("item_id") or ""), step_id=str(metric.get("step_id") or ""),
                step_seq=str(metric.get("step_seq") or ""),
                inline_table=str(metric.get("inline_table") or ""),
                split_source=split_source if color_source == "split" and kind == "et" else "",
                tkout_from=tkout_from, tkout_to=tkout_to,
            )
            label = str(metric.get("label") or f"{kind.upper()} · {result.get('selected_item') or 'value'}")
        maps.append(({**metric, "id": metric_id, "kind": kind, "label": label}, result))

    point_by_key: dict[tuple[str, float, float], dict] = {}
    for metric, result in maps:
        for row in result.get("rows") or []:
            if row.get("value") is None or row.get("shot_x") is None or row.get("shot_y") is None:
                continue
            key = (str(row.get("wafer") or ""), float(row["shot_x"]), float(row["shot_y"]))
            point = point_by_key.setdefault(key, {
                "root_lot_id": root_lot_id, "wafer_id": key[0], "wafer": key[0],
                "shot_x": key[1], "shot_y": key[2], "values": {}, "color": "(미지정)",
            })
            point["values"][metric["id"]] = row.get("value")
            if color_source == "split" and row.get("split"):
                point["color"] = str(row["split"])
    if color_source == "split" and split_source:
        split_rows = _split_rows(split_source, root_lot_id, wafer_id)
        wafer_split, shot_split = {}, {}
        for row in split_rows:
            wafer = str(row.get("wafer") or "")
            if row.get("shot_level"):
                shot_split[(wafer, float(row["shot_x"]), float(row["shot_y"]))] = str(row.get("split") or "")
            else:
                wafer_split[wafer] = str(row.get("split") or "")
        for key, point in point_by_key.items():
            point["color"] = shot_split.get(key, wafer_split.get(key[0], point["color"])) or "(미지정)"
    elif color_source == "fab":
        colors = _fab_color_by_wafer(product, root_lot_id, color_field)
        for key, point in point_by_key.items():
            point["color"] = colors.get(key[0], "(미지정)")

    points = sorted(point_by_key.values(), key=lambda row: (_natural_text_key(row["wafer"]), row["shot_y"], row["shot_x"]))
    saved = {row.get("key"): row for row in load_relationships()}
    pairs = []
    for left_index, (left, _left_map) in enumerate(maps):
        for right, _right_map in maps[left_index + 1:]:
            if target_metric_id and target_metric_id not in {left["id"], right["id"]}:
                continue
            pair_rows = [
                {**row, "left": row["values"].get(left["id"]), "right": row["values"].get(right["id"])}
                for row in points
                if row["values"].get(left["id"]) is not None and row["values"].get(right["id"]) is not None
            ]
            similarity = _pearson_similarity(pair_rows, "left", "right")
            groups = []
            for color in sorted({str(row.get("color") or "(미지정)") for row in pair_rows}, key=_natural_text_key):
                group_rows = [row for row in pair_rows if str(row.get("color") or "(미지정)") == color]
                groups.append({"color": color, "fit": _linear_fit(group_rows, "left", "right")})
            key = _relation_key(product, left["label"], right["label"])
            saved_row = saved.get(key)
            threshold = _threshold_fit(pair_rows, "left", "right")
            linear_score = abs(similarity["pearson_r"]) if similarity["pearson_r"] is not None else 0.0
            threshold_score = float(threshold.get("improvement") or 0.0) if threshold else 0.0
            learned_adjustment = 0.05 if saved_row and saved_row.get("status") == "significant" else (
                -0.03 if saved_row and saved_row.get("status") == "not_significant" else 0.0
            )
            pairs.append({
                "id": f"{left['id']}::{right['id']}",
                "left_id": left["id"], "right_id": right["id"],
                "left_label": left["label"], "right_label": right["label"],
                "pearson_r": similarity["pearson_r"], "sample_count": similarity["sample_count"],
                "fit": _linear_fit(pair_rows, "left", "right"), "group_fits": groups,
                "threshold": threshold,
                "relationship_score": max(linear_score, threshold_score),
                "priority_score": max(0.0, max(linear_score, threshold_score) + learned_adjustment),
                "saved": saved_row,
            })
    pairs.sort(key=lambda row: row["priority_score"], reverse=True)
    return {
        "product": product, "vehicle": vehicle, "root_lot_id": root_lot_id,
        "metrics": [metric for metric, _result in maps], "points": points,
        "pairs": pairs, "point_count": len(points),
        "geometry": next((result.get("geometry") for _metric, result in maps if result.get("geometry")), None),
        "target_metric_id": target_metric_id,
        "tkout_from": tkout_from, "tkout_to": tkout_to,
        "color_source": color_source, "color_field": color_field,
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
