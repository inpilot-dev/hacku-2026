"""GET /catalog from contracts/openapi.json.

Serves the same Catalog instance the wallet prices quotes from, so a listed
price and a quoted price cannot come from different files. Mount with
``app.include_router(build_catalog_router(wallet), prefix="/api/v1")``.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from mandate.payments.auth import Actor, current_actor, require_role
from mandate.payments.catalog import CatalogError
from mandate.payments.errors import not_found
from mandate.payments.models import Category, CategoryStatus, Currency
from mandate.payments.service import Wallet


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Product(_Strict):
    id: StrictStr
    merchant_id: StrictStr
    title: StrictStr
    description: StrictStr
    category: Category
    category_status: CategoryStatus
    unit_label: StrictStr
    unit_price_minor: Annotated[StrictInt, Field(ge=0)]
    currency: Currency
    available: StrictBool
    evidence_ids: list[StrictStr]


class Evidence(_Strict):
    id: StrictStr
    source_url: StrictStr
    observed_at: StrictStr
    kind: Literal["product_price", "delivery_fee", "merchant_identity", "category"]
    capture_path: StrictStr | None
    conditions: StrictStr


class CatalogResponse(_Strict):
    products: list[Product]
    evidence: list[Evidence]


def _user_or_agent(actor: Actor = Depends(current_actor)) -> Actor:
    return require_role(actor, "user", "agent")


def build_catalog_router(wallet: Wallet) -> APIRouter:
    router = APIRouter(tags=["catalog"])

    @router.get("/catalog", response_model=CatalogResponse)
    def get_catalog(actor: Annotated[Actor, Depends(_user_or_agent)],
                    merchant_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None):
        try:
            listing = wallet.catalog.listing(merchant_id)
        except CatalogError as exc:
            if exc.kind == "not_found":
                raise not_found("Merchant") from exc
            raise
        body = CatalogResponse.model_validate(listing).model_dump(mode="json")
        return JSONResponse(body)

    return router
