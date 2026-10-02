# Noah — Frontend and End-to-End Integration Plan

**Project:** Mandate / HacKU 2026
**Owner:** Noah
**Baseline inspected:** `main` at `0468327` on 2 October 2026
**Goal:** an integrated first version on Saturday morning; submission freeze Sunday, 4 October, 13:00 Asia/Hong_Kong
**Status:** Noah-owned local integration is implemented and has a documented clean-checkout launch path. The React/Vite UI, typed wallet client, shared FastAPI entrypoint, health route, observed Wellcome catalog and gated local checkout adapter are connected. The UI also consumes Timmy’s route/approval data, handles receipts and refusals, and makes revoked/expired mandates read-only. Local API acceptance rehearsal confirms one simulated receipt, an alcohol-policy refusal and a post-revocation refusal. No real funds move. Remaining completion dependencies are owned by teammates: Abdullah’s live draft/shopping-run APIs, Timmy’s server-side coverage enforcement for the HK$50 pickup evidence and payment-route revalidation, and Seungbin’s event/audit/verifier/Z3/concurrency services. Keep the explicit disconnected states until those endpoints are present and reviewed. A mobile-size browser rehearsal and a second physical machine rehearsal are still outstanding.

## 1. Product and scope

Build a mobile-friendly caregiver shopping application with a desktop demonstration dashboard. A user confirms a spending mandate, starts a grocery order, sees the chosen basket and total, and receives either a sandbox receipt or a clear refusal. Users can revoke spending permission and inspect why each decision happened.

The technical views show two agents competing for a shared budget, the bounded formal-verification result, and independently checked audit records. These views consume Timmy's and Seungbin's real outputs. The UI now has contract-backed polling and action handlers for event, audit export/checkpoint/verifier, and unsafe/atomic solver APIs; the current checkout still returns unavailable for the teammate-owned endpoints. Product prices and quote delivery charges display their linked evidence IDs as source links with evidence kind and observation date; placeholder or incomplete evidence is labelled unverified and is never linked as a live source.

The initial story is one caregiver delegating weekly groceries. Seungbin proposes one root weekly mandate with two shop-specific child mandates, which fits the current same-owner implementation. Treat this as a proposed demo story until agreed. The current wallet confirmation code requires the parent and child owner to be the same user; do not build a separate-parent-and-student account experience that claims cross-user delegation already works.

Core scope:

1. Explicit mandate confirmation.
2. Grocery basket and server-calculated total, including delivery.
3. Shopping progress and policy decision.
4. Actual sandbox purchase and receipt.
5. Remaining/reserved/spent budget and revocation.
6. Evidence/source inspection and accurate development-data labels.
7. Actual concurrency, bounded-verification and audit results once connected.

Extensions after integration: conversational draft UX, character animation, fridge-photo assistance and WhatsApp. Phone calls, automatic refunds, payment-card issuance and unverified reward recommendations are outside the first version.

## 2. Current repository reality

The repository contains the React/Vite client, shared FastAPI app, Timmy's wallet and Abdullah's observed Wellcome catalog API/capture utilities. Abdullah's natural-language draft and shopping-run endpoints, and Seungbin's independent audit/verifier/Z3 services, are still absent from the current checkout; the UI keeps those functions visibly disconnected instead of returning fabricated results.

| Area | Current state at `97134bd` | Consequence for Noah |
|---|---|---|
| Wallet | Timmy's routes, service, models, signing, SQLite storage and sandbox rail are present | Compose and consume wallet APIs; do not reimplement policy or ledger |
| Shared app | `mandate.app:app` mounts wallet routes, demo checkout, health and the built SPA | Keep this as the single entrypoint and preserve JSON errors for unknown API paths |
| Drafts | Wallet has an in-memory seeded `draft_demo`; natural-language draft route is not in this checkout | Keep explicit seeded/structured fallback; do not claim a real model interpretation |
| Quotes | Server calculates catalog totals and hashes | Never manufacture final prices in the browser |
| Agent | No shopping-run router/worker is present; Abdullah's separate `services/agent` captures observed products through Jev/Steel | Keep manual catalog selection available and label agent-run status as disconnected |
| Audit | Wallet shim writes stub events; no independent audit/checkpoint/verifier routes are mounted | Never present stub records as independently verified history |
| Catalog | `GET /catalog` serves the wallet's trusted catalog; `data/catalog/wellcome.json` has captured, timestamped Wellcome evidence | Use the observed snapshot by default, retain the explicit placeholder fallback, and disclose snapshot age/coverage limits |
| Payment rail | Local simulated adapter; no money moves | Receipts and screens clearly say sandbox simulation |
| Verification/evaluation | No Z3 or evaluation router is mounted in this checkout | Render honest not-connected states rather than invented passing results |
| Rubric | Percentages are asserted in Seungbin's proposal; handbook not inspected here | Obtain the source before using those weights to prioritize or claim compliance |

Existing wallet routes:

```text
POST /api/v1/mandates/confirm
GET  /api/v1/mandates/{mandate_id}
POST /api/v1/mandates/{mandate_id}/revoke
POST /api/v1/quotes
GET  /api/v1/quotes/{quote_id}
POST /api/v1/authorizations
POST /api/v1/payments
GET  /api/v1/payments/{transaction_id}
POST /api/v1/reservations/{reservation_id}/cancel
GET  /api/v1/wallet/{mandate_id}
```

The OpenAPI contract also defines draft creation, catalog, agent runs, events, audit and verification operations. Their presence in the specification does not mean their implementation exists.

## 3. Ownership and handoffs

### Noah owns

- `apps/web`: page structure, design, interaction states, API client, client state and presentation.
- Shared API composition: app startup, router mounting, error handlers, health/readiness, local proxy and startup documentation.
- Integration adapters and user-triggered demo orchestration, with explicit contracts and role enforcement.
- Demonstration scenario controls, deterministic reset lifecycle, screenshots/video and a backup run.
- Presentation of evidence, refusal reasons, bounded verification and audit outcomes.

### Teammates own

- **Abdullah:** draft interpretation and lookup, agent runs, basket selection, model metadata, observed catalog data and catalog endpoint.
- **Timmy:** policy, quotes, financial transitions, credentials/signing, reservations, payment and revocation. Owns storage migrations for his tables.
- **Seungbin:** audit append/signing/retention/verifier, Z3 models, concurrent API evaluation, manual comparison and result data.

Noah connects their modules rather than editing their policy, model or verification algorithms. Shared-router registration and proposed additional endpoints require agreement in `contracts/` first.

## 4. Application architecture

Use React + TypeScript + Vite with a small CSS token system. Import the existing `contracts/types.ts`; regenerate when the public schema changes. Keep data access in a typed API layer and domain state in feature hooks/reducers. Avoid introducing a large component library or a second backend framework before the first transaction works.

During development, Vite proxies `/api` to the local FastAPI service, preserving `/api/v1`. The frontend uses relative API URLs. This avoids a separate cross-origin configuration for the first local run. For a packaged demo, the shared FastAPI app serves built assets and API from one origin. The SPA fallback excludes unknown API paths so they remain JSON errors.

The shared API entrypoint should reuse:

```python
app.include_router(build_router(wallet), prefix="/api/v1")
install_error_handlers(app)
```

Construct the wallet through its documented dependencies and expose a draft-lookup integration seam for Abdullah. Timmy's `dev_app.create_app` is the current reference. Do not copy his service implementation into Noah's module.

Browser code must never contain the agent credential, authorization signing key or a selectable arbitrary actor identity. Purchase execution belongs in Abdullah's worker. Until that worker exists, any local demo orchestration lives on the server, is user-authenticated and demo-gated, and selects only the configured agent assigned to the current user's mandate. It calls the existing wallet logic without bypassing its policy checks. Do not make a general user-to-agent impersonation proxy.

## 5. Screen design and behavior

Use a clear, friendly product interface. Mobile width is the main user flow; desktop adds an evidence sidebar. A character is an optional status indicator attached to real events, not a substitute for readable decisions.

### A. Setup and readiness

Purpose: make the demo's real mode and dependencies obvious.

- Show backend connectivity, agent provider/mode, payment sandbox and catalog quality.
- Explicitly identify placeholder prices and unavailable modules.
- Display last successful refresh time and reconnect action.
- Keep debug/reset controls behind a visible local demo mode.

Do not infer verified data from `data_mode: observed_snapshot`: the current fixture uses that value despite its placeholder warning. Require explicit source/evidence checks before presenting compliant observed data.

### B. Create and confirm mandate

- Natural-language request input when Abdullah's endpoint is available.
- Structured review of per-order and period caps, merchants, blocked categories, expiry and optional approval threshold.
- Clarifying question when period, merchant or expiry is ambiguous.
- Explicit confirmation action; show the returned active mandate/version.
- Seeded-template path for the first version, prominently labeled, using a registered server draft.
- Readable “May do / Requires review / Cannot do” summary based on persisted policy.

Formatting in the interface is not authority. The backend decides validity and narrowing. Do not turn a policy draft into an active mandate in client state before confirmation succeeds.

### C. Shopping and basket

- Grocery list input, quantities and supported units.
- Agent progress with actual provider name and local/cloud/scripted mode.
- Candidate comparison based on returned products/quotes, clearly scoped to supported offers.
- Final basket rows, quantities, subtotal, delivery charges and total.
- Source/evidence links and applicability conditions.
- Quote expiry and stale-quote handling.

If the agent is unavailable, a developer adapter may select known catalog product IDs and call the real quote service. Label it scripted. It must not claim intelligent comparison or a live retailer connection.

### D. Decision and checkout

- States for checking policy, authorized/reserved, refused, review required, paying, completed and uncertain result.
- Display affected rule, actual amount and limit from the response.
- Show reservation expiry and refresh budget after financial mutations.
- When the basket changes, discard the old quote/authorization and request new ones.
- Disable accidental duplicate clicks while preserving intentional API retry semantics.
- Provide revoke/cancel controls appropriate to current state.

The browser does not decide that a purchase is safe. A green badge requires a real approved response, and purchase completion requires a real receipt.

### E. Wallet and receipts

- Show paid, reserved and available amounts separately for every applicable budget.
- Distinguish root budget from each child constraint; do not add ancestor and child balances together as if they were independent money.
- Show parent chain, merchant, receipt ID, amount, timestamp and sandbox label.
- A replayed receipt is marked as an existing purchase, not a second success.
- Fetch current mandate and budget after reload using known IDs; do not rely on local cache as financial truth.

### F. Evidence, audit and verification

- Human-readable event feed with cursor-based polling and deduplication.
- Rule/source detail drawer, including policy version and relevant quote.
- Audit export, previously retained checkpoint reference and verifier result.
- Separate structurally valid but unanchored history from checkpoint-covered history.
- Safety Lab with actual concurrent API outcomes beside the formal counterexample.
- Display bounds, assumptions, runtime and inconclusive status for Z3.
- Evaluation view reads Seungbin's measured file/schema after agreement; until then show not available.

If a module is unavailable, show that honestly. Never render “verified,” “zero violations” or a completed refund as an illustrative success.

## 6. State management and recovery

```mermaid
stateDiagram-v2
    [*] --> Draft
    Draft --> Active: user confirms; server succeeds
    Active --> Quoted: authoritative quote
    Quoted --> Reserved: authorization approved
    Quoted --> Refused: policy refuses
    Quoted --> Review: user input required
    Reserved --> Paid: payment receipt
    Reserved --> Cancelled: cancel/revoke commits
    Reserved --> Expired: server expiry
    Reserved --> Uncertain: network response lost
    Uncertain --> Paid: receipt reconciliation
    Uncertain --> Reserved: server confirms still pending
    Refused --> Quoted: new basket and transaction
    Review --> Quoted: permitted new input/authority
```

Server responses drive transitions. Merely closing a browser dialog or aborting a network request does not cancel a reservation or payment.

Store a stable transaction ID and idempotency key per semantic operation before submission. A network retry keeps the same key and request. A changed basket or fresh attempt uses new identifiers. Do not regenerate them on each React render.

After a payment timeout, retrieve its receipt by transaction ID and reconcile. A 404 alone does not prove the original request cannot still commit; use a controlled same-key retry or authoritative worker status. Never send a new transaction to “try again” before resolving an uncertain payment.

Use server expiry timestamps for countdowns, treating client time only as presentation. Poll the active run/budget/events while needed, stop polling on unmount, and refresh on reconnection. Out-of-order responses cannot overwrite newer run or ledger state.

Keep HTTP errors distinct from business refusals. A 200 response with `status: refused` becomes a policy decision view. A 401/403/409/422/503 becomes the appropriate error/retry behavior, retaining existing authoritative financial state.

## 7. API integration sequence

| Stage | Wire first | Depend on | Exit criterion |
|---|---|---|---|
| 1 | Existing wallet local app + typed fetch layer | Timmy router/auth/error envelope | UI fetches real mandate/quote/budget without inventing data |
| 2 | Seeded mandate confirmation + quote display | Registered draft and catalog fixture | Confirmed mandate and server total visible |
| 3 | Server-owned purchase execution | Abdullah worker or agreed demo adapter | One real sandbox receipt and updated budgets |
| 4 | Refusal, cancel and revoke | Wallet response semantics | Over-budget order cannot pay; revoked reservation cannot pay |
| 5 | Real draft and run APIs | Abdullah modules | Replace development adapters without changing screen flow |
| 6 | Ordered events and export | Seungbin audit module | Real decision history and policy snapshot visible |
| 7 | Concurrency and Z3 | Seungbin evaluation/model | Actual outcomes, counterexample and bounded result visible |
| 8 | Independent verifier and evidence dataset | Seungbin verifier; Abdullah observations | Anchored tamper check and source-backed monetary values ready |

Do not block all UI work on stages 5–8. Development adapters live behind one boundary and are never mixed silently with real results.

## 8. Proposed module layout

```text
apps/web/
  src/app/                    # routing, layout, readiness
  src/components/             # accessible reusable controls
  src/features/mandate/       # draft/review/confirm
  src/features/shopping/      # list, run feed, candidate basket
  src/features/wallet/        # budgets, decisions, revoke, receipt
  src/features/audit/         # events, export, checkpoint verification UI
  src/features/safety-lab/    # concurrency + bounded-model presentation
  src/features/demo/          # scenario controls and reset UI
  src/lib/api/                # typed fetch, errors, idempotency helpers
  src/lib/format/             # HKD, time, policy labels
  src/dev/                    # explicitly scripted development adapters
services/api/mandate/
  app.py                      # Noah: shared composition entrypoint
  integration/                # Noah: narrowly scoped adapters/demo orchestration
  agent/                      # Abdullah
  payments/                   # Timmy
  storage/                    # owner by table/migration agreement
  audit/                      # Seungbin
verification/                 # Seungbin
evaluation/                   # Seungbin
contracts/                    # agreed public contracts
scripts/                      # Noah: startup and demo coordination
```

This layout is proposed; choose the shared entrypoint once and avoid parallel edits to it. Folder separation does not replace agreement on process ownership, database transactions and interfaces.

## 9. Delivery sequence and time targets

Targets are Hong Kong time. Saturday morning is the team-agreed first-version window; **10:00 on 3 October is a proposed integration checkpoint**, not a new organizer deadline.

### First 60–90 minutes of implementation

1. Sync main, inspect any new teammate commits and record changed integration assumptions.
2. Create the web shell, basic visual tokens and typed API client.
3. Agree shared app composition and user/agent credential boundary with Timmy/Abdullah.
4. Connect the wallet router and show backend readiness.

### Tonight: first end-to-end path

1. Confirm seeded policy through the actual backend.
2. Request and display an actual sandbox quote.
3. Invoke server-owned agent execution and receive authorization/payment outcomes.
4. Display receipt, paid/reserved/available amounts and one budget refusal.
5. Commit a working checkpoint before optional visual features.

### Saturday morning: integrated v0

1. Replace seed-only paths with Abdullah's draft/run APIs if ready.
2. Add revoke, retry/replay and stale-quote UX.
3. Connect Seungbin's live events if ready; otherwise distinguish audit stub/unavailable mode.
4. Ensure the mobile-width flow works and collect first full demonstration recording.
5. Handoff any source-data gap to the catalog/evidence owner; retain visible development labels until fixed.

### Saturday afternoon

1. Add the real two-agent concurrency scene once Seungbin's runner is ready.
2. Add readable conflict/violation timeline and rule details.
3. Integrate measured manual comparison and source-backed prices/fees.
4. Prepare the supported payment-rail explanation from verified sources; show the implemented simulator accurately.

### Saturday evening

1. Integrate bounded Z3 results and independent audit verifier.
2. Freeze the demonstration sequence and shared contracts except necessary fixes.
3. Add character states only if core flows and evidence are stable.
4. Rehearse on the main and backup machines; record a backup.

### Sunday before submission

1. Sync agreed final changes, run targeted acceptance checks and rehearse from a clean seed.
2. Finalize README/start commands, configuration template, video and presentation screenshots.
3. Reserve roughly the last hour before 13:00 for submission and fixes; do not introduce architecture changes.

If an external module misses a handoff, retain an explicitly labeled development mode and prioritise the complete genuine wallet transaction. Document missing capability rather than showing a fabricated passing demonstration.

## 10. Meaningful verification plan

Tests here are planned acceptance checks, not results already achieved.

- Successful policy confirmation, quote, actual sandbox receipt and ledger refresh.
- An over-budget basket shows the real refusal and never shows paid.
- Agent-only payment endpoints cannot be invoked with the user credential.
- Two purchase attempts competing for one remaining budget cannot both complete.
- Double-click, retry and reload do not create duplicate payments.
- Revocation before payment blocks it; already completed purchase stays completed.
- Changing a basket invalidates old authorization.
- Timeout UI does not imply failure or issue a new transaction prematurely.
- Placeholder observations never receive a verified-data badge.
- Formal timeout/unknown displays inconclusive; verifier without checkpoint does not show pass.
- Mobile-width keyboard flow, readable contrast, accessible labels and non-color status text.
- UI events and displayed receipts/amounts agree with exported backend records.

Use component tests for consequential state/error behavior and browser checks for complete flows; coordinate domain tests with Seungbin instead of duplicating his suite. Run Timmy's existing suite when composing the shared app or changing integration assumptions, not simply to claim tests have been run.

## 11. Demonstration script

Prepare one action per scene with clear state reset and backend evidence.

1. **Delegate:** mobile view shows the caregiver's confirmed rules.
2. **Shop:** agent chooses a supported basket; show final total and sources.
3. **Complete:** real simulator produces receipt and wallet updates.
4. **Hold the boundary:** second basket fails the actual order cap with applicable observed delivery charge.
5. **Concurrent agents:** root budget has only enough for one purchase; actual requests show one reservation succeeding.
6. **Revoke:** pause after authorization, revoke, then show rejected payment.
7. **Inspect:** event record identifies the policy/rule and receipt.
8. **Verify:** altered anchored audit export fails independently; bounded-model result shows scope.

The headline is safe, understandable grocery delegation. Deep technical views answer follow-up questions without burying the user journey. Every simulated element, model fallback and unimplemented external rail is identified accurately.

## 12. Repository synchronization rules

Before each work session and at integration checkpoints, inspect local status and fetch the current remote. Pull using fast-forward only when the checkout is clean and the branch relationship allows it. Read changes to contracts, teammate modules and docs before continuing.

Never auto-stash, reset, force-push or overwrite local work to achieve synchronization. If local changes or divergence block a safe pull, inspect the incoming diff, preserve work and reconcile deliberately. Recheck remote before a push; handle a concurrent push through a normal merge/rebase process appropriate to the branch, never force.

This rule is for active work sessions. There is no background polling job or unattended pull process installed by this plan. Team members still need to commit, integrate and communicate contract changes.

## 13. Definition of done for Noah

- A clean local checkout can start the web application and shared API using documented commands.
- The caregiver can confirm authority, shop and inspect one actual sandbox result.
- Budget/refusal/revocation views are driven by backend truth.
- Agent, wallet, audit and verification modules have explicit, functioning integration boundaries.
- Financial credentials remain outside browser code and logs.
- Placeholder data and simulation labels are correct; demonstrated financial values have source evidence.
- Evidence and safety views show measured/verified results with their limits.
- Main and backup demonstrations can restart from known seed state.
- Changes are committed and pushed with team-facing handoff notes and the shared contract kept consistent.

## 14. Integration checkpoint — 3 October 2026

The wallet exposes payment-route comparisons, one-time approval requests, refunds and velocity limits. The web client integrates the quote-specific route picker, owner approve/decline actions, and an explicit continuation action for an approval that succeeded before a client/network failure. The demo purchase adapter resumes only the matching transaction after checking that its approval is approved; the wallet still reevaluates policy and budget before payment.

The mandate review now exposes the approval threshold and purchase-frequency limit, and the wallet view reads those values from the confirmed policy. The demo request contract carries an optional route ID and approval ID. The browser still receives no signed authorization token or payment credential.

The shared API composition is mounted. The local production build and the actual browser flow have been checked: the server returned the captured 80-product Wellcome catalog, the user confirmed an HK$800 weekly / HK$300 per-order mandate, the wallet quoted an HK$100.90 basket with its captured item and pickup evidence, the route picker showed three server-ranked options, and the sandbox produced a receipt and updated budget. No real funds moved. The focused local sandbox data is temporary and should be removed after the run.

Noah's local run/reset commands and concise presentation runbook are documented. Continue fetching the team branch at each integration checkpoint and reconnect new teammate modules only after reviewing their contracts and code. Remaining external dependencies: Abdullah's live draft and shopping-run APIs; Timmy's server-side rejection for unsupported Wellcome pickup subtotals and final route validation; Seungbin's audit/verifier/Z3 and measured concurrency/evaluation APIs. The frontend must keep these visibly unavailable until their actual results are connected. Payment routes and cited rewards remain advisory until the server performs final route eligibility checks. Checkout uncertainty must preserve the transaction ID and reconcile an existing receipt; a `409` conflict is not treated as proof that no payment occurred.

The documented `scripts/run-demo.sh` launcher was rehearsed from a clean temporary ledger in the browser: structured mandate confirmation, observed catalog quote, wallet-ranked payment route, sandbox receipt and refreshed budget all completed. A separate sandbox pass showed the alcohol rule refusal and that a revoked mandate cannot authorize a new purchase. An approval-gated UI run confirmed that a basket pauses for the owner's one-time approval, then completes under the same transaction; the stale review prompt disappears when its receipt arrives. The UI-facing checkout response was tightened to omit both the signed authorization token and one-time payment credential; the response was inspected after purchase and neither secret was present. Stopping the documented launcher removed its temporary demo directories. Frontend production build and generated OpenAPI TypeScript types were verified after the response-contract change.

A further clean-ledger browser run confirmed that starting a new agent order clears the prior receipt/refusal/uncertain result from the active order panel. The agent endpoint returned the expected 404 in this checkout; the old receipt remained a wallet record and was not shown as the new order's outcome. The production build passed after this UI-state fix.

The clean-ledger browser run also rechecked the refusal and revocation scenes: a HK$51 captured alcohol item received a wallet quote but checkout returned the `blocked_categories` refusal without a receipt; revoking the active mandate returned success, updated its state to revoked, and disabled agent, quantity and quote actions in the shopping view. The 404 event endpoint continued to appear as “Not connected,” not an empty verified feed.

On 3 October, a fresh single-origin demo ledger was exercised through the shared API: the captured 80-product catalog loaded; the seeded mandate confirmed; a timestamped Wellcome pantry item was quoted at HK$89.90; wallet-ranked payment options loaded; and the local sandbox returned a receipt. A captured alcohol item was refused with `CATEGORY_BLOCKED` and no receipt. After revocation, a fresh valid-item quote was refused with `MANDATE_REVOKED` and no receipt. The demo process was stopped afterward, removing its temporary ledger. This was an API-level acceptance rehearsal; it does not replace the remaining small-screen browser and backup-machine rehearsals.
