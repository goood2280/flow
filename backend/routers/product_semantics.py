"""Shared product semantics with explicit administrator vocabulary management."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from core import product_semantics as semantic, product_wiki as wiki
from core.auth import require_admin, is_page_manager
from routers.product_wiki import require_access

router = APIRouter(prefix="/api/product-semantics", tags=["product-semantics"])


class Input(BaseModel):
    product: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=40000)


class Confirmation(BaseModel):
    product: str = Field(min_length=1, max_length=200)
    id: str = Field(min_length=1, max_length=64)
    draft: dict


class Aliases(BaseModel):
    product: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=50)


class ItemAliasRequest(BaseModel):
    product: str = Field(min_length=1, max_length=200)
    step_id: str = Field(..., max_length=100)
    item_id: str = Field(..., max_length=100)
    aliases: list[str] = Field(default_factory=list, max_length=50)
    module: str = Field("", max_length=100)
    step_desc: str = Field("", max_length=300)
    item_desc: str = Field("", max_length=300)


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except wiki.Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/catalog")
def catalog(user=Depends(require_admin)):
    return {**semantic.snapshot(), "product_aliases": semantic.product_aliases()}


@router.post("/bootstrap")
def bootstrap(user=Depends(require_admin)):
    return _call(semantic.bootstrap, user["username"])


@router.post("/product-aliases")
def product_aliases(body: Aliases, user=Depends(require_admin)):
    return _call(semantic.save_product_aliases, body.product, body.aliases, user["username"])


@router.get("/product")
def product(product: str, user=Depends(require_access)):
    return _call(semantic.overview, product)


@router.post("/propose")
def propose(body: Input, user=Depends(require_admin)):
    return _call(semantic.propose, body.product, body.text, user["username"])


@router.post("/confirm")
def confirm(body: Confirmation, user=Depends(require_access)):
    return _call(semantic.confirm, body.product, body.id, body.draft, user["username"],
                 manager=user.get("role") == "admin" or is_page_manager(user, "productwiki"))


@router.post("/item-alias")
def save_item_alias(body: ItemAliasRequest, user=Depends(require_admin)):
    return _call(semantic.save_item_alias, body.product, body.step_id, body.item_id,
                 body.aliases, user["username"], body.module, body.step_desc, body.item_desc)
