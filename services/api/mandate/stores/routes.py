"""Store connections and real-cart sync (proposed contract additions, user role).

    GET    /stores                       every supported store and its connection status
    POST   /stores/{store_id}/connect    open the shop's sign-in in the store window (202)
    POST   /stores/{store_id}/login/ticket   single-use ticket for the sign-in stream
    WS     /stores/{store_id}/login/stream?ticket=...   the sign-in tab, relayed (stream.py)
    GET    /stores/{store_id}            status; finishes a sign-in once the shop logs the user in
    DELETE /stores/{store_id}/connection forget the saved session
    POST   /carts/sync                   fill the store cart with a quote; never checks out
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictStr

from mandate.payments.auth import Actor, current_actor, require_role

from .carts import CartSync
from .connections import StoreConnections
from .registry import STORES
from .steel import StoreBrowserError
from .stream import relay_login


class CartSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mandate_id: StrictStr
    quote_id: StrictStr


def _user(actor: Actor = Depends(current_actor)) -> Actor:
    return require_role(actor, "user")


def build_store_router(connections: StoreConnections, carts: CartSync, relay=relay_login) -> APIRouter:
    router = APIRouter(tags=["stores"])

    @router.get("/stores")
    def list_stores(actor: Annotated[Actor, Depends(_user)]):
        return JSONResponse(connections.list(actor))

    @router.post("/stores/{store_id}/connect", status_code=202)
    def connect(actor: Annotated[Actor, Depends(_user)], store_id: str):
        return JSONResponse(connections.start(actor, store_id), status_code=202)

    @router.post("/stores/{store_id}/login/ticket")
    def login_ticket(actor: Annotated[Actor, Depends(_user)], store_id: str):
        return JSONResponse(connections.issue_ticket(actor, store_id))

    @router.websocket("/stores/{store_id}/login/stream")
    async def login_stream(websocket: WebSocket, store_id: str, ticket: str = ""):
        redeemed = connections.redeem_ticket(ticket, store_id)
        if redeemed is None:
            await websocket.close(code=4401, reason="Invalid or used ticket.")
            return
        user_id, window = redeemed
        actor = Actor(actor_id=user_id, role="user")
        await websocket.accept()

        async def receive():
            try:
                return json.loads(await websocket.receive_text())
            except WebSocketDisconnect:
                return None
            except ValueError:
                return {}  # ignored by the relay

        async def finished():
            view = await run_in_threadpool(connections.get, actor, store_id)
            return None if view["status"] == "awaiting_login" else view["status"]

        try:
            await relay(window, STORES[store_id], websocket.send_json, receive, finished)
        except WebSocketDisconnect:
            return  # the user closed the store window; the sign-in stays open until it times out
        except (StoreBrowserError, OSError):
            try:
                await websocket.send_json({"type": "error", "text": "The store window could not be shown. Try Connect again."})
            except (WebSocketDisconnect, RuntimeError):
                pass
        try:
            await websocket.close()
        except (RuntimeError, WebSocketDisconnect):
            pass  # the client already closed

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
