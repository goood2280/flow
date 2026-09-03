"""core/matching_fill.py — 매칭 CSV의 공정 메타 열을 raw DB 스캔으로 채운다.

무엇을 하나
  `Vehicle_matching.csv` / `Inline_matching.csv` 는 어느 제품의 것인지가 비어 있는
  경우가 많다. 이 모듈은 각 raw DB(FAB/INLINE)의 **제품 폴더**를 돌면서 그 행의
  키(step_id, item_id)가 실제로 등장하는 제품을 찾아 `product` 를 채운다.
  두 제품에서 모두 나오면 `PRODA, PRODB` 처럼 이어 붙인다.

  `vm_matching.csv` 는 예외다 — (step_desc, item_id) 만 들고 있고 제품 귀속은
  `Vehicle_matching.csv` 가 step_desc 로 이미 정한다. 그래서 product 는 채우지
  않고, module 도 step 번호 구간표가 아니라 이름이 맞는 Vehicle_matching 행의
  module 을 그대로 가져온다 (SplitTable 의 VM module 해석과 같은 원천).

왜 바로 안 쓰나
  매칭 CSV 는 SplitTable/Valve/인폼이 모두 읽는 마스터다. 스캔 결과를 곧바로 덮어쓰면
  잘못된 한 번이 전 화면에 퍼진다. 그래서 스캔은 **제안(proposal)** 만 만들고,
  관리자가 행 단위로 확인한 뒤 `apply()` 에서만 파일이 바뀐다.

엔지니어 규칙
  step_id 앞 두 글자로 검사 대상 제품을 좁힐 수 있다(`prefix_rules`). 규칙이 없는
  prefix 는 전체 제품을 검사한다. 규칙이 있으면 그 제품들만 본다 — 사내 DB 가 커질수록
  "AA 는 A 계열만" 같은 사전 지식이 스캔 시간을 좌우한다.
"""
from __future__ import annotations

import datetime
import logging
import re
import threading
from pathlib import Path
from typing import Any, Iterable

from core.paths import PATHS
from core.utils import load_json, save_json

logger = logging.getLogger(__name__)

STORE_PATH = PATHS.data_root / "matching_fill.json"
PRODUCT_COL = "product"
_lock = threading.RLock()

# target → 매칭 CSV + 스캔할 raw DB 루트 후보 + 매칭 키
#   키는 "CSV 에도 있고 DB 에도 있는 열" 만 실제로 쓴다 (vm_matching 은 step_id 가 없어
#   item_id 만으로 맞추는 식). 후보 루트는 먼저 존재하는 것을 쓴다.
TARGETS: dict[str, dict[str, Any]] = {
    "vehicle": {
        "label": "Vehicle_matching",
        "file": "Vehicle_matching.csv",
        "db_roots": ("1.RAWDATA_DB_FAB", "1.RAWDATA_DB", "FAB"),
        "keys": ("step_id",),
        "id_cols": ("vehicle", "step_id", "step_desc"),
        "fill_columns": ("product", "module"),
        "module_source": "step_range",
    },
    "inline": {
        # 사내 DB 의 실제 파일명은 대문자 I 로 시작한다. Windows 개발 PC 는
        # 대소문자를 안 가려 예전 이름으로도 열렸지만 사내 Linux 서버에서는
        # 그대로 "파일 없음" 이었다 (_csv_path 가 대소문자 폴백도 한다).
        "label": "Inline_matching",
        "file": "Inline_matching.csv",
        "db_roots": ("1.RAWDATA_DB_INLINE", "INLINE"),
        "keys": ("step_id", "item_id"),
        "id_cols": ("item_id", "step_id", "item_desc"),
        "fill_columns": ("product", "module"),
        "module_source": "step_range",
    },
    "ppid": {
        # ppid_knob.csv의 value(PPID)를 FAB 원본 ppid와 대조해 실제 제품/step_id를
        # 찾고, 같은 제품+step_id의 Vehicle_matching에서 step_desc를 가져온다.
        "label": "PPID → FAB 공정",
        "file": "ppid_knob.csv",
        "db_roots": ("1.RAWDATA_DB_FAB", "1.RAWDATA_DB", "FAB"),
        "keys": ("value",),
        "db_key_aliases": {"value": ("ppid", "pp_id")},
        "id_cols": ("feature_name", "function_step", "value", "product", "step_id", "step_desc"),
        "fill_columns": ("product", "step_id", "step_desc"),
        "match_source": "fab_ppid",
    },
    "mask": {
        # mask_info.csv의 reticle_id를 FAB 원본 reticle_id와 대조해 제품/공정을 찾고,
        # 같은 제품+step_id의 Vehicle_matching에서 step_desc를 가져온다.
        "label": "MASK → FAB 공정",
        "file": "mask_info.csv",
        "db_roots": ("1.RAWDATA_DB_FAB", "1.RAWDATA_DB", "FAB"),
        "keys": ("reticle_id",),
        "id_cols": (
            "reticle_id", "mask_version", "mask_vendor", "photo_step",
            "product", "step_id", "step_desc",
        ),
        "fill_columns": ("product", "step_id", "step_desc"),
        "match_source": "fab_reticle",
    },
    "vm": {
        # vm_matching.csv 는 (step_desc, item_id) 만 들고 있다. 제품 귀속과
        # module 은 Vehicle_matching.csv 가 step_desc 로 이미 정해 두므로
        # 여기서 product 를 따로 채우지 않는다 — module 만 채운다.
        "label": "vm_matching",
        "file": "vm_matching.csv",
        "db_roots": ("1.RAWDATA_DB_VM", "VM"),
        "keys": ("step_id", "item_id"),
        "id_cols": ("item_id", "step_desc", "step_id"),
        "fill_columns": ("module",),
        "module_source": "vehicle_step_desc",
    },
}

DEFAULT_SETTINGS: dict[str, Any] = {
    # [{"prefix": "CC", "products": ["PRODA"], "targets": []}] — targets 비면 전체 적용
    "prefix_rules": [],
    # 제품 1개당 읽을 parquet 상한 (0 = 무제한). 사내 대용량 DB 보호용.
    "max_files_per_product": 0,
    # module 채우기 구간표 — [{"prefix":"AA","breaks":[{"from":100000,"module":"PC"}, ...]}]
    "module_rules": [],
}

# 채울 수 있는 열. PPID 대상은 FAB에서 product/step_id를 찾은 뒤 Vehicle에서
# step_desc를 보강한다. module은 기존 step 번호 구간표/Vehicle 이름 매칭을 쓴다.
FILL_COLUMNS = ("product", "step_id", "step_desc", "module")
MODULE_COL = "module"
# step_id = 앞 영문자 + 숫자(6자리 관행) + 꼬리. 구간 판정은 숫자 부분만 본다.
_STEP_RE = re.compile(r"^\s*([A-Za-z]+)\s*(\d+)")


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _db_root() -> Path:
    try:
        from app_v2.shared.source_adapter import resolve_existing_root
        return Path(resolve_existing_root("db", PATHS.db_root))
    except Exception:
        return Path(PATHS.db_root)


def _load_store() -> dict:
    data = load_json(STORE_PATH, {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("settings", {})
    data.setdefault("proposals", {})
    return data


def _save_store(data: dict) -> None:
    save_json(STORE_PATH, data)


# ────────────────────────────────────────── 설정
def settings() -> dict:
    raw = (_load_store().get("settings") or {})
    out = dict(DEFAULT_SETTINGS)
    rules = raw.get("prefix_rules")
    if isinstance(rules, list):
        out["prefix_rules"] = _clean_rules(rules)
    mod_rules = raw.get("module_rules")
    if isinstance(mod_rules, list):
        out["module_rules"] = _clean_module_rules(mod_rules)
    try:
        out["max_files_per_product"] = max(0, int(raw.get("max_files_per_product") or 0))
    except Exception:
        out["max_files_per_product"] = 0
    return out


def _clean_rules(rules: Iterable[Any]) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for item in rules or []:
        if not isinstance(item, dict):
            continue
        prefix = str(item.get("prefix") or "").strip().upper()[:8]
        if not prefix or prefix in seen:
            continue
        products = [str(p).strip() for p in (item.get("products") or []) if str(p).strip()]
        targets = [str(t).strip() for t in (item.get("targets") or []) if str(t).strip() in TARGETS]
        if not products:
            continue
        seen.add(prefix)
        out.append({"prefix": prefix, "products": products, "targets": targets})
    return out


def _clean_module_rules(rules: Iterable[Any]) -> list[dict]:
    """구간표 정리 — prefix 별로 시작 번호 오름차순. 다음 시작 직전까지가 그 module 이다."""
    out: list[dict] = []
    seen: set[str] = set()
    for item in rules or []:
        if not isinstance(item, dict):
            continue
        prefix = str(item.get("prefix") or "").strip().upper()[:8]
        if not prefix or prefix in seen:
            continue
        breaks: list[dict] = []
        break_seen: set[int] = set()
        for b in (item.get("breaks") or []):
            if not isinstance(b, dict):
                continue
            module = str(b.get("module") or "").strip()[:60]
            try:
                start = int(str(b.get("from")).strip())
            except Exception:
                continue
            if not module or start in break_seen:
                continue
            break_seen.add(start)
            breaks.append({"from": start, "module": module})
        if not breaks:
            continue
        breaks.sort(key=lambda x: x["from"])
        seen.add(prefix)
        out.append({"prefix": prefix, "breaks": breaks})
    return out


def _step_parts(step_id: str) -> tuple[str, int | None]:
    """step_id → (앞 영문자 2글자, 숫자). 숫자를 못 읽으면 (prefix, None)."""
    m = _STEP_RE.match(str(step_id or ""))
    if not m:
        return "", None
    return m.group(1).upper()[:2], int(m.group(2))


def module_for_step(step_id: str, rules: list[dict]) -> str:
    """앞 영문자 2글자가 같은 구간표에서 숫자 구간으로 module 을 정한다.

    100000→PC, 200000→RPMG 로 넣으면 100000~199999 는 PC, 200000 부터는 RPMG.
    첫 구간 시작보다 작은 번호는 매칭 없음("")이다.
    """
    prefix, number = _step_parts(step_id)
    if not prefix or number is None:
        return ""
    for rule in rules:
        if rule.get("prefix") != prefix:
            continue
        picked = ""
        for b in rule.get("breaks") or []:          # 이미 오름차순
            if number >= int(b.get("from", 0)):
                picked = str(b.get("module") or "")
            else:
                break
        return picked
    return ""


def save_settings(patch: dict, username: str = "") -> dict:
    with _lock:
        store = _load_store()
        cur = dict(store.get("settings") or {})
        if "prefix_rules" in patch:
            cur["prefix_rules"] = _clean_rules(patch.get("prefix_rules") or [])
        if "module_rules" in patch:
            cur["module_rules"] = _clean_module_rules(patch.get("module_rules") or [])
        if "max_files_per_product" in patch:
            try:
                cur["max_files_per_product"] = max(0, int(patch.get("max_files_per_product") or 0))
            except Exception:
                cur["max_files_per_product"] = 0
        cur["updated_at"], cur["updated_by"] = _now(), username or ""
        store["settings"] = cur
        _save_store(store)
    return settings()


# ────────────────────────────────────────── DB 스캔
def _target_db_dir(target: str) -> Path | None:
    spec = TARGETS.get(target) or {}
    root = _db_root()
    for name in spec.get("db_roots") or ():
        p = root / name
        if p.is_dir():
            return p
    return None


def list_products(target: str) -> list[str]:
    """raw DB 아래 제품 폴더 목록 (스캔 대상 후보)."""
    db_dir = _target_db_dir(target)
    if not db_dir:
        return []
    out = []
    for child in sorted(db_dir.iterdir()):
        name = child.name
        if not child.is_dir() or name.startswith((".", "_")):
            continue
        out.append(name)
    return out


def _product_files(target: str, product: str, limit: int = 0) -> list[Path]:
    db_dir = _target_db_dir(target)
    if not db_dir:
        return []
    p = db_dir / product
    if not p.is_dir():
        return []
    files = sorted((f for f in p.rglob("*.parquet") if "_backups" not in f.parts),
                   reverse=True)
    return files[:limit] if limit and limit > 0 else files


def _product_key_index(target: str, product: str, keys: tuple[str, ...], limit: int = 0) -> set[tuple]:
    """제품 폴더 전체에서 (keys) 조합의 유니크 집합을 만든다."""
    files = _product_files(target, product, limit)
    if not files:
        return set()
    try:
        import polars as pl
    except ImportError:
        logger.warning("[matching_fill] polars 미설치 — 스캔 불가")
        return set()
    try:
        lf = pl.scan_parquet([str(f) for f in files])
        schema = lf.collect_schema().names()
        folded = {str(name).strip().casefold(): name for name in schema}
        aliases = (TARGETS.get(target) or {}).get("db_key_aliases") or {}
        resolved: list[tuple[int, str]] = []
        for pos, key in enumerate(keys):
            candidates = [key, *(aliases.get(key) or ())]
            actual = next((folded.get(str(candidate).strip().casefold()) for candidate in candidates
                           if folded.get(str(candidate).strip().casefold())), None)
            if actual:
                resolved.append((pos, actual))
        if not resolved:
            return set()
        df = lf.select([
            pl.col(actual).cast(pl.Utf8).alias(f"_key_{pos}") for pos, actual in resolved
        ]).unique().collect()
    except Exception as e:
        logger.warning("[matching_fill] %s/%s 스캔 실패: %s", target, product, e)
        return set()
    rows = df.rows()
    if len(resolved) == len(keys):
        return {tuple("" if v is None else str(v).strip() for v in r) for r in rows}
    # DB 에 없는 키는 와일드카드로 둔다 — 있는 열만으로 비교하도록 위치를 맞춘다.
    out: set[tuple] = set()
    for r in rows:
        slot: list[str] = ["*"] * len(keys)
        for (pos, _actual), value in zip(resolved, r):
            slot[pos] = "" if value is None else str(value).strip()
        out.add(tuple(slot))
    return out


def _rule_products(step_id: str, target: str, rules: list[dict]) -> list[str] | None:
    """step_id 앞 두 글자 규칙 → 검사 대상 제품. 규칙이 없으면 None(전체 검사)."""
    head = str(step_id or "").strip().upper()[:2]
    if not head:
        return None
    for rule in rules:
        if rule.get("prefix") != head:
            continue
        targets = rule.get("targets") or []
        if targets and target not in targets:
            continue
        return list(rule.get("products") or [])
    return None


def _csv_path(target: str) -> Path:
    """대상 CSV 경로. 선언한 이름이 없으면 대소문자만 다른 파일을 찾아 쓴다.

    사내 DB 는 `Inline_matching.csv` 처럼 대문자로 시작하는 이름을 쓴다. 개발
    PC(Windows)는 대소문자를 안 가려 아무 철자로도 열리지만 사내 Linux 서버는
    가린다 — 철자가 하나 어긋나면 화면에 "CSV 없음" 만 뜬다.
    """
    root = _db_root()
    name = str((TARGETS.get(target) or {}).get("file") or "")
    fp = root / name
    if not name or fp.is_file():
        return fp
    try:
        wanted = name.casefold()
        for child in root.iterdir():
            if child.is_file() and child.name.casefold() == wanted:
                return child
    except Exception:
        pass
    return fp


def target_fill_columns(target: str) -> list[str]:
    """이 대상에서 채울 수 있는 열. 선언이 없으면 전부 허용(예전 동작)."""
    spec = TARGETS.get(target) or {}
    allowed = tuple(spec.get("fill_columns") or FILL_COLUMNS)
    return [c for c in FILL_COLUMNS if c in allowed]


def _step_desc_key(value: Any) -> str:
    """step_desc 비교 키 — 공백 접기 + 대소문자 무시 (SplitTable 과 같은 규약)."""
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _row_value_ci(row: dict, name: str) -> str:
    """대소문자/공백 차이를 무시하고 열 값을 읽는다."""
    if name in row:
        return str(row.get(name) or "").strip()
    wanted = str(name or "").strip().casefold()
    for key, value in row.items():
        if str(key or "").strip().casefold() == wanted:
            return str(value or "").strip()
    return ""


def vehicle_module_by_step_desc() -> dict[str, str]:
    """Vehicle_matching.csv 의 step_desc → module 맵.

    vm_matching 의 module 원천이다. 같은 step_desc 가 여러 줄이면 처음 나온
    비어 있지 않은 module 을 쓴다 (SplitTable 의 module 해석과 같은 원천).
    """
    _cols, rows = _read_csv("vehicle")
    out: dict[str, str] = {}
    for row in rows:
        key = _step_desc_key(_row_value_ci(row, "step_desc"))
        module = _row_value_ci(row, MODULE_COL)
        if key and module and key not in out:
            out[key] = module
    return out


def _read_csv(target: str) -> tuple[list[str], list[dict]]:
    fp = _csv_path(target)
    if not fp.is_file():
        return [], []
    from core.valve_alerts import _read_csv_preserving
    cols, rows = _read_csv_preserving(fp)
    return list(cols), rows


def _resolve_column(names: Iterable[str], *candidates: str) -> str:
    folded = {str(name).strip().casefold(): str(name) for name in names or []}
    for candidate in candidates:
        hit = folded.get(str(candidate or "").strip().casefold())
        if hit:
            return hit
    return ""


def _fab_value_step_index(target: str, product: str, value_candidates: tuple[str, ...],
                          value_label: str, limit: int = 0) -> dict[str, list[str]]:
    """한 FAB 제품의 공정 식별자(casefold) → step_id 목록."""
    files = _product_files(target, product, limit)
    if not files:
        return {}
    try:
        import polars as pl
        lf = pl.scan_parquet([str(f) for f in files])
        schema = lf.collect_schema().names()
        value_col = _resolve_column(schema, *value_candidates)
        step_col = _resolve_column(schema, "step_id", "stepid", "step")
        if not value_col or not step_col:
            return {}
        frame = lf.select([
            pl.col(value_col).cast(pl.Utf8).alias("value"),
            pl.col(step_col).cast(pl.Utf8).alias("step_id"),
        ]).drop_nulls().unique(maintain_order=True).collect()
    except Exception as e:
        logger.warning("[matching_fill] FAB %s 공정 스캔 실패 %s/%s: %s",
                       value_label, target, product, e)
        return {}
    out: dict[str, list[str]] = {}
    for value, step_id in frame.rows():
        key = str(value or "").strip().casefold()
        sid = str(step_id or "").strip()
        if key and sid and sid not in out.setdefault(key, []):
            out[key].append(sid)
    return out


def _fab_ppid_step_index(target: str, product: str, limit: int = 0) -> dict[str, list[str]]:
    """한 FAB 제품의 PPID(casefold) → step_id 목록."""
    return _fab_value_step_index(target, product, ("ppid", "pp_id"), "PPID", limit)


def _fab_reticle_step_index(target: str, product: str, limit: int = 0) -> dict[str, list[str]]:
    """한 FAB 제품의 reticle_id(casefold) → step_id 목록."""
    return _fab_value_step_index(
        target, product, ("reticle_id", "reticle", "reticleid"), "reticle_id", limit,
    )


def _vehicle_step_desc_lookup() -> tuple[dict[tuple[str, str], tuple[str, int]], dict[str, tuple[str, int]]]:
    """(product, step_id) 및 step_id fallback → (step_desc, 설정 행 순서)."""
    _cols, rows = _read_csv("vehicle")
    exact: dict[tuple[str, str], tuple[str, int]] = {}
    fallback: dict[str, tuple[str, int]] = {}
    for order, row in enumerate(rows):
        sid = _row_value_ci(row, "step_id")
        desc = _row_value_ci(row, "step_desc")
        products = [x.strip() for x in re.split(r"[,;|]+", _row_value_ci(row, PRODUCT_COL)) if x.strip()]
        if not sid:
            continue
        sid_key = sid.casefold()
        # product가 비어 있는 공통 Vehicle 행만 fallback으로 쓴다. 다른 제품의 같은
        # step_id를 가져오면 PPID→제품→step 연결이 어긋날 수 있다.
        if not products:
            fallback.setdefault(sid_key, (desc, order))
        for product in products:
            exact.setdefault((product.casefold(), sid_key), (desc, order))
    return exact, fallback


def _scan_fab_process(target: str, spec: dict, cols: list[str], rows: list[dict],
                      username: str, column: str, *, csv_keys: tuple[str, ...],
                      index_builder, match_source: str) -> dict:
    """CSV 공정 식별자를 FAB와 연결해 product/step_id/step_desc를 제안한다."""
    cfg = settings()
    products = list_products(target)
    if not products:
        raise FileNotFoundError("FAB raw DB 제품 폴더를 찾을 수 없습니다")
    limit = cfg["max_files_per_product"]
    value_indexes = {product: index_builder(target, product, limit) for product in products}
    vehicle_exact, vehicle_fallback = _vehicle_step_desc_lookup()

    out_rows: list[dict] = []
    counts = {"fill": 0, "change": 0, "same": 0, "miss": 0}
    for i, row in enumerate(rows):
        source_value = next((_row_value_ci(row, key) for key in csv_keys if _row_value_ci(row, key)), "")
        matches: list[dict] = []
        for product in products:
            for sid in value_indexes.get(product, {}).get(source_value.casefold(), []):
                desc, order = vehicle_exact.get(
                    (product.casefold(), sid.casefold()),
                    vehicle_fallback.get(sid.casefold(), ("", 10**9)),
                )
                matches.append({"product": product, "step_id": sid, "step_desc": desc, "order": order})
        matches.sort(key=lambda item: (item["order"], item["product"].casefold(), item["step_id"].casefold()))

        values: list[str] = []
        seen: set[str] = set()
        for match in matches:
            value = str(match.get(column) or "").strip()
            key = value.casefold()
            if value and key not in seen:
                seen.add(key)
                values.append(value)
        current = _row_value_ci(row, column) if _resolve_column(cols, column) else ""
        proposed = ", ".join(values)
        if not proposed:
            status = "miss"
        elif not current:
            status = "fill"
        elif current == proposed:
            status = "same"
        else:
            status = "change"
        counts[status] += 1
        out_rows.append({
            "i": i,
            "keys": {csv_keys[0]: source_value},
            "id": {c: row.get(c, "") for c in (spec.get("id_cols") or ()) if c in cols},
            "current": current,
            "proposed": proposed,
            "status": status,
            "scoped": [" · ".join(filter(None, (
                str(m.get("product") or ""), str(m.get("step_id") or ""), str(m.get("step_desc") or ""),
            ))) for m in matches],
        })

    proposal = {
        "target": target,
        "column": column,
        "file": spec["file"],
        "keys": [csv_keys[0]],
        "products": products,
        "add_column": column not in cols,
        "rows": out_rows,
        "counts": counts,
        "scanned_at": _now(),
        "scanned_by": username or "",
        "applied": False,
        "match_source": match_source,
    }
    with _lock:
        store = _load_store()
        store.setdefault("proposals", {})[_proposal_key(target, column)] = proposal
        _save_store(store)
    return proposal


def _scan_ppid_fab(target: str, spec: dict, cols: list[str], rows: list[dict],
                    username: str, column: str) -> dict:
    """ppid_knob.value를 FAB ppid에 연결해 product/step_id/step_desc를 제안한다."""
    return _scan_fab_process(
        target, spec, cols, rows, username, column,
        csv_keys=("value", "ppid"), index_builder=_fab_ppid_step_index,
        match_source="fab_ppid",
    )


def _scan_reticle_fab(target: str, spec: dict, cols: list[str], rows: list[dict],
                       username: str, column: str) -> dict:
    """mask_info.csv.reticle_id를 FAB reticle_id에 연결해 공정 메타를 제안한다."""
    if not _resolve_column(cols, "reticle_id", "reticle", "reticleid"):
        raise ValueError(f"{spec['file']} 에 reticle_id 열이 없습니다")
    return _scan_fab_process(
        target, spec, cols, rows, username, column,
        csv_keys=("reticle_id", "reticle", "reticleid"),
        index_builder=_fab_reticle_step_index, match_source="fab_reticle",
    )


# ────────────────────────────────────────── 제안 생성
def _proposal_key(target: str, column: str) -> str:
    return f"{target}:{column}"


def scan(target: str, username: str = "", column: str = "product") -> dict:
    """매칭 CSV 전 행을 검사해 제안을 만든다 (파일은 건드리지 않는다).

    column="product" 는 raw DB 스캔, column="module" 은 step 번호 구간표로 정한다.
    """
    if target not in TARGETS:
        raise ValueError(f"알 수 없는 대상: {target}")
    if column not in FILL_COLUMNS:
        raise ValueError(f"알 수 없는 열: {column}")
    spec = TARGETS[target]
    if column not in target_fill_columns(target):
        raise ValueError(
            f"{spec['label']} 에는 {column} 을 채우지 않습니다 — "
            "제품 귀속은 Vehicle_matching.csv 가 step_desc 로 이미 정합니다."
        )
    cols, rows = _read_csv(target)
    if not cols:
        raise FileNotFoundError(f"{spec['file']} 을 찾을 수 없습니다 ({_db_root()})")
    if str(spec.get("match_source") or "") == "fab_ppid":
        return _scan_ppid_fab(target, spec, cols, rows, username, column)
    if str(spec.get("match_source") or "") == "fab_reticle":
        return _scan_reticle_fab(target, spec, cols, rows, username, column)
    if column == MODULE_COL:
        if str(spec.get("module_source") or "step_range") == "vehicle_step_desc":
            return _scan_module_by_vehicle(target, spec, cols, rows, username)
        return _scan_module(target, spec, cols, rows, username)

    keys = tuple(k for k in spec["keys"] if k in cols)
    if not keys:
        raise ValueError(f"{spec['file']} 에 매칭 키({', '.join(spec['keys'])})가 없습니다")

    cfg = settings()
    rules = cfg["prefix_rules"]
    limit = cfg["max_files_per_product"]
    products = list_products(target)
    if not products:
        raise FileNotFoundError(f"{target} raw DB 제품 폴더를 찾을 수 없습니다")

    index_cache: dict[str, set[tuple]] = {}

    def index_of(product: str) -> set[tuple]:
        if product not in index_cache:
            index_cache[product] = _product_key_index(target, product, keys, limit)
        return index_cache[product]

    step_col = "step_id" if "step_id" in cols else ""
    out_rows: list[dict] = []
    counts = {"fill": 0, "change": 0, "same": 0, "miss": 0}
    for i, row in enumerate(rows):
        key = tuple(str(row.get(k) or "").strip() for k in keys)
        current = str(row.get(PRODUCT_COL) or "").strip()
        scoped = _rule_products(row.get(step_col) or "", target, rules) if step_col else None
        candidates = [p for p in (scoped if scoped is not None else products)]
        hits = []
        for product in candidates:
            if product not in products:
                continue          # 규칙에 적힌 제품이 DB 에 없으면 조용히 건너뛴다
            idx = index_of(product)
            if key in idx or _wildcard_hit(key, idx):
                hits.append(product)
        proposed = ", ".join(hits)
        if not hits:
            status = "miss"
        elif not current:
            status = "fill"
        elif current == proposed:
            status = "same"
        else:
            status = "change"
        counts[status] += 1
        out_rows.append({
            "i": i,
            "keys": {k: row.get(k, "") for k in keys},
            "id": {c: row.get(c, "") for c in (spec.get("id_cols") or ()) if c in cols},
            "current": current,
            "proposed": proposed,
            "status": status,
            "scoped": scoped or [],
        })

    proposal = {
        "target": target,
        "file": spec["file"],
        "keys": list(keys),
        "products": products,
        "add_column": PRODUCT_COL not in cols,
        "rows": out_rows,
        "counts": counts,
        "scanned_at": _now(),
        "scanned_by": username or "",
        "applied": False,
    }
    with _lock:
        store = _load_store()
        store.setdefault("proposals", {})[_proposal_key(target, PRODUCT_COL)] = proposal
        _save_store(store)
    return proposal


def _scan_module(target: str, spec: dict, cols: list[str], rows: list[dict], username: str) -> dict:
    """module 채우기 — DB 를 읽지 않고 step 번호 구간표만 본다."""
    if "step_id" not in cols:
        raise ValueError(f"{spec['file']} 에 step_id 열이 없어 module 구간을 정할 수 없습니다")
    rules = settings()["module_rules"]
    if not rules:
        raise ValueError("module 구간표가 비어 있습니다. 먼저 구간을 등록하세요.")

    out_rows: list[dict] = []
    counts = {"fill": 0, "change": 0, "same": 0, "miss": 0}
    for i, row in enumerate(rows):
        step_id = str(row.get("step_id") or "").strip()
        current = str(row.get(MODULE_COL) or "").strip()
        proposed = module_for_step(step_id, rules)
        prefix, number = _step_parts(step_id)
        if not proposed:
            status = "miss"
        elif not current:
            status = "fill"
        elif current == proposed:
            status = "same"
        else:
            status = "change"
        counts[status] += 1
        out_rows.append({
            "i": i,
            "keys": {"step_id": step_id},
            "id": {c: row.get(c, "") for c in (spec.get("id_cols") or ()) if c in cols},
            "current": current,
            "proposed": proposed,
            "status": status,
            "scoped": [f"{prefix}{number}"] if prefix and number is not None else [],
        })

    proposal = {
        "target": target,
        "column": MODULE_COL,
        "file": spec["file"],
        "keys": ["step_id"],
        "products": [],
        "add_column": MODULE_COL not in cols,
        "rows": out_rows,
        "counts": counts,
        "scanned_at": _now(),
        "scanned_by": username or "",
        "applied": False,
    }
    with _lock:
        store = _load_store()
        store.setdefault("proposals", {})[_proposal_key(target, MODULE_COL)] = proposal
        _save_store(store)
    return proposal


def _scan_module_by_vehicle(target: str, spec: dict, cols: list[str], rows: list[dict],
                            username: str) -> dict:
    """vm_matching 의 module — Vehicle_matching 에서 이름이 맞는 step_desc 의 module.

    vm_matching.csv 에는 step_id 가 없어 step 번호 구간표를 쓸 수 없다. 대신
    SplitTable 이 VM 행을 풀 때와 같은 경로(step_desc → Vehicle_matching)로
    module 을 가져온다 — 두 화면이 같은 원천을 보게 하려는 것이다.
    """
    desc_col = next((c for c in cols if str(c).strip().casefold() == "step_desc"), "")
    if not desc_col:
        raise ValueError(f"{spec['file']} 에 step_desc 열이 없어 module 을 정할 수 없습니다")
    module_map = vehicle_module_by_step_desc()
    if not module_map:
        raise ValueError(
            "Vehicle_matching.csv 에서 step_desc→module 을 읽지 못했습니다. "
            "먼저 Vehicle_matching 의 module 열을 채우세요."
        )

    out_rows: list[dict] = []
    counts = {"fill": 0, "change": 0, "same": 0, "miss": 0}
    for i, row in enumerate(rows):
        step_desc = str(row.get(desc_col) or "").strip()
        current = str(row.get(MODULE_COL) or "").strip()
        proposed = module_map.get(_step_desc_key(step_desc), "")
        if not proposed:
            status = "miss"
        elif not current:
            status = "fill"
        elif current == proposed:
            status = "same"
        else:
            status = "change"
        counts[status] += 1
        out_rows.append({
            "i": i,
            "keys": {"step_desc": step_desc},
            "id": {c: row.get(c, "") for c in (spec.get("id_cols") or ()) if c in cols},
            "current": current,
            "proposed": proposed,
            "status": status,
            # '검사 범위' 칸에 어떤 step_desc 로 맞췄는지 그대로 보여준다.
            "scoped": [step_desc] if step_desc else [],
        })

    proposal = {
        "target": target,
        "column": MODULE_COL,
        "file": spec["file"],
        "keys": ["step_desc"],
        "products": [],
        "add_column": MODULE_COL not in cols,
        "rows": out_rows,
        "counts": counts,
        "scanned_at": _now(),
        "scanned_by": username or "",
        "applied": False,
        "module_source": "vehicle_step_desc",
    }
    with _lock:
        store = _load_store()
        store.setdefault("proposals", {})[_proposal_key(target, MODULE_COL)] = proposal
        _save_store(store)
    return proposal


def _wildcard_hit(key: tuple, index: set[tuple]) -> bool:
    """DB 에 없던 키 자리는 '*' 로 들어 있다 — 그 자리는 비교에서 뺀다."""
    if not index:
        return False
    for cand in index:
        if all(c == "*" or c == k for c, k in zip(cand, key)):
            return True
    return False


def get_proposal(target: str, column: str = "product") -> dict | None:
    proposal = (_load_store().get("proposals") or {}).get(_proposal_key(target, column))
    # 대상 파일 계약이 바뀐 뒤에도 디스크에 남은 예전 제안을 새 CSV의 같은 행 번호에
    # 적용하면 전혀 다른 행을 덮어쓸 수 있다. 현재 정본 파일에서 만든 제안만 돌려준다.
    expected_file = str((TARGETS.get(target) or {}).get("file") or "")
    if proposal and str(proposal.get("file") or "") != expected_file:
        return None
    return proposal


def discard(target: str, column: str = "product") -> None:
    with _lock:
        store = _load_store()
        (store.get("proposals") or {}).pop(_proposal_key(target, column), None)
        _save_store(store)


# ────────────────────────────────────────── 반영 (관리자)
def apply_proposal(target: str, username: str = "", skip_rows: Iterable[int] | None = None,
                   column: str = "product", expected_scanned_at: str = "") -> dict:
    """관리자 확인이 끝난 제안을 CSV 에 쓴다. 대상 열이 없으면 **맨 왼쪽**에 만든다."""
    if column not in FILL_COLUMNS:
        raise ValueError(f"알 수 없는 열: {column}")
    if column not in target_fill_columns(target):
        raise ValueError(f"{target} 대상에는 {column} 열을 채울 수 없습니다")
    proposal = get_proposal(target, column)
    if not proposal:
        raise LookupError("반영할 제안이 없습니다. 먼저 검사를 실행하세요.")
    if proposal.get("applied"):
        raise ValueError("이미 반영된 제안입니다. 다시 검사해 주세요.")
    if not expected_scanned_at or str(proposal.get("scanned_at") or "") != str(expected_scanned_at):
        raise ValueError("미리보기 이후 제안이 바뀌었습니다. 전체 Before/After를 다시 확인해 주세요.")

    skip = {int(x) for x in (skip_rows or [])}
    fp = _csv_path(target)
    from core.valve_alerts import _read_csv_preserving, _write_csv_atomic, _after_write

    with _lock:
        cols, rows = _read_csv_preserving(fp)
        if column not in cols:
            cols = [column] + cols               # 없으면 제일 왼쪽에 새로 만든다
            for r in rows:
                r.setdefault(column, "")
        if target in {"ppid", "mask"}:
            # 세 열을 어느 순서로 반영해도 최종 CSV 헤더는 항상 같은 순서다.
            front = [name for name in (PRODUCT_COL, "step_id", "step_desc") if name in cols]
            cols = front + [name for name in cols if name not in front]
        selected = []
        for item in proposal.get("rows") or []:
            i = int(item.get("i", -1))
            if i in skip or i < 0 or i >= len(rows):
                continue
            if item.get("status") in ("miss", "same"):
                continue
            actual = _row_value_ci(rows[i], column)
            expected = str(item.get("current") or "").strip()
            if actual != expected:
                raise ValueError(
                    f"{proposal['file']} {i + 1}행의 {column} 값이 검사 후 변경되었습니다. "
                    "다시 검사해 전체 Before/After를 확인해 주세요."
                )
            selected.append((i, item))
        changed = 0
        for i, item in selected:
            rows[i][column] = item.get("proposed") or ""
            changed += 1
        _write_csv_atomic(fp, cols, rows)
        note = f"[매칭 {column} 채우기] {proposal['file']} {changed}행 반영"
        post = _after_write(fp, username or "flow", note)

        store = _load_store()
        cur = (store.get("proposals") or {}).get(_proposal_key(target, column))
        if cur:
            cur["applied"] = True
            cur["applied_at"] = _now()
            cur["applied_by"] = username or ""
            cur["applied_rows"] = changed
            cur["skipped_rows"] = sorted(skip)
            _save_store(store)

    return {"ok": True, "file": proposal["file"], "changed": changed,
            "added_column": bool(proposal.get("add_column")), **post}
