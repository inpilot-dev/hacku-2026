"""Velocity limit: N purchases inside M minutes, across the whole delegation subtree."""

from __future__ import annotations

from .conftest import POLICY, USER, key

FAST = {**POLICY, "velocity_limit": {"max_purchases": 2, "window_minutes": 10},
        "period_limits": [{"period": "calendar_week", "limit_minor": 500000, "timezone": "Asia/Hong_Kong"}]}


def test_third_purchase_inside_the_window_is_refused(h):
    m = h.confirm(FAST)
    h.buy(m["id"])
    h.clock.advance(minutes=3)
    h.buy(m["id"])
    body = h.authorize(m["id"], h.quote()["id"]).json()
    assert body["status"] == "refused"
    v = body["violations"][0]
    assert v["code"] == "VELOCITY_LIMIT_EXCEEDED"
    assert v["message"] == "2 purchases in the last 10 minutes; this mandate allows 2 per 10 minutes."
    assert v["rule_id"] == f"{m['id']}/v1/velocity_limit"


def test_window_slides(h):
    m = h.confirm(FAST)
    h.buy(m["id"])
    h.buy(m["id"])
    h.clock.advance(minutes=11)
    assert h.authorize(m["id"], h.quote()["id"]).json()["status"] == "approved"


def test_cancelled_reservations_do_not_count(h):
    m = h.confirm(FAST)
    for _ in range(2):
        held = h.authorize(m["id"], h.quote()["id"]).json()
        h.client.post(f"/api/v1/reservations/{held['reservation']['id']}/cancel", headers={**USER, **key()}, json={})
    assert h.authorize(m["id"], h.quote()["id"]).json()["status"] == "approved"


def test_parent_velocity_counts_purchases_by_every_child(h):
    parent = h.confirm(FAST)
    child_policy = {**POLICY, "allowed_merchant_ids": ["demo_store_a"]}
    a = h.confirm(child_policy, parent=parent["id"], delegatee="agent_student")
    b = h.confirm(child_policy, parent=parent["id"], delegatee="agent_student")
    h.buy(a["id"])
    h.buy(b["id"])
    body = h.authorize(a["id"], h.quote()["id"]).json()
    assert body["violations"][0]["code"] == "VELOCITY_LIMIT_EXCEEDED"
    assert body["violations"][0]["mandate_id"] == parent["id"]


def test_velocity_limit_is_validated(h):
    res = h.client.post("/api/v1/mandates/confirm", headers={**USER, **key()},
                        json={"draft_id": "draft_x", "policy": {**POLICY, "velocity_limit": {
                            "max_purchases": 0, "window_minutes": 10}}})
    assert res.status_code == 422
