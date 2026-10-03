"""Virtual cards: one card per mandate, a single-use card per purchase, issuer-side controls and freeze."""

from __future__ import annotations

import json
import re
from dataclasses import replace

from mandate.payments.issuing import CardPresentment, luhn_check_digit, luhn_valid

from .conftest import AGENT, POLICY, USER, key


def card(h, mandate_id, headers=USER):
    res = h.client.get(f"/api/v1/mandates/{mandate_id}/card", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def freeze(h, mandate_id, action="freeze", headers=None, **body):
    return h.client.post(f"/api/v1/mandates/{mandate_id}/card/{action}", headers=headers or {**USER, **key()},
                         json=body)


def authorizations(h, mandate_id, headers=USER):
    res = h.client.get(f"/api/v1/mandates/{mandate_id}/card/authorizations", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["authorizations"]


def present(h, card_id, **override) -> dict:
    """Play a shop (or a fraudster holding the card data) presenting a card to the issuer."""
    issuer = h.wallet.issuer
    with h.wallet.db.write_tx() as conn:
        p = replace(issuer.reveal_for_test(conn, card_id), merchant_id="demo_store_a", amount_minor=29700)
        return issuer.authorize(conn, replace(p, **override), h.clock.now())


def single_use_card(h, auth) -> dict:
    with h.wallet.db.read() as conn:
        return h.wallet.issuer.card(conn, auth["payment_credential"]["credential_id"])


def test_luhn():
    assert luhn_valid("4242424242424242") and luhn_valid("5555555555554444")
    assert not luhn_valid("4242424242424241")
    assert luhn_check_digit("424242424242424") == "2"


def test_confirming_a_mandate_issues_its_virtual_card(h):
    m = h.confirm()
    c = card(h, m["id"])
    assert (c["usage"], c["network"], c["status"], c["payment_mode"]) == ("mandate", "mastercard", "active", "sandbox")
    assert re.fullmatch(r"\d{4}", c["last4"])
    assert (c["exp_month"], c["exp_year"]) == (10, 2026)  # valid thru the mandate's expiry
    assert c["controls"] == {
        "spend_limit_minor": 30000, "currency": "HKD", "allowed_merchant_ids": ["demo_store_a", "demo_store_b"],
        "blocked_mccs": ["4829", "5921", "6051", "7995"],  # liquor stores because alcohol is blocked
        "single_use": False, "expires_at": "2026-10-31T23:59:59+08:00",
    }
    assert c["single_use_cards"] == {"active": 0, "used": 0, "cancelled": 0}
    assert card(h, m["id"], headers=AGENT)["card_id"] == c["card_id"]  # the agent sees the masked card too


def test_card_number_is_never_stored_or_logged_in_clear(h):
    m = h.confirm()
    paid = h.buy(m["id"])
    with h.wallet.db.read() as conn:
        rows = conn.execute("SELECT id, network, pan_ciphertext FROM virtual_cards").fetchall()
        pans = [h.wallet.issuer.reveal_for_test(conn, r["id"]).pan for r in rows]
        bins = {r["network"]: pan[:6] for r, pan in zip(rows, pans)}
        dump = "\n".join(conn.iterdump())
        events = [json.loads(r[0]) for r in conn.execute("SELECT payload_json FROM audit_events")]
    assert len(pans) == 2 and all(luhn_valid(p) and len(p) == 16 for p in pans)
    for pan in pans:
        assert pan not in dump
        assert pan not in json.dumps(events)
        assert pan.encode() not in b"".join(r["pan_ciphertext"] for r in rows)
    assert bins == {"mastercard": "222300", "visa": "400000"}  # sandbox BINs; the recommended route is the network token
    confirmed = next(e for e in events if "card" in e and e.get("rails"))
    assert set(confirmed["card"]) == {"card_id", "usage", "network", "last4", "simulated"}
    assert paid["receipt"]["payment_mode"] == "sandbox"


def test_each_purchase_pays_with_its_own_single_use_card(h):
    m = h.confirm()
    mandate_card = card(h, m["id"])
    auth = h.authorize(m["id"], h.quote()["id"]).json()
    su = single_use_card(h, auth)
    assert auth["payment_credential"]["last4"] == su["last4"]
    assert (su["usage"], su["parent_card_id"], su["status"]) == ("single_use", mandate_card["card_id"], "active")
    assert su["controls"]["allowed_merchant_ids"] == ["demo_store_a"]
    assert su["controls"]["spend_limit_minor"] == 29700
    assert su["controls"]["expires_at"] == auth["reservation"]["expires_at"]

    assert h.pay(auth).json()["status"] == "completed"
    assert single_use_card(h, auth)["status"] == "used"
    [log] = authorizations(h, m["id"])
    assert (log["approved"], log["response_code"], log["card_last4"], log["mcc"]) == (True, "00", su["last4"], "5411")
    assert card(h, m["id"])["single_use_cards"] == {"active": 0, "used": 1, "cancelled": 0}


def test_issuer_declines_card_data_used_outside_its_purchase(h):
    m = h.confirm()
    auth = h.authorize(m["id"], h.quote()["id"]).json()
    su = single_use_card(h, auth)["card_id"]

    def declined(reason, **override):
        out = present(h, su, **override)
        assert (out["approved"], out["decline_reason"]) == (False, reason), out
        return out["response_code"]

    assert declined("merchant_not_allowed", merchant_id="demo_store_b") == "57"
    assert declined("over_spend_limit", amount_minor=29701) == "61"
    assert declined("mcc_blocked", mcc="5921") == "57"
    with h.wallet.db.read() as conn:
        real_cvv = h.wallet.issuer.reveal_for_test(conn, su).cvv
    assert declined("cvv_mismatch", cvv=f"{(int(real_cvv) + 1) % 1000:03d}") == "82"
    assert declined("expired_card", exp_year=2027) == "54"
    bad = "222300000000000"
    assert declined("invalid_card_number", pan=bad + str((int(luhn_check_digit(bad)) + 1) % 10)) == "14"
    mandate_card = card(h, m["id"])["card_id"]
    out = present(h, mandate_card)
    assert (out["decline_reason"], out["response_code"]) == ("mandate_card_not_presentable", "57")

    # The real checkout still works once, and the card is burnt afterwards.
    assert h.pay(auth).json()["status"] == "completed"
    assert declined("card_already_used") == "05"
    reasons = [a["decline_reason"] for a in authorizations(h, m["id"])]
    assert reasons.count(None) == 1 and "merchant_not_allowed" in reasons


def test_owner_can_freeze_and_unfreeze_the_card(h):
    m = h.confirm()
    assert freeze(h, m["id"], headers={**AGENT, **key()}).status_code == 403

    res = freeze(h, m["id"], reason="Lost phone")
    assert res.status_code == 200, res.text
    assert res.json()["card"]["status"] == "frozen"
    assert freeze(h, m["id"]).status_code == 409

    refused = h.authorize(m["id"], h.quote()["id"]).json()
    assert refused["status"] == "refused"
    assert [v["code"] for v in refused["violations"]] == ["CARD_FROZEN"]
    assert refused["violations"][0]["rule_id"].endswith("/card")

    assert freeze(h, m["id"], "unfreeze").json()["card"]["status"] == "active"
    assert h.authorize(m["id"], h.quote()["id"]).json()["status"] == "approved"
    with h.wallet.db.read() as conn:
        types = [r[0] for r in conn.execute("SELECT type FROM audit_events ORDER BY sequence")]
    assert types.count("card_frozen") == 1 and types.count("card_unfrozen") == 1


def test_freeze_after_authorization_declines_the_payment_and_releases_the_hold(h):
    m = h.confirm()
    auth = h.authorize(m["id"], h.quote()["id"]).json()
    freeze(h, m["id"])
    paid = h.pay(auth).json()
    assert paid["status"] == "refused"
    assert paid["violations"][0]["code"] == "CARD_FROZEN"
    assert h.budget(m["id"])[0]["reserved_minor"] == 0
    assert single_use_card(h, auth)["status"] == "cancelled"
    [log] = authorizations(h, m["id"])
    assert (log["approved"], log["response_code"], log["decline_reason"]) == (False, "62", "card_frozen")


def test_freezing_a_parent_card_stops_its_child_mandates(h):
    root = h.confirm(delegatee="agent_coordinator")
    child = h.confirm({**POLICY, "allowed_merchant_ids": ["demo_store_a"]}, parent=root["id"])
    assert card(h, child["id"])["parent_card_id"] == card(h, root["id"])["card_id"]
    freeze(h, root["id"])
    refused = h.authorize(child["id"], h.quote()["id"]).json()
    assert refused["violations"][0]["code"] == "CARD_FROZEN"
    assert refused["violations"][0]["mandate_id"] == root["id"]


def test_revoking_the_mandate_cancels_its_cards(h):
    m = h.confirm()
    auth = h.authorize(m["id"], h.quote()["id"]).json()
    h.client.post(f"/api/v1/mandates/{m['id']}/revoke", headers={**USER, **key()}, json={})
    assert card(h, m["id"])["status"] == "cancelled"
    assert single_use_card(h, auth)["status"] == "cancelled"
    assert present(h, single_use_card(h, auth)["card_id"])["decline_reason"] == "card_cancelled"
    assert freeze(h, m["id"]).status_code == 409
    assert freeze(h, m["id"], "unfreeze").status_code == 409


def test_expired_hold_cancels_its_single_use_card(h):
    m = h.confirm()
    auth = h.authorize(m["id"], h.quote()["id"]).json()
    h.clock.advance(seconds=121)
    h.budget(m["id"])  # any read-write call sweeps expired holds
    h.authorize(m["id"], h.quote()["id"])
    assert single_use_card(h, auth)["status"] == "cancelled"


def test_an_agent_only_sees_cards_it_was_delegated(h):
    m = h.confirm(delegatee="agent_coordinator")
    assert h.client.get(f"/api/v1/mandates/{m['id']}/card", headers=AGENT).status_code == 404
    assert h.client.get(f"/api/v1/mandates/{m['id']}/card/authorizations", headers=AGENT).status_code == 404
    assert h.client.get("/api/v1/mandates/m_nope/card", headers=USER).status_code == 404
