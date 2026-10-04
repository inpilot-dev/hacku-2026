"""UNSAFE baseline wallet: evaluation only, never part of the real wallet, never connected to a payment rail.

Same paths and body shapes as the wallet (contracts/openapi.json) for just the
endpoints the race needs: confirm, quote, authorize, pay, budget. The bug it
demonstrates is check-then-act without a lock: authorize reads the remaining
budget, waits, then reserves. Two concurrent requests both see the old balance
and both get approved.

ARTIFICIAL DELAY: ``race_delay_s`` (default 0.05s) sleeps between the read and
the write so the interleaving reproduces on every run. Results label it.
Without it the race still exists but only shows up intermittently.

``enforce_caps=False`` (prompt-only baseline of the model-dependent scenarios) also drops the
order-cap and weekly checks: the simulator still prices the basket from the catalog, but nothing
enforces the mandate. ``catalog`` swaps in the evaluation catalog.

State is in memory; payments are fake records ("payment_mode": "unsafe_baseline").
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime

from fastapi import Depends, FastAPI, Header

from mandate.payments.auth import Actor, current_actor
from mandate.payments.catalog import Catalog
from mandate.payments.clock import HKT, iso


def create_app(race_delay_s: float = 0.05, catalog: Catalog | None = None, enforce_caps: bool = True) -> FastAPI:
    app = FastAPI(title="UNSAFE baseline wallet (evaluation only)")
    catalog = catalog or Catalog.load()
    mandates: dict[str, dict] = {}
    quotes: dict[str, dict] = {}
    reservations: dict[str, dict] = {}
    receipts: dict[str, dict] = {}
    _dict_lock = threading.Lock()  # protects Python dicts only; the budget check is deliberately NOT under it

    def _id(prefix):
        return f"{prefix}_{uuid.uuid4().hex}"

    def week(m):
        return {"mandate_id": m["id"], "period": "calendar_week", "limit_minor": m["weekly_limit"],
                "paid_minor": m["paid"], "reserved_minor": m["reserved"],
                "available_minor": m["weekly_limit"] - m["paid"] - m["reserved"]}

    @app.post("/api/v1/mandates/confirm", status_code=201)
    def confirm(body: dict, actor: Actor = Depends(current_actor), idempotency_key: str = Header(alias="Idempotency-Key")):
        p = body["policy"]
        m = {"id": _id("m"), "status": "active", "version": 1, "policy": p,
             "weekly_limit": p["period_limits"][0]["limit_minor"], "paid": 0, "reserved": 0}
        with _dict_lock:
            mandates[m["id"]] = m
        return {"id": m["id"], "status": "active", "version": 1}

    @app.post("/api/v1/quotes", status_code=201)
    def quote(body: dict, actor: Actor = Depends(current_actor)):
        q = {"id": _id("q"), **catalog.price(body["merchant_id"], body["items"], body["delivery_context_id"])}
        with _dict_lock:
            quotes[q["id"]] = q
        return q

    @app.post("/api/v1/authorizations")
    def authorize(body: dict, actor: Actor = Depends(current_actor), idempotency_key: str = Header(alias="Idempotency-Key")):
        m, q = mandates[body["mandate_id"]], quotes[body["quote_id"]]
        amount = q["total_minor"]
        available = m["weekly_limit"] - m["paid"] - m["reserved"]  # 1) read
        time.sleep(race_delay_s)                                     # ARTIFICIAL DELAY between read and write
        if enforce_caps and (amount > m["policy"]["per_order_limit_minor"] or amount > available):  # 2) stale read
            return {"status": "refused", "transaction_id": body["transaction_id"],
                    "violations": [{"code": "PERIOD_BUDGET_EXCEEDED", "rule_id": f"{m['id']}/v1/period:calendar_week",
                                    "actual_minor": amount, "limit_minor": available}]}
        m["reserved"] += amount                                      # 3) write, unconditionally
        r = {"id": _id("res"), "transaction_id": body["transaction_id"], "mandate_id": m["id"],
             "quote_id": q["id"], "amount_minor": amount, "status": "reserved"}
        with _dict_lock:
            reservations[body["transaction_id"]] = r
        return {"status": "approved", "transaction_id": body["transaction_id"], "reservation": r,
                "claims": {"quote_id": q["id"]}, "authorization_token": "unsafe-no-signature",
                "budgets": [week(m)]}

    @app.post("/api/v1/payments")
    def pay(body: dict, actor: Actor = Depends(current_actor), idempotency_key: str = Header(alias="Idempotency-Key")):
        r = reservations[body["transaction_id"]]
        m = mandates[r["mandate_id"]]
        r["status"] = "paid"
        m["reserved"] -= r["amount_minor"]
        m["paid"] += r["amount_minor"]
        receipt = {"id": _id("rcpt"), "transaction_id": r["transaction_id"], "amount_minor": r["amount_minor"],
                   "payment_mode": "unsafe_baseline", "status": "paid",
                   "paid_at": iso(datetime.now(HKT))}
        receipts[r["transaction_id"]] = receipt
        return {"status": "completed", "receipt": receipt, "replayed": False}

    @app.get("/api/v1/wallet/{mandate_id}")
    def budget(mandate_id: str, actor: Actor = Depends(current_actor)):
        return {"mandate_id": mandate_id, "applicable_budgets": [week(mandates[mandate_id])],
                "server_time": iso(datetime.now(HKT))}

    return app
