"""Approval webhook and decision tokens: off by default, scoped to one approval, never a payment credential."""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from mandate.payments.approval_notify import NoNotifier, WebhookNotifier, notifier_from_env
from mandate.payments.clock import HKT, FixedClock
from mandate.payments.dev_app import create_app
from mandate.payments.drafts import InMemoryDrafts

from .conftest import AGENT, USER, Harness, key
from .test_approvals import NEEDS_OK


class Recorder:
    def __init__(self):
        self.events = []

    def approval_opened(self, event):
        self.events.append(event)


class Broken:
    def approval_opened(self, event):
        raise RuntimeError("receiver is down")


def harness(tmp_path, notifier) -> Harness:
    clock = FixedClock(datetime(2026, 10, 7, 10, 0, tzinfo=HKT))
    drafts = InMemoryDrafts()
    app, wallet = create_app(tmp_path, clock=clock, draft_lookup=drafts, notifier=notifier)
    return Harness(TestClient(app), wallet, drafts, clock)


@pytest.fixture
def rec():
    return Recorder()


@pytest.fixture
def hr(tmp_path, rec) -> Harness:
    return harness(tmp_path, rec)


def escalate(h, txn=None):
    m = h.confirm(NEEDS_OK)
    q = h.quote()
    txn = txn or str(uuid.uuid4())
    body = h.authorize(m["id"], q["id"], txn).json()
    assert body["status"] == "requires_review"
    return m, q, txn, body


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def decide(h, token, verb, **body):
    return h.client.post(f"/api/v1/approval-decision/{verb}", headers={**bearer(token), **key()}, json=body)


def test_notifier_is_off_without_a_webhook_url(monkeypatch):
    monkeypatch.delenv("MANDATE_APPROVAL_WEBHOOK_URL", raising=False)
    assert isinstance(notifier_from_env(), NoNotifier)
    monkeypatch.setenv("MANDATE_APPROVAL_WEBHOOK_URL", "http://127.0.0.1:9/approvals")
    assert isinstance(notifier_from_env(), WebhookNotifier)


def test_opening_an_approval_notifies_once(hr, rec):
    m, q, txn, body = escalate(hr)
    assert len(rec.events) == 1
    event = rec.events[0]
    assert event["type"] == "approval.opened" and event["owner_id"] == "user_demo"
    assert event["approval"] == body["approval_request"]
    # A retry of the same purchase replays the pending decision and must not ring again.
    hr.authorize(m["id"], q["id"], txn)
    assert len(rec.events) == 1


def test_purchases_that_need_no_review_do_not_notify(hr, rec):
    m = hr.confirm()
    hr.buy(m["id"])
    assert rec.events == []


def test_decision_token_reads_and_approves_its_approval(hr, rec):
    m, q, txn, body = escalate(hr)
    token = rec.events[0]["decision_token"]
    read = hr.client.get("/api/v1/approval-decision", headers=bearer(token))
    assert read.status_code == 200, read.text
    assert read.json()["approval"]["id"] == body["approval_request"]["id"]
    assert read.json()["quote"]["id"] == q["id"]

    res = decide(hr, token, "approve")
    assert res.status_code == 200, res.text
    approval = res.json()["approval"]
    assert (approval["status"], approval["decided_by"]) == ("approved", "user_demo")
    assert approval["note"] == "Approved outside the app with a decision token."

    auth = hr.authorize(m["id"], q["id"], txn).json()
    assert auth["status"] == "approved"
    assert hr.pay(auth).json()["status"] == "completed"
    # The approval is decided, so the token has nothing left to do.
    assert decide(hr, token, "deny").status_code == 409


def test_decision_token_can_decline(hr, rec):
    m, q, txn, body = escalate(hr)
    res = decide(hr, rec.events[0]["decision_token"], "deny", note="Too much this week")
    assert res.json()["approval"]["status"] == "denied"
    auth = hr.authorize(m["id"], q["id"], txn).json()
    assert auth["status"] == "refused" and auth["violations"][0]["code"] == "APPROVAL_DENIED"


def test_each_token_only_reaches_its_own_approval(hr, rec):
    escalate(hr)
    _, _, _, second = escalate(hr)
    first_token = rec.events[0]["decision_token"]
    decide(hr, first_token, "deny")
    still = hr.client.get(f"/api/v1/approvals/{second['approval_request']['id']}", headers=USER).json()
    assert still["status"] == "pending"


def test_user_and_agent_tokens_are_not_decision_tokens(hr):
    escalate(hr)
    for headers in (USER, AGENT, bearer("not-a-token"), {}):
        res = hr.client.post("/api/v1/approval-decision/approve", headers={**headers, **key()}, json={})
        assert res.status_code == 401


def test_decision_token_expires_with_the_approval(hr, rec):
    escalate(hr)
    hr.clock.advance(minutes=11)
    res = decide(hr, rec.events[0]["decision_token"], "approve")
    assert res.status_code == 401


def test_decision_token_cannot_pay(hr, rec):
    m = hr.confirm()
    q = hr.quote()
    auth = hr.authorize(m["id"], q["id"]).json()
    assert auth["status"] == "approved"
    # Mint a decision token by escalating a second purchase on another mandate.
    escalate(hr)
    paid = hr.pay(auth, authorization_token=rec.events[0]["decision_token"]).json()
    assert paid["status"] == "refused" and paid["violations"][0]["code"] == "AUTHORIZATION_INVALID"


def test_a_broken_receiver_never_changes_the_authorization(tmp_path):
    h = harness(tmp_path, Broken())
    _, _, _, body = escalate(h)
    assert body["approval_request"]["status"] == "pending"
