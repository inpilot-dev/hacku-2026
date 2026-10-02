"""Wallet endpoints from contracts/openapi.json as one APIRouter.

The shared app (Noah) mounts this with ``app.include_router(build_router(wallet), prefix="/api/v1")``
and calls ``install_error_handlers(app)``. Role checks are dependencies, so a
blocked role gets 403 before its request body is even validated.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse

from . import models
from .auth import Actor, current_actor, require_role
from .service import Wallet

IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


def role(*roles: str):
    def dep(actor: Actor = Depends(current_actor)) -> Actor:
        return require_role(actor, *roles)
    return dep


UserOnly = Annotated[Actor, Depends(role("user"))]
AgentOnly = Annotated[Actor, Depends(role("agent"))]
UserOrAgent = Annotated[Actor, Depends(role("user", "agent"))]


def _out(model, status: int, body: dict) -> JSONResponse:
    """Validate the outgoing body against the contract model before sending it."""
    return JSONResponse(model.model_validate(body).model_dump(mode="json"), status_code=status)


def build_router(wallet: Wallet) -> APIRouter:
    router = APIRouter(tags=["wallet"])

    @router.post("/mandates/confirm", status_code=201, response_model=models.Mandate)
    def confirm_mandate(actor: UserOnly, key: IdempotencyKey, body: models.ConfirmRequest):
        status, out = wallet.confirm_mandate(actor, key, body.model_dump(mode="json"))
        return _out(models.Mandate, status, out)

    @router.get("/mandates/{mandate_id}", response_model=models.Mandate)
    def get_mandate(actor: UserOrAgent, mandate_id: str):
        return _out(models.Mandate, 200, wallet.get_mandate(actor, mandate_id))

    @router.post("/mandates/{mandate_id}/revoke", response_model=models.RevokeResponse)
    def revoke_mandate(actor: UserOnly, key: IdempotencyKey, mandate_id: str, body: models.RevokeRequest):
        status, out = wallet.revoke_mandate(actor, key, mandate_id, body.model_dump(mode="json", exclude_unset=True))
        return _out(models.RevokeResponse, status, out)

    @router.post("/quotes", status_code=201, response_model=models.Quote)
    def create_quote(actor: UserOrAgent, body: models.QuoteRequest):
        return _out(models.Quote, 201, wallet.create_quote(actor, body.model_dump(mode="json", exclude_unset=True)))

    @router.get("/quotes/{quote_id}", response_model=models.Quote)
    def get_quote(actor: UserOrAgent, quote_id: str):
        return _out(models.Quote, 200, wallet.get_quote(actor, quote_id))

    @router.post("/authorizations", response_model=models.AuthorizationDecision)
    def authorize(actor: AgentOnly, key: IdempotencyKey, body: models.AuthorizationRequest):
        status, out = wallet.authorize(actor, key, body.model_dump(mode="json"))
        model = models.AuthorizationApproved if out["status"] == "approved" else models.AuthorizationRefused
        return _out(model, status, out)

    @router.post("/payments", response_model=models.PaymentDecision)
    def pay(actor: AgentOnly, key: IdempotencyKey, body: models.PaymentRequest):
        status, out = wallet.pay(actor, key, body.model_dump(mode="json"))
        model = models.PaymentCompleted if out["status"] == "completed" else models.PaymentRefused
        return _out(model, status, out)

    @router.get("/payments/{transaction_id}", response_model=models.Receipt)
    def get_payment(actor: UserOrAgent, transaction_id: str):
        return _out(models.Receipt, 200, wallet.get_payment(actor, transaction_id))

    @router.post("/reservations/{reservation_id}/cancel", response_model=models.CancelResponse)
    def cancel_reservation(actor: UserOrAgent, key: IdempotencyKey, reservation_id: str, body: models.CancelRequest):
        status, out = wallet.cancel_reservation(actor, key, reservation_id,
                                                body.model_dump(mode="json", exclude_unset=True))
        return _out(models.CancelResponse, status, out)

    @router.get("/quotes/{quote_id}/payment-options", response_model=models.PaymentOptionsResponse)
    def payment_options(actor: UserOrAgent, quote_id: str):
        return _out(models.PaymentOptionsResponse, 200, wallet.payment_options(actor, quote_id))

    @router.post("/payments/{transaction_id}/refund", response_model=models.RefundResponse)
    def refund(actor: UserOnly, key: IdempotencyKey, transaction_id: str, body: models.RefundRequest):
        status, out = wallet.refund(actor, key, transaction_id, body.model_dump(mode="json", exclude_unset=True))
        return _out(models.RefundResponse, status, out)

    @router.get("/approvals", response_model=models.ApprovalList)
    def list_approvals(actor: UserOrAgent,
                       status: Literal["pending", "approved", "denied", "expired", "used"] | None = Query(None)):
        return _out(models.ApprovalList, 200, wallet.list_approvals(actor, status))

    @router.get("/approvals/{approval_id}", response_model=models.ApprovalRequest)
    def get_approval(actor: UserOrAgent, approval_id: str):
        return _out(models.ApprovalRequest, 200, wallet.get_approval(actor, approval_id))

    @router.post("/approvals/{approval_id}/approve", response_model=models.ApprovalDecisionResponse)
    def approve(actor: UserOnly, key: IdempotencyKey, approval_id: str, body: models.ApprovalDecisionRequest):
        status, out = wallet.decide_approval(actor, key, approval_id, True,
                                             body.model_dump(mode="json", exclude_unset=True))
        return _out(models.ApprovalDecisionResponse, status, out)

    @router.post("/approvals/{approval_id}/deny", response_model=models.ApprovalDecisionResponse)
    def deny(actor: UserOnly, key: IdempotencyKey, approval_id: str, body: models.ApprovalDecisionRequest):
        status, out = wallet.decide_approval(actor, key, approval_id, False,
                                             body.model_dump(mode="json", exclude_unset=True))
        return _out(models.ApprovalDecisionResponse, status, out)

    @router.get("/wallet/{mandate_id}", response_model=models.BudgetResponse)
    def get_budget(actor: UserOrAgent, mandate_id: str):
        return _out(models.BudgetResponse, 200, wallet.budget(actor, mandate_id))

    return router
