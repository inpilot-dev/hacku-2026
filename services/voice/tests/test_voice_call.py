"""The voice service against a real wallet app, with ElevenLabs replaced by a recorder."""

from __future__ import annotations

import uuid
from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from mandate.payments.clock import HKT, FixedClock
from mandate.payments.dev_app import create_app as create_wallet
from mandate.payments.drafts import InMemoryDrafts
from voice_call.app import create_app
from voice_call.config import Settings

USER = {"Authorization": "Bearer dev-user-token"}
AGENT = {"Authorization": "Bearer dev-agent-token"}
POLICY = {
    "currency": "HKD", "per_order_limit_minor": 30000,
    "period_limits": [{"period": "calendar_week", "limit_minor": 80000, "timezone": "Asia/Hong_Kong"}],
    "allowed_merchant_ids": ["demo_store_a"], "blocked_categories": ["alcohol"],
    "expires_at": "2026-10-31T23:59:59+08:00", "approval_above_minor": 20000,
}
RICE_X3 = {"merchant_id": "demo_store_a", "items": [{"product_id": "p_a_rice", "quantity": 3}],
           "delivery_context_id": "ctx_a_standard"}
SETTINGS = Settings(webhook_secret="hook", tool_secret="tool", caregiver_pin="2468", caregiver_phone="+85291234567",
                    elevenlabs_api_key="xi", agent_id="agent_1", phone_number_id="phnum_1")
TOOL = {"X-Tool-Secret": "tool"}


def key():
    return {"Idempotency-Key": str(uuid.uuid4())}


class Recorder:
    def __init__(self):
        self.events = []

    def approval_opened(self, event):
        self.events.append(event)


class World:
    def __init__(self, tmp_path, settings=SETTINGS):
        self.clock = FixedClock(datetime(2026, 10, 7, 10, 0, tzinfo=HKT))
        self.drafts = InMemoryDrafts()
        self.hook = Recorder()
        wallet_app, self.wallet = create_wallet(tmp_path, clock=self.clock, draft_lookup=self.drafts,
                                                notifier=self.hook)
        self.w = TestClient(wallet_app)
        self.outbound = []

        def eleven(request: httpx.Request):
            self.outbound.append((request.url, request.headers.get("xi-api-key"), request.read()))
            return httpx.Response(200, json={"success": True, "conversation_id": "conv_1", "callSid": "CA1"})

        self.app = create_app(settings, wallet_http=TestClient(wallet_app, base_url="http://testserver/api/v1"),
                              eleven_http=httpx.Client(transport=httpx.MockTransport(eleven)))
        self.v = TestClient(self.app)

    def escalate(self):
        draft = f"draft_{uuid.uuid4().hex}"
        self.drafts.register(draft, owner_id="user_demo", delegatee_id="agent_student",
                             expires_at="2026-10-31T23:59:59+08:00")
        m = self.w.post("/api/v1/mandates/confirm", headers={**USER, **key()},
                        json={"draft_id": draft, "policy": POLICY}).json()
        q = self.w.post("/api/v1/quotes", headers=AGENT, json=RICE_X3).json()
        txn = str(uuid.uuid4())
        body = self.w.post("/api/v1/authorizations", headers={**AGENT, **key()},
                           json={"transaction_id": txn, "mandate_id": m["id"], "quote_id": q["id"]}).json()
        assert body["status"] == "requires_review"
        res = self.v.post("/approval-opened", json=self.hook.events[-1], headers={"Authorization": "Bearer hook"})
        assert res.status_code == 202, res.text
        return m, q, txn, res.json()["call_id"]

    def tool(self, name, call_id, **body):
        return self.v.post(f"/tools/{name}", headers=TOOL, json={"call_id": call_id, **body})

    def retry_purchase(self, m, q, txn):
        return self.w.post("/api/v1/authorizations", headers={**AGENT, **key()},
                           json={"transaction_id": txn, "mandate_id": m["id"], "quote_id": q["id"]}).json()


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def test_an_opened_approval_places_one_call_without_the_token(world):
    _, _, _, call_id = world.escalate()
    assert len(world.outbound) == 1
    url, api_key, body = world.outbound[0]
    assert str(url).endswith("/v1/convai/twilio/outbound-call") and api_key == "xi"
    assert b"+85291234567" in body and call_id.encode() in body and b"HK$297.00" in body
    # The decision token stays here; ElevenLabs only gets the call ID.
    assert world.hook.events[0]["decision_token"].encode() not in body


def test_full_call_approves_and_the_agent_can_pay(world):
    m, q, txn, call_id = world.escalate()
    order = world.tool("get_order", call_id).json()
    assert order["total"] == "HK$297.00" and order["items"] and order["reasons"]
    assert world.tool("verify_pin", call_id, pin="2 4 6 8").json()["ok"]
    assert world.tool("approve", call_id).json()["ok"]
    assert world.retry_purchase(m, q, txn)["status"] == "approved"
    # A repeated tool call does not decide twice.
    assert not world.tool("approve", call_id).json()["ok"]


def test_approve_needs_pin_and_read_back(world):
    _, _, _, call_id = world.escalate()
    assert "PIN" in world.tool("approve", call_id).json()["say"]
    world.tool("verify_pin", call_id, pin="2468")
    assert "read the order back" in world.tool("approve", call_id).json()["say"]


def test_three_wrong_pins_decline_the_order(world):
    m, q, txn, call_id = world.escalate()
    for left in (2, 1):
        assert world.tool("verify_pin", call_id, pin="0000").json()["tries_left"] == left
    assert world.tool("verify_pin", call_id, pin="0000").json()["tries_left"] == 0
    assert not world.tool("verify_pin", call_id, pin="2468").json()["ok"]
    auth = world.retry_purchase(m, q, txn)
    assert auth["status"] == "refused" and auth["violations"][0]["code"] == "APPROVAL_DENIED"


def test_decline_works_without_a_pin(world):
    m, q, txn, call_id = world.escalate()
    assert world.tool("decline", call_id).json()["ok"]
    assert world.retry_purchase(m, q, txn)["status"] == "refused"


def test_expired_approval_is_reported_not_crashed(world):
    _, _, _, call_id = world.escalate()
    world.tool("get_order", call_id)
    world.tool("verify_pin", call_id, pin="2468")
    world.clock.advance(minutes=11)
    assert "expired" in world.tool("approve", call_id).json()["say"]


def test_secrets_are_required(world):
    _, _, _, call_id = world.escalate()
    assert world.v.post("/tools/get_order", json={"call_id": call_id}).status_code == 401
    assert world.v.post("/tools/get_order", headers={"X-Tool-Secret": "nope"}, json={"call_id": call_id}).status_code == 401
    assert world.v.post("/approval-opened", json=world.hook.events[0]).status_code == 401


def test_unknown_call_and_ambiguous_missing_id(world):
    world.escalate()
    assert world.tool("get_order", "call_nope").status_code == 404
    assert world.v.post("/tools/get_order", headers=TOOL, json={}).status_code == 200  # one open call
    world.escalate()
    assert world.v.post("/tools/get_order", headers=TOOL, json={}).status_code == 404  # two: ambiguous


def test_without_elevenlabs_nothing_is_called(tmp_path):
    world = World(tmp_path, Settings(webhook_secret="hook", tool_secret="tool", caregiver_pin="2468"))
    world.escalate()
    assert world.outbound == []


def test_web_session_gives_the_browser_the_call_id_not_the_token(world):
    assert world.v.get("/web-session").status_code == 404
    _, _, _, call_id = world.escalate()
    session = world.v.get("/web-session").json()
    assert session["agent_id"] == "agent_1" and session["call_id"] == call_id
    assert session["dynamic_variables"] == {"call_id": call_id, "shop": "demo_store_a", "total": "HK$297.00"}
    assert world.hook.events[0]["decision_token"] not in str(session)
    world.tool("decline", call_id)
    assert world.v.get("/web-session").status_code == 404
