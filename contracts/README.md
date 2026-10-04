# Mandate API Contracts — v0.1.0

This document is the integration agreement for the four feature owners. All requests and responses use JSON. The initial backend runs at `http://localhost:8000/api/v1`.

**Source of truth:** `openapi.json`. **Generated frontend types:** `types.ts`. Regenerate with `python3 contracts/generate_types.py` from the repository root. Change the OpenAPI contract before changing shared request/response fields. Backend owners should implement matching Pydantic models and response validation; generated TypeScript types are not runtime validation. Endpoints marked with a required `Idempotency-Key` in the specification need that header, including draft creation and agent-run creation.

## 1. Team boundaries

| Owner | Implement first | Consume |
|---|---|---|
| Abdullah / agent | Draft interpretation, catalog access, shopping runs, trusted quote requests | Wallet authorization and payment responses |
| Timmy / wallet | Policy activation/revocation, authoritative quote adapter, reservations, signing, sandbox payment and budgets | Agent basket proposals and audit append function |
| Seungbin / verification + audit | Z3 models, concurrent/retry/revocation evaluation, audit append function, checkpoints, export and independent verifier | Wallet state transitions, catalog evidence and measured API outcomes |
| Noah / frontend + demo integration | Mandate confirmation, shopping/budget/receipt screens, event feed, audit and verification views, scenario controls and end-to-end integration | Agent, wallet, verification and audit APIs |

Draft interpretation is implemented by Abdullah; financial policy storage is owned by Timmy. Seungbin owns the audit append function and verifier. Timmy calls that function inside the same transaction as reservations and payments. Noah owns the frontend and integration wiring, and displays real API outcomes rather than inventing financial state. Agree on router registration and storage migrations rather than editing one shared application file concurrently.

First-version responsibilities:

- **Abdullah:** produce a basket/quote from the agent and expose shopping progress.
- **Timmy:** approve or refuse authorization, complete a sandbox payment, return a receipt and current budgets.
- **Seungbin:** evaluate over-budget, duplicate-payment and revocation cases; implement the financial audit function, then add the bounded Z3 model and independent verifier.
- **Noah:** connect mandate confirmation, basket, decision, receipt and budget into one usable web flow; show rule-specific refusals and wire the demo reset/scenario controls.

Seungbin provides structured results to Noah for the safety and audit screens. Noah supplies reusable screen components so Seungbin can add technical views without editing shared application routing. API contract changes are reviewed together.

The interface submits user requests. The agent worker submits authorization and payment requests using an agent credential. The verification component does not reserve or spend funds. The audit function participates in wallet transactions; it must not call a second independently committing write transaction.

## 2. Authentication and ownership

Use `Authorization: Bearer <configured-demo-token>`. The server maps each token to an actor ID, role (`user`, `agent`, or `verifier`) and authorized resources. This is scoped prototype authentication, not production identity.

- User credentials can create/confirm owned mandates and revoke owned or explicitly authorized descendant authority.
- Agent credentials can inspect their assigned authority and request purchases, but cannot activate, modify, revoke someone else's permission or impersonate the owner.
- Neither role nor owner identity is accepted from a client header or request body.
- Agent credentials stay in the agent process. Signing keys and database credentials stay in the trusted service. Never bundle privileged credentials into the frontend.
- A user's or agent's resource scope must be checked on every identifier lookup.

Each operation's `x-roles` identifies eligible roles; ownership checks still apply. Use separate demo tokens rather than a single shared administrator token. Any request to a blocked route must fail with HTTP 403 even if its JSON is otherwise valid.

## 3. Common conventions

1. **Money:** integer HKD cents. `30000` means HK$300. No floating-point money and no other currencies in v0.
2. **Time:** RFC 3339 with explicit offset. Server time decides expiry. Budget periods use Asia/Hong_Kong; calendar weeks begin Monday at 00:00. Period ends are exclusive.
3. **IDs:** opaque identifiers except transaction IDs, which the caller creates and preserves across retries. UUIDs are recommended. Clients must not parse identifiers.
4. **Null versus missing:** optional request fields may be omitted. Required nullable response fields are always present, using `null` when not available. Unknown fields are rejected unless the schema explicitly allows them.
5. **Quotes:** one merchant per quote. Catalog and quote totals come from the trusted adapter, including observed delivery fees under the selected delivery context. An LLM-supplied total is never authoritative.
6. **Evidence:** every displayed price or fee links to observations with source URL, observation time and applicability conditions. Mock snapshots are explicitly distinguished from a live retailer checkout.
7. **Categories:** trusted/curated metadata is used for the prototype. `unknown` or `conflicting` categories return review where a category restriction needs them. Model confidence alone is not an exemption.

## 4. First integrated flow

```text
User UI → POST /mandates/draft
User UI → POST /mandates/confirm
Agent   → GET /catalog
Agent   → POST /quotes
Agent   → POST /authorizations
Agent   → POST /payments
User UI → GET /wallet/{mandate_id}, GET /events
```

For early integration, the UI can request a quote as a user, and a developer-run agent worker can carry out authorization/payment. The browser must not receive an unrestricted agent secret to bypass roles. `auto_purchase: false` is the default shopping-run mode; enabling it still passes through wallet enforcement.

### A. Draft mandate

`POST /mandates/draft` — user role; HTTP 201.

```json
{
  "text": "Buy groceries, at most HK$300 per order and HK$800 per week, no alcohol, from demo_store_a, until 31 October 2026.",
  "delegatee_id": "agent_student",
  "parent_mandate_id": null
}
```

Return a draft ID, proposed policy, readable summary and unresolved questions. A draft is not spending authority. The model must not silently invent unspecified merchant restrictions, fee values, timezones or limits.

### B. Confirm mandate

`POST /mandates/confirm` — user role; HTTP 201; mandatory `Idempotency-Key`.

```json
{
  "draft_id": "draft_example",
  "policy": {
    "currency": "HKD",
    "per_order_limit_minor": 30000,
    "period_limits": [
      {"period": "calendar_week", "limit_minor": 80000, "timezone": "Asia/Hong_Kong"}
    ],
    "allowed_merchant_ids": ["demo_store_a", "demo_store_b"],
    "blocked_categories": ["alcohol"],
    "expires_at": "2026-10-31T23:59:59+08:00",
    "approval_above_minor": null
  }
}
```

These numbers are user-selected limits, not claimed merchant rates. The backend validates the complete policy and confirms parent delegation, if present. Expiry must be in the future; duplicate period definitions are invalid. The user can correct model interpretation in this explicit confirmation request.

### C. Create quote

`POST /quotes` — user/agent; HTTP 201.

```json
{
  "merchant_id": "demo_store_a",
  "items": [{"product_id": "product_from_catalog", "quantity": 2}],
  "delivery_context_id": "captured_delivery_context"
}
```

The backend returns an immutable quote containing server-calculated prices, categories, charges, final amount, revision, expiry and basket hash. A new quantity, fee, delivery context or catalog revision needs a new quote. The trusted adapter should detect stale quotes at payment; a snapshot adapter must label its snapshot nature. Quotes can be obtained for comparison even when eventual purchase is outside the mandate.

### D. Authorize and reserve

`POST /authorizations` — agent role; mandatory `Idempotency-Key`; HTTP 200 for all valid policy outcomes.

```json
{
  "transaction_id": "client_stable_uuid",
  "mandate_id": "confirmed_mandate_id",
  "quote_id": "authoritative_quote_id"
}
```

Discriminate the response by `status`:

- `approved`: contains reservation, signed token, display claims and updated budgets.
- `refused`: contains rule violations and current budgets; no token and no reservation.
- `requires_review`: contains the rule requiring user action; no token and no reservation.

For a refusal, show `violations[].code`, `rule_id`, `mandate_id`, `message` and relevant actual/limit amounts. The response identifies which ancestor or child rule applied. A refusal is not an HTTP/network exception.

### E. Pay

`POST /payments` — agent role; mandatory `Idempotency-Key`; HTTP 200 with `completed` or `refused`.

```json
{
  "transaction_id": "client_stable_uuid",
  "quote_id": "authoritative_quote_id",
  "authorization_token": "opaque_signed_token_from_authorization"
}
```

On completion, return a `Receipt`, `replayed` and the financial audit event sequence. Every receipt explicitly says `payment_mode: "sandbox"`. A replay returns the original receipt and must not debit or append another payment-completed event. A failed payment has no receipt and records its refusal.

The token is a bearer capability for one transaction, and must never be printed in ordinary logs. Signature validation alone does not authorize payment: persisted state and every applicable ancestor are rechecked.

## 5. Budget and delegation semantics

Authorization must atomically reserve **all** applicable ancestor and child budget periods:

`paid_minor + reserved_minor + new_amount_minor <= limit_minor`

Each displayed available amount equals `limit_minor - paid_minor - reserved_minor`. Successful payment moves reserved to paid without reducing available a second time. Cancel/expiry releases reserved money exactly once. A transaction belongs to fixed period IDs and cannot pay against expired periods.

Parent permission can only narrow as it travels down the delegation chain. Allowed merchants intersect; blocked categories accumulate; applicable per-order caps and expiries tighten. Every period constraint remains applicable, so a child weekly cap does not replace the parent's monthly cap. Parent confirmation authorizes delegation to the named subject; merely knowing a parent ID does not authorize a child.

The `approval_above_minor` threshold is evaluated across applicable policies. For v0, exceeding it returns `requires_review` and the flow stops. Automatic permission escalation, approval callbacks and widening a confirmed mandate are outside v0; never let an agent self-approve. Implement narrowly scoped approval grants later with another contract version.

For the first demo, root monthly and child weekly budgets can be added once a single mandate works. Do not expose a fake parent-delegation UI before the ancestor checks are implemented.

## 6. Concurrency, retries, expiry and revocation

- Use the database transaction as the serialization boundary. Check all budgets, create one reservation and append its financial event together. Model calls run outside this transaction.
- Payment, revocation and cancellation serialize through that same trusted ledger. If revocation commits first, a new payment cannot commit. If payment commits first, revocation cannot undo that completed purchase.
- Revoking a parent cancels unpaid reservations under descendant mandates; already-paid receipts remain retrievable by authorized actors.
- Choose a short authorization lifetime, e.g. 120 seconds as a prototype configuration. Actual expiry is no later than the quote, mandate, any ancestor or current budget period. The API response contains the exact server expiry.
- An invalidated/expired/cancelled reservation is never resurrected. Make a new transaction and authorization for a new attempt.
- `Idempotency-Key` is scoped by actor and operation. Same key and semantically identical JSON returns the saved outcome, including saved refusal. Changed payload is HTTP 409. No rechecking to silently replace a historical outcome.
- Independently enforce transaction uniqueness even if a retry arrives with a new idempotency key. A completed transaction returns its original receipt; mismatched quote or amount is a conflict.
- Retrieve an already completed receipt without performing a new debit, including after later revocation. Authentication, resource scope and transaction binding still apply.
- Database contention can return HTTP 503 with `retryable: true`; it must never fall back to approving a purchase. Retain the same idempotency key when safely retrying an uncertain response.

## 7. Token and audit integrity

Use EdDSA/Ed25519 JWS through a standard library with fixed accepted algorithm, pinned public key, audience and expiry checks. Claims bind transaction, reservation, leaf mandate/version, authoritative quote/hash, merchant, amount, currency and expiry. Revalidate ancestors from persisted state. Store signing keys outside agent access.

Define the basket hash as SHA-256 over UTF-8 canonical JSON of the immutable quote fields excluding `basket_hash`. For this prototype use recursively sorted object keys, compact separators, no NaN/infinity and no floating-point values; preserve array order. Agree on this encoding across producers. The agent normally forwards the server hash rather than recomputing it.

Each audit stream starts with a previous hash of 64 zeroes. Hash canonical event fields excluding `event_hash`. Sequences increase monotonically per stream. Event payloads include relevant policy snapshot/version, quote/amount, rule IDs and result; no credentials. Financial events and state changes are one transaction.

`GET /events` is a live-feed poll for the user's authorized stream. Return events after the cursor in order, with `next_after` set to the last returned sequence (or unchanged when empty). `has_more` indicates pagination. The audit exporter includes the full stream; document event-specific payload versions while implementing Seungbin's audit module.

`POST /audit/checkpoints` signs and independently retains a checkpoint before reporting success. Implement its retention using a separate verifier component/process. The verifier pins the signing public key and stores checkpoints independently of exports. A checkpoint can be referenced by a stable ID derived from its signed stream ID and sequence, e.g. `stream_demo:42`.

`POST /verifier/check` receives an export and that retained checkpoint ID. It cannot trust a checkpoint supplied only in the mutable export. Return `valid_through_checkpoint` only when anchored history agrees. Events after the retained checkpoint are structurally checked but reported as unanchored. No trusted checkpoint means `no_trusted_checkpoint`, never an unqualified pass. Modification or deletion of checkpoint-covered history must fail.

## 8. Formal-verification contract

`POST /verification/runs` accepts an unsafe/atomic model variant, initial available budget, two purchase amounts and bounded steps. It returns the solver result, assumptions, properties, runtime and an optional counterexample timeline.

Example concurrency scenario:

```json
{
  "variant": "atomic",
  "max_steps": 8,
  "initial_available_minor": 40000,
  "purchase_amounts_minor": [30000, 30000],
  "timeout_ms": 3000
}
```

`sat` plus a violating sequence maps to `counterexample_found`; `unsat` maps to `no_counterexample_within_bound`; timeout/unknown maps to `inconclusive`. This model endpoint is not used to approve individual purchases. Show model results beside real concurrent wallet requests; do not claim the bounded result proves the whole deployed implementation.

## 9. HTTP errors versus business decisions

Errors use:

```json
{
  "error": {
    "code": "IDEMPOTENCY_CONFLICT",
    "message": "This key was already used with a different request.",
    "request_id": "request_example",
    "retryable": false,
    "details": {}
  }
}
```

Use 401 for missing/invalid authentication, 403 for insufficient role/resource authority, 404 for missing resources, 409 for idempotency/state conflicts, 422 for invalid request shape and 503 for unavailable services. A well-formed purchase that violates policy returns HTTP 200 with a refused decision. Model-provider failures are service errors and must not fabricate successful runs.

Map FastAPI validation errors to this agreed envelope rather than leaking a second undocumented format. Every error path must preserve the no-double-debit guarantee.

## 10. Integration acceptance checklist

- User can confirm a mandate; agent cannot activate or widen one.
- Quote total is calculated by the trusted adapter from observed catalog data and applicable observed fees.
- Approved authorization reserves budgets; refusal reserves nothing.
- Two HK$300 requests competing for HK$400 remaining cannot both complete.
- Repeating payment returns the same receipt with no extra debit.
- Revocation committed before payment blocks it; completed payment remains completed.
- Changed or expired quote cannot use old authorization.
- Cancel/expiry releases funds once; subsequent payment is rejected.
- Audit export records confirmed authority and exact decision rules.
- Independent checkpoint verification detects covered mutation and truncation.

Prioritize the first single-mandate purchase and refusal for tomorrow morning's v0. The formal and independent audit views can follow after that flow works. Keep all demo reset/time-control functions separate from agent-accessible routes; they are not part of this public contract.

## 11. Proposed v0.2 wallet additions (Timmy)

Status: **proposed, needs the group's OK**. Every change is additive: new optional request fields, new optional
response fields, new reason codes and new endpoints. A v0.1 client keeps working if it ignores unknown fields
and treats an unknown reason code like any other refusal. TypeScript types are regenerated.

**Single-use payment credential.** An approved authorization now also returns `payment_credential`: a
simulated card number (or FPS reference) locked to one merchant, the authorized amount, HKD and the
reservation's expiry, bound to the token's `token_id`. It captures once; a second presentment, another merchant
or a larger amount is declined by the rail itself. Claims gain `purpose` and `payment_route_id`.

**Payment route picker.** `GET /quotes/{quote_id}/payment-options` (user or agent) ranks the routes in
`services/api/mandate/payments/fixtures/payment_routes.json`: Tap & Go Single Use Card, a credit card through a
scoped network token, and FPS eDDA. Rule: net cost = basket total + fee − reward value; lowest wins; ties go to a
route that holds funds at the rail, then route id. A reward counts only when its rate and cash value were
observed; every figure carries a source URL and the time it was read. `POST /authorizations` takes an optional
`payment_route_id` (default: the recommended route). Approved decisions and receipts carry `payment_route`
with the fee and reward. Budgets still count the basket total.

**Expiring approval.** A `requires_review` decision now carries `approval_request` (pending, with
`expires_at` = the sooner of 15 minutes, the quote's expiry and the mandate's expiry). The owner calls
`POST /approvals/{id}/approve` or `/deny` (user only, `Idempotency-Key`); `GET /approvals` and
`GET /approvals/{id}` list and read them. After an approval the agent calls `POST /authorizations` again with the
**same `transaction_id` and a new `Idempotency-Key`**: the review reasons the owner saw are waived once, and every
hard rule is checked again. A denial or a request that lapses turns the stored decision into a refusal with
`APPROVAL_DENIED` or `APPROVAL_EXPIRED`. An approval does not survive a new mandate version. Agents can never
approve.

**Refund.** `POST /payments/{transaction_id}/refund` (user only, `Idempotency-Key`, full refund once) refunds on
the rail, takes the amount out of `paid_minor` in every affected budget period and reverses the reward the
payment earned, which also moves the route's monthly reward tier back. Audit event `payment_refunded`.

**Velocity limit.** `Policy.velocity_limit` (optional `{max_purchases, window_minutes}`) refuses with
`VELOCITY_LIMIT_EXCEEDED` once that many reserved or paid purchases by the mandate and its descendants fall
inside the window. Cancelled and expired reservations do not count.

**Risk review (proposed v0.3, wallet, Timmy).** `Policy.risk_review` (optional boolean, default `false`) adds
a points scorecard for purchases that fit every hard rule but look unusual for this owner. Signals: a basket
well above the owner's usual (median paid basket, shrunk toward a quarter of the order cap while history is short),
a basket at least 90% of the order cap, a shop the owner has never bought from, items never bought before (after 3
purchases), a unit price at least 25% above the last price paid, orders just under `approval_above_minor` within 24
hours (split to skip approval), 3+ purchases within an hour, 80% of a period budget used in its first 40%, and an
hour the owner never shops in. One weak signal never interrupts; at a score of 50 the decision is `requires_review`
with every contributing signal as a reason. Every decision on such a mandate carries `risk_assessment`
(`{score, threshold, signals: [{check, points}]}`). Separately, and on every mandate, a product
listing whose text tries to instruct the agent ("ignore previous instructions", "approve without asking") is a
review reason. Each reason is a `RuleViolation` with code `RISK_REVIEW_REQUIRED`, a plain `message`, and its own
`rule_id` (`<mandate>/v<n>/risk:<check>`). They never refuse on their own: the decision is `requires_review` and goes
through the expiring approval above. An approval waives only the `(code, rule_id)` pairs the owner saw; a risk reason
that first appears after the approval refuses the retry. Hard rules are never waivable. A child mandate must keep
`risk_review` on if its parent has it.

`RISK_REVIEW_REQUIRED` also appears in a **refusal** in two cases where the reason cannot be put to the owner: a
risk reason that first appears after the owner approved (the retry is refused; start a new purchase), and an agent
that already has 3 purchases waiting for the owner on that mandate (`rule_id` `.../risk:pending_approvals`), so it
cannot wear the owner down with approval requests.

**Zero trust on agent requests (wallet, Timmy).** An agent token is valid only for mandates whose delegatee is that
agent *and* whose owner is the user the token names; anything else is 404. `RevokeRequest.reason` and
`CancelRequest.reason` are capped at 500 characters like the other free-text fields.

**Virtual cards (proposed v0.3, wallet, Timmy).** Confirming a mandate issues it a virtual card from a sandbox
card issuer: a Luhn-valid 16-digit number on a sandbox Mastercard BIN, expiry = the mandate's expiry, and spending
controls copied from the policy (per-order limit, HKD, allowed shops, blocked merchant category codes: gambling,
quasi-cash and money transfer always, liquor stores when `alcohol` is blocked). A child mandate's card hangs under its
parent's card. The mandate card is never presented to a shop. On the card rails, each approved authorization issues a
**single-use virtual card** under it, locked to the shop, the authorized amount and the reservation's expiry; its last4
is the `payment_credential.last4` the agent already receives (no new fields there). At payment the wallet presents that
card to the issuer, which runs a card authorization (number, expiry, security code, card status up the chain, shop,
category code, amount) and burns the card on approval. A cancelled or expired hold cancels its card; revoking a
mandate cancels its card for good. The number is encrypted at rest and only the last4 ever leaves the issuer; the
security code is derived, never stored.

New endpoints: `GET /mandates/{mandate_id}/card` (owner or the mandate's agent; masked), `POST
/mandates/{mandate_id}/card/freeze` and `/unfreeze` (owner only, `Idempotency-Key`, optional `reason`), `GET
/mandates/{mandate_id}/card/authorizations` (owner or the mandate's agent; every approved and declined card
authorization with its ISO 8583 response code). Freeze is reversible, unlike revoke: while a card in the mandate chain is
frozen, `POST /authorizations` refuses with the new reason code **`CARD_FROZEN`** (`rule_id` `<mandate>/v<n>/card`), and
`POST /payments` on an earlier authorization refuses with `CARD_FROZEN`, releases the hold and records a declined
authorization (response code 62). FPS purchases are refused too: the card is the face of the whole mandate.

**Owner funding (proposed v0.3, wallet, Timmy).** Each single-use card is paid for by a simulated hold on the
owner's funding source for exactly the authorized amount: held at authorization, captured once the issuer approves
the card at payment (never above the hold), released on cancel, expiry, freeze or revoke, and refunded with the
payment. No API shape changes. The `rail` object in `authorization_approved`, `payment_completed` and
`payment_refunded` audit payloads gains an optional `funding` object (`hold_id`, `source`, `label`, `held_minor`,
`captured_minor`, `refunded_minor`, `status`, `simulated: true`); FPS purchases have none.

New audit event types: `approval_granted`, `approval_denied`, `approval_expired`, `payment_refunded`, `card_frozen`,
`card_unfrozen`. `mandate_confirmed` records `rails` (one account per rail) instead of a single `rail`, and `card`
(card id, usage, network, last4; never the number).

## Local demo checkout adapter (Noah)

`POST /demo/purchases` is a local-only adapter to complete the UI's scripted sandbox path while Abdullah's shopping worker is not integrated. It is disabled unless `MANDATE_ENABLE_DEMO_CHECKOUT=1`. The request uses the authenticated user’s mandate and quote plus a caller-stable transaction ID. The server checks the user owns the mandate and quote, derives the delegatee from that mandate, and calls the normal wallet authorization and payment methods. The browser receives the decision and receipt, but never an agent credential, signed authorization token, or single-use payment credential. A replay uses deterministic server idempotency keys derived from the transaction ID.

This endpoint does not give the user control over an arbitrary actor, does not bypass the policy engine, and does not move real money. Remove it or replace its caller with Abdullah’s agent-run service when that service is integrated.

## Proposed: store accounts and real carts (Abdullah)

Status: **proposed, needs the group's OK**. Additive endpoints under the `stores` tag; nothing existing changes.

**Store sign-in.** `POST /stores/{store_id}/connect` opens the store's own sign-in page (Wellcome: yuu Rewards, mobile
number + SMS code) in an isolated browser context on the server. The client shows that one tab through
`WS /stores/{store_id}/login/stream?ticket=` (ticket from `POST /stores/{store_id}/login/ticket`, single use, 60 s):
the server sends JPEG frames, the client sends pointer and key events from a fixed set. The user types their code into
the store's page; the service does not store or log it. When the store sets its login cookie, the session (cookies
and localStorage) is saved server-side in a 0600 file and is never returned by any endpoint. `GET /stores` and
`GET /stores/{store_id}` report `not_connected`, `awaiting_login`, `connected` or `expired`;
`DELETE /stores/{store_id}/connection` forgets the session.

**Real cart.** `POST /carts/sync {mandate_id, quote_id}` fills the user's real cart at the quote's store, only when the
mandate is active and allows that store and the account is connected. It sets each quoted line to its quantity (read
first, so a retry never doubles a line), reads the cart back and compares line by line. **It never checks out.**
`checkout_ready` is true only when every line matches the quote and the cart holds no other selected item, because the
store checks out everything selected.

**Open questions for checkout (Timmy):**

- The store's cart price can differ from the snapshot catalog: on 2026-10-03 milk was HK$38.00 in the catalog and
  HK$40.00 in a logged-in cart, because cart prices follow the account's fulfilment store. Proposal: record the synced
  cart as an observation (evidence) and re-quote from it, so the single-use card amount equals what the store will
  charge. `cart_subtotal_minor` is evidence only; it never replaces the quote.
- A run that splits a list across stores yields one quote per store. `AgentRun.quote_id` would become a list, and the
  per-order limit needs defined semantics across several store orders.

## Proposed: one-time purchases (Abdullah)

Status: **proposed, needs the group's OK**. Additive endpoints under the `purchases` tag; nothing existing changes.

**Delivery details.** `PUT /profile` (onboarding) saves name, email, phone and Hong Kong address server-side in a 0600
file; `GET /profile` returns them with `missing` (required fields still empty). A purchase is refused until `missing` is
empty. Guest checkout forms are filled only from these values, so they go to the shop and to the browser agent's
models (TypeSafe picks fields, an OpenRouter model picks which saved value fits).

**Chat flow.** `POST /purchases {text}` returns at once; poll `GET /purchases/{id}`. `message` is the agent's reply to show
in the chat, `events` its progress. The agent searches the web and picks the **best deal**: the cheapest product whose
page shows every stated requirement (products whose page does not state one come after), unless the shopper states
another preference (`spec.preference`). It then tries the shop's **guest** checkout up to the card form:

- `awaiting_approval`: `order.total_minor` is the total read from the checkout page. The UI shows it and calls
  `POST /purchases/{id}/approve {total_minor}` (must match; one-shot) or `/cancel`. Not approved in 20 minutes: `expired`.
- `needs_account`: every matching shop needs an account. The agent never signs in; `message` and `options[].url` link
  to the best deal for the shopper to buy themselves.

**Paying (Timmy).** Approve re-reads the checkout total, takes a single-use card from a `CardSource`
(`issue`/`reveal`/`settle`/`close`, `services/api/mandate/agent/purchase/cards.py`) and types it into the shop's payment
fields over CDP, so no model sees it. The shop's one final button is clicked only when the server runs with
`MANDATE_LIVE_PAYMENTS=1`; otherwise the run ends as `stopped_before_payment`.

The app uses the wallet's source (`services/api/mandate/payments/web_cards.py`). This is how a web purchase is gated:

- **Opt-in per allowance.** `Policy.web_purchases` (default `false`) lets an allowance pay web shops that are not in
  `allowed_merchant_ids`. It applies only when every mandate in the chain has it on. The newest active allowance
  of the owner that qualifies is used. Without one, approval fails with "No allowance allows web purchases".
- **`issue`.** The wallet turns the approved total into a `web_checkout` quote (merchant = the shop's hostname, one
  line with category `unknown`) and runs the normal authorization. Every hard rule applies: per-order limit, period
  budgets, velocity, card freeze and revocation. Review reasons (unknown category, approval threshold, risk score)
  are answered by the owner's approve click, because only the owner can approve a web purchase. On approval the
  amount is reserved and a single-use card is held, locked to that hostname and total.
- **`settle`.** Called when the shop confirms the order. It captures the card, moves the amount from reserved to
  paid and writes a receipt (`GET /payments/web_<purchase id>`). The catalog re-pricing step is skipped for
  `web_checkout` quotes; the run has already re-read the page total before issuing.
- **`close`.** Nothing was ordered (dry run, declined, form not filled). It cancels the card and releases the
  amount.

Still open: the authorization TTL is the wallet's 120 s. A run that ends in `needs_user` (3-D Secure, unclear
result) is not settled, so its reservation expires and releases the amount.
