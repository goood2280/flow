"""분석의뢰 — 의뢰서(의뢰 내용 + 대상 Lot) × 실제 진행(SplitTable) × ET 측정.

흐름은 랏 배정/요청과 같다(의뢰 → 답글 → 처리 상태 → 이력). 의뢰서는 정형화된 양식의
의뢰 내용과 대상 Lot 만 받는다. 실제 진행과 측정이 의뢰와 맞는지는 보고서를 쓰는 엔지니어가
**SplitTable 과 같은 화면**(랏별로 wafer 가 열, 항목이 행)에서 직접 확인한다. 그 표 아래에
wafer 별 ET 측정(DC layer 단위 행)을 같은 wafer 열에 맞춰 붙인다.

- 실제 진행: ``informs.splittable_embed.build_splittable_embed`` — SplitTable/인폼 스냅샷과
  같은 규약(plan 표시·불일치·미진행 회색)을 그대로 쓴다.
- ET 측정: ET 추적과 같은 제품 ET history 캐시에서 wafer 별 측정 이력을 누적한다.
  ET 추적 스캔(``et_tracker.run_scan``)이 돌 때 같이 갱신된다. step_id → DC layer 해석은
  flow 공용 매핑(``core.dc_layer_mapping``)을 **보여줄 때마다** 다시 적용한다(매핑을 고치면 즉시 반영).
- 보고서: 엔지니어가 Template Report 로 만든 결과(PPTX)를 답글에 첨부한다. 비슷한 의뢰는
  기존 Template 을 이 의뢰의 랏 · wafer slot · split 에 맞게 규칙(+선택적 LLM)으로 바꿔 쓴다.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import threading
from pathlib import Path
from typing import Any

from core.paths import PATHS
from core.utils import jsonl_append, load_json, save_json

logger = logging.getLogger("flow.analysis_requests")

STORE_DIR: Path = PATHS.data_root / "analysis_requests"
LOCK = threading.RLock()
_SCAN_LOCK = threading.Lock()

MAX_SPLIT_COLUMNS = 60
MAX_LOTS = 30
MAX_WAFERS_PER_LOT = 25
DEFAULT_KNOB_LIMIT = 60        # 표시 열을 따로 고르지 않았을 때 보여줄 KNOB_ 열 상한
ET_HISTORY_CAP = 500           # wafer 당 누적 이력 상한 (ET 추적 lot 행과 같은 값)
ET_QUERY_LIMIT = 12_500
IDENTITY_KEYS = ("root_lot_id", "lot_id", "wafer_id", "product")
DEFAULT_REQUEST_TYPES = ["변경점(ECN) 평가", "Split 비교", "재측정 확인"]


def requests_file() -> Path:
    return STORE_DIR / "requests.json"


def audit_file() -> Path:
    return STORE_DIR / "audit.jsonl"


def config_file() -> Path:
    return STORE_DIR / "config.json"


def uploads_dir() -> Path:
    return STORE_DIR / "uploads"


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def clean(value: object, *, max_len: int = 4000) -> str:
    return str(value or "").strip()[:max_len]


def load_rows() -> list[dict]:
    raw = load_json(requests_file(), [])
    return [row for row in raw if isinstance(row, dict)] if isinstance(raw, list) else []


def save_rows(rows: list[dict]) -> None:
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    save_json(requests_file(), rows, indent=2)


def audit(action: str, actor: str, request_id: str, **detail) -> None:
    try:
        STORE_DIR.mkdir(parents=True, exist_ok=True)
        jsonl_append(audit_file(), {
            "at": now_iso(), "action": action, "actor": actor,
            "request_id": request_id, **detail,
        }, add_timestamp=False)
    except Exception as exc:  # 감사 로그 실패가 저장을 막으면 안 된다
        logger.warning("analysis request audit failed: %s", exc)


# ─────────────────────────── 입력 정규화 ───────────────────────────

def clean_product(value: object) -> str:
    raw = clean(value, max_len=120)
    return re.sub(r"^ML_TABLE_", "", raw, flags=re.I).strip()


def wafer_key(value: object) -> str:
    """wafer 표기 통일 — ML_TABLE·ET history 가 '01' / 'W1' / '#1' 로 달라도 같은 wafer."""
    text = str(value or "").strip().upper()
    if not text:
        return ""
    core = re.sub(r"^(?:#|WAFER|WF|W)\s*", "", text).strip()
    try:
        num = float(core)
    except Exception:
        return text
    return str(int(num)) if num.is_integer() else text


def wafer_selection(value: object) -> list[str]:
    """'1,2' / '1~5' / 'all' → wafer 목록. 'all'·빈칸은 [] (실제 wafer 는 데이터에서 찾는다)."""
    text = clean(value, max_len=200)
    if not text or text.lower() in {"all", "전체", "*"}:
        return []
    out: list[str] = []
    for token in re.split(r"[,;\s]+", text):
        token = token.strip()
        if not token:
            continue
        if "~" in token or re.fullmatch(r"\d+-\d+", token):
            left, right = re.split(r"[~-]", token, maxsplit=1)
            try:
                start, end = int(left), int(right)
            except ValueError:
                continue
            step = 1 if end >= start else -1
            out.extend(str(n) for n in range(start, end + step, step) if 1 <= n <= 50)
        else:
            key = wafer_key(token)
            if key:
                out.append(key)
    return list(dict.fromkeys(out))[:MAX_WAFERS_PER_LOT]


def split_tokens(value: object, *, limit: int, max_len: int = 160) -> list[str]:
    if isinstance(value, (list, tuple)):
        items = [str(v or "") for v in value]
    else:
        items = re.split(r"[,\n;]+", str(value or ""))
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        token = item.strip()[:max_len]
        if token and token.casefold() not in seen:
            seen.add(token.casefold())
            out.append(token)
        if len(out) >= limit:
            break
    return out


def normalize_split_columns(value: object) -> list[str]:
    return [col for col in split_tokens(value, limit=MAX_SPLIT_COLUMNS)
            if col.casefold() not in IDENTITY_KEYS]


_LOT_TOKEN = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:[:(]\s*([^)]*?)\s*\)?|\s+(.+?))?\s*$")


def normalize_lots(value: object) -> list[dict]:
    """대상 Lot — 'A7001, B7002' / 'A7001(1~12)' / 'A7001:1,3,5' / 줄마다 하나. wafer 를 안 적으면 전체."""
    items: list[dict] = []
    if isinstance(value, list):
        for raw in value:
            if isinstance(raw, dict):
                items.append({"root_lot_id": raw.get("root_lot_id"), "wafer_id": raw.get("wafer_id")})
            else:
                items.extend(normalize_lots(str(raw or "")))
    else:
        for token in re.split(r"[\n;]+|,(?![^()]*\))", str(value or "")):
            match = _LOT_TOKEN.match(token)
            if match and match.group(1):
                items.append({"root_lot_id": match.group(1), "wafer_id": match.group(2) or match.group(3) or ""})
    out: list[dict] = []
    seen: set[str] = set()
    for raw in items:
        root = clean(raw.get("root_lot_id"), max_len=80).upper()
        if not root or root in seen:
            continue
        seen.add(root)
        wafers = wafer_selection(raw.get("wafer_id"))
        out.append({"root_lot_id": root, "wafer_id": ",".join(wafers)})
        if len(out) >= MAX_LOTS:
            break
    return out


def lots_text(lots: list[dict]) -> str:
    return ", ".join(f"{lot['root_lot_id']}({compact_wafers(lot['wafer_id'].split(','))})" if lot.get("wafer_id")
                     else lot["root_lot_id"] for lot in lots or [])


def request_lots(item: dict) -> list[dict]:
    """의뢰의 대상 Lot. 예전 의뢰 표(plan_rows)만 있는 기록은 root 별로 묶어 읽는다."""
    lots = item.get("lots")
    if isinstance(lots, list) and lots:
        return normalize_lots(lots)
    merged: dict[str, list[str]] = {}
    for row in item.get("plan_rows") or []:
        root = clean(row.get("root_lot_id"), max_len=80).upper()
        if root:
            merged.setdefault(root, []).extend(wafer_selection(row.get("wafer_id")))
    return [{"root_lot_id": root, "wafer_id": ",".join(dict.fromkeys(w))} for root, w in merged.items()]


# ─────────────────────────── 설정(톱니바퀴) ───────────────────────────

def _normalize_template(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    name = clean(raw.get("name"), max_len=80)
    if not name:
        return None
    return {
        "id": clean(raw.get("id"), max_len=40) or re.sub(r"[^0-9A-Za-z가-힣]+", "-", name).strip("-")[:40] or "template",
        "name": name,
        "request_type": clean(raw.get("request_type"), max_len=80),
        "title": clean(raw.get("title"), max_len=240),
        "details": str(raw.get("details") or "")[:200000],
        "split_columns": normalize_split_columns(raw.get("split_columns")),
        "report_template_id": clean(raw.get("report_template_id"), max_len=80),
    }


def load_config() -> dict:
    raw = load_json(config_file(), {})
    raw = raw if isinstance(raw, dict) else {}
    types = split_tokens(raw.get("request_types"), limit=50, max_len=80)
    teams = split_tokens(raw.get("request_teams"), limit=100, max_len=120)
    templates = [tpl for tpl in (_normalize_template(item) for item in raw.get("templates") or []) if tpl]
    default_id = clean(raw.get("default_template_id"), max_len=40)
    if templates and default_id not in {tpl["id"] for tpl in templates}:
        default_id = templates[0]["id"]
    return {
        "request_types": types or list(DEFAULT_REQUEST_TYPES),
        "request_teams": teams,
        "templates": templates,
        "default_template_id": default_id if templates else "",
        "notify_on_response": raw.get("notify_on_response", True) is not False,
    }


def save_config(payload: dict, actor: str) -> dict:
    templates: list[dict] = []
    seen: set[str] = set()
    for item in payload.get("templates") or []:
        tpl = _normalize_template(item)
        if not tpl:
            continue
        base, n = tpl["id"], 2
        while tpl["id"] in seen:
            tpl["id"] = f"{base}-{n}"
            n += 1
        seen.add(tpl["id"])
        templates.append(tpl)
    config = {
        "request_types": split_tokens(payload.get("request_types"), limit=50, max_len=80) or list(DEFAULT_REQUEST_TYPES),
        "request_teams": split_tokens(payload.get("request_teams"), limit=100, max_len=120),
        "templates": templates[:30],
        "default_template_id": clean(payload.get("default_template_id"), max_len=40),
        "notify_on_response": payload.get("notify_on_response", True) is not False,
        "updated_at": now_iso(),
        "updated_by": actor,
    }
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    save_json(config_file(), config, indent=2)
    return load_config()


# ─────────────────────────── 진행 요약 (저장 결과만 보는 계산) ───────────────────────────

def _dc_layers() -> dict[str, str]:
    try:
        from core.dc_layer_mapping import step_to_layer
        return step_to_layer()
    except Exception:
        return {}


def entry_layer(entry: dict, layers: dict[str, str]) -> str:
    """ET 측정 1건의 DC layer — 지금 매핑 우선, 없으면 측정 당시 값, 그래도 없으면 step_id."""
    step = str(entry.get("step_id") or "").strip().upper()
    return layers.get(step) or str(entry.get("dc_layer") or "").strip() or step or "-"


def build_progress(item: dict, layers: dict[str, str] | None = None) -> dict:
    """대상 Lot × 마지막 갱신 결과 → wafer 행 + 요약. 조회는 하지 않는다."""
    layers = _dc_layers() if layers is None else layers
    tracking = item.get("tracking") if isinstance(item.get("tracking"), dict) else {}
    split_data = tracking.get("split") if isinstance(tracking.get("split"), dict) else {}
    et_data = tracking.get("et") if isinstance(tracking.get("et"), dict) else {}
    known = tracking.get("wafers_by_root") if isinstance(tracking.get("wafers_by_root"), dict) else {}
    rows: list[dict] = []
    unresolved: list[str] = []
    for lot in request_lots(item):
        root = lot["root_lot_id"]
        wafers = wafer_selection(lot.get("wafer_id")) or [wafer_key(w) for w in known.get(root) or [] if wafer_key(w)]
        if not wafers:
            unresolved.append(root)
            continue
        for wafer in wafers[:MAX_WAFERS_PER_LOT]:
            key = f"{root}|{wafer}"
            actual = split_data.get(key) if isinstance(split_data.get(key), dict) else {}
            entries = sorted((e for e in et_data.get(key) or [] if isinstance(e, dict)),
                             key=lambda e: str(e.get("time") or ""), reverse=True)
            rows.append({
                "key": key, "root_lot_id": root, "wafer_id": wafer, "lot_id": str(actual.get("lot_id") or ""),
                "values": {k: v for k, v in actual.items() if k != "lot_id"},
                "et": entries, "et_measured": bool(entries),
                "layers": list(dict.fromkeys(entry_layer(e, layers) for e in entries)),
            })
    layer_counts: dict[str, int] = {}
    for row in rows:
        for layer in row["layers"]:
            layer_counts[layer] = layer_counts.get(layer, 0) + 1
    summary = {
        "lots": len(request_lots(item)),
        "wafers": len(rows),
        "unresolved_lots": unresolved,
        "et_measured": len([r for r in rows if r["et_measured"]]),
        "layers": layer_counts,
    }
    return {
        "rows": rows, "summary": summary,
        "tracking": {key: tracking.get(key, "") for key in (
            "split_checked_at", "split_status", "split_error", "split_file",
            "et_checked_at", "et_status", "et_error", "et_cache_built_at", "et_source_root", "refreshed_by")},
    }


def report_handoff(item: dict, progress: dict | None = None) -> dict:
    """Template Report 실행 폼에 넘길 범위 — Root Lot · Wafer. 색은 넘기지 않는다
    (wafer 별 색 규칙을 넘기면 차트가 wafer 마다 갈라져 split 비교가 깨진다)."""
    progress = progress or build_progress(item)
    return {
        "template_id": str(item.get("report_template_id") or ""),
        "request_id": str(item.get("id") or ""),
        "title": str(item.get("title") or ""),
        "product": str(item.get("product") or ""),
        "root_lot_ids": list(dict.fromkeys(r["root_lot_id"] for r in progress["rows"])),
        "wafer_ids": list(dict.fromkeys(r["wafer_id"] for r in progress["rows"])),
    }


# ─────────────────────────── 조회 (ML_TABLE · ET history) ───────────────────────────

def _ci(names: list[str], *wanted: str) -> str:
    folded = {str(name).casefold(): str(name) for name in names}
    for name in wanted:
        hit = folded.get(name.casefold())
        if hit:
            return hit
    return ""


def ml_table_columns(product: str) -> list[str]:
    try:
        import polars as pl
        from core.ml_table_lookup import resolve_ml_table_file
        fp = resolve_ml_table_file(clean_product(product))
        return list(pl.read_parquet_schema(str(fp)).keys()) if fp else []
    except Exception:
        return []


def display_columns(item: dict, config: dict | None = None) -> list[str]:
    """SplitTable 형식 표에 보일 열 — 의뢰에 고른 열 → 의뢰 템플릿의 열 → 제품 ML_TABLE 의 KNOB_ 열."""
    chosen = list(item.get("split_columns") or [])
    if chosen:
        return chosen
    config = config or load_config()
    template = next((t for t in config["templates"] if t["id"] == item.get("template_id")), None)
    if template and template.get("split_columns"):
        return list(template["split_columns"])
    return [c for c in ml_table_columns(item.get("product", "")) if c.upper().startswith("KNOB_")][:DEFAULT_KNOB_LIMIT]


def read_actual_split(product: str, roots: list[str], columns: list[str]) -> dict:
    """ML_TABLE root_lot 조회 캐시에서 wafer 목록과 고른 열의 실제 값을 읽는다.

    캐시가 없거나 그 root 파티션이 아직 없으면 원본을 스캔하지 않고 missing 으로 남긴다."""
    out: dict[str, Any] = {"status": "ok", "error": "", "file": "", "rows": {},
                           "wafers_by_root": {}, "missing_roots": [], "unknown_columns": []}
    if not roots:
        return out
    try:
        import polars as pl
        from core.ml_table_lookup import resolve_ml_table_file, scan_root_lot_cache
    except Exception as exc:
        return {**out, "status": "error", "error": f"ML_TABLE 조회 모듈을 불러오지 못했습니다: {exc}"}
    fp = resolve_ml_table_file(product)
    if fp is None:
        return {**out, "status": "no_ml_table", "error": f"ML_TABLE_{product} 파일을 찾지 못했습니다"}
    out["file"] = fp.name
    for root in roots:
        try:
            lf, _status = scan_root_lot_cache(fp, root, "", allow_stale=True)
        except Exception as exc:
            out["missing_roots"].append(root)
            out["error"] = f"{root}: {exc}"
            continue
        if lf is None:
            out["missing_roots"].append(root)
            continue
        names = lf.collect_schema().names()
        root_col, lot_col, wafer_col = _ci(names, "root_lot_id"), _ci(names, "lot_id"), _ci(names, "wafer_id", "wf_id")
        if not wafer_col:
            out["error"] = "ML_TABLE 에 wafer_id 열이 없습니다"
            continue
        picked = [(col, _ci(names, col)) for col in columns]
        out["unknown_columns"].extend(col for col, hit in picked if not hit and col not in out["unknown_columns"])
        picked = [(col, hit) for col, hit in picked if hit]
        exprs = [pl.col(wafer_col).cast(pl.Utf8, strict=False).alias("__wafer")]
        if lot_col:
            exprs.append(pl.col(lot_col).cast(pl.Utf8, strict=False).alias("__lot"))
        exprs.extend(pl.col(hit).cast(pl.Utf8, strict=False).alias(f"__v{i}") for i, (_, hit) in enumerate(picked))
        frame = lf
        if root_col:
            frame = frame.filter(pl.col(root_col).cast(pl.Utf8, strict=False).str.strip_chars().str.to_uppercase() == root)
        wafers: list[str] = []
        for record in frame.select(exprs).collect().to_dicts():
            wafer = wafer_key(record.get("__wafer"))
            if not wafer:
                continue
            row = out["rows"].setdefault(f"{root}|{wafer}", {"lot_id": ""})
            if record.get("__lot") and not row["lot_id"]:
                row["lot_id"] = str(record.get("__lot") or "")
            for i, (col, _) in enumerate(picked):
                value = record.get(f"__v{i}")
                if value not in (None, "") and not row.get(col):
                    row[col] = str(value)
            if wafer not in wafers:
                wafers.append(wafer)
        wafers.sort(key=lambda w: (0, int(w)) if w.isdigit() else (1, w))
        out["wafers_by_root"][root] = wafers
    if out["missing_roots"]:
        out["status"] = "cache_pending"
        out["error"] = out["error"] or ("ML_TABLE 조회 캐시가 아직 준비되지 않은 랏: " + ", ".join(out["missing_roots"])
                                        + " — 캐시가 만들어지면 다음 갱신에서 채워집니다")
    elif out["error"]:
        out["status"] = "error"
    return out


def _et_source_root() -> str:
    try:
        from core.lot_step import source_root_for_context
        return source_root_for_context("et", "")
    except Exception:
        return ""


def read_et_history(product: str, lots: list[dict], *, source_root: str) -> dict:
    """대상 Lot 마다 제품 ET history 캐시를 읽어 wafer 별 엔트리로 나눈다(누적 전 원시 결과)."""
    out: dict[str, Any] = {"status": "ok", "error": "", "entries": {}, "built_at": "", "source_root": source_root}
    specs = [{"root_lot_id": lot["root_lot_id"], "lot_id": "", "wafer_id": lot.get("wafer_id") or "",
              "limit": ET_QUERY_LIMIT} for lot in lots if lot.get("root_lot_id")]
    if not specs:
        return out
    diag: dict = {}
    try:
        from core.et_tracker import _entry_from_package
        from core.lot_step import et_history_packages_multi
        results = et_history_packages_multi(product, specs, limit=ET_QUERY_LIMIT, source_root=source_root, diag=diag)
    except Exception as exc:
        return {**out, "status": "error", "error": f"ET history 조회 실패: {exc}"}
    if results is None:
        return {**out, "status": "no_cache",
                "error": diag.get("error") or f"{product} ET history 캐시가 아직 준비되지 않았습니다 (ET 추적과 같은 캐시)"}
    out["built_at"] = str(diag.get("history_built_at") or "")
    out["source_root"] = str(diag.get("source_root") or source_root)
    layers = _dc_layers()
    for spec, packages in zip(specs, results):
        for pkg in packages or []:
            wafer = wafer_key(pkg.get("wafer_id"))
            if not wafer:
                continue
            entry = _entry_from_package(pkg, layers)
            if entry.get("step_id"):
                out["entries"].setdefault(f"{spec['root_lot_id']}|{wafer}", []).append(entry)
    return out


def merge_et(previous: dict, fresh: dict, now: str) -> tuple[dict, int]:
    """wafer 별 누적 — 예전에 확인된 측정은 남기고 새 key 만 붙인다(ET 추적 et_history 와 같은 key)."""
    merged: dict[str, list[dict]] = {}
    added = 0
    for key in set(previous) | set(fresh):
        history = [h for h in (previous.get(key) or []) if isinstance(h, dict)]
        seen = {str(h.get("key") or "") for h in history}
        new_items = []
        for entry in fresh.get(key) or []:
            entry_key = str(entry.get("key") or "")
            if not entry_key or entry_key in seen:
                continue
            seen.add(entry_key)
            new_items.append({**entry, "detected_at": now})
        if new_items:
            added += len(new_items)
            new_items.sort(key=lambda e: str(e.get("time") or ""))
            history = (history + new_items)[-ET_HISTORY_CAP:]
        if history:
            merged[key] = history
    return merged, added


def collect_tracking(item: dict, *, actor: str) -> dict:
    """의뢰 하나의 wafer 목록 · split 값 · ET 측정을 새로 읽는다(저장은 하지 않는다)."""
    product = clean_product(item.get("product"))
    lots = request_lots(item)
    roots = [lot["root_lot_id"] for lot in lots]
    previous = item.get("tracking") if isinstance(item.get("tracking"), dict) else {}
    now = now_iso()
    split = read_actual_split(product, roots, list(item.get("split_columns") or []))
    et = read_et_history(product, lots, source_root=_et_source_root())
    tracking = dict(previous)
    tracking.update({
        "refreshed_by": actor, "split_checked_at": now, "split_status": split["status"],
        "split_error": split["error"], "split_file": split["file"],
        "split_unknown_columns": split.get("unknown_columns") or [],
    })
    if split["status"] in {"ok", "cache_pending"}:
        # 캐시가 아직 없는 랏은 지난 값을 지우지 않는다 — "준비 중" 이 "값 없음" 으로 보이면 안 된다.
        rows = dict(previous.get("split") or {}) if split["missing_roots"] else {}
        for root in split["wafers_by_root"]:
            rows = {k: v for k, v in rows.items() if not k.startswith(f"{root}|")}
        rows.update(split["rows"])
        tracking["split"] = rows
        wafers_by_root = {root: wafers for root, wafers in (previous.get("wafers_by_root") or {}).items() if root in roots}
        wafers_by_root.update(split["wafers_by_root"])
        tracking["wafers_by_root"] = wafers_by_root
    tracking.update({"et_checked_at": now, "et_status": et["status"], "et_error": et["error"],
                     "et_source_root": et["source_root"]})
    new_et = 0
    if et["status"] == "ok":
        tracking["et_cache_built_at"] = et["built_at"]
        tracking["et"], new_et = merge_et(previous.get("et") or {}, et["entries"], now)
        wafers_by_root = dict(tracking.get("wafers_by_root") or {})
        for key in tracking["et"]:
            root, _, wafer = key.partition("|")
            if root not in roots:
                continue
            known = list(wafers_by_root.get(root) or [])
            if wafer and wafer not in known:
                known.append(wafer)
                known.sort(key=lambda w: (0, int(w)) if w.isdigit() else (1, w))
                wafers_by_root[root] = known
        tracking["wafers_by_root"] = wafers_by_root
    tracking["last_new_et"] = new_et
    return tracking


def apply_tracking(request_id: str, revision: int, tracking: dict) -> dict | None:
    """revision 이 그대로일 때만 저장. 갱신 중 대상 Lot 이 바뀌었으면 None (다음 갱신이 다시 읽는다)."""
    with LOCK:
        rows = load_rows()
        item = next((row for row in rows if row.get("id") == request_id and not row.get("deleted_at")), None)
        if item is None or int(item.get("revision") or 0) != int(revision or 0):
            return None
        item["tracking"] = tracking
        save_rows(rows)
        return item


def _live(request_id: str) -> dict | None:
    with LOCK:
        return next((row for row in load_rows() if row.get("id") == request_id and not row.get("deleted_at")), None)


def refresh_request(request_id: str, *, actor: str) -> dict | None:
    item = _live(request_id)
    if item is None:
        return None
    saved = apply_tracking(request_id, int(item.get("revision") or 0), collect_tracking(item, actor=actor))
    if saved is None:
        item = _live(request_id)   # 대상 Lot 이 바뀐 경우 한 번만 새 값으로 다시 읽는다.
        if item is None:
            return None
        saved = apply_tracking(request_id, int(item.get("revision") or 0), collect_tracking(item, actor=actor))
    return saved


def scan_all(*, actor: str = "scheduler") -> dict:
    """ET 추적 스캔과 같이 도는 전체 갱신 — 완료·반려 의뢰는 건너뛴다."""
    summary = {"ok": True, "requests": 0, "saved": 0, "stale": 0, "new_et": 0, "errors": []}
    if not _SCAN_LOCK.acquire(blocking=False):
        return {**summary, "ok": False, "errors": ["analysis request scan already running"]}
    try:
        with LOCK:
            targets = [row for row in load_rows()
                       if not row.get("deleted_at") and row.get("status") not in {"completed", "rejected"}
                       and request_lots(row)]
        for item in targets:
            summary["requests"] += 1
            try:
                tracking = collect_tracking(item, actor=actor)
            except Exception as exc:
                summary["errors"].append(f"{item.get('id')}: {exc}")
                continue
            saved = apply_tracking(str(item.get("id") or ""), int(item.get("revision") or 0), tracking)
            if saved is None:
                summary["stale"] += 1
                continue
            summary["saved"] += 1
            summary["new_et"] += int(tracking.get("last_new_et") or 0)
            if tracking.get("last_new_et"):
                _notify_new_et(saved, int(tracking.get("last_new_et") or 0), actor)
    finally:
        _SCAN_LOCK.release()
    summary["ok"] = not summary["errors"]
    return summary


def _notify_new_et(item: dict, count: int, actor: str) -> None:
    try:
        from core.worker_dispatch import server_role
        if server_role() == "worker":
            return
    except Exception:
        pass
    try:
        from core.notify import emit_event
        summary = build_progress(item)["summary"]
        layers = ", ".join(f"{k} {v}" for k, v in summary["layers"].items())
        emit_event("analysis_request_et", actor=actor, target_user=str(item.get("author") or ""),
                   title=f"[분석의뢰] {item.get('title') or item.get('id')}",
                   body=f"신규 ET 측정 {count}건 · {layers or '측정 wafer ' + str(summary['et_measured'])}",
                   payload={"request_id": item.get("id")})
    except Exception as exc:
        logger.warning("analysis request notify failed: %s", exc)


# ─────────────────────────── SplitTable 형식 표 + ET 행 ───────────────────────────

def _et_cell_text(entries: list[dict]) -> str:
    pgms = list(dict.fromkeys(str(e.get("pgm") or e.get("step_seq") or "") for e in entries if e.get("pgm") or e.get("step_seq")))
    latest = max((str(e.get("time") or "") for e in entries), default="")
    date = latest[5:10].replace("-", "/") if len(latest) >= 10 else ""
    return " ".join(part for part in (", ".join(pgms[:2]) + (" …" if len(pgms) > 2 else ""), date) if part)


def _filter_st_view(st: dict, keep: list[int]) -> dict:
    """st_view 의 wafer 열을 keep 인덱스만 남기고 다시 번호를 매긴다."""
    remap = {old: new for new, old in enumerate(keep)}
    out = dict(st)
    for key in ("headers", "wafer_keys", "wafer_fab_list"):
        if isinstance(st.get(key), list):
            out[key] = [st[key][i] for i in keep if i < len(st[key])]
    rows = []
    for row in st.get("rows") or []:
        cells = row.get("_cells") if isinstance(row.get("_cells"), dict) else {}
        new_row = dict(row)
        new_row["_cells"] = {str(remap[int(k)]): v for k, v in cells.items() if str(k).isdigit() and int(k) in remap}
        new_row.pop("_merged_runs", None)
        rows.append(new_row)
    out["rows"] = rows
    return out


def split_view(item: dict, *, loader=None) -> dict:
    """랏마다 SplitTable 과 같은 st_view + 아래에 DC layer 별 ET 측정 행.

    ``loader`` 는 테스트용(기본은 인폼·Template Report 와 같은 build_splittable_embed)."""
    product = clean_product(item.get("product"))
    columns = display_columns(item)
    layers = _dc_layers()
    progress = build_progress(item, layers)
    by_key = {row["key"]: row for row in progress["rows"]}
    if loader is None:
        from app_v2.modules.informs.splittable_embed import build_splittable_embed as loader
    lots_out = []
    for lot in request_lots(item):
        root = lot["root_lot_id"]
        try:
            embed = loader(product=product, lot_id=root, custom_cols=columns)
        except Exception as exc:
            detail = getattr(exc, "detail", None) or str(exc)
            lots_out.append({"root_lot_id": root, "error": str(detail)[:300], "embed": None})
            continue
        st = dict(embed.get("st_view") or {})
        keys = [wafer_key(k) for k in (st.get("wafer_keys") or st.get("headers") or [])]
        wanted = wafer_selection(lot.get("wafer_id"))
        if wanted:
            keep = [i for i, key in enumerate(keys) if key in wanted]
            st = _filter_st_view(st, keep)
            st.pop("header_groups", None)       # 열을 걸렀으니 FAB lot 묶음 폭이 더는 맞지 않는다
            keys = [keys[i] for i in keep]
        if not st.get("lot_id_label"):
            lot_ids = sorted({row["lot_id"] for row in progress["rows"] if row["root_lot_id"] == root and row.get("lot_id")})
            st["lot_id_label"] = ", ".join(lot_ids)
        et_layers: dict[str, dict[int, list[dict]]] = {}
        for index, wafer in enumerate(keys):
            for entry in (by_key.get(f"{root}|{wafer}") or {}).get("et") or []:
                et_layers.setdefault(entry_layer(entry, layers), {}).setdefault(index, []).append(entry)
        et_rows = []
        for layer in sorted(et_layers, key=lambda name: (not re.fullmatch(r"[A-Z0-9]+DC", name), name)):
            cells = {str(i): {"actual": _et_cell_text(entries), "plan": None, "key": f"{root}|{keys[i]}|ET_{layer}"}
                     for i, entries in et_layers[layer].items()}
            et_rows.append({"_param": f"ET_{layer}", "_display": f"ET · {layer}", "_cells": cells, "_et": True})
        base_rows = [dict(row) for row in st.get("rows") or []]
        # SplitTable 표시 규약은 prefix 를 떼고 보여준다 — KNOB_5.0 PC 와 FAB_5.0 PC 가 둘 다 "5.0 PC" 가
        # 되지 않게, 같은 공정 이름이 여러 prefix 로 나오면 "KNOB · 5.0 PC" 처럼 prefix 를 붙여 둔다.
        suffix_prefixes: dict[str, set[str]] = {}
        for row in base_rows:
            prefix, _, suffix = str(row.get("_param") or "").partition("_")
            if suffix:
                suffix_prefixes.setdefault(suffix, set()).add(prefix)
        for row in base_rows:
            prefix, _, suffix = str(row.get("_param") or "").partition("_")
            if suffix and len(suffix_prefixes.get(suffix, ())) > 1:
                row["_display"] = f"{prefix} · {suffix}"
        st["rows"] = base_rows + et_rows
        measured = len({i for cells in et_layers.values() for i in cells})
        lots_out.append({
            "root_lot_id": root, "wafer_filter": lot.get("wafer_id") or "",
            "embed": {**embed, "st_view": st}, "wafers": len(keys), "et_measured": measured,
            "et_layers": sorted(et_layers),
        })
    return {"lots": lots_out, "columns": columns, "summary": progress["summary"], "tracking": progress["tracking"]}


# ─────────────────────────── 보고서 Template 맞추기 (규칙 치환 + 선택적 LLM + 검증) ───────────────────────────
# 형식 보고서는 보통 "랏만 바뀌거나, wafer slot 이 바뀌거나, split 열이 바뀌는" 변형이다.
# 그룹(wafer slot)은 의뢰 표에 적지 않으므로 대표 split 열(의뢰의 첫 표시 열)의 **실제 값**으로 나눈다.
# 원본 Template 을 만든 의뢰의 그룹과 새 의뢰의 그룹을 값 이름 → 순서로 짝지어 글의 slot·값을 바꾼다.

_ML_PREFIXES = ("KNOB_", "FAB_", "MASK_", "INLINE_", "VM_", "QTIME_", "TAG_", "MGMT_")
_ROOT_LOTS_LINE = re.compile(r"^\s*ROOT_LOTS?\s*[:=]\s*(.+)$", re.I | re.M)
_CHART_FIELD_LINE = re.compile(r"^\s*(COLOR|X|TRELLIS)\s*[:=]\s*(.+)$", re.I | re.M)
_ITEM_LITERAL = re.compile(r"`?item_id`?\s*=\s*'([^']+)'", re.I)


def compact_wafers(wafers: list[str]) -> str:
    """[1..12] → '1~12', [2,4,6] → '2,4,6', [1,2,3,7] → '1~3,7'."""
    nums = sorted({int(w) for w in wafers if str(w).strip().isdigit()})
    others = [w for w in wafers if str(w).strip() and not str(w).strip().isdigit()]
    parts: list[str] = []
    i = 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        parts.append(f"{nums[i]}~{nums[j]}" if j - i >= 2 else ",".join(str(n) for n in nums[i:j + 1]))
        i = j + 1
    return ",".join(parts + others)


def _is_ml_column(name: str) -> bool:
    return str(name or "").strip().upper().startswith(_ML_PREFIXES)


def template_references(template: dict) -> dict:
    """Template 이 가리키는 랏 · split 열 · ET 항목 · 블록 수(검증과 규칙 치환용)."""
    lots: list[str] = []
    split_columns: list[str] = []
    color_columns: list[str] = []
    items: list[str] = []
    slots = 0

    def add_lot(token: str) -> None:
        token = token.strip().strip("`'\"")
        if token and token.upper() not in {t.upper() for t in lots}:
            lots.append(token)

    for page in template.get("pages") or []:
        for slot in page.get("slots") or []:
            slots += 1
            kind = str(slot.get("kind") or "chart")
            if kind == "chart":
                code = str(slot.get("definition_code") or "")
                for match in _ROOT_LOTS_LINE.finditer(code):
                    for token in re.split(r"[,\s]+", match.group(1)):
                        add_lot(token)
                for match in _CHART_FIELD_LINE.finditer(code):
                    value = match.group(2).strip().strip("`")
                    if _is_ml_column(value):
                        if value not in split_columns:
                            split_columns.append(value)
                        if match.group(1).upper() == "COLOR" and value not in color_columns:
                            color_columns.append(value)
                for found in _ITEM_LITERAL.findall(code):
                    if found not in items:
                        items.append(found)
            elif kind == "split":
                add_lot(str(slot.get("lot") or ""))
                first = next((c.strip() for c in str(slot.get("columns") or "").split(",") if c.strip()), "")
                if first and _is_ml_column(first) and first not in split_columns:
                    split_columns.append(first)
    return {"lots": lots, "split_columns": split_columns, "color_columns": color_columns,
            "items": items, "pages": len(template.get("pages") or []), "slots": slots}


def group_facts(item: dict, progress: dict, column: str = "") -> list[dict]:
    """대표 split 열의 실제 값으로 wafer 를 묶는다 — [{group: 값, slots: {root: '1~12'}}], 첫 wafer 순."""
    column = column or next(iter(item.get("split_columns") or []), "")
    groups: dict[str, dict] = {}
    for row in progress["rows"]:
        value = str((row.get("values") or {}).get(column) or "")
        if not value:
            continue
        group = groups.setdefault(value, {"group": value, "wafers": {}})
        group["wafers"].setdefault(row["root_lot_id"], []).append(row["wafer_id"])
    return [{"group": g["group"], "slots": {root: compact_wafers(w) for root, w in g["wafers"].items()}}
            for g in groups.values()]


def source_request_for(item: dict, template_id: str) -> dict | None:
    """이 Template 을 만든 원본 의뢰 — 복제 원본이 같은 Template 을 쓰면 그것, 아니면 같은
    Template 을 연결한 가장 오래된 의뢰. 원본 그룹을 알아야 글의 slot·값을 규칙으로 바꿀 수 있다."""
    rows = [row for row in load_rows() if not row.get("deleted_at") and row.get("id") != item.get("id")]
    parent = next((row for row in rows if row.get("id") == item.get("copied_from")), None)
    if parent and str(parent.get("report_template_id") or "") == template_id:
        return parent
    linked = sorted((row for row in rows if str(row.get("report_template_id") or "") == template_id),
                    key=lambda row: str(row.get("created_at") or ""))
    return linked[0] if linked else parent


def _bracket_tag(title: str) -> str:
    match = re.search(r"\[([^\]]+)\]", str(title or ""))
    return match.group(1).strip() if match else ""


def _swap(text: str, mapping: dict[str, str]) -> str:
    """한 번에 모든 키를 바꾼다(A↔B 맞교환 가능). 키 앞뒤가 단어 문자면 바꾸지 않는다."""
    keys = [k for k in sorted(mapping, key=len, reverse=True) if k and mapping[k] != k]
    if not text or not keys:
        return text
    pattern = re.compile(r"(?<![\w])(" + "|".join(re.escape(k) for k in keys) + r")(?![\w])")
    return pattern.sub(lambda m: mapping[m.group(1)], text)


def _pair_groups(old: list[dict], new: list[dict]) -> list[tuple[dict, dict]]:
    """값 이름이 같은 그룹끼리 먼저, 나머지는 첫 wafer 순서대로 짝짓는다."""
    pairs: list[tuple[dict, dict]] = []
    rest_new = list(new)
    rest_old = []
    for group in old:
        same = next((g for g in rest_new if g["group"] == group["group"]), None)
        if same:
            pairs.append((group, same))
            rest_new.remove(same)
        else:
            rest_old.append(group)
    pairs.extend(zip(rest_old, rest_new))
    return pairs


def rule_swap_template(template: dict, item: dict, progress: dict | None = None,
                       source: dict | None = None) -> tuple[dict, list[str]]:
    """형식 보고서의 기계적인 변형(랏 · lot_id · split 열 · 그룹 slot · split 값 · 측정 매수 · 변경 번호)을
    규칙으로 바꾼다. 설명 문장(목적·배경)의 뜻은 바꾸지 못한다 — LLM 단계나 엔지니어가 다듬는다."""
    import copy as _copy

    progress = progress or build_progress(item)
    refs = template_references(template)
    new_roots = list(dict.fromkeys(r["root_lot_id"] for r in progress["rows"])) or [l["root_lot_id"] for l in request_lots(item)]
    changes: list[str] = []
    code_map: dict[str, str] = {}
    prose_map: dict[str, str] = {}

    old_roots = [lot for lot in refs["lots"] if lot.upper() not in {r.upper() for r in new_roots}]
    if old_roots and new_roots:
        if len(old_roots) == len(new_roots):
            pairs = list(zip(old_roots, new_roots))
        elif len(new_roots) == 1:
            pairs = [(old, new_roots[0]) for old in old_roots]
        else:
            pairs = [(old_roots[0], ", ".join(new_roots))] + [(old, "") for old in old_roots[1:]]
        code_map.update(dict(pairs))
        changes.append("랏: " + ", ".join(f"{o}→{n or '(삭제)'}" for o, n in pairs))

    source_progress = build_progress(source) if source else None
    if source_progress:
        old_lot_ids = {r["root_lot_id"]: r["lot_id"] for r in source_progress["rows"] if r.get("lot_id")}
        new_lot_ids = {r["root_lot_id"]: r["lot_id"] for r in progress["rows"] if r.get("lot_id")}
        for old, new in list(code_map.items()):
            if old in old_lot_ids and new in new_lot_ids and old_lot_ids[old] != new_lot_ids[new]:
                code_map[old_lot_ids[old]] = new_lot_ids[new]

    new_primary = next(iter(item.get("split_columns") or []), "")
    old_primary = (refs["color_columns"] or refs["split_columns"] or [""])[0]
    if old_primary and new_primary and old_primary != new_primary:
        old_suffix = old_primary.split("_", 1)[1] if "_" in old_primary else old_primary
        new_suffix = new_primary.split("_", 1)[1] if "_" in new_primary else new_primary
        for prefix in _ML_PREFIXES:
            a, b = prefix + old_suffix, prefix + new_suffix
            code_map[a], code_map[b] = b, a       # 맞교환 — 이전 split 열은 보조 열 자리로 간다
        changes.append(f"split 열: {old_primary}↔{new_primary} (같은 공정의 FAB_/MASK_ 열 포함)")

    if source_progress:
        source_column = next(iter(source.get("split_columns") or []), "") or old_primary
        old_groups = group_facts(source, source_progress, source_column)
        new_groups = group_facts(item, progress, new_primary)
        described = []
        for old_g, new_g in _pair_groups(old_groups, new_groups):
            old_slot = next(iter(old_g["slots"].values()), "")
            new_slot = next(iter(new_g["slots"].values()), "")
            if old_slot and new_slot and old_slot != new_slot:
                prose_map["#" + old_slot] = "#" + new_slot
            if old_g["group"] != new_g["group"]:
                prose_map[old_g["group"]] = new_g["group"]
            described.append(f"{old_g['group']} #{old_slot} → {new_g['group']} #{new_slot}")
        if described:
            changes.append("wafer slot·split 값: " + "; ".join(described))
        old_s, new_s = source_progress["summary"], progress["summary"]
        old_count = f"{old_s['et_measured']}/{old_s['wafers']}매"
        new_count = f"{new_s['et_measured']}/{new_s['wafers']}매"
        if old_count != new_count:
            prose_map[old_count] = new_count
            changes.append(f"측정 매수: {old_count}→{new_count}")
        old_tag, new_tag = _bracket_tag(source.get("title", "")), _bracket_tag(item.get("title", ""))
        if old_tag and new_tag and old_tag != new_tag:
            code_map[old_tag] = new_tag
            changes.append(f"변경 번호: {old_tag}→{new_tag}")

    out = _copy.deepcopy(template)

    def prose(value: str) -> str:
        return _swap(_swap(str(value or ""), code_map), prose_map)

    out["name"] = prose(out.get("name", ""))
    options = dict(out.get("options") or {})
    options["subtitle"] = prose(options.get("subtitle", ""))
    out["options"] = options
    for page in out.get("pages") or []:
        page["title"] = prose(page.get("title", ""))
        page["subtitle"] = prose(page.get("subtitle", ""))
        for slot in page.get("slots") or []:
            for key in ("title", "text", "chart_name", "chart_label"):
                if slot.get(key):
                    slot[key] = prose(slot[key])
            if slot.get("definition_code"):
                slot["definition_code"] = _swap(slot["definition_code"], code_map)
            if slot.get("lot"):
                slot["lot"] = _swap(slot["lot"], code_map).split(",")[0].strip()
            if slot.get("columns"):
                cols = [c.strip() for c in _swap(slot["columns"], code_map).split(",") if c.strip()]
                slot["columns"] = ",".join(dict.fromkeys(cols))
    return out, changes


def report_instruction(item: dict, template: dict, progress: dict, note: str, rule_changes: list[str]) -> str:
    """규칙 치환 뒤 LLM 에 주는 지시문(≤3000자) — 값은 그대로 두고 설명 문장만 새 의뢰에 맞춘다."""
    details = re.sub(r"<[^>]+>", " ", str(item.get("details") or ""))
    details = re.sub(r"\s+", " ", details).strip()[:500]
    lines = [
        "현재 Template 은 새 분석의뢰에 맞게 랏·wafer slot·split 값이 이미 규칙으로 바뀌어 있습니다.",
        "text 블록·페이지 제목·부제·Template 이름의 설명 문장(평가 목적·배경·변경 내용)이 아래 새 의뢰와 맞지 않으면 그 문장만 고쳐 주세요.",
        "",
        "[새 분석의뢰]",
        f"- 제목: {item.get('title', '')}",
        f"- 대상 Lot: {lots_text(request_lots(item))}",
        f"- 대표 split 열: {next(iter(item.get('split_columns') or []), '-')}",
    ]
    for group in group_facts(item, progress):
        lines.append("  · " + group["group"] + ": " + " / ".join(f"{r} #{s}" for r, s in group["slots"].items()))
    if details:
        lines.append(f"- 의뢰 내용: {details}")
    lines += ["", "[이미 반영한 규칙 치환 — 되돌리지 말 것]"] + [f"- {c}" for c in rule_changes]
    lines += [
        "", "[지킬 것]",
        "- 랏·lot_id·wafer slot(#…)·split 열·split 값·측정 매수는 그대로 둘 것",
        "- chart definition_code·split 블록·stats 블록·레이아웃·item_id 는 바꾸지 말 것, 블록을 지우거나 새로 만들지 말 것",
        "- 평균·Δ 같은 숫자를 지어내지 말 것",
    ]
    if note.strip():
        lines += ["", "[추가 요청]", note.strip()[:600]]
    return "\n".join(lines)[:3000]


def verify_report_template(original: dict, candidate: dict, item: dict, progress: dict | None = None) -> dict:
    """고친 Template 을 원본·의뢰와 대조한다. error 가 하나라도 있으면 저장하지 않는 것이 원칙."""
    import json

    progress = progress or build_progress(item)
    before, after = template_references(original), template_references(candidate)
    roots = list(dict.fromkeys(r["root_lot_id"] for r in progress["rows"])) or [l["root_lot_id"] for l in request_lots(item)]
    root_keys = {r.upper() for r in roots}
    split_columns = list(item.get("split_columns") or [])
    blob = json.dumps(candidate, ensure_ascii=False)
    checks: list[dict] = []

    def add(level: str, label: str, detail: str = "") -> None:
        checks.append({"level": level, "label": label, "detail": detail})

    stale = [lot for lot in before["lots"] if lot.upper() not in root_keys and re.search(rf"(?<![\w]){re.escape(lot)}(?![\w])", blob)]
    add("error" if stale else "ok", "이전 랏 잔존", ", ".join(stale) if stale else "없음")
    missing = [r for r in roots if r not in blob]
    add("error" if missing else "ok", "새 랏 반영", ", ".join(missing) + " 누락" if missing else ", ".join(roots))
    foreign = [lot for lot in after["lots"] if lot.upper() not in root_keys]
    add("error" if foreign else "ok", "차트·Split 블록 랏", ", ".join(foreign) + " 가 의뢰에 없음" if foreign else "의뢰 랏만 사용")
    if split_columns:
        bad = [c for c in after["color_columns"] if c not in split_columns]
        add("error" if bad else "ok", "차트 색 기준 split 열",
            ", ".join(bad) + " 가 의뢰 표시 열에 없음" if bad else (", ".join(after["color_columns"]) or "색 기준 열 없음"))
    add("ok" if sorted(before["items"]) == sorted(after["items"]) else "error", "ET 항목 유지",
        ", ".join(after["items"]) if sorted(before["items"]) == sorted(after["items"])
        else f"{', '.join(before['items'])} → {', '.join(after['items'])}")
    same_shape = (before["pages"], before["slots"]) == (after["pages"], after["slots"])
    add("ok" if same_shape else "error", "페이지·블록 수 유지",
        f"{after['pages']}p · {after['slots']}블록" if same_shape
        else f"{before['pages']}p/{before['slots']}블록 → {after['pages']}p/{after['slots']}블록")
    texts = " ".join(str(slot.get("text") or "") for page in candidate.get("pages") or [] for slot in page.get("slots") or [])
    original_texts = " ".join(str(slot.get("text") or "") for page in original.get("pages") or [] for slot in page.get("slots") or [])
    # 숫자 검증 — 형식 보고서에서 가장 위험한 건 그럴듯한 숫자 오류다(측정 매수 25/25 ↔ 실제 24/25,
    # 21pt → 25pt 처럼 지어낸 값). 측정 매수는 실제 값과, 나머지 단위 숫자는 원본·의뢰 사실과 대조한다.
    summary = progress["summary"]
    expected_count = f"{summary['et_measured']}/{summary['wafers']}매"
    counts = sorted(set(re.findall(r"\d+/\d+매", texts.replace(" ", ""))))
    wrong_counts = [c for c in counts if c != expected_count]
    if counts:
        add("error" if wrong_counts else "ok", "글의 측정 매수",
            f"{', '.join(wrong_counts)} — 실제 {expected_count}" if wrong_counts else expected_count)
    unit = r"(?<![A-Za-z0-9.])(?:\d+(?:\.\d+)?(?:pt|mV|V|%|배|nm|uA|mA|A)(?![A-Za-z0-9])|\d+매)"
    allowed = set(re.findall(unit, original_texts.replace(" ", ""))) | {f"{summary['wafers']}매", f"{summary['et_measured']}매"}
    invented = sorted(set(re.findall(unit, re.sub(r"\d+/\d+매", "", texts.replace(" ", "")))) - allowed)
    if texts.strip():
        add("warn" if invented else "ok", "글의 숫자(단위)",
            f"원본·의뢰에 없는 값: {', '.join(invented)}" if invented else "원본·의뢰 값만 사용")
    groups = group_facts(item, progress)
    if texts.strip() and groups:
        missing_slots = [f"{g['group']} {root} #{s}" for g in groups for root, s in g["slots"].items() if f"#{s}" not in texts]
        add("warn" if missing_slots else "ok", "글에 적힌 wafer slot",
            ("글에서 못 찾음: " + "; ".join(missing_slots)) if missing_slots else "split 값별 slot 모두 확인")
    return {"ok": not any(c["level"] == "error" for c in checks), "checks": checks, "before": before, "after": after}
