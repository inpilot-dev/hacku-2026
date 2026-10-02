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
