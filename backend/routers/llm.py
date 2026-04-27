"""routers/llm.py v8.7.7 — 선택적 사내 LLM 어댑터 노출.

- GET  /api/llm/status     is_available + redacted config (모든 유저 조회 가능 — UI 가시성용)
- POST /api/llm/test       admin 전용.  prompt 1건 실행해 연결 확인.
- POST /api/llm/flowi/chat 홈 Flowi 토큰 활성화 + fab 데이터 질의

caller 주의: LLM 은 옵션. UI 는 status.available == false 면 관련 버튼을 숨겨야 함.
설정 편집은 /api/admin/settings/save 에서 llm 블록으로 수행.
"""
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
import polars as pl

from core.paths import PATHS
from core.utils import _STR
from core.auth import current_user, require_admin
from core import llm_adapter


router = APIRouter(prefix="/api/llm", tags=["llm"])
logger = logging.getLogger("flow.llm.router")
FLOWI_FEEDBACK_FILE = PATHS.data_root / "flowi_feedback.jsonl"
FLOWI_READ_ONLY_POLICY = {
    "read_only": True,
    "applies_to": ["user", "admin"],
    "blocked_targets": ["raw data DB", "Files", "DB root files", "product reformatter files"],
}

_WRITE_TERMS = (
    "수정", "변경", "바꿔", "바꾸", "저장", "삭제", "지워", "업로드", "올려",
    "덮어", "추가", "생성", "편집", "업데이트", "이동", "rename", "delete",
    "update", "insert", "drop", "write", "save", "modify", "edit", "upload",
    "create", "remove", "overwrite", "replace", "move",
)
_WRITE_TARGET_TERMS = (
    "db", "database", "data root", "raw data", "source file", "files", "file",
    "csv", "parquet", "json", "reformatter", "원 data", "원데이터", "원본",
    "데이터", "파일", "루트", "소스", "제품별 reformatter",
)

_STOP_TOKENS = {
    "A", "AN", "THE", "ET", "WF", "WAFER", "WAFERS", "BY", "PER", "ITEM", "LOT", "LOTS",
    "KNOB", "KNOBS", "MEDIAN", "MEAN", "AVG", "AVERAGE", "VALUE", "VALUES", "FLOWI",
    "값", "중앙값", "평균", "별로", "별", "랏", "로트", "노브", "아이템", "어떤",
    "어떻게", "몇이야", "처리", "데이터", "조회", "보여줘",
}


def _text(raw: Any) -> str:
    return str(raw or "").strip()


def _upper(raw: Any) -> str:
    return _text(raw).upper()


def _flowi_write_block_message(prompt: str) -> str:
    text = str(prompt or "")
    low = text.lower()
    has_write = any(term in low or term in text for term in _WRITE_TERMS)
    has_target = any(term in low or term in text for term in _WRITE_TARGET_TERMS)
    if not (has_write and has_target):
        return ""
    return (
        "Flowi LLM은 사용자와 admin 모두 원 data DB 또는 Files를 수정할 수 없습니다. "
        "LLM은 조회/요약/표시만 수행하며, DB/Files 변경은 전용 화면에서 직접 처리해야 합니다."
    )


def _tokens(prompt: str) -> list[str]:
    return [m.group(0).upper() for m in re.finditer(r"[A-Za-z][A-Za-z0-9_.-]*|\d+(?:\.\d+)?", prompt or "")]


def _product_aliases(product: str) -> set[str]:
    raw = _upper(product)
    if not raw:
        return set()
    out = {raw}
    if raw.startswith("ML_TABLE_"):
        raw = raw[len("ML_TABLE_"):]
        if raw:
            out.add(raw)
    if raw.startswith("PRODUCT_A0") or raw == "PRODA0":
        out.update({"PRODA", "PRODA0", "PRODUCT_A0", "ML_TABLE_PRODA", "ML_TABLE_PRODA0"})
    elif raw.startswith("PRODUCT_A1") or raw == "PRODA1":
        out.update({"PRODA", "PRODA1", "PRODUCT_A1", "ML_TABLE_PRODA", "ML_TABLE_PRODA1"})
    elif raw.startswith("PRODUCT_A") or raw == "PRODA":
        out.update({"PRODA", "PRODA0", "PRODA1", "PRODUCT_A", "PRODUCT_A0", "PRODUCT_A1", "ML_TABLE_PRODA"})
    elif raw.startswith("PRODUCT_B") or raw == "PRODB":
        out.update({"PRODB", "PRODUCT_B", "ML_TABLE_PRODB"})
    return {v for v in out if v}


def _product_hint(prompt: str, explicit: str = "") -> str:
    if explicit:
        return explicit
    for tok in _tokens(prompt):
        if tok.startswith(("ML_TABLE_", "PRODUCT_", "PROD")):
            return tok
    return ""


def _lot_tokens(prompt: str) -> list[str]:
    out = []
    for tok in _tokens(prompt):
        if re.fullmatch(r"[A-Z]\d{4,}(?:[A-Z])?(?:\.\d+)?", tok):
            out.append(tok)
    return out


def _step_tokens(prompt: str) -> list[str]:
    out = []
    for tok in _tokens(prompt):
        if re.fullmatch(r"[A-Z]{1,5}\d{4,}", tok):
            out.append(tok)
    return out


def _query_tokens(prompt: str) -> list[str]:
    out = []
    for tok in _tokens(prompt):
        if tok in _STOP_TOKENS:
            continue
        if tok.startswith(("PROD", "ML_TABLE_", "PRODUCT_")):
            continue
        if re.fullmatch(r"\d+(?:\.\d+)?", tok):
            continue
        out.append(tok)
    return out


def _matches_any(value: str, needles: set[str]) -> bool:
    val = _upper(value)
    return any(n and (val == n or n in val) for n in needles)


def _filter_files_by_product(files: list[Path], product: str) -> list[Path]:
    aliases = _product_aliases(product)
    if not aliases:
        return files
    out = []
    for fp in files:
        parts = {_upper(fp.stem), _upper(fp.parent.name)}
        if any(_matches_any(p, aliases) or _matches_any(p.replace("ML_TABLE_", ""), aliases) for p in parts):
            out.append(fp)
    return out


def _scan_parquet(files: list[Path]) -> pl.LazyFrame:
    if not files:
        raise HTTPException(404, "읽을 parquet 파일이 없습니다")
    return pl.scan_parquet([str(p) for p in files])


def _schema_names(lf: pl.LazyFrame) -> list[str]:
    try:
        return list(lf.collect_schema().names())
    except Exception:
        return list(lf.schema.keys())


def _ci_col(cols: list[str], *candidates: str) -> str:
    by_lower = {c.lower(): c for c in cols}
    for cand in candidates:
        hit = by_lower.get(str(cand).lower())
        if hit:
            return hit
    return ""


def _db_root_candidates(kind: str) -> list[Path]:
    base = PATHS.db_root
    kind_u = kind.upper()
    if not base.exists():
        return []
    roots = []
    if base.is_dir() and kind_u in base.name.upper():
        roots.append(base)
    try:
        for child in sorted(base.iterdir()):
            if child.is_dir() and kind_u in child.name.upper():
                roots.append(child)
    except Exception:
        pass
    return roots


def _et_files(product: str) -> list[Path]:
    files: list[Path] = []
    for root in _db_root_candidates("ET"):
        files.extend(sorted(root.rglob("*.parquet")))
    return _filter_files_by_product(files, product)


def _ml_files(product: str) -> list[Path]:
    roots = []
    for root in (PATHS.base_root, PATHS.db_root):
        try:
            if root.exists() and root not in roots:
                roots.append(root)
        except Exception:
            pass
    files: list[Path] = []
    for root in roots:
        try:
            files.extend(sorted(root.glob("ML_TABLE_*.parquet")))
        except Exception:
            pass
    dedup = []
    seen = set()
    for fp in files:
        key = str(fp.resolve()) if fp.exists() else str(fp)
        if key in seen:
            continue
        seen.add(key)
        dedup.append(fp)
    return _filter_files_by_product(dedup, product)


def _unique_strings(lf: pl.LazyFrame, col: str, limit: int = 200) -> list[str]:
    if not col:
        return []
    try:
        vals = (
            lf.select(pl.col(col).cast(_STR, strict=False).drop_nulls().unique().alias(col))
            .limit(limit)
            .collect()[col]
            .to_list()
        )
    except Exception:
        return []
    return [_text(v) for v in vals if _text(v)]


def _match_values(values: list[str], needles: list[str]) -> list[str]:
    clean = [_upper(n) for n in needles if _upper(n) and _upper(n) not in _STOP_TOKENS]
    if not clean:
        return []
    exact = [v for v in values if _upper(v) in clean]
    if exact:
        return sorted(set(exact))
    contains = [v for v in values if any(n in _upper(v) for n in clean)]
    return sorted(set(contains))


def _sort_wafer_rows(rows: list[dict]) -> list[dict]:
    def key(row):
        raw = _text(row.get("wafer_id") or row.get("WAFER_ID"))
        m = re.search(r"\d+", raw)
        return (int(m.group(0)) if m else 9999, raw)
    return sorted(rows, key=key)


def _round4(value: Any) -> Any:
    if value is None:
        return None
    try:
        return round(float(value), 4)
    except Exception:
        return value


def _or_contains(cols: list[str], needles: list[str]) -> Any:
    expr = None
    for col in cols:
        for tok in needles:
            piece = pl.col(col).cast(_STR, strict=False).str.contains(tok, literal=True)
            expr = piece if expr is None else (expr | piece)
    return expr


def _handle_et_query(prompt: str, product: str, max_rows: int) -> dict:
    if "ET" not in _upper(prompt):
        return {"handled": False}
    files = _et_files(product)
    if not files:
        return {
            "handled": True,
            "intent": "et_wafer_median",
            "answer": "ET 원천 parquet을 찾지 못했습니다. DB root 아래 `*ET*` 폴더를 확인해주세요.",
            "rows": [],
        }
    lf = _scan_parquet(files)
    cols = _schema_names(lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    step_col = _ci_col(cols, "step_id", "STEP_ID")
    item_col = _ci_col(cols, "item_id", "ITEM_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID")
    value_col = _ci_col(cols, "value", "VALUE")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID", "fab_lot_id", "FAB_LOT_ID")
    if not (step_col and item_col and wafer_col and value_col):
        return {
            "handled": True,
            "intent": "et_wafer_median",
            "answer": "ET 데이터 컬럼(step_id/item_id/wafer_id/value)을 찾지 못했습니다.",
            "rows": [],
        }

    step_vals = _unique_strings(lf, step_col)
    item_vals = _unique_strings(lf, item_col)
    step_matches = _match_values(step_vals, _step_tokens(prompt))
    item_matches = _match_values(item_vals, _query_tokens(prompt))
    lot_matches = _lot_tokens(prompt)
    aliases = _product_aliases(product)

    filters = []
    if aliases and product_col:
        filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    if step_matches:
        filters.append(pl.col(step_col).cast(_STR, strict=False).is_in(step_matches))
    if item_matches:
        filters.append(pl.col(item_col).cast(_STR, strict=False).is_in(item_matches))
    if lot_matches:
        lot_cols = [c for c in (root_col, lot_col, _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")) if c]
        lot_expr = _or_contains(lot_cols, lot_matches)
        if lot_expr is not None:
            filters.append(lot_expr)
    for expr in filters:
        lf = lf.filter(expr)

    group_cols = [c for c in (product_col, step_col, item_col, wafer_col) if c]
    try:
        out = (
            lf.group_by(group_cols)
            .agg([
                pl.col(value_col).cast(pl.Float64, strict=False).median().alias("median"),
                pl.col(value_col).cast(pl.Float64, strict=False).mean().alias("mean"),
                pl.col(value_col).cast(pl.Float64, strict=False).count().alias("count"),
            ])
            .sort(group_cols)
            .limit(max(1, min(120, max_rows * 6)))
            .collect()
        )
    except Exception as e:
        logger.warning("flowi ET query failed: %s", e)
        raise HTTPException(400, f"ET 집계 실패: {e}")

    rows = out.rename({
        product_col: "product",
        step_col: "step_id",
        item_col: "item_id",
        wafer_col: "wafer_id",
    }).to_dicts() if out.height else []
    rows = _sort_wafer_rows(rows)
    for row in rows:
        row["median"] = _round4(row.get("median"))
        row["mean"] = _round4(row.get("mean"))
        row["count"] = int(row.get("count") or 0)

    if not rows:
        hints = []
        if _step_tokens(prompt) and not step_matches:
            hints.append(f"step 후보: {', '.join(step_vals[:8])}")
        if _query_tokens(prompt) and not item_matches:
            hints.append(f"item 후보: {', '.join(item_vals[:8])}")
        hint_txt = " / ".join(hints) if hints else "필터 조건에 맞는 ET row가 없습니다."
        return {
            "handled": True,
            "intent": "et_wafer_median",
            "answer": hint_txt,
            "rows": [],
            "filters": {"step": step_matches, "item": item_matches, "lot": lot_matches, "product": sorted(aliases)},
        }

    preview = rows[:max_rows]
    lines = [
        f"- WF {r.get('wafer_id')}: median {r.get('median')} (mean {r.get('mean')}, n={r.get('count')})"
        for r in preview
    ]
    scope = []
    if step_matches:
        scope.append("step=" + ",".join(step_matches))
    if item_matches:
        scope.append("item=" + ",".join(item_matches))
    if lot_matches:
        scope.append("lot~" + ",".join(lot_matches))
    if aliases:
        scope.append("product=" + ",".join(sorted(aliases)[:4]))
    answer = "ET value wafer별 median입니다"
    if scope:
        answer += " (" + " / ".join(scope) + ")"
    answer += f". 총 {len(rows)}개 그룹 중 상위 {len(preview)}개를 표시합니다.\n" + "\n".join(lines)
    return {
        "handled": True,
        "intent": "et_wafer_median",
        "answer": answer,
        "rows": rows,
        "filters": {"step": step_matches, "item": item_matches, "lot": lot_matches, "product": sorted(aliases)},
    }


def _handle_knob_query(prompt: str, product: str, max_rows: int) -> dict:
    up = _upper(prompt)
    if "KNOB" not in up and "노브" not in prompt:
        return {"handled": False}
    lot_matches = _lot_tokens(prompt)
    files = _ml_files(product)
    if not files:
        return {
            "handled": True,
            "intent": "lot_knobs",
            "answer": "ML_TABLE parquet을 찾지 못했습니다. DB root의 `ML_TABLE_*.parquet` 파일을 확인해주세요.",
            "knobs": [],
        }
    lf = _scan_parquet(files)
    cols = _schema_names(lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID")
    knob_cols = [c for c in cols if _upper(c).startswith("KNOB_")]
    if not knob_cols:
        return {"handled": True, "intent": "lot_knobs", "answer": "ML_TABLE에서 KNOB_* 컬럼을 찾지 못했습니다.", "knobs": []}

    aliases = _product_aliases(product)
    filters = []
    if aliases and product_col:
        filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    if lot_matches:
        lot_cols = [c for c in (root_col, lot_col) if c]
        lot_expr = _or_contains(lot_cols, lot_matches)
        if lot_expr is not None:
            filters.append(lot_expr)
    for expr in filters:
        lf = lf.filter(expr)

    if not lot_matches:
        try:
            sample_cols = [c for c in (product_col, root_col, lot_col) if c]
            sample = lf.select(sample_cols).unique().limit(8).collect().to_dicts()
        except Exception:
            sample = []
        lots = ", ".join(sorted({_text(r.get(root_col) or r.get(lot_col)) for r in sample if _text(r.get(root_col) or r.get(lot_col))})[:8])
        suffix = f" 예: {lots}" if lots else ""
        return {
            "handled": True,
            "intent": "lot_knobs",
            "answer": "KNOB 조회는 lot/root lot 조건이 필요합니다." + suffix,
            "knobs": [],
            "lot_candidates": sample,
        }

    keep = [c for c in (product_col, root_col, lot_col, wafer_col) if c] + knob_cols
    try:
        df = lf.select(keep).collect()
    except Exception as e:
        logger.warning("flowi knob query failed: %s", e)
        raise HTTPException(400, f"KNOB 조회 실패: {e}")
    if df.height == 0:
        return {
            "handled": True,
            "intent": "lot_knobs",
            "answer": f"{', '.join(lot_matches)} 조건에 맞는 ML_TABLE row가 없습니다.",
            "knobs": [],
        }

    q_tokens = set(_query_tokens(prompt)) - set(lot_matches)
    selected_knobs = []
    for col in knob_cols:
        body = _upper(col.replace("KNOB_", ""))
        if not q_tokens or any(tok in body for tok in q_tokens):
            selected_knobs.append(col)
    if not selected_knobs:
        selected_knobs = knob_cols

    table = None
    detail_requested = bool(q_tokens) or any(w in prompt for w in ("다", "전체", "테이블", "표", "보여"))
    if detail_requested and selected_knobs:
        table_knobs = selected_knobs[:8]
        table_cols = [c for c in (product_col, root_col, lot_col, wafer_col) if c] + table_knobs
        rename = {}
        if product_col:
            rename[product_col] = "product"
        if root_col:
            rename[root_col] = "root_lot_id"
        if lot_col:
            rename[lot_col] = "lot_id"
        if wafer_col:
            rename[wafer_col] = "wafer_id"
        for col in table_knobs:
            rename[col] = col.replace("KNOB_", "", 1)
        try:
            tdf = df.select(table_cols).rename(rename)
            table_rows = _sort_wafer_rows(tdf.to_dicts())[:80]
        except Exception:
            table_rows = []
        table_columns = []
        for key, label in [
            ("product", "PRODUCT"),
            ("root_lot_id", "ROOT_LOT_ID"),
            ("lot_id", "LOT_ID"),
            ("wafer_id", "WAFER_ID"),
        ]:
            if key in rename.values():
                table_columns.append({"key": key, "label": label})
        table_columns.extend({"key": col.replace("KNOB_", "", 1), "label": col.replace("KNOB_", "", 1)} for col in table_knobs)
        table = {
            "kind": "splittable_preview",
            "title": f"{', '.join(lot_matches)} KNOB table",
            "placement": "below",
            "columns": table_columns,
            "rows": table_rows,
            "total": int(df.height),
        }

    summaries = []
    for col in selected_knobs[: max(1, min(40, max_rows * 3))]:
        vc = (
            df.select(pl.col(col).cast(_STR, strict=False).alias("value"))
            .drop_nulls()
            .group_by("value")
            .len()
            .sort("len", descending=True)
        )
        values = vc.to_dicts()
        wafer_by_value = {}
        for rec in values[:5]:
            val = rec.get("value")
            try:
                wafers = (
                    df.filter(pl.col(col).cast(_STR, strict=False) == val)
                    .select(pl.col(wafer_col).cast(_STR, strict=False).alias("wafer_id"))
                    .unique()
                    .sort("wafer_id")
                    .limit(30)
                    .to_series()
                    .to_list()
                ) if wafer_col else []
            except Exception:
                wafers = []
            wafer_by_value[_text(val)] = wafers
        summaries.append({
            "knob": col,
            "display_name": col.replace("KNOB_", "", 1),
            "split": len(values) > 1,
            "values": [{"value": r.get("value"), "count": int(r.get("len") or 0), "wafers": wafer_by_value.get(_text(r.get("value")), [])} for r in values[:5]],
        })

    lot_label = ", ".join(lot_matches)
    preview = summaries[:max_rows]
    lines = []
    for item in preview:
        val_txt = "; ".join(
            f"{v.get('value')}({v.get('count')}wf" + (f": {','.join(v.get('wafers')[:8])}" if item.get("split") else "") + ")"
            for v in item.get("values", [])[:3]
        )
        lines.append(f"- {item.get('display_name')}: {val_txt}")
    answer = f"{lot_label} KNOB 요약입니다. {df.height} wafer row 기준, {len(summaries)}개 KNOB 중 {len(preview)}개를 표시합니다.\n" + "\n".join(lines)
    return {
        "handled": True,
        "intent": "lot_knobs",
        "answer": answer,
        "knobs": summaries,
        "table": table,
        "filters": {"lot": lot_matches, "product": sorted(aliases)},
    }


def _handle_flowi_query(prompt: str, product: str = "", max_rows: int = 12) -> dict:
    product = _product_hint(prompt, product)
    for handler in (_handle_et_query, _handle_knob_query):
        out = handler(prompt, product, max_rows)
        if out.get("handled"):
            return out
    return {
        "handled": False,
        "intent": "general",
        "answer": (
            "Flowi local tools는 현재 ET wafer별 median 조회와 lot KNOB 요약을 우선 지원합니다.\n"
            "예: `ET ETA100010 VTH median wf별`, `A1000 knob 어떻게돼`"
        ),
    }


@router.get("/status")
def status(request: Request):
    _ = current_user(request)
    cfg = llm_adapter.get_config(redact=True)
    return {
        "available": llm_adapter.is_available(),
        "config": cfg,
        "flowi": {
            "requires_token": True,
            "local_tools": ["et_wafer_median", "lot_knobs"],
            "policy": FLOWI_READ_ONLY_POLICY,
        },
    }


class LLMTestReq(BaseModel):
    prompt: str
    system: str | None = None


@router.post("/test")
def test(req: LLMTestReq, _admin=Depends(require_admin)):
    if not llm_adapter.is_available():
        raise HTTPException(400, "LLM 이 설정되어 있지 않거나 비활성화됨")
    out = llm_adapter.complete((req.prompt or "").strip(), system=req.system)
    return out


class FlowiChatReq(BaseModel):
    prompt: str
    token: str = ""
    product: str = ""
    max_rows: int = 12


class FlowiVerifyReq(BaseModel):
    token: str = ""


class FlowiFeedbackReq(BaseModel):
    rating: str = ""
    prompt: str = ""
    answer: str = ""
    intent: str = ""
    note: str = ""


@router.post("/flowi/verify")
def flowi_verify(req: FlowiVerifyReq, request: Request):
    _ = current_user(request)
    token = (req.token or "").strip()
    if not token:
        raise HTTPException(400, "Flowi 활성화 토큰을 입력해주세요")
    if not llm_adapter.is_available():
        return {"ok": False, "message": "LLM 설정이 비활성화되어 있습니다.", "error": "llm unavailable"}
    out = llm_adapter.complete(
        "연결 확인입니다. 정상 수신했다면 확인완료 라고만 답하세요.",
        system="Flowi 연결 확인 응답은 반드시 확인완료 한 단어로만 작성합니다.",
        timeout=8,
        auth_token=token,
    )
    if out.get("ok"):
        return {"ok": True, "message": "확인완료"}
    return {"ok": False, "message": "LLM 연결 확인 실패", "error": out.get("error") or "unknown"}


@router.post("/flowi/feedback")
def flowi_feedback(req: FlowiFeedbackReq, request: Request):
    me = current_user(request)
    rating = (req.rating or "").strip().lower()
    if rating not in {"up", "down", "neutral"}:
        raise HTTPException(400, "rating must be up/down/neutral")
    rec = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "username": me.get("username") or "",
        "rating": rating,
        "intent": (req.intent or "").strip()[:80],
        "prompt_excerpt": (req.prompt or "").strip()[:500],
        "answer_excerpt": (req.answer or "").strip()[:800],
        "note": (req.note or "").strip()[:1000],
    }
    try:
        FLOWI_FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
        with FLOWI_FEEDBACK_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("flowi feedback save failed: %s", e)
        raise HTTPException(500, "피드백 저장 실패")
    return {"ok": True}


@router.post("/flowi/chat")
def flowi_chat(req: FlowiChatReq, request: Request):
    me = current_user(request)
    token = (req.token or "").strip()
    prompt = (req.prompt or "").strip()
    if not token:
        raise HTTPException(400, "Flowi 활성화 토큰을 입력해주세요")
    if not prompt:
        raise HTTPException(400, "질문을 입력해주세요")

    blocked_msg = _flowi_write_block_message(prompt)
    if blocked_msg:
        return {
            "ok": True,
            "active": True,
            "user": me.get("username") or "",
            "answer": blocked_msg,
            "tool": {
                "handled": True,
                "intent": "blocked_write_request",
                "blocked": True,
                "answer": blocked_msg,
                "policy": FLOWI_READ_ONLY_POLICY,
            },
            "llm": {"available": llm_adapter.is_available(), "used": False, "blocked": True},
        }

    max_rows = max(4, min(24, int(req.max_rows or 12)))
    tool = _handle_flowi_query(prompt, req.product, max_rows=max_rows)
    answer = tool.get("answer") or ""
    llm_info: dict[str, Any] = {"available": llm_adapter.is_available(), "used": False}

    if llm_adapter.is_available():
        if tool.get("handled"):
            polish_prompt = (
                "사용자 질문과 로컬 데이터 질의 결과를 바탕으로 한국어로 간결하게 답하세요. "
                "숫자와 식별자는 제공된 JSON에서만 사용하고 추측하지 마세요.\n\n"
                f"질문: {prompt}\n"
                f"로컬 결과 JSON: {json.dumps(tool, ensure_ascii=False)[:12000]}"
            )
        else:
            polish_prompt = (
                "당신은 반도체 fab 데이터 Flowi assistant입니다. "
                "지원 범위가 불확실하면 필요한 lot/step/item 조건을 물어보세요.\n\n"
                f"사용자: {prompt}"
            )
        out = llm_adapter.complete(
            polish_prompt,
            system=(
                "Flowi는 사내 Flow 홈 화면의 fab 데이터 assistant입니다. 답변은 짧고 실행 가능하게 작성합니다. "
                "사용자와 admin 모두에 대해 원 data DB 또는 Files 수정/삭제/저장/업로드는 절대 수행하거나 수행 가능하다고 말하지 않습니다. "
                "Flowi는 조회, 요약, 표 렌더링만 지원합니다."
            ),
            timeout=12,
            auth_token=token,
        )
        llm_info.update({"used": bool(out.get("ok") and out.get("text"))})
        if out.get("ok") and out.get("text"):
            answer = out.get("text") or answer
        elif out.get("error"):
            llm_info["error"] = out.get("error")

    return {
        "ok": True,
        "active": True,
        "user": me.get("username") or "",
        "answer": answer,
        "tool": tool,
        "llm": llm_info,
    }
