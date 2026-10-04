"""All-in merchant totals, checkout binding and uncertain payment retries."""

from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from .conftest import AGENT, POLICY, key


def fee(amount, kind="delivery"):
    return {"label": kind, "kind": "other" if kind == "service" else kind, "min_subtotal_minor": 0,
            "max_subtotal_minor": None, "amount_minor": amount,
            "evidence_ids": ["ev_fee_a_std"]}


@pytest.mark.parametrize("budget_kind", ["order", "period"])
def test_delivery_and_service_fee_both_count_towards_limits(h, budget_kind):
    h.wallet.catalog = h.wallet.catalog.with_changes(
        fee_rules={"ctx_a_standard": [fee(3000), fee(700, "service")]})
    policy = {**POLICY, "per_order_limit_minor": 30000 if budget_kind == "order" else 40000,
              "period_limits": [{"period": "calendar_week", "timezone": "Asia/Hong_Kong",
                                 "limit_minor": 80000 if budget_kind == "order" else 30000}]}
    mandate = h.confirm(policy)
    quote = h.quote()
    assert (quote["subtotal_minor"], quote["total_minor"]) == (26700, 30400)
    body = h.authorize(mandate["id"], quote["id"]).json()
    assert body["status"] == "refused"
    code = "ORDER_CAP_EXCEEDED" if budget_kind == "order" else "PERIOD_BUDGET_EXCEEDED"
    violation = next(v for v in body["violations"] if v["code"] == code)
    assert (violation["actual_minor"], violation["limit_minor"]) == (30400, 30000)
    assert "HK$30.00 delivery" in violation["message"]
    assert "HK$7.00 other" in violation["message"]
    assert all(b["reserved_minor"] == b["paid_minor"] == 0 for b in h.budget(mandate["id"]))


@pytest.mark.parametrize("field,value", [("currency", "USD"), ("merchant_id", "demo_store_b"),
                                        ("delivery_context_id", "different_context")])
def test_checkout_binding_change_refuses_even_with_identical_price(h, monkeypatch, field, value):
    mandate = h.confirm()
    auth = h.authorize(mandate["id"], h.quote()["id"]).json()
    original = h.wallet.catalog.price
    monkeypatch.setattr(h.wallet.catalog, "price", lambda *a, **kw: {**original(*a, **kw), field: value})
    body = h.pay(auth).json()
    assert body["status"] == "refused"
    assert body["violations"][0]["code"] == "QUOTE_CHANGED"
    assert all(b["reserved_minor"] == b["paid_minor"] == 0 for b in h.budget(mandate["id"]))
    with h.wallet.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 0
        assert conn.execute("SELECT status FROM rail_payments WHERE reservation_id = ?",
                            (auth["reservation"]["id"],)).fetchone()[0] == "voided"


def test_changed_fee_breakdown_requires_new_authorization_and_cannot_resurrect(h):
    mandate = h.confirm()
    original_catalog = h.wallet.catalog
    auth = h.authorize(mandate["id"], h.quote()["id"]).json()
    # Same final total, different charges: approval must bind the whole checkout.
    h.wallet.catalog = original_catalog.with_changes(
        fee_rules={"ctx_a_standard": [fee(2000), fee(1000, "service")]})
    headers = {**AGENT, **key()}
    first = h.pay(auth, headers=headers).json()
    assert first["violations"][0]["code"] == "QUOTE_CHANGED"
    h.wallet.catalog = original_catalog
    assert h.pay(auth, headers=headers).json() == first
    assert h.pay(auth).json()["violations"][0]["code"] == "RESERVATION_CANCELLED"
    assert all(b["reserved_minor"] == b["paid_minor"] == 0 for b in h.budget(mandate["id"]))


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_payment_retries_charge_and_log_once(h, same_key):
    mandate = h.confirm()
    auth = h.authorize(mandate["id"], h.quote()["id"]).json()
    headers = {**AGENT, **key()}
    barrier = threading.Barrier(6)

    def retry(_):
        barrier.wait(timeout=10)
        response = h.pay(auth, headers=headers if same_key else {**AGENT, **key()})
        assert response.status_code == 200, response.text
        return response.json()

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(retry, range(6)))
    assert all(r["status"] == "completed" and r["receipt"] == results[0]["receipt"] for r in results)
    assert sum(not r["replayed"] for r in results) == 1
    assert all((b["paid_minor"], b["reserved_minor"]) == (29700, 0) for b in h.budget(mandate["id"]))
    with h.wallet.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM audit_events WHERE type = 'payment_completed'").fetchone()[0] == 1
        assert conn.execute("SELECT status FROM rail_payments WHERE reservation_id = ?",
                            (auth["reservation"]["id"],)).fetchone()[0] == "captured"
