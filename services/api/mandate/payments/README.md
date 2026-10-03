# Wallet service (owner: Timmy)

Implements the wallet half of `contracts/openapi.json` v0.1.0: mandate confirm/get/revoke, quotes,
authorizations, payments, receipts, reservation cancel and budgets.

```bash
cd services/api
pip install -r requirements-wallet.txt
python3 -m pytest tests/payments -q                                       # acceptance tests
uvicorn --factory mandate.payments.dev_app:seeded_app --port 8000          # dev server at /api/v1
```

The dev server uses the tokens `dev-user-token` and `dev-agent-token` and seeds draft `draft_demo`.
Set `MANDATE_DEMO_TOKENS` for real demo tokens, as described in `auth.py`.

## For the rest of the team

- **Noah (shared app):** `app.include_router(build_router(wallet), prefix="/api/v1")` and
  `install_error_handlers(app)`. See `dev_app.create_app` for the wiring.
- **Abdullah (drafts, catalog):** pass `draft_lookup=` a callable that returns a draft's `owner_id`,
  `delegatee_id`, `parent_mandate_id` and `expires_at`. Point `MANDATE_CATALOG_PATH` at the observed catalog,
  using the file shape in `catalog.py`. The bundled `fixtures/placeholder_catalog.json` is **not observed data**.
- **Seungbin (audit):** once `mandate.audit` exports `append_event` and `canonical_json`, `audit_shim.py`
  uses them automatically. The wallet calls
  `append_event(conn, stream_id, type, payload, actor_id=, mandate_id=, transaction_id=, occurred_at=)`
  on its open transaction. Until then, events go to `wallet_stub_audit_events`.

## Guarantees and where they live

| Guarantee | Mechanism |
|---|---|
| No overspend under concurrency | `BEGIN IMMEDIATE` around check-and-reserve; a guarded `UPDATE`; a table `CHECK (paid + reserved <= limit)` |
| One payment per transaction | Unique `transaction_id` on reservations, decisions and payments; a replay returns the stored receipt |
| Idempotency | `(actor, operation, key)` plus a request hash; a changed payload returns 409; refusals replay too |
| Token is not enough to pay | Payment re-checks the chain's status, expiry and version, the reservation state, and the stored quote, then re-prices the quote |
| Release exactly once | A status-guarded `UPDATE ... WHERE status = 'reserved'` |
| No tokens at rest | Ed25519 is deterministic, so replays re-sign the persisted claims |

## Payment rails and routes

`rails.py` maps ledger states to rail steps: open a rail account on confirm (limit = per-order cap, expiry =
mandate expiry), mint a **single-use credential** on reserve, capture it once on pay, void it on cancel or expiry,
refund it on refund, and close the account on revoke. A credential is locked to one merchant, the authorized
amount, HKD, the reservation's expiry and the token's `token_id`; the rail itself declines a second capture,
another merchant or a larger amount.

Three rails, all **local simulations** (no sandbox exists for any of them, and no money moves):

| Route | Rail | Shaped like |
|---|---|---|
| `tng_single_use_card` | `tap_and_go_single_use_card` | HKT's Tap & Go Single Use Card: virtual prepaid Mastercard, one payment per card, HK$2,000 max |
| `card_hsbc_red` | `card_network_token` | The caregiver's credit card, tokenised per purchase (the Mastercard agent-token pattern) |
| `fps_edda` | `fps_edda` | FPS debit under an eDDA authorisation; no hold, so the reservation lives in the ledger |

`routing.py` ranks routes for a quote: net cost = total + fee − reward value, lowest first; ties go to a route that
holds funds at the rail. Figures live in `fixtures/payment_routes.json`, each with a source URL, the time it was
read and a quote from the page. Rewards count only when observed. Tiered rewards ("4% on the first HK$10,000 a
month") use the owner's spend this month from `reward_ledger`, so the ranking can change as the month goes on.
**The figures were read by Claude through a web fetch on 2026-10-02; re-open each URL before the demo.** Point
`MANDATE_ROUTES_PATH` at another file to change routes.

Receipts say `payment_mode: "sandbox"`, and audit payloads record `rail: {name, simulated: true, ref}`.

## Virtual cards

`issuing.py` is a sandbox card issuer behind a `CardIssuer` interface, so a real issuer (Stripe Issuing, Marqeta, a
bank BIN sponsor) can replace it without touching the ledger.

- **Mandate card.** Confirming a mandate issues one virtual Mastercard (sandbox BIN 222300, Luhn-valid, valid thru
  the mandate's expiry) with controls copied from the policy: per-order limit, HKD, allowed shops and blocked MCCs
  (7995 gambling, 6051 quasi-cash, 4829 money transfer, plus 5921 liquor when `alcohol` is blocked). It is never
  presented to a shop; the agent never holds a reusable card number. A child mandate's card hangs under its parent's.
- **Single-use cards.** On the two card rails, each reservation gets its own card under the mandate card, locked to
  that shop, amount and expiry. `payment_credential.last4` is its real last4. Pay presents it to the issuer, which
  checks number, expiry, CVV, status up the card chain, shop, MCC and amount, then burns it. Cancel, expiry and
  revoke cancel cards.
- **Funding.** The owner's money pays for each single-use card (`funding.py`): when the card is issued, the
  exact authorized amount is held on the route's funding source (Tap & Go wallet balance, or an authorization
  on the HSBC Red card). It is captured only after the issuer approves the card, never above the hold; cancel,
  expiry, freeze and revoke release it; a refund returns it. Rows live in `funding_holds`, and the
  `authorization_approved`, `payment_completed` and `payment_refunded` audit events carry `rail.funding`.
  FPS has no card, so it has no separate hold. Simulated: no balance is checked and no money moves.
- **Freeze.** `POST /mandates/{id}/card/freeze` and `/unfreeze` (owner only) is the reversible kill switch: new
  purchases refuse with `CARD_FROZEN`, a pending payment refuses and releases its hold, and the issuer declines
  with response code 62. Revoke still cancels for good.
- **Card data.** The PAN is AES-256-GCM encrypted at rest (key in `<key dir>/card_vault.key`) and found by an HMAC
  fingerprint; the CVV is derived from a card verification key and never stored; only the last4 leaves `issuing.py`.
  `GET /mandates/{id}/card/authorizations` lists every approved and declined attempt with ISO 8583 codes.

## Approvals, refunds, velocity and risk review

- **Approval that expires.** A purchase over `approval_above_minor` (or with an unknown category) returns
  `requires_review` with an `approval_request`. The owner approves or denies it; unanswered, it lapses into
  `APPROVAL_EXPIRED`. After approval the agent re-calls `POST /authorizations` with the same `transaction_id` and
  a new `Idempotency-Key`. The approval waives only the review reasons the owner saw, once, for that mandate version.
- **Refund.** `POST /payments/{txn}/refund` gives the money back on the rail, the amount back to every budget
  period, and reverses the reward.
- **Velocity.** `policy.velocity_limit = {max_purchases, window_minutes}` counts reserved and paid purchases in the
  mandate's subtree.
- **Risk review.** `risk.py` holds simple, explainable checks that escalate instead of refuse. With
  `policy.risk_review` on: first purchase on the mandate, first order from a new shop, basket at least 3x the usual, never-bought items, and a price
  at least 25% over the last price paid. Always on: product listing text aimed at the agent (prompt injection). Each
  adds a `RISK_REVIEW_REQUIRED` reason with its own rule id, so an approval waives only the reasons the owner read.

See `contracts/README.md` section 11 for the API shapes.
