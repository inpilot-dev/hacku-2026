from __future__ import annotations

import uuid
from datetime import datetime

from fastapi.testclient import TestClient

from mandate.agent.drafts import DraftService, interpret
from mandate.agent.run_routes import build_agent_run_router
from mandate.agent.runs import AgentRuns
from mandate.payments.clock import HKT, FixedClock
from mandate.payments.dev_app import create_app
from mandate.payments.drafts import InMemoryDrafts

USER = {"Authorization": "Bearer dev-user-token"}
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=HKT)
UI_TEXT = ("Buy groceries each week. Spend no more than HK$300 per order and HK$800 per week. "
           "Only from Wellcome · Click & Collect. No alcohol. Permission expires 31 October 2026.")


def test_interprets_the_ui_sample_request():
    policy, ambiguities, summary = interpret(UI_TEXT, ["wellcome"], NOW)
    assert ambiguities == []
    assert policy == {
        "currency": "HKD", "per_order_limit_minor": 30000,
        "period_limits": [{"period": "calendar_week", "limit_minor": 80000, "timezone": "Asia/Hong_Kong"}],
        "allowed_merchant_ids": ["wellcome"], "blocked_categories": ["alcohol"],
        "expires_at": "2026-10-31T23:59:59+08:00", "approval_above_minor": None,
    }
    assert "not an AI model" in summary


def test_other_phrasings():
    policy, ambiguities, _ = interpret(
        "Groceries from wellcome, at most $250 a order and $1,000 per month, without dairy, ask me before anything "
        "over HK$200, until the end of December", ["wellcome"], NOW)
    assert ambiguities == []
    assert policy["per_order_limit_minor"] == 25000
    assert policy["period_limits"] == [{"period": "calendar_month", "limit_minor": 100000, "timezone": "Asia/Hong_Kong"}]
    assert policy["blocked_categories"] == ["dairy"]
    assert policy["approval_above_minor"] == 20000
    assert policy["expires_at"] == "2026-12-31T23:59:59+08:00"


def test_missing_or_unknown_fields_become_questions_not_guesses():
    policy, ambiguities, _ = interpret("Buy groceries from ParknShop for HK$300 per order.", ["wellcome"], NOW)
    assert policy is None
    assert {a["field"] for a in ambiguities} == {"period_limits", "allowed_merchant_ids", "expires_at"}
    _, past, _ = interpret("HK$300 per order, HK$800 per week, wellcome, expires 1 January 2026", ["wellcome"], NOW)
    assert past[0]["field"] == "expires_at" and "already passed" in past[0]["question"]


def make_client(tmp_path):
    drafts = InMemoryDrafts()
    app, wallet = create_app(tmp_path, clock=FixedClock(NOW), draft_lookup=drafts)
    app.include_router(build_agent_run_router(AgentRuns(wallet), DraftService(wallet, drafts)), prefix="/api/v1")
    return TestClient(app)


def draft(client, text=UI_TEXT, key=None):
    return client.post("/api/v1/mandates/draft", headers={**USER, "Idempotency-Key": key or str(uuid.uuid4())},
                       json={"text": text, "delegatee_id": "agent_student"})


def test_each_draft_can_back_a_new_mandate(tmp_path, monkeypatch):
    monkeypatch.setenv("MANDATE_CATALOG_PATH", "")  # placeholder catalog: merchants demo_store_a/b/c
    client = make_client(tmp_path)
    text = UI_TEXT.replace("Wellcome · Click & Collect", "demo_store_a")
    for _ in range(2):  # a second activation in the same wallet needs its own draft
        res = draft(client, text)
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["owner_id"] == "user_demo" and body["proposed_policy"]["allowed_merchant_ids"] == ["demo_store_a"]
        confirmed = client.post("/api/v1/mandates/confirm", headers={**USER, "Idempotency-Key": str(uuid.uuid4())},
                                json={"draft_id": body["draft_id"], "policy": body["proposed_policy"]})
        assert confirmed.status_code == 201, confirmed.text
        again = client.post("/api/v1/mandates/confirm", headers={**USER, "Idempotency-Key": str(uuid.uuid4())},
                            json={"draft_id": body["draft_id"], "policy": body["proposed_policy"]})
        assert again.status_code == 409  # one draft, one mandate


def test_draft_idempotency_and_roles(tmp_path):
    client = make_client(tmp_path)
    first = draft(client, key="k").json()
    assert draft(client, key="k").json()["draft_id"] == first["draft_id"]
    assert draft(client, text="something else", key="k").status_code == 409
    agent = client.post("/api/v1/mandates/draft", headers={"Authorization": "Bearer dev-agent-token", "Idempotency-Key": "a"},
                        json={"text": UI_TEXT, "delegatee_id": "agent_student"})
    assert agent.status_code == 403
