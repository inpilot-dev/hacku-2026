# Evaluation (owner: Seungbin)

Measures the wallet against an unsafe baseline over real HTTP (C1) and through 20 deterministic gateway
scenarios (C2). Uses the wallet's venv (`services/api/.venv`, see `services/api/requirements-wallet.txt`).

```bash
# from the repo root
services/api/.venv/bin/python -m evaluation.run            # C1 + C2, prints a summary, writes results/latest.json
services/api/.venv/bin/python -m evaluation.live           # C1 only
services/api/.venv/bin/python -m pytest evaluation -q      # both, as tests
```

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
  substitution. The 10 model-dependent scenarios (including injection) wait for the agent module.

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
holds `authorize` and `pay`, each `{n, p50, p95}`, measured in-process with no network or model. All money is
integer HKD cents. Every value comes from the run that wrote the file. The file is not part of `contracts/` until
the team agrees.

## Limits

- Zero violations in 20 scenarios is not proof of safety. It covers these setups only.
- Latency is in-process on one laptop. It is not a network or production figure.
- In concurrency scenarios either request may win. The winner counts as the legitimate attempt and the loser
  must be refused with `PERIOD_BUDGET_EXCEEDED`.
