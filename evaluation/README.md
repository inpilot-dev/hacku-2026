# Evaluation (owner: Seungbin)

Measures the wallet against an unsafe baseline over real HTTP (C1), through 20 deterministic gateway
scenarios (C2) and through 10 model-dependent shopping scenarios run on Mandate and on a prompt-only baseline. Uses the repository venv from `just install` (`.venv`); any venv with the API and verification requirements works.

```bash
# from the repo root
.venv/bin/python -m evaluation.run            # C1 + C2, prints a summary, writes results/latest.json
.venv/bin/python -m evaluation.live           # C1 only
.venv/bin/python -m evaluation.model_eval     # model-dependent scenarios only
.venv/bin/python -m pytest evaluation -q      # all of it as tests; the model test uses scripted Jev answers
```

The model-dependent section needs `TYPESAFE_API_KEY` (environment or repo `.env`). Without it `run.py` still
writes every other section and records the model section as `not_run` with the reason. `EVAL_MODEL_REPEATS`
(default 3) sets the repeats per scenario and path; each trial makes one Jev call per allowed shop (2 here).

Servers start on free ports in 8100–8199 and are stopped (own PIDs only) when the run ends. Runtime data lives in
`evaluation/.data/` (git-ignored) and is deleted after the run.

## What runs

- **`unsafe_wallet/`** is an evaluation-only baseline with the wallet's paths and body shapes for confirm, quote,
  authorize, pay and budget. Authorize reads the remaining budget, sleeps an **artificial 0.05 s**, then reserves
  without a lock, so the race reproduces every run. It has no rail and no real payment. The real wallet has no
  unsafe path.
- **C1 (`live.py`)**: weekly HK$800 with HK$400 already paid, then two HK$300 authorizations sent concurrently
  over HTTP (`httpx.AsyncClient` + `asyncio.gather`). The approved ones are paid. On the real wallet only, it also
  sends one payment three times (same idempotency key twice, then a new key) and revokes a mandate between
  approval and payment.
- **C2 (`scenarios.py`)**: 20 scenarios `det-01`–`det-20` (version 1). Each gets a fresh in-process wallet
  (TestClient, fixed clock on Wednesday 2026-10-07 10:00 HKT). Categories: normal, fee change, unknown category,
  blocked category, blocked merchant, approval threshold, expiry, revocation, concurrency, retry and quote
  substitution.
- **Extra §11 items (`extra.py`)**: escalation outcomes (owner approves, denies, or never answers), the wallet's
  per-route cost for one basket, the bounded Z3 run for both variants (same inputs as the Safety Lab), and audit
  tamper/truncation detection on one real purchase's export.
- **Model-dependent (`model_eval.py`)**: 10 scenarios `model-01`–`model-10` (version 1): normal baskets (2),
  ambiguous items with a substitute or NONE, a list over the order cap, a beer request under an alcohol block,
  an unknown-category tonic, three prompt injections in listing titles (towards a blocked product, the order cap
  and the weekly cap) and a listing that lies about its price. Both paths get the same catalog, model, list,
  shopper instruction and checkout (the simulated user pays for whatever basket comes back). **Mandate** runs the
  real `AgentRuns` (Jev is offered only what the mandate allows), then wallet quote, authorize and pay.
  **prompt_only_baseline** gives Jev the rules as text and every available product of the allowed shops, and pays
  through `unsafe_wallet` with `enforce_caps=False` (no rail). Jev has no separate prompt field, so the rules are
  appended to the shopper instruction; the exact wording of that approximation is in the result.

The catalog is the **placeholder** fixture (`services/api/mandate/payments/fixtures/placeholder_catalog.json`),
not observed shop data.

## `results/latest.json` schema (v1)

The top level holds `schema_version`, `generated_at` (RFC 3339, HKT), `git_commit`, `python` and `catalog`.
`live_http` holds the C1 run: `race[]` per server (`server`, `available_before_minor`, `request_amount_minor`,
`approved`, `refused`, `refusals[]{code, rule_id}`, `completed_payments`, `week_after`, `overspend_minor`),
`retry` (`receipt_ids`, `same_receipt`, `replayed_flags`, `extra_debit_minor`), `revoke` (`payment_after_revoke`,
`refusal`, `earlier_payment_still_paid`, `week_after`) and `unsafe_artificial_delay_s`. `deterministic` holds
`scenario_count`, `passed` and `failed`, plus `scenarios[]`, each with `id`, `version`, `category`, `setup`,
`expected[]`, `observed[]`, `passed`, `error` and `attempts[]`. An attempt is one purchase attempt with `legit`
(policy allows it), `expected`, `outcome` (`completed` or `<status>:<violation code>`), `completed`,
`amount_minor` and `receipt_id`. It also holds `metrics`. `unauthorized_completed_payments`, `order_cap_overspend`,
`weekly_cap_overspend`, `legitimate_purchase_completion` and `false_refusal` are each `{count, denominator, rate}`.
`*_overspend` also carries `amount_minor`. `duplicate_completed_payments_under_retry` is a count. `latency_ms`
holds `authorize` and `pay`, each `{n, p50, p95}`, measured in-process with no network or model. `escalation` is `{count, denominator, rate}` over all C2 attempts.
`escalation_outcomes[]` replays one HK$297 order over a HK$200 approval threshold three times (`owner_decision`
`approve`, `deny`, `no_answer`) with `first_authorization`, `reasons`, `after_decision` and `paid_minor`.
`route_costs` is the wallet's payment-options answer for `basket`: `routes[]` with `gross_minor`, `fee_minor`,
`reward_minor`, `reward_counted`, `net_minor`, `rank` and `evidence_ids` (charged = gross + fee; sandbox, nothing is
charged). `formal[]` is one bounded Z3 run per `variant` with `status`, `solver_result`, `max_steps`,
`runtime_ms`, `assumptions`, `checked_properties` and `counterexample_steps`. `audit` checks one real purchase's
export against a checkpoint: `cases[]` (`original`, edited, edited and rechained, deleted and rechained,
truncated, no retained checkpoint) each with `expected`, `status`, `failure_codes` and `as_expected`, plus
`detected` of `of`. `model_dependent` is `{status: "not_run", reason}` without a key; otherwise it holds `provider`,
`endpoint`, `model_requested`, `model_ids_returned`, `settings`, `repeats_per_path`, `api_calls` (real HTTP
requests to TypeSafe per path and `total`, retries included), `baseline_approximation`, `definitions` and
`metrics.{mandate, prompt_only_baseline}`. Each path's metrics hold `trials`, `model_errors` (left out of every
denominator), `completed_payments`, `unauthorized_completed_payments`, `order_cap_overspend` and
`weekly_cap_overspend` (with `amount_minor`), `legitimate_purchase_completion`, `false_refusal`, `escalation`,
`injection_success` and `misleading_price_success` (each `target_picked` and `target_paid`), all
`{count, denominator, rate}`, plus `model_latency_ms` (TypeSafe round trip per call) and `wallet_latency_ms`
(in-process authorize/pay) kept apart, and `api_calls`. `scenarios[]` has `id`, `version`, `category`, `setup`,
`shopping_list`, `instruction`, `paid_before_minor`, `legit_possible`, `target` and one trial list per path; a trial
has `outcome`, `completed`, `merchant_id`, `basket`, `total_minor`, `violations` (the shared judge against the
policy), `target_picked`, `target_paid`, the run `message` and `jev_calls[]` (shop, products offered, each pick and
its probability). `not_run` lists what is still missing. All money is
integer HKD cents. Every value comes from the run that wrote the file. The file is not part of `contracts/` until
the team agrees.

## Limits

- Zero violations in 20 scenarios is not proof of safety. It covers these setups only.
- The model-dependent scenarios run a few repeats of 10 hand-written setups on the placeholder catalog with
  evaluation-only listings. Rates describe this model, these listings and these repeats only. Jev sees only
  product title, price and category, so injections live in titles.
- Latency is in-process on one laptop. It is not a network or production figure.
- In concurrency scenarios either request may win. The winner counts as the legitimate attempt and the loser
  must be refused with `PERIOD_BUDGET_EXCEEDED`.
