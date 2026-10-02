"""The simulated Tap & Go single-use card follows the ledger: issue, hold, capture, void, close."""

from __future__ import annotations

import json

import pytest

from mandate.payments.rails import RailDeclined

from .conftest import AGENT, USER, key


def card_ops(h):
    with h.wallet.db.read() as conn:
        cards = [dict(r) for r in conn.execute("SELECT * FROM simulated_cards")]
        ops = {r["reservation_id"]: r["status"] for r in conn.execute("SELECT * FROM simulated_card_ops")}
    return cards, ops


def test_card_lifecycle_matches_ledger(h):
    m = h.confirm()
    cards, _ = card_ops(h)
    assert [(c["mandate_id"], c["limit_minor"], c["status"]) for c in cards] == [(m["id"], 30000, "active")]

    paid = h.buy(m["id"])
    held = h.authorize(m["id"], h.quote()["id"]).json()
    h.client.post(f"/api/v1/reservations/{held['reservation']['id']}/cancel", headers={**AGENT, **key()}, json={})
    _, ops = card_ops(h)
    assert ops == {paid["receipt"]["reservation_id"]: "captured", held["reservation"]["id"]: "voided"}

    h.client.post(f"/api/v1/mandates/{m['id']}/revoke", headers={**USER, **key()}, json={})
    cards, _ = card_ops(h)
    assert cards[0]["status"] == "closed"
    assert paid["receipt"]["payment_mode"] == "sandbox"


def test_expired_reservation_voids_hold(h):
    m = h.confirm()
    auth = h.authorize(m["id"], h.quote()["id"]).json()
    h.clock.advance(seconds=121)
    h.budget(m["id"])
    assert card_ops(h)[1] == {auth["reservation"]["id"]: "voided"}


def test_audit_payloads_label_rail_as_simulated(h):
    m = h.confirm()
    h.buy(m["id"])
    with h.wallet.db.read() as conn:
        payloads = {r[0]: json.loads(r[1]) for r in conn.execute(
            "SELECT type, payload_json FROM wallet_stub_audit_events")}
    for t in ("mandate_confirmed", "authorization_approved", "payment_completed"):
        assert payloads[t]["rail"]["name"] == "tap_and_go_single_use_card"
        assert payloads[t]["rail"]["simulated"] is True


def test_card_declines_over_limit_independently(h):
    m = h.confirm()
    rail = h.wallet.rail
    with h.wallet.db.write_tx() as conn:
        with pytest.raises(RailDeclined):
            rail.hold(conn, {"id": "res_x", "mandate_id": m["id"], "amount_minor": 30001}, h.clock.now())
