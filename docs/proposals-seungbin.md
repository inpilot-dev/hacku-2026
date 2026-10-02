# Proposals (Seungbin)

Status: proposals, not decisions. Nothing here changes `contracts/` yet. Push back in the group.

I'm taking Z3 verification, scenario testing and the audit log/verifier.

## Story and concurrency
- The adult child is a Gen Z caregiver ordering Mum's groceries.
- Concurrency scene: one root weekly mandate (HK$800) with two child mandates, one per shop (fresh food, household). Both agents run on the same Sunday 18:00 schedule, so they hit the budget at the same time.
- This fits the contract as-is: one delegatee per mandate, one merchant per quote, `parent_mandate_id`.

## Models
- Jev-style decision models only pick from options we define (choice, score, yes/no). They can't write the mandate JSON or build a basket.
- Draft + basket: local LLM (Ollama `qwen2.5:7b-instruct` with JSON-schema output).
- Checks: Ollaya (local, Jev-compatible API) with `laya`. Three advisory checks: manipulative listing, unknown category, ambiguous budget period or expiry in the user's sentence. All go to review, never auto-approve.
- Cloud (AI Gateway / Jev) only as a labelled fallback. Scripted agent as the last resort.

## Audit log inside wallet transactions
- I provide `canonical_json()` (same encoding for basket hash and audit hash) and `append_event(conn, stream_id, event_type, payload)`.
- `append_event` writes on Timmy's open SQLite connection and never commits. I own the `audit_events` table.
- Checkpoint signing key is separate from the authorization token key.
- The verifier runs as its own process (own port, own data dir, pinned public key). `/audit/checkpoints` and `/verifier/check` forward to it, so contract paths stay the same.

## Concurrency proof
- The unsafe baseline is a tiny read-then-reserve service in `evaluation/` with the same API shape. The real wallet never has an unsafe path.
- The same concurrent test hits both.
- Real API race test by Sat 14:00 (critical path). Z3 counterexample by Sat 20:00 (second goal).

## Evaluation and results
- 30 scenarios: 20 deterministic gateway cases, 10 model-dependent shopping cases (incl. injection).
- Output: `evaluation/results/latest.json`. I'll add its schema to `contracts/README.md` once the group is OK with it. Not an API.
- Manual route (required by the problem statement): Sat 09:00–10:00, same basket by hand on 2 real HK grocery sites. Steps, minutes, total incl. delivery, timestamped.

## Gen Z UX (20% of the HKT score)
- Three mobile-width caregiver screens on top of the dashboard: create mandate (with a clarifying question), live feed with approve/revoke, receipts.
- Open the pitch on the phone view.

## Payment rails (15% of the HKT score, judged on how realistic the proposed rail is)
- Lead with card authorization → capture (Mastercard, UnionPay alongside). Reserve, re-check, commit and revoke map 1:1 to [Stripe manual capture](https://docs.stripe.com/payments/place-a-hold-on-a-payment-method) and to [Mastercard agent-token spend controls](https://developer.mastercard.com/mdes-customer-service/documentation/release-history/release-notes-july2026/).
- HKT's own [Tap & Go Single Use Card](https://www.pccw.com/assets/Common/files/press-release/2026/Jul/Press%20release_Tap%20%26%20Go%20Single%20Use%20Card_EN.pdf) (virtual Mastercard, user-set limit and expiry) is the same pattern. Our line: we make it programmable for agents.
- FPS eDDA as the bank-account rail: limit and expiry, but no hold, so the reservation lives in our ledger.
- A slide is a must. A Stripe test adapter only after the core works.

## Demo machines
- Both machines run everything. Main is my M5 Pro.
- One setup script: pinned Ollama/Ollaya versions, model pulls, seed data.

## Handbook
- The HKT rubric is published: fit 25, tech 25, security & trust 15, Gen Z UX 20, payment-rail feasibility 15. Plan §13 says it isn't, so worth updating.
