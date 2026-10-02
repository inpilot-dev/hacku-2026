"""Smoke test: a real MCP client session drives the wallet dev app through the MCP server."""

from __future__ import annotations

import json
import uuid
from datetime import datetime

import anyio
import pytest
from fastapi.testclient import TestClient
from mcp.shared.memory import create_connected_server_and_client_session

from mandate.payments.clock import HKT, FixedClock
from mandate.payments.dev_app import create_app
from mandate.payments.drafts import InMemoryDrafts
from wallet_mcp import WalletAPI, build_server

POLICY = {
    "currency": "HKD",
    "per_order_limit_minor": 30000,
    "period_limits": [{"period": "calendar_week", "limit_minor": 80000, "timezone": "Asia/Hong_Kong"}],
    "allowed_merchant_ids": ["demo_store_a", "demo_store_b"],
    "blocked_categories": ["alcohol"],
    "expires_at": "2026-10-31T23:59:59+08:00",
    "approval_above_minor": None,
}
RICE = {"merchant_id": "demo_store_a", "delivery_context_id": "ctx_a_standard"}


@pytest.fixture
def setup(tmp_path):
    drafts = InMemoryDrafts()
    drafts.register("draft_demo", owner_id="user_demo", delegatee_id="agent_student",
                    expires_at="2026-10-31T23:59:59+08:00")
    app, _ = create_app(tmp_path, clock=FixedClock(datetime(2026, 10, 7, 10, 0, tzinfo=HKT)), draft_lookup=drafts)
    user = TestClient(app, base_url="http://testserver/api/v1/", headers={"Authorization": "Bearer dev-user-token"})
    res = user.post("mandates/confirm", headers={"Idempotency-Key": str(uuid.uuid4())},
                    json={"draft_id": "draft_demo", "policy": POLICY})
    assert res.status_code == 201, res.text
    agent = TestClient(app, base_url="http://testserver/api/v1/", headers={"Authorization": "Bearer dev-agent-token"})
    return build_server(WalletAPI(agent)), res.json()["id"]


def run(server, script):
    async def go():
        async with create_connected_server_and_client_session(server._mcp_server) as session:
            async def call(name, **args):
                result = await session.call_tool(name, args)
                text = result.content[0].text if result.content else ""
                return result.isError, (text if result.isError else json.loads(text))
            return await script(session, call)
    return anyio.run(go)


def test_only_agent_tools_are_exposed(setup):
    server, _ = setup

    async def script(session, call):
        return {t.name for t in (await session.list_tools()).tools}

    names = run(server, script)
    assert {"create_quote", "authorize_purchase", "pay", "get_budget"} <= names
    assert not {n for n in names if "confirm" in n or "revoke" in n}


def test_quote_authorize_pay_and_replay(setup):
    server, mandate_id = setup

    async def script(session, call):
        err, quote = await call("create_quote", items=[{"product_id": "p_a_rice", "quantity": 3}], **RICE)
        assert not err, quote
        err, auth = await call("authorize_purchase", mandate_id=mandate_id, quote_id=quote["id"])
        assert not err and auth["status"] == "approved", auth
        assert "." not in auth["authorization_token"], "signed token must not reach the model"
        txn = auth["transaction_id"]
        err, paid = await call("pay", transaction_id=txn)
        assert not err and paid["status"] == "completed", paid
        err, again = await call("pay", transaction_id=txn)
        assert again["replayed"] is True and again["receipt"]["id"] == paid["receipt"]["id"]
        err, budget = await call("get_budget", mandate_id=mandate_id)
        return quote, budget

    quote, budget = run(server, script)
    week = budget["applicable_budgets"][0]
    assert week["paid_minor"] == quote["total_minor"] and week["reserved_minor"] == 0


def test_refusal_is_a_result_not_an_error(setup):
    server, mandate_id = setup

    async def script(session, call):
        _, quote = await call("create_quote", items=[{"product_id": "p_a_rice", "quantity": 4}], **RICE)
        return await call("authorize_purchase", mandate_id=mandate_id, quote_id=quote["id"])

    err, auth = run(server, script)
    assert not err and auth["status"] == "refused"
    assert auth["violations"][0]["code"]


def test_pay_without_authorization_and_http_errors(setup):
    server, _ = setup

    async def script(session, call):
        return await call("pay", transaction_id="never-authorized"), await call("get_budget", mandate_id="nope")

    (pay_err, pay_msg), (budget_err, budget_msg) = run(server, script)
    assert pay_err and "authorize_purchase" in pay_msg
    assert budget_err and "HTTP 404" in budget_msg
