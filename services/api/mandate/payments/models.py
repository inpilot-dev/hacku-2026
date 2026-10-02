"""Pydantic mirrors of the wallet schemas in contracts/openapi.json (v0.1.0).

Requests forbid unknown fields and use strict integers so a float or string
amount is rejected rather than coerced. Responses are validated against these
models before they leave the service.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

Currency = Literal["HKD"]
ReasonCode = Literal[
    "ORDER_CAP_EXCEEDED", "PERIOD_BUDGET_EXCEEDED", "MERCHANT_NOT_ALLOWED", "CATEGORY_BLOCKED",
    "CATEGORY_REVIEW_REQUIRED", "MANDATE_NOT_ACTIVE", "MANDATE_EXPIRED", "MANDATE_REVOKED",
    "MANDATE_VERSION_CHANGED", "QUOTE_EXPIRED", "QUOTE_CHANGED", "RESERVATION_EXPIRED",
    "RESERVATION_CANCELLED", "AUTHORIZATION_INVALID", "AUTHORIZATION_EXPIRED",
    "TRANSACTION_CONFLICT", "APPROVAL_REQUIRED", "POLICY_NOT_NARROWER", "PARENT_MANDATE_INVALID",
]
Category = Literal[
    "produce", "dairy", "eggs", "meat", "seafood", "bakery", "pantry",
    "beverage_non_alcoholic", "alcohol", "household", "unknown",
]
CategoryStatus = Literal["curated", "verified", "unknown", "conflicting"]
PeriodName = Literal["calendar_week", "calendar_month"]
Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
NonNegInt = Annotated[StrictInt, Field(ge=0)]
PosInt = Annotated[StrictInt, Field(ge=1)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- requests ---------------------------------------------------------------

class PeriodLimit(Strict):
    period: PeriodName
    limit_minor: PosInt
    timezone: Literal["Asia/Hong_Kong"]


class Policy(Strict):
    currency: Currency
    per_order_limit_minor: PosInt
    period_limits: list[PeriodLimit] = Field(min_length=1)
    allowed_merchant_ids: list[StrictStr] = Field(min_length=1)
    blocked_categories: list[Category]
    expires_at: StrictStr
    approval_above_minor: NonNegInt | None


class ConfirmRequest(Strict):
    draft_id: StrictStr
    policy: Policy


class RevokeRequest(Strict):
    reason: StrictStr | None = None


class CancelRequest(Strict):
    reason: StrictStr | None = None


class QuoteItemRequest(Strict):
    product_id: StrictStr
    quantity: Annotated[StrictInt, Field(ge=1, le=100)]


class QuoteRequest(Strict):
    merchant_id: StrictStr
    items: list[QuoteItemRequest] = Field(min_length=1)
    delivery_context_id: StrictStr
    expected_revision: StrictStr | None = None


class AuthorizationRequest(Strict):
    transaction_id: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    mandate_id: StrictStr
    quote_id: StrictStr


class PaymentRequest(Strict):
    transaction_id: StrictStr
    quote_id: StrictStr
    authorization_token: StrictStr


# --- responses --------------------------------------------------------------

class Mandate(Strict):
    id: str
    owner_id: str
    delegatee_id: str
    parent_mandate_id: str | None
    version: PosInt
    status: Literal["active", "revoked", "expired"]
    policy: Policy
    created_at: str
    revoked_at: str | None


class RevokeResponse(Strict):
    mandate: Mandate
    cancelled_reservation_ids: list[str]
    event_sequence: PosInt


class QuoteItem(Strict):
    product_id: str
    title: str
    quantity: PosInt
    unit_price_minor: NonNegInt
    line_total_minor: NonNegInt
    category: Category
    category_status: CategoryStatus
    evidence_ids: list[str]


class Charge(Strict):
    kind: Literal["delivery", "other"]
    label: str
    amount_minor: NonNegInt
    evidence_ids: list[str] = Field(min_length=1)


class Quote(Strict):
    id: str
    merchant_id: str
    revision: str
    currency: Currency
    items: list[QuoteItem]
    subtotal_minor: NonNegInt
    charges: list[Charge]
    total_minor: NonNegInt
    basket_hash: Hash
    delivery_context_id: str
    data_mode: Literal["observed_snapshot", "observed_live"]
    evidence_ids: list[str]
    created_at: str
    expires_at: str


class RuleViolation(Strict):
    code: ReasonCode
    rule_id: str
    mandate_id: str
    message: str
    actual_minor: NonNegInt | None = None
    limit_minor: NonNegInt | None = None


class BudgetPeriod(Strict):
    mandate_id: str
    period_id: str
    period: PeriodName
    starts_at: str
    ends_at: str
    currency: Currency
    limit_minor: PosInt
    paid_minor: NonNegInt
    reserved_minor: NonNegInt
    available_minor: NonNegInt
    version: NonNegInt


class BudgetResponse(Strict):
    mandate_id: str
    applicable_budgets: list[BudgetPeriod]
    server_time: str


class Reservation(Strict):
    id: str
    transaction_id: str
    mandate_id: str
    mandate_version: PosInt
    quote_id: str
    basket_hash: Hash
    amount_minor: NonNegInt
    currency: Currency
    status: Literal["reserved", "paid", "cancelled", "expired"]
    expires_at: str
    affected_period_ids: list[str]


class AuthorizationClaims(Strict):
    transaction_id: str
    reservation_id: str
    mandate_id: str
    mandate_version: PosInt
    quote_id: str
    basket_hash: Hash
    merchant_id: str
    amount_minor: NonNegInt
    currency: Currency
    audience: Literal["mandate-payment-sandbox"]
    issued_at: str
    expires_at: str
    token_id: str


class _DecisionBase(Strict):
    decision_id: str
    transaction_id: str
    mandate_id: str
    mandate_version: PosInt
    rule_ids: list[str]
    message: str
    evaluated_at: str
    event_sequence: PosInt


class AuthorizationApproved(_DecisionBase):
    status: Literal["approved"]
    reservation: Reservation
    authorization_token: str
    claims: AuthorizationClaims
    budgets: list[BudgetPeriod]


class AuthorizationRefused(_DecisionBase):
    status: Literal["refused", "requires_review"]
    violations: list[RuleViolation] = Field(min_length=1)
    budgets: list[BudgetPeriod]


AuthorizationDecision = Union[AuthorizationApproved, AuthorizationRefused]


class Receipt(Strict):
    id: str
    transaction_id: str
    reservation_id: str
    mandate_id: str
    quote_id: str
    merchant_id: str
    basket_hash: Hash
    amount_minor: NonNegInt
    currency: Currency
    payment_mode: Literal["sandbox"]
    status: Literal["paid"]
    paid_at: str


class PaymentCompleted(Strict):
    status: Literal["completed"]
    decision_id: str
    receipt: Receipt
    replayed: StrictBool
    event_sequence: PosInt


class PaymentRefused(Strict):
    status: Literal["refused"]
    decision_id: str
    transaction_id: str
    violations: list[RuleViolation] = Field(min_length=1)
    message: str
    event_sequence: PosInt


PaymentDecision = Union[PaymentCompleted, PaymentRefused]


class CancelResponse(Strict):
    reservation: Reservation
    released_minor: NonNegInt
    event_sequence: PosInt
