"""Simulated rails follow the ledger: one account per mandate, one single-use credential per purchase."""

from __future__ import annotations

import json

import pytest

from mandate.payments.rails import RailDeclined

from .conftest import AGENT, POLICY, USER, key


def rail_state(h):
    with h.wallet.db.read() as conn:
        accounts = [dict(r) for r in conn.execute("SELECT * FROM rail_accounts ORDER BY rail")]
        payments = {r["reservation_id"]: dict(r) for r in conn.execute("SELECT * FROM rail_payments")}
    return accounts, payments


def test_every_rail_opens_an_account_bounded_by_the_mandate(h):
    m = h.confirm()
    accounts, _ = rail_state(h)
    assert {(a["rail"], a["limit_minor"], a["expires_at"], a["status"]) for a in accounts} == {
        ("card_network_token", 30000, "2026-10-31T23:59:59+08:00", "active"),
        ("fps_edda", 30000, "2026-10-31T23:59:59+08:00", "active"),
        ("tap_and_go_single_use_card", 30000, "2026-10-31T23:59:59+08:00", "active"),
    }
    h.client.post(f"/api/v1/mandates/{m['id']}/revoke", headers={**USER, **key()}, json={})
    assert {a["status"] for a in rail_state(h)[0]} == {"closed"}


def test_tap_and_go_account_never_exceeds_the_observed_hk2000_card_maximum(h):
    h.confirm({**POLICY, "per_order_limit_minor": 500000,
               "period_limits": [{"period": "calendar_week", "limit_minor": 900000, "timezone": "Asia/Hong_Kong"}]})
    limits = {a["rail"]: a["limit_minor"] for a in rail_state(h)[0]}
    assert limits == {"card_network_token": 500000, "fps_edda": 500000, "tap_and_go_single_use_card": 200000}


def test_each_purchase_gets_its_own_scoped_single_use_credential(h):
    m = h.confirm()
    auth = h.authorize(m["id"], h.quote()["id"], headers={**AGENT, **key()}).json()
    cred = auth["payment_credential"]
    assert cred["single_use"] is True
    assert (cred["merchant_id"], cred["amount_minor"], cred["currency"]) == ("demo_store_a", 29700, "HKD")
    assert cred["expires_at"] == auth["reservation"]["expires_at"] == auth["claims"]["expires_at"]
    assert cred["purpose"] == auth["claims"]["purpose"]
    assert auth["claims"]["payment_route_id"] == auth["payment_route"]["route_id"]

    second = h.authorize(m["id"], h.quote()["id"]).json()
    assert second["payment_credential"]["credential_id"] != cred["credential_id"]

    _, payments = rail_state(h)
    row = payments[auth["reservation"]["id"]]
    assert (row["status"], row["token_id"]) == ("held", auth["claims"]["token_id"])


def test_credential_lifecycle_matches_ledger(h):
    m = h.confirm()
    paid = h.buy(m["id"])
    held = h.authorize(m["id"], h.quote()["id"]).json()
    h.client.post(f"/api/v1/reservations/{held['reservation']['id']}/cancel", headers={**AGENT, **key()}, json={})
    _, payments = rail_state(h)
    assert payments[paid["receipt"]["reservation_id"]]["status"] == "captured"
    assert payments[held["reservation"]["id"]]["status"] == "voided"
    assert paid["receipt"]["payment_mode"] == "sandbox"


def test_expired_reservation_voids_credential(h):
    m = h.confirm()
    auth = h.authorize(m["id"], h.quote()["id"]).json()
    h.clock.advance(seconds=121)
    h.budget(m["id"])
    assert rail_state(h)[1][auth["reservation"]["id"]]["status"] == "voided"


def test_credential_works_once_for_its_own_merchant_and_amount(h):
    m = h.confirm()
    auth = h.authorize(m["id"], h.quote()["id"]).json()
    cred = auth["payment_credential"]
    rail = h.wallet.rails[cred["rail"]]
    now = h.clock.now()
    with h.wallet.db.write_tx() as conn:
        with pytest.raises(RailDeclined, match="locked to demo_store_a"):
            rail.present(conn, cred["credential_id"], "demo_store_b", 29700, now)
        with pytest.raises(RailDeclined, match="over the credential"):
            rail.present(conn, cred["credential_id"], "demo_store_a", 29701, now)
    assert h.pay(auth).json()["status"] == "completed"
    with h.wallet.db.write_tx() as conn:
        with pytest.raises(RailDeclined, match="works once"):
            rail.present(conn, cred["credential_id"], "demo_store_a", 29700, now)


def test_audit_payloads_label_rails_as_simulated(h):
    m = h.confirm()
    h.buy(m["id"])
    with h.wallet.db.read() as conn:
        payloads = {r[0]: json.loads(r[1]) for r in conn.execute(
            "SELECT type, payload_json FROM wallet_stub_audit_events")}
    assert all(a["simulated"] for a in payloads["mandate_confirmed"]["rails"])
    for t in ("authorization_approved", "payment_completed"):
        assert payloads[t]["rail"]["simulated"] is True
    assert payloads["authorization_approved"]["credential"]["single_use"] is True


def test_rail_declines_over_account_limit_independently(h):
    m = h.confirm()
    rail = h.wallet.rails["tap_and_go_single_use_card"]
    with h.wallet.db.write_tx() as conn:
        with pytest.raises(RailDeclined):
            rail.hold(conn, {"id": "res_x", "mandate_id": m["id"], "amount_minor": 30001, "merchant_id": "m",
                             "expires_at": "2026-10-07T10:02:00+08:00", "token_id": "tok_x", "purpose": "x"},
                      h.clock.now())
