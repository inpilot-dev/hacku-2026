"""One-time purchases and delivery details (proposed contract additions, user role).

    GET  /profile                      the user's delivery details (404 until saved)
    PUT  /profile                      save them (onboarding)
    POST /purchases                    start a purchase from a typed request (202)
    GET  /purchases/{id}               progress, events, the order waiting for approval
    POST /purchases/{id}/approve       pay exactly `total_minor` (one-shot)
    POST /purchases/{id}/cancel        stop before paying
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from mandate.payments.auth import Actor, current_actor, require_role
from mandate.payments.errors import not_found

from .profile import ProfileStore, missing_fields
from .runs import PurchaseRuns


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


Short = Annotated[StrictStr, Field(max_length=200)]


class Profile(_Strict):
    full_name: Short
    email: Short
    phone: Short
    address_line1: Short
    address_line2: Short | None = None
    district: Short
    region: Short | None = None  # Hong Kong Island / Kowloon / New Territories; derived from district if omitted
    city: Short | None = "Hong Kong"
    country: Short = "Hong Kong"
    postal_code: Short | None = None


class PurchaseRequest(_Strict):
    text: Annotated[StrictStr, Field(min_length=3, max_length=1000)]


class Approval(_Strict):
    total_minor: Annotated[StrictInt, Field(ge=1)]


def _user(actor: Actor = Depends(current_actor)) -> Actor:
    return require_role(actor, "user")


def build_purchase_router(runs: PurchaseRuns, profiles: ProfileStore) -> APIRouter:
    router = APIRouter(tags=["purchases"])

    @router.get("/profile")
    def get_profile(actor: Annotated[Actor, Depends(_user)]):
        profile = profiles.get(actor.family_id)
        if profile is None:
            raise not_found("Profile")
        return JSONResponse({**profile, "missing": missing_fields(profile)})

    @router.put("/profile")
    def put_profile(actor: Annotated[Actor, Depends(_user)], body: Profile):
        profile = profiles.put(actor.family_id, body.model_dump())
        return JSONResponse({**profile, "missing": missing_fields(profile)})

    @router.post("/purchases", status_code=202)
    def start(actor: Annotated[Actor, Depends(_user)], body: PurchaseRequest):
        return JSONResponse(runs.start(actor, body.text.strip()), status_code=202)

    @router.get("/purchases/{run_id}")
    def get(actor: Annotated[Actor, Depends(_user)], run_id: str):
        return JSONResponse(runs.get(actor, run_id))

    @router.post("/purchases/{run_id}/approve", status_code=202)
    def approve(actor: Annotated[Actor, Depends(_user)], run_id: str, body: Approval):
        return JSONResponse(runs.approve(actor, run_id, body.total_minor), status_code=202)

    @router.post("/purchases/{run_id}/cancel")
    def cancel(actor: Annotated[Actor, Depends(_user)], run_id: str):
        return JSONResponse(runs.cancel(actor, run_id))

    return router
