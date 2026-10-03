"""C2: 20 deterministic gateway scenarios (build plan §11 categories that need no model).

    .venv/bin/python -m evaluation.run      (from the repo root; writes results/latest.json)

Each scenario runs against a fresh in-process wallet (FastAPI TestClient, FixedClock on a
Wednesday in HKT, placeholder catalog). It returns the purchase *attempts* it made; each
attempt says whether policy allows it (``legit``), whether a payment completed, and the
decisive outcome. Pass = observed outcome equals the expected one for every attempt.
"""

from __future__ import annotations

import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from fastapi.testclient import TestClient

from mandate.payments.clock import HKT, FixedClock
from mandate.payments.dev_app import create_app
from mandate.payments.drafts import InMemoryDrafts

from .common import AGENT, NORMAL_BASKET, POLICY, PRESPEND, RACE_BASKET, Api, basket, key, txn

VERSION = 1


class H:
    """Fresh wallet + API client for one scenario."""

    def __init__(self, tmp: str):
        self.clock = FixedClock(datetime(2026, 10, 7, 10, 0, tzinfo=HKT))  # Wednesday
        self.drafts = InMemoryDrafts()
        app, self.wallet = create_app(tmp, clock=self.clock, draft_lookup=self.drafts)
        self.api = Api(TestClient(app))

    def confirm(self, policy=POLICY, parent=None, delegatee="agent_student") -> str:
        d = f"draft_{txn()}"
        self.drafts.register(d, owner_id="user_demo", delegatee_id=delegatee,
                             expires_at="2026-10-31T23:59:59+08:00", parent_mandate_id=parent)
        return self.api.confirm(d, policy)["id"]


def attempt(legit: bool, final: dict, expected: str) -> dict:
    """``final`` is the last response of the attempt (authorization or payment)."""
    if final.get("status") == "completed":
        outcome = "completed"
    else:
        outcome = f"{final['status']}:{final['violations'][0]['code']}"
    amount = final["receipt"]["amount_minor"] if outcome == "completed" else None
    return {"legit": legit, "expected": expected, "outcome": outcome,
            "completed": outcome == "completed", "amount_minor": amount,
            "receipt_id": final["receipt"]["id"] if outcome == "completed" else None}


def purchase(h: H, m: str, body: dict, legit: bool, expected: str) -> dict:
    auth = h.api.authorize(m, h.api.quote(body)["id"])
    final = h.api.pay(auth) if auth["status"] == "approved" else auth
    return attempt(legit, final, expected)


# --- scenarios --------------------------------------------------------------------------------

def normal_basket(h):
    return [purchase(h, h.confirm(), NORMAL_BASKET, True, "completed")]


def normal_store_b(h):
    b = basket(("p_b_detergent", 2), ("p_b_bread", 3), merchant="demo_store_b", ctx="ctx_b_standard")
    return [purchase(h, h.confirm(), b, True, "completed")]


def normal_child_mandate(h):
    root = h.confirm(delegatee="agent_coordinator")
    child = h.confirm({**POLICY, "allowed_merchant_ids": ["demo_store_a"]}, parent=root)
    return [purchase(h, child, NORMAL_BASKET, True, "completed")]


def fee_pushes_over_order_cap(h):
    # HK$289 apples fit HK$300 alone; the HK$30 delivery fee makes HK$319.
    return [purchase(h, h.confirm(), basket(("p_a_apples", 10)), False, "refused:ORDER_CAP_EXCEEDED")]


def fee_change_after_authorization(h):
    m = h.confirm()
    auth = h.api.authorize(m, h.api.quote(NORMAL_BASKET)["id"])
    h.wallet.catalog = h.wallet.catalog.with_changes(fee_rules={"ctx_a_standard": [
        {"label": "Delivery", "min_subtotal_minor": 0, "max_subtotal_minor": None, "amount_minor": 4500,
         "evidence_ids": ["ev_fee_a_std"]}]})
    return [attempt(False, h.api.pay(auth), "refused:QUOTE_CHANGED")]


def unknown_category(h):
    return [purchase(h, h.confirm(), basket(("p_a_tonic", 1)), False, "requires_review:CATEGORY_REVIEW_REQUIRED")]


def blocked_category(h):
    return [purchase(h, h.confirm(), basket(("p_a_beer", 1)), False, "refused:CATEGORY_BLOCKED")]


def blocked_merchant(h):
    b = basket(("p_c_snacks", 1), merchant="demo_store_c", ctx="ctx_c_standard")
    return [purchase(h, h.confirm(), b, False, "refused:MERCHANT_NOT_ALLOWED")]


def approval_threshold(h):
    m = h.confirm({**POLICY, "approval_above_minor": 20000})
    return [purchase(h, m, NORMAL_BASKET, False, "requires_review:APPROVAL_REQUIRED")]


def authorization_expired(h):
    m = h.confirm()
    auth = h.api.authorize(m, h.api.quote(NORMAL_BASKET)["id"])
    h.clock.advance(seconds=121)  # wallet authorization TTL is 120s
    return [attempt(False, h.api.pay(auth), "refused:AUTHORIZATION_EXPIRED")]


def mandate_expired(h):
    m = h.confirm({**POLICY, "expires_at": "2026-10-07T12:00:00+08:00"})
    h.clock.advance(hours=3)
    return [purchase(h, m, NORMAL_BASKET, False, "refused:MANDATE_EXPIRED")]


def revoke_before_payment(h):
    m = h.confirm()
    auth = h.api.authorize(m, h.api.quote(NORMAL_BASKET)["id"])
    h.api.revoke(m)
    return [attempt(False, h.api.pay(auth), "refused:MANDATE_REVOKED")]


def payment_before_revoke_stays(h):
    m = h.confirm()
    auth = h.api.authorize(m, h.api.quote(NORMAL_BASKET)["id"])
    paid = h.api.pay(auth)
    h.api.revoke(m)
    after = {k: v for k, v in h.api.receipt(auth["transaction_id"]).items() if k != "http_status"}
    still = {"status": "completed", "receipt": after} if after.get("status") == "paid" else \
        {"status": "missing", "violations": [{"code": "RECEIPT_GONE"}]}
    assert paid["receipt"] == after, "receipt changed after revocation"
    return [attempt(True, still, "completed")]


def parent_revoked_blocks_child(h):
    root = h.confirm(delegatee="agent_coordinator")
    child = h.confirm({**POLICY, "allowed_merchant_ids": ["demo_store_a"]}, parent=root)
    auth = h.api.authorize(child, h.api.quote(NORMAL_BASKET)["id"])
    h.api.revoke(root)
    return [attempt(False, h.api.pay(auth), "refused:MANDATE_REVOKED")]


def _concurrent(h, mandates: list[str]) -> list[dict]:
    """One HK$300 authorization per mandate, released together by a barrier; then pay the approved ones."""
    quotes = [h.api.quote(RACE_BASKET)["id"] for _ in mandates]
    results, barrier = [None] * len(mandates), threading.Barrier(len(mandates))

    def go(i):
        barrier.wait()
        results[i] = h.api.authorize(mandates[i], quotes[i])

    threads = [threading.Thread(target=go, args=(i,)) for i in range(len(mandates))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    out = []
    for r in results:
        final = h.api.pay(r) if r["status"] == "approved" else r
        # Either request may win; whichever completes is the legitimate one, the other must be refused.
        won = final.get("status") == "completed"
        out.append(attempt(won, final, "completed" if won else "refused:PERIOD_BUDGET_EXCEEDED"))
    return out


def concurrent_same_mandate(h):
    m = h.confirm()
    for b in PRESPEND:  # HK$400 of HK$800 already paid this week
        h.api.buy(m, b)
    return _concurrent(h, [m, m])


def concurrent_two_shop_children(h):
    # Grill decision: mum's HK$800 parent, one child per shop, both agents run at Sunday 18:00.
    root = h.confirm(delegatee="agent_coordinator")
    a = h.confirm({**POLICY, "allowed_merchant_ids": ["demo_store_a"]}, parent=root)
    b = h.confirm({**POLICY, "allowed_merchant_ids": ["demo_store_a"]}, parent=root)
    for body in PRESPEND:
        h.api.buy(a, body)
    return _concurrent(h, [a, b])


def payment_retry(h):
    m = h.confirm()
    auth = h.api.authorize(m, h.api.quote(NORMAL_BASKET)["id"])
    k = {**AGENT, **key()}
    tries = [h.api.pay(auth, headers=k), h.api.pay(auth, headers=k), h.api.pay(auth)]
    first = attempt(True, tries[0], "completed")
    # A retry that yields a *different* receipt would be a duplicate completed payment.
    dupes = [attempt(False, t, "completed") for t in tries[1:] if t["receipt"]["id"] != tries[0]["receipt"]["id"]]
    return [first, *dupes]


def authorization_retry(h):
    m = h.confirm()
    q, t, k = h.api.quote(NORMAL_BASKET)["id"], txn(), {**AGENT, **key()}
    a1, a2 = h.api.authorize(m, q, t, headers=k), h.api.authorize(m, q, t)
    assert a1["reservation"]["id"] == a2["reservation"]["id"], "authorization replay made a second reservation"
    return [attempt(True, h.api.pay(a2), "completed")]


def quote_swap(h):
    m = h.confirm()
    auth = h.api.authorize(m, h.api.quote(NORMAL_BASKET)["id"])
    other = h.api.quote(basket(("p_a_rice", 1)))
    return [attempt(False, h.api.pay(auth, quote_id=other["id"]), "refused:QUOTE_CHANGED")]


def forged_token(h):
    m = h.confirm()
    auth = h.api.authorize(m, h.api.quote(NORMAL_BASKET)["id"])
    tok = auth["authorization_token"]
    forged = tok[:-4] + ("AAAA" if not tok.endswith("AAAA") else "BBBB")
    return [attempt(False, h.api.pay(auth, authorization_token=forged), "refused:AUTHORIZATION_INVALID")]


@dataclass(frozen=True)
class Scenario:
    id: str
    category: str
    setup: str
    run: Callable[[H], list[dict]]
    version: int = VERSION


SCENARIOS = [
    Scenario("det-01", "normal", "HK$297 rice basket at an allowed shop, empty week", normal_basket),
    Scenario("det-02", "normal", "Store B detergent + bread (HK$232.80 incl. HK$25 delivery)", normal_store_b),
    Scenario("det-03", "normal", "Child mandate (store A only) under HK$800 parent", normal_child_mandate),
    Scenario("det-04", "fee_change", "HK$289 apples + HK$30 delivery = HK$319 over HK$300 order cap",
             fee_pushes_over_order_cap),
    Scenario("det-05", "fee_change", "Delivery fee rises HK$30 -> HK$45 between authorization and payment",
             fee_change_after_authorization),
    Scenario("det-06", "unknown_category", "Herbal tonic with unknown category under an alcohol block",
             unknown_category),
    Scenario("det-07", "blocked_category", "Lager 6-pack under an alcohol block", blocked_category),
    Scenario("det-08", "blocked_merchant", "Store C is not on the allowed merchant list", blocked_merchant),
    Scenario("det-09", "approval_threshold", "HK$297 order with approval required above HK$200", approval_threshold),
    Scenario("det-10", "expiry", "Pay 121s after authorization (TTL 120s)", authorization_expired),
    Scenario("det-11", "expiry", "Authorize 3h after the mandate expired", mandate_expired),
    Scenario("det-12", "revocation", "User revokes between authorization and payment", revoke_before_payment),
    Scenario("det-13", "revocation", "Payment completed before revocation stays completed",
             payment_before_revoke_stays),
    Scenario("det-14", "revocation", "Parent revoked after child authorization", parent_revoked_blocks_child),
    Scenario("det-15", "concurrency", "HK$400 left, 2x HK$300 concurrent on one mandate", concurrent_same_mandate),
    Scenario("det-16", "concurrency", "Two shop child mandates share HK$400 left of the parent, 2x HK$300 concurrent",
             concurrent_two_shop_children),
    Scenario("det-17", "retry", "Same payment sent 3x (same key twice, then a new key)", payment_retry),
    Scenario("det-18", "retry", "Same authorization replayed with a new idempotency key", authorization_retry),
    Scenario("det-19", "quote_substitution", "Token for quote A presented with quote B", quote_swap),
    Scenario("det-20", "quote_substitution", "Tampered authorization token signature", forged_token),
]


def run_all() -> tuple[list[dict], list[float], list[float]]:
    results, auth_ms, pay_ms = [], [], []
    for s in SCENARIOS:
        with tempfile.TemporaryDirectory() as tmp:
            h = H(tmp)
            try:
                attempts, error = s.run(h), None
            except Exception as exc:  # a crash is a failed scenario, not a crashed run
                attempts, error = [], f"{type(exc).__name__}: {exc}"
            auth_ms += h.api.latency_ms["authorize"]
            pay_ms += h.api.latency_ms["pay"]
        passed = error is None and bool(attempts) and all(a["outcome"] == a["expected"] for a in attempts)
        results.append({"id": s.id, "version": s.version, "category": s.category, "setup": s.setup,
                        "expected": [a["expected"] for a in attempts], "observed": [a["outcome"] for a in attempts],
                        "passed": passed, "error": error, "attempts": attempts})
    return results, auth_ms, pay_ms
