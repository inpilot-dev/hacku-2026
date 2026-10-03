"""The owner's money behind each single-use card: held at reserve, captured on pay, released or refunded."""

from __future__ import annotations

import json
from dataclasses import replace

from .conftest import AGENT, USER, key
from .test_virtual_cards import freeze


def hold(h, reservation_id) -> dict | None:
    with h.wallet.db.read() as conn:
        row = conn.execute("SELECT * FROM funding_holds WHERE reservation_id = ?", (reservation_id,)).fetchone()
    return dict(row) if row else None


def event(h, event_type) -> dict:
    with h.wallet.db.read() as conn:
        return json.loads(conn.execute("SELECT payload_json FROM audit_events WHERE type = ? "
                                       "ORDER BY sequence DESC", (event_type,)).fetchone()[0])


def authorize_on(h, mandate_id, route_id, txn):
    return h.client.post("/api/v1/authorizations", headers={**AGENT, **key()},
                         json={"transaction_id": txn, "mandate_id": mandate_id, "quote_id": h.quote()["id"],
                               "payment_route_id": route_id}).json()


def test_reserving_holds_the_exact_total_on_the_owners_funding_source(h):
    m = h.confirm()
    auth = h.authorize(m["id"], h.quote()["id"]).json()
    f = hold(h, auth["reservation"]["id"])
    assert (f["status"], f["amount_minor"], f["captured_minor"]) == ("held", 29700, 0)
    assert (f["source_kind"], f["source_label"]) == ("card_authorization", "HSBC Red credit card")
    assert f["card_id"] == auth["payment_credential"]["credential_id"]
    funding = event(h, "authorization_approved")["rail"]["funding"]
    assert (funding["status"], funding["held_minor"], funding["simulated"]) == ("held", 29700, True)


def test_paying_captures_the_hold_once_the_card_is_approved(h):
    m = h.confirm()
    paid = h.buy(m["id"])
    f = hold(h, paid["receipt"]["reservation_id"])
    assert (f["status"], f["captured_minor"]) == ("captured", 29700)
    assert event(h, "payment_completed")["rail"]["funding"]["captured_minor"] == 29700


def test_tap_and_go_card_is_funded_from_the_wallet_balance(h):
    m = h.confirm()
    auth = authorize_on(h, m["id"], "tng_single_use_card", "t-tng")
    assert hold(h, auth["reservation"]["id"])["source_kind"] == "stored_value"
    assert h.pay(auth).json()["status"] == "completed"
    assert hold(h, auth["reservation"]["id"])["status"] == "captured"


def test_fps_debit_has_no_card_and_no_separate_hold(h):
    m = h.confirm()
    auth = authorize_on(h, m["id"], "fps_edda", "t-fps")
    assert h.pay(auth).json()["status"] == "completed"
    assert hold(h, auth["reservation"]["id"]) is None
    assert "funding" not in event(h, "payment_completed")["rail"]


def test_cancel_expiry_and_freeze_release_the_hold(h):
    m = h.confirm()
    cancelled = h.authorize(m["id"], h.quote()["id"]).json()
    h.client.post(f"/api/v1/reservations/{cancelled['reservation']['id']}/cancel",
                  headers={**AGENT, **key()}, json={})
    assert hold(h, cancelled["reservation"]["id"])["status"] == "released"

    expired = h.authorize(m["id"], h.quote()["id"]).json()
    h.clock.advance(seconds=121)
    h.budget(m["id"])
    assert hold(h, expired["reservation"]["id"])["status"] == "released"

    frozen = h.authorize(m["id"], h.quote()["id"]).json()
    freeze(h, m["id"])
    assert h.pay(frozen).json()["status"] == "refused"
    f = hold(h, frozen["reservation"]["id"])
    assert (f["status"], f["captured_minor"]) == ("released", 0)


def test_refund_returns_the_money_to_the_funding_source(h):
    m = h.confirm()
    paid = h.buy(m["id"])
    res = h.client.post(f"/api/v1/payments/{paid['receipt']['transaction_id']}/refund",
                        headers={**USER, **key()}, json={})
    assert res.status_code == 200, res.text
    f = hold(h, paid["receipt"]["reservation_id"])
    assert (f["status"], f["refunded_minor"]) == ("refunded", 29700)
    assert event(h, "payment_refunded")["rail"]["funding"]["refunded_minor"] == 29700


def test_a_shop_charging_the_card_outside_checkout_never_captures_owner_funds(h):
    """The issuer declines a charge above the card's cap, so nothing is taken from the owner."""
    m = h.confirm()
    auth = h.authorize(m["id"], h.quote()["id"]).json()
    issuer = h.wallet.issuer
    with h.wallet.db.write_tx() as conn:
        p = replace(issuer.reveal_for_test(conn, auth["payment_credential"]["credential_id"]),
                    merchant_id="demo_store_a", amount_minor=29701)
        assert issuer.authorize(conn, p, h.clock.now())["decline_reason"] == "over_spend_limit"
    f = hold(h, auth["reservation"]["id"])
    assert (f["status"], f["captured_minor"]) == ("held", 0)
