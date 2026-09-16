"""Authenticated PI collaboration, with server-side access and author attribution."""
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from core import product_wiki as wiki
from core.auth import current_user, is_page_manager, parse_tab_tokens


def require_access(request: Request):
    user = current_user(request)
    tabs, _ = parse_tab_tokens(user.get("tabs", ""))
    if user.get("role") == "admin" or is_page_manager(user, "productwiki") or set(tabs) & {"productwiki", "splittable", "lotmanage"}:
        return user
    raise HTTPException(403, "제품 위키 접근 권한이 필요합니다.")


router = APIRouter(prefix="/api/product-wiki", tags=["product-wiki"], dependencies=[Depends(require_access)])


class Entry(BaseModel):
    id: str = Field("", max_length=64)
    kind: Literal["structure", "split", "issue", "fact", "opinion", "decision"]
    title: str = Field(..., min_length=1, max_length=200)
    body: str = Field("", max_length=12000)
    structure: str = Field("", max_length=200)
    split: str = Field("", max_length=200)
    lot_ids: list[str] = Field(default_factory=list, max_length=100)
    purpose: str = Field("", max_length=3000)
    expected_effect: str = Field("", max_length=3000)
    observed_effect: str = Field("", max_length=3000)
    status: Literal["open", "investigating", "validated", "closed"] = "open"
    evidence: str = Field("", max_length=6000)
    occurred_on: str = Field("", max_length=10)
    related_ids: list[str] = Field(default_factory=list, max_length=100)
    source_text: str | None = Field(None, max_length=40000)


class SaveRequest(BaseModel):
    product: str = Field(..., min_length=1, max_length=200)
    expected_revision: int = Field(..., ge=0)
    entry: Entry


class ReportRequest(BaseModel):
    product: str = Field(..., min_length=1, max_length=200)
    use_ai: bool = False


class IntakeRequest(BaseModel):
    product: str = Field(..., min_length=1, max_length=200)
    expected_revision: int = Field(..., ge=0)
    text: str = Field(..., min_length=1, max_length=40000)
    entry_id: str = Field("", max_length=64)


@router.get("/products")
def products():
    from core.product_order import clean_product_order, load_product_order, order_products
    from core.lot_progress_cache import list_products
    from core.product_wiki_structure import matching_products
    names = wiki.products() + load_product_order() + matching_products()
    try:
        names += list_products()
    except Exception:
        pass
    # Include persisted LOT tables without scanning FAB/ML parquet files.
    from core.paths import PATHS
    import json
    for path in (PATHS.data_root / "lot_management" / "tables").glob("*.json"):
        try:
            names.append(json.loads(path.read_text("utf-8")).get("product", ""))
        except (OSError, ValueError):
            continue
    return {"products": order_products(clean_product_order(names))}


@router.get("/product")
def product(product: str = Query(..., min_length=1, max_length=200)):
    try:
        return wiki.document(product)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/sources")
def sources(product: str = Query(..., min_length=1, max_length=200)):
    """Read saved LOT tables only; never infer experiments from matching text."""
    import json
    from core.paths import PATHS
    from core.product_order import canonical_product_name
    target = canonical_product_name(product).casefold()
    tables, failed = [], False
    for path in (PATHS.data_root / "lot_management" / "tables").glob("*.json"):
        try:
            row = json.loads(path.read_text("utf-8"))
            if canonical_product_name(row.get("product")).casefold() == target:
                tables.append({key: row.get(key) for key in (
                    "product", "version", "updated_at", "updated_by", "columns", "rows")})
        except (OSError, ValueError, AttributeError):
            failed = True
    return {"lot_tables": tables, "warning": "일부 랏 관리 자료를 읽지 못했습니다." if failed else ""}


@router.post("/entries")
def save(req: SaveRequest, user=Depends(require_access)):
    entry = req.entry.model_dump() if hasattr(req.entry, "model_dump") else req.entry.dict()
    entry["title"] = entry["title"].strip()
    try:
        if not entry["title"]:
            raise ValueError("제목을 입력하세요.")
        if entry["occurred_on"]:
            date.fromisoformat(entry["occurred_on"])
        if any(not s.strip() or len(s) > 200 for s in entry["lot_ids"] + entry["related_ids"]):
            raise ValueError("연결 ID는 1~200자여야 합니다.")
        if entry["kind"] == "fact" and not entry["evidence"].strip():
            raise ValueError("사실 기록에는 측정·문서 등 근거를 입력하세요.")
        return wiki.save_entry(req.product, req.expected_revision, entry,
                               user["username"], is_page_manager(user, "productwiki"))
    except wiki.Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/intake")
def intake(req: IntakeRequest, user=Depends(require_access)):
    try:
        return wiki.intake_entry(req.product, req.expected_revision, req.text,
                                 user["username"], req.entry_id,
                                 is_page_manager(user, "productwiki"))
    except wiki.Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/history")
def history(product: str = Query(..., min_length=1, max_length=200), entry_id: str = "",
            before_revision: int | None = Query(None, ge=1)):
    rows = wiki.history(product, entry_id, before_revision)
    return {"history": rows, "next_before_revision": rows[-1]["revision"] if len(rows) == 100 else None}


@router.post("/report")
def report(req: ReportRequest, user=Depends(require_access)):
    try:
        return wiki.create_report(req.product, user["username"], req.use_ai)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class StructureRow(BaseModel):
    module: str = Field(..., min_length=1, max_length=100)
    path: str = Field(..., min_length=1, max_length=504)
    step_ids: list[str] = Field(default_factory=list, max_length=200)
    description: str = Field("", max_length=3000)


class StructureTransition(BaseModel):
    order: int | None = Field(None, ge=1, le=500)
    module: str = Field(..., min_length=1, max_length=100)
    from_path: str = Field("", max_length=504)
    to_path: str = Field("", max_length=504)
    relation: str = Field("", max_length=80)
    description: str = Field("", max_length=3000)
    step_ids: list[str] = Field(default_factory=list, max_length=200)


class StructureRequest(BaseModel):
    product: str = Field(..., min_length=1, max_length=200)
    expected_revision: int = Field(..., ge=0)
    rows: list[StructureRow] = Field(default_factory=list, max_length=500)
    # Optional so an older client that only knows about structure rows cannot
    # accidentally erase administrator-authored change history.
    transitions: list[StructureTransition] | None = Field(None, max_length=500)


@router.get("/structure")
def product_structure(product: str = Query(..., min_length=1, max_length=200)):
    from core import product_wiki_structure as structure
    try:
        return structure.document(product)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/structure")
def save_product_structure(req: StructureRequest, user=Depends(require_access)):
    from core import product_wiki_structure as structure
    try:
        rows = [row.model_dump() if hasattr(row, "model_dump") else row.dict() for row in req.rows]
        transitions = None if req.transitions is None else [item.model_dump() if hasattr(item, "model_dump") else item.dict() for item in req.transitions]
        return structure.save(req.product, req.expected_revision, rows, user["username"],
                              is_page_manager(user, "productwiki"), transitions)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except wiki.Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/structure/history")
def product_structure_history(product: str = Query(..., min_length=1, max_length=200)):
    from core import product_wiki_structure as structure
    try:
        return {"history": structure.history(product)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
