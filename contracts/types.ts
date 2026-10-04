// GENERATED FROM openapi.json. Change the OpenAPI source, then regenerate; do not edit these definitions independently.
// Runtime validation is still required. Monetary numbers are integer HKD cents.

export type Currency = "HKD";

export type ReasonCode = "ORDER_CAP_EXCEEDED" | "PERIOD_BUDGET_EXCEEDED" | "MERCHANT_NOT_ALLOWED" | "CATEGORY_BLOCKED" | "CATEGORY_REVIEW_REQUIRED" | "MANDATE_NOT_ACTIVE" | "MANDATE_EXPIRED" | "MANDATE_REVOKED" | "MANDATE_VERSION_CHANGED" | "QUOTE_EXPIRED" | "QUOTE_CHANGED" | "RESERVATION_EXPIRED" | "RESERVATION_CANCELLED" | "AUTHORIZATION_INVALID" | "AUTHORIZATION_EXPIRED" | "TRANSACTION_CONFLICT" | "APPROVAL_REQUIRED" | "POLICY_NOT_NARROWER" | "PARENT_MANDATE_INVALID" | "VELOCITY_LIMIT_EXCEEDED" | "APPROVAL_DENIED" | "APPROVAL_EXPIRED" | "RISK_REVIEW_REQUIRED" | "CARD_FROZEN";

export type Evidence = {
  "id": string;
  "source_url": string;
  "observed_at": string;
  "kind": "product_price" | "delivery_fee" | "merchant_identity" | "category";
  "capture_path": string | null;
  "conditions": string;
};

export type Category = "produce" | "dairy" | "eggs" | "meat" | "seafood" | "bakery" | "pantry" | "beverage_non_alcoholic" | "alcohol" | "household" | "unknown";

export type Product = {
  "id": string;
  "merchant_id": string;
  "title": string;
  "description": string;
  "category": Category;
  "category_status": "curated" | "verified" | "unknown" | "conflicting";
  "unit_label": string;
  "unit_price_minor": number;
  "currency": Currency;
  "available": boolean;
  "evidence_ids": Array<string>;
};

export type CatalogResponse = {
  "products": Array<Product>;
  "evidence": Array<Evidence>;
};

export type PeriodLimit = {
  "period": "calendar_week" | "calendar_month";
  "limit_minor": number;
  "timezone": string;
};

export type Policy = {
  "currency": string;
  "per_order_limit_minor": number;
  "period_limits": Array<PeriodLimit>;
  "allowed_merchant_ids": Array<string>;
  "blocked_categories": Array<"produce" | "dairy" | "eggs" | "meat" | "seafood" | "bakery" | "pantry" | "beverage_non_alcoholic" | "alcohol" | "household" | "unknown">;
  "expires_at": string;
  "approval_above_minor": number | null;
  "velocity_limit"?: VelocityLimit | null;
  "risk_review"?: boolean;
  "web_purchases"?: boolean;
};

export type DraftRequest = {
  "text": string;
  "delegatee_id": string;
  "parent_mandate_id"?: string | null;
};

export type Ambiguity = {
  "field": string;
  "question": string;
};

export type DraftResponse = {
  "draft_id": string;
  "owner_id": string;
  "delegatee_id": string;
  "parent_mandate_id": string | null;
  "proposed_policy": Policy | null;
  "ambiguities": Array<Ambiguity>;
  "summary": string;
  "expires_at": string;
};

export type ConfirmRequest = {
  "draft_id": string;
  "policy": Policy;
};

export type Mandate = {
  "id": string;
  "owner_id": string;
  "delegatee_id": string;
  "parent_mandate_id": string | null;
  "version": number;
  "status": "active" | "revoked" | "expired";
  "policy": Policy;
  "created_at": string;
  "revoked_at": string | null;
};

export type RevokeRequest = {
  "reason"?: string;
};

export type RevokeResponse = {
  "mandate": Mandate;
  "cancelled_reservation_ids": Array<string>;
  "event_sequence": number;
};

export type ShoppingItem = {
  "name": string;
  "quantity": number;
  "unit"?: string | null;
};

export type AgentRunRequest = {
  "mandate_id": string;
  "shopping_list": Array<ShoppingItem>;
  "instruction"?: string | null;
  "auto_purchase"?: boolean;
};

export type AgentRun = {
  "id": string;
  "mandate_id": string;
  "status": "queued" | "running" | "awaiting_review" | "quoted" | "completed" | "refused" | "failed";
  "provider": "jev" | "fallback" | "scripted";
  "model_id": string | null;
  "execution_mode": "local" | "cloud" | "scripted";
  "quote_id": string | null;
  "transaction_id": string | null;
  "payment_id": string | null;
  "latest_decision_id": string | null;
  "message": string;
  "created_at": string;
  "updated_at": string;
  "comparisons"?: Array<AgentComparison>;
};

export type QuoteItemRequest = {
  "product_id": string;
  "quantity": number;
};

export type QuoteRequest = {
  "merchant_id": string;
  "items": Array<QuoteItemRequest>;
  "delivery_context_id": string;
  "expected_revision"?: string | null;
};

export type QuoteItem = {
  "product_id": string;
  "title": string;
  "quantity": number;
  "unit_price_minor": number;
  "line_total_minor": number;
  "category": Category;
  "category_status": "curated" | "verified" | "unknown" | "conflicting";
  "evidence_ids": Array<string>;
};

export type Charge = {
  "kind": "delivery" | "other";
  "label": string;
  "amount_minor": number;
  "evidence_ids": Array<string>;
};

export type Quote = {
  "id": string;
  "merchant_id": string;
  "revision": string;
  "currency": Currency;
  "items": Array<QuoteItem>;
  "subtotal_minor": number;
  "charges": Array<Charge>;
  "total_minor": number;
  "basket_hash": string;
  "delivery_context_id": string;
  "data_mode": "observed_snapshot" | "observed_live";
  "evidence_ids": Array<string>;
  "created_at": string;
  "expires_at": string;
};

export type AuthorizationRequest = {
  "transaction_id": string;
  "mandate_id": string;
  "quote_id": string;
  "payment_route_id"?: string | null;
};

export type RuleViolation = {
  "code": ReasonCode;
  "rule_id": string;
  "mandate_id": string;
  "message": string;
  "actual_minor"?: number | null;
  "limit_minor"?: number | null;
};

export type BudgetPeriod = {
  "mandate_id": string;
  "period_id": string;
  "period": "calendar_week" | "calendar_month";
  "starts_at": string;
  "ends_at": string;
  "currency": Currency;
  "limit_minor": number;
  "paid_minor": number;
  "reserved_minor": number;
  "available_minor": number;
  "version": number;
};

export type BudgetResponse = {
  "mandate_id": string;
  "applicable_budgets": Array<BudgetPeriod>;
  "server_time": string;
};

export type Reservation = {
  "id": string;
  "transaction_id": string;
  "mandate_id": string;
  "mandate_version": number;
  "quote_id": string;
  "basket_hash": string;
  "amount_minor": number;
  "currency": Currency;
  "status": "reserved" | "paid" | "cancelled" | "expired";
  "expires_at": string;
  "affected_period_ids": Array<string>;
};

export type AuthorizationClaims = {
  "transaction_id": string;
  "reservation_id": string;
  "mandate_id": string;
  "mandate_version": number;
  "quote_id": string;
  "basket_hash": string;
  "merchant_id": string;
  "amount_minor": number;
  "currency": Currency;
  "audience": "mandate-payment-sandbox";
  "issued_at": string;
  "expires_at": string;
  "token_id": string;
  "purpose"?: string | null;
  "payment_route_id"?: string | null;
};

export type AuthorizationApproved = {
  "decision_id": string;
  "transaction_id": string;
  "mandate_id": string;
  "mandate_version": number;
  "rule_ids": Array<string>;
  "message": string;
  "evaluated_at": string;
  "event_sequence": number;
  "status": "approved";
  "reservation": Reservation;
  "authorization_token": string;
  "claims": AuthorizationClaims;
  "budgets": Array<BudgetPeriod>;
  "payment_route"?: PaymentRouteSummary | null;
  "payment_credential"?: PaymentCredential | null;
  "risk_assessment"?: RiskAssessment | null;
};

export type AuthorizationRefused = {
  "decision_id": string;
  "transaction_id": string;
  "mandate_id": string;
  "mandate_version": number;
  "rule_ids": Array<string>;
  "message": string;
  "evaluated_at": string;
  "event_sequence": number;
  "status": "refused" | "requires_review";
  "violations": Array<RuleViolation>;
  "budgets": Array<BudgetPeriod>;
  "approval_request"?: ApprovalRequest | null;
  "risk_assessment"?: RiskAssessment | null;
};

export type AuthorizationDecision = AuthorizationApproved | AuthorizationRefused;

export type PaymentRequest = {
  "transaction_id": string;
  "quote_id": string;
  "authorization_token": string;
};

export type Receipt = {
  "id": string;
  "transaction_id": string;
  "reservation_id": string;
  "mandate_id": string;
  "quote_id": string;
  "merchant_id": string;
  "basket_hash": string;
  "amount_minor": number;
  "currency": Currency;
  "payment_mode": "sandbox";
  "status": "paid";
  "paid_at": string;
  "payment_route"?: PaymentRouteSummary | null;
};

export type PaymentCompleted = {
  "status": "completed";
  "decision_id": string;
  "receipt": Receipt;
  "replayed": boolean;
  "event_sequence": number;
};

export type PaymentRefused = {
  "status": "refused";
  "decision_id": string;
  "transaction_id": string;
  "violations": Array<RuleViolation>;
  "message": string;
  "event_sequence": number;
};

export type PaymentDecision = PaymentCompleted | PaymentRefused;

export type CancelRequest = {
  "reason"?: string;
};

export type CancelResponse = {
  "reservation": Reservation;
  "released_minor": number;
  "event_sequence": number;
};

export type Error = {
  "error": {
  "code": "INVALID_REQUEST" | "UNAUTHENTICATED" | "FORBIDDEN" | "NOT_FOUND" | "IDEMPOTENCY_CONFLICT" | "STATE_CONFLICT" | "SERVICE_BUSY" | "MODEL_UNAVAILABLE" | "INTERNAL_ERROR";
  "message": string;
  "request_id": string;
  "retryable": boolean;
  "details": Record<string, unknown>;
};
};

export type Health = {
  "status": "ok";
  "api_version": "0.1.0";
  "server_time": string;
  "payment_mode": "sandbox";
};

export type AuditEvent = {
  "stream_id": string;
  "sequence": number;
  "event_id": string;
  "type": "mandate_confirmed" | "mandate_revoked" | "quote_created" | "authorization_approved" | "authorization_refused" | "payment_completed" | "payment_refused" | "reservation_cancelled" | "reservation_expired" | "agent_run_updated" | "card_frozen" | "card_unfrozen";
  "occurred_at": string;
  "actor_id": string;
  "mandate_id": string | null;
  "transaction_id": string | null;
  "payload": Record<string, unknown>;
  "previous_hash": string;
  "event_hash": string;
};

export type EventsResponse = {
  "events": Array<AuditEvent>;
  "next_after": number;
  "has_more": boolean;
};

export type Checkpoint = {
  "stream_id": string;
  "sequence": number;
  "event_hash": string;
  "key_id": string;
  "created_at": string;
  "signature": string;
};

export type AuditExport = {
  "format_version": "0.1.0";
  "stream_id": string;
  "events": Array<AuditEvent>;
  "latest_checkpoint": Checkpoint | null;
  "public_key_id": string;
};

export type CheckpointRequest = {
  "stream_id": string;
};

export type VerifierRequest = {
  "export": AuditExport;
  "retained_checkpoint_id": string;
};

export type VerifierResult = {
  "status": "valid_through_checkpoint" | "invalid" | "no_trusted_checkpoint";
  "valid": boolean;
  "checked_through_sequence": number;
  "export_last_sequence": number;
  "unanchored_event_count": number;
  "failures": Array<{
  "code": "HASH_MISMATCH" | "SEQUENCE_GAP" | "CHECKPOINT_MISMATCH" | "SIGNATURE_INVALID" | "TRUNCATED_BEFORE_CHECKPOINT" | "STREAM_MISMATCH";
  "sequence": number | null;
  "message": string;
}>;
  "message": string;
};

export type VerificationRequest = {
  "variant": "unsafe" | "atomic";
  "max_steps"?: number;
  "initial_available_minor": number;
  "purchase_amounts_minor": Array<number>;
  "timeout_ms"?: number;
};

export type ModelStep = {
  "step": number;
  "actor": string;
  "action": string;
  "paid_minor": number;
  "reserved_minor": number;
  "remaining_minor": number;
  "explanation": string;
};

export type VerificationResult = {
  "id": string;
  "variant": "unsafe" | "atomic";
  "status": "counterexample_found" | "no_counterexample_within_bound" | "inconclusive";
  "solver_result": "sat" | "unsat" | "unknown" | "timeout";
  "max_steps": number;
  "transaction_count": number;
  "runtime_ms": number;
  "assumptions": Array<string>;
  "checked_properties": Array<string>;
  "counterexample": Array<ModelStep>;
  "message": string;
};

export type DemoPurchaseRequest = {
  "mandate_id": string;
  "quote_id": string;
  "transaction_id": string;
  "payment_route_id"?: string | null;
  "approval_id"?: string | null;
};

export type DemoPurchaseResponse = {
  "authorization": DemoAuthorizationApproved | AuthorizationRefused;
  "payment": PaymentCompleted | PaymentRefused | null;
};

export type DemoAuthorizationApproved = {
  "decision_id": string;
  "transaction_id": string;
  "mandate_id": string;
  "mandate_version": number;
  "rule_ids": Array<string>;
  "message": string;
  "evaluated_at": string;
  "event_sequence": number;
  "status": "approved";
  "reservation": Reservation;
  "claims": AuthorizationClaims;
  "budgets": Array<BudgetPeriod>;
  "payment_route"?: PaymentRouteSummary | null;
  "risk_assessment"?: RiskAssessment | null;
};

export type VelocityLimit = {
  "max_purchases": number;
  "window_minutes": number;
};

export type PaymentRouteSummary = {
  "route_id": string;
  "label": string;
  "network": string | null;
  "rail": string;
  "fee_minor": number;
  "reward_minor": number;
  "net_minor": number;
  "rank"?: number | null;
  "recommended_route_id"?: string | null;
  "rule"?: string | null;
  "caveats"?: Array<string>;
};

export type PaymentCredential = {
  "credential_id": string;
  "rail": string;
  "network": string | null;
  "last4": string | null;
  "merchant_id": string;
  "amount_minor": number;
  "currency": Currency;
  "expires_at": string;
  "single_use": boolean;
  "holds_funds_at_rail": boolean;
  "purpose": string;
};

export type PaymentOption = {
  "route_id": string;
  "label": string;
  "provider": string;
  "network": string | null;
  "rail": string;
  "holds_funds_at_rail": boolean;
  "settlement": string;
  "eligible": boolean;
  "ineligible_reason": string | null;
  "gross_minor": number;
  "fee_minor": number;
  "reward_minor": number;
  "net_minor": number;
  "reward_counted": boolean;
  "rank": number | null;
  "evidence_ids": Array<string>;
  "caveats": Array<string>;
};

export type RouteEvidence = {
  "id": string;
  "title": string;
  "url": string;
  "observed_at": string;
  "quote": string;
};

export type PaymentOptionsResponse = {
  "quote_id": string;
  "currency": Currency;
  "total_minor": number;
  "rule": string;
  "recommended_route_id": string | null;
  "options": Array<PaymentOption>;
  "evidence": Array<RouteEvidence>;
  "evaluated_at": string;
};

export type ApprovalRequest = {
  "id": string;
  "transaction_id": string;
  "mandate_id": string;
  "quote_id": string;
  "merchant_id": string;
  "basket_hash": string;
  "amount_minor": number;
  "currency": Currency;
  "reasons": Array<RuleViolation>;
  "status": "pending" | "approved" | "denied" | "expired" | "used";
  "created_at": string;
  "expires_at": string;
  "decided_at": string | null;
  "decided_by": string | null;
  "note": string | null;
};

export type ApprovalList = {
  "approvals": Array<ApprovalRequest>;
  "server_time": string;
};

export type ApprovalDecisionRequest = {
  "note"?: string | null;
};

export type ApprovalDecisionResponse = {
  "approval": ApprovalRequest;
  "event_sequence": number;
};

export type RefundRequest = {
  "reason"?: string | null;
};

export type Refund = {
  "id": string;
  "transaction_id": string;
  "amount_minor": number;
  "currency": Currency;
  "reward_reversed_minor": number;
  "payment_route_id": string | null;
  "reason": string | null;
  "status": "refunded";
  "refunded_at": string;
};

export type RefundResponse = {
  "refund": Refund;
  "budgets": Array<BudgetPeriod>;
  "event_sequence": number;
};

export type ShoppingListParseRequest = {
  "text": string;
};

export type ShoppingListParseResponse = {
  "items": Array<ShoppingItem>;
  "source": "model" | "rules";
  "model_id": string | null;
  "note": string;
};

export type TranscriptionRequest = {
  "audio_base64": string;
  "format": "ogg" | "webm" | "wav" | "mp3" | "m4a" | "aac" | "flac";
};

export type TranscriptionResponse = {
  "text": string;
  "model_id": string;
};

export type CardControls = {
  "spend_limit_minor": number;
  "currency": Currency;
  "allowed_merchant_ids": Array<string> | null;
  "blocked_mccs": Array<string>;
  "single_use": boolean;
  "expires_at": string;
};

export type VirtualCard = {
  "card_id": string;
  "mandate_id": string;
  "parent_card_id": string | null;
  "usage": "mandate" | "single_use";
  "network": "mastercard" | "visa";
  "last4": string;
  "exp_month": number;
  "exp_year": number;
  "status": "active" | "frozen" | "used" | "cancelled";
  "controls": CardControls;
  "issued_at": string;
  "status_changed_at": string | null;
  "payment_mode": "sandbox";
  "single_use_cards": {
  "active": number;
  "used": number;
  "cancelled": number;
};
};

export type CardFreezeRequest = {
  "reason"?: string;
};

export type CardStatusResponse = {
  "card": VirtualCard;
  "event_sequence": number;
};

export type CardAuthorization = {
  "id": string;
  "card_id": string;
  "card_last4": string;
  "card_usage": "mandate" | "single_use";
  "merchant_id": string;
  "mcc": string | null;
  "amount_minor": number;
  "currency": Currency;
  "approved": boolean;
  "response_code": string;
  "decline_reason": string | null;
  "message": string;
  "reservation_id": string | null;
  "created_at": string;
};

export type CardAuthorizationList = {
  "card_id": string;
  "authorizations": Array<CardAuthorization>;
};

export type StoreConnectionStatus = "not_connected" | "awaiting_login" | "connected" | "expired";

export type StoreConnection = {
  "store_id": string;
  "name": string;
  "status": StoreConnectionStatus;
  "connected_at": string | null;
  "message": string;
};

export type StoreList = {
  "stores": Array<StoreConnection>;
};

export type StoreLoginTicket = {
  "ticket": string;
  "expires_in_s": number;
};

export type LoginStreamServerMessage = {
  "type": "viewport";
  "width": number;
  "height": number;
} | {
  "type": "frame";
  "data": string;
  "width": number;
  "height": number;
} | {
  "type": "notice" | "error";
  "text": string;
} | {
  "type": "status";
  "status": StoreConnectionStatus;
};

export type LoginStreamClientMessage = {
  "type": "down" | "up" | "move";
  "x": number;
  "y": number;
} | {
  "type": "wheel";
  "x": number;
  "y": number;
  "dx": number;
  "dy": number;
} | {
  "type": "text";
  "text": string;
} | {
  "type": "key";
  "key": "Backspace" | "Tab" | "Enter" | "Escape" | "ArrowLeft" | "ArrowRight" | "Delete";
} | {
  "type": "resize";
  "width": number;
  "height": number;
};

export type CartSyncRequest = {
  "mandate_id": string;
  "quote_id": string;
};

export type CartSyncLine = {
  "product_id": string;
  "sku": string;
  "title": string;
  "quoted_quantity": number;
  "cart_quantity": number;
  "quoted_unit_price_minor": number;
  "cart_unit_price_minor": number | null;
  "status": "ok" | "price_changed" | "quantity_mismatch" | "missing";
};

export type CartOtherItem = {
  "sku": string;
  "title": string;
  "quantity": number;
  "unit_price_minor": number;
  "checked": boolean;
};

export type CartSyncResult = {
  "store_id": string;
  "quote_id": string;
  "mandate_id": string;
  "status": "synced" | "mismatch" | "partial" | "not_connected" | "session_expired" | "store_error";
  "observed_at": string;
  "lines": Array<CartSyncLine>;
  "other_items": Array<CartOtherItem>;
  "cart_subtotal_minor": number | null;
  "quote_subtotal_minor": number;
  "checkout_ready": boolean;
  "message": string;
};

export type AttackSummary = {
  "id": string;
  "title": string;
  "category": "control" | "spending_limits" | "policy" | "integrity" | "concurrency" | "revocation" | "access_control" | "web_checkout" | "scale";
  "threat": string;
  "defence": string;
};

export type AttackList = {
  "attacks": Array<AttackSummary>;
};

export type AttackStep = {
  "phase": "setup" | "attack";
  "actor": string;
  "title": string;
  "method": string;
  "path": string;
  "request": Record<string, unknown> | null;
  "http_status": number;
  "outcome": string;
  "response": Record<string, unknown>;
};

export type AttackLedger = {
  "completed_payments": number;
  "paid_total_minor": number;
  "week_limit_minor": number | null;
  "week_paid_minor": number | null;
  "week_reserved_minor": number | null;
};

export type AttackSandbox = {
  "isolated": boolean;
  "simulated_time": string;
  "catalog": string;
};

export type AttackResult = {
  "id": string;
  "title": string;
  "category": "control" | "spending_limits" | "policy" | "integrity" | "concurrency" | "revocation" | "access_control" | "web_checkout" | "scale";
  "threat": string;
  "defence": string;
  "expected": string;
  "observed": string;
  "held": boolean;
  "error": string | null;
  "ran_at": string;
  "sandbox": AttackSandbox;
  "ledger": AttackLedger | null;
  "steps": Array<AttackStep>;
};

export type RiskSignal = {
  "check": string;
  "points": number;
};

export type RiskAssessment = {
  "score": number;
  "threshold": number;
  "signals": Array<RiskSignal>;
};

export type ProfileInput = {
  "full_name": string;
  "email": string;
  "phone": string;
  "address_line1": string;
  "address_line2"?: string | null;
  "district": string;
  "region"?: string | null;
  "city"?: string | null;
  "country"?: string;
  "postal_code"?: string | null;
};

export type Profile = {
  "full_name": string | null;
  "email": string | null;
  "phone": string | null;
  "address_line1": string | null;
  "address_line2": string | null;
  "district": string | null;
  "region": string | null;
  "city": string | null;
  "country": string | null;
  "postal_code": string | null;
  "missing": Array<string>;
};

export type PurchaseRequest = {
  "text": string;
};

export type PurchaseApproval = {
  "total_minor": number;
};

export type PurchaseCheck = {
  "requirement": string;
  "ok": boolean | null;
  "evidence": string | null;
  "reason": string;
};

export type PurchaseCandidate = {
  "url": string;
  "title": string;
  "price_minor": number | null;
  "price_text": string | null;
  "image_url": string | null;
  "currency": string | null;
  "in_stock": boolean | null;
  "matches": boolean;
  "unverified": Array<string>;
  "checks": Array<PurchaseCheck>;
  "problems": Array<string>;
};

export type PurchaseOption = {
  "title": string;
  "url": string;
  "shop": string | null;
  "price_minor": number | null;
  "price_text": string | null;
  "unverified": Array<string>;
  "checkout": "trying" | "guest" | "account_required" | "blocked" | "failed";
  "reason": string | null;
};

export type PurchaseOrder = {
  "total_minor": number;
  "total_text": string | null;
  "shipping_text": string | null;
  "currency": string | null;
  "checkout_url": string;
  "shop": string | null;
};

export type PurchaseSpec = {
  "item": string;
  "search_query": string;
  "quantity": number;
  "max_price_minor": number | null;
  "requirements": Array<string>;
  "preference": string | null;
};

export type Purchase = {
  "id": string;
  "status": "queued" | "searching" | "checking_out" | "awaiting_approval" | "paying" | "ordered" | "needs_account" | "needs_user" | "stopped_before_payment" | "cancelled" | "expired" | "failed";
  "request": string;
  "spec": PurchaseSpec | null;
  "candidates": Array<PurchaseCandidate>;
  "options": Array<PurchaseOption>;
  "choice": PurchaseCandidate | null;
  "order": PurchaseOrder | null;
  "card": {
  "last4": string;
  "source": string;
} | null;
  "events": Array<{
  "at": string;
  "text": string;
}>;
  "message": string;
  "live_payments": boolean;
  "created_at": string;
  "updated_at": string;
};

export type CredentialTemplate = {
  "mandate_id": string;
  "public_x": string;
};

export type CredentialVerify = {
  "credential": string;
};

export type PlanInput = {
  "mandate_id": string;
  "items": Array<QuoteItemRequest>;
};

export type PreviewInput = {
  "policy": Policy;
  "parent_mandate_id"?: string | null;
};

export type StudyFinish = {
  "actions": number;
  "errors": number;
  "outcome": "ready" | "failed" | "abandoned";
  "source_url": string;
  "observed_at": string;
  "note"?: string;
};

export type StudyStart = {
  "participant": string;
  "task_id": string;
  "mode": "manual" | "agent";
  "quote_id": string;
  "setup_seconds": number;
  "execution_mode": "human" | "scripted" | "model" | "fallback";
};

export type AgentComparison = {
  "merchant_id": string;
  "quote": Quote | null;
  "matched_items": number;
  "missing_items": Array<string>;
  "problem": string;
};

export type HTTPValidationError = {
  "detail"?: Array<ValidationError>;
};

export type ValidationError = {
  "loc": Array<string | number>;
  "msg": string;
  "type": string;
  "input"?: unknown;
  "ctx"?: Record<string, unknown>;
};

export type SandboxStart = {
  "mandate_id": string;
  "quote_id": string;
  "approved_quote_hash": string;
  "scenario"?: "happy" | "order_failure" | "lost_capture_response" | "pending_refund" | "failed_refund";
};
