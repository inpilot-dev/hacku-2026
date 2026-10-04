"""The wallet as card source for one-time web purchases: opt-in, hard rules, one single-use card per approval."""

from __future__ import annotations

import pytest

from mandate.agent.purchase.cards import CardError
from mandate.payments.auth import Actor
from mandate.payments.issuing import luhn_valid
from mandate.payments.web_cards import WalletCardSource

from .conftest import POLICY, USER, key

OWNER = Actor("user_demo", "user")
WEB_POLICY = {**POLICY, "web_purchases": True}
SHOP = "shop.example.hk"


def issue(source, amount=19900, purchase_id="pur_1", actor=OWNER):
    return source.issue(amount, SHOP, actor=actor, purchase_id=purchase_id, title="USB-C charger",
                        url=f"https://{SHOP}/checkout")


def week(h, mandate_id) -> dict:
    return next(b for b in h.budget(mandate_id) if b["period"] == "calendar_week")


def test_web_purchases_are_off_unless_the_allowance_opts_in(h):
    h.confirm()
    with pytest.raises(CardError, match="No allowance allows web purchases"):
        issue(WalletCardSource(h.wallet))


def test_approved_web_purchase_holds_a_card_and_settles_into_a_receipt(h):
    m = h.confirm(WEB_POLICY)
    source = WalletCardSource(h.wallet)
    # The policy blocks a category and the web item's category is unknown: the owner approving it in person
    # answers that review, so the card is still issued.
    card = issue(source)
    assert week(h, m["id"])["reserved_minor"] == 19900

    details = source.reveal(card)
    assert luhn_valid(details["pan"]) and details["pan"][-4:] == card.last4
    assert card.funded.startswith("HK$199.00 held on allowance")
    assert details["pan"] not in repr(card)

    source.settle(card)
    budget = week(h, m["id"])
    assert (budget["reserved_minor"], budget["paid_minor"]) == (0, 19900)
    receipt = h.client.get(f"/api/v1/payments/{card.card_id}", headers=USER).json()
    assert (receipt["merchant_id"], receipt["amount_minor"], receipt["status"]) == (SHOP, 19900, "paid")
    with pytest.raises(CardError):
        source.reveal(card)  # captured: the number is gone


def test_closing_a_card_releases_the_amount_and_kills_the_number(h):
    m = h.confirm(WEB_POLICY)
    source = WalletCardSource(h.wallet)
    card = issue(source)
    source.close(card)
    assert week(h, m["id"])["reserved_minor"] == 0
    with pytest.raises(CardError):
        source.reveal(card)
    source.close(card)  # a second close is a no-op


def test_hard_rules_still_refuse_web_purchases(h):
    m = h.confirm(WEB_POLICY)
    source = WalletCardSource(h.wallet)
    with pytest.raises(CardError, match="per-order limit"):
        issue(source, amount=POLICY["per_order_limit_minor"] + 1)
    assert week(h, m["id"])["reserved_minor"] == 0

    res = h.client.post(f"/api/v1/mandates/{m['id']}/card/freeze", headers={**USER, **key()}, json={})
    assert res.status_code == 200, res.text
    with pytest.raises(CardError, match="refused"):
        issue(source, purchase_id="pur_2")


def test_only_the_owner_can_approve_a_web_purchase(h):
    h.confirm(WEB_POLICY)
    agent = Actor("agent_student", "agent", owner_id="user_demo")
    with pytest.raises(CardError, match="owner"):
        issue(WalletCardSource(h.wallet), actor=agent)
