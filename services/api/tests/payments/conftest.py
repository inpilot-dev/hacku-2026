from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from mandate.payments.clock import HKT, FixedClock
from mandate.payments.dev_app import create_app
from mandate.payments.drafts import InMemoryDrafts

USER = {"Authorization": "Bearer dev-user-token"}
AGENT = {"Authorization": "Bearer dev-agent-token"}

POLICY = {
    "currency": "HKD",
    "per_order_limit_minor": 30000,
    "period_limits": [{"period": "calendar_week", "limit_minor": 80000, "timezone": "Asia/Hong_Kong"}],
    "allowed_merchant_ids": ["demo_store_a", "demo_store_b"],
    "blocked_categories": ["alcohol"],
    "expires_at": "2026-10-31T23:59:59+08:00",
    "approval_above_minor": None,
}

# Jasmine rice 5kg x3 = HK$267 + HK$30 delivery = HK$297: fits the HK$300 order cap.
RICE_X3 = {"merchant_id": "demo_store_a", "items": [{"product_id": "p_a_rice", "quantity": 3}],
           "delivery_context_id": "ctx_a_standard"}


def key() -> dict:
    return {"Idempotency-Key": str(uuid.uuid4())}


class Harness:
    def __init__(self, client: TestClient, wallet, drafts: InMemoryDrafts, clock: FixedClock):
        self.client, self.wallet, self.drafts, self.clock = client, wallet, drafts, clock

    def confirm(self, policy=None, *, parent=None, delegatee="agent_student") -> dict:
        draft_id = f"draft_{uuid.uuid4().hex}"
        self.drafts.register(draft_id, owner_id="user_demo", delegatee_id=delegatee,
                             expires_at="2026-10-31T23:59:59+08:00", parent_mandate_id=parent)
        res = self.client.post("/api/v1/mandates/confirm", headers={**USER, **key()},
                               json={"draft_id": draft_id, "policy": policy or POLICY})
        assert res.status_code == 201, res.text
        return res.json()

    def quote(self, body=None, headers=AGENT) -> dict:
        res = self.client.post("/api/v1/quotes", headers=headers, json=body or RICE_X3)
        assert res.status_code == 201, res.text
        return res.json()

    # Most safety tests exercise card holds explicitly, independent of cost recommendation.
    def authorize(self, mandate_id, quote_id, txn=None, headers=None, route_id="card_hsbc_red"):
        return self.client.post("/api/v1/authorizations", headers=headers or {**AGENT, **key()},
                                json={"transaction_id": txn or str(uuid.uuid4()), "mandate_id": mandate_id,
                                      "quote_id": quote_id, "payment_route_id": route_id})

    def pay(self, approved: dict, headers=None, **override):
        body = {"transaction_id": approved["transaction_id"], "quote_id": approved["claims"]["quote_id"],
                "authorization_token": approved["authorization_token"], **override}
        return self.client.post("/api/v1/payments", headers=headers or {**AGENT, **key()}, json=body)

    def budget(self, mandate_id) -> list[dict]:
        res = self.client.get(f"/api/v1/wallet/{mandate_id}", headers=USER)
        assert res.status_code == 200, res.text
        return res.json()["applicable_budgets"]

    def buy(self, mandate_id, quote_body=None) -> dict:
        q = self.quote(quote_body)
        auth = self.authorize(mandate_id, q["id"]).json()
        assert auth["status"] == "approved", auth
        paid = self.pay(auth).json()
        assert paid["status"] == "completed", paid
        return paid


@pytest.fixture
def h(tmp_path) -> Harness:
    clock = FixedClock(datetime(2026, 10, 7, 10, 0, tzinfo=HKT))  # a Wednesday
    drafts = InMemoryDrafts()
    app, wallet = create_app(tmp_path, clock=clock, draft_lookup=drafts)
    return Harness(TestClient(app), wallet, drafts, clock)
