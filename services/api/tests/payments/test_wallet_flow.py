"""Wallet behaviour against the contract's integration acceptance checklist (contracts/README.md section 10)."""

from __future__ import annotations

import threading
import uuid

from mandate.payments.audit_shim import GENESIS_HASH, sha256_hex
from mandate.payments.catalog import Catalog

from .conftest import AGENT, POLICY, RICE_X3, USER, key


def week(h, mandate_id):
    return next(b for b in h.budget(mandate_id) if b["mandate_id"] == mandate_id and b["period"] == "calendar_week")


# --- happy path ---------------------------------------------------------------

def test_purchase_reserves_then_pays_once(h):
    m = h.confirm()
    q = h.quote()
    assert q["total_minor"] == 29700 and q["data_mode"] == "observed_snapshot"

    auth = h.authorize(m["id"], q["id"]).json()
    assert auth["status"] == "approved"
    assert auth["reservation"]["amount_minor"] == 29700
    assert auth["claims"]["basket_hash"] == q["basket_hash"]
    assert auth["budgets"][0]["reserved_minor"] == 29700
    assert auth["budgets"][0]["available_minor"] == 80000 - 29700
    # Expiry is the 120 s authorization lifetime, the earliest applicable bound here.
    assert auth["reservation"]["expires_at"] == "2026-10-07T10:02:00+08:00"

    paid = h.pay(auth).json()
    assert paid["status"] == "completed" and paid["replayed"] is False
    assert paid["receipt"]["payment_mode"] == "sandbox"
    b = week(h, m["id"])
    assert (b["paid_minor"], b["reserved_minor"], b["available_minor"]) == (29700, 0, 50300)

    receipt = h.client.get(f"/api/v1/payments/{auth['transaction_id']}", headers=USER).json()
    assert receipt == paid["receipt"]


def test_week_runs_monday_to_monday_hkt(h):
    m = h.confirm()
    b = week(h, m["id"])
    assert (b["starts_at"], b["ends_at"]) == ("2026-10-05T00:00:00+08:00", "2026-10-12T00:00:00+08:00")


# --- refusals reserve nothing ---------------------------------------------------

def test_order_cap_refusal_reserves_nothing(h):
    m = h.confirm()
    q = h.quote({**RICE_X3, "items": [{"product_id": "p_a_rice", "quantity": 4}]})  # HK$356 + HK$30 delivery
    res = h.authorize(m["id"], q["id"])
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "refused"
    v = body["violations"][0]
    assert (v["code"], v["actual_minor"], v["limit_minor"]) == ("ORDER_CAP_EXCEEDED", 38600, 30000)
    assert v["message"] == "Order total HK$386.00 (includes HK$30.00 delivery) is over the HK$300.00 per-order limit."
    assert v["rule_id"] == f"{m['id']}/v1/per_order_limit"
    assert week(h, m["id"])["reserved_minor"] == 0


def test_delivery_fee_pushes_basket_over_cap(h):
    # HK$289 of apples fits HK$300 alone; the observed HK$30 delivery fee takes it to HK$319.
    policy = {**POLICY, "per_order_limit_minor": 30000}
    m = h.confirm(policy)
    q = h.quote({**RICE_X3, "items": [{"product_id": "p_a_apples", "quantity": 10}]})
    assert (q["subtotal_minor"], q["total_minor"]) == (28900, 31900)
    body = h.authorize(m["id"], q["id"]).json()
    assert body["violations"][0]["code"] == "ORDER_CAP_EXCEEDED"


def test_blocked_category_unapproved_shop_and_unknown_category(h):
    m = h.confirm()
    beer = h.quote({**RICE_X3, "items": [{"product_id": "p_a_beer", "quantity": 1}]})
    assert h.authorize(m["id"], beer["id"]).json()["violations"][0]["code"] == "CATEGORY_BLOCKED"

    shop_c = h.quote({"merchant_id": "demo_store_c", "items": [{"product_id": "p_c_snacks", "quantity": 1}],
                      "delivery_context_id": "ctx_c_standard"})
    assert h.authorize(m["id"], shop_c["id"]).json()["violations"][0]["code"] == "MERCHANT_NOT_ALLOWED"

    tonic = h.quote({**RICE_X3, "items": [{"product_id": "p_a_tonic", "quantity": 1}]})
    body = h.authorize(m["id"], tonic["id"]).json()
    assert body["status"] == "requires_review"
    assert body["violations"][0]["code"] == "CATEGORY_REVIEW_REQUIRED"
    assert "authorization_token" not in body
    assert week(h, m["id"])["reserved_minor"] == 0


def test_approval_threshold_requires_review(h):
    m = h.confirm({**POLICY, "approval_above_minor": 20000})
    body = h.authorize(m["id"], h.quote()["id"]).json()
    assert body["status"] == "requires_review"
    assert body["violations"][0]["code"] == "APPROVAL_REQUIRED"


# --- concurrency -----------------------------------------------------------------

def test_two_hk300_orders_cannot_both_fit_hk400_remaining(h):
    """The demo's race: HK$297 each against HK$503 left this week; exactly one may reserve."""
    m = h.confirm()
    h.buy(m["id"])
    assert week(h, m["id"])["available_minor"] == 50300

    quotes = [h.quote() for _ in range(8)]
    results, barrier = [], threading.Barrier(len(quotes))

    def attempt(q):
        barrier.wait()
        results.append(h.authorize(m["id"], q["id"]).json())

    threads = [threading.Thread(target=attempt, args=(q,)) for q in quotes]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    statuses = sorted(r["status"] for r in results)
    assert statuses.count("approved") == 1, statuses
    assert all(r["violations"][0]["code"] == "PERIOD_BUDGET_EXCEEDED" for r in results if r["status"] == "refused")
    b = week(h, m["id"])
    assert b["paid_minor"] + b["reserved_minor"] <= b["limit_minor"]
    assert b["reserved_minor"] == 29700


# --- retries and idempotency ---------------------------------------------------

def test_payment_replay_returns_same_receipt_without_debit(h):
    m = h.confirm()
    auth = h.authorize(m["id"], h.quote()["id"]).json()
    headers = {**AGENT, **key()}
    first = h.pay(auth, headers=headers).json()
    again = h.pay(auth, headers=headers).json()
    new_key = h.pay(auth).json()
    assert again["receipt"] == first["receipt"] == new_key["receipt"]
    assert again["replayed"] is True and new_key["replayed"] is True
    assert week(h, m["id"])["paid_minor"] == 29700


def test_same_key_with_changed_payload_is_409(h):
    m = h.confirm()
    headers = {**AGENT, **key()}
    q1, q2 = h.quote(), h.quote()
    assert h.authorize(m["id"], q1["id"], txn="t1", headers=headers).status_code == 200
    res = h.authorize(m["id"], q2["id"], txn="t2", headers=headers)
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_authorization_replay_returns_same_token_and_one_reservation(h):
    m = h.confirm()
    q = h.quote()
    headers = {**AGENT, **key()}
    a = h.authorize(m["id"], q["id"], txn="t1", headers=headers).json()
    b = h.authorize(m["id"], q["id"], txn="t1", headers=headers).json()
    c = h.authorize(m["id"], q["id"], txn="t1").json()  # new key, same transaction
    assert a["authorization_token"] == b["authorization_token"] == c["authorization_token"]
    assert week(h, m["id"])["reserved_minor"] == 29700


def test_transaction_id_reused_for_different_quote_is_409(h):
    m = h.confirm()
    h.authorize(m["id"], h.quote()["id"], txn="t1")
    res = h.authorize(m["id"], h.quote()["id"], txn="t1")
    assert res.status_code == 409
    assert res.json()["error"]["details"]["reason_code"] == "TRANSACTION_CONFLICT"


# --- revocation ------------------------------------------------------------------

def test_revocation_before_payment_blocks_it_and_releases_funds(h):
    m = h.confirm()
    auth = h.authorize(m["id"], h.quote()["id"]).json()
    rev = h.client.post(f"/api/v1/mandates/{m['id']}/revoke", headers={**USER, **key()}, json={"reason": "demo"})
    assert rev.status_code == 200, rev.text
    assert rev.json()["cancelled_reservation_ids"] == [auth["reservation"]["id"]]
    assert rev.json()["mandate"]["status"] == "revoked"

    paid = h.pay(auth).json()
    assert paid["status"] == "refused"
    assert paid["violations"][0]["code"] == "MANDATE_REVOKED"
    b = week(h, m["id"])
    assert (b["paid_minor"], b["reserved_minor"]) == (0, 0)


def test_payment_before_revocation_stays_completed(h):
    m = h.confirm()
    paid = h.buy(m["id"])
    h.client.post(f"/api/v1/mandates/{m['id']}/revoke", headers={**USER, **key()}, json={})
    receipt = h.client.get(f"/api/v1/payments/{paid['receipt']['transaction_id']}", headers=USER)
    assert receipt.status_code == 200 and receipt.json() == paid["receipt"]
    assert week(h, m["id"])["paid_minor"] == 29700


# --- changed quotes, expiry and cancellation --------------------------------------

def test_fee_change_after_authorization_refuses_payment(h):
    m = h.confirm()
    auth = h.authorize(m["id"], h.quote()["id"]).json()
    h.wallet.catalog = h.wallet.catalog.with_changes(fee_rules={"ctx_a_standard": [
        {"label": "Delivery", "min_subtotal_minor": 0, "max_subtotal_minor": None, "amount_minor": 4500,
         "evidence_ids": ["ev_fee_a_std"]}]})
    paid = h.pay(auth).json()
    assert paid["violations"][0]["code"] == "QUOTE_CHANGED"
    assert week(h, m["id"])["reserved_minor"] == 0


def test_token_for_other_quote_is_refused(h):
    m = h.confirm()
    auth = h.authorize(m["id"], h.quote()["id"]).json()
    other = h.quote()
    paid = h.pay(auth, quote_id=other["id"]).json()
    assert paid["violations"][0]["code"] == "QUOTE_CHANGED"


def test_forged_token_is_refused_without_touching_reservation(h):
    m = h.confirm()
    auth = h.authorize(m["id"], h.quote()["id"]).json()
    tampered = auth["authorization_token"][:-4] + ("AAAA" if not auth["authorization_token"].endswith("AAAA") else "BBBB")
    paid = h.pay(auth, authorization_token=tampered).json()
    assert paid["violations"][0]["code"] == "AUTHORIZATION_INVALID"
    assert week(h, m["id"])["reserved_minor"] == 29700
    assert h.pay(auth).json()["status"] == "completed"


def test_expiry_releases_once_and_blocks_payment(h):
    m = h.confirm()
    auth = h.authorize(m["id"], h.quote()["id"]).json()
    h.clock.advance(seconds=121)
    assert week(h, m["id"])["reserved_minor"] == 0
    paid = h.pay(auth).json()
    assert paid["violations"][0]["code"] == "AUTHORIZATION_EXPIRED"
    assert week(h, m["id"])["reserved_minor"] == 0  # not released twice
    res = h.client.post(f"/api/v1/reservations/{auth['reservation']['id']}/cancel", headers={**AGENT, **key()}, json={})
    assert res.status_code == 409


def test_cancel_releases_once_then_payment_refused(h):
    m = h.confirm()
    auth = h.authorize(m["id"], h.quote()["id"]).json()
    url = f"/api/v1/reservations/{auth['reservation']['id']}/cancel"
    res = h.client.post(url, headers={**AGENT, **key()}, json={"reason": "changed mind"})
    assert res.status_code == 200 and res.json()["released_minor"] == 29700
    assert h.client.post(url, headers={**USER, **key()}, json={}).status_code == 409
    assert week(h, m["id"])["available_minor"] == 80000
    assert h.pay(auth).json()["violations"][0]["code"] == "RESERVATION_CANCELLED"


# --- delegation ----------------------------------------------------------------------

def test_child_mandate_is_bounded_by_parent_budget(h):
    root = h.confirm({**POLICY, "allowed_merchant_ids": ["demo_store_a", "demo_store_b"]}, delegatee="agent_coordinator")
    child_policy = {**POLICY, "allowed_merchant_ids": ["demo_store_a"],
                    "period_limits": [{"period": "calendar_week", "limit_minor": 60000, "timezone": "Asia/Hong_Kong"}]}
    child = h.confirm(child_policy, parent=root["id"])
    auth = h.authorize(child["id"], h.quote()["id"]).json()
    assert auth["status"] == "approved"
    assert {b["mandate_id"] for b in auth["budgets"]} == {root["id"], child["id"]}
    assert all(b["reserved_minor"] == 29700 for b in auth["budgets"])

    # Revoking the parent cancels the child's unpaid reservation.
    rev = h.client.post(f"/api/v1/mandates/{root['id']}/revoke", headers={**USER, **key()}, json={}).json()
    assert rev["cancelled_reservation_ids"] == [auth["reservation"]["id"]]
    assert h.pay(auth).json()["violations"][0]["code"] == "MANDATE_REVOKED"


def test_child_cannot_widen_parent(h):
    root = h.confirm({**POLICY, "allowed_merchant_ids": ["demo_store_a"]}, delegatee="agent_coordinator")
    h.drafts.register("draft_wide", owner_id="user_demo", delegatee_id="agent_student",
                      expires_at="2026-10-31T23:59:59+08:00", parent_mandate_id=root["id"])
    res = h.client.post("/api/v1/mandates/confirm", headers={**USER, **key()},
                        json={"draft_id": "draft_wide", "policy": POLICY})
    assert res.status_code == 422
    assert res.json()["error"]["details"]["reason_code"] == "POLICY_NOT_NARROWER"


# --- authentication, roles and input validation -------------------------------------

def test_roles_are_enforced_before_body_validation(h):
    assert h.client.post("/api/v1/mandates/confirm", headers={**AGENT, **key()}, json={"bogus": 1}).status_code == 403
    assert h.client.post("/api/v1/authorizations", headers={**USER, **key()}, json={}).status_code == 403
    assert h.client.post("/api/v1/payments", headers=key(), json={}).status_code == 401
    assert h.client.get("/api/v1/wallet/m_x", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_agent_cannot_use_someone_elses_mandate(h):
    m = h.confirm(delegatee="agent_other")
    res = h.authorize(m["id"], h.quote()["id"])
    assert res.status_code == 404


def test_float_money_and_unknown_fields_are_rejected(h):
    h.drafts.register("draft_f", owner_id="user_demo", delegatee_id="agent_student",
                      expires_at="2026-10-31T23:59:59+08:00")
    for policy in ({**POLICY, "per_order_limit_minor": 300.5}, {**POLICY, "surprise": True}):
        res = h.client.post("/api/v1/mandates/confirm", headers={**USER, **key()},
                            json={"draft_id": "draft_f", "policy": policy})
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "INVALID_REQUEST"


def test_missing_idempotency_key_is_422(h):
    m = h.confirm()
    res = h.client.post("/api/v1/authorizations", headers=AGENT,
                        json={"transaction_id": "t", "mandate_id": m["id"], "quote_id": "q"})
    assert res.status_code == 422


def test_policy_validation(h):
    h.drafts.register("draft_v", owner_id="user_demo", delegatee_id="agent_student",
                      expires_at="2026-10-31T23:59:59+08:00")
    dup = {**POLICY, "period_limits": POLICY["period_limits"] * 2, "expires_at": "2026-10-01T00:00:00+08:00"}
    res = h.client.post("/api/v1/mandates/confirm", headers={**USER, **key()}, json={"draft_id": "draft_v", "policy": dup})
    assert res.status_code == 422
    assert len(res.json()["error"]["details"]["problems"]) == 2


# --- audit stub --------------------------------------------------------------------

def test_events_form_a_hash_chain_in_the_same_transaction(h):
    m = h.confirm()
    h.buy(m["id"])
    with h.wallet.db.read() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM wallet_stub_audit_events WHERE stream_id = 'stream_user_demo' ORDER BY sequence")]
    assert [r["type"] for r in rows] == ["mandate_confirmed", "quote_created", "authorization_approved", "payment_completed"]
    previous = GENESIS_HASH
    for r in rows:
        import json
        event = {k: r[k] for k in ("stream_id", "sequence", "event_id", "type", "occurred_at", "actor_id",
                                   "mandate_id", "transaction_id", "previous_hash")}
        event["payload"] = json.loads(r["payload_json"])
        assert r["previous_hash"] == previous
        assert sha256_hex(event) == r["event_hash"]
        previous = r["event_hash"]
        assert "authorization_token" not in r["payload_json"]


def test_placeholder_catalog_is_labelled():
    cat = Catalog.load()
    assert all("PLACEHOLDER" in e["conditions"] for e in cat._data["evidence"])
    assert uuid  # keep import used
