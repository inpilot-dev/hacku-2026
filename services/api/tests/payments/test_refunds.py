"""Refunds give the money and the budget back, and take back the reward the purchase earned."""

from __future__ import annotations

import json

from .conftest import AGENT, USER, key


def refund(h, txn, headers=USER, **body):
    return h.client.post(f"/api/v1/payments/{txn}/refund", headers={**headers, **key()}, json=body)


def test_refund_returns_budget_and_reverses_reward(h):
    m = h.confirm()
    paid = h.buy(m["id"])
    txn = paid["receipt"]["transaction_id"]
    assert paid["receipt"]["payment_route"]["reward_minor"] == 1188
    assert h.budget(m["id"])[0]["paid_minor"] == 29700

    res = refund(h, txn, reason="Rice arrived damaged")
    assert res.status_code == 200, res.text
    out = res.json()
    assert (out["refund"]["amount_minor"], out["refund"]["reward_reversed_minor"]) == (29700, 1188)
    assert out["budgets"][0]["paid_minor"] == 0
    with h.wallet.db.read() as conn:
        net = conn.execute("SELECT SUM(reward_minor), SUM(spend_minor) FROM reward_ledger").fetchone()
        rail = conn.execute("SELECT status, refunded_minor FROM rail_payments").fetchone()
        event = json.loads(conn.execute("SELECT payload_json FROM wallet_stub_audit_events "
                                        "WHERE type = 'payment_refunded'").fetchone()[0])
    assert tuple(net) == (0, 0)
    assert tuple(rail) == ("refunded", 29700)
    assert event["refund"]["reward_reversed_minor"] == 1188


def test_refund_restores_the_reward_tier_position(h):
    m = h.confirm()
    paid = h.buy(m["id"])
    q = h.quote()
    refund(h, paid["receipt"]["transaction_id"])
    red = next(o for o in h.client.get(f"/api/v1/quotes/{q['id']}/payment-options", headers=AGENT).json()["options"]
               if o["route_id"] == "card_hsbc_red")
    assert red["reward_minor"] == 1188


def test_refund_happens_once_and_only_for_the_owner(h):
    m = h.confirm()
    txn = h.buy(m["id"])["receipt"]["transaction_id"]
    assert refund(h, txn, headers=AGENT).status_code == 403
    assert refund(h, txn).status_code == 200
    assert refund(h, txn).status_code == 409
    assert refund(h, "unknown").status_code == 404


def test_refund_replay_with_same_key_returns_same_refund(h):
    m = h.confirm()
    txn = h.buy(m["id"])["receipt"]["transaction_id"]
    headers = {**USER, **key()}
    first = h.client.post(f"/api/v1/payments/{txn}/refund", headers=headers, json={})
    second = h.client.post(f"/api/v1/payments/{txn}/refund", headers=headers, json={})
    assert first.json() == second.json()
