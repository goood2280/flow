"""SplitTable 열기 Unit AI — "ABB11 스플릿테이블 보여줘" 류 네비게이션 처리.

prompt 에서 root lot 토큰을 찾아 ML_TABLE 에서 product 를 확인(가능하면)하고,
SplitTable 페이지를 해당 root lot 으로 여는 navigate 액션을 반환한다. LLM 불필요.
프런트(My_Home.jsx)가 tool.navigate 를 받아 flow:navigate 이벤트로 탭을 연다.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from core.flowi_units.base import BaseUnitAI, CodeRef, DataSourceRef

_SPLITTABLE_RE = re.compile(r"(스플릿\s*테이블|split\s*-?\s*table|splittable)", re.IGNORECASE)
_ACTION_RE = re.compile(r"(보여|열어|열기|띄워|이동|open|show|view)", re.IGNORECASE)
# root lot 후보 토큰 (영숫자, 2~20자, 숫자 포함) — 키워드/일반 단어는 제외.
_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9_\-]{1,19}(?![A-Za-z0-9_])")
_STOPWORDS = {
    "SPLIT", "TABLE", "SPLITTABLE", "SHOW", "OPEN", "VIEW", "LOT", "ROOT",
    "ML", "ML_TABLE", "THE", "FOR",
}


def _ml_table_products() -> list[tuple[str, Path]]:
    """data/Fab 의 ML_TABLE_{PRODUCT}.parquet 목록 → [(product, path)]."""
    from core.paths import PATHS

    out: list[tuple[str, Path]] = []
    try:
        for fp in sorted(Path(PATHS.db_root).glob("ML_TABLE_*.parquet")):
            product = fp.stem[len("ML_TABLE_"):]
            if product:
                out.append((product, fp))
    except Exception:
        pass
    return out


def _find_products_for_root(root: str, tables: list[tuple[str, Path]]) -> list[str]:
    """root lot 이 실제로 존재하는 product 목록 (ML_TABLE lazy scan, 실패 시 빈 목록)."""
    hits: list[str] = []
    try:
        import polars as pl
    except Exception:
        return hits
    target = str(root or "").strip().upper()
    for product, fp in tables:
        try:
            n = (pl.scan_parquet(fp)
                   .filter(pl.col("ROOT_LOT_ID").cast(pl.Utf8).str.to_uppercase() == target)
                   .select(pl.len()).collect().item())
            if n:
                hits.append(product)
        except Exception:
            continue
    return hits


class SplitNavUnitAI(BaseUnitAI):
    KEY = "split_nav"
    TITLE = "SplitTable 열기"
    DESCRIPTION = (
        "prompt 의 root lot 토큰으로 SplitTable 페이지를 연다 (ML_TABLE 에서 product 자동 확인). "
        "예: 'ABB11 스플릿테이블 보여줘', 'A1006 splittable 열어줘'."
    )
    LLM_PROFILE = "deterministic navigation; no LLM"
    DATA_SOURCES = (
        DataSourceRef(
            kind="parquet",
            path="FLOW_DB_ROOT/ML_TABLE_{PRODUCT}.parquet",
            description="root lot 존재 여부/제품 확인용 wafer-level ML_TABLE.",
        ),
    )
    HANDLER_ENTRY = CodeRef(
        module="backend.core.flowi_units.split_nav",
        function="SplitNavUnitAI.handle",
        description="스플릿테이블 키워드 + root lot 토큰 → navigate 액션",
    )
    INPUT_SCHEMA = {
        "type": "object",
        "properties": {"prompt": {"type": "string"}, "product": {"type": "string"}},
        "required": ["prompt"],
    }
    OUTPUT_SCHEMA = {
        "type": "object",
        "properties": {
            "handled": {"type": "boolean"},
            "answer": {"type": "string"},
            "navigate": {"type": "object"},
        },
        "required": ["handled"],
    }
    EXAMPLES = (
        {"prompt": "A1006 스플릿테이블 보여줘"},
        {"prompt": "splittable 열어줘"},
    )

    def feature_md_path(self) -> Path:
        return Path("docs/features/flowi-agent.md")

    def handle(
        self,
        prompt: str,
        slots: dict[str, Any],
        ctx: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        text = str(prompt or "")
        if not _SPLITTABLE_RE.search(text):
            return None
        # 스플릿테이블 키워드만으로는 약한 신호 — 열기 의도 또는 lot 토큰이 있어야 개입.
        cleaned = _SPLITTABLE_RE.sub(" ", text)
        tokens = [t.group(0).upper() for t in _TOKEN_RE.finditer(cleaned)]
        tables = _ml_table_products()
        products = {p.upper() for p, _fp in tables}
        product_hit = next((t for t in tokens if t in products), "")
        lot_tokens = [t for t in tokens
                      if t not in _STOPWORDS and t not in products and any(ch.isdigit() for ch in t)]
        root = lot_tokens[0] if lot_tokens else ""
        if not root and not _ACTION_RE.search(text):
            return None

        found_products: list[str] = []
        note = ""
        if root:
            found_products = _find_products_for_root(root, tables)
            if found_products:
                note = f"ML_TABLE 확인: {root} → {', '.join(found_products)}"
            else:
                note = (f"주의: ML_TABLE 에서 root lot '{root}' 을(를) 찾지 못했습니다 — "
                        "페이지에서 직접 확인하세요.")
        product = product_hit or (found_products[0] if len(found_products) == 1 else "")

        params = []
        if product:
            params.append(f"product={product}")
        if root:
            params.append(f"root={root}")
        search = ("?" + "&".join(params)) if params else ""
        answer = (f"SplitTable 을 엽니다" + (f" — root lot {root}" if root else "")
                  + (f" ({product})" if product else "") + "."
                  + (f"\n{note}" if note else ""))
        return {
            "handled": True,
            "type": "answer",
            "intent": "split_nav",
            "feature": "splittable",
            "unit_ai": "split_nav",
            "action": "open_splittable",
            "answer": answer,
            "product": product,
            "root_lot_id": root,
            "navigate": {"tab": "splittable", "search": search, "auto": True},
        }
