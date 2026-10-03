"""Store connections and real-cart sync (proposed contract additions, user role).

    GET    /stores                       every supported store and its connection status
    POST   /stores/{store_id}/connect    open the shop's sign-in in the store window (202)
    GET    /stores/{store_id}            status; finishes a sign-in once the shop logs the user in
    DELETE /stores/{store_id}/connection forget the saved session
    POST   /carts/sync                   fill the store cart with a quote; never checks out
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictStr

from mandate.payments.auth import Actor, current_actor, require_role

from .carts import CartSync
from .connections import StoreConnections


class CartSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mandate_id: StrictStr
    quote_id: StrictStr


def _user(actor: Actor = Depends(current_actor)) -> Actor:
    return require_role(actor, "user")


def build_store_router(connections: StoreConnections, carts: CartSync) -> APIRouter:
    router = APIRouter(tags=["stores"])

    @router.get("/stores")
    def list_stores(actor: Annotated[Actor, Depends(_user)]):
        return JSONResponse(connections.list(actor))

    @router.post("/stores/{store_id}/connect", status_code=202)
    def connect(actor: Annotated[Actor, Depends(_user)], store_id: str):
        return JSONResponse(connections.start(actor, store_id), status_code=202)

    @router.get("/stores/{store_id}")
    def get_store(actor: Annotated[Actor, Depends(_user)], store_id: str):
        return JSONResponse(connections.get(actor, store_id))

    @router.delete("/stores/{store_id}/connection")
    def disconnect(actor: Annotated[Actor, Depends(_user)], store_id: str):
        return JSONResponse(connections.disconnect(actor, store_id))

    @router.post("/carts/sync")
    def sync_cart(actor: Annotated[Actor, Depends(_user)], body: CartSyncRequest):
        return JSONResponse(carts.sync(actor, body.mandate_id, body.quote_id))

    return router
