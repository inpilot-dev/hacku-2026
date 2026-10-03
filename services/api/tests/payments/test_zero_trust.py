"""Zero trust on agent requests: nothing the agent says is believed unless the wallet can check it itself.

Price, category, total, merchant and payment amount come from the trusted catalog and the wallet's own
records; the agent's token names one user; every purchase token is bound to one transaction.
"""

from __future__ import annotations

import uuid

import pytest

from mandate.payments.auth import Actor
from mandate.payments.errors import ApiError

from .conftest import AGENT, POLICY, RICE_X3, key

NEEDS_OK = {**POLICY, "approval_above_minor": 0,
            "period_limits": [{"period": "calendar_week", "limit_minor": 500000, "timezone": "Asia/Hong_Kong"}]}


def test_agent_cannot_set_prices_totals_or_categories(h):
    for extra in ({"total_minor": 1}, {"unit_price_minor": 1}, {"category": "pantry"}):
        body = {**RICE_X3, "items": [{**RICE_X3["items"][0], **extra}]}
        assert h.client.post("/api/v1/quotes", headers=AGENT, json=body).status_code == 422
    assert h.client.post("/api/v1/quotes", headers=AGENT, json={**RICE_X3, "total_minor": 1}).status_code == 422
    q = h.quote()
    assert (q["total_minor"], q["items"][0]["category"]) == (29700, "pantry")  # the catalog's numbers


def test_agent_cannot_name_the_payment_amount(h):
    m = h.confirm()
    auth = h.authorize(m["id"], h.quote()["id"]).json()
    assert h.pay(auth, amount_minor=1).status_code == 422


def test_agent_for_another_family_cannot_see_or_use_the_mandate(h):
    m = h.confirm()
    q = h.quote()
    stranger = Actor("agent_student", "agent", owner_id="user_other")  # same agent id, different user's token
    with pytest.raises(ApiError) as exc:
        h.wallet.authorize(stranger, str(uuid.uuid4()), {"transaction_id": str(uuid.uuid4()),
                                                         "mandate_id": m["id"], "quote_id": q["id"]})
    assert exc.value.status == 404
    with pytest.raises(ApiError):
        h.wallet.get_mandate(stranger, m["id"])

    auth = h.authorize(m["id"], q["id"]).json()
    with pytest.raises(ApiError) as exc:
        h.wallet.pay(stranger, str(uuid.uuid4()), {"transaction_id": auth["transaction_id"], "quote_id": q["id"],
                                                   "authorization_token": auth["authorization_token"]})
    assert exc.value.status == 404


def test_agent_cannot_flood_the_owner_with_approval_requests(h):
    m = h.confirm(NEEDS_OK)
    for _ in range(3):
        assert h.authorize(m["id"], h.quote()["id"]).json()["status"] == "requires_review"
    body = h.authorize(m["id"], h.quote()["id"]).json()
    assert body["status"] == "refused" and body["approval_request"] is None
    assert body["violations"][0]["code"] == "RISK_REVIEW_REQUIRED"
    assert body["message"] == ("3 purchases are already waiting for user_demo to answer; the agent must wait for "
                               "those before asking again.")
    # Once the owner answers (or the requests lapse), the agent may ask again.
    h.clock.advance(minutes=11)
    assert h.authorize(m["id"], h.quote()["id"]).json()["status"] == "requires_review"


def test_agent_free_text_is_bounded(h):
    m = h.confirm()
    auth = h.authorize(m["id"], h.quote()["id"]).json()
    res = h.client.post(f"/api/v1/reservations/{auth['reservation']['id']}/cancel",
                        headers={**AGENT, **key()}, json={"reason": "x" * 501})
    assert res.status_code == 422
