"""Answer one approval request with the decision token from the approval webhook.

These routes are for a receiver outside the app, such as the voice-call service. They
are not part of contracts/openapi.json; the app keeps using /approvals/{id}/approve|deny
with the owner's token.

The bearer token here is a decision token (approval_notify.decision_token), not a user
token. It names its approval, so no route takes an approval ID: a token can only ever
read and decide the approval it was minted for, and only while that approval is open.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header

from . import models
from .approval_notify import PURPOSE
from .auth import Actor
from .errors import unauthenticated
from .routes import IdempotencyKey, _out
from .service import Wallet
from .signing import APPROVAL_AUDIENCE, TokenExpired, TokenInvalid


class ApprovalWithQuote(models.Strict):
    """What the receiver reads back before a decision: the request and the exact basket it covers."""
    approval: models.ApprovalRequest
    quote: models.Quote


DEFAULT_NOTE = {True: "Approved outside the app with a decision token.",
                False: "Declined outside the app with a decision token."}


def build_approval_decision_router(wallet: Wallet) -> APIRouter:
    router = APIRouter(tags=["approval decisions"])

    def decision_claims(authorization: str | None = Header(default=None)) -> dict:
        if not authorization or not authorization.startswith("Bearer "):
            raise unauthenticated()
        try:
            claims = wallet.signer.verify(authorization.removeprefix("Bearer ").strip(), wallet.clock.now(),
                                          audience=APPROVAL_AUDIENCE)
        except TokenExpired:
            raise unauthenticated("This decision token has expired.") from None
        except TokenInvalid:
            raise unauthenticated("Invalid decision token.") from None
        if claims.get("purpose") != PURPOSE or not claims.get("approval_id") or not claims.get("owner_id"):
            raise unauthenticated("Invalid decision token.")
        return claims

    def owner(claims: dict) -> Actor:
        return Actor(actor_id=claims["owner_id"], role="user")

    @router.get("/approval-decision", response_model=ApprovalWithQuote)
    def read(claims: dict = Depends(decision_claims)):
        actor = owner(claims)
        approval = wallet.get_approval(actor, claims["approval_id"])
        return _out(ApprovalWithQuote, 200,
                    {"approval": approval, "quote": wallet.get_quote(actor, approval["quote_id"])})

    def decide(claims: dict, key: str, body: models.ApprovalDecisionRequest, approve: bool):
        req = body.model_dump(mode="json", exclude_unset=True)
        req.setdefault("note", DEFAULT_NOTE[approve])
        status, out = wallet.decide_approval(owner(claims), key, claims["approval_id"], approve, req)
        return _out(models.ApprovalDecisionResponse, status, out)

    @router.post("/approval-decision/approve", response_model=models.ApprovalDecisionResponse)
    def approve(key: IdempotencyKey, body: models.ApprovalDecisionRequest,
                claims: dict = Depends(decision_claims)):
        return decide(claims, key, body, True)

    @router.post("/approval-decision/deny", response_model=models.ApprovalDecisionResponse)
    def deny(key: IdempotencyKey, body: models.ApprovalDecisionRequest,
             claims: dict = Depends(decision_claims)):
        return decide(claims, key, body, False)

    return router
