from __future__ import annotations

import json
import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from mandate.agent.run_routes import build_agent_run_router
from mandate.agent.runs import AgentRuns
from mandate.agent.selector import NONE, Pick, Selection, SelectorError, build_questions, read_answers
from mandate.payments.clock import HKT, FixedClock
from mandate.payments.dev_app import create_app
from mandate.payments.drafts import InMemoryDrafts

USER = {"Authorization": "Bearer dev-user-token"}
AGENT = {"Authorization": "Bearer dev-agent-token"}


def product(pid, title, price, category="pantry", status="curated"):
    return {"id": pid, "merchant_id": "shop", "title": title, "description": title, "category": category,
            "category_status": status, "unit_label": "pack", "unit_price_minor": price, "currency": "HKD",
            "available": True, "evidence_ids": ["ev"]}


CATALOG = {
    "merchants": {"shop": {"revision": "r1", "data_mode": "observed_snapshot", "evidence_ids": ["ev"]}},
    "products": [product("rice", "Jasmine Rice 8KG", 8990), product("brocc", "Broccoli 1EA", 890, "produce"),
                 product("beer", "Lager 4x500ML", 5100, "alcohol"),
                 product("wine", "Shaoxing Rice Wine", 3900, "pantry", "conflicting")],
    "delivery_contexts": [{"id": "ctx_pickup", "merchant_id": "shop", "fee_rules": [
        {"label": "Pickup", "min_subtotal_minor": 5001, "max_subtotal_minor": None, "amount_minor": 0,
         "evidence_ids": ["ev"]}]}],
    "evidence": [{"id": "ev", "source_url": "https://shop.example.com/", "observed_at": "2026-10-03T00:00:00+08:00",
                  "kind": "product_price", "capture_path": "x.html.gz", "conditions": "Guest session."}],
}
POLICY = {"currency": "HKD", "per_order_limit_minor": 30000,
          "period_limits": [{"period": "calendar_week", "limit_minor": 80000, "timezone": "Asia/Hong_Kong"}],
          "allowed_merchant_ids": ["shop"], "blocked_categories": ["alcohol"],
          "expires_at": "2026-10-31T23:59:59+08:00", "approval_above_minor": None}


class Inline:
    def submit(self, fn, *args):
        fn(*args)


class FakeSelector:
    """Picks the product whose title contains the item name; records what it was offered."""

    def __init__(self, error: Exception | None = None):
        self.error = error
        self.offered: list[str] = []

    def choose(self, items, products, instruction):
        if self.error:
            raise self.error
        self.offered = [p["id"] for p in products]
        picks = []
        for item in items:
            match = next((p for p in products if item["name"].lower() in p["title"].lower()), None)
            picks.append(Pick(item["name"], item["quantity"], match["id"] if match else None,
                              0.97 if match else 1.0, "" if match else "no matching product"))
        return Selection(picks, "jev-test")


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(CATALOG))
    monkeypatch.setenv("MANDATE_CATALOG_PATH", str(path))
    drafts = InMemoryDrafts()
    app, wallet = create_app(tmp_path, clock=FixedClock(datetime(2026, 10, 7, 10, 0, tzinfo=HKT)),
                             draft_lookup=drafts)
    selector = FakeSelector()
    app.include_router(build_agent_run_router(AgentRuns(wallet, selector, Inline())), prefix="/api/v1")
    client = TestClient(app)

    def confirm(policy=POLICY):
        draft = f"draft_{uuid.uuid4().hex}"
        drafts.register(draft, owner_id="user_demo", delegatee_id="agent_student",
                        expires_at="2026-10-31T23:59:59+08:00")
        res = client.post("/api/v1/mandates/confirm", headers={**USER, "Idempotency-Key": str(uuid.uuid4())},
                          json={"draft_id": draft, "policy": policy})
        assert res.status_code == 201, res.text
        return res.json()["id"]

    return client, confirm, selector


def start(client, mandate_id, names, key=None, **extra):
    body = {"mandate_id": mandate_id, "shopping_list": [{"name": n, "quantity": q} for n, q in names], **extra}
    return client.post("/api/v1/agent-runs", headers={**USER, "Idempotency-Key": key or str(uuid.uuid4())}, json=body)


def test_run_quotes_a_basket_the_user_can_load(env):
    client, confirm, selector = env
    mandate = confirm()
    res = start(client, mandate, [("rice", 1), ("broccoli", 2), ("eggs", 1)])
    assert res.status_code == 202, res.text
    run = client.get(f"/api/v1/agent-runs/{res.json()['id']}", headers=USER).json()
    assert run["status"] == "quoted" and run["provider"] == "jev" and run["model_id"] == "jev-test"
    assert "picked 2 of 3 items" in run["message"] and "eggs (no matching product)" in run["message"]
    assert "categories your mandate blocks (alcohol) were not offered" in run["message"]

    quote = client.get(f"/api/v1/quotes/{run['quote_id']}", headers=USER).json()
    assert {(i["product_id"], i["quantity"]) for i in quote["items"]} == {("rice", 1), ("brocc", 2)}
    assert quote["total_minor"] == 8990 + 2 * 890
    # Blocked and conflicting-category products are never offered to the selector.
    assert set(selector.offered) == {"rice", "brocc"}


def test_over_limit_basket_is_quoted_with_a_warning(env):
    client, confirm, _ = env
    run_id = start(client, confirm(), [("rice", 4)]).json()["id"]
    run = client.get(f"/api/v1/agent-runs/{run_id}", headers=USER).json()
    assert run["status"] == "quoted"
    assert "over your HK$300.00 per-order limit" in run["message"]


def test_basket_below_observed_fee_rule_fails_honestly(env):
    client, confirm, _ = env
    run_id = start(client, confirm(), [("broccoli", 1)]).json()["id"]
    run = client.get(f"/api/v1/agent-runs/{run_id}", headers=USER).json()
    assert run["status"] == "failed" and run["quote_id"] is None
    assert "No basket could be quoted" in run["message"]


def test_nothing_matched_fails(env):
    client, confirm, _ = env
    run = client.get(f"/api/v1/agent-runs/{start(client, confirm(), [('eggs', 1)]).json()['id']}", headers=USER).json()
    assert run["status"] == "failed" and "no list item matched" in run["message"]


def test_selector_error_fails_the_run(env, monkeypatch):
    client, confirm, selector = env
    selector.error = SelectorError("Could not reach Jev; no basket was built.")
    run = client.get(f"/api/v1/agent-runs/{start(client, confirm(), [('rice', 1)]).json()['id']}", headers=USER).json()
    assert run["status"] == "failed" and run["message"] == "Could not reach Jev; no basket was built."


def test_idempotency(env):
    client, confirm, _ = env
    mandate = confirm()
    first = start(client, mandate, [("rice", 1)], key="k1").json()
    again = start(client, mandate, [("rice", 1)], key="k1")
    assert again.status_code == 202 and again.json()["id"] == first["id"]
    changed = start(client, mandate, [("rice", 2)], key="k1")
    assert changed.status_code == 409 and changed.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_rejections(env):
    client, confirm, _ = env
    mandate = confirm()
    assert start(client, mandate, [("rice", 1)], auto_purchase=True).status_code == 422
    assert start(client, "m_missing", [("rice", 1)]).status_code == 404
    agent = client.post("/api/v1/agent-runs", headers={**AGENT, "Idempotency-Key": "a"},
                        json={"mandate_id": mandate, "shopping_list": [{"name": "rice", "quantity": 1}]})
    assert agent.status_code == 403
    assert client.get("/api/v1/agent-runs/run_missing", headers=USER).status_code == 404
    client.post(f"/api/v1/mandates/{mandate}/revoke", headers={**USER, "Idempotency-Key": "r"}, json={})
    revoked = start(client, mandate, [("rice", 1)])
    assert revoked.status_code == 409 and revoked.json()["error"]["code"] == "MANDATE_NOT_ACTIVE"


# --- selector parsing -------------------------------------------------------

ITEMS = [{"name": "rice", "quantity": 1}, {"name": "eggs", "quantity": 1}, {"name": "milk", "quantity": 2}]


def test_read_answers_keeps_only_confident_listed_choices():
    answers = {"item_0": {"choice": "rice", "probabilities": {"rice": 0.96, NONE: 0.04}},
               "item_1": {"choice": NONE, "probabilities": {"rice": 0.01, NONE: 0.99}},
               "item_2": {"choice": "rice", "probabilities": {"rice": 0.40, NONE: 0.35}}}
    picks = read_answers(answers, ITEMS, {"rice"})
    assert [p.product_id for p in picks] == ["rice", None, None]
    assert picks[1].reason == "no matching product"
    assert picks[2].reason.startswith("no confident match")
    assert picks[2].quantity == 2


def test_read_answers_rejects_unlisted_choice():
    with pytest.raises(SelectorError):
        read_answers({"item_0": {"choice": "invented", "probabilities": {"invented": 1.0}}}, ITEMS[:1], {"rice"})


def test_questions_offer_every_product_and_none():
    questions = build_questions(ITEMS[:1], [product("rice", "Jasmine Rice 8KG", 8990)], "cheapest")
    criteria = questions["item_0"]["criteria"]
    assert set(criteria) == {"rice", NONE}
    assert criteria["rice"]["price_hkd"] == "89.90"
    assert questions["item_0"]["instructions"]["shopper_preference"] == "cheapest"


def test_mandate_for_a_store_missing_from_the_catalog_explains_itself(env):
    client, confirm, _ = env
    mandate = confirm({**POLICY, "allowed_merchant_ids": ["demo_store_a"]})
    run = client.get(f"/api/v1/agent-runs/{start(client, mandate, [('rice', 1)]).json()['id']}", headers=USER).json()
    assert run["status"] == "failed"
    assert "demo_store_a is not in the current catalog" in run["message"]
