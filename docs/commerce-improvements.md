# Commerce comparison, evidence and recovery

This implements the competitor review's ten priorities as prototype features and measurement tools. Live model/search trials and human evidence have separate completion conditions. Do not present local fixture tests as real payment transactions, or automated browser trials as human time savings.

## Feature map

| Priority | Implementation | Evidence / remaining condition |
|---|---|---|
| Manual vs agent | Persistent participant/task records, server elapsed time, setup time, actions/errors, failed/abandoned outcomes, raw JSON export | Browser automated P9999 trial tested in an isolated wallet, labelled scripted. No human trials measured; recruit participants and use the protocol below. |
| Permission preview | Same pure wallet evaluator, current parent budgets and narrowing checks, allow/review/refuse examples with rule IDs | Read-only database snapshot test; browser review flow. Examples exclude risk history/stock. |
| Store comparison | Agent returns every candidate's actual quote, coverage, omissions and pricing failure; UI shows charges/source/time and coverage-first selection rule | Quoted candidates are proposals, not authorization. Different pack sizes are never claimed equivalent. |
| Search | Explicit DuckDuckGo/Serper adapter, bounded retry/cache, original observation times in event log, challenge/error surfaced | Mock HTTP tests. HK retailer extraction accuracy and live Serper latency remain unmeasured without keys/network trials. |
| Durable sandbox rail | Local independently persisted payment simulator, exact quote hash, owner-only explicit approval and stable operation IDs | Keyless capture/cancel/refund and restart/lost-response tests. Per user instruction, external official-provider integration is excluded. No retailer order is submitted. |
| Recovery states | Payment/order/recovery/budget tracked separately, independent lease worker, authoritative retrieval, pending/failed refunds keep budget accounted | Lost creation/capture response, restart, duplicate reconciliation, revocation, pending/failed recovery tested with injected provider. |
| Model evaluation | Explicit live Jev runner, ten synthetic normal/ambiguous/adversarial cases, model ID, selection correctness, unsafe allowance count and latency | Current run reports `not_configured`; no model quality or cost claim. This author-written set is not independently held out. |
| Basket planning | Up to three stores, exact title/pack only, fixed quantities, captured fee rules, 512-combination cap, all ancestor remaining budgets | Read-only proposals, no group purchase. Split payment execution is deliberately unavailable until group-wide reservation/partial-failure recovery is implemented and verified. |
| English / Traditional Chinese | Persistent language toggle; primary purchase/approval/refusal/receipt/unknown-fee labels; comparison/measurement/sandbox/credential controls | Browser language toggle checked. Legacy surfaces, provider messages, evidence text and some old copy remain English. Human comprehension review still required. |
| Owner credential | Browser Ed25519 private key in IndexedDB, pinned server public key, VC 2.0 envelope + vc+jwt, current mandate/version/expiry/revocation verification | Real browser sign/export/verify tested; tamper/key-swap/revocation tests. Export references mandate terms, never grants payment capability. No external interoperability, identity or bank support assertion. |

## Running durable sandbox recovery

The user chose a local sandbox instead of Stripe. There is no external payment adapter, key, webhook or card-entry step. This demonstrates our recovery protocol against a persisted simulator; it is not evidence about real payment-network reliability.

1. Build a basket in Groceries and open “Compare, measure and test payments.” Review the exact basket, choose a scenario, and explicitly approve it in the sandbox. Scenarios are normal confirmation, captured/order-failure/refund, lost capture response, pending refund and failed refund.
2. For a lost response, the independent sandbox ledger records the capture before throwing the injected error. The coordinator retains the budget hold and displays unknown. “Retrieve same operation” reconciles the existing capture without a second debit. A pending refund retains spent accounting on first retrieval and succeeds on the second; failed refunds remain spent and need operator investigation. These transitions are explicit deterministic simulations.
3. Restart the API with the same database/catalog/signing keys to demonstrate recovery. For `scripts/run-demo.sh`, set `MANDATE_DEMO_DATA_DIR` to a dedicated persistent directory; its default temporary wallet is disposable and cannot demonstrate recovery across launches. Do not delete/reset unresolved operation data.
4. Run the reconciler in another terminal with the **same absolute data/key/catalog paths**:

```sh
PYTHONPATH=services/api .venv/bin/python -m mandate.commerce.worker --once
# Omit --once for polling. API buttons also reconcile the same persisted operation.
```

Sandbox provider objects commit independently of wallet transactions. Stable operation IDs, a 300-second worker lease and authoritative simulator retrieval handle response loss/replay/restart. A crash may require waiting for that lease before another worker can recover it. State changes and money transitions append to the existing per-owner hash-chain audit in the same database transaction. Unknown responses and failed/pending refunds do not release money. The local simulator retains operation IDs indefinitely; external-provider expiry behavior is exercised only by injected unit-test providers.

A confirmed synthetic order is not retailer fulfillment. No real funds, external payment service or bank support is involved. A lost browser owner key requires an explicitly authorized server key migration; this version cannot silently replace a pinned key.

## Participant comparison protocol

Use at least three willing participants and counterbalance which route they try first. Keep participant codes anonymous (P01, P02). Prepare one reference basket per task with exact product/pack/quantity, source URL, observation timestamp and a shared completion boundary: reviewed basket / checkout readiness, **not payment or delivery**.

Start the timer BEFORE repeating the task. For manual trials, use execution `human`; agent trials must record `model`, `fallback`, `human` or `scripted` honestly. Record setup seconds separately, actions, errors and every failed/abandoned run. Observe the participant if making a human-study claim: server elapsed time alone cannot prove participation. Do not label our automated P9999 browser smoke test a human trial.

Compare snapshot tasks with the same snapshot. A live-retailer task must separately record current availability, checkout fees and observation time. Do not combine them into a price-parity or monetary-saving claim. Finish results are immutable; an identical retry is idempotent. Finish/abandon the active trial before starting the next. Export raw records from the UI:

```sh
.venv/bin/python -m evaluation.study_summary /absolute/path/mandate-study-records.json
PYTHONPATH=services/api .venv/bin/python -m evaluation.model_quality
```

The summary pairs exactly one successful manual and one successful agent run for the same participant/task, excludes scripted agent trials and non-human manual trials, and retains the failure/abandonment count. Duplicate trials need a new task/repeat identifier and explicit reporting; they are not silently averaged. The model runner exits 2 and records `not_configured` when credentials are absent. Its ten author-written cases measure selection on synthetic products plus deterministic wallet checks; they do not cover independent model evaluation, live retailer extraction, actual payment or observed API cost.

For [Serper](https://serper.dev/), set `MANDATE_SEARCH_PROVIDER=serper` and a server `SERPER_API_KEY`. Search observations retain their original cache timestamp (120-second TTL). A search hit is not price evidence; product pages and checkout still need their own validation.

## Contracts and storage

New endpoints live under `/api/v1/commerce`: preview, plans, study records, exact-quote approval, provider configuration status, persisted operations, reconciliation/cancellation and credential template/verify. Every endpoint requires an authenticated owner; agent credentials cannot use these owner controls. New strict input models are in `contracts/openapi.json` and generated `contracts/types.ts`. Commerce response payloads are documented here and typed in `apps/web/src/lib/commerce.ts`; their OpenAPI responses currently remain extensible objects rather than a frozen shared response-model agreement.

Storage schema version 6 adds durable studies, jobs and sandbox provider objects. Durable sandbox holds share the wallet's existing period rows; normal and durable sandbox purchases compete for that budget. Unresolved unknown/operator sandbox jobs also block new normal wallet authorization for the owner. Keep API, worker, catalog and SQLite file consistent. Plans do not produce executable quotes and cannot be used to bypass the one-merchant-per-quote payment API.
