"""Request/response schemas for the local-only checkout adapter."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mandate.payments.models import (AuthorizationRefused, AuthorizationClaims, BudgetPeriod, PaymentCredential,
                                     PaymentDecision, PaymentRouteSummary, Reservation)


class DemoPurchaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mandate_id: str
    quote_id: str
    transaction_id: str = Field(min_length=1, max_length=128)
    payment_route_id: str | None = None
    approval_id: str | None = None


class DemoAuthorizationApproved(BaseModel):
    """A non-bearer view of the persisted approval: no token leaves the API."""
    model_config = ConfigDict(extra="forbid")
    decision_id: str
    transaction_id: str
    mandate_id: str
    mandate_version: int
    rule_ids: list[str]
    message: str
    evaluated_at: str
    event_sequence: int
    status: Literal["approved"]
    reservation: Reservation
    claims: AuthorizationClaims
    budgets: list[BudgetPeriod]
    payment_route: PaymentRouteSummary | None = None
    payment_credential: PaymentCredential | None = None


class DemoPurchaseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    authorization: DemoAuthorizationApproved | AuthorizationRefused
    payment: PaymentDecision | None
