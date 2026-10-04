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
    # v0.2 additions
    "VELOCITY_LIMIT_EXCEEDED", "APPROVAL_DENIED", "APPROVAL_EXPIRED",
    # v0.3 additions
    "RISK_REVIEW_REQUIRED", "CARD_FROZEN",
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


class VelocityLimit(Strict):
    max_purchases: Annotated[StrictInt, Field(ge=1, le=1000)]
    window_minutes: Annotated[StrictInt, Field(ge=1, le=10080)]


class Policy(Strict):
    currency: Currency
    per_order_limit_minor: PosInt
    period_limits: list[PeriodLimit] = Field(min_length=1)
    allowed_merchant_ids: list[StrictStr] = Field(min_length=1)
    blocked_categories: list[Category]
    expires_at: StrictStr
    approval_above_minor: NonNegInt | None
    velocity_limit: VelocityLimit | None = None
    risk_review: StrictBool = False
    web_purchases: StrictBool = False


class ConfirmRequest(Strict):
    draft_id: StrictStr
    policy: Policy


class RevokeRequest(Strict):
    reason: Annotated[StrictStr, Field(max_length=500)] | None = None


class CancelRequest(Strict):
    reason: Annotated[StrictStr, Field(max_length=500)] | None = None


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
    payment_route_id: StrictStr | None = None


class PaymentRequest(Strict):
    transaction_id: StrictStr
    quote_id: StrictStr
    authorization_token: StrictStr


class ApprovalDecisionRequest(Strict):
    note: Annotated[StrictStr, Field(max_length=500)] | None = None


class RefundRequest(Strict):
    reason: Annotated[StrictStr, Field(max_length=500)] | None = None


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
    purpose: str | None = None
    payment_route_id: str | None = None


class PaymentRouteSummary(Strict):
    route_id: str
    label: str
    network: str | None
    rail: str
    fee_minor: NonNegInt
    reward_minor: NonNegInt
    net_minor: NonNegInt
    rank: PosInt | None = None
    recommended_route_id: str | None = None
    rule: str | None = None
    caveats: list[str] = []


class PaymentCredential(Strict):
    credential_id: str
    rail: str
    network: str | None
    last4: str | None
    merchant_id: str
    amount_minor: NonNegInt
    currency: Currency
    expires_at: str
    single_use: StrictBool
    holds_funds_at_rail: StrictBool
    purpose: str


class PaymentOption(Strict):
    route_id: str
    label: str
    provider: str
    network: str | None
    rail: str
    holds_funds_at_rail: StrictBool
    settlement: str
    eligible: StrictBool
    ineligible_reason: str | None
    gross_minor: NonNegInt
    fee_minor: NonNegInt
    reward_minor: NonNegInt
    net_minor: NonNegInt
    reward_counted: StrictBool
    rank: PosInt | None
    evidence_ids: list[str]
    caveats: list[str]


class RouteEvidence(Strict):
    id: str
    title: str
    url: str
    observed_at: str
    quote: str


class PaymentOptionsResponse(Strict):
    quote_id: str
    currency: Currency
    total_minor: NonNegInt
    rule: str
    recommended_route_id: str | None
    options: list[PaymentOption]
    evidence: list[RouteEvidence]
    evaluated_at: str


class RiskSignal(Strict):
    check: str
    points: NonNegInt


class RiskAssessment(Strict):
    score: NonNegInt
    threshold: PosInt
    signals: list[RiskSignal]


class _DecisionBase(Strict):
    decision_id: str
    transaction_id: str
    mandate_id: str
    mandate_version: PosInt
    rule_ids: list[str]
    message: str
    evaluated_at: str
    event_sequence: PosInt
    risk_assessment: RiskAssessment | None = None


class AuthorizationApproved(_DecisionBase):
    status: Literal["approved"]
    reservation: Reservation
    authorization_token: str
    claims: AuthorizationClaims
    budgets: list[BudgetPeriod]
    payment_route: PaymentRouteSummary | None = None
    payment_credential: PaymentCredential | None = None


class ApprovalRequest(Strict):
    id: str
    transaction_id: str
    mandate_id: str
    quote_id: str
    merchant_id: str
    basket_hash: Hash
    amount_minor: NonNegInt
    currency: Currency
    reasons: list[RuleViolation]
    status: Literal["pending", "approved", "denied", "expired", "used"]
    created_at: str
    expires_at: str
    decided_at: str | None
    decided_by: str | None
    note: str | None


class ApprovalList(Strict):
    approvals: list[ApprovalRequest]
    server_time: str


class ApprovalDecisionResponse(Strict):
    approval: ApprovalRequest
    event_sequence: PosInt


class AuthorizationRefused(_DecisionBase):
    status: Literal["refused", "requires_review"]
    violations: list[RuleViolation] = Field(min_length=1)
    budgets: list[BudgetPeriod]
    approval_request: ApprovalRequest | None = None


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
    payment_route: PaymentRouteSummary | None = None


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


class Refund(Strict):
    id: str
    transaction_id: str
    amount_minor: NonNegInt
    currency: Currency
    reward_reversed_minor: NonNegInt
    payment_route_id: str | None
    reason: str | None
    status: Literal["refunded"]
    refunded_at: str


class RefundResponse(Strict):
    refund: Refund
    budgets: list[BudgetPeriod]
    event_sequence: PosInt


# v0.3 virtual cards (wallet, Timmy)

class CardControls(Strict):
    spend_limit_minor: NonNegInt
    currency: Currency
    allowed_merchant_ids: list[str] | None
    blocked_mccs: list[str]
    single_use: StrictBool
    expires_at: str


class SingleUseCardCounts(Strict):
    active: NonNegInt
    used: NonNegInt
    cancelled: NonNegInt


class VirtualCard(Strict):
    card_id: str
    mandate_id: str
    parent_card_id: str | None
    usage: Literal["mandate", "single_use"]
    network: Literal["mastercard", "visa"]
    last4: Annotated[str, Field(pattern=r"^[0-9]{4}$")]
    exp_month: Annotated[int, Field(ge=1, le=12)]
    exp_year: PosInt
    status: Literal["active", "frozen", "used", "cancelled"]
    controls: CardControls
    issued_at: str
    status_changed_at: str | None
    payment_mode: Literal["sandbox"]
    single_use_cards: SingleUseCardCounts


class CardFreezeRequest(Strict):
    reason: Annotated[StrictStr, Field(max_length=500)] | None = None


class CardStatusResponse(Strict):
    card: VirtualCard
    event_sequence: PosInt


class CardAuthorization(Strict):
    id: str
    card_id: str
    card_last4: str
    card_usage: Literal["mandate", "single_use"]
    merchant_id: str
    mcc: str | None
    amount_minor: NonNegInt
    currency: Currency
    approved: StrictBool
    response_code: str
    decline_reason: str | None
    message: str
    reservation_id: str | None
    created_at: str


class CardAuthorizationList(Strict):
    card_id: str
    authorizations: list[CardAuthorization]
