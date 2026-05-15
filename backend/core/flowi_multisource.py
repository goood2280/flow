"""Read-only Flow-i multi-source filter/join helper.

This module keeps Home Flow-i cross-source execution deterministic:
schema docs and the column catalog provide term evidence, confirmed
schema_relations provide join evidence, and actual DB/base files provide rows.
It does not accept free-form SELECT/FROM SQL from the LLM.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl

from core import dashboard_join
from core import knowledge_vault as kv
from core.paths import PATHS


SCHEMA_RELATION_FILE = kv.SCHEMA_RELATION_FILE
MAX_SELECTED_SOURCES = 4
MAX_SOURCE_BYTES = 200 * 1024 * 1024
MAX_COLLECT_ROWS = 5000
MAX_OUTPUT_COLUMNS = 48


_SOURCE_WORDS = {
    "FAB",
    "ET",
    "INLINE",
    "VM",
    "EDS",
    "QTIME",
    "ML_TABLE",
    "KNOB",
    "MASK",
    "SPC",
}
_JOIN_TERMS = {
    "join",
    "joined",
    "조인",
    "합쳐",
    "합친",
    "연결",
    "관계",
    "relation",
    "multi",
    "여러",
    "db",
    "file",
    "파일",
    "소스",
    "source",
}
_QUERY_TERMS = {
    "보여",
    "조회",
    "확인",
    "preview",
    "필터",
    "filter",
    "chart",
    "차트",
    "그래프",
    "그려",
    "table",
}
_IDENTITY_ALIASES = {
    "product": {"product", "prod", "device"},
    "root_lot_id": {"rootlotid", "root_lot_id", "rootlot", "root_lot", "lot", "lot_id", "lotid"},
    "lot_id": {"lotid", "lot_id", "fablotid", "fab_lot_id", "currentlotid", "current_lot_id"},
    "wafer_id": {"waferid", "wafer_id", "wf", "wf_id", "wfid", "wafer"},
    "lot_wf": {"lotwf", "lot_wf", "lotwafer", "lot_wafer"},
    "step_id": {"stepid", "step_id", "processstep", "process_step"},
    "function_step": {"functionstep", "function_step", "funcstep", "func_step", "module"},
}


@dataclass
class SourceProfile:
    source_id: str
    source_type: str
    label: str
    files: list[Path] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    dtypes: dict[str, str] = field(default_factory=dict)
    catalog_rows: list[dict[str, Any]] = field(default_factory=list)
    score: int = 0
    terms: list[str] = field(default_factory=list)
    join_columns: dict[str, str] = field(default_factory=dict)
    selected_columns: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def bytes(self) -> int:
        total = 0
        for fp in self.files:
            try:
                total += fp.stat().st_size
            except Exception:
                pass
        return total


def _load_schema_registry() -> dict[str, Any]:
    try:
        data = json.loads(Path(SCHEMA_RELATION_FILE).read_text(encoding="utf-8"))
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get("relations"), list):
        data["relations"] = []
    if not isinstance(data.get("column_catalog"), list):
        data["column_catalog"] = []
    return data


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


def _safe_output_prefix(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[:40] or "source"


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def _source_key(source_id: str, label: str = "") -> str:
    return _norm(label) or _norm(source_id)


def _source_words(label: str, source_id: str) -> set[str]:
    raw = f"{label} {source_id}".replace("_", " ")
    words = {w for w in re.findall(r"[A-Za-z][A-Za-z0-9]*", raw.upper()) if len(w) > 1}
    if "ML" in words and "TABLE" in words:
        words.add("ML_TABLE")
    return words


def _canonical_column(name: str) -> str:
    n = _norm(name)
    for canonical, aliases in _IDENTITY_ALIASES.items():
        if n in {_norm(a) for a in aliases}:
            return canonical
    return ""


def _column_aliases(row: dict[str, Any]) -> set[str]:
    out = {_norm(row.get("column")), _norm(row.get("canonical_alias")), _norm(row.get("fk"))}
    for raw in row.get("raw_names") or []:
        out.add(_norm(raw))
    return {x for x in out if x}


def _find_column(columns: list[str], *candidates: Any) -> str:
    by_norm = {_norm(col): col for col in columns}
    for raw in candidates:
        if not raw:
            continue
        found = by_norm.get(_norm(raw))
        if found:
            return found
    return ""


def _relation_sources(relations: list[dict[str, Any]]) -> dict[str, SourceProfile]:
    sources: dict[str, SourceProfile] = {}
    for rel in relations:
        if not isinstance(rel, dict):
            continue
        for side in ("left", "right"):
            sid = str(rel.get(f"{side}_source_id") or "").strip()
            if not sid:
                continue
            sources.setdefault(
                sid,
                SourceProfile(
                    source_id=sid,
                    source_type=str(rel.get(f"{side}_source_type") or "").strip().lower() or "file",
                    label=str(rel.get(f"{side}_label") or sid).strip() or sid,
                ),
            )
    return sources


def _add_catalog_sources(sources: dict[str, SourceProfile], catalog: list[dict[str, Any]]) -> None:
    existing_keys = {_source_key(source.source_id, source.label) for source in sources.values()}
    for row in catalog:
        if not isinstance(row, dict):
            continue
        relation_id = str(row.get("relation_id") or "").strip()
        if not relation_id or _norm(relation_id) in existing_keys:
            continue
        relation_u = _upper(relation_id)
        if relation_u.startswith("ML_TABLE") or relation_u.endswith((".PARQUET", ".CSV")):
            file_name = relation_id if relation_u.endswith((".PARQUET", ".CSV")) else f"{relation_id}.parquet"
            source_id = f"file_base_root_{file_name}"
            source_type = "file"
        else:
            source_id = f"db_{relation_id}"
            source_type = "db"
        if source_id in sources:
            continue
        sources[source_id] = SourceProfile(source_id=source_id, source_type=source_type, label=relation_id)
        existing_keys.add(_norm(relation_id))


def _catalog_relation_matches_source(row_relation_id: str, source: SourceProfile) -> bool:
    rel = str(row_relation_id or "").strip()
    if not rel:
        return False
    rel_n = _norm(rel)
    label_n = _norm(source.label)
    sid_n = _norm(source.source_id)
    if rel_n in {label_n, sid_n}:
        return True
    if rel_n and (rel_n in sid_n or sid_n in rel_n or rel_n in label_n or label_n in rel_n):
        return True
    return False


def _attach_catalog_rows(sources: dict[str, SourceProfile], catalog: list[dict[str, Any]]) -> None:
    for source in sources.values():
        source.catalog_rows = [
            row for row in catalog
            if isinstance(row, dict) and _catalog_relation_matches_source(str(row.get("relation_id") or ""), source)
        ][:200]


def _product_from_label(label: str, product_hint: str = "") -> str:
    if product_hint:
        return _upper(product_hint)
    words = re.findall(r"[A-Za-z][A-Za-z0-9_]*", str(label or ""))
    for word in reversed(words):
        up = _upper(word)
        if up and up not in _SOURCE_WORDS and up not in {"DB", "ROOT", "BASE", "FILE"}:
            return up
    return ""


def _resolve_source_files(source: SourceProfile, product_hint: str = "") -> tuple[list[Path], list[str]]:
    warnings: list[str] = []
    sid = source.source_id
    stype = source.source_type
    label = source.label

    if stype == "db" or sid.startswith("db_"):
        body = sid[3:] if sid.startswith("db_") else sid
        product = _product_from_label(label, product_hint)
        root_part = body
        if product and _upper(body).endswith("_" + product):
            root_part = body[: -(len(product) + 1)]
        root_part = root_part.strip("_")
        candidates: list[Path] = []
        if root_part and root_part not in {"db", "db_root"}:
            candidates.append((PATHS.db_root / root_part / product).resolve() if product else (PATHS.db_root / root_part).resolve())
        if product:
            candidates.append((PATHS.db_root / product).resolve())
        candidates.append(PATHS.db_root.resolve())
        for target in candidates:
            if not _is_relative_to(target, PATHS.db_root) or not target.exists():
                continue
            if target.is_file() and target.suffix.lower() in {".parquet", ".csv"}:
                return [target], warnings
            if target.is_dir():
                files = sorted(target.rglob("*.parquet"))
                if not files:
                    files = sorted(target.rglob("*.csv"))
                if files:
                    return files[:80], warnings
        warnings.append(f"source not found: {label or sid}")
        return [], warnings

    file_name = ""
    for prefix in ("file_base_root_", "file_db_root_"):
        if sid.startswith(prefix):
            file_name = sid[len(prefix):]
            root = PATHS.base_root if prefix == "file_base_root_" else PATHS.db_root
            cand = (root / file_name).resolve()
            if _is_relative_to(cand, root) and cand.is_file() and cand.suffix.lower() in {".parquet", ".csv"}:
                return [cand], warnings
    for raw in (label, sid):
        text = str(raw or "").strip()
        if not text:
            continue
        if "." not in text:
            text = text.replace(" ", "_")
        for root in (PATHS.base_root, PATHS.db_root):
            cand = (root / text).resolve()
            if _is_relative_to(cand, root) and cand.is_file() and cand.suffix.lower() in {".parquet", ".csv"}:
                return [cand], warnings
    warnings.append(f"source not found: {label or sid}")
    return [], warnings


def _scan_files(files: list[Path]) -> pl.LazyFrame:
    if not files:
        raise ValueError("no files")
    suffixes = {fp.suffix.lower() for fp in files}
    if suffixes == {".parquet"}:
        return pl.scan_parquet([str(fp) for fp in files])
    if len(files) == 1 and files[0].suffix.lower() == ".csv":
        return pl.scan_csv(str(files[0]), infer_schema_length=5000, try_parse_dates=False)
    raise ValueError("mixed or unsupported source files")


def _schema_for_source(source: SourceProfile) -> None:
    if not source.files:
        return
    try:
        schema = _scan_files(source.files).collect_schema()
        source.columns = list(schema.names())
        source.dtypes = {name: str(schema[name]) for name in source.columns}
    except Exception as exc:
        source.warnings.append(f"schema read failed: {source.label}: {exc}")


def _extract_lot_tokens(prompt: str) -> list[str]:
    out: list[str] = []
    for token in re.findall(r"\b[A-Z][A-Z0-9]{4,}(?:\.[A-Z0-9]+)?\b", _upper(prompt)):
        if token in _SOURCE_WORDS or not re.search(r"\d", token):
            continue
        root = token.split(".", 1)[0]
        if root and root not in out:
            out.append(root)
    return out[:12]


def _extract_wafer_tokens(prompt: str) -> list[str]:
    out: list[str] = []
    for match in re.finditer(r"(?:#|WF|WAFER|W)\s*0?(\d{1,3})\b", str(prompt or ""), flags=re.I):
        try:
            value = int(match.group(1))
        except Exception:
            continue
        if 1 <= value <= 25 and str(value) not in out:
            out.append(str(value))
    return out[:25]


def _prompt_terms(prompt: str, limit: int = 16) -> list[str]:
    text = str(prompt or "")
    parts = re.findall(r"[A-Za-z][A-Za-z0-9_.-]*|[가-힣][가-힣0-9_]*", text)
    terms: list[str] = []
    seen: set[str] = set()
    for part in parts:
        clean = part.strip("._-")
        if len(clean) < 2:
            continue
        key = clean.casefold()
        if key in seen:
            continue
        seen.add(key)
        terms.append(clean)
    for size in (3, 2):
        for idx in range(0, max(0, len(parts) - size + 1)):
            phrase = " ".join(parts[idx:idx + size]).strip()
            key = phrase.casefold()
            if len(phrase) > 80 or key in seen:
                continue
            seen.add(key)
            terms.append(phrase)
    return terms[:limit]


def _lookup_prompt_knowledge(prompt: str) -> tuple[list[dict[str, Any]], set[str], set[str]]:
    knowledge: list[dict[str, Any]] = []
    relation_hits: set[str] = set()
    column_hits: set[str] = set()
    seen_ids: set[str] = set()
    for term in _prompt_terms(prompt):
        try:
            found = kv.lookup_term(term, limit=8)
        except Exception:
            continue
        for row in found.get("columns") or []:
            if not isinstance(row, dict):
                continue
            relation = str(row.get("relation_id") or "").strip()
            column = str(row.get("column") or "").strip()
            if relation:
                relation_hits.add(relation)
            if column:
                column_hits.add(_norm(column))
            item_id = f"column:{relation}.{column}" if relation else f"column:{column}"
            if item_id not in seen_ids:
                seen_ids.add(item_id)
                knowledge.append({
                    "id": item_id,
                    "doc_id": row.get("wiki_doc_id") or item_id,
                    "kind": "column_catalog",
                    "title": f"{relation}.{column}" if relation else column,
                    "summary": row.get("canonical_alias") or row.get("dtype") or "",
                    "term": term,
                    "source": "column_catalog",
                    "relation_id": relation,
                    "column": column,
                })
        for doc in found.get("docs") or []:
            if not isinstance(doc, dict):
                continue
            doc_id = str(doc.get("doc_id") or "").strip()
            if not doc_id or doc_id in seen_ids:
                continue
            seen_ids.add(doc_id)
            knowledge.append({
                "id": doc_id,
                "doc_id": doc_id,
                "kind": doc.get("kind") or "wiki_doc",
                "title": doc.get("title") or doc_id,
                "summary": doc.get("summary") or "",
                "term": term,
                "source": "schema_doc" if doc.get("kind") == "schema_doc" else "agent_wiki",
                "relation_id": doc.get("relation_id") or "",
            })
            if doc.get("relation_id"):
                relation_hits.add(str(doc.get("relation_id") or ""))
            for ref in doc.get("column_refs") or []:
                if "." in str(ref):
                    rel, col = str(ref).split(".", 1)
                    relation_hits.add(rel)
                    column_hits.add(_norm(col))
    return knowledge[:24], relation_hits, column_hits


def _score_sources(
    sources: dict[str, SourceProfile],
    prompt: str,
    relation_hits: set[str],
    product: str = "",
) -> list[SourceProfile]:
    prompt_norm = _norm(prompt)
    prompt_words = set(re.findall(r"[A-Za-z][A-Za-z0-9_]*", str(prompt or "").upper()))
    if "ML_TABLE" in str(prompt or "").upper() or {"ML", "TABLE"}.issubset(prompt_words):
        prompt_words.add("ML_TABLE")
    scored: list[SourceProfile] = []
    for source in sources.values():
        score = 0
        terms: list[str] = []
        label_norm = _norm(source.label)
        sid_norm = _norm(source.source_id)
        if label_norm and label_norm in prompt_norm:
            score += 5
            terms.append(source.label)
        if sid_norm and sid_norm in prompt_norm:
            score += 5
            terms.append(source.source_id)
        words = _source_words(source.label, source.source_id)
        source_word_hits = sorted((words & _SOURCE_WORDS) & prompt_words)
        if source_word_hits:
            score += 3 + len(source_word_hits)
            terms.extend(source_word_hits)
        for relation_id in relation_hits:
            if _catalog_relation_matches_source(relation_id, source):
                score += 6
                terms.append(relation_id)
        if product:
            product_u = _upper(product)
            if product_u and product_u in _upper(source.label + " " + source.source_id) and score > 0:
                score += 1
        if score > 0:
            source.score = score
            source.terms = list(dict.fromkeys([t for t in terms if t]))
            scored.append(source)
    scored.sort(key=lambda s: (s.score, s.label), reverse=True)
    return scored[:MAX_SELECTED_SOURCES]


def _confirmed_relations_between(relations: list[dict[str, Any]], source_ids: set[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rel in relations:
        if not isinstance(rel, dict):
            continue
        if str(rel.get("status") or "").lower() != "confirmed":
            continue
        left = str(rel.get("left_source_id") or "")
        right = str(rel.get("right_source_id") or "")
        if left in source_ids and right in source_ids:
            out.append(rel)
    return out


def _relation_side_column(rel: dict[str, Any], source_id: str) -> tuple[str, str]:
    if source_id == str(rel.get("left_source_id") or ""):
        return "left", str(rel.get("left_column") or "").strip()
    if source_id == str(rel.get("right_source_id") or ""):
        return "right", str(rel.get("right_column") or "").strip()
    return "", ""


def _relation_key(rel: dict[str, Any]) -> str:
    key = str(rel.get("canonical_key") or "").strip()
    if key:
        clean = re.sub(r"[^A-Za-z0-9_]+", "_", key).strip("_").lower()
        return clean or (_norm(key) or key)
    return _canonical_column(str(rel.get("left_column") or "")) or _norm(rel.get("left_column") or rel.get("right_column"))


def _attach_join_columns(sources: dict[str, SourceProfile], relations: list[dict[str, Any]]) -> list[str]:
    missing: list[str] = []
    for rel in relations:
        key = _relation_key(rel)
        if not key:
            continue
        for side in ("left", "right"):
            sid = str(rel.get(f"{side}_source_id") or "")
            source = sources.get(sid)
            if not source:
                continue
            col = _find_column(source.columns, rel.get(f"{side}_column"))
            if not col:
                missing.append(f"{source.label}.{rel.get(f'{side}_column')} missing")
                continue
            source.join_columns.setdefault(key, col)
    return missing


def _source_selected_columns(source: SourceProfile, prompt: str, column_hits: set[str], chart_intent: bool) -> list[str]:
    selected: list[str] = []

    def add(col: str) -> None:
        if col and col in source.columns and col not in selected:
            selected.append(col)

    for col in source.join_columns.values():
        add(col)
    for canonical in ("product", "root_lot_id", "lot_id", "wafer_id", "lot_wf", "step_id", "function_step"):
        aliases = _IDENTITY_ALIASES.get(canonical) or {canonical}
        add(_find_column(source.columns, *aliases, canonical))
    prompt_norm = _norm(prompt)
    for row in source.catalog_rows:
        aliases = _column_aliases(row)
        if aliases & column_hits or any(alias and alias in prompt_norm for alias in aliases):
            add(_find_column(source.columns, row.get("column"), row.get("canonical_alias"), *(row.get("raw_names") or [])))
    for col in source.columns:
        col_norm = _norm(col)
        if col_norm in column_hits or (len(col_norm) > 2 and col_norm in prompt_norm):
            add(col)
    if chart_intent:
        for col in source.columns:
            dtype = str(source.dtypes.get(col) or "").lower()
            if any(t in dtype for t in ("int", "float", "decimal")):
                add(col)
                if len(selected) >= 12:
                    break
    if not selected:
        for col in source.columns[:8]:
            add(col)
    return selected[:24]


def _string_key_expr(col: str) -> pl.Expr:
    return pl.col(col).cast(pl.Utf8, strict=False).str.strip_chars().str.to_uppercase()


def _wafer_key_expr(col: str) -> pl.Expr:
    raw = pl.col(col).cast(pl.Utf8, strict=False).str.strip_chars().str.to_uppercase()
    stripped = raw.str.replace(r"^(?:WAFER|WF|W)", "")
    numeric = stripped.cast(pl.Int64, strict=False)
    return pl.when((numeric >= 1) & (numeric <= 25)).then(numeric.cast(pl.Utf8, strict=False)).otherwise(None)


def _join_key_expr(key: str, col: str) -> pl.Expr:
    if "wafer" in _norm(key):
        return _wafer_key_expr(col)
    return _string_key_expr(col)


def _filter_source_lf(lf: pl.LazyFrame, source: SourceProfile, filters: dict[str, Any]) -> pl.LazyFrame:
    product = str(filters.get("product") or "").strip()
    lots = [str(x).strip().upper() for x in filters.get("root_lot_ids") or [] if str(x).strip()]
    wafers = [str(x).strip() for x in filters.get("wafer_ids") or [] if str(x).strip()]
    if product:
        product_col = _find_column(source.columns, "product", "prod", "device")
        if product_col:
            lf = lf.filter(_string_key_expr(product_col).eq(_upper(product)))
    if lots:
        root_col = _find_column(source.columns, "root_lot_id", "rootlot", "ROOT_LOT_ID")
        lot_col = _find_column(source.columns, "lot_id", "fab_lot_id", "LOT_ID")
        exprs = []
        if root_col:
            exprs.append(_string_key_expr(root_col).is_in(lots))
        if lot_col:
            exprs.append(_string_key_expr(lot_col).str.split(".").list.first().is_in(lots))
        if exprs:
            expr = exprs[0]
            for extra in exprs[1:]:
                expr = expr | extra
            lf = lf.filter(expr)
    if wafers:
        wafer_col = _find_column(source.columns, "wafer_id", "wf_id", "WAFER_ID")
        if wafer_col:
            lf = lf.filter(_wafer_key_expr(wafer_col).is_in(wafers))
    return lf


def _source_frame(source: SourceProfile, filters: dict[str, Any]) -> tuple[pl.DataFrame | None, list[str]]:
    warnings: list[str] = []
    if not source.files:
        return None, [f"source not found: {source.label}"]
    if source.bytes > MAX_SOURCE_BYTES and not (filters.get("root_lot_ids") or filters.get("wafer_ids")):
        return None, [f"large source requires filter: {source.label} ({source.bytes} bytes)"]
    try:
        lf = _scan_files(source.files)
        lf = _filter_source_lf(lf, source, filters)
        columns = list(dict.fromkeys([*source.selected_columns, *source.join_columns.values()]))
        columns = [col for col in columns if col in source.columns]
        if len(source.columns) > 500 and len(columns) <= len(source.join_columns):
            return None, [f"wide source requires selected columns: {source.label} ({len(source.columns)} columns)"]
        if not columns:
            columns = source.columns[: min(12, len(source.columns))]
        lf = lf.select(columns)
        for key, col in source.join_columns.items():
            if col in columns:
                lf = lf.with_columns(_join_key_expr(key, col).alias(f"__join_{key}"))
        df = lf.head(MAX_COLLECT_ROWS).collect()
    except Exception as exc:
        return None, [f"source read failed: {source.label}: {exc}"]
    prefix = _safe_output_prefix(source.label)
    rename = {col: f"{prefix}.{col}" for col in df.columns if not str(col).startswith("__join_")}
    return df.rename(rename), warnings


def _connectivity_join_plan(selected: list[SourceProfile], relations: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    if len(selected) <= 1:
        return [], []
    pending = {s.source_id for s in selected}
    connected = {selected[0].source_id}
    pending.remove(selected[0].source_id)
    plan: list[dict[str, Any]] = []
    warnings: list[str] = []
    while pending:
        progressed = False
        for sid in list(pending):
            rels = [
                rel for rel in relations
                if {
                    str(rel.get("left_source_id") or ""),
                    str(rel.get("right_source_id") or ""),
                } & connected
                and sid in {str(rel.get("left_source_id") or ""), str(rel.get("right_source_id") or "")}
            ]
            if not rels:
                continue
            keys = []
            relation_ids = []
            for rel in rels:
                key = _relation_key(rel)
                if key and key not in keys:
                    keys.append(key)
                rid = str(rel.get("relation_id") or "").strip()
                if rid:
                    relation_ids.append(rid)
            plan.append({
                "source_id": sid,
                "join_keys": keys,
                "relation_ids": list(dict.fromkeys(relation_ids)),
                "connected_to": sorted(connected),
            })
            connected.add(sid)
            pending.remove(sid)
            progressed = True
        if not progressed:
            warnings.append("confirmed relation chain is incomplete")
            break
    if pending:
        warnings.append("관계 확인 필요: " + ", ".join(sorted(pending)))
    return plan, warnings


def _join_frames(
    selected: list[SourceProfile],
    frames: dict[str, pl.DataFrame],
    plan: list[dict[str, Any]],
) -> tuple[pl.DataFrame | None, list[str]]:
    warnings: list[str] = []
    if not selected:
        return None, ["no selected source"]
    base = frames.get(selected[0].source_id)
    if base is None:
        return None, [f"source frame missing: {selected[0].label}"]
    joined = base
    for step in plan:
        sid = str(step.get("source_id") or "")
        right = frames.get(sid)
        if right is None:
            return None, [f"source frame missing: {sid}"]
        keys = [f"__join_{key}" for key in (step.get("join_keys") or []) if f"__join_{key}" in joined.columns and f"__join_{key}" in right.columns]
        if not keys:
            return None, [f"join key not available for {sid}"]
        try:
            joined = joined.join(right, on=keys, how="inner", coalesce=True)
        except Exception as exc:
            return None, [f"join failed for {sid}: {exc}"]
    drop_cols = [col for col in joined.columns if str(col).startswith("__join_")]
    if drop_cols:
        joined = joined.drop(drop_cols)
    if len(joined.columns) > MAX_OUTPUT_COLUMNS:
        keep = joined.columns[:MAX_OUTPUT_COLUMNS]
        joined = joined.select(keep)
        warnings.append(f"output columns capped to {MAX_OUTPUT_COLUMNS}")
    return joined, warnings


def _rows_from_df(df: pl.DataFrame, max_rows: int) -> list[dict[str, Any]]:
    rows = df.head(max(1, min(int(max_rows or 12), 100))).to_dicts()
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append({str(k): (None if v is None else v) for k, v in row.items()})
    return out


def _numeric_columns(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return []
    cols = list(rows[0].keys())
    out = []
    for col in cols:
        for row in rows[:20]:
            value = row.get(col)
            if value in (None, ""):
                continue
            try:
                float(value)
            except Exception:
                break
            out.append(col)
            break
    return out


def _chart_payload(prompt: str, rows: list[dict[str, Any]], selected_columns: list[str], evidence: dict[str, Any]) -> dict[str, Any]:
    chart_type = dashboard_join.infer_chart_type(prompt, selected_columns)
    if chart_type == "box":
        chart_type = "boxplot"
    numeric = _numeric_columns(rows)
    columns = list(rows[0].keys()) if rows else selected_columns
    x_col = ""
    y_col = ""
    if chart_type in {"scatter", "correlation_matrix"} and len(numeric) >= 2:
        x_col, y_col = numeric[0], numeric[1]
    else:
        y_col = numeric[0] if numeric else ""
        x_col = next((c for c in columns if c != y_col), columns[0] if columns else "")
    config = {
        "chart_type": chart_type,
        "x": x_col,
        "y": y_col,
        "columns": selected_columns[:40],
        "source_evidence": evidence,
        "title": "Flow-i joined dataset chart",
        "editable": True,
    }
    points = []
    for row in rows[:500]:
        points.append({
            "x": row.get(x_col) if x_col else None,
            "y": row.get(y_col) if y_col else None,
            **{k: row.get(k) for k in columns[:8] if k not in {x_col, y_col}},
        })
    return {
        "chart_config": config,
        "chart_result": {
            "ok": True,
            "kind": f"dashboard_{chart_type}",
            "chart_type": chart_type,
            "title": config["title"],
            "points": points,
            "total": len(points),
            "config": config,
            "chart_config": config,
            "status": "draft",
        },
    }


def _has_multisource_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    up = text.upper()
    hard_join_terms = _JOIN_TERMS - {"db", "file", "파일", "소스", "source"}
    return (
        any(term in low or term in text for term in hard_join_terms)
        or len([word for word in _SOURCE_WORDS if word in up]) >= 2
    ) and any(term in low or term in text for term in _QUERY_TERMS)


def _has_source_query_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    up = text.upper()
    return any(word in up for word in _SOURCE_WORDS) and any(term in low or term in text for term in _QUERY_TERMS)


def _has_chart_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    return any(term in low or term in text for term in ("chart", "plot", "graph", "차트", "그래프", "그려", "scatter", "bar", "line", "box"))


def execute_multisource_request(prompt: str, product: str = "", max_rows: int = 12) -> dict[str, Any]:
    """Execute a deterministic read-only source query from prompt evidence."""
    prompt = str(prompt or "").strip()
    if not prompt:
        return {"handled": False}
    chart_intent = _has_chart_intent(prompt)
    multi_intent = _has_multisource_intent(prompt)
    source_query_intent = _has_source_query_intent(prompt)
    if not (multi_intent or chart_intent or source_query_intent):
        return {"handled": False}

    registry = _load_schema_registry()
    relations_all = [row for row in registry.get("relations") or [] if isinstance(row, dict)]
    catalog = [row for row in registry.get("column_catalog") or [] if isinstance(row, dict)]
    if not relations_all and not catalog:
        return {"handled": False}
    knowledge, relation_hits, column_hits = _lookup_prompt_knowledge(prompt)
    sources = _relation_sources(relations_all)
    _add_catalog_sources(sources, catalog)
    if not sources:
        return {"handled": False}
    _attach_catalog_rows(sources, catalog)
    selected = _score_sources(sources, prompt, relation_hits, product)
    if not selected:
        return {"handled": False}
    product_filter = _upper(product) or ""
    filters = {
        "product": product_filter,
        "root_lot_ids": [lot for lot in _extract_lot_tokens(prompt) if lot != product_filter],
        "wafer_ids": _extract_wafer_tokens(prompt),
    }
    selected_ids = {source.source_id for source in selected}
    confirmed_relations = _confirmed_relations_between(relations_all, selected_ids)
    missing_evidence: list[str] = []
    if len(selected) > 1 and not confirmed_relations:
        missing_evidence.append("관계 확인 필요: selected sources have no confirmed schema relation")

    for source in selected:
        source.files, warnings = _resolve_source_files(source, product_hint=filters["product"])
        source.warnings.extend(warnings)
        _schema_for_source(source)
        if not source.files:
            missing_evidence.append(f"missing source: {source.label}")
        if not source.columns:
            missing_evidence.append(f"missing schema: {source.label}")

    missing_evidence.extend(_attach_join_columns({s.source_id: s for s in selected}, confirmed_relations))
    plan, plan_warnings = _connectivity_join_plan(selected, confirmed_relations)
    missing_evidence.extend(plan_warnings)
    if len(selected) > 1 and len(plan) < len(selected) - 1:
        missing_evidence.append("관계 확인 필요: confirmed relation chain is required before join execution")
    if missing_evidence:
        return {
            "handled": True,
            "ok": False,
            "blocked": True,
            "status": "blocked",
            "reason": "missing_evidence",
            "answer": "요청한 데이터는 확인된 source/relation 근거가 부족해 실행하지 않았습니다.",
            "source_ids": sorted(selected_ids),
            "relation_ids": [str(r.get("relation_id") or "") for r in confirmed_relations if r.get("relation_id")],
            "join_keys": sorted({_relation_key(r) for r in confirmed_relations if _relation_key(r)}),
            "filters": filters,
            "selected_columns": [],
            "row_count": 0,
            "sample_rows": [],
            "warnings": list(dict.fromkeys(missing_evidence))[:20],
            "retrieved_knowledge": knowledge,
            "join_plan": {
                "sources": [{"source_id": s.source_id, "label": s.label, "source_type": s.source_type} for s in selected],
                "relations": confirmed_relations,
                "steps": plan,
                "missing_evidence": missing_evidence,
            },
        }

    for source in selected:
        source.selected_columns = _source_selected_columns(source, prompt, column_hits, chart_intent)
    frames: dict[str, pl.DataFrame] = {}
    warnings: list[str] = []
    for source in selected:
        frame, frame_warnings = _source_frame(source, filters)
        warnings.extend(source.warnings)
        warnings.extend(frame_warnings)
        if frame is None:
            return {
                "handled": True,
                "ok": False,
                "blocked": True,
                "status": "readiness" if any("large source" in w or "wide source" in w for w in frame_warnings) else "blocked",
                "reason": "source_readiness",
                "answer": "대형/wide source는 필터 또는 선택 컬럼을 먼저 확정해야 합니다.",
                "source_ids": sorted(selected_ids),
                "relation_ids": [str(r.get("relation_id") or "") for r in confirmed_relations if r.get("relation_id")],
                "join_keys": sorted({_relation_key(r) for r in confirmed_relations if _relation_key(r)}),
                "filters": filters,
                "selected_columns": [f"{s.label}.{c}" for s in selected for c in s.selected_columns],
                "row_count": 0,
                "sample_rows": [],
                "warnings": list(dict.fromkeys(warnings))[:20],
                "retrieved_knowledge": knowledge,
                "join_plan": {"sources": [{"source_id": s.source_id, "label": s.label} for s in selected], "relations": confirmed_relations, "steps": plan},
            }
        frames[source.source_id] = frame

    if len(selected) == 1:
        joined = frames[selected[0].source_id].drop([c for c in frames[selected[0].source_id].columns if str(c).startswith("__join_")])
        join_warnings: list[str] = []
    else:
        joined, join_warnings = _join_frames(selected, frames, plan)
        warnings.extend(join_warnings)
        if joined is None:
            return {
                "handled": True,
                "ok": False,
                "blocked": True,
                "status": "blocked",
                "reason": "join_failed",
                "answer": "confirmed relation은 있지만 join key 적용에 실패했습니다.",
                "source_ids": sorted(selected_ids),
                "relation_ids": [str(r.get("relation_id") or "") for r in confirmed_relations if r.get("relation_id")],
                "join_keys": sorted({_relation_key(r) for r in confirmed_relations if _relation_key(r)}),
                "filters": filters,
                "selected_columns": [f"{s.label}.{c}" for s in selected for c in s.selected_columns],
                "row_count": 0,
                "sample_rows": [],
                "warnings": list(dict.fromkeys(warnings))[:20],
                "retrieved_knowledge": knowledge,
                "join_plan": {"sources": [{"source_id": s.source_id, "label": s.label} for s in selected], "relations": confirmed_relations, "steps": plan},
            }

    sample_rows = _rows_from_df(joined, max_rows=max_rows)
    selected_columns = [col for col in joined.columns if not str(col).startswith("__join_")]
    relation_ids = [str(r.get("relation_id") or "") for r in confirmed_relations if r.get("relation_id")]
    join_keys = sorted({_relation_key(r) for r in confirmed_relations if _relation_key(r)})
    evidence = {
        "source_ids": sorted(selected_ids),
        "relation_ids": relation_ids,
        "join_keys": join_keys,
        "filters": filters,
        "selected_columns": selected_columns,
    }
    out: dict[str, Any] = {
        "handled": True,
        "ok": True,
        "blocked": False,
        "status": "success",
        "source_ids": sorted(selected_ids),
        "relation_ids": relation_ids,
        "join_keys": join_keys,
        "filters": filters,
        "selected_columns": selected_columns,
        "row_count": int(joined.height),
        "sample_rows": sample_rows,
        "warnings": list(dict.fromkeys(warnings))[:20],
        "retrieved_knowledge": knowledge,
        "join_plan": {
            "sources": [
                {
                    "source_id": s.source_id,
                    "label": s.label,
                    "source_type": s.source_type,
                    "files": [str(fp) for fp in s.files[:3]],
                    "selected_columns": s.selected_columns,
                }
                for s in selected
            ],
            "relations": confirmed_relations,
            "steps": plan,
        },
    }
    if chart_intent:
        out.update(_chart_payload(prompt, sample_rows, selected_columns, evidence))
    return out
