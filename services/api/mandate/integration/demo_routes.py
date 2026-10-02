"""Narrow local checkout adapter for the browser demonstration.

This is deliberately not a general actor proxy. A user can only submit their
own mandate and quote; the wallet resolves its assigned delegatee and applies
its ordinary authorize/pay checks. The agent token and signed capability stay
inside this process.
"""
from __future__ import annotations

import hashlib
import os

from fastapi import APIRouter, Depends

from .demo_models import DemoPurchaseRequest, DemoPurchaseResponse
from mandate.payments.auth import Actor, current_actor, require_role
from mandate.payments.errors import conflict, forbidden
from mandate.payments.service import Wallet


def build_demo_router(wallet: Wallet) -> APIRouter:
    router = APIRouter(tags=["interface"])

    @router.post("/demo/purchases", response_model=DemoPurchaseResponse)
    def run_demo_purchase(body: DemoPurchaseRequest,
                          actor: Actor = Depends(current_actor)):
        require_role(actor, "user")
        if os.environ.get("MANDATE_ENABLE_DEMO_CHECKOUT") != "1":
            raise forbidden("Local demo checkout is disabled.")

        # These lookups enforce ownership/family scope before we derive the
        # fixed delegatee. The client cannot choose a role or an agent ID.
        mandate = wallet.get_mandate(actor, body.mandate_id)
        wallet.get_quote(actor, body.quote_id)
        agent = Actor(actor_id=mandate["delegatee_id"], role="agent", owner_id=actor.actor_id)
        digest = hashlib.sha256(body.transaction_id.encode("utf-8")).hexdigest()
        if body.approval_id:
            approval = wallet.get_approval(actor, body.approval_id)
            if approval["transaction_id"] != body.transaction_id or approval["status"] != "approved":
                raise conflict("This approval does not authorize the saved transaction.")
        auth_attempt = body.approval_id or "initial"
        attempt_digest = hashlib.sha256(f"{body.transaction_id}:{auth_attempt}".encode("utf-8")).hexdigest()
        auth_key = f"demo-auth-{attempt_digest}"
        payment_key = f"demo-pay-{digest}"
        _, authorization = wallet.authorize(agent, auth_key, {
            "transaction_id": body.transaction_id,
            "mandate_id": body.mandate_id,
            "quote_id": body.quote_id,
            "payment_route_id": body.payment_route_id,
        })
        # The browser needs the decision and receipt, never bearer capabilities
        # or single-use rail credentials. Keep both secrets process-local.
        authorization = {
            key: value for key, value in authorization.items()
            if key not in {"authorization_token", "payment_credential"}
        }
        payment = None
        if authorization["status"] == "approved":
            # Rehydrate only the short-lived token from the persisted claims;
            # Wallet.pay still rechecks signature, revocation, version, quote
            # freshness, reservation state, expiry, and every budget.
            full_authorization = wallet.signer.sign(authorization["claims"])
            _, payment = wallet.pay(agent, payment_key, {
                "transaction_id": body.transaction_id,
                "quote_id": body.quote_id,
                "authorization_token": full_authorization,
            })
        return {"authorization": authorization, "payment": payment}

    return router
